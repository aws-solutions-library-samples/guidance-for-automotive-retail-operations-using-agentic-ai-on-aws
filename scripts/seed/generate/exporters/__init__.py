"""Exporters: serialize the in-memory accumulator (G.t / G.ix) to local artifacts.

U1 (skeleton) implements the DynamoDB-item-shaped JSON tables path + manifest.
U7 adds the S3 lake (NDJSON/CSV) prefixes, the Quick Index corpora path, and the
DynamoDB item-shape loader (contract C3 load-time wrap + C1 item_key PK).
"""
from __future__ import annotations

from .dynamodb import export_tables, write_manifest, export_all
from .lake import export_lake, LAKE_PREFIXES
from .quick_index import export_quick_index
from .ddb_loader import to_ddb_item, load_pk, ITEM_KEY_TABLES

__all__ = [
    "export_tables", "write_manifest", "export_all",
    "export_lake", "LAKE_PREFIXES",
    "export_quick_index",
    "to_ddb_item", "load_pk", "ITEM_KEY_TABLES",
]
