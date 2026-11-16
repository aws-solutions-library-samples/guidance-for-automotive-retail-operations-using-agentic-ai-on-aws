"""Demo gaps closed on 2026-11-04 (each test pins one reported symptom).

* delivery: a seeded delivery-week deal is booked against a reserved unit, so
  "Mark delivered" records the car in the customer's garage
* book service: an advisor finds the car by owner name or model, not only by VIN
* parts-wait: the Service Cockpit tile, the board's parts_hold filter and the
  insight count the same journeys; hours are to the latest ETA
* stale insight: a written insight stating numbers the figures no longer show is
  not served (the page falls back to the live figures-only text)
* objective: the scenario baseline is the Today view's objective attainment
* inspections: re-inspecting an item replaces its row (no "Rear brakes" twice);
  seeded readings fit the car in the bay (no engine oil on an EV, nothing worn on
  a car far under the item's wear interval)
"""
from __future__ import annotations

import json
from pathlib import Path

from app.infra import cognito_setup
from app.services.api import handler
from app.services.common import ddb
from app.services.dealer import delivery, store_digest
from app.services.oem import planner, today
from app.services.service import desk, inspection
from tests.app.conftest import api_event

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "sample-data" / "synthetic" / "us" / "tables"


def _rows(table: str) -> list[dict]:
    return json.loads((SEED / f"{table}.json").read_text())


# --- sign-in -------------------------------------------------------------------------

def test_every_seed_user_is_in_its_cognito_group():
    groups = {r["user_id"]: r.get("groups") or [] for r in _rows("PLAT_Users")}
    assert [(u, role) for u, role in cognito_setup.SEED_USERS if role not in groups.get(u, [])] == []


# --- delivery → ownership ----------------------------------------------------------

def test_every_upcoming_delivery_is_booked_against_a_reserved_unit():
    inv = {v["vin"]: v for v in _rows("DEALER_Inventory")}
    deals = {d["deal_id"]: d for d in _rows("DEALER_Deals")}
    upcoming = [d for d in _rows("DEALER_Deliveries") if d.get("status") == "scheduled"]
    assert upcoming
    for d in upcoming:
        unit = inv[d["vin"]]
        assert unit["status"] == "reserved" and unit["reserved_for_journey_id"] == d["journey_id"]
        assert unit["model_id"] == d["vehicle_model_id"] and deals[d["deal_id"]]["vin"] == d["vin"]


def test_mark_delivered_puts_the_car_in_the_customers_garage(fake_ddb):
    j = ddb.get_item("DEALER_Journeys", {"journey_id": "J-US-J5"})
    vin = next(d["vin"] for d in ddb.scan_filter("DEALER_Deliveries", "journey_id", "J-US-J5"))
    delivery.update_delivery("J-US-J5", {"checklist": {k: True for k in delivery.PREP_ITEMS},
                                         "charger_install": "installed", "docs_signed": True}, "U-US-00009")
    out = delivery.deliver_journey("J-US-J5", "U-US-00009")
    assert out["journey_advance"]["to"] == "OWN"
    car = ddb.get_item("DEALER_Vehicles", {"vin": vin})
    assert car and car["customer_id"] == j["customer_id"] and car["delivered_at"]
    assert ddb.get_item("DEALER_Inventory", {"vin": vin})["status"] == "sold"


# --- book service: vehicle lookup ----------------------------------------------------

def _search(q: str, role: str = "dealer_service", user: str = "U-US-00007") -> list[dict]:
    ev = api_event("GET", "/search", user=user, role=role, dealer_ids="D-101", query={"q": q, "types": "vin"})
    out = handler.lambda_handler(ev, None)
    assert out["statusCode"] == 200
    return json.loads(out["body"])["results"]


def test_book_service_finds_the_car_by_owner_name_or_model(fake_ddb):
    by_name = _search("Kowalski")
    assert [r["id"] for r in by_name] == ["7M7V30T28SR000002"]
    assert by_name[0]["detail"] == "2023 Meridian Ion C · Sam Kowalski"      # no raw model code
    assert "7M7V30T28SR000002" in [r["id"] for r in _search("Ion C")]


def test_a_masked_role_finds_by_vin_but_never_by_or_with_the_owner_name(fake_ddb):
    assert _search("Kowalski", role="dealer_technician", user="U-US-00053") == []
    hit = _search("7M7V30T28SR000002", role="dealer_technician", user="U-US-00053")
    assert hit and "Kowalski" not in hit[0]["detail"]


# --- parts-wait: one definition --------------------------------------------------------

def test_cockpit_tile_board_filter_and_insight_count_the_same_journeys(fake_ddb):
    claims = {"user_id": "U-US-00056", "role": "dealer_service_manager", "market_id": "us",
              "dealer_ids": {"D-101"}, "is_oem": False}
    view = desk.cockpit(claims, "us")
    # "held for parts" has ONE definition (service/metrics.parts_hold_ids): the visit's flag or an open
    # held order on its RO — the parts desk, the board badge and this tile all read it
    from app.services.service import metrics
    sjs = [s for s in ddb.scan_all("DEALER_ServiceJourneys") if s.get("dealer_id") == "D-101"]
    held = metrics.parts_hold_ids(sjs, ddb.scan_all("DEALER_RepairOrders"), ddb.scan_all("DEALER_PartsOrders"))
    assert view["tiles"]["parts_wait_ros"] == len(held)
    board = desk.list_journeys(claims)
    assert board["kpis"]["parts_hold"] == len(held) == board["kpis"]["shared_metrics"]["parts_hold"]
    text = view["insight"]["text"]
    assert ("held for parts" in text) == bool(held)


