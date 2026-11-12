"""Fourth round of demo gaps (2026-11-04); each test pins one reported symptom."""
from __future__ import annotations

import datetime as dt

from app.services.agents import prospects
from app.services.common import ddb
from app.services.dealer import inventory

SCOPE = {"user_id": "U-US-00008", "role": "dealer_inventory", "market_id": "us",
         "dealer_ids": {"D-101"}, "is_oem": False}


def _iso(days_ago: int) -> str:
    t = dt.datetime.now(dt.UTC) - dt.timedelta(days=days_ago)
    return t.replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --- item 3: a delivered car leaves inventory ----------------------------------------

def test_a_sold_unit_is_never_shown_as_reserved(fake_ddb):
    unit = next(u for u in ddb.scan_all("DEALER_Inventory") if u.get("dealer_id") == "D-101")
    ddb.put_item("DEALER_Inventory", {**unit, "status": "sold", "reserved_for_journey_id": "J-X",
                                      "sold_to_journey_id": "J-X"})
    row = next(r for r in inventory.list_inventory(SCOPE)["vehicles"] if r["vin"] == unit["vin"])
    assert row["status"] == "sold" and row["reserved_for_journey_id"] is None
    assert row["sold_to_journey_id"] == "J-X"


def test_the_loader_keeps_runtime_sales_sold():
    from app.services.l1_data import ddb_load

    class FakeClient:
        def scan(self, **kw):
            assert kw["TableName"] == "p-DEALER_Vehicles"
            return {"Items": [
                {"vin": {"S": "V1"}, "journey_id": {"S": "J-1"},
                 "source_ref": {"S": "DeliveryBoard.mark_delivered (DLV-1; inventory V1 → VIN master)"}},
                {"vin": {"S": "V2"}, "source_ref": {"S": "seed"}}]}

    pack = [{"vin": "V1", "status": "in_stock", "reserved_for_journey_id": "J-1"},
            {"vin": "V2", "status": "in_stock"}]
    out = {r["vin"]: r for r in ddb_load._keep_runtime_sales(FakeClient(), "p-", pack)}
    assert out["V1"]["status"] == "sold" and out["V1"]["sold_to_journey_id"] == "J-1"
    assert "reserved_for_journey_id" not in out["V1"]
    assert out["V2"]["status"] == "in_stock"


def test_mark_delivered_records_the_vin_on_the_delivery(fake_ddb):
    from app.services.dealer import delivery
    unit = next(u for u in ddb.scan_all("DEALER_Inventory")
                if u.get("dealer_id") == "D-101" and u.get("status") == "in_stock")
    d = next(x for x in ddb.scan_all("DEALER_Deliveries") if x.get("status") != "completed")
    ddb.put_item("DEALER_Deliveries", {**d, "vin": None})
    ddb.put_item("DEALER_Inventory", {**unit, "reserved_for_journey_id": d["journey_id"]})
    delivery.mark_delivered(d["delivery_id"])
    after = ddb.get_item("DEALER_Deliveries", {"delivery_id": d["delivery_id"]})
    assert after["status"] == "completed" and after["vin"] == unit["vin"]
    assert ddb.get_item("DEALER_Inventory", {"vin": unit["vin"]})["status"] == "sold"


# --- item 4: the acquisition scout never offers to buy back a fresh sale ---------------

def test_a_car_the_store_just_delivered_is_not_an_acquisition(fake_ddb):
    assert prospects.just_sold({"delivered_at": _iso(1), "odometer": 412, "deal_id": "DEAL-1"})
    assert prospects.just_sold({"odometer": 418, "deal_id": "DEAL-2"})
    assert prospects.just_sold({"delivered_at": _iso(900), "odometer": 41000}) is None
    car = next(v for v in ddb.scan_all("DEALER_Vehicles") if v.get("customer_id"))
    ddb.put_item("DEALER_Vehicles", {**car, "delivered_at": _iso(1), "odometer": 412})
    rec = prospects.recommend_acquisition(car["vin"])
    assert rec["recommend_acquire"] is False and rec["skipped"]
    assert car["vin"] not in {p["vin"] for p in prospects.find_upgrade_prospects()["prospects"]}


