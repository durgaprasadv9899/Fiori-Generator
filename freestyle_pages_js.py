"""
freestyle_pages_js.py — controllers of the additional multi-page app page types:
kanban board, calendar, analytics (sap.viz charts) and split (list + record side
by side). Generic like freestyle_js: everything page-specific is in
model/appConfig.js (pages.<id>). `@@APP@@` = app namespace.
"""

KANBAN_CONTROLLER = r'''sap.ui.define([
    "./BaseController",
    "sap/ui/model/json/JSONModel",
    "sap/m/MessageToast",
    "../model/DataService",
    "../model/formatter"
], function (BaseController, JSONModel, MessageToast, DataService, formatter) {
    "use strict";

    // Board: one lane per value of the lane field; drag a card to another lane
    // to update that field (only when the service allows it: config.move).
    return BaseController.extend("@@APP@@.controller.Kanban", {

        onInit: function () {
            this.bindService();
            this.setModel(new JSONModel({ lanes: [], busy: true, query: "", total: 0, shown: 0 }), "kb");
            this.getRouter().getRoute(this.routeName("self")).attachPatternMatched(this._load, this);
        },

        _load: function () {
            var that = this, c = this.getConfig(), oKb = this.getModel("kb");
            oKb.setProperty("/busy", true);
            var aSel = [c.laneField, c.cardTitle, c.cardSubtitle].concat(
                c.cardFields.map(function (f) { return f.field; })).filter(Boolean);
            DataService.readRows(this.getModel(), c.entitySet, { top: 500, select: aSel, keys: c.keys })
                .then(function (r) { that._data = r; that._build(); })
                .catch(function (e) { oKb.setProperty("/busy", false); that.showError(e); });
        },

        _laneKey: function (v) {
            return v === null || v === undefined ? "" : String(v);
        },

        _build: function () {
            var that = this, c = this.getConfig(), r = this._data, oKb = this.getModel("kb");
            if (!r) { return; }
            var q = (oKb.getProperty("/query") || "").toLowerCase();
            var aLanes = c.lanes.map(function (l) {
                return { key: l.value, title: that.getText(l.titleKey), items: [], count: 0 };
            });
            var mLane = {};
            aLanes.forEach(function (l) { mLane[l.key] = l; });
            r.rows.forEach(function (row, i) {
                var sKey = that._laneKey(row[c.laneField]);
                var oLane = mLane[sKey];
                if (!oLane) {
                    oLane = mLane[sKey] = { key: sKey, title: sKey || that.getText("kbBlank"), items: [], count: 0 };
                    aLanes.push(oLane);
                }
                var oCard = {
                    index: i,
                    title: formatter.value(row[c.cardTitle], c.cardTitleType),
                    subtitle: c.cardSubtitle ? formatter.value(row[c.cardSubtitle], c.cardSubtitleType) : "",
                    attrs: c.cardFields.map(function (f) {
                        return { label: f.label, value: formatter.value(row[f.field], f.uiType, f.scale) };
                    }).filter(function (a) { return a.value !== ""; })
                };
                if (q && JSON.stringify(oCard).toLowerCase().indexOf(q) < 0) { return; }
                oLane.items.push(oCard);
            });
            aLanes.forEach(function (l) { l.count = l.items.length; });
            oKb.setData({ lanes: aLanes, busy: false, query: q, total: r.count, shown: r.rows.length,
                          sampled: r.rows.length < r.count,
                          sampledText: this.getText("kbSampled", [r.rows.length, r.count]) });
        },

        onSearch: function (oEvent) {
            var q = oEvent.getParameter("query");
            if (q === undefined) { q = oEvent.getParameter("newValue"); }
            this.getModel("kb").setProperty("/query", q || "");
            this._build();
        },

        onRefresh: function () { this._load(); },

        onCardPress: function (oEvent) {
            var c = this.getConfig();
            if (!c.route) { return; }
            var oCard = oEvent.getSource().getBindingContext("kb").getObject();
            var sPath = this._data.paths[oCard.index];
            if (sPath) {
                this.getRouter().navTo(c.route, { key: encodeURIComponent(sPath.substring(1)) });
            }
        },

        onDrop: function (oEvent) {
            var that = this, c = this.getConfig();
            if (!c.move) { return; }
            var oDragged = oEvent.getParameter("draggedControl");
            var oDropped = oEvent.getParameter("droppedControl");
            if (!oDragged || !oDropped) { return; }
            var oCard = oDragged.getBindingContext("kb").getObject();
            var oList = oDropped.isA("sap.m.List") ? oDropped : oDropped.getParent();
            var oLane = oList && oList.getBindingContext("kb") && oList.getBindingContext("kb").getObject();
            if (!oLane) { return; }
            var oRow = this._data.rows[oCard.index];
            var vOld = oRow[c.laneField];
            if (this._laneKey(vOld) === oLane.key) { return; }
            var vNew = c.laneUiType === "number" ? Number(oLane.key)
                : c.laneUiType === "boolean" ? oLane.key === "true" : oLane.key;
            var m = {};
            m[c.laneField] = DataService.toWire(this.getModel(), c.laneEdmType, vNew);
            oRow[c.laneField] = vNew;           // optimistic
            this._build();
            DataService.updatePath(this.getModel(), this._data.paths[oCard.index], m,
                this._data.contexts[oCard.index]).then(function () {
                MessageToast.show(that.getText("kbMoved", [oCard.title, oLane.title]));
            }).catch(function (oError) {
                oRow[c.laneField] = vOld;
                that._build();
                that.showError(oError);
            });
        }
    });
});
'''

