import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Multi-service OData + authorization support (see odata_config.py).
from odata_config import (
    ODataService, AUTH_NONE, AUTH_CHOICES,
    services_from_legacy_spec,
    manifest_datasources, manifest_models,
    ui5_proxy_middleware, env_example,
    xs_app_json as _oc_xs_app_json, destinations_template,
    mta_auth_resources, xs_security_json,
    mta_parameters_block, mta_extension,
    _slug as _oc_slug,
)


# ─────────────────────────────────────────────────────────────────────────────
# AppSpec — single source of truth for all generation
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AppSpec:
    """All parameters needed to generate a complete Fiori application."""
    user_prompt: str
    app_type: str    = "ListDetail"   # ListDetail | Worklist | MasterDetail | SinglePage
    app_id: str      = "com.mycompany.app"
    app_name: str    = "My Fiori App"
    odata_url: str   = ""
    entity_set: str  = "Items"
    key_field: str   = "Id"
    title_field: str = "Title"
    fields: List[str] = field(default_factory=list)
    theme: str       = "sap_horizon"
    languages: List[str] = field(default_factory=lambda: ["en"])
    with_routing: bool   = True
    with_fragments: bool = True
    with_model: bool     = True
    with_i18n: bool      = True
    with_deployment: bool = True
    # Header-items (e.g. Sales Order -> line items). Empty items_entity_set = off.
    items_entity_set: str = ""
    items_key_field: str  = "ItemId"
    items_title_field: str = "Product"
    items_fields: List[str] = field(default_factory=list)
    # OData navigation property name from header -> items (e.g. "ToItems").
    # Used by the ObjectPage child table to bind/expand the relationship. Falls
    # back to items_entity_set when not known.
    items_nav_name: str = ""
    # Multi-service: full list of OData services the app talks to. When empty,
    # service_list() derives a single primary service from the legacy fields
    # above, so existing single-service specs keep working unchanged.
    services: List["ODataService"] = field(default_factory=list)
    auth_type: str = AUTH_NONE     # fallback auth for the legacy primary service
    # Optional metadata-driven build hints (from business_analyzer). When present
    # the type-aware view/controller generators use them for richer output:
    # status_field -> colored ObjectStatus, value_help_fields -> ValueHelp,
    # filter_fields -> filter bar, child items table from a navigation. Empty by
    # default so the manual (non-metadata) path is completely unaffected.
    plan_hints: Dict = field(default_factory=dict)
    cf_org: str    = ""
    cf_space: str  = ""
    cf_api: str    = "https://api.cf.us10-001.hana.ondemand.com"

    def all_fields(self) -> List[str]:
        """All fields including key and title."""
        base = [self.key_field, self.title_field]
        return base + [f for f in self.fields if f not in base]

    def all_fields_str(self) -> str:
        return ", ".join(self.all_fields())

    def svc_url(self) -> str:
        """The primary OData service URL. A real URL is required now that mock
        generation has been removed; an obvious placeholder is returned when
        unset so misconfiguration is visible rather than silently mocked."""
        url = self.odata_url.strip()
        if not url or "<your" in url or "YOUR_SERVICE" in url:
            return "/sap/opu/odata/sap/YOUR_SERVICE/"
        return url

    def has_real_odata(self) -> bool:
        url = self.odata_url.strip()
        return bool(url) and "<your" not in url and "YOUR_SERVICE" not in url

    def app_path(self) -> str:
        """app_id as a resource path, e.g. com.mycompany.app -> com/mycompany/app."""
        return self.app_id.replace(".", "/")

    def service_list(self) -> List["ODataService"]:
        """All OData services for this app. Uses self.services when populated,
        otherwise derives a single primary (+ optional items) service from the
        legacy single-service fields. Single source of truth for the manifest,
        proxy, xs-app.json and destination generation."""
        return services_from_legacy_spec(self)

    def has_items(self) -> bool:
        """True when a child (line-items) entity is configured."""
        return bool(self.items_entity_set.strip())

    def all_item_fields(self) -> List[str]:
        """Child fields: own key + foreign key (header key) + title + extras."""
        base = [self.items_key_field, self.key_field, self.items_title_field]
        return base + [f for f in self.items_fields if f not in base]


# ─────────────────────────────────────────────────────────────────────────────
# HARDCODED FILE GENERATORS
# All critical files are generated directly in Python — no AI, no risk
# ─────────────────────────────────────────────────────────────────────────────

def _build_clean_manifest(spec: AppSpec) -> str:
    """
    Build guaranteed-correct manifest.json in Python.
    This is the ONLY source of manifest content — never trust AI for this.
    """
    manifest = {
        "_version": "1.58.0",
        "sap.app": {
            "id": spec.app_id,
            "type": "application",
            "applicationVersion": {"version": "0.0.1"},
            "title": "{{i18n>appTitle}}",
            "description": "{{i18n>appDescription}}",
            "dataSources": {
                "mainService": {
                    "uri": spec.svc_url(),
                    "type": "OData",
                    "settings": {"odataVersion": "2.0"}
                }
            }
        },
        "sap.ui": {
            "technology": "UI5",
            "deviceTypes": {"desktop": True, "tablet": True, "phone": True}
        },
        "sap.ui5": {
            "rootView": {
                "viewName": spec.app_id + ".view.App",
                "type": "XML",
                "async": True,
                "id": "app"
            },
            "dependencies": {
                "minUI5Version": "1.120.0",
                "libs": {
                    "sap.m":         {},
                    "sap.ui.core":   {},
                    "sap.f":         {},
                    "sap.ui.layout": {}
                }
            },
            "contentDensities": {
                "compact": True,
                "cozy": True
            },
            "models": {
                "": {
                    "dataSource": "mainService",
                    "preload": True,
                    "settings": {
                        "synchronizationMode": "None",
                        "operationMode": "Server",
                        "autoExpandSelect": True,
                        "earlyRequests": True
                    }
                },
                "i18n": {
                    "type": "sap.ui.model.resource.ResourceModel",
                    "settings": {
                        "bundleName": spec.app_id + ".i18n.i18n",
                        "supportedLocales": [""],
                        "fallbackLocale": ""
                    }
                }
            },
            "routing": {
                "config": {
                    "routerClass": "sap.m.routing.Router",
                    "viewType": "XML",
                    "viewPath": spec.app_id + ".view",
                    "controlId": "app",
                    "controlAggregation": "pages",
                    "async": True
                },
                "routes": [
                    {
                        "name": "RouteMain",
                        "pattern": "",
                        "target": ["TargetMain"]
                    }
                ],
                "targets": {
                    "TargetMain": {
                        "viewType": "XML",
                        "viewLevel": 1,
                        "viewName": "Main"
                    }
                }
            }
        }
    }
    return json.dumps(manifest, indent=4)


def _make_component_hardcoded(spec: AppSpec) -> str:
    """
    Guaranteed-correct Component.js.
    - No model/models import (file doesn't exist)
    - Correct extend name using spec.app_id
    - Device model inline
    - Router initialized after parent init
    """
    return (
        'sap.ui.define([\n'
        '    "sap/ui/core/UIComponent",\n'
        '    "sap/ui/Device",\n'
        '    "sap/ui/model/json/JSONModel"\n'
        '], function (UIComponent, Device, JSONModel) {\n'
        '    "use strict";\n\n'
        '    return UIComponent.extend("' + spec.app_id + '.Component", {\n\n'
        '        metadata: {\n'
        '            manifest: "json"\n'
        '        },\n\n'
        '        /**\n'
        '         * Called when the component is instantiated.\n'
        '         * @override\n'
        '         */\n'
        '        init: function () {\n'
        '            // Call parent init FIRST\n'
        '            UIComponent.prototype.init.apply(this, arguments);\n\n'
        '            // Device model — inlined, no external dependency\n'
        '            var oDeviceModel = new JSONModel(Device);\n'
        '            oDeviceModel.setDefaultBindingMode("OneWay");\n'
        '            this.setModel(oDeviceModel, "device");\n\n'
        '            // Initialize router LAST\n'
        '            this.getRouter().initialize();\n'
        '        }\n'
        '    });\n'
        '});\n'
    )


def _make_app_view_hardcoded(spec: AppSpec) -> str:
    """
    App shell view — contains ONLY sap.m.App.
    Router injects pages here via controlAggregation=pages.
    Must NOT contain Page, content, or any other controls.
    """
    return (
        '<mvc:View\n'
        '    xmlns:mvc="sap.ui.core.mvc"\n'
        '    xmlns="sap.m"\n'
        '    displayBlock="true">\n'
        '    <App id="app"/>\n'
        '</mvc:View>\n'
    )


def _make_index_html_hardcoded(spec: AppSpec) -> str:
    """
    Bootstrap index.html — declarative ComponentSupport. The app connects to
    the live OData service configured in manifest.json (proxied locally by
    fiori-tools-proxy, and via a BTP destination when deployed).
    resource_roots and settings are built as plain strings — no f-string brace conflicts.
    """
    resource_roots = '{"' + spec.app_id + '": "./"}'
    settings       = '{"id": "' + spec.app_id + '"}'

    head_open = (
        '<!DOCTYPE html>\n'
        '<html>\n'
        '<head>\n'
        '    <meta charset="utf-8">\n'
        '    <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        '    <title>' + spec.app_name + '</title>\n'
    )

    return (
        head_open +
        '    <script id="sap-ui-bootstrap"\n'
        '        src="https://ui5.sap.com/resources/sap-ui-core.js"\n'
        '        data-sap-ui-libs="sap.m,sap.ui.core,sap.f,sap.ui.layout"\n'
        '        data-sap-ui-theme="' + spec.theme + '"\n'
        '        data-sap-ui-resourceroots=\'' + resource_roots + '\'\n'
        '        data-sap-ui-oninit="module:sap/ui/core/ComponentSupport"\n'
        '        data-sap-ui-compatversion="edge"\n'
        '        data-sap-ui-async="true">\n'
        '    </script>\n'
        '</head>\n'
        '<body class="sapUiBody" id="content">\n'
        '    <div data-sap-ui-component\n'
        '         data-name="' + spec.app_id + '"\n'
        '         data-id="container"\n'
        '         data-settings=\'' + settings + '\'>\n'
        '    </div>\n'
        '</body>\n'
        '</html>\n'
    )


def _make_ui5_yaml_hardcoded(spec: AppSpec) -> str:
    """
    Clean ui5.yaml — no fiori-tools-proxy unless a real OData URL is given.
    Proxy section causes ui5 serve to crash when destination is not configured.
    """
    base = (
        "specVersion: '3.0'\n"
        "metadata:\n"
        "  name: " + spec.app_id + "\n"
        "type: application\n"
        "framework:\n"
        "  name: SAPUI5\n"
        "  version: 1.120.0\n"
        "  libraries:\n"
        "  - name: sap.m\n"
        "  - name: sap.ui.core\n"
        "  - name: sap.f\n"
        "  - name: sap.uxap\n"
        "  - name: sap.ui.layout\n"
        "  - name: themelib_sap_horizon\n"
    )
    # Proxy every REAL backend for local dev. Credentials are read at runtime
    # from a gitignored .env by fiori-tools-proxy — never written into yaml.
    base += ui5_proxy_middleware(spec.service_list())
    return base


