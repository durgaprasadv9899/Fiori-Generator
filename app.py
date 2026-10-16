"""
app.py — Evoke Fiori App Studio
Describe an application in plain language; the AI designs it against the real
OData service (any standard Fiori floorplan or a custom multi-page app),
validates and repairs the design, shows requirement coverage and a live
wireframe, generates the SAPUI5 project and deploys it to SAP BTP.
Powered by SAP AI Core · Generative AI Hub.
"""
import json
import os
import re
import tempfile
import time

import streamlit as st
import streamlit.components.v1 as components

import ui_theme as ui

# ──────────────────────────────────────────────────────────────────────────────
# AI Core bootstrap (local: profile file; on CF: environment via cf set-env)
# ──────────────────────────────────────────────────────────────────────────────
PROFILE_PATH = "profiles/config_evoke.json"
AICORE_KEYS = ("AICORE_AUTH_URL", "AICORE_CLIENT_ID", "AICORE_CLIENT_SECRET",
               "AICORE_RESOURCE_GROUP", "AICORE_BASE_URL")
CREDENTIALS_LOADED, CREDENTIAL_ERROR = False, ""
try:
    if os.path.exists(PROFILE_PATH):
        with open(PROFILE_PATH, "r") as f:
            _profile = json.load(f)
        for _k in AICORE_KEYS:
            os.environ[_k] = _profile[_k]
    _missing = [k for k in AICORE_KEYS if not os.environ.get(k)]
    if _missing:
        raise RuntimeError(f"AI Core credentials missing: {', '.join(_missing)}. Provide {PROFILE_PATH} "
                           f"locally, or set these environment variables (cf set-env) when deployed.")
    import ai_client
    CREDENTIALS_LOADED = True
except Exception as e:  # noqa: BLE001
    CREDENTIAL_ERROR = str(e)

from deployer import create_zip_archive, write_project_to_disk, CFDeployer
from odata_config import ODataService, AUTH_CHOICES, AUTH_NONE, AUTH_BASIC, AUTH_OAUTH_CC, mta_extension

st.set_page_config(page_title="Evoke · Fiori App Studio", page_icon="🔷", layout="wide",
                   initial_sidebar_state="expanded")
st.markdown(ui.CSS, unsafe_allow_html=True)
LOGO = ui.logo_data_uri()


def ai_fn():
    """The foundation model chosen in the sidebar as a plain callable (cached per model +
    temperature) — usable from background jobs, which have no access to st.session_state."""
    if not has_ai():
        return None
    model = st.session_state.get("ai_model")
    temp = st.session_state.get("temperature", 0.15)
    key = f"_ai_fn::{model}::{temp}"
    if key not in st.session_state:
        st.session_state[key] = ai_client.make_ai_fn(model, temperature=temp, max_tokens=16000)
    return st.session_state[key]


def ai_call(system_prompt: str, user_prompt: str) -> str:
    return ai_fn()(system_prompt, user_prompt)


def has_ai() -> bool:
    return bool(CREDENTIALS_LOADED and st.session_state.get("ai_model"))


# ──────────────────────────────────────────────────────────────────────────────
# Identity (XSUAA via approuter | named studio users | shared password | open)
# ──────────────────────────────────────────────────────────────────────────────
import access_control as ac
import workspace as wsp

AUTH_MODE = ac.auth_mode()
STORE = wsp.get_store()
try:
    _headers = dict(st.context.headers or {})
except Exception:  # noqa: BLE001
    _headers = {}
USER = ac.current_user(_headers, st.session_state)
if USER is None and AUTH_MODE == "open":           # local development: the OS user, no prompt
    import getpass as _gp
    USER = ac.login_shared("", _gp.getuser(), st.session_state)
if USER is None:
    st.markdown(ui.brand_bar(LOGO, []), unsafe_allow_html=True)
    st.markdown('<div style="height:3.5rem"></div>', unsafe_allow_html=True)
    _c = st.columns([1, 1.1, 1])[1]
    with _c:
        with st.container(key="login_card"):
            st.markdown("**🔒 Sign in**")
            if AUTH_MODE == "xsuaa":
                st.error("Your SAP BTP sign-in did not reach the studio. Open the studio through its approuter URL.")
            else:
                with st.form("login_form"):
                    if AUTH_MODE == "local":
                        _id = st.text_input("E-mail")
                        _pw = st.text_input("Password", type="password")
                    else:
                        _id = st.text_input("Your name or e-mail", help="Recorded in the audit trail.")
                        _pw = st.text_input("Access password", type="password",
                                            placeholder="Enter the studio access password")
                    _go = st.form_submit_button("Sign in", type="primary", use_container_width=True)
                if _go:
                    _u = (ac.login_local(_id, _pw, st.session_state) if AUTH_MODE == "local"
                          else ac.login_shared(_pw, _id, st.session_state))
                    if _u:
                        STORE.audit(_u, None, "sign-in", {"source": _u.source})
                        st.rerun()
                    st.error("Wrong credentials." if AUTH_MODE == "local" or (_id or "").strip()
                             else "Enter your name — it is recorded in the audit trail.")
            st.caption(ac.describe_mode(AUTH_MODE))
        st.markdown('<div class="ev-login-foot">Protected workspace · Evoke Technologies</div>',
                    unsafe_allow_html=True)
    st.stop()

# Streamlit drops a widget's value when that widget is not rendered in a run (another step, the
# Projects page). Re-assigning keeps these inputs while the user moves around.
for _key in ("user_prompt", "app_id_v", "bp_floorplan", "theme_v", "sa_db", "val_level", "dep_env", "style_text"):
    if _key in st.session_state:
        st.session_state[_key] = st.session_state[_key]

for _key, _default in {"generated_files": {}, "deploy_log": [],
                       "deployed_url": "", "step": 0, "refine_history": [], "view": "studio"}.items():
    st.session_state.setdefault(_key, _default)


# ──────────────────────────────────────────────────────────────────────────────
# Workspace: projects + versions (persistent), snapshot/restore of the studio state
# ──────────────────────────────────────────────────────────────────────────────
SNAPSHOT_KEYS = ("app_mode", "user_prompt", "app_id_v", "md_edmx", "md_service_url_val", "md_extra",
                 "md_use_existing_dest", "md_dest_name_val", "md_on_premise", "md_auth", "bp_design",
                 "theme_v", "app_style", "standalone", "build_result", "deploy_meta", "validation_report",
                 "refine_history", "bp_floorplan")


def current_project():
    pid = st.session_state.get("project_id")
    return STORE.get_project(pid) if pid else None


def _snapshot() -> dict:
    return {k: st.session_state.get(k) for k in SNAPSHOT_KEYS if st.session_state.get(k) is not None}


def _ensure_project(name: str = "") -> dict:
    """The open project, or a new one owned by the user (created on first save)."""
    p = current_project()
    if p:
        return p
    ac.require(USER, "project.create")
    title = (name or ((st.session_state.get("bp_design") or {}).get("blueprint") or {}).get("app", {}).get("title")
             or st.session_state.get("app_id_v") or "Untitled app")
    p = STORE.create_project(title, USER, "standalone" if st.session_state.get("standalone") else
                             ("import" if st.session_state.get("import_mode") else "sap"),
                             app_id=st.session_state.get("app_id_v", ""),
                             description=(st.session_state.get("user_prompt") or "")[:300])
    st.session_state["project_id"] = p["id"]
    return p


def save_version(label: str = "") -> dict:
    p = _ensure_project()
    ac.require(USER, "version.save", p, STORE)
    br = st.session_state.get("build_result") or {}
    v = STORE.save_version(p["id"], USER, _snapshot(), st.session_state.generated_files or {}, label=label,
                           quality=br.get("quality"), validation=st.session_state.get("validation_report"))
    st.session_state["version_id"] = v["id"]
    st.session_state["version_sha"] = v["sha"]
    return v


def files_saved() -> bool:
    """Do the current files equal the last saved version (what approvals and deployments refer to)?"""
    return bool(st.session_state.get("version_id")) and \
        st.session_state.get("version_sha") == wsp.files_sha(st.session_state.generated_files or {})


def restore_version(vid: str) -> None:
    import metadata_parser as _mdp
    v = STORE.restore_version(vid, USER)
    if not v:
        st.error("Version not found.")
        return
    for k in SNAPSHOT_KEYS + ("md_model", "import", "import_mode", "files_raw", "deployed_url", "_model_for"):
        st.session_state.pop(k, None)
    st.session_state.update(v["snapshot"])
    if st.session_state.get("md_edmx"):
        st.session_state["md_model"] = _mdp.parse_metadata(st.session_state["md_edmx"])
    if st.session_state.get("bp_design"):
        st.session_state["_bp_json_next"] = json.dumps(st.session_state["bp_design"]["blueprint"], indent=2)
    st.session_state.generated_files = v["files"]
    st.session_state.update(project_id=v["project_id"], version_id=v["id"], version_sha=v["sha"], view="studio",
                            deployed_url="")
    st.session_state["_goto"] = 3 if v["files"] else (2 if st.session_state.get("bp_design") else 0)


def new_session() -> None:
    """Start a new app (keeps sign-in, AI settings and CF session)."""
    keep = {"ai_models", "ai_models_error", "ai_model", "temperature", "cf_home", "cf_target", ac.SESSION_KEY}
    for k in list(st.session_state.keys()):
        if k not in keep and not str(k).startswith("_ai_fn::"):
            st.session_state.pop(k, None)

# ──────────────────────────────────────────────────────────────────────────────
# Sidebar — AI settings, history
# ──────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### Workspace")
    st.markdown(f"<div class='ev-hist'><b>{USER.display}</b><br><span>"
                f"{', '.join(ac.ROLE_LABELS.get(r, r) for r in sorted(USER.roles)) or 'no role'} · "
                f"{ {'xsuaa': 'SAP BTP login', 'local': 'studio user', 'shared-password': 'shared password', 'open': 'local dev'}.get(USER.source, USER.source)}"
                f"</span></div>", unsafe_allow_html=True)
    _proj = current_project()
    if _proj:
        _vers = STORE.list_versions(_proj["id"])
        st.caption(f"Project **{_proj['name']}** · {len(_vers)} version(s)"
                   + ("" if files_saved() or not st.session_state.generated_files else " · unsaved changes"))
    else:
        st.caption("No project open — the first generated version creates one.")
    _c1, _c2 = st.columns(2)
    if _c1.button("📁 Projects", use_container_width=True,
                  type="primary" if st.session_state.get("view") == "projects" else "secondary"):
        st.session_state["view"] = "studio" if st.session_state.get("view") == "projects" else "projects"
        st.rerun()
    if _c2.button("＋ New app", use_container_width=True):
        new_session()
        st.rerun()
    if st.session_state.generated_files and not files_saved() and ac.can(USER, "version.save", _proj, STORE):
        if st.button("💾 Save version", use_container_width=True):
            try:
                _v = save_version("Saved manually")
                st.toast(f"Saved version {_v['number']}")
                st.rerun()
            except PermissionError as _e:
                st.error(str(_e))
    if not STORE.durable:
        st.caption("⚠️ " + STORE.durability_note)
    if USER.source in ("local", "shared-password"):
        if st.button("Sign out", use_container_width=True):
            ac.logout(st.session_state)
            new_session()
            st.rerun()

    st.markdown("### AI model")
    if CREDENTIALS_LOADED:
        if "ai_models" not in st.session_state:
            try:
                st.session_state["ai_models"] = ai_client.available_models()
                st.session_state["ai_models_error"] = ""
            except Exception as _e:  # noqa: BLE001
                st.session_state["ai_models"], st.session_state["ai_models_error"] = [], str(_e)
        _models = [m for m in st.session_state["ai_models"] if not m.startswith("text-embedding")]
        if _models:
            try:
                _default = ai_client.pick_model(models=_models)
            except Exception:  # noqa: BLE001
                _default = _models[0]
            _cur = st.session_state.get("ai_model", _default)
            st.session_state["ai_model"] = st.selectbox(
                "Foundation model (SAP AI Core)", _models,
                index=_models.index(_cur) if _cur in _models else 0, label_visibility="collapsed")
        else:
            st.session_state["ai_model"] = None
            st.warning("No running model deployment in AI Core — designs use the rule-based fallback. "
                       + st.session_state.get("ai_models_error", ""))
    else:
        st.session_state["ai_model"] = None
        st.error("AI Core credentials not loaded — rule-based design only.")
        with st.expander("Details"):
            st.caption(CREDENTIAL_ERROR)

    st.markdown("### Generation style")
    _STYLE_TEMP = {"Precise": 0.0, "Balanced": 0.15, "Creative": 0.35}
    _gen_style = st.select_slider("Generation style", options=list(_STYLE_TEMP), value="Balanced",
                                  label_visibility="collapsed",
                                  help="Model temperature: Precise 0.0 · Balanced 0.15 · Creative 0.35")
    st.session_state["temperature"] = _STYLE_TEMP[_gen_style]
    st.caption({"Precise": "Most repeatable designs.",
                "Balanced": "Recommended — reliable with some freedom.",
                "Creative": "More varied page ideas; less predictable."}[_gen_style])

    st.markdown("### Pipeline")
    st.toggle("Analyse the requirement first", value=True, key="opt_analyze",
              help="Splits the prompt into checkable features, rules and service gaps before designing.")
    st.toggle("Self-repair invalid designs", value=True, key="opt_repair",
              help="Sends validation problems back to the AI once to fix them.")
    st.toggle("Coverage report", value=True, key="opt_coverage",
              help="Checks every requirement feature against the final design.")

    st.markdown("### Recent projects")
    _recent = STORE.list_projects(USER, see_all=ac.can(USER, "project.view_all"))[:5]
    if not _recent:
        st.caption("Saved projects appear here.")
    for _p in _recent:
        _lv = STORE.latest_version(_p["id"], with_files=False)
        st.markdown(f'<div class="ev-hist"><b>{_p["name"]}</b><br><span>{_p["mode"]} · '
                    f'{("v" + str(_lv["number"]) + " · " + time.strftime("%d %b %H:%M", time.localtime(_lv["created_at"]))) if _lv else "no version"}'
                    f'</span></div>', unsafe_allow_html=True)
        if _lv and st.button("Open", key=f"open_{_p['id']}", use_container_width=True):
            restore_version(_lv["id"])
            st.rerun()

# ──────────────────────────────────────────────────────────────────────────────
# Brand bar + stepper
# ──────────────────────────────────────────────────────────────────────────────
_pills = [((st.session_state.get("ai_model") or "No AI model"), "ok" if has_ai() else "off")]
if st.session_state.get("md_model"):
    _pills.append((f"OData V{st.session_state['md_model']['odata_version'][0]} · "
                   f"{len(st.session_state['md_model']['entity_sets'])} sets", "ok"))
st.markdown(ui.brand_bar(LOGO, _pills), unsafe_allow_html=True)

_done = [bool(st.session_state.get("md_model")), bool(st.session_state.get("bp_design")),
         bool(st.session_state.get("bp_design")) and bool(st.session_state.generated_files),
         bool(st.session_state.generated_files), bool(st.session_state.deployed_url)]
