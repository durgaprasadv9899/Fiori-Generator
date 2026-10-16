"""
access_control.py — who is using the studio, what they may do, and the release gate.

Identity (first match wins):
  1. XSUAA  — a JWT forwarded by an approuter in front of the studio (Authorization: Bearer …
              or x-approuter-authorization). The signature is verified (RS256 against the bound
              XSUAA's token_keys); role collections / scopes map to studio roles.
  2. local  — named users from STUDIO_USERS (JSON) or $STUDIO_DATA_DIR/users.json with
              PBKDF2 password hashes (see hash_password()).
  3. shared — the legacy APP_ACCESS_PASSWORD; users still enter their name/e-mail so the audit
              trail has an identity, but everyone gets the same roles (NOT real RBAC).
  4. open   — nothing configured (local development): name only, admin.

Roles → permissions are in ROLE_PERMISSIONS. Project membership (owner / member role) decides
what a user may do on a given project; reviewer, release_manager and admin act across projects.

Environments (dev/test/prod) come from STUDIO_ENVIRONMENTS; check_deploy() is the gate:
permission for the environment, a four-eyes approval of exactly that version where required,
and no blocking build/runtime validation failures.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, MutableMapping, Optional, Set, Tuple
from urllib.parse import urlparse

ROLES = ("viewer", "developer", "reviewer", "release_manager", "admin")
ROLE_LABELS = {"viewer": "Viewer", "developer": "Developer", "reviewer": "Reviewer",
               "release_manager": "Release manager", "admin": "Administrator"}

PERMISSIONS = ("project.view", "project.view_all", "project.create", "project.edit", "version.save",
               "approval.request", "approval.decide", "deploy.dev", "deploy.test", "deploy.prod",
               "admin.users")

ROLE_PERMISSIONS: Dict[str, Set[str]] = {
    "viewer": {"project.view"},
    "developer": {"project.view", "project.create", "project.edit", "version.save", "approval.request",
                  "deploy.dev"},
    "reviewer": {"project.view", "project.view_all", "approval.decide"},
    "release_manager": {"project.view", "project.view_all", "approval.request", "deploy.test", "deploy.prod"},
    "admin": set(PERMISSIONS),
}
# Roles that act on every project (gatekeepers); other roles need project membership.
CROSS_PROJECT_ROLES = {"reviewer", "release_manager", "admin"}
# Project membership → role on that project.
PROJECT_ROLE_MAP = {"owner": "developer", "developer": "developer", "reviewer": "reviewer", "viewer": "viewer"}
# Permissions that need no project (creating one, admin screens).
GLOBAL_ONLY = {"project.create", "admin.users", "project.view_all"}

SESSION_KEY = "_ac_user"


@dataclass
class User:
    id: str                                  # e-mail where known — the audit identity
    name: str = ""
    roles: Set[str] = field(default_factory=set)
    source: str = "open"                     # xsuaa | local | shared-password | open
    claims: Dict = field(default_factory=dict)

    @property
    def display(self) -> str:
        return self.name or self.id

    def to_dict(self) -> Dict:
        return {"id": self.id, "name": self.name, "roles": sorted(self.roles), "source": self.source}

    @staticmethod
    def from_dict(d: Dict) -> "User":
        return User(id=d["id"], name=d.get("name", ""), roles=set(d.get("roles") or []), source=d.get("source", "open"))


# ─────────────────────────────────────────────────────────────────────────────
# Configuration helpers
# ─────────────────────────────────────────────────────────────────────────────
def _data_dir() -> str:
    return os.environ.get("STUDIO_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".evoke-studio")


def _vcap_xsuaa() -> Optional[Dict]:
    """Credentials of an XSUAA instance bound to the studio, if any."""
    raw = os.environ.get("VCAP_SERVICES")
    if not raw:
        return None
    try:
        vcap = json.loads(raw)
    except ValueError:
        return None
    for label, insts in vcap.items():
        for inst in insts or []:
            if label == "xsuaa" or "xsuaa" in (inst.get("tags") or []):
                # the user-administration instance (plan apiaccess) is not the login instance
                if inst.get("plan") == "apiaccess":
                    continue
                return inst.get("credentials") or None
    return None


def _clean_roles(roles) -> Set[str]:
    if isinstance(roles, str):
        roles = roles.split(",")
    return {r.strip().lower() for r in roles or [] if r and r.strip().lower() in ROLES}


def default_roles(mode: str) -> Set[str]:
    env = os.environ.get("STUDIO_DEFAULT_ROLES")
    if env:
        return _clean_roles(env) or {"viewer"}
    return {"admin"} if mode == "open" else {"developer", "reviewer"}


def local_users() -> Dict[str, Dict]:
    """{email: {"name", "roles", "password_pbkdf2" | "password_sha256"}} — env first, then users.json."""
    raw = os.environ.get("STUDIO_USERS", "").strip()
    if not raw:
        p = os.path.join(_data_dir(), "users.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                raw = f.read()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return {str(k).strip().lower(): v for k, v in data.items() if isinstance(v, dict)}


def auth_mode() -> str:
    """xsuaa | local | shared | open (override with STUDIO_AUTH)."""
    forced = os.environ.get("STUDIO_AUTH", "").strip().lower()
    if forced in ("xsuaa", "local", "shared", "open"):
        return forced
    if _vcap_xsuaa():
        return "xsuaa"
    if local_users():
        return "local"
    if os.environ.get("APP_ACCESS_PASSWORD"):
        return "shared"
    return "open"


def describe_mode(mode: Optional[str] = None) -> str:
    mode = mode or auth_mode()
    return {
        "xsuaa": "Signed in through SAP BTP (XSUAA). Roles come from your role collections.",
        "local": "Named studio users with individual passwords and roles.",
        "shared": ("Shared access password: everyone gets the same roles "
                   f"({', '.join(sorted(default_roles('shared')))}). This is NOT role-based access control — "
                   "your name is only recorded for the audit trail."),
        "open": "No sign-in configured (local development) — every user is an administrator.",
    }[mode]


# ─────────────────────────────────────────────────────────────────────────────
# Passwords (local users)
# ─────────────────────────────────────────────────────────────────────────────
def hash_password(password: str, iterations: int = 310_000) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, rec: Dict) -> bool:
    stored = rec.get("password_pbkdf2") or ""
    if stored:
        try:
            algo, it, salt, dk = stored.split("$")
            if algo != "pbkdf2_sha256":
                return False
            calc = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), base64.b64decode(salt), int(it))
            return hmac.compare_digest(calc, base64.b64decode(dk))
        except (ValueError, TypeError):
            return False
    if rec.get("password_sha256"):     # legacy/unsalted — accepted, but prefer pbkdf2
        return hmac.compare_digest(hashlib.sha256(password.encode("utf-8")).hexdigest(),
                                   str(rec["password_sha256"]).lower())
    return False


def login_local(email: str, password: str, session: MutableMapping) -> Optional[User]:
    email = (email or "").strip().lower()
    rec = local_users().get(email)
    if not rec or not verify_password(password or "", rec):
        return None
    u = User(id=email, name=rec.get("name") or email, roles=_clean_roles(rec.get("roles")) or {"viewer"}, source="local")
    session[SESSION_KEY] = u.to_dict()
    return u


def login_shared(password: str, name: str, session: MutableMapping) -> Optional[User]:
    """Legacy shared password + a name/e-mail for the audit trail (open mode: no password)."""
    name = (name or "").strip()
    if not name:
        return None
    mode = auth_mode()
    if mode == "shared":
        expected = os.environ.get("APP_ACCESS_PASSWORD", "")
        if not (password and hmac.compare_digest(password, expected)):
            return None
    elif mode != "open":
        return None
    u = User(id=name.lower() if "@" in name else name, name=name, roles=default_roles(mode),
             source="shared-password" if mode == "shared" else "open")
    session[SESSION_KEY] = u.to_dict()
    return u


def logout(session: MutableMapping) -> None:
    session.pop(SESSION_KEY, None)


# ─────────────────────────────────────────────────────────────────────────────
# XSUAA JWT (RS256) — verification without third-party libraries
# ─────────────────────────────────────────────────────────────────────────────
class TokenError(Exception):
    pass


def _b64url(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _der_len(b: bytes, i: int) -> Tuple[int, int]:
    n = b[i]
    i += 1
    if n < 0x80:
        return n, i
    k = n & 0x7F
    return int.from_bytes(b[i:i + k], "big"), i + k


def _der_int(b: bytes, i: int) -> Tuple[int, int]:
    if b[i] != 0x02:
        raise TokenError("bad key encoding")
    n, i = _der_len(b, i + 1)
    return int.from_bytes(b[i:i + n], "big"), i + n


def _rsa_from_pem(pem: str) -> Tuple[int, int]:
    """(n, e) from a SubjectPublicKeyInfo or PKCS#1 RSA public key PEM."""
    body = "".join(l for l in pem.strip().splitlines() if l and not l.startswith("-----"))
    der = base64.b64decode(body)
    if "RSA PUBLIC KEY" in pem:                          # PKCS#1: SEQ { n, e }
        _, i = _der_len(der, 1)
        n, i = _der_int(der, i)
        e, _ = _der_int(der, i)
        return n, e
    # SPKI: SEQ { SEQ { algo }, BITSTRING { SEQ { n, e } } }
    _, i = _der_len(der, 1)
    if der[i] != 0x30:
        raise TokenError("bad key encoding")
    alen, j = _der_len(der, i + 1)
    i = j + alen
    if der[i] != 0x03:
        raise TokenError("bad key encoding")
    _, i = _der_len(der, i + 1)
    i += 1                                              # unused-bits byte
    _, i = _der_len(der, i + 1)                         # inner SEQ
    n, i = _der_int(der, i)
    e, _ = _der_int(der, i)
    return n, e


