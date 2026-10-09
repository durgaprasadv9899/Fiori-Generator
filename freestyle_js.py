"""
freestyle_js.py — static JavaScript of generated freestyle SAPUI5 apps.

These controllers/services are generic: everything app-specific (entity set,
columns, filters, value helps, actions, status mapping, KPIs, export columns)
lives in the generated model/appConfig.js and in the XML views. `@@APP@@` is
replaced with the app namespace (e.g. com.evoke.salesorders) and
`@@APPPATH@@` with its module path (com/evoke/salesorders).
"""

COMPONENT = r'''sap.ui.define([
    "sap/ui/core/UIComponent",
    "sap/ui/Device",
    "sap/ui/model/json/JSONModel",
    "./model/appConfig"
], function (UIComponent, Device, JSONModel, appConfig) {
    "use strict";

    return UIComponent.extend("@@APP@@.Component", {
        metadata: {
            manifest: "json",
            interfaces: ["sap.ui.core.IAsyncContentCreation"]
        },

        init: function () {
            UIComponent.prototype.init.apply(this, arguments);
            var oDevice = new JSONModel(Device);
            oDevice.setDefaultBindingMode("OneWay");
            this.setModel(oDevice, "device");
            this.setModel(new JSONModel(appConfig), "config");
            // multi-service apps: the default model is also available under its service alias
            if (appConfig.primaryService) { this.setModel(this.getModel(), appConfig.primaryService); }
            this.setModel(new JSONModel({ layout: "OneColumn", navKey: "",
                sideExpanded: !Device.system.phone }), "appView");
            this.getRouter().initialize();
        },

        getContentDensityClass: function () {
            return Device.support.touch ? "sapUiSizeCozy" : "sapUiSizeCompact";
        }
    });
});
'''

