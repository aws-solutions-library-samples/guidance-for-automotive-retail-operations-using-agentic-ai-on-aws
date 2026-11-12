"""A7 — 05:00 morning shop-load run (agentic job), spec §6a / §4.10.2.

The service counterpart of the nurture tick: nothing starts with a user action
(acceptance criterion 32). Before the shift begins, A7 sweeps today's booked
service journeys and does the advisor's morning prep, so the Service Board is
ready before first login (KPI: "advisor morning work done before shift start",
spec §1.3):

  dispatch_ro         — for each BOOK RO with no technician assigned, pick an
                        eligible technician (skills roster from PLAT_Users) and
                        recommend the assignment.
  stage_parts         — predict the RO's likely parts; where a predicted part is
                        not on-hand, recommend staging it (tied to the appt).
  reforecast_promise  — where the projected-ready time already runs past the
                        promised time, recommend re-forecasting the promise
                        before the customer is surprised.

Each autonomous action is gated on its OWN PLAT_ConfigAutonomy level
(a7.dispatch_ro / a7.stage_parts / a7.reforecast_promise) and, at WRITE levels,
lands as a DEALER_Recommendations row carrying the level in force — so the
dealer principal sees acceptance/overrides in Admin › Agents before raising
autonomy (spec §14 Q8). apply=False is a full dry run (computes, writes nothing).

  run_shop_load(market, apply, limit, dealer_id) -> summary of the morning work.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from ..common import ddb, autonomy_config
from . import booking as svc_booking

_SJ = "DEALER_ServiceJourneys"
_USERS = "PLAT_Users"
_RECS = "DEALER_Recommendations"

DISPATCH_ACTION = "a7.dispatch_ro"
STAGE_PARTS_ACTION = "a7.stage_parts"
REFORECAST_ACTION = "a7.reforecast_promise"
SOURCE = "agentic:shop_load (spec §6a; 05:00 morning work)"

# Technician roles eligible to be assigned a repair order.
_TECH_ROLES = frozenset({"dealer_technician", "dealer_service"})


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(dt: _dt.datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse(ts: str | None) -> _dt.datetime | None:
    if not ts:
        return None
    try:
        return _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _today_booked(market: str, dealer_id: str | None, env_prefix: str | None) -> list[dict[str, Any]]:
    """BOOK-stage service journeys promised for today (the morning work set).
    A journey with no promised_at still counts (needs prep either way)."""
    sjs = ddb.scan_filter(_SJ, "market_id", market, env_prefix)
    today = _now().date()
    out = []
    for s in sjs:
        if s.get("stage") != "BOOK":
            continue
        if dealer_id and s.get("dealer_id") != dealer_id:
            continue
        promised = _parse(s.get("promised_at"))
        # today's work, or anything already due/overdue that still sits in BOOK
        if promised is None or promised.date() <= today:
            out.append(s)
    return out


def _technicians(dealer_id: str, env_prefix: str | None) -> list[dict[str, Any]]:
    """Eligible service technicians for a dealer (skills roster from PLAT_Users)."""
    users = ddb.scan_all(_USERS, env_prefix)
    techs = []
    for u in users:
        groups = set(u.get("groups") or [])
        if groups & _TECH_ROLES and (u.get("dealer_id") == dealer_id or u.get("scope_node_id") == dealer_id):
            techs.append(u)
    return techs


def _rec_row(rec_id: str, action: str, level: str, sj: dict[str, Any],
             reason: str, now: str, extra: dict[str, Any]) -> dict[str, Any]:
    row = {
        "recommendation_id": rec_id,
        "action": action,
        "agent_id": "A7",
        "customer_id": sj.get("customer_id"),
        "service_journey_id": sj.get("service_journey_id"),
        "dealer_id": sj.get("dealer_id"),
        "shown": 0, "accepted": 0, "converted": 0,
        "shown_at": now,
        "autonomy_level": level,
        "reason": reason,
        "source_ref": SOURCE,
    }
    row.update(extra)
    return row


def run_shop_load(market: str = "us", apply: bool = False, limit: int | None = None,
                  dealer_id: str | None = None,
                  env_prefix: str | None = None) -> dict[str, Any]:
    """Run the 05:00 morning shop-load sweep (dispatch/stage-parts/reforecast).

    Reads the autonomy level PER sub-action; at a WRITE level it writes a
    DEALER_Recommendations card, otherwise it only reports. apply=False writes
    nothing regardless of level."""
    lvl_dispatch = autonomy_config.active_level(DISPATCH_ACTION, market, env_prefix)
    lvl_parts = autonomy_config.active_level(STAGE_PARTS_ACTION, market, env_prefix)
    lvl_reforecast = autonomy_config.active_level(REFORECAST_ACTION, market, env_prefix)

    booked = _today_booked(market, dealer_id, env_prefix)
    if limit is not None:
        booked = booked[:limit]

    now = _iso(_now())
    scanned = 0
    dispatched = staged = reforecast = written = 0
    results: list[dict[str, Any]] = []

    # The per-journey work delegates to the spec-named morning-work tools
    # (app/services/service/morning_work.py) — those are the source of truth;
    # shop_load is the sweep that calls them across today's booked ROs.
    from . import morning_work as mw

    for sj in booked:
        scanned += 1
        sj_id = sj.get("service_journey_id")
        dlr = sj.get("dealer_id")
        actions_for_sj: list[str] = []

        d = mw.dispatch_ro(sj, apply=apply, env_prefix=env_prefix)
        if d.get("technician_id"):
            dispatched += 1; actions_for_sj.append("dispatch_ro"); written += int(bool(d.get("written")))
        s = mw.stage_parts(sj, apply=apply, env_prefix=env_prefix)
        if s.get("predicted_services"):
            staged += 1; actions_for_sj.append("stage_parts"); written += int(bool(s.get("written")))
        r = mw.reforecast_promise(sj, apply=apply, env_prefix=env_prefix)
        if r.get("slip_minutes"):
            reforecast += 1; actions_for_sj.append("reforecast_promise"); written += int(bool(r.get("written")))

        if actions_for_sj:
            results.append({"service_journey_id": sj_id, "dealer_id": dlr, "actions": actions_for_sj})

    # Supervisor promotion: after the deterministic cards are computed,
    # one guarded Converse call may ORDER them (priority + a one-line rationale
    # each) — never add/remove/edit one. Local mode and any model failure keep
    # the deterministic order; `decision_engine` reports which path ran.
    results, decision_engine = mw.prioritize_cards(results, market=market, env_prefix=env_prefix)

    return {
        "job": "shop-load",
        "market": market,
        "dealer_id": dealer_id,
        "decision_engine": decision_engine,
        "autonomy_levels": {"dispatch_ro": lvl_dispatch, "stage_parts": lvl_parts,
                            "reforecast_promise": lvl_reforecast},
        "scanned_booked": scanned,
        "dispatch_recommendations": dispatched,
        "stage_parts_recommendations": staged,
        "reforecast_recommendations": reforecast,
        "recommendations_written": written,
        "applied": apply,
        "ran_at": now,
        "sample": results[:10],
    }
