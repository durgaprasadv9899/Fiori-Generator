"""
adopt_scan.py — understand an existing project: which frameworks, which modules, how they connect,
the data model, the screens / endpoints, how it is built and tested, what is missing — and the
questions the studio still has to ask the user ("fill the gaps").

Supported stacks (depth of support per module = `level`):
  full     CAP Node.js (db + srv), Fiori Elements V2/V4, Overview Page
  code     SAPUI5 freestyle, CAP Java, React, Angular, Vue, Node.js APIs (Express, Fastify, Nest, Koa),
           TypeScript, Python (FastAPI, Flask, Django, Streamlit), static web
  config   application router, HTML5 deployer, HDI content, CI files

Everything here is static analysis (no code is executed); `understand()` adds an AI summary.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import xml.etree.ElementTree as ET
from typing import Callable, Dict, List, Optional, Tuple

import adopt_files

CAPS = ("understand", "preview", "designers", "ai_change", "validate", "deploy")

# kind -> (label, level, caps "yyyyyy" in CAPS order, role)
KINDS: Dict[str, Tuple[str, str, str, str]] = {
    "cap-db": ("CDS data model", "full", "yyyyyy", "data"),
    "cap-srv": ("CAP Node.js service", "full", "yyyyyy", "service"),
    "cap-java": ("CAP Java service", "code", "ypnyyy", "service"),
    "fe": ("Fiori Elements", "full", "yyyyyy", "ui"),
    "ovp": ("Fiori Overview Page", "full", "yyyyyy", "ui"),
    "ui5": ("SAPUI5 freestyle", "code", "yypyyy", "ui"),
    "react": ("React", "code", "yypyyy", "ui"),
    "angular": ("Angular", "code", "yypyyy", "ui"),
    "vue": ("Vue", "code", "yypyyy", "ui"),
    "static": ("Static web content", "code", "yynyyy", "ui"),
    "node-api": ("Node.js API", "code", "yypyyy", "service"),
    "python": ("Python application", "code", "yypyyy", "service"),
    "java": ("Java application", "code", "ynnyyp", "service"),
    "approuter": ("Application router", "config", "ynnpyy", "entry"),
    "html5-deployer": ("HTML5 app deployer", "config", "ynnpyy", "entry"),
    "hdb": ("SAP HANA HDI content", "config", "ynpyyy", "data"),
    "config": ("Configuration", "config", "ynnyyn", "other"),
}
LEVEL_ORDER = {"full": 0, "code": 1, "config": 2}


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────
def _json(files: Dict[str, str], path: str) -> Dict:
    c = files.get(path)
    if not c or adopt_files.is_binary(c):
        return {}
    try:
        v = json.loads(re.sub(r"^﻿", "", c))
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def _yaml(files: Dict[str, str], path: str):
    c = files.get(path)
    if not c:
        return None
    try:
        import yaml
        return yaml.safe_load(c)
    except Exception:  # noqa: BLE001
        return None


def _dir(path: str) -> str:
    d = posixpath.dirname(path)
    return d + "/" if d else ""


def _under(path: str, root: str) -> bool:
    return path.startswith(root)


def _deps(pkg: Dict) -> Dict[str, str]:
    d = {}
    for k in ("dependencies", "devDependencies", "peerDependencies"):
        d.update(pkg.get(k) or {})
    return d


def _text(files: Dict[str, str], root: str = "", ext: Tuple[str, ...] = ()) -> List[Tuple[str, str]]:
    return [(p, c) for p, c in files.items() if _under(p, root) and (not ext or p.endswith(ext))
            and not adopt_files.is_binary(c)]


def _cmd_prefix(root: str) -> str:
    return f"cd {root.rstrip('/')} && " if root else ""


# ─────────────────────────────────────────────────────────────────────────────
# CDS model
# ─────────────────────────────────────────────────────────────────────────────
_CDS_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_CDS_ANNO = re.compile(r"@\w[\w.#]*\s*(:\s*('[^']*'|\[[^\]]*\]|\{[^{}]*\}|[\w.#'{}-]+))?")
_ASPECT_FIELDS = {"cuid": [("ID", "UUID", True)],
                  "managed": [("createdAt", "Timestamp", False), ("createdBy", "User", False),
                              ("modifiedAt", "Timestamp", False), ("modifiedBy", "User", False)],
                  "temporal": [("validFrom", "Timestamp", False), ("validTo", "Timestamp", False)]}


def _block(text: str, start: int) -> Tuple[str, int]:
    """Text between the brace at `start` and its matching brace."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:i], i
    return text[start + 1:], len(text)


def _split_top(body: str, sep: str = ";") -> List[str]:
    out, depth, cur = [], 0, []
    for ch in body:
        if ch in "{([":
            depth += 1
        elif ch in "})]":
            depth -= 1
        if ch == sep and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur))
    return out


def _cds_fields(body: str) -> List[Dict]:
    fields = []
    for el in _split_top(body):
        el = _CDS_ANNO.sub("", el).strip()
        if not el or el.startswith(("action ", "function ")):
            continue
        m = re.match(r"(key\s+)?(virtual\s+)?(\w+)\s*:\s*(.+)$", el, re.S)
        if not m:
            continue
        typ = re.sub(r"\s+", " ", m.group(4)).strip()
        assoc = ""
        am = re.match(r"(?:Association|Composition)\s+(?:to\s+)?(?:many\s+|one\s+)?(?:of\s+)?(?:many\s+|one\s+)?([\w.]+)", typ)
        if am:
            assoc = am.group(1).split(".")[-1]
            typ = ("Composition of " if typ.startswith("Composition") else "→ ") + assoc
        elif "{" in typ:
            typ = "structured"
        else:
            typ = re.sub(r"\s*(default|not null|null|=).*$", "", typ).strip()[:40]
        fields.append({"name": m.group(3), "type": typ, "key": bool(m.group(1)), "assoc": assoc})
    return fields


def parse_cds(files: Dict[str, str], roots: List[str]) -> Dict:
    """{entities: [{name, namespace, file, fields}], services: [{name, path, file, entities, actions}]}."""
    entities, services = [], []
    for path, text in files.items():
        if not path.endswith(".cds") or adopt_files.is_binary(text) or not any(_under(path, r) for r in roots):
            continue
        t = _CDS_COMMENT.sub("", text)
        ns = (re.search(r"^\s*namespace\s+([\w.]+)", t, re.M) or [None, ""])[1]
        for m in re.finditer(r"\bservice\s+([\w.]+)\s*(@\([^)]*\))?\s*\{", t):
            body, _ = _block(t, m.end() - 1)
            pm = re.search(r"path\s*:\s*'([^']+)'", m.group(2) or "") or \
                re.search(r"@path\s*:\s*'([^']+)'\s*service\s+" + re.escape(m.group(1)), t)
            exposed = [{"name": e.group(1), "on": e.group(2).split(".")[-1]} for e in
                       re.finditer(r"entity\s+(\w+)\s+as\s+(?:projection\s+on|select\s+from)\s+([\w.]+)", body)]
            actions = re.findall(r"\b(?:action|function)\s+(\w+)\s*\(", body)
            services.append({"name": m.group(1), "path": pm.group(1) if pm else "", "file": path,
                             "entities": exposed, "actions": actions})
        for m in re.finditer(r"\bentity\s+(\w+)\s*(?::\s*([\w\s,.]+?))?\s*(@\([^)]*\))?\s*\{", t):
            if re.search(r"\bservice\b[^{}]*\{[^}]*$", t[:m.start()]) and _inside_service(t, m.start()):
                continue
            body, _ = _block(t, m.end() - 1)
            flds = []
            for asp in [a.strip().split(".")[-1] for a in (m.group(2) or "").split(",") if a.strip()]:
                flds += [{"name": n, "type": ty, "key": k, "assoc": "", "from": asp} for n, ty, k in _ASPECT_FIELDS.get(asp, [])]
            flds += _cds_fields(body)
            entities.append({"name": m.group(1), "namespace": ns, "file": path, "fields": flds,
                             "aspects": [a.strip() for a in (m.group(2) or "").split(",") if a.strip()]})
        for m in re.finditer(r"\bextend\s+(?:entity\s+)?([\w.]+)\s+with\s*(?:[\w\s,]*)\{", t):
            body, _ = _block(t, m.end() - 1)
            name = m.group(1).split(".")[-1]
            for e in entities:
                if e["name"] == name:
                    e["fields"] += _cds_fields(body)
    return {"entities": entities, "services": services}


