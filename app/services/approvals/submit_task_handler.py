"""§8 R2 — Step Functions "submit" task Lambda (waitForTaskToken).

The approval state machine's first task invokes THIS handler with the task token
injected. Its only job is durable: record the token on the plan so a later human
decision (POST /approvals/plan/{id}/decide) can resume the exact paused
execution via SendTaskSuccess/Failure. It does NOT reply to Step Functions — the
execution stays paused until the human callback arrives.

State Machine passes: {"plan_id": ..., "taskToken": "<token>", ...}. Wiring the
token in the state's Parameters ("taskToken.$": "$$.Task.Token") is what makes
this the callback pattern.
"""
from __future__ import annotations

from typing import Any

from . import plan_approval


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    plan_id = event.get("plan_id")
    token = event.get("taskToken") or event.get("task_token")
    if not plan_id or not token:
        # No token means SFN wasn't wired for callback; nothing to persist.
        return {"ok": False, "reason": "missing plan_id or taskToken"}
    plan_approval.record_task_token(plan_id, token)
    # Deliberately return nothing to SFN's waitForTaskToken: the execution
    # remains paused until decide_plan sends the success/failure callback.
    return {"ok": True, "plan_id": plan_id, "recorded_token": True}
