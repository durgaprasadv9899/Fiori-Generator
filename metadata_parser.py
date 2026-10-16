"""
metadata_parser.py — Module 2: Metadata Parser.

Converts raw EDMX ($metadata) XML into a clean, version-agnostic JSON business
model that every downstream module (analyzer, screen planner, generator) can
consume without touching XML again.

Handles both OData V2 and V4 EDMX. The two differ in namespaces and in how
EntitySets are declared (V2: inside <EntityContainer><EntitySet EntityType=.../>
with association sets; V4: same container idea but different edm namespace and
navigation via <NavigationProperty>). We normalise both into:

{
  "odata_version": "2.0" | "4.0",
  "namespace": "ZSALES_ORDER_SRV",
  "entity_sets": [
     {
       "name": "SalesOrderSet",
       "entity_type": "SalesOrder",
       "keys": ["Vbeln"],
       "properties": [
          {"name":"Vbeln","type":"Edm.String","nullable":false,"label":"Sales Order",
           "max_length":10, "ui_type":"text"},
          ...
       ],
       "navigations": [
          {"name":"ToItems","target_set":"SalesOrderItemSet","to_many":true}
       ],
       "label": "Sales Order",
       "creatable": true, "updatable": true, "deletable": true
     }, ...
  ]
}

`ui_type` is a coarse control hint derived from the EDM type, used by the screen
planner / generator to pick a sensible cell/field control:
  string->text, number->number, decimal->amount, date->date,
  datetime->datetime, boolean->boolean.
"""

from __future__ import annotations
import re
from typing import Dict, List
import xml.etree.ElementTree as ET


class MetadataParseError(Exception):
    pass


# Coarse EDM -> UI control hint
def _ui_type(edm_type: str) -> str:
    t = (edm_type or "").replace("Edm.", "").lower()
    if t in ("string", "guid"):
        return "text"
    if t in ("boolean",):
        return "boolean"
    if t in ("date", "datetime", "datetimeoffset", "time"):
        return "datetime" if "time" in t else "date"
    if t in ("decimal", "double", "single"):
        return "amount"
    if t in ("int16", "int32", "int64", "byte", "sbyte"):
        return "number"
    return "text"


def _localname(tag: str) -> str:
    """Strip the XML namespace from a tag: '{ns}EntityType' -> 'EntityType'."""
    return tag.split("}")[-1] if "}" in tag else tag


def _humanize(name: str) -> str:
    """Turn 'NetAmount'/'NET_AMOUNT'/'sales_order' into 'Net Amount'."""
    if not name:
        return name
    s = name.replace("_", " ")
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s)  # camelCase split
    s = re.sub(r"\s+", " ", s).strip()
    return s[:1].upper() + s[1:]


