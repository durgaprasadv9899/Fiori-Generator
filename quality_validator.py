"""
quality_validator.py — static quality checks on a generated project (roadmap #8).

Runs BEFORE deployment over the in-memory files dict and reports problems it can
detect purely statically (no Node, no live service). Detection only — it does not
modify files. Each finding has a severity:

  "error"   — will break the app at runtime or deploy (must fix)
  "warning" — likely a problem or a smell (should review)
  "info"    — minor / advisory

Checks:
  1. JSON well-formedness   — manifest.json, xs-app.json, package.json, *.json
  2. manifest structure     — required sections, dataSources, routing targets
  3. XML well-formedness     — every *.view.xml / *.fragment.xml parses
  4. routing integrity       — every route target exists in targets; every
                               target viewName has a matching view file
  5. binding paths           — {Field} bindings in views resolve against the
                               entity's known fields (best-effort; skips
                               expression/i18n/named-model bindings)
  6. i18n completeness       — every {i18n>key} referenced in views exists in
                               i18n.properties
  7. controller references    — controllerName in each view has a matching
                               controller file

Returns a list of Finding dicts and a convenience `ok` flag (no errors).
"""

from __future__ import annotations
import json
import re
from dataclasses import dataclass, asdict
from typing import Dict, List
import xml.etree.ElementTree as ET


@dataclass
class Finding:
    severity: str      # error | warning | info
    check: str         # short check id
    file: str          # file the finding relates to ("" if project-wide)
    message: str

    def as_dict(self):
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# individual checks
# ─────────────────────────────────────────────────────────────────────────────
def _check_json(files: Dict[str, str]) -> List[Finding]:
    out = []
    for name, content in files.items():
        if not name.endswith(".json"):
            continue
        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            out.append(Finding("error", "json-parse", name,
                               f"Invalid JSON: {e}"))
    return out


def _check_xml(files: Dict[str, str]) -> List[Finding]:
    out = []
    for name, content in files.items():
        if not (name.endswith(".xml")):
            continue
        try:
            ET.fromstring(content.encode("utf-8"))
        except ET.ParseError as e:
            out.append(Finding("error", "xml-parse", name,
                               f"Malformed XML: {e}"))
    return out


def _check_manifest(files: Dict[str, str]) -> List[Finding]:
    out = []
    raw = files.get("manifest.json")
    if not raw:
        out.append(Finding("error", "manifest-missing", "manifest.json",
                           "manifest.json is not present."))
        return out
    try:
        m = json.loads(raw)
    except json.JSONDecodeError:
        return out  # already reported by _check_json
    for section in ("sap.app", "sap.ui5"):
        if section not in m:
            out.append(Finding("error", "manifest-section", "manifest.json",
                               f"Missing required section '{section}'."))
    app = m.get("sap.app", {})
    if "dataSources" not in app:
        out.append(Finding("warning", "manifest-datasource", "manifest.json",
                           "sap.app.dataSources missing — no OData source declared."))
    ui5 = m.get("sap.ui5", {})
    # V2 Fiori Elements (sap.ui.generic.app) and OVP (sap.ovp) route internally
    if "routing" not in ui5 and "sap.ui.generic.app" not in m and "sap.ovp" not in m:
        out.append(Finding("warning", "manifest-routing", "manifest.json",
                           "sap.ui5.routing missing — navigation may not work."))
    if "models" not in ui5:
        out.append(Finding("warning", "manifest-models", "manifest.json",
                           "sap.ui5.models missing — no model wired (incl. i18n)."))
    # Unresolved service placeholder — the app will 404 on every data request.
    ds = app.get("dataSources", {})
    for dsname, dsdef in ds.items():
        uri = (dsdef or {}).get("uri", "")
        if "YOUR_SERVICE" in uri or "<your" in uri.lower():
            out.append(Finding("error", "manifest-service-placeholder", "manifest.json",
                               f"dataSource '{dsname}' still has the placeholder URI "
                               f"'{uri}'. Set a real OData service URL before deploying "
                               f"— every data request will 404 otherwise."))
    return out


