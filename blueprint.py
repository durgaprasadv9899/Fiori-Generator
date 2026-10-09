"""
blueprint.py — prompt + metadata  ->  validated application blueprint.

The blueprint is the single contract between the AI and the code renderers
(fe_renderer, ovp_renderer, freestyle_renderer). The AI decides WHAT the app
should do; the renderers decide HOW it is written in SAPUI5. Keeping the AI's
job to a JSON spec (not raw code) is what makes the output reliable.

Shape (all names refer to the real service — enforced by validate()):

{
  "app": {"title", "description", "languages": ["en", ...]},
  "floorplan": one of FLOORPLANS,
  "main": {
     "entity_set", "key": [...], "title_field", "description_field",
     "columns": [{"field", "importance": "High|Medium|Low"}],
     "filters": [{"field"}], "search": bool,
     "sort": {"field", "descending"},
     "status": {"field", "states": {value: Success|Warning|Error|Information|None}}
  },
  "object_page": {
     "header_fields": [...],
     "sections": [{"title", "fields": [...]}],
     "tables": [{"navigation", "entity_set", "title", "columns": [...]}]
  },
  "capabilities": {"create", "update", "delete"},
  "form": {"create_fields": [...], "required": [...], "readonly": [...]},
  "value_helps": [{"field", "entity_set", "key", "text"}],
  "actions": [{"id", "label", "kind": "operation|set_field", "operation",
               "field", "value", "scope": "row|object|global", "confirm"}],
  "analytics": {"measures": [{"field", "aggregation"}], "dimensions": [...],
                "chart": "column|bar|line|pie|donut", "kpis": [{"field","label","aggregation"}]},
  "cards": [{"id", "type": "list|table|analytical", "title", "entity_set",
             "fields": [...], "measure", "dimension"}],
  "reasoning": str
}
"""

from __future__ import annotations
import json
import re
from typing import Callable, Dict, List, Optional, Tuple

import style as _style
import pages as _pages
import logic as _logic
import multi_service as _ms

FLOORPLANS = {
    # id                     family       description
    "ListReportObjectPage": ("fe",        "Fiori Elements List Report + Object Page"),
    "Worklist":             ("fe",        "Fiori Elements Worklist (table, no filter bar)"),
    "AnalyticalListPage":   ("fe",        "Fiori Elements Analytical List Page (chart + table)"),
    "OverviewPage":         ("ovp",       "Overview Page (cards dashboard)"),
    "FreestyleListDetail":  ("freestyle", "Freestyle list -> object page"),
    "FreestyleMasterDetail": ("freestyle", "Freestyle master-detail (flexible column layout)"),
    "FreestyleWorklist":    ("freestyle", "Freestyle worklist with KPI header and bulk actions"),
    "FreestyleApp":         ("freestyle", "Custom multi-page app: side navigation, dashboard, "
                                          "list/detail pages for several entities, wizard create"),
}
STATES = ("Success", "Warning", "Error", "Information", "None")
CHARTS = ("column", "bar", "line", "pie", "donut")
AGGREGATIONS = ("sum", "average", "min", "max", "count")
LANGS = ("en", "de", "fr", "es", "it", "pt", "nl", "pl", "ja", "zh", "ko", "ru",
         "hi", "ar", "tr", "cs", "sv", "da", "fi", "nb")

_TECH_FIELD = re.compile(r"(^__|etag$|^sap__|uuid$|^last_?change(d)?_?(by|at|date|time)|"
                         r"changed_?(by|at|on)$|created_?by$|^lastchangedatetime$)", re.I)


def family(floorplan: str) -> str:
    return FLOORPLANS.get(floorplan, ("fe",))[0]


# ─────────────────────────────────────────────────────────────────────────────
# Model helpers
# ─────────────────────────────────────────────────────────────────────────────
def _sets(model: Dict) -> Dict[str, Dict]:
    return {es["name"]: es for es in model["entity_sets"]}


def _props(es: Dict) -> Dict[str, Dict]:
    return {p["name"]: p for p in es["properties"]}


def _is_auxiliary(es: Dict) -> bool:
    """Text/value-help/technical sets — poor choices for a main entity."""
    n = es["name"]
    return bool(re.search(r"(_?Text$|Texts$|VH$|ValueHelp|_VH_|^I_|^SAP__|Set_?Text|"
                          r"_by_|Summar|_Extended$|^Alphabetical|^Current_|Demographic)", n))


def _to_many_navs(model: Dict, es: Dict) -> List[Tuple[Dict, Dict]]:
    by_type = {s["entity_type"]: s for s in model["entity_sets"]}
    out = []
    for n in es.get("navigations", []):
        tgt = by_type.get(n.get("target_type", ""))
        if n.get("to_many") and tgt and tgt["name"] != es["name"]:
            out.append((n, tgt))
    return out


def _display_fields(es: Dict, limit: int) -> List[str]:
    """Useful, human-facing fields in a sensible order."""
    keys = es["keys"]
    props = [p for p in es["properties"]
             if p.get("visible", True) and not _TECH_FIELD.search(p["name"])
             and p["ui_type"] != "boolean" or p["name"] in keys]
    ranked = sorted(props, key=lambda p: (
        p["name"] not in keys,
        not re.search(r"(name|desc|text|title|status|type|amount|date|customer|"
                      r"supplier|party|quantity|price|value)", p["name"], re.I),
        es["properties"].index(p)))
    return [p["name"] for p in ranked[:limit]]


def _title_field(es: Dict) -> str:
    keys = es["keys"]
    for p in es["properties"]:
        if p["name"] not in keys and p["ui_type"] == "text" and re.search(
                r"(name|description|title|text)$", p["name"], re.I) and not re.search(
                r"(system|user|createdby|changedby|file|mime|url|contact|persn|person|"
                r"first_?name|last_?name)", p["name"], re.I) and len(p["name"]) <= 30:
            return p["name"]
    # SAP business documents (sales order, PO, ...) are identified by their key
    return keys[0] if keys else es["properties"][0]["name"]


def _status_field(es: Dict) -> str:
    for p in es["properties"]:
        if p["ui_type"] == "text" and re.search(r"status", p["name"], re.I) and \
                (p.get("max_length") or 99) <= 20:
            return p["name"]
    return ""


def _ops_for(model: Dict, es: Dict) -> List[Dict]:
    """Operations that apply to rows of `es`: bound to its type, V2 action-for,
    or (V2 heuristic) whose parameters are exactly the set's keys."""
    keys = set(es["keys"])
    out = []
    for op in model.get("operations", []):
        bt = op.get("bound_type", "")
        if bt and bt == es["entity_type"]:
            out.append(op)
        elif not bt and op.get("parameters") and \
                {p["name"] for p in op["parameters"]} == keys:
            out.append(op)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# AI context + prompt
