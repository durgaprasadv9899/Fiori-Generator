"""
standalone.py — "New application" mode: build an app WITHOUT an existing SAP service.

    requirement ──> (clarifying questions) ──> domain model (AI or rules, validated)
        ──> OData V4 EDMX (synthesised here, identical in shape to what CAP serves)
        ──> the normal studio pipeline (design / review / renderers) unchanged
        ──> + a CAP Node.js backend under backend/ (schema, service, workflow
            handlers, sample data) wired into the MTA and approuter.

Domain model (validated by validate_domain — every other function expects that shape):

{
  "name": "PurchaseRequest",                 # PascalCase base name (service = <name>Service)
  "title": "Purchase Requests", "description": "...",
  "root": "PurchaseRequests",                # main entity (plural = entity set = type name)
  "entities": [{
      "name": "PurchaseRequests", "label": "Purchase Request", "title_field": "title",
      "fields": [{"name", "type": String|LargeString|Integer|Decimal|Date|DateTime|Boolean|UUID,
                  "label", "length", "precision", "scale", "required", "default", "enum": [...],
                  "min", "max", "readonly", "formula": {"kind": "product", "fields": [a, b]}
                                                  | {"kind": "sum", "composition": c, "field": f}}],
      "compositions": [{"name": "items", "target": "PurchaseRequestItems", "label": "Items"}],
      "associations": [{"name": "costCenter", "target": "CostCenters", "label": "Cost Center"}]}],
  "workflow": None | {"entity", "status_field": "status", "states": [{"code", "label", "criticality"}],
      "initial": "Draft", "editable_states": [...], "segregation_of_duties": bool,
      "enforce_roles": bool, "auto_approve_below": None | {"field", "value"},
      "transitions": [{"action", "label", "from": [...], "to", "role", "comment": none|optional|required,
                       "kind": submit|decide|withdraw|other}]},
  "rules": [{"entity", "field", "check": required|min|max|future|past|max_length, "value", "message"}],
  "roles": ["Requester", "Approver"],
  "sample_data": {"PurchaseRequests": [{field: value, "items": [{...}]}]}
}

The workflow is enforced by the backend (srv/service.js): actions only in their valid
states (409 otherwise), comments where required, edits only in editable states, an
approval history row per step, optional role checks and segregation of duties.
The same valid states travel in the EDMX (Evoke.StateGuard) so the generated UI only
offers an action when the backend will accept it.
"""

from __future__ import annotations

import csv
import io
import json
import re
import uuid
from typing import Callable, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape as _xesc

AiFn = Callable[[str, str], str]

TYPES = ("String", "LargeString", "Integer", "Decimal", "Date", "DateTime", "Boolean", "UUID")
_TYPE_ALIASES = {
    "string": "String", "text": "String", "char": "String", "varchar": "String", "email": "String",
    "phone": "String", "url": "String", "code": "String", "enum": "String",
    "largestring": "LargeString", "longtext": "LargeString", "memo": "LargeString", "note": "LargeString",
    "integer": "Integer", "int": "Integer", "int32": "Integer", "int64": "Integer", "number": "Integer",
    "count": "Integer",
    "decimal": "Decimal", "amount": "Decimal", "money": "Decimal", "currencyamount": "Decimal",
    "float": "Decimal", "double": "Decimal", "price": "Decimal", "quantity": "Decimal",
    "date": "Date", "datetime": "DateTime", "timestamp": "DateTime", "time": "DateTime",
    "datetimeoffset": "DateTime",
    "boolean": "Boolean", "bool": "Boolean", "flag": "Boolean",
    "uuid": "UUID", "guid": "UUID",
}
_EDM = {"String": "Edm.String", "LargeString": "Edm.String", "Integer": "Edm.Int32",
        "Decimal": "Edm.Decimal", "Date": "Edm.Date", "DateTime": "Edm.DateTimeOffset",
        "Boolean": "Edm.Boolean", "UUID": "Edm.Guid"}
MANAGED = [("createdAt", "Created On"), ("createdBy", "Created By"),
           ("modifiedAt", "Changed On"), ("modifiedBy", "Changed By")]
_RESERVED = {"ID", "createdAt", "createdBy", "modifiedAt", "modifiedBy", "parent", "parent_ID",
             "history", "up_", "in", "entity", "type", "key", "namespace", "service", "using", "as",
             "on", "many", "one", "to", "of", "where", "select", "from", "action", "function",
             "default", "null", "not", "and", "or", "true", "false"}
WF_FIELDS = [("submittedAt", "DateTime", "Submitted On"), ("decidedBy", "String", "Decided By"),
             ("decidedAt", "DateTime", "Decided On"), ("decisionNote", "String", "Decision Note")]
CRITICALITY = ("Neutral", "Information", "Positive", "Critical", "Negative")
CHECKS = ("required", "min", "max", "future", "past", "max_length")
DEFAULT_ROLES = {"approve": "Approver", "reject": "Approver", "rejectRequest": "Approver"}
# methods of cds.ApplicationService — an action with such a name would shadow them
_CAP_METHODS = {"reject", "error", "info", "warn", "notify", "emit", "send", "run", "read", "create", "insert",
                "upsert", "update", "delete", "init", "on", "before", "after", "handle", "dispatch", "tx",
                "get", "put", "post", "patch", "schedule", "prepend", "connect", "disconnect", "entities"}


# ─────────────────────────────────────────────────────────────────────────────
# Names
# ─────────────────────────────────────────────────────────────────────────────
def _words(text: str) -> List[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(text or ""))
    return [w for w in re.split(r"[^A-Za-z0-9]+", text) if w]


def pascal(text: str, fallback: str = "Item") -> str:
    out = "".join(w[:1].upper() + w[1:] for w in _words(text))
    out = re.sub(r"^[0-9]+", "", out)
    return out or fallback


def camel(text: str, fallback: str = "field") -> str:
    p = pascal(text, "")
    if not p:
        return fallback
    # keep acronyms readable: "ID" stays, "VATRate" -> "vatRate"
    m = re.match(r"^([A-Z]+)([A-Z][a-z].*)$", p)
    if m and len(m.group(1)) > 1:
        return m.group(1).lower() + m.group(2)
    return p[:1].lower() + p[1:]


def plural(name: str) -> str:
    if re.search(r"(s|History|Data|Info|Equipment|Staff)$", name):
        return name
    if re.search(r"[^aeiou]y$", name):
        return name[:-1] + "ies"
    if re.search(r"(x|ch|sh)$", name):
        return name + "es"
    return name + "s"


def singular(name: str) -> str:
    if name.endswith("ies"):
        return name[:-3] + "y"
    if re.search(r"(xes|ches|shes)$", name):
        return name[:-2]
    if name.endswith("s") and not name.endswith("ss"):
        return name[:-1]
    return name


def human(name: str) -> str:
    return " ".join(w[:1].upper() + w[1:] for w in _words(name)) or name


def _kebab(name: str) -> str:
    return "-".join(w.lower() for w in _words(name)) or "app"


def service_name(domain: Dict) -> str:
    return domain["name"] + "Service"


def namespace(domain: Dict) -> str:
    return "evoke." + domain["name"].lower()


def service_path(domain: Dict) -> str:
    """Where CAP serves the service (fixed by @path in srv/service.cds)."""
    return f"/odata/v4/{_kebab(domain['name'])}/"


def history_entity(domain: Dict) -> str:
    return domain["name"] + "History"


# ─────────────────────────────────────────────────────────────────────────────
# Validation — turns any (AI) JSON into a consistent, generatable domain
# ─────────────────────────────────────────────────────────────────────────────
def _num(v, default=None):
    try:
        return float(v) if "." in str(v) else int(v)
    except (TypeError, ValueError):
        return default


