#!/usr/bin/env python3
"""§8 deploy via boto3: the event backbone.

Creates, in the dev account:
  * EventBus      app-bus-<env>
  * Lambda        retail-ops-<env>-event-subscriber (python3.12, reuses retail-ops-<env>-journeys-role)
  * Rule          retail-ops-<env>-vehicle-delivered on app-bus-<env>, pattern
                  {source:[retail-ops.journeys], detail-type:[<every subscribed type>]} ->
                  subscriber. The type list comes from bus.subscribed_types(), so a
                  new subscriber is routed live by re-running this deployer. (Rule
                  name kept from v1 so re-runs update in place.)
  * DLQ           SQS retail-ops-<env>-event-subscriber-dlq on the rule target, with a
                  bounded retry policy (spec §4.6: DLQ per subscriber)
  * Permission    lets EventBridge invoke the subscriber Lambda

The journeys Lambda already carries the exec role; to let the deployed /events
route PUBLISH, we add events:PutEvents to that role's inline policy here (applied
live). The subscriber then opens the first service journey (A7) — the two
supervisors meet through the bus.

dry-run default; --apply; --delete; --publish-test emits a live vehicle.delivered.
"""
from __future__ import annotations

import argparse
import io
import json
import time
import zipfile
from pathlib import Path

try:
    from app.infra import iam_roles, naming
except ImportError:
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.infra import iam_roles, naming

ROOT = Path(__file__).resolve().parents[2]
REGION = "us-east-1"
BUS_NAME = naming.EVENT_BUS
SUB_FN = naming.name("event-subscriber")
DLQ_NAME = naming.name("event-subscriber-dlq")
EXEC_ROLE = iam_roles.ROLE_SUBSCRIBER   # per-function subscriber role (scoped)
RULE_NAME = naming.name("vehicle-delivered")
RUNTIME = "python3.12"


def _zip_code() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for py in (ROOT / "app").rglob("*.py"):
            if "__pycache__" in py.parts or py.name in ("devserver.py", "deploy_l4.py",
                                                        "deploy_l6.py", "deploy_events.py",
                                                        "deploy_approvals.py", "ddb_load.py",
                                                        "cognito_setup.py", "iam_roles.py"):
                continue
            z.write(py, str(py.relative_to(ROOT)))
    return buf.getvalue()


def _wait(lam, name):
    for _ in range(30):
        if lam.get_function(FunctionName=name)["Configuration"].get("LastUpdateStatus") in (None, "Successful"):
            return
        time.sleep(2)


def ensure_bus(eb, apply):
    if not apply:
        print(f"  [bus] would ensure {BUS_NAME}"); return
    try:
        eb.describe_event_bus(Name=BUS_NAME); print(f"  [bus] reusing {BUS_NAME}")
    except eb.exceptions.ResourceNotFoundException:
        eb.create_event_bus(Name=BUS_NAME); print(f"  [bus] created {BUS_NAME}")


def ensure_subscriber(lam, role_arn, code, apply) -> str:
    env = {"RETAIL_OPS_ENV": naming.ENV, "RETAIL_OPS_ENV_PREFIX": naming.PREFIX, "RETAIL_OPS_EVENT_BUS": BUS_NAME}
    try:
        lam.get_function(FunctionName=SUB_FN); exists = True
    except lam.exceptions.ResourceNotFoundException:
        exists = False
    if not apply:
        print(f"  [lambda] {'would update' if exists else 'would create'} {SUB_FN}")
        return f"(arn {SUB_FN})"
    if exists:
        lam.update_function_code(FunctionName=SUB_FN, ZipFile=code); _wait(lam, SUB_FN)
        lam.update_function_configuration(FunctionName=SUB_FN,
            Handler="app.services.events.subscriber_handler.lambda_handler", Role=role_arn,
            Runtime=RUNTIME, Timeout=30, MemorySize=256, Environment={"Variables": env}); _wait(lam, SUB_FN)
        print(f"  [lambda] updated {SUB_FN}")
    else:
        lam.create_function(FunctionName=SUB_FN, Runtime=RUNTIME, Role=role_arn,
            Handler="app.services.events.subscriber_handler.lambda_handler",
            Code={"ZipFile": code}, Timeout=30, MemorySize=256, Environment={"Variables": env}); _wait(lam, SUB_FN)
        print(f"  [lambda] created {SUB_FN}")
    from app.infra import freshness
    freshness.stamp_lambda(lam, SUB_FN, code)   # verify.sh freshness: which source this Lambda runs
    return lam.get_function(FunctionName=SUB_FN)["Configuration"]["FunctionArn"]


def _subscribed_types() -> list[str]:
    import sys
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from app.services.events import bus
    return bus.subscribed_types()


