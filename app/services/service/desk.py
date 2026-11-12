"""A7 Service Desk read models and workflows (spec §5.20, §6 /service/*).

Read models (role-masked server-side — a technician never receives customer
contact data, pricing, or coverage cost split; §5.20.5, acceptance #30):
  list_journeys(claims, filters)       Service Board cards + badges
  journey_detail(sj_id, role)          RO view: header, lines, parts, inspection,
                                       estimate, timeline, documents, lane strip
  attention(claims)                    estimates waiting > 20 min, past promise, back-orders
  availability(dealer_id, date)        lane capacity per slot (scheduling sandbox)
  history(vin, role)                   all ROs / inspections / declined work for a VIN
  templates(market, model_id)          MPI templates (ICE / EV / Express) with items
  diagnose(codes, vin)                 DTC + TSB citations; procedure per labor op
  parts_desk(claims)                   held ROs, open orders by ETA, back-orders
  cockpit(claims)                      fixed-ops tiles + [AI INSIGHT] (sourced)
Workflows:
  checkin(sj_id, ...)                  mileage, concerns, photos, approval pref → RO open
  create_parts_order / update_parts_order (emit parts.ordered|received|backordered)
  update_declined(declined_id, ...)    follow-up cadence / booked / dismissed
  draft_message / send_message         A7 drafts, advisor approves, OutreachProvider sends
"""
from __future__ import annotations

import datetime as _dt
import hashlib
from typing import Any

from ..common import ddb
from ..common import vehicles as vehicle_names
from ..events import bus as _bus
from . import estimates as est_mod
from . import metrics
from . import supervisor as svc_supervisor

_SJ = "DEALER_ServiceJourneys"
_RO = "DEALER_RepairOrders"
_ESTIMATES = "DEALER_ServiceEstimates"
_INSPECTIONS = "DEALER_Inspections"
_PARTS = "DEALER_Parts"
_ORDERS = "DEALER_PartsOrders"
_DECLINED = "DEALER_DeclinedWork"
_APPTS = "DEALER_Appointments"
_EVENTS = "DEALER_ServiceJourneyEvents"
_VEHICLES = "DEALER_Vehicles"
_CUSTOMERS = "DEALER_Customers"
_USERS = "PLAT_Users"
_DOCS = "CUST_Documents"
_HANDOFFS = "DEALER_Handoffs"

SERVICE_STAGES = ["BOOK", "CHECK_IN", "INSPECT", "DIAGNOSE", "APPROVE", "REPAIR", "READY", "FOLLOW_UP"]
TECH_ROLES = frozenset({"dealer_technician"})
PARTS_ROLES = frozenset({"dealer_parts"})
ESTIMATE_ROLES = frozenset({"dealer_service", "dealer_service_manager", "dealer_manager"})
_WAITING_MIN = 20
_LANE_CAPACITY = 4                    # bays per hourly slot (dealer config in production)
_LOANERS = 3
_SLOT_HOURS = range(8, 17)
_HELD = ("backordered", "ordered", "in_transit")
_ORDER_STATUSES = ("ordered", "in_transit", "backordered", "received", "cancelled")
# Express lane: fixed-menu quick service only (flat-rate hours at or under the
# cap); diagnosis and HV work are standard-service ops, never express.
_EXPRESS_MAX_HOURS = 0.6
_EXPRESS_EXCLUDE = ("DIAG", "EV-")
# Mobile vans / pickup-delivery drivers per store (dealer config in production).
_MOBILE_VANS = 2
_PICKUP_DRIVERS = 2

# MPI template items (from the prior vsa.json + OEM schedules; §5.20.3). Items
# carry their unit so the technician records a measurement, not a guess.
_ICE_ITEMS = [
    {"item": "Front brakes", "unit": "mm", "group": "Brakes"},
    {"item": "Rear brakes", "unit": "mm", "group": "Brakes"},
    {"item": "Tires", "unit": "32nds", "group": "Tires & wheels"},
    {"item": "Battery", "unit": "V", "group": "Electrical"},
    {"item": "Coolant", "unit": "%", "group": "Fluids"},
    {"item": "Engine oil", "unit": "%", "group": "Fluids"},
    {"item": "Cabin filter", "unit": None, "group": "Filters"},
    {"item": "Wipers", "unit": None, "group": "Visibility"},
]
_EV_ITEMS = [
    {"item": "HV battery SoH", "unit": "%", "group": "High voltage", "hv": True},
    {"item": "Charge port", "unit": None, "group": "High voltage", "hv": True},
    {"item": "Coolant", "unit": "%", "group": "Thermal loops"},
    {"item": "Front brakes", "unit": "mm", "group": "Brakes"},
    {"item": "Rear brakes", "unit": "mm", "group": "Brakes"},
    {"item": "Tires", "unit": "32nds", "group": "Tires & wheels"},
    {"item": "Battery", "unit": "V", "group": "Electrical (12V)"},
    {"item": "Cabin filter", "unit": None, "group": "Filters"},
    {"item": "Wipers", "unit": None, "group": "Visibility"},
]


class NotFound(Exception):
    pass


class Forbidden(Exception):
    pass


class Invalid(ValueError):
    pass


def _now_dt() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def _iso(d: _dt.datetime) -> str:
    return d.isoformat().replace("+00:00", "Z")


def _now() -> str:
    return _iso(_now_dt())


def _parse(ts: str | None) -> _dt.datetime | None:
    if not ts:
        return None
    try:
        return _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def _local_label(ts: str | None, tz: _dt.tzinfo) -> str:
    """A stored UTC time as the store reads it: "Nov 4, 7:05 AM" (— when absent)."""
    d = _parse(ts)
    if d is None:
        return "—"
    loc = d.astimezone(tz)
    return f"{loc.strftime('%b')} {loc.day}, {loc.strftime('%I:%M %p').lstrip('0')}"


def _store_zone(dealer_id: str | None, cache: dict[str, _dt.tzinfo], env_prefix: str | None) -> _dt.tzinfo:
    key = dealer_id or ""
    if key not in cache:
        cache[key] = _zone(_dealer_timezone(dealer_id, env_prefix)) if dealer_id else _dt.timezone.utc
    return cache[key]


def _sj(sj_id: str, env_prefix: str | None = None) -> dict[str, Any]:
    sj = ddb.get_item(_SJ, {"service_journey_id": sj_id}, env_prefix)
    if sj is None:
        raise NotFound(f"service journey {sj_id} not found")
    return sj


def _ro(sj_id: str, env_prefix: str | None) -> dict[str, Any] | None:
    ros = ddb.scan_filter(_RO, "service_journey_id", sj_id, env_prefix)
    return ros[0] if ros else None


def _latest_estimate(sj_id: str, env_prefix: str | None) -> dict[str, Any] | None:
    ests = ddb.scan_filter(_ESTIMATES, "service_journey_id", sj_id, env_prefix)
    if not ests:
        return None
    return max(ests, key=lambda e: e.get("updated_at") or e.get("created_at") or e.get("presented_at") or "")


def _user_name(user_id: str | None, cache: dict[str, str], env_prefix: str | None) -> str | None:
    if not user_id:
        return None
    if user_id not in cache:
        u = ddb.get_item(_USERS, {"user_id": user_id}, env_prefix) or {}
        cache[user_id] = " ".join(filter(None, [u.get("given_name"), u.get("family_name")])) or user_id
    return cache[user_id]


def _customer_name(cid: str | None, env_prefix: str | None) -> str | None:
    if not cid:
        return None
    c = ddb.get_item("DEALER_Customers", {"customer_id": cid}, env_prefix) or {}
    return " ".join(filter(None, [c.get("given_name"), c.get("family_name")])) or None


def _vehicle_name(vin: str | None, env_prefix: str | None) -> str | None:
    from ..common import vehicles
    v = vehicles.for_vin(vin, env_prefix) if vin else {}
    return " ".join(str(x) for x in (v.get("year"), v.get("make"), v.get("model")) if x) or None


def _model_id(vin: str | None, env_prefix: str | None) -> str | None:
    if not vin:
        return None
    v = ddb.get_item(_VEHICLES, {"vin": vin}, env_prefix)
    return (v or {}).get("model_id")


def is_ev_model(model_id: str | None) -> bool:
    return bool(model_id and "VOLT" in model_id.upper()) or bool(model_id and model_id.upper().startswith(("NW-E", "NOR-E")))


def _overdue(sj: dict[str, Any], now: _dt.datetime) -> bool:
    """Past the promise or projected past it — the shared definition (service/metrics.py)."""
    return metrics.promise_state(sj, now) in ("past", "at_risk")


def _equity_flag(customer_id: str | None, cache: dict[str, Any], env_prefix: str | None,
                 vin: str | None = None) -> dict[str, Any] | None:
    """Positive equity in THE CAR ON THIS VISIT (common/vehicles.valuation_for) — never the
    customer's latest valuation, which after a sale is the car they traded in."""
    if not customer_id or not vin:
        return None
    key = f"{customer_id}|{vin}"
    if key not in cache:
        from ..common import vehicles
        v = vehicles.valuation_for(vin, customer_id, env_prefix)
        cache[key] = ({"equity": v.get("equity"), "currency": v.get("currency"),
                       "source_ref": f"DEALER_Valuations:{v.get('valuation_id')}"}
                      if v and (v.get("equity") or 0) > 0 else None)
    return cache[key]


# --- board -------------------------------------------------------------------

