"""§5.18.3/§5.18.6 — Journey ownership handoffs and the journey activity feed.

Ownership of a journey changes ONLY through a handoff (BDC → advisor → F&I →
delivery, or A7 → sales for a service-lane repurchase). A handoff is:

  create_handoff(journey_id, to_user, ...) -> DEALER_Handoffs row (pending) +
                                              a Task for the receiver +
                                              handoff.created event
  accept_handoff(handoff_id, user)         -> owner_user_id flips, the task
                                              completes, handoff.accepted +
                                              journey.owner_changed events
  activity_feed(journey_id, audience)      -> events + handoffs + tasks +
                                              recommendations merged newest-first;
                                              audience="customer" is the safe subset
"""
from __future__ import annotations

import datetime as _dt
import hashlib
from typing import Any

from ..common import ddb, labels, people, vehicles, resource_url as _ru
from ..events import bus as _bus
from ..platform import tasks as _tasks

_JOURNEYS = "DEALER_Journeys"
_HANDOFFS = "DEALER_Handoffs"
_EVENTS = "DEALER_JourneyEvents"


class NotFound(Exception):
    pass


class Forbidden(Exception):
    pass


class InvalidState(Exception):
    pass


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def create_handoff(journey_id: str, to_user: str, *, from_user: str, note: str = "",
                   stage: str | None = None, handoff_id: str | None = None,
                   env_prefix: str | None = None) -> dict[str, Any]:
    j = ddb.get_item(_JOURNEYS, {"journey_id": journey_id}, env_prefix)
    if j is None:
        raise NotFound(f"journey {journey_id} not found")
    ts = _now()
    hid = handoff_id or "HO-" + hashlib.sha1(f"{journey_id}|{to_user}|{ts}".encode()).hexdigest()[:12]
    task = _tasks.create_task(
        f"Accept handoff: {note or journey_id}", to_user, created_by=from_user,
        journey_id=journey_id, dealer_id=j.get("dealer_id"), sla_hours=4,
        source_agent="handoff", handoff_id=hid, payload={"handoff_id": hid}, env_prefix=env_prefix)
    row = {
        "handoff_id": hid, "journey_id": journey_id, "stage": stage or j.get("stage"),
        "from_user": from_user, "to_user": to_user, "note": note, "created_at": ts,
        "accepted_at": None, "task_id": task["task_id"], "dealer_id": j.get("dealer_id"),
    }
    ddb.put_item(_HANDOFFS, row, env_prefix)
    # The journey shows who it is on its way to until the receiver accepts: every screen
    # that prints the owner can say "Keisha → Elizabeth (handoff pending)" instead of
    # leaving the old owner on the card as if nothing happened.
    j = dict(j)
    j["pending_handoff"] = {"handoff_id": hid, "to_user": to_user, "from_user": from_user, "created_at": ts}
    j["updated_at"] = ts
    ddb.put_item(_JOURNEYS, j, env_prefix)
    row["bus_event"] = _bus.emit("handoff.created", {
        "handoff_id": hid, "journey_id": journey_id, "from_user": from_user,
        "to_user": to_user, "dealer_id": j.get("dealer_id"), "stage": row["stage"]},
        actor=from_user, resource_url=_ru.resource_url("journey", journey_id),
        env_prefix=env_prefix)
    return row


def accept_handoff(handoff_id: str, user_id: str, env_prefix: str | None = None) -> dict[str, Any]:
    h = ddb.get_item(_HANDOFFS, {"handoff_id": handoff_id}, env_prefix)
    if h is None:
        raise NotFound(f"handoff {handoff_id} not found")
    if h.get("to_user") != user_id:
        raise Forbidden("only the receiving user may accept a handoff")
    if h.get("accepted_at"):
        raise InvalidState(f"handoff {handoff_id} was already accepted")
    ts = _now()
    h = dict(h)
    h["accepted_at"] = ts
    ddb.put_item(_HANDOFFS, h, env_prefix)

    j = ddb.get_item(_JOURNEYS, {"journey_id": h["journey_id"]}, env_prefix)
    previous_owner = None
    if j is not None:
        j = dict(j)
        previous_owner = j.get("owner_user_id")
        j["owner_user_id"] = user_id
        j["updated_at"] = ts
        if (j.get("pending_handoff") or {}).get("handoff_id") in (None, handoff_id):
            j.pop("pending_handoff", None)
        ddb.put_item(_JOURNEYS, j, env_prefix)
    if h.get("task_id"):
        try:
            _tasks.update_task(h["task_id"], actor=user_id, status="done", env_prefix=env_prefix)
        except _tasks.NotFound:
            pass
    scope = {"dealer_id": h.get("dealer_id") or (j or {}).get("dealer_id")}
    events = [
        _bus.emit("handoff.accepted", {"handoff_id": handoff_id, "journey_id": h["journey_id"],
                                       "to_user": user_id, **scope},
                  actor=user_id, env_prefix=env_prefix),
        _bus.emit("journey.owner_changed", {"journey_id": h["journey_id"], "from_owner": previous_owner,
                                            "to_owner": user_id, "handoff_id": handoff_id, **scope},
                  actor=user_id, resource_url=_ru.resource_url("journey", h["journey_id"]),
                  env_prefix=env_prefix),
    ]
    return {**h, "owner_user_id": user_id, "previous_owner": previous_owner, "bus_events": events}


