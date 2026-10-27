"""
adopt_client.py — the studio API's connection to the runner (runner_service.py).

  RUNNER_URL          runner base URL (on BTP: https://evoke-studio-runner.cfapps…). When unset, the API
                      starts a local runner on 127.0.0.1:RUNNER_PORT (default 8621) in a thread.
  RUNNER_PUBLIC_URL   base URL the browser uses for previews (defaults to RUNNER_URL, or
                      http://localhost:<port> locally).
  RUNNER_TOKEN        shared secret between API and runner (generated per process locally).
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from typing import Callable, Dict, Optional

import requests

import adopt_files

_LOCAL = {"started": False, "lock": threading.Lock()}


def ensure_local() -> None:
    if os.environ.get("RUNNER_URL") or _LOCAL["started"]:
        return
    with _LOCAL["lock"]:
        if _LOCAL["started"]:
            return
        os.environ.setdefault("RUNNER_TOKEN", secrets.token_hex(24))
        port = int(os.environ.get("RUNNER_PORT", "8621"))
        try:                                         # already running (another studio process)?
            requests.get(f"http://127.0.0.1:{port}/health", timeout=2)
            raise RuntimeError(f"Port {port} is in use by another process; set RUNNER_PORT or RUNNER_URL.")
        except requests.RequestException:
            pass
        import uvicorn
        import runner_service
        server = uvicorn.Server(uvicorn.Config(runner_service.app, host="127.0.0.1", port=port, log_level="warning"))
        threading.Thread(target=server.run, name="studio-runner", daemon=True).start()
        for _ in range(60):
            try:
                requests.get(f"http://127.0.0.1:{port}/health", timeout=2)
                break
            except requests.RequestException:
                time.sleep(0.25)
        _LOCAL["started"] = True


def base() -> str:
    ensure_local()
    return (os.environ.get("RUNNER_URL") or f"http://127.0.0.1:{os.environ.get('RUNNER_PORT', '8621')}").rstrip("/")


def public_base() -> str:
    b = os.environ.get("RUNNER_PUBLIC_URL") or os.environ.get("RUNNER_URL") or \
        f"http://localhost:{os.environ.get('RUNNER_PORT', '8621')}"
    return b.rstrip("/")


def _h() -> Dict[str, str]:
    return {"X-Runner-Token": os.environ.get("RUNNER_TOKEN", "")}


def mode() -> str:
    return "remote" if os.environ.get("RUNNER_URL") else "local"


def health() -> Dict:
    try:
        r = requests.get(base() + "/health", timeout=15)
        return dict(r.json(), mode=mode()) if r.status_code == 200 else {"ok": False, "mode": mode(), "error": f"HTTP {r.status_code}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "mode": mode(), "error": str(e)[:300]}


def run_job(kind: str, files: Dict[str, str], meta: Dict, say: Callable[[str], None] = lambda m: None,
            timeout: float = 2400) -> Dict:
    """Start a runner job and wait for it, relaying its progress messages."""
    data = adopt_files.to_zip(files)
    try:
        r = requests.post(f"{base()}/jobs/{kind}", headers=_h(), timeout=300,
                          files={"files": ("project.zip", data, "application/zip")},
                          data={"meta": json.dumps(meta, default=str)})
    except requests.RequestException as e:
        raise RuntimeError(f"The studio runner is not reachable: {e}")
    if r.status_code != 200:
        raise RuntimeError(f"Runner refused the job (HTTP {r.status_code}): {r.text[:300]}")
    jid = r.json()["job_id"]
    seen, end = 0, time.time() + timeout
    while time.time() < end:
        time.sleep(1.5)
        try:
            j = requests.get(f"{base()}/jobs/{jid}", headers=_h(), params={"since": seen}, timeout=60).json()
        except requests.RequestException:
            continue
        for m in j.get("messages") or []:
            say(m)
        seen = j.get("count", seen)
        if j.get("status") == "done":
            return j.get("result") or {}
        if j.get("status") == "error":
            raise RuntimeError(j.get("error") or "runner job failed")
    raise RuntimeError("The runner job took too long.")


def artifact(aid: str) -> bytes:
    r = requests.get(f"{base()}/artifacts/{aid}", headers=_h(), timeout=600)
    if r.status_code != 200:
        raise RuntimeError(f"Could not fetch the build result (HTTP {r.status_code}).")
    return r.content


class Session:
    """A persistent runner sandbox for the robot: created on first use, changed files synced before each
    command, closed at the end of the mission."""

    def __init__(self, base_files: Dict[str, str]):
        self.base = base_files
        self.sid = ""
        self.synced: Dict[str, Optional[str]] = {}

    def exec(self, cmd: str, changes: Dict[str, Optional[str]], timeout: float = 300) -> Dict:
        if not self.sid:
            files = dict(self.base)
            for p, c in changes.items():
                if c is None:
                    files.pop(p, None)
                else:
                    files[p] = c
            r = requests.post(f"{base()}/sessions", headers=_h(), timeout=300,
                              files={"files": ("project.zip", adopt_files.to_zip(files), "application/zip")})
            if r.status_code != 200:
                raise RuntimeError(f"Runner session failed (HTTP {r.status_code}): {r.text[:200]}")
            self.sid = r.json()["session_id"]
            self.synced = dict(changes)
        else:
            delta = {p: c for p, c in changes.items() if self.synced.get(p, "\0") != c}
            for p in self.synced:                       # reverted to the base content
                if p not in changes:
                    delta[p] = self.base.get(p)
            if delta:
                r = requests.post(f"{base()}/sessions/{self.sid}/sync", headers=_h(), json={"files": delta}, timeout=120)
                if r.status_code == 404:
                    self.sid = ""
                    return self.exec(cmd, changes, timeout)
            self.synced = dict(changes)
        r = requests.post(f"{base()}/sessions/{self.sid}/exec", headers=_h(), json={"cmd": cmd, "timeout": timeout},
                          timeout=timeout + 60)
        if r.status_code == 404:
            self.sid = ""
            return self.exec(cmd, changes, timeout)
        return r.json()

    def close(self) -> None:
        if self.sid:
            try:
                requests.delete(f"{base()}/sessions/{self.sid}", headers=_h(), timeout=30)
            except requests.RequestException:
                pass
            self.sid = ""


def stop_preview(pid: str) -> bool:
    try:
        return bool(requests.post(f"{base()}/previews/{pid}/stop", headers=_h(), timeout=30).json().get("stopped"))
    except Exception:  # noqa: BLE001
        return False


def preview_log(pid: str) -> str:
    try:
        return requests.get(f"{base()}/previews/{pid}/log", headers=_h(), timeout=30).text
    except Exception as e:  # noqa: BLE001
        return f"(log not available: {e})"


def compare_base() -> str:
    """A second address of the runner for the 'before' preview of a side-by-side compare: the runner picks the
    preview from a cookie per host, so two previews open at once need two hosts (RUNNER_COMPARE_URL, else the
    runner host with '-compare' added to its first label; locally 127.0.0.1 next to localhost)."""
    if os.environ.get("RUNNER_COMPARE_URL"):
        return os.environ["RUNNER_COMPARE_URL"].rstrip("/")
    from urllib.parse import urlsplit
    u = urlsplit(public_base())
    host = u.hostname or ""
    if host == "localhost":
        alt = "127.0.0.1"
    elif host in ("127.0.0.1", "::1"):
        alt = "localhost"
    else:
        first, _, rest = host.partition(".")
        alt = f"{first}-compare.{rest}" if rest else host
    return f"{u.scheme}://{alt}" + (f":{u.port}" if u.port else "")


def preview_url(result: Dict, slot: str = "main") -> str:
    entry = (result.get("entry") or "/").lstrip("/")
    return f"{compare_base() if slot == 'base' else public_base()}/p/{result['token']}/{entry}"
