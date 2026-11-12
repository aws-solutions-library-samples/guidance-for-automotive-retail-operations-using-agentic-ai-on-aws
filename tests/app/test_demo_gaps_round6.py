"""Demo gaps, round 6 — the classes behind six rounds of 'new' gaps: one payment formula and one
payment of record; a delivery closes what it leaves behind; the seed's dates agree with each
other; and words, not codes, in everything a person reads."""
from __future__ import annotations

import json
from pathlib import Path

from app.services.agents import discovery, trade
from app.services.common import ddb
from app.services.dealer import delivery
from app.services.finance import payment, prequalify
from app.services.service import desk, predictive
from tests.app.conftest import api_event, call

ROOT = Path(__file__).resolve().parents[2]


# --- one payment ------------------------------------------------------------------------

def test_every_module_uses_the_one_monthly_formula():
    assert payment.monthly(30000, 6.4, 72) == 502.87
    assert payment.monthly(30000, 0, 60) == 500.0 and payment.monthly(0, 6.4, 72) == 0.0
    for fn in (prequalify._monthly, trade._monthly, discovery._monthly):
        assert fn(30000, 6.4, 72) == payment.monthly(30000, 6.4, 72)


def test_payment_of_record_is_the_deal_once_booked(fake_ddb):
    j = next(j for j in ddb.scan_filter("DEALER_Journeys", "market_id", "us") if j.get("stage") == "OWN"
             and any(d.get("status") == "booked" and d.get("type") != "cash"
                     for d in ddb.scan_filter("DEALER_Deals", "journey_id", j["journey_id"])))
    rec = payment.payment_of_record(j["journey_id"])
    assert rec and rec["basis"] == "deal" and rec["monthly"] > 0 and rec["lender"]
    code, fin = call(api_event("GET", "/garage/finance", user=f"cust:{j['customer_id']}", role="customer",
                               customer_id=j["customer_id"]))
    assert code == 200 and fin["payment"]["monthly"] == rec["monthly"] and fin["payment"]["basis"] == "deal"
    code, b = call(api_event("GET", "/garage/builds", user=f"cust:{j['customer_id']}", role="customer",
                             customer_id=j["customer_id"]))
    for build in b["builds"]:
        if build.get("journey_id") == j["journey_id"]:
            assert build["payment"]["monthly"] == rec["monthly"]          # the same number on every screen


def test_builds_say_what_each_incentive_does(fake_ddb):
    j = next(j for j in ddb.scan_filter("DEALER_Journeys", "market_id", "us") if j.get("config_id"))
    code, b = call(api_event("GET", "/garage/builds", user=f"cust:{j['customer_id']}", role="customer",
                             customer_id=j["customer_id"]))
    assert code == 200
    for build in b["builds"]:
        for inc in build["incentives"]:
            assert inc["kind"] in ("price", "apr", "informational") and inc["how"]
            assert "(synthetic)" not in inc["name"] and "(generic template)" not in inc["name"]
        off = sum(i["amount"] or 0 for i in build["incentives"] if i["kind"] == "price")
        assert abs(off - (build["incentives_total"] or 0)) < 1          # "−$0" only when nothing comes off the price


# --- a delivery closes what it leaves behind ----------------------------------------------

def test_mark_delivered_closes_the_open_test_drive(fake_ddb):
    d = next(x for x in ddb.scan_all("DEALER_Deliveries") if x.get("status") != "completed" and x.get("journey_id"))
    jid = d["journey_id"]
    ddb.put_item("DEALER_Appointments", {"appointment_id": "AP-TEST-TD", "journey_id": jid, "type": "test_drive",
                                         "status": "scheduled", "slot_start": "2026-12-01T15:00:00Z",
                                         "customer_id": d.get("customer_id"), "dealer_id": d.get("dealer_id")})
    j = ddb.get_item("DEALER_Journeys", {"journey_id": jid})
    ddb.put_item("DEALER_Journeys", {**j, "stage": "DELIVER", "context": {**(j.get("context") or {}), "delivered": False}})
    out = delivery.mark_delivered(d["delivery_id"], actor="U-US-00009")
    assert "AP-TEST-TD" in out["appointments_closed"]
    assert ddb.get_item("DEALER_Appointments", {"appointment_id": "AP-TEST-TD"})["status"] == "completed"


