"""A7 — predict_next_service + book_service (spec §3.7 / §5.20).

The front door of the service lane:

  predict_next_service(vin)                 -> next-due mileage/date + interval basis
  book_service(vin, services, wait_type)    -> opens a service journey in BOOK +
                                               an appointment, writes service.booked

Grounding: prediction uses the vehicle's odometer + delivery date + the market's
annual-distance default + a standard service interval — all transparent inputs,
returned so the estimate is explainable (never a black-box date). book_service
creates real DEALER_ServiceJourneys / DEALER_Appointments rows and the opening
service event; nothing is fabricated.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from ..common import ddb
from ..events import bus as _bus
from . import supervisor as svc_supervisor

_VEHICLES = "DEALER_Vehicles"
_SJ = "DEALER_ServiceJourneys"
_APPTS = "DEALER_Appointments"
_MARKETS = "OEM_Markets"
_EVENTS = "DEALER_ServiceJourneyEvents"

# Standard maintenance interval (distance + time), market unit applied via the
# market's annual-distance default. Miles for US, km for DE.
_SERVICE_INTERVAL = {"mi": 7500, "km": 12000}
_INTERVAL_MONTHS = 6
_WAIT_TYPES = ("wait", "drop", "loaner", "mobile", "pickup_delivery")
# `express` is the fixed-menu, no-MPI service class (§3.7); `standard` takes the
# full multi-point inspection.
SERVICE_CLASSES = ("standard", "express")


class NotFound(Exception):
    pass


class InvalidWaitType(Exception):
    pass


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(dt: _dt.datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


# the sandbox scheduling connector: hourly slots in the store's own hours, weekdays, 2 bays/hour
STORE_OPEN_HOUR, STORE_CLOSE_HOUR, BAYS_PER_HOUR = 8, 16, 2


def store_slots(dealer_id: str | None, days: int = 5, env_prefix: str | None = None,
                now: _dt.datetime | None = None) -> dict[str, Any]:
    """Bookable service slots for the next `days` business days at a store: hourly from
    8:00 to 16:00 in the store's time zone, stored as UTC, minus appointments already booked.
    The customer's slot picker and a booking made without a time both use this, so neither
    can land outside store hours (a fixed "9:00 UTC" was 4 AM in Atlanta, on a Sunday)."""
    from . import desk
    taken: dict[str, int] = {}
    for a in ddb.scan_filter(_APPTS, "dealer_id", dealer_id, env_prefix) if dealer_id else []:
        if (a.get("type") or "").startswith("service") and a.get("status") not in ("cancelled",):
            k = (a.get("slot_start") or "")[:13]
            taken[k] = taken.get(k, 0) + 1
    tz_name = desk._dealer_timezone(dealer_id, env_prefix) if dealer_id else "UTC"
    tz = desk._zone(tz_name)
    out: list[dict[str, Any]] = []
    d = (now or _now()).astimezone(tz).date()
    while len({s["date"] for s in out}) < days:
        d += _dt.timedelta(days=1)
        if d.weekday() >= 5:
            continue
        for hour in range(STORE_OPEN_HOUR, STORE_CLOSE_HOUR + 1):
            start = _dt.datetime(d.year, d.month, d.day, hour, tzinfo=tz).astimezone(_dt.timezone.utc)
            free = max(0, BAYS_PER_HOUR - taken.get(_iso(start)[:13], 0))
            if free:
                out.append({"date": d.isoformat(), "slot_start": _iso(start), "local_time": f"{hour:02d}:00",
                            "capacity": free})
    return {"timezone": tz_name, "slots": out}


def next_open_slot(dealer_id: str | None, env_prefix: str | None = None,
                   now: _dt.datetime | None = None) -> str:
    """The store's first open slot from tomorrow — the time a booking without one gets."""
    slots = store_slots(dealer_id, days=10, env_prefix=env_prefix, now=now)["slots"]
    if slots:
        return slots[0]["slot_start"]
    raise RuntimeError(f"no open service slot at {dealer_id} in the next 10 business days")


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _parse(ts: str | None) -> _dt.datetime | None:
    if not ts:
        return None
    try:
        return _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _last_ro_date(vin: str, env_prefix: str | None) -> _dt.datetime | None:
    """Most recent repair-order open date for a VIN (via its service journeys)."""
    sjs = [s for s in ddb.scan_all(_SJ, env_prefix) if s.get("vin") == vin]
    latest = None
    for sj in sjs:
        for ro in ddb.scan_filter("DEALER_RepairOrders", "service_journey_id",
                                  sj.get("service_journey_id"), env_prefix):
            d = _parse(ro.get("opened_at"))
            if d and (latest is None or d > latest):
                latest = d
    return latest