def _check_routing(files: Dict[str, str]) -> List[Finding]:
    out = []
    raw = files.get("manifest.json")
    if not raw:
        return out
    try:
        m = json.loads(raw)
    except json.JSONDecodeError:
        return out
    routing = m.get("sap.ui5", {}).get("routing", {})
    routes = routing.get("routes", [])
    targets = routing.get("targets", {})

    # every route target must be defined
    for r in routes:
        tlist = r.get("target", [])
        if isinstance(tlist, str):
            tlist = [tlist]
        for t in tlist:
            if t not in targets:
                out.append(Finding("error", "routing-target", "manifest.json",
                                   f"Route '{r.get('name')}' references undefined "
                                   f"target '{t}'."))
    # every target viewName should have a matching view file
    view_files = {n for n in files if n.startswith("view/") and n.endswith(".view.xml")}
    for tname, t in targets.items():
        # newer syntax: {"type": "View", "name": "List"}; FE targets are Components
        vn = t.get("viewName") or (t.get("name") if t.get("type", "View") == "View"
                                   and "." not in str(t.get("name", "")) else None)
        if vn:
            expected = "view/" + vn + ".view.xml"
            if expected not in view_files:
                out.append(Finding("error", "routing-view", "manifest.json",
                                   f"Target '{tname}' viewName '{vn}' has no "
                                   f"matching {expected}."))
    return out


_BINDING_RE = re.compile(r"\{([^{}>]+)\}")


def _known_fields(files: Dict[str, str]) -> set:
    """Collect plausible field names from i18n column_ keys + manifest — used as
    a soft allowlist for binding-path validation."""
    fields = set()
    i18n = files.get("i18n/i18n.properties", "")
    for line in i18n.splitlines():
        if line.startswith("column_") and "=" in line:
            # value is the real field name
            fields.add(line.split("=", 1)[1].strip())
    return fields


def _check_bindings(files: Dict[str, str]) -> List[Finding]:
    """Best-effort: flag simple property bindings in views that don't look like
    a known field. Skips i18n bindings, named-model bindings, expression
    bindings, and absolute/navigation paths (those need model metadata to judge,
    so we don't guess)."""
    out = []
    known = _known_fields(files)
    if not known:
        return out  # nothing to compare against; skip rather than false-alarm
    for name, content in files.items():
        if not name.endswith(".view.xml"):
            continue
        for m in _BINDING_RE.finditer(content):
            expr = m.group(1).strip()
            # skip non-simple bindings
            if (">" in expr or "/" in expr or ":" in expr or "'" in expr
                    or "(" in expr or expr.startswith("=") or " " in expr):
                continue
            if expr and expr not in known:
                out.append(Finding("info", "binding-path", name,
                                   f"Binding {{{expr}}} not among known fields "
                                   f"(may be a navigation or computed value)."))
    return out


_I18N_RE = re.compile(r"\{i18n>([A-Za-z0-9_.]+)\}")


def _check_i18n(files: Dict[str, str]) -> List[Finding]:
    out = []
    i18n = files.get("i18n/i18n.properties")
    if i18n is None:
        # only a problem if something references i18n
        if any("{i18n>" in c for n, c in files.items() if n.endswith(".xml")):
            out.append(Finding("error", "i18n-missing", "i18n/i18n.properties",
                               "Views reference i18n but i18n.properties is missing."))
        return out
    keys = set()
    for line in i18n.splitlines():
        if "=" in line and not line.strip().startswith("#"):
            keys.add(line.split("=", 1)[0].strip())
    for name, content in files.items():
        if not name.endswith(".xml"):
            continue
        for m in _I18N_RE.finditer(content):
            k = m.group(1)
            if k not in keys:
                out.append(Finding("warning", "i18n-key", name,
                                   f"Missing i18n key '{k}' referenced in view."))
    return out


_HANDLER_RE = re.compile(r'\b(?:press|search|clear|change|submit|liveChange|selectionChange|'
                         r'updateFinished|valueHelpRequest|confirm|cancel|navButtonPress|'
                         r'delete|itemPress|beforeOpen|afterClose)="\.([A-Za-z_]\w*)"')
_FORMATTER_RE = re.compile(r"formatter:\s*'\.formatter\.([A-Za-z_]\w*)'")
_METHOD_RE = re.compile(r"\b([A-Za-z_]\w*)\s*:\s*function\b")


