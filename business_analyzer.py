"""
business_analyzer.py — Module 3 (Business Requirement Analyzer) +
                        Module 4 (Screen Planner).

Given the user's natural-language requirement AND the parsed metadata business
model, an AI call decides:

  Module 3 — Requirement Analysis:
     - which entity set is the primary focus
     - which CRUD actions are wanted (search/create/edit/delete/approve...)
     - the app archetype (List Report, Object Page, Worklist, Analytical,
       Flexible Column Layout, Single Page)

  Module 4 — Screen Planning:
     - which UI building blocks are needed (table, form, dialog, filter bar,
       chart, object header, upload...)
     - which fields to show in the list vs the detail
     - the title/description field to use per entity

The AI is constrained to choose ONLY from the entity sets / fields that actually
exist in the metadata (passed in the prompt), so it cannot hallucinate a field
that isn't in the service. The result is validated against the model and
repaired if the AI picks something invalid, so downstream generation always gets
a usable plan.

`ai_fn(system_prompt, user_prompt) -> str` is the same callable the generator
uses (wraps SAP AI Core). This module never imports Streamlit or the SDK.
"""

from __future__ import annotations
import json
import re
from typing import Dict, List, Callable

from metadata_parser import summarize_for_prompt
from sample_data import summarize_profile_for_prompt


def _relationships_for_prompt(model: Dict) -> str:
    """Describe navigation relationships (header -> items) for the AI."""
    by_type = {es["entity_type"]: es["name"] for es in model["entity_sets"]}
    lines = []
    for es in model["entity_sets"]:
        for nav in es.get("navigations", []):
            target_set = by_type.get(nav.get("target_type", ""), nav.get("target_type", ""))
            if target_set:
                arrow = "-*>" if nav.get("to_many") else "->"
                lines.append(f"- {es['name']} {arrow} {target_set} (nav '{nav['name']}')")
    return ("Navigation relationships:\n" + "\n".join(lines)) if lines else ""


def _annotations_for_prompt(model: Dict) -> str:
    """Summarise UI annotations + capabilities per entity set for the AI."""
    blocks = []
    for es in model["entity_sets"]:
        ann = es.get("annotations")
        if not ann:
            continue
        parts = []
        if ann.get("line_item"):
            parts.append("LineItem=[" + ",".join(ann["line_item"]) + "]")
        if ann.get("selection_fields"):
            parts.append("SelectionFields=[" + ",".join(ann["selection_fields"]) + "]")
        if ann.get("value_lists"):
            parts.append("ValueHelp=[" + ",".join(ann["value_lists"].keys()) + "]")
        if ann.get("field_groups"):
            parts.append("FieldGroups=" + ",".join(ann["field_groups"].keys()))
        cap = ann.get("capabilities", {})
        parts.append("caps(C%s U%s D%s)" % (
            "+" if cap.get("insertable", True) else "-",
            "+" if cap.get("updatable", True) else "-",
            "+" if cap.get("deletable", True) else "-"))
        if parts:
            blocks.append(f"- {es['name']}: " + "; ".join(parts))
    return ("UI annotations & capabilities:\n" + "\n".join(blocks)) if blocks else ""


APP_ARCHETYPES = [
    "ListReport",         # list + filter + navigate to object page
    "ListDetail",         # simple master/detail (split)
    "Worklist",           # actionable list of items to process
    "ObjectPage",         # single object, header + sections
    "Analytical",         # KPIs + charts over an aggregatable set
    "FlexibleColumnLayout",  # multi-column drill-in
]

KNOWN_ACTIONS = ["search", "create", "edit", "delete", "approve", "reject", "export"]
KNOWN_BLOCKS = ["table", "form", "dialog", "filterBar", "chart", "objectHeader",
                "upload", "wizard", "smartTable", "smartFilterBar"]


