"""
fiori_elements_generator.py — Fiori Elements output path.

When the Design Recommendation Engine chooses target="fiori_elements" (only when
the service carries UI annotations), we generate a Fiori Elements application
instead of freestyle views. FE apps are almost entirely metadata- and
annotation-driven: there are no per-screen XML views or controllers to write —
SAP's FE runtime renders List Report / Object Page from:

  1. manifest.json  — declares the FE template (sap.fe.templates.ListReport /
                       ObjectPage), the routing between them, and the OData model.
  2. annotations.xml — local annotations that supplement the service's own:
                       UI.LineItem (table columns), UI.SelectionFields (filter
                       bar), UI.HeaderInfo (object page title), UI.FieldGroup
                       (object page sections), and a UI.Facets referencing the
                       child entity set as a line-item facet (the "items table"
                       on the object page).

This produces the header + item table + object-page navigation the roadmap asks
for, the SAP-standard way, using the annotations we already parsed.

Relationship handling: if the primary entity set has a to-many navigation and
the recommendation floorplan is ObjectPage, we emit a UI.Facets entry with a
UI.ReferenceFacet -> child UI.LineItem, which FE renders as a responsive item
table inside the object page, with $expand handled by the FE runtime.
"""

from __future__ import annotations
import json
from typing import Dict, List


def _ns(model: Dict) -> str:
    return model.get("namespace", "") or "Service"


def _primary_set(model: Dict, name: str) -> Dict:
    for es in model["entity_sets"]:
        if es["name"] == name:
            return es
    return model["entity_sets"][0]


def _child_set(model: Dict, name: str) -> Dict:
    for es in model["entity_sets"]:
        if es["name"] == name:
            return es
    return {}


def _datafield(prop: str, label: str = None) -> str:
    lbl = ('        <PropertyValue Property="Label" String="%s"/>\n' % label) if label else ""
    return (
        '      <Record Type="UI.DataField">\n'
        + lbl +
        '        <PropertyValue Property="Value" Path="%s"/>\n'
        '      </Record>\n' % prop
    )


