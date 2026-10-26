"""
api_adopt.py — REST routes for adopting existing projects (contract: docs/adopt-api.md).

Bring a project (ZIP / Git / Cloud Foundry) → analyse it (adopt_scan + AI understanding) → the user
fills the gaps (brief) → an `adopted` project with version 1. Then: the robot (adopt_robot) changes it
through missions, the runner (adopt_client → runner_service) validates, previews and builds it, and
deployments follow the studio's environment / approval gates.

Registered from studio_api.py: `api_adopt.register(app, globals())`.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import Body, Depends, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse

import adopt_client
import adopt_files
import adopt_robot
import adopt_scan

UPLOAD_TTL = 3600
ANALYSIS_TTL = 24 * 3600
MAX_UPLOAD = 200 * 1024 * 1024

_UPLOADS: Dict[str, Dict] = {}
_ANALYSES: Dict[str, Dict] = {}
_PREVIEWS: Dict[str, Dict] = {}          # preview_id -> {owner, project_id, started}
_CONVERSE: Dict[str, Any] = {}
_LOCK = threading.Lock()
_HEALTH: Dict[str, Any] = {"at": 0, "value": None}


def _gc() -> None:
    now = time.time()
    for d, ttl in ((_UPLOADS, UPLOAD_TTL), (_ANALYSES, ANALYSIS_TTL)):
        for k in [k for k, v in d.items() if now - v["at"] > ttl]:
            d.pop(k, None)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", (name or "app").lower()).strip("-")[:60] or "app"


def _public_analysis(a: Dict, aid: str = "") -> Dict:
    out = {k: v for k, v in a.items() if k != "_ctx"}
    if aid:
        out["id"] = aid
    return out


def _brief_from_answers(answers: Dict, analysis: Dict) -> Dict:
    b: Dict[str, Any] = {"destinations": {}}
    for k, v in (answers or {}).items():
        if k.startswith("dest:"):
            b["destinations"][k[5:]] = v
        else:
            b[k] = v
    if not isinstance(b.get("protected"), list):
        b["protected"] = [x.strip() for x in str(b.get("protected") or "").splitlines() if x.strip()]
    return b


def _answers_from_brief(brief: Dict) -> Dict:
    a = {k: v for k, v in (brief or {}).items() if k != "destinations"}
    for k, v in ((brief or {}).get("destinations") or {}).items():
        a["dest:" + k] = v
    return a


def register(app, h: Dict[str, Any]) -> None:
    STORE = h["STORE"]
    current_user = h["current_user"]
    _require = h["_require"]
    _start = h["_start"]
    _ai = h["_ai"]
    _project_or_404 = h["_project_or_404"]
    _version_or_404 = h["_version_or_404"]
    _body_str = h["_body_str"]
    ac = h["ac"]
    User = ac.User
    adopt_robot.mark_interrupted()

    # ── helpers ────────────────────────────────────────────────────────────
    def converse():
        fn, model = _ai()
        if not fn or not model:
            raise HTTPException(503, "The AI assistant needs SAP AI Core (Claude). AI is not available: "
                                     + (h["_AI"].get("error") or "not configured"))
        if model not in _CONVERSE:
            import ai_client
            _CONVERSE[model] = ai_client.make_converse_fn(model)
        return _CONVERSE[model]

    def adopted_version(pid: str, user, vid: str = "") -> Dict:
        p = _project_or_404(pid, user)
        v = STORE.get_version(vid, with_files=True) if vid else STORE.latest_version(pid, with_files=True)
        if not v or v["project_id"] != p["id"]:
            raise HTTPException(404, "Version not found.")
        if (v.get("snapshot") or {}).get("app_mode") != "adopted":
            raise HTTPException(400, "This project was not adopted (it has no analysis / brief).")
        return v

    def snap(v: Dict):
        s = v.get("snapshot") or {}
        return s, s.get("analysis") or {}, s.get("brief") or {}

    def checks_fn(analysis, brief):
        return lambda files, say: adopt_client.run_job("checks", files, {"analysis": analysis, "brief": brief}, say)

    def mission_or_404(mid: str, user) -> Dict:
        m = adopt_robot.get(mid)
        if not m:
            raise HTTPException(404, "Mission not found.")
        _project_or_404(m["project_id"], user)
        return m

    def history(s: Dict) -> List[Dict]:
        return s.get("missions") or []

    def runner_health() -> Dict:
        if time.time() - _HEALTH["at"] > 60 or not _HEALTH["value"]:
            hv = adopt_client.health()
            env = hv.get("environment") or {}
            _HEALTH.update(at=time.time(), value={"mode": hv.get("mode"), "ok": bool(hv.get("ok")),
                                                  "node": env.get("node", ""), "python": env.get("python", ""),
                                                  "error": hv.get("error", "")})
        return _HEALTH["value"]

    # ── 1. bring the project ───────────────────────────────────────────────
    @app.post("/api/adopt/upload")
    async def adopt_upload(file: UploadFile = File(...), user: User = Depends(current_user)):
        _require(user, "project.create")
        data = await file.read()
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "The ZIP is larger than 200 MB. Leave out node_modules and build output.")
        if not data.startswith(b"PK"):
            raise HTTPException(400, "Upload a .zip file.")
        _gc()
        uid = uuid.uuid4().hex
        _UPLOADS[uid] = {"data": data, "name": file.filename or "project.zip", "owner": user.id, "at": time.time()}
        return {"upload_id": uid, "name": file.filename, "bytes": len(data)}

    def _w_analyze(say, u, source: Dict):
        kind = source.get("kind")
        if kind == "zip":
            up = _UPLOADS.get(source.get("upload_id") or "")
            if not up or up["owner"] != u.id:
                raise ValueError("The upload expired — upload the ZIP again.")
            say(f"Reading {up['name']}…")
            files, info = adopt_files.from_zip(up["data"])
            label = up["name"]
            src = {"kind": "zip", "label": label}
        elif kind == "git":
            files, info = adopt_files.from_git(source.get("url", ""), source.get("branch", ""), source.get("token", ""), say)
            label = source["url"].rstrip("/").split("/")[-1].removesuffix(".git") + f" ({info.get('branch', '')})"
            src = {"kind": "git", "label": label, "url": source["url"], "branch": info.get("branch", ""),
                   "commit": info.get("commit", "")}
        elif kind == "cf":
            import api_deploy
            d = api_deploy.deployer(u, "dev", say)
            say(f"Downloading {source.get('app')} from Cloud Foundry…")
            files, info = adopt_files.from_cf(source.get("app", ""), d.cf_home, say)
            related = [r["app"] for r in info.get("related") or []]
            src = {"kind": "cf", "label": " + ".join([source.get("app", "")] + related), "app": source.get("app", ""),
                   "url": info.get("cf_url", ""), "related": info.get("related") or [], "external": info.get("external") or []}
        else:
            raise ValueError("Choose a source: zip, git or cf.")
        say(f"{info['files']} files ({info['bytes'] // 1024} KB)" + (f", {len(info['skipped'])} skipped" if info["skipped"] else ""))
        a = adopt_scan.analyze(files, src, info, say)
        fn, _ = _ai()
        if fn:
            say("Understanding the business scenario with AI…")
            try:
                a["understanding"] = adopt_scan.understand(files, a, fn)
            except Exception as e:  # noqa: BLE001 — the analysis is useful without it
                say(f"AI summary not available: {str(e)[:200]}")
        a["questions"] = adopt_scan.questions(files, a, src)
        aid = uuid.uuid4().hex[:16]
        _ANALYSES[aid] = {"analysis": a, "files": files, "owner": u.id, "at": time.time(), "source": src}
        say("Analysis complete.")
        return _public_analysis(a, aid)

    @app.post("/api/jobs/adopt-analyze")
    def job_adopt_analyze(body: Dict = Body(...), user: User = Depends(current_user)):
        _require(user, "project.create")
        src = body.get("source") or {}
        if src.get("kind") not in ("zip", "git", "cf"):
            raise HTTPException(400, "Choose a source: ZIP upload, Git repository or Cloud Foundry app.")
        if src.get("kind") == "git" and not str(src.get("url") or "").startswith("https://"):
            raise HTTPException(400, "Enter an https:// Git repository URL.")
        if src.get("kind") == "cf" and not src.get("app"):
            raise HTTPException(400, "Choose a Cloud Foundry app.")
        _gc()
        label = {"zip": "Analysing the uploaded project", "git": f"Analysing {src.get('url', '')}",
                 "cf": f"Analysing {src.get('app', '')}"}[src["kind"]]
        return _start(user, "adopt-analyze", label, _w_analyze, user, src)

    @app.get("/api/adopt/analysis/{aid}")
    def adopt_analysis(aid: str, user: User = Depends(current_user)):
        a = _ANALYSES.get(aid)
        if not a or a["owner"] != user.id:
            raise HTTPException(404, "Analysis expired — analyse the project again.")
        return _public_analysis(a["analysis"], aid)

    @app.post("/api/adopt/{aid}/create")
    def adopt_create(aid: str, body: Dict = Body(...), user: User = Depends(current_user)):
        _require(user, "project.create")
        a = _ANALYSES.get(aid)
        if not a or a["owner"] != user.id:
            raise HTTPException(404, "Analysis expired — analyse the project again.")
        analysis = {k: v for k, v in a["analysis"].items() if k != "questions"}
        brief = _brief_from_answers(body.get("answers") or {}, analysis)
        name = _body_str(body, "name") or analysis.get("name") or "Adopted project"
        snapshot = {"app_mode": "adopted", "app_id_v": _slug(analysis.get("name", name)),
                    "user_prompt": brief.get("purpose") or "", "analysis": analysis, "brief": brief,
                    "source": a["source"], "missions": []}
        desc = (brief.get("purpose") or "")[:300] or f"Adopted {', '.join(analysis.get('stack', [])[:3])} project"
        proj = STORE.create_project(name, user, "adopted", app_id=snapshot["app_id_v"], description=desc)
        v = STORE.save_version(proj["id"], user, snapshot, a["files"],
                               label=f"Adopted from {a['source'].get('label') or a['source']['kind']}")
        STORE.audit(user, proj["id"], "project.adopt", {"source": a["source"].get("kind"), "label": a["source"].get("label"),
                                                         "files": len(a["files"]), "stack": analysis.get("stack", [])[:6]})
        _ANALYSES.pop(aid, None)
        return {"project_id": proj["id"], "version_id": v["id"]}

    # ── 3. workspace ───────────────────────────────────────────────────────
    @app.get("/api/adopt/projects/{pid}")
    def adopt_project(pid: str, user: User = Depends(current_user)):
        v = adopted_version(pid, user)
        s, analysis, brief = snap(v)
        meta = {k: v.get(k) for k in ("id", "number", "label", "author", "created_at", "file_count")}
        q = adopt_scan.questions(v["files"], analysis, s.get("source") or {})
        answers = _answers_from_brief(brief)
        for x in q:                                   # current answers into the question list
            if x["id"] in answers:
                x["value"] = answers[x["id"]]
        return {"project": STORE.get_project(pid), "version": meta, "analysis": _public_analysis(dict(analysis, questions=q)),
                "brief": brief, "source": s.get("source") or {},
                "missions": [adopt_robot.summary(m) for m in adopt_robot.list_for(pid)][:50],
                "applied": history(s), "runner": runner_health()}

    @app.post("/api/adopt/projects/{pid}/brief")
    def adopt_brief(pid: str, body: Dict = Body(...), user: User = Depends(current_user)):
        p = _project_or_404(pid, user)
        _require(user, "project.edit", p)
        v = adopted_version(pid, user)
        s, analysis, _ = snap(v)
        s = dict(s, brief=_brief_from_answers(body.get("answers") or {}, analysis))
        nv = STORE.save_version(pid, user, s, v["files"], label="Brief updated")
        return {"version_id": nv["id"], "number": nv["number"]}

    def _w_reanalyze(say, u, pid):
        v = STORE.latest_version(pid, with_files=True)
        s, old, brief = snap(v)
        a = adopt_scan.analyze(v["files"], s.get("source") or {}, {"files": len(v["files"]), "bytes": 0}, say)
        fn, _ = _ai()
        if fn:
            say("Understanding the business scenario with AI…")
            try:
                a["understanding"] = adopt_scan.understand(v["files"], a, fn)
            except Exception as e:  # noqa: BLE001
                say(f"AI summary not available: {str(e)[:200]}")
                a["understanding"] = old.get("understanding")
        nv = STORE.save_version(pid, u, dict(s, analysis=a), v["files"], label="Re-analysed")
        return {"version_id": nv["id"], "number": nv["number"]}

    @app.post("/api/jobs/adopt-reanalyze")
    def job_reanalyze(body: Dict = Body(...), user: User = Depends(current_user)):
        pid = _body_str(body, "project_id")
        p = _project_or_404(pid, user)
        _require(user, "project.edit", p)
        adopted_version(pid, user)
        return _start(user, "adopt-reanalyze", "Re-analysing the project", _w_reanalyze, user, pid)

    # ── robot ──────────────────────────────────────────────────────────────
    def _w_plan(say, u, mid, conv):
        m = adopt_robot.get(mid)
        v = STORE.get_version(m["version_id"], with_files=True)
        s, analysis, brief = snap(v)
        try:
            return adopt_robot.public(adopt_robot.plan(m, v["files"], analysis, brief, history(s), conv, say))
        except Exception as e:  # noqa: BLE001
            m["status"], m["error"] = "failed", str(e)[:800]
            adopt_robot.save(m)
            raise

    @app.post("/api/jobs/robot-plan")
    def job_robot_plan(body: Dict = Body(...), user: User = Depends(current_user)):
        pid = _body_str(body, "project_id")
        p = _project_or_404(pid, user)
        _require(user, "project.edit", p)
        req = _body_str(body, "request")
        if len(req) < 8:
            raise HTTPException(400, "Describe the change you want (a sentence or two).")
        v = adopted_version(pid, user)
        conv = converse()
        m = adopt_robot.new_mission(pid, v["id"], user.id, req)
        STORE.audit(user, pid, "robot.mission", {"mission": m["id"], "request": req[:300]})
        r = _start(user, "robot-plan", "AI: understanding the request", _w_plan, user, m["id"], conv)
        return dict(r, mission_id=m["id"])

    def _w_run(say, u, mid, conv):
        m = adopt_robot.get(mid)
        v = STORE.get_version(m["version_id"], with_files=True)
        s, analysis, brief = snap(v)
        return adopt_robot.public(adopt_robot.run(m, v["files"], analysis, brief, history(s), conv,
                                                  checks_fn(analysis, brief), say,
                                                  terminal=adopt_client.Session(v["files"])))

    @app.post("/api/jobs/robot-run")
    def job_robot_run(body: Dict = Body(...), user: User = Depends(current_user)):
        m = mission_or_404(_body_str(body, "mission_id"), user)
        _require(user, "project.edit", STORE.get_project(m["project_id"]))
        if m["status"] in ("working", "understanding"):
            raise HTTPException(409, "The AI assistant is already working on this mission.")
        if m["status"] in ("applied", "discarded"):
            raise HTTPException(409, f"This mission is {m['status']}.")
        m["answers"] = {str(k): str(v) for k, v in (body.get("answers") or {}).items()}
        adopt_robot.save(m)
        conv = converse()
        return _start(user, "robot-run", "AI: implementing the change", _w_run, user, m["id"], conv)

    def _w_revise(say, u, mid, feedback, conv):
        m = adopt_robot.get(mid)
        v = STORE.get_version(m["version_id"], with_files=True)
        s, analysis, brief = snap(v)
        return adopt_robot.public(adopt_robot.revise(m, feedback, v["files"], analysis, brief, history(s), conv,
                                                     checks_fn(analysis, brief), say,
                                                     terminal=adopt_client.Session(v["files"])))

    @app.post("/api/jobs/robot-revise")
    def job_robot_revise(body: Dict = Body(...), user: User = Depends(current_user)):
        m = mission_or_404(_body_str(body, "mission_id"), user)
        _require(user, "project.edit", STORE.get_project(m["project_id"]))
        fb = _body_str(body, "feedback")
        if not fb:
            raise HTTPException(400, "Write what should change.")
        if m["status"] not in ("ready", "failed"):
            raise HTTPException(409, "Only a finished mission can be revised.")
        conv = converse()
        return _start(user, "robot-revise", "AI: revising the change", _w_revise, user, m["id"], fb, conv)

    @app.get("/api/adopt/missions/{mid}")
    def mission(mid: str, user: User = Depends(current_user)):
        return adopt_robot.public(mission_or_404(mid, user))

    @app.get("/api/adopt/missions/{mid}/file")
    def mission_file(mid: str, path: str, user: User = Depends(current_user)):
        m = mission_or_404(mid, user)
        files = m.get("_files") or {}
        if path in files and files[path] is not None:
            c = files[path]
        else:
            v = STORE.get_version(m["version_id"], with_files=True)
            c = None if path in files else (v["files"] or {}).get(path)
        if c is None:
            raise HTTPException(404, "File not found (or deleted by this change).")
        return PlainTextResponse("(binary file)" if adopt_files.is_binary(c) else c)

    @app.get("/api/adopt/projects/{pid}/missions")
    def missions(pid: str, user: User = Depends(current_user)):
        _project_or_404(pid, user)
        return {"missions": [adopt_robot.summary(m) for m in adopt_robot.list_for(pid)]}

    @app.post("/api/adopt/missions/{mid}/apply")
    def mission_apply(mid: str, body: Dict = Body(default={}), user: User = Depends(current_user)):
        m = mission_or_404(mid, user)
        p = STORE.get_project(m["project_id"])
        _require(user, "project.edit", p)
        if m["status"] != "ready":
            raise HTTPException(409, "Only a finished (ready) mission can be applied.")
        base = STORE.get_version(m["version_id"], with_files=True)
        latest = STORE.latest_version(m["project_id"], with_files=False)
        files = dict(base["files"])
        for path, c in (m.get("_files") or {}).items():
            if c is None:
                files.pop(path, None)
            else:
                files[path] = c
        s = dict(base.get("snapshot") or {})
        if latest and latest["id"] != base["id"]:          # keep the newest brief / analysis
            s = dict((STORE.get_version(latest["id"], with_files=False) or {}).get("snapshot") or s)
            other = STORE.get_version(latest["id"], with_files=True)
            if other and other["files"] != base["files"]:
                conflicts = [c["path"] for c in m["changes"] if other["files"].get(c["path"]) != base["files"].get(c["path"])]
                if conflicts:
                    raise HTTPException(409, "Newer versions changed the same files (" + ", ".join(conflicts[:5])
                                        + "). Start the request again on the latest version.")
                files = dict(other["files"])
                for path, c in (m.get("_files") or {}).items():
                    if c is None:
                        files.pop(path, None)
                    else:
                        files[path] = c
        s["missions"] = (s.get("missions") or []) + [{"id": m["id"], "request": m["request"], "summary": m["summary"][:1500],
                                                      "at": time.time(), "author": m["author"],
                                                      "changes": len(m["changes"])}]
        label = _body_str(body, "label") or ("AI change: " + m["request"][:70])
        nv = STORE.save_version(m["project_id"], user, s, files, label=label)
        if m.get("checks"):
            STORE.set_validation(nv["id"], m["checks"], user)
        m["status"] = "applied"
        m["applied_version"] = {"id": nv["id"], "number": nv["number"]}
        adopt_robot.save(m)
        STORE.audit(user, m["project_id"], "robot.apply", {"mission": m["id"], "version": nv["number"],
                                                           "files": [c["path"] for c in m["changes"]][:30]})
        return {"version_id": nv["id"], "number": nv["number"]}

    @app.post("/api/adopt/missions/{mid}/discard")
    def mission_discard(mid: str, user: User = Depends(current_user)):
        m = mission_or_404(mid, user)
        _require(user, "project.edit", STORE.get_project(m["project_id"]))
        if m["status"] in ("working", "understanding"):
            raise HTTPException(409, "Wait until the AI assistant has finished.")
        m["status"] = "discarded"
        return adopt_robot.public(adopt_robot.save(m))

    def _w_suggest(say, u, pid):
        v = STORE.latest_version(pid, with_files=True)
        s, analysis, brief = snap(v)
        fn, _ = _ai()
        say("Reviewing the project for valuable next changes…")
        return {"suggestions": adopt_robot.suggest(v["files"], analysis, brief, fn)}

    @app.post("/api/jobs/robot-suggest")
    def job_robot_suggest(body: Dict = Body(...), user: User = Depends(current_user)):
        pid = _body_str(body, "project_id")
        _project_or_404(pid, user)
        adopted_version(pid, user)
        fn, _ = _ai()
        if not fn:
            raise HTTPException(503, "Suggestions need SAP AI Core.")
        return _start(user, "robot-suggest", "AI: suggesting improvements", _w_suggest, user, pid)

    # ── preview ────────────────────────────────────────────────────────────
    def _w_preview(say, u, files, analysis, brief, pid):
        r = adopt_client.run_job("preview", files, {"analysis": analysis, "brief": brief}, say, timeout=1800)
        _PREVIEWS[r["preview_id"]] = {"owner": u.id, "project_id": pid, "started": time.time()}
        say("Preview is running.")
        return {"preview_id": r["preview_id"], "url": adopt_client.preview_url(r), "kind": r.get("kind"),
                "entry": r.get("entry"), "services": r.get("services") or [], "warnings": r.get("warnings") or []}

    @app.post("/api/jobs/adopt-preview")
    def job_adopt_preview(body: Dict = Body(...), user: User = Depends(current_user)):
        mid = _body_str(body, "mission_id")
        if mid:
            m = mission_or_404(mid, user)
            v = STORE.get_version(m["version_id"], with_files=True)
            files = adopt_robot.Workspace(v["files"], m.get("_files") or {}).files()
            label = "Preview of the AI change"
        else:
            v = _version_or_404(_body_str(body, "version_id"), user)
            v = STORE.get_version(v["id"], with_files=True)
            files = v["files"]
            label = f"Preview of version {v['number']}"
        s, analysis, brief = snap(v)
        if s.get("app_mode") != "adopted":
            raise HTTPException(400, "Preview of adopted projects only — generated apps use Try it in the studio.")
        for k, p in list(_PREVIEWS.items()):          # one running preview per user
            if p["owner"] == user.id:
                adopt_client.stop_preview(k)
                _PREVIEWS.pop(k, None)
        return _start(user, "adopt-preview", label, _w_preview, user, files, analysis, brief, v["project_id"])

    def _preview_or_404(pvid: str, user) -> Dict:
        p = _PREVIEWS.get(pvid)
        if not p or p["owner"] != user.id:
            raise HTTPException(404, "Preview not found.")
        return p

    @app.post("/api/adopt/preview/{pvid}/stop")
    def preview_stop(pvid: str, user: User = Depends(current_user)):
        _preview_or_404(pvid, user)
        _PREVIEWS.pop(pvid, None)
        return {"stopped": adopt_client.stop_preview(pvid)}

    @app.get("/api/adopt/preview/{pvid}/log")
    def preview_log(pvid: str, user: User = Depends(current_user)):
        _preview_or_404(pvid, user)
        return PlainTextResponse(adopt_client.preview_log(pvid))

    # ── validation (also used by /api/jobs/validate for adopted versions) ─
    def validate_version(say, u, vid):
        v = STORE.get_version(vid, with_files=True)
        s, analysis, brief = snap(v)
        rep = adopt_client.run_job("checks", v["files"], {"analysis": analysis, "brief": brief}, say)
        STORE.set_validation(vid, rep, u)
        return rep
    h["_adopt_validate"] = validate_version

    # ── deploy ─────────────────────────────────────────────────────────────
    @app.get("/api/adopt/deploy/{vid}")
    def adopt_deploy_info(vid: str, user: User = Depends(current_user)):
        v = _version_or_404(vid, user, with_files=False)
        full = STORE.get_version(vid, with_files=True)
        s, analysis, brief = snap(full)
        targets = adopt_scan.deploy_targets(analysis.get("modules") or [], analysis.get("_ctx") or {}, s.get("source") or {})
        gate = {}
        for env in ac.environments():
            ok, reasons = ac.check_deploy(user, env, v["project_id"], vid, STORE, v.get("validation"))
            gate[env] = {"allowed": ok, "reasons": reasons, "reason": " · ".join(reasons)}
        default = brief.get("deploy_target") if brief.get("deploy_target") in [t["value"] for t in targets] else targets[0]["value"]
        return {"targets": targets, "default": default, "env_gate": gate, "source": s.get("source") or {},
                "environments": {k: {"label": e.get("label", k)} for k, e in ac.environments().items()}}

    def _mta_entry_app(files: Dict[str, str]) -> str:
        try:
            import yaml
            mta = yaml.safe_load(files.get("mta.yaml") or "") or {}
            mods = mta.get("modules") or []
            for t in ("approuter.nodejs", "nodejs", "html5"):
                for m in mods:
                    if m.get("type") == t:
                        return (m.get("parameters") or {}).get("app-name") or m.get("name", "")
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _w_deploy(say, u, vid, env, target, git):
        import api_deploy
        v = STORE.get_version(vid, with_files=True)
        s, analysis, brief = snap(v)
        logs: List[str] = []

        def log(line):
            logs.append(str(line))
            say(line)

        def record(status, url="", extra=None):
            STORE.record_deployment(v["project_id"], vid, env, dict(api_deploy.target(env), app=extra or "", target=target),
                                    u, status, app_url=url, log_tail="\n".join(logs)[-20000:])

        if target == "git-pr":
            src = s.get("source") or {}
            if src.get("kind") != "git":
                raise ValueError("This project did not come from Git.")
            token = (git or {}).get("token") or ""
            base_branch = (git or {}).get("base_branch") or src.get("branch") or "main"
            branch = (git or {}).get("branch") or f"evoke/v{v['number']}-{int(time.time())}"
            title = (git or {}).get("title") or (v.get("label") or f"Studio version {v['number']}")
            res = adopt_files.push_branch(v["files"], src["url"], base_branch, branch, token, title,
                                          u.name or u.id, u.id if "@" in u.id else f"{u.id}@users.noreply.studio", log)
            if not res["pushed"]:
                log("Nothing to push — the repository already has this content.")
                return {"status": "skipped", "url": "", "log": "\n".join(logs)}
            pr = adopt_files.open_pull_request(src["url"], token, base_branch, branch, title,
                                               "Created by the Evoke Fiori App Studio.\n\n" + "\n".join(
                                                   f"- {x.get('request', '')}" for x in (s.get("missions") or [])[-10:]))
            log(f"Branch {branch} pushed ({res['changed']} files)." + (f" Pull request: {pr}" if pr else ""))
            record("succeeded", pr or src["url"], branch)
            return {"status": "succeeded", "url": pr or src["url"], "pr_url": pr, "log": "\n".join(logs)}

        d = api_deploy.deployer(u, env, say)
        if not d.check_cf_cli():
            raise ValueError("The Cloud Foundry CLI is not available on the studio host.")
        if not d.is_logged_in():
            raise ValueError("The studio has no Cloud Foundry session (configure CF_TECH_USER / CF_TECH_PASSWORD).")
        log("Building in the studio runner…")
        r = adopt_client.run_job("build", v["files"], {"analysis": analysis, "brief": brief, "target": target}, log, timeout=2700)
        data = adopt_client.artifact(r["artifact_id"])
        log(f"Build ready ({r.get('kind')}, {len(data) // 1024} KB" + (f", builder {r.get('builder')}" if r.get("builder") else "") + ").")
        with tempfile.TemporaryDirectory() as tmp:
            for line in d.target():
                log(line)
            if target == "mta":
                if not d.check_multiapps_plugin():
                    raise ValueError("The CF multiapps plugin (cf deploy) is not installed on the studio host.")
                os.makedirs(os.path.join(tmp, "mta_archives"))
                with open(os.path.join(tmp, "mta_archives", "app.mtar"), "wb") as f:
                    f.write(data)
                failed = False
                for line in d.deploy_mtar(tmp):
                    log(line)
                    failed = failed or line.startswith("[ERROR]") or "Deploy failed" in line
                app_name = _mta_entry_app(v["files"])
            else:
                import io
                import zipfile
                zipfile.ZipFile(io.BytesIO(data)).extractall(tmp)
                failed = False
                for line in d._run(["cf", "push", "-f", os.path.join(tmp, "manifest.yml")], cwd=tmp):
                    log(line)
                    failed = failed or line.startswith("[ERROR]") or line.strip() == "FAILED"
                app_name = r.get("app") or ""
            url = d.get_app_url(app_name) if app_name else ""
        status = "failed" if failed else "succeeded"
        record(status, url or "", app_name)
        if url:
            log(f"🌐 {url}")
        return {"status": status, "url": url or "", "log": "\n".join(logs)}

    @app.post("/api/jobs/adopt-deploy")
    def job_adopt_deploy(body: Dict = Body(...), user: User = Depends(current_user)):
        vid, env = _body_str(body, "version_id"), _body_str(body, "env") or "dev"
        v = _version_or_404(vid, user, with_files=False)
        target = _body_str(body, "target") or "mta"
        if target == "download":
            raise HTTPException(400, "Download the ZIP from the version list.")
        if target not in ("mta", "cf-push", "git-pr"):
            raise HTTPException(400, "Unknown deploy target.")
        ok, reasons = ac.check_deploy(user, env, v["project_id"], vid, STORE, v.get("validation"))
        if not ok:
            raise HTTPException(409, "Deployment blocked: " + " · ".join(reasons))
        if target == "git-pr" and not ((body.get("git") or {}).get("token")):
            raise HTTPException(400, "A Git access token with write access is needed to push the branch.")
        return _start(user, "adopt-deploy", f"Deploying version {v['number']} ({target}) to {env}", _w_deploy,
                      user, vid, env, target, body.get("git") or {})
