"""Service-side client for the per-agent AgentCore runtimes (ADR 0009; spec §3
"Lambda -> InvokeAgentRuntime").

Each custom agent has its own runtime (`app/agentcore/registry.py`). The service
Lambda, the job Lambdas and the Journey Agent's runtime call them through this
module, and run the same function in-process when no runtime is configured.
The client never raises into the request path: every outcome is a
`RuntimeResult` status the caller maps (design §6.4).

Resolution (environment only, so a local checkout never routes to AWS):
  1. RETAIL_OPS_AGENT_RUNTIMES   JSON map {"A1": "arn…", …} (written by deploy_l4 / deploy_l6)
  2. RETAIL_OPS_AGENT_RUNTIME_ARN  the older single-runtime variable, read as A3's ARN
  3. app/infra/agentcore-config.json, only with RETAIL_OPS_AGENT_RUNTIME_USE_CONFIG=1
None when RETAIL_OPS_AGENT_RUNTIME_DISABLE is "1" or a CSV naming the agent, or
when this process is that agent's own runtime (a runtime never calls itself).

  runtime_arn(agent) / enabled(agent)              -> is the agent's runtime configured?
  invoke_agent(agent, prompt, ...) -> dict | None  reasoning turn (unchanged contract)
  invoke_tool(agent, tool, args, ...) -> RuntimeResult
  invoke_job(agent, job, payload, ...) -> RuntimeResult
  call_or_local(agent, tool, args, local=...) -> (code, body)   idempotent tools only

Statuses: not_configured | ok | caller_error | failed | unavailable | lost.
`unavailable` means nothing ran (safe to run locally); `lost` means the request
was sent and no usable answer came back, so the work may still be running.
"""
from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.common import envname

from . import registry

_log = logging.getLogger(__name__)

_CFG = envname.config_file(Path(__file__).resolve().parents[1] / "infra", "agentcore")
_TRANSPORT = "agentcore-runtime"
_SESSION_BUCKET_S = 600           # a user's calls to one agent within 10 minutes share a warm session
_CONNECT_TIMEOUT_S = 3

# ClientError codes that mean the request was rejected before the runtime ran it.
_NOT_EXECUTED = frozenset({"AccessDeniedException", "ResourceNotFoundException", "ValidationException",
                           "ThrottlingException", "ServiceQuotaExceededException", "UnauthorizedException"})


@dataclass
class RuntimeResult:
    """One runtime call's outcome. `body` is the runtime's `result` object for
    answered calls; `error` is a class name and message, never payload values."""
    status: str                   # not_configured | ok | caller_error | failed | unavailable | lost
    code: int = 0                 # the runtime's statusCode when it answered, else 0
    body: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class _Refused(Exception):
    """Prepare-phase refusal (e.g. payload over the body limit)."""


# --- resolution ----------------------------------------------------------------

@functools.lru_cache(maxsize=8)
def _parse_map(raw: str) -> tuple[tuple[str, str], ...]:
    """The RETAIL_OPS_AGENT_RUNTIMES map as items; malformed -> empty, logged once per value."""
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        _log.error("RETAIL_OPS_AGENT_RUNTIMES is not a JSON object of agent code -> runtime ARN; ignoring it")
        return ()
    return tuple((k.upper(), v) for k, v in data.items() if v)


def _disabled(code: str) -> bool:
    raw = os.environ.get("RETAIL_OPS_AGENT_RUNTIME_DISABLE", "").strip()
    if not raw:
        return False
    if raw == "1":
        return True
    return code in {c.strip().upper() for c in raw.split(",") if c.strip()}


