"""Service Desk routes (spec §6 `/service/*`, Process 2 / A7, §5.20).

Spec-shaped routes alongside the legacy `/service/{id}/...` shapes in
handler.py (both stay). Authorization, enforced here (acceptance #30):
  * service roles read; OEM roles read across dealers (GET only)
  * technician: masked payloads (no customer contact, pricing, coverage split);
    may not open estimates or message customers
  * dealer_parts: may create/update parts orders, may not change line status
  * estimates: dealer_service / dealer_service_manager / dealer_manager
    (and the owning customer for decisions)
"""
from __future__ import annotations

import re
from typing import Any

from ...common import authz, ddb
from ...dealer import store_digest
from ...service import booking, connectors, desk, estimates, inspection, morning_work, predictive, repair
from ...service import supervisor as svc_supervisor
from .. import routing as r

SERVICE = frozenset(authz.SERVICE_READ_ROLES)
ADVISORS = frozenset({"dealer_service", "dealer_service_manager", "dealer_manager"})
LINE_STATUS = frozenset({"dealer_service", "dealer_technician", "dealer_service_manager", "dealer_manager"})
PARTS = frozenset({"dealer_parts", "dealer_service", "dealer_service_manager", "dealer_manager"})
INSPECTORS = frozenset({"dealer_technician", "dealer_service", "dealer_service_manager", "dealer_manager"})


def _read(claims):
    r.roles(claims, SERVICE, allow_oem=True)


def _write(claims, allowed=SERVICE):
    r.roles(claims, allowed)


def _sj_scoped(claims, sj_id):
    sj = r.service_journey_in_scope(claims, sj_id)
    if claims.get("role") == "dealer_technician" and sj.get("technician_id") not in (None, claims.get("user_id")):
        # technicians work their own ROs; unassigned ROs are visible for dispatch pickup
        raise r.Bad(403, "RO is assigned to another technician")
    return sj


def _ro(claims, ro_id):
    ro = ddb.get_item("DEALER_RepairOrders", {"ro_id": ro_id})
    if ro is None:
        raise r.Bad(404, f"repair order {ro_id} not found")
    return ro, _sj_scoped(claims, ro["service_journey_id"])


def _est(claims, est_id):
    e = ddb.get_item("DEALER_ServiceEstimates", {"estimate_id": est_id})
    if e is None:
        raise r.Bad(404, f"estimate {est_id} not found")
    sj = r.service_journey_in_scope(claims, e["service_journey_id"])
    if claims.get("role") != "customer":
        r.roles(claims, ADVISORS, allow_oem=True)
    return e, sj


def _market(claims, q):
    return q.get("market") or claims.get("market_id") or "us"


# --- journeys / board ------------------------------------------------------------

def _journeys(event, claims, _m):
    _read(claims)
    return 200, desk.list_journeys(claims, r.qs(event))


def _journey(event, claims, m):
    _read(claims)
    _sj_scoped(claims, m.group(1))
    return 200, desk.journey_detail(m.group(1), claims.get("role"))


def _brief(event, claims, m):
    """A7 prepare_ro_brief (§3.7): what's due, open recalls, coverage, pre-drafted
    lines. Read-only; a technician gets the brief without the pricing fields."""
    _read(claims)
    sj = _sj_scoped(claims, m.group(1))
    out = morning_work.prepare_ro_brief(sj)
    if claims.get("role") == "dealer_technician":
        out = {k: v for k, v in out.items() if k not in ("equity_snapshot",)}
        out["due_items"] = [{k: v for k, v in d.items() if k not in ("amount", "currency")}
                            for d in out.get("due_items") or []]
    return 200, out


def _arrivals(event, claims, _m):
    _read(claims)
    return 200, {"items": desk.arrivals(claims)}


def _attention(event, claims, _m):
    _read(claims)
    if claims.get("role") == "dealer_technician":
        raise r.Bad(403, "the attention list is an advisor view")
    return 200, desk.attention(claims)


