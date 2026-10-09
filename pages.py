"""
pages.py — multi-page application design (floorplan "FreestyleApp").

The AI (or the rule-based fallback) designs an app as a set of navigation pages:

  {"id": "home", "type": "dashboard", "title", "icon",
   "kpis":   [{"label", "entity_set", "field", "aggregation"}],
   "charts": [{"title", "entity_set", "dimension", "measure", "aggregation", "type"}],
   "lists":  [{"title", "entity_set", "fields", "sort": {"field", "descending"}}]}

  {"id": "orders", "type": "list", "title", "icon", "entity_set",
   "columns", "filters", "search", "sort", "status", "title_field", "description_field",
   "capabilities", "form", "value_helps", "actions", "kpis",
   "detail": {"header_fields", "sections", "tables"} | null,
   "create": "dialog" | "wizard", "wizard_steps": [{"title", "fields"}]}

validate_pages() checks every page against the metadata (list pages are run
through blueprint.validate() with their own entity set, so they get exactly the
same repairs as a single-entity app) and returns normalised pages:

  dashboard: {"id", "type", "title", "icon", "kpis", "charts", "lists"}  (with routes)
  list:      {"id", "type", "title", "icon", "entity_set", "sub": <validated single-entity
              blueprint parts>, "has_detail", "create", "wizard_steps", "detail_id", "wizard_id"}
"""

from __future__ import annotations
import re
from typing import Dict, List, Tuple

AGGS = ("sum", "average", "min", "max", "count")
CHART_TYPES = ("bar", "donut", "column")
VIZ_TYPES = ("column", "bar", "line", "area", "pie", "donut", "stacked_column", "stacked_bar",
             "combination", "heatmap")
TIME_GRAINS = ("day", "month", "quarter", "year")
PAGE_TYPES = ("dashboard", "list", "kanban", "calendar", "analytics")
DEFAULT_ICONS = {"dashboard": "sap-icon://home", "list": "sap-icon://list",
                 "kanban": "sap-icon://screen-split-three", "calendar": "sap-icon://calendar",
                 "analytics": "sap-icon://business-objects-experience"}
ICON_HINTS = [
    (r"order|sales", "sap-icon://sales-order"), (r"customer|partner|client", "sap-icon://customer"),
    (r"product|material|article|item", "sap-icon://product"), (r"supplier|vendor", "sap-icon://supplier"),
    (r"employee|person|people|user", "sap-icon://employee"), (r"invoice|bill", "sap-icon://monitor-payments"),
    (r"purchase", "sap-icon://cart"), (r"ship|deliver|freight|logistic", "sap-icon://shipping-status"),
    (r"categor|group", "sap-icon://group-2"), (r"trip|travel|flight|airport|airline", "sap-icon://flight"),
    (r"stock|inventory|warehouse", "sap-icon://inventory"),
]


