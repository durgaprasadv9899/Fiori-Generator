"""
interactive_preview.py — clickable prototype of a DESIGNED app with mock data.

Shown in the studio before any code is generated or deployed: the blueprint
(any floorplan family) is normalised into a compact page spec, every entity set
the pages touch gets deterministic, realistic sample rows (or rows passed in by
the caller), and a self-contained HTML page (inline CSS + vanilla JS, no
network) renders the app: navigation, list pages with search / filters / quick
tabs / sorting / highlights / calculated columns, object pages with edit,
validation, child tables and actions, create dialogs and wizards, dashboards,
boards with drag & drop, calendars and analytics charts.

Nothing is read from or sent to a backend. Custom logic expressions are
re-checked with logic.check (the same sandbox whitelist the generator uses)
before they are evaluated in the preview iframe.

    preview_html(bp, model, rows=12, seed=7, sample_rows=None, workflow=None, height=620) -> str
    mock_rows(model, entity_set, n, seed) -> [row, ...]
"""

from __future__ import annotations

import datetime as _dt
import json
import random
import re
import uuid
import zlib
from typing import Dict, List, Optional

import logic as _logic

# ─────────────────────────────────────────────────────────────────────────────
# Mock data
# ─────────────────────────────────────────────────────────────────────────────
_COMPANIES = ["Acme Industries", "Blue Ridge Foods", "Contoso Retail", "Danube Logistics", "Evergreen Pharma",
              "Fjord Marine", "Granite Works", "Helios Energy", "Ivory Textiles", "Juniper Health",
              "Kestrel Motors", "Lumen Optics", "Meridian Bank", "Northwind Traders", "Orion Chemicals"]
_PRODUCTS = ["Laptop Pro 14", "Office Chair Ergo", "LED Monitor 27", "Steel Bolt M8", "Hydraulic Pump",
             "Copy Paper A4", "Safety Gloves", "Coffee Beans 1kg", "Wireless Mouse", "Pallet Wrap",
             "Industrial Fan", "Toner Cartridge", "Desk Lamp", "Drill Set", "Water Filter"]
_FIRST = ["Anna", "Ben", "Clara", "David", "Elena", "Farid", "Grace", "Hiro", "Irene", "Jonas", "Kavya", "Liam"]
_LAST = ["Schmidt", "Miller", "Rossi", "Kowalski", "Sharma", "Dubois", "Tanaka", "Novak", "Silva", "Berg"]
_CITIES = ["Berlin", "Walldorf", "London", "Paris", "Warsaw", "Hyderabad", "New York", "Chicago", "Madrid", "Vienna"]
_COUNTRY_CODES = ["DE", "US", "IN", "FR", "GB", "PL"]
_COUNTRIES = ["Germany", "USA", "India", "France", "UK", "Poland"]
_CURRENCIES = ["EUR", "USD", "EUR", "GBP", "INR"]
_UNITS = ["EA", "PC", "KG", "L", "BOX"]
_STREETS = ["Main Street", "Park Avenue", "Hauptstrasse", "Rue de Rivoli", "Market Square", "Station Road"]
_WORDS = ["Standard", "Express", "Priority", "Quarterly", "Annual", "Regional", "Strategic", "Bulk",
          "Spare parts", "Maintenance", "Service", "Project"]
_PHRASES = ["Urgent delivery requested", "Customer prefers morning delivery", "Check credit limit",
            "Partial shipment allowed", "Follow-up with procurement", "Contract renewal pending",
            "Quality check passed", "Awaiting confirmation"]
_JOBS = ["Sales Representative", "Buyer", "Account Manager", "Controller", "Warehouse Lead", "Engineer"]

_STATUSY = re.compile(r"(status|stat$|state$|stage|phase|priority|category|type$|kind$|level$|class$)", re.I)


def _rng(seed: int, *parts) -> random.Random:
    return random.Random(zlib.crc32(("|".join(str(p) for p in parts) + f"#{seed}").encode()))


def _trunc(s: str, n) -> str:
    return s[:n] if (n and isinstance(s, str)) else s


def _is_time(p: Dict) -> bool:
    return p.get("type") in ("Edm.Time", "Edm.TimeOfDay", "Edm.Duration")