def _make_controller_hardcoded(spec: AppSpec) -> str:
    """
    Guaranteed-correct Main.controller.js.
    - attachPatternMatched (NOT attachRouteMatched — causes assertion error)
    - No model/models import
    - Full CRUD handlers with proper error handling
    - JSDoc on every function
    """
    entity = spec.entity_set
    key    = spec.key_field
    title  = spec.title_field
    extend = spec.app_id + ".controller.Main"
    svc    = spec.svc_url()

    return (
        'sap.ui.define([\n'
        '    "sap/ui/core/mvc/Controller",\n'
        '    "sap/ui/model/json/JSONModel",\n'
        '    "sap/m/MessageBox",\n'
        '    "sap/m/MessageToast",\n'
        '    "sap/ui/model/Filter",\n'
        '    "sap/ui/model/FilterOperator"\n'
        '], function (Controller, JSONModel, MessageBox, MessageToast, Filter, FilterOperator) {\n'
        '    "use strict";\n\n'
        '    return Controller.extend("' + extend + '", {\n\n'
        '        // ── Lifecycle ────────────────────────────────────────────\n\n'
        '        /**\n'
        '         * Called when the controller is instantiated.\n'
        '         * Registers route handler using attachPatternMatched.\n'
        '         */\n'
        '        onInit: function () {\n'
        '            var oRouter = this.getOwnerComponent().getRouter();\n'
        '            oRouter.getRoute("RouteMain").attachPatternMatched(this._onRouteMatched, this);\n\n'
        '            // Local view model for busy state\n'
        '            var oViewModel = new JSONModel({\n'
        '                busy: false,\n'
        '                delay: 0,\n'
        '                listTitle: ""\n'
        '            });\n'
        '            this.getView().setModel(oViewModel, "viewModel");\n'
        '        },\n\n'
        '        // ── Route handler ────────────────────────────────────────\n\n'
        '        /**\n'
        '         * Called when RouteMain pattern is matched.\n'
        '         * Refreshes the list binding to get latest data.\n'
        '         */\n'
        '        _onRouteMatched: function () {\n'
        '            var oList = this.byId("itemsList");\n'
        '            if (oList) {\n'
        '                var oBinding = oList.getBinding("items");\n'
        '                if (oBinding) {\n'
        '                    oBinding.refresh();\n'
        '                }\n'
        '            }\n'
        '        },\n\n'
        '        // ── Search ───────────────────────────────────────────────\n\n'
        '        /**\n'
        '         * Filters the list by the search query.\n'
        '         * @param {sap.ui.base.Event} oEvent - The search/liveChange event\n'
        '         */\n'
        '        onSearch: function (oEvent) {\n'
        '            var sQuery = oEvent.getParameter("query") ||\n'
        '                         oEvent.getParameter("newValue") || "";\n'
        '            var oList    = this.byId("itemsList");\n'
        '            if (!oList) { return; }\n'
        '            var oBinding = oList.getBinding("items");\n'
        '            if (!oBinding) { return; }\n'
        '            var aFilters = sQuery.trim()\n'
        '                ? [new Filter("' + title + '", FilterOperator.Contains, sQuery.trim())]\n'
        '                : [];\n'
        '            oBinding.filter(aFilters);\n'
        '        },\n\n'
        '        // ── List interactions ────────────────────────────────────\n\n'
        '        /**\n'
        '         * Handles item press — navigates to detail or shows info.\n'
        '         * @param {sap.ui.base.Event} oEvent - The press event\n'
        '         */\n'
        '        onItemPress: function (oEvent) {\n'
        '            var oCtx = oEvent.getSource().getBindingContext();\n'
        '            if (!oCtx) { return; }\n'
        '            var sKey   = oCtx.getProperty("' + key + '");\n'
        '            var sTitle = oCtx.getProperty("' + title + '");\n'
        '            MessageToast.show("Selected: " + (sTitle || sKey || "item"));\n'
        '        },\n\n'
        '        // ── CRUD operations ──────────────────────────────────────\n\n'
        '        /**\n'
        '         * Opens the create dialog.\n'
        '         */\n'
        '        onCreatePress: function () {\n'
        '            var oModel = this.getView().getModel();\n'
        '            if (!oModel) {\n'
        '                MessageBox.information(\n'
        '                    "Connect an OData service to enable Create."\n'
        '                );\n'
        '                return;\n'
        '            }\n'
        '            // Set empty context on view model for the dialog\n'
        '            var oViewModel = this.getView().getModel("viewModel");\n'
        '            var oNewItem = {};\n'
        '            oViewModel.setProperty("/NewItem", oNewItem);\n'
        '            this._openDialog();\n'
        '        },\n\n'
        '        /**\n'
        '         * Handles delete from List mode=Delete.\n'
        '         * @param {sap.ui.base.Event} oEvent - The delete event\n'
        '         */\n'
        '        onDeletePress: function (oEvent) {\n'
        '            var oItem  = oEvent.getParameter("listItem");\n'
        '            var oCtx   = oItem ? oItem.getBindingContext() : null;\n'
        '            if (!oCtx) { return; }\n'
        '            var sName  = oCtx.getProperty("' + title + '") ||\n'
        '                         oCtx.getProperty("' + key + '") || "this item";\n'
        '            var oModel = this.getView().getModel();\n'
        '            MessageBox.confirm(\n'
        '                "Delete \\"" + sName + "\\"?",\n'
        '                {\n'
        '                    title: "Confirm Delete",\n'
        '                    onClose: function (sAction) {\n'
        '                        if (sAction === MessageBox.Action.OK && oModel) {\n'
        '                            oModel.remove(oCtx.getPath(), {\n'
        '                                success: function () {\n'
        '                                    MessageToast.show("Deleted successfully.");\n'
        '                                },\n'
        '                                error: function (oError) {\n'
        '                                    this._handleError(oError);\n'
        '                                }.bind(this)\n'
        '                            });\n'
        '                        } else if (sAction === MessageBox.Action.OK) {\n'
        '                            MessageBox.information("Connect OData service to enable delete.");\n'
        '                        }\n'
        '                    }.bind(this)\n'
        '                }\n'
        '            );\n'
        '        },\n\n'
        '        /**\n'
        '         * Saves the new/edited item.\n'
        '         */\n'
        '        onSavePress: function () {\n'
        '            var oModel    = this.getView().getModel();\n'
        '            var oViewModel = this.getView().getModel("viewModel");\n'
        '            var oNewItem  = oViewModel ? oViewModel.getProperty("/NewItem") : null;\n'
        '            if (!oModel || !oNewItem) {\n'
        '                MessageBox.information("Connect OData service to enable save.");\n'
        '                return;\n'
        '            }\n'
        '            oModel.create("/' + entity + '", oNewItem, {\n'
        '                success: function () {\n'
        '                    MessageToast.show("Created successfully.");\n'
        '                    this._closeDialog();\n'
        '                }.bind(this),\n'
        '                error: function (oError) {\n'
        '                    this._handleError(oError);\n'
        '                }.bind(this)\n'
        '            });\n'
        '        },\n\n'
        '        /**\n'
        '         * Cancels the dialog and reverts pending changes.\n'
        '         */\n'
        '        onCancelPress: function () {\n'
        '            var oModel = this.getView().getModel();\n'
        '            if (oModel && oModel.hasPendingChanges()) {\n'
        '                oModel.resetChanges();\n'
        '            }\n'
        '            this._closeDialog();\n'
        '        },\n\n'
        '        // ── Dialog helpers ───────────────────────────────────────\n\n'
        + _status_formatter_js(spec) +
        '        /**\n'
        '         * Opens the DetailDialog fragment.\n'
        '         * @private\n'
        '         */\n'
        '        _openDialog: function () {\n'
        '            var oView = this.getView();\n'
        '            if (!this._oDialog) {\n'
        '                this._oDialog = sap.ui.xmlfragment(\n'
        '                    oView.getId(),\n'
        '                    "' + spec.app_id + '.fragment.DetailDialog",\n'
        '                    this\n'
        '                );\n'
        '                oView.addDependent(this._oDialog);\n'
        '            }\n'
        '            this._oDialog.open();\n'
        '        },\n\n'
        '        /**\n'
        '         * Closes the DetailDialog fragment.\n'
        '         * @private\n'
        '         */\n'
        '        _closeDialog: function () {\n'
        '            if (this._oDialog) {\n'
        '                this._oDialog.close();\n'
        '            }\n'
        '        },\n\n'
        '        // ── Error handling ───────────────────────────────────────\n\n'
        '        /**\n'
        '         * Parses and displays an OData error.\n'
        '         * @param {object} oError - The OData error response\n'
        '         * @private\n'
        '         */\n'
        '        _handleError: function (oError) {\n'
        '            var sMessage = "An unexpected error occurred.";\n'
        '            try {\n'
        '                var oBody = JSON.parse(oError.responseText || "{}");\n'
        '                sMessage  = (oBody.error && oBody.error.message &&\n'
        '                             oBody.error.message.value)\n'
        '                          ? oBody.error.message.value\n'
        '                          : sMessage;\n'
        '            } catch (e) { /* ignore parse errors */ }\n'
        '            MessageBox.error(sMessage, { title: "Error" });\n'
        '        }\n\n'
        '    });\n'
        '});\n'
    )


def _make_i18n_hardcoded(spec: AppSpec) -> str:
    """
    Guaranteed-correct i18n.properties with ALL required keys.
    App name and entity-specific keys derived directly from spec.
    """
    key_lower   = spec.key_field.lower()
    title_lower = spec.title_field.lower()

    content = (
        "# Application\n"
        "appTitle=" + spec.app_name + "\n"
        "appDescription=" + spec.app_name + " Application\n"
        "pageTitle=" + spec.app_name + "\n\n"
        "# List page\n"
        "listTitle=" + spec.entity_set + "\n"
        "noDataText=No " + spec.entity_set.lower() + " found\n"
        "searchPlaceholder=Search " + spec.entity_set.lower() + "...\n\n"
        "# Detail page\n"
        "detailTitle=" + spec.entity_set.rstrip("s") + " Detail\n"
        "generalInfoTitle=General Information\n\n"
        "# Buttons\n"
        "createButtonText=Create\n"
        "deleteButtonText=Delete\n"
        "saveButtonText=Save\n"
        "cancelButtonText=Cancel\n"
        "editButtonText=Edit\n"
        "backButtonText=Back\n\n"
        "# Dialogs\n"
        "confirmDeleteTitle=Confirm Delete\n"
        "confirmDeleteMessage=Are you sure you want to delete this item?\n\n"
        "# Messages\n"
        "errorTitle=Error\n"
        "successMessage=Operation completed successfully\n"
        "busyText=Loading...\n"
        "savedMessage=Saved successfully\n"
        "deletedMessage=Deleted successfully\n\n"
        "# Column headers\n"
        "column_" + key_lower + "=" + _field_label(spec, spec.key_field) + "\n"
        "column_" + title_lower + "=" + _field_label(spec, spec.title_field) + "\n"
    )
    for f in spec.fields:
        content += "column_" + f.lower() + "=" + _field_label(spec, f) + "\n"

    if spec.has_items():
        content += (
            "\n# Line items\n"
            "itemsButtonText=Line Items\n"
            "itemsTitle=" + spec.items_entity_set + "\n"
        )
        for f in spec.all_item_fields():
            if f in (spec.key_field,):
                continue
            content += "column_" + f.lower() + "=" + _field_label(spec, f) + "\n"

    return content


# ─────────────────────────────────────────────────────────────────────────────
# AI-ASSISTED FILE GENERATORS (structured prompts)
# ─────────────────────────────────────────────────────────────────────────────

def _system_prompt() -> str:
    return (
        "You are an expert SAPUI5 1.120 / Fiori application code generator.\n"
        "OUTPUT RULES — follow ALL strictly:\n"
        "1. Return ONLY the raw file content. Zero markdown fences (no ```). Zero explanations.\n"
        "2. No preamble ('Here is...', 'Below is...', 'Sure!'). Start with file content immediately.\n"
        "3. No trailing notes, comments outside file syntax, or closing remarks.\n"
        "4. Follow SAPUI5 best practices: MVC, i18n for ALL strings, OData v2 bindings.\n"
        "5. NEVER put sap.m.Button inside ObjectListItem actions — use List mode='Delete'.\n"
        "6. NEVER import model/models.js — it does not exist in this project.\n"
        "7. NEVER use attachRouteMatched — always use attachPatternMatched.\n"
        "8. The rootView is always App.view.xml (shell). Main.view.xml is the routed content page.\n"
    )


def _make_main_view_prompt(spec: AppSpec) -> str:
    ctrl    = spec.app_id + ".controller.Main"
    binding = "{/" + spec.entity_set + "}"

    return (
        "Generate view/Main.view.xml for a SAPUI5 Fiori " + spec.app_type + " application.\n\n"
        "controllerName : " + ctrl + "\n"
        "Entity binding : " + binding + "\n"
        "Key field      : " + spec.key_field + "\n"
        "Title field    : " + spec.title_field + "\n"
        "All fields     : " + spec.all_fields_str() + "\n"
        "App type       : " + spec.app_type + "\n\n"
        "STRICT RULES:\n"
        "1. mvc:View controllerName='" + ctrl + "' — exact string, no deviation\n"
        "2. ALL user-visible strings use {i18n>key} — zero hardcoded text\n"
        "3. Page title='{i18n>listTitle}'\n"
        "4. headerContent: Button text='{i18n>createButtonText}' press='.onCreatePress' type='Emphasized'\n"
        "5. subHeader: Bar > SearchField placeholder='{i18n>searchPlaceholder}' search='.onSearch'\n"
        "6. List id='itemsList' items='" + binding + "' noDataText='{i18n>noDataText}'\n"
        "   mode='Delete' delete='.onDeletePress'\n"
        "7. ObjectListItem: title='{" + spec.title_field + "}' intro='{" + spec.key_field + "}'\n"
        "   type='Active' press='.onItemPress'\n"
        "8. NO Button or any other control inside ObjectListItem <actions> aggregation\n"
        "   (actions only accepts ObjectListItemAction, never sap.m.Button)\n"
        "9. For Worklist: use sap.m.Table with ColumnListItem instead of ObjectListItem\n"
        "10. Responsive layout — works on desktop and mobile\n\n"
        "Output raw XML only. No fences. No explanation."
    )


def _make_app_model_prompt(spec: AppSpec) -> str:
    busy_obj = '{"busy": false, "delay": 0}'
    return (
        "Generate model/AppModel.js for a SAPUI5 Fiori application.\n\n"
        "Entity set : " + spec.entity_set + "\n"
        "Key field  : " + spec.key_field + "\n"
        "Fields     : " + spec.all_fields_str() + "\n\n"
        "Requirements:\n"
        "- sap.ui.define module with JSONModel and ODataModel\n"
        "- createInitialItemModel(): JSONModel with empty structure for all fields\n"
        "- createBusyModel(): JSONModel with " + busy_obj + "\n"
        "- createDeviceModel(Device): JSONModel(Device) with OneWay binding\n"
        "- initODataModel(sUrl): sap.ui.model.odata.v2.ODataModel\n"
        "  useBatch:true, defaultBindingMode:TwoWay, refreshAfterChange:true\n"
        "- formatters: formatDate(val), formatStatus(val), formatBoolean(val)\n"
        "- Export object with all functions\n"
        "- DO NOT reference model/models — this IS the model file\n\n"
        "Output raw JavaScript only. No fences. No explanation."
    )


def _make_fragment_prompt(spec: AppSpec) -> str:
    i18n_title = "{i18n>detailTitle}"
    fields_str = spec.all_fields_str()
    return (
        "Generate fragment/DetailDialog.fragment.xml for a SAPUI5 Fiori application.\n\n"
        "Entity set  : " + spec.entity_set + "\n"
        "Key field   : " + spec.key_field + "\n"
        "Title field : " + spec.title_field + "\n"
        "Fields      : " + fields_str + "\n\n"
        "Requirements:\n"
        "1. Root: core:FragmentDefinition xmlns:core='sap.ui.core' xmlns='sap.m' xmlns:f='sap.ui.layout.form'\n"
        "2. sap.m.Dialog id='DetailDialog' title='" + i18n_title + "'\n"
        "3. content: f:SimpleForm with Label + Input pairs for each field\n"
        "   Input value binding: {viewModel>/NewItem/FieldName}\n"
        "4. beginButton: Button text='{i18n>saveButtonText}' press='.onSavePress' type='Emphasized'\n"
        "5. endButton: Button text='{i18n>cancelButtonText}' press='.onCancelPress'\n"
        "6. All strings via i18n\n\n"
        "Output raw XML only. No fences. No explanation."
    )


