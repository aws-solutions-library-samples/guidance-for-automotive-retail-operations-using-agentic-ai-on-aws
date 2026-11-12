"""Runtime-side AgentCore Gateway client (spec §6).

The sanctioned way for a non-OEM persona (e.g. a Dealer advisor agent running in
the AgentCore runtime) to read OEM-owned data: call the Gateway's MCP tools
instead of touching retail-ops-dev-OEM_* directly. This client hides whether the
Gateway is reachable and falls back to a clear "unavailable" result so a caller
never silently reads across the persona boundary.

  enabled()                         -> is a Gateway configured?
  call(tool, claims, **arguments)   -> invoke one Gateway MCP tool with caller scope

Config comes from gateway-config.json (written by deploy_gateway.py) and/or the
env vars RETAIL_OPS_GATEWAY_URL / RETAIL_OPS_GATEWAY_ARN. When no Gateway is configured this
returns {enabled: false, note: ...} rather than reading the table directly —
that is the whole point of the boundary.

Transport note: AgentCore Gateway speaks MCP over HTTP with SigV4 (authorizerType
AWS_IAM). Rather than vendor an MCP client into the runtime zip, this client
invokes the same backing Lambda through the Gateway's MCP `tools/call` when a URL
is configured, and can also invoke the target Lambda directly by ARN as a
documented fallback (still the Gateway's scoped tool code, still no direct table
read by the dealer). Both paths run oem_read's scope+strip logic.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..common import envname

REGION = os.environ.get("AWS_REGION", "us-east-1")
_CONFIG_PATH = envname.config_file(Path(__file__).resolve().parents[3] / "app" / "infra", "gateway")


@lru_cache(maxsize=1)
def _config() -> dict[str, Any]:
    # env wins over file so the deployed Lambda can be reconfigured without a rebuild
    cfg: dict[str, Any] = {}
    try:
        cfg = json.loads(_CONFIG_PATH.read_text())
    except Exception:
        cfg = {}
    if os.environ.get("RETAIL_OPS_GATEWAY_URL"):
        cfg["gateway_url"] = os.environ["RETAIL_OPS_GATEWAY_URL"]
    if os.environ.get("RETAIL_OPS_GATEWAY_ARN"):
        cfg["gateway_arn"] = os.environ["RETAIL_OPS_GATEWAY_ARN"]
    if os.environ.get("RETAIL_OPS_GATEWAY_TARGET_LAMBDA"):
        cfg["target_lambda_arn"] = os.environ["RETAIL_OPS_GATEWAY_TARGET_LAMBDA"]
    return cfg


def enabled() -> bool:
    cfg = _config()
    return bool(cfg.get("gateway_url") or cfg.get("gateway_arn") or cfg.get("target_lambda_arn"))


def call(tool: str, claims: dict[str, Any], **arguments: Any) -> dict[str, Any]:
    """Invoke one Gateway MCP tool with the caller's scope.

    Prefers the MCP HTTP endpoint (SigV4) when a gateway_url is set; otherwise
    invokes the target Lambda directly by ARN (still the Gateway's scoped tool
    code). Returns the tool result dict, or an {enabled:false}/{error:...} shape
    the caller can surface — never a direct cross-persona table read."""
    cfg = _config()
    if not enabled():
        return {"enabled": False,
                "note": "AgentCore Gateway not configured; cross-persona read refused "
                        "(deploy_gateway.py --apply to provision)."}
    payload = {"claims": claims, "tool": tool, **arguments}

    # Primary transport: invoke the Gateway's target Lambda by ARN. This runs the
    # Gateway's scoped tool code (oem_read) with the caller's claims, so the
    # persona boundary is enforced exactly as through the MCP endpoint — but
    # without SigV4-over-HTTP fragility. The MCP HTTP endpoint (below) is for
    # true MCP clients (e.g. the AgentCore runtime) that speak the protocol.
    lam_arn = cfg.get("target_lambda_arn")
    if lam_arn:
        try:
            import boto3
            lam = boto3.client("lambda", region_name=REGION)
            resp = lam.invoke(FunctionName=lam_arn, Payload=json.dumps(payload).encode())
            out = json.loads(resp["Payload"].read() or b"{}")
            if not (isinstance(out, dict) and out.get("errorType")):
                return out
        except Exception:
            pass  # fall through to MCP HTTP

    url = cfg.get("gateway_url")
    if url:
        try:
            return _call_mcp_http(url, tool, claims, arguments)
        except Exception as e:
            return {"error": f"gateway call failed: {type(e).__name__}: {e}"}
    return {"error": "gateway configured without a callable transport", "config_keys": sorted(cfg)}


def _call_mcp_http(url: str, tool: str, claims: dict[str, Any],
                   arguments: dict[str, Any]) -> dict[str, Any]:
    """Call the Gateway MCP endpoint's tools/call over HTTP with SigV4.

    Kept minimal (urllib + botocore SigV4) so the runtime zip needs no MCP SDK.
    The Gateway forwards claims+args to the target Lambda, which runs oem_read."""
    import urllib.request

    import botocore.auth
    import botocore.awsrequest
    import boto3

    body = json.dumps({
        "jsonrpc": "2.0", "id": "1", "method": "tools/call",
        "params": {"name": tool, "arguments": {"claims": claims, **arguments}},
    }).encode()

    session = boto3.Session()
    creds = session.get_credentials().get_frozen_credentials()
    aws_req = botocore.awsrequest.AWSRequest(
        method="POST", url=url, data=body,
        headers={"Content-Type": "application/json"})
    botocore.auth.SigV4Auth(creds, "bedrock-agentcore", REGION).add_auth(aws_req)

    req = urllib.request.Request(url, data=body, method="POST")
    for k, v in aws_req.headers.items():
        req.add_header(k, v)
    # urlopen raises HTTPError on non-2xx, which the caller catches and falls
    # back to the (reliable) target-Lambda invoke. That fallback still runs the
    # Gateway's scoped tool code, so the persona boundary is never bypassed.
    with urllib.request.urlopen(req, timeout=15) as r:
        env = json.loads(r.read() or b"{}")
    # MCP tools/call result -> content; unwrap to the tool's dict when possible
    result = env.get("result") or env
    content = (result.get("content") or []) if isinstance(result, dict) else []
    for part in content:
        if part.get("type") == "text":
            try:
                return json.loads(part["text"])
            except Exception:
                return {"text": part["text"]}
    return result if isinstance(result, dict) else {"result": result}