# --- the seed agrees with itself ------------------------------------------------------------

def test_seed_vehicle_delivery_is_the_journey_delivery_event():
    for m in ("us", "de"):
        t = ROOT / "sample-data" / "synthetic" / m / "tables"
        journeys = {j["journey_id"]: j for j in json.loads((t / "DEALER_Journeys.json").read_text())}
        delivered = {j["customer_id"]: j for j in journeys.values() if j.get("delivered_at")}
        assert delivered
        for v in json.loads((t / "DEALER_Vehicles.json").read_text()):
            j = delivered.get(v.get("customer_id"))
            if j and j.get("primary_model_id") == v.get("model_id") and str(v.get("source_ref", "")).endswith("(delivered)"):
                assert v["delivered_at"] == j["delivered_at"], (m, v["vin"])


def test_seed_open_visits_are_current_and_promises_fit_the_stage():
    for m in ("us", "de"):
        rows = json.loads((ROOT / "sample-data" / "synthetic" / m / "tables" / "DEALER_ServiceJourneys.json").read_text())
        now = "2026-09-10T09:00:00Z"                               # the seed's frozen clock
        for sj in rows:
            if sj["stage"] == "BOOK":
                assert sj["promised_at"] > now, sj["service_journey_id"]
            elif sj["stage"] in ("CHECK_IN", "INSPECT", "DIAGNOSE", "APPROVE", "REPAIR"):
                assert sj["opened_at"] > "2026-09-07", sj["service_journey_id"]
                assert sj["promised_at"] > "2026-09-10T00:00", sj["service_journey_id"]
        assert any(sj["projected_at"] > sj["promised_at"] for sj in rows if sj["stage"] == "REPAIR")   # real at-risk cases


# --- words, not codes ------------------------------------------------------------------------

def test_predictive_text_reads_as_english(fake_ddb):
    pred = {"vin": "V", "part": "Front brake pads", "predicted_due_mileage": 40000, "odometer": 38000, "overdue": False,
            "confidence": 0.78, "offer_pct": 15, "also_due": [{"part": "12V battery"}, {"part": "Cabin air filter"}]}
    text = predictive.lead_comment(pred)
    assert "the front brake pads on this vehicle are due" in text and "12V battery and cabin air filter" in text and "12v" not in text
    assert predictive.lead_comment({**pred, "part": "12V battery", "also_due": []}).count(" is due ") == 1


def test_lead_desk_names_the_car_a_heads_up_is_about(fake_ddb):
    out = predictive.run_scan({"D-101"}, "us", "test")
    assert out["leads"]
    code, queue = call(api_event("GET", "/leads/queue", user="U-US-00005", role="dealer_bdc", dealer_ids="D-101"))
    assert code == 200
    cards = [c for b in queue["queue"].values() for c in b if c.get("source") == "predictive_maintenance"]
    assert cards and all(c["vehicle_name"] and c["vehicle_name"][0].isdigit() for c in cards)   # "2022 Meridian Ion M"


def test_activity_feed_shows_names_not_ids(fake_ddb):
    j = next(j for j in ddb.scan_filter("DEALER_Journeys", "market_id", "us") if j.get("stage") == "OWN")
    code, feed = call(api_event("GET", f"/journeys/{j['journey_id']}/activity", user="U-US-00002", dealer_ids="D-101"))
    assert code == 200 and feed["items"]
    for i in feed["items"]:
        for k in ("actor_name", "to_name", "summary"):
            v = i.get(k)
            if v:
                assert "U-US-" not in v and "DEALER_" not in v and "=" not in v, (k, v)


def test_needs_attention_tells_past_from_at_risk(fake_ddb_today):
    claims = {"dealer_ids": {"D-101"}, "is_oem": False, "market_id": "us", "role": "dealer_service"}
    items = [i for i in desk.attention(claims)["items"] if i["kind"] == "past_promise"]
    assert items
    for i in items:
        assert ("past the promised time" in i["title"]) == bool(i["missed"])
        assert "SJ-US-" not in i["title"]
