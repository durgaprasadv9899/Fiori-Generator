"""
logic.py — sandboxed custom business logic for generated freestyle apps.

The AI may express requirements the declarative blueprint cannot (calculated
columns, cross-field validations, create defaults, derived fields, row
highlighting, multi-step custom actions) as SMALL JavaScript snippets. They are
never trusted:

  1. A tokenizer whitelist: only a tiny expression language is accepted —
     `row.<Field>` (fields that exist in the entity), local variables, the
     `util` helper API (and `api` inside action scripts), Math/Number/String/
     Date/parse*, literals, operators, if/else/return/var/let/const.
     No loops, no function definitions, no globals (window, document, fetch,
     eval, ...), no computed member access (`a[b]`), no prototype tricks.
  2. Every reference is checked against the service metadata.
  3. `node --check` on the generated module, and every hook is executed once
     against sample rows typed from the metadata (when node is available) —
     ReferenceErrors / SyntaxErrors drop the hook.
  4. At runtime every hook is wrapped (Logic.run): an exception logs a warning
     and falls back (no value / valid / no highlight) instead of breaking the app.

Blueprint shape (per list page, or bp["logic"] for single-entity apps):

  "logic": {
    "computed":    [{"label", "expression", "type": "number|text|date", "after": <field>}],
    "row_state":   "<expression -> 'Error'|'Warning'|'Success'|'Information'|'None'>",
    "validations": [{"expression": "<truthy = valid>", "message", "fields": [..]}],
    "defaults":    [{"field", "expression"}],          // create form initial values
    "derived":     [{"field", "expression"}]           // set before create/save
  }
  actions[] may contain {"kind": "script", "label", "scope", "confirm", "script": "<statements>"}
  using api.set(field, value), api.call(operation, {params}), api.message(text), api.fail(text).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional, Tuple

MAX_EXPR = 600
MAX_SCRIPT = 2000

# JS tokens (no comments, no template literals, no regex literals)
_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
  | (?P<str>'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*")
  | (?P<id>[A-Za-z_$][A-Za-z0-9_$]*)
  | (?P<op>===|!==|==|!=|<=|>=|&&|\|\||\?\?|\+\+|--|[-+*/%<>!?:.,;(){}\[\]=])
""", re.X)

KEYWORDS_EXPR = {"true", "false", "null", "undefined", "typeof", "new"}
KEYWORDS_SCRIPT = KEYWORDS_EXPR | {"if", "else", "return", "var", "let", "const"}
GLOBALS = {"row", "util", "Math", "Number", "String", "Boolean", "Date", "parseFloat",
           "parseInt", "isNaN", "isFinite", "NaN", "Infinity"}
DENY_PROPS = {"constructor", "__proto__", "prototype", "__defineGetter__", "__defineSetter__",
              "__lookupGetter__", "__lookupSetter__", "call", "apply", "bind", "caller",
              "callee", "arguments", "valueOf", "toString"}
SAFE_PROPS = {
    # strings
    "length", "indexOf", "lastIndexOf", "includes", "startsWith", "endsWith", "toUpperCase",
    "toLowerCase", "trim", "substring", "substr", "slice", "charAt", "padStart", "padEnd",
    "split", "join", "replace", "concat", "localeCompare",
    # numbers / Math
    "toFixed", "abs", "round", "floor", "ceil", "min", "max", "pow", "sqrt", "sign", "trunc",
    "PI", "isInteger", "isFinite", "isNaN", "parseFloat", "parseInt",
    # dates
    "getTime", "getFullYear", "getMonth", "getDate", "getDay", "getHours", "getMinutes",
    "toISOString", "now",
    # arrays
    "some", "every",
}
UTIL_API = {
    "today": "Today at 00:00 (Date).",
    "now": "Current date-time (Date).",
    "toDate": "Any date value (Date, ISO string, /Date(..)/) -> Date or null.",
    "daysBetween": "util.daysBetween(a, b): whole days from a to b (b - a).",
    "addDays": "util.addDays(date, n) -> Date.",
    "num": "util.num(v): number (null/'' -> 0).",
    "round": "util.round(v, decimals).",
    "isEmpty": "util.isEmpty(v): null, undefined or blank string.",
    "contains": "util.contains(text, part): case-insensitive substring test.",
    "oneOf": "util.oneOf(v, ['A','B']): v is one of the values.",
    "pct": "util.pct(part, total): percentage (0 when total is 0).",
    "concat": "util.concat(a, b, ...): join non-empty parts with a space.",
}
API_SCRIPT = {"set", "call", "message", "fail"}
DENY_NAMES = {"api", "window", "document", "self", "globalThis", "top", "parent", "frames",
              "this", "eval", "Function", "sap", "jQuery", "$", "location", "navigator",
              "fetch", "XMLHttpRequest", "localStorage", "sessionStorage", "require", "module"}
