"""
studio_api.py — REST API of the Evoke Fiori App Studio (contract: docs/studio-api-v2.md).

The web front end (studio-web/, React + UI5 Web Components) talks to this API; on SAP BTP the
approuter authenticates the user (XSUAA), serves the UI and forwards /api/* with the user's token.
The API is a thin layer over the existing engine — "connect to SAP" (destinations, catalog,
$metadata, multi-service), standalone apps (data model -> CAP backend), AI design + refinement,
interactive preview, generation, build validation, the HANA workspace, approvals, audit and
Cloud Foundry deployment. Long-running work runs as background jobs (jobs.py); the UI polls them.

Run locally:   python -m uvicorn studio_api:app --port 8620     (UI at http://localhost:8620/)
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from fastapi import Body, Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

import access_control as ac
import jobs
import workspace as wsp

# ─────────────────────────────────────────────────────────────────────────────
# AI Core (local: profile file; on CF: environment)
# ─────────────────────────────────────────────────────────────────────────────
PROFILE_PATH = "profiles/config_evoke.json"
AICORE_KEYS = ("AICORE_AUTH_URL", "AICORE_CLIENT_ID", "AICORE_CLIENT_SECRET", "AICORE_RESOURCE_GROUP",
               "AICORE_BASE_URL")
_AI: Dict[str, Any] = {"fn": None, "model": None, "error": "", "loaded": False}


def _ai():
    """(ai_fn, model) — resolved once per process; (None, None) when AI Core is unavailable."""
    if not _AI["loaded"]:
        _AI["loaded"] = True
        if os.environ.get("STUDIO_NO_AI") == "1":
            _AI["error"] = "AI disabled (STUDIO_NO_AI=1) — rule-based design."
            return None, None
        try:
            if os.path.exists(PROFILE_PATH):
                with open(PROFILE_PATH, encoding="utf-8") as f:
                    prof = json.load(f)
                for k in AICORE_KEYS:
                    os.environ.setdefault(k, prof[k])
            missing = [k for k in AICORE_KEYS if not os.environ.get(k)]
            if missing:
                raise RuntimeError("AI Core credentials missing: " + ", ".join(missing))
            import ai_client
            models = [m for m in ai_client.available_models() if not m.startswith("text-embedding")]
            if not models:
                raise RuntimeError("no running model deployment in AI Core")
            model = ai_client.pick_model(models=models)
            _AI.update(fn=ai_client.make_ai_fn(model, temperature=0.15, max_tokens=16000), model=model)
        except Exception as e:  # noqa: BLE001 — rule-based design still works
            _AI["error"] = str(e)[:300]
    return _AI["fn"], _AI["model"]


# ─────────────────────────────────────────────────────────────────────────────
# App, identity, errors
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(title="Evoke Fiori App Studio API", version="2.0")
STORE = wsp.get_store()


def current_user(request: Request) -> ac.User:
    """XSUAA token forwarded by the approuter; local development (no auth configured): the OS user."""
    mode = ac.auth_mode()
    if mode == "xsuaa":
        u = ac.user_from_headers(dict(request.headers))
        if not u:
            raise HTTPException(401, "Sign in through the studio URL (SAP BTP login).")
        return u
    if mode == "open":
        import getpass
        return ac.User(id=getpass.getuser(), name=getpass.getuser(), roles=ac.default_roles("open"), source="open")
    raise HTTPException(401, "The studio API needs SAP BTP login (XSUAA) or open local mode.")


def _require(user, perm, project=None):
    try:
        ac.require(user, perm, project, STORE)
    except PermissionError as e:
        raise HTTPException(403, str(e))


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):        # readable errors for the UI
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})


def _body_str(body: Dict, key: str) -> str:
    return str(body.get(key) or "").strip()


# ─────────────────────────────────────────────────────────────────────────────
# Info, dashboard, notifications, environments
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/me")
def me(user: ac.User = Depends(current_user)):
    fn, model = _ai()
    return {"user": {"id": user.id, "name": user.display, "roles": sorted(user.roles), "source": user.source},
            "ai": {"available": bool(fn), "model": model, "error": _AI["error"]},
            "store": {"kind": "hana" if isinstance(STORE, wsp.HanaStore) else "sqlite", "durable": STORE.durable,
                      "note": STORE.durability_note or wsp.STORE_NOTE},
            "permissions": sorted(ac.permissions(user)), "auth_mode": ac.auth_mode()}


SAP_EXAMPLES = {
    "Sales desk": "A custom sales order desk: a home dashboard with open order count, total net value and a chart "
                  "of orders by sales organization; a kanban board of orders by delivery status; a calendar of "
                  "requested delivery dates; an order list with tabs per status that highlights overdue orders "
                  "in red and shows the net value incl. 19% VAT; creating an order defaults the delivery date "
                  "to 14 days from today and rejects dates in the past; release/reject approval for orders in "
                  "approval; an analysis page with net value per month.",
    "Approval inbox": "An approval inbox for managers: tabs by approval status with counts, a split view with the "
                      "list on the left and the document on the right, approve and reject actions only for "
                      "documents waiting for approval, and a highlight for documents older than 30 days.",
    "Inventory control": "An inventory control app: a product browser with list and details side by side, flag "
                         "products below their reorder level as critical, show the stock value (units × unit "
                         "price), a 'Reorder' action that adds 50 to the units on order, and an analysis of stock "
                         "value by category.",
    "Standard List Report": "A standard Fiori Elements List Report and Object Page to browse and maintain the "
                            "main business object, with filters on the key attributes, status colours and the "
                            "item table on the object page. German and English texts.",
    "Overview page": "An overview page with cards: recent documents, a table of open items, and an analytical card "
                     "of the main amount by status.",
}


@app.get("/api/examples")
def examples(user: ac.User = Depends(current_user)):
    return {"new_app": {
        "Purchase request": "A purchase request application with multiple line items, manager approval (approve / "
                            "reject with a reason), totals calculated from the items, and finance reporting by "
                            "department and month.",
        "Leave request": "A leave request app: employees request vacation or sick leave with start and end date, the "
                         "number of days is calculated, the team lead approves or rejects, and a calendar shows absences.",
        "Service tickets": "A service ticket app for our support team: tickets with priority, category and due date, "
                           "a board by status (new, in progress, resolved, closed), comments per ticket and a "
                           "dashboard of open tickets per priority."},
        "sap": SAP_EXAMPLES}


def _visible_projects(user, include_archived=True) -> List[Dict]:
    return STORE.list_projects(user, include_archived=include_archived, see_all=ac.can(user, "project.view_all"))


@app.get("/api/environments")
def environments(user: ac.User = Depends(current_user)):
    return {"envs": ac.environments()}


@app.get("/api/dashboard")
def dashboard(user: ac.User = Depends(current_user)):
    projs = _visible_projects(user)
    names = {p["id"]: p["name"] for p in projs}
    versions, deps = [], []
    for p in projs:
        versions += STORE.list_versions(p["id"])
        deps += STORE.list_deployments(p["id"])
    ok = [d for d in deps if d.get("status") == "succeeded"]
    finished = [d for d in deps if d.get("status") in ("succeeded", "failed")]
    live = {d.get("app_url") for d in ok if d.get("app_url")}
    today = time.time()
    per_day = []
    for i in range(13, -1, -1):
        day = time.strftime("%Y-%m-%d", time.localtime(today - i * 86400))
        per_day.append({"day": day, "count": sum(1 for v in versions
                                                  if time.strftime("%Y-%m-%d", time.localtime(v["created_at"] or 0)) == day)})
    by_mode = {"sap": 0, "standalone": 0, "import": 0}
    for p in projs:
        by_mode[p.get("mode") or "sap"] = by_mode.get(p.get("mode") or "sap", 0) + 1
    pending = [a for a in STORE.list_approvals("pending", user=user) if ac.can(user, "approval.decide")]
    activity = [_audit_row(a, names) for a in STORE.list_audit(None, 200)
                if a.get("project_id") in names or a.get("user") == user.id][:25]
    active = [p for p in projs if not p.get("archived")]
    return {"kpis": {"projects": len(active), "versions": len(versions), "deployments": len(deps),
                     "deploy_success_rate": round(100 * len(ok) / len(finished)) if finished else None,
                     "validated_versions": sum(1 for v in versions if v.get("validation")),
                     "pending_approvals": len(pending), "apps_live": len(live)},
            "activity": activity, "recent_projects": [_project_row(p) for p in active[:6]],
            "running_jobs": sum(1 for j in jobs.active(user.id, API_SESSION) if j.running),
            "by_mode": by_mode, "versions_per_day": per_day}


@app.get("/api/notifications")
def notifications(user: ac.User = Depends(current_user)):
    now, week = time.time(), 7 * 86400
    items = []
    if ac.can(user, "approval.decide"):
        for a in STORE.list_approvals("pending", user=user):
            items.append({"id": "ap-" + a["id"], "kind": "approval_pending", "at": a.get("requested_at"),
                          "title": f"Approval requested: {a.get('project_name') or 'project'} v{a.get('version_number')}",
                          "text": f"{a['requester']} asks to release to {a['env']}." + (f" {a['note']}" if a.get("note") else ""),
                          "link": "/approvals"})
    for a in STORE.list_approvals(None, project_id=None):
        if a.get("requester") == user.id and a.get("status") in ("approved", "rejected") \
                and now - (a.get("decided_at") or 0) < week:
            items.append({"id": "ad-" + a["id"], "kind": "approval_decided", "at": a.get("decided_at"),
                          "title": f"Release {a['status']}: {a.get('project_name') or 'project'} v{a.get('version_number')}",
                          "text": f"{a.get('decided_by')}: {a.get('comment') or 'no comment'}",
                          "link": f"/projects/{a['project_id']}"})
    for p in _visible_projects(user, include_archived=False):
        for d in STORE.list_deployments(p["id"]):
            if d.get("user") == user.id and now - (d.get("at") or 0) < week:
                items.append({"id": "dp-" + d["id"], "kind": "deploy", "at": d.get("at"),
                              "title": f"Deployment {d.get('status')}: {p['name']} → {d.get('env')}",
                              "text": d.get("app_url") or "", "link": f"/projects/{p['id']}"})
    for j in list(jobs._JOBS.values()):
        if j.owner == user.id and not j.running and now - (j.finished or 0) < 3600:
            items.append({"id": "jb-" + j.id, "kind": "job", "at": j.finished,
                          "title": f"{j.label} — {'done' if j.status == 'done' else 'failed'}",
                          "text": j.error or f"finished in {j.seconds:.0f}s", "link": "/studio"})
    items.sort(key=lambda i: -(i.get("at") or 0))
    unread = sum(1 for i in items if i["kind"] == "approval_pending" or now - (i.get("at") or 0) < 86400)
    return {"items": items[:50], "unread": unread}


# ─────────────────────────────────────────────────────────────────────────────
# Projects / versions
# ─────────────────────────────────────────────────────────────────────────────
_PROJECT_KEYS = ("id", "name", "mode", "owner", "app_id", "description", "updated_at", "latest_version", "archived")


def _project_row(p: Dict) -> Dict:
    return {k: p.get(k) for k in _PROJECT_KEYS}


@app.get("/api/projects")
def projects(include_archived: int = 0, user: ac.User = Depends(current_user)):
    rows = []
    for p in _visible_projects(user, bool(include_archived)):
        r = _project_row(p)
        r["can_edit"] = ac.can(user, "project.edit", p, STORE)
        r["can_delete"] = _can_delete(user, p)
        rows.append(r)
    return {"projects": rows}


@app.get("/api/admin/users")
def admin_users(q: str = "", studio_only: int = 0, user: ac.User = Depends(current_user)):
    """Subaccount users with their studio roles (administrators only)."""
    import api_users as au
    _require(user, "admin.users")
    try:
        return {"users": au.list_users(q, bool(studio_only)), "available": True}
    except au.UserAdminError as e:
        raise HTTPException(503 if not au.available() else 502, str(e))


@app.get("/api/admin/idps")
def admin_idps(user: ac.User = Depends(current_user)):
    import api_users as au
    _require(user, "admin.users")
    try:
        return {"idps": au.identity_providers()}
    except au.UserAdminError as e:
        raise HTTPException(503 if not au.available() else 502, str(e))


@app.post("/api/admin/users")
def admin_set_roles(body: Dict = Body(...), user: ac.User = Depends(current_user)):
    """Assign exactly these studio roles to a user (creates the user for the identity provider if new)."""
    import api_users as au
    _require(user, "admin.users")
    try:
        res = au.set_roles(_body_str(body, "user"), [r for r in body.get("roles") or [] if r],
                           _body_str(body, "origin") or "sap.default", acting_admin=user.id)
    except au.UserAdminError as e:
        raise HTTPException(400, str(e))
    STORE.audit(user, None, "user.roles", {"user": res["user"]["user"], "origin": res["user"]["origin"],
                                           "added": res["added"], "removed": res["removed"], "created": res["created"]})
    return res


@app.get("/api/access")
def access(user: ac.User = Depends(current_user)):
    """Role-based access overview: the roles, what each may do, the BTP role collections that grant them,
    and the signed-in user's own roles and permissions."""
    xsapp = "evoke-fiori-studio"
    collections = {"viewer": "Evoke_Studio_Viewer", "developer": "Evoke_Studio_Developer",
                   "reviewer": "Evoke_Studio_Reviewer", "release_manager": "Evoke_Studio_ReleaseManager",
                   "admin": "Evoke_Studio_Admin"}
    return {
        "mode": ac.auth_mode(),
        "me": {"id": user.id, "name": user.display, "roles": sorted(user.roles), "source": user.source,
               "permissions": sorted(ac.permissions(user))},
        "roles": [{"key": r, "label": ac.ROLE_LABELS.get(r, r), "role_collection": collections.get(r, ""),
                   "permissions": sorted(ac.ROLE_PERMISSIONS.get(r, set())),
                   "cross_project": r in ac.CROSS_PROJECT_ROLES} for r in ac.ROLES],
        "permissions": [{"key": k, "text": ac._PERM_TEXT.get(k, k)} for k in ac.PERMISSIONS],
        "user_admin": __import__("api_users").available(),
        "admins_bootstrap": [a.strip() for a in os.environ.get("STUDIO_ADMINS", "").split(",") if a.strip()]
                            if ac.can(user, "admin.users") else [],
        "how_to": ("Roles come from SAP BTP role collections. An administrator assigns them in the BTP cockpit: "
                   "Security → Users → (user) → Assign Role Collection, or Security → Role Collections → "
                   f"Evoke_Studio_… → Edit → Users. The role collections belong to the XSUAA app '{xsapp}'. "
                   "Sign out and in again after a change."),
    }


