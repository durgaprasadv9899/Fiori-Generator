"""
deployer.py
Handles Cloud Foundry deployment via subprocess calls to cf CLI and mbt.
Streams log output back line-by-line for display in Streamlit.
"""
import os
import re
import sys
import subprocess
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Generator, Optional


# Directory where vendored cf / mbt binaries live when the POC runs somewhere
# that has no system-installed CLI (e.g. a Cloud Foundry buildpack container).
# Populated at deploy time by scripts/vendor_cli.sh (see that script). Resolved
# relative to this file so it works regardless of the container's CWD.
_APP_ROOT = Path(__file__).resolve().parent
_VENDOR_BIN = _APP_ROOT / "bin"

# cf looks for plugins under $CF_PLUGIN_HOME/.cf/plugins. In a buildpack
# container the real HOME is ephemeral, so we point CF_PLUGIN_HOME at an in-app
# directory that uploads with `cf push` (populated by vendor_cli.sh). This is
# what makes the multiapps plugin (which provides `cf deploy`) persist to
# runtime. Layout: ./cf-plugins/.cf/plugins/<plugin binary> + config.json
_PLUGIN_HOME = _APP_ROOT / "cf-plugins"


def _cf_env(cf_home: Optional[str] = None) -> dict:
    """Environment for cf subprocesses: forces CF_PLUGIN_HOME to the vendored
    in-app plugin dir (when present) so `cf deploy` from the multiapps plugin is
    found at runtime, and puts ./bin on PATH as a belt-and-suspenders."""
    env = os.environ.copy()
    if cf_home:
        # per-user CF session (the studio is multi-user: one login must never be
        # reused by another browser session)
        os.makedirs(cf_home, exist_ok=True)
        env["CF_HOME"] = cf_home
    if _PLUGIN_HOME.is_dir():
        env["CF_PLUGIN_HOME"] = str(_PLUGIN_HOME)
    if _VENDOR_BIN.is_dir() and sys.platform.startswith("linux"):
        env["PATH"] = str(_VENDOR_BIN) + os.pathsep + env.get("PATH", "")
    return env


def _resolve_binary(name: str) -> Optional[str]:
    """Return an executable path for `name`, preferring a vendored copy in
    ./bin over anything on PATH. Returns None if neither exists."""
    vendored = _VENDOR_BIN / name
    # ./bin holds LINUX binaries for the CF container (scripts/vendor_cli.sh);
    # never pick them on Windows/macOS, where they can't execute.
    if sys.platform.startswith("linux") and vendored.is_file() and os.access(vendored, os.X_OK):
        return str(vendored)
    found = shutil.which(name)
    return found


def _resolve_cmd(cmd: list[str]) -> list[str]:
    """Rewrite a command list so its first token (cf/mbt/etc.) points at the
    resolved binary path. Leaves the command unchanged if it can't be resolved
    (the caller's own not-found handling then applies)."""
    if not cmd:
        return cmd
    resolved = _resolve_binary(cmd[0])
    if resolved:
        return [resolved] + cmd[1:]
    return cmd


_CF_MISSING_MSG = (
    "[ERROR] The Cloud Foundry CLI (cf) is not available in this environment, so "
    "this hosted generator cannot deploy directly. Two options:\n"
    "  1) Download the project ZIP (button above) and deploy it from a machine "
    "where you're logged in with `cf login` — this is the recommended flow.\n"
    "  2) If you administer this hosted instance, run scripts/vendor_cli.sh "
    "before `cf push` so cf/mbt are bundled into ./bin, then redeploy the POC."
)


