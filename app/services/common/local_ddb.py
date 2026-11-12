"""In-memory stand-in for the boto3 DynamoDB client used by `app.services.common.ddb`.

Used by the offline unit tests (tests/app) and by the dev server's local data
mode (`RETAIL_OPS_LOCAL_DATA=1 python3 -m app.services.devserver`), so every screen
can run end to end without AWS.

Implements exactly the four calls the service layer makes (get_item, put_item,
scan with an equality FilterExpression, query on the partition key) over
attribute-value maps, so every code path that goes through `ddb.*` runs
unchanged and offline. Tables are seeded from the committed synthetic packs
(`sample-data/synthetic/{us,de}/tables/*.json`) with the same key schemas the
L1 loader (`app/services/l1_data/ddb_load.py`) creates in AWS.
"""
from __future__ import annotations

import copy
import datetime as _dt
import json
import re
from pathlib import Path
from typing import Any

from . import ddb
from ..l1_data import ddb_load

_ROOT = Path(__file__).resolve().parents[3]

# The packs' frozen clock (scripts/seed/generate/config.py RunConfig.now). Fifty days after it,
# every appointment is in the past, every open lead has breached its SLA, every journey is idle
# 45-70 days and the delivery board is empty: a demo that reads as abandoned. seed_markets can
# shift timestamps so this instant becomes "now" (the devserver does, RETAIL_OPS_LOCAL_REBASE).
SEED_NOW = _dt.datetime(2026, 9, 10, 9, 0, tzinfo=_dt.UTC)
# Full timestamps only (date + time). Date-only values stay: incentive validity windows and
# compliance review dates are real-world or statutory dates, and source_ref URLs carry dates.
_TS_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?(Z|[+-]\d{2}:\d{2})?$")


def _shift_ts(s: str, shift: _dt.timedelta) -> str:
    m = _TS_RE.match(s)
    if not m:
        return s
    try:
        d = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s
    out = (d + shift).replace(microsecond=0).isoformat()
    return out.replace("+00:00", "Z") if s.endswith("Z") else out


def rebase_timestamps(value: Any, shift: _dt.timedelta) -> Any:
    """Return `value` with every ISO date-time string (any depth) moved by `shift`."""
    if isinstance(value, str):
        return _shift_ts(value, shift) if "T" in value and len(value) >= 16 else value
    if isinstance(value, list):
        return [rebase_timestamps(v, shift) for v in value]
    if isinstance(value, dict):
        return {k: rebase_timestamps(v, shift) for k, v in value.items()}
    return value
_ITEM_KEY_TABLES = frozenset({
    "PLAT_Config", "PLAT_ConfigAutonomy", "PLAT_ConfigGuardrails",
    "PLAT_ConfigNotifications", "PLAT_ConfigNurture", "PLAT_ConfigSuggestions",
    "PLAT_EntityMap",
})

# Runtime-only tables: never seeded (no pack file, nothing in the L1 loader),
# written only at runtime, so their key schema is declared here for local
# get/scan to work. In AWS the owning deployer creates them (deploy_memory.py
# for PLAT_AgentMemory).
_RUNTIME_PK: dict[str, str] = {
    "PLAT_AgentMemory": "memory_id",   # platform/memory.py
}


def _split_prefixed(bare: str) -> tuple[str, str]:
    owner, _, name = bare.partition("_")
    return owner, name


def key_schema(bare: str, rows: list[dict[str, Any]] | None = None) -> tuple[str, ...]:
    """(hash,) or (hash, sort) attribute names for a bare table name."""
    if bare in _RUNTIME_PK:
        return (_RUNTIME_PK[bare],)
    if bare in ddb_load.COMPOSITE_KEYS:
        spec = ddb_load.COMPOSITE_KEYS[bare]
        return (spec["hash"], spec["sort"])
    if rows:
        # same resolution as the L1 loader: override only if present + unique
        return (ddb_load.resolve_pk_field(bare, rows, _ITEM_KEY_TABLES, _split_prefixed),)
    if bare in ddb_load.PK_OVERRIDES:
        return (ddb_load.PK_OVERRIDES[bare],)
    return (ddb_load._singular_id_field(bare, _split_prefixed),)


