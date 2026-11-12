"""A7 — service-lane monitor (agentic job), spec §2.1.1 / §4.10.2.

The self-driving half of the service->sale loop: no user in the loop. On a
schedule it scans FOLLOW_UP service journeys, computes the customer's trade
equity, and for those crossing into positive equity (a repurchase/upgrade
signal) it writes/refreshes a DEALER_Recommendations card (action=
service_handoff) — the "equity flag" that an advisor actions into an A7
handoff_to_sales. Agents act on a clock, not only a click.

Autonomy gate (PLAT_ConfigAutonomy `a7.reforecast_promise` is A7's, but the
handoff recommendation is A2-adjacent; we gate on `a2.select_next_best_action`
since the card enters A2's funnel): at WRITE levels it writes the card; at
EXECUTE levels it could auto-create the purchase journey (left off by default —
a handoff creates customer-facing funnel state, so recommend is the safe floor).

  run_service_lane_monitor(market, apply, limit) -> summary of scanned/flagged/written.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from ..common import ddb, autonomy_config
from . import supervisor as svc_supervisor

MONITOR_ACTION_ID = "a2.select_next_best_action"
MONITOR_SOURCE = "agentic:service_lane_monitor (plan §2.1.1; repurchase/upgrade handoff)"
_POSITIVE_EQUITY = 0


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _latest_valuation(customer_id: str, env_prefix: str | None) -> dict[str, Any] | None:
    vals = ddb.scan_filter("DEALER_Valuations", "customer_id", customer_id, env_prefix)
    if not vals:
        return None
    return max(vals, key=lambda v: v.get("valued_at") or "")


def run_service_lane_monitor(market: str = "us", apply: bool = False,
                             limit: int | None = None,
                             env_prefix: str | None = None) -> dict[str, Any]:
    """Scan FOLLOW_UP service journeys, flag positive-equity repurchase candidates.

    apply=False is a dry run (computes + reports, writes nothing). At WRITE
    autonomy it writes a service_handoff recommendation card; it does NOT
    auto-create the purchase journey (that stays a human-actioned handoff)."""
    level = autonomy_config.active_level(MONITOR_ACTION_ID, market, env_prefix)
    sjs = ddb.scan_filter("DEALER_ServiceJourneys", "market_id", market, env_prefix)
    followups = [s for s in sjs if s.get("stage") == "FOLLOW_UP"]
    if limit is not None:
        followups = followups[:limit]

    now = _now()
    scanned = flagged = written = 0
    results: list[dict[str, Any]] = []

    for sj in followups:
        scanned += 1
        cid = sj.get("customer_id")
        # equity in the car that was serviced, not the customer's latest valuation
        from ..common import vehicles
        val = vehicles.valuation_for(sj.get("vin"), cid, env_prefix) if cid else None
        if not val:
            continue
        equity = val.get("equity")
        try:
            equity_num = float(equity) if equity is not None else None
        except (TypeError, ValueError):
            equity_num = None
        if equity_num is None or equity_num <= _POSITIVE_EQUITY:
            continue

        flagged += 1
        sj_id = sj.get("service_journey_id")
        rec_id = f"REC-SVCLANE-{sj_id}"
        reason = f"positive equity {val.get('currency','')} {equity} at FOLLOW_UP — repurchase/upgrade"
        row = {
            "recommendation_id": rec_id,
            "action": "service_handoff",
            "agent_id": "A7",
            "customer_id": cid,
            "journey_id": sj.get("purchase_journey_id"),
            "service_journey_id": sj_id,
            "shown": 0, "accepted": 0, "converted": 0,
            "shown_at": now,
            "autonomy_level": level,
            "equity_value": equity,
            "reason": reason,
            "source_ref": MONITOR_SOURCE,
        }
        results.append({"service_journey_id": sj_id, "equity": equity, "rec_id": rec_id})
        if apply and level in autonomy_config.WRITE_LEVELS:
            ddb.put_item("DEALER_Recommendations", row, env_prefix)
            written += 1

    return {
        "market": market,
        "autonomy_level": level,
        "scanned_followups": scanned,
        "positive_equity_flagged": flagged,
        "recommendations_written": written,
        "applied": apply,
        "ran_at": now,
        "sample": results[:10],
    }