def _in_scope(claims: dict[str, Any], dealer_id: str | None) -> bool:
    return bool(claims.get("is_oem")) or dealer_id in (claims.get("dealer_ids") or set())


_TECH_WORK_STAGES = ("CHECK_IN", "INSPECT", "DIAGNOSE", "APPROVE", "REPAIR")


def _tech_still_holds(sj: dict[str, Any], env_prefix: str | None) -> bool:
    """A technician's list holds the visits they are working, plus the cars they finished
    TODAY (store time). A car finished on an earlier day is the advisor's to hand back — it
    no longer sits in the bay list (yesterday's READY visit stayed there for good)."""
    st = sj.get("stage")
    if st in _TECH_WORK_STAGES:
        return True
    if st != "READY":
        return False
    done = _parse(sj.get("updated_at"))
    if done is None:
        return False
    tz = _zone(_dealer_timezone(sj.get("dealer_id") or "", env_prefix))
    return done.astimezone(tz).date() == _now_dt().astimezone(tz).date()


def list_journeys(claims: dict[str, Any], filters: dict[str, str] | None = None,
                  env_prefix: str | None = None) -> dict[str, Any]:
    f = filters or {}
    market = f.get("market") or claims.get("market_id") or "us"
    role = claims.get("role")
    rows = [r for r in ddb.scan_filter(_SJ, "market_id", market, env_prefix) if _in_scope(claims, r.get("dealer_id"))]
    in_scope = list(rows)                  # the whole scope, before filters: the summary counts the store, not the view
    if role in TECH_ROLES:
        # technicians see their own ROs, plus unassigned ones when picking up work
        want_unassigned = f.get("unassigned") == "1"
        rows = [r for r in rows if (r.get("technician_id") == claims.get("user_id") and _tech_still_holds(r, env_prefix))
                or (want_unassigned and not r.get("technician_id")
                    and r.get("stage") in ("CHECK_IN", "INSPECT", "DIAGNOSE", "REPAIR"))]
    elif f.get("mine") == "1":
        rows = [r for r in rows if r.get("advisor_id") == claims.get("user_id")]
    for k in ("advisor_id", "technician_id", "wait_type", "stage", "dealer_id"):
        if f.get(k):
            rows = [r for r in rows if r.get(k) == f[k]]
    now = _now_dt()
    if f.get("day") in ("today", "tomorrow"):
        day = (now + _dt.timedelta(days=1 if f["day"] == "tomorrow" else 0)).date()
        rows = [r for r in rows if (_parse(r.get("promised_at")) or _parse(r.get("opened_at")) or now).date() == day]

    names: dict[str, str] = {}
    equity_cache: dict[str, Any] = {}
    # "held for parts" is the shared set: the flag, or an open held order on the RO (service/metrics.py)
    held_ids = metrics.parts_hold_ids(in_scope, ddb.scan_all(_RO, env_prefix), ddb.scan_all(_ORDERS, env_prefix))
    cards = []
    for r in rows:
        sj_id = r["service_journey_id"]
        est = _latest_estimate(sj_id, env_prefix)
        model = _model_id(r.get("vin"), env_prefix)
        waiting = bool(est and est.get("status") == "presented")
        card = {
            "service_journey_id": sj_id, "stage": r.get("stage"), "vin": r.get("vin"), "model_id": model,
            # the screen shows the car's name, never the catalog code (MER-VOLT-C)
            "vehicle_name": _vehicle_name(r.get("vin"), env_prefix),
            "dealer_id": r.get("dealer_id"), "wait_type": r.get("wait_type"),
            "promised_at": r.get("promised_at"), "projected_at": r.get("projected_at"),
            "advisor_id": r.get("advisor_id"), "advisor_name": _user_name(r.get("advisor_id"), names, env_prefix),
            "technician_id": r.get("technician_id"),
            "technician_name": _user_name(r.get("technician_id"), names, env_prefix),
            "badges": {"waiting_approval": waiting, "parts_hold": sj_id in held_ids,
                       "overdue_promise": _overdue(r, now), "ev": is_ev_model(model),
                       "equity_flag": role not in TECH_ROLES and bool(_equity_flag(r.get("customer_id"), equity_cache, env_prefix, r.get("vin")))},
            "estimate_id": (est or {}).get("estimate_id"), "estimate_status": (est or {}).get("status"),
        }
        if role not in TECH_ROLES:
            c = ddb.get_item(_CUSTOMERS, {"customer_id": r.get("customer_id")}, env_prefix) if r.get("customer_id") else None
            card["customer_name"] = " ".join(filter(None, [(c or {}).get("given_name"), (c or {}).get("family_name")])) or r.get("customer_id")
            card["customer_id"] = r.get("customer_id")
        cards.append(card)
    cards.sort(key=lambda c: c.get("promised_at") or "")
    rows_by_id = {r["service_journey_id"]: r for r in rows}
    lanes = {st: [c for c in cards if c["stage"] == st] for st in SERVICE_STAGES}
    kpis = {
        "lane_load": len([c for c in cards if c["stage"] not in ("FOLLOW_UP",)]),
        "shared_metrics": metrics.summary(in_scope, ddb.scan_all(_RO, env_prefix), ddb.scan_all(_ORDERS, env_prefix),
                                          [e for e in ddb.scan_all(_ESTIMATES, env_prefix)
                                           if e.get("service_journey_id") in {s["service_journey_id"] for s in in_scope}], now),
        "waiting_approval": sum(1 for c in cards if c["badges"]["waiting_approval"]),
        "parts_hold": sum(1 for c in cards if c["badges"]["parts_hold"]),
        "overdue_promise": sum(1 for c in cards if c["badges"]["overdue_promise"]),
        "promise_past": sum(1 for c in cards if metrics.promise_state(rows_by_id.get(c["service_journey_id"], {}), now) == "past"),
        "promise_at_risk": sum(1 for c in cards if metrics.promise_state(rows_by_id.get(c["service_journey_id"], {}), now) == "at_risk"),
        "ready_for_pickup": len(lanes["READY"]),
    }
    advisors = sorted({(c["advisor_id"], c.get("advisor_name")) for c in cards if c.get("advisor_id")})
    techs = sorted({(c["technician_id"], c.get("technician_name")) for c in cards if c.get("technician_id")})
    return {"market_id": market, "stages": SERVICE_STAGES, "lanes": lanes, "cards": cards, "kpis": kpis,
            "filters": {"advisors": [{"id": a, "name": n} for a, n in advisors],
                        "technicians": [{"id": t, "name": n} for t, n in techs],
                        "wait_types": sorted({c["wait_type"] for c in cards if c.get("wait_type")})},
            "masked": role in TECH_ROLES,
            "source_ref": "A7 board read model (DEALER_ServiceJourneys + estimates + valuations)"}


def arrivals(claims: dict[str, Any], env_prefix: str | None = None) -> list[dict[str, Any]]:
    """BOOK-stage journeys not yet checked in (right rail, §5.20.1)."""
    rows = [r for r in ddb.scan_filter(_SJ, "stage", "BOOK", env_prefix) if _in_scope(claims, r.get("dealer_id"))]
    out, zones = [], {}
    now = _now_dt()
    for r in sorted(rows, key=lambda x: x.get("promised_at") or x.get("opened_at") or ""):
        appts = ddb.scan_filter(_APPTS, "service_journey_id", r["service_journey_id"], env_prefix)
        a = appts[0] if appts else {}
        # "today's appointments — not arrived": due today in the store's own day, or already
        # missed; a booking for Sunday is not waiting at the door on Friday
        slot = _parse(a.get("slot_start") or r.get("promised_at"))
        tz = _store_zone(r.get("dealer_id"), zones, env_prefix)
        if slot is not None and slot.astimezone(tz).date() > now.astimezone(tz).date():
            continue
        out.append({"service_journey_id": r["service_journey_id"], "vin": r.get("vin"),
                    "slot_start": a.get("slot_start") or r.get("promised_at"), "wait_type": r.get("wait_type"),
                    "services": a.get("services") or r.get("services") or []})
    return out


