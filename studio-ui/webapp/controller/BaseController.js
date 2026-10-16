sap.ui.define([
    "sap/ui/core/mvc/Controller",
    "sap/ui/core/routing/History",
    "sap/m/MessageBox",
    "../model/formatter"
], function (Controller, History, MessageBox, formatter) {
    "use strict";

    return Controller.extend("com.evoke.studio.controller.BaseController", {
        fmt: formatter,

        getRouter: function () { return this.getOwnerComponent().getRouter(); },
        model: function (name) { return this.getOwnerComponent().getModel(name); },
        draft: function () { return this.model("draft"); },
        jobs: function () { return this.getOwnerComponent().jobs; },
        text: function (key, args) {
            return this.getOwnerComponent().getModel("i18n").getResourceBundle().getText(key, args);
        },

        navBack: function (fallback) {
            if (History.getInstance().getPreviousHash() !== undefined) {
                window.history.go(-1);
            } else {
                this.getRouter().navTo(fallback || "projects", {}, true);
            }
        },

        showError: function (e, title) {
            MessageBox.error((e && e.message) || String(e), { title: title || "Error" });
        },

        /** Start a background job; errors from the start request are shown, results land in the draft. */
        startJob: function (kind, payload) {
            var that = this;
            return this.jobs().start(kind, payload).catch(function (e) { that.showError(e); });
        }
    });
});
