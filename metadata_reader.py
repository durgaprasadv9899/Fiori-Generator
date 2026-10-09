"""
metadata_reader.py — Module 1: OData Connector / $metadata fetch.

Fetches the raw EDMX ($metadata) XML for an OData service at GENERATION time.
This is deliberately separate from how the DEPLOYED app reaches the backend:

  - The deployed app always goes browser -> AppRouter -> BTP destination ->
    (Cloud Connector) -> Gateway. That path does not exist yet while we are
    still generating the app, so we cannot use it to read metadata.

  - At generation time we therefore need our own way to reach $metadata. Two
    modes are supported (user picks per run):

      MODE "direct"      : GET {url}$metadata with optional Basic auth, straight
                           from the Streamlit backend. Works for any gateway the
                           machine running Streamlit can reach (cloud gateways,
                           trials, anything with a public/VPN route).

      MODE "destination" : Resolve the backend through an existing BTP
                           destination (e.g. an on-prem S/4 system like
                           "EvokeSD4") by reading the destination's config via a
                           destination-service service key, then calling the
                           destination's /destination-configuration API to get a
                           short-lived token + URL, and finally fetching
                           $metadata. This reuses the same Cloud-Connector path
                           the deployed app will use, so on-prem systems work
                           without exposing them to the Streamlit host directly.

Both modes return the same thing: the raw EDMX XML string (to be handed to
metadata_parser.py). Network/credential errors are raised as MetadataError with
a human-readable message.
"""

from __future__ import annotations
import json
import re
import subprocess
import urllib.parse
from dataclasses import dataclass
from typing import Optional

import requests


class MetadataError(Exception):
    """Raised when $metadata cannot be fetched or is not valid."""


def _metadata_url(service_url: str) -> str:
    """Append $metadata to a service root, preserving any query string."""
    u = service_url.strip()
    # Split off an existing query (e.g. ?sap-client=100) to re-attach after.
    parsed = urllib.parse.urlsplit(u)
    path = parsed.path
    if not path.endswith("/"):
        path = path + "/"
    path = path + "$metadata"
    rebuilt = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, path, parsed.query, "")
    )
    return rebuilt


# ─────────────────────────────────────────────────────────────────────────────
# MODE "direct" — fetch straight from the Streamlit backend
# ─────────────────────────────────────────────────────────────────────────────
def fetch_direct(service_url: str,
                 user: Optional[str] = None,
                 password: Optional[str] = None,
                 verify_ssl: bool = True,
                 timeout: int = 30) -> str:
    """GET {service_url}$metadata directly, with optional HTTP Basic auth."""
    su = (service_url or "").strip()
    if not su:
        raise MetadataError(
            "No service URL provided. In Direct URL mode, enter the full "
            "absolute service URL including host, e.g. "
            "https://my-gateway.example.com/sap/opu/odata/sap/API_SALES_ORDER_SRV/"
        )
    if not re.match(r"^https?://", su, re.IGNORECASE):
        raise MetadataError(
            f"Direct URL mode needs an ABSOLUTE URL with a host, but got a "
            f"relative path: '{su}'. The Streamlit host can't fetch a bare "
            f"path — prefix it with the gateway host, e.g. "
            f"https://my-gateway.example.com{su if su.startswith('/') else '/' + su}"
            f"  (A relative path like this is correct for the DEPLOYED app's "
            f"manifest, but not for reading metadata here. For an on-prem "
            f"system, use 'Via BTP destination' or 'Paste metadata XML'.)"
        )
    url = _metadata_url(su)
    auth = (user, password) if user else None
    try:
        resp = requests.get(
            url,
            auth=auth,
            headers={"Accept": "application/xml"},
            verify=verify_ssl,
            timeout=timeout,
        )
    except requests.exceptions.SSLError as e:
        raise MetadataError(
            f"SSL error fetching {url}. If this is a trusted internal gateway "
            f"with a self-signed cert, disable SSL verification for this run. ({e})"
        )
    except requests.exceptions.RequestException as e:
        raise MetadataError(f"Could not reach {url}: {e}")

    if resp.status_code == 401:
        raise MetadataError(
            f"401 Unauthorized fetching $metadata from {url}. "
            f"Check the username/password."
        )
    if resp.status_code == 404:
        raise MetadataError(
            f"404 Not Found at {url}. Check the service URL — it should be the "
            f"service root, e.g. /sap/opu/odata/sap/ZSALES_ORDER_SRV/"
        )
    if resp.status_code >= 400:
        raise MetadataError(
            f"HTTP {resp.status_code} fetching $metadata from {url}: "
            f"{resp.text[:300]}"
        )
    if "edmx" not in resp.text.lower():
        raise MetadataError(
            f"Response from {url} does not look like OData $metadata (no EDMX). "
            f"First 200 chars: {resp.text[:200]}"
        )
    return resp.text


