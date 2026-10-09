"""
annotation_reader.py — Annotation Reader (roadmap item #3, also feeds #1,#2,#4,#7).

Enterprise SAP OData services carry UI/business intent in annotations, not just
in the bare EDM types. This module extracts the ones that most improve generated
UIs, from BOTH annotation styles:

  * OData V4 inline annotations: <Annotation Term="UI.LineItem"> ... </Annotation>
    plus <Annotations Target="ns.Type/Prop"> blocks, and the Capabilities vocab
    (Insert/Update/DeleteRestrictions).

  * OData V2 SAP attributes: sap:label, sap:creatable/updatable/deletable,
    sap:sortable/filterable, sap:value-list, sap:semantics, sap:visible,
    sap:unit, and (when present) the analogous SAP annotations file.

Extracted, normalised per entity set:

  {
    "line_item":      ["Vbeln","Customer","NetAmount",...],   # UI.LineItem order
    "selection_fields":["Status","Customer"],                 # UI.SelectionField
    "field_groups":   {"General":["Customer","Currency"], ...},# UI.FieldGroup
    "value_lists":    {"Customer": {"collection":"CustomerSet",
                                     "key":"CustomerId","text":"Name"}},
    "capabilities":   {"insertable":true,"updatable":true,"deletable":false},
    "semantics":      {"NetAmount":"amount","Currency":"currency",
                        "Status":"status"}                     # sap:semantics/unit
  }

Everything is best-effort and defensive: a service with no annotations yields
empty structures, and the generator degrades to plain metadata-driven output.
Nothing here raises on missing annotations.
"""

from __future__ import annotations
from typing import Dict, List
import xml.etree.ElementTree as ET