def _project_or_404(pid: str, user) -> Dict:
    p = STORE.get_project(pid)
    if not p:
        raise HTTPException(404, "Project not found.")
    if not (ac.can(user, "project.view_all") or STORE.project_role(pid, user)):
        raise HTTPException(403, "You are not a member of this project.")
    return p


@app.get("/api/projects/{pid}")
def project(pid: str, user: ac.User = Depends(current_user)):
    p = _project_or_404(pid, user)
    vers = []
    for v in STORE.list_versions(pid):
        val = v.get("validation") or None
        row = {k: v.get(k) for k in ("id", "number", "label", "author", "created_at", "file_count")}
        row["validation"] = {"summary": val.get("summary"), "blocking": val.get("blocking") or []} if val else None
        vers.append(row)
    deps = [{k: d.get(k) for k in ("id", "env", "status", "app_url", "user", "at", "version_number", "version_id")}
            for d in STORE.list_deployments(pid)]
    return {"project": p, "versions": vers, "deployments": deps, "role": STORE.project_role(pid, user),
            "permissions": sorted(ac.permissions(user, p, STORE)), "can_delete": _can_delete(user, p)}


@app.post("/api/projects/{pid}/rename")
def project_rename(pid: str, body: Dict = Body(...), user: ac.User = Depends(current_user)):
    p = _project_or_404(pid, user)
    _require(user, "project.edit", p)
    if not _body_str(body, "name"):
        raise HTTPException(400, "A project needs a name.")
    return STORE.rename_project(pid, _body_str(body, "name"), user)


