"""Inventory & Trade Desk, Delivery Board, Store Cockpit, store campaigns."""
from __future__ import annotations

import pytest

from app.services.dealer import cockpit
from app.services.events import bus
from tests.app.conftest import api_event, call

MGR = dict(user="U-US-00001", role="dealer_manager", dealer_ids="D-101")
INV = dict(user="U-US-00008", role="dealer_inventory", dealer_ids="D-101")
DLV = dict(user="U-US-00009", role="dealer_delivery", dealer_ids="D-101")


@pytest.fixture(autouse=True)
def _fresh_cockpit():
    cockpit._CACHE.clear()


def _types():
    return [e["type"].split("@")[0] for e in bus.EMITTED]


# --- inventory -----------------------------------------------------------------

def test_inventory_grid_has_comps_and_demand(fake_ddb):
    code, inv = call(api_event("GET", "/inventory", **INV))
    assert code == 200 and inv["count"] > 0
    v = inv["vehicles"][0]
    assert v["dealer_id"] == "D-101" and v["market_price"] is not None and v["comps_n"] >= 1
    assert isinstance(v["demand_score"], int) and "comps=median" in v["source_ref"]


def test_reserve_and_unreserve_emit_inventory_changed(fake_ddb):
    vin = next(r["vin"] for r in fake_ddb.rows("DEALER_Inventory") if r["dealer_id"] == "D-101" and r["status"] == "in_stock")
    bus.EMITTED.clear()
    code, out = call(api_event("POST", f"/inventory/{vin}/reserve", **INV, body={"journey_id": "J-US-J1"}))
    assert code == 200 and out["status"] == "reserved"
    code, _ = call(api_event("POST", f"/inventory/{vin}/reserve", **INV, body={"journey_id": "J-US-J4"}))   # another D-101 journey
    assert code == 409                                          # already held for another journey
    code, out = call(api_event("POST", f"/inventory/{vin}/reserve", **INV, body={"reserve": False}))
    assert code == 200 and out["status"] == "in_stock"
    assert _types().count("inventory.changed") == 2
    code, _ = call(api_event("POST", f"/inventory/{vin}/reserve", user="U-US-00007", role="dealer_service",
                             dealer_ids="D-101", body={"journey_id": "J-US-J1"}))
    assert code == 403


def test_trade_disposition_recommend_and_decide(fake_ddb):
    fake_ddb.seed("DEALER_TradeDispositions", [{
        "trade_disposition_id": "TD-T1", "disposition_id": "TD-T1", "customer_id": "C-US-J1", "dealer_id": "D-101",
        "acquired_at": "2026-09-30T09:00:00Z", "currency": "USD", "recon_cost": 400, "source_ref": "test"}])
    code, q = call(api_event("GET", "/trades/dispositions", **INV))
    row = next(r for r in q["dispositions"] if r["disposition_id"] == "TD-T1")
    assert row["status"] == "pending" and row["recommendation"] in ("keep_cpo", "retail", "wholesale", "auction")
    assert row["reasoning"] and row["trade_value"] is not None
    code, _ = call(api_event("POST", "/trades/TD-T1/decide", **INV, body={"decision": "scrap"}))
    assert code == 400
    code, out = call(api_event("POST", "/trades/TD-T1/decide", **INV, body={"decision": "wholesale", "reason": "thin demand"}))
    assert code == 200 and out["decision"] == "wholesale"
    code, _ = call(api_event("POST", "/trades/TD-T1/decide", **INV, body={"decision": "auction"}))
    assert code == 409


# --- delivery ------------------------------------------------------------------

def _deliver_journey(fake_ddb):
    """A D-101 journey moved to DELIVER with no delivery row yet (the board synthesizes its card)."""
    with_delivery = {d["journey_id"] for d in fake_ddb.rows("DEALER_Deliveries")}
    j = next(r for r in fake_ddb.rows("DEALER_Journeys")
             if r["dealer_id"] == "D-101" and r["status"] == "active" and r["stage"] in ("AGREE", "FINANCE")
             and r["journey_id"] not in with_delivery and not r["journey_id"].startswith("J-US-J"))
    fake_ddb.seed("DEALER_Journeys", [{**j, "stage": "DELIVER", "context": {**(j.get("context") or {})}}])
    return j["journey_id"]


def test_trade_dispositions_read_customers_and_valuations_once(fake_ddb, monkeypatch):
    """The Store Cockpit builds this on every load; with every store in scope a
    per-disposition Customers get_item and Valuations scan cost ~400 DynamoDB
    calls (~9 s in Lambda). Each table must be read once, whatever the row count."""
    from app.services.common import ddb
    from app.services.dealer import inventory
    calls: list[tuple[str, str]] = []
    for name in ("get_item", "scan_filter", "scan_all"):
        real = getattr(ddb, name)
        def spy(bare, *a, _real=real, _name=name, **k):
            calls.append((_name, bare))
            return _real(bare, *a, **k)
        monkeypatch.setattr(ddb, name, spy)
    out = inventory.trade_dispositions({"is_oem": True, "dealer_ids": []}, "us")
    assert out["count"] > 1, "seed should carry several US dispositions"
    per_table = lambda t: [c for c in calls if c[1] == t]
    assert per_table("DEALER_Customers") == [("scan_all", "DEALER_Customers")]
    assert per_table("DEALER_Valuations") == [("scan_all", "DEALER_Valuations")]
    # the rows still carry the valuation-derived fields
    assert any(r["trade_value"] is not None and r["vehicle"] for r in out["dispositions"])


