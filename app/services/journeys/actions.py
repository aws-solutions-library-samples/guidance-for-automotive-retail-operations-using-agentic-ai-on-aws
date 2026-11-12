"""A2 — action tools: book_test_drive, nudge, log_nba (spec §3.2).

The "smallest next commitment" write tools of the supervisor:

  book_test_drive(journey, slot, actor)  -> DEALER_Appointments + visit_booked event
  nudge(journey, target_commitment?)      -> the smallest next commitment + a
                                             one-click action descriptor
  log_nba(journey, actor)                 -> persist select_next_best_action's
                                             choice to DEALER_Recommendations

Grounding / invariants:
  * book_test_drive writes an appointment AND an append-only visit_booked event
    (nothing advances or is recorded without an event, §2.1) — event first.
  * nudge never fabricates: slots come from the dealer calendar (here the
    DEALER_Appointments sandbox / a generated near-future slot), links are route
    descriptors the UI renders as one-click actions.
  * log_nba records the chosen action and its alternatives with the context_hash
    so the §3.2 bandit can learn from outcomes (shown/accepted/converted).
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from ..common import ddb
from ..events import bus as _bus
from . import supervisor

_APPTS = "DEALER_Appointments"
_RECS = "DEALER_Recommendations"
_EVENTS = "DEALER_JourneyEvents"

# The commitment ladder (§3.2): save build -> test drive -> prequal -> trade -> sign.
# nudge picks the smallest commitment appropriate to the journey's stage.
_COMMITMENT_BY_STAGE = {
    "DISCOVER": ("save_build", "view_build"),
    "CONFIGURE": ("book_test_drive", "book_test_drive"),
    "QUALIFY": ("book_test_drive", "book_test_drive"),
    "FINANCE": ("start_prequal", "start_prequal"),
    "TRADE_IN": ("get_trade_estimate", "get_trade_estimate"),
    "AGREE": ("sign", "review_deal"),
    "DELIVER": ("schedule_delivery", "schedule_delivery"),
    "OWN": ("book_service", "book_service"),
}

# One-click action descriptors the UI turns into buttons/links.
_ACTION_ROUTES = {
    "save_build": {"kind": "link", "route": "/shop"},
    "view_build": {"kind": "link", "route": "/garage#builds"},
    "book_test_drive": {"kind": "slots", "route": "/journeys/{journey_id}/appointments"},
    "start_prequal": {"kind": "link", "route": "/garage#financing"},
    "get_trade_estimate": {"kind": "link", "route": "/garage#trade-in"},
    "schedule_delivery": {"kind": "link", "route": "/garage#appointments"},
    "book_service": {"kind": "link", "route": "/garage#service"},
    "sign": {"kind": "link", "route": "/garage#documents"},
    "review_deal": {"kind": "link", "route": "/garage#financing"},
}


class NotFound(Exception):
    pass


class InvalidSlot(Exception):
    pass


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(dt: _dt.datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --- book_test_drive --------------------------------------------------------

def _default_slot() -> tuple[str, str]:
    """A sensible next-business-day 1-hour slot when the caller supplies none.
    (In production this comes from the dealer calendar connector.)"""
    start = (_now() + _dt.timedelta(days=1)).replace(hour=16, minute=0, second=0, microsecond=0)
    return _iso(start), _iso(start + _dt.timedelta(hours=1))


def book_test_drive(journey: dict[str, Any], slot_start: str | None = None,
                    slot_end: str | None = None, actor: str = "A2",
                    env_prefix: str | None = None) -> dict[str, Any]:
    """Book a test-drive appointment for a journey and record visit_booked.

    Writes the appointment, then an append-only visit_booked event. Idempotency
    is intentionally NOT enforced (a customer may book more than one), but the
    appointment id is time-derived so a double-submit in the same second dedupes.
    """
    jid = journey.get("journey_id")
    if not jid:
        raise NotFound("journey has no id")
    if bool(slot_start) ^ bool(slot_end):
        raise InvalidSlot("provide both slot_start and slot_end, or neither")
    if not slot_start:
        slot_start, slot_end = _default_slot()

    ts = _now()
    market = (journey.get("market_id") or "us").upper()
    appt_id = f"AP-{market}-TD-{int(ts.timestamp())}"
    appt = {
        "appointment_id": appt_id,
        "type": "test_drive",
        "journey_id": jid,
        "customer_id": journey.get("customer_id"),
        "dealer_id": journey.get("dealer_id"),
        "advisor_user_id": journey.get("owner_user_id"),
        "slot_start": slot_start,
        "slot_end": slot_end,
        "status": "booked",
        "created_at": _iso(ts),
        "source_ref": f"A2.book_test_drive:{jid}",
    }
    # event first (audit can't lag state)
    ev = supervisor._event_row(jid, "visit_booked", actor,
                               "test drive booked", {"appointment_id": appt_id,
                               "slot_start": slot_start}, _iso(ts))
    ddb.put_item(_EVENTS, ev, env_prefix)
    ddb.put_item(_APPTS, appt, env_prefix)
    _bus.emit("appointment.booked", {"appointment_id": appt_id, "journey_id": jid, "type": "test_drive",
                                     "customer_id": journey.get("customer_id"),
                                     "dealer_id": journey.get("dealer_id"), "slot_start": slot_start},
              actor=actor, resource_url=f"/r/appointment/{appt_id}", env_prefix=env_prefix)
    return {"appointment_id": appt_id, "journey_id": jid, "type": "test_drive",
            "slot_start": slot_start, "slot_end": slot_end, "status": "booked",
            "event_id": ev["event_id"]}


# --- nudge ------------------------------------------------------------------

def _proposed_slots(n: int = 3) -> list[dict[str, str]]:
    """A few near-future test-drive slots (dealer-calendar stand-in)."""
    base = (_now() + _dt.timedelta(days=1)).replace(minute=0, second=0, microsecond=0)
    out = []
    for d in range(n):
        s = (base + _dt.timedelta(days=d)).replace(hour=16)
        out.append({"slot_start": _iso(s), "slot_end": _iso(s + _dt.timedelta(hours=1))})
    return out


def nudge(journey: dict[str, Any], target_commitment: str | None = None) -> dict[str, Any]:
    """Choose the smallest next commitment for a journey and attach a one-click
    action. Read-only: it proposes; booking/prequal happens on the customer's tap."""
    stage = journey.get("stage")
    default_commit, _alt = _COMMITMENT_BY_STAGE.get(stage, ("view_build", "view_build"))
    commitment = target_commitment or default_commit
    route = dict(_ACTION_ROUTES.get(commitment, {"kind": "link", "route": "/garage"}))
    if "route" in route:
        route["route"] = route["route"].format(journey_id=journey.get("journey_id"))

    action: dict[str, Any] = {"commitment": commitment, **route}
    if route.get("kind") == "slots":
        action["slots"] = _proposed_slots()

    return {
        "journey_id": journey.get("journey_id"),
        "stage": stage,
        "target_commitment": commitment,
        "one_click_action": action,
        "message": f"Smallest next step for a {stage} journey: {commitment.replace('_', ' ')}.",
    }


