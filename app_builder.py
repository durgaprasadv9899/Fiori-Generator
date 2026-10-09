"""
app_builder.py — end-to-end: requirement + service metadata -> deployable app.

    metadata (EDMX) ──> parse + annotations ──> blueprint (AI, validated)
        ──> renderer (Fiori Elements | Overview Page | Freestyle)
        ──> i18n (+ AI translations) ──> deployment (MTA, approuter, XSUAA,
            destination, connectivity) ──> local preview (mock server)
        ──> quality validation (+ safe auto-fixes)

The blueprint can be reviewed/edited between design and rendering: call
design() first, then build(..., blueprint=edited) — it is re-validated against
the metadata, so an edit can never produce a dangling reference.
"""

from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import metadata_parser as mdp
import annotation_reader as ar
import blueprint as bpm
import fe_renderer
import freestyle_renderer
import quality_validator as qv
import style as stylemod
from ui5_common import RenderContext, i18n_properties, mock_yaml, package_json, readme
from odata_config import ODataService, AUTH_NONE
from generator import (AppSpec, _make_mta_yaml_hardcoded, _make_root_xs_app_json_hardcoded,
                       _make_auth_config_files)


@dataclass
class BuildResult:
    model: Dict
    blueprint: Dict
    notes: List[str]
    source: str                       # "ai" | "rules" | "edited"
    files: Dict[str, str]
    quality: Dict
    fixes: List[str]
    spec: AppSpec
    translation_note: str = ""


def load_model(edmx: str) -> Dict:
    """Parsed + annotation-enriched service model, cached by metadata content
    (design, review and build all need it; large SAP services take seconds to parse)."""
    import copy
    import hashlib
    key = hashlib.sha256(edmx.encode("utf-8", "replace")).hexdigest()
    if key not in _MODEL_CACHE:
        model = mdp.parse_metadata(edmx)
        if len(_MODEL_CACHE) > 10:
            _MODEL_CACHE.pop(next(iter(_MODEL_CACHE)))
        _MODEL_CACHE[key] = ar.enrich_model_with_annotations(model, edmx)
    return copy.deepcopy(_MODEL_CACHE[key])


_MODEL_CACHE: Dict[str, Dict] = {}


def load_app_model(edmx: str, service_url: str = "", extra_services: Optional[List[Dict]] = None) -> Dict:
    """Model of the whole app: the service itself, or — with extra services
    ([{"edmx", "service_url", "alias"?, "title"?}]) — the merged multi-service model."""
    if not extra_services:
        return load_model(edmx)
    import multi_service as ms
    return ms.merge([{"edmx": edmx, "service_url": service_url}] + list(extra_services), load_model)


def design(requirement: str, edmx: str, ai_fn: Optional[Callable] = None,
           floorplan: str = "", style: Optional[Dict] = None,
           progress: Optional[Callable[[str], None]] = None, *, service_url: str = "",
           extra_services: Optional[List[Dict]] = None) -> Dict:
    """Design step: requirement analysis -> blueprint -> repair -> coverage.
    Returns {"model", "blueprint", "notes", "source", "spec", "coverage", "steps"}."""
    import ai_designer
    model = load_app_model(edmx, service_url, extra_services)
    res = ai_designer.design(requirement, model, ai_fn, floorplan, style, progress=progress)
    return dict(res, model=model)


def refine(blueprint: Dict, instruction: str, requirement: str, edmx: str, ai_fn: Callable,
           spec: Optional[Dict] = None, *, service_url: str = "",
           extra_services: Optional[List[Dict]] = None) -> Dict:
    """Apply a chat instruction to a designed blueprint (re-validated)."""
    import ai_designer
    model = load_app_model(edmx, service_url, extra_services)
    bp, notes = ai_designer.refine(blueprint, instruction, requirement, model, ai_fn, spec)
    return {"model": model, "blueprint": bp, "notes": notes}