def test_board_lanes_derive_from_checklist_and_deliver_is_gated(fake_ddb):
    jid = _deliver_journey(fake_ddb)
    code, b = call(api_event("GET", "/deliveries", **DLV))
    assert code == 200 and set(b["lanes"]) == {"scheduled", "prepped", "docs_ready", "delivered"}
    assert any(c["journey_id"] == jid for c in b["lanes"]["scheduled"])

    code, body = call(api_event("POST", f"/deliveries/{jid}/deliver", **DLV))
    assert code == 409 and "pdi" in body["missing"] and "docs_signed" in body["missing"]

    chk = {"pdi": True, "detail": True, "accessories": True, "charge_level": True}
    code, u = call(api_event("PATCH", f"/deliveries/{jid}", **DLV, body={"checklist": chk}))
    assert code == 200 and u["delivery"]["lane"] == "prepped"
    bus.EMITTED.clear()
    code, u = call(api_event("PATCH", f"/deliveries/{jid}", **DLV,
                             body={"docs_signed": True, "scheduled_at": "2026-10-10T15:00:00Z"}))
    assert code == 200 and u["delivery"]["lane"] == "docs_ready" and "appointment.changed" in _types()
    code, _ = call(api_event("PATCH", f"/deliveries/{jid}", **DLV, body={"checklist": {"teleport": True}}))
    assert code == 400

    bus.EMITTED.clear()
    code, out = call(api_event("POST", f"/deliveries/{jid}/deliver", **DLV))
    assert code == 200 and out["status"] == "completed" and "vehicle.delivered" in _types()
    j = next(r for r in fake_ddb.rows("DEALER_Journeys") if r["journey_id"] == jid)
    assert j["stage"] == "OWN"


def test_delivery_roles(fake_ddb):
    code, _ = call(api_event("PATCH", "/deliveries/J-US-00166", user="U-US-00002", role="dealer_advisor",
                             dealer_ids="D-101", body={"docs_signed": True}))
    assert code == 403


# --- cockpit ---------------------------------------------------------------------

@pytest.mark.parametrize("tab", cockpit.TABS)
def test_every_cockpit_tab_has_kpis_and_sourced_insight(fake_ddb, tab):
    code, t = call(api_event("GET", f"/dealer/cockpit/{tab}", **MGR))
    assert code == 200 and t["tab"] == tab and t["kpis"]
    assert t["insight"]["text"] and t["insight"]["sources"]
    assert t["dealer_scope"] == ["D-101"]


def test_cockpit_tab_visibility_by_role(fake_ddb):
    assert call(api_event("GET", "/dealer/cockpit/inventory", **INV))[0] == 200
    assert call(api_event("GET", "/dealer/cockpit/sales", **INV))[0] == 403
    assert call(api_event("GET", "/dealer/cockpit/service", user="U-SM", role="dealer_service_manager", dealer_ids="D-101"))[0] == 200
    assert call(api_event("GET", "/dealer/cockpit/funnel", user="U-US-00002", role="dealer_advisor", dealer_ids="D-101"))[0] == 403


def test_cockpit_is_materialized_and_invalidated_by_events(fake_ddb):
    _, a = call(api_event("GET", "/dealer/cockpit/funnel", **MGR))
    _, b = call(api_event("GET", "/dealer/cockpit/funnel", **MGR))
    assert a["materialized_at"] == b["materialized_at"]
    bus.emit("journey.stage_changed", {"journey_id": "J-US-J1", "dealer_id": "D-101"})
    assert not cockpit._CACHE


def test_alert_ack_and_store_campaigns(fake_ddb):
    _, al = call(api_event("GET", "/dealer/cockpit/alerts", **MGR))
    aid = al["alerts"][0]["alert_id"]
    bus.EMITTED.clear()
    code, out = call(api_event("POST", f"/dealer/cockpit/alerts/{aid}/ack", **MGR, body={"note": "plan in place"}))
    assert code == 200 and out["status"] == "acknowledged" and "alert.acknowledged" in _types()
    code, c = call(api_event("GET", "/dealer/campaigns", **MGR))
    assert code == 200 and "spend_allocation" in c["assumptions"]
    assert all(r["share_of_campaign"] <= 1 for r in c["campaigns"])
    assert call(api_event("GET", "/dealer/campaigns", user="U-US-00002", role="dealer_advisor", dealer_ids="D-101"))[0] == 403


def test_legacy_cockpit_and_delivery_routes_still_work(fake_ddb):
    code, c = call(api_event("GET", "/dealer/cockpit", **MGR))
    assert code == 200 and set(c["tiles"]) == set(cockpit.TABS)
    code, d = call(api_event("GET", "/dealer/delivery", **MGR))
    assert code == 200 and "lanes" in d