_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def rsa_verify_sha256(n: int, e: int, message: bytes, signature: bytes) -> bool:
    """RSASSA-PKCS1-v1_5 with SHA-256."""
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    s = int.from_bytes(signature, "big")
    if s >= n:
        return False
    em = pow(s, e, n).to_bytes(k, "big")
    t = _SHA256_DIGEST_INFO + hashlib.sha256(message).digest()
    if k < len(t) + 11:
        return False
    expected = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return hmac.compare_digest(em, expected)


_KEY_CACHE: Dict[str, Tuple[float, Dict[str, Tuple[int, int]]]] = {}
_KEY_LOCK = threading.Lock()


def _token_keys(jku: str) -> Dict[str, Tuple[int, int]]:
    with _KEY_LOCK:
        hit = _KEY_CACHE.get(jku)
        if hit and time.time() - hit[0] < 900:
            return hit[1]
    import requests
    r = requests.get(jku, timeout=10)
    r.raise_for_status()
    keys = {}
    for k in r.json().get("keys", []):
        try:
            if k.get("n") and k.get("e"):
                keys[k.get("kid", "")] = (int.from_bytes(_b64url(k["n"]), "big"), int.from_bytes(_b64url(k["e"]), "big"))
            elif k.get("value"):
                keys[k.get("kid", "")] = _rsa_from_pem(k["value"])
        except (TokenError, ValueError, IndexError):
            continue
    with _KEY_LOCK:
        _KEY_CACHE[jku] = (time.time(), keys)
    return keys