def _claim(event, claims, m):
    r.roles(claims, {"dealer_technician"})
    _sj_scoped(claims, m.group(1))
    try:
        return 200, desk.claim(m.group(1), claims["user_id"])
    except desk.Forbidden as e:
        raise r.Bad(409, str(e)) from e


def _checkin(event, claims, m):
    _write(claims, ADVISORS)
    _sj_scoped(claims, m.group(1))
    b = r.body(event)
    return 200, desk.checkin(m.group(1), actor=claims["user_id"], mileage=b.get("mileage"),
                             concerns=b.get("concerns"), photos=b.get("photos"),
                             approval_preference=b.get("approval_preference"),
                             promised_at=b.get("promised_at"), add_declined=b.get("add_declined"))


def _message_draft(event, claims, m):
    _write(claims, ADVISORS)
    _sj_scoped(claims, m.group(1))
    return 200, desk.draft_message(m.group(1), r.qs(event).get("purpose") or "status")


def _message_send(event, claims, m):
    _write(claims, ADVISORS)
    _sj_scoped(claims, m.group(1))
    b = r.body(event)
    r.require(b, "channel", "body")
    if b["channel"] not in ("email", "sms"):
        raise r.Bad(400, "channel must be email or sms")
    return 200, desk.send_message(m.group(1), actor=claims["user_id"], channel=b["channel"],
                                  subject=b.get("subject") or "Your service visit", body=b["body"])


# --- bookings ----------------------------------------------------------------------

def _availability(event, claims, _m):
    _read(claims)
    q = r.qs(event)
    dealer = q.get("dealer") or (sorted(claims.get("dealer_ids") or [None])[0])
    if not dealer:
        raise r.Bad(400, "query must include dealer")
    r.dealer_in_scope(claims, dealer)
    return 200, desk.availability(dealer, q.get("date"))


def _express_menu(event, claims, _m):
    _read(claims)
    q = r.qs(event)
    dealer = q.get("dealer") or (sorted(claims.get("dealer_ids") or [None])[0])
    if dealer:
        r.dealer_in_scope(claims, dealer)
    return 200, desk.express_menu(dealer, _market(claims, q))


def _book(event, claims, _m):
    _write(claims, ADVISORS)
    b = r.body(event)
    r.require(b, "vin")
    v = ddb.get_item("DEALER_Vehicles", {"vin": b["vin"]})
    if v is None:
        raise r.Bad(404, f"vehicle {b['vin']} not found")
    r.dealer_in_scope(claims, v.get("dealer_id"))
    out = booking.book_service(b["vin"], services=b.get("services"), wait_type=b.get("wait_type") or "drop",
                               preferred_slot=b.get("slot_start"), actor=claims["user_id"],
                               service_class=b.get("service_class"))
    return 201, out


def _booking_get(event, claims, m):
    _read(claims)
    a = ddb.get_item("DEALER_Appointments", {"appointment_id": m.group(1)})
    if a is None:
        raise r.Bad(404, "booking not found")
    r.dealer_in_scope(claims, a.get("dealer_id"))
    return 200, a


def _booking_patch(event, claims, m):
    _write(claims, ADVISORS)
    a = ddb.get_item("DEALER_Appointments", {"appointment_id": m.group(1)})
    if a is None:
        raise r.Bad(404, "booking not found")
    r.dealer_in_scope(claims, a.get("dealer_id"))
    b = r.body(event)
    return 200, desk.reschedule(m.group(1), actor=claims["user_id"], slot_start=b.get("slot_start"), cancel=False)


def _booking_delete(event, claims, m):
    _write(claims, ADVISORS)
    a = ddb.get_item("DEALER_Appointments", {"appointment_id": m.group(1)})
    if a is None:
        raise r.Bad(404, "booking not found")
    r.dealer_in_scope(claims, a.get("dealer_id"))
    return 200, desk.reschedule(m.group(1), actor=claims["user_id"], slot_start=None, cancel=True)


