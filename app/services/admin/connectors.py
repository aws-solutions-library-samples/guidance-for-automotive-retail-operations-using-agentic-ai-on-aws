"""Admin Console — connector registry and health (spec §4.1, §5.17 Connectors, §2.6.7).

The registry is a versioned `PLAT_Config` document (kind "connectors") listing
every external system behind the Connector interface with its mode
(sandbox | live), a credential *reference* (a Secrets Manager secret name — the
value is never read, stored, or returned here), health, and last sync.

  default_registry(market)       -> the spec's connector set, all sandbox
  validate_registry(entries)     -> normalised entries (rejects secret material)
  list_connectors(market)        -> registry merged with last health results
  test_connection(id, market)    -> sandbox health check against the backing
                                    tables; live mode reports not_configured
                                    until a live implementation exists. A
                                    failure emits connector.sync_failed.
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any

from ..common import ddb
from . import audit_log

# (connector_id, system, vendor, backing sandbox tables) — spec §2.3, §2.6.7, §3.7
_REGISTRY: list[tuple[str, str, str, list[str]]] = [
    ("crm", "CRM", "Salesforce", ["DEALER_Customers"]),
    ("cdp", "CDP", "Customer data platform", ["DEALER_Signals"]),
    ("configurator", "Configurator / catalog", "OEM configurator", ["OEM_Catalog", "OEM_CatalogModels"]),
    ("inventory", "Dealer inventory (VIN-level)", "DMS inventory feed", ["DEALER_Inventory"]),
    ("fni", "F&I", "CDK", ["DEALER_FinanceOffers", "DEALER_FIProducts"]),
    ("dms", "DMS (deals, RO mirror)", "CDK", ["DEALER_Deals", "DEALER_RepairOrders"]),
    ("valuation", "Trade-in valuation", "Cox (vAuto / KBB) · Black Book", ["DEALER_Valuations"]),
    ("ads", "Ad platforms", "Adobe · Google · Meta", ["OEM_Campaigns", "DEALER_Touchpoints"]),
    ("leads", "Lead providers (ADF)", "Third-party lead feeds", ["DEALER_Leads"]),
    ("calendar", "Dealer calendar", "Scheduling", ["DEALER_Appointments"]),
    ("incentives", "OEM incentive feed", "OEM C&I", ["OEM_Incentives"]),
    ("rdr", "RDR / sales data lake", "OEM lake", ["OEM_Accounts"]),
    ("ci", "C&I incentive system", "OEM C&I", ["OEM_CIPrograms"]),
    ("marketintel", "Market intelligence", "S&P Global Mobility · R.L. Polk", ["OEM_Markets"]),
    ("csi", "CSI survey", "Survey vendor", ["DEALER_Outcomes"]),
    ("lms", "Training LMS / facility compliance", "OEM LMS", ["OEM_Compliance"]),
    ("comms", "Sales Ops comms", "OEM comms", ["OEM_Comms"]),
    ("scheduling", "Service scheduling", "Scheduling", ["DEALER_Appointments"]),
    ("inspection", "Inspection (MPI/VSA)", "Inspection tool", ["DEALER_InspectionTemplates", "DEALER_Inspections"]),
    ("dtc", "DTC / repair procedures", "OEM service info · Mitchell · ALLDATA", ["DEALER_DTCReference"]),
    ("labor", "Labor-time guide", "Labor guide", ["DEALER_LaborOps"]),
    ("parts", "Parts catalog + inventory", "OEM parts", ["DEALER_Parts", "DEALER_PartsOrders"]),
    ("warranty", "Warranty / recall", "OEM warranty", ["DEALER_Coverage"]),
    ("telematics", "Connected-car alerts", "OEM telematics (--enable-telematics)", []),
]
MODES = ("sandbox", "live")
# Domains already migrated behind the Connector interface (connector-interface migration wave 1):
# their health comes from connector.health(), not a raw table probe here.
_MIGRATED = ("dms", "labor", "parts")
_SECRET_REF = re.compile(r"^[A-Za-z0-9/_+=.@-]{1,512}$")
_SECRETISH = re.compile(r"(secret|password|token|key)\s*[:=]", re.IGNORECASE)


class UnknownConnector(KeyError):
    pass


class InvalidRegistry(ValueError):
    pass


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_registry(market: str) -> list[dict[str, Any]]:
    return [{"connector_id": cid, "system": system, "vendor": vendor, "mode": "sandbox",
             "credential_ref": f"retail-ops/{market}/connectors/{cid}", "enabled": cid != "telematics",
             "tables": tables} for cid, system, vendor, tables in _REGISTRY]


def validate_registry(entries: Any) -> list[dict[str, Any]]:
    if not isinstance(entries, list) or not entries:
        raise InvalidRegistry("connectors must be a non-empty list")
    known = {c[0]: c for c in _REGISTRY}
    out, seen = [], set()
    for e in entries:
        cid = (e or {}).get("connector_id")
        if cid not in known:
            raise InvalidRegistry(f"unknown connector {cid!r}")
        if cid in seen:
            raise InvalidRegistry(f"duplicate connector {cid}")
        seen.add(cid)
        mode = e.get("mode", "sandbox")
        if mode not in MODES:
            raise InvalidRegistry(f"{cid}: mode must be one of {MODES}")
        ref = e.get("credential_ref") or ""
        if ref and (not _SECRET_REF.match(ref) or _SECRETISH.search(ref)):
            raise InvalidRegistry(f"{cid}: credential_ref must be a Secrets Manager secret NAME, not secret material")
        if mode == "live" and not ref:
            raise InvalidRegistry(f"{cid}: live mode needs a credential_ref")
        _, system, vendor, tables = known[cid]
        out.append({"connector_id": cid, "system": system, "vendor": vendor, "mode": mode,
                    "credential_ref": ref, "enabled": bool(e.get("enabled", True)), "tables": tables})
    return out


def _registry(market: str, env_prefix: str | None) -> list[dict[str, Any]]:
    from . import config_store
    return config_store.get("connectors", market, env_prefix)["content"].get("connectors") or default_registry(market)


def _health_key(market: str, cid: str) -> str:
    return f"connector_health#{market}#{cid}"


def _last_health(market: str, cid: str, env_prefix: str | None) -> dict[str, Any] | None:
    rows = ddb.query_hash("PLAT_Config", "item_key", _health_key(market, cid), env_prefix)
    return rows[0] if rows else None


def _rotation_key(market: str, cid: str) -> str:
    return f"connector_rotation#{market}#{cid}"


def _last_rotation(market: str, cid: str, env_prefix: str | None) -> dict[str, Any] | None:
    rows = ddb.query_hash("PLAT_Config", "item_key", _rotation_key(market, cid), env_prefix)
    return rows[0] if rows else None


def request_rotation(connector_id: str, market: str, actor: str,
                     env_prefix: str | None = None) -> dict[str, Any]:
    """Mark a credential rotation as requested for a connector (§5.17).

    Records the request against the registry entry — status + rotated_at + who
    asked — so the rotation is auditable and the console can show it. The
    rotation itself is performed by the platform's Secrets Manager rotation
    (the credential_ref names the secret; its value is never read, stored, or
    returned here), and nothing about the registry entry needs to change
    afterwards because connectors read the secret at call time. A connector with
    no credential_ref has nothing to rotate."""
    reg = {c["connector_id"]: c for c in _registry(market, env_prefix)}
    if connector_id not in reg:
        raise UnknownConnector(connector_id)
    c = reg[connector_id]
    if not c.get("credential_ref"):
        raise InvalidRegistry(f"{connector_id}: no credential_ref configured — nothing to rotate")
    ts = _now()
    prior = _last_rotation(market, connector_id, env_prefix) or {}
    row = {"item_key": _rotation_key(market, connector_id), "version": 0,
           "status": "rotation_requested", "rotated_at": ts, "requested_at": ts, "requested_by": actor,
           "credential_ref": c["credential_ref"], "mode": c["mode"],
           "previous_rotated_at": prior.get("rotated_at"),
           "source_ref": "admin.request_rotation (§5.17 credential rotation)"}
    ddb.put_item("PLAT_Config", row, env_prefix)
    audit_log.record(actor, "connector.rotate", f"{connector_id}#{market}",
                     {"status": row["status"], "credential_ref": c["credential_ref"], "mode": c["mode"]},
                     env_prefix=env_prefix)
    detail = (f"rotation requested for secret {c['credential_ref']}; the connector reads the secret at call "
              "time, so no registry change is needed — run Test to confirm the new credentials")
    return {"connector_id": connector_id, "market_id": market, "mode": c["mode"],
            "enabled": bool(c.get("enabled")),
            "status": "rotation_requested", "rotated_at": ts, "requested_by": actor,
            "credential_ref": c["credential_ref"], "previous_rotated_at": prior.get("rotated_at"),
            "detail": detail, "source_ref": row["source_ref"]}


def list_connectors(market: str, env_prefix: str | None = None) -> dict[str, Any]:
    items = []
    for c in _registry(market, env_prefix):
        h = _last_health(market, c["connector_id"], env_prefix) or {}
        rot = _last_rotation(market, c["connector_id"], env_prefix) or {}
        items.append({**c, "health": h.get("status", "unknown"), "last_checked_at": h.get("checked_at"),
                      "last_sync_at": h.get("last_sync_at"), "detail": h.get("detail"),
                      "rotation_status": rot.get("status"), "rotated_at": rot.get("rotated_at"),
                      "rotation_requested_by": rot.get("requested_by")})
    summary = {s: sum(1 for i in items if i["health"] == s) for s in ("ok", "degraded", "failed", "not_configured", "unknown")}
    return {"market_id": market, "connectors": items, "summary": summary}


def test_connection(connector_id: str, market: str, actor: str, env_prefix: str | None = None) -> dict[str, Any]:
    reg = {c["connector_id"]: c for c in _registry(market, env_prefix)}
    if connector_id not in reg:
        raise UnknownConnector(connector_id)
    c = reg[connector_id]
    checked = _now()
    if not c.get("enabled"):
        status, detail, rows = "not_configured", "connector disabled", {}
    elif connector_id in _MIGRATED:
        # connector-interface migration wave 1: these domains sit behind the Connector interface — ask the
        # connector itself (sandbox probes its backing tables; live reports
        # not_configured until Phase 6 wires an endpoint).
        from .. import connectors as connector_boundary
        h = connector_boundary.get_connector(connector_id, env_prefix).health()
        status, detail, rows = h["status"], h["detail"], h["tables"]
    elif c["mode"] == "live":
        # No live implementation ships in the pilot (Phase 6); never fake a pass.
        status, detail, rows = "not_configured", "live connector not implemented (Phase 6); sandbox data in use", {}
    elif not c["tables"]:
        status, detail, rows = "not_configured", "no sandbox backing for this connector", {}
    else:
        rows = {}
        for t in c["tables"]:
            try:
                rows[t] = len(ddb.scan_filter(t, "market_id", market, env_prefix, limit=1)) or \
                    len(ddb.scan_all(t, env_prefix, limit=1))
            except Exception as e:  # missing table / access denied
                rows[t] = f"error: {type(e).__name__}"
        errors = [t for t, v in rows.items() if not isinstance(v, int)]
        empty = [t for t, v in rows.items() if v == 0]
        status = "failed" if errors else "degraded" if empty else "ok"
        detail = (f"unreachable: {errors}" if errors else f"empty: {empty}" if empty else "sandbox tables reachable")
    row = {"item_key": _health_key(market, connector_id), "version": 0, "status": status, "detail": detail,
           "checked_at": checked, "last_sync_at": checked if status == "ok" else (_last_health(market, connector_id, env_prefix) or {}).get("last_sync_at"),
           "checked_by": actor}
    ddb.put_item("PLAT_Config", row, env_prefix)
    bus_event = None
    if status == "failed":
        from ..events import bus
        bus_event = bus.emit("connector.sync_failed", {"connector_id": connector_id, "market_id": market,
                                                        "detail": detail}, actor=actor, env_prefix=env_prefix)
    audit_log.record(actor, "connector.test", f"{connector_id}#{market}", {"status": status},
                     outcome="ok" if status in ("ok", "not_configured") else "error", env_prefix=env_prefix)
    return {"connector_id": connector_id, "mode": c["mode"], "status": status, "detail": detail,
            "checked_at": checked, "tables": rows, "bus_event": bus_event}
