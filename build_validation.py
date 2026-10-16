"""
build_validation.py — execution-based validation of a generated project.

quality_validator.py checks the generated files structurally. This module goes
further and actually RUNS the project in a throw-away sandbox:

    static    structure (quality_validator), JSON, XML, JavaScript syntax (node)
    build     MTA descriptor + approuter routes, real `ui5 build`
    backend   CAP projects (backend/): npm install, CDS compile, contract check of
              the compiled service against the metadata the UI was built on,
              in-memory server, read/create/action smoke requests
    runtime   (level "deep") the app served (mock server, or CAP serving the UI
              against the real backend) and opened in headless Chrome/Edge

Every check reports passed / failed / warning / not_tested — a check that could
not run (no node on this host, no browser, no network, time budget used up) is
reported as not_tested with the reason, never as passed. Critical failures are
returned in `blocking` so the studio can refuse to deploy.

Node tooling (@ui5/cli, the fe-mockserver, the CAP runtime) is installed once
per dependency set into a persistent tool cache
($STUDIO_DATA_DIR or ~/.evoke-studio)/toolcache/<hash>/node_modules and linked
into each sandbox, so only the first run pays for `npm install`.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from typing import Callable, Dict, List, Optional, Tuple

IS_WIN = os.name == "nt"
UI5_TOOL_DEPS = ("@ui5/cli", "@sap-ux/ui5-middleware-fe-mockserver")
# better-sqlite3 >= 12.5 ships no prebuilt binaries for older Node versions; without
# a C++ toolchain the install fails — retry with a version that has them.
SQLITE_FALLBACK = "12.4.1"
NETWORK_ERR = re.compile(r"ENOTFOUND|ETIMEDOUT|EAI_AGAIN|ECONNRESET|ECONNREFUSED|network|proxy", re.I)


# ─────────────────────────────────────────────────────────────────────────────
# Report helpers
# ─────────────────────────────────────────────────────────────────────────────
class _Report:
    def __init__(self, progress: Optional[Callable[[str], None]], deadline: float):
        self.checks: List[Dict] = []
        self.progress = progress
        self.deadline = deadline

    def say(self, msg: str) -> None:
        if self.progress:
            try:
                self.progress(msg)
            except Exception:  # noqa: BLE001 — a UI callback must never break validation
                pass

    def add(self, cid: str, name: str, category: str, status: str, detail: str = "",
            critical: bool = True, seconds: float = 0.0) -> Dict:
        c = {"id": cid, "name": name, "category": category, "status": status,
             "critical": bool(critical), "detail": (detail or "").strip()[:2000],
             "seconds": round(seconds, 1)}
        self.checks.append(c)
        return c

    def remaining(self) -> float:
        return self.deadline - time.time()

    def get(self, cid: str) -> Optional[Dict]:
        return next((c for c in self.checks if c["id"] == cid), None)


class _Abort(Exception):
    pass


def blocking(report: Dict) -> List[str]:
    """Names of critical checks that failed — deployment should be refused."""
    return [c["name"] for c in report.get("checks", []) if c["critical"] and c["status"] == "failed"]


def format_markdown(report: Dict) -> str:
    icon = {"passed": "✅", "failed": "❌", "warning": "⚠️", "not_tested": "⏭️"}
    s = report.get("summary", {})
    env = report.get("environment", {})
    lines = [f"**Validation** — {s.get('passed', 0)} passed · {s.get('failed', 0)} failed · "
             f"{s.get('warnings', 0)} warnings · {s.get('not_tested', 0)} not tested",
             f"_node {env.get('node') or 'not available'} · browser {'yes' if env.get('chrome') else 'not available'}"
             f" · level {report.get('level', '')} · {report.get('seconds', 0)}s_", ""]
    if report.get("blocking"):
        lines.append("**Deployment blocked by:** " + ", ".join(report["blocking"]))
        lines.append("")
    cat = ""
    for c in report.get("checks", []):
        if c["category"] != cat:
            cat = c["category"]
            lines.append(f"**{cat.capitalize()}**")
        crit = " _(critical)_" if c["critical"] and c["status"] == "failed" else ""
        detail = (" — " + c["detail"].splitlines()[0][:300]) if c["detail"] else ""
        lines.append(f"- {icon.get(c['status'], '-')} {c['name']}{crit}{detail}")
    if report.get("requirements"):
        lines += ["", "**Requirements**"]
        for r in report["requirements"]:
            lines.append(f"- {icon.get(r['status'], '-')} {r['feature']} — design: {r['design_status']}"
                         + (f" · {r['detail']}" if r.get("detail") else ""))
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Process / environment helpers
# ─────────────────────────────────────────────────────────────────────────────
_REAL_NODE: Dict[str, str] = {}


def _real_node() -> str:
    """The node binary itself, not a version-manager shim: shims (nvm, volta…) may pick another
    Node version per directory (e.g. from package.json engines), and native modules installed
    for one version fail to load in another."""
    shim = shutil.which("node") or ""
    if shim and shim not in _REAL_NODE:
        try:
            out = subprocess.run([shim, "-p", "process.execPath"], capture_output=True, text=True, timeout=20,
                                 cwd=os.path.expanduser("~"))
            path = out.stdout.strip()
            _REAL_NODE[shim] = path if path and os.path.exists(path) else shim
        except (OSError, subprocess.SubprocessError):
            _REAL_NODE[shim] = shim
    return _REAL_NODE.get(shim, "")


def _which(name: str) -> str:
    """node, and the npm that belongs to THAT node (several Node installs may be on PATH;
    an npm of another Node version would install native modules for the wrong ABI)."""
    node = _real_node()
    if name == "node" or not node:
        return node if name == "node" else ""
    d = os.path.dirname(node)
    for cand in ((name + ".exe", name + ".cmd") if IS_WIN else (name,)):
        if os.path.exists(os.path.join(d, cand)):
            return os.path.join(d, cand)
    return (shutil.which(name + ".cmd") if IS_WIN else "") or shutil.which(name) or ""


def _node_env() -> Dict[str, str]:
    env = os.environ.copy()
    node = _real_node()
    if node:                       # lifecycle scripts (e.g. native prebuilds) must see the same node
        env["PATH"] = os.path.dirname(node) + os.pathsep + env.get("PATH", "")
    return env


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=20)
        else:
            import signal
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def _popen_kwargs() -> Dict:
    if IS_WIN:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {"start_new_session": True}


def _run(cmd: List[str], cwd: str, timeout: float, env: Optional[Dict] = None, stdin: str = None) -> Tuple[int, str]:
    """Run a command; returns (exit code, combined output). Exit -9 = timed out (tree killed)."""
    if timeout <= 1:
        return -9, "time budget exhausted"
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                            env=env or _node_env(), **_popen_kwargs())
    try:
        out, _ = proc.communicate(input=stdin.encode("utf-8") if stdin is not None else None, timeout=timeout)
        return proc.returncode, out.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            out, _ = proc.communicate(timeout=5)
        except Exception:  # noqa: BLE001
            out = b""
        return -9, (out or b"").decode("utf-8", "replace") + f"\n[timed out after {int(timeout)}s]"


def _version(cmd: List[str]) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def _find_browser() -> str:
    for env in ("CHROME_PATH", "EDGE_PATH"):
        if os.environ.get(env) and os.path.exists(os.environ[env]):
            return os.environ[env]
    cands = []
    if IS_WIN:
        for base in (os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                     os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                     os.environ.get("LOCALAPPDATA", "")):
            cands += [os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"),
                      os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")]
    else:
        cands = [shutil.which(n) or "" for n in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge")]
    return next((c for c in cands if c and os.path.exists(c)), "")


def environment() -> Dict:
    node = _which("node")
    return {"node": _version([node, "--version"]) if node else "",
            "npm": _version([_which("npm"), "--version"]) if _which("npm") else "",
            "chrome": _find_browser(),
            "websockets": _has_module("websockets.sync.client"),
            "platform": sys.platform}


def _has_module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:  # noqa: BLE001
        return False


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_http(url: str, proc: subprocess.Popen, timeout: float, auth=None) -> Tuple[bool, int]:
    import requests
    end = time.time() + timeout
    while time.time() < end:
        if proc.poll() is not None:
            return False, 0
        try:
            r = requests.get(url, timeout=3, auth=auth)
            return True, r.status_code
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return False, 0


def _err_first(text: str) -> str:
    """Log text with its first error line moved to the top (reports show line 1)."""
    m = re.search(r"^.*\b(Error|ERR!|error:).*$", text or "", re.M)
    return (m.group(0).strip() + "\n" + text) if m else (text or "")


def _tail(path: str, n: int = 1500) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()[-n:]
    except OSError:
        return ""


def data_dir() -> str:
    d = os.environ.get("STUDIO_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".evoke-studio")
    os.makedirs(d, exist_ok=True)
    return d


def _link_dir(target: str, link: str) -> None:
    """node_modules of the tool cache -> sandbox (junction on Windows, symlink elsewhere)."""
    if os.path.lexists(link):
        return
    if IS_WIN:
        subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True, timeout=30)
        if not os.path.exists(link):
            raise OSError(f"could not link {link}")
    else:
        os.symlink(target, link, target_is_directory=True)


def _unlink_dir(link: str) -> None:
    """Remove a junction/symlink WITHOUT touching the cache it points to."""
    try:
        if IS_WIN:
            os.rmdir(link)        # removes a junction itself
        else:
            os.unlink(link)
    except OSError:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Tool cache (npm install once per dependency set)
# ─────────────────────────────────────────────────────────────────────────────
def _tool_cache(deps: Dict[str, str], extra: Dict, rep: _Report, label: str) -> Tuple[str, str]:
    """Returns (node_modules dir, "") or ("", reason). `extra` = further package.json keys."""
    npm = _which("npm")
    if not npm:
        return "", "npm is not available on this host"
    node_major = (environment()["node"] or "v0").lstrip("v").split(".")[0]
    key = hashlib.sha256(json.dumps([deps, extra, node_major], sort_keys=True).encode()).hexdigest()[:16]
    root = os.path.join(data_dir(), "toolcache", key)
    nm = os.path.join(root, "node_modules")
    if os.path.exists(os.path.join(root, ".ok")):
        return nm, ""
    lock = root + ".lock"
    t_lock = time.time()
    while True:                                   # another studio session installs the same set
        try:
            os.makedirs(lock)
            break
        except FileExistsError:
            if time.time() - t_lock > min(600, rep.remaining()) or time.time() - os.path.getmtime(lock) > 900:
                shutil.rmtree(lock, ignore_errors=True)
                continue
            if os.path.exists(os.path.join(root, ".ok")):
                return nm, ""
            time.sleep(2)
    try:
        if os.path.exists(os.path.join(root, ".ok")):
            return nm, ""
        rep.say(f"Installing {label} tooling (first run only)…")
        shutil.rmtree(root, ignore_errors=True)
        os.makedirs(root)
        pkg = dict({"name": "evoke-toolcache", "version": "1.0.0", "private": True, "dependencies": deps}, **extra)

        def install(p):
            with open(os.path.join(root, "package.json"), "w", encoding="utf-8") as f:
                json.dump(p, f, indent=1)
            return _run([npm, "install", "--no-audit", "--no-fund", "--loglevel=error"], root,
                        min(600, rep.remaining() - 5))

        code, out = install(pkg)
        if code != 0 and "better-sqlite3" in out:
            pkg["overrides"] = dict(pkg.get("overrides") or {}, **{"better-sqlite3": SQLITE_FALLBACK})
            code, out = install(pkg)
        if code != 0:
            shutil.rmtree(root, ignore_errors=True)
            return "", ("npm install failed: " + out[-1200:])
        open(os.path.join(root, ".ok"), "w").close()
        return nm, ""
    finally:
        shutil.rmtree(lock, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# Sandbox
# ─────────────────────────────────────────────────────────────────────────────
def _write_sandbox(files: Dict[str, str], tmp: str) -> None:
    if any(p.startswith("webapp/") for p in files):          # imported project: paths are final
        for p, c in files.items():
            fp = os.path.join(tmp, *p.split("/"))
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, "w", encoding="utf-8") as f:
                f.write(c)
    else:
        try:                       # the deploy layout (root files vs webapp/) — same as the real deploy
            from deployer import write_project_to_disk
        except Exception:  # noqa: BLE001
            write_project_to_disk = _write_fallback
        write_project_to_disk(files, tmp)
    # older write_project_to_disk put backend/ under webapp/ — CAP lives at the root
    stray = os.path.join(tmp, "webapp", "backend")
    if os.path.isdir(stray) and not os.path.exists(os.path.join(tmp, "backend")):
        shutil.move(stray, os.path.join(tmp, "backend"))


_ROOT_FILES = ("mta.yaml", "package.json", "ui5.yaml", "xs-app.json", "xs-security.json", ".env.example",
               ".gitignore", "destinations.stub.json", "deploy.mtaext.example", "ui5-mock.yaml", "README.md",
               "evoke-app.json")


def _write_fallback(files: Dict[str, str], base: str) -> None:
    for p, c in files.items():
        top = p in _ROOT_FILES or p.split("/")[0] in ("approuter", "backend")
        fp = os.path.join(base, *p.split("/")) if top else os.path.join(base, "webapp", *p.split("/"))
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w", encoding="utf-8") as f:
            f.write(c)


def _flat_view(files: Dict[str, str]) -> Dict[str, str]:
    """quality_validator expects the studio's flat layout (manifest.json at the top)."""
    if "manifest.json" in files or not any(p.startswith("webapp/") for p in files):
        return files
    return {p[len("webapp/"):]: c for p, c in files.items() if p.startswith("webapp/")}


