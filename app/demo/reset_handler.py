"""The demo-reset Lambda (retail-ops-<env>-demo-reset) — runs app/demo/reset.run() off the API.

The Admin Console's "Demo data" card calls POST /admin/demo/reset, which writes the status row
as `running` and invokes this function asynchronously; a reset takes a few minutes, far past
API Gateway's 29 s. The seed (sample-data/synthetic/{us,de}) is bundled into this function's
zip by app/infra/deploy_demo_reset.py, so the reset needs no S3 copy of it.

Event: {"actor": "<who pressed the button>", "requested_at": "<iso>", "backup": true}
The status row (PLAT_AgentTraces / DEMO-RESET-STATUS) ends as `done` with the counts, or
`failed` with the error — the card polls it.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from app.demo import reset
from app.services.common import ddb

log = logging.getLogger()
log.setLevel(logging.INFO)


def _client():
    import boto3
    from botocore.config import Config
    # reset.plan/apply use 8 worker threads; adaptive retries ride out write throttling
    cfg = Config(max_pool_connections=16, retries={"mode": "adaptive", "max_attempts": 10})
    return boto3.client("dynamodb", region_name=os.environ.get("AWS_REGION", ddb.DEFAULT_REGION), config=cfg)


def lambda_handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    event = event or {}
    client = _client()
    prefix = ddb.DEFAULT_ENV_PREFIX
    base = {"actor": event.get("actor"), "requested_at": event.get("requested_at")}
    try:
        reset.check_prefix(prefix)
    except ValueError as e:              # not a demo stack: touch nothing, not even the status row
        log.error("%s", e)
        return {"ok": False, "error": str(e)}
    try:
        started = reset.write_status(client, prefix, "running", **base,
                                     started_at=reset._now_iso())["started_at"]
        summary = reset.run(client, prefix, backup=bool(event.get("backup", True)), log=log.info)
        reset.write_status(client, prefix, "done", **base, started_at=started,
                           finished_at=reset._now_iso(), **summary)
        log.info("demo reset done: %s", summary)
        return {"ok": True, **summary}
    except Exception as e:
        log.exception("demo reset failed")
        try:
            reset.write_status(client, prefix, "failed", **base, finished_at=reset._now_iso(),
                               error=f"{type(e).__name__}: {e}"[:500])
        except Exception:
            log.exception("could not record the failure on the status row")
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:500]}
