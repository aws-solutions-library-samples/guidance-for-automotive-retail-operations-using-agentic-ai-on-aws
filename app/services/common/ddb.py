"""L2 — DynamoDB access primitives shared by the service layer.

Thin wrapper over boto3 DynamoDB that:
  * resolves the env-prefixed table name (`retail-ops-dev-<Owner>_<Table>`),
  * deserializes DynamoDB attribute-value maps back into plain Python dicts
    (the inverse of the generator's `to_ddb_item`), and
  * offers the two access patterns the read models need: get-by-key and
    query/scan-by-attribute.

Deserialization is deliberately the inverse of `scripts/seed/generate/exporters/
ddb_loader.py:to_ddb_item` so a row written by the loader round-trips to the
same plain dict the JSON artifacts hold.
"""
from __future__ import annotations

import contextlib
import contextvars
import os
from collections.abc import Iterator
from functools import lru_cache
from typing import Any

from . import envname  # noqa: E402

DEFAULT_ENV_PREFIX = envname.table_prefix()      # RETAIL_OPS_ENV_PREFIX, else retail-ops-<env>-
DEFAULT_REGION = os.environ.get("AWS_REGION", "us-east-1")


# Request-scoped read memo (see read_memo): one read model that joins the same tables from
# several helpers scans each table once instead of once per helper.
_MEMO: contextvars.ContextVar[dict | None] = contextvars.ContextVar("ddb_read_memo", default=None)


@contextlib.contextmanager
def read_memo() -> Iterator[None]:
    """Within this block, identical scans are served from memory (rows are copied out, so a
    caller mutating its rows never changes another's). A write to a table drops that table's
    memo. Use around one read model; never across requests."""
    token = _MEMO.set({})
    try:
        yield
    finally:
        _MEMO.reset(token)


def _memo_get(key: tuple) -> list[dict[str, Any]] | None:
    memo = _MEMO.get()
    if memo is None or key not in memo:
        return None
    return [dict(r) for r in memo[key]]


def _memo_put(key: tuple, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    memo = _MEMO.get()
    if memo is not None:
        memo[key] = [dict(r) for r in rows]
    return rows


def _memo_drop(name: str) -> None:
    memo = _MEMO.get()
    if memo:
        for k in [k for k in memo if k[1] == name]:
            del memo[k]


@lru_cache(maxsize=1)
def _client():
    import boto3  # lazy: keeps import-time cheap and offline-friendly
    return boto3.client("dynamodb", region_name=DEFAULT_REGION)


def table_name(bare: str, env_prefix: str | None = None) -> str:
    """`retail-ops-<env>-DEALER_Journeys` for bare name `DEALER_Journeys`."""
    return f"{env_prefix or DEFAULT_ENV_PREFIX}{bare}"


# --- deserialize DynamoDB attribute-value maps -> plain Python -------------

def from_attr(attr: dict[str, Any]) -> Any:
    """Inverse of ddb_loader.to_ddb_item's `_to_attr`."""
    (tag, val), = attr.items()
    if tag == "S":
        return val
    if tag == "N":
        # Preserve int vs float the way the source did.
        return int(val) if val.lstrip("-").isdigit() else float(val)
    if tag == "BOOL":
        return val
    if tag == "NULL":
        return None
    if tag == "L":
        return [from_attr(x) for x in val]
    if tag == "M":
        return {k: from_attr(x) for k, x in val.items()}
    if tag in ("SS", "NS"):
        return list(val)
    return val


def item_to_dict(item: dict[str, Any]) -> dict[str, Any]:
    return {k: from_attr(v) for k, v in item.items()}


def to_attr(value: Any) -> dict[str, Any]:
    """Full forward serializer (scalars, lists, maps, null) — inverse of from_attr.
    Used for key values AND for writing full items with nested structures."""
    if value is None:
        return {"NULL": True}
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, (int, float)):
        return {"N": repr(value) if isinstance(value, float) else str(value)}
    if isinstance(value, str):
        return {"S": value}
    if isinstance(value, list):
        return {"L": [to_attr(x) for x in value]}
    if isinstance(value, dict):
        return {"M": {k: to_attr(x) for k, x in value.items()}}
    return {"S": str(value)}


# --- access patterns --------------------------------------------------------

