"""Service Desk API (spec §6 /service/*, §5.20; acceptance #26–#28, #30)."""
from __future__ import annotations

import pytest

from app.services.events import bus
from tests.app.conftest import api_event, call

ADV = dict(user="U-US-00007", role="dealer_service", dealer_ids="D-101")
TECH = dict(user="U-TECH-1", role="dealer_technician", dealer_ids="D-101")
PARTS = dict(user="U-PARTS-1", role="dealer_parts", dealer_ids="D-101")
OTHER = dict(user="U-US-00016", role="dealer_service", dealer_ids="D-102")


def _types():
    return [e["type"].split("@")[0] for e in bus.EMITTED]


def _row(fake, table, key, value):
    return next(r for r in fake.rows(table) if r.get(key) == value)


def test_board_lists_scoped_cards_with_badges(fake_ddb):
    code, b = call(api_event("GET", "/service/journeys", **ADV))
    assert code == 200 and b["cards"]
    assert {c["dealer_id"] for c in b["cards"]} == {"D-101"}
    assert set(b["lanes"]) == {"BOOK", "CHECK_IN", "INSPECT", "DIAGNOSE", "APPROVE", "REPAIR", "READY", "FOLLOW_UP"}
    card = next(c for c in b["cards"] if c["service_journey_id"] == "SJ-US-00001")
    assert card["badges"]["waiting_approval"] is True
    assert "customer_name" in card


def test_technician_payloads_are_masked_server_side(fake_ddb):
    code, b = call(api_event("GET", "/service/journeys", **TECH, query={"unassigned": "1"}))
    assert code == 200 and b["masked"] is True
    assert all("customer_name" not in c and "customer_id" not in c for c in b["cards"])
    code, d = call(api_event("GET", "/service/journeys/SJ-US-00001", **TECH))
    assert code == 200 and d["masked"] and "customer" not in d
    for line in d["lines"]:
        assert "line_total" not in line and "labor_amount" not in line
        assert set(line["coverage"]) == {"pay_type"}
    assert all("unit_price" not in p for p in d["parts"])
    code, _ = call(api_event("GET", "/service/estimates/EST-US-00001", **TECH))
    assert code == 403  # technicians cannot open Estimate & Approval (acc #30)


def test_advisor_detail_has_customer_and_pricing(fake_ddb):
    code, d = call(api_event("GET", "/service/journeys/SJ-US-00001", **ADV))
    assert code == 200 and d["customer"]["name"]
    assert d["lines"] and all("line_total" in l for l in d["lines"])
    assert "service_lane" in d


def test_scope_enforced(fake_ddb):
    code, _ = call(api_event("GET", "/service/journeys/SJ-US-00001", **OTHER))
    assert code == 403


def test_estimate_view_lines_are_sourced_with_bundles(fake_ddb):
    code, e = call(api_event("GET", "/service/estimates/EST-US-00001", **ADV))
    assert code == 200 and e["lines"]
    for line in e["lines"]:
        assert line["labor_source_ref"].startswith("DEALER_LaborOps:")
        assert line["finding_ref"].startswith("INS-")
        assert line["coverage"]["pay_type"] in ("warranty", "customer_pay")
    assert e["bundles"]["best"]["count"] == len(e["lines"])
    assert e["bundles"]["good"]["count"] <= e["bundles"]["better"]["count"] <= e["bundles"]["best"]["count"]


def _multi_line_estimate(fake):
    """An APPROVE-stage D-101..D-111 estimate with >= 2 actionable findings."""
    for sj in fake.rows("DEALER_ServiceJourneys"):
        if sj.get("stage") != "APPROVE" or sj.get("market_id") != "us":
            continue
        n = sum(1 for i in fake.rows("DEALER_Inspections")
                if i.get("service_journey_id") == sj["service_journey_id"] and i.get("status") in ("red", "yellow"))
        est = [e for e in fake.rows("DEALER_ServiceEstimates") if e.get("service_journey_id") == sj["service_journey_id"]]
        if n >= 2 and est:
            return sj, est[0]["estimate_id"]
    pytest.skip("no multi-line estimate in seed")