def _make_xs_app_prompt(spec: AppSpec) -> str:
    return (
        "Generate xs-app.json for SAP BTP Cloud Foundry AppRouter.\n\n"
        "OData path: " + spec.svc_url() + "\n\n"
        "Requirements:\n"
        "- welcomeFile: /index.html\n"
        "- authenticationMethod: route\n"
        "- routes:\n"
        "  1. source '^/sap/(.*)$' destination 'backend' authenticationType none\n"
        "  2. source '^(.*)$' localDir 'webapp' authenticationType none\n"
        "- logout: {logoutEndpoint: '/logout'}\n\n"
        "Output raw JSON only. No fences. No explanation."
    )


def _make_mta_prompt(spec: AppSpec) -> str:
    org   = spec.cf_org   or "your-org"
    space = spec.cf_space or "dev"
    return (
        "Generate mta.yaml for SAP BTP Cloud Foundry MTA deployment.\n\n"
        "App ID : " + spec.app_id + "\n"
        "CF API : " + spec.cf_api + "\n"
        "Org    : " + org + "\n"
        "Space  : " + space + "\n\n"
        "Requirements:\n"
        "_schema-version: '3.3.0'\n"
        "ID: " + spec.app_id + "\n"
        "version: 0.0.1\n"
        "Modules:\n"
        "1. " + spec.app_id + "-approuter (nodejs)\n"
        "   path: ./ requires: dest-service, xsuaa-service\n"
        "2. " + spec.app_id + "-ui (com.sap.staticfile)\n"
        "   path: webapp build: npm run build\n"
        "Resources:\n"
        "- dest-service: org.cloudfoundry.existing-service\n"
        "- xsuaa-service: org.cloudfoundry.existing-service\n\n"
        "Output raw YAML only. No fences. No explanation."
    )