def build_annotations_xml(model: Dict, plan: Dict, rec: Dict) -> str:
    """Local annotations.xml supplementing the service metadata. Uses the
    parsed annotations where present and falls back to plan fields otherwise."""
    ns = _ns(model)
    primary = _primary_set(model, plan["primary_entity_set"])
    et = primary["entity_type"]
    ann = primary.get("annotations") or {}
    prop_names = [p["name"] for p in primary["properties"]]

    # LineItem: prefer parsed UI.LineItem, then plan list_fields, then first cols
    line = ann.get("line_item") or plan.get("list_fields") or prop_names[:5]
    line = [f for f in line if f in prop_names]

    # SelectionFields: parsed selection, else plan filter_fields
    sel = ann.get("selection_fields") or plan.get("filter_fields") or []
    sel = [f for f in sel if f in prop_names]

    title_field = plan.get("title_field") if plan.get("title_field") in prop_names else (line[0] if line else prop_names[0])
    key_field = plan.get("key_field") if plan.get("key_field") in prop_names else primary["keys"][0]

    line_items = "".join(_datafield(f) for f in line)
    sel_fields = "".join('      <PropertyPath>%s</PropertyPath>\n' % f for f in sel)

    # Object page header
    header = (
        '    <Annotation Term="UI.HeaderInfo">\n'
        '      <Record Type="UI.HeaderInfoType">\n'
        '        <PropertyValue Property="TypeName" String="%s"/>\n' % et +
        '        <PropertyValue Property="TypeNamePlural" String="%ss"/>\n' % et +
        '        <PropertyValue Property="Title">\n'
        '          <Record Type="UI.DataField">\n'
        '            <PropertyValue Property="Value" Path="%s"/>\n' % title_field +
        '          </Record>\n'
        '        </PropertyValue>\n'
        '      </Record>\n'
        '    </Annotation>\n'
    )

    # Identification (object page general fields) from detail_fields / field group
    detail = plan.get("detail_fields") or prop_names
    detail = [f for f in detail if f in prop_names]
    identification = (
        '    <Annotation Term="UI.Identification">\n'
        '      <Collection>\n'
        + "".join(_datafield(f) for f in detail) +
        '      </Collection>\n'
        '    </Annotation>\n'
    )

    # Facets: General section + (if child) an items line-item facet
    facets_inner = (
        '        <Record Type="UI.ReferenceFacet">\n'
        '          <PropertyValue Property="Label" String="General"/>\n'
        '          <PropertyValue Property="Target" AnnotationPath="@UI.Identification"/>\n'
        '        </Record>\n'
    )
    child_name = plan.get("child_entity_set") or ""
    child_nav = ""
    if child_name:
        # find the navigation on the primary pointing at the child's type
        child = _child_set(model, child_name)
        child_type = child.get("entity_type", "")
        nav = next((n for n in primary.get("navigations", [])
                    if n.get("target_type") == child_type or n.get("to_many")), None)
        if nav and nav.get("name"):
            child_nav = nav["name"]
            facets_inner += (
                '        <Record Type="UI.ReferenceFacet">\n'
                '          <PropertyValue Property="Label" String="Items"/>\n'
                '          <PropertyValue Property="Target" '
                'AnnotationPath="%s/@UI.LineItem"/>\n' % child_nav +
                '        </Record>\n'
            )

    facets = (
        '    <Annotation Term="UI.Facets">\n'
        '      <Collection>\n'
        + facets_inner +
        '      </Collection>\n'
        '    </Annotation>\n'
    )

    # Child LineItem annotation (so the items facet renders columns)
    child_annotations = ""
    if child_name:
        child = _child_set(model, child_name)
        if child:
            cprops = [p["name"] for p in child["properties"]]
            ckeys = child.get("keys", [])
            ccols = [c for c in cprops if c not in ckeys][:5] or cprops[:5]
            child_annotations = (
                '  <Annotations Target="%s.%s">\n' % (ns, child["entity_type"]) +
                '    <Annotation Term="UI.LineItem">\n'
                '      <Collection>\n'
                + "".join(_datafield(f) for f in ccols) +
                '      </Collection>\n'
                '    </Annotation>\n'
                '  </Annotations>\n'
            )

    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<edmx:Edmx Version="4.0" xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">\n'
        '  <edmx:Reference Uri="https://sap.github.io/odata-vocabularies/vocabularies/UI.xml">\n'
        '    <edmx:Include Namespace="com.sap.vocabularies.UI.v1" Alias="UI"/>\n'
        '  </edmx:Reference>\n'
        '  <edmx:DataServices>\n'
        '   <Schema xmlns="http://docs.oasis-open.org/odata/ns/edm" Namespace="local">\n'
        '  <Annotations Target="%s.%s">\n' % (ns, et) +
        '    <Annotation Term="UI.LineItem">\n'
        '      <Collection>\n'
        + line_items +
        '      </Collection>\n'
        '    </Annotation>\n'
        '    <Annotation Term="UI.SelectionFields">\n'
        '      <Collection>\n'
        + sel_fields +
        '      </Collection>\n'
        '    </Annotation>\n'
        + header + identification + facets +
        '  </Annotations>\n'
        + child_annotations +
        '   </Schema>\n'
        '  </edmx:DataServices>\n'
        '</edmx:Edmx>\n'
    )