def decode_jwt(token: str, creds: Optional[Dict] = None, *, verify: bool = True,
               keys: Optional[Dict[str, Tuple[int, int]]] = None, now: Optional[float] = None) -> Dict:
    """Claims of an XSUAA token. With verify=True the RS256 signature, expiry, the key URL (must be
    on the XSUAA's uaadomain) and the audience (studio's xsappname/clientid) are checked."""
    try:
        h64, p64, s64 = token.split(".")
        header, claims = json.loads(_b64url(h64)), json.loads(_b64url(p64))
        sig = _b64url(s64)
    except (ValueError, TypeError) as e:
        raise TokenError(f"malformed token: {e}") from e
    if not verify:
        return claims
    if header.get("alg") != "RS256":
        raise TokenError(f"unsupported algorithm {header.get('alg')}")
    creds = creds or {}
    if keys is None:
        jku = header.get("jku") or (creds.get("url", "").rstrip("/") + "/token_keys")
        host = urlparse(jku).hostname or ""
        dom = (creds.get("uaadomain") or urlparse(creds.get("url", "")).hostname or "").lower()
        if not dom or not (host == dom or host.endswith("." + dom)) or urlparse(jku).scheme != "https":
            raise TokenError("token key URL is not on the bound XSUAA domain")
        keys = _token_keys(jku)
    kid = header.get("kid", "")
    cand = [keys[kid]] if kid in keys else list(keys.values())
    msg = f"{h64}.{p64}".encode("ascii")
    if not any(rsa_verify_sha256(n, e, msg, sig) for n, e in cand):
        raise TokenError("signature verification failed")
    t = now if now is not None else time.time()
    if claims.get("exp") is not None and t > float(claims["exp"]) + 60:
        raise TokenError("token expired")
    if claims.get("nbf") is not None and t + 60 < float(claims["nbf"]):
        raise TokenError("token not yet valid")
    app, cid = creds.get("xsappname", ""), creds.get("clientid", "")
    if app or cid:
        aud = claims.get("aud") or []
        aud = [aud] if isinstance(aud, str) else aud
        scopes = claims.get("scope") or []
        ok = (cid and (cid in aud or claims.get("cid") == cid or claims.get("azp") == cid)) or \
             (app and (any(a == app or a.startswith(app + ".") for a in aud)
                       or any(s.startswith(app + ".") for s in scopes)))
        if not ok:
            raise TokenError("token was not issued for this application")
    return claims