def _predict(event, claims, _m):
    _read(claims)
    vin = r.qs(event).get("vin")
    if not vin:
        raise r.Bad(400, "query must include vin")
    v = ddb.get_item("DEALER_Vehicles", {"vin": vin})
    if v is None:
        raise r.Bad(404, f"vehicle {vin} not found")
    r.dealer_in_scope(claims, v.get("dealer_id"))
    due = booking.predict_next_service(vin)
    cov = connectors.check_coverage(vin, "maint", market=v.get("market_id") or "us")
    open_decl = [d for d in desk.declined(claims)["items"] if d.get("vin") == vin and d["status"] == "open"]
    from ...common import vehicles
    shown = vehicles.display_fields(v)
    name = " ".join(str(x) for x in (shown.get("year"), shown.get("make"), shown.get("model")) if x) or None
    return 200, {"vin": vin, "vehicle": {**{k: v.get(k) for k in ("model_id", "odometer", "customer_id", "dealer_id")},
                                         "name": name},
                 "next_service": due, "coverage": cov, "open_recalls": [], "declined_open": open_decl,
                 "recalls_source_ref": "warranty/recall connector (sandbox: no open campaigns)"}


# --- repair orders --------------------------------------------------------------------

def _ro_get(event, claims, m):
    _read(claims)
    ro, sj = _ro(claims, m.group(1))
    return 200, desk.journey_detail(sj["service_journey_id"], claims.get("role"))


def _line_status(event, claims, m):
    if claims.get("role") == "dealer_parts":
        raise r.Bad(403, "parts counter may not change line status")
    _write(claims, LINE_STATUS)
    ro, sj = _ro(claims, m.group(1))
    status = r.body(event).get("status")
    line_id = m.group(2)
    if line_id.startswith("LN-"):
        est = desk._latest_estimate(sj["service_journey_id"], None)
        if not est:
            raise r.Bad(404, "no estimate on this RO")
        est = estimates.persist_lines(est["estimate_id"])
        lines = est.get("lines") or []
        line = next((l for l in lines if l["line_id"] == line_id), None)
        if line is None:
            raise r.Bad(404, f"line {line_id} not on this RO")
        if line.get("decision") != "approve":
            raise r.Bad(409, "only customer-approved lines can be worked")
        if status not in ("in_progress", "complete", "qc_passed"):
            raise r.Bad(400, "status must be in_progress, complete, or qc_passed")
        line["line_status"] = status
        line["technician_id"] = claims["user_id"]
        ddb.put_item("DEALER_ServiceEstimates", est)
        for p in ddb.scan_filter("DEALER_Parts", "line_id", line_id):
            repair.update_line_status(sj["service_journey_id"], p["part_id"], status, actor=claims["user_id"])
        if not ddb.scan_filter("DEALER_Parts", "line_id", line_id) and status in ("complete", "qc_passed"):
            from ...events import bus
            bus.emit("ro.line_completed", {"service_journey_id": sj["service_journey_id"], "line_id": line_id,
                                           "status": status, "dealer_id": sj.get("dealer_id")}, actor=claims["user_id"])
        return 200, {"line_id": line_id, "status": status, "service_journey_id": sj["service_journey_id"]}
    return 200, repair.update_line_status(sj["service_journey_id"], line_id, status, actor=claims["user_id"])


def _ro_close(event, claims, m):
    _write(claims, ADVISORS)
    ro, sj = _ro(claims, m.group(1))
    pickup = r.body(event).get("pickup")
    if pickup is not None and not isinstance(pickup, dict):
        raise r.Bad(400, "pickup must be an object {mode, address?, window_start?, window_end?, driver_id?, note?}")
    return 200, repair.notify_ready(sj["service_journey_id"], actor=claims["user_id"], pickup=pickup)


# --- inspection / diagnose ------------------------------------------------------------

