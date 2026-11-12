"""L6/§8 — Event backbone: publisher + the vehicle.delivered subscriber.

The event bus is how state changes propagate across the two supervisors without
point-to-point coupling (spec §8). The canonical crossing point: when the
purchase side delivers a vehicle (A2), the service side (A7) picks it up off the
bus and opens the first service journey — the two supervisors meet through
events, never by driving each other.

  publish(detail_type, detail)         -> put a typed event on the bus
  on_vehicle_delivered(detail)         -> A7: open the first service journey
  dispatch_local(detail_type, detail)  -> run the matching subscriber in-process
                                          (local stand-in for the EventBridge rule)

Events are typed `<name>@v1` in the detail; the deployed EventBridge rule matches
on detail-type, and locally dispatch_local routes the same way so behavior matches.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any

from ..common import ddb, envname

BUS_NAME = envname.event_bus()      # RETAIL_OPS_EVENT_BUS, else app-bus-<env>
EVENT_SOURCE = "retail-ops.journeys"
REGION = os.environ.get("AWS_REGION", "us-east-1")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --- event catalog v1 (spec §4.6) --------------------------------------------
# Every type a stack may emit. emit() refuses anything else so the catalog stays
# the contract (schema-registry stand-in); add a type here before emitting it.
CATALOG: frozenset[str] = frozenset({
    # Journey
    "journey.created", "journey.stage_changed", "journey.stalled", "journey.owner_changed",
    "handoff.created", "handoff.accepted",
    # Customer
    "lead.received", "lead.predictive_raised", "signal.recorded", "consent.changed", "message.inbound", "message.outbound",
    # Commerce
    "build.saved", "appointment.booked", "appointment.changed", "appointment.cancelled",
    "prequal.requested", "prequal.decided", "trade.valued", "trade.accepted",
    "deal.structured", "deal.pushed", "deal.booked", "vehicle.delivered",
    # Service (Process 2)
    "service.journey.created", "service.stage_changed", "service.appointment.booked",
    "service.appointment.changed", "service.appointment.cancelled",
    "ro.opened", "ro.updated", "ro.closed", "inspection.submitted",
    "estimate.presented", "estimate.line_decided", "service.declined_work.recorded",
    "parts.ordered", "parts.received", "parts.backordered", "ro.line_completed",
    "vehicle.ready", "service.visit.completed", "service.equity_flag",
    "service.handoff_to_sales", "recall.detected",
    # Field (OEM)
    "alert.raised", "alert.acknowledged", "alert.resolved", "contact_report.filed",
    "action_plan.created", "action_plan.milestone_updated", "action_plan.milestone_due",
    "plan.generated", "plan.published", "guardrail.blocked", "rdr.updated",
    # Agentic
    "discovery.match_found", "equity.crossed_positive", "lease.end_window_entered",
    "repair_vs_replace_triggered", "acquisition.recommended",
    "recommendation.shown", "recommendation.accepted", "recommendation.converted",
    "autonomy.published", "autonomy.promoted", "autonomy.demoted",
    "inventory.changed", "incentive.changed", "telematics.alert",
    "campaign.spend_updated",
    # Marketing Studio (added 2026-10-05: audience export to ad platforms, §5.16)
    "audience.exported",
    # System
    "notification.requested", "task.created", "task.assigned", "task.completed", "task.overdue",
    "approval.requested", "approval.decided", "document.created", "connector.sync_failed",
    "config.published",
})


class UnknownEventType(ValueError):
    pass


def _current_correlation_id() -> str | None:
    from ..common import trace
    return trace.CURRENT_CORRELATION_ID.get()


def _live() -> bool:
    return os.environ.get("RETAIL_OPS_EVENT_BUS_LIVE", "0") == "1"


def envelope(event_type: str, detail: dict[str, Any], *, actor: dict[str, Any] | str | None = None,
             scope: dict[str, Any] | None = None, resource_url: str | None = None,
             correlation_id: str | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
    """The §4.6 event envelope: {event_id, type, ts, actor, scope, resource_url,
    correlation_id, idempotency_key} merged over the domain detail."""
    import hashlib
    ts = _now()
    if isinstance(actor, str):
        actor = {"type": "user", "user_id": actor}
    key = idempotency_key or hashlib.sha1(
        f"{event_type}|{json.dumps(detail, sort_keys=True, default=str)}".encode()).hexdigest()[:20]
    out = dict(detail)
    out.update({
        "event_id": out.get("event_id") or f"EVT-{key}",
        "type": f"{event_type}@v1",
        "event_type": f"{event_type}@v1",
        "ts": ts,
        "actor": actor or out.get("actor") or {"type": "system"},
        "scope": scope or {k: out[k] for k in ("dealer_id", "district_id", "region_id") if out.get(k)},
        "resource_url": resource_url or out.get("resource_url"),
        "correlation_id": correlation_id or out.get("correlation_id") or _current_correlation_id(),
        "idempotency_key": key,
    })
    return out


# Events emitted in this process (newest last). Local-mode observability and the
# test hook for "this state change emitted that event"; bounded.
EMITTED: list[dict[str, Any]] = []
_EMITTED_MAX = 500


def emit(event_type: str, detail: dict[str, Any], *, env_prefix: str | None = None,
         **envelope_kw: Any) -> dict[str, Any]:
    """Emit one catalogued domain event after a state change has been written.

    Live (RETAIL_OPS_EVENT_BUS_LIVE=1): put it on EventBridge; the rule fans out to the
    subscriber Lambda. Local: dispatch the subscriber in-process (same code).
    Never raises on transport failure — the state change is already committed,
    so the failure is returned for the caller to surface rather than undoing it.
    """
    if event_type not in CATALOG:
        raise UnknownEventType(f"{event_type!r} is not in the §4.6 event catalog")
    env = envelope(event_type, detail, **envelope_kw)
    EMITTED.append(env)
    del EMITTED[:-_EMITTED_MAX]
    try:
        if _live():
            return {"mode": "bus", "event_id": env["event_id"], **publish(event_type, env)}
        return {"mode": "local-dispatch", "event_id": env["event_id"],
                "result": dispatch_local(event_type, env, env_prefix)}
    except Exception as e:  # transport failure must not undo the committed write
        return {"mode": "bus" if _live() else "local-dispatch", "event_id": env["event_id"],
                "published": False, "error": f"{type(e).__name__}: {e}"}


def publish(detail_type: str, detail: dict[str, Any]) -> dict[str, Any]:
    """Put one typed event on the EventBridge bus."""
    import boto3
    eb = boto3.client("events", region_name=REGION)
    payload = dict(detail)
    payload.setdefault("event_type", f"{detail_type}@v1")
    payload.setdefault("published_at", _now())
    resp = eb.put_events(Entries=[{
        "Source": EVENT_SOURCE,
        "DetailType": detail_type,
        "Detail": json.dumps(payload),
        "EventBusName": BUS_NAME,
    }])
    return {"published": resp.get("FailedEntryCount", 0) == 0,
            "detail_type": detail_type, "failed": resp.get("FailedEntryCount", 0)}


# --- subscribers ------------------------------------------------------------

def on_vehicle_delivered(detail: dict[str, Any], env_prefix: str | None = None) -> dict[str, Any]:
    """A7 reacts to a delivered vehicle: open the first service journey (BOOK).

    Idempotent: keyed SJ-DLV-<journey_id>. This is the sale -> first-service
    handoff (§4 first lap), the point where A2 hands to A7 via the bus."""
    journey_id = detail.get("journey_id")
    customer_id = detail.get("customer_id")
    dealer_id = detail.get("dealer_id")
    market = detail.get("market_id", "us")
    if not journey_id:
        return {"handled": False, "reason": "no journey_id in event"}

    sj_id = f"SJ-DLV-{journey_id}"
    # resolve the delivered vehicle's VIN if we can (customer's newest vehicle)
    vin = None
    vehicles = ddb.scan_filter("DEALER_Vehicles", "customer_id", customer_id, env_prefix) if customer_id else []
    if vehicles:
        vin = sorted(vehicles, key=lambda v: v.get("delivered_at") or "", reverse=True)[0].get("vin")

    row = {
        "service_journey_id": sj_id,
        "stage": "BOOK",
        "customer_id": customer_id,
        "dealer_id": dealer_id,
        "vin": vin,
        "purchase_journey_id": journey_id,
        "advisor_id": None,
        "technician_id": None,
        "parts_hold": False,
        "wait_type": "drop",
        "market_id": market,
        "opened_at": _now(),
        "golden_story": None,
        "source_ref": "agentic:first_service_reminder (event vehicle.delivered@v1; §4 first lap)",
    }
    ddb.put_item("DEALER_ServiceJourneys", row, env_prefix)
    return {"handled": True, "created_service_journey": sj_id,
            "purchase_journey_id": journey_id, "vin": vin, "stage": "BOOK"}


def _on_play_trigger(trigger: str):
    """A2 Plays subscriber factory (§4.10.2): fire the matching Play for the
    journey named in the event, at the play's autonomy level."""
    def handler(detail: dict, env_prefix: str | None = None) -> dict:
        journey_id = detail.get("journey_id")
        if not journey_id:
            return {"handled": False, "reason": "no journey_id in event"}
        from ..journeys import plays
        r = plays.run_play(journey_id, trigger=trigger,
                           trigger_event_id=detail.get("event_id"), apply=True, env_prefix=env_prefix)
        return {"handled": True, "trigger": trigger, "play": r}
    return handler


