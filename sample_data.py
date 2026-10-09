"""
sample_data.py — Sample Data Inspection (roadmap item #6).

Metadata says `Status: Edm.String` but not that its values are Open / Closed /
Cancelled. Reading a few real records lets the AI generate genuinely smarter UI:
status colours/badges, sensible default filters, value-help candidates, and
which columns are actually populated vs always-empty.

Fetches the top-N rows of an entity set (OData v2: $top=N&$format=json; v4 same)
and derives, per property:
  - distinct low-cardinality values (candidate enum -> badge/semantic states)
  - min/max for numeric/date columns (candidate ranges)
  - fill rate (fraction non-null -> hide always-empty columns)

Reuses the SAME reachability model as metadata: direct URL (optional basic auth)
or pasted-JSON fallback. If the service isn't reachable (e.g. on-prem behind
Cloud Connector, unreachable from the generation host), inspection is skipped
and generation proceeds on metadata + annotations alone. Never raises to the
caller for network issues — returns an empty profile with a `reachable=False`
flag instead.
"""

from __future__ import annotations
from typing import Dict, List, Optional
import json as _json
import urllib.parse

import requests


# Columns with at most this many distinct values are treated as enum-like
# (good candidates for status badges / dropdown filters).
ENUM_MAX_DISTINCT = 12


def _entityset_url(service_url: str, entity_set: str, top: int, odata_v4: bool) -> str:
    base = service_url.strip()
    if not base.endswith("/"):
        base += "/"
    sep = "&" if "?" in entity_set else "?"
    fmt = "json"
    return f"{base}{entity_set}{sep}$top={top}&$format={fmt}"


def _rows_from_response(payload: dict) -> List[dict]:
    """Extract the row list from v2 ({d:{results:[...]}} or {d:[...]}) or
    v4 ({value:[...]}) JSON shapes."""
    if not isinstance(payload, dict):
        return []
    if "value" in payload and isinstance(payload["value"], list):   # v4
        return payload["value"]
    d = payload.get("d")
    if isinstance(d, dict) and isinstance(d.get("results"), list):  # v2 collection
        return d["results"]
    if isinstance(d, list):                                         # v2 (older)
        return d
    return []


def fetch_rows(service_url: str, entity_set: str,
               user: Optional[str] = None, password: Optional[str] = None,
               top: int = 20, verify_ssl: bool = True,
               odata_v4: bool = False, timeout: int = 30) -> Dict:
    """Return {"reachable": bool, "rows": [...], "error": str|None}."""
    url = _entityset_url(service_url, entity_set, top, odata_v4)
    auth = (user, password) if user else None
    try:
        resp = requests.get(url, auth=auth,
                            headers={"Accept": "application/json"},
                            verify=verify_ssl, timeout=timeout)
    except requests.exceptions.RequestException as e:
        return {"reachable": False, "rows": [], "error": str(e)}
    if resp.status_code >= 400:
        return {"reachable": False, "rows": [],
                "error": f"HTTP {resp.status_code}: {resp.text[:150]}"}
    try:
        rows = _rows_from_response(resp.json())
    except ValueError:
        return {"reachable": False, "rows": [], "error": "response was not JSON"}
    return {"reachable": True, "rows": rows, "error": None}


def profile_rows(rows: List[dict], properties: List[dict]) -> Dict:
    """Derive a per-property profile from sample rows.

    properties: the entity set's property dicts (name + ui_type) from the model.
    Returns {prop_name: {distinct:[...], is_enum:bool, min, max, fill_rate}}.
    """
    profile: Dict[str, Dict] = {}
    n = len(rows) or 1
    for p in properties:
        name = p["name"]
        vals = []
        for r in rows:
            v = r.get(name)
            # v2 wraps navigation/deferred objects in dicts; skip those.
            if isinstance(v, (dict, list)):
                continue
            vals.append(v)
        non_null = [v for v in vals if v not in (None, "")]
        distinct = list(dict.fromkeys(non_null))  # preserve order, de-dupe
        entry: Dict = {
            "fill_rate": round(len(non_null) / n, 2),
            "distinct": distinct[:ENUM_MAX_DISTINCT],
            "is_enum": 0 < len(distinct) <= ENUM_MAX_DISTINCT
                        and p.get("ui_type") in ("text",),
        }
        if p.get("ui_type") in ("amount", "number") and non_null:
            nums = []
            for v in non_null:
                try:
                    nums.append(float(v))
                except (TypeError, ValueError):
                    pass
            if nums:
                entry["min"] = min(nums)
                entry["max"] = max(nums)
        profile[name] = entry
    return profile


def inspect_entity_set(service_url: str, entity_set: str, properties: List[dict],
                       user: Optional[str] = None, password: Optional[str] = None,
                       top: int = 20, verify_ssl: bool = True,
                       odata_v4: bool = False) -> Dict:
    """Convenience: fetch + profile in one call.
    Returns {"reachable":bool, "error":str|None, "sample_count":int,
             "profile": {prop: {...}}}."""
    res = fetch_rows(service_url, entity_set, user, password, top, verify_ssl, odata_v4)
    if not res["reachable"]:
        return {"reachable": False, "error": res["error"],
                "sample_count": 0, "profile": {}}
    prof = profile_rows(res["rows"], properties)
    return {"reachable": True, "error": None,
            "sample_count": len(res["rows"]), "profile": prof}


def profile_from_manual_json(payload_text: str, properties: List[dict]) -> Dict:
    """Profile from a pasted JSON response (for unreachable/on-prem services:
    the user pastes a `GET EntitySet?$top=20&$format=json` body from a browser)."""
    try:
        payload = _json.loads(payload_text)
    except ValueError as e:
        return {"reachable": False, "error": f"invalid JSON: {e}",
                "sample_count": 0, "profile": {}}
    rows = _rows_from_response(payload)
    return {"reachable": True, "error": None, "sample_count": len(rows),
            "profile": profile_rows(rows, properties)}


def summarize_profile_for_prompt(entity_set: str, profile: Dict,
                                 max_props: int = 20) -> str:
    """Compact sample-data summary for the AI prompt (enums + ranges + empties)."""
    if not profile:
        return ""
    lines = [f"Sample-data profile for {entity_set}:"]
    shown = 0
    for name, e in profile.items():
        if shown >= max_props:
            break
        bits = []
        if e.get("is_enum") and e.get("distinct"):
            bits.append("values=" + "/".join(str(v) for v in e["distinct"]))
        if "min" in e:
            bits.append(f"range={e['min']}..{e['max']}")
        if e.get("fill_rate", 1) < 0.15:
            bits.append("mostly-empty")
        if bits:
            lines.append(f"- {name}: " + ", ".join(bits))
            shown += 1
    return "\n".join(lines) if len(lines) > 1 else ""