def get_item(bare: str, key: dict[str, Any], env_prefix: str | None = None) -> dict[str, Any] | None:
    resp = _client().get_item(
        TableName=table_name(bare, env_prefix),
        Key={k: to_attr(v) for k, v in key.items()},
    )
    item = resp.get("Item")
    return item_to_dict(item) if item else None


def scan_filter(bare: str, attr: str, value: Any, env_prefix: str | None = None,
                limit: int | None = None) -> list[dict[str, Any]]:
    """Scan a table returning rows whose `attr == value`.

    A Scan is acceptable for the small tables and the read-model prototype; the
    production refinement is a GSI on the filtered attribute (noted for L4).
    """
    name = table_name(bare, env_prefix)
    mkey = ("filter", name, attr, repr(value), limit)
    hit = _memo_get(mkey)
    if hit is None and limit is None:
        whole = _memo_get(("all", name, None))     # the table is already in memory: filter it there
        if whole is not None:
            hit = [r for r in whole if r.get(attr) == value]
    if hit is not None:
        return hit
    client = _client()
    out: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {
        "TableName": name,
        "FilterExpression": "#a = :v",
        "ExpressionAttributeNames": {"#a": attr},
        "ExpressionAttributeValues": {":v": to_attr(value)},
    }
    while True:
        resp = client.scan(**kwargs)
        out.extend(item_to_dict(i) for i in resp.get("Items", []))
        if limit is not None and len(out) >= limit:
            return _memo_put(mkey, out[:limit])
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    return _memo_put(mkey, out)


def query_hash(bare: str, hash_attr: str, hash_value: Any, env_prefix: str | None = None) -> list[dict[str, Any]]:
    """Query all items sharing a partition-key value (composite-key tables)."""
    client = _client()
    name = table_name(bare, env_prefix)
    out: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {
        "TableName": name,
        "KeyConditionExpression": "#h = :v",
        "ExpressionAttributeNames": {"#h": hash_attr},
        "ExpressionAttributeValues": {":v": to_attr(hash_value)},
    }
    while True:
        resp = client.query(**kwargs)
        out.extend(item_to_dict(i) for i in resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    return out


def put_item(bare: str, row: dict[str, Any], env_prefix: str | None = None) -> None:
    """Write a plain row dict as a DynamoDB item (agentic-write path, L6)."""
    name = table_name(bare, env_prefix)
    _memo_drop(name)
    _client().put_item(TableName=name, Item={k: to_attr(v) for k, v in row.items()})


def scan_all(bare: str, env_prefix: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    name = table_name(bare, env_prefix)
    mkey = ("all", name, limit)
    hit = _memo_get(mkey)
    if hit is not None:
        return hit
    client = _client()
    out: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"TableName": name}
    while True:
        resp = client.scan(**kwargs)
        out.extend(item_to_dict(i) for i in resp.get("Items", []))
        if limit is not None and len(out) >= limit:
            return _memo_put(mkey, out[:limit])
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    return _memo_put(mkey, out)


def scan_projected(bare: str, fields: tuple[str, ...], key: str, env_prefix: str | None = None,
                   segments: int = 4) -> list[dict[str, Any]]:
    """Every row of a large table, but only `fields`, read as a parallel scan.

    For a read that needs a few attributes of a big table (the agent-trace log is tens of
    thousands of rows with long payloads): projection cuts the bytes moved and decoded, and
    the segments read in parallel. Rows are de-duplicated on `key`."""
    from concurrent.futures import ThreadPoolExecutor
    name = table_name(bare, env_prefix)
    names = {f"#f{i}": f for i, f in enumerate(fields)}
    proj = ", ".join(names)

    def one(seg: int) -> list[dict[str, Any]]:
        client = _client()
        out: list[dict[str, Any]] = []
        kw: dict[str, Any] = {"TableName": name, "ProjectionExpression": proj,
                              "ExpressionAttributeNames": names, "Segment": seg, "TotalSegments": segments}
        while True:
            resp = client.scan(**kw)
            out.extend(item_to_dict(i) for i in resp.get("Items", []))
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                return out
            kw["ExclusiveStartKey"] = lek

    with ThreadPoolExecutor(max_workers=segments) as pool:
        parts = list(pool.map(one, range(segments)))
    seen: dict[Any, dict[str, Any]] = {}
    for part in parts:
        for row in part:
            seen.setdefault(row.get(key), row)
    return list(seen.values())