def on_equity_crossed(detail: dict, env_prefix: str | None = None) -> dict:
    """A4 equity.crossed_positive -> A2 Upgrade Pitch play (§4.10.2)."""
    return _on_play_trigger("equity.crossed_positive")(detail, env_prefix)


def on_discovery_match(detail: dict, env_prefix: str | None = None) -> dict:
    """A1 discovery.match_found -> A2 Recommend Vehicle play (§4.10.2)."""
    return _on_play_trigger("discovery.match_found")(detail, env_prefix)


# subscriber registry (detail-type -> handler)
_SUBSCRIBERS = {
    "vehicle.delivered": on_vehicle_delivered,
    "equity.crossed_positive": on_equity_crossed,
    "lease.end_window_entered": _on_play_trigger("lease.end_window_entered"),
    "discovery.match_found": on_discovery_match,
    "service.handoff_to_sales": _on_play_trigger("service.handoff_to_sales"),
    "journey.stalled": _on_play_trigger("journey.stalled"),
}


def subscribe(detail_type: str, handler) -> None:
    """Register an additional subscriber for a catalogued type. Several handlers
    may share a type; dispatch_local runs them all (one failing never blocks the
    others — the per-subscriber DLQ semantics of §4.6)."""
    if detail_type not in CATALOG:
        raise UnknownEventType(f"{detail_type!r} is not in the §4.6 event catalog")
    _EXTRA_SUBSCRIBERS.setdefault(detail_type, []).append(handler)


