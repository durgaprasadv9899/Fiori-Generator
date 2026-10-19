"""
workspace.py — persistent project workspace for the studio.

Projects, versions (studio snapshot + generated files), deployments, release
approvals and an audit trail, so users can return to an application instead of
relying on one browser session.

    project ──< version (snapshot JSON + files ZIP blob, sha, quality, validation)
        ├──< deployment (env, target, status, url, log tail)
        ├──< approval   (version + env, requester, decider, status)
        └──< audit      (who did what, when)

Storage: SQLite (WAL) at $STUDIO_DATA_DIR/workspace.db — default ~/.evoke-studio.
On Cloud Foundry the container filesystem is ephemeral (lost on restage/restart),
so `store.durable` is False there unless STUDIO_DATA_DIR points at a mounted
volume (set STUDIO_DATA_DURABLE=1). A managed database is the seam for later:
DATABASE_URL selects PostgresStore (see the stub below) — not enabled until a
database service is chosen.
"""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import io
import json
import os
import sqlite3
import threading
import time
import uuid
import zipfile
from typing import Any, Dict, List, Optional

PROJECT_MODES = ("sap", "standalone", "import", "adopted")
MEMBER_ROLES = ("viewer", "developer", "reviewer")
APPROVAL_STATES = ("pending", "approved", "rejected", "imported")
_DIFF_CAP = 60_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, owner TEXT NOT NULL, mode TEXT NOT NULL,
    app_id TEXT DEFAULT '', description TEXT DEFAULT '', created_at REAL, updated_at REAL,
    archived INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS members (
    project_id TEXT NOT NULL, user TEXT NOT NULL, role TEXT NOT NULL, added_at REAL,
    PRIMARY KEY (project_id, user));
CREATE TABLE IF NOT EXISTS versions (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, number INTEGER NOT NULL, label TEXT DEFAULT '',
    author TEXT NOT NULL, created_at REAL, sha TEXT, file_count INTEGER, files_zip BLOB,
    snapshot TEXT, quality TEXT, validation TEXT, UNIQUE (project_id, number));
CREATE TABLE IF NOT EXISTS deployments (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, version_id TEXT, env TEXT, target TEXT,
    user TEXT, status TEXT, app_url TEXT DEFAULT '', log_tail TEXT DEFAULT '', at REAL);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, version_id TEXT NOT NULL, env TEXT NOT NULL,
    requester TEXT NOT NULL, note TEXT DEFAULT '', status TEXT NOT NULL, requested_at REAL,
    decided_by TEXT DEFAULT '', decided_at REAL, comment TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL, user TEXT, project_id TEXT, action TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS ix_versions_project ON versions (project_id, number);