def _role_map() -> Dict[str, List[str]]:
    raw = os.environ.get("STUDIO_ROLE_MAP", "").strip()
    if raw:
        try:
            return {k: ([v] if isinstance(v, str) else list(v)) for k, v in json.loads(raw).items()}
        except ValueError:
            pass
    return {}


_SUFFIX_ROLES = {".viewer": "viewer", ".developer": "developer", ".reviewer": "reviewer",
                 ".releasemanager": "release_manager", ".admin": "admin"}


def roles_from_claims(claims: Dict) -> Set[str]:
    """Scopes (xsappname.Developer …) and role collections → studio roles. STUDIO_ROLE_MAP maps exact
    scope or role-collection names, e.g. {"Evoke_Studio_Approver": "reviewer"}."""
    scopes = [str(s) for s in claims.get("scope") or []]
    rcs = [str(r) for r in ((claims.get("xs.system.attributes") or {}).get("xs.rolecollections") or [])]
    mapping = _role_map()
    roles: Set[str] = set()
    for name in scopes + rcs:
        for r in mapping.get(name, []):
            roles |= _clean_roles([r])
    for s in scopes:
        low = s.lower()
        for suf, r in _SUFFIX_ROLES.items():
            if low.endswith(suf):
                roles.add(r)
    return roles


def _header(headers: Mapping[str, str], name: str) -> str:
    for k, v in (headers or {}).items():
        if k.lower() == name:
            return v or ""
    return ""


