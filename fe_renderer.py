"""
fe_renderer.py — blueprint -> Fiori Elements / Overview Page application.

Floorplans: ListReportObjectPage, Worklist, AnalyticalListPage (FE) and
OverviewPage (sap.ovp).

OData V4 services get the sap.fe (V4) templates; OData V2 services get the
sap.suite.ui.generic.template (V2) templates — the two are not interchangeable,
an FE V4 template cannot render a V2 service.

Everything the screens show comes from a generated local annotations file
(annotations/annotations.xml): UI.LineItem (columns, importance, row actions),
UI.SelectionFields (filter bar), UI.HeaderInfo / HeaderFacets / DataPoints
(object header), UI.Facets + UI.FieldGroup (sections), child UI.LineItem
(items tables + child object pages), Common.ValueList (value helps),
Common.FieldControl (mandatory fields), Capabilities restrictions, UI.Chart /
PresentationVariant (ALP + OVP analytical cards), UI.Identification (object
actions). Service actions/function imports become DataFieldForAction buttons;
blueprint "set_field" actions become small controller extensions.
"""

from __future__ import annotations
import json
from collections import OrderedDict
from typing import Dict, List

from ui5_common import (RenderContext, xattr, xtext, annotations_document, data_field,
                        data_field_for_action, collection, annotations_block,
                        index_html, i18n_key, i18n_model, sap_app_i18n, flp_sandbox)

_CRIT = {"Error": 1, "Warning": 2, "Success": 3, "Information": 5, "None": 0}


class _Annotations:
    """Collects annotation snippets per target and renders one block each."""

    def __init__(self):
        self.by_target: "OrderedDict[str, List[str]]" = OrderedDict()

    def add(self, target: str, xml: str):
        self.by_target.setdefault(target, []).append(xml)

    def render(self) -> str:
        return "".join(annotations_block(t, "".join(x)) for t, x in self.by_target.items())


# ─────────────────────────────────────────────────────────────────────────────
# Annotation content
# ─────────────────────────────────────────────────────────────────────────────
def _criticality(ctx: RenderContext, field: str) -> str:
    """V4 dynamic Criticality expression from the blueprint status mapping.
    (FE V2 does not evaluate dynamic expressions — status is shown uncoloured.)"""
    st = ctx.bp["main"]["status"]
    if not ctx.v4 or field != st["field"] or not st["states"]:
        return ""
    expr = "<Int>0</Int>"
    for value, state in reversed(list(st["states"].items())):
        expr = (f"<If><Eq><Path>{field}</Path><String>{value}</String></Eq>"
                f"<Int>{_CRIT[state]}</Int>{expr}</If>")
    return (f'            <PropertyValue Property="Criticality">{expr}</PropertyValue>\n')


def _action_target(ctx: RenderContext, a: Dict) -> str:
    op = ctx.op(a["operation"])
    if ctx.v4:
        if op.get("bound_type"):
            return f"{ctx.ns}.{op['name']}"
        return f"{ctx.ns}.{ctx.model.get('container', 'EntityContainer')}/{op.get('import_name') or op['name']}"
    # V2 templates resolve "<container namespace>.<container>/<function import>";
    # the unqualified "<container>/<fi>" is NOT found by the metamodel and the
    # button fails silently (TypeError reading 'sap:action-for').
    cns = ctx.model.get("container_namespace") or ctx.ns
    return f"{cns}.{ctx.model.get('container', '')}/{op['name']}"


def _op_actions(ctx: RenderContext, scope: str) -> List[Dict]:
    out = []
    for a in ctx.bp["actions"]:
        if a["kind"] != "operation":
            continue
        if ctx.v4 and ctx.op(a["operation"])["kind"] != "action":
            continue  # FE V4 can only invoke actions from buttons
        if not ctx.v4 and a.get("enabled_when"):
            continue  # V2: state-guarded -> custom extension action (see _ext_v2)
        if a["scope"] == scope or (scope == "object" and a["scope"] == "global"):
            out.append(a)
    return out