STATES = ("Error", "Warning", "Success", "Information", "None")


class LogicError(ValueError):
    pass


def _tokens(src: str) -> List[Tuple[str, str]]:
    out, pos = [], 0
    while pos < len(src):
        m = _TOKEN.match(src, pos)
        if not m:
            raise LogicError(f"unsupported character {src[pos]!r}")
        kind = m.lastgroup
        if kind != "ws":
            out.append((kind, m.group()))
        pos = m.end()
    return out


def check(src: str, fields: set, *, script: bool = False, operations: Optional[set] = None) -> List[str]:
    """Validate a snippet; returns the row fields it reads. Raises LogicError."""
    if not isinstance(src, str) or not src.strip():
        raise LogicError("empty")
    if len(src) > (MAX_SCRIPT if script else MAX_EXPR):
        raise LogicError("too long")
    if "`" in src or "/*" in src or "//" in src or "=>" in src:
        raise LogicError("comments, template literals and arrow functions are not allowed")
    toks = _tokens(src)
    keywords = KEYWORDS_SCRIPT if script else KEYWORDS_EXPR
    allowed = set(GLOBALS) | ({"api"} if script else set())
    used: List[str] = []
    depth = 0
    for i, (kind, val) in enumerate(toks):
        prev = toks[i - 1] if i else ("", "")
        nxt = toks[i + 1] if i + 1 < len(toks) else ("", "")
        if kind == "op":
            if val in ("{", "}") and not script:
                raise LogicError("blocks are only allowed in action scripts")
            if val == ";" and not script:
                raise LogicError("statements are only allowed in action scripts")
            if val == "=" and not script:
                raise LogicError("assignment is not allowed in expressions")
            if val in ("++", "--"):
                raise LogicError("++/-- are not allowed")
            if val == "[" and (prev[0] in ("id", "str", "num") and prev[1] not in keywords
                               or prev[1] in (")", "]")):
                raise LogicError("computed member access a[b] is not allowed")
            if val == "(":
                depth += 1
            if val == ")":
                depth -= 1
            continue
        if kind != "id":
            continue
        if prev == ("op", "."):
            # property access
            obj = toks[i - 2] if i >= 2 else ("", "")
            # api.<name> is checked against its own whitelist (api.call is the API, not
            # Function.prototype.call); everything else must not touch prototype plumbing.
            if val in DENY_PROPS and not (obj == ("id", "api") and val in API_SCRIPT):
                raise LogicError(f"property '{val}' is not allowed")
            if obj == ("id", "row"):
                if val not in fields:
                    raise LogicError(f"row.{val} is not a field of this entity")
                if val not in used:
                    used.append(val)
            elif obj == ("id", "util"):
                if val not in UTIL_API:
                    raise LogicError(f"util.{val} does not exist")
            elif obj == ("id", "api"):
                if val not in API_SCRIPT:
                    raise LogicError(f"api.{val} does not exist")
            elif val not in SAFE_PROPS:
                raise LogicError(f"property '.{val}' is not allowed")
            continue
        if nxt == ("op", ":") and prev[1] in ("{", ","):
            continue   # object-literal key: {SalesOrder: row.SalesOrder}
        if val in keywords:
            if val == "new" and nxt != ("id", "Date"):
                raise LogicError("only 'new Date(...)' is allowed")
            continue
        if script and prev[1] in ("var", "let", "const"):
            if val in GLOBALS or val in DENY_NAMES or val in DENY_PROPS:
                raise LogicError(f"cannot redeclare '{val}'")
            allowed.add(val)
            continue
        if val not in allowed:
            raise LogicError(f"identifier '{val}' is not allowed (use row.<Field>, util.*, Math...)")
    if depth != 0:
        raise LogicError("unbalanced parentheses")
    if script and operations is not None:
        for m in re.finditer(r"api\.call\(\s*(['\"])([^'\"]+)\1", src):
            if m.group(2) not in operations:
                raise LogicError(f"api.call: operation '{m.group(2)}' is not available")
        for m in re.finditer(r"api\.set\(\s*(['\"])([^'\"]+)\1", src):
            if m.group(2) not in fields:
                raise LogicError(f"api.set: '{m.group(2)}' is not a field")
    return used