def _key_value(p: Dict, i: int, es_name: str):
    t, ml = p.get("type", "Edm.String"), p.get("max_length")
    if t == "Edm.Guid":
        return str(uuid.UUID(int=(zlib.crc32(es_name.encode()) << 64) + i + 1))
    if t in ("Edm.Int16", "Edm.Int32", "Edm.Int64", "Edm.Byte", "Edm.SByte", "Edm.Decimal", "Edm.Double"):
        return 10248 + i if t not in ("Edm.Byte", "Edm.SByte") else 1 + i % 120
    if p.get("ui_type") == "date":
        return (_dt.date.today() - _dt.timedelta(days=i)).isoformat()
    if p.get("ui_type") == "datetime":
        return (_dt.datetime.now().replace(microsecond=0) - _dt.timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if ml and ml < 4:
        a = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        v = a[i % 26] + (a[(i // 26) % 26] if ml > 1 else "") + (str(i // 676) if ml > 2 else "")
        return v[:ml]
    base = 1000000 if (ml or 10) >= 7 else 1000 if (ml or 10) >= 4 else 10
    return _trunc(str(base + 1 + i), ml)


def _value(p: Dict, i: int, rng: random.Random, hints: Optional[List[str]], today: _dt.date):
    name, t, ui, ml = p["name"], p.get("type", "Edm.String"), p.get("ui_type", "text"), p.get("max_length")
    ln = name.lower()
    if hints:
        v = hints[(i + rng.randint(0, 1)) % len(hints)] if len(hints) > 1 else hints[0]
        if ui in ("number", "amount"):
            try:
                return float(v) if "." in str(v) else int(v)
            except ValueError:
                pass
        if ui == "boolean":
            return str(v).lower() == "true"
        return v
    if _is_time(p):
        return f"{8 + rng.randint(0, 9):02d}:{rng.choice([0, 15, 30, 45]):02d}:00"
    if ui == "boolean":
        return rng.random() < 0.5
    if ui == "date":
        return (today + _dt.timedelta(days=rng.randint(-50, 40))).isoformat()
    if ui == "datetime":
        d = _dt.datetime.combine(today, _dt.time(8 + rng.randint(0, 9), rng.choice([0, 15, 30, 45])))
        return (d + _dt.timedelta(days=rng.randint(-50, 40))).strftime("%Y-%m-%dT%H:%M:%SZ")
    if t == "Edm.Guid":
        return str(uuid.UUID(int=rng.getrandbits(128)))
    if ui in ("number", "amount"):
        scale = p.get("scale") if p.get("scale") is not None else (2 if t in ("Edm.Decimal", "Edm.Double", "Edm.Single") else 0)
        if re.search(r"percent|pct|rate|discount|probab", ln):
            v = rng.uniform(0, 40)
        elif re.search(r"qty|quantity|units|count|stock|pieces", ln):
            v = rng.randint(1, 240)
        elif re.search(r"amount|price|value|net|gross|total|cost|freight|revenue|sales|budget|tax|limit", ln):
            v = rng.uniform(80, 24000)
        elif re.search(r"weight|volume", ln):
            v = rng.uniform(0.5, 900)
        elif re.search(r"id$|via$|no$|number$", ln) and t in ("Edm.Int16", "Edm.Int32", "Edm.Int64", "Edm.Byte"):
            v = rng.randint(1, 6)
        else:
            v = rng.uniform(1, 500)
        if t in ("Edm.Int16", "Edm.Int32", "Edm.Int64", "Edm.Byte", "Edm.SByte") or not scale:
            return int(round(v))
        return round(v, min(int(scale), 2))
    # strings
    if re.search(r"currency|waers|curr$", ln):
        return _trunc(rng.choice(_CURRENCIES), ml)
    if re.search(r"unit$|uom|meins", ln):
        return _trunc(rng.choice(_UNITS), ml)
    if "country" in ln:
        return _trunc(rng.choice(_COUNTRY_CODES if (ml and ml <= 3) else _COUNTRIES), ml)
    if "city" in ln:
        return _trunc(rng.choice(_CITIES), ml)
    if re.search(r"street|address", ln):
        return _trunc(f"{rng.randint(1, 120)} {rng.choice(_STREETS)}", ml)
    if re.search(r"postal|zip|postcode", ln):
        return _trunc(str(rng.randint(10000, 99999)), ml)
    if "email" in ln or "mail" == ln[-4:]:
        return _trunc(f"{rng.choice(_FIRST).lower()}.{rng.choice(_LAST).lower()}@example.com", ml)
    if re.search(r"phone|fax|mobile|tel", ln):
        return _trunc(f"+49 30 {rng.randint(1000000, 9999999)}", ml)
    if re.search(r"url|web|homepage", ln):
        return _trunc("https://www.example.com", ml)
    if re.search(r"firstname|first_name|givenname", ln):
        return _trunc(rng.choice(_FIRST), ml)
    if re.search(r"lastname|last_name|surname|familyname", ln):
        return _trunc(rng.choice(_LAST), ml)
    if re.search(r"contact|person|employee.*name|createdby|owner|responsible|requester|approver", ln) and (ml or 40) > 8:
        return _trunc(f"{rng.choice(_FIRST)} {rng.choice(_LAST)}", ml)
    if re.search(r"product.*(name|desc)|material.*(name|desc)|itemname|productname", ln):
        return _trunc(rng.choice(_PRODUCTS), ml)
    if re.search(r"(company|customer|supplier|vendor|ship|party|partner|account).*name|^name$|name$", ln) and (ml or 40) > 8:
        return _trunc(rng.choice(_COMPANIES), ml)
    if re.search(r"title$|position", ln) and (ml or 40) > 8:
        return _trunc(rng.choice(_JOBS), ml)
    if re.search(r"desc|text|note|comment|remark|reason$", ln) and (ml or 60) > 12:
        return _trunc(rng.choice(_PHRASES), ml)
    if _STATUSY.search(name):
        if ml and ml <= 4:
            pool = ["OR", "CR", "DR"] if re.search(r"type$|kind$", ln) and ml >= 2 else ["A", "B", "C"]
            return _trunc(rng.choice(pool), ml)
        if "priority" in ln:
            return rng.choice(["High", "Medium", "Low"])
        return _trunc(rng.choice(["Open", "In Process", "Completed"] if "stat" in ln else _WORDS[:4]), ml)
    if re.search(r"org|plant|company.?code|division|channel|werks|bukrs|vkorg", ln) and (not ml or ml <= 6):
        return _trunc(rng.choice(["1010", "1710", "2010"]), ml)
    if re.search(r"(id|number|no|nr|party|customer|supplier|vendor|material|product|employee|order|document)$", ln):
        if ml and ml <= 3:
            return _trunc(rng.choice(["A1", "B2", "C3"]), ml)
        return _trunc(str(17100000 + rng.randint(1, 6) * 7), ml)
    if ml and ml <= 4:
        return _trunc(rng.choice(["A", "B", "C"]) + (str(rng.randint(1, 3)) if ml > 1 else ""), ml)
    return _trunc(f"{rng.choice(_WORDS)} {rng.randint(1, 99)}", ml)


def _hints_for(bp: Dict) -> Dict[str, Dict[str, List[str]]]:
    """Code values the design itself mentions, per entity set + field (status states,
    board lanes, action guards, table views, set_field values)."""
    out: Dict[str, Dict[str, List[str]]] = {}

    def add(es, f, vals):
        if not (es and f):
            return
        cur = out.setdefault(es, {}).setdefault(f, [])
        for v in vals or []:
            if v is not None and str(v) not in cur:
                cur.append(str(v))

    def from_sub(es, sub):
        st = (sub.get("main") or {}).get("status") or {}
        add(es, st.get("field"), list((st.get("states") or {}).keys()))
        for a in sub.get("actions") or []:
            ew = a.get("enabled_when") or {}
            vals = list(ew.get("values") or [])
            add(es, ew.get("field"), vals)
            if a.get("kind") == "set_field":
                add(es, a.get("field"), [a.get("value")])

    try:
        if bp.get("pages"):
            for p in bp["pages"]:
                if not isinstance(p, dict):
                    continue
                if p.get("type") == "list" and p.get("sub"):
                    from_sub(p["entity_set"], p["sub"])
                elif p.get("type") == "kanban":
                    add(p.get("entity_set"), p.get("lane_field"), [l.get("value") for l in p.get("lanes") or []])
        if bp.get("main"):
            from_sub(bp["main"].get("entity_set"), bp)
            for v in ((bp.get("fe") or {}).get("table_views") or []):
                for f in v.get("filters") or []:
                    add(bp["main"].get("entity_set"), f.get("field"), f.get("values"))
    except Exception:  # noqa: BLE001 — hints are best effort
        pass
    for fields in out.values():          # blank codes only when nothing else is known
        for f, vals in fields.items():
            if len(vals) > 1 and "" in vals:
                vals.remove("")
    # a single guard value (e.g. approval status 'A') would make every row eligible:
    # mix in neighbouring codes so the preview shows enabled AND disabled actions
    for es, fields in out.items():
        for f, vals in fields.items():
            if len(vals) == 1 and len(vals[0]) <= 2:
                for extra in ("A", "B", "C"):
                    if extra not in vals and len(vals) < 3:
                        vals.append(extra)
    return out


def _set(model: Dict, name: str) -> Optional[Dict]:
    return next((e for e in model.get("entity_sets", []) if e["name"] == name), None)


def mock_rows(model: Dict, entity_set: str, n: int = 12, seed: int = 7,
              hints: Optional[Dict[str, List[str]]] = None, start: int = 0) -> List[Dict]:
    """Deterministic sample rows for an entity set (unique keys, typed values)."""
    es = _set(model, entity_set)
    if not es:
        return []
    today = _dt.date.today()
    hints = hints or {}
    out = []
    for i in range(n):
        rng = _rng(seed, entity_set, i)
        row = {}
        for p in es["properties"]:
            if p["name"] in es["keys"]:
                row[p["name"]] = _key_value(p, start + i, entity_set)
            else:
                row[p["name"]] = _value(p, i, rng, hints.get(p["name"]), today)
        out.append(row)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Blueprint -> preview spec
# ─────────────────────────────────────────────────────────────────────────────
def _safe(expr, fields, script=False):
    try:
        _logic.check(expr, set(fields), script=script, operations=None)
        return True
    except Exception:  # noqa: BLE001
        return False


def _list_spec(pid: str, title: str, es: Dict, sub: Dict, *, has_detail: bool, layout: str, quick_tabs: bool,
               create: str, wizard_steps: List[Dict], views: List[Dict], notes: List[str], show_filters=True,
               kpis=None, chart=None) -> Dict:
    fields = {p["name"] for p in es["properties"]}
    m = sub.get("main") or {}
    lg = sub.get("logic") or {}
    computed = []
    for c in lg.get("computed") or []:
        if _safe(c.get("expression"), fields):
            computed.append({"id": c.get("id") or f"c{len(computed) + 1}", "label": c.get("label") or "Calculated",
                             "expr": c["expression"], "type": c.get("type") or "text",
                             "decimals": c.get("decimals", 2), "after": c.get("after") or ""})
        else:
            notes.append(f"[{title}] calculated column '{c.get('label')}' not previewed (expression not allowed).")
    rs = (lg.get("row_state") or {}).get("expression") if isinstance(lg.get("row_state"), dict) else lg.get("row_state")
    if rs and not _safe(rs, fields):
        notes.append(f"[{title}] row highlight not previewed (expression not allowed).")
        rs = None
    vals = [{"expr": v["expression"], "message": v.get("message") or "Invalid input.", "fields": v.get("fields") or []}
            for v in lg.get("validations") or [] if _safe(v.get("expression"), fields)]
    dflt = [{"field": d["field"], "expr": d["expression"]} for d in lg.get("defaults") or []
            if d.get("field") in fields and _safe(d.get("expression"), fields)]
    der = [{"field": d["field"], "expr": d["expression"]} for d in lg.get("derived") or []
           if d.get("field") in fields and _safe(d.get("expression"), fields)]
    acts = []
    for a in sub.get("actions") or []:
        x = {"id": a.get("id") or f"a{len(acts)}", "label": a.get("label") or "Action", "kind": a.get("kind"),
             "scope": a.get("scope") or "object", "confirm": bool(a.get("confirm")),
             "ew": a.get("enabled_when") or {}}
        if a.get("kind") == "operation":
            x["operation"] = a.get("operation")
        elif a.get("kind") == "set_field":
            x.update(field=a.get("field"), value=a.get("value"))
        elif a.get("kind") == "script":
            if not _safe(a.get("script"), fields, script=True):
                notes.append(f"[{title}] script action '{x['label']}' not previewed (script not allowed).")
                continue
            x["script"] = a["script"]
        else:
            continue
        acts.append(x)
    op = sub.get("object_page") or {}
    form = sub.get("form") or {}
    groups = form.get("create_groups") or ([{"title": "General", "fields": form.get("create_fields") or []}]
                                           if form.get("create_fields") else [])
    return {
        "id": pid, "type": "list", "title": title, "set": es["name"],
        "columns": [c["field"] if isinstance(c, dict) else c for c in m.get("columns") or []],
        "filters": [f["field"] if isinstance(f, dict) else f for f in m.get("filters") or []] if show_filters else [],
        "search": bool(m.get("search", True)), "sort": m.get("sort") or {},
        "status": m.get("status") or {"field": "", "states": {}},
        "title_field": m.get("title_field") or "", "desc_field": m.get("description_field") or "",
        "quick_tabs": bool(quick_tabs), "views": views or [], "layout": layout or "table",
        "detail": ({"header_fields": op.get("header_fields") or [],
                    "sections": op.get("sections") or [],
                    "tables": [{"nav": t["navigation"], "set": t["entity_set"], "title": t.get("title") or t["navigation"],
                                "columns": t.get("columns") or []} for t in op.get("tables") or []]}
                   if (has_detail or layout == "split") else None),
        "caps": sub.get("capabilities") or {},
        "form": {"groups": groups, "required": form.get("required") or [], "readonly": form.get("readonly") or []},
        "value_helps": [{"field": v["field"], "set": v["entity_set"], "key": v["key"], "text": v.get("text") or ""}
                        for v in sub.get("value_helps") or [] if v.get("field")],
        "actions": acts, "computed": computed, "row_state": rs, "validations": vals,
        "defaults": dflt, "derived": der, "create": create or "dialog", "wizard_steps": wizard_steps or [],
        "kpis": kpis or [], "chart": chart,
    }


def _spec_pages(bp: Dict, model: Dict, notes: List[str]) -> List[Dict]:
    sets = {e["name"]: e for e in model.get("entity_sets", [])}
    out: List[Dict] = []
    if bp.get("pages"):
        for p in bp["pages"]:
            if not isinstance(p, dict):
                continue
            try:
                t = p.get("type")
                if t == "list":
                    es = sets.get(p.get("entity_set"))
                    if not es:
                        continue
                    out.append(_list_spec(p["id"], p.get("title") or es["name"], es, p.get("sub") or {},
                                          has_detail=bool(p.get("has_detail") or p.get("detail_id")),
                                          layout=p.get("layout") or "table", quick_tabs=p.get("quick_tabs"),
                                          create=p.get("create"), wizard_steps=p.get("wizard_steps") or [],
                                          views=[], notes=notes))
                    out[-1]["icon"] = p.get("icon")
                elif t in ("dashboard", "kanban", "calendar", "analytics"):
                    x = {k: v for k, v in p.items() if k not in ("sub",)}
                    if t == "analytics":
                        for c in x.get("charts") or []:
                            es = sets.get(c.get("entity_set")) or {"properties": []}
                            ok = []
                            for m in c.get("measures") or []:
                                if m.get("expression") and not _safe(m["expression"], {q["name"] for q in es["properties"]}):
                                    notes.append(f"[{p.get('title')}] calculated measure '{m.get('label')}' not previewed.")
                                    continue
                                ok.append(m)
                            c["measures"] = ok or [{"field": "", "aggregation": "count", "label": "Count"}]
                    out.append(x)
                else:
                    out.append({"id": p.get("id") or f"p{len(out)}", "type": "unknown",
                                "title": p.get("title") or str(t), "kind": str(t)})
            except Exception as e:  # noqa: BLE001 — one odd page never breaks the preview
                out.append({"id": f"err{len(out)}", "type": "unknown", "title": str(p.get("title") or "Page"),
                            "kind": f"not previewable ({e.__class__.__name__})"})
        return out
    # single-entity floorplans (Fiori Elements, OVP, freestyle list/detail)
    main = bp.get("main") or {}
    es = sets.get(main.get("entity_set"))
    if not es:
        return out
    fp = bp.get("floorplan") or ""
    sub = {k: bp.get(k) for k in ("main", "object_page", "capabilities", "form", "actions", "value_helps", "logic")}
    an = bp.get("analytics") or {}
    chart = None
    if fp == "AnalyticalListPage" and an.get("dimensions"):
        m0 = (an.get("measures") or [{}])[0]
        chart = {"dimension": an["dimensions"][0], "measure": m0.get("field") or "",
                 "aggregation": m0.get("aggregation") or "count", "type": an.get("chart") or "column"}
    kpis = an.get("kpis") if fp in ("FreestyleWorklist", "AnalyticalListPage") else []
    lst = _list_spec("list", (bp.get("app") or {}).get("title") or es["name"], es, sub, has_detail=True,
                     layout="split" if fp == "FreestyleMasterDetail" else "table", quick_tabs=False,
                     create="dialog", wizard_steps=[], views=(bp.get("fe") or {}).get("table_views") or [],
                     notes=notes, show_filters=fp != "Worklist", kpis=kpis, chart=chart)
    if fp == "OverviewPage" and bp.get("cards"):
        dash = {"id": "overview", "type": "dashboard", "title": "Overview", "kpis": [], "charts": [], "lists": []}
        for c in bp["cards"]:
            if c.get("type") == "analytical" and c.get("dimension"):
                dash["charts"].append({"title": c.get("title"), "entity_set": c["entity_set"],
                                       "dimension": c["dimension"], "measure": c.get("measure") or "",
                                       "aggregation": "sum" if c.get("measure") else "count", "type": "column",
                                       "route": "list" if c["entity_set"] == es["name"] else ""})
            else:
                dash["lists"].append({"title": c.get("title"), "entity_set": c["entity_set"],
                                      "fields": c.get("fields") or [], "sort": {},
                                      "route": "list" if c["entity_set"] == es["name"] else ""})
        out.append(dash)
    out.append(lst)
    return out


def _set_meta(es: Dict) -> Dict:
    props = {}
    names = [p["name"] for p in es["properties"]]
    cur = next((n for n in names if re.search(r"currency$|waers$", n, re.I)), "")
    for p in es["properties"]:
        ui = "time" if _is_time(p) else p.get("ui_type", "text")
        unit = p.get("unit_property") or ""
        if not unit and ui == "amount" and cur and cur != p["name"]:
            unit = cur
        props[p["name"]] = {"label": p.get("label") or p["name"], "ui": ui, "type": p.get("type", ""),
                            "scale": p.get("scale"), "ml": p.get("max_length"),
                            "nullable": p.get("nullable", True), "upd": p.get("updatable", True),
                            "cre": p.get("creatable", True), "unit": unit if unit in names else ""}
    return {"label": es.get("label") or es["name"], "keys": list(es["keys"]), "order": names, "props": props,
            "navs": {n["name"]: n.get("target_type", "") for n in es.get("navigations") or []}}


def _jsonable(v):
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def _used_fields(pages: List[Dict], links: List[Dict], workflow: Dict) -> Dict[str, set]:
    """Fields per entity set that the previewed pages display, filter, compute or act on."""
    out: Dict[str, set] = {}

    def add(es, *fields):
        if es:
            out.setdefault(es, set()).update(f for f in fields if isinstance(f, str) and f)

    def exprs(es, *srcs):
        for s in srcs:
            if isinstance(s, str):
                add(es, *re.findall(r"\brow\.([A-Za-z_$][\w$]*)", s))
                add(es, *re.findall(r"api\.set\(\s*['\"]([^'\"]+)", s))
    for p in pages:
        t = p.get("type")
        if t == "list":
            n = p["set"]
            add(n, *p["columns"], *p["filters"], p["title_field"], p["desc_field"], p["status"].get("field"))
            d = p.get("detail") or {}
            add(n, *(d.get("header_fields") or []))
            for s in d.get("sections") or []:
                add(n, *(s.get("fields") or []))
            for tb in d.get("tables") or []:
                add(tb["set"], *(tb.get("columns") or []))
            for g in p["form"]["groups"]:
                add(n, *g.get("fields", []))
            for w in p.get("wizard_steps") or []:
                add(n, *(w.get("fields") or []))
            add(n, *p["form"]["required"])
            for v in p.get("value_helps") or []:
                add(n, v["field"])
                add(v["set"], v["key"], v.get("text"))
            for a in p["actions"]:
                add(n, (a.get("ew") or {}).get("field"), a.get("field"))
                exprs(n, a.get("script"))
            for c in p["computed"]:
                exprs(n, c["expr"])
            exprs(n, p.get("row_state"), *[v["expr"] for v in p["validations"]],
                  *[d["expr"] for d in p["defaults"] + p["derived"]])
            add(n, *[d["field"] for d in p["defaults"] + p["derived"]])
            for k in p.get("kpis") or []:
                add(n, k.get("field"))
            if p.get("chart"):
                add(n, p["chart"].get("dimension"), p["chart"].get("measure"))
            for v in p.get("views") or []:
                add(n, *[f.get("field") for f in v.get("filters") or []])
        elif t == "dashboard":
            for k in p.get("kpis") or []:
                add(k.get("entity_set"), k.get("field"))
            for c in p.get("charts") or []:
                add(c.get("entity_set"), c.get("dimension"), c.get("measure"))
            for l in p.get("lists") or []:
                add(l.get("entity_set"), *(l.get("fields") or []), (l.get("sort") or {}).get("field"))
        elif t == "kanban":
            add(p.get("entity_set"), p.get("lane_field"), p.get("card_title"), p.get("card_subtitle"),
                *(p.get("card_fields") or []))
        elif t == "calendar":
            add(p.get("entity_set"), p.get("start_field"), p.get("end_field"), p.get("title_field"),
                p.get("text_field"), p.get("color_field"))
        elif t == "analytics":
            for c in p.get("charts") or []:
                add(c.get("entity_set"), c.get("dimension"), c.get("series"))
                for m in c.get("measures") or []:
                    add(c.get("entity_set"), m.get("field"))
                    exprs(c.get("entity_set"), m.get("expression"))
    for l in links:
        add(l["from"], l["field"])
        add(l["to"], l["to_field"])
    for p in pages:
        if p.get("type") == "list":
            for a in p["actions"]:
                w = workflow.get(a.get("operation") or "")
                if w:
                    add(p["set"], w.get("field"))
    return out


def build_spec(bp: Dict, model: Dict, *, rows: int = 12, seed: int = 7,
               sample_rows: Optional[Dict[str, List[Dict]]] = None,
               workflow: Optional[Dict[str, Dict]] = None) -> Dict:
    notes: List[str] = []
    sets = {e["name"]: e for e in model.get("entity_sets", [])}
    pages = _spec_pages(bp, model, notes)
    hints = _hints_for(bp)
    for op, w in (workflow or {}).items():
        for p in pages:
            if p.get("type") == "list" and any(a.get("operation") == op for a in p["actions"]) and w.get("field"):
                cur = hints.setdefault(p["set"], {}).setdefault(w["field"], [])
                for v in list(w.get("from") or []) + [w.get("to")]:
                    if v is not None and str(v) not in cur:
                        cur.append(str(v))
    # entity sets the preview needs: pages, child tables, value helps, links
    need: List[str] = []

    def want(name):
        if name in sets and name not in need:
            need.append(name)
    for p in pages:
        want(p.get("set") or p.get("entity_set"))
        for c in p.get("charts") or []:
            want(c.get("entity_set"))
        for k in p.get("kpis") or []:
            want(k.get("entity_set"))
        for l in p.get("lists") or []:
            want(l.get("entity_set"))
        for v in p.get("value_helps") or []:
            want(v["set"])
    links = [l for l in bp.get("links") or [] if l.get("from") in sets and l.get("to") in sets]
    for l in links:
        want(l["from"])
        want(l["to"])
    child_sets = []
    for p in pages:
        for t in ((p.get("detail") or {}).get("tables") or []):
            if t["set"] in sets and t["set"] not in child_sets:
                child_sets.append(t["set"])

    data: Dict[str, List[Dict]] = {}
    uid = [0]

    def tag(rws):
        for r in rws:
            uid[0] += 1
            r["__id"] = uid[0]
        return rws
    real = set()
    for name in need:
        given = (sample_rows or {}).get(name)
        if given:
            data[name] = tag([{k: _jsonable(v) for k, v in r.items() if not str(k).startswith("__")}
                              for r in given[:max(rows * 3, 30)] if isinstance(r, dict)])
            real.add(name)
        else:
            data[name] = tag(mock_rows(model, name, rows, seed, hints.get(name)))

    # consistent references: link fields / value-help fields use the target's key values
    def adopt(src, field, tgt, tfield):
        if src in real or not data.get(tgt) or not data.get(src):
            return
        vals = [r.get(tfield) for r in data[tgt] if r.get(tfield) not in (None, "")]
        if vals:
            for i, r in enumerate(data[src]):
                if field not in sets[src]["keys"]:
                    r[field] = vals[i % len(vals)]
    for l in links:
        adopt(l["from"], l["field"], l["to"], l["to_field"])
    for p in pages:
        for v in p.get("value_helps") or []:
            adopt(p["set"], v["field"], v["set"], v["key"])

    # child rows per parent row and navigation
    children: Dict[str, Dict[str, Dict[str, List[Dict]]]] = {}
    for p in pages:
        if p.get("type") != "list" or not p.get("detail"):
            continue
        pes = sets[p["set"]]
        for t in p["detail"]["tables"]:
            ces = sets.get(t["set"])
            if not ces:
                continue
            bucket = children.setdefault(p["set"], {}).setdefault(t["nav"], {})
            if bucket:
                continue
            given = (sample_rows or {}).get(t["set"])
            # (child field, parent key) pairs: a CAP-style foreign key "<back-nav>_<key>" (e.g.
            # parent_ID) when the child has one, else fields named like the parent key (SAP style)
            cprops = {q["name"] for q in ces["properties"]}
            backs = [nv["name"] for nv in ces.get("navigations", [])
                     if nv.get("target_type") == pes.get("entity_type") and not nv.get("to_many")]
            pairs = [(f"{b}_{k}", k) for b in backs for k in pes["keys"] if f"{b}_{k}" in cprops]
            if not pairs:
                pairs = [(k, k) for k in pes["keys"] if k in cprops]
            for pr in data.get(p["set"], []):
                if given:
                    rs = [dict(r) for r in given if pairs and all(str(r.get(c)) == str(pr.get(k)) for c, k in pairs)]
                else:
                    n = 1 + _rng(seed, t["set"], pr["__id"]).randint(1, 3)
                    rs = mock_rows(model, t["set"], n, seed + pr["__id"], hints.get(t["set"]))
                    shared = [c for c, _ in pairs]
                    for j, r in enumerate(rs):
                        for c, k in pairs:
                            r[c] = pr.get(k)
                        for k in ces["keys"]:
                            if k not in shared:
                                q = next(x for x in ces["properties"] if x["name"] == k)
                                if q.get("ui_type") in ("number", "amount") or q.get("type", "").startswith("Edm.Int"):
                                    r[k] = (j + 1) * 10
                                elif q.get("type") != "Edm.Guid":
                                    r[k] = _trunc(str((j + 1) * 10).zfill(min(q.get("max_length") or 6, 6)),
                                                  q.get("max_length"))
                rs = [{k: _jsonable(v) for k, v in r.items()} for r in rs]
                bucket[str(pr["__id"])] = tag(rs)

    # keep only the fields the design shows or computes with (SAP sets have 100+ fields)
    used = _used_fields(pages, links, workflow or {})
    for n in set(need) | set(child_sets):
        es = sets[n]
        keep = [p["name"] for p in es["properties"]
                if p["name"] in used.get(n, set()) or p["name"] in es["keys"]]
        if len(keep) <= len(es["keys"]):
            keep = [p["name"] for p in es["properties"]][:16]
        units = {p.get("unit_property") for p in es["properties"] if p["name"] in keep}
        cur = next((p["name"] for p in es["properties"] if re.search(r"currency$|waers$", p["name"], re.I)), "")
        if cur and any(p["ui_type"] == "amount" for p in es["properties"] if p["name"] in keep):
            units.add(cur)
        keep_set = set(keep) | {u for u in units if u}
        sets[n] = dict(es, properties=[p for p in es["properties"] if p["name"] in keep_set])
        for r in data.get(n, []):
            for k in [k for k in r if k != "__id" and k not in keep_set]:
                del r[k]
    nav_set = {(p["set"], t["nav"]): t["set"] for p in pages if p.get("type") == "list"
               for t in ((p.get("detail") or {}).get("tables") or [])}
    for pset, navs in children.items():
        for nav, per_parent in navs.items():
            allowed = {q["name"] for q in sets.get(nav_set.get((pset, nav)), {}).get("properties", [])}
            for rs in per_parent.values():
                for r in rs:
                    for k in [k for k in r if k != "__id" and allowed and k not in allowed]:
                        del r[k]
    meta = {n: _set_meta(sets[n]) for n in set(need) | set(child_sets)}
    # code lists (selects in filters / forms): design hints, else low-cardinality generated values
    for n, m in meta.items():
        for f, pm in m["props"].items():
            opts = list((hints.get(n) or {}).get(f) or [])
            if not opts and pm["ui"] == "text" and f not in m["keys"] and n in data:
                distinct = sorted({str(r.get(f)) for r in data[n] if r.get(f) not in (None, "")})
                if 1 < len(distinct) <= 6 and len(data[n]) >= 6:
                    opts = distinct
            if opts:
                pm["opts"] = opts
    # status texts for quick tabs / chips: the design's state map
    return {"app": {"title": (bp.get("app") or {}).get("title") or "App", "floorplan": bp.get("floorplan") or ""},
            "pages": pages, "sets": meta, "data": data, "children": children, "links": links,
            "workflow": workflow or {}, "real": sorted(real), "notes": notes,
            "today": _dt.date.today().isoformat()}


def preview_html(bp: Dict, model: Dict, *, rows: int = 12, seed: int = 7,
                 sample_rows: Optional[Dict[str, List[Dict]]] = None,
                 workflow: Optional[Dict[str, Dict]] = None, height: int = 620) -> str:
    try:
        spec = build_spec(bp, model, rows=rows, seed=seed, sample_rows=sample_rows, workflow=workflow)
    except Exception as e:  # noqa: BLE001
        spec = {"app": {"title": (bp.get("app") or {}).get("title") or "App", "floorplan": bp.get("floorplan") or ""},
                "pages": [{"id": "err", "type": "unknown", "title": "Preview", "kind": f"preview failed: {e}"}],
                "sets": {}, "data": {}, "children": {}, "links": [], "workflow": {}, "real": [], "notes": [],
                "today": _dt.date.today().isoformat()}
    payload = json.dumps(spec, ensure_ascii=False, default=str).replace("</", "<\\/").replace("<!--", "<\\!--")
    title = (spec["app"]["title"] or "App").replace("&", "&amp;").replace("<", "&lt;")
    return ("<!doctype html><html><head><meta charset=\"utf-8\"><title>" + title + "</title><style>"
            + _CSS.replace("__H__", str(int(height))) + "</style></head><body><div id=\"app\"></div>"
            "<div id=\"modal\" class=\"mbg\" style=\"display:none\"></div><div id=\"toast\"></div>"
            "<script>var SPEC = " + payload + ";</script><script>" + _JS + "</script></body></html>")


# ─────────────────────────────────────────────────────────────────────────────
# Front end
# ─────────────────────────────────────────────────────────────────────────────
_CSS = r"""
*{box-sizing:border-box}html,body{margin:0;height:100%}
body{font-family:'72','72full',-apple-system,'Segoe UI',Roboto,Arial,sans-serif;font-size:13px;color:#1d2d3e;background:#f5f6f7}
#app{display:flex;flex-direction:column;height:__H__px;max-height:100vh}
.shell{height:44px;background:#fff;border-bottom:1px solid #e5e5e5;display:flex;align-items:center;gap:10px;padding:0 14px;flex:0 0 auto}
.shell b{font-size:14px;color:#1d2d3e}.shell .logo{width:22px;height:22px;border-radius:6px;background:#0070f2;color:#fff;font-weight:700;display:flex;align-items:center;justify-content:center;font-size:12px}
.shell .sp{margin-left:auto;display:flex;gap:8px;align-items:center}
.chip{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;font-weight:600;background:#eef1f4;color:#556b82;white-space:nowrap}
.chip.mock{background:#fff3b8;color:#7a5800}.chip.real{background:#e2f5e6;color:#256f3a}
.S-Success{background:#e2f5e6;color:#256f3a}.S-Warning{background:#fff3b8;color:#7a5800}.S-Error{background:#ffeaf4;color:#aa0808}.S-Information{background:#e1f4ff;color:#0057d2}
.banner{background:#fff8d6;border-bottom:1px solid #f0dc8a;color:#5c4a00;padding:5px 14px;font-size:12px;display:flex;gap:10px;align-items:center;flex:0 0 auto}
.banner a{color:#0057d2;cursor:pointer;margin-left:auto;white-space:nowrap}
.errbar{background:#ffeaf4;color:#aa0808;padding:4px 14px;font-size:12px;display:none}
.body{display:flex;flex:1 1 auto;min-height:0}
.nav{width:190px;background:#fff;border-right:1px solid #e5e5e5;padding:6px 0;flex:0 0 auto;overflow:auto}
.nav div{padding:8px 14px;cursor:pointer;color:#32363a;border-left:3px solid transparent;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.nav div:hover{background:#f0f6fd}.nav div.on{background:#eaf3fc;border-left-color:#0070f2;color:#0070f2;font-weight:600}
.main{flex:1 1 auto;overflow:auto;min-width:0}
.ph{background:#fff;padding:10px 18px;border-bottom:1px solid #e5e5e5}
.ph h2{margin:0;font-size:18px;font-weight:700}.ph .sub{color:#556b82;font-size:12px;margin-top:2px}
.crumb{font-size:12px;color:#0070f2;margin-bottom:4px}.crumb span{cursor:pointer}.crumb span:hover{text-decoration:underline}
.fb{display:flex;flex-wrap:wrap;gap:10px;margin-top:8px;align-items:flex-end}
.fld{display:flex;flex-direction:column;gap:3px}.fld>span{font-size:11px;color:#556b82}
input,select,textarea{font:inherit;font-size:12px;height:28px;border:1px solid #bcc3ca;border-radius:6px;padding:0 8px;background:#fff;color:#1d2d3e;min-width:0}
input[type=checkbox]{height:auto}
input:focus,select:focus{outline:2px solid #0070f2;outline-offset:-1px}
input.bad,select.bad{border-color:#aa0808;background:#fff6f8}
.fb input,.fb select{width:150px}
.btn{display:inline-flex;align-items:center;gap:4px;height:28px;padding:0 11px;border-radius:8px;border:1px solid #bcc3ca;color:#0064d9;background:#fff;font-size:12px;font-weight:600;white-space:nowrap;cursor:pointer}
.btn:hover{background:#eaf3fc}.btn.e{background:#0070f2;border-color:#0070f2;color:#fff}.btn.e:hover{background:#0064d9}
.btn.n{color:#aa0808}.btn.g{border-color:transparent}.btn[disabled]{opacity:.45;cursor:not-allowed;background:#fff}
.ct{margin:12px 14px;background:#fff;border-radius:12px;box-shadow:0 0 0 1px #e5e5e5;overflow:hidden}
.tb{display:flex;align-items:center;gap:8px;padding:9px 12px;border-bottom:1px solid #eee;flex-wrap:wrap}
.tb .t{font-weight:700;margin-right:auto}
.srch{width:170px;border-radius:14px}
table{width:100%;border-collapse:collapse}
th{font-size:11px;text-align:left;color:#556b82;font-weight:600;padding:7px 10px;border-bottom:1px solid #eee;white-space:nowrap;cursor:pointer;user-select:none;background:#fafbfc}
th.f{color:#7a4fd6}th .ar{color:#0070f2}
td{padding:7px 10px;border-bottom:1px solid #f2f2f2;white-space:nowrap;max-width:220px;overflow:hidden;text-overflow:ellipsis}
td.num,th.num{text-align:right}
tr.row{cursor:pointer}tr.row:hover td{background:#f5faff}tr.sel td{background:#eaf3fc}
tr.R-Error td:first-child{box-shadow:inset 4px 0 #aa0808}tr.R-Warning td:first-child{box-shadow:inset 4px 0 #e76500}
tr.R-Success td:first-child{box-shadow:inset 4px 0 #256f3a}tr.R-Information td:first-child{box-shadow:inset 4px 0 #0070f2}
.tabs{display:flex;gap:16px;padding:8px 12px 0;border-bottom:1px solid #eee;flex-wrap:wrap}
.tabs span{padding-bottom:7px;color:#556b82;font-weight:600;cursor:pointer}.tabs span.on{color:#0070f2;box-shadow:inset 0 -2px #0070f2}
.tabs em{font-style:normal;background:#eef1f4;border-radius:8px;padding:0 6px;margin-left:4px;font-size:11px}
.empty{padding:26px;text-align:center;color:#556b82}
.kpis{display:flex;flex-wrap:wrap;gap:10px;padding:12px 14px 0}
.kpi{min-width:150px;background:#fff;border-radius:12px;box-shadow:0 0 0 1px #e5e5e5;padding:10px 14px;cursor:pointer}
.kpi span{display:block;font-size:12px;font-weight:600;color:#556b82}.kpi b{display:block;font-size:24px;font-weight:400;color:#0070f2;margin-top:6px}
.kpi:hover{box-shadow:0 0 0 2px #0070f2}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px;padding:12px 14px}
.card{background:#fff;border-radius:12px;box-shadow:0 0 0 1px #e5e5e5;padding:10px 12px;min-width:0}
.card h4{margin:0 0 6px;font-size:13px}.card small{color:#556b82;display:block;margin-bottom:4px}
svg text{font-family:inherit}.bar{cursor:pointer}.bar:hover{opacity:.8}
.board{display:flex;gap:10px;padding:12px 14px;overflow:auto;align-items:flex-start}
.lane{width:220px;flex:0 0 auto;background:#eef1f5;border-radius:12px;padding:8px;min-height:120px}
.lane.over{background:#dcebfb;box-shadow:inset 0 0 0 2px #0070f2}
.lane h5{margin:2px 4px 8px;font-size:12px;display:flex;justify-content:space-between}
.kc{background:#fff;border-radius:8px;padding:8px 10px;margin-bottom:8px;box-shadow:0 1px 2px rgba(0,0,0,.1);cursor:pointer}
.kc[draggable=true]{cursor:grab}.kc b{display:block;font-size:12px}.kc span{display:block;font-size:11px;color:#556b82;margin-top:2px}
.calh{display:flex;gap:8px;align-items:center;margin:10px 14px 0}.calh b{min-width:130px;text-align:center}
.cal{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:1px;background:#e5e5e5;margin:10px 14px;border-radius:10px;overflow:hidden}
.cal>div{background:#fff;min-height:70px;padding:4px 5px;font-size:11px;color:#556b82;min-width:0}
.cal>div.h{min-height:auto;font-weight:600;background:#f7f7f7;text-align:center}.cal>div.o{background:#fafafa;color:#bbb}
.cal>div.td{box-shadow:inset 0 0 0 2px #0070f2}
.ap{display:block;margin-top:2px;border-radius:4px;padding:1px 4px;font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:pointer;color:#1d2d3e}
.split{display:flex;margin:12px 14px;background:#fff;border-radius:12px;box-shadow:0 0 0 1px #e5e5e5;min-height:420px;overflow:hidden}
.split .l{width:270px;border-right:1px solid #eee;flex:0 0 auto;overflow:auto;max-height:520px}.split .r{flex:1;min-width:0;overflow:auto;max-height:520px}
.li{padding:9px 12px;border-bottom:1px solid #f2f2f2;cursor:pointer}.li:hover{background:#f5faff}.li.on{background:#eaf3fc;box-shadow:inset 3px 0 #0070f2}
.li b{display:block;font-size:12px}.li span{font-size:11px;color:#556b82}
.oh{background:#fff;padding:12px 18px;border-bottom:1px solid #e5e5e5}
.oh .top{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.oh h2{margin:0;font-size:19px}
.acts{display:flex;gap:6px;flex-wrap:wrap;margin-left:auto}
.hdr{display:flex;gap:26px;margin-top:10px;flex-wrap:wrap}.hdr span{display:block;font-size:11px;color:#556b82}.hdr b{font-size:14px;font-weight:600}
.sec{margin:12px 14px;background:#fff;border-radius:12px;box-shadow:0 0 0 1px #e5e5e5;padding:12px 16px}
.sec h4{margin:0 0 10px;font-size:14px}
.form{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:10px 18px}
.form .fv span{display:block;font-size:11px;color:#556b82;margin-bottom:2px}.form .fv div{min-height:18px;word-break:break-word}
.form .fv input,.form .fv select{width:100%}.req:after{content:" *";color:#aa0808}
.err{color:#aa0808;font-size:11px;margin-top:2px}
.lnk{color:#0064d9;cursor:pointer}.lnk:hover{text-decoration:underline}
.msgs{margin:10px 14px 0;padding:8px 12px;border-radius:8px;background:#ffeaf4;color:#aa0808;font-size:12px}
.mbg{position:fixed;inset:0;background:rgba(0,0,0,.32);display:flex;align-items:center;justify-content:center;z-index:20}
.dlg{background:#fff;border-radius:14px;box-shadow:0 10px 30px rgba(0,0,0,.25);width:min(720px,94vw);max-height:88vh;display:flex;flex-direction:column}
.dlg header{padding:12px 16px;font-weight:700;font-size:15px;border-bottom:1px solid #eee}.dlg .c{padding:12px 16px;overflow:auto}
.dlg footer{padding:10px 16px;border-top:1px solid #eee;display:flex;gap:8px;justify-content:flex-end}
.wiz{display:flex;gap:6px;margin:12px 14px}.wiz span{flex:1;text-align:center;padding:7px;background:#fff;border-radius:8px;box-shadow:0 0 0 1px #e5e5e5;font-size:12px}
.wiz span.on{box-shadow:0 0 0 2px #0070f2;color:#0070f2;font-weight:600}.wiz span.dn{color:#256f3a}
#toast{position:fixed;bottom:16px;left:50%;transform:translateX(-50%);background:#1d2d3e;color:#fff;padding:8px 14px;border-radius:8px;font-size:12px;opacity:0;transition:opacity .2s;pointer-events:none;z-index:30;max-width:80%}
#toast.on{opacity:.95}
.ph2{margin:14px;padding:24px;background:#fff;border-radius:12px;box-shadow:0 0 0 1px #e5e5e5;color:#556b82}
.notes{margin:0;padding-left:18px}
"""

_JS = r"""
(function () {
"use strict";
var S = SPEC, ORIG = JSON.stringify({d: S.data, c: S.children});
var UI = {}, stack = [], cur = null, editing = null, modalCb = null;
function err(m) { var b = document.getElementById("errbar"); if (b) { b.style.display = "block"; b.textContent = "Preview error: " + m; } document.title = "ERR: " + m; }
window.onerror = function (m) { err(m); return false; };

/* ---------- helpers ---------- */
function esc(s) { return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]; }); }
function A(v) { return esc(JSON.stringify(v)); }
function toDate(v) { if (v === null || v === undefined || v === "") { return null; } if (v instanceof Date) { return v; }
  var m = /\/Date\((-?\d+)/.exec(String(v)); if (m) { return new Date(Number(m[1])); }
  var s = String(v); if (/^\d{4}-\d{2}-\d{2}$/.test(s)) { var p = s.split("-"); return new Date(+p[0], +p[1] - 1, +p[2]); }
  var d = new Date(s); return isNaN(d.getTime()) ? null : d; }
var util = {
  today: function () { var d = new Date(); d.setHours(0, 0, 0, 0); return d; }, now: function () { return new Date(); }, toDate: toDate,
  daysBetween: function (a, b) { var x = toDate(a), y = toDate(b); if (!x || !y) { return null; } return Math.round((Date.UTC(y.getFullYear(), y.getMonth(), y.getDate()) - Date.UTC(x.getFullYear(), x.getMonth(), x.getDate())) / 86400000); },
  addDays: function (d, n) { var x = toDate(d) || new Date(); x = new Date(x.getTime()); x.setDate(x.getDate() + Number(n || 0)); return x; },
  num: function (v) { var n = Number(v); return v === null || v === undefined || v === "" || isNaN(n) ? 0 : n; },
  round: function (v, d) { var f = Math.pow(10, d || 0); return Math.round(Number(v || 0) * f) / f; },
  isEmpty: function (v) { return v === null || v === undefined || String(v).trim() === ""; },
  contains: function (t, p) { return String(t || "").toLowerCase().indexOf(String(p || "").toLowerCase()) >= 0; },
  oneOf: function (v, a) { return (a || []).map(String).indexOf(String(v)) >= 0; },
  pct: function (a, b) { b = Number(b || 0); return b ? Number(a || 0) / b * 100 : 0; },
  concat: function () { return Array.prototype.slice.call(arguments).filter(function (x) { return x !== null && x !== undefined && String(x) !== ""; }).join(" "); }
};
var FN = {};
function fnOf(params, body) { var k = params + "|" + body; if (!(k in FN)) { try { FN[k] = new Function(params.split(","), '"use strict"; ' + body); } catch (e) { FN[k] = null; } } return FN[k]; }
function expr(e, row) { var f = fnOf("row,util", "return (" + e + ");"); if (!f) { return undefined; }
  try { var v = f(Object.assign({}, row), util); return (typeof v === "number" && isNaN(v)) ? undefined : v; } catch (x) { return undefined; } }
function ymd(d) { return d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2) + "-" + ("0" + d.getDate()).slice(-2); }
function toast(m) { var t = document.getElementById("toast"); t.textContent = m; t.className = "on"; clearTimeout(toast.h); toast.h = setTimeout(function () { t.className = ""; }, 2600); }
function setOf(n) { return S.sets[n] || {props: {}, keys: [], order: [], label: n, navs: {}}; }
function prop(n, f) { return setOf(n).props[f]; }
function lab(n, f) { var p = prop(n, f); return p ? p.label : f; }
function rows(n) { return S.data[n] || (S.data[n] = []); }
function byId(n, id) { return rows(n).filter(function (r) { return r.__id === id; })[0]; }
function pageById(id) { return S.pages.filter(function (p) { return p.id === id; })[0]; }
function detailPageFor(set) { return S.pages.filter(function (p) { return p.type === "list" && p.set === set && p.detail; })[0]; }
function isNum(n, f) { var p = prop(n, f); return p && (p.ui === "number" || p.ui === "amount"); }

function fmt(n, f, v, row) {
  if (v === null || v === undefined || v === "") { return ""; }
  var p = prop(n, f); if (!p) { return String(v); }
  if (p.ui === "boolean") { return (v === true || v === "true") ? "Yes" : "No"; }
  if (p.ui === "date" || p.ui === "datetime") { var d = toDate(v); if (!d) { return String(v); }
    return p.ui === "date" ? d.toLocaleDateString(undefined, {year: "numeric", month: "short", day: "numeric"}) : d.toLocaleString(undefined, {year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"}); }
  if (p.ui === "number" || p.ui === "amount") { var x = Number(v); if (isNaN(x)) { return String(v); }
    if (/^Edm\.(Int|Byte|SByte)/.test(p.type) && (setOf(n).keys.indexOf(f) >= 0 || /(id|no|nr|number|code|via)$/i.test(f))) { return String(v); }
    var sc = (p.scale === null || p.scale === undefined) ? (p.ui === "amount" ? 2 : 0) : Math.min(p.scale, 3);
    var s = x.toLocaleString(undefined, {minimumFractionDigits: sc, maximumFractionDigits: sc});
    return p.unit && row && row[p.unit] ? s + " " + row[p.unit] : s; }
  return String(v);
}
function fmtVal(v, type, dec) { if (v === null || v === undefined || v === "") { return ""; }
  if (v instanceof Date || type === "date") { var d = toDate(v); return d ? d.toLocaleDateString(undefined, {year: "numeric", month: "short", day: "numeric"}) : String(v); }
  if (typeof v === "number" || type === "number") { var x = Number(v); return isNaN(x) ? String(v) : x.toLocaleString(undefined, {minimumFractionDigits: dec || 0, maximumFractionDigits: dec || 0}); }
  if (typeof v === "boolean") { return v ? "Yes" : "No"; } return String(v); }
function stateOf(pg, row) { var st = pg.status || {}; if (!st.field) { return ""; } return (st.states || {})[String(row[st.field])] || ""; }
function cell(pg, n, f, row) {
  var v = fmt(n, f, row[f], row);
  if (pg && pg.status && pg.status.field === f && v !== "") { return '<span class="chip S-' + esc(stateOf(pg, row)) + '">' + esc(v) + "</span>"; }
  var lk = linkFor(n, f);
  if (lk && v !== "") { return '<span class="lnk" onclick="event.stopPropagation();P.openBy(' + A(lk.to) + "," + A(lk.to_field) + "," + A(row[f]) + ')">' + esc(v) + "</span>"; }
  return esc(v);
}
function linkFor(n, f) { return (S.links || []).filter(function (l) { return l.from === n && l.field === f; })[0]; }
function titleOf(pg, n, row) { var t = pg && pg.title_field ? row[pg.title_field] : ""; if (t === null || t === undefined || t === "") { t = setOf(n).keys.map(function (k) { return row[k]; }).join(" / "); } return fmt(n, (pg && pg.title_field) || setOf(n).keys[0], t, row) || String(t); }
function agg(list, how, get) {
  if (how === "count" || !get) { return list.length; }
  var v = list.map(get).map(Number).filter(function (x) { return !isNaN(x); });
  if (!v.length) { return 0; }
  if (how === "sum") { return v.reduce(function (a, b) { return a + b; }, 0); }
  if (how === "average") { return v.reduce(function (a, b) { return a + b; }, 0) / v.length; }
  if (how === "min") { return Math.min.apply(null, v); } if (how === "max") { return Math.max.apply(null, v); }
  return v.length;
}
function short(x) { x = Number(x) || 0; var a = Math.abs(x); if (a >= 1e6) { return (x / 1e6).toFixed(1) + "M"; } if (a >= 1e4) { return (x / 1e3).toFixed(1) + "K"; } return a >= 100 || x === Math.round(x) ? Math.round(x).toLocaleString() : x.toFixed(1); }

/* ---------- charts (inline SVG) ---------- */
var COLORS = ["#5899da", "#e8743b", "#19a979", "#ed4a7b", "#945ecf", "#13a4b4", "#525df4", "#bf399e", "#6c8893", "#ee6868"];
function chart(type, cats, series, M, onclick) {
  // M[s][c] values; cats: [{key,label}]
  if (!cats.length) { return '<div class="empty">No data</div>'; }
  var W = 420, H = 190, pad = 34, i, j, out = '<svg viewBox="0 0 ' + W + " " + H + '" width="100%" height="' + H + '">';
  function click(c) { return onclick ? ' class="bar" onclick="' + esc(onclick + JSON.stringify(c.key) + ")") + '"' : ""; }
  if (type === "pie" || type === "donut") {
    var tot = 0; cats.forEach(function (c, k) { tot += Math.max(0, M[0][k] || 0); }); if (!tot) { tot = 1; }
    var a0 = -Math.PI / 2, cx = 95, cy = H / 2, r = 78;
    cats.forEach(function (c, k) { var v = Math.max(0, M[0][k] || 0), a1 = a0 + v / tot * Math.PI * 2;
      var large = a1 - a0 > Math.PI ? 1 : 0, x0 = cx + r * Math.cos(a0), y0 = cy + r * Math.sin(a0), x1 = cx + r * Math.cos(a1), y1 = cy + r * Math.sin(a1);
      var d = v >= tot ? "M" + (cx - r) + "," + cy + " a" + r + "," + r + " 0 1,0 " + 2 * r + ",0 a" + r + "," + r + " 0 1,0 " + (-2 * r) + ",0" : "M" + cx + "," + cy + " L" + x0 + "," + y0 + " A" + r + "," + r + " 0 " + large + ",1 " + x1 + "," + y1 + " Z";
      out += '<path d="' + d + '" fill="' + COLORS[k % 10] + '"' + click(c) + "><title>" + esc(c.label + ": " + short(v)) + "</title></path>"; a0 = a1; });
    if (type === "donut") { out += '<circle cx="' + cx + '" cy="' + cy + '" r="44" fill="#fff"/><text x="' + cx + '" y="' + (cy + 5) + '" text-anchor="middle" font-size="14" font-weight="600">' + short(tot) + "</text>"; }
    cats.slice(0, 8).forEach(function (c, k) { out += '<rect x="200" y="' + (16 + k * 20) + '" width="10" height="10" rx="2" fill="' + COLORS[k % 10] + '"/><text x="216" y="' + (25 + k * 20) + '" font-size="11">' + esc(String(c.label).slice(0, 22) + " · " + short(M[0][k])) + "</text>"; });
    return out + "</svg>";
  }
  var stacked = /stacked/.test(type), max = 0;
  for (j = 0; j < cats.length; j++) { var s = 0; for (i = 0; i < M.length; i++) { s = stacked ? s + Math.max(0, M[i][j] || 0) : Math.max(s, M[i][j] || 0); } max = Math.max(max, s); }
  if (!max) { max = 1; }
  if (type === "bar" || type === "stacked_bar") {
    var bh = Math.min(22, (H - 10) / cats.length - 4), lw = 110;
    cats.forEach(function (c, k) { var y = 6 + k * (bh + 4), x = lw;
      out += '<text x="' + (lw - 6) + '" y="' + (y + bh / 2 + 4) + '" font-size="10" text-anchor="end">' + esc(String(c.label).slice(0, 18)) + "</text>";
      for (i = 0; i < M.length; i++) { var w = (M[i][k] || 0) / max * (W - lw - 50), hh = stacked ? bh : bh / M.length, yy = stacked ? y : y + i * hh;
        out += '<rect x="' + x + '" y="' + yy + '" width="' + Math.max(0, w) + '" height="' + Math.max(1, hh - 1) + '" rx="2" fill="' + COLORS[i % 10] + '"' + click(c) + "><title>" + esc(c.label + (series.length > 1 ? " · " + series[i] : "") + ": " + short(M[i][k])) + "</title></rect>";
        if (stacked) { x += w; } }
      if (!stacked || M.length === 1) { out += '<text x="' + (x + (stacked ? 0 : (M[0][k] || 0) / max * (W - lw - 50)) + 4) + '" y="' + (y + bh / 2 + 4) + '" font-size="10" fill="#556b82">' + short(M[0][k]) + "</text>"; } });
    return out + "</svg>";
  }
  var cw = (W - pad - 6) / cats.length, base = H - 26;
  out += '<line x1="' + pad + '" y1="' + base + '" x2="' + W + '" y2="' + base + '" stroke="#ddd"/>';
  out += '<text x="' + (pad - 4) + '" y="12" font-size="10" text-anchor="end" fill="#556b82">' + short(max) + "</text>";
  if (type === "line" || type === "area") {
    for (i = 0; i < M.length; i++) { var pts = cats.map(function (c, k) { return (pad + cw * k + cw / 2) + "," + (base - (M[i][k] || 0) / max * (base - 14)); });
      if (type === "area") { out += '<polygon fill="' + COLORS[i % 10] + '" fill-opacity=".18" points="' + (pad + cw / 2) + "," + base + " " + pts.join(" ") + " " + (pad + cw * (cats.length - 1) + cw / 2) + "," + base + '"/>'; }
      out += '<polyline fill="none" stroke="' + COLORS[i % 10] + '" stroke-width="2.5" points="' + pts.join(" ") + '"/>';
      if (i === 0) { cats.forEach(function (c, k) { out += '<text x="' + (pad + cw * k + cw / 2) + '" y="' + (H - 10) + '" font-size="9.5" text-anchor="middle" fill="#556b82">' + esc(String(c.label).slice(0, Math.max(3, Math.floor(cw / 6)))) + "</text>"; }); }
      cats.forEach(function (c, k) { out += '<circle cx="' + (pad + cw * k + cw / 2) + '" cy="' + (base - (M[i][k] || 0) / max * (base - 14)) + '" r="3.5" fill="' + COLORS[i % 10] + '"' + click(c) + "><title>" + esc(c.label + ": " + short(M[i][k])) + "</title></circle>"; }); }
  } else if (type === "heatmap") {
    out = '<table style="font-size:11px"><tr><th></th>' + cats.map(function (c) { return "<th>" + esc(c.label) + "</th>"; }).join("") + "</tr>";
    for (i = 0; i < M.length; i++) { out += "<tr><th>" + esc(series[i]) + "</th>" + cats.map(function (c, k) { var v = M[i][k] || 0; return '<td style="background:rgba(0,112,242,' + (0.08 + 0.8 * v / max).toFixed(2) + ')">' + short(v) + "</td>"; }).join("") + "</tr>"; }
    return out + "</table>";
  } else {
    cats.forEach(function (c, k) { var x = pad + cw * k + 4, bw = Math.max(2, cw - 8), y = base;
      for (i = 0; i < M.length; i++) { var h = (M[i][k] || 0) / max * (base - 14);
        if (type === "combination" && i > 0) { continue; }
        var ww = stacked ? bw : bw / (type === "combination" ? 1 : M.length), xx = stacked ? x : x + i * ww;
        out += '<rect x="' + xx + '" y="' + (stacked ? y - h : base - h) + '" width="' + Math.max(1, ww - 1) + '" height="' + Math.max(0, h) + '" rx="2" fill="' + COLORS[i % 10] + '"' + click(c) + "><title>" + esc(c.label + (series.length > 1 ? " · " + series[i] : "") + ": " + short(M[i][k])) + "</title></rect>";
        if (stacked) { y -= h; } }
      out += '<text x="' + (x + bw / 2) + '" y="' + (H - 10) + '" font-size="9.5" text-anchor="middle" fill="#556b82">' + esc(String(c.label).slice(0, Math.max(3, Math.floor(cw / 6)))) + "</text>"; });
    if (type === "combination" && M.length > 1) { var m2 = Math.max.apply(null, M[1].concat([1]));
      var p2 = cats.map(function (c, k) { return (pad + cw * k + cw / 2) + "," + (base - (M[1][k] || 0) / m2 * (base - 14)); });
      out += '<polyline fill="none" stroke="' + COLORS[1] + '" stroke-width="2.5" points="' + p2.join(" ") + '"/>'; }
  }
  if (series.length > 1 && type !== "heatmap") { series.slice(0, 6).forEach(function (s, k) { out += '<rect x="' + (pad + 8 + k * 70) + '" y="2" width="8" height="8" fill="' + COLORS[k % 10] + '"/><text x="' + (pad + 20 + k * 70) + '" y="10" font-size="9.5">' + esc(String(s).slice(0, 10)) + "</text>"; }); }
  return out + "</svg>";
}
function bucket(v, grain) { var d = toDate(v); if (!d) { return "(none)"; }
  if (grain === "year") { return String(d.getFullYear()); } if (grain === "quarter") { return d.getFullYear() + " Q" + (Math.floor(d.getMonth() / 3) + 1); }
  if (grain === "day") { return ymd(d); } return d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2); }
function groupChart(n, dim, grain, measures, seriesField, type, onclick) {
  var data = rows(n), keys = [], groups = {}, sk = [], i;
  data.forEach(function (r) { var k = grain ? bucket(r[dim], grain) : String(r[dim] === null || r[dim] === undefined ? "" : r[dim]);
    if (!(k in groups)) { groups[k] = []; keys.push(k); } groups[k].push(r);
    if (seriesField) { var s = String(r[seriesField]); if (sk.indexOf(s) < 0) { sk.push(s); } } });
  if (grain) { keys.sort(); } else if (keys.length > 12) { keys.sort(function (a, b) { return groups[b].length - groups[a].length; }); keys = keys.slice(0, 12); }
  var cats = keys.map(function (k) { var lbl = grain ? k : (fmt(n, dim, groups[k][0][dim], groups[k][0]) || "(blank)"); return {key: k, label: lbl}; });
  var getter = function (m) { if (m.expression) { return function (r) { return expr(m.expression, r); }; } return m.field ? function (r) { return r[m.field]; } : null; };
  var M = [], names = [];
  if (seriesField && sk.length) { var m0 = measures[0]; sk.slice(0, 8).forEach(function (s) { names.push(fmt(n, seriesField, s) || s); M.push(keys.map(function (k) { return agg(groups[k].filter(function (r) { return String(r[seriesField]) === s; }), m0.aggregation, getter(m0)); })); }); }
  else { measures.forEach(function (m) { names.push(m.label || m.field || "Count"); M.push(keys.map(function (k) { return agg(groups[k], m.aggregation, getter(m)); })); }); }
  return chart(type, cats, names, M, onclick);
}

/* ---------- navigation ---------- */
function go(r, push) { if (push !== false && cur) { stack.push(cur); } cur = r; editing = null; render(); var m = document.querySelector(".main"); if (m) { m.scrollTop = 0; } }
function back() { cur = stack.pop() || {page: firstPage()}; editing = null; render(); }
function navTo(pid) { stack = []; go({page: pid}, false); }
function firstPage() { var p = S.pages.filter(function (x) { return x.type !== "hidden"; })[0]; return p ? p.id : ""; }
function openRow(n, id) {
  var pg = detailPageFor(n);
  if (pg && pg.layout === "split") { ui(pg.id).sel = id; go({page: pg.id}); return; }
  go({detail: n, id: id, pid: pg ? pg.id : ""});
}
function openBy(n, f, v) { var r = rows(n).filter(function (x) { return String(x[f]) === String(v); })[0];
  if (r) { openRow(n, r.__id); } else { toast("No " + setOf(n).label + " " + v + " in the preview data"); } }
function ui(pid) { return UI[pid] || (UI[pid] = {q: "", f: {}, sort: null, tab: "__all", sel: null, view: 0, month: null}); }

/* ---------- list pages ---------- */
function columnsOf(pg) { var cols = pg.columns.filter(function (c) { return prop(pg.set, c); }).map(function (c) { return {f: c}; });
  (pg.computed || []).forEach(function (c) { var at = -1; cols.forEach(function (x, i) { if (x.f === c.after) { at = i; } }); if (at >= 0) { cols.splice(at + 1, 0, {c: c}); } else { cols.push({c: c}); } });
  return cols; }
function filtered(pg) {
  var u = ui(pg.id), data = rows(pg.set).slice(), st = pg.status || {};
  if (pg.quick_tabs && st.field && u.tab !== "__all") { data = data.filter(function (r) { return String(r[st.field]) === u.tab; }); }
  if (pg.views && pg.views.length && u.view) { var v = pg.views[u.view - 1]; (v.filters || []).forEach(function (f) { data = data.filter(function (r) { return (f.values || []).map(String).indexOf(String(r[f.field])) >= 0; }); }); }
  Object.keys(u.f).forEach(function (f) { var val = u.f[f]; if (val === "" || val === null || val === undefined) { return; }
    var p = prop(pg.set, f) || {};
    data = data.filter(function (r) { if (p.opts || p.ui === "boolean") { return String(r[f]) === String(val); }
      if (p.ui === "date" || p.ui === "datetime") { var d = toDate(r[f]); return d && ymd(d) === val; }
      return util.contains(fmt(pg.set, f, r[f], r) + " " + r[f], val); }); });
  if (u.q) { var q = u.q.toLowerCase(); data = data.filter(function (r) { return Object.keys(r).some(function (k) { return k !== "__id" && String(r[k] === null ? "" : r[k]).toLowerCase().indexOf(q) >= 0; }); }); }
  var s = u.sort || (pg.sort && pg.sort.field ? {f: pg.sort.field, d: !!pg.sort.descending} : null);
  if (s) { data.sort(function (a, b) { var x = s.c ? expr(s.c.expr, a) : a[s.f], y = s.c ? expr(s.c.expr, b) : b[s.f];
    var nx = Number(x), ny = Number(y), r = (!isNaN(nx) && !isNaN(ny) && x !== "" && y !== "" && x !== null && y !== null) ? nx - ny : String(x === null || x === undefined ? "" : x).localeCompare(String(y === null || y === undefined ? "" : y));
    return s.d ? -r : r; }); }
  return data;
}
function computedCell(pg, c, row) { var v = expr(c.expr, row); return esc(fmtVal(v, c.type, c.decimals)); }
function rowState(pg, row) { if (!pg.row_state) { return ""; } var v = expr(pg.row_state, row); return typeof v === "string" && /^(Error|Warning|Success|Information)$/.test(v) ? v : ""; }
function tableHtml(pg) {
  var cols = columnsOf(pg), u = ui(pg.id), data = filtered(pg), s = u.sort || {f: (pg.sort || {}).field, d: (pg.sort || {}).descending};
  var rowActs = (pg.actions || []).filter(function (a) { return a.scope === "row"; });
  var h = "<table><tr>" + (rowActs.length ? "<th></th>" : "") + cols.map(function (c, i) { var arrow = (c.f && s.f === c.f && !s.c) || (c.c && s.c && s.c.id === c.c.id) ? '<span class="ar">' + (s.d ? " ▼" : " ▲") + "</span>" : "";
    return '<th class="' + (c.c ? "f" : "") + ((c.f && isNum(pg.set, c.f)) || (c.c && c.c.type === "number") ? " num" : "") + '" onclick="P.sortBy(' + A(pg.id) + "," + i + ')">' + (c.c ? "ƒ " + esc(c.c.label) : esc(lab(pg.set, c.f))) + arrow + "</th>"; }).join("") + "</tr>";
  if (!data.length) { return h + '<tr><td colspan="' + (cols.length + 1) + '"><div class="empty">No records match.</div></td></tr></table>'; }
  data.forEach(function (r) { var rs = rowState(pg, r);
    h += '<tr class="row' + (rs ? " R-" + rs : "") + (u.sel === r.__id ? " sel" : "") + '" onclick="P.rowClick(' + A(pg.id) + "," + r.__id + ')">' + (rowActs.length ? '<td onclick="event.stopPropagation()"><input type="radio" name="sel_' + esc(pg.id) + '"' + (u.sel === r.__id ? " checked" : "") + ' onclick="P.select(' + A(pg.id) + "," + r.__id + ')"></td>' : "")
      + cols.map(function (c) { return c.c ? '<td class="' + (c.c.type === "number" ? "num" : "") + '">' + computedCell(pg, c.c, r) + "</td>" : '<td class="' + (isNum(pg.set, c.f) ? "num" : "") + '">' + cell(pg, pg.set, c.f, r) + "</td>"; }).join("") + "</tr>"; });
  return h + "</table>";
}
function filterBar(pg) {
  var u = ui(pg.id);
  var f = (pg.filters || []).filter(function (x) { return prop(pg.set, x); }).map(function (x) {
    var p = prop(pg.set, x), v = u.f[x] || "", inp;
    if (p.opts || p.ui === "boolean") { var opts = p.ui === "boolean" ? ["true", "false"] : p.opts;
      inp = '<select onchange="P.filt(' + A(pg.id) + "," + A(x) + ',this.value)"><option value="">All</option>' + opts.filter(function (o) { return o !== ""; }).map(function (o) { return '<option value="' + esc(o) + '"' + (String(v) === String(o) ? " selected" : "") + ">" + esc(p.ui === "boolean" ? (o === "true" ? "Yes" : "No") : (fmtStatus(pg, x, o))) + "</option>"; }).join("") + "</select>"; }
    else if (p.ui === "date" || p.ui === "datetime") { inp = '<input type="date" value="' + esc(v) + '" onchange="P.filt(' + A(pg.id) + "," + A(x) + ',this.value)">'; }
    else { inp = '<input value="' + esc(v) + '" placeholder="contains…" oninput="P.filt(' + A(pg.id) + "," + A(x) + ',this.value)">'; }
    return '<div class="fld"><span>' + esc(p.label) + "</span>" + inp + "</div>"; }).join("");
  return f ? '<div class="fb">' + f + (Object.keys(u.f).some(function (k) { return u.f[k]; }) ? '<button class="btn g" onclick="P.clearF(' + A(pg.id) + ')">Clear</button>' : "") + "</div>" : "";
}
function fmtStatus(pg, f, o) { var t = fmt(pg.set, f, o); return t; }
function kpiTiles(pg) { if (!(pg.kpis && pg.kpis.length)) { return ""; }
  return '<div class="kpis">' + pg.kpis.map(function (k) { var v = agg(rows(pg.set), k.aggregation, k.field ? function (r) { return r[k.field]; } : null); return '<div class="kpi"><span>' + esc(k.label || lab(pg.set, k.field)) + "</span><b>" + short(v) + "</b></div>"; }).join("") + "</div>"; }
function tabsHtml(pg) {
  var u = ui(pg.id), st = pg.status || {}, h = "";
  if (pg.quick_tabs && st.field) { var all = rows(pg.set), keys = Object.keys(st.states || {});
    all.forEach(function (r) { var k = String(r[st.field]); if (keys.indexOf(k) < 0) { keys.push(k); } });
    h = '<div class="tabs"><span class="' + (u.tab === "__all" ? "on" : "") + '" onclick="P.tab(' + A(pg.id) + ',&quot;__all&quot;)">All<em>' + all.length + "</em></span>" + keys.map(function (k) {
      return '<span class="' + (u.tab === k ? "on" : "") + '" onclick="P.tab(' + A(pg.id) + "," + A(k) + ')">' + esc(fmt(pg.set, st.field, k) || "(blank)") + "<em>" + all.filter(function (r) { return String(r[st.field]) === k; }).length + "</em></span>"; }).join("") + "</div>"; }
  else if (pg.views && pg.views.length) { h = '<div class="tabs"><span class="' + (!u.view ? "on" : "") + '" onclick="P.view(' + A(pg.id) + ',0)">All</span>' + pg.views.map(function (v, i) { return '<span class="' + (u.view === i + 1 ? "on" : "") + '" onclick="P.view(' + A(pg.id) + "," + (i + 1) + ')">' + esc(v.title) + "</span>"; }).join("") + "</div>"; }
  return h;
}
function toolbar(pg, count) {
  var u = ui(pg.id), sel = u.sel ? byId(pg.set, u.sel) : null;
  var acts = (pg.actions || []).filter(function (a) { return a.scope === "row" || a.scope === "global"; }).map(function (a) { return actBtn(pg, a, a.scope === "global" ? {} : sel, a.scope === "row" && !sel ? "Select a row first" : ""); }).join("");
  return '<div class="tb"><span class="t">' + esc(setOf(pg.set).label) + ' <span style="font-weight:400;color:#556b82">(' + count + ")</span></span>"
    + (pg.search ? '<input class="srch" placeholder="Search" value="' + esc(u.q) + '" oninput="P.search(' + A(pg.id) + ',this.value)">' : "")
    + ((pg.caps || {}).create ? '<button class="btn e" onclick="P.create(' + A(pg.id) + ')">Create</button>' : "") + acts + "</div>";
}
function renderList(pg) {
  var u = ui(pg.id), data = filtered(pg);
  if (pg.layout === "split") { return renderSplit(pg); }
  var chartHtml = "";
  if (pg.chart && pg.chart.dimension) { chartHtml = '<div class="ct" style="padding:8px 12px"><small style="color:#556b82">' + esc((pg.chart.measure ? lab(pg.set, pg.chart.measure) : "Count") + " by " + lab(pg.set, pg.chart.dimension)) + "</small>"
    + groupChart(pg.set, pg.chart.dimension, "", [{field: pg.chart.measure, aggregation: pg.chart.measure ? pg.chart.aggregation : "count", label: pg.chart.measure ? lab(pg.set, pg.chart.measure) : "Count"}], "", pg.chart.type, "P.chartFilter(" + JSON.stringify(pg.id) + "," + JSON.stringify(pg.chart.dimension) + ",") + "</div>"; }
  return '<div class="ph"><h2>' + esc(pg.title) + "</h2>" + filterBar(pg) + "</div>" + kpiTiles(pg) + chartHtml
    + '<div class="ct">' + tabsHtml(pg) + '<div id="tb_' + esc(pg.id) + '">' + toolbar(pg, data.length) + "</div>" + '<div id="tbl_' + esc(pg.id) + '">' + tableHtml(pg) + "</div></div>";
}
function refresh(pg) {
  var t = document.getElementById("tbl_" + pg.id);
  if (!t || pg.layout === "split") { render(); return; }
  t.innerHTML = tableHtml(pg);
  var tb = document.getElementById("tb_" + pg.id), a = document.activeElement, isS = a && a.classList.contains("srch");
  if (tb && !isS) { tb.innerHTML = toolbar(pg, filtered(pg).length); }
  else if (tb) { var cnt = tb.querySelector(".t span"); if (cnt) { cnt.textContent = "(" + filtered(pg).length + ")"; } }
}
function renderSplit(pg) {
  var u = ui(pg.id), data = filtered(pg);
  if (!u.sel && data.length) { u.sel = data[0].__id; }
  var items = data.map(function (r) { var st = stateOf(pg, r); return '<div class="li' + (u.sel === r.__id ? " on" : "") + '" onclick="P.select(' + A(pg.id) + "," + r.__id + ',true)"><b>' + esc(titleOf(pg, pg.set, r)) + "</b><span>"
    + (pg.desc_field ? esc(fmt(pg.set, pg.desc_field, r[pg.desc_field], r)) : "") + (pg.status.field ? ' <span class="chip S-' + esc(st) + '">' + esc(fmt(pg.set, pg.status.field, r[pg.status.field])) + "</span>" : "") + "</span></div>"; }).join("") || '<div class="empty">No records.</div>';
  var row = u.sel ? byId(pg.set, u.sel) : null;
  return '<div class="ph"><h2>' + esc(pg.title) + "</h2>" + filterBar(pg) + "</div>" + tabsHtml(pg)
    + '<div class="split"><div class="l"><div class="tb">' + (pg.search ? '<input class="srch" style="width:100%" placeholder="Search" value="' + esc(u.q) + '" onchange="P.search(' + A(pg.id) + ',this.value,true)">' : "")
    + ((pg.caps || {}).create ? '<button class="btn e" onclick="P.create(' + A(pg.id) + ')">Create</button>' : "") + "</div>" + items + '</div><div class="r">' + (row ? detailBody(pg, pg.set, row, true) : '<div class="empty">Select a record.</div>') + "</div></div>";
}

/* ---------- object page ---------- */
function genericDetail(n) { var m = setOf(n); return {id: "", set: n, title: m.label, title_field: "", status: {field: "", states: {}}, caps: {}, actions: [], computed: [], validations: [], derived: [], defaults: [], form: {required: [], readonly: [], groups: []}, value_helps: [],
  detail: {header_fields: [], sections: [{title: "General Information", fields: m.order.slice(0, 16)}], tables: []}}; }
function enabled(a, row) { var ew = a.ew || {}; if (!row) { return false; } if (!ew.field) { return true; } return (ew.values || []).map(String).indexOf(String(row[ew.field])) >= 0; }
function actBtn(pg, a, row, why) {
  var ok = !why && enabled(a, row), tip = why || (ok ? "" : "Only available when " + lab(pg.set, a.ew.field) + " is " + (a.ew.values || []).join(" / "));
  return '<button class="btn" ' + (ok ? "" : "disabled ") + 'title="' + esc(tip) + '" onclick="P.act(' + A(pg.id || "") + "," + A(pg.set) + "," + A(a.id) + "," + (row && row.__id ? row.__id : 0) + ')">' + esc(a.label) + "</button>";
}
function inputFor(pg, n, f, v, bad) {
  var p = prop(n, f) || {ui: "text"}, cls = bad ? ' class="bad"' : "", att = ' data-f="' + esc(f) + '"' + cls;
  var vh = (pg.value_helps || []).filter(function (x) { return x.field === f; })[0];
  if (vh && rows(vh.set).length) { return "<select" + att + '><option value=""></option>' + rows(vh.set).map(function (r) { var k = r[vh.key]; return '<option value="' + esc(k) + '"' + (String(k) === String(v) ? " selected" : "") + ">" + esc(k + (vh.text && r[vh.text] ? " · " + r[vh.text] : "")) + "</option>"; }).join("") + "</select>"; }
  if (p.ui === "boolean") { return "<select" + att + '><option value=""></option><option value="true"' + (v === true || v === "true" ? " selected" : "") + '>Yes</option><option value="false"' + (v === false || v === "false" ? " selected" : "") + ">No</option></select>"; }
  if (p.opts) { var o = p.opts.slice(); if (v !== null && v !== undefined && v !== "" && o.indexOf(String(v)) < 0) { o.unshift(String(v)); }
    return "<select" + att + '><option value=""></option>' + o.map(function (x) { return '<option value="' + esc(x) + '"' + (String(x) === String(v) ? " selected" : "") + ">" + esc(x) + "</option>"; }).join("") + "</select>"; }
  if (p.ui === "date") { var d = toDate(v); return '<input type="date"' + att + ' value="' + (d ? ymd(d) : "") + '">'; }
  if (p.ui === "datetime") { var dt = toDate(v); return '<input type="datetime-local"' + att + ' value="' + (dt ? ymd(dt) + "T" + ("0" + dt.getHours()).slice(-2) + ":" + ("0" + dt.getMinutes()).slice(-2) : "") + '">'; }
  if (p.ui === "number" || p.ui === "amount") { return '<input type="number" step="any"' + att + ' value="' + esc(v === null || v === undefined ? "" : v) + '">'; }
  return "<input" + att + (p.ml ? ' maxlength="' + p.ml + '"' : "") + ' value="' + esc(v === null || v === undefined ? "" : v) + '">';
}
function readForm(box, n, base) {
  var r = Object.assign({}, base || {});
  box.querySelectorAll("[data-f]").forEach(function (el) { var f = el.getAttribute("data-f"), p = prop(n, f) || {ui: "text"}, v = el.value;
    if (v === "") { r[f] = null; return; }
    if (p.ui === "boolean") { r[f] = v === "true"; } else if (p.ui === "number" || p.ui === "amount") { r[f] = Number(v); }
    else if (p.ui === "datetime") { r[f] = new Date(v).toISOString().replace(/\.\d{3}Z$/, "Z"); } else { r[f] = v; } });
  return r;
}
function normalize(n, f, v) { var p = prop(n, f) || {}; if (v instanceof Date) { return p.ui === "date" ? ymd(v) : v.toISOString().replace(/\.\d{3}Z$/, "Z"); } return v; }
function check(pg, n, row, fields) {
  var bad = {}, msgs = [];
  (pg.form.required || []).concat(fields.filter(function (f) { var p = prop(n, f); return p && p.nullable === false && setOf(n).keys.indexOf(f) < 0; })).forEach(function (f) {
    if (fields.indexOf(f) >= 0 && util.isEmpty(row[f])) { bad[f] = "Required"; } });
  if (Object.keys(bad).length) { msgs.push("Fill in all required fields."); }
  (pg.validations || []).forEach(function (v) { var ok = expr(v.expr, row); if (ok === false || ok === 0 || ok === "") { msgs.push(v.message); (v.fields || []).forEach(function (f) { bad[f] = bad[f] || v.message; }); } });
  return {bad: bad, msgs: msgs};
}
function fieldGrid(pg, n, fields, row, edit, bad) {
  return '<div class="form">' + fields.filter(function (f) { return prop(n, f); }).map(function (f) { var p = prop(n, f), ro = !edit || p.upd === false || setOf(n).keys.indexOf(f) >= 0 || (pg.form.readonly || []).indexOf(f) >= 0;
    var req = edit && !ro && ((pg.form.required || []).indexOf(f) >= 0 || p.nullable === false);
    return '<div class="fv"><span class="' + (req ? "req" : "") + '">' + esc(p.label) + "</span>" + (ro ? "<div>" + (cell(pg, n, f, row) || "–") + "</div>" : inputFor(pg, n, f, row[f], bad && bad[f]) + (bad && bad[f] ? '<div class="err">' + esc(bad[f]) + "</div>" : "")) + "</div>"; }).join("") + "</div>";
}
function detailBody(pg, n, row, inSplit) {
  var d = pg.detail || genericDetail(n).detail, ed = editing && editing.id === row.__id && editing.set === n ? editing : null, r = ed ? ed.draft : row;
  var hdr = (d.header_fields || []).filter(function (f) { return prop(n, f); }).map(function (f) { return "<div><span>" + esc(lab(n, f)) + "</span><b>" + (cell(pg, n, f, r) || "–") + "</b></div>"; }).join("")
    + (pg.computed || []).map(function (c) { return "<div><span>ƒ " + esc(c.label) + "</span><b>" + (computedCell(pg, c, r) || "–") + "</b></div>"; }).join("");
  if (pg.status && pg.status.field && (d.header_fields || []).indexOf(pg.status.field) < 0) { hdr = "<div><span>" + esc(lab(n, pg.status.field)) + "</span><b>" + cell(pg, n, pg.status.field, r) + "</b></div>" + hdr; }
  var acts = ed ? '<button class="btn e" onclick="P.save()">Save</button><button class="btn" onclick="P.cancel()">Cancel</button>'
    : (((pg.caps || {}).update ? '<button class="btn e" onclick="P.edit(' + A(pg.id) + "," + A(n) + "," + row.__id + ')">Edit</button>' : "")
      + (function () { var seen = {}; return (pg.actions || []).filter(function (a) {
          // the same action may be a row AND an object action — one button here
          var k = a.kind + "|" + (a.operation || a.field || a.label) + "|" + (a.value || "");
          if (a.scope === "global" || seen[k]) { return false; } seen[k] = 1; return true; }); })()
          .map(function (a) { return actBtn(pg, a, row, ""); }).join("")
      + ((pg.caps || {}).delete ? '<button class="btn n" onclick="P.del(' + A(pg.id) + "," + A(n) + "," + row.__id + ')">Delete</button>' : ""));
  var desc = pg.desc_field ? fmt(n, pg.desc_field, r[pg.desc_field], r) : setOf(n).label;
  var h = '<div class="oh">' + (inSplit ? "" : '<div class="crumb"><span onclick="P.back()">‹ ' + esc(pg.title || setOf(n).label) + "</span></div>")
    + '<div class="top"><div><h2>' + esc(titleOf(pg, n, r)) + '</h2><div class="sub" style="color:#556b82">' + esc(desc) + '</div></div><div class="acts">' + acts + "</div></div>"
    + (hdr ? '<div class="hdr">' + hdr + "</div>" : "") + "</div>";
  if (ed && ed.msgs && ed.msgs.length) { h += '<div class="msgs">' + ed.msgs.map(esc).join("<br>") + "</div>"; }
  h += '<div id="edbox">' + (d.sections || []).map(function (s) { return '<div class="sec"><h4>' + esc(s.title) + "</h4>" + fieldGrid(pg, n, s.fields || [], r, !!ed, ed && ed.bad) + "</div>"; }).join("") + "</div>";
  (d.tables || []).forEach(function (t) { var kids = ((S.children[n] || {})[t.nav] || {})[String(row.__id)] || [], cols = (t.columns || []).filter(function (c) { return prop(t.set, c); });
    h += '<div class="sec"><h4>' + esc(t.title) + ' <span style="font-weight:400;color:#556b82">(' + kids.length + ")</span></h4><table><tr>" + cols.map(function (c) { return '<th class="' + (isNum(t.set, c) ? "num" : "") + '">' + esc(lab(t.set, c)) + "</th>"; }).join("") + "</tr>"
      + (kids.length ? kids.map(function (k) { return "<tr>" + cols.map(function (c) { return '<td class="' + (isNum(t.set, c) ? "num" : "") + '">' + cell(null, t.set, c, k) + "</td>"; }).join("") + "</tr>"; }).join("") : '<tr><td colspan="' + cols.length + '"><div class="empty">No items.</div></td></tr>') + "</table></div>"; });
  return h;
}

/* ---------- other page types ---------- */
function renderDashboard(pg) {
  var h = '<div class="ph"><h2>' + esc(pg.title) + "</h2></div>";
  if ((pg.kpis || []).length) { h += '<div class="kpis">' + pg.kpis.map(function (k) { var v = agg(rows(k.entity_set), k.aggregation, k.field ? function (r) { return r[k.field]; } : null);
    return '<div class="kpi" ' + (k.route ? 'onclick="P.navTo(' + A(k.route) + ')"' : "") + "><span>" + esc(k.label) + "</span><b>" + short(v) + "</b></div>"; }).join("") + "</div>"; }
  h += '<div class="grid">' + (pg.charts || []).map(function (c) { var m = {field: c.measure, aggregation: c.measure ? c.aggregation : "count", label: c.measure ? lab(c.entity_set, c.measure) : "Count"};
    var t = {bar: "bar", donut: "donut", column: "column"}[c.type] || "bar";
    return '<div class="card"><h4>' + esc(c.title) + "</h4><small>" + esc(m.label + " by " + lab(c.entity_set, c.dimension)) + "</small>" + groupChart(c.entity_set, c.dimension, "", [m], "", t, c.route ? "P.drill(" + JSON.stringify(c.route) + "," + JSON.stringify(c.dimension) + "," : "") + "</div>"; }).join("")
    + (pg.lists || []).map(function (l) { var data = rows(l.entity_set).slice(), s = l.sort || {};
      if (s.field) { data.sort(function (a, b) { var x = a[s.field], y = b[s.field], r = (typeof x === "number" && typeof y === "number") ? x - y : String(x).localeCompare(String(y)); return s.descending ? -r : r; }); }
      return '<div class="card"><h4>' + esc(l.title) + "</h4><table><tr>" + l.fields.map(function (f) { return "<th>" + esc(lab(l.entity_set, f)) + "</th>"; }).join("") + "</tr>"
        + data.slice(0, 5).map(function (r) { return '<tr class="row" onclick="P.openRow(' + A(l.entity_set) + "," + r.__id + ')">' + l.fields.map(function (f) { return "<td>" + cell(null, l.entity_set, f, r) + "</td>"; }).join("") + "</tr>"; }).join("") + "</table></div>"; }).join("") + "</div>";
  return h;
}
function renderKanban(pg) {
  var n = pg.entity_set, data = rows(n), lanes = (pg.lanes || []).slice();
  if (!lanes.length) { data.forEach(function (r) { var v = String(r[pg.lane_field]); if (!lanes.some(function (l) { return l.value === v; })) { lanes.push({value: v, title: fmt(n, pg.lane_field, v) || "(blank)"}); } }); }
  var other = data.filter(function (r) { return !lanes.some(function (l) { return l.value === String(r[pg.lane_field]); }); });
  var h = '<div class="ph"><h2>' + esc(pg.title) + '</h2><div class="sub">' + (pg.move ? "Drag cards between columns to change " + esc(lab(n, pg.lane_field)) + "." : "Cards grouped by " + esc(lab(n, pg.lane_field)) + ".") + '</div></div><div class="board">';
  function card(r) { return '<div class="kc"' + (pg.move ? ' draggable="true" ondragstart="P.drag(event,' + r.__id + ')"' : "") + ' onclick="P.openRow(' + A(n) + "," + r.__id + ')"><b>' + esc(fmt(n, pg.card_title, r[pg.card_title], r) || titleOf(null, n, r)) + "</b>"
    + (pg.card_subtitle ? "<span>" + esc(fmt(n, pg.card_subtitle, r[pg.card_subtitle], r)) + "</span>" : "") + (pg.card_fields || []).map(function (f) { return "<span>" + esc(lab(n, f)) + ": " + esc(fmt(n, f, r[f], r)) + "</span>"; }).join("") + "</div>"; }
  lanes.forEach(function (l) { var items = data.filter(function (r) { return String(r[pg.lane_field]) === l.value; });
    h += '<div class="lane"' + (pg.move ? ' ondragover="P.over(event,this)" ondragleave="this.classList.remove(\'over\')" ondrop="P.drop(event,' + A(pg.id) + "," + A(l.value) + ',this)"' : "") + "><h5>" + esc(l.title) + "<span>" + items.length + "</span></h5>" + items.map(card).join("") + "</div>"; });
  if (other.length) { h += '<div class="lane"><h5>Other<span>' + other.length + "</span></h5>" + other.map(card).join("") + "</div>"; }
  return h + "</div>";
}
function renderCalendar(pg) {
  var n = pg.entity_set, u = ui(pg.id), t = toDate(S.today) || new Date();
  if (!u.month) { u.month = [t.getFullYear(), t.getMonth()]; }
  var y = u.month[0], m = u.month[1], first = new Date(y, m, 1), start = (first.getDay() + 6) % 7, days = new Date(y, m + 1, 0).getDate();
  var colorKeys = [];
  var h = '<div class="ph"><h2>' + esc(pg.title) + '</h2></div><div class="calh"><button class="btn" onclick="P.month(' + A(pg.id) + ',-1)">‹</button><b>' + first.toLocaleDateString(undefined, {month: "long", year: "numeric"}) + '</b><button class="btn" onclick="P.month(' + A(pg.id) + ',1)">›</button><button class="btn g" onclick="P.month(' + A(pg.id) + ',0)">Today</button></div><div class="cal">';
  ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].forEach(function (d) { h += '<div class="h">' + d + "</div>"; });
  for (var i = 0; i < start; i++) { h += '<div class="o"></div>'; }
  for (var d = 1; d <= days; d++) { var key = y + "-" + ("0" + (m + 1)).slice(-2) + "-" + ("0" + d).slice(-2);
    var items = rows(n).filter(function (r) { var a = toDate(r[pg.start_field]), b = pg.end_field ? toDate(r[pg.end_field]) : null; if (!a) { return false; } if (b && b > a) { return ymd(a) <= key && key <= ymd(b); } return ymd(a) === key; });
    h += '<div class="' + (key === ymd(t) ? "td" : "") + '">' + d + items.slice(0, 3).map(function (r) { var c = pg.color_field ? String(r[pg.color_field]) : ""; if (colorKeys.indexOf(c) < 0) { colorKeys.push(c); }
      var col = COLORS[colorKeys.indexOf(c) % 10];
      return '<span class="ap" style="background:' + col + '22;border-left:3px solid ' + col + '" title="' + esc((pg.text_field ? fmt(n, pg.text_field, r[pg.text_field], r) : "")) + '" onclick="P.openRow(' + A(n) + "," + r.__id + ')">' + esc(fmt(n, pg.title_field, r[pg.title_field], r)) + "</span>"; }).join("") + (items.length > 3 ? '<span style="font-size:10px">+' + (items.length - 3) + " more</span>" : "") + "</div>"; }
  return h + "</div>";
}
function renderAnalytics(pg) {
  return '<div class="ph"><h2>' + esc(pg.title) + '</h2></div><div class="grid">' + (pg.charts || []).map(function (c) {
    return '<div class="card"><h4>' + esc(c.title) + "</h4><small>" + esc(c.measures.map(function (m) { return (m.label || m.field) + (m.aggregation && m.aggregation !== "count" ? " (" + m.aggregation + ")" : ""); }).join(", ") + " by " + (c.dimension_label || lab(c.entity_set, c.dimension)) + (c.time_grain ? " / " + c.time_grain : "") + (c.series ? " and " + (c.series_label || c.series) : "")) + "</small>"
      + groupChart(c.entity_set, c.dimension, c.time_grain, c.measures, c.series, c.type, c.route && !c.time_grain ? "P.drill(" + JSON.stringify(c.route) + "," + JSON.stringify(c.dimension) + "," : "") + "</div>"; }).join("") + "</div>";
}

/* ---------- create ---------- */
function createFields(pg) { var g = (pg.form.groups || []).filter(function (x) { return x.fields && x.fields.length; });
  if (!g.length) { g = [{title: "General", fields: setOf(pg.set).order.filter(function (f) { return setOf(pg.set).keys.indexOf(f) < 0 && (prop(pg.set, f) || {}).cre !== false; }).slice(0, 10)}]; }
  return g; }
function newRow(pg) {
  var n = pg.set, r = {}; setOf(n).order.forEach(function (f) { r[f] = null; });
  (pg.defaults || []).forEach(function (d) { var v = expr(d.expr, r); if (v !== undefined) { r[d.field] = normalize(n, d.field, v); } });
  return r;
}
function formGrid(pg, fields, r, bad) { return '<div class="form">' + fields.filter(function (f) { return prop(pg.set, f); }).map(function (f) { var p = prop(pg.set, f), req = (pg.form.required || []).indexOf(f) >= 0 || p.nullable === false;
  return '<div class="fv"><span class="' + (req ? "req" : "") + '">' + esc(p.label) + "</span>" + inputFor(pg, pg.set, f, r[f], bad && bad[f]) + (bad && bad[f] ? '<div class="err">' + esc(bad[f]) + "</div>" : "") + "</div>"; }).join("") + "</div>"; }
function openCreate(pg, draft, bad, msgs) {
  var r = draft || newRow(pg), groups = createFields(pg);
  modal('<div class="dlg"><header>New ' + esc(setOf(pg.set).label) + '</header><div class="c" id="cbox">' + (msgs && msgs.length ? '<div class="msgs" style="margin:0 0 10px">' + msgs.map(esc).join("<br>") + "</div>" : "")
    + groups.map(function (g) { return '<h4 style="margin:6px 0 8px">' + esc(g.title) + "</h4>" + formGrid(pg, g.fields, r, bad); }).join("")
    + '</div><footer><button class="btn e" onclick="P.doCreate(' + A(pg.id) + ')">Create</button><button class="btn" onclick="P.close()">Cancel</button></footer></div>');
  modal.draft = r;
}
function finishCreate(pg, r, fields) {
  var n = pg.set, res;
  (pg.derived || []).forEach(function (d) { var v = expr(d.expr, r); if (v !== undefined) { r[d.field] = normalize(n, d.field, v); } });
  res = check(pg, n, r, fields);
  if (res.msgs.length) { return res; }
  var keys = setOf(n).keys, all = rows(n);
  keys.forEach(function (k) { if (util.isEmpty(r[k])) { var p = prop(n, k) || {}, nums = all.map(function (x) { return Number(x[k]); }).filter(function (x) { return !isNaN(x); });
    var nx = nums.length ? Math.max.apply(null, nums) + 1 : 1; r[k] = (p.ui === "number" || p.ui === "amount") ? nx : (p.type === "Edm.Guid" ? "new-" + Date.now() : String(nx)); } });
  r.__id = Date.now(); all.unshift(r);
  return null;
}
function renderWizard(pg) {
  var w = cur.wiz, steps = pg.wizard_steps, i = w.step, last = i >= steps.length;
  var h = '<div class="ph"><div class="crumb"><span onclick="P.back()">‹ ' + esc(pg.title) + "</span></div><h2>New " + esc(setOf(pg.set).label) + '</h2></div><div class="wiz">'
    + steps.map(function (s, k) { return '<span class="' + (k === i ? "on" : k < i ? "dn" : "") + '">' + (k < i ? "✓ " : (k + 1) + ". ") + esc(s.title) + "</span>"; }).join("") + '<span class="' + (last ? "on" : "") + '">Review</span></div>';
  if (w.msgs && w.msgs.length) { h += '<div class="msgs">' + w.msgs.map(esc).join("<br>") + "</div>"; }
  if (!last) { h += '<div class="sec" id="cbox">' + formGrid(pg, steps[i].fields, w.draft, w.bad) + "</div>"; }
  else { h += steps.map(function (s) { return '<div class="sec"><h4>' + esc(s.title) + '</h4><div class="form">' + s.fields.filter(function (f) { return prop(pg.set, f); }).map(function (f) { return '<div class="fv"><span>' + esc(lab(pg.set, f)) + "</span><div>" + (esc(fmt(pg.set, f, w.draft[f], w.draft)) || "–") + "</div></div>"; }).join("") + "</div></div>"; }).join(""); }
  h += '<div style="margin:12px 14px;display:flex;gap:8px">' + (i > 0 ? '<button class="btn" onclick="P.wiz(-1)">Previous</button>' : "") + (last ? '<button class="btn e" onclick="P.wizDone()">Create</button>' : '<button class="btn e" onclick="P.wiz(1)">Next step</button>') + '<button class="btn g" onclick="P.back()">Cancel</button></div>';
  return h;
}

/* ---------- modal ---------- */
function modal(html) { var m = document.getElementById("modal"); m.innerHTML = html; m.style.display = "flex"; }
function closeModal() { var m = document.getElementById("modal"); m.style.display = "none"; m.innerHTML = ""; modalCb = null; }
function confirmBox(text, cb) { modalCb = cb; modal('<div class="dlg" style="width:min(420px,94vw)"><header>Confirm</header><div class="c">' + esc(text) + '</div><footer><button class="btn e" onclick="P.ok()">OK</button><button class="btn" onclick="P.close()">Cancel</button></footer></div>'); }

/* ---------- actions ---------- */
function runAction(pg, n, a, row) {
  if (a.kind === "set_field") { row[a.field] = a.value; toast(a.label + ": " + lab(n, a.field) + " set to " + a.value + " (preview only)"); render(); return; }
  if (a.kind === "operation") {
    var w = (S.workflow || {})[a.operation];
    if (w && w.field) { if (w.from && w.from.length && w.from.map(String).indexOf(String(row[w.field])) < 0) { toast(a.label + " is not allowed in status " + row[w.field]); return; }
      row[w.field] = w.to; toast(a.label + " done — " + lab(n, w.field) + " is now " + w.to + " (simulated)"); render(); return; }
    toast("Would call " + a.operation + " on the backend — not executed in the preview"); return; }
  if (a.kind === "script") {
    var draft = Object.assign({}, row), msgs = [], calls = [], failed = null;
    var api = {set: function (f, v) { draft[f] = normalize(n, f, v); }, call: function (op) { calls.push(op); return Promise.resolve(); }, message: function (m) { msgs.push(String(m)); }, fail: function (m) { failed = String(m); throw new Error("__fail"); }};
    var f = fnOf("row,api", a.script);
    try { if (f) { f(Object.assign({}, row), api); } } catch (e) { if (!failed) { failed = "Script error: " + e.message; } }
    if (failed) { toast(failed); return; }
    Object.keys(draft).forEach(function (k) { row[k] = draft[k]; });
    toast((msgs.concat(calls.map(function (c) { return "would call " + c; })).join(" · ")) || (a.label + " done (preview)")); render(); }
}

/* ---------- shell ---------- */
function render() {
  var nav = S.pages.filter(function (p) { return p.type !== "hidden"; }), multi = nav.length > 1;
  if (!cur) { cur = {page: firstPage()}; }
  var active = cur.page || cur.pid || "", main = "";
  try {
    if (cur.wiz) { main = renderWizard(pageById(cur.wiz.pid)); }
    else if (cur.detail) { var pg = cur.pid ? pageById(cur.pid) : null, n = cur.detail, row = byId(n, cur.id);
      main = row ? detailBody(pg || genericDetail(n), n, row, false) : '<div class="ph2">This record no longer exists. <span class="lnk" onclick="P.back()">Back</span></div>'; }
    else { var p = pageById(cur.page);
      if (!p) { main = '<div class="ph2">Nothing to show.</div>'; }
      else if (p.type === "list") { main = renderList(p); } else if (p.type === "dashboard") { main = renderDashboard(p); }
      else if (p.type === "kanban") { main = renderKanban(p); } else if (p.type === "calendar") { main = renderCalendar(p); }
      else if (p.type === "analytics") { main = renderAnalytics(p); }
      else { main = '<div class="ph"><h2>' + esc(p.title) + '</h2></div><div class="ph2">Page type <b>' + esc(p.kind || p.type) + "</b> is not part of the interactive preview — it is generated in the app.</div>"; } }
  } catch (e) { main = '<div class="ph2">This page could not be previewed: ' + esc(e.message) + "</div>"; err(e.message); }
  var real = (S.real || []).length;
  var a = document.getElementById("app");
  a.innerHTML = '<div class="shell"><div class="logo">' + esc((S.app.title || "A").charAt(0)) + "</div><b>" + esc(S.app.title) + '</b><span class="chip">' + esc(S.app.floorplan) + '</span><div class="sp">'
    + (real ? '<span class="chip real" title="' + esc(S.real.join(", ")) + '">sample rows: ' + real + " set(s)</span>" : "") + '<span class="chip mock">MOCK DATA</span><button class="btn g" onclick="P.reset()" title="Restore the original sample data">↺ Reset</button></div></div>'
    + '<div class="banner"><span>⚠</span><span>Preview with mock data — nothing is read from or sent to a backend. The deployed app uses the live service.</span>' + ((S.notes || []).length ? '<a onclick="P.notes()">' + S.notes.length + " preview note(s)</a>" : "") + '</div><div id="errbar" class="errbar"></div>'
    + '<div class="body">' + (multi ? '<div class="nav">' + nav.map(function (p) { return '<div class="' + (p.id === active ? "on" : "") + '" onclick="P.navTo(' + A(p.id) + ')">' + esc(p.title) + "</div>"; }).join("") + "</div>" : "") + '<div class="main">' + main + "</div></div>";
}

window.P = {
  navTo: navTo, back: back, openRow: openRow, openBy: openBy, close: closeModal,
  ok: function () { var cb = modalCb; closeModal(); if (cb) { cb(); } },
  sortBy: function (pid, i) { var pg = pageById(pid), c = columnsOf(pg)[i], u = ui(pid), s = u.sort || {f: (pg.sort || {}).field, d: (pg.sort || {}).descending};
    var same = c.c ? (s.c && s.c.id === c.c.id) : (!s.c && s.f === c.f); u.sort = {f: c.f, c: c.c, d: same ? !s.d : false}; render(); },
  rowClick: function (pid, id) { var pg = pageById(pid); if (pg.detail) { openRow(pg.set, id); } else { ui(pid).sel = id; refresh(pg); } },
  select: function (pid, id, full) { ui(pid).sel = id; var pg = pageById(pid); if (full || pg.layout === "split") { editing = null; render(); } else { refresh(pg); } },
  search: function (pid, v, full) { ui(pid).q = v; var pg = pageById(pid); if (full) { render(); } else { refresh(pg); } },
  filt: function (pid, f, v) { ui(pid).f[f] = v; var pg = pageById(pid); if (pg.layout === "split") { render(); } else { refresh(pg); } },
  clearF: function (pid) { ui(pid).f = {}; render(); },
  tab: function (pid, k) { ui(pid).tab = k; render(); }, view: function (pid, i) { ui(pid).view = i; render(); },
  chartFilter: function (pid, f, v) { var pg = pageById(pid), u = ui(pid); u.f[f] = v; if ((pg.filters || []).indexOf(f) < 0) { pg.filters.push(f); } render(); },
  drill: function (pid, f, v) { var pg = pageById(pid); if (!pg || pg.type !== "list") { navTo(pid); return; } var u = ui(pid); u.f = {}; u.f[f] = v; if ((pg.filters || []).indexOf(f) < 0) { pg.filters.push(f); } navTo(pid); },
  month: function (pid, d) { var u = ui(pid), t = toDate(S.today) || new Date(); if (!d) { u.month = [t.getFullYear(), t.getMonth()]; } else { var m = new Date(u.month[0], u.month[1] + d, 1); u.month = [m.getFullYear(), m.getMonth()]; } render(); },
  drag: function (ev, id) { ev.dataTransfer.setData("text/plain", String(id)); },
  over: function (ev, el) { ev.preventDefault(); el.classList.add("over"); },
  drop: function (ev, pid, val, el) { ev.preventDefault(); el.classList.remove("over"); var pg = pageById(pid), id = Number(ev.dataTransfer.getData("text/plain")), r = byId(pg.entity_set, id);
    if (r && String(r[pg.lane_field]) !== val) { r[pg.lane_field] = val; toast(lab(pg.entity_set, pg.lane_field) + " changed to " + val + " (preview only)"); } render(); },
  edit: function (pid, n, id) { editing = {pid: pid, set: n, id: id, draft: Object.assign({}, byId(n, id)), bad: null, msgs: null}; render(); },
  cancel: function () { editing = null; render(); },
  save: function () { var e = editing, pg = (e.pid && pageById(e.pid)) || genericDetail(e.set), box = document.getElementById("edbox"), row = byId(e.set, e.id);
    var d = readForm(box, e.set, e.draft), fields = [];
    box.querySelectorAll("[data-f]").forEach(function (el) { fields.push(el.getAttribute("data-f")); });
    (pg.derived || []).forEach(function (x) { var v = expr(x.expr, d); if (v !== undefined) { d[x.field] = normalize(e.set, x.field, v); } });
    var res = check(pg, e.set, d, fields);
    if (res.msgs.length) { e.draft = d; e.bad = res.bad; e.msgs = res.msgs; render(); return; }
    Object.keys(d).forEach(function (k) { row[k] = d[k]; }); editing = null; toast("Saved (in the preview only)"); render(); },
  del: function (pid, n, id) { confirmBox("Delete this " + setOf(n).label + "?", function () { S.data[n] = rows(n).filter(function (r) { return r.__id !== id; }); toast("Deleted (preview only)"); if (cur.detail) { back(); } else { render(); } }); },
  act: function (pid, n, aid, id) { var pg = pageById(pid) || genericDetail(n), a = (pg.actions || []).filter(function (x) { return x.id === aid; })[0], row = id ? byId(n, id) : {};
    if (!a) { return; } if (a.confirm) { confirmBox(a.label + "?", function () { runAction(pg, n, a, row); }); } else { runAction(pg, n, a, row); } },
  create: function (pid) { var pg = pageById(pid); if (pg.create === "wizard" && (pg.wizard_steps || []).length) { go({wiz: {pid: pid, step: 0, draft: newRow(pg)}}); } else { openCreate(pg); } },
  doCreate: function (pid) { var pg = pageById(pid), box = document.getElementById("cbox"), r = readForm(box, pg.set, modal.draft), fields = [];
    box.querySelectorAll("[data-f]").forEach(function (el) { fields.push(el.getAttribute("data-f")); });
    var res = finishCreate(pg, r, fields); if (res) { openCreate(pg, r, res.bad, res.msgs); return; }
    closeModal(); toast("Created (in the preview only)"); openRow(pg.set, r.__id); },
  wiz: function (d) { var w = cur.wiz, pg = pageById(w.pid), box = document.getElementById("cbox");
    if (box) { w.draft = readForm(box, pg.set, w.draft); }
    if (d > 0 && box) { var fs = pg.wizard_steps[w.step].fields, res = check(pg, pg.set, w.draft, fs);
      res.msgs = res.msgs.filter(function (m, i) { return true; });
      if (Object.keys(res.bad).length) { w.bad = res.bad; w.msgs = ["Complete this step first."]; render(); return; } }
    w.bad = null; w.msgs = null; w.step = Math.max(0, w.step + d); render(); },
  wizDone: function () { var w = cur.wiz, pg = pageById(w.pid), all = [];
    pg.wizard_steps.forEach(function (s) { all = all.concat(s.fields); });
    var res = finishCreate(pg, w.draft, all); if (res) { w.msgs = res.msgs; w.bad = res.bad; render(); return; }
    toast("Created (in the preview only)"); stack.pop(); cur = null; cur = {page: w.pid}; openRow(pg.set, w.draft.__id); },
  reset: function () { var o = JSON.parse(ORIG); S.data = o.d; S.children = o.c; editing = null; UI = {}; toast("Sample data restored"); render(); },
  notes: function () { modal('<div class="dlg"><header>Preview notes</header><div class="c"><ul class="notes">' + S.notes.map(function (n) { return "<li>" + esc(n) + "</li>"; }).join("") + '</ul></div><footer><button class="btn" onclick="P.close()">Close</button></footer></div>'); }
};
render();
})();
"""
