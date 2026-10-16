sap.ui.define([
    "sap/ui/base/Object",
    "sap/m/MessageToast",
    "sap/m/MessageBox",
    "./api"
], function (BaseObject, MessageToast, MessageBox, api) {
    "use strict";

    var POLL_MS = 1500;

    /**
     * Long-running studio work (AI design, generation, validation) runs as server-side jobs.
     * The UI only starts them and polls; navigating between screens never stops a job, and a
     * page reload re-attaches to the user's running jobs (GET api/jobs).
     *
     * jobs model: { byKind: {<kind>: job}, list: [job], count: n }
     */
    return BaseObject.extend("com.evoke.studio.model.JobService", {
        constructor: function (oModel, fnApply) {
            this._model = oModel;
            this._apply = fnApply;           // (kind, result, job) -> void
            this._timer = null;
            this._tracked = {};              // id -> kind
        },

        isRunning: function (kind) {
            return !!this._model.getProperty("/byKind/" + kind);
        },

        /** POST api/jobs/<kind> and track the job. Resolves with the job id. */
        start: function (kind, payload) {
            if (this.isRunning(kind)) {
                return Promise.reject(new Error("This step is already running."));
            }
            var that = this;
            // optimistic placeholder so the button is disabled immediately
            this._set(kind, { id: "", kind: kind, label: "Starting…", status: "running", seconds: 0, messages: [] });
            return api.post("jobs/" + kind, payload).then(function (r) {
                that._tracked[r.job_id] = kind;
                that._set(kind, { id: r.job_id, kind: kind, label: "Starting…", status: "running", seconds: 0, messages: [] });
                that._schedule(0);
                return r.job_id;
            }, function (e) {
                that._clear(kind);
                throw e;
            });
        },

        /** Re-attach to jobs still running on the server (after a reload). */
        resume: function () {
            var that = this;
            return api.get("jobs").then(function (r) {
                (r.jobs || []).forEach(function (j) {
                    if (j.status === "running" && !that._tracked[j.id]) {
                        that._tracked[j.id] = j.kind;
                        that._set(j.kind, j);
                    }
                });
                if (Object.keys(that._tracked).length) { that._schedule(0); }
            }).catch(function () { /* jobs list is a convenience */ });
        },

        _schedule: function (ms) {
            if (this._timer) { return; }
            var that = this;
            this._timer = setTimeout(function () {
                that._timer = null;
                that._poll();
            }, ms === undefined ? POLL_MS : ms);
        },

        _poll: function () {
            var that = this;
            var ids = Object.keys(this._tracked);
            if (!ids.length) { return; }
            Promise.all(ids.map(function (id) {
                return api.get("jobs/" + id).then(function (j) {
                    that._handle(id, j);
                }, function (e) {
                    if (e.status === 404) {          // server restarted: the job is gone
                        var kind = that._tracked[id];
                        delete that._tracked[id];
                        that._clear(kind);
                        MessageBox.error("A background job was lost (the studio was restarted). Please start it again.");
                    }
                });
            })).then(function () {
                if (Object.keys(that._tracked).length) { that._schedule(); }
            });
        },

        _handle: function (id, j) {
            var kind = this._tracked[id];
            if (j.status === "running") {
                this._set(kind, j);
                return;
            }
            delete this._tracked[id];
            this._clear(kind);
            if (j.status === "done") {
                try {
                    this._apply(kind, j.result, j);
                    MessageToast.show(j.label + " - finished in " + Math.round(j.seconds) + "s");
                } catch (e) {
                    MessageBox.error(j.label + ": the result could not be shown (" + e.message + ").");
                }
            } else {
                MessageBox.error(j.error || "The job failed.", { title: j.label || "Job failed" });
            }
        },

        _set: function (kind, job) {
            var by = Object.assign({}, this._model.getProperty("/byKind"));
            by[kind] = job;
            this._publish(by);
        },

        _clear: function (kind) {
            var by = Object.assign({}, this._model.getProperty("/byKind"));
            delete by[kind];
            this._publish(by);
        },

        _publish: function (by) {
            var list = Object.keys(by).map(function (k) { return by[k]; });
            this._model.setData({ byKind: by, list: list, count: list.length });
        },

        destroy: function () {
            if (this._timer) { clearTimeout(this._timer); }
            BaseObject.prototype.destroy.apply(this, arguments);
        }
    });
});