def _check_handlers(files: Dict[str, str]) -> List[Finding]:
    """Every '.onX' event handler / '.formatter.x' used in a view or fragment
    must be defined in a controller (or BaseController) / model/formatter.js."""
    out = []
    controllers = {n: c for n, c in files.items() if n.startswith("controller/") and n.endswith(".js")}
    if not controllers:
        return out
    methods = set()
    for c in controllers.values():
        methods |= set(_METHOD_RE.findall(c))
    fmt = set(_METHOD_RE.findall(files.get("model/formatter.js", "")))
    for name, content in files.items():
        if not (name.endswith(".view.xml") or name.endswith(".fragment.xml")):
            continue
        for h in sorted(set(_HANDLER_RE.findall(content))):
            if h not in methods:
                out.append(Finding("error", "handler-missing", name,
                                   f"Event handler '.{h}' is not defined in any controller."))
        for f in sorted(set(_FORMATTER_RE.findall(content))):
            if f not in fmt:
                out.append(Finding("error", "formatter-missing", name,
                                   f"Formatter '.formatter.{f}' is not defined in model/formatter.js."))
    return out


def _i18n_keys(files: Dict[str, str]) -> set:
    keys = set()
    for line in files.get("i18n/i18n.properties", "").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            keys.add(line.split("=", 1)[0].strip())
    return keys


def _check_text_refs(files: Dict[str, str]) -> List[Finding]:
    """{{key}} in manifest.json and {@i18n>key} / {i18n>key} anywhere must exist."""
    out = []
    if "i18n/i18n.properties" not in files:
        return out
    keys = _i18n_keys(files)
    for k in sorted(set(re.findall(r"\{\{([A-Za-z0-9_.]+)\}\}", files.get("manifest.json", "")))):
        if k not in keys:
            out.append(Finding("warning", "i18n-key", "manifest.json",
                               f"Missing i18n key '{k}' referenced as {{{{{k}}}}}."))
    for name, content in files.items():
        if not (name.endswith(".xml") or name == "manifest.json"):
            continue
        for k in sorted(set(re.findall(r"\{@i18n>([A-Za-z0-9_.]+)\}", content))):
            if k not in keys:
                out.append(Finding("warning", "i18n-key", name, f"Missing i18n key '{k}'."))
    return out


def _check_controller_extensions(files: Dict[str, str]) -> List[Finding]:
    """Controller extensions / custom-action modules referenced in the manifest
    must exist under the UI5 naming convention (<path>.controller.js)."""
    out = []
    raw = files.get("manifest.json", "")
    try:
        m = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return out
    app_id = m.get("sap.app", {}).get("id", "")
    ext = (m.get("sap.ui5", {}).get("extends", {}).get("extensions", {})
           .get("sap.ui.controllerExtensions", {}))
    for base, cfg in ext.items():
        name = (cfg or {}).get("controllerName", "")
        if name.startswith(app_id + "."):
            rel = name[len(app_id) + 1:].replace(".", "/") + ".controller.js"
            if rel not in files:
                out.append(Finding("error", "extension-missing", "manifest.json",
                                   f"Controller extension '{name}' for {base} needs {rel}, "
                                   f"which is not in the project (UI5 would 404 at runtime)."))
    for press in re.findall(r'"press":\s*"([A-Za-z0-9_.]+)\.[A-Za-z0-9_]+"', raw):
        if press.startswith(app_id + "."):
            rel = press[len(app_id) + 1:].replace(".", "/") + ".js"
            if rel not in files:
                out.append(Finding("error", "extension-missing", "manifest.json",
                                   f"Custom action module '{press}' needs {rel}."))
    return out


def _check_controllers(files: Dict[str, str]) -> List[Finding]:
    out = []
    ctrl_re = re.compile(r'controllerName="([^"]+)"')
    for name, content in files.items():
        if not name.endswith(".view.xml"):
            continue
        m = ctrl_re.search(content)
        if not m:
            continue
        cname = m.group(1)
        # last two segments -> controller/<Name>.controller.js
        short = cname.split(".controller.")[-1] if ".controller." in cname else cname.split(".")[-1]
        expected = "controller/" + short + ".controller.js"
        if expected not in files:
            out.append(Finding("error", "controller-missing", name,
                               f"View references controller '{cname}' but "
                               f"{expected} is not present."))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# public API
