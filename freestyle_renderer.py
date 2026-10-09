"""
freestyle_renderer.py — blueprint -> freestyle SAPUI5 application.

Floorplans: FreestyleListDetail (list page -> object page), FreestyleMasterDetail
(sap.f.FlexibleColumnLayout, list + object side by side) and FreestyleWorklist
(KPI header, multi-select, bulk actions).

Generated features: DynamicPage with FilterBar (text/date-range/status
multi-select/boolean/value-help filters), search, growing table with count,
status colouring, typed formatting from the real EDM types, sort dialog, Excel
export, create dialog with required-field and type validation, multi-delete,
row/object actions (service operations with parameter prompts, or set-field
updates), Object page with header key figures, sectioned forms with
edit/save/cancel, child tables over navigations, value-help dialogs.
Works against OData V2 and V4 (model/DataService.js hides the differences).
"""

from __future__ import annotations
import json
from typing import Dict, List

from ui5_common import RenderContext, xattr, index_html, i18n_key, i18n_model, sap_app_i18n
import freestyle_js as js
import freestyle_pages_js as pjs
import multi_service as _ms

_ODATA_TYPES = {
    "Edm.Decimal": "Decimal", "Edm.Double": "Double", "Edm.Single": "Single",
    "Edm.Int16": "Int16", "Edm.Int32": "Int32", "Edm.Int64": "Int64",
    "Edm.Byte": "Byte", "Edm.SByte": "SByte", "Edm.Boolean": "Boolean",
    "Edm.Guid": "Guid", "Edm.Date": "Date", "Edm.TimeOfDay": "TimeOfDay",
    "Edm.DateTimeOffset": "DateTimeOffset", "Edm.DateTime": "DateTime", "Edm.Time": "Time",
}


def _binding(ctx: RenderContext, p: Dict, model: str = "", json_model: bool = False, rel: bool = False) -> str:
    """Typed binding expression for a property.

    V4 models type bindings from $metadata automatically, so plain paths are
    used there — except for the JSON 'create' model, which needs explicit
    OData types so input is parsed into the wire format the service expects."""
    path = (model + (">" if rel else ">/") if model else "") + p["name"]
    if p.get("enum"):
        # V4 types enum properties as odata.type.Raw, which cannot format to
        # a string; show/edit the member name as-is.
        return "{path: '" + path + "', targetType: 'any'}"
    if ctx.v4 and not json_model:
        return "{" + path + "}"
    t = p.get("type", "Edm.String")
    cons = {}
    if t == "Edm.String":
        if p.get("max_length"):
            cons["maxLength"] = p["max_length"]
        tname = "String"
    else:
        tname = _ODATA_TYPES.get(t)
        if not tname:
            return "{" + path + "}"
        if t == "Edm.Decimal":
            if p.get("precision"):
                cons["precision"] = p["precision"]
            if p.get("scale") is not None:
                cons["scale"] = p["scale"]
        if t == "Edm.DateTime" and p.get("ui_type") == "date":
            cons["displayFormat"] = "Date"
        if t == "Edm.DateTimeOffset" and ctx.v4:
            cons["V4"] = True
    expr = f"path: '{path}', type: 'sap.ui.model.odata.type.{tname}'"
    if cons:
        expr += ", constraints: " + json.dumps(cons).replace('"', "'").replace("'V4'", "V4") \
            .replace("'maxLength'", "maxLength").replace("'precision'", "precision") \
            .replace("'scale'", "scale").replace("'displayFormat'", "displayFormat")
    return "{" + expr + "}"


