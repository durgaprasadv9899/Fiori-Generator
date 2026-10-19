"""
adopt_files.py — bring an existing project into the studio: from a ZIP, a Git repository or a
Cloud Foundry app, into the workspace's file map {path: text}.

The workspace stores text. Binary files (images, fonts, xlsx seed data…) are kept as
`BINARY_PREFIX + base64` so an adopted project round-trips unchanged; `to_dir` / `to_zip` decode
them again. Build output, dependencies and VCS folders are skipped; so are files that would not
fit (sizes below).
"""

from __future__ import annotations

import base64
import io
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from typing import Dict, List, Optional, Tuple

BINARY_PREFIX = "\u0000evoke-b64:"
MAX_FILES = 6000
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 80 * 1024 * 1024

# Folder names that are never part of the source (dependencies, caches, build output of tools).
SKIP_DIRS = {"node_modules", ".git", ".svn", ".hg", "mta_archives", "gen", ".venv", "venv", "env", "__pycache__",
             ".pytest_cache", ".mypy_cache", "coverage", ".nyc_output", ".angular", ".next", ".nuxt", ".turbo",
             ".cache", ".parcel-cache", ".idea", ".vscode-test", "target", ".gradle", ".DS_Store", ".tox",
             "site-packages", ".svelte-kit", ".expo"}
SKIP_FILES = {".DS_Store", "Thumbs.db"}
SKIP_EXT = (".mtar", ".pyc", ".class", ".log", ".tsbuildinfo")
# Secrets that must not be imported (they would end up in every version and export).
SECRET_FILES = ("default-env.json", ".env", ".env.local", ".env.production", "vcap_services.json",
                ".cdsrc-private.json", "secrets.toml")


class SourceError(ValueError):
    pass


def is_binary(content: str) -> bool:
    return content.startswith(BINARY_PREFIX)


def decode(content: str) -> bytes:
    return base64.b64decode(content[len(BINARY_PREFIX):]) if is_binary(content) else content.encode("utf-8")


def encode(data: bytes) -> str:
    """Text when the bytes are UTF-8 text, else the binary marker + base64."""
    if b"\x00" not in data[:8000]:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            pass
    return BINARY_PREFIX + base64.b64encode(data).decode("ascii")


def _skip_path(rel: str, keep_dist: bool) -> Optional[str]:
    parts = rel.split("/")
    name = parts[-1]
    for p in parts[:-1]:
        if p in SKIP_DIRS or (p in ("dist", "build", "out") and not keep_dist):
            return "build output / dependencies"
    if name in SKIP_FILES or name.endswith(SKIP_EXT):
        return "not source"
    if name in SECRET_FILES or name.startswith(".env."):
        return "secrets (not imported)"
    return None


def _has_sources(paths: List[str]) -> bool:
    """dist/ and build/ are build output when the project also has sources; a deployed app downloaded
    from Cloud Foundry may consist of nothing else."""
    return any(re.search(r"(^|/)(src|webapp|app|srv|db)/", p) or p.endswith(("package.json", "ui5.yaml",
                                                                                "pyproject.toml", "requirements.txt"))
               for p in paths)


def _common_root(paths: List[str]) -> str:
    """'project-main/' when every entry lives in that one folder (GitHub archives, zipped folders)."""
    tops = {p.split("/", 1)[0] for p in paths if "/" in p}
    if len(tops) == 1 and all("/" in p for p in paths):
        return tops.pop() + "/"
    return ""


def from_entries(entries: List[Tuple[str, bytes]]) -> Tuple[Dict[str, str], Dict]:
    """[(path, bytes)] -> (files, info). info: {files, bytes, skipped: [{path, why}], binary}."""
    paths = [p.replace("\\", "/").lstrip("./") for p, _ in entries]
    root = _common_root([p for p in paths if p])
    keep_dist = not _has_sources(paths)
    files: Dict[str, str] = {}
    skipped: List[Dict] = []
    total = 0
    binary = 0
    for (orig, data), p in zip(entries, paths):
        rel = p[len(root):] if root and p.startswith(root) else p
        if not rel or rel.endswith("/"):
            continue
        why = _skip_path(rel, keep_dist)
        if not why and len(data) > MAX_FILE_BYTES:
            why = f"larger than {MAX_FILE_BYTES // (1024 * 1024)} MB"
        if not why and total + len(data) > MAX_TOTAL_BYTES:
            why = "project size limit reached"
        if not why and len(files) >= MAX_FILES:
            why = "file count limit reached"
        if why:
            if len(skipped) < 400 and why not in ("build output / dependencies", "not source"):
                skipped.append({"path": rel, "why": why})
            continue
        content = encode(data)
        binary += is_binary(content)
        files[rel] = content
        total += len(data)
    if not files:
        raise SourceError("The project is empty (or only contains build output / dependencies).")
    return files, {"files": len(files), "bytes": total, "skipped": skipped, "binary": binary}


