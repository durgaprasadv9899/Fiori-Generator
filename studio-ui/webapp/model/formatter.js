sap.ui.define([], function () {
    "use strict";

    var STATUS = {
        covered: ["Covered", "Success"], partial: ["Partial", "Warning"], missing: ["Missing", "Error"],
        passed: ["Passed", "Success"], failed: ["Failed", "Error"], not_tested: ["Not tested", "None"],
        warning: ["Warning", "Warning"], running: ["Running", "Information"], done: ["Done", "Success"],
        error: ["Error", "Error"], succeeded: ["Succeeded", "Success"], skipped: ["Skipped", "None"]
    };

    return {
        date: function (epoch) {
            if (!epoch) { return ""; }
            var d = new Date(epoch * 1000);
            return d.toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" }) + " " +
                d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
        },
        statusText: function (s) { return (STATUS[s] || [s || ""])[0]; },
        statusState: function (s) { return (STATUS[s] || [null, "None"])[1]; },
        criticality: function (c) {
            return { Positive: "Success", Negative: "Error", Critical: "Warning", Information: "Information" }[c] || "None";
        },
        modeText: function (m) {
            return { sap: "SAP service", standalone: "New app (CAP)", "import": "Imported" }[m] || m || "";
        },
        modeState: function (m) {
            return { sap: "Information", standalone: "Success", "import": "Warning" }[m] || "None";
        },
        validationText: function (v) {
            if (!v || !v.summary) { return "Not validated"; }
            if (v.blocking && v.blocking.length) { return "Blocked (" + v.blocking.length + ")"; }
            return v.summary.passed + " passed" + (v.summary.not_tested ? ", " + v.summary.not_tested + " not tested" : "");
        },
        validationState: function (v) {
            if (!v || !v.summary) { return "None"; }
            return v.blocking && v.blocking.length ? "Error" : "Success";
        },
        yesNo: function (b) { return b ? "Yes" : ""; },
        calc: function (f) { return f ? "Calculated" : ""; },
        fieldType: function (t, len, prec, scale) {
            if (len) { return t + "(" + len + ")"; }
            if (prec) { return t + "(" + prec + (scale !== undefined && scale !== null ? "," + scale : "") + ")"; }
            return t || "";
        },
        join: function (a) { return Array.isArray(a) ? a.join(" / ") : (a || ""); },
        lastMessages: function (m) { return Array.isArray(m) ? m.slice(-4).join("\n") : ""; },
        seconds: function (s) { return s === undefined || s === null ? "" : Math.round(s) + "s"; },
        score: function (s) { return s === null || s === undefined ? "-" : s + "%"; },
        scoreState: function (s) {
            if (s === null || s === undefined) { return "None"; }
            return s >= 85 ? "Success" : s >= 60 ? "Warning" : "Error";
        },
        qualityText: function (q) {
            if (!q) { return ""; }
            return q.errors ? q.errors + " error(s)" : "Passed" + (q.warnings ? " (" + q.warnings + " warning(s))" : "");
        },
        qualityState: function (q) { return q && q.errors ? "Error" : "Success"; },
        severityType: function (s) { return { error: "Error", warning: "Warning" }[s] || "Information"; },
        initials: function (n) {
            return (n || "?").split(/[\s@.]+/).filter(Boolean).slice(0, 2).map(function (p) { return p[0].toUpperCase(); }).join("");
        },
        roles: function (r) { return Array.isArray(r) ? r.join(", ") : ""; },
        nonEmpty: function (v) { return Array.isArray(v) ? v.length > 0 : !!v; },
        appIdOk: function (id) { return /^[a-zA-Z]\w*(\.[a-zA-Z]\w*)+$/.test(id || ""); },
        appIdState: function (id) { return /^[a-zA-Z]\w*(\.[a-zA-Z]\w*)+$/.test(id || "") ? "None" : "Error"; },
        canGenerate: function (design, job, id) {
            return !!design && !job && /^[a-zA-Z]\w*(\.[a-zA-Z]\w*)+$/.test(id || "");
        },
        pageCount: function (pages) { return String(Array.isArray(pages) && pages.length ? pages.length : 2); },
        titleCount: function (title, list) { return (title || "") + " (" + (Array.isArray(list) ? list.length : 0) + ")"; },
        blockingText: function (b) {
            return Array.isArray(b) && b.length ? "Deployment is blocked by: " + b.join("; ")
                : "No blocking failures - this version can be deployed.";
        }
    };
});
