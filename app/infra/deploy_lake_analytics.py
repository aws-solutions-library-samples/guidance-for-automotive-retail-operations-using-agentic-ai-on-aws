#!/usr/bin/env python3
"""Push the generator's local analytics lake prefixes to the S3 event lake and
register one Athena/Glue external table per prefix (spec §3.7 analytics lake).

`deploy_lake.py` wires only the live `journey_events` Firehose/Athena table. The
generator also materializes several analytics prefixes locally under
`sample-data/synthetic/<market>/lake/<prefix>/data.ndjson` (service, recommendations,
touchpoints, outcomes, journeys, agent_traces, …). This uploader lands those
NDJSON rows in the SAME lake bucket under `<prefix>/` (merging both markets) and
creates a schema-on-read JSON external table over each, so A5/QuickSight and
Athena can query them alongside `journey_events`.

Design:
  * Additive and idempotent — overwrites only the objects it writes
    (`<prefix>/market=<m>/data.ndjson`); never touches `journey-events/`.
  * Schema-on-read: the JSON SerDe tolerates heterogeneous rows, so each table
    declares a single `line string`-free, columns-discovered contract via the
    openx JsonSerDe with `ignore.malformed.json`. Columns are the sorted union
    of keys seen locally, all typed `string` (QuickSight casts as needed).
  * Reuses the existing database `retail_ops_<env>_lake` and workgroup `retail-ops-<env>-lake`.

Usage:
  python3 -m app.infra.deploy_lake_analytics            # dry-run (lists what it would do)
  python3 -m app.infra.deploy_lake_analytics --apply    # upload + register tables
  python3 -m app.infra.deploy_lake_analytics --status   # per-prefix S3 object + row counts
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from app.infra import naming
except ImportError:  # run as a script from app/infra/
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.infra import naming

ROOT = Path(__file__).resolve().parents[2]
REGION = "us-east-1"
DATABASE = naming.name_us("lake")
WORKGROUP = naming.name("lake")
LAKE_CONFIG = naming.config_path("lake")
SYN_DIR = ROOT / "sample-data" / "synthetic"
MARKETS = ("us", "de")

# The local-lake prefixes to publish. `journey_events` is intentionally excluded
# (it is owned by deploy_lake.py's partitioned Firehose table).
PREFIXES = ("journeys", "service", "service_events", "recommendations",
            "outcomes", "touchpoints", "agent_traces")


def _bucket() -> str:
    if LAKE_CONFIG.exists():
        b = json.loads(LAKE_CONFIG.read_text()).get("bucket")
        if b:
            return b
    import boto3
    acct = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    return naming.name(f"event-lake-{acct}")


def _local_ndjson(prefix: str, market: str) -> Path | None:
    p = SYN_DIR / market / "lake" / prefix / "data.ndjson"
    return p if p.is_file() else None


def _rows(path: Path) -> list[dict]:
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _columns(rows: list[dict]) -> list[str]:
    return sorted({k for r in rows for k in r})


def _s3_key(prefix: str, market: str) -> str:
    # Hive-style single partition by market so both markets coexist and are filterable.
    return f"{prefix}/market={market}/data.ndjson"


def upload(apply: bool) -> dict[str, dict]:
    import boto3
    bucket = _bucket()
    s3 = boto3.client("s3", region_name=REGION)
    summary: dict[str, dict] = {}
    for prefix in PREFIXES:
        total_rows = 0
        cols: set[str] = set()
        present_markets = []
        for market in MARKETS:
            path = _local_ndjson(prefix, market)
            if not path:
                continue
            rows = _rows(path)
            if not rows:
                continue
            present_markets.append(market)
            total_rows += len(rows)
            cols.update(_columns(rows))
            if apply:
                # canonical NDJSON (sorted keys) → stable bytes across same-seed runs
                body = ("\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True)
                                  for r in rows) + "\n").encode("utf-8")
                s3.put_object(Bucket=bucket, Key=_s3_key(prefix, market),
                              Body=body, ContentType="application/x-ndjson")
        if total_rows == 0:
            continue
        summary[prefix] = {"rows": total_rows, "markets": present_markets,
                           "columns": sorted(cols)}
        print(f"  [{'s3' if apply else 'dry-run'}] {prefix}: "
              f"{total_rows} rows across {present_markets} "
              f"({len(cols)} columns)")
    return summary


def register_tables(summary: dict[str, dict], apply: bool) -> None:
    import boto3
    bucket = _bucket()
    glue = boto3.client("glue", region_name=REGION)
    for prefix, info in summary.items():
        table = f"lake_{prefix}"  # e.g. lake_service, lake_recommendations
        cols = [{"Name": c, "Type": "string"} for c in info["columns"]]
        table_input = {
            "Name": table,
            "TableType": "EXTERNAL_TABLE",
            "Parameters": {"classification": "json", "typeOfData": "file"},
            "PartitionKeys": [{"Name": "market", "Type": "string"}],
            "StorageDescriptor": {
                "Columns": cols,
                "Location": f"s3://{bucket}/{prefix}/",
                "InputFormat": "org.apache.hadoop.mapred.TextInputFormat",
                "OutputFormat": "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
                "SerdeInfo": {
                    "SerializationLibrary": "org.openx.data.jsonserde.JsonSerDe",
                    "Parameters": {"ignore.malformed.json": "true"},
                },
            },
        }
        if not apply:
            print(f"  [dry-run] would register table {DATABASE}.{table} "
                  f"({len(cols)} cols) over s3://{bucket}/{prefix}/")
            continue
        try:
            glue.create_table(DatabaseName=DATABASE, TableInput=table_input)
            print(f"  [glue] created table {table}")
        except glue.exceptions.AlreadyExistsException:
            glue.update_table(DatabaseName=DATABASE, TableInput=table_input)
            print(f"  [glue] updated table {table}")


def repair_partitions(summary: dict[str, dict]) -> None:
    """MSCK REPAIR each new table so Athena discovers the market= partitions."""
    import boto3
    import time
    athena = boto3.client("athena", region_name=REGION)
    for prefix in summary:
        table = f"lake_{prefix}"
        try:
            r = athena.start_query_execution(
                QueryString=f"MSCK REPAIR TABLE {DATABASE}.{table}",
                WorkGroup=WORKGROUP,
                QueryExecutionContext={"Database": DATABASE})
            qid = r["QueryExecutionId"]
            for _ in range(30):
                st = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]["State"]
                if st in ("SUCCEEDED", "FAILED", "CANCELLED"):
                    break
                time.sleep(1)
            print(f"  [athena] repaired partitions for {table} ({st})")
        except Exception as e:  # noqa: BLE001
            print(f"  [athena] repair {table} note: {e}")


def status() -> None:
    import boto3
    bucket = _bucket()
    s3 = boto3.client("s3", region_name=REGION)
    athena = boto3.client("athena", region_name=REGION)
    import time
    print(f"lake bucket: {bucket}")
    for prefix in PREFIXES:
        objs = s3.list_objects_v2(Bucket=bucket, Prefix=f"{prefix}/").get("Contents", [])
        n_obj = len([o for o in objs if o["Key"].endswith(".ndjson")])
        table = f"lake_{prefix}"
        rows = "?"
        try:
            r = athena.start_query_execution(
                QueryString=f"SELECT count(*) FROM {DATABASE}.{table}",
                WorkGroup=WORKGROUP, QueryExecutionContext={"Database": DATABASE})
            qid = r["QueryExecutionId"]
            for _ in range(30):
                qe = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]["State"]
                if qe in ("SUCCEEDED", "FAILED", "CANCELLED"):
                    break
                time.sleep(1)
            if qe == "SUCCEEDED":
                res = athena.get_query_results(QueryExecutionId=qid)
                rows = res["ResultSet"]["Rows"][1]["Data"][0].get("VarCharValue", "?")
        except Exception:
            pass
        print(f"  {prefix}: {n_obj} ndjson object(s) in S3 · {DATABASE}.{table} = {rows} rows")


def main() -> None:
    ap = argparse.ArgumentParser(description="Publish local analytics lake prefixes to S3 + Athena")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    if args.status:
        status()
        return
    if not SYN_DIR.exists():
        raise SystemExit(f"ERROR: {SYN_DIR} not found — generate the synthetic lake first.")
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] publish analytics lake prefixes to {_bucket()}")
    summary = upload(args.apply)
    if not summary:
        print("  nothing to publish (no local lake prefixes found)")
        return
    register_tables(summary, args.apply)
    if args.apply:
        repair_partitions(summary)
    print(f"[{mode}] done. prefixes: {', '.join(summary)}")


if __name__ == "__main__":
    main()
