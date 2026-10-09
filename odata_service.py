"""
odata_service.py — Module 1 (OData Connector), public facade.

Thin facade over metadata_reader so callers have a single, stable entry point
for "get me the metadata for this service, however I'm allowed to reach it."
Mode is chosen per run: 'direct' | 'destination' | 'manual'.
"""
from __future__ import annotations
from typing import Optional
import metadata_reader as mr
from metadata_reader import MetadataError  # re-export


def get_metadata(mode: str, **kw) -> str:
    """Fetch raw EDMX for the chosen mode.

    mode='direct'      -> service_url, [user], [password], [verify_ssl]
    mode='destination' -> dest_instance, dest_name, service_path, [verify_ssl]
    mode='manual'      -> edmx_text
    """
    if mode == "direct":
        return mr.fetch_direct(
            kw["service_url"], kw.get("user"), kw.get("password"),
            kw.get("verify_ssl", True),
        )
    if mode == "destination":
        return mr.fetch_via_destination(
            kw["dest_instance"], kw["dest_name"], kw["service_path"],
            kw.get("key_name", "genkey"), kw.get("verify_ssl", True),
        )
    if mode == "manual":
        return mr.load_manual(kw["edmx_text"])
    raise MetadataError(f"Unknown metadata mode: {mode!r}")
