"""
btp_discovery.py — browse BTP systems and the OData services behind them.

Replaces "type the service URL, user, password, SSL flag" with three picks:

    1. a destination service instance  (any one sees all subaccount destinations)
    2. a destination / backend system   (e.g. "EvokeSD4")
    3. an OData service from that system's Gateway catalog

and then fetches the chosen service's $metadata through the same destination.

How the backend is reached depends on where the generator runs (see
runtime_environment()):

  "bas"          : SAP Business Application Studio. BAS exposes every
                   destination as http://<name>.dest/... through its built-in
                   proxy, which handles auth AND Cloud Connector — so on-premise
                   systems work too. Destination must have WebIDEEnabled=true.
  "connectivity" : running on Cloud Foundry with a `connectivity` service bound
                   (VCAP_SERVICES). On-premise requests go through the
                   connectivity proxy; Internet ones go direct.
  "direct"       : anywhere else (e.g. a laptop). Internet destinations are
                   called directly with the auth token the destination service
                   returns. On-premise destinations are NOT reachable — the
                   caller is told so and can paste the metadata instead.

Destination-service credentials come from VCAP_SERVICES when a `destination`
service is bound, otherwise from a service key read with the logged-in cf CLI
(created if missing — same mechanism as metadata_reader's destination mode).

Pure Python + requests; no Streamlit. Errors are DiscoveryError with a
human-readable message.
"""

from __future__ import annotations
import json
import os
import subprocess
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import requests

from metadata_reader import (MetadataError, DestinationServiceKey,
                             read_service_key_via_cli)


class DiscoveryError(MetadataError):
    """Raised when a discovery step (cf, destination service, catalog) fails."""


# Dedicated instance for the generator, so it never needs service keys on
# instances that belong to other apps in a shared space.
DEFAULT_INSTANCE = "fiori-generator-destination"
DEFAULT_KEY = "genkey"

V2_CATALOG = "/sap/opu/odata/IWFND/CATALOGSERVICE;v=2/ServiceCollection"
V4_CATALOG = "/sap/opu/odata4/iwfnd/config/default/iwfnd/catalog/0002/ServiceGroups"

# Destination fields safe to keep in memory / show in the UI. Everything else
# (Password, clientSecret, tokens, ...) is dropped when listing.
_PUBLIC_FIELDS = ("Name", "Type", "URL", "ProxyType", "Authentication",
                  "Description", "CloudConnectorLocationId", "sap-client",
                  "WebIDEEnabled", "WebIDEUsage")


# ─────────────────────────────────────────────────────────────────────────────
# Runtime environment
# ─────────────────────────────────────────────────────────────────────────────
def _vcap(label: str) -> Optional[Dict]:
    """Credentials of the first bound service with this label, or None."""
    raw = os.environ.get("VCAP_SERVICES")
    if not raw:
        return None
    try:
        bound = json.loads(raw).get(label) or []
    except ValueError:
        return None
    return bound[0].get("credentials") if bound else None


def runtime_environment() -> str:
    """'bas' | 'connectivity' | 'direct'. Override with FIORI_GEN_TRANSPORT."""
    forced = os.environ.get("FIORI_GEN_TRANSPORT", "").strip().lower()
    if forced in ("bas", "connectivity", "direct"):
        return forced
    if _vcap("connectivity"):
        return "connectivity"
    if os.environ.get("WS_BASE_URL") or os.environ.get("H2O_URL"):
        return "bas"
    return "direct"


def describe_environment(env: str) -> str:
    return {
        "bas": "Running in SAP Business Application Studio — every destination, "
               "including on-premise ones, is reached through the BAS proxy.",
        "connectivity": "Running on Cloud Foundry with the connectivity service "
                        "bound — on-premise destinations go through Cloud Connector.",
        "direct": "Running locally — Internet destinations work directly. "
                  "On-premise destinations (Cloud Connector) can't be reached "
                  "from this machine; run the generator in BAS for those, or "
                  "use 'Paste metadata XML'.",
    }.get(env, env)