# ─────────────────────────────────────────────────────────────────────────────
def context_for_ai(model: Dict, max_props: int = 60) -> str:
    lines = [f"OData V{model['odata_version'][0]} service, namespace {model['namespace']}."]
    if _ms.is_multi(model):
        lines = [_ms.catalog_header(model)]
    sets = sorted(model["entity_sets"], key=_is_auxiliary)
    by_type = {s["entity_type"]: s["name"] for s in model["entity_sets"]}
    for es in sets:
        crud = "".join(c if es.get(f) else "-" for c, f in
                       (("C", "creatable"), ("U", "updatable"), ("D", "deletable")))
        flags = []
        if es.get("aggregatable"):
            flags.append("AGGREGATABLE")
        if _is_auxiliary(es):
            flags.append("auxiliary")
        lines.append(f"\nENTITY SET {es['name']}{_ms.tag(model, es)} (label '{es.get('label', '')}', keys "
                     f"{es['keys']}, CRUD {crud}{', ' + ', '.join(flags) if flags else ''})")
        for p in es["properties"][:max_props]:
            f = [p["ui_type"]]
            if p["name"] in es["keys"]:
                f.append("key")
            if not p.get("nullable", True):
                f.append("required")
            if not p.get("updatable", True):
                f.append("readonly")
            if p.get("max_length"):
                f.append(f"len{p['max_length']}")
            if p.get("unit_property"):
                f.append(f"unit={p['unit_property']}")
            if p.get("text_property"):
                f.append(f"text={p['text_property']}")
            if (es.get("annotations") or {}).get("value_lists", {}).get(p["name"]) is not None:
                f.append("has-value-list")
            lines.append(f"  - {p['name']} '{p.get('label', '')}' [{', '.join(f)}]")
        if len(es["properties"]) > max_props:
            lines.append(f"  (+{len(es['properties']) - max_props} more properties)")
        for n in es.get("navigations", []):
            lines.append(f"  nav {n['name']} -> {by_type.get(n.get('target_type'), n.get('target_type'))}"
                         f"{' (to-many)' if n.get('to_many') else ''}")
        for op in _ops_for(model, es):
            lines.append(f"  operation {op['name']} ({op['kind']}, params "
                         f"{[p['name'] for p in op['parameters']]})")
    unbound = [op for op in model.get("operations", [])
               if not any(op in _ops_for(model, es) for es in model["entity_sets"])]
    for op in unbound:
        lines.append(f"\nUNBOUND operation {op['name']} ({op['kind']}, params "
                     f"{[p['name'] for p in op['parameters']]})")
    return "\n".join(lines)


def system_prompt() -> str:
    return (
        "You are a senior SAP Fiori solution architect. From a business requirement and "
        "the REAL OData service metadata, design the application as a JSON blueprint. "
        "Use ONLY entity sets, properties, navigations and operations that appear in the "
        "metadata — never invent names. Reply with STRICT JSON only (no prose, no fences).\n\n"
        "Floorplans: " + "; ".join(f"{k} = {v[1]}" for k, v in FLOORPLANS.items()) + ".\n"
        "Choosing: browse/maintain records => ListReportObjectPage (default for standard "
        "Fiori); processing a queue/approvals => Worklist (or FreestyleWorklist when custom "
        "UI or KPI header is asked); charts/analysis over an AGGREGATABLE set => "
        "AnalyticalListPage; dashboard/cards/overview => OverviewPage; when the requirement "
        "says custom/freestyle/bespoke UI => a Freestyle* floorplan (master-detail/split => "
        "FreestyleMasterDetail).\n\n"
        "JSON schema:\n"
        "{\n"
        ' "app": {"title": str, "description": str, "languages": ["en", ...ISO codes asked for]},\n'
        ' "floorplan": str,\n'
        ' "main": {"entity_set": str, "title_field": str, "description_field": str,\n'
        '   "columns": [{"field": str, "importance": "High|Medium|Low"}],   // 5-9 columns\n'
        '   "filters": [{"field": str}],                                   // 3-6 fields\n'
        '   "search": bool, "sort": {"field": str, "descending": bool},\n'
        '   "status": {"field": str, "states": {"<raw value>": "Success|Warning|Error|Information|None"}}},\n'
        ' "object_page": {"header_fields": [str],                          // 2-4 key figures\n'
        '   "sections": [{"title": str, "fields": [str]}],                 // 2-4 sections\n'
        '   "tables": [{"navigation": str, "title": str, "columns": [str]}]},\n'
        ' "capabilities": {"create": bool, "update": bool, "delete": bool},\n'
        ' "form": {"create_fields": [str], "required": [str], "readonly": [str],\n'
        '          "mode": "complete|essential",   // complete (default) = all enterable section fields\n'
        '          "create_children": [{"navigation": str, "fields": [str], "required": [str]}]},\n'
        '          // child records entered with the document and created together (deep insert),\n'
        '          // e.g. sales/purchase order items: material, quantity, unit, plant\n'
        ' "value_helps": [{"field": str, "entity_set": str, "key": str, "text": str, "dropdown": bool}],'
        '   // dropdown = short fixed code list\n'
        ' "fe": {  // Fiori Elements floorplans only (ListReportObjectPage/Worklist/AnalyticalListPage)\n'
        '   "table_views": [{"title": str, "filters": [{"field": str, "values": [str]}]}],  // tabs above the table\n'
        '   "layout": "fcl|full",   // fcl = list and object side by side (default)\n'
        '   "header_microcharts": [{"field": <numeric>, "target_field": <numeric>, "target_value": num,'
        ' "type": "bullet|radial|progress", "title": str}],\n'
        '   "side_effects": [{"source": [field], "targets": [field or to-many navigation]}],  // refresh after edit\n'
        '   "field_rules": [{"field": str, "readonly_when"|"hidden_when"|"mandatory_when": '
        '{"field": str, "values": [str]}}]},\n'
        ' "actions": [{"label": str, "kind": "operation", "operation": str, "scope": "row|object", "confirm": bool,\n'
        '              "enabled_when": {"field": str, "values": [str]}}\n'
        '            | {"label": str, "kind": "set_field", "field": str, "value": str, "scope": "row|object", "confirm": bool,\n'
        '              "enabled_when": {"field": str, "values": [str]}}\n'
        '            | {"label": str, "kind": "script", "script": str, "scope": "row|object", "confirm": bool,\n'
        '              "enabled_when": {"field": str, "values": [str]}}],   // see CUSTOM LOGIC\n'
        ' "logic": {...},   // single-entity apps: see CUSTOM LOGIC (multi-page apps: per list page)\n'
        ' "analytics": {"measures": [{"field": str, "aggregation": "sum|average|min|max|count"}],\n'
        '               "dimensions": [str], "chart": "column|bar|line|pie|donut",\n'
        '               "kpis": [{"field": str, "label": str, "aggregation": str}]},\n'
        ' "cards": [{"type": "list|table|analytical", "title": str, "entity_set": str,\n'
        '            "fields": [str], "measure": str, "dimension": str}],      // OverviewPage only, 3-6\n'
        + _pages.schema_prompt() +
        ' "reasoning": str\n'
        "}\n\n"
        "Rules:\n"
        "- Prefer the entity set the requirement is about; avoid auxiliary text/value-help sets as main.\n"
        "- status.field: a short code/status property; map its REAL codes (e.g. SAP 'A'/'B'/'C') "
        "to states using your SAP domain knowledge.\n"
        "- sections group fields meaningfully (e.g. General, Pricing, Dates, Organization, Admin).\n"
        "- tables: to-many navigations of the main entity worth showing (e.g. items, partners).\n"
        "- value_helps only to entity sets that exist in THIS service and hold the code values.\n"
        "- actions: map requirement verbs (approve, reject, release, cancel, block...) to listed "
        "operations; if no operation fits but an updatable status field does, use set_field.\n"
        "- capabilities must respect CRUD flags; required = fields the user must fill on create.\n"
        "- enabled_when: the record state in which an action is valid (REAL codes of a status "
        "field). ALWAYS set it for state-dependent operations — e.g. SAP approval operations "
        "(release/reject approval request) only for documents whose approval status is "
        "'in approval'; calling them otherwise makes the SAP backend dump (ASSERTION_FAILED).\n"
        "- create_fields: what a user types when creating (exclude readonly/server-computed fields); "
        "required = what the backend needs to accept the document (for SAP sales orders: order type, "
        "sales organization, distribution channel, division, sold-to party). For documents with line "
        "items (sales/purchase orders, deliveries, invoices) ALWAYS add create_children for the item "
        "navigation with the item fields a user enters (e.g. Material, RequestedQuantity + unit, Plant) "
        "so a complete document can be created; add partners/schedule lines only when asked.\n"
        "- Use the metadata's own labels as guidance for titles; keep the app title short.\n"
        + _pages.prompt_rules()
        + _logic.prompt_doc()
    )