def _coerce(value, f: Dict):
    """Value for field f, or None when it does not fit the type."""
    if value is None or value == "":
        return None
    t = f["type"]
    if isinstance(value, str) and value.startswith("$"):
        return value if value in ("$today", "$now", "$user") else None
    if t in ("Integer",):
        n = _num(value)
        return int(n) if n is not None else None
    if t == "Decimal":
        n = _num(value)
        return n
    if t == "Boolean":
        return value if isinstance(value, bool) else str(value).lower() in ("true", "1", "yes", "x")
    if t == "Date":
        m = re.match(r"^\d{4}-\d{2}-\d{2}", str(value))
        return m.group(0) if m else None
    if t == "DateTime":
        m = re.match(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?", str(value))
        if not m:
            return None
        v = m.group(0)
        return v + ("T00:00:00Z" if "T" not in v else ("Z" if v.count(":") == 2 else ":00Z"))
    if t == "UUID":
        return str(value) if re.match(r"^[0-9a-fA-F-]{36}$", str(value)) else None
    s = str(value)
    if f.get("enum") and s not in f["enum"]:
        return None
    return s[: f.get("length") or 5000]


def _field(raw, used: set, note, ent: str) -> Optional[Dict]:
    if isinstance(raw, str):
        raw = {"name": raw}
    if not isinstance(raw, dict):
        return None
    name = camel(raw.get("name") or raw.get("label") or "")
    if name in _RESERVED or name.lower() in {u.lower() for u in used} or name.endswith("_ID"):
        if name not in ("ID",) and name not in MANAGED:
            note(f"{ent}: field '{raw.get('name')}' skipped (reserved or duplicate name).")
        return None
    t = str(raw.get("type") or "String")
    t = t if t in TYPES else _TYPE_ALIASES.get(re.sub(r"[^a-z0-9]", "", t.lower()).split("(")[0], "")
    if not t:
        note(f"{ent}.{name}: type '{raw.get('type')}' unknown — String used.")
        t = "String"
    f = {"name": name, "type": t, "label": str(raw.get("label") or human(name))[:60],
         "required": bool(raw.get("required")), "readonly": bool(raw.get("readonly"))}
    if t == "String":
        f["length"] = max(1, min(int(_num(raw.get("length") or raw.get("max_length"), 100) or 100), 5000))
    if t == "Decimal":
        f["precision"] = max(3, min(int(_num(raw.get("precision"), 15) or 15), 34))
        f["scale"] = max(0, min(int(_num(raw.get("scale"), 2) or 2), f["precision"] - 1))
    enum = [str(v)[:60] for v in (raw.get("enum") or raw.get("values") or []) if str(v).strip()]
    if enum and t == "String":
        f["enum"] = list(dict.fromkeys(enum))[:30]
        f["length"] = max(f["length"], max(len(v) for v in f["enum"]))
    for k in ("min", "max"):
        if raw.get(k) is not None and t in ("Integer", "Decimal") and _num(raw.get(k)) is not None:
            f[k] = _num(raw.get(k))
    if raw.get("default") is not None:
        d = _coerce(raw.get("default"), f)
        if d is not None:
            f["default"] = d
    if isinstance(raw.get("formula"), dict):
        f["formula"] = dict(raw["formula"])
    used.add(name)
    return f


def _sanitize_workflow(raw, entities: Dict[str, Dict], root: str, note) -> Optional[Dict]:
    if not raw:
        return None
    if raw is True or isinstance(raw, str):
        raw = {}
    if not isinstance(raw, dict):
        return None
    ent = raw.get("entity") if raw.get("entity") in entities else root
    base = approval_workflow()
    states_raw = raw.get("states") or base["states"]
    states = []
    for s in states_raw:
        s = {"code": s} if isinstance(s, str) else (s if isinstance(s, dict) else {})
        code = re.sub(r"[^\w -]", "", str(s.get("code") or s.get("label") or "")).strip()[:20]
        if code and code not in [x["code"] for x in states]:
            crit = s.get("criticality") if s.get("criticality") in CRITICALITY else _criticality(code)
            states.append({"code": code, "label": str(s.get("label") or code)[:40], "criticality": crit})
    if len(states) < 2:
        note("Workflow: fewer than two states — the standard approval states are used.")
        states = base["states"]
    codes = [s["code"] for s in states]
    initial = raw.get("initial") if raw.get("initial") in codes else codes[0]
    trans = []
    for t in raw.get("transitions") or base["transitions"]:
        if not isinstance(t, dict):
            continue
        action = camel(t.get("action") or t.get("label") or "", "")
        if action in _CAP_METHODS:          # e.g. "reject" would shadow cds.Service.reject()
            action += "Request"
        frm = [c for c in (t.get("from") or []) if c in codes] if isinstance(t.get("from"), list) else \
            [c for c in [t.get("from")] if c in codes]
        to = t.get("to") if t.get("to") in codes else ""
        if not action or action in _RESERVED or action in [x["action"] for x in trans] or not frm or not to:
            note(f"Workflow transition '{t.get('action') or t.get('label')}' dropped — needs a unique name and "
                 f"existing from/to states.")
            continue
        comment = t.get("comment") if t.get("comment") in ("none", "optional", "required") else \
            ("required" if re.search(r"reject|return|declin", action, re.I) else "optional")
        kind = t.get("kind") if t.get("kind") in ("submit", "decide", "withdraw", "other") else (
            "submit" if re.search(r"submit|send|request", action, re.I) else
            "decide" if re.search(r"approv|reject|declin|release|accept", action, re.I) else
            "withdraw" if re.search(r"withdraw|recall|cancel|retract", action, re.I) else "other")
        role = pascal(t.get("role") or "", "") if t.get("role") else DEFAULT_ROLES.get(action, "")
        if kind != "decide":
            role = ""                       # roles guard decisions only; any signed-in user may submit
        trans.append({"action": action, "label": str(t.get("label") or human(action))[:30], "from": frm,
                      "to": to, "role": role, "comment": comment, "kind": kind})
    if not trans:
        note("Workflow: no usable transitions — the standard submit/approve/reject/withdraw are used.")
        trans = [t for t in base["transitions"] if t["from"][0] in codes and t["to"] in codes]
    editable = [c for c in (raw.get("editable_states") or []) if c in codes] or \
        [c for c in codes if c in ("Draft", "Rejected", "New", "Open")] or [initial]
    auto = raw.get("auto_approve_below")
    ent_fields = {f["name"]: f for f in entities[ent]["fields"]}
    if isinstance(auto, dict) and auto.get("field") in ent_fields and \
            ent_fields[auto["field"]]["type"] in ("Integer", "Decimal") and _num(auto.get("value")) is not None:
        auto = {"field": auto["field"], "value": _num(auto["value"])}
    else:
        auto = None
    return {"entity": ent, "status_field": "status", "states": states, "initial": initial,
            "editable_states": editable, "transitions": trans,
            "segregation_of_duties": bool(raw.get("segregation_of_duties")),
            "enforce_roles": bool(raw.get("enforce_roles")), "auto_approve_below": auto}


def _criticality(code: str) -> str:
    c = code.lower()
    if re.search(r"approv|done|closed|complete|resolved|released|accepted", c):
        return "Positive"
    if re.search(r"reject|cancel|declin|fail", c):
        return "Negative"
    if re.search(r"submit|pending|review|progress|wait|open", c):
        return "Critical"
    return "Neutral" if re.search(r"draft|new", c) else "Information"


def validate_domain(domain: Dict) -> Tuple[Dict, List[str]]:
    """Sanitise an AI/rule domain into the shape documented above. Never raises."""
    notes: List[str] = []
    note = notes.append
    d = domain if isinstance(domain, dict) else {}
    name = pascal(singular(pascal(d.get("name") or d.get("title") or "", "")), "App")[:40]
    title = str(d.get("title") or human(plural(name)))[:60]

    ents_raw = [e for e in (d.get("entities") or []) if isinstance(e, dict)][:12]
    if not ents_raw:
        note("The domain had no entities — a generic header/items model is used.")
        ents_raw = _template("generic", name, title)["entities"]
    entities: Dict[str, Dict] = {}
    rename: Dict[str, str] = {}
    for e in ents_raw:
        en = plural(pascal(e.get("name") or e.get("label") or "", "Item"))[:40]
        if en == history_entity({"name": name}) or en in entities:
            note(f"Entity '{e.get('name')}' skipped (duplicate or reserved name).")
            continue
        rename[str(e.get("name"))] = en
        rename[en] = en
        rename[singular(en)] = en
        used: set = set()
        fields = [f for f in (_field(x, used, note, en) for x in (e.get("fields") or [])) if f]
        if not fields:
            fields = [_field({"name": "name", "type": "String", "required": True}, used, note, en)]
        entities[en] = {"name": en, "label": str(e.get("label") or human(singular(en)))[:60],
                        "fields": fields, "_comp": e.get("compositions") or [],
                        "_assoc": e.get("associations") or [], "_title": e.get("title_field")}

    def target(t):
        t = str(t or "")
        return rename.get(t) or rename.get(plural(pascal(t, ""))) or ""

    composed: Dict[str, str] = {}
    for en, e in entities.items():
        comps, assocs = [], []
        used = {f["name"] for f in e["fields"]}
        for c in e.pop("_comp"):
            c = {"target": c} if isinstance(c, str) else (c if isinstance(c, dict) else {})
            tg = target(c.get("target") or c.get("entity"))
            if not tg or tg == en or tg in composed:
                note(f"{en}: composition '{c.get('name') or c.get('target')}' dropped "
                     f"({'unknown target' if not tg else 'target already belongs to ' + composed.get(tg, en)}).")
                continue
            cname = camel(c.get("name") or tg)
            if cname in used or cname in _RESERVED:
                cname = camel(tg)
            used.add(cname)
            composed[tg] = en
            comps.append({"name": cname, "target": tg, "label": str(c.get("label") or human(tg))[:60]})
        for a in e.pop("_assoc"):
            a = {"target": a} if isinstance(a, str) else (a if isinstance(a, dict) else {})
            tg = target(a.get("target") or a.get("entity"))
            if not tg or tg == en:
                note(f"{en}: association '{a.get('name') or a.get('target')}' dropped (unknown target).")
                continue
            aname = camel(a.get("name") or singular(tg))
            if aname in used or aname in _RESERVED:
                note(f"{en}: association '{aname}' dropped (name clash).")
                continue
            used.add(aname)
            assocs.append({"name": aname, "target": tg, "label": str(a.get("label") or human(singular(tg)))[:60]})
        e["compositions"], e["associations"] = comps, assocs
    for en, e in entities.items():
        strings = [f["name"] for f in e["fields"] if f["type"] == "String"]
        tf = e.pop("_title")
        e["title_field"] = tf if tf in strings else next(
            (s for s in strings if re.search(r"(title|name|subject|description)$", s, re.I)), strings[0] if strings else "")
        e["parent"] = composed.get(en, "")

    root = target(d.get("root")) if target(d.get("root")) in entities and not composed.get(target(d.get("root"))) \
        else next((n for n in entities if not composed.get(n)), next(iter(entities)))
    # formulas
    for en, e in entities.items():
        fmap = {f["name"]: f for f in e["fields"]}
        for f in e["fields"]:
            fo = f.pop("formula", None)
            if not fo:
                continue
            if f["type"] not in ("Integer", "Decimal"):
                note(f"{en}.{f['name']}: formula ignored — only numeric fields can be calculated.")
                continue
            if fo.get("kind") == "product" and len([x for x in fo.get("fields") or [] if
                                                    fmap.get(x, {}).get("type") in ("Integer", "Decimal")
                                                    and x != f["name"]]) >= 2:
                f["formula"] = {"kind": "product", "fields": [x for x in fo["fields"] if x in fmap and x != f["name"]][:3]}
            elif fo.get("kind") == "sum":
                comp = next((c for c in e["compositions"] if c["name"] == fo.get("composition")
                             or c["target"] == target(fo.get("composition"))), None)
                tf = {x["name"]: x for x in entities[comp["target"]]["fields"]} if comp else {}
                if comp and tf.get(fo.get("field"), {}).get("type") in ("Integer", "Decimal"):
                    f["formula"] = {"kind": "sum", "composition": comp["name"], "field": fo["field"]}
                else:
                    note(f"{en}.{f['name']}: sum formula ignored — unknown composition/field.")
            else:
                note(f"{en}.{f['name']}: formula ignored — use product of fields or sum over items.")
            if f.get("formula"):
                f["readonly"], f["required"] = True, False

    wf = _sanitize_workflow(d.get("workflow"), entities, root, note)
    if wf:
        names = {f["name"] for f in entities[wf["entity"]]["fields"]}
        for fname in ["status"] + [x[0] for x in WF_FIELDS]:
            if fname in names:
                entities[wf["entity"]]["fields"] = [f for f in entities[wf["entity"]]["fields"] if f["name"] != fname]
                note(f"{wf['entity']}.{fname}: replaced by the workflow's own field.")
    rules = []
    for r in d.get("rules") or []:
        if not isinstance(r, dict):
            continue
        en = target(r.get("entity")) or root
        fmap = {f["name"]: f for f in entities.get(en, {}).get("fields", [])}
        fld = r.get("field") if r.get("field") in fmap else camel(r.get("field") or "")
        chk = r.get("check")
        if fld not in fmap or chk not in CHECKS:
            note(f"Rule '{r.get('message') or r}' dropped — unknown field or check.")
            continue
        ft = fmap[fld]["type"]
        if (chk in ("min", "max") and (ft not in ("Integer", "Decimal") or _num(r.get("value")) is None)) or \
                (chk in ("future", "past") and ft not in ("Date", "DateTime")) or \
                (chk == "max_length" and (ft != "String" or _num(r.get("value")) is None)):
            note(f"Rule '{r.get('message') or chk}' dropped — does not fit {en}.{fld} ({ft}).")
            continue
        msg = str(r.get("message") or _rule_message(chk, fmap[fld]["label"], r.get("value")))[:200]
        rules.append({"entity": en, "field": fld, "check": chk, "value": _num(r.get("value")), "message": msg})
    roles = sorted({pascal(x, "") for x in (d.get("roles") or []) if pascal(str(x), "")} |
                   {t["role"] for t in (wf or {}).get("transitions", []) if t["role"]})
    out = {"name": name, "title": title, "description": str(d.get("description") or "")[:500],
           "root": root, "entities": list(entities.values()), "workflow": wf, "rules": rules, "roles": roles}
    out["sample_data"] = _sample_data(out, d.get("sample_data") or {}, note)
    return out, notes


def _rule_message(chk: str, label: str, value) -> str:
    return {"required": f"{label} is required.", "min": f"{label} must be at least {value}.",
            "max": f"{label} must not exceed {value}.", "future": f"{label} must not be in the past.",
            "past": f"{label} must not be in the future.",
            "max_length": f"{label} must not be longer than {value} characters."}[chk]


# ─────────────────────────────────────────────────────────────────────────────
# Sample data
# ─────────────────────────────────────────────────────────────────────────────
_FAKE = {
    r"(title|subject|name)$": ["Laptop replacement", "Office chairs", "Conference travel", "Software licences",
                              "Monitor upgrade", "Team offsite"],
    r"(description|reason|justification|note|comment)": ["Needed for the new team members.",
                                                          "Replacement of broken equipment.",
                                                          "Annual renewal.", "Requested by the department head."],
    r"product|material|article": ["Notebook 14\"", "Docking station", "Desk chair", "27\" monitor", "Headset"],
    r"customer|supplier|vendor|partner": ["Acme Corp", "Globex GmbH", "Initech Ltd", "Umbrella SA"],
    r"department|team|org": ["Finance", "Sales", "IT", "Operations"],
    r"cost.?center": ["CC-1000", "CC-2100", "CC-3300"],
    r"currency": ["EUR", "USD"],
    r"unit|uom": ["EA", "PC"],
    r"email": ["anna@example.com", "ben@example.com"],
    r"employee|requester|owner|assignee|person": ["Anna Schmidt", "Ben Carter", "Chen Li"],
    r"category|type|kind": ["Hardware", "Software", "Services"],
}


def _fake(f: Dict, i: int):
    if f.get("formula") or f.get("readonly"):
        return None
    if f.get("enum"):
        return f["enum"][i % len(f["enum"])]
    if f.get("default") not in (None, "") and not str(f["default"]).startswith("$"):
        return f["default"]
    t, n = f["type"], f["name"]
    if t == "String":
        for pat, vals in _FAKE.items():
            if re.search(pat, n, re.I):
                return vals[i % len(vals)][: f.get("length") or 100]
        return f"{f['label']} {i + 1}"[: f.get("length") or 100]
    if t == "LargeString":
        return f"{f['label']} — sample text {i + 1}."
    if t == "Integer":
        lo = int(f.get("min", 1) or 1)
        return lo + (i * 3) % 10
    if t == "Decimal":
        base = 2 if re.search(r"quantity|qty", n, re.I) else 125.5
        return round(max(float(f.get("min", 0) or 0), base * (i % 4 + 1)), f.get("scale", 2))
    if t == "Date":
        return f"2026-{(i % 12) + 1:02d}-{(i * 7) % 27 + 1:02d}"
    if t == "DateTime":
        return f"2026-{(i % 12) + 1:02d}-{(i * 7) % 27 + 1:02d}T09:00:00Z"
    if t == "Boolean":
        return i % 2 == 0
    return None


def _sample_data(domain: Dict, raw: Dict, note) -> Dict[str, List[Dict]]:
    """Root rows with nested composition rows (validated against the fields; synthesised
    when the design gives none). Status values must be workflow states."""
    ents = {e["name"]: e for e in domain["entities"]}
    wf = domain.get("workflow")

    def clean(en: str, row: Dict, i: int, depth: int) -> Dict:
        e = ents[en]
        out = {}
        for f in e["fields"]:
            v = _coerce(row.get(f["name"]), f) if isinstance(row, dict) else None
            if isinstance(v, str) and v.startswith("$"):
                v = None
            if v is None and not row:
                v = _fake(f, i)
            if v is not None and not f.get("formula"):
                out[f["name"]] = v
        if wf and wf["entity"] == en:
            codes = [s["code"] for s in wf["states"]]
            st = row.get("status") if isinstance(row, dict) else None
            out["status"] = st if st in codes else (codes[i % len(codes)] if not row else wf["initial"])
        if depth < 2:
            for c in e["compositions"]:
                kids = row.get(c["name"]) if isinstance(row, dict) else None
                if not isinstance(kids, list) or not kids:
                    kids = [{}] * (2 if not row else 0)
                out[c["name"]] = [clean(c["target"], k if isinstance(k, dict) else {}, i + j, depth + 1)
                                  for j, k in enumerate(kids[:10])]
        return out

    rows = raw.get(domain["root"]) if isinstance(raw, dict) else None
    if not isinstance(rows, list) or not rows:
        rows = [{}] * 4
    roots = [clean(domain["root"], r if isinstance(r, dict) else {}, i, 0) for i, r in enumerate(rows[:20])]
    out = {domain["root"]: roots}
    for en, e in ents.items():           # other top-level entities (e.g. cost centers)
        if en == domain["root"] or e["parent"]:
            continue
        rr = raw.get(en) if isinstance(raw, dict) else None
        if not isinstance(rr, list) or not rr:
            rr = [{}] * 3
        out[en] = [clean(en, r if isinstance(r, dict) else {}, i, 0) for i, r in enumerate(rr[:20])]
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Rule-based domains
# ─────────────────────────────────────────────────────────────────────────────
def approval_workflow(entity: str = "") -> Dict:
    return {
        "entity": entity, "status_field": "status", "initial": "Draft",
        "states": [{"code": "Draft", "label": "Draft", "criticality": "Neutral"},
                   {"code": "Submitted", "label": "Awaiting Approval", "criticality": "Critical"},
                   {"code": "Approved", "label": "Approved", "criticality": "Positive"},
                   {"code": "Rejected", "label": "Rejected", "criticality": "Negative"}],
        "editable_states": ["Draft", "Rejected"],
        "transitions": [
            {"action": "submit", "label": "Submit", "from": ["Draft", "Rejected"], "to": "Submitted",
             "role": "", "comment": "optional", "kind": "submit"},
            {"action": "approve", "label": "Approve", "from": ["Submitted"], "to": "Approved",
             "role": "Approver", "comment": "optional", "kind": "decide"},
            {"action": "reject", "label": "Reject", "from": ["Submitted"], "to": "Rejected",
             "role": "Approver", "comment": "required", "kind": "decide"},
            {"action": "withdraw", "label": "Withdraw", "from": ["Submitted"], "to": "Draft",
             "role": "", "comment": "none", "kind": "withdraw"}],
        "segregation_of_duties": False, "enforce_roles": False, "auto_approve_below": None,
    }


def _lifecycle_workflow() -> Dict:
    return {
        "status_field": "status", "initial": "New",
        "states": [{"code": "New", "criticality": "Information"}, {"code": "InProgress", "label": "In Progress",
                                                                   "criticality": "Critical"},
                   {"code": "Resolved", "criticality": "Positive"}, {"code": "Closed", "criticality": "Neutral"}],
        "editable_states": ["New", "InProgress"],
        "transitions": [
            {"action": "start", "label": "Start Work", "from": ["New"], "to": "InProgress", "comment": "none"},
            {"action": "resolve", "label": "Resolve", "from": ["InProgress"], "to": "Resolved", "comment": "required"},
            {"action": "close", "label": "Close", "from": ["Resolved"], "to": "Closed", "comment": "none"},
            {"action": "reopen", "label": "Reopen", "from": ["Resolved", "Closed"], "to": "InProgress",
             "comment": "required"}],
    }


def _F(name, type_="String", **kw):
    return dict(kw, name=name, type=type_)


_ITEMS = [_F("product", required=True, label="Product / Service", length=80), _F("quantity", "Decimal", precision=13,
          scale=3, required=True, min=0.001, default=1), _F("unit", length=3, default="EA"),
          _F("unitPrice", "Decimal", required=True, min=0), _F("netAmount", "Decimal", label="Net Amount",
          formula={"kind": "product", "fields": ["quantity", "unitPrice"]})]


def _template(kind: str, name: str = "", title: str = "") -> Dict:
    if kind == "purchase":
        return {"name": "PurchaseRequest", "title": "Purchase Requests", "root": "PurchaseRequests", "entities": [
            {"name": "PurchaseRequests", "label": "Purchase Request", "title_field": "title", "fields": [
                _F("title", required=True, length=100), _F("description", "LargeString", label="Justification"),
                _F("department", enum=["Finance", "Sales", "IT", "Operations", "HR"], required=True),
                _F("costCenter", label="Cost Center", length=10), _F("neededBy", "Date", label="Needed By"),
                _F("priority", enum=["Low", "Medium", "High"], default="Medium"),
                _F("currency", length=3, default="EUR"),
                _F("totalAmount", "Decimal", label="Total Amount",
                   formula={"kind": "sum", "composition": "items", "field": "netAmount"})],
             "compositions": [{"name": "items", "target": "PurchaseRequestItems", "label": "Items"}]},
            {"name": "PurchaseRequestItems", "label": "Item", "fields": _ITEMS}],
            "rules": [{"entity": "PurchaseRequests", "field": "neededBy", "check": "future",
                       "message": "The needed-by date must not be in the past."}],
            "workflow": approval_workflow()}
    if kind == "sales":
        return {"name": "SalesOrder", "title": "Sales Orders", "root": "SalesOrders", "entities": [
            {"name": "SalesOrders", "label": "Sales Order", "title_field": "customer", "fields": [
                _F("customer", required=True, length=80), _F("customerReference", label="Customer Reference", length=35),
                _F("orderDate", "Date", label="Order Date", default="$today"),
                _F("requestedDeliveryDate", "Date", label="Requested Delivery"),
                _F("salesOrganization", label="Sales Organization", enum=["1000", "2000", "3000"]),
                _F("currency", length=3, default="EUR"),
                _F("netAmount", "Decimal", label="Net Amount",
                   formula={"kind": "sum", "composition": "items", "field": "netAmount"})],
             "compositions": [{"name": "items", "target": "SalesOrderItems", "label": "Items"}]},
            {"name": "SalesOrderItems", "label": "Item", "fields": _ITEMS}],
            "rules": [{"entity": "SalesOrders", "field": "requestedDeliveryDate", "check": "future",
                       "message": "The requested delivery date must not be in the past."}],
            "workflow": approval_workflow()}
    if kind == "leave":
        return {"name": "LeaveRequest", "title": "Leave Requests", "root": "LeaveRequests", "entities": [
            {"name": "LeaveRequests", "label": "Leave Request", "title_field": "employee", "fields": [
                _F("employee", required=True, length=80), _F("leaveType", label="Leave Type", required=True,
                                                             enum=["Vacation", "Sick", "Training", "Unpaid"]),
                _F("startDate", "Date", label="From", required=True), _F("endDate", "Date", label="To", required=True),
                _F("days", "Decimal", precision=5, scale=1, min=0.5, required=True),
                _F("reason", "LargeString")]}],
            "rules": [{"entity": "LeaveRequests", "field": "startDate", "check": "future",
                       "message": "Leave cannot start in the past."}],
            "workflow": approval_workflow()}
    if kind == "ticket":
        return {"name": "Ticket", "title": "Service Tickets", "root": "Tickets", "entities": [
            {"name": "Tickets", "label": "Ticket", "title_field": "subject", "fields": [
                _F("subject", required=True, length=120), _F("description", "LargeString"),
                _F("priority", enum=["Low", "Medium", "High", "Very High"], default="Medium", required=True),
                _F("category", enum=["Hardware", "Software", "Access", "Other"]),
                _F("assignee", length=80), _F("dueDate", "Date", label="Due Date")],
             "compositions": [{"name": "comments", "target": "TicketComments", "label": "Comments"}]},
            {"name": "TicketComments", "label": "Comment", "fields": [_F("text", "LargeString", required=True)]}],
            "workflow": _lifecycle_workflow()}
    if kind == "expense":
        return {"name": "ExpenseReport", "title": "Expense Reports", "root": "ExpenseReports", "entities": [
            {"name": "ExpenseReports", "label": "Expense Report", "title_field": "purpose", "fields": [
                _F("purpose", required=True, length=100), _F("employee", required=True, length=80),
                _F("tripStart", "Date", label="Trip Start"), _F("tripEnd", "Date", label="Trip End"),
                _F("currency", length=3, default="EUR"),
                _F("totalAmount", "Decimal", label="Total", formula={"kind": "sum", "composition": "expenses",
                                                                     "field": "amount"})],
             "compositions": [{"name": "expenses", "target": "ExpenseItems", "label": "Expenses"}]},
            {"name": "ExpenseItems", "label": "Expense", "fields": [
                _F("expenseDate", "Date", label="Date", required=True),
                _F("category", enum=["Travel", "Hotel", "Meals", "Other"], required=True),
                _F("description", length=120), _F("amount", "Decimal", required=True, min=0)]}],
            "workflow": approval_workflow()}
    base = pascal(name or "Request", "Request")
    return {"name": base, "title": title or human(plural(base)), "root": plural(base), "entities": [
        {"name": plural(base), "label": human(base), "title_field": "title", "fields": [
            _F("title", required=True, length=100), _F("description", "LargeString"),
            _F("category", length=40), _F("dueDate", "Date", label="Due Date"),
            _F("amount", "Decimal", label="Amount", formula={"kind": "sum", "composition": "items",
                                                             "field": "netAmount"})],
         "compositions": [{"name": "items", "target": base + "Items",
                           "label": "Items"}]},
        {"name": base + "Items", "label": "Item", "fields": _ITEMS}]}


def _rules_domain(requirement: str) -> Dict:
    r = requirement.lower()
    kind = ("purchase" if re.search(r"purchas\w* (request|requisition)|requisition|procure", r) else
            "sales" if re.search(r"sales ?order|customer order", r) else
            "leave" if re.search(r"leave|vacation|absence|time.?off", r) else
            "ticket" if re.search(r"ticket|incident|helpdesk|help desk|issue track|service desk", r) else
            "expense" if re.search(r"expense|travel claim|reimburse", r) else "generic")
    name = ""
    if kind == "generic":
        m = re.search(r"(?:an?|the)\s+([a-z][a-z ]{2,30}?)\s+(?:app|application|tracker|management|system|portal|"
                      r"register|tool)\b", r)
        name = singular(pascal(m.group(1))) if m else "Request"
    d = _template(kind, name)
    approval = re.search(r"approv|sign.?off|release|authori[sz]", r)
    if kind == "generic" and approval:
        d["workflow"] = approval_workflow()
    if kind not in ("generic", "ticket") and not approval:
        d["workflow"] = approval_workflow()     # these documents are approval processes by nature
    if d.get("workflow") and re.search(r"segregation|four.?eyes|own request|cannot approve (their|his|her) own", r):
        d["workflow"]["segregation_of_duties"] = True
    if d.get("workflow") and re.search(r"role|only managers|only approvers|authori[sz]ation", r):
        d["workflow"]["enforce_roles"] = True
    m = re.search(r"(?:below|under|less than)\s*(?:€|\$|eur|usd)?\s*([0-9][0-9,.]*)", r)
    tot = next((f["name"] for f in d["entities"][0]["fields"] if f.get("formula", {}).get("kind") == "sum"), "")
    if d.get("workflow") and m and tot and re.search(r"auto", r):
        d["workflow"]["auto_approve_below"] = {"field": tot, "value": float(m.group(1).replace(",", ""))}
    d["description"] = requirement[:300]
    return d


# ─────────────────────────────────────────────────────────────────────────────
# AI
# ─────────────────────────────────────────────────────────────────────────────
_DOMAIN_SYSTEM = """You design the data model and business process of a NEW SAP CAP application from a requirement.
Reply with STRICT JSON only:
{"name": "PascalCase singular business object, e.g. PurchaseRequest",
 "title": "app title", "description": "one sentence",
 "root": "PurchaseRequests",
 "entities": [{"name": "PluralPascalCase", "label": "Singular label", "title_field": "field",
   "fields": [{"name": "camelCase", "type": "String|LargeString|Integer|Decimal|Date|DateTime|Boolean",
               "label": "...", "length": 100, "precision": 15, "scale": 2, "required": true,
               "default": "value | $today | $now | $user", "enum": ["fixed", "values"], "min": 0, "max": 100,
               "formula": {"kind": "product", "fields": ["quantity", "unitPrice"]}
                        | {"kind": "sum", "composition": "items", "field": "netAmount"}}],
   "compositions": [{"name": "items", "target": "PurchaseRequestItems", "label": "Items"}],
   "associations": [{"name": "costCenter", "target": "CostCenters", "label": "Cost Center"}]}],
 "workflow": null | {"entity": "PurchaseRequests", "initial": "Draft",
   "states": [{"code": "Draft", "label": "Draft", "criticality": "Neutral|Information|Positive|Critical|Negative"}],
   "editable_states": ["Draft", "Rejected"],
   "transitions": [{"action": "submit", "label": "Submit", "from": ["Draft"], "to": "Submitted", "role": "",
                    "comment": "none|optional|required", "kind": "submit|decide|withdraw|other"}],
   "segregation_of_duties": false, "enforce_roles": false, "auto_approve_below": null | {"field", "value"}},
 "rules": [{"entity": "...", "field": "...", "check": "required|min|max|future|past|max_length", "value": 0,
            "message": "user-facing text"}],
 "roles": ["Requester", "Approver"],
 "sample_data": {"<root entity>": [{"field": "value", "<composition>": [{"field": "value"}]}]}}
Rules:
- Every entity automatically has a UUID key ID and createdAt/createdBy/modifiedAt/modifiedBy — never list them.
- Composition children (line items, comments, attachments-as-text) get a parent link automatically — no FK fields.
- Associations are only for master data the app itself maintains (e.g. CostCenters); otherwise use a String field.
- A workflow adds its own status, submittedAt, decidedBy, decidedAt, decisionNote fields and an approval
  history — do not list those. Approval processes: Draft -> Submitted -> Approved/Rejected, withdraw back to Draft;
  reject requires a comment. Status lifecycles without approval (tickets) are also workflows.
- Calculated amounts use formula (they become read-only). Use enums for small fixed value sets.
- Keep it focused: 1-5 entities, 4-14 fields each. 3-5 realistic sample root rows with 1-3 children each.
- External systems (S/4HANA, email...) cannot be generated — model the data locally and mention it in description."""


def clarifying_questions(requirement: str, ai_fn: Optional[AiFn]) -> List[Dict]:
    """Up to 5 questions when the requirement is genuinely ambiguous; [] when it is clear."""
    if not ai_fn or not (requirement or "").strip():
        return []
    system = ("You review a business requirement for a new SAP application before it is generated. Ask ONLY "
              "questions whose answer changes the data model or process (approval levels/roles, line items, "
              "required fields, calculations, integrations). If the requirement is clear enough to build a "
              "sensible first version, return no questions. Reply STRICT JSON: {\"questions\": [{\"id\": "
              "\"short_id\", \"question\": \"...\", \"options\": [\"...\"], \"why\": \"...\"}]} (max 5, options "
              "may be empty).")
    try:
        import ai_designer
        data = ai_designer.ask_json(ai_fn, system, f"Requirement:\n{requirement}")
    except Exception:  # noqa: BLE001 — questions are optional
        return []
    out = []
    for i, q in enumerate((data or {}).get("questions") or []):
        if isinstance(q, dict) and str(q.get("question") or "").strip():
            out.append({"id": re.sub(r"\W", "_", str(q.get("id") or f"q{i + 1}"))[:30],
                        "question": str(q["question"])[:300],
                        "options": [str(o)[:80] for o in (q.get("options") or []) if str(o).strip()][:6],
                        "why": str(q.get("why") or "")[:200]})
    return out[:5]


def design_domain(requirement: str, ai_fn: Optional[AiFn] = None, answers: Optional[Dict[str, str]] = None,
                  progress: Optional[Callable[[str], None]] = None) -> Dict:
    """Requirement (+ answers to the clarifying questions) -> validated domain.
    Returns {"domain", "notes", "source": "ai"|"rules"}."""
    say = progress or (lambda m: None)
    extra = "".join(f"\n- {k}: {v}" for k, v in (answers or {}).items() if str(v or "").strip())
    req = requirement + (f"\n\nClarifications:{extra}" if extra else "")
    if ai_fn:
        try:
            say("Designing the data model and business process…")
            import ai_designer
            raw = ai_designer.ask_json(ai_fn, _DOMAIN_SYSTEM, f"Requirement:\n{req}")
            domain, notes = validate_domain(raw)
            say(f"Data model: {len(domain['entities'])} entities"
                + (f", workflow on {domain['workflow']['entity']}" if domain["workflow"] else ""))
            return {"domain": domain, "notes": notes, "source": "ai"}
        except Exception as e:  # noqa: BLE001 — fall back to the rule-based model
            say(f"AI data model failed ({str(e)[:120]}) — using the rule-based model.")
            domain, notes = validate_domain(_rules_domain(req))
            return {"domain": domain, "notes": [f"AI design failed: {str(e)[:160]}"] + notes, "source": "rules"}
    domain, notes = validate_domain(_rules_domain(req))
    return {"domain": domain, "notes": notes, "source": "rules"}


def workflow_guards(domain: Dict) -> Dict[str, Dict]:
    """Action name -> {"field", "values"}: the states in which the backend accepts it."""
    wf = domain.get("workflow")
    if not wf:
        return {}
    return {t["action"]: {"field": wf["status_field"], "values": list(t["from"])} for t in wf["transitions"]}


def summary(domain: Dict) -> str:
    """Short text overview (review screen / design prompt)."""
    lines = [f"{domain['title']} — service {service_name(domain)} at {service_path(domain)}"]
    for e in domain["entities"]:
        flds = ", ".join(f"{f['name']} ({f['type']}{'*' if f.get('required') else ''})" for f in e["fields"])
        lines.append(f"• {e['name']}: {flds}")
        for c in e["compositions"]:
            lines.append(f"    ↳ {c['name']}: composition of {c['target']}")
        for a in e["associations"]:
            lines.append(f"    → {a['name']}: association to {a['target']}")
    wf = domain.get("workflow")
    if wf:
        lines.append(f"Workflow on {wf['entity']}: " + " · ".join(
            f"{t['label']} ({'/'.join(t['from'])} → {t['to']}{', role ' + t['role'] if t['role'] and wf['enforce_roles'] else ''})"
            for t in wf["transitions"]))
    for r in domain.get("rules") or []:
        lines.append(f"Rule: {r['message']}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Model helpers shared by EDMX / CDS / JS
# ─────────────────────────────────────────────────────────────────────────────
def _all_entities(domain: Dict) -> List[Dict]:
    """Entities incl. the workflow's history entity, each with its full field list."""
    out = []
    wf = domain.get("workflow")
    for e in domain["entities"]:
        e = dict(e, fields=list(e["fields"]), compositions=list(e["compositions"]))
        if wf and wf["entity"] == e["name"]:
            e["fields"].append({"name": "status", "type": "String", "length": 20, "label": "Status",
                                "readonly": True, "default": wf["initial"], "enum": [s["code"] for s in wf["states"]]})
            e["fields"] += [{"name": n, "type": t, "label": lab, "readonly": True,
                             **({"length": 1000 if n == "decisionNote" else 255} if t == "String" else {})}
                            for n, t, lab in WF_FIELDS if n in workflow_fields(wf)]
            e["compositions"].append({"name": "history", "target": history_entity(domain), "label": "Approval History"})
        out.append(e)
    if wf:
        out.append({"name": history_entity(domain), "label": "Approval Step", "title_field": "action",
                    "parent": wf["entity"], "readonly": True, "compositions": [], "associations": [],
                    "fields": [{"name": "action", "type": "String", "length": 30, "label": "Step"},
                               {"name": "fromStatus", "type": "String", "length": 20, "label": "From Status"},
                               {"name": "toStatus", "type": "String", "length": 20, "label": "To Status"},
                               {"name": "comment", "type": "String", "length": 1000, "label": "Comment"}]})
    return out


def workflow_fields(wf: Dict) -> List[str]:
    """Bookkeeping fields a workflow adds: submittedAt for submit steps, decided* for decisions."""
    kinds = {t["kind"] for t in wf.get("transitions") or []}
    return (["submittedAt"] if "submit" in kinds else []) +         (["decidedBy", "decidedAt", "decisionNote"] if "decide" in kinds else [])


def _parent_comp(domain: Dict, ents: List[Dict], child: str) -> Optional[Tuple[str, str]]:
    for e in ents:
        for c in e["compositions"]:
            if c["target"] == child:
                return e["name"], c["name"]
    return None


# ─────────────────────────────────────────────────────────────────────────────
# EDMX (OData V4) — the shape CAP serves for srv/service.cds
# ─────────────────────────────────────────────────────────────────────────────
def _a(s) -> str:
    return _xesc(str(s), {'"': "&quot;"})


def _edm_prop(f: Dict) -> str:
    t = _EDM[f["type"]]
    attrs = f'Name="{f["name"]}" Type="{t}"'
    if f["type"] == "String" and f.get("length"):
        attrs += f' MaxLength="{f["length"]}"'
    if f["type"] == "Decimal":
        attrs += f' Precision="{f["precision"]}" Scale="{f["scale"]}"'
    if f.get("required") and not f.get("readonly"):
        attrs += ' Nullable="false"'
    return f"<Property {attrs}/>"


def to_edmx(domain: Dict) -> str:
    ns = service_name(domain)
    ents = _all_entities(domain)
    wf = domain.get("workflow")
    types, sets, anns, actions = [], [], [], []
    for e in ents:
        en = e["name"]
        props = ['<Property Name="ID" Type="Edm.Guid" Nullable="false"/>',
                 '<Property Name="createdAt" Type="Edm.DateTimeOffset" Precision="7"/>',
                 '<Property Name="createdBy" Type="Edm.String" MaxLength="255"/>',
                 '<Property Name="modifiedAt" Type="Edm.DateTimeOffset" Precision="7"/>',
                 '<Property Name="modifiedBy" Type="Edm.String" MaxLength="255"/>']
        navs, bindings = [], []
        pc = _parent_comp(domain, ents, en)
        if pc:
            props.append('<Property Name="parent_ID" Type="Edm.Guid"/>')
            navs.append(f'<NavigationProperty Name="parent" Type="{ns}.{pc[0]}" Partner="{pc[1]}">'
                        f'<ReferentialConstraint Property="parent_ID" ReferencedProperty="ID"/></NavigationProperty>')
            bindings.append(f'<NavigationPropertyBinding Path="parent" Target="{pc[0]}"/>')
        props += [_edm_prop(f) for f in e["fields"]]
        for a in e["associations"]:
            props.append(f'<Property Name="{a["name"]}_ID" Type="Edm.Guid"/>')
            navs.append(f'<NavigationProperty Name="{a["name"]}" Type="{ns}.{a["target"]}">'
                        f'<ReferentialConstraint Property="{a["name"]}_ID" ReferencedProperty="ID"/></NavigationProperty>')
            bindings.append(f'<NavigationPropertyBinding Path="{a["name"]}" Target="{a["target"]}"/>')
        for c in e["compositions"]:
            navs.append(f'<NavigationProperty Name="{c["name"]}" Type="Collection({ns}.{c["target"]})" '
                        f'Partner="parent"><OnDelete Action="Cascade"/></NavigationProperty>')
            bindings.append(f'<NavigationPropertyBinding Path="{c["name"]}" Target="{c["target"]}"/>')
        types.append(f'<EntityType Name="{en}"><Key><PropertyRef Name="ID"/></Key>{"".join(props)}{"".join(navs)}</EntityType>')
        sets.append(f'<EntitySet Name="{en}" EntityType="{ns}.{en}">{"".join(bindings)}</EntitySet>')
        # labels / computed
        lab = [("ID", "ID", True)] + [(n, l, True) for n, l in MANAGED]
        if pc:
            lab.append(("parent_ID", human(pc[0]), False))
        lab += [(f["name"], f["label"], bool(f.get("readonly") or f.get("formula"))) for f in e["fields"]]
        lab += [(a["name"] + "_ID", a["label"], False) for a in e["associations"]]
        for n, l, comp in lab:
            anns.append(f'<Annotations Target="{ns}.{en}/{n}"><Annotation Term="Common.Label" String="{_a(l)}"/>'
                        + ('<Annotation Term="Core.Computed" Bool="true"/>' if comp else "") + "</Annotations>")
        for a in e["associations"]:
            tgt = next((x for x in ents if x["name"] == a["target"]), None)
            disp = (f'<Record Type="Common.ValueListParameterDisplayOnly"><PropertyValue Property="ValueListProperty" '
                    f'String="{tgt["title_field"]}"/></Record>') if tgt and tgt.get("title_field") else ""
            anns.append(f'<Annotations Target="{ns}.{en}/{a["name"]}_ID"><Annotation Term="Common.ValueList"><Record '
                        f'Type="Common.ValueListType"><PropertyValue Property="CollectionPath" String="{a["target"]}"/>'
                        f'<PropertyValue Property="Parameters"><Collection><Record Type="Common.ValueListParameterInOut">'
                        f'<PropertyValue Property="LocalDataProperty" PropertyPath="{a["name"]}_ID"/><PropertyValue '
                        f'Property="ValueListProperty" String="ID"/></Record>{disp}</Collection></PropertyValue>'
                        f'</Record></Annotation></Annotations>')
        if e.get("readonly"):
            anns.append(f'<Annotations Target="{ns}.EntityContainer/{en}">'
                        + "".join(f'<Annotation Term="Capabilities.{r}Restrictions"><Record><PropertyValue '
                                  f'Property="{p}" Bool="false"/></Record></Annotation>'
                                  for r, p in (("Insert", "Insertable"), ("Update", "Updatable"),
                                               ("Delete", "Deletable"))) + "</Annotations>")
    if wf:
        for t in wf["transitions"]:
            params = [f'<Parameter Name="in" Type="{ns}.{wf["entity"]}"/>']
            if t["comment"] != "none":
                params.append('<Parameter Name="comment" Type="Edm.String" MaxLength="1000"/>')
            vals = "".join(f"<String>{_a(v)}</String>" for v in t["from"])
            actions.append(
                f'<Action Name="{t["action"]}" IsBound="true" EntitySetPath="in">{"".join(params)}'
                f'<ReturnType Type="{ns}.{wf["entity"]}"/>'
                f'<Annotation Term="Common.Label" String="{_a(t["label"])}"/>'
                f'<Annotation Term="Evoke.StateGuard"><Record><PropertyValue Property="Field" '
                f'String="{wf["status_field"]}"/><PropertyValue Property="Values"><Collection>{vals}</Collection>'
                f'</PropertyValue></Record></Annotation></Action>')
    refs = "".join(
        f'<edmx:Reference Uri="https://sap.github.io/odata-vocabularies/vocabularies/{v}.xml">'
        f'<edmx:Include Alias="{v}" Namespace="com.sap.vocabularies.{v}.v1"/></edmx:Reference>'
        for v in ("Common",))
    refs += "".join(
        f'<edmx:Reference Uri="https://oasis-tcs.github.io/odata-vocabularies/vocabularies/Org.OData.{v}.V1.xml">'
        f'<edmx:Include Alias="{v}" Namespace="Org.OData.{v}.V1"/></edmx:Reference>'
        for v in ("Core", "Capabilities"))
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<edmx:Edmx Version="4.0" xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">'
            f'{refs}<edmx:DataServices><Schema Namespace="{ns}" xmlns="http://docs.oasis-open.org/odata/ns/edm">'
            f'<EntityContainer Name="EntityContainer">{"".join(sets)}</EntityContainer>'
            f'{"".join(types)}{"".join(actions)}{"".join(anns)}</Schema></edmx:DataServices></edmx:Edmx>')


# ─────────────────────────────────────────────────────────────────────────────
# CAP project (backend/)
# ─────────────────────────────────────────────────────────────────────────────
def _cds_str(s) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def _cds_type(f: Dict) -> str:
    t = f["type"]
    if t == "String":
        return f"String({f.get('length') or 100})"
    if t == "Decimal":
        return f"Decimal({f['precision']}, {f['scale']})"
    return t


def _cds_default(f: Dict) -> str:
    d = f.get("default")
    if d is None or (isinstance(d, str) and d.startswith("$")):
        return ""
    if f["type"] in ("Integer", "Decimal"):
        return f" default {d}"
    if f["type"] == "Boolean":
        return f" default {'true' if d else 'false'}"
    return f" default {_cds_str(d)}"


def _schema_cds(domain: Dict) -> str:
    ents = _all_entities(domain)
    lines = [f"namespace {namespace(domain)};", "", "using { cuid, managed } from '@sap/cds/common';", ""]
    for e in ents:
        lines.append(f"@title: {_cds_str(e['label'])}")
        lines.append(f"entity {e['name']} : cuid, managed {{")
        pc = _parent_comp(domain, ents, e["name"])
        if pc:
            lines.append(f"  @title: {_cds_str(human(pc[0]))}")
            lines.append(f"  parent : Association to {pc[0]};")
        for f in e["fields"]:
            lines.append(f"  @title: {_cds_str(f['label'])}")
            lines.append(f"  {f['name']} : {_cds_type(f)}{_cds_default(f)};")
        for a in e["associations"]:
            lines.append(f"  @title: {_cds_str(a['label'])}")
            lines.append(f"  {a['name']} : Association to {a['target']};")
        for c in e["compositions"]:
            lines.append(f"  @title: {_cds_str(c['label'])}")
            lines.append(f"  {c['name']} : Composition of many {c['target']} on {c['name']}.parent = $self;")
        lines += ["}", ""]
    return "\n".join(lines)


def _service_cds(domain: Dict) -> str:
    ents = _all_entities(domain)
    wf = domain.get("workflow")
    svc = service_name(domain)
    lines = [f"using {{ {namespace(domain)} as db }} from '../db/schema';", "",
             f"@path: '{_kebab(domain['name'])}'", "@requires: 'authenticated-user'", f"service {svc} {{"]
    for e in ents:
        pre = "  @readonly\n" if e.get("readonly") else ""
        if wf and wf["entity"] == e["name"]:
            acts = []
            for t in wf["transitions"]:
                req = f"@(requires: {_cds_str(t['role'])}) " if t["role"] and wf["enforce_roles"] else ""
                par = "comment : String(1000)" if t["comment"] != "none" else ""
                acts.append(f"    {req}@title: {_cds_str(t['label'])}\n    action {t['action']}({par}) returns {e['name']};")
            lines.append(f"{pre}  entity {e['name']} as projection on db.{e['name']} actions {{\n"
                         + "\n".join(acts) + "\n  };")
        else:
            lines.append(f"{pre}  entity {e['name']} as projection on db.{e['name']};")
    lines.append("}")
    ro = []
    for e in ents:
        flds = [f["name"] for f in e["fields"] if f.get("readonly") or f.get("formula")]
        req = [f["name"] for f in e["fields"] if f.get("required") and not f.get("readonly") and not f.get("formula")]
        body = [f"  {n} @readonly;" for n in flds] + [f"  {n} @mandatory;" for n in req]
        for a in e["associations"]:
            tgt = next((x for x in ents if x["name"] == a["target"]), None)
            if tgt and tgt.get("title_field"):
                body.append(f"  {a['name']} @Common.ValueList: {{ CollectionPath: {_cds_str(a['target'])}, Parameters: [\n"
                            f"    {{ $Type: 'Common.ValueListParameterInOut', LocalDataProperty: {a['name']}_ID, ValueListProperty: 'ID' }},\n"
                            f"    {{ $Type: 'Common.ValueListParameterDisplayOnly', ValueListProperty: {_cds_str(tgt['title_field'])} }}\n  ]}};")
        if body:
            ro.append(f"annotate {svc}.{e['name']} with {{\n" + "\n".join(body) + "\n};")
    return "\n".join(lines) + "\n\n" + "\n\n".join(ro) + "\n"


def _runtime_config(domain: Dict) -> Dict:
    ents = _all_entities(domain)
    wf = domain.get("workflow")
    cfg = {"namespace": namespace(domain), "root": domain["root"], "entities": {}, "rules": domain.get("rules") or [],
           "workflow": None}
    for e in ents:
        pc = _parent_comp(domain, ents, e["name"])
        cfg["entities"][e["name"]] = {
            "label": e["label"], "parent": pc[0] if pc else "",
            "compositions": [{"name": c["name"], "target": c["target"]} for c in e["compositions"]],
            "required": [f["name"] for f in e["fields"] if f.get("required") and not f.get("readonly")],
            "enums": {f["name"]: f["enum"] for f in e["fields"] if f.get("enum") and not f.get("readonly")},
            "defaults": {f["name"]: f["default"] for f in e["fields"]
                         if f.get("default") is not None and not f.get("readonly")},
            "formulas": {f["name"]: f["formula"] for f in e["fields"] if f.get("formula")},
            "labels": {f["name"]: f["label"] for f in e["fields"]},
        }
    if wf:
        cfg["workflow"] = {"entity": wf["entity"], "history": history_entity(domain), "field": wf["status_field"],
                           "states": {s["code"]: s["label"] for s in wf["states"]}, "initial": wf["initial"],
                           "fields": workflow_fields(wf),
                           "editable": wf["editable_states"], "sod": wf["segregation_of_duties"],
                           "enforceRoles": wf["enforce_roles"], "autoApprove": wf["auto_approve_below"],
                           "approvedState": next((t["to"] for t in wf["transitions"]
                                                  if t["kind"] == "decide" and re.search(r"approv|releas|accept", t["action"], re.I)), ""),
                           "transitions": {t["action"]: {k: t[k] for k in ("label", "from", "to", "role", "comment", "kind")}
                                           for t in wf["transitions"]}}
    return cfg


_SERVICE_JS = r"""/**
 * Generated by the Evoke Fiori App Studio — business logic of the __SVC__.
 * Everything is driven by CONFIG (data model facts, rules, workflow) so the handlers
 * stay generic. The workflow is enforced HERE (not only in the UI):
 *   - an action is only accepted in its valid source states (HTTP 409 otherwise),
 *   - comments where required, optional role check and segregation of duties,
 *   - documents can only be edited in the editable states,
 *   - every step is written to the approval history.
 */
const cds = require('@sap/cds');

const CONFIG = __CONFIG__;

const today = () => new Date().toISOString().slice(0, 10);
const num = v => (v === null || v === undefined || v === '' ? 0 : Number(v));
const keyOf = req => { const p = req.params && req.params[req.params.length - 1]; return p && typeof p === 'object' ? p.ID : p; };

module.exports = class __CLASS__ extends cds.ApplicationService {
  async init() {
    const db = cds.entities(CONFIG.namespace);
    const WF = CONFIG.workflow;

    const special = (v, req) => v === '$today' ? today() : v === '$now' ? new Date().toISOString()
      : v === '$user' ? req.user.id : v;

    // defaults, calculated fields and rules for one record (+ nested composition rows)
    const prepare = (name, data, req, isCreate, path) => {
      const ent = CONFIG.entities[name];
      if (!ent || !data || typeof data !== 'object') return;
      if (isCreate) {
        for (const [f, v] of Object.entries(ent.defaults)) if (data[f] === undefined || data[f] === null) data[f] = special(v, req);
      }
      for (const c of ent.compositions) {
        if (Array.isArray(data[c.name])) data[c.name].forEach((row, i) => prepare(c.target, row, req, true, `${path}${c.name}[${i}]/`));
      }
      for (const [f, fo] of Object.entries(ent.formulas)) {
        if (fo.kind === 'product' && fo.fields.some(x => data[x] !== undefined)) {
          data[f] = fo.fields.reduce((p, x) => p * num(data[x]), 1);
        } else if (fo.kind === 'sum' && Array.isArray(data[fo.composition])) {
          data[f] = data[fo.composition].reduce((s, row) => s + num(row[fo.field]), 0);
        }
      }
      for (const f of ent.required) {
        if ((isCreate || f in data) && (data[f] === undefined || data[f] === null || String(data[f]).trim() === '')) {
          req.error(400, `${ent.labels[f] || f} is required.`, path + f);
        }
      }
      for (const [f, vals] of Object.entries(ent.enums)) {
        if (data[f] !== undefined && data[f] !== null && data[f] !== '' && !vals.includes(data[f])) {
          req.error(400, `${ent.labels[f] || f} must be one of: ${vals.join(', ')}.`, path + f);
        }
      }
      for (const r of CONFIG.rules) {
        if (r.entity !== name || data[r.field] === undefined || data[r.field] === null || data[r.field] === '') continue;
        const v = data[r.field];
        const bad = (r.check === 'min' && num(v) < r.value) || (r.check === 'max' && num(v) > r.value)
          || (r.check === 'future' && String(v).slice(0, 10) < today())
          || (r.check === 'past' && String(v).slice(0, 10) > today())
          || (r.check === 'max_length' && String(v).length > r.value);
        if (bad) req.error(400, r.message, path + r.field);
      }
    };

    // the root document a record belongs to (for edit locks and totals)
    const rootOf = async (name, id) => {
      let ent = CONFIG.entities[name];
      while (ent && ent.parent && id) {
        const row = await SELECT.one.from(db[name], id).columns('parent_ID');
        id = row && row.parent_ID; name = ent.parent; ent = CONFIG.entities[name];
      }
      return { name, id };
    };

    const assertEditable = async (req, name, id) => {
      if (!WF || !id) return;
      const r = await rootOf(name, id);
      if (r.name !== WF.entity || !r.id) return;
      const doc = await SELECT.one.from(db[WF.entity], r.id).columns(WF.field);
      if (doc && !WF.editable.includes(doc[WF.field])) {
        req.reject(409, `This ${CONFIG.entities[WF.entity].label} is '${WF.states[doc[WF.field]] || doc[WF.field]}' and can no longer be changed. `
          + `Changes are only possible while it is ${WF.editable.map(s => WF.states[s] || s).join(' or ')}.`);
      }
    };

    // recalculate sums over compositions (e.g. total = sum of item net amounts)
    const recalc = async (name, id) => {
      const ent = CONFIG.entities[name];
      if (!ent || !id) return;
      const upd = {};
      for (const [f, fo] of Object.entries(ent.formulas)) {
        if (fo.kind !== 'sum') continue;
        const c = ent.compositions.find(x => x.name === fo.composition);
        const row = await SELECT.one.from(db[c.target]).columns(`sum(${fo.field}) as total`).where({ parent_ID: id });
        upd[f] = num(row && row.total);
      }
      if (Object.keys(upd).length) await UPDATE(db[name], id).with(upd);
      if (ent.parent) {
        const r = await SELECT.one.from(db[name], id).columns('parent_ID');
        if (r && r.parent_ID) await recalc(ent.parent, r.parent_ID);
      }
    };

    for (const name of Object.keys(CONFIG.entities)) {
      const ent = CONFIG.entities[name];
      const target = this.entities[name];
      if (!target) continue;
      this.before('CREATE', target, async req => {
        prepare(name, req.data, req, true, '');
        if (ent.parent && req.data.parent_ID) await assertEditable(req, ent.parent, req.data.parent_ID);
      });
      this.before('UPDATE', target, async req => {
        const id = req.data.ID || keyOf(req);
        await assertEditable(req, name, id);
        const formulas = Object.values(ent.formulas).filter(f => f.kind === 'product');
        if (formulas.length && id) {               // calculate with the stored values of fields not sent
          const cur = await SELECT.one.from(db[name], id);
          if (cur) for (const fo of formulas) for (const x of fo.fields) if (req.data[x] === undefined) req.data[x] = cur[x];
        }
        prepare(name, req.data, req, false, '');
      });
      this.before('DELETE', target, async req => {
        const id = keyOf(req);
        await assertEditable(req, name, id);
        if (ent.parent && id) {
          const r = await SELECT.one.from(db[name], id).columns('parent_ID');
          req._evokeParent = r && r.parent_ID;
        }
      });
      this.after(['CREATE', 'UPDATE'], target, async (res, req) => {
        const id = (res && res.ID) || req.data.ID || keyOf(req);
        if (Object.values(ent.formulas).some(f => f.kind === 'sum')) await recalc(name, id);
        else if (ent.parent) {
          const r = id && await SELECT.one.from(db[name], id).columns('parent_ID');
          if (r && r.parent_ID) await recalc(ent.parent, r.parent_ID);
        }
      });
      this.after('DELETE', target, async (_, req) => { if (req._evokeParent) await recalc(ent.parent, req._evokeParent); });
    }

    // ── workflow actions ───────────────────────────────────────────────────
    if (WF) {
      const label = CONFIG.entities[WF.entity].label;
      for (const [action, t] of Object.entries(WF.transitions)) {
        this.on(action, WF.entity, async req => {
          const id = keyOf(req);
          const doc = await SELECT.one.from(db[WF.entity], id);
          if (!doc) return req.reject(404, `${label} not found.`);
          const from = doc[WF.field];
          if (!t.from.includes(from)) {
            return req.reject(409, `'${t.label}' is not possible: the ${label} is '${WF.states[from] || from}'. `
              + `It is only possible when the ${label} is ${t.from.map(s => WF.states[s] || s).join(' or ')}.`);
          }
          if (WF.enforceRoles && t.role && !req.user.is(t.role)) {
            return req.reject(403, `'${t.label}' requires the role ${t.role}. Ask your administrator to assign the role collection.`);
          }
          const comment = (req.data.comment || '').trim();
          if (t.comment === 'required' && !comment) return req.reject(400, `Please enter a comment to ${t.label.toLowerCase()}.`);
          if (WF.sod && t.kind === 'decide' && doc.createdBy && doc.createdBy === req.user.id) {
            return req.reject(403, `Segregation of duties: you created this ${label} and cannot ${t.label.toLowerCase()} it yourself.`);
          }
          let to = t.to;
          let step = action;
          const now = new Date().toISOString();
          const upd = { [WF.field]: to };
          if (t.kind === 'submit') {
            upd.submittedAt = now;
            const a = WF.autoApprove;
            if (a && WF.approvedState && num(doc[a.field]) < a.value) {
              to = upd[WF.field] = WF.approvedState; step = 'autoApprove';
              upd.decidedBy = 'system'; upd.decidedAt = now; upd.decisionNote = `Automatically approved (below ${a.value}).`;
            }
          }
          if (t.kind === 'decide') { upd.decidedBy = req.user.id; upd.decidedAt = now; upd.decisionNote = comment || null; }
          if (t.kind === 'withdraw') { upd.decidedBy = null; upd.decidedAt = null; upd.decisionNote = null; }
          for (const k of Object.keys(upd)) if (k !== WF.field && !WF.fields.includes(k)) delete upd[k];
          await UPDATE(db[WF.entity], id).with(upd);
          await INSERT.into(db[WF.history]).entries({
            parent_ID: id, action: step === 'autoApprove' ? 'Auto-approved' : t.label, fromStatus: from, toStatus: to, comment: comment || null,
            createdBy: req.user.id, createdAt: now
          });
          req.info(`${label}: ${t.label} done — status is now '${WF.states[to] || to}'.`);
          return SELECT.one.from(this.entities[WF.entity], id);
        });
      }
    }
    return super.init();
  }
};
"""


def _service_js(domain: Dict) -> str:
    cfg = json.dumps(_runtime_config(domain), indent=2, ensure_ascii=False)
    return (_SERVICE_JS.replace("__CONFIG__", cfg).replace("__CLASS__", service_name(domain))
            .replace("__SVC__", service_name(domain)))


def _uid(*parts) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "evoke:" + "/".join(map(str, parts))))


