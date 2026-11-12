"""Second round of demo gaps (2026-11-04); each test pins one reported symptom."""
from __future__ import annotations

import json
import urllib.parse

from app.services.api import handler
from app.services.common import ddb, vehicles
from app.services.customer import service_portal
from app.services.dealer import fni
from app.services.events import bus
from app.services.service import booking, connectors, desk, estimates
from tests.app.conftest import api_event


# --- presenting a trim is the build ----------------------------------------------------

def test_presenting_a_trim_saves_the_build_and_meets_both_gates(fake_ddb):
    j = next(x for x in ddb.scan_all("DEALER_Journeys") if x.get("stage") == "DISCOVER" and x.get("dealer_id") == "D-101")
    ev = api_event("POST", f"/journeys/{j['journey_id']}/present", user=j.get("owner_user_id") or "U-US-00002",
                   role="dealer_advisor", dealer_ids="D-101", body={"trim_id": "MER-CROSSTRAIL-T2"})
    out = handler.lambda_handler(ev, None)
    assert out["statusCode"] == 200 and json.loads(out["body"])["config_id"]
    after = ddb.get_item("DEALER_Journeys", {"journey_id": j["journey_id"]})
    assert after["context"]["shortlist"] and after["context"]["saved_build"]
    assert after["stage"] == "DISCOVER"                                   # the advisor advances
    assert ddb.scan_filter("PLAT_Configurations", "journey_id", j["journey_id"])   # Trade-In has a build


# --- the lead's Activity feed ------------------------------------------------------------

def test_a_seeded_answered_lead_shows_the_draft_and_the_send(fake_ddb):
    lead = next(x for x in ddb.scan_all("DEALER_Leads") if x.get("first_response_at") and x.get("dealer_id") == "D-101")
    ev = api_event("GET", "/traces", user="U-US-00005", role="dealer_bdc", dealer_ids="D-101",
                   query={"lead_id": lead["lead_id"], "limit": "30"})
    tools = {t["tool"] for t in json.loads(handler.lambda_handler(ev, None)["body"])["traces"]}
    assert {"a2.lead_intake", "a3.draft_first_response", "a3.send_first_response"} <= tools


# --- F&I: equity, stable rates, budget ------------------------------------------------------

def _desked_with_equity(fake_ddb):
    vals = {v["customer_id"]: v for v in ddb.scan_all("DEALER_Valuations") if (v.get("equity") or 0) > 5000}
    have = {d["journey_id"] for d in ddb.scan_all("DEALER_Deals")}
    j = next(x for x in ddb.scan_all("DEALER_Journeys") if x["customer_id"] in vals and x["journey_id"] not in have
             and x.get("stage") in ("CONFIGURE", "QUALIFY", "FINANCE") and x.get("primary_model_id") and x.get("primary_trim_id"))
    return fni.ensure_deal(j["journey_id"], "U-US-00006")["deal"], vals[j["customer_id"]]["equity"]


def test_trade_equity_lowers_the_amount_financed(fake_ddb):
    deal, equity = _desked_with_equity(fake_ddb)
    want = round(deal["price_total"] - deal["down_payment"] - equity, 2)
    out = fni.request_lender_decision(deal["deal_id"], "U-US-00006")
    assert {d["amount_financed"] for d in out["decisions"]} == {max(0.0, want)}
    ws = fni.deal_worksheet(deal["deal_id"])
    assert ws["price"]["amount_financed"] == max(0.0, want) and ws["price"]["trade_equity_applied"] == equity


def test_lender_rates_do_not_move_between_runs(fake_ddb):
    deal, _ = _desked_with_equity(fake_ddb)
    first = [(d["lender"], d["apr_pct"]) for d in fni.request_lender_decision(deal["deal_id"], "U-US-00006")["decisions"]]
    again = [(d["lender"], d["apr_pct"]) for d in fni.request_lender_decision(deal["deal_id"], "U-US-00006")["decisions"]]
    assert first == again


def test_the_desk_sees_whether_the_payment_fits_her_budget(fake_ddb):
    deal, _ = _desked_with_equity(fake_ddb)
    check = fni.request_lender_decision(deal["deal_id"], "U-US-00006")["budget_check"]
    assert check and check["fits"] == (check["monthly_payment"] <= check["monthly_max"])


# --- service booking fills the agent's visit ------------------------------------------------

