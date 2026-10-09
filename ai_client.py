"""
ai_client.py — model-agnostic access to SAP AI Core (Generative AI Hub).

The rest of the generator only needs `ai_fn(system_prompt, user_prompt) -> str`.
This module builds that callable for whichever foundation model is actually
deployed in the AI Core resource group:

  * Anthropic Claude (e.g. "anthropic--claude-4.6-sonnet")  -> Bedrock Converse API
  * OpenAI GPT       (e.g. "gpt-4o")                         -> OpenAI chat completions

Model choice: explicit argument > AI_MODEL env var > first running Claude
deployment > first GPT deployment > first deployment of any kind.

Credentials come from the AICORE_* environment variables (app.py loads them
from profiles/config_evoke.json locally, or cf set-env when deployed).
"""

from __future__ import annotations
import os
import time
from typing import Callable, List, Optional


class AIError(RuntimeError):
    pass


_PREFERENCE = ("anthropic--claude", "claude", "gpt-4o", "gpt-4", "gpt")


def _family(model: str) -> str:
    m = model.lower()
    if "claude" in m or m.startswith("anthropic") or m.startswith("amazon--"):
        return "bedrock"
    if m.startswith("gpt") or m.startswith("o1") or m.startswith("o3") or m.startswith("o4"):
        return "openai"
    raise AIError(f"Model '{model}' is not supported by this generator "
                  f"(supported: Anthropic Claude, OpenAI GPT).")


def available_models() -> List[str]:
    """Model names of the running deployments in the configured resource group."""
    try:
        from gen_ai_hub.proxy.core.proxy_clients import get_proxy_client
        names = []
        for d in get_proxy_client("gen-ai-hub").deployments:
            if d.model_name and d.model_name not in names:
                names.append(d.model_name)
        return names
    except Exception as e:  # noqa: BLE001 — surfaced to the UI as a message
        raise AIError(f"Could not list AI Core deployments: {e}")


def pick_model(preferred: Optional[str] = None,
               models: Optional[List[str]] = None) -> str:
    want = preferred or os.environ.get("AI_MODEL", "")
    models = models if models is not None else available_models()
    if want:
        if want in models:
            return want
        raise AIError(f"Model '{want}' has no running deployment. Available: "
                      f"{', '.join(models) or 'none'}.")
    for prefix in _PREFERENCE:
        for m in models:
            if m.lower().startswith(prefix):
                try:
                    _family(m)
                    return m
                except AIError:
                    continue
    for m in models:
        try:
            _family(m)
            return m
        except AIError:
            continue
    raise AIError("No supported model is deployed in AI Core "
                  f"(found: {', '.join(models) or 'none'}). Deploy Claude or GPT.")


def make_ai_fn(model: str, temperature: float = 0.15,
               max_tokens: int = 8000) -> Callable[[str, str], str]:
    """Return ai_fn(system_prompt, user_prompt) -> text for `model`.

    Retries throttling/transient errors twice with backoff. Raises AIError when
    the answer was cut off at max_tokens (callers parse JSON; a truncated answer
    is worse than a clear error)."""
    family = _family(model)

    if family == "bedrock":
        from gen_ai_hub.proxy.native.amazon.clients import Session
        client = Session().client(model_name=model)

        def _call(system_prompt: str, user_prompt: str, tokens: int) -> str:
            r = client.converse(
                system=[{"text": system_prompt}],
                messages=[{"role": "user", "content": [{"text": user_prompt}]}],
                inferenceConfig={"maxTokens": tokens, "temperature": temperature},
            )
            if r.get("stopReason") == "max_tokens":
                raise AIError(f"AI answer truncated at {tokens} tokens.")
            parts = r["output"]["message"]["content"]
            return "".join(p.get("text", "") for p in parts)
    else:
        from gen_ai_hub.proxy.native.openai import chat

        def _call(system_prompt: str, user_prompt: str, tokens: int) -> str:
            r = chat.completions.create(
                model_name=model,
                messages=[{"role": "system", "content": system_prompt},
                          {"role": "user", "content": user_prompt}],
                temperature=temperature, max_tokens=tokens)
            if r.choices[0].finish_reason == "length":
                raise AIError(f"AI answer truncated at {tokens} tokens.")
            return r.choices[0].message.content

    def ai_fn(system_prompt: str, user_prompt: str) -> str:
        last = None
        for attempt in range(3):
            try:
                return _call(system_prompt, user_prompt, max_tokens)
            except AIError:
                raise
            except Exception as e:  # noqa: BLE001
                last = e
                text = str(e).lower()
                if not any(t in text for t in ("throttl", "429", "rate", "timeout",
                                                "temporarily", "503", "502")):
                    break
                time.sleep(2 * (attempt + 1))
        raise AIError(f"AI call to '{model}' failed: {last}")

    ai_fn.model = model  # type: ignore[attr-defined]
    return ai_fn


# ─────────────────────────────────────────────────────────────────────────────
# Multimodal: text + images / PDFs (style references, brand guidelines, mockups)
# ─────────────────────────────────────────────────────────────────────────────
_IMG = {"image/png": "png", "image/jpeg": "jpeg", "image/jpg": "jpeg",
        "image/gif": "gif", "image/webp": "webp"}


def make_multimodal_fn(model: str, temperature: float = 0.1, max_tokens: int = 4000):
    """fn(system_prompt, user_text, attachments) -> text, where attachments is a
    list of (bytes, mime_type, name). Images and PDFs are passed to the model
    natively; anything else should be sent as text by the caller."""
    family = _family(model)

    if family == "bedrock":
        from gen_ai_hub.proxy.native.amazon.clients import Session
        client = Session().client(model_name=model)

        def fn(system_prompt, user_text, attachments=()):
            content = []
            for i, (data, mime, name) in enumerate(attachments):
                if mime in _IMG:
                    content.append({"image": {"format": _IMG[mime], "source": {"bytes": data}}})
                elif mime == "application/pdf":
                    safe = "".join(c for c in (name or f"doc{i}") if c.isalnum() or c in " -_")[:60] or f"doc{i}"
                    content.append({"document": {"format": "pdf", "name": safe,
                                                 "source": {"bytes": data}}})
            content.append({"text": user_text})
            r = client.converse(system=[{"text": system_prompt}],
                                messages=[{"role": "user", "content": content}],
                                inferenceConfig={"maxTokens": max_tokens, "temperature": temperature})
            if r.get("stopReason") == "max_tokens":
                raise AIError(f"AI answer truncated at {max_tokens} tokens.")
            return "".join(p.get("text", "") for p in r["output"]["message"]["content"])
    else:
        import base64
        from gen_ai_hub.proxy.native.openai import chat

        def fn(system_prompt, user_text, attachments=()):
            parts = [{"type": "text", "text": user_text}]
            for data, mime, _ in attachments:
                if mime in _IMG:
                    parts.append({"type": "image_url", "image_url": {
                        "url": f"data:{mime};base64," + base64.b64encode(data).decode()}})
            r = chat.completions.create(
                model_name=model, temperature=temperature, max_tokens=max_tokens,
                messages=[{"role": "system", "content": system_prompt},
                          {"role": "user", "content": parts}])
            return r.choices[0].message.content
    return fn