DATA_SERVICE = r'''sap.ui.define([
    "sap/ui/model/Filter",
    "sap/ui/model/FilterOperator",
    "sap/ui/core/format/DateFormat"
], function (Filter, FilterOperator, DateFormat) {
    "use strict";

    // One API over the OData V2 and V4 models, so controllers stay identical.
    var oDateOnly = DateFormat.getDateInstance({ pattern: "yyyy-MM-dd" });

    function isV4(oModel) {
        return !!oModel && oModel.isA("sap.ui.model.odata.v4.ODataModel");
    }

    function v2BatchError(oData) {
        var aResponses = (oData && oData.__batchResponses) || [];
        for (var i = 0; i < aResponses.length; i++) {
            var oResp = aResponses[i];
            if (oResp.response && Number(oResp.response.statusCode) >= 400) {
                return oResp.response;
            }
            var aChanges = oResp.__changeResponses || [];
            for (var j = 0; j < aChanges.length; j++) {
                if (Number(aChanges[j].statusCode) >= 400) {
                    return aChanges[j];
                }
            }
        }
        return null;
    }

    // SAP answers some backend failures with a short dump ("Runtime Error:
    // 'ASSERTION_FAILED' ... abnormally terminated"). Nothing was changed (the
    // request is rolled back); tell the user that instead of the raw dump text.
    var APPROVAL_DUMP = "SAP could not {0} the approval of this document and stopped with a system error " +
        "(ASSERTION_FAILED in the SD approval management). Nothing was changed.\n\n" +
        "The document's approval data in SAP is inconsistent: it is still 'In Approval' although its approval " +
        "workflow was already rejected, cancelled or never started (SAP Knowledge Base Article 3406016). " +
        "This cannot be solved in the app — please ask your SAP administrator to apply the correction from " +
        "KBA 3406016 and repair the document's approval workflow (e.g. 'Manage Workflows for Sales Documents' " +
        "or transaction SWIA). Other documents in approval can still be released normally.";

    function isDump(sText) {
        return /Runtime Error|abnormally terminated|ASSERTION_FAILED/i.test(sText || "");
    }

    function explainDump(sText) {
        var m = /Runtime Error:\s*'?([A-Z_]+)'?/.exec(sText || "");
        if (!m && !/abnormally terminated/i.test(sText || "")) { return sText; }
        return "SAP could not process this request for the selected record and stopped with a " +
            "system error (" + (m ? m[1] : "short dump") + "). Nothing was changed.\n\n" +
            "This is caused by the record's data in SAP (e.g. an inconsistent approval / workflow " +
            "state), not by your input. Please ask your SAP administrator to check transaction " +
            "ST22 for this time stamp.\n\nDetails: " + sText;
    }

    var DataService = {
        isV4: isV4,

        /** One record by path (V2: from the cache when loaded). */
        readObject: function (oModel, sPath) {
            if (isV4(oModel)) { return oModel.bindContext(sPath).requestObject(); }
            var o = oModel.getObject(sPath);
            if (o) { return Promise.resolve(o); }
            return new Promise(function (fnResolve, fnReject) {
                oModel.read(sPath, { success: fnResolve, error: fnReject });
            });
        },

        /** "Set('key')" for a single-key entity (route argument of a detail page). */
        keyPath: function (sSet, sType, v, bV4) {
            var s = String(v);
            if (sType === "Edm.Guid") { s = bV4 ? s : "guid'" + s + "'"; }
            else if (sType === "Edm.Int64") { s = bV4 ? s : s + "L"; }
            else if (!/^Edm\.(Int16|Int32|Byte|SByte)$/.test(sType || "")) {
                s = "'" + encodeURIComponent(s.replace(/'/g, "''")) + "'";
            }
            return sSet + "(" + s + ")";
        },

        create: function (oModel, sEntitySet, oData) {
            if (isV4(oModel)) {
                var oList = oModel.bindList("/" + sEntitySet, undefined, undefined, undefined,
                    { $$updateGroupId: "createGroup" });
                var oContext = oList.create(oData);
                oModel.submitBatch("createGroup");
                return oContext.created().then(function () { return oContext; });
            }
            return new Promise(function (fnResolve, fnReject) {
                oModel.create("/" + sEntitySet, oData, { success: fnResolve, error: fnReject });
            });
        },

        remove: function (oContext) {
            var oModel = oContext.getModel();
            if (isV4(oModel)) {
                return oContext.delete("$auto");
            }
            return new Promise(function (fnResolve, fnReject) {
                oModel.remove(oContext.getPath(), { success: fnResolve, error: fnReject });
            });
        },

        hasChanges: function (oModel) {
            return isV4(oModel) ? oModel.hasPendingChanges("changes") : oModel.hasPendingChanges();
        },

        save: function (oModel) {
            if (isV4(oModel)) {
                return oModel.submitBatch("changes").then(function () {
                    if (oModel.hasPendingChanges("changes")) {
                        var aMsg = sap.ui.getCore().getMessageManager().getMessageModel().getData()
                            .filter(function (m) { return m.getType() === "Error"; });
                        throw new Error(aMsg.length ? aMsg[aMsg.length - 1].getMessage() : "Save failed.");
                    }
                });
            }
            return new Promise(function (fnResolve, fnReject) {
                if (!oModel.hasPendingChanges()) { fnResolve(); return; }
                oModel.submitChanges({
                    success: function (oData) {
                        var oErr = v2BatchError(oData);
                        if (oErr) { fnReject(oErr); } else { fnResolve(oData); }
                    },
                    error: fnReject
                });
            });
        },

        reset: function (oModel) {
            if (isV4(oModel)) { oModel.resetChanges("changes"); } else { oModel.resetChanges(); }
        },

        /** Write several fields of one record and save (V2 / V4). */
        setFields: function (oContext, mValues) {
            var oModel = oContext.getModel();
            var aKeys = Object.keys(mValues || {});
            if (!aKeys.length) { return Promise.resolve(); }
            if (isV4(oModel)) {
                return Promise.all(aKeys.map(function (k) {
                    return oContext.setProperty(k, mValues[k], "$auto");
                }));
            }
            return new Promise(function (fnResolve, fnReject) {
                oModel.update(oContext.getPath(), mValues, { success: fnResolve, error: fnReject });
            });
        },

        /** Update by path (rows read outside a binding, e.g. kanban cards). */
        updatePath: function (oModel, sPath, mValues, oV4Context) {
            if (isV4(oModel)) {
                return DataService.setFields(oV4Context, mValues);
            }
            return new Promise(function (fnResolve, fnReject) {
                oModel.update(sPath, mValues, { success: fnResolve, error: fnReject });
            });
        },

        /** Number of records matching aFilters. */
        count: function (oModel, sEntitySet, aFilters) {
            if (isV4(oModel)) {
                var oList = oModel.bindList("/" + sEntitySet, undefined, undefined, aFilters || [],
                    { $count: true });
                return oList.requestContexts(0, 1).then(function () {
                    return oList.getHeaderContext().getProperty("$count") || 0;
                });
            }
            return new Promise(function (fnResolve, fnReject) {
                oModel.read("/" + sEntitySet + "/$count", {
                    filters: aFilters || [],
                    success: function (v) { fnResolve(Number(v) || 0); },
                    error: fnReject
                });
            });
        },

        /**
         * Read up to mOpts.top rows (+ total) of an entity set. Returns
         * {rows, count, contexts (V4), paths} — paths address each row for updates.
         */
        readRows: function (oModel, sEntitySet, mOpts) {
            mOpts = mOpts || {};
            var iTop = mOpts.top || 500;
            if (isV4(oModel)) {
                var mParams = { $count: true };
                if (mOpts.select && mOpts.select.length) {
                    mParams.$select = mOpts.select.concat(mOpts.keys || []).filter(function (v, i, a) {
                        return a.indexOf(v) === i;
                    }).join(",");
                }
                var oList = oModel.bindList("/" + sEntitySet, undefined, mOpts.sorters || [],
                    mOpts.filters || [], mParams);
                return oList.requestContexts(0, iTop).then(function (aCtx) {
                    var oHeader = oList.getHeaderContext();
                    var iCount = oHeader ? oHeader.getProperty("$count") : aCtx.length;
                    return { rows: aCtx.map(function (c) { return c.getObject(); }), contexts: aCtx,
                             paths: aCtx.map(function (c) { return c.getPath(); }),
                             count: iCount === undefined ? aCtx.length : iCount };
                });
            }
            return new Promise(function (fnResolve, fnReject) {
                var mUrl = { "$top": iTop, "$inlinecount": "allpages" };
                if (mOpts.select && mOpts.select.length) {
                    mUrl.$select = mOpts.select.concat(mOpts.keys || []).filter(function (v, i, a) {
                        return a.indexOf(v) === i;
                    }).join(",");
                }
                oModel.read("/" + sEntitySet, {
                    urlParameters: mUrl, filters: mOpts.filters || [], sorters: mOpts.sorters || [],
                    success: function (oData) {
                        var aRows = oData.results || [];
                        fnResolve({ rows: aRows, contexts: [],
                                    paths: aRows.map(function (r) {
                                        var u = (r.__metadata && r.__metadata.uri) || "";
                                        return u ? "/" + decodeURIComponent(u.split("/").pop()) : "";
                                    }),
                                    count: Number(oData.__count || aRows.length) });
                    },
                    error: fnReject
                });
            });
        },

        /** JS value -> wire value for an EDM type (create payloads / updates). */
        toWire: function (oModel, sEdmType, v) {
            if (v === undefined || v === null) { return v; }
            var v4 = isV4(oModel);
            if (v instanceof Date) {
                if (!v4) {
                    // V2 date-only fields (Edm.DateTime, displayFormat Date) are UTC
                    // midnight: a local-midnight date (util.today/addDays) must keep
                    // its calendar day instead of shifting to the previous UTC day.
                    if (sEdmType === "Edm.DateTime" && v.getHours() === 0 && v.getMinutes() === 0 &&
                            v.getSeconds() === 0 && v.getMilliseconds() === 0) {
                        return new Date(Date.UTC(v.getFullYear(), v.getMonth(), v.getDate()));
                    }
                    return v;
                }
                return sEdmType === "Edm.Date" ? oDateOnly.format(v) : v.toISOString();
            }
            if (typeof v === "number") {
                if (sEdmType === "Edm.Decimal" || sEdmType === "Edm.Int64" || sEdmType === "Edm.String") {
                    return String(v);
                }
            }
            if (typeof v === "boolean" && sEdmType === "Edm.String") { return v ? "X" : ""; }
            return v;
        },

        setField: function (oContext, sField, vValue) {
            var oModel = oContext.getModel();
            if (isV4(oModel)) {
                return oContext.setProperty(sField, vValue, "$auto");
            }
            oModel.setProperty(oContext.getPath() + "/" + sField, vValue);
            return DataService.save(oModel);
        },

        // oOp: {name, bound, importName, namespace, method, keyParams:[...]}
        callOperation: function (oModel, oOp, oContext, mParams) {
            if (isV4(oModel)) {
                var sPath = oOp.bound
                    ? oOp.namespace + "." + oOp.name + "(...)"
                    : "/" + (oOp.importName || oOp.name) + "(...)";
                var oBinding = oModel.bindContext(sPath, oOp.bound ? oContext : undefined);
                Object.keys(mParams || {}).forEach(function (k) { oBinding.setParameter(k, mParams[k]); });
                return (oBinding.invoke || oBinding.execute).call(oBinding);
            }
            var mUrl = Object.assign({}, mParams || {});
            (oOp.keyParams || []).forEach(function (k) {
                if (oContext) { mUrl[k] = oContext.getProperty(k); }
            });
            return new Promise(function (fnResolve, fnReject) {
                oModel.callFunction("/" + oOp.name, {
                    method: oOp.method || "POST", urlParameters: mUrl,
                    success: fnResolve, error: fnReject
                });
            });
        },

        // Filter for one configured filter field and the user's input.
        filterFor: function (oModel, oField, vValue, vValue2) {
            var v4 = isV4(oModel);
            var fnDate = function (d) {
                if (!(d instanceof Date)) { return d; }
                if (!v4) { return d; }
                return oField.type === "Edm.Date" ? oDateOnly.format(d) : d.toISOString();
            };
            if (oField.mode === "range") {
                if (!vValue) { return null; }
                return new Filter(oField.field, FilterOperator.BT, fnDate(vValue), fnDate(vValue2 || vValue));
            }
            if (oField.mode === "multi") {
                if (!vValue || !vValue.length) { return null; }
                return new Filter({
                    filters: vValue.map(function (v) { return new Filter(oField.field, FilterOperator.EQ, v); }),
                    and: false
                });
            }
            if (vValue === undefined || vValue === null || vValue === "") { return null; }
            if (oField.mode === "contains") {
                return this.textFilter(oModel, oField.field, String(vValue));
            }
            return new Filter(oField.field, FilterOperator.EQ, vValue);
        },

        /**
         * Case-insensitive "contains" filter. V4 uses tolower(); SAP Gateway V2
         * services reject toupper() ("Function toupper is not supported" ->
         * 400, empty table), so V2 ORs plain substringof() over the typed,
         * UPPER, lower and Capitalized spellings instead.
         */
        textFilter: function (oModel, sPath, sValue) {
            if (isV4(oModel)) {
                return new Filter({ path: sPath, operator: FilterOperator.Contains,
                    value1: sValue, caseSensitive: false });
            }
            var aVariants = [sValue, sValue.toUpperCase(), sValue.toLowerCase(),
                sValue.charAt(0).toUpperCase() + sValue.slice(1).toLowerCase()]
                .filter(function (v, i, a) { return a.indexOf(v) === i; });
            var aFilters = aVariants.map(function (v) {
                return new Filter({ path: sPath, operator: FilterOperator.Contains,
                    value1: v, caseSensitive: true });
            });
            return aFilters.length === 1 ? aFilters[0] : new Filter({ filters: aFilters, and: false });
        },

        /** Current server value of one property of a record (V2 / V4). */
        readProperty: function (oContext, sField) {
            var oModel = oContext.getModel();
            if (isV4(oModel)) {
                return oContext.requestProperty(sField);   // loads it if not yet selected
            }
            return new Promise(function (fnResolve, fnReject) {
                oModel.read(oContext.getPath(), {
                    urlParameters: { "$select": sField },
                    success: function (oData) { fnResolve(oData[sField]); },
                    error: fnReject
                });
            });
        },

        errorText: function (oError, sOperation) {
            var sRaw = this._rawErrorText(oError);
            if (isDump(sRaw) && /ApprovalRequest/i.test(sOperation || "")) {
                return APPROVAL_DUMP.replace("{0}", /reject/i.test(sOperation) ? "reject" : "release") +
                    "\n\nDetails: " + sRaw;
            }
            return explainDump(sRaw);
        },

        isDump: function (oError) { return isDump(this._rawErrorText(oError)); },

        _rawErrorText: function (oError) {
            if (!oError) { return "Unknown error."; }
            var sBody = oError.responseText || oError.body || "";
            if (sBody) {
                try {
                    var o = JSON.parse(sBody);
                    var m = o.error && o.error.message;
                    if (m) { return typeof m === "string" ? m : m.value; }
                } catch (e) {
                    var aXml = /<message[^>]*>([^<]+)<\/message>/.exec(sBody);
                    if (aXml) { return aXml[1]; }
                }
            }
            return oError.message || oError.statusText || String(oError);
        }
    };
    return DataService;
});
'''

