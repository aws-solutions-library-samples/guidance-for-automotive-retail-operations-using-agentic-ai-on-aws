"""My Garage › Service (spec §5.12 Service row, §5.20.4 customer-facing mode, acceptance #27).

The owner's side of the A7 service journey:

  overview(cid)                       due service per vehicle, active visits, declined work
  slots(cid, vin, days)               bookable slots (sandbox scheduling connector)
  book(cid, vin, …)                   A7 book_service for an owned VIN
  detail(cid, sj_id)                  tracker + estimate lines + inspection report + timeline
  decide_lines(cid, estimate_id, …)   line-by-line approve / decline
  history(cid)                        service history per VIN

Estimate lines. The A7 estimate row stores totals; lines are the priced
actionable inspection findings (labor guide flat-rate × labor rate + parts
catalog — the same derivation `service.inspection.build_estimate` uses), unless
the estimate row already carries `lines`. Every line keeps its `price_source`
and `finding_ref`, so what the customer approves is exactly what the advisor
presented (same component, two themes — §5.20).

Decisions. Each line decision is written to the estimate's `line_decisions`
(decided_by=customer, the presented evidence snapshot, the session purpose) and
emits `estimate.line_decided`. When every line is decided the A7 supervisor's
`record_approval` closes the APPROVE gate (approve if any line was approved),
approved line ids are recorded for dispatch, and declined lines go to
`schedule_declined_followup` (DEALER_DeclinedWork + a service-family nurture).
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any

from ..common import ddb
from ..events import bus as _bus
from . import portal

SERVICE_STAGES = ["BOOK", "CHECK_IN", "INSPECT", "DIAGNOSE", "APPROVE", "REPAIR", "READY", "FOLLOW_UP"]
# customer-safe service timeline events
_TIMELINE_TYPES = {
    "service.booked": "Service booked", "ro.opened": "Checked in — repair order opened",
    "inspection.submitted": "Inspection completed", "estimate.presented": "Estimate sent to you",
    "estimate.built": "Estimate prepared", "estimate.approved": "Estimate approved",
    "estimate.declined": "Estimate declined", "estimate.line_decided": "Estimate decision recorded",
    "service.ready": "Vehicle ready for pickup", "vehicle.ready": "Vehicle ready for pickup",
    "ro.line_completed": "Work completed on a line", "parts.ordered": "Parts ordered",
    "service.stage_changed": "Stage updated", "service.stage.advanced": "Stage updated",
}
_WAIT_TYPES = ("wait", "drop", "loaner", "mobile", "pickup_delivery")
_WARRANTY_MONTHS = 36
_HV_WARRANTY_MONTHS = 96
_DECIDABLE = ("presented", "pending")


class NotFound(Exception):
    pass


class Forbidden(Exception):
    pass


class Invalid(ValueError):
    pass


class NotDecidable(Exception):
    """The estimate is not (or no longer) waiting for the customer's decision."""


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def _iso(d: _dt.datetime) -> str:
    return d.isoformat().replace("+00:00", "Z")


def _own_sj(cid: str, sj_id: str, env_prefix: str | None) -> dict[str, Any]:
    sj = ddb.get_item("DEALER_ServiceJourneys", {"service_journey_id": sj_id}, env_prefix)
    if sj is None or sj.get("customer_id") != cid:
        raise NotFound(f"service visit {sj_id} not found")
    return sj


def _own_vehicle(cid: str, vin: str, env_prefix: str | None) -> dict[str, Any]:
    v = ddb.get_item("DEALER_Vehicles", {"vin": vin}, env_prefix)
    if v is None or v.get("customer_id") != cid:
        raise NotFound(f"vehicle {vin} not found")
    return v


def _estimate_for(sj_id: str, env_prefix: str | None) -> dict[str, Any] | None:
    ests = ddb.scan_filter("DEALER_ServiceEstimates", "service_journey_id", sj_id, env_prefix)
    if not ests:
        return None
    return sorted(ests, key=lambda e: e.get("presented_at") or e.get("created_at") or "", reverse=True)[0]