if "_goto" in st.session_state:
    st.session_state["step"] = st.session_state.pop("_goto")
    st.session_state.pop("nav_step", None)
with st.container(key="stepper"):
    for _i, (_col, (_t, _d)) in enumerate(zip(st.columns(len(ui.STEPS), gap="small"), ui.STEPS)):
        _cur = _i == st.session_state["step"]
        _mark = "✓" if _done[_i] and not _cur else str(_i + 1)
        if _col.button(f"{_mark}  {_t}", key=f"stepbtn_{_i}", help=_d, use_container_width=True,
                       type="primary" if _cur else "secondary"):
            st.session_state["step"] = _i
            st.session_state["view"] = "studio"
            st.rerun()
STEP = st.session_state["step"]


def goto(i: int):
    st.session_state["_goto"] = i
    st.rerun()


def nav_buttons(back: int = None, nxt: int = None, next_label: str = "Continue", next_disabled: bool = False,
                next_help: str = None):
    st.write("")
    c1, _, c3 = st.columns([1, 3, 1.3])
    if back is not None and c1.button("← Back", use_container_width=True, key=f"back_{STEP}"):
        goto(back)
    if nxt is not None and c3.button(f"{next_label} →", type="primary", use_container_width=True,
                                     disabled=next_disabled, help=next_help, key=f"next_{STEP}"):
        goto(nxt)


# ──────────────────────────────────────────────────────────────────────────────
# Background jobs: AI design, generation, validation and deployment keep running while the
# user switches steps, tabs or pages (a Streamlit rerun would otherwise stop them).
# ──────────────────────────────────────────────────────────────────────────────
import jobs

try:
    from streamlit.runtime.scriptrunner import get_script_run_ctx as _ctx
    SESSION_ID = _ctx().session_id
except Exception:  # noqa: BLE001
    SESSION_ID = st.session_state.setdefault("_session_id", os.urandom(8).hex())
jobs.heartbeat(SESSION_ID)

RESTORE_KEYS = ("app_mode", "user_prompt", "app_id_v", "md_edmx", "md_service_url_val", "md_extra",
                "md_use_existing_dest", "md_dest_name_val", "md_on_premise", "md_auth", "theme_v", "app_style",
                "standalone", "bp_floorplan", "bp_design", "project_id", "version_id", "version_sha",
                "deploy_meta", "build_result", "validation_report")


def job_running(kind: str):
    return jobs.running(USER.id, SESSION_ID, kind)


def start_job(kind: str, label: str, fn, *args, target_step=None, extra=None, **kwargs):
    """Start fn(say, *args, **kwargs) in the background and show its progress panel."""
    ctx = {"origin_step": st.session_state.get("step", 0), "target_step": target_step,
           "state": {k: st.session_state.get(k) for k in RESTORE_KEYS if st.session_state.get(k) is not None}}
    ctx.update(extra or {})
    jobs.start(kind, USER.id, SESSION_ID, label, fn, *args, context=ctx, **kwargs)
    st.rerun()


def _apply_job(job) -> None:
    """Hand a finished job's result to the page (runs once, in the user's session)."""
    job.applied = True
    if job.session != SESSION_ID:            # the browser reconnected: restore the inputs first
        st.session_state.update(job.context.get("state") or {})
        if st.session_state.get("md_edmx") and not st.session_state.get("md_model"):
            import metadata_parser as _mdp
            st.session_state["md_model"] = _mdp.parse_metadata(st.session_state["md_edmx"])
    if job.status == "error":
        st.session_state["_job_error"] = f"{job.label} failed: {job.error}"
        return
    try:
        _JOB_APPLY[job.kind](job)
    except Exception as e:  # noqa: BLE001
        st.session_state["_job_error"] = f"{job.label}: the result could not be applied ({e})"
        return
    st.session_state.setdefault("_job_done", f"{job.label} finished in {job.seconds:.0f}s")
    tgt = job.context.get("target_step")
    # take the user to the result only when they are still where they started it
    if tgt is not None and st.session_state.get("step") == job.context.get("origin_step") \
            and st.session_state.get("view", "studio") == "studio":
        st.session_state["_goto"] = tgt


def _job_box(job) -> None:
    with st.container(border=True, key=f"jobbox_{job.id}"):
        c1, c2 = st.columns([5, 1.3])
        c1.markdown(f"⏳ **{job.label}** · {job.seconds:.0f}s  \n"
                    "<span style='color:var(--ev-muted);font-size:.82rem'>Runs in the background — you can switch "
                    "steps, tabs or pages; the result is applied when it is ready.</span>", unsafe_allow_html=True)
        c2.markdown(ui.chip("running", "accent"), unsafe_allow_html=True)
        if job.messages:
            st.caption("  \n".join(m[:220] for m in job.messages[-5:]))


@st.fragment(run_every=2)
def _jobs_live():
    jobs.heartbeat(SESSION_ID)
    act = jobs.active(USER.id, SESSION_ID)
    finished = [j for j in act if not j.running]
    if finished:
        for j in finished:
            _apply_job(j)
        st.rerun()                           # whole page: show the result
    for j in act:
        _job_box(j)


def jobs_panel():
    for k, kind in (("_job_done", "success"), ("_job_error", "error")):
        msg = st.session_state.pop(k, None)
        if msg:
            (st.toast if kind == "success" else st.error)(("✅ " if kind == "success" else "") + msg)
    if jobs.active(USER.id, SESSION_ID):
        _jobs_live()


_JOB_SLOT = st.container()          # filled at the end of the script (all apply handlers defined)


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — Connect
# ══════════════════════════════════════════════════════════════════════════════
def _store_metadata(edmx: str, service_url: str) -> None:
    import metadata_parser as _mdp
    model = _mdp.parse_metadata(edmx)
    if st.session_state.get("md_model") and st.session_state.get("md_target_v") == "Add another service":
        _add_extra_service(edmx, service_url, model)
        return
    st.session_state["md_edmx"] = edmx
    st.session_state["md_model"] = model
    st.session_state["md_service_url_val"] = service_url
    st.session_state.pop("bp_design", None)       # a new service invalidates the design
    st.session_state.pop("standalone", None)      # …and replaces a generated backend


def _add_extra_service(edmx: str, service_url: str, model: dict) -> None:
    """Another OData service for a multi-service app (sales + purchasing + HR, …)."""
    url = (service_url or "").strip()
    if not url:
        raise ValueError("Enter the service path the deployed app should call for this service.")
    norm = "/" + url.strip("/")
    if norm == "/" + (st.session_state.get("md_service_url_val") or "").strip().strip("/") or \
            any("/" + s["service_url"].strip("/") == norm for s in st.session_state.get("md_extra", [])):
        raise ValueError("This service is already part of the app.")
    main_v = st.session_state["md_model"]["odata_version"][0]
    if model["odata_version"][0] != main_v:
        raise ValueError(f"This is an OData V{model['odata_version'][0]} service; the main service is V{main_v}. "
                         f"One app combines services of the same OData version — use the V{main_v} variant.")
    title = norm.rstrip("/").split("/")[-1]
    st.session_state.setdefault("md_extra", []).append(
        {"edmx": edmx, "service_url": url, "title": title, "sets": len(model["entity_sets"])})
    st.session_state.pop("bp_design", None)        # the app now spans more services
    st.session_state.pop("_model_for", None)


def _extra_services() -> list:
    return [{"edmx": s["edmx"], "service_url": s["service_url"], "title": s["title"]}
            for s in st.session_state.get("md_extra", [])]


def _app_model():
    """The model the app is designed against (merged when several services are connected)."""
    import app_builder as _ab
    return _ab.load_app_model(st.session_state["md_edmx"], st.session_state.get("md_service_url_val", ""),
                              _extra_services())


APP_MODES = {"sap": "🔌 Connect to SAP", "standalone": "🧱 Create a new app"}


def step_connect():
    with st.container(key="tabs_appmode"):
        mode = st.segmented_control(
            "What are you building?", list(APP_MODES), format_func=APP_MODES.get,
            default=st.session_state.get("app_mode") or "sap", key="app_mode_v",
            help="Connect to SAP: use an existing OData service and destination. Create a new app: the AI "
                 "generates the data model, a CAP backend with database and business process, and the UI.")
    mode = mode or "sap"
    if mode != (st.session_state.get("app_mode") or "sap"):
        st.session_state["app_mode"] = mode
        if mode == "sap" and st.session_state.get("standalone"):      # leave the generated backend
            for k in ("standalone", "md_model", "md_edmx", "md_service_url_val", "bp_design"):
                st.session_state.pop(k, None)
            st.session_state.generated_files = {}
    if mode == "standalone":
        step_new_app()
        return
    st.markdown(ui.header("Step 1", "Connect your OData service",
                          "The AI designs only with entity sets, fields and operations that really exist in "
                          "the service, so the generated app works against your backend from the first run."),
                unsafe_allow_html=True)
    left, right = st.columns([1.55, 1], gap="large")
    with left:
        if st.session_state.get("md_model"):
            with st.container(key="tabs_target"):
                st.segmented_control(
                    "Load as", ["Main service", "Add another service"], default="Main service", key="md_target_v",
                    help="One app can combine several OData services (e.g. sales orders, purchase orders and "
                         "employees): every service gets its own pages, linked to each other, plus a common home page.")
        with st.container(key="tabs_source"):
            mode = st.segmented_control("Source", ["Browse BTP systems", "Service URL", "Paste $metadata",
                                                   "Existing app"],
                                        default=st.session_state.get("md_mode_v", "Browse BTP systems"),
                                        key="md_mode_v")
        mode = mode or "Browse BTP systems"
        with st.container(border=True):
            if mode == "Browse BTP systems":
                _connect_btp()
            elif mode == "Existing app":
                _connect_existing()
            else:
                _connect_manual(mode)
    with right:
        _connection_summary()
    nav_buttons(nxt=1, next_label="Describe the app", next_disabled=not st.session_state.get("md_model"),
                next_help=None if st.session_state.get("md_model") else "Load a service first.")


NEW_APP_EXAMPLES = {
    "Purchase request": "A purchase request application with multiple line items, manager approval (approve / "
                        "reject with a reason), totals calculated from the items, and finance reporting by "
                        "department and month.",
    "Leave request": "A leave request app: employees request vacation or sick leave with start and end date, the "
                     "number of days is calculated, the team lead approves or rejects, and a calendar shows absences.",
    "Service tickets": "A service ticket app for our support team: tickets with priority, category and due date, "
                       "a board by status (new, in progress, resolved, closed), comments per ticket and a dashboard "
                       "of open tickets per priority.",
}


def step_new_app():
    """Standalone mode: requirement -> (clarifying questions) -> data model + process -> CAP backend."""
    import standalone as _sa
    st.markdown(ui.header("Step 1", "Describe the new application",
                          "The AI designs the data model, the business process (e.g. approvals) and sample data. "
                          "The studio then generates a CAP backend with its own database and the Fiori UI on top."),
                unsafe_allow_html=True)
    st.session_state.setdefault("user_prompt", "")
    if "_pending_template" in st.session_state:
        st.session_state["user_prompt"] = st.session_state.pop("_pending_template")
    left, right = st.columns([1.35, 1], gap="large")
    with left:
        ex = st.pills("Start from an example", list(NEW_APP_EXAMPLES), key="sa_ex_pick", selection_mode="single")
        if ex and st.session_state.get("_sa_ex_last") != ex:
            st.session_state["_sa_ex_last"] = ex
            st.session_state["_pending_template"] = NEW_APP_EXAMPLES[ex]
            st.rerun()
        st.text_area("What would you like to build?", key="user_prompt", height=170,
                     placeholder="e.g. Create a purchase request application with multiple line items, manager "
                                 "approval and finance reporting.")
        req = (st.session_state.get("user_prompt") or "").strip()
        c1, c2 = st.columns(2)
        qbusy = job_running("questions")
        if c1.button("⏳ Reviewing…" if qbusy else "❓ Check the requirement", use_container_width=True,
                     disabled=not (req and has_ai()) or bool(qbusy),
                     help="The AI asks questions when roles, approval levels or data are unclear."
                     if has_ai() else "Needs an AI model."):
            start_job("questions", "Reviewing the requirement",
                      lambda say, r, fn: {"req": r, "questions": _sa.clarifying_questions(r, fn)}, req, ai_fn())
        qs = st.session_state.get("sa_questions") or []
        answers = {}
        if qs and st.session_state.get("sa_questions_for") == req:
            with st.container(border=True):
                st.markdown("**The AI needs a few details**")
                for q in qs:
                    opts = q["options"]
                    if opts:
                        a = st.selectbox(q["question"], ["(let the AI decide)"] + opts + ["Other…"], key=f"sa_q_{q['id']}",
                                         help=q.get("why") or None)
                        if a == "Other…":
                            a = st.text_input("Your answer", key=f"sa_qo_{q['id']}", label_visibility="collapsed")
                        elif a == "(let the AI decide)":
                            a = ""
                    else:
                        a = st.text_input(q["question"], key=f"sa_q_{q['id']}", help=q.get("why") or None)
                    if (a or "").strip():
                        answers[q["question"]] = a
        busy = job_running("domain")
        if c2.button("⏳ Designing…" if busy else "🧱 Design data model", type="primary", use_container_width=True,
                     disabled=not req or bool(busy)):
            start_job("domain", "Designing the data model and process",
                      lambda say, r, fn, ans: _sa.design_domain(r, fn, ans, progress=say), req, ai_fn(), answers)
    with right:
        draft = st.session_state.get("sa_draft")
        cur = st.session_state.get("standalone")
        if not draft and not cur:
            st.markdown(ui.empty("🧱", "No data model yet",
                                 "Describe the app and choose Design data model. You can review and edit the "
                                 "entities, fields and workflow before anything is generated."),
                        unsafe_allow_html=True)
        else:
            domain = (draft or {}).get("domain") or cur["domain"]
            _domain_review(domain, (draft or {}).get("notes") or [])
    if st.session_state.get("sa_draft"):
        _domain_editor()
    nav_buttons(nxt=1, next_label="Describe the screens", next_disabled=not st.session_state.get("standalone"),
                next_help=None if st.session_state.get("standalone") else "Accept a data model first.")


def _apply_questions(job):
    st.session_state["sa_questions"] = job.result["questions"]
    st.session_state["sa_questions_for"] = job.result["req"]
    if not job.result["questions"]:
        st.session_state["_job_done"] = "The requirement is clear enough — design the data model."


def _apply_domain(job):
    st.session_state["sa_draft"] = job.result
    st.session_state["_sa_json_next"] = json.dumps(job.result["domain"], indent=2)
    st.session_state["app_mode"] = "standalone"