class _Fs:
    def __init__(self, ctx: RenderContext, pid: str = "", fid: str = "",
                 has_detail: bool = True, back_key: str = "appTitle",
                 quick_tabs: bool = False, logic_page: str = "main"):
        """pid/fid: page id / file prefix for multi-page apps ('' = single-page
        app, original file and i18n names). back_key: i18n key of the page the
        detail breadcrumb returns to. logic_page: key of this entity's hooks in
        model/logic.js (the list page id; 'main' for single-page apps)."""
        self.ctx = ctx
        self.bp = ctx.bp
        self.main = ctx.bp["main"]
        self.vh = {v["field"]: v for v in ctx.bp["value_helps"]}
        self.pid, self.fid = pid, fid
        self.has_detail = has_detail
        self.back_key = back_key
        self.quick_tabs = quick_tabs
        self.logic_page = logic_page

    # ── custom logic (model/logic.js) ───────────────────────────────────────
    def logic(self) -> Dict:
        return self.bp.get("logic") or {}

    def computed(self) -> List[Dict]:
        return self.logic().get("computed") or []

    def _parts(self, fields: List[str]) -> str:
        """Composite-binding parts delivering RAW values to the logic hooks (V4
        would otherwise format them, e.g. 8287 -> "8,287", which is not a number)."""
        fields = fields or list(self.main["key"])[:1]
        tt = ", targetType: 'any'" if self.ctx.v4 else ""
        return "[" + ", ".join("{path: '" + f + "'" + tt + "}" for f in fields) + "]"

    def computed_binding(self, cc: Dict) -> str:
        return "{parts: " + self._parts(cc["uses"]) + ", formatter: '.cmp_" + cc["id"] + "'}"

    def row_state_attr(self) -> str:
        rs = self.logic().get("row_state")
        if not rs:
            return ""
        return ' highlight="{parts: ' + self._parts(rs["uses"]) + ", formatter: '.cmpState'}\""

    def logic_methods(self) -> str:
        """Controller formatter methods for the composite bindings above."""
        out = []
        for cc in self.computed():
            out.append(f'        cmp_{cc["id"]}: function () {{\n'
                       f'            return this.computeValue({json.dumps(cc["id"])}, '
                       f'{json.dumps(cc["uses"] or list(self.main["key"])[:1])}, arguments, '
                       f'{json.dumps(cc["type"])}, {int(cc.get("decimals", 2))});\n        }},\n')
        rs = self.logic().get("row_state")
        if rs:
            out.append(f'        cmpState: function () {{\n'
                       f'            return this.computeState({json.dumps(rs["uses"] or list(self.main["key"])[:1])}, arguments);\n'
                       f'        }},\n')
        return "".join(out)

    def k(self, key: str) -> str:
        """Page-scoped i18n key (matches BaseController.textKey)."""
        return f"{self.pid}_{key}" if self.pid else key

    # ── i18n helpers ────────────────────────────────────────────────────────
    def lbl(self, field: str, es_name: str = None) -> str:
        key = i18n_key("fld", es_name or self.main["entity_set"], field)
        self.ctx.t(key, self.ctx.label(field, es_name))
        return "{i18n>" + key + "}"

    def txt(self, key: str, text: str) -> str:
        self.ctx.t(key, text)
        return "{i18n>" + key + "}"


    def link_infobar(self) -> str:
        """Info bar of a list that was opened from a link on another page."""
        if not getattr(self, "link_target", False):
            return ""
        self.ctx.t("linkFilter", "Showing records where {0} = {1}")
        self.ctx.t("clearFilter", "Show all")
        return ('                <infoToolbar>\n'
                '                    <OverflowToolbar design="Info" visible="{= !!${viewModel>/linkFilter} }">\n'
                '                        <Text text="{viewModel>/linkFilter}"/>\n'
                '                        <ToolbarSpacer/>\n'
                '                        <Button text="{i18n>clearFilter}" icon="sap-icon://decline" type="Transparent" '
                'press=".onClearLinkFilter"/>\n'
                '                    </OverflowToolbar>\n'
                '                </infoToolbar>\n')

    def link_header(self) -> str:
        """Header fields that open the linked record of another area (cross links)."""
        out = ""
        for i, l in enumerate(getattr(self, "cross", [])):
            if l["entitySetFrom"] != self.main["entity_set"]:
                continue                       # child-table links are rendered in the table rows
            p = self.ctx.prop(l["field"])
            out += ('                <VBox class="sapUiMediumMarginEnd sapUiSmallMarginBottom">\n'
                    f'                    <Label text={xattr(self.txt(self.k(f"lnk{i}"), l["label"]))}/>\n'
                    f'                    <Link text={xattr(_binding(self.ctx, p))} press=".onCrossLink" '
                    f'enabled="{{= !!${{{l["field"]}}} }}">\n'
                    f'                        <customData><core:CustomData key="link" value="{i}"/></customData>\n'
                    '                    </Link>\n'
                    '                </VBox>\n')
        return out

    def related_sections(self) -> str:
        """Records of other entity sets/services that point to this record."""
        out = ""
        ctx = self.ctx
        for r in getattr(self, "related", []):
            fes, m = r["entity_set"], r["model"]
            cols = "".join(f'                                    <Column><Text text={xattr(self.lbl(c, fes))}/></Column>\n'
                           for c in r["columns"])
            cells = "".join(self.display(c, fes, indent="                                            ", model=m)
                            for c in r["columns"])
            out += (f'            <uxap:ObjectPageSection title={xattr(self.txt(self.k(r["id"] + "Title"), r["title"]))} '
                    'titleUppercase="false">\n'
                    '                <uxap:subSections>\n'
                    '                    <uxap:ObjectPageSubSection>\n'
                    '                        <uxap:blocks>\n'
                    f'                            <Table id="{r["id"]}" growing="true" growingThreshold="10" '
                    'noDataText="{i18n>noData}" popinLayout="GridSmall" itemPress=".onRelatedPress">\n'
                    f'                                <customData><core:CustomData key="route" value="{r["route"]}"/>'
                    f'<core:CustomData key="model" value="{m}"/></customData>\n'
                    '                                <columns>\n' + cols +
                    '                                </columns>\n'
                    '                                <items>\n'
                    f'                                    <ColumnListItem id="{r["id"]}Tpl" '
                    f'type="{"Navigation" if r["route"] else "Inactive"}">\n'
                    '                                        <cells>\n' + cells +
                    '                                        </cells>\n'
                    '                                    </ColumnListItem>\n'
                    '                                </items>\n'
                    '                            </Table>\n'
                    '                        </uxap:blocks>\n'
                    '                    </uxap:ObjectPageSubSection>\n'
                    '                </uxap:subSections>\n'
                    '            </uxap:ObjectPageSection>\n')
        return out

    # ── field renderers ─────────────────────────────────────────────────────
    def display(self, field: str, es_name: str = None, indent: str = "", model: str = "") -> str:
        ctx = self.ctx
        p = ctx.prop(field, es_name)
        pre = model + ">" if model else ""
        b = _binding(ctx, p, model=model, rel=True) if model else _binding(ctx, p)
        es_main = (es_name or self.main["entity_set"]) == self.main["entity_set"]
        if es_main and not model and field == self.main["status"]["field"]:
            return (f'{indent}<ObjectStatus text={xattr(b)} '
                    f'state="{{path: \'{field}\', formatter: \'.formatter.statusState\'}}"/>\n')
        if p["ui_type"] == "boolean":
            return f'{indent}<Text text="{{path: \'{pre}{field}\', formatter: \'.formatter.yesNo\'}}"/>\n'
        if p["ui_type"] in ("amount", "number") and p.get("unit_property"):
            return (f'{indent}<ObjectNumber number={xattr(b)} '
                    f'unit="{{{pre}{p["unit_property"]}}}" emphasized="false"/>\n')
        return f'{indent}<Text text={xattr(b)} wrapping="true"/>\n'

    def editor(self, field: str, model: str = "", id_prefix: str = "", required: bool = False,
               indent: str = "") -> str:
        ctx = self.ctx
        p = ctx.prop(field)
        jm = bool(model)
        b = _binding(ctx, p, model, json_model=jm)
        idattr = f' id="{id_prefix}{field}"' if id_prefix else ""
        if jm:   # create dialog / wizard: clear required errors while typing
            idattr += ' change=".onFieldChange"'
        req = ' required="true"' if required else ""
        if p["ui_type"] == "boolean":
            path = (model + ">/" if model else "") + field
            return f'{indent}<CheckBox{idattr} selected="{{{path}}}"/>\n'
        if p["ui_type"] == "date":
            return f'{indent}<DatePicker{idattr} value={xattr(b)}{req}/>\n'
        if p["ui_type"] == "datetime":
            return f'{indent}<DateTimePicker{idattr} value={xattr(b)}{req}/>\n'
        extra = ""
        if field in self.vh:
            extra = (' showValueHelp="true" valueHelpRequest=".onValueHelp"'
                     f'>\n{indent}    <customData><core:CustomData key="vhField" value="{field}"/></customData>\n'
                     f'{indent}</Input>\n')
            self.txt(f"vh_{field}", self.ctx.label(field))
            return f'{indent}<Input{idattr} value={xattr(b)}{req}{extra}'
        return f'{indent}<Input{idattr} value={xattr(b)}{req}/>\n'

    # ── views ───────────────────────────────────────────────────────────────
    def filter_items(self) -> str:
        out = ""
        for f in self.main["filters"]:
            field = f["field"]
            p = self.ctx.prop(field)
            fid = f'id="filter_{field}"'
            if field == self.main["status"]["field"] and self.main["status"]["states"]:
                items = "".join(f'                                    <core:Item key={xattr(v)} '
                                f'text={xattr(v or "(blank)")}/>\n'
                                for v in self.main["status"]["states"])
                ctrl = (f'<MultiComboBox {fid}>\n'
                        f'                                    <items>\n{items}'
                        f'                                    </items>\n'
                        f'                                </MultiComboBox>')
            elif p["ui_type"] in ("date", "datetime"):
                ctrl = f'<DateRangeSelection {fid}/>'
            elif p["ui_type"] == "boolean":
                ctrl = (f'<Select {fid} forceSelection="false"><core:Item key="" text=""/>'
                        f'<core:Item key="true" text="{{i18n>yes}}"/><core:Item key="false" text="{{i18n>no}}"/></Select>')
            elif field in self.vh:
                ctrl = (f'<Input {fid} showValueHelp="true" valueHelpRequest=".onValueHelp" submit=".onSearch">'
                        f'<customData><core:CustomData key="vhField" value="{field}"/></customData></Input>')
                self.txt(f"vh_{field}", self.ctx.label(field))
            else:
                ctrl = f'<Input {fid} submit=".onSearch"' + (' type="Number"' if p["ui_type"] in ("number", "amount") else "") + '/>'
            out += (f'                        <fb:FilterGroupItem name="{field}" label={xattr(self.lbl(field))} '
                    f'groupName="__$INTERNAL$" visibleInFilterBar="true">\n'
                    f'                            <fb:control>\n'
                    f'                                {ctrl}\n'
                    f'                            </fb:control>\n'
                    f'                        </fb:FilterGroupItem>\n')
        return out

    def list_view(self) -> str:
        bp, main, ctx = self.bp, self.main, self.ctx
        s = main["entity_set"]
        worklist = bp["floorplan"] == "FreestyleWorklist"
        caps = bp["capabilities"]
        row_actions = [a for a in bp["actions"] if a["scope"] == "row"]
        multi = bool(row_actions) or caps["delete"]
        sort = main["sort"]
        params = ", parameters: {$count: true}" if ctx.v4 else ""
        items = (f"{{path: '/{s}', sorter: {{path: '{sort['field']}', descending: "
                 f"{str(sort['descending']).lower()}}}{params}}}")

        cols = cells = ""
        computed = self.computed()
        after = {}
        for cc in computed:
            after.setdefault(cc["after"] or "__end", []).append(cc)

        def computed_col(cc):
            return (f'                    <Column hAlign="{"End" if cc["type"] == "number" else "Begin"}" '
                    f'minScreenWidth="Tablet" demandPopin="true" popinDisplay="Inline">\n'
                    f'                        <Text text={xattr(self.txt(self.k("cmp_" + cc["id"]), cc["label"]))}/>\n'
                    f'                    </Column>\n',
                    f'                            <Text text={xattr(self.computed_binding(cc))}/>\n')

        for i, c in enumerate(main["columns"]):
            imp = c["importance"]
            demand = "true" if imp == "High" or i < 2 else "false"
            cols += (f'                    <Column importance="{imp}" minScreenWidth="{"" if i < 2 else "Tablet"}" '
                     f'demandPopin="{demand}" popinDisplay="Inline">\n'
                     f'                        <Text text={xattr(self.lbl(c["field"]))}/>\n'
                     f'                    </Column>\n').replace(' minScreenWidth=""', "")
            if i == 0:
                title_b = _binding(ctx, ctx.prop(c["field"]))
                desc = main["description_field"]
                cells += (f'                            <ObjectIdentifier title={xattr(title_b)}'
                          + (f' text={xattr(_binding(ctx, ctx.prop(desc)))}' if desc and desc != c["field"] else "")
                          + '/>\n')
            else:
                cells += self.display(c["field"], indent="                            ")
            for cc in after.pop(c["field"], []):
                cc_col, cc_cell = computed_col(cc)
                cols, cells = cols + cc_col, cells + cc_cell
        for group in after.values():
            for cc in group:
                cc_col, cc_cell = computed_col(cc)
                cols, cells = cols + cc_col, cells + cc_cell
        highlight = self.row_state_attr()

        tabs = ""
        if self.quick_tabs:
            sfield = main["status"]["field"]
            self.txt("tabAll", "All")
            tab_items = ('                    <IconTabFilter key="__all" text="{i18n>tabAll}" '
                     'count="{viewModel>/tabCounts/__all}" showAll="true"/>\n'
                     '                    <IconTabSeparator/>\n')
            icon_state = {"Success": "Positive", "Warning": "Critical", "Error": "Negative",
                          "Information": "Neutral", "None": "Default"}
            for v, state in main["status"]["states"].items():
                tk = self.k(i18n_key("tab", sfield, v or "blank"))
                key = v if v != "" else "__blank"
                tab_items += (f'                    <IconTabFilter key={xattr(key)} '
                          f'text={xattr(self.txt(tk, v or "(blank)"))} '
                          f'iconColor="{icon_state.get(state, "Default")}" '
                          f'count="{{viewModel>/tabCounts/{key}}}"/>\n')
            tabs = ('            <IconTabBar id="quickTabs" select=".onQuickTab" expandable="false" '
                    'headerMode="Inline" selectedKey="{viewModel>/tab}" applyContentPadding="false" '
                    'backgroundDesign="Transparent" class="sapUiNoContentPadding">\n'
                    '                <items>\n' + tab_items + '                </items>\n'
                    '            </IconTabBar>\n')
        wrap_open = '            <VBox fitContainer="true" renderType="Bare">\n' if tabs else ""
        wrap_close = '            </VBox>\n' if tabs else ""

        buttons = ""
        if caps["create"]:
            buttons += (f'                            <Button text="{self.txt("create", "Create")}" type="Emphasized" '
                        f'press=".onCreate"/>\n')
        for a in row_actions:
            buttons += (f'                            <Button text={xattr(self.txt(i18n_key("action", a["id"]), a["label"]))} '
                        f'press=".onAction" enabled="{{viewModel>/hasSelection}}">\n'
                        f'                                <customData><core:CustomData key="actionId" value="{a["id"]}"/></customData>\n'
                        f'                            </Button>\n')
        if caps["delete"]:
            buttons += (f'                            <Button icon="sap-icon://delete" tooltip="{self.txt("delete", "Delete")}" '
                        f'press=".onDeleteSelected" enabled="{{viewModel>/hasSelection}}"/>\n')
        buttons += (f'                            <Button icon="sap-icon://sort" tooltip="{self.txt("sort", "Sort")}" press=".onSort"/>\n'
                    f'                            <Button icon="sap-icon://excel-attachment" tooltip="{self.txt("export", "Export to Spreadsheet")}" press=".onExport"/>\n')

        kpis = ""
        if worklist or bp["analytics"]["kpis"]:
            kpis = ('                    <HBox wrap="Wrap" class="sapUiSmallMarginBottom">\n'
                    '                        <VBox class="sapUiMediumMarginEnd">\n'
                    f'                            <Label text="{self.txt("kpiCount", "Records")}"/>\n'
                    '                            <ObjectNumber number="{viewModel>/count}" emphasized="true" class="sapMObjectNumberLarge"/>\n'
                    '                        </VBox>\n'
                    '                        <HBox items="{viewModel>/kpis}">\n'
                    '                            <VBox class="sapUiMediumMarginEnd">\n'
                    '                                <Label text="{viewModel>label}"/>\n'
                    '                                <ObjectNumber number="{viewModel>value}" emphasized="true" class="sapMObjectNumberLarge"/>\n'
                    '                            </VBox>\n'
                    '                        </HBox>\n'
                    '                    </HBox>\n')
            if bp["analytics"]["kpis"]:
                self.txt("kpiNote", "Key figures are calculated over the records loaded in the table.")
                kpis += '                    <Text text="{i18n>kpiNote}" class="sapUiTinyMarginBottom sapThemeLightText"/>\n'

        logo = ('                        <Image src="{config>/logo}" height="2rem" '
                'class="sapUiSmallMarginEnd" alt="{i18n>appTitle}"/>\n') if ctx.logo and not self.pid else ""
        self.txt("searchPlaceholder", "Search")
        self.txt(self.k("tableTitleCount"), (ctx.es().get("label") or s) + " ({0})")
        self.txt("noData", "No records found.")
        return f'''<mvc:View
    controllerName="{ctx.app_id}.controller.{self.fid}List"
    xmlns:mvc="sap.ui.core.mvc"
    xmlns="sap.m"
    xmlns:f="sap.f"
    xmlns:fb="sap.ui.comp.filterbar"
    xmlns:core="sap.ui.core"
    displayBlock="true"
    height="100%">
    <f:DynamicPage id="listPage" headerExpanded="true" toggleHeaderOnTitleClick="true">
        <f:title>
            <f:DynamicPageTitle>
                <f:heading>
                    <HBox alignItems="Center">
{logo}                        <Title text="{{i18n>{self.k('pageTitle') if self.pid else 'appTitle'}}}" wrapping="true"/>
                    </HBox>
                </f:heading>
            </f:DynamicPageTitle>
        </f:title>
        <f:header>
            <f:DynamicPageHeader pinnable="true">
                <VBox>
{kpis}                    <fb:FilterBar id="filterBar" search=".onSearch" clear=".onFilterClear"
                        showClearOnFB="true" useToolbar="false" showFilterConfiguration="false"
                        persistencyKey="{ctx.app_id}.filterBar">
                        <fb:filterGroupItems>
{self.filter_items()}                        </fb:filterGroupItems>
                    </fb:FilterBar>
                </VBox>
            </f:DynamicPageHeader>
        </f:header>
        <f:content>
{wrap_open}{tabs}            <Table id="table"
                items="{items}"
                growing="true" growingThreshold="30" growingScrollToLoad="true"
                sticky="ColumnHeaders,HeaderToolbar"
                mode="{"MultiSelect" if multi else "None"}"
                selectionChange=".onSelectionChange"
                updateFinished=".onUpdateFinished"
                noDataText="{{i18n>noData}}"
                popinLayout="GridSmall">
{self.link_infobar()}                <headerToolbar>
                    <OverflowToolbar>
                        <Title text="{{viewModel>/tableTitle}}" level="H2"/>
                        <ToolbarSpacer/>
                        <SearchField id="searchField" width="16rem" search=".onSearch"
                            placeholder="{{i18n>searchPlaceholder}}"/>
{buttons}                    </OverflowToolbar>
                </headerToolbar>
                <columns>
{cols}                </columns>
                <items>
                    <ColumnListItem type="{"Navigation" if self.has_detail else "Inactive"}" press=".onItemPress"{highlight}>
                        <cells>
{cells}                        </cells>
                    </ColumnListItem>
                </items>
            </Table>
{wrap_close}        </f:content>
    </f:DynamicPage>
</mvc:View>
'''

    def object_view(self, embedded: bool = False) -> str:
        """Object page view; embedded=True returns only the ObjectPageLayout for
        the split layout (actions via .onDetailAction, no breadcrumbs)."""
        bp, main, ctx = self.bp, self.main, self.ctx
        caps = bp["capabilities"]
        fcl = bp["floorplan"] == "FreestyleMasterDetail" and not embedded
        act_handler = ".onDetailAction" if embedded else ".onAction"
        readonly = set(bp["form"]["readonly"]) | set(main["key"])
        # split layout: the selected record IS the row, so row actions go in its header too
        obj_actions = [a for a in bp["actions"] if a["scope"] in ("object", "global")
                       or (embedded and a["scope"] == "row")]

        actions = ""
        if caps["update"]:
            actions += (f'                    <Button text="{self.txt("edit", "Edit")}" type="Emphasized" press=".onEdit" '
                        'visible="{= !${viewModel>/editMode} }"/>\n'
                        f'                    <Button text="{self.txt("save", "Save")}" type="Emphasized" press=".onSave" '
                        'visible="{viewModel>/editMode}"/>\n'
                        f'                    <Button text="{self.txt("cancel", "Cancel")}" press=".onCancel" '
                        'visible="{viewModel>/editMode}"/>\n')
        for a in obj_actions:
            ew = a.get("enabled_when") or {}
            enabled = ""
            if ew:
                vals = ",".join("'" + v.replace("'", "") + "'" for v in ew["values"])
                enabled = f' enabled="{{= [{vals}].indexOf(${{{ew["field"]}}}) >= 0 }}"'
            actions += (f'                    <Button text={xattr(self.txt(i18n_key("action", a["id"]), a["label"]))} '
                        f'press="{act_handler}" ' + 'visible="{= !${viewModel>/editMode} }"' + enabled + '>\n'
                        f'                        <customData><core:CustomData key="actionId" value="{a["id"]}"/></customData>\n'
                        '                    </Button>\n')
        if caps["delete"]:
            actions += (f'                    <Button text="{self.txt("delete", "Delete")}" press=".onDelete" '
                        'visible="{= !${viewModel>/editMode} }"/>\n')
        nav_actions = ""
        breadcrumbs = ""
        if fcl:
            nav_actions = ('                <uxap:navigationActions>\n'
                           '                    <OverflowToolbarButton type="Transparent" '
                           'icon="{= ${viewModel>/fullScreen} ? \'sap-icon://exit-full-screen\' : \'sap-icon://full-screen\' }" '
                           'press=".onToggleFullScreen" tooltip="{i18n>fullScreen}"/>\n'
                           '                    <OverflowToolbarButton type="Transparent" icon="sap-icon://decline" '
                           'press=".onClose" tooltip="{i18n>close}"/>\n'
                           '                </uxap:navigationActions>\n')
            self.txt("fullScreen", "Full screen")
            self.txt("close", "Close")
        elif not embedded:
            breadcrumbs = ('                <uxap:breadcrumbs>\n'
                           '                    <Breadcrumbs>\n'
                           '                        <Link text="{i18n>' + self.back_key + '}" press=".onNavBack"/>\n'
                           '                    </Breadcrumbs>\n'
                           '                </uxap:breadcrumbs>\n')

        header = ""
        for f in bp["object_page"]["header_fields"]:
            header += ('                <VBox class="sapUiMediumMarginEnd sapUiSmallMarginBottom">\n'
                       f'                    <Label text={xattr(self.lbl(f))}/>\n'
                       + self.display(f, indent="                    ") +
                       '                </VBox>\n')
        for cc in self.computed():
            header += ('                <VBox class="sapUiMediumMarginEnd sapUiSmallMarginBottom">\n'
                       f'                    <Label text={xattr(self.txt(self.k("cmp_" + cc["id"]), cc["label"]))}/>\n'
                       f'                    <Text text={xattr(self.computed_binding(cc))}/>\n'
                       '                </VBox>\n')

        if not embedded:
            header += self.link_header()
        sections = ""
        for i, s in enumerate(bp["object_page"]["sections"], 1):
            rows = ""
            for f in s["fields"]:
                rows += f'                                    <Label text={xattr(self.lbl(f))}/>\n'
                if caps["update"] and f not in readonly:
                    # Display + edit control as sibling fields of one label
                    # (SimpleForm/ColumnLayout only accepts field controls, not
                    # layout containers like VBox), toggled by edit mode.
                    ind = "                                    "
                    rows += (self.display(f, indent=ind).replace(
                                 "/>\n", ' visible="{= !${viewModel>/editMode} }"/>\n', 1)
                             + self.editor(f, indent=ind).replace(
                                 "<Input ", '<Input visible="{viewModel>/editMode}" ', 1)
                             .replace("<DatePicker ", '<DatePicker visible="{viewModel>/editMode}" ', 1)
                             .replace("<DateTimePicker ", '<DateTimePicker visible="{viewModel>/editMode}" ', 1)
                             .replace("<CheckBox ", '<CheckBox visible="{viewModel>/editMode}" ', 1))
                else:
                    rows += self.display(f, indent="                                    ")
            sections += (f'            <uxap:ObjectPageSection title={xattr(self.txt(self.k(i18n_key("section", str(i))), s["title"]))} titleUppercase="false">\n'
                         '                <uxap:subSections>\n'
                         '                    <uxap:ObjectPageSubSection>\n'
                         '                        <uxap:blocks>\n'
                         '                            <form:SimpleForm editable="{viewModel>/editMode}" layout="ColumnLayout"\n'
                         '                                columnsM="2" columnsL="3" columnsXL="4">\n'
                         '                                <form:content>\n'
                         + rows +
                         '                                </form:content>\n'
                         '                            </form:SimpleForm>\n'
                         '                        </uxap:blocks>\n'
                         '                    </uxap:ObjectPageSubSection>\n'
                         '                </uxap:subSections>\n'
                         '            </uxap:ObjectPageSection>\n')
        for t in bp["object_page"]["tables"]:
            tcols = "".join(f'                                        <Column><Text text={xattr(self.lbl(c, t["entity_set"]))}/></Column>\n'
                            for c in t["columns"])
            tlinks = {l["field"]: i for i, l in enumerate(getattr(self, "cross", []))
                      if l["entitySetFrom"] == t["entity_set"]}
            tcells = "".join(
                (f'                                                <Link text={xattr(_binding(ctx, ctx.prop(c, t["entity_set"])))} '
                 f'press=".onCrossLink"><customData><core:CustomData key="link" value="{tlinks[c]}"/></customData></Link>\n')
                if c in tlinks else self.display(c, t["entity_set"], indent="                                                ")
                for c in t["columns"])
            params = ", parameters: {$count: true}" if ctx.v4 else ""
            sections += (f'            <uxap:ObjectPageSection title={xattr(self.txt(self.k(i18n_key("table", t["navigation"])), t["title"]))} titleUppercase="false">\n'
                         '                <uxap:subSections>\n'
                         '                    <uxap:ObjectPageSubSection>\n'
                         '                        <uxap:blocks>\n'
                         f'                            <Table items="{{path: \'{t["navigation"]}\'{params}}}" growing="true" growingThreshold="20"\n'
                         '                                noDataText="{i18n>noData}" popinLayout="GridSmall">\n'
                         '                                <columns>\n' + tcols +
                         '                                </columns>\n'
                         '                                <items>\n'
                         '                                    <ColumnListItem>\n'
                         '                                        <cells>\n' + tcells +
                         '                                        </cells>\n'
                         '                                    </ColumnListItem>\n'
                         '                                </items>\n'
                         '                            </Table>\n'
                         '                        </uxap:blocks>\n'
                         '                    </uxap:ObjectPageSubSection>\n'
                         '                </uxap:subSections>\n'
                         '            </uxap:ObjectPageSection>\n')

        if not embedded:
            sections += self.related_sections()
        title_b = _binding(ctx, ctx.prop(main["title_field"]))
        desc = main["description_field"]
        sub = (f'                <uxap:expandedContent>\n                    <Text text={xattr(_binding(ctx, ctx.prop(desc)))}/>\n'
               f'                </uxap:expandedContent>\n') if desc else ""
        visible = ' visible="{viewModel>/selected}"' if embedded else ""
        layout = f'''    <uxap:ObjectPageLayout id="objectPage" showTitleInHeaderContent="true" upperCaseAnchorBar="false"
        headerContentPinnable="true" busy="{{viewModel>/busy}}"{visible}>
        <uxap:headerTitle>
            <uxap:ObjectPageDynamicHeaderTitle>
{breadcrumbs}                <uxap:expandedHeading>
                    <Title text={xattr(title_b)} wrapping="true"/>
                </uxap:expandedHeading>
                <uxap:snappedHeading>
                    <Title text={xattr(title_b)}/>
                </uxap:snappedHeading>
{sub}                <uxap:actions>
{actions}                </uxap:actions>
{nav_actions}            </uxap:ObjectPageDynamicHeaderTitle>
        </uxap:headerTitle>
        <uxap:headerContent>
            <FlexBox wrap="Wrap">
{header}            </FlexBox>
        </uxap:headerContent>
        <uxap:sections>
{sections}        </uxap:sections>
    </uxap:ObjectPageLayout>
'''
        if embedded:
            return layout
        return f'''<mvc:View
    controllerName="{ctx.app_id}.controller.{self.fid}Object"
    xmlns:mvc="sap.ui.core.mvc"
    xmlns="sap.m"
    xmlns:uxap="sap.uxap"
    xmlns:form="sap.ui.layout.form"
    xmlns:core="sap.ui.core"
    displayBlock="true"
    height="100%">
{layout}</mvc:View>
'''

    # ── complete create form ────────────────────────────────────────────────
    def create_groups(self) -> List[Dict]:
        form = self.bp["form"]
        return form.get("create_groups") or [{"title": "", "fields": form["create_fields"]}]

    def create_form(self, groups: List[Dict], indent: str, two_columns: bool) -> str:
        req = set(self.bp["form"]["required"])
        rows = ""
        for i, g in enumerate(groups):
            if g["title"] and len(groups) > 1:
                rows += f'{indent}    <core:Title text={xattr(self.txt(self.k(i18n_key("cgroup", str(i + 1))), g["title"]))}/>\n'
            for f in g["fields"]:
                rows += (f'{indent}    <Label text={xattr(self.lbl(f))}' + (' required="true"' if f in req else "") + '/>\n'
                         + self.editor(f, model="create", id_prefix="create_", required=f in req, indent=indent + "    "))
        cols = 'columnsXL="2" columnsL="2" columnsM="1"' if two_columns else 'columnsL="1" columnsM="1"'
        return (f'{indent}<form:SimpleForm editable="true" layout="ResponsiveGridLayout" labelSpanXL="4" labelSpanL="4" '
                f'labelSpanM="4" emptySpanL="0" {cols}>\n{indent}    <form:content>\n'
                + rows + f'{indent}    </form:content>\n{indent}</form:SimpleForm>\n')

    def child_editor(self, es_name: str, field: str, required: bool) -> str:
        p = self.ctx.prop(field, es_name)
        b = _binding(self.ctx, p, "create", json_model=True, rel=True)
        req = ' required="true"' if required else ""
        if p["ui_type"] == "boolean":
            return f'<CheckBox selected="{{create>{field}}}"/>'
        if p["ui_type"] == "date":
            return f'<DatePicker value={xattr(b)}{req}/>'
        if p["ui_type"] == "datetime":
            return f'<DateTimePicker value={xattr(b)}{req}/>'
        return f'<Input value={xattr(b)}{req}/>'

    def child_tables(self, indent: str, children: List[Dict] = None) -> str:
        out = ""
        self.txt("addRow", "Add")
        self.txt("removeRow", "Remove")
        self.txt("noRows", "No entries yet - choose Add.")
        for c in (self.bp["form"].get("create_children") or []) if children is None else children:
            tkey = self.k(i18n_key("child", c["navigation"]))
            self.txt(tkey, c["title"])
            req = set(c["required"])
            cols = "".join(f'{indent}        <Column minScreenWidth="Tablet" demandPopin="true"><Label text='
                           f'{xattr(self.lbl(f, c["entity_set"]))}' + (' required="true"' if f in req else "") + '/></Column>\n'
                           for f in c["fields"])
            cells = "".join(f'{indent}                {self.child_editor(c["entity_set"], f, f in req)}\n' for f in c["fields"])
            nav = c["navigation"]
            out += (f'{indent}<Table items="{{create>/{nav}}}" noDataText="{{i18n>noRows}}" class="sapUiSmallMarginTop"\n'
                    f'{indent}    popinLayout="GridSmall">\n'
                    f'{indent}    <headerToolbar><OverflowToolbar>\n'
                    f'{indent}        <Title text="{{i18n>{tkey}}}" level="H3"/>\n'
                    f'{indent}        <ToolbarSpacer/>\n'
                    f'{indent}        <Button icon="sap-icon://add" text="{{i18n>addRow}}" press=".onAddChildRow">\n'
                    f'{indent}            <customData><core:CustomData key="nav" value="{nav}"/></customData>\n'
                    f'{indent}        </Button>\n'
                    f'{indent}    </OverflowToolbar></headerToolbar>\n'
                    f'{indent}    <columns>\n{cols}{indent}        <Column width="3rem" hAlign="End"/>\n{indent}    </columns>\n'
                    f'{indent}    <items>\n{indent}        <ColumnListItem>\n{indent}            <cells>\n{cells}'
                    f'{indent}                <Button icon="sap-icon://delete" type="Transparent" tooltip="{{i18n>removeRow}}" press=".onRemoveChildRow">\n'
                    f'{indent}                    <customData><core:CustomData key="nav" value="{nav}"/></customData>\n'
                    f'{indent}                </Button>\n'
                    f'{indent}            </cells>\n{indent}        </ColumnListItem>\n{indent}    </items>\n{indent}</Table>\n')
        return out

    def create_dialog(self) -> str:
        bp = self.bp
        groups = self.create_groups()
        children = bp["form"].get("create_children") or []
        n_fields = len(bp["form"]["create_fields"])
        wide = bool(children) or n_fields > 10
        body = self.create_form(groups, "            ", two_columns=n_fields > 10) + self.child_tables("            ")
        width = "70rem" if wide else "40rem"
        height = ' contentHeight="75vh"' if wide else ""
        ckey = self.k("createTitle")
        self.txt(ckey, f"New {self.ctx.es().get('label') or self.main['entity_set']}")
        self.txt("save", "Save")
        self.txt("cancel", "Cancel")
        return f'''<core:FragmentDefinition
    xmlns="sap.m"
    xmlns:core="sap.ui.core"
    xmlns:form="sap.ui.layout.form">
    <Dialog id="createDialog" title="{{i18n>{ckey}}}" contentWidth="{width}"{height}
        resizable="true" draggable="true" stretch="{{device>/system/phone}}">
        <content>
{body}        </content>
        <beginButton>
            <Button text="{{i18n>save}}" type="Emphasized" press=".onCreateSave"/>
        </beginButton>
        <endButton>
            <Button text="{{i18n>cancel}}" press=".onCreateCancel"/>
        </endButton>
    </Dialog>
</core:FragmentDefinition>
'''

    def sort_dialog(self) -> str:
        items = "".join(f'            <ViewSettingsItem key="{c["field"]}" text={xattr(self.lbl(c["field"]))}'
                        + (' selected="true"' if c["field"] == self.main["sort"]["field"] else "") + '/>\n'
                        for c in self.main["columns"] if self.ctx.prop(c["field"]).get("sortable", True))
        return f'''<core:FragmentDefinition xmlns="sap.m" xmlns:core="sap.ui.core">
    <ViewSettingsDialog id="sortDialog" confirm=".onSortConfirm"
        sortDescending="{str(self.main['sort']['descending']).lower()}">
        <sortItems>
{items}        </sortItems>
    </ViewSettingsDialog>
</core:FragmentDefinition>
'''

    def split_view(self, title_key: str) -> str:
        """List (left) + selected record (right) on one page."""
        bp, main, ctx = self.bp, self.main, self.ctx
        s = main["entity_set"]
        sort = main["sort"]
        params = ", parameters: {$count: true}" if ctx.v4 else ""
        items = (f"{{path: '/{s}', sorter: {{path: '{sort['field']}', descending: "
                 f"{str(sort['descending']).lower()}}}{params}}}")
        title_b = _binding(ctx, ctx.prop(main["title_field"]))
        cols = [c["field"] for c in main["columns"] if c["field"] != main["title_field"]]
        num = next((f for f in cols if ctx.prop(f)["ui_type"] in ("amount", "number")), "")
        attrs = [f for f in cols if f not in (num, main["status"]["field"])][:2]
        number = ""
        if num:
            p = ctx.prop(num)
            number = f' number={xattr(_binding(ctx, p))}' + (f' numberUnit="{{{p["unit_property"]}}}"'
                                                              if p.get("unit_property") else "")
        attr_xml = "".join(f'                                <ObjectAttribute title={xattr(self.lbl(f))} '
                           f'text={xattr(_binding(ctx, ctx.prop(f)))}/>\n' for f in attrs)
        status = ""
        if main["status"]["field"]:
            sf = main["status"]["field"]
            status = (f'                            <firstStatus><ObjectStatus text={xattr(_binding(ctx, ctx.prop(sf)))} '
                      f'state="{{path: \'{sf}\', formatter: \'.formatter.statusState\'}}"/></firstStatus>\n')
        highlight = self.row_state_attr()
        create = ""
        if bp["capabilities"]["create"]:
            create = (f'                    <Button icon="sap-icon://add" tooltip="{self.txt("create", "Create")}" '
                      'type="Transparent" press=".onCreate"/>\n')
        self.txt("splitSelect", "Select a record on the left to see its details.")
        self.txt("searchPlaceholder", "Search")
        self.txt("noData", "No records found.")
        self.txt(self.k("tableTitleCount"), (ctx.es().get("label") or s) + " ({0})")
        return f'''<mvc:View
    controllerName="{ctx.app_id}.controller.{self.fid}Split"
    xmlns:mvc="sap.ui.core.mvc"
    xmlns="sap.m"
    xmlns:l="sap.ui.layout"
    xmlns:uxap="sap.uxap"
    xmlns:form="sap.ui.layout.form"
    xmlns:core="sap.ui.core"
    displayBlock="true"
    height="100%">
    <l:Splitter id="splitter" height="100%" width="100%">
        <Page id="listPane" showHeader="false" enableScrolling="true">
            <layoutData><l:SplitterLayoutData size="24rem" minSize="280"/></layoutData>
            <subHeader>
                <OverflowToolbar>
                    <SearchField id="searchField" width="100%" search=".onSearch"
                        placeholder="{{i18n>searchPlaceholder}}">
                        <layoutData><OverflowToolbarLayoutData shrinkable="true" priority="NeverOverflow"/></layoutData>
                    </SearchField>
{create}                    <Button icon="sap-icon://sort" tooltip="{self.txt("sort", "Sort")}" type="Transparent" press=".onSort"/>
                </OverflowToolbar>
            </subHeader>
            <content>
                <Title text="{{viewModel>/tableTitle}}" level="H2" class="sapUiSmallMargin"/>
                <List id="table" items="{items}" mode="SingleSelectMaster"
                    growing="true" growingThreshold="40" growingScrollToLoad="true"
                    selectionChange=".onSelectionChange" updateFinished=".onUpdateFinished"
                    noDataText="{{i18n>noData}}">
{self.link_infobar()}                    <items>
                        <ObjectListItem title={xattr(title_b)}{number} type="Active"{highlight}>
                            <attributes>
{attr_xml}                            </attributes>
{status}                        </ObjectListItem>
                    </items>
                </List>
            </content>
        </Page>
        <VBox id="detail" height="100%" fitContainer="true" renderType="Bare">
            <layoutData><l:SplitterLayoutData size="auto"/></layoutData>
            <IllustratedMessage illustrationType="sapIllus-NoEntries" title="{{i18n>splitSelect}}"
                visible="{{= !${{viewModel>/selected}} }}"/>
{self.object_view(embedded=True)}        </VBox>
    </l:Splitter>
</mvc:View>
'''

    def app_view(self) -> str:
        if self.bp["floorplan"] == "FreestyleMasterDetail":
            body = '    <f:FlexibleColumnLayout id="fcl" layout="{appView>/layout}" backgroundDesign="Translucent"/>\n'
        else:
            body = '    <App id="app"/>\n'
        return (f'<mvc:View controllerName="{self.ctx.app_id}.controller.App" xmlns:mvc="sap.ui.core.mvc"\n'
                '    xmlns="sap.m" xmlns:f="sap.f" displayBlock="true" height="100%">\n'
                + body + '</mvc:View>\n')

    # ── config + manifest ───────────────────────────────────────────────────
    def config(self) -> Dict:
        ctx, bp, main = self.ctx, self.bp, self.main
        es = ctx.es()

        def col(f):
            p = ctx.prop(f)
            return {"field": f, "label": ctx.label(f), "uiType": p["ui_type"],
                    "scale": p.get("scale") or 0}

        filters = []
        for f in main["filters"]:
            p = ctx.prop(f["field"])
            if f["field"] == main["status"]["field"] and main["status"]["states"]:
                mode = "multi"
            elif p["ui_type"] in ("date", "datetime"):
                mode = "range"
            elif p["ui_type"] == "text" and f["field"] not in self.vh and \
                    (p.get("max_length") or 99) > 4 and p.get("type") == "Edm.String":
                mode = "contains"
            else:
                mode = "eq"
            filters.append({"field": f["field"], "uiType": p["ui_type"], "type": p["type"], "mode": mode})

        search = [f for f in [main["title_field"], main["description_field"]] +
                  [c["field"] for c in main["columns"]] + list(main["key"])
                  if f and ctx.prop(f).get("type") == "Edm.String" and ctx.prop(f).get("filterable", True)]
        search = list(dict.fromkeys(search))
        # Free-text fields first (customer reference, names, descriptions), then
        # keys/IDs; 1-5 char codes (currency, status) are what the filter bar is for.
        def _rank(f):
            ml = ctx.prop(f).get("max_length") or 40
            return (0 if ml > 5 else 1, 0 if f in (main["title_field"], main["description_field"]) else 1, -ml)
        search = sorted(search, key=_rank)[:8]

        actions = []
        for a in bp["actions"]:
            out = {"id": a["id"], "label": a["label"], "kind": a["kind"], "scope": a["scope"],
                   "confirm": a["confirm"], "enabledWhen": a.get("enabled_when") or {}}
            if out["enabledWhen"]:
                out["enabledWhen"]["label"] = ctx.label(out["enabledWhen"]["field"])
            if a["kind"] == "set_field":
                out.update({"field": a["field"], "value": a["value"]})
            elif a["kind"] == "script":
                out.update({"calls": a.get("calls", []), "sets": a.get("sets", [])})
            else:
                op = ctx.op(a["operation"])
                props = {p["name"] for p in es["properties"]}
                key_params = [p["name"] for p in op["parameters"] if p["name"] in props]
                out["operation"] = {
                    "name": op["name"], "bound": bool(op.get("bound_type")) and ctx.v4,
                    "importName": op.get("import_name", ""), "namespace": op.get("namespace") or ctx.ns,
                    "method": op.get("http_method") or "POST", "keyParams": key_params,
                    "askParams": [{"name": p["name"], "label": p.get("label") or p["name"],
                                   "nullable": p.get("nullable", True),
                                   "numeric": p["ui_type"] in ("number", "amount")}
                                  for p in op["parameters"] if p["name"] not in key_params],
                }
                if out["operation"]["askParams"] or out["scope"] == "global":
                    pass
                if not ctx.v4 and not key_params and not op.get("bound_type"):
                    out["scope"] = "global"
            actions.append(out)

        # operations a custom script may call (api.call)
        operations = {}
        for name in sorted({c for a in bp["actions"] if a["kind"] == "script" for c in a.get("calls", [])}):
            op = ctx.op(name)
            eprops = {p["name"] for p in es["properties"]}
            operations[name] = {"name": op["name"], "bound": bool(op.get("bound_type")) and ctx.v4,
                                "importName": op.get("import_name", ""), "namespace": op.get("namespace") or ctx.ns,
                                "method": op.get("http_method") or "POST",
                                "keyParams": [p["name"] for p in op["parameters"] if p["name"] in eprops]}
        quick = None
        if self.quick_tabs and main["status"]["field"]:
            quick = {"field": main["status"]["field"], "values": list(main["status"]["states"])}

        return {
            "odataVersion": ctx.model["odata_version"],
            "logicPage": self.logic_page,
            "edmTypes": {p["name"]: p.get("type", "Edm.String") for p in es["properties"]},
            "operations": operations,
            "quickTabs": quick,
            "entitySet": main["entity_set"],
            "keys": main["key"],
            "titleField": main["title_field"],
            "searchFields": search,
            "filters": filters,
            "columns": [col(c["field"]) for c in main["columns"]],
            "createFields": [{"field": f, "label": ctx.label(f), "required": f in bp["form"]["required"],
                              "default": False if ctx.prop(f)["ui_type"] == "boolean" else None}
                             for f in bp["form"]["create_fields"]],
            "createChildren": [{"nav": c["navigation"], "entitySet": c["entity_set"], "autoKeys": c["auto_keys"],
                                "title": c["title"],
                                "fields": [{"field": f, "label": ctx.label(f, c["entity_set"]),
                                            "required": f in c["required"],
                                            "default": False if ctx.prop(f, c["entity_set"])["ui_type"] == "boolean" else None}
                                           for f in c["fields"]]}
                               for c in bp["form"].get("create_children") or []],
            "valueHelps": {v["field"]: {"entitySet": v["entity_set"], "key": v["key"], "text": v["text"]}
                           for v in bp["value_helps"]},
            "actions": actions,
            "kpis": bp["analytics"]["kpis"],
            "capabilities": bp["capabilities"],
            "exportFileName": main["entity_set"] + ".xlsx",
            "logo": ctx.logo or "",
        }

    def manifest(self) -> Dict:
        ctx, bp = self.ctx, self.bp
        fcl = bp["floorplan"] == "FreestyleMasterDetail"
        if fcl:
            routing = {
                "config": {"routerClass": "sap.f.routing.Router", "type": "View", "viewType": "XML",
                           "path": f"{ctx.app_id}.view", "controlId": "fcl", "async": True},
                "routes": [{"pattern": ":?query:", "name": "list", "target": ["list"]},
                           {"pattern": "object/{key}", "name": "object", "target": ["list", "object"]}],
                "targets": {"list": {"name": "List", "id": "list", "controlAggregation": "beginColumnPages"},
                            "object": {"name": "Object", "id": "object", "controlAggregation": "midColumnPages"}},
            }
        else:
            routing = {
                "config": {"routerClass": "sap.m.routing.Router", "type": "View", "viewType": "XML",
                           "path": f"{ctx.app_id}.view", "controlId": "app",
                           "controlAggregation": "pages", "async": True},
                "routes": [{"pattern": ":?query:", "name": "list", "target": ["list"]},
                           {"pattern": "object/{key}", "name": "object", "target": ["object"]}],
                "targets": {"list": {"name": "List", "id": "list", "level": 1},
                            "object": {"name": "Object", "id": "object", "level": 2}},
            }
        model_settings = ({"operationMode": "Server", "autoExpandSelect": True, "earlyRequests": True,
                           "groupId": "$auto", "updateGroupId": "$auto"} if ctx.v4 else
                          {"defaultBindingMode": "TwoWay", "defaultCountMode": "Inline",
                           "useBatch": True, "refreshAfterChange": True})
        sem = "".join(c for c in ctx.es()["entity_type"] if c.isalnum()) or "App"
        return {
            "_version": "1.59.0",
            "sap.app": {
                "id": ctx.app_id, "type": "application", "i18n": sap_app_i18n(ctx),
                "title": "{{appTitle}}",
                "description": "{{appDescription}}", "applicationVersion": {"version": "1.0.0"},
                "dataSources": {"mainService": {
                    "uri": ctx.service_path, "type": "OData",
                    "settings": {"odataVersion": "4.0" if ctx.v4 else "2.0",
                                 "localUri": "localService/metadata.xml"}}},
                "crossNavigation": {"inbounds": {f"{sem}-display": {
                    "semanticObject": sem, "action": "display", "title": "{{appTitle}}",
                    "signature": {"parameters": {}, "additionalParameters": "allowed"}}}},
            },
            "sap.ui": {"technology": "UI5", "deviceTypes": {"desktop": True, "tablet": True, "phone": True}},
            "sap.ui5": {
                "rootView": {"viewName": f"{ctx.app_id}.view.App", "type": "XML", "id": "appView"},
                "dependencies": {"minUI5Version": "1.120.0", "libs": {
                    "sap.m": {}, "sap.ui.core": {}, "sap.f": {}, "sap.uxap": {}, "sap.ui.layout": {},
                    "sap.ui.comp": {}, "sap.ui.export": {"lazy": True}}},
                "handleValidation": True,
                "contentDensities": {"compact": True, "cozy": True},
                "models": {
                    "i18n": i18n_model(ctx),
                    "": {"dataSource": "mainService", "preload": True, "settings": model_settings},
                },
                "routing": routing,
            },
        }