def validate(raw: Optional[Dict], es: Dict, note, where: str = "") -> Dict:
    """Validated logic block for one entity set (drops anything unsafe, with a note)."""
    raw = raw if isinstance(raw, dict) else {}
    props = {p["name"]: p for p in es["properties"]}
    fields = set(props)
    pfx = f"[{where}] " if where else ""
    out = {"computed": [], "row_state": None, "validations": [], "defaults": [], "derived": []}

    def ok(expr, what):
        try:
            return check(expr, fields)
        except LogicError as e:
            note(f"{pfx}Custom logic '{what}' dropped — {e}.")
            return None

    for i, c in enumerate(raw.get("computed") or []):
        if not isinstance(c, dict):
            continue
        label = str(c.get("label") or f"Calculated {i + 1}")[:40]
        used = ok(c.get("expression"), label)
        if used is None:
            continue
        typ = c.get("type") if c.get("type") in ("number", "text", "date") else "text"
        out["computed"].append({"id": f"c{i + 1}", "label": label, "expression": c["expression"].strip(),
                                "type": typ, "uses": used,
                                "after": c.get("after") if c.get("after") in fields else "",
                                "decimals": int(c.get("decimals", 2)) if str(c.get("decimals", 2)).isdigit() else 2})
    rs = raw.get("row_state")
    if isinstance(rs, dict):
        rs = rs.get("expression")
    if rs:
        used = ok(rs, "row highlight")
        if used is not None:
            out["row_state"] = {"expression": rs.strip(), "uses": used}
    for i, v in enumerate(raw.get("validations") or []):
        if not isinstance(v, dict):
            continue
        msg = str(v.get("message") or "Invalid input.")[:200]
        used = ok(v.get("expression"), msg[:40])
        if used is None:
            continue
        out["validations"].append({"id": f"v{i + 1}", "expression": v["expression"].strip(),
                                   "message": msg, "uses": used,
                                   "fields": [f for f in (v.get("fields") or used) if f in fields]})
    for kind in ("defaults", "derived"):
        seen = set()
        for d in raw.get(kind) or []:
            if not isinstance(d, dict):
                continue
            f = d.get("field")
            if f not in fields:
                note(f"{pfx}Custom logic '{kind}' for unknown field '{f}' dropped.")
                continue
            if kind == "derived" and not props[f].get("updatable", True) and not props[f].get("creatable", True):
                note(f"{pfx}Derived field '{f}' dropped — the service does not let it be written.")
                continue
            if f in seen:
                continue
            used = ok(d.get("expression"), f"{kind} {f}")
            if used is None:
                continue
            seen.add(f)
            out[kind].append({"field": f, "expression": d["expression"].strip(), "uses": used,
                              "uiType": props[f]["ui_type"], "edmType": props[f].get("type", "")})
    return out


def validate_script_action(a: Dict, es: Dict, operations: Dict, note, label: str) -> Optional[Dict]:
    fields = {p["name"] for p in es["properties"]}
    try:
        used = check(a.get("script"), fields, script=True, operations=set(operations))
    except LogicError as e:
        note(f"Action '{label}' (custom script) dropped — {e}.")
        return None
    calls = sorted({m.group(2) for m in re.finditer(r"api\.call\(\s*(['\"])([^'\"]+)\1", a["script"])})
    sets = sorted({m.group(2) for m in re.finditer(r"api\.set\(\s*(['\"])([^'\"]+)\1", a["script"])})
    for f in sets:
        p = next(p for p in es["properties"] if p["name"] == f)
        if not p.get("updatable", True):
            note(f"Action '{label}' (custom script) dropped — it sets read-only field {f}.")
            return None
    return {"script": a["script"].strip(), "uses": used, "calls": calls, "sets": sets}


def is_empty(logic: Optional[Dict]) -> bool:
    return not logic or not any(logic.get(k) for k in ("computed", "row_state", "validations",
                                                         "defaults", "derived", "measures"))


