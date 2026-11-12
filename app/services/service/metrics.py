"""Fixed-ops metrics — one definition each, read by every screen that shows the number.

The Store Cockpit's service tab, the Service Board, the Service Cockpit and the parts desk each
computed "promise at risk", "close rate", "parts wait" and "lane load" their own way, so a GM saw
1 promise at risk, the advisor's board 12 and the service manager "25% kept" for the same store
on the same morning. These are the definitions; the invariant sweep asserts the screens agree.

  lane_load        open visits (every stage before COMPLETED, FOLLOW_UP excluded): what is in the lane now
  promised_today / promised_this_week   visits whose promise falls in the window (a schedule, not a load)
  past_promise     promised time already behind us and the car not READY/FOLLOW_UP/COMPLETED
  at_risk          promise still ahead, projection runs past it
  promise_kept_rate  1 − (past + at risk) / open visits with a promise
  parts_hold       the visit's parts_hold flag OR an open held order on its RO — the parts desk and the
                   cockpit tile read the same set
  close_rate       estimates decided (approved, partially approved, declined): share not declined
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

OPEN_STAGES = ("BOOK", "CHECK_IN", "INSPECT", "DIAGNOSE", "APPROVE", "REPAIR", "READY")
IN_LANE = ("BOOK", "CHECK_IN", "INSPECT", "DIAGNOSE", "APPROVE", "REPAIR", "READY")
DONE_FOR_PROMISE = ("READY", "FOLLOW_UP", "COMPLETED")
DECIDED = ("approved", "partially_approved", "declined")
HELD_ORDER = ("ordered", "backordered", "back_ordered", "on_order", "shipped", "in_transit")


def parse(ts: Any) -> _dt.datetime | None:
    if not ts:
        return None
    try:
        d = _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=_dt.UTC)


def is_open(sj: dict[str, Any]) -> bool:
    return sj.get("stage") in OPEN_STAGES


def lane_load(sjs: list[dict[str, Any]]) -> int:
    return sum(1 for s in sjs if s.get("stage") in IN_LANE)


def promised_in(sjs: list[dict[str, Any]], start: _dt.date, end: _dt.date) -> int:
    out = 0
    for s in sjs:
        p = parse(s.get("promised_at"))
        if p and start <= p.date() <= end:
            out += 1
    return out


def promise_state(sj: dict[str, Any], now: _dt.datetime) -> str | None:
    """'past' | 'at_risk' | 'on_track' | None (no promise, or the car is done)."""
    if sj.get("stage") in DONE_FOR_PROMISE or not is_open(sj):
        return None
    promised, projected = parse(sj.get("promised_at")), parse(sj.get("projected_at"))
    if not promised:
        return None
    if now > promised:
        return "past"
    if projected and projected > promised:
        return "at_risk"
    return "on_track"


def past_promise(sjs: list[dict[str, Any]], now: _dt.datetime) -> list[dict[str, Any]]:
    return [s for s in sjs if promise_state(s, now) == "past"]


def at_risk(sjs: list[dict[str, Any]], now: _dt.datetime) -> list[dict[str, Any]]:
    return [s for s in sjs if promise_state(s, now) == "at_risk"]


def promise_kept_rate(sjs: list[dict[str, Any]], now: _dt.datetime) -> float | None:
    states = [st for st in (promise_state(s, now) for s in sjs) if st]
    return round(sum(1 for st in states if st == "on_track") / len(states), 3) if states else None


def parts_hold_ids(sjs: list[dict[str, Any]], ros: list[dict[str, Any]], orders: list[dict[str, Any]]) -> set[str]:
    """Visits held for parts: the flag, or an open held order on the visit's RO."""
    ro_sj = {r["ro_id"]: r.get("service_journey_id") for r in ros if r.get("ro_id")}
    held = {s["service_journey_id"] for s in sjs if s.get("parts_hold") and is_open(s)}
    open_ids = {s["service_journey_id"] for s in sjs if is_open(s)}
    for o in orders:
        if o.get("status") in HELD_ORDER and ro_sj.get(o.get("ro_id")) in open_ids:
            held.add(ro_sj[o["ro_id"]])
    return held


def close_rate(estimates: list[dict[str, Any]]) -> tuple[float | None, int]:
    decided = [e for e in estimates if e.get("status") in DECIDED]
    if not decided:
        return None, 0
    return round(sum(1 for e in decided if e.get("status") != "declined") / len(decided), 3), len(decided)


def summary(sjs: list[dict[str, Any]], ros: list[dict[str, Any]], orders: list[dict[str, Any]],
            estimates: list[dict[str, Any]], now: _dt.datetime | None = None) -> dict[str, Any]:
    """Every shared fixed-ops number for one scope, from the same rows."""
    now = now or _dt.datetime.now(_dt.UTC)
    week_start = (now - _dt.timedelta(days=now.weekday())).date()
    rate, decided = close_rate(estimates)
    return {
        "lane_load": lane_load(sjs),
        "open_visits": sum(1 for s in sjs if is_open(s)),
        "promised_today": promised_in(sjs, now.date(), now.date()),
        "promised_this_week": promised_in(sjs, week_start, week_start + _dt.timedelta(days=6)),
        "past_promise": len(past_promise(sjs, now)),
        "at_risk": len(at_risk(sjs, now)),
        "promise_kept_rate": promise_kept_rate(sjs, now),
        "parts_hold": len(parts_hold_ids(sjs, ros, orders)),
        "waiting_approval": sum(1 for s in sjs if s.get("stage") == "APPROVE"),
        "close_rate": rate, "decided_estimates": decided,
    }