class CFDeployer:
    """Wraps cf CLI for BTP Cloud Foundry deployments."""

    def __init__(self, api_url: str, org: str, space: str, cf_home: Optional[str] = None):
        self.api_url = (api_url or "").strip().rstrip("/")
        self.org = (org or "").strip()
        self.space = (space or "").strip()
        self.cf_home = cf_home

    def _env(self) -> dict:
        return _cf_env(self.cf_home)

    def _run(self, cmd: list[str], cwd: Optional[str] = None) -> Generator[str, None, None]:
        """Run a command and stream stdout lines. The command's binary is
        resolved against the vendored ./bin dir first, then PATH."""
        cmd = _resolve_cmd(cmd)
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=cwd,
                env=self._env(), stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            yield (f"[ERROR] '{cmd[0]}' not found. The Cloud Foundry CLI (cf) and "
                   f"MTA build tool (mbt) must be installed or vendored into ./bin. "
                   f"If this POC is running inside a container, run "
                   f"scripts/vendor_cli.sh before pushing, or deploy the generated "
                   f"app from a machine that has the cf CLI.")
            return
        for line in iter(process.stdout.readline, ""):
            yield line.rstrip()
        process.wait()
        if process.returncode != 0:
            yield f"[ERROR] Command failed with exit code {process.returncode}"

    def check_cf_cli(self) -> bool:
        """Return True if cf CLI is available (vendored or on PATH)."""
        return _resolve_binary("cf") is not None

    def check_mbt(self) -> bool:
        """Return True if mbt (MTA build tool) is available (vendored or on PATH)."""
        return _resolve_binary("mbt") is not None

    def check_multiapps_plugin(self) -> bool:
        """Return True if the CF 'multiapps' plugin (provides `cf deploy`) is
        installed. Required for MTA deployment that provisions services."""
        try:
            result = subprocess.run(_resolve_cmd(["cf", "plugins"]), capture_output=True, text=True, env=self._env(), stdin=subprocess.DEVNULL)
            return "multiapps" in result.stdout.lower() or "MtaPlugin" in result.stdout
        except Exception:
            return False

    def service_status(self, service_name: str) -> str:
        """Return the create/update status of a service instance, or 'not found'.
        Used to diagnose 'Using empty destinations to run' crashes: if the
        destination service instance failed to create (often because an
        .mtaext field was left blank), the approuter will never see it."""
        try:
            r = subprocess.run(_resolve_cmd(["cf", "service", service_name]),
                                capture_output=True, text=True, env=self._env(), stdin=subprocess.DEVNULL)
            if r.returncode != 0:
                return "not found"
            for line in r.stdout.splitlines():
                if line.strip().lower().startswith("status:") or \
                   line.strip().lower().startswith("last operation"):
                    return line.strip()
            return r.stdout.strip()[:200]
        except Exception as e:
            return f"error: {e}"

    def is_service_bound(self, app_name: str, service_name: str) -> bool:
        """True if service_name appears in the app's bound services."""
        try:
            r = subprocess.run(_resolve_cmd(["cf", "services"]), capture_output=True, text=True, env=self._env(), stdin=subprocess.DEVNULL)
            if r.returncode != 0:
                return False
            for line in r.stdout.splitlines():
                if service_name in line and app_name in line:
                    return True
            return False
        except Exception:
            return False

    def create_destination_service(self, service_name: str,
                                   destinations_json: str) -> Generator[str, None, None]:
        """Explicitly create (or update) the destination service instance with
        the given destinations config, via `cf create-service` / `cf
        update-service`. This is the debuggable alternative to relying on the
        MTA resource's init_data: the create/update call's own error message
        tells you immediately why a destination failed (bad plan, missing
        entitlement, malformed config) instead of surfacing only as a later
        'Using empty destinations to run' crash in the approuter logs."""
        import json as _json
        try:
            config = _json.loads(destinations_json)
        except Exception as e:
            yield f"[ERROR] destinations config is not valid JSON: {e}"
            return

        status = self.service_status(service_name)
        config_str = _json.dumps(config)
        if status == "not found":
            yield f"Creating destination service '{service_name}'..."
            yield from self._run([
                "cf", "create-service", "destination", "lite", service_name,
                "-c", config_str,
            ])
        else:
            yield f"Destination service '{service_name}' exists ({status}); updating..."
            yield from self._run([
                "cf", "update-service", service_name, "-c", config_str,
            ])

    def bind_service(self, app_name: str, service_name: str) -> Generator[str, None, None]:
        """Bind a service instance to an app and restage so the binding takes
        effect. Needed after create_destination_service() when deploying via
        cf push rather than through the MTA's `requires:` wiring."""
        yield f"Binding '{service_name}' to '{app_name}'..."
        yield from self._run(["cf", "bind-service", app_name, service_name])
        yield f"Restaging '{app_name}' to apply the binding..."
        yield from self._run(["cf", "restage", app_name])

    @staticmethod
    def write_mta_extension(project_dir: str, content: str,
                            filename: str = "deploy.mtaext") -> str:
        """Write a deploy-time MTA extension file (carries live secrets) into the
        build dir. Returns its path. The temp dir is deleted after deploy."""
        path = os.path.join(project_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def login(self, username: str, password: str, origin: str = "") -> Generator[str, None, None]:
        """Password login via `cf auth` (non-interactive). Works for SAP ID service users
        (origin "sap.ids", the default) and, with `origin`, for a custom identity provider
        (e.g. the subaccount's SAP Cloud Identity tenant). Users with 2FA must use SSO."""
        if not self.check_cf_cli():
            yield _CF_MISSING_MSG
            return
        username = (username or "").strip()
        env = self._env()
        env["CF_USERNAME"] = username
        env["CF_PASSWORD"] = password
        # Set the API first so the session is scoped correctly.
        yield from self._run(["cf", "api", self.api_url])
        # `cf auth` reads the credentials from the environment (no stdin, no echo,
        # nothing on the command line / process list).
        args = ["cf", "auth"] + (["--origin", origin.strip()] if (origin or "").strip() else [])
        try:
            proc = subprocess.run(
                _resolve_cmd(args),
                capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=120,
            )
        except FileNotFoundError:
            yield _CF_MISSING_MSG
            return
        except subprocess.TimeoutExpired:
            yield "[ERROR] cf auth timed out."
            return
        out = (proc.stdout or "") + (proc.stderr or "")
        for line in out.splitlines():
            yield line.rstrip()
        if proc.returncode != 0:
            yield "[ERROR] Sign-in with user and password failed. Check the user/password; if your "
            yield "        user belongs to a custom identity provider, enter its origin key; users "
            yield "        with two-factor authentication must use the SSO passcode."
            return
        yield from self._run(["cf", "target", "-o", self.org, "-s", self.space])

    def login_sso(self, passcode: str) -> Generator[str, None, None]:
        """SSO one-time-passcode login. Get the passcode from the URL shown by
        `cf login -a <api> --sso` (the field labelled 'Temporary Authentication
        Code'). Works for subaccounts where password login is disabled."""
        if not self.check_cf_cli():
            yield _CF_MISSING_MSG
            return
        # People paste the code with spaces/line breaks, or the whole page text.
        code = re.sub(r"\s+", "", passcode or "")
        m = re.search(r"([A-Za-z0-9_-]{6,})$", code)
        code = m.group(1) if m else code
        if not code:
            yield "[ERROR] No passcode entered."
            return
        # Set the API, authenticate with the one-time code, then target org/space as
        # separate non-interactive steps (cf login would prompt when the org/space
        # cannot be resolved and wait forever for input).
        yield from self._run(["cf", "api", self.api_url])
        yield from self._run(["cf", "login", "-a", self.api_url, "--sso-passcode", code])
        if self._authenticated():
            yield from self._run(["cf", "target", "-o", self.org, "-s", self.space])
        else:
            yield ("[ERROR] The passcode was not accepted. Passcodes are single-use and expire after a "
                   "few minutes — open " + self.passcode_url() + " , sign in, and paste the NEW code "
                   "right away.")

    def _authenticated(self) -> bool:
        try:
            r = subprocess.run(_resolve_cmd(["cf", "oauth-token"]), capture_output=True, text=True,
                               env=self._env(), stdin=subprocess.DEVNULL, timeout=60)
            return r.returncode == 0 and "bearer" in (r.stdout or "").lower()
        except Exception:
            return False

    def passcode_url(self) -> str:
        """Where the one-time passcode comes from: the platform's LOGIN host (not the API
        host), e.g. https://login.cf.us10-001.hana.ondemand.com/passcode."""
        try:
            import requests
            info = requests.get(self.api_url + "/v2/info", timeout=10).json()
            ep = (info.get("authorization_endpoint") or "").rstrip("/")
            if ep:
                return ep + "/passcode"
        except Exception:
            pass
        return re.sub(r"//api\.", "//login.", self.api_url) + "/passcode"

    def whoami(self) -> str:
        """'user@org/space' of the current session, '' when not signed in."""
        try:
            r = subprocess.run(_resolve_cmd(["cf", "target"]), capture_output=True, text=True,
                               env=self._env(), stdin=subprocess.DEVNULL, timeout=60)
        except Exception:
            return ""
        if r.returncode != 0:
            return ""
        vals = {}
        for line in (r.stdout or "").splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                vals[k.strip().lower()] = v.strip()
        if not vals.get("user") or not self._authenticated():
            return ""
        return f"{vals.get('user')} · {vals.get('org', '-')} / {vals.get('space', '-')}"

    def is_logged_in(self) -> bool:
        """True only when there is a valid session AND an org/space is targeted.
        Guards against the 'token expired' -> 'no org targeted' cascade."""
        try:
            r = subprocess.run(_resolve_cmd(["cf", "target"]), capture_output=True, text=True, env=self._env(), stdin=subprocess.DEVNULL)
        except Exception:
            return False
        if r.returncode != 0:
            return False
        text = (r.stdout or "").lower()
        # `cf target` still prints user/org from the config after the token expired —
        # require a token that can actually be refreshed.
        return ("org:" in text and "space:" in text) and self._authenticated()

    def target(self) -> Generator[str, None, None]:
        """Explicitly target the org and space (deploy needs this set)."""
        yield f"Targeting org '{self.org}' / space '{self.space}'..."
        yield from self._run(["cf", "target", "-o", self.org, "-s", self.space])

    def build_mta(self, project_dir: str) -> Generator[str, None, None]:
        """Build mta_archives/app.mtar. Uses mbt when it AND GNU make are
        available (mbt drives make); otherwise — e.g. inside the CF Python
        container, which has no make — builds the identical archive layout in
        pure Python (build_mtar_python)."""
        yield "Building MTA archive..."
        if self.check_mbt() and shutil.which("make"):
            yield from self._run(["mbt", "build", "--mtar", "app.mtar"], cwd=project_dir)
            return
        try:
            path = build_mtar_python(project_dir)
            yield f"MTA archive built (python builder): {os.path.relpath(path, project_dir)}"
        except Exception as e:  # noqa: BLE001 — surfaced in the deploy log
            yield f"[ERROR] Command failed: could not build the MTA archive: {e}"

    def deploy_mtar(self, project_dir: str,
                    ext_file: Optional[str] = None) -> Generator[str, None, None]:
        """Deploy compiled .mtar to CF. When ext_file is given it is passed via
        `-e` so the destination credentials (and any other overrides) are
        applied at deploy time without living in the committed mta.yaml.

        On failure, the underlying `cf deploy` error is often terse; we capture
        it and, when an MTA operation id is present, auto-fetch the detailed
        operation log (`cf dmol`) so the real reason is visible without a manual
        follow-up step."""
        # Ensure org/space are targeted — 'cf deploy' fails without a target
        yield from self.target()
        yield "Deploying to Cloud Foundry..."
        mtar_path = os.path.join(project_dir, "mta_archives", "app.mtar")
        if not os.path.exists(mtar_path):
            mtar_path = os.path.join(project_dir, "app.mtar")
        cmd = ["cf", "deploy", mtar_path, "-f"]
        if ext_file and os.path.exists(ext_file):
            # Pass the ABSOLUTE path to the extension so cf resolves it
            # regardless of its working directory (a bare basename can fail to
            # resolve in some containers).
            cmd += ["-e", os.path.abspath(ext_file)]
            yield f"Applying deploy extension {os.path.basename(ext_file)} (destinations + credentials)..."

        # Run, capturing lines so we can detect failure + extract an operation id.
        op_id = None
        failed = False
        for line in self._run(cmd, cwd=project_dir):
            yield line
            low = line.lower()
            if "operation id" in low or "process id" in low:
                # e.g. "Process id: 0123abcd-..." — grab the last token
                parts = line.replace(":", " ").split()
                if parts:
                    op_id = parts[-1].strip()
            # Only treat genuine failure signals as failure. Matching a bare
            # "error" substring is too broad — routine lines ("0 errors",
            # "error log", URLs) contain it and would flag a SUCCESSFUL deploy
            # as failed. Use the specific signals cf/mbt actually emit.
            if (line.startswith("[ERROR] Command failed")
                    or "process failed" in low
                    or "deployment failed" in low
                    or low.startswith("error:")
                    or "failed with exit code" in low):
                failed = True

        if failed:
            yield "─" * 60
            yield "Deploy failed. Fetching the detailed MTA operation log..."
            if op_id:
                yield from self._run(["cf", "dmol", "-i", op_id], cwd=project_dir)
            else:
                # No id parsed — list recent ops so the user can inspect.
                yield "Could not parse an operation id automatically. Recent MTA operations:"
                yield from self._run(["cf", "mta-ops"], cwd=project_dir)
                yield "Run: cf dmol -i <operation-id>  to see the full error."

    def cf_push(self, project_dir: str, app_name: str) -> Generator[str, None, None]:
        """Fallback: simple cf push for static UI5 apps."""
        yield f"Pushing {app_name} to Cloud Foundry..."
        yield from self._run(["cf", "push", app_name], cwd=project_dir)

    def get_app_url(self, app_name: str) -> Optional[str]:
        """Retrieve the app route URL after deployment."""
        try:
            result = subprocess.run(
                _resolve_cmd(["cf", "app", app_name]),
                capture_output=True, text=True, env=self._env(), stdin=subprocess.DEVNULL
            )
            for line in result.stdout.split("\n"):
                if "routes:" in line.lower():
                    parts = line.split(":", 1)
                    if len(parts) > 1:
                        route = parts[1].strip()
                        if route:
                            return f"https://{route}"
        except Exception:
            pass
        return None

    def get_recent_logs(self, app_name: str, lines: int = 80) -> tuple:
        """Recent app logs as (ok, text). On failure the text explains why
        (cf prints only 'FAILED' on stdout; the reason is on stderr)."""
        if not app_name:
            return False, "No app name given."
        try:
            result = subprocess.run(
                _resolve_cmd(["cf", "logs", app_name, "--recent"]),
                capture_output=True, text=True, env=self._env(), stdin=subprocess.DEVNULL, timeout=120)
        except FileNotFoundError:
            return False, _CF_MISSING_MSG
        except subprocess.TimeoutExpired:
            return False, "Timed out reading logs."
        out = (result.stdout or "").strip()
        if result.returncode != 0 or out.endswith("FAILED"):
            reason = (result.stderr or "").strip() or out
            hint = ""
            if "not found" in reason.lower():
                hint = (f"\n\nNo app named '{app_name}' exists in this space — use the "
                        f"generated app's App ID (it is the CF app name).")
            elif "not logged in" in reason.lower() or "token" in reason.lower():
                hint = "\n\nThe CF session expired — deploy (log in) again first."
            return False, f"Could not read logs for '{app_name}': {reason}{hint}"
        body = [l for l in out.splitlines() if not l.startswith("Retrieving logs for app")]
        return True, "\n".join(body[-lines:]).strip()


# Module types as the MTA deployer expects them in mtad.yaml (what mbt maps).
_MTAD_MODULE_TYPES = {"approuter.nodejs": "javascript.nodejs", "nodejs": "javascript.nodejs",
                      "html5": "javascript.nodejs"}


def build_mtar_python(project_dir: str, mtar_name: str = "app.mtar") -> str:
    """Pure-Python equivalent of `mbt build` for the generator's projects
    (modules with no build step + file-based resource parameters).

    Archive layout (same as mbt):
        META-INF/MANIFEST.MF
        META-INF/mtad.yaml          deployment descriptor derived from mta.yaml
        <module>/data.zip           module content (minus build-parameters.ignore)
        <resource file>             e.g. xs-security.json, declared as MTA-Resource
    Returns the archive path (<project>/mta_archives/<mtar_name>)."""
    import fnmatch
    import io
    import yaml

    root = Path(project_dir)
    mta = yaml.safe_load((root / "mta.yaml").read_text(encoding="utf-8"))
    mtad = {"_schema-version": str(mta.get("_schema-version", "3.3.0")),
            "ID": mta["ID"], "version": str(mta.get("version", "0.0.1"))}
    manifest = ["manifest-Version: 1.0", "Created-By: Evoke Fiori Generator", ""]
    entries = {}

    mods = []
    for m in mta.get("modules") or []:
        src = (root / (m.get("path") or ".")).resolve()
        ignore = list((m.get("build-parameters") or {}).get("ignore") or [])
        ignore += ["mta_archives/", "*.mtar", "node_modules/", ".git/"]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(src.rglob("*")):
                if not f.is_file():
                    continue
                rel = f.relative_to(src).as_posix()
                skip = False
                for pat in ignore:
                    if pat.endswith("/"):
                        if rel.startswith(pat) or ("/" + pat) in ("/" + rel):
                            skip = True
                    elif fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(f.name, pat):
                        skip = True
                if not skip:
                    zf.write(f, rel)
        entries[f"{m['name']}/data.zip"] = buf.getvalue()
        manifest += [f"Name: {m['name']}/data.zip", f"MTA-Module: {m['name']}",
                     "Content-Type: application/zip", "", ""]
        mod = {k: v for k, v in m.items() if k not in ("build-parameters",)}
        mod["type"] = _MTAD_MODULE_TYPES.get(m.get("type"), m.get("type"))
        mod["path"] = m["name"]
        mods.append(mod)
    mtad["modules"] = mods

    res = []
    for r in mta.get("resources") or []:
        params = r.get("parameters") or {}
        if params.get("path"):
            fpath = (root / params["path"]).resolve()
            entries[fpath.name] = fpath.read_bytes()
            manifest += [f"Name: {fpath.name}", f"MTA-Resource: {r['name']}",
                         "Content-Type: application/json", "", ""]
        res.append(r)
    if res:
        mtad["resources"] = res
    if mta.get("parameters"):
        mtad["parameters"] = mta["parameters"]
    manifest += ["Name: META-INF/mtad.yaml", "Content-Type: text/plain", "", ""]

    out_dir = root / "mta_archives"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / mtar_name
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("META-INF/MANIFEST.MF", "\n".join(manifest))
        zf.writestr("META-INF/mtad.yaml", yaml.safe_dump(mtad, sort_keys=False))
        for name, data in entries.items():
            zf.writestr(name, data)
    return str(out)


def write_project_to_disk(files: dict[str, str], base_dir: str) -> None:
    """Write generated file dict to disk, creating subdirectories as needed."""
    # Files that live at the PROJECT ROOT (not under webapp/)
    root_files = ("mta.yaml", "package.json", "ui5.yaml", "xs-app.json",
                  "xs-security.json", ".env.example", ".gitignore",
                  "destinations.stub.json", "deploy.mtaext.example",
                  "ui5-mock.yaml", "README.md", "evoke-app.json")
    for filepath, content in files.items():
        if filepath in root_files or filepath.startswith("approuter/"):
            full_path = Path(base_dir) / filepath          # project root (or approuter/)
        else:
            full_path = Path(base_dir) / "webapp" / filepath
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")

    # Write index.html bootstrap
    index_path = Path(base_dir) / "webapp" / "index.html"
    if not index_path.exists():
        app_id = "com.generated.app"
        try:
            manifest = __import__("json").loads(files.get("manifest.json", "{}"))
            app_id = manifest.get("sap.app", {}).get("id", app_id)
        except Exception:
            pass
        index_path.write_text(
            _index_html_template(app_id),
            encoding="utf-8",
        )


def _index_html_template(app_id: str) -> str:
    # Build the JSON snippets separately to avoid f-string brace conflicts
    resource_roots = '{' + f'"{app_id}": "./"' + '}'
    settings = '{' + f'"id": "{app_id}"' + '}'
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Fiori App</title>
    <script id="sap-ui-bootstrap"
        src="https://ui5.sap.com/resources/sap-ui-core.js"
        data-sap-ui-libs="sap.m,sap.ui.core,sap.f"
        data-sap-ui-theme="sap_horizon"
        data-sap-ui-resourceroots='{resource_roots}'
        data-sap-ui-oninit="module:sap/ui/core/ComponentSupport"
        data-sap-ui-compatversion="edge"
        data-sap-ui-async="true">
    </script>
</head>
<body class="sapUiBody" id="content">
    <div data-sap-ui-component
         data-name="{app_id}"
         data-id="container"
         data-settings='{settings}'>
    </div>
</body>
</html>"""


def create_zip_archive(files: dict[str, str], app_name: str = "fiori_app") -> bytes:
    """Return a ZIP archive of all generated files as bytes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        write_project_to_disk(files, tmpdir)
        zip_path = os.path.join(tmpdir, f"{app_name}.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, filenames in os.walk(tmpdir):
                for fname in filenames:
                    if fname.endswith(".zip"):
                        continue
                    full = os.path.join(root, fname)
                    arcname = os.path.relpath(full, tmpdir)
                    zf.write(full, arcname)
        with open(zip_path, "rb") as f:
            return f.read()