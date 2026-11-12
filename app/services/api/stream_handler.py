"""Streaming floor path — Lambda Function URL handler (spec R2, SSE transport).

API Gateway REST cannot stream, so POST /journeys/{id}/utterance gets a second,
streaming transport: a small dedicated Lambda (`retail-ops-dev-floor-stream`,
app/infra/deploy_streaming.py) behind a Function URL that answers in
Server-Sent-Events form — one `event: token` per generation delta, then one
`event: result` carrying the full classify payload (the same shape the REST
route returns).

Auth: the URL is AuthType=AWS_IAM and is reached SAME-ORIGIN through the console
CloudFront distribution with Origin Access Control (never public — a public
Function URL is auto-mitigated by the account's
`lambda_function_policy_block_public_access` control). CloudFront signs the hop;
the app token rides in `X-Retail-Ops-Token` because OAC overwrites `Authorization`.
The SAME token verification runs here in-process (`authorizer.resolve_principal`) and the
journey scope check mirrors the REST route — a bad token is a 401 and an
out-of-scope journey a 403, exactly like the gateway path.

PLATFORM LIMITATION (ADR 0008): the managed Python runtime
has no response-streaming API — only Node.js (`awslambda.streamifyResponse`) or
a custom runtime/Lambda Web Adapter can flush chunks progressively — so this
handler BUFFERS the SSE document and sends it in one flush (the Function URL is
provisioned BUFFERED). The SSE wire format is kept so the console client and
any future streaming runtime are drop-in; the token deltas come from
`platform/streaming`, which releases them once the guardrail has passed the answer.

CORS: these responses carry no CORS headers. The Function URL's own CORS
configuration (deploy_streaming) sets them, and Lambda would send both sets on a
POST, duplicating Access-Control-Allow-Origin (app/services/api/cors.py).
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..agents import objection
from ..common import authz, ddb
from . import authorizer

_UTTERANCE_RE = re.compile(r"^/journeys/([^/]+)/utterance$")

_SSE_HEADERS = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
}


def _sse(event_name: str, payload: Any) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload, default=str)}\n\n"


def _response(status: int, body: Any, headers: dict[str, str] | None = None) -> dict[str, Any]:
    return {"statusCode": status,
            "headers": headers or {"Content-Type": "application/json"},
            "body": body if isinstance(body, str) else json.dumps(body, default=str)}


def _request(event: dict[str, Any]) -> tuple[str, str, dict[str, str], str]:
    """(method, path, headers-lowercased, body) from a Function URL (payload
    v2.0) or REST-proxy-shaped event, so local tests reuse the same shape."""
    http = ((event.get("requestContext") or {}).get("http") or {})
    method = http.get("method") or event.get("httpMethod") or ""
    path = http.get("path") or event.get("path") or ""
    headers = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        import base64
        body = base64.b64decode(body).decode("utf-8", "replace")
    return method.upper(), path, headers, body


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    method, path, headers, body = _request(event)
    m = _UTTERANCE_RE.match(path)
    if not (m and method == "POST"):
        return _response(404, {"error": f"no streaming route for {method} {path}"})
    journey_id = m.group(1)

    # Function URLs bypass the API Gateway authorizer: verify the token here,
    # through the SAME resolution path, and reject with 401 the same way.
    # CloudFront OAC replaces `Authorization` with its own SigV4 signature, so the
    # app token arrives in `X-Retail-Ops-Token`; `Authorization` still works for a direct
    # IAM-signed call (and for local dev against the module).
    token = headers.get("x-retail-ops-token") or headers.get("authorization")
    try:
        claims = authorizer.resolve_principal(token)
    except authz.Unauthenticated as e:
        return _response(401, {"error": f"unauthenticated: {e}"})
    except Exception:
        return _response(401, {"error": "unauthenticated"})
    scope = {**claims, "dealer_ids": set(filter(None, (claims.get("dealer_ids") or "").split(","))),
             "is_oem": str(claims.get("is_oem")).lower() == "true"}

    journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id})
    if journey is None:
        return _response(404, {"error": f"journey {journey_id} not found"})
    try:
        authz.check_journey_access(scope, journey)
    except authz.Forbidden as e:
        return _response(403, {"error": str(e)})

    try:
        payload = json.loads(body or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": "invalid JSON body"})
    utterance = (payload or {}).get("utterance", "")
    if not utterance:
        return _response(400, {"error": "body must include 'utterance'"})

    chunks: list[str] = []
    try:
        result = objection.classify_objection_stream(journey_id, utterance,
                                                     lambda t: chunks.append(_sse("token", {"text": t})))
    except Exception as e:
        return _response(500, {"error": f"{type(e).__name__}: {e}"})
    chunks.append(_sse("result", result))
    return _response(200, "".join(chunks), headers=dict(_SSE_HEADERS))
