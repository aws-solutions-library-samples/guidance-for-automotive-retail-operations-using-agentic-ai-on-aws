"""Quick Index corpora exporter (U7): serialize the U5 document corpus.

U5 (corpora) builds the agent-grounding document corpus in-memory at
`g.ix["corpus"]` and the `PLAT_KnowledgeCollections` catalog rows. U7 serializes
that corpus to the Quick Index prefix so the retrieval layer (A3/A6/A7 grounding)
has documents to index. Local, infra-free layout:

    quick_index/collections.json          the collection catalog (one per kind)
    quick_index/<collection_id>.ndjson    the documents in that collection

Documents are grouped by `collection_id` (KC-<MKT>-<kind>); each NDJSON line is a
canonical JSON doc (sorted keys → stable bytes, NFR1). Nothing calls AWS; this is
the infra-free stand-in for the Quick Index ingest.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..config import RunConfig


def _canonical_line(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def export_quick_index(cfg: RunConfig, corpus: list[dict]) -> list[dict]:
    """Write the corpus grouped by collection. Returns artifact rows.

    `corpus` is `g.ix["corpus"]` (may be empty/absent if U5 did not run, in which
    case nothing is written and no artifact is returned)."""
    if not corpus:
        return []
    qi_dir = cfg.out_dir() / "quick_index"
    qi_dir.mkdir(parents=True, exist_ok=True)
    artifacts: list[dict] = []

    by_collection: dict[str, list[dict]] = {}
    for doc in corpus:
        by_collection.setdefault(doc["collection_id"], []).append(doc)

    # Catalog: one entry per collection with its doc count.
    catalog = [
        {"collection_id": cid, "kind": docs[0]["kind"], "market_id": docs[0]["market_id"],
         "doc_count": len(docs), "index_target": "quick_index"}
        for cid, docs in sorted(by_collection.items())
    ]
    cat_bytes = (json.dumps(catalog, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    (qi_dir / "collections.json").write_bytes(cat_bytes)
    artifacts.append({
        "path": "quick_index/collections.json", "kind": "quick_index_catalog",
        "collections": len(catalog), "sha256": _sha256(cat_bytes),
    })

    for cid, docs in sorted(by_collection.items()):
        lines = [_canonical_line(d) for d in docs]
        data = ("\n".join(lines) + "\n").encode("utf-8")
        (qi_dir / f"{cid}.ndjson").write_bytes(data)
        artifacts.append({
            "path": f"quick_index/{cid}.ndjson", "kind": "quick_index_corpus",
            "collection_id": cid, "docs": len(docs), "sha256": _sha256(data),
        })
    return artifacts