def test_the_offer_is_anchored_on_this_cars_value(fake_ddb):
    car = next(v for v in ddb.scan_all("DEALER_Vehicles")
               if v.get("customer_id") and not prospects.just_sold(v))
    ddb.put_item("DEALER_Valuations", {"valuation_id": "VAL-OTHER", "customer_id": car["customer_id"],
                                       "make": "Other", "model": "Car", "trade_value": 99999,
                                       "valued_at": "2099-01-01T00:00:00Z"})
    rec = prospects.recommend_acquisition(car["vin"])
    assert not rec.get("offer_range") or (rec["offer_range"]["high"] or 0) < 99999


# --- item 5: a predictive service lead is booked as service, never sold to ----------

def _predictive_lead():
    from app.services.service import predictive
    pred = next(p for p in predictive.predictions({"D-101"}, "us")["predictions"] if p.get("customer_id"))
    lead = predictive.raise_lead(pred, "U-US-00005")["lead"]
    # the shape the scan writes when more parts come due: records, not names
    lead = {**lead, "predicted_also_due": [{"part": "Cabin air filter", "miles_remaining": 267, "offer_pct": 20}]}
    ddb.put_item("DEALER_Leads", lead)
    return lead


def test_assigning_a_service_lead_opens_no_sales_journey(fake_ddb):
    from app.services.dealer import leads
    lead = _predictive_lead()
    before = len(ddb.scan_all("DEALER_Journeys"))
    out = leads.assign_lead(lead["lead_id"], "U-US-00007", actor="U-US-00005")
    assert out["service_lead"] and out["journey_id"] is None and out["task_id"]
    assert len(ddb.scan_all("DEALER_Journeys")) == before
    assert ddb.get_item("DEALER_Leads", {"lead_id": lead["lead_id"]})["assigned_user_id"] == "U-US-00007"


def test_booking_a_service_lead_books_a_service_visit_not_a_test_drive(fake_ddb):
    import json as _json

    from app.services.api import handler
    from tests.app.conftest import api_event
    lead = _predictive_lead()
    ev = api_event("POST", f"/leads/{lead['lead_id']}/appointment", user="U-US-00005", role="dealer_bdc",
                   dealer_ids="D-101", body={})
    res = handler.lambda_handler(ev, None)
    out = _json.loads(res["body"])
    assert res["statusCode"] == 200, out
    assert out["type"] == "service" and out["service_journey_id"].startswith("SJ-")
    sj = ddb.get_item("DEALER_ServiceJourneys", {"service_journey_id": out["service_journey_id"]})
    assert sj["vin"] == lead["predicted_vin"] and lead["predicted_part"] in sj["services"]
    assert all(isinstance(x, str) and "{" not in x for x in sj["services"]), sj["services"]
    appts = [a for a in ddb.scan_all("DEALER_Appointments") if a.get("appointment_id") == out["appointment_id"]]
    assert appts and appts[0]["type"] == "service"
    from app.services.dealer import leads
    desk = leads.build_lead_desk({"dealer_ids": {"D-101"}, "market_id": "us"})
    card = next(c for cs in desk["queue"].values() for c in cs if c["lead_id"] == lead["lead_id"])
    assert card["service_lead"] and card["service_appointment"]["service_journey_id"] == out["service_journey_id"]
    assert card["journey_id"] is None


# --- item 6: a new action-plan draft is the newest version and can be opened ----------

def test_a_new_plan_draft_is_the_latest_version(fake_ddb):
    from app.services.oem import account_plan, workspace
    acct = next(a for a in ddb.scan_all("OEM_Accounts")
                if a.get("dealer_id") and ddb.scan_filter("OEM_AccountPlans", "dealer_id", a["dealer_id"]))
    prior = ddb.scan_filter("OEM_AccountPlans", "dealer_id", acct["dealer_id"])
    out = account_plan.draft_account_plan(acct["account_id"], author="U-US-00044")
    assert out["version"] == max(int(p.get("version") or 0) for p in prior) + 1
    plans = workspace.account_detail(acct["account_id"], {"is_oem": True, "market_id": "us"},
                                     acct.get("market_id") or "us")["plans"]
    assert plans[0]["plan_id"] == out["plan_id"] and plans[0]["status"] == "draft"
    assert plans[0]["dated_at"]                               # never "draft · —"


# --- item 7: campaign ROI is on money spent, and a shift returns more than it moves ---