# ─────────────────────────────────────────────────────────────────────────────
# i18n translation
# ─────────────────────────────────────────────────────────────────────────────
def translate(entries: Dict[str, str], languages: List[str],
              ai_fn: Optional[Callable]) -> (Dict[str, Dict[str, str]], str):
    targets = [l for l in languages if l != "en"]
    if not targets:
        return {}, ""
    if not ai_fn:
        return {}, "No AI available — only English texts were generated."
    # Fast path: one AI call per (language, chunk of texts) in parallel, identical texts
    # translated once, and a process-wide cache so rebuilds/refinements reuse results.
    system = ("You translate SAP Fiori UI texts. Keep placeholders like {0} unchanged, keep "
              "SAP terminology standard for the language, keep texts short. Reply with STRICT "
              "minified JSON only, mapping each given number to its translation: {\"1\": \"...\"}.")
    texts = sorted(set(entries.values()))
    failures = []

    def run(lang, chunk):
        numbered = {str(i + 1): t for i, t in enumerate(chunk)}
        user = (f"Target language: {lang}\nTexts (English):\n"
                f"{json.dumps(numbered, ensure_ascii=False, separators=(',', ':'))}")
        raw = ai_fn(system, user)
        try:
            data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        except ValueError as e:           # e.g. an unescaped quote inside a translation
            raw = ai_fn(system, user + f"\n\nYour previous answer was not valid JSON ({e}). Escape every "
                                       f"double quote inside texts as \\\" or use typographic quotes.")
            data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        return {t: str(data.get(str(i + 1)) or t) for i, t in enumerate(chunk)}

    jobs = []
    for lang in targets:
        todo = [t for t in texts if (lang, t) not in _TR_CACHE]
        jobs += [(lang, todo[i:i + 120]) for i in range(0, len(todo), 120)]
    if jobs:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(6, len(jobs))) as pool:
            futures = {pool.submit(run, lang, chunk): lang for lang, chunk in jobs}
            for fut, lang in futures.items():
                try:
                    for t, tr in fut.result().items():
                        _TR_CACHE[(lang, t)] = tr
                except Exception as e:  # noqa: BLE001
                    failures.append(f"{lang}: {str(e)[:80]}")
    out = {lang: {k: _TR_CACHE.get((lang, v), v) for k, v in entries.items()} for lang in targets}
    note = (f"Some translations failed ({'; '.join(failures)[:200]}) — English is used for those texts."
            if failures else "")
    return out, note


_TR_CACHE: Dict = {}


# ─────────────────────────────────────────────────────────────────────────────
# Deployment + local dev files
# ─────────────────────────────────────────────────────────────────────────────
def _ui5_yaml(ctx: RenderContext, destination: str) -> str:
    prefix = "/" + ctx.service_path.strip("/").split("/")[0]
    dest = f"\n        destination: {destination}" if destination else ""
    # (multi-service apps: all services of one backend share the /sap prefix)
    return f"""# Local development against the real backend (BAS or local machine).
#   In BAS the destination below is used directly.
#   Locally: set url to the backend host and put credentials in .env
#   (FIORI_TOOLS_USER / FIORI_TOOLS_PASSWORD).
specVersion: "3.0"
metadata:
  name: {ctx.app_id}
type: application
server:
  customMiddleware:
    - name: fiori-tools-proxy
      afterMiddleware: compression
      configuration:
        ignoreCertError: false
        backend:
          - path: {prefix}
            url: https://<backend-host>{dest}
"""


def _spec(ctx: RenderContext, *, app_title: str, auth_type: str, use_existing_destination: bool,
          destination_name: str, on_premise: bool, theme: str) -> AppSpec:
    import multi_service as ms
    main = ctx.bp["main"]
    svc = ODataService(
        id="mainService", url=ctx.service_path, odata_version=ctx.model["odata_version"],
        entity_set=main["entity_set"], key_field=main["key"][0] if main["key"] else "",
        title_field=main["title_field"], fields=[c["field"] for c in main["columns"]],
        auth_type=auth_type, is_primary=True, use_existing_destination=use_existing_destination,
        destination=destination_name.strip(), on_premise=on_premise)
    services = [svc]
    # further services of a multi-service app: same backend destination, own approuter route
    for s in ms.services(ctx.model)[1:]:
        es0 = next((e for e in ctx.model["entity_sets"] if e.get("service") == s["alias"]), None)
        services.append(ODataService(
            id=s["alias"], url="/" + s["service_url"].strip().strip("/") + "/",
            odata_version=ctx.model["odata_version"], entity_set=es0["name"] if es0 else "",
            key_field=(es0["keys"][0] if es0 and es0["keys"] else ""), title_field="",
            auth_type=auth_type, use_existing_destination=use_existing_destination,
            destination=destination_name.strip(), on_premise=on_premise))
    return AppSpec(user_prompt=ctx.bp["app"]["description"], app_id=ctx.app_id, app_name=app_title,
                   odata_url=ctx.service_path, entity_set=main["entity_set"],
                   key_field=svc.key_field, title_field=main["title_field"], theme=theme,
                   with_deployment=True, services=services, auth_type=auth_type)