def _inside_service(t: str, pos: int) -> bool:
    for m in re.finditer(r"\bservice\s+[\w.]+\s*(@\([^)]*\))?\s*\{", t[:pos]):
        _, end = _block(t, m.end() - 1)
        if end > pos:
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Other data models (EDMX, Prisma, Knex, TypeORM, SQLAlchemy, Django, SQL)
# ─────────────────────────────────────────────────────────────────────────────
def parse_edmx(text: str) -> List[Dict]:
    out = []
    try:
        root = ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    except ET.ParseError:
        return out
    for et in root.iter():
        if not et.tag.endswith("EntityType"):
            continue
        keys = {pr.get("Name") for pr in et.iter() if pr.tag.endswith("PropertyRef")}
        fields = [{"name": p.get("Name"), "type": (p.get("Type") or "").replace("Edm.", ""), "key": p.get("Name") in keys,
                   "assoc": ""} for p in et if p.tag.endswith("}Property") or p.tag == "Property"]
        fields += [{"name": n.get("Name"), "type": "→ " + (n.get("Type") or n.get("ToRole") or "").split(".")[-1].rstrip(")"),
                    "key": False, "assoc": (n.get("Type") or n.get("ToRole") or "").split(".")[-1].rstrip(")")}
                   for n in et if n.tag.endswith("NavigationProperty")]
        out.append({"name": et.get("Name"), "fields": fields[:60]})
    return out[:80]


def _models_code(files: Dict[str, str]) -> Tuple[str, List[Dict]]:
    ents: List[Dict] = []
    # Prisma
    for p, t in _text(files, ext=(".prisma",)):
        for m in re.finditer(r"\bmodel\s+(\w+)\s*\{([^}]*)\}", t):
            fl = []
            for line in m.group(2).splitlines():
                fm = re.match(r"\s*(\w+)\s+(\w+)(\[\])?\??(.*)$", line)
                if fm and not line.strip().startswith(("@@", "//")):
                    fl.append({"name": fm.group(1), "type": fm.group(2) + (fm.group(3) or ""), "key": "@id" in fm.group(4),
                               "assoc": ""})
            ents.append({"name": m.group(1), "fields": fl, "file": p})
    if ents:
        return "Prisma schema", ents
    # Knex / SQL migrations
    for p, t in _text(files, ext=(".js", ".ts", ".mjs", ".cjs")):
        if "createTable" not in t:
            continue
        for m in re.finditer(r"createTable\(\s*['\"](\w+)['\"]\s*,\s*(?:function\s*\(\s*(\w+)\s*\)|\(?\s*(\w+)\s*\)?\s*=>)", t):
            var = m.group(2) or m.group(3) or "table"
            body = t[m.end():m.end() + 4000]
            body = body[:body.find("createTable")] if "createTable" in body else body
            fl = [{"name": f.group(2), "type": f.group(1), "key": f.group(1) in ("increments", "uuid") and f.group(2) == "id",
                   "assoc": ""} for f in re.finditer(re.escape(var) + r"\.(\w+)\(\s*['\"](\w+)['\"]", body)]
            ents.append({"name": m.group(1), "fields": fl, "file": p})
    if ents:
        return "Knex migrations", ents
    # TypeORM
    for p, t in _text(files, ext=(".ts",)):
        if "@Entity" not in t:
            continue
        for m in re.finditer(r"@Entity\([^)]*\)\s*(?:export\s+)?class\s+(\w+)\s*\{", t):
            body, _ = _block(t, m.end() - 1)
            fl = [{"name": f.group(2), "type": f.group(3).strip(), "key": "Primary" in f.group(1), "assoc": ""}
                  for f in re.finditer(r"@(\w*Column|PrimaryGeneratedColumn|PrimaryColumn)\([^)]*\)\s*(\w+)[!?]?\s*:\s*([\w<>\[\] |]+)", body)]
            ents.append({"name": m.group(1), "fields": fl, "file": p})
    if ents:
        return "TypeORM entities", ents
    # Django / SQLAlchemy
    for p, t in _text(files, ext=(".py",)):
        for m in re.finditer(r"^class\s+(\w+)\(([\w.]*(?:models\.Model|Base|db\.Model|SQLModel|DeclarativeBase)[^)]*)\)\s*:", t, re.M):
            body = t[m.end():]
            nxt = re.search(r"^\S", body, re.M)
            body = body[:nxt.start()] if nxt else body
            fl = [{"name": f.group(1), "type": f.group(2), "key": "primary_key=True" in f.group(0), "assoc": ""}
                  for f in re.finditer(r"^\s+(\w+)\s*(?::\s*[\w\[\]. ]+)?=\s*(?:models\.(\w+)|(?:db\.)?(?:Column|mapped_column|Field)\(\s*(?:db\.)?(\w+)?)[^\n]*", body, re.M)]
            for f in fl:
                f["type"] = f["type"] or "Column"
            if fl:
                ents.append({"name": m.group(1), "fields": fl, "file": p})
    if ents:
        return "Python ORM models", ents
    # plain SQL
    for p, t in _text(files, ext=(".sql",)):
        for m in re.finditer(r"CREATE\s+(?:COLUMN\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`]?([\w.]+)[\"`]?\s*\((.*?)\)\s*;", t, re.S | re.I):
            fl = []
            for col in _split_top(m.group(2), ","):
                cm = re.match(r"\s*[\"`]?(\w+)[\"`]?\s+(\w+)", col)
                if cm and cm.group(1).upper() not in ("PRIMARY", "CONSTRAINT", "FOREIGN", "UNIQUE", "KEY", "INDEX"):
                    fl.append({"name": cm.group(1), "type": cm.group(2), "key": "PRIMARY KEY" in col.upper(), "assoc": ""})
            ents.append({"name": m.group(1).split(".")[-1], "fields": fl, "file": p})
    return ("SQL scripts", ents) if ents else ("", [])


# ─────────────────────────────────────────────────────────────────────────────
# Screens and endpoints
# ─────────────────────────────────────────────────────────────────────────────
_FE_TEMPLATES = {"ListReport": "List Report", "ObjectPage": "Object Page", "AnalyticalListPage": "Analytical List Page",
                 "OVP": "Overview Page", "FPM": "Custom page (FPM)", "Worklist": "Worklist"}


def ui5_screens(man: Dict, root: str) -> List[Dict]:
    out = []
    ui5 = man.get("sap.ui5") or {}
    gen = man.get("sap.ui.generic.app") or {}
    if gen.get("pages"):
        def walk(pages, depth=0):
            items = pages.values() if isinstance(pages, dict) else pages
            for pg in items or []:
                comp = ((pg.get("component") or {}).get("name") or "").split(".")[-1]
                out.append({"name": pg.get("entitySet", "") or comp, "type": _FE_TEMPLATES.get(comp, comp or "Page"),
                            "source": root + "webapp/manifest.json", "editing": "visual"})
                walk(pg.get("pages"), depth + 1)
        walk(gen["pages"])
        return out
    targets = (ui5.get("routing") or {}).get("targets") or {}
    for tid, t in targets.items():
        name = str(t.get("name") or t.get("viewName") or t.get("id") or tid)
        st = (t.get("options") or {}).get("settings") or {}
        if name.startswith("sap.fe.templates") or name.startswith("sap.fe.core.fpm"):
            kind = next((v for k, v in _FE_TEMPLATES.items() if k in name), "Fiori Elements page")
            ent = (st.get("contextPath") or ("/" + st["entitySet"] if st.get("entitySet") else "")).strip("/")
            out.append({"name": ent or tid, "type": kind, "source": root + "webapp/manifest.json", "editing": "visual"})
        else:
            vp = (ui5.get("routing") or {}).get("config", {}).get("viewPath") or ""
            view = name.split(".")[-1]
            out.append({"name": view, "type": "View (" + str(t.get("viewType") or "XML") + ")",
                        "source": root + "webapp/view/" + view + ".view.xml" if not vp or "view" in vp else vp, "editing": "ai"})
    if not out and ui5.get("rootView"):
        rv = ui5["rootView"]
        rv = rv.get("viewName") if isinstance(rv, dict) else rv
        out.append({"name": str(rv).split(".")[-1], "type": "Root view", "source": root + "webapp/", "editing": "ai"})
    return out


