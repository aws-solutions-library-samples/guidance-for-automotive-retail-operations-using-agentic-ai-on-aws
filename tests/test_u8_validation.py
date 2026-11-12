"""U8 Validation tests (test-after): the full FR5.2 invariant set + FR6 coverage
report. A good exported market PASSes; deliberately-broken fixtures trip each new
invariant (BR2.1-BR2.6). Mutations re-hash the artifact + manifest so BR1.1 stays
green and the FAIL is attributable to the targeted invariant.

Run this unit only:
    python3 -m pytest tests/test_u8_validation.py -q
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.seed.generate import phase_registry  # noqa: E402
from scripts.seed.generate.config import RunConfig  # noqa: E402
from scripts.seed.generate import market as market_mod  # noqa: E402
from scripts.seed.generate.core import G  # noqa: E402
from scripts.seed.generate.exporters import export_all  # noqa: E402
from scripts.seed.generate.validate import validate_market  # noqa: E402


def _build_market(tmp_path: Path, market: str = "us", seed: int = 42) -> Path:
    cfg = RunConfig(market=market, seed=seed)
    mdir = tmp_path / market
    cfg.out_dir = lambda: (mdir.mkdir(parents=True, exist_ok=True) or mdir)  # type: ignore[method-assign]
    g = G(cfg, market_mod.build(market))
    for phase in phase_registry.ordered_phases():
        phase(g)
    export_all(cfg, g.t, g.ix)
    return mdir


def _rewrite_table(mdir: Path, bare_or_full: str, mutate) -> None:
    """Load a table artifact, apply mutate(rows) in place, rewrite it, and fix its
    manifest sha256 so BR1.1 still passes (byte-for-byte canonical shape)."""
    manifest = json.loads((mdir / "manifest.json").read_text())
    art = next(a for a in manifest["artifacts"]
               if a.get("kind") == "table" and a["name"].endswith(bare_or_full))
    path = mdir / art["path"]
    rows = json.loads(path.read_text())
    mutate(rows)
    # Match the exporter's canonical JSON exactly (sorted keys, indent 2, trailing nl).
    data = (json.dumps(rows, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    path.write_bytes(data)
    art["sha256"] = hashlib.sha256(data).hexdigest()
    art["rows"] = len(rows)
    (mdir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


# ── Happy path ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("market", ["us", "de"])
def test_good_market_passes_all_invariants(tmp_path, market):
    mdir = _build_market(tmp_path, market)
    rep = validate_market(mdir)
    assert rep.passed, rep.failures
    assert rep.checks_run == 14  # BR1.1-1.7 + BR2.1-2.7 (2.7 = plausibility)


def test_coverage_report_written_and_complete(tmp_path):
    mdir = _build_market(tmp_path)
    rep = validate_market(mdir)
    cov = json.loads((mdir / "coverage-report.json").read_text())
    assert cov["passed"] is True and cov["failure_count"] == 0
    assert cov["storylines"]["purchase_present"] == cov["storylines"]["purchase_total"] == 9
    assert cov["storylines"]["service_present"] == cov["storylines"]["service_total"] == 8
    assert cov["storylines"]["district_present"] is True
    assert cov["table_count"] == 68


# ── Deliberately-broken fixtures (one per new invariant) ──────────────────────

def test_br2_1_missing_source_ref_fails(tmp_path):
    mdir = _build_market(tmp_path)

    def drop_source_ref(rows):
        rows[0].pop("source_ref", None)

    _rewrite_table(mdir, "OEM_Incentives", drop_source_ref)
    rep = validate_market(mdir)
    assert not rep.passed
    assert any(f.startswith("BR2.1") for f in rep.failures)


def test_br2_2_dangling_deal_journey_fails(tmp_path):
    mdir = _build_market(tmp_path)

    def break_ref(rows):
        rows[0]["journey_id"] = "J-US-DOES-NOT-EXIST"

    _rewrite_table(mdir, "DEALER_Deals", break_ref)
    rep = validate_market(mdir)
    assert not rep.passed
    assert any(f.startswith("BR2.2") for f in rep.failures)


def test_br2_3_bad_guardrail_rule_id_fails(tmp_path):
    mdir = _build_market(tmp_path)

    def break_rule(rows):
        rows[0]["rule_id"] = "GR-999"

    _rewrite_table(mdir, "OEM_GuardrailEvents", break_rule)
    rep = validate_market(mdir)
    assert not rep.passed
    assert any(f.startswith("BR2.3") for f in rep.failures)


def test_br2_4_non_monotone_funnel_fails(tmp_path):
    mdir = _build_market(tmp_path)

    def break_funnel(rows):
        rows[0]["shown"] = 0
        rows[0]["accepted"] = 5  # accepted > shown

    _rewrite_table(mdir, "DEALER_Recommendations", break_funnel)
    rep = validate_market(mdir)
    assert not rep.passed
    assert any(f.startswith("BR2.4") for f in rep.failures)


def test_br2_5_future_trace_fails(tmp_path):
    mdir = _build_market(tmp_path)

    def push_future(rows):
        rows[0]["ts"] = "2099-01-01T00:00:00Z"

    _rewrite_table(mdir, "PLAT_AgentTraces", push_future)
    rep = validate_market(mdir)
    assert not rep.passed
    assert any(f.startswith("BR2.5") for f in rep.failures)


def test_br2_6_missing_storyline_fails(tmp_path):
    mdir = _build_market(tmp_path)

    def drop_golden(rows):
        # remove the golden_story tag from every J1 row → storyline goes missing
        for r in rows:
            if r.get("golden_story") == "J1":
                r["golden_story"] = None

    _rewrite_table(mdir, "DEALER_Journeys", drop_golden)
    rep = validate_market(mdir)
    assert not rep.passed
    assert any(f.startswith("BR2.6") for f in rep.failures)


def test_br1_1_still_catches_tamper_without_rehash(tmp_path):
    # Sanity: a mutation that does NOT fix the manifest sha256 trips BR1.1.
    mdir = _build_market(tmp_path)
    manifest = json.loads((mdir / "manifest.json").read_text())
    art = next(a for a in manifest["artifacts"] if a.get("kind") == "table")
    (mdir / art["path"]).write_bytes(b"[]\n")  # tamper, no re-hash
    rep = validate_market(mdir)
    assert not rep.passed
    assert any(f.startswith("BR1.1") for f in rep.failures)