CALENDAR_CONTROLLER = r'''sap.ui.define([
    "./BaseController",
    "sap/ui/model/json/JSONModel",
    "../model/DataService",
    "../model/formatter"
], function (BaseController, JSONModel, DataService, formatter) {
    "use strict";

    var TYPES = ["Type01", "Type08", "Type06", "Type03", "Type05", "Type09", "Type02", "Type07",
                 "Type04", "Type10", "Type11", "Type12"];

    function toLocal(v, bDateOnly) {
        if (v === null || v === undefined || v === "") { return null; }
        var m = /\/Date\((-?\d+)/.exec(String(v));
        var d = v instanceof Date ? v : (m ? new Date(Number(m[1])) : new Date(bDateOnly && /^\d{4}-\d{2}-\d{2}$/.test(v) ? v + "T00:00:00Z" : v));
        if (isNaN(d.getTime())) { return null; }
        // date-only values are UTC midnight: show them on that calendar day
        return bDateOnly ? new Date(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) : d;
    }

    return BaseController.extend("@@APP@@.controller.Calendar", {

        onInit: function () {
            this.bindService();
            var oStart = new Date();
            oStart.setDate(1);
            this.setModel(new JSONModel({ items: [], legend: [], start: oStart, busy: true }), "cal");
            this.getRouter().getRoute(this.routeName("self")).attachPatternMatched(this._load, this);
        },

        _range: function () {
            var oCal = this.byId("calendar");
            var d = (oCal && oCal.getStartDate()) || new Date();
            var oFrom = new Date(d.getFullYear(), d.getMonth(), d.getDate() - 7);
            var oTo = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 45);
            return [oFrom, oTo];
        },

        _load: function () {
            var that = this, c = this.getConfig(), oCal = this.getModel("cal");
            var aRange = this._range();
            oCal.setProperty("/busy", true);
            var oFilter = DataService.filterFor(this.getModel(),
                { field: c.startField, mode: "range", type: c.startEdmType }, aRange[0], aRange[1]);
            var aSel = [c.startField, c.endField, c.titleField, c.textField, c.colorField].filter(Boolean);
            DataService.readRows(this.getModel(), c.entitySet,
                { top: 1000, select: aSel, keys: c.keys, filters: oFilter ? [oFilter] : [] })
                .then(function (r) {
                    that._data = r;
                    var mType = {}, aLegend = [];
                    var aItems = r.rows.map(function (row, i) {
                        var bDate = c.startUiType === "date";
                        var oS = toLocal(row[c.startField], bDate);
                        if (!oS) { return null; }
                        var oE = c.endField ? toLocal(row[c.endField], c.endUiType === "date") : null;
                        if (!oE || oE <= oS) {
                            oE = new Date(oS.getTime() + (bDate ? 86400000 : 3600000));
                        } else if (c.endUiType === "date") {
                            oE = new Date(oE.getTime() + 86400000);   // inclusive end day
                        }
                        var sColor = c.colorField ? String(row[c.colorField] === null || row[c.colorField] === undefined ? "" : row[c.colorField]) : "";
                        if (c.colorField && !mType[sColor]) {
                            mType[sColor] = TYPES[Object.keys(mType).length % TYPES.length];
                            aLegend.push({ text: (c.colorLabel ? c.colorLabel + ": " : "") + (sColor || "-"), type: mType[sColor] });
                        }
                        return {
                            index: i, start: oS, end: oE,
                            title: formatter.value(row[c.titleField], c.titleUiType),
                            text: c.textField ? formatter.value(row[c.textField], c.textUiType) : "",
                            type: c.colorField ? mType[sColor] : "Type01"
                        };
                    }).filter(Boolean);
                    oCal.setProperty("/items", aItems);
                    oCal.setProperty("/legend", aLegend);
                    oCal.setProperty("/busy", false);
                })
                .catch(function (e) { oCal.setProperty("/busy", false); that.showError(e); });
        },

        onStartDateChange: function () { this._load(); },

        onRefresh: function () { this._load(); },

        onAppointmentSelect: function (oEvent) {
            var c = this.getConfig();
            var oApp = oEvent.getParameter("appointment");
            if (!oApp || !c.route) { return; }
            var oItem = oApp.getBindingContext("cal").getObject();
            var sPath = this._data.paths[oItem.index];
            if (sPath) {
                this.getRouter().navTo(c.route, { key: encodeURIComponent(sPath.substring(1)) });
            }
        },

        onToday: function () {
            this.byId("calendar").setStartDate(new Date());
            this._load();
        }
    });
});
'''