def detect_version(edmx_text: str) -> str:
    """Best-effort OData version from the EDMX document.

    The most reliable signal is the edmx WRAPPER namespace, which differs
    unambiguously between versions and is always present:
      * V4:      xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx"
      * V2/V3:   xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx"
    We check that first. Only if the wrapper is missing/unrecognised do we fall
    back to softer signals (edm schema namespace, Version attribute).

    NOTE: the edmx wrapper's Version attribute (often "1.0" for V2 services or
    "4.0" for V4) refers to the EDMX schema, and must NOT be confused with the
    OData protocol version — hence we anchor on the namespace, not that number.
    """
    text = edmx_text

    # 1) Authoritative: the namespace declared on the ROOT <Edmx> element.
    #    A plain substring search is not enough: SAP V2 services embed
    #    vocabulary <edmx:Reference xmlns:edmx="...oasis.../edmx"> elements,
    #    which would otherwise make every such V2 service look like V4.
    root = re.search(r"<(?:([\w.-]+):)?Edmx\b[^>]*>", text)
    if root:
        attr = "xmlns:" + root.group(1) if root.group(1) else "xmlns"
        ns = re.search(re.escape(attr) + r"\s*=\s*[\"']([^\"']+)", root.group(0))
        if ns and "docs.oasis-open.org/odata/ns/edmx" in ns.group(1):
            return "4.0"
        if ns and "schemas.microsoft.com/ado/2007/06/edmx" in ns.group(1):
            return "2.0"

    # 1b) Wrapper namespace anywhere (root tag unusual / not found).
    if "docs.oasis-open.org/odata/ns/edmx" in text:
        return "4.0"
    if "schemas.microsoft.com/ado/2007/06/edmx" in text:
        return "2.0"

    # 2) Fallback: the edm (schema) namespace.
    if "docs.oasis-open.org/odata/ns/edm" in text:
        return "4.0"
    if ("schemas.microsoft.com/ado/2008/09/edm" in text or
            "schemas.microsoft.com/ado/2009/11/edm" in text or
            "schemas.microsoft.com/ado/2006/04/edm" in text):
        return "2.0"

    # 3) Last resort: an explicit DataServiceVersion / Version hint.
    head = text[:2000].lower()
    if 'dataserviceversion="2.0"' in head or 'm:dataserviceversion="2.0"' in head:
        return "2.0"
    if 'version="4.0"' in head:
        return "4.0"
    # SAP gateway services are predominantly V2; default there rather than V4,
    # since mis-selecting V4 breaks metadata parsing entirely (the V4 converter
    # rejects a V2 document), whereas the V2 model reads V2 metadata correctly.
    return "2.0"


def _find_all(root, localname: str):
    """Find all descendants whose local tag == localname, namespace-agnostic."""
    return [el for el in root.iter() if _localname(el.tag) == localname]


def _sap_label(el, fallback_name: str) -> str:
    """SAP annotates human labels via sap:label (V2) or a Common.Label
    annotation (V4). Fall back to a humanised property name."""
    for k, v in el.attrib.items():
        if _localname(k) == "label" and v:
            return v
    return _humanize(fallback_name)


def _sap_attr(el, attr: str):
    """Value of a namespaced attribute like sap:text, matched by local name."""
    for k, v in el.attrib.items():
        if _localname(k) == attr:
            return v
    return None


def _sap_bool(el, attr: str, default: bool) -> bool:
    """Read sap:creatable / sap:updatable / sap:deletable style flags (V2)."""
    for k, v in el.attrib.items():
        if _localname(k) == attr:
            return v.strip().lower() != "false"
    return default