_EXTRA_SUBSCRIBERS: dict[str, list] = {}

# Idempotency: event ids already handled in this process (at-least-once delivery
# means the same event may arrive twice; subscribers must not double-apply).
_HANDLED: dict[str, dict[str, Any]] = {}
_HANDLED_MAX = 2000


def subscribed_types() -> list[str]:
    """Every detail-type with at least one subscriber (drives the bus rule pattern)."""
    _ensure_platform_subscribers()
    return sorted(set(_SUBSCRIBERS) | set(_EXTRA_SUBSCRIBERS))


def _ensure_platform_subscribers() -> None:
    # Platform subscribers live in their own modules and register on import.
    from ..platform import recommendations  # noqa: F401


def dispatch_local(detail_type: str, detail: dict[str, Any], env_prefix: str | None = None) -> dict[str, Any]:
    """Run the matching subscriber(s) in-process (local stand-in for the bus rule)."""
    _ensure_platform_subscribers()
    eid = detail.get("event_id")
    if eid and eid in _HANDLED:
        return {"handled": True, "duplicate": True, "event_id": eid}
    primary = _SUBSCRIBERS.get(detail_type)
    handlers = ([primary] if primary else []) + _EXTRA_SUBSCRIBERS.get(detail_type, [])
    if not handlers:
        return {"handled": False, "reason": f"no subscriber for {detail_type}"}
    primary_result: Any = None
    fanout, failures = [], []
    for h in handlers:
        try:
            r = h(detail, env_prefix)
        except Exception as e:
            failures.append({"subscriber": getattr(h, "__name__", repr(h)),
                             "error": f"{type(e).__name__}: {e}"})
            continue
        if h is primary:
            primary_result = r
        else:
            fanout.append(r)
    # Keep the primary subscriber's result shape (callers read it); additional
    # platform subscribers' results ride alongside under `fanout`.
    if isinstance(primary_result, dict):
        out = dict(primary_result)
    else:
        out = {"handled": bool(fanout)}
    if fanout:
        out["fanout"] = fanout
    if failures:
        out["failures"] = failures
    if eid and not failures:
        _HANDLED[eid] = out
        if len(_HANDLED) > _HANDLED_MAX:          # bounded in a warm Lambda
            for k in list(_HANDLED)[: len(_HANDLED) - _HANDLED_MAX]:
                del _HANDLED[k]
    return out
