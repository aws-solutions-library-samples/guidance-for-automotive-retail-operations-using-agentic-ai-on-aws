"""Demo gaps, round 7 — three more classes: screens computing one metric differently, per-browser
state hiding what the record says, and seed values with no plausibility rule; plus the lifecycle
and wording items of the round."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from app.services.common import ddb, labels, vehicles
from app.services.dealer import delivery
from app.services.marketing import audiences
from app.services.oem import campaigns, planner
from app.services.service import booking, metrics
from scripts.seed.generate.vins import looks_like_vin, synthetic_vin
from tests.app.conftest import api_event, call

ROOT = Path(__file__).resolve().parents[2]


# --- one definition per fixed-ops metric -----------------------------------------------------

def test_promise_state_tells_past_from_at_risk_from_on_track():
    now = dt.datetime(2026, 11, 9, 12, 0, tzinfo=dt.UTC)
    past = {"service_journey_id": "p", "stage": "REPAIR", "promised_at": "2026-11-09T10:00:00Z", "projected_at": "2026-11-09T14:00:00Z"}
    risk = {"service_journey_id": "r", "stage": "INSPECT", "promised_at": "2026-11-09T16:00:00Z", "projected_at": "2026-11-09T18:00:00Z"}
    ok = {"service_journey_id": "o", "stage": "INSPECT", "promised_at": "2026-11-09T16:00:00Z", "projected_at": "2026-11-09T15:00:00Z"}
    done = {"service_journey_id": "d", "stage": "READY", "promised_at": "2026-11-09T10:00:00Z", "projected_at": "2026-11-09T14:00:00Z"}
    assert [metrics.promise_state(s, now) for s in (past, risk, ok, done)] == ["past", "at_risk", "on_track", None]
    s = metrics.summary([past, risk, ok, done], [], [], [], now)
    assert (s["past_promise"], s["at_risk"], s["promise_kept_rate"]) == (1, 1, round(1 / 3, 3))


def test_parts_hold_counts_the_flag_or_an_open_order():
    sjs = [{"service_journey_id": "A", "stage": "REPAIR", "parts_hold": True},
           {"service_journey_id": "B", "stage": "INSPECT"}, {"service_journey_id": "C", "stage": "COMPLETED", "parts_hold": True}]
    ros = [{"ro_id": "R1", "service_journey_id": "B"}, {"ro_id": "R2", "service_journey_id": "C"}]
    orders = [{"ro_id": "R1", "status": "backordered"}, {"ro_id": "R2", "status": "backordered"}]
    assert metrics.parts_hold_ids(sjs, ros, orders) == {"A", "B"}          # C is closed


def test_the_three_fixed_ops_screens_carry_the_same_summary(fake_ddb_today):
    from app.demo import invariants
    (r,) = invariants.check(only={"screens-agree-fixed-ops"})
    assert r.ok, r.sample


# --- the survey follows the record ----------------------------------------------------------------

def test_delivery_survey_state_comes_from_the_record(fake_ddb):
    v = next(v for v in ddb.scan_filter("DEALER_Vehicles", "market_id", "us") if v.get("customer_id") and v.get("delivered_at"))
    ddb.put_item("DEALER_Vehicles", {**v, "delivered_at": dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")})
    cid = v["customer_id"]
    code, o = call(api_event("GET", "/garage/ownership", user=f"cust:{cid}", role="customer", customer_id=cid))
    car = next(x for x in o["vehicles"] if x["vin"] == v["vin"])
    assert code == 200 and car["delivery_survey"] == "pending"
    code, _ = call(api_event("POST", "/garage/survey", user=f"cust:{cid}", role="customer", customer_id=cid,
                             body={"rating": 5, "vin": v["vin"]}))
    assert code == 201
    _, o = call(api_event("GET", "/garage/ownership", user=f"cust:{cid}", role="customer", customer_id=cid))
    assert next(x for x in o["vehicles"] if x["vin"] == v["vin"])["delivery_survey"] == "submitted"


# --- lifecycle: the trade reaches used stock; no nudge on a new car ------------------------------

def test_delivery_takes_the_trade_into_used_stock_and_retires_its_valuation(fake_ddb):
    d = next(x for x in ddb.scan_all("DEALER_Deliveries") if x.get("status") != "completed" and x.get("journey_id"))
    jid, cid = d["journey_id"], d["customer_id"]
    j = ddb.get_item("DEALER_Journeys", {"journey_id": jid})
    ddb.put_item("DEALER_Journeys", {**j, "stage": "DELIVER", "context": {**(j.get("context") or {}), "valuation": True}})
    ddb.put_item("DEALER_Valuations", {"valuation_id": "VAL-TEST-1", "customer_id": cid, "dealer_id": j["dealer_id"],
                                       "make": "Kestrel", "model": "Trek 4", "year": 2019, "mileage": 61000,
                                       "trade_value": 24954, "payoff": 8400, "equity": 16554, "currency": "USD",
                                       "valued_at": "2026-11-01T10:00:00Z"})
    out = delivery.mark_delivered(d["delivery_id"], actor="U-US-00009")
    t = out["trade_taken_in"]
    assert t and t["trade_value"] == 24954
    unit = ddb.get_item("DEALER_Inventory", {"vin": t["vin"]})
    assert unit["condition"] == "used" and unit["status"] == "in_stock" and unit["cost"] == 24954
    assert ddb.get_item("DEALER_Valuations", {"valuation_id": "VAL-TEST-1"})["disposition"] == "traded"
    assert any(x["disposition_id"] == t["disposition_id"] and x.get("decided_at") is None
               for x in ddb.scan_all("DEALER_TradeDispositions"))
    # the traded car's equity is no longer the buyer's "upgrade" — her new car is a day old
    assert vehicles.upgrade_window(cid) is None
    code, o = call(api_event("GET", "/garage/ownership", user=f"cust:{cid}", role="customer", customer_id=cid))
    assert code == 200 and o["upgrade_offer"] is None


def test_upgrade_window_rules():
    assert vehicles.UPGRADE_MIN_OWNERSHIP_DAYS >= 365 and vehicles.UPGRADE_LEASE_MONTHS_LEFT <= 12


# --- plausible numbers ---------------------------------------------------------------------------

def test_reach_is_people_not_memberships(fake_ddb):
    out = audiences.build_audiences({"market_id": "us", "is_oem": True, "scope_node_id": "OEM"}, "us")
    assert out["marketable_reach"] <= out["in_any_segment"] <= out["population"]
    assert sum(s["size"] for s in out["segments"]) >= out["in_any_segment"]


def test_budget_shift_is_sized_to_what_the_winner_can_absorb(fake_ddb_today):
    s = campaigns.recommend_budget_shift({"market_id": "us", "is_oem": True}, "us")
    if s.get("recommended"):
        sh = s["shift"]
        best = next(c for c in campaigns.build_campaign_roi({"market_id": "us", "is_oem": True}, "us")["campaigns"]
                    if c["campaign_id"] == sh["to"]["campaign_id"])
        assert sh["expected_extra_deliveries"] <= 0.5 * best["attributed_deliveries"] + 0.05
        assert sh["expected_lift_gross"] > sh["amount"]
        assert best["cost_per_delivery"] >= 200                      # a district flight, not pocket change


def test_scenario_share_moves_with_volume(fake_ddb):
    base = planner.simulate_scenario("DIST-07", {}, "us")
    up = planner.simulate_scenario("DIST-07", {"allocation_pct": 30}, "us")
    vol = {c["metric"]: c for c in up["projections"]}
    share0 = next(c for c in base["projections"] if c["metric"] == "market_share")["projected"]
    assert vol["volume"]["delta"] > 0 and vol["market_share"]["projected"] > share0


# --- seed plausibility ----------------------------------------------------------------------------

def test_vins_read_as_vins():
    v = synthetic_vin("VIN-US-00190", "us", 2024)
    assert looks_like_vin(v) and v.endswith("000190") and v.startswith("7M7")
    assert synthetic_vin("VIN-US-00190", "us", 2024) == v                   # deterministic
    for m in ("us", "de"):
        for row in json.loads((ROOT / "sample-data" / "synthetic" / m / "tables" / "DEALER_Vehicles.json").read_text()):
            assert looks_like_vin(row["vin"]), row["vin"]


def test_seed_rows_pass_the_plausibility_rules():
    for m in ("us", "de"):
        t = ROOT / "sample-data" / "synthetic" / m / "tables"

        def L(n: str, t: Path = t) -> list[dict]:
            return json.loads((t / f"{n}.json").read_text())
        assert all(d["declined_at"] <= "2026-09-10T09:00:00Z" for d in L("DEALER_DeclinedWork"))
        assert all(d["amount"] <= 130 for d in L("DEALER_DeclinedWork") if d["item"] == "Cabin filter")
        assert all(o.get("part_number") and o.get("description") for o in L("DEALER_PartsOrders"))
        journeys = {j["journey_id"] for j in L("DEALER_Journeys")}
        assert all(u.get("reserved_for_journey_id") in journeys for u in L("DEALER_Inventory") if u["status"] == "reserved")
        custs = {c["customer_id"] for c in L("DEALER_Customers")}
        assert all(a["customer_id"] in custs for a in L("DEALER_Appointments") if a.get("kind") != "open_slot")
        assert all(len(x["trim_name"].split()) >= 2 for x in L("OEM_Catalog") if x.get("trim_id") and x.get("kind") != "package")
        names = [c["name"] for c in L("OEM_Campaigns")]
        assert len(set(names)) == len(names) and not any(n.startswith("Seasonal campaign") for n in names)


# --- words --------------------------------------------------------------------------------------

def test_recommendation_and_handoff_labels():
    assert labels.recommendation_label("present:MER-VOLT-C-T1", {"MER-VOLT-C-T1": "Ion C Standard"}) == "suggested presenting the Ion C Standard"
    assert labels.handoff_note_label("service_lane handoff: positive_equity, lease_end") == "from the service lane: positive equity and lease end"
    assert labels.evidence_label("test drive 2026-11-10T16:00:00Z") == "test drive booked"


def test_service_menu_fits_the_powertrain():
    assert "Oil & filter" not in booking.service_menu(True) and "HV battery health check" in booking.service_menu(True)
    assert "Oil & filter" in booking.service_menu(False) and "HV battery health check" not in booking.service_menu(False)