def schema_prompt() -> str:
    return (
        ' "pages": [   // ONLY for floorplan FreestyleApp: 2-7 navigation pages\n'
        '   {"id": str, "type": "dashboard", "title": str, "icon": "sap-icon://...",\n'
        '    "kpis": [{"label": str, "entity_set": str, "field": str, "aggregation": "count|sum|average|min|max"}],\n'
        '    "charts": [{"title": str, "entity_set": str, "dimension": str, "measure": str, '
        '"aggregation": str, "type": "bar|donut|column"}],\n'
        '    "lists": [{"title": str, "entity_set": str, "fields": [str], "sort": {"field": str, "descending": bool}}]},\n'
        '   {"id": str, "type": "list", "title": str, "icon": "sap-icon://...", "entity_set": str,\n'
        '    "title_field": str, "description_field": str, "columns": [{"field": str, "importance": str}],\n'
        '    "filters": [{"field": str}], "search": bool, "sort": {...}, "status": {...},\n'
        '    "capabilities": {...}, "form": {...}, "value_helps": [...], "actions": [...],\n'
        '    "kpis": [{"field": str, "label": str, "aggregation": str}],\n'
        '    "detail": {"header_fields": [str], "sections": [...], "tables": [...]} | null,\n'
        '    "create": "dialog|wizard", "wizard_steps": [{"title": str, "fields": [str]}],\n'
        '    "layout": "table|split",            // split = list on the left, record on the right\n'
        '    "quick_tabs": bool,                 // tabs per status value with counts (needs status)\n'
        '    "logic": {...custom logic, see CUSTOM LOGIC...}},\n'
        '   {"id": str, "type": "kanban", "title": str, "icon": str, "entity_set": str,\n'
        '    "lane_field": str, "lanes": [{"value": str, "title": str}],   // REAL codes, in process order\n'
        '    "card_title": str, "card_subtitle": str, "card_fields": [str], // max 4\n'
        '    "move": bool},                      // drag a card to another lane = update lane_field\n'
        '   {"id": str, "type": "calendar", "title": str, "icon": str, "entity_set": str,\n'
        '    "start_field": str, "end_field": str, "title_field": str, "text_field": str,\n'
        '    "color_field": str},                // date fields; color_field = status/type code\n'
        '   {"id": str, "type": "analytics", "title": str, "icon": str,\n'
        '    "charts": [{"title": str, "entity_set": str, "dimension": str, "time_grain": "day|month|quarter|year",\n'
        '                "measures": [{"field": str, "aggregation": "sum|average|min|max|count"}\n'
        '                             | {"expression": "<CUSTOM LOGIC expr over row>", "label": str, "aggregation": str}],\n'
        '                "type": "column|bar|line|area|pie|donut|stacked_column|stacked_bar|combination|heatmap",\n'
        '                "series": str}]}    // optional 2nd dimension (stacked/colour)\n'
        ' ],\n'
    )


def prompt_rules() -> str:
    return (
        "- FreestyleApp = a custom multi-page app with side navigation. Use it when the "
        "requirement asks for several pages/areas/entities, a home/dashboard plus "
        "lists, or a bespoke app spanning multiple business objects. Give each business "
        "object its own 'list' page (with 'detail' unless it is a simple lookup) and add a "
        "'dashboard' page first when KPIs/overview/charts/home are mentioned. Use "
        "create:'wizard' with 2-4 logical wizard_steps when a guided/step-by-step creation "
        "is asked or the create form is long; otherwise 'dialog'. Dashboard charts group a "
        "measure (or count, measure empty) by a low-cardinality dimension. For FreestyleApp "
        "still fill 'main' with the most important list page's entity.\n"
        "- Pick page types from what the requirement describes: board/kanban/pipeline/stages/"
        "drag between columns => 'kanban' (lane_field = a status/stage code; move only when that "
        "field is updatable); schedule/calendar/planning/appointments/deadlines/timeline => "
        "'calendar' (needs a date field); analysis/reports/trends/breakdowns/charts with several "
        "measures => 'analytics' (time trends: dimension = date field + time_grain, type line/area); "
        "master-detail/split/side-by-side on one screen => list page with layout 'split'; "
        "queues/inboxes by state (open/in process/done) => list page with quick_tabs. Every "
        "kanban/calendar/analytics entity should ALSO have a list page (with detail) so cards, "
        "appointments and chart drill-downs can open the record.\n"
    )


def _slug(text: str, used: set) -> str:
    base = re.sub(r"[^a-z0-9]", "", (text or "").lower())[:20] or "page"
    if base[0].isdigit():
        base = "p" + base
    s, i = base, 2
    while s in used:
        s = f"{base}{i}"
        i += 1
    used.add(s)
    return s


def _icon(icon: str, ptype: str, label: str) -> str:
    from sap_icons import SAP_ICONS
    if isinstance(icon, str) and re.fullmatch(r"sap-icon://[A-Za-z0-9-]+", icon or "") \
            and icon.split("//", 1)[1] in SAP_ICONS:
        return icon                      # only icons that exist (others render blank)
    if ptype in ("kanban", "calendar", "analytics"):
        return DEFAULT_ICONS[ptype]          # the page type says more than its subject
    for pat, ic in ICON_HINTS:
        if re.search(pat, label or "", re.I):
            return ic
    return DEFAULT_ICONS.get(ptype, "sap-icon://document")


