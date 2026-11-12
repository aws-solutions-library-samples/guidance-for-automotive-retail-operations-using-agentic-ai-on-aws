"""A2 — detect_intent_shift + create_task (spec §3.2, §5.18.4).

The signal-watching half of A2 nurture: it reads a journey's recent events,
messages, and CDP signals and flags an intent shift the plan should react to —
budget change, a new competitor considered, gone cold, or ready to buy — then
(optionally) opens a dealer task so a human sees it. This is the
"stalled journey -> detect_intent_shift -> schedule_outreach + dealer task"
trigger in §3.2.

  detect_intent_shift(journey)          -> {shifts[], primary, evidence, recommend}
  create_task(assignee, journey_id, ..) -> a DEALER_Tasks row (the dealer alert)

Grounding: every flag cites the concrete signal/event that fired it (with its
timestamp), so the alert a human sees is explainable and never fabricated.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from ..common import ddb
from ..events import bus as _bus

_SIGNALS = "DEALER_Signals"
_EVENTS = "DEALER_JourneyEvents"
_MESSAGES = "CUST_Messages"
_TASKS = "DEALER_Tasks"

# Windows (days). Matches §3.3 "went cold = 14+ days no activity".
COLD_DAYS = float(14)
RECENT_DAYS = float(10)

# High-intent event types => ready_to_buy.
_READY_EVENTS = {"prequal.requested", "prequal.decided", "trade.accepted",
                 "deal.structured", "appointment.booked", "vehicle_presented"}
# High-intent signal types => ready_to_buy / active shopping.
_READY_SIGNALS = {"test_drive_slot_view", "configure_save", "price_check"}


class NotFound(Exception):
    pass


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


def detect_intent_shift(journey: dict[str, Any], env_prefix: str | None = None) -> dict[str, Any]:
    """Flag intent shifts for a journey from its events, messages, and signals.

    Returns {journey_id, shifts:[{kind, evidence, at}], primary, recommend,
    last_activity_at}. `recommend` is the A2 next move (re-plan / handoff /
    dealer alert), grounded in which shift fired."""
    jid = journey.get("journey_id")
    cid = journey.get("customer_id")
    if not jid:
        raise NotFound("journey has no id")
    at = _now()

    events = ddb.query_hash(_EVENTS, "journey_id", jid, env_prefix)
    signals = ddb.scan_filter(_SIGNALS, "customer_id", cid, env_prefix) if cid else []
    msgs = ddb.scan_filter(_MESSAGES, "customer_id", cid, env_prefix) if cid else []

    # last activity across all three streams
    stamps = ([_parse(e.get("event_ts")) for e in events]
              + [_parse(s.get("signal_ts")) for s in signals]
              + [_parse(m.get("sent_at")) for m in msgs])
    stamps = [s for s in stamps if s]
    last_activity = max(stamps) if stamps else None
    idle_days = (at - last_activity).total_seconds() / 86400 if last_activity else None

    shifts: list[dict[str, Any]] = []

    # 1) competitor_considered — a compare signal naming a rival.
    comps = [s for s in signals if s.get("type") == "compare" and s.get("compare_with")]
    if comps:
        latest = max(comps, key=lambda s: s.get("signal_ts") or "")
        shifts.append({"kind": "competitor_considered",
                       "evidence": f"compared with {latest.get('compare_with')}",
                       "at": latest.get("signal_ts")})

    # 2) ready_to_buy — a recent high-intent event or signal.
    recent_cut = at - _dt.timedelta(days=RECENT_DAYS)
    ready_ev = [e for e in events if e.get("event_type") in _READY_EVENTS
                and (_parse(e.get("event_ts")) or at) >= recent_cut]
    ready_sig = [s for s in signals if s.get("type") in _READY_SIGNALS
                 and (_parse(s.get("signal_ts")) or at) >= recent_cut]
    if ready_ev or ready_sig:
        src = (max(ready_ev, key=lambda e: e.get("event_ts") or "") if ready_ev
               else max(ready_sig, key=lambda s: s.get("signal_ts") or ""))
        label = src.get("event_type") or src.get("type")
        shifts.append({"kind": "ready_to_buy",
                       "evidence": f"recent high-intent activity: {label}",
                       "at": src.get("event_ts") or src.get("signal_ts")})

    # 3) budget_change — repeated price_check signals (re-evaluating affordability).
    price_checks = [s for s in signals if s.get("type") == "price_check"]
    if len(price_checks) >= 2:
        latest = max(price_checks, key=lambda s: s.get("signal_ts") or "")
        shifts.append({"kind": "budget_change",
                       "evidence": f"{len(price_checks)} price checks (re-evaluating budget)",
                       "at": latest.get("signal_ts")})

    # 4) went_cold — no activity for COLD_DAYS (and not already flagged ready).
    if idle_days is not None and idle_days >= COLD_DAYS and not ready_ev and not ready_sig:
        shifts.append({"kind": "went_cold",
                       "evidence": f"no activity for {round(idle_days)} days",
                       "at": _iso(last_activity) if last_activity else None})

    # primary shift priority: ready_to_buy > competitor > budget > cold.
    priority = ["ready_to_buy", "competitor_considered", "budget_change", "went_cold"]
    primary = next((k for k in priority if any(s["kind"] == k for s in shifts)), None)

    recommend = {
        "ready_to_buy": {"action": "handoff_to_human", "reason": "hot — route to advisor now"},
        "competitor_considered": {"action": "schedule_outreach",
                                  "reason": "counter-competitor talk track; re-plan nurture"},
        "budget_change": {"action": "schedule_outreach",
                          "reason": "re-plan with payment/lease options"},
        "went_cold": {"action": "schedule_outreach", "reason": "re-engagement angle from last interest"},
    }.get(primary, {"action": "none", "reason": "no shift detected"})

    return {
        "journey_id": jid,
        "customer_id": cid,
        "shifts": shifts,
        "primary": primary,
        "recommend": recommend,
        "last_activity_at": _iso(last_activity) if last_activity else None,
        "idle_days": round(idle_days, 1) if idle_days is not None else None,
    }


def create_task(assignee: str, journey_id: str, title: str, due_at: str | None = None,
                actor: str = "A2", env_prefix: str | None = None) -> dict[str, Any]:
    """Open a dealer task (the §3.2 "dealer alert" output). Deterministic id per
    (journey, assignee) so repeated alerts upsert rather than pile up."""
    now = _now()
    if not due_at:
        due_at = _iso(now + _dt.timedelta(days=2))
    task_id = f"TASK-{journey_id}-{assignee}"
    row = {
        "task_id": task_id,
        "assigned_user_id": assignee,
        "journey_id": journey_id,
        "title": title,
        "due_at": due_at,
        "status": "open",
        "created_at": _iso(now),
        "source_ref": f"A2.create_task:{journey_id}",
    }
    ddb.put_item(_TASKS, row, env_prefix)
    _bus.emit("task.created", {"task_id": task_id, "assigned_user_id": assignee,
                               "journey_id": journey_id, "title": title, "due_at": due_at},
              actor=actor, resource_url=f"/r/task/{task_id}", env_prefix=env_prefix)
    return {"task_id": task_id, "assigned_user_id": assignee, "journey_id": journey_id,
            "title": title, "due_at": due_at, "status": "open"}
