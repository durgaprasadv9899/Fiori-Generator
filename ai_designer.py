"""
ai_designer.py — multi-step AI design pipeline (requirement -> validated blueprint).

  1. analyze   the requirement becomes a structured spec: features (each with
               an id and priority), the entity sets involved, business rules,
               assumptions and the gaps the service cannot support.
  2. design    the blueprint, from the requirement + spec + the RELEVANT part
               of the metadata (large SAP services would otherwise flood the
               context), with the full component catalogue (pages, logic).
  3. repair    validation never trusts the AI: whatever it had to drop or
               change is fed back to the AI once to fix (instead of silently
               losing the feature), and the better of the two designs is kept.
  4. coverage  every feature is checked against the final blueprint:
               covered / partial / missing (+ where / why).
  +  refine    an existing blueprint + a chat instruction -> updated blueprint.

Every step degrades gracefully: no AI -> rule-based design; a failed step is
reported in `steps` and the pipeline continues.
"""

from __future__ import annotations

import json
import re
import time
from typing import Callable, Dict, List, Optional, Tuple

import blueprint as bpm
import multi_service as _ms
import style as stylemod

AiFn = Callable[[str, str], str]

_DROP = re.compile(r"(dropped|unknown|does not exist|not a field|not allowed|not available|"
                   r"replaced by|needs a|not usable|is off|disabled|not updatable|no such)", re.I)


# ─────────────────────────────────────────────────────────────────────────────
# JSON calls (with one self-repair round for malformed JSON)
# ─────────────────────────────────────────────────────────────────────────────
COMPACT = ("\n\nOUTPUT FORMAT: minified JSON on one line (no indentation, no line breaks, no comments) "
           "— generation time is proportional to the answer length.")


def merge_patch(raw: Dict, patch: Dict) -> Dict:
    """Merge a repair patch: pages are replaced/added by id (or title), other keys replaced."""
    import copy
    out = copy.deepcopy(raw)
    if not isinstance(patch, dict):
        return out
    if "blueprint" in patch and isinstance(patch["blueprint"], dict) and len(patch) == 1:
        patch = patch["blueprint"]          # some answers wrap the patch
    for k, v in patch.items():
        if k == "pages" and isinstance(v, list) and isinstance(out.get("pages"), list):
            pages = out["pages"]
            for p in v:
                if not isinstance(p, dict):
                    continue
                idx = next((i for i, q in enumerate(pages) if isinstance(q, dict) and (
                    (p.get("id") and q.get("id") == p.get("id")) or
                    (not p.get("id") and p.get("title") and q.get("title") == p.get("title")))), None)
                if p.get("_delete") and idx is not None:
                    pages.pop(idx)
                elif idx is None:
                    pages.append(p)
                else:
                    pages[idx] = p
        else:
            out[k] = v
    return out


def ask_json(ai_fn: AiFn, system: str, user: str) -> Dict:
    raw = ai_fn(system + COMPACT, user)
    try:
        return bpm._extract_json(raw)
    except Exception as e:  # noqa: BLE001
        fix = ai_fn("You repair JSON. Reply with the corrected JSON object only — no prose, no fences.",
                    f"This answer is not valid JSON ({e}). Return it as valid JSON, unchanged in "
                    f"content:\n\n{raw[:60000]}")
        return bpm._extract_json(fix)


# ─────────────────────────────────────────────────────────────────────────────
# Metadata context
# ─────────────────────────────────────────────────────────────────────────────
def compact_catalog(model: Dict, max_props: int = 150) -> str:
    """Short service overview for the analysis step."""
    lines = [f"OData V{model['odata_version'][0]} service with {len(model['entity_sets'])} entity sets."]
    if _ms.is_multi(model):
        lines = [_ms.catalog_header(model)]
    by_type = {s["entity_type"]: s["name"] for s in model["entity_sets"]}
    for es in sorted(model["entity_sets"], key=bpm._is_auxiliary):
        crud = "".join(c if es.get(f) else "-" for c, f in
                       (("C", "creatable"), ("U", "updatable"), ("D", "deletable")))
        props = ", ".join(p["name"] for p in es["properties"][:max_props])
        more = f" +{len(es['properties']) - max_props}" if len(es["properties"]) > max_props else ""
        navs = ", ".join(f"{n['name']}->{by_type.get(n.get('target_type'), '?')}"
                         for n in es.get("navigations", [])[:6])
        ops = ", ".join(o["name"] for o in bpm._ops_for(model, es))
        lines.append(f"- {es['name']}{_ms.tag(model, es)} '{es.get('label', '')}' [{crud}{', aux' if bpm._is_auxiliary(es) else ''}]: "
                     f"{props}{more}" + (f" | navs: {navs}" if navs else "") + (f" | ops: {ops}" if ops else ""))
    return "\n".join(lines)