def _extract_json(raw: str) -> Dict:
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b < a:
        raise ValueError("no JSON object in AI answer")
    return json.loads(s[a:b + 1])


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic fallback (no AI / AI failed)
# ─────────────────────────────────────────────────────────────────────────────
def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z]{3,}", text.lower())}


def _pick_main(requirement: str, model: Dict) -> Dict:
    req = _words(requirement)
    nav_targets = {n.get("target_type") for es in model["entity_sets"] if not _is_auxiliary(es)
                   for n in es.get("navigations", []) if n.get("to_many")}
    items_app = re.search(r"\b(item|line)s?\s+(list|app|worklist|report)\b", requirement, re.I)
    best, best_score = None, -1e9
    for es in model["entity_sets"]:
        name = re.sub(r"([a-z])([A-Z])", r"\1 \2", es["name"]).replace("_", " ")
        words = _words(name + " " + es.get("label", ""))
        # singular/plural tolerant overlap
        overlap = sum(1 for w in words for r in req if w.startswith(r[:5]) or r.startswith(w[:5]))
        # a sub-entity (items, partners, ...) that a header navigates to loses
        # to its header unless the requirement is explicitly about items
        sub = es["entity_type"] in nav_targets and not items_app and bool(re.search(
            r"(Item|Itm|Line|Schedule|Partner|Note|Assignment|Element|Detail|Text|ProcFlow|Flow)", es["name"]))
        score = overlap * 10 + len(_to_many_navs(model, es)) * 2 - (40 if _is_auxiliary(es) else 0) \
            - (35 if sub else 0) + min(len(es["properties"]), 30) / 10
        if score > best_score:
            best, best_score = es, score
    return best


def _floorplan_from_text(requirement: str, es: Dict) -> str:
    r = requirement.lower()
    custom = re.search(r"freestyle|custom|bespoke", r)
    multi = re.search(r"multi.?page|several pages|multiple pages|pages for|side ?nav|navigation menu|"
                      r"\bwizard\b|step.by.step|\bmodules?\b|\bscreens\b|kanban|\bboard\b|pipeline|"
                      r"calendar|schedule|timeline", r)
    if multi or (custom and re.search(r"dashboard|home page|overview", r)):
        return "FreestyleApp"
    if re.search(r"overview|dashboard|cards?\b", r):
        return "OverviewPage"
    if re.search(r"master.?detail|split|flexible column", r):
        return "FreestyleMasterDetail"
    if re.search(r"analytic|chart|kpi|trend|graph", r) and es.get("aggregatable"):
        return "AnalyticalListPage"
    if re.search(r"worklist|approv|process|queue|task", r):
        return "FreestyleWorklist" if custom else "Worklist"
    return "FreestyleListDetail" if custom else "ListReportObjectPage"


def _languages_from_text(requirement: str) -> List[str]:
    names = {"english": "en", "german": "de", "french": "fr", "spanish": "es",
             "italian": "it", "portuguese": "pt", "dutch": "nl", "polish": "pl",
             "japanese": "ja", "chinese": "zh", "korean": "ko", "russian": "ru",
             "hindi": "hi", "arabic": "ar", "turkish": "tr"}
    found = ["en"]
    r = requirement.lower()
    for n, code in names.items():
        if n in r and code not in found:
            found.append(code)
    for code in re.findall(r"\b(EN|DE|FR|ES|IT|PT|NL|PL|JA|ZH)\b", requirement):
        if code.lower() not in found:
            found.append(code.lower())
    return found


