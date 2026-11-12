"""Delivery Board read model + actions (spec §5.15, G-07).

Kanban Scheduled → Prepped → Docs Ready → Delivered. A card is a delivery for a
journey in DELIVER (or recently delivered): customer, VIN, slot, the prep
checklist (PDI, detail, accessories, charge / fuel level, home-charger install
for EVs), document status, and customer confirmation.

  build_delivery_board(scope, market)       -> lanes + counts (the column is derived
                                               from the checklist, never set by hand)
  ensure_delivery(journey_id, actor)        -> the DEALER_Deliveries row for a DELIVER
                                               journey (created on first touch)
  update_delivery(journey_id, patch, actor) -> checklist / docs / confirmation /
                                               reschedule (appointment.changed + customer notice)
  send_delivery_message(journey_id, ...)    -> delivery-day message via OutreachProvider
  deliver_journey(journey_id, actor)        -> gated mark-delivered: prep + docs complete
  mark_delivered(delivery_id, actor)        -> advance DELIVER->OWN, complete the delivery,
                                               emit vehicle.delivered (A7 opens the first
                                               service journey — the sale→service loop)
"""
from __future__ import annotations

import datetime as _dt
import hashlib
from typing import Any

from ..common import ddb
from ..events import bus as event_bus
from ..journeys import supervisor as journey_supervisor

_DELIVERIES = "DEALER_Deliveries"
_JOURNEYS = "DEALER_Journeys"
_APPTS = "DEALER_Appointments"

PREP_ITEMS = ("pdi", "detail", "accessories", "charge_level")
EV_ITEMS = ("charger_install",)
LANES = ("scheduled", "prepped", "docs_ready", "delivered")
LANE_LABELS = {"scheduled": "Scheduled", "prepped": "Prepped", "docs_ready": "Docs ready", "delivered": "Delivered"}
_DELIVERED_LOOKBACK_DAYS = 30


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class NotFound(Exception):
    pass


class AlreadyDelivered(Exception):
    pass


class NotReady(Exception):
    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__(f"delivery not ready: {', '.join(missing)}")


def _in_scope(scope: dict[str, Any], row: dict[str, Any]) -> bool:
    if scope.get("is_oem"):
        return True
    return row.get("dealer_id") in (scope.get("dealer_ids") or set())


def _is_ev(model_id: str | None, env_prefix: str | None) -> bool:
    m = ddb.get_item("OEM_CatalogModels", {"model_id": model_id}, env_prefix) if model_id else None
    return any(str(p).upper() in ("EV", "BEV") for p in ((m or {}).get("powertrains") or []))


def required_items(d: dict[str, Any], env_prefix: str | None) -> list[str]:
    return list(PREP_ITEMS) + (list(EV_ITEMS) if _is_ev(d.get("vehicle_model_id"), env_prefix) else [])


def lane_for(d: dict[str, Any], env_prefix: str | None = None) -> str:
    if d.get("status") == "completed":
        return "delivered"
    chk = d.get("checklist") or {}
    prep_done = all(chk.get(k) for k in required_items(d, env_prefix))
    if prep_done and d.get("docs_signed"):
        return "docs_ready"
    if prep_done:
        return "prepped"
    return "scheduled"


def _customer_name(cid: str | None, env_prefix: str | None) -> str | None:
    c = ddb.get_item("DEALER_Customers", {"customer_id": cid}, env_prefix) if cid else None
    return " ".join(filter(None, [(c or {}).get("given_name"), (c or {}).get("family_name")])) or None if c else None