# ─────────────────────────────────────────────────────────────────────────────
# MODE "destination" — resolve through a BTP destination service key
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class DestinationServiceKey:
    """The subset of a destination-service service key we need."""
    uri: str            # destination service base, e.g. https://destination-configuration...
    token_url: str      # uaa.url + /oauth/token
    clientid: str
    clientsecret: str

    @classmethod
    def from_json(cls, data: dict) -> "DestinationServiceKey":
        try:
            uaa = data["uaa"]
            return cls(
                uri=data["uri"].rstrip("/"),
                token_url=uaa["url"].rstrip("/") + "/oauth/token",
                clientid=uaa["clientid"],
                clientsecret=uaa["clientsecret"],
            )
        except KeyError as e:
            raise MetadataError(
                f"Destination service key is missing expected field {e}. "
                f"Provide the JSON from `cf create-service-key <dest-instance> <key>` "
                f"then `cf service-key <dest-instance> <key>`."
            )


def read_service_key_via_cli(dest_instance: str, key_name: str = "genkey") -> dict:
    """Create (if needed) and read a destination-service service key via cf CLI.

    Requires the user to already be `cf login`'d and targeting the right space
    (the app enforces this before calling). Returns the parsed key JSON.
    """
    # Create the key if it doesn't exist (idempotent-ish: ignore "already exists").
    subprocess.run(
        ["cf", "create-service-key", dest_instance, key_name],
        capture_output=True, text=True,
    )
    r = subprocess.run(
        ["cf", "service-key", dest_instance, key_name],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise MetadataError(
            f"Could not read service key '{key_name}' for '{dest_instance}': "
            f"{r.stderr or r.stdout}. Is the destination service instance name correct, "
            f"and are you `cf login`'d to the right space?"
        )
    # cf prints a preamble line before the JSON body; find the first '{'.
    out = r.stdout
    brace = out.find("{")
    if brace == -1:
        raise MetadataError(f"Unexpected `cf service-key` output: {out[:200]}")
    try:
        return json.loads(out[brace:])
    except json.JSONDecodeError as e:
        raise MetadataError(f"Could not parse service key JSON: {e}")


def _dest_token(key: DestinationServiceKey, timeout: int = 30) -> str:
    """OAuth2 client-credentials token for the destination service."""
    try:
        resp = requests.post(
            key.token_url,
            data={"grant_type": "client_credentials",
                  "client_id": key.clientid,
                  "client_secret": key.clientsecret},
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
    except requests.exceptions.RequestException as e:
        raise MetadataError(f"Could not get destination-service token: {e}")
    if resp.status_code >= 400:
        raise MetadataError(
            f"Destination-service token request failed (HTTP {resp.status_code}): "
            f"{resp.text[:200]}"
        )
    return resp.json()["access_token"]


def resolve_destination(key: DestinationServiceKey, dest_name: str,
                        timeout: int = 30) -> dict:
    """Call /destination-configuration/v1/destinations/{name} to get the
    destination's URL, auth token, and any proxy info."""
    token = _dest_token(key, timeout)
    url = f"{key.uri}/destination-configuration/v1/destinations/{dest_name}"
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
    except requests.exceptions.RequestException as e:
        raise MetadataError(f"Could not resolve destination '{dest_name}': {e}")
    if resp.status_code == 404:
        raise MetadataError(
            f"Destination '{dest_name}' not found via the destination service. "
            f"Confirm it exists at subaccount level in the BTP cockpit."
        )
    if resp.status_code >= 400:
        raise MetadataError(
            f"Resolving destination '{dest_name}' failed (HTTP {resp.status_code}): "
            f"{resp.text[:200]}"
        )
    return resp.json()


def fetch_via_destination(dest_instance: str, dest_name: str,
                          service_path: str,
                          key_name: str = "genkey",
                          verify_ssl: bool = True,
                          timeout: int = 30) -> str:
    """Full destination-mode fetch.

    dest_instance : name of the BTP *destination service instance* bound in this
                    space (the thing `cf services` lists), NOT the destination.
    dest_name     : the destination entry name, e.g. "EvokeSD4".
    service_path  : the OData service path to append, e.g.
                    /sap/opu/odata/sap/ZSALES_ORDER_SRV/

    NOTE: for on-prem destinations reached via Cloud Connector, the Streamlit
    host itself usually CANNOT open that tunnel — the destination service returns
    the on-prem URL, but only the approuter/connectivity proxy can actually reach
    it. So destination mode reliably yields metadata only when the destination's
    URL is reachable from where Streamlit runs (cloud destinations, or on-prem
    exposed through a reachable proxy). The app surfaces this clearly and lets the
    user fall back to pasting metadata manually.
    """
    key_json = read_service_key_via_cli(dest_instance, key_name)
    key = DestinationServiceKey.from_json(key_json)
    dest = resolve_destination(key, dest_name, timeout)

    owner = dest.get("destinationConfiguration", dest)
    base_url = owner.get("URL", "").rstrip("/")
    if not base_url:
        raise MetadataError(
            f"Destination '{dest_name}' has no URL in its configuration."
        )

    full = base_url + "/" + service_path.strip("/") + "/"
    md_url = _metadata_url(full)

    # Auth: the destination service may return an authTokens array (for OAuth/
    # principal propagation). For BasicAuthentication the token isn't returned;
    # fall back to User/Password fields on the destination config if present.
    headers = {"Accept": "application/xml"}
    auth = None
    tokens = dest.get("authTokens") or []
    if tokens and tokens[0].get("value"):
        headers["Authorization"] = f"{tokens[0].get('type','Bearer')} {tokens[0]['value']}"
    elif owner.get("User"):
        auth = (owner.get("User"), owner.get("Password"))

    try:
        resp = requests.get(md_url, headers=headers, auth=auth,
                            verify=verify_ssl, timeout=timeout)
    except requests.exceptions.RequestException as e:
        raise MetadataError(
            f"Resolved destination '{dest_name}' to {base_url}, but could not "
            f"fetch {md_url} from the Streamlit host: {e}. If this is an on-prem "
            f"system behind Cloud Connector, the generation host may not be able "
            f"to reach it directly — paste the metadata manually instead."
        )
    if resp.status_code >= 400:
        raise MetadataError(
            f"HTTP {resp.status_code} fetching $metadata via destination "
            f"'{dest_name}': {resp.text[:200]}"
        )
    if "edmx" not in resp.text.lower():
        raise MetadataError(
            f"Response via destination '{dest_name}' is not EDMX metadata."
        )
    return resp.text


# ─────────────────────────────────────────────────────────────────────────────
# MODE "manual" — user pastes the EDMX (always-works fallback)
# ─────────────────────────────────────────────────────────────────────────────
def load_manual(edmx_text: str) -> str:
    """Validate pasted EDMX text and return it unchanged."""
    if not edmx_text or "edmx" not in edmx_text.lower():
        raise MetadataError("Pasted text does not look like OData $metadata (no EDMX).")
    return edmx_text