def from_zip(data: bytes) -> Tuple[Dict[str, str], Dict]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise SourceError("The upload is not a ZIP file.")
    entries, big = [], []
    with zf:
        for zi in zf.infolist():
            if zi.is_dir():
                continue
            name = zi.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/"):
                continue                                    # zip-slip
            # cheap pre-filter so big node_modules folders are never decompressed
            if any(part in SKIP_DIRS for part in name.split("/")[:-1]):
                continue
            if zi.file_size > MAX_FILE_BYTES:
                big.append({"path": name, "why": f"larger than {MAX_FILE_BYTES // (1024 * 1024)} MB"})
                continue
            entries.append((name, zf.read(zi)))
    files, info = from_entries(entries)
    info["skipped"] = big + info["skipped"]
    return files, info


def from_dir(base: str) -> Tuple[Dict[str, str], Dict]:
    entries = []
    for root, dirs, names in os.walk(base):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for n in names:
            fp = os.path.join(root, n)
            rel = os.path.relpath(fp, base).replace(os.sep, "/")
            try:
                if os.path.getsize(fp) > MAX_FILE_BYTES:
                    continue
                with open(fp, "rb") as f:
                    entries.append((rel, f.read()))
            except OSError:
                continue
    return from_entries(entries)


# ─────────────────────────────────────────────────────────────────────────────
# Git
# ─────────────────────────────────────────────────────────────────────────────
_GIT_URL = re.compile(r"^https://[\w.\-]+(:\d+)?/[\w.\-~/%]+?(\.git)?/?$")


def _redact(text: str, token: str) -> str:
    return text.replace(token, "***") if token else text


def _auth_url(url: str, token: str) -> str:
    if not token:
        return url
    host = url.split("/")[2]
    user = "oauth2" if "gitlab" in host else "x-access-token"
    return url.replace("https://", f"https://{user}:{token}@", 1)


def from_git(url: str, branch: str = "", token: str = "", say=lambda m: None) -> Tuple[Dict[str, str], Dict]:
    """Shallow clone (git CLI); without git, the GitHub / GitLab archive download."""
    url = (url or "").strip()
    if not _GIT_URL.match(url):
        raise SourceError("Enter an https:// Git repository URL (for example https://github.com/org/repo).")
    branch = (branch or "").strip()
    if branch and not re.match(r"^[\w.\-/]+$", branch):
        raise SourceError("The branch name contains characters that are not allowed.")
    git = shutil.which("git")
    if git:
        tmp = tempfile.mkdtemp(prefix="evoke-git-")
        try:
            cmd = [git, "-c", "credential.helper=", "-c", "core.askPass=", "clone", "--depth", "1",
                   "--single-branch", "--no-tags"]
            if branch:
                cmd += ["--branch", branch]
            cmd += [_auth_url(url, token), os.path.join(tmp, "repo")]
            say(f"Cloning {url}" + (f" ({branch})" if branch else "") + "…")
            env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_LFS_SKIP_SMUDGE="1")
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
            if r.returncode != 0:
                msg = _redact((r.stderr or r.stdout or "").strip(), token)
                if "Authentication failed" in msg or "could not read Username" in msg or "403" in msg or "404" in msg:
                    raise SourceError("The repository could not be read. Check the URL and branch; a private "
                                      "repository needs an access token with read access.")
                raise SourceError("git clone failed: " + msg[-600:])
            sha = subprocess.run([git, "rev-parse", "HEAD"], cwd=os.path.join(tmp, "repo"), capture_output=True,
                                 text=True, timeout=30).stdout.strip()
            real_branch = branch or subprocess.run([git, "rev-parse", "--abbrev-ref", "HEAD"],
                                                   cwd=os.path.join(tmp, "repo"), capture_output=True, text=True,
                                                   timeout=30).stdout.strip()
            files, info = from_dir(os.path.join(tmp, "repo"))
            info.update(commit=sha, branch=real_branch)
            return files, info
        except subprocess.TimeoutExpired:
            raise SourceError("git clone took longer than 5 minutes.")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return _archive_download(url, branch, token, say)


def _archive_download(url: str, branch: str, token: str, say) -> Tuple[Dict[str, str], Dict]:
    import requests
    m = re.match(r"https://(github\.com|gitlab\.com)/([^/]+)/([^/]+?)(\.git)?/?$", url)
    if not m:
        raise SourceError("git is not installed on this host; only github.com and gitlab.com repositories "
                          "can be downloaded without it.")
    host, owner, repo = m.group(1), m.group(2), m.group(3)
    ref = branch or "HEAD"
    if host == "github.com":
        api = f"https://api.github.com/repos/{owner}/{repo}/zipball/{ref}"
        headers = {"Authorization": f"Bearer {token}"} if token else {}
    else:
        api = f"https://gitlab.com/api/v4/projects/{owner}%2F{repo}/repository/archive.zip?sha={ref}"
        headers = {"PRIVATE-TOKEN": token} if token else {}
    say(f"Downloading {owner}/{repo} ({ref})…")
    r = requests.get(api, headers=headers, timeout=180)
    if r.status_code != 200:
        raise SourceError(f"Download failed (HTTP {r.status_code}). Check the URL, branch and access token.")
    files, info = from_zip(r.content)
    info.update(branch=branch or "default")
    return files, info