def _card(d: dict[str, Any], env_prefix: str | None) -> dict[str, Any]:
    chk = d.get("checklist") or {}
    req = required_items(d, env_prefix)
    inv = ddb.scan_filter("DEALER_Inventory", "reserved_for_journey_id", d.get("journey_id"), env_prefix) if d.get("journey_id") else []
    docs = ddb.scan_filter("CUST_Documents", "journey_id", d.get("journey_id"), env_prefix) if d.get("journey_id") else []
    model = ddb.get_item("OEM_CatalogModels", {"model_id": d.get("vehicle_model_id")}, env_prefix) if d.get("vehicle_model_id") else None
    return {
        "delivery_id": d.get("delivery_id"), "journey_id": d.get("journey_id"), "deal_id": d.get("deal_id"),
        "customer_id": d.get("customer_id"), "customer_name": _customer_name(d.get("customer_id"), env_prefix),
        "dealer_id": d.get("dealer_id"), "vehicle_model_id": d.get("vehicle_model_id"),
        "vehicle_name": (model or {}).get("name"),
        "vin": d.get("vin") or (inv[0].get("vin") if inv else None),
        "scheduled_at": d.get("scheduled_at"), "lane": lane_for(d, env_prefix),
        "checklist": {k: bool(chk.get(k)) for k in req},
        "charger_install": d.get("charger_install") if "charger_install" in req else None,
        "prep_done": sum(1 for k in req if chk.get(k)), "prep_total": len(req),
        "docs_signed": bool(d.get("docs_signed")),
        "documents": [{"document_id": x.get("document_id"), "kind": x.get("kind"), "title": x.get("title"),
                       "shared_with": x.get("shared_with") or []} for x in docs],
        "customer_confirmed": bool(d.get("customer_confirmed")), "confirmed_at": d.get("confirmed_at"),
        "delivered_at": d.get("delivered_at"), "persisted": not d.get("_virtual", False),
        "last_message_at": d.get("last_message_at"),
    }


def _virtual(j: dict[str, Any], env_prefix: str | None) -> dict[str, Any]:
    deals = ddb.scan_filter("DEALER_Deals", "journey_id", j["journey_id"], env_prefix)
    appts = [a for a in ddb.scan_filter(_APPTS, "journey_id", j["journey_id"], env_prefix) if a.get("type") == "delivery"]
    return {"delivery_id": f"DLV-{j['journey_id']}", "journey_id": j["journey_id"],
            "customer_id": j.get("customer_id"), "dealer_id": j.get("dealer_id"),
            "deal_id": deals[0]["deal_id"] if deals else None, "vehicle_model_id": j.get("primary_model_id"),
            "market_id": j.get("market_id"),
            "scheduled_at": appts[0].get("slot_start") if appts else None,
            "status": "scheduled", "checklist": {}, "_virtual": True}


def build_delivery_board(scope: dict[str, Any], market_id: str | None = None,
                         env_prefix: str | None = None) -> dict[str, Any]:
    market = market_id or scope.get("market_id") or "us"
    cutoff = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=_DELIVERED_LOOKBACK_DAYS)).isoformat()
    journeys = {j["journey_id"]: j for j in ddb.scan_filter(_JOURNEYS, "market_id", market, env_prefix)
                if _in_scope(scope, j)}
    rows = []
    open_for: set[str] = set()
    for d in ddb.scan_all(_DELIVERIES, env_prefix):
        if not _in_scope(scope, d) or (d.get("market_id") and d["market_id"] != market):
            continue
        if not d.get("market_id") and d.get("journey_id") not in journeys:
            continue
        if d.get("status") == "completed" and (d.get("delivered_at") or d.get("scheduled_at") or "") < cutoff:
            continue
        rows.append(d)
        if d.get("status") != "completed":
            open_for.add(d.get("journey_id"))
    for j in journeys.values():
        if j.get("stage") == "DELIVER" and j["journey_id"] not in open_for:
            rows.append(_virtual(j, env_prefix))
    lanes: dict[str, list[dict[str, Any]]] = {s: [] for s in LANES}
    for d in rows:
        c = _card(d, env_prefix)
        lanes[c["lane"]].append(c)
    for s in lanes:
        lanes[s].sort(key=lambda c: c.get("scheduled_at") or "9999")
    today = _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    return {
        "market_id": market,
        "lanes": lanes, "lane_order": list(LANES), "lane_labels": LANE_LABELS,
        "counts": {s: len(v) for s, v in lanes.items()},
        "today": sum(1 for v in lanes.values() for c in v if (c.get("scheduled_at") or "").startswith(today)),
        "unconfirmed": sum(1 for s in ("scheduled", "prepped", "docs_ready") for c in lanes[s] if not c["customer_confirmed"]),
        "prep_checklist": list(PREP_ITEMS) + list(EV_ITEMS),
        "source_ref": "DeliveryBoard.build (§5.15; lanes derived from checklist + docs)",
    }