CREATE INDEX IF NOT EXISTS ix_audit_project ON audit (project_id, id);
CREATE INDEX IF NOT EXISTS ix_approvals_status ON approvals (status);
"""


def _uid(user: Any) -> str:
    """Accept a user id string or an access_control.User."""
    return str(getattr(user, "id", user) or "").strip()


def _is_admin(user: Any) -> bool:
    return "admin" in (getattr(user, "roles", None) or ())


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


def _j(v) -> Optional[str]:
    return None if v is None else json.dumps(v, ensure_ascii=False, separators=(",", ":"), default=str)


def _uj(s):
    return None if s in (None, "") else json.loads(s)


def files_sha(files: Dict[str, str]) -> str:
    h = hashlib.sha256()
    for p in sorted(files):
        h.update(p.encode("utf-8"))
        h.update(b"\0")
        c = files[p]
        h.update(c if isinstance(c, bytes) else str(c).encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def _zip_files(files: Dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(files):
            zf.writestr(p, files[p])
    return buf.getvalue()


def _unzip_files(blob: Optional[bytes]) -> Dict[str, str]:
    if not blob:
        return {}
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return {n: zf.read(n).decode("utf-8", "replace") for n in zf.namelist()}


def _default_data_dir() -> str:
    return os.environ.get("STUDIO_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".evoke-studio")


class Store:
    """SQLite workspace. Thread-safe: one connection per call, a process lock around writes."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or os.path.join(_default_data_dir(), "workspace.db")
        os.makedirs(os.path.dirname(os.path.abspath(self.path)) or ".", exist_ok=True)
        self._lock = threading.RLock()
        on_cf = bool(os.environ.get("VCAP_APPLICATION"))
        self.durable = not on_cf or os.environ.get("STUDIO_DATA_DURABLE", "") == "1"
        self.durability_note = "" if self.durable else (
            "Projects are saved in the studio container's local disk, which Cloud Foundry wipes on every "
            "restart or redeploy. Export important projects (ZIP) until a managed database is connected.")
        with self._conn() as c:
            c.executescript(_SCHEMA)

    # ── plumbing ────────────────────────────────────────────────────────────
    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        return _Closing(c)

    @contextlib.contextmanager
    def _tx(self):
        """Write transaction (process lock + BEGIN IMMEDIATE so other processes wait)."""
        with self._lock:
            c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            c.row_factory = sqlite3.Row
            try:
                c.execute("PRAGMA foreign_keys=ON")
                c.execute("BEGIN IMMEDIATE")
                try:
                    yield c
                except BaseException:
                    c.execute("ROLLBACK")
                    raise
                c.execute("COMMIT")
            finally:
                c.close()

    def _audit(self, c, user, project_id, action: str, detail=None) -> None:
        c.execute("INSERT INTO audit (at, user, project_id, action, detail) VALUES (?,?,?,?,?)",
                  (time.time(), _uid(user), project_id or "", action, _j(detail or {})))

    @staticmethod
    def _project(r) -> Dict:
        return dict(r, archived=bool(r["archived"])) if r else None

    @staticmethod
    def _version(r, with_files: bool) -> Optional[Dict]:
        if not r:
            return None
        d = {k: r[k] for k in ("id", "project_id", "number", "label", "author", "created_at", "sha", "file_count")}
        d["quality"] = _uj(r["quality"])
        d["validation"] = _uj(r["validation"])
        if with_files:
            d["snapshot"] = _uj(r["snapshot"]) or {}
            d["files"] = _unzip_files(r["files_zip"])
        return d

    # ── projects ────────────────────────────────────────────────────────────
    def create_project(self, name: str, owner, mode: str, app_id: str = "", description: str = "") -> Dict:
        name, owner_id = (name or "").strip(), _uid(owner)
        if not name:
            raise ValueError("A project needs a name.")
        if not owner_id:
            raise ValueError("A project needs an owner.")
        if mode not in PROJECT_MODES:
            raise ValueError(f"Unknown project mode '{mode}' (use {', '.join(PROJECT_MODES)}).")
        pid, now = _new_id(), time.time()
        with self._tx() as c:
            c.execute("INSERT INTO projects (id, name, owner, mode, app_id, description, created_at, updated_at, "
                      "archived) VALUES (?,?,?,?,?,?,?,?,0)", (pid, name, owner_id, mode, app_id, description, now, now))
            self._audit(c, owner_id, pid, "project.create", {"name": name, "mode": mode, "app_id": app_id})
        return self.get_project(pid)

    def get_project(self, project_id: str) -> Optional[Dict]:
        with self._conn() as c:
            r = c.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if not r:
                return None
            p = self._project(r)
            p["members"] = [dict(m) for m in c.execute(
                "SELECT user, role, added_at FROM members WHERE project_id=? ORDER BY user", (project_id,))]
            lv = c.execute("SELECT number, created_at FROM versions WHERE project_id=? ORDER BY number DESC LIMIT 1",
                           (project_id,)).fetchone()
            p["latest_version"] = lv["number"] if lv else 0
        return p

    def list_projects(self, user, include_archived: bool = False, see_all: bool = False) -> List[Dict]:
        """Projects the user owns or is a member of; admins (or see_all) get every project."""
        uid = _uid(user)
        q = ("SELECT p.*, (SELECT MAX(number) FROM versions v WHERE v.project_id=p.id) AS latest_version "
             "FROM projects p")
        args: list = []
        conds = []
        if not (see_all or _is_admin(user)):
            conds.append("(p.owner=? OR EXISTS (SELECT 1 FROM members m WHERE m.project_id=p.id AND m.user=?))")
            args += [uid, uid]
        if not include_archived:
            conds.append("p.archived=0")
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY p.updated_at DESC"
        with self._conn() as c:
            return [dict(self._project(r), latest_version=r["latest_version"] or 0) for r in c.execute(q, args)]

    def project_role(self, project_id: str, user) -> str:
        """'owner', the member role, or '' (no access through the project)."""
        uid = _uid(user)
        with self._conn() as c:
            r = c.execute("SELECT owner FROM projects WHERE id=?", (project_id,)).fetchone()
            if not r:
                return ""
            if r["owner"] == uid:
                return "owner"
            m = c.execute("SELECT role FROM members WHERE project_id=? AND user=?", (project_id, uid)).fetchone()
        return m["role"] if m else ""

    def _touch(self, c, project_id):
        c.execute("UPDATE projects SET updated_at=? WHERE id=?", (time.time(), project_id))

    def _need_project(self, c, project_id) -> sqlite3.Row:
        r = c.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not r:
            raise KeyError(f"Project {project_id} not found.")
        return r

    def rename_project(self, project_id: str, new_name: str, user) -> Dict:
        new_name = (new_name or "").strip()
        if not new_name:
            raise ValueError("A project needs a name.")
        with self._tx() as c:
            old = self._need_project(c, project_id)["name"]
            c.execute("UPDATE projects SET name=?, updated_at=? WHERE id=?", (new_name, time.time(), project_id))
            self._audit(c, user, project_id, "project.rename", {"from": old, "to": new_name})
        return self.get_project(project_id)

    def update_project(self, project_id: str, user, *, app_id: Optional[str] = None,
                       description: Optional[str] = None) -> Dict:
        with self._tx() as c:
            self._need_project(c, project_id)
            changes = {}
            if app_id is not None:
                c.execute("UPDATE projects SET app_id=? WHERE id=?", (app_id, project_id))
                changes["app_id"] = app_id
            if description is not None:
                c.execute("UPDATE projects SET description=? WHERE id=?", (description, project_id))
                changes["description"] = description[:200]
            self._touch(c, project_id)
            self._audit(c, user, project_id, "project.update", changes)
        return self.get_project(project_id)

    def archive_project(self, project_id: str, user, archived: bool = True) -> Dict:
        with self._tx() as c:
            self._need_project(c, project_id)
            c.execute("UPDATE projects SET archived=?, updated_at=? WHERE id=?",
                      (1 if archived else 0, time.time(), project_id))
            self._audit(c, user, project_id, "project.archive" if archived else "project.unarchive")
        return self.get_project(project_id)

    def delete_project(self, project_id: str, user) -> Dict:
        """Permanently delete a project with its versions, deployment records, approvals and members.
        The audit trail is kept (with the project's name) — who deleted what stays traceable."""
        with self._tx() as c:
            p = self._need_project(c, project_id)
            counts = {t: c.execute(f"SELECT COUNT(*) FROM {t} WHERE project_id=?", (project_id,)).fetchone()[0]
                      for t in ("versions", "deployments", "approvals")}
            for t in ("approvals", "deployments", "versions", "members"):
                c.execute(f"DELETE FROM {t} WHERE project_id=?", (project_id,))
            c.execute("DELETE FROM projects WHERE id=?", (project_id,))
            self._audit(c, user, project_id, "project.delete",
                        {"name": p["name"], "app_id": p["app_id"], "mode": p["mode"], **counts})
        return {"id": project_id, "name": p["name"], "deleted": counts}

    def delete_version(self, version_id: str, user) -> Dict:
        """Delete one version (and approvals for it). The project's last version cannot be deleted —
        delete the project instead."""
        with self._tx() as c:
            r = c.execute("SELECT project_id, number FROM versions WHERE id=?", (version_id,)).fetchone()
            if not r:
                raise KeyError(f"Version {version_id} not found.")
            n = c.execute("SELECT COUNT(*) FROM versions WHERE project_id=?", (r["project_id"],)).fetchone()[0]
            if n <= 1:
                raise ValueError("This is the project's only version — delete the project instead.")
            c.execute("DELETE FROM approvals WHERE version_id=?", (version_id,))
            c.execute("UPDATE deployments SET version_id=NULL WHERE version_id=?", (version_id,))
            c.execute("DELETE FROM versions WHERE id=?", (version_id,))
            self._touch(c, r["project_id"])
            self._audit(c, user, r["project_id"], "version.delete", {"version": r["number"]})
        return {"id": version_id, "project_id": r["project_id"], "number": r["number"]}

    def add_member(self, project_id: str, user: str, role: str, by=None) -> Dict:
        if role not in MEMBER_ROLES:
            raise ValueError(f"Unknown project role '{role}' (use {', '.join(MEMBER_ROLES)}).")
        uid = _uid(user)
        if not uid:
            raise ValueError("Member user id is empty.")
        with self._tx() as c:
            self._need_project(c, project_id)
            c.execute("INSERT INTO members (project_id, user, role, added_at) VALUES (?,?,?,?) "
                      "ON CONFLICT (project_id, user) DO UPDATE SET role=excluded.role",
                      (project_id, uid, role, time.time()))
            self._audit(c, by or uid, project_id, "member.set", {"user": uid, "role": role})
        return self.get_project(project_id)

    def remove_member(self, project_id: str, user: str, by=None) -> Dict:
        uid = _uid(user)
        with self._tx() as c:
            c.execute("DELETE FROM members WHERE project_id=? AND user=?", (project_id, uid))
            self._audit(c, by or uid, project_id, "member.remove", {"user": uid})
        return self.get_project(project_id)

    def duplicate_project(self, project_id: str, new_name: str, user) -> Dict:
        """New project owned by `user` with a copy of the latest version (history is not copied)."""
        src = self.get_project(project_id)
        if not src:
            raise KeyError(f"Project {project_id} not found.")
        new = self.create_project(new_name or f"{src['name']} (copy)", user, src["mode"], src["app_id"],
                                  src["description"])
        latest = self.latest_version(project_id)
        if latest:
            self.save_version(new["id"], user, latest["snapshot"], latest["files"],
                              label=f"copied from {src['name']} v{latest['number']}",
                              quality=latest["quality"], validation=latest["validation"])
        with self._tx() as c:
            self._audit(c, user, project_id, "project.duplicated", {"new_project": new["id"]})
            self._audit(c, user, new["id"], "project.duplicate_of",
                        {"source": project_id, "version": latest["number"] if latest else None})
        return self.get_project(new["id"])

    # ── versions ────────────────────────────────────────────────────────────
    def save_version(self, project_id: str, user, snapshot: Dict, files: Dict[str, str], label: str = "",
                     quality: Optional[Dict] = None, validation: Optional[Dict] = None) -> Dict:
        files = dict(files or {})
        vid, sha = _new_id(), files_sha(files)
        with self._tx() as c:
            self._need_project(c, project_id)
            n = (c.execute("SELECT MAX(number) FROM versions WHERE project_id=?", (project_id,)).fetchone()[0] or 0) + 1
            c.execute("INSERT INTO versions (id, project_id, number, label, author, created_at, sha, file_count, "
                      "files_zip, snapshot, quality, validation) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (vid, project_id, n, label or "", _uid(user), time.time(), sha, len(files),
                       sqlite3.Binary(_zip_files(files)), _j(snapshot or {}), _j(quality), _j(validation)))
            self._touch(c, project_id)
            self._audit(c, user, project_id, "version.save",
                        {"version": n, "version_id": vid, "files": len(files), "sha": sha[:12], "label": label})
        return self.get_version(vid, with_files=False)

    def set_validation(self, version_id: str, validation: Dict, user) -> Dict:
        """Attach a (later) validation run to a stored version."""
        with self._tx() as c:
            r = c.execute("SELECT project_id, number FROM versions WHERE id=?", (version_id,)).fetchone()
            if not r:
                raise KeyError(f"Version {version_id} not found.")
            c.execute("UPDATE versions SET validation=? WHERE id=?", (_j(validation), version_id))
            self._audit(c, user, r["project_id"], "version.validated",
                        {"version": r["number"], "blocking": bool((validation or {}).get("blocking"))})
        return self.get_version(version_id, with_files=False)

    def list_versions(self, project_id: str) -> List[Dict]:
        with self._conn() as c:
            return [self._version(r, False) for r in c.execute(
                "SELECT id, project_id, number, label, author, created_at, sha, file_count, quality, validation "
                "FROM versions WHERE project_id=? ORDER BY number DESC", (project_id,))]

    def get_version(self, version_id: str, with_files: bool = True) -> Optional[Dict]:
        with self._conn() as c:
            r = c.execute("SELECT * FROM versions WHERE id=?", (version_id,)).fetchone()
        return self._version(r, with_files)

    def latest_version(self, project_id: str, with_files: bool = True) -> Optional[Dict]:
        with self._conn() as c:
            r = c.execute("SELECT * FROM versions WHERE project_id=? ORDER BY number DESC LIMIT 1",
                          (project_id,)).fetchone()
        return self._version(r, with_files)

    def restore_version(self, version_id: str, user=None) -> Optional[Dict]:
        v = self.get_version(version_id, with_files=True)
        if v and user is not None:
            with self._tx() as c:
                self._audit(c, user, v["project_id"], "version.restore", {"version": v["number"]})
        return v

    def compare_versions(self, a_id: str, b_id: str) -> Dict:
        a, b = self.get_version(a_id), self.get_version(b_id)
        if not a or not b:
            raise KeyError("Version not found.")
        fa, fb = a["files"], b["files"]
        added = sorted(set(fb) - set(fa))
        removed = sorted(set(fa) - set(fb))
        changed = sorted(p for p in set(fa) & set(fb) if fa[p] != fb[p])
        parts, size = [], 0
        for p in changed + added + removed:
            d = "".join(difflib.unified_diff(fa.get(p, "").splitlines(True), fb.get(p, "").splitlines(True),
                                             f"v{a['number']}/{p}", f"v{b['number']}/{p}", n=2))
            if size + len(d) > _DIFF_CAP:
                parts.append(f"\n… diff truncated ({len(changed) + len(added) + len(removed)} files differ)\n")
                break
            parts.append(d if d.endswith("\n") or not d else d + "\n")
            size += len(d)
        return {"a": {"id": a["id"], "number": a["number"]}, "b": {"id": b["id"], "number": b["number"]},
                "added": added, "removed": removed, "changed": changed, "diff": "".join(parts),
                "blueprint_changes": blueprint_changes(_bp_of(a["snapshot"]), _bp_of(b["snapshot"]))}

    # ── deployments ─────────────────────────────────────────────────────────
    def record_deployment(self, project_id: str, version_id: Optional[str], env: str, target: Dict, user,
                          status: str, app_url: str = "", log_tail: str = "") -> Dict:
        did = _new_id()
        with self._tx() as c:
            self._need_project(c, project_id)
            c.execute("INSERT INTO deployments (id, project_id, version_id, env, target, user, status, app_url, "
                      "log_tail, at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (did, project_id, version_id, env, _j(target or {}), _uid(user), status, app_url,
                       (log_tail or "")[-8000:], time.time()))
            self._touch(c, project_id)
            self._audit(c, user, project_id, "deploy." + (status or "unknown"),
                        {"env": env, "version_id": version_id, "app_url": app_url,
                         "target": {k: v for k, v in (target or {}).items() if k in ("api", "org", "space", "app")}})
        return self.list_deployments(project_id)[0]

    def list_deployments(self, project_id: str) -> List[Dict]:
        with self._conn() as c:
            rows = c.execute("SELECT d.*, v.number AS version_number FROM deployments d LEFT JOIN versions v "
                             "ON v.id=d.version_id WHERE d.project_id=? ORDER BY d.at DESC", (project_id,))
            return [dict(r, target=_uj(r["target"]) or {}) for r in rows]

    # ── approvals ───────────────────────────────────────────────────────────
    def request_approval(self, project_id: str, version_id: str, env: str, user, note: str = "") -> Dict:
        aid = _new_id()
        with self._tx() as c:
            self._need_project(c, project_id)
            v = c.execute("SELECT number FROM versions WHERE id=? AND project_id=?", (version_id, project_id)).fetchone()
            if not v:
                raise KeyError("That version does not belong to the project.")
            dup = c.execute("SELECT id FROM approvals WHERE project_id=? AND version_id=? AND env=? AND status='pending'",
                            (project_id, version_id, env)).fetchone()
            if dup:
                return self.get_approval(dup["id"])
            c.execute("INSERT INTO approvals (id, project_id, version_id, env, requester, note, status, requested_at) "
                      "VALUES (?,?,?,?,?,?,'pending',?)", (aid, project_id, version_id, env, _uid(user), note, time.time()))
            self._audit(c, user, project_id, "approval.request", {"approval": aid, "version": v["number"], "env": env,
                                                                  "note": note[:200]})
        return self.get_approval(aid)

    def get_approval(self, approval_id: str) -> Optional[Dict]:
        with self._conn() as c:
            r = c.execute("SELECT a.*, v.number AS version_number, v.author AS version_author, p.name AS project_name "
                          "FROM approvals a LEFT JOIN versions v ON v.id=a.version_id "
                          "LEFT JOIN projects p ON p.id=a.project_id WHERE a.id=?", (approval_id,)).fetchone()
        return dict(r) if r else None

    def decide_approval(self, approval_id: str, user, approve: bool, comment: str = "") -> Dict:
        """Four-eyes: neither the requester nor the version's author may decide."""
        uid = _uid(user)
        with self._tx() as c:
            a = c.execute("SELECT a.*, v.author AS version_author FROM approvals a LEFT JOIN versions v "
                          "ON v.id=a.version_id WHERE a.id=?", (approval_id,)).fetchone()
            if not a:
                raise KeyError(f"Approval {approval_id} not found.")
            if a["status"] != "pending":
                raise ValueError(f"This request was already {a['status']}.")
            if uid == a["requester"]:
                raise PermissionError("You requested this release — another reviewer has to approve or reject it.")
            if uid == a["version_author"]:
                raise PermissionError("You authored this version — another reviewer has to approve or reject it.")
            if not approve and not (comment or "").strip():
                raise ValueError("Give a reason when rejecting.")
            status = "approved" if approve else "rejected"
            c.execute("UPDATE approvals SET status=?, decided_by=?, decided_at=?, comment=? WHERE id=?",
                      (status, uid, time.time(), comment or "", approval_id))
            self._audit(c, uid, a["project_id"], "approval." + ("approve" if approve else "reject"),
                        {"approval": approval_id, "env": a["env"], "version_id": a["version_id"],
                         "comment": (comment or "")[:300]})
        return self.get_approval(approval_id)

    def approval_for(self, project_id: str, version_id: str, env: str) -> Optional[Dict]:
        with self._conn() as c:
            r = c.execute("SELECT id FROM approvals WHERE project_id=? AND version_id=? AND env=? "
                          "ORDER BY requested_at DESC LIMIT 1", (project_id, version_id, env)).fetchone()
        return self.get_approval(r["id"]) if r else None

    def list_approvals(self, status: Optional[str] = "pending", user=None, project_id: Optional[str] = None) -> List[Dict]:
        """`user` filters to requests that user may decide (not requested/authored by them)."""
        q = ("SELECT a.*, v.number AS version_number, v.author AS version_author, p.name AS project_name "
             "FROM approvals a LEFT JOIN versions v ON v.id=a.version_id LEFT JOIN projects p ON p.id=a.project_id")
        conds, args = [], []
        if status:
            conds.append("a.status=?")
            args.append(status)
        if project_id:
            conds.append("a.project_id=?")
            args.append(project_id)
        if user is not None:
            conds.append("a.requester<>? AND COALESCE(v.author,'')<>?")
            args += [_uid(user), _uid(user)]
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY a.requested_at DESC"
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, args)]

    # ── audit ───────────────────────────────────────────────────────────────
    def audit(self, user, project_id: Optional[str], action: str, detail: Optional[Dict] = None) -> None:
        """Record a studio event that is not a store mutation (sign-in, generation, validation run…)."""
        with self._tx() as c:
            self._audit(c, user, project_id, action, detail)

    def list_audit(self, project_id: Optional[str] = None, limit: int = 200) -> List[Dict]:
        q, args = "SELECT * FROM audit", []
        if project_id:
            q += " WHERE project_id=?"
            args.append(project_id)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        with self._conn() as c:
            return [dict(r, detail=_uj(r["detail"]) or {}) for r in c.execute(q, args)]

    # ── export / import ─────────────────────────────────────────────────────
    def export_project(self, project_id: str, user=None) -> bytes:
        p = self.get_project(project_id)
        if not p:
            raise KeyError(f"Project {project_id} not found.")
        buf = io.BytesIO()
        with self._conn() as c, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            versions = c.execute("SELECT * FROM versions WHERE project_id=? ORDER BY number", (project_id,)).fetchall()
            zf.writestr("project.json", json.dumps(dict(p, format="evoke-studio-project/1", exported_at=time.time(),
                                                        versions=[{k: r[k] for k in ("number", "label", "author",
                                                                                     "created_at", "sha", "file_count")}
                                                                  for r in versions]), indent=2, default=str))
            for r in versions:
                base = f"versions/{r['number']}/"
                zf.writestr(base + "files.zip", bytes(r["files_zip"] or b""))
                zf.writestr(base + "snapshot.json", r["snapshot"] or "{}")
                zf.writestr(base + "meta.json", json.dumps({"label": r["label"], "author": r["author"],
                                                            "created_at": r["created_at"], "sha": r["sha"],
                                                            "quality": _uj(r["quality"]),
                                                            "validation": _uj(r["validation"])}, default=str))
            num = {r["id"]: r["number"] for r in versions}
            deps = [dict(d, version_number=num.get(d["version_id"])) for d in self.list_deployments(project_id)]
            zf.writestr("deployments.json", json.dumps(deps, indent=2, default=str))
            apps = [dict(a, version_number=num.get(a["version_id"])) for a in self.list_approvals(None, project_id=project_id)]
            zf.writestr("approvals.json", json.dumps(apps, indent=2, default=str))
            zf.writestr("audit.json", json.dumps(self.list_audit(project_id, limit=100000), indent=2, default=str))
        if user is not None:
            self.audit(user, project_id, "project.export")
        return buf.getvalue()

    def import_project(self, zip_bytes: bytes, user, name: Optional[str] = None) -> Dict:
        """New project owned by `user`. History (deployments, approvals, audit) is kept for reference, but
        imported approvals never count as release approvals (status 'imported')."""
        try:
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        except zipfile.BadZipFile as e:
            raise ValueError(f"Not a project export: {e}") from e
        with zf:
            names = set(zf.namelist())
            if "project.json" not in names:
                raise ValueError("Not a studio project export (project.json missing).")
            meta = json.loads(zf.read("project.json"))
            mode = meta.get("mode") if meta.get("mode") in PROJECT_MODES else "import"
            new = self.create_project(name or meta.get("name") or "Imported project", user, mode,
                                      meta.get("app_id", ""), meta.get("description", ""))
            pid = new["id"]
            nums = sorted({int(n.split("/")[1]) for n in names if n.startswith("versions/") and n.count("/") >= 2})
            vmap = {}
            with self._tx() as c:
                for n in nums:
                    base = f"versions/{n}/"
                    vm = json.loads(zf.read(base + "meta.json")) if base + "meta.json" in names else {}
                    blob = zf.read(base + "files.zip") if base + "files.zip" in names else _zip_files({})
                    files = _unzip_files(blob)
                    vid = _new_id()
                    vmap[n] = vid
                    c.execute("INSERT INTO versions (id, project_id, number, label, author, created_at, sha, file_count, "
                              "files_zip, snapshot, quality, validation) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                              (vid, pid, n, vm.get("label", ""), vm.get("author") or _uid(user),
                               vm.get("created_at") or time.time(), files_sha(files), len(files), sqlite3.Binary(blob),
                               zf.read(base + "snapshot.json").decode("utf-8") if base + "snapshot.json" in names else "{}",
                               _j(vm.get("quality")), _j(vm.get("validation"))))
                for d in json.loads(zf.read("deployments.json")) if "deployments.json" in names else []:
                    c.execute("INSERT INTO deployments (id, project_id, version_id, env, target, user, status, app_url, "
                              "log_tail, at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                              (_new_id(), pid, vmap.get(d.get("version_number")), d.get("env"), _j(d.get("target") or {}),
                               d.get("user"), d.get("status"), d.get("app_url", ""), d.get("log_tail", ""), d.get("at")))
                for a in json.loads(zf.read("approvals.json")) if "approvals.json" in names else []:
                    if not vmap.get(a.get("version_number")):
                        continue
                    c.execute("INSERT INTO approvals (id, project_id, version_id, env, requester, note, status, "
                              "requested_at, decided_by, decided_at, comment) VALUES (?,?,?,?,?,?,'imported',?,?,?,?)",
                              (_new_id(), pid, vmap[a["version_number"]], a.get("env", ""), a.get("requester", ""),
                               a.get("note", ""), a.get("requested_at"), a.get("decided_by", ""), a.get("decided_at"),
                               "imported (was " + str(a.get("status")) + ") " + (a.get("comment") or "")))
                for e in reversed(json.loads(zf.read("audit.json")) if "audit.json" in names else []):
                    c.execute("INSERT INTO audit (at, user, project_id, action, detail) VALUES (?,?,?,?,?)",
                              (e.get("at"), e.get("user"), pid, "history:" + str(e.get("action")), _j(e.get("detail"))))
                self._audit(c, user, pid, "project.import", {"source_project": meta.get("id"), "versions": len(nums)})
        return self.get_project(pid)


