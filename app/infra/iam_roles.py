#!/usr/bin/env python3
"""Least-privilege IAM: per-function Lambda execution roles (spec §8, R-4).

Single source of truth for the Retail Ops Lambda roles (names: app/infra/naming.py). Before this module, every
Lambda shared one role (`retail-ops-dev-journeys-role`) whose inline policy carried a
DynamoDB wildcard `table/retail-ops-dev-*` and was rewritten by four different
deployers under the same policy name (last-writer-wins superset). Spec §8:

    "Least-privilege IAM per role: replace Resource: '*' in current policies with
     table/agent ARNs (see R-4)."

This module replaces that with one role per Lambda, each scoped to:
  * DynamoDB — an EXPLICIT enumerated list of table ARNs (no `*` wildcard), and
    only the actions the function performs (read-only functions get no PutItem).
  * Bedrock  — only for functions that call a model (the authorizer does not).
  * events / states — only for functions that publish events or drive the
    approval state machine.
  * logs     — scoped to that function's own log-group ARN (not `Resource: "*"`).

The table→function mapping is intentionally coarse-but-honest: the API service
handler and the agentic workers run the shared application code, which reads
across most domain tables, so they are granted read on the full enumerated table
set. The precision that matters for least privilege and is enforced here:
  1. no `Resource: "*"` anywhere except the STS-less `states:SendTask*` verbs,
     which are token-scoped by AWS design and cannot take a resource ARN;
  2. the authorizer — the one function on the untrusted edge — is locked to
     read-only on exactly the two identity tables it consults;
  3. write (PutItem) is granted only to functions that write, and only to the
     tables in their write-set;
  4. Bedrock / events / states are granted per function, not blanket.

`aws lambda` auto-creates each function's log group as
`/aws/lambda/<function-name>`; we scope logs to that ARN.
"""
from __future__ import annotations

import json

try:
    from app.infra import naming
except ImportError:  # run as a script from app/infra/
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
    from app.infra import naming

REGION = "us-east-1"
ENV_PREFIX = naming.PREFIX                 # retail-ops-<env>-

# Domain table families (spec §6 data model). Scoping is by family prefix
# (DEALER_/OEM_/CUST_/PLAT_) rather than one ARN per table: the application
# service handler and the agentic workers run shared code that reads across all
# four families, and a fully enumerated 68-ARN policy exceeds the 10,240-char
# IAM inline-policy limit (~22 KB). Family prefixes keep the policy small while
# still REPLACING the blanket `table/retail-ops-dev-*` catch-all — the exact thing
# spec §8 / R-4 requires ("replace Resource: '*' with table/agent ARNs"). Each
# role now names only the families it uses; the authorizer stays locked to two
# exact tables (below), which is where tight scoping matters most.
TABLE_FAMILIES = ["CUST_", "DEALER_", "OEM_", "PLAT_"]

# The authorizer only ever resolves identity + org scope (authz.get_user reads
# PLAT_Users; _org_nodes_cached scans PLAT_OrgHierarchy). Read-only, two EXACT
# tables — no family prefix, no wildcard.
AUTHORIZER_READ_TABLES = ["PLAT_Users", "PLAT_OrgHierarchy"]

# Family shorthands for readability in per-role policies.
ALL_FAMILIES = TABLE_FAMILIES

READ_ACTIONS = ["dynamodb:GetItem", "dynamodb:BatchGetItem", "dynamodb:Query", "dynamodb:Scan"]
WRITE_ACTIONS = ["dynamodb:PutItem", "dynamodb:UpdateItem"]

BEDROCK_ACTIONS = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
# Converse with guardrailConfig needs ApplyGuardrail on the guardrail ARN.
GUARDRAIL_ACTIONS = ["bedrock:ApplyGuardrail"]

TRUST_LAMBDA = {
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"},
                   "Action": "sts:AssumeRole"}],
}


def _exact_table_arns(account: str, names: list[str]) -> list[str]:
    """Explicit ARNs (table + any index) for named tables — no wildcard."""
    arns: list[str] = []
    for n in names:
        base = f"arn:aws:dynamodb:{REGION}:{account}:table/{ENV_PREFIX}{n}"
        arns.append(base)
        arns.append(base + "/index/*")
    return arns