def test_roi_charges_only_the_spend_to_date():
    from app.services.oem import campaigns
    now = dt.datetime(2026, 11, 1, tzinfo=dt.UTC)
    c = {"budget": 10000, "start_at": "2026-10-01T00:00:00Z", "end_at": "2026-12-01T00:00:00Z"}
    assert 4000 < campaigns.spend_to_date(c, now) < 6000              # about halfway through the flight
    assert campaigns.spend_to_date({**c, "start_at": "2026-11-15T00:00:00Z"}, now) == 0   # not started
    assert campaigns.spend_to_date({**c, "end_at": "2026-10-15T00:00:00Z"}, now) == 10000  # flight over


def test_campaign_roi_is_not_negative_everywhere_and_the_shift_pays(fake_ddb):
    from app.services.oem import campaigns
    for m in ("us", "de"):
        scope = {"market_id": m, "is_oem": True, "scope_node_id": "OEM", "role": "oem_marketing"}
        rows = campaigns.build_campaign_roi(scope, m)["campaigns"]
        assert any((r["roi"] or 0) > 0 for r in rows), m
        assert all(r["currency"] == ("EUR" if m == "de" else "USD") for r in rows)
        shift = campaigns.recommend_budget_shift(scope, m)
        assert shift["recommended"], shift.get("note")
        s = shift["shift"]
        assert s["expected_lift_gross"] > s["amount"] and s["expected_net_gain"] > 0


def test_no_shift_into_a_campaign_that_loses_money():
    from app.services.oem import campaigns
    rows = [{"campaign_id": "A", "roi": -0.04, "budget": 1000, "cost_per_delivery": 3100},
            {"campaign_id": "B", "roi": -0.5, "budget": 1000, "cost_per_delivery": 6000}]
    assert campaigns._rank_shift(rows, "budget") is None


# --- item 8: the approvals inbox is per market ---------------------------------------

def test_us_finance_never_sees_german_approvals(fake_ddb):
    from app.services.approvals import generic
    ddb.put_item("DEALER_Approvals", {"approval_id": "APR-DE-T1", "action": "budget_shift", "status": "pending",
                                      "requested_by": "A5", "requested_at": "2026-11-01T00:00:00Z"})
    ddb.put_item("DEALER_Approvals", {"approval_id": "APR-REC-BUDGETSHIFT-CMP-DE-00006-to-CMP-DE-00005",
                                      "type": "budget_shift", "status": "pending", "requested_by": "U-DE-X",
                                      "scope": {"node": "OEM", "market_id": "de"}})
    ddb.put_item("DEALER_Approvals", {"approval_id": "APR-US-T1", "action": "budget_shift", "status": "pending",
                                      "requested_by": "A5", "market_id": "us"})
    us = {"user_id": "U-US-00048", "role": "oem_finance", "market_id": "us", "is_oem": True, "dealer_ids": set()}
    ids = {r["approval_id"] for r in generic.inbox(us)["approvals"]}
    assert "APR-US-T1" in ids and not any("-DE-" in i for i in ids)
    de = {**us, "user_id": "U-DE-00048", "market_id": "de"}
    assert "APR-DE-T1" in {r["approval_id"] for r in generic.inbox(de)["approvals"]}
    import pytest
    with pytest.raises(generic.Forbidden):
        generic.decide("APR-DE-T1", "approve", us)


# --- item 9: words, not codes ---------------------------------------------------------

def test_a_blocked_handoff_reads_as_words():
    from app.services.common import labels
    from app.services.journeys import supervisor
    msg = str(supervisor.HandoffIncomplete("DISCOVER", ["shortlist"]))
    assert "['" not in msg and "shortlist of vehicles" in msg and "Discover" in msg
    assert labels.stage_label("TRADE_IN") == "Trade-in"
    assert labels.humanize("partially_approved") == "Partially approved"