import multi_service as _ms


def validate_pages(raw_pages: List[Dict], model: Dict, requirement: str,
                   notes: List[str]) -> List[Dict]:
    import blueprint as bpm   # local import: blueprint imports this module
    sets = {es["name"]: es for es in model["entity_sets"]}
    used: set = set()
    pages: List[Dict] = []
    # Accept already-validated pages (e.g. the reviewed/edited blueprint coming
    # back from the UI): flatten their "sub" parts to the design shape again.
    raw_pages = [_from_validated(p) if isinstance(p, dict) and p.get("type") == "list" and "sub" in p
                 else p for p in (raw_pages or [])]

    # list pages first so dashboards can link to them
    raw_lists = [p for p in raw_pages or [] if isinstance(p, dict) and p.get("type") == "list"]
    raw_dash = [p for p in raw_pages or [] if isinstance(p, dict) and p.get("type") == "dashboard"]
    raw_other = [p for p in raw_pages or [] if isinstance(p, dict)
                 and p.get("type") in ("kanban", "calendar", "analytics")]
    for p in raw_pages or []:
        if isinstance(p, dict) and p.get("type") not in PAGE_TYPES:
            notes.append(f"Page '{p.get('title') or p.get('id')}' dropped — unknown page type "
                         f"'{p.get('type')}' (use {', '.join(PAGE_TYPES)}).")

    pos = {id(p): i for i, p in enumerate(raw_pages or [])}
    list_by_set: Dict[str, Dict] = {}
    for p in raw_lists:
        es = sets.get(p.get("entity_set"))
        if not es:
            notes.append(f"List page '{p.get('title') or p.get('id')}' dropped — unknown entity set "
                         f"'{p.get('entity_set')}'.")
            continue
        det = p.get("detail") if isinstance(p.get("detail"), dict) else None
        if det is None and "detail" not in p:
            # no detail layout given: default sections + every to-many child as a table
            det = {"header_fields": [], "sections": [],
                   "tables": [{"navigation": n["name"], "title": t.get("label") or n["name"]}
                              for n, t in bpm._to_many_navs(model, es)[:3]]}
        single = {
            "app": {"title": p.get("title") or es.get("label") or es["name"]},
            "floorplan": "FreestyleListDetail",
            "main": {k: p.get(k) for k in ("title_field", "description_field", "columns", "filters",
                                           "search", "sort", "status")} | {"entity_set": es["name"]},
            "object_page": det or {"header_fields": [], "sections": [], "tables": []},
            "capabilities": p.get("capabilities") or {},
            "form": p.get("form") or {},
            "value_helps": p.get("value_helps") or [],
            "actions": p.get("actions") or [],
            "analytics": {"kpis": p.get("kpis") or [], "measures": [], "dimensions": []},
            "logic": p.get("logic") or {},
        }
        # a page stays inside its own service (value helps, child tables, actions)
        sub, sub_notes = bpm.validate(single, _ms.model_for_set(model, es["name"]), requirement or es["name"])
        title = str(p.get("title") or es.get("label") or es["name"])[:40]
        notes.extend(f"[{title}] {n}" for n in sub_notes)
        pid = _slug(p.get("id") or title, used)
        create = "wizard" if p.get("create") == "wizard" and sub["capabilities"]["create"] else "dialog"
        steps = []
        if create == "wizard":
            create_fields = set(sub["form"]["create_fields"])
            seen = set()
            for st in p.get("wizard_steps") or []:
                flds = [f for f in (st.get("fields") or []) if f in create_fields and f not in seen]
                seen.update(flds)
                if flds:
                    steps.append({"title": str(st.get("title") or f"Step {len(steps) + 1}")[:40],
                                  "fields": flds})
            rest = [f for f in sub["form"]["create_fields"] if f not in seen]
            if rest:
                if steps and len(rest) <= 4:
                    steps[-1]["fields"] += rest
                else:   # one step per form section (complete create form)
                    rset = set(rest)
                    for g in sub["form"].get("create_groups") or [{"title": "Details", "fields": rest}]:
                        flds = [f for f in g["fields"] if f in rset]
                        rset -= set(flds)
                        for i in range(0, len(flds), 12):
                            steps.append({"title": (g["title"] or "Details")[:40], "fields": flds[i:i + 12]})
                    if rset:
                        steps.append({"title": "Additional Details", "fields": [f for f in rest if f in rset]})
            # child-record steps (items, …) + Review must fit the sap.m.Wizard limit of 8
            steps = steps[:max(1, 7 - len(sub["form"].get("create_children") or []))]
            steps = steps[:7]                     # + Review step = max 8 (sap.m.Wizard limit)
            # sap.m.Wizard needs >= 3 steps: ensure >= 2 data steps (+ Review)
            if len(steps) == 1 and len(steps[0]["fields"]) >= 2:
                f = steps[0]["fields"]
                half = (len(f) + 1) // 2
                steps = [{"title": steps[0]["title"], "fields": f[:half]},
                         {"title": "Additional Details", "fields": f[half:]}]
        layout = "split" if p.get("layout") == "split" else "table"
        quick_tabs = bool(p.get("quick_tabs")) and bool(sub["main"]["status"]["field"])
        if p.get("quick_tabs") and not quick_tabs:
            notes.append(f"[{title}] Status tabs need a status field — shown without tabs.")
        page = {"id": pid, "type": "list", "title": title,
                "icon": _icon(p.get("icon"), "list", title + " " + es["name"]),
                "entity_set": es["name"], "sub": sub,
                "has_detail": det is not None or p.get("detail") is None and "detail" not in p,
                "create": create, "wizard_steps": steps,
                "detail_id": "", "wizard_id": "", "layout": layout, "quick_tabs": quick_tabs}
        if layout == "split":
            page["has_detail"] = False   # the record is shown next to the list
        page["_pos"] = pos.get(id(p), 0)
        # action ids must be unique app-wide (i18n keys / config)
        for a in sub["actions"]:
            a["id"] = f"{pid}{a['id']}"
        pages.append(page)
        list_by_set.setdefault(es["name"], page)

    # detail / wizard route ids
    for page in pages:
        if page["has_detail"]:
            page["detail_id"] = _slug(page["id"] + "detail", used)
        if page["create"] == "wizard":
            page["wizard_id"] = _slug(page["id"] + "new", used)

    dashboards = []
    for p in raw_dash:
        title = str(p.get("title") or "Overview")[:40]
        did = _slug(p.get("id") or title, used)
        kpis, charts, lists = [], [], []
        for i, k in enumerate(p.get("kpis") or []):
            es = sets.get(k.get("entity_set"))
            agg = k.get("aggregation") if k.get("aggregation") in AGGS else "count"
            field = k.get("field") if es and k.get("field") in {x["name"] for x in es["properties"]} else ""
            if not es or (agg != "count" and not field):
                notes.append(f"[{title}] KPI '{k.get('label')}' dropped — unknown entity set/field.")
                continue
            target = list_by_set.get(es["name"])
            kpis.append({"id": f"{did}k{i + 1}", "label": str(k.get("label") or es.get("label") or es["name"])[:40],
                         "entity_set": es["name"], "field": field, "aggregation": agg,
                         "route": target["id"] if target else ""})
        for i, c in enumerate(p.get("charts") or []):
            es = sets.get(c.get("entity_set"))
            props = {x["name"]: x for x in es["properties"]} if es else {}
            dim = c.get("dimension") if c.get("dimension") in props else ""
            meas = c.get("measure") if c.get("measure") in props and \
                props[c.get("measure")]["ui_type"] in ("amount", "number") else ""
            if not es or not dim:
                notes.append(f"[{title}] Chart '{c.get('title')}' dropped — needs an entity set and a dimension.")
                continue
            charts.append({"id": f"{did}c{i + 1}", "title": str(c.get("title") or dim)[:60],
                           "entity_set": es["name"], "dimension": dim, "measure": meas,
                           "aggregation": (c.get("aggregation") if c.get("aggregation") in AGGS else "sum")
                           if meas else "count",
                           "type": c.get("type") if c.get("type") in CHART_TYPES else "bar"})
        for i, l in enumerate(p.get("lists") or []):
            es = sets.get(l.get("entity_set"))
            if not es:
                notes.append(f"[{title}] List '{l.get('title')}' dropped — unknown entity set.")
                continue
            props = {x["name"] for x in es["properties"]}
            fields = [f for f in (l.get("fields") or []) if f in props][:5] or bpm._display_fields(es, 4)
            sort = l.get("sort") or {}
            sfield = sort.get("field") if sort.get("field") in props else (es["keys"][0] if es["keys"] else fields[0])
            target = list_by_set.get(es["name"])
            lists.append({"id": f"{did}l{i + 1}", "title": str(l.get("title") or es.get("label") or es["name"])[:60],
                          "entity_set": es["name"], "fields": fields,
                          "sort": {"field": sfield, "descending": bool(sort.get("descending", True))},
                          "route": target["detail_id"] if target and target["detail_id"] else ""})
        if not (kpis or charts or lists):
            notes.append(f"Dashboard '{title}' dropped — none of its tiles/charts/lists were valid.")
            continue
        dashboards.append({"id": did, "type": "dashboard", "title": title,
                           "icon": _icon(p.get("icon"), "dashboard", title),
                           "kpis": kpis[:6], "charts": charts[:4], "lists": lists[:3],
                           "_pos": pos.get(id(p), 0)})

    others = []
    for p in raw_other:
        fn = {"kanban": _kanban, "calendar": _calendar, "analytics": _analytics}[p["type"]]
        page = fn(p, sets, list_by_set, used, notes, bpm)
        if page:
            page["_pos"] = pos.get(id(p), 0)
            others.append(page)
    # navigation order as designed
    out = sorted(dashboards + pages + others, key=lambda x: x["_pos"])
    for x in out:
        x.pop("_pos", None)
    return out