def _local(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _iter(root, name: str):
    return [el for el in root.iter() if _local(el.tag) == name]


# ─────────────────────────────────────────────────────────────────────────────
# V4 inline annotation extraction
# ─────────────────────────────────────────────────────────────────────────────
def _v4_collection_prop_paths(ann_el) -> List[str]:
    """From a UI.LineItem / UI.SelectionField annotation, pull the ordered
    property names referenced by PropertyPath / DataField Value."""
    out = []
    for el in ann_el.iter():
        ln = _local(el.tag)
        if ln == "PropertyPath" and el.text:
            out.append(el.text.strip())
        elif ln == "PropertyValue" and el.attrib.get("Property") in ("Value", "ValueListProperty"):
            # DataField record: <PropertyValue Property="Value" Path="NetAmount"/>
            path = el.attrib.get("Path")
            if path:
                out.append(path.strip())
        elif ln == "Record":
            # DataField record with a nested <PropertyValue Property="Value" Path=.../>
            for pv in el:
                if _local(pv.tag) == "PropertyValue" and pv.attrib.get("Property") == "Value":
                    p = pv.attrib.get("Path")
                    if p:
                        out.append(p.strip())
    # de-dupe preserving order
    seen, ordered = set(), []
    for p in out:
        if p not in seen:
            seen.add(p); ordered.append(p)
    return ordered


def _v4_field_groups(ann_el) -> Dict[str, List[str]]:
    """UI.FieldGroup -> {label: [prop,...]}. Uses the Data collection's paths."""
    label = "General"
    for pv in ann_el.iter():
        if _local(pv.tag) == "PropertyValue" and pv.attrib.get("Property") == "Label":
            label = pv.attrib.get("String", label)
    return {label: _v4_collection_prop_paths(ann_el)}


def _v4_value_list(ann_el) -> Dict:
    """Common.ValueList -> {collection, key, text}."""
    out = {"collection": "", "key": "", "text": ""}
    for pv in ann_el.iter():
        if _local(pv.tag) != "PropertyValue":
            continue
        prop = pv.attrib.get("Property")
        if prop == "CollectionPath":
            out["collection"] = pv.attrib.get("String", "")
        elif prop == "Parameters":
            for rec in pv.iter():
                if _local(rec.tag) == "PropertyValue":
                    lp = rec.attrib.get("Property")
                    if lp == "ValueListProperty" and not out["text"]:
                        out["text"] = rec.attrib.get("String", "")
                    if lp == "LocalDataProperty" and not out["key"]:
                        out["key"] = rec.attrib.get("String", "")
    return out


def _extract_v4(root) -> Dict[str, Dict]:
    """Walk <Annotations Target="..."> and inline <Annotation> to build a
    per-target annotation map keyed by the EDM target string."""
    per_target: Dict[str, Dict] = {}

    def ensure(target: str) -> Dict:
        return per_target.setdefault(target, {
            "line_item": [], "selection_fields": [], "field_groups": {},
            "value_lists": {}, "capabilities": {}, "semantics": {},
        })

    for anns in _iter(root, "Annotations"):
        target = anns.attrib.get("Target", "")
        if not target:
            continue
        bucket = ensure(target)
        for ann in anns:
            if _local(ann.tag) != "Annotation":
                continue
            term = ann.attrib.get("Term", "")
            if term.endswith("UI.LineItem") or term == "UI.LineItem":
                bucket["line_item"] = _v4_collection_prop_paths(ann)
            elif term.endswith("SelectionFields") or "SelectionField" in term:
                bucket["selection_fields"] = _v4_collection_prop_paths(ann)
            elif term.endswith("FieldGroup"):
                bucket["field_groups"].update(_v4_field_groups(ann))
            elif term.endswith("ValueList") or "ValueList" in term:
                # target like "ns.Type/Customer" -> prop is after the slash
                prop = target.split("/")[-1] if "/" in target else ""
                if prop:
                    bucket["value_lists"][prop] = _v4_value_list(ann)
            elif term.endswith("InsertRestrictions"):
                bucket["capabilities"]["insertable"] = _v4_restriction_bool(ann, "Insertable")
            elif term.endswith("UpdateRestrictions"):
                bucket["capabilities"]["updatable"] = _v4_restriction_bool(ann, "Updatable")
            elif term.endswith("DeleteRestrictions"):
                bucket["capabilities"]["deletable"] = _v4_restriction_bool(ann, "Deletable")
    return per_target


def _v4_property_facts(root) -> Dict:
    """Property-level V4 facts, from both inline <Property><Annotation .../>
    and external <Annotations Target="ns.Type/Prop">:
      {"labels": {(type, prop): label}, "computed": {(type, prop)},
       "apply_supported": bool}"""
    facts = {"labels": {}, "computed": set(), "apply_supported": False}

    def take(type_name, prop, ann):
        term = ann.attrib.get("Term", "")
        if term.endswith("Label") and ann.attrib.get("String"):
            facts["labels"][(type_name, prop)] = ann.attrib["String"]
        elif term.endswith("Computed") or term.endswith("Immutable"):
            if ann.attrib.get("Bool", "true").lower() != "false":
                facts["computed"].add((type_name, prop))

    for et in _iter(root, "EntityType"):
        tname = et.attrib.get("Name", "")
        for prop in et:
            if _local(prop.tag) == "Property":
                for ann in prop:
                    if _local(ann.tag) == "Annotation":
                        take(tname, prop.attrib.get("Name", ""), ann)
    for anns in _iter(root, "Annotations"):
        target = anns.attrib.get("Target", "")
        for ann in anns:
            if _local(ann.tag) != "Annotation":
                continue
            if ann.attrib.get("Term", "").endswith("ApplySupported"):
                facts["apply_supported"] = True
            if "/" in target:
                tpart, prop = target.split("/", 1)
                take(tpart.split(".")[-1], prop, ann)
    for ann in _iter(root, "Annotation"):
        if ann.attrib.get("Term", "").endswith("ApplySupported"):
            facts["apply_supported"] = True
    return facts


def _v4_restriction_bool(ann_el, prop_name: str) -> bool:
    for pv in ann_el.iter():
        if _local(pv.tag) == "PropertyValue" and pv.attrib.get("Property") == prop_name:
            return pv.attrib.get("Bool", "true").strip().lower() != "false"
    return True


# ─────────────────────────────────────────────────────────────────────────────
# V2 SAP-attribute extraction (per Property element)
# ─────────────────────────────────────────────────────────────────────────────
def _sap_attr(el, name: str):
    for k, v in el.attrib.items():
        if _local(k) == name:
            return v
    return None


def _extract_v2_semantics(prop_el) -> str:
    """Map sap:semantics / sap:unit hints to a coarse semantic role."""
    sem = _sap_attr(prop_el, "semantics")
    if sem:
        s = sem.lower()
        if "currency" in s:
            return "currency"
        if "amount" in s or "quantity" in s:
            return "amount"
        if s in ("email", "tel", "url"):
            return s
    if _sap_attr(prop_el, "unit"):
        return "amount"
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Public API — merge annotations onto an already-parsed business model
# ─────────────────────────────────────────────────────────────────────────────
def enrich_model_with_annotations(model: Dict, edmx_text: str) -> Dict:
    """Adds an `annotations` block to each entity set in `model` (in place) and
    returns the model. Safe to call for any service; produces empty structures
    when no annotations are present."""
    try:
        root = ET.fromstring(edmx_text.encode("utf-8"))
    except ET.ParseError:
        # If XML won't parse here, leave the model as-is with empty annotations.
        for es in model.get("entity_sets", []):
            es.setdefault("annotations", _empty_ann())
        return model

    is_v4 = model.get("odata_version", "2.0").startswith("4")
    v4_map = _extract_v4(root) if is_v4 else {}
    facts = _v4_property_facts(root) if is_v4 else None
    ns = model.get("namespace", "")

    for es in model["entity_sets"]:
        ann = _empty_ann()
        et = es["entity_type"]
        prop_names = [p["name"] for p in es["properties"]]

        if is_v4:
            for p in es["properties"]:
                lbl = facts["labels"].get((et, p["name"]))
                if lbl:
                    p["label"] = lbl
                if (et, p["name"]) in facts["computed"]:
                    p["updatable"] = p["creatable"] = False
            if facts["apply_supported"]:
                es["aggregatable"] = True
            # V4: annotations targeted at "ns.Type" or "ns.EntityContainer/Set"
            for target, bucket in v4_map.items():
                tail = target.split(".")[-1]
                if tail == et or tail.startswith(et) or es["name"] in target:
                    for k in ann:
                        if bucket.get(k):
                            if isinstance(ann[k], dict):
                                ann[k].update(bucket[k])
                            elif isinstance(ann[k], list) and not ann[k]:
                                ann[k] = bucket[k]
                            else:
                                ann[k] = bucket[k]
        else:
            # V2: derive from sap: attributes we can see on properties.
            sortable, filterable = [], []
            # Re-scan the raw XML for this entity type's Property elements.
            for et_el in _iter(root, "EntityType"):
                if et_el.attrib.get("Name") != et:
                    continue
                for p in et_el:
                    if _local(p.tag) != "Property":
                        continue
                    pname = p.attrib.get("Name")
                    if not pname:
                        continue
                    sem = _extract_v2_semantics(p)
                    if sem:
                        ann["semantics"][pname] = sem
                    if (_sap_attr(p, "filterable") or "true").lower() != "false":
                        filterable.append(pname)
                    if _sap_attr(p, "value-list"):
                        # V2 marks the field; the actual VL service is a separate
                        # entity set the AI/consumer resolves. Record the marker.
                        ann["value_lists"][pname] = {"collection": "", "key": "", "text": ""}
            # A reasonable default selection set: filterable text/status fields.
            ann["selection_fields"] = [f for f in filterable
                                       if f in prop_names][:4]

        # Fill capabilities from the model's CRUD flags if annotations didn't set them.
        ann["capabilities"].setdefault("insertable", es.get("creatable", True))
        ann["capabilities"].setdefault("updatable", es.get("updatable", True))
        ann["capabilities"].setdefault("deletable", es.get("deletable", True))

        es["annotations"] = ann

    return model


def _empty_ann() -> Dict:
    return {
        "line_item": [], "selection_fields": [], "field_groups": {},
        "value_lists": {}, "capabilities": {}, "semantics": {},
    }
