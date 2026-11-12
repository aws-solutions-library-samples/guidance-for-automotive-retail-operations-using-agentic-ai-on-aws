#!/usr/bin/env python3
"""L1 — Load synthetic-data artifacts into real AWS DynamoDB.

Reads a market's `sample-data/synthetic/<market>/manifest.json`, and for every
`kind:"table"` artifact creates a DynamoDB table (on-demand billing) and
batch-writes its rows. The plain-JSON row artifacts remain the source of truth
(contract C3); this loader performs only the load-time wrap, reusing the
generator's authoritative `to_ddb_item()` / `load_pk()` so the load contract
cannot drift from the exporter.

Safety:
  * dry-run by default; `--apply` required for any create/write.
  * never deletes a table unless BOTH `--apply` and `--yes-delete` are passed
    with `--recreate`, and only for tables under `--env-prefix`.
  * intended for a dev/sandbox account only.

Usage:
    python3 app/services/l1_data/ddb_load.py --market us --region us-east-1
    python3 app/services/l1_data/ddb_load.py --market us --region us-east-1 --apply
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Reuse the generator's authoritative load-time contract by file-path import,
# so this loader and the exporter can never diverge on item shape / PK.
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parents[3]
_LOADER_SRC = _ROOT / "scripts" / "seed" / "generate" / "exporters" / "ddb_loader.py"
_MARKETS = ("us", "de")
# RETAIL_OPS_ENV_PREFIX, else retail-ops-<RETAIL_OPS_ENV>- (app/infra/naming.py; default dev)
_DEFAULT_ENV_PREFIX = os.environ.get("RETAIL_OPS_ENV_PREFIX") or f"retail-ops-{os.environ.get('RETAIL_OPS_ENV') or 'dev'}-"

# ---------------------------------------------------------------------------
# Per-table partition-key map (contract for L1 real-DynamoDB load).
#
# The generator's load_pk() picks the first *_id field, which for many tables is
# a FOREIGN key (e.g. DEALER_Journeys.campaign_id) and would collide in DynamoDB,
# silently overwriting rows. For a real, queryable table each table must key on
# its own UNIQUE identifier. This map names that field explicitly for every
# table whose unique key is not the auto-derived `<singular(name)>_id`.
#
# item_key tables (PLAT config family + EntityMap) always key on `item_key`
# (contract C1) and are handled before this map.
#
# The loader still VERIFIES the chosen key is unique across the rows and aborts
# if it is not — this map is the intent, the uniqueness check is the guarantee.
# ---------------------------------------------------------------------------
# Composite-key tables: append-only event logs with no single unique field.
# Modeled as (HASH=parent journey, RANGE=sort key). The loader synthesizes a
# unique sort-key attribute `sk` = "<event_ts>#<tiebreak>" so rows never collide
# and the natural query ("all events for a journey, in time order") is a Query.
COMPOSITE_KEYS: dict[str, dict[str, str]] = {
    "DEALER_JourneyEvents": {"hash": "journey_id", "sort": "sk",
                             "sort_from": "event_ts", "tiebreak": "correlation_id"},
    "DEALER_ServiceJourneyEvents": {"hash": "service_journey_id", "sort": "sk",
                                    "sort_from": "event_ts", "tiebreak": "event_type"},
    "DEALER_Signals": {"hash": "customer_id", "sort": "sk",
                       "sort_from": "signal_ts", "tiebreak": "type"},
    # Versioned config family: same item_key, one row per version (config
    # history). PK=item_key, SK=version. `sort_field` uses an existing field
    # directly as the sort key rather than synthesizing one.
    "PLAT_Config": {"hash": "item_key", "sort": "version", "sort_field": "version"},
    "PLAT_ConfigAutonomy": {"hash": "item_key", "sort": "version", "sort_field": "version"},
    "PLAT_ConfigGuardrails": {"hash": "item_key", "sort": "version", "sort_field": "version"},
    "PLAT_ConfigNotifications": {"hash": "item_key", "sort": "version", "sort_field": "version"},
    "PLAT_ConfigNurture": {"hash": "item_key", "sort": "version", "sort_field": "version"},
    "PLAT_ConfigSuggestions": {"hash": "item_key", "sort": "version", "sort_field": "version"},
    # Entity-resolution map: one row per app entity per external system.
    "PLAT_EntityMap": {"hash": "item_key", "sort": "system", "sort_field": "system"},
    # Market-scoped reference data: the natural id (DTC code, labor-op code, org
    # node) is only unique WITHIN a market — every market publishes the same
    # codes with its own description, labor rate, hierarchy and currency. Keyed
    # on market alone these silently overwrite each other across markets (the
    # per-market uniqueness check cannot see it), so market is part of the
    # identity. PK=market_id + SK=<natural id> also makes the natural read
    # ("everything for my market") a Query instead of a Scan.
    "DEALER_DTCReference": {"hash": "market_id", "sort": "dtc", "sort_field": "dtc"},
    "DEALER_LaborOps": {"hash": "market_id", "sort": "labor_op_id", "sort_field": "labor_op_id"},
    "PLAT_OrgHierarchy": {"hash": "market_id", "sort": "node_id", "sort_field": "node_id"},
}

# Composite-key tables whose rows do not carry the hash attribute in the seed
# data: the loader stamps it from the market being loaded (it is the one fact
# the loader always knows). PLAT_OrgHierarchy rows are market-implicit —
# "OEM"/"DG-01" mean a different company in each market.
STAMP_MARKET_ON: frozenset[str] = frozenset({"PLAT_OrgHierarchy"})

PK_OVERRIDES: dict[str, str] = {
    # --- CUST_* : one row per customer per table ---
    "CUST_Consent": "consent_id",
    "CUST_Documents": "document_id",
    "CUST_Messages": "message_id",
    # --- DEALER_* : true own-id where first *_id is a foreign key ---
    "DEALER_Appointments": "appointment_id",
    "DEALER_Customers": "customer_id",
    "DEALER_Deals": "deal_id",
    "DEALER_DeclinedWork": "declined_work_id",
    "DEALER_Deliveries": "delivery_id",
    "DEALER_FIProducts": "fi_product_id",
    "DEALER_FinanceOffers": "finance_offer_id",
    "DEALER_InspectionTemplates": "inspection_template_id",
    "DEALER_JourneyEvents": "event_id",
    "DEALER_Journeys": "journey_id",
    "DEALER_Leads": "lead_id",
    "DEALER_Notifications": "notification_id",
    "DEALER_NurturePlans": "nurture_plan_id",
    "DEALER_ObjectionStrategies": "objection_strategy_id",
    "DEALER_Outcomes": "outcome_id",
    "DEALER_PlayRuns": "play_run_id",
    "DEALER_Plays": "play_id",
    "DEALER_Recommendations": "recommendation_id",
    "DEALER_RepairOrders": "ro_id",
    "DEALER_SalesPackages": "package_id",
    "DEALER_ServiceJourneyEvents": "event_id",
    "DEALER_ServiceJourneys": "service_journey_id",
    "DEALER_Signals": "signal_id",
    "DEALER_Tasks": "task_id",
    "DEALER_Touchpoints": "touchpoint_id",
    "DEALER_TradeDispositions": "trade_disposition_id",
    "DEALER_Valuations": "valuation_id",
    "DEALER_Vehicles": "vin",
    "DEALER_LeadSLA": "dealer_id",
    "DEALER_Coverage": "coverage_id",
    "DEALER_Inventory": "vin",
    "DEALER_Inspections": "inspection_id",
    "DEALER_Parts": "part_id",
    "DEALER_PartsOrders": "parts_order_id",
    "DEALER_ServiceEstimates": "estimate_id",
    "DEALER_Approvals": "approval_id",
    "DEALER_Handoffs": "handoff_id",
    # --- OEM_* ---
    "OEM_AccountPlans": "plan_id",
    "OEM_CIPrograms": "program_id",
    "OEM_CatalogModels": "model_id",
    "OEM_Comms": "comm_id",
    "OEM_ContactReports": "report_id",
    "OEM_GuardrailEvents": "guardrail_event_id",
    "OEM_StateRules": "region_code",
    "OEM_Catalog": "model_id",
    "OEM_Markets": "market_id",
    # --- PLAT_* (non item_key) ---
    "PLAT_AgentTraces": "trace_id",
    "PLAT_Configurations": "config_id",
    "PLAT_KnowledgeCollections": "collection_id",
    "PLAT_Users": "user_id",
}


def _load_ddb_loader():
    """Import scripts/seed/generate/exporters/ddb_loader.py as a standalone
    module. That file does `from ..owner_map import split_prefixed`, a relative
    import that only resolves inside its package, so we register the minimal
    package chain first and load owner_map as a submodule."""
    import types

    gen_dir = _ROOT / "scripts" / "seed" / "generate"

    # Register a lightweight package for `scripts.seed.generate` so the relative
    # import `from ..owner_map import split_prefixed` resolves.
    pkg_name = "_retail_ops_gen"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(gen_dir)]  # type: ignore[attr-defined]
        sys.modules[pkg_name] = pkg

    # owner_map as _retail_ops_gen.owner_map
    om_name = f"{pkg_name}.owner_map"
    if om_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(om_name, gen_dir / "owner_map.py")
        assert spec and spec.loader
        om = importlib.util.module_from_spec(spec)
        sys.modules[om_name] = om
        spec.loader.exec_module(om)

    # exporters subpackage
    exp_pkg = f"{pkg_name}.exporters"
    if exp_pkg not in sys.modules:
        ep = types.ModuleType(exp_pkg)
        ep.__path__ = [str(gen_dir / "exporters")]  # type: ignore[attr-defined]
        sys.modules[exp_pkg] = ep

    mod_name = f"{exp_pkg}.ddb_loader"
    spec = importlib.util.spec_from_file_location(mod_name, _LOADER_SRC)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_manifest(market: str) -> dict[str, Any]:
    path = _ROOT / "sample-data" / "synthetic" / market / "manifest.json"
    if not path.exists():
        raise SystemExit(
            f"manifest not found: {path}\n"
            f"Run the generator first: python3 scripts/seed/generate/generate_synthetic.py "
            f"--market {market} --seed 42 --out local"
        )
    return json.loads(path.read_text())


def table_artifacts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [a for a in manifest.get("artifacts", []) if a.get("kind") == "table"]


def read_rows(market: str, rel_path: str) -> list[dict[str, Any]]:
    path = _ROOT / "sample-data" / "synthetic" / market / rel_path
    rows = json.loads(path.read_text())
    return stamp_market(path.stem, rows, market)


def stamp_market(bare: str, rows: list[dict[str, Any]], market: str) -> list[dict[str, Any]]:
    """Fill in `market_id` for market-scoped tables whose seed rows omit it, so
    the composite key is complete. Rows that already carry one are untouched."""
    if bare not in STAMP_MARKET_ON:
        return rows
    return [r if r.get("market_id") else {**r, "market_id": market} for r in rows]


def _singular_id_field(bare: str, split_prefixed) -> str:
    """`<singular(snake(bare-name))>_id`, e.g. DEALER_Journeys -> journey_id."""
    _owner, name = split_prefixed(bare)
    snake = "".join(("_" + c.lower()) if c.isupper() and i > 0 else c.lower()
                    for i, c in enumerate(name)).lstrip("_")
    # naive singularization: Journeys->journey, Deals->deal, Appointments->appointment
    if snake.endswith("ies"):
        snake = snake[:-3] + "y"
    elif snake.endswith("ses"):
        snake = snake[:-2]
    elif snake.endswith("s"):
        snake = snake[:-1]
    return f"{snake}_id"


def _is_unique(rows: list[dict[str, Any]], field: str) -> bool:
    seen: set[str] = set()
    for r in rows:
        v = r.get(field)
        if v is None:
            return False
        key = str(v)
        if key in seen:
            return False
        seen.add(key)
    return True


def resolve_pk_field(table: str, rows: list[dict[str, Any]],
                     item_key_tables: frozenset[str], split_prefixed) -> str:
    """Determine the UNIQUE partition-key attribute name for the table and
    VERIFY it is unique across every row. Aborts if no unique key is found, so
    a foreign key can never silently overwrite rows in DynamoDB.

    Order of preference:
      1. item_key tables (contract C1) -> item_key
      2. explicit PK_OVERRIDES entry
      3. <singular(name)>_id  (DEALER_Journeys -> journey_id)
      4. any *_id field that is unique across all rows
      5. abort
    """
    if not rows:
        raise ValueError(f"{table}: no rows, cannot infer a partition key")

    if table in item_key_tables:
        field = "item_key"
        if not _is_unique(rows, field):
            raise SystemExit(f"ABORT {table}: item_key is not unique across rows — cannot load safely")
        return field

    candidates: list[str] = []
    if table in PK_OVERRIDES:
        candidates.append(PK_OVERRIDES[table])
    candidates.append(_singular_id_field(table, split_prefixed))
    # then any *_id field, and a few common non-_id keys, as last resorts
    present = list(rows[0].keys())
    candidates += [k for k in present if k.endswith("_id")]
    candidates += [k for k in ("vin", "dtc", "code", "collection_id", "config_key")
                   if k in rows[0]]

    tried: list[str] = []
    for field in candidates:
        if field in tried:
            continue
        tried.append(field)
        if field in rows[0] and _is_unique(rows, field):
            return field

    raise SystemExit(
        f"ABORT {table}: no unique partition key found (tried {tried}). "
        f"Add a correct entry to PK_OVERRIDES — refusing to load and risk overwriting rows."
    )


def dynamo_type(value: Any) -> str:
    """Key attribute type for DynamoDB CreateTable (S or N). Keys are S or N only."""
    if isinstance(value, bool):
        return "S"
    if isinstance(value, (int, float)):
        return "N"
    return "S"


def chunked(seq: list[Any], size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Load synthetic-data artifacts into AWS DynamoDB (L1).")
    ap.add_argument("--market", choices=_MARKETS, default="us")
    ap.add_argument("--region", default=None, help="AWS region (default: session/profile region).")
    ap.add_argument("--env-prefix", default=_DEFAULT_ENV_PREFIX,
                    help=f"table-name namespace prefix (default: {_DEFAULT_ENV_PREFIX!r})")
    ap.add_argument("--apply", action="store_true",
                    help="perform real AWS creates/writes (default: dry-run).")
    ap.add_argument("--recreate", action="store_true",
                    help="delete + recreate existing tables (requires --apply and --yes-delete).")
    ap.add_argument("--yes-delete", action="store_true",
                    help="explicit confirmation required by --recreate.")
    ap.add_argument("--only", default=None,
                    help="load only tables whose name contains this substring (debugging).")
    ap.add_argument("--delete", action="store_true",
                    help="DELETE every table under --env-prefix (requires --apply and "
                         "--yes-delete). Destroys all rows. Dev/sandbox teardown only.")
    ap.add_argument("--rebase-now", action="store_true",
                    help="shift every full ISO timestamp so the pack's frozen clock (SEED_NOW) lands on "
                         "now — the same rebase the local sandbox applies — so 'today' views on the "
                         "deployed stack are current. Date-only values and URLs are left alone.")
    args = ap.parse_args(argv)
    shift = None
    if args.rebase_now:
        import datetime as _dt
        if str(_ROOT) not in sys.path:      # the script is run by path, not as a module
            sys.path.insert(0, str(_ROOT))
        from app.services.common.local_ddb import SEED_NOW, rebase_timestamps
        shift = _dt.datetime.now(_dt.UTC).replace(microsecond=0) - SEED_NOW
        print(f"[rebase] SEED_NOW {SEED_NOW.isoformat()} -> now (+{shift.days}d {shift.seconds // 3600}h)")

    if args.delete:
        return _delete_all(args)

    loader = _load_ddb_loader()
    to_ddb_item = loader.to_ddb_item
    item_key_tables = loader.ITEM_KEY_TABLES
    split_prefixed = loader.split_prefixed

    manifest = load_manifest(args.market)
    tables = table_artifacts(manifest)
    if args.only:
        tables = [t for t in tables if args.only in t["name"]]

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] market={args.market} region={args.region or '(session default)'} "
          f"env-prefix={args.env_prefix!r} tables={len(tables)}")

    # boto3 is only needed for --apply; import lazily so dry-run stays offline.
    ddb = None
    existing: set[str] = set()
    if args.apply:
        import boto3
        session = boto3.Session(region_name=args.region) if args.region else boto3.Session()
        ident = session.client("sts").get_caller_identity()
        print(f"[APPLY] account={ident['Account']} arn={ident['Arn']} region={session.region_name}")
        ddb = session.client("dynamodb")
        existing = set(_list_tables(ddb))

    total_rows = 0
    for art in tables:
        bare = art["name"]                       # e.g. CUST_Consent
        ddb_name = f"{args.env_prefix}{bare}"    # e.g. retail-ops-dev-CUST_Consent
        rows = read_rows(args.market, art["path"])
        if shift is not None:
            rows = rebase_timestamps(rows, shift)
        if bare == "DEALER_Inventory" and args.apply and ddb is not None and not args.recreate:
            rows = _keep_runtime_sales(ddb, args.env_prefix, rows)
        total_rows += len(rows)

        # Resolve key schema: composite (event logs) or single-field.
        if bare in COMPOSITE_KEYS:
            spec = COMPOSITE_KEYS[bare]
            if "sort_field" not in spec:
                rows = _add_sort_key(rows, spec)  # synthesize the unique `sk`
            _verify_composite_unique(bare, rows, spec)
            sort_type = dynamo_type(rows[0].get(spec["sort"])) if "sort_field" in spec else "S"
            key_schema = [
                {"AttributeName": spec["hash"], "AttributeType": dynamo_type(rows[0].get(spec["hash"]))},
                {"AttributeName": spec["sort"], "AttributeType": sort_type},
            ]
            key_desc = f"pk={spec['hash']}+sk={spec['sort']}"
        else:
            pk_field = resolve_pk_field(bare, rows, item_key_tables, split_prefixed)
            _verify_unique_across_markets(bare, art["path"], pk_field, args.market)
            key_schema = [{"AttributeName": pk_field, "AttributeType": dynamo_type(rows[0].get(pk_field))}]
            key_desc = f"pk={pk_field}({key_schema[0]['AttributeType']})"

        if not args.apply:
            print(f"  would load {ddb_name:<40} {key_desc:<32} rows={len(rows)}")
            continue

        assert ddb is not None
        if ddb_name in existing:
            if args.recreate:
                if not args.yes_delete:
                    raise SystemExit(f"--recreate needs --yes-delete for {ddb_name}")
                print(f"  deleting existing {ddb_name} (--recreate --yes-delete)")
                ddb.delete_table(TableName=ddb_name)
                _wait_gone(ddb, ddb_name)
            else:
                print(f"  table exists, loading rows into {ddb_name} (use --recreate to rebuild)")
                _batch_write(ddb, ddb_name, rows, to_ddb_item)
                print(f"    wrote {len(rows)} rows")
                continue

        _create_table(ddb, ddb_name, key_schema)
        _wait_active(ddb, ddb_name)
        _batch_write(ddb, ddb_name, rows, to_ddb_item)
        print(f"  created + loaded {ddb_name:<40} {key_desc:<32} rows={len(rows)}")

    verb = "loaded" if args.apply else "would load"
    print(f"[{mode}] done: {verb} {len(tables)} tables, {total_rows} rows"
          + ("" if args.apply else "  (re-run with --apply to write)"))
    return 0


def _delete_all(args) -> int:
    """Delete every DynamoDB table under --env-prefix. Destructive: guarded by
    both --apply and --yes-delete, and scoped strictly to the prefix so it can
    never touch a table outside that env's retail-ops-<env>- namespace."""
    prefix = args.env_prefix
    if not prefix or prefix in ("", "retail-ops-", "retail-ops"):
        raise SystemExit(f"refusing to delete with an over-broad env-prefix {prefix!r}")

    import boto3
    session = boto3.Session(region_name=args.region) if args.region else boto3.Session()
    ident = session.client("sts").get_caller_identity()
    ddb = session.client("dynamodb")
    targets = sorted(t for t in _list_tables(ddb) if t.startswith(prefix))

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] DELETE account={ident['Account']} region={session.region_name} "
          f"prefix={prefix!r} -> {len(targets)} tables")
    for t in targets:
        print(f"  {'deleting' if (args.apply and args.yes_delete) else 'would delete'} {t}")

    if not args.apply:
        print("  (dry-run; re-run with --apply --yes-delete to delete)")
        return 0
    if not args.yes_delete:
        raise SystemExit("--delete needs --yes-delete to actually remove tables")

    for t in targets:
        try:
            ddb.delete_table(TableName=t)
        except ddb.exceptions.ResourceNotFoundException:
            pass
    for t in targets:
        _wait_gone(ddb, t)
    print(f"[APPLY] deleted {len(targets)} tables under {prefix!r}")
    return 0