def test_seed_parts_hold_follows_the_orders():
    ros = {r["ro_id"]: r for r in _rows("DEALER_RepairOrders")}
    sjs = {s["service_journey_id"]: s for s in _rows("DEALER_ServiceJourneys")}
    held_sjs = {ros[o["ro_id"]]["service_journey_id"] for o in _rows("DEALER_PartsOrders")
                if o["status"] in ("ordered", "backordered", "in_transit")}
    for sid, s in sjs.items():
        assert bool(s.get("parts_hold")) == (s["stage"] == "REPAIR" and sid in held_sjs), sid
        if s["stage"] in ("READY", "COMPLETED", "FOLLOW_UP"):
            assert sid not in held_sjs, sid   # a car past repair has its parts


# --- stale written insight ---------------------------------------------------------------

def test_a_written_insight_whose_numbers_moved_is_not_served(fake_ddb):
    rid = store_digest.rec_id("us", "D-101", store_digest.SERVICE_SURFACE)
    now = store_digest._now_dt()
    ddb.put_item("DEALER_Recommendations", {
        "recommendation_id": rid, "agent_id": "A7", "market_id": "us", "dealer_id": "D-101",
        "text": "2 ROs are held for parts at 190.3 hours to the latest ETA.", "sources": [],
        "generated_at": store_digest._iso(now), "checked_at": store_digest._iso(now)})
    now_facts = ["parts wait ros: 2", "parts wait hours: 0.0", "Figures-only summary: 2 ROs are held (0.0 hours)."]
    assert store_digest.stored_insight("us", "D-101", store_digest.SERVICE_SURFACE, facts=now_facts) is None
    same = ["parts wait ros: 2", "parts wait hours: 190.3"]
    assert store_digest.stored_insight("us", "D-101", store_digest.SERVICE_SURFACE, facts=same)["written_by"] == "A7"


def test_figures_agree_reads_percent_and_money_forms():
    assert store_digest.figures_agree("sales health is 78%", ["avg sales health: 0.78"])
    assert store_digest.figures_agree("about $1,963 pending", ["pending estimate value: 1963.0"])
    assert not store_digest.figures_agree("over 2 decided estimates", ["decided estimates: 3"])


# --- objective -----------------------------------------------------------------------------

def test_scenario_objective_baseline_is_the_today_objective(fake_ddb):
    s = planner.simulate_scenario("DIST-07", {}, "us")
    base = next(p for p in s["projections"] if p["metric"] == "objective_pct")["baseline"]
    assert base == today._kpis(planner._accounts("us", "DIST-07", None))["avg_sales_health"]
    lifted = planner.simulate_scenario("DIST-07", {"allocation_pct": 20}, "us")
    assert next(p for p in lifted["projections"] if p["metric"] == "objective_pct")["projected"] > base


# --- inspections -------------------------------------------------------------------------

def test_reinspecting_an_item_replaces_its_row(fake_ddb):
    sj = "SJ-US-00003"
    before = [r["item"] for r in ddb.scan_filter("DEALER_Inspections", "service_journey_id", sj)]
    inspection.record_inspection(sj, [{"item": "Rear brakes", "status": "red", "measurement": 2.9, "unit": "mm"},
                                      {"item": "Cabin filter", "status": "red"},
                                      {"item": "Charge port", "status": "green"}])
    items = [r["item"] for r in ddb.scan_filter("DEALER_Inspections", "service_journey_id", sj)]
    assert sorted(items) == sorted(set(items))                       # each item once
    assert set(items) == set(before) | {"Charge port"}


def test_reinspecting_never_overwrites_a_different_items_row(fake_ddb):
    sj = "SJ-US-00003"
    inspection.record_inspection(sj, [{"item": "Charge port", "status": "green"}])
    inspection.record_inspection(sj, [{"item": "Alignment", "status": "yellow"}])
    items = [r["item"] for r in ddb.scan_filter("DEALER_Inspections", "service_journey_id", sj)]
    assert "Charge port" in items and "Alignment" in items and sorted(items) == sorted(set(items))


def test_seeded_readings_fit_the_car_in_the_bay():
    models = {m["model_id"]: m for m in _rows("OEM_CatalogModels")}
    cars = {v["vin"]: v for v in _rows("DEALER_Vehicles")}
    sjs = {s["service_journey_id"]: s for s in _rows("DEALER_ServiceJourneys")}
    wear = {"Front brakes": 40000, "Rear brakes": 50000, "Tires": 40000, "Cabin filter": 20000}
    for i in _rows("DEALER_Inspections"):
        car = cars.get(sjs[i["service_journey_id"]]["vin"]) or {}
        if (models.get(car.get("model_id")) or {}).get("powertrains") == ["EV"]:
            assert i["item"] != "Engine oil", i["inspection_id"]
        odo = car.get("odometer")
        if odo is not None and i["item"] in wear and odo < wear[i["item"]] * 0.5:
            assert i["status"] == "green", (i["inspection_id"], i["item"], odo)


def test_storyline_owner_keeps_the_storylines_car():
    car = next(v for v in _rows("DEALER_Vehicles") if v["customer_id"] == "C-US-J6")
    assert (car["odometer"], car["year"], car["ownership"]) == (41000, 2023, "lease")