ANALYTICS_CONTROLLER = r'''sap.ui.define([
    "./BaseController",
    "sap/ui/model/json/JSONModel",
    "sap/viz/ui5/controls/Popover",
    "../model/DataService",
    "../model/logic"
], function (BaseController, JSONModel, VizPopover, DataService, Logic) {
    "use strict";

    var SAMPLE = 1000;   // rows read per entity set to aggregate client-side

    function toDate(v) {
        if (v === null || v === undefined || v === "") { return null; }
        var m = /\/Date\((-?\d+)/.exec(String(v));
        var d = v instanceof Date ? v : (m ? new Date(Number(m[1])) : new Date(v));
        return isNaN(d.getTime()) ? null : d;
    }

    function bucket(v, sGrain) {
        if (!sGrain) {
            return v === null || v === undefined || v === "" ? "(none)" : String(v);
        }
        var d = toDate(v);
        if (!d) { return "(none)"; }
        var y = d.getUTCFullYear(), mo = d.getUTCMonth() + 1;
        switch (sGrain) {
            case "year": return String(y);
            case "quarter": return y + " Q" + (Math.floor((mo - 1) / 3) + 1);
            case "day": return y + "-" + (mo < 10 ? "0" : "") + mo + "-" + (d.getUTCDate() < 10 ? "0" : "") + d.getUTCDate();
            default: return y + "-" + (mo < 10 ? "0" : "") + mo;
        }
    }

    function agg(a, sAgg) {
        if (sAgg === "count") { return a.length; }
        var v = a.map(Number).filter(function (x) { return !isNaN(x); });
        if (!v.length) { return 0; }
        var s = v.reduce(function (x, y) { return x + y; }, 0);
        switch (sAgg) {
            case "average": return Math.round(s / v.length * 100) / 100;
            case "min": return Math.min.apply(null, v);
            case "max": return Math.max.apply(null, v);
            default: return Math.round(s * 100) / 100;
        }
    }

    return BaseController.extend("@@APP@@.controller.Analytics", {

        onInit: function () {
            this.bindService();
            this.setModel(new JSONModel({ charts: {}, notes: {}, busy: true }), "ana");
            this.getRouter().getRoute(this.routeName("self")).attachPatternMatched(this._load, this);
            var that = this;
            this.getConfig().charts.forEach(function (c) {
                var oViz = that.byId(c.id);
                if (!oViz) { return; }
                var bPie = c.type === "pie" || c.type === "donut";
                oViz.setVizProperties({
                    title: { visible: false },
                    legend: { visible: bPie || !!c.series || c.measures.length > 1 },
                    plotArea: { dataLabel: { visible: bPie || c.type === "heatmap" },
                                window: { start: "firstDataPoint", end: "lastDataPoint" } },
                    valueAxis: { title: { visible: c.measures.length === 1 } },
                    categoryAxis: { title: { visible: true } },
                    interaction: { selectability: { mode: "single" } }
                });
                new VizPopover().connect(oViz.getVizUid());
            });
        },

        _load: function () {
            var that = this, cfg = this.getConfig(), oAna = this.getModel("ana");
            var mSets = {};
            cfg.charts.forEach(function (c) {
                var a = mSets[c.entitySet] || (mSets[c.entitySet] = []);
                var aF = [c.dimension, c.series];
                c.measures.forEach(function (m) { aF = aF.concat([m.field], m.uses || []); });
                aF.forEach(function (f) { if (f && a.indexOf(f) < 0) { a.push(f); } });
            });
            oAna.setProperty("/busy", true);
            Promise.all(Object.keys(mSets).map(function (s) {
                return DataService.readRows(that.modelFor(s), s, { top: SAMPLE, select: mSets[s] })
                    .then(function (r) { return [s, r]; });
            })).then(function (aRes) {
                var mData = {}, mCharts = {}, mNotes = {};
                aRes.forEach(function (x) { mData[x[0]] = x[1]; });
                cfg.charts.forEach(function (c) {
                    var d = mData[c.entitySet] || { rows: [], count: 0 };
                    var mGroups = {};
                    d.rows.forEach(function (r) {
                        var sDim = bucket(r[c.dimension], c.timeGrain);
                        var sSer = c.series ? bucket(r[c.series], "") : "";
                        var k = sDim + "\u0001" + sSer;
                        var g = mGroups[k] || (mGroups[k] = { d: sDim, s: sSer, vals: c.measures.map(function () { return []; }) });
                        c.measures.forEach(function (m, i) {
                            g.vals[i].push(m.expr ? Logic.run((Logic.page(cfg.logicPage).measures || {})[m.expr], [r], null)
                                : (m.field ? r[m.field] : 1));
                        });
                    });
                    var aOut = Object.keys(mGroups).map(function (k) {
                        var g = mGroups[k], o = { d: g.d, s: g.s };
                        c.measures.forEach(function (m, i) { o["m" + i] = agg(g.vals[i], m.aggregation); });
                        return o;
                    });
                    if (c.timeGrain) {
                        aOut.sort(function (a, b) { return a.d < b.d ? -1 : a.d > b.d ? 1 : 0; });
                    } else if (!c.series) {
                        aOut.sort(function (a, b) { return b.m0 - a.m0; });
                        aOut = aOut.slice(0, 15);
                    }
                    mCharts[c.id] = aOut;
                    mNotes[c.id] = d.rows.length < d.count
                        ? that.getText("anaSampled", [d.rows.length, d.count]) : "";
                });
                oAna.setData({ charts: mCharts, notes: mNotes, busy: false });
            }).catch(function (e) {
                oAna.setProperty("/busy", false);
                that.showError(e);
            });
        },

        onRefresh: function () { this._load(); },

        onOpenList: function (oEvent) {
            var sRoute = oEvent.getSource().data("route");
            if (sRoute) { this.getRouter().navTo(sRoute); }
        }
    });
});
'''