def _make_package_json_prompt(spec: AppSpec) -> str:
    return (
        "Generate package.json for a SAPUI5 Fiori app.\n\n"
        "App ID   : " + spec.app_id + "\n"
        "App Name : " + spec.app_name + "\n\n"
        "Requirements:\n"
        "name: " + spec.app_id + "\n"
        "version: 0.0.1\n"
        "scripts: start='ui5 serve', build='ui5 build --all',\n"
        "         deploy='cf push " + spec.app_id + "', lint='eslint webapp'\n"
        "devDependencies: @ui5/cli ^3.0.0, @sap/ux-ui5-tooling ^1.0.0\n"
        "engines: node >=18\n\n"
        "Output raw JSON only. No fences. No explanation."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Type-inference helpers (entity/type naming + EDM type guessing) used by the
# view, controller and manifest generators.
# ─────────────────────────────────────────────────────────────────────────────

def _entity_type_name(spec: AppSpec) -> str:
    """Singular-ish EntityType name derived from the entity set."""
    name = spec.entity_set.strip() or "Item"
    if len(name) > 1 and name.endswith("s"):
        name = name[:-1]
    return name[:1].upper() + name[1:]


def _edm_type(field_name: str) -> str:
    """Pick a plausible EDM type from a field name."""
    lower = field_name.lower()

    # Money first — so e.g. "UnitPrice" is Decimal, not caught by the "unit" hint
    if any(t in lower for t in ("price", "amount", "total", "cost", "salary", "revenue",
                                "rate", "tax", "discount", "fee", "balance",
                                "net", "gross", "wage", "budget", "credit", "limit")):
        return "Edm.Decimal"

    # Clearly-textual names next, so e.g. "Country" isn't caught by "count"
    string_hints = ("name", "email", "phone", "mobile", "tel", "fax", "city", "town",
                    "country", "street", "address", "company", "vendor", "supplier",
                    "partner", "account", "category", "segment", "class", "status",
                    "state", "currency", "unit", "uom", "product", "item", "material",
                    "article", "title", "code", "ref", "desc", "note", "comment",
                    "remark", "summary", "text", "first", "last", "surname", "customer",
                    "contact", "owner", "manager", "user", "person", "region", "zip",
                    "postal", "type", "group", "department", "dept", "division", "team",
                    "designation", "role", "position", "job")
    if any(t in lower for t in string_hints):
        return "Edm.String"

    if any(t in lower for t in ("qty", "quantity", "count", "stock", "units", "age",
                                "year", "level", "score", "rank", "priority", "weight")):
        return "Edm.Int32"
    if any(t in lower for t in ("date", "time", "created", "modified", "changed",
                                "due", "delivery")):
        return "Edm.DateTime"
    if (lower.startswith("is") or lower.startswith("has") or
        any(t in lower for t in ("active", "enabled", "flag", "done", "completed",
                                 "available", "discontinued", "shipped", "paid",
                                 "approved", "deleted", "blocked", "valid"))):
        return "Edm.Boolean"
    return "Edm.String"


# ─────────────────────────────────────────────────────────────────────────────
# DETAIL PAGE (ListDetail pattern) — separate page reached by clicking an item
# ─────────────────────────────────────────────────────────────────────────────

def _make_detail_view(spec: AppSpec) -> str:
    """Detail.view.xml — ObjectHeader + all fields, bound to the selected record."""
    ctrl = spec.app_id + ".controller.Detail"

    form_content = ""
    for f in spec.all_fields():
        form_content += (
            '                    <Label text="{i18n>column_' + f.lower() + '}"/>\n'
            '                    <Text text="{' + f + '}"/>\n'
        )

    items_button = ""
    if spec.has_items():
        items_button = (
            '            <Button\n'
            '                text="{i18n>itemsButtonText}"\n'
            '                icon="sap-icon://list"\n'
            '                press=".onShowItems"\n'
            '                type="Emphasized"/>\n'
        )
    header_content = (
        '        <headerContent>\n'
        + items_button +
        '            <Button\n'
        '                text="{i18n>deleteButtonText}"\n'
        '                icon="sap-icon://delete"\n'
        '                press=".onDelete"\n'
        '                type="Reject"/>\n'
        '        </headerContent>\n'
    )

    return (
        '<mvc:View\n'
        '    controllerName="' + ctrl + '"\n'
        '    xmlns:mvc="sap.ui.core.mvc"\n'
        '    xmlns="sap.m"\n'
        '    xmlns:f="sap.ui.layout.form"\n'
        '    displayBlock="true">\n'
        '    <Page\n'
        '        id="detailPage"\n'
        '        title="{i18n>detailTitle}"\n'
        '        showNavButton="true"\n'
        '        navButtonPress=".onNavBack">\n'
        + header_content +
        '        <content>\n'
        '            <ObjectHeader\n'
        '                title="{' + spec.title_field + '}"\n'
        '                number="{' + spec.key_field + '}"\n'
        '                responsive="true"\n'
        '                class="sapUiSmallMarginBottom"/>\n'
        '            <f:SimpleForm\n'
        '                editable="false"\n'
        '                layout="ResponsiveGridLayout"\n'
        '                labelSpanXL="4" labelSpanL="4" labelSpanM="4" labelSpanS="12"\n'
        '                adjustLabelSpan="false"\n'
        '                columnsXL="2" columnsL="2" columnsM="1"\n'
        '                class="sapUiSmallMarginTop">\n'
        '                <f:content>\n'
        + form_content +
        '                </f:content>\n'
        '            </f:SimpleForm>\n'
        '        </content>\n'
        '    </Page>\n'
        '</mvc:View>\n'
    )


def _make_detail_controller(spec: AppSpec) -> str:
    """
    Detail.controller.js — binds the view to the record whose key arrives in the
    RouteDetail pattern ({objectId}). Uses oModel.createKey so it works for any
    key type, and metadataLoaded() so the binding waits for $metadata.
    """
    extend = spec.app_id + ".controller.Detail"
    entity = spec.entity_set
    key    = spec.key_field
    title  = spec.title_field

    return (
        'sap.ui.define([\n'
        '    "sap/ui/core/mvc/Controller",\n'
        '    "sap/m/MessageBox",\n'
        '    "sap/m/MessageToast",\n'
        '    "sap/ui/core/routing/History"\n'
        '], function (Controller, MessageBox, MessageToast, History) {\n'
        '    "use strict";\n\n'
        '    return Controller.extend("' + extend + '", {\n\n'
        '        /**\n'
        '         * Attach the detail route handler.\n'
        '         */\n'
        '        onInit: function () {\n'
        '            this.getOwnerComponent().getRouter()\n'
        '                .getRoute("RouteDetail")\n'
        '                .attachPatternMatched(this._onObjectMatched, this);\n'
        '        },\n\n'
        '        /**\n'
        '         * Bind the view to the record identified by the route parameter.\n'
        '         * @param {sap.ui.base.Event} oEvent - the routing event\n'
        '         * @private\n'
        '         */\n'
        '        _onObjectMatched: function (oEvent) {\n'
        '            var sObjectId = decodeURIComponent(\n'
        '                oEvent.getParameter("arguments").objectId\n'
        '            );\n'
        '            this._objectId = sObjectId;\n'
        '            var oView  = this.getView();\n'
        '            var oModel = oView.getModel();\n'
        '            if (!oModel) { return; }\n'
        '            oModel.metadataLoaded().then(function () {\n'
        '                var oKey = {};\n'
        '                oKey["' + key + '"] = sObjectId;\n'
        '                var sPath = "/" + oModel.createKey("' + entity + '", oKey);\n'
        '                oView.bindElement({ path: sPath });\n'
        '            });\n'
        '        },\n\n'
        + _detail_items_handler(spec) +
        '        /**\n'
        '         * Delete the currently displayed record (with confirmation),\n'
        '         * then navigate back to the list.\n'
        '         */\n'
        '        onDelete: function () {\n'
        '            var oView = this.getView();\n'
        '            var oCtx  = oView.getBindingContext();\n'
        '            if (!oCtx) { return; }\n'
        '            var oModel = oView.getModel();\n'
        '            var sName  = oCtx.getProperty("' + title + '") ||\n'
        '                         oCtx.getProperty("' + key + '") || "this item";\n'
        '            MessageBox.confirm("Delete \\"" + sName + "\\"?", {\n'
        '                title: "Confirm Delete",\n'
        '                onClose: function (sAction) {\n'
        '                    if (sAction !== MessageBox.Action.OK) { return; }\n'
        '                    if (!oModel) {\n'
        '                        MessageBox.information("Connect OData service to enable delete.");\n'
        '                        return;\n'
        '                    }\n'
        '                    oModel.remove(oCtx.getPath(), {\n'
        '                        success: function () {\n'
        '                            MessageToast.show("Deleted.");\n'
        '                            this.getOwnerComponent().getRouter().navTo("RouteMain", {}, true);\n'
        '                        }.bind(this),\n'
        '                        error: this._handleError.bind(this)\n'
        '                    });\n'
        '                }.bind(this)\n'
        '            });\n'
        '        },\n\n'
        '        /**\n'
        '         * Parses and displays an OData error.\n'
        '         * @param {object} oError - the OData error response\n'
        '         * @private\n'
        '         */\n'
        '        _handleError: function (oError) {\n'
        '            var sMessage = "An unexpected error occurred.";\n'
        '            try {\n'
        '                var oBody = JSON.parse(oError.responseText || "{}");\n'
        '                sMessage = (oBody.error && oBody.error.message && oBody.error.message.value)\n'
        '                         ? oBody.error.message.value : sMessage;\n'
        '            } catch (e) { /* ignore */ }\n'
        '            MessageBox.error(sMessage, { title: "Error" });\n'
        '        },\n\n'
        '        /**\n'
        '         * Navigate back to the list.\n'
        '         */\n'
        '        onNavBack: function () {\n'
        '            var sPreviousHash = History.getInstance().getPreviousHash();\n'
        '            if (sPreviousHash !== undefined) {\n'
        '                window.history.go(-1);\n'
        '            } else {\n'
        '                this.getOwnerComponent().getRouter().navTo("RouteMain", {}, true);\n'
        '            }\n'
        '        }\n\n'
        '    });\n'
        '});\n'
    )


def _make_fragment_hardcoded(spec: AppSpec) -> str:
    """
    Create/Edit dialog fragment — generated in Python so labels use the REAL
    i18n keys (column_*) and inputs bind to viewModel>/NewItem, matching the
    controller. Input control is chosen by field type (DatePicker for dates,
    numeric Input for decimals/ints, StepInput for counts). Only editable,
    non-key fields get inputs; the key is auto-assigned on save.
    """
    has_date = False
    rows = ""
    for f in spec.all_fields():
        if f == spec.key_field:
            continue  # key is auto-assigned on save
        edm = _edm_type(f)
        label = ('                <Label text="{i18n>column_' + f.lower()
                 + '}" labelFor="in_' + f + '"/>\n')
        if edm == "Edm.DateTime":
            has_date = True
            ctrl = ('                <DatePicker id="in_' + f + '" '
                    'value="{viewModel>/NewItem/' + f + '}" '
                    'valueFormat="yyyy-MM-dd" displayFormat="medium"/>\n')
        elif edm in ("Edm.Decimal", "Edm.Int32"):
            ctrl = ('                <Input id="in_' + f + '" type="Number" '
                    'value="{viewModel>/NewItem/' + f + '}"/>\n')
        elif edm == "Edm.Boolean":
            ctrl = ('                <CheckBox id="in_' + f + '" '
                    'selected="{viewModel>/NewItem/' + f + '}"/>\n')
        else:
            ctrl = ('                <Input id="in_' + f + '" '
                    'value="{viewModel>/NewItem/' + f + '}"/>\n')
        rows += label + ctrl

    return (
        '<core:FragmentDefinition\n'
        '    xmlns="sap.m"\n'
        '    xmlns:core="sap.ui.core">\n'
        '    <Dialog id="detailDialog" title="{i18n>createButtonText}" contentWidth="30rem">\n'
        '        <content>\n'
        '            <VBox class="sapUiSmallMargin">\n'
        + rows +
        '            </VBox>\n'
        '        </content>\n'
        '        <beginButton>\n'
        '            <Button text="{i18n>saveButtonText}" press=".onSavePress" type="Emphasized"/>\n'
        '        </beginButton>\n'
        '        <endButton>\n'
        '            <Button text="{i18n>cancelButtonText}" press=".onCancelPress"/>\n'
        '        </endButton>\n'
        '    </Dialog>\n'
        '</core:FragmentDefinition>\n'
    )


def _make_mta_yaml_hardcoded(spec: AppSpec) -> str:
    """
    Standalone approuter MTA. When no service has a real backend URL this is a
    single static-serving approuter with its own public route (URL immediately).
    When any service is real, the approuter additionally `requires` an XSUAA
    and a Destination service, and those managed services are declared as
    resources — a destination is needed to reach ANY real backend through the
    approuter, regardless of whether that backend requires credentials or
    already exists (e.g. a shared destination like "EvokeSD4"). If any service
    is on-premise, the Connectivity service is also required — on-premise
    destinations route through Cloud Connector via that service, and without
    it bound the approuter gets a 502 even with a correctly configured
    destination.
    """
    app = spec.app_id
    svcs = spec.service_list()
    protected = any(s.is_real() for s in svcs)
    on_prem = any(getattr(s, "on_premise", False) for s in svcs)

    requires = ""
    if protected:
        xsuaa = _oc_slug(app) + "-xsuaa"
        dest  = _oc_slug(app) + "-destination"
        conn  = _oc_slug(app) + "-connectivity"
        requires = (
            "    requires:\n"
            "      - name: " + xsuaa + "\n"
            "      - name: " + dest + "\n"
            + ("      - name: " + conn + "\n" if on_prem else "")
        )

    yaml_str = (
        "_schema-version: '3.3.0'\n"
        "ID: " + app + "\n"
        "version: 0.0.1\n"
        "parameters:\n"
        "  enable-parallel-deployments: true\n"
        + (mta_parameters_block(svcs) if protected else "") +
        "\n"
        "modules:\n"
        "  - name: " + app + "\n"
        "    type: approuter.nodejs\n"
        "    path: .\n"
        "    parameters:\n"
        "      memory: 256M\n"
        "      disk-quota: 512M\n"
        + requires +
        "    build-parameters:\n"
        "      # No local build step: mbt only needs to PACKAGE the approuter\n"
        "      # sources. The @sap/approuter dependency is installed by the CF\n"
        "      # Node.js buildpack when the module STAGES on BTP (where node/npm\n"
        "      # exist), so `mbt build` requires no local node/npm toolchain.\n"
        "      builder: custom\n"
        "      commands: []\n"
        "      build-result: .\n"
        "      ignore:\n"
        "        - node_modules/\n"
        "        - .env\n"
        "        - '*.mtar'\n"
        "        - mta_archives/\n"
    )
    if protected:
        yaml_str += "\n" + mta_auth_resources(app, svcs)
    return yaml_str


def _make_root_xs_app_json_hardcoded(spec: AppSpec) -> str:
    """
    Approuter routing (project root). When no service needs auth this serves the
    built UI statically with no authentication (simplest working URL). When any
    service requires auth, each backend gets an xsuaa-protected route forwarding
    to a named BTP Destination, and the UI itself is protected too.
    """
    return _oc_xs_app_json(spec.service_list())


def _make_root_package_json_hardcoded(spec: AppSpec) -> str:
    """
    Project package.json: runs the @sap/approuter at start and serves the UI5
    sources directly (no ui5 build step). @sap/approuter is a runtime dependency
    installed by the CF Node.js buildpack when the module stages on BTP; there is
    no local node/npm requirement at MTA build time.
    """
    return json.dumps({
        "name": spec.app_id.replace(".", "-"),
        "version": "0.0.1",
        "description": spec.app_name,
        "dependencies": {"@sap/approuter": "^16.0.0"},
        "scripts": {
            "start": "node node_modules/@sap/approuter/approuter.js"
        },
        "engines": {"node": ">=18"}
    }, indent=2)


def _detail_items_handler(spec: AppSpec) -> str:
    """onShowItems handler for the detail controller (only when items enabled)."""
    if not spec.has_items():
        return ""
    return (
        '        /**\n'
        '         * Navigate to the line-items subpage for this record.\n'
        '         */\n'
        '        onShowItems: function () {\n'
        '            if (!this._objectId) { return; }\n'
        '            this.getOwnerComponent().getRouter().navTo("RouteItems", {\n'
        '                objectId: encodeURIComponent(this._objectId)\n'
        '            });\n'
        '        },\n\n'
    )


def _make_items_view(spec: AppSpec) -> str:
    """Items.view.xml — a table of the child records for the selected header."""
    ctrl    = spec.app_id + ".controller.Items"
    binding = "{/" + spec.items_entity_set + "}"
    columns = ""
    cells   = ""
    for f in spec.all_item_fields():
        if f == spec.key_field:
            continue  # foreign key — no need to show it as a column
        columns += (
            '                    <Column><Text text="{i18n>column_' + f.lower() + '}"/></Column>\n'
        )
        cells += (
            '                        <Text text="{' + f + '}"/>\n'
        )
    return (
        '<mvc:View\n'
        '    controllerName="' + ctrl + '"\n'
        '    xmlns:mvc="sap.ui.core.mvc"\n'
        '    xmlns="sap.m"\n'
        '    displayBlock="true">\n'
        '    <Page\n'
        '        id="itemsPage"\n'
        '        title="{i18n>itemsTitle}"\n'
        '        showNavButton="true"\n'
        '        navButtonPress=".onNavBack">\n'
        '        <content>\n'
        '            <Table\n'
        '                id="itemsTable"\n'
        '                items="' + binding + '"\n'
        '                noDataText="{i18n>noDataText}"\n'
        '                growing="true"\n'
        '                growingThreshold="50">\n'
        '                <columns>\n'
        + columns +
        '                </columns>\n'
        '                <items>\n'
        '                    <ColumnListItem>\n'
        '                        <cells>\n'
        + cells +
        '                        </cells>\n'
        '                    </ColumnListItem>\n'
        '                </items>\n'
        '            </Table>\n'
        '        </content>\n'
        '    </Page>\n'
        '</mvc:View>\n'
    )


def _make_items_controller(spec: AppSpec) -> str:
    """Items.controller.js — filters the child table by the header's key."""
    extend = spec.app_id + ".controller.Items"
    fk     = spec.key_field
    return (
        'sap.ui.define([\n'
        '    "sap/ui/core/mvc/Controller",\n'
        '    "sap/ui/model/Filter",\n'
        '    "sap/ui/model/FilterOperator",\n'
        '    "sap/ui/core/routing/History"\n'
        '], function (Controller, Filter, FilterOperator, History) {\n'
        '    "use strict";\n\n'
        '    return Controller.extend("' + extend + '", {\n\n'
        '        onInit: function () {\n'
        '            this.getOwnerComponent().getRouter()\n'
        '                .getRoute("RouteItems")\n'
        '                .attachPatternMatched(this._onMatched, this);\n'
        '        },\n\n'
        '        _onMatched: function (oEvent) {\n'
        '            var sHeaderId = decodeURIComponent(\n'
        '                oEvent.getParameter("arguments").objectId\n'
        '            );\n'
        '            var oTable = this.byId("itemsTable");\n'
        '            if (!oTable) { return; }\n'
        '            var oBinding = oTable.getBinding("items");\n'
        '            if (!oBinding) { return; }\n'
        '            // Show only the line items belonging to this header\n'
        '            oBinding.filter([\n'
        '                new Filter("' + fk + '", FilterOperator.EQ, sHeaderId)\n'
        '            ]);\n'
        '        },\n\n'
        '        onNavBack: function () {\n'
        '            var sPrev = History.getInstance().getPreviousHash();\n'
        '            if (sPrev !== undefined) {\n'
        '                window.history.go(-1);\n'
        '            } else {\n'
        '                this.getOwnerComponent().getRouter().navTo("RouteMain", {}, true);\n'
        '            }\n'
        '        }\n\n'
        '    });\n'
        '});\n'
    )


def _make_auth_config_files(spec: AppSpec) -> Dict[str, str]:
    """Runtime auth/config files. Never contain real secrets:
      - .env.example      : local-dev credential template (copy to .env)
      - .gitignore        : keeps .env / build output out of source control
      - xs-security.json  : XSUAA descriptor (created whenever any service is real)
      - destinations.stub.json : BTP destination stubs to import in the cockpit
    """
    svcs = spec.service_list()
    files: Dict[str, str] = {}
    real = [s for s in svcs if s.is_real()]
    protected = any(s.is_real() for s in svcs)
    if real:
        files[".env.example"] = env_example(svcs)
        files[".gitignore"]   = (
            "node_modules/\n"
            "dist/\n"
            "mta_archives/\n"
            "*.mtar\n"
            ".env\n"
            "deploy.mtaext\n"      # real, secret-bearing extension — never commit
        )
    if protected:
        files["xs-security.json"]       = xs_security_json(spec.app_id)
        files["destinations.stub.json"] = destinations_template(svcs)
        # Example MTA extension — copy to deploy.mtaext, fill in, pass to
        # `cf deploy app.mtar -e deploy.mtaext`. The Deploy tab does this for you.
        files["deploy.mtaext.example"]  = mta_extension(spec.app_id, svcs, {})
    return files


def _make_type_extra_files(spec: AppSpec) -> Dict[str, str]:
    """Extra per-pattern files. ListDetail gets a standalone detail page,
    plus a line-items subpage when a child entity is configured."""
    files = {}
    if spec.app_type == "ListDetail":
        files["view/Detail.view.xml"] = _make_detail_view(spec)
        files["controller/Detail.controller.js"] = _make_detail_controller(spec)
        if spec.has_items():
            files["view/Items.view.xml"] = _make_items_view(spec)
            files["controller/Items.controller.js"] = _make_items_controller(spec)
    elif spec.app_type == "ObjectPage":
        files["view/Object.view.xml"] = _make_object_page_view(spec)
        files["controller/Object.controller.js"] = _make_object_page_controller(spec)
    return files


# ─────────────────────────────────────────────────────────────────────────────

# Files generated directly in Python — zero AI involvement
HARDCODED_FILES = {
    # manifest: type-aware (routing differs per pattern)
    "manifest.json":              lambda spec: _make_manifest_by_type(spec),
    "index.html":                 lambda spec: _make_index_html_hardcoded(spec),
    "Component.js":               lambda spec: _make_component_hardcoded(spec),
    "view/App.view.xml":          lambda spec: _make_app_view_hardcoded(spec),
    # view/Main.view.xml: type-aware (ListDetail/Worklist/MasterDetail/SinglePage)
    "view/Main.view.xml":         lambda spec: _make_view_by_type(spec),
    # controller: type-aware (MasterDetail adds onMasterSelect/onNavBack etc.)
    "controller/Main.controller.js": lambda spec: _make_controller_by_type(spec),
    "i18n/i18n.properties":       lambda spec: _make_i18n_hardcoded(spec),
    "ui5.yaml":                   lambda spec: _make_ui5_yaml_hardcoded(spec),
    "fragment/DetailDialog.fragment.xml": lambda spec: _make_fragment_hardcoded(spec),
    "mta.yaml":                   lambda spec: _make_mta_yaml_hardcoded(spec),
    "xs-app.json":                lambda spec: _make_root_xs_app_json_hardcoded(spec),
    "package.json":               lambda spec: _make_root_package_json_hardcoded(spec),
}

# Files generated by AI with structured prompts
PROMPT_BUILDERS = {
    # view/Main.view.xml moved to HARDCODED_FILES (type-specific Python generation)
    "model/AppModel.js":                  lambda spec, ctx: _make_app_model_prompt(spec),
}


def build_file_list(spec: AppSpec) -> List[str]:
    """Return ordered list of files to generate."""
    files = [
        "manifest.json",                  # hardcoded (type-aware)
        "index.html",                     # hardcoded
        "Component.js",                   # hardcoded
        "view/App.view.xml",              # hardcoded
        "view/Main.view.xml",             # hardcoded (type-aware: ListDetail/Worklist/MasterDetail/SinglePage)
        "controller/Main.controller.js",  # hardcoded (type-aware)
        "ui5.yaml",                       # hardcoded
    ]
    if spec.with_model:
        files.append("model/AppModel.js")      # AI
    if spec.with_i18n:
        files.append("i18n/i18n.properties")   # hardcoded
    if spec.with_fragments:
        files.append("fragment/DetailDialog.fragment.xml")  # AI
    if spec.with_deployment:
        files += ["xs-app.json", "mta.yaml", "package.json"]
        files += list(_make_auth_config_files(spec).keys())   # .env.example, xs-security.json, etc.
    files += list(_make_type_extra_files(spec).keys())        # per-pattern extras (detail page)
    return files


# ─────────────────────────────────────────────────────────────────────────────
# Response cleaner
# ─────────────────────────────────────────────────────────────────────────────

def clean_response(raw: str) -> str:
    """
    Strip all AI output artifacts:
    - Markdown code fences
    - Leading preamble ('Here is...', 'Sure!', etc.)
    - Trailing notes ('Note:', 'This...', etc.)
    - Windows CRLF line endings
    """
    raw = raw.strip().replace("\r\n", "\n")

    # Extract content inside fence if present
    fence_match = re.search(r"```[a-zA-Z]*\s*\n(.*?)```", raw, re.DOTALL)
    if fence_match:
        return fence_match.group(1).strip()

    # Strip leading preamble lines
    lines = raw.splitlines()
    start = 0
    preamble = re.compile(
        r"^(here is|here's|sure|below is|the following|of course|"
        r"certainly|absolutely|i'll|i will|let me|as requested|"
        r"great|happy to|no problem)",
        re.IGNORECASE
    )
    for i, line in enumerate(lines):
        s = line.strip()
        if preamble.match(s) or (s.endswith(":") and i == 0 and len(s) < 80):
            start = i + 1
        else:
            break

    # Strip trailing note lines
    end = len(lines)
    trailing = re.compile(
        r"^(note:|note that|this |the above|make sure|remember|"
        r"you can|feel free|please|additionally|also note|"
        r"the code|this code|this file)",
        re.IGNORECASE
    )
    for i in range(len(lines) - 1, start - 1, -1):
        s = lines[i].strip()
        if trailing.match(s) or (s.startswith("```") and i == len(lines) - 1):
            end = i
        else:
            break

    return "\n".join(lines[start:end]).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Post-generation validator
# ─────────────────────────────────────────────────────────────────────────────

def _is_manifest_valid(mf: dict, spec: AppSpec) -> bool:
    """Validate all critical manifest fields."""
    try:
        ui5 = mf.get("sap.ui5", {})

        # App ID
        if mf.get("sap.app", {}).get("id") != spec.app_id:
            return False

        # i18n settings
        i18n = ui5.get("models", {}).get("i18n", {}).get("settings", {})
        if i18n.get("supportedLocales") != [""]:
            return False
        if i18n.get("fallbackLocale") != "":
            return False
        if i18n.get("bundleName") != spec.app_id + ".i18n.i18n":
            return False

        # rootView
        root = ui5.get("rootView", {})
        if not isinstance(root, dict):
            return False
        if not root.get("viewName", "").endswith(".view.App"):
            return False

        # routing viewPath
        if ui5.get("routing", {}).get("config", {}).get("viewPath") != spec.app_id + ".view":
            return False

        # library names — must all start with "sap.", not be numbers
        libs = ui5.get("dependencies", {}).get("libs", {})
        for lib in libs.keys():
            if lib.isdigit() or not lib.startswith("sap."):
                return False

        return True
    except Exception:
        return False


def validate_and_fix(files: Dict[str, str], spec: AppSpec) -> Dict[str, str]:
    """
    Post-generation safety net.
    Strategy: always overwrite hardcoded files with fresh Python output.
    Validate AI-generated manifest; replace entirely if any field is wrong.
    Fix known AI mistakes in view and any remaining AI files.
    """
    fixed = dict(files)

    # ── Always overwrite hardcoded files ────────────────────────────────────
    # These are generated directly — AI output for them is discarded entirely
    for fname, generator_fn in HARDCODED_FILES.items():
        fixed[fname] = generator_fn(spec)

    # ── Always (re)write per-pattern extra files (detail page) ──────────────
    for fname, content in _make_type_extra_files(spec).items():
        fixed[fname] = content

    # ── Always (re)write auth/config files (.env.example, xs-security, etc.) ─
    if spec.with_deployment:
        for fname, content in _make_auth_config_files(spec).items():
            fixed[fname] = content

    # ── Validate AI-generated view/Main.view.xml ─────────────────────────────
    view_key = "view/Main.view.xml"
    if view_key in fixed:
        view = fixed[view_key]
        correct_ctrl = spec.app_id + ".controller.Main"

        # Fix wrong controllerName
        view = re.sub(
            r'controllerName\s*=\s*"[^"]*"',
            'controllerName="' + correct_ctrl + '"',
            view
        )
        # Remove Button from ObjectListItem actions (common AI mistake)
        view = re.sub(
            r'<actions>\s*<Button[^/]*/>\s*</actions>',
            '', view, flags=re.DOTALL
        )
        view = re.sub(
            r'<actions>\s*<Button[^>]*>.*?</Button>\s*</actions>',
            '', view, flags=re.DOTALL
        )
        fixed[view_key] = view

    return fixed


# ─────────────────────────────────────────────────────────────────────────────
# Main generation loop
# ─────────────────────────────────────────────────────────────────────────────

def generate_app_files(spec: AppSpec, ai_fn) -> Dict[str, str]:
    """
    Generate all app files.

    ai_fn(system_prompt: str, user_prompt: str) -> str

    Flow:
    1. For each file in build_file_list():
       - HARDCODED: generate directly from Python template
       - AI: send structured prompt to ai_fn, clean response
    2. Run validate_and_fix() as final safety net
    3. Return dict of filename -> content

    All critical files (manifest, Component.js, controller, i18n, etc.)
    are hardcoded — AI is only used for view/Main.view.xml, AppModel.js,
    fragment, and deployment descriptors.
    """
    files_to_generate = build_file_list(spec)
    results: Dict[str, str] = {}
    context: Dict[str, str] = {}
    sys_prompt = _system_prompt()
    extra_files = _make_type_extra_files(spec)
    auth_files = _make_auth_config_files(spec) if spec.with_deployment else {}

    for filename in files_to_generate:
        try:
            if filename in extra_files:
                # Per-pattern extra (e.g. detail page) — pure Python, no AI
                content = extra_files[filename]
                results[filename] = content
                context[filename] = content

            elif filename in auth_files:
                # Auth/config file (.env.example, xs-security.json, etc.) — no AI
                content = auth_files[filename]
                results[filename] = content
                context[filename] = content

            elif filename in HARDCODED_FILES:
                # Pure Python — no AI call
                content = HARDCODED_FILES[filename](spec)
                results[filename] = content
                context[filename] = content

            elif filename in PROMPT_BUILDERS:
                # AI-generated with structured prompt
                user_prompt = PROMPT_BUILDERS[filename](spec, context)
                raw = ai_fn(sys_prompt, user_prompt)
                content = clean_response(raw)
                results[filename] = content
                context[filename] = content

            else:
                results[filename] = "/* Unknown file: " + filename + " */"

        except Exception as e:
            results[filename] = "/* ERROR generating " + filename + ": " + str(e) + " */"

    # Final safety net — always runs
    results = validate_and_fix(results, spec)

    return results


# ─────────────────────────────────────────────────────────────────────────────
# APP-TYPE SPECIFIC HARDCODED VIEW GENERATORS
# Each pattern produces correct, working SAPUI5 markup
# ─────────────────────────────────────────────────────────────────────────────

def _entity_label(spec: AppSpec) -> str:
    """Human label for the primary entity itself (for the object-page subtitle),
    e.g. 'Sales Order'. Prefers plan_hints['entity_label'], else humanizes the
    entity set name (stripping a leading A_ / C_ convention prefix)."""
    lbl = (spec.plan_hints or {}).get("entity_label", "")
    if lbl:
        return lbl
    import re as _re
    name = spec.entity_set or "Item"
    name = _re.sub(r"^[A-Za-z]_", "", name)          # strip A_ / C_ prefix
    name = _re.sub(r"(Set|Collection)$", "", name)   # strip Set/Collection suffix
    s = _re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name.replace("_", " "))
    s = _re.sub(r"\s+", " ", s).strip()
    return (s[:1].upper() + s[1:]) if s else name