def _list_tables(ddb) -> list[str]:
    names: list[str] = []
    kwargs: dict[str, Any] = {}
    while True:
        resp = ddb.list_tables(**kwargs)
        names.extend(resp.get("TableNames", []))
        last = resp.get("LastEvaluatedTableName")
        if not last:
            break
        kwargs["ExclusiveStartTableName"] = last
    return names


def _add_sort_key(rows: list[dict[str, Any]], spec: dict[str, str]) -> list[dict[str, Any]]:
    """Return rows with a synthetic unique sort-key attribute added.
    sk = "<sort_from>#<tiebreak>#<ordinal>"; the trailing ordinal guarantees
    uniqueness even if sort_from and tiebreak both repeat."""
    out: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for r in rows:
        r = dict(r)
        base = f"{r.get(spec['sort_from'], '')}#{r.get(spec.get('tiebreak', ''), '')}"
        n = seen.get(base, 0)
        seen[base] = n + 1
        r[spec["sort"]] = f"{base}#{n:04d}"
        out.append(r)
    return out


def _verify_composite_unique(table: str, rows: list[dict[str, Any]], spec: dict[str, str]) -> None:
    keys = {(str(r.get(spec["hash"])), str(r.get(spec["sort"]))) for r in rows}
    if len(keys) != len(rows):
        raise SystemExit(f"ABORT {table}: composite key ({spec['hash']},{spec['sort']}) not unique")


