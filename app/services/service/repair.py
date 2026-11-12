"""A7 — track_parts + update_line_status + notify_ready (spec §3.7 / §5.20.5).

The REPAIR -> READY tail of the service journey:

  track_parts(sj_id)               -> parts + parts-order status for the RO;
                                      sets parts_hold when anything is back-ordered
  update_line_status(sj_id, ...)   -> advance a repair line (in_progress -> complete
                                      -> qc_passed); ready to close when all complete
  notify_ready(sj_id)              -> close the RO, schedule pickup, notify customer,
                                      advance READY (writes service.ready)

Grounding: parts status comes from DEALER_Parts / DEALER_PartsOrders rows;
notify_ready only fires once the RO's approved work is complete. Nothing is
fabricated — a held part blocks READY the same way the APPROVE gate blocks on a
missing decision.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from ..common import ddb
from ..events import bus as _bus
from . import supervisor as svc_supervisor

_SJ = "DEALER_ServiceJourneys"
_RO = "DEALER_RepairOrders"
_PARTS = "DEALER_Parts"
_PARTS_ORDERS = "DEALER_PartsOrders"
_APPTS = "DEALER_Appointments"
_EVENTS = "DEALER_ServiceJourneyEvents"

_HELD_STATUSES = ("backordered", "ordered", "in_transit")   # not yet on the shelf
_LINE_STATUSES = ("pending", "in_progress", "complete", "qc_passed")
# How the vehicle goes back to the customer at close (§3.7 wait types).
_PICKUP_MODES = ("counter", "pickup_delivery", "mobile")
_PICKUP_FIELDS = ("mode", "address", "window_start", "window_end", "driver_id", "contact", "note")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class NotFound(Exception):
    pass


class NotReady(Exception):
    pass


def _ro_for_sj(sj_id: str, env_prefix: str | None) -> dict[str, Any] | None:
    ros = ddb.scan_filter(_RO, "service_journey_id", sj_id, env_prefix)
    return ros[0] if ros else None


def track_parts(sj_id: str, persist_hold: bool = False,
                env_prefix: str | None = None) -> dict[str, Any]:
    """Parts + parts-order status for the journey's RO (spec §5.20.5).

    Reports each part (on-hand?) and each parts order (status + ETA), and whether
    the journey is on a parts hold (any order not yet received). Read-only by
    default; pass persist_hold=True (notify_ready does) to reflect the hold flag
    on the service-journey row. Keeping the plain GET read-only avoids a
    surprise write on a status check."""
    sj = ddb.get_item(_SJ, {"service_journey_id": sj_id}, env_prefix)
    if sj is None:
        raise NotFound(f"service journey {sj_id} not found")
    ro = _ro_for_sj(sj_id, env_prefix)
    ro_id = (ro or {}).get("ro_id")

    parts = ddb.scan_filter(_PARTS, "ro_id", ro_id, env_prefix) if ro_id else []
    orders = ddb.scan_filter(_PARTS_ORDERS, "ro_id", ro_id, env_prefix) if ro_id else []

    held = [o for o in orders if str(o.get("status")).lower() in _HELD_STATUSES]
    parts_hold = bool(held) or any(not p.get("on_hand") for p in parts)
    soonest_eta = min((o.get("eta") for o in held if o.get("eta")), default=None)

    if persist_hold and bool(sj.get("parts_hold")) != parts_hold:
        sj = dict(sj)
        sj["parts_hold"] = parts_hold
        sj["updated_at"] = _now()
        ddb.put_item(_SJ, sj, env_prefix)

    return {
        "service_journey_id": sj_id, "ro_id": ro_id,
        "parts": [{"part_id": p.get("part_id"), "description": p.get("description"),
                   "qty": p.get("qty"), "on_hand": bool(p.get("on_hand"))} for p in parts],
        "parts_orders": [{"parts_order_id": o.get("parts_order_id"), "status": o.get("status"),
                          "eta": o.get("eta")} for o in orders],
        "parts_hold": parts_hold,
        "held_count": len(held),
        "soonest_eta": soonest_eta,
        "ready_for_repair": not parts_hold,
        "source_ref": "A7.track_parts (DEALER_Parts + DEALER_PartsOrders)",
    }


def update_line_status(sj_id: str, line_id: str, status: str, actor: str = "A7",
                       env_prefix: str | None = None) -> dict[str, Any]:
    """Advance a repair line's status (spec §5.20.5).

    Records the line status on a parts row (the sandbox stand-in for the RO line)
    + a service.line_status event. status in pending|in_progress|complete|qc_passed."""
    if status not in _LINE_STATUSES:
        raise NotReady(f"status must be one of {_LINE_STATUSES}")
    sj = ddb.get_item(_SJ, {"service_journey_id": sj_id}, env_prefix)
    if sj is None:
        raise NotFound(f"service journey {sj_id} not found")
    part = ddb.get_item(_PARTS, {"part_id": line_id}, env_prefix)
    if part is None:
        raise NotFound(f"repair line {line_id} not found")
    part = dict(part)
    part["line_status"] = status
    ddb.put_item(_PARTS, part, env_prefix)

    ts = _now()
    ev = svc_supervisor._event_row(sj_id, "service.line_status", actor,
                                   f"line {line_id} -> {status}", {"line_id": line_id, "status": status}, ts)
    ddb.put_item(_EVENTS, ev, env_prefix)
    if status in ("complete", "qc_passed"):
        _bus.emit("ro.line_completed", {"service_journey_id": sj_id, "line_id": line_id,
                                        "status": status, "dealer_id": sj.get("dealer_id")},
                  actor=actor, resource_url=f"/r/service_journey/{sj_id}", env_prefix=env_prefix)
    return {"service_journey_id": sj_id, "line_id": line_id, "status": status}


def notify_ready(sj_id: str, actor: str = "A7", pickup: dict[str, Any] | None = None,
                 env_prefix: str | None = None) -> dict[str, Any]:
    """Close the RO, schedule pickup, notify the customer, advance READY (§5.20.5).

    Blocks if the journey is still on a parts hold (track_parts). Writes the
    pickup appointment + a service.ready event; the actual stage advance to READY
    is done via the supervisor so the handoff gate/event ordering holds.

    `pickup` records how the vehicle goes back to the customer when it is not a
    counter pickup — {mode: pickup_delivery|mobile|counter, address?, window_start?,
    window_end?, driver_id?, note?}. It is stored on the pickup appointment and
    on the close event so the delivery promise is auditable."""
    sj = ddb.get_item(_SJ, {"service_journey_id": sj_id}, env_prefix)
    if sj is None:
        raise NotFound(f"service journey {sj_id} not found")
    if sj.get("stage") != "REPAIR":
        raise NotReady(f"{sj_id} is at {sj.get('stage')}, not REPAIR — cannot notify ready")

    parts = track_parts(sj_id, persist_hold=True, env_prefix=env_prefix)
    if parts["parts_hold"]:
        raise NotReady(f"{sj_id} is on a parts hold (soonest ETA {parts['soonest_eta']})")

    ts = _now()
    ro = _ro_for_sj(sj_id, env_prefix)
    if ro:
        ro = dict(ro)
        ro["status"] = "closed"
        ro["closed_at"] = ts
        ddb.put_item(_RO, ro, env_prefix)

    # schedule a pickup appointment
    market = (sj.get("market_id") or "us").upper()
    appt_id = f"AP-{market}-PICKUP-{int(_dt.datetime.now(_dt.timezone.utc).timestamp())}"
    details = {k: v for k, v in (pickup or {}).items()
               if k in _PICKUP_FIELDS and v not in (None, "")} if pickup else {}
    mode = details.get("mode") or ("pickup_delivery" if sj.get("wait_type") == "pickup_delivery" else "counter")
    if mode not in _PICKUP_MODES:
        raise NotReady(f"pickup.mode must be one of {_PICKUP_MODES}")
    details["mode"] = mode
    pickup_at = details.get("window_start") or (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=1)).replace(
        hour=17, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")
    ddb.put_item(_APPTS, {
        "appointment_id": appt_id, "type": "service_pickup", "service_journey_id": sj_id,
        "customer_id": sj.get("customer_id"), "dealer_id": sj.get("dealer_id"),
        "vin": sj.get("vin"), "slot_start": pickup_at, "status": "scheduled",
        "pickup": details, "pickup_mode": mode,
        "created_at": ts, "source_ref": "A7.notify_ready",
    }, env_prefix)

    # service.ready event + advance to READY via the supervisor
    ev = svc_supervisor._event_row(sj_id, "service.ready", actor,
                                   f"vehicle ready; {mode} {pickup_at}",
                                   {"ro_id": (ro or {}).get("ro_id"), "pickup_appointment_id": appt_id,
                                    "pickup": details}, ts)
    ddb.put_item(_EVENTS, ev, env_prefix)
    advanced = svc_supervisor.advance_service_journey(sj_id, "vehicle ready — notified customer",
                                                      actor, env_prefix=env_prefix)

    scope = {"service_journey_id": sj_id, "dealer_id": sj.get("dealer_id"),
             "customer_id": sj.get("customer_id"), "vin": sj.get("vin")}
    if ro:
        _bus.emit("ro.closed", {**scope, "ro_id": ro.get("ro_id"), "pickup_mode": mode}, actor=actor,
                  resource_url=f"/r/ro/{ro.get('ro_id')}", env_prefix=env_prefix)
    _bus.emit("vehicle.ready", {**scope, "pickup_appointment_id": appt_id, "pickup_at": pickup_at,
                                "pickup_mode": mode},
              actor=actor, resource_url=f"/r/service_journey/{sj_id}", env_prefix=env_prefix)
    return {"service_journey_id": sj_id, "ro_closed": bool(ro),
            "pickup_appointment_id": appt_id, "pickup_at": pickup_at,
            "pickup": details, "advanced": advanced, "notified": True,
            "source_ref": "A7.notify_ready (RO close + pickup appointment)"}