FORMATTER = r'''sap.ui.define([
    "sap/ui/core/format/DateFormat",
    "sap/ui/core/format/NumberFormat"
], function (DateFormat, NumberFormat) {
    "use strict";

    var mStates = @@STATES@@;

    return {
        /**
         * Status code -> sap.ui.core.ValueState. Uses the mapping designed for
         * this service first, then keyword heuristics.
         */
        statusState: function (vValue) {
            if (vValue === null || vValue === undefined || vValue === "") {
                return mStates.hasOwnProperty("") ? mStates[""] : "None";
            }
            var s = String(vValue);
            if (mStates.hasOwnProperty(s)) { return mStates[s]; }
            var l = s.toLowerCase();
            if (/(cancel|reject|error|fail|block|overdue|incomplete|unpaid)/.test(l)) { return "Error"; }
            if (/(partial|progress|pending|hold|await|draft|open|new)/.test(l)) { return "Warning"; }
            if (/(complete|done|approv|success|closed|paid|deliver|active|finish|released)/.test(l)) { return "Success"; }
            return "None";
        },

        yesNo: function (b) {
            return b ? "✓" : "";
        },

        /** Display text of a raw value by UI type (rows read outside bindings). */
        value: function (v, sUiType, iScale) {
            if (v === null || v === undefined || v === "") { return ""; }
            if (sUiType === "boolean") { return v ? "✓" : ""; }
            if (sUiType === "date" || sUiType === "datetime") {
                var m = /\/Date\((-?\d+)/.exec(String(v));
                var d = v instanceof Date ? v : (m ? new Date(Number(m[1])) : new Date(v));
                if (isNaN(d.getTime())) { return String(v); }
                return sUiType === "date"
                    ? DateFormat.getDateInstance({ style: "medium", UTC: true }).format(d)
                    : DateFormat.getDateTimeInstance({ style: "medium" }).format(d);
            }
            if (sUiType === "number" || sUiType === "amount") {
                var n = Number(v);
                return isNaN(n) ? String(v) : NumberFormat.getFloatInstance(
                    { maxFractionDigits: iScale === undefined ? 2 : iScale, groupingEnabled: true }).format(n);
            }
            return String(v);
        }
    };
});
'''