def render(ctx: RenderContext) -> Dict[str, str]:
    fs = _Fs(ctx)
    bp = ctx.bp
    ctx.t("appTitle", bp["app"]["title"])
    ctx.t("appDescription", bp["app"]["description"] or bp["app"]["title"])
    for k, v in {"ok": "OK", "cancel": "Cancel", "yes": "Yes", "no": "No",
                 "created": "Record created.", "saved": "Changes saved.", "deleted": "Deleted.",
                 "deleteConfirm": "Delete {0} record(s)?", "noSelection": "Select at least one record first.",
                 "actionDone": "{0}: done.", "actionConfirm": "{0} - apply to {1} record(s)?",
                 "fixFields": "Please fill in or correct: {0}", "noData": "No records found.",
                 "actionNotAllowed": "{0} is only possible when {1} is {2}. {3} of the {4} selected record(s) "
                                     "are in a different state.",
                 "dumpKnown": "{0} already failed in SAP for this record with a system error caused by its data "
                              "(see the previous message). It is not sent again — please ask your SAP "
                              "administrator to repair the record first."}.items():
        ctx.t(k, v)

    files = {"model/logic.js": _logic_files(ctx, {"main": bp.get("logic") or {}},
                                            {"main": bp["main"]["entity_set"]},
                                            _scripts_of(bp["actions"], bp["main"]["entity_set"]), ctx.notes)}
    files.update({
        "view/App.view.xml": fs.app_view(),
        "view/List.view.xml": fs.list_view(),
        "view/Object.view.xml": fs.object_view(),
        "fragment/SortDialog.fragment.xml": fs.sort_dialog(),
    })
    if bp["capabilities"]["create"]:
        files["fragment/CreateDialog.fragment.xml"] = fs.create_dialog()
    cfg = fs.config()
    files["model/appConfig.js"] = ('sap.ui.define([], function () {\n    "use strict";\n'
                                   '    // Generated from the app blueprint — the generic controllers read this.\n'
                                   f'    return {json.dumps(cfg, indent=4)};\n}});\n')
    methods = fs.logic_methods()
    sub = lambda s: (s.replace("@@APP@@", ctx.app_id).replace("@@APPPATH@@", ctx.app_path)
                     .replace("/*@@LOGIC_METHODS@@*/\n", methods))
    files["Component.js"] = sub(js.COMPONENT)
    files["model/DataService.js"] = sub(js.DATA_SERVICE)
    files["model/formatter.js"] = js.FORMATTER.replace("@@STATES@@", json.dumps(bp["main"]["status"]["states"]))
    files["controller/BaseController.js"] = sub(js.BASE_CONTROLLER)
    files["controller/App.controller.js"] = sub(js.APP_CONTROLLER)
    files["controller/List.controller.js"] = sub(js.LIST_CONTROLLER)
    files["controller/Object.controller.js"] = sub(js.OBJECT_CONTROLLER)
    files["manifest.json"] = json.dumps(fs.manifest(), indent=4)
    files["index.html"] = index_html(ctx, "sap.m,sap.f,sap.uxap")
    return files


