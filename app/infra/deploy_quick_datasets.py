#!/usr/bin/env python3
"""Make the Retail Ops event lake available to AWS Quick (QuickSight) as datasets.

The Quick-native dashboards/analyses are console-authored (spec
retail-ops-quick-project.md), but they need something to bind to: an Athena data
source over the lake workgroup and one dataset per lake table. This provisioner
creates exactly that, idempotently, so a Quick author opens the console and the
Retail Ops datasets are already there — no manual data-source wiring.

It first puts the inline policy `retail-ops-lake-and-data-read` on the Quick service
role (S3 lake read + Athena results write, workgroup `retail-ops-<env>-lake`, Glue
`retail_ops_<env>_lake`, DynamoDB `retail-ops-<env>-*` read), so these datasets are
immediately queryable in a fresh account. SPICE is NOT used — datasets are DIRECT_QUERY against
Athena so they always reflect the current lake (no refresh step).

Creates:
  * one Athena data source  `retail-ops-lake`  (workgroup retail-ops-<env>-lake)
  * one DIRECT_QUERY dataset per Glue table in `retail_ops_<env>_lake`
    (journey_events + the lake_* analytics tables), granted to the embed user.

Grants owner permissions on the data source and on every dataset it manages to
the Quick group `retail-ops-builders` and to each builder USER directly (the
group's current members plus the Quick user names in RETAIL_OPS_QUICK_BUILDERS;
names that are not registered Quick users are skipped with a note). The direct
grant is what makes them visible: the Quick console lists content by direct
user permission, not group membership, and a builder starts from these datasets.
The group comes from deploy_quick_groups (run it first); when it does not exist
the builder grants are skipped with a note. Grants only add, nothing is revoked.
The `retail-ops-oem-*` reader groups get nothing here: they open content
through the dashboards and Flows shared with them.

Usage:
  python3 -m app.infra.deploy_quick_datasets            # dry-run
  python3 -m app.infra.deploy_quick_datasets --apply    # create data source + datasets
  python3 -m app.infra.deploy_quick_datasets --status   # list Retail Ops datasets
  python3 -m app.infra.deploy_quick_datasets --delete   # remove Retail Ops datasets + data source
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from ..services.platform.quick import QUICK_BUILDERS_GROUP
from . import naming
from .deploy_quick_groups import _quick_names

ROOT = Path(__file__).resolve().parents[2]
REGION = "us-east-1"
DATABASE = naming.name_us("lake")
WORKGROUP = naming.name("lake")
DATA_SOURCE_ID = "retail-ops-lake"
DATA_SOURCE_NAME = "Retail Ops event lake (Athena)"
QUICK_SERVICE_ROLE_PREFIX = "aws-quicksight-service-role"
QUICK_ROLE_POLICY = "retail-ops-lake-and-data-read"  # teardown.py removes it by this name
LAKE_CONFIG = naming.config_path("lake")
QUICK_CONFIG = naming.config_path("quick")
QUICK_NAMESPACE = "default"
BUILDERS_ENV = "RETAIL_OPS_QUICK_BUILDERS"   # comma-separated Quick user names (deploy_quick_groups reads it too)

# The standard owner action sets Quick grants the owner of a data source / dataset.
DATA_SOURCE_OWNER_ACTIONS = (
    "quicksight:DescribeDataSource", "quicksight:DescribeDataSourcePermissions",
    "quicksight:PassDataSource", "quicksight:UpdateDataSource",
    "quicksight:DeleteDataSource", "quicksight:UpdateDataSourcePermissions")
DATASET_OWNER_ACTIONS = (
    "quicksight:DescribeDataSet", "quicksight:DescribeDataSetPermissions",
    "quicksight:PassDataSet", "quicksight:DescribeIngestion",
    "quicksight:ListIngestions", "quicksight:UpdateDataSet",
    "quicksight:DeleteDataSet", "quicksight:CreateIngestion",
    "quicksight:CancelIngestion", "quicksight:UpdateDataSetPermissions")


def _account(sts) -> str:
    return sts.get_caller_identity()["Account"]


def _embed_user_arn() -> str | None:
    if QUICK_CONFIG.exists():
        return json.loads(QUICK_CONFIG.read_text()).get("embed_user_arn")
    return None


# Quick row-level security was removed on 2026-10-21 (spec §13). A stack deployed
# before then can still hold its rule tables (`lake_rls_district`,
# `lake_rls_dealer`: user -> district/dealer) in the lake database; they are not
# analytics data, so never publish them as datasets. Teardown deletes them with
# the Glue database.
_EXCLUDED_PREFIXES = ("lake_rls_",)


def _lake_tables(glue) -> list[str]:
    """Every analytics table in the lake database (journey_events + lake_*),
    except leftover rule tables from before 2026-10-21."""
    out: list[str] = []
    paginator = glue.get_paginator("get_tables")
    for page in paginator.paginate(DatabaseName=DATABASE):
        out.extend(t["Name"] for t in page.get("TableList", [])
                   if not t["Name"].startswith(_EXCLUDED_PREFIXES))
    return sorted(out)


def _dataset_id(table: str) -> str:
    """`retail-ops-lake-<table>`, without the table's own `lake_` prefix, so
    `lake_marketintel` -> `retail-ops-lake-marketintel` (not `...-lake-lake-...`)."""
    name = table.removeprefix("lake_")
    return f"retail-ops-lake-{name.replace('_', '-')}"


def _lake_bucket(account: str) -> str:
    if LAKE_CONFIG.exists():
        bucket = json.loads(LAKE_CONFIG.read_text()).get("bucket")
        if bucket:
            return bucket
    return naming.name(f"event-lake-{account}")


def quick_role_policy(account: str, bucket: str) -> dict:
    """The inline grant AWS Quick's service role needs to query the lake through
    Athena: read the lake, write Athena results, use the workgroup and the Glue
    catalog, and read the solution's DynamoDB tables."""
    b = f"arn:aws:s3:::{bucket}"
    glue = f"arn:aws:glue:{REGION}:{account}"
    return {"Version": "2012-10-17", "Statement": [
        {"Sid": "LakeRead", "Effect": "Allow",
         "Action": ["s3:GetObject", "s3:ListBucket", "s3:GetBucketLocation"],
         "Resource": [b, f"{b}/*"]},
        {"Sid": "AthenaResultsWrite", "Effect": "Allow",
         "Action": ["s3:PutObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"],
         "Resource": f"{b}/athena-results/*"},
        {"Sid": "AthenaWorkgroup", "Effect": "Allow",
         "Action": ["athena:StartQueryExecution", "athena:GetQueryExecution",
                    "athena:GetQueryResults", "athena:StopQueryExecution", "athena:GetWorkGroup"],
         "Resource": f"arn:aws:athena:{REGION}:{account}:workgroup/{WORKGROUP}"},
        {"Sid": "GlueCatalog", "Effect": "Allow",
         "Action": ["glue:GetDatabase", "glue:GetDatabases", "glue:GetTable", "glue:GetTables",
                    "glue:GetPartition", "glue:GetPartitions"],
         "Resource": [f"{glue}:catalog", f"{glue}:database/{DATABASE}",
                      f"{glue}:table/{DATABASE}/*"]},
        {"Sid": "DynamoRead", "Effect": "Allow",
         "Action": ["dynamodb:DescribeTable", "dynamodb:GetItem", "dynamodb:Query",
                    "dynamodb:Scan"],
         "Resource": f"arn:aws:dynamodb:{REGION}:{account}:table/{naming.PREFIX}*"},
    ]}


def ensure_quick_role_policy(iam, account: str, apply: bool) -> bool:
    """Put `retail-ops-lake-and-data-read` on the Quick service role (idempotent).

    Returns True when the policy was created or changed, so the caller can wait for
    IAM propagation before Quick tests the Athena connection.
    """
    doc = quick_role_policy(account, _lake_bucket(account))
    roles = [r["RoleName"] for page in iam.get_paginator("list_roles").paginate(PathPrefix="/service-role/")
             for r in page["Roles"] if r["RoleName"].startswith(QUICK_SERVICE_ROLE_PREFIX)]
    if not roles:
        print(f"  WARN: no {QUICK_SERVICE_ROLE_PREFIX}* role; is AWS Quick subscribed in this account?")
        return False
    changed = False
    for role in roles:
        try:
            current = iam.get_role_policy(RoleName=role, PolicyName=QUICK_ROLE_POLICY)["PolicyDocument"]
        except iam.exceptions.NoSuchEntityException:
            current = None
        if current == doc:
            print(f"  [iam] {role}/{QUICK_ROLE_POLICY} up to date")
            continue
        if not apply:
            print(f"  [dry-run] would put {QUICK_ROLE_POLICY} on {role}")
            continue
        iam.put_role_policy(RoleName=role, PolicyName=QUICK_ROLE_POLICY, PolicyDocument=json.dumps(doc))
        print(f"  [iam] put {QUICK_ROLE_POLICY} on {role}")
        changed = True
    return changed


def _wait_data_source(qs, account: str, timeout_s: int = 180) -> None:
    """Block until Quick has finished testing the data source; fail with its error."""
    import time
    deadline = time.monotonic() + timeout_s
    while True:
        d = qs.describe_data_source(AwsAccountId=account, DataSourceId=DATA_SOURCE_ID)["DataSource"]
        status = d.get("Status", "")
        if status.endswith("_SUCCESSFUL"):
            print(f"  [quick] data source {DATA_SOURCE_ID} {status}")
            return
        if status.endswith("_FAILED"):
            raise SystemExit(f"data source {DATA_SOURCE_ID} {status}: {d.get('ErrorInfo')}")
        if time.monotonic() > deadline:
            raise SystemExit(f"data source {DATA_SOURCE_ID} still {status} after {timeout_s}s")
        time.sleep(3)


def ensure_data_source(qs, account: str, user_arn: str | None, apply: bool) -> str:
    arn = f"arn:aws:quicksight:{REGION}:{account}:datasource/{DATA_SOURCE_ID}"
    if not apply:
        print(f"  [dry-run] would ensure Athena data source {DATA_SOURCE_ID} (workgroup {WORKGROUP})")
        return arn
    params = {
        "AwsAccountId": account, "DataSourceId": DATA_SOURCE_ID, "Name": DATA_SOURCE_NAME,
        "Type": "ATHENA",
        "DataSourceParameters": {"AthenaParameters": {"WorkGroup": WORKGROUP}},
    }
    if user_arn:
        params["Permissions"] = [{"Principal": user_arn, "Actions": list(DATA_SOURCE_OWNER_ACTIONS)}]
    try:
        qs.create_data_source(**params)
        print(f"  [quick] created data source {DATA_SOURCE_ID}")
    except qs.exceptions.ResourceExistsException:
        qs.update_data_source(AwsAccountId=account, DataSourceId=DATA_SOURCE_ID,
                              Name=DATA_SOURCE_NAME,
                              DataSourceParameters={"AthenaParameters": {"WorkGroup": WORKGROUP}})
        print(f"  [quick] data source {DATA_SOURCE_ID} exists (updated)")
    # Datasets cannot be created on a data source that is still being tested or
    # whose connection test failed, so wait and surface Quick's own error.
    _wait_data_source(qs, account)
    return arn


def ensure_dataset(qs, account: str, ds_arn: str, table: str, columns: list[dict],
                   user_arn: str | None, apply: bool) -> None:
    dsid = _dataset_id(table)
    if not apply:
        print(f"  [dry-run] would ensure dataset {dsid} over {DATABASE}.{table} ({len(columns)} cols)")
        return
    phys = {
        "lake-phys": {
            "RelationalTable": {
                "DataSourceArn": ds_arn,
                "Catalog": "AwsDataCatalog",
                "Schema": DATABASE,
                "Name": table,
                "InputColumns": columns,
            }
        }
    }
    params = {
        "AwsAccountId": account, "DataSetId": dsid, "Name": f"Retail Ops · {table}",
        "PhysicalTableMap": phys,
        "ImportMode": "DIRECT_QUERY",   # always current; no SPICE refresh needed
    }
    if user_arn:
        params["Permissions"] = [{"Principal": user_arn, "Actions": list(DATASET_OWNER_ACTIONS)}]
    try:
        qs.create_data_set(**params)
        print(f"  [quick] created dataset {dsid} ({table})")
    except qs.exceptions.ResourceExistsException:
        # UpdateDataSet replaces the definition: carry over any logical tables
        # (fields a Quick author added) so the update keeps them. A rule set
        # attached by a stack deployed before 2026-10-21 is not carried over, so
        # the update removes it: Quick datasets are not row-scoped (spec §13).
        current = qs.describe_data_set(AwsAccountId=account, DataSetId=dsid)["DataSet"]
        upd = {"AwsAccountId": account, "DataSetId": dsid, "Name": f"Retail Ops · {table}",
               "PhysicalTableMap": phys, "ImportMode": "DIRECT_QUERY"}
        if current.get("LogicalTableMap"):
            upd["LogicalTableMap"] = current["LogicalTableMap"]
        qs.update_data_set(**upd)
        removed = " (old row-level security removed)" if current.get("RowLevelPermissionDataSet") else ""
        print(f"  [quick] dataset {dsid} exists (updated){removed}")