def web_screens(files: Dict[str, str], root: str, kind: str) -> List[Dict]:
    out, seen = [], set()
    exts = (".tsx", ".jsx", ".js", ".ts", ".vue")
    for p, t in _text(files, root, exts):
        if "/test" in p or ".spec." in p or ".test." in p:
            continue
        pats = []
        if kind == "react":
            pats = [r"<Route\b[^>]*?\bpath=[\"'{`]+([^\"'`}]+)[\"'`}]+[^>]*?\b(?:element=\{<|component=\{)(\w+)",
                    r"\bpath\s*:\s*[\"'`]([^\"'`]*)[\"'`]\s*,\s*(?:element|Component|component)\s*:\s*<?(\w+)"]
        else:
            pats = [r"\bpath\s*:\s*[\"'`]([^\"'`]*)[\"'`]\s*,\s*(?:component|loadComponent)\s*:\s*(?:\(\)\s*=>\s*import\([^)]*\)\.then\(\s*\w+\s*=>\s*\w+\.)?(\w+)"]
        for pat in pats:
            for m in re.finditer(pat, t):
                key = (m.group(1), m.group(2))
                if key in seen:
                    continue
                seen.add(key)
                out.append({"name": m.group(2), "type": f"Route /{m.group(1).lstrip('/')}", "source": p, "editing": "ai"})
    if not out and kind == "react":              # Next.js file routes
        for p in files:
            m = re.match(re.escape(root) + r"(?:src/)?(?:app/(.*)page|pages/(.*?))\.(tsx|jsx|js)$", p)
            if m and not (m.group(2) or "").startswith(("_", "api/")):
                route = (m.group(1) or m.group(2) or "").rstrip("/").replace("index", "")
                out.append({"name": route or "home", "type": f"Page /{route}", "source": p, "editing": "ai"})
    return out[:60]


def endpoints(files: Dict[str, str], root: str, kind: str) -> List[Dict]:
    out = []
    if kind == "node-api":
        for p, t in _text(files, root, (".js", ".ts", ".mjs", ".cjs")):
            if "node_modules" in p or ".test." in p or ".spec." in p:
                continue
            for m in re.finditer(r"\b(?:app|router|server|fastify|api|r)\.(get|post|put|patch|delete)\(\s*[\"'`]([^\"'`]+)", t):
                out.append({"method": m.group(1).upper(), "path": m.group(2), "source": p})
            for m in re.finditer(r"@(Get|Post|Put|Patch|Delete)\(\s*[\"'`]?([^\"'`)]*)", t):
                out.append({"method": m.group(1).upper(), "path": "/" + m.group(2).lstrip("/"), "source": p})
    elif kind == "python":
        for p, t in _text(files, root, (".py",)):
            for m in re.finditer(r"@\w+\.(get|post|put|patch|delete|route)\(\s*[\"']([^\"']+)", t):
                out.append({"method": "ANY" if m.group(1) == "route" else m.group(1).upper(), "path": m.group(2), "source": p})
            for m in re.finditer(r"\b(?:re_)?path\(\s*r?[\"']([^\"']*)[\"']\s*,\s*([\w.]+)", t):
                out.append({"method": "ANY", "path": "/" + m.group(1), "source": p})
    return out[:120]


# ─────────────────────────────────────────────────────────────────────────────
# Module detection
# ─────────────────────────────────────────────────────────────────────────────
def _module(mid: str, name: str, path: str, kind: str, framework: str, evidence: List[str], **extra) -> Dict:
    label, level, caps, role = KINDS[kind]
    return dict({"id": mid, "name": name, "path": path, "kind": kind, "framework": framework or label,
                 "evidence": " · ".join(e for e in evidence if e), "level": level,
                 "caps": dict(zip(CAPS, caps)), "role": role, "commands": {}}, **extra)


def _node_commands(files: Dict[str, str], root: str, pkg: Dict) -> Dict:
    scripts = pkg.get("scripts") or {}
    c = {"install": _cmd_prefix(root) + ("npm ci" if (root + "package-lock.json") in files else "npm install")}
    if scripts.get("build"):
        c["build"] = _cmd_prefix(root) + "npm run build"
    test = scripts.get("test") or ""
    if test and "no test specified" not in test:
        # Karma / ng test watch by default — run once, headless
        once = " -- --watch=false --browsers=ChromeHeadless" if re.search(r"\bng test\b", test) else ""
        c["test"] = _cmd_prefix(root) + "npm test" + once
    for k in ("start", "dev", "serve", "watch"):
        if scripts.get(k):
            c["run"] = _cmd_prefix(root) + ("npm start" if k == "start" else f"npm run {k}")
            break
    return c


def _py_commands(files: Dict[str, str], root: str, framework: str) -> Dict:
    c = {}
    if root + "requirements.txt" in files:
        c["install"] = _cmd_prefix(root) + "pip install -r requirements.txt"
    elif root + "pyproject.toml" in files or root + "setup.py" in files:
        c["install"] = _cmd_prefix(root) + "pip install ."
    has_tests = any(re.match(re.escape(root) + r"(tests?/|.*test_\w+\.py$)", p) for p in files)
    if has_tests:
        c["test"] = _cmd_prefix(root) + "python -m pytest -q"
    entry = next((p[len(root):] for p in (root + "main.py", root + "app.py", root + "app/main.py", root + "manage.py",
                                         root + "streamlit_app.py") if p in files), "")
    if framework == "FastAPI" and entry:
        mod = entry[:-3].replace("/", ".")
        c["run"] = _cmd_prefix(root) + f"python -m uvicorn {mod}:app --port $PORT"
    elif framework == "Django":
        c["run"] = _cmd_prefix(root) + "python manage.py runserver 127.0.0.1:$PORT"
    elif framework == "Streamlit" and entry:
        c["run"] = _cmd_prefix(root) + f"python -m streamlit run {entry} --server.port $PORT --server.headless true"
    elif framework == "Flask" and entry:
        c["run"] = _cmd_prefix(root) + f"python -m flask --app {entry[:-3]} run --port $PORT"
    elif entry:
        c["run"] = _cmd_prefix(root) + f"python {entry}"
    return c


def _python_framework(files: Dict[str, str], root: str) -> str:
    req = (files.get(root + "requirements.txt") or "") + (files.get(root + "pyproject.toml") or "")
    req = req.lower()
    for key, fw in (("fastapi", "FastAPI"), ("django", "Django"), ("flask", "Flask"), ("streamlit", "Streamlit")):
        if key in req:
            return fw
    code = " ".join(t[:3000] for _, t in _text(files, root, (".py",))[:40])
    for key, fw in (("FastAPI(", "FastAPI"), ("Flask(", "Flask"), ("django", "Django"), ("import streamlit", "Streamlit")):
        if key in code:
            return fw
    return "Python"


