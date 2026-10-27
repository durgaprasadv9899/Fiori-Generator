"""
runner_service.py — the studio runner: executes adopted projects (checks, previews, deploy builds) in
sandboxes, isolated from the studio API.

On SAP BTP it is its own Cloud Foundry app (manifest-runner.yml: Node.js + Python buildpacks, no
service bindings — client code that runs here never sees studio credentials). Locally the studio API
starts it on a second port (adopt_client.ensure_local).

Two kinds of callers:
  * the studio API — every /jobs, /artifacts and /previews call carries X-Runner-Token (RUNNER_TOKEN);
  * the user's browser — opens a preview through a signed, expiring link /p/<token>/<entry>. The link
    sets a cookie, and from then on every request on this origin is proxied to that preview, so apps
    that use absolute paths (/odata/…, /api/…, /assets/…) work unchanged. The preview runs on this
    origin, never on the studio's: previewed code cannot reach the studio session or API.

Run locally:  python -m uvicorn runner_service:app --port 8621   (RUNNER_TOKEN must be set)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import uuid
from typing import Dict, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response

import adopt_files
import adopt_runner
import jobs

app = FastAPI(title="Evoke studio runner", version="1.0")
SESSION = "runner"
_ARTIFACTS: Dict[str, Dict] = {}
LINK_TTL = 8 * 3600
COOKIE = "evoke_preview"


def _secret() -> str:
    return os.environ.get("RUNNER_TOKEN", "")


def _auth(request: Request) -> None:
    s = _secret()
    got = request.headers.get("x-runner-token", "")
    if not s or not hmac.compare_digest(s, got):
        raise HTTPException(401, "runner token required")


def sign(pid: str, ttl: int = LINK_TTL) -> str:
    exp = int(time.time()) + ttl
    sig = hmac.new(_secret().encode(), f"{pid}.{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{pid}.{exp}.{sig}"


def verify(token: str) -> Optional[str]:
    try:
        pid, exp, sig = (token or "").split(".")
        good = hmac.new(_secret().encode(), f"{pid}.{exp}".encode(), hashlib.sha256).hexdigest()[:32]
        if _secret() and hmac.compare_digest(good, sig) and int(exp) > time.time():
            return pid
    except ValueError:
        pass
    return None


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})


@app.get("/health")
def health():
    return {"ok": True, "environment": adopt_runner.environment(), "previews": len(adopt_runner.list_previews())}


# ─────────────────────────────────────────────────────────────────────────────
# Jobs (studio API only)
# ─────────────────────────────────────────────────────────────────────────────
def _w_checks(say, files, meta):
    return adopt_runner.run_checks(files, meta.get("analysis") or {}, meta.get("brief") or {}, say)


def _w_preview(say, files, meta):
    r = adopt_runner.start_preview(files, meta.get("analysis") or {}, meta.get("brief") or {}, say)
    r["token"] = sign(r["preview_id"])
    return r


def _w_build(say, files, meta):
    data, info = adopt_runner.build_artifact(files, meta.get("analysis") or {}, meta.get("brief") or {},
                                             meta.get("target") or "mta", say)
    aid = uuid.uuid4().hex
    _ARTIFACTS[aid] = {"data": data, "at": time.time()}
    for k in [k for k, v in _ARTIFACTS.items() if time.time() - v["at"] > 3600]:
        _ARTIFACTS.pop(k, None)
    return dict(info, artifact_id=aid, bytes=len(data))


def _w_rebuild(say, files, meta):
    return adopt_runner.rebuild_outputs(files, meta.get("outputs") or [], say)


_KINDS = {"checks": (_w_checks, "Validating"), "preview": (_w_preview, "Starting preview"), "build": (_w_build, "Building"),
          "rebuild": (_w_rebuild, "Rebuilding the UI bundle")}


@app.post("/jobs/{kind}")
async def start_job(kind: str, request: Request, files: UploadFile = File(...), meta: str = Form("{}")):
    _auth(request)
    if kind not in _KINDS:
        raise HTTPException(404, "unknown job kind")
    fmap, _ = adopt_files.from_zip(await files.read())
    fn, label = _KINDS[kind]
    j = jobs.start(kind, "runner", SESSION, label, fn, fmap, json.loads(meta or "{}"))
    return {"job_id": j.id}


@app.get("/jobs/{job_id}")
def job(job_id: str, request: Request, since: int = 0):
    _auth(request)
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(404, "job not found (the runner may have restarted)")
    return {"status": j.status, "messages": j.messages[since:], "count": len(j.messages), "error": j.error,
            "result": j.result if j.status == "done" else None}


@app.get("/artifacts/{aid}")
def artifact(aid: str, request: Request):
    _auth(request)
    a = _ARTIFACTS.pop(aid, None)
    if not a:
        raise HTTPException(404, "artifact not found")
    return Response(a["data"], media_type="application/octet-stream")


@app.post("/sessions")
async def session_create(request: Request, files: UploadFile = File(...)):
    _auth(request)
    fmap, _ = adopt_files.from_zip(await files.read())
    return {"session_id": adopt_runner.session_create(fmap)}


def _session(fn, *args):
    try:
        return fn(*args)
    except KeyError:
        raise HTTPException(404, "session expired")


@app.post("/sessions/{sid}/sync")
async def session_sync(sid: str, request: Request):
    _auth(request)
    body = await request.json()
    return {"synced": _session(adopt_runner.session_sync, sid, body.get("files") or {})}


@app.post("/sessions/{sid}/exec")
async def session_exec(sid: str, request: Request):
    _auth(request)
    body = await request.json()
    import asyncio
    return await asyncio.to_thread(_session, adopt_runner.session_exec, sid, str(body.get("cmd") or ""),
                                   float(body.get("timeout") or 300))


@app.delete("/sessions/{sid}")
def session_close(sid: str, request: Request):
    _auth(request)
    return {"closed": adopt_runner.session_close(sid)}


@app.post("/previews/{pid}/stop")
def preview_stop(pid: str, request: Request):
    _auth(request)
    return {"stopped": adopt_runner.stop_preview(pid)}


@app.get("/previews/{pid}/log")
def preview_log(pid: str, request: Request):
    _auth(request)
    return PlainTextResponse(adopt_runner.preview_log(pid))


@app.get("/previews")
def previews(request: Request):
    _auth(request)
    return {"previews": adopt_runner.list_previews()}


# ─────────────────────────────────────────────────────────────────────────────
# Browser: signed link -> cookie -> proxy
# ─────────────────────────────────────────────────────────────────────────────
def _secure(request: Request) -> bool:
    return (request.headers.get("x-forwarded-proto") or request.url.scheme) == "https"


@app.get("/p/{token}/{path:path}")
def open_link(token: str, path: str, request: Request):
    pid = verify(token)
    if not pid or not adopt_runner.get_preview(pid):
        return PlainTextResponse("This preview has ended. Start it again in the studio.", status_code=410)
    q = request.url.query
    resp = RedirectResponse("/" + path + (("?" + q) if q else ""), status_code=302)
    attrs = "; Path=/; HttpOnly; Max-Age=28800"
    attrs += "; Secure; SameSite=None; Partitioned" if _secure(request) else "; SameSite=Lax"
    resp.headers.append("set-cookie", f"{COOKIE}={token}{attrs}")
    return resp


_HOP = {"host", "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
        "transfer-encoding", "upgrade", "content-length", "authorization", "cookie", "accept-encoding"}
_DROP_RESP = {"content-encoding", "content-length", "transfer-encoding", "connection", "www-authenticate",
              "x-frame-options", "content-security-policy", "keep-alive"}


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def proxy(path: str, request: Request):
    import requests
    pid = verify(request.cookies.get(COOKIE, ""))
    p = adopt_runner.get_preview(pid) if pid else None
    if not p:
        return PlainTextResponse("No preview is open in this browser. Start one in the studio (Try it).", status_code=404)
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP}
    headers["accept-encoding"] = "identity"
    app_cookies = "; ".join(f"{k}={v}" for k, v in request.cookies.items() if k != COOKIE)
    if app_cookies:                       # the app's own cookies (e.g. its login session) — never the preview cookie
        headers["cookie"] = app_cookies
    body = await request.body()
    url = f"http://127.0.0.1:{p['port']}/{path}" + (("?" + request.url.query) if request.url.query else "")
    try:
        r = requests.request(request.method, url, headers=headers, data=body or None, allow_redirects=False,
                             timeout=180, auth=tuple(p["mock_auth"]) if p.get("mock_auth") else None)
    except requests.RequestException as e:
        return PlainTextResponse(f"The preview did not answer: {e}", status_code=502)
    content = r.content
    ctype = r.headers.get("content-type", "")
    if re.search(r"text/|json|javascript|xml", ctype) and _LOCAL_ORIGIN.search(content.decode("utf-8", "replace")[:2_000_000]):
        # the app builds absolute links from the address it sees (127.0.0.1:<port>) — point them at this preview
        content = _LOCAL_ORIGIN.sub("", content.decode("utf-8", "replace")).encode("utf-8")
    out = Response(content, status_code=r.status_code)
    for k, v in r.raw.headers.items():
        lk = k.lower()
        if lk in _DROP_RESP:
            continue
        if lk == "location":
            v = _LOCAL_ORIGIN.sub("", v) or "/"
        out.headers.append(k, v)
    return out


_LOCAL_ORIGIN = re.compile(r"https?://(?:127\.0\.0\.1|localhost|0\.0\.0\.0)(?::\d+)?(?=[/\"'?#\s]|$)")
