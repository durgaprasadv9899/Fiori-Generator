# Deploying the Fiori Generator POC to BTP Cloud Foundry

This deploys the **generator app itself** (Streamlit) as a hosted web app, with
`cf`/`mbt` vendored so the in-app "Deploy to BTP" button works inside the
container.

## Security model (read first)
In-container deploy means each user enters **their own** CF credentials in the
Deploy tab, which pass through this server process. Use a one-time **SSO
passcode** (expires ~1 min) rather than a password. Only run a deploy-enabled
hosted instance for **trusted users**. For public/demo use, prefer the ZIP
download + deploy-locally flow instead.

## Steps

1. **Vendor the CLI binaries + multiapps plugin into ./bin and ./cf-plugins**
   (run locally once, needs curl+tar; use Git Bash or WSL on Windows):
   ```
   ./scripts/vendor_cli.sh
   ```
   This downloads `cf` v8 and `mbt` into `./bin/`, and installs the LINUX
   multiapps plugin (which provides `cf deploy`) into `./cf-plugins/`. Both
   dirs upload with the app. At runtime deployer._cf_env() sets
   CF_PLUGIN_HOME=./cf-plugins so `cf deploy` is found in the container.
   Verify after running:
   ```
   ls -la bin/                         # cf, mbt (Linux binaries)
   ls -la cf-plugins/.cf/plugins/      # the multiapps plugin binary
   ```
   NOTE: these are Linux binaries — they won't run on Windows/macOS directly,
   which is correct; they're for the CF container.

2. **Verify requirements.txt** provides the `gen_ai_hub` module. The pip package
   is `generative-ai-hub-sdk[all]`. If your local env installed it from an SAP
   internal index, pin the exact `name==version` from `pip freeze` and add:
   ```
   cf set-env fiori-generator-poc PIP_EXTRA_INDEX_URL "<sap-index-url>"
   ```

3. **Log in and target:**
   ```
   cf login -a https://api.cf.us10-001.hana.ondemand.com --sso
   cf target -o "EVOKE TECHNOLOGIES SP. Z O.O._evokebtp-subaccount-qt2znvbe" -s BAS_DEV
   ```

4. **Push:**
   ```
   cf push
   ```

5. **Set AI Core credentials** (never commit real secrets):
   ```
   cf set-env fiori-generator-poc AICORE_CLIENT_ID     "<id>"
   cf set-env fiori-generator-poc AICORE_CLIENT_SECRET "<secret>"
   cf set-env fiori-generator-poc AICORE_AUTH_URL      "<url>"
   cf set-env fiori-generator-poc AICORE_BASE_URL      "<url>"
   cf set-env fiori-generator-poc AICORE_RESOURCE_GROUP "default"
   cf restage fiori-generator-poc
   ```
   (app.py should read these from env; the profiles/config_evoke.json file is
   for local dev only and should not contain live secrets in the pushed code.)

## Known limitations
- **On-prem metadata reads** (e.g. EvokeSD4 via Cloud Connector) still can't be
  reached from the container — use "Paste metadata XML" at generation time.
- **multiapps plugin**: `cf deploy` needs it. vendor_cli.sh attempts to install
  it; if it didn't, the first MTA deploy will prompt/fail — install once against
  the vendored cf.
- The vendored `cf`/`mbt` are Linux amd64 binaries matched to the CF stack; if
  the buildpack stack changes, re-run vendor_cli.sh.