def _field_label(spec: AppSpec, field: str) -> str:
    """Human label for a field: prefer the metadata's sap:label/Common.Label
    (threaded via plan_hints['field_labels']), else a humanized field name
    (split camelCase / underscores). Used for form labels and column headers so
    the UI reads 'Sold-To Party', not 'SoldToParty'."""
    labels = (spec.plan_hints or {}).get("field_labels", {})
    if field in labels and labels[field]:
        return labels[field]
    import re as _re
    s = field.replace("_", " ")
    s = _re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s)
    s = _re.sub(r"\s+", " ", s).strip()
    return (s[:1].upper() + s[1:]) if s else field


def _status_formatter_js(spec: AppSpec) -> str:
    """Build a formatStatusState(sValue) JS method that maps a status field's
    values to sap.ui.core.ValueState (Success/Warning/Error/None). When the
    metadata pipeline captured the field's real sample values, they're mapped
    heuristically (open/new->Warning, done/complete/approved->Success,
    cancelled/rejected/error->Error); otherwise a generic keyword mapping is
    emitted. Returns '' when there's no status field so nothing extra is added."""
    hints = spec.plan_hints or {}
    status_field = hints.get("status_field", "")
    if not status_field:
        return ""

    # Optional: explicit value->state map derived from sample data, if provided.
    value_states = hints.get("status_value_states") or {}
    case_lines = ""
    for val, state in value_states.items():
        safe = str(val).replace('"', '\\"')
        case_lines += (
            '            if (s === "' + safe.lower() + '") { return "' + state + '"; }\n'
        )

    return (
        '        // ── Formatters ───────────────────────────────────────────\n\n'
        '        /**\n'
        '         * Maps a status value to a sap.ui.core.ValueState for colored\n'
        '         * ObjectStatus rendering. Generated from the service\'s real\n'
        '         * status values where available.\n'
        '         * @param {string} sValue - The raw status value\n'
        '         * @returns {string} One of Success|Warning|Error|None\n'
        '         */\n'
        '        formatStatusState: function (sValue) {\n'
        '            if (!sValue) { return "None"; }\n'
        '            var s = String(sValue).toLowerCase();\n'
        + case_lines +
        '            if (/(cancel|reject|error|fail|block|overdue|^not |\\bnot\\b|incomplete|unpaid)/.test(s)) { return "Error"; }\n'
        '            if (/(partial|in\\s?progress|pending|hold|await|draft|started)/.test(s)) { return "Warning"; }\n'
        '            if (/(complete|done|approv|success|closed|paid|deliver|active|finish|invoiced)/.test(s)) { return "Success"; }\n'
        '            if (/(open|new)/.test(s)) { return "Warning"; }\n'
        '            return "None";\n'
        '        },\n\n'
    )


def _cell_xml(spec: AppSpec, field: str, indent: str = "                        ") -> str:
    """Render one table cell for a field. If it's the plan's status field,
    emit a colored ObjectStatus bound to .formatStatusState; else plain Text.
    Shared by the ListDetail / Worklist / MasterDetail view builders so status
    rendering is consistent across archetypes."""
    status_field = (spec.plan_hints or {}).get("status_field", "")
    if field and field == status_field:
        return (
            indent + '<ObjectStatus\n'
            + indent + '    text="{' + field + '}"\n'
            + indent + '    state="{ path: \'' + field + '\', formatter: \'.formatStatusState\' }"/>\n'
        )
    return indent + '<Text text="{' + field + '}"/>\n'


def _make_object_page_view(spec: AppSpec) -> str:
    """sap.uxap.ObjectPageLayout: an object header bound to a single entity by
    key, a 'General Information' section rendering the detail fields as a
    field-group form, and — when a child navigation exists — an Items section
    with a responsive table bound to the navigation property (expand). This is
    the relationship-aware header+items object page."""
    ctrl = spec.app_id + ".controller.Object"
    key = spec.key_field
    title = spec.title_field
    status_field = (spec.plan_hints or {}).get("status_field", "")

    # NOTE: the status is rendered inside the General Info form rows below (as a
    # colored ObjectStatus). We deliberately do NOT add a separate status block
    # in the ObjectPage header: sap.uxap.ObjectPageHeaderContent has no default
    # aggregation, so placing an ObjectStatus (or any control) as a direct child
    # throws "Cannot add direct child without default aggregation defined" and
    # breaks the whole view before data binds.
    header_status = ""

    # General info form rows (detail fields)
    form_rows = ""
    for f in spec.all_fields():
        form_rows += (
            '                                <Label text="' + _field_label(spec, f) + '"/>\n'
        )
        if f and f == status_field:
            form_rows += (
                '                                <ObjectStatus text="{' + f + '}" '
                'state="{ path: \'' + f + '\', formatter: \'.formatStatusState\' }"/>\n'
            )
        else:
            form_rows += (
                '                                <Text text="{' + f + '}"/>\n'
            )

    # Items section (child table) — only when a navigation child is configured.
    items_section = ""
    if spec.has_items():
        item_cols = ""
        item_cells = ""
        # Detect labels shared by 2+ item fields so we can disambiguate the
        # column headers (the standard SO service gives PurchaseOrderByCustomer
        # and PurchaseOrderByShipToParty the SAME sap:label "Customer Reference",
        # which otherwise renders as two identical columns).
        _item_fields = [f for f in spec.all_item_fields() if f != spec.key_field]
        _label_counts = {}
        for f in _item_fields:
            lbl = _field_label(spec, f)
            _label_counts[lbl] = _label_counts.get(lbl, 0) + 1
        for f in _item_fields:
            lbl = _field_label(spec, f)
            if _label_counts.get(lbl, 0) > 1:
                # append the distinguishing part of the field name
                import re as _re
                suffix = _re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ",
                                 f.replace("_", " ")).strip()
                lbl = f"{lbl} ({suffix})"
            item_cols += (
                '                                        <Column><Text text="'
                + lbl + '"/></Column>\n'
            )
            item_cells += (
                '                                            <Text text="{' + f + '}"/>\n'
            )
        # The table binds to the navigation property relative to the object
        # context, so UI5 issues an $expand/related read for the children.
        nav = spec.items_nav_name or spec.items_entity_set
        items_section = (
            '            <uxap:ObjectPageSection title="{i18n>itemsTitle}">\n'
            '                <uxap:subSections>\n'
            '                    <uxap:ObjectPageSubSection>\n'
            '                        <uxap:blocks>\n'
            '                            <Table id="itemsTable" items="{' + nav + '}"\n'
            '                                   noDataText="{i18n>noDataText}" growing="true">\n'
            '                                <columns>\n'
            + item_cols +
            '                                </columns>\n'
            '                                <items>\n'
            '                                    <ColumnListItem>\n'
            '                                        <cells>\n'
            + item_cells +
            '                                        </cells>\n'
            '                                    </ColumnListItem>\n'
            '                                </items>\n'
            '                            </Table>\n'
            '                        </uxap:blocks>\n'
            '                    </uxap:ObjectPageSubSection>\n'
            '                </uxap:subSections>\n'
            '            </uxap:ObjectPageSection>\n'
        )

    return (
        '<mvc:View\n'
        '    controllerName="' + ctrl + '"\n'
        '    xmlns:mvc="sap.ui.core.mvc"\n'
        '    xmlns="sap.m"\n'
        '    xmlns:uxap="sap.uxap"\n'
        '    xmlns:f="sap.ui.layout.form"\n'
        '    displayBlock="true">\n'
        '    <uxap:ObjectPageLayout id="objectPage" showTitleInHeaderContent="true"\n'
        '                           upperCaseAnchorBar="false">\n'
        '        <uxap:headerTitle>\n'
        '            <uxap:ObjectPageHeader '
        'objectTitle="{' + title + '}" '
        'objectSubtitle="' + _entity_label(spec) + ' {' + key + '}">\n'
        '                <uxap:actions>\n'
        '                    <uxap:ObjectPageHeaderActionButton icon="sap-icon://nav-back" '
        'text="{i18n>backButtonText}" press=".onNavBack"/>\n'
        '                </uxap:actions>\n'
        '            </uxap:ObjectPageHeader>\n'
        '        </uxap:headerTitle>\n'
        + header_status +
        '        <uxap:sections>\n'
        '            <uxap:ObjectPageSection title="{i18n>generalInfoTitle}">\n'
        '                <uxap:subSections>\n'
        '                    <uxap:ObjectPageSubSection>\n'
        '                        <uxap:blocks>\n'
        '                            <f:SimpleForm editable="false" layout="ResponsiveGridLayout">\n'
        '                                <f:content>\n'
        + form_rows +
        '                                </f:content>\n'
        '                            </f:SimpleForm>\n'
        '                        </uxap:blocks>\n'
        '                    </uxap:ObjectPageSubSection>\n'
        '                </uxap:subSections>\n'
        '            </uxap:ObjectPageSection>\n'
        + items_section +
        '        </uxap:sections>\n'
        '    </uxap:ObjectPageLayout>\n'
        '</mvc:View>\n'
    )


