"""
adopt_runner.py — the sandbox that RUNS adopted projects: validation checks, live previews and
deploy builds. It executes the client's own code (npm install scripts, builds, tests, servers), so
it runs in the separate runner service (runner_service.py — on BTP its own Cloud Foundry app without
service bindings; locally a second port started by the studio API). Child processes get a minimal
environment: no studio credentials ever reach them.

Reports use the build_validation format ({checks, summary, blocking, environment, level, seconds}) so
the studio shows and gates them like any other validation.
"""

from __future__ import annotations

import http.server
import json
import os
import re
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from typing import Callable, Dict, List, Optional, Tuple

import adopt_files
import build_validation as bv

IS_WIN = os.name == "nt"
JSONC = re.compile(r"(^|/)(tsconfig[\w.-]*|jsconfig[\w.-]*|\.eslintrc[\w.-]*|devcontainer|settings|launch|"
                   r"extensions|tasks|\.babelrc|api-extractor|tslint)\.json$")
PREVIEW_IDLE = 25 * 60
MAX_PREVIEWS = 4


# ─────────────────────────────────────────────────────────────────────────────
# Environment for child processes
# ─────────────────────────────────────────────────────────────────────────────
_KEEP_ENV = ("PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
             "TMPDIR", "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA", "LANG", "LC_ALL",
             "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS", "HOSTNAME", "SHELL", "USER", "LOGNAME",
             "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy", "NODE_EXTRA_CA_CERTS",
             "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "LD_LIBRARY_PATH", "PYTHONHOME")


def child_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Minimal environment: OS basics + Node/npm settings. No AICORE_*, CF_*, VCAP_*, tokens."""
    base = bv._node_env()
    env = {k: base[k] for k in _KEEP_ENV if k in base}
    cache = os.path.join(bv.data_dir(), "npm-cache")
    os.makedirs(cache, exist_ok=True)
    env.update({"npm_config_cache": cache, "npm_config_audit": "false", "npm_config_fund": "false",
                "npm_config_update_notifier": "false", "npm_config_loglevel": "error", "CI": "true",
                "BROWSERSLIST_IGNORE_OLD_DATA": "1", "NODE_OPTIONS": "--max-old-space-size=2560",
                "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_CACHE_DIR": os.path.join(bv.data_dir(), "pip-cache"),
                "PYTHONDONTWRITEBYTECODE": "1", "CDS_PLUGIN_UI5_ACTIVE": "false", "NG_CLI_ANALYTICS": "false",
                "NEXT_TELEMETRY_DISABLED": "1"})
    env.update(extra or {})
    return env


def environment() -> Dict:
    e = bv.environment()

    def ver(cmd):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            return (r.stdout or r.stderr).strip().splitlines()[0] if r.returncode == 0 else ""
        except Exception:  # noqa: BLE001
            return ""
    return {"node": e.get("node", ""), "npm": e.get("npm", ""), "python": sys.version.split()[0],
            "git": ver(["git", "--version"]) if shutil.which("git") else "", "java": "",
            "make": bool(shutil.which("make")), "platform": sys.platform}


def _sandbox(files: Dict[str, str], prefix: str = "evoke-adopt-") -> str:
    tmp = tempfile.mkdtemp(prefix=prefix)
    adopt_files.to_dir(files, tmp)
    return tmp


def _rmtree(path: str) -> None:
    for _ in range(3):
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            return
        time.sleep(1)


def _python_venv(tmp: str, env: Dict[str, str], rep=None) -> Dict[str, str]:
    """A venv inside the sandbox; `pip` / `python` in commands then resolve to it."""
    venv = os.path.join(tmp, ".venv")
    bindir = os.path.join(venv, "Scripts" if IS_WIN else "bin")
    if not os.path.isdir(bindir):
        if rep:
            rep.say("Creating a Python virtual environment…")
        subprocess.run([sys.executable, "-m", "venv", venv], capture_output=True, timeout=240)
    env = dict(env)
    env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
    env["VIRTUAL_ENV"] = venv
    return env


def _shell(cmd: str, cwd: str, env: Dict[str, str], timeout: float) -> Tuple[int, str]:
    if timeout <= 1:
        return -9, "time budget exhausted"
    proc = subprocess.Popen(cmd, cwd=cwd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, env=env, **bv._popen_kwargs())
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, out.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        bv._kill_tree(proc)
        try:
            out, _ = proc.communicate(timeout=5)
        except Exception:  # noqa: BLE001
            out = b""
        return -9, (out or b"").decode("utf-8", "replace") + f"\n[timed out after {int(timeout)}s]"


def _tail_err(out: str, n: int = 1600) -> str:
    lines = [l for l in (out or "").splitlines() if l.strip()]
    hit = [i for i, l in enumerate(lines) if re.search(r"\b(error|ERR!|failed|Traceback|Exception)\b", l, re.I)]
    if hit:
        sel = lines[max(0, hit[0] - 2):hit[0] + 14]
        return ("\n".join(sel) + ("\n…\n" + "\n".join(lines[-6:]) if len(lines) > hit[0] + 14 else ""))[-n:]
    return "\n".join(lines[-20:])[-n:]


_TEST_MARK = re.compile(r"●|✕|✗|FAIL\b|AssertionError|Expected|Received|expected|assert|Error:|Traceback|"
                        r"E\s{3}|^\s+at\s.*\.(test|spec)\.|failed|ERR!", re.I)


def test_output(out: str, n: int = 5000) -> str:
    """Failure output of a test / build run: the lines around every failure marker, then the summary."""
    lines = (out or "").splitlines()
    keep = set()
    for i, l in enumerate(lines):
        if _TEST_MARK.search(l):
            keep.update(range(max(0, i - 2), min(len(lines), i + 8)))
    keep.update(range(max(0, len(lines) - 12), len(lines)))
    text, last = [], -2
    for i in sorted(keep):
        if i != last + 1:
            text.append("…")
        text.append(lines[i])
        last = i
    return "\n".join(text)[-n:]


def commands_from_brief(brief: Dict, key: str = "commands") -> List[str]:
    text = (brief or {}).get(key) or ""
    if isinstance(text, list):
        text = "\n".join(text)
    return [l.strip() for l in str(text).splitlines() if l.strip() and not l.strip().startswith("#")]


def _uses_python(cmds: List[str], analysis: Dict) -> bool:
    return any(re.match(r"(pip|python|pytest|uvicorn|flask|django)", re.sub(r"^cd [^&]+&&\s*", "", c)) for c in cmds) or \
        any(m["kind"] == "python" for m in analysis.get("modules") or [])


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────
def _static(files: Dict[str, str], analysis: Dict, tmp: str, rep) -> None:
    t0 = time.time()
    bad = []
    n = 0
    for p, c in files.items():
        if p.endswith(".json") and not adopt_files.is_binary(c) and not JSONC.search(p) and "package-lock" not in p \
                and "/.vscode/" not in "/" + p:
            n += 1
            try:
                json.loads(c.lstrip("﻿"))
            except ValueError as e:
                bad.append(f"{p}: {e}")
    if n:
        rep.add("static.json", f"JSON files are valid ({n})", "static", "failed" if bad else "passed",
                "\n".join(bad[:12]) or f"{n} files parsed", True, time.time() - t0)
    t0 = time.time()
    bad, n = [], 0
    for p, c in files.items():
        if p.endswith(".xml") and not adopt_files.is_binary(c):
            n += 1
            try:
                ET.fromstring(c.lstrip("﻿").encode("utf-8"))
            except ET.ParseError as e:
                bad.append(f"{p}: {e}")
    if n:
        rep.add("static.xml", f"XML files are well-formed ({n})", "static", "failed" if bad else "passed",
                "\n".join(bad[:12]) or f"{n} files parsed", True, time.time() - t0)
    # JavaScript syntax where no bundler/transpiler is involved
    js_roots = [m["path"] for m in analysis.get("modules") or [] if m["kind"] in
                ("ui5", "fe", "ovp", "cap-srv", "node-api", "approuter", "static")]
    node = bv._which("node")
    js = [p for p in files if p.endswith((".js", ".cjs", ".mjs")) and not p.endswith(".min.js")
          and any(p.startswith(r) for r in js_roots) and "/thirdparty/" not in p and "/lib/" not in "/" + p]
    if js and node:
        t0 = time.time()
        bad = []
        for p in js[:300]:
            if rep.remaining() < 60:
                break
            code, out = bv._run([node, "--check", os.path.join(tmp, *p.split("/"))], tmp, 30, env=child_env())
            if code not in (0,):
                if "Cannot use import statement" in out or "ERR_REQUIRE_ESM" in out:
                    continue
                bad.append(f"{p}: " + _tail_err(out, 300))
        rep.add("static.js", f"JavaScript syntax ({min(len(js), 300)} files)", "static", "failed" if bad else "passed",
                "\n".join(bad[:10]) or "no syntax errors", True, time.time() - t0)
    py = [p for p in files if p.endswith(".py") and not adopt_files.is_binary(files[p])]
    if py:
        t0 = time.time()
        bad = []
        for p in py[:600]:
            try:
                compile(files[p], p, "exec", dont_inherit=True)
            except SyntaxError as e:
                bad.append(f"{p}:{e.lineno}: {e.msg}")
        rep.add("static.py", f"Python syntax ({len(py)} files)", "static", "failed" if bad else "passed",
                "\n".join(bad[:10]) or "no syntax errors", True, time.time() - t0)


_NO_BROWSER = re.compile(r"No binary for (Chrome|ChromeHeadless)|CHROME_BIN|Cannot start Chrome|"
                         r"Could not find (Chrome|a browser)|ChromeHeadless.*(not|failed)|No usable sandbox|"
                         r"Failed to launch the browser", re.I)


def _run_commands(cmds: List[str], tmp: str, env: Dict[str, str], rep, results: Dict) -> None:
    for i, cmd in enumerate(cmds):
        low = re.sub(r"^cd [^&]+&&\s*", "", cmd).lower()
        cat = "install" if re.match(r"(npm (ci|install|i)\b|pip install|yarn( install)?$|pnpm install|python -m pip)", low) \
            else "test" if re.search(r"\b(test|pytest|jest|vitest|karma|mocha)\b", low) else "build"
        cid = f"cmd.{i + 1}"
        t0 = time.time()
        m_cd = re.match(r"\s*cd\s+([^&]+?)\s*&&", cmd)
        folder = os.path.join(tmp, *m_cd.group(1).strip().strip("/\\").split("/")) if m_cd else tmp
        if cat != "install" and re.search(r"\bnpm (run|test)\b|\bnpx\b", low) and \
                os.path.exists(os.path.join(folder, "package.json")) and not os.path.isdir(os.path.join(folder, "node_modules")):
            # the brief has no install step for this folder: install first, or every build/test fails
            lock = os.path.exists(os.path.join(folder, "package-lock.json"))
            rep.say(f"Installing dependencies in {os.path.relpath(folder, tmp)} first…")
            _shell("npm ci" if lock else "npm install", folder, env, min(600, rep.remaining() - 30))
        rep.say(f"Running: {cmd}")
        code, out = _shell(cmd, tmp, env, min(600, rep.remaining() - 30))
        if code != 0 and "pytest" in low and re.search(r"No module named pytest", out):
            rep.say("pytest is not in the requirements — installing it for the test run…")
            _shell("python -m pip install -q pytest", folder, env, 300)
            code, out = _shell(cmd, tmp, env, min(600, rep.remaining() - 30))
        if code != 0 and "heap out of memory" in out:
            rep.say("The build ran out of memory — retrying with a larger Node.js heap…")
            code, out = _shell(cmd, tmp, dict(env, NODE_OPTIONS="--max-old-space-size=3584"), min(900, rep.remaining() - 30))
        if code == 5 and "pytest" in low:                         # pytest: no tests collected
            rep.add(cid, cmd, cat, "not_tested", "pytest found no tests to run.\n" + out.strip()[-400:], False, time.time() - t0)
            results[cmd] = 0
            continue
        if code != 0 and re.match(r"(cd [^&]+&&\s*)?npm ci\b", cmd) and ("in sync" in out or "package-lock" in out
                                                                       or "EUSAGE" in out):
            rep.say("package-lock.json is out of sync — retrying with npm install…")
            code, out = _shell(cmd.replace("npm ci", "npm install", 1), tmp, env, min(600, rep.remaining() - 30))
            if code == 0:
                out = "npm ci failed (lock file out of sync); npm install succeeded.\n" + out[-600:]
        status = "passed" if code == 0 else "not_tested" if code == -9 and "budget" in out else "failed"
        detail = (test_output(out, 4000) if cat in ("test", "build") else _tail_err(out, 2500)) if code else out.strip()[-500:]
        if code and cat == "test" and _NO_BROWSER.search(out):
            status, detail = "not_tested", "These tests run in a browser (Karma / UI5 test runner); the studio runner has none.\n" + detail[-800:]
        rep.add(cid, cmd, cat, status, detail or "ok", True, time.time() - t0)
        results[cmd] = code
        if code != 0 and cat == "install":
            rep.say("Install failed — later steps in this folder may fail too.")


def _cap_smoke(analysis: Dict, tmp: str, env: Dict[str, str], rep) -> None:
    for m in [m for m in analysis.get("modules") or [] if m["kind"] == "cap-srv"]:
        root = os.path.join(tmp, *(m.get("project_root") or "").split("/"))
        t0 = time.time()
        name = f"CAP service starts and serves its endpoints ({m.get('project_root') or '/'})"
        cds_pkg = os.path.join(root, "node_modules", "@sap", "cds", "package.json")
        if not os.path.exists(cds_pkg):
            rep.add("cap.serve", name, "runtime", "not_tested", "@sap/cds is not installed (the install command did not run or failed)",
                    True, 0)
            continue
        ok, why = ensure_sqlite(root, env, rep)
        if not ok:
            rep.add("cap.serve", name, "runtime", "not_tested", why, True, time.time() - t0)
            continue
        srv = start_cap(root, env)
        try:
            up, served = srv["wait"](min(120, rep.remaining() - 20))
            if not up:
                rep.add("cap.serve", name, "runtime", "failed", _tail_err(srv["log"]()), True, time.time() - t0)
                continue
            import requests
            detail, failed = [], []
            for svc, path in served[:8]:
                url = f"http://127.0.0.1:{srv['port']}{path.rstrip('/')}/$metadata"
                try:
                    r = requests.get(url, timeout=20, auth=mock_user(root))
                    if r.status_code == 200:
                        detail.append(f"{svc} {path}: $metadata OK")
                    elif r.status_code in (401, 403):
                        detail.append(f"{svc} {path}: needs a user with other roles ({r.status_code})")
                    else:
                        failed.append(f"{svc} {path}: HTTP {r.status_code} {r.text[:200]}")
                except Exception as e:  # noqa: BLE001
                    failed.append(f"{svc} {path}: {e}")
            rep.add("cap.serve", name, "runtime", "failed" if failed else "passed",
                    "\n".join(failed + detail) or "server started (no services listed)", True, time.time() - t0)
        finally:
            srv["stop"]()


def mock_user(root: str) -> Tuple[str, str]:
    """A mocked-auth user of the project (cds.requires.auth.users): 'admin' if defined, else the user with the
    most roles; CAP's default 'alice' when the project defines none."""
    users: Dict = {}
    for f in ("package.json", ".cdsrc.json"):
        try:
            j = json.load(open(os.path.join(root, f), encoding="utf-8"))
        except (OSError, ValueError):
            continue
        auth = ((j.get("cds", j) if f == "package.json" else j).get("requires") or {}).get("auth") or {}
        if isinstance(auth, dict) and isinstance(auth.get("users"), dict):
            users.update(auth["users"])
    if not users:
        return ("alice", "")
    name = "admin" if "admin" in users else max(users, key=lambda u: len((users[u] or {}).get("roles") or []))
    return (name, str((users[name] or {}).get("password") or ""))


def ensure_sqlite(root: str, env: Dict[str, str], rep=None) -> Tuple[bool, str]:
    nm = os.path.join(root, "node_modules")
    if os.path.exists(os.path.join(nm, "@cap-js", "sqlite")) or os.path.exists(os.path.join(nm, "sqlite3")):
        return True, ""
    if rep:
        rep.say("Adding the SQLite driver for the in-memory database…")
    code, out = _shell("npm install --no-save @cap-js/sqlite", root, env, 300)
    if code != 0:
        return False, "could not install @cap-js/sqlite: " + _tail_err(out, 400)
    return True, ""


def start_cap(root: str, env: Dict[str, str], port: Optional[int] = None) -> Dict:
    """Start `cds-serve all --in-memory` in `root`; returns {port, wait(timeout)->(up, [(svc, path)]), log(), stop(), proc}."""
    port = port or bv._free_port()
    pkg = json.load(open(os.path.join(root, "node_modules", "@sap", "cds", "package.json"), encoding="utf-8"))
    bin_rel = (pkg.get("bin") or {}).get("cds-serve") or "bin/serve.js"
    serve = os.path.join(root, "node_modules", "@sap", "cds", bin_rel)
    log = os.path.join(root, ".evoke-cap.log")
    fh = open(log, "w", encoding="utf-8")
    node = bv._which("node") or "node"
    proc = subprocess.Popen([node, serve, "all", "--in-memory", "--port", str(port)], cwd=root, stdout=fh,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=dict(env, PORT=str(port)),
                            **bv._popen_kwargs())

    def read():
        try:
            return open(log, encoding="utf-8", errors="replace").read()
        except OSError:
            return ""

    def wait(timeout):
        end = time.time() + timeout
        while time.time() < end:
            if proc.poll() is not None:
                return False, []
            text = read()
            if re.search(r"server listening on|\[cds\] - server listening", text):
                served = re.findall(r"serving (\w+)\s*\{[^}]*?(?:path|at):\s*\[?\s*'([^']+)'", text)
                return True, served
            time.sleep(0.5)
        return False, []

    def stop():
        bv._kill_tree(proc)
        try:
            fh.close()
        except Exception:  # noqa: BLE001
            pass
    return {"port": port, "wait": wait, "log": read, "stop": stop, "proc": proc}


def run_checks(files: Dict[str, str], analysis: Dict, brief: Dict, say: Callable[[str], None] = lambda m: None,
               budget: float = 1200) -> Dict:
    t_start = time.time()
    rep = bv._Report(say, t_start + budget)
    env_info = environment()
    rep.say(f"Sandbox: node {env_info['node'] or '—'} · python {env_info['python']}")
    tmp = _sandbox(files)
    try:
        rep.say("Static checks (JSON, XML, JavaScript, Python syntax)…")
        _static(files, analysis, tmp, rep)
        cmds = commands_from_brief(brief)
        try:                         # tests added after the brief was written (e.g. by the robot) run too
            import adopt_scan
            mods, _ = adopt_scan.detect_modules(files)
            cmds += [m["commands"]["test"] for m in mods if m["commands"].get("test") and m["commands"]["test"] not in cmds]
        except Exception:  # noqa: BLE001
            pass
        env = child_env()
        if _uses_python(cmds, analysis):
            env = _python_venv(tmp, env, rep)
        if not env_info["node"] and any("npm" in c or "npx" in c for c in cmds):
            for i, c in enumerate(cmds):
                rep.add(f"cmd.{i + 1}", c, "build", "not_tested", "Node.js is not available on this host", True)
        else:
            _run_commands(cmds, tmp, env, rep, {})
        if any(m["kind"] == "cap-srv" for m in analysis.get("modules") or []) and env_info["node"]:
            rep.say("Starting the CAP service on an in-memory database…")
            _cap_smoke(analysis, tmp, env, rep)
        if any(m["kind"] in ("cap-java", "java") for m in analysis.get("modules") or []) and not shutil.which("mvn"):
            rep.add("java.build", "Java build", "build", "not_tested", "Maven / JDK are not available in the studio runner", False)
    except Exception as e:  # noqa: BLE001
        rep.add("runner.error", "Runner", "runtime", "failed", f"{type(e).__name__}: {e}", True)
    finally:
        _rmtree(tmp)
    checks = rep.checks
    summary = {k: sum(1 for c in checks if c["status"] == s) for k, s in
               (("passed", "passed"), ("failed", "failed"), ("warnings", "warning"), ("not_tested", "not_tested"))}
    report = {"checks": checks, "summary": summary, "environment": env_info, "level": "adopted",
              "seconds": round(time.time() - t_start, 1)}
    report["blocking"] = bv.blocking(report)
    return report


# ─────────────────────────────────────────────────────────────────────────────
# Sessions: a persistent sandbox the robot works in (install once, run commands repeatedly)
# ─────────────────────────────────────────────────────────────────────────────
_SESSIONS: Dict[str, Dict] = {}
SESSION_IDLE = 40 * 60


def session_create(files: Dict[str, str]) -> str:
    for sid, s in list(_SESSIONS.items()):
        if time.time() - s["last"] > SESSION_IDLE:
            session_close(sid)
    sid = uuid.uuid4().hex[:16]
    _SESSIONS[sid] = {"dir": _sandbox(files, prefix="evoke-session-"), "last": time.time()}
    return sid


def session_sync(sid: str, changes: Dict[str, Optional[str]]) -> int:
    s = _SESSIONS.get(sid)
    if not s:
        raise KeyError("session expired")
    s["last"] = time.time()
    for p, c in changes.items():
        if ".." in p.split("/"):
            continue
        fp = os.path.join(s["dir"], *p.split("/"))
        if c is None:
            try:
                os.remove(fp)
            except OSError:
                pass
        else:
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, "wb") as f:
                f.write(adopt_files.decode(c))
    return len(changes)