# ═════════════════════════════════════════════════════════════════════════════
# Multi-page apps (floorplan "FreestyleApp")
# ═════════════════════════════════════════════════════════════════════════════
import copy as _copy


def _fid(pid: str) -> str:
    return pid[:1].upper() + pid[1:]


def _page_ctx(ctx: RenderContext, sub: Dict) -> RenderContext:
    """Context for one list/detail page: same model, shared i18n, page blueprint."""
    c = _copy.copy(ctx)                       # shallow: i18n dict is shared
    c.bp = dict(ctx.bp, main=sub["main"], object_page=sub["object_page"],
                capabilities=sub["capabilities"], form=sub["form"],
                value_helps=sub["value_helps"], actions=sub["actions"],
                analytics=sub["analytics"], logic=sub.get("logic") or {},
                floorplan="FreestyleListDetail")
    return c


def _page_controller(ctx: RenderContext, base: str, fid: str, kind: str, pid: str,
                     methods: str = "") -> str:
    body = f'pageId: "{pid}"' if not methods else f'\n        pageId: "{pid}",\n{methods.rstrip().rstrip(",")}\n    '
    return (f'sap.ui.define([\n    "./{base}.controller"\n], function ({base}) {{\n'
            f'    "use strict";\n'
            f'    // Page "{pid}": generic {base} logic, configured by appConfig.pages.{pid}\n'
            f'    return {base}.extend("{ctx.app_id}.controller.{fid}{kind}", {{ {body}}});\n'
            f'}});\n')