def _inspection_get(event, claims, m):
    _read(claims)
    ro, sj = _ro(claims, m.group(1))
    rows = ddb.scan_filter("DEALER_Inspections", "service_journey_id", sj["service_journey_id"])
    return 200, {"ro_id": ro["ro_id"], "service_journey_id": sj["service_journey_id"], "stage": sj.get("stage"),
                 "findings": sorted(rows, key=lambda i: i.get("inspection_id") or "")}


def _inspection_post(event, claims, m):
    _write(claims, INSPECTORS)
    ro, sj = _ro(claims, m.group(1))
    b = r.body(event)
    if not isinstance(b.get("findings"), list) or not b["findings"]:
        raise r.Bad(400, "findings must be a non-empty list")
    out = inspection.record_inspection(sj["service_journey_id"], b["findings"], actor=claims["user_id"],
                                       require_evidence=True, template_id=b.get("template_id"))
    advanced = None
    cur = ddb.get_item("DEALER_ServiceJourneys", {"service_journey_id": sj["service_journey_id"]})
    if cur and cur.get("stage") == "CHECK_IN":
        svc_supervisor.advance_service_journey(sj["service_journey_id"], "technician started MPI", claims["user_id"])
        cur = ddb.get_item("DEALER_ServiceJourneys", {"service_journey_id": sj["service_journey_id"]})
    if cur and cur.get("stage") == "INSPECT":
        advanced = svc_supervisor.advance_service_journey(sj["service_journey_id"],
                                                          f"MPI submitted ({len(b['findings'])} items)", claims["user_id"])
    return 200, {**out, "advanced": advanced}


def _templates(event, claims, _m):
    _read(claims)
    q = r.qs(event)
    return 200, desk.templates(_market(claims, q), q.get("model_id"))


def _dtc(event, claims, _m):
    _read(claims)
    q = r.qs(event)
    return 200, desk.diagnose((q.get("codes") or "").split(","), q.get("vin"))


def _procedures(event, claims, _m):
    _read(claims)
    q = r.qs(event)
    if not q.get("labor_op"):
        raise r.Bad(400, "query must include labor_op")
    return 200, desk.get_repair_procedure(q["labor_op"], q.get("vin"))


def _labor(event, claims, _m):
    _read(claims)
    q = r.qs(event)
    if q.get("labor_op"):
        out = connectors.get_labor_time(q["labor_op"])
        if claims.get("role") == "dealer_technician":
            out = {k: v for k, v in out.items() if k not in ("shop_rate", "labor_total")}
        return 200, out
    ops = desk.labor_ops(_market(claims, q))
    if claims.get("role") == "dealer_technician":
        ops = [{k: v for k, v in o.items() if k != "shop_rate"} for o in ops]
    return 200, {"labor_ops": ops}


def _parts_lookup(event, claims, _m):
    _read(claims)
    q = r.qs(event)
    out = connectors.lookup_parts(labor_op_id=q.get("labor_op"),
                                  part_numbers=[q["part_no"]] if q.get("part_no") else None, ro_id=q.get("ro_id"))
    if claims.get("role") == "dealer_technician":
        out["parts"] = [{k: v for k, v in p.items() if k != "unit_price"} for p in out["parts"]]
    return 200, out


def _coverage(event, claims, _m):
    _read(claims)
    q = r.qs(event)
    if not q.get("vin"):
        raise r.Bad(400, "query must include vin")
    v = ddb.get_item("DEALER_Vehicles", {"vin": q["vin"]})
    if v is None:
        raise r.Bad(404, f"vehicle {q['vin']} not found")
    r.dealer_in_scope(claims, v.get("dealer_id"))
    out = {"vin": q["vin"], "contract": connectors.check_coverage(q["vin"], q.get("code"), market=v.get("market_id") or "us")}
    if q.get("item"):
        out["line"] = estimates.line_coverage(v, q["item"])
    return 200, out


# --- estimates -------------------------------------------------------------------------

