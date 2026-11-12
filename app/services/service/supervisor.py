"""A7 — Service Journey Orchestration (supervisor core), spec §2.1.1 / §3.7.

A7 owns the service-journey state machine (the second supervisor; A2 owns
purchase). This is its deterministic spine:

  validate_service_handoff(sj)              -> per-stage required-context gate
  advance_service_journey(sj_id, ...)       -> guarded transition + append-only event
  record_approval(sj_id, decision, ...)     -> customer estimate approve/decline (APPROVE)
  get_service_state(sj_id)                  -> supervisor view (stepper + gate + events)

Invariants (spec §2.1.1), mirroring A2:
  * Stage order BOOK→CHECK_IN→INSPECT→DIAGNOSE→APPROVE→REPAIR→READY→FOLLOW_UP
    (→COMPLETED terminal). One stage at a time.
  * Nothing advances without an event in DEALER_ServiceJourneyEvents — event
    written BEFORE the journey row flips.
  * validate_service_handoff blocks a move whose current stage lacks its
    required context. The APPROVE gate enforces the §2.1.1 rule: an RO cannot
    enter/leave APPROVE with an estimate that has no total/price source.
  * Every number shown comes from a record; A7 invents nothing.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
from typing import Any

from ..common import ddb
from ..events import bus as _bus

_SJ = "DEALER_ServiceJourneys"
_EVENTS = "DEALER_ServiceJourneyEvents"
_ESTIMATES = "DEALER_ServiceEstimates"
_INSPECTIONS = "DEALER_Inspections"
_RO = "DEALER_RepairOrders"

SERVICE_STAGES = [
    "BOOK", "CHECK_IN", "INSPECT", "DIAGNOSE", "APPROVE",
    "REPAIR", "READY", "FOLLOW_UP", "COMPLETED",
]

DECISIONS = ("approve", "decline")


class NotFound(Exception):
    pass


class HandoffIncomplete(Exception):
    def __init__(self, stage: str, missing: list[str]):
        self.stage, self.missing = stage, missing
        from ..common.labels import missing_message
        super().__init__(missing_message(stage, missing))


class InvalidTransition(Exception):
    pass


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _next_stage(stage: str) -> str | None:
    try:
        i = SERVICE_STAGES.index(stage)
    except ValueError:
        return None
    return SERVICE_STAGES[i + 1] if i + 1 < len(SERVICE_STAGES) else None


# --- validate_service_handoff -----------------------------------------------

def validate_service_handoff(sj: dict[str, Any], env_prefix: str | None = None) -> dict[str, Any]:
    """Check the CURRENT stage's required context (§2.1.1).

    Rules grounded in the service tables:
      CHECK_IN -> INSPECT   : a repair order must be open (RO mirrored)
      INSPECT  -> DIAGNOSE  : at least one inspection finding recorded
      APPROVE  -> REPAIR    : an estimate exists with a total AND a source_ref
                              (no line priced without a source), and it is not
                              still pending a customer decision
      others                : advance freely (BOOK/DIAGNOSE/REPAIR/READY/FOLLOW_UP)
    """
    stage = sj.get("stage")
    sj_id = sj.get("service_journey_id")
    missing: list[str] = []

    if stage == "CHECK_IN":
        ros = ddb.scan_filter(_RO, "service_journey_id", sj_id, env_prefix)
        if not ros:
            missing.append("repair_order")
    elif stage == "INSPECT":
        insp = ddb.scan_filter(_INSPECTIONS, "service_journey_id", sj_id, env_prefix)
        if not insp:
            missing.append("inspection_findings")
    elif stage == "APPROVE":
        ests = ddb.scan_filter(_ESTIMATES, "service_journey_id", sj_id, env_prefix)
        if not ests:
            missing.append("estimate")
        else:
            priced = [e for e in ests if e.get("total") is not None and e.get("source_ref")]
            if not priced:
                missing.append("estimate_priced_with_source")
            # must have a recorded customer decision on the CURRENT estimate to leave
            # APPROVE (an older, already-decided estimate does not count)
            latest = max(ests, key=lambda e: e.get("updated_at") or e.get("created_at") or e.get("presented_at") or "")
            if latest.get("status") not in ("approved", "declined", "partially_approved"):
                missing.append("customer_decision")
            for line in latest.get("lines") or []:
                # §2.1.1: no line may enter/leave APPROVE without labor op, price source, coverage check
                if not (line.get("labor_op_id") and line.get("labor_source_ref") and line.get("coverage")):
                    missing.append(f"line_unsourced:{line.get('line_id')}")

    return {"stage": stage, "missing": missing, "ok": not missing}


# --- advance_service_journey ------------------------------------------------

def _event_row(sj_id: str, event_type: str, actor: str, evidence: str,
               payload: dict[str, Any], ts: str) -> dict[str, Any]:
    """Compose a DEALER_ServiceJourneyEvents row (composite key service_journey_id
    + sk). sk mirrors the corpus: <event_ts>#<event_type>#<seq>."""
    return {
        "service_journey_id": sj_id,
        "sk": f"{ts}#{event_type}#0000",
        "event_ts": ts,
        "event_type": event_type,
        "actor": {"type": "agent", "agent": "A7", "user_id": actor},
        "evidence": evidence,
        "payload": payload,
    }


