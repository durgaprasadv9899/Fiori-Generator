"""
api_users.py — studio user & role administration through the SAP Authorization and Trust
Management API (XSUAA instance of plan "apiaccess" bound to the studio API).

Studio roles are BTP role collections (Evoke_Studio_*). Administrators can search users of the
subaccount, add a user (shadow user for an identity provider) and assign or remove studio role
collections. Only the five studio role collections can ever be changed here — nothing else in the
subaccount is touched. Users see changes after they sign in again (roles travel in the login token).
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Dict, List, Optional

import requests

STUDIO_COLLECTIONS = {"viewer": "Evoke_Studio_Viewer", "developer": "Evoke_Studio_Developer",
                      "reviewer": "Evoke_Studio_Reviewer", "release_manager": "Evoke_Studio_ReleaseManager",
                      "admin": "Evoke_Studio_Admin"}
_BY_COLLECTION = {v: k for k, v in STUDIO_COLLECTIONS.items()}


class UserAdminError(Exception):
    pass


def credentials() -> Optional[Dict]:
    """Credentials of the bound apiaccess instance (or USER_ADMIN_CREDENTIALS JSON for local tests)."""
    raw = os.environ.get("USER_ADMIN_CREDENTIALS", "").strip()
    if raw:
        try:
            c = json.loads(raw)
            return c.get("credentials", c)
        except ValueError:
            return None
    try:
        vcap = json.loads(os.environ.get("VCAP_SERVICES") or "{}")
    except ValueError:
        return None
    for insts in vcap.values():
        for inst in insts or []:
            cr = inst.get("credentials") or {}
            if inst.get("plan") == "apiaccess":
                return cr
    return None


def available() -> bool:
    return bool(credentials())


_TOKEN: Dict = {"value": "", "exp": 0.0}
_LOCK = threading.Lock()


def _headers() -> Dict[str, str]:
    cr = credentials()
    if not cr:
        raise UserAdminError("User administration is not set up (no apiaccess service bound to the studio).")
    with _LOCK:
        if time.time() > _TOKEN["exp"] - 60:
            r = requests.post(cr["url"].rstrip("/") + "/oauth/token", data={"grant_type": "client_credentials"},
                              auth=(cr["clientid"], cr["clientsecret"]), timeout=30)
            if not r.ok:
                raise UserAdminError(f"Could not get a token for the user-administration API ({r.status_code}).")
            j = r.json()
            _TOKEN.update(value=j["access_token"], exp=time.time() + int(j.get("expires_in", 600)))
        return {"Authorization": "Bearer " + _TOKEN["value"], "Accept": "application/json",
                "Content-Type": "application/json"}


def _api() -> str:
    cr = credentials() or {}
    return (cr.get("apiurl") or cr.get("url") or "").rstrip("/")


def _call(method: str, path: str, **kw) -> requests.Response:
    r = requests.request(method, _api() + path, headers=_headers(), timeout=30, **kw)
    if r.status_code >= 400 and r.status_code != 404:
        try:
            msg = r.json().get("error_description") or r.json().get("message") or r.text
        except ValueError:
            msg = r.text
        raise UserAdminError(f"{method} {path.split('?')[0]} → {r.status_code}: {str(msg)[:300]}")
    return r


def _row(u: Dict) -> Dict:
    groups = [g.get("value") or g.get("display") for g in u.get("groups") or []]
    name = u.get("name") or {}
    full = " ".join(x for x in (name.get("givenName"), name.get("familyName")) if x)
    return {"id": u.get("id"), "user": u.get("userName"), "origin": u.get("origin"), "name": full,
            "email": ((u.get("emails") or [{}])[0] or {}).get("value") or u.get("userName"),
            "roles": sorted(_BY_COLLECTION[g] for g in groups if g in _BY_COLLECTION),
            "last_logon": u.get("lastLogonTime") or (u.get("meta") or {}).get("lastModified")}


def identity_providers() -> List[Dict]:
    """Origins a user can come from (for the 'add user' form)."""
    r = _call("GET", "/sap/rest/identity-providers")
    out = [{"origin": "sap.default", "name": "Default identity provider"}]
    for p in r.json() if r.ok else []:
        key = p.get("originKey") or p.get("origin")
        if key and key not in ("sap.default", "uaa") and p.get("active", True):
            desc = ""
            try:
                desc = json.loads(p.get("config") or "{}").get("providerDescription") or ""
            except ValueError:
                pass
            out.append({"origin": key, "name": p.get("name") or desc or key})
    return out


def list_users(query: str = "", studio_only: bool = False, limit: int = 100) -> List[Dict]:
    q = (query or "").strip().replace('"', "")
    params = {"count": min(max(limit, 1), 500), "sortBy": "userName"}
    if q:
        params["filter"] = f'userName co "{q}"'
    r = _call("GET", "/Users", params=params)
    rows = [_row(u) for u in (r.json().get("resources") or [])] if r.ok else []
    return [x for x in rows if x["roles"]] if studio_only else rows


def _find(user: str, origin: str) -> Optional[Dict]:
    r = _call("GET", "/Users", params={"filter": f'userName eq "{user}" and origin eq "{origin}"'})
    res = r.json().get("resources") or [] if r.ok else []
    return res[0] if res else None


def set_roles(user: str, roles: List[str], origin: str = "sap.default", *, acting_admin: str = "") -> Dict:
    """Make the user's studio role collections exactly `roles` (other role collections are untouched).
    Creates the (shadow) user for that identity provider when it does not exist yet."""
    user = (user or "").strip().lower()
    origin = (origin or "sap.default").strip()
    if not user or "@" not in user:
        raise UserAdminError("Enter the user's e-mail address.")
    bad = [r for r in roles if r not in STUDIO_COLLECTIONS]
    if bad:
        raise UserAdminError(f"Unknown studio role(s): {', '.join(bad)}.")
    if acting_admin and user == acting_admin.lower() and "admin" not in roles:
        raise UserAdminError("You cannot remove your own Administrator role — ask another administrator.")
    u = _find(user, origin)
    created = False
    if not u:
        r = _call("POST", "/Users", data=json.dumps({"userName": user, "origin": origin,
                                                     "emails": [{"value": user, "primary": True}]}))
        u = r.json()
        created = True
    current = set(_row(u)["roles"])
    wanted = set(roles)
    for role in sorted(wanted - current):
        _call("POST", f"/Groups/{STUDIO_COLLECTIONS[role]}/members",
              data=json.dumps({"value": u["id"], "origin": origin, "type": "USER"}))
    for role in sorted(current - wanted):
        _call("DELETE", f"/Groups/{STUDIO_COLLECTIONS[role]}/members/{u['id']}")
    after = _find(user, origin) or u
    return {"user": _row(after), "created": created, "added": sorted(wanted - current),
            "removed": sorted(current - wanted)}