def _logic_files(ctx: RenderContext, logic_by_page: Dict[str, Dict], es_by_page: Dict[str, str],
                 scripts: Dict[str, Dict], notes: List[str]) -> str:
    """model/logic.js (+ node runtime check that drops hooks that cannot run)."""
    import logic as lg
    src = lg.module_js(logic_by_page, scripts)
    if not lg.node_available():
        return src
    for pid, block in list(logic_by_page.items()):
        if lg.is_empty(block) or pid not in es_by_page:
            continue
        es = ctx.es(es_by_page[pid])
        hooks = [(f"L.page({json.dumps(pid)}).computed[{json.dumps(c['id'])}]", "e") for c in block["computed"]]
        if block["row_state"]:
            hooks.append((f"L.page({json.dumps(pid)}).rowState", "e"))
        hooks += [(f"L.page({json.dumps(pid)}).validations[{i}].test", "e") for i in range(len(block["validations"]))]
        for kind in ("defaults", "derived"):
            hooks += [(f"L.page({json.dumps(pid)}).{kind}[{json.dumps(d['field'])}]", "e") for d in block[kind]]
        bad = lg.runtime_check(src, es, hooks)
        if bad.get("*"):
            notes.append(f"Custom logic could not be checked ({bad['*']}) — it is guarded at runtime.")
            continue
        if bad:
            for path, err in bad.items():
                notes.append(f"Custom logic hook {path} removed — {err}.")
            def keep(path):
                return path not in bad
            block["computed"] = [c for c in block["computed"]
                                 if keep(f"L.page({json.dumps(pid)}).computed[{json.dumps(c['id'])}]")]
            if block["row_state"] and not keep(f"L.page({json.dumps(pid)}).rowState"):
                block["row_state"] = None
            block["validations"] = [v for i, v in enumerate(block["validations"])
                                    if keep(f"L.page({json.dumps(pid)}).validations[{i}].test")]
            for kind in ("defaults", "derived"):
                block[kind] = [d for d in block[kind]
                               if keep(f"L.page({json.dumps(pid)}).{kind}[{json.dumps(d['field'])}]")]
    for aid, sc in list(scripts.items()):
        es = ctx.es(sc["_entity_set"])
        bad = lg.runtime_check(lg.module_js({}, {aid: sc}), es, [(f"L.script({json.dumps(aid)})", "script")])
        if bad and not bad.get("*"):
            notes.append(f"Custom action script '{sc.get('label', aid)}' has an error ({list(bad.values())[0]}) — "
                         f"it will report the error instead of running.")
    return lg.module_js(logic_by_page, scripts)


def _wizard_view(fs: "_Fs", page: Dict) -> str:
    ctx = fs.ctx
    req = set(fs.bp["form"]["required"])
    steps = ""
    for i, st in enumerate(page["wizard_steps"], 1):
        rows = ""
        for f in st["fields"]:
            rows += (f'                            <Label text={xattr(fs.lbl(f))}'
                     + (' required="true"' if f in req else "") + '/>\n'
                     + fs.editor(f, model="create", id_prefix="create_", required=f in req,
                                 indent="                            "))
        key = f"{page['wizard_id']}_step{i}"
        ctx.t(key, st["title"])
        cols = 'columnsXL="2" columnsL="2" columnsM="1"' if len(st["fields"]) > 6 else 'columnsL="1" columnsM="1"'
        steps += (f'                <WizardStep title="{{i18n>{key}}}" validated="true">\n'
                  f'                    <form:SimpleForm editable="true" layout="ResponsiveGridLayout"\n'
                  f'                        labelSpanXL="4" labelSpanL="4" labelSpanM="4" emptySpanL="0" {cols}>\n'
                  '                        <form:content>\n' + rows +
                  '                        </form:content>\n'
                  '                    </form:SimpleForm>\n'
                  '                </WizardStep>\n')
    for c in fs.bp["form"].get("create_children") or []:
        key = f"{page['wizard_id']}_child_{c['navigation']}"
        ctx.t(key, c["title"])
        steps += (f'                <WizardStep title="{{i18n>{key}}}" validated="true">\n'
                  + fs.child_tables("                    ", [c]) +
                  '                </WizardStep>\n')
    # Review step: read-only summary of everything entered
    review = ""
    for st in page["wizard_steps"]:
        for f in st["fields"]:
            review += (f'                            <Label text={xattr(fs.lbl(f))}/>\n'
                       f'                            <Text text={xattr(_binding(ctx, ctx.prop(f), "create", json_model=True))}/>\n')
    for c in fs.bp["form"].get("create_children") or []:
        review += (f'                            <Label text="{{i18n>{page["wizard_id"]}_child_{c["navigation"]}}}"/>\n'
                   f'                            <Text text="{{= ${{create>/{c["navigation"]}}}.length }}"/>\n')
    rkey = f"{page['wizard_id']}_review"
    ctx.t(rkey, "Review")
    ctx.t("reviewHint", "Check your entries, then choose Create.")
    steps += (f'                <WizardStep title="{{i18n>{rkey}}}" validated="true">\n'
              '                    <MessageStrip text="{i18n>reviewHint}" type="Information" class="sapUiSmallMarginBottom"/>\n'
              '                    <form:SimpleForm editable="false" layout="ResponsiveGridLayout"\n'
              '                        labelSpanL="3" labelSpanM="4" columnsL="2" columnsM="1">\n'
              '                        <form:content>\n' + review +
              '                        </form:content>\n'
              '                    </form:SimpleForm>\n'
              '                </WizardStep>\n')
    tkey = f"{page['wizard_id']}_pageTitle"
    ctx.t(tkey, f"New {ctx.es().get('label') or page['entity_set']}")
    ctx.t("submit", "Create")
    return f'''<mvc:View
    controllerName="{ctx.app_id}.controller.{_fid(page['id'])}Wizard"
    xmlns:mvc="sap.ui.core.mvc"
    xmlns="sap.m"
    xmlns:form="sap.ui.layout.form"
    xmlns:core="sap.ui.core"
    displayBlock="true"
    height="100%">
    <Page title="{{i18n>{tkey}}}" showNavButton="true" navButtonPress=".onCancel" showFooter="true">
        <content>
            <Wizard id="wizard" showNextButton="true" class="sapUiResponsivePadding--header sapUiResponsivePadding--content">
{steps}            </Wizard>
        </content>
        <footer>
            <OverflowToolbar>
                <ToolbarSpacer/>
                <Button text="{{i18n>submit}}" type="Emphasized" press=".onSubmit"/>
                <Button text="{{i18n>cancel}}" press=".onCancel"/>
            </OverflowToolbar>
        </footer>
    </Page>
</mvc:View>
'''