def attention(claims: dict[str, Any], env_prefix: str | None = None) -> dict[str, Any]:
    now = _now_dt()
    items = []
    zones: dict[str, _dt.tzinfo] = {}
    sjs = {r["service_journey_id"]: r for r in ddb.scan_all(_SJ, env_prefix) if _in_scope(claims, r.get("dealer_id"))}
    for e in ddb.scan_filter(_ESTIMATES, "status", "presented", env_prefix):
        sj = sjs.get(e.get("service_journey_id"))
        pres = _parse(e.get("presented_at"))
        if sj and pres and (now - pres).total_seconds() > _WAITING_MIN * 60:
            items.append({"kind": "estimate_waiting", "severity": "medium",
                          "service_journey_id": sj["service_journey_id"], "estimate_id": e["estimate_id"],
                          "title": f"{_customer_name(sj.get('customer_id'), env_prefix) or 'A customer'}: estimate waiting for a decision",
                          "detail": f"presented {int((now - pres).total_seconds() // 60)} min ago",
                          "since": e.get("presented_at")})
    for sj in sjs.values():
        if _overdue(sj, now):
            tz = _store_zone(sj.get("dealer_id"), zones, env_prefix)
            promised = _parse(sj.get("promised_at"))
            missed = bool(promised and now > promised)
            who = _customer_name(sj.get("customer_id"), env_prefix) or _vehicle_name(sj.get("vin"), env_prefix) or "a visit"
            # the times as the store reads them; the raw UTC values ride along for the console. A promise
            # already behind us is "past"; one the projection will overrun is "at risk" — not the same thing
            items.append({"kind": "past_promise", "severity": "high" if missed else "medium",
                          "service_journey_id": sj["service_journey_id"], "missed": missed,
                          "title": f"{who}: {'past the promised time' if missed else 'at risk of missing the promised time'}",
                          "detail": f"promised {_local_label(sj.get('promised_at'), tz)} · "
                                    f"projected {_local_label(sj.get('projected_at'), tz)}",
                          "promised_at": sj.get("promised_at"), "projected_at": sj.get("projected_at"),
                          "since": sj.get("promised_at")})
    ros = {r["ro_id"]: r for r in ddb.scan_all(_RO, env_prefix)}
    for o in ddb.scan_filter(_ORDERS, "status", "backordered", env_prefix):
        ro = ros.get(o.get("ro_id")) or {}
        sj = sjs.get(ro.get("service_journey_id"))
        if sj:
            eta = o.get("eta")
            tz = _store_zone(sj.get("dealer_id"), zones, env_prefix)
            items.append({"kind": "backorder", "severity": "medium", "service_journey_id": sj["service_journey_id"],
                          "title": f"{_customer_name(sj.get('customer_id'), env_prefix) or 'A visit'}: a part is on back-order",
                          "detail": f"ETA {_local_label(eta, tz) if _parse(eta) else (eta or 'unknown')}",
                          "eta": eta, "since": eta})
    rank = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda i: (rank.get(i["severity"], 3), i.get("since") or ""))
    return {"count": len(items), "items": items, "threshold_minutes": _WAITING_MIN,
            "source_ref": "A7 attention list (estimates, promises, parts orders)"}


# --- RO view (role-masked) ---------------------------------------------------------

_PRICE_KEYS = ("labor_amount", "labor_rate", "parts_amount", "line_total", "customer_amount", "formula",
               "unit_price", "amount")


def _mask_line(line: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in line.items() if k not in _PRICE_KEYS and k != "coverage"}
    out["parts"] = [{k: v for k, v in p.items() if k not in _PRICE_KEYS} for p in line.get("parts") or []]
    out["coverage"] = {"pay_type": (line.get("coverage") or {}).get("pay_type")}
    return out


def journey_detail(sj_id: str, role: str | None, env_prefix: str | None = None) -> dict[str, Any]:
    sj = _sj(sj_id, env_prefix)
    tech = role in TECH_ROLES
    ro = _ro(sj_id, env_prefix)
    est = _latest_estimate(sj_id, env_prefix)
    lines: list[dict[str, Any]] = []
    if est:
        lines, _ = est_mod.lines_for(est, env_prefix)
    parts = ddb.scan_filter(_PARTS, "ro_id", ro["ro_id"], env_prefix) if ro else []
    orders = ddb.scan_filter(_ORDERS, "ro_id", ro["ro_id"], env_prefix) if ro else []
    inspections = sorted(ddb.scan_filter(_INSPECTIONS, "service_journey_id", sj_id, env_prefix),
                         key=lambda i: {"red": 0, "yellow": 1, "green": 2}.get(i.get("status"), 3))
    events = sorted(ddb.query_hash(_EVENTS, "service_journey_id", sj_id, env_prefix),
                    key=lambda e: e.get("event_ts") or "", reverse=True)
    vehicle = ddb.get_item(_VEHICLES, {"vin": sj.get("vin")}, env_prefix) if sj.get("vin") else None
    names: dict[str, str] = {}
    out: dict[str, Any] = {
        "service_journey_id": sj_id, "stage": sj.get("stage"), "market_id": sj.get("market_id"),
        "dealer_id": sj.get("dealer_id"), "wait_type": sj.get("wait_type"),
        "promised_at": sj.get("promised_at"), "projected_at": sj.get("projected_at"),
        "overdue_promise": _overdue(sj, _now_dt()), "parts_hold": bool(sj.get("parts_hold")),
        "tag_number": sj.get("tag_number") or sj_id.split("-")[-1],
        "vehicle": {**vehicle_names.display_fields(
                        {"vin": sj.get("vin"),
                         **{k: (vehicle or {}).get(k) for k in ("model_id", "trim_id", "odometer", "year", "make", "model")}},
                        env_prefix),
                    "mileage_in": sj.get("mileage_in"), "ev": is_ev_model((vehicle or {}).get("model_id"))},
        "concerns": sj.get("concerns") or [],
        "advisor": {"id": sj.get("advisor_id"), "name": _user_name(sj.get("advisor_id"), names, env_prefix)},
        "technician": {"id": sj.get("technician_id"), "name": _user_name(sj.get("technician_id"), names, env_prefix)},
        "repair_order": ro and {"ro_id": ro.get("ro_id"), "ro_number": ro.get("ro_number"), "status": ro.get("status"),
                                "opened_at": ro.get("opened_at"), "dms_ref": f"DMS:{ro.get('ro_number')}"},
        "handoff": svc_supervisor.validate_service_handoff(sj, env_prefix),
        "lines": [_mask_line(l) for l in lines] if tech else lines,
        "parts": [{"part_id": p.get("part_id"), "part_number": p.get("part_number"), "description": p.get("description"),
                   "qty": p.get("qty"), "on_hand": bool(p.get("on_hand")), "line_status": p.get("line_status") or "pending",
                   **({} if tech else {"unit_price": p.get("unit_price"), "currency": p.get("currency")})} for p in parts],
        "parts_orders": [{k: o.get(k) for k in ("parts_order_id", "status", "eta", "part_number", "qty", "supersedes")} for o in orders],
        "inspection": [{k: i.get(k) for k in ("inspection_id", "item", "status", "urgency", "measurement", "unit",
                                               "evidence_doc_id", "note", "dtc_codes")} for i in inspections],
        "estimate": None if not est else (
            {"estimate_id": est.get("estimate_id"), "status": est.get("status")} if tech else
            {k: est.get(k) for k in ("estimate_id", "status", "total", "currency", "presented_at", "decided_at")}),
        "timeline": [{"ts": e.get("event_ts"), "type": e.get("event_type"), "evidence": e.get("evidence"),
                      "actor": (e.get("actor") or {}).get("user_id") or (e.get("actor") or {}).get("type")} for e in events[:50]],
        "masked": tech,
    }
    if not tech:
        c = ddb.get_item(_CUSTOMERS, {"customer_id": sj.get("customer_id")}, env_prefix) if sj.get("customer_id") else None
        out["customer"] = c and {"customer_id": c.get("customer_id"),
                                 "name": " ".join(filter(None, [c.get("given_name"), c.get("family_name")])),
                                 "email": c.get("email"), "phone": c.get("phone"),
                                 "approval_preference": sj.get("approval_preference")}
        out["documents"] = [{k: d.get(k) for k in ("document_id", "kind", "uri", "created_at", "tag")}
                            for d in ddb.scan_filter(_DOCS, "service_journey_id", sj_id, env_prefix)]
        out["declined_work"] = [{"declined_id": d.get("declined_item_id"), **{k: d.get(k) for k in ("item", "amount", "currency", "declined_at", "status")}}
                                for d in _declined_entries(env_prefix)
                                if sj.get("vin") and d.get("vin") == sj.get("vin") and d.get("service_journey_id") != sj_id]
    else:
        out["documents"] = []
    if role in ("dealer_service", "dealer_service_manager", "dealer_manager"):
        eq = _equity_flag(sj.get("customer_id"), {}, env_prefix, sj.get("vin"))
        upsell = [i for i in inspections if i.get("status") == "yellow"]
        out["service_lane"] = {"equity": eq,
                               "upsell": [{"item": i.get("item"), "finding_ref": i.get("inspection_id")} for i in upsell],
                               "source_ref": "A3 upsell brief (yellow findings) + A4 equity (DEALER_Valuations)"}
    return out


def claim(sj_id: str, user_id: str, env_prefix: str | None = None) -> dict[str, Any]:
    """A technician picks up an unassigned RO (dispatch by self-service)."""
    sj = dict(_sj(sj_id, env_prefix))
    if sj.get("technician_id") and sj["technician_id"] != user_id:
        raise Forbidden("RO already assigned to another technician")
    sj["technician_id"] = user_id
    sj["updated_at"] = _now()
    ddb.put_item(_SJ, sj, env_prefix)
    ev = svc_supervisor._event_row(sj_id, "ro.updated", user_id, f"technician {user_id} assigned", {"technician_id": user_id}, sj["updated_at"])
    ddb.put_item(_EVENTS, ev, env_prefix)
    _bus.emit("ro.updated", {"service_journey_id": sj_id, "technician_id": user_id, "dealer_id": sj.get("dealer_id")},
              actor=user_id, resource_url=f"/r/service_journey/{sj_id}", env_prefix=env_prefix)
    return {"service_journey_id": sj_id, "technician_id": user_id}


# --- check-in (§5.20.2) -----------------------------------------------------------