def ensure_delivery(journey_id: str, actor: str, env_prefix: str | None = None) -> dict[str, Any]:
    existing = [d for d in ddb.scan_filter(_DELIVERIES, "journey_id", journey_id, env_prefix)
                if d.get("status") != "completed"]
    if existing:
        return existing[0]
    j = ddb.get_item(_JOURNEYS, {"journey_id": journey_id}, env_prefix)
    if j is None:
        raise NotFound(f"journey {journey_id} not found")
    row = _virtual(j, env_prefix)
    row.pop("_virtual", None)
    row.update({"created_at": _now(), "created_by": actor, "source_ref": "DeliveryBoard.ensure_delivery"})
    ddb.put_item(_DELIVERIES, row, env_prefix)
    return row


def _notify_customer(d: dict[str, Any], subject: str, body: str, env_prefix: str | None) -> dict[str, Any]:
    from ..platform import outreach
    c = ddb.get_item("DEALER_Customers", {"customer_id": d.get("customer_id")}, env_prefix) or {}
    channel = "email" if c.get("email") else "sms"
    return outreach.get_outreach_provider().send(
        channel, c.get("email") if channel == "email" else c.get("phone"), subject, body,
        customer_id=d.get("customer_id"), dealer_id=d.get("dealer_id"), env_prefix=env_prefix)


def update_delivery(journey_id: str, patch: dict[str, Any], actor: str, env_prefix: str | None = None) -> dict[str, Any]:
    d = dict(ensure_delivery(journey_id, actor, env_prefix))
    if d.get("status") == "completed":
        raise AlreadyDelivered(f"{d['delivery_id']} is already delivered")
    req = required_items(d, env_prefix)
    chk = dict(d.get("checklist") or {})
    for k, v in (patch.get("checklist") or {}).items():
        if k not in req:
            raise ValueError(f"unknown checklist item {k!r} (expected {req})")
        chk[k] = bool(v)
    if "charger_install" in patch:
        if "charger_install" not in req:
            raise ValueError("charger install applies to EV deliveries only")
        val = patch["charger_install"]
        if val not in ("not_needed", "scheduled", "installed"):
            raise ValueError("charger_install must be not_needed, scheduled, or installed")
        d["charger_install"] = val
        chk["charger_install"] = val in ("not_needed", "installed")
    d["checklist"] = chk
    if "docs_signed" in patch:
        d["docs_signed"] = bool(patch["docs_signed"])
    if "customer_confirmed" in patch:
        d["customer_confirmed"] = bool(patch["customer_confirmed"])
        d["confirmed_at"] = _now() if d["customer_confirmed"] else None
    events = []
    notice = None
    if patch.get("scheduled_at"):
        new_slot = str(patch["scheduled_at"])
        try:
            _dt.datetime.fromisoformat(new_slot.replace("Z", "+00:00"))
        except ValueError as e:
            raise ValueError("scheduled_at must be an ISO-8601 timestamp") from e
        old = d.get("scheduled_at")
        d["scheduled_at"] = new_slot
        d["customer_confirmed"] = False
        appts = [a for a in ddb.scan_filter(_APPTS, "journey_id", journey_id, env_prefix) if a.get("type") == "delivery"]
        appt = dict(appts[0]) if appts else {"appointment_id": f"AP-DLV-{journey_id}", "type": "delivery",
                                             "journey_id": journey_id, "customer_id": d.get("customer_id"),
                                             "dealer_id": d.get("dealer_id"), "created_at": _now()}
        appt.update({"slot_start": new_slot, "status": "scheduled", "updated_at": _now(), "updated_by": actor})
        ddb.put_item(_APPTS, appt, env_prefix)
        events.append(event_bus.emit("appointment.changed", {
            "appointment_id": appt["appointment_id"], "journey_id": journey_id, "type": "delivery",
            "from": old, "to": new_slot, "dealer_id": d.get("dealer_id"), "customer_id": d.get("customer_id")},
            actor=actor, resource_url=f"/r/appointment/{appt['appointment_id']}", env_prefix=env_prefix))
        notice = _notify_customer(d, "Your delivery time has changed",
                                  f"Your vehicle delivery is now scheduled for {new_slot}. Reply to confirm.", env_prefix)
    d["updated_at"] = _now()
    d["updated_by"] = actor
    ddb.put_item(_DELIVERIES, d, env_prefix)
    return {"delivery": _card(d, env_prefix), "bus_events": events, "customer_notice": notice}


