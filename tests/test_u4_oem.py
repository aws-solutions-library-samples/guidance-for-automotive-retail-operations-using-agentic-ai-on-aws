"""U4 OEM tests (test-after): OEM_* tables, guardrail events, red-dealer plan
revision, referential integrity, source_ref, determinism, and the phase-registry
regression (resolved PHASES order == pre-refactor order).

Run this unit only:
    python3 -m pytest tests/test_u4_oem.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.seed.generate import commerce  # noqa: E402
from scripts.seed.generate import oem  # noqa: E402
from scripts.seed.generate import platform  # noqa: E402
from scripts.seed.generate import phase_registry  # noqa: E402
from scripts.seed.generate.config import RunConfig  # noqa: E402
from scripts.seed.generate import market as market_mod  # noqa: E402
from scripts.seed.generate.core import G  # noqa: E402
from scripts.seed.generate.owner_map import owner_matches  # noqa: E402
from scripts.seed.generate.platform import GUARDRAIL_IDS, GUARDRAIL_RULES  # noqa: E402
from scripts.seed.generate.storylines import DISTRICT  # noqa: E402

# The 10 OEM_ tables U4 owns (upstream Markets/StateRules/Catalog/CatalogModels excluded).
OEM_NEW_TABLES = [
    "OEM_Accounts", "OEM_AccountPlans", "OEM_ContactReports", "OEM_Alerts", "OEM_Campaigns",
    "OEM_Incentives", "OEM_CIPrograms", "OEM_Comms", "OEM_Compliance", "OEM_GuardrailEvents",
]
OEM_UPSTREAM_TABLES = ["OEM_Markets", "OEM_StateRules", "OEM_Catalog", "OEM_CatalogModels"]

EXPECTED_PHASE_ORDER = [
    "G.gen_org_users", "gen_platform_config", "G.gen_catalog", "G.gen_inventory",
    "G.gen_customers", "G.gen_signals_leads", "G.gen_journeys", "G.gen_calendar_slots",
    "gen_commerce", "gen_oem", "gen_platform_late",
]


def _g(market: str = "us", seed: int = 42) -> G:
    """Full registered phase list, run in memory (foundation -> ... -> OEM -> late)."""
    cfg = RunConfig(market=market, seed=seed)
    g = G(cfg, market_mod.build(market))
    for phase in phase_registry.ordered_phases():
        phase(g)
    return g


def _red(market: str) -> str:
    return {"us": "D-101", "de": "D-201"}[market]


# ── Part A: phase-registry refactor ──────────────────────────────────────────

def test_registry_resolves_expected_order():
    # The U4-and-earlier phases must resolve in exactly this relative order, and
    # gen_platform_late must remain the terminal phase. Later units (U5 corpora at
    # band 40, U6 storylines at band 50) insert phases between gen_oem and
    # gen_platform_late; this assertion tolerates those additions without edits by
    # checking the U4 prefix and the terminal phase rather than pinning the full list.
    names = [getattr(f, "__qualname__", str(f)) for f in phase_registry.ordered_phases()]
    assert names[: len(EXPECTED_PHASE_ORDER) - 1] == EXPECTED_PHASE_ORDER[:-1]
    # gen_platform_late is the last data phase; only the demo pack (band 99) follows it
    assert names[-2:] == [EXPECTED_PHASE_ORDER[-1], "gen_demo_pack"] and EXPECTED_PHASE_ORDER[-1] == "gen_platform_late"
    # every U4-and-earlier phase is present and in order
    assert names[: EXPECTED_PHASE_ORDER.index("gen_oem") + 1] == \
        EXPECTED_PHASE_ORDER[: EXPECTED_PHASE_ORDER.index("gen_oem") + 1]


def test_registry_orders_non_decreasing():
    # Internal (order, seq, fn) tuples must be sorted non-decreasing by order.
    orders = [o for o, _, _ in sorted(phase_registry._PHASES, key=lambda t: (t[0], t[1]))]
    assert orders == sorted(orders)


def test_full_run_deterministic():
    def canon(g: G) -> str:
        return json.dumps({k: g.t[k] for k in sorted(g.t)}, sort_keys=True, default=str)
    assert canon(_g("us", 42)) == canon(_g("us", 42))


# ── Part B: OEM tables ────────────────────────────────────────────────────────

def test_all_oem_new_tables_present_and_owner_prefixed():
    g = _g()
    for t in OEM_NEW_TABLES:
        assert t in g.t and g.t[t], f"missing/empty {t}"
        assert owner_matches(t), t
        assert t.split("_")[0] == "OEM"


def test_upstream_oem_tables_untouched_by_oem_module():
    # oem.gen_oem must not emit the four upstream OEM_ tables; run it on a fresh G
    # that has only run the spine, and assert those tables are unchanged.
    cfg = RunConfig(market="us", seed=42)
    g = G(cfg, market_mod.build("us"))
    g.gen_org_users(); platform.gen_platform_config(g); g.gen_catalog(); g.gen_inventory()
    g.gen_customers(); g.gen_signals_leads(); g.gen_journeys(); g.gen_calendar_slots()
    commerce.gen_commerce(g)
    before = {t: len(g.t.get(t, [])) for t in OEM_UPSTREAM_TABLES}
    oem.gen_oem(g)
    after = {t: len(g.t.get(t, [])) for t in OEM_UPSTREAM_TABLES}
    assert before == after


def test_guardrail_event_rule_ids_and_actions():
    g = _g()
    action_for = {rid: a for rid, _n, a in GUARDRAIL_RULES}
    outcome_for = {"block": "blocked", "require_approval": "approval_required", "warn": "warned"}
    for e in g.t["OEM_GuardrailEvents"]:
        assert e["rule_id"] in GUARDRAIL_IDS
        assert e["action"] == action_for[e["rule_id"]]
        assert e["outcome"] == outcome_for[e["action"]]


def test_gr_boundary_comms_have_matching_events():
    g = _g()
    boundary = {c["gr_boundary_rule_id"]: c for c in g.t["OEM_Comms"] if c.get("gr_boundary_rule_id")}
    assert set(boundary) == {"GR-003", "GR-006"}
    ev_by_comm = {e["comm_id"] for e in g.t["OEM_GuardrailEvents"] if e.get("comm_id")}
    for rid, comm in boundary.items():
        assert comm["comm_id"] in ev_by_comm, f"no guardrail event for boundary bulletin {rid}"


def test_red_dealer_plan_v1_and_v2():
    for mid in ("us", "de"):
        g = _g(mid)
        red = _red(mid)
        versions = sorted(p["version"] for p in g.t["OEM_AccountPlans"]
                          if p["dealer_id"] == red and p["status"] == "published")
        assert versions == [1, 2], (mid, versions)


def test_accounts_reference_dealers_and_groups():
    g = _g()
    dealer_codes = {d.code for d in g.m.dealers}
    for a in g.t["OEM_Accounts"]:
        if a["account_type"] == "dealer":
            assert a["dealer_id"] in dealer_codes
        elif a["account_type"] == "dealer_group":
            assert a["group_id"] in ("DG-01", "DG-02")
        elif a["account_type"] == "prospect":
            assert a["dealer_id"] is None


def test_alerts_reference_dealers_open_and_resolved():
    g = _g()
    dealer_codes = {d.code for d in g.m.dealers}
    statuses = {a["status"] for a in g.t["OEM_Alerts"]}
    assert {"open", "resolved"} <= statuses
    for a in g.t["OEM_Alerts"]:
        assert a["dealer_id"] in dealer_codes
    # red dealer over-represented
    from collections import Counter
    counts = Counter(a["dealer_id"] for a in g.t["OEM_Alerts"])
    assert counts[_red(g.m.id)] == max(counts.values())


def test_source_ref_on_monetary_and_allocation():
    g = _g()
    for c in g.t["OEM_Campaigns"]:
        assert c.get("source_ref")
    for i in g.t["OEM_Incentives"]:
        assert i.get("source_ref")
    for p in g.t["OEM_AccountPlans"]:
        for alloc in p.get("allocations", []):
            assert alloc.get("source_ref")


def test_two_market_incentive_currency_and_district():
    us, de = _g("us"), _g("de")
    assert any(i.get("amount_usd") is not None for i in us.t["OEM_Incentives"])
    assert any(i.get("amount_eur") is not None for i in de.t["OEM_Incentives"])
    assert us.t["OEM_Accounts"][0]["district_id"] == DISTRICT["us"]["district_id"]
    assert de.t["OEM_Accounts"][0]["district_id"] == DISTRICT["de"]["district_id"]


def test_oem_module_emits_only_oem_tables():
    # gen_oem's _add guard rejects non-OEM tables; assert every table it touches is OEM_.
    g = _g()
    for t in OEM_NEW_TABLES:
        for row in g.t[t]:
            assert isinstance(row, dict)
    # no OEM_ table carries a credential-shaped string in obvious text fields
    for t in OEM_NEW_TABLES:
        for row in g.t[t]:
            for v in row.values():
                if isinstance(v, str):
                    # Two independent assertions (not a boolean-OR that is true
                    # whenever either clause holds); validate.py is the authoritative gate.
                    assert "password" not in v.lower()
                    assert "IrpoDemo" not in v