def _ui5_version(files: Dict[str, str], root: str, man: Dict) -> str:
    v = ((man.get("sap.ui5") or {}).get("dependencies") or {}).get("minUI5Version") or ""
    v = v[0] if isinstance(v, list) and v else v
    ui5y = files.get(root + "ui5.yaml") or ""
    m = re.search(r"framework:\s*\n(?:\s+.*\n)*?\s+version:\s*[\"']?([\d.]+)", ui5y)
    if m:
        v = m.group(1)
    idx = files.get(root + "webapp/index.html") or ""
    m = re.search(r"(?:sapui5|openui5|ui5)\.(?:hana\.ondemand|sap)\.com/(\d+\.\d+\.\d+)", idx)
    if m:
        v = m.group(1)
    return str(v)


def detect_modules(files: Dict[str, str]) -> Tuple[List[Dict], Dict]:
    """(modules, context) — context keeps parsed descriptors for later steps."""
    mods: List[Dict] = []
    ctx: Dict = {"mta": None, "cap_roots": [], "ui5_apps": []}
    used_roots = set()

    mta = _yaml(files, "mta.yaml")
    if isinstance(mta, dict):
        ctx["mta"] = mta

    pkgs = {p[:-len("package.json")]: _json(files, p) for p in files if p.endswith("package.json")}
    manifests = {}
    for p in files:
        if p.endswith("manifest.json") and not p.startswith(("dist/", "resources/")) and "/dist/" not in p:
            j = _json(files, p)
            if "sap.app" in j:
                d = _dir(p)
                root = d[:-len("webapp/")] if d.endswith("webapp/") else d
                manifests[root] = (p, j)

    # CAP Node.js projects
    for root, pkg in sorted(pkgs.items()):
        deps = _deps(pkg)
        if "@sap/cds" in deps and not any(root.startswith(r) and root != r for r in ctx["cap_roots"]):
            ctx["cap_roots"].append(root)
    for root in ctx["cap_roots"]:
        pkg = pkgs[root]
        cds_conf = (pkg.get("cds") or {})
        rc = _json(files, root + ".cdsrc.json")
        folders = dict({"db": "db/", "srv": "srv/", "app": "app/"}, **((cds_conf.get("folders") or rc.get("folders") or {})))
        db, srv = root + folders["db"].rstrip("/") + "/", root + folders["srv"].rstrip("/") + "/"
        model = parse_cds(files, [db]) if any(p.startswith(db) for p in files) else {"entities": []}
        cdeps = _deps(pkg)
        cmds = _node_commands(files, root, pkg)
        if any(p.startswith(db) for p in files):
            csv = sum(1 for p in files if p.startswith(db + "data/") and p.endswith(".csv"))
            mods.append(_module("db", "db", db, "cap-db", "CDS data model",
                                [f"{len(model['entities'])} entities", f"{csv} CSV seed files" if csv else ""]))
            used_roots.add(db)
        sv = parse_cds(files, [srv, root + "app/"] if root else [srv])
        nodefiles = [p for p in files if p.startswith(srv) and p.endswith((".js", ".ts"))]
        mods.append(_module("srv", "srv", srv if any(p.startswith(srv) for p in files) else root, "cap-srv",
                            f"CAP Node.js {('@sap/cds ' + cdeps.get('@sap/cds', '')).strip()}",
                            [f"{len(sv['services'])} service(s)", f"{sum(len(s['actions']) for s in sv['services'])} actions",
                             f"{len(nodefiles)} handler file(s)", "TypeScript" if "typescript" in cdeps or "@cap-js/cds-typer" in cdeps else ""],
                            commands=cmds, project_root=root,
                            services=[{"name": s["name"], "path": s["path"], "entities": [e["name"] for e in s["entities"]],
                                       "actions": s["actions"]} for s in sv["services"]]))
        used_roots.update({srv, root})

    # CAP Java
    for p in files:
        if p.endswith("pom.xml") and "cds-services" in (files.get(p) or "") and _dir(p) not in used_roots:
            root = _dir(p)
            mods.append(_module("srv-java", (root.strip("/") or "srv"), root, "cap-java", "CAP Java (Spring Boot)",
                                ["pom.xml", f"{sum(1 for q in files if q.startswith(root) and q.endswith('.java'))} Java files"],
                                commands={"build": _cmd_prefix(root) + "mvn -q -B package -DskipTests",
                                          "test": _cmd_prefix(root) + "mvn -q -B test"}))
            used_roots.add(root)

    # UI5 apps (Fiori Elements, OVP, freestyle)
    import app_import
    for root, (mpath, man) in sorted(manifests.items()):
        sub = {p[len(root):]: c for p, c in files.items() if p.startswith(root) and not adopt_files.is_binary(c)}
        try:
            info = app_import.analyze(sub)
        except Exception:  # noqa: BLE001
            info = {"kind": "freestyle", "notes": [], "app_id": (man.get("sap.app") or {}).get("id", "")}
        kind = {"fe_v4": "fe", "fe_v2": "fe", "ovp": "ovp", "generated": "ui5"}.get(info.get("kind"), "ui5")
        app = man.get("sap.app") or {}
        ds = app.get("dataSources") or {}
        views = sum(1 for p in sub if p.endswith((".view.xml", ".view.js")))
        ctrls = sum(1 for p in sub if ".controller." in p)
        ver = _ui5_version(files, root, man)
        fw = {"fe": "Fiori Elements " + ("V4" if info.get("kind") == "fe_v4" else "V2"), "ovp": "Overview Page"}.get(kind)
        fw = fw or ("SAPUI5 freestyle" + (" (studio-generated)" if info.get("kind") == "generated" else ""))
        pkg = pkgs.get(root) or {}
        cmds = _node_commands(files, root, pkg) if pkg else {}
        if pkg and "build" not in cmds and root + "ui5.yaml" in files:
            cmds["build"] = _cmd_prefix(root) + "npx ui5 build --clean-dest"
        name = root.rstrip("/").split("/")[-1] or app.get("id", "webapp").split(".")[-1]
        mid = "ui:" + (root or "webapp/")
        mods.append(_module(mid, name, root or "webapp/", kind, fw + (f" {ver}" if ver else ""),
                            [f"{views} views" if views else "", f"{ctrls} controllers" if ctrls else "",
                             ", ".join(f"{k}: {v.get('uri', '')}" for k, v in list(ds.items())[:2] if v.get("type", "OData") == "OData")],
                            commands=cmds, app_id=app.get("id", ""), ui5_version=ver, manifest=mpath,
                            screens=ui5_screens(man, root), import_kind=info.get("kind"),
                            data_sources={k: {"uri": v.get("uri", ""), "version": (v.get("settings") or {}).get("odataVersion", "2.0"),
                                              "local": (v.get("settings") or {}).get("localUri", "")}
                                          for k, v in ds.items() if v.get("type", "OData") == "OData"}))
        ctx["ui5_apps"].append(root)
        used_roots.add(root)

    # plain HTML / JavaScript front ends that CAP serves from app/ (no UI5 manifest, no own package.json)
    for root in ctx["cap_roots"]:
        app_dir = root + "app/"
        for p in sorted(files):
            if not (p.startswith(app_dir) and p.endswith("index.html")):
                continue
            d = _dir(p)
            if any(d.startswith(u) or u.startswith(d) for u in ctx["ui5_apps"] if u) or d in used_roots or (d + "package.json") in files:
                continue
            html = files.get(p) or ""
            js = sum(1 for q in files if q.startswith(d) and q.endswith((".js", ".mjs")))
            mods.append(_module("web:" + d, d.rstrip("/").split("/")[-1] or "app", d, "static",
                                "HTML / JavaScript web UI (served by CAP)",
                                [f"{html.count(chr(10)) + 1} lines in index.html", f"{js} script file(s)" if js else ""],
                                screens=[{"name": "index.html", "type": "Web page", "source": p, "editing": "ai"}]))
            used_roots.add(d)
            break

    # Other Node.js packages (web frameworks, APIs, approuter)
    for root, pkg in sorted(pkgs.items()):
        if root in used_roots:
            continue
        deps = _deps(pkg)
        ts = "typescript" in deps or (root + "tsconfig.json") in files
        cmds = _node_commands(files, root, pkg)
        name = pkg.get("name") or root.strip("/") or "app"
        if "@sap/approuter" in deps or "@sap/html5-app-deployer" in deps:
            cmds.pop("run", None)                 # a gateway / deployer is never the app to start in a preview
        if "@sap/approuter" in deps:
            routes = len((_json(files, root + "xs-app.json").get("routes") or []))
            mods.append(_module("approuter:" + root, root.strip("/") or "approuter", root, "approuter", "SAP application router",
                                ["xs-app.json", f"{routes} routes"], commands=cmds))
        elif "@sap/html5-app-deployer" in deps:
            mods.append(_module("deployer:" + root, root.strip("/") or "ui-deployer", root, "html5-deployer",
                                "HTML5 app deployer", ["HTML5 repository content"]))
        elif "@angular/core" in deps:
            mods.append(_module("web:" + root, name, root, "angular", f"Angular {deps['@angular/core'].lstrip('^~')}",
                                ["angular.json" if root + "angular.json" in files else "", "TypeScript"],
                                commands=cmds, screens=web_screens(files, root, "angular")))
        elif "react" in deps or "next" in deps:
            tool = "Next.js" if "next" in deps else "Vite" if "vite" in deps else "Create React App" if "react-scripts" in deps else ""
            mods.append(_module("web:" + root, name, root, "react", f"React {deps.get('react', '').lstrip('^~')}" + (f" · {tool}" if tool else ""),
                                ["TypeScript" if ts else "JavaScript", f"{sum(1 for p in files if p.startswith(root) and p.endswith(('.tsx', '.jsx')))} components"],
                                commands=cmds, screens=web_screens(files, root, "react"), tool=tool))
        elif "vue" in deps or "nuxt" in deps:
            mods.append(_module("web:" + root, name, root, "vue", f"Vue {deps.get('vue', '').lstrip('^~')}",
                                ["TypeScript" if ts else "JavaScript", f"{sum(1 for p in files if p.startswith(root) and p.endswith('.vue'))} components"],
                                commands=cmds, screens=web_screens(files, root, "vue")))
        elif any(k in deps for k in ("express", "fastify", "koa", "@nestjs/core", "hapi", "@hapi/hapi", "restify")):
            fw = next(f for k, f in (("@nestjs/core", "NestJS"), ("fastify", "Fastify"), ("koa", "Koa"), ("express", "Express"),
                                     ("@hapi/hapi", "hapi"), ("hapi", "hapi"), ("restify", "Restify")) if k in deps)
            eps = endpoints(files, root, "node-api")
            mods.append(_module("api:" + root, name, root, "node-api", f"Node.js · {fw}" + (" · TypeScript" if ts else ""),
                                [f"{len(eps)} endpoints"], commands=cmds, endpoints=eps))
        elif root == "" and not mods and any(p.endswith((".js", ".ts")) for p in files):
            mods.append(_module("node:" + root, name, root, "node-api", "Node.js" + (" · TypeScript" if ts else ""),
                                ["package.json"], commands=cmds, endpoints=endpoints(files, root, "node-api")))
        else:
            continue
        used_roots.add(root)

    # xs-app.json without a package.json (managed approuter)
    for p in files:
        if p.endswith("xs-app.json") and _dir(p) not in used_roots and not any(_dir(p).startswith(u) for u in used_roots if u):
            mods.append(_module("approuter:" + _dir(p), "managed approuter", _dir(p), "approuter", "Managed application router",
                                [f"{len(_json(files, p).get('routes') or [])} routes"]))
            used_roots.add(_dir(p))

    # Python
    for p in files:
        if p.endswith(("requirements.txt", "pyproject.toml", "setup.py", "manage.py")):
            root = _dir(p)
            if root in used_roots or any(root.startswith(u) for u in used_roots if u and u.endswith("/") and len(u) > 1):
                continue
            if not any(q.startswith(root) and q.endswith(".py") for q in files):
                continue
            fw = _python_framework(files, root)
            eps = endpoints(files, root, "python")
            mods.append(_module("py:" + root, root.strip("/") or "app", root, "python", fw,
                                [f"{sum(1 for q in files if q.startswith(root) and q.endswith('.py'))} Python files",
                                 f"{len(eps)} endpoints" if eps else ""], commands=_py_commands(files, root, fw), endpoints=eps,
                                python_framework=fw))
            used_roots.add(root)

    # Java (non-CAP)
    for p in files:
        if p.endswith(("pom.xml", "build.gradle")) and _dir(p) not in used_roots:
            root = _dir(p)
            mods.append(_module("java:" + root, root.strip("/") or "java", root, "java",
                                "Spring Boot" if "spring-boot" in (files.get(p) or "") else "Java",
                                [p.split("/")[-1]], commands={"build": _cmd_prefix(root) + ("mvn -q -B package" if p.endswith("pom.xml") else "gradle build")}))
            used_roots.add(root)

    # HDI content of mta modules
    if isinstance(mta, dict):
        for m in mta.get("modules") or []:
            path = (m.get("path") or "").strip("./")
            path = path + "/" if path else ""
            if m.get("type") == "hdb" and not any(x["path"] == path for x in mods):
                if any(p.startswith(path) for p in files):
                    mods.append(_module("hdb:" + path, m.get("name", "db-deployer"), path, "hdb", "SAP HANA HDI deployer",
                                        ["mta module"]))
                else:                          # gen/db — produced by `cds build` from the CDS model
                    for x in mods:
                        if x["kind"] == "cap-db":
                            x["evidence"] += f" · deployed to HANA by {m.get('name', 'the db deployer')}"

    # static web content (index.html, no framework)
    if not mods:
        idx = [p for p in files if p.endswith("index.html")]
        if idx:
            root = _dir(sorted(idx, key=len)[0])
            mods.append(_module("static:" + root, root.strip("/") or "web", root, "static", "Static web (HTML/JS/CSS)",
                                [f"{sum(1 for p in files if p.endswith('.html'))} HTML files"]))

    # label modules with their mta.yaml names
    if isinstance(mta, dict):
        for m in mta.get("modules") or []:
            path = (m.get("path") or "").strip().strip("./")
            for x in mods:
                xp = x["path"].rstrip("/")
                if path and (xp == path or (x["kind"] == "cap-srv" and path.startswith(xp.split("/")[0]))):
                    x["mta_module"] = m.get("name")
                    x["mta_type"] = m.get("type")
    mods.sort(key=lambda x: (LEVEL_ORDER[x["level"]], {"data": 0, "service": 1, "ui": 2, "entry": 3, "other": 4}[x["role"]]))
    return mods, ctx