def _main_annotations(ctx: RenderContext, ann: _Annotations):
    bp, main = ctx.bp, ctx.bp["main"]
    es = ctx.es()
    tgt = ctx.type_fqn()

    # Table columns (+ row-level actions as toolbar buttons)
    recs = "".join(data_field(c["field"], importance=c["importance"],
                              criticality=_criticality(ctx, c["field"]))
                   for c in main["columns"])
    recs += "".join(data_field_for_action(_action_target(ctx, a),
                                          "{@i18n>" + ctx.t(i18n_key("action", a["id"]), a["label"]) + "}")
                    for a in _op_actions(ctx, "row"))
    ann.add(tgt, collection("UI.LineItem", recs))

    # Filter bar
    if main["filters"]:
        ann.add(tgt, '        <Annotation Term="UI.SelectionFields">\n          <Collection>\n'
                + "".join(f'            <PropertyPath>{f["field"]}</PropertyPath>\n'
                          for f in main["filters"])
                + '          </Collection>\n        </Annotation>\n')

    # Object header
    singular = ctx.t("typeName", es.get("label") or es["name"])
    plural = ctx.t("typeNamePlural", (es.get("label") or es["name"]) + "s")
    desc = (f'            <PropertyValue Property="Description">\n'
            f'              <Record Type="UI.DataField"><PropertyValue Property="Value" '
            f'Path={xattr(main["description_field"])}/></Record>\n'
            f'            </PropertyValue>\n') if main["description_field"] else ""
    ann.add(tgt,
            '        <Annotation Term="UI.HeaderInfo">\n'
            '          <Record Type="UI.HeaderInfoType">\n'
            f'            <PropertyValue Property="TypeName" String="{{@i18n>{singular}}}"/>\n'
            f'            <PropertyValue Property="TypeNamePlural" String="{{@i18n>{plural}}}"/>\n'
            '            <PropertyValue Property="Title">\n'
            f'              <Record Type="UI.DataField"><PropertyValue Property="Value" '
            f'Path={xattr(main["title_field"])}/></Record>\n'
            '            </PropertyValue>\n' + desc +
            '          </Record>\n'
            '        </Annotation>\n')

    # Header key figures as DataPoints
    hdr = bp["object_page"]["header_fields"]
    for f in hdr:
        crit = _criticality(ctx, f).replace("            ", "            ", 1)
        ann.add(tgt,
                f'        <Annotation Term="UI.DataPoint" Qualifier="{f}">\n'
                '          <Record Type="UI.DataPointType">\n'
                f'            <PropertyValue Property="Value" Path="{f}"/>\n'
                f'            <PropertyValue Property="Title" String={xattr(ctx.label(f))}/>\n'
                + crit +
                '          </Record>\n'
                '        </Annotation>\n')
    mcs = (bp.get("fe") or {}).get("header_microcharts") or []
    targets = [f"@UI.DataPoint#{f}" for f in hdr] + [_microchart(ctx, ann, tgt, c) for c in mcs]
    if targets:
        ann.add(tgt, '        <Annotation Term="UI.HeaderFacets">\n          <Collection>\n'
                + "".join('            <Record Type="UI.ReferenceFacet">\n'
                          f'              <PropertyValue Property="Target" AnnotationPath="{t}"/>\n'
                          '            </Record>\n' for t in targets)
                + '          </Collection>\n        </Annotation>\n')

    # Sections -> FieldGroups, tables -> child LineItems
    facets = ""
    for i, s in enumerate(bp["object_page"]["sections"], 1):
        q = f"S{i}"
        key = ctx.t(i18n_key("section", q), s["title"])
        ann.add(tgt, f'        <Annotation Term="UI.FieldGroup" Qualifier="{q}">\n'
                '          <Record Type="UI.FieldGroupType">\n'
                '            <PropertyValue Property="Data">\n              <Collection>\n'
                + "".join(data_field(f).replace("          <", "                <")
                          for f in s["fields"]) +
                '              </Collection>\n            </PropertyValue>\n'
                '          </Record>\n        </Annotation>\n')
        facets += ('            <Record Type="UI.ReferenceFacet">\n'
                   f'              <PropertyValue Property="ID" String="{q}"/>\n'
                   f'              <PropertyValue Property="Label" String="{{@i18n>{key}}}"/>\n'
                   f'              <PropertyValue Property="Target" AnnotationPath="@UI.FieldGroup#{q}"/>\n'
                   '            </Record>\n')
    for t in bp["object_page"]["tables"]:
        key = ctx.t(i18n_key("table", t["navigation"]), t["title"])
        facets += ('            <Record Type="UI.ReferenceFacet">\n'
                   f'              <PropertyValue Property="ID" String="{t["navigation"]}"/>\n'
                   f'              <PropertyValue Property="Label" String="{{@i18n>{key}}}"/>\n'
                   f'              <PropertyValue Property="Target" AnnotationPath="{t["navigation"]}/@UI.LineItem"/>\n'
                   '            </Record>\n')
    ann.add(tgt, '        <Annotation Term="UI.Facets">\n          <Collection>\n' + facets
            + '          </Collection>\n        </Annotation>\n')

    # Object-level actions (Object Page header buttons)
    obj_actions = _op_actions(ctx, "object")
    if obj_actions:
        ann.add(tgt, collection("UI.Identification", "".join(
            data_field_for_action(_action_target(ctx, a),
                                  "{@i18n>" + ctx.t(i18n_key("action", a["id"]), a["label"]) + "}")
            for a in obj_actions)))

    # Keys are never editable on an existing record (editable only while
    # creating) — otherwise FE offers to change e.g. the sales order number.
    for k in main["key"]:
        ann.add(f"{tgt}/{k}", '        <Annotation Term="Core.Immutable" Bool="true"/>\n')

    # Mandatory fields (a dynamic field rule on the same field takes over, V4)
    ruled = {r["field"] for r in (bp.get("fe") or {}).get("field_rules") or []
             if ctx.v4 and (r.get("readonly_when") or r.get("mandatory_when"))}
    for f in [f for f in bp["form"]["required"] if f not in ruled]:
        ann.add(f"{tgt}/{f}", '        <Annotation Term="Common.FieldControl" '
                'EnumMember="Common.FieldControlType/Mandatory"/>\n')

    # Value helps (same-service collections)
    for v in bp["value_helps"]:
        params = ('                  <Record Type="Common.ValueListParameterInOut">\n'
                  f'                    <PropertyValue Property="LocalDataProperty" PropertyPath="{v["field"]}"/>\n'
                  f'                    <PropertyValue Property="ValueListProperty" String="{v["key"]}"/>\n'
                  '                  </Record>\n')
        if v.get("text"):
            params += ('                  <Record Type="Common.ValueListParameterDisplayOnly">\n'
                       f'                    <PropertyValue Property="ValueListProperty" String="{v["text"]}"/>\n'
                       '                  </Record>\n')
        ann.add(f"{tgt}/{v['field']}",
                '        <Annotation Term="Common.ValueList">\n'
                '          <Record Type="Common.ValueListType">\n'
                f'            <PropertyValue Property="CollectionPath" String="{v["entity_set"]}"/>\n'
                '            <PropertyValue Property="Parameters">\n              <Collection>\n'
                + params +
                '              </Collection>\n            </PropertyValue>\n'
                '          </Record>\n        </Annotation>\n')

    # Capabilities (switch off what the blueprint disallows)
    container = f"{ctx.ns}.{ctx.model.get('container', '')}/{es['name']}"
    for cap, term, prop in (("create", "InsertRestrictions", "Insertable"),
                            ("update", "UpdateRestrictions", "Updatable"),
                            ("delete", "DeleteRestrictions", "Deletable")):
        if not bp["capabilities"][cap]:
            ann.add(container, f'        <Annotation Term="Capabilities.{term}">\n'
                    f'          <Record><PropertyValue Property="{prop}" Bool="false"/></Record>\n'
                    '        </Annotation>\n')

    _fe_extras(ctx, ann, tgt)

    # Chart + presentation variant (Analytical List Page)
    if bp["floorplan"] == "AnalyticalListPage" and bp["analytics"]["measures"]:
        _chart(ctx, ann, tgt, "", bp["analytics"]["measures"][0]["field"],
               bp["analytics"]["dimensions"][:1] or [main["columns"][0]["field"]],
               bp["analytics"]["chart"], with_line_item=not ctx.v4)


def _microchart(ctx, ann, tgt, c) -> str:
    """Header micro chart: DataPoint (+ UI.Chart for bullet/radial). Returns the facet target."""
    q = c["id"]
    target = (f'            <PropertyValue Property="TargetValue" Path="{c["target_field"]}"/>\n' if c["target_field"]
              else f'            <PropertyValue Property="TargetValue" Decimal="{c["target_value"]}"/>\n')
    vis = ('            <PropertyValue Property="Visualization" EnumMember="UI.VisualizationType/Progress"/>\n'
           if c["type"] == "progress" else "")
    ann.add(tgt,
            f'        <Annotation Term="UI.DataPoint" Qualifier="{q}">\n'
            '          <Record Type="UI.DataPointType">\n'
            f'            <PropertyValue Property="Value" Path="{c["field"]}"/>\n'
            f'            <PropertyValue Property="Title" String={xattr(c["title"])}/>\n'
            + target + vis +
            ('            <PropertyValue Property="MinimumValue" Decimal="0"/>\n' if c["type"] == "bullet" else "") +
            '          </Record>\n'
            '        </Annotation>\n')
    if c["type"] == "progress":
        return f"@UI.DataPoint#{q}"
    ctype = "Bullet" if c["type"] == "bullet" else "Donut"
    ann.add(tgt,
            f'        <Annotation Term="UI.Chart" Qualifier="{q}">\n'
            '          <Record Type="UI.ChartDefinitionType">\n'
            f'            <PropertyValue Property="Title" String={xattr(c["title"])}/>\n'
            f'            <PropertyValue Property="ChartType" EnumMember="UI.ChartType/{ctype}"/>\n'
            f'            <PropertyValue Property="Measures"><Collection><PropertyPath>{c["field"]}</PropertyPath></Collection></PropertyValue>\n'
            '            <PropertyValue Property="MeasureAttributes"><Collection>\n'
            '              <Record Type="UI.ChartMeasureAttributeType">\n'
            f'                <PropertyValue Property="Measure" PropertyPath="{c["field"]}"/>\n'
            '                <PropertyValue Property="Role" EnumMember="UI.ChartMeasureRoleType/Axis1"/>\n'
            f'                <PropertyValue Property="DataPoint" AnnotationPath="@UI.DataPoint#{q}"/>\n'
            '              </Record>\n'
            '            </Collection></PropertyValue>\n'
            '          </Record>\n'
            '        </Annotation>\n')
    return f"@UI.Chart#{q}"