def _analyzer_system_prompt() -> str:
    return (
        "You are an SAP Fiori solution architect. Given a business requirement "
        "and RICH context about a real OData service (metadata, navigation "
        "relationships, UI annotations, CRUD capabilities, and sample-data value "
        "profiles), produce a build plan. "
        "You MUST only reference entity sets and field/property names that appear "
        "in the provided context — never invent fields. "
        "Respond with STRICT JSON only, no prose, no markdown fences. "
        "Schema:\n"
        "{\n"
        '  "primary_entity_set": "<one of the entity set names>",\n'
        '  "app_type": "<one of: ' + ", ".join(APP_ARCHETYPES) + '>",\n'
        '  "actions": ["<subset of: ' + ", ".join(KNOWN_ACTIONS) + '>"],\n'
        '  "blocks": ["<subset of: ' + ", ".join(KNOWN_BLOCKS) + '>"],\n'
        '  "list_fields": ["<fields to show in the list/table>"],\n'
        '  "detail_fields": ["<fields to show on the detail/object page>"],\n'
        '  "title_field": "<best field for each row\'s title>",\n'
        '  "key_field": "<the key field of the primary entity set>",\n'
        '  "child_entity_set": "<a navigation target set for line-items, or empty>",\n'
        '  "filter_fields": ["<fields to expose in the filter bar>"],\n'
        '  "status_field": "<a low-cardinality field to render as colored status, or empty>",\n'
        '  "value_help_fields": ["<fields that should get a value-help dialog>"],\n'
        '  "reasoning": "<one short sentence>"\n'
        "}\n"
        "Guidance:\n"
        "- Prefer UI.LineItem order for list_fields and UI.SelectionField for "
        "filter_fields when those annotations are provided.\n"
        "- If the context lists a navigation relationship (header -> items), set "
        "child_entity_set to that target and prefer app_type ObjectPage or "
        "FlexibleColumnLayout so the items render as a related table.\n"
        "- Use the sample-data value profiles: a field with a small fixed set of "
        "values (e.g. Open/Closed/Cancelled) is the status_field; fields with a "
        "value-list annotation are value_help_fields; skip mostly-empty fields.\n"
        "- Respect CRUD capabilities: do not include create/edit/delete actions "
        "the capabilities forbid.\n"
        "- read-only browse => ListReport; process/approve => Worklist; heavy "
        "aggregation => Analytical; header+items drill-in => ObjectPage or "
        "FlexibleColumnLayout; otherwise ListDetail. Choose 4-8 list_fields."
    )


def _extract_json(raw: str) -> Dict:
    """Pull a JSON object out of an AI response that may have stray text/fences."""
    s = raw.strip()
    s = re.sub(r"^```(?:json)?", "", s).strip()
    s = re.sub(r"```$", "", s).strip()
    # Grab the outermost {...}
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"No JSON object found in AI response: {raw[:200]}")
    return json.loads(s[start:end + 1])