def push_branch(files: Dict[str, str], url: str, base_branch: str, new_branch: str, token: str, message: str,
                author_name: str, author_email: str, say=lambda m: None) -> Dict:
    """Commit the version's files onto `base_branch` as `new_branch` and push it. Files the studio does
    not keep (dependencies, build output) stay untouched in the repository."""
    git = shutil.which("git")
    if not git:
        raise SourceError("git is not available on this host.")
    if not re.match(r"^[\w.\-/]+$", new_branch or ""):
        raise SourceError("Invalid branch name.")
    tmp = tempfile.mkdtemp(prefix="evoke-push-")
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_AUTHOR_NAME=author_name, GIT_AUTHOR_EMAIL=author_email,
               GIT_COMMITTER_NAME=author_name, GIT_COMMITTER_EMAIL=author_email)

    def g(*args, cwd=None, timeout=300):
        r = subprocess.run([git, "-c", "credential.helper=", *args], cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, env=env)
        if r.returncode != 0:
            raise SourceError(f"git {args[0]} failed: " + _redact((r.stderr or r.stdout).strip(), token)[-600:])
        return r.stdout
    try:
        repo = os.path.join(tmp, "repo")
        say(f"Cloning {url} ({base_branch})…")
        g("clone", "--depth", "1", "--branch", base_branch, _auth_url(url, token), repo)
        g("checkout", "-b", new_branch, cwd=repo)
        tracked = set(g("ls-files", cwd=repo).splitlines())
        keep_dist = not _has_sources(list(tracked))
        for p in tracked:                                   # files deleted in the studio
            if _skip_path(p, keep_dist) is None and p not in files and os.path.exists(os.path.join(repo, p)):
                os.remove(os.path.join(repo, p))
        to_dir(files, repo)
        g("add", "-A", cwd=repo)
        status = g("status", "--porcelain", cwd=repo)
        if not status.strip():
            return {"branch": new_branch, "changed": 0, "pushed": False}
        g("commit", "-m", message, cwd=repo)
        say(f"Pushing branch {new_branch}…")
        g("push", "-u", "origin", new_branch, cwd=repo)
        return {"branch": new_branch, "changed": len(status.splitlines()), "pushed": True}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def open_pull_request(url: str, token: str, base: str, head: str, title: str, body: str) -> str:
    """GitHub / GitLab pull (merge) request; returns its web URL ('' when the host is not supported)."""
    import requests
    m = re.match(r"https://(github\.com|gitlab\.com)/([^/]+)/([^/]+?)(\.git)?/?$", url)
    if not m or not token:
        return ""
    host, owner, repo = m.group(1), m.group(2), m.group(3)
    if host == "github.com":
        r = requests.post(f"https://api.github.com/repos/{owner}/{repo}/pulls", timeout=60,
                          headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                          json={"title": title, "head": head, "base": base, "body": body})
        return r.json().get("html_url", "") if r.status_code in (200, 201) else ""
    r = requests.post(f"https://gitlab.com/api/v4/projects/{owner}%2F{repo}/merge_requests", timeout=60,
                      headers={"PRIVATE-TOKEN": token},
                      json={"title": title, "source_branch": head, "target_branch": base, "description": body})
    return r.json().get("web_url", "") if r.status_code in (200, 201) else ""


# ─────────────────────────────────────────────────────────────────────────────
# Cloud Foundry
# ─────────────────────────────────────────────────────────────────────────────
def from_cf(app_name: str, cf_home: Optional[str]) -> Tuple[Dict[str, str], Dict]:
    import app_import
    try:
        data = app_import.download_cf_app(app_name, cf_home=cf_home)
    except app_import.ImportError_ as e:
        raise SourceError(str(e))
    files, info = from_zip(data)
    # the droplet of a Node.js / Python app has the app under app/ — unwrap it
    if all(p.startswith("app/") for p in files) and any(p in files for p in ("app/package.json", "app/manifest.json",
                                                                              "app/requirements.txt")):
        files = {p[4:]: c for p, c in files.items()}
    info.update(cf_app=app_name)
    return files, info


# ─────────────────────────────────────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────────────────────────────────────
def to_dir(files: Dict[str, str], base: str) -> None:
    for p, c in files.items():
        if ".." in p.split("/") or p.startswith("/"):
            continue
        fp = os.path.join(base, *p.split("/"))
        os.makedirs(os.path.dirname(fp) or base, exist_ok=True)
        with open(fp, "wb") as f:
            f.write(decode(c))


def to_zip(files: Dict[str, str], root: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(files):
            zf.writestr(root + p, decode(files[p]))
    return buf.getvalue()


def text_files(files: Dict[str, str]) -> Dict[str, str]:
    return {p: c for p, c in files.items() if not is_binary(c)}