def user_from_headers(headers: Mapping[str, str]) -> Optional[User]:
    """The XSUAA user of this request, or None (no / untrusted token)."""
    tok = _header(headers, "x-approuter-authorization") or _header(headers, "authorization")
    if not tok.lower().startswith("bearer "):
        return None
    tok = tok.split(" ", 1)[1].strip()
    creds = _vcap_xsuaa()
    trust_unverified = os.environ.get("STUDIO_TRUST_UNVERIFIED_JWT", "") == "1" and not os.environ.get("VCAP_APPLICATION")
    if not creds and not trust_unverified:
        return None
    try:
        claims = decode_jwt(tok, creds, verify=bool(creds))
    except TokenError:
        return None
    email = (claims.get("email") or claims.get("user_name") or claims.get("sub") or "").strip()
    if not email:
        return None
    name = " ".join(x for x in (claims.get("given_name"), claims.get("family_name")) if x) or claims.get("user_name") or email
    roles = roles_from_claims(claims)
    # bootstrap: STUDIO_ADMINS (comma-separated e-mails) are administrators before role collections exist
    if email.lower() in {a.strip().lower() for a in os.environ.get("STUDIO_ADMINS", "").split(",") if a.strip()}:
        roles.add("admin")
    return User(id=email.lower(), name=name, roles=roles, source="xsuaa",
                claims={k: claims.get(k) for k in ("origin", "zid", "exp")})


def current_user(headers: Mapping[str, str], session: MutableMapping) -> Optional[User]:
    """Identity of this browser session (see module doc). None = must sign in."""
    mode = auth_mode()
    if mode == "xsuaa":
        u = user_from_headers(headers)
        if u:
            session[SESSION_KEY] = u.to_dict()
            return u
        # websocket reruns may not carry the token again: keep the identity proven earlier
        d = session.get(SESSION_KEY)
        return User.from_dict(d) if d and d.get("source") == "xsuaa" else None
    d = session.get(SESSION_KEY)
    if not d:
        return None
    u = User.from_dict(d)
    expected = {"local": "local", "shared": "shared-password", "open": "open"}[mode]
    if u.source != expected:                   # configuration changed since sign-in
        session.pop(SESSION_KEY, None)
        return None
    if mode == "local":                        # roles follow the user list (revocation)
        rec = local_users().get(u.id)
        if not rec:
            session.pop(SESSION_KEY, None)
            return None
        u.roles = _clean_roles(rec.get("roles")) or {"viewer"}
    return u


# ─────────────────────────────────────────────────────────────────────────────
# Authorization
# ─────────────────────────────────────────────────────────────────────────────
def permissions(user: Optional[User], project: Optional[Dict] = None, store=None) -> Set[str]:
    if not user:
        return set()
    roles = set(user.roles)
    if project is None:
        out: Set[str] = set()
        for r in roles:
            out |= ROLE_PERMISSIONS.get(r, set())
        return out
    out = set()
    for r in roles & CROSS_PROJECT_ROLES:
        out |= ROLE_PERMISSIONS[r]
    prole = ""
    if project.get("owner") == user.id:
        prole = "owner"
    elif store is not None and project.get("id"):
        prole = store.project_role(project["id"], user)
    else:
        prole = next((m["role"] for m in project.get("members") or [] if m.get("user") == user.id), "")
    if prole:
        mapped = PROJECT_ROLE_MAP.get(prole, "viewer")
        # a global viewer stays read-only even when made a project developer
        if mapped == "developer" and not (roles & {"developer", "admin"}):
            mapped = "viewer"
        out |= ROLE_PERMISSIONS[mapped]
    return out - (GLOBAL_ONLY - {"project.view_all"})


def can(user: Optional[User], permission: str, project: Optional[Dict] = None, store=None) -> bool:
    if not user:
        return False
    if "admin" in user.roles:
        return True
    if permission in GLOBAL_ONLY:
        return permission in permissions(user)
    return permission in permissions(user, project, store)


_PERM_TEXT = {"project.view": "view this project", "project.view_all": "see all projects",
              "project.create": "create projects", "project.edit": "change this project",
              "version.save": "save versions", "approval.request": "request a release approval",
              "approval.decide": "approve or reject releases", "deploy.dev": "deploy to development",
              "deploy.test": "deploy to test", "deploy.prod": "deploy to production",
              "admin.users": "manage users"}