# ─────────────────────────────────────────────────────────────────────────────
# Connections, env, findings
# ─────────────────────────────────────────────────────────────────────────────
_ENV_IGNORE = {"NODE_ENV", "PORT", "HOME", "PATH", "CDS_ENV", "DEBUG", "CI", "TZ", "LANG", "PWD", "USER", "HOST",
               "VCAP_SERVICES", "VCAP_APPLICATION", "BASE_URL", "PUBLIC_URL", "npm_package_version"}


def destinations_used(files: Dict[str, str]) -> List[Dict]:
    out = []
    for p in files:
        if p.endswith("package.json") or p.endswith(".cdsrc.json"):
            j = _json(files, p)
            req = (j.get("cds") or j).get("requires") or {}
            for name, conf in req.items():
                if isinstance(conf, dict):
                    for prof in [conf] + [v for k, v in conf.items() if k.startswith("[") and isinstance(v, dict)]:
                        d = (prof.get("credentials") or {}).get("destination")
                        if d:
                            out.append({"name": d, "used_by": p, "purpose": f"CAP remote service {name}"})
        elif p.endswith("xs-app.json"):
            for r in _json(files, p).get("routes") or []:
                if r.get("destination"):
                    out.append({"name": r["destination"], "used_by": p, "purpose": f"route {r.get('source', '')}"})
        elif p.endswith(("ui5.yaml", "ui5-local.yaml")):
            for m in re.finditer(r"destination:\s*[\"']?([\w-]+)", files[p] or ""):
                out.append({"name": m.group(1), "used_by": p, "purpose": "UI5 tooling proxy"})
    seen, uniq = set(), []
    for d in out:
        if d["name"] not in seen and d["name"] not in ("srv-api", "srv_api"):
            seen.add(d["name"])
            uniq.append(d)
    return uniq