def test_people_are_named_not_ided(fake_ddb):
    from app.services.approvals import generic
    from app.services.platform import tasks
    ddb.put_item("DEALER_Approvals", {"approval_id": "APR-US-NM", "action": "goodwill", "status": "pending",
                                      "requested_by": "U-US-00007", "dealer_id": "D-101", "market_id": "us"})
    mgr = {"user_id": "U-US-00056", "role": "dealer_service_manager", "market_id": "us", "dealer_ids": {"D-101"}}
    row = next(r for r in generic.inbox(mgr)["approvals"] if r["approval_id"] == "APR-US-NM")
    assert row["requested_by_name"] and not row["requested_by_name"].startswith("U-")
    made = tasks.create_task("Call the customer", "U-US-00007", created_by="U-US-00056", dealer_id="D-101")
    t = next(x for x in tasks.list_tasks(dealer_ids={"D-101"})["tasks"] if x["task_id"] == made["task_id"])
    assert t["assigned_user_name"] and not t["assigned_user_name"].startswith("U-")


# --- item 10: the same car is worth the same every run ---------------------------------

def test_revaluing_the_same_car_never_drifts(fake_ddb):
    from app.services.agents import equity, trade
    cid = next(v["customer_id"] for v in ddb.scan_all("DEALER_Valuations") if v.get("customer_id"))
    runs = [trade.get_trade_valuation(cid, mileage=60000, condition="fair", persist=True)["trade_value"]
            for _ in range(4)]
    assert len(set(runs)) == 1, runs                         # persisting a re-valuation never compounds
    assert trade.get_trade_valuation(cid, mileage=60000, condition="fair")["trade_value"] == runs[0]
    assert trade.get_trade_valuation(cid)["trade_value"] == runs[0]   # the recorded value stands
    a = equity.refresh_valuation(cid, condition="good")["new_trade_value"]
    b = equity.refresh_valuation(cid, condition="good")["new_trade_value"]
    assert a == b


def test_comparables_ignore_derived_rows():
    from app.services.agents import equity
    assert equity._is_derived({"baseline_valuation_id": "VAL-1"})
    assert equity._is_derived({"source_ref": "sandbox valuation connector: median of 3"})
    assert not equity._is_derived({"source_ref": "guidebook"})


# --- items 11-13: one alert per store+condition; one dealer count; gap-based risk -----

def test_one_open_alert_per_store_and_condition(fake_ddb):
    from collections import Counter
    for m in ("us", "de"):
        c = Counter((a["dealer_id"], a["kind"]) for a in ddb.scan_filter("OEM_Alerts", "market_id", m)
                    if a.get("status") == "open")
        assert c and max(c.values()) == 1, m


def test_today_and_accounts_agree_on_dealers_and_risk(fake_ddb):
    from app.services.oem import today, workspace
    scope = {"is_oem": True, "market_id": "us", "scope_node_id": "OEM", "role": "oem_dm"}
    t = today.build_today(scope, "us")
    acc = workspace.list_accounts(scope, "us")
    assert t["kpis"]["dealers"] == acc["dealers"] == sum(1 for a in acc["accounts"] if a["account_type"] == "dealer")
    assert t["kpis"]["dealers_at_risk"] == acc["at_risk"]
    for a in acc["accounts"]:
        if a["account_type"] != "dealer" or a["target_gap"] is None:
            continue
        if a["target_gap"] >= 0 and (a["sales_health_index"] or 1) >= 0.6:
            assert not a["at_risk"], a                      # above its peers is never "at risk"
        if a["target_gap"] <= workspace.AT_RISK_GAP:
            assert a["at_risk"], a                          # 5+ pts below peers is never "on track"


# --- item 14: no channel may send more than the weekly cap ------------------------------

def test_a_channel_cap_above_the_weekly_cap_is_refused():
    from app.services.marketing import journey_ops
    errs = journey_ops.validate({"cadence_caps": {"per_day": 1, "per_week": 3,
                                                  "per_channel_week": {"email": 7, "sms": 1}},
                                 "policy": {"stop_conditions": ["opt_out"]}, "templates": []})
    assert any("per_channel_week.email" in e and "per_week" in e for e in errs)


def test_nurture_plans_stay_within_the_weekly_cap(fake_ddb):
    from app.services.journeys import nurture
    assert nurture._cadence_days("weekly") == 7 and nurture._cadence_days("daily") == 1
    for p in ddb.scan_all("DEALER_NurturePlans"):
        if str(p.get("source_ref", "")).startswith("synthetic"):
            assert 7 / nurture._cadence_days(p["cadence"]) <= 3, p["cadence"]
    assert nurture._cadence_for({"idle_days": 0}) == "every_3_days"