def advance_service_journey(sj_id: str, evidence: str, actor: str,
                            to_stage: str | None = None,
                            env_prefix: str | None = None) -> dict[str, Any]:
    """Advance a service journey one stage, guarded by validate_service_handoff.

    Event is written first (audit can't lag state), then the journey row flips.
    """
    sj = ddb.get_item(_SJ, {"service_journey_id": sj_id}, env_prefix)
    if sj is None:
        raise NotFound(f"service journey {sj_id} not found")

    cur = sj.get("stage")
    nxt = _next_stage(cur)
    if nxt is None:
        raise InvalidTransition(f"{sj_id} is at terminal stage {cur}")
    if to_stage is not None and to_stage != nxt:
        raise InvalidTransition(f"can only advance {cur} -> {nxt} (one stage at a time)")

    check = validate_service_handoff(sj, env_prefix)
    if not check["ok"]:
        raise HandoffIncomplete(cur, check["missing"])

    ts = _now()
    ev = _event_row(sj_id, "service.stage.advanced", actor, evidence,
                    {"from": cur, "to": nxt}, ts)
    ddb.put_item(_EVENTS, ev, env_prefix)

    sj["stage"] = nxt
    sj["updated_at"] = ts
    ddb.put_item(_SJ, sj, env_prefix)
    bus_event = _bus.emit("service.stage_changed", {
        "service_journey_id": sj_id, "customer_id": sj.get("customer_id"),
        "dealer_id": sj.get("dealer_id"), "vin": sj.get("vin"), "market_id": sj.get("market_id"),
        "from": cur, "to": nxt}, actor=actor,
        resource_url=f"/r/service_journey/{sj_id}", env_prefix=env_prefix)
    return {"service_journey_id": sj_id, "from": cur, "to": nxt, "advanced_at": ts,
            "event_sk": ev["sk"], "bus_event": bus_event}


# --- record_approval (customer estimate decision at APPROVE) -----------------

def record_approval(sj_id: str, decision: str, actor: str,
                    estimate_id: str | None = None,
                    env_prefix: str | None = None) -> dict[str, Any]:
    """Record the customer's estimate decision at APPROVE (approve|decline).

    Sets the estimate status and writes an approval event. Does NOT advance the
    stage — advance_service_journey does, gated on this decision being present.
    """
    if decision not in DECISIONS:
        raise InvalidTransition(f"decision must be one of {DECISIONS}, got {decision!r}")
    sj = ddb.get_item(_SJ, {"service_journey_id": sj_id}, env_prefix)
    if sj is None:
        raise NotFound(f"service journey {sj_id} not found")

    ests = ddb.scan_filter(_ESTIMATES, "service_journey_id", sj_id, env_prefix)
    if not ests:
        raise NotFound(f"no estimate to decide on for {sj_id}")
    est = next((e for e in ests if e.get("estimate_id") == estimate_id), ests[0])

    new_status = "approved" if decision == "approve" else "declined"
    est = dict(est)
    est["status"] = new_status
    est["decided_by"] = actor
    est["decided_at"] = _now()
    ddb.put_item(_ESTIMATES, est, env_prefix)

    ts = _now()
    ev = _event_row(sj_id, f"estimate.{new_status}", actor,
                    f"estimate {est['estimate_id']} {new_status}",
                    {"estimate_id": est["estimate_id"], "total": est.get("total")}, ts)
    ddb.put_item(_EVENTS, ev, env_prefix)
    bus_event = _bus.emit("estimate.line_decided", {
        "service_journey_id": sj_id, "estimate_id": est["estimate_id"],
        "decision": new_status, "decided_by": actor, "dealer_id": sj.get("dealer_id"),
        "customer_id": sj.get("customer_id")}, actor=actor,
        resource_url=f"/r/estimate/{est['estimate_id']}", env_prefix=env_prefix)
    return {"service_journey_id": sj_id, "estimate_id": est["estimate_id"],
            "decision": decision, "estimate_status": new_status, "decided_by": actor,
            "bus_event": bus_event}