def destinations_defined(mta: Optional[Dict]) -> List[str]:
    names = []
    if not isinstance(mta, dict):
        return names
    for r in mta.get("resources") or []:
        inst = (((r.get("parameters") or {}).get("config") or {}).get("init_data") or {})
        for scope in ("instance", "subaccount"):
            for d in ((inst.get(scope) or {}).get("destinations") or []):
                if d.get("Name"):
                    names.append(d["Name"])
    for m in mta.get("modules") or []:
        for r in m.get("provides") or []:
            names.append(r.get("name", ""))
        for r in m.get("requires") or []:
            if (r.get("group") == "destinations") or (r.get("properties") or {}).get("name"):
                names.append((r.get("properties") or {}).get("name", r.get("name", "")))
    return [n for n in names if n]


def env_vars(files: Dict[str, str]) -> List[str]:
    found = set()
    for p, t in files.items():
        if adopt_files.is_binary(t) or not p.endswith((".js", ".ts", ".mjs", ".cjs", ".py", ".tsx", ".jsx")):
            continue
        for m in re.finditer(r"process\.env\.([A-Z][A-Z0-9_]{2,})|process\.env\[[\"']([A-Z][A-Z0-9_]{2,})|"
                             r"os\.environ(?:\.get)?\(?\[?\s*[\"']([A-Z][A-Z0-9_]{2,})|os\.getenv\(\s*[\"']([A-Z][A-Z0-9_]{2,})|"
                             r"import\.meta\.env\.([A-Z][A-Z0-9_]{2,})", t):
            v = next(g for g in m.groups() if g)
            if v not in _ENV_IGNORE and not v.startswith(("npm_", "VCAP_")):
                found.add(v)
    return sorted(found)[:40]


def findings(files: Dict[str, str], mods: List[Dict], ctx: Dict, dests: List[Dict], src_info: Dict) -> List[Dict]:
    out = []
    for s in src_info.get("skipped") or []:
        if s["why"].startswith("secrets"):
            out.append({"severity": "bad", "text": f"{s['path']} looks like it holds credentials and was not imported.",
                        "fix": "If this file was ever committed or shared, rotate the credentials in it."})
    big = [s for s in src_info.get("skipped") or [] if "larger" in s["why"] or "limit" in s["why"]]
    if big:
        out.append({"severity": "warn", "text": f"{len(big)} file(s) were too large and were not imported (e.g. {big[0]['path']}).",
                    "fix": "Large binaries stay in the original source; the studio keeps the rest."})
    for d in dests:
        if ctx.get("mta") and not d.get("internal"):
            out.append({"severity": "warn", "text": f"Destination {d['name']} is used by {d['used_by']} ({d['purpose']}) "
                                                    "but mta.yaml does not define or bind it.",
                        "fix": "Map it to a BTP destination when you fill the gaps."})
    for m in mods:
        v = m.get("ui5_version") or ""
        mv = re.match(r"(\d+)\.(\d+)", v)
        if mv and int(mv.group(1)) == 1 and int(mv.group(2)) < 108:
            out.append({"severity": "warn", "text": f"{m['name']} targets SAPUI5 {v}, an older release line.",
                        "fix": "Check the SAPUI5 maintenance status; the AI assistant can upgrade it as a separate change."})
        if m["kind"] in ("cap-srv", "node-api", "react", "angular", "vue", "python") and "test" not in m.get("commands", {}):
            out.append({"severity": "info", "text": f"{m['name']} has no automated tests.",
                        "fix": "Validation still installs, builds and starts it; ask the AI assistant to add tests."})
        if m["kind"] == "ui5" and m.get("import_kind") == "generated":
            out.append({"severity": "info", "text": f"{m['name']} was generated by this studio.",
                        "fix": "Use Projects → Import to restore its visual design instead, if you prefer."})
    if not mods:
        out.append({"severity": "bad", "text": "No supported framework was recognised.",
                    "fix": "The AI assistant can still read and change the files; preview and validation are not available."})
    if any(m["kind"] == "cap-java" for m in mods):
        out.append({"severity": "info", "text": "CAP Java needs Maven and a JDK to build; the studio runner does not have them yet.",
                    "fix": "AI changes work; builds and preview of the Java service are skipped."})
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Architecture columns
# ─────────────────────────────────────────────────────────────────────────────
def architecture(mods: List[Dict], dests: List[Dict], model_src: str, envs: List[str]) -> List[List[Dict]]:
    cols: List[List[Dict]] = [[{"id": "users", "label": "Users", "sub": "browser"}]]
    entry = [m for m in mods if m["role"] == "entry"]
    if entry:
        cols.append([{"id": m["id"], "label": m["name"], "sub": m["framework"]} for m in entry])
    ui = [m for m in mods if m["role"] == "ui"]
    if ui:
        cols.append([{"id": m["id"], "label": m["name"], "sub": m["framework"]} for m in ui])
    svc = [m for m in mods if m["role"] == "service"]
    if svc:
        cols.append([{"id": m["id"], "label": m["name"], "sub": m["framework"]} for m in svc])
    data = [{"id": m["id"], "label": m["name"], "sub": m["framework"]} for m in mods if m["role"] == "data"]
    if not data and model_src:
        data = [{"id": "database", "label": "Database", "sub": model_src}]
    data += [{"id": "dest:" + d["name"], "label": d["name"], "sub": "destination · " + d["purpose"], "external": True}
             for d in dests if not d.get("internal")][:4]
    if any(e in envs for e in ("DATABASE_URL", "POSTGRES_URL", "MONGODB_URI", "MONGO_URL", "REDIS_URL")):
        data.append({"id": "env:db", "label": "External database", "sub": "from environment", "external": True})
    if data:
        cols.append(data)
    return cols


# ─────────────────────────────────────────────────────────────────────────────
# Questions ("fill the gaps")
# ─────────────────────────────────────────────────────────────────────────────
def _guess_purpose(files: Dict[str, str], mods: List[Dict]) -> str:
    for p in ("README.md", "readme.md", "README.MD", "README"):
        t = re.sub(r"```.*?```", "", files.get(p) or "", flags=re.S)
        t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", t)                 # markdown links / images -> text
        paras = [re.sub(r"\s+", " ", x.strip()) for x in re.split(r"\n\s*\n", t)]
        paras = [x for x in paras if len(x) > 50 and not re.match(r"^(#|\||<|-|\*|>|\d+\.|\[)", x)]
        if paras:
            return paras[0][:600]
    root = _json(files, "package.json")
    if root.get("description"):
        return root["description"][:600]
    for m in mods:
        if m.get("manifest"):
            app = (_json(files, m["manifest"]).get("sap.app") or {})
            d = app.get("description") or app.get("title") or ""
            if d and not d.startswith("{{"):
                return d
    return ""


