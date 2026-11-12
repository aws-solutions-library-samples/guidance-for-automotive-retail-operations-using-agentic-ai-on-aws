#!/usr/bin/env python3
"""§8 R2 deploy via boto3: the OEM account-plan approval state machine.

Models the approval as a Step Functions STANDARD state machine using the
`waitForTaskToken` callback pattern:

    SubmitTask (lambda:invoke.waitForTaskToken)  <-- pauses holding a task token
        |  the submit Lambda records the token on the plan (record_task_token)
        |  a human POST /approvals/plan/{id}/decide resumes the execution:
        |    approve -> SendTaskSuccess  -> Approved (terminal Succeed)
        |    reject  -> SendTaskFailure  -> Rejected (terminal Fail)

Creates, in the dev account:
  * Lambda        retail-ops-<env>-approvals-submit  (python3.12, reuses retail-ops-<env>-journeys-role)
  * IAM role      retail-ops-<env>-approvals-sfn-role  (SFN service role: invoke submit Lambda)
  * StateMachine  retail-ops-<env>-plan-approval  (STANDARD)

Also EXTENDS the shared exec role (retail-ops-<env>-journeys-role) inline policy so:
  * the journeys Lambda can states:StartExecution (submit_plan), and
  * states:SendTaskSuccess / states:SendTaskFailure (decide_plan).
This is additive — it re-writes the same inline policy carrying every statement
the earlier deployers granted (DynamoDB, bedrock, logs, events) PLUS states:*.

After apply, set the state machine ARN on the journeys Lambda so submit_plan and
decide_plan run in SFN mode:
    RETAIL_OPS_APPROVALS_SM_ARN=<arn>   (this deployer patches it onto the Lambda env)

dry-run default; --apply; --delete; --submit-test PLAN_ID starts one execution.
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
SUBMIT_FN = naming.name("approvals-submit")
EXEC_ROLE = iam_roles.ROLE_APPROVALS          # per-function submit role (scoped)
SFN_ROLE = naming.name("approvals-sfn-role")      # Step Functions service role
STATE_MACHINE = naming.name("plan-approval")
JOURNEYS_FN = naming.name("journeys-service")     # carries the API routes
SFN_POLICY = naming.name("approvals-sfn-inline")
RUNTIME = "python3.12"

# Deployer scripts never ship inside the Lambda zip.
_SKIP_FILES = {"devserver.py", "deploy_l4.py", "deploy_l6.py", "deploy_events.py",
               "deploy_approvals.py", "ddb_load.py", "cognito_setup.py", "iam_roles.py"}


def _zip_code() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for py in (ROOT / "app").rglob("*.py"):
            if "__pycache__" in py.parts or py.name in _SKIP_FILES:
                continue
            z.write(py, str(py.relative_to(ROOT)))
    return buf.getvalue()


def _wait_lambda(lam, name):
    for _ in range(30):
        st = lam.get_function(FunctionName=name)["Configuration"].get("LastUpdateStatus")
        if st in (None, "Successful"):
            return
        time.sleep(2)


# The API service role (iam_roles.service_policy) already grants
# states:StartExecution + SendTask* to the journeys Lambda, so no shared-role
# patching is needed here. The submit Lambda gets its own scoped role
# (iam_roles.ROLE_APPROVALS) which the SFN invokes.

# --- Step Functions service role --------------------------------------------

def ensure_sfn_role(iam, account, submit_arn, apply) -> str:
    role_arn = f"arn:aws:iam::{account}:role/{SFN_ROLE}"
    if not apply:
        print(f"  [role] would ensure {SFN_ROLE} (invoke {SUBMIT_FN})"); return role_arn
    trust = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Principal": {"Service": "states.amazonaws.com"}, "Action": "sts:AssumeRole"}]}
    try:
        iam.get_role(RoleName=SFN_ROLE)
        print(f"  [role] reusing {SFN_ROLE}")
    except iam.exceptions.NoSuchEntityException:
        iam.create_role(RoleName=SFN_ROLE, AssumeRolePolicyDocument=json.dumps(trust),
                        Description="Retail Ops plan-approval state machine service role")
        print(f"  [role] created {SFN_ROLE}")
    pol = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": ["lambda:InvokeFunction"],
         "Resource": f"arn:aws:lambda:{REGION}:{account}:function:{SUBMIT_FN}"}]}
    iam.put_role_policy(RoleName=SFN_ROLE, PolicyName=SFN_POLICY,
                        PolicyDocument=json.dumps(pol))
    return role_arn


# --- submit-task Lambda ------------------------------------------------------

def ensure_submit_lambda(lam, role_arn, code, apply) -> str:
    env = {"RETAIL_OPS_ENV": naming.ENV, "RETAIL_OPS_ENV_PREFIX": naming.PREFIX}
    handler = "app.services.approvals.submit_task_handler.lambda_handler"
    try:
        lam.get_function(FunctionName=SUBMIT_FN); exists = True
    except lam.exceptions.ResourceNotFoundException:
        exists = False
    if not apply:
        print(f"  [lambda] {'would update' if exists else 'would create'} {SUBMIT_FN}")
        return f"arn:aws:lambda:{REGION}:...:function:{SUBMIT_FN}"
    if exists:
        lam.update_function_code(FunctionName=SUBMIT_FN, ZipFile=code); _wait_lambda(lam, SUBMIT_FN)
        lam.update_function_configuration(FunctionName=SUBMIT_FN, Handler=handler, Role=role_arn,
            Runtime=RUNTIME, Timeout=30, MemorySize=256, Environment={"Variables": env}); _wait_lambda(lam, SUBMIT_FN)
        print(f"  [lambda] updated {SUBMIT_FN}")
    else:
        lam.create_function(FunctionName=SUBMIT_FN, Runtime=RUNTIME, Role=role_arn, Handler=handler,
            Code={"ZipFile": code}, Timeout=30, MemorySize=256, Environment={"Variables": env}); _wait_lambda(lam, SUBMIT_FN)
        print(f"  [lambda] created {SUBMIT_FN}")
    from app.infra import freshness
    freshness.stamp_lambda(lam, SUBMIT_FN, code)   # verify.sh freshness: which source this Lambda runs
    return lam.get_function(FunctionName=SUBMIT_FN)["Configuration"]["FunctionArn"]


# --- state machine -----------------------------------------------------------

def state_machine_definition(submit_arn: str) -> dict:
    """SubmitTask (waitForTaskToken) -> Approved | Rejected."""
    return {
        "Comment": "OEM account-plan approval (human callback via waitForTaskToken)",
        "StartAt": "SubmitTask",
        "States": {
            "SubmitTask": {
                "Type": "Task",
                "Resource": "arn:aws:states:::lambda:invoke.waitForTaskToken",
                # Bound the human wait: an undecided plan fails (-> Rejected) after
                # 7 days rather than holding the execution open indefinitely.
                "TimeoutSeconds": 604800,
                "Parameters": {
                    "FunctionName": submit_arn,
                    "Payload": {
                        "plan_id.$": "$.plan_id",
                        "submitted_by.$": "$.submitted_by",
                        "taskToken.$": "$$.Task.Token",
                    },
                },
                "Next": "Approved",
                "Catch": [{"ErrorEquals": ["States.ALL"], "Next": "Rejected"}],
            },
            "Approved": {"Type": "Succeed"},
            "Rejected": {"Type": "Fail", "Error": "Rejected", "Cause": "Plan rejected by approver"},
        },
    }


def ensure_state_machine(sfn, account, role_arn, submit_arn, apply) -> str:
    sm_arn = f"arn:aws:states:{REGION}:{account}:stateMachine:{STATE_MACHINE}"
    definition = json.dumps(state_machine_definition(submit_arn))
    if not apply:
        print(f"  [sfn] would ensure state machine {STATE_MACHINE}"); return sm_arn
    existing = None
    for sm in sfn.list_state_machines()["stateMachines"]:
        if sm["name"] == STATE_MACHINE:
            existing = sm["stateMachineArn"]; break
    if existing:
        sfn.update_state_machine(stateMachineArn=existing, definition=definition, roleArn=role_arn)
        print(f"  [sfn] updated {STATE_MACHINE}")
        return existing
    resp = sfn.create_state_machine(name=STATE_MACHINE, definition=definition, roleArn=role_arn,
                                    type="STANDARD")
    print(f"  [sfn] created {STATE_MACHINE}")
    return resp["stateMachineArn"]


def patch_journeys_env(lam, sm_arn, apply):
    """Point the journeys Lambda at the state machine so submit/decide run in
    SFN mode (start_execution + send_task_*)."""
    if not apply:
        print("  [lambda] would set RETAIL_OPS_APPROVALS_SM_ARN on journeys Lambda"); return
    try:
        cfg = lam.get_function(FunctionName=JOURNEYS_FN)["Configuration"]
    except lam.exceptions.ResourceNotFoundException:
        print(f"  [lambda] {JOURNEYS_FN} not found — skipping env patch"); return
    env = (cfg.get("Environment") or {}).get("Variables", {})
    env["RETAIL_OPS_APPROVALS_SM_ARN"] = sm_arn
    lam.update_function_configuration(FunctionName=JOURNEYS_FN, Environment={"Variables": env})
    _wait_lambda(lam, JOURNEYS_FN)
    print(f"  [lambda] RETAIL_OPS_APPROVALS_SM_ARN set on {JOURNEYS_FN}")


def delete_stack(sfn, lam, iam, account):
    for sm in sfn.list_state_machines()["stateMachines"]:
        if sm["name"] == STATE_MACHINE:
            sfn.delete_state_machine(stateMachineArn=sm["stateMachineArn"])
            print(f"  deleted state machine {STATE_MACHINE}")
    try:
        lam.delete_function(FunctionName=SUBMIT_FN); print(f"  deleted lambda {SUBMIT_FN}")
    except lam.exceptions.ResourceNotFoundException:
        pass
    for pol in (SFN_POLICY,):
        try:
            iam.delete_role_policy(RoleName=SFN_ROLE, PolicyName=pol)
        except Exception:
            pass
    try:
        iam.delete_role(RoleName=SFN_ROLE); print(f"  deleted role {SFN_ROLE}")
    except Exception:
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Deploy the §8 R2 plan-approval state machine.")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--delete", action="store_true")
    ap.add_argument("--submit-test", metavar="PLAN_ID", help="start one execution for a plan")
    args = ap.parse_args(argv)

    import boto3
    s = boto3.Session(region_name=REGION)
    account = s.client("sts").get_caller_identity()["Account"]
    lam, iam, sfn = s.client("lambda"), s.client("iam"), s.client("stepfunctions")
    print(f"account={account} region={REGION}")

    if args.delete:
        delete_stack(sfn, lam, iam, account); return 0

    if args.submit_test:
        sm_arn = f"arn:aws:states:{REGION}:{account}:stateMachine:{STATE_MACHINE}"
        r = sfn.start_execution(stateMachineArn=sm_arn,
            input=json.dumps({"plan_id": args.submit_test, "submitted_by": "deploy-test"}))
        print("started execution:", r["executionArn"])
        return 0

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] deploying plan-approval state machine")
    code = _zip_code(); print(f"  [pkg] app zip = {len(code)} bytes")

    # submit Lambda gets its own scoped role (read all + PutItem on plan tables).
    exec_arn = iam_roles.ensure_role(iam, account, EXEC_ROLE, args.apply)
    if args.apply:
        time.sleep(8)  # let the submit role propagate before create_function
    submit_arn = ensure_submit_lambda(lam, exec_arn, code, args.apply)
    sfn_role_arn = ensure_sfn_role(iam, account, submit_arn, args.apply)
    if args.apply:
        time.sleep(8)  # let the new SFN role propagate before create_state_machine
    sm_arn = ensure_state_machine(sfn, account, sfn_role_arn, submit_arn, args.apply)
    patch_journeys_env(lam, sm_arn, args.apply)
    print(f"[{mode}] done. state_machine_arn={sm_arn}"
          + ("" if args.apply else "  (re-run with --apply)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