def ensure_dlq(sqs, account, apply) -> str:
    arn = f"arn:aws:sqs:{REGION}:{account}:{DLQ_NAME}"
    if not apply:
        print(f"  [dlq] would ensure {DLQ_NAME}"); return arn
    url = sqs.create_queue(QueueName=DLQ_NAME,
                           Attributes={"MessageRetentionPeriod": str(14 * 24 * 3600)})["QueueUrl"]
    rule_arn = f"arn:aws:events:{REGION}:{account}:rule/{BUS_NAME}/{RULE_NAME}"
    policy = {"Version": "2012-10-17", "Statement": [{
        "Sid": "EventBridgeDLQ", "Effect": "Allow", "Principal": {"Service": "events.amazonaws.com"},
        "Action": "sqs:SendMessage", "Resource": arn,
        "Condition": {"ArnEquals": {"aws:SourceArn": rule_arn}}}]}
    sqs.set_queue_attributes(QueueUrl=url, Attributes={"Policy": json.dumps(policy)})
    print(f"  [dlq] ensured {DLQ_NAME}")
    return arn


def ensure_rule(eb, lam, account, sub_arn, apply, dlq_arn=None):
    types = _subscribed_types()
    if not apply:
        print(f"  [rule] would ensure {RULE_NAME} on {BUS_NAME} for {len(types)} types: {types}"); return
    pattern = {"source": ["retail-ops.journeys"], "detail-type": types}
    eb.put_rule(Name=RULE_NAME, EventBusName=BUS_NAME, EventPattern=json.dumps(pattern), State="ENABLED",
                Description=f"{len(types)} subscribed event types -> subscriber (§4.6/§4.10.2)")
    target = {"Id": "subscriber", "Arn": sub_arn,
              "RetryPolicy": {"MaximumRetryAttempts": 8, "MaximumEventAgeInSeconds": 3600}}
    if dlq_arn:
        target["DeadLetterConfig"] = {"Arn": dlq_arn}
    eb.put_targets(Rule=RULE_NAME, EventBusName=BUS_NAME, Targets=[target])
    sid = f"eb-invoke-{RULE_NAME}"
    rule_arn = f"arn:aws:events:{REGION}:{account}:rule/{BUS_NAME}/{RULE_NAME}"
    try:
        lam.remove_permission(FunctionName=SUB_FN, StatementId=sid)
    except lam.exceptions.ResourceNotFoundException:
        pass
    lam.add_permission(FunctionName=SUB_FN, StatementId=sid, Action="lambda:InvokeFunction",
                       Principal="events.amazonaws.com", SourceArn=rule_arn)
    print(f"  [rule] created {RULE_NAME} -> {SUB_FN}")


def delete_stack(eb, lam):
    try:
        eb.remove_targets(Rule=RULE_NAME, EventBusName=BUS_NAME, Ids=["subscriber"])
        eb.delete_rule(Name=RULE_NAME, EventBusName=BUS_NAME); print(f"  deleted rule {RULE_NAME}")
    except Exception:
        pass
    try:
        lam.delete_function(FunctionName=SUB_FN); print(f"  deleted lambda {SUB_FN}")
    except lam.exceptions.ResourceNotFoundException:
        pass
    try:
        eb.delete_event_bus(Name=BUS_NAME); print(f"  deleted bus {BUS_NAME}")
    except Exception:
        pass
    try:
        import boto3
        sqs = boto3.client("sqs", region_name=REGION)
        sqs.delete_queue(QueueUrl=sqs.get_queue_url(QueueName=DLQ_NAME)["QueueUrl"])
        print(f"  deleted dlq {DLQ_NAME}")
    except Exception:
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Deploy the §8 event bus + subscriber.")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--delete", action="store_true")
    ap.add_argument("--publish-test", action="store_true", help="emit a live vehicle.delivered")
    args = ap.parse_args(argv)

    import boto3
    s = boto3.Session(region_name=REGION)
    account = s.client("sts").get_caller_identity()["Account"]
    eb, lam, iam, sqs = s.client("events"), s.client("lambda"), s.client("iam"), s.client("sqs")
    print(f"account={account} region={REGION}")

    if args.delete:
        delete_stack(eb, lam); return 0

    if args.publish_test:
        r = eb.put_events(Entries=[{"Source": "retail-ops.journeys", "DetailType": "vehicle.delivered",
            "Detail": json.dumps({"journey_id": "J-US-J5", "customer_id": "C-US-J5",
                                  "dealer_id": "D-107", "vehicle_model_id": "MER-VOLT-M", "market_id": "us"}),
            "EventBusName": BUS_NAME}])
        print("put_events:", json.dumps(r.get("Entries"), default=str), "failed:", r.get("FailedEntryCount"))
        return 0

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] deploying event bus")
    role_arn = iam_roles.ensure_role(iam, account, EXEC_ROLE, args.apply)
    if args.apply:
        time.sleep(8)  # let the subscriber role propagate before create_function
    code = _zip_code(); print(f"  [pkg] app zip = {len(code)} bytes")
    ensure_bus(eb, args.apply)
    sub_arn = ensure_subscriber(lam, role_arn, code, args.apply)
    dlq_arn = ensure_dlq(sqs, account, args.apply)
    ensure_rule(eb, lam, account, sub_arn, args.apply, dlq_arn)
    print(f"[{mode}] done." + ("" if args.apply else "  (re-run with --apply)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