def _domain_review(domain, notes):
    import standalone as _sa
    wf = domain.get("workflow")
    st.markdown(ui.metrics([("Entities", len(domain["entities"]), domain["title"], True),
                            ("Fields", sum(len(e["fields"]) for e in domain["entities"]), "incl. calculated"),
                            ("Workflow", len(wf["transitions"]) if wf else 0, "transitions" if wf else "none")]),
                unsafe_allow_html=True)
    with st.container(border=True):
        for e in domain["entities"]:
            st.markdown(f"**{e['label']}** <code>{e['name']}</code>" + (f" · item of {e['parent']}" if e.get("parent") else ""),
                        unsafe_allow_html=True)
            st.caption(" · ".join(f"{f['label']}{'*' if f.get('required') else ''}"
                                  + (" (calc.)" if f.get("formula") else "") for f in e["fields"]))
        if wf:
            st.markdown("**Process** — " + " → ".join(s["label"] for s in wf["states"]))
            st.caption(" · ".join(f"{t['label']}: {'/'.join(t['from'])} → {t['to']}"
                                  + (f" (role {t['role']})" if t.get("role") and wf.get("enforce_roles") else "")
                                  for t in wf["transitions"]))
        for n in notes:
            st.caption("ℹ️ " + n)
    cur = st.session_state.get("standalone") or {}
    db = st.radio("Database of the deployed app", ["sqlite", "hana"], horizontal=True, key="sa_db",
                  index=0 if cur.get("db", "sqlite") == "sqlite" else 1,
                  format_func={"sqlite": "SQLite in memory (demo — data resets on restart)",
                               "hana": "SAP HANA Cloud (persistent, needs an HDI container)"}.get)
    if db == "hana":
        st.caption("⚠️ HANA needs a HANA Cloud instance in the subaccount (an hdi-shared service is created at "
                   "deploy) and Node.js to build the database module — build it in SAP Business Application Studio.")
    is_current = cur.get("domain") == domain and cur.get("db") == db
    if st.button("✅ Use this data model" if not is_current else "✓ Data model in use", type="primary",
                 use_container_width=True, disabled=is_current):
        _use_domain(domain, db)
        st.rerun()


def _domain_editor():
    import standalone as _sa
    with st.expander("{ }  Data model JSON (expert)"):
        if "_sa_json_next" in st.session_state:
            st.session_state["sa_json"] = st.session_state.pop("_sa_json_next")
        st.text_area("Domain JSON", key="sa_json", height=320, label_visibility="collapsed")
        if st.button("Apply JSON changes", key="sa_json_apply"):
            try:
                dom, notes = _sa.validate_domain(json.loads(st.session_state["sa_json"]))
                st.session_state["sa_draft"] = {"domain": dom, "notes": notes, "source": "edited"}
                st.session_state["_sa_json_next"] = json.dumps(dom, indent=2)
                st.rerun()
            except ValueError as e:
                st.error(f"Not valid JSON: {e}")


def _use_domain(domain, db):
    """Generated backend: its EDMX drives the normal design pipeline."""
    import standalone as _sa
    import metadata_parser as _mdp
    edmx = _sa.to_edmx(domain)
    st.session_state.update(md_edmx=edmx, md_model=_mdp.parse_metadata(edmx), md_service_url_val=_sa.service_path(domain),
                            md_extra=[], md_use_existing_dest=False, md_dest_name_val="", md_on_premise=False,
                            md_auth=AUTH_NONE, standalone={"domain": domain, "db": db}, app_mode="standalone")
    if not st.session_state.get("app_id_v") or st.session_state.get("app_id_v") == "com.mycompany.app":
        st.session_state["app_id_v"] = "com.evoke." + re.sub(r"[^a-z0-9]", "", domain["name"].lower())[:30]
    for k in ("bp_design", "_model_for", "sa_draft"):
        st.session_state.pop(k, None)
    st.session_state.generated_files = {}


def _connect_btp():
    import btp_discovery as _bd
    env = _bd.runtime_environment()
    st.caption(_bd.describe_environment(env))
    bound = _bd.DestinationClient.from_vcap() if env == "connectivity" else None
    instance = None
    if bound:
        st.markdown(f"**Destination service** — {bound.source}")
    else:
        if "bd_target" not in st.session_state:
            st.session_state["bd_target"] = _bd.cf_target()
        tgt = st.session_state["bd_target"]
        if not tgt:
            st.error("Not logged in to Cloud Foundry. Run `cf login -a <api-endpoint> --sso`, choose your "
                     "space, then retry.")
            if st.button("↻ Retry", key="bd_retry_login"):
                for k in ("bd_target", "bd_instances"):
                    st.session_state.pop(k, None)
                st.rerun()
            return
        st.markdown(f"**Cloud Foundry** — org `{tgt['org']}` · space `{tgt['space']}`")
        if "bd_instances" not in st.session_state:
            try:
                with st.spinner("Listing destination service instances..."):
                    st.session_state["bd_instances"] = _bd.list_destination_instances()
            except _bd.DiscoveryError as e:
                st.session_state["bd_instances"] = []
                st.error(str(e))
        insts = st.session_state["bd_instances"]
        if _bd.DEFAULT_INSTANCE in insts:
            insts = [_bd.DEFAULT_INSTANCE] + [i for i in insts if i != _bd.DEFAULT_INSTANCE]
        c1, c2 = st.columns([3, 1])
        with c1:
            instance = st.selectbox("Destination service instance", insts or ["(none in this space)"],
                                    key="bd_instance", disabled=not insts,
                                    help="Any instance sees every subaccount-level destination. The studio reads "
                                         f"a service key named '{_bd.DEFAULT_KEY}' (created if missing).")
            if not insts:
                instance = None
        with c2:
            st.markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
            if _bd.DEFAULT_INSTANCE not in insts and st.button("➕ Create own", key="bd_create_inst",
                                                               help=f"cf create-service destination lite {_bd.DEFAULT_INSTANCE}"):
                try:
                    with st.spinner(f"Creating {_bd.DEFAULT_INSTANCE}..."):
                        _bd.create_destination_instance()
                    st.session_state.pop("bd_instances", None)
                    st.session_state["bd_instance"] = _bd.DEFAULT_INSTANCE
                    st.rerun()
                except _bd.DiscoveryError as e:
                    st.error(str(e))
    if not (bound or instance):
        return
    if st.button("🔌 Load backend systems", key="bd_load_dests", use_container_width=True):
        try:
            with st.spinner("Reading destinations..."):
                client = bound or _bd.destination_client(instance)
                st.session_state["bd_client"] = client
                st.session_state["bd_dests"] = client.list_destinations()
            for k in ("bd_backend", "bd_services", "bd_service_warnings"):
                st.session_state.pop(k, None)
        except _bd.DiscoveryError as e:
            st.error(str(e))
    dests = st.session_state.get("bd_dests")
    if dests is not None and not dests:
        st.warning("No HTTP destinations are visible. Create one in the BTP cockpit (Connectivity → Destinations).")
    if not dests:
        return
    by_name = {d["Name"]: d for d in dests}

    def _lab(n):
        d = by_name[n]
        mark = "" if _bd.reachable(d.get("ProxyType", ""), env) else "  ⛔ not reachable here"
        return f"{n} · {d.get('ProxyType', 'Internet')} · {d.get('Authentication', '')}{mark}"

    dest = st.selectbox("Backend system (destination)", list(by_name), key="bd_dest", format_func=_lab)
    d = by_name[dest]
    st.caption(f"URL `{d.get('URL', '-')}`" + (f" · sap-client {d['sap-client']}" if d.get("sap-client") else ""))
    if env == "bas" and str(d.get("WebIDEEnabled", "")).lower() != "true":
        st.warning("This destination lacks WebIDEEnabled=true, which the BAS proxy needs.")
    if st.session_state.get("bd_backend") and st.session_state["bd_backend"].name != dest:
        for k in ("bd_backend", "bd_services", "bd_service_warnings"):
            st.session_state.pop(k, None)
    reachable = _bd.reachable(d.get("ProxyType", ""), env)
    if st.button("📚 List OData services", key="bd_list_services", use_container_width=True, disabled=not reachable):
        try:
            with st.spinner(f"Reading the Gateway catalog of {dest}..."):
                be = _bd.open_backend(st.session_state["bd_client"], dest, env)
                svcs, warns = _bd.list_services(be)
            st.session_state.update(bd_backend=be, bd_services=svcs, bd_service_warnings=warns)
        except _bd.DiscoveryError as e:
            st.error(str(e))
    if not reachable:
        st.info("On-premise destinations need SAP Business Application Studio or this studio deployed on BTP with "
                "the connectivity service. From here, use 'Paste $metadata'.")
    svcs = st.session_state.get("bd_services")
    if not (svcs and st.session_state.get("bd_backend")):
        return
    for w in st.session_state.get("bd_service_warnings", []):
        st.caption("⚠️ " + w)
    labels = {s["path"]: s for s in svcs}
    picked = st.selectbox(f"OData service ({len(svcs)} published — type to search)", list(labels), key="bd_service",
                          format_func=lambda p: f"{labels[p]['name']} — {labels[p]['title']}  (V{labels[p]['odata_version'][0]})")
    typed = st.text_input("…or a service path not in the catalog", key="bd_service_typed",
                          placeholder="/sap/opu/odata/sap/ZMY_SRV/")
    path = typed.strip() or picked
    if st.button("🔍 Load service metadata", key="bd_fetch", type="primary", use_container_width=True):
        be = st.session_state["bd_backend"]
        try:
            with st.spinner("Fetching $metadata..."):
                edmx = _bd.fetch_metadata(be, path)
            _store_metadata(edmx, path)
            st.session_state["md_use_existing_dest"] = True
            st.session_state["md_dest_name_val"] = be.name
            st.session_state["md_on_premise"] = be.proxy_type.lower() == "onpremise"
            st.rerun()
        except Exception as e:  # noqa: BLE001
            st.error(f"Metadata fetch/parse failed: {e}")


def _connect_manual(mode: str):
    url = st.text_input("Service path the deployed app will call",
                        value=st.session_state.get("md_service_url_val", ""),
                        placeholder="/sap/opu/odata/sap/API_SALES_ORDER_SRV/", key="md_service_url",
                        help="For 'Service URL' it must be absolute (https://host/path/) so the metadata can be "
                             "fetched from here; for 'Paste' a relative path is enough.")
    user = pw = edmx_in = ""
    verify = True
    if mode == "Service URL":
        c1, c2 = st.columns(2)
        user = c1.text_input("User (optional)", key="md_user")
        pw = c2.text_input("Password (optional)", type="password", key="md_pw")
        verify = not st.checkbox("Ignore SSL certificate errors (self-signed gateways)", key="md_verify")
    else:
        edmx_in = st.text_area("$metadata XML", height=180, key="md_edmx_in",
                               placeholder='<?xml version="1.0" encoding="utf-8"?><edmx:Edmx ...')
    if st.button("🔍 Load service metadata", type="primary", use_container_width=True, key="md_fetch"):
        import odata_service
        try:
            edmx = (odata_service.get_metadata("direct", service_url=url, user=user or None,
                                               password=pw or None, verify_ssl=verify)
                    if mode == "Service URL" else odata_service.get_metadata("manual", edmx_text=edmx_in))
            _store_metadata(edmx, url)
            st.rerun()
        except Exception as e:  # noqa: BLE001
            st.error(f"Metadata fetch/parse failed: {e}")


CF_DEFAULTS = {"api": "https://api.cf.us10-001.hana.ondemand.com",
               "org": "EVOKE TECHNOLOGIES SP. Z O.O._evokebtp-subaccount-qt2znvbe", "space": "BAS_DEV"}


def _cf_home():
    """Own CF session per browser session when the studio runs on BTP (multi-user); locally
    the developer's own cf login is used."""
    if not os.environ.get("VCAP_APPLICATION"):
        return None
    if "cf_home" not in st.session_state:
        st.session_state["cf_home"] = tempfile.mkdtemp(prefix="cfhome_")
    return st.session_state["cf_home"]


def _cf_target_inputs(prefix: str):
    c = st.session_state.setdefault("cf_target", dict(CF_DEFAULTS))
    c1, c2, c3 = st.columns([1.3, 1.3, 0.6])
    c["api"] = c1.text_input("API endpoint", value=c["api"], key=f"{prefix}_api")
    c["org"] = c2.text_input("Org", value=c["org"], key=f"{prefix}_org")
    c["space"] = c3.text_input("Space", value=c["space"], key=f"{prefix}_space")
    return c


def _cf_signin(prefix: str) -> bool:
    """Cloud Foundry sign-in with an SSO passcode or user + password. True when signed in."""
    t = st.session_state.setdefault("cf_target", dict(CF_DEFAULTS))
    dep = CFDeployer(api_url=t["api"], org=t["org"], space=t["space"], cf_home=_cf_home())
    who = dep.whoami() if dep.check_cf_cli() else ""
    if who:
        c1, c2 = st.columns([4, 1])
        c1.markdown(ui.chip("Signed in", "ok") + f" <span style='font-size:.86rem'>{who}</span>",
                    unsafe_allow_html=True)
        if c2.button("Sign out", key=f"{prefix}_logout", use_container_width=True):
            for _ in dep._run(["cf", "logout"]):
                pass
            st.rerun()
        return True
    if not dep.check_cf_cli():
        st.warning("The Cloud Foundry CLI is not available on this host.")
        return False
    t = _cf_target_inputs(prefix)
    dep = CFDeployer(api_url=t["api"], org=t["org"], space=t["space"], cf_home=_cf_home())
    mode = st.segmented_control("Sign in with", ["SSO passcode", "User + password"], default="SSO passcode",
                                key=f"{prefix}_mode") or "SSO passcode"
    if mode == "SSO passcode":
        if f"{prefix}_pcurl" not in st.session_state or st.session_state.get(f"{prefix}_pcapi") != t["api"]:
            st.session_state[f"{prefix}_pcurl"] = dep.passcode_url()
            st.session_state[f"{prefix}_pcapi"] = t["api"]
        url = st.session_state[f"{prefix}_pcurl"]
        st.markdown(f"1. Open **[{url}]({url})** and sign in with your BTP user.  \n"
                    "2. Copy the **Temporary Authentication Code** and paste it below — it is single-use and "
                    "expires after a few minutes.")
        code = st.text_input("Passcode", type="password", key=f"{prefix}_pc")
        go = st.button("Sign in", type="primary", disabled=not (code or "").strip(), key=f"{prefix}_go")
        if go:
            _cf_do_login(dep, dep.login_sso(code), prefix)
    else:
        c1, c2 = st.columns(2)
        user = c1.text_input("User (e-mail)", key=f"{prefix}_user")
        pw = c2.text_input("Password", type="password", key=f"{prefix}_pw")
        origin = st.text_input("Identity provider origin (optional)", key=f"{prefix}_origin",
                               placeholder="leave empty for SAP ID service; e.g. the origin key of a custom IdP",
                               help="Users of a custom identity provider (e.g. SAP Cloud Identity) need its origin "
                                    "key (BTP cockpit → Trust Configuration). Users with two-factor "
                                    "authentication must use the SSO passcode.")
        go = st.button("Sign in", type="primary", disabled=not (user and pw), key=f"{prefix}_go2")
        if go:
            _cf_do_login(dep, dep.login(user, pw, origin), prefix)
    return False


def _cf_do_login(dep, lines, prefix):
    out = []
    with st.spinner("Signing in to Cloud Foundry…"):
        for line in lines:
            out.append(line)
    if dep.whoami():
        st.session_state.pop(f"{prefix}_pc", None)
        st.session_state.pop(f"{prefix}_pw", None)
        st.rerun()
    st.error("Sign-in failed.")
    with st.expander("Details", expanded=True):
        st.code("\n".join(l for l in out if l)[-3000:] or "(no output)", language="text")


