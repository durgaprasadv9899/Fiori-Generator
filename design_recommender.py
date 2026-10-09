"""
design_recommender.py — AI Design Recommendation Engine.

Runs BEFORE code generation and produces an inspectable recommendation, so the
tool behaves less like a code generator and more like a solution architect. It
decides, from the enriched model (metadata + relationships + annotations +
sample-data profiles) and the requirement:

  - target:      "fiori_elements" | "freestyle"
                 FE is recommended only when the service carries enough UI
                 annotations to drive it (UI.LineItem etc.); otherwise freestyle.
  - floorplan:   ListReport | ObjectPage | WorklistPage | AnalyticalListPage |
                 FreestyleListDetail | FreestyleWorklist | FreestyleMasterDetail
  - controls:    the rich controls the UI should include (table, objectPage,
                 chart, timeline, wizard, upload, treeTable, calendar,
                 filterBar, smartTable, kpiCards, valueHelp)
  - rationale:   short human-readable why

The recommendation is produced by the AI when available, then *validated* against
what the metadata can actually support (e.g. never recommend FE if there are no
UI annotations; never recommend a child object-page table if there's no
navigation). If the AI is unavailable, a deterministic rules-based
recommendation is derived from the model alone, so this never blocks.

This module is pure Python + an injected ai_fn; no Streamlit, no SDK.
"""

from __future__ import annotations
import json
import re
from typing import Callable, Dict, List


TARGETS = ["fiori_elements", "freestyle"]

FE_FLOORPLANS = ["ListReport", "ObjectPage", "WorklistPage", "AnalyticalListPage"]
FREESTYLE_FLOORPLANS = ["FreestyleListDetail", "FreestyleWorklist", "FreestyleMasterDetail"]

RICH_CONTROLS = [
    "table", "objectPage", "chart", "timeline", "wizard", "upload",
    "treeTable", "calendar", "filterBar", "smartTable", "kpiCards", "valueHelp",
]


def _annotation_richness(model: Dict) -> Dict:
    """How much UI-annotation signal does the service carry? Drives FE viability."""
    line_items = selection = value_lists = field_groups = 0
    for es in model.get("entity_sets", []):
        ann = es.get("annotations") or {}
        line_items += 1 if ann.get("line_item") else 0
        selection += 1 if ann.get("selection_fields") else 0
        value_lists += len(ann.get("value_lists") or {})
        field_groups += len(ann.get("field_groups") or {})
    return {
        "line_items": line_items,
        "selection": selection,
        "value_lists": value_lists,
        "field_groups": field_groups,
        # FE is viable when at least one set has a LineItem (the minimum for a
        # List Report / Object Page to render columns from annotations).
        "fe_viable": line_items > 0,
    }


def _has_navigation(model: Dict) -> bool:
    return any(es.get("navigations") for es in model.get("entity_sets", []))


def _system_prompt() -> str:
    return (
        "You are an SAP Fiori lead architect making a DESIGN RECOMMENDATION "
        "before any code is written. Given a requirement and rich service facts "
        "(entity sets, navigation relationships, UI annotations, capabilities, "
        "sample-data value profiles), recommend the best UX approach. "
        "Respond with STRICT JSON only, no prose or fences. Schema:\n"
        "{\n"
        '  "target": "fiori_elements | freestyle",\n'
        '  "floorplan": "<ListReport|ObjectPage|WorklistPage|AnalyticalListPage'
        '|FreestyleListDetail|FreestyleWorklist|FreestyleMasterDetail>",\n'
        '  "controls": ["<subset of: ' + ", ".join(RICH_CONTROLS) + '>"],\n'
        '  "primary_entity_set": "<entity set name>",\n'
        '  "rationale": "<one or two short sentences>"\n'
        "}\n"
        "Rules:\n"
        "- Recommend fiori_elements ONLY if the service has UI annotations "
        "(UI.LineItem etc.); otherwise freestyle.\n"
        "- Heavy aggregation/KPIs => AnalyticalListPage (FE) or a freestyle list "
        "with chart+kpiCards. Process/approve => Worklist. Header with line-items "
        "=> ObjectPage with a child table. Simple browse+edit => ListReport (FE) "
        "or FreestyleListDetail.\n"
        "- Only include controls the facts justify: timeline needs a date field; "
        "treeTable needs a self/parent relationship; upload needs an attachment/"
        "stream; chart/kpiCards need numeric measures; wizard suits multi-step "
        "create.\n"
    )


