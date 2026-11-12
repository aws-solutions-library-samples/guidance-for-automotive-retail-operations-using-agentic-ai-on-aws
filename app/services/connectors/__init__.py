"""`get_connector()` — the single factory behind external-system reads.

Mode selection, most specific wins:
  1. the per-system mode on the Admin Console connector registry
     (`admin/connectors.py`, versioned PLAT_Config "connectors" doc) when a row
     for the domain exists — this is how an admin flips one system to live;
  2. otherwise sandbox. INTEGRATION_MODE never selects live by itself: `aws`
     mode turns on the AWS *platform* engines (Bedrock, AgentCore), while a live
     business-system connector additionally needs its registry row flipped and
     credentials referenced — so sandbox stays the safe default everywhere.
"""
from __future__ import annotations

import os

from .base import Connector, NotConfigured, NotFound  # noqa: F401 (re-exported)
from .live import LiveConnector
from .sandbox import SandboxConnector

DOMAINS = ("dms", "labor", "parts")


def _registry_entry(domain: str, env_prefix: str | None) -> dict | None:
    """The domain's row from the Admin connector registry, or None when the
    registry cannot be read (table missing offline) or has no row."""
    try:
        from ..admin import connectors as admin_connectors  # lazy: avoids an import cycle
        market = os.environ.get("RETAIL_OPS_DEFAULT_MARKET", "us")
        for entry in admin_connectors._registry(market, env_prefix):
            if entry.get("connector_id") == domain:
                return entry
    except Exception:
        pass
    return None


def get_connector(domain: str, env_prefix: str | None = None) -> Connector:
    """The connector for one wave-1 domain ("dms" | "labor" | "parts")."""
    if domain not in DOMAINS:
        raise KeyError(f"unknown connector domain {domain!r} (wave 1: {DOMAINS})")
    entry = _registry_entry(domain, env_prefix)
    if entry and entry.get("mode") == "live":
        return LiveConnector(domain, env_prefix, registry_entry=entry)
    return SandboxConnector(domain, env_prefix)