def send_delivery_message(journey_id: str, actor: str, note: str | None = None,
                          env_prefix: str | None = None) -> dict[str, Any]:
    d = dict(ensure_delivery(journey_id, actor, env_prefix))
    slot = d.get("scheduled_at") or "your scheduled time"
    body = (f"We're getting your new vehicle ready for {slot}. Bring your driver's license and proof of "
            f"insurance; your delivery specialist will walk you through every feature.")
    if note:
        body += f"\n\n{note}"
    sent = _notify_customer(d, "Your delivery day", body, env_prefix)
    d["last_message_at"] = _now()
    ddb.put_item(_DELIVERIES, d, env_prefix)
    return {"journey_id": journey_id, "message": sent}


def deliver_journey(journey_id: str, actor: str, env_prefix: str | None = None) -> dict[str, Any]:
    d = ensure_delivery(journey_id, actor, env_prefix)
    missing = [k for k in required_items(d, env_prefix) if not (d.get("checklist") or {}).get(k)]
    if not d.get("docs_signed"):
        missing.append("docs_signed")
    if missing:
        raise NotReady(missing)
    out = mark_delivered(d["delivery_id"], actor=actor, env_prefix=env_prefix)
    out["survey"] = _notify_customer(d, "How was your delivery?",
                                     "Thanks for choosing us. Tell us about your delivery experience — it takes one minute.",
                                     env_prefix)
    return out


_OWNERSHIP_BY_DEAL_TYPE = {"finance": "loan", "lease": "lease", "cash": "owned"}


def _record_delivered_vehicle(d: dict[str, Any], journey: dict[str, Any] | None, actor: str,
                              env_prefix: str | None) -> dict[str, Any] | None:
    """The sale → ownership hand-over on the record (spec §4.2 VIN master): the delivered unit
    leaves inventory as sold and becomes the customer's DEALER_Vehicles row, so My Garage,
    the service lane and A4's equity watch see the car from day one. The VIN is the one the
    deal booked against (or the unit reserved for the journey). No VIN → nothing to record."""
    jid = d.get("journey_id")
    vin = d.get("vin") or (journey or {}).get("vin")
    unit = None
    if not vin and jid:
        rows = ddb.scan_filter("DEALER_Inventory", "reserved_for_journey_id", jid, env_prefix)
        unit = rows[0] if rows else None
        vin = (unit or {}).get("vin")
    if not vin:
        return None
    unit = unit or ddb.get_item("DEALER_Inventory", {"vin": vin}, env_prefix)
    ts = _now()
    if unit and unit.get("status") != "sold":
        unit = dict(unit)
        unit.update({"status": "sold", "sold_at": ts, "sold_to_journey_id": jid, "sold_by": actor})
        # a sold unit is no longer held for anyone (the screens read the hold as "reserved")
        unit.pop("reserved_for_journey_id", None)
        unit.pop("status_before_reserve", None)
        ddb.put_item("DEALER_Inventory", unit, env_prefix)
    deals = ddb.scan_filter("DEALER_Deals", "journey_id", jid, env_prefix) if jid else []
    deal = max(deals, key=lambda x: x.get("created_at") or "") if deals else {}
    existing = ddb.get_item("DEALER_Vehicles", {"vin": vin}, env_prefix) or {}
    row = {
        **existing,
        "vin": vin, "customer_id": d.get("customer_id"), "dealer_id": d.get("dealer_id"),
        "market_id": d.get("market_id") or (journey or {}).get("market_id") or (unit or {}).get("market_id") or "us",
        "model_id": (unit or {}).get("model_id") or d.get("vehicle_model_id"),
        "trim_id": (unit or {}).get("trim_id") or (journey or {}).get("primary_trim_id"),
        # make / model / year resolve from the catalog on read (common/vehicles.display_fields),
        # the same way the seeded delivered rows do
        "trim_name": (unit or {}).get("trim_name"), "color": (unit or {}).get("color"),
        "odometer": (unit or {}).get("odometer") or 0,
        "ownership": _OWNERSHIP_BY_DEAL_TYPE.get(str(deal.get("type") or "finance"), "loan"),
        "delivered_at": ts, "journey_id": jid, "deal_id": deal.get("deal_id"),
        "owner_history": (existing.get("owner_history") or []) + [{"customer_id": d.get("customer_id"), "from": ts, "journey_id": jid}],
        "source_ref": f"DeliveryBoard.mark_delivered ({d.get('delivery_id')}; inventory {vin} → VIN master)",
    }
    ddb.put_item("DEALER_Vehicles", row, env_prefix)
    return row


