#!/usr/bin/env python3
"""Amazon Bedrock AgentCore Memory for the A3/A2 agents.

Customer facts were originally read only from DynamoDB (`CUST_*`,
`DEALER_Signals`). This deployer provisions the durable managed memory layer:
ONE AgentCore Memory resource that
`app/services/platform/memory.py` writes to (`create_event`, actor_id =
customer_id) and recalls from (`retrieve_memory_records`) in aws mode —
preferences stated, objections raised, promises made.

Creates, idempotently, in the dev account:
  1. AgentCore Memory  retail-ops-<env>-agent-memory  (control plane
     `bedrock-agentcore-control` create_memory). A long-term USER_PREFERENCE
     extraction strategy is requested so the service distills durable
     preferences from the raw events; if the account's API rejects the strategy
     shape, a plain (short-term/event) memory is created instead and the run
     reports which one it got.
  2. DynamoDB table  retail-ops-<env>-PLAT_AgentMemory  (on-demand) — the deterministic
     fallback + provenance store platform/memory.py writes ALWAYS (it is the
     only place source_ref/actor are guaranteed). Runtime-only: deliberately
     NOT registered in the seed loader; the local sandbox declares its key in
     local_ddb._RUNTIME_PK.
  3. app/infra/memory-config.json {memory_id, arn, ...} which deploy_l4 /
     deploy_l6 read into RETAIL_OPS_MEMORY_ID.

Dry-run by default; --apply to create; --delete to tear down.
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
NAME = naming.name("agent-memory")
# The control-plane name field allows [a-zA-Z0-9_] only; the memory id embeds it.
API_NAME = NAME.replace("-", "_")
TABLE = naming.name("PLAT_AgentMemory")
CONFIG_PATH = naming.config_path("memory")

# Raw events are kept 90 days; the long-term strategy's extracted records
# persist beyond that (the point of the resource).
EVENT_EXPIRY_DAYS = 90

# Namespace must match platform/memory.py's recall()
# (retrieve_memory_records namespace=f"/preferences/{customer_id}").
STRATEGY = {"userPreferenceMemoryStrategy": {
    "name": "CustomerPreferences",
    "description": "Durable customer preferences, objections and promises for the A3/A2 agents.",
    "namespaces": ["/preferences/{actorId}"],
}}


def _find_memory(ctl) -> dict | None:
    """The existing memory named NAME, if any (list may page)."""
    token = None
    while True:
        kw = {"nextToken": token} if token else {}
        try:
            page = ctl.list_memories(maxResults=100, **kw)
        except Exception as e:
            print(f"  [memory] list_memories failed: {type(e).__name__}: {e}")
            return None
        for m in page.get("memories", []):
            # Summaries carry id (which embeds the name) and sometimes the arn.
            if str(m.get("id", "")).startswith(API_NAME) or m.get("name") in (NAME, API_NAME):
                return m
        token = page.get("nextToken")
        if not token:
            return None


def _wait_active(ctl, mem_id: str) -> dict:
    for _ in range(60):
        m = ctl.get_memory(memoryId=mem_id)["memory"]
        if m.get("status") == "ACTIVE":
            return m
        if m.get("status") == "FAILED":
            raise RuntimeError(f"memory {mem_id} failed: {m.get('failureReason')}")
        time.sleep(5)
    return m


def ensure_memory(ctl, apply: bool) -> dict:
    existing = _find_memory(ctl)
    if not apply:
        print(f"  [memory] {'exists ' + str(existing.get('id')) if existing else 'would create'} {NAME} "
              f"(user-preference strategy, events kept {EVENT_EXPIRY_DAYS}d)")
        return existing or {"id": "(pending)", "arn": "(pending)"}
    if existing:
        print(f"  [memory] exists {NAME} ({existing.get('id')})")
        return ctl.get_memory(memoryId=existing["id"])["memory"]
    base = {"name": API_NAME,
            "description": "Retail Ops agent memory: customer preferences, objections, promises.",
            "eventExpiryDuration": EVENT_EXPIRY_DAYS}
    strategy_used = True
    try:
        mem = ctl.create_memory(**base, memoryStrategies=[STRATEGY])["memory"]
    except Exception as e:
        # The strategy parameter shape varies by SDK version; a plain memory
        # still gives create_event/list_events (short-term) semantics.
        print(f"  [memory] create with strategy failed ({type(e).__name__}: {e}); creating plain memory")
        strategy_used = False
        mem = ctl.create_memory(**base)["memory"]
    mem = _wait_active(ctl, mem["id"])
    print(f"  [memory] created {NAME} ({mem['id']}, status {mem.get('status')}, "
          f"{'user-preference strategy' if strategy_used else 'plain — no long-term strategy'})")
    mem["_strategy_used"] = strategy_used
    return mem


def ensure_table(dyn, apply: bool) -> None:
    """The PLAT_AgentMemory fallback/provenance table (on-demand billing)."""
    try:
        dyn.describe_table(TableName=TABLE)
        print(f"  [table] exists {TABLE}")
        return
    except dyn.exceptions.ResourceNotFoundException:
        pass
    if not apply:
        print(f"  [table] would create {TABLE} (pk memory_id, on-demand)")
        return
    dyn.create_table(TableName=TABLE,
                     AttributeDefinitions=[{"AttributeName": "memory_id", "AttributeType": "S"}],
                     KeySchema=[{"AttributeName": "memory_id", "KeyType": "HASH"}],
                     BillingMode="PAY_PER_REQUEST")
    dyn.get_waiter("table_exists").wait(TableName=TABLE)
    print(f"  [table] created {TABLE} (pk memory_id, on-demand)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Deploy the AgentCore Memory for the agents.")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--delete", action="store_true")
    args = ap.parse_args(argv)

    import boto3
    s = boto3.Session(region_name=REGION)
    account = s.client("sts").get_caller_identity()["Account"]
    ctl, dyn = s.client("bedrock-agentcore-control"), s.client("dynamodb")
    print(f"account={account} region={REGION} memory={NAME}")

    if args.delete:
        existing = _find_memory(ctl)
        if existing:
            ctl.delete_memory(memoryId=existing["id"])
            print(f"  deleted memory {existing['id']}")
        try:
            dyn.delete_table(TableName=TABLE)
            print(f"  deleted table {TABLE}")
        except dyn.exceptions.ResourceNotFoundException:
            pass
        if CONFIG_PATH.exists():
            CONFIG_PATH.unlink()
        return 0

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] deploying AgentCore Memory")
    mem = ensure_memory(ctl, args.apply)
    ensure_table(dyn, args.apply)
    if args.apply:
        cfg = {"memory_id": mem.get("id"), "arn": mem.get("arn"), "name": NAME,
               "region": REGION, "event_expiry_days": EVENT_EXPIRY_DAYS,
               "fallback_table": TABLE,
               "strategy": "userPreference" if mem.get("_strategy_used", True) else "none"}
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")
        print(f"  wrote {CONFIG_PATH.relative_to(ROOT)}")
        print("  next: redeploy l4 / l6 so RETAIL_OPS_MEMORY_ID reaches the Lambdas")
    print(f"[{mode}] done." + ("" if args.apply else "  (re-run with --apply)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