def _expr(cond: Dict, then: str, other: str) -> str:
    vals = "".join(f"<Eq><Path>{cond['field']}</Path><String>{xtext(v)}</String></Eq>" for v in cond["values"])
    test = vals if len(cond["values"]) == 1 else f"<Or>{vals}</Or>"
    return f"<If>{test}{then}{other}</If>"


def _fe_extras(ctx: RenderContext, ann: "_Annotations", tgt: str):
    """Table views, code + text display, dropdowns, side effects, field rules."""
    fe = ctx.bp.get("fe") or {}
    # Labels: services without sap:label / Common.Label (e.g. Northwind) would show
    # unlabeled form fields in FE V4 — give every displayed property a readable label.
    from ui5_common import humanize
    bp, main = ctx.bp, ctx.bp["main"]
    shown = ({c["field"] for c in main["columns"]} | {f["field"] for f in main["filters"]}
             | {f for s in bp["object_page"]["sections"] for f in s["fields"]}
             | set(bp["object_page"]["header_fields"]))
    for f in sorted(shown):
        p = ctx.prop(f)
        if p.get("label") == humanize(f) or not p.get("label"):
            ann.add(f"{tgt}/{f}", f'        <Annotation Term="Common.Label" String={xattr(humanize(f))}/>\n')
    for t in bp["object_page"]["tables"]:
        ctgt = ctx.type_fqn(t["entity_set"])
        for f in t["columns"]:
            p = ctx.prop(f, t["entity_set"])
            if p.get("label") == humanize(f) or not p.get("label"):
                ann.add(f"{ctgt}/{f}", f'        <Annotation Term="Common.Label" String={xattr(humanize(f))}/>\n')
    # table views (tabs) -> SelectionVariants
    for v in fe.get("table_views") or []:
        opts = ""
        for f in v["filters"]:
            ranges = "".join('                    <Record Type="UI.SelectionRangeType">\n'
                             '                      <PropertyValue Property="Sign" EnumMember="UI.SelectionRangeSignType/I"/>\n'
                             '                      <PropertyValue Property="Option" EnumMember="UI.SelectionRangeOptionType/EQ"/>\n'
                             f'                      <PropertyValue Property="Low" String={xattr(val)}/>\n'
                             '                    </Record>\n' for val in f["values"])
            opts += ('              <Record Type="UI.SelectOptionType">\n'
                     f'                <PropertyValue Property="PropertyName" PropertyPath="{f["field"]}"/>\n'
                     '                <PropertyValue Property="Ranges">\n                  <Collection>\n'
                     + ranges + '                  </Collection>\n                </PropertyValue>\n'
                     '              </Record>\n')
        key = ctx.t(i18n_key("view", v["id"]), v["title"])
        ann.add(tgt,
                f'        <Annotation Term="UI.SelectionVariant" Qualifier="{v["id"]}">\n'
                '          <Record Type="UI.SelectionVariantType">\n'
                f'            <PropertyValue Property="Text" String="{{@i18n>{key}}}"/>\n'
                + ('            <PropertyValue Property="SelectOptions">\n              <Collection>\n'
                   + opts + '              </Collection>\n            </PropertyValue>\n' if opts else "") +
                '          </Record>\n'
                '        </Annotation>\n')
    # code + description
    for f, t in (fe.get("text_arrangement") or {}).items():
        ann.add(f"{tgt}/{f}", f'        <Annotation Term="Common.Text" Path="{t}">\n'
                '          <Annotation Term="UI.TextArrangement" EnumMember="UI.TextArrangementType/TextFirst"/>\n'
                '        </Annotation>\n')
    # dropdowns for short code lists
    for v in ctx.bp["value_helps"]:
        if v.get("dropdown"):
            ann.add(f"{tgt}/{v['field']}", '        <Annotation Term="Common.ValueListWithFixedValues" Bool="true"/>\n')
    # side effects
    for i, se in enumerate(fe.get("side_effects") or [], 1):
        src = "".join(f"<PropertyPath>{f}</PropertyPath>" for f in se["source"])
        tp = "".join((f"<String>{f}</String>" if ctx.v4 else f"<PropertyPath>{f}</PropertyPath>")
                     for f in se["target_properties"])
        te = "".join(f"<NavigationPropertyPath>{n}</NavigationPropertyPath>" for n in se["target_entities"])
        ann.add(tgt,
                f'        <Annotation Term="Common.SideEffects" Qualifier="SE{i}">\n'
                '          <Record Type="Common.SideEffectsType">\n'
                f'            <PropertyValue Property="SourceProperties"><Collection>{src}</Collection></PropertyValue>\n'
                + (f'            <PropertyValue Property="TargetProperties"><Collection>{tp}</Collection></PropertyValue>\n' if tp else "")
                + (f'            <PropertyValue Property="TargetEntities"><Collection>{te}</Collection></PropertyValue>\n' if te else "") +
                '          </Record>\n'
                '        </Annotation>\n')
    # state-dependent field rules (FE V4 evaluates dynamic expressions; V2 does not)
    rules = fe.get("field_rules") or []
    if rules and not ctx.v4:
        ctx.notes.append("State-dependent field rules (read-only/hidden/mandatory when ...) need OData V4 — "
                         "this V2 app shows those fields with their static settings.")
        return
    for r in rules:
        f = r["field"]
        if r.get("hidden_when"):
            ann.add(f"{tgt}/{f}", f'        <Annotation Term="UI.Hidden">{_expr(r["hidden_when"], "<Bool>true</Bool>", "<Bool>false</Bool>")}</Annotation>\n')
        fc_other = ("<EnumMember>Common.FieldControlType/Mandatory</EnumMember>" if f in ctx.bp["form"]["required"]
                    else "<EnumMember>Common.FieldControlType/Optional</EnumMember>")
        if r.get("readonly_when"):
            ann.add(f"{tgt}/{f}", '        <Annotation Term="Common.FieldControl">'
                    + _expr(r["readonly_when"], "<EnumMember>Common.FieldControlType/ReadOnly</EnumMember>", fc_other)
                    + '</Annotation>\n')
        elif r.get("mandatory_when"):
            ann.add(f"{tgt}/{f}", '        <Annotation Term="Common.FieldControl">'
                    + _expr(r["mandatory_when"], "<EnumMember>Common.FieldControlType/Mandatory</EnumMember>",
                            "<EnumMember>Common.FieldControlType/Optional</EnumMember>")
                    + '</Annotation>\n')


