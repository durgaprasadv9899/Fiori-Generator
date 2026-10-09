# Multi-OData + Authorization — Integration Guide

This guide wires `odata_config.py` into your existing `app.py` / `generator.py`
with the smallest possible diff, then lists recommended next steps for the POC.

---

## The core principle (read first)

A UI5 app runs in the **browser**. It can never safely hold OData credentials —
anything in `manifest.json`, a `.js` file, or the client half of `xs-app.json`
is visible in DevTools. So authorization is always **server-side**:

| Environment | Who holds the credential | How the app authenticates |
|-------------|--------------------------|---------------------------|
| Local dev (`ui5 serve`) | a **gitignored `.env`** read by `fiori-tools-proxy` | proxy injects auth + solves CORS |
| BTP deploy | a **Destination** in the BTP cockpit | AppRouter route → destination; **XSUAA** logs the user in |

The generated frontend only ever carries the user's session token. Real backend
secrets live in the `.env` (local) or the Destination (BTP) — never in code.

---

## Step 1 — extend `AppSpec` (generator.py)

Add a `services` list and an `auth_type` fallback. Keep the old single-service
fields so nothing breaks:

```python
# at top of generator.py
from odata_config import (
    ODataService, AUTH_NONE, AUTH_CHOICES,
    services_from_legacy_spec,
    manifest_datasources, manifest_models,
    ui5_proxy_middleware, env_example,
    xs_app_json, destinations_template,
    mta_auth_resources, xs_security_json,
)

@dataclass
class AppSpec:
    ...
    services: List["ODataService"] = field(default_factory=list)
    auth_type: str = AUTH_NONE      # fallback for the legacy single service

    def service_list(self):
        return services_from_legacy_spec(self)   # returns self.services or derives one
```

## Step 2 — feed services into the manifest

In `_make_manifest_by_type`, replace the single hardcoded `dataSources` /
`models` blocks with:

```python
svcs = spec.service_list()
manifest["sap.app"]["dataSources"] = manifest_datasources(svcs)
manifest["sap.ui5"]["models"] = {
    **manifest_models(svcs),
    "i18n": { ...keep your existing i18n model... },
}
```

Your views already bind the default model (`{/SalesOrders}`). A **second**
service is reached with its model name as a prefix, e.g. `{products>/Products}`.

## Step 3 — local-dev proxy (ui5.yaml)

Replace the body of `_make_ui5_yaml_hardcoded` proxy section with:

```python
base += ui5_proxy_middleware(spec.service_list())
```

Then also emit a `.env.example` so the developer supplies credentials at runtime:

```python
# add to _make_type_extra_files() or your file map:
files[".env.example"] = env_example(spec.service_list())
files[".gitignore"]  = "node_modules/\ndist/\n.env\n"
```

> Refinement: if several services share the same gateway root (`/sap/...`),
> dedupe them to a single proxy `path: /sap` forwarding to the common root.
> `ui5_proxy_middleware` currently emits one entry per service — fine for a
> POC, tidy this when you have >1 service on the same host.

## Step 4 — BTP auth (xs-app.json + mta.yaml + xs-security.json)

```python
# replace _make_root_xs_app_json_hardcoded body:
return xs_app_json(spec.service_list())

# in your mta.yaml builder, append:
mta_yaml += mta_auth_resources(spec.app_id, spec.service_list())

# add two new generated files:
files["xs-security.json"]      = xs_security_json(spec.app_id)
files["destinations.stub.json"] = destinations_template(spec.service_list())
```

The `destinations.stub.json` is documentation for you — import it in
**BTP cockpit → Connectivity → Destinations** and fill in the real URL +
credentials there. It ships with placeholders only.

## Step 5 — Streamlit UI (app.py) for N services

Replace the single "OData Connection" block with a repeatable one. Minimal version:

```python
st.markdown("**OData Services**")
n = st.number_input("How many services?", 1, 5, 1)
services = []
for i in range(int(n)):
    with st.expander(f"Service {i+1}", expanded=(i == 0)):
        sid   = st.text_input("Service id", value="salesorders" if i == 0 else f"svc{i}", key=f"id{i}")
        url   = st.text_input("OData URL", key=f"url{i}")
        ver   = st.selectbox("OData version", ["2.0", "4.0"], key=f"ver{i}")
        eset  = st.text_input("Entity set", value="SalesOrders", key=f"e{i}")
        keyf  = st.text_input("Key field", value="SalesOrderID", key=f"k{i}")
        titf  = st.text_input("Title field", value="CustomerName", key=f"t{i}")
        xtra  = st.text_input("Extra fields (comma-sep)", key=f"x{i}")
        auth  = st.selectbox("Authorization", AUTH_CHOICES, key=f"a{i}")
        services.append(ODataService(
            id=sid, url=url, odata_version=ver, entity_set=eset,
            key_field=keyf, title_field=titf,
            fields=[f.strip() for f in xtra.split(",") if f.strip()],
            auth_type=auth, is_primary=(i == 0),
        ))
# then: spec = AppSpec(..., services=services)
```

For **BasicAuthentication** you do NOT collect the password in the Streamlit UI —
the developer puts it in `.env` (local) or the Destination (BTP). The UI only
picks the auth *type*.

---

## Recommended POC implementation roadmap (priority order)

1. **Multi-service + auth (this change).** Foundation for everything else.
2. **Live `$metadata` introspection.** Instead of asking the user to type the
   entity set, key, and fields, fetch `<service_url>$metadata` server-side in
   `app.py`, parse the EDMX (e.g. `xml.etree`), and offer a dropdown of entity
   sets and auto-fill fields. Removes the biggest source of user error and makes
   "accept different OData services" nearly zero-config.
3. **OData v4 support.** You currently assume v2 (v2 `ODataModel`, `useBatch`,
   `X-CSRF-Token`). v4 uses a different model class and binding syntax. The
   module already tags version per service; the remaining work is v4-aware view
   / controller templates. Treat this as its own milestone — it is not a
   one-line change.
4. **Authorize the generator itself.** Right now anyone who opens the Streamlit
   app can generate and deploy, and `profiles/config_evoke.json` holds AI Core
   secrets in plaintext. For a shared POC add a login layer
   (`streamlit-authenticator` or put it behind the BTP AppRouter + XSUAA) and
   move secrets to environment variables / a secrets manager rather than a
   committed file.
5. **CSRF + write-path validation.** For real backends, confirm create/update/
   delete work end-to-end: v2 needs the `X-CSRF-Token` fetch (the model does it
   when `tokenHandling: true`, already set), and the proxy must forward cookies.
6. **Generated smoke test.** Emit a tiny script (or OPA5 test) that boots the
   app against the mock service and asserts the list renders — so every
   generated app is self-verifying before the user even deploys.

---

## Files in this change

| File | Purpose |
|------|---------|
| `odata_config.py` | New module: services, auth, and all the config builders |
| `.env.example` (generated) | Local-dev credential template — copy to `.env`, gitignore it |
| `destinations.stub.json` (generated) | BTP destination stubs to import in the cockpit |
| `xs-security.json` (generated) | XSUAA descriptor for the login layer |
