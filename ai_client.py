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


def _bedrock_config():
    """botocore defaults (60 s read timeout, silent retries) break long answers: a big generation takes
    longer than 60 s, times out and is silently regenerated up to 5 times — minutes of apparent hanging.
    Long read timeout, one retry; throttling is retried by the callers."""
    from botocore.config import Config
    return Config(read_timeout=600, connect_timeout=30, retries={"max_attempts": 2, "mode": "standard"})


_NET_ERR = ("nameresolution", "getaddrinfo", "name or service not known", "failed to resolve", "connection",
            "prematurely", "reset", "incomplete", "chunked", "broken pipe", "eof", "network is unreachable", "timed out",
            "timeout", "proxy")
_BUSY_ERR = ("throttl", "429", "rate", "temporarily", "503", "502", "overloaded")
_NET_WAIT = (5, 10, 20, 30, 45)          # ~2 minutes in total: a Wi-Fi / VPN reconnect does not fail the work
_BUSY_WAIT = (3, 6, 10, 15)


def _wait_for(e: Exception, attempt: int) -> Optional[float]:
    """Seconds to wait before retrying `e`, or None when it must not be retried."""
    text = str(e).lower()
    if any(t in text for t in _NET_ERR):
        return _NET_WAIT[attempt] if attempt < len(_NET_WAIT) else None
    if any(t in text for t in _BUSY_ERR):
        return _BUSY_WAIT[attempt] if attempt < len(_BUSY_WAIT) else None
    return None


def _failure(model: str, e: Exception) -> AIError:
    text = str(e).lower()
    if any(t in text for t in ("nameresolution", "getaddrinfo", "failed to resolve", "name or service not known",
                               "network is unreachable", "max retries exceeded", "connection refused")):
        return AIError("Can't reach SAP AI Core — the network or DNS is not available from the studio host. Check the "
                       "internet / VPN connection and try again. (" + str(e)[:160] + ")")
    return AIError(f"AI call to '{model}' failed: {e}")


def make_ai_fn(model: str, temperature: float = 0.15,
               max_tokens: int = 8000) -> Callable[[str, str], str]:
    """Return ai_fn(system_prompt, user_prompt) -> text for `model`.

    Retries throttling/transient errors twice with backoff. Raises AIError when
    the answer was cut off at max_tokens (callers parse JSON; a truncated answer
    is worse than a clear error)."""
    family = _family(model)

    if family == "bedrock":
        from gen_ai_hub.proxy.native.amazon.clients import Session
        client = Session().client(model_name=model, config=_bedrock_config())

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
        attempt = 0
        while True:
            try:
                return _call(system_prompt, user_prompt, max_tokens)
            except AIError:
                raise
            except Exception as e:  # noqa: BLE001
                wait = _wait_for(e, attempt)
                if wait is None:
                    raise _failure(model, e)
                time.sleep(wait)
                attempt += 1

    ai_fn.model = model  # type: ignore[attr-defined]
    return ai_fn


class _BadToolJSON(AIError):
    pass


def _to_anthropic(messages: list) -> list:
    """Converse-format messages -> Anthropic Messages API format."""
    out = []
    for m in messages:
        blocks = []
        for c in m.get("content") or []:
            if c.get("text"):
                blocks.append({"type": "text", "text": c["text"]})
            elif "toolUse" in c:
                tu = c["toolUse"]
                blocks.append({"type": "tool_use", "id": tu["toolUseId"], "name": tu["name"], "input": tu.get("input") or {}})
            elif "toolResult" in c:
                tr = c["toolResult"]
                text = "\n".join(x.get("text", "") for x in tr.get("content") or []) or "(empty)"
                blocks.append({"type": "tool_result", "tool_use_id": tr["toolUseId"],
                               "content": [{"type": "text", "text": text}], "is_error": tr.get("status") == "error"})
        if blocks:
            out.append({"role": m["role"], "content": blocks})
    return out