def _dashboard_view(ctx: RenderContext, fs: "_Fs", page: Dict) -> str:
    pid = page["id"]
    tkey = f"{pid}_pageTitle"
    ctx.t(tkey, page["title"])
    ctx.t("kpiSampled", "of latest records")
    ctx.t("refresh", "Refresh")
    tiles = ""
    for k in page["kpis"]:
        lkey = f"{k['id']}_label"
        ctx.t(lkey, k["label"])
        tiles += (f'                <GenericTile class="sapUiTinyMarginBegin sapUiTinyMarginTop" frameType="OneByOne"\n'
                  f'                    header="{{i18n>{lkey}}}" press=".onKpiPress"\n'
                  f'                    subheader="{{= ${{dash>/kpis/{k["id"]}/sample}} ? ${{i18n>kpiSampled}} : \'\' }}">\n'
                  f'                    <customData><core:CustomData key="route" value="{k["route"]}"/></customData>\n'
                  '                    <TileContent>\n'
                  f'                        <NumericContent value="{{dash>/kpis/{k["id"]}/value}}" '
                  f'scale="{{dash>/kpis/{k["id"]}/scale}}" withMargin="false"/>\n'
                  '                    </TileContent>\n'
                  '                </GenericTile>\n')
    cards = ""
    for c in page["charts"]:
        ckey = f"{c['id']}_title"
        ctx.t(ckey, c["title"])
        path = f"dash>/charts/{c['id']}"
        if c["type"] == "donut":
            chart = (f'<mc:InteractiveDonutChart selectionEnabled="false" displayedSegments="6" segments="{{{path}}}">\n'
                     '                            <mc:segments><mc:InteractiveDonutChartSegment label="{dash>label}" '
                     'value="{dash>value}" displayedValue="{dash>display}"/></mc:segments>\n'
                     '                        </mc:InteractiveDonutChart>')
        elif c["type"] == "column":
            # sap.suite.ui.microchart has no "InteractiveColumnChart" (404 ->
            # the whole view fails to load); ColumnMicroChart is the column chart.
            chart = (f'<mc:ColumnMicroChart width="100%" height="12rem" allowColumnLabels="true" columns="{{{path}}}">\n'
                     '                            <mc:columns><mc:ColumnMicroChartData label="{dash>label}" '
                     'value="{dash>value}" displayValue="{dash>display}" '
                     'color="sapUiChartPaletteQualitativeHue1"/></mc:columns>\n'
                     '                        </mc:ColumnMicroChart>')
        else:
            chart = (f'<mc:InteractiveBarChart selectionEnabled="false" displayedBars="8" bars="{{{path}}}">\n'
                     '                            <mc:bars><mc:InteractiveBarChartBar label="{dash>label}" '
                     'value="{dash>value}" displayedValue="{dash>display}"/></mc:bars>\n'
                     '                        </mc:InteractiveBarChart>')
        cards += (f'                <f:Card class="sapUiSmallMarginBottom" width="100%">\n'
                  f'                    <f:header><card:Header title="{{i18n>{ckey}}}" iconSrc="sap-icon://bar-chart"/></f:header>\n'
                  f'                    <f:content>\n'
                  f'                        <VBox class="sapUiSmallMargin" width="100%">\n'
                  f'                        {chart}\n'
                  f'                        </VBox>\n'
                  f'                    </f:content>\n'
                  f'                </f:Card>\n')
    for l in page["lists"]:
        lkey = f"{l['id']}_title"
        ctx.t(lkey, l["title"])
        cols = "".join(f'                                <Column><Text text={xattr(fs.lbl(f, l["entity_set"]))}/></Column>\n'
                       for f in l["fields"])
        lm = _ms.service_of(ctx.model, l["entity_set"]) if _ms.is_multi(ctx.model) else ""
        cells = "".join(fs.display(f, l["entity_set"], indent="                                        ", model=lm)
                        for f in l["fields"])
        s = l["sort"]
        cards += (f'                <f:Card class="sapUiSmallMarginBottom" width="100%">\n'
                  f'                    <f:header><card:Header title="{{i18n>{lkey}}}" iconSrc="sap-icon://list"/></f:header>\n'
                  f'                    <f:content>\n'
                  f'                        <Table width="100%" itemPress=".onRecentPress"\n'
                  f'                            items="{{path: \'{lm + ">" if lm else ""}/{l["entity_set"]}\', length: 5, '
                  f'sorter: {{path: \'{s["field"]}\', descending: {str(s["descending"]).lower()}}}}}">\n'
                  f'                            <customData><core:CustomData key="route" value="{l["route"]}"/>'
                  f'<core:CustomData key="model" value="{lm}"/></customData>\n'
                  f'                            <columns>\n{cols}                            </columns>\n'
                  f'                            <items>\n'
                  f'                                <ColumnListItem type="{"Navigation" if l["route"] else "Inactive"}">\n'
                  f'                                    <cells>\n{cells}                                    </cells>\n'
                  f'                                </ColumnListItem>\n'
                  f'                            </items>\n'
                  f'                        </Table>\n'
                  f'                    </f:content>\n'
                  f'                </f:Card>\n')
    return f'''<mvc:View
    controllerName="{ctx.app_id}.controller.{_fid(pid)}Dashboard"
    xmlns:mvc="sap.ui.core.mvc"
    xmlns="sap.m"
    xmlns:f="sap.f"
    xmlns:card="sap.f.cards"
    xmlns:l="sap.ui.layout"
    xmlns:mc="sap.suite.ui.microchart"
    xmlns:core="sap.ui.core"
    displayBlock="true"
    height="100%">
    <Page title="{{i18n>{tkey}}}" busy="{{dash>/busy}}" busyIndicatorDelay="0">
        <headerContent>
            <Button icon="sap-icon://refresh" tooltip="{{i18n>refresh}}" press=".onRefresh"/>
        </headerContent>
        <content>
            <HBox wrap="Wrap" class="sapUiSmallMarginBeginEnd sapUiSmallMarginTop">
{tiles}            </HBox>
            <l:Grid defaultSpan="XL6 L6 M12 S12" containerQuery="true" class="sapUiSmallMarginBeginEnd sapUiSmallMarginTop">
{cards}            </l:Grid>
        </content>
    </Page>
</mvc:View>
'''


def _viz_name(text: str) -> str:
    """sap.viz dimension/measure names: no commas/braces (FeedItem values split on ',')."""
    import re as _re
    return _re.sub(r"[^\w .\-/()%]", " ", text or "").strip()[:40] or "Value"


def _kanban_view(ctx: RenderContext, page: Dict) -> str:
    pid = page["id"]
    tkey = f"{pid}_pageTitle"
    ctx.t(tkey, page["title"])
    ctx.t("refresh", "Refresh")
    ctx.t("searchPlaceholder", "Search")
    ctx.t("kbSampled", "Showing the latest {0} of {1} records.")
    dnd = ""
    if page["move"]:
        dnd = ('                            <dragDropConfig>\n'
               '                                <dnd:DragInfo groupName="kb" sourceAggregation="items"/>\n'
               '                                <dnd:DropInfo groupName="kb" targetAggregation="items" dropPosition="On" drop=".onDrop"/>\n'
               '                                <dnd:DropInfo groupName="kb" drop=".onDrop"/>\n'
               '                            </dragDropConfig>\n')
        ctx.t("kbHint", "Drag a card to another column to change its status.")
    hint = ('            <MessageStrip text="{i18n>kbHint}" type="Information" showIcon="true" '
            'class="sapUiSmallMarginBeginEnd sapUiTinyMarginTop"/>\n') if page["move"] else ""
    return f'''<mvc:View
    controllerName="{ctx.app_id}.controller.{_fid(pid)}Kanban"
    xmlns:mvc="sap.ui.core.mvc"
    xmlns="sap.m"
    xmlns:dnd="sap.ui.core.dnd"
    xmlns:core="sap.ui.core"
    displayBlock="true"
    height="100%">
    <Page title="{{i18n>{tkey}}}" busy="{{kb>/busy}}" busyIndicatorDelay="0">
        <headerContent>
            <SearchField width="14rem" liveChange=".onSearch" search=".onSearch"
                placeholder="{{i18n>searchPlaceholder}}"/>
            <Button icon="sap-icon://refresh" tooltip="{{i18n>refresh}}" press=".onRefresh"/>
        </headerContent>
        <content>
{hint}            <MessageStrip text="{{kb>/sampledText}}"
                type="Warning" visible="{{= !!${{kb>/sampled}} }}" class="sapUiSmallMarginBeginEnd sapUiTinyMarginTop"/>
            <ScrollContainer horizontal="true" vertical="false" width="100%">
                <HBox id="board" items="{{path: 'kb>/lanes', templateShareable: false}}" class="sapUiSmallMargin"
                    alignItems="Start">
                    <VBox width="19rem" class="sapUiTinyMarginEnd evkLane">
                        <layoutData><FlexItemData shrinkFactor="0" minWidth="19rem"/></layoutData>
                        <Toolbar design="Transparent" style="Clear">
                            <Title text="{{kb>title}}" level="H3"/>
                            <ToolbarSpacer/>
                            <ObjectNumber number="{{kb>count}}" emphasized="false"/>
                        </Toolbar>
                        <List items="{{path: 'kb>items', templateShareable: false}}" showNoData="false"
                            backgroundDesign="Transparent" showSeparators="None"
                            class="evkLaneList">
{dnd}                            <CustomListItem type="Active" press=".onCardPress" class="sapUiTinyMarginBottom">
                                <VBox class="sapUiSmallMargin">
                                    <Title text="{{kb>title}}" wrapping="true" level="H4"/>
                                    <Text text="{{kb>subtitle}}" visible="{{= !!${{kb>subtitle}} }}" class="sapUiTinyMarginTop"/>
                                    <VBox items="{{path: 'kb>attrs', templateShareable: false}}" class="sapUiTinyMarginTop">
                                        <ObjectAttribute title="{{kb>label}}" text="{{kb>value}}"/>
                                    </VBox>
                                </VBox>
                            </CustomListItem>
                        </List>
                    </VBox>
                </HBox>
            </ScrollContainer>
        </content>
    </Page>
</mvc:View>
'''


def _calendar_view(ctx: RenderContext, page: Dict) -> str:
    pid = page["id"]
    tkey = f"{pid}_pageTitle"
    ctx.t(tkey, page["title"])
    ctx.t("refresh", "Refresh")
    for k, v in {"calMonth": "Month", "calWeek": "Week", "calWorkWeek": "Work Week",
                 "calDay": "Day", "calToday": "Today"}.items():
        ctx.t(k, v)
    return f'''<mvc:View
    controllerName="{ctx.app_id}.controller.{_fid(pid)}Calendar"
    xmlns:mvc="sap.ui.core.mvc"
    xmlns="sap.m"
    xmlns:unified="sap.ui.unified"
    xmlns:core="sap.ui.core"
    displayBlock="true"
    height="100%">
    <Page title="{{i18n>{tkey}}}" busy="{{cal>/busy}}" busyIndicatorDelay="0">
        <headerContent>
            <Button icon="sap-icon://refresh" tooltip="{{i18n>refresh}}" press=".onRefresh"/>
        </headerContent>
        <content>
            <SinglePlanningCalendar id="calendar" class="sapUiSmallMarginTop" startDate="{{cal>/start}}"
                startDateChange=".onStartDateChange" appointmentSelect=".onAppointmentSelect"
                appointments="{{path: 'cal>/items', templateShareable: false}}" legend="legend"
                stickyMode="NavBarAndColHeaders" height="100%">
                <views>
                    <SinglePlanningCalendarMonthView key="Month" title="{{i18n>calMonth}}"/>
                    <SinglePlanningCalendarWorkWeekView key="WorkWeek" title="{{i18n>calWorkWeek}}"/>
                    <SinglePlanningCalendarWeekView key="Week" title="{{i18n>calWeek}}"/>
                    <SinglePlanningCalendarDayView key="Day" title="{{i18n>calDay}}"/>
                </views>
                <appointments>
                    <unified:CalendarAppointment title="{{cal>title}}" text="{{cal>text}}"
                        startDate="{{cal>start}}" endDate="{{cal>end}}" type="{{cal>type}}"/>
                </appointments>
            </SinglePlanningCalendar>
            <PlanningCalendarLegend id="legend" appointmentItems="{{path: 'cal>/legend', templateShareable: false}}"
                standardItems="Today" class="sapUiSmallMargin">
                <appointmentItems>
                    <unified:CalendarLegendItem text="{{cal>text}}" type="{{cal>type}}"/>
                </appointmentItems>
            </PlanningCalendarLegend>
        </content>
    </Page>
</mvc:View>
'''


