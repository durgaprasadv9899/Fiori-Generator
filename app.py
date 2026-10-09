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


def ai_call(system_prompt: str, user_prompt: str) -> str:
    """The foundation model chosen in the sidebar (cached per model + temperature)."""
    model = st.session_state.get("ai_model")
    temp = st.session_state.get("temperature", 0.15)
    key = f"_ai_fn::{model}::{temp}"
    if key not in st.session_state:
        st.session_state[key] = ai_client.make_ai_fn(model, temperature=temp, max_tokens=16000)
    return st.session_state[key](system_prompt, user_prompt)


def has_ai() -> bool:
    return bool(CREDENTIALS_LOADED and st.session_state.get("ai_model"))


# ──────────────────────────────────────────────────────────────────────────────
# Access gate (APP_ACCESS_PASSWORD on public routes)
# ──────────────────────────────────────────────────────────────────────────────
_ACCESS_PASSWORD = os.environ.get("APP_ACCESS_PASSWORD", "")
if _ACCESS_PASSWORD and not st.session_state.get("_access_ok"):
    import hmac as _hmac
    st.markdown(ui.brand_bar(LOGO, []), unsafe_allow_html=True)
    st.markdown('<div style="height:3.5rem"></div>', unsafe_allow_html=True)
    _c = st.columns([1, 1.1, 1])[1]
    with _c:
        with st.container(key="login_card"):
            st.markdown("**🔒 Access required**")
            _pw = st.text_input("Access password", type="password", key="_access_pw",
                                placeholder="Enter the studio access password")
            if _pw and _hmac.compare_digest(_pw, _ACCESS_PASSWORD):
                st.session_state["_access_ok"] = True
                st.rerun()
            elif _pw:
                st.error("Wrong password.")
        st.markdown('<div class="ev-login-foot">Protected workspace · Evoke Technologies</div>',
                    unsafe_allow_html=True)
    st.stop()

for _key, _default in {"generated_files": {}, "generation_history": [], "deploy_log": [],
                       "deployed_url": "", "step": 0, "refine_history": []}.items():
    st.session_state.setdefault(_key, _default)

# ──────────────────────────────────────────────────────────────────────────────
# Sidebar — AI settings, history
# ──────────────────────────────────────────────────────────────────────────────
with st.sidebar:
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

    st.markdown("### Recent apps")
    if not st.session_state.generation_history:
        st.caption("Generated apps appear here.")
    for _i, _entry in enumerate(reversed(st.session_state.generation_history[-8:])):
        st.markdown(f'<div class="ev-hist"><b>{_entry["name"]}</b><br><span>{_entry["time"]} · '
                    f'{_entry["file_count"]} files · {_entry.get("floorplan", "")}</span></div>',
                    unsafe_allow_html=True)
        if st.button("Restore", key=f"restore_{_i}", use_container_width=True):
            st.session_state.generated_files = _entry["files"]
            st.session_state["_goto"] = 3
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


def step_connect():
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
    if c3.button("✨ Design my app", type="primary", use_container_width=True, disabled=not ready):
        _run_design()