def test_partial_approval_dispatches_and_declines(fake_ddb):
    bus.EMITTED.clear()
    sj, est_id = _multi_line_estimate(fake_ddb)
    who = dict(user="U-ADV", role="dealer_service", dealer_ids=sj["dealer_id"])
    code, e = call(api_event("GET", f"/service/estimates/{est_id}", **who))
    lines = e["lines"]
    assert len(lines) >= 2
    decisions = [{"line_id": lines[0]["line_id"], "decision": "approve", "reason": "customer said yes on call"}] + \
                [{"line_id": l["line_id"], "decision": "decline", "reason": "budget"} for l in lines[1:]]
    code, err = call(api_event("POST", f"/service/estimates/{est_id}/decisions", **who,
                               body={"decisions": [{"line_id": lines[0]["line_id"], "decision": "approve"}]}))
    assert code == 422  # advisor-recorded decision needs a reason
    code, out = call(api_event("POST", f"/service/estimates/{est_id}/decisions", **who, body={"decisions": decisions}))
    assert code == 200, out
    assert out["status"] == "partially_approved"
    assert out["dispatched"] == [lines[0]["line_id"]]
    assert len(out["declined_work"]) == len(lines) - 1
    row = _row(fake_ddb, "DEALER_DeclinedWork", "customer_id", sj["customer_id"])   # keyed per customer
    dw = next(i for i in row["items"] if i["declined_item_id"] == out["declined_work"][0])
    assert dw["evidence_snapshot"]["finding_ref"] and dw["decided_by"] == "advisor"
    est_row = _row(fake_ddb, "DEALER_ServiceEstimates", "estimate_id", est_id)
    assert est_row["approved_line_ids"] == [lines[0]["line_id"]]       # portal-compatible fields
    code, reg = call(api_event("GET", "/service/declined", **who))
    assert code == 200 and any(r["declined_id"] == out["declined_work"][0] for r in reg["items"])
    code, upd = call(api_event("PATCH", f"/service/declined/{out['declined_work'][0]}", **who, body={"cadence_days": 60}))
    assert code == 200 and upd["follow_up_cadence_days"] == 60
    assert _types().count("estimate.line_decided") == len(lines)
    # APPROVE gate now clears
    code, adv = call(api_event("POST", f"/service/{sj['service_journey_id']}/advance", **who, body={"evidence": "decided"}))
    assert code == 200 and adv["to"] == "REPAIR"


def test_customer_decides_own_estimate_only(fake_ddb):
    code, e = call(api_event("GET", "/service/estimates/EST-US-00001", user="C-US-J1", role="customer", customer_id="C-US-J1"))
    assert code == 200
    lid = e["lines"][0]["line_id"]
    code, out = call(api_event("POST", "/service/estimates/EST-US-00001/decisions", user="C-US-J1", role="customer",
                               customer_id="C-US-J1", body={"decisions": [{"line_id": lid, "decision": "approve"}]}))
    assert code == 200 and next(l for l in out["lines"] if l["line_id"] == lid)["decided_by"] == "customer"
    code, _ = call(api_event("POST", "/service/estimates/EST-US-00001/decisions", user="C-OTHER", role="customer",
                             customer_id="C-OTHER", body={"decisions": [{"line_id": lid, "decision": "approve"}]}))
    assert code == 403


def test_full_flow_checkin_inspect_estimate_present(fake_ddb):
    # any US service journey still at BOOK; the advisor acts in that store
    row = next(r for r in fake_ddb.rows("DEALER_ServiceJourneys") if r["stage"] == "BOOK" and r["market_id"] == "us")
    sj = row["service_journey_id"]
    ADV = dict(user="U-US-00007", role="dealer_service", dealer_ids=row["dealer_id"])
    TECH = dict(user="U-TECH-1", role="dealer_technician", dealer_ids=row["dealer_id"])
    code, out = call(api_event("POST", f"/service/journeys/{sj}/checkin", **ADV,
                               body={"mileage": 41250, "concerns": ["squeal when braking"],
                                     "photos": [{"name": "fl.jpg", "tag": "scratch front-left"}],
                                     "approval_preference": "text"}))
    assert code == 200, out
    ro_id = out["ro"]["ro_id"]
    assert _row(fake_ddb, "DEALER_ServiceJourneys", "service_journey_id", sj)["stage"] == "CHECK_IN"
    findings = [{"item": "Front brakes", "status": "red", "measurement": 2, "unit": "mm"},
                {"item": "Cabin filter", "status": "green"}]
    code, err = call(api_event("POST", f"/service/ros/{ro_id}/inspection", **TECH, body={"findings": findings}))
    assert code == 422 and "Front brakes" in err["error"]       # acc #28: evidence required
    findings[0]["evidence"] = ["SDOC-photo-1"]
    code, ins = call(api_event("POST", f"/service/ros/{ro_id}/inspection", **TECH, body={"findings": findings}))
    assert code == 200 and ins["advanced"]["to"] == "DIAGNOSE"
    code, est = call(api_event("POST", f"/service/ros/{ro_id}/estimate", **ADV, body={}))
    assert code == 201 and est["lines"][0]["item"] == "Front brakes"
    assert est["advanced"]["to"] == "APPROVE"
    bus.EMITTED.clear()
    code, pres = call(api_event("POST", f"/service/estimates/{est['estimate_id']}/present", **ADV, body={}))
    assert code == 200 and pres["approval_link"].startswith("/garage/login?token=") and "estimate.presented" in _types()
    code, gate = call(api_event("POST", f"/service/{sj}/advance", **ADV, body={"evidence": "x"}))
    assert code == 409   # no decision yet