def _protect_suggestions(files: Dict[str, str], mods: List[Dict]) -> List[Dict]:
    s = []
    def add(path, checked, why):
        if path not in [x["value"] for x in s]:
            s.append({"value": path, "label": path, "hint": why, "checked": checked})
    for p in files:
        if p.endswith("xs-security.json"):
            add(p, True, "roles and scopes")
        if p.endswith(("mta.yaml",)):
            add(p, False, "deployment descriptor")
    if any(re.search(r"(^|/)db/data/.*\.csv$", p) for p in files):
        add(next(re.sub(r"[^/]+\.csv$", "*.csv", p) for p in files if re.search(r"(^|/)db/data/.*\.csv$", p)), False, "seed data")
    if any("/migrations/" in "/" + p for p in files):
        add(next(p.split("migrations/")[0] + "migrations/**" for p in files if "migrations/" in p), True,
            "existing migrations (new ones may be added)")
    if any(p.startswith(".github/") for p in files):
        add(".github/**", True, "CI pipelines")
    if any("/external/" in "/" + p for p in files if p.endswith((".cds", ".edmx", ".xml"))):
        add(next(p.split("external/")[0] + "external/**" for p in files if "external/" in p), True, "imported external APIs")
    if any("localService/" in p for p in files):
        add(next(p.split("localService/")[0] + "localService/**" for p in files if "localService/" in p), True,
            "service metadata copies")
    if "Dockerfile" in files:
        add("Dockerfile", False, "container build")
    return s[:10]


def _rules_suggestion(files: Dict[str, str], mods: List[Dict]) -> str:
    r = []
    kinds = {m["kind"] for m in mods}
    if any(".eslintrc" in p or p.endswith("eslint.config.js") or p.endswith("eslint.config.mjs") for p in files):
        r.append("Follow the project's ESLint rules.")
    if any(p.endswith((".prettierrc", ".prettierrc.json", "prettier.config.js")) for p in files):
        r.append("Format with the project's Prettier settings.")
    if any(p.endswith("tsconfig.json") for p in files):
        r.append("Use TypeScript with explicit types; no `any`.")
    if kinds & {"ui5", "fe", "ovp"}:
        r.append("Every UI text goes into i18n (all languages present).")
    if "cap-srv" in kinds:
        r.append("Use async/await in CAP handlers and req.error() for validation errors.")
    if "react" in kinds:
        r.append("Function components and hooks only.")
    if "python" in kinds:
        r.append("Type hints on new functions; keep PEP 8.")
    r.append("Add or update a test for every behaviour you change.")
    return " ".join(r)


def deploy_targets(mods: List[Dict], ctx: Dict, source: Dict) -> List[Dict]:
    t = []
    if ctx.get("mta"):
        t.append({"value": "mta", "label": "MTA build + deploy to Cloud Foundry", "hint": "mbt build, then cf deploy (all modules)"})
    kinds = {m["kind"] for m in mods}
    if kinds & {"node-api", "python", "react", "angular", "vue", "static", "cap-srv"} and not ctx.get("mta"):
        t.append({"value": "cf-push", "label": "cf push to Cloud Foundry", "hint": "buildpack deploy; the studio writes manifest.yml if missing"})
    if kinds & {"ui5", "fe", "ovp"} and not ctx.get("mta"):
        t.append({"value": "cf-push", "label": "cf push (static UI5 app with approuter)", "hint": "the studio adds a minimal approuter"})
    if source.get("kind") == "git":
        t.append({"value": "git-pr", "label": "Pull request to the Git repository", "hint": "push a branch with the change and open a PR"})
    t.append({"value": "download", "label": "Download only", "hint": "you deploy with your own pipeline"})
    seen, out = set(), []
    for x in t:
        if x["value"] not in seen:
            seen.add(x["value"])
            out.append(x)
    return out


def questions(files: Dict[str, str], a: Dict, source: Dict) -> List[Dict]:
    mods = a["modules"]
    ui = [m for m in mods if m["role"] == "ui"]
    q = [{"id": "purpose", "title": "What does this app do?", "kind": "textarea",
          "why": "Context for every AI change, preview data and documentation.",
          "value": (a.get("understanding") or {}).get("summary") or _guess_purpose(files, mods),
          "guess": "Drafted from the code" if (a.get("understanding") or {}).get("summary") else "From README / manifest"}]
    if len(ui) > 1:
        q.append({"id": "entry", "title": "Which app do users open first?", "kind": "select",
                  "why": "Start page of the preview and of the deployed app.",
                  "options": [{"value": m["id"], "label": f"{m['path'] or '/'} — {m['framework']}"} for m in ui], "value": ui[0]["id"]})
    pv = []
    if any(m["kind"] == "cap-srv" for m in mods):
        csv = any(p.endswith(".csv") and "/data/" in p for p in files)
        pv.append({"value": "cap-sqlite", "label": "Run the CAP service on SQLite" + (" with the CSV seed data" if csv else ""),
                   "hint": "In the studio sandbox. Nothing leaves it."})
    if any(m["kind"] in ("fe", "ui5", "ovp") for m in mods) and not any(m["kind"] == "cap-srv" for m in mods):
        pv.append({"value": "mock", "label": "Mock server from the service metadata", "hint": "Generated sample data. Nothing leaves the studio."})
    if any(m["kind"] in ("react", "angular", "vue", "static") for m in mods):
        pv.append({"value": "web-build", "label": "Build the web app and serve it", "hint": "API calls go to the services started next to it."})
    if any(m["kind"] in ("node-api", "python") for m in mods):
        pv.append({"value": "run-services", "label": "Start the API/service with its run command", "hint": "Uses the run commands below."})
    if pv:
        q.append({"id": "preview_data", "title": "How should the preview run?", "kind": "radio",
                  "why": "Preview runs in an isolated sandbox.", "options": pv, "value": pv[0]["value"]})
    for d in [d for d in a.get("destinations") or [] if not d.get("internal")]:
        q.append({"id": "dest:" + d["name"], "title": f"Backend connection {d['name']}", "kind": "text",
                  "why": f"Used by {d['used_by']} ({d['purpose']}). Name of the BTP destination to use on deploy.",
                  "value": d["name"], "guess": "Same name as in the code"})
    if a.get("env_vars"):
        q.append({"id": "env", "title": "Where do these settings come from?", "kind": "textarea",
                  "why": "Variables the code reads. Say where each comes from (service binding, CF variable…). Never paste values.",
                  "value": "\n".join(f"{v} = " for v in a["env_vars"][:15])})
    cmds = []
    for m in mods:
        for k in ("install", "build", "test"):
            if m["commands"].get(k):
                cmds.append(m["commands"][k])
    q.append({"id": "commands", "title": "How is it built and tested?", "kind": "code",
              "why": "Every change is validated with these commands (one per line, run from the project root).",
              "value": "\n".join(dict.fromkeys(cmds)), "guess": "Detected from package.json / requirements"})
    run = [m["commands"]["run"] for m in mods if m["commands"].get("run") and m["kind"] in ("node-api", "python")]
    if run:
        q.append({"id": "run", "title": "How is the service started?", "kind": "code",
                  "why": "Used by the preview. $PORT is replaced with a free port.", "value": "\n".join(run)})
    q.append({"id": "protected", "title": "What must AI never change?", "kind": "checks",
              "why": "Changes to these paths are refused.", "options": _protect_suggestions(files, mods),
              "value": [x["value"] for x in _protect_suggestions(files, mods) if x["checked"]]})
    q.append({"id": "rules", "title": "Team rules for code changes", "kind": "textarea",
              "why": "Applied to every AI change, like a style guide for a new team member.",
              "value": _rules_suggestion(files, mods), "guess": "Suggested from the project's tooling"})
    targets = deploy_targets(mods, a.get("_ctx") or {}, source)
    q.append({"id": "deploy_target", "title": "Where does it deploy?", "kind": "select",
              "why": "Production still needs an approval by someone other than the author.",
              "options": targets, "value": targets[0]["value"]})
    return q


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
def analyze(files: Dict[str, str], source: Optional[Dict] = None, src_info: Optional[Dict] = None,
            say: Callable[[str], None] = lambda m: None) -> Dict:
    source = source or {"kind": "zip"}
    src_info = src_info or {}
    say("Reading project descriptors (mta.yaml, package.json, manifest.json, requirements)…")
    mods, ctx = detect_modules(files)
    say(f"Found {len(mods)} module(s): " + ", ".join(f"{m['name']} ({m['framework']})" for m in mods[:8]))

    # data model
    model = {"source": "", "editable": False, "entities": [], "services": []}
    if ctx["cap_roots"]:
        roots = [r + "db/" for r in ctx["cap_roots"]] + ctx["cap_roots"]
        cds = parse_cds(files, roots)
        names = set()
        ents = []
        for e in cds["entities"]:
            if e["name"] not in names:
                names.add(e["name"])
                ents.append(e)
        model = {"source": "CDS", "editable": True, "entities": ents, "services": cds["services"]}
        say(f"Parsed the CDS model: {len(ents)} entities, {len(cds['services'])} service(s).")
    else:
        for m in mods:
            for ds in (m.get("data_sources") or {}).values():
                loc = ds.get("local")
                for cand in ([m["path"] + "webapp/" + loc] if loc else []) + [m["path"] + "webapp/localService/metadata.xml",
                                                                             m["path"] + "webapp/localService/mainService/metadata.xml"]:
                    if cand in files:
                        model = {"source": f"OData metadata ({cand})", "editable": False, "entities": parse_edmx(files[cand]),
                                 "services": [{"name": ds["uri"], "path": ds["uri"]}]}
                        break
                if model["entities"]:
                    break
            if model["entities"]:
                break
        if not model["entities"]:
            src, ents = _models_code(files)
            if ents:
                model = {"source": src, "editable": False, "entities": ents[:80], "services": []}
        if model["entities"]:
            say(f"Data model from {model['source']}: {len(model['entities'])} entities.")

    screens = [dict(s, module=m["name"]) for m in mods for s in (m.get("screens") or [])]
    eps = [dict(e, module=m["name"]) for m in mods for e in (m.get("endpoints") or [])]
    dests = destinations_used(files)
    defined = destinations_defined(ctx.get("mta"))
    for d in dests:                      # wired inside the MTA (e.g. approuter -> own service)
        d["internal"] = d["name"] in defined
    envs = env_vars(files)
    stack = []
    for m in mods:
        stack.append(m["framework"])
    if ctx.get("mta"):
        stack.insert(0, "MTA")
    if any(p.endswith("tsconfig.json") for p in files) and not any("TypeScript" in s for s in stack):
        stack.append("TypeScript")
    if any(p.endswith("Dockerfile") for p in files):
        stack.append("Docker")
    if any(p.startswith(".github/workflows/") for p in files):
        stack.append("GitHub Actions")
    if any("xs-security.json" in p for p in files):
        stack.append("XSUAA")
    primary = mods[0]["kind"] if mods else "unknown"
    name = ((ctx.get("mta") or {}).get("ID") if isinstance(ctx.get("mta"), dict) else "") or \
        _json(files, "package.json").get("name") or (mods[0]["name"] if mods else "project")

    a = {"name": str(name), "primary": primary, "stack": list(dict.fromkeys(s for s in stack if s)),
         "modules": mods, "model": model, "screens": screens, "endpoints": eps, "destinations": dests,
         "env_vars": envs, "files": len(files), "bytes": src_info.get("bytes", 0),
         "skipped": src_info.get("skipped") or [], "source": source,
         "languages": _languages(files)}
    a["arch"] = architecture(mods, dests, model["source"], envs)
    a["findings"] = findings(files, mods, ctx, dests, src_info)
    a["_ctx"] = {"mta": bool(ctx.get("mta")), "cap_roots": ctx["cap_roots"]}
    return a