# --- log_nba (persist select_next_best_action) ------------------------------

def log_nba(journey: dict[str, Any], actor: str = "A2",
            apply: bool = True, env_prefix: str | None = None) -> dict[str, Any]:
    """Compute the next-best-action and log it as a recommendation.

    Persists the chosen action (and its alternatives + context bucket) to
    DEALER_Recommendations so the §3.2 bandit can compare candidates and learn
    from shown/accepted/converted outcomes. Deterministic id per journey so
    repeated runs upsert rather than pile up (idempotent)."""
    nba = supervisor.select_next_best_action(journey, env_prefix=env_prefix)
    jid = journey.get("journey_id")
    rec_id = f"REC-NBA-{jid}"
    row = {
        "recommendation_id": rec_id,
        "action": nba["chosen"].get("action"),
        "agent_id": "A2",
        "customer_id": journey.get("customer_id"),
        "journey_id": jid,
        "shown": 0, "accepted": 0, "converted": 0,
        "shown_at": _iso(_now()),
        "context_hash": nba["context_hash"],
        "route_to": nba["chosen"].get("route_to"),
        "channel": nba["chosen"].get("channel"),
        "alternatives": [a.get("action") for a in nba["alternatives"]],
        "reason": nba["chosen"].get("reason"),
        # Which engine chose (supervisor-promotion constraint): deterministic policy or the model's pick
        # among the gated candidates — kept on the row so the outcome loop can
        # compare the two engines' acceptance rates.
        "chosen_by": nba.get("chosen_by"),
        "rationale": nba.get("rationale"),
        "source_ref": f"A2.select_next_best_action:{jid}",
    }
    if apply:
        ddb.put_item(_RECS, row, env_prefix)
    return {"recommendation_id": rec_id, "chosen": nba["chosen"],
            "alternatives": nba["alternatives"], "context_hash": nba["context_hash"],
            # surface which engine chose (supervisor-promotion constraint) so a caller/UI never has to
            # guess whether the model or the deterministic policy picked
            "chosen_by": nba.get("chosen_by"), "rationale": nba.get("rationale"),
            "logged": apply}