def mark_delivered(delivery_id: str, actor: str = "delivery",
                   env_prefix: str | None = None) -> dict[str, Any]:
    """Complete a delivery: advance DELIVER->OWN and publish vehicle.delivered.

    The event opens the first service journey (A7), closing the first lap of the
    sale->service loop. Refuses if already completed."""
    d = ddb.get_item(_DELIVERIES, {"delivery_id": delivery_id}, env_prefix)
    if d is None:
        raise NotFound(f"delivery {delivery_id} not found")
    if d.get("status") == "completed":
        raise AlreadyDelivered(f"{delivery_id} is already completed")

    jid = d.get("journey_id")
    journey = ddb.get_item(_JOURNEYS, {"journey_id": jid}, env_prefix) if jid else None

    advance = None
    if journey and journey.get("stage") == "DELIVER":
        try:
            advance = journey_supervisor.advance_journey(
                jid, evidence=f"vehicle delivered ({delivery_id})", actor=actor,
                context_updates={"delivered": True}, env_prefix=env_prefix)
        except Exception as e:
            advance = {"error": f"{type(e).__name__}: {e}"}

    d = dict(d)
    d["status"] = "completed"
    d["delivered_at"] = _now()
    closed = _close_open_appointments(jid, d["delivered_at"], env_prefix)
    vehicle = _record_delivered_vehicle(d, journey, actor, env_prefix)
    trade_unit = _take_in_trade(d, journey, d["delivered_at"], actor, env_prefix)
    if vehicle:
        d["vin"] = vehicle["vin"]      # the delivery records which car was handed over
    ddb.put_item(_DELIVERIES, d, env_prefix)

    detail = {"journey_id": jid, "customer_id": d.get("customer_id"),
              "dealer_id": d.get("dealer_id"), "vehicle_model_id": d.get("vehicle_model_id"),
              "vin": (vehicle or {}).get("vin"),
              "market_id": d.get("market_id") or (journey or {}).get("market_id") or "us"}
    event_result = event_bus.emit("vehicle.delivered", detail, actor=actor,
                                  resource_url=f"/r/journey/{jid}", env_prefix=env_prefix)

    return {
        "delivery_id": delivery_id, "journey_id": jid, "status": "completed",
        "journey_advance": advance, "vehicle_delivered_event": event_result,
        "appointments_closed": closed, "trade_taken_in": trade_unit,
        "source_ref": "DeliveryBoard.mark_delivered (§5.15; emits vehicle.delivered)",
    }