def _validate_and_repair(plan: Dict, model: Dict) -> Dict:
    """Force the plan to reference only things that exist in the metadata.
    Any invalid choice is replaced with a sensible default derived from the
    model, so downstream generation never receives a dangling reference."""
    sets_by_name = {es["name"]: es for es in model["entity_sets"]}
    set_names = list(sets_by_name.keys())

    # primary entity set
    primary = plan.get("primary_entity_set")
    if primary not in sets_by_name:
        primary = set_names[0]
    es = sets_by_name[primary]
    prop_names = [p["name"] for p in es["properties"]]

    # app type
    app_type = plan.get("app_type")
    if app_type not in APP_ARCHETYPES:
        app_type = "ListDetail"

    # actions / blocks filtered to known + CRUD-flag-aware
    actions = [a for a in (plan.get("actions") or []) if a in KNOWN_ACTIONS]
    if not es.get("creatable", True):
        actions = [a for a in actions if a != "create"]
    if not es.get("deletable", True):
        actions = [a for a in actions if a != "delete"]
    if not es.get("updatable", True):
        actions = [a for a in actions if a not in ("edit", "approve", "reject")]
    if "search" not in actions:
        actions = ["search"] + actions

    blocks = [b for b in (plan.get("blocks") or []) if b in KNOWN_BLOCKS]
    if "table" not in blocks:
        blocks = ["table"] + blocks

    # key field
    key_field = plan.get("key_field")
    if key_field not in prop_names:
        key_field = es["keys"][0] if es["keys"] else prop_names[0]

    # list/detail fields (keep only real ones; prefer LineItem annotation order)
    _ann = es.get("annotations", {}) or {}
    list_fields = [f for f in (plan.get("list_fields") or []) if f in prop_names]
    if not list_fields and _ann.get("line_item"):
        list_fields = [f for f in _ann["line_item"] if f in prop_names]
    if not list_fields:
        list_fields = prop_names[:6]
    detail_fields = [f for f in (plan.get("detail_fields") or []) if f in prop_names]
    if not detail_fields:
        detail_fields = prop_names

    # title field
    title_field = plan.get("title_field")
    # Prefer a descriptive non-key text field. Even when the AI returned the key
    # as the title (common for standard SAP services where the key is a text
    # field like "SalesOrder"), pick a more meaningful field if one exists — a
    # list of bare IDs is not useful. Heuristic: favor fields whose name hints
    # at a name/description/party/text, else the first non-key text field.
    def _pick_title():
        text_fields = [p["name"] for p in es["properties"]
                       if p["ui_type"] == "text" and p["name"] != key_field]
        if not text_fields:
            return key_field
        pref = [f for f in text_fields
                if re.search(r"(name|desc|text|party|title|customer|product|"
                             r"material|subject)", f, re.I)]
        return (pref or text_fields)[0]
    if title_field not in prop_names or title_field == key_field:
        title_field = _pick_title()

    # Ensure the list shows more than just the key: if list_fields collapsed to
    # only the key (or is empty), widen it to the first several real fields.
    if not list_fields or set(list_fields) <= {key_field}:
        widened = [key_field] + [p["name"] for p in es["properties"]
                                 if p["name"] != key_field][:5]
        # de-dupe preserving order
        seen = set(); list_fields = [f for f in widened
                                     if not (f in seen or seen.add(f))]

    # child entity set from navigations
    nav_targets = {n.get("target_type"): n for n in es.get("navigations", [])}
    child = plan.get("child_entity_set") or ""
    if child and child not in set_names:
        # AI may have given a type name; try to map to a set of that type
        by_type = {s["entity_type"]: s["name"] for s in model["entity_sets"]}
        child = by_type.get(child, "")

    ann = es.get("annotations", {}) or {}

    # filter fields: prefer annotation SelectionFields, else AI, validated
    filter_fields = [f for f in (plan.get("filter_fields") or []) if f in prop_names]
    if not filter_fields and ann.get("selection_fields"):
        filter_fields = [f for f in ann["selection_fields"] if f in prop_names]

    # status field: validate against props; must be a real (ideally enum) field
    status_field = plan.get("status_field") or ""
    if status_field not in prop_names:
        status_field = ""

    # value-help fields: prefer annotation value_lists, intersect with AI + props
    vh = [f for f in (plan.get("value_help_fields") or []) if f in prop_names]
    for f in (ann.get("value_lists") or {}):
        if f in prop_names and f not in vh:
            vh.append(f)

    return {
        "primary_entity_set": primary,
        "app_type": app_type,
        "actions": actions,
        "blocks": blocks,
        "list_fields": list_fields,
        "detail_fields": detail_fields,
        "title_field": title_field,
        "key_field": key_field,
        "child_entity_set": child,
        "filter_fields": filter_fields,
        "status_field": status_field,
        "value_help_fields": vh,
        "reasoning": plan.get("reasoning", ""),
    }