# ─────────────────────────────────────────────────────────────────────────────
def validate_project(files: Dict[str, str]) -> Dict:
    """Run all static checks. Returns:
       {"ok": bool, "errors": int, "warnings": int, "infos": int,
        "findings": [Finding dict, ...]}"""
    findings: List[Finding] = []
    findings += _check_json(files)
    findings += _check_xml(files)
    findings += _check_manifest(files)
    findings += _check_routing(files)
    findings += _check_bindings(files)
    findings += _check_i18n(files)
    findings += _check_controllers(files)
    findings += _check_handlers(files)
    findings += _check_controller_extensions(files)
    findings += _check_text_refs(files)

    errors   = sum(1 for f in findings if f.severity == "error")
    warnings = sum(1 for f in findings if f.severity == "warning")
    infos    = sum(1 for f in findings if f.severity == "info")
    return {
        "ok": errors == 0,
        "errors": errors,
        "warnings": warnings,
        "infos": infos,
        "findings": [f.as_dict() for f in findings],
    }


def format_report(result: Dict) -> str:
    """Human-readable one-liner summary + bulleted findings (for logs/UI)."""
    head = (f"Quality check: {result['errors']} error(s), "
            f"{result['warnings']} warning(s), {result['infos']} info.")
    icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️", "fixed": "🔧"}
    lines = [head]
    for f in result["findings"]:
        loc = f" [{f['file']}]" if f["file"] else ""
        lines.append(f"{icon.get(f['severity'],'-')} {f['message']}{loc}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# auto-fix layer (roadmap #8: "fix common issues automatically")
# Only unambiguous, behaviour-preserving fixes are applied. Everything else is
# left for the developer and reported as-is.
# ─────────────────────────────────────────────────────────────────────────────
def _humanize_i18n_key(k: str) -> str:
    s = k.split(".")[-1]
    s = re.sub(r"^(column|label|btn|text|title)_", "", s)
    s = s.replace("_", " ").strip()
    return (s[:1].upper() + s[1:]) if s else k


def _fix_trailing_comma_json(files: Dict[str, str]) -> Dict[str, str]:
    """Repair JSON files that only fail due to a trailing comma."""
    fixes = {}
    for name, content in files.items():
        if not name.endswith(".json"):
            continue
        try:
            json.loads(content)
            continue  # already valid
        except json.JSONDecodeError:
            repaired = re.sub(r",(\s*[}\]])", r"\1", content)
            try:
                parsed = json.loads(repaired)
                fixes[name] = json.dumps(parsed, indent=2)
            except json.JSONDecodeError:
                pass  # not a simple trailing-comma issue; leave for the report
    return fixes


def _fix_missing_i18n(files: Dict[str, str]) -> Dict[str, str]:
    """Append humanized defaults for i18n keys referenced by views but missing
    from i18n.properties."""
    referenced = set()
    for name, content in files.items():
        if name.endswith(".xml"):
            referenced.update(_I18N_RE.findall(content))
    if not referenced:
        return {}
    path = "i18n/i18n.properties"
    existing_text = files.get(path, "")
    existing = set()
    for line in existing_text.splitlines():
        if "=" in line and not line.strip().startswith("#"):
            existing.add(line.split("=", 1)[0].strip())
    missing = sorted(referenced - existing)
    if not missing:
        return {}
    add = "".join(f"{k}={_humanize_i18n_key(k)}\n" for k in missing)
    new_text = (existing_text.rstrip("\n") + "\n" if existing_text else "") + add
    return {path: new_text}


def validate_and_autofix(files: Dict[str, str]) -> Dict:
    """Run detection, apply safe auto-fixes, then re-validate.

    Returns:
      {"report_before": <validate_project result before fixes>,
       "report_after":  <result after fixes applied>,
       "fixed_files":   {path: new_content, ...},   # merge into your project
       "files":         {full project dict with fixes merged},
       "fixes_applied": [human-readable strings]}
    """
    before = validate_project(files)

    fixes: Dict[str, str] = {}
    applied: List[str] = []

    json_fixes = _fix_trailing_comma_json(files)
    for name in json_fixes:
        applied.append(f"Repaired invalid JSON (trailing comma) in {name}.")
    fixes.update(json_fixes)

    # apply json fixes before i18n so i18n sees any repaired files
    interim = dict(files); interim.update(fixes)

    i18n_fixes = _fix_missing_i18n(interim)
    for name in i18n_fixes:
        # count how many keys were added for the message
        added = i18n_fixes[name].count("\n") - interim.get(name, "").count("\n")
        applied.append(f"Added {max(added,0)} missing i18n key(s) to {name}.")
    fixes.update(i18n_fixes)

    merged = dict(files); merged.update(fixes)
    after = validate_project(merged)

    # tag the fixes into the after-report findings for visibility
    for msg in applied:
        after["findings"].insert(0, Finding("fixed", "auto-fix", "", msg).as_dict())

    return {
        "report_before": before,
        "report_after": after,
        "fixed_files": fixes,
        "files": merged,
        "fixes_applied": applied,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Model-aware checks (needs the parsed service model)
# ─────────────────────────────────────────────────────────────────────────────
def validate_against_model(files: Dict[str, str], model: Dict) -> Dict:
    """Annotation paths / manifest + config entity sets must exist in the service."""
    findings: List[Finding] = []
    types = {es["entity_type"]: es for es in model.get("entity_sets", [])}
    sets = {es["name"] for es in model.get("entity_sets", [])}
    container = model.get("container", "")

    ann = files.get("annotations/annotations.xml")
    root = None
    if ann:
        try:
            root = ET.fromstring(ann.encode("utf-8"))
        except ET.ParseError:
            root = None
    if root is not None:
        for block in root.iter():
            if not block.tag.endswith("}Annotations"):
                continue
            target = block.attrib.get("Target", "")
            head = target.split("/")[0]
            if container and head.endswith("." + container):
                es_name = target.split("/", 1)[1] if "/" in target else ""
                if es_name and es_name not in sets:
                    findings.append(Finding("error", "annotation-target", "annotations/annotations.xml",
                                            f"Entity set '{es_name}' in target '{target}' does not exist."))
                continue
            if "(" in head:                   # bound action overload: ns.action(ns.Type)
                op_name = head.split("(")[0].split(".")[-1]
                if op_name not in {o["name"] for o in model.get("operations", [])}:
                    findings.append(Finding("error", "annotation-target", "annotations/annotations.xml",
                                            f"Operation '{op_name}' in target '{target}' does not exist."))
                continue
            tname = head.split(".")[-1]
            es = types.get(tname)
            if not es:
                findings.append(Finding("error", "annotation-target", "annotations/annotations.xml",
                                        f"Annotation target '{target}' is not an entity type of the service."))
                continue
            props = {p["name"] for p in es["properties"]}
            names = props | {n["name"] for n in es.get("navigations", [])}
            if "/" in target and target.split("/", 1)[1] not in props:
                findings.append(Finding("error", "annotation-path", "annotations/annotations.xml",
                                        f"'{target.split('/', 1)[1]}' is not a property of {tname}."))
            for el in block.iter():
                paths = [el.attrib[a] for a in ("Path", "PropertyPath") if el.attrib.get(a)]
                tag = el.tag.split("}")[-1]
                if tag in ("PropertyPath", "Path") and el.text:
                    paths.append(el.text.strip())
                ap = el.attrib.get("AnnotationPath", "")
                if "/@" in ap:
                    paths.append(ap.split("/@")[0])
                for pth in paths:
                    first = pth.split("/")[0]
                    if first and first not in names:
                        findings.append(Finding("error", "annotation-path", "annotations/annotations.xml",
                                                f"Path '{pth}' does not exist on {tname}."))

    raw = files.get("manifest.json", "")
    for s in sorted(set(re.findall(r'"(?:entitySet|contextPath)"\s*:\s*"/?([A-Za-z0-9_]+)', raw))):
        if s not in sets:
            findings.append(Finding("error", "manifest-entityset", "manifest.json",
                                    f"Entity set '{s}' is not in the service."))
    cfg = files.get("model/appConfig.js", "")
    for s in sorted(set(re.findall(r'"entitySet":\s*"([A-Za-z0-9_]+)"', cfg))):
        if s not in sets:
            findings.append(Finding("error", "config-entityset", "model/appConfig.js",
                                    f"Entity set '{s}' is not in the service."))
    errors = sum(f.severity == "error" for f in findings)
    return {"ok": errors == 0, "errors": errors,
            "warnings": sum(f.severity == "warning" for f in findings),
            "infos": sum(f.severity == "info" for f in findings),
            "findings": [f.as_dict() for f in findings]}


def merge_reports(a: Dict, b: Dict) -> Dict:
    errors = a["errors"] + b["errors"]
    return {"ok": errors == 0, "errors": errors, "warnings": a["warnings"] + b["warnings"],
            "infos": a["infos"] + b["infos"], "findings": a["findings"] + b["findings"]}