def _estimate_build(event, claims, m):
    _write(claims, INSPECTORS)
    ro, sj = _ro(claims, m.group(1))
    b = r.body(event)
    existing = desk._latest_estimate(sj["service_journey_id"], None)
    if existing and existing.get("status") == "pending" and existing.get("lines") and b.get("adjustments"):
        out = estimates.rebuild(existing["estimate_id"], b["adjustments"], claims["user_id"])
    else:
        built = inspection.build_estimate(sj["service_journey_id"], actor=claims["user_id"],
                                          adjustments=b.get("adjustments"))
        out = estimates.estimate_view(built["estimate_id"])
    cur = ddb.get_item("DEALER_ServiceJourneys", {"service_journey_id": sj["service_journey_id"]})
    advanced = None
    if cur and cur.get("stage") == "DIAGNOSE":
        advanced = svc_supervisor.advance_service_journey(sj["service_journey_id"], "estimate built", claims["user_id"])
    if claims.get("role") == "dealer_technician":
        out = {**{k: v for k, v in out.items() if k in ("estimate_id", "status", "service_journey_id", "ro_id")},
               "lines": [desk._mask_line(l) for l in out["lines"]]}
    return 201, {**out, "advanced": advanced}


def _estimate_get(event, claims, m):
    e, sj = _est(claims, m.group(1))
    out = estimates.estimate_view(m.group(1))
    if r.qs(event).get("explain") == "1":
        for line in out["lines"]:
            if line.get("finding_ref"):
                line["explanation"] = connectors.explain_finding(line["finding_ref"], sj["service_journey_id"])
    return 200, out


def _estimate_patch(event, claims, m):
    _est(claims, m.group(1))
    _write(claims, ADVISORS)
    b = r.body(event)
    if not isinstance(b.get("adjustments"), dict):
        raise r.Bad(400, "body must include adjustments {finding_ref: {labor_hours?, part_qty?, include?}}")
    return 200, estimates.rebuild(m.group(1), b["adjustments"], claims["user_id"])


def _estimate_present(event, claims, m):
    e, sj = _est(claims, m.group(1))
    _write(claims, ADVISORS)
    estimates.persist_lines(m.group(1))
    out = connectors.present_estimate(sj["service_journey_id"], actor=claims["user_id"],
                                      channel=r.body(event).get("channel") or "magic_link")
    return 200, {**out, "customer_mode_url": f"/garage/service/{sj['service_journey_id']}?estimate={m.group(1)}"}


def _estimate_decisions(event, claims, m):
    e, sj = _est(claims, m.group(1))
    b = r.body(event)
    if claims.get("role") == "customer":
        decided_by = "customer"
    else:
        _write(claims, ADVISORS)
        decided_by = "advisor"
    out = estimates.decide_lines(m.group(1), b.get("decisions") or [], claims["user_id"], decided_by)
    if decided_by == "advisor" and b.get("advance") and out["status"] in ("approved", "partially_approved", "declined"):
        cur = ddb.get_item("DEALER_ServiceJourneys", {"service_journey_id": sj["service_journey_id"]})
        if cur and cur.get("stage") == "APPROVE" and out["status"] != "declined":
            out["advanced"] = svc_supervisor.advance_service_journey(sj["service_journey_id"],
                                                                     "customer decision recorded", claims["user_id"])
    return 200, out


def _re_present(event, claims, _m):
    _write(claims, ADVISORS)
    ids = r.body(event).get("estimate_ids") or []
    if not ids:
        raise r.Bad(400, "estimate_ids is required")
    done, failed = [], []
    for eid in ids:
        try:
            e, sj = _est(claims, eid)
            if e.get("status") not in ("presented", "pending"):
                failed.append({"estimate_id": eid, "error": f"status {e.get('status')}"}); continue
            connectors.present_estimate(sj["service_journey_id"], actor=claims["user_id"], channel="magic_link")
            done.append(eid)
        except r.Bad as ex:
            failed.append({"estimate_id": eid, "error": ex.msg})
    return 200, {"re_presented": done, "failed": failed}


