"""DynamoDB item-shape loader (U7): the load-time wrap for the plain-JSON tables.

Contract C3: the table artifacts under `tables/<OWNER>_<Table>.json` are plain
JSON row-dict arrays (diffable, snapshot-friendly, reusable by Quick/analytics),
NOT DynamoDB attribute-value maps. This module is the loader that wraps a plain
row into a DynamoDB item at load time, and resolves the load-time partition key.

Two responsibilities:

  to_ddb_item(row)   — wrap a plain row dict into a DynamoDB attribute-value map
                       ({"S": ...} / {"N": ...} / {"BOOL": ...} / {"NULL": true}
                       / {"L": [...]} / {"M": {...}}), the shape a DynamoDB
                       BatchWriteItem / PutItem expects.
  load_pk(table,row) — the partition key VALUE to load the row under. For the
                       PLAT config family and PLAT_EntityMap the spec is
                       internally inconsistent on the natural key, so those rows
                       carry a deterministic composite `item_key` that IS the
                       load-time PK (contract C1). Every other table uses its
                       conventional `<lower(table)>_id` / first *_id field.

Pure functions, no I/O; the JSON tables remain the source of truth.
"""
from __future__ import annotations

from ..owner_map import split_prefixed

# Tables whose load-time partition key is the composite `item_key` (contract C1).
# These are the versioned PLAT config family + the entity map, where config_key /
# version / market collide on any single natural field.
ITEM_KEY_TABLES = frozenset({
    "PLAT_Config", "PLAT_ConfigAutonomy", "PLAT_ConfigGuardrails",
    "PLAT_ConfigNotifications", "PLAT_ConfigNurture", "PLAT_ConfigSuggestions",
    "PLAT_EntityMap",
})


def to_ddb_item(row: dict) -> dict:
    """Wrap a plain row dict into a DynamoDB attribute-value map."""
    return {k: _to_attr(v) for k, v in row.items()}


def _to_attr(v: object) -> dict:
    # bool BEFORE int/float — bool is a subclass of int in Python.
    if v is None:
        return {"NULL": True}
    if isinstance(v, bool):
        return {"BOOL": v}
    if isinstance(v, (int, float)):
        # DynamoDB Number is a string-encoded numeric.
        return {"N": repr(v) if isinstance(v, float) else str(v)}
    if isinstance(v, str):
        return {"S": v}
    if isinstance(v, list):
        return {"L": [_to_attr(x) for x in v]}
    if isinstance(v, dict):
        return {"M": {k: _to_attr(x) for k, x in v.items()}}
    # Fallback: stringify anything exotic so a load never crashes.
    return {"S": str(v)}


def _default_pk_field(table: str) -> str:
    """`<lower(bare-table)>_id` (e.g. DEALER_Deals -> deal_id-ish). Callers fall
    back to the first *_id field when this exact field is absent."""
    _owner, name = split_prefixed(table)
    # snake_case the CamelCase bare name, then singular-ish + _id is unreliable,
    # so callers use first-*_id fallback; this is only a best-effort primary guess.
    snake = _camel_to_snake(name)
    return f"{snake}_id"


def _camel_to_snake(name: str) -> str:
    out: list[str] = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0:
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def load_pk(table: str, row: dict) -> str:
    """The partition-key VALUE this row loads under.

    - item_key tables (contract C1): the composite `item_key`.
    - otherwise: the conventional `<table>_id` if present, else the first key
      ending in `_id`, else a stable fallback string. Raises only if the row is
      empty (a row with no fields has no identity)."""
    if table in ITEM_KEY_TABLES:
        ik = row.get("item_key")
        if ik is None:
            raise ValueError(f"{table} row is missing its composite item_key (contract C1)")
        return str(ik)
    guess = _default_pk_field(table)
    if guess in row and row[guess] is not None:
        return str(row[guess])
    for k, v in row.items():
        if k.endswith("_id") and v is not None:
            return str(v)
    # Some reference/config rows key on a non-_id field; fall back deterministically.
    for k in ("collection_id", "code", "config_key", "dtc", "labor_op_id"):
        if k in row and row[k] is not None:
            return str(row[k])
    raise ValueError(f"cannot resolve a load-time PK for a {table} row: {sorted(row)[:6]}")