def build_manifest(model: Dict, plan: Dict, rec: Dict, app_id: str,
                   app_name: str, service_url: str, odata_version: str) -> str:
    """FE manifest.json: FE templates + routing (LR -> OP) + OData model."""
    primary = _primary_set(model, plan["primary_entity_set"])
    is_op = rec.get("floorplan") == "ObjectPage"
    v4 = odata_version.startswith("4")

    data_source = {
        "mainService": {
            "uri": service_url,
            "type": "OData",
            "settings": {
                "odataVersion": "4.0" if v4 else "2.0",
                "localUri": "localService/metadata.xml",
                "annotations": ["localAnnotations"],
            },
        },
        "localAnnotations": {
            "type": "ODataAnnotation",
            "uri": "annotations/annotations.xml",
            "settings": {"localUri": "annotations/annotations.xml"},
        },
    }

    model_settings = {"synchronizationMode": "None", "operationMode": "Server",
                      "autoExpandSelect": True, "earlyRequests": True} if v4 else {}

    # Routing: List Report as root, Object Page as detail (when OP floorplan).
    routing = {
        "routes": [
            {"pattern": ":?query:", "name": "ListReport",
             "target": "ListReport"},
        ],
        "targets": {
            "ListReport": {
                "type": "Component",
                "id": "ListReport",
                "name": "sap.fe.templates.ListReport",
                "options": {"settings": {
                    "entitySet": primary["name"],
                    "variantManagement": "Page",
                }},
            },
        },
    }
    if is_op:
        routing["routes"].append({
            "pattern": primary["name"] + "({key}):?query:",
            "name": "ObjectPage", "target": "ObjectPage",
        })
        routing["targets"]["ObjectPage"] = {
            "type": "Component", "id": "ObjectPage",
            "name": "sap.fe.templates.ObjectPage",
            "options": {"settings": {"entitySet": primary["name"]}},
        }
        # link LR -> OP navigation
        routing["targets"]["ListReport"]["options"]["settings"]["navigation"] = {
            primary["name"]: {"detail": {"route": "ObjectPage"}}
        }

    manifest = {
        "_version": "1.59.0",
        "sap.app": {
            "id": app_id,
            "type": "application",
            "title": app_name,
            "applicationVersion": {"version": "1.0.0"},
            "dataSources": data_source,
            "crossNavigation": {
                "inbounds": {
                    (app_id + "-display"): {
                        "semanticObject": "".join(c for c in primary["entity_type"] if c.isalnum()) or "App",
                        "action": "display",
                        "signature": {"parameters": {}, "additionalParameters": "allowed"},
                    }
                }
            },
        },
        "sap.ui": {"technology": "UI5", "deviceTypes": {"desktop": True, "tablet": True, "phone": True}},
        "sap.ui5": {
            "dependencies": {
                "minUI5Version": "1.120.0",
                "libs": {"sap.fe.templates": {}},
            },
            "models": {
                "": {
                    "dataSource": "mainService",
                    "preload": True,
                    **({"settings": model_settings} if model_settings else {}),
                },
                "i18n": {
                    "type": "sap.ui.model.resource.ResourceModel",
                    "uri": "i18n/i18n.properties",
                },
            },
            "routing": {
                "config": {"routerClass": "sap.f.routing.Router" if is_op else "sap.m.routing.Router"},
                **routing,
            },
            "contentDensities": {"compact": True, "cozy": True},
        },
    }
    return json.dumps(manifest, indent=2)


def build_fe_project(model: Dict, plan: Dict, rec: Dict, *, app_id: str,
                     app_name: str, service_url: str, odata_version: str,
                     extra_files: Dict[str, str] = None) -> Dict[str, str]:
    """Assemble the full FE app file set. `extra_files` lets the orchestrator
    merge in the shared deployment files (mta.yaml/xs-app.json/etc.)."""
    files: Dict[str, str] = {}
    files["manifest.json"] = build_manifest(
        model, plan, rec, app_id, app_name, service_url, odata_version)
    files["annotations/annotations.xml"] = build_annotations_xml(model, plan, rec)

    # Minimal Component.js (FE apps still need an app Component extending FE).
    files["Component.js"] = (
        'sap.ui.define(["sap/fe/core/AppComponent"], function (AppComponent) {\n'
        '    "use strict";\n'
        '    return AppComponent.extend("' + app_id + '.Component", {\n'
        '        metadata: { manifest: "json" }\n'
        '    });\n'
        '});\n'
    )
    files["index.html"] = (
        '<!DOCTYPE html>\n<html><head><meta charset="utf-8">\n'
        '<title>' + app_name + '</title>\n'
        '<script id="sap-ui-bootstrap"\n'
        '    src="https://ui5.sap.com/resources/sap-ui-core.js"\n'
        '    data-sap-ui-theme="sap_horizon"\n'
        '    data-sap-ui-resourceroots=\'{"' + app_id + '": "./"}\'\n'
        '    data-sap-ui-oninit="module:sap/ui/core/ComponentSupport"\n'
        '    data-sap-ui-compatVersion="edge"\n'
        '    data-sap-ui-async="true"></script>\n'
        '</head>\n<body class="sapUiBody">\n'
        '  <div data-sap-ui-component data-name="' + app_id + '"\n'
        '       data-id="container" data-settings=\'{"id":"' + app_id + '"}\'\n'
        '       style="height:100%"></div>\n'
        '</body></html>\n'
    )
    files["i18n/i18n.properties"] = (
        "appTitle=" + app_name + "\nappDescription=Fiori Elements application\n"
    )
    if extra_files:
        files.update(extra_files)
    return files