def checkin(sj_id: str, *, actor: str, mileage: int | float | None, concerns: list[str] | None,
            photos: list[dict[str, Any]] | None, approval_preference: str | None,
            promised_at: str | None, add_declined: list[str] | None = None,
            env_prefix: str | None = None) -> dict[str, Any]:
    sj = dict(_sj(sj_id, env_prefix))
    if sj.get("stage") not in ("BOOK", "CHECK_IN"):
        raise Invalid(f"{sj_id} is at {sj.get('stage')}; check-in happens at BOOK/CHECK_IN")
    if mileage is None or float(mileage) <= 0:
        raise Invalid("mileage is required")
    if approval_preference and approval_preference not in ("text", "call", "my_garage", "email"):
        raise Invalid("approval_preference must be text, call, my_garage, or email")
    ts = _now()
    docs = []
    for i, ph in enumerate(photos or []):
        doc_id = "SDOC-" + hashlib.sha1(f"{sj_id}|{i}|{ph.get('name')}|{ts}".encode()).hexdigest()[:12]
        row = {"document_id": doc_id, "customer_id": sj.get("customer_id"), "service_journey_id": sj_id,
               "kind": "walkaround_photo", "tag": ph.get("tag"), "note": ph.get("note"),
               "uri": f"app-docs/service/{sj_id}/{doc_id}.jpg", "thumb": (ph.get("thumb") or "")[:60000] or None,
               "created_at": ts, "created_by": actor, "data_class": "C3", "retention_class": "operational",
               "source_ref": "A7.checkin (walk-around)"}
        ddb.put_item(_DOCS, row, env_prefix)
        docs.append(doc_id)
    sj.update({"mileage_in": float(mileage), "concerns": [c for c in (concerns or []) if c.strip()],
               "approval_preference": approval_preference, "checked_in_at": ts, "checked_in_by": actor,
               "updated_at": ts})
    # the advisor who writes the car up owns the visit (the route admits advisor roles only); a booking
    # made from My Garage arrives with advisor_id=None, which otherwise shows as "Advisor —" on every RO screen
    if not sj.get("advisor_id"):
        sj["advisor_id"] = actor
    if promised_at:
        sj["promised_at"] = promised_at
    if add_declined:
        sj["added_declined"] = list(dict.fromkeys(list(sj.get("added_declined") or []) + add_declined))
    if sj.get("vin"):
        v = ddb.get_item(_VEHICLES, {"vin": sj["vin"]}, env_prefix)
        if v and float(mileage) > float(v.get("odometer") or 0):
            v = dict(v); v["odometer"] = float(mileage); ddb.put_item(_VEHICLES, v, env_prefix)
    ddb.put_item(_SJ, sj, env_prefix)
    advanced = None
    if sj.get("stage") == "BOOK":
        advanced = svc_supervisor.advance_service_journey(sj_id, f"checked in at {mileage}", actor, env_prefix=env_prefix)
    from .connectors import open_repair_order
    ro = open_repair_order(sj_id, actor=actor, env_prefix=env_prefix)
    if add_declined:
        from .followup import add_declined_items
        for did in add_declined:
            row = _declined_row(did, env_prefix)
            if row:
                add_declined_items(row.get("customer_id"), [{"declined_item_id": did, "status": "added_to_ro",
                                                             "added_to_sj": sj_id}], env_prefix)
    return {"service_journey_id": sj_id, "ro": ro, "advanced": advanced, "documents": docs,
            "stage": "CHECK_IN", "mileage_in": float(mileage)}


# --- scheduling sandbox (§5.20.2) ---------------------------------------------------

def _dealer_timezone(dealer_id: str, env_prefix: str | None) -> str:
    """The store's IANA zone (PLAT_OrgHierarchy), else its market's default, else UTC."""
    node = next((n for n in ddb.scan_filter("PLAT_OrgHierarchy", "node_id", dealer_id, env_prefix)), None) or {}
    if node.get("timezone"):
        return str(node["timezone"])
    mkt = ddb.get_item("OEM_Markets", {"market_id": node.get("market_id") or "us"}, env_prefix) or {}
    return str(mkt.get("timezone_default") or "UTC")


def _zone(name: str) -> _dt.tzinfo:
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - an unknown zone (or no tz database) keeps UTC
        return _dt.timezone.utc


def availability(dealer_id: str, date: str | None = None, env_prefix: str | None = None) -> dict[str, Any]:
    day = (_parse(f"{date}T00:00:00Z") if date else _now_dt()).date()
    appts = [a for a in ddb.scan_filter(_APPTS, "dealer_id", dealer_id, env_prefix)
             if str(a.get("type", "")).startswith("service") and a.get("status") not in ("cancelled",)]
    def _out(wait_type: str) -> int:
        return sum(1 for a in appts if a.get("wait_type") == wait_type
                   and (a.get("slot_start") or "")[:10] == day.isoformat())

    loaners_out = _out("loaner")
    # Lane hours are the STORE's local hours (PLAT_OrgHierarchy timezone, e.g. Decatur →
    # America/New_York): 08:00–16:00 there, stored as UTC. Built in UTC they read as
    # midnight–8 AM to anyone west of Greenwich.
    tz_name = _dealer_timezone(dealer_id, env_prefix)
    tz = _zone(tz_name)
    slots = []
    for h in _SLOT_HOURS:
        start = _dt.datetime(day.year, day.month, day.day, h, tzinfo=tz).astimezone(_dt.timezone.utc)
        booked = sum(1 for a in appts if _parse(a.get("slot_start")) == start)
        slots.append({"slot_start": _iso(start), "local_time": f"{h:02d}:00", "booked": booked,
                      "capacity": _LANE_CAPACITY, "available": max(0, _LANE_CAPACITY - booked)})
    wait_types = ["wait", "drop", "loaner", "mobile", "pickup_delivery"]
    # Mobile vans and pickup-delivery drivers are reported the same way loaners
    # are: fleet size minus the day's bookings of that wait type (§3.7 — offer
    # only wait types that still have capacity on the chosen day).
    mobile_out, drivers_out = _out("mobile"), _out("pickup_delivery")
    return {"dealer_id": dealer_id, "date": day.isoformat(), "timezone": tz_name, "slots": slots,
            "loaners": {"total": _LOANERS, "available": max(0, _LOANERS - loaners_out),
                        "booked": loaners_out},
            "mobile": {"total": _MOBILE_VANS, "available": max(0, _MOBILE_VANS - mobile_out),
                       "booked": mobile_out, "unit": "van"},
            "drivers": {"total": _PICKUP_DRIVERS, "available": max(0, _PICKUP_DRIVERS - drivers_out),
                        "booked": drivers_out, "unit": "driver"},
            "wait_types": wait_types,
            "capacity_basis": "lane bays per hour; loaner / mobile-van / pickup-driver fleet "
                              "minus that day's bookings of the wait type (DEALER_Appointments)",
            "source_ref": "scheduling connector (sandbox: DEALER_Appointments + lane capacity)"}


def reschedule(appointment_id: str, *, actor: str, slot_start: str | None, cancel: bool,
               env_prefix: str | None = None) -> dict[str, Any]:
    a = ddb.get_item(_APPTS, {"appointment_id": appointment_id}, env_prefix)
    if a is None:
        raise NotFound(f"appointment {appointment_id} not found")
    a = dict(a)
    if cancel:
        a["status"] = "cancelled"; etype = "service.appointment.cancelled"
    else:
        if not slot_start:
            raise Invalid("slot_start is required to reschedule")
        a["slot_start"] = slot_start; a["status"] = "booked"; etype = "service.appointment.changed"
    a["updated_at"] = _now(); a["updated_by"] = actor
    ddb.put_item(_APPTS, a, env_prefix)
    _bus.emit(etype, {"appointment_id": appointment_id, "service_journey_id": a.get("service_journey_id"),
                      "dealer_id": a.get("dealer_id"), "slot_start": a.get("slot_start")},
              actor=actor, resource_url=f"/r/appointment/{appointment_id}", env_prefix=env_prefix)
    return a


# --- templates + diagnose (§5.20.3) --------------------------------------------------

def templates(market: str, model_id: str | None = None, env_prefix: str | None = None) -> dict[str, Any]:
    """The market's MPI templates (ICE/Hybrid, EV, Express) with their items.

    Seeded DEALER_InspectionTemplates rows supply version + provenance; the
    three standard templates always exist so a technician is never left
    without a checklist."""
    seeded = {}
    for t in ddb.scan_filter("DEALER_InspectionTemplates", "market_id", market, env_prefix):
        name = (t.get("name") or "").lower()
        kind = "exp" if not t.get("has_mpi") else ("ev" if name.startswith("ev") or " ev " in f" {name} " else "ice")
        seeded[kind] = t
    m = market.upper()
    base = {"ice": (f"MPI-{m}-ICE-v3", "ICE/Hybrid MPI", True, _ICE_ITEMS),
            "ev": (f"MPI-{m}-EV-v3", "EV MPI", True, _EV_ITEMS),
            "exp": (f"MPI-{m}-EXP-v1", "Express (no MPI)", False, [])}
    out = []
    for kind, (tid, name, has_mpi, items) in base.items():
        row = seeded.get(kind, {})
        out.append({"template_id": (row.get("template_id") or tid).replace(" ", ""), "kind": kind,
                    "name": row.get("name") or name, "version": row.get("version") or int(tid[-1]),
                    "has_mpi": row.get("has_mpi", has_mpi), "items": items,
                    "evidence_required_for": ["yellow", "red"],
                    "source_ref": row.get("source_ref") or "prior:vsa.json + OEM schedules"})
    # An unidentified model (trade-in, other-brand vehicle in the service lane)
    # still gets the ICE/Hybrid checklist rather than no checklist at all.
    kind = "ev" if model_id and is_ev_model(model_id) else "ice"
    recommended = next(t["template_id"] for t in out if t["kind"] == kind)
    return {"market_id": market, "templates": out, "recommended": recommended}


