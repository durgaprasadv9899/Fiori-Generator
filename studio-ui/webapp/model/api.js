sap.ui.define([], function () {
    "use strict";

    /**
     * Thin client for the studio API. URLs are relative ("api/..."): locally the Python API serves
     * this app, on BTP the approuter serves it and forwards /api with the user's token.
     */
    function errorText(status, body) {
        if (body && typeof body === "object") {
            var d = body.detail;
            if (typeof d === "string") { return d; }
            if (Array.isArray(d)) {           // FastAPI validation errors
                return d.map(function (e) { return (e.loc || []).join(".") + ": " + e.msg; }).join("; ");
            }
            if (d) { return JSON.stringify(d); }
        }
        if (typeof body === "string" && body.trim()) { return body.slice(0, 400); }
        return "HTTP " + status;
    }

    function request(method, path, data, asText) {
        var opts = { method: method, headers: { "Accept": asText ? "text/html, text/plain" : "application/json" },
                     credentials: "same-origin" };
        if (data !== undefined) {
            opts.headers["Content-Type"] = "application/json";
            opts.body = JSON.stringify(data);
        }
        return fetch("api/" + path, opts).then(function (res) {
            var ct = res.headers.get("Content-Type") || "";
            var parse = ct.indexOf("application/json") >= 0 ? res.json() : res.text();
            return parse.catch(function () { return null; }).then(function (body) {
                if (!res.ok) {
                    if (res.status === 401) {     // approuter session expired: reload to sign in again
                        var e401 = new Error("Your session has expired - reload the page to sign in again.");
                        e401.status = 401;
                        throw e401;
                    }
                    var err = new Error(errorText(res.status, body));
                    err.status = res.status;
                    throw err;
                }
                return body;
            });
        }, function (e) {
            throw new Error("The studio API is not reachable (" + e.message + ").");
        });
    }

    return {
        get: function (path) { return request("GET", path); },
        post: function (path, data) { return request("POST", path, data || {}); },
        postText: function (path, data) { return request("POST", path, data || {}, true); },
        getText: function (path) { return request("GET", path, undefined, true); },
        url: function (path) { return "api/" + path; }
    };
});