def focused_context(model: Dict, sets: List[str]) -> str:
    """Full detail for the chosen entity sets + their navigation targets + value-help sets."""
    names = {es["name"] for es in model["entity_sets"]}
    keep = {s for s in sets if s in names}
    if not keep:
        return bpm.context_for_ai(model)
    by_type = {s["entity_type"]: s for s in model["entity_sets"]}
    for es in model["entity_sets"]:
        if es["name"] in keep:
            for n in es.get("navigations", []):
                t = by_type.get(n.get("target_type"))
                if t:
                    keep.add(t["name"])
            for vl in ((es.get("annotations") or {}).get("value_lists") or {}).values():
                if isinstance(vl, dict) and vl.get("collection") in names:
                    keep.add(vl["collection"])
    sub = dict(model, entity_sets=[es for es in model["entity_sets"] if es["name"] in keep])
    ctx = bpm.context_for_ai(sub, max_props=80)
    others = [es["name"] for es in model["entity_sets"] if es["name"] not in keep]
    if others:
        ctx += ("\n\nOTHER ENTITY SETS (not detailed; use only if really needed): " + ", ".join(others[:80]))
    return ctx


# ─────────────────────────────────────────────────────────────────────────────
# 1. Requirement analysis
# ─────────────────────────────────────────────────────────────────────────────
ANALYSIS_SYSTEM = (
    "You are a senior SAP business analyst. Turn the user's application requirement into a precise, "
    "testable specification, using the REAL OData service overview. Reply with STRICT JSON only.\n"
    "{\n"
    ' "summary": str,                       // one sentence\n'
    ' "app_kind": "standard" | "custom",    // standard Fiori floorplan vs bespoke multi-page/custom UI\n'
    ' "personas": [str],\n'
    ' "entity_sets": [{"name": str, "role": str}],   // REAL entity set names, most important first\n'
    ' "features": [{"id": "F1", "text": str, "kind": "view|search|filter|create|edit|delete|action|'
    'calculation|validation|default|highlight|kpi|chart|board|calendar|wizard|navigation|i18n|style|other",\n'
    '               "priority": "must|should|could"}],   // split the requirement into atomic, checkable features\n'
    ' "rules": [str],                       // business rules / calculations stated or clearly implied\n'
    ' "assumptions": [str],                 // what you decided where the requirement is vague\n'
    ' "gaps": [{"text": str, "reason": str}],  // asked-for things this service CANNOT support '
    "(missing fields/operations/CRUD)\n"
    ' "suggested_floorplan": str            // one of: ' + ", ".join(bpm.FLOORPLANS) + "\n"
    "}\n"
    "Rules: list EVERY feature the requirement asks for (also implicit ones such as 'search' when "
    "it says 'find'), at most 25; feature texts max 12 words; roles max 8 words; at most 8 rules, 6 "
    "assumptions and 6 gaps, each one short sentence; never invent entity sets; mark something as a gap "
    "only when the service really lacks it (e.g. an action without an operation and without an "
    "updatable field, or a field that is not in the overview). The generator computes KPIs, charts "
    "and analyses client-side from the records, so server-side aggregation is NOT needed and never "
    "a gap; calculations, validations, defaults and highlighting are implemented as custom logic, "
    "so they are not gaps either."
)


