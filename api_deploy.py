"""
api_deploy.py — Cloud Foundry sign-in and deployment for the studio API (port of the classic
studio's _cf_signin / _release_panel / _deploy / _w_deploy). One CF session per studio user
when the API runs on BTP (own CF_HOME); locally the developer's own `cf login` is used.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from typing import Dict, List, Optional

import access_control as ac
from deployer import CFDeployer, write_project_to_disk
from odata_config import AUTH_BASIC, AUTH_OAUTH_CC, ODataService, mta_extension


def technical() -> Optional[Dict]:
    """Technical deployment user (env CF_TECH_USER / CF_TECH_PASSWORD [/ CF_TECH_ORIGIN]): the studio
    signs in to Cloud Foundry by itself, so users never sign in twice. Who may deploy or remove an app
    is still decided by the studio roles, and the audit log records the real user."""
    u, p = os.environ.get("CF_TECH_USER", "").strip(), os.environ.get("CF_TECH_PASSWORD", "")
    return {"user": u, "password": p, "origin": os.environ.get("CF_TECH_ORIGIN", "").strip()} if u and p else None


_LOGIN_LOCK = __import__("threading").Lock()


def cf_home(user, env: str = "dev") -> Optional[str]:
    if not os.environ.get("VCAP_APPLICATION"):
        return None                     # local development: the developer's own `cf login`
    import workspace as wsp
    # technical user: one session per environment (cf target is per CF_HOME); else one per studio user
    key = f"_technical_{env}" if technical() else hashlib.sha1(user.id.encode()).hexdigest()[:16]
    d = os.path.join(wsp._default_data_dir(), "cfhome", key)
    os.makedirs(d, exist_ok=True)
    return d


def target(env: str = "dev") -> Dict:
    cfg = ac.environments().get(env) or ac.environments()["dev"]
    return {"api": cfg.get("api", ""), "org": cfg.get("org", ""), "space": cfg.get("space", "")}


def deployer(user, env: str = "dev", say=None) -> CFDeployer:
    """CF CLI for `env`. With a technical user the session is (re)established automatically."""
    t = target(env)
    d = CFDeployer(api_url=t["api"], org=t["org"], space=t["space"], cf_home=cf_home(user, env))
    tech = technical()
    if tech and os.environ.get("VCAP_APPLICATION") and d.check_cf_cli():
        with _LOGIN_LOCK:
            if not d.is_logged_in():
                if say:
                    say("Signing in to Cloud Foundry with the studio's deployment user…")
                lines = list(d.login(tech["user"], tech["password"], tech["origin"]))
                if not d.is_logged_in():
                    errs = [l for l in lines if "ERROR" in l or "FAILED" in l or "rror" in l]
                    raise RuntimeError("The studio's Cloud Foundry deployment user could not sign in — ask an "
                                       "administrator to check CF_TECH_USER / CF_TECH_PASSWORD. "
                                       + " ".join(errs[-2:])[:300])
    return d


def session(user) -> Dict:
    tech = technical() if os.environ.get("VCAP_APPLICATION") else None
    try:
        d = deployer(user)
    except RuntimeError as e:
        t = target()
        return {"signed_in": False, "user": "", "api": t["api"], "org": t["org"], "space": t["space"],
                "passcode_url": "", "cli_available": True, "mode": "technical", "error": str(e)}
    cli = d.check_cf_cli()
    who = d.whoami() if cli else ""
    t = target()
    return {"signed_in": bool(who), "user": who, "api": t["api"], "org": t["org"], "space": t["space"],
            "passcode_url": d.passcode_url() if cli and not tech else "", "cli_available": cli,
            "mode": "technical" if tech else ("personal" if os.environ.get("VCAP_APPLICATION") else "local")}


def login(user, body: Dict) -> Dict:
    d = deployer(user)
    if not d.check_cf_cli():
        raise ValueError("The Cloud Foundry CLI is not available on the studio host.")
    lines = list(d.login_sso(body["passcode"]) if body.get("passcode")
                 else d.login(body.get("user") or "", body.get("password") or "", body.get("origin") or ""))
    s = session(user)
    if not s["signed_in"]:
        errs = [l for l in lines if "[ERROR]" in l or "FAILED" in l or "rror" in l]
        raise ValueError("Sign-in failed. " + " ".join(errs[-3:])[:600])
    return s


def logout(user) -> Dict:
    d = deployer(user)
    for _ in d._run(["cf", "logout"]):
        pass
    return session(user)


def deploy_meta(snapshot: Dict) -> Dict:
    return (snapshot or {}).get("deploy_meta") or {}


def needs(meta: Dict) -> Dict:
    """Destinations the deploy has to create (credentials asked at deploy time) + method choices."""
    real = [m for m in meta.get("services", []) if m.get("is_real")]
    dests = [{"id": m["id"], "dest_name": m.get("dest_name", ""), "auth_type": m.get("auth_type", ""),
              "use_existing": bool(m.get("use_existing_destination")), "on_premise": bool(m.get("on_premise")),
              "url": m.get("url", ""),
              "fields": ([] if m.get("use_existing_destination") else
                         ["url"] + (["user", "password"] if m.get("auth_type") == AUTH_BASIC else
                                    ["token_url", "client_id", "client_secret"] if m.get("auth_type") == AUTH_OAUTH_CC
                                    else []))} for m in real]
    methods = ["MTA Build + Deploy"] if (real or meta.get("standalone")) else ["MTA Build + Deploy", "cf push (static)"]
    return {"destinations": dests, "method_choices": methods}


def missing_creds(meta: Dict, creds: Dict) -> List[str]:
    out = []
    for d in needs(meta)["destinations"]:
        for f in d["fields"]:
            if not str((creds.get(d["id"]) or {}).get(f) or "").strip():
                out.append(f"{d['dest_name']} → {f}")
    return out


def quality_gate(files: Dict[str, str]) -> List[str]:
    import quality_validator as qv
    gate = qv.validate_project(files)
    return [f["message"] + (f" ({f['file']})" if f.get("file") else "")
            for f in gate["findings"] if f["severity"] == "error"]


def run(say, user, files: Dict[str, str], meta: Dict, env: str, dest_creds: Dict, method: str) -> Dict:
    """Background deployment. Returns {"status": succeeded|failed|skipped, "url", "log", "app_id"}."""
    try:
        d = deployer(user, env, say)
    except RuntimeError as e:
        say(f"❌ {e}")
        return {"status": "skipped", "url": "", "log": str(e), "app_id": meta.get("app_id") or ""}
    app_id = meta.get("app_id") or "fiori-app"
    protected = any(m.get("is_real") for m in meta.get("services", []))
    logs: List[str] = []

    def log(line):
        logs.append(str(line))
        say(line)

    def done(status, url=""):
        return {"status": status, "url": url, "log": "\n".join(logs), "app_id": app_id}

    if not d.check_cf_cli():
        log("❌ The Cloud Foundry CLI is not available on the studio host.")
        return done("skipped")
    with tempfile.TemporaryDirectory() as tmp:
        write_project_to_disk(files, tmp)
        log("📁 Project written.")
        mta_path = os.path.join(tmp, "mta.yaml")
        mta_disk = open(mta_path, encoding="utf-8").read() if os.path.exists(mta_path) else ""
        if protected and ("resources:" not in mta_disk or "requires:" not in mta_disk):
            log("❌ mta.yaml has no resources/requires — regenerate the app.")
            return done("failed")
        for line in d.target():
            log(line)
        if not d.is_logged_in():
            log("❌ " + ("The studio has no Cloud Foundry session. An administrator can configure the studio's deployment user (CF_TECH_USER / CF_TECH_PASSWORD) so nobody has to sign in to Cloud Foundry separately." if not technical() else "The deployment user's Cloud Foundry session could not be established."))
            return done("skipped")
        log(f"✅ Signed in as {d.whoami()}")
        if method.startswith("MTA"):
            if (protected or meta.get("standalone")) and not d.check_multiapps_plugin():
                log("❌ CF multiapps plugin missing (cf install-plugin multiapps).")
                return done("failed")
            ext = None
            if protected:
                svc = [ODataService(id=m["id"], url=(dest_creds.get(m["id"]) or {}).get("url", "") or m.get("url", ""),
                                    auth_type=m["auth_type"], is_primary=(i == 0),
                                    use_existing_destination=bool(m.get("use_existing_destination")),
                                    destination=m.get("dest_name", ""), on_premise=bool(m.get("on_premise")))
                       for i, m in enumerate(meta.get("services", []))]
                ext = d.write_mta_extension(tmp, mta_extension(app_id, svc, dest_creds))
                log("🧩 deploy.mtaext written (destinations).")
            for line in d.build_mta(tmp):
                log(line)
            for line in d.deploy_mtar(tmp, ext_file=ext):
                log(line)
        else:
            for line in d.cf_push(tmp, app_id):
                log(line)
        url = d.get_app_url(app_id)
        if url:
            log(f"🌐 {url}")
        return done("succeeded" if url else "failed", url or "")
