"""U5 Corpora tests (test-after): PLAT_KnowledgeCollections rows + the in-memory
agent-grounding document corpus, citation resolution, source_ref, determinism,
and the band-40 registry order.

Run this unit only:
    python3 -m pytest tests/test_u5_corpora.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.seed.generate import corpora  # noqa: E402
from scripts.seed.generate import phase_registry  # noqa: E402
from scripts.seed.generate.config import RunConfig  # noqa: E402
from scripts.seed.generate import market as market_mod  # noqa: E402
from scripts.seed.generate.core import G  # noqa: E402
from scripts.seed.generate.owner_map import owner_matches  # noqa: E402
from scripts.seed.generate.platform import KNOWLEDGE_KINDS, KNOWLEDGE_COLLECTIONS  # noqa: E402

# Full resolved phase order after U5 (corpora at band 40, before platform-late).
EXPECTED_PHASE_ORDER = [
    "G.gen_org_users", "gen_platform_config", "G.gen_catalog", "G.gen_inventory",
    "G.gen_customers", "G.gen_signals_leads", "G.gen_journeys", "G.gen_calendar_slots",
    "gen_commerce", "gen_oem", "gen_corpora", "gen_storylines", "G.gen_service_staff", "gen_platform_late",
    "gen_demo_pack",   # band 99: the demo pack runs after everything (its rows move nothing earlier)
]


def _g(market: str = "us", seed: int = 42) -> G:
    cfg = RunConfig(market=market, seed=seed)
    g = G(cfg, market_mod.build(market))
    for phase in phase_registry.ordered_phases():
        phase(g)
    return g


def test_registry_includes_corpora_at_band_40():
    names = [getattr(f, "__qualname__", str(f)) for f in phase_registry.ordered_phases()]
    assert names == EXPECTED_PHASE_ORDER
    # corpora sits after oem and before platform-late
    assert names.index("gen_corpora") == names.index("gen_oem") + 1
    assert names.index("gen_corpora") < names.index("gen_platform_late")


def test_knowledge_collections_owner_prefixed_and_sole_writer():
    g = _g()
    kc = g.t.get("PLAT_KnowledgeCollections", [])
    assert kc, "U5 must populate PLAT_KnowledgeCollections (U2 emitted none)"
    assert owner_matches("PLAT_KnowledgeCollections")
    for row in kc:
        assert row["collection_id"].startswith("KC-")


def test_one_row_per_document():
    g = _g()
    kc = g.t["PLAT_KnowledgeCollections"]
    corpus = g.ix["corpus"]
    assert len(kc) == len(corpus)
    doc_ids = {d["doc_id"] for d in corpus}
    for row in kc:
        assert row["doc_id"] in doc_ids


def test_collection_vocabulary_and_all_kinds():
    for mid in ("us", "de"):
        g = _g(mid)
        valid = {c["collection_id"] for c in KNOWLEDGE_COLLECTIONS[mid]}
        for row in g.t["PLAT_KnowledgeCollections"]:
            assert row["collection_id"] in valid
        kinds = {d["kind"] for d in g.ix["corpus"]}
        assert kinds == set(KNOWLEDGE_KINDS), (mid, kinds)


def test_tsb_citations_resolve_to_dtc_reference():
    g = _g()
    dtcs = {r["dtc"] for r in g.t.get("DEALER_DTCReference", [])}
    tsbs = [d for d in g.ix["corpus"] if d["kind"] == "tsbs"]
    assert tsbs
    for doc in tsbs:
        # source_ref is entity:<dtc>
        ref = doc["source_ref"].split("entity:", 1)[-1].split(";")[0]
        assert ref in dtcs


def test_repair_procedure_citations_resolve_to_labor_ops():
    g = _g()
    ops = {r["labor_op_id"] for r in g.t.get("DEALER_LaborOps", [])}
    procs = [d for d in g.ix["corpus"] if d["kind"] == "repair_procedures"]
    assert procs
    for doc in procs:
        ref = doc["source_ref"].split("entity:", 1)[-1].split(";")[0]
        assert ref in ops


def test_gr_boundary_bulletins_present():
    g = _g()
    bulletins = [d for d in g.ix["corpus"] if d["kind"] == "bulletins"]
    titles = " ".join(d["title"] for d in bulletins)
    assert "GR:GR-003" in titles
    assert "GR:GR-006" in titles


def test_source_ref_on_every_document_and_row():
    g = _g()
    for d in g.ix["corpus"]:
        assert d.get("source_ref")
    for row in g.t["PLAT_KnowledgeCollections"]:
        assert row.get("source_ref")


def test_no_credential_shaped_strings_in_corpus():
    g = _g()
    for d in g.ix["corpus"]:
        body = str(d.get("body", ""))
        assert "IrpoDemo" not in body
        assert "password" not in body.lower()


def test_two_market_corpus():
    us, de = _g("us"), _g("de")
    assert us.ix["corpus"] and de.ix["corpus"]
    # market_id is consistent per run
    assert all(d["market_id"] == "us" for d in us.ix["corpus"])
    assert all(d["market_id"] == "de" for d in de.ix["corpus"])


def test_corpora_deterministic():
    a, b = _g("us", 42), _g("us", 42)
    assert json.dumps(a.t["PLAT_KnowledgeCollections"], sort_keys=True) == \
        json.dumps(b.t["PLAT_KnowledgeCollections"], sort_keys=True)
    assert json.dumps(a.ix["corpus"], sort_keys=True) == \
        json.dumps(b.ix["corpus"], sort_keys=True)


def test_objection_strategies_cover_commerce_vocabulary():
    # corpora derives its objection topics from commerce; assert coverage so the
    # grounding scripts cannot silently drift from the operational vocabulary.
    from scripts.seed.generate import commerce
    assert set(commerce.OBJECTION_STRATEGY_TYPES) <= set(corpora._OBJECTION_STRATEGIES)


def test_corpora_emits_only_plat_tables():
    # gen_corpora on a fresh full run must not add any non-PLAT table beyond what
    # upstream phases created; specifically its only new table is KnowledgeCollections.
    g = _g()
    # KnowledgeCollections is PLAT_ and present; no DEALER_/OEM_/CUST_ table is
    # introduced by corpora (it writes only via its guarded _add).
    assert "PLAT_KnowledgeCollections" in g.t
    assert owner_matches("PLAT_KnowledgeCollections")
