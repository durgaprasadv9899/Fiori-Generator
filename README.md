# End-to-End Fiori App Generator

Generates complete, deployable SAPUI5/Fiori applications from a text prompt.
Powered by SAP AI Core → deploys directly to SAP BTP Cloud Foundry.

Supports **multiple OData services** per app and **authorization** for each,
done the only browser-safe way: credentials live in a gitignored `.env` for
local dev and in a BTP **Destination** + **XSUAA** for deployment — never in
generated frontend code. See `INTEGRATION_GUIDE.md` for the full design.

## Multiple OData services + authorization

In Stage 1 choose *Number of services*. Service 1 is the primary (it drives the
generated list/detail views); additional services become extra named UI5 models
you can bind to (e.g. `{products>/Products}`). Each service has its own URL,
OData version, entity config, and **Authorization** type:

| Auth type | Local dev (`ui5 serve`) | BTP deploy |
|-----------|-------------------------|------------|
| `NoAuthentication` | direct | static route, no login |
| `BasicAuthentication` | `.env` → fiori-tools-proxy | Destination (User/Password) |
| `OAuth2ClientCredentials` | `.env` (token URL + client id/secret) | Destination (OAuth) |
| `PrincipalPropagation` | n/a (needs Cloud Connector) | Destination + Cloud Connector |

You never type a password in the UI — only the auth *type*. The generator then
emits, when a service needs auth:

- `.env.example` — copy to `.env`, fill in, **never commit** (gitignored)
- `ui5.yaml` proxy block — one backend per service (also fixes CORS)
- `xs-app.json` — an xsuaa-protected route per backend → named Destination
- `mta.yaml` — approuter wired to XSUAA + Destination managed services
- `xs-security.json` — XSUAA descriptor
- `destinations.stub.json` — import in BTP cockpit and fill in the real values

---

## Project Structure

```
fiori_generator/
├── app.py              ← Streamlit UI  (5 stages, 3 tabs)
├── generator.py        ← Multi-pass AI generation engine
├── deployer.py         ← BTP CF deployment + ZIP export
├── requirements.txt
├── profiles/
│   └── config_evoke.json   ← AI Core credentials (you provide)
└── generated_app/          ← Written at runtime
    ├── webapp/
    │   ├── manifest.json
    │   ├── index.html
    │   ├── Component.js
    │   ├── view/Main.view.xml
    │   ├── controller/Main.controller.js
    │   ├── model/AppModel.js
    │   ├── i18n/i18n.properties
    │   └── fragment/DetailDialog.fragment.xml
    ├── xs-app.json
    ├── mta.yaml
    ├── package.json
    └── ui5.yaml
```

---

## Setup

```bash
pip install -r requirements.txt
streamlit run app.py
```

Ensure `profiles/config_evoke.json` is present with:
```json
{
  "AICORE_AUTH_URL":       "...",
  "AICORE_CLIENT_ID":      "...",
  "AICORE_CLIENT_SECRET":  "...",
  "AICORE_RESOURCE_GROUP": "...",
  "AICORE_BASE_URL":       "..."
}
```

---

## What Each File Does

| File | Role |
|------|------|
| `app.py` | 3-tab Streamlit UI: Build → Preview/Export → Deploy |
| `generator.py` | `AppSpec` dataclass + 11 per-file prompt builders + multi-pass loop |
| `deployer.py` | `CFDeployer` (cf CLI wrapper), ZIP archive builder, disk writer |

---

## Stage 1 — Prompt Builder
- Free-text description OR quick template
- App pattern: ListDetail / Worklist / MasterDetail / SinglePage
- OData URL, entity set, key field, extra fields
- Theme picker (sap_horizon / sap_belize / sap_fiori_3)
- Options: model layer, i18n, fragments, deployment files

## Stage 2–3 — Multi-pass AI Generation
Generates up to 11 files in dependency order:
1. `manifest.json` — routing, models, Fiori launchpad inbound
2. `Component.js` — UIComponent with router init
3. `view/Main.view.xml` — sap.m controls, i18n bindings, search/CRUD
4. `controller/Main.controller.js` — OData CRUD, error handling, dialog
5. `model/AppModel.js` — ODataModel factory, JSONModel helpers, formatters
6. `i18n/i18n.properties` — all UI strings
7. `fragment/DetailDialog.fragment.xml` — create/edit dialog
8. `xs-app.json` — AppRouter config for BTP
9. `mta.yaml` — MTA deployment descriptor
10. `package.json` — UI5 CLI build scripts
11. `ui5.yaml` — UI5 tooling config

## Stage 4 — Preview & Export
- File tree navigator + editable code editor
- Syntax highlighting per file type
- Single-file regeneration with additional instructions
- **Download full ZIP** (ready to unzip into BAS or VS Code)

## Stage 5 — BTP Cloud Foundry Deploy
- CF credentials form (password never stored)
- Two methods: MTA build + deploy (recommended) OR cf push
- Real-time streaming logs
- Auto-retrieves deployed app URL
- Fetch recent app logs

---

## Prerequisites for Deployment

```bash
# CF CLI
brew install cloudfoundry/tap/cf-cli@8   # macOS
# or download from https://docs.cloudfoundry.org/cf-cli/

# MTA build tool (for MTA method)
npm install -g mbt

# CF multiapps plugin — provides `cf deploy`, required to provision services
cf install-plugin multiapps

# UI5 CLI (for local preview)
npm install -g @ui5/cli
```

## Deployment that provisions services + destinations

When the app has any authorized service, deployment runs through **MTA** (not
`cf push`, which only serves static files and cannot create services). The
generated `mta.yaml`:

- declares an **XSUAA** managed service (from `xs-security.json`) and a
  **destination** managed service, and the approuter `requires:` both — so they
  are created and **bound** on deploy;
- auto-creates the **OData destination(s)** via the destination service's
  `init_data`, so no manual cockpit step is needed.

Secrets never live in `mta.yaml` — the destination URL/credentials are
`${parameter}` placeholders. At deploy time the Deploy tab collects them (you
type them into the *Destination Credentials* form), writes a temporary
`deploy.mtaext`, and runs `cf deploy app.mtar -e deploy.mtaext`. That file is
gitignored and lives only in the temp build dir. A `deploy.mtaext.example`
(placeholders only) is generated for reference.

---

## Extending the Generator

To add a new file type, add to `generator.py`:
1. A `_make_<name>_prompt(spec, ctx)` function
2. An entry in `PROMPT_BUILDERS` dict
3. Add the filename to `build_file_list()` logic