# ─────────────────────────────────────────────────────────────────────────────
# JavaScript module
# ─────────────────────────────────────────────────────────────────────────────
UTIL_JS = r'''
    function toDate(v) {
        if (v === null || v === undefined || v === "") { return null; }
        if (v instanceof Date) { return v; }
        var m = /\/Date\((-?\d+)/.exec(String(v));
        if (m) { return new Date(Number(m[1])); }
        var d = new Date(v);
        return isNaN(d.getTime()) ? null : d;
    }
    var util = {
        today: function () { var d = new Date(); d.setHours(0, 0, 0, 0); return d; },
        now: function () { return new Date(); },
        toDate: toDate,
        daysBetween: function (a, b) {
            var x = toDate(a), y = toDate(b);
            if (!x || !y) { return null; }
            return Math.round((Date.UTC(y.getFullYear(), y.getMonth(), y.getDate()) -
                Date.UTC(x.getFullYear(), x.getMonth(), x.getDate())) / 86400000);
        },
        addDays: function (d, n) { var x = toDate(d) || new Date(); x = new Date(x.getTime()); x.setDate(x.getDate() + Number(n || 0)); return x; },
        num: function (v) { var n = Number(v); return v === null || v === undefined || v === "" || isNaN(n) ? 0 : n; },
        round: function (v, d) { var f = Math.pow(10, d || 0); return Math.round(Number(v || 0) * f) / f; },
        isEmpty: function (v) { return v === null || v === undefined || String(v).trim() === ""; },
        contains: function (t, p) { return String(t || "").toLowerCase().indexOf(String(p || "").toLowerCase()) >= 0; },
        oneOf: function (v, a) { return (a || []).map(String).indexOf(String(v)) >= 0; },
        pct: function (a, b) { b = Number(b || 0); return b ? Number(a || 0) / b * 100 : 0; },
        concat: function () { return Array.prototype.slice.call(arguments).filter(function (x) { return x !== null && x !== undefined && String(x) !== ""; }).join(" "); }
    };
'''


def _fn(params: str, body: str) -> str:
    return f'function ({params}) {{ "use strict"; {body} }}'


def module_js(pages: Dict[str, Dict], scripts: Dict[str, Dict]) -> str:
    """model/logic.js — {pageId: {...hooks}}; scripts: {actionId: {...}}."""
    parts = []
    for pid, lg in pages.items():
        if is_empty(lg):
            continue
        items = []
        if lg.get("computed"):
            items.append("computed: {" + ", ".join(
                f'{json.dumps(c["id"])}: {_fn("row", "return (" + c["expression"] + ");")}'
                for c in lg["computed"]) + "}")
        if lg.get("row_state"):
            items.append("rowState: " + _fn("row", "return (" + lg["row_state"]["expression"] + ");"))
        if lg.get("validations"):
            items.append("validations: [" + ", ".join(
                "{" + f'id: {json.dumps(v["id"])}, message: {json.dumps(v["message"])}, '
                f'fields: {json.dumps(v["fields"])}, test: {_fn("row", "return (" + v["expression"] + ");")}' + "}"
                for v in lg["validations"]) + "]")
        if lg.get("measures"):
            items.append("measures: {" + ", ".join(
                f'{json.dumps(m["id"])}: {_fn("row", "return (" + m["expression"] + ");")}'
                for m in lg["measures"]) + "}")
        for kind in ("defaults", "derived"):
            if lg.get(kind):
                items.append(kind + ": {" + ", ".join(
                    f'{json.dumps(d["field"])}: {_fn("row", "return (" + d["expression"] + ");")}'
                    for d in lg[kind]) + "}")
        parts.append(f"        {json.dumps(pid)}: {{\n            " + ",\n            ".join(items) + "\n        }")
    sc = [f"        {json.dumps(aid)}: {_fn('row, api', s['script'])}" for aid, s in scripts.items()]
    return ('sap.ui.define([], function () {\n    "use strict";\n'
            '    // Generated custom business logic (sandboxed: validated against the service\n'
            '    // metadata, executed through Logic.run so a failing hook never breaks the app).\n'
            + UTIL_JS +
            '    var PAGES = {\n' + ",\n".join(parts) + "\n    };\n"
            '    var SCRIPTS = {\n' + ",\n".join(sc) + "\n    };\n"
            '''
    function run(fn, args, fallback) {
        if (typeof fn !== "function") { return fallback; }
        try {
            var v = fn.apply(null, args);
            return v === undefined || (typeof v === "number" && isNaN(v)) ? fallback : v;
        } catch (e) {
            if (window.console) { window.console.warn("[custom logic] " + e.message); }
            return fallback;
        }
    }

    return {
        util: util,
        page: function (sPage) { return PAGES[sPage] || {}; },
        script: function (sAction) { return SCRIPTS[sAction]; },
        run: run,
        /** row object from a composite binding's part values, in `aFields` order */
        rowOf: function (aFields, aValues) {
            var o = {};
            aFields.forEach(function (f, i) { o[f] = aValues[i]; });
            return o;
        }
    };
});
''')


# ─────────────────────────────────────────────────────────────────────────────
# Runtime check (node): syntax + one execution per hook against sample rows
# ─────────────────────────────────────────────────────────────────────────────
def node_available() -> bool:
    return bool(shutil.which("node"))