BASE_CONTROLLER = r'''sap.ui.define([
    "sap/ui/core/mvc/Controller",
    "sap/ui/core/routing/History",
    "sap/ui/model/Filter",
    "sap/ui/model/FilterOperator",
    "sap/m/MessageBox",
    "sap/m/MessageToast",
    "sap/m/SelectDialog",
    "sap/m/StandardListItem",
    "sap/m/Dialog",
    "sap/m/Button",
    "sap/m/Label",
    "sap/m/Input",
    "sap/ui/layout/form/SimpleForm",
    "sap/ui/core/format/NumberFormat",
    "sap/ui/core/format/DateFormat",
    "../model/DataService",
    "../model/formatter",
    "../model/logic"
], function (Controller, History, Filter, FilterOperator, MessageBox, MessageToast, SelectDialog,
             StandardListItem, Dialog, Button, Label, Input, SimpleForm, NumberFormat, DateFormat,
             DataService, formatter, Logic) {
    "use strict";

    return Controller.extend("@@APP@@.controller.BaseController", {
        formatter: formatter,

        getRouter: function () { return this.getOwnerComponent().getRouter(); },
        getModel: function (sName) { return this.getView().getModel(sName); },
        setModel: function (oModel, sName) { return this.getView().setModel(oModel, sName); },
        // Multi-service apps: a page works on the model of its own service (config
        // "service"); dashboards/analytics read each entity set from its service.
        bindService: function () {
            var s = this.getConfig().service, oM = s && this.getOwnerComponent().getModel(s);
            if (oM) { this.getView().setModel(oM); }
        },
        modelFor: function (sEntitySet) {
            var s = (this.getConfig().serviceOfSet || {})[sEntitySet];
            return (s && this.getOwnerComponent().getModel(s)) || this.getModel();
        },
        getText: function (sKey, aArgs) {
            return this.getOwnerComponent().getModel("i18n").getResourceBundle().getText(sKey, aArgs);
        },
        // Multi-page apps: page controllers set pageId; their config (entity set,
        // columns, routes, ...) is overlaid on the app-wide config.
        pageId: "",
        getConfig: function () {
            var oCfg = this.getOwnerComponent().getModel("config").getData();
            return (this.pageId && oCfg.pages && oCfg.pages[this.pageId])
                ? Object.assign({}, oCfg, oCfg.pages[this.pageId]) : oCfg;
        },
        routeName: function (sKind) {
            var oRoutes = this.getConfig().routes || {};
            return oRoutes[sKind] || sKind;
        },
        fragmentName: function (sName) {
            return "@@APP@@.fragment." + (this.getConfig().fileId || "") + sName;
        },
        textKey: function (sKey) {
            return this.pageId ? this.pageId + "_" + sKey : sKey;
        },
        setLayout: function (sLayout) {
            this.getOwnerComponent().getModel("appView").setProperty("/layout", sLayout);
        },

        onNavBack: function () {
            if (History.getInstance().getPreviousHash() !== undefined) {
                window.history.go(-1);
            } else {
                this.getRouter().navTo(this.routeName("list"), {}, true);
            }
        },

        /** Clear a "required" error as soon as the user enters a value. */
        onFieldChange: function (oEvent) {
            var oCtrl = oEvent.getSource();
            if (oCtrl.data("requiredError") && oCtrl.getValue && oCtrl.getValue() !== "") {
                oCtrl.setValueState("None");
                oCtrl.data("requiredError", false);
            }
        },

        showError: function (oError, sOperation) {
            MessageBox.error(DataService.errorText(oError, sOperation));
        },

        /** Generic value help for any Input carrying customData vhField. */
        onValueHelp: function (oEvent) {
            var oInput = oEvent.getSource();
            var sField = oInput.data("vhField");
            var oVh = this.getConfig().valueHelps[sField];
            if (!oVh) { return; }
            var oDialog = new SelectDialog({
                title: this.getText("vh_" + sField),
                growing: true,
                search: function (oEvt) {
                    var sValue = oEvt.getParameter("value");
                    var aFilters = sValue ? [DataService.textFilter(
                        oEvt.getSource().getModel(), oVh.text || oVh.key, sValue)] : [];
                    oEvt.getSource().getBinding("items").filter(aFilters);
                },
                confirm: function (oEvt) {
                    var oItem = oEvt.getParameter("selectedItem");
                    if (oItem) {
                        oInput.setValue(oItem.getBindingContext().getProperty(oVh.key));
                        oInput.fireChange({ value: oInput.getValue() });
                    }
                },
                afterClose: function () { oDialog.destroy(); }
            });
            this.getView().addDependent(oDialog);
            oDialog.bindAggregation("items", {
                path: "/" + oVh.entitySet,
                template: new StandardListItem({
                    title: "{" + oVh.key + "}",
                    description: oVh.text ? "{" + oVh.text + "}" : ""
                })
            });
            oDialog.open();
        },

        /** Ask for non-key operation parameters; resolves {name: value}. */
        _askParameters: function (oAction) {
            var aParams = (oAction.operation && oAction.operation.askParams) || [];
            if (!aParams.length) { return Promise.resolve({}); }
            var that = this;
            return new Promise(function (fnResolve, fnReject) {
                var oForm = new SimpleForm({ editable: true });
                var mInputs = {};
                aParams.forEach(function (p) {
                    oForm.addContent(new Label({ text: p.label, required: !p.nullable }));
                    mInputs[p.name] = new Input({ type: p.numeric ? "Number" : "Text" });
                    oForm.addContent(mInputs[p.name]);
                });
                var oDialog = new Dialog({
                    title: oAction.label,
                    content: [oForm],
                    beginButton: new Button({
                        text: that.getText("ok"), type: "Emphasized",
                        press: function () {
                            var mValues = {};
                            aParams.forEach(function (p) {
                                var v = mInputs[p.name].getValue();
                                mValues[p.name] = p.numeric && v !== "" ? Number(v) : v;
                            });
                            oDialog.close();
                            fnResolve(mValues);
                        }
                    }),
                    endButton: new Button({
                        text: that.getText("cancel"),
                        press: function () { oDialog.close(); fnReject(null); }
                    }),
                    afterClose: function () { oDialog.destroy(); }
                });
                that.getView().addDependent(oDialog);
                oDialog.open();
            });
        },

        /** Run a configured action on the given contexts. */
        runAction: function (oAction, aContexts) {
            var that = this;
            var oModel = this.getModel();
            if (oAction.scope !== "global" && !aContexts.length) {
                MessageToast.show(this.getText("noSelection"));
                return Promise.resolve();
            }
            // State guard: never send an operation for records in a state the
            // backend does not support (SAP may dump instead of erroring).
            var oWhen = oAction.enabledWhen || {};
            if (oWhen.field && oAction.scope !== "global" && !oAction._stateChecked) {
                // current server state, not what the table happened to load
                return Promise.all(aContexts.map(function (c) {
                    return DataService.readProperty(c, oWhen.field).then(function (v) {
                        return oWhen.values.indexOf(String(v)) >= 0;
                    }, function () { return false; });
                })).then(function (aOk) {
                    var iBad = aOk.filter(function (b) { return !b; }).length;
                    if (iBad) {
                        MessageBox.information(that.getText("actionNotAllowed",
                            [oAction.label, oWhen.label || oWhen.field, oWhen.values.join(" / "),
                             iBad, aContexts.length]));
                        return;
                    }
                    return that.runAction(Object.assign({}, oAction, { _stateChecked: true }), aContexts);
                });
            }
            var fnRun = function () {
                return that._askParameters(oAction).then(function (mParams) {
                    var aTargets = oAction.scope === "global" ? [null] : aContexts;
                    return Promise.all(aTargets.map(function (oCtx) {
                        if (oAction.kind === "script") { return that._runScript(oAction, oCtx); }
                        if (oAction.kind === "set_field") {
                            return DataService.setField(oCtx, oAction.field, oAction.value);
                        }
                        // A record on which SAP already dumped for this operation is not sent
                        // again (a dump is caused by the record's data; retrying only repeats it).
                        var sKey = "evkDump:" + oAction.operation.name + ":" + (oCtx ? oCtx.getPath() : "");
                        var bKnown = false;
                        try { bKnown = !!window.sessionStorage.getItem(sKey); } catch (e) { bKnown = false; }
                        if (bKnown) {
                            return Promise.reject({ message: that.getText("dumpKnown", [oAction.label]),
                                                    _known: true });
                        }
                        return DataService.callOperation(oModel, oAction.operation, oCtx, mParams)
                            .catch(function (oError) {
                                if (DataService.isDump(oError)) {
                                    try { window.sessionStorage.setItem(sKey, "1"); } catch (e) { /* ignore */ }
                                }
                                throw oError;
                            });
                    }));
                }).then(function () {
                    MessageToast.show(that.getText("actionDone", [oAction.label]));
                    if (that.refreshData) { that.refreshData(); }
                }).catch(function (oError) {
                    if (oError && oError._known) {
                        MessageBox.warning(oError.message);
                    } else if (oError) {
                        that.showError(oError, oAction.operation && oAction.operation.name);
                    }
                });
            };
            if (!oAction.confirm) { return fnRun(); }
            return new Promise(function (fnResolve) {
                MessageBox.confirm(that.getText("actionConfirm", [oAction.label, aContexts.length || 1]), {
                    onClose: function (sAction) {
                        if (sAction === MessageBox.Action.OK) { fnResolve(fnRun()); } else { fnResolve(); }
                    }
                });
            });
        },

        findAction: function (sId) {
            return this.getConfig().actions.filter(function (a) { return a.id === sId; })[0];
        },

        // ── Custom business logic (model/logic.js, sandboxed) ───────────────
        logic: function () { return Logic.page(this.getConfig().logicPage || "main"); },

        /** Calculated column / header figure: composite-binding formatter body. */
        computeValue: function (sId, aFields, aArgs, sType, iDecimals) {
            var fn = (this.logic().computed || {})[sId];
            var v = Logic.run(fn, [Logic.rowOf(aFields, aArgs)], null);
            if (v === null || v === undefined || v === "") { return ""; }
            if (sType === "number") {
                var n = Number(v);
                return isNaN(n) ? String(v) : NumberFormat.getFloatInstance({
                    minFractionDigits: iDecimals, maxFractionDigits: iDecimals, groupingEnabled: true }).format(n);
            }
            if (sType === "date") {
                var d = Logic.util.toDate(v);
                return d ? DateFormat.getDateInstance({ style: "medium" }).format(d) : String(v);
            }
            return String(v);
        },

        /** Row highlight (sap.ui.core.MessageType / IndicationColor). */
        computeState: function (aFields, aArgs) {
            var hook = this.logic().rowState;
            var v = Logic.run(hook, [Logic.rowOf(aFields, aArgs)], "None");
            return ["Error", "Warning", "Success", "Information", "None"].indexOf(v) >= 0 ? v : "None";
        },

        /** Create-form initial values from logic.defaults. */
        applyDefaults: function (oData) {
            var mDef = this.logic().defaults || {}, oModel = this.getModel(), mTypes = this._edmTypes();
            Object.keys(mDef).forEach(function (f) {
                var v = Logic.run(mDef[f], [Object.assign({}, oData)], undefined);
                if (v !== undefined) { oData[f] = DataService.toWire(oModel, mTypes[f], v); }
            });
            return oData;
        },

        /** logic.derived: fields computed from the others before create / save. */
        derivedValues: function (oRow) {
            var mDer = this.logic().derived || {}, oModel = this.getModel(), mTypes = this._edmTypes();
            var mOut = {};
            Object.keys(mDer).forEach(function (f) {
                var v = Logic.run(mDer[f], [Object.assign({}, oRow)], undefined);
                if (v !== undefined) { mOut[f] = DataService.toWire(oModel, mTypes[f], v); }
            });
            return mOut;
        },

        /** logic.validations -> list of messages for rules that fail. */
        ruleErrors: function (oRow, sIdPrefix) {
            var that = this, aMsg = [];
            (this.logic().validations || []).forEach(function (r) {
                var bOk = Logic.run(r.test, [Object.assign({}, oRow)], true);
                if (!bOk) {
                    aMsg.push(r.message);
                    (r.fields || []).forEach(function (f) {
                        var oCtrl = sIdPrefix && that.byId(sIdPrefix + f);
                        if (oCtrl && oCtrl.setValueState) {
                            oCtrl.setValueState("Error");
                            oCtrl.setValueStateText(r.message);
                        }
                    });
                }
            });
            return aMsg;
        },

        _edmTypes: function () { return this.getConfig().edmTypes || {}; },

        // ── child records entered with the document (deep insert) ──────────
        _newChildRow: function (oChild) {
            var o = {};
            oChild.fields.forEach(function (f) { o[f.field] = f.default; });
            return o;
        },

        /** Create-model initial data: one empty row per child table (e.g. one item). */
        initChildren: function (oData) {
            var that = this;
            (this.getConfig().createChildren || []).forEach(function (c) {
                oData[c.nav] = [that._newChildRow(c)];
            });
            return oData;
        },

        onAddChildRow: function (oEvent) {
            var sNav = oEvent.getSource().data("nav");
            var oChild = (this.getConfig().createChildren || []).filter(function (c) { return c.nav === sNav; })[0];
            var oCreate = this.getModel("create");
            if (!oChild || !oCreate) { return; }
            var aRows = (oCreate.getProperty("/" + sNav) || []).slice();
            aRows.push(this._newChildRow(oChild));
            oCreate.setProperty("/" + sNav, aRows);
        },

        onRemoveChildRow: function (oEvent) {
            var sNav = oEvent.getSource().data("nav");
            var oCtx = oEvent.getSource().getBindingContext("create");
            var oCreate = this.getModel("create");
            if (!oCtx || !oCreate) { return; }
            var iIdx = Number(oCtx.getPath().split("/").pop());
            var aRows = (oCreate.getProperty("/" + sNav) || []).slice();
            aRows.splice(iIdx, 1);
            oCreate.setProperty("/" + sNav, aRows);
        },

        /**
         * Before create: drop empty rows, check required child fields, number the
         * child keys (10, 20, ...) when the user left them empty. Returns messages.
         */
        prepareChildren: function (oData) {
            var aMsg = [];
            (this.getConfig().createChildren || []).forEach(function (c) {
                var aRows = (oData[c.nav] || []).map(function (r) {
                    var o = {};
                    Object.keys(r).forEach(function (k) {
                        if (r[k] !== undefined && r[k] !== null && r[k] !== "" && !(r[k] === false &&
                                c.fields.some(function (f) { return f.field === k && f.default === false; }))) {
                            o[k] = r[k];
                        }
                    });
                    return o;
                }).filter(function (o) { return Object.keys(o).length > 0; });
                aRows.forEach(function (o, i) {
                    c.fields.forEach(function (f) {
                        if (f.required && (o[f.field] === undefined || o[f.field] === "")) {
                            aMsg.push(c.title + " " + (i + 1) + ": " + f.label);
                        }
                    });
                    (c.autoKeys || []).forEach(function (k) {
                        if (o[k] === undefined || o[k] === "") { o[k] = String((i + 1) * 10); }
                    });
                });
                if (aRows.length) { oData[c.nav] = aRows; } else { delete oData[c.nav]; }
            });
            return aMsg;
        },

        /** Action kind "script": run the generated script per record, then apply. */
        _runScript: function (oAction, oCtx) {
            var oModel = this.getModel(), mOps = this.getConfig().operations || {};
            var fn = Logic.script(oAction.id);
            var oRow = Object.assign({}, oCtx ? oCtx.getObject() : {});
            var mSet = {}, aCalls = [], aMsg = [], sFail = null;
            var api = {
                set: function (f, v) { mSet[f] = v; oRow[f] = v; },
                call: function (sOp, mParams) { aCalls.push([sOp, mParams || {}]); },
                message: function (t) { aMsg.push(String(t)); },
                fail: function (t) { sFail = String(t || "Not possible."); throw new Error("__fail__"); }
            };
            try {
                if (typeof fn === "function") { fn(oRow, api); }
            } catch (e) {
                if (!sFail) { sFail = "Custom action failed: " + e.message; }
            }
            if (sFail) { return Promise.reject({ message: sFail }); }
            var mTypes = this._edmTypes(), mWire = {};
            Object.keys(mSet).forEach(function (f) { mWire[f] = DataService.toWire(oModel, mTypes[f], mSet[f]); });
            return DataService.setFields(oCtx, mWire).then(function () {
                return aCalls.reduce(function (p, c) {
                    return p.then(function () {
                        var oOp = mOps[c[0]];
                        if (!oOp) { throw { message: "Operation " + c[0] + " is not available." }; }
                        return DataService.callOperation(oModel, oOp, oCtx, c[1]);
                    });
                }, Promise.resolve());
            }).then(function () {
                if (aMsg.length) { MessageToast.show(aMsg.join("\n")); }
            });
        }
    });
});
'''