def analyze(requirement: str, model: Dict, ai_fn: AiFn) -> Dict:
    spec = ask_json(ai_fn, ANALYSIS_SYSTEM,
                    f"REQUIREMENT:\n{requirement.strip()}\n\nSERVICE OVERVIEW:\n{compact_catalog(model)}")
    names = {es["name"] for es in model["entity_sets"]}
    ents = []
    for e in spec.get("entity_sets") or []:
        n = e.get("name") if isinstance(e, dict) else e
        if n in names and n not in [x["name"] for x in ents]:
            ents.append({"name": n, "role": str(e.get("role", "") if isinstance(e, dict) else "")[:120]})
    feats = []
    for i, f in enumerate(spec.get("features") or []):
        if not isinstance(f, dict) or not f.get("text"):
            continue
        feats.append({"id": f"F{i + 1}", "text": str(f["text"])[:200],
                      "kind": str(f.get("kind") or "other"),
                      "priority": f.get("priority") if f.get("priority") in ("must", "should", "could") else "must"})
    return {
        "summary": str(spec.get("summary") or "")[:300],
        "app_kind": "custom" if spec.get("app_kind") == "custom" else "standard",
        "personas": [str(p)[:80] for p in (spec.get("personas") or [])][:6],
        "entity_sets": ents,
        "features": feats[:40],
        "rules": [str(r)[:240] for r in (spec.get("rules") or [])][:20],
        "assumptions": [str(a)[:240] for a in (spec.get("assumptions") or [])][:12],
        "gaps": [{"text": str(g.get("text", ""))[:200], "reason": str(g.get("reason", ""))[:240]}
                 for g in (spec.get("gaps") or []) if isinstance(g, dict)][:12],
        "suggested_floorplan": spec.get("suggested_floorplan") if spec.get("suggested_floorplan") in bpm.FLOORPLANS else "",
    }


def spec_text(spec: Dict) -> str:
    out = [f"SUMMARY: {spec['summary']}", f"APP KIND: {spec['app_kind']}"]
    if spec["entity_sets"]:
        out.append("ENTITY SETS: " + "; ".join(f"{e['name']} ({e['role']})" for e in spec["entity_sets"]))
    out.append("FEATURES (implement every 'must' and as many 'should' as the service allows):")
    out += [f"  {f['id']} [{f['priority']}/{f['kind']}] {f['text']}" for f in spec["features"]]
    if spec["rules"]:
        out.append("BUSINESS RULES (use CUSTOM LOGIC where columns/filters cannot express them):")
        out += [f"  - {r}" for r in spec["rules"]]
    if spec["assumptions"]:
        out.append("ASSUMPTIONS: " + " | ".join(spec["assumptions"]))
    if spec["gaps"]:
        out.append("KNOWN GAPS (do not try to fake these): " + " | ".join(g["text"] for g in spec["gaps"]))
    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────────────
# 2 + 3. Design and repair
# ─────────────────────────────────────────────────────────────────────────────
def _design_user(requirement: str, spec: Optional[Dict], ctx: str, floorplan: str) -> str:
    return (f"BUSINESS REQUIREMENT:\n{requirement.strip()}\n\n"
            + (f"ANALYSED SPECIFICATION:\n{spec_text(spec)}\n\n" if spec else "")
            + (f"The user explicitly wants floorplan {floorplan}.\n\n" if floorplan else "")
            + f"SERVICE METADATA:\n{ctx}\n\nReturn the blueprint JSON.")


def _problems(notes: List[str]) -> List[str]:
    return [n for n in notes if _DROP.search(n)]


def design(requirement: str, model: Dict, ai_fn: Optional[AiFn], floorplan_override: str = "",
           style: Optional[Dict] = None, *, analyze_first: bool = True, repair: bool = True,
           check_coverage: bool = True, progress: Optional[Callable[[str], None]] = None,
           cache_tag: str = "") -> Dict:
    """Run the pipeline. Returns {blueprint, notes, source, spec, coverage, steps}.
    cache_tag (e.g. model + temperature): identical inputs return the cached design."""
    import copy
    import hashlib
    key = None
    if ai_fn and cache_tag:
        key = hashlib.sha256(json.dumps([requirement, floorplan_override, style, analyze_first, repair,
                                         check_coverage, cache_tag, model.get("namespace"),
                                         [(e["name"], len(e["properties"])) for e in model["entity_sets"]]],
                                        sort_keys=True, default=str).encode()).hexdigest()
        if key in _DESIGN_CACHE:
            if progress:
                progress("Same requirement and service as before — reusing the design.")
            res = copy.deepcopy(_DESIGN_CACHE[key])
            res["steps"] = [{"step": "cache", "ok": True, "detail": "reused the previous design (same input)",
                             "seconds": 0}]
            return res
    res = _design(requirement, model, ai_fn, floorplan_override, style, analyze_first=analyze_first,
                  repair=repair, check_coverage=check_coverage, progress=progress)
    if key and res.get("source") == "ai":
        if len(_DESIGN_CACHE) > 30:
            _DESIGN_CACHE.pop(next(iter(_DESIGN_CACHE)))
        _DESIGN_CACHE[key] = copy.deepcopy(res)
    return res