def _chart(ctx, ann, tgt, qualifier, measure, dims, chart_type, with_line_item=False):
    q = f' Qualifier="{qualifier}"' if qualifier else ""
    at = f"#{qualifier}" if qualifier else ""
    ctype = {"column": "Column", "bar": "Bar", "line": "Line", "pie": "Pie",
             "donut": "Donut"}.get(chart_type, "Column")
    ann.add(tgt,
            f'        <Annotation Term="UI.Chart"{q}>\n'
            '          <Record Type="UI.ChartDefinitionType">\n'
            f'            <PropertyValue Property="ChartType" EnumMember="UI.ChartType/{ctype}"/>\n'
            f'            <PropertyValue Property="Measures"><Collection><PropertyPath>{measure}</PropertyPath></Collection></PropertyValue>\n'
            '            <PropertyValue Property="Dimensions"><Collection>'
            + "".join(f"<PropertyPath>{d}</PropertyPath>" for d in dims) + '</Collection></PropertyValue>\n'
            '            <PropertyValue Property="MeasureAttributes"><Collection>\n'
            '              <Record Type="UI.ChartMeasureAttributeType">\n'
            f'                <PropertyValue Property="Measure" PropertyPath="{measure}"/>\n'
            '                <PropertyValue Property="Role" EnumMember="UI.ChartMeasureRoleType/Axis1"/>\n'
            '              </Record>\n'
            '            </Collection></PropertyValue>\n'
            '            <PropertyValue Property="DimensionAttributes"><Collection>\n'
            + "".join('              <Record Type="UI.ChartDimensionAttributeType">\n'
                      f'                <PropertyValue Property="Dimension" PropertyPath="{d}"/>\n'
                      '                <PropertyValue Property="Role" EnumMember="UI.ChartDimensionRoleType/Category"/>\n'
                      '              </Record>\n' for d in dims) +
            '            </Collection></PropertyValue>\n'
            '          </Record>\n'
            '        </Annotation>\n')
    vis = f'<AnnotationPath>@UI.Chart{at}</AnnotationPath>'
    if with_line_item:
        vis += '<AnnotationPath>@UI.LineItem</AnnotationPath>'
    ann.add(tgt,
            f'        <Annotation Term="UI.PresentationVariant"{q}>\n'
            '          <Record Type="UI.PresentationVariantType">\n'
            f'            <PropertyValue Property="Visualizations"><Collection>{vis}</Collection></PropertyValue>\n'
            '          </Record>\n'
            '        </Annotation>\n')
    ann.add(tgt,
            f'        <Annotation Term="UI.DataPoint" Qualifier="{qualifier or measure}">\n'
            '          <Record Type="UI.DataPointType">\n'
            f'            <PropertyValue Property="Value" Path="{measure}"/>\n'
            f'            <PropertyValue Property="Title" String={xattr(ctx.label(measure))}/>\n'
            '          </Record>\n'
            '        </Annotation>\n')


def _child_annotations(ctx: RenderContext, ann: _Annotations):
    for t in ctx.bp["object_page"]["tables"]:
        tgt = ctx.type_fqn(t["entity_set"])
        child = ctx.es(t["entity_set"])
        ann.add(tgt, collection("UI.LineItem", "".join(
            data_field(c, importance="High" if i < 3 else "Medium") for i, c in enumerate(t["columns"]))))
        title = t["columns"][0] if t["columns"] else child["keys"][0]
        ann.add(tgt,
                '        <Annotation Term="UI.HeaderInfo">\n'
                '          <Record Type="UI.HeaderInfoType">\n'
                f'            <PropertyValue Property="TypeName" String={xattr(child.get("label") or child["name"])}/>\n'
                f'            <PropertyValue Property="TypeNamePlural" String={xattr(t["title"])}/>\n'
                '            <PropertyValue Property="Title">\n'
                f'              <Record Type="UI.DataField"><PropertyValue Property="Value" Path="{title}"/></Record>\n'
                '            </PropertyValue>\n'
                '          </Record>\n'
                '        </Annotation>\n')
        fields = [p["name"] for p in child["properties"]
                  if p.get("visible", True)][:20]
        ann.add(tgt, '        <Annotation Term="UI.FieldGroup" Qualifier="Details">\n'
                '          <Record Type="UI.FieldGroupType">\n'
                '            <PropertyValue Property="Data">\n              <Collection>\n'
                + "".join(data_field(f).replace("          <", "                <") for f in fields) +
                '              </Collection>\n            </PropertyValue>\n'
                '          </Record>\n        </Annotation>\n')
        ann.add(tgt, '        <Annotation Term="UI.Facets">\n          <Collection>\n'
                '            <Record Type="UI.ReferenceFacet">\n'
                '              <PropertyValue Property="Label" String="{@i18n>section_Details}"/>\n'
                '              <PropertyValue Property="Target" AnnotationPath="@UI.FieldGroup#Details"/>\n'
                '            </Record>\n'
                '          </Collection>\n        </Annotation>\n')
        ctx.t("section_Details", "Details")


# ─────────────────────────────────────────────────────────────────────────────
# Custom (set_field) actions — controller extensions
# ─────────────────────────────────────────────────────────────────────────────
def _set_field_actions(ctx: RenderContext) -> List[Dict]:
    """Actions implemented as controller extensions: set_field always; on V2
    also service operations with a state condition (the V2 templates cannot
    disable a DataFieldForAction by record state, and calling e.g. SAP's
    releaseApprovalRequest on a document not in approval makes the backend dump)."""
    return [a for a in ctx.bp["actions"]
            if a["kind"] == "set_field" or (not ctx.v4 and a["kind"] == "operation" and a.get("enabled_when"))]


def _operation_available(ctx: RenderContext, ann: "_Annotations"):
    """V4: Core.OperationAvailable on bound actions with a state condition."""
    if not ctx.v4:
        return
    tname = ctx.type_fqn()
    for a in ctx.bp["actions"]:
        ew = a.get("enabled_when") or {}
        if a["kind"] != "operation" or not ew:
            continue
        op = ctx.op(a["operation"])
        if not op.get("bound_type") or not op.get("binding_param"):
            continue
        conds = "".join(f"<Eq><Path>{op['binding_param']}/{ew['field']}</Path><String>{xtext(v)}</String></Eq>"
                        for v in ew["values"])
        expr = conds if len(ew["values"]) == 1 else f"<Or>{conds}</Or>"
        ann.add(f"{ctx.ns}.{op['name']}({tname})",
                f'        <Annotation Term="Core.OperationAvailable">{expr}</Annotation>\n')


