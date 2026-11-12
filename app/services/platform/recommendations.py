"""§4.10.3 — Outcome loop over DEALER_Recommendations (shown → accepted → converted).

Every recommendation any agent surfaces is a row. Agents and jobs write the row
when it is produced (`shown_at`); this module owns the rest of its life:

  record_shown(...)               -> write/refresh a row when a surface renders it
  accept(rec_id, user)            -> "Use This" / "Present" / "Do it"
  dismiss(rec_id, user)           -> explicit rejection (kept for learning)
  on_conversion(event)            -> subscriber: a later conversion event sets
                                     converted_at on the accepted recommendation(s)
                                     for the same journey / service journey
  stats(agent, action_id, scope)  -> shown/accepted/converted counts + rates

Rows carry both the per-row timestamps (accepted_at, converted_at, …) and the
integer counters the seed packs use (shown / accepted / converted), so aggregate
seed rows and live rows sum the same way. No contact fields are ever written here
(the lake `recommendations/` prefix must stay PII-free, acceptance #34).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
from typing import Any

from ..common import ddb

_TABLE = "DEALER_Recommendations"

# conversion event type -> predicate on the event detail (§4.10.3, acceptance #34)
CONVERSION_EVENTS: dict[str, Any] = {
    "appointment.booked": lambda d: True,
    "service.appointment.booked": lambda d: True,
    "estimate.line_decided": lambda d: (d.get("decision") or "").lower() in ("approve", "approved"),
    "vehicle.delivered": lambda d: True,
    "deal.booked": lambda d: True,
}

# Fields that must never land on a recommendation row (C3 contact data).
_FORBIDDEN = frozenset({"email", "phone", "mobile", "address", "first_name", "last_name", "name"})


class NotFound(Exception):
    pass


class InvalidState(Exception):
    pass


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _emit(event_type: str, row: dict[str, Any], env_prefix: str | None) -> None:
    from ..events import bus
    bus.emit(event_type, {k: row.get(k) for k in (
        "recommendation_id", "agent_id", "action", "journey_id", "service_journey_id",
        "account_id", "dealer_id", "context_hash", "autonomy_level_at_time")},
        env_prefix=env_prefix)


def record_shown(agent_id: str, action: str, *, shown_to: str,
                 journey_id: str | None = None, service_journey_id: str | None = None,
                 account_id: str | None = None, dealer_id: str | None = None,
                 context_hash: str | None = None, autonomy_level: str | None = None,
                 trace_id: str | None = None, reason: str | None = None,
                 recommendation_id: str | None = None,
                 extra: dict[str, Any] | None = None,
                 env_prefix: str | None = None) -> dict[str, Any]:
    """Write (or re-show) a recommendation row. Re-showing an existing id bumps
    `shown` and refreshes `shown_at` without touching accept/convert state."""
    if not (journey_id or service_journey_id or account_id):
        raise ValueError("a recommendation must reference a journey, service journey, or account")
    extra = {k: v for k, v in (extra or {}).items() if k not in _FORBIDDEN}
    rid = recommendation_id or "REC-" + hashlib.sha1(
        f"{agent_id}|{action}|{journey_id}|{service_journey_id}|{account_id}|{context_hash}".encode()
    ).hexdigest()[:16]
    ts = _now()
    existing = ddb.get_item(_TABLE, {"recommendation_id": rid}, env_prefix)
    if existing:
        row = dict(existing)
        row["shown"] = int(row.get("shown") or 0) + 1
        row["shown_at"] = ts
        row["shown_to"] = shown_to
    else:
        row = {
            "recommendation_id": rid, "agent_id": agent_id, "action": action,
            "journey_id": journey_id, "service_journey_id": service_journey_id,
            "account_id": account_id, "dealer_id": dealer_id,
            "context_hash": context_hash, "shown_to": shown_to, "shown_at": ts,
            "shown": 1, "accepted": 0, "converted": 0,
            "accepted_at": None, "accepted_by": None, "dismissed_at": None,
            "converted_at": None, "conversion_event": None,
            "autonomy_level_at_time": autonomy_level, "trace_id": trace_id,
            "reason": reason, "source_ref": f"{agent_id}.{action}", **extra,
        }
    ddb.put_item(_TABLE, row, env_prefix)
    _emit("recommendation.shown", row, env_prefix)
    return row


def _get(rec_id: str, env_prefix: str | None) -> dict[str, Any]:
    row = ddb.get_item(_TABLE, {"recommendation_id": rec_id}, env_prefix)
    if row is None:
        raise NotFound(f"recommendation {rec_id} not found")
    return dict(row)


def accept(rec_id: str, user_id: str, env_prefix: str | None = None) -> dict[str, Any]:
    row = _get(rec_id, env_prefix)
    if row.get("dismissed_at"):
        raise InvalidState(f"{rec_id} was dismissed; it cannot be accepted")
    if not row.get("accepted_at"):
        row["accepted_at"] = _now()
        row["accepted_by"] = user_id
        row["accepted"] = int(row.get("accepted") or 0) + 1
        ddb.put_item(_TABLE, row, env_prefix)
        _emit("recommendation.accepted", row, env_prefix)
    return row


def dismiss(rec_id: str, user_id: str, reason: str | None = None,
            env_prefix: str | None = None) -> dict[str, Any]:
    row = _get(rec_id, env_prefix)
    if row.get("accepted_at"):
        raise InvalidState(f"{rec_id} was already accepted")
    if not row.get("dismissed_at"):
        row["dismissed_at"] = _now()
        row["dismissed_by"] = user_id
        row["dismiss_reason"] = reason
        ddb.put_item(_TABLE, row, env_prefix)
    return row


def on_conversion(detail: dict[str, Any], env_prefix: str | None = None) -> dict[str, Any]:
    """Subscriber: mark accepted, not-yet-converted recommendations for the same
    journey / service journey as converted by this event."""
    etype = (detail.get("type") or detail.get("event_type") or "").split("@")[0]
    pred = CONVERSION_EVENTS.get(etype)
    if pred is None or not pred(detail):
        return {"handled": False, "reason": f"{etype or 'event'} is not a conversion"}
    converted: list[str] = []
    for attr in ("journey_id", "service_journey_id"):
        ref = detail.get(attr)
        if not ref:
            continue
        for row in ddb.scan_filter(_TABLE, attr, ref, env_prefix):
            if row.get("accepted_at") and not row.get("converted_at"):
                row = dict(row)
                row["converted_at"] = _now()
                row["conversion_event"] = detail.get("event_id") or etype
                row["converted"] = int(row.get("converted") or 0) + 1
                ddb.put_item(_TABLE, row, env_prefix)
                converted.append(row["recommendation_id"])
    for rid in converted:
        _emit("recommendation.converted", _get(rid, env_prefix), env_prefix)
    return {"handled": True, "conversion": etype, "converted": converted}


def stats(agent_id: str | None = None, action: str | None = None,
          dealer_id: str | None = None, context_hash: str | None = None,
          env_prefix: str | None = None) -> dict[str, Any]:
    """Aggregate shown/accepted/converted per (agent, action), optionally scoped."""
    rows = ddb.scan_filter(_TABLE, "agent_id", agent_id, env_prefix) if agent_id \
        else ddb.scan_all(_TABLE, env_prefix)
    buckets: dict[tuple[str, str], dict[str, int]] = {}
    for r in rows:
        if action and r.get("action") != action:
            continue
        if dealer_id and r.get("dealer_id") not in (None, dealer_id):
            continue
        if context_hash and r.get("context_hash") != context_hash:
            continue
        b = buckets.setdefault((r.get("agent_id") or "?", r.get("action") or "?"),
                               {"rows": 0, "shown": 0, "accepted": 0, "converted": 0})
        b["rows"] += 1
        for k in ("shown", "accepted", "converted"):
            b[k] += int(r.get(k) or 0)
    out = []
    for (agent, act), b in sorted(buckets.items()):
        shown = b["shown"] or 0
        out.append({"agent_id": agent, "action": act, **b,
                    "acceptance_rate": round(b["accepted"] / shown, 4) if shown else None,
                    "conversion_rate": round(b["converted"] / shown, 4) if shown else None})
    return {"filters": {"agent_id": agent_id, "action": action, "dealer_id": dealer_id,
                        "context_hash": context_hash}, "actions": out}


def conversion_rates(context_hash: str, env_prefix: str | None = None) -> dict[str, float]:
    """action -> conversion rate for one context bucket (A2 NBA learning input)."""
    rates: dict[str, float] = {}
    for a in stats(context_hash=context_hash, env_prefix=env_prefix)["actions"]:
        if a["conversion_rate"] is not None and a["shown"] >= 1:
            rates[a["action"]] = max(rates.get(a["action"], 0.0), a["conversion_rate"])
    return rates


def _register() -> None:
    from ..events import bus
    for et in CONVERSION_EVENTS:
        if on_conversion not in bus._EXTRA_SUBSCRIBERS.get(et, []):
            bus.subscribe(et, on_conversion)


_register()