_DESIGN_CACHE: Dict[str, Dict] = {}


def _design(requirement: str, model: Dict, ai_fn: Optional[AiFn], floorplan_override: str = "",
            style: Optional[Dict] = None, *, analyze_first: bool = True, repair: bool = True,
            check_coverage: bool = True, progress: Optional[Callable[[str], None]] = None) -> Dict:
    steps: List[Dict] = []

    def step(name, ok, detail="", t0=None):
        steps.append({"step": name, "ok": ok, "detail": detail,
                      "seconds": round(time.time() - t0, 1) if t0 else None})

    def say(msg):
        if progress:
            progress(msg)

    if style and style.get("layout_hints"):
        requirement = (requirement.rstrip() + "\n\nVISUAL REFERENCE (from the user's image): "
                       + style["layout_hints"])
    if not ai_fn:
        bp, notes, source = bpm.design(requirement, model, None, floorplan_override, style)
        step("design", True, "rule-based (no AI model available)")
        return {"blueprint": bp, "notes": notes, "source": source, "spec": None, "coverage": [], "steps": steps}

    spec = None
    if analyze_first:
        say("Analysing the requirement…")
        t0 = time.time()
        try:
            spec = analyze(requirement, model, ai_fn)
            step("analyze", True, f"{len(spec['features'])} features, {len(spec['entity_sets'])} entity sets, "
                                  f"{len(spec['gaps'])} gaps", t0)
        except Exception as e:  # noqa: BLE001
            step("analyze", False, str(e)[:200], t0)

    sets = [e["name"] for e in (spec or {}).get("entity_sets", [])]
    ctx = (focused_context(model, sets) if sets else bpm.context_for_ai(model)) + _ms.prompt_rules(model)
    fp = floorplan_override or ""
    if not fp and spec and spec["app_kind"] == "custom" and spec["suggested_floorplan"].startswith("Freestyle"):
        fp = ""   # let the designer choose among the freestyle floorplans

    say("Designing the application…")
    t0 = time.time()
    try:
        raw = ask_json(ai_fn, bpm.system_prompt(), _design_user(requirement, spec, ctx, fp))
        step("design", True, f"floorplan {raw.get('floorplan')}", t0)
    except Exception as e:  # noqa: BLE001
        step("design", False, str(e)[:200], t0)
        bp, notes, _ = bpm.design(requirement, model, None, floorplan_override, style)
        notes.insert(0, f"AI design failed ({str(e)[:160]}); used rule-based design.")
        return {"blueprint": bp, "notes": notes, "source": "rules", "spec": spec, "coverage": [], "steps": steps}

    if floorplan_override in bpm.FLOORPLANS:
        raw["floorplan"] = floorplan_override
    if style:
        raw["style"] = style
    bp, notes = bpm.validate(raw, model, requirement)
    problems = _problems(notes)

    if repair and problems:
        say(f"Repairing {len(problems)} design problem(s)…")
        t0 = time.time()
        try:
            # Patch, not a rewrite: the AI returns only the parts it changes (whole
            # pages by id, or top-level keys), merged into the design — far less output.
            fix_user = ("BUSINESS REQUIREMENT:\n" + requirement.strip()
                        + f"\n\nSERVICE METADATA:\n{ctx}"
                        + "\n\nYOUR DESIGN:\n" + json.dumps(raw, separators=(",", ":"))[:50000]
                        + "\n\nVALIDATION AGAINST THE REAL SERVICE FOUND THESE PROBLEMS:\n"
                        + "\n".join(f"- {p}" for p in problems)
                        + "\n\nFix every problem using only names that exist in the metadata (or drop the "
                          "part when the service cannot support it). Return ONLY a JSON patch with the parts "
                          "you change: {\"pages\": [<each changed page, complete, with its id>], "
                          "\"<other top-level key>\": <its complete new value>}. Do not repeat unchanged parts.")
            patch = ask_json(ai_fn, bpm.system_prompt(), fix_user)
            raw2 = merge_patch(raw, patch)
            if floorplan_override in bpm.FLOORPLANS:
                raw2["floorplan"] = floorplan_override
            if style:
                raw2["style"] = style
            bp2, notes2 = bpm.validate(raw2, model, requirement)
            p2 = _problems(notes2)
            if len(p2) <= len(problems):
                bp, notes = bp2, notes2
                step("repair", True, f"{len(problems)} → {len(p2)} problem(s)", t0)
            else:
                step("repair", True, f"kept the first design ({len(problems)} vs {len(p2)} problems)", t0)
        except Exception as e:  # noqa: BLE001
            step("repair", False, str(e)[:200], t0)

    coverage: List[Dict] = []
    if check_coverage and spec and spec["features"]:
        say("Checking requirement coverage…")
        t0 = time.time()
        try:
            coverage = assess_coverage(spec, bp, ai_fn)
            n_ok = sum(c["status"] == "covered" for c in coverage)
            step("coverage", True, f"{n_ok}/{len(coverage)} features covered", t0)
        except Exception as e:  # noqa: BLE001
            step("coverage", False, str(e)[:200], t0)
    return {"blueprint": bp, "notes": notes, "source": "ai", "spec": spec, "coverage": coverage, "steps": steps}