def _verify_unique_across_markets(table: str, rel_path: str, pk_field: str, market: str) -> None:
    """All markets load into ONE table, so a single-field PK must be unique
    across markets too — not just within the market being loaded. Reference
    tables (DTC codes, labor ops, org nodes) reuse the same ids per market and
    would silently overwrite each other; those belong in COMPOSITE_KEYS keyed
    on market_id. Abort loudly rather than lose rows."""
    base = _ROOT / "sample-data" / "synthetic"
    mine = {str(r.get(pk_field)) for r in read_rows(market, rel_path)}
    for other in sorted(p.name for p in base.iterdir() if p.is_dir()):
        if other == market or not (base / other / rel_path).exists():
            continue
        clash = mine & {str(r.get(pk_field)) for r in read_rows(other, rel_path)}
        if clash:
            raise SystemExit(
                f"ABORT {table}: pk={pk_field} collides with market {other!r} on "
                f"{len(clash)} id(s) e.g. {sorted(clash)[:3]} — all markets share one "
                f"table, so these rows would overwrite each other. Add {table} to "
                f"COMPOSITE_KEYS keyed on market_id.")


def _create_table(ddb, name: str, key_schema: list[dict[str, str]]) -> None:
    """key_schema: [{AttributeName, AttributeType}, ...] in HASH, RANGE order."""
    key_types = ["HASH", "RANGE"]
    ddb.create_table(
        TableName=name,
        AttributeDefinitions=[{"AttributeName": k["AttributeName"], "AttributeType": k["AttributeType"]}
                              for k in key_schema],
        KeySchema=[{"AttributeName": k["AttributeName"], "KeyType": key_types[i]}
                   for i, k in enumerate(key_schema)],
        BillingMode="PAY_PER_REQUEST",
    )