def _mock_yaml_multi(ctx: RenderContext, extra: List[Dict]) -> str:
    """ui5-mock.yaml serving every service of a multi-service app from its own metadata."""
    base = mock_yaml(ctx)
    more = "".join(f"""          - urlPath: {"/" + s["service_url"].strip().strip("/")}
            metadataPath: ./webapp/localService/{s["alias"]}/metadata.xml
            mockdataPath: ./webapp/localService/{s["alias"]}/data
            generateMockData: true
""" for s in extra)
    return base.replace("        annotations: []", more + "        annotations: []", 1)


# ─────────────────────────────────────────────────────────────────────────────
# Build
# ─────────────────────────────────────────────────────────────────────────────
def build(*, edmx: str, requirement: str, app_id: str, service_url: str,
          ai_fn: Optional[Callable] = None, blueprint: Optional[Dict] = None,
          floorplan: str = "", auth_type: str = AUTH_NONE,
          use_existing_destination: bool = False, destination_name: str = "",
          on_premise: bool = False, theme: str = "sap_horizon",
          translate_texts: bool = True, style: Optional[Dict] = None,
          logo_data_uri: str = "", design_spec: Optional[Dict] = None,
          coverage: Optional[List] = None, extra_services: Optional[List[Dict]] = None) -> BuildResult:
    if not re.match(r"^[a-zA-Z][\w]*(\.[a-zA-Z][\w]*)+$", app_id or ""):
        raise ValueError(f"App ID '{app_id}' must be a dotted namespace like com.company.app.")
    if not (service_url or "").strip():
        raise ValueError("The OData service path the deployed app should call is required.")

    import multi_service as ms
    extra_services = [s for s in (extra_services or []) if (s.get("edmx") or "").strip()]
    model = load_app_model(edmx, service_url, extra_services)
    if blueprint is None:
        bp, notes, source = bpm.design(requirement, model, ai_fn, floorplan, style)
    else:
        if style and not blueprint.get("style"):
            blueprint = dict(blueprint, style=style)
        bp, notes = bpm.validate(blueprint, model, requirement)
        source = "edited"

    # A non-default style decides the base theme; otherwise keep the chosen one.
    st = bp.get("style") or stylemod.default_style()
    if not stylemod.is_default(st):
        theme = st["base_theme"]
    ctx = RenderContext(model=model, bp=bp, app_id=app_id, service_url=service_url,
                        edmx=edmx, theme=theme, logo=logo_data_uri)
    fam = bpm.family(bp["floorplan"])
    if fam in ("fe", "ovp"):
        files = fe_renderer.render(ctx)
    elif bp["floorplan"] == "FreestyleApp":
        files = freestyle_renderer.render_app(ctx)
    else:
        files = freestyle_renderer.render(ctx)
    notes.extend(n for n in ctx.notes if n not in notes)

    # Look & feel: theme-parameter overrides (+ web font) registered in the manifest
    style_css = stylemod.css(st)
    if style_css:
        files["css/style.css"] = style_css
        m = json.loads(files["manifest.json"])
        m.setdefault("sap.ui5", {}).setdefault("resources", {}).setdefault("css", []).append(
            {"uri": "css/style.css"})
        files["manifest.json"] = json.dumps(m, indent=4)

    # i18n (English is the default bundle; other languages via AI)
    files["i18n/i18n.properties"] = i18n_properties(ctx.i18n)
    tr_note = ""
    if translate_texts:
        translations, tr_note = translate(dict(ctx.i18n), bp["app"]["languages"], ai_fn)
        for lang, entries in translations.items():
            files[f"i18n/i18n_{lang}.properties"] = i18n_properties(entries)
    if tr_note:
        notes.append(tr_note)

    # Local preview with mock data, real-backend proxy, docs
    files["localService/metadata.xml"] = edmx
    files["ui5-mock.yaml"] = mock_yaml(ctx)
    extra_info = ms.services(model)[1:]
    if extra_info:
        by_url = {"/" + s["service_url"].strip().strip("/"): s for s in extra_services}
        for s in extra_info:
            src = by_url.get("/" + s["service_url"].strip().strip("/")) or {}
            files[f"localService/{s['alias']}/metadata.xml"] = src.get("edmx", "")
        files["ui5-mock.yaml"] = _mock_yaml_multi(ctx, extra_info)
        for n in model.get("merge_notes") or []:
            notes.append(n)
    files["ui5.yaml"] = _ui5_yaml(ctx, destination_name if use_existing_destination else "")
    files["package.json"] = package_json(ctx)
    files["README.md"] = readme(ctx)

    # BTP deployment (approuter + XSUAA + destination [+ connectivity])
    spec = _spec(ctx, app_title=bp["app"]["title"], auth_type=auth_type,
                 use_existing_destination=use_existing_destination,
                 destination_name=destination_name, on_premise=on_premise, theme=theme)
    files["mta.yaml"] = _make_mta_yaml_hardcoded(spec)
    files["xs-app.json"] = _make_root_xs_app_json_hardcoded(spec)
    files.update(_make_auth_config_files(spec))
    gi = files.get(".gitignore", "node_modules/\n.env\n")
    if "node_modules" not in gi:
        gi = "node_modules/\n" + gi
    files[".gitignore"] = gi
    # The design travels with the app: the studio re-opens it from the project ZIP or
    # the deployed CF app (package) to change and redeploy it later.
    import app_import
    files[app_import.DESIGN_FILE] = app_import.design_record(
        requirement=requirement, blueprint=bp, spec=design_spec, coverage=coverage, app_id=app_id,
        service_url=service_url, destination=destination_name, use_existing_destination=use_existing_destination,
        on_premise=on_premise, auth_type=auth_type, theme=theme,
        services=[{"alias": s["alias"], "title": s["title"], "service_url": s["service_url"],
                   "metadata": f"localService/{s['alias']}/metadata.xml"} for s in extra_info])

    # Quality gate (+ model-aware checks)
    qa = qv.validate_and_autofix(files)
    files = qa["files"]
    report = qv.merge_reports(qa["report_after"], qv.validate_against_model(files, model))
    return BuildResult(model=model, blueprint=bp, notes=notes, source=source, files=files,
                       quality=report, fixes=qa["fixes_applied"], spec=spec,
                       translation_note=tr_note)