def list_handoffs(journey_id: str, env_prefix: str | None = None) -> list[dict[str, Any]]:
    rows = ddb.scan_filter(_HANDOFFS, "journey_id", journey_id, env_prefix)
    return sorted(rows, key=lambda r: r.get("created_at") or "")


# Fields that are internal-only; the customer-safe feed drops them (§5.18.6).
_CUSTOMER_SAFE_EVENT_TYPES = ("stage.advanced", "visit_booked", "build.saved",
                              "vehicle.delivered", "appointment.booked")


def activity_feed(journey_id: str, audience: str = "staff", limit: int = 50,
                  env_prefix: str | None = None) -> dict[str, Any]:
    """One merged, newest-first feed shared by Advisor, F&I, Delivery, Journeys
    card and (customer-safe) My Garage, so the chain reads identically everywhere."""
    items: list[dict[str, Any]] = []
    # every actor and recipient reads as a name (a person, an agent, or an agent acting for a person);
    # the raw ids stay on the item for links, never for display
    names = people.directory(env_prefix)
    trims = vehicles.trim_names(env_prefix)
    for e in ddb.query_hash(_EVENTS, "journey_id", journey_id, env_prefix):
        items.append({"kind": "event", "ts": e.get("event_ts"), "type": e.get("event_type"),
                      "actor": e.get("actor"), "actor_name": labels.actor_label(e.get("actor"), names),
                      "summary": labels.evidence_label(e.get("evidence"), trims)})
    for h in list_handoffs(journey_id, env_prefix):
        items.append({"kind": "handoff", "ts": h.get("created_at"), "type": "handoff.created",
                      "actor": h.get("from_user"), "actor_name": labels.actor_label(h.get("from_user"), names),
                      "to": h.get("to_user"), "to_name": labels.actor_label(h.get("to_user"), names),
                      "summary": labels.handoff_note_label(h.get("note")), "accepted_at": h.get("accepted_at"),
                      "handoff_id": h.get("handoff_id")})
        if h.get("accepted_at"):
            items.append({"kind": "handoff", "ts": h["accepted_at"], "type": "handoff.accepted",
                          "actor": h.get("to_user"), "actor_name": labels.actor_label(h.get("to_user"), names),
                          "handoff_id": h.get("handoff_id")})
    if audience == "staff":
        for t in ddb.scan_filter("DEALER_Tasks", "journey_id", journey_id, env_prefix):
            items.append({"kind": "task", "ts": t.get("created_at") or t.get("due_at"),
                          "type": f"task.{t.get('status')}", "actor": t.get("created_by"),
                          "actor_name": labels.actor_label(t.get("created_by"), names),
                          "summary": t.get("title"), "task_id": t.get("task_id")})
        for r in ddb.scan_filter("DEALER_Recommendations", "journey_id", journey_id, env_prefix):
            items.append({"kind": "agent", "ts": r.get("shown_at"), "type": "recommendation",
                          "actor": r.get("agent_id"), "actor_name": labels.actor_label(r.get("agent_id"), names),
                          "summary": labels.recommendation_label(r.get("action"), trims),
                          "recommendation_id": r.get("recommendation_id")})
    else:
        # customer-safe: milestones + who is helping them; no internal notes or agents
        items = [{"kind": i["kind"], "ts": i["ts"], "type": i["type"]}
                 for i in items
                 if (i["kind"] == "event" and (i.get("type") or "") in _CUSTOMER_SAFE_EVENT_TYPES)
                 or i["type"] == "handoff.accepted"]
    items.sort(key=lambda i: i.get("ts") or "", reverse=True)
    return {"journey_id": journey_id, "audience": audience, "count": len(items),
            "items": items[:limit]}