_TALK = {
    "price_too_high": "Start with the safety items today ({good} for the essentials) and schedule the rest — "
                      "you'll get a reminder before anything becomes urgent.",
    "spouse_approval": "I'll send the estimate to My Garage so you can review it together, with photos of each item.",
    "not_ready": "No pressure — the yellow items can wait; we'll remind you in 30 days and keep the quote.",
    "payment": "We can split the work across visits; the good / better / best options show each total.",
}


def _prioritize(event, claims, m):
    """A7 prioritize_findings: reorder the lines by urgency and persist the order."""
    _est(claims, m.group(1))
    _write(claims, ADVISORS)
    return 200, estimates.prioritize_findings(m.group(1), claims["user_id"])


def _pushback(event, claims, m):
    e, sj = _est(claims, m.group(1))
    _write(claims, ADVISORS)
    utt = (r.body(event).get("utterance") or "").strip()
    if not utt:
        raise r.Bad(400, "utterance is required")
    from ...agents import objection
    strategies = ddb.scan_filter("DEALER_ObjectionStrategies", "market_id", sj.get("market_id") or "us")
    matched = objection.classify(utt, strategies)
    view = estimates.estimate_view(m.group(1))
    good = (view.get("bundles") or {}).get("good", {})
    otype = (matched or {}).get("objection_type")
    track = (_TALK.get(otype) or "Acknowledge the concern, explain the finding with its photo, and offer to defer non-urgent items.")
    return 200, {"utterance": utt, "objection_type": otype, "matched": bool(matched),
                 "talk_track": track.replace("{good}", "the good bundle"),
                 "good_bundle_total": good.get("customer_total"), "currency": view.get("currency"),
                 "strategy_source_ref": (matched or {}).get("strategy_id"),
                 "source_ref": "A3.classify_objection (service talk track) + A7 bundles"}


def _explain(event, claims, m):
    _read(claims)
    if claims.get("role") == "dealer_technician":
        raise r.Bad(403, "customer explanations are an advisor tool")
    out = connectors.explain_finding(m.group(1))
    return 200, out


# --- parts desk ------------------------------------------------------------------------

def _parts_desk(event, claims, _m):
    r.roles(claims, PARTS | {"dealer_technician"}, allow_oem=True)
    return 200, desk.parts_desk(claims)


def _held(event, claims, _m):
    r.roles(claims, PARTS, allow_oem=True)
    return 200, {"held_ros": desk.parts_desk(claims)["held_ros"]}


def _order_create(event, claims, _m):
    _write(claims, PARTS)
    b = r.body(event)
    r.require(b, "ro_id", "part_number")
    try:
        return 201, desk.create_parts_order(claims, b["ro_id"], b["part_number"], int(b.get("qty") or 1),
                                            b.get("eta"), b.get("status") or "ordered", b.get("supersedes"))
    except desk.Forbidden as e:
        raise r.Bad(403, str(e)) from e


def _order_patch(event, claims, m):
    _write(claims, PARTS)
    b = r.body(event)
    try:
        return 200, desk.update_parts_order(claims, m.group(1), b.get("status"), b.get("eta"))
    except desk.Forbidden as e:
        raise r.Bad(403, str(e)) from e


# --- declined / history / cockpit -------------------------------------------------------

def _declined(event, claims, _m):
    r.roles(claims, ADVISORS, allow_oem=True)
    q = r.qs(event)
    if q.get("dealer"):
        r.dealer_in_scope(claims, q["dealer"])
    out = desk.declined(claims, q.get("due_before"), q.get("status"))
    if q.get("dealer"):
        out["items"] = [i for i in out["items"] if i.get("dealer_id") == q["dealer"]]
        out["count"] = len(out["items"])
    return 200, out


