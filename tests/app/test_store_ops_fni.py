"""F&I Desk (§5.13): queue, worksheet, lender decision, compliance gate, DMS push,
stage advancement → delivery handoff (acceptance #5, #23, #37)."""
from __future__ import annotations

import pytest

from app.services.dealer import cockpit, fni
from app.services.events import bus
from tests.app.conftest import api_event, call

FNI = dict(user="U-US-00006", role="dealer_fni", dealer_ids="D-101")
DEAL, JOURNEY = "DL-US-00002", "J-US-J4"          # D-101, the J4 storyline journey at AGREE


@pytest.fixture(autouse=True)
def _fresh_cockpit():
    cockpit._CACHE.clear()


def _types():
    return [e["type"].split("@")[0] for e in bus.EMITTED]


def _reserve_unit(fake_ddb, journey=JOURNEY):
    """Reserve an in-stock VIN of the journey's model at its store (what the desk does before
    a DMS push)."""
    j = next(r for r in fake_ddb.rows("DEALER_Journeys") if r["journey_id"] == journey)
    unit = next(v for v in fake_ddb.rows("DEALER_Inventory")
                if v["dealer_id"] == j["dealer_id"] and v["status"] == "in_stock" and v["model_id"] == j["primary_model_id"])
    code, out = call(api_event("POST", f"/inventory/{unit['vin']}/reserve", **FNI, body={"journey_id": journey}))
    assert code == 200 and out["status"] == "reserved"
    return unit["vin"]


def test_queue_lists_desk_journeys_with_readiness_and_kpis(fake_ddb):
    code, q = call(api_event("GET", "/fni/queue", **FNI))
    assert code == 200 and q["count"] >= 1
    assert all(r["stage"] in ("FINANCE", "TRADE_IN", "AGREE") and r["dealer_id"] == "D-101" for r in q["queue"])
    assert set(q["queue"][0]["readiness"]) >= {"credit_app_in", "trade_agreed", "vin_reserved", "compliance_clear"}
    assert q["kpis"]["attach_rate"] is not None and q["kpis"]["pvr"] is not None


def test_queue_role_and_scope(fake_ddb):
    code, _ = call(api_event("GET", "/fni/queue", user="U-US-00007", role="dealer_service", dealer_ids="D-101"))
    assert code == 403
    code, _ = call(api_event("GET", f"/fni/deals/{DEAL}", user="U-US-00011", role="dealer_fni", dealer_ids="D-102"))
    assert code == 403


def test_worksheet_has_every_panel_with_sources(fake_ddb):
    code, ws = call(api_event("GET", f"/fni/deals/{DEAL}", **FNI))
    assert code == 200
    for k in ("vehicle", "price", "lender_offers", "scenarios", "menu", "compliance"):
        assert k in ws
    assert ws["price"]["source_ref"] and all(s["source_ref"] for s in ws["scenarios"])
    assert ws["compliance"]["disclosures"]["items"], "TILA/Reg Z disclosures come from OEM_Markets"
    assert ws["compliance"]["gate"]["ok"] is False           # nothing recorded yet → blocked, never assumed
    assert {m["code"] for m in ws["menu"]} >= {"vsc", "gap", "tire_wheel", "maint"}