def _analytics_view(ctx: RenderContext, page: Dict) -> str:
    pid = page["id"]
    tkey = f"{pid}_pageTitle"
    ctx.t(tkey, page["title"])
    ctx.t("refresh", "Refresh")
    ctx.t("anaSampled", "Based on the latest {0} of {1} records.")
    ctx.t("anaOpenList", "Open list")
    cards = ""
    for c in page["charts"]:
        ckey = f"{c['id']}_title"
        ctx.t(ckey, c["title"])
        dname = _viz_name(c["dimension_label"])
        sname = _viz_name(c["series_label"]) if c["series"] else ""
        mnames = []
        for m in c["measures"]:
            n = _viz_name(("Count" if m["aggregation"] == "count" else
                           f"{m['label']} ({m['aggregation']})" if m["aggregation"] != "sum" else m["label"]))
            while n in mnames or n in (dname, sname):
                n += " "
            mnames.append(n)
        dims = f'                                <vizData:DimensionDefinition name={xattr(dname)} value="{{ana>d}}"/>\n'
        if sname:
            dims += f'                                <vizData:DimensionDefinition name={xattr(sname)} value="{{ana>s}}"/>\n'
        meas = "".join(f'                                <vizData:MeasureDefinition name={xattr(n)} value="{{ana>m{i}}}"/>\n'
                       for i, n in enumerate(mnames))
        if c["type"] in ("pie", "donut"):
            feeds = (f'                        <vizFeeds:FeedItem uid="size" type="Measure" values={xattr(mnames[0])}/>\n'
                     f'                        <vizFeeds:FeedItem uid="color" type="Dimension" values={xattr(dname)}/>\n')
        elif c["type"] == "heatmap":
            feeds = (f'                        <vizFeeds:FeedItem uid="color" type="Measure" values={xattr(mnames[0])}/>\n'
                     f'                        <vizFeeds:FeedItem uid="categoryAxis" type="Dimension" values={xattr(dname)}/>\n'
                     f'                        <vizFeeds:FeedItem uid="categoryAxis2" type="Dimension" values={xattr(sname)}/>\n')
        else:
            feeds = (f'                        <vizFeeds:FeedItem uid="valueAxis" type="Measure" values={xattr(",".join(mnames))}/>\n'
                     f'                        <vizFeeds:FeedItem uid="categoryAxis" type="Dimension" values={xattr(dname)}/>\n')
            if sname:
                feeds += f'                        <vizFeeds:FeedItem uid="color" type="Dimension" values={xattr(sname)}/>\n'
        open_list = ""
        if c["route"]:
            open_list = (f'                            <Button text="{{i18n>anaOpenList}}" type="Transparent" press=".onOpenList">\n'
                         f'                                <customData><core:CustomData key="route" value="{c["route"]}"/></customData>\n'
                         f'                            </Button>\n')
        cards += f'''                <f:Card class="sapUiSmallMarginBottom" width="100%">
                    <f:header><card:Header title="{{i18n>{ckey}}}" iconSrc="sap-icon://business-objects-experience"/></f:header>
                    <f:content>
                        <VBox width="100%" class="sapUiSmallMarginBeginEnd sapUiSmallMarginBottom">
                            <viz:VizFrame id="{c['id']}" vizType="{c['type']}" width="100%" height="22rem"
                                uiConfig="{{applicationSet: 'fiori'}}">
                                <viz:dataset>
                                    <vizData:FlattenedDataset data="{{ana>/charts/{c['id']}}}">
                                        <vizData:dimensions>
{dims}                                        </vizData:dimensions>
                                        <vizData:measures>
{meas}                                        </vizData:measures>
                                    </vizData:FlattenedDataset>
                                </viz:dataset>
                                <viz:feeds>
{feeds}                                </viz:feeds>
                            </viz:VizFrame>
                            <HBox justifyContent="SpaceBetween" alignItems="Center">
                                <Text text="{{ana>/notes/{c['id']}}}" class="sapThemeLightText"/>
{open_list}                            </HBox>
                        </VBox>
                    </f:content>
                </f:Card>
'''
    span = "XL6 L6 M12 S12" if len(page["charts"]) > 1 else "XL12 L12 M12 S12"
    return f'''<mvc:View
    controllerName="{ctx.app_id}.controller.{_fid(pid)}Analytics"
    xmlns:mvc="sap.ui.core.mvc"
    xmlns="sap.m"
    xmlns:f="sap.f"
    xmlns:card="sap.f.cards"
    xmlns:l="sap.ui.layout"
    xmlns:viz="sap.viz.ui5.controls"
    xmlns:vizData="sap.viz.ui5.data"
    xmlns:vizFeeds="sap.viz.ui5.controls.common.feeds"
    xmlns:core="sap.ui.core"
    displayBlock="true"
    height="100%">
    <Page title="{{i18n>{tkey}}}" busy="{{ana>/busy}}" busyIndicatorDelay="0">
        <headerContent>
            <Button icon="sap-icon://refresh" tooltip="{{i18n>refresh}}" press=".onRefresh"/>
        </headerContent>
        <content>
            <l:Grid defaultSpan="{span}" containerQuery="true" class="sapUiSmallMarginBeginEnd sapUiSmallMarginTop">
{cards}            </l:Grid>
        </content>
    </Page>
</mvc:View>
'''


def _shell_view(ctx: RenderContext, nav: List[Dict]) -> str:
    items = "".join(f'                    <tnt:NavigationListItem key="{p["id"]}" icon="{p["icon"]}" '
                    f'text="{{i18n>{p["id"]}_pageTitle}}"/>\n' for p in nav)
    logo = (f'            <Image src="{{config>/logo}}" height="1.75rem" class="sapUiTinyMarginBegin"/>\n'
            if ctx.logo else "")
    ctx.t("menu", "Menu")
    return f'''<mvc:View controllerName="{ctx.app_id}.controller.App"
    xmlns:mvc="sap.ui.core.mvc" xmlns="sap.m" xmlns:tnt="sap.tnt"
    displayBlock="true" height="100%">
    <tnt:ToolPage id="toolPage" sideExpanded="{{appView>/sideExpanded}}">
        <tnt:header>
            <tnt:ToolHeader>
                <Button icon="sap-icon://menu2" type="Transparent" press=".onSideNavToggle" tooltip="{{i18n>menu}}"/>
{logo}                <Title text="{{i18n>appTitle}}" level="H1" wrapping="false"/>
                <ToolbarSpacer/>
            </tnt:ToolHeader>
        </tnt:header>
        <tnt:sideContent>
            <tnt:SideNavigation id="sideNav" selectedKey="{{appView>/navKey}}" itemSelect=".onNavSelect">
                <tnt:NavigationList>
{items}                </tnt:NavigationList>
            </tnt:SideNavigation>
        </tnt:sideContent>
        <tnt:mainContents>
            <App id="app"/>
        </tnt:mainContents>
    </tnt:ToolPage>
</mvc:View>
'''


def _common_texts(ctx: RenderContext) -> None:
    for k, v in {"ok": "OK", "cancel": "Cancel", "yes": "Yes", "no": "No",
                 "created": "Record created.", "saved": "Changes saved.", "deleted": "Deleted.",
                 "deleteConfirm": "Delete {0} record(s)?", "noSelection": "Select at least one record first.",
                 "actionDone": "{0}: done.", "actionConfirm": "{0} - apply to {1} record(s)?",
                 "fixFields": "Please fill in or correct: {0}", "noData": "No records found.",
                 "actionNotAllowed": "{0} is only possible when {1} is {2}. {3} of the {4} selected record(s) "
                                     "are in a different state.",
                 "dumpKnown": "{0} already failed in SAP for this record with a system error caused by its data "
                              "(see the previous message). It is not sent again — please ask your SAP "
                              "administrator to repair the record first."}.items():
        ctx.t(k, v)


def _scripts_of(actions: List[Dict], es_name: str) -> Dict[str, Dict]:
    return {a["id"]: dict(a, _entity_set=es_name) for a in actions if a["kind"] == "script"}