def _config_arn(code: str) -> str | None:
    """The ARN from agentcore-config.json (v2 `agents`, or v1 `agent_runtime_arn` for A3)."""
    try:
        cfg = json.loads(_CFG.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(cfg, dict):
        return None
    agents = cfg.get("agents")
    if isinstance(agents, dict):
        entry = agents.get(code)
        arn = entry.get("runtime_arn") if isinstance(entry, dict) else None
        return arn if isinstance(arn, str) and arn else None
    arn = cfg.get("agent_runtime_arn") if code == "A3" else None
    return arn if isinstance(arn, str) and arn else None


def runtime_arn(agent: str = "A3") -> str | None:
    """The agent's runtime ARN, or None (not configured, disabled, or self)."""
    code = (agent or "").upper()
    if _disabled(code) or registry.current_agent_id() == code:
        return None
    arn = dict(_parse_map(os.environ.get("RETAIL_OPS_AGENT_RUNTIMES", "") or "{}")).get(code)
    if not arn and code == "A3":
        arn = os.environ.get("RETAIL_OPS_AGENT_RUNTIME_ARN") or None
    if not arn and os.environ.get("RETAIL_OPS_AGENT_RUNTIME_USE_CONFIG") == "1":
        arn = _config_arn(code)
    return arn or None


def enabled(agent: str = "A3") -> bool:
    return bool(runtime_arn(agent))


# --- transport -------------------------------------------------------------------

def _data_client(timeout_s: int) -> Any:
    """The bedrock-agentcore data-plane client (the test seam, planner decision P6)."""
    import boto3
    from botocore.config import Config
    return boto3.client("bedrock-agentcore", region_name=os.environ.get("AWS_REGION", "us-east-1"),
                        config=Config(connect_timeout=_CONNECT_TIMEOUT_S, read_timeout=timeout_s,
                                      retries={"max_attempts": 1}))


def _session_id(code: str, user_id: str | None, journey_id: str | None, now: float | None = None) -> str:
    """One warm session per (agent, user, journey) and 10-minute bucket; >= 33 chars."""
    bucket = int((time.time() if now is None else now) // _SESSION_BUCKET_S)
    digest = hashlib.sha256(f"{user_id}|{journey_id}|{bucket}".encode()).hexdigest()[:40]
    return f"{code}-{digest}"


def _job_session_id(code: str, job: str) -> str:
    return f"{code}-job-{job}-{uuid.uuid4().hex}"


def _classify_send(exc: BaseException) -> str:
    """Send-phase exception -> unavailable (not executed) or lost (may have run)."""
    try:
        from botocore import exceptions as bx
    except ImportError:
        return "lost"
    if isinstance(exc, bx.ReadTimeoutError):
        return "lost"                     # sent; the runtime may still be working
    if isinstance(exc, (bx.NoCredentialsError, bx.ParamValidationError, bx.EndpointConnectionError,
                        bx.ConnectTimeoutError)):
        return "unavailable"
    if isinstance(exc, bx.ClientError):
        code = (exc.response or {}).get("Error", {}).get("Code")
        return "unavailable" if code in _NOT_EXECUTED else "lost"
    return "lost"


def _classify_answer(data: Any) -> RuntimeResult:
    """A parsed runtime envelope -> ok / caller_error / failed; no statusCode -> lost."""
    code = data.get("statusCode") if isinstance(data, dict) else None
    if type(code) is not int:
        return RuntimeResult("lost", 0, {}, "response has no statusCode")
    result = data.get("result")
    body = result if isinstance(result, dict) else {"result": result}
    body.setdefault("transport", _TRANSPORT)
    error = body.get("error") if isinstance(body.get("error"), str) else None
    if 200 <= code <= 299:
        return RuntimeResult("ok", code, body, None)
    if 400 <= code <= 499:
        return RuntimeResult("caller_error", code, body, error)
    return RuntimeResult("failed", code, body, error or f"runtime answered {code}")


def _send(code: str, arn: str, build_payload: Callable[[], dict[str, Any]], *,
          session_id: str, timeout_s: int) -> RuntimeResult:
    """One InvokeAgentRuntime call, classified by phase (prepare / send / read)."""
    phase = "prepare"
    try:
        data = json.dumps(build_payload(), default=str).encode()
        if len(data) > registry.MAX_BODY_BYTES:
            raise _Refused(f"payload is {len(data)} bytes, over the {registry.MAX_BODY_BYTES}-byte limit")
        dp = _data_client(timeout_s)
        phase = "send"
        resp = dp.invoke_agent_runtime(agentRuntimeArn=arn, runtimeSessionId=session_id,
                                       contentType="application/json", accept="application/json",
                                       payload=data)
        phase = "read"
        stream = resp.get("response")
        raw = stream.read() if hasattr(stream, "read") else stream
        answer = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
    except Exception as e:  # noqa: BLE001 - the client never raises into the request path; the phase decides the status
        status = "unavailable" if phase == "prepare" else _classify_send(e) if phase == "send" else "lost"
        _log.warning("agent runtime %s call %s at %s: %s", code, status, phase, type(e).__name__)
        return RuntimeResult(status, 0, {}, f"{type(e).__name__}: {str(e)[:200]}")
    return _classify_answer(answer)


def _fallback_span(code: str, result: RuntimeResult, call: str, correlation_id: str | None,
                   market: str | None) -> None:
    """One trace row for a configured call that ended unavailable or lost (P3)."""
    if result.status not in ("unavailable", "lost"):
        return
    from app.services.common import trace
    trace.write_trace("runtime.fallback", agent_id=code, correlation_id=correlation_id, outcome=result.status,
                      market=market or None,
                      extra={"transport": "in-process-fallback", "call": call})


def _wire_claims(claims: dict[str, Any]) -> dict[str, Any]:
    return registry.serialize_claims(registry.normalize_claims(claims))


def _call(code: str, build_payload: Callable[[], dict[str, Any]], *, call: str, session_id: str,
          timeout_s: int, correlation_id: str | None, market: str | None) -> RuntimeResult:
    arn = runtime_arn(code)
    if not arn:
        return RuntimeResult("not_configured")
    result = _send(code, arn, build_payload, session_id=session_id, timeout_s=timeout_s)
    _fallback_span(code, result, call, correlation_id, market)
    return result


# --- public API ---------------------------------------------------------------------

def invoke_agent(agent: str, prompt: str, *, journey_id: str | None, claims: dict[str, Any],
                 correlation_id: str | None = None, timeout_s: int = 26) -> dict[str, Any] | None:
    """A reasoning turn on the agent's runtime. Returns the runtime's result (with
    `transport`) when it answered, including an error object for `failed` and
    `caller_error`, so the caller does not run the turn again. Returns None when
    the runtime is not configured, unavailable or lost (the caller runs in-process);
    with RETAIL_OPS_AGENT_RUNTIME_STRICT=1 the last two return {"__error__": ...}."""
    code = (agent or "").upper()

    def payload() -> dict[str, Any]:
        return {"kind": "agent", "agent": code, "prompt": prompt, "journey_id": journey_id,
                "claims": _wire_claims(claims), "correlation_id": correlation_id}

    res = _call(code, payload, call="agent", session_id=_session_id(code, claims.get("user_id"), journey_id),
                timeout_s=timeout_s, correlation_id=correlation_id, market=claims.get("market_id"))
    if res.status in ("ok", "failed", "caller_error"):
        return res.body
    if res.status in ("unavailable", "lost") and os.environ.get("RETAIL_OPS_AGENT_RUNTIME_STRICT") == "1":
        return {"__error__": res.error or res.status}
    return None


def invoke_tool(agent: str, tool: str, args: dict[str, Any], *, claims: dict[str, Any],
                correlation_id: str | None = None, caller: str | None = None,
                timeout_s: int | None = None) -> RuntimeResult:
    """One registry tool on the agent's runtime (`kind: tool`)."""
    code = (agent or "").upper()
    spec = registry.tool(code, tool)
    if spec is None:
        raise ValueError(f"{code} has no tool {tool!r}")

    def payload() -> dict[str, Any]:
        return {"kind": "tool", "agent": code, "tool": tool, "args": args, "claims": _wire_claims(claims),
                "correlation_id": correlation_id, "caller": caller}

    return _call(code, payload, call=tool,
                 session_id=_session_id(code, claims.get("user_id"), (args or {}).get("journey_id")),
                 timeout_s=timeout_s or spec.timeout_s, correlation_id=correlation_id,
                 market=claims.get("market_id"))


def invoke_job(agent: str, job: str, payload: dict[str, Any], *, apply: bool,
               correlation_id: str | None = None, timeout_s: int = 280) -> RuntimeResult:
    """One agent-owned job on the agent's runtime (`kind: job`), in a fresh session."""
    code = (agent or "").upper()

    def body() -> dict[str, Any]:
        return {"kind": "job", "agent": code, "job": job, "payload": payload, "apply": apply,
                "correlation_id": correlation_id}

    return _call(code, body, call=job, session_id=_job_session_id(code, job), timeout_s=timeout_s,
                 correlation_id=correlation_id, market=(payload or {}).get("market"))


def call_or_local(agent: str, tool: str, args: dict[str, Any], *, claims: dict[str, Any],
                  local: Callable[[], tuple[int, dict[str, Any]]],
                  correlation_id: str | None = None) -> tuple[int, dict[str, Any]]:
    """Run a read-only or idempotent tool on its runtime, or `local()` when the
    runtime is not configured, unavailable or lost (a re-run is safe for these
    tools). `failed` -> (500, the runtime's error); `caller_error` passes through."""
    code = (agent or "").upper()
    spec = registry.tool(code, tool)
    if spec is None or not (spec.read_only or spec.idempotent):
        raise ValueError(f"call_or_local needs a read-only or idempotent tool, not {code}.{tool}")
    res = invoke_tool(code, tool, args, claims=claims, correlation_id=correlation_id)
    if res.status == "ok":
        return 200, res.body
    if res.status == "failed":
        return 500, res.body
    if res.status == "caller_error":
        return res.code, res.body
    return local()