def _ext_v4(ctx: RenderContext) -> str:
    acts = _set_field_actions(ctx)
    for a in acts:
        ctx.t(i18n_key("action", a["id"]), a["label"])
        ctx.t(i18n_key("action", a["id"], "confirm"), f"{a['label']} the selected record(s)?")
        ctx.t(i18n_key("action", a["id"], "done"), f"{a['label']}: done.")
    body = ",\n".join(f"""        {a['id']}: function (oContext, aSelectedContexts) {{
            return run.call(this, oContext, aSelectedContexts, {json.dumps(a['field'])},
                {json.dumps(a['value'])}, "{i18n_key('action', a['id'])}", {str(a['confirm']).lower()});
        }}""" for a in acts)
    return f"""sap.ui.define([
    "sap/m/MessageBox",
    "sap/m/MessageToast"
], function (MessageBox, MessageToast) {{
    "use strict";

    // Blueprint "set_field" actions: update one field on the selected
    // record(s) (or the object shown) and save. `this` is the FE ExtensionAPI.
    function run(oContext, aSelectedContexts, sField, vValue, sKey, bConfirm) {{
        var oApi = this;
        var aContexts = (aSelectedContexts && aSelectedContexts.length) ? aSelectedContexts : [oContext];
        var oBundle = oApi.getModel("i18n").getResourceBundle();
        var fnDo = function () {{
            return Promise.all(aContexts.map(function (oCtx) {{
                return oCtx.setProperty(sField, vValue);
            }})).then(function () {{
                MessageToast.show(oBundle.getText(sKey + "_done"));
                return oApi.refresh ? oApi.refresh() : undefined;
            }}).catch(function (oError) {{
                MessageBox.error(oError.message || String(oError));
            }});
        }};
        if (!bConfirm) {{ return fnDo(); }}
        MessageBox.confirm(oBundle.getText(sKey + "_confirm"), {{
            onClose: function (sAction) {{ if (sAction === MessageBox.Action.OK) {{ fnDo(); }} }}
        }});
    }}

    return {{
{body}
    }};
}});
"""


def _ext_v2(ctx: RenderContext) -> str:
    acts = _set_field_actions(ctx)
    spec = {}
    props = {p["name"] for p in ctx.es()["properties"]}
    for a in acts:
        key = i18n_key("action", a["id"])
        ctx.t(key, a["label"])
        ctx.t(key + "_confirm", f"{a['label']} the selected record(s)?")
        ctx.t(key + "_done", f"{a['label']}: done.")
        ew = dict(a.get("enabled_when") or {})
        if ew:
            ew["label"] = ctx.label(ew["field"])
        item = {"kind": a["kind"], "key": key, "confirm": a["confirm"], "enabledWhen": ew}
        if a["kind"] == "set_field":
            item.update({"field": a["field"], "value": a["value"]})
        else:
            op = ctx.op(a["operation"])
            item["operation"] = {"name": op["name"], "method": op.get("http_method") or "POST",
                                 "keyParams": [p["name"] for p in op["parameters"] if p["name"] in props]}
        spec[a["id"]] = item
    ctx.t("noSelection", "Select at least one record first.")
    ctx.t("actionNotAllowed", "{0} is only possible when {1} is {2}. {3} of the {4} selected "
                              "record(s) are in a different state.")
    body = ",\n".join(f'        on{a["id"].capitalize()}: function () {{ run.call(this, "{a["id"]}"); }}'
                      for a in acts)
    return """sap.ui.define([
    "sap/m/MessageBox",
    "sap/m/MessageToast"
], function (MessageBox, MessageToast) {
    "use strict";

    // Custom actions for the V2 Fiori Elements templates, driven by ACTIONS:
    //  - set_field: MERGE one field on the selected rows / shown object
    //  - operation: call a function import, but ONLY for records in a state the
    //    backend supports (enabledWhen) — e.g. SAP releaseApprovalRequest on an
    //    order that is not in approval terminates with ASSERTION_FAILED.
    // `this` is the template controller.
    var ACTIONS = """ + json.dumps(spec, indent=4) + """;

    // SAP answers some backend failures with a short dump ("Runtime Error:
    // 'ASSERTION_FAILED' ... abnormally terminated"). Nothing was changed (the
    // request is rolled back); tell the user that instead of the raw dump text.
    function explainDump(sText) {
        var m = /Runtime Error:\\s*'?([A-Z_]+)'?/.exec(sText || "");
        if (!m && !/abnormally terminated/i.test(sText || "")) { return sText; }
        return "SAP could not process this request for the selected record and stopped with a " +
            "system error (" + (m ? m[1] : "short dump") + "). Nothing was changed.\\n\\n" +
            "This is caused by the record's data in SAP (e.g. an inconsistent approval / workflow " +
            "state), not by your input. Please ask your SAP administrator to check transaction " +
            "ST22 for this time stamp.\\n\\nDetails: " + sText;
    }

    function errorText(oError, sOperation) {
        var sRaw = rawErrorText(oError);
        if (/Runtime Error|abnormally terminated|ASSERTION_FAILED/i.test(sRaw) && /ApprovalRequest/i.test(sOperation || "")) {
            return "SAP could not " + (/reject/i.test(sOperation) ? "reject" : "release") + " the approval of this " +
                "document and stopped with a system error (ASSERTION_FAILED in the SD approval management). Nothing " +
                "was changed.\n\nThe document's approval data in SAP is inconsistent: it is still 'In Approval' although " +
                "its approval workflow was already rejected, cancelled or never started (SAP Knowledge Base Article " +
                "3406016). Please ask your SAP administrator to apply the correction from KBA 3406016 and repair the " +
                "document's approval workflow ('Manage Workflows for Sales Documents' or SWIA). Other documents in " +
                "approval can still be released normally.\n\nDetails: " + sRaw;
        }
        return explainDump(sRaw);
    }

    function rawErrorText(oError) {
        try {
            var o = JSON.parse(oError.responseText);
            return (o.error && o.error.message && (o.error.message.value || o.error.message)) || oError.message;
        } catch (e) {
            var m = /<message[^>]*>([^<]+)<\\/message>/.exec((oError && oError.responseText) || "");
            return m ? m[1] : ((oError && (oError.message || oError.statusText)) || String(oError));
        }
    }

    function run(sId) {
        var a = ACTIONS[sId];
        var oApi = this.extensionAPI;
        var oModel = this.getView().getModel();
        var oBundle = this.getView().getModel("@i18n").getResourceBundle();
        var aContexts = oApi.getSelectedContexts ? oApi.getSelectedContexts() : [];
        if (!aContexts.length && this.getView().getBindingContext()) {
            aContexts = [this.getView().getBindingContext()];
        }
        if (!aContexts.length) {
            MessageToast.show(oBundle.getText("noSelection"));
            return;
        }
        var w = a.enabledWhen || {};
        var that = this;
        // Read the CURRENT state from the server (the table may not have loaded
        // the field, and another user may have changed it meanwhile).
        var fnState = function (oCtx) {
            return new Promise(function (fnResolve) {
                if (!w.field) { fnResolve(true); return; }
                oModel.read(oCtx.getPath(), {
                    urlParameters: { "$select": w.field },
                    success: function (oData) { fnResolve(w.values.indexOf(String(oData[w.field])) >= 0); },
                    error: function () { fnResolve(false); }
                });
            });
        };
        Promise.all(aContexts.map(fnState)).then(function (aOk) {
            var iBad = aOk.filter(function (b) { return !b; }).length;
            if (iBad) {
                MessageBox.information(oBundle.getText("actionNotAllowed",
                    [oBundle.getText(a.key), w.label || w.field, w.values.join(" / "),
                     iBad, aContexts.length]));
                return;
            }
            proceed.call(that);
        });

        function proceed() {
        var fnOne = function (oCtx) {
            return new Promise(function (fnResolve, fnReject) {
                if (a.kind === "set_field") {
                    var mPayload = {};
                    mPayload[a.field] = a.value;
                    // direct MERGE: nothing stays pending, no data-loss popup
                    oModel.update(oCtx.getPath(), mPayload, { success: fnResolve, error: fnReject });
                } else {
                    var mUrl = {};
                    a.operation.keyParams.forEach(function (k) { mUrl[k] = oCtx.getProperty(k); });
                    oModel.callFunction("/" + a.operation.name, {
                        method: a.operation.method, urlParameters: mUrl,
                        success: fnResolve,
                        error: function (oErr) { fnReject(Object.assign(oErr || {}, { _op: a.operation.name })); }
                    });
                }
            });
        };
        var fnDo = function () {
            oApi.securedExecution(function () {
                return Promise.all(aContexts.map(fnOne));
            }).then(function () {
                MessageToast.show(oBundle.getText(a.key + "_done"));
                oModel.refresh(true);
            }).catch(function (oError) {
                // securedExecution already displays the backend's error messages; a short
                // dump additionally gets an explanation of what it means and who can fix it
                if (oError && /Runtime Error|abnormally terminated|ASSERTION_FAILED/i.test(rawErrorText(oError))) {
                    MessageBox.error(errorText(oError, oError._op));
                }
                oModel.refresh(true);
            });
        };
        if (!a.confirm) { fnDo(); return; }
        MessageBox.confirm(oBundle.getText(a.key + "_confirm"), {
            onClose: function (sAction) { if (sAction === MessageBox.Action.OK) { fnDo(); } }
        });
        }   // proceed
    }

    return {
""" + body + """
    };
});
"""


