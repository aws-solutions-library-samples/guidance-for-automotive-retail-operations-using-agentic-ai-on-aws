"""U1 skeleton tests (test-after): determinism, golden snapshot, and the
minimal blocking invariants over the generate -> export -> validate spine.

Run this unit only:
    python3 -m pytest tests/test_u1_skeleton.py -q
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.seed.generate.config import RunConfig  # noqa: E402
from scripts.seed.generate import market as market_mod  # noqa: E402
from scripts.seed.generate.core import G  # noqa: E402
from scripts.seed.generate.exporters import export_all  # noqa: E402
from scripts.seed.generate.validate import validate_market  # noqa: E402

GOLDEN = ROOT / "tests" / "golden" / "us_manifest_hashes.json"


def _generate(out_root: Path, market: str = "us", seed: int = 42) -> Path:
    """Run the skeleton generate->export into out_root/<market>; return market dir."""
    import scripts.seed.generate.config as cfg_mod

    # Redirect the exporter's output root to a temp dir for isolation.
    orig = cfg_mod.OUT_ROOT
    cfg_mod.OUT_ROOT = out_root
    try:
        cfg = RunConfig(market=market, seed=seed)
        # RunConfig.out_dir() reads the module OUT_ROOT indirectly via config;
        # rebuild it to point at the temp root.
        m = market_mod.build(market)
        g = G(cfg, m)
        g.gen_org_users()
        # cfg.out_dir() uses OUT_ROOT captured at import; compute explicitly.
        market_dir = out_root / market
        _export_to(cfg, g, market_dir)
        return market_dir
    finally:
        cfg_mod.OUT_ROOT = orig


def _export_to(cfg, g, market_dir: Path):
    """Export g.t into an explicit market_dir (test helper mirroring export_all)."""
    from scripts.seed.generate.exporters import dynamodb as ex

    # Monkeypatch-free: temporarily override cfg.out_dir via a thin wrapper.
    tables_dir = market_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)
    artifacts = []
    for key in sorted(g.t):
        rows = g.t[key]
        data = ex._canonical_json(rows)
        (tables_dir / f"{key}.json").write_bytes(data)
        artifacts.append(
            {"path": f"tables/{key}.json", "kind": "table", "name": key,
             "rows": len(rows), "sha256": ex._sha256(data)}
        )
    manifest = {
        "schema_version": ex.MANIFEST_SCHEMA_VERSION,
        "run_id": f"{cfg.market}-{cfg.seed}",
        "market": cfg.market, "seed": str(cfg.seed),
        "seed_now": _iso(cfg.now), "generated_at": _iso(cfg.now),
        "artifacts": sorted(artifacts, key=lambda a: a["path"]),
    }
    (market_dir / "manifest.json").write_bytes(ex._canonical_json(manifest))


def _iso(dt):
    from scripts.seed.generate.config import iso
    return iso(dt)


def _manifest_hashes(market_dir: Path) -> dict[str, str]:
    man = json.loads((market_dir / "manifest.json").read_text(encoding="utf-8"))
    return {a["path"]: a["sha256"] for a in man["artifacts"]}


# ── NFR1 / BR2.2: determinism ─────────────────────────────────────────────────
def test_determinism_same_seed_identical_hashes(tmp_path):
    a = _generate(tmp_path / "a", "us", 42)
    b = _generate(tmp_path / "b", "us", 42)
    assert _manifest_hashes(a) == _manifest_hashes(b)


# ── Golden snapshot ────────────────────────────────────────────────────────────
def test_golden_snapshot_us(tmp_path):
    market_dir = _generate(tmp_path / "g", "us", 42)
    hashes = _manifest_hashes(market_dir)
    assert GOLDEN.is_file(), "golden fixture missing; regenerate tests/golden/us_manifest_hashes.json"
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert hashes == golden


# ── BR1.1: manifest present + sha256 integrity (happy path) ───────────────────
def test_manifest_integrity_pass(tmp_path):
    market_dir = _generate(tmp_path / "m", "us", 42)
    rep = validate_market(market_dir)
    assert rep.passed, rep.failures


def test_manifest_missing_fails(tmp_path):
    market_dir = _generate(tmp_path / "m", "us", 42)
    (market_dir / "manifest.json").unlink()
    rep = validate_market(market_dir)
    assert not rep.passed
    assert any(f.startswith("BR1.1") for f in rep.failures)


def test_sha256_mismatch_fails(tmp_path):
    market_dir = _generate(tmp_path / "m", "us", 42)
    # Corrupt one table artifact after the manifest was written.
    some_table = next((market_dir / "tables").glob("*.json"))
    some_table.write_text(some_table.read_text(encoding="utf-8") + "\n ", encoding="utf-8")
    rep = validate_market(market_dir)
    assert not rep.passed
    assert any(f.startswith("BR1.1") for f in rep.failures)


# ── BR1.2: owner-prefix ────────────────────────────────────────────────────────
def test_owner_prefix_mismatch_fails(tmp_path):
    market_dir = _generate(tmp_path / "m", "us", 42)
    # Rename a table artifact + its manifest entry to a mis-prefixed name.
    man_path = market_dir / "manifest.json"
    man = json.loads(man_path.read_text(encoding="utf-8"))
    for art in man["artifacts"]:
        if art["name"] == "PLAT_Users":
            art["name"] = "OEM_Users"  # Users belongs to PLAT, not OEM
            break
    man_path.write_text(json.dumps(man, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    rep = validate_market(market_dir)
    assert not rep.passed
    assert any(f.startswith("BR1.2") for f in rep.failures)


# ── BR1.3: no retired identifiers ──────────────────────────────────────────────
def test_retired_identifier_fails(tmp_path):
    market_dir = _generate(tmp_path / "m", "us", 42)
    some_table = next((market_dir / "tables").glob("*.json"))
    rows = json.loads(some_table.read_text(encoding="utf-8"))
    rows.append({"node_id": "SSO_Injected", "type": "dealer"})
    data = json.dumps(rows, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    some_table.write_bytes(data.encode("utf-8"))
    # Refresh the manifest hash so BR1.1 does not mask BR1.3.
    _refresh_hash(market_dir, some_table)
    rep = validate_market(market_dir)
    assert not rep.passed
    assert any(f.startswith("BR1.3") for f in rep.failures)


# ── BR1.4: no real-PII; all foundation domains PASS ───────────────────────────
def test_foundation_emails_pass_br14(tmp_path):
    # Both markets' real emails must validate clean (the R-01 regression guard).
    for market in ("us", "de"):
        market_dir = _generate(tmp_path / market, market, 42)
        rep = validate_market(market_dir)
        assert rep.passed, (market, rep.failures)


def test_real_domain_email_fails_br14(tmp_path):
    market_dir = _generate(tmp_path / "m", "us", 42)
    users = market_dir / "tables" / "PLAT_Users.json"
    rows = json.loads(users.read_text(encoding="utf-8"))
    rows[0]["email"] = "real.person@gmail.com"  # live domain, not allowlisted
    users.write_bytes((json.dumps(rows, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))
    _refresh_hash(market_dir, users)
    rep = validate_market(market_dir)
    assert not rep.passed
    assert any(f.startswith("BR1.4") for f in rep.failures)


# ── BR1.5: no credential strings; synthetic demo password allowlisted ─────────
def test_synthetic_password_passes_br15(tmp_path):
    market_dir = _generate(tmp_path / "m", "us", 42)
    rep = validate_market(market_dir)  # users carry test_password "IrpoDemo2026!"
    assert rep.passed, rep.failures


def test_aws_key_fails_br15(tmp_path):
    market_dir = _generate(tmp_path / "m", "us", 42)
    users = market_dir / "tables" / "PLAT_Users.json"
    rows = json.loads(users.read_text(encoding="utf-8"))
    rows[0]["leaked"] = "AKIAIOSFODNN7EXAMPLE"
    users.write_bytes((json.dumps(rows, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))
    _refresh_hash(market_dir, users)
    rep = validate_market(market_dir)
    assert not rep.passed
    assert any(f.startswith("BR1.5") for f in rep.failures)


# ── BR1.7: referential integrity ──────────────────────────────────────────────
def test_dangling_user_scope_fails_br17(tmp_path):
    market_dir = _generate(tmp_path / "m", "us", 42)
    users = market_dir / "tables" / "PLAT_Users.json"
    rows = json.loads(users.read_text(encoding="utf-8"))
    rows[0]["scope_node_id"] = "NODE-DOES-NOT-EXIST"
    users.write_bytes((json.dumps(rows, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))
    _refresh_hash(market_dir, users)
    rep = validate_market(market_dir)
    assert not rep.passed
    assert any(f.startswith("BR1.7") for f in rep.failures)


# ── BR4.1: CLI exit semantics ──────────────────────────────────────────────────
def test_cli_exit_zero_on_pass(tmp_path, monkeypatch):
    import scripts.seed.generate.config as cfg_mod
    from scripts.seed.generate import generate_synthetic as gs

    monkeypatch.setattr(cfg_mod, "OUT_ROOT", tmp_path)
    # RunConfig.out_dir reads OUT_ROOT via the config module attribute at call time.
    rc = gs.main(["--market", "us", "--seed", "42", "--out", "local"])
    assert rc == 0


def _refresh_hash(market_dir: Path, changed_file: Path):
    """After mutating a table file, update its manifest sha256 so a targeted
    invariant (not BR1.1) is what fails."""
    import hashlib
    man_path = market_dir / "manifest.json"
    man = json.loads(man_path.read_text(encoding="utf-8"))
    rel = str(changed_file.relative_to(market_dir))
    digest = hashlib.sha256(changed_file.read_bytes()).hexdigest()
    for art in man["artifacts"]:
        if art["path"] == rel:
            art["sha256"] = digest
            art["rows"] = len(json.loads(changed_file.read_text(encoding="utf-8")))
    man_path.write_text(json.dumps(man, indent=2, sort_keys=True) + "\n", encoding="utf-8")