def _take_in_trade(d: dict[str, Any], journey: dict[str, Any] | None, delivered_at: str, actor: str,
                   env_prefix: str | None) -> dict[str, Any] | None:
    """The trade the buyer brought becomes the store's used unit the moment the keys change hands:
    a DEALER_Inventory row (used, in stock, cost = trade value), a pending disposition for the
    inventory manager, and the valuation marked `traded` so it no longer reads as the buyer's equity.
    Hannah's Trek 4 never reached Trade dispositions because nothing did this."""
    cid = d.get("customer_id") or (journey or {}).get("customer_id")
    if not cid or not journey or not (journey.get("context") or {}).get("valuation"):
        return None
    vals = [v for v in ddb.scan_filter("DEALER_Valuations", "customer_id", cid, env_prefix)
            if v.get("disposition") != "traded" and (v.get("journey_id") in (None, journey.get("journey_id")))]
    if not vals:
        return None
    val = max(vals, key=lambda v: v.get("valued_at") or "")
    # the car the valuation is about is the buyer's old car (their current_vehicle), not the one delivered
    new_vin = d.get("vin")
    if val.get("vin") and val["vin"] == new_vin:
        return None
    dealer = d.get("dealer_id") or journey.get("dealer_id")
    market = d.get("market_id") or journey.get("market_id") or "us"
    stock = f"U{hashlib.sha1(f'{val['valuation_id']}'.encode()).hexdigest()[:6].upper()}"
    vin = val.get("vin") or f"TRADE-{val['valuation_id']}"
    unit = {"vin": vin, "vehicle_id": f"VEH-{stock}", "stock_number": stock, "dealer_id": dealer, "market_id": market,
            "status": "in_stock", "condition": "used", "acquired_via": "trade", "acquired_at": delivered_at,
            "make": val.get("make"), "model": val.get("model"), "year": val.get("year"), "odometer": val.get("mileage"),
            "model_id": None, "trim_id": None, "trim_name": " ".join(str(x) for x in (val.get("year"), val.get("make"), val.get("model")) if x),
            "cost": val.get("trade_value"), "currency": val.get("currency"), "age_days": 0,
            "from_journey_id": journey.get("journey_id"), "trade_valuation_id": val.get("valuation_id"),
            "source_ref": f"DeliveryBoard.mark_delivered (trade taken in: {val.get('valuation_id')})"}
    ddb.put_item("DEALER_Inventory", unit, env_prefix)
    ddb.put_item("DEALER_TradeDispositions", {
        "disposition_id": f"TD-{stock}", "customer_id": cid, "dealer_id": dealer, "market_id": market, "vin": vin,
        "valuation_id": val.get("valuation_id"), "acquired_at": delivered_at, "recon_cost": None,
        "currency": val.get("currency"), "disposition": None, "decided_at": None,
        "source_ref": "DeliveryBoard.mark_delivered (trade taken in; disposition pending)"}, env_prefix)
    ddb.put_item("DEALER_Valuations", {**val, "disposition": "traded", "traded_at": delivered_at,
                                       "traded_on_journey_id": journey.get("journey_id")}, env_prefix)
    return {"vin": vin, "stock_number": stock, "disposition_id": f"TD-{stock}", "trade_value": val.get("trade_value")}


def _close_open_appointments(journey_id: str | None, delivered_at: str, env_prefix: str | None) -> list[str]:
    """A delivered journey has nothing left to show up for: a test drive or delivery slot still
    `scheduled` (the buyer skipped the drive, or a reschedule left a copy) is closed as completed
    at delivery — My Garage stopped offering 'Reschedule' on a test drive for a car already owned."""
    if not journey_id:
        return []
    closed = []
    for a in ddb.scan_filter(_APPTS, "journey_id", journey_id, env_prefix):
        if a.get("type") in ("test_drive", "delivery") and a.get("status") in ("scheduled", "booked"):
            ddb.put_item(_APPTS, {**a, "status": "completed", "completed_at": delivered_at,
                                  "closed_by": "delivery"}, env_prefix)
            closed.append(a.get("appointment_id"))
    return closed