def _wait_active(ddb, name: str, timeout_s: int = 120) -> None:
    waiter = ddb.get_waiter("table_exists")
    waiter.wait(TableName=name, WaiterConfig={"Delay": 2, "MaxAttempts": timeout_s // 2})


def _wait_gone(ddb, name: str, timeout_s: int = 120) -> None:
    waiter = ddb.get_waiter("table_not_exists")
    waiter.wait(TableName=name, WaiterConfig={"Delay": 2, "MaxAttempts": timeout_s // 2})


def _keep_runtime_sales(ddb, env_prefix: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A reload resets stock to the pack, but a car delivered on this stack is already a
    customer's (its DEALER_Vehicles row, written by "mark delivered", is not in the pack and
    survives the load). Keep that unit sold, so a delivered car never reappears as in stock."""
    sold: dict[str, str | None] = {}
    kw: dict[str, Any] = {"TableName": f"{env_prefix}DEALER_Vehicles",
                          "ProjectionExpression": "vin, source_ref, journey_id"}
    try:
        while True:
            out = ddb.scan(**kw)
            for i in out.get("Items", []):
                if str((i.get("source_ref") or {}).get("S", "")).startswith("DeliveryBoard.mark_delivered"):
                    sold[i["vin"]["S"]] = (i.get("journey_id") or {}).get("S")
            if "LastEvaluatedKey" not in out:
                break
            kw["ExclusiveStartKey"] = out["LastEvaluatedKey"]
    except Exception as e:  # noqa: BLE001 - no vehicle table yet (first load): nothing to keep
        print(f"    [inventory] runtime sales not checked ({type(e).__name__})")
        return rows
    kept = 0
    out_rows = []
    for r in rows:
        if r.get("vin") in sold:
            r = {k: v for k, v in r.items() if k not in ("reserved_for_journey_id", "status_before_reserve")}
            r.update({"status": "sold", "sold_to_journey_id": sold[r["vin"]] or r.get("sold_to_journey_id")})
            kept += 1
        out_rows.append(r)
    if kept:
        print(f"    [inventory] {kept} unit(s) delivered on this stack kept as sold")
    return out_rows


def _batch_write(ddb, name: str, rows: list[dict[str, Any]], to_ddb_item) -> None:
    for batch in chunked(rows, 25):
        request = {name: [{"PutRequest": {"Item": to_ddb_item(r)}} for r in batch]}
        resp = ddb.batch_write_item(RequestItems=request)
        # Retry unprocessed items with simple backoff.
        unprocessed = resp.get("UnprocessedItems", {})
        attempt = 0
        while unprocessed and attempt < 8:
            time.sleep(min(2 ** attempt * 0.05, 2.0))
            resp = ddb.batch_write_item(RequestItems=unprocessed)
            unprocessed = resp.get("UnprocessedItems", {})
            attempt += 1
        if unprocessed:
            raise RuntimeError(f"{name}: {len(unprocessed.get(name, []))} items unprocessed after retries")


if __name__ == "__main__":
    raise SystemExit(main())