def reachable(proxy_type: str, env: str) -> bool:
    """Can a destination with this ProxyType be called from this environment?"""
    return env != "direct" or (proxy_type or "Internet").lower() != "onpremise"


# ─────────────────────────────────────────────────────────────────────────────
# cf CLI helpers
# ─────────────────────────────────────────────────────────────────────────────
def _cf(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["cf", *args], capture_output=True, text=True,
                              timeout=timeout)
    except FileNotFoundError:
        raise DiscoveryError("The cf CLI is not installed or not on PATH.")
    except subprocess.TimeoutExpired:
        raise DiscoveryError(f"`cf {' '.join(args[:2])}` timed out.")


def _cf_curl(path: str) -> Dict:
    r = _cf("curl", path)
    try:
        data = json.loads(r.stdout or "{}")
    except ValueError:
        raise DiscoveryError(f"Unexpected `cf curl {path}` output: "
                             f"{(r.stdout or r.stderr)[:200]}")
    if r.returncode != 0 or data.get("errors"):
        detail = (data.get("errors") or [{}])[0].get("detail") or r.stderr
        raise DiscoveryError(f"`cf curl {path}` failed: {detail}")
    return data


def cf_target() -> Dict[str, str]:
    """{'api endpoint','user','org','space',...} for the current cf session, or
    {} when not logged in / nothing targeted."""
    try:
        r = _cf("target", timeout=30)
    except DiscoveryError:
        return {}
    if r.returncode != 0:
        return {}
    info = {}
    for line in r.stdout.splitlines():
        k, sep, v = line.partition(":")
        if sep:
            info[k.strip().lower()] = v.strip()
    return info if info.get("org") and info.get("space") else {}


def list_destination_instances() -> List[str]:
    """Names of destination-service instances in the targeted space."""
    tgt = cf_target()
    if not tgt:
        raise DiscoveryError("Not logged in to Cloud Foundry. Run "
                             "`cf login -a <api> --sso` and select your space.")
    r = _cf("space", tgt["space"], "--guid")
    if r.returncode != 0 or not r.stdout.strip():
        raise DiscoveryError(f"Could not resolve space '{tgt['space']}': {r.stderr or r.stdout}")
    space_guid = r.stdout.strip().splitlines()[-1].strip()

    offerings = _cf_curl("/v3/service_offerings?names=destination")["resources"]
    if not offerings:
        return []
    plans = _cf_curl("/v3/service_plans?per_page=200&service_offering_guids="
                     + ",".join(o["guid"] for o in offerings))["resources"]
    if not plans:
        return []
    insts = _cf_curl("/v3/service_instances?per_page=200&space_guids=" + space_guid
                     + "&service_plan_guids=" + ",".join(p["guid"] for p in plans))
    return sorted(i["name"] for i in insts["resources"])


def create_destination_instance(name: str = DEFAULT_INSTANCE) -> None:
    """Create a destination service instance (lite plan) and wait for it."""
    r = _cf("create-service", "destination", "lite", name, "--wait", timeout=300)
    if r.returncode != 0 and "already exists" not in (r.stdout + r.stderr).lower():
        raise DiscoveryError(f"Could not create '{name}': {(r.stderr or r.stdout)[-300:]}")