def _make_object_page_controller(spec: AppSpec) -> str:
    """Controller for the object page: binds the view to a single entity by key
    on RouteObject pattern match (with $expand of the child navigation so the
    items table is populated in one read), plus nav-back and the status
    formatter."""
    extend = spec.app_id + ".controller.Object"
    entity = spec.entity_set
    key = spec.key_field
    nav = spec.items_nav_name or spec.items_entity_set

    expand = ('            var mParams = {};\n'
              '            mParams.expand = "' + nav + '";\n') if spec.has_items() else \
             ('            var mParams = {};\n')

    return (
        'sap.ui.define([\n'
        '    "sap/ui/core/mvc/Controller",\n'
        '    "sap/m/MessageBox"\n'
        '], function (Controller, MessageBox) {\n'
        '    "use strict";\n\n'
        '    return Controller.extend("' + extend + '", {\n\n'
        '        onInit: function () {\n'
        '            this.getOwnerComponent().getRouter()\n'
        '                .getRoute("RouteObject")\n'
        '                .attachPatternMatched(this._onObjectMatched, this);\n'
        '        },\n\n'
        '        /**\n'
        '         * Bind the view to the selected entity (with $expand of the\n'
        '         * child navigation so the items table loads in the same read).\n'
        '         * @param {sap.ui.base.Event} oEvent - The route-matched event\n'
        '         * @private\n'
        '         */\n'
        '        _onObjectMatched: function (oEvent) {\n'
        '            var sId    = decodeURIComponent(oEvent.getParameter("arguments").objectId);\n'
        '            var oModel = this.getOwnerComponent().getModel();\n'
        '            if (!oModel) { return; }\n'
        '            var sPath  = "/" + oModel.createKey("' + entity + '", (function () {\n'
        '                var o = {}; o["' + key + '"] = sId; return o;\n'
        '            })());\n'
        + expand +
        '            this.getView().bindElement({ path: sPath, parameters: mParams });\n'
        '        },\n\n'
        + _status_formatter_js(spec) +
        '        onNavBack: function () {\n'
        '            this.getOwnerComponent().getRouter().navTo("RouteMain", {}, true);\n'
        '        }\n\n'
        '    });\n'
        '});\n'
    )


def _make_list_detail_view(spec: AppSpec) -> str:
    """ListDetail: List view with ObjectListItem. Detail on item press."""
    ctrl    = spec.app_id + ".controller.Main"
    binding = "{/" + spec.entity_set + "}"
    status_field = (spec.plan_hints or {}).get("status_field", "")

    # Title = the meaningful title field; intro = key only if it differs from
    # the title (otherwise intro would duplicate the title). Remaining fields
    # render as ObjectAttributes so the row shows real data, not just the key.
    title_field = spec.title_field
    intro_line = ""
    if spec.key_field and spec.key_field != title_field:
        intro_line = '                        intro="{' + spec.key_field + '}"\n'

    # Fields to show as attributes: everything except the ones already used as
    # title/intro. The status field (if any) becomes a firstStatus ObjectStatus.
    used = {title_field, spec.key_field}
    attr_fields = [f for f in spec.all_fields()
                   if f and f not in used and f != status_field]

    attributes = ""
    if attr_fields:
        attributes += '                        <attributes>\n'
        for f in attr_fields:
            attributes += (
                '                            <ObjectAttribute text="{' + f + '}"/>\n'
            )
        attributes += '                        </attributes>\n'

    first_status = ""
    if status_field:
        first_status = (
            '                        <firstStatus>\n'
            '                            <ObjectStatus\n'
            '                                text="{' + status_field + '}"\n'
            '                                state="{ path: \'' + status_field + '\', formatter: \'.formatStatusState\' }"/>\n'
            '                        </firstStatus>\n'
        )

    object_list_item = (
        '                    <ObjectListItem\n'
        '                        title="{' + title_field + '}"\n'
        + intro_line +
        '                        type="Active"\n'
        '                        press=".onItemPress">\n'
        + first_status
        + attributes +
        '                    </ObjectListItem>\n'
    )

    return (
        '<mvc:View\n'
        '    controllerName="' + ctrl + '"\n'
        '    xmlns:mvc="sap.ui.core.mvc"\n'
        '    xmlns="sap.m"\n'
        '    displayBlock="true">\n'
        '    <Page title="{i18n>listTitle}">\n'
        '        <headerContent>\n'
        '            <Button\n'
        '                text="{i18n>createButtonText}"\n'
        '                press=".onCreatePress"\n'
        '                type="Emphasized"/>\n'
        '        </headerContent>\n'
        '        <subHeader>\n'
        '            <Bar>\n'
        '                <contentMiddle>\n'
        '                    <SearchField\n'
        '                        placeholder="{i18n>searchPlaceholder}"\n'
        '                        search=".onSearch"\n'
        '                        liveChange=".onSearch"\n'
        '                        width="100%"/>\n'
        '                </contentMiddle>\n'
        '            </Bar>\n'
        '        </subHeader>\n'
        '        <content>\n'
        '            <List\n'
        '                id="itemsList"\n'
        '                items="' + binding + '"\n'
        '                noDataText="{i18n>noDataText}"\n'
        '                mode="None">\n'
        '                <items>\n'
        + object_list_item +
        '                </items>\n'
        '            </List>\n'
        '        </content>\n'
        '    </Page>\n'
        '</mvc:View>\n'
    )


def _make_worklist_view(spec: AppSpec) -> str:
    """Worklist: Table with columns, filter bar, and pagination."""
    ctrl    = spec.app_id + ".controller.Main"
    binding = "{/" + spec.entity_set + "}"

    # Build column headers and cells from all fields. When the metadata-driven
    # plan flagged a status field (a low-cardinality value like Open/Closed),
    # render it as a colored ObjectStatus via a formatter instead of plain text.
    status_field = (spec.plan_hints or {}).get("status_field", "")
    columns = ""
    cells   = ""
    for f in spec.all_fields():
        col_key = "column_" + f.lower()
        columns += (
            '                    <Column>\n'
            '                        <Text text="{i18n>' + col_key + '}"/>\n'
            '                    </Column>\n'
        )
        if f and f == status_field:
            cells += (
                '                        <ObjectStatus\n'
                '                            text="{' + f + '}"\n'
                '                            state="{ path: \'' + f + '\', formatter: \'.formatStatusState\' }"/>\n'
            )
        else:
            cells += (
                '                        <Text text="{' + f + '}"/>\n'
            )

    return (
        '<mvc:View\n'
        '    controllerName="' + ctrl + '"\n'
        '    xmlns:mvc="sap.ui.core.mvc"\n'
        '    xmlns="sap.m"\n'
        '    xmlns:semantic="sap.f.semantic"\n'
        '    displayBlock="true">\n'
        '    <Page title="{i18n>listTitle}">\n'
        '        <headerContent>\n'
        '            <Button\n'
        '                text="{i18n>createButtonText}"\n'
        '                press=".onCreatePress"\n'
        '                type="Emphasized"/>\n'
        '        </headerContent>\n'
        '        <subHeader>\n'
        '            <Bar>\n'
        '                <contentMiddle>\n'
        '                    <SearchField\n'
        '                        id="searchField"\n'
        '                        placeholder="{i18n>searchPlaceholder}"\n'
        '                        search=".onSearch"\n'
        '                        liveChange=".onSearch"\n'
        '                        width="100%"/>\n'
        '                </contentMiddle>\n'
        '            </Bar>\n'
        '        </subHeader>\n'
        '        <content>\n'
        '            <Table\n'
        '                id="itemsList"\n'
        '                items="' + binding + '"\n'
        '                noDataText="{i18n>noDataText}"\n'
        '                growing="true"\n'
        '                growingThreshold="20"\n'
        '                mode="Delete"\n'
        '                delete=".onDeletePress">\n'
        '                <headerToolbar>\n'
        '                    <Toolbar>\n'
        '                        <Title text="{i18n>listTitle}"/>\n'
        '                        <ToolbarSpacer/>\n'
        '                        <Button\n'
        '                            icon="sap-icon://excel-attachment"\n'
        '                            tooltip="Export"\n'
        '                            press=".onExport"/>\n'
        '                    </Toolbar>\n'
        '                </headerToolbar>\n'
        '                <columns>\n'
        + columns +
        '                </columns>\n'
        '                <items>\n'
        '                    <ColumnListItem\n'
        '                        type="Active"\n'
        '                        press=".onItemPress">\n'
        '                        <cells>\n'
        + cells +
        '                        </cells>\n'
        '                    </ColumnListItem>\n'
        '                </items>\n'
        '            </Table>\n'
        '        </content>\n'
        '    </Page>\n'
        '</mvc:View>\n'
    )


def _make_master_detail_view(spec: AppSpec) -> str:
    """
    MasterDetail: SplitApp with master list and detail panel.
    Master shows list, detail shows selected item fields.
    """
    ctrl    = spec.app_id + ".controller.Main"
    binding = "{/" + spec.entity_set + "}"

    # Detail fields (rendered as clean label/value rows in a SimpleForm)
    status_field = (spec.plan_hints or {}).get("status_field", "")
    detail_content = ""
    for f in spec.all_fields():
        detail_content += (
            '                        <Label text="{i18n>column_' + f.lower() + '}"/>\n'
        )
        if f and f == status_field:
            detail_content += (
                '                        <ObjectStatus text="{' + f + '}" '
                'state="{ path: \'' + f + '\', formatter: \'.formatStatusState\' }"/>\n'
            )
        else:
            detail_content += (
                '                        <Text text="{' + f + '}"/>\n'
            )

    return (
        '<mvc:View\n'
        '    controllerName="' + ctrl + '"\n'
        '    xmlns:mvc="sap.ui.core.mvc"\n'
        '    xmlns="sap.m"\n'
        '    xmlns:l="sap.ui.layout"\n'
        '    xmlns:f="sap.ui.layout.form"\n'
        '    displayBlock="true">\n'
        '    <SplitApp id="splitApp">\n'
        '        <!-- Master panel -->\n'
        '        <masterPages>\n'
        '            <Page\n'
        '                id="masterPage"\n'
        '                title="{i18n>listTitle}"\n'
        '                showNavButton="false">\n'
        '                <headerContent>\n'
        '                    <Button\n'
        '                        text="{i18n>createButtonText}"\n'
        '                        press=".onCreatePress"\n'
        '                        type="Emphasized"/>\n'
        '                </headerContent>\n'
        '                <subHeader>\n'
        '                    <Bar>\n'
        '                        <contentMiddle>\n'
        '                            <SearchField\n'
        '                                placeholder="{i18n>searchPlaceholder}"\n'
        '                                search=".onSearch"\n'
        '                                liveChange=".onSearch"\n'
        '                                width="100%"/>\n'
        '                        </contentMiddle>\n'
        '                    </Bar>\n'
        '                </subHeader>\n'
        '                <content>\n'
        '                    <List\n'
        '                        id="itemsList"\n'
        '                        items="' + binding + '"\n'
        '                        noDataText="{i18n>noDataText}"\n'
        '                        mode="SingleSelectMaster"\n'
        '                        selectionChange=".onMasterSelect">\n'
        '                        <items>\n'
        '                            <StandardListItem\n'
        '                                title="{' + spec.title_field + '}"\n'
        '                                description="{' + spec.key_field + '}"\n'
        '                                type="Active"/>\n'
        '                        </items>\n'
        '                    </List>\n'
        '                </content>\n'
        '            </Page>\n'
        '        </masterPages>\n'
        '        <!-- Detail panel -->\n'
        '        <detailPages>\n'
        '            <Page\n'
        '                id="detailPage"\n'
        '                title="{i18n>detailTitle}"\n'
        '                showNavButton="true"\n'
        '                navButtonPress=".onNavBack">\n'
        '                <headerContent>\n'
        '                    <Button\n'
        '                        text="{i18n>deleteButtonText}"\n'
        '                        press=".onDeleteSelected"\n'
        '                        type="Reject"\n'
        '                        visible="{viewModel>/itemSelected}"/>\n'
        '                </headerContent>\n'
        '                <content>\n'
        '                    <ObjectHeader\n'
        '                        title="{' + spec.title_field + '}"\n'
        '                        number="{' + spec.key_field + '}"\n'
        '                        responsive="true"\n'
        '                        visible="{viewModel>/itemSelected}"\n'
        '                        class="sapUiSmallMarginBottom"/>\n'
        '                    <f:SimpleForm\n'
        '                        visible="{viewModel>/itemSelected}"\n'
        '                        editable="false"\n'
        '                        layout="ResponsiveGridLayout"\n'
        '                        labelSpanXL="3" labelSpanL="3" labelSpanM="4" labelSpanS="12"\n'
        '                        adjustLabelSpan="false"\n'
        '                        columnsXL="1" columnsL="1" columnsM="1"\n'
        '                        class="sapUiSmallMarginTop sapUiSmallMarginBegin">\n'
        '                        <f:content>\n'
        + detail_content +
        '                        </f:content>\n'
        '                    </f:SimpleForm>\n'
        '                    <VBox\n'
        '                        visible="{= !${viewModel>/itemSelected} }"\n'
        '                        class="sapUiLargeMarginTop"\n'
        '                        alignItems="Center">\n'
        '                        <items>\n'
        '                            <IllustratedMessage\n'
        '                                enableVerticalResponsiveness="true"\n'
        '                                illustrationType="sapIllus-NoData"\n'
        '                                title="{i18n>noDataText}"\n'
        '                                description="Select an item from the list"/>\n'
        '                        </items>\n'
        '                    </VBox>\n'
        '                </content>\n'
        '            </Page>\n'
        '            <!-- Empty detail for initial state -->\n'
        '            <Page id="detailPageEmpty" title="{i18n>detailTitle}">\n'
        '                <content>\n'
        '                    <Text\n'
        '                        text="Select an item from the list"\n'
        '                        class="sapUiLargeMarginTop sapUiSmallMarginBegin"/>\n'
        '                </content>\n'
        '            </Page>\n'
        '        </detailPages>\n'
        '    </SplitApp>\n'
        '</mvc:View>\n'
    )


def _make_single_page_view(spec: AppSpec) -> str:
    """SinglePage: Object page layout with all fields displayed."""
    ctrl = spec.app_id + ".controller.Main"

    # Build form fields
    form_content = ""
    for f in spec.all_fields():
        form_content += (
            '                    <Label text="{i18n>column_' + f.lower() + '}"/>\n'
            '                    <Input\n'
            '                        value="{' + f + '}"\n'
            '                        editable="{viewModel>/editable}"/>\n'
        )

    return (
        '<mvc:View\n'
        '    controllerName="' + ctrl + '"\n'
        '    xmlns:mvc="sap.ui.core.mvc"\n'
        '    xmlns="sap.m"\n'
        '    xmlns:f="sap.ui.layout.form"\n'
        '    displayBlock="true">\n'
        '    <Page title="{i18n>pageTitle}">\n'
        '        <headerContent>\n'
        '            <Button\n'
        '                text="{i18n>editButtonText}"\n'
        '                press=".onEditPress"\n'
        '                visible="{= !${viewModel>/editable} }"\n'
        '                type="Emphasized"/>\n'
        '            <Button\n'
        '                text="{i18n>saveButtonText}"\n'
        '                press=".onSavePress"\n'
        '                visible="{viewModel>/editable}"\n'
        '                type="Emphasized"/>\n'
        '            <Button\n'
        '                text="{i18n>cancelButtonText}"\n'
        '                press=".onCancelPress"\n'
        '                visible="{viewModel>/editable}"/>\n'
        '        </headerContent>\n'
        '        <subHeader>\n'
        '            <Bar>\n'
        '                <contentMiddle>\n'
        '                    <SearchField\n'
        '                        placeholder="{i18n>searchPlaceholder}"\n'
        '                        search=".onSearch"\n'
        '                        width="100%"/>\n'
        '                </contentMiddle>\n'
        '            </Bar>\n'
        '        </subHeader>\n'
        '        <content>\n'
        '            <f:SimpleForm\n'
        '                id="mainForm"\n'
        '                editable="{viewModel>/editable}"\n'
        '                layout="ResponsiveGridLayout"\n'
        '                labelSpanXL="4" labelSpanL="4" labelSpanM="4" labelSpanS="12"\n'
        '                adjustLabelSpan="false"\n'
        '                emptySpanXL="0" emptySpanL="0" emptySpanM="0"\n'
        '                columnsXL="2" columnsL="2" columnsM="1"\n'
        '                class="sapUiSmallMarginTop">\n'
        '                <f:content>\n'
        + form_content +
        '                </f:content>\n'
        '            </f:SimpleForm>\n'
        '        </content>\n'
        '    </Page>\n'
        '</mvc:View>\n'
    )


def _make_view_by_type(spec: AppSpec) -> str:
    """Route to the correct view generator based on app_type."""
    generators = {
        "ListDetail":   _make_list_detail_view,
        "Worklist":     _make_worklist_view,
        "MasterDetail": _make_master_detail_view,
        "SinglePage":   _make_single_page_view,
        "ObjectPage":   _make_list_detail_view,  # Main = the list; Object.view is the OP
    }
    fn = generators.get(spec.app_type, _make_list_detail_view)
    return fn(spec)


def _make_controller_by_type(spec: AppSpec) -> str:
    """
    Generate controller with app-type specific handlers.
    Core handlers (onSearch, onDelete, _handleError) are always present.
    MasterDetail adds onMasterSelect, onNavBack, onDeleteSelected.
    SinglePage adds onEditPress.
    Worklist adds onExport.
    """
    extend  = spec.app_id + ".controller.Main"
    entity  = spec.entity_set
    key     = spec.key_field
    title   = spec.title_field
    app_type = spec.app_type

    # ── Base controller (common to all types) ────────────────────────
    base = (
        'sap.ui.define([\n'
        '    "sap/ui/core/mvc/Controller",\n'
        '    "sap/ui/model/json/JSONModel",\n'
        '    "sap/m/MessageBox",\n'
        '    "sap/m/MessageToast",\n'
        '    "sap/ui/model/Filter",\n'
        '    "sap/ui/model/FilterOperator",\n'
        '    "sap/ui/core/Fragment"\n'
        '], function (Controller, JSONModel, MessageBox, MessageToast, Filter, FilterOperator, Fragment) {\n'
        '    "use strict";\n\n'
        '    return Controller.extend("' + extend + '", {\n\n'
    )

    # ── onInit — varies by type ──────────────────────────────────────
    if app_type == "MasterDetail":
        on_init = (
            '        onInit: function () {\n'
            '            var oRouter = this.getOwnerComponent().getRouter();\n'
            '            oRouter.getRoute("RouteMain").attachPatternMatched(this._onRouteMatched, this);\n'
            '            this.getView().setModel(new JSONModel({\n'
            '                busy: false, delay: 0, itemSelected: false\n'
            '            }), "viewModel");\n'
            '        },\n\n'
        )
    elif app_type == "SinglePage":
        on_init = (
            '        onInit: function () {\n'
            '            var oRouter = this.getOwnerComponent().getRouter();\n'
            '            oRouter.getRoute("RouteMain").attachPatternMatched(this._onRouteMatched, this);\n'
            '            this.getView().setModel(new JSONModel({\n'
            '                busy: false, delay: 0, editable: false\n'
            '            }), "viewModel");\n'
            '        },\n\n'
        )
    else:
        on_init = (
            '        onInit: function () {\n'
            '            var oRouter = this.getOwnerComponent().getRouter();\n'
            '            oRouter.getRoute("RouteMain").attachPatternMatched(this._onRouteMatched, this);\n'
            '            this.getView().setModel(new JSONModel({\n'
            '                busy: false, delay: 0, listTitle: ""\n'
            '            }), "viewModel");\n'
            '        },\n\n'
        )

    # ── _onRouteMatched ──────────────────────────────────────────────
    route_matched = (
        '        _onRouteMatched: function () {\n'
        '            var oList = this.byId("itemsList");\n'
        '            if (oList) {\n'
        '                var oBinding = oList.getBinding("items");\n'
        '                if (oBinding) { oBinding.refresh(); }\n'
        '            }\n'
        '        },\n\n'
    )

    # ── onSearch ─────────────────────────────────────────────────────
    # Search across ALL text fields (key + string fields), not just the title,
    # so typing matches any visible column. Non-text fields (dates, numbers,
    # booleans) are skipped because substringof only applies to strings.
    search_fields = [f for f in spec.all_fields()
                     if f == spec.key_field or _edm_type(f) == "Edm.String"]
    if not search_fields:
        search_fields = [title]
    _filters_js = ",\n".join(
        '                        new Filter("' + f + '", FilterOperator.Contains, q)'
        for f in search_fields
    )
    on_search = (
        '        onSearch: function (oEvent) {\n'
        '            var sQuery = oEvent.getParameter("query");\n'
        '            if (sQuery === undefined || sQuery === null) {\n'
        '                sQuery = oEvent.getParameter("newValue");\n'
        '            }\n'
        '            sQuery = (sQuery || "").trim();\n'
        '            var oList = this.byId("itemsList");\n'
        '            if (!oList) { return; }\n'
        '            var oBinding = oList.getBinding("items");\n'
        '            if (!oBinding) { return; }\n'
        '            var aFilters = [];\n'
        '            if (sQuery) {\n'
        '                var q = sQuery;\n'
        '                aFilters = [new Filter({\n'
        '                    filters: [\n'
        + _filters_js + '\n'
        '                    ],\n'
        '                    and: false\n'
        '                })];\n'
        '            }\n'
        '            oBinding.filter(aFilters);\n'
        '        },\n\n'
    )

    # ── onItemPress ──────────────────────────────────────────────────
    if app_type == "ListDetail":
        on_item_press = (
            '        onItemPress: function (oEvent) {\n'
            '            var oCtx = oEvent.getSource().getBindingContext();\n'
            '            if (!oCtx) { return; }\n'
            '            var sId = oCtx.getProperty("' + key + '");\n'
            '            this.getOwnerComponent().getRouter().navTo("RouteDetail", {\n'
            '                objectId: encodeURIComponent(sId)\n'
            '            });\n'
            '        },\n\n'
        )
    elif app_type == "ObjectPage":
        on_item_press = (
            '        onItemPress: function (oEvent) {\n'
            '            var oCtx = oEvent.getSource().getBindingContext();\n'
            '            if (!oCtx) { return; }\n'
            '            var sId = oCtx.getProperty("' + key + '");\n'
            '            this.getOwnerComponent().getRouter().navTo("RouteObject", {\n'
            '                objectId: encodeURIComponent(sId)\n'
            '            });\n'
            '        },\n\n'
        )
    else:
        on_item_press = (
            '        onItemPress: function (oEvent) {\n'
            '            var oCtx = oEvent.getSource().getBindingContext();\n'
            '            if (!oCtx) { return; }\n'
            '            MessageToast.show("Selected: " + (oCtx.getProperty("' + title + '") || oCtx.getProperty("' + key + '")));\n'
            '        },\n\n'
        )

    # ── onCreatePress ────────────────────────────────────────────────
    on_create = (
        '        onCreatePress: function () {\n'
        '            var oViewModel = this.getView().getModel("viewModel");\n'
        '            if (oViewModel) { oViewModel.setProperty("/NewItem", {}); }\n'
        '            this._openDialog();\n'
        '        },\n\n'
    )

    # ── onDeletePress (List mode=Delete) ─────────────────────────────
    on_delete = (
        '        onDeletePress: function (oEvent) {\n'
        '            var oItem  = oEvent.getParameter("listItem");\n'
        '            var oCtx   = oItem ? oItem.getBindingContext() : null;\n'
        '            if (!oCtx) { return; }\n'
        '            var sName  = oCtx.getProperty("' + title + '") || oCtx.getProperty("' + key + '") || "item";\n'
        '            var oModel = this.getView().getModel();\n'
        '            MessageBox.confirm("Delete \\"" + sName + "\\"?", {\n'
        '                title: "Confirm Delete",\n'
        '                onClose: function (sAction) {\n'
        '                    if (sAction === MessageBox.Action.OK) {\n'
        '                        if (oModel) {\n'
        '                            oModel.remove(oCtx.getPath(), {\n'
        '                                success: function () { MessageToast.show("Deleted."); },\n'
        '                                error: this._handleError.bind(this)\n'
        '                            });\n'
        '                        } else {\n'
        '                            MessageBox.information("Connect OData service to enable delete.");\n'
        '                        }\n'
        '                    }\n'
        '                }.bind(this)\n'
        '            });\n'
        '        },\n\n'
    )

    # ── onSavePress ──────────────────────────────────────────────────
    # Build type coercion so values match the OData/metadata types (a text
    # value in a Decimal/DateTime field otherwise makes the mock/back end fail).
    _coerce = ""
    for f in spec.all_fields():
        if f == spec.key_field:
            continue
        edm = _edm_type(f)
        if edm == "Edm.Decimal":
            _coerce += (
                '            if (oNewItem["' + f + '"] !== undefined) {\n'
                '                var _d = parseFloat(oNewItem["' + f + '"]);\n'
                '                oNewItem["' + f + '"] = isNaN(_d) ? "0" : String(_d);\n'
                '            }\n'
            )
        elif edm == "Edm.Int32":
            _coerce += (
                '            if (oNewItem["' + f + '"] !== undefined) {\n'
                '                var _i = parseInt(oNewItem["' + f + '"], 10);\n'
                '                oNewItem["' + f + '"] = isNaN(_i) ? 0 : _i;\n'
                '            }\n'
            )
        elif edm == "Edm.DateTime":
            _coerce += (
                '            if (oNewItem["' + f + '"]) {\n'
                '                var _dt = new Date(oNewItem["' + f + '"]);\n'
                '                oNewItem["' + f + '"] = isNaN(_dt.getTime()) ? new Date() : _dt;\n'
                '            } else {\n'
                '                oNewItem["' + f + '"] = new Date();\n'
                '            }\n'
            )
        elif edm == "Edm.Boolean":
            _coerce += (
                '            oNewItem["' + f + '"] = (oNewItem["' + f + '"] === true ||\n'
                '                oNewItem["' + f + '"] === "true");\n'
            )
    on_save = (
        '        onSavePress: function () {\n'
        '            var oModel    = this.getView().getModel();\n'
        '            var oViewModel = this.getView().getModel("viewModel");\n'
        '            var oNewItem  = oViewModel ? oViewModel.getProperty("/NewItem") : null;\n'
        '            if (!oModel || !oNewItem) {\n'
        '                MessageBox.information("Connect OData service to enable save.");\n'
        '                return;\n'
        '            }\n'
        '            // Key handling: never fabricate a key. If the user left the\n'
        '            // key empty, remove it from the payload so the backend can\n'
        '            // assign it (SAP services like A_SalesOrder generate the key\n'
        '            // server-side and REJECT a client-supplied value). Only send\n'
        '            // the key when the user actually typed one (user-keyed sets).\n'
        '            if (!oNewItem["' + key + '"] || String(oNewItem["' + key + '"]).trim() === "") {\n'
        '                delete oNewItem["' + key + '"];\n'
        '            }\n'
        '            // Coerce each field to its metadata type\n'
        + _coerce +
        '            oModel.create("/' + entity + '", oNewItem, {\n'
        '                success: function () {\n'
        '                    MessageToast.show("Saved successfully.");\n'
        '                    this._closeDialog();\n'
        '                    var oList = this.byId("itemsList");\n'
        '                    if (oList && oList.getBinding("items")) {\n'
        '                        oList.getBinding("items").refresh();\n'
        '                    }\n'
        '                }.bind(this),\n'
        '                error: this._handleError.bind(this)\n'
        '            });\n'
        '        },\n\n'
    )

    # ── onCancelPress ────────────────────────────────────────────────
    on_cancel = (
        '        onCancelPress: function () {\n'
        '            var oModel = this.getView().getModel();\n'
        '            if (oModel && oModel.hasPendingChanges()) { oModel.resetChanges(); }\n'
        '            this._closeDialog();\n'
        '        },\n\n'
    )

    # ── MasterDetail-specific handlers ───────────────────────────────
    master_detail_handlers = ""
    if app_type == "MasterDetail":
        master_detail_handlers = (
            '        onMasterSelect: function (oEvent) {\n'
            '            var oItem = oEvent.getParameter("listItem");\n'
            '            if (!oItem) { return; }\n'
            '            var oCtx = oItem.getBindingContext();\n'
            '            if (!oCtx) { return; }\n'
            '            var oViewModel = this.getView().getModel("viewModel");\n'
            '            if (oViewModel) { oViewModel.setProperty("/itemSelected", true); }\n'
            '            var oSplitApp = this.byId("splitApp");\n'
            '            var oDetailPage = this.byId("detailPage");\n'
            '            if (oDetailPage) { oDetailPage.bindElement(oCtx.getPath()); }\n'
            '            if (oSplitApp) { oSplitApp.toDetail(this.byId("detailPage")); }\n'
            '        },\n\n'
            '        onDeleteSelected: function () {\n'
            '            var oDetailPage = this.byId("detailPage");\n'
            '            var oCtx = oDetailPage ? oDetailPage.getBindingContext() : null;\n'
            '            if (!oCtx) { return; }\n'
            '            var sName = oCtx.getProperty("' + title + '") || "item";\n'
            '            var oModel = this.getView().getModel();\n'
            '            MessageBox.confirm("Delete \\"" + sName + "\\"?", {\n'
            '                onClose: function (sAction) {\n'
            '                    if (sAction === MessageBox.Action.OK && oModel) {\n'
            '                        oModel.remove(oCtx.getPath(), {\n'
            '                            success: function () {\n'
            '                                MessageToast.show("Deleted.");\n'
            '                                this.getView().getModel("viewModel").setProperty("/itemSelected", false);\n'
            '                            }.bind(this),\n'
            '                            error: this._handleError.bind(this)\n'
            '                        });\n'
            '                    }\n'
            '                }.bind(this)\n'
            '            });\n'
            '        },\n\n'
            '        onNavBack: function () {\n'
            '            var oHistory = sap.ui.require("sap/ui/core/routing/History");\n'
            '            if (oHistory) {\n'
            '                var sPreviousHash = oHistory.getInstance().getPreviousHash();\n'
            '                if (sPreviousHash !== undefined) {\n'
            '                    window.history.go(-1);\n'
            '                    return;\n'
            '                }\n'
            '            }\n'
            '            this.getOwnerComponent().getRouter().navTo("RouteMain");\n'
            '        },\n\n'
        )

    # ── SinglePage-specific handlers ─────────────────────────────────
    single_page_handlers = ""
    if app_type == "SinglePage":
        single_page_handlers = (
            '        onEditPress: function () {\n'
            '            this.getView().getModel("viewModel").setProperty("/editable", true);\n'
            '        },\n\n'
        )

    # ── Worklist-specific handlers ───────────────────────────────────
    worklist_handlers = ""
    if app_type == "Worklist":
        worklist_handlers = (
            '        onExport: function () {\n'
            '            MessageBox.information("Export: connect OData service to enable export.");\n'
            '        },\n\n'
        )

    # ── Dialog helpers ───────────────────────────────────────────────
    dialog_helpers = (
        '        _openDialog: function () {\n'
        '            var oView = this.getView();\n'
        '            if (!this._pDialog) {\n'
        '                this._pDialog = Fragment.load({\n'
        '                    id: oView.getId(),\n'
        '                    name: "' + spec.app_id + '.fragment.DetailDialog",\n'
        '                    controller: this\n'
        '                }).then(function (oDialog) {\n'
        '                    oView.addDependent(oDialog);\n'
        '                    return oDialog;\n'
        '                });\n'
        '            }\n'
        '            this._pDialog.then(function (oDialog) {\n'
        '                oDialog.open();\n'
        '            });\n'
        '        },\n\n'
        '        _closeDialog: function () {\n'
        '            if (this._pDialog) {\n'
        '                this._pDialog.then(function (oDialog) {\n'
        '                    oDialog.close();\n'
        '                });\n'
        '            }\n'
        '        },\n\n'
    )

    # ── Error handler ────────────────────────────────────────────────
    error_handler = (
        '        _handleError: function (oError) {\n'
        '            var sMessage = "An unexpected error occurred.";\n'
        '            try {\n'
        '                var oBody = JSON.parse(oError.responseText || "{}");\n'
        '                sMessage = (oBody.error && oBody.error.message && oBody.error.message.value)\n'
        '                         ? oBody.error.message.value : sMessage;\n'
        '            } catch (e) { /* ignore */ }\n'
        '            MessageBox.error(sMessage, { title: "Error" });\n'
        '        }\n\n'
    )

    return (
        base
        + on_init
        + route_matched
        + on_search
        + on_item_press
        + on_create
        + on_delete
        + on_save
        + on_cancel
        + master_detail_handlers
        + single_page_handlers
        + worklist_handlers
        + dialog_helpers
        + _status_formatter_js(spec)
        + error_handler
        + '    });\n'
        + '});\n'
    )