class _Closing:
    """sqlite3 connection as a context manager that also closes (sqlite3's own only commits)."""

    def __init__(self, conn):
        self._c = conn

    def __getattr__(self, n):
        return getattr(self._c, n)

    def __enter__(self):
        return self._c

    def __exit__(self, *exc):
        self._c.close()
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Blueprint comparison (human-readable)
# ─────────────────────────────────────────────────────────────────────────────
def _bp_of(snapshot: Dict) -> Dict:
    s = snapshot or {}
    return s.get("blueprint") or (s.get("design") or {}).get("blueprint") or {}


def _bp_facets(bp: Dict) -> Dict[str, set]:
    pages, sets, actions = set(), set(), set()

    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get("entity_set"), str) and o["entity_set"]:
                sets.add(o["entity_set"])
            acts = o.get("actions")
            for a in acts if isinstance(acts, list) else []:
                if isinstance(a, dict):
                    actions.add(a.get("label") or a.get("operation") or a.get("id") or "?")
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(bp)
    for p in bp.get("pages") or []:
        if isinstance(p, dict):
            pages.add(f"{p.get('type', 'page')} '{p.get('title') or p.get('id') or ''}'")
    return {"pages": pages, "entity sets": sets, "actions": actions}


def blueprint_changes(a: Dict, b: Dict) -> List[str]:
    out = []
    ta, tb = ((a.get("app") or {}).get("title"), (b.get("app") or {}).get("title"))
    if ta != tb:
        out.append(f"title: '{ta or ''}' → '{tb or ''}'")
    if a.get("floorplan") != b.get("floorplan"):
        out.append(f"floorplan: {a.get('floorplan') or '—'} → {b.get('floorplan') or '—'}")
    fa, fb = _bp_facets(a), _bp_facets(b)
    for k in fa:
        add, rem = sorted(fb[k] - fa[k]), sorted(fa[k] - fb[k])
        if add:
            out.append(f"{k} added: {', '.join(add)}")
        if rem:
            out.append(f"{k} removed: {', '.join(rem)}")
    la, lb = (a.get("app") or {}).get("languages"), (b.get("app") or {}).get("languages")
    if la != lb and (la or lb):
        out.append(f"languages: {', '.join(la or [])} → {', '.join(lb or [])}")
    if not out and a != b:
        out.append("details changed (fields, columns, logic or layout)")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Managed database seam
