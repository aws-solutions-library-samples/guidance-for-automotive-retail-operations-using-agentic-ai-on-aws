"""A2 — stalled-journey sweep (agentic job), spec §3.2 / §4.10.2.

The `journey.stalled` trigger exists in the event catalogue and has a Play
subscriber (Callback Brief), but until this module nothing emitted it. The
sweep is the emitter: on a schedule it looks at every ACTIVE purchase journey,
computes how long it has been idle since its last event, and when that passes
the stall SLA it marks the journey stalled and emits `journey.stalled` — which
fires the Callback Brief play and lands a card on the advisor's desk.

  run_stall_sweep(market, apply, limit) -> summary

Grounding / invariants:
  * idle time is measured from `last_event_at` (falls back to `updated_at`),
    never guessed; the SLA is `RETAIL_OPS_STALL_SLA_DAYS` (default 7).
  * idempotent: a journey already flagged `stalled` with `stall_notified_at`
    set is skipped, so the event fires once per stall, not once per tick. A
    journey that moves stage again clears the flag in `advance_journey`.
  * only active stages (DISCOVER..DELIVER) can stall; OWN is terminal for sales.
"""
from __future__ import annotations

import datetime as _dt
import os
from typing import Any

from ..common import ddb
from ..events import bus

_ACTIVE = ("DISCOVER", "CONFIGURE", "QUALIFY", "FINANCE", "TRADE_IN", "AGREE", "DELIVER")
SOURCE = "agentic:journey_stall_sweep (spec §3.2; trigger journey.stalled)"


def _sla_days() -> float:
    try:
        return float(os.environ.get("RETAIL_OPS_STALL_SLA_DAYS", "7"))
    except ValueError:
        return 7.0


def _parse(ts: str | None) -> _dt.datetime | None:
    if not ts:
        return None
    try:
        return _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(d: _dt.datetime) -> str:
    return d.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_stall_sweep(market: str = "us", apply: bool = False, limit: int | None = None,
                    env_prefix: str | None = None, now: _dt.datetime | None = None) -> dict[str, Any]:
    """Flag newly stalled journeys and emit journey.stalled for each.

    apply=False is a dry run: reports what WOULD be flagged, writes nothing."""
    at = now or _dt.datetime.now(_dt.timezone.utc)
    sla = _sla_days()
    journeys = ddb.scan_filter("DEALER_Journeys", "market_id", market, env_prefix)
    candidates: list[tuple[dict[str, Any], float]] = []
    for j in journeys:
        if j.get("stage") not in _ACTIVE:
            continue
        if j.get("stalled") and j.get("stall_notified_at"):
            continue                       # already flagged + notified
        last = _parse(j.get("last_event_at")) or _parse(j.get("updated_at"))
        if last is None:
            continue
        idle_days = (at - last).total_seconds() / 86400.0
        if idle_days >= sla:
            candidates.append((j, idle_days))
    candidates.sort(key=lambda c: -c[1])
    if limit is not None:
        candidates = candidates[:limit]
    flagged: list[dict[str, Any]] = []
    for j, idle in candidates:
        jid = j["journey_id"]
        entry = {"journey_id": jid, "stage": j.get("stage"), "idle_days": round(idle, 1),
                 "dealer_id": j.get("dealer_id"), "owner_user_id": j.get("owner_user_id")}
        if apply:
            row = dict(j)
            row["stalled"] = True
            row["idle_days"] = round(idle, 1)
            row["stall_notified_at"] = _iso(at)
            row["updated_at"] = _iso(at)
            ddb.put_item("DEALER_Journeys", row, env_prefix)
            ev = bus.emit("journey.stalled", {
                "journey_id": jid, "customer_id": j.get("customer_id"), "dealer_id": j.get("dealer_id"),
                "market_id": market, "stage": j.get("stage"), "idle_days": round(idle, 1),
                "sla_days": sla, "owner_user_id": j.get("owner_user_id"),
                "source_ref": SOURCE,
            }, env_prefix=env_prefix, actor="A2")
            entry["event_id"] = ev.get("event_id")
        flagged.append(entry)
    return {
        "market": market, "sla_days": sla, "scanned": len(journeys),
        "newly_stalled": len(flagged), "applied": apply, "ran_at": _iso(at),
        "sample": flagged[:12],
    }