def _detail_route(list_by_set: Dict, es_name: str) -> str:
    t = list_by_set.get(es_name)
    return t["detail_id"] if t and t["detail_id"] else ""


def _field(name, props, kinds=None):
    if name in props and (kinds is None or props[name]["ui_type"] in kinds):
        return name
    return ""


def _kanban(p, sets, list_by_set, used, notes, bpm):
    title = str(p.get("title") or "Board")[:40]
    es = sets.get(p.get("entity_set"))
    if not es:
        notes.append(f"Board '{title}' dropped — unknown entity set '{p.get('entity_set')}'.")
        return None
    props = {x["name"]: x for x in es["properties"]}
    lane = _field(p.get("lane_field"), props, ("text", "number", "boolean"))
    if not lane:
        lane = bpm._status_field(es)
        if not lane:
            notes.append(f"Board '{title}' dropped — no lane (status/stage) field.")
            return None
        notes.append(f"[{title}] Lane field '{p.get('lane_field')}' not usable — using {lane}.")
    lanes, seen = [], set()
    for l in p.get("lanes") or []:
        v = l.get("value") if isinstance(l, dict) else l
        if v is None or str(v) in seen:
            continue
        seen.add(str(v))
        lanes.append({"value": str(v), "title": str((l.get("title") if isinstance(l, dict) else "") or v or "(blank)")[:30]})
    card_title = _field(p.get("card_title"), props) or bpm._title_field(es)
    card_sub = _field(p.get("card_subtitle"), props)
    fields = [f for f in (p.get("card_fields") or []) if f in props and f not in (card_title, card_sub, lane)][:4]
    if not fields:
        fields = [f for f in bpm._display_fields(es, 6) if f not in (card_title, card_sub, lane)][:3]
    move = bool(p.get("move")) and es.get("updatable", True) and props[lane].get("updatable", True)
    if p.get("move") and not move:
        notes.append(f"[{title}] Drag & drop between lanes is off — {lane} is not updatable in the service.")
    pid = _slug(p.get("id") or title, used)
    return {"id": pid, "type": "kanban", "title": title, "icon": _icon(p.get("icon"), "kanban", title),
            "entity_set": es["name"], "key": list(es["keys"]), "lane_field": lane, "lanes": lanes[:10],
            "card_title": card_title, "card_subtitle": card_sub, "card_fields": fields, "move": move,
            "route": _detail_route(list_by_set, es["name"])}