def _rel_files(tmp: str, ext: Tuple[str, ...]) -> List[str]:
    out = []
    for root, dirs, names in os.walk(tmp):
        dirs[:] = [d for d in dirs if d not in ("node_modules", "dist", "gen", "mta_archives", ".git")]
        out += [os.path.relpath(os.path.join(root, n), tmp).replace("\\", "/") for n in names if n.endswith(ext)]
    return sorted(out)


# ─────────────────────────────────────────────────────────────────────────────
# Static checks
# ─────────────────────────────────────────────────────────────────────────────
_JS_CHECK = r"""
const fs = require('fs'), vm = require('vm');
const files = JSON.parse(fs.readFileSync(0, 'utf8')); const errs = [];
for (const [f, cjs] of files) {
  let src = fs.readFileSync(f, 'utf8');
  if (cjs) src = '(function (exports, require, module, __filename, __dirname) {' + src + '\n})';
  try { new vm.Script(src, { filename: f }); }
  catch (e) { const m = /:(\d+)\n/.exec(e.stack || '') ; errs.push({ file: f, line: m ? +m[1] : 0, error: String(e.message) }); }
}
process.stdout.write(JSON.stringify(errs));
"""


def _static(files: Dict[str, str], tmp: str, node: str, rep: _Report) -> None:
    import quality_validator as qv
    t0 = time.time()
    q = qv.validate_project(_flat_view(files))
    errs = [f for f in q["findings"] if f["severity"] == "error"]
    warns = [f for f in q["findings"] if f["severity"] == "warning"]
    rep.add("static.structure", "Project structure (manifest, routing, bindings, i18n, handlers)", "static",
            "failed" if errs else ("warning" if warns else "passed"),
            "\n".join(f"{f['file']}: {f['message']}" for f in (errs or warns)[:12]) or
            f"{len(q['findings'])} finding(s), none blocking", True, time.time() - t0)

    t0 = time.time()
    bad = []
    for p, c in files.items():
        if p.endswith(".json"):
            try:
                json.loads(c)
            except ValueError as e:
                bad.append(f"{p}: {e}")
    rep.add("static.json", f"JSON files parse ({sum(p.endswith('.json') for p in files)})", "static",
            "failed" if bad else "passed", "\n".join(bad[:10]), True, time.time() - t0)

    t0 = time.time()
    bad = []
    for p, c in files.items():
        if p.endswith(".xml"):
            try:
                ET.fromstring(c.encode("utf-8"))
            except ET.ParseError as e:
                bad.append(f"{p}: {e}")
    rep.add("static.xml", f"XML files well-formed ({sum(p.endswith('.xml') for p in files)})", "static",
            "failed" if bad else "passed", "\n".join(bad[:10]), True, time.time() - t0)

    t0 = time.time()
    js = _rel_files(tmp, (".js", ".cjs"))
    if not node:
        rep.add("static.js", "JavaScript syntax (node)", "static", "not_tested",
                "Node.js is not available on this host — syntax not executed-checked.", True)
        return
    if not js:
        rep.add("static.js", "JavaScript syntax (node)", "static", "passed", "no JavaScript files", True)
        return
    items = [[os.path.join(tmp, p), p.startswith("backend/") or p.endswith(".cjs")] for p in js]
    code, out = _run([node, "-e", _JS_CHECK], tmp, min(60, rep.remaining()), stdin=json.dumps(items))
    try:
        errs = json.loads(out)
    except ValueError:
        rep.add("static.js", "JavaScript syntax (node)", "static", "not_tested", f"checker failed: {out[-400:]}",
                True, time.time() - t0)
        return
    rep.add("static.js", f"JavaScript syntax (node, {len(js)} files)", "static", "failed" if errs else "passed",
            "\n".join(os.path.relpath(e["file"], tmp).replace(os.sep, "/") + f":{e['line']}: {e['error']}"
                      for e in errs[:10]),
            True, time.time() - t0)