APP_CONTROLLER = r'''sap.ui.define([
    "./BaseController"
], function (BaseController) {
    "use strict";

    return BaseController.extend("@@APP@@.controller.App", {
        onInit: function () {
            this.getView().addStyleClass(this.getOwnerComponent().getContentDensityClass());
        }
    });
});
'''

LIST_CONTROLLER = r'''sap.ui.define([
    "./BaseController",
    "sap/ui/model/json/JSONModel",
    "sap/ui/model/Filter",
    "sap/ui/model/FilterOperator",
    "sap/ui/model/Sorter",
    "sap/m/MessageBox",
    "sap/m/MessageToast",
    "sap/ui/core/Fragment",
    "sap/ui/core/format/NumberFormat",
    "../model/DataService"
], function (BaseController, JSONModel, Filter, FilterOperator, Sorter, MessageBox, MessageToast,
             Fragment, NumberFormat, DataService) {
    "use strict";

    return BaseController.extend("@@APP@@.controller.List", {
/*@@LOGIC_METHODS@@*/

        onInit: function () {
            this.bindService();
            this.setModel(new JSONModel({ tableTitle: "", count: 0, hasSelection: false, kpis: [],
                tab: "__all", tabCounts: {}, linkFilter: "" }), "viewModel");
            this.getRouter().getRoute(this.routeName("list")).attachPatternMatched(this._onRouteMatched, this);
        },

        _onRouteMatched: function (oEvent) {
            this.setLayout("OneColumn");
            // opened from a link on another page: show only the matching records
            var q = ((oEvent && oEvent.getParameter("arguments")) || {})["?query"] || null;
            var oLf = q && q.f ? { field: q.f, value: q.v, label: q.l || q.f } : null;
            var bChanged = JSON.stringify(oLf) !== JSON.stringify(this._linkFilter || null);
            this._linkFilter = oLf;
            this.getModel("viewModel").setProperty("/linkFilter",
                oLf ? this.getText("linkFilter", [oLf.label, oLf.value]) : "");
            if (bChanged && this._table().getBinding("items")) { this.onSearch(); }
            this._updateTabCounts();
        },

        onClearLinkFilter: function () {
            this.getRouter().navTo(this.routeName("list"), {}, true);
        },

        // ── Status tabs (quick filters with counts) ─────────────────────────
        onQuickTab: function (oEvent) {
            this.getModel("viewModel").setProperty("/tab", oEvent.getParameter("key"));
            this.onSearch();
        },

        _tabFilter: function () {
            var oQt = this.getConfig().quickTabs;
            var sTab = this.getModel("viewModel").getProperty("/tab");
            if (!oQt || !sTab || sTab === "__all") { return null; }
            return new Filter(oQt.field, FilterOperator.EQ, sTab === "__blank" ? "" : sTab);
        },

        _updateTabCounts: function () {
            var oQt = this.getConfig().quickTabs, that = this;
            if (!oQt) { return; }
            var oModel = this.getModel(), oVm = this.getModel("viewModel");
            DataService.count(oModel, this.getConfig().entitySet, []).then(function (n) {
                oVm.setProperty("/tabCounts/__all", n);
            }).catch(function () {});
            oQt.values.forEach(function (v) {
                DataService.count(oModel, that.getConfig().entitySet,
                    [new Filter(oQt.field, FilterOperator.EQ, v)]).then(function (n) {
                    oVm.setProperty("/tabCounts/" + (v === "" ? "__blank" : v), n);
                }).catch(function () {});
            });
        },

        _table: function () { return this.byId("table"); },

        refreshData: function () {
            var oBinding = this._table().getBinding("items");
            if (oBinding) { oBinding.refresh(); }
            this._table().removeSelections(true);
            this.getModel("viewModel").setProperty("/hasSelection", false);
            this._updateTabCounts();
        },

        onUpdateFinished: function (oEvent) {
            var iTotal = oEvent.getParameter("total");
            var oVm = this.getModel("viewModel");
            oVm.setProperty("/count", iTotal);
            oVm.setProperty("/tableTitle", this.getText(this.textKey("tableTitleCount"), [iTotal]));
            this._computeKpis();
        },

        _computeKpis: function () {
            var aKpis = this.getConfig().kpis || [];
            if (!aKpis.length) { return; }
            var aRows = this._table().getItems().map(function (oItem) {
                return oItem.getBindingContext().getObject() || {};
            });
            var oFmt = NumberFormat.getFloatInstance({ maxFractionDigits: 2, groupingEnabled: true });
            this.getModel("viewModel").setProperty("/kpis", aKpis.map(function (k) {
                var aVals = aRows.map(function (r) { return Number(r[k.field]) || 0; });
                var v;
                switch (k.aggregation) {
                    case "average": v = aVals.length ? aVals.reduce(function (a, b) { return a + b; }, 0) / aVals.length : 0; break;
                    case "min": v = aVals.length ? Math.min.apply(null, aVals) : 0; break;
                    case "max": v = aVals.length ? Math.max.apply(null, aVals) : 0; break;
                    case "count": v = aVals.length; break;
                    default: v = aVals.reduce(function (a, b) { return a + b; }, 0);
                }
                return { label: k.label, value: oFmt.format(v) };
            }));
        },

        onSelectionChange: function () {
            this.getModel("viewModel").setProperty("/hasSelection",
                this._table().getSelectedItems().length > 0);
        },

        // ── Filtering & search ──────────────────────────────────────────────
        onSearch: function () {
            var oModel = this.getModel();
            var oCfg = this.getConfig();
            var that = this;
            var aFilters = [];
            oCfg.filters.forEach(function (f) {
                var oCtrl = that.byId("filter_" + f.field);
                if (!oCtrl) { return; }
                var oFilter;
                if (f.mode === "range") {
                    oFilter = DataService.filterFor(oModel, f, oCtrl.getDateValue(), oCtrl.getSecondDateValue());
                } else if (f.mode === "multi") {
                    oFilter = DataService.filterFor(oModel, f, oCtrl.getSelectedKeys());
                } else if (f.uiType === "boolean") {
                    var sKey = oCtrl.getSelectedKey();
                    oFilter = sKey ? DataService.filterFor(oModel, f, sKey === "true") : null;
                } else {
                    var sVal = oCtrl.getValue();
                    oFilter = DataService.filterFor(oModel, f,
                        (f.uiType === "number" || f.uiType === "amount") && sVal !== "" ? Number(sVal) : sVal);
                }
                if (oFilter) { aFilters.push(oFilter); }
            });
            var oTab = this._tabFilter();
            if (oTab) { aFilters.push(oTab); }
            if (this._linkFilter) {
                aFilters.push(new Filter(this._linkFilter.field, FilterOperator.EQ, this._linkFilter.value));
            }
            var sQuery = (this.byId("searchField").getValue() || "").trim();
            if (sQuery && oCfg.searchFields.length) {
                aFilters.push(new Filter({
                    filters: oCfg.searchFields.map(function (sField) {
                        return DataService.textFilter(oModel, sField, sQuery);
                    }),
                    and: false
                }));
            }
            this._table().getBinding("items").filter(
                aFilters.length ? new Filter({ filters: aFilters, and: true }) : [], "Application");
        },

        onFilterClear: function () {
            var that = this;
            this.getConfig().filters.forEach(function (f) {
                var oCtrl = that.byId("filter_" + f.field);
                if (!oCtrl) { return; }
                if (oCtrl.setSelectedKeys) { oCtrl.setSelectedKeys([]); }
                else if (oCtrl.setSelectedKey) { oCtrl.setSelectedKey(""); }
                else if (oCtrl.setDateValue) { oCtrl.setDateValue(null); oCtrl.setSecondDateValue(null); }
                else { oCtrl.setValue(""); }
            });
            this.byId("searchField").setValue("");
            this.onSearch();
        },

        // ── Navigation ──────────────────────────────────────────────────────
        onItemPress: function (oEvent) {
            var mRoutes = this.getConfig().routes || {};
            if (Object.prototype.hasOwnProperty.call(mRoutes, "object") && !mRoutes.object) { return; }
            var oCtx = (oEvent.getParameter("listItem") || oEvent.getSource()).getBindingContext();
            this.getRouter().navTo(this.routeName("object"), {
                key: encodeURIComponent(oCtx.getPath().substring(1))
            });
        },

        // ── Create ──────────────────────────────────────────────────────────
        onCreate: function () {
            var oCfg = this.getConfig();
            if (oCfg.createRoute) {             // guided create (wizard page)
                this.getRouter().navTo(oCfg.createRoute);
                return;
            }
            var oData = {};
            oCfg.createFields.forEach(function (f) { oData[f.field] = f.default; });
            this.applyDefaults(oData);
            this.initChildren(oData);
            this.setModel(new JSONModel(oData), "create");
            var oView = this.getView();
            if (!this._pCreate) {
                this._pCreate = Fragment.load({
                    id: oView.getId(), name: this.fragmentName("CreateDialog"), controller: this
                }).then(function (oDialog) { oView.addDependent(oDialog); return oDialog; });
            }
            this._pCreate.then(function (oDialog) { oDialog.open(); });
        },

        onCreateSave: function () {
            var that = this;
            var oCfg = this.getConfig();
            var oData = Object.assign({}, this.getModel("create").getData());
            var aMissing = [];
            oCfg.createFields.forEach(function (f) {
                var oCtrl = that.byId("create_" + f.field);
                var v = oData[f.field];
                var bEmpty = v === undefined || v === null || v === "";
                if (f.required && bEmpty) {
                    aMissing.push(f.label);
                    if (oCtrl && oCtrl.setValueState) {
                        oCtrl.setValueState("Error");
                        oCtrl.data("requiredError", true);   // ours — cleared once filled
                    }
                } else if (oCtrl && oCtrl.setValueState) {
                    if (oCtrl.data("requiredError")) {
                        oCtrl.setValueState("None");
                        oCtrl.data("requiredError", false);
                    } else if (oCtrl.getValueState() === "Error") {
                        aMissing.push(f.label);              // type/format validation error
                    }
                }
                if (bEmpty) { delete oData[f.field]; }   // let the backend default it
            });
            if (aMissing.length) {
                MessageBox.error(this.getText("fixFields", [aMissing.join(", ")]));
                return;
            }
            var aChild = this.prepareChildren(oData);
            if (aChild.length) {
                MessageBox.error(this.getText("fixFields", [aChild.join(", ")]));
                return;
            }
            Object.assign(oData, this.derivedValues(oData));
            var aRules = this.ruleErrors(oData, "create_");
            if (aRules.length) {
                MessageBox.error(aRules.join("\n"));
                return;
            }
            DataService.create(this.getModel(), oCfg.entitySet, oData).then(function () {
                MessageToast.show(that.getText("created"));
                that.byId("createDialog").close();
                that.refreshData();
            }).catch(this.showError.bind(this));
        },

        onCreateCancel: function () {
            this.byId("createDialog").close();
        },

        // ── Delete / actions ────────────────────────────────────────────────
        onDeleteSelected: function () {
            var that = this;
            var aCtx = this._table().getSelectedContexts();
            if (!aCtx.length) { return; }
            MessageBox.confirm(this.getText("deleteConfirm", [aCtx.length]), {
                onClose: function (sAction) {
                    if (sAction !== MessageBox.Action.OK) { return; }
                    Promise.all(aCtx.map(DataService.remove)).then(function () {
                        MessageToast.show(that.getText("deleted"));
                        that.refreshData();
                    }).catch(that.showError.bind(that));
                }
            });
        },

        onAction: function (oEvent) {
            var oAction = this.findAction(oEvent.getSource().data("actionId"));
            this.runAction(oAction, this._table().getSelectedContexts());
        },

        // ── Sort / export ───────────────────────────────────────────────────
        onSort: function () {
            var oView = this.getView();
            if (!this._pSort) {
                this._pSort = Fragment.load({
                    id: oView.getId(), name: this.fragmentName("SortDialog"), controller: this
                }).then(function (oDialog) { oView.addDependent(oDialog); return oDialog; });
            }
            this._pSort.then(function (oDialog) { oDialog.open(); });
        },

        onSortConfirm: function (oEvent) {
            var oItem = oEvent.getParameter("sortItem");
            if (!oItem) { return; }
            this._table().getBinding("items").sort(
                new Sorter(oItem.getKey(), oEvent.getParameter("sortDescending")));
        },

        onExport: function () {
            var oCfg = this.getConfig();
            var that = this;
            sap.ui.require(["sap/ui/export/Spreadsheet", "sap/ui/export/library"], function (Spreadsheet, exportLibrary) {
                var EdmType = exportLibrary.EdmType;
                var mType = { date: EdmType.Date, datetime: EdmType.DateTime, amount: EdmType.Number,
                    number: EdmType.Number, boolean: EdmType.Boolean, text: EdmType.String };
                var aRows = that._table().getItems().map(function (oItem) {
                    return oItem.getBindingContext().getObject();
                });
                var oSheet = new Spreadsheet({
                    workbook: { columns: oCfg.columns.map(function (c) {
                        return { label: c.label, property: c.field, type: mType[c.uiType] || EdmType.String,
                            scale: c.scale };
                    }) },
                    dataSource: aRows,
                    fileName: oCfg.exportFileName,
                    worker: false
                });
                oSheet.build().finally(function () { oSheet.destroy(); });
            });
        }
    });
});
'''