# --- handoff_to_sales (repurchase/upgrade handoff, §2.1.1) ------------------

_JOURNEYS = "DEALER_Journeys"
_HANDOFFS = "DEALER_Handoffs"


def _sales_receiver(sj: dict[str, Any], actor: str, previous_id: str | None,
                    env_prefix: str | None) -> str:
    """Who on the sales floor receives a service-lane handoff: a person, always.

    The customer's own sales advisor first, then whoever sold them the car, then the person
    handing off when they are a floor advisor, then the visit's advisor when that is a floor
    advisor, then the store's first floor advisor. Never an agent id ("A2"), which no one can
    accept, and never the service advisor, who does not work the sale."""
    users = {u["user_id"]: u for u in ddb.scan_all("PLAT_Users", env_prefix) if u.get("user_id")}
    dealer = sj.get("dealer_id")

    def floor_advisor(uid: Any) -> bool:
        u = users.get(uid) if uid else None
        return bool(u) and "dealer_advisor" in (u.get("groups") or []) and u.get("active", True) \
            and (not dealer or u.get("dealer_id") in (None, dealer))
    cust = ddb.get_item("DEALER_Customers", {"customer_id": sj.get("customer_id")}, env_prefix) if sj.get("customer_id") else None
    prev = ddb.get_item(_JOURNEYS, {"journey_id": previous_id}, env_prefix) if previous_id else None
    for uid in ((cust or {}).get("assigned_advisor_user_id"), (prev or {}).get("owner_user_id"),
                actor, sj.get("advisor_id")):
        if floor_advisor(uid):
            return uid
    store = sorted(uid for uid in users if floor_advisor(uid) and users[uid].get("dealer_id") == dealer)
    return store[0] if store else (sj.get("advisor_id") or actor)


