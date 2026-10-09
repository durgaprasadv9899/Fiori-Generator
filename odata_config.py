"""
odata_config.py — multi-service OData + authorization support for the Fiori generator.

WHY THIS MODULE EXISTS
----------------------
The original generator carried a single `odata_url` on AppSpec. This module adds
first-class support for:

  1. Multiple, differently-typed OData services in one generated app
     (e.g. a Sales-Order service AND a Product service).
  2. Authorization done the ONLY safe way for a browser app:
       - local dev  -> a server-side proxy that injects credentials from a
                       gitignored .env file (also fixes CORS).
       - BTP deploy -> AppRouter route -> named Destination (holds the real
                       credentials + auth type) -> XSUAA for the user login.
     No secret is ever written into generated frontend code.

It is intentionally dependency-free (pure string/dict builders) so it plugs into
the existing generator.py style without new packages.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List
import json
import re


# ─────────────────────────────────────────────────────────────────────────────
# Auth types we support. These map 1:1 onto SAP BTP Destination "Authentication"
# values, so the same enum drives both local proxy and the destination template.
# ─────────────────────────────────────────────────────────────────────────────
AUTH_NONE      = "NoAuthentication"          # public (no auth)
AUTH_BASIC     = "BasicAuthentication"       # user + password (on-prem GW, trials)
AUTH_OAUTH_CC  = "OAuth2ClientCredentials"   # clientid/secret/tokenurl (BTP APIs)
AUTH_PRINCIPAL = "PrincipalPropagation"      # SSO via Cloud Connector (advanced)

AUTH_CHOICES = [AUTH_NONE, AUTH_BASIC, AUTH_OAUTH_CC, AUTH_PRINCIPAL]


def _slug(text: str) -> str:
    """Safe identifier: 'Sales Order v2!' -> 'SalesOrderv2'."""
    return re.sub(r"[^A-Za-z0-9]", "", text) or "Service"


def _env_prefix(service_id: str) -> str:
    """Env-var prefix for a service, e.g. 'salesorders' -> 'SALESORDERS'."""
    return re.sub(r"[^A-Za-z0-9]", "_", service_id).upper()


def _path_prefix(url: str) -> str:
    """The routable path of a service URL, without scheme/host and without a
    trailing slash. Used so distinct services produce distinct, non-colliding
    proxy paths and approuter routes.

      'https://host/sap/opu/odata/sap/SO_SRV/' -> '/sap/opu/odata/sap/SO_SRV'
      '/sap/opu/odata/sap/PROD_SRV/'           -> '/sap/opu/odata/sap/PROD_SRV'
    """
    u = (url or "").strip()
    m = re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://[^/]+(/.*)$", u)
    if m:
        u = m.group(1)
    u = "/" + u.strip("/")
    return u if u != "/" else "/sap"


# ─────────────────────────────────────────────────────────────────────────────
# One OData service the generated app talks to.
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ODataService:
    id: str                                   # short id, unique per app: "salesorders"
    url: str = ""                             # e.g. "/sap/opu/odata/sap/SEPMRA_SO_MAN/"
    odata_version: str = "2.0"                # "2.0" or "4.0"
    entity_set: str = "Items"
    key_field: str = "Id"
    title_field: str = "Title"
    fields: List[str] = field(default_factory=list)
    auth_type: str = AUTH_NONE
    # Destination name in BTP. If blank we derive one from id. The DESTINATION
    # (not this app) stores the real credentials + auth config.
    destination: str = ""
    is_primary: bool = False                  # the default ("") UI5 model
    # When set, this service reuses an EXISTING destination already configured
    # in the BTP cockpit (e.g. a shared S/4HANA connection like "EvokeSD4")
    # instead of auto-creating a new one via init_data. No URL/credentials are
    # collected or written for this service — the destination already has them.
    use_existing_destination: bool = False
    # On-premise backends (reached via Cloud Connector) need the Connectivity
    # service bound to the approuter, regardless of who created the
    # destination. Set this when the destination's ProxyType is OnPremise.
    on_premise: bool = False

    # ── derived helpers ──────────────────────────────────────────────────────
    def dest_name(self) -> str:
        return self.destination or (_slug(self.id) + "_dest")

    def datasource_name(self) -> str:
        return self.id if not self.is_primary else "mainService"

    def model_name(self) -> str:
        # Primary service is the unnamed default model (""), others are named.
        return "" if self.is_primary else self.id

    def model_class(self) -> str:
        return ("sap.ui.model.odata.v4.ODataModel" if self.odata_version.startswith("4")
                else "sap.ui.model.odata.v2.ODataModel")

    def all_fields(self) -> List[str]:
        base = [self.key_field, self.title_field]
        return base + [f for f in self.fields if f not in base]

    def is_real(self) -> bool:
        u = (self.url or "").strip()
        return bool(u) and "<your" not in u and "YOUR_SERVICE" not in u

    def needs_auth(self) -> bool:
        return self.auth_type != AUTH_NONE and self.is_real()

    def needs_own_destination(self) -> bool:
        """True when this service needs a NEW destination created via
        init_data — i.e. it's real and NOT reusing an existing cockpit
        destination. False for existing-destination services: they still
        need the destination SERVICE bound (so the approuter can look up
        destinations at all), but nothing new is created for them."""
        return self.is_real() and not self.use_existing_destination



# ─────────────────────────────────────────────────────────────────────────────
# manifest.json — dataSources + models for N services
# ─────────────────────────────────────────────────────────────────────────────
def manifest_datasources(services: List[ODataService]) -> Dict:
    out = {}
    for s in services:
        out[s.datasource_name()] = {
            "uri": s.url or "/sap/opu/odata/sap/YOUR_SERVICE/",
            "type": "OData",
            "settings": {"odataVersion": s.odata_version},
        }
    return out


def manifest_models(services: List[ODataService]) -> Dict:
    out = {}
    for s in services:
        if s.odata_version.startswith("4"):
            settings = {"synchronizationMode": "None", "operationMode": "Server",
                        "autoExpandSelect": True, "earlyRequests": True}
        else:
            # v2 defaults; tokenHandling True lets the model fetch X-CSRF-Token
            # automatically before any write (POST/PUT/DELETE).
            settings = {"defaultBindingMode": "TwoWay", "useBatch": True,
                        "tokenHandling": True}
        out[s.model_name()] = {
            "dataSource": s.datasource_name(),
            "preload": s.is_primary,
            "settings": settings,
        }
    return out


# ─────────────────────────────────────────────────────────────────────────────
# LOCAL DEV — ui5.yaml proxy middleware.
# One backend entry per real service. Credentials are NEVER written here; the
# fiori-tools-proxy reads them at runtime from environment variables (see
# env_example()). This both authorizes the call and eliminates CORS.
# ─────────────────────────────────────────────────────────────────────────────
def ui5_proxy_middleware(services: List[ODataService]) -> str:
    real = [s for s in services if s.is_real()]
    if not real:
        return "# No OData proxy — no real backend configured.\n"

    lines = [
        "server:",
        "  customMiddleware:",
        "  - name: fiori-tools-proxy",
        "    afterMiddleware: compression",
        "    configuration:",
        "      ignoreCertError: false",
        "      backend:",
    ]
    seen = set()
    for s in real:
        # Use the full service path so two services never collide on '/sap'.
        path = _path_prefix(s.url)
        if path in seen:
            continue
        seen.add(path)
        lines += [
            f"      - path: {path}",
            f"        url: {s.url}",
        ]
        if s.needs_auth():
            # fiori-tools-proxy resolves basic-auth creds from the environment.
            # See env_example() for the exact variable names it expects.
            lines.append(f"        # auth: {s.auth_type} — credentials via env, never committed")
    return "\n".join(lines) + "\n"


def env_example(services: List[ODataService]) -> str:
    """.env.example — the runtime secrets the developer fills in locally.

    IMPORTANT: add `.env` to .gitignore. Only `.env.example` (no real values)
    is committed. fiori-tools-proxy picks these up when you run `ui5 serve`.
    """
    out = [
        "# Local-dev credentials for OData backends. Copy to `.env` and fill in.",
        "# NEVER commit `.env`. These are read by fiori-tools-proxy at runtime.",
        "",
        "# fiori-tools-proxy generic basic-auth fallback (single backend):",
        "FIORI_TOOLS_USER=",
        "FIORI_TOOLS_PASSWORD=",
        "",
    ]
    for s in services:
        if not s.needs_auth():
            continue
        p = _env_prefix(s.id)
        out.append(f"# --- {s.id} ({s.auth_type}) ---")
        if s.auth_type == AUTH_BASIC:
            out += [f"{p}_USER=", f"{p}_PASSWORD="]
        elif s.auth_type == AUTH_OAUTH_CC:
            out += [f"{p}_TOKEN_URL=", f"{p}_CLIENT_ID=", f"{p}_CLIENT_SECRET="]
        out.append("")
    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────────────
# BTP DEPLOY — xs-app.json routes.
# Each real service gets a route that (a) requires an authenticated user
# (xsuaa) and (b) forwards to a named Destination. The Destination — configured
# in the BTP cockpit, not here — holds the actual backend credentials.
# ─────────────────────────────────────────────────────────────────────────────
def xs_app_routes(services: List[ODataService]) -> List[Dict]:
    routes = []
    seen = set()
    for s in services:
        if not s.is_real():
            continue
        prefix = _path_prefix(s.url)          # e.g. /sap/opu/odata/sap/SO_SRV
        if prefix in seen:
            continue
        seen.add(prefix)
        routes.append({
            # Distinct full-path source so multiple backends don't collide.
            "source": f"^{prefix}/(.*)$",
            "target": f"{prefix}/$1",
            "destination": s.dest_name(),
            # xsuaa => user must log in; the approuter then attaches the token /
            # does principal propagation per the destination config.
            # ALWAYS require a BTP login for a real backend: even when the app
            # itself needs no credentials, the DESTINATION does (often a
            # technical user) — an unauthenticated route would expose the
            # backend to anyone who finds the URL.
            "authenticationType": "xsuaa",
            "csrfProtection": False,   # let the ODataModel manage CSRF end-to-end
        })
    # Fallback: serve the UI5 sources directly. The generated app keeps its UI
    # files (manifest.json, view/, controller/, index.html) under the `webapp/`
    # folder (see write_project_to_disk in deployer.py), so the approuter must
    # serve from there — NOT from "." (the project root, which only holds
    # mta.yaml/package.json/xs-app.json). With localDir "." the welcomeFile
    # "/index.html" is not found and the deployed app returns "Not Found".
    # There is NO ui5 build step, so no local node/npm is needed at MTA build
    # time — the approuter serves the sources as-is and BTP's buildpack installs
    # @sap/approuter at staging. Keep this route LAST so the OData routes above
    # match first.
    routes.append({
        "source": "^(.*)$",
        "localDir": "webapp",
        "authenticationType": "xsuaa" if any(s.is_real() for s in services) else "none",
    })
    return routes


def xs_app_json(services: List[ODataService]) -> str:
    protected = any(s.is_real() for s in services)
    return json.dumps({
        # The approuter requires a leading slash on welcomeFile; without it the
        # welcome file is not resolved and the root URL returns "not found" even
        # though the app started fine.
        "welcomeFile": "/index.html",
        "authenticationMethod": "route" if protected else "none",
        "routes": xs_app_routes(services),
        "logout": {"logoutEndpoint": "/logout"},
    }, indent=2)


def destinations_template(services: List[ODataService]) -> str:
    """A cockpit-importable destination stub — only for services that need a
    NEW destination created. Services reusing an existing cockpit destination
    (use_existing_destination=True) are skipped: nothing to create, they
    already exist (e.g. a shared S/4HANA connection like "EvokeSD4").

    You import this in BTP (Connectivity > Destinations) and fill in URL +
    credentials there. This file documents WHAT to create; it holds no secrets.
    """
    dests = []
    for s in services:
        if not s.needs_own_destination():
            continue
        d = {
            "Name": s.dest_name(),
            "Type": "HTTP",
            "URL": "<backend-base-url>",
            "ProxyType": "OnPremise" if s.on_premise else "Internet",
            "Authentication": s.auth_type,
            "HTML5.DynamicDestination": "true",
            "WebIDEEnabled": "true",
        }
        if s.on_premise:
            d["CloudConnectorLocationId"] = "<location-id-if-any>"
        if s.auth_type == AUTH_BASIC:
            d.update({"User": "<user>", "Password": "<password>"})
        elif s.auth_type == AUTH_OAUTH_CC:
            d.update({"tokenServiceURL": "<token-url>",
                      "clientId": "<client-id>",
                      "clientSecret": "<client-secret>"})
        dests.append(d)
    return json.dumps({"destinations": dests}, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# BTP DEPLOY — mta.yaml resources: XSUAA + destination (+ Connectivity, for
# on-premise backends) service with the OData destination auto-created at
# deploy time via init_data. Secrets are NOT in the committed mta.yaml — they
# are parameter placeholders (default TO_BE_PROVIDED) overridden at deploy
# time by a generated .mtaext (see mta_extension()).
#
# Services with use_existing_destination=True (e.g. a shared S/4HANA
# connection like "EvokeSD4" already configured in the BTP cockpit) are NOT
# added to init_data — nothing new is created for them. They still need the
# destination SERVICE bound (so the approuter can look up ANY subaccount
# destination, self-created or not) and, if on_premise, the Connectivity
# service bound too.
# ─────────────────────────────────────────────────────────────────────────────
MTA_PARAM_DEFAULT = "TO_BE_PROVIDED"


def _dest_params(s: ODataService) -> Dict[str, str]:
    """Parameter names (keys) this service contributes to the MTA, mapped to the
    destination field they populate. Only services needing a NEW destination
    contribute parameters — existing-destination services need none, since
    the destination is already fully configured in the cockpit."""
    if not s.needs_own_destination():
        return {}
    p = _env_prefix(s.id).lower()
    out = {f"{p}_url": "URL"}
    if s.auth_type == AUTH_BASIC:
        out[f"{p}_user"] = "User"
        out[f"{p}_password"] = "Password"
    elif s.auth_type == AUTH_OAUTH_CC:
        out[f"{p}_tokenurl"] = "tokenServiceURL"
        out[f"{p}_clientid"] = "clientId"
        out[f"{p}_clientsecret"] = "clientSecret"
    return out


def mta_parameters_block(services: List[ODataService]) -> str:
    """Top-level MTA `parameters:` defaults — only for services that need a
    NEW destination created. These are safe placeholders; the real values
    arrive via the deploy-time .mtaext."""
    lines = []
    for s in services:
        for param in _dest_params(s):
            lines.append(f"  {param}: {MTA_PARAM_DEFAULT}")
    return "\n".join(lines) + ("\n" if lines else "")


def _destination_initdata_yaml(services: List[ODataService], indent: str) -> str:
    """The `destinations:` list (YAML) for the destination resource init_data.
    Only services needing a NEW destination appear here — existing-destination
    services are skipped entirely since nothing should be created for them."""
    out = []
    for s in services:
        if not s.needs_own_destination():
            continue
        p = _env_prefix(s.id).lower()
        out.append(f"{indent}- Name: {s.dest_name()}")
        out.append(f"{indent}  Type: HTTP")
        out.append(f"{indent}  URL: ${{{p}_url}}")
        out.append(f"{indent}  ProxyType: {'OnPremise' if s.on_premise else 'Internet'}")
        out.append(f"{indent}  Authentication: {s.auth_type}")
        out.append(f"{indent}  HTML5.DynamicDestination: 'true'")
        out.append(f"{indent}  WebIDEEnabled: 'true'")
        if s.auth_type == AUTH_BASIC:
            out.append(f"{indent}  User: ${{{p}_user}}")
            out.append(f"{indent}  Password: ${{{p}_password}}")
        elif s.auth_type == AUTH_OAUTH_CC:
            out.append(f"{indent}  tokenServiceURL: ${{{p}_tokenurl}}")
            out.append(f"{indent}  clientId: ${{{p}_clientid}}")
            out.append(f"{indent}  clientSecret: ${{{p}_clientsecret}}")
    return "\n".join(out) + "\n"


def mta_auth_resources(app_id: str, services: List[ODataService]) -> str:
    """XSUAA + destination (+ Connectivity if any service is on-premise)
    managed services. Bound whenever any service is real — a bound
    destination-service instance is what lets the approuter look up ANY
    subaccount destination, whether auto-created here or already existing
    (e.g. "EvokeSD4"). init_data only lists services needing a NEW
    destination; existing ones are referenced by name in xs-app.json but
    never (re)created."""
    if not any(s.is_real() for s in services):
        return "# No resources needed — no real backend service configured.\n"
    xsuaa = _slug(app_id) + "-xsuaa"
    dest  = _slug(app_id) + "-destination"
    conn  = _slug(app_id) + "-connectivity"
    needs_new = [s for s in services if s.needs_own_destination()]

    if needs_new:
        dest_config = (
            "      config:\n"
            "        init_data:\n"
            "          instance:\n"
            "            existing_destinations_policy: update\n"
            "            destinations:\n"
            + _destination_initdata_yaml(services, "              ")
        )
    else:
        # No new destinations to create — just bind the destination service so
        # the approuter can look up existing cockpit destinations (e.g. EvokeSD4).
        dest_config = ""

    out = (
        "resources:\n"
        f"  - name: {xsuaa}\n"
        "    type: org.cloudfoundry.managed-service\n"
        "    parameters:\n"
        "      service: xsuaa\n"
        "      service-plan: application\n"
        "      path: ./xs-security.json\n"
        f"  - name: {dest}\n"
        "    type: org.cloudfoundry.managed-service\n"
        "    parameters:\n"
        "      service: destination\n"
        "      service-plan: lite\n"
        + dest_config
    )
    if any(s.on_premise for s in services):
        out += (
            f"  - name: {conn}\n"
            "    type: org.cloudfoundry.managed-service\n"
            "    parameters:\n"
            "      service: connectivity\n"
            "      service-plan: lite\n"
        )
    return out


def mta_extension(app_id: str, services: List[ODataService],
                  creds: Dict[str, Dict[str, str]]) -> str:
    """Generate a deploy-time MTA extension (.mtaext) that fills in the real
    destination values for NEW destinations only. `creds` maps service.id ->
    {field: value}. Existing-destination services are skipped — they need no
    parameters, since the destination is already fully configured.

    IMPORTANT: if no service needs a new destination (e.g. every service uses
    an existing one like "EvokeSD4"), the `parameters:` key is omitted
    entirely rather than left empty. An empty `parameters:` key parses as
    YAML null, and `cf deploy` rejects that with 'Null value for key
    "parameters"' — a bare header with no key is valid, an empty-valued key
    is not.

    This file is written only to the temp build dir at deploy time and is
    NEVER committed — it carries live secrets.
    """
    header = [
        "_schema-version: '3.3.0'",
        f"ID: {_slug(app_id)}.ext",
        f"extends: {app_id}",
    ]
    param_lines = []
    for s in services:
        if not s.needs_own_destination():
            continue
        c = creds.get(s.id, {})
        p = _env_prefix(s.id).lower()
        param_lines.append(f"  {p}_url: {c.get('url', MTA_PARAM_DEFAULT)}")
        if s.auth_type == AUTH_BASIC:
            param_lines.append(f"  {p}_user: {c.get('user', MTA_PARAM_DEFAULT)}")
            param_lines.append(f"  {p}_password: {c.get('password', MTA_PARAM_DEFAULT)}")
        elif s.auth_type == AUTH_OAUTH_CC:
            param_lines.append(f"  {p}_tokenurl: {c.get('token_url', MTA_PARAM_DEFAULT)}")
            param_lines.append(f"  {p}_clientid: {c.get('client_id', MTA_PARAM_DEFAULT)}")
            param_lines.append(f"  {p}_clientsecret: {c.get('client_secret', MTA_PARAM_DEFAULT)}")

    if param_lines:
        body = header + ["parameters:"] + param_lines
    else:
        # No new destinations to parameterize (e.g. all services reuse an
        # existing destination). Omit 'parameters:' entirely — MTA rejects
        # an empty-valued key ("Null value for key \"parameters\"").
        body = header
    return "\n".join(body) + "\n"


def xs_security_json(app_id: str) -> str:
    """Minimal XSUAA security descriptor (xs-security.json)."""
    xsappname = _slug(app_id)
    return json.dumps({
        "xsappname": xsappname,
        "tenant-mode": "dedicated",
        "scopes": [{"name": f"$XSAPPNAME.Viewer",
                    "description": "View app data"}],
        "role-templates": [{"name": "Viewer",
                            "description": "View app data",
                            "scope-references": [f"$XSAPPNAME.Viewer"]}],
        # XSUAA only returns the login to whitelisted callback URLs; without
        # this the approuter's /login/callback is rejected with "OpenID provider
        # cannot process the request due to configuration issues".
        "oauth2-configuration": {
            "redirect-uris": [
                "https://*.hana.ondemand.com/**",   # CF app routes (all regions)
                "https://*.cloud.sap/**",           # custom/BAS domains
                "http://localhost:*/**",
            ],
        },
    }, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# Bridge: build the service list from the legacy single-service AppSpec fields,
# so existing code keeps working while you migrate the UI to multi-service.
# ─────────────────────────────────────────────────────────────────────────────
def services_from_legacy_spec(spec) -> List[ODataService]:
    """Derive a one- or two-element service list from an old-style AppSpec.

    Falls through gracefully if the spec already has `.services`.
    """
    existing = getattr(spec, "services", None)
    if existing:
        return existing

    primary = ODataService(
        id="mainService",
        url=getattr(spec, "odata_url", "") or "",
        entity_set=getattr(spec, "entity_set", "Items"),
        key_field=getattr(spec, "key_field", "Id"),
        title_field=getattr(spec, "title_field", "Title"),
        fields=list(getattr(spec, "fields", []) or []),
        auth_type=getattr(spec, "auth_type", AUTH_NONE),
        is_primary=True,
    )
    services = [primary]

    items_set = (getattr(spec, "items_entity_set", "") or "").strip()
    if items_set:
        services.append(ODataService(
            id="itemsService",
            url=getattr(spec, "odata_url", "") or "",   # often same service
            entity_set=items_set,
            key_field=getattr(spec, "items_key_field", "ItemId"),
            title_field=getattr(spec, "items_title_field", "Product"),
            fields=list(getattr(spec, "items_fields", []) or []),
            auth_type=getattr(spec, "auth_type", AUTH_NONE),
        ))
    return services