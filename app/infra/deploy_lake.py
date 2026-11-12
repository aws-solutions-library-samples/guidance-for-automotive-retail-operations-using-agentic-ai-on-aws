#!/usr/bin/env python3
"""S3 event lake for A5/QuickSight analytics (spec retail-ops-quick-project.md §4, §7).

Turns the journey-event stream that today lives only in DynamoDB +
EventBridge into a queryable S3 lake, so A5 ROI/funnel dashboards and QuickSight
datasets have a real Athena source (the spec's "S3 lake via Athena").

Provisions, idempotently:
  1. an S3 bucket  retail-ops-<env>-event-lake-<account>  (the lake)
  2. a Kinesis Firehose delivery stream that lands events as newline-JSON under
     journey-events/  with Hive-style dt=YYYY-MM-DD/ partitioning
  3. an EventBridge rule on app-bus-<env> matching Retail Ops journey events
     (source = retail-ops.journeys), targeting the Firehose stream
  4. a Glue database  retail_ops_<env>_lake  + external table  journey_events  over the
     S3 prefix (so Athena/QuickSight can query immediately)
  5. an Athena workgroup  retail-ops-<env>-lake  with results under athena-results/

  --apply     create/update everything
  --backfill  read the DynamoDB event tables and write them into the lake now
              (so Athena returns rows without waiting for new live events)
  --status    print what exists + an Athena row count
  --delete    tear down (empties + removes the bucket, rule, stream, glue, wg)

Writes app/infra/lake-config.json {bucket, database, table, workgroup, stream}.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

try:
    from app.infra import naming
except ImportError:  # run as a script from app/infra/
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.infra import naming

ROOT = Path(__file__).resolve().parents[2]
REGION = "us-east-1"
CONFIG_PATH = naming.config_path("lake")

BUS = naming.EVENT_BUS
EVENT_SOURCE = "retail-ops.journeys"
STREAM = naming.name("event-lake")
RULE = naming.name("event-lake-rule")
FIREHOSE_ROLE = naming.name("firehose-role")
EVENTS_ROLE = naming.name("events-firehose-role")
DATABASE = naming.name_us("lake")
TABLE = "journey_events"
WORKGROUP = naming.name("lake")
PREFIX = "journey-events/"
ENV_PREFIX = naming.PREFIX


def _bucket(account: str) -> str:
    return naming.name(f"event-lake-{account}")


# --- S3 ---------------------------------------------------------------------

def _ensure_bucket(s3, account: str, apply: bool) -> str:
    name = _bucket(account)
    if not apply:
        print(f"  [dry-run] would ensure bucket {name}")
        return name
    try:
        s3.head_bucket(Bucket=name)
        print(f"  [s3] bucket {name} exists")
    except Exception:
        # us-east-1: no LocationConstraint allowed
        s3.create_bucket(Bucket=name)
        print(f"  [s3] created {name}")
    s3.put_public_access_block(Bucket=name, PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True,
        "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
    return name


# --- IAM --------------------------------------------------------------------

def _ensure_firehose_role(iam, account: str, bucket: str, apply: bool) -> str:
    arn = f"arn:aws:iam::{account}:role/{FIREHOSE_ROLE}"
    assume = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
              "Principal": {"Service": "firehose.amazonaws.com"}, "Action": "sts:AssumeRole"}]}
    policy = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
              "Action": ["s3:AbortMultipartUpload", "s3:GetBucketLocation", "s3:GetObject",
                         "s3:ListBucket", "s3:ListBucketMultipartUploads", "s3:PutObject"],
              "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"]}]}
    if not apply:
        print(f"  [dry-run] would ensure {FIREHOSE_ROLE}")
        return arn
    _upsert_role(iam, FIREHOSE_ROLE, assume, "firehose-s3", policy)
    return arn


def _ensure_events_role(iam, account: str, apply: bool) -> str:
    arn = f"arn:aws:iam::{account}:role/{EVENTS_ROLE}"
    assume = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
              "Principal": {"Service": "events.amazonaws.com"}, "Action": "sts:AssumeRole"}]}
    policy = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
              "Action": ["firehose:PutRecord", "firehose:PutRecordBatch"],
              "Resource": f"arn:aws:firehose:{REGION}:{account}:deliverystream/{STREAM}"}]}
    if not apply:
        print(f"  [dry-run] would ensure {EVENTS_ROLE}")
        return arn
    _upsert_role(iam, EVENTS_ROLE, assume, "events-to-firehose", policy)
    return arn


def _upsert_role(iam, name: str, assume: dict, pol_name: str, policy: dict) -> None:
    try:
        iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(assume),
                        Description="Retail Ops event lake (spec §4)")
        print(f"  [iam] created {name}")
        time.sleep(8)
    except iam.exceptions.EntityAlreadyExistsException:
        print(f"  [iam] role {name} exists")
    iam.put_role_policy(RoleName=name, PolicyName=pol_name, PolicyDocument=json.dumps(policy))


# --- Firehose ---------------------------------------------------------------

def _ensure_firehose(fh, account: str, bucket: str, role_arn: str, apply: bool) -> str:
    stream_arn = f"arn:aws:firehose:{REGION}:{account}:deliverystream/{STREAM}"
    if not apply:
        print(f"  [dry-run] would ensure firehose {STREAM}")
        return stream_arn
    try:
        fh.describe_delivery_stream(DeliveryStreamName=STREAM)
        print(f"  [firehose] {STREAM} exists")
        return stream_arn
    except fh.exceptions.ResourceNotFoundException:
        pass
    fh.create_delivery_stream(
        DeliveryStreamName=STREAM,
        DeliveryStreamType="DirectPut",
        ExtendedS3DestinationConfiguration={
            "RoleARN": role_arn,
            "BucketARN": f"arn:aws:s3:::{bucket}",
            "Prefix": PREFIX + "dt=!{timestamp:yyyy-MM-dd}/",
            "ErrorOutputPrefix": "errors/!{firehose:error-output-type}/",
            "BufferingHints": {"SizeInMBs": 1, "IntervalInSeconds": 60},
            "CompressionFormat": "UNCOMPRESSED",
        },
    )
    print(f"  [firehose] created {STREAM}")
    for _ in range(30):
        st = fh.describe_delivery_stream(DeliveryStreamName=STREAM)["DeliveryStreamDescription"]["DeliveryStreamStatus"]
        if st == "ACTIVE":
            break
        print(f"    firehose status={st}")
        time.sleep(6)
    return stream_arn


# --- EventBridge rule -------------------------------------------------------

def _ensure_rule(ev, account: str, stream_arn: str, events_role_arn: str, apply: bool) -> None:
    if not apply:
        print(f"  [dry-run] would ensure rule {RULE} on {BUS}")
        return
    pattern = {"source": [EVENT_SOURCE]}
    ev.put_rule(Name=RULE, EventBusName=BUS, EventPattern=json.dumps(pattern), State="ENABLED",
                Description="Retail Ops journey events -> event lake (spec §4)")
    ev.put_targets(Rule=RULE, EventBusName=BUS, Targets=[{
        "Id": "firehose", "Arn": stream_arn, "RoleArn": events_role_arn,
    }])
    print(f"  [events] rule {RULE} -> firehose {STREAM}")


# --- Glue + Athena ----------------------------------------------------------

def _ensure_glue(glue, account: str, bucket: str, apply: bool) -> None:
    if not apply:
        print(f"  [dry-run] would ensure glue db {DATABASE} + table {TABLE}")
        return
    try:
        glue.create_database(DatabaseInput={"Name": DATABASE,
                             "Description": "Retail Ops event lake (spec §4)"})
        print(f"  [glue] created db {DATABASE}")
    except glue.exceptions.AlreadyExistsException:
        print(f"  [glue] db {DATABASE} exists")
    cols = [
        {"Name": "journey_id", "Type": "string"},
        {"Name": "event_type", "Type": "string"},
        {"Name": "event_ts", "Type": "string"},
        {"Name": "correlation_id", "Type": "string"},
        {"Name": "evidence", "Type": "string"},
        {"Name": "actor", "Type": "string"},
        {"Name": "payload", "Type": "string"},
    ]
    table_input = {
        "Name": TABLE,
        "TableType": "EXTERNAL_TABLE",
        "Parameters": {"classification": "json", "typeOfData": "file"},
        "PartitionKeys": [{"Name": "dt", "Type": "string"}],
        "StorageDescriptor": {
            "Columns": cols,
            "Location": f"s3://{bucket}/{PREFIX}",
            "InputFormat": "org.apache.hadoop.mapred.TextInputFormat",
            "OutputFormat": "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
            "SerdeInfo": {"SerializationLibrary": "org.openx.data.jsonserde.JsonSerDe"},
        },
    }
    try:
        glue.create_table(DatabaseName=DATABASE, TableInput=table_input)
        print(f"  [glue] created table {TABLE}")
    except glue.exceptions.AlreadyExistsException:
        glue.update_table(DatabaseName=DATABASE, TableInput=table_input)
        print(f"  [glue] updated table {TABLE}")


def _ensure_workgroup(athena, bucket: str, apply: bool) -> None:
    if not apply:
        print(f"  [dry-run] would ensure athena workgroup {WORKGROUP}")
        return
    try:
        athena.create_work_group(Name=WORKGROUP, Configuration={
            "ResultConfiguration": {"OutputLocation": f"s3://{bucket}/athena-results/"}},
            Description="Retail Ops event lake queries (spec §4)")
        print(f"  [athena] created workgroup {WORKGROUP}")
    except athena.exceptions.InvalidRequestException:
        print(f"  [athena] workgroup {WORKGROUP} exists")


# --- backfill ---------------------------------------------------------------

def backfill(bucket: str) -> int:
    """Read the DynamoDB journey-event tables and write them into the lake as
    newline-JSON, partitioned by event date, so Athena has rows immediately."""
    import boto3
    from ..services.common import ddb
    s3 = boto3.client("s3", region_name=REGION)
    total = 0
    by_day: dict[str, list[str]] = {}
    for bare in ("DEALER_JourneyEvents", "DEALER_ServiceJourneyEvents"):
        rows = ddb.scan_all(bare, ENV_PREFIX)
        for r in rows:
            ts = r.get("event_ts") or r.get("sk", "")[:10] or "1970-01-01"
            day = ts[:10]
            rec = {
                "journey_id": r.get("journey_id") or r.get("service_journey_id"),
                "event_type": r.get("event_type"),
                "event_ts": r.get("event_ts") or ts,
                "correlation_id": r.get("correlation_id"),
                "evidence": r.get("evidence"),
                "actor": json.dumps(r.get("actor") or {}),
                "payload": json.dumps(r.get("payload") or {}),
            }
            by_day.setdefault(day, []).append(json.dumps(rec))
            total += 1
    for day, lines in by_day.items():
        key = f"{PREFIX}dt={day}/backfill-{int(time.time())}.json"
        s3.put_object(Bucket=bucket, Key=key, Body=("\n".join(lines) + "\n").encode())
    print(f"  [backfill] wrote {total} events across {len(by_day)} day-partitions")
    return total


def _repair_partitions(athena, bucket: str) -> None:
    """MSCK REPAIR so Athena discovers the dt= partitions the backfill wrote."""
    q = f"MSCK REPAIR TABLE {DATABASE}.{TABLE}"
    r = athena.start_query_execution(
        QueryString=q, WorkGroup=WORKGROUP,
        QueryExecutionContext={"Database": DATABASE})
    _await_query(athena, r["QueryExecutionId"])


def _await_query(athena, qid: str, tries: int = 30) -> str:
    for _ in range(tries):
        ex = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
        st = ex["Status"]["State"]
        if st in ("SUCCEEDED", "FAILED", "CANCELLED"):
            return st
        time.sleep(2)
    return "TIMEOUT"


# --- orchestration ----------------------------------------------------------

def apply_stack(apply: bool, do_backfill: bool) -> dict:
    import boto3
    account = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    s3 = boto3.client("s3", region_name=REGION)
    iam = boto3.client("iam", region_name=REGION)
    fh = boto3.client("firehose", region_name=REGION)
    ev = boto3.client("events", region_name=REGION)
    glue = boto3.client("glue", region_name=REGION)
    athena = boto3.client("athena", region_name=REGION)

    bucket = _ensure_bucket(s3, account, apply)
    fh_role = _ensure_firehose_role(iam, account, bucket, apply)
    ev_role = _ensure_events_role(iam, account, apply)
    stream_arn = _ensure_firehose(fh, account, bucket, fh_role, apply)
    _ensure_rule(ev, account, stream_arn, ev_role, apply)
    _ensure_glue(glue, account, bucket, apply)
    _ensure_workgroup(athena, bucket, apply)

    if apply and do_backfill:
        n = backfill(bucket)
        if n:
            time.sleep(3)
            _repair_partitions(athena, bucket)

    cfg = {"region": REGION, "bucket": bucket, "database": DATABASE, "table": TABLE,
           "workgroup": WORKGROUP, "stream": STREAM, "bus": BUS, "prefix": PREFIX,
           "athena_source": f"{DATABASE}.{TABLE}"}
    if apply:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")
        print(f"  [config] wrote {CONFIG_PATH.name}")
    return cfg


def status() -> None:
    import boto3
    account = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    bucket = _bucket(account)
    athena = boto3.client("athena", region_name=REGION)
    fh = boto3.client("firehose", region_name=REGION)
    try:
        st = fh.describe_delivery_stream(DeliveryStreamName=STREAM)["DeliveryStreamDescription"]["DeliveryStreamStatus"]
        print(f"firehose {STREAM}: {st}")
    except Exception:
        print(f"firehose {STREAM}: NOT PROVISIONED")
    try:
        r = athena.start_query_execution(
            QueryString=f"SELECT count(*) FROM {DATABASE}.{TABLE}",
            WorkGroup=WORKGROUP, QueryExecutionContext={"Database": DATABASE})
        if _await_query(athena, r["QueryExecutionId"]) == "SUCCEEDED":
            res = athena.get_query_results(QueryExecutionId=r["QueryExecutionId"])
            n = res["ResultSet"]["Rows"][1]["Data"][0]["VarCharValue"]
            print(f"athena {DATABASE}.{TABLE}: {n} rows")
        else:
            print(f"athena {DATABASE}.{TABLE}: query failed (table may be empty/unprovisioned)")
    except Exception as e:
        print(f"athena: {type(e).__name__}: {str(e)[:100]}")


def delete_stack() -> None:
    import boto3
    account = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    bucket = _bucket(account)
    s3 = boto3.client("s3", region_name=REGION)
    iam = boto3.client("iam", region_name=REGION)
    fh = boto3.client("firehose", region_name=REGION)
    ev = boto3.client("events", region_name=REGION)
    glue = boto3.client("glue", region_name=REGION)
    athena = boto3.client("athena", region_name=REGION)
    try:
        ev.remove_targets(Rule=RULE, EventBusName=BUS, Ids=["firehose"])
        ev.delete_rule(Name=RULE, EventBusName=BUS)
        print(f"  deleted rule {RULE}")
    except Exception:
        pass
    try:
        fh.delete_delivery_stream(DeliveryStreamName=STREAM)
        print(f"  deleted firehose {STREAM}")
    except Exception:
        pass
    try:
        glue.delete_table(DatabaseName=DATABASE, Name=TABLE)
        glue.delete_database(Name=DATABASE)
        print(f"  deleted glue {DATABASE}.{TABLE}")
    except Exception:
        pass
    try:
        athena.delete_work_group(WorkGroup=WORKGROUP, RecursiveDeleteOption=True)
        print(f"  deleted workgroup {WORKGROUP}")
    except Exception:
        pass
    # empty + delete bucket
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if objs:
                s3.delete_objects(Bucket=bucket, Delete={"Objects": objs})
        s3.delete_bucket(Bucket=bucket)
        print(f"  deleted bucket {bucket}")
    except Exception:
        pass
    for role, pol in ((FIREHOSE_ROLE, "firehose-s3"), (EVENTS_ROLE, "events-to-firehose")):
        try:
            iam.delete_role_policy(RoleName=role, PolicyName=pol)
            iam.delete_role(RoleName=role)
            print(f"  deleted role {role}")
        except Exception:
            pass
    if CONFIG_PATH.exists():
        CONFIG_PATH.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description="Retail Ops S3 event lake deployer (spec §4)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--backfill", action="store_true", help="load existing DynamoDB events into the lake")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--delete", action="store_true")
    args = ap.parse_args()
    if args.status:
        status(); return
    if args.delete:
        delete_stack(); return
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] event lake (EventBridge {BUS} -> Firehose -> S3 -> Glue/Athena)")
    cfg = apply_stack(args.apply, args.backfill)
    print(f"[{mode}] done. athena source = {cfg.get('athena_source')} bucket = {cfg.get('bucket')}")


if __name__ == "__main__":
    main()