def test_push_blocked_until_gate_clear_then_books(fake_ddb):
    code, body = call(api_event("POST", f"/fni/deals/{DEAL}/push", **FNI))
    assert code == 409 and body["gate"] == "credit_compliance" and "ofac" in body["missing"]

    code, dec = call(api_event("POST", f"/fni/deals/{DEAL}/lender-decision", **FNI))
    assert code == 200 and len(dec["decisions"]) == 3 and dec["compliance"]["ok"]
    approved = [d for d in dec["decisions"] if d["decision"] != "declined"]
    assert approved and all(d["source_ref"].startswith("F&I connector sandbox") for d in approved)

    code, upd = call(api_event("PATCH", f"/fni/deals/{DEAL}", **FNI,
                               body={"selected_offer_id": approved[0]["offer_id"], "term_months": 60}))
    assert code == 200 and upd["monthly_payment"] > 0

    code, menu = call(api_event("POST", f"/fni/deals/{DEAL}/menu", **FNI, body={"accepted": ["vsc", "gap"]}))
    assert code == 200 and menu["attached"] == ["gap", "vsc"] and menu["fi_products_total"] > 0

    # the gate is clear but no unit is reserved: a DMS deal is written against a VIN
    code, body = call(api_event("POST", f"/fni/deals/{DEAL}/push", **FNI))
    assert code == 409 and body["gate"] == "vehicle" and body["missing"] == ["vin_reserved"]
    vin = _reserve_unit(fake_ddb)
    bus.EMITTED.clear()
    code, pushed = call(api_event("POST", f"/fni/deals/{DEAL}/push", **FNI))
    assert code == 200 and pushed["status"] == "booked" and pushed["dms_deal_number"].startswith("DMS-")
    assert pushed["vin"] == vin
    assert next(r for r in fake_ddb.rows("DEALER_Journeys") if r["journey_id"] == JOURNEY)["vin"] == vin
    assert {"deal.pushed", "deal.booked"} <= set(_types())
    code, _ = call(api_event("PATCH", f"/fni/deals/{DEAL}", **FNI, body={"term_months": 72}))
    assert code == 409                                       # booked deals are unwound in the DMS


def test_all_declined_requires_adverse_action_before_agree(fake_ddb):
    deal = next(r for r in fake_ddb.rows("DEALER_Deals") if r["deal_id"] == DEAL)
    fake_ddb.seed("DEALER_Deals", [{**deal, "down_payment": -40000}])       # LTV far above every lender
    code, dec = call(api_event("POST", f"/fni/deals/{DEAL}/lender-decision", **FNI))
    assert code == 200 and not dec["any_approved"]
    assert "adverse_action" in dec["compliance"]["missing"]
    code, sent = call(api_event("POST", f"/fni/deals/{DEAL}/adverse-action", **FNI))
    assert code == 200 and sent["adverse_action_status"] == "sent"
    doc = next(d for d in fake_ddb.rows("CUST_Documents") if d["document_id"] == sent["document_id"])
    assert doc["data_class"] == "C4" and doc["shared_with"] == ["customer"]


def test_advance_agree_to_deliver_creates_delivery_and_handoff(fake_ddb):
    code, body = call(api_event("POST", f"/fni/journeys/{JOURNEY}/advance", **FNI))
    assert code == 409 and "deal_structure" in body["missing"]
    call(api_event("POST", f"/fni/deals/{DEAL}/lender-decision", **FNI))
    code, body = call(api_event("POST", f"/fni/journeys/{JOURNEY}/advance", **FNI))
    assert code == 409, "without a VIN the deal cannot book, so AGREE cannot close"
    _reserve_unit(fake_ddb)
    call(api_event("POST", f"/fni/deals/{DEAL}/push", **FNI))
    code, out = call(api_event("POST", f"/fni/journeys/{JOURNEY}/advance", **FNI))
    assert code == 200 and out["advance"]["to"] == "DELIVER"
    assert out["delivery"]["delivery_id"] == f"DLV-{JOURNEY}"
    assert out["handoff"]["to_user"] == "U-US-00009"         # D-101 delivery coordinator