def analyze(requirement: str, model: Dict, ai_fn: Callable[[str, str], str],
            sample_profiles: Dict = None) -> Dict:
    """Run Modules 3+4 with rich context. `sample_profiles` maps
    entity_set_name -> profile dict from sample_data.inspect_entity_set (optional).
    Returns a validated build plan dict. Falls back to a deterministic plan if
    the AI call/JSON fails, so generation never blocks."""
    summary = summarize_for_prompt(model)
    rels = _relationships_for_prompt(model)
    anns = _annotations_for_prompt(model)
    sample_txt = ""
    if sample_profiles:
        chunks = []
        for es_name, prof in sample_profiles.items():
            s = summarize_profile_for_prompt(es_name, prof)
            if s:
                chunks.append(s)
        sample_txt = "\n".join(chunks)

    context_parts = [f"AVAILABLE METADATA (only use these entity sets and fields):\n{summary}"]
    if rels:
        context_parts.append(rels)
    if anns:
        context_parts.append(anns)
    if sample_txt:
        context_parts.append(sample_txt)

    user_prompt = (
        f"BUSINESS REQUIREMENT:\n{requirement.strip()}\n\n"
        + "\n\n".join(context_parts)
        + "\n\nProduce the build-plan JSON now."
    )
    try:
        raw = ai_fn(_analyzer_system_prompt(), user_prompt)
        plan = _extract_json(raw)
    except Exception:
        plan = {}  # fall through to repair, which fills defaults from the model
    return _validate_and_repair(plan, model)


def plan_to_appspec_kwargs(plan: Dict, model: Dict) -> Dict:
    """Translate the build plan into the keyword arguments the existing
    generator.AppSpec expects, so the metadata pipeline plugs straight into the
    generator we already have (entity_set/key_field/title_field/fields/app_type
    + optional child items)."""
    sets_by_name = {es["name"]: es for es in model["entity_sets"]}
    es = sets_by_name[plan["primary_entity_set"]]

    # Map the analyzer's business archetype to the generator's concrete view
    # types (ListDetail | Worklist | MasterDetail | SinglePage). The archetypes
    # are richer than the generator's four patterns, so several collapse:
    #   ListReport/ListDetail        -> ListDetail
    #   Worklist                     -> Worklist
    #   ObjectPage/FlexibleColumnLayout (header+items) -> MasterDetail
    #   Analytical                   -> ListDetail (table + KPIs via freestyle)
    archetype = plan.get("app_type", "ListDetail")
    has_child = bool(plan.get("child_entity_set"))
    _MAP = {
        "ListReport": "ListDetail",
        "ListDetail": "ListDetail",
        "Worklist": "Worklist",
        "ObjectPage": "ObjectPage" if has_child else "ListDetail",
        "FlexibleColumnLayout": "ObjectPage" if has_child else "MasterDetail",
        "Analytical": "ListDetail",
    }
    generator_type = _MAP.get(archetype, "ListDetail")

    kwargs = {
        "app_type": generator_type,
        "entity_set": plan["primary_entity_set"],
        "key_field": plan["key_field"],
        "title_field": plan["title_field"],
        # generator's `fields` are the extra columns beyond key/title
        "fields": [f for f in plan["list_fields"]
                   if f not in (plan["key_field"], plan["title_field"])],
    }

    child = plan.get("child_entity_set")
    if child and child in sets_by_name:
        child_es = sets_by_name[child]
        child_keys = child_es["keys"] or [child_es["properties"][0]["name"]]
        child_props = [p["name"] for p in child_es["properties"]]
        title = next((p["name"] for p in child_es["properties"]
                      if p["ui_type"] == "text" and p["name"] not in child_keys),
                     child_props[0])
        # Find the navigation property on the PRIMARY entity that targets the
        # child's type, so the object page can bind/expand the relationship by
        # its OData navigation name (e.g. "ToItems"), not the entity-set name.
        nav_name = ""
        child_type = child_es["entity_type"]
        for nav in es.get("navigations", []):
            if nav.get("target_type") == child_type:
                nav_name = nav.get("name", "")
                break
        kwargs.update({
            "items_entity_set": child,
            "items_key_field": child_keys[0],
            "items_title_field": title,
            "items_fields": [p for p in child_props
                             if p not in (child_keys[0], title)][:6],
            "items_nav_name": nav_name or child,
        })
    return kwargs