def _languages(files: Dict[str, str]) -> Dict[str, int]:
    ext = {".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
           ".jsx": "JavaScript", ".py": "Python", ".java": "Java", ".cds": "CDS", ".xml": "XML", ".html": "HTML",
           ".css": "CSS", ".scss": "CSS", ".vue": "Vue", ".json": "JSON", ".yaml": "YAML", ".yml": "YAML", ".sql": "SQL"}
    out: Dict[str, int] = {}
    for p, c in files.items():
        e = os.path.splitext(p)[1].lower()
        if e in ext and not adopt_files.is_binary(c) and not p.endswith("package-lock.json"):
            out[ext[e]] = out.get(ext[e], 0) + c.count("\n") + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1])[:8])


# ─────────────────────────────────────────────────────────────────────────────
# AI understanding
# ─────────────────────────────────────────────────────────────────────────────
_UNDERSTAND_SYS = """You are a senior SAP BTP and full-stack architect joining a client project.
Read the project overview and the key files and explain the business scenario in plain language.
Answer ONLY with JSON:
{"summary": "2-4 sentences: who uses the app and for what business process",
 "business_objects": ["main business objects, e.g. Sales Order"],
 "roles": ["user roles you can infer, e.g. Manager approves …"],
 "processes": ["key flows, e.g. Draft → Submitted → Approved"],
 "integrations": ["external systems / APIs"],
 "risks": ["notable technical debt or risks, short"],
 "suggested_missions": [{"title": "short", "request": "a concrete change request the client would likely want", "why": "short"}]}
No markdown, no code fences."""


def key_files(files: Dict[str, str], a: Dict, budget: int = 60_000) -> str:
    """The files that explain a project best, within a character budget."""
    prio = []
    for p in files:
        if adopt_files.is_binary(files[p]) or p.endswith(("package-lock.json", "yarn.lock", "pnpm-lock.yaml")):
            continue
        name = p.split("/")[-1]
        score = 0
        if name.lower().startswith("readme"):
            score = 100
        elif name in ("mta.yaml", "package.json", "manifest.json", "xs-security.json", "requirements.txt", "pyproject.toml"):
            score = 90 - p.count("/") * 5
        elif p.endswith(".cds"):
            score = 85
        elif name.endswith(("i18n.properties",)) and "_" not in name:
            score = 60
        elif re.search(r"(routes?|router|app|main|server|index|service|handler|controller)\.(js|ts|tsx|py)$", name):
            score = 55
        elif p.endswith((".view.xml",)):
            score = 45
        elif p.endswith((".ts", ".js", ".py", ".tsx", ".jsx", ".java")):
            score = 20
        if score:
            prio.append((score, p))
    out, used = [], 0
    for _, p in sorted(prio, key=lambda x: (-x[0], x[1])):
        chunk = files[p][:8000]
        if used + len(chunk) > budget:
            continue
        out.append(f"=== {p} ===\n{chunk}")
        used += len(chunk)
    return "\n\n".join(out)


def overview_text(a: Dict) -> str:
    lines = [f"Project {a['name']} — stack: {', '.join(a['stack'])}", "Modules:"]
    for m in a["modules"]:
        lines.append(f"- {m['name']} ({m['path'] or '/'}): {m['framework']} — {m['evidence']}")
    if a["model"]["entities"]:
        lines.append(f"Data model ({a['model']['source']}): " + ", ".join(
            f"{e['name']}({', '.join(f['name'] for f in e['fields'][:8])})" for e in a["model"]["entities"][:20]))
    if a["screens"]:
        lines.append("Screens: " + ", ".join(f"{s['name']} [{s['type']}]" for s in a["screens"][:25]))
    if a["endpoints"]:
        lines.append("Endpoints: " + ", ".join(f"{e['method']} {e['path']}" for e in a["endpoints"][:30]))
    if a["destinations"]:
        lines.append("Destinations: " + ", ".join(d["name"] for d in a["destinations"]))
    return "\n".join(lines)


def understand(files: Dict[str, str], a: Dict, ai_fn: Callable[[str, str], str]) -> Dict:
    raw = ai_fn(_UNDERSTAND_SYS, overview_text(a) + "\n\nKEY FILES\n" + key_files(files, a))
    m = re.search(r"\{.*\}", raw, re.S)
    data = json.loads(m.group(0)) if m else {}
    keep = ("summary", "business_objects", "roles", "processes", "integrations", "risks", "suggested_missions")
    return {k: data.get(k) for k in keep if data.get(k)}
