"""§8 R2 — OEM account-plan approval workflow (Step Functions callback pattern).

An OEM account plan (OEM_AccountPlans) moves draft|brief -> submitted
(pending_approval) -> approved(published) | rejected. The approval is a HUMAN
step modeled as a Step Functions `waitForTaskToken`: submit starts an execution
that pauses holding a task token; a human decision resumes it via
SendTaskSuccess/Failure, and the plan status flips.

  submit_plan(plan_id, actor)            -> mark pending, start SFN execution
  decide_plan(plan_id, decision, actor)  -> send the callback, flip plan status
  plan_status(plan_id)                   -> current approval state

Degrades gracefully: if the Step Functions ARN isn't configured (local dev), the
status transitions are still recorded in DynamoDB so the flow is testable without
the state machine. The state machine, when present, is the durable coordinator.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any

from ..common import ddb
from ..events import bus as _bus

STATE_MACHINE_ARN = os.environ.get("RETAIL_OPS_APPROVALS_SM_ARN", "")
REGION = os.environ.get("AWS_REGION", "us-east-1")
_TABLE = "OEM_AccountPlans"
DECISIONS = ("approve", "reject")


class NotFound(Exception):
    pass


class InvalidState(Exception):
    pass


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def plan_status(plan_id: str, env_prefix: str | None = None) -> dict[str, Any]:
    p = ddb.get_item(_TABLE, {"plan_id": plan_id}, env_prefix)
    if p is None:
        raise NotFound(f"account plan {plan_id} not found")
    return {"plan_id": plan_id, "status": p.get("status"),
            "approval_status": p.get("approval_status"),
            "decided_by": p.get("decided_by"), "decided_at": p.get("decided_at"),
            "version": p.get("version"), "publish_level": p.get("publish_level")}


def submit_plan(plan_id: str, actor: str, env_prefix: str | None = None) -> dict[str, Any]:
    """Submit a plan for approval: mark pending and start the SFN execution."""
    p = ddb.get_item(_TABLE, {"plan_id": plan_id}, env_prefix)
    if p is None:
        raise NotFound(f"account plan {plan_id} not found")
    if p.get("approval_status") == "pending":
        raise InvalidState(f"{plan_id} already pending approval")

    execution_arn = None
    if STATE_MACHINE_ARN:
        import boto3
        sfn = boto3.client("stepfunctions", region_name=REGION)
        resp = sfn.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            input=json.dumps({"plan_id": plan_id, "submitted_by": actor,
                              "district_id": p.get("district_id"),
                              "publish_level": p.get("publish_level")}),
        )
        execution_arn = resp["executionArn"]

    updated = dict(p)
    updated["approval_status"] = "pending"
    updated["status"] = "submitted"
    updated["submitted_by"] = actor
    updated["submitted_at"] = _now()
    updated["execution_arn"] = execution_arn
    updated["source_ref"] = p.get("source_ref")
    ddb.put_item(_TABLE, updated, env_prefix)
    _bus.emit("approval.requested", {"approval_type": "action_plan_publish", "plan_id": plan_id,
                                     "district_id": p.get("district_id"), "requested_by": actor},
              actor=actor, resource_url=f"/r/approval/{plan_id}", env_prefix=env_prefix)
    return {"plan_id": plan_id, "approval_status": "pending", "status": "submitted",
            "execution_arn": execution_arn, "submitted_by": actor}


def decide_plan(plan_id: str, decision: str, actor: str,
                task_token: str | None = None, env_prefix: str | None = None) -> dict[str, Any]:
    """Record a human decision and resume the paused execution.

    decision: 'approve' | 'reject'. On approve the plan publishes (status
    published), on reject it returns to draft with a rejection note. Version is
    bumped either way."""
    if decision not in DECISIONS:
        raise InvalidState(f"decision must be one of {DECISIONS}, got {decision!r}")
    p = ddb.get_item(_TABLE, {"plan_id": plan_id}, env_prefix)
    if p is None:
        raise NotFound(f"account plan {plan_id} not found")
    if p.get("approval_status") != "pending":
        raise InvalidState(f"{plan_id} is not pending approval (status={p.get('approval_status')})")

    # resume the Step Functions execution via the callback (if a token is held).
    token = task_token or p.get("task_token")
    if STATE_MACHINE_ARN and token:
        import boto3
        sfn = boto3.client("stepfunctions", region_name=REGION)
        if decision == "approve":
            sfn.send_task_success(taskToken=token, output=json.dumps({"decision": "approve", "by": actor}))
        else:
            sfn.send_task_failure(taskToken=token, error="Rejected", cause=f"rejected by {actor}")

    updated = dict(p)
    updated["approval_status"] = "approved" if decision == "approve" else "rejected"
    updated["status"] = "published" if decision == "approve" else "draft"
    updated["decided_by"] = actor
    updated["decided_at"] = _now()
    updated["version"] = (p.get("version") or 1) + 1
    updated["task_token"] = None
    ddb.put_item(_TABLE, updated, env_prefix)
    _bus.emit("approval.decided", {"approval_type": "action_plan_publish", "plan_id": plan_id,
                                   "decision": decision, "decided_by": actor,
                                   "district_id": p.get("district_id")},
              actor=actor, resource_url=f"/r/approval/{plan_id}", env_prefix=env_prefix)
    if decision == "approve":
        _bus.emit("plan.published", {"plan_id": plan_id, "district_id": p.get("district_id"),
                                     "account_id": p.get("account_id")},
                  actor=actor, env_prefix=env_prefix)
    return {"plan_id": plan_id, "decision": decision,
            "approval_status": updated["approval_status"], "status": updated["status"],
            "decided_by": actor, "version": updated["version"]}


def record_task_token(plan_id: str, task_token: str, env_prefix: str | None = None) -> None:
    """Store the SFN task token on the plan (called by the state machine's submit
    Lambda so a later human decision can resume the exact execution)."""
    p = ddb.get_item(_TABLE, {"plan_id": plan_id}, env_prefix)
    if p is None:
        return
    p["task_token"] = task_token
    ddb.put_item(_TABLE, p, env_prefix)