def session_exec(sid: str, cmd: str, timeout: float = 300) -> Dict:
    s = _SESSIONS.get(sid)
    if not s:
        raise KeyError("session expired")
    s["last"] = time.time()
    env = child_env()
    if os.path.isdir(os.path.join(s["dir"], ".venv")) or re.match(r"(cd [^&]+&&\s*)?(pip|python|pytest)", cmd):
        env = _python_venv(s["dir"], env)
    t0 = time.time()
    code, out = _shell(cmd, s["dir"], env, max(5, min(float(timeout or 300), 900)))
    s["last"] = time.time()
    return {"code": code, "output": out if len(out) <= 14000 else out[:3000] + "\n…\n" + test_output(out, 10000),
            "seconds": round(time.time() - t0, 1)}


def session_close(sid: str) -> bool:
    s = _SESSIONS.pop(sid, None)
    if s:
        threading.Thread(target=_rmtree, args=(s["dir"],), daemon=True).start()
    return bool(s)


# ─────────────────────────────────────────────────────────────────────────────
# Previews
# ─────────────────────────────────────────────────────────────────────────────
_PREVIEWS: Dict[str, Dict] = {}
_PLOCK = threading.Lock()


_CDN_OK: Dict[str, bool] = {}


def _ui5_cdn(version: str = "") -> str:
    """The SAPUI5 CDN bootstrap for `version` — or the current release when the CDN no longer serves that
    version (out-of-maintenance releases are removed)."""
    latest = "https://ui5.sap.com/resources/sap-ui-core.js"
    if not re.match(r"^\d+\.\d+\.\d+$", version or ""):
        return latest
    url = f"https://ui5.sap.com/{version}/resources/sap-ui-core.js"
    if version not in _CDN_OK:
        try:
            import requests
            _CDN_OK[version] = requests.head(url, timeout=8, allow_redirects=True).status_code == 200
        except Exception:  # noqa: BLE001
            _CDN_OK[version] = False
    return url if _CDN_OK[version] else latest


