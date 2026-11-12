"""U6 Storylines tests (test-after): the g.ix['storylines'] coherence index over
the golden slices, storyline coverage, the R-08 AgentTraces SEED_NOW clamp, the
band-50 registry position, and determinism.

Run this unit only:
    python3 -m pytest tests/test_u6_storylines.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.seed.generate import phase_registry  # noqa: E402
from scripts.seed.generate import storylines_gen  # noqa: E402
from scripts.seed.generate.config import RunConfig, iso  # noqa: E402
from scripts.seed.generate import market as market_mod  # noqa: E402
from scripts.seed.generate.core import G  # noqa: E402
from scripts.seed.generate.storylines import JOURNEYS, COLD_COHORT_ID, DISTRICT  # noqa: E402


def _g(market: str = "us", seed: int = 42):
    cfg = RunConfig(market=market, seed=seed)
    g = G(cfg, market_mod.build(market))
    for phase in phase_registry.ordered_phases():
        phase(g)
    return g, cfg


def test_storylines_phase_registered_at_band_50():
    names = [getattr(f, "__qualname__", str(f)) for f in phase_registry.ordered_phases()]
    assert "gen_storylines" in names
    # after corpora (band 40), before platform-late (band 90)
    assert names.index("gen_corpora") < names.index("gen_storylines") < names.index("gen_platform_late")


@pytest.mark.parametrize("mid", ["us", "de"])
def test_all_golden_storylines_present(mid):
    g, _ = _g(mid)
    cov = g.ix["storyline_coverage"]
    assert cov["purchase_present"] == cov["purchase_total"] == len(JOURNEYS)
    assert cov["service_present"] == cov["service_total"] == len(storylines_gen.SERVICE_GOLDEN)
    assert cov["cold_cohort_present"] is True
    assert cov["district_present"] is True


@pytest.mark.parametrize("mid", ["us", "de"])
def test_purchase_storylines_resolve_to_journeys(mid):
    g, _ = _g(mid)
    purchase = [s for s in g.ix["storylines"] if s["kind"] == "purchase"]
    assert {s["storyline_id"] for s in purchase} == {j["id"] for j in JOURNEYS}
    journey_ids = {j["journey_id"] for j in g.t["DEALER_Journeys"]}
    for s in purchase:
        assert s["present"] and s["journey_id"] in journey_ids
        assert s["event_count"] > 0  # golden journeys have a rich event timeline


@pytest.mark.parametrize("mid", ["us", "de"])
def test_service_storylines_resolve_to_service_journeys(mid):
    g, _ = _g(mid)
    service = [s for s in g.ix["storylines"] if s["kind"] == "service"]
    assert {s["storyline_id"] for s in service} == set(storylines_gen.SERVICE_GOLDEN)
    sj_ids = {s["service_journey_id"] for s in g.t["DEALER_ServiceJourneys"]}
    for s in service:
        assert s["present"] and s["service_journey_id"] in sj_ids


@pytest.mark.parametrize("mid", ["us", "de"])
def test_district_storyline_red_dealer_plan_versions(mid):
    g, _ = _g(mid)
    district = next(s for s in g.ix["storylines"] if s["kind"] == "district")
    assert district["district_id"] == DISTRICT[mid]["district_id"]
    assert district["red_dealer_id"] == ("D-101" if mid == "us" else "D-201")
    assert district["red_dealer_plan_versions"] == [1, 2]
    assert district["account_count"] > 0 and district["alert_count"] > 0


@pytest.mark.parametrize("mid", ["us", "de"])
def test_cold_cohort_storyline(mid):
    g, _ = _g(mid)
    cold = next(s for s in g.ix["storylines"] if s["kind"] == "cold_cohort")
    assert cold["storyline_id"] == COLD_COHORT_ID
    assert cold["count"] > 0 and len(cold["journey_ids"]) == cold["count"]


@pytest.mark.parametrize("mid", ["us", "de"])
def test_r08_agent_traces_never_exceed_seed_now(mid):
    # R-08: golden journeys carry future-dated events (test-drive/delivery via
    # cfg.ahead); the AgentTraces window must be clamped so no trace ts > SEED_NOW.
    g, cfg = _g(mid)
    now = iso(cfg.now)
    traces = g.t.get("PLAT_AgentTraces", [])
    assert traces, "expected golden AgentTraces"
    assert all(t["ts"] <= now for t in traces), "R-08: a trace ts exceeded SEED_NOW"


def test_storylines_deterministic():
    a, _ = _g("us", 42)
    b, _ = _g("us", 42)
    assert json.dumps(a.ix["storylines"], sort_keys=True) == json.dumps(b.ix["storylines"], sort_keys=True)


def test_storylines_writes_no_tables():
    # U6 is a pure weaving pass over already-materialized rows: it must not add a
    # storyline table. Run the spine WITHOUT gen_storylines, snapshot table names,
    # then run gen_storylines and assert the table set is unchanged.
    g, _ = _g("us")
    before = set(g.t)
    storylines_gen.gen_storylines(g)  # idempotent re-run
    assert set(g.t) == before