# --- item 15: not every customer has opted in ---------------------------------------------

def test_the_largest_segment_is_not_all_consented(fake_ddb):
    from app.services.marketing import audiences
    scope = {"is_oem": True, "market_id": "us", "scope_node_id": "OEM", "role": "oem_marketing"}
    segs = audiences.build_audiences(scope, "us")["segments"]
    big = max(segs, key=lambda s: s["size"])
    assert big["size"] and 0 < big["marketable"] < big["size"]


# --- item 16: the marketing landing reads each table once and asks the model once -------

def test_campaign_overview_scans_each_table_once_and_calls_the_model_once(fake_ddb, monkeypatch):
    from collections import Counter

    from app.services.marketing import analytics
    from app.services.oem import campaigns
    scans = Counter()
    real_client = ddb._client()
    class Counting:
        def __getattr__(self, n):
            return getattr(real_client, n)
        def scan(self, **kw):
            scans[kw["TableName"]] += 1
            return real_client.scan(**kw)
    monkeypatch.setattr(ddb, "_client", lambda: Counting())
    asked = []
    monkeypatch.setattr(campaigns, "_insight", lambda r, **k: (asked.append(1) or ("x", "fallback")))
    out = analytics.campaign_overview({"market_id": "us", "is_oem": True, "scope_node_id": "OEM",
                                       "role": "oem_marketing"}, "us")
    assert out["roi"]["campaigns"] and len(asked) == 1
    assert max(scans.values()) <= 2, scans                   # was 7 journeys / 5 deliveries / 6 touchpoints


def test_the_read_memo_never_leaks_mutations_or_stale_writes(fake_ddb):
    with ddb.read_memo():
        a = ddb.scan_all("OEM_Campaigns")
        a[0]["name"] = "changed"
        assert ddb.scan_all("OEM_Campaigns")[0]["name"] != "changed"
        row = dict(ddb.scan_all("OEM_Campaigns")[0], name="written")
        ddb.put_item("OEM_Campaigns", row)
        assert any(r["name"] == "written" for r in ddb.scan_all("OEM_Campaigns"))


# --- item 18: a car finished on an earlier day leaves the technician's list --------------

def test_yesterdays_ready_car_leaves_the_technicians_list(fake_ddb):
    from app.services.service import desk
    tech = {"user_id": "U-US-00053", "role": "dealer_technician", "market_id": "us", "dealer_ids": {"D-101"}}
    base = next(s for s in ddb.scan_all("DEALER_ServiceJourneys") if s.get("dealer_id") == "D-101")
    ddb.put_item("DEALER_ServiceJourneys", {**base, "service_journey_id": "SJ-T-OLD", "stage": "READY",
                                            "technician_id": "U-US-00053", "updated_at": _iso(2)})
    ddb.put_item("DEALER_ServiceJourneys", {**base, "service_journey_id": "SJ-T-NOW", "stage": "READY",
                                            "technician_id": "U-US-00053", "updated_at": _iso(0)})
    ddb.put_item("DEALER_ServiceJourneys", {**base, "service_journey_id": "SJ-T-BAY", "stage": "REPAIR",
                                            "technician_id": "U-US-00053", "updated_at": _iso(5)})
    ids = {c["service_journey_id"] for c in desk.list_journeys(tech, {"unassigned": "1"})["cards"]}
    assert {"SJ-T-NOW", "SJ-T-BAY"} <= ids and "SJ-T-OLD" not in ids
    card = next(c for c in desk.list_journeys(tech, {"unassigned": "1"})["cards"] if c["service_journey_id"] == "SJ-T-BAY")
    assert card["vehicle_name"] and "-" not in card["vehicle_name"].split()[0]


def test_the_roi_insight_is_asked_once_per_set_of_figures(fake_ddb, monkeypatch):
    from app.services.oem import campaigns
    monkeypatch.setattr(campaigns._gen, "enabled", lambda: True)
    calls = []

    class Rt:
        def converse(self, **kw):
            calls.append(1)
            return {"output": {"message": {"content": [{"text": "Campaign 4 leads; campaign 5 trails."}]}},
                    "stopReason": "end_turn"}
    import boto3
    monkeypatch.setattr(boto3, "client", lambda *a, **k: Rt())
    scope = {"market_id": "us", "is_oem": True, "scope_node_id": "OEM", "role": "oem_marketing"}
    a = campaigns.build_campaign_roi(scope, "us")
    b = campaigns.build_campaign_roi(scope, "us")
    assert len(calls) == 1 and a["ai_insight"] == b["ai_insight"] and b["insight_by"] == "bedrock"