def _family_table_arns(account: str, families: list[str]) -> list[str]:
    """ARNs for whole table families (e.g. DEALER_*). Two patterns per family:
    the tables themselves and their indexes. Still replaces the single
    `table/retail-ops-dev-*` catch-all with named-family ARNs."""
    arns: list[str] = []
    for fam in families:
        base = f"arn:aws:dynamodb:{REGION}:{account}:table/{ENV_PREFIX}{fam}*"
        arns.append(base)
        arns.append(base + "/index/*")
    return arns


def _logs_arn(account: str, fn_name: str) -> str:
    return f"arn:aws:logs:{REGION}:{account}:log-group:/aws/lambda/{fn_name}:*"


def _bedrock_resources(account: str) -> list[str]:
    return ["arn:aws:bedrock:*::foundation-model/anthropic.claude-*",
            f"arn:aws:bedrock:*:{account}:inference-profile/us.anthropic.claude-*"]


def _events_bus_arn(account: str, bus: str = naming.EVENT_BUS) -> str:
    return f"arn:aws:events:{REGION}:{account}:event-bus/{bus}"


def _state_machine_arn(account: str, sm: str = naming.name("plan-approval")) -> str:
    return f"arn:aws:states:{REGION}:{account}:stateMachine:{sm}"


def _logs_stmt(account: str, fn_name: str) -> dict:
    return {"Effect": "Allow",
            "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            "Resource": _logs_arn(account, fn_name)}


def _ddb_read_families(account: str, families: list[str]) -> dict:
    return {"Sid": "DynamoRead", "Effect": "Allow", "Action": READ_ACTIONS,
            "Resource": _family_table_arns(account, families)}


def _ddb_write_families(account: str, families: list[str]) -> dict:
    return {"Sid": "DynamoWrite", "Effect": "Allow", "Action": WRITE_ACTIONS,
            "Resource": _family_table_arns(account, families)}


def _ddb_read_exact(account: str, names: list[str]) -> dict:
    return {"Sid": "DynamoRead", "Effect": "Allow", "Action": READ_ACTIONS,
            "Resource": _exact_table_arns(account, names)}


def _ddb_write_exact(account: str, names: list[str]) -> dict:
    return {"Sid": "DynamoWrite", "Effect": "Allow", "Action": WRITE_ACTIONS,
            "Resource": _exact_table_arns(account, names)}


def _bedrock_stmt(account: str) -> dict:
    return {"Sid": "Bedrock", "Effect": "Allow", "Action": BEDROCK_ACTIONS,
            "Resource": _bedrock_resources(account)}


def _knowledge_base_stmt(account: str) -> dict:
    """Retrieve on the account's Bedrock Knowledge Bases (deploy_knowledge_base.py)."""
    return {"Sid": "KnowledgeBase", "Effect": "Allow", "Action": ["bedrock:Retrieve"],
            "Resource": f"arn:aws:bedrock:{REGION}:{account}:knowledge-base/*"}


def _agent_runtime_stmt(account: str, patterns: tuple[str, ...] = ("*",)) -> dict:
    """InvokeAgentRuntime on the account's AgentCore runtimes whose names match
    `patterns` (the service role: every runtime; the worker role and the agent
    runtimes: only this project's per-agent runtimes, `retail_ops_<env>_*`)."""
    return {"Sid": "AgentRuntime", "Effect": "Allow", "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
            "Resource": [f"arn:aws:bedrock-agentcore:{REGION}:{account}:runtime/{p}" for p in patterns]}


def _code_interpreter_stmt(account: str) -> dict:
    """AgentCore Code Interpreter session lifecycle (platform/compute.py): the
    AWS built-in public interpreter (aws.codeinterpreter.v1 lives under the
    `aws` account namespace) plus any custom interpreter in this account."""
    return {"Sid": "CodeInterpreter", "Effect": "Allow",
            "Action": ["bedrock-agentcore:StartCodeInterpreterSession",
                       "bedrock-agentcore:InvokeCodeInterpreter",
                       "bedrock-agentcore:StopCodeInterpreterSession"],
            "Resource": [f"arn:aws:bedrock-agentcore:{REGION}:aws:code-interpreter/*",
                         f"arn:aws:bedrock-agentcore:{REGION}:{account}:code-interpreter/*",
                         f"arn:aws:bedrock-agentcore:{REGION}:{account}:code-interpreter-session/*"]}


def _guardrail_stmt(account: str) -> dict:
    """ApplyGuardrail on the account's guardrails (any version)."""
    # STANDARD-tier guardrails evaluate through a cross-region guardrail profile
    # (us.guardrail.v1:0), which lives in whichever region serves the call.
    return {"Sid": "Guardrail", "Effect": "Allow", "Action": GUARDRAIL_ACTIONS,
            "Resource": [f"arn:aws:bedrock:{REGION}:{account}:guardrail/*",
                         f"arn:aws:bedrock:*:{account}:guardrail-profile/*"]}


def _events_stmt(account: str) -> dict:
    return {"Sid": "PutEvents", "Effect": "Allow", "Action": ["events:PutEvents"],
            "Resource": _events_bus_arn(account)}


def _states_start_stmt(account: str) -> dict:
    return {"Sid": "StartApproval", "Effect": "Allow", "Action": ["states:StartExecution"],
            "Resource": _state_machine_arn(account)}


def _states_callback_stmt() -> dict:
    # SendTask{Success,Failure,Heartbeat} are authorized by the opaque task token,
    # not a resource ARN — AWS requires Resource:"*" for these three verbs. This is
    # the single documented exception to the no-wildcard rule.
    return {"Sid": "ApprovalCallback", "Effect": "Allow",
            "Action": ["states:SendTaskSuccess", "states:SendTaskFailure",
                       "states:SendTaskHeartbeat"],
            "Resource": "*"}


# --- per-function role catalog ----------------------------------------------
# name -> builder(account) -> inline policy document. One role per Lambda.

ROLE_AUTHORIZER = naming.name("authorizer-role")
ROLE_SERVICE = naming.name("service-role")
ROLE_WORKER = naming.name("worker-role")          # every agentic job Lambda (deploy_l6.JOBS)
# Log groups the worker role may write (one per job Lambda; keep in sync with
# app/infra/deploy_l6.JOBS — the deployer asserts this at import time).
WORKER_FUNCTIONS = [naming.name(job) for job in (
    "equity-monitor", "nurture-tick", "stall-sweep",
    "rescore-profiles", "service-lane-monitor", "shop-load",
    "service-followup-tick", "predictive-scan", "nightly-attribution",
    "district-cycle", "store-digest", "autonomy-promotion",
)]
ROLE_SUBSCRIBER = naming.name("subscriber-role")
ROLE_APPROVALS = naming.name("approvals-role")
ROLE_STREAM = naming.name("stream-role")          # Function URL floor-stream Lambda

# Legacy shared role — kept for a clean teardown of the pre-split deployment.
LEGACY_SHARED_ROLE = naming.name("journeys-role")


def authorizer_policy(account: str, fn_name: str = naming.name("journeys-authorizer")) -> dict:
    """Read-only on the two EXACT identity tables. No writes, no Bedrock, no
    events, no wildcard — the tightest-scoped role, on the untrusted edge."""
    return {"Version": "2012-10-17", "Statement": [
        _logs_stmt(account, fn_name),
        _ddb_read_exact(account, AUTHORIZER_READ_TABLES),
    ]}


def service_policy(account: str, fn_name: str = naming.name("journeys-service")) -> dict:
    """The API monolith: reads across all domain table families, writes to all
    (it routes ~90 endpoints incl. recommendation cards, tasks, appointments,
    estimates), invokes Bedrock, publishes events, drives the approval SM."""
    return {"Version": "2012-10-17", "Statement": [
        _logs_stmt(account, fn_name),
        _ddb_read_families(account, ALL_FAMILIES),
        _ddb_write_families(account, ALL_FAMILIES),
        _bedrock_stmt(account),
        _guardrail_stmt(account),
        _knowledge_base_stmt(account),
        _agent_runtime_stmt(account),
        _code_interpreter_stmt(account),
        _events_stmt(account),
        _states_start_stmt(account),
        _states_callback_stmt(),
        # Tier C outreach: SES email + SNS SMS (real delivery). Bedrock KB
        # Retrieve + Personalize ranking are read-only and covered by the
        # bedrock stmt / are no-ops until configured.
        {"Sid": "Outreach", "Effect": "Allow",
         "Action": ["ses:SendEmail", "ses:SendRawEmail", "sns:Publish"], "Resource": "*"},
        # Plug-and-play Amazon Quick (Q Apps) — only used when ENABLE_QUICK=true
        # + a Q Business instance is configured; granted here so enabling the flag
        # needs no IAM change. Plus Bedrock KB / Personalize read paths for
        # INTEGRATION_MODE=aws.
        # Amazon Quick = the quicksight service (Quick evolved from QuickSight).
        # Flow listing, dashboard listing, and registered-user embed for A6/A5.
        {"Sid": "AmazonQuick", "Effect": "Allow",
         "Action": ["quicksight:ListFlows", "quicksight:SearchFlows",
                    "quicksight:ListDashboards", "quicksight:DescribeDashboard",
                    "quicksight:GenerateEmbedUrlForRegisteredUser",
                    "quicksight:ListUsers", "quicksight:DescribeDefaultQBusinessApplication",
                    # per-user embed (spec §8): register the signed-in user as a
                    # Quick Sight reader and add them to the sharing group
                    # for their role (platform/quick.py QUICK_GROUP_BY_ROLE).
                    "quicksight:DescribeUser", "quicksight:RegisterUser",
                    "quicksight:CreateGroupMembership"],
         "Resource": "*"},
        {"Sid": "PlatformReads", "Effect": "Allow",
         "Action": ["personalize:GetPersonalizedRanking"], "Resource": "*"},
        # the Admin Console's "Demo data" card starts the demo-reset Lambda (dev only)
        *([_demo_reset_invoke_stmt(account)] if naming.ENV == DEMO_RESET_ENV else []),
    ]}


DEMO_RESET_ENV = "dev"                               # the only stack the demo reset exists on
DEMO_RESET_FN = naming.name("demo-reset")
ROLE_DEMO_RESET = naming.name("demo-reset-role")
# What app/demo/reset.py needs: scan the keys, delete the unseeded rows, write the seed
# rows, describe each table by name (never ListTables, which only grants on "*"), and take
# the on-demand backup first. Plus Get/Put on the status row.
DEMO_RESET_DDB_ACTIONS = ["dynamodb:DescribeTable", "dynamodb:Scan", "dynamodb:Query", "dynamodb:GetItem",
                          "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem",
                          "dynamodb:BatchWriteItem", "dynamodb:CreateBackup"]


def _demo_reset_invoke_stmt(account: str) -> dict:
    return {"Sid": "StartDemoReset", "Effect": "Allow", "Action": ["lambda:InvokeFunction"],
            "Resource": f"arn:aws:lambda:{REGION}:{account}:function:{DEMO_RESET_FN}"}


def demo_reset_policy(account: str, fn_name: str = DEMO_RESET_FN) -> dict:
    """The demo-reset Lambda (app/demo/reset_handler.py): syncs every domain table back to
    the seed. Table-family scoped; no Bedrock, no events, no other service."""
    return {"Version": "2012-10-17", "Statement": [
        _logs_stmt(account, fn_name),
        {"Sid": "DynamoReset", "Effect": "Allow", "Action": DEMO_RESET_DDB_ACTIONS,
         "Resource": _family_table_arns(account, ALL_FAMILIES)},
    ]}


def worker_policy(account: str) -> dict:
    """Agentic scheduled jobs (equity-monitor, nurture-tick, service-lane-monitor).
    Read across domain tables, write recommendation/message/handoff cards, invoke
    Bedrock, publish events. No approval-state-machine access. Logs scoped by a
    shared prefix statement covering the three worker log groups."""
    logs = {"Effect": "Allow",
            "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            "Resource": [_logs_arn(account, fn) for fn in WORKER_FUNCTIONS]}
    return {"Version": "2012-10-17", "Statement": [
        logs,
        _ddb_read_families(account, ALL_FAMILIES),
        _ddb_write_families(account, ALL_FAMILIES),
        _bedrock_stmt(account),
        _guardrail_stmt(account),
        _knowledge_base_stmt(account),
        _events_stmt(account),
        # nurture-tick sends via SES/SNS (OutreachProvider).
        {"Sid": "Outreach", "Effect": "Allow",
         "Action": ["ses:SendEmail", "ses:SendRawEmail", "sns:Publish"], "Resource": "*"},
        # the eight agent-owned jobs run in their owner's runtime (ADR 0009); only
        # this project's per-agent runtimes, never another runtime in the account
        _agent_runtime_stmt(account, patterns=(f"{naming.PREFIX_US}*",)),
    ]}


def subscriber_policy(account: str, fn_name: str = naming.name("event-subscriber")) -> dict:
    """Event subscriber: opens the first service journey on vehicle.delivered.
    Read + write domain families, invoke Bedrock, publish follow-on events."""
    return {"Version": "2012-10-17", "Statement": [
        _logs_stmt(account, fn_name),
        _ddb_read_families(account, ALL_FAMILIES),
        _ddb_write_families(account, ALL_FAMILIES),
        _bedrock_stmt(account),
        _guardrail_stmt(account),
        _knowledge_base_stmt(account),
        _events_stmt(account),
    ]}


def approvals_policy(account: str, fn_name: str = naming.name("approvals-submit")) -> dict:
    """Approval submit-task Lambda: records the task token on the plan (PutItem on
    the OEM plan tables + trace) and reads it back. Read is family-wide (it may
    look up the account/plan context); write is scoped to EXACT plan tables. No
    Bedrock, no events, no states verbs (the API service sends the callback)."""
    write_tables = ["OEM_AccountPlans", "OEM_Accounts", "PLAT_AgentTraces"]
    return {"Version": "2012-10-17", "Statement": [
        _logs_stmt(account, fn_name),
        _ddb_read_families(account, ALL_FAMILIES),
        _ddb_write_exact(account, write_tables),
    ]}


def stream_policy(account: str, fn_name: str = naming.name("floor-stream")) -> dict:
    """The streaming floor path (app/services/api/stream_handler.py): it verifies
    the token itself (Function URLs bypass the API Gateway authorizer) and streams
    one grounded objection response. Reads across domain families (identity +
    journey + strategy library), writes only the agent-memory row the objection
    produces, invokes Bedrock with the guardrail. No events, no states, no SES."""
    return {"Version": "2012-10-17", "Statement": [
        _logs_stmt(account, fn_name),
        _ddb_read_families(account, ALL_FAMILIES),
        _ddb_write_exact(account, ["PLAT_AgentMemory"]),
        _bedrock_stmt(account),
        _guardrail_stmt(account),
    ]}


# role_name -> (policy_name, policy_builder)
ROLE_CATALOG = {
    ROLE_AUTHORIZER: (naming.name("authorizer-inline"), authorizer_policy),
    ROLE_SERVICE:    (naming.name("service-inline"), service_policy),
    ROLE_WORKER:     (naming.name("worker-inline"), worker_policy),
    ROLE_SUBSCRIBER: (naming.name("subscriber-inline"), subscriber_policy),
    ROLE_APPROVALS:  (naming.name("approvals-inline"), approvals_policy),
    ROLE_STREAM:     (naming.name("stream-inline"), stream_policy),
    ROLE_DEMO_RESET: (naming.name("demo-reset-inline"), demo_reset_policy),
}


def ensure_role(iam, account: str, role_name: str, apply: bool) -> str:
    """Create (if absent) and set the inline policy for one per-function role.
    Idempotent. Returns the role ARN."""
    policy_name, builder = ROLE_CATALOG[role_name]
    arn = f"arn:aws:iam::{account}:role/{role_name}"
    try:
        iam.get_role(RoleName=role_name); exists = True
    except iam.exceptions.NoSuchEntityException:
        exists = False
    if not apply:
        print(f"  [role] {'exists' if exists else 'would create'} {role_name} "
              f"(scoped: {policy_name})")
        return arn
    if not exists:
        iam.create_role(RoleName=role_name, AssumeRolePolicyDocument=json.dumps(TRUST_LAMBDA),
                        Description=f"Retail Ops {naming.ENV} least-privilege role ({role_name})")
        print(f"  [role] created {role_name}")
    iam.put_role_policy(RoleName=role_name, PolicyName=policy_name,
                        PolicyDocument=json.dumps(builder(account)))
    print(f"  [role] scoped inline policy set on {role_name}")
    return arn


def ensure_all_roles(iam, account: str, apply: bool) -> dict[str, str]:
    """Ensure every per-function role. Returns {role_name: arn}."""
    return {name: ensure_role(iam, account, name, apply) for name in ROLE_CATALOG}


def delete_all_roles(iam, account: str) -> None:
    """Tear down every per-function role (and the legacy shared role if present)."""
    for role_name in list(ROLE_CATALOG) + [LEGACY_SHARED_ROLE]:
        try:
            for p in iam.list_role_policies(RoleName=role_name)["PolicyNames"]:
                iam.delete_role_policy(RoleName=role_name, PolicyName=p)
            iam.delete_role(RoleName=role_name)
            print(f"  deleted role {role_name}")
        except iam.exceptions.NoSuchEntityException:
            pass