OBJECT_CONTROLLER = r'''sap.ui.define([
    "./BaseController",
    "sap/ui/model/json/JSONModel",
    "sap/m/MessageBox",
    "sap/m/MessageToast",
    "../model/DataService",
    "sap/ui/model/Filter",
    "sap/ui/model/FilterOperator"
], function (BaseController, JSONModel, MessageBox, MessageToast, DataService, Filter, FilterOperator) {
    "use strict";

    return BaseController.extend("@@APP@@.controller.Object", {
/*@@LOGIC_METHODS@@*/

        onInit: function () {
            this.bindService();
            this.setModel(new JSONModel({ editMode: false, busy: false, fullScreen: false }), "viewModel");
            this.getRouter().getRoute(this.routeName("object")).attachPatternMatched(this._onObjectMatched, this);
            // related-record tables: their static row is the template, bound per record
            var that = this;
            this._relTpl = {};
            (this.getConfig().related || []).forEach(function (r) {
                var oTable = that.byId(r.id), oTpl = that.byId(r.id + "Tpl");
                if (oTable && oTpl) {
                    oTable.removeAllItems();
                    that._relTpl[r.id] = oTpl;
                }
            });
        },

        _loadRelated: function (sPath) {
            var that = this, aRel = this.getConfig().related || [];
            if (!aRel.length) { return; }
            DataService.readObject(this.getModel(), sPath).then(function (oObj) {
                aRel.forEach(function (r) {
                    var oTable = that.byId(r.id), oTpl = that._relTpl[r.id];
                    if (!oTable || !oTpl) { return; }
                    var v = oObj ? oObj[r.sourceField] : null;
                    if (v === undefined || v === null || v === "") { oTable.unbindItems(); return; }
                    var oM = that.getOwnerComponent().getModel(r.model);
                    oTable.bindItems({ path: "/" + r.entitySet, model: r.model, template: oTpl,
                        templateShareable: true, filters: [new Filter(r.field, FilterOperator.EQ, v)],
                        parameters: DataService.isV4(oM) ? { $count: true } : {} });
                });
            }).catch(function () { /* the record itself shows the error */ });
        },

        onRelatedPress: function (oEvent) {
            var oItem = oEvent.getParameter("listItem"), oTable = oEvent.getSource();
            var sRoute = oTable.data("route"), sModel = oTable.data("model");
            if (!sRoute || !oItem) { return; }
            this.getRouter().navTo(sRoute, {
                key: encodeURIComponent(oItem.getBindingContext(sModel).getPath().substring(1))
            });
        },

        onCrossLink: function (oEvent) {
            var l = (this.getConfig().crossLinks || [])[Number(oEvent.getSource().data("link"))];
            // header link: the record itself; child-table link: its row
            var oCtx = oEvent.getSource().getBindingContext() || this.getView().getBindingContext();
            var v = l && oCtx ? oCtx.getProperty(l.field) : null;
            if (v === undefined || v === null || v === "") { return; }
            if (l.detailRoute) {
                var oM = this.getOwnerComponent().getModel(l.model) || this.getModel();
                this.getRouter().navTo(l.detailRoute, {
                    key: encodeURIComponent(DataService.keyPath(l.entitySet, l.keyType, v, DataService.isV4(oM)))
                });
            } else if (l.listRoute) {
                this.getRouter().navTo(l.listRoute, { "?query": { f: l.toField, v: String(v), l: l.toLabel } });
            }
        },

        _onObjectMatched: function (oEvent) {
            var sPath = "/" + decodeURIComponent(oEvent.getParameter("arguments").key);
            var oModel = this.getModel();
            this.setLayout(this.getModel("viewModel").getProperty("/fullScreen")
                ? "MidColumnFullScreen" : "TwoColumnsMidExpanded");
            if (DataService.hasChanges(oModel)) { DataService.reset(oModel); }
            this.getModel("viewModel").setProperty("/editMode", false);
            this.getView().bindElement(DataService.isV4(oModel)
                ? { path: sPath, parameters: { $$updateGroupId: "changes" } }
                : { path: sPath });
            this._loadRelated(sPath);
        },

        refreshData: function () {
            var oBinding = this.getView().getElementBinding();
            if (oBinding) { oBinding.refresh(); }
        },

        onEdit: function () {
            this.getModel("viewModel").setProperty("/editMode", true);
        },

        onSave: function () {
            var that = this;
            var oVm = this.getModel("viewModel");
            var oCtx = this.getView().getBindingContext();
            var oRow = oCtx ? Object.assign({}, oCtx.getObject()) : {};
            var mDerived = this.derivedValues(oRow);
            Object.assign(oRow, mDerived);
            var aRules = this.ruleErrors(oRow, "");
            if (aRules.length) {
                MessageBox.error(aRules.join("\n"));
                return;
            }
            Object.keys(mDerived).forEach(function (f) {
                if (oCtx && oCtx.getProperty(f) !== mDerived[f]) {
                    if (DataService.isV4(that.getModel())) { oCtx.setProperty(f, mDerived[f]); }
                    else { that.getModel().setProperty(oCtx.getPath() + "/" + f, mDerived[f]); }
                }
            });
            oVm.setProperty("/busy", true);
            DataService.save(this.getModel()).then(function () {
                oVm.setProperty("/editMode", false);
                MessageToast.show(that.getText("saved"));
            }).catch(this.showError.bind(this)).finally(function () {
                oVm.setProperty("/busy", false);
            });
        },

        onCancel: function () {
            DataService.reset(this.getModel());
            this.getModel("viewModel").setProperty("/editMode", false);
        },

        onDelete: function () {
            var that = this;
            var oCtx = this.getView().getBindingContext();
            MessageBox.confirm(this.getText("deleteConfirm", [1]), {
                onClose: function (sAction) {
                    if (sAction !== MessageBox.Action.OK) { return; }
                    DataService.remove(oCtx).then(function () {
                        MessageToast.show(that.getText("deleted"));
                        that.getRouter().navTo(that.routeName("list"), {}, true);
                    }).catch(that.showError.bind(that));
                }
            });
        },

        onAction: function (oEvent) {
            var oAction = this.findAction(oEvent.getSource().data("actionId"));
            this.runAction(oAction, [this.getView().getBindingContext()]);
        },

        onClose: function () {
            this.getModel("viewModel").setProperty("/fullScreen", false);
            this.getRouter().navTo(this.routeName("list"));
        },

        onToggleFullScreen: function () {
            var oVm = this.getModel("viewModel");
            var bFull = !oVm.getProperty("/fullScreen");
            oVm.setProperty("/fullScreen", bFull);
            this.setLayout(bFull ? "MidColumnFullScreen" : "TwoColumnsMidExpanded");
        }
    });
});
'''