def parse_metadata(edmx_text: str) -> Dict:
    """Parse EDMX text into the normalised business-model dict."""
    if not edmx_text or "edmx" not in edmx_text.lower():
        raise MetadataParseError("Input is not EDMX metadata.")

    version = detect_version(edmx_text)
    try:
        root = ET.fromstring(edmx_text.encode("utf-8"))
    except ET.ParseError as e:
        raise MetadataParseError(f"Invalid XML in $metadata: {e}")

    schemas = _find_all(root, "Schema")
    if not schemas:
        raise MetadataParseError("No <Schema> element found in metadata.")

    # Namespace (first schema with a Namespace attr)
    namespace = ""
    for sch in schemas:
        ns = sch.attrib.get("Namespace")
        if ns:
            namespace = ns
            break

    # ── Build EntityType -> {keys, properties, navigations} lookup ───────────
    entity_types: Dict[str, Dict] = {}
    # V4 enum types render as their member names — usable like strings.
    enum_types = {el.attrib.get("Name") for el in _find_all(root, "EnumType")}
    for et_el in _find_all(root, "EntityType"):
        et_name = et_el.attrib.get("Name")
        if not et_name:
            continue
        keys: List[str] = []
        for key_el in _find_all(et_el, "PropertyRef"):
            if key_el.attrib.get("Name"):
                keys.append(key_el.attrib["Name"])

        props = []
        complex_props = []
        for p in et_el:
            if _localname(p.tag) != "Property":
                continue
            pname = p.attrib.get("Name")
            if not pname:
                continue
            ptype = p.attrib.get("Type", "Edm.String")
            # Collections and complex types can't be bound to a cell/field as
            # a value; keep them aside so no UI binds them like a primitive.
            if ptype.startswith("Collection(") or (
                    not ptype.startswith("Edm.") and ptype.split(".")[-1] not in enum_types):
                complex_props.append({"name": pname, "type": ptype})
                continue
            is_enum = not ptype.startswith("Edm.")
            nullable = p.attrib.get("Nullable", "true").strip().lower() != "false"
            max_len = p.attrib.get("MaxLength")
            prec, scale = p.attrib.get("Precision"), p.attrib.get("Scale")
            display_format = _sap_attr(p, "display-format")
            ui_type = _ui_type(ptype)
            # V2 Edm.DateTime with sap:display-format="Date" is a pure date.
            if ui_type == "datetime" and (display_format or "").lower() == "date":
                ui_type = "date"
            props.append({
                "name": pname,
                "type": ptype,
                "nullable": nullable,
                "label": _sap_label(p, pname),
                "max_length": int(max_len) if (max_len and max_len.isdigit()) else None,
                "precision": int(prec) if (prec and prec.isdigit()) else None,
                "scale": int(scale) if (scale and scale.isdigit()) else None,
                "ui_type": ui_type,
                "enum": is_enum,
                # SAP V2 property semantics (absent => default)
                "text_property": _sap_attr(p, "text") or "",
                "unit_property": _sap_attr(p, "unit") or "",
                "semantics": _sap_attr(p, "semantics") or "",
                "display_format": display_format or "",
                "creatable": _sap_bool(p, "creatable", True),
                "updatable": _sap_bool(p, "updatable", True),
                "filterable": _sap_bool(p, "filterable", True),
                "sortable": _sap_bool(p, "sortable", True),
                "visible": _sap_bool(p, "visible", True),
                "required_in_filter": _sap_bool(p, "required-in-filter", False),
            })

        navs = []
        for nav in et_el:
            if _localname(nav.tag) != "NavigationProperty":
                continue
            navs.append({
                "name": nav.attrib.get("Name", ""),
                # V4 exposes Type directly (Collection(ns.Type) => to-many)
                "type_raw": nav.attrib.get("Type", ""),
                "relationship": nav.attrib.get("Relationship", ""),  # V2
                "to_role": nav.attrib.get("ToRole", ""),             # V2
            })

        entity_types[et_name] = {
            "keys": keys,
            "properties": props,
            "complex_properties": complex_props,
            "navigations": navs,
            "label": _sap_label(et_el, et_name),
            "semantics": _sap_attr(et_el, "semantics") or "",
            "has_stream": (et_el.attrib.get("HasStream") or
                           _sap_attr(et_el, "HasStream") or "").lower() == "true",
        }

    # ── V2 associations: map (Relationship, ToRole) -> (target_type, to_many) ─
    # In OData V2 a NavigationProperty carries only Relationship + ToRole; the
    # actual target entity type and cardinality live in an <Association> with
    # two <End> elements (Type + Multiplicity). Build a lookup so V2 navigations
    # resolve their target type (V4 carries Type inline and skips this).
    assoc_ends: Dict[str, Dict[str, Dict]] = {}
    for assoc in _find_all(root, "Association"):
        aname = assoc.attrib.get("Name")
        if not aname:
            continue
        ends = {}
        for end in assoc:
            if _localname(end.tag) != "End":
                continue
            role = end.attrib.get("Role", "")
            etype = end.attrib.get("Type", "").split(".")[-1]
            mult = end.attrib.get("Multiplicity", "")
            ends[role] = {"type": etype, "to_many": mult == "*"}
        assoc_ends[aname] = ends

    def _resolve_v2_nav(relationship: str, to_role: str):
        """Return (target_type, to_many) for a V2 navigation, or ('', False)."""
        assoc = (relationship or "").split(".")[-1]
        ends = assoc_ends.get(assoc, {})
        end = ends.get(to_role)
        if end:
            return end["type"], end["to_many"]
        return "", False

    # ── EntitySets (inside EntityContainer) ──────────────────────────────────
    entity_sets = []
    for es_el in _find_all(root, "EntitySet"):
        es_name = es_el.attrib.get("Name")
        et_ref = es_el.attrib.get("EntityType", "")
        if not es_name or not et_ref:
            continue
        et_local = et_ref.split(".")[-1]
        et = entity_types.get(et_local)
        if not et:
            # EntitySet referencing a type we didn't parse — skip gracefully.
            continue

        # Resolve navigation to-many vs to-one + target type (V4 inline Type,
        # V2 via the association lookup).
        navigations = []
        for nav in et["navigations"]:
            if nav["type_raw"]:                      # V4
                to_many = nav["type_raw"].startswith("Collection(")
                target_type = (nav["type_raw"]
                               .replace("Collection(", "").rstrip(")")
                               .split(".")[-1])
            else:                                    # V2
                target_type, to_many = _resolve_v2_nav(
                    nav.get("relationship", ""), nav.get("to_role", ""))
            navigations.append({
                "name": nav["name"],
                "to_many": to_many,
                "target_type": target_type,
            })

        entity_sets.append({
            "name": es_name,
            "entity_type": et_local,
            "keys": et["keys"],
            "properties": et["properties"],
            "complex_properties": et.get("complex_properties", []),
            "navigations": navigations,
            "label": _sap_bool  # placeholder replaced below
        })
        # label + CRUD flags (sap: annotations live on the EntitySet in V2)
        entity_sets[-1]["label"] = _sap_label(es_el, et["label"] or es_name)
        entity_sets[-1]["creatable"] = _sap_bool(es_el, "creatable", True)
        entity_sets[-1]["updatable"] = _sap_bool(es_el, "updatable", True)
        entity_sets[-1]["deletable"] = _sap_bool(es_el, "deletable", True)
        entity_sets[-1]["searchable"] = _sap_bool(es_el, "searchable", False)
        entity_sets[-1]["semantics"] = et.get("semantics", "")
        entity_sets[-1]["has_stream"] = et.get("has_stream", False)
        # V2 analytical services mark aggregatable types sap:semantics="aggregate";
        # V4 needs Aggregation.ApplySupported (set by annotation_reader).
        entity_sets[-1]["aggregatable"] = et.get("semantics", "") == "aggregate"

    if not entity_sets:
        raise MetadataParseError(
            "No EntitySets found. The metadata parsed but exposed no entity sets "
            "— check that this is a data service URL, not a catalog/root URL."
        )

    return {
        "odata_version": version,
        "namespace": namespace,
        "entity_sets": entity_sets,
        "operations": _parse_operations(root, version, namespace),
        "container": next((c.attrib.get("Name", "") for c in _find_all(root, "EntityContainer")), ""),
        # namespace of the schema that holds the container (may differ from
        # the entity types' schema) — needed for qualified function imports
        "container_namespace": next((sch.attrib.get("Namespace", "") for sch in schemas
                                     if any(_localname(el.tag) == "EntityContainer" for el in sch)), namespace),
    }