def _calendar(p, sets, list_by_set, used, notes, bpm):
    title = str(p.get("title") or "Calendar")[:40]
    es = sets.get(p.get("entity_set"))
    if not es:
        notes.append(f"Calendar '{title}' dropped — unknown entity set '{p.get('entity_set')}'.")
        return None
    props = {x["name"]: x for x in es["properties"]}
    dates = [x["name"] for x in es["properties"] if x["ui_type"] in ("date", "datetime")]
    start = _field(p.get("start_field"), props, ("date", "datetime")) or (dates[0] if dates else "")
    if not start:
        notes.append(f"Calendar '{title}' dropped — {es['name']} has no date field.")
        return None
    end = _field(p.get("end_field"), props, ("date", "datetime"))
    ttl = _field(p.get("title_field"), props) or bpm._title_field(es)
    pid = _slug(p.get("id") or title, used)
    return {"id": pid, "type": "calendar", "title": title, "icon": _icon(p.get("icon"), "calendar", title),
            "entity_set": es["name"], "key": list(es["keys"]), "start_field": start,
            "end_field": end if end != start else "", "title_field": ttl,
            "text_field": _field(p.get("text_field"), props),
            "color_field": _field(p.get("color_field"), props, ("text", "number", "boolean")) or bpm._status_field(es),
            "route": _detail_route(list_by_set, es["name"])}