# --- walkthrough warnings: repurchase handoff and the predictive heads-up -----------------

def test_a_lane_handoff_goes_to_a_floor_advisor_never_an_agent(fake_ddb):
    from app.services.service import supervisor
    sj = next(s for s in ddb.scan_all("DEALER_ServiceJourneys") if s.get("dealer_id") == "D-101" and s.get("customer_id"))
    ddb.put_item("DEALER_ServiceJourneys", {**sj, "advisor_id": None, "purchase_journey_id": None})
    cust = ddb.get_item("DEALER_Customers", {"customer_id": sj["customer_id"]})
    ddb.put_item("DEALER_Customers", {**cust, "assigned_advisor_user_id": None})
    out = supervisor.handoff_to_sales(sj["service_journey_id"], "positive_equity", actor="U-US-00007")
    h = ddb.get_item("DEALER_Handoffs", {"handoff_id": out["handoff_id"]})
    u = ddb.get_item("PLAT_Users", {"user_id": h["to_user"]})
    assert u and "dealer_advisor" in u["groups"] and u.get("dealer_id") == "D-101", h["to_user"]


def test_the_customers_own_advisor_receives_the_handoff(fake_ddb):
    from app.services.service import supervisor
    sj = next(s for s in ddb.scan_all("DEALER_ServiceJourneys") if s.get("dealer_id") == "D-101" and s.get("customer_id"))
    cust = ddb.get_item("DEALER_Customers", {"customer_id": sj["customer_id"]})
    ddb.put_item("DEALER_Customers", {**cust, "assigned_advisor_user_id": "U-US-00002"})
    for j in ddb.scan_filter("DEALER_Journeys", "customer_id", sj["customer_id"]):
        ddb.put_item("DEALER_Journeys", {**j, "status": "closed"})
    out = supervisor.handoff_to_sales(sj["service_journey_id"], "positive_equity", actor="U-US-00007")
    assert ddb.get_item("DEALER_Handoffs", {"handoff_id": out["handoff_id"]})["to_user"] == "U-US-00002"


def test_the_customers_own_booking_answers_the_heads_up_and_the_desk_never_moves_it(fake_ddb):
    from app.services.customer import service_portal
    from app.services.dealer import leads
    lead = _predictive_lead()
    vin, cid = lead["predicted_vin"], lead["customer_id"]
    mine = service_portal.book(cid, vin, services=["Brake inspection"], wait_type="drop",
                               slot_start="2026-11-09T15:00:00Z")
    assert mine["offer_applied_pct"] == lead["offer_pct"]
    after = ddb.get_item("DEALER_Leads", {"lead_id": lead["lead_id"]})
    assert after["service_appointment"]["booked_by"] == "customer"
    out = leads.book_service_for_lead(lead["lead_id"], "U-US-00005")           # the BDC clicks Book
    assert out["already_booked"] and out["slot_start"] == "2026-11-09T15:00:00Z"
    sj = ddb.get_item("DEALER_ServiceJourneys", {"service_journey_id": mine["service_journey_id"]})
    assert sj["promised_at"] == "2026-11-09T15:00:00Z" and "Brake inspection" in sj["services"]
    assert lead["predicted_part"] in sj["services"]                            # the predicted work rides along
    from app.services.service import predictive
    offer = predictive.customer_offers(cid)[0]
    assert offer["booked"]["slot_start"] == "2026-11-09T15:00:00Z"


def test_a_booking_without_a_time_keeps_the_visits_booked_time(fake_ddb):
    from app.services.service import booking
    vin = next(v["vin"] for v in ddb.scan_all("DEALER_Vehicles") if v.get("customer_id") and v.get("dealer_id") == "D-101")
    first = booking.book_service(vin, services=["Oil & filter"], preferred_slot="2026-11-20T15:00:00Z")
    again = booking.book_service(vin, services=["Tire rotation"])
    assert again["service_journey_id"] == first["service_journey_id"] and again["slot_start"] == "2026-11-20T15:00:00Z"