def test_customer_facing_menu_document_shared_to_garage(fake_ddb):
    code, doc = call(api_event("POST", f"/fni/deals/{DEAL}/documents", **FNI, body={"kind": "fi_menu", "share": True}))
    assert code == 201 and doc["shared_with"] == ["customer"]
    cust = "C-US-J4"                                            # the deal's customer
    code, mine = call(api_event("GET", "/documents", user=cust, role="customer", customer_id=cust))
    assert code == 200 and any(d["document_id"] == doc["document_id"] for d in mine["documents"])
    code, url = call(api_event("GET", f"/documents/{doc['document_id']}/url", user=cust, role="customer", customer_id=cust))
    assert code == 200
    qs = dict(p.split("=") for p in url["url"].split("?")[1].split("&"))
    code, content = call(api_event("GET", f"/documents/{doc['document_id']}/content", user=cust, role="customer",
                                   customer_id=cust, query=qs))
    assert code == 200 and "menu" in content["content"]
    code, _ = call(api_event("GET", f"/documents/{doc['document_id']}/content", user=cust, role="customer",
                             customer_id=cust, query={**qs, "sig": "0" * 32}))
    assert code == 403
    code, _ = call(api_event("GET", f"/documents/{doc['document_id']}/url", user="C-US-J1", role="customer", customer_id="C-US-J1"))
    assert code == 403


def test_worksheet_by_journey_id_for_resolver_links(fake_ddb):
    code, ws = call(api_event("GET", f"/fni/deals/{JOURNEY}", **FNI))
    assert code == 200 and ws["deal_id"] == DEAL and ws["journey_id"] == JOURNEY


def test_lender_panel_lives_on_deal_and_one_offer_row_per_deal(fake_ddb):
    code, dec = call(api_event("POST", f"/fni/deals/{DEAL}/lender-decision", **FNI))
    offers = [o for o in fake_ddb.rows("DEALER_FinanceOffers") if o["deal_id"] == DEAL]
    assert len(offers) == 1 and offers[0]["status"] == "presented"      # table keyed by deal_id
    code, ws = call(api_event("GET", f"/fni/deals/{DEAL}", **FNI))
    assert len(ws["lender_offers"]) == 3
    other = next(o for o in ws["lender_offers"] if o["offer_id"] != offers[0]["offer_id"] and o["decision"] != "declined")
    call(api_event("PATCH", f"/fni/deals/{DEAL}", **FNI, body={"selected_offer_id": other["offer_id"]}))
    offers = [o for o in fake_ddb.rows("DEALER_FinanceOffers") if o["deal_id"] == DEAL]
    assert len(offers) == 1 and offers[0]["offer_id"] == other["offer_id"] and offers[0]["status"] == "accepted"



# --- the numbers on the worksheet agree with each other --------------------------------

def test_worksheet_price_breakdown_reconciles_and_labels_each_incentive(fake_ddb):
    code, ws = call(api_event("GET", f"/fni/deals/{DEAL}", **FNI))
    assert code == 200
    b = ws["price"]["breakdown"]
    assert b["vehicle_price"] == ws["vehicle"]["price_vehicle"] and b["tax_est"] == ws["vehicle"]["price_tax_est"]
    assert b["computed_total"] == ws["price"]["price_total"] and b["reconciles"] is True
    kinds = {i["incentive_id"]: i["kind"] for i in ws["incentives"]}
    assert kinds["US-OEM-APR-SPECIAL"] == "apr" and kinds["US-FED-LOAN-INTEREST"] == "informational"
    apr = next(i for i in ws["incentives"] if i["kind"] == "apr")
    assert apr["applied_to_deal"] is False and "Near Prime" in apr["note"]      # 9.8% deal tiers Near Prime
    assert ws["price"]["credit_tier"] == "Near Prime"