def _csv_files(domain: Dict) -> Dict[str, str]:
    ents = {e["name"]: e for e in _all_entities(domain)}
    rows: Dict[str, List[Dict]] = {n: [] for n in ents}

    def walk(en, row, i, parent_id=""):
        e = ents[en]
        rid = _uid(domain["name"], en, parent_id, i)
        out = {"ID": rid}
        if parent_id:
            out["parent_ID"] = parent_id
        for f in e["fields"]:
            if f["name"] in row and not isinstance(row[f["name"]], list):
                out[f["name"]] = row[f["name"]]
        for c in e["compositions"]:
            for j, k in enumerate(row.get(c["name"]) or []):
                walk(c["target"], k, j, rid)
        # calculated values in the seed data
        for f in e["fields"]:
            fo = f.get("formula")
            if fo and fo["kind"] == "product":
                v = 1.0
                for x in fo["fields"]:
                    v *= float(out.get(x) or 0)
                out[f["name"]] = round(v, f.get("scale", 2))
        rows[en].append(out)
        return out

    for en, rr in (domain.get("sample_data") or {}).items():
        for i, r in enumerate(rr):
            walk(en, r, i)
    # sums over compositions
    for en, e in ents.items():
        for f in e["fields"]:
            fo = f.get("formula")
            if fo and fo["kind"] == "sum":
                comp = next(c for c in e["compositions"] if c["name"] == fo["composition"])
                for r in rows[en]:
                    r[f["name"]] = round(sum(float(k.get(fo["field"]) or 0) for k in rows[comp["target"]]
                                             if k.get("parent_ID") == r["ID"]), f.get("scale", 2))
    wf = domain.get("workflow")
    if wf:                                   # history for seeded documents that are past the initial state
        for r in rows[wf["entity"]]:
            if r.get("status") and r["status"] != wf["initial"]:
                t = next((t for t in wf["transitions"] if t["to"] == r["status"]), None)
                if t:
                    rows[history_entity(domain)].append({"ID": _uid(r["ID"], "h"), "parent_ID": r["ID"],
                                                         "action": t["label"], "fromStatus": t["from"][0],
                                                         "toStatus": r["status"], "comment": "Sample data"})
    files = {}
    for en, rr in rows.items():
        if not rr:
            continue
        cols = list(dict.fromkeys(k for r in rr for k in r))
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";", lineterminator="\n")
        w.writerow(cols)
        for r in rr:
            w.writerow(["true" if r.get(c) is True else "false" if r.get(c) is False else
                        ("" if r.get(c) is None else r.get(c)) for c in cols])
        files[f"backend/db/data/{namespace(domain)}-{en}.csv"] = buf.getvalue()
    return files