def render_app(ctx: RenderContext) -> Dict[str, str]:
    """Multi-page freestyle app: shell + dashboard / list (table or split) /
    detail / wizard / kanban / calendar / analytics pages."""
    bp = ctx.bp
    pages = bp["pages"]
    ctx.t("appTitle", bp["app"]["title"])
    ctx.t("appDescription", bp["app"]["description"] or bp["app"]["title"])
    _common_texts(ctx)

    files: Dict[str, str] = {}
    cfg_pages: Dict[str, Dict] = {}
    nav_by_route: Dict[str, str] = {}
    routes, targets = [], {}
    nav = [p for p in pages if p["type"] in ("dashboard", "list", "kanban", "calendar", "analytics")]
    base_fs = _Fs(_page_ctx(ctx, next(p for p in pages if p["type"] == "list")["sub"]))
    uses_charts = uses_viz = uses_cal = False
    used_bases = set()

    # custom logic first: the runtime check may drop hooks the views then omit
    lists = [p for p in pages if p["type"] == "list"]
    logic_by_page = {p["id"]: p["sub"].get("logic") or {} for p in lists}
    scripts = {}
    for p in lists:
        scripts.update(_scripts_of(p["sub"]["actions"], p["entity_set"]))
    for p in pages:
        if p["type"] == "analytics":
            ms = [{"id": f"{c['id']}_m{i}", "expression": m["expression"]}
                  for c in p["charts"] for i, m in enumerate(c["measures"]) if m.get("expression")]
            if ms:
                logic_by_page[p["id"]] = {"measures": ms}
    files["model/logic.js"] = _logic_files(ctx, logic_by_page, {p["id"]: p["entity_set"] for p in lists},
                                           scripts, ctx.notes)

    # multi-service apps: every page on its service's model, links between the areas
    multi = _ms.is_multi(ctx.model)
    svc = (lambda es_name: _ms.service_of(ctx.model, es_name)) if multi else (lambda es_name: "")
    links = bp.get("links") or []
    list_by_set: Dict[str, Dict] = {}
    for p in lists:
        list_by_set.setdefault(p["entity_set"], p)

    for i, p in enumerate(nav):
        pid, fid = p["id"], _fid(p["id"])
        ctx.t(f"{pid}_pageTitle", p["title"])
        routes.append({"name": pid, "pattern": ":?query:" if i == 0 else f"{pid}:?query:", "target": [pid]})
        nav_by_route[pid] = pid
        if p["type"] == "dashboard":
            uses_charts = uses_charts or bool(p["charts"])
            files[f"view/{fid}Dashboard.view.xml"] = _dashboard_view(ctx, base_fs, p)
            files[f"controller/{fid}Dashboard.controller.js"] = _page_controller(ctx, "Dashboard", fid, "Dashboard", pid)
            used_bases.add("Dashboard")
            targets[pid] = {"name": f"{fid}Dashboard", "id": pid, "level": 1}
            cfg_pages[pid] = {
                "routes": {"self": pid},
                "kpis": [{"id": k["id"], "entitySet": k["entity_set"], "field": k["field"],
                          "aggregation": k["aggregation"]} for k in p["kpis"]],
                "charts": [{"id": c["id"], "entitySet": c["entity_set"], "dimension": c["dimension"],
                            "measure": c["measure"], "aggregation": c["aggregation"]} for c in p["charts"]],
            }
            continue

        if p["type"] == "kanban":
            files[f"view/{fid}Kanban.view.xml"] = _kanban_view(ctx, p)
            files[f"controller/{fid}Kanban.controller.js"] = _page_controller(ctx, "Kanban", fid, "Kanban", pid)
            used_bases.add("Kanban")
            targets[pid] = {"name": f"{fid}Kanban", "id": pid, "level": 1}
            ctx.t("kbBlank", "(blank)")
            ctx.t("kbMoved", "{0} moved to {1}.")
            es = ctx.es(p["entity_set"])
            lanes = []
            for j, l in enumerate(p["lanes"]):
                key = f"{pid}_lane{j + 1}"
                ctx.t(key, l["title"])
                lanes.append({"value": l["value"], "titleKey": key})
            lp = ctx.prop(p["lane_field"], p["entity_set"])
            cfg_pages[pid] = {
                "service": svc(p["entity_set"]),
                "routes": {"self": pid}, "entitySet": p["entity_set"], "keys": list(es["keys"]),
                "laneField": p["lane_field"], "laneUiType": lp["ui_type"], "laneEdmType": lp.get("type"),
                "lanes": lanes, "move": p["move"], "route": p["route"],
                "cardTitle": p["card_title"], "cardTitleType": ctx.prop(p["card_title"], p["entity_set"])["ui_type"],
                "cardSubtitle": p["card_subtitle"],
                "cardSubtitleType": ctx.prop(p["card_subtitle"], p["entity_set"])["ui_type"] if p["card_subtitle"] else "text",
                "cardFields": [{"field": f, "label": ctx.label(f, p["entity_set"]),
                                "uiType": ctx.prop(f, p["entity_set"])["ui_type"],
                                "scale": ctx.prop(f, p["entity_set"]).get("scale") or 0} for f in p["card_fields"]],
            }
            continue

        if p["type"] == "calendar":
            uses_cal = True
            files[f"view/{fid}Calendar.view.xml"] = _calendar_view(ctx, p)
            files[f"controller/{fid}Calendar.controller.js"] = _page_controller(ctx, "Calendar", fid, "Calendar", pid)
            used_bases.add("Calendar")
            targets[pid] = {"name": f"{fid}Calendar", "id": pid, "level": 1}
            es_name = p["entity_set"]
            pr = lambda f: ctx.prop(f, es_name) if f else {}
            cfg_pages[pid] = {
                "service": svc(es_name),
                "routes": {"self": pid}, "entitySet": es_name, "keys": list(ctx.es(es_name)["keys"]),
                "startField": p["start_field"], "startEdmType": pr(p["start_field"]).get("type"),
                "startUiType": pr(p["start_field"]).get("ui_type"),
                "endField": p["end_field"], "endUiType": pr(p["end_field"]).get("ui_type", ""),
                "titleField": p["title_field"], "titleUiType": pr(p["title_field"]).get("ui_type", "text"),
                "textField": p["text_field"], "textUiType": pr(p["text_field"]).get("ui_type", "text"),
                "colorField": p["color_field"],
                "colorLabel": ctx.label(p["color_field"], es_name) if p["color_field"] else "",
                "route": p["route"],
            }
            continue

        if p["type"] == "analytics":
            uses_viz = True
            files[f"view/{fid}Analytics.view.xml"] = _analytics_view(ctx, p)
            files[f"controller/{fid}Analytics.controller.js"] = _page_controller(ctx, "Analytics", fid, "Analytics", pid)
            used_bases.add("Analytics")
            targets[pid] = {"name": f"{fid}Analytics", "id": pid, "level": 1}
            cfg_pages[pid] = {
                "routes": {"self": pid},
                "charts": [{"id": c["id"], "entitySet": c["entity_set"], "dimension": c["dimension"],
                            "timeGrain": c["time_grain"], "series": c["series"], "type": c["type"],
                            "measures": [{"field": m["field"], "aggregation": m["aggregation"],
                                          "expr": f"{c['id']}_m{i}" if m.get("expression") else "",
                                          "uses": m.get("uses") or []}
                                         for i, m in enumerate(c["measures"])]} for c in p["charts"]],
                "logicPage": pid,
            }
            continue

        # list page (table or split; + detail, + wizard)
        pctx = _page_ctx(ctx, p["sub"])
        split = p.get("layout") == "split"
        fs = _Fs(pctx, pid=pid, fid=fid, has_detail=bool(p["detail_id"]),
                 quick_tabs=p.get("quick_tabs", False) and not split, logic_page=pid)
        fs.link_target = list_by_set.get(p["entity_set"]) is p and any(
            l["to"] == p["entity_set"] for l in links)
        methods = fs.logic_methods()
        if split:
            files[f"view/{fid}Split.view.xml"] = fs.split_view(f"{pid}_pageTitle")
            files[f"controller/{fid}Split.controller.js"] = _page_controller(ctx, "Split", fid, "Split", pid, methods)
            used_bases.add("Split")
            targets[pid] = {"name": f"{fid}Split", "id": pid, "level": 1}
        else:
            files[f"view/{fid}List.view.xml"] = fs.list_view()
            files[f"controller/{fid}List.controller.js"] = _page_controller(ctx, "List", fid, "List", pid, methods)
            targets[pid] = {"name": f"{fid}List", "id": pid, "level": 1}
        files[f"fragment/{fid}SortDialog.fragment.xml"] = fs.sort_dialog()
        if p["sub"]["capabilities"]["create"] and p["create"] == "dialog":
            files[f"fragment/{fid}CreateDialog.fragment.xml"] = fs.create_dialog()
        lcfg = fs.config()
        lcfg.update({"fileId": fid, "createRoute": p["wizard_id"], "service": svc(p["entity_set"]),
                     "routes": {"list": pid, "object": p["detail_id"]}})
        cfg_pages[pid] = lcfg

        if p["wizard_id"]:
            wid = p["wizard_id"]
            files[f"view/{fid}Wizard.view.xml"] = _wizard_view(fs, p)
            files[f"controller/{fid}Wizard.controller.js"] = _page_controller(ctx, "Wizard", fid, "Wizard", wid)
            routes.append({"name": wid, "pattern": f"new-{pid}", "target": [wid]})
            targets[wid] = {"name": f"{fid}Wizard", "id": wid, "level": 2}
            nav_by_route[wid] = pid
            cfg_pages[wid] = {"entitySet": lcfg["entitySet"], "keys": lcfg["keys"],
                              "createFields": lcfg["createFields"], "valueHelps": lcfg["valueHelps"],
                              "createChildren": lcfg["createChildren"],
                              "logicPage": pid, "edmTypes": lcfg["edmTypes"], "service": lcfg["service"],
                              "routes": {"self": wid, "list": pid, "object": p["detail_id"]}}

        if p["detail_id"]:
            did = p["detail_id"]
            dfs = _Fs(pctx, pid=did, fid=fid, back_key=f"{pid}_pageTitle", logic_page=pid)
            dfs.cross, dfs.related = _link_parts(ctx, p, links, list_by_set, svc)
            files[f"view/{fid}Object.view.xml"] = dfs.object_view()
            files[f"controller/{fid}Object.controller.js"] = _page_controller(ctx, "Object", fid, "Object", did,
                                                                              dfs.logic_methods())
            routes.append({"name": did, "pattern": f"{pid}/{{key}}", "target": [did]})
            targets[did] = {"name": f"{fid}Object", "id": did, "level": 2}
            nav_by_route[did] = pid
            dcfg = dfs.config()
            dcfg.update({"fileId": fid, "routes": {"list": pid, "object": did}, "service": svc(p["entity_set"]),
                         "crossLinks": [{k: c[k] for k in ("field", "listRoute", "detailRoute", "entitySet",
                                                           "keyType", "toField", "toLabel", "model")}
                                        for c in dfs.cross],
                         "related": [{k: r[k] for k in ("id", "model", "entitySet", "field", "sourceField")}
                                     for r in dfs.related]})
            cfg_pages[did] = dcfg

    cfg = {"odataVersion": ctx.model["odata_version"], "logo": ctx.logo or "",
           "pages": cfg_pages, "navKeyByRoute": nav_by_route,
           "primaryService": _ms.primary_alias(ctx.model) if multi else "",
           "serviceOfSet": {e["name"]: e["service"] for e in ctx.model["entity_sets"]} if multi else {},
           # app-wide defaults for anything a page doesn't override
           "valueHelps": {}, "actions": [], "kpis": [], "filters": [], "searchFields": [],
           "columns": [], "createFields": [], "operations": {}, "edmTypes": {}}
    files["model/appConfig.js"] = ('sap.ui.define([], function () {\n    "use strict";\n'
                                   '    // Generated from the app blueprint — pages are configured here.\n'
                                   f'    return {json.dumps(cfg, indent=4)};\n}});\n')
    files["view/App.view.xml"] = _shell_view(ctx, nav)
    sub = lambda s: (s.replace("@@APP@@", ctx.app_id).replace("@@APPPATH@@", ctx.app_path)
                     .replace("/*@@LOGIC_METHODS@@*/\n", ""))
    files["Component.js"] = sub(js.COMPONENT)
    files["model/DataService.js"] = sub(js.DATA_SERVICE)
    states = {}
    for p in lists:
        states.update(p["sub"]["main"]["status"]["states"])
    files["model/formatter.js"] = js.FORMATTER.replace("@@STATES@@", json.dumps(states))
    files["controller/BaseController.js"] = sub(js.BASE_CONTROLLER)
    files["controller/App.controller.js"] = sub(js.SHELL_CONTROLLER)
    files["controller/List.controller.js"] = sub(js.LIST_CONTROLLER)
    files["controller/Object.controller.js"] = sub(js.OBJECT_CONTROLLER)
    if "Dashboard" in used_bases:
        files["controller/Dashboard.controller.js"] = sub(js.DASHBOARD_CONTROLLER)
    if any(p["wizard_id"] for p in lists):
        files["controller/Wizard.controller.js"] = sub(js.WIZARD_CONTROLLER)
    for base, src in (("Kanban", pjs.KANBAN_CONTROLLER), ("Calendar", pjs.CALENDAR_CONTROLLER),
                      ("Analytics", pjs.ANALYTICS_CONTROLLER), ("Split", pjs.SPLIT_CONTROLLER)):
        if base in used_bases:
            files[f"controller/{base}.controller.js"] = sub(src)
    if "Kanban" in used_bases:
        files["css/board.css"] = BOARD_CSS

    model_settings = ({"operationMode": "Server", "autoExpandSelect": True, "earlyRequests": True,
                       "groupId": "$auto", "updateGroupId": "$auto"} if ctx.v4 else
                      {"defaultBindingMode": "TwoWay", "defaultCountMode": "Inline",
                       "useBatch": True, "refreshAfterChange": True})
    libs = {"sap.m": {}, "sap.ui.core": {}, "sap.f": {}, "sap.uxap": {}, "sap.ui.layout": {},
            "sap.ui.comp": {}, "sap.tnt": {}, "sap.ui.export": {"lazy": True}}
    if uses_charts:
        libs["sap.suite.ui.microchart"] = {}
    if uses_viz:
        libs["sap.viz"] = {}
    if uses_cal:
        libs["sap.ui.unified"] = {}
    main_type = ctx.es(nav_first_list_set(pages))["entity_type"]
    sem = "".join(ch for ch in main_type if ch.isalnum()) or "App"
    resources = {"css": [{"uri": "css/board.css"}]} if "Kanban" in used_bases else {}
    manifest = {
        "_version": "1.59.0",
        "sap.app": {
            "id": ctx.app_id, "type": "application", "i18n": sap_app_i18n(ctx),
            "title": "{{appTitle}}", "description": "{{appDescription}}",
            "applicationVersion": {"version": "1.0.0"},
            "dataSources": dict({"mainService": {
                "uri": ctx.service_path, "type": "OData",
                "settings": {"odataVersion": "4.0" if ctx.v4 else "2.0",
                             "localUri": "localService/metadata.xml"}}},
                **{f"{s['alias']}Service": {
                    "uri": "/" + s["service_url"].strip().strip("/") + "/", "type": "OData",
                    "settings": {"odataVersion": "4.0" if ctx.v4 else "2.0",
                                 "localUri": f"localService/{s['alias']}/metadata.xml"}}
                   for s in (_ms.services(ctx.model)[1:] if multi else [])}),
            "crossNavigation": {"inbounds": {f"{sem}-display": {
                "semanticObject": sem, "action": "display", "title": "{{appTitle}}",
                "signature": {"parameters": {}, "additionalParameters": "allowed"}}}},
        },
        "sap.ui": {"technology": "UI5", "deviceTypes": {"desktop": True, "tablet": True, "phone": True}},
        "sap.ui5": {
            "rootView": {"viewName": f"{ctx.app_id}.view.App", "type": "XML", "id": "appView"},
            "dependencies": {"minUI5Version": "1.120.0", "libs": libs},
            "handleValidation": True,
            "contentDensities": {"compact": True, "cozy": True},
            "models": dict({
                "i18n": i18n_model(ctx),
                "": {"dataSource": "mainService", "preload": True, "settings": model_settings},
            }, **{s["alias"]: {"dataSource": f"{s['alias']}Service", "preload": True, "settings": model_settings}
                  for s in (_ms.services(ctx.model)[1:] if multi else [])}),
            "routing": {
                "config": {"routerClass": "sap.m.routing.Router", "type": "View", "viewType": "XML",
                           "path": f"{ctx.app_id}.view", "controlId": "app",
                           "controlAggregation": "pages", "async": True},
                "routes": routes,
                "targets": targets,
            },
        },
    }
    if resources:
        manifest["sap.ui5"]["resources"] = resources
    files["manifest.json"] = json.dumps(manifest, indent=4)
    boot = "sap.m,sap.f,sap.uxap,sap.tnt" + (",sap.viz" if uses_viz else "") + (",sap.ui.unified" if uses_cal else "")
    files["index.html"] = index_html(ctx, boot)
    return files


BOARD_CSS = """/* Kanban board lanes */
.evkLane { background: var(--sapGroup_ContentBackground, #f5f6f7); border-radius: .75rem;
           padding: .25rem .25rem .5rem; min-height: 8rem; }
.evkLaneList { min-height: 5rem; }
.evkLaneList .sapMLIB { border-radius: .5rem; box-shadow: var(--sapContent_Shadow0, 0 0 .125rem rgba(34,53,72,.15));
                        margin: .25rem; background: var(--sapList_Background, #fff); }
.evkLaneList .sapMLIB:hover { box-shadow: var(--sapContent_Shadow1, 0 .125rem .5rem rgba(34,53,72,.2)); }
.evkLaneList .sapUiDnDDragging { opacity: .5; }
"""


_KEY_TYPES = {"Edm.String", "Edm.Int16", "Edm.Int32", "Edm.Int64", "Edm.Byte", "Edm.SByte", "Edm.Guid"}


def _link_parts(ctx: RenderContext, page: Dict, links: List[Dict], list_by_set: Dict[str, Dict], svc):
    """(cross links shown in the header, related-record sections) of a list page's detail."""
    es_name = page["entity_set"]
    cross, related = [], []
    child_sets = {t["entity_set"] for t in page["sub"]["object_page"]["tables"]}
    for l in links:
        if (l["from"] == es_name or l["from"] in child_sets) and l["to"] in list_by_set:
            tgt = list_by_set[l["to"]]
            tes = ctx.es(l["to"])
            tp = ctx.prop(l["to_field"], l["to"])
            direct = (tgt["detail_id"] and tes["keys"] == [l["to_field"]] and tp.get("type", "Edm.String") in _KEY_TYPES)
            cross.append({"field": l["field"], "label": l["label"], "listRoute": tgt["id"],
                          "detailRoute": tgt["detail_id"] if direct else "", "entitySet": l["to"],
                          "keyType": tp.get("type", "Edm.String"), "toField": l["to_field"],
                          "toLabel": ctx.label(l["to_field"], l["to"]), "model": svc(l["to"]),
                          "entitySetFrom": l["from"]})
    for l in links:
        if l["to"] == es_name:
            src = list_by_set.get(l["from"])
            fes = ctx.es(l["from"])
            cols = ([c["field"] for c in src["sub"]["main"]["columns"]] if src else []) or \
                [f for f in fes["keys"]] + [p["name"] for p in fes["properties"]
                                            if p["name"] not in fes["keys"]][:4]
            cols = [c for c in cols if c != l["field"]][:6] or cols[:1]
            related.append({"id": f"rel{len(related) + 1}", "model": svc(l["from"]) or _ms.primary_alias(ctx.model),
                            "entity_set": l["from"], "entitySet": l["from"], "field": l["field"],
                            "sourceField": l["to_field"], "columns": cols,
                            "title": f"{fes.get('label') or l['from']}",
                            "route": src["detail_id"] if src and src["detail_id"] else ""})
    return cross, related


def nav_first_list_set(pages: List[Dict]) -> str:
    return next(p["entity_set"] for p in pages if p["type"] == "list")
