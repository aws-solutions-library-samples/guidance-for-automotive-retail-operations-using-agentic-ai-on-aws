#!/usr/bin/env python3
"""Amazon Quick integration config (spec retail-ops-quick-project.md).

CORRECTION: Amazon Quick evolved from QuickSight — it is the **`quicksight`
service** (QuickSight continues as "Amazon Quick Sight", a feature within Quick;
all QuickSight APIs still work). The earlier Amazon **Q Business** (`qbusiness`/
`qapps`) build targeted the DEPRECATED product and has been removed.

The Quick-native build surface — Quick Flows, Quick Automate connectors, Quick
Sight dashboards, Quick Index — lives in **Amazon Quick** on its own lifecycle,
per the spec: deploy_quick_content.py creates what has an API, the rest is built
in the Quick console (docs/QUICK_BUILD.md). What this deployer does is
**discover and record** the account's Quick resources so the Retail Ops Quick
provider can consume them:

  * Quick Flows        (quicksight ListFlows)
  * Quick Sight dashboards (quicksight ListDashboards) for embedding
  * a Quick Sight user   (for registered-user embed URLs)
  * the default Quick↔app link (DescribeDefaultQBusinessApplication)

Writes app/infra/quick-config.json {region, flows[], dashboards[], embed_user_arn}
which platform/quick.py + the QuickSight embed path read. Idempotent, read-only
discovery (no create). --status prints what's found; --delete clears the config.
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
CONFIG_PATH = naming.config_path("quick")


def discover(qs, sts, account: str) -> dict:
    cfg: dict = {"region": REGION, "service": "quicksight (Amazon Quick)"}

    # Quick Flows (console-authored; discovered here)
    try:
        flows = qs.list_flows(AwsAccountId=account).get("FlowSummaryList", [])
        cfg["flows"] = [{"flow_id": f.get("FlowId"), "name": f.get("Name")} for f in flows]
    except Exception as e:
        cfg["flows_error"] = str(e)[:120]

    # Quick Sight dashboards (for embedding)
    try:
        dbs = qs.list_dashboards(AwsAccountId=account).get("DashboardSummaryList", [])
        cfg["dashboards"] = [{"dashboard_id": d.get("DashboardId"), "name": d.get("Name")} for d in dbs]
    except Exception as e:
        cfg["dashboards_error"] = str(e)[:120]

    # Quick Automate action connectors
    try:
        acs = qs.list_action_connectors(AwsAccountId=account).get("ActionConnectorSummaries", [])
        cfg["action_connectors"] = [{"id": a.get("ActionConnectorId"), "name": a.get("Name")} for a in acs]
    except Exception:
        pass

    # a Quick Sight user for registered-user embed URLs (prefer the caller)
    try:
        caller = sts.get_caller_identity().get("Arn", "")
        users = qs.list_users(AwsAccountId=account, Namespace="default").get("UserList", [])
        me = next((u for u in users if u.get("UserName") and u["UserName"] in caller), None) or (users[0] if users else None)
        if me:
            cfg["embed_user_arn"] = me.get("Arn")
            cfg["embed_user_name"] = me.get("UserName")
    except Exception as e:
        cfg["users_error"] = str(e)[:120]

    # default Quick↔app link
    try:
        d = qs.describe_default_q_business_application(AwsAccountId=account)
        if d.get("ApplicationId"):
            cfg["default_q_application_id"] = d["ApplicationId"]
    except Exception:
        pass

    return cfg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Discover + record the account's Amazon Quick resources.")
    ap.add_argument("--apply", action="store_true", help="write quick-config.json")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--delete", action="store_true", help="remove quick-config.json")
    args = ap.parse_args(argv)

    import boto3
    s = boto3.Session(region_name=REGION)
    account = s.client("sts").get_caller_identity()["Account"]
    qs, sts = s.client("quicksight"), s.client("sts")
    print(f"account={account} region={REGION} service=quicksight (Amazon Quick)")

    if args.delete:
        if CONFIG_PATH.exists():
            CONFIG_PATH.unlink(); print("  removed quick-config.json")
        return 0
    if args.status:
        print(CONFIG_PATH.read_text() if CONFIG_PATH.exists() else "  (no quick-config.json)")
        return 0

    cfg = discover(qs, sts, account)
    print(f"  flows={len(cfg.get('flows', []))} dashboards={len(cfg.get('dashboards', []))} "
          f"connectors={len(cfg.get('action_connectors', []))} embed_user={cfg.get('embed_user_name')}")
    if args.apply:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
        print(f"  [config] wrote {CONFIG_PATH.relative_to(ROOT)}")
    else:
        print("  (dry-run; re-run with --apply to write quick-config.json)")
        print(json.dumps(cfg, indent=2)[:500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