def _connect_existing():
    """Change an existing app: one generated by this studio (its design is restored) or a
    client Fiori app (Fiori Elements: enhanced in place; freestyle: AI code changes)."""
    import app_import
    st.caption("Re-open an app generated by this studio to change and redeploy it, or import a client's "
               "Fiori project to enhance it.")
    src = st.radio("From", ["Deployed app on Cloud Foundry", "Project ZIP"], horizontal=True, key="imp_src")
    data = None
    if src == "Project ZIP":
        up = st.file_uploader("Project ZIP (the whole project folder, or the ZIP downloaded from this studio)",
                              type=["zip"], key="imp_zip")
        if up is not None and st.button("📂 Open project", type="primary", use_container_width=True):
            data = up.getvalue()
    else:
        if not _cf_signin("imp"):
            return
        if st.button("↻ List apps in the targeted space", key="imp_list"):
            try:
                st.session_state["imp_apps"] = app_import.list_cf_apps(cf_home=_cf_home())
            except Exception as e:  # noqa: BLE001
                st.error(f"Could not list apps: {e}")
        apps = st.session_state.get("imp_apps") or []
        if apps:
            name = st.selectbox("App", [a["name"] for a in apps], key="imp_app",
                                format_func=lambda n: n + ("  · " + next(a["state"] for a in apps if a["name"] == n)))
            if st.button("⬇ Open deployed app", type="primary", use_container_width=True):
                try:
                    with st.spinner(f"Downloading {name} from Cloud Foundry…"):
                        data = app_import.download_cf_app(name, cf_home=_cf_home())
                except Exception as e:  # noqa: BLE001
                    st.error(f"Download failed: {e}")
    if data is None:
        return
    try:
        text, binary = app_import.read_zip(data)
        info = app_import.analyze(text)
    except app_import.ImportError_ as e:
        st.error(str(e))
        return
    _apply_import(text, binary, info)


def _apply_import(text, binary, info):
    import app_import
    import blueprint as _bpm
    st.session_state["import"] = {"kind": info["kind"], "text": text, "binary": binary, "original": dict(text),
                                  "info": {k: v for k, v in info.items() if k != "metadata"}}
    st.session_state.generated_files = {}
    st.session_state["md_target_v"] = "Main service"
    if info["metadata"]:
        _store_metadata(info["metadata"], info["service_path"])
    st.session_state["md_extra"] = [
        {"edmx": s["edmx"], "service_url": s["service_url"], "title": s.get("title") or s["service_url"],
         "sets": s.get("sets", 0)} for s in info.get("extra_services") or []]
    st.session_state.pop("_model_for", None)
    if info["kind"] == "generated":
        d = info["design"]
        st.session_state.update(
            user_prompt=d.get("requirement", ""), app_id_v=d.get("app_id") or info["app_id"],
            md_service_url_val=d.get("service_url") or info["service_path"],
            md_use_existing_dest=bool(d.get("use_existing_destination")),
            md_dest_name_val=d.get("destination", ""), md_on_premise=bool(d.get("on_premise")),
            md_auth=d.get("auth_type") or AUTH_NONE)
        st.session_state["bp_design"] = {"blueprint": d["blueprint"], "notes": [], "source": "imported",
                                         "spec": d.get("spec"), "coverage": d.get("coverage") or [],
                                         "steps": [{"step": "import", "ok": True, "seconds": 0,
                                                    "detail": f"design of {d.get('app_id')} restored (saved {d.get('saved_at', '')})"}]}
        st.session_state["_bp_json_next"] = json.dumps(d["blueprint"], indent=2)
        st.session_state["import_mode"] = "generated"
        if d.get("standalone"):              # app with a generated CAP backend: restore its data model
            st.session_state.update(standalone=d["standalone"], app_mode="standalone")
        goto(2)
    if not st.session_state.get("md_model"):
        st.warning("The project has no local service metadata — load the service with one of the other sources, "
                   "then open the project again.")
        return
    st.session_state["app_id_v"] = info["app_id"] or "com.client.app"
    st.session_state["md_service_url_val"] = info["service_path"]
    if info["kind"] in ("fe_v4", "fe_v2"):
        raw, notes = app_import.blueprint_from_fe(text, info, _model())
        bp, vnotes = _bpm.validate(raw, _model(), "")
        st.session_state["user_prompt"] = f"Existing Fiori Elements app '{bp['app']['title']}'"
        st.session_state["bp_design"] = {"blueprint": bp, "notes": notes + vnotes, "source": "client app",
                                         "spec": None, "coverage": [],
                                         "steps": [{"step": "import", "ok": True, "seconds": 0,
                                                    "detail": f"{info['kind'].upper()} app {info['app_id']} — enhance in place"}]}
        st.session_state["_bp_json_next"] = json.dumps(bp, indent=2)
        st.session_state["import_mode"] = "client_fe"
        goto(2)
    st.session_state["import_mode"] = "client_code"
    st.session_state["bp_design"] = None
    st.session_state["code_changes"] = []
    goto(2)


def _connection_summary():
    m = st.session_state.get("md_model")
    if not m:
        st.markdown(ui.empty("🔌", "No service loaded yet",
                             "Pick a BTP backend system and one of its OData services — or paste its "
                             "$metadata. Nothing is written to the backend at design time."),
                    unsafe_allow_html=True)
        return
    extra = st.session_state.get("md_extra", [])
    if extra:
        try:
            m = _model()
        except Exception as e:  # noqa: BLE001
            st.error(f"The services cannot be combined: {e}")
        with st.container(border=True):
            st.markdown(f"**{1 + len(extra)} services in this app**")
            st.markdown(ui.chip("main", "accent") + f" <code>{st.session_state.get('md_service_url_val', '')}</code>",
                        unsafe_allow_html=True)
            for i, s in enumerate(list(extra)):
                c1, c2 = st.columns([5, 1])
                c1.markdown(ui.chip(f"{s['sets']} sets", "ok") + f" <code>{s['service_url']}</code>",
                            unsafe_allow_html=True)
                if c2.button("✕", key=f"md_extra_rm_{i}", help="Remove this service from the app"):
                    extra.pop(i)
                    st.session_state.pop("bp_design", None)
                    st.session_state.pop("_model_for", None)
                    st.rerun()
            st.caption("Each service gets its own pages; records are linked across services (e.g. a purchase "
                       "order item opens its product), and the home page shows KPIs of all services.")
    sets = m["entity_sets"]
    ops = len(m.get("operations", []))
    st.markdown(ui.metrics([("OData", f"V{m['odata_version'][0]}", st.session_state.get("md_service_url_val") or "", True),
                            ("Entity sets", len(sets), f"{sum(len(s['properties']) for s in sets)} fields"),
                            ("Operations", ops, "actions / functions")]), unsafe_allow_html=True)
    with st.container(border=True):
        st.markdown("**Deployed app → backend**")
        st.session_state["md_use_existing_dest"] = st.checkbox(
            "Use an existing BTP destination", value=st.session_state.get("md_use_existing_dest", False),
            key="md_use_existing_cb", help="Required for on-premise systems via Cloud Connector.")
        if st.session_state["md_use_existing_dest"]:
            c1, c2 = st.columns([1.4, 1])
            st.session_state["md_dest_name_val"] = c1.text_input(
                "Destination", value=st.session_state.get("md_dest_name_val", ""), placeholder="EvokeSD4",
                key="md_dest_name_cb")
            st.session_state["md_on_premise"] = c2.checkbox(
                "On-premise", value=st.session_state.get("md_on_premise", True), key="md_on_premise_cb",
                help="Binds the Connectivity service so the approuter can reach Cloud Connector.")
        else:
            st.session_state["md_dest_name_val"] = ""
            st.session_state["md_on_premise"] = False
            st.session_state["md_auth"] = st.selectbox(
                "Authentication of the NEW destination", AUTH_CHOICES, key="md_auth_sel",
                help="Credentials are entered only at deploy time and never stored.")
    with st.expander(f"Entity sets ({len(sets)})"):
        rows = [{"Entity set": es["name"], "Label": es.get("label", ""), "Keys": ", ".join(es["keys"]),
                 "Fields": len(es["properties"]),
                 "CRUD": "".join(c if es.get(f) else "–" for c, f in (("C", "creatable"), ("U", "updatable"), ("D", "deletable"))),
                 "Navigations": len(es.get("navigations", []))} for es in sets]
        st.dataframe(rows, hide_index=True, use_container_width=True, height=min(38 * len(rows) + 40, 360))


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — Describe
# ══════════════════════════════════════════════════════════════════════════════
EXAMPLES = {
    "Sales desk": "A custom sales order desk: a home dashboard with open order count, total net value and a chart "
                  "of orders by sales organization; a kanban board of orders by delivery status; a calendar of "
                  "requested delivery dates; an order list with tabs per status that highlights overdue orders "
                  "in red and shows the net value incl. 19% VAT; creating an order defaults the delivery date "
                  "to 14 days from today and rejects dates in the past; release/reject approval for orders in "
                  "approval; an analysis page with net value per month.",
    "Approval inbox": "An approval inbox for managers: tabs by approval status with counts, a split view with the "
                      "list on the left and the document on the right, approve and reject actions only for "
                      "documents waiting for approval, and a highlight for documents older than 30 days.",
    "Inventory control": "An inventory control app: a product browser with list and details side by side, flag "
                         "products below their reorder level as critical, show the stock value (units × unit "
                         "price), a 'Reorder' action that adds 50 to the units on order, and an analysis of stock "
                         "value by category.",
    "Standard List Report": "A standard Fiori Elements List Report and Object Page to browse and maintain the "
                            "main business object, with filters on the key attributes, status colours and the "
                            "item table on the object page. German and English texts.",
    "Overview page": "An overview page with cards: recent documents, a table of open items, and an analytical card "
                     "of the main amount by status.",
}


def step_describe():
    import blueprint as _bpm
    if not st.session_state.get("md_model"):
        st.markdown(ui.empty("🔌", "Connect a service first", "The AI needs the real service to design against."),
                    unsafe_allow_html=True)
        nav_buttons(back=0)
        return
    st.markdown(ui.header("Step 2", "Describe the application",
                          "Write it the way you would brief a developer: who uses it, which pages, what they "
                          "see, which rules and calculations apply, what users can change. Standard Fiori "
                          "floorplans and fully custom multi-page apps are both supported."),
                unsafe_allow_html=True)
    if st.session_state.get("standalone"):
        st.info(f"New app with a generated backend — data model **{st.session_state['standalone']['domain']['title']}** "
                f"({st.session_state.get('md_service_url_val')}). Add what the screens should show; the data model "
                "itself is changed in step 1.", icon="🧱")
    st.session_state.setdefault("user_prompt", "")
    if "_pending_template" in st.session_state:
        st.session_state["user_prompt"] = st.session_state.pop("_pending_template")

    left, right = st.columns([1.7, 1], gap="large")
    with left:
        ex = st.pills("Start from an example", list(EXAMPLES), key="ex_pick", selection_mode="single")
        if not ex:
            st.session_state.pop("_ex_last", None)
        elif st.session_state.get("_ex_last") != ex:
            st.session_state["_ex_last"] = ex
            st.session_state["_pending_template"] = EXAMPLES[ex]
            st.rerun()
        st.text_area("Requirement", key="user_prompt", height=230, label_visibility="collapsed",
                     placeholder="e.g. A service ticket board for our support team: tickets as cards in columns "
                                 "by status (drag to change), a calendar of due dates, a ticket list that highlights "
                                 "overdue tickets, a create wizard, and a dashboard with open tickets per priority…")
        st.caption("Tip: mention calculations (\"show …\"), rules (\"must not …\", \"only when …\"), defaults, "
                   "highlights, page types (dashboard, board, calendar, analysis, split view, wizard) and languages.")
    with right:
        with st.container(border=True):
            st.markdown("**App settings**")
            st.text_input("App ID (namespace)", value=st.session_state.get("app_id_v", "com.mycompany.app"),
                          key="app_id_v", help="Reverse-domain id, e.g. com.evoke.salesdesk — also the CF app name.")
            fp_opts = ["auto"] + list(_bpm.FLOORPLANS)
            st.selectbox("Floorplan", fp_opts, key="bp_floorplan",
                         format_func=lambda k: "Let the AI decide (recommended)" if k == "auto"
                         else f"{k} — {_bpm.FLOORPLANS[k][1]}")
            st.selectbox("SAP theme", ["sap_horizon", "sap_horizon_dark", "sap_fiori_3", "sap_belize"], key="theme_v")
        _look_and_feel()

    st.write("")
    c1, c2, c3 = st.columns([1, 2.2, 1.4])
    if c1.button("← Back", use_container_width=True):
        goto(0)
    ready = bool(st.session_state["user_prompt"].strip())
    busy = job_running("design")
    if c3.button("⏳ Designing…" if busy else "✨ Design my app", type="primary", use_container_width=True,
                 disabled=not ready or bool(busy)):
        _run_design()


def _run_design():
    """Start the design in the background (inputs are captured now; the job never reads the session)."""
    fp = st.session_state.get("bp_floorplan", "auto")
    fn = ai_fn()
    opts = {"analyze_first": st.session_state.get("opt_analyze", True),
            "repair": st.session_state.get("opt_repair", True),
            "check_coverage": st.session_state.get("opt_coverage", True),
            "cache_tag": f"{st.session_state.get('ai_model')}|{st.session_state.get('temperature')}"} if fn else {}
    start_job("design", "Designing your application", _w_design, st.session_state["user_prompt"],
              st.session_state.get("standalone"), st.session_state["md_edmx"],
              st.session_state.get("md_service_url_val", ""), _extra_services(),
              _app_model() if fn and not st.session_state.get("standalone") else None,
              fn, "" if fp == "auto" else fp, st.session_state.get("app_style"), opts, target_step=2)


def _w_design(say, requirement, sa_state, edmx, service_url, extra, model, fn, fp, style, opts):
    import app_builder as _ab
    import ai_designer as _ad
    if sa_state:
        return _ab.design_standalone(requirement, sa_state["domain"], fn, fp, style, progress=say, **opts)
    if fn:
        return _ad.design(requirement, model, fn, fp, style, progress=say, **opts)
    say("No AI model — using the rule-based designer.")
    return _ab.design(requirement, edmx, None, fp, style, service_url=service_url, extra_services=extra)


def _apply_design(job):
    res = job.result
    st.session_state["bp_design"] = {k: res.get(k) for k in ("blueprint", "notes", "source", "spec", "coverage", "steps")}
    st.session_state["_bp_json_next"] = json.dumps(res["blueprint"], indent=2)
    st.session_state["refine_history"] = []
    st.session_state.generated_files = {}


