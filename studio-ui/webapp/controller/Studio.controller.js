sap.ui.define([
    "./BaseController",
    "sap/m/MessageBox",
    "sap/m/MessageToast",
    "../model/api"
], function (BaseController, MessageBox, MessageToast, api) {
    "use strict";

    var STEPS = ["describe", "model", "design", "preview", "generate"];

    return BaseController.extend("com.evoke.studio.controller.Studio", {
        onInit: function () {
            this.getRouter().getRoute("studio").attachPatternMatched(this._onMatched, this);
            this._previewHtml = "";
            var host = this.byId("previewHost");
            host.attachAfterRendering(this._paintPreview, this);
            // the preview follows the design: rebuild it when the design changes and the tab is open
            this.draft().attachPropertyChange(this._maybePreview, this);
            this.getOwnerComponent().getEventBus().subscribe("studio", "draft", this._maybePreview, this);
            this._previewSeq = 0;
        },

        _onMatched: function (e) {
            var step = (e.getParameter("arguments") || {}).step;
            if (STEPS.indexOf(step) >= 0 && this._enabled(step)) {
                this.draft().setProperty("/step", step);
            } else {
                this._go(this.draft().getProperty("/step") || "describe", true);
            }
            this._maybePreview();
        },

        _enabled: function (step) {
            var d = this.draft().getData();
            return step === "describe" || (step === "model" || step === "design" ? !!d.domain : !!d.design);
        },

        _go: function (step, replace) {
            this.draft().setProperty("/step", step);
            this.getOwnerComponent().saveDraft();
            this.getRouter().navTo("studio", { step: step }, replace !== false);
            this._maybePreview();
        },

        onStepSelect: function (e) { this._go(e.getParameter("key")); },

        onPrev: function () {
            var i = STEPS.indexOf(this.draft().getProperty("/step"));
            if (i > 0) { this._go(STEPS[i - 1]); }
        },

        onNext: function () {
            var i = STEPS.indexOf(this.draft().getProperty("/step"));
            if (i < STEPS.length - 1 && this._enabled(STEPS[i + 1])) { this._go(STEPS[i + 1]); }
        },

        onStartNew: function () {
            var comp = this.getOwnerComponent(), that = this;
            if (!comp.hasDraft()) { return; }
            MessageBox.confirm("Discard the application in progress and start a new one? Generated versions stay in the project.", {
                onClose: function (a) {
                    if (a === MessageBox.Action.OK) {
                        comp.resetDraft();
                        that._previewHtml = "";
                        that._go("describe");
                    }
                }
            });
        },

        // ── 1 Describe ──
        onExample: function (e) {
            var ex = e.getSource().getBindingContext("app").getObject();
            this.getOwnerComponent().setDraft({ requirement: ex.text, questions: [] });
        },

        onQuestions: function () {
            this.startJob("questions", { requirement: this.draft().getProperty("/requirement") });
        },

        onDesignDomain: function () {
            var d = this.draft().getData(), answers = {};
            (d.questions || []).forEach(function (q) {
                if ((q.answer || "").trim()) { answers[q.question] = q.answer.trim(); }
            });
            this.startJob("domain", { requirement: d.requirement, answers: answers });
        },

        // ── 2 Data model ──
        onApplyJson: function () {
            var comp = this.getOwnerComponent(), that = this, domain;
            try {
                domain = JSON.parse(this.draft().getProperty("/domainJson"));
            } catch (e) {
                MessageBox.error("Not valid JSON: " + e.message);
                return;
            }
            var btn = this.byId("btnApplyJson");
            btn.setBusy(true);
            api.post("domain/validate", { domain: domain }).then(function (r) {
                comp.setDraft({ domain: r.domain, domainNotes: r.notes || [], domainSummary: r.summary || "",
                                domainSource: "edited", domainJson: JSON.stringify(r.domain, null, 2),
                                design: null, generated: null, validation: null, previewStale: true });
                MessageToast.show("Data model updated - design the screens again.");
            }, function (e) { that.showError(e, "Data model"); })
                .finally(function () { btn.setBusy(false); });
        },

        onDbChange: function () {
            this.getOwnerComponent().saveDraft();
        },

        // ── 3 Screens ──
        onDesign: function () {
            var d = this.draft().getData();
            this.startJob("design", { requirement: d.requirement, domain: d.domain, floorplan: d.floorplan || "" });
        },

        // ── 4 Try it ──
        _maybePreview: function () {
            var d = this.draft().getData();
            if (d.step === "preview" && d.design && (d.previewStale || !this._previewHtml)) {
                this.onPreview();
            }
        },

        onPreview: function () {
            var d = this.draft().getData(), that = this;
            if (!d.design || this._previewLoading) { return; }
            this._previewLoading = true;
            this.draft().setProperty("/previewStale", false);
            var host = this.byId("previewHost");
            host.setBusy(true);
            api.postText("preview", { blueprint: d.design.blueprint, domain: d.domain }).then(function (html) {
                that._previewHtml = html || "";
                that._previewSeq += 1;
                that._paintPreview();
            }, function (e) {
                that.draft().setProperty("/previewStale", true);
                that.showError(e, "Preview");
            }).finally(function () {
                that._previewLoading = false;
                host.setBusy(false);
            });
        },

        _paintPreview: function () {
            var frame = document.getElementById("evPreviewFrame");
            if (frame && this._previewHtml && frame.getAttribute("data-painted") !== String(this._previewSeq)) {
                frame.srcdoc = this._previewHtml;
                frame.setAttribute("data-painted", String(this._previewSeq));
            }
        },

        // ── 5 Generate & validate ──
        onGenerate: function () {
            var d = this.draft().getData();
            this.startJob("generate", {
                requirement: d.requirement, domain: d.domain, db: d.db || "sqlite", blueprint: d.design.blueprint,
                app_id: d.appId, project_id: d.projectId || null,
                design: { spec: d.design.spec || null, coverage: d.design.coverage || [] }
            });
        },

        onValidate: function () {
            var d = this.draft().getData();
            this.startJob("validate", { version_id: d.generated.version_id, level: d.validateLevel || "standard" });
        },

        onDownload: function () {
            var vid = this.draft().getProperty("/generated/version_id");
            if (vid) { window.open(api.url("versions/" + encodeURIComponent(vid) + "/zip"), "_blank"); }
        },

        onOpenProject: function () {
            var pid = this.draft().getProperty("/projectId");
            if (pid) { this.getRouter().navTo("project", { pid: pid }); }
        }
    });
});
