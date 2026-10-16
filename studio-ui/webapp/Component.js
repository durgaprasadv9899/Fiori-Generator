sap.ui.define([
    "sap/ui/core/UIComponent",
    "sap/ui/model/json/JSONModel",
    "sap/ui/Device",
    "./model/api",
    "./model/JobService"
], function (UIComponent, JSONModel, Device, api, JobService) {
    "use strict";

    var DRAFT_KEY = "evoke.studio.draft.v1";

    function emptyDraft() {
        return {
            step: "describe", requirement: "", questions: [], domain: null, domainNotes: [], domainSummary: "",
            domainSource: "", domainJson: "", db: "sqlite", floorplan: "", design: null, appId: "", projectId: null,
            generated: null, validation: null, validateLevel: "standard", previewHtml: "", previewStale: true
        };
    }

    return UIComponent.extend("com.evoke.studio.Component", {
        metadata: { manifest: "json", interfaces: ["sap.ui.core.IAsyncContentCreation"] },

        init: function () {
            UIComponent.prototype.init.apply(this, arguments);
            this.setModel(new JSONModel({ isPhone: Device.system.phone }), "device");
            this.setModel(new JSONModel({ loaded: false, user: { name: "", roles: [] }, ai: {}, store: {} }), "me");
            this.setModel(new JSONModel({ examples: [] }), "app");
            this.setModel(new JSONModel(this._loadDraft()), "draft");
            this.setModel(new JSONModel({ byKind: {}, list: [], count: 0 }), "jobs");

            this.jobs = new JobService(this.getModel("jobs"), this.applyJob.bind(this));
            this._watchDraft();
            this._loadContext();
            this.getRouter().initialize();
        },

        _loadContext: function () {
            var me = this.getModel("me"), app = this.getModel("app");
            api.get("me").then(function (r) {
                me.setData(Object.assign({ loaded: true }, r));
            }, function (e) {
                me.setProperty("/loaded", true);
                me.setProperty("/error", e.message);
            });
            api.get("examples").then(function (r) {
                var ex = (r && r.new_app) || {};
                app.setProperty("/examples", Object.keys(ex).map(function (k) { return { title: k, text: ex[k] }; }));
            }, function () { /* examples are optional */ });
            this.jobs.resume();
        },

        // ── draft (the application being built) — survives reloads via localStorage ──
        _loadDraft: function () {
            try {
                var raw = window.localStorage.getItem(DRAFT_KEY);
                if (raw) { return Object.assign(emptyDraft(), JSON.parse(raw), { previewHtml: "", previewStale: true }); }
            } catch (e) { /* private mode / blocked storage */ }
            return emptyDraft();
        },

        _watchDraft: function () {
            var that = this, timer = null;
            this.getModel("draft").attachPropertyChange(function () { that.saveDraft(); });
            this.saveDraft = function () {
                clearTimeout(timer);
                timer = setTimeout(function () {
                    try {
                        var d = Object.assign({}, that.getModel("draft").getData());
                        delete d.previewHtml;           // large and cheap to rebuild
                        window.localStorage.setItem(DRAFT_KEY, JSON.stringify(d));
                    } catch (e) { /* quota / blocked storage: the draft just isn't kept */ }
                }, 300);
            };
        },

        hasDraft: function () {
            var d = this.getModel("draft").getData();
            return !!((d.requirement || "").trim() || d.domain);
        },

        resetDraft: function () {
            this.getModel("draft").setData(emptyDraft());
            this.saveDraft();
        },

        setDraft: function (patch) {
            var m = this.getModel("draft");
            m.setData(Object.assign({}, m.getData(), patch));
            this.saveDraft();
            this.getEventBus().publish("studio", "draft");
        },

        // ── job results -> draft ──
        applyJob: function (kind, res) {
            var d = this.getModel("draft");
            switch (kind) {
            case "questions":
                this.setDraft({ questions: (res.questions || []).map(function (q) {
                    return Object.assign({ answer: "" }, q, { options: (q.options || []).map(function (o) { return { text: o }; }) });
                }) });
                if (!(res.questions || []).length) {
                    sap.ui.require(["sap/m/MessageToast"], function (T) {
                        T.show("The requirement is clear enough - design the data model.");
                    });
                }
                break;
            case "domain":
                this.setDraft({ domain: res.domain, domainNotes: res.notes || [], domainSummary: res.summary || "",
                                domainSource: res.source || "", domainJson: JSON.stringify(res.domain, null, 2),
                                design: null, generated: null, validation: null, previewStale: true,
                                appId: d.getProperty("/appId") || this.suggestAppId(res.domain),
                                step: d.getProperty("/step") === "describe" ? "model" : d.getProperty("/step") });
                break;
            case "design":
                this.setDraft({ design: res, generated: null, validation: null, previewStale: true });
                break;
            case "generate":
                this.setDraft({ generated: res, projectId: res.project_id, validation: null });
                this.getEventBus().publish("studio", "projectsChanged");
                break;
            case "validate":
                this.setDraft({ validation: res });
                break;
            default:
                break;
            }
        },

        suggestAppId: function (domain) {
            var n = ((domain && (domain.name || domain.title)) || "app").toLowerCase().replace(/[^a-z0-9]/g, "");
            return "com.evoke." + (n || "app").slice(0, 30);
        },

        getContentDensityClass: function () {
            return Device.support.touch ? "sapUiSizeCozy" : "sapUiSizeCompact";
        },

        destroy: function () {
            if (this.jobs) { this.jobs.destroy(); }
            UIComponent.prototype.destroy.apply(this, arguments);
        }
    });
});