def _look_and_feel():
    import style as _style
    roles = {"brand": "Brand", "accent": "Accent", "shell": "Header", "shell_text": "Header text",
             "background": "Background", "surface": "Cards", "text": "Text", "title": "Titles", "link": "Links",
             "positive": "Success", "critical": "Warning", "negative": "Error"}

    def seed(stl):
        for r in _style.COLOR_ROLES:
            st.session_state[f"stl_c_{r}"] = stl["colors"].get(r) or ""
        st.session_state.update(stl_theme=stl["base_theme"], stl_font=stl["font"]["family"],
                                stl_head_font=stl["font"]["heading_family"], stl_radius=stl["radius"] or "default",
                                stl_density=stl["density"], stl_header=stl["header"] == "brand")

    if "_pending_style" in st.session_state:
        seed(st.session_state.pop("_pending_style"))
    with st.expander("🎨 Look & feel (optional)", expanded=bool(st.session_state.get("app_style"))):
        st.caption("Upload screenshots, mockups, brand sheets or a style-guide PDF, or describe the style. Colours "
                   "are measured from the pixels; the AI assigns roles, fonts, corners and density.")
        refs = st.file_uploader("References", accept_multiple_files=True, key="style_refs",
                                type=["png", "jpg", "jpeg", "webp", "gif", "pdf", "css", "less", "scss", "json", "txt", "md"])
        text = st.text_area("Style description", key="style_text", height=70,
                            placeholder="e.g. dark header, rounded corners, Inter, brand red #E30613")
        logo = st.file_uploader("Logo (freestyle app headers)", type=["png", "jpg", "jpeg", "svg", "webp"], key="style_logo")
        b1, b2 = st.columns(2)
        an = b1.button("Analyze style", use_container_width=True, disabled=not (refs or (text or "").strip()))
        if b2.button("SAP standard", use_container_width=True):
            st.session_state.pop("app_style", None)
            st.session_state["_pending_style"] = _style.default_style()
            st.rerun()
        if an:
            imgs, docs, texts = [], [], [(text or "").strip()]
            for f in refs or []:
                data, mime, name = f.getvalue(), (f.type or "").lower(), f.name
                if mime.startswith("image/"):
                    imgs.append((data, "image/jpeg" if mime == "image/jpg" else mime, name))
                elif mime == "application/pdf" or name.lower().endswith(".pdf"):
                    docs.append((data, "application/pdf", name))
                else:
                    texts.append(f"--- {name} ---\n" + data.decode("utf-8", "replace")[:8000])
            mm = None
            if has_ai():
                try:
                    mm = ai_client.make_multimodal_fn(st.session_state["ai_model"])
                except Exception as e:  # noqa: BLE001
                    st.warning(f"Multimodal AI unavailable ({e}); using measured colours only.")
            with st.spinner("Analyzing the references..."):
                stl, notes = _style.analyze(mm, images=imgs, documents=docs, text="\n\n".join(t for t in texts if t))
            st.session_state.update(app_style=stl, app_style_notes=notes, _pending_style=stl)
            st.rerun()
        if logo is not None:
            st.image(logo, width=90)
        if not st.session_state.get("app_style"):
            return
        with st.popover("Fine-tune", use_container_width=True):
            c1, c2 = st.columns(2)
            c1.selectbox("Base theme", list(_style.THEMES), key="stl_theme")
            c2.selectbox("Corners", ["default"] + list(_style.RADII), key="stl_radius")
            c1.text_input("Font (Google Font)", key="stl_font")
            c2.text_input("Heading font", key="stl_head_font")
            c1.selectbox("Density", ["compact", "cozy"], key="stl_density")
            c2.checkbox("Brand-coloured headers", key="stl_header")
            cols = st.columns(3)
            for i, r in enumerate(_style.COLOR_ROLES):
                with cols[i % 3]:
                    cur = st.session_state.get(f"stl_c_{r}") or ""
                    if st.checkbox(roles[r], value=bool(cur), key=f"stl_use_{r}"):
                        st.color_picker(roles[r], value=cur or "#0070f2", key=f"stl_pick_{r}", label_visibility="collapsed")
        raw = {"base_theme": st.session_state.get("stl_theme", "sap_horizon"),
               "colors": {r: (st.session_state.get(f"stl_pick_{r}") or st.session_state.get(f"stl_c_{r}") or "")
                          if st.session_state.get(f"stl_use_{r}") else "" for r in _style.COLOR_ROLES},
               "font": {"family": st.session_state.get("stl_font", ""),
                        "heading_family": st.session_state.get("stl_head_font", ""), "source": ""},
               "radius": "" if st.session_state.get("stl_radius") == "default" else st.session_state.get("stl_radius"),
               "density": st.session_state.get("stl_density", "compact"),
               "header": "brand" if st.session_state.get("stl_header") else "default",
               "layout_hints": st.session_state["app_style"].get("layout_hints", ""),
               "rationale": st.session_state["app_style"].get("rationale", "")}
        st.session_state["app_style"] = _style.validate(raw)[0]
        stl = st.session_state["app_style"]
        c = stl["colors"]
        sw = "".join(f'<span title="{roles[r]}" style="display:inline-block;width:22px;height:22px;border-radius:6px;'
                     f'background:{c[r]};border:1px solid #d0d4e4;margin:0 4px 4px 0"></span>'
                     for r in _style.COLOR_ROLES if c.get(r))
        st.markdown(f"{ui.chip(stl['base_theme'], 'accent')}{ui.chip(stl['font']['family'] or '72 (SAP)')}"
                    f"{ui.chip(stl['density'])}<div style='margin-top:6px'>{sw}</div>", unsafe_allow_html=True)
        if stl.get("layout_hints"):
            st.caption("Layout cues passed to the design: " + stl["layout_hints"])


def _logo_uri() -> str:
    f = st.session_state.get("style_logo")
    if f is None:
        return ""
    data = f.getvalue()
    if len(data) > 300_000:
        st.warning("Logo is larger than 300 KB — not embedded.")
        return ""
    import base64 as _b64
    mime = "image/svg+xml" if f.name.lower().endswith(".svg") else (f.type or "image/png")
    return f"data:{mime};base64," + _b64.b64encode(data).decode()


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — Review
# ══════════════════════════════════════════════════════════════════════════════
def step_review():
    import blueprint as _bpm
    import ai_designer as _ad
    import ui_preview
    if st.session_state.get("import_mode") == "client_code" and st.session_state.get("import"):
        _review_code()
        return
    design = st.session_state.get("bp_design")
    if st.session_state.get("import_mode") in ("generated", "client_fe") and st.session_state.get("import"):
        imp = st.session_state["import"]
        what = ("Re-opened app generated by this studio — change it below, then Generate and Deploy to update "
                f"{imp['info'].get('app_id')} in place." if st.session_state["import_mode"] == "generated" else
                f"Client Fiori Elements app {imp['info'].get('app_id')} ({imp['kind'].upper()}) — the design below is "
                "reconstructed from its manifest and annotations. Describe the enhancements in 'Refine the design'; "
                "Generate writes them as an overlay, the app's own code stays untouched.")
        st.info(what, icon="🔁")
    if not design:
        st.markdown(ui.empty("🧠", "No design yet", "Describe the application and let the AI design it."),
                    unsafe_allow_html=True)
        nav_buttons(back=1)
        return
    bp = design["blueprint"]
    cov = design.get("coverage") or []
    score = _ad.coverage_score(cov)
    pages = bp.get("pages") or []
    src = {"ai": f"AI · {st.session_state.get('ai_model') or ''}", "rules": "Rule-based", "edited": "Edited"}.get(
        design["source"], design["source"])
    st.markdown(ui.header("Step 3", bp["app"]["title"], (bp.get("reasoning") or "")[:400]), unsafe_allow_html=True)
    covered = sum(c["status"] == "covered" for c in cov)
    st.markdown(ui.metrics([
        ("Requirement coverage", f'<span style="color:{ui.score_color(score)}">{score}%</span>' if score is not None else "—",
         f"{covered}/{len(cov)} features fully covered" if cov else "coverage check off", True),
        ("Floorplan", "Custom app" if bp["floorplan"] == "FreestyleApp" else bp["floorplan"],
         _bpm.FLOORPLANS[bp["floorplan"]][1][:42]),
        ("Pages", len([p for p in pages if p["type"] != "detail"]) or 2,
         ", ".join(sorted({p["type"] for p in pages})) if pages else "list + object"),
        ("Custom logic", _logic_count(bp), "calculations, rules, defaults, scripts"),
        ("Languages", len(bp["app"]["languages"]), ", ".join(bp["app"]["languages"])),
    ]), unsafe_allow_html=True)
    if design.get("steps"):
        st.markdown(ui.timeline(design["steps"]) + ui.chip(src, "accent"), unsafe_allow_html=True)

    t_prev, t_wire, t_cov, t_spec, t_notes, t_json = st.tabs(
        ["▶  Try the app", "🖥  Wireframe", "✅  Coverage" + (f" ({score}%)" if score is not None else ""),
         "📋  Specification", f"⚙️  Design notes ({len(design['notes'])})", "{ }  Blueprint"])
    with t_prev:
        _interactive_preview(bp)
    with t_wire:
        st.caption("Wireframe with the real field labels — click the navigation, rows and cards.")
        components.html(ui_preview.wireframe_html(bp, _model()), height=560, scrolling=False)
        _structure(bp)
    with t_cov:
        if cov:
            st.markdown(ui.coverage_table(cov), unsafe_allow_html=True)
            missing = [c for c in cov if c["status"] != "covered"]
            if missing and has_ai():
                st.caption("Partial or missing items can often be fixed with a refinement below "
                           "(e.g. \"add a confirm dialog to Reorder\").")
        else:
            st.info("Coverage is checked when the requirement analysis and coverage report are on (sidebar) and an "
                    "AI model is available.")
    with t_spec:
        spec = design.get("spec")
        if not spec:
            st.info("The requirement analysis step was not run.")
        else:
            st.markdown(f"**{spec['summary']}**")
            st.markdown(ui.chip(spec["app_kind"] + " app", "accent") + "".join(ui.chip(p) for p in spec["personas"]),
                        unsafe_allow_html=True)
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Entity sets**")
                for e in spec["entity_sets"]:
                    st.markdown(f"- `{e['name']}` — {e['role']}")
                if spec["rules"]:
                    st.markdown("**Business rules**")
                    for r in spec["rules"]:
                        st.markdown(f"- {r}")
            with c2:
                if spec["assumptions"]:
                    st.markdown("**Assumptions**")
                    for a in spec["assumptions"]:
                        st.markdown(f"- {a}")
                if spec["gaps"]:
                    st.markdown("**Not possible with this service**")
                    for g in spec["gaps"]:
                        st.markdown(f"- **{g['text']}** — {g['reason']}")
    with t_notes:
        if design["notes"]:
            st.caption("Corrections made while validating the design against the real service metadata.")
            for n in design["notes"]:
                st.markdown(f"- {n}")
        else:
            st.success("The design validated without corrections.")
    with t_json:
        st.caption("Expert mode: edit the blueprint and apply — it is re-validated against the service.")
        st.text_area("Blueprint JSON", key="bp_json", height=360, label_visibility="collapsed")
        if st.button("Apply JSON changes"):
            try:
                edited = json.loads(st.session_state["bp_json"])
                bp2, notes2 = _bpm.validate(edited, _model(), st.session_state.get("user_prompt", ""))
                design.update(blueprint=bp2, notes=notes2, source="edited")
                st.session_state["_bp_json_next"] = json.dumps(bp2, indent=2)
                st.rerun()
            except ValueError as e:
                st.error(f"Not valid JSON: {e}")

    _refine_box(design)
    st.write("")
    c1, _, c3 = st.columns([1, 2.2, 1.4])
    if c1.button("← Back", use_container_width=True, key="rv_back"):
        goto(1)
    gbusy = job_running("generate")
    if c3.button("⏳ Generating…" if gbusy else "🚀 Generate application", type="primary", use_container_width=True,
                 disabled=bool(gbusy)):
        _generate(design)


def _interactive_preview(bp):
    """Clickable prototype of the design with sample data — clearly NOT the live backend."""
    import interactive_preview as _ip
    sa_state = st.session_state.get("standalone")
    rows, workflow = None, None
    if sa_state:
        import standalone as _sa
        rows = {}
        for path, text in _sa.mock_data(sa_state["domain"]).items():
            try:
                rows[path.rsplit("/", 1)[-1].rsplit(".", 1)[0]] = json.loads(text)
            except ValueError:
                pass
        wf = sa_state["domain"].get("workflow")
        if wf:
            workflow = {t["action"]: {"field": wf["status_field"], "from": list(t["from"]), "to": t["to"]}
                        for t in wf["transitions"]}
    st.markdown(ui.chip("MOCK DATA", "warn") + (
        " <span style='font-size:.86rem'>Sample records of the generated backend's seed data, kept in your browser. "
        "Nothing is saved. The deployed app uses its own CAP service and database.</span>" if sa_state else
        " <span style='font-size:.86rem'>Generated sample records — nothing is read from or sent to "
        f"<code>{st.session_state.get('md_service_url_val', '')}</code>. The deployed app reads the live SAP service.</span>"),
                unsafe_allow_html=True)
    try:
        components.html(_ip.preview_html(bp, _model(), sample_rows=rows, workflow=workflow, height=620),
                        height=640, scrolling=False)
    except Exception as e:  # noqa: BLE001
        st.warning(f"The interactive preview could not render this design ({e}). Use the Wireframe tab.")


def _logic_count(bp) -> int:
    def cnt(lg, actions):
        lg = lg or {}
        return (len(lg.get("computed") or []) + (1 if lg.get("row_state") else 0) + len(lg.get("validations") or [])
                + len(lg.get("defaults") or []) + len(lg.get("derived") or [])
                + sum(a["kind"] == "script" for a in actions))
    if bp.get("pages"):
        return sum(cnt(p["sub"].get("logic"), p["sub"]["actions"]) for p in bp["pages"] if p["type"] == "list")
    return cnt(bp.get("logic"), bp.get("actions", []))


def _structure(bp):
    with st.expander("Page structure in detail"):
        import ai_designer as _ad
        st.code(_ad.summarize_blueprint(bp).split("\nBUILT INTO")[0], language="text")


def _refine_box(design):
    import app_builder as _ab
    import ai_designer as _ad
    with st.container(border=True):
        st.markdown("**💬 Refine the design**")
        for h in st.session_state.get("refine_history", []):
            st.markdown(f"{ui.chip('you', 'accent')} {h['instruction']}<br>"
                        f"<span style='color:var(--ev-muted);font-size:.84rem'>→ {h['result']}</span>",
                        unsafe_allow_html=True)
        c1, c2 = st.columns([5, 1])
        rkey = f"refine_text_{len(st.session_state.get('refine_history', []))}"
        instr = c1.text_input("Change", key=rkey, label_visibility="collapsed",
                              placeholder="e.g. add a calendar of delivery dates · make the board draggable · "
                                          "add German · show the margin as a column",
                              disabled=not has_ai())
        go_btn = c2.button("Apply", use_container_width=True, disabled=not (has_ai() and (instr or "").strip()))
        if not has_ai():
            st.caption("Refinement needs an AI model.")
        if go_btn:
            with st.spinner("Applying the change…"):
                try:
                    r = _ab.refine(design["blueprint"], instr, st.session_state.get("user_prompt", ""),
                                   st.session_state["md_edmx"], ai_call, design.get("spec"),
                                   service_url=st.session_state.get("md_service_url_val", ""),
                                   extra_services=_extra_services())
                    design.update(blueprint=r["blueprint"], notes=r["notes"], source="ai")
                    result = "design updated"
                    if design.get("spec") and st.session_state.get("opt_coverage", True):
                        design["coverage"] = _ad.assess_coverage(design["spec"], r["blueprint"], ai_call)
                        result += f" · coverage {_ad.coverage_score(design['coverage'])}%"
                    st.session_state["refine_history"].append({"instruction": instr, "result": result})
                    st.session_state["_bp_json_next"] = json.dumps(r["blueprint"], indent=2)
                    st.session_state.generated_files = {}
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"Could not apply the change: {e}")