def fallback(requirement: str, model: Dict) -> Dict:
    es = _pick_main(requirement, model)
    props = _props(es)
    cols = _display_fields(es, 7)
    title = _title_field(es)
    status = _status_field(es)
    filters = [f for f in cols if props[f]["ui_type"] in ("text", "date")
               and props[f].get("filterable", True)][:4]
    fields = _display_fields(es, 40)
    admin = [f for f in fields if re.search(r"(created|changed|date|time|by)$", f, re.I)]
    general = [f for f in fields if f not in admin]
    sections = [{"title": "General Information", "fields": general[:12]}]
    if general[12:]:
        sections.append({"title": "Details", "fields": general[12:24]})
    if admin:
        sections.append({"title": "Administrative Data", "fields": admin[:8]})
    tables = [{"navigation": n["name"], "title": tgt.get("label") or n["name"],
               "columns": _display_fields(tgt, 6)}
              for n, tgt in _to_many_navs(model, es)[:2]]
    measures = [p["name"] for p in es["properties"] if p["ui_type"] in ("amount", "number")
                and p["name"] not in es["keys"]][:2]
    dims = [f for f in cols if props[f]["ui_type"] == "text" and f not in es["keys"]][:2]
    floorplan = "FreestyleApp" if _ms.is_multi(model) else _floorplan_from_text(requirement, es)
    actions = [{"label": op.get("label") or op["name"], "kind": "operation",
                "operation": op["name"], "scope": "object", "confirm": True}
               for op in _ops_for(model, es) if op["kind"] == "action"][:3]
    create = [p["name"] for p in es["properties"] if p.get("creatable", True)
              and not _TECH_FIELD.search(p["name"]) and p["name"] in fields][:10]
    bp = {
        "app": {"title": "Business Cockpit" if _ms.is_multi(model) else (es.get("label") or es["name"]),
                "description": requirement[:120],
                "languages": _languages_from_text(requirement)},
        "floorplan": floorplan,
        "main": {"entity_set": es["name"], "title_field": title,
                 "description_field": next((f for f in cols if f not in (title, *es["keys"])
                                            and props[f]["ui_type"] == "text"), ""),
                 "columns": [{"field": f, "importance": "High" if i < 3 else "Medium"}
                             for i, f in enumerate(cols)],
                 "filters": [{"field": f} for f in filters], "search": True,
                 "sort": {"field": es["keys"][0] if es["keys"] else cols[0], "descending": True},
                 "status": {"field": status, "states": {}}},
        "object_page": {"header_fields": ([status] if status else []) + measures[:2],
                        "sections": sections, "tables": tables},
        "capabilities": {"create": es.get("creatable", True), "update": es.get("updatable", True),
                         "delete": es.get("deletable", True)},
        "form": {"create_fields": create,
                 "required": [p["name"] for p in es["properties"]
                              if not p.get("nullable", True) and p["name"] in create],
                 "readonly": []},
        "value_helps": [],
        "actions": actions,
        "analytics": {"measures": [{"field": m, "aggregation": "sum"} for m in measures],
                      "dimensions": dims, "chart": "column",
                      "kpis": [{"field": m, "label": props[m].get("label") or m,
                                "aggregation": "sum"} for m in measures[:2]]},
        "cards": [],
        "reasoning": "Rule-based design (AI unavailable).",
    }
    if floorplan == "OverviewPage":
        bp["cards"] = _default_cards(model, es, bp)
    if floorplan == "FreestyleApp":
        bp["pages"] = _pages.fallback_pages(requirement, model)
    return bp


def _default_cards(model: Dict, es: Dict, bp: Dict) -> List[Dict]:
    cols = [c["field"] for c in bp["main"]["columns"]]
    cards = [{"type": "list", "title": f"Recent {es.get('label') or es['name']}",
              "entity_set": es["name"], "fields": cols[:3]},
             {"type": "table", "title": es.get("label") or es["name"],
              "entity_set": es["name"], "fields": cols[:4]}]
    m = bp["analytics"]["measures"]
    if m and bp["analytics"]["dimensions"]:
        cards.append({"type": "analytical", "title": f"{m[0]['field']} by "
                      f"{bp['analytics']['dimensions'][0]}", "entity_set": es["name"],
                      "fields": [], "measure": m[0]["field"],
                      "dimension": bp["analytics"]["dimensions"][0]})
    for n, tgt in _to_many_navs(model, es)[:1]:
        cards.append({"type": "table", "title": tgt.get("label") or tgt["name"],
                      "entity_set": tgt["name"], "fields": _display_fields(tgt, 4)})
    return cards


# ─────────────────────────────────────────────────────────────────────────────
# Validation / repair — everything must exist in the metadata
# ─────────────────────────────────────────────────────────────────────────────
# Known state rules for standard SAP operations (applied when the design gives
# none). Calling these on documents in the wrong state makes the SAP backend
# terminate the request with a short dump (ASSERTION_FAILED) instead of an error.
_KNOWN_STATE_RULES = [
    # (operation name pattern, status field, values in which the operation is valid)
    (r"^(release|reject)ApprovalRequest$", "SalesDocApprovalStatus", ["A"]),
    (r"^(release|reject)ApprovalRequest$", "PurchasingDocumentApprovalStatus", ["A"]),
    (r"^(release|reject)ApprovalRequest$", "ApprovalStatus", ["A"]),
]


def _enabled_when(a: Dict, op: Optional[Dict], props: Dict, note, label: str) -> Dict:
    ew = a.get("enabled_when") or {}
    # Known SAP rules win over the AI's guess: a wrong guard either disables the
    # action forever (e.g. TotalBlockStatus='B', which no order has) or lets it
    # run in a state where the backend dumps.
    if op:
        for pat, field, vals in _KNOWN_STATE_RULES:
            if re.search(pat, op["name"], re.I) and field in props:
                if ew and (ew.get("field") != field or sorted(map(str, ew.get("values") or [])) != sorted(vals)):
                    note(f"Action '{label}': availability '{ew.get('field')}' replaced by the SAP rule "
                         f"{field} = {'/'.join(vals)}.")
                return {"field": field, "values": list(vals)}
    if isinstance(ew, dict) and ew.get("field") in props:
        vals = [str(v) for v in (ew.get("values") or []) if v is not None][:12]
        if vals:
            return {"field": ew["field"], "values": vals}
    if isinstance(ew, dict) and ew.get("field") and ew.get("field") not in props:
        note(f"Action '{label}': availability field '{ew.get('field')}' does not exist — ignored.")
    if op:
        for pat, field, vals in _KNOWN_STATE_RULES:
            if re.search(pat, op["name"], re.I) and field in props:
                note(f"Action '{label}' is only offered when {field} is {'/'.join(vals)} "
                     f"(SAP rejects it — with a short dump — in any other state).")
                return {"field": field, "values": list(vals)}
    return {}


def _norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9]", "", s.lower())
    s = re.sub(r"^a", "", s) if s.startswith("a") and len(s) > 4 else s
    return re.sub(r"(type|set|s)$", "", s)


def _generated_key(es: Dict, field: str) -> bool:
    """Key that is the document's own number (SalesOrder in A_SalesOrder, OrderID in
    Orders, ProductID in Products) — assigned by the backend, not typed on create."""
    if field not in es["keys"]:
        return False
    p = _props(es).get(field, {})
    if p.get("type") in ("Edm.Int32", "Edm.Int64", "Edm.Int16", "Edm.Guid"):
        return True                                   # identity / UUID keys
    if not re.match(r"^[ACI]_", es["name"]):
        return False                                  # e.g. Northwind CustomerID: typed by the user
    base = _norm(re.sub(r"(ID|Id|Number|No|UUID)$", "", field))
    return bool(base) and (base in _norm(es["name"]) or base in _norm(es.get("entity_type", "")))