# ─────────────────────────────────────────────────────────────────────────────
class PostgresStore(Store):  # pragma: no cover - not enabled yet
    """Placeholder for a managed database (e.g. PostgreSQL on SAP BTP, hyperscaler option).

    Enabling it needs a decision + a service instance bound to the studio (DATABASE_URL). The Store
    SQL is portable except: '?' placeholders (psycopg uses %s), BLOB→BYTEA, AUTOINCREMENT→IDENTITY,
    PRAGMA lines, ON CONFLICT (supported). Implement _conn() returning a psycopg connection wrapper
    that rewrites placeholders, and translate _SCHEMA."""

    def __init__(self, url: str):
        raise NotImplementedError("PostgreSQL storage is not enabled yet.")


# ─────────────────────────────────────────────────────────────────────────────
# SAP HANA Cloud (schema service instance bound to the studio)
# ─────────────────────────────────────────────────────────────────────────────
_HANA_TEXT = "NVARCHAR(500)"
_HANA_DDL = {
    "PROJECTS": "id NVARCHAR(64) PRIMARY KEY, name NVARCHAR(500) NOT NULL, owner NVARCHAR(500) NOT NULL, "
                "mode NVARCHAR(32) NOT NULL, app_id NVARCHAR(500) DEFAULT '', description NCLOB, "
                "created_at DOUBLE, updated_at DOUBLE, archived INTEGER DEFAULT 0",
    "MEMBERS": "project_id NVARCHAR(64) NOT NULL, \"USER\" NVARCHAR(500) NOT NULL, role NVARCHAR(32) NOT NULL, "
               "added_at DOUBLE, PRIMARY KEY (project_id, \"USER\")",
    "VERSIONS": "id NVARCHAR(64) PRIMARY KEY, project_id NVARCHAR(64) NOT NULL, \"NUMBER\" INTEGER NOT NULL, "
                "label NVARCHAR(500) DEFAULT '', author NVARCHAR(500) NOT NULL, created_at DOUBLE, sha NVARCHAR(128), "
                "file_count INTEGER, files_zip BLOB, snapshot NCLOB, quality NCLOB, validation NCLOB, "
                "UNIQUE (project_id, \"NUMBER\")",
    "DEPLOYMENTS": "id NVARCHAR(64) PRIMARY KEY, project_id NVARCHAR(64) NOT NULL, version_id NVARCHAR(64), "
                   "env NVARCHAR(32), target NCLOB, \"USER\" NVARCHAR(500), status NVARCHAR(32), "
                   "app_url NVARCHAR(1000) DEFAULT '', log_tail NCLOB, \"AT\" DOUBLE",
    "APPROVALS": "id NVARCHAR(64) PRIMARY KEY, project_id NVARCHAR(64) NOT NULL, version_id NVARCHAR(64) NOT NULL, "
                 "env NVARCHAR(32) NOT NULL, requester NVARCHAR(500) NOT NULL, note NCLOB, status NVARCHAR(32) NOT NULL, "
                 "requested_at DOUBLE, decided_by NVARCHAR(500) DEFAULT '', decided_at DOUBLE, \"COMMENT\" NCLOB",
    "AUDIT": "id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, \"AT\" DOUBLE, \"USER\" NVARCHAR(500), "
             "project_id NVARCHAR(64), action NVARCHAR(100), detail NCLOB",
}
# identifiers that are reserved words in HANA SQL -> quoted (upper case = the unquoted DDL name)
_HANA_RESERVED = __import__("re").compile(r'(?<![\w"])(user|at|number|comment)(?![\w"(])', __import__("re").I)
_UPSERT = __import__("re").compile(r"INSERT INTO (\w+) \(([^)]*)\) VALUES \(([^)]*)\)\s*ON CONFLICT \([^)]*\) DO UPDATE SET .*",
                                    __import__("re").S)