def express_menu(dealer_id: str | None, market: str, env_prefix: str | None = None) -> dict[str, Any]:
    """The fixed express-service menu (§3.7 `express` service class: fixed menu,
    no MPI).

    Derived from the market's labor guide — the quick-service ops at or under the
    express time cap — plus the Express (no-MPI) inspection template, so every
    item carries its flat-rate hours and price basis. EV-specific and diagnostic
    ops are excluded: express lanes do not take diagnosis or HV work."""
    # DEALER_LaborOps is keyed by labor_op_id alone, so a market filter can come
    # back empty where another market's row holds the id (same tolerance as
    # estimates._labor): fall back to the guide across markets rather than
    # returning an empty menu.
    guide = labor_ops(market, env_prefix) or [
        {k: o.get(k) for k in ("labor_op_id", "name", "flat_rate_hours", "shop_rate", "currency", "source_ref")}
        for o in ddb.scan_all("DEALER_LaborOps", env_prefix)]
    ops = [o for o in guide
           if o.get("flat_rate_hours") is not None
           and float(o["flat_rate_hours"]) <= _EXPRESS_MAX_HOURS
           and not any(x in (o.get("labor_op_id") or "") for x in _EXPRESS_EXCLUDE)]
    ops.sort(key=lambda o: float(o["flat_rate_hours"]))
    items = []
    for o in ops:
        hours = float(o["flat_rate_hours"])
        rate = float(o.get("shop_rate") or 0)
        items.append({"labor_op_id": o.get("labor_op_id"), "name": o.get("name"),
                      "flat_rate_hours": hours, "minutes": int(round(hours * 60)),
                      "labor_price": round(hours * rate, 2), "currency": o.get("currency", "USD"),
                      "source_ref": f"DEALER_LaborOps:{o.get('labor_op_id')}"})
    tpl = next((t for t in templates(market, None, env_prefix)["templates"] if t["kind"] == "exp"), None)
    total_minutes = sum(i["minutes"] for i in items)
    return {"market_id": market, "dealer_id": dealer_id, "service_class": "express",
            "items": items, "count": len(items),
            "has_mpi": False, "template_id": (tpl or {}).get("template_id"),
            "max_flat_rate_hours": _EXPRESS_MAX_HOURS,
            "promise_minutes": total_minutes,
            "note": "Express: fixed-menu work only, no multi-point inspection; "
                    "diagnosis and high-voltage work are booked as standard service.",
            "source_ref": "A7 express menu (DEALER_LaborOps under the express time cap "
                          "+ DEALER_InspectionTemplates Express)"}


def _knowledge(kind: str, ref: str, env_prefix: str | None) -> dict[str, Any] | None:
    for r in ddb.scan_filter("PLAT_KnowledgeCollections", "kind", kind, env_prefix):
        if ref and ref.upper() in (r.get("title") or "").upper():
            return {"doc_id": r.get("doc_id"), "title": r.get("title"), "collection_id": r.get("collection_id"),
                    "source_ref": f"quick_index:{r.get('collection_id')}:{r.get('doc_id')}"}
    return None


def get_repair_procedure(labor_op_id: str, vin: str | None = None, env_prefix: str | None = None) -> dict[str, Any]:
    """Repair procedure for a labor op, cited from the indexed procedures (Quick
    Index / KnowledgeBase sandbox). No citation → the procedure is not shown."""
    from .connectors import get_labor_time
    labor = get_labor_time(labor_op_id, env_prefix)
    doc = _knowledge("repair_procedures", labor_op_id, env_prefix)
    return {"labor_op_id": labor_op_id, "name": labor.get("name"), "vin": vin,
            "flat_rate_hours": labor.get("flat_rate_hours"), "citation": doc,
            "steps": [f"Follow OEM procedure {doc['title']}" if doc else "No indexed procedure — consult OEM service information",
                      f"Flat-rate {labor.get('flat_rate_hours')} h per {labor.get('source_ref')}"],
            "source_ref": doc["source_ref"] if doc else labor.get("source_ref")}


def diagnose(codes: list[str], vin: str | None = None, env_prefix: str | None = None) -> dict[str, Any]:
    from .connectors import lookup_dtc
    out = []
    for code in [c.strip().upper() for c in codes if c.strip()]:
        d = lookup_dtc(code, env_prefix)
        d["tsb"] = _knowledge("tsbs", code, env_prefix) if d.get("found") else None
        d["likely_labor_ops"] = (["LOP-DIAG"] + (["LOP-BATT"] if code.startswith("B13") else [])
                                 + (["LOP-EV-HV-INSPECT"] if code.startswith("P0A") else []))
        d["recommended_tests"] = ["Scan tool freeze-frame review", "Verify concern on road test"] if d.get("found") else []
        out.append(d)
    return {"vin": vin, "codes": out}


def labor_ops(market: str, env_prefix: str | None = None) -> list[dict[str, Any]]:
    return [{k: r.get(k) for k in ("labor_op_id", "name", "flat_rate_hours", "shop_rate", "currency", "source_ref")}
            for r in ddb.scan_filter("DEALER_LaborOps", "market_id", market, env_prefix)]


# --- parts desk (§5.20.6) --------------------------------------------------------------

def parts_desk(claims: dict[str, Any], env_prefix: str | None = None) -> dict[str, Any]:
    sjs = {r["service_journey_id"]: r for r in ddb.scan_all(_SJ, env_prefix) if _in_scope(claims, r.get("dealer_id"))}
    ros = {r["ro_id"]: r for r in ddb.scan_all(_RO, env_prefix) if r.get("service_journey_id") in sjs}
    orders = [o for o in ddb.scan_all(_ORDERS, env_prefix) if o.get("ro_id") in ros]
    for o in orders:
        ro = ros[o["ro_id"]]
        o["service_journey_id"] = ro.get("service_journey_id")
        o["vin"] = sjs[ro["service_journey_id"]].get("vin")
    held_ros = {}
    for o in orders:
        if o.get("status") in _HELD:
            held_ros.setdefault(o["ro_id"], {"ro_id": o["ro_id"], "service_journey_id": o["service_journey_id"],
                                             "vin": o.get("vin"), "orders": 0, "soonest_eta": None})
            h = held_ros[o["ro_id"]]
            h["orders"] += 1
            if o.get("eta") and (not h["soonest_eta"] or o["eta"] < h["soonest_eta"]):
                h["soonest_eta"] = o["eta"]
    open_orders = sorted([o for o in orders if o.get("status") not in ("received", "cancelled")],
                         key=lambda o: o.get("eta") or "9999")
    return {"held_ros": sorted(held_ros.values(), key=lambda h: h.get("soonest_eta") or "9999"),
            "open_orders": open_orders,
            "backorders": [o for o in open_orders if o.get("status") == "backordered"],
            "received_recent": sorted([o for o in orders if o.get("status") == "received"],
                                      key=lambda o: o.get("received_at") or "", reverse=True)[:20],
            "statuses": list(_ORDER_STATUSES), "source_ref": "DEALER_PartsOrders + DEALER_RepairOrders"}