SHELL_CONTROLLER = r'''sap.ui.define([
    "./BaseController"
], function (BaseController) {
    "use strict";

    // Multi-page shell: side navigation <-> router.
    return BaseController.extend("@@APP@@.controller.App", {
        onInit: function () {
            this.getView().addStyleClass(this.getOwnerComponent().getContentDensityClass());
            this.getRouter().attachRouteMatched(function (oEvent) {
                var mNav = this.getConfig().navKeyByRoute || {};
                var sKey = mNav[oEvent.getParameter("name")];
                if (sKey) { this.getModel("appView").setProperty("/navKey", sKey); }
            }, this);
        },
        onNavSelect: function (oEvent) {
            var sKey = oEvent.getParameter("item").getKey();
            if (sKey) { this.getRouter().navTo(sKey); }
        },
        onSideNavToggle: function () {
            var oVm = this.getModel("appView");
            oVm.setProperty("/sideExpanded", !oVm.getProperty("/sideExpanded"));
        }
    });
});
'''

DASHBOARD_CONTROLLER = r'''sap.ui.define([
    "./BaseController",
    "sap/ui/model/json/JSONModel",
    "sap/ui/core/format/NumberFormat",
    "../model/DataService"
], function (BaseController, JSONModel, NumberFormat, DataService) {
    "use strict";

    var SAMPLE = 500;   // rows read per entity set for aggregates / charts

    return BaseController.extend("@@APP@@.controller.Dashboard", {

        onInit: function () {
            this.bindService();
            this.setModel(new JSONModel({ kpis: {}, charts: {}, busy: true }), "dash");
            this.getRouter().getRoute(this.routeName("self")).attachPatternMatched(this._load, this);
        },

        // Read up to SAMPLE rows (+ total count) of one entity set, V2 or V4.
        _read: function (sEntitySet, aSelect) {
            var oModel = this.modelFor(sEntitySet);
            if (DataService.isV4(oModel)) {
                var mParams = { $count: true };
                if (aSelect.length) { mParams.$select = aSelect.join(","); }
                var oList = oModel.bindList("/" + sEntitySet, undefined, undefined, undefined, mParams);
                return oList.requestContexts(0, SAMPLE).then(function (aCtx) {
                    var oHeader = oList.getHeaderContext();
                    var iCount = oHeader ? oHeader.getProperty("$count") : aCtx.length;
                    return { rows: aCtx.map(function (c) { return c.getObject(); }),
                             count: iCount === undefined ? aCtx.length : iCount };
                });
            }
            return new Promise(function (fnResolve, fnReject) {
                var mUrl = { "$top": SAMPLE, "$inlinecount": "allpages" };
                if (aSelect.length) { mUrl.$select = aSelect.join(","); }
                oModel.read("/" + sEntitySet, {
                    urlParameters: mUrl,
                    success: function (oData) {
                        fnResolve({ rows: oData.results || [],
                                    count: Number(oData.__count || (oData.results || []).length) });
                    },
                    error: fnReject
                });
            });
        },

        _agg: function (aVals, sAgg) {
            if (sAgg === "count") { return aVals.length; }
            var a = aVals.map(Number).filter(function (v) { return !isNaN(v); });
            if (!a.length) { return 0; }
            var fSum = a.reduce(function (x, y) { return x + y; }, 0);
            switch (sAgg) {
                case "average": return fSum / a.length;
                case "min": return Math.min.apply(null, a);
                case "max": return Math.max.apply(null, a);
                default: return fSum;
            }
        },

        _load: function () {
            var that = this, oCfg = this.getConfig(), oDash = this.getModel("dash");
            var oFmt = NumberFormat.getFloatInstance({ maxFractionDigits: 1, groupingEnabled: true,
                style: "short" });
            var mSets = {};
            (oCfg.kpis || []).concat(oCfg.charts || []).forEach(function (o) {
                var a = mSets[o.entitySet] || (mSets[o.entitySet] = []);
                [o.field, o.dimension, o.measure].forEach(function (f) {
                    if (f && a.indexOf(f) < 0) { a.push(f); }
                });
            });
            oDash.setProperty("/busy", true);
            Promise.all(Object.keys(mSets).map(function (sSet) {
                return that._read(sSet, mSets[sSet]).then(function (r) { return [sSet, r]; });
            })).then(function (aRes) {
                var mData = {};
                aRes.forEach(function (x) { mData[x[0]] = x[1]; });
                var mKpis = {};
                (oCfg.kpis || []).forEach(function (k) {
                    var d = mData[k.entitySet] || { rows: [], count: 0 };
                    var v = k.aggregation === "count" ? d.count
                        : that._agg(d.rows.map(function (r) { return r[k.field]; }), k.aggregation);
                    // "73.9K" -> value "73.9" + scale "K" (NumericContent renders them apart)
                    var aM = /^([^A-Za-z]*)(.*)$/.exec(oFmt.format(v)) || ["", String(v), ""];
                    mKpis[k.id] = { value: aM[1].trim(), scale: aM[2].trim(), raw: v,
                                    sample: k.aggregation !== "count" && d.rows.length < d.count };
                });
                var mCharts = {};
                (oCfg.charts || []).forEach(function (c) {
                    var d = mData[c.entitySet] || { rows: [] };
                    var mGroups = {};
                    d.rows.forEach(function (r) {
                        var v = r[c.dimension];
                        var sKey = (v === null || v === undefined || v === "") ? "(none)" : String(v);
                        (mGroups[sKey] = mGroups[sKey] || []).push(c.measure ? r[c.measure] : 1);
                    });
                    mCharts[c.id] = Object.keys(mGroups).map(function (sKey) {
                        var v = c.measure ? that._agg(mGroups[sKey], c.aggregation) : mGroups[sKey].length;
                        return { label: sKey, value: v, display: oFmt.format(v) };
                    }).sort(function (a, b) { return b.value - a.value; }).slice(0, 8);
                });
                oDash.setData({ kpis: mKpis, charts: mCharts, busy: false });
            }).catch(function (oError) {
                oDash.setProperty("/busy", false);
                that.showError(oError);
            });
        },

        onRefresh: function () {
            this._load();
            this.getView().findAggregatedObjects(true, function (o) {
                return o.isA && o.isA("sap.m.Table");
            }).forEach(function (t) { var b = t.getBinding("items"); if (b) { b.refresh(); } });
        },

        onKpiPress: function (oEvent) {
            var sRoute = oEvent.getSource().data("route");
            if (sRoute) { this.getRouter().navTo(sRoute); }
        },

        onRecentPress: function (oEvent) {
            var oItem = oEvent.getParameter("listItem") || oEvent.getSource();
            var oTable = oItem.getParent();
            var sRoute = oTable && oTable.data && oTable.data("route");
            if (!sRoute) { return; }
            var sModel = (oTable.data("model") || undefined);
            this.getRouter().navTo(sRoute, {
                key: encodeURIComponent(oItem.getBindingContext(sModel).getPath().substring(1))
            });
        }
    });
});
'''