# ─────────────────────────────────────────────────────────────────────────────
# Build checks
# ─────────────────────────────────────────────────────────────────────────────
def _mta(files: Dict[str, str], tmp: str, rep: _Report) -> Dict:
    t0 = time.time()
    text = files.get("mta.yaml")
    if not text:
        rep.add("build.mta", "MTA deployment descriptor", "build", "not_tested", "project has no mta.yaml", True)
        return {}
    import yaml
    try:
        mta = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        rep.add("build.mta", "MTA deployment descriptor", "build", "failed", f"mta.yaml is not valid YAML: {e}", True)
        return {}
    problems = []
    if not mta.get("ID"):
        problems.append("no ID")
    mods = mta.get("modules") or []
    if not mods:
        problems.append("no modules")
    names = {r.get("name") for r in mta.get("resources") or []}
    for m in mods:
        for p in m.get("provides") or []:
            names.add(p.get("name"))
    for m in mods:
        path = (m.get("path") or ".").strip()
        if not os.path.exists(os.path.join(tmp, path)):
            problems.append(f"module {m.get('name')}: path '{path}' does not exist")
        if not m.get("type"):
            problems.append(f"module {m.get('name')}: no type")
        for r in m.get("requires") or []:
            if r.get("name") not in names:
                problems.append(f"module {m.get('name')} requires '{r.get('name')}' which nothing provides")
        if m.get("type") in ("nodejs", "approuter.nodejs") and not os.path.exists(
                os.path.join(tmp, path, "package.json")):
            problems.append(f"module {m.get('name')}: no package.json in '{path}'")
    for r in mta.get("resources") or []:
        p = (r.get("parameters") or {}).get("path")
        if p and not os.path.exists(os.path.join(tmp, p)):
            problems.append(f"resource {r.get('name')}: file '{p}' does not exist")
    rep.add("build.mta", f"MTA deployment descriptor ({len(mods)} module(s), {len(mta.get('resources') or [])} resource(s))",
            "build", "failed" if problems else "passed", "\n".join(problems), True, time.time() - t0)
    return mta


def _xs_app(files: Dict[str, str], mta: Dict, rep: _Report) -> None:
    t0 = time.time()
    text = files.get("xs-app.json")
    if not text:
        rep.add("build.xsapp", "Approuter routes (xs-app.json)", "build", "not_tested", "no xs-app.json", True)
        return
    try:
        cfg = json.loads(text)
    except ValueError as e:
        rep.add("build.xsapp", "Approuter routes (xs-app.json)", "build", "failed", str(e), True)
        return
    errs, warns = [], []
    known = set()
    for m in mta.get("modules") or []:
        for r in m.get("requires") or []:
            g = (r.get("properties") or {}).get("destinations") or ""
            known.update(re.findall(r"name\s*:\s*([\w\-]+)", str(g)))
            known.update(re.findall(r'"name"\s*:\s*"([\w\-]+)"', str(g)))
        for p in m.get("provides") or []:
            known.add(p.get("name"))
    blob = files.get("mta.yaml", "") + files.get("destinations.stub.json", "") + files.get("deploy.mtaext.example", "")
    for i, r in enumerate(cfg.get("routes") or []):
        src = r.get("source")
        try:
            re.compile(src or "")
        except re.error as e:
            errs.append(f"route {i} source '{src}': {e}")
        d = r.get("destination")
        if d and d not in known and d not in blob:
            warns.append(f"route {src} → destination '{d}' is not defined in the MTA — it must already exist "
                         f"in the BTP subaccount")
        if not (d or r.get("localDir") or r.get("service")):
            errs.append(f"route {i} '{src}' has no destination, localDir or service")
    rep.add("build.xsapp", f"Approuter routes ({len(cfg.get('routes') or [])})", "build",
            "failed" if errs else ("warning" if warns else "passed"), "\n".join(errs + warns), True, time.time() - t0)