# ─────────────────────────────────────────────────────────────────────────────
# 4. Coverage
# ─────────────────────────────────────────────────────────────────────────────
def summarize_blueprint(bp: Dict) -> str:
    """Human/AI-readable outline of a validated blueprint."""
    out = [f"App '{bp['app']['title']}' — floorplan {bp['floorplan']} — languages {', '.join(bp['app']['languages'])}"]

    def entity_part(main, op, caps, form, actions, logic, prefix="  "):
        lines = [f"{prefix}entity {main['entity_set']}: columns {', '.join(c['field'] for c in main['columns'])}; "
                 f"filters {', '.join(f['field'] for f in main['filters']) or '-'}; search {main['search']}; "
                 f"sort {main['sort']['field']}; status {main['status']['field'] or '-'}"]
        lines.append(f"{prefix}detail sections: " + "; ".join(f"{s['title']} ({len(s['fields'])} fields)"
                                                                for s in op["sections"])
                     + (" | child tables: " + ", ".join(t["title"] for t in op["tables"]) if op["tables"] else ""))
        lines.append(f"{prefix}CRUD: " + ", ".join(k for k, v in caps.items() if v) + f"; create fields "
                     f"{', '.join(form['create_fields'])}; required {', '.join(form['required']) or '-'}")
        for a in actions:
            what = a.get("operation") or (f"{a.get('field')}={a.get('value')}" if a["kind"] == "set_field"
                                          else "script: " + (a.get("script") or "")[:300])
            ew = a.get("enabled_when") or {}
            lines.append(f"{prefix}action '{a['label']}' ({a['kind']}: {what}, {a['scope']}"
                         + (", asks for confirmation" if a.get("confirm") else ", no confirmation") + ")"
                         + (f" only when {ew['field']} in {ew['values']}" if ew else ""))
        lg = logic or {}
        for c in lg.get("computed") or []:
            lines.append(f"{prefix}calculated column '{c['label']}' = {c['expression']}")
        if lg.get("row_state"):
            lines.append(f"{prefix}row highlight = {lg['row_state']['expression']}")
        for v in lg.get("validations") or []:
            lines.append(f"{prefix}validation '{v['message']}': {v['expression']}")
        for d in lg.get("defaults") or []:
            lines.append(f"{prefix}default {d['field']} = {d['expression']}")
        for d in lg.get("derived") or []:
            lines.append(f"{prefix}derived {d['field']} = {d['expression']}")
        return lines

    if bp.get("pages"):
        for p in bp["pages"]:
            if p["type"] == "dashboard":
                out.append(f"- page '{p['title']}' DASHBOARD: KPIs {', '.join(k['label'] for k in p['kpis']) or '-'}; "
                           f"charts {', '.join(c['title'] + ' (' + c['type'] + ')' for c in p['charts']) or '-'}; "
                           f"lists {', '.join(l['title'] for l in p['lists']) or '-'}")
            elif p["type"] == "list":
                s = p["sub"]
                extra = []
                if p.get("layout") == "split":
                    extra.append("split layout (list + record side by side)")
                if p.get("quick_tabs"):
                    extra.append("status tabs with counts")
                if p["detail_id"]:
                    extra.append("detail page")
                if p["create"] == "wizard":
                    extra.append("create wizard: " + " → ".join(st["title"] for st in p["wizard_steps"])
                                 + " → Review (read-only summary of all entries before Create)")
                out.append(f"- page '{p['title']}' LIST" + (f" ({'; '.join(extra)})" if extra else ""))
                out += entity_part(s["main"], s["object_page"], s["capabilities"], s["form"], s["actions"], s.get("logic"))
            elif p["type"] == "kanban":
                out.append(f"- page '{p['title']}' KANBAN on {p['entity_set']} by {p['lane_field']} lanes "
                           f"{[l['title'] for l in p['lanes']]} (drag&drop {'on' if p['move'] else 'off'})")
            elif p["type"] == "calendar":
                out.append(f"- page '{p['title']}' CALENDAR on {p['entity_set']} by {p['start_field']}"
                           + (f"–{p['end_field']}" if p["end_field"] else ""))
            elif p["type"] == "analytics":
                out.append(f"- page '{p['title']}' ANALYTICS: " + "; ".join(
                    f"{c['title']} ({c['type']}: {', '.join(m['aggregation'] + ' ' + (m.get('expression') or m['field'] or 'count') for m in c['measures'])}"
                    f" by {c['dimension']}{(' per ' + c['time_grain']) if c['time_grain'] else ''})" for c in p["charts"]))
    else:
        out += entity_part(bp["main"], bp["object_page"], bp["capabilities"], bp["form"], bp["actions"], bp.get("logic"))
        an = bp.get("analytics") or {}
        if bp["floorplan"] in ("AnalyticalListPage",) and an.get("measures"):
            out.append(f"  chart {an['chart']}: {', '.join(m['field'] for m in an['measures'])} by {', '.join(an['dimensions'])}")
        if an.get("kpis"):
            out.append("  KPIs: " + ", ".join(k["label"] for k in an["kpis"]))
        for c in bp.get("cards") or []:
            out.append(f"  card '{c['title']}' ({c['type']})")
    fe = bp.get("fe") or {}
    if bpm.family(bp["floorplan"]) == "fe" and fe:
        if fe.get("table_views"):
            out.append("table tabs: " + "; ".join(
                v["title"] + (" (" + ", ".join(f"{f['field']} in {f['values']}" for f in v["filters"]) + ")" if v["filters"] else "")
                for v in fe["table_views"]))
        out.append("layout: " + ("flexible column layout (list and object side by side)" if fe.get("layout") == "fcl"
                                 else "full-screen pages"))
        for c in fe.get("header_microcharts") or []:
            out.append(f"header {c['type']} chart '{c['title']}': {c['field']} vs "
                       f"{c['target_field'] or c['target_value']}")
        for r in fe.get("field_rules") or []:
            for k in ("readonly_when", "hidden_when", "mandatory_when"):
                if r.get(k):
                    out.append(f"field rule: {r['field']} {k.split('_')[0]} when {r[k]['field']} in {r[k]['values']}"
                               + ("" if bp.get("_v4", True) else ""))
        for se in fe.get("side_effects") or []:
            out.append(f"side effect: after changing {', '.join(se['source'])} refresh "
                       f"{', '.join(se['target_properties'] + se['target_entities'])}")
        if fe.get("personalization"):
            out.append("table personalization (columns, sort, filter), Excel export, variant management")
    hdr = bp.get("object_page", {}).get("header_fields")
    if hdr:
        out.append("object header key figures: " + ", ".join(hdr))
    st = bp.get("style") or {}
    if st and not stylemod.is_default(st):
        out.append(f"style: theme {st['base_theme']}, font {st['font']['family'] or 'default'}, "
                   f"brand {st['colors'].get('brand') or '-'}")
    if bpm.family(bp["floorplan"]) == "freestyle":
        out.append(
            "BUILT INTO EVERY GENERATED FREESTYLE APP (not repeated above): list pages have free-text "
            "search over the title/description/text columns, a sort dialog (any column, asc/desc), Excel "
            "export, a record count, responsive layout (desktop/tablet/phone) "
            "and (when update/delete allowed) multi-select delete; rows open the detail page; detail pages "
            "have edit/save/cancel and delete; dashboard KPI tiles open their list page and the recent-"
            "records lists open the record; kanban cards and calendar appointments open the record's detail "
            "page when that entity has a list page with detail; analytics charts have an 'Open list' link; "
            "all texts are in i18n bundles (translated into the listed languages); value helps, required-"
            "field and type validation in create forms; side navigation between pages.")
    return "\n".join(out)