class FakeDynamoDB:
    def __init__(self, env_prefix: str = ddb.DEFAULT_ENV_PREFIX) -> None:
        self.env_prefix = env_prefix
        self.tables: dict[str, dict[tuple, dict[str, Any]]] = {}
        self.keys: dict[str, tuple[str, ...]] = {}

    # --- seeding ------------------------------------------------------------
    def _bare(self, table_name: str) -> str:
        return table_name[len(self.env_prefix):] if table_name.startswith(self.env_prefix) else table_name

    def seed(self, bare: str, rows: list[dict[str, Any]]) -> None:
        if bare in ddb_load.COMPOSITE_KEYS and "sort_field" not in ddb_load.COMPOSITE_KEYS[bare]:
            rows = ddb_load._add_sort_key(rows, ddb_load.COMPOSITE_KEYS[bare])
        ks = self.keys.setdefault(bare, key_schema(bare, rows))
        tbl = self.tables.setdefault(bare, {})
        for r in rows:
            tbl[tuple(str(r.get(k)) for k in ks)] = {k: ddb.to_attr(v) for k, v in r.items()}

    def seed_markets(self, markets: tuple[str, ...] = ("us", "de"), rebase_to: _dt.datetime | None = None) -> None:
        """Load the committed packs. rebase_to (optional) shifts every timestamp so the pack's frozen
        clock (SEED_NOW) lands on that instant — the demo sandbox's "today"."""
        shift = (rebase_to - SEED_NOW) if rebase_to else None
        for m in markets:
            for path in sorted((_ROOT / "sample-data" / "synthetic" / m / "tables").glob("*.json")):
                rows = json.loads(path.read_text())
                if rows:
                    if shift:
                        rows = rebase_timestamps(rows, shift)
                    # Same market stamp the L1 loader applies, so market-scoped
                    # reference tables have a complete composite key here too.
                    self.seed(path.stem, ddb_load.stamp_market(path.stem, rows, m))

    def rows(self, bare: str) -> list[dict[str, Any]]:
        return [ddb.item_to_dict(i) for i in self.tables.get(bare, {}).values()]

    # --- boto3 surface ------------------------------------------------------
    def _table(self, table_name: str) -> tuple[str, dict[tuple, dict[str, Any]]]:
        bare = self._bare(table_name)
        self.keys.setdefault(bare, key_schema(bare))
        return bare, self.tables.setdefault(bare, {})

    def get_item(self, TableName: str, Key: dict[str, Any], **_: Any) -> dict[str, Any]:
        bare, tbl = self._table(TableName)
        k = tuple(str(ddb.from_attr(Key[a])) for a in self.keys[bare] if a in Key)
        item = tbl.get(k)
        return {"Item": copy.deepcopy(item)} if item else {}

    def put_item(self, TableName: str, Item: dict[str, Any], **_: Any) -> dict[str, Any]:
        bare, tbl = self._table(TableName)
        missing = [a for a in self.keys[bare] if a not in Item]
        if missing:
            raise ValueError(f"put_item on {bare}: missing key attribute(s) {missing}")
        tbl[tuple(str(ddb.from_attr(Item[a])) for a in self.keys[bare])] = copy.deepcopy(Item)
        return {}

    def scan(self, TableName: str, FilterExpression: str | None = None,
             ExpressionAttributeNames: dict[str, str] | None = None,
             ExpressionAttributeValues: dict[str, Any] | None = None, **_: Any) -> dict[str, Any]:
        _bare, tbl = self._table(TableName)
        items = list(tbl.values())
        if FilterExpression:
            attr = (ExpressionAttributeNames or {})["#a"]
            want = ddb.from_attr((ExpressionAttributeValues or {})[":v"])
            items = [i for i in items if attr in i and ddb.from_attr(i[attr]) == want]
        return {"Items": copy.deepcopy(items)}

    def query(self, TableName: str, KeyConditionExpression: str,
              ExpressionAttributeNames: dict[str, str],
              ExpressionAttributeValues: dict[str, Any], **_: Any) -> dict[str, Any]:
        bare, tbl = self._table(TableName)
        attr = ExpressionAttributeNames["#h"]
        want = ddb.from_attr(ExpressionAttributeValues[":v"])
        items = [i for i in tbl.values() if attr in i and ddb.from_attr(i[attr]) == want]
        if len(self.keys[bare]) == 2:
            sk = self.keys[bare][1]
            items.sort(key=lambda i: str(ddb.from_attr(i.get(sk, {"S": ""}))))
        return {"Items": copy.deepcopy(items)}


class _OfflineClient:
    """Stands in for every non-DynamoDB boto3 client in local mode: any call
    raises, so the code's offline fallbacks run and nothing reaches real AWS
    (SES, SNS, Bedrock, EventBridge, Step Functions, …)."""

    def __init__(self, service: str) -> None:
        self._service = service

    def __getattr__(self, name: str):
        def _blocked(*_a, **_k):
            raise RuntimeError(f"local data mode: AWS {self._service}.{name} is disabled")
        return _blocked


def block_aws() -> None:
    """Make boto3 unable to reach AWS for the rest of the process."""
    import os
    os.environ["AWS_ENDPOINT_URL"] = "http://127.0.0.1:9"      # belt and braces
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "local")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "local")
    try:
        import boto3
    except ImportError:
        return
    boto3.client = lambda service, *a, **k: _OfflineClient(service)  # type: ignore[assignment]

    class _OfflineSession:
        def __init__(self, *a, **k):
            pass

        def client(self, service, *a, **k):
            return _OfflineClient(service)

    boto3.Session = _OfflineSession  # type: ignore[assignment]
    boto3.session.Session = _OfflineSession  # type: ignore[assignment]


def install(markets: tuple[str, ...] = ("us", "de"), rebase_to: _dt.datetime | None = None) -> FakeDynamoDB:
    """Seed an in-memory store, route every `ddb.*` call to it, and block every
    other AWS client (process-wide). rebase_to: see seed_markets."""
    block_aws()
    fake = FakeDynamoDB()
    fake.seed_markets(markets, rebase_to=rebase_to)
    ddb._client.cache_clear()
    ddb._client = lambda: fake  # type: ignore[assignment]
    return fake