def _context(requirement: str, model: Dict, sample_profiles: Dict = None) -> str:
    from metadata_parser import summarize_for_prompt
    from business_analyzer import _relationships_for_prompt, _annotations_for_prompt
    from sample_data import summarize_profile_for_prompt

    parts = [f"REQUIREMENT:\n{requirement.strip()}",
             "METADATA:\n" + summarize_for_prompt(model)]
    rel = _relationships_for_prompt(model)
    if rel:
        parts.append(rel)
    ann = _annotations_for_prompt(model)
    if ann:
        parts.append(ann)
    if sample_profiles:
        chunks = [summarize_profile_for_prompt(k, v) for k, v in sample_profiles.items()]
        chunks = [c for c in chunks if c]
        if chunks:
            parts.append("\n".join(chunks))
    rich = _annotation_richness(model)
    parts.append(f"ANNOTATION RICHNESS: {json.dumps(rich)}; "
                 f"has_navigation={_has_navigation(model)}")
    return "\n\n".join(parts)


def _extract_json(raw: str) -> Dict:
    s = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    a, b = s.find("{"), s.rfind("}")
    if a == -1 or b == -1:
        raise ValueError("no json")
    return json.loads(s[a:b + 1])


def _validate(rec: Dict, model: Dict, sample_profiles: Dict = None) -> Dict:
    """Force the recommendation to be consistent with what the service supports."""
    set_names = [es["name"] for es in model["entity_sets"]]
    by_name = {es["name"]: es for es in model["entity_sets"]}
    rich = _annotation_richness(model)

    primary = rec.get("primary_entity_set")
    if primary not in by_name:
        # pick the set with a LineItem if any, else the first
        primary = next((es["name"] for es in model["entity_sets"]
                        if (es.get("annotations") or {}).get("line_item")), set_names[0])
    es = by_name[primary]

    # Target: FE only if annotations make it viable.
    target = rec.get("target")
    if target not in TARGETS:
        target = "fiori_elements" if rich["fe_viable"] else "freestyle"
    if target == "fiori_elements" and not rich["fe_viable"]:
        target = "freestyle"

    # Floorplan must belong to the chosen target's family.
    floorplan = rec.get("floorplan", "")
    fam = FE_FLOORPLANS if target == "fiori_elements" else FREESTYLE_FLOORPLANS
    if floorplan not in fam:
        # sensible default per target
        has_child = bool(es.get("navigations"))
        if target == "fiori_elements":
            floorplan = "ObjectPage" if has_child else "ListReport"
        else:
            floorplan = "FreestyleMasterDetail" if has_child else "FreestyleListDetail"

    # Controls: keep only supported ones, drop those the facts can't justify.
    controls = [c for c in (rec.get("controls") or []) if c in RICH_CONTROLS]
    props = es["properties"]
    has_date = any(p["ui_type"] in ("date", "datetime") for p in props)
    has_measure = any(p["ui_type"] in ("amount", "number") for p in props)
    has_nav = bool(es.get("navigations"))
    ann = es.get("annotations") or {}

    def _keep(c):
        if c == "timeline":
            return has_date
        if c == "chart" or c == "kpiCards":
            return has_measure
        if c == "treeTable":
            return has_nav  # coarse: real tree needs self-reference; allow if nav
        if c == "valueHelp":
            return bool(ann.get("value_lists"))
        if c == "objectPage":
            return has_nav
        return True
    controls = [c for c in controls if _keep(c)]
    if "table" not in controls and "smartTable" not in controls:
        controls.insert(0, "smartTable" if target == "fiori_elements" else "table")
    if has_nav and "objectPage" not in controls:
        controls.append("objectPage")

    return {
        "target": target,
        "floorplan": floorplan,
        "controls": controls,
        "primary_entity_set": primary,
        "rationale": rec.get("rationale", ""),
        "annotation_richness": rich,
    }


def recommend(requirement: str, model: Dict, ai_fn: Callable[[str, str], str],
              sample_profiles: Dict = None) -> Dict:
    """Produce a validated design recommendation. Never raises; falls back to a
    deterministic rules-based recommendation if the AI is unavailable."""
    try:
        raw = ai_fn(_system_prompt(), _context(requirement, model, sample_profiles)
                    + "\n\nProduce the recommendation JSON now.")
        rec = _extract_json(raw)
    except Exception:
        rec = {}
    return _validate(rec, model, sample_profiles)


def recommendation_summary(rec: Dict) -> str:
    """One-line human summary for logs / UI."""
    return (f"{rec['target']} · {rec['floorplan']} · "
            f"controls: {', '.join(rec['controls'])}"
            + (f" — {rec['rationale']}" if rec.get("rationale") else ""))