def _ui5_deps(files: Dict[str, str]) -> Dict[str, str]:
    deps = {}
    try:
        p = json.loads(files.get("package.json") or "{}")
        for k in ("dependencies", "devDependencies"):
            deps.update(p.get(k) or {})
    except ValueError:
        pass
    return {d: deps.get(d) or ("^4.0.0" if d == "@ui5/cli" else "^2.2.0") for d in UI5_TOOL_DEPS}


def _prepare_ui5(files: Dict[str, str], tmp: str, rep: _Report) -> Tuple[str, str]:
    """Links the UI5 tool cache into the sandbox and replaces package.json with one listing
    only the installed tools (the UI5 CLI resolves extensions from package.json)."""
    nm, why = _tool_cache(_ui5_deps(files), {}, rep, "UI5")
    if not nm:
        return "", why
    _link_dir(nm, os.path.join(tmp, "node_modules"))
    name = "evoke-app"
    try:
        name = json.loads(files.get("package.json") or "{}").get("name") or name
    except ValueError:
        pass
    with open(os.path.join(tmp, "package.json"), "w", encoding="utf-8") as f:
        json.dump({"name": name, "version": "0.0.1", "private": True,
                   "devDependencies": _ui5_deps(files)}, f, indent=1)
    cli = os.path.join(nm, "@ui5", "cli", "bin", "ui5.cjs")
    return (cli, "") if os.path.exists(cli) else ("", "@ui5/cli is missing in the tool cache")


def _app_id(files: Dict[str, str]) -> str:
    try:
        m = json.loads(_flat_view(files).get("manifest.json") or "{}")
        return m.get("sap.app", {}).get("id") or "evoke.app"
    except ValueError:
        return "evoke.app"


def _ui5_build(files: Dict[str, str], tmp: str, node: str, cli: str, why: str, rep: _Report) -> None:
    t0 = time.time()
    name = "UI5 build (preload, minification)"
    if not node or not cli:
        rep.add("build.ui5", name, "build", "not_tested", why or "Node.js is not available on this host", True)
        return
    # own minimal config: the project's ui5.yaml carries serve-time middleware only
    with open(os.path.join(tmp, "ui5-evoke-build.yaml"), "w", encoding="utf-8") as f:
        f.write(f'specVersion: "3.0"\nmetadata:\n  name: {_app_id(files)}\ntype: application\n')
    rep.say("Running ui5 build…")
    code, out = _run([node, cli, "build", "--config", "ui5-evoke-build.yaml", "--dest", "dist", "--clean-dest"],
                     tmp, min(240, rep.remaining()))
    ok = code == 0 and os.path.exists(os.path.join(tmp, "dist", "Component-preload.js"))
    if code == -9:
        rep.add("build.ui5", name, "build", "not_tested", "timed out — " + out[-600:], True, time.time() - t0)
        return
    detail = "dist/Component-preload.js built"
    if not ok:                     # the UI5 CLI prints the cause after its "Error" banner
        lines = [l.strip() for l in out.splitlines() if l.strip() and "Process Failed" not in l]
        hit = [i for i, l in enumerate(lines) if re.search(r"error", l, re.I)]
        detail = "\n".join(lines[hit[0]:hit[0] + 8] if hit else lines[-12:])[-1500:]
    rep.add("build.ui5", name, "build", "passed" if ok else "failed", detail, True, time.time() - t0)


# ─────────────────────────────────────────────────────────────────────────────
# CAP backend checks
# ─────────────────────────────────────────────────────────────────────────────
_CDS_COMPILE = r"""
const path = require('path');
const cds = require(require.resolve('@sap/cds', { paths: [process.cwd()] }));
(async () => {
  const csn = await cds.load('*');
  const out = [];
  for (const [xml, meta] of cds.compile.to.edmx(csn, { service: 'all' })) out.push({ service: meta.file || meta.name, edmx: xml });
  const paths = {};
  for (const s of cds.linked(csn).services) { try { paths[s.name] = cds.service.protocols.path4(s); } catch (e) { paths[s.name] = s['@path'] || ''; } }
  process.stdout.write(JSON.stringify({ services: out, paths }));
})().catch(e => { process.stderr.write(String(e.stack || e.message || e)); process.exit(1); });
"""


def _auth_user(pkg: Dict, cdsrc: Dict) -> Tuple[str, str]:
    """Mocked user for smoke requests: the configured one with most roles, else alice."""
    users: Dict = {}

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "users" and isinstance(v, dict):
                    users.update({u: d for u, d in v.items() if isinstance(d, dict) or d is True})
                else:
                    walk(v)
    walk(pkg.get("cds") or {})
    walk(cdsrc)
    if not users:
        return "alice", ""
    best = max(users, key=lambda u: len((users[u] or {}).get("roles") or []) if isinstance(users[u], dict) else 0)
    d = users[best] if isinstance(users[best], dict) else {}
    return best, str(d.get("password") or "")


def _key_predicate(es: Dict, row: Dict) -> str:
    props = {p["name"]: p for p in es["properties"]}
    parts = []
    for k in es["keys"]:
        v = row.get(k)
        t = (props.get(k) or {}).get("type", "Edm.String")
        if t == "Edm.String":
            lit = "'" + str(v).replace("'", "''") + "'"
        elif t == "Edm.Boolean":
            lit = "true" if v else "false"
        else:
            lit = str(v)
        parts.append(lit if len(es["keys"]) == 1 else f"{k}={lit}")
    return "(" + ",".join(parts) + ")"


def _dummy(p: Dict, n: int):
    t = p["type"]
    if t == "Edm.String":
        return f"Test {n}"[: p.get("max_length") or 40]
    if t in ("Edm.Int16", "Edm.Int32", "Edm.Int64", "Edm.Byte", "Edm.SByte"):
        return 1
    if t in ("Edm.Decimal",):
        return "1.00" if (p.get("scale") or 0) else "1"
    if t in ("Edm.Double", "Edm.Single"):
        return 1.0
    if t == "Edm.Boolean":
        return False
    if t == "Edm.Date":
        return _dt.date.today().isoformat()
    if t == "Edm.DateTimeOffset":
        return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if t == "Edm.TimeOfDay":
        return "12:00:00"
    if t == "Edm.Guid":
        import uuid
        return str(uuid.uuid4())
    return None


_DRAFT = {"IsActiveEntity", "HasActiveEntity", "HasDraftEntity"}