# ─────────────────────────────────────────────────────────────────────────────
# Destination service client
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class DestinationClient:
    key: DestinationServiceKey
    source: str = ""                  # human description of where creds came from
    timeout: int = 30
    _token: str = field(default="", repr=False)
    _token_exp: float = field(default=0.0, repr=False)

    @staticmethod
    def _key(creds: Dict) -> DestinationServiceKey:
        """Credentials in either layout: current flat keys/bindings
        (clientid/clientsecret/url/uri at top level) or the older nested
        {"uaa": {...}, "uri": ...}. cf v8 additionally wraps service keys in
        {"credentials": {...}}."""
        creds = creds.get("credentials", creds)
        uaa = creds.get("uaa") or creds
        try:
            return DestinationServiceKey(
                uri=creds["uri"].rstrip("/"),
                token_url=uaa["url"].rstrip("/") + "/oauth/token",
                clientid=uaa["clientid"], clientsecret=uaa["clientsecret"])
        except KeyError as e:
            raise DiscoveryError(f"Destination service credentials lack field {e}.")

    @classmethod
    def from_vcap(cls) -> Optional["DestinationClient"]:
        creds = _vcap("destination")
        if not creds:
            return None
        return cls(cls._key(creds), source="bound destination service")

    @classmethod
    def from_cf(cls, instance: str, key_name: str = DEFAULT_KEY) -> "DestinationClient":
        try:
            key_json = read_service_key_via_cli(instance, key_name)
        except MetadataError as e:
            raise DiscoveryError(str(e))
        return cls(cls._key(key_json), source=f"service key '{key_name}' of '{instance}'")

    def _bearer(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        try:
            r = requests.post(self.key.token_url,
                              data={"grant_type": "client_credentials"},
                              auth=(self.key.clientid, self.key.clientsecret),
                              timeout=self.timeout)
        except requests.exceptions.RequestException as e:
            raise DiscoveryError(f"Could not get a destination-service token: {e}")
        if r.status_code >= 400:
            raise DiscoveryError(f"Destination-service token request failed "
                                 f"(HTTP {r.status_code}): {r.text[:200]}")
        body = r.json()
        self._token = body["access_token"]
        self._token_exp = time.time() + float(body.get("expires_in", 3600))
        return self._token

    def _get(self, path: str) -> requests.Response:
        try:
            return requests.get(self.key.uri + path,
                                headers={"Authorization": "Bearer " + self._bearer()},
                                timeout=self.timeout)
        except requests.exceptions.RequestException as e:
            raise DiscoveryError(f"Destination service unreachable: {e}")

    def list_destinations(self) -> List[Dict]:
        """HTTP destinations visible to this instance (subaccount + instance
        level), with secrets stripped. Sorted by name."""
        found: Dict[str, Dict] = {}
        for level in ("subaccountDestinations", "instanceDestinations"):
            r = self._get("/destination-configuration/v1/" + level)
            if r.status_code >= 400:
                raise DiscoveryError(f"Listing {level} failed (HTTP {r.status_code}): "
                                     f"{r.text[:200]}")
            for d in r.json() or []:
                if (d.get("Type") or "HTTP").upper() != "HTTP" or not d.get("Name"):
                    continue
                pub = {k: d[k] for k in _PUBLIC_FIELDS if k in d}
                pub["level"] = "subaccount" if level.startswith("sub") else "instance"
                found.setdefault(d["Name"], pub)
        return sorted(found.values(), key=lambda d: d["Name"].lower())

    def find(self, name: str) -> Dict:
        """Find-destination API: config + ready-to-use authTokens."""
        r = self._get("/destination-configuration/v1/destinations/"
                      + urllib.parse.quote(name, safe=""))
        if r.status_code == 404:
            raise DiscoveryError(f"Destination '{name}' not found.")
        if r.status_code >= 400:
            raise DiscoveryError(f"Reading destination '{name}' failed "
                                 f"(HTTP {r.status_code}): {r.text[:200]}")
        return r.json()


def destination_client(instance: Optional[str] = None,
                       key_name: str = DEFAULT_KEY) -> DestinationClient:
    """Bound destination service if present, else a service key via cf CLI."""
    if not instance:
        client = DestinationClient.from_vcap()
        if client:
            return client
        raise DiscoveryError("Choose a destination service instance.")
    return DestinationClient.from_cf(instance, key_name)


# ─────────────────────────────────────────────────────────────────────────────
# Backend (one destination) — knows how to send a request in this environment
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Backend:
    name: str
    base_url: str
    proxy_type: str = "Internet"
    authentication: str = ""
    transport: str = "direct"
    sap_client: str = ""
    headers: Dict[str, str] = field(default_factory=dict, repr=False)
    proxies: Optional[Dict[str, str]] = field(default=None, repr=False)
    timeout: Tuple[int, int] = (10, 90)        # (connect, read) seconds

    def get(self, path: str, params: Optional[Dict] = None,
            accept: str = "application/json") -> requests.Response:
        params = dict(params or {})
        if self.sap_client:
            params.setdefault("sap-client", self.sap_client)
        url = self.base_url.rstrip("/") + "/" + path.lstrip("/")
        try:
            return requests.get(url, params=params, proxies=self.proxies,
                                headers={**self.headers, "Accept": accept},
                                timeout=self.timeout)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            host = urllib.parse.urlsplit(url).netloc
            kind = "timed out" if isinstance(e, requests.exceptions.Timeout) else "refused/failed"
            raise HostUnreachable(f"Connection to {host} {kind} — the system behind "
                                  f"'{self.name}' is down or not reachable from here.")
        except requests.exceptions.RequestException as e:
            raise DiscoveryError(f"Could not reach '{self.name}' ({self.transport}): {e}")


class HostUnreachable(DiscoveryError):
    """The backend host itself could not be contacted (no point retrying)."""


def _auth_header(found: Dict, name: str) -> Dict[str, str]:
    tokens = found.get("authTokens") or []
    for t in tokens:
        if t.get("error"):
            raise DiscoveryError(f"Destination '{name}' could not issue a token: "
                                 f"{t['error']}")
        hdr = t.get("http_header") or {}
        if hdr.get("value"):
            return {hdr.get("key", "Authorization"): hdr["value"]}
        if t.get("value"):
            return {"Authorization": f"{t.get('type', 'Bearer')} {t['value']}"}
    return {}


def _connectivity_proxy(location_id: str) -> Tuple[Dict[str, str], Dict[str, str]]:
    """(proxies, headers) for the bound connectivity service."""
    c = _vcap("connectivity")
    if not c:
        raise DiscoveryError("No connectivity service is bound to this app.")
    token_url = (c.get("token_service_url") or c["url"]).rstrip("/")
    if not token_url.endswith("/oauth/token"):
        token_url += "/oauth/token"
    try:
        r = requests.post(token_url, data={"grant_type": "client_credentials"},
                          auth=(c["clientid"], c["clientsecret"]), timeout=30)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise DiscoveryError(f"Connectivity-service token request failed: {e}")
    port = c.get("onpremise_proxy_http_port") or c.get("onpremise_proxy_port")
    proxy = f"http://{c['onpremise_proxy_host']}:{port}"
    headers = {"Proxy-Authorization": "Bearer " + r.json()["access_token"]}
    if location_id:
        headers["SAP-Connectivity-SCC-Location_ID"] = location_id
    return {"http": proxy, "https": proxy}, headers


def open_backend(client: DestinationClient, dest_name: str,
                 env: Optional[str] = None) -> Backend:
    """Resolve a destination into a Backend that can call it from here."""
    env = env or runtime_environment()
    found = client.find(dest_name)
    cfg = found.get("destinationConfiguration") or {}
    url = (cfg.get("URL") or "").rstrip("/")
    proxy_type = cfg.get("ProxyType") or "Internet"
    on_prem = proxy_type.lower() == "onpremise"
    common = dict(name=dest_name, proxy_type=proxy_type,
                  authentication=cfg.get("Authentication", ""),
                  sap_client=cfg.get("sap-client", ""))

    if env == "bas":
        # BAS proxy injects auth + tunnels to on-prem; requests picks up the
        # dev space's HTTP_PROXY from the environment.
        return Backend(base_url=f"http://{dest_name}.dest", transport="bas", **common)
    if not url:
        raise DiscoveryError(f"Destination '{dest_name}' has no URL.")
    if on_prem and env == "connectivity":
        proxies, headers = _connectivity_proxy(cfg.get("CloudConnectorLocationId", ""))
        headers.update(_auth_header(found, dest_name))
        return Backend(base_url=url, transport="connectivity", headers=headers,
                       proxies=proxies, **common)
    if on_prem:
        raise DiscoveryError(
            f"'{dest_name}' is an on-premise system (Cloud Connector) and can't be "
            f"reached from this machine. Run the generator in SAP Business "
            f"Application Studio, or open its $metadata in a browser and use "
            f"'Paste metadata XML'.")
    return Backend(base_url=url, transport="direct",
                   headers=_auth_header(found, dest_name), **common)


# ─────────────────────────────────────────────────────────────────────────────
# Gateway catalog + metadata
# ─────────────────────────────────────────────────────────────────────────────
def _service_path(service_url: str, base: str) -> str:
    """Path of a catalog ServiceUrl (absolute, root-relative or relative),
    without query, with a trailing slash."""
    u = urllib.parse.urljoin(base, (service_url or "").strip())
    path = urllib.parse.urlsplit(u).path
    return path if path.endswith("/") else path + "/"


def _check(r: requests.Response, what: str) -> None:
    if r.status_code in (401, 403):
        raise DiscoveryError(f"{what}: HTTP {r.status_code} — the destination's user "
                             f"lacks access (Gateway role /IWFND/RT_GW_USER).")
    if r.status_code >= 400:
        raise DiscoveryError(f"{what}: HTTP {r.status_code} {r.text[:150]}")


def list_services(backend: Backend) -> Tuple[List[Dict], List[str]]:
    """OData V2 + V4 services published on the backend's Gateway.

    Returns (services, warnings). Each service:
      {"name", "title", "version", "odata_version", "path"}
    Raises DiscoveryError only when neither catalog yields anything.
    """
    services: List[Dict] = []
    warnings: List[str] = []

    try:
        r = backend.get(V2_CATALOG, {
            "$format": "json",
            "$select": "TechnicalServiceName,TechnicalServiceVersion,Title,Description,ServiceUrl"})
        _check(r, "V2 catalog")
        for s in r.json()["d"]["results"]:
            name = s.get("TechnicalServiceName") or ""
            path = (_service_path(s["ServiceUrl"], r.url) if s.get("ServiceUrl")
                    else f"/sap/opu/odata/sap/{name}/")
            services.append({"name": name, "title": s.get("Title") or s.get("Description") or "",
                             "version": str(s.get("TechnicalServiceVersion") or ""),
                             "odata_version": "2.0", "path": path})
    except HostUnreachable:
        raise
    except (DiscoveryError, ValueError, KeyError, TypeError) as e:
        warnings.append(f"V2 catalog unavailable ({e})")

    try:
        r = backend.get(V4_CATALOG, {"$expand": "DefaultSystem($expand=Services)"})
        _check(r, "V4 catalog")
        for g in r.json().get("value", []):
            for s in ((g.get("DefaultSystem") or {}).get("Services") or []):
                if not s.get("ServiceUrl"):
                    continue
                services.append({"name": s.get("ServiceId") or "",
                                 "title": g.get("GroupId") or "",
                                 "version": str(s.get("ServiceVersion") or ""),
                                 "odata_version": "4.0",
                                 "path": _service_path(s["ServiceUrl"], r.url)})
    except (DiscoveryError, ValueError, KeyError, TypeError) as e:
        warnings.append(f"V4 catalog unavailable ({e})")

    if not services:
        raise DiscoveryError("No OData services could be listed from "
                             f"'{backend.name}': " + "; ".join(warnings))
    unique = {s["path"]: s for s in services}
    return sorted(unique.values(), key=lambda s: s["name"].lower()), warnings


def fetch_metadata(backend: Backend, service_path: str) -> str:
    """$metadata of a service on the backend, as EDMX text."""
    path = "/" + service_path.strip().strip("/") + "/$metadata"
    r = backend.get(path, accept="application/xml")
    _check(r, f"$metadata of {service_path}")
    if "edmx" not in r.text[:2000].lower():
        raise DiscoveryError(f"{service_path} did not return EDMX metadata "
                             f"(first chars: {r.text[:120]!r}).")
    return r.text