def _analytics(p, sets, list_by_set, used, notes, bpm):
    title = str(p.get("title") or "Analytics")[:40]
    charts = []
    for i, c in enumerate(p.get("charts") or []):
        es = sets.get(c.get("entity_set"))
        ctitle = str(c.get("title") or "")[:60]
        if not es:
            notes.append(f"[{title}] Chart '{ctitle}' dropped — unknown entity set.")
            continue
        props = {x["name"]: x for x in es["properties"]}
        dim = _field(c.get("dimension"), props)
        if not dim:
            notes.append(f"[{title}] Chart '{ctitle}' dropped — dimension '{c.get('dimension')}' is not a field.")
            continue
        measures = []
        for m in c.get("measures") or ([{"field": c.get("measure"), "aggregation": c.get("aggregation")}]
                                       if c.get("measure") else []):
            if not isinstance(m, dict):
                continue
            agg = m.get("aggregation") if m.get("aggregation") in AGGS else "sum"
            f = _field(m.get("field"), props, ("amount", "number"))
            if m.get("expression") and agg != "count":
                import logic as _lg
                try:
                    uses = _lg.check(m["expression"], set(props))
                    measures.append({"field": "", "aggregation": agg, "expression": m["expression"].strip(),
                                     "uses": uses, "label": str(m.get("label") or "Value")[:40]})
                except _lg.LogicError as e:
                    notes.append(f"[{title}] Calculated measure '{m.get('label')}' dropped — {e}.")
                continue
            if agg == "count" or not m.get("field"):
                measures.append({"field": "", "aggregation": "count", "label": "Count"})
            elif f:
                measures.append({"field": f, "aggregation": agg,
                                 "label": props[f].get("label") or f})
            else:
                notes.append(f"[{title}] Measure '{m.get('field')}' dropped — not a numeric field.")
        if not measures:
            measures = [{"field": "", "aggregation": "count", "label": "Count"}]
        typ = c.get("type") if c.get("type") in VIZ_TYPES else "column"
        is_time = props[dim]["ui_type"] in ("date", "datetime")
        grain = (c.get("time_grain") if c.get("time_grain") in TIME_GRAINS else "month") if is_time else ""
        series = _field(c.get("series"), props)
        if series == dim:
            series = ""
        if typ in ("pie", "donut"):
            measures, series = measures[:1], ""
        if typ == "heatmap" and not series:
            typ = "column"
        if typ == "combination" and len(measures) < 2:
            typ = "column"
        charts.append({"id": "", "title": ctitle or f"{measures[0]['label']} by {props[dim].get('label') or dim}",
                       "entity_set": es["name"], "dimension": dim,
                       "dimension_label": props[dim].get("label") or dim, "time_grain": grain,
                       "measures": measures[:4], "type": typ, "series": series,
                       "series_label": props[series].get("label") or series if series else "",
                       "route": list_by_set[es["name"]]["id"] if es["name"] in list_by_set else ""})
    if not charts:
        notes.append(f"Analytics page '{title}' dropped — none of its charts were valid.")
        return None
    pid = _slug(p.get("id") or title, used)
    for i, c in enumerate(charts):
        c["id"] = f"{pid}v{i + 1}"
    return {"id": pid, "type": "analytics", "title": title,
            "icon": _icon(p.get("icon"), "analytics", title), "charts": charts[:6]}