def _declined_patch(event, claims, m):
    _write(claims, ADVISORS)
    b = r.body(event)
    try:
        return 200, desk.update_declined(claims, m.group(1), status=b.get("status"), cadence_days=b.get("cadence_days"))
    except desk.Forbidden as e:
        raise r.Bad(403, str(e)) from e


def _history(event, claims, m):
    _read(claims)
    vin = m.group(1)
    v = ddb.get_item("DEALER_Vehicles", {"vin": vin})
    if v is None and not ddb.scan_filter("DEALER_ServiceJourneys", "vin", vin):
        raise r.Bad(404, f"no service history for {vin}")
    # history spans dealers in the tenant (§5.20.6); staff of any dealer may read it
    return 200, desk.history(vin, claims.get("role"))


def _cockpit(event, claims, m):
    r.roles(claims, {"dealer_service_manager", "dealer_manager", "dealer_service"}, allow_oem=True)
    q = r.qs(event)
    labor, close = float(q.get("labor_rate_delta_pct") or 0), float(q.get("close_rate_delta_pts") or 0)
    rvr = float(q["rvr_threshold_pct"]) if q.get("rvr_threshold_pct") else None
    out = desk.cockpit(claims, q.get("market"), labor, close, rvr_threshold_pct=rvr)
    # The nightly digest's Service Agent insight describes the store at its configured
    # repair-vs-replace threshold with no what-if applied; a moved slider keeps the
    # figures-only insight, which follows the sliders.
    if not labor and not close and rvr in (None, estimates.RVR_THRESHOLD_PCT):
        written = store_digest.stored_insight(out["market_id"], store_digest.scope_key(claims),
                                              store_digest.SERVICE_SURFACE,
                                              facts=store_digest.service_facts(out))
        if written:
            out["insight"] = {**written, "figures_only": out["insight"]["text"]}
    tile = m.group(1) if m.lastindex else None
    if tile:
        key = tile.replace("-", "_")
        match = {k: v for k, v in out["tiles"].items() if k.startswith(key)}
        if not match and key not in ("what_if", "insight"):
            raise r.Bad(404, f"unknown cockpit tile {tile}")
        return 200, {"tile": tile, "value": match or out.get(key), "source_ref": out["source_ref"]}
    return 200, out


def _predictive_get(event, claims, _m):
    """Predicted part-wear across the caller's stores (A7 predictive watch)."""
    r.roles(claims, SERVICE | ADVISORS | {"dealer_bdc", "dealer_manager"}, allow_oem=True)
    q = r.qs(event)
    return 200, predictive.predictions(claims.get("dealer_ids") or None,
                                       q.get("market") or claims.get("market_id") or "us",
                                       vin=q.get("vin"))


def _predictive_run(event, claims, _m):
    """Scan now: raise a predictive lead (+ customer offer) per due prediction."""
    r.roles(claims, SERVICE | ADVISORS | {"dealer_bdc", "dealer_manager"})
    b = r.body(event)
    return 200, predictive.run_scan(claims.get("dealer_ids") or None,
                                    claims.get("market_id") or "us", claims["user_id"],
                                    vin=b.get("vin"))