COVERAGE_SYSTEM = (
    "You are a strict QA lead. Compare each requirement feature with the generated application "
    "outline. Reply with STRICT JSON listing ONLY the features that are NOT fully covered: "
    '{"issues": [{"id": "F1", "status": "partial|missing", "where": str, "note": str}]} '
    "(every feature not listed counts as covered; an empty list means everything is covered). "
    "'partial' = only part of it, or only approximated; 'missing' = not in the outline. 'where' "
    "names the page/element (max 6 words); 'note' says what is missing or why the service cannot "
    "support it (max 20 words)."
)


def assess_coverage(spec: Dict, bp: Dict, ai_fn: AiFn) -> List[Dict]:
    feats = "\n".join(f"{f['id']} [{f['priority']}] {f['text']}" for f in spec["features"])
    gaps = "\n".join(f"- {g['text']}: {g['reason']}" for g in spec["gaps"])
    res = ask_json(ai_fn, COVERAGE_SYSTEM,
                   f"FEATURES:\n{feats}\n\nKNOWN SERVICE GAPS:\n{gaps or '-'}\n\n"
                   f"GENERATED APPLICATION:\n{summarize_blueprint(bp)}")
    items = res.get("issues")
    if items is None:                       # tolerate the older full-list shape
        items = res.get("coverage") or []
    by_id = {c.get("id"): c for c in items if isinstance(c, dict)}
    out = []
    for f in spec["features"]:
        c = by_id.get(f["id"]) or {}
        st = c.get("status") if c.get("status") in ("covered", "partial", "missing") else "covered"
        out.append({"id": f["id"], "text": f["text"], "priority": f["priority"], "status": st,
                    "where": str(c.get("where") or "")[:120], "note": str(c.get("note") or "")[:240]})
    return out