def _hana_sql(sql: str) -> str:
    m = _UPSERT.match(sql.strip())
    if m:                                       # SQLite upsert -> HANA UPSERT … WITH PRIMARY KEY
        sql = f"UPSERT {m.group(1)} ({m.group(2)}) VALUES ({m.group(3)}) WITH PRIMARY KEY"
    return _HANA_RESERVED.sub(lambda mm: '"' + mm.group(1).upper() + '"', sql)


class _Row(dict):
    """sqlite3.Row look-alike: r["col"], r[0], dict(r)."""

    def __init__(self, cols, values):
        super().__init__(zip(cols, values))
        self._vals = values

    def __getitem__(self, k):
        return self._vals[k] if isinstance(k, int) else super().__getitem__(k)


def _hana_value(v):
    if hasattr(v, "read"):                      # LOB locator
        v = v.read()
    if isinstance(v, memoryview):
        v = v.tobytes()
    return v


class _HanaCursor:
    def __init__(self, cur):
        self._cur = cur

    def _rows(self, raw):
        cols = [d[0].lower() for d in (self._cur.description or [])]
        return [_Row(cols, [_hana_value(v) for v in r]) for r in raw]

    def fetchone(self):
        r = self._cur.fetchone()
        return self._rows([r])[0] if r is not None else None

    def fetchall(self):
        return self._rows(self._cur.fetchall())

    def __iter__(self):
        return iter(self.fetchall())