def _make_manifest_by_type(spec: AppSpec) -> str:
    """
    Build manifest with routing config appropriate for the app type.
    MasterDetail gets master/detail targets.
    Others get single RouteMain target.
    """
    svc = spec.svc_url()

    # Semantic object for the launchpad tile intent (alphanumeric only)
    sem_obj = re.sub(r"[^A-Za-z0-9]", "", _entity_type_name(spec)) or "App"

    # Base routing config
    routing_config = {
        "routerClass": "sap.m.routing.Router",
        "viewType": "XML",
        "viewPath": spec.app_id + ".view",
        "controlId": "app",
        "controlAggregation": "pages",
        "async": True
    }

    if spec.app_type == "MasterDetail":
        routes = [
            {"name": "RouteMain", "pattern": "", "target": ["TargetMaster", "TargetDetail"]}
        ]
        targets = {
            "TargetMaster": {
                "viewType": "XML",
                "viewName": "Main",
                "controlId": "splitApp",
                "controlAggregation": "masterPages"
            },
            "TargetDetail": {
                "viewType": "XML",
                "viewName": "Main",
                "controlId": "splitApp",
                "controlAggregation": "detailPages"
            }
        }
        # MasterDetail uses a different approach — single view with SplitApp
        routes = [{"name": "RouteMain", "pattern": "", "target": ["TargetMain"]}]
        targets = {"TargetMain": {"viewType": "XML", "viewLevel": 1, "viewName": "Main"}}
    elif spec.app_type == "ListDetail":
        # List + separate detail page reached by clicking an item
        routes = [
            {"name": "RouteMain",   "pattern": "",                  "target": ["TargetMain"]},
            {"name": "RouteDetail", "pattern": "detail/{objectId}", "target": ["TargetDetail"]}
        ]
        targets = {
            "TargetMain":   {"viewType": "XML", "viewLevel": 1, "viewName": "Main",   "transition": "slide"},
            "TargetDetail": {"viewType": "XML", "viewLevel": 2, "viewName": "Detail", "transition": "slide"}
        }
        if spec.has_items():
            routes.append({"name": "RouteItems",
                           "pattern": "detail/{objectId}/items",
                           "target": ["TargetItems"]})
            targets["TargetItems"] = {"viewType": "XML", "viewLevel": 3,
                                      "viewName": "Items", "transition": "slide"}
    elif spec.app_type == "ObjectPage":
        # List navigates to a full sap.uxap.ObjectPageLayout for one object.
        # The object page binds to a single entity by key and (when a child
        # navigation exists) shows its items via an expand-bound table.
        routes = [
            {"name": "RouteMain",   "pattern": "",                  "target": ["TargetMain"]},
            {"name": "RouteObject", "pattern": "object/{objectId}", "target": ["TargetObject"]},
        ]
        targets = {
            "TargetMain":   {"viewType": "XML", "viewLevel": 1, "viewName": "Main",   "transition": "slide"},
            "TargetObject": {"viewType": "XML", "viewLevel": 2, "viewName": "Object", "transition": "slide"},
        }
    else:
        routes  = [{"name": "RouteMain", "pattern": "", "target": ["TargetMain"]}]
        targets = {"TargetMain": {"viewType": "XML", "viewLevel": 1, "viewName": "Main"}}

    manifest = {
        "_version": "1.58.0",
        "sap.app": {
            "id": spec.app_id,
            "type": "application",
            "applicationVersion": {"version": "0.0.1"},
            "title": "{{i18n>appTitle}}",
            "description": "{{i18n>appDescription}}",
            "dataSources": {
                "mainService": {
                    "uri": svc,
                    "type": "OData",
                    "settings": {"odataVersion": "2.0"}
                }
            },
            "crossNavigation": {
                "inbounds": {
                    sem_obj + "-display": {
                        "semanticObject": sem_obj,
                        "action": "display",
                        "title": spec.app_name,
                        "icon": "sap-icon://list",
                        "signature": {
                            "parameters": {},
                            "additionalParameters": "allowed"
                        }
                    }
                }
            }
        },
        "sap.cloud": {
            "public": True,
            "service": spec.app_id.replace(".", "")
        },
        "sap.ui": {
            "technology": "UI5",
            "deviceTypes": {"desktop": True, "tablet": True, "phone": True}
        },
        "sap.ui5": {
            "rootView": {
                "viewName": spec.app_id + ".view.App",
                "type": "XML",
                "async": True,
                "id": "app"
            },
            "dependencies": {
                "minUI5Version": "1.120.0",
                "libs": ({
                    "sap.m":         {},
                    "sap.ui.core":   {},
                    "sap.f":         {},
                    "sap.ui.layout": {},
                    "sap.uxap":      {}
                } if spec.app_type == "ObjectPage" else {
                    "sap.m":         {},
                    "sap.ui.core":   {},
                    "sap.f":         {},
                    "sap.ui.layout": {}
                })
            },
            "contentDensities": {"compact": True, "cozy": True},
            "models": {
                "": {
                    "dataSource": "mainService",
                    "preload": True,
                    "settings": {
                        "synchronizationMode": "None",
                        "operationMode": "Server",
                        "autoExpandSelect": True,
                        "earlyRequests": True
                    }
                },
                "i18n": {
                    "type": "sap.ui.model.resource.ResourceModel",
                    "settings": {
                        "bundleName": spec.app_id + ".i18n.i18n",
                        "supportedLocales": [""],
                        "fallbackLocale": ""
                    }
                }
            },
            "routing": {
                "config": routing_config,
                "routes": routes,
                "targets": targets
            }
        }
    }

    # ── Multi-service override ───────────────────────────────────────────────
    # Replace the single default dataSource/model with one per configured
    # service. The primary service stays the unnamed default model (""); any
    # additional services become named models (e.g. {products>/Products}).
    # The i18n resource model is preserved.
    svcs = spec.service_list()
    manifest["sap.app"]["dataSources"] = manifest_datasources(svcs)
    _models = manifest_models(svcs)
    _models["i18n"] = manifest["sap.ui5"]["models"]["i18n"]
    manifest["sap.ui5"]["models"] = _models

    return json.dumps(manifest, indent=4)