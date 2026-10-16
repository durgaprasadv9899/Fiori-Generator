sap.ui.define([
    "./BaseController",
    "sap/ui/model/json/JSONModel",
    "sap/ui/model/Filter",
    "sap/ui/model/FilterOperator",
    "../model/api"
], function (BaseController, JSONModel, Filter, FilterOperator, api) {
    "use strict";

    return BaseController.extend("com.evoke.studio.controller.Projects", {
        onInit: function () {
            this.getView().setModel(new JSONModel({ projects: [], busy: false }), "projects");
            this.getRouter().getRoute("projects").attachPatternMatched(this.load, this);
            this.getOwnerComponent().getEventBus().subscribe("studio", "projectsChanged", this.load, this);
        },

        load: function () {
            var m = this.getView().getModel("projects"), that = this;
            m.setProperty("/busy", true);
            api.get("projects").then(function (r) {
                m.setProperty("/projects", r.projects || []);
            }, function (e) { that.showError(e, "Projects"); })
                .finally(function () { m.setProperty("/busy", false); });
        },

        onSearch: function (e) {
            var q = (e.getParameter("newValue") || "").trim();
            var b = this.byId("projectsTable").getBinding("items");
            b.filter(q ? [new Filter({ filters: [
                new Filter("name", FilterOperator.Contains, q),
                new Filter("app_id", FilterOperator.Contains, q),
                new Filter("owner", FilterOperator.Contains, q)
            ], and: false })] : []);
        },

        onOpen: function (e) {
            var p = e.getSource().getBindingContext("projects").getObject();
            this.getRouter().navTo("project", { pid: p.id });
        },

        onNewApp: function () {
            this.getOwnerComponent().getRootControl().getController().openStudio();
        }
    });
});