def test_parts_role_limits_and_receive_clears_hold(fake_ddb):
    code, _ = call(api_event("PATCH", "/service/ros/RO-US-00001/lines/LN-x/status", **PARTS, body={"status": "complete"}))
    assert code == 403
    code, o = call(api_event("POST", "/service/parts/orders", **PARTS,
                             body={"ro_id": "RO-US-00001", "part_number": "P1", "qty": 1, "status": "backordered"}))
    assert code == 201
    bus.EMITTED.clear()
    code, upd = call(api_event("PATCH", f"/service/parts/orders/{o['parts_order_id']}", **PARTS, body={"status": "received"}))
    assert code == 200 and "parts.received" in _types()
    code, d = call(api_event("GET", "/service/parts/desk", **PARTS))
    assert code == 200 and "held_ros" in d


def test_history_cockpit_attention_availability(fake_ddb):
    vin = _row(fake_ddb, "DEALER_ServiceJourneys", "service_journey_id", "SJ-US-00001")["vin"]
    code, h = call(api_event("GET", f"/service/history/{vin}", **ADV))
    assert code == 200 and h["visits"]
    code, c = call(api_event("GET", "/service/cockpit", user="U-US-00001", role="dealer_manager", dealer_ids="D-101"))
    assert code == 200 and "recommended_work_close_rate" in c["tiles"] and c["insight"]["sources"]
    code, w = call(api_event("GET", "/service/cockpit", user="U-US-00001", role="dealer_manager", dealer_ids="D-101",
                             query={"labor_rate_delta_pct": "10"}))
    assert w["what_if"]["projected_labor_revenue"] == pytest.approx(w["what_if"]["labor_revenue"] * 1.1, rel=1e-3)
    code, a = call(api_event("GET", "/service/attention", **ADV))
    assert code == 200 and isinstance(a["items"], list)
    code, av = call(api_event("GET", "/service/availability", **ADV, query={"dealer": "D-101", "date": "2026-10-06"}))
    assert code == 200 and len(av["slots"]) == 9 and av["slots"][0]["capacity"] == 4


def test_diagnose_lookups_cite_sources(fake_ddb):
    code, d = call(api_event("GET", "/service/dtc", **TECH, query={"codes": "P0300,ZZZ"}))
    assert code == 200
    p0300 = d["codes"][0]
    assert p0300["found"] and p0300["source_ref"] == "connector:dms-sandbox DEALER_DTCReference:P0300"
    tsb_rows = [k for k in fake_ddb.rows("PLAT_KnowledgeCollections") if k.get("title") == "TSB — P0300"]
    if tsb_rows:   # the in-memory store keeps one row per collection_id (key override)
        assert p0300["tsb"]["source_ref"].startswith("quick_index:")
    assert d["codes"][1]["found"] is False
    code, p = call(api_event("GET", "/service/procedures", **TECH, query={"labor_op": "LOP-BRAKE-F"}))
    assert code == 200 and p["flat_rate_hours"]
    if any(k.get("title") == "Procedure — LOP-BRAKE-F" for k in fake_ddb.rows("PLAT_KnowledgeCollections")):
        assert p["citation"]["doc_id"]
    else:
        assert p["citation"] is None and p["source_ref"] == "connector:labor-sandbox DEALER_LaborOps:LOP-BRAKE-F"
    code, t = call(api_event("GET", "/service/inspection-templates", **TECH, query={"model_id": "MER-VOLT-M"}))
    assert code == 200 and "EV" in t["recommended"]
    ev = next(x for x in t["templates"] if x["template_id"] == t["recommended"])
    assert any(i["item"] == "HV battery SoH" for i in ev["items"])
    code, lab = call(api_event("GET", "/service/labor", **TECH, query={"labor_op": "LOP-OIL"}))
    assert code == 200 and "shop_rate" not in lab


def test_booking_and_reschedule(fake_ddb):
    vin = next(v["vin"] for v in fake_ddb.rows("DEALER_Vehicles") if v.get("dealer_id") == "D-101")
    code, b = call(api_event("POST", "/service/bookings", **ADV,
                             body={"vin": vin, "services": ["Oil change"], "wait_type": "loaner",
                                   "slot_start": "2026-10-06T09:00:00Z"}))
    assert code == 201, b
    bus.EMITTED.clear()
    code, r = call(api_event("PATCH", f"/service/bookings/{b['appointment_id']}", **ADV,
                             body={"slot_start": "2026-10-06T11:00:00Z"}))
    assert code == 200 and r["slot_start"] == "2026-10-06T11:00:00Z" and "service.appointment.changed" in _types()
    code, d = call(api_event("GET", "/service/due", **ADV, query={"vin": vin}))
    assert code == 200 and "next_service" in d


def test_pushback_and_message(fake_ddb):
    code, p = call(api_event("POST", "/service/estimates/EST-US-00001/pushback", **ADV,
                             body={"utterance": "that's way too expensive"}))
    assert code == 200 and p["talk_track"]
    code, m = call(api_event("GET", "/service/journeys/SJ-US-00001/message", **ADV, query={"purpose": "estimate"}))
    assert code == 200 and m["requires_approval"] and m["body"]
    code, _ = call(api_event("GET", "/service/journeys/SJ-US-00001/message", **TECH))
    assert code == 403