def _from_validated(p: Dict) -> Dict:
    sub = p["sub"]
    m = sub["main"]
    actions = []
    for a in sub.get("actions", []):
        a = dict(a)
        a.pop("id", None)        # ids are re-assigned per page
        actions.append(a)
    return {"id": p.get("id"), "type": "list", "title": p.get("title"), "icon": p.get("icon"),
            "entity_set": p.get("entity_set") or m.get("entity_set"),
            "title_field": m.get("title_field"), "description_field": m.get("description_field"),
            "columns": m.get("columns"), "filters": m.get("filters"), "search": m.get("search"),
            "sort": m.get("sort"), "status": m.get("status"),
            "capabilities": sub.get("capabilities"), "form": sub.get("form"),
            "value_helps": sub.get("value_helps"), "actions": actions,
            "kpis": (sub.get("analytics") or {}).get("kpis"),
            "detail": sub.get("object_page") if (p.get("detail_id") or p.get("layout") == "split") else None,
            "create": p.get("create"), "wizard_steps": p.get("wizard_steps"),
            "layout": p.get("layout"), "quick_tabs": p.get("quick_tabs"),
            "logic": _logic_raw(sub.get("logic"))}


def _logic_raw(lg):
    """validated logic block -> design shape (drop derived ids/uses)."""
    if not lg:
        return {}
    return {"computed": [{k: c[k] for k in ("label", "expression", "type", "after", "decimals") if k in c}
                         for c in lg.get("computed") or []],
            "row_state": (lg.get("row_state") or {}).get("expression") if lg.get("row_state") else None,
            "validations": [{k: v[k] for k in ("expression", "message", "fields")} for v in lg.get("validations") or []],
            "defaults": [{"field": d["field"], "expression": d["expression"]} for d in lg.get("defaults") or []],
            "derived": [{"field": d["field"], "expression": d["expression"]} for d in lg.get("derived") or []]}