def require(user: Optional[User], permission: str, project: Optional[Dict] = None, store=None) -> None:
    if not can(user, permission, project, store):
        who = user.display if user else "Anonymous"
        roles = ", ".join(ROLE_LABELS.get(r, r) for r in sorted(user.roles)) if user else "none"
        raise PermissionError(f"{who} (roles: {roles}) may not {_PERM_TEXT.get(permission, permission)}.")


# ─────────────────────────────────────────────────────────────────────────────
# Environments + deployment gate
# ─────────────────────────────────────────────────────────────────────────────
_CF_API = "https://api.cf.us10-001.hana.ondemand.com"
_CF_ORG = "EVOKE TECHNOLOGIES SP. Z O.O._evokebtp-subaccount-qt2znvbe"


def environments() -> Dict[str, Dict]:
    """{"dev": {"label","api","org","space","requires_approval","requires_validation","configured"}, …}"""
    defaults = {
        "dev": {"label": "Development", "api": _CF_API, "org": _CF_ORG, "space": "BAS_DEV",
                "requires_approval": False, "requires_validation": False, "configured": True},
        "test": {"label": "Test", "api": _CF_API, "org": _CF_ORG, "space": "",
                 "requires_approval": False, "requires_validation": True, "configured": False},
        "prod": {"label": "Production", "api": _CF_API, "org": _CF_ORG, "space": "",
                 "requires_approval": True, "requires_validation": True, "configured": False},
    }
    raw = os.environ.get("STUDIO_ENVIRONMENTS", "").strip()
    if not raw:
        return defaults
    try:
        cfg = json.loads(raw)
    except ValueError:
        return defaults
    out = {}
    for key, v in cfg.items():
        base = dict(defaults.get(key, {"label": key.title(), "api": _CF_API, "org": _CF_ORG, "space": "",
                                       "requires_approval": True, "requires_validation": True}))
        base.update(v or {})
        base["configured"] = bool(base.get("space") and base.get("org") and base.get("api")) \
            if "configured" not in (v or {}) else bool(v["configured"])
        out[key] = base
    return out


def _blocking(report: Optional[Dict]) -> List[str]:
    if not report:
        return []
    b = report.get("blocking")
    if isinstance(b, list):
        return [str(x.get("name") or x.get("check") or x.get("message") or x) if isinstance(x, dict) else str(x)
                for x in b]
    if isinstance(b, (int, bool)) and b:
        return [f"{int(b)} blocking check(s) failed"] if not isinstance(b, bool) else ["blocking checks failed"]
    return []


def check_deploy(user: Optional[User], env: str, project_id: str, version_id: Optional[str], store,
                 validation_report: Optional[Dict] = None) -> Tuple[bool, List[str]]:
    """May `user` deploy that version of the project to `env`? (ok, reasons it may not)."""
    reasons: List[str] = []
    envs = environments()
    cfg = envs.get(env)
    if not cfg:
        return False, [f"Unknown environment '{env}'."]
    if not cfg.get("configured", True):
        reasons.append(f"{cfg['label']} is not configured yet (no Cloud Foundry space assigned).")
    project = store.get_project(project_id) if project_id else None
    if not project:
        return False, reasons + ["Save the app as a project first — deployments are tracked per project version."]
    if not can(user, f"deploy.{env}", project, store):
        roles = ", ".join(ROLE_LABELS.get(r, r) for r in sorted(user.roles)) if user else "none"
        reasons.append(f"Your roles ({roles}) do not allow deploying to {cfg['label']}.")
    version = store.get_version(version_id, with_files=False) if version_id else None
    if not version or version["project_id"] != project_id:
        reasons.append("Save the current state as a version first — only saved versions can be deployed.")
        return False, reasons
    report = validation_report if validation_report is not None else version.get("validation")
    if cfg.get("requires_validation") and not report:
        reasons.append(f"{cfg['label']} needs a build & runtime validation run of version {version['number']}.")
    for b in _blocking(report):
        reasons.append(f"Blocking validation failure: {b}")
    if cfg.get("requires_approval"):
        a = store.approval_for(project_id, version_id, env)
        if not a or a["status"] != "approved":
            state = a["status"] if a else "not requested"
            reasons.append(f"{cfg['label']} needs an approved release request for version {version['number']} "
                           f"(currently: {state}).")
        elif a["decided_by"] in (a["requester"], version["author"]):
            reasons.append("The release was approved by its requester or the version's author — "
                           "a different reviewer must approve.")
    return not reasons, reasons


