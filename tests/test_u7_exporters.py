"""U7 Exporters tests (test-after): DynamoDB item-shape loader (contract C3 wrap
+ C1 item_key PK), S3 lake NDJSON/CSV prefixes, Quick Index corpora export, and
manifest completeness + determinism over a full generated market.

Run this unit only:
    python3 -m pytest tests/test_u7_exporters.py -q
"""
from __future__ import annotations

import csv
import io
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
from scripts.seed.generate.exporters import (  # noqa: E402
    export_all, LAKE_PREFIXES, to_ddb_item, load_pk, ITEM_KEY_TABLES,
)


def _run_and_export(tmp_path: Path, market: str = "us", seed: int = 42):
    cfg = RunConfig(market=market, seed=seed)
    # Redirect output under tmp_path so tests never touch sample-data/.
    cfg.__dict__["_out_override"] = tmp_path
    orig_out_dir = cfg.out_dir

    def out_dir():
        d = tmp_path / market
        d.mkdir(parents=True, exist_ok=True)
        return d

    cfg.out_dir = out_dir  # type: ignore[method-assign]
    g = G(cfg, market_mod.build(market))
    for phase in phase_registry.ordered_phases():
        phase(g)
    manifest_path = export_all(cfg, g.t, g.ix)
    return g, cfg, manifest_path


# ── DynamoDB item-shape loader (contract C3 + C1) ─────────────────────────────

def test_to_ddb_item_shapes():
    row = {"s": "x", "n_int": 3, "n_float": 1.5, "b": True, "nil": None,
           "lst": ["a", 2], "mp": {"k": "v"}}
    item = to_ddb_item(row)
    assert item["s"] == {"S": "x"}
    assert item["n_int"] == {"N": "3"}
    assert item["n_float"] == {"N": "1.5"}
    assert item["b"] == {"BOOL": True}          # bool before int
    assert item["nil"] == {"NULL": True}
    assert item["lst"] == {"L": [{"S": "a"}, {"N": "2"}]}
    assert item["mp"] == {"M": {"k": {"S": "v"}}}


def test_load_pk_uses_item_key_for_config_family():
    for table in ITEM_KEY_TABLES:
        assert load_pk(table, {"item_key": "K#1", "other": 1}) == "K#1"
    # missing item_key on an item_key table is a hard error (contract C1)
    with pytest.raises(ValueError):
        load_pk("PLAT_Config", {"config_key": "tenant"})


def test_load_pk_default_id_field():
    assert load_pk("DEALER_Deals", {"deal_id": "DL-US-1"}) == "DL-US-1"
    assert load_pk("OEM_Accounts", {"account_id": "ACC-US-1"}) == "ACC-US-1"
    # first *_id fallback when the conventional field name does not match
    assert load_pk("DEALER_ServiceJourneyEvents", {"service_journey_id": "SJ-1", "event_ts": "t"}) == "SJ-1"


# ── S3 lake NDJSON/CSV ────────────────────────────────────────────────────────

def test_lake_prefixes_written_ndjson_and_csv(tmp_path):
    g, cfg, _ = _run_and_export(tmp_path)
    lake = cfg.out_dir() / "lake"
    for prefix, sources in LAKE_PREFIXES.items():
        has_rows = any(g.t.get(s) for s in sources)
        pdir = lake / prefix
        if not has_rows:
            continue
        nd = pdir / "data.ndjson"
        cv = pdir / "data.csv"
        assert nd.is_file() and cv.is_file(), prefix
        # NDJSON: one JSON object per line, count matches source rows
        lines = [ln for ln in nd.read_text().splitlines() if ln]
        expected = sum(len(g.t.get(s, [])) for s in sources)
        assert len(lines) == expected, prefix
        json.loads(lines[0])  # first line parses
        # CSV: header + one row per record
        reader = list(csv.DictReader(io.StringIO(cv.read_text())))
        assert len(reader) == expected, prefix


def test_service_and_recommendations_prefixes_present(tmp_path):
    # The two prefixes the plan calls out explicitly (§3.10 service, §3.11 agentic).
    g, cfg, _ = _run_and_export(tmp_path)
    for prefix in ("service", "recommendations"):
        assert (cfg.out_dir() / "lake" / prefix / "data.ndjson").is_file()


# ── Quick Index corpora ───────────────────────────────────────────────────────

def test_quick_index_catalog_and_corpora(tmp_path):
    g, cfg, _ = _run_and_export(tmp_path)
    qi = cfg.out_dir() / "quick_index"
    catalog = json.loads((qi / "collections.json").read_text())
    corpus = g.ix["corpus"]
    # catalog doc_count sums to the corpus size
    assert sum(c["doc_count"] for c in catalog) == len(corpus)
    # each collection ndjson exists and its line count matches the catalog
    for c in catalog:
        nd = qi / f"{c['collection_id']}.ndjson"
        assert nd.is_file()
        lines = [ln for ln in nd.read_text().splitlines() if ln]
        assert len(lines) == c["doc_count"]


# ── Manifest completeness + determinism ───────────────────────────────────────

def test_manifest_lists_all_artifact_kinds(tmp_path):
    _, cfg, manifest_path = _run_and_export(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    assert manifest["schema_version"] == 2
    kinds = {a["kind"] for a in manifest["artifacts"]}
    assert {"table", "lake", "quick_index_corpus", "quick_index_catalog"} <= kinds


def test_manifest_sha256_matches_every_artifact(tmp_path):
    import hashlib
    _, cfg, manifest_path = _run_and_export(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    base = cfg.out_dir()
    for art in manifest["artifacts"]:
        raw = (base / art["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == art["sha256"], art["path"]


def test_export_deterministic(tmp_path):
    import hashlib
    a = tmp_path / "a"
    b = tmp_path / "b"
    _, ca, ma = _run_and_export(a)
    _, cb, mb = _run_and_export(b)
    ha = hashlib.sha256(ma.read_bytes()).hexdigest()
    hb = hashlib.sha256(mb.read_bytes()).hexdigest()
    assert ha == hb


def test_export_all_tables_only_back_compatible(tmp_path):
    # U1 back-compat: export_all without indexes still writes tables + manifest,
    # and simply omits the Quick Index corpora (no corpus provided).
    cfg = RunConfig(market="us", seed=42)
    cfg.out_dir = lambda: (tmp_path / "us")  # type: ignore[method-assign]
    (tmp_path / "us").mkdir(parents=True, exist_ok=True)
    g = G(cfg, market_mod.build("us"))
    for phase in phase_registry.ordered_phases():
        phase(g)
    manifest_path = export_all(cfg, g.t)  # no indexes
    manifest = json.loads(manifest_path.read_text())
    kinds = {a["kind"] for a in manifest["artifacts"]}
    assert "table" in kinds
    assert "quick_index_corpus" not in kinds  # corpus not passed → not exported