class _HanaConn:
    def __init__(self, conn):
        self._c = conn

    def execute(self, sql: str, params=()):
        s = sql.strip().upper()
        if s.startswith("PRAGMA") or s in ("BEGIN IMMEDIATE", "BEGIN"):
            return _HanaCursor(self._c.cursor())
        if s == "COMMIT":
            self._c.commit()
            return _HanaCursor(self._c.cursor())
        if s == "ROLLBACK":
            self._c.rollback()
            return _HanaCursor(self._c.cursor())
        cur = self._c.cursor()
        params = [bytes(p) if isinstance(p, (memoryview, bytearray)) else p for p in (params or ())]
        cur.execute(_hana_sql(sql), params)
        return _HanaCursor(cur)

    def close(self):
        try:
            self._c.close()
        except Exception:  # noqa: BLE001
            pass


class HanaStore(Store):
    """The same store on SAP HANA Cloud: a `hana` service instance of plan `schema` bound to the studio.
    Tables are created in the instance's schema on first start; data survives restarts and redeploys."""

    def __init__(self, creds: Dict):
        from hdbcli import dbapi           # SAP HANA client (pip: hdbcli)
        self._dbapi = dbapi
        self._creds = creds
        self.path = f"hana://{creds.get('host')}:{creds.get('port')}/{creds.get('schema')}"
        self._lock = threading.RLock()
        self.durable = True
        self.durability_note = ""
        c = self._raw()
        try:
            cur = c.cursor()
            cur.execute("SELECT TABLE_NAME FROM SYS.TABLES WHERE SCHEMA_NAME=CURRENT_SCHEMA")
            have = {r[0] for r in cur.fetchall()}
            for t, cols in _HANA_DDL.items():
                if t not in have:
                    cur.execute(f"CREATE COLUMN TABLE {t} ({cols})")
            c.commit()
        finally:
            c.close()

    def _raw(self):
        cr = self._creds
        conn = self._dbapi.connect(address=cr["host"], port=int(cr["port"]), user=cr["user"],
                                   password=cr["password"], encrypt=True,
                                   sslValidateCertificate=True, currentSchema=cr.get("schema") or None)
        conn.setautocommit(True)
        return conn

    def _conn(self):
        return _Closing(_HanaConn(self._raw()))

    @contextlib.contextmanager
    def _tx(self):
        with self._lock:
            raw = self._raw()
            raw.setautocommit(False)
            c = _HanaConn(raw)
            try:
                try:
                    yield c
                except BaseException:
                    raw.rollback()
                    raise
                raw.commit()
            finally:
                c.close()