def test_apr_special_prices_the_captive_offer_for_a_qualifying_tier(fake_ddb):
    deal = next(r for r in fake_ddb.rows("DEALER_Deals") if r["deal_id"] == DEAL)
    fake_ddb.seed("DEALER_Deals", [{**deal, "apr_pct": 6.4, "term_months": 60}])     # Prime, 60 months
    code, dec = call(api_event("POST", f"/fni/deals/{DEAL}/lender-decision", **FNI))
    assert code == 200
    captive = next(d for d in dec["decisions"] if d["lender"] == "Meridian Financial Services")
    assert captive["decision"] == "approved" and captive["apr_pct"] == 2.9
    assert captive["apr_program"]["incentive_id"] == "US-OEM-APR-SPECIAL"
    code, ws = call(api_event("GET", f"/fni/deals/{DEAL}", **FNI))
    assert ws["price"]["apr_pct"] == 2.9 and ws["price"]["apr_program"]["incentive_id"] == "US-OEM-APR-SPECIAL"
    apr = next(i for i in ws["incentives"] if i["kind"] == "apr")
    assert apr["applied_to_deal"] is True and "applied" in apr["note"]


def test_apr_special_withheld_from_a_near_prime_deal(fake_ddb):
    code, dec = call(api_event("POST", f"/fni/deals/{DEAL}/lender-decision", **FNI))   # seeded 9.8% → Near Prime
    captive = next(d for d in dec["decisions"] if d["lender"] == "Meridian Financial Services")
    assert captive["apr_pct"] >= 9.8 and "apr_program" not in captive
    deal = next(r for r in fake_ddb.rows("DEALER_Deals") if r["deal_id"] == DEAL)
    assert "requires Prime" in deal["apr_program_check"]["note"]


# --- desking a journey that has no deal yet ---------------------------------------------

def test_ensure_deal_desks_from_the_saved_build_once(fake_ddb):
    # J-US-J1 (QUALIFY, saved build CFG) has no deal: the desk opens one from the build
    code, out = call(api_event("POST", "/fni/journeys/J-US-J1/deal", **FNI))
    assert code == 201 and out["created"] is True
    d = out["deal"]
    cfg = max((c for c in fake_ddb.rows("PLAT_Configurations") if c["journey_id"] == "J-US-J1"), key=lambda c: c["saved_at"])
    assert d["price_total"] == cfg["price_total"] and d["status"] == "desking" and d["credit_tier"] == "Prime"
    assert "saved build" in d["source_ref"] and out["worksheet"]["price"]["breakdown"]["reconciles"] is True
    assert "not a credit decision" in out["worksheet"]["price"]["apr_basis"]
    code, again = call(api_event("POST", "/fni/journeys/J-US-J1/deal", **FNI))
    assert code == 200 and again["created"] is False and again["deal"]["deal_id"] == d["deal_id"]


def test_ensure_deal_from_a_presented_trim_saves_the_build(fake_ddb):
    # a journey born from a lead: no build, but the advisor presented a trim on the floor
    adv = dict(user="U-US-00002", role="dealer_advisor", dealer_ids="D-101")
    code, lead = call(api_event("POST", "/leads", user="U-US-00005", role="dealer_bdc", dealer_ids="D-101",
                                body={"source": "web_form", "given_name": "Ana", "family_name": "Ruiz",
                                      "email": "ana.ruiz@example.com", "vehicle_of_interest_model_id": "MER-VOLT-C"}))
    assert code == 201
    code, a = call(api_event("POST", f"/leads/{lead['lead']['lead_id']}/assign", user="U-US-00005", role="dealer_bdc",
                             dealer_ids="D-101", body={"assignee": "U-US-00002"}))
    jid = a["journey_id"]
    code, _ = call(api_event("POST", f"/journeys/{jid}/present", **adv, body={"model_id": "MER-VOLT-C", "trim_id": "MER-VOLT-C-T2"}))
    assert code == 200
    code, out = call(api_event("POST", f"/fni/journeys/{jid}/deal", **FNI))
    assert code == 201 and out["deal"]["trim_id"] == "MER-VOLT-C-T2" and out["deal"]["price_total"] > 0
    assert any(c["journey_id"] == jid for c in fake_ddb.rows("PLAT_Configurations"))
    code, _ = call(api_event("POST", "/fni/journeys/J-US-00001/deal", **FNI))
    assert code in (201, 200, 403, 404, 409)         # another store's journey or one with nothing to desk never 500s
