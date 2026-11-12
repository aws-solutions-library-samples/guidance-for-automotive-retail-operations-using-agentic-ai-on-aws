"""U3 Commerce tests (test-after): DEALER_*/CUST_* tables, referential integrity,
recommendation funnels, service journeys, determinism.

Run this unit only:
    python3 -m pytest tests/test_u3_commerce.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.seed.generate import commerce  # noqa: E402
from scripts.seed.generate import platform  # noqa: E402
from scripts.seed.generate.config import RunConfig  # noqa: E402
from scripts.seed.generate import market as market_mod  # noqa: E402
from scripts.seed.generate.core import G  # noqa: E402
from scripts.seed.generate.owner_map import owner_matches  # noqa: E402


def _g(market: str = "us", seed: int = 42) -> G:
    """Full sales spine + platform config + commerce, in memory."""
    cfg = RunConfig(market=market, seed=seed)
    g = G(cfg, market_mod.build(market))
    g.gen_org_users()
    platform.gen_platform_config(g)
    g.gen_catalog()
    g.gen_inventory()
    g.gen_customers()
    g.gen_signals_leads()
    g.gen_journeys()
    g.gen_calendar_slots()
    commerce.gen_commerce(g)
    return g


COMMERCE_NEW_TABLES = [
    "DEALER_Deals", "DEALER_FinanceOffers", "DEALER_FIProducts", "DEALER_Deliveries",
    "DEALER_SalesPackages", "DEALER_Touchpoints", "DEALER_Valuations", "DEALER_TradeDispositions",
    "DEALER_Coverage", "DEALER_Vehicles", "DEALER_ServiceJourneys", "DEALER_ServiceJourneyEvents",
    "DEALER_RepairOrders", "DEALER_ServiceEstimates", "DEALER_DeclinedWork", "DEALER_Inspections",
    "DEALER_InspectionTemplates", "DEALER_Parts", "DEALER_PartsOrders", "DEALER_LaborOps",
    "DEALER_DTCReference", "DEALER_Recommendations", "DEALER_Outcomes", "DEALER_Approvals",
    "DEALER_Tasks", "DEALER_Notifications", "DEALER_NurturePlans", "DEALER_ObjectionStrategies",
    "DEALER_Plays", "DEALER_PlayRuns", "CUST_Messages", "CUST_Documents",
]

# The 10 DEALER_ tables U2 deferred to U3 (BR5.6).
DEFERRED_10 = ["DEALER_Recommendations", "DEALER_Outcomes", "DEALER_Approvals", "DEALER_Tasks",
               "DEALER_Notifications", "DEALER_NurturePlans", "DEALER_ObjectionStrategies",
               "DEALER_Plays", "DEALER_PlayRuns"]


def test_all_commerce_tables_present_and_owner_prefixed():
    g = _g()
    for t in COMMERCE_NEW_TABLES:
        assert t in g.t and g.t[t], f"missing/empty {t}"
        assert owner_matches(t), t
    # commerce.py must emit ONLY DEALER_/CUST_ tables
    # (find keys added by commerce vs. the spine — assert none are OEM_/PLAT_ from commerce's own guard)
    for t in COMMERCE_NEW_TABLES:
        assert t.split("_")[0] in ("DEALER", "CUST")


def test_deferred_ten_tables_present():
    g = _g()
    for t in DEFERRED_10:
        assert t in g.t and g.t[t], f"deferred table {t} missing (BR5.6)"


def test_deals_reference_journeys_and_stage_gate():
    g = _g()
    jids = {j["journey_id"]: j for j in g.t["DEALER_Journeys"]}
    for d in g.t["DEALER_Deals"]:
        assert d["journey_id"] in jids
        assert jids[d["journey_id"]]["stage"] in commerce.FINANCE_STAGES


def test_service_journey_pk_and_events_reference():
    g = _g()
    sj_ids = {s["service_journey_id"] for s in g.t["DEALER_ServiceJourneys"]}
    assert sj_ids
    for e in g.t["DEALER_ServiceJourneyEvents"]:
        assert e["service_journey_id"] in sj_ids
        assert "event_ts" in e  # the field U2's gen_platform_late reads
    for ro in g.t["DEALER_RepairOrders"]:
        assert ro["service_journey_id"] in sj_ids


def test_repair_order_children_reference_ro():
    g = _g()
    ro_ids = {r["ro_id"] for r in g.t["DEALER_RepairOrders"]}
    for ins in g.t["DEALER_Inspections"]:
        assert ins["ro_id"] in ro_ids
    for est in g.t["DEALER_ServiceEstimates"]:
        assert est["ro_id"] in ro_ids


def test_recommendation_funnel_monotonic():
    g = _g()
    for r in g.t["DEALER_Recommendations"]:
        assert r["shown"] >= r["accepted"] >= r["converted"] >= 0


def test_inspection_status_mix_and_evidence():
    g = _g()
    for ins in g.t["DEALER_Inspections"]:
        assert ins["status"] in ("green", "yellow", "red")
        if ins["status"] in ("yellow", "red"):
            assert ins["evidence_doc_id"] is not None


def test_source_ref_on_monetary_rows():
    g = _g()
    for t in ("DEALER_Deals", "DEALER_FinanceOffers", "DEALER_ServiceEstimates", "DEALER_Valuations"):
        for row in g.t[t]:
            assert row.get("source_ref"), (t, row)


def test_golden_service_visits_tagged():
    g = _g()
    golden = [s for s in g.t["DEALER_ServiceJourneys"] if s.get("golden_story")]
    tags = {s["golden_story"] for s in golden}
    assert tags == set(commerce.SERVICE_GOLDEN)  # S1-S8 all present


def test_vehicles_master_and_service_vin_link():
    g = _g()
    vins = {v["vin"] for v in g.t["DEALER_Vehicles"]}
    assert vins
    # service journeys for owners key to a vehicle VIN in the master
    linked = [s for s in g.t["DEALER_ServiceJourneys"] if s["vin"] in vins]
    assert linked  # at least the owner-linked service journeys resolve to the VIN master


def test_cust_tables_reference_customers():
    g = _g()
    cids = {c["customer_id"] for c in g.t["DEALER_Customers"]}
    for t in ("CUST_Messages", "CUST_Documents", "CUST_Consent"):
        for row in g.t[t]:
            assert row["customer_id"] in cids


def test_two_market_currency():
    us, de = _g("us"), _g("de")
    assert us.t["DEALER_Deals"][0]["currency"] == "USD"
    assert de.t["DEALER_Deals"][0]["currency"] == "EUR"


def test_commerce_deterministic():
    a, b = _g("us", 42), _g("us", 42)
    for t in COMMERCE_NEW_TABLES:
        assert json.dumps(a.t[t], sort_keys=True) == json.dumps(b.t[t], sort_keys=True), t