def _entry_unit(props: Dict, field: str) -> str:
    """The unit field a user enters for a quantity/amount: SAP APIs annotate the
    internal SAP code (…SAPUnit) as the unit, but accept the plain …Unit field
    (e.g. RequestedQuantityUnit 'CAR'; RequestedQuantitySAPUnit 'CAR' is rejected)."""
    for cand in (field + "Unit", field + "Currency"):
        if cand in props and props[cand].get("creatable", True):
            return cand
    u = props.get(field, {}).get("unit_property") or ""
    plain = re.sub(r"(SAP|ISO)Unit$", "Unit", u)
    if plain in props and props[plain].get("creatable", True):
        return plain
    return u if u in props and props[u].get("creatable", True) else ""


_ITEM_ESSENTIALS = [r"^material$|^product(id)?$", r"quantity$", r"plant$", r"^netpriceamount$|^unitprice$|^price$",
                    r"text$|description$", r"date$", r"storagelocation"]


def _child_entry_fields(tgt: Dict, exclude: set) -> List[str]:
    """Default item fields a user enters: material/product, quantity (+unit), plant, price…"""
    props = _props(tgt)
    ok = [p["name"] for p in tgt["properties"] if p["name"] not in exclude and p.get("creatable", True)
          and not _TECH_FIELD.search(p["name"]) and not re.search(r"(Name|Status|Category)$", p["name"])]
    out = []
    for pat in _ITEM_ESSENTIALS:
        for f in ok:
            if f not in out and re.search(pat, f, re.I) and len(out) < 7:
                out.append(f)
                u = _entry_unit(props, f)
                if u and u not in out:
                    out.append(u)
                break
    return out or ok[:6]


def _cond(c, props) -> Dict:
    if isinstance(c, dict) and c.get("field") in props and c.get("values"):
        return {"field": c["field"], "values": [str(v) for v in c["values"]][:10]}
    return {}


def _fe_options(raw: Optional[Dict], es: Dict, model: Dict, main: Dict, object_page: Dict, fp: str,
                note) -> Dict:
    """Fiori Elements extras: table views (tabs), layout, header micro charts, side
    effects, state-dependent field rules, code + text display. Validated against the
    metadata; sensible defaults when the design gives none."""
    raw = raw if isinstance(raw, dict) else {}
    props = _props(es)
    numeric = {p["name"] for p in es["properties"] if p["ui_type"] in ("amount", "number")}
    # table views
    views = []
    for i, v in enumerate(raw.get("table_views") or []):
        if not isinstance(v, dict):
            continue
        flt = [c for c in (_cond(f, props) for f in v.get("filters") or []) if c]
        views.append({"id": f"view{i + 1}", "title": str(v.get("title") or f"View {i + 1}")[:30], "filters": flt})
    if not views and raw.get("status_tabs", True) and main["status"]["field"] and main["status"]["states"] \
            and fp in ("ListReportObjectPage", "Worklist"):
        views = [{"id": f"view{i + 1}", "title": (k or "(blank)")[:30],
                  "filters": [{"field": main["status"]["field"], "values": [k]}]}
                 for i, k in enumerate(list(main["status"]["states"])[:6])]
    if views and not any(not v["filters"] for v in views):
        views.insert(0, {"id": "viewAll", "title": "All", "filters": []})
    # header micro charts
    charts = []
    for i, c in enumerate(raw.get("header_microcharts") or []):
        if not isinstance(c, dict) or c.get("field") not in numeric:
            if isinstance(c, dict) and c.get("field"):
                note(f"Header chart for '{c.get('field')}' dropped — needs a numeric field.")
            continue
        tgt_f = c.get("target_field") if c.get("target_field") in numeric and c.get("target_field") != c["field"] else ""
        try:
            tgt_v = float(c.get("target_value")) if c.get("target_value") not in (None, "") else None
        except (TypeError, ValueError):
            tgt_v = None
        if not tgt_f and tgt_v is None:
            note(f"Header chart for '{c['field']}' dropped — needs a target field or value.")
            continue
        charts.append({"id": f"mc{i + 1}", "field": c["field"], "target_field": tgt_f, "target_value": tgt_v,
                       "type": c.get("type") if c.get("type") in ("bullet", "radial", "progress") else "bullet",
                       "title": str(c.get("title") or props[c["field"]].get("label") or c["field"])[:40]})
    # code + description (text arrangement): automatic for fields with a sibling text field
    shown = {c["field"] for c in main["columns"]} | {f for s in object_page["sections"] for f in s["fields"]}
    texts = {}
    for f in sorted(shown):
        p = props.get(f, {})
        if p.get("text_property") or p.get("ui_type") != "text":
            continue
        for suf in ("Name", "Text", "Description", "Desc"):
            if f + suf in props:
                texts[f] = f + suf
                break
    # side effects
    navs = {n["name"] for n in es.get("navigations", [])}
    side = []
    for se in raw.get("side_effects") or []:
        if not isinstance(se, dict):
            continue
        src = [f for f in se.get("source") or [] if f in props]
        tp = [f for f in se.get("targets") or [] if f in props]
        te = [f for f in se.get("targets") or [] if f in navs]
        if src and (tp or te):
            side.append({"source": src, "target_properties": tp, "target_entities": te})
    for f, t in texts.items():                        # code changed -> its description refreshes
        side.append({"source": [f], "target_properties": [t], "target_entities": []})
    # field rules
    rules = []
    for r in raw.get("field_rules") or []:
        if not isinstance(r, dict) or r.get("field") not in props:
            continue
        out = {"field": r["field"]}
        for k in ("readonly_when", "hidden_when", "mandatory_when"):
            c = _cond(r.get(k), props)
            if c:
                out[k] = c
        if len(out) > 1:
            rules.append(out)
    layout = raw.get("layout") if raw.get("layout") in ("fcl", "full") else "fcl"
    return {"table_views": views[:8], "layout": layout, "header_microcharts": charts[:4],
            "text_arrangement": texts, "side_effects": side[:20], "field_rules": rules[:20],
            "personalization": raw.get("personalization", True) is not False}