WIZARD_CONTROLLER = r'''sap.ui.define([
    "./BaseController",
    "sap/ui/model/json/JSONModel",
    "sap/m/MessageBox",
    "sap/m/MessageToast",
    "../model/DataService"
], function (BaseController, JSONModel, MessageBox, MessageToast, DataService) {
    "use strict";

    // Guided create: one WizardStep per field group, create on submit,
    // then open the new record (or the list).
    return BaseController.extend("@@APP@@.controller.Wizard", {

        onInit: function () {
            this.bindService();
            this.getRouter().getRoute(this.routeName("self")).attachPatternMatched(this._onMatched, this);
        },

        _onMatched: function () {
            var oData = {};
            this.getConfig().createFields.forEach(function (f) { oData[f.field] = f.default; });
            this.applyDefaults(oData);
            this.initChildren(oData);
            this.setModel(new JSONModel(oData), "create");
            var oWizard = this.byId("wizard");
            if (oWizard && oWizard.getSteps().length) {
                oWizard.discardProgress(oWizard.getSteps()[0]);
            }
            this.getConfig().createFields.forEach(function (f) {
                var oCtrl = this.byId("create_" + f.field);
                if (oCtrl && oCtrl.setValueState) { oCtrl.setValueState("None"); }
            }, this);
        },

        _missing: function () {
            var that = this, aMissing = [];
            var oData = this.getModel("create").getData();
            this.getConfig().createFields.forEach(function (f) {
                var oCtrl = that.byId("create_" + f.field);
                var v = oData[f.field];
                var bEmpty = v === undefined || v === null || v === "";
                if (f.required && bEmpty) {
                    aMissing.push(f.label);
                    if (oCtrl && oCtrl.setValueState) { oCtrl.setValueState("Error"); }
                } else if (oCtrl && oCtrl.setValueState) {
                    if (oCtrl.data("requiredError")) { oCtrl.setValueState("None"); }
                    else if (oCtrl.getValueState() === "Error") { aMissing.push(f.label); }
                }
                if (oCtrl && oCtrl.data) { oCtrl.data("requiredError", f.required && bEmpty); }
            });
            return aMissing;
        },

        onSubmit: function () {
            var that = this, oCfg = this.getConfig();
            var aMissing = this._missing();
            if (aMissing.length) {
                MessageBox.error(this.getText("fixFields", [aMissing.join(", ")]));
                return;
            }
            var oData = Object.assign({}, this.getModel("create").getData());
            var aChild = this.prepareChildren(oData);
            if (aChild.length) {
                MessageBox.error(this.getText("fixFields", [aChild.join(", ")]));
                return;
            }
            Object.assign(oData, this.derivedValues(oData));
            var aRules = this.ruleErrors(oData, "create_");
            if (aRules.length) {
                MessageBox.error(aRules.join("\n"));
                return;
            }
            Object.keys(oData).forEach(function (k) {
                if (oData[k] === undefined || oData[k] === null || oData[k] === "") { delete oData[k]; }
            });
            // (child arrays were cleaned by prepareChildren)
            var oModel = this.getModel();
            DataService.create(oModel, oCfg.entitySet, oData).then(function (oResult) {
                MessageToast.show(that.getText("created"));
                var sPath = "";
                if (oResult && oResult.getPath) {
                    sPath = oResult.getPath();
                } else if (oResult && oModel.createKey) {
                    var mKeys = {};
                    oCfg.keys.forEach(function (k) { mKeys[k] = oResult[k]; });
                    try { sPath = "/" + oModel.createKey(oCfg.entitySet, mKeys); } catch (e) { sPath = ""; }
                }
                var mRoutes = oCfg.routes || {};
                if (sPath && mRoutes.object) {
                    that.getRouter().navTo(mRoutes.object, { key: encodeURIComponent(sPath.substring(1)) });
                } else {
                    that.getRouter().navTo(mRoutes.list);
                }
            }).catch(this.showError.bind(this));
        },

        onCancel: function () {
            this.getRouter().navTo(this.routeName("list"));
        }
    });
});
'''