def _vcap_hana() -> Optional[Dict]:
    """Credentials of a bound HANA schema/HDI instance (VCAP_SERVICES), or HANA_CREDENTIALS JSON (local tests)."""
    raw = os.environ.get("HANA_CREDENTIALS", "").strip()
    if raw:
        try:
            return json.loads(raw)
        except ValueError:
            return None
    try:
        vcap = json.loads(os.environ.get("VCAP_SERVICES") or "{}")
    except ValueError:
        return None
    for label, insts in vcap.items():
        for inst in insts or []:
            if label == "hana" or "hana" in (inst.get("tags") or []):
                cr = inst.get("credentials") or {}
                if cr.get("host") and cr.get("user"):
                    return cr
    return None


_STORE: Optional[Store] = None
_STORE_LOCK = threading.Lock()
STORE_NOTE = ""


def get_store() -> Store:
    """Process-wide store: SAP HANA Cloud when a hana schema instance is bound, else local SQLite."""
    global _STORE, STORE_NOTE
    with _STORE_LOCK:
        if _STORE is None:
            creds = _vcap_hana()
            if creds:
                try:
                    _STORE = HanaStore(creds)
                    return _STORE
                except Exception as e:  # noqa: BLE001 — keep the studio usable, say why
                    STORE_NOTE = f"SAP HANA workspace unavailable ({str(e)[:200]}) — using local storage."
            elif os.environ.get("DATABASE_URL"):
                STORE_NOTE = ("DATABASE_URL is set but managed-database storage is not enabled yet — "
                              "using the local SQLite workspace.")
            _STORE = Store()
        return _STORE


def reset_store_for_tests(path: Optional[str] = None) -> Store:
    global _STORE
    with _STORE_LOCK:
        _STORE = Store(path) if path else None
    return _STORE
