"""Admin Console — admin action audit trail (spec §5.17 Audit, §8).

Every admin write (user/org/market edits, config publish/rollback, connector
tests, data requests, entity merges, approval decisions) records one row. Rows
live in PLAT_AgentTraces with `agent_id="ADMIN"` (the existing audit/trace
store, so no new table is needed and the lake export picks them up); the Agents
panel excludes them. No PII: ids, field names, and versions only.

  record(actor, action, target, detail)  -> audit row
  recent(limit, action_prefix)           -> newest admin actions
"""
from __future__ import annotations

import datetime as _dt
import hashlib
from typing import Any

from ..common import ddb, trace

_TABLE = "PLAT_AgentTraces"
AGENT_ID = "ADMIN"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def record(actor: str, action: str, target: str, detail: dict[str, Any] | None = None,
           outcome: str = "ok", env_prefix: str | None = None) -> dict[str, Any]:
    ts = _now()
    tid = "ADM-" + hashlib.sha1(f"{actor}|{action}|{target}|{ts}|{detail}".encode()).hexdigest()[:14]
    row = {
        "trace_id": tid, "ts": ts, "agent_id": AGENT_ID, "tool": action, "actor": actor,
        "target": target, "detail": detail or {}, "outcome": outcome,
        "routing_decision": f"admin-console {action} {target}",
        "source_ref": f"admin-console:{action}",
    }
    row["correlation_id"] = trace.CURRENT_CORRELATION_ID.get()
    ddb.put_item(_TABLE, row, env_prefix)
    return row


def recent(limit: int = 50, action_prefix: str | None = None,
           env_prefix: str | None = None) -> list[dict[str, Any]]:
    rows = ddb.scan_filter(_TABLE, "agent_id", AGENT_ID, env_prefix)
    if action_prefix:
        rows = [r for r in rows if str(r.get("tool") or "").startswith(action_prefix)]
    rows.sort(key=lambda r: r.get("ts") or "", reverse=True)
    return rows[:limit]


# --- audit query + export (Admin › Audit) -------------------------------------

KINDS = ("admin_actions", "journey_events", "service_events", "guardrail_events")


def query(kind: str, since: str | None = None, until: str | None = None, q: str | None = None,
          limit: int = 500, env_prefix: str | None = None) -> dict[str, Any]:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if kind == "admin_actions":
        rows = ddb.scan_filter(_TABLE, "agent_id", AGENT_ID, env_prefix)
        cols = ["ts", "actor", "tool", "target", "outcome", "correlation_id"]
        tkey = "ts"
    elif kind == "journey_events":
        rows = ddb.scan_all("DEALER_JourneyEvents", env_prefix)
        cols = ["event_ts", "journey_id", "event_type", "actor", "evidence", "correlation_id"]
        tkey = "event_ts"
    elif kind == "service_events":
        rows = ddb.scan_all("DEALER_ServiceJourneyEvents", env_prefix)
        cols = ["event_ts", "service_journey_id", "event_type", "actor", "evidence"]
        tkey = "event_ts"
    else:
        rows = ddb.scan_all("OEM_GuardrailEvents", env_prefix)
        cols = ["evaluated_at", "rule_id", "rule_name", "outcome", "agent_id", "tool", "district_id", "query_hash"]
        tkey = "evaluated_at"
    if since:
        rows = [r for r in rows if (r.get(tkey) or "") >= since]
    if until:
        rows = [r for r in rows if (r.get(tkey) or "") <= until + ("T23:59:59Z" if len(until) == 10 else "")]
    if q:
        needle = q.lower()
        rows = [r for r in rows if any(needle in str(r.get(c) or "").lower() for c in cols)]
    rows.sort(key=lambda r: r.get(tkey) or "", reverse=True)
    total = len(rows)

    def cell(v: Any) -> Any:
        if isinstance(v, dict):
            return v.get("user_id") or v.get("agent") or v.get("type") or str(v)
        return v
    return {"kind": kind, "columns": cols, "total": total, "truncated": total > limit,
            "rows": [{c: cell(r.get(c)) for c in cols} for r in rows[:limit]]}


def to_csv(result: dict[str, Any]) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=result["columns"], extrasaction="ignore")
    w.writeheader()
    for r in result["rows"]:
        w.writerow({k: ("" if v is None else v) for k, v in r.items()})
    return buf.getvalue()