def test_a_booking_fills_the_visit_the_service_agent_opened(fake_ddb):
    car = next(v for v in ddb.scan_all("DEALER_Vehicles") if v.get("customer_id") and v.get("dealer_id") == "D-101")
    bus.on_vehicle_delivered({"journey_id": "J-TEST-DLV", "customer_id": car["customer_id"], "dealer_id": "D-101",
                              "market_id": "us"})
    opened = ddb.get_item("DEALER_ServiceJourneys", {"service_journey_id": "SJ-DLV-J-TEST-DLV"})
    assert opened and opened["stage"] == "BOOK"
    vin = opened["vin"]
    before = [s for s in ddb.scan_filter("DEALER_ServiceJourneys", "vin", vin) if s.get("stage") == "BOOK"]
    out = booking.book_service(vin, wait_type="wait", preferred_slot="2026-11-10T14:00:00Z")
    after = [s for s in ddb.scan_filter("DEALER_ServiceJourneys", "vin", vin) if s.get("stage") == "BOOK"]
    assert out["filled_open_visit"] and out["service_journey_id"] in {s["service_journey_id"] for s in before}
    assert len(after) == len(before)                                       # no second visit


# --- lane hours are the store's -----------------------------------------------------------

def test_service_slots_are_the_stores_local_hours(fake_ddb):
    a = desk.availability("D-101", "2026-11-05")                           # Decatur, America/New_York (EST)
    assert a["timezone"] == "America/New_York"
    assert a["slots"][0]["slot_start"] == "2026-11-05T13:00:00Z" and a["slots"][0]["local_time"] == "08:00"
    assert a["slots"][-1]["local_time"] == "16:00"


# --- service never reads the sale's trade as the car in the bay ------------------------------

def test_a_traded_in_car_is_not_the_value_of_the_car_in_the_bay(fake_ddb):
    car = next(v for v in ddb.scan_all("DEALER_Vehicles") if v.get("model_id") and v.get("customer_id"))
    ddb.put_item("DEALER_Valuations", {"valuation_id": "VAL-TRADE-X", "customer_id": car["customer_id"],
                                       "make": "Kestrel", "model": "Trek 4", "year": 2019, "trade_value": 24954,
                                       "payoff": 8400, "equity": 16554, "valued_at": "2099-01-01T00:00:00Z"})
    assert (vehicles.valuation_for(car["vin"], car["customer_id"]) or {}).get("valuation_id") != "VAL-TRADE-X"
    sj = {"vin": car["vin"], "customer_id": car["customer_id"]}
    card = estimates.repair_vs_replace(sj, 527.0)
    assert card is None or card["vehicle_value"] != 24954
    assert desk._equity_flag(car["customer_id"], {}, None, car["vin"]) is None or \
        desk._equity_flag(car["customer_id"], {}, None, car["vin"])["equity"] != 16554


def test_prepaid_maintenance_is_covered_only_when_bought(fake_ddb):
    car = next(v for v in ddb.scan_all("DEALER_Vehicles") if v.get("customer_id"))
    assert connectors.check_coverage(car["vin"], "maint")["covered"] is False


# --- warranty on a nearly new car -----------------------------------------------------------

def test_a_brake_job_at_412_miles_is_not_customer_pay():
    car = {"delivered_at": "2026-10-20T09:00:00Z", "odometer": 412, "market_id": "us"}
    cov = estimates.line_coverage(car, "Rear brakes")
    assert cov["pay_type"] == "warranty" and "wear-item adjustment" in cov["rule"]
    old = {"delivered_at": "2023-01-01T09:00:00Z", "odometer": 41000, "market_id": "us"}
    assert estimates.line_coverage(old, "Rear brakes")["pay_type"] == "customer_pay"


# --- the customer can open the inspection evidence --------------------------------------------

def test_the_customer_sees_the_finding_evidence_as_an_image():
    url = service_portal.evidence_card("Rear brakes", "red", 2.9, "mm", "7M7V30T28SR000002")
    assert url.startswith("data:image/svg+xml")
    svg = urllib.parse.unquote(url.split(",", 1)[1])
    assert "REAR BRAKES" in svg and "2.9 mm" in svg and "000002" in svg and "Sample image" in svg
    assert "<script" not in service_portal.evidence_card("<script>x</script>", "red", None, None, "V")