def builders_group_arn(account: str) -> str:
    return f"arn:aws:quicksight:{REGION}:{account}:group/{QUICK_NAMESPACE}/{QUICK_BUILDERS_GROUP}"


def builders_principal(qs, account: str) -> str | None:
    """The `retail-ops-builders` group ARN, or None (with a note) when the group
    does not exist yet, so the grants are skipped rather than failing the stage."""
    try:
        qs.describe_group(AwsAccountId=account, Namespace=QUICK_NAMESPACE, GroupName=QUICK_BUILDERS_GROUP)
    except qs.exceptions.ResourceNotFoundException:
        print(f"  [grant] Quick group {QUICK_BUILDERS_GROUP} does not exist; skipping the builder grants "
              "(deploy_quick_groups --apply creates it, then re-run this step)")
        return None
    return builders_group_arn(account)


def quick_user_arn(account: str, name: str) -> str:
    return f"arn:aws:quicksight:{REGION}:{account}:user/{QUICK_NAMESPACE}/{name}"


def principal_name(arn: str) -> str:
    """`group/default/retail-ops-builders` -> `retail-ops-builders`;
    `user/default/admin/jdoe` -> `admin/jdoe`."""
    return arn.split(":", 5)[-1].split("/", 2)[-1]


def builder_user_arns(qs, account: str) -> tuple[str, ...]:
    """The builder USERS that get the owner grants directly, next to the group:
    the current members of `retail-ops-builders` plus the names in
    RETAIL_OPS_QUICK_BUILDERS, de-duplicated and sorted. The Quick console lists
    spaces, chat agents and Flows by direct user permission, not group
    membership. Names that are not registered Quick users are skipped with a note."""
    base = {"AwsAccountId": account, "Namespace": QUICK_NAMESPACE}
    try:
        members = _quick_names(qs.list_group_memberships, "GroupMemberList", "MemberName",
                               GroupName=QUICK_BUILDERS_GROUP, **base)
    except Exception as e:
        if "NotFound" not in type(e).__name__:
            raise
        members = set()
    listed = {b.strip() for b in os.environ.get(BUILDERS_ENV, "").split(",") if b.strip()}
    registered = _quick_names(qs.list_users, "UserList", "UserName", **base)
    users: list[str] = []
    for name in sorted(members | listed):
        if name in registered:
            users.append(name)
        else:
            print(f"  [builders] {name} is not a registered Quick user; no direct grant")
    print(f"  [builders] direct owner grants to {len(users)} builder user(s): {', '.join(users) or 'none'}")
    return tuple(quick_user_arn(account, n) for n in users)


# kind -> (describe call, update call, id parameter, owner actions)
_GRANT_API = {
    "data source": ("describe_data_source_permissions", "update_data_source_permissions",
                    "DataSourceId", DATA_SOURCE_OWNER_ACTIONS),
    "dataset": ("describe_data_set_permissions", "update_data_set_permissions",
                "DataSetId", DATASET_OWNER_ACTIONS),
}


def grant_owner(qs, kind: str, account: str, resource_id: str, principal: str, apply: bool) -> bool:
    """Give `principal` the owner actions on one data source or dataset.

    Idempotent: nothing is sent when the principal already holds every owner
    action. Dry run unless `apply`. Returns True when a grant was (or would be) made."""
    describe, update, id_key, actions = _GRANT_API[kind]
    label = f"{kind} {resource_id}"
    who = principal_name(principal)
    try:
        perms = getattr(qs, describe)(AwsAccountId=account, **{id_key: resource_id}).get("Permissions") or []
    except qs.exceptions.ResourceNotFoundException:
        perms = None  # not created yet (a dry run on a fresh account)
    have: set[str] = set()
    for p in perms or []:
        if p.get("Principal") == principal:
            have.update(p.get("Actions", []))
    if set(actions) <= have:
        print(f"  [grant] {who} already owns {label}")
        return False
    if not apply:
        print(f"  [dry-run] would grant {who} owner ({len(actions)} actions) on {label}"
              + (" once it exists" if perms is None else ""))
        return True
    getattr(qs, update)(AwsAccountId=account, **{id_key: resource_id},
                        GrantPermissions=[{"Principal": principal, "Actions": list(actions)}])
    print(f"  [grant] {who} owner ({len(actions)} actions) on {label}")
    return True