def mock_data(domain: Dict) -> Dict[str, str]:
    """localService/data/<Set>.json for the UI's mock-server preview (same rows as the CSV seed)."""
    out = {}
    for path, text in _csv_files(domain).items():
        en = path.rsplit("-", 1)[-1][:-4]
        rows = list(csv.DictReader(io.StringIO(text), delimiter=";"))
        ent = next((e for e in _all_entities(domain) if e["name"] == en), None)
        types = {f["name"]: f["type"] for f in (ent or {}).get("fields", [])}
        clean = []
        for r in rows:
            o = {}
            for k, v in r.items():
                if v == "":
                    continue
                t = types.get(k)
                o[k] = (float(v) if t == "Decimal" else int(float(v)) if t == "Integer" else
                        v == "true" if t == "Boolean" else v)
            clean.append(o)
        out[f"localService/data/{en}.json"] = json.dumps(clean, indent=1, ensure_ascii=False)
    return out


def _slug(app_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", app_id) or "app"


def roles(domain: Dict) -> List[str]:
    return sorted(set(domain.get("roles") or []) | {t["role"] for t in ((domain.get("workflow") or {}).get("transitions") or [])
                                                     if t["role"]})


def xs_security(domain: Dict, app_id: str) -> str:
    rs = roles(domain)
    xsappname = _slug(app_id)
    return json.dumps({
        "xsappname": xsappname, "tenant-mode": "dedicated",
        "scopes": [{"name": f"$XSAPPNAME.{r}", "description": r} for r in rs],
        "role-templates": [{"name": r, "description": f"{r} of {domain['title']}",
                            "scope-references": [f"$XSAPPNAME.{r}"]} for r in rs],
        "role-collections": [{"name": f"{xsappname}_{r}", "description": f"{domain['title']} — {r}",
                              "role-template-references": [f"$XSAPPNAME.{r}"]} for r in rs],
        "oauth2-configuration": {"redirect-uris": ["https://*.hana.ondemand.com/**", "https://*.cloud.sap/**",
                                                   "http://localhost:*/**"]},
    }, indent=2)


def _package_json(domain: Dict, app_id: str, db: str) -> str:
    users = {"alice": {"roles": ["Requester"]}, "bob": {"roles": roles(domain) or ["Approver"]},
             "carol": {"roles": ["Requester"]}}
    requires = {"[development]": {"auth": {"kind": "mocked", "users": users},
                                  "db": {"kind": "sqlite", "credentials": {"url": ":memory:"}}},
                "[production]": {"auth": "xsuaa",
                                 "db": "hana" if db == "hana" else {"kind": "sqlite", "credentials": {"url": ":memory:"}}}}
    deps = {"@sap/cds": "^9.8", "@cap-js/sqlite": "^2", "@sap/xssec": "^4"}
    if db == "hana":
        deps["@cap-js/hana"] = "^2"
    pkg = {"name": _slug(app_id).lower() + "-srv", "version": "1.0.0", "private": True,
           "description": f"{domain['title']} — CAP service generated by the Evoke Fiori App Studio",
           "dependencies": deps,
           # better-sqlite3 is native: pin a release with prebuilt binaries for Node 20/22 (Windows,
           # macOS, CF Linux) — newer majors need Node >= 22 and fall back to a C++ build otherwise
           "overrides": {"better-sqlite3": "12.11.1"},
           "engines": {"node": "^20 || ^22"},
           "scripts": {"start": "cds-serve", "watch": "cds watch"},
           "cds": {"requires": requires}}
    if db == "hana":
        pkg["devDependencies"] = {"@sap/cds-dk": "^9"}
        pkg["scripts"]["build"] = "cds build --production"
        pkg["cds"]["build"] = {"target": "gen"}
    else:
        # sqlite in memory: schema + db/data CSVs are deployed at every start (demo data,
        # changes are lost on restart/restage) — `cds-serve` does this for ':memory:'
        pkg["cds"]["features"] = {"in_memory_db": True}
    return json.dumps(pkg, indent=2)


def _backend_readme(domain: Dict, db: str) -> str:
    wf = domain.get("workflow")
    flow = ""
    if wf:
        flow = "\n## Workflow\n\n| Action | Allowed when | New status | Role | Comment |\n|---|---|---|---|---|\n" + "".join(
            f"| {t['label']} (`{t['action']}`) | {', '.join(t['from'])} | {t['to']} | "
            f"{t['role'] if t['role'] and wf['enforce_roles'] else 'any user'} | {t['comment']} |\n"
            for t in wf["transitions"]) + (
            f"\nDocuments can be changed only while they are {' or '.join(wf['editable_states'])}. "
            "Every step is written to the approval history. Calling an action in another state returns "
            "HTTP 409 with an explanation." + (" Approvers cannot decide on their own documents (segregation of duties)."
                                              if wf["segregation_of_duties"] else ""))
    return f"""# {domain['title']} — CAP backend

Generated by the Evoke Fiori App Studio. Service `{service_name(domain)}` at `{service_path(domain)}`.

## Run locally

```bash
cd backend
npm install
npx cds watch          # http://localhost:4004 — users alice / bob / carol (any password)
```

Then start the UI against it from the project root: `npm run start-cap` (ui5-cap.yaml proxies /odata to
localhost:4004).

## Database

{"SQLite in memory: the schema and the sample data in db/data are deployed at every start. Data entered in the app is LOST when the app restarts — fine for demos, not for production. Regenerate with SAP HANA Cloud for persistent data." if db != "hana" else "SAP HANA Cloud (HDI container). `npm run build` (cds build --production) creates gen/srv and gen/db; the MTA build runs it."}
{flow}
"""


def cap_files(domain: Dict, app_id: str, *, db: str = "sqlite") -> Dict[str, str]:
    """The CAP backend (paths under backend/) for a validated domain."""
    files = {
        "backend/package.json": _package_json(domain, app_id, db),
        "backend/db/schema.cds": _schema_cds(domain),
        "backend/srv/service.cds": _service_cds(domain),
        "backend/srv/service.js": _service_js(domain),
        "backend/README.md": _backend_readme(domain, db),
        "backend/.gitignore": "node_modules/\ngen/\n*.sqlite\n",
    }
    files.update(_csv_files(domain))
    return files


# ─────────────────────────────────────────────────────────────────────────────
# Deployment: MTA (approuter + CAP service [+ HDI deployer]) and approuter routes
# ─────────────────────────────────────────────────────────────────────────────
def mta_yaml(domain: Dict, app_id: str, *, db: str = "sqlite") -> str:
    slug = _slug(app_id)
    xsuaa, hdi = slug + "-xsuaa", slug + "-db"
    hana = db == "hana"
    srv_path = "backend/gen/srv" if hana else "backend"
    out = (
        "_schema-version: '3.3.0'\n"
        f"ID: {app_id}\n"
        "version: 0.0.1\n"
        "parameters:\n"
        "  enable-parallel-deployments: true\n"
        + ("build-parameters:\n"
           "  before-all:\n"
           "    - builder: custom\n"
           "      commands:\n"
           "        - npm install --prefix backend\n"
           "        - npm run build --prefix backend\n" if hana else "")
        + "\nmodules:\n"
        f"  - name: {app_id}\n"
        "    type: approuter.nodejs\n"
        "    path: .\n"
        "    parameters:\n"
        "      memory: 256M\n"
        "      disk-quota: 512M\n"
        "    requires:\n"
        f"      - name: {xsuaa}\n"
        "      - name: srv-api\n"
        "        group: destinations\n"
        "        properties:\n"
        "          name: srv-api\n"
        "          url: ~{srv-url}\n"
        "          forwardAuthToken: true\n"
        "    build-parameters:\n"
        "      # no local build: the CF Node.js buildpack installs @sap/approuter at staging\n"
        "      builder: custom\n"
        "      commands: []\n"
        "      build-result: .\n"
        "      ignore:\n"
        "        - node_modules/\n"
        "        - backend/\n"
        "        - .env\n"
        "        - '*.mtar'\n"
        "        - mta_archives/\n"
        f"  - name: {app_id}-srv\n"
        "    type: nodejs\n"
        f"    path: {srv_path}\n"
        "    parameters:\n"
        "      memory: 256M\n"
        "      disk-quota: 1024M\n"
        "      buildpack: nodejs_buildpack\n"
        "    provides:\n"
        "      - name: srv-api\n"
        "        properties:\n"
        "          srv-url: ${default-url}\n"
        "    requires:\n"
        f"      - name: {xsuaa}\n"
        + (f"      - name: {hdi}\n" if hana else "")
        + "    build-parameters:\n"
        "      builder: custom\n"
        "      commands: []\n"
        "      ignore:\n"
        "        - node_modules/\n"
        "        - '*.sqlite'\n"
        + ("        - gen/\n" if not hana else "")
    )
    if hana:
        out += (
            f"  - name: {app_id}-db-deployer\n"
            "    type: hdb\n"
            "    path: backend/gen/db\n"
            "    parameters:\n"
            "      buildpack: nodejs_buildpack\n"
            "    requires:\n"
            f"      - name: {hdi}\n"
            "    build-parameters:\n"
            "      builder: custom\n"
            "      commands: []\n"
            "      ignore:\n"
            "        - node_modules/\n"
        )
    out += (
        "\nresources:\n"
        f"  - name: {xsuaa}\n"
        "    type: org.cloudfoundry.managed-service\n"
        "    parameters:\n"
        "      service: xsuaa\n"
        "      service-plan: application\n"
        "      path: ./xs-security.json\n"
    )
    if hana:
        out += (
            f"  - name: {hdi}\n"
            "    type: com.sap.xs.hdi-container\n"
            "    parameters:\n"
            "      service: hana\n"
            "      service-plan: hdi-shared\n"
        )
    return out


def xs_app_json() -> str:
    return json.dumps({
        "welcomeFile": "/index.html", "authenticationMethod": "route",
        "routes": [{"source": "^/odata/(.*)$", "target": "/odata/$1", "destination": "srv-api",
                    "authenticationType": "xsuaa", "csrfProtection": False},
                   {"source": "^(.*)$", "localDir": "webapp", "authenticationType": "xsuaa"}],
        "logout": {"logoutEndpoint": "/logout"},
    }, indent=2)


def ui5_cap_yaml(app_id: str) -> str:
    return f"""# Local development against the generated CAP backend:
#   terminal 1:  cd backend && npm install && npx cds watch
#   terminal 2:  npm run start-cap        (log in as alice / bob / carol, any password)
specVersion: "3.0"
metadata:
  name: {app_id}
type: application
server:
  customMiddleware:
    - name: fiori-tools-proxy
      afterMiddleware: compression
      configuration:
        backend:
          - path: /odata
            url: http://localhost:4004
"""


# ─────────────────────────────────────────────────────────────────────────────
# UI design support
# ─────────────────────────────────────────────────────────────────────────────
_CRIT_STATE = {"Positive": "Success", "Critical": "Warning", "Negative": "Error",
               "Information": "Information", "Neutral": "None"}


def design_hints(domain: Dict) -> str:
    """Appended to the requirement for the AI UI design: what the generated backend offers."""
    wf = domain.get("workflow")
    lines = [f"\n\nGENERATED BACKEND (own CAP service): main object {domain['root']}."]
    for e in domain["entities"]:
        for c in e["compositions"]:
            lines.append(f"- {e['name']}.{c['name']} are line items created together with the document "
                         f"(deep insert) and shown as a table on its detail page.")
    if wf:
        lines.append(f"- Workflow on {wf['entity']}.status: " + ", ".join(
            f"action '{t['action']}' ({t['label']}) only when status is {'/'.join(t['from'])}" for t in wf["transitions"])
            + ". Offer these actions on the list and detail page; colour the status ("
            + ", ".join(f"{s['code']}={_CRIT_STATE[s['criticality']]}" for s in wf["states"])
            + f"); show {history_entity(domain)} (navigation 'history') as the approval history table on the "
              f"detail page. Status and calculated fields are read-only.")
    return "\n".join(lines)


def default_blueprint(domain: Dict, model: Dict, requirement: str = "", floorplan: str = "") -> Dict:
    """Rule-based UI blueprint for a standalone app (used when no AI designs the UI):
    root list with status colours, workflow actions and items/history on the detail page."""
    import blueprint as bpm
    wf = domain.get("workflow")
    bp = bpm.fallback(requirement or domain["title"], model)
    es = next(s for s in model["entity_sets"] if s["name"] == domain["root"])
    if bp["main"]["entity_set"] != es["name"]:      # the rules picked items: design the root instead
        bp = bpm.fallback(domain["title"], {**model, "entity_sets": [es]})
    root = next(e for e in domain["entities"] if e["name"] == domain["root"])
    bp["app"]["title"] = domain["title"]
    bp["main"]["entity_set"] = es["name"]
    bp["main"]["title_field"] = root["title_field"] or bp["main"]["title_field"]
    cols = [f["name"] for f in root["fields"] if f["type"] not in ("LargeString",)][:6]
    if wf and "status" not in cols:
        cols.append("status")
    bp["main"]["columns"] = [{"field": c, "importance": "High" if i < 3 else "Medium"} for i, c in enumerate(cols)]
    bp["main"]["sort"] = {"field": "createdAt", "descending": True}
    actions = []
    if wf and wf["entity"] == es["name"]:
        bp["main"]["status"] = {"field": "status",
                                "states": {s["code"]: _CRIT_STATE[s["criticality"]] for s in wf["states"]}}
        actions = [{"label": t["label"], "kind": "operation", "operation": t["action"], "scope": "object",
                    "confirm": True} for t in wf["transitions"]]
    bp["actions"] = actions
    tables = [{"navigation": c["name"], "title": c["label"]} for c in root["compositions"]]
    if wf and wf["entity"] == es["name"]:
        tables.append({"navigation": "history", "title": "Approval History",
                       "columns": ["action", "fromStatus", "toStatus", "comment", "createdBy", "createdAt"]})
    bp["object_page"]["tables"] = tables
    bp["object_page"]["header_fields"] = (["status"] if wf else []) + [
        f["name"] for f in root["fields"] if f.get("formula")][:2]
    bp["form"] = {"create_children": [{"navigation": c["name"]} for c in root["compositions"]][:1]}
    if floorplan:
        bp["floorplan"] = floorplan
    elif bp["floorplan"] not in ("FreestyleApp",):
        bp["floorplan"] = "FreestyleApp"
    if bp["floorplan"] == "FreestyleApp":
        nums = [f["name"] for f in root["fields"] if f["type"] in ("Decimal", "Integer")]
        page = {"type": "list", "entity_set": es["name"], "title": domain["title"],
                "columns": bp["main"]["columns"], "status": bp["main"]["status"], "actions": actions,
                "quick_tabs": bool(wf), "sort": bp["main"]["sort"], "form": bp["form"],
                "detail": {"header_fields": bp["object_page"]["header_fields"], "tables": tables}}
        dash = {"type": "dashboard", "title": "Overview",
                "kpis": [{"label": domain["title"], "entity_set": es["name"], "aggregation": "count"}]
                + [{"label": f"Total {human(n)}", "entity_set": es["name"], "field": n, "aggregation": "sum"}
                   for n in nums[:1]],
                "charts": ([{"title": "By status", "entity_set": es["name"], "dimension": "status",
                             "measure": nums[0] if nums else "", "aggregation": "sum" if nums else "count",
                             "type": "donut"}] if wf else []),
                "lists": [{"title": f"Latest {domain['title']}", "entity_set": es["name"],
                           "sort": {"field": "createdAt", "descending": True}}]}
        pages = [dash, page]
        if re.search(r"report|analy|finance|trend|kpi", requirement, re.I) and nums:
            pages.append({"type": "analytics", "title": "Reporting", "charts": [
                {"title": "Amount by status", "entity_set": es["name"], "dimension": "status" if wf else root["title_field"],
                 "measures": [{"field": nums[0], "aggregation": "sum"}], "type": "column"},
                {"title": "Trend", "entity_set": es["name"], "dimension": "createdAt", "time_grain": "month",
                 "measures": [{"field": nums[0], "aggregation": "sum"}], "type": "line"}]})
        if wf and re.search(r"kanban|board", requirement, re.I):
            pages.append({"type": "kanban", "entity_set": es["name"], "lane_field": "status", "move": False,
                          "lanes": [{"value": s["code"], "title": s["label"]} for s in wf["states"]],
                          "title": f"{domain['title']} Board"})
        bp["pages"] = pages
    bp["reasoning"] = "Rule-based design for the generated backend."
    return bp
