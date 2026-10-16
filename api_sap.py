"""
api_sap.py — "Connect to SAP" for the studio API: BTP destinations, Gateway catalog, $metadata,
multi-service compatibility. Same engine calls as the classic studio (app.py _connect_btp /
_connect_manual / _add_extra_service), without Streamlit.
"""

from __future__ import annotations

import os
import threading
from typing import Dict, List, Optional

import btp_discovery as bd

_LOCK = threading.Lock()
_CLIENT: Dict[str, object] = {}          # one destination client per process (token cached inside)
_BACKENDS: Dict[str, object] = {}


class SapError(Exception):
    """User-facing problem (bad input, unreachable system) — the API answers 400/502 with it."""


def runtime() -> Dict:
    env = bd.runtime_environment()
    tgt = None
    if env != "connectivity":
        t = bd.cf_target()
        tgt = {"org": t.get("org", ""), "space": t.get("space", "")} if t else None
    return {"env": {"direct": "local"}.get(env, env), "description": bd.describe_environment(env), "cf_target": tgt}


def _client():
    with _LOCK:
        if "c" in _CLIENT:
            return _CLIENT["c"], _CLIENT["source"]
        env = bd.runtime_environment()
        client = bd.DestinationClient.from_vcap() if env == "connectivity" else None
        if client:
            source = "bound destination service"
        else:
            if not bd.cf_target():
                raise SapError("Not signed in to Cloud Foundry on the studio host — run `cf login`, or paste the "
                               "service $metadata instead.")
            insts = bd.list_destination_instances()
            if not insts:
                raise SapError("No destination service instance in the targeted CF space.")
            # the studio's own instance first; never pick some other team's instance silently
            prefs = [os.environ.get("STUDIO_DESTINATION_INSTANCE", ""), bd.DEFAULT_INSTANCE, "evokesd4-destination-service"]
            inst = next((i for i in prefs if i and i in insts), "")
            if not inst:
                raise SapError("No destination service instance chosen for the studio — set STUDIO_DESTINATION_INSTANCE "
                               f"(available: {', '.join(insts[:8])}).")
            client = bd.destination_client(inst)
            source = f"destination service instance '{inst}'"
        _CLIENT.update(c=client, source=source)
        return client, source


def destinations() -> Dict:
    env = bd.runtime_environment()
    try:
        client, source = _client()
        dests = client.list_destinations()
    except bd.DiscoveryError as e:
        raise SapError(str(e))
    out = [{"name": d.get("Name", ""), "url": d.get("URL", ""), "proxy_type": d.get("ProxyType", "Internet"),
            "authentication": d.get("Authentication", ""), "sap_client": d.get("sap-client", ""),
            "reachable": bd.reachable(d.get("ProxyType", ""), env)} for d in dests]
    return {"destinations": sorted(out, key=lambda d: d["name"].lower()), "source": source}


def _backend(dest: str):
    with _LOCK:
        if dest in _BACKENDS:
            return _BACKENDS[dest]
    client, _ = _client()
    try:
        be = bd.open_backend(client, dest, bd.runtime_environment())
    except bd.DiscoveryError as e:
        raise SapError(str(e))
    with _LOCK:
        _BACKENDS[dest] = be
    return be


def services(dest: str) -> Dict:
    if not dest:
        raise SapError("Choose a destination.")
    try:
        svcs, warns = bd.list_services(_backend(dest))
    except bd.DiscoveryError as e:
        raise SapError(str(e))
    return {"services": [{"path": s.get("path", ""), "name": s.get("name", ""), "title": s.get("title", ""),
                          "odata_version": s.get("odata_version", "")} for s in svcs], "warnings": warns}


def summary(model: Dict) -> Dict:
    sets = []
    for es in model["entity_sets"]:
        sets.append({"name": es["name"], "label": es.get("label", ""), "keys": es.get("keys", []),
                     "field_count": len(es.get("properties", [])),
                     "fields": [{"name": p["name"], "label": p.get("label", ""), "type": p.get("type", "")}
                                for p in es.get("properties", [])][:80],
                     "creatable": bool(es.get("creatable")), "updatable": bool(es.get("updatable")),
                     "deletable": bool(es.get("deletable")), "service": es.get("service", ""),
                     "navigations": [{"name": n["name"], "target": n.get("target_type", ""), "to_many": n.get("to_many")}
                                     for n in es.get("navigations", [])]})
    ops = [{"name": o["name"], "kind": o.get("kind", ""), "bound_type": o.get("bound_type", ""),
            "label": o.get("label", ""), "service": o.get("service", "")} for o in model.get("operations", [])]
    return {"odata_version": model["odata_version"], "entity_sets": sets, "operations": ops}


def metadata(body: Dict) -> Dict:
    """Fetch $metadata through a destination, or take pasted EDMX. Returns {edmx, service_url, summary}."""
    import app_builder as ab
    import metadata_parser as mdp
    service_url = (body.get("service_url") or body.get("path") or "").strip()
    edmx = (body.get("edmx") or "").strip()
    if not edmx:
        dest, path = (body.get("destination") or "").strip(), (body.get("path") or "").strip()
        if not (dest and path):
            raise SapError("Paste the $metadata XML, or choose a destination and a service.")
        try:
            edmx = bd.fetch_metadata(_backend(dest), path)
        except bd.DiscoveryError as e:
            raise SapError(str(e))
        service_url = service_url or path
    if not service_url:
        raise SapError("Enter the service path the deployed app will call (e.g. /sap/opu/odata/sap/API_SALES_ORDER_SRV/).")
    try:
        model = ab.load_model(edmx)
    except mdp.MetadataParseError as e:
        raise SapError(f"Not valid OData $metadata: {e}")
    except Exception as e:  # noqa: BLE001 — XML errors etc.
        raise SapError(f"The $metadata could not be read: {e}")
    return {"edmx": edmx, "service_url": service_url, "summary": summary(model)}


def check_services(services_: List[Dict]) -> Dict:
    """Can these services form one app? (same OData version, unique paths, merge works)."""
    import app_builder as ab
    if not services_:
        return {"ok": False, "error": "No service.", "merged_summary": None}
    seen = set()
    for s in services_:
        norm = "/" + (s.get("service_url") or "").strip().strip("/")
        if norm == "/":
            return {"ok": False, "error": "Every service needs its service path.", "merged_summary": None}
        if norm in seen:
            return {"ok": False, "error": f"{norm} is added twice.", "merged_summary": None}
        seen.add(norm)
    try:
        model = model_for(services_)
    except SapError as e:
        return {"ok": False, "error": str(e), "merged_summary": None}
    return {"ok": True, "error": "", "merged_summary": summary(model)}


def model_for(services_: List[Dict]) -> Dict:
    """Merged app model of the services (first = main)."""
    import app_builder as ab
    main, extra = services_[0], [dict(s) for s in services_[1:]]
    versions = set()
    for s in services_:
        try:
            versions.add(ab.load_model(s["edmx"])["odata_version"][0])
        except Exception as e:  # noqa: BLE001
            raise SapError(f"{s.get('service_url')}: not valid $metadata ({e})")
    if len(versions) > 1:
        raise SapError("One app combines services of the same OData version — mix of V2 and V4 given.")
    try:
        return ab.load_app_model(main["edmx"], main.get("service_url", ""),
                                 [{"edmx": s["edmx"], "service_url": s["service_url"],
                                   "title": s.get("title") or s["service_url"].rstrip("/").split("/")[-1]}
                                  for s in extra] or None)
    except Exception as e:  # noqa: BLE001
        raise SapError(f"The services cannot be combined: {e}")