def service_menu(is_ev: bool) -> list[str]:
    """The services a car of this powertrain can be booked for — one list for every booking screen."""
    common = ["Multi-point inspection", "Tire rotation", "Brake inspection", "Cabin air filter", "Wiper blades",
              "Wheel alignment", "12V battery test"]
    return (["HV battery health check", "Charge-port inspection", "Coolant (battery loop) check"] + common
            if is_ev else ["Oil & filter", "Engine air filter", "Coolant flush", "Transmission service"] + common)


def _is_ev_vehicle(veh: dict[str, Any], env_prefix: str | None) -> bool:
    """Battery-electric per the catalog (OEM_CatalogModels powertrains == ["EV"])."""
    if not veh.get("model_id"):
        return False
    row = next((m for m in ddb.scan_filter("OEM_CatalogModels", "model_id", veh["model_id"], env_prefix)), None) or {}
    return list(row.get("powertrains") or []) == ["EV"]


def predict_next_service(vin: str, env_prefix: str | None = None) -> dict[str, Any]:
    """Predict the next service due for a VIN (spec §3.7).

    Basis: last-service date (most recent RO) or delivery date, plus the market
    interval (distance + 6 months). Returns due-by date, mileage estimate, and
    the inputs so the prediction is explainable."""
    veh = ddb.get_item(_VEHICLES, {"vin": vin}, env_prefix)
    if veh is None:
        raise NotFound(f"vehicle {vin} not found")
    market = (veh.get("market_id") or "us").lower()
    mkt = ddb.get_item(_MARKETS, {"market_id": market}, env_prefix) or {}
    unit = mkt.get("distance_unit") or ("km" if market == "de" else "mi")
    annual = _num(mkt.get("annual_distance_default")) or (12000 if unit == "mi" else 15000)
    interval = _SERVICE_INTERVAL.get(unit, 7500)

    odo = _num(veh.get("odometer")) or 0
    last_ro = _last_ro_date(vin, env_prefix)
    basis_date = last_ro or _parse(veh.get("delivered_at")) or _now()
    basis = "last_service" if last_ro else ("delivery" if veh.get("delivered_at") else "today")

    # date due: interval months from basis; mileage due: next interval boundary
    due_date = basis_date + _dt.timedelta(days=30 * _INTERVAL_MONTHS)
    next_mileage_boundary = (int(odo // interval) + 1) * interval
    now = _now()
    overdue = due_date < now

    return {
        "vin": vin,
        "customer_id": veh.get("customer_id"),
        "dealer_id": veh.get("dealer_id"),
        "market": market,
        "unit": unit,
        "odometer": odo,
        "interval": interval,
        "annual_distance": annual,
        "basis": basis,
        "basis_date": _iso(basis_date),
        "next_service_mileage": next_mileage_boundary,
        "due_by": _iso(due_date),
        "overdue": overdue,
        # an EV has no engine oil: its interval service is the HV battery / cabin-filter check
        "recommended_services": (["Multi-point inspection", "Tire rotation", "HV battery health check"]
                                 if _is_ev_vehicle(veh, env_prefix)
                                 else ["Multi-point inspection", "Oil & filter", "Tire rotation"])
                                + (["Brake inspection"] if odo >= 30000 else []),
        # everything this car can be booked for: the menu a booking screen offers (an EV is never
        # offered an oil change; a combustion car never an HV battery check)
        "bookable_services": service_menu(_is_ev_vehicle(veh, env_prefix)),
        "source_ref": "A7.predict_next_service (odometer + market interval)",
    }


def book_service(vin: str, services: list[str] | None = None, wait_type: str = "drop",
                 preferred_slot: str | None = None, actor: str = "A7",
                 service_class: str | None = None,
                 env_prefix: str | None = None) -> dict[str, Any]:
    """Open a service journey in BOOK for a VIN + an appointment (spec §3.7).

    Creates the DEALER_ServiceJourneys row (stage BOOK), a service-type
    DEALER_Appointments row, and the opening service.booked event. Idempotent
    per VIN's active booking (deterministic id) so a re-book updates rather than
    piles up."""
    if wait_type not in _WAIT_TYPES:
        raise InvalidWaitType(f"wait_type must be one of {_WAIT_TYPES}")
    svc_class = (service_class or "standard").lower()
    if svc_class not in SERVICE_CLASSES:
        raise InvalidWaitType(f"service_class must be one of {SERVICE_CLASSES}")
    veh = ddb.get_item(_VEHICLES, {"vin": vin}, env_prefix)
    if veh is None:
        raise NotFound(f"vehicle {vin} not found")

    ts = _now()
    market = (veh.get("market_id") or "us").upper()
    # A visit the Service Agent already opened for this car (e.g. the first-service journey
    # opened at delivery, SJ-DLV-…) and still in BOOK is the one the booking fills — never a
    # second visit next to it. Only a car with no open visit gets a new journey.
    open_sj = next((s for s in sorted(ddb.scan_filter(_SJ, "vin", vin, env_prefix),
                                      key=lambda s: s.get("opened_at") or "", reverse=True)
                    if s.get("stage") == "BOOK"), None)
    if open_sj:
        sj_id = open_sj["service_journey_id"]
    else:
        # a new visit for this car: never reuse the id of an earlier visit (the second booking of a
        # car used to overwrite its first visit, history and all)
        base = f"SJ-{market}-BK-{vin.replace('-', '')[-8:]}"
        sj_id, n = base, 1
        while ddb.get_item(_SJ, {"service_journey_id": sj_id}, env_prefix):
            n += 1
            sj_id = f"{base}-{n}"
    prior_appt = next((a for a in ddb.scan_filter(_APPTS, "service_journey_id", sj_id, env_prefix)
                       if a.get("type") == "service" and a.get("status") == "booked"), None) if open_sj else None
    # a booking with no time of its own keeps the visit's booked time — it never quietly moves a
    # slot the customer chose — else takes the store's next open slot in its own hours
    slot = (preferred_slot or (prior_appt or {}).get("slot_start")
            or next_open_slot(veh.get("dealer_id"), env_prefix, now=ts))
    appt_id = (prior_appt or {}).get("appointment_id") or f"AP-{market}-SVC-{int(ts.timestamp())}"

    sj = {
        **(open_sj or {}),
        "service_journey_id": sj_id, "stage": "BOOK", "market_id": veh.get("market_id"),
        "vin": vin, "customer_id": veh.get("customer_id"), "dealer_id": veh.get("dealer_id"),
        "wait_type": wait_type, "services": services or ["Multi-point inspection"],
        "service_class": svc_class, "promised_at": slot,
        "opened_at": (open_sj or {}).get("opened_at") or _iso(ts),
        "source_ref": "A7.book_service" + (f" (fills {open_sj.get('source_ref') or 'open visit'})" if open_sj else ""),
        "advisor_id": (open_sj or {}).get("advisor_id"), "updated_at": _iso(ts),
    }
    ddb.put_item(_SJ, sj, env_prefix)

    ddb.put_item(_APPTS, {
        "appointment_id": appt_id, "type": "service", "service_journey_id": sj_id,
        "customer_id": veh.get("customer_id"), "dealer_id": veh.get("dealer_id"),
        "vin": vin, "slot_start": slot, "wait_type": wait_type, "service_class": svc_class,
        "services": services or ["Multi-point inspection"], "status": "booked",
        "created_at": _iso(ts), "source_ref": "A7.book_service",
    }, env_prefix)

    ev = svc_supervisor._event_row(sj_id, "service.booked", actor,
                                   f"{svc_class} service booked ({wait_type}) for {slot}",
                                   {"appointment_id": appt_id, "services": services,
                                    "service_class": svc_class}, _iso(ts))
    ddb.put_item(_EVENTS, ev, env_prefix)

    scope = {"service_journey_id": sj_id, "customer_id": veh.get("customer_id"),
             "dealer_id": veh.get("dealer_id"), "vin": vin}
    if not open_sj:   # a filled visit already announced itself when the agent opened it
        _bus.emit("service.journey.created", scope, actor=actor,
                  resource_url=f"/r/service_journey/{sj_id}", env_prefix=env_prefix)
    _bus.emit("service.appointment.booked", {**scope, "appointment_id": appt_id, "slot_start": slot,
                                             "wait_type": wait_type, "service_class": svc_class},
              actor=actor, resource_url=f"/r/appointment/{appt_id}", env_prefix=env_prefix)
    return {"service_journey_id": sj_id, "appointment_id": appt_id, "vin": vin, "filled_open_visit": bool(open_sj),
            "stage": "BOOK", "wait_type": wait_type, "service_class": svc_class, "slot_start": slot,
            "services": services or ["Multi-point inspection"],
            "source_ref": "A7.book_service"}