def _backend(files: Dict[str, str], tmp: str, node: str, rep: _Report, level: str, main_set: str,
             ui_model: Optional[Dict]) -> Dict:
    """CAP checks. Returns {"dir", "nm", "port"...} when a server could be started (left
    running for the runtime check — the caller stops it)."""
    import metadata_parser as mdp
    be = os.path.join(tmp, "backend")
    names = {"backend.install": "Backend dependencies (npm install)",
             "backend.compile": "CDS model compiles to OData",
             "backend.contract": "Backend service matches the metadata the UI was built on",
             "backend.serve": "Backend starts (in-memory database + sample data)",
             "backend.read": "Backend entity sets readable",
             "backend.create": "Create a record via the backend",
             "backend.action": "Workflow action round-trip"}

    def skip_rest(after: str, why: str):
        hit = False
        for cid, nm in names.items():
            if hit and not rep.get(cid):
                rep.add(cid, nm, "backend", "not_tested", why, cid not in ("backend.create", "backend.action"))
            hit = hit or cid == after

    if not node:
        for cid, nm in names.items():
            rep.add(cid, nm, "backend", "not_tested", "Node.js is not available on this host",
                    cid not in ("backend.create", "backend.action"))
        return {}
    t0 = time.time()
    try:
        pkg = json.loads(files.get("backend/package.json") or "{}")
    except ValueError as e:
        rep.add("backend.install", names["backend.install"], "backend", "failed", f"backend/package.json: {e}")
        skip_rest("backend.install", "backend/package.json is invalid")
        return {}
    try:
        cdsrc = json.loads(files.get("backend/.cdsrc.json") or "{}")
    except ValueError:
        cdsrc = {}
    deps = dict(pkg.get("dependencies") or {})
    deps.update({k: v for k, v in (pkg.get("devDependencies") or {}).items() if not k.startswith("@sap/cds-dk")})
    if "@sap/cds" not in deps:
        rep.add("backend.install", names["backend.install"], "backend", "failed",
                "backend/package.json does not depend on @sap/cds")
        skip_rest("backend.install", "no CAP runtime")
        return {}
    if not any(d in deps for d in ("@cap-js/sqlite", "sqlite3")):
        deps["@cap-js/sqlite"] = "*"            # in-memory database for the smoke test only
    rep.say("Installing the CAP backend dependencies…")
    extra = {"overrides": pkg["overrides"]} if pkg.get("overrides") else {}
    nm, why = _tool_cache(deps, extra, rep, "CAP")
    if not nm:
        net = bool(NETWORK_ERR.search(why))
        rep.add("backend.install", names["backend.install"], "backend", "not_tested" if net else "failed",
                why, True, time.time() - t0)
        skip_rest("backend.install", "dependencies could not be installed")
        return {}
    _link_dir(nm, os.path.join(be, "node_modules"))
    rep.add("backend.install", names["backend.install"], "backend", "passed",
            f"{len(deps)} dependencies (cached tool set)", True, time.time() - t0)

    t0 = time.time()
    rep.say("Compiling the CDS model…")
    code, out = _run([node, "-e", _CDS_COMPILE], be, min(120, rep.remaining()))
    try:
        comp = json.loads(out[out.find("{"):]) if code == 0 else None
    except ValueError:
        comp = None
    if not comp or not comp.get("services"):
        rep.add("backend.compile", names["backend.compile"], "backend", "failed",
                ("\n".join([l.strip() for l in out.splitlines() if re.search(r"\.cds:\d+", l)][:8]
                           + [out[-1200:]]) if code else "the model defines no service"), True, time.time() - t0)
        skip_rest("backend.compile", "the CDS model does not compile")
        return {}
    compiled = []
    for s in comp["services"]:
        try:
            compiled.append(mdp.parse_metadata(s["edmx"]))
        except Exception as e:  # noqa: BLE001
            rep.add("backend.compile", names["backend.compile"], "backend", "failed",
                    f"compiled EDMX of {s['service']} unreadable: {e}", True, time.time() - t0)
            skip_rest("backend.compile", "compiled EDMX unreadable")
            return {}
    rep.add("backend.compile", names["backend.compile"], "backend", "passed",
            "services: " + ", ".join(f"{s['service']} at {comp['paths'].get(s['service'], '?')}"
                                     for s in comp["services"]), True, time.time() - t0)

    # contract: what the UI was built against must exist in the real service
    t0 = time.time()
    ui_md = files.get("localService/metadata.xml") or files.get("webapp/localService/metadata.xml") or ""
    sets = {es["name"]: es for m in compiled for es in m["entity_sets"]}
    ops = {o["name"] for m in compiled for o in m.get("operations", [])}
    problems = []
    try:
        ui = ui_model if ui_model and not ui_md else mdp.parse_metadata(ui_md)
    except Exception as e:  # noqa: BLE001
        ui = None
        problems.append(f"UI metadata unreadable: {e}")
    if ui:
        for es in ui["entity_sets"]:
            real = sets.get(es["name"])
            if not real:
                problems.append(f"entity set {es['name']} missing in the backend")
                continue
            rp = {p["name"] for p in real["properties"]}
            miss = [p["name"] for p in es["properties"] if p["name"] not in rp]
            if miss:
                problems.append(f"{es['name']}: properties missing in the backend: {', '.join(miss[:8])}")
            if sorted(es["keys"]) != sorted(real["keys"]):
                problems.append(f"{es['name']}: keys differ (UI {es['keys']} vs backend {real['keys']})")
        for o in ui.get("operations", []):
            if o["name"] not in ops:
                problems.append(f"operation {o['name']} missing in the backend")
    rep.add("backend.contract", names["backend.contract"], "backend", "failed" if problems else "passed",
            "\n".join(problems[:15]) or f"{len(ui['entity_sets']) if ui else 0} entity sets, "
                                        f"{len(ui.get('operations', [])) if ui else 0} operations match",
            True, time.time() - t0)

    # start the server (in-memory sqlite, sample data from db/data/*.csv)
    t0 = time.time()
    serve = os.path.join(nm, "@sap", "cds", "bin", "serve.js")
    port = _free_port()
    log = os.path.join(tmp, "cds-serve.log")
    env = _node_env()
    env.update(PORT=str(port), NODE_ENV="development", CDS_ENV="development")
    env.pop("VCAP_SERVICES", None)
    ui_dir = None
    if level == "deep" and os.path.isdir(os.path.join(tmp, "webapp")):   # CAP also serves the UI (same origin)
        app_dir = os.path.join(be, "app")
        os.makedirs(app_dir, exist_ok=True)
        ui_dir = os.path.join(app_dir, "__evoke_ui")
        try:
            _link_dir(os.path.join(tmp, "webapp"), ui_dir)
        except OSError:
            ui_dir = None
    fh = open(log, "w", encoding="utf-8")
    proc = subprocess.Popen([node, serve, "all", "--in-memory?", "--port", str(port)], cwd=be, stdout=fh,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env, **_popen_kwargs())
    user = _auth_user(pkg, cdsrc)
    up, _ = _wait_http(f"http://127.0.0.1:{port}/", proc, min(90, rep.remaining()), auth=user)
    if not up:
        _kill_tree(proc)
        fh.close()
        rep.add("backend.serve", names["backend.serve"], "backend", "failed", _err_first(_tail(log, 3000)),
                True, time.time() - t0)
        skip_rest("backend.serve", "the backend did not start")
        if ui_dir:
            _unlink_dir(ui_dir)
        return {}
    import requests
    s = requests.Session()
    s.auth = user
    base = f"http://127.0.0.1:{port}"
    # the path the UI calls (manifest dataSource) — else the CAP default
    uri = ""
    try:
        man = json.loads(_flat_view(files).get("manifest.json") or "{}")
        uri = ((man.get("sap.app", {}).get("dataSources") or {}).get("mainService") or {}).get("uri", "")
    except ValueError:
        pass
    svc_path = (uri or next(iter(comp["paths"].values()), "/")).rstrip("/") + "/"
    try:
        r = s.get(base + svc_path + "$metadata", timeout=20)
        ok = r.status_code == 200
    except Exception as e:  # noqa: BLE001
        ok, r = False, None
    rep.add("backend.serve", names["backend.serve"], "backend", "passed" if ok else "failed",
            f"GET {svc_path}$metadata → {r.status_code if r is not None else 'no response'}"
            + ("" if ok else "\nThe UI calls this path (manifest mainService) — the backend does not serve it.\n"
               + _tail(log, 800)), True, time.time() - t0)
    ctx = {"proc": proc, "log": fh, "port": port, "base": base, "ui_url": "", "auth": user, "ui_dir": ui_dir}
    if ui_dir:
        ctx["ui_url"] = base + "/__evoke_ui/index.html"
    if not ok:
        skip_rest("backend.serve", "service path not served")
        return ctx

    t0 = time.time()
    read_bad, rows = [], {}
    ui_sets = [es["name"] for es in (ui or compiled[0])["entity_sets"]] if (ui or compiled) else []
    for name in ui_sets:
        if name not in sets or name.endswith("_texts") or name.startswith("DraftAdministrativeData"):
            continue
        try:
            r = s.get(f"{base}{svc_path}{name}?$top=3", timeout=20)
            if r.status_code != 200:
                read_bad.append(f"{name}: HTTP {r.status_code} {r.text[:160]}")
            else:
                rows[name] = r.json().get("value") or []
        except Exception as e:  # noqa: BLE001
            read_bad.append(f"{name}: {e}")
    rep.add("backend.read", f"{names['backend.read']} ({len(rows)}/{len(rows) + len(read_bad)})", "backend",
            "failed" if read_bad else "passed",
            "\n".join(read_bad[:10]) or ", ".join(f"{k}: {len(v)} row(s)" for k, v in rows.items()),
            True, time.time() - t0)

    # create (non-critical: required-field rules may reject a generic payload)
    t0 = time.time()
    main = sets.get(main_set) or (sets.get(ui_sets[0]) if ui_sets and ui_sets[0] in sets else None)
    if main:
        payload = {}
        for i, p in enumerate(main["properties"]):
            if p["name"] in _DRAFT or p["name"] in ("createdAt", "createdBy", "modifiedAt", "modifiedBy"):
                continue
            if p["name"] in main["keys"]:
                if p["type"] != "Edm.Guid":
                    payload[p["name"]] = _dummy(p, 9000 + i)
                continue
            if not p.get("nullable", True):
                v = _dummy(p, i)
                if v is not None:
                    payload[p["name"]] = v
        try:
            r = s.post(f"{base}{svc_path}{main['name']}", json=payload, timeout=20)
            if r.status_code == 400:      # @mandatory is not visible in the EDMX: fill what CAP names, retry once
                try:
                    err = r.json().get("error") or {}
                    targets = {d.get("target") for d in (err.get("details") or [err])
                               if d.get("code") == "ASSERT_MANDATORY"}
                except ValueError:
                    targets = set()
                props = {p["name"]: (i, p) for i, p in enumerate(main["properties"])}
                seed = (rows.get(main["name"]) or [{}])[0]       # a seeded record has valid values (enums…)
                extra = {t: seed[t] if seed.get(t) is not None else _dummy(props[t][1], props[t][0])
                         for t in targets if t in props}
                if any(v is not None for v in extra.values()):
                    payload.update({k: v for k, v in extra.items() if v is not None})
                    r = s.post(f"{base}{svc_path}{main['name']}", json=payload, timeout=20)
            created = r.status_code in (200, 201)
            rep.add("backend.create", f"{names['backend.create']} ({main['name']})", "backend",
                    "passed" if created else "warning",
                    f"POST → {r.status_code}" + ("" if created else f": {r.text[:400]}"), False, time.time() - t0)
        except Exception as e:  # noqa: BLE001
            rep.add("backend.create", names["backend.create"], "backend", "warning", str(e), False, time.time() - t0)
    else:
        rep.add("backend.create", names["backend.create"], "backend", "not_tested", "no main entity set", False)

    # first bound action without required parameters on a seeded record (informational)
    t0 = time.time()
    done = False
    by_type = {es["entity_type"]: es for m in compiled for es in m["entity_sets"]}
    ns = compiled[0].get("namespace", "")
    for m in compiled:
        for o in m.get("operations", []):
            es = by_type.get(o.get("bound_type"))
            if o["kind"] != "action" or not es or not rows.get(es["name"]):
                continue
            if any(not p.get("nullable", True) for p in o.get("parameters", [])):
                continue
            row = dict(rows[es["name"]][0])
            if "IsActiveEntity" in es["keys"]:
                row["IsActiveEntity"] = True
            url = f"{base}{svc_path}{es['name']}{_key_predicate(es, row)}/{m.get('namespace') or ns}.{o['name']}"
            try:
                r = s.post(url, json={}, timeout=20)
                ok = r.status_code in (200, 201, 204)
                msg = ""
                if not ok:
                    try:
                        msg = r.json().get("error", {}).get("message", "")
                    except ValueError:
                        msg = r.text[:200]
                rep.add("backend.action", f"{names['backend.action']} ({es['name']}.{o['name']})", "backend",
                        "passed" if ok else "warning",
                        f"POST → {r.status_code}" + (f": {msg} (a state rule may legitimately reject this record)"
                                                     if not ok else ""), False, time.time() - t0)
            except Exception as e:  # noqa: BLE001
                rep.add("backend.action", names["backend.action"], "backend", "warning", str(e), False)
            done = True
            break
        if done:
            break
    if not done:
        rep.add("backend.action", names["backend.action"], "backend", "not_tested",
                "no bound action without required parameters on an entity with sample data", False)
    return ctx