# ─────────────────────────────────────────────────────────────────────────────
# Files to put the studio itself behind XSUAA (text only — deploying needs a decision)
# ─────────────────────────────────────────────────────────────────────────────
def studio_xs_security(xsappname: str = "evoke-fiori-studio") -> str:
    tmpl = [("Viewer", "Read-only access to studio projects"),
            ("Developer", "Create, generate and deploy apps to development"),
            ("Reviewer", "Approve or reject production releases"),
            ("ReleaseManager", "Deploy approved versions to test and production"),
            ("Admin", "Studio administration")]
    return json.dumps({
        "xsappname": xsappname,
        "tenant-mode": "dedicated",
        "scopes": [{"name": f"$XSAPPNAME.{n}", "description": d} for n, d in tmpl],
        "role-templates": [{"name": n, "description": d, "scope-references": [f"$XSAPPNAME.{n}"]} for n, d in tmpl],
        "role-collections": [{"name": f"Evoke_Studio_{n}", "description": d,
                              "role-template-references": [f"$XSAPPNAME.{n}"]} for n, d in tmpl],
        "oauth2-configuration": {"redirect-uris": ["https://*.cfapps.us10-001.hana.ondemand.com/**"]},
    }, indent=2)


def studio_approuter_files(studio_url: str = "https://fiori-generator-poc.cfapps.us10-001.hana.ondemand.com",
                           xsappname: str = "evoke-fiori-studio") -> Dict[str, str]:
    """Approuter in front of the Streamlit studio, forwarding the user's token (websockets enabled)."""
    return {
        "studio-approuter/xs-app.json": json.dumps({
            "authenticationMethod": "route", "websockets": {"enabled": True},
            "routes": [{"source": "^/(.*)$", "target": "/$1", "destination": "studio", "authenticationType": "xsuaa",
                        "csrfProtection": False}]}, indent=2),
        "studio-approuter/package.json": json.dumps({
            "name": "evoke-studio-approuter", "version": "1.0.0", "dependencies": {"@sap/approuter": "^16.0.0"},
            "scripts": {"start": "node node_modules/@sap/approuter/approuter.js"}, "engines": {"node": ">=18"}},
            indent=2),
        "studio-approuter/manifest.yml": (
            "applications:\n"
            "  - name: " + xsappname + "\n"
            "    memory: 256M\n"
            "    buildpacks: [nodejs_buildpack]\n"
            "    services: [" + xsappname + "-xsuaa]\n"
            "    env:\n"
            "      SESSION_TIMEOUT: 120\n"
            "      destinations: >\n"
            "        [{\"name\": \"studio\", \"url\": \"" + studio_url + "\", \"forwardAuthToken\": true}]\n"),
        "studio-approuter/xs-security.json": studio_xs_security(xsappname),
        "studio-approuter/README.md": (
            "# Studio behind XSUAA (not deployed — needs a decision)\n\n"
            "1. `cf create-service xsuaa application " + xsappname + "-xsuaa -c xs-security.json`\n"
            "2. `cf bind-service fiori-generator-poc " + xsappname + "-xsuaa` (the studio verifies tokens with it)\n"
            "3. `cf push` this folder; map the public route to the approuter only and remove the studio's own\n"
            "   public route (or keep APP_ACCESS_PASSWORD off).\n"
            "4. Assign the Evoke_Studio_* role collections to users in the BTP cockpit.\n"),
    }