_APP_ID_RE = __import__("re").compile(r"^[a-zA-Z][\w]*(\.[a-zA-Z][\w]*)+$")


@app.post("/api/projects/{pid}/update")
def project_update(pid: str, body: Dict = Body(...), user: ac.User = Depends(current_user)):
    """Edit name / description / app id (the app id is used by the next generation + deployment)."""
    p = _project_or_404(pid, user)
    _require(user, "project.edit", p)
    name, app_id = _body_str(body, "name"), _body_str(body, "app_id")
    if "name" in body and not name:
        raise HTTPException(400, "A project needs a name.")
    if "app_id" in body and app_id and not _APP_ID_RE.match(app_id):
        raise HTTPException(400, f"App ID '{app_id}' must be a dotted namespace like com.company.app.")
    if name and name != p["name"]:
        STORE.rename_project(pid, name, user)
    if "app_id" in body or "description" in body:
        STORE.update_project(pid, user, app_id=app_id if "app_id" in body else None,
                             description=str(body.get("description") or "") if "description" in body else None)
    return STORE.get_project(pid)


def _can_delete(user, p) -> bool:
    """Owners and administrators may delete a project."""
    return ac.can(user, "admin.users") or (STORE.project_role(p["id"], user) == "owner"
                                           and ac.can(user, "project.edit", p, STORE))


@app.post("/api/projects/{pid}/delete")
def project_delete(pid: str, body: Dict = Body(default={}), user: ac.User = Depends(current_user)):
    """Delete from the workspace. With {"undeploy": ["dev", …]} the deployed app is first removed from
    Cloud Foundry (a job; the project is deleted when it succeeded)."""
    p = _project_or_404(pid, user)
    if not _can_delete(user, p):
        raise HTTPException(403, "Only the project's owner or an administrator can delete it.")
    if (body.get("confirm_name") or "").strip() != p["name"]:
        raise HTTPException(400, "Type the project's name exactly to confirm the deletion.")
    envs = [e for e in (body.get("undeploy") or []) if e]
    if not envs:
        return STORE.delete_project(pid, user)
    for e in envs:
        _require(user, f"deploy.{e}", p)
    return _start(user, "undeploy", f"Removing {p['name']} from Cloud Foundry and the workspace",
                  _w_undeploy, user, pid, envs, bool(body.get("delete_services", True)), True)


@app.post("/api/projects/bulk-delete")
def projects_bulk_delete(body: Dict = Body(...), user: ac.User = Depends(current_user)):
    """Delete several projects from the workspace (deployed apps stay on Cloud Foundry)."""
    done, failed = [], []
    for pid in body.get("ids") or []:
        p = STORE.get_project(pid)
        if not p:
            failed.append({"id": pid, "error": "not found"})
        elif not _can_delete(user, p):
            failed.append({"id": pid, "name": p["name"], "error": "only the owner or an administrator can delete it"})
        else:
            done.append(STORE.delete_project(pid, user))
    return {"deleted": done, "failed": failed}


@app.post("/api/versions/{vid}/delete")
def version_delete(vid: str, user: ac.User = Depends(current_user)):
    v = _version_or_404(vid, user, with_files=False)
    _require(user, "project.edit", STORE.get_project(v["project_id"]))
    try:
        return STORE.delete_version(vid, user)
    except ValueError as e:
        raise HTTPException(400, str(e))