def _model():
    key = (id(st.session_state.get("md_edmx")), tuple(s["service_url"] for s in st.session_state.get("md_extra", [])))
    if st.session_state.get("_model_for") != key:
        st.session_state["_model_cache"] = _app_model()
        st.session_state["_model_for"] = key
    return st.session_state["_model_cache"]


def _generate(design):
    import app_builder as _ab
    if st.session_state.get("import_mode") == "client_fe" and st.session_state.get("import"):
        _generate_client_fe(design)
        return
    st.session_state["files_raw"] = False
    sa_state = st.session_state.get("standalone")
    svc = (st.session_state.get("md_service_url_val") or "").strip()
    if not svc or "YOUR_SERVICE" in svc:
        st.error("No OData service path set for the app (e.g. /sap/opu/odata/sap/API_SALES_ORDER_SRV/). "
                 "Set it in step 1 — otherwise every data request 404s.")
        return
    use_existing = bool(st.session_state.get("md_use_existing_dest"))
    kwargs = dict(
        edmx=st.session_state["md_edmx"], requirement=st.session_state.get("user_prompt", ""),
        app_id=st.session_state.get("app_id_v", "com.mycompany.app"), service_url=svc,
        ai_fn=ai_fn(), blueprint=design["blueprint"],
        auth_type=st.session_state.get("md_auth", AUTH_NONE) if not use_existing else AUTH_NONE,
        use_existing_destination=use_existing,
        destination_name=(st.session_state.get("md_dest_name_val") or "").strip(),
        on_premise=bool(st.session_state.get("md_on_premise")),
        theme=st.session_state.get("theme_v", "sap_horizon"),
        style=st.session_state.get("app_style"), logo_data_uri=_logo_uri(),
        design_spec=design.get("spec"), coverage=design.get("coverage"),
        extra_services=_extra_services(),
        **({"standalone": sa_state["domain"], "standalone_db": sa_state.get("db", "sqlite")} if sa_state else {}))
    start_job("generate", "Generating the application", _w_generate, kwargs, target_step=3)


def _w_generate(say, kwargs):
    import app_builder as _ab
    say("Rendering views, controllers, custom logic and configuration…")
    langs = kwargs["blueprint"]["app"]["languages"]
    if kwargs.get("ai_fn") and len(langs) > 1:
        say(f"Translating texts into {', '.join(langs[1:])}…")
    if kwargs.get("standalone"):
        say("Generating the CAP backend (data model, service, workflow, sample data)…")
    res = _ab.build(**kwargs)
    say(f"Generated {len(res.files)} files — quality gate: {res.quality.get('errors', 0)} error(s).")
    return res


def _apply_generate(job):
    res = job.result
    sa_state = st.session_state.get("standalone")
    design = st.session_state.get("bp_design") or {}
    st.session_state["files_raw"] = False
    st.session_state.generated_files = res.files
    design["blueprint"] = res.blueprint
    st.session_state["build_result"] = {"quality": res.quality, "fixes": res.fixes, "notes": res.notes,
                                        "floorplan": res.blueprint["floorplan"],
                                        "odata": res.model["odata_version"], "title": res.blueprint["app"]["title"]}
    st.session_state.pop("validation_report", None)
    if sa_state:
        st.session_state["deploy_meta"] = {"app_id": res.spec.app_id, "services": [], "standalone": True,
                                           "db": sa_state.get("db", "sqlite")}
    else:
        st.session_state["deploy_meta"] = {
            "app_id": res.spec.app_id,
            "services": [{"id": s.id, "url": s.url, "auth_type": s.auth_type, "dest_name": s.dest_name(),
                          "needs_auth": s.needs_auth(), "is_real": s.is_real(),
                          "use_existing_destination": s.use_existing_destination, "on_premise": s.on_premise}
                         for s in res.spec.service_list()]}
    try:                     # every generation is a restorable version of the project
        v = save_version("Generated")
        STORE.audit(USER, v["project_id"], "app.generate",
                    {"version": v["number"], "files": len(res.files), "floorplan": res.blueprint["floorplan"],
                     "quality_errors": res.quality.get("errors", 0)})
    except PermissionError as e:
        st.session_state["_job_error"] = f"Generated, but not saved to the workspace: {e}"


def _generate_client_fe(design):
    """Client Fiori Elements app: overlay annotations + merged manifest, own code untouched."""
    import app_builder as _ab
    imp = st.session_state["import"]
    info = dict(imp["info"])
    with st.status("Enhancing the existing app…", expanded=True) as status:
        status.write("Writing the overlay annotations and merging manifest settings…")
        try:
            res = _ab.enhance_fe(original_text=imp["text"], info=info, edmx=st.session_state["md_edmx"],
                                 blueprint=design["blueprint"], requirement=st.session_state.get("user_prompt", ""))
        except Exception as e:  # noqa: BLE001
            status.update(label=f"Enhancement failed: {e}", state="error")
            return
        status.update(label="Enhancement ready", state="complete", expanded=False)
    changed = sorted(k for k, v in res["files"].items() if imp["text"].get(k) != v)
    st.session_state.generated_files = res["files"]
    st.session_state["files_raw"] = True
    st.session_state["raw_binary"] = imp["binary"]
    st.session_state["raw_changed"] = changed
    st.session_state["build_result"] = {"quality": {"errors": len(res["errors"]), "warnings": 0,
                                                    "findings": [{"severity": "error", "message": e, "file": ""}
                                                                 for e in res["errors"]]},
                                        "fixes": [], "notes": res["notes"], "floorplan": res["blueprint"]["floorplan"],
                                        "odata": "4.0" if info["kind"] == "fe_v4" else "2.0",
                                        "title": res["blueprint"]["app"]["title"] + " (enhanced)"}
    st.session_state["deploy_meta"] = {"app_id": info.get("app_id"), "services": [], "raw": True}
    goto(3)


def _review_code():
    """Client freestyle app: describe a change -> AI edits files -> review the diff -> apply."""
    import app_import
    imp = st.session_state["import"]
    st.markdown(ui.header("Step 3", f"Change {imp['info'].get('app_id') or 'the app'}",
                          "Freestyle client app: describe a change, review the AI's file changes as a diff, then "
                          "apply. Only the files shown change; existing texts are never rewritten."),
                unsafe_allow_html=True)
    for h in st.session_state.get("code_changes", []):
        st.markdown(f"{ui.chip('applied', 'ok')} {h['instruction']} — <span style='color:var(--ev-muted)'>"
                    f"{', '.join(h['files'])}</span>", unsafe_allow_html=True)
    instr = st.text_area("Change request", key=f"code_req_{len(st.session_state.get('code_changes', []))}",
                         height=110, placeholder="e.g. add a 'Copy order number' button to the order list toolbar; "
                                                 "show the net value with 2 decimals on the detail page …",
                         disabled=not has_ai())
    if st.button("✨ Propose change", type="primary", disabled=not (has_ai() and (instr or "").strip())):
        import blueprint as _bpm
        with st.spinner("The AI is changing the code…"):
            try:
                ctx = _bpm.context_for_ai(_model()) if st.session_state.get("md_model") else ""
                st.session_state["code_proposal"] = dict(app_import.ai_code_change(imp["text"], instr, ai_call, ctx),
                                                         instruction=instr)
            except Exception as e:  # noqa: BLE001
                st.error(f"The AI change failed: {e}")
    prop = st.session_state.get("code_proposal")
    if prop:
        errs = app_import.check_files(prop["files"])
        st.markdown(f"**Proposed:** {prop['summary']}")
        st.markdown("".join(ui.chip(p, "accent") for p in prop["files"]) or "_no file changes_", unsafe_allow_html=True)
        if errs:
            st.error("Syntax problems — not applicable as is:\n" + "\n".join(f"- {e}" for e in errs))
        st.code(prop["diff"] or "(no differences)", language="diff", height=420)
        c1, c2 = st.columns(2)
        if c1.button("✅ Apply change", type="primary", disabled=bool(errs) or not prop["files"], use_container_width=True):
            imp["text"].update(prop["files"])
            st.session_state.setdefault("code_changes", []).append({"instruction": prop["instruction"],
                                                                    "files": list(prop["files"])})
            st.session_state.pop("code_proposal", None)
            st.session_state.generated_files = dict(imp["text"])
            st.session_state.update(files_raw=True, raw_binary=imp["binary"],
                                    raw_changed=sorted({f for h in st.session_state["code_changes"] for f in h["files"]}),
                                    deploy_meta={"app_id": imp["info"].get("app_id"), "services": [], "raw": True},
                                    build_result={"quality": {"errors": 0, "warnings": 0, "findings": []}, "fixes": [],
                                                  "notes": [], "floorplan": "freestyle (client app)",
                                                  "odata": imp["info"].get("odata_version", "2.0"),
                                                  "title": (imp["info"].get("app_id") or "App") + " (changed)"})
            st.rerun()
        if c2.button("Discard", use_container_width=True):
            st.session_state.pop("code_proposal", None)
            st.rerun()
    nav_buttons(back=0, nxt=3, next_label="Review files", next_disabled=not st.session_state.get("code_changes"))


# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — Build
# ══════════════════════════════════════════════════════════════════════════════
def step_build():
    files = st.session_state.generated_files
    if not files:
        st.markdown(ui.empty("📦", "Nothing generated yet", "Review the design and choose Generate application."),
                    unsafe_allow_html=True)
        nav_buttons(back=2)
        return
    br = st.session_state.get("build_result") or {}
    q = br.get("quality") or {"errors": 0, "warnings": 0, "findings": []}
    if st.session_state.get("files_raw"):        # imported project: the same check the deploy gate runs
        import quality_validator as _qv
        q = _qv.validate_project(_qv_view(files))
    st.markdown(ui.header("Step 4", br.get("title") or "Generated project",
                          "A complete SAPUI5 project: app, i18n, mock data preview, approuter, XSUAA and MTA "
                          "deployment descriptors."), unsafe_allow_html=True)
    loc = sum(c.count("\n") for c in files.values())
    qual = ("<span style='color:var(--ev-ok)'>Passed</span>" if not q["errors"]
            else f"<span style='color:var(--ev-bad)'>{q['errors']} error(s)</span>")
    st.markdown(ui.metrics([("Quality gate", qual, f"{q['warnings']} warning(s) · {len(br.get('fixes') or [])} auto-fix(es)", True),
                            ("Files", len(files), f"{loc:,} lines"),
                            ("Floorplan", br.get("floorplan", "—"), f"OData V{(br.get('odata') or '?')[0]}")]),
                unsafe_allow_html=True)
    c1, c2 = st.columns([1, 1])
    with c1:
        st.download_button("📦 Download project (.zip)", data=_project_zip(files),
                           file_name=f"{(st.session_state.get('deploy_meta') or {}).get('app_id', 'fiori_app')}.zip",
                           mime="application/zip", use_container_width=True, type="primary")
    with c2:
        with st.popover("▶ Run it locally", use_container_width=True):
            st.markdown("Unzip, then:")
            st.code("npm install\nnpm run start-mock   # generated mock data, no backend\nnpm start            # real backend via ui5.yaml proxy",
                    language="bash")
    if q["findings"] or br.get("fixes") or br.get("notes"):
        with st.expander(f"Quality report & notes", expanded=bool(q["errors"])):
            for f in br.get("fixes") or []:
                st.markdown(f"- 🔧 {f}")
            icons = {"error": "❌", "warning": "⚠️", "info": "ℹ️", "fixed": "🔧"}
            for f in q["findings"]:
                if f.get("check") != "auto-fix":
                    st.markdown(f"- {icons.get(f['severity'], '-')} {f['message']}" + (f" `{f['file']}`" if f.get("file") else ""))
            for n in br.get("notes") or []:
                st.markdown(f"- ℹ️ {n}")

    _validation_panel(files)
    st.markdown("#### Files")
    if st.session_state.get("files_raw") and st.session_state.get("raw_changed"):
        import app_import
        imp = st.session_state.get("import") or {}
        st.markdown("**Changed files:** " + "".join(ui.chip(p, "accent") for p in st.session_state["raw_changed"]),
                    unsafe_allow_html=True)
        with st.expander("Diff against the original project"):
            orig = {}
            if imp:
                # the import keeps the latest text; rebuild the original from the ZIP-time snapshot when present
                orig = imp.get("original") or {}
            st.code(app_import.diff(orig, {p: files[p] for p in st.session_state["raw_changed"] if p in files})
                    or "(no differences)", language="diff", height=360)
    groups = {}
    for name in sorted(files):
        top = name.split("/")[0] if "/" in name else "(root)"
        groups.setdefault(top, []).append(name)
    tree, code = st.columns([1, 2.6], gap="medium")
    with tree:
        folder = st.selectbox("Folder", list(groups), key="fb_folder")
        sel = st.radio("File", groups[folder], key=f"fb_file_{folder}", label_visibility="collapsed",
                       format_func=lambda n: n.split("/")[-1] if folder != "(root)" else n)
    with code:
        lang = {".json": "json", ".js": "javascript", ".xml": "xml", ".yaml": "yaml", ".html": "html",
                ".properties": "properties", ".md": "markdown", ".css": "css"}.get(os.path.splitext(sel)[1], "text")
        st.markdown(f"`{sel}`")
        if st.toggle("Edit", key=f"edit_toggle_{sel}"):
            edited = st.text_area("Edit file", value=files[sel], height=460, key=f"edit_{sel}", label_visibility="collapsed")
            if edited != files[sel]:
                st.session_state.generated_files[sel] = edited
                st.success("Saved in this session — re-download the ZIP or deploy.")
        else:
            st.code(files[sel], language=lang, line_numbers=True, height=460)
    nav_buttons(back=2, nxt=4, next_label="Deploy to BTP")


_VSTAT = {"passed": ("Passed", "ok"), "failed": ("Failed", "bad"), "not_tested": ("Not tested", ""),
          "warning": ("Warning", "warn")}


def _apply_validate(job):
    rep = job.result
    st.session_state["validation_report"] = rep
    n = rep["summary"]
    st.session_state["_job_done"] = (f"Validation: {n['passed']} passed · {n['failed']} failed · "
                                     f"{n['not_tested']} not tested" + (" — deployment blocked" if rep["blocking"] else ""))
    if st.session_state.get("version_id") and st.session_state.get("version_sha") == job.context.get("files_sha"):
        STORE.set_validation(st.session_state["version_id"], rep, USER)
    else:
        STORE.audit(USER, st.session_state.get("project_id"), "validation.run",
                    {"level": job.context.get("level"), "summary": n, "blocking": rep["blocking"]})


