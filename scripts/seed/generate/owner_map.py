"""Authoritative table -> owner-prefix resolution (contract C4).

The single source of truth for the OWNER_MAP is `scripts/apply_owner_prefixes.py`.
This module loads that map by file path (the script guards its CLI under
`__main__`, so importing it has no side effects) and exposes:

  - OWNER_MAP:        {owner_prefix: [table_name, ...]}
  - NAME_TO_OWNER:    {table_name: owner_prefix}
  - owner_of(table):  owner prefix for a bare table name  (hard error if unmapped)
  - prefixed(table):  "<OWNER>_<Table>"
  - split_prefixed(k):("<OWNER>", "<Table>") for an already-prefixed key
  - owner_matches(k): True iff a "<OWNER>_<Table>" key's prefix matches the map

A table with no OWNER_MAP entry is a hard error (contract C4: the mapping is
exact and total). No table name is duplicated across owners.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

# scripts/seed/generate/owner_map.py -> repo root is parents[3]
_ROOT = Path(__file__).resolve().parents[3]
_APPLY = _ROOT / "scripts" / "apply_owner_prefixes.py"

OWNERS = ("OEM", "DEALER", "CUST", "PLAT")


def _load_owner_map() -> dict[str, list[str]]:
    spec = importlib.util.spec_from_file_location("_retail_ops_apply_owner_prefixes", _APPLY)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot load OWNER_MAP source at {_APPLY}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    owner_map = getattr(module, "OWNER_MAP", None)
    if not isinstance(owner_map, dict) or not owner_map:
        raise RuntimeError("OWNER_MAP missing or empty in apply_owner_prefixes.py")
    return {owner: list(tables) for owner, tables in owner_map.items()}


OWNER_MAP: dict[str, list[str]] = _load_owner_map()

# name -> owner. Build eagerly and reject any duplicate table name across owners.
NAME_TO_OWNER: dict[str, str] = {}
for _owner, _tables in OWNER_MAP.items():
    for _t in _tables:
        if _t in NAME_TO_OWNER:
            raise RuntimeError(
                f"OWNER_MAP invariant violated: table {_t!r} maps to both "
                f"{NAME_TO_OWNER[_t]} and {_owner}"
            )
        NAME_TO_OWNER[_t] = _owner


class OwnerMapError(ValueError):
    """Raised when a table name has no OWNER_MAP entry (contract C4)."""


def owner_of(table: str) -> str:
    """Owner prefix for a bare table name. Hard error if unmapped (C4)."""
    try:
        return NAME_TO_OWNER[table]
    except KeyError:
        raise OwnerMapError(
            f"table {table!r} has no OWNER_MAP entry (contract C4: mapping is total)"
        ) from None


def prefixed(table: str) -> str:
    """'<OWNER>_<Table>' for a bare table name."""
    return f"{owner_of(table)}_{table}"


def split_prefixed(key: str) -> tuple[str, str]:
    """Split an already-prefixed accumulator key into (owner, table).

    Splits on the FIRST underscore, since table names never lead with one.
    """
    owner, _, table = key.partition("_")
    return owner, table


def owner_matches(key: str) -> bool:
    """True iff an already-prefixed '<OWNER>_<Table>' key's prefix is the
    owner OWNER_MAP assigns to <Table>. False for unmapped or mis-prefixed
    keys (drives validate.py BR1.2)."""
    owner, table = split_prefixed(key)
    return NAME_TO_OWNER.get(table) == owner