# ─────────────────────────────────────────────────────────────────────────────
# Runtime (browser) checks
# ─────────────────────────────────────────────────────────────────────────────
_PROBE = r"""(() => {
  const r = { ui5: !!(window.sap && sap.ui && sap.ui.getCore), rendered: false, rows: 0, controls: 0, title: document.title };
  try {
    r.rendered = !!document.querySelector('.sapMShell, .sapMApp, .sapMNav, .sapFDynamicPage, .sapUxAPObjectPageLayout, .sapMPage, .sapFFlexibleColumnLayout, .sapTntToolPage');
    let n = document.querySelectorAll('.sapMListTblRow:not(.sapMListTblRowNoData), .sapUiTableRow:not(.sapUiTableRowHidden):not(.sapUiTableHeaderRow), .sapFCard, .sapMGT, .sapMObjLItem').length;
    const reg = sap && sap.ui && sap.ui.core && sap.ui.core.Element && sap.ui.core.Element.registry;
    if (reg) { r.controls = reg.size;
      reg.forEach(c => { try { if (c.isA && c.isA('sap.m.ListBase') && c.getItems) n = Math.max(n, c.getItems().length); } catch (e) {} }); }
    r.rows = n;
    const busy = document.querySelector('.sapUiLocalBusyIndicator'); r.busy = !!busy;
  } catch (e) { r.err = String(e); }
  return JSON.stringify(r);
})()"""