def _validation_panel(files):
    """Real build/runtime validation in a sandbox (node, ui5 build, CAP compile + server, headless browser)."""
    import build_validation as _bv
    if st.session_state.get("files_raw"):
        st.caption("Build validation runs on projects generated by the studio; client projects use the quality "
                   "gate above.")
        return
    rep = st.session_state.get("validation_report")
    with st.container(border=True):
        c1, c2, c3 = st.columns([2.2, 1.3, 1])
        c1.markdown("**🧪 Build & runtime validation**")
        c1.caption("Builds and runs the project in a sandbox: JavaScript syntax, UI5 build, MTA/approuter wiring, "
                   "CAP compile + backend smoke test, and (deep) a headless-browser start of the app.")
        level = c2.segmented_control("Level", ["standard", "deep"], default="standard", key="val_level",
                                     format_func={"standard": "Standard", "deep": "Deep (browser)"}.get) or "standard"
        vbusy = job_running("validate")
        if c3.button("⏳ Validating…" if vbusy else "Run validation", type="primary", use_container_width=True,
                     disabled=bool(vbusy)):
            design = st.session_state.get("bp_design") or {}
            start_job("validate", f"Validating the project ({level})",
                      lambda say, f, m, cov, lv: _bv.run(f, model=m, coverage=cov, level=lv, progress=say),
                      dict(files), _model() if st.session_state.get("md_edmx") else None, design.get("coverage"), level,
                      extra={"files_sha": wsp.files_sha(files), "level": level})
        if not rep:
            env = _bv.environment()
            if not env.get("node"):
                st.caption("⚠️ Node.js is not available on this host — build, backend and browser checks will be "
                           "reported as not tested. Run the studio locally / in BAS for the full validation.")
            return
        n = rep["summary"]
        st.markdown(ui.metrics([
            ("Result", "<span style='color:var(--ev-bad)'>Blocked</span>" if rep["blocking"] else
             "<span style='color:var(--ev-ok)'>Deployable</span>", f"level {rep.get('level', '')} · {rep.get('seconds', 0):.0f}s", True),
            ("Passed", n["passed"], "checks"), ("Failed", n["failed"], "checks"),
            ("Not tested", n["not_tested"], "tool unavailable or skipped")]), unsafe_allow_html=True)
        if rep["blocking"]:
            st.error("Deployment is blocked by: " + "; ".join(rep["blocking"]))
        t1, t2 = st.tabs([f"Checks ({len(rep['checks'])})", f"Requirements ({len(rep.get('requirements') or [])})"])
        with t1:
            for c in rep["checks"]:
                lab, kind = _VSTAT.get(c["status"], (c["status"], ""))
                st.markdown(f"{ui.chip(lab, kind)} **{c['name']}** <span style='color:var(--ev-muted);font-size:.8rem'>"
                            f"{c['category']}{' · critical' if c.get('critical') else ''}</span>",
                            unsafe_allow_html=True)
                if c.get("detail") and c["status"] != "passed":
                    st.caption(c["detail"][:600])
        with t2:
            if not rep.get("requirements"):
                st.caption("No requirement coverage — turn on the analysis + coverage report (sidebar) when designing.")
            for r in rep.get("requirements") or []:
                lab, kind = _VSTAT.get(r["status"], (r["status"], ""))
                st.markdown(f"{ui.chip(lab, kind)} {r['feature']}", unsafe_allow_html=True)
                if r.get("detail"):
                    st.caption(r["detail"][:300])
        st.download_button("⬇ Validation report (.md)", data=_bv.format_markdown(rep), file_name="validation-report.md",
                           mime="text/markdown")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 — Deploy
# ══════════════════════════════════════════════════════════════════════════════
def step_deploy():
    files = st.session_state.generated_files
    if not files:
        st.markdown(ui.empty("🚀", "Nothing to deploy yet", "Generate an application first."), unsafe_allow_html=True)
        nav_buttons(back=2)
        return
    if "mta.yaml" not in files:
        if st.session_state.get("files_raw"):
            st.info("This client project has no mta.yaml, so the studio cannot deploy it as is. Download the "
                    "changed project in step 4 and deploy it the way the client normally does (BAS, CI/CD, "
                    "HTML5 repository).")
        else:
            st.warning("This project has no deployment files. Regenerate it from the design.")
        return
    if st.session_state.get("files_raw"):
        st.info("Deploying the client's project with its own mta.yaml — its destinations and services must "
                "already exist in the space.", icon="ℹ️")
    meta = st.session_state.get("deploy_meta", {})
    st.markdown(ui.header("Step 5", "Approve & deploy to SAP BTP",
                          ("Builds the MTA and deploys the CAP backend (" + ("SAP HANA Cloud" if meta.get("db") == "hana"
                           else "in-memory SQLite") + "), the approuter and XSUAA login." if meta.get("standalone") else
                           "Builds the MTA and deploys the approuter with XSUAA login, the destination and (for "
                           "on-premise systems) the connectivity service.")
                          + " Each environment has its own rules: production needs an approved, validated version."),
                unsafe_allow_html=True)
    gate_ok, env_key, env_cfg = _release_panel()
    mta_text = files.get("mta.yaml", "")
    needs_dest = any(s.get("is_real") for s in meta.get("services", []))
    if needs_dest and not ("resources:" in mta_text and "requires:" in mta_text):
        st.error("This project's mta.yaml has no service bindings — it was generated by an older version. "
                 "Regenerate it from the design.")
        return

    left, right = st.columns([1.15, 1], gap="large")
    with left:
        with st.container(border=True):
            st.markdown("**Cloud Foundry**")
            signed_in = _cf_signin("dep")
    t = st.session_state.get("cf_target") or dict(CF_DEFAULTS)
    cf_api, cf_org, cf_space = t["api"], t["org"], t["space"]
    if env_cfg.get("configured"):        # the environment decides where it goes
        cf_api, cf_org, cf_space = env_cfg["api"], env_cfg["org"], env_cfg["space"]
        with left:
            st.caption(f"Target of {env_cfg['label']}: org `{cf_org}` · space `{cf_space}`")
    use_sso, cf_passcode, cf_user, cf_pass = False, "", "", ""
    creds_ok = signed_in

    dest_services = [m for m in meta.get("services", []) if m.get("is_real")]
    new_dest = [m for m in dest_services if not m.get("use_existing_destination")]
    existing = [m for m in dest_services if m.get("use_existing_destination")]
    dest_creds = {}
    with right:
        with st.container(border=True):
            st.markdown("**Backend connection**")
            for m in existing:
                st.markdown(ui.chip(f"🔗 {m['dest_name']}", "accent")
                            + (ui.chip("On-premise · Connectivity bound", "info") if m.get("on_premise") else ""),
                            unsafe_allow_html=True)
                st.caption("Existing destination — make sure it exists in the BTP cockpit (Connectivity → Destinations).")
                dest_creds[m["id"]] = {"url": m.get("url", "")}
            for m in new_dest:
                st.markdown(f"New destination **{m['dest_name']}** · {m['auth_type']}")
                url = st.text_input("Backend base URL", value=_host_root(m.get("url", "")), key=f"dc_url_{m['id']}")
                c = {"url": url}
                if m["auth_type"] == AUTH_BASIC:
                    c["user"] = st.text_input("User", key=f"dc_user_{m['id']}")
                    c["password"] = st.text_input("Password", type="password", key=f"dc_pw_{m['id']}")
                elif m["auth_type"] == AUTH_OAUTH_CC:
                    c["token_url"] = st.text_input("Token service URL", key=f"dc_turl_{m['id']}")
                    c["client_id"] = st.text_input("Client ID", key=f"dc_cid_{m['id']}")
                    c["client_secret"] = st.text_input("Client secret", type="password", key=f"dc_csec_{m['id']}")
                dest_creds[m["id"]] = c
            if not dest_services:
                st.caption("No real backend service configured.")
        with st.expander("mta.yaml"):
            st.code(mta_text, language="yaml")

    protected = bool(dest_services)
    missing = []
    for m in new_dest:
        req = ["url"] + (["user", "password"] if m["auth_type"] == AUTH_BASIC else
                         ["token_url", "client_id", "client_secret"] if m["auth_type"] == AUTH_OAUTH_CC else [])
        missing += [f"{m['dest_name']} → {f}" for f in req if not (dest_creds.get(m["id"], {}).get(f) or "").strip()]
    if protected and missing:
        st.warning("Fill in every destination field first:\n" + "\n".join(f"- {f}" for f in missing))
    method = "MTA Build + Deploy"
    if not protected and not st.session_state.get("files_raw") and not meta.get("standalone"):
        method = st.segmented_control("Method", ["MTA Build + Deploy", "cf push (static)"], default="MTA Build + Deploy",
                                      key="dep_method") or "MTA Build + Deploy"

    dbusy = job_running("deploy")
    if st.button("⏳ Deploying…" if dbusy else f"🚀 Deploy to {env_cfg.get('label', env_key)}", type="primary",
                 use_container_width=True, disabled=bool(dbusy) or not (gate_ok and cf_org and cf_space and creds_ok)
                 or bool(protected and missing)):
        _deploy(files, meta, cf_api, cf_org, cf_space, dest_creds, protected, method, env_key)

    if st.session_state.get("deploy_log_text"):
        with st.expander("Deployment log", expanded=not st.session_state.deployed_url):
            st.code(st.session_state["deploy_log_text"][-12000:], language="text")
    if st.session_state.deployed_url:
        st.success(f"Live at [{st.session_state.deployed_url}]({st.session_state.deployed_url})")
    _logs_box(cf_api, cf_org, cf_space)
    nav_buttons(back=3)


def _release_panel():
    """Environment + saved version + approval + validation gate. Returns (may deploy, env key, env config)."""
    envs = ac.environments()
    keys = list(envs)
    env_key = st.segmented_control(
        "Environment", keys, default=keys[0], key="dep_env",
        format_func=lambda k: envs[k]["label"] + ("" if envs[k].get("configured") else " · not configured")) or keys[0]
    cfg = envs[env_key]
    proj = current_project()
    with st.container(border=True):
        c1, c2 = st.columns([1.4, 1])
        with c1:
            st.markdown(f"**Release to {cfg['label']}**")
            rules = (["approved version"] if cfg.get("requires_approval") else []) + \
                    (["validation run without blocking failures"] if cfg.get("requires_validation") else [])
            st.caption("Requires: " + (", ".join(rules) if rules else "the deploy permission only")
                       + ("" if cfg.get("configured") else " · ⚠️ no CF space configured for this environment "
                          "(STUDIO_ENVIRONMENTS)"))
        with c2:
            if files_saved():
                v = STORE.get_version(st.session_state["version_id"], with_files=False)
                st.markdown(ui.chip(f"Version {v['number']}", "accent") + ui.chip(proj["name"] if proj else ""),
                            unsafe_allow_html=True)
            elif st.button("💾 Save as new version", use_container_width=True,
                           disabled=not ac.can(USER, "version.save", proj, STORE)):
                save_version("Release candidate")
                st.rerun()
            else:
                st.caption("Unsaved changes — save a version to release it.")
        vid = st.session_state.get("version_id") if files_saved() else None
        if cfg.get("requires_approval") and vid and proj:
            _approval_box(proj, vid, env_key, cfg)
        ok, reasons = ac.check_deploy(USER, env_key, proj["id"] if proj else "", vid, STORE,
                                      st.session_state.get("validation_report"))
        if ok:
            st.markdown(ui.chip("Release gate passed", "ok"), unsafe_allow_html=True)
        else:
            st.markdown(ui.chip("Release blocked", "bad") + "<br>" + "<br>".join(f"• {r}" for r in reasons),
                        unsafe_allow_html=True)
    return ok, env_key, cfg


def _approval_box(proj, vid, env_key, cfg):
    appr = STORE.approval_for(proj["id"], vid, env_key)
    status = (appr or {}).get("status")
    if status == "approved":
        st.markdown(ui.chip("Approved", "ok") + f" by {appr.get('decided_by')} — {appr.get('comment') or ''}",
                    unsafe_allow_html=True)
        return
    if status == "pending":
        st.markdown(ui.chip("Awaiting approval", "warn") + f" requested by {appr.get('requester')}"
                    + (f" — {appr.get('note')}" if appr.get("note") else ""), unsafe_allow_html=True)
        if ac.can(USER, "approval.decide", proj, STORE) and appr.get("requester") != USER.id:
            cm = st.text_input("Decision comment", key=f"appr_cm_{appr['id']}")
            a1, a2 = st.columns(2)
            for col, approve, lab in ((a1, True, "✅ Approve release"), (a2, False, "❌ Reject")):
                if col.button(lab, key=f"appr_{approve}_{appr['id']}", use_container_width=True):
                    try:
                        STORE.decide_approval(appr["id"], USER, approve, cm)
                        st.rerun()
                    except (PermissionError, ValueError) as e:
                        st.error(str(e))
        else:
            st.caption("A reviewer (not the requester or the version's author) decides in Projects → Approvals.")
        return
    if status == "rejected":
        st.markdown(ui.chip("Rejected", "bad") + f" by {appr.get('decided_by')}: {appr.get('comment') or ''}",
                    unsafe_allow_html=True)
    if ac.can(USER, "approval.request", proj, STORE):
        note = st.text_input("Note for the reviewer", key=f"appr_note_{vid}_{env_key}",
                             placeholder="What changed, what was tested…")
        if st.button(f"📨 Request approval for {cfg['label']}", key=f"appr_req_{vid}_{env_key}"):
            STORE.request_approval(proj["id"], vid, env_key, USER, note)
            st.rerun()


def _qv_view(files):
    """The quality checks expect the studio's flat layout (manifest.json, view/, controller/ at
    the top). Imported client projects keep their own layout (webapp/...): check that folder."""
    if not st.session_state.get("files_raw"):
        return files
    import app_import
    wp = app_import.webapp_prefix(files)
    if not wp:
        return files
    return {p[len(wp):]: c for p, c in files.items() if p.startswith(wp)}


def _project_zip(files) -> bytes:
    if st.session_state.get("files_raw"):
        import app_import
        return app_import.to_zip(files, st.session_state.get("raw_binary") or {})
    return create_zip_archive(files, app_name="fiori_app")


def _host_root(u: str) -> str:
    m = re.match(r"^([a-zA-Z][\w+.\-]*://[^/]+)", (u or "").strip())
    return m.group(1) if m else ""


