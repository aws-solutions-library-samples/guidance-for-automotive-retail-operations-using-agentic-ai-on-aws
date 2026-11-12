"""Admin Console "Demo data" card (GET /admin/demo, POST /admin/demo/reset).

Shows how many rehearsals each end-to-end scenario has left (app/demo/readiness.py) and the
last reset, and starts a reset in the demo-reset Lambda (app/demo/reset_handler.py), which
syncs every table back to the seed. The reset runs for minutes, so the API only starts it
(an asynchronous invoke) and the card polls the status row.

Only on the dev demo stack: anywhere else the card says so and the reset is refused.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any

from app.demo import reset

from ..common import ddb, envname

# A reset that has said `running` for longer than the Lambda's 15-minute limit died without
# recording it (timeout, out of memory): the card offers to run it again.
STALE_AFTER = _dt.timedelta(minutes=16)
CONFIRM_WORD = "reset"


class Refused(Exception):
    """The reset may not start (not the demo stack, already running, not confirmed)."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


def enabled() -> bool:
    return envname.env() == "dev" and envname.table_prefix().startswith(reset._ALLOWED_PREFIXES)


def function_name() -> str:
    return os.environ.get("RETAIL_OPS_DEMO_RESET_FN") or f"{envname.table_prefix()}demo-reset"


def _ddb_client():
    import boto3
    return boto3.client("dynamodb", region_name=ddb.DEFAULT_REGION)


def _lambda_client():
    import boto3
    return boto3.client("lambda", region_name=ddb.DEFAULT_REGION)


def _parse(ts: Any) -> _dt.datetime | None:
    try:
        return _dt.datetime.fromisoformat(str(ts))       # 3.11+ reads the trailing Z
    except ValueError:
        return None


def last_reset(client=None, now: _dt.datetime | None = None) -> dict[str, Any] | None:
    """The status row, with `running` turned into `stale` once it outlived the Lambda."""
    row = reset.read_status(client or _ddb_client(), envname.table_prefix())
    if not row:
        return None
    if row.get("actor"):
        from ..common import people
        row = {**row, "actor_name": people.directory().get(str(row["actor"]), str(row["actor"]))}
    if row.get("state") == "running":
        since = _parse(row.get("started_at") or row.get("updated_at"))
        if since and (now or _dt.datetime.now(_dt.UTC)) - since > STALE_AFTER:
            row = {**row, "state": "stale"}
    return row


def overview(client=None) -> dict[str, Any]:
    out: dict[str, Any] = {"enabled": enabled(), "env": envname.env(), "confirm_word": CONFIRM_WORD}
    if not out["enabled"]:
        out["reason"] = "Demo reset is only available on the dev demo stack."
        return out
    client = client or _ddb_client()
    out["last_reset"] = last_reset(client)
    try:
        from app.demo import readiness
        out["readiness"] = [s.__dict__ for s in readiness.report()]
    except Exception as e:  # noqa: BLE001 - the card still shows the reset when readiness fails
        out["readiness"] = None
        out["readiness_error"] = f"{type(e).__name__}: {e}"[:300]
    return out


def start(actor: str, confirm: str | None, client=None, lam=None) -> dict[str, Any]:
    """Record `running` and start the reset Lambda asynchronously."""
    if not enabled():
        raise Refused(403, "Demo reset is only available on the dev demo stack.")
    if (confirm or "").strip().lower() != CONFIRM_WORD:
        raise Refused(400, f"Type {CONFIRM_WORD!r} to confirm: this replaces every table's data with the seed.")
    client = client or _ddb_client()
    cur = last_reset(client)
    if cur and cur.get("state") == "running":
        raise Refused(409, f"A reset is already running (started {cur.get('started_at')} by {cur.get('actor')}).")
    now = reset._now_iso()
    prefix = envname.table_prefix()
    row = reset.write_status(client, prefix, "running", actor=actor, requested_at=now, started_at=now)
    lam = lam or _lambda_client()
    try:
        lam.invoke(FunctionName=function_name(), InvocationType="Event",
                   Payload=json.dumps({"actor": actor, "requested_at": now, "backup": True}).encode())
    except Exception as e:  # any start failure is recorded, then re-raised as a 503
        reset.write_status(client, prefix, "failed", actor=actor, requested_at=now, finished_at=reset._now_iso(),
                           error=f"could not start {function_name()}: {type(e).__name__}")
        raise Refused(503, f"Could not start the reset ({type(e).__name__}); is the demo-reset stage deployed?") from e
    return {"started": True, "status": row}