SPLIT_CONTROLLER = r'''sap.ui.define([
    "./List.controller",
    "sap/ui/Device",
    "sap/m/MessageBox",
    "sap/m/MessageToast",
    "../model/DataService"
], function (ListController, Device, MessageBox, MessageToast, DataService) {
    "use strict";

    // List on the left, the selected record (sections, child tables, edit,
    // actions) on the right — one screen.
    return ListController.extend("@@APP@@.controller.Split", {

        onInit: function () {
            ListController.prototype.onInit.apply(this, arguments);
            var oVm = this.getModel("viewModel");
            oVm.setProperty("/editMode", false);
            oVm.setProperty("/selected", false);
            oVm.setProperty("/busy", false);
        },

        onUpdateFinished: function () {
            ListController.prototype.onUpdateFinished.apply(this, arguments);
            var oList = this._table();
            if (oList.getSelectedItem() || !oList.getItems().length || Device.system.phone) { return; }
            // keep the record the user was working on (after save / actions / refresh)
            var sKeep = this._keepPath;
            var oItem = (sKeep && oList.getItems().filter(function (i) {
                return i.getBindingContext() && i.getBindingContext().getPath() === sKeep;
            })[0]) || oList.getItems()[0];
            this._keepPath = null;
            oList.setSelectedItem(oItem);
            if (!sKeep || oItem.getBindingContext().getPath() !== sKeep) {
                this._show(oItem.getBindingContext());
            }
        },

        onSelectionChange: function (oEvent) {
            ListController.prototype.onSelectionChange.apply(this, arguments);
            var oItem = oEvent.getParameter("listItem");
            if (oItem) { this._show(oItem.getBindingContext()); }
        },

        _show: function (oCtx) {
            var oModel = this.getModel(), oVm = this.getModel("viewModel");
            if (DataService.hasChanges(oModel)) { DataService.reset(oModel); }
            oVm.setProperty("/editMode", false);
            oVm.setProperty("/selected", true);
            this.byId("detail").bindElement(DataService.isV4(oModel)
                ? { path: oCtx.getPath(), parameters: { $$updateGroupId: "changes" } }
                : { path: oCtx.getPath() });
        },

        _detailCtx: function () { return this.byId("detail").getBindingContext(); },

        onEdit: function () { this.getModel("viewModel").setProperty("/editMode", true); },

        onSave: function () {
            var that = this, oVm = this.getModel("viewModel"), oCtx = this._detailCtx();
            var oRow = oCtx ? Object.assign({}, oCtx.getObject()) : {};
            var mDerived = this.derivedValues(oRow);
            Object.assign(oRow, mDerived);
            var aRules = this.ruleErrors(oRow, "");
            if (aRules.length) { MessageBox.error(aRules.join("\n")); return; }
            Object.keys(mDerived).forEach(function (f) {
                if (DataService.isV4(that.getModel())) { oCtx.setProperty(f, mDerived[f]); }
                else { that.getModel().setProperty(oCtx.getPath() + "/" + f, mDerived[f]); }
            });
            oVm.setProperty("/busy", true);
            DataService.save(this.getModel()).then(function () {
                oVm.setProperty("/editMode", false);
                MessageToast.show(that.getText("saved"));
            }).catch(this.showError.bind(this)).finally(function () { oVm.setProperty("/busy", false); });
        },

        onCancel: function () {
            DataService.reset(this.getModel());
            this.getModel("viewModel").setProperty("/editMode", false);
        },

        onDelete: function () {
            var that = this, oCtx = this._detailCtx();
            if (!oCtx) { return; }
            MessageBox.confirm(this.getText("deleteConfirm", [1]), {
                onClose: function (sAction) {
                    if (sAction !== MessageBox.Action.OK) { return; }
                    DataService.remove(oCtx).then(function () {
                        MessageToast.show(that.getText("deleted"));
                        that.getModel("viewModel").setProperty("/selected", false);
                        that.byId("detail").unbindElement();
                        that.refreshData();
                    }).catch(that.showError.bind(that));
                }
            });
        },

        onDetailAction: function (oEvent) {
            var oAction = this.findAction(oEvent.getSource().data("actionId"));
            var oCtx = this._detailCtx();
            this.runAction(oAction, oCtx ? [oCtx] : []);
        },

        refreshData: function () {
            var oCtx = this._detailCtx();
            this._keepPath = oCtx ? oCtx.getPath() : null;
            ListController.prototype.refreshData.apply(this, arguments);
            var oB = this.byId("detail").getElementBinding();
            if (oB) { oB.refresh(); }
        }
    });
});
'''