def _w_undeploy(say, user, pid, envs, delete_services, then_delete):
    """Remove the app (MTA with its services, or a plain cf-pushed app) from each environment."""
    import api_deploy as apd
    p = STORE.get_project(pid)
    app_id = p.get("app_id") or ""
    deps = STORE.list_deployments(pid)
    results = []
    for env in envs:
        target_app = next((d.get("target", {}).get("app") for d in deps
                           if d.get("env") == env and (d.get("target") or {}).get("app")), None) or app_id
        if not target_app:
            raise RuntimeError("The project has no app ID — nothing to remove on Cloud Foundry.")
        d = apd.deployer(user, env, say)
        if not d.check_cf_cli():
            raise RuntimeError("The Cloud Foundry CLI is not available on the studio host.")
        for line in d.target():
            say(line)
        if not d.is_logged_in():
            raise RuntimeError("The studio has no Cloud Foundry session. An administrator can configure the studio's deployment user (CF_TECH_USER / CF_TECH_PASSWORD) so nobody has to sign in to Cloud Foundry separately.")
        say(f"Removing {target_app} from {env}…")
        out = []
        cmd = ["cf", "undeploy", target_app, "-f"] + (["--delete-services", "--delete-service-keys"] if delete_services else [])
        for line in d._run(cmd):
            out.append(line)
            say(line)
        text = "\n".join(out)
        if "not found" in text.lower() or "does not exist" in text.lower() or "FAILED" in text:
            say(f"No MTA {target_app} — removing the app itself…")
            out = []
            for line in d._run(["cf", "delete", target_app, "-f", "-r"]):
                out.append(line)
                say(line)
            text = "\n".join(out)
        ok = "FAILED" not in text
        STORE.record_deployment(pid, None, env, apd.target(env) | {"app": target_app}, user,
                                "undeployed" if ok else "undeploy-failed", log_tail=text[-4000:])
        results.append({"env": env, "app": target_app, "ok": ok})
        if not ok:
            raise RuntimeError(f"Removing {target_app} from {env} failed — see the log.")
    deleted = STORE.delete_project(pid, user) if then_delete else None
    return {"undeployed": results, "deleted": deleted}


@app.post("/api/jobs/undeploy")
def job_undeploy(body: Dict = Body(...), user: ac.User = Depends(current_user)):
    """Remove a project's deployed app from Cloud Foundry (the project stays in the workspace)."""
    p = _project_or_404(body.get("project_id") or "", user)
    env = body.get("env") or "dev"
    _require(user, f"deploy.{env}", p)
    return _start(user, "undeploy", f"Removing {p['name']} from {env}", _w_undeploy, user, p["id"], [env],
                  bool(body.get("delete_services", True)), False)


@app.post("/api/projects/{pid}/archive")
def project_archive(pid: str, body: Dict = Body(...), user: ac.User = Depends(current_user)):
    p = _project_or_404(pid, user)
    _require(user, "project.edit", p)
    return STORE.archive_project(pid, user, bool(body.get("archived", True)))


@app.post("/api/projects/{pid}/duplicate")
def project_duplicate(pid: str, body: Dict = Body(...), user: ac.User = Depends(current_user)):
    p = _project_or_404(pid, user)
    _require(user, "project.create")
    return STORE.duplicate_project(pid, _body_str(body, "name") or p["name"] + " (copy)", user)


@app.post("/api/projects/{pid}/members")
def project_member(pid: str, body: Dict = Body(...), user: ac.User = Depends(current_user)):
    p = _project_or_404(pid, user)
    _require(user, "project.edit", p)
    who, role = _body_str(body, "user").lower(), _body_str(body, "role") or "developer"
    if not who:
        raise HTTPException(400, "Enter the member's user (e-mail).")
    if body.get("remove"):
        return STORE.remove_member(pid, who, by=user)
    try:
        return STORE.add_member(pid, who, role, by=user)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/projects/{pid}/export")