def handoff_to_sales(sj_id: str, reason: str, actor: str,
                     env_prefix: str | None = None) -> dict[str, Any]:
    """Create a purchase journey from a service journey (§2.1.1).

    The service→sale loop: when a FOLLOW_UP service journey shows positive
    equity, lease-end, or a repair-vs-replace trigger, A7 hands off to sales.
    Creates a new DEALER_Journeys row in DISCOVER with source=service_lane and
    previous_journey_id set (the service journey's linked purchase journey, if
    any), writes a DEALER_Handoffs row, and emits a service.handoff_to_sales
    event on the service journey. A2 then owns the new purchase journey; A7
    keeps the service journey (it never advances the sale itself).
    """
    sj = ddb.get_item(_SJ, {"service_journey_id": sj_id}, env_prefix)
    if sj is None:
        raise NotFound(f"service journey {sj_id} not found")

    ts = _now()
    customer_id = sj.get("customer_id")
    # §2.1 single-active rule: OWN is a resting state and does not count; any
    # other active purchase journey absorbs the handoff instead of a duplicate.
    existing = [j for j in (ddb.scan_filter(_JOURNEYS, "customer_id", customer_id, env_prefix)
                            if customer_id else [])
                if j.get("stage") != "OWN" and (j.get("status") or "active") == "active"
                and not j.get("superseded_by")]
    previous_id = sj.get("purchase_journey_id")
    created = not existing
    receiver = _sales_receiver(sj, actor, previous_id, env_prefix)
    if existing:
        target = sorted(existing, key=lambda j: j.get("updated_at") or "", reverse=True)[0]
        new_jid = target["journey_id"]
        target = dict(target)
        ctx = dict(target.get("context") or {})
        ctx.update({"service_journey_id": sj_id, "handoff_reason": reason})
        target["context"] = ctx
        target["updated_at"] = ts
        ddb.put_item(_JOURNEYS, target, env_prefix)
    else:
        new_jid = f"J-{(sj.get('market_id') or 'us').upper()}-SVC-{sj_id.split('-')[-1]}"
        new_journey = {
            "journey_id": new_jid,
            "stage": "DISCOVER",
            "source": "service_lane",
            "status": "active",
            "customer_id": customer_id,
            "dealer_id": sj.get("dealer_id"),
            "market_id": sj.get("market_id"),
            "previous_journey_id": previous_id,
            "owner_user_id": receiver,
            "context": {"service_journey_id": sj_id, "handoff_reason": reason},
            "created_at": ts,
            "updated_at": ts,
            "last_event_at": ts,
            "source_ref": f"A7.handoff_to_sales:{sj_id}",
        }
        ddb.put_item(_JOURNEYS, new_journey, env_prefix)
        # the OWN journey is kept for history/attribution and marked superseded
        if previous_id:
            prev = ddb.get_item(_JOURNEYS, {"journey_id": previous_id}, env_prefix)
            if prev and prev.get("stage") == "OWN" and not prev.get("superseded_by"):
                prev = dict(prev)
                prev["superseded_by"] = new_jid
                prev["updated_at"] = ts
                ddb.put_item(_JOURNEYS, prev, env_prefix)
        _bus.emit("journey.created", {"journey_id": new_jid, "customer_id": customer_id,
                                      "dealer_id": sj.get("dealer_id"), "source": "service_lane",
                                      "previous_journey_id": previous_id},
                  actor=actor, resource_url=f"/r/journey/{new_jid}", env_prefix=env_prefix)

    from ..journeys import handoffs as _handoffs
    handoff = _handoffs.create_handoff(
        new_jid, receiver, from_user=f"A7:{actor}",
        note=f"service_lane handoff: {reason}", stage="DISCOVER",
        handoff_id=f"HANDOFF-{sj_id}", env_prefix=env_prefix)
    handoff_id = handoff["handoff_id"]

    ev = _event_row(sj_id, "service.handoff_to_sales", actor, reason,
                    {"new_journey_id": new_jid, "handoff_id": handoff_id}, ts)
    ddb.put_item(_EVENTS, ev, env_prefix)
    bus_event = _bus.emit("service.handoff_to_sales", {
        "service_journey_id": sj_id, "journey_id": new_jid, "handoff_id": handoff_id,
        "customer_id": customer_id, "dealer_id": sj.get("dealer_id"), "reason": reason},
        actor=actor, resource_url=f"/r/journey/{new_jid}", env_prefix=env_prefix)

    return {"service_journey_id": sj_id, "new_journey_id": new_jid,
            "handoff_id": handoff_id, "reason": reason, "source": "service_lane",
            "created_journey": created, "task_id": handoff.get("task_id"),
            "bus_event": bus_event}


# --- get_service_state ------------------------------------------------------

def _stepper(current: str | None) -> list[dict[str, Any]]:
    out, reached = [], True
    for st in SERVICE_STAGES:
        is_cur = st == current
        out.append({"stage": st, "current": is_cur, "reached": reached})
        if is_cur:
            reached = False
    return out


def get_service_state(sj_id: str, event_limit: int = 10,
                      env_prefix: str | None = None) -> dict[str, Any]:
    """The A7 supervisor view of one service journey."""
    sj = ddb.get_item(_SJ, {"service_journey_id": sj_id}, env_prefix)
    if sj is None:
        raise NotFound(f"service journey {sj_id} not found")

    events = ddb.query_hash(_EVENTS, "service_journey_id", sj_id, env_prefix)
    events.sort(key=lambda e: e.get("event_ts") or "", reverse=True)
    recent = [{"event_ts": e.get("event_ts"), "event_type": e.get("event_type"),
               "actor": e.get("actor"), "evidence": e.get("evidence")} for e in events[:event_limit]]

    return {
        "service_journey_id": sj_id,
        "stage": sj.get("stage"),
        "vin": sj.get("vin"),
        "dealer_id": sj.get("dealer_id"),
        "customer_id": sj.get("customer_id"),
        "advisor_id": sj.get("advisor_id"),
        "technician_id": sj.get("technician_id"),
        "promised_at": sj.get("promised_at"),
        "projected_at": sj.get("projected_at"),
        "parts_hold": sj.get("parts_hold"),
        "stepper": _stepper(sj.get("stage")),
        "handoff": validate_service_handoff(sj, env_prefix),
        "next_stage": _next_stage(sj.get("stage")),
        "event_count": len(events),
        "recent_events": recent,
    }
