"""U2 Platform tests (test-after): PLAT_* config family, autonomy versions, guardrails,
nurture coverage, phase-list contract, and the gen_platform_late guard.

Run this unit only:
    python3 -m pytest tests/test_u2_platform.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.seed.generate import platform  # noqa: E402
from scripts.seed.generate import generate_synthetic as gs  # noqa: E402
from scripts.seed.generate.config import RunConfig, iso  # noqa: E402
from scripts.seed.generate import market as market_mod  # noqa: E402
from scripts.seed.generate.core import G  # noqa: E402
from scripts.seed.generate.owner_map import owner_matches  # noqa: E402


def _g(market: str = "us", seed: int = 42) -> G:
    cfg = RunConfig(market=market, seed=seed)
    g = G(cfg, market_mod.build(market))
    g.gen_org_users()
    platform.gen_platform_config(g)
    return g


CONFIG_TABLES = ("PLAT_Config", "PLAT_ConfigAutonomy", "PLAT_ConfigNurture",
                 "PLAT_ConfigNotifications", "PLAT_ConfigSuggestions", "PLAT_ConfigGuardrails")


# ── BR5.1: PLAT_-only emission ────────────────────────────────────────────────
def test_platform_emits_only_plat_tables():
    g = _g()
    for key in g.t:
        assert owner_matches(key), key
    for t in CONFIG_TABLES:
        assert t in g.t and g.t[t], t
    assert "PLAT_KnowledgeCollections" not in g.t  # BR7.7: U5 is the sole writer


# ── BR5.5: phase-list contract ────────────────────────────────────────────────
def test_phase_list_is_ordered_callables():
    assert isinstance(gs.PHASES, tuple)
    assert all(callable(p) for p in gs.PHASES)
    names = [getattr(p, "__name__", "") for p in gs.PHASES]
    assert names[0] == "gen_org_users"
    assert names[1] == "gen_platform_config"
    assert names[-2:] == ["gen_platform_late", "gen_demo_pack"]   # the demo pack runs last (band 99)


# ── BR6.2 / BR6.3 / BR6.4 / BR6.5: autonomy versions ─────────────────────────
def test_autonomy_three_versions_and_day14_flip():
    g = _g()
    rows = g.t["PLAT_ConfigAutonomy"]
    by_v = {}
    for r in rows:
        by_v.setdefault(r["version"], []).append(r)
    assert set(by_v) == {1, 2, 3}
    # v1: all active, all recommend, tenant scope, effective SEED_NOW-14d (BR6.3)
    assert all(r["is_active"] for r in by_v[1])
    assert all(r["level"] == "recommend" and r["scope"] == "tenant" for r in by_v[1])
    assert all(r["effective_from"] == iso(g.cfg.ago(days=14)) for r in by_v[1])
    # v2: scheduled at SEED_NOW+1d, not active (BR6.4)
    assert all(not r["is_active"] and r["effective_from"] == iso(g.cfg.ahead(days=1)) for r in by_v[2])
    # v3: dealer-scoped draft, effective_from null (BR6.2)
    assert all(not r["is_active"] and r["effective_from"] is None and r["scope"] == "dealer_id" for r in by_v[3])


def test_autonomy_v2_pilot_defaults():
    g = _g()
    v2 = {r["action_id"]: r for r in g.t["PLAT_ConfigAutonomy"] if r["version"] == 2}
    for a in ("a7.stage_parts", "a7.dispatch_ro", "a7.auto_present_estimate"):
        assert v2[a]["level"] == "act_and_notify", a
    assert v2["a7.reforecast_promise"]["level"] == "recommend"  # day-time work, not morning
    assert v2["a7.stage_parts"]["constraints"]["confidence_min"] == 0.8
    assert v2["a7.stage_parts"]["constraints"]["on_hand"] == 0
    assert "daily_cap" in v2["a7.stage_parts"]["constraints"]
    assert "max_amount" in v2["a7.auto_present_estimate"]["constraints"]
    assert v2["a5.publish_campaign"]["level"] == "act_with_approval"
    assert v2["a6.publish_account_plan"]["level"] == "act_with_approval"
    assert v2["a1.match_touch"]["level"] == "recommend"
    assert v2["a2.compose_message"]["level"] == "act_and_notify"  # spec shipped default
    # BR6.4 catch-all: every action NOT in the justified set is recommend (R-04)
    for action_id, row in v2.items():
        if action_id not in platform.V2_NON_RECOMMEND:
            assert row["level"] == "recommend", action_id
        else:
            assert row["level"] != "recommend", action_id


def test_autonomy_item_key_unique_across_markets():
    """R-02: item_key includes market so US and DE rows never collide in one table."""
    us = _g("us"); de = _g("de")
    keys_us = {(r["item_key"], r["version"]) for r in us.t["PLAT_ConfigAutonomy"]}
    keys_de = {(r["item_key"], r["version"]) for r in de.t["PLAT_ConfigAutonomy"]}
    assert not (keys_us & keys_de)
    # and unique within a market
    assert len(keys_us) == len(us.t["PLAT_ConfigAutonomy"])


def test_autonomy_v3_red_dealer_override():
    g = _g()
    red = next(d.code for d in g.m.dealers if d.condition == "csi_decline_lead_sla")
    v3 = {r["action_id"]: r for r in g.t["PLAT_ConfigAutonomy"] if r["version"] == 3}
    assert all(r["scope_id"] == red for r in v3.values())
    assert v3["a1.match_touch"]["level"] == "act_and_notify"


# ── BR6.6 / BR6.7: enum + hard rules + full registry coverage ─────────────────
def test_autonomy_enum_hard_rules_and_registry_coverage():
    g = _g()
    rows = g.t["PLAT_ConfigAutonomy"]
    assert all(r["level"] in platform.LEVELS for r in rows)
    ids_in_rows = {r["action_id"] for r in rows}
    assert not (ids_in_rows & platform.HARD_RULE_ACTIONS)
    v1_ids = [r["action_id"] for r in rows if r["version"] == 1]
    registry_ids = [a for a, _, _ in platform.ACTION_REGISTRY]
    assert sorted(v1_ids) == sorted(registry_ids)          # every registry action exactly once in v1
    assert len(set(v1_ids)) == len(v1_ids)


# ── BR7.1 / BR7.2: key schema + one active per key ────────────────────────────
def test_config_family_key_schema_and_single_active():
    g = _g()
    for t in CONFIG_TABLES:
        for r in g.t[t]:
            for f in ("config_key", "version", "is_active", "market_id", "item_key"):
                assert f in r, (t, f)
    for t in CONFIG_TABLES:
        active = [r for r in g.t[t] if r["is_active"]]
        # autonomy has one active row PER ACTION (v1); other tables exactly one row
        if t == "PLAT_ConfigAutonomy":
            assert {r["version"] for r in active} == {1}
        else:
            assert len(active) == 1, t
    nurture_versions = sorted(r["version"] for r in g.t["PLAT_ConfigNurture"])
    assert nurture_versions == [1, 2]
    assert [r["is_active"] for r in sorted(g.t["PLAT_ConfigNurture"], key=lambda r: r["version"])] == [True, False]


# ── BR7.4: guardrails exactly GR-001..GR-008 + denylist (R-16) ────────────────
def test_guardrails_exact_spec_set():
    g = _g()
    row = g.t["PLAT_ConfigGuardrails"][0]
    ids = [r["rule_id"] for r in row["rules"]]
    assert ids == [f"GR-00{i}" for i in range(1, 9)]
    assert row["rules"][0]["name"] == "Wholesale reduction recommendations"
    assert row["rules"][7]["name"] == "Competitive disparagement"
    assert len(row["scare_language_denylist"]) >= 4


# ── BR7.3: nurture covers sales + service, both languages ─────────────────────
def test_nurture_coverage_and_language():
    us = _g("us"); de = _g("de")
    def active(g):
        return next(r for r in g.t["PLAT_ConfigNurture"] if r["is_active"])["templates"]
    tu, td = active(us), active(de)
    assert {t["family"] for t in tu} == {"sales", "service"}
    assert set(platform.SERVICE_TRIGGERS) <= {t["stage_or_trigger"] for t in tu if t["family"] == "service"}
    from scripts.seed.generate.config import STAGES
    assert set(STAGES) <= {t["stage_or_trigger"] for t in tu if t["family"] == "sales"}
    us_bodies = {t["template_id"]: t["body"] for t in tu}
    de_bodies = {t["template_id"].replace("T-DE-", "T-US-"): t["body"] for t in td}
    assert any(us_bodies[k] != de_bodies[k] for k in us_bodies if k in de_bodies)  # different language


# ── BR7.5: no literal PII in templates (placeholders only) ────────────────────
def test_templates_use_placeholders_not_pii():
    import re
    g = _g()
    for r in g.t["PLAT_ConfigNurture"]:
        for t in r["templates"]:
            assert "{first_name}" in t["body"] or "{" in t["body"]
            assert not re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", t["body"])


# ── BR7.7: knowledge vocabulary published, no rows ────────────────────────────
def test_knowledge_vocabulary_no_rows():
    for m in ("us", "de"):
        vocab = platform.KNOWLEDGE_COLLECTIONS[m]
        assert len(vocab) == len(platform.KNOWLEDGE_KINDS)
        assert all(v["collection_id"].startswith(f"KC-{m.upper()}-") for v in vocab)


# ── BR5.3 unhappy path: gen_platform_late guard (no U3 tables yet) ────────────
def test_platform_late_guard_skips_absent_tables():
    g = _g()
    report = platform.gen_platform_late(g)
    # dealers exist -> dealer mappings emitted; customers/deals/ROs absent -> skipped, reported
    assert "PLAT_EntityMap" in g.t
    assert {r["entity_type"] for r in g.t["PLAT_EntityMap"]} == {"dealer"}
    for k in ("customer", "deal", "repair_order", "purchase_traces", "service_traces"):
        assert k in report
    assert "PLAT_AgentTraces" not in g.t
    assert report["_traces_emitted"] == "0"


# ── BR8.x happy path over the foundation's own journeys (R-01/R-03/R-07) ──────
def _g_with_journeys(market: str = "us", seed: int = 42) -> G:
    """Foundation sales spine (already in core.py) + platform config, so gen_platform_late
    has customers, journeys, and events to trace — the path U3 will wire."""
    g = _g(market, seed)
    g.gen_catalog(); g.gen_inventory(); g.gen_customers(); g.gen_signals_leads(); g.gen_journeys()
    return g


def test_platform_late_over_foundation_journeys():
    g = _g_with_journeys()
    report = platform.gen_platform_late(g)
    em = g.t["PLAT_EntityMap"]
    # BR8.1: customer + dealer mappings emitted; deals/ROs still absent -> reported, not stubbed
    assert {"customer", "dealer"} <= {r["entity_type"] for r in em}
    assert "deal" in report and "repair_order" in report
    # spec PK+SK uniqueness on (entity_type, app_id, system)
    keys = [(r["entity_type"], r["app_id"], r["system"]) for r in em]
    assert len(keys) == len(set(keys))
    # BR8.2: exactly 12 merge-queue rows, each low-confidence and colliding with its pair's external_id
    dupes = [r for r in em if r["duplicate_of"]]
    assert len(dupes) == 12
    crm = {(r["app_id"]): r for r in em if r["entity_type"] == "customer" and r["system"] == "crm"}
    for d in dupes:
        assert d["confidence"] < 0.6
        assert d["external_id"] == crm[d["duplicate_of"]]["external_id"]
    # BR8.4: purchase traces only for golden J1-J9, ts inside the event window, journey_id set
    # (the Lead Desk lifecycle traces seeded with the leads are their own kind, keyed by lead_id)
    lead_traces = [t for t in g.t["PLAT_AgentTraces"] if t.get("lead_id")]
    assert all(t["journey_kind"] == "lead" and t["tool"].startswith(("a2.", "a3.")) for t in lead_traces)
    traces = [t for t in g.t["PLAT_AgentTraces"] if not t.get("lead_id")]
    assert traces and all(t["journey_kind"] == "purchase" and t["journey_id"] for t in traces)
    golden_jids = {j["journey_id"] for j in g.t["DEALER_Journeys"]
                   if j.get("golden_story") and j["golden_story"] != "J10"}
    assert {t["journey_id"] for t in traces} <= golden_jids
    windows = {}
    for e in g.t["DEALER_JourneyEvents"]:
        lo, hi = windows.get(e["journey_id"], (e["event_ts"], e["event_ts"]))
        windows[e["journey_id"]] = (min(lo, e["event_ts"]), max(hi, e["event_ts"]))
    for t in traces:
        lo, hi = windows[t["journey_id"]]
        assert lo <= t["ts"] <= hi
    # BR8.5: rule_id set iff guardrail_blocked, and from GR-001..GR-008
    for t in traces:
        if t["outcome"] == "guardrail_blocked":
            assert t["rule_id"] in platform.GUARDRAIL_IDS
        else:
            assert t["rule_id"] is None
    # R-03: autonomy_level is RESOLVED — inside the v1 window every action is recommend;
    # before v1.effective_from it is observe. Never a random act_* during observation.
    v1_from = iso(g.cfg.ago(days=14))
    for t in traces:
        expected = "recommend" if t["ts"] >= v1_from else "observe"
        assert t["autonomy_level"] == expected, (t["ts"], t["autonomy_level"])
    assert int(report["_traces_emitted"]) == len(traces) > 0


# ── NFR1: determinism of the config phase ────────────────────────────────────
def test_config_phase_deterministic():
    a = _g("us", 42); b = _g("us", 42)
    for t in CONFIG_TABLES:
        assert json.dumps(a.t[t], sort_keys=True) == json.dumps(b.t[t], sort_keys=True), t