def _columns_for(glue, table: str) -> list[dict]:
    """Athena InputColumns from the Glue table schema (all STRING for the JSON
    SerDe tables; partition keys included so dashboards can filter by market)."""
    t = glue.get_table(DatabaseName=DATABASE, Name=table)["Table"]
    sd_cols = t["StorageDescriptor"]["Columns"]
    part_cols = t.get("PartitionKeys", [])
    seen = set()
    cols = []
    for c in list(sd_cols) + list(part_cols):
        name = c["Name"]
        if name in seen:
            continue
        seen.add(name)
        # QuickSight Athena input types: STRING/INTEGER/DECIMAL/DATETIME/BIT/BOOLEAN
        cols.append({"Name": name, "Type": "STRING"})
    return cols


def run(apply: bool) -> None:
    import boto3
    s = boto3.Session(region_name=REGION)
    sts, qs, glue = s.client("sts"), s.client("quicksight"), s.client("glue")
    account = _account(sts)
    user_arn = _embed_user_arn()
    print(f"account={account} region={REGION} embed_user={'set' if user_arn else 'none'}")
    tables = _lake_tables(glue)
    if not tables:
        print(f"  no tables in {DATABASE} — run deploy_lake / deploy_lake_analytics first")
        return
    if ensure_quick_role_policy(s.client("iam"), account, apply):
        import time
        print("  [iam] waiting 20 s for the grant to propagate before Quick tests the connection")
        time.sleep(20)
    builders = builders_principal(qs, account)
    # The group and each builder user directly (the console lists by direct permission).
    owners = (builders, *builder_user_arns(qs, account)) if builders else ()
    ds_arn = ensure_data_source(qs, account, user_arn, apply)
    for owner in owners:
        grant_owner(qs, "data source", account, DATA_SOURCE_ID, owner, apply)
    for table in tables:
        cols = _columns_for(glue, table)
        ensure_dataset(qs, account, ds_arn, table, cols, user_arn, apply)
        for owner in owners:
            grant_owner(qs, "dataset", account, _dataset_id(table), owner, apply)
    print(f"  {'created' if apply else 'would create'} {len(tables)} dataset(s): {', '.join(tables)}")


def status() -> None:
    import boto3
    s = boto3.Session(region_name=REGION)
    account = _account(s.client("sts"))
    qs = s.client("quicksight")
    dss = qs.list_data_sources(AwsAccountId=account).get("DataSources", [])
    retail_ops_ds = [d for d in dss if d.get("DataSourceId") == DATA_SOURCE_ID]
    print(f"data source {DATA_SOURCE_ID}: {'present' if retail_ops_ds else 'MISSING'}"
          + (f" ({retail_ops_ds[0].get('Status')})" if retail_ops_ds else ""))
    sets = qs.list_data_sets(AwsAccountId=account).get("DataSetSummaries", [])
    retail_ops_sets = [d for d in sets if d.get("DataSetId", "").startswith("retail-ops-lake-")]
    print(f"Retail Ops datasets: {len(retail_ops_sets)}")
    for d in sorted(retail_ops_sets, key=lambda x: x.get("Name", "")):
        print(f"  {d.get('DataSetId')}  ·  {d.get('Name')}  ·  {d.get('ImportMode')}")


def delete() -> None:
    import boto3
    s = boto3.Session(region_name=REGION)
    account = _account(s.client("sts"))
    qs = s.client("quicksight")
    sets = qs.list_data_sets(AwsAccountId=account).get("DataSetSummaries", [])
    for d in sets:
        if d.get("DataSetId", "").startswith("retail-ops-lake-"):
            qs.delete_data_set(AwsAccountId=account, DataSetId=d["DataSetId"])
            print(f"  deleted dataset {d['DataSetId']}")
    try:
        qs.delete_data_source(AwsAccountId=account, DataSourceId=DATA_SOURCE_ID)
        print(f"  deleted data source {DATA_SOURCE_ID}")
    except Exception as e:  # noqa: BLE001
        print(f"  data source delete note: {e}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Provision AWS Quick datasets over the Retail Ops lake")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--delete", action="store_true")
    args = ap.parse_args()
    if args.status:
        status(); return
    if args.delete:
        delete(); return
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] provision AWS Quick datasets over {DATABASE}")
    run(args.apply)
    print(f"[{mode}] done.")


if __name__ == "__main__":
    main()