# ─────────────────────────────────────────────────────────────────────────────
# Manifests
# ─────────────────────────────────────────────────────────────────────────────
def _sap_app(ctx: RenderContext, extra_ds: Dict = None) -> Dict:
    ds = {
        "mainService": {
            "uri": ctx.service_path, "type": "OData",
            "settings": {"odataVersion": "4.0" if ctx.v4 else "2.0",
                         "localUri": "localService/metadata.xml",
                         "annotations": ["annotation"]},
        },
        "annotation": {"type": "ODataAnnotation", "uri": "annotations/annotations.xml",
                       "settings": {"localUri": "annotations/annotations.xml"}},
    }
    ds.update(extra_ds or {})
    sem = "".join(c for c in ctx.es()["entity_type"] if c.isalnum()) or "App"
    return {
        "id": ctx.app_id, "type": "application", "i18n": sap_app_i18n(ctx),
        "title": "{{appTitle}}", "description": "{{appDescription}}",
        "applicationVersion": {"version": "1.0.0"},
        "dataSources": ds,
        "crossNavigation": {"inbounds": {f"{sem}-display": {
            "semanticObject": sem, "action": "display", "title": "{{appTitle}}",
            "signature": {"parameters": {}, "additionalParameters": "allowed"}}}},
    }


def _manifest_v4(ctx: RenderContext) -> Dict:
    bp, es = ctx.bp, ctx.es()
    s = es["name"]
    fp = bp["floorplan"]
    keyp = "{key}"   # FE V4 routing convention: {key}, {key2}, ...
    lr_settings = {
        "contextPath": f"/{s}", "variantManagement": "Page", "initialLoad": "Enabled",
        "navigation": {s: {"detail": {"route": "ObjectPage"}}},
        "controlConfiguration": {"@com.sap.vocabularies.UI.v1.LineItem": {
            "tableSettings": {"type": "ResponsiveTable",
                              "selectionMode": "Multi" if any(a["scope"] == "row" for a in bp["actions"])
                              or bp["capabilities"]["delete"] else "Auto"}}},
    }
    fe = bp.get("fe") or {}
    tbl = lr_settings["controlConfiguration"]["@com.sap.vocabularies.UI.v1.LineItem"]["tableSettings"]
    if fe.get("personalization", True):
        tbl.update({"personalization": {"column": True, "sort": True, "filter": True},
                    "enableExport": True, "enableFullScreen": True, "condensedTableLayout": True})
    if fe.get("table_views") and fp in ("ListReportObjectPage", "Worklist"):
        lr_settings["views"] = {"paths": [{"key": v["id"],
                                           "annotationPath": f"com.sap.vocabularies.UI.v1.SelectionVariant#{v['id']}"}
                                          for v in fe["table_views"]], "showCounts": True}
    if fp == "Worklist":
        lr_settings["hideFilterBar"] = True
    if fp == "AnalyticalListPage":
        # FE V4 Analytical List Page = List Report in hybrid mode: chart
        # (primary presentation variant) above the table (secondary).
        lr_settings["views"] = {"paths": [{
            "primary": [{"annotationPath": "com.sap.vocabularies.UI.v1.PresentationVariant"}],
            "secondary": [{"annotationPath": "com.sap.vocabularies.UI.v1.LineItem"}],
            "defaultPath": "both"}]}
    op_settings = {"contextPath": f"/{s}", "editableHeaderContent": False, "navigation": {}}
    routes = [{"pattern": ":?query:", "name": "ListReport", "target": "ListReport"},
              {"pattern": f"{s}({keyp}):?query:", "name": "ObjectPage", "target": "ObjectPage"}]
    targets = {
        "ListReport": {"type": "Component", "id": "ListReport", "name": "sap.fe.templates.ListReport",
                       "options": {"settings": lr_settings}},
        "ObjectPage": {"type": "Component", "id": "ObjectPage", "name": "sap.fe.templates.ObjectPage",
                       "options": {"settings": op_settings}},
    }
    for t in bp["object_page"]["tables"]:
        child = ctx.es(t["entity_set"])
        ckey = "{key2}"
        rname = f"{t['navigation']}ObjectPage"
        routes.append({"pattern": f"{s}({keyp})/{t['navigation']}({ckey}):?query:",
                       "name": rname, "target": rname})
        targets[rname] = {"type": "Component", "id": rname, "name": "sap.fe.templates.ObjectPage",
                          "options": {"settings": {"contextPath": f"/{s}/{t['navigation']}",
                                                   "navigation": {}}}}
        op_settings["navigation"][t["navigation"]] = {"detail": {"route": rname}}

    # set_field custom actions
    acts = _set_field_actions(ctx)
    if acts:
        module = f"{ctx.app_id}.ext.CustomActions"
        row = {a["id"]: {"press": f"{module}.{a['id']}", "text": "{i18n>" + i18n_key("action", a["id"]) + "}",
                         "requiresSelection": True, "enabled": True, "visible": True}
               for a in acts if a["scope"] == "row"}
        obj = {a["id"]: {"press": f"{module}.{a['id']}", "text": "{i18n>" + i18n_key("action", a["id"]) + "}",
                         "enabled": True, "visible": True}
               for a in acts if a["scope"] != "row"}
        if row:
            lr_settings["controlConfiguration"]["@com.sap.vocabularies.UI.v1.LineItem"]["actions"] = row
        if obj:
            op_settings["content"] = {"header": {"actions": obj}}

    routing_config = {}
    root_view = None
    if fe.get("layout", "fcl") == "fcl":
        # list | object (| child object) side by side
        routing_config = {"routerClass": "sap.f.routing.Router",
                          "flexibleColumnLayout": {"defaultTwoColumnLayoutType": "TwoColumnsMidExpanded",
                                                   "defaultThreeColumnLayoutType": "ThreeColumnsMidExpanded"}}
        root_view = {"viewName": "sap.fe.templates.RootContainer.view.Fcl", "type": "XML", "async": True,
                     "id": "appRootView"}
        targets["ListReport"].update({"controlAggregation": "beginColumnPages", "contextPattern": ""})
        targets["ObjectPage"].update({"controlAggregation": "midColumnPages", "contextPattern": f"/{s}({keyp})"})
        routes[0]["target"] = ["ListReport"]
        routes[1]["target"] = ["ListReport", "ObjectPage"]
        for r in routes[2:]:
            targets[r["name"]].update({"controlAggregation": "endColumnPages"})
            r["target"] = ["ListReport", "ObjectPage", r["name"]]
    ui5_root = {"rootView": root_view} if root_view else {}
    return {
        "_version": "1.59.0",
        "sap.app": _sap_app(ctx),
        "sap.ui": {"technology": "UI5", "deviceTypes": {"desktop": True, "tablet": True, "phone": True}},
        "sap.ui5": {
            **ui5_root,
            "flexEnabled": True,
            "dependencies": {"minUI5Version": "1.120.0",
                             "libs": {"sap.m": {}, "sap.ui.core": {}, "sap.fe.templates": {},
                                      **({"sap.f": {}} if fe.get("layout", "fcl") == "fcl" else {}),
                                      **({"sap.suite.ui.microchart": {"lazy": True}}
                                         if fe.get("header_microcharts") else {})}},
            "contentDensities": {"compact": True, "cozy": True},
            "models": {
                "i18n": i18n_model(ctx),
                "": {"dataSource": "mainService", "preload": True,
                     "settings": {"operationMode": "Server", "autoExpandSelect": True,
                                  "earlyRequests": True}},
                "@i18n": i18n_model(ctx),
            },
            "routing": {"config": routing_config, "routes": routes, "targets": targets},
        },
        "sap.fiori": {"registrationIds": [], "archeType": "transactional"},
    }