def _fix_ui5_app(app_dir: str, manifest_path: str, version: str, relative_to_root: bool) -> str:
    """Make a UI5 app servable in the sandbox: bootstrap from the CDN when index.html loads UI5 from a
    path that only the UI5 tooling serves; data source URIs that rely on approuter routes made absolute
    (CAP serves its services at the root). Returns the entry file relative to app_dir."""
    webapp = os.path.dirname(manifest_path)
    try:
        man = json.load(open(manifest_path, encoding="utf-8"))
    except (OSError, ValueError):
        man = {}
    if relative_to_root:
        changed = False
        for ds in ((man.get("sap.app") or {}).get("dataSources") or {}).values():
            uri = ds.get("uri") or ""
            if uri and not uri.startswith(("/", "http")) and ds.get("type", "OData") == "OData":
                ds["uri"] = "/" + uri
                changed = True
        if changed:
            json.dump(man, open(manifest_path, "w", encoding="utf-8"), indent=2)
    idx = os.path.join(webapp, "index.html")
    if os.path.exists(idx):
        html = open(idx, encoding="utf-8", errors="replace").read()
        new = re.sub(r"""src=(["'])(?:\.?\.?/)*resources/sap-ui-core\.js\1""", f'src="{_ui5_cdn(version)}"', html)
        new = re.sub(r"""https://(?:sapui5\.hana\.ondemand\.com|ui5\.sap\.com)/(\d+\.\d+\.\d+)/resources/sap-ui-core\.js""",
                     lambda mm: _ui5_cdn(mm.group(1)), new)
        # a component container without a height renders Fiori Elements pages 0px high
        new = re.sub(r"<div(?![^>]*data-height)([^>]*\bdata-sap-ui-component\b)", r'<div data-height="100%"\1', new)
        if new != html:
            open(idx, "w", encoding="utf-8").write(new)
    else:
        app_id = (man.get("sap.app") or {}).get("id") or "app"
        open(idx, "w", encoding="utf-8").write(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{app_id}</title>
<style>html, body, body > div {{ height: 100%; margin: 0; }}</style>
<script id="sap-ui-bootstrap" src="{_ui5_cdn(version)}" data-sap-ui-theme="sap_horizon"
 data-sap-ui-resource-roots='{{"{app_id}": "./"}}' data-sap-ui-on-init="module:sap/ui/core/ComponentSupport"
 data-sap-ui-compat-version="edge" data-sap-ui-async="true" data-sap-ui-frame-options="allow"></script>
</head><body class="sapUiBody sapUiSizeCompact"><div data-sap-ui-component data-name="{app_id}" data-id="container" data-height="100%"
 data-settings='{{"id": "{app_id}"}}' style="height:100%"></div></body></html>""")
    return os.path.relpath(idx, app_dir).replace(os.sep, "/")


class _StaticHandler(http.server.SimpleHTTPRequestHandler):
    """Serves a built single-page app. `aliases` map URL prefixes the page uses (e.g. a Vite base '/static/')
    onto the build folder; a request the build cannot answer goes to the backend (`proxy_to`) when one runs —
    /api/* always — and page navigations fall back to index.html."""
    root = "."
    proxy_to = ""
    aliases: Tuple[str, ...] = ()

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=self.root, **kw)

    def log_message(self, *a):        # quiet
        pass

    def _local(self, url_path: str) -> Optional[str]:
        """The file for url_path (with base-path aliases), or None."""
        clean = url_path.split("?")[0].split("#")[0]
        cands = [clean] + [("/" + clean[len(a):].lstrip("/")) for a in self.aliases if clean.startswith(a)]
        for c in cands:
            fp = self.translate_path(c)
            if os.path.isfile(fp) or (os.path.isdir(fp) and os.path.isfile(os.path.join(fp, "index.html"))):
                return c
        return None

    def _route(self) -> bool:
        """True when the request was answered by the backend proxy."""
        path = self.path
        if self.proxy_to and (path == "/api" or path.startswith(("/api/", "/api?"))):
            return self._proxy()
        local = self._local(path)
        if local is not None:
            self.path = local + (("?" + path.split("?", 1)[1]) if "?" in path else "")
            return False
        is_page = "." not in os.path.basename(path.split("?")[0]) and "text/html" in (self.headers.get("Accept") or "text/html")
        if self.proxy_to and not is_page:
            return self._proxy()                    # e.g. files the backend serves itself
        if is_page:
            self.path = "/index.html"               # single-page app navigation
        return False

    def _proxy(self) -> bool:
        import urllib.error
        import urllib.request
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else None
        req = urllib.request.Request(self.proxy_to + self.path, data=body, method=self.command,
                                     headers={k: v for k, v in self.headers.items()
                                              if k.lower() not in ("host", "connection", "content-length")})
        try:
            r = urllib.request.urlopen(req, timeout=120)
            status, headers, data = r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            status, headers, data = e.code, e.headers, e.read()
        except Exception as e:  # noqa: BLE001
            status, headers, data = 502, {}, f"API not reachable: {e}".encode()
        self.send_response(status)
        for k, v in (headers.items() if headers else []):
            if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        return True

    def do_GET(self):
        if not self._route():
            super().do_GET()

    def do_HEAD(self):
        if not self._route():
            super().do_HEAD()

    def do_POST(self):
        if not (self.proxy_to and self._proxy()):
            self.send_error(405)

    do_PUT = do_PATCH = do_DELETE = do_POST


_ASSET_RX = re.compile(r"""<(?:script|link)\b[^>]*?\b(?:src|href)\s*=\s*["']([^"'#]+)["']""", re.I)


def _page_assets(html: str) -> List[str]:
    return [u for u in _ASSET_RX.findall(html or "")
            if not u.startswith(("http:", "https:", "//", "data:")) and not u.endswith((".ico", ".png", ".svg", ".webmanifest", ".json"))]


def _base_aliases(out_dir: str) -> Tuple[str, ...]:
    """URL prefixes the built index.html uses that are not folders of the build (a configured base path)."""
    try:
        html = open(os.path.join(out_dir, "index.html"), encoding="utf-8", errors="replace").read()
    except OSError:
        return ()
    aliases = []
    for u in _page_assets(html):
        if not u.startswith("/"):
            continue
        u = u.split("?")[0]
        if os.path.isfile(os.path.join(out_dir, *u.strip("/").split("/"))):
            continue
        parts = u.strip("/").split("/")
        for i in range(1, len(parts)):
            if os.path.isfile(os.path.join(out_dir, *parts[i:])):
                aliases.append("/" + "/".join(parts[:i]) + "/")
                break
    return tuple(sorted(set(aliases), key=len, reverse=True))


_BASENAME_RX = re.compile(r"""basename\s*(?:=\s*\{?\s*|:\s*)["'`](/[^"'`$]*)["'`]""")


def _router_basename(front_root: str, out_dir: str = "") -> str:
    """The path a single-page app's router expects to live under (React Router / Vue Router basename,
    Angular <base href>), from the source or — when it comes from an environment variable — the built bundle."""
    hits: List[str] = []

    def scan(base: str, exts: Tuple[str, ...], limit: int = 400):
        n = 0
        for r, dirs, names in os.walk(base):
            dirs[:] = [d for d in dirs if d not in _SKIP_WALK and d not in ("dist", "build", "out", "coverage")]
            for f in names:
                if f.endswith(exts) and n < limit:
                    n += 1
                    try:
                        hits.extend(_BASENAME_RX.findall(open(os.path.join(r, f), encoding="utf-8", errors="replace").read()))
                    except OSError:
                        pass
    src = os.path.join(front_root, "src")
    scan(src if os.path.isdir(src) else front_root, (".tsx", ".ts", ".jsx", ".js", ".vue"))
    if not hits and out_dir:
        for r, _, names in os.walk(out_dir):
            for f in names:
                if f.endswith((".js", ".mjs")):
                    try:
                        hits.extend(_BASENAME_RX.findall(open(os.path.join(r, f), encoding="utf-8", errors="replace").read()))
                    except OSError:
                        pass
    hits = [h.rstrip("/") for h in hits if h not in ("/", "")]
    if hits:
        return max(set(hits), key=hits.count) + "/"
    try:                                                   # Angular: <base href="/x/"> in the built page
        html = open(os.path.join(out_dir or front_root, "index.html"), encoding="utf-8", errors="replace").read()
        m = re.search(r"""<base\s+href=["'](/[^"']+)["']""", html)
        if m and m.group(1) != "/":
            return m.group(1)
    except OSError:
        pass
    return ""


def _static_server(root: str, proxy_to: str = "") -> Tuple[int, Callable[[], None]]:
    handler = type("H", (_StaticHandler,), {"root": root, "proxy_to": proxy_to, "aliases": _base_aliases(root)})
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1], lambda: (srv.shutdown(), srv.server_close())


def _self_check(port: int, entry: str, auth=None) -> List[str]:
    """Load the preview's own entry page and every script / stylesheet it references; return problems
    (a page whose assets 404 renders blank)."""
    import requests
    from urllib.parse import urljoin
    base = f"http://127.0.0.1:{port}"
    try:
        r = requests.get(base + entry, timeout=60, auth=auth, headers={"Accept": "text/html"})
    except Exception as e:  # noqa: BLE001
        return [f"The entry page {entry} did not answer: {e}"]
    if r.status_code >= 400:
        return [f"The entry page {entry} returned HTTP {r.status_code}."]
    problems = []
    if "<script" not in r.text.lower() and len(r.text.strip()) < 200:
        problems.append(f"The entry page {entry} has no content ({len(r.text)} bytes).")
    for u in _page_assets(r.text)[:30]:
        url = urljoin(base + entry, u)
        try:
            a = requests.get(url, timeout=60, auth=auth)
            ctype = a.headers.get("content-type", "")
            if a.status_code >= 400:
                problems.append(f"{u} → HTTP {a.status_code}")
            elif u.split("?")[0].endswith((".js", ".mjs")) and "html" in ctype:
                problems.append(f"{u} → returned an HTML page instead of JavaScript (wrong base path?)")
        except Exception as e:  # noqa: BLE001
            problems.append(f"{u} → {e}")
    return problems


def _web_build(mod: Dict, tmp: str, env: Dict[str, str], say) -> Tuple[str, str]:
    """Install + build a React / Angular / Vue / static module; returns (output dir, error)."""
    root = os.path.join(tmp, *mod["path"].split("/")) if mod["path"] else tmp
    if mod["kind"] == "static":
        return root, ""
    lock = os.path.exists(os.path.join(root, "package-lock.json"))
    say(f"Installing {mod['name']} ({'npm ci' if lock else 'npm install'})…")
    code, out = _shell("npm ci" if lock else "npm install", root, env, 900)
    if code != 0 and lock:
        code, out = _shell("npm install", root, env, 900)
    if code != 0:
        return "", "install failed:\n" + _tail_err(out)
    say(f"Building {mod['name']}…")
    pkg = json.load(open(os.path.join(root, "package.json"), encoding="utf-8"))
    if "next" in (pkg.get("dependencies") or {}):
        return "", "Next.js needs its own server; use the run command preview instead."
    t_build = time.time() - 2
    code, out = _shell("npm run build" if (pkg.get("scripts") or {}).get("build") else "npx vite build", root, env, 900)
    if code != 0:
        return "", "build failed:\n" + _tail_err(out)
    found = _find_build_output(root, tmp, pkg, t_build)
    if not found:
        return "", "NO_OUTPUT"
    return found, ""


_SKIP_WALK = {"node_modules", ".git", ".venv", "venv", "__pycache__", ".angular", ".cache"}


def _index_dirs(base: str) -> List[str]:
    out = []
    for r, dirs, names in os.walk(base):
        dirs[:] = [d for d in dirs if d not in _SKIP_WALK]
        if "index.html" in names:
            out.append(r)
    return out


def _configured_out_dirs(root: str, pkg: Dict) -> List[str]:
    """Output folders the project configures: Vite outDir, Angular outputPath, Vue outputDir, CRA BUILD_PATH,
    --outDir / --out-dir in the build script."""
    dirs: List[str] = []
    script = (pkg.get("scripts") or {}).get("build", "")
    for m in re.finditer(r"--out-?[dD]ir[= ]+([^\s&;]+)|BUILD_PATH=([^\s&;]+)", script):
        dirs.append(m.group(1) or m.group(2))
    for name in ("vite.config.ts", "vite.config.js", "vite.config.mjs", "vite.config.mts", "vite.config.cjs", "vue.config.js"):
        try:
            text = open(os.path.join(root, name), encoding="utf-8").read()
        except OSError:
            continue
        for m in re.finditer(r"(?:outDir|outputDir)\s*:\s*['\"`]([^'\"`]+)", text):
            dirs.append(m.group(1))
        for m in re.finditer(r"(?:outDir|outputDir)\s*:\s*(?:path\.)?(?:resolve|join)\([^)]*?['\"`]([^'\"`]+)['\"`]\s*\)", text):
            dirs.append(m.group(1))
    try:
        ang = json.load(open(os.path.join(root, "angular.json"), encoding="utf-8"))
        for proj in (ang.get("projects") or {}).values():
            op = (((proj.get("architect") or {}).get("build") or {}).get("options") or {}).get("outputPath")
            op = op.get("base") if isinstance(op, dict) else op
            if op:
                dirs.append(op)
    except (OSError, ValueError):
        pass
    return [os.path.normpath(os.path.join(root, d)) for d in dirs if d]


def _find_build_output(root: str, tmp: str, pkg: Dict, since: float) -> str:
    """Folder with the built index.html, wherever the build wrote it (also outside the frontend folder,
    e.g. a backend's static/ folder)."""
    def best(dirs: List[str]) -> str:
        dirs = [d for d in dirs if os.path.exists(os.path.join(d, "index.html"))]
        dirs.sort(key=lambda r: (0 if r.endswith("browser") else 1, len(r)))
        return dirs[0] if dirs else ""
    for d in _configured_out_dirs(root, pkg):                  # 1. what the project configures
        if os.path.isdir(d) and os.path.realpath(d).startswith(os.path.realpath(tmp)):
            hit = best(_index_dirs(d))
            if hit:
                return hit
    fresh = [d for d in _index_dirs(tmp)                         # 2. what this build just wrote
             if os.path.getmtime(os.path.join(d, "index.html")) >= since]
    hit = best(fresh)
    if hit:
        return hit
    conv = []                                                    # 3. the usual folders
    for base in ("dist", "build", "out", "www"):
        d = os.path.join(root, base)
        if os.path.isdir(d):
            conv += _index_dirs(d)
    return best(conv)


def _dev_server_cmd(root: str, pkg: Dict, port: int) -> str:
    """Start the frontend's own dev server on `port` (fallback when a build produces no page)."""
    scripts = pkg.get("scripts") or {}
    deps = dict(pkg.get("dependencies") or {}, **(pkg.get("devDependencies") or {}))
    if "vite" in deps:
        return f"npx vite --host 127.0.0.1 --port {port} --strictPort"
    if "@angular/core" in deps:
        return f"npx ng serve --host 127.0.0.1 --port {port}"
    if "react-scripts" in deps:
        return "npm start"
    if scripts.get("dev"):
        return f"npm run dev -- --port {port}"
    if scripts.get("start"):
        return "npm start"
    return ""


RUNNABLE = ("node-api", "python", "cap-srv", "java")     # never the approuter / HTML5 deployer: they need BTP


def _is_gateway_cmd(cmd: str, mods: List[Dict]) -> bool:
    """A run command that would start an application router or HTML5 deployer."""
    if re.search(r"approuter|html5-app-deployer", cmd, re.I):
        return True
    m = re.match(r"\s*cd\s+([^&]+?)\s*&&", cmd)
    folder = (m.group(1).strip().strip("/\\") + "/") if m else ""
    return any(x["kind"] in ("approuter", "html5-deployer") and x["path"] == folder for x in mods)


def _service_installs(brief: Dict, mods: List[Dict], entry_mod: Optional[Dict]) -> List[str]:
    """Install commands for the services a preview starts: the brief's, else each backend module's own."""
    skip = f"cd {entry_mod['path'].rstrip('/')} " if entry_mod and entry_mod["path"] else None
    cmds = [c for c in commands_from_brief(brief) if re.search(r"\b(npm (ci|install)|pip install)\b", c)
            and not (skip and c.startswith(skip))]
    return cmds or [m["commands"]["install"] for m in mods
                    if m["kind"] in ("node-api", "python") and m["commands"].get("install")
                    and not (entry_mod and m["id"] == entry_mod["id"])]


def start_preview(files: Dict[str, str], analysis: Dict, brief: Dict, say: Callable[[str], None] = lambda m: None) -> Dict:
    """Start the project in a sandbox; returns {preview_id, port, entry, kind, mock_auth}."""
    _reap()
    mods = analysis.get("modules") or []
    mode = (brief or {}).get("preview_data") or ""
    entry_mod = next((m for m in mods if m["id"] == (brief or {}).get("entry")), None) or \
        next((m for m in mods if m["role"] == "ui"), None)
    cap = next((m for m in mods if m["kind"] == "cap-srv"), None)
    if cap and mode in ("run-services", "web-build", "mock"):
        mode = "cap-sqlite"                    # CAP serves its own UIs; an approuter needs BTP destinations
    if not mode:
        mode = "cap-sqlite" if cap else "mock" if entry_mod and entry_mod["kind"] in ("ui5", "fe", "ovp") else \
            "web-build" if entry_mod else "run-services"
    pid = uuid.uuid4().hex[:16]
    tmp = _sandbox(files, prefix="evoke-preview-")
    env = child_env()
    stops: List[Callable[[], None]] = []
    info = {"preview_id": pid, "dir": tmp, "kind": mode, "entry": "/", "port": 0, "mock_auth": None,
            "started": time.time(), "last": time.time(), "stops": stops, "log": ""}
    try:
        if mode == "cap-sqlite" and cap:
            root = os.path.join(tmp, *(cap.get("project_root") or "").split("/"))
            lock = os.path.exists(os.path.join(root, "package-lock.json"))
            say("Installing the CAP project (" + ("npm ci" if lock else "npm install") + ")…")
            code, out = _shell("npm ci" if lock else "npm install", root, env, 900)
            if code != 0 and lock:
                code, out = _shell("npm install", root, env, 900)
            if code != 0:
                raise RuntimeError("npm install failed:\n" + _tail_err(out))
            ok, why = ensure_sqlite(root, env)
            if not ok:
                raise RuntimeError(why)
            ui = [m for m in mods if m["role"] == "ui" and m.get("manifest")]
            for m in ui:
                mp = os.path.join(tmp, *m["manifest"].split("/"))
                e = _fix_ui5_app(os.path.join(tmp, *m["path"].split("/")), mp, m.get("ui5_version", ""), True)
                if entry_mod and m["id"] == entry_mod["id"]:
                    rel_app = os.path.relpath(os.path.join(tmp, *m["path"].split("/")),
                                              os.path.join(root, "app")).replace(os.sep, "/")
                    info["entry"] = "/" + ("" if rel_app == "." else rel_app.strip("/") + "/") + e
            say("Starting the CAP service on SQLite (in memory)…")
            srv = start_cap(root, env)
            stops.append(srv["stop"])
            up, served = srv["wait"](150)
            info["log"] = srv["log"]()
            if not up:
                raise RuntimeError("The CAP service did not start:\n" + _tail_err(srv["log"]()))
            info.update(port=srv["port"], mock_auth=mock_user(root), log_fn=srv["log"],
                        services=[{"name": s, "path": p} for s, p in served])
            if not entry_mod:
                info["entry"] = "/"
        elif mode == "mock" and entry_mod and entry_mod.get("manifest"):
            app_dir = os.path.join(tmp, *entry_mod["path"].split("/")) if entry_mod["path"] else tmp
            mp = os.path.join(tmp, *entry_mod["manifest"].split("/"))
            entry = _fix_ui5_app(app_dir, mp, entry_mod.get("ui5_version", ""), False)
            rep = bv._Report(say, time.time() + 900)
            nm, why = bv._tool_cache({d: ("^4.0.0" if d == "@ui5/cli" else "^2.2.0") for d in bv.UI5_TOOL_DEPS}, {}, rep, "UI5")
            if not nm:
                raise RuntimeError(why)
            nm_dir = os.path.join(app_dir, "node_modules")
            _rmtree(nm_dir) if os.path.isdir(nm_dir) and not os.path.islink(nm_dir) else None
            bv._link_dir(nm, nm_dir)
            with open(os.path.join(app_dir, "package.json"), "w", encoding="utf-8") as f:
                json.dump({"name": "evoke-preview", "version": "0.0.1", "private": True,
                           "devDependencies": {d: "*" for d in bv.UI5_TOOL_DEPS}}, f)
            webapp_rel = os.path.relpath(os.path.dirname(mp), app_dir).replace(os.sep, "/")
            services = []
            for ds in (entry_mod.get("data_sources") or {}).values():
                local = ds.get("local") or "localService/metadata.xml"
                if not os.path.exists(os.path.join(app_dir, webapp_rel, *local.split("/"))):
                    continue
                url = ds["uri"] if ds["uri"].startswith("/") else "/" + ds["uri"]
                services.append(f"""        - urlPath: {url.rstrip('/')}
          metadataPath: ./{webapp_rel}/{local}
          mockdataPath: ./{webapp_rel}/{posixpath_dir(local)}data
          generateMockData: true""")
            yaml_text = f"""specVersion: "3.0"
metadata:
  name: {re.sub(r'[^a-z0-9.-]', '-', (entry_mod.get('app_id') or 'evoke.preview').lower())}
type: application
resources:
  configuration:
    paths:
      webapp: {webapp_rel}
server:
  customMiddleware:
    - name: sap-fe-mockserver
      beforeMiddleware: csp
      configuration:
        mountPath: /
        services:
{chr(10).join(services) if services else '          []'}
"""
            open(os.path.join(app_dir, "ui5-evoke-preview.yaml"), "w", encoding="utf-8").write(yaml_text)
            port = bv._free_port()
            log = os.path.join(app_dir, ".evoke-ui5.log")
            fh = open(log, "w", encoding="utf-8")
            cli = os.path.join(nm, "@ui5", "cli", "bin", "ui5.cjs")
            say("Starting the UI5 server with mock data…")
            proc = subprocess.Popen([bv._which("node") or "node", cli, "serve", "--config", "ui5-evoke-preview.yaml",
                                     "--port", str(port)], cwd=app_dir, stdout=fh, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, env=env, **bv._popen_kwargs())
            stops.append(lambda: (bv._kill_tree(proc), fh.close()))
            # ui5 serve maps the webapp folder to the server root
            url_entry = "/" + (entry[len(webapp_rel) + 1:] if entry.startswith(webapp_rel + "/") else entry)
            up, code = bv._wait_http(f"http://127.0.0.1:{port}{url_entry}", proc, 120)
            info["log_fn"] = lambda: open(log, encoding="utf-8", errors="replace").read()
            if not up:
                raise RuntimeError("The UI5 server did not start:\n" + _tail_err(info["log_fn"]()))
            info.update(port=port, entry=url_entry)
        elif mode == "web-build" and entry_mod:
            out_dir, err = _web_build(entry_mod, tmp, env, say)
            dev = None
            if err == "NO_OUTPUT":
                froot = os.path.join(tmp, *entry_mod["path"].split("/")) if entry_mod["path"] else tmp
                fpkg = json.load(open(os.path.join(froot, "package.json"), encoding="utf-8"))
                dport = bv._free_port()
                dcmd = _dev_server_cmd(froot, fpkg, dport)
                if not dcmd:
                    raise RuntimeError("The build finished but wrote no index.html, and the frontend has no dev server "
                                       "script to start instead. Set the build output folder (e.g. Vite build.outDir).")
                say(f"The build wrote no page — starting the frontend dev server instead: {dcmd}")
                dlog = os.path.join(tmp, ".evoke-dev.log")
                dfh = open(dlog, "w", encoding="utf-8")
                dproc = subprocess.Popen(dcmd, cwd=froot, shell=True, stdout=dfh, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, env=dict(env, PORT=str(dport), HOST="127.0.0.1", BROWSER="none"),
                                         **bv._popen_kwargs())
                stops.append(lambda: (bv._kill_tree(dproc), dfh.close()))
                up, _ = bv._wait_http(f"http://127.0.0.1:{dport}/", dproc, 180)
                if not up:
                    raise RuntimeError("The frontend dev server did not start:\n" + _tail_err(open(dlog, encoding="utf-8", errors="replace").read()))
                dev = dport
                vbase = ""
                for vf in ("vite.config.ts", "vite.config.js", "vite.config.mjs", "vite.config.mts"):
                    try:
                        mb = re.search(r"\bbase\s*:\s*['\"`]([^'\"`]+)", open(os.path.join(froot, vf), encoding="utf-8").read())
                    except OSError:
                        continue
                    if mb and mb.group(1).startswith("/"):
                        vbase = mb.group(1)
                        break
            elif err:
                raise RuntimeError(err)
            api = ""
            run = [c for c in commands_from_brief(brief, "run") if not _is_gateway_cmd(c, mods)] or                 [m["commands"]["run"] for m in mods if m["kind"] in ("node-api", "python") and m["commands"].get("run")]
            if run:                                   # full stack: the API behind /api
                api_port = bv._free_port()
                svc_env = env
                if _uses_python(run, analysis):
                    svc_env = _python_venv(tmp, env)
                for c in _service_installs(brief, mods, entry_mod):
                    say(f"Running: {c}")
                    code, out = _shell(c, tmp, svc_env, 900)
                    if code != 0:
                        say("⚠ Install failed: " + _tail_err(out)[-300:])
                cmd = run[0].replace("$PORT", str(api_port)).replace("${PORT}", str(api_port))
                log = os.path.join(tmp, ".evoke-api.log")
                fh = open(log, "w", encoding="utf-8")
                say(f"Starting the API: {cmd}")
                proc = subprocess.Popen(cmd, cwd=tmp, shell=True, stdout=fh, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, env=dict(svc_env, PORT=str(api_port), HOST="127.0.0.1"),
                                        **bv._popen_kwargs())
                stops.append(lambda: (bv._kill_tree(proc), fh.close()))
                info["log_fn"] = lambda: open(log, encoding="utf-8", errors="replace").read()
                up, _ = bv._wait_http(f"http://127.0.0.1:{api_port}/", proc, 90)
                if up:
                    api = f"http://127.0.0.1:{api_port}"
                    info["services"] = [{"name": "API", "path": "/api"}]
                else:
                    why = _tail_err(info["log_fn"]())[-400:].strip()
                    info["api_problem"] = f"The backend ({cmd}) did not start, so calls to /api will fail" + \
                        (f": {why}" if why else ".") + " Fix the start command under 'How is the service started?' in the brief."
                    say("⚠ " + info["api_problem"])
            elif any(m["kind"] in ("node-api", "python") for m in mods):
                info["api_problem"] = "The project has a backend but no start command was found, so calls to /api will fail. " \
                    "Add it under 'How is the service started?' in the brief."
                say("⚠ " + info["api_problem"])
            if dev:                                    # the dev server serves the app (and proxies /api itself)
                info.update(port=dev, entry=_router_basename(os.path.join(tmp, *entry_mod["path"].split("/")) if entry_mod["path"] else tmp)
                            or vbase or "/", kind="dev-server")
            else:
                port, stop = _static_server(out_dir, api)
                stops.append(stop)
                aliases = _base_aliases(out_dir)
                froot = os.path.join(tmp, *entry_mod["path"].split("/")) if entry_mod["path"] else tmp
                router_base = _router_basename(froot, out_dir)   # where the app's router expects to run
                info.update(port=port, entry=router_base or (aliases[-1] if aliases else "/"))
        else:
            run = [c for c in commands_from_brief(brief, "run") if not _is_gateway_cmd(c, mods)] or \
                [m["commands"]["run"] for m in mods if m["kind"] in RUNNABLE and m["commands"].get("run")]
            if not run:
                ui = entry_mod if entry_mod and entry_mod["kind"] in ("static", "ui5", "fe", "ovp") else None
                pages = sorted((p for p in files if p.endswith("index.html") and "node_modules/" not in p), key=len)
                if ui or pages:                        # nothing to run: serve the UI files as they are
                    root = (os.path.join(tmp, *ui["path"].split("/")) if ui["path"] else tmp) if ui else \
                        os.path.join(tmp, *os.path.dirname(pages[0]).split("/")) if os.path.dirname(pages[0]) else tmp
                    webapp = os.path.join(root, "webapp")
                    serve_dir = webapp if os.path.isdir(webapp) else root
                    if ui and ui.get("manifest"):
                        _fix_ui5_app(root, os.path.join(tmp, *ui["manifest"].split("/")), ui.get("ui5_version", ""), False)
                    say("No service to start — serving the UI files (backend calls will not answer).")
                    port, stop = _static_server(serve_dir)
                    stops.append(stop)
                    info.update(port=port, entry="/", kind="static")
                    with _PLOCK:
                        _PREVIEWS[pid] = info
                    return {k: info[k] for k in ("preview_id", "port", "entry", "kind")} | {"services": []}
                src = analysis.get("source") or {}
                dest = ", ".join(f"{x['destination']} → {x['url']}" for x in src.get("external") or [])
                if any(m["kind"] == "approuter" for m in mods):
                    raise RuntimeError(
                        "This project is only an application router: its screens and services come from the application "
                        "behind it" + (f" ({dest})" if dest else "") + ". Adopt that application (from Cloud Foundry pick the "
                        "router again — its backend is now adopted with it — or adopt the backend app / Git repository)"
                        + (f". The deployed app is at {src['url']}" if src.get("url") else "") + ".")
                raise RuntimeError("Nothing in this project can be started for a preview. Add the service start command "
                                   "under 'How is the service started?' in the project brief.")
            if _uses_python(run, analysis):
                env = _python_venv(tmp, env)
            for c in _service_installs(brief, mods, None):
                say(f"Running: {c}")
                _shell(c, tmp, env, 900)
            port = bv._free_port()
            cmd = run[0].replace("$PORT", str(port)).replace("${PORT}", str(port))
            log = os.path.join(tmp, ".evoke-run.log")
            fh = open(log, "w", encoding="utf-8")
            say(f"Starting: {cmd}")
            proc = subprocess.Popen(cmd, cwd=tmp, shell=True, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    env=dict(env, PORT=str(port), HOST="127.0.0.1"), **bv._popen_kwargs())
            stops.append(lambda: (bv._kill_tree(proc), fh.close()))
            info["log_fn"] = lambda: open(log, encoding="utf-8", errors="replace").read()
            up, _ = bv._wait_http(f"http://127.0.0.1:{port}/", proc, 150)
            if not up:
                raise RuntimeError("The service did not answer on its port:\n" + _tail_err(info["log_fn"]()))
            fastapi = any(m.get("python_framework") == "FastAPI" for m in mods)
            info.update(port=port, entry="/docs" if fastapi else "/", kind="run-services")
    except Exception:
        for s in stops:
            try:
                s()
            except Exception:  # noqa: BLE001
                pass
        _rmtree(tmp)
        raise
    say("Checking that the page and its scripts load…")
    info["warnings"] = ([info["api_problem"]] if info.get("api_problem") else []) + \
        _self_check(info["port"], info["entry"], tuple(info["mock_auth"]) if info.get("mock_auth") else None)
    for w in info["warnings"][:5]:
        say("⚠ " + w)
    with _PLOCK:
        _PREVIEWS[pid] = info
    return {k: info[k] for k in ("preview_id", "port", "entry", "kind")} | {"services": info.get("services", []),
                                                                          "warnings": info["warnings"]}


def posixpath_dir(p: str) -> str:
    d = p.rsplit("/", 1)[0] + "/" if "/" in p else ""
    return d


def get_preview(pid: str) -> Optional[Dict]:
    p = _PREVIEWS.get(pid)
    if p:
        p["last"] = time.time()
    return p


def preview_log(pid: str) -> str:
    p = _PREVIEWS.get(pid)
    if not p:
        return ""
    try:
        return (p.get("log_fn") or (lambda: p.get("log", "")))()[-20000:]
    except Exception:  # noqa: BLE001
        return p.get("log", "")


def stop_preview(pid: str) -> bool:
    with _PLOCK:
        p = _PREVIEWS.pop(pid, None)
    if not p:
        return False
    for s in p["stops"]:
        try:
            s()
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=_rmtree, args=(p["dir"],), daemon=True).start()
    return True


def _reap() -> None:
    now = time.time()
    for pid, p in list(_PREVIEWS.items()):
        if now - p["last"] > PREVIEW_IDLE:
            stop_preview(pid)
    while len(_PREVIEWS) >= MAX_PREVIEWS:
        oldest = min(_PREVIEWS.values(), key=lambda p: p["last"])
        stop_preview(oldest["preview_id"])


def list_previews() -> List[Dict]:
    return [{"preview_id": p["preview_id"], "kind": p["kind"], "entry": p["entry"], "idle": round(time.time() - p["last"])}
            for p in _PREVIEWS.values()]


# ─────────────────────────────────────────────────────────────────────────────
# Deploy builds
# ─────────────────────────────────────────────────────────────────────────────
def build_artifact(files: Dict[str, str], analysis: Dict, brief: Dict, target: str,
                   say: Callable[[str], None] = lambda m: None) -> Tuple[bytes, Dict]:
    """(artifact bytes, meta). target "mta" -> .mtar; "cf-push" -> ZIP of a pushable folder."""
    tmp = _sandbox(files, prefix="evoke-build-")
    env = child_env()
    try:
        if target == "mta":
            say("Building the MTA archive (mbt)…")
            code, out = _shell("npx -y mbt@1 build --mtar app.mtar", tmp, env, 1500)
            mtar = os.path.join(tmp, "mta_archives", "app.mtar")
            if code != 0 or not os.path.exists(mtar):
                say("mbt build was not possible here — using the studio's MTA builder (runs the before-all commands).")
                from deployer import build_mtar_python
                old = os.environ.get("PATH", "")
                os.environ["PATH"] = env["PATH"]
                try:
                    mtar = build_mtar_python(tmp)
                finally:
                    os.environ["PATH"] = old
                meta = {"builder": "studio", "mbt_log": _tail_err(out, 800)}
            else:
                meta = {"builder": "mbt"}
            with open(mtar, "rb") as f:
                return f.read(), dict(meta, kind="mtar")
        # cf push: build web modules, write a manifest when there is none
        mods = analysis.get("modules") or []
        manifest = os.path.join(tmp, "manifest.yml")
        name = re.sub(r"[^a-z0-9-]", "-", (analysis.get("name") or "app").lower()).strip("-")[:50] or "app"
        web = [m for m in mods if m["kind"] in ("react", "angular", "vue")]
        svc = [m for m in mods if m["kind"] in ("node-api", "python", "cap-srv")]
        push_dir = tmp
        if web and not svc:
            out_dir, err = _web_build(web[0], tmp, env, say)
            if err:
                raise RuntimeError(err)
            push_dir = out_dir
            open(os.path.join(push_dir, "Staticfile"), "w").write("pushstate: enabled\n")
            if not os.path.exists(os.path.join(push_dir, "manifest.yml")):
                open(os.path.join(push_dir, "manifest.yml"), "w").write(
                    f"applications:\n  - name: {name}\n    memory: 64M\n    buildpacks: [staticfile_buildpack]\n")
        elif not os.path.exists(manifest):
            m = svc[0] if svc else (mods[0] if mods else None)
            path = (m or {}).get("path", "").rstrip("/") or "."
            if m and m["kind"] == "python":
                bp, cmd = "python_buildpack", (m["commands"].get("run") or "python app.py").split("&& ")[-1]
            elif m and m["kind"] in ("ui5", "fe", "ovp", "static"):
                bp, cmd = "staticfile_buildpack", ""
            else:
                bp, cmd = "nodejs_buildpack", ""
            open(manifest, "w").write(f"applications:\n  - name: {name}\n    path: {path}\n    memory: 256M\n"
                                      f"    buildpacks: [{bp}]\n" + (f"    command: {cmd}\n" if cmd else ""))
            say("Wrote manifest.yml (" + bp + ").")
        files_out, _ = adopt_files.from_dir(push_dir)
        return adopt_files.to_zip(files_out), {"kind": "zip", "app": name}
    finally:
        _rmtree(tmp)