def _params(el) -> List[Dict]:
    out = []
    for prm in el:
        if _localname(prm.tag) != "Parameter" or not prm.attrib.get("Name"):
            continue
        out.append({
            "name": prm.attrib["Name"],
            "type": prm.attrib.get("Type", "Edm.String"),
            "nullable": prm.attrib.get("Nullable", "true").lower() != "false",
            "label": _sap_label(prm, prm.attrib["Name"]),
            "ui_type": _ui_type(prm.attrib.get("Type", "")),
        })
    return out


def _evoke_action_facts(el) -> Dict:
    """Studio-generated (standalone CAP) services describe their workflow on the action:
    <Annotation Term="Evoke.StateGuard"> {Field, Values[]} = the states the action is valid
    in, plus Common.Label. Read into op["state_guard"] / op["label"]."""
    out: Dict = {}
    for ann in el:
        if _localname(ann.tag) != "Annotation":
            continue
        term = ann.attrib.get("Term", "")
        if term.endswith("Label") and ann.attrib.get("String"):
            out["label"] = ann.attrib["String"]
        elif term.endswith("StateGuard"):
            field, values = "", []
            for pv in ann.iter():
                if _localname(pv.tag) != "PropertyValue":
                    continue
                if pv.attrib.get("Property") == "Field":
                    field = pv.attrib.get("String", "")
                elif pv.attrib.get("Property") == "Values":
                    values = [v.text or "" for v in pv.iter() if _localname(v.tag) == "String"]
            if field and values:
                out["state_guard"] = {"field": field, "values": values}
    return out