def _manifest_v2(ctx: RenderContext) -> Dict:
    bp, es = ctx.bp, ctx.es()
    s = es["name"]
    fp = bp["floorplan"]
    multi = any(a["scope"] == "row" for a in bp["actions"]) or bp["capabilities"]["delete"]
    lr_component = {
        "name": "sap.suite.ui.generic.template.AnalyticalListPage" if fp == "AnalyticalListPage"
        else "sap.suite.ui.generic.template.ListReport",
        "list": True,
        "settings": {"condensedTableLayout": True, "smartVariantManagement": True,
                     "enableTableFilterInPageVariant": True,
                     # load data on launch (newer V2 FE setting + legacy flag)
                     "dataLoadSettings": {"loadDataOnAppLaunch": "always"},
                     "filterSettings": {"dateSettings": {"useDateRange": True}},
                     "tableSettings": {"multiSelect": multi, "type": "ResponsiveTable"}},
    }
    fe = bp.get("fe") or {}
    if fe.get("table_views") and fp in ("ListReportObjectPage", "Worklist"):
        lr_component["settings"]["quickVariantSelectionX"] = {
            "showCounts": True,
            "variants": {v["id"]: {"key": v["id"],
                                   "annotationPath": f"com.sap.vocabularies.UI.v1.SelectionVariant#{v['id']}"}
                         for v in fe["table_views"]}}
    if fp == "Worklist":
        lr_component["settings"]["isWorklist"] = True
    if fp == "AnalyticalListPage":
        lr_component["settings"] = {"tableType": "ResponsiveTable", "multiSelect": multi,
                                    "qualifier": "", "defaultContentView": "charttable",
                                    "smartVariantManagement": True}
    child_pages = {}
    for t in bp["object_page"]["tables"]:
        child_pages[f"ObjectPage|{t['navigation']}"] = {
            "navigationProperty": t["navigation"], "entitySet": t["entity_set"],
            "component": {"name": "sap.suite.ui.generic.template.ObjectPage"}}
    pages = {f"ListReport|{s}": {
        "entitySet": s, "component": lr_component,
        "pages": {f"ObjectPage|{s}": {
            "entitySet": s, "defaultLayoutTypeIfExternalNavigation": "MidColumnFullScreen",
            "component": {"name": "sap.suite.ui.generic.template.ObjectPage"},
            **({"pages": child_pages} if child_pages else {})}}}}

    manifest = {
        "_version": "1.59.0",
        "sap.app": _sap_app(ctx),
        "sap.ui": {"technology": "UI5", "deviceTypes": {"desktop": True, "tablet": True, "phone": True}},
        "sap.ui5": {
            "dependencies": {"minUI5Version": "1.120.0", "libs": {
                "sap.m": {}, "sap.ui.core": {}, "sap.f": {}, "sap.ui.comp": {},
                "sap.ui.generic.app": {}, "sap.suite.ui.generic.template": {}}},
            "contentDensities": {"compact": True, "cozy": True},
            "models": {
                "i18n": i18n_model(ctx),
                "@i18n": i18n_model(ctx),
                "": {"dataSource": "mainService", "preload": True,
                     "settings": {"defaultBindingMode": "TwoWay", "defaultCountMode": "Inline",
                                  "refreshAfterChange": False,
                                  "metadataUrlParams": {"sap-value-list": "none"}}},
            },
            "extends": {"extensions": {}},
        },
        "sap.ui.generic.app": {
            "_version": "1.3.0",
            "settings": {"forceGlobalRefresh": False, "objectPageHeaderType": "Dynamic",
                         "considerAnalyticalParameters": True, "showDraftToggle": False,
                         "flexibleColumnLayout": {"defaultTwoColumnLayoutType": "TwoColumnsMidExpanded",
                                                  "defaultThreeColumnLayoutType": "ThreeColumnsEndExpanded"}
                         if fp != "AnalyticalListPage" and fe.get("layout", "fcl") == "fcl" else None},
            "pages": pages,
        },
        "sap.fiori": {"registrationIds": [], "archeType": "transactional"},
    }
    if manifest["sap.ui.generic.app"]["settings"]["flexibleColumnLayout"] is None:
        del manifest["sap.ui.generic.app"]["settings"]["flexibleColumnLayout"]

    acts = _set_field_actions(ctx)
    if acts:
        ext = {}
        row = {a["id"]: {"id": f"{a['id']}Button", "text": "{@i18n>" + i18n_key("action", a["id"]) + "}",
                         "press": f"on{a['id'].capitalize()}", "requiresSelection": True}
               for a in acts if a["scope"] == "row"}
        obj = {a["id"]: {"id": f"{a['id']}Button", "text": "{@i18n>" + i18n_key("action", a["id"]) + "}",
                         "press": f"on{a['id'].capitalize()}", "determining": False}
               for a in acts if a["scope"] != "row"}
        if row:
            ext["sap.suite.ui.generic.template.ListReport.view.ListReport"] = {
                "controllerName": f"{ctx.app_id}.ext.controller.ListReportExt",
                "sap.ui.generic.app": {s: {"EntitySet": s, "Actions": row}}}
        if obj:
            ext["sap.suite.ui.generic.template.ObjectPage.view.Details"] = {
                "controllerName": f"{ctx.app_id}.ext.controller.ObjectPageExt",
                "sap.ui.generic.app": {s: {"EntitySet": s, "Header": {"Actions": obj}}}}
        manifest["sap.ui5"]["extends"]["extensions"]["sap.ui.controllerExtensions"] = ext
    return manifest