def enhance_fe(*, original_text: Dict[str, str], info: Dict, edmx: str, blueprint: Dict, requirement: str,
               ai_fn: Optional[Callable] = None, translate_texts: bool = False) -> Dict:
    """Enhance a client Fiori Elements app in place (overlay annotations + merged manifest).
    Returns {"files", "notes", "quality", "blueprint"}."""
    import app_import
    model = load_model(edmx)
    bp, notes = bpm.validate(blueprint, model, requirement)
    if bpm.family(bp["floorplan"]) != "fe":
        bp["floorplan"] = "ListReportObjectPage"
        notes.append("Existing Fiori Elements apps keep their floorplan — enhanced as a List Report.")
    app_id = info.get("app_id") or "com.client.app"
    ctx = RenderContext(model=model, bp=bp, app_id=app_id, service_url=info.get("service_path") or "/",
                        edmx=edmx)
    gen = fe_renderer.render(ctx)
    gen["i18n/i18n.properties"] = i18n_properties(ctx.i18n)
    files, n2 = app_import.fe_overlay(original_text, info, gen)
    notes += n2 + ctx.notes
    # quality: XML/JSON of everything we touched
    errs = app_import.check_files({k: v for k, v in files.items() if original_text.get(k) != v})
    return {"files": files, "notes": notes, "errors": errs, "blueprint": bp}