def _sample_value(p: Dict, variant: int):
    t, ui = p.get("type", "Edm.String"), p.get("ui_type", "text")
    if variant == 1:
        return None
    if ui == "boolean":
        return True
    if ui in ("number", "amount"):
        return "125.50" if t == "Edm.Decimal" else 12
    if ui in ("date", "datetime"):
        return "2026-05-17T00:00:00Z"
    return "A"


def runtime_check(src: str, es: Dict, hooks: List[Tuple[str, str]]) -> Dict[str, str]:
    """Run `hooks` [(path, kind)] of a logic.js module in node; returns {path: error}
    for hooks that threw a ReferenceError/SyntaxError (TypeErrors on null data are
    expected and handled by Logic.run at runtime)."""
    if not node_available():
        return {}
    rows = [{p["name"]: _sample_value(p, v) for p in es["properties"]} for v in (0, 1)]
    harness = ("var window = {console: console};\n"
               "var sap = {ui: {define: function (d, f) { module.exports = f(); }}};\n"
               + src +
               "\nvar L = module.exports, rows = " + json.dumps(rows) + ", bad = {};\n"
               "var api = {set: function(){}, call: function(){ return Promise.resolve(); }, "
               "message: function(){}, fail: function(){ throw new Error('fail'); }};\n"
               "function probe(key, fn, args) { try { fn.apply(null, args); } catch (e) {"
               " if (e instanceof ReferenceError || e instanceof SyntaxError) { bad[key] = e.message; } } }\n"
               + "".join(
                   f"rows.forEach(function (r) {{ var f = {path}; if (f) probe({json.dumps(path)}, f, "
                   + ("[r, api]" if kind == "script" else "[r]") + "); });\n"
                   for path, kind in hooks)
               + "console.log(JSON.stringify(bad));\n")
    with tempfile.TemporaryDirectory() as d:
        fp = os.path.join(d, "logic_check.js")
        with open(fp, "w", encoding="utf-8") as f:
            f.write(harness)
        try:
            r = subprocess.run(["node", fp], capture_output=True, text=True, timeout=20)
        except Exception as e:  # noqa: BLE001
            return {"*": f"node failed: {e}"}
    if r.returncode != 0:
        return {"*": (r.stderr or r.stdout).strip().splitlines()[-1][:300] if (r.stderr or r.stdout) else "error"}
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        return {}


def prompt_doc() -> str:
    """The sandbox contract, for the AI system prompt."""
    return (
        "CUSTOM LOGIC (optional, per list page as \"logic\", and action kind \"script\"): small JavaScript "
        "EXPRESSIONS over the current record `row` — ONLY when the requirement asks for calculations, "
        "rules, validations, defaults or highlighting that columns/filters cannot express.\n"
        "  Allowed: row.<Field> (real fields only), literals, + - * / % == === != !== < > <= >= && || ?? ! ?:, "
        "Math.*, Number(), String(), parseFloat(), new Date(), string/number methods "
        "(toUpperCase, indexOf, includes, toFixed, slice, ...), and util.*: "
        + "; ".join(f"util.{k} — {v}" for k, v in UTIL_API.items()) + ".\n"
        "  NOT allowed: loops, function definitions, arrow functions, globals (window, document, fetch, eval), "
        "a[b] member access, assignments (except in scripts), comments.\n"
        '  "logic": {"computed": [{"label": str, "expression": str, "type": "number|text|date", "decimals": int, '
        '"after": <field>}],  // extra calculated table column + header figure\n'
        '            "row_state": "<expr returning \'Error\'|\'Warning\'|\'Success\'|\'Information\'|\'None\'>",  '
        '// row highlight\n'
        '            "validations": [{"expression": "<true = valid>", "message": str, "fields": [str]}],  '
        '// checked on create + save\n'
        '            "defaults": [{"field": str, "expression": str}],   // create-form initial values\n'
        '            "derived": [{"field": str, "expression": str}]}    // written before create/save\n'
        '  action {"kind": "script", "label": str, "scope": "row|object", "confirm": bool, '
        '"enabled_when": {...}, "script": "<statements>"}: statements with var/let/const/if/else/return '
        "and api.set('<Field>', value) (updatable fields), api.call('<operation>', {params}) (operations "
        "listed for the entity), api.message(text), api.fail(text) to abort. Runs per selected record; "
        "all api.set changes of a record are saved together.\n"
        "  Example: \"row_state\": \"util.daysBetween(row.RequestedDeliveryDate, util.today()) > 0 && "
        "row.OverallDeliveryStatus !== 'C' ? 'Error' : 'None'\"\n"
    )