def project_export(pid: str, user: ac.User = Depends(current_user)):
    p = _project_or_404(pid, user)
    name = "".join(c if c.isalnum() or c in "-_" else "_" for c in p["name"]) + "-workspace.zip"
    return Response(STORE.export_project(pid, user), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/projects/import")
async def project_import(file: UploadFile = File(...), user: ac.User = Depends(current_user)):
    """A studio workspace export, or any UI5 / Fiori project folder as ZIP."""
    import api_import
    _require(user, "project.create")
    data = await file.read()
    try:
        r = api_import.import_zip(STORE, user, data, file.filename or "project.zip")
    except Exception as e:  # noqa: BLE001 — bad ZIP, no manifest.json …
        raise HTTPException(400, f"Import failed: {e}")
    return dict(STORE.get_project(r["project_id"]) or {}, imported=r)


@app.get("/api/cf/apps")
def cf_apps(user: ac.User = Depends(current_user)):
    """Apps deployed in the development space (to import one into the workspace)."""
    import api_deploy
    import app_import
    _require(user, "project.create")
    try:
        d = api_deploy.deployer(user, "dev")
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    if not d.check_cf_cli():
        raise HTTPException(503, "The Cloud Foundry CLI is not available on the studio host.")
    if not d.is_logged_in():
        raise HTTPException(409, "The studio has no Cloud Foundry session. An administrator can configure the studio's "
                                 "deployment user (CF_TECH_USER / CF_TECH_PASSWORD).")
    try:
        apps = app_import.list_cf_apps(cf_home=d.cf_home)
    except app_import.ImportError_ as e:
        raise HTTPException(502, str(e))
    t = api_deploy.target("dev")
    known = {p.get("app_id") for p in STORE.list_projects(user, see_all=True)}
    return {"apps": [dict(a, in_workspace=a["name"] in known) for a in apps], "org": t["org"], "space": t["space"]}


@app.post("/api/jobs/import-cf")
def job_import_cf(body: Dict = Body(...), user: ac.User = Depends(current_user)):
    import api_deploy
    import api_import
    _require(user, "project.create")
    name = _body_str(body, "app")
    if not name:
        raise HTTPException(400, "Choose an app.")

    def run(say, u, app_name):
        d = api_deploy.deployer(u, "dev", say)
        return api_import.import_cf(STORE, u, app_name, d.cf_home, say)
    return _start(user, "import", f"Importing {name} from Cloud Foundry", run, user, name)


def _version_or_404(vid: str, user, with_files=True) -> Dict:
    v = STORE.get_version(vid, with_files=with_files)
    if not v:
        raise HTTPException(404, "Version not found.")
    _project_or_404(v["project_id"], user)
    return v


def _source_from_snapshot(s: Dict) -> Dict:
    """v2 `source` from a stored snapshot (studio format: app_mode/standalone/md_* keys)."""
    sa = s.get("standalone") or {}
    if sa or s.get("app_mode") == "standalone":
        return {"mode": "standalone", "domain": sa.get("domain"), "db": sa.get("db", "sqlite")}
    services = []
    if s.get("md_edmx"):
        url = s.get("md_service_url_val") or ""
        services.append({"edmx": s["md_edmx"], "service_url": url, "title": url.rstrip("/").split("/")[-1]})
    for e in s.get("md_extra") or []:
        services.append({"edmx": e.get("edmx", ""), "service_url": e.get("service_url", ""),
                         "title": e.get("title") or e.get("service_url", "")})
    return {"mode": "sap", "services": services,
            "destination": {"use_existing": bool(s.get("md_use_existing_dest")), "name": s.get("md_dest_name_val") or "",
                            "on_premise": bool(s.get("md_on_premise")), "auth_type": s.get("md_auth") or "NoAuthentication"}}


@app.get("/api/versions/{vid}")
def version(vid: str, user: ac.User = Depends(current_user)):
    v = _version_or_404(vid, user)
    s = v.get("snapshot") or {}
    design = s.get("bp_design") or {}
    snap = {"requirement": s.get("user_prompt") or s.get("requirement") or "", "source": _source_from_snapshot(s),
            "blueprint": design.get("blueprint"),
            "design": {"spec": design.get("spec"), "coverage": design.get("coverage") or []},
            "app_id": s.get("app_id_v") or s.get("app_id") or ""}
    meta = {k: v.get(k) for k in ("id", "number", "label", "author", "created_at", "file_count", "project_id")}
    val = v.get("validation") or None
    meta["validation"] = {"summary": val.get("summary"), "blocking": val.get("blocking") or []} if val else None
    return {"version": meta, "snapshot": snap, "files": sorted(v["files"]), "validation_report": val}


@app.get("/api/versions/{vid}/file")
def version_file(vid: str, path: str, user: ac.User = Depends(current_user)):
    v = _version_or_404(vid, user)
    if path not in v["files"]:
        raise HTTPException(404, "File not found in this version.")
    return PlainTextResponse(v["files"][path])


@app.get("/api/versions/{vid}/zip")
def version_zip(vid: str, user: ac.User = Depends(current_user)):
    from deployer import create_zip_archive
    v = _version_or_404(vid, user)
    p = STORE.get_project(v["project_id"]) or {}
    name = f"{p.get('app_id') or 'app'}-v{v['number']}.zip"
    return Response(create_zip_archive(v["files"], app_name="fiori_app"), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/compare")
def compare(a: str, b: str, user: ac.User = Depends(current_user)):
    _version_or_404(a, user, with_files=False)
    _version_or_404(b, user, with_files=False)
    return STORE.compare_versions(a, b)


# ─────────────────────────────────────────────────────────────────────────────
# Approvals / audit
# ─────────────────────────────────────────────────────────────────────────────
def _approval_row(a: Dict, user) -> Dict:
    v = STORE.get_version(a["version_id"], with_files=False) or {}
    val = v.get("validation") or None
    row = {k: a.get(k) for k in ("id", "project_id", "project_name", "version_id", "version_number", "env",
                                 "requester", "note", "status", "requested_at", "decided_by", "decided_at", "comment")}
    row["can_decide"] = (a.get("status") == "pending" and ac.can(user, "approval.decide")
                         and a.get("requester") != user.id and (a.get("version_author") or v.get("author")) != user.id)
    row["validation"] = {"summary": val.get("summary"), "blocking": val.get("blocking") or []} if val else None
    return row


@app.get("/api/approvals")
def approvals(status: str = "pending", user: ac.User = Depends(current_user)):
    st_ = None if status == "all" else status
    visible = {p["id"] for p in _visible_projects(user)}
    rows = [a for a in STORE.list_approvals(st_) if a["project_id"] in visible or a.get("requester") == user.id
            or ac.can(user, "approval.decide")]
    return {"approvals": [_approval_row(a, user) for a in rows]}


@app.post("/api/approvals")
def approval_request(body: Dict = Body(...), user: ac.User = Depends(current_user)):
    p = _project_or_404(_body_str(body, "project_id"), user)
    _require(user, "approval.request", p)
    v = _version_or_404(_body_str(body, "version_id"), user, with_files=False)
    if v["project_id"] != p["id"]:
        raise HTTPException(400, "The version does not belong to this project.")
    env = _body_str(body, "env") or "prod"
    if env not in ac.environments():
        raise HTTPException(400, f"Unknown environment '{env}'.")
    a = STORE.request_approval(p["id"], v["id"], env, user, _body_str(body, "note"))
    return _approval_row(STORE.get_approval(a["id"]), user)


@app.post("/api/approvals/{aid}/decide")
def approval_decide(aid: str, body: Dict = Body(...), user: ac.User = Depends(current_user)):
    a = STORE.get_approval(aid)
    if not a:
        raise HTTPException(404, "Approval request not found.")
    _require(user, "approval.decide")
    try:
        STORE.decide_approval(aid, user, bool(body.get("approve")), _body_str(body, "comment"))
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _approval_row(STORE.get_approval(aid), user)


def _audit_row(a: Dict, names: Dict[str, str]) -> Dict:
    return {"at": a.get("at"), "user": a.get("user"), "action": a.get("action"), "project_id": a.get("project_id"),
            "project_name": names.get(a.get("project_id") or "", ""), "detail": a.get("detail") or {}}


@app.get("/api/audit")
def audit(project_id: str = "", limit: int = 200, user: ac.User = Depends(current_user)):
    projs = _visible_projects(user)
    names = {p["id"]: p["name"] for p in projs}
    if project_id:
        _project_or_404(project_id, user)
        rows = STORE.list_audit(project_id, limit)
    else:
        rows = STORE.list_audit(None, max(limit * 3, 300))
        if not ac.can(user, "project.view_all"):
            rows = [a for a in rows if a.get("project_id") in names or a.get("user") == user.id]
    return {"events": [_audit_row(a, names) for a in rows[:limit]]}


# ─────────────────────────────────────────────────────────────────────────────
# Jobs
# ─────────────────────────────────────────────────────────────────────────────
API_SESSION = "api"


def _job_dict(j, with_result=True) -> Dict:
    d = {"id": j.id, "kind": j.kind, "label": j.label, "status": j.status, "seconds": round(j.seconds, 1),
         "messages": j.messages[-40:], "error": j.error}
    if with_result:
        d["result"] = j.result if j.status == "done" else None
    return d


@app.get("/api/jobs")
def job_list(user: ac.User = Depends(current_user)):
    return {"jobs": [_job_dict(j, False) for j in jobs.active(user.id, API_SESSION) if j.running]}


@app.get("/api/jobs/{job_id}")
def job_get(job_id: str, user: ac.User = Depends(current_user)):
    j = jobs.get(job_id)
    if not j or j.owner != user.id:
        raise HTTPException(404, "Job not found (the studio may have restarted).")
    return _job_dict(j)


def _start(user, kind, label, fn, *args, **kwargs) -> Dict:
    jobs.heartbeat(API_SESSION)
    j = jobs.start(kind, user.id, API_SESSION, label, fn, *args, **kwargs)
    return {"job_id": j.id}


# ─────────────────────────────────────────────────────────────────────────────
# Standalone data model
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/api/jobs/questions")
def job_questions(body: Dict = Body(...), user: ac.User = Depends(current_user)):
    import standalone as sa
    req = _body_str(body, "requirement")
    if not req:
        raise HTTPException(400, "Describe the application first.")
    fn, _ = _ai()
    return _start(user, "questions", "Reviewing the requirement",
                  lambda say, r, f: {"questions": sa.clarifying_questions(r, f)}, req, fn)


def _domain_result(res: Dict) -> Dict:
    import standalone as sa
    return {"domain": res["domain"], "notes": res.get("notes") or [], "source": res.get("source", ""),
            "summary": sa.summary(res["domain"])}


@app.post("/api/jobs/domain")
def job_domain(body: Dict = Body(...), user: ac.User = Depends(current_user)):
    import standalone as sa
    req = _body_str(body, "requirement")
    if not req:
        raise HTTPException(400, "Describe the application first.")
    fn, _ = _ai()
    return _start(user, "domain", "Designing the data model and process",
                  lambda say, r, f, a: _domain_result(sa.design_domain(r, f, a, progress=say)),
                  req, fn, body.get("answers") or {})


@app.post("/api/domain/validate")
def domain_validate(body: Dict = Body(...), user: ac.User = Depends(current_user)):
    import standalone as sa
    dom, notes = sa.validate_domain(body.get("domain") or {})
    return {"domain": dom, "notes": notes, "summary": sa.summary(dom)}


# ─────────────────────────────────────────────────────────────────────────────
# SAP services
# ─────────────────────────────────────────────────────────────────────────────
def _sap(fn, *args):
    import api_sap
    try:
        return fn(*args)
    except api_sap.SapError as e:
        raise HTTPException(400, str(e))


@app.get("/api/sap/runtime")
def sap_runtime(user: ac.User = Depends(current_user)):
    import api_sap
    return api_sap.runtime()


@app.get("/api/sap/destinations")
def sap_destinations(user: ac.User = Depends(current_user)):
    import api_sap
    return _sap(api_sap.destinations)


@app.post("/api/sap/services")
def sap_services(body: Dict = Body(...), user: ac.User = Depends(current_user)):
    import api_sap
    return _sap(api_sap.services, _body_str(body, "destination"))


@app.post("/api/sap/metadata")
def sap_metadata(body: Dict = Body(...), user: ac.User = Depends(current_user)):
    import api_sap
    return _sap(api_sap.metadata, body)


@app.post("/api/sap/check-services")
def sap_check(body: Dict = Body(...), user: ac.User = Depends(current_user)):
    import api_sap
    return api_sap.check_services(body.get("services") or [])


# ─────────────────────────────────────────────────────────────────────────────
# Sources (what an app is built on) — resolved per call, the server keeps no draft state
# ─────────────────────────────────────────────────────────────────────────────
def _source(body: Dict) -> Dict:
    src = body.get("source")
    if not src and body.get("domain"):                     # v1 clients
        src = {"mode": "standalone", "domain": body["domain"], "db": body.get("db") or "sqlite"}
    if not isinstance(src, dict) or src.get("mode") not in ("standalone", "sap"):
        raise HTTPException(400, "Missing source: {mode: 'standalone', domain} or {mode: 'sap', services}.")
    return src


def _resolve(src: Dict) -> Dict:
    """{mode, model, edmx, service_url, extra, domain?, db?, destination?} for a source."""
    import app_builder as ab
    if src["mode"] == "standalone":
        import standalone as sa
        if not src.get("domain"):
            raise HTTPException(400, "Design the data model first.")
        dom, _ = sa.validate_domain(src["domain"])
        edmx = sa.to_edmx(dom)
        return {"mode": "standalone", "domain": dom, "db": src.get("db") or "sqlite", "edmx": edmx,
                "service_url": sa.service_path(dom), "extra": [], "model": ab.load_model(edmx)}
    import api_sap
    services_ = [s for s in src.get("services") or [] if (s.get("edmx") or "").strip()]
    if not services_:
        raise HTTPException(400, "Load an OData service first.")
    if not (services_[0].get("service_url") or "").strip():
        raise HTTPException(400, "The main service needs the service path the deployed app will call.")
    model = _sap(api_sap.model_for, services_)
    extra = [{"edmx": s["edmx"], "service_url": s["service_url"],
              "title": s.get("title") or s["service_url"].rstrip("/").split("/")[-1]} for s in services_[1:]]
    return {"mode": "sap", "edmx": services_[0]["edmx"], "service_url": services_[0]["service_url"].strip(),
            "extra": extra, "model": model, "destination": src.get("destination") or {}}


def _pages(bp: Dict) -> List[Dict]:
    if bp.get("pages"):
        return [{"id": p.get("id", ""), "type": p.get("type", ""), "title": p.get("title", ""),
                 "entity_set": p.get("entity_set") or (p.get("sub") or {}).get("entity_set", "")}
                for p in bp["pages"]]
    es = (bp.get("main") or {}).get("entity_set", "")
    title = (bp.get("app") or {}).get("title", "")
    if bp.get("floorplan") == "OverviewPage":
        return [{"id": "ovp", "type": "dashboard", "title": title, "entity_set": es},
                {"id": "list", "type": "list", "title": title, "entity_set": es}]
    return [{"id": "list", "type": "list", "title": title, "entity_set": es},
            {"id": "object", "type": "detail", "title": (bp.get("object_page") or {}).get("title") or es, "entity_set": es}]


def _design_result(res: Dict, service_url: str) -> Dict:
    import ai_designer as ad
    bp = res["blueprint"]
    return {"blueprint": bp, "notes": res.get("notes") or [], "source": res.get("source", ""),
            "spec": res.get("spec"), "coverage": res.get("coverage") or [],
            "coverage_score": ad.coverage_score(res.get("coverage") or []), "steps": res.get("steps") or [],
            "service_url": service_url, "structure": ad.summarize_blueprint(bp).split("\nBUILT INTO")[0],
            "pages": _pages(bp)}


def _w_design(say, requirement, r, fn, floorplan, style):
    import app_builder as ab
    import ai_designer as ad
    opts = {"analyze_first": True, "repair": True, "check_coverage": True,
            "cache_tag": f"{_AI['model']}|0.15"} if fn else {}
    if r["mode"] == "standalone":
        res = ab.design_standalone(requirement, r["domain"], fn, floorplan, style, progress=say, **opts)
    elif fn:
        res = ad.design(requirement, r["model"], fn, floorplan, style, progress=say, **opts)
    else:
        say("No AI model — using the rule-based designer.")
        res = ab.design(requirement, r["edmx"], None, floorplan, style, service_url=r["service_url"],
                        extra_services=r["extra"] or None)
    return _design_result(res, r["service_url"])


@app.get("/api/floorplans")
def floorplans(user: ac.User = Depends(current_user)):
    import blueprint as bpm
    return {"floorplans": {k: list(v) for k, v in bpm.FLOORPLANS.items()}}


@app.post("/api/jobs/design")
def job_design(body: Dict = Body(...), user: ac.User = Depends(current_user)):
    r = _resolve(_source(body))
    req = _body_str(body, "requirement")
    if not req:
        raise HTTPException(400, "Describe the application first.")
    fn, _ = _ai()
    return _start(user, "design", "Designing the screens", _w_design, req, r, fn, body.get("floorplan") or "",
                  body.get("style"))


def _w_refine(say, instruction, requirement, bp, r, fn, spec):
    import ai_designer as ad
    say(f"Applying: {instruction}")
    bp2, notes = ad.refine(bp, instruction, requirement, r["model"], fn, spec)
    cov = []
    if spec:
        say("Re-checking requirement coverage…")
        cov = ad.assess_coverage(spec, bp2, fn)
    return _design_result({"blueprint": bp2, "notes": notes, "source": "ai", "spec": spec, "coverage": cov,
                           "steps": []}, r["service_url"])


@app.post("/api/jobs/refine")
def job_refine(body: Dict = Body(...), user: ac.User = Depends(current_user)):
    fn, _ = _ai()
    if not fn:
        raise HTTPException(400, "Refining the design needs an AI model (AI Core).")
    instr = _body_str(body, "instruction")
    if not instr or not body.get("blueprint"):
        raise HTTPException(400, "Describe the change and design the app first.")
    r = _resolve(_source(body))
    return _start(user, "refine", "Refining the design", _w_refine, instr, _body_str(body, "requirement"),
                  body["blueprint"], r, fn, body.get("spec"))


@app.post("/api/preview", response_class=HTMLResponse)
def preview(body: Dict = Body(...), user: ac.User = Depends(current_user)):
    import interactive_preview as ip
    bp = body.get("blueprint")
    if not bp:
        raise HTTPException(400, "Design the app first.")
    r = _resolve(_source(body))
    rows, workflow = None, None
    if r["mode"] == "standalone":
        import standalone as sa
        rows = {}
        for path, text in sa.mock_data(r["domain"]).items():
            try:
                rows[path.rsplit("/", 1)[-1].rsplit(".", 1)[0]] = json.loads(text)
            except ValueError:
                pass
        wf = r["domain"].get("workflow")
        if wf:
            workflow = {t["action"]: {"field": wf["status_field"], "from": list(t["from"]), "to": t["to"]}
                        for t in wf["transitions"]}
    return HTMLResponse(ip.preview_html(bp, r["model"], sample_rows=rows, workflow=workflow, height=620))


# ─────────────────────────────────────────────────────────────────────────────
# Generate / validate
# ─────────────────────────────────────────────────────────────────────────────
def _w_generate(say, user, body, r, fn):
    import app_builder as ab
    from odata_config import AUTH_NONE
    design = body.get("design") or {}
    requirement = body.get("requirement") or ""
    bp = body["blueprint"]
    theme = body.get("theme") or "sap_horizon"
    if r["mode"] == "standalone":
        import standalone as sa
        dom, db = r["domain"], r["db"]
        app_id = _body_str(body, "app_id") or ("com.evoke." + "".join(c for c in dom["name"].lower() if c.isalnum())[:30])
        say("Rendering the UI5 app and the CAP backend (data model, service, workflow, sample data)…")
        res = ab.build(edmx="", requirement=requirement, app_id=app_id, service_url="", ai_fn=fn, blueprint=bp,
                       theme=theme, design_spec=design.get("spec"), coverage=design.get("coverage"),
                       standalone=dom, standalone_db=db)
        snap_src = {"app_mode": "standalone", "md_edmx": sa.to_edmx(dom), "md_service_url_val": sa.service_path(dom),
                    "standalone": {"domain": dom, "db": db}}
        deploy_meta = {"app_id": app_id, "services": [], "standalone": True, "db": db}
        mode = "standalone"
    else:
        dest = r["destination"]
        use_existing = bool(dest.get("use_existing"))
        app_id = _body_str(body, "app_id") or "com.mycompany.app"
        say("Rendering views, controllers, custom logic and configuration…")
        langs = (bp.get("app") or {}).get("languages") or []
        if fn and len(langs) > 1:
            say(f"Translating texts into {', '.join(langs[1:])}…")
        res = ab.build(edmx=r["edmx"], requirement=requirement, app_id=app_id, service_url=r["service_url"], ai_fn=fn,
                       blueprint=bp, auth_type=(dest.get("auth_type") or AUTH_NONE) if not use_existing else AUTH_NONE,
                       use_existing_destination=use_existing, destination_name=(dest.get("name") or "").strip(),
                       on_premise=bool(dest.get("on_premise")), theme=theme, design_spec=design.get("spec"),
                       coverage=design.get("coverage"), extra_services=r["extra"] or None)
        snap_src = {"app_mode": "sap", "md_edmx": r["edmx"], "md_service_url_val": r["service_url"],
                    "md_extra": [dict(e, sets=0) for e in r["extra"]], "md_use_existing_dest": use_existing,
                    "md_dest_name_val": dest.get("name") or "", "md_on_premise": bool(dest.get("on_premise")),
                    "md_auth": dest.get("auth_type") or AUTH_NONE}
        deploy_meta = {"app_id": res.spec.app_id,
                       "services": [{"id": s.id, "url": s.url, "auth_type": s.auth_type, "dest_name": s.dest_name(),
                                     "needs_auth": s.needs_auth(), "is_real": s.is_real(),
                                     "use_existing_destination": s.use_existing_destination,
                                     "on_premise": s.on_premise} for s in res.spec.service_list()]}
        mode = "sap"
    say(f"Generated {len(res.files)} files — quality gate: {res.quality.get('errors', 0)} error(s). Saving the version…")
    pid = body.get("project_id")
    proj = STORE.get_project(pid) if pid else None
    if not proj:
        ac.require(user, "project.create")
        proj = STORE.create_project(res.blueprint["app"]["title"], user, mode, app_id=app_id,
                                    description=requirement[:300])
    ac.require(user, "version.save", proj, STORE)
    # Snapshot in the studio's format, so the classic (Streamlit) studio can open these versions too
    snapshot = dict(snap_src, user_prompt=requirement, app_id_v=app_id, theme_v=theme,
                    bp_design={"blueprint": res.blueprint, "notes": res.notes, "source": "web-studio",
                               "spec": design.get("spec"), "coverage": design.get("coverage") or [], "steps": []},
                    build_result={"quality": res.quality, "fixes": res.fixes, "notes": res.notes,
                                  "floorplan": res.blueprint["floorplan"], "odata": res.model["odata_version"],
                                  "title": res.blueprint["app"]["title"]},
                    deploy_meta=deploy_meta)
    v = STORE.save_version(proj["id"], user, snapshot, res.files, label="Generated (web studio)", quality=res.quality)
    STORE.audit(user, proj["id"], "app.generate", {"version": v["number"], "files": len(res.files), "client": "web"})
    return {"project_id": proj["id"], "version_id": v["id"], "version_number": v["number"],
            "file_count": len(res.files), "files": sorted(res.files), "notes": res.notes,
            "quality": {"errors": res.quality.get("errors", 0), "warnings": res.quality.get("warnings", 0),
                        "findings": [{k: f.get(k) for k in ("severity", "message", "file")}
                                     for f in res.quality.get("findings") or []][:100]}}


@app.post("/api/jobs/generate")
def job_generate(body: Dict = Body(...), user: ac.User = Depends(current_user)):
    if not body.get("blueprint"):
        raise HTTPException(400, "Design the screens first.")
    r = _resolve(_source(body))
    if body.get("project_id"):
        p = _project_or_404(body["project_id"], user)
        _require(user, "version.save", p)
    else:
        _require(user, "project.create")
    app_id = _body_str(body, "app_id")
    import re
    if app_id and not re.match(r"^[a-zA-Z]\w*(\.[a-zA-Z]\w*)+$", app_id):
        raise HTTPException(400, f"App ID '{app_id}' must be a dotted namespace like com.company.app.")
    fn, _ = _ai()
    return _start(user, "generate", "Generating the application", _w_generate, user, body, r, fn)


def _w_validate(say, user, vid, level):
    import app_builder as ab
    import build_validation as bv
    v = STORE.get_version(vid, with_files=True)
    s = v.get("snapshot") or {}
    model = None
    if s.get("md_edmx"):
        extra = [{"edmx": e["edmx"], "service_url": e["service_url"], "title": e.get("title", "")}
                 for e in s.get("md_extra") or [] if e.get("edmx")]
        model = ab.load_app_model(s["md_edmx"], s.get("md_service_url_val", ""), extra or None)
    rep = bv.run(v["files"], model=model, coverage=(s.get("bp_design") or {}).get("coverage"), level=level,
                 progress=say)
    STORE.set_validation(vid, rep, user)
    return rep


@app.post("/api/jobs/validate")
def job_validate(body: Dict = Body(...), user: ac.User = Depends(current_user)):
    vid = _body_str(body, "version_id")
    _version_or_404(vid, user, with_files=False)
    level = body.get("level") if body.get("level") in ("standard", "deep") else "standard"
    return _start(user, "validate", f"Validating version ({level})", _w_validate, user, vid, level)


# ─────────────────────────────────────────────────────────────────────────────
# Cloud Foundry: session, release gate, deploy, logs
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/cf/session")
def cf_session(user: ac.User = Depends(current_user)):
    import api_deploy
    return api_deploy.session(user)


@app.post("/api/cf/login")
def cf_login(body: Dict = Body(...), user: ac.User = Depends(current_user)):
    import api_deploy
    if not (body.get("passcode") or (body.get("user") and body.get("password"))):
        raise HTTPException(400, "Enter the SSO passcode, or user and password.")
    try:
        return api_deploy.login(user, body)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/cf/logout")
def cf_logout(user: ac.User = Depends(current_user)):
    import api_deploy
    return api_deploy.logout(user)


@app.get("/api/deploy/check")
def deploy_check(version_id: str, env: str = "dev", user: ac.User = Depends(current_user)):
    import api_deploy
    v = _version_or_404(version_id, user, with_files=False)
    full = STORE.get_version(version_id, with_files=True)
    if env not in ac.environments():
        raise HTTPException(400, f"Unknown environment '{env}'.")
    ok, reasons = ac.check_deploy(user, env, v["project_id"], version_id, STORE, v.get("validation"))
    meta = api_deploy.deploy_meta(full.get("snapshot"))
    if not meta.get("app_id"):
        ok, reasons = False, reasons + ["This version has no deployment settings — regenerate it."]
    blockers = api_deploy.quality_gate(full["files"]) if full.get("files") else []
    if blockers:
        ok = False
        reasons = reasons + [f"Quality gate: {b}" for b in blockers[:10]]
    if "mta.yaml" not in (full.get("files") or {}):
        ok, reasons = False, reasons + ["The project has no mta.yaml — regenerate it."]
    appr = STORE.approval_for(v["project_id"], version_id, env)
    n = api_deploy.needs(meta)
    return {"ok": ok, "reasons": reasons, "approval": _approval_row(appr, user) if appr else None,
            "needs": {"destinations": n["destinations"]}, "method_choices": n["method_choices"],
            "target": api_deploy.target(env), "env": ac.environments()[env], "app_id": meta.get("app_id", "")}


def _w_deploy(say, user, vid, env, creds, method):
    import api_deploy
    v = STORE.get_version(vid, with_files=True)
    meta = api_deploy.deploy_meta(v.get("snapshot"))
    res = api_deploy.run(say, user, v["files"], meta, env, creds, method)
    if res["status"] != "skipped":
        STORE.record_deployment(v["project_id"], vid, env, dict(api_deploy.target(env), app=meta.get("app_id")),
                                user, res["status"], app_url=res["url"] or "", log_tail=res["log"])
    return res


@app.post("/api/jobs/deploy")
def job_deploy(body: Dict = Body(...), user: ac.User = Depends(current_user)):
    import api_deploy
    vid, env = _body_str(body, "version_id"), _body_str(body, "env") or "dev"
    chk = deploy_check(vid, env, user)
    if not chk["ok"]:
        raise HTTPException(409, "Deployment blocked: " + " · ".join(chk["reasons"]))
    full = STORE.get_version(vid, with_files=True)
    meta = api_deploy.deploy_meta(full.get("snapshot"))
    creds = body.get("dest_creds") or {}
    missing = api_deploy.missing_creds(meta, creds)
    if missing:
        raise HTTPException(400, "Fill in every destination field: " + ", ".join(missing))
    method = body.get("method") if body.get("method") in chk["method_choices"] else chk["method_choices"][0]
    sess = api_deploy.session(user)
    if not sess["signed_in"]:
        raise HTTPException(409, sess.get("error") or "The studio has no Cloud Foundry session. An administrator can configure the studio's deployment user (CF_TECH_USER / CF_TECH_PASSWORD) so nobody has to sign in to Cloud Foundry separately.")
    return _start(user, "deploy", f"Deploying {meta.get('app_id')} to {env}", _w_deploy, user, vid, env, creds, method)


@app.get("/api/cf/logs")
def cf_logs(app_name: str = Query("", alias="app"), user: ac.User = Depends(current_user)):
    import api_deploy
    ok, text = api_deploy.deployer(user).get_recent_logs(app_name.strip())
    return {"ok": ok, "text": text}


# ─────────────────────────────────────────────────────────────────────────────
# UI (local development: served here; on BTP the approuter serves it)
# ─────────────────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_UI = os.path.join(_HERE, "studio-web", "dist")             # React + UI5 Web Components build
if os.path.isdir(_UI) and os.environ.get("STUDIO_SERVE_UI", "1") == "1":
    app.mount("/", StaticFiles(directory=_UI, html=True), name="ui")
