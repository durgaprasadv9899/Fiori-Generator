sap.ui.define([
    "./BaseController",
    "sap/ui/model/json/JSONModel",
    "sap/ui/core/Fragment",
    "sap/m/MessageBox"
], function (BaseController, JSONModel, Fragment, MessageBox) {
    "use strict";

    return BaseController.extend("com.evoke.studio.controller.App", {
        onInit: function () {
            this.getView().addStyleClass(this.getOwnerComponent().getContentDensityClass());
            this.getView().setModel(new JSONModel({ navKey: "projects" }), "view");
            this.getRouter().attachRouteMatched(function (e) {
                var name = e.getParameter("name");
                if (window.innerWidth < 600) {      // phones: the side navigation is an overlay — close it
                    this.byId("toolPage").setSideExpanded(false);
                }
                this.getView().getModel("view").setProperty("/navKey", name === "studio" ? "studio" : "projects");
            }, this);
        },

        onSideToggle: function () {
            var tp = this.byId("toolPage");
            tp.setSideExpanded(!tp.getSideExpanded());
        },

        onNavSelect: function (e) {
            var key = e.getParameter("item").getKey();
            if (key === "projects") {
                this.getRouter().navTo("projects");
            } else if (key === "studio") {
                this.openStudio();
            }
        },

        /** "New application": continue the draft in progress, or start a new one. */
        openStudio: function () {
            var comp = this.getOwnerComponent(), router = this.getRouter();
            if (!comp.hasDraft()) {
                router.navTo("studio", { step: "describe" });
                return;
            }
            var cont = this.text("continueDraft"), fresh = this.text("startNew");
            MessageBox.confirm(this.text("draftQuestion"), {
                title: this.text("navNewApp"),
                actions: [cont, fresh, MessageBox.Action.CANCEL],
                emphasizedAction: cont,
                onClose: function (a) {
                    if (a === fresh) {
                        comp.resetDraft();
                        router.navTo("studio", { step: "describe" });
                    } else if (a === cont) {
                        router.navTo("studio", { step: comp.getModel("draft").getProperty("/step") || "describe" });
                    } else {               // keep the nav selection in sync with the shown page
                        var h = router.getHashChanger().getHash();
                        this.getView().getModel("view").setProperty("/navKey", h.indexOf("studio") === 0 ? "studio" : "projects");
                    }
                }.bind(this)
            });
        },

        onJobsPress: function (e) {
            var btn = e.getSource(), view = this.getView();
            if (!this._jobsPopover) {
                this._jobsPopover = Fragment.load({ id: view.getId(), name: "com.evoke.studio.fragment.JobsPopover", controller: this })
                    .then(function (p) { view.addDependent(p); return p; });
            }
            this._jobsPopover.then(function (p) { p.openBy(btn); });
        },

        onUserPress: function (e) {
            var src = e.getSource(), view = this.getView();
            if (!this._userPopover) {
                this._userPopover = Fragment.load({ id: view.getId(), name: "com.evoke.studio.fragment.UserPopover", controller: this })
                    .then(function (p) { view.addDependent(p); return p; });
            }
            this._userPopover.then(function (p) { p.openBy(src); });
        }
    });
});