def validate(bp: Dict, model: Dict, requirement: str = "") -> Tuple[Dict, List[str]]:
    """Return (repaired blueprint, notes). Never raises for content problems."""
    notes: List[str] = []
    sets = _sets(model)
    base = fallback(requirement or "", model)

    def note(msg):
        if msg not in notes:
            notes.append(msg)

    # ── main entity
    main = dict(bp.get("main") or {})
    es = sets.get(main.get("entity_set"))
    if not es:
        if main.get("entity_set"):
            note(f"Unknown entity set '{main.get('entity_set')}' replaced by "
                 f"'{base['main']['entity_set']}'.")
        es = sets[base["main"]["entity_set"]]
        main = dict(base["main"])
    props = _props(es)

    def keep(fields, where, pool=props, allow_empty=True):
        out = []
        for f in fields or []:
            f = f.get("field") if isinstance(f, dict) else f
            if f in pool and f not in out:
                out.append(f)
            elif f:
                note(f"Dropped unknown field '{f}' from {where}.")
        return out

    # ── floorplan
    fp = bp.get("floorplan")
    if fp not in FLOORPLANS:
        if fp:
            note(f"Unknown floorplan '{fp}' — using {base['floorplan']}.")
        fp = base["floorplan"]
    if _ms.is_multi(model) and fp != "FreestyleApp":
        note(f"This app combines {len(_ms.services(model))} services — built as a multi-page app "
             f"(FreestyleApp) with one area per service instead of {fp}.")
        fp = "FreestyleApp"
    if fp == "AnalyticalListPage" and not es.get("aggregatable"):
        note(f"{es['name']} does not support aggregation ($apply / analytical "
             f"semantics), which the Analytical List Page needs — using "
             f"ListReportObjectPage with a chart-free table instead.")
        fp = "ListReportObjectPage"

    # ── columns / filters / title / sort / status
    col_specs = []
    for c in main.get("columns") or []:
        f = c.get("field") if isinstance(c, dict) else c
        if f in props and f not in [x["field"] for x in col_specs]:
            imp = (c.get("importance") if isinstance(c, dict) else None) or "Medium"
            col_specs.append({"field": f, "importance": imp if imp in ("High", "Medium", "Low") else "Medium"})
        elif f:
            note(f"Dropped unknown column '{f}'.")
    if not col_specs:
        col_specs = [{"field": f, "importance": "High" if i < 3 else "Medium"}
                     for i, f in enumerate(_display_fields(es, 7))]
    filters = [{"field": f} for f in keep(main.get("filters"), "filters")
               if props[f].get("filterable", True)]
    title = main.get("title_field") if main.get("title_field") in props else _title_field(es)
    desc = main.get("description_field") if main.get("description_field") in props else ""
    sort = main.get("sort") or {}
    if sort.get("field") not in props:
        sort = {"field": es["keys"][0] if es["keys"] else col_specs[0]["field"], "descending": True}
    sort = {"field": sort["field"], "descending": bool(sort.get("descending", True))}
    status = main.get("status") or {}
    sfield = status.get("field") if status.get("field") in props else ""
    if status.get("field") and not sfield:
        note(f"Status field '{status.get('field')}' does not exist — status colouring off.")
    states = {str(k): (v if v in STATES else "None")
              for k, v in (status.get("states") or {}).items()} if sfield else {}

    main = {"entity_set": es["name"], "key": list(es["keys"]), "title_field": title,
            "description_field": desc, "columns": col_specs, "filters": filters,
            "search": bool(main.get("search", True)), "sort": sort,
            "status": {"field": sfield, "states": states}}

    # ── object page
    op = bp.get("object_page") or {}
    sections = []
    for s in op.get("sections") or []:
        flds = keep(s.get("fields"), f"section '{s.get('title', '')}'")
        if flds:
            sections.append({"title": str(s.get("title") or "Details"), "fields": flds})
    if not sections:
        sections = base["object_page"]["sections"] if base["main"]["entity_set"] == es["name"] \
            else [{"title": "General Information", "fields": _display_fields(es, 12)}]
    header = keep(op.get("header_fields"), "object-page header")[:4]
    navs = {n["name"]: (n, t) for n, t in _to_many_navs(model, es)}
    tables = []
    for t in op.get("tables") or []:
        nav = t.get("navigation")
        if nav not in navs:
            # AI may give the target set name instead of the navigation
            nav = next((k for k, (_, tgt) in navs.items()
                        if tgt["name"] in (t.get("navigation"), t.get("entity_set"))), None)
        if not nav:
            note(f"Dropped table '{t.get('title') or t.get('navigation')}' — no such to-many navigation.")
            continue
        tgt = navs[nav][1]
        cols = keep(t.get("columns"), f"table '{nav}'", _props(tgt)) or _display_fields(tgt, 6)
        tables.append({"navigation": nav, "entity_set": tgt["name"],
                       "key": list(tgt["keys"]), "title": str(t.get("title") or tgt.get("label") or nav),
                       "columns": cols})
    object_page = {"header_fields": header, "sections": sections, "tables": tables}

    # ── capabilities (respect service CRUD flags)
    cap_in = bp.get("capabilities") or {}
    caps = {}
    for k, flag in (("create", "creatable"), ("update", "updatable"), ("delete", "deletable")):
        want = bool(cap_in.get(k, es.get(flag, True)))
        if want and not es.get(flag, True):
            note(f"{k.capitalize()} disabled — {es['name']} is not {flag} in the service.")
        caps[k] = want and es.get(flag, True)

    # ── form
    form = bp.get("form") or {}
    create_fields = [f for f in keep(form.get("create_fields"), "create form")
                     if props[f].get("creatable", True)]
    if caps["create"] and not create_fields:
        create_fields = [f for f in base["form"]["create_fields"] if f in props] or \
                        [p["name"] for p in es["properties"] if p.get("creatable", True)][:8]
    required = sorted(set(keep(form.get("required"), "required fields")) |
                      {p["name"] for p in es["properties"]
                       if not p.get("nullable", True) and p["name"] in create_fields
                       and p["name"] not in es["keys"]})
    readonly = sorted(set(keep(form.get("readonly"), "readonly fields")) |
                      {p["name"] for p in es["properties"] if not p.get("updatable", True)})

    # Complete create form (default): every field a user can enter that the detail
    # sections show, grouped like the sections, so a full document can be created
    # from the app — not only the handful of fields the design listed.
    mode = form.get("mode") if form.get("mode") in ("essential", "complete") else "complete"
    groups = []
    if caps["create"]:
        def enterable(f):
            p = props[f]
            return (p.get("creatable", True) and not _TECH_FIELD.search(f) and f not in es["keys"]
                    and p.get("ui_type") != "binary")
        listed = [f for f in create_fields if not _generated_key(es, f)]
        seen = set()
        if mode == "complete":
            for s in object_page["sections"]:
                flds = [f for f in s["fields"] if enterable(f) and f not in seen]
                seen.update(flds)
                if flds:
                    groups.append({"title": s["title"], "fields": flds})
        extra = [f for f in listed if f not in seen]
        extra += [f for f in required if f not in seen and f not in extra and not _generated_key(es, f)]
        if extra:
            groups.insert(0, {"title": "General", "fields": extra})
        create_fields = [f for g in groups for f in g["fields"]][:60]
        allowed = set(create_fields)
        groups = [dict(g, fields=[f for f in g["fields"] if f in allowed]) for g in groups]
        groups = [g for g in groups if g["fields"]]

    # Child records created together with the document (deep insert), e.g. items.
    children = []
    if caps["create"]:
        navs_all = {n["name"]: (n, t) for n, t in _to_many_navs(model, es)}
        raw_children = form.get("create_children")
        if raw_children is None:      # default: the item-like child collection, if creatable
            raw_children = [{"navigation": name} for name, (n, t) in navs_all.items()
                            if t.get("creatable", True) and re.search(r"item", name + t["name"], re.I)][:1]
        for c in raw_children or []:
            nav = c.get("navigation") if isinstance(c, dict) else c
            if nav not in navs_all:
                nav = next((k for k, (_, t) in navs_all.items() if t["name"] in (nav, (c or {}).get("entity_set")
                                                                                   if isinstance(c, dict) else nav)), None)
            if not nav:
                note(f"Create: child table '{(c or {}).get('navigation') if isinstance(c, dict) else c}' dropped "
                     f"— no such to-many navigation.")
                continue
            tgt = navs_all[nav][1]
            if not tgt.get("creatable", True):
                note(f"Create: {tgt['name']} cannot be created in the service — not part of the create form.")
                continue
            tprops = _props(tgt)
            parent_keys = set(es["keys"])
            cand = [f for f in ((c.get("fields") if isinstance(c, dict) else None) or [])
                    if f in tprops and f not in parent_keys]
            auto_keys = [k for k in tgt["keys"] if k not in parent_keys]
            if not cand:
                cand = _child_entry_fields(tgt, parent_keys | set(auto_keys))
            else:
                # SAP-internal / ISO unit variants -> the unit field users enter
                cand = list(dict.fromkeys(re.sub(r"(SAP|ISO)Unit$", "Unit", f)
                                          if re.sub(r"(SAP|ISO)Unit$", "Unit", f) in tprops else f for f in cand))
                for f in list(cand):          # a quantity/amount needs its unit/currency next to it
                    if tprops[f]["ui_type"] not in ("number", "amount"):
                        continue
                    u = _entry_unit(tprops, f)
                    if u and u not in cand:
                        cand.insert(cand.index(f) + 1, u)
            creq = sorted({f for f in ((c.get("required") if isinstance(c, dict) else None) or []) if f in cand}
                          | {f for f in cand if not tprops[f].get("nullable", True) and f not in auto_keys})
            children.append({"navigation": nav, "entity_set": tgt["name"],
                             "title": str((c.get("title") if isinstance(c, dict) else "") or tgt.get("label") or nav),
                             "fields": cand[:12], "required": creq, "auto_keys": auto_keys,
                             "parent_keys": [k for k in tgt["keys"] if k in parent_keys]})
            if len(children) >= 3:
                break
    required = sorted(set(keep(form.get("required"), "required fields")) |
                      {p["name"] for p in es["properties"]
                       if not p.get("nullable", True) and p["name"] in create_fields
                       and p["name"] not in es["keys"]})
    form = {"create_fields": create_fields, "required": [f for f in required if f in create_fields],
            "readonly": readonly, "mode": mode, "create_groups": groups, "create_children": children}

    # ── value helps (same service only)
    vhs = []
    for v in bp.get("value_helps") or []:
        tgt = sets.get(v.get("entity_set"))
        if tgt and tgt["name"] == es["name"]:
            note(f"Dropped value help for '{v.get('field')}' — it pointed at {es['name']} itself, "
                 f"which would list records instead of codes.")
            continue
        if v.get("field") not in props or not tgt:
            note(f"Dropped value help for '{v.get('field')}' — "
                 f"{'unknown field' if v.get('field') not in props else 'set ' + str(v.get('entity_set')) + ' not in service'}.")
            continue
        tprops = _props(tgt)
        key = v.get("key") if v.get("key") in tprops else (tgt["keys"][0] if tgt["keys"] else None)
        text = v.get("text") if v.get("text") in tprops else _title_field(tgt)
        if key:
            vhs.append({"field": v["field"], "entity_set": tgt["name"], "key": key,
                        "text": text if text != key else "",
                        "dropdown": bool(v.get("dropdown"))})

    # ── actions
    ops = {o["name"]: o for o in model.get("operations", [])}
    actions = []
    for i, a in enumerate(bp.get("actions") or []):
        label = str(a.get("label") or a.get("operation") or a.get("field") or f"Action {i + 1}")
        scope = a.get("scope") if a.get("scope") in ("row", "object", "global") else "object"
        if a.get("kind") == "operation" and a.get("operation") in ops:
            o = ops[a["operation"]]
            actions.append({"id": f"act{i + 1}", "label": label, "kind": "operation",
                            "operation": o["name"], "scope": scope,
                            "confirm": bool(a.get("confirm", True)),
                            "enabled_when": _enabled_when(a, o, props, note, label)})
        elif a.get("kind") == "script" and a.get("script"):
            ent_ops = {o["name"]: o for o in _ops_for(model, es)}
            sc = _logic.validate_script_action(a, es, ent_ops, note, label)
            if sc:
                if not es.get("updatable", True) and sc["sets"]:
                    note(f"Action '{label}' dropped — {es['name']} is not updatable.")
                    continue
                actions.append({"id": f"act{i + 1}", "label": label, "kind": "script",
                                "scope": scope if scope != "global" else "object",
                                "confirm": bool(a.get("confirm", True)),
                                "enabled_when": _enabled_when(a, None, props, note, label), **sc})
        elif a.get("kind") == "set_field" and a.get("field") in props:
            if not props[a["field"]].get("updatable", True) or not es.get("updatable", True):
                note(f"Action '{label}' dropped — {a['field']} is not updatable.")
                continue
            actions.append({"id": f"act{i + 1}", "label": label, "kind": "set_field",
                            "field": a["field"], "value": str(a.get("value", "")),
                            "scope": scope, "confirm": bool(a.get("confirm", True)),
                            "enabled_when": _enabled_when(a, None, props, note, label)})
        else:
            note(f"Action '{label}' dropped — operation/field not in the service.")

    # ── analytics
    an = bp.get("analytics") or {}
    numeric = {p["name"] for p in es["properties"] if p["ui_type"] in ("amount", "number")}
    measures = [{"field": m.get("field"), "aggregation": m.get("aggregation")
                 if m.get("aggregation") in AGGREGATIONS else "sum"}
                for m in an.get("measures") or [] if isinstance(m, dict) and m.get("field") in numeric]
    if not measures:
        measures = base["analytics"]["measures"] if base["main"]["entity_set"] == es["name"] else []
    dims = keep(an.get("dimensions"), "chart dimensions")
    kpis = [{"field": k.get("field"), "label": str(k.get("label") or k.get("field")),
             "aggregation": k.get("aggregation") if k.get("aggregation") in AGGREGATIONS else "sum"}
            for k in an.get("kpis") or [] if isinstance(k, dict) and k.get("field") in numeric]
    analytics = {"measures": measures, "dimensions": dims or
                 [c["field"] for c in col_specs if props[c["field"]]["ui_type"] == "text"
                  and c["field"] not in es["keys"]][:2],
                 "chart": an.get("chart") if an.get("chart") in CHARTS else "column",
                 "kpis": kpis or [{"field": m["field"], "label": props[m["field"]].get("label") or m["field"],
                                   "aggregation": m["aggregation"]} for m in measures[:2]]}

    # ── cards (Overview Page)
    cards = []
    if fp == "OverviewPage":
        for i, c in enumerate(bp.get("cards") or []):
            cset = sets.get(c.get("entity_set"))
            if not cset:
                note(f"Card '{c.get('title')}' dropped — unknown entity set.")
                continue
            cp = _props(cset)
            ctype = c.get("type") if c.get("type") in ("list", "table", "analytical") else "table"
            fields = keep(c.get("fields"), f"card '{c.get('title')}'", cp) or _display_fields(cset, 4)
            card = {"id": f"card{i + 1:02d}", "type": ctype,
                    "title": str(c.get("title") or cset.get("label") or cset["name"]),
                    "entity_set": cset["name"], "fields": fields,
                    "measure": c.get("measure") if c.get("measure") in cp else "",
                    "dimension": c.get("dimension") if c.get("dimension") in cp else ""}
            if ctype == "analytical" and not (card["measure"] and card["dimension"]):
                note(f"Card '{card['title']}' needs a measure and dimension — shown as a table.")
                card["type"] = "table"
            cards.append(card)
        if not cards:
            cards = [dict(c, id=f"card{i + 1:02d}") for i, c in
                     enumerate(_default_cards(model, es, {"main": main, "analytics": analytics}))]

    # ── multi-page apps
    pages = []
    if fp == "FreestyleApp":
        raw_pages = bp.get("pages")
        if not raw_pages:
            note("No pages were designed — derived pages from the requirement.")
            raw_pages = _pages.fallback_pages(requirement, model)
        pages = _pages.validate_pages(raw_pages, model, requirement, notes)
        lists = [p for p in pages if p["type"] == "list"]
        if not lists:
            note("Multi-page app needs at least one list page — using a single list/detail app.")
            fp, pages = "FreestyleListDetail", []
        else:
            # 'main' mirrors the first list page (used for deployment config, README)
            first = lists[0]["sub"]
            main, object_page, caps, form = first["main"], first["object_page"], first["capabilities"], first["form"]
            vhs, actions, analytics = first["value_helps"], first["actions"], first["analytics"]

    app = bp.get("app") or {}
    langs = [l.lower() for l in (app.get("languages") or []) if str(l).lower() in LANGS]
    if "en" not in langs:
        langs.insert(0, "en")
    return ({
        "app": {"title": str(app.get("title") or es.get("label") or es["name"])[:60],
                "description": str(app.get("description") or "")[:250],
                "languages": langs},
        "floorplan": fp,
        "main": main,
        "object_page": object_page,
        "capabilities": caps,
        "form": form,
        "value_helps": vhs,
        "actions": actions,
        "analytics": analytics,
        "cards": cards,
        "pages": pages,
        "logic": _logic.validate(bp.get("logic"), es, note) if fp != "FreestyleApp" else
                 _logic.validate({}, es, note),
        "reasoning": str(bp.get("reasoning") or ""),
        "style": _style.validate(bp.get("style"))[0] if bp.get("style") else _style.default_style(),
        "fe": _fe_options(bp.get("fe"), es, model, main, object_page, fp, note),
        "links": _links(bp, model, pages, note),
    }, notes)


