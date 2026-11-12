"""CORS headers for every API response: the one helper.

The console calls the API cross-origin (NEXT_PUBLIC_RETAIL_OPS_API is the
execute-api URL) with a bearer token, so the allowed origin is the console's own,
never `*`. RETAIL_OPS_CONSOLE_ORIGIN (comma-separated; deploy_l4 sets it from
console-config.json) names the deployed console; the local Next.js console
(`npm run dev`, port 3100) is always allowed.

`headers()` is what every response the service Lambda returns carries: the JSON
replies (handler._response), the SSE floor route (handler._sse_response) and the
local dev server, which forwards the handler's headers. A response that builds
its own header dict skips this, and the browser then blocks it even though the
preflight passed (the objection check's "Failed to fetch", 2026-10-29).

The floor-stream Function URL (stream_handler.py) is the exception: its CORS is
the Function URL's own configuration (deploy_streaming), and Lambda would send
both sets on a POST, duplicating Access-Control-Allow-Origin.
"""
from __future__ import annotations

import os

CONSOLE_ORIGIN_ENV = "RETAIL_OPS_CONSOLE_ORIGIN"
LOCAL_ORIGINS = ("http://localhost:3100", "http://127.0.0.1:3100")
ALLOW_HEADERS = "Authorization,Content-Type,X-Correlation-Id,X-Act-As"
ALLOW_METHODS = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
EXPOSE_HEADERS = "X-Correlation-Id"


def allowed_origins() -> list[str]:
    """The origins this API answers CORS for: the configured console origin(s)
    first, then the local dev console."""
    raw = os.environ.get(CONSOLE_ORIGIN_ENV, "")
    configured = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]
    return configured + [o for o in LOCAL_ORIGINS if o not in configured]


def cors_origin(request_origin: str | None) -> str:
    """The Access-Control-Allow-Origin value: the request's Origin when it is
    allowed, else the first allowed origin (so the browser refuses the response
    for any other origin, and a non-browser client sees the intended origin)."""
    allowed = allowed_origins()
    if request_origin:
        origin = request_origin.strip().rstrip("/")
        if origin in allowed:
            return origin
    return allowed[0]


def headers(request_origin: str | None, correlation_id: str | None = None,
            content_type: str = "application/json") -> dict[str, str]:
    """Content-Type plus the CORS headers for a response to `request_origin`, and
    X-Correlation-Id when the request has one."""
    out = {
        "Content-Type": content_type,
        "Access-Control-Allow-Origin": cors_origin(request_origin),
        "Vary": "Origin",
        "Access-Control-Allow-Headers": ALLOW_HEADERS,
        "Access-Control-Allow-Methods": ALLOW_METHODS,
        "Access-Control-Expose-Headers": EXPOSE_HEADERS,
    }
    if correlation_id:
        out["X-Correlation-Id"] = correlation_id
    return out