def _months_since(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        d = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (_now() - d).days / 30.44


def _coverage_for(item: str, vehicle: dict[str, Any] | None) -> dict[str, Any]:
    """Coverage badge (warranty / customer pay). Sandbox rule from the vehicle's
    in-service date: HV battery items inside the HV warranty, powertrain-type
    items inside the base warranty; maintenance and wear items are customer pay."""
    age = _months_since((vehicle or {}).get("delivered_at"))
    key = (item or "").lower()
    if age is not None:
        if ("hv" in key or "high-voltage" in key or "battery state" in key) and age <= _HV_WARRANTY_MONTHS:
            return {"pay_type": "warranty", "label": "Covered by battery warranty",
                    "source_ref": f"warranty rule: HV battery ≤ {_HV_WARRANTY_MONTHS} months in service"}
        if any(k in key for k in ("coolant pump", "drive unit", "transmission", "engine")) and age <= _WARRANTY_MONTHS:
            return {"pay_type": "warranty", "label": "Covered by warranty",
                    "source_ref": f"warranty rule: powertrain ≤ {_WARRANTY_MONTHS} months in service"}
    return {"pay_type": "customer_pay", "label": "Customer pay", "source_ref": "DEALER_Coverage (no match)"}


def estimate_lines(sj: dict[str, Any], est: dict[str, Any] | None,
                   env_prefix: str | None = None) -> list[dict[str, Any]]:
    """Customer-safe priced lines for the estimate (see module docstring)."""
    from ..service import inspection as insp_mod
    vehicle = ddb.get_item("DEALER_Vehicles", {"vin": sj.get("vin")}, env_prefix) if sj.get("vin") else None
    currency = (est or {}).get("currency") or ("EUR" if sj.get("market_id") == "de" else "USD")
    stored = (est or {}).get("lines")
    if est and not stored:
        # Same lines (and line ids) the Service Desk prices and decides on.
        from ..service import estimates as svc_est
        stored = svc_est.lines_for(est, env_prefix)[0]
    findings = sorted(ddb.scan_filter("DEALER_Inspections", "service_journey_id", sj["service_journey_id"], env_prefix),
                      key=lambda f: f.get("inspection_id") or "")
    by_item = {f.get("item"): f for f in findings}
    by_ref = {f.get("inspection_id"): f for f in findings}
    out: list[dict[str, Any]] = []
    if isinstance(stored, list) and stored:
        src = [(ln, by_ref.get(ln.get("finding_ref")) or by_item.get(ln.get("item"))) for ln in stored]
    else:
        actionable = [f for f in findings if f.get("status") in ("red", "yellow")]
        rate = insp_mod._LABOR_RATE.get(currency, 175.0)
        src = []
        for f in actionable:
            op = insp_mod._labor_op_for(f.get("item"))
            part_name, part_price, qty = op["part"]
            labor = round(op["hours"] * rate, 2)
            parts = round(part_price * qty, 2)
            src.append(({"item": f.get("item"), "finding_status": f.get("status"), "labor_op": op["op"],
                         "labor_hours": op["hours"], "labor_amount": labor,
                         "part": {"description": part_name, "qty": qty, "amount": parts},
                         "line_total": round(labor + parts, 2),
                         "price_source": "labor guide (flat-rate) + parts catalog"}, f))
    decisions = (est or {}).get("line_decisions") or {}
    for i, (ln, f) in enumerate(src):
        line_id = ln.get("line_id") or f"L{i + 1:02d}"
        status = ln.get("finding_status") or (f or {}).get("status")
        cov = ln.get("coverage") if isinstance(ln.get("coverage"), dict) and ln["coverage"].get("pay_type") \
            else _coverage_for(ln.get("item"), vehicle)
        total = ln.get("line_total") or 0
        d = decisions.get(line_id) or {}
        out.append({
            "line_id": line_id, "item": ln.get("item"), "status": status,
            "urgency": (f or {}).get("urgency") or ("safety" if status == "red" else "soon"),
            "measurement": (f or {}).get("measurement"), "unit": (f or {}).get("unit"),
            "finding_ref": (f or {}).get("inspection_id") or ln.get("finding_ref"),
            "evidence_doc_id": (f or {}).get("evidence_doc_id"),
            "evidence_url": (evidence_card(ln.get("item"), status, (f or {}).get("measurement"), (f or {}).get("unit"),
                                           sj.get("vin")) if (f or {}).get("evidence_doc_id") else None),
            "work": ln.get("labor_op"),
            "labor_amount": ln.get("labor_amount"),
            "parts_amount": ln.get("parts_amount", (ln.get("part") or {}).get("amount")),
            "line_total": total, "currency": currency,
            "customer_pays": ln["customer_amount"] if ln.get("customer_amount") is not None
            else (0 if cov["pay_type"] == "warranty" else total),
            "coverage": cov, "price_source": ln.get("price_source"),
            "decision": d.get("decision"), "decided_at": d.get("decided_at"), "decided_by": d.get("decided_by"),
        })
    return out


_CARD_COLOR = {"red": "#EF4444", "yellow": "#F59E0B", "green": "#10B981"}


def evidence_card(item: Any, status: Any, measurement: Any, unit: Any, vin: Any) -> str:
    """The technician's evidence for one finding as an image the customer can open: the
    sandbox stand-in for the photo (the evidence store keeps only the capture id), drawn
    the way the placeholder evidence backend draws it (evidence_images.py): the burned-in
    "<ITEM> · <MEASUREMENT> · VIN …<last6>" label over an urgency colour bar. Labelled as a
    sample so it never reads as a real photo. A data: URI, no storage and no dependencies."""
    from html import escape
    from urllib.parse import quote
    meas = " ".join(str(x) for x in (measurement, unit) if x not in (None, "")) or "see note"
    label = escape(f"{str(item or 'Finding').upper()}  ·  {meas}  ·  VIN …{str(vin or '')[-6:]}")
    color = _CARD_COLOR.get(str(status or "").lower(), "#64748B")
    word = escape({"red": "Needs attention now", "yellow": "Needs attention soon"}.get(str(status or "").lower(), "Checked"))
    svg = ("<svg xmlns='http://www.w3.org/2000/svg' width='640' height='360' viewBox='0 0 640 360'>"
           "<rect width='640' height='360' fill='#1F2937'/>"
           "<rect x='24' y='24' width='592' height='248' rx='12' fill='#374151'/>"
           "<text x='320' y='140' text-anchor='middle' font-family='system-ui,sans-serif' font-size='22' fill='#E5E7EB'>"
           "Technician inspection evidence</text>"
           f"<text x='320' y='176' text-anchor='middle' font-family='system-ui,sans-serif' font-size='16' fill='#9CA3AF'>{word}</text>"
           "<text x='320' y='206' text-anchor='middle' font-family='system-ui,sans-serif' font-size='13' fill='#9CA3AF'>"
           "Sample image · the photo on file is held by the dealer's evidence store</text>"
           f"<rect x='0' y='296' width='640' height='64' fill='{color}'/>"
           f"<text x='24' y='336' font-family='ui-monospace,monospace' font-size='18' fill='#FFFFFF'>{label}</text></svg>")
    return "data:image/svg+xml;charset=utf-8," + quote(svg)


def _bundles(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Good-better-best: safety-only, recommended (red + yellow), everything."""
    def pack(tier: str, label: str, pick: list[dict[str, Any]]) -> dict[str, Any]:
        return {"tier": tier, "label": label, "line_ids": [ln["line_id"] for ln in pick],
                "total": round(sum(ln["customer_pays"] for ln in pick), 2)}
    red = [ln for ln in lines if ln["status"] == "red"]
    rec = [ln for ln in lines if ln["status"] in ("red", "yellow")]
    out = []
    if red:
        out.append(pack("good", "Safety items only", red))
    if rec:
        out.append(pack("better", "Recommended", rec))
    out.append(pack("best", "Everything we found", lines))
    seen, uniq = set(), []
    for b in out:
        k = tuple(b["line_ids"])
        if k and k not in seen:
            seen.add(k)
            uniq.append(b)
    return uniq


def _explanations(lines: list[dict[str, Any]], sj_id: str, env_prefix: str | None) -> None:
    from ..service import connectors
    for ln in lines:
        if not ln.get("finding_ref"):
            continue
        try:
            ex = connectors.explain_finding(ln["finding_ref"], sj_id, env_prefix)
        except Exception:
            ex = None
        if ex and ex.get("explained", True) and ex.get("guardrail_safe", True) and (ex.get("explanation") or ex.get("text")):
            ln["explanation"] = ex.get("explanation") or ex.get("text")
            ln["explanation_source"] = ex.get("source_ref")


# --- overview ---------------------------------------------------------------------

def _declined(cid: str, env_prefix: str | None) -> list[dict[str, Any]]:
    rows = ddb.scan_filter("DEALER_DeclinedWork", "customer_id", cid, env_prefix)
    out = []
    for r in rows:
        items = r.get("items") or [{"item": r.get("item"), "amount": r.get("amount"), "currency": r.get("currency")}]
        for it in items:
            if not it.get("item"):
                continue
            out.append({"item": it.get("item"), "amount": it.get("amount"),
                        "currency": it.get("currency") or r.get("currency"),
                        "declined_at": r.get("declined_at"), "service_journey_id": r.get("service_journey_id"),
                        "booked": bool(r.get("booked_at"))})
    return sorted(out, key=lambda d: d.get("declined_at") or "", reverse=True)


def _sj_summary(sj: dict[str, Any], env_prefix: str | None) -> dict[str, Any]:
    est = _estimate_for(sj["service_journey_id"], env_prefix)
    stage = sj.get("stage")
    from ..service import desk
    return {"service_journey_id": sj["service_journey_id"], "vin": sj.get("vin"), "stage": stage,
            "promised_at": sj.get("promised_at"), "projected_at": sj.get("projected_at"),
            # promise times read in the store's own hours, not the browser's (6:00 AM)
            "timezone": desk._dealer_timezone(sj.get("dealer_id"), env_prefix) if sj.get("dealer_id") else None,
            "parts_hold": bool(sj.get("parts_hold")), "wait_type": sj.get("wait_type"),
            "dealer_name": portal._dealer_name(sj.get("dealer_id"), env_prefix),
            "awaiting_approval": stage == "APPROVE" and bool(est) and est.get("status") in _DECIDABLE}


def overview(cid: str, env_prefix: str | None = None) -> dict[str, Any]:
    from ..service import booking
    portal.customer(cid, env_prefix)
    vehicles = []
    for v in ddb.scan_filter("DEALER_Vehicles", "customer_id", cid, env_prefix):
        try:
            due = booking.predict_next_service(v["vin"], env_prefix)
        except Exception:
            due = {}
        vehicles.append({"vin": v["vin"], "year": v.get("year"), "make": v.get("make"), "model": v.get("model"),
                         "odometer": v.get("odometer"), "due_by": due.get("due_by"), "overdue": due.get("overdue"),
                         "next_service_mileage": due.get("next_service_mileage"),
                         "recommended_services": due.get("recommended_services") or [],
                         "bookable_services": due.get("bookable_services") or [],
                         "recalls": [], "source_ref": due.get("source_ref")})
    sjs = ddb.scan_filter("DEALER_ServiceJourneys", "customer_id", cid, env_prefix)
    active = [s for s in sjs if s.get("stage") not in ("COMPLETED",)]
    return {"customer_id": cid, "vehicles": vehicles,
            "active": [_sj_summary(s, env_prefix) for s in sorted(active, key=lambda s: s.get("opened_at") or "", reverse=True)],
            "declined_work": _declined(cid, env_prefix),
            "wait_types": list(_WAIT_TYPES)}


def slots(cid: str, vin: str, days: int = 5, env_prefix: str | None = None) -> dict[str, Any]:
    """Bookable slots for the next `days` business days at the vehicle's dealer
    (sandbox scheduling connector: hourly 8:00–16:00 local, 2 bays per hour,
    minus service appointments already booked)."""
    v = _own_vehicle(cid, vin, env_prefix)
    dealer = v.get("dealer_id")
    # the store's own hours (its timezone), stored as UTC — the same rule a staff booking uses
    from ..service import booking
    s = booking.store_slots(dealer, days, env_prefix, now=_now())
    return {"vin": vin, "dealer_id": dealer, "dealer_name": portal._dealer_name(dealer, env_prefix),
            "timezone": s["timezone"], "slots": s["slots"],
            "source_ref": "scheduling connector (sandbox: 2 bays/hour, store hours)"}


def book(cid: str, vin: str, *, services: list[str] | None, wait_type: str, slot_start: str | None,
         env_prefix: str | None = None) -> dict[str, Any]:
    from ..service import booking
    _own_vehicle(cid, vin, env_prefix)
    if wait_type not in _WAIT_TYPES:
        raise Invalid(f"wait_type must be one of {_WAIT_TYPES}")
    if services is not None and (not isinstance(services, list) or not all(isinstance(s, str) and s.strip() for s in services)):
        raise Invalid("services must be a list of names")
    out = booking.book_service(vin, services=services, wait_type=wait_type, preferred_slot=slot_start,
                               actor=f"customer:{cid}", env_prefix=env_prefix)
    # a car with an open heads-up: the customer's own booking answers the lead and earns its offer
    from ..service import predictive
    linked = predictive.link_booking(vin, out, "customer", env_prefix)
    if linked:
        out = {**out, "offer_applied_pct": linked.get("offer_applied_pct"), "lead_id": linked.get("lead_id")}
    # mark matching declined work as booked so reminders stop (§2.1.1, acc #29)
    if services:
        for r in ddb.scan_filter("DEALER_DeclinedWork", "customer_id", cid, env_prefix):
            names = {i.get("item") for i in (r.get("items") or [])} | {r.get("item")}
            if names & set(services) and not r.get("booked_at"):
                r = dict(r)
                r["booked_at"] = _iso(_now())
                r["booked_service_journey_id"] = out.get("service_journey_id")
                ddb.put_item("DEALER_DeclinedWork", r, env_prefix)
    return out


# --- detail + decisions ---------------------------------------------------------------

def detail(cid: str, sj_id: str, env_prefix: str | None = None) -> dict[str, Any]:
    sj = _own_sj(cid, sj_id, env_prefix)
    est = _estimate_for(sj_id, env_prefix)
    lines = estimate_lines(sj, est, env_prefix) if est else []
    _explanations(lines, sj_id, env_prefix)
    findings = ddb.scan_filter("DEALER_Inspections", "service_journey_id", sj_id, env_prefix)
    events = ddb.query_hash("DEALER_ServiceJourneyEvents", "service_journey_id", sj_id, env_prefix)
    timeline = [{"ts": e.get("event_ts"), "label": _TIMELINE_TYPES[e.get("event_type")]}
                for e in sorted(events, key=lambda e: e.get("event_ts") or "", reverse=True)
                if e.get("event_type") in _TIMELINE_TYPES]
    ro = next(iter(ddb.scan_filter("DEALER_RepairOrders", "service_journey_id", sj_id, env_prefix)), None)
    decidable = bool(est) and sj.get("stage") == "APPROVE" and est.get("status") in _DECIDABLE
    total = round(sum(ln["line_total"] for ln in lines), 2)
    pays = round(sum(ln["customer_pays"] for ln in lines), 2)
    return {
        **_sj_summary(sj, env_prefix),
        "market_id": sj.get("market_id"),
        "steps": [{"stage": s, "state": "done" if SERVICE_STAGES.index(s) < SERVICE_STAGES.index(sj["stage"]) else
                   "current" if s == sj["stage"] else "todo"} for s in SERVICE_STAGES] if sj.get("stage") in SERVICE_STAGES
        else [{"stage": s, "state": "done"} for s in SERVICE_STAGES],
        "estimate": ({"estimate_id": est["estimate_id"], "status": est.get("status"),
                      "presented_at": est.get("presented_at"), "currency": est.get("currency") or (lines[0]["currency"] if lines else None),
                      "lines": lines, "total": total, "you_pay": pays, "covered": round(total - pays, 2),
                      "bundles": _bundles(lines) if lines else [], "decidable": decidable,
                      "source_ref": est.get("source_ref")} if est else None),
        "inspection": [{"item": f.get("item"), "status": f.get("status"), "measurement": f.get("measurement"),
                        "unit": f.get("unit"), "evidence_doc_id": f.get("evidence_doc_id"),
                        "evidence_url": (evidence_card(f.get("item"), f.get("status"), f.get("measurement"), f.get("unit"),
                                                       sj.get("vin")) if f.get("evidence_doc_id") else None)}
                       for f in sorted(findings, key=lambda f: {"red": 0, "yellow": 1}.get(f.get("status"), 2))],
        "documents": ([{"kind": "invoice", "label": "Invoice", "ro_number": ro.get("ro_number"), "available": ro.get("status") == "closed"}]
                      if ro else []) + ([{"kind": "inspection_report", "label": "Inspection report", "available": bool(findings)}]),
        "ro": {"ro_number": ro.get("ro_number"), "status": ro.get("status"), "total": ro.get("total"),
               "currency": ro.get("currency")} if ro else None,
        "timeline": timeline,
    }


def decide_lines(cid: str, estimate_id: str, decisions: list[dict[str, Any]], *,
                 via: str = "my_garage", env_prefix: str | None = None) -> dict[str, Any]:
    """Customer line-by-line decisions. Validates ownership and stage here, then
    delegates to the Service Desk's single implementation
    (service.estimates.decide_lines): approved lines are dispatched, declined
    lines go to DEALER_DeclinedWork with their evidence snapshot, and the
    estimate becomes approved / partially_approved / declined (acc #27)."""
    from ..service import estimates as svc_est
    est = ddb.get_item("DEALER_ServiceEstimates", {"estimate_id": estimate_id}, env_prefix)
    if est is None:
        raise NotFound(f"estimate {estimate_id} not found")
    sj = _own_sj(cid, est.get("service_journey_id"), env_prefix)
    if sj.get("stage") != "APPROVE" or est.get("status") not in _DECIDABLE:
        raise NotDecidable("this estimate is not waiting for your decision")
    if not isinstance(decisions, list) or not decisions:
        raise Invalid("decisions must be a non-empty list of {line_id, decision}")
    est = svc_est.persist_lines(estimate_id, env_prefix)      # freeze line ids before deciding
    lines = {ln["line_id"]: ln for ln in estimate_lines(sj, est, env_prefix)}
    for d in decisions:
        if d.get("line_id") not in lines:
            raise Invalid(f"unknown line {d.get('line_id')!r}")
        if d.get("decision") not in ("approve", "decline"):
            raise Invalid("decision must be approve or decline")
    try:
        out = svc_est.decide_lines(
            estimate_id,
            [{"line_id": d["line_id"], "decision": d["decision"], "reason": d.get("reason")} for d in decisions],
            actor=f"customer:{cid}:{via}", decided_by="customer", env_prefix=env_prefix)
    except svc_est.InvalidDecision as e:
        raise Invalid(str(e)) from e
    est = dict(ddb.get_item("DEALER_ServiceEstimates", {"estimate_id": estimate_id}, env_prefix) or est)
    # §8 audit: each customer decision keeps the evidence snapshot they saw.
    recorded = dict(est.get("line_decisions") or {})
    for d in decisions:
        lid, ln = d["line_id"], lines[d["line_id"]]
        if lid in recorded and "presented" not in recorded[lid]:
            recorded[lid] = {**recorded[lid], "decided_via": via, "presented": {
                "item": ln["item"], "status": ln["status"], "line_total": ln["line_total"],
                "customer_pays": ln["customer_pays"], "coverage": (ln.get("coverage") or {}).get("pay_type"),
                "finding_ref": ln["finding_ref"], "price_source": ln["price_source"]}}
    est["line_decisions"] = recorded
    ddb.put_item("DEALER_ServiceEstimates", est, env_prefix)
    complete = all(lid in recorded for lid in lines)
    result: dict[str, Any] = {"estimate_id": estimate_id, "service_journey_id": sj["service_journey_id"],
                              "decided": len(recorded), "lines": len(lines), "complete": complete,
                              "status": est.get("status"),
                              "approved_line_ids": est.get("approved_line_ids") or [],
                              "declined_line_ids": est.get("declined_line_ids") or [],
                              "approved_total": est.get("approved_total"),
                              "dispatched": out.get("dispatched") or []}
    if out.get("declined_work"):
        result["declined_followup"] = {"declined_id": out["declined_work"][0],
                                       "declined_item_ids": out["declined_work"]}
    return result


def history(cid: str, env_prefix: str | None = None) -> dict[str, Any]:
    portal.customer(cid, env_prefix)
    ros = ddb.scan_filter("DEALER_RepairOrders", "customer_id", cid, env_prefix)
    sjs = {s["service_journey_id"]: s for s in ddb.scan_filter("DEALER_ServiceJourneys", "customer_id", cid, env_prefix)}
    by_vin: dict[str, list[dict[str, Any]]] = {}
    for r in ros:
        sj = sjs.get(r.get("service_journey_id") or "") or {}
        vin = sj.get("vin") or "unknown"
        n_insp = len(ddb.scan_filter("DEALER_Inspections", "service_journey_id", sj.get("service_journey_id"), env_prefix)) \
            if sj.get("service_journey_id") else 0
        by_vin.setdefault(vin, []).append({"ro_number": r.get("ro_number"), "status": r.get("status"),
                                           "opened_at": r.get("opened_at"), "total": r.get("total"),
                                           "currency": r.get("currency"), "inspection_items": n_insp,
                                           "service_journey_id": r.get("service_journey_id")})
    for v in by_vin.values():
        v.sort(key=lambda r: r.get("opened_at") or "", reverse=True)
    return {"customer_id": cid, "vehicles": [{"vin": k, "visits": v} for k, v in sorted(by_vin.items())],
            "declined_work": _declined(cid, env_prefix)}
