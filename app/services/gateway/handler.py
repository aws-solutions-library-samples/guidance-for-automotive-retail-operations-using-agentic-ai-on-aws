"""AgentCore Gateway Lambda target — cross-persona read tools (spec §6).

The AgentCore Gateway (protocolType=MCP) exposes the tools declared in
oem_read.TOOLS. When a tool is invoked, the Gateway calls THIS Lambda with:

  * the tool name in the client context / event context
    (`context.client_context.custom["bedrockAgentCoreToolName"]`), which the
    Gateway prefixes with the target name (`<target>___<tool>`); we take the
    part after the last `___`.
  * the tool arguments as the event body (a plain dict of the declared args).
  * the caller identity in the event under `claims` (the runtime/bridge forwards
    the caller scope; the Gateway authorizer (AWS_IAM) authenticates the runtime).

We re-derive the caller scope from `claims`, dispatch to oem_read.call_tool, and
return the tool result. This is the ONLY sanctioned path for a non-OEM persona
to read OEM-owned data; the reads inside oem_read scope + strip before returning.

Local invocation (tests, the eval harness) can call `dispatch(name, claims, args)`
directly without a Lambda context.
"""
from __future__ import annotations

import json
from typing import Any

from ..common import envname
from . import oem_read

ENV_PREFIX = envname.table_prefix()

# tool-name separator the Gateway uses between target name and tool name
_SEP = "___"


def _tool_name_from_context(event: dict[str, Any], context: Any) -> str | None:
    """The Gateway puts the invoked tool name in the Lambda client context.
    Fall back to an explicit event field for local/direct invocation."""
    try:
        custom = (context.client_context.custom or {}) if context and context.client_context else {}
        name = custom.get("bedrockAgentCoreToolName")
        if name:
            return name.split(_SEP)[-1]
    except Exception:
        pass
    return event.get("tool") or event.get("name")


# An OEM-authorized service principal (a client_credentials token proving the
# retail-ops-oem/read scope) reads OEM data with OEM read-across — it is NOT scoped to
# a single dealer and carries no end user. This is the identity the CUSTOM_JWT
# gateway path resolves to; dealer scoping for that path is the Quick agent's
# account filter, not here (Quick content itself is not row-scoped).
_SERVICE_OEM_SCOPE = {
    "user_id": "quick-service", "role": "oem_service", "scope_node_id": "OEM",
    "market_id": None, "dealer_ids": None, "is_oem": True, "customer_id": "",
}


def _jwt_context(context: Any) -> dict[str, Any] | None:
    """Identity the CUSTOM_JWT gateway attaches to the Lambda client context.

    AgentCore forwards the validated token's claims under client_context.custom
    (keys vary: 'bedrockagentcoreScope'/'scope', 'client_id', 'sub'). We only
    need to know a VALIDATED token is present — the gateway has already verified
    its signature/issuer/audience/scope, so we trust the authorizer, not the
    model's arguments. Returns the authenticated scope, or None when there is no
    JWT context (AWS_IAM path)."""
    try:
        custom = (context.client_context.custom or {}) if context and context.client_context else {}
    except Exception:
        return None
    # Any scope/client/sub claim present means the request arrived through the
    # JWT authorizer (not the IAM/direct path).
    if any(k in custom for k in ("scope", "bedrockagentcoreScope", "client_id", "clientId", "sub")):
        return dict(_SERVICE_OEM_SCOPE)
    return None


def _claims(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Resolve the caller scope.

    SECURITY: when the request carries a validated JWT (CUSTOM_JWT gateway), the
    identity comes from the TOKEN (via the gateway), and any `claims` the model
    put in the arguments is IGNORED — a model must not be able to widen its own
    scope. The AWS_IAM path (runtime/service bridge) still forwards `claims` in
    the event, which is trusted because that caller is itself IAM-authenticated."""
    jwt_scope = _jwt_context(context)
    if jwt_scope is not None:
        return jwt_scope
    c = event.get("claims") or {}
    return {
        "user_id": c.get("user_id"),
        "role": c.get("role"),
        "scope_node_id": c.get("scope_node_id"),
        "market_id": c.get("market_id"),
        "dealer_ids": c.get("dealer_ids"),
        "is_oem": str(c.get("is_oem")).lower() == "true" if not isinstance(c.get("is_oem"), bool) else c.get("is_oem"),
        "customer_id": c.get("customer_id") or "",
    }


def dispatch(name: str, claims: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    """Direct dispatch (local/tests). Returns the tool result dict."""
    return oem_read.call_tool(name, claims, arguments, env_prefix=ENV_PREFIX)


def lambda_handler(event: dict[str, Any], context: Any = None) -> Any:
    """AgentCore Gateway Lambda target entry point.

    event carries the tool arguments (plus `claims`); the tool name arrives via
    the client context. Returns the tool result as a plain dict (the Gateway
    wraps it into the MCP tool response)."""
    if isinstance(event, str):
        try:
            event = json.loads(event)
        except Exception:
            event = {}
    name = _tool_name_from_context(event, context)
    if not name:
        return {"error": "no tool name in invocation", "available": sorted(oem_read.TOOLS)}
    claims = _claims(event, context)
    # tool arguments are the event minus the reserved keys
    args = {k: v for k, v in event.items() if k not in ("claims", "tool", "name")}
    return oem_read.call_tool(name, claims, args, env_prefix=ENV_PREFIX)