ROUTES = [
    ("GET", re.compile(r"^/service/predictive$"), _predictive_get),
    ("POST", re.compile(r"^/service/predictive/run$"), _predictive_run),
    ("GET", re.compile(r"^/service/journeys$"), _journeys),
    ("GET", re.compile(r"^/service/journeys/([^/]+)$"), _journey),
    ("GET", re.compile(r"^/service/journeys/([^/]+)/brief$"), _brief),
    ("POST", re.compile(r"^/service/journeys/([^/]+)/checkin$"), _checkin),
    ("POST", re.compile(r"^/service/journeys/([^/]+)/claim$"), _claim),
    ("GET", re.compile(r"^/service/journeys/([^/]+)/message$"), _message_draft),
    ("POST", re.compile(r"^/service/journeys/([^/]+)/message$"), _message_send),
    ("GET", re.compile(r"^/service/arrivals$"), _arrivals),
    ("GET", re.compile(r"^/service/attention$"), _attention),
    ("GET", re.compile(r"^/service/availability$"), _availability),
    ("GET", re.compile(r"^/service/express-menu$"), _express_menu),
    ("GET", re.compile(r"^/service/due$"), _predict),
    ("POST", re.compile(r"^/service/bookings$"), _book),
    ("GET", re.compile(r"^/service/bookings/([^/]+)$"), _booking_get),
    ("PATCH", re.compile(r"^/service/bookings/([^/]+)$"), _booking_patch),
    ("DELETE", re.compile(r"^/service/bookings/([^/]+)$"), _booking_delete),
    ("GET", re.compile(r"^/service/ros/([^/]+)$"), _ro_get),
    ("PATCH", re.compile(r"^/service/ros/([^/]+)/lines/([^/]+)/status$"), _line_status),
    ("POST", re.compile(r"^/service/ros/([^/]+)/close$"), _ro_close),
    ("GET", re.compile(r"^/service/ros/([^/]+)/inspection$"), _inspection_get),
    ("POST", re.compile(r"^/service/ros/([^/]+)/inspection$"), _inspection_post),
    ("POST", re.compile(r"^/service/ros/([^/]+)/estimate$"), _estimate_build),
    ("GET", re.compile(r"^/service/inspection-templates$"), _templates),
    ("GET", re.compile(r"^/service/dtc$"), _dtc),
    ("GET", re.compile(r"^/service/procedures$"), _procedures),
    ("GET", re.compile(r"^/service/labor$"), _labor),
    ("GET", re.compile(r"^/service/parts$"), _parts_lookup),
    ("GET", re.compile(r"^/service/coverage$"), _coverage),
    ("POST", re.compile(r"^/service/estimates/re-present$"), _re_present),
    ("GET", re.compile(r"^/service/estimates/([^/]+)$"), _estimate_get),
    ("PATCH", re.compile(r"^/service/estimates/([^/]+)$"), _estimate_patch),
    ("POST", re.compile(r"^/service/estimates/([^/]+)/present$"), _estimate_present),
    ("POST", re.compile(r"^/service/estimates/([^/]+)/decisions$"), _estimate_decisions),
    ("POST", re.compile(r"^/service/estimates/([^/]+)/prioritize$"), _prioritize),
    ("POST", re.compile(r"^/service/estimates/([^/]+)/pushback$"), _pushback),
    ("GET", re.compile(r"^/service/explain/([^/]+)$"), _explain),
    ("GET", re.compile(r"^/service/parts/desk$"), _parts_desk),
    ("GET", re.compile(r"^/service/parts/orders$"), _parts_desk),
    ("POST", re.compile(r"^/service/parts/orders$"), _order_create),
    ("PATCH", re.compile(r"^/service/parts/orders/([^/]+)$"), _order_patch),
    ("GET", re.compile(r"^/service/parts/held-ros$"), _held),
    ("GET", re.compile(r"^/service/declined$"), _declined),
    ("PATCH", re.compile(r"^/service/declined/([^/]+)$"), _declined_patch),
    ("GET", re.compile(r"^/service/history/([^/]+)$"), _history),
    ("GET", re.compile(r"^/service/cockpit$"), _cockpit),
    ("GET", re.compile(r"^/service/cockpit/([^/]+)$"), _cockpit),
]

ERRORS = [
    (desk.NotFound, 404), (estimates.NotFound, 404), (inspection.NotFound, 404), (repair.NotFound, 404),
    (connectors.NotFound, 404), (booking.NotFound, 404), (svc_supervisor.NotFound, 404),
    (desk.Forbidden, 403),
    (inspection.MissingEvidence, 422), (estimates.InvalidDecision, 422), (desk.Invalid, 400),
    (repair.NotReady, 409), (svc_supervisor.HandoffIncomplete, 409), (svc_supervisor.InvalidTransition, 409),
    (booking.InvalidWaitType, 400),
]
