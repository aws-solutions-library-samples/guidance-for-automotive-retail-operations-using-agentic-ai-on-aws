"""DynamoDB-item-shaped table exporter + manifest writer (contract C3, U1).

Each accumulator table serializes to a JSON ARRAY of plain row dicts (one JSON
object per item — NOT DynamoDB attribute-value maps; a separate loader wraps to
typed maps at load time) under:

    sample-data/synthetic/<market>/tables/<OWNER>_<Table>.json

The manifest is written LAST, after every table artifact is written and hashed,
so a run that fails mid-export leaves no valid manifest (no partial acceptance,
BR3.2). `generated_at` is SEED_NOW-relative, and `run_id` is a pure function of
(market, seed), so two runs with the same seed produce byte-identical output
(NFR1 / BR2.2).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..config import RunConfig, iso
from ..owner_map import owner_matches
from .lake import export_lake
from .quick_index import export_quick_index

MANIFEST_SCHEMA_VERSION = 2


def _canonical_json(obj: object) -> bytes:
    """Deterministic JSON bytes: stable key order, UTF-8, trailing newline.

    sort_keys makes row serialization independent of dict insertion order so
    the bytes (and therefore the sha256) are stable across runs (NFR1).
    """
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2)
    return (text + "\n").encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def export_tables(cfg: RunConfig, tables: dict[str, list[dict]]) -> list[dict]:
    """Write every populated table to tables/<key>.json and return artifact rows.

    Every table key MUST be an OWNER-prefixed name whose prefix matches OWNER_MAP
    (contract C4); an unmapped/mis-prefixed key is a hard error at export time so
    a bad key never reaches an artifact.
    """
    tables_dir = cfg.out_dir() / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)
    artifacts: list[dict] = []
    for key in sorted(tables):
        rows = tables[key]
        if not owner_matches(key):
            raise ValueError(
                f"table key {key!r} is not owner-prefixed per OWNER_MAP (contract C4)"
            )
        data = _canonical_json(rows)
        path = tables_dir / f"{key}.json"
        path.write_bytes(data)
        artifacts.append(
            {
                "path": f"tables/{key}.json",
                "kind": "table",
                "name": key,
                "rows": len(rows),
                "sha256": _sha256(data),
            }
        )
    return artifacts


def write_manifest(cfg: RunConfig, artifacts: list[dict]) -> Path:
    """Write manifest.json LAST (BR3.2). Returns its path."""
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "run_id": f"{cfg.market}-{cfg.seed}",  # pure function of (market, seed) — BR2.2
        "market": cfg.market,
        "seed": str(cfg.seed),
        "seed_now": iso(cfg.now),
        # SEED_NOW-relative, NOT wall-clock, so re-runs stay byte-identical (NFR1/NFR6).
        "generated_at": iso(cfg.now),
        "artifacts": sorted(artifacts, key=lambda a: a["path"]),
    }
    path = cfg.out_dir() / "manifest.json"
    path.write_bytes(_canonical_json(manifest))
    return path


def export_all(cfg: RunConfig, tables: dict[str, list[dict]],
               indexes: dict | None = None) -> Path:
    """Full export: table artifacts (U1), then the S3 lake prefixes + Quick Index
    corpora (U7), then the manifest LAST (BR3.2).

    `indexes` is the generator's `g.ix` cross-reference dict; when present its
    ``corpus`` key (U5) is serialized to the Quick Index prefix. Passing None
    keeps the U1 tables-only behaviour (back-compatible with the skeleton).

    Returns the manifest path. Raises before writing the manifest if any write
    fails, so a failed run leaves no valid manifest (no partial acceptance).
    """
    artifacts = export_tables(cfg, tables)
    # U7: analytics lake (NDJSON/CSV) over the event/agentic tables.
    artifacts.extend(export_lake(cfg, tables))
    # U7: Quick Index corpora from the U5 document corpus (g.ix["corpus"]).
    corpus = (indexes or {}).get("corpus", [])
    artifacts.extend(export_quick_index(cfg, corpus))
    return write_manifest(cfg, artifacts)