# ─────────────────────────────────────────────────────────────────────────────
# Overview Page
# ─────────────────────────────────────────────────────────────────────────────
def _ovp(ctx: RenderContext, ann: _Annotations) -> Dict:
    bp = ctx.bp
    cards = {}
    v = "v4." if ctx.v4 else ""
    for c in bp["cards"]:
        tgt = ctx.type_fqn(c["entity_set"])
        q = c["id"]
        title = ctx.t(f"{q}_title", c["title"])
        settings = {"title": "{{" + title + "}}", "entitySet": c["entity_set"],
                    "addODataSelect": True}
        if c["type"] in ("list", "table"):
            ann.add(tgt, collection("UI.LineItem", "".join(
                data_field(f, importance="High") for f in c["fields"][:3 if c["type"] == "list" else 4]), q))
            settings["annotationPath"] = f"com.sap.vocabularies.UI.v1.LineItem#{q}"
            template = f"sap.ovp.cards.{v}{c['type']}"
            if c["type"] == "list":
                settings["listType"] = "condensed"
                settings["listFlavor"] = "standard"
        else:
            _chart(ctx, ann, tgt, q, c["measure"], [c["dimension"]], bp["analytics"]["chart"])
            ann.add(tgt, collection("UI.Identification", "", q))
            settings.update({
                "chartAnnotationPath": f"com.sap.vocabularies.UI.v1.Chart#{q}",
                "presentationAnnotationPath": f"com.sap.vocabularies.UI.v1.PresentationVariant#{q}",
                "dataPointAnnotationPath": f"com.sap.vocabularies.UI.v1.DataPoint#{q}",
                "identificationAnnotationPath": f"com.sap.vocabularies.UI.v1.Identification#{q}",
            })
            template = f"sap.ovp.cards.{v}charts.analytical"
        cards[q] = {"model": "mainModel", "template": template, "settings": settings}

    # Global filter from the main entity's selection fields
    main_tgt = ctx.type_fqn()
    if bp["main"]["filters"]:
        ann.add(main_tgt, '        <Annotation Term="UI.SelectionFields">\n          <Collection>\n'
                + "".join(f'            <PropertyPath>{f["field"]}</PropertyPath>\n'
                          for f in bp["main"]["filters"])
                + '          </Collection>\n        </Annotation>\n')
    ovp = {"globalFilterModel": "mainModel", "containerLayout": "resizable",
           "enableLiveFilter": True, "considerAnalyticalParameters": False, "cards": cards}
    # Entity SET, not type: OVP resolves a V2 entity type in the container's
    # schema, which fails for services whose types live in another schema
    # (e.g. Northwind) and leaves the page blank.
    ovp["globalFilterEntitySet"] = bp["main"]["entity_set"]
    return ovp


def _manifest_ovp(ctx: RenderContext, ann: _Annotations) -> Dict:
    ovp = _ovp(ctx, ann)
    model_settings = ({"operationMode": "Server", "autoExpandSelect": True}
                      if ctx.v4 else {"defaultCountMode": "Inline", "refreshAfterChange": False})
    return {
        "_version": "1.59.0",
        "sap.app": _sap_app(ctx),
        "sap.ui": {"technology": "UI5", "deviceTypes": {"desktop": True, "tablet": True, "phone": True}},
        "sap.ui5": {
            "dependencies": {"minUI5Version": "1.120.0", "libs": {
                "sap.m": {}, "sap.ui.core": {}, "sap.ui.layout": {}, "sap.f": {},
                "sap.ui.comp": {}, "sap.ovp": {}}},
            "contentDensities": {"compact": True, "cozy": True},
            "models": {
                "@i18n": i18n_model(ctx),
                "i18n": i18n_model(ctx),
                "mainModel": {"dataSource": "mainService", "preload": True, "settings": model_settings},
            },
            "extends": {"extensions": {}},
        },
        "sap.ovp": ovp,
        "sap.fiori": {"registrationIds": [], "archeType": "analytical"},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
def render(ctx: RenderContext) -> Dict[str, str]:
    bp = ctx.bp
    ctx.t("appTitle", bp["app"]["title"])
    ctx.t("appDescription", bp["app"]["description"] or bp["app"]["title"])
    ann = _Annotations()
    files: Dict[str, str] = {}

    if bp["floorplan"] == "OverviewPage":
        manifest = _manifest_ovp(ctx, ann)
        component_base, libs = "sap/ovp/app/Component", "sap.m,sap.ovp"
    else:
        _main_annotations(ctx, ann)
        _child_annotations(ctx, ann)
        _operation_available(ctx, ann)
        if ctx.v4:
            manifest = _manifest_v4(ctx)
            component_base, libs = "sap/fe/core/AppComponent", "sap.m,sap.fe.templates"
            if _set_field_actions(ctx):
                files["ext/CustomActions.js"] = _ext_v4(ctx)
        else:
            manifest = _manifest_v2(ctx)
            component_base, libs = ("sap/suite/ui/generic/template/lib/AppComponent",
                                    "sap.m,sap.suite.ui.generic.template")
            if _set_field_actions(ctx):
                code = _ext_v2(ctx)
                if any(a["scope"] == "row" for a in _set_field_actions(ctx)):
                    files["ext/controller/ListReportExt.controller.js"] = code
                if any(a["scope"] != "row" for a in _set_field_actions(ctx)):
                    files["ext/controller/ObjectPageExt.controller.js"] = code

    files["manifest.json"] = json.dumps(manifest, indent=4)
    files["annotations/annotations.xml"] = annotations_document(ctx, ann.render())
    files["Component.js"] = (
        f'sap.ui.define(["{component_base}"], function (Component) {{\n'
        '    "use strict";\n'
        f'    return Component.extend("{ctx.app_id}.Component", {{\n'
        '        metadata: { manifest: "json" }\n'
        '    });\n'
        '});\n')
    files["index.html"] = index_html(ctx, libs)
    files["test/flpSandbox.html"] = flp_sandbox(ctx, libs.replace("sap.m,", ""))
    return files