def fallback_pages(requirement: str, model: Dict) -> List[Dict]:
    """Rule-based multi-page design: entity sets the requirement mentions (or the
    main one + its children), plus a dashboard when asked for."""
    import blueprint as bpm
    req = bpm._words(requirement)
    scored = []
    for es in model["entity_sets"]:
        if bpm._is_auxiliary(es):
            continue
        name = re.sub(r"([a-z])([A-Z])", r"\1 \2", es["name"]).replace("_", " ")
        words = bpm._words(name + " " + es.get("label", ""))
        overlap = sum(1 for w in words for r in req if w.startswith(r[:5]) or r.startswith(w[:5]))
        if overlap:
            scored.append((overlap, len(es.get("navigations", [])), es))
    scored.sort(key=lambda t: (-t[0], -t[1]))
    chosen = [t[2] for t in scored[:5]]
    if _ms.is_multi(model):
        # one area per service: the best matching (or main) object of every service
        per = []
        for s in _ms.services(model):
            sub = _ms.sub_model(model, s["alias"])
            main = bpm._pick_main(requirement, sub)
            per.append(main)
            # plus a strongly matching second object of the same service
            per += [t[2] for t in scored if t[2].get("service") == s["alias"] and t[0] >= 2
                    and t[2]["name"] != main["name"]][:1]
        chosen = per[:8]
    if not chosen:
        main = bpm._pick_main(requirement, model)
        chosen = [main] + [t for _, t in bpm._to_many_navs(model, main)][:2]
    raw = []
    if _ms.is_multi(model) or re.search(r"dashboard|overview|kpi|home|chart", requirement, re.I):
        first = chosen[0]
        nums = [p["name"] for p in first["properties"] if p["ui_type"] in ("amount", "number")
                and p["name"] not in first["keys"]]
        texts = [p for p in first["properties"] if p["ui_type"] == "text" and p["name"] not in first["keys"]]
        # low-cardinality dimensions first (status/type/category/country/...)
        dims = [p["name"] for p in texts if re.search(
            r"(status|type|category|country|city|region|group|class|priority|currency|unit)", p["name"], re.I)]             or [p["name"] for p in texts if (p.get("max_length") or 99) <= 15]
        raw.append({"type": "dashboard", "title": "Overview",
                    "kpis": [{"label": es.get("label") or es["name"], "entity_set": es["name"],
                              "aggregation": "count"} for es in chosen[:4 if _ms.is_multi(model) else 3]]
                    + ([{"label": f"Total {nums[0]}", "entity_set": first["name"], "field": nums[0],
                         "aggregation": "sum"}] if nums else []),
                    "charts": ([{"title": f"{first.get('label') or first['name']} by {dims[0]}",
                                 "entity_set": first["name"], "dimension": dims[0],
                                 "measure": nums[0] if nums else "", "type": "bar"}] if dims else []),
                    "lists": [{"title": f"Latest {first.get('label') or first['name']}",
                               "entity_set": first["name"]}]})
    wizard = bool(re.search(r"wizard|step.by.step|guided", requirement, re.I))
    split = bool(re.search(r"split|side.by.side|master.?detail", requirement, re.I))
    for i, es in enumerate(chosen):
        raw.append({"type": "list", "entity_set": es["name"], "title": es.get("label") or es["name"],
                    "create": "wizard" if wizard and i == 0 else "dialog",
                    "layout": "split" if split and i == 0 else "table",
                    "quick_tabs": bool(re.search(r"\btabs?\b|inbox|queue", requirement, re.I)) and i == 0})
    first = chosen[0]
    if re.search(r"kanban|\bboard\b|pipeline|stages?\b|swim ?lane", requirement, re.I):
        lane = bpm._status_field(first)
        if lane:
            raw.append({"type": "kanban", "entity_set": first["name"], "lane_field": lane,
                        "title": f"{first.get('label') or first['name']} Board", "move": True})
    if re.search(r"calendar|schedule|planning|appointment|deadline|timeline", requirement, re.I):
        dates = [p["name"] for p in first["properties"] if p["ui_type"] in ("date", "datetime")]
        if dates:
            raw.append({"type": "calendar", "entity_set": first["name"], "start_field": dates[0],
                        "title": f"{first.get('label') or first['name']} Calendar"})
    if re.search(r"analy|report|trend|breakdown", requirement, re.I):
        nums = [p["name"] for p in first["properties"] if p["ui_type"] in ("amount", "number")
                and p["name"] not in first["keys"]]
        dates = [p["name"] for p in first["properties"] if p["ui_type"] in ("date", "datetime")]
        dim = bpm._status_field(first) or next((p["name"] for p in first["properties"]
                                                if p["ui_type"] == "text" and p["name"] not in first["keys"]), "")
        charts = []
        if dates:
            charts.append({"title": "Trend", "entity_set": first["name"], "dimension": dates[0],
                           "time_grain": "month", "type": "line",
                           "measures": [{"field": nums[0], "aggregation": "sum"}] if nums else [{"aggregation": "count"}]})
        if dim:
            charts.append({"title": "Breakdown", "entity_set": first["name"], "dimension": dim, "type": "column",
                           "measures": [{"field": nums[0], "aggregation": "sum"}] if nums else [{"aggregation": "count"}]})
        if charts:
            raw.append({"type": "analytics", "title": "Analysis", "charts": charts})
    return raw