def _run_design():
    import app_builder as _ab
    import ai_designer as _ad
    fp = st.session_state.get("bp_floorplan", "auto")
    with st.status("Designing your application…", expanded=True) as status:
        t0 = time.time()
        try:
            if has_ai():
                model = _app_model()
                res = _ad.design(st.session_state["user_prompt"], model, ai_call, "" if fp == "auto" else fp,
                                 st.session_state.get("app_style"),
                                 analyze_first=st.session_state.get("opt_analyze", True),
                                 repair=st.session_state.get("opt_repair", True),
                                 check_coverage=st.session_state.get("opt_coverage", True),
                                 progress=lambda m: status.write(m),
                                 cache_tag=f"{st.session_state.get('ai_model')}|{st.session_state.get('temperature')}")
            else:
                status.write("No AI model — using the rule-based designer.")
                res = _ab.design(st.session_state["user_prompt"], st.session_state["md_edmx"], None,
                                 "" if fp == "auto" else fp, st.session_state.get("app_style"),
                                 service_url=st.session_state.get("md_service_url_val", ""),
                                 extra_services=_extra_services())
        except Exception as e:  # noqa: BLE001
            status.update(label=f"Design failed: {e}", state="error")
            return
        status.update(label=f"Design ready in {time.time() - t0:.0f}s", state="complete", expanded=False)
    st.session_state["bp_design"] = {k: res.get(k) for k in ("blueprint", "notes", "source", "spec", "coverage", "steps")}
    st.session_state["bp_json"] = json.dumps(res["blueprint"], indent=2)
    st.session_state["refine_history"] = []
    st.session_state.generated_files = {}
    goto(2)


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

    t_prev, t_cov, t_spec, t_notes, t_json = st.tabs(
        ["🖥  App preview", "✅  Coverage" + (f" ({score}%)" if score is not None else ""), "📋  Specification",
         f"⚙️  Design notes ({len(design['notes'])})", "{ }  Blueprint"])
    with t_prev:
        st.caption("Interactive wireframe with the real field labels — click the navigation, rows and cards.")
        model = _model()
        components.html(ui_preview.wireframe_html(bp, model), height=560, scrolling=False)
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
    if c3.button("🚀 Generate application", type="primary", use_container_width=True):
        _generate(design)


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
    svc = (st.session_state.get("md_service_url_val") or "").strip()
    if not svc or "YOUR_SERVICE" in svc:
        st.error("No OData service path set for the app (e.g. /sap/opu/odata/sap/API_SALES_ORDER_SRV/). "
                 "Set it in step 1 — otherwise every data request 404s.")
        return
    use_existing = bool(st.session_state.get("md_use_existing_dest"))
    with st.status("Generating the application…", expanded=True) as status:
        status.write("Rendering views, controllers, custom logic and configuration…")
        if has_ai() and len(design["blueprint"]["app"]["languages"]) > 1:
            status.write(f"Translating texts into {', '.join(design['blueprint']['app']['languages'][1:])}…")
        try:
            res = _ab.build(
                edmx=st.session_state["md_edmx"], requirement=st.session_state.get("user_prompt", ""),
                app_id=st.session_state.get("app_id_v", "com.mycompany.app"), service_url=svc,
                ai_fn=ai_call if has_ai() else None, blueprint=design["blueprint"],
                auth_type=st.session_state.get("md_auth", AUTH_NONE) if not use_existing else AUTH_NONE,
                use_existing_destination=use_existing,
                destination_name=(st.session_state.get("md_dest_name_val") or "").strip(),
                on_premise=bool(st.session_state.get("md_on_premise")),
                theme=st.session_state.get("theme_v", "sap_horizon"),
                style=st.session_state.get("app_style"), logo_data_uri=_logo_uri(),
                design_spec=design.get("spec"), coverage=design.get("coverage"),
                extra_services=_extra_services())
        except Exception as e:  # noqa: BLE001
            status.update(label=f"Generation failed: {e}", state="error")
            return
        status.update(label=f"Generated {len(res.files)} files", state="complete", expanded=False)
    st.session_state.generated_files = res.files
    design["blueprint"] = res.blueprint
    st.session_state["build_result"] = {"quality": res.quality, "fixes": res.fixes, "notes": res.notes,
                                        "floorplan": res.blueprint["floorplan"],
                                        "odata": res.model["odata_version"], "title": res.blueprint["app"]["title"]}
    st.session_state["deploy_meta"] = {
        "app_id": res.spec.app_id,
        "services": [{"id": s.id, "url": s.url, "auth_type": s.auth_type, "dest_name": s.dest_name(),
                      "needs_auth": s.needs_auth(), "is_real": s.is_real(),
                      "use_existing_destination": s.use_existing_destination, "on_premise": s.on_premise}
                     for s in res.spec.service_list()]}
    st.session_state.generation_history.append({
        "name": res.blueprint["app"]["title"], "prompt": st.session_state.get("user_prompt", ""),
        "files": res.files, "file_count": len(res.files), "time": time.strftime("%H:%M"),
        "floorplan": res.blueprint["floorplan"]})
    goto(3)


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
    st.markdown(ui.header("Step 5", "Deploy to SAP BTP Cloud Foundry",
                          "Builds the MTA and deploys the approuter with XSUAA login, the destination and (for "
                          "on-premise systems) the connectivity service."), unsafe_allow_html=True)
    mta_text = files.get("mta.yaml", "")
    meta = st.session_state.get("deploy_meta", {})
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
    if not protected and not st.session_state.get("files_raw"):
        method = st.segmented_control("Method", ["MTA Build + Deploy", "cf push (static)"], default="MTA Build + Deploy",
                                      key="dep_method") or "MTA Build + Deploy"

    if st.button("🚀 Deploy", type="primary", use_container_width=True,
                 disabled=not (cf_org and cf_space and creds_ok) or bool(protected and missing)):
        _deploy(files, meta, cf_api, cf_org, cf_space, use_sso, cf_passcode, cf_user, cf_pass, dest_creds,
                protected, method)

    if st.session_state.deployed_url:
        st.success(f"Live at [{st.session_state.deployed_url}]({st.session_state.deployed_url})")
    _logs_box(cf_api, cf_org, cf_space)
    nav_buttons(back=3)


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


def _deploy(files, meta, cf_api, cf_org, cf_space, use_sso, cf_passcode, cf_user, cf_pass, dest_creds,
            protected, method):
    import quality_validator as _qv
    deployer = CFDeployer(api_url=cf_api, org=cf_org, space=cf_space, cf_home=_cf_home())
    app_id = meta.get("app_id") or "fiori-app"
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
    log_area = st.empty()
    logs = []

    def log(line):
        logs.append(str(line))
        log_area.markdown('<div class="ev-log">' + "<br>".join(l.replace("<", "&lt;") for l in logs[-60:]) + "</div>",
                          unsafe_allow_html=True)

    with tempfile.TemporaryDirectory() as tmp:
        if st.session_state.get("files_raw"):        # imported project: paths are already final
            for p, c in files.items():
                fp = os.path.join(tmp, p)
                os.makedirs(os.path.dirname(fp) or tmp, exist_ok=True)
                with open(fp, "w", encoding="utf-8") as fh:
                    fh.write(c)
            for p, c in (st.session_state.get("raw_binary") or {}).items():
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
            return
        for line in deployer.target():
            log(line)
        if not deployer.is_logged_in():
            log("❌ The Cloud Foundry session has expired — sign in again above.")
            st.error("Not signed in to Cloud Foundry.")
            return
        log(f"✅ Signed in as {deployer.whoami()}")
        if method.startswith("MTA"):
            if protected and not deployer.check_multiapps_plugin():
                log("❌ CF multiapps plugin missing (cf install-plugin multiapps).")
                return
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
        st.session_state["deployed_app"] = app_id
        url = deployer.get_app_url(app_id)
        if url:
            st.session_state.deployed_url = url
            log(f"🌐 {url}")
            st.balloons()


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


# ──────────────────────────────────────────────────────────────────────────────
if "_bp_json_next" in st.session_state:          # programmatic update of the JSON editor
    st.session_state["bp_json"] = st.session_state.pop("_bp_json_next")

[step_connect, step_describe, step_review, step_build, step_deploy][STEP]()
