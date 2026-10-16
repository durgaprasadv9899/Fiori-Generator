sap.ui.define([
    "./BaseController",
    "sap/ui/model/json/JSONModel",
    "sap/ui/core/Fragment",
    "sap/m/MessageBox",
    "sap/m/MessageToast",
    "../model/api"
], function (BaseController, JSONModel, Fragment, MessageBox, MessageToast, api) {
    "use strict";

    function coverageScore(cov) {
        if (!cov || !cov.length) { return null; }
        var pts = cov.reduce(function (s, c) { return s + (c.status === "covered" ? 1 : c.status === "partial" ? 0.5 : 0); }, 0);
        return Math.round(100 * pts / cov.length);
    }

    return BaseController.extend("com.evoke.studio.controller.ProjectDetail", {
        onInit: function () {
            this.getView().setModel(new JSONModel({ project: {}, versions: [], deployments: [], busy: false }), "detail");
            this.getView().setModel(new JSONModel({ files: [], path: "", content: "", busy: false, title: "" }), "files");
            this.getRouter().getRoute("project").attachPatternMatched(this._onMatched, this);
        },

        _onMatched: function (e) {
            this._pid = e.getParameter("arguments").pid;
            this.load();
        },

        load: function () {
            var m = this.getView().getModel("detail"), that = this;
            m.setData({ project: {}, versions: [], deployments: [], busy: true });
            api.get("projects/" + encodeURIComponent(this._pid)).then(function (r) {
                m.setData({ project: r.project || {}, versions: r.versions || [], deployments: r.deployments || [], busy: false });
            }, function (e) {
                m.setProperty("/busy", false);
                that.showError(e, "Project");
            });
        },

        onBack: function () { this.getRouter().navTo("projects"); },

        _version: function (e) { return e.getSource().getBindingContext("detail").getObject(); },

        onDownload: function (e) {
            window.open(api.url("versions/" + encodeURIComponent(this._version(e).id) + "/zip"), "_blank");
        },

        onBrowse: function (e) {
            var v = this._version(e), fm = this.getView().getModel("files"), view = this.getView(), that = this;
            fm.setData({ files: [], path: "", content: "", busy: true, title: "v" + v.number + " - " + (v.label || "files"),
                         vid: v.id });
            if (!this._filesDialog) {
                this._filesDialog = Fragment.load({ id: view.getId(), name: "com.evoke.studio.fragment.FilesDialog", controller: this })
                    .then(function (d) { view.addDependent(d); return d; });
            }
            this._filesDialog.then(function (d) { d.open(); });
            api.get("versions/" + encodeURIComponent(v.id)).then(function (r) {
                fm.setProperty("/files", (r.files || []).sort().map(function (p) { return { path: p }; }));
                fm.setProperty("/busy", false);
            }, function (err) {
                fm.setProperty("/busy", false);
                that.showError(err, "Files");
            });
        },

        onFileSelect: function (e) {
            var item = e.getParameter("listItem"), fm = this.getView().getModel("files"), that = this;
            var path = item.getBindingContext("files").getProperty("path");
            fm.setProperty("/path", path);
            fm.setProperty("/content", "Loading…");
            api.getText("versions/" + encodeURIComponent(fm.getProperty("/vid")) + "/file?path=" + encodeURIComponent(path))
                .then(function (t) { fm.setProperty("/content", t); }, function (err) {
                    fm.setProperty("/content", "");
                    that.showError(err, path);
                });
        },

        onCloseFiles: function () {
            this._filesDialog.then(function (d) { d.close(); });
        },

        onOpenLatest: function () {
            var v = this.getView().getModel("detail").getProperty("/versions/0");
            if (v) { this._openInStudio(v.id); }
        },

        onOpenVersion: function (e) { this._openInStudio(this._version(e).id); },

        /** Load a saved version into the draft and continue in the guided flow (Screens step). */
        _openInStudio: function (vid) {
            var comp = this.getOwnerComponent(), router = this.getRouter(), that = this;
            var go = function () {
                api.get("versions/" + encodeURIComponent(vid)).then(function (r) {
                    var s = r.snapshot || {}, v = r.version || {};
                    if (!s.domain) {
                        MessageBox.information("This version was built against an existing SAP service. The new UI " +
                            "currently opens applications with a generated backend; use the classic studio for SAP-service apps.");
                        return;
                    }
                    var design = s.blueprint ? {
                        blueprint: s.blueprint, notes: [], source: "restored", spec: (s.design || {}).spec || null,
                        coverage: (s.design || {}).coverage || [], coverage_score: coverageScore((s.design || {}).coverage),
                        steps: [], structure: "", service_url: ""
                    } : null;
                    comp.resetDraft();
                    comp.setDraft({
                        requirement: s.requirement || "", domain: s.domain, domainJson: JSON.stringify(s.domain, null, 2),
                        domainSummary: "", domainNotes: [], domainSource: "saved", db: s.db || "sqlite", design: design,
                        appId: s.app_id || comp.suggestAppId(s.domain), projectId: v.project_id || that._pid,
                        generated: { project_id: v.project_id || that._pid, version_id: v.id, version_number: v.number,
                                     file_count: v.file_count || (r.files || []).length, files: r.files || [],
                                     quality: null, notes: [] },
                        step: design ? "design" : "model"
                    });
                    MessageToast.show("Version v" + v.number + " opened");
                    router.navTo("studio", { step: design ? "design" : "model" });
                }, function (err) { that.showError(err, "Open version"); });
            };
            if (comp.hasDraft()) {
                MessageBox.confirm("Replace the application currently in progress with this version?", {
                    onClose: function (a) { if (a === MessageBox.Action.OK) { go(); } }
                });
            } else {
                go();
            }
        }
    });
});