def _ro_scope(ro_id: str, claims: dict[str, Any], env_prefix: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    ro = ddb.get_item(_RO, {"ro_id": ro_id}, env_prefix)
    if ro is None:
        raise NotFound(f"repair order {ro_id} not found")
    sj = _sj(ro["service_journey_id"], env_prefix)
    if not _in_scope(claims, sj.get("dealer_id")):
        raise Forbidden("repair order outside caller scope")
    return ro, sj


def create_parts_order(claims: dict[str, Any], ro_id: str, part_number: str, qty: int,
                       eta: str | None, status: str = "ordered", supersedes: str | None = None,
                       env_prefix: str | None = None) -> dict[str, Any]:
    ro, sj = _ro_scope(ro_id, claims, env_prefix)
    if status not in ("ordered", "backordered", "in_transit"):
        raise Invalid("a new order is ordered, in_transit, or backordered")
    if int(qty) <= 0:
        raise Invalid("qty must be positive")
    ts = _now()
    oid = "PO-" + hashlib.sha1(f"{ro_id}|{part_number}|{ts}".encode()).hexdigest()[:10]
    # the RO's part this order is for: a part on order is not on the shelf (the desk showed the brake
    # kit we "ordered" as on hand), and the order names what it is
    part = next((p for p in ddb.scan_filter("DEALER_Parts", "ro_id", ro_id, env_prefix) if p.get("part_number") == part_number), None)
    if part is not None and part.get("on_hand"):
        ddb.put_item("DEALER_Parts", {**part, "on_hand": False, "ordered_at": ts, "parts_order_id": oid}, env_prefix)
    row = {"parts_order_id": oid, "ro_id": ro_id, "part_number": part_number, "qty": int(qty), "status": status,
           "description": (part or {}).get("description"), "part_id": (part or {}).get("part_id"),
           "eta": eta, "supersedes": supersedes, "created_at": ts, "created_by": claims.get("user_id"),
           "source_ref": "parts order (dealer parts system)"}
    ddb.put_item(_ORDERS, row, env_prefix)
    sj = dict(sj); sj["parts_hold"] = True; sj["updated_at"] = ts; ddb.put_item(_SJ, sj, env_prefix)
    _bus.emit("parts.backordered" if status == "backordered" else "parts.ordered",
              {"parts_order_id": oid, "ro_id": ro_id, "service_journey_id": sj["service_journey_id"],
               "dealer_id": sj.get("dealer_id"), "eta": eta}, actor=claims.get("user_id"),
              resource_url=f"/r/ro/{ro_id}", env_prefix=env_prefix)
    return row


def update_parts_order(claims: dict[str, Any], order_id: str, status: str | None, eta: str | None,
                       env_prefix: str | None = None) -> dict[str, Any]:
    o = ddb.get_item(_ORDERS, {"parts_order_id": order_id}, env_prefix)
    if o is None:
        raise NotFound(f"parts order {order_id} not found")
    ro, sj = _ro_scope(o["ro_id"], claims, env_prefix)
    if status and status not in _ORDER_STATUSES:
        raise Invalid(f"status must be one of {_ORDER_STATUSES}")
    o = dict(o)
    ts = _now()
    if eta:
        o["eta"] = eta
    prev = o.get("status")
    if status:
        o["status"] = status
        if status == "received":
            o["received_at"] = ts
    o["updated_at"] = ts
    ddb.put_item(_ORDERS, o, env_prefix)
    still_held = [x for x in ddb.scan_filter(_ORDERS, "ro_id", o["ro_id"], env_prefix) if x.get("status") in _HELD]
    if bool(sj.get("parts_hold")) != bool(still_held):
        sj = dict(sj); sj["parts_hold"] = bool(still_held); sj["updated_at"] = ts; ddb.put_item(_SJ, sj, env_prefix)
    detail = {"parts_order_id": order_id, "ro_id": o["ro_id"], "service_journey_id": sj["service_journey_id"],
              "dealer_id": sj.get("dealer_id"), "eta": o.get("eta"), "parts_hold": bool(still_held)}
    if status == "received" and prev != "received":
        _bus.emit("parts.received", detail, actor=claims.get("user_id"), resource_url=f"/r/ro/{o['ro_id']}", env_prefix=env_prefix)
    elif status == "backordered" and prev != "backordered":
        _bus.emit("parts.backordered", detail, actor=claims.get("user_id"), resource_url=f"/r/ro/{o['ro_id']}", env_prefix=env_prefix)
    return {**o, "parts_hold": bool(still_held)}


# --- declined work register --------------------------------------------------------------

def _declined_entries(env_prefix: str | None) -> list[dict[str, Any]]:
    from .followup import declined_items
    out = []
    for row in ddb.scan_all(_DECLINED, env_prefix):
        out.extend(declined_items(row))
    return out


def _declined_row(declined_item_id: str, env_prefix: str | None) -> dict[str, Any] | None:
    return next((d for d in _declined_entries(env_prefix) if d.get("declined_item_id") == declined_item_id), None)


def declined(claims: dict[str, Any], due_before: str | None = None, status: str | None = None,
             env_prefix: str | None = None) -> dict[str, Any]:
    """Declined-work register: one entry per declined line (DEALER_DeclinedWork
    rows are per customer and hold `items`)."""
    sjs = {r["service_journey_id"]: r for r in ddb.scan_all(_SJ, env_prefix)}
    rows = []
    for d in _declined_entries(env_prefix):
        sj = sjs.get(d.get("service_journey_id")) or {}
        dealer = d.get("dealer_id") or sj.get("dealer_id")
        if not _in_scope(claims, dealer):
            continue
        cadence = int(d.get("follow_up_cadence_days") or 30)
        declined_at = _parse(d.get("declined_at"))
        due = _iso(declined_at + _dt.timedelta(days=cadence)) if declined_at else None
        # A follow-up the advisor scheduled (30/60/90 from the register) runs from the moment
        # it was scheduled; the page shows it as "Next follow-up" and highlights its cadence.
        scheduled_at = _parse(d.get("followup_scheduled_at"))
        followup_at = (_iso(scheduled_at + _dt.timedelta(days=cadence)) if scheduled_at else None)
        amount = d.get("amount")
        # A declined line recorded at $0 because coverage was misjudged (a cabin filter priced
        # as warranty) is customer-pay work: quote what the line actually costs.
        if not amount and d.get("line_total") and d.get("pay_type") != "warranty":
            amount = d["line_total"]
        r = {"declined_id": d.get("declined_item_id"), "item": d.get("item"), "amount": amount,
             "currency": d.get("currency"), "declined_at": d.get("declined_at"), "customer_id": d.get("customer_id"),
             "service_journey_id": d.get("service_journey_id"), "vin": d.get("vin") or sj.get("vin"),
             "reason": d.get("reason"), "evidence_snapshot": d.get("evidence_snapshot"),
             "dealer_id": dealer, "follow_up_cadence_days": cadence, "follow_up_due": followup_at or due,
             "cadence_days": cadence if scheduled_at else None, "followup_at": followup_at,
             "followup_scheduled_at": d.get("followup_scheduled_at"),
             "status": d.get("status") or "open"}
        if due_before and due and due > due_before:
            continue
        if status and r["status"] != status:
            continue
        rows.append(r)
    rows.sort(key=lambda r: r.get("follow_up_due") or "")
    return {"count": len(rows), "items": rows}


def update_declined(claims: dict[str, Any], declined_id: str, *, status: str | None, cadence_days: int | None,
                    env_prefix: str | None = None) -> dict[str, Any]:
    from .followup import add_declined_items, schedule_declined_followup
    d = _declined_row(declined_id, env_prefix)
    if d is None:
        raise NotFound(f"declined work {declined_id} not found")
    sj = ddb.get_item(_SJ, {"service_journey_id": d.get("service_journey_id")}, env_prefix) or {}
    if not _in_scope(claims, d.get("dealer_id") or sj.get("dealer_id")):
        raise Forbidden("declined work outside caller scope")
    upd = {"declined_item_id": declined_id}
    if status:
        if status not in ("open", "booked", "dismissed", "added_to_ro"):
            raise Invalid("status must be open, booked, dismissed, or added_to_ro")
        upd["status"] = status
    if cadence_days is not None:
        if int(cadence_days) not in (30, 60, 90):
            raise Invalid("cadence must be 30, 60, or 90 days")
        upd["follow_up_cadence_days"] = int(cadence_days)
        upd["followup_scheduled_at"] = _now()   # the register's "Next follow-up" runs from here
    upd["updated_at"] = _now()
    add_declined_items(d.get("customer_id"), [upd], env_prefix)
    if cadence_days is not None and d.get("service_journey_id"):
        try:
            schedule_declined_followup(d["service_journey_id"], [{"item": d.get("item"), "amount": d.get("amount"),
                                                                  "currency": d.get("currency"),
                                                                  "declined_item_id": declined_id,
                                                                  "declined_at": d.get("declined_at")}],
                                       cadence_days=int(cadence_days), actor=claims.get("user_id") or "A7",
                                       env_prefix=env_prefix)
        except Exception:
            pass
    # the register row as the page will show it (followup_at / cadence_days)
    shown = next((x for x in declined(claims, env_prefix=env_prefix)["items"] if x["declined_id"] == declined_id), None)
    return {**d, **upd, **(shown or {})}


# --- history (§5.20.6) ---------------------------------------------------------------

def history(vin: str, role: str | None, env_prefix: str | None = None) -> dict[str, Any]:
    tech = role in TECH_ROLES
    vehicle = ddb.get_item(_VEHICLES, {"vin": vin}, env_prefix)
    sjs = sorted(ddb.scan_filter(_SJ, "vin", vin, env_prefix), key=lambda s: s.get("opened_at") or "", reverse=True)
    visits = []
    for s in sjs:
        ro = _ro(s["service_journey_id"], env_prefix)
        est = _latest_estimate(s["service_journey_id"], env_prefix)
        insp = ddb.scan_filter(_INSPECTIONS, "service_journey_id", s["service_journey_id"], env_prefix)
        visits.append({
            "service_journey_id": s["service_journey_id"], "dealer_id": s.get("dealer_id"), "stage": s.get("stage"),
            "opened_at": s.get("opened_at"), "ro_number": (ro or {}).get("ro_number"),
            "ro_total": None if tech else (ro or {}).get("total"), "currency": (ro or {}).get("currency"),
            "estimate_status": (est or {}).get("status"),
            "inspection": {"red": sum(1 for i in insp if i.get("status") == "red"),
                           "yellow": sum(1 for i in insp if i.get("status") == "yellow"),
                           "green": sum(1 for i in insp if i.get("status") == "green")},
            "findings": [{k: i.get(k) for k in ("item", "status", "measurement", "unit")} for i in insp
                         if i.get("status") in ("red", "yellow")],
        })
    sj_ids = {s["service_journey_id"] for s in sjs}
    declined_rows = [{**d, "declined_id": d.get("declined_item_id")} for d in _declined_entries(env_prefix)
                     if d.get("vin") == vin or d.get("service_journey_id") in sj_ids]
    # prior estimates for this VIN (every visit's estimate, newest first; totals
    # masked for technicians exactly like RO totals are)
    estimate_rows = []
    for e in ddb.scan_all(_ESTIMATES, env_prefix):
        if e.get("service_journey_id") not in sj_ids:
            continue
        row = {"estimate_id": e.get("estimate_id"), "service_journey_id": e.get("service_journey_id"),
               "status": e.get("status"), "created_at": e.get("created_at"),
               "presented_at": e.get("presented_at"), "decided_at": e.get("decided_at"),
               "line_count": e.get("line_count") if e.get("line_count") is not None else len(e.get("lines") or []),
               "source_ref": e.get("source_ref") or f"DEALER_ServiceEstimates:{e.get('estimate_id')}"}
        if not tech:
            row.update({"total": e.get("total"), "approved_total": e.get("approved_total"),
                        "currency": e.get("currency")})
        estimate_rows.append(row)
    estimate_rows.sort(key=lambda e: (e.get("created_at") or e.get("presented_at") or ""), reverse=True)
    from .booking import predict_next_service
    try:
        nxt = predict_next_service(vin, env_prefix)
    except Exception:
        nxt = None
    named = vehicle_names.display_fields(vehicle, env_prefix) if vehicle else None
    return {"vin": vin, "vehicle": named and {k: named.get(k) for k in ("model_id", "trim_id", "odometer",
                                                                        "year", "make", "model",
                                                                        "delivered_at", "ownership", "market_id")},
            "visits": visits,
            "declined_work": [{k: d.get(k) for k in ("declined_id", "item", "declined_at", "status",
                                                     *(() if tech else ("amount", "currency")))} for d in declined_rows],
            "estimates": estimate_rows,
            "recalls": [], "recalls_source_ref": "warranty/recall connector (sandbox: no open campaigns)",
            "next_service": nxt, "masked": tech,
            "source_ref": "service history read model (DEALER_ServiceJourneys/RepairOrders/Inspections/"
                          "DeclinedWork/ServiceEstimates)"}


# --- cockpit (§5.20.7) -----------------------------------------------------------------

# Owned-vehicle age cohorts for the retention tile (months in service). The
# headline rate reports the OLDEST cohort that actually has vehicles — that is
# where retention leaks first — and names which cohort it is.
_AGE_BUCKETS = (("0-24", 0, 24), ("25-48", 25, 48), ("49-72", 49, 72), ("73+", 73, None))


def _months_since(ts: str | None, now: _dt.datetime) -> int | None:
    d = _parse(ts)
    if d is None:
        return None
    return max(0, (now.year - d.year) * 12 + (now.month - d.month))


def _retention_tiles(claims: dict[str, Any], market: str, sjs: list[dict[str, Any]],
                     now: _dt.datetime, env_prefix: str | None) -> dict[str, Any]:
    """Service retention of owned vehicles by age cohort (§5.20.7).

    Retention = owned vehicles of that age with at least one service journey at
    this store, over all owned vehicles of that age. The headline figure is the
    oldest cohort that has vehicles (named in the payload) — the out-of-warranty
    end is where retention is actually at risk. Vehicles with no in-service date
    can't be aged, so they are counted separately, never bucketed by guess."""
    owned = [v for v in ddb.scan_all(_VEHICLES, env_prefix)
             if _in_scope(claims, v.get("dealer_id")) and (v.get("market_id") or market) == market]
    serviced = {s.get("vin") for s in sjs if s.get("vin")}
    buckets, unknown_age = [], 0
    for name, lo, hi in _AGE_BUCKETS:
        rows = []
        for v in owned:
            age = _months_since(v.get("delivered_at"), now)
            if age is None:
                continue
            if age >= lo and (hi is None or age <= hi):
                rows.append(v)
        kept = sum(1 for v in rows if v.get("vin") in serviced)
        buckets.append({"bucket": name, "months": f"{lo}-{hi}" if hi else f"{lo}+", "owned": len(rows),
                        "serviced": kept, "rate": round(kept / len(rows), 3) if rows else None})
    unknown_age = sum(1 for v in owned if _months_since(v.get("delivered_at"), now) is None)
    cohort = next((b for b in reversed(buckets) if b["owned"]), None)
    return {"retention_by_vehicle_age": (cohort or {}).get("rate"),
            "retention_by_vehicle_age_cohort": (cohort or {}).get("bucket"),
            "retention_by_vehicle_age_buckets": buckets,
            "retention_by_vehicle_age_unknown_age": unknown_age,
            "retention_by_vehicle_age_source": "DEALER_Vehicles.delivered_at × DEALER_ServiceJourneys.vin"}


def _efficiency_tiles(ests: list[dict[str, Any]], ros: list[dict[str, Any]],
                      env_prefix: str | None) -> dict[str, Any]:
    """Technician efficiency (billed ÷ clocked hours) — reported only if a labor
    timeclock feed exists. The sandbox has flat-rate BILLED hours (estimate
    lines) but no clocked hours (a DMS timeclock), so the ratio is left null and
    the billed side is published on its own rather than inventing a denominator."""
    billed = 0.0
    for e in ests:
        for l in e.get("lines") or []:
            if l.get("decision") == "approve" or l.get("line_status") in ("complete", "qc_passed"):
                try:
                    billed += float(l.get("labor_hours") or 0)
                except (TypeError, ValueError):
                    continue
    return {"technician_efficiency": None,
            "technician_efficiency_note": "needs clocked hours — no DMS labor timeclock feed in this environment",
            "technician_billed_hours": round(billed, 2),
            "technician_billed_hours_source": "DEALER_ServiceEstimates approved/completed lines × flat-rate hours"}


def _handoff_delivery_tiles(claims: dict[str, Any], market: str, env_prefix: str | None) -> dict[str, Any]:
    """Repurchase handoffs that actually delivered: sales journeys sourced from
    the service lane whose delivery completed (or that reached OWN)."""
    js = [j for j in ddb.scan_filter("DEALER_Journeys", "market_id", market, env_prefix)
          if _in_scope(claims, j.get("dealer_id")) and j.get("source") == "service_lane"]
    delivered_ids = {d.get("journey_id") for d in ddb.scan_all("DEALER_Deliveries", env_prefix)
                     if d.get("status") == "completed"}
    delivered = [j for j in js if j["journey_id"] in delivered_ids or j.get("stage") == "OWN"]
    return {"handoff_deliveries": len(delivered),
            "handoff_journeys": len(js),
            "handoff_delivery_rate": round(len(delivered) / len(js), 3) if js else None,
            "handoff_deliveries_source": "DEALER_Journeys.source=service_lane × DEALER_Deliveries.status=completed"}

def _csi_for(claims: dict[str, Any], market: str, env_prefix: str | None) -> dict[str, Any] | None:
    rows = [a.get("csi") for a in ddb.scan_filter("OEM_Accounts", "market_id", market, env_prefix)
            if _in_scope(claims, a.get("dealer_id")) and isinstance(a.get("csi"), dict) and a["csi"].get("score") is not None]
    if not rows:
        return None
    score = round(sum(float(r["score"]) for r in rows) / len(rows))
    prior = round(sum(float(r.get("prior_month") or r["score"]) for r in rows) / len(rows))
    return {"score": score, "prior_month": prior, "delta": score - prior, "responses": sum(int(r.get("responses") or 0) for r in rows),
            "source_ref": "OEM_Accounts.csi (OEM owner-survey roll-up)"}


def cockpit(claims: dict[str, Any], market: str | None = None, labor_rate_delta_pct: float = 0.0,
            close_rate_delta_pts: float = 0.0, rvr_threshold_pct: float | None = None,
            env_prefix: str | None = None) -> dict[str, Any]:
    market = market or claims.get("market_id") or "us"
    sjs = [r for r in ddb.scan_filter(_SJ, "market_id", market, env_prefix) if _in_scope(claims, r.get("dealer_id"))]
    ids = {s["service_journey_id"] for s in sjs}
    now = _now_dt()
    ests = [e for e in ddb.scan_all(_ESTIMATES, env_prefix) if e.get("service_journey_id") in ids]
    insps = [i for i in ddb.scan_all(_INSPECTIONS, env_prefix) if i.get("service_journey_id") in ids]
    ros = [r for r in ddb.scan_all(_RO, env_prefix) if r.get("service_journey_id") in ids]
    ro_ids = {r["ro_id"] for r in ros}
    orders = [o for o in ddb.scan_all(_ORDERS, env_prefix) if o.get("ro_id") in ro_ids]
    declined_rows = [d for d in _declined_entries(env_prefix) if d.get("service_journey_id") in ids]
    handoffs = [h for h in ddb.scan_all(_HANDOFFS, env_prefix) if str(h.get("handoff_id", "")).startswith("HANDOFF-SJ")
                and h["handoff_id"][len("HANDOFF-"):] in ids]

    past_inspect = [s for s in sjs if s.get("stage") not in ("BOOK", "CHECK_IN", "INSPECT")]
    inspected = {i["service_journey_id"] for i in insps}
    mpi_rate = round(len([s for s in past_inspect if s["service_journey_id"] in inspected]) / len(past_inspect), 3) if past_inspect else None
    shared = metrics.summary(sjs, ros, orders, ests, now)          # the definitions every screen uses
    decided = [e for e in ests if e.get("status") in metrics.DECIDED]
    close_rate = shared["close_rate"]
    by_wait: dict[str, list[float]] = {}
    for s in sjs:
        o, p = _parse(s.get("opened_at")), _parse(s.get("projected_at") or s.get("promised_at"))
        if o and p and p > o:
            by_wait.setdefault(s.get("wait_type") or "unknown", []).append((p - o).total_seconds() / 3600)
    cycle = {k: round(sum(v) / len(v), 1) for k, v in by_wait.items()}
    # "Held for parts" has one definition: the journey's parts_hold flag, which the parts
    # desk keeps in step with its orders (order placed → True, last one received → False).
    # The board's ?badge=parts_hold filter reads the same flag, so tile, board and insight agree.
    # Hours are to the LATEST ETA among those journeys' open orders (what the label says).
    hold_sjs = metrics.parts_hold_ids(sjs, ros, orders)
    hold_ros = {r["ro_id"] for r in ros if r.get("service_journey_id") in hold_sjs}
    held = [o for o in orders if o.get("status") in _HELD and o.get("ro_id") in hold_ros]
    hours_lost = round(max([max(0.0, ((_parse(o.get("eta")) or now) - now).total_seconds() / 3600) for o in held],
                           default=0.0), 1)
    turnaround = []
    for e in decided:
        p, d = _parse(e.get("presented_at")), _parse(e.get("decided_at"))
        if p and d and d > p:
            turnaround.append((d - p).total_seconds() / 60)
    revenue = round(sum(float(r.get("total") or 0) for r in ros), 2)
    labor = round(sum(float(r.get("labor_total") or 0) for r in ros), 2)
    proj_labor = round(labor * (1 + labor_rate_delta_pct / 100), 2)
    base_close = close_rate or 0.0
    proj_close = min(1.0, max(0.0, base_close + close_rate_delta_pts / 100))
    pending_total = round(sum(float(e.get("total") or 0) for e in ests if e.get("status") in ("presented", "pending")), 2)
    # repair-vs-replace what-if: how many estimates cross the chosen % of the
    # vehicle's value (the slider threshold; dealer config is the default)
    rvr_threshold = float(rvr_threshold_pct if rvr_threshold_pct is not None else est_mod.RVR_THRESHOLD_PCT)
    sj_by_id = {s["service_journey_id"]: s for s in sjs}
    rvr_triggered, rvr_scored = [], 0
    for e in ests:
        total = e.get("approved_total") if e.get("approved_total") else e.get("total")
        sj_row = sj_by_id.get(e.get("service_journey_id"))
        if sj_row is None or total in (None, 0):
            continue
        card = est_mod.repair_vs_replace(sj_row, float(total), env_prefix, threshold_pct=rvr_threshold)
        if card is None:
            continue
        rvr_scored += 1
        if card["triggered"]:
            rvr_triggered.append({"estimate_id": e.get("estimate_id"),
                                  "service_journey_id": e.get("service_journey_id"),
                                  "pct_of_value": card["pct_of_value"]})
    whatif = {"labor_rate_delta_pct": labor_rate_delta_pct, "close_rate_delta_pts": close_rate_delta_pts,
              "labor_revenue": labor, "projected_labor_revenue": proj_labor,
              "pending_estimate_value": pending_total,
              "projected_recovered_from_pending": round(pending_total * (proj_close - base_close), 2),
              "rvr_threshold_pct": rvr_threshold,
              "rvr_triggered_count": len(rvr_triggered),
              "rvr_scored_estimates": rvr_scored,
              "rvr_triggered": sorted(rvr_triggered, key=lambda x: -(x["pct_of_value"] or 0))[:10],
              "formula": "labor × (1 + Δrate%) ; pending × Δclose ; estimate total ÷ vehicle value ≥ RVR%",
              "estimate": True}

    # lane load this week: the ISO week containing today (Mon–Sun)
    week_start = (now - _dt.timedelta(days=now.weekday())).date()
    week_end = week_start + _dt.timedelta(days=6)
    tiles = {
        # "lane load" is what is in the lane now; "promised today / this week" is the schedule. The two
        # were both called lane load, so "this week (5)" read lower than "today (16)"
        "lane_load": {"now": shared["lane_load"], "promised_today": shared["promised_today"],
                      "promised_this_week": shared["promised_this_week"],
                      "week_start": week_start.isoformat(), "week_end": week_end.isoformat(),
                      "open": shared["open_visits"],
                      "by_stage": {st: sum(1 for s in sjs if s.get("stage") == st) for st in SERVICE_STAGES},
                      "filter": "stage"},
        "cycle_time_hours_by_wait_type": cycle,
        "mpi_completion_rate": mpi_rate,
        "recommended_work_close_rate": close_rate,
        "declined_followup_conversion": (round(sum(1 for d in declined_rows if d.get("status") == "booked")
                                               / len(declined_rows), 3) if declined_rows else None),
        "declined_open": sum(1 for d in declined_rows if (d.get("status") or "open") == "open"),
        "parts_wait_ros": len(hold_sjs), "parts_wait_hours": hours_lost,
        "estimate_turnaround_min": round(sum(turnaround) / len(turnaround), 1) if turnaround else None,
        # CSI: the OEM owner-survey roll-up on the store's account — the same figure the GM cockpit shows
        "csi": _csi_for(claims, market, env_prefix),
        # the same definition the Store Cockpit uses (sales journeys sourced from the service
        # lane in scope), so the two cockpits show one number; the handoff records written by
        # handoff_to_sales in this environment are reported alongside, not instead
        "handoffs_to_sales": _handoff_delivery_tiles(claims, market, env_prefix)["handoff_journeys"],
        "handoff_records": len(handoffs),
        "ro_revenue": revenue,
        "promise_kept_rate": shared["promise_kept_rate"],
        "promise_past": shared["past_promise"], "promise_at_risk": shared["at_risk"],
        "shared_metrics": shared,
        **_retention_tiles(claims, market, sjs, now, env_prefix),
        **_efficiency_tiles(ests, ros, env_prefix),
        **_handoff_delivery_tiles(claims, market, env_prefix),
    }
    sources = ["DEALER_ServiceJourneys", "DEALER_ServiceEstimates", "DEALER_Inspections", "DEALER_PartsOrders",
               "DEALER_DeclinedWork", "DEALER_Handoffs", "DEALER_Vehicles", "DEALER_Deliveries",
               "DEALER_Valuations"]
    insight = []
    if close_rate is not None:
        insight.append(f"Recommended-work close rate is {close_rate:.0%} over {len(decided)} decided estimates.")
    if hold_sjs:
        insight.append(f"{tiles['parts_wait_ros']} ROs are held for parts ({hours_lost} hours to the latest ETA) — "
                       "chase back-orders or offer loaners on those journeys.")
    if tiles["declined_open"]:
        insight.append(f"{tiles['declined_open']} declined items are open; a 30-day service reminder recovers the most of these.")
    if mpi_rate is not None and mpi_rate < 0.9:
        insight.append(f"MPI completion is {mpi_rate:.0%}; every RO past INSPECT should carry an inspection.")
    if rvr_triggered:
        insight.append(f"{len(rvr_triggered)} estimate(s) exceed {rvr_threshold:g}% of the vehicle's value — "
                       "those are repair-vs-replace conversations for the sales desk.")
    cohort_rate = tiles.get("retention_by_vehicle_age")
    if cohort_rate is not None and cohort_rate < 0.5:
        insight.append(f"Only {cohort_rate:.0%} of owned vehicles in the "
                       f"{tiles.get('retention_by_vehicle_age_cohort')}-month cohort came back for service — "
                       "the older the vehicle, the earlier retention leaks.")
    return {"market_id": market, "tiles": tiles, "what_if": whatif,
            "insight": {"text": " ".join(insight) or "No exceptions in the current data.", "sources": sources},
            "note": "Figures are computed from today's repair orders and visits at the time you open the page.",
            "source_ref": "A7 service cockpit read model"}


# --- message customer (A7 drafts, advisor approves) -----------------------------------------

def draft_message(sj_id: str, purpose: str, env_prefix: str | None = None) -> dict[str, Any]:
    sj = _sj(sj_id, env_prefix)
    c = ddb.get_item(_CUSTOMERS, {"customer_id": sj.get("customer_id")}, env_prefix) or {}
    first = c.get("given_name") or "there"
    templates_ = {
        "status": f"Hi {first}, a quick update on your vehicle: it's currently at the {sj.get('stage', '').replace('_', ' ').lower()} step. "
                  "We'll let you know as soon as the next step is done.",
        "estimate": f"Hi {first}, your inspection results and estimate are ready to review in My Garage. "
                    "You can approve or decline each item, and we're happy to answer questions.",
        "delay": f"Hi {first}, we're running behind on your vehicle and want to give you an updated time. "
                 "We're sorry for the wait — reply here if you'd like a loaner or a call.",
        "ready": f"Hi {first}, your vehicle is ready for pickup. Your invoice and inspection report are in My Garage.",
    }
    body = templates_.get(purpose) or templates_["status"]
    channel = {"text": "sms", "call": "sms", "email": "email"}.get(sj.get("approval_preference") or "", "email")
    return {"service_journey_id": sj_id, "purpose": purpose, "channel": channel, "subject": "Your service visit",
            "body": body, "requires_approval": True, "source_ref": "A7.draft_message (service templates)"}


def send_message(sj_id: str, *, actor: str, channel: str, subject: str, body: str,
                 env_prefix: str | None = None) -> dict[str, Any]:
    sj = _sj(sj_id, env_prefix)
    if not body.strip():
        raise Invalid("message body is empty")
    c = ddb.get_item(_CUSTOMERS, {"customer_id": sj.get("customer_id")}, env_prefix) or {}
    to = c.get("email") if channel == "email" else c.get("phone")
    from ..platform.outreach import get_outreach_provider
    res = get_outreach_provider().send(channel, to, subject, body, customer_id=sj.get("customer_id"),
                                       dealer_id=sj.get("dealer_id"), env_prefix=env_prefix)
    ev = svc_supervisor._event_row(sj_id, "message.outbound", actor, f"{channel}: {subject}",
                                   {"message_id": res.get("message_id"), "sent": res.get("sent")}, _now())
    ddb.put_item(_EVENTS, ev, env_prefix)
    _bus.emit("message.outbound", {"service_journey_id": sj_id, "customer_id": sj.get("customer_id"),
                                   "dealer_id": sj.get("dealer_id"), "channel": channel,
                                   "message_id": res.get("message_id")}, actor=actor, env_prefix=env_prefix)
    return res