def _parse_operations(root, version: str, namespace: str) -> List[Dict]:
    """Actions / functions the service exposes.

    Normalised to:
      {"name", "kind": "action"|"function", "bound_type": <entity type or "">,
       "entity_set": <set it returns/acts on or "">, "http_method", "label",
       "parameters": [...], "import_name": <name used for unbound calls>}
    V2: <FunctionImport> (m:HttpMethod, sap:action-for). V4: <Action>/<Function>
    (IsBound + binding parameter) plus <ActionImport>/<FunctionImport>.
    """
    ops: List[Dict] = []
    if version.startswith("4"):
        imports = {}
        for imp in _find_all(root, "ActionImport") + _find_all(root, "FunctionImport"):
            target = (imp.attrib.get("Action") or imp.attrib.get("Function") or "").split(".")[-1]
            if target:
                imports[target] = imp.attrib.get("Name", target)
        for kind in ("Action", "Function"):
            for el in _find_all(root, kind):
                name = el.attrib.get("Name")
                if not name:
                    continue
                params = _params(el)
                bound = el.attrib.get("IsBound", "false").lower() == "true"
                bound_type, binding_param = "", ""
                if bound and params:
                    bound_type = (params[0]["type"].replace("Collection(", "")
                                  .rstrip(")").split(".")[-1])
                    binding_param = params[0]["name"]
                    params = params[1:]
                op = {
                    "name": name, "kind": kind.lower(), "bound_type": bound_type,
                    "entity_set": "", "http_method": "POST" if kind == "Action" else "GET",
                    "label": _humanize(name), "parameters": params,
                    "import_name": imports.get(name, ""), "namespace": namespace,
                    "binding_param": binding_param,
                }
                op.update(_evoke_action_facts(el))
                ops.append(op)
    else:
        for el in _find_all(root, "FunctionImport"):
            name = el.attrib.get("Name")
            if not name:
                continue
            method = ""
            for k, v in el.attrib.items():
                if _localname(k) == "HttpMethod":
                    method = v.upper()
            ops.append({
                "name": name,
                "kind": "action" if method in ("POST", "PUT", "MERGE", "DELETE") else "function",
                "bound_type": (_sap_attr(el, "action-for") or "").split(".")[-1],
                "entity_set": el.attrib.get("EntitySet", ""),
                "http_method": method or "GET",
                "label": _sap_label(el, name),
                "parameters": _params(el),
                "import_name": name, "namespace": namespace,
            })
    return ops


def summarize_for_prompt(model: Dict, max_props: int = 25) -> str:
    """Compact text summary of the business model for feeding to the AI, kept
    small so it fits comfortably in the prompt budget."""
    lines = [f"OData {model['odata_version']} service, namespace {model['namespace']}.",
             f"{len(model['entity_sets'])} entity set(s):"]
    for es in model["entity_sets"]:
        props = ", ".join(f"{p['name']}({p['ui_type']})" for p in es["properties"][:max_props])
        more = "" if len(es["properties"]) <= max_props else f" (+{len(es['properties'])-max_props} more)"
        navs = ", ".join(n["name"] + ("[*]" if n["to_many"] else "") for n in es["navigations"])
        crud = "".join([
            "C" if es.get("creatable") else "-",
            "U" if es.get("updatable") else "-",
            "D" if es.get("deletable") else "-",
        ])
        lines.append(
            f"- {es['name']} (type {es['entity_type']}, keys {es['keys']}, CRUD {crud}): "
            f"{props}{more}" + (f"; navs: {navs}" if navs else "")
        )
    return "\n".join(lines)