def coverage_score(coverage: List[Dict]) -> Optional[int]:
    """Weighted % (must x3, should x2, could x1; partial = half)."""
    if not coverage:
        return None
    w = {"must": 3, "should": 2, "could": 1}
    tot = sum(w[c["priority"]] for c in coverage)
    got = sum(w[c["priority"]] * {"covered": 1, "partial": .5}.get(c["status"], 0) for c in coverage)
    return round(100 * got / tot) if tot else None


# ─────────────────────────────────────────────────────────────────────────────
# Refine by chat
# ─────────────────────────────────────────────────────────────────────────────
def refine(bp: Dict, instruction: str, requirement: str, model: Dict, ai_fn: AiFn,
           spec: Optional[Dict] = None) -> Tuple[Dict, List[str]]:
    """Apply a natural-language change to a validated blueprint."""
    sets = sorted({p.get("entity_set") for p in bp.get("pages") or [] if p.get("entity_set")}
                  | {bp["main"]["entity_set"]})
    ctx = focused_context(model, sets) + _ms.prompt_rules(model)
    current = dict(bp)
    current.pop("style", None)          # the style is kept as is (edited in Look & feel)
    user = (f"ORIGINAL REQUIREMENT:\n{requirement.strip()}\n\n"
            + (f"SPECIFICATION:\n{spec_text(spec)}\n\n" if spec else "")
            + "CURRENT BLUEPRINT (validated):\n" + json.dumps(current, separators=(",", ":"))[:60000] + "\n\n"
            f"CHANGE REQUESTED BY THE USER:\n{instruction.strip()}\n\n"
            f"SERVICE METADATA:\n{ctx}\n\n"
            "Apply the change (and only the change). Return ONLY a JSON patch in the design schema with "
            "the parts you change: {\"pages\": [<each changed or new page, complete, with its id; "
            "{\"id\": ..., \"_delete\": true} removes a page>], \"<other top-level key>\": <its complete "
            "new value>}. Do not repeat unchanged parts.")
    raw = merge_patch(current, ask_json(ai_fn, bpm.system_prompt(), user))
    if bp.get("style"):
        raw["style"] = bp["style"]
    return bpm.validate(raw, model, requirement)