def _deploy(files, meta, cf_api, cf_org, cf_space, dest_creds, protected, method, env_key):
    """Checks that need the page (CLI, quality gate), then the deployment as a background job."""
    import quality_validator as _qv
    deployer = CFDeployer(api_url=cf_api, org=cf_org, space=cf_space, cf_home=_cf_home())
    if not deployer.check_cf_cli():
        st.warning("The Cloud Foundry CLI is not available here. Download the project and deploy from a machine "
                   "where you are logged in with `cf login`.")
        st.code("unzip <project>.zip -d myapp && cd myapp\n"
                f"cf login -a {cf_api} --sso\ncf target -o \"{cf_org}\" -s {cf_space}\n"
                "mbt build --mtar app.mtar\ncf deploy mta_archives/app.mtar -e deploy.mtaext", language="bash")
        return
    gate = _qv.validate_project(_qv_view(files))
    if gate["errors"]:
        st.error(f"Deployment blocked: {gate['errors']} quality error(s) would produce a broken app.")
        for f in gate["findings"]:
            if f["severity"] == "error":
                st.markdown(f"- ❌ {f['message']}" + (f" `{f['file']}`" if f["file"] else ""))
        return
    st.session_state.pop("deploy_log_text", None)
    start_job("deploy", f"Deploying {meta.get('app_id') or 'the app'} to {env_key}", _w_deploy, dict(files),
              dict(st.session_state.get("raw_binary") or {}) if st.session_state.get("files_raw") else None,
              meta, cf_api, cf_org, cf_space, _cf_home(), dest_creds, protected, method,
              extra={"env": env_key, "target": {"api": cf_api, "org": cf_org, "space": cf_space,
                                                 "app": meta.get("app_id")}})


def _w_deploy(say, files, raw_binary, meta, cf_api, cf_org, cf_space, cf_home, dest_creds, protected, method):
    """Background deployment. Returns {"status": succeeded|failed|skipped, "url", "log", "app_id"}."""
    deployer = CFDeployer(api_url=cf_api, org=cf_org, space=cf_space, cf_home=cf_home)
    app_id = meta.get("app_id") or "fiori-app"
    logs = []

    def log(line):
        logs.append(str(line))
        say(line)

    def done(status, url=""):
        return {"status": status, "url": url, "log": "\n".join(logs), "app_id": app_id}

    with tempfile.TemporaryDirectory() as tmp:
        if raw_binary is not None:                 # imported project: paths are already final
            for p, c in files.items():
                fp = os.path.join(tmp, p)
                os.makedirs(os.path.dirname(fp) or tmp, exist_ok=True)
                with open(fp, "w", encoding="utf-8") as fh:
                    fh.write(c)
            for p, c in raw_binary.items():
                fp = os.path.join(tmp, p)
                os.makedirs(os.path.dirname(fp) or tmp, exist_ok=True)
                with open(fp, "wb") as fh:
                    fh.write(c)
        else:
            write_project_to_disk(files, tmp)
        log("📁 Project written.")
        mta_disk = open(os.path.join(tmp, "mta.yaml")).read() if os.path.exists(os.path.join(tmp, "mta.yaml")) else ""
        if protected and ("resources:" not in mta_disk or "requires:" not in mta_disk):
            log("❌ mta.yaml on disk has no resources/requires — regenerate the app.")
            return done("failed")
        for line in deployer.target():
            log(line)
        if not deployer.is_logged_in():
            log("❌ The Cloud Foundry session has expired — sign in again in step 5.")
            return done("skipped")
        log(f"✅ Signed in as {deployer.whoami()}")
        if method.startswith("MTA"):
            if (protected or meta.get("standalone")) and not deployer.check_multiapps_plugin():
                log("❌ CF multiapps plugin missing (cf install-plugin multiapps).")
                return done("failed")
            ext = None
            if protected:
                svc = [ODataService(id=m["id"], url=dest_creds.get(m["id"], {}).get("url", "") or m.get("url", ""),
                                    auth_type=m["auth_type"], is_primary=(i == 0),
                                    use_existing_destination=bool(m.get("use_existing_destination")),
                                    destination=m.get("dest_name", ""), on_premise=bool(m.get("on_premise")))
                       for i, m in enumerate(meta.get("services", []))]
                ext = deployer.write_mta_extension(tmp, mta_extension(app_id, svc, dest_creds))
                log("🧩 deploy.mtaext written (destinations).")
            for line in deployer.build_mta(tmp):
                log(line)
            for line in deployer.deploy_mtar(tmp, ext_file=ext):
                log(line)
        else:
            for line in deployer.cf_push(tmp, app_id):
                log(line)
        url = deployer.get_app_url(app_id)
        if url:
            log(f"🌐 {url}")
        return done("succeeded" if url else "failed", url or "")


def _apply_deploy(job):
    r = job.result
    st.session_state["deploy_log_text"] = r["log"]
    if r["status"] == "skipped":
        st.session_state["_job_error"] = "Deployment skipped: " + (r["log"].splitlines() or [""])[-1]
        return
    st.session_state["deployed_app"] = r["app_id"]
    if r["url"]:
        st.session_state.deployed_url = r["url"]
        st.session_state["_job_done"] = f"Deployed — live at {r['url']}"
    else:
        st.session_state["_job_error"] = "Deployment failed — see the deployment log in step 5."
    if st.session_state.get("project_id"):
        STORE.record_deployment(st.session_state["project_id"], job.context["state"].get("version_id"),
                                job.context.get("env", "dev"), job.context.get("target") or {}, USER, r["status"],
                                app_url=r["url"] or "", log_tail=r["log"])


def _logs_box(cf_api, cf_org, cf_space):
    default = st.session_state.get("deployed_app") or st.session_state.get("deploy_meta", {}).get("app_id", "")
    if not default:
        return
    with st.expander("Application logs"):
        c1, c2 = st.columns([3, 1])
        name = c1.text_input("CF app name", value=default, key="log_app_name", label_visibility="collapsed")
        if c2.button("Fetch logs", use_container_width=True):
            ok, text = CFDeployer(api_url=cf_api, org=cf_org, space=cf_space,
                                  cf_home=_cf_home()).get_recent_logs(name.strip())
            if ok:
                st.code(text or "(no recent log lines — open the app and fetch again)", language="text")
            else:
                st.error(text)


_JOB_APPLY = {"questions": _apply_questions, "domain": _apply_domain, "design": _apply_design,
              "generate": _apply_generate, "validate": _apply_validate, "deploy": _apply_deploy}


# ══════════════════════════════════════════════════════════════════════════════
# PROJECTS — persistent workspace, versions, approvals, audit
# ══════════════════════════════════════════════════════════════════════════════
def _ts(t) -> str:
    return time.strftime("%d %b %Y %H:%M", time.localtime(t)) if t else ""


def page_projects():
    st.markdown(ui.header("Workspace", "Projects",
                          "Every generated app is saved as a project with its prompt, design, files, validation "
                          "results, approvals and deployments — restore, compare, duplicate or export any version."),
                unsafe_allow_html=True)
    if not STORE.durable:
        st.warning(STORE.durability_note, icon="⚠️")
    pend = STORE.list_approvals("pending", user=USER)
    t_proj, t_appr, t_audit = st.tabs(["📁  Projects", f"📨  Approvals ({len(pend)})", "🧾  Audit log"])
    with t_proj:
        _projects_tab()
    with t_appr:
        _approvals_tab(pend)
    with t_audit:
        rows = STORE.list_audit(None if ac.can(USER, "project.view_all") else st.session_state.get("project_id"), 300)
        st.dataframe([{"When": _ts(a["at"]), "User": a["user"], "Action": a["action"],
                       "Project": a.get("project_id", "")[:8], "Detail": json.dumps(a.get("detail") or {})[:160]}
                      for a in rows], hide_index=True, use_container_width=True, height=420)


def _projects_tab():
    see_all = ac.can(USER, "project.view_all")
    c1, c2 = st.columns([3, 1])
    archived = c2.toggle("Show archived", key="pj_arch")
    projects = STORE.list_projects(USER, include_archived=archived, see_all=see_all)
    with c1:
        with st.popover("⬆ Import project (.zip)"):
            up = st.file_uploader("Workspace export", type=["zip"], key="pj_import")
            if up is not None and st.button("Import", type="primary", disabled=not ac.can(USER, "project.create")):
                try:
                    p = STORE.import_project(up.getvalue(), USER)
                    st.session_state["pj_sel"] = p["id"]
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"Import failed: {e}")
    if not projects:
        st.info("No projects yet — generate an app and it is saved here automatically.")
        return
    ids = [p["id"] for p in projects]
    by_id = {p["id"]: p for p in projects}
    default = st.session_state.get("pj_sel") or st.session_state.get("project_id")
    sel = st.selectbox("Project", ids, index=ids.index(default) if default in ids else 0, key="pj_sel_box",
                       format_func=lambda i: f"{by_id[i]['name']} · {by_id[i]['mode']} · owner {by_id[i]['owner']}"
                                             + (" · archived" if by_id[i]["archived"] else ""))
    p = STORE.get_project(sel)
    can_edit = ac.can(USER, "project.edit", p, STORE)
    versions = STORE.list_versions(sel)
    st.markdown(ui.metrics([("Versions", len(versions), f"updated {_ts(p['updated_at'])}", True),
                            ("Mode", p["mode"], p.get("app_id") or ""),
                            ("Your role", STORE.project_role(sel, USER) or ", ".join(sorted(USER.roles)), ""),
                            ("Deployments", len(STORE.list_deployments(sel)), "")]), unsafe_allow_html=True)
    if p.get("description"):
        st.caption(p["description"])
    vt, ct, dt, mt = st.tabs(["Versions", "Compare", "Deployments", "Settings & members"])
    with vt:
        for v in versions[:15]:
            val = v.get("validation") or {}
            vs = ("validation blocked" if val.get("blocking") else "validated") if val else "not validated"
            a, b, c = st.columns([4, 1, 1])
            a.markdown(f"**v{v['number']}** {v['label'] or ''} · {v['file_count']} files · {v['author']} · "
                       f"{_ts(v['created_at'])} · {vs}")
            if b.button("Restore", key=f"pj_rs_{v['id']}", use_container_width=True):
                restore_version(v["id"])
                st.rerun()
            vf = STORE.get_version(v["id"], with_files=True)
            c.download_button("ZIP", data=create_zip_archive(vf["files"], app_name="fiori_app") if vf["files"] else b"",
                              file_name=f"{p.get('app_id') or 'app'}-v{v['number']}.zip", key=f"pj_zip_{v['id']}",
                              use_container_width=True, disabled=not vf["files"])
    with ct:
        if len(versions) < 2:
            st.caption("Two versions are needed for a comparison.")
        else:
            lab = {v["id"]: f"v{v['number']} {v['label'] or ''}" for v in versions}
            x1, x2 = st.columns(2)
            va = x1.selectbox("From", list(lab), index=1, format_func=lab.get, key="pj_cmp_a")
            vb = x2.selectbox("To", list(lab), index=0, format_func=lab.get, key="pj_cmp_b")
            if va != vb:
                cmp = STORE.compare_versions(va, vb)
                st.markdown(f"**{len(cmp['added'])} added · {len(cmp['removed'])} removed · {len(cmp['changed'])} changed**")
                for ch in cmp.get("blueprint_changes") or []:
                    st.markdown(f"- {ch}")
                st.code(cmp["diff"] or "(no file differences)", language="diff", height=380)
    with dt:
        deps = STORE.list_deployments(sel)
        if not deps:
            st.caption("Not deployed yet.")
        st.dataframe([{"When": _ts(d["at"]), "Env": d["env"], "Version": d.get("version_number"), "By": d["user"],
                       "Status": d["status"], "URL": d.get("app_url", ""),
                       "Space": (d.get("target") or {}).get("space", "")} for d in deps],
                     hide_index=True, use_container_width=True)
    with mt:
        n1, n2 = st.columns([3, 1])
        new_name = n1.text_input("Name", value=p["name"], key=f"pj_name_{sel}", disabled=not can_edit)
        if n2.button("Rename", disabled=not can_edit or new_name.strip() == p["name"], use_container_width=True):
            STORE.rename_project(sel, new_name, USER)
            st.rerun()
        d1, d2, d3 = st.columns(3)
        if d1.button("⧉ Duplicate", use_container_width=True, disabled=not ac.can(USER, "project.create")):
            np_ = STORE.duplicate_project(sel, p["name"] + " (copy)", USER)
            st.session_state["pj_sel"] = np_["id"]
            st.rerun()
        d2.download_button("⬇ Export (.zip)", data=STORE.export_project(sel, USER), use_container_width=True,
                           file_name=f"{re.sub(r'[^A-Za-z0-9_-]+', '_', p['name'])}-workspace.zip")
        if d3.button("Unarchive" if p["archived"] else "Archive", use_container_width=True, disabled=not can_edit):
            STORE.archive_project(sel, USER, not p["archived"])
            st.rerun()
        st.markdown("**Members**")
        for m in p.get("members") or []:
            st.markdown(f"- {m['user']} — {m['role']}")
        if can_edit:
            m1, m2, m3 = st.columns([2.2, 1.2, 1])
            mu = m1.text_input("User (e-mail)", key=f"pj_mu_{sel}")
            mr = m2.selectbox("Role", ["developer", "reviewer", "viewer"], key=f"pj_mr_{sel}")
            if m3.button("Add", use_container_width=True, disabled=not (mu or "").strip()):
                STORE.add_member(sel, mu.strip().lower(), mr, by=USER)
                st.rerun()


def _approvals_tab(pend):
    if not ac.can(USER, "approval.decide"):
        st.caption("Only reviewers decide release approvals. Your own requests:")
        pend = [a for a in STORE.list_approvals("pending") if a.get("requester") == USER.id]
    if not pend:
        st.info("No pending approvals.")
    for a in pend:
        p = STORE.get_project(a["project_id"]) or {}
        v = STORE.get_version(a["version_id"], with_files=False) or {}
        val = v.get("validation") or {}
        with st.container(border=True):
            st.markdown(f"**{p.get('name', '?')}** v{v.get('number', '?')} → **{a['env']}** · requested by "
                        f"{a['requester']} · {_ts(a.get('requested_at'))}")
            if a.get("note"):
                st.caption(a["note"])
            st.caption(("Validation: " + ("BLOCKED — " + "; ".join(val.get("blocking") or []) if val.get("blocking")
                                          else f"{(val.get('summary') or {}).get('passed', 0)} passed, "
                                               f"{(val.get('summary') or {}).get('not_tested', 0)} not tested"))
                       if val else "Validation: not run for this version")
            if ac.can(USER, "approval.decide") and a["requester"] != USER.id:
                cm = st.text_input("Comment", key=f"ap_cm_{a['id']}")
                b1, b2, b3 = st.columns([1, 1, 2])
                if b1.button("✅ Approve", key=f"ap_ok_{a['id']}", use_container_width=True):
                    try:
                        STORE.decide_approval(a["id"], USER, True, cm)
                        st.rerun()
                    except (PermissionError, ValueError) as e:
                        st.error(str(e))
                if b2.button("❌ Reject", key=f"ap_no_{a['id']}", use_container_width=True):
                    try:
                        STORE.decide_approval(a["id"], USER, False, cm)
                        st.rerun()
                    except (PermissionError, ValueError) as e:
                        st.error(str(e))
                if b3.button("Open this version", key=f"ap_open_{a['id']}"):
                    restore_version(a["version_id"])
                    st.rerun()


# ──────────────────────────────────────────────────────────────────────────────
if "_bp_json_next" in st.session_state:          # programmatic update of the JSON editor
    st.session_state["bp_json"] = st.session_state.pop("_bp_json_next")

with _JOB_SLOT:
    jobs_panel()

if st.session_state.get("view") == "projects":
    page_projects()
else:
    [step_connect, step_describe, step_review, step_build, step_deploy][STEP]()