def _cdn_reachable(tmp: str) -> Tuple[bool, str]:
    try:
        with open(os.path.join(tmp, "webapp", "index.html"), encoding="utf-8") as f:
            html = f.read()
    except OSError:
        return False, "webapp/index.html missing"
    m = re.search(r'id="sap-ui-bootstrap"[^>]*?src="([^"]+)"', html, re.S) or \
        re.search(r'src="([^"]+)"[^>]*?id="sap-ui-bootstrap"', html, re.S)
    if not m or not m.group(1).startswith("http"):
        return True, ""
    import requests
    try:
        r = requests.head(m.group(1), timeout=8, allow_redirects=True)
        return r.status_code < 400, f"{m.group(1)} → HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001
        return False, f"{m.group(1)} unreachable ({type(e).__name__})"


def _browser_smoke(url: str, browser: str, rep: _Report, auth: Optional[Tuple[str, str]], wait: float = 40) -> Dict:
    """Open `url` in headless Chrome/Edge via the DevTools protocol. Returns
    {"ok", "probe", "exceptions", "errors", "detail"}."""
    import base64
    import requests
    from websockets.sync.client import connect
    port = _free_port()
    prof = tempfile.mkdtemp(prefix="evoke-chrome-")
    proc = subprocess.Popen([browser, "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={prof}",
                             "--no-first-run", "--no-default-browser-check", "--disable-gpu", "--disable-extensions",
                             "--window-size=1400,900", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_popen_kwargs())
    out = {"ok": False, "probe": {}, "exceptions": [], "errors": [], "detail": ""}
    try:
        ws_url = ""
        end = time.time() + 20
        while time.time() < end and not ws_url:
            try:
                pages = requests.get(f"http://127.0.0.1:{port}/json/list", timeout=2).json()
                ws_url = next((p["webSocketDebuggerUrl"] for p in pages if p.get("type") == "page"), "")
            except Exception:  # noqa: BLE001
                time.sleep(0.4)
        if not ws_url:
            out["detail"] = "browser did not expose the DevTools endpoint"
            return out
        with connect(ws_url, max_size=20_000_000, open_timeout=10) as ws:
            mid = [0]

            def send(method, params=None):
                mid[0] += 1
                ws.send(json.dumps({"id": mid[0], "method": method, "params": params or {}}))
                return mid[0]

            def pump(until_id=None, seconds=0.5):
                stop = time.time() + seconds
                result = None
                while time.time() < stop:
                    try:
                        msg = json.loads(ws.recv(timeout=max(0.05, stop - time.time())))
                    except TimeoutError:
                        break
                    m = msg.get("method")
                    if m == "Runtime.exceptionThrown":
                        d = msg["params"]["exceptionDetails"]
                        out["exceptions"].append(((d.get("exception") or {}).get("description") or d.get("text", ""))[:400])
                    elif m == "Runtime.consoleAPICalled" and msg["params"].get("type") == "error":
                        out["errors"].append(" ".join(str(a.get("value", a.get("description", "")))
                                                      for a in msg["params"].get("args", []))[:300])
                    elif m == "Log.entryAdded" and msg["params"]["entry"].get("level") == "error":
                        out["errors"].append(msg["params"]["entry"].get("text", "")[:300])
                    if until_id and msg.get("id") == until_id:
                        result = msg
                        break
                return result

            for m in ("Runtime.enable", "Log.enable", "Page.enable", "Network.enable"):
                pump(send(m), 5)
            if auth:
                tok = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
                pump(send("Network.setExtraHTTPHeaders", {"headers": {"Authorization": "Basic " + tok}}), 5)
            pump(send("Page.navigate", {"url": url}), 15)
            end = time.time() + min(wait, max(5, rep.remaining() - 5))
            probe, stable = {}, 0
            while time.time() < end:
                pump(None, 2)
                res = pump(send("Runtime.evaluate", {"expression": _PROBE, "returnByValue": True}), 10)
                try:
                    probe = json.loads(res["result"]["result"]["value"])
                except Exception:  # noqa: BLE001
                    continue
                if probe.get("rendered") and probe.get("rows") and not probe.get("busy"):
                    stable += 1
                    if stable >= 2:
                        break
            out["probe"] = probe
            out["ok"] = bool(probe.get("ui5") and probe.get("rendered"))
    except Exception as e:  # noqa: BLE001
        out["detail"] = f"{type(e).__name__}: {e}"
    finally:
        _kill_tree(proc)
        shutil.rmtree(prof, ignore_errors=True)
    return out


def _runtime(files: Dict[str, str], tmp: str, node: str, cli: str, why: str, rep: _Report, env: Dict,
             backend_ctx: Dict) -> None:
    serve_name = "App served locally" + (" by the CAP backend" if backend_ctx.get("ui_url") else " with mock data")
    names = {"runtime.serve": serve_name,
             "runtime.browser": "App boots in a headless browser (UI5 core, first page rendered, no uncaught errors)",
             "runtime.data": "First page shows data"}

    def skip(why_, from_id="runtime.serve"):
        hit = False
        for cid, nm in names.items():
            hit = hit or cid == from_id
            if hit and not rep.get(cid):
                rep.add(cid, nm, "runtime", "not_tested", why_, cid != "runtime.data")

    browser = env.get("chrome")
    if not node:
        return skip("Node.js is not available on this host")
    if not env.get("websockets"):
        return skip("the Python package 'websockets' is missing (pip install websockets)")
    if not browser:
        return skip("no Chrome/Edge found (set CHROME_PATH) — headless browser test not possible here")
    ok, msg = _cdn_reachable(tmp)
    if not ok:
        return skip(f"UI5 runtime not reachable from this host: {msg}")
    proc, fh = None, None
    t0 = time.time()
    auth = None
    if backend_ctx.get("ui_url"):
        url, auth = backend_ctx["ui_url"], backend_ctx.get("auth")
        rep.add("runtime.serve", serve_name, "runtime", "passed", url, True, 0)
    elif backend_ctx.get("proc") is not None:
        return skip("the backend is running but cannot serve the UI")
    else:
        if not cli:
            return skip(why or "UI5 tooling unavailable")
        if not os.path.exists(os.path.join(tmp, "ui5-mock.yaml")):
            return skip("project has no ui5-mock.yaml")
        port = _free_port()
        log = os.path.join(tmp, "ui5-serve.log")
        fh = open(log, "w", encoding="utf-8")
        proc = subprocess.Popen([node, cli, "serve", "--config", "ui5-mock.yaml", "--port", str(port)], cwd=tmp,
                                stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **_popen_kwargs())
        url = f"http://localhost:{port}/index.html"
        up, code = _wait_http(url, proc, min(90, rep.remaining()))
        if not up or code != 200:
            _kill_tree(proc)
            fh.close()
            rep.add("runtime.serve", serve_name, "runtime", "failed", _err_first(f"HTTP {code}\n" + _tail(log)), True,
                    time.time() - t0)
            return skip("the app could not be served", "runtime.browser")
        rep.add("runtime.serve", serve_name, "runtime", "passed", url, True, time.time() - t0)
    try:
        t0 = time.time()
        rep.say("Opening the app in a headless browser…")
        res = _browser_smoke(url, browser, rep, auth)
        p = res["probe"]
        exc = res["exceptions"]
        if not p and res["detail"]:
            rep.add("runtime.browser", names["runtime.browser"], "runtime", "not_tested", res["detail"], True,
                    time.time() - t0)
            return skip("browser test did not run", "runtime.data")
        failed = not res["ok"] or bool(exc)
        detail = (("UI5 core not loaded. " if not p.get("ui5") else "")
                  + ("Nothing rendered. " if p.get("ui5") and not p.get("rendered") else "")
                  + ("Uncaught: " + " | ".join(exc[:3]) if exc else "")
                  or f"rendered ({p.get('controls', 0)} controls)")
        if res["errors"]:
            detail += "\nConsole errors: " + " | ".join(dict.fromkeys(res["errors"]))[:900]
        rep.add("runtime.browser", names["runtime.browser"], "runtime", "failed" if failed else "passed", detail,
                True, time.time() - t0)
        if failed:
            return skip("the app did not render", "runtime.data")
        rep.add("runtime.data", names["runtime.data"], "runtime", "passed" if p.get("rows") else "warning",
                f"{p.get('rows')} row(s)/card(s) visible" if p.get("rows") else
                "no rows visible on the first page (it may be a dashboard, need a filter/Go, or have no data)",
                False, 0)
    finally:
        if proc is not None:
            _kill_tree(proc)
        if fh:
            fh.close()


# ─────────────────────────────────────────────────────────────────────────────
# Requirements
# ─────────────────────────────────────────────────────────────────────────────
def _requirements(coverage: Optional[List[Dict]], rep: _Report) -> List[Dict]:
    if not coverage:
        return []
    base = [c for c in rep.checks if c["category"] in ("static", "build") and c["critical"]]
    backend = [c for c in rep.checks if c["category"] == "backend" and c["critical"]]
    runtime = [c for c in rep.checks if c["category"] == "runtime" and c["critical"]]
    out = []
    for f in coverage:
        ds = f.get("status", "covered")
        verified = [c for c in base + backend + runtime if c["status"] in ("passed", "warning")]
        failed = [c for c in base + backend + runtime if c["status"] == "failed"]
        untested = [c for c in base + backend if c["status"] == "not_tested"]
        if ds == "missing":
            st, detail = "failed", "not in the design"
        elif ds == "partial":
            st, detail = "failed", "only partially covered by the design" + (f" — {f['note']}" if f.get("note") else "")
        elif failed:
            st, detail = "failed", "blocked by: " + ", ".join(c["name"] for c in failed[:3])
        elif untested or not base:
            st, detail = "not_tested", "could not run: " + ", ".join(c["name"] for c in untested[:3])
        else:
            ran_rt = any(c["status"] == "passed" for c in runtime)
            st = "passed"
            detail = ("built and ran in the browser" if ran_rt else "built; not exercised in a browser") + \
                     ("; backend smoke-tested" if any(c["status"] == "passed" for c in backend) else "")
        out.append({"feature": f.get("text") or f.get("id", ""), "id": f.get("id", ""),
                    "priority": f.get("priority", ""), "design_status": ds,
                    "verified_by": [c["id"] for c in verified], "status": st, "detail": detail})
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
def _main_set(files: Dict[str, str]) -> str:
    try:
        rec = json.loads(files.get("evoke-app.json") or "{}")
        return ((rec.get("blueprint") or {}).get("main") or {}).get("entity_set", "")
    except ValueError:
        return ""


def run(files: Dict[str, str], *, model: Optional[Dict] = None, coverage: Optional[List[Dict]] = None,
        level: str = "standard", progress: Optional[Callable[[str], None]] = None, timeout: int = 300) -> Dict:
    """Validate a generated project by building and (level "deep") running it. See module doc."""
    t_start = time.time()
    rep = _Report(progress, t_start + max(30, timeout))
    env = environment()
    node = _which("node") if env["node"] else ""
    tmp = tempfile.mkdtemp(prefix="evoke-validate-")
    backend_ctx: Dict = {}
    try:
        rep.say("Writing the project to a sandbox…")
        try:
            _write_sandbox(files, tmp)
        except Exception as e:  # noqa: BLE001
            rep.add("static.sandbox", "Project written to a sandbox", "static", "failed", f"{type(e).__name__}: {e}")
            raise _Abort()
        rep.say("Static checks…")
        _static(files, tmp, node, rep)
        mta = _mta(files, tmp, rep)
        _xs_app(files, mta, rep)
        cli, why = "", ""
        if node and os.path.isdir(os.path.join(tmp, "webapp")):
            try:
                cli, why = _prepare_ui5(files, tmp, rep)
            except Exception as e:  # noqa: BLE001
                cli, why = "", f"UI5 tooling setup failed: {e}"
        elif not os.path.isdir(os.path.join(tmp, "webapp")):
            why = "no webapp/ folder"
        _ui5_build(files, tmp, node, cli, why, rep)
        if "backend/package.json" in files:
            try:
                backend_ctx = _backend(files, tmp, node, rep, level, _main_set(files), model)
            except Exception as e:  # noqa: BLE001
                rep.add("backend.error", "Backend checks", "backend", "not_tested", f"checker error: {e}", False)
        static_bad = [c["name"] for c in rep.checks if c["category"] == "static" and c["status"] == "failed"]
        if level == "deep" and static_bad:
            for cid, nm in (("runtime.browser", "App boots in a headless browser"),):
                rep.add(cid, nm, "runtime", "not_tested", "skipped — fix the static errors first", False)
        elif level == "deep":
            try:
                _runtime(files, tmp, node, cli, why, rep, env, backend_ctx)
            except Exception as e:  # noqa: BLE001
                rep.add("runtime.error", "Runtime checks", "runtime", "not_tested", f"checker error: {e}", False)
        else:
            for cid, nm in (("runtime.browser", "App boots in a headless browser"),):
                rep.add(cid, nm, "runtime", "not_tested", "runs only with the deep validation level", False)
    except _Abort:
        pass
    except Exception as e:  # noqa: BLE001 — never crash the studio; report what could not run
        rep.add("validator.error", "Validation run", "static", "not_tested", f"validator error: {type(e).__name__}: {e}",
                False)
    finally:
        if backend_ctx.get("proc") is not None:
            _kill_tree(backend_ctx["proc"])
            try:
                backend_ctx["log"].close()
            except Exception:  # noqa: BLE001
                pass
        for link in (os.path.join(tmp, "node_modules"), os.path.join(tmp, "backend", "node_modules"),
                     backend_ctx.get("ui_dir") or ""):
            if link and os.path.lexists(link):
                _unlink_dir(link)
        shutil.rmtree(tmp, ignore_errors=True)
    reqs = _requirements(coverage, rep)
    summary = {k: sum(c["status"] == s for c in rep.checks)
               for k, s in (("passed", "passed"), ("failed", "failed"), ("not_tested", "not_tested"),
                            ("warnings", "warning"))}
    report = {"checks": rep.checks, "requirements": reqs, "summary": summary, "level": level,
              "environment": {k: env[k] for k in ("node", "npm", "chrome")},
              "ran_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
              "seconds": round(time.time() - t_start, 1)}
    report["blocking"] = blocking(report)
    return report
