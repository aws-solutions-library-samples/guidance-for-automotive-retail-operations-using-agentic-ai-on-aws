"""Demo reset — put the stack's tables back to the seed, rebased to today.

Every rehearsal writes rows the seed does not have (journeys, handoffs, bookings, approvals,
traces), and a plain data reload cannot take them away: it overwrites the seeded rows and
leaves everything else. Worse, event logs are keyed on their (rebased) time, so each reload
on a new day added a second copy of every seeded event. This syncs each table to exactly
the rows the loader would write, for both markets:

  1. compute today's rebase once (the same shift the loader applies);
  2. build the seed rows per table with the loader's own key rules (ddb_load);
  3. delete every live row whose key is not among them — except PROTECTED rows;
  4. write the seed rows.

Tables share both markets' rows, so a table-level --recreate would wipe the other market;
this never drops or recreates a table (indexes, streams and settings stay as they are).

    python -m app.demo.reset                        # dry run: what would be deleted / written
    python -m app.demo.reset --apply --yes          # do it (backs every table up first)
    python -m app.demo.reset --apply --yes --no-backup

Use deploy/demo-reset.sh, which also restores the SSO administrators and prints readiness —
or the Admin Console's "Demo data" card, which runs `run()` in the demo-reset Lambda
(app/demo/reset_handler.py) with the seed bundled into the function.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import importlib
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from app.services.common import ddb

MARKETS = ("us", "de")
STATUS_TABLE = "PLAT_AgentTraces"
STATUS_ID = "DEMO-RESET-STATUS"     # the last / running reset (the Admin Console reads it)
# Rows kept even though the seed does not have them: the SSO administrators written by
# deploy.sh --only sso-admins, and the reset's own status row.
PROTECTED: dict[str, Any] = {
    "PLAT_Users": lambda key: str(key.get("user_id", "")).startswith("U-ADMIN-"),
    STATUS_TABLE: lambda key: key.get("trace_id") == STATUS_ID,
}
_ALLOWED_PREFIXES = ("retail-ops-dev-",)   # the demo stacks a reset may touch without --any-env


@dataclass
class TablePlan:
    table: str
    key_attrs: list[str]
    seed_rows: list[dict[str, Any]] = field(default_factory=list)
    live: int = 0
    delete: list[dict[str, Any]] = field(default_factory=list)   # DynamoDB key maps
    protected: int = 0


def _loader():
    return importlib.import_module("app.services.l1_data.ddb_load")


def seed_rows(shift: _dt.timedelta) -> dict[str, list[dict[str, Any]]]:
    """bare table -> the rows the loader writes for both markets, keys synthesized as it does."""
    L = _loader()
    from app.services.common.local_ddb import rebase_timestamps
    out: dict[str, list[dict[str, Any]]] = {}
    for m in MARKETS:
        for art in L.table_artifacts(L.load_manifest(m)):
            bare = art["name"]
            rows = rebase_timestamps(L.read_rows(m, art["path"]), shift)
            spec = L.COMPOSITE_KEYS.get(bare)
            if spec and "sort_field" not in spec:
                rows = L._add_sort_key(rows, spec)
            out.setdefault(bare, []).extend(rows)
    return out


def _key_of(row: dict[str, Any], attrs: list[str]) -> tuple[str, ...]:
    return tuple(str(row.get(a)) for a in attrs)


def _table_keys(client, name: str) -> list[str] | None:
    """The table's key attributes, or None when it is not deployed. Looked up by name — never
    ListTables, which only grants on Resource "*" (the reset role stays table-scoped)."""
    try:
        return [k["AttributeName"] for k in client.describe_table(TableName=name)["Table"]["KeySchema"]]
    except Exception as e:  # noqa: BLE001 - ResourceNotFoundException on the real client
        if "ResourceNotFound" in type(e).__name__ or "ResourceNotFound" in str(e):
            return None
        raise


def plan(client, env_prefix: str, shift: _dt.timedelta, workers: int = 8) -> list[TablePlan]:
    seeds = seed_rows(shift)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        plans = list(pool.map(lambda kv: _plan_table(client, env_prefix, kv[0], kv[1]), sorted(seeds.items())))
    return [p for p in plans if p is not None]


def _plan_table(client, env_prefix: str, bare: str, rows: list[dict[str, Any]]) -> TablePlan | None:
    name = f"{env_prefix}{bare}"
    attrs = _table_keys(client, name)
    if attrs is None:
        return None                       # not deployed: the data stage creates it
    want = {_key_of(r, attrs) for r in rows}
    p = TablePlan(bare, attrs, rows)
    guard = PROTECTED.get(bare)
    skw: dict[str, Any] = {"TableName": name, "ProjectionExpression": ", ".join(f"#k{i}" for i in range(len(attrs))),
                           "ExpressionAttributeNames": {f"#k{i}": a for i, a in enumerate(attrs)}}
    while True:
        resp = client.scan(**skw)
        for item in resp.get("Items", []):
            p.live += 1
            key = {a: ddb.from_attr(item[a]) for a in attrs if a in item}
            if _key_of(key, attrs) in want:
                continue
            if guard and guard(key):
                p.protected += 1
                continue
            p.delete.append({a: item[a] for a in attrs})
        if "LastEvaluatedKey" not in resp:
            break
        skw["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return p


def _batch_delete(client, name: str, keys: list[dict[str, Any]]) -> None:
    for i in range(0, len(keys), 25):
        req = {name: [{"DeleteRequest": {"Key": k}} for k in keys[i:i + 25]]}
        attempt = 0
        while req and attempt < 10:
            resp = client.batch_write_item(RequestItems=req)
            req = resp.get("UnprocessedItems") or {}
            if req:
                time.sleep(min(0.05 * 2 ** attempt, 2.0))
            attempt += 1
        if req:
            raise RuntimeError(f"{name}: {len(req.get(name, []))} deletes unprocessed after retries")


def backup_all(client, env_prefix: str, tables: list[str], tag: str) -> int:
    n = 0
    for bare in tables:
        client.create_backup(TableName=f"{env_prefix}{bare}", BackupName=f"{tag}-{bare}"[:255])
        n += 1
    return n


def current_shift() -> _dt.timedelta:
    """The rebase the loader applies today (seed time -> now)."""
    from app.services.common.local_ddb import SEED_NOW
    return _dt.datetime.now(_dt.UTC).replace(microsecond=0) - SEED_NOW


def check_prefix(env_prefix: str, any_env: bool = False) -> None:
    if not env_prefix.startswith(_ALLOWED_PREFIXES) and not any_env:
        raise ValueError(f"refusing to reset {env_prefix!r}: only {_ALLOWED_PREFIXES}")


def apply(client, env_prefix: str, plans: list[TablePlan], workers: int = 8,
          log: Callable[[str], None] | None = None) -> None:
    """Delete the unseeded rows and write the seed rows, a few tables at a time."""
    L = _loader()
    to_item = L._load_ddb_loader().to_ddb_item

    def one(p: TablePlan) -> None:
        name = f"{env_prefix}{p.table}"
        if p.delete:
            _batch_delete(client, name, p.delete)
        L._batch_write(client, name, p.seed_rows, to_item)
        if log:
            log(f"  {p.table:34s} -{len(p.delete):6d} rows, wrote {len(p.seed_rows):6d}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, plans))      # list() re-raises the first table's error


def run(client, env_prefix: str, backup: bool = True, any_env: bool = False,
        log: Callable[[str], None] | None = None) -> dict[str, Any]:
    """The whole reset (plan, optional backup, apply). Returns a summary for the status row."""
    check_prefix(env_prefix, any_env)
    t0 = time.monotonic()
    shift = current_shift()
    plans = plan(client, env_prefix, shift)
    tag = None
    if backup:
        tag = f"demoreset-{_dt.datetime.now(_dt.UTC).strftime('%Y%m%d%H%M')}"
        backup_all(client, env_prefix, [p.table for p in plans], tag)
        if log:
            log(f"  backed up {len(plans)} tables as {tag}-*")
    apply(client, env_prefix, plans, log=log)
    return {"tables": len(plans), "removed": sum(len(p.delete) for p in plans),
            "written": sum(len(p.seed_rows) for p in plans), "protected": sum(p.protected for p in plans),
            "backup_tag": tag, "rebase_days": shift.days, "seconds": round(time.monotonic() - t0, 1)}


# --- status row (the Admin Console's "Demo data" card reads it) ---------------------------

def _now_iso() -> str:
    return _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_status(client, env_prefix: str, state: str, **fields: Any) -> dict[str, Any]:
    """Overwrite the one status row. `state` is running | done | failed."""
    row = {"trace_id": STATUS_ID, "agent_id": "admin", "tool": "admin.demo_reset",
           "state": state, "updated_at": _now_iso(), **{k: v for k, v in fields.items() if v is not None}}
    client.put_item(TableName=f"{env_prefix}{STATUS_TABLE}", Item={k: ddb.to_attr(v) for k, v in row.items()})
    return row


def read_status(client, env_prefix: str) -> dict[str, Any] | None:
    resp = client.get_item(TableName=f"{env_prefix}{STATUS_TABLE}", Key={"trace_id": {"S": STATUS_ID}},
                           ConsistentRead=True)
    item = resp.get("Item")
    return {k: ddb.from_attr(v) for k, v in item.items()} if item else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="delete + write (default: dry run)")
    ap.add_argument("--yes", action="store_true", help="confirm --apply")
    ap.add_argument("--no-backup", action="store_true", help="skip the on-demand backup of every table")
    ap.add_argument("--env-prefix", default=ddb.DEFAULT_ENV_PREFIX)
    ap.add_argument("--any-env", action="store_true", help=f"allow a prefix other than {_ALLOWED_PREFIXES}")
    ap.add_argument("--region", default="us-east-1")
    args = ap.parse_args(argv)
    try:
        check_prefix(args.env_prefix, args.any_env)
    except ValueError as e:
        raise SystemExit(f"{e} without --any-env") from None
    if args.apply and not args.yes:
        raise SystemExit("--apply needs --yes (this deletes every row the seed does not have)")
    import boto3
    client = boto3.client("dynamodb", region_name=args.region)
    shift = current_shift()
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] demo reset of {args.env_prefix}* to the seed, rebased +{shift.days}d {shift.seconds // 3600}h")
    plans = plan(client, args.env_prefix, shift)
    total_del = sum(len(p.delete) for p in plans)
    total_write = sum(len(p.seed_rows) for p in plans)
    for p in plans:
        if p.delete or p.protected:
            print(f"  {p.table:34s} live {p.live:6d}  delete {len(p.delete):6d}  keep-protected {p.protected:3d}  "
                  f"seed {len(p.seed_rows):6d}")
    print(f"  -> {total_del} rows to delete across {sum(1 for p in plans if p.delete)} tables; "
          f"{total_write} seed rows to write into {len(plans)} tables")
    if not args.apply:
        print("  (event logs are keyed on their rebased time, so every seeded event re-keys to now:")
        print("   their 'delete' count is the previous reset's copies even on a clean stack)")
        print("  (dry run; re-run with --apply --yes)")
        return 0
    if not args.no_backup:
        tag = f"demoreset-{_dt.datetime.now(_dt.UTC).strftime('%Y%m%d%H%M')}"
        print(f"  backed up {backup_all(client, args.env_prefix, [p.table for p in plans], tag)} tables as {tag}-*")
    apply(client, args.env_prefix, plans, log=print)
    print(f"[APPLY] reset complete: {total_del} rows removed, {total_write} seed rows written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
