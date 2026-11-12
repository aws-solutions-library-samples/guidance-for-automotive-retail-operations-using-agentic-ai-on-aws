"""Admin Console — entity merge queue (spec §4.7, §5.17 Entities).

`PLAT_EntityMap` maps external ids (CRM / CDP / DMS) to solution-minted ids. Matches
below the confidence threshold land here for a human: approve (confirm the
mapping / duplicate link) or reject (break the link; the external id is treated
as a distinct entity). Decisions are recorded on the row and audit-logged.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from ..common import ddb
from . import audit_log

_TABLE = "PLAT_EntityMap"
THRESHOLD = 0.6
DECISIONS = ("approve", "reject")


class NotFound(Exception):
    pass


class InvalidState(Exception):
    pass


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def queue(status: str = "pending", market: str | None = None, env_prefix: str | None = None) -> dict[str, Any]:
    rows = ddb.scan_all(_TABLE, env_prefix)
    low = [r for r in rows if float(r.get("confidence") or 0) < THRESHOLD or r.get("merge_status")]
    if market:
        low = [r for r in low if r.get("market_id") == market]

    def st(r: dict[str, Any]) -> str:
        return r.get("merge_status") or "pending"

    items = [r for r in low if status == "all" or st(r) == status]
    items.sort(key=lambda r: float(r.get("confidence") or 0))
    names = {}
    for r in items:
        for cid in (r.get("app_id"), r.get("duplicate_of")):
            if cid and r.get("entity_type") == "customer" and cid not in names:
                c = ddb.get_item("DEALER_Customers", {"customer_id": cid}, env_prefix) or {}
                names[cid] = {"dealer_id": c.get("dealer_id"), "region_code": c.get("region_code"),
                              "label": " ".join(filter(None, [c.get("given_name"), (c.get("family_name") or "")[:1] + "." if c.get("family_name") else None])) or cid}
    return {"threshold": THRESHOLD, "count": len(items),
            "pending": sum(1 for r in low if st(r) == "pending"),
            "items": [{**r, "merge_status": st(r), "app": names.get(r.get("app_id")),
                       "candidate": names.get(r.get("duplicate_of"))} for r in items]}


def decide(item_key: str, system: str, decision: str, actor: str, reason: str | None = None,
           env_prefix: str | None = None) -> dict[str, Any]:
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}")
    row = ddb.get_item(_TABLE, {"item_key": item_key, "system": system}, env_prefix)
    if row is None:
        raise NotFound(f"entity mapping {item_key}/{system} not found")
    if row.get("merge_status") in ("approved", "rejected"):
        raise InvalidState(f"already {row['merge_status']}")
    row = dict(row)
    row["merge_status"] = "approved" if decision == "approve" else "rejected"
    row["decided_by"] = actor
    row["decided_at"] = _now()
    row["decision_reason"] = reason
    row["confidence_before"] = row.get("confidence")
    if decision == "approve":
        row["confidence"] = 1.0
    else:
        row["duplicate_of"] = None
    ddb.put_item(_TABLE, row, env_prefix)
    audit_log.record(actor, f"entity.{decision}", f"{item_key}/{system}",
                     {"duplicate_of": row.get("duplicate_of"), "reason": bool(reason)}, env_prefix=env_prefix)
    return row
