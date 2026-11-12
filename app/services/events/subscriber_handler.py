"""§8 — EventBridge subscriber Lambda entrypoint.

The deployed EventBridge rule (detail-type=vehicle.delivered) targets this
handler. EventBridge delivers the event as {detail-type, detail, ...}; we route
to the matching subscriber in bus.py — the same code the local dispatch uses, so
cloud behavior matches local.
"""
from __future__ import annotations

from typing import Any

from . import bus


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    detail_type = event.get("detail-type") or event.get("detailType") or ""
    detail = event.get("detail") or {}
    result = bus.dispatch_local(detail_type, detail)
    return {"ok": bool(result.get("handled")), "detail_type": detail_type, "result": result}