def make_converse_fn(model: str, temperature: float = 0.2, max_tokens: int = 16000):
    """converse(system, messages, tools=None, on_progress=None) -> Converse-shaped response
    {"output": {"message": {...}}, "stopReason", "usage"} — multi-turn with tool use, for agents (the
    adoption robot). Messages and tool specs use the Converse API format.

    The answer is STREAMED (invoke_model_with_response_stream, Anthropic Messages format): a long answer
    sent in one piece (non-streaming) does not come back reliably through AI Core — big file rewrites
    stalled until read timeouts. Streaming keeps the connection busy and reports progress.
    Claude only (AI Core's Bedrock proxy)."""
    if _family(model) != "bedrock":
        raise AIError(f"The project robot needs an Anthropic Claude deployment (found '{model}').")
    import json as _json
    from gen_ai_hub.proxy.native.amazon.clients import Session
    client = Session().client(model_name=model, config=_bedrock_config())

    def once(body: dict, on_progress):
        r = client.invoke_model_with_response_stream(body=_json.dumps(body))
        blocks: dict = {}
        stop, usage, produced = None, {}, 0
        for ev in r["body"]:
            raw = (ev.get("chunk") or {}).get("bytes")
            d = _json.loads(raw) if raw else ev
            t = d.get("type")
            if t == "content_block_start":
                cb = d.get("content_block") or {}
                blocks[d["index"]] = {"type": cb.get("type"), "text": cb.get("text", ""), "id": cb.get("id"),
                                      "name": cb.get("name"), "json": ""}
            elif t == "content_block_delta":
                b, delta = blocks.setdefault(d["index"], {"type": "text", "text": "", "json": ""}), d.get("delta") or {}
                piece = delta.get("text") or delta.get("partial_json") or ""
                if delta.get("type") == "input_json_delta":
                    b["json"] += piece
                else:
                    b["text"] += piece
                produced += len(piece)
                if on_progress:
                    on_progress(produced)
            elif t == "message_start":
                usage.update(((d.get("message") or {}).get("usage")) or {})
            elif t == "message_delta":
                stop = (d.get("delta") or {}).get("stop_reason") or stop
                usage.update(d.get("usage") or {})
            elif t == "error" or "Exception" in str(t or ""):
                raise AIError(f"stream error: {d}")
        content = []
        for i in sorted(blocks):
            b = blocks[i]
            if b["type"] == "tool_use":
                try:                                           # strict=False: raw newlines/tabs inside strings
                    inp = _json.loads(b["json"] or "{}", strict=False)
                except ValueError:
                    if stop != "max_tokens":
                        raise _BadToolJSON(f"invalid tool input JSON from the model ({len(b['json'])} chars)")
                    inp = {"__partial_json": b["json"]}        # cut off in the middle of the tool input
                content.append({"toolUse": {"toolUseId": b["id"], "name": b["name"], "input": inp}})
            elif b.get("text"):
                content.append({"text": b["text"]})
        return {"output": {"message": {"role": "assistant", "content": content}}, "stopReason": stop or "end_turn",
                "usage": {"inputTokens": usage.get("input_tokens"), "outputTokens": usage.get("output_tokens")}}

    def converse(system: str, messages: list, tools: Optional[list] = None, tool_choice: Optional[dict] = None,
                 on_progress=None):
        # fine-grained tool streaming: tool inputs (e.g. a file's content) stream as they are written instead of
        # arriving in one piece at the end — so progress is visible and a cut-off answer can be salvaged
        body = {"anthropic_version": "bedrock-2023-05-31", "anthropic_beta": ["fine-grained-tool-streaming-2025-05-14"],
                "max_tokens": max_tokens, "temperature": temperature,
                "system": system, "messages": _to_anthropic(messages)}
        if tools:
            body["tools"] = [{"name": t["name"], "description": t.get("description", ""),
                              "input_schema": t["inputSchema"]["json"]} for t in tools]
        attempt, bad_json = 0, 0
        while True:
            try:
                return once(body, on_progress)
            except _BadToolJSON as e:
                # fine-grained streaming may (rarely) deliver malformed tool JSON — redo this call without it
                bad_json += 1
                if bad_json > 2:
                    raise _failure(model, e)
                body = {k: v for k, v in body.items() if k != "anthropic_beta"}
            except Exception as e:  # noqa: BLE001
                wait = _wait_for(e, attempt)
                if wait is None:
                    raise _failure(model, e)
                time.sleep(wait)
                attempt += 1

    converse.model = model  # type: ignore[attr-defined]
    return converse


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
        client = Session().client(model_name=model, config=_bedrock_config())

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