def _links(bp: Dict, model: Dict, pages: List[Dict], note) -> List[Dict]:
    """Cross-service links of a multi-service app (designed, or found by field names)."""
    if not (_ms.is_multi(model) and pages):
        return []
    page_sets = [p["entity_set"] for p in pages if p["type"] == "list"]
    if bp.get("links") is None:
        return _ms.auto_links(model, page_sets)
    tmp: List[str] = []
    out = _ms.validate_links(bp.get("links"), model, tmp)
    for n in tmp:
        note(n)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────
def design(requirement: str, model: Dict, ai_fn: Optional[Callable[[str, str], str]],
           floorplan_override: str = "", style: Optional[Dict] = None) -> Tuple[Dict, List[str], str]:
    """(blueprint, notes, source) where source is 'ai' or 'rules'. A style
    (from style.analyze) is attached to the blueprint; its layout_hints — what
    the reference image shows structurally — are added to the requirement."""
    if style and style.get("layout_hints"):
        requirement = (requirement.rstrip() + "\n\nVISUAL REFERENCE (from the user's image): "
                       + style["layout_hints"])
    source, raw_bp = "rules", None
    if ai_fn:
        try:
            user = (f"BUSINESS REQUIREMENT:\n{requirement.strip()}\n\n"
                    + (f"The user explicitly wants floorplan {floorplan_override}.\n\n"
                       if floorplan_override else "")
                    + f"SERVICE METADATA:\n{context_for_ai(model)}{_ms.prompt_rules(model)}\n\n"
                      f"Return the blueprint JSON.")
            raw_bp = _extract_json(ai_fn(system_prompt(), user))
            source = "ai"
        except Exception as e:  # noqa: BLE001 — degrade to rules, but say why
            raw_bp = None
            fallback_reason = str(e)[:200]
    if raw_bp is None:
        raw_bp = fallback(requirement, model)
    if floorplan_override in FLOORPLANS:
        raw_bp["floorplan"] = floorplan_override
    if style:
        raw_bp["style"] = style
    bp, notes = validate(raw_bp, model, requirement)
    if source == "rules" and ai_fn:
        notes.insert(0, f"AI design failed ({fallback_reason}); used rule-based design.")
    return bp, notes, source
