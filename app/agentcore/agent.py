#!/usr/bin/env python3
"""Retail Ops agent runtime — Amazon Bedrock AgentCore Runtime entrypoint (HTTP protocol).

One runtime per custom agent (ADR 0009). Every runtime runs this file; the
environment variable RETAIL_OPS_AGENT_ID says which agent it serves, and the
agent registry (`app/agentcore/registry.py`) says what that agent may do.

Implements the AgentCore Runtime HTTP service contract directly (no SDK):
  * GET  /ping         -> {"status":"Healthy"}   (health check)
  * POST /invocations  -> runs one request for this agent and returns a JSON envelope

Served on port 8080 with the Python stdlib http.server, so a tools runtime needs
no dependency beyond boto3 (vendored in the bundle under deps/). Deployed via
CreateAgentRuntime's codeConfiguration (code in S3); no Docker, no ECR.

Payload kinds (design §6.2):
  {"kind": "agent", "agent": "A3", "prompt": "...", "journey_id"?, "claims": {...}, "correlation_id"?}
      reasoning turn -> the agent's reasoning entry (A3: agents.a3_agent.run, which keeps its
      own refusal classifier, scope check and single trace row)
  {"kind": "tool", "agent": "A1", "tool": "rank_vehicles", "args": {...}, "claims": {...},
   "correlation_id"?, "caller"?}
      one registry tool: role allowlist -> scope check -> adapter -> one runtime.<tool> span
  {"kind": "job", "agent": "A4", "job": "equity-monitor", "payload": {"market", "limit"}, "apply"?}
      one agent-owned job, under platform.mode.override("local") -> one runtime.job.<job> span
  {"kind": "ping"}
      identity check: {"agent", "runtime", "version"}; no data access

Response envelope (HTTP 200; the logical status is in the body):
  {"statusCode", "result", "agent", "runtime", "transport": "agentcore-runtime", "version"}

Claims are taken as sent (registry.normalize_claims) with no defaults. Every
service module is loaded with importlib from registry strings, so a bundle holds
only its own agent's code. The legacy router payload ({"method", "path"} with no
kind) runs the API handler only in local tests (no RETAIL_OPS_AGENT_ID); a
deployed runtime answers 400.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# The runtime unpacks the S3 code at the working dir with a bare interpreter
# (no boto3). Vendored dependencies ride in the bundle under deps/; put that AND
# the code root on sys.path before any app import.
_HERE = Path(__file__).resolve()
_roots = [_HERE.parents[2], _HERE.parents[1], Path.cwd()]
for cand in _roots:
    deps = cand / "deps"
    if deps.is_dir() and str(deps) not in sys.path:
        sys.path.insert(0, str(deps))
    if (cand / "app").is_dir() and str(cand) not in sys.path:
        sys.path.insert(0, str(cand))
# also the code dir itself (where requirements/deps may unpack)
for extra in (str(Path.cwd() / "deps"), str(_HERE.parent.parent.parent / "deps")):
    if Path(extra).is_dir() and extra not in sys.path:
        sys.path.insert(0, extra)

from app.agentcore import registry  # imported after the sys.path bootstrap above

PORT = int(os.environ.get("PORT", "8080"))
TRANSPORT = "agentcore-runtime"
KINDS = ("agent", "tool", "job", "ping")
_CORRELATION_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_LEGACY_ROUTER = "app.services.api.handler"     # local tests only; never in a bundle
_TRACE = "app.services.common.trace"
_log = logging.getLogger("retail_ops.agent_runtime")


def _build_version() -> str:
    """`code_sha256[:12]` from BUILD_INFO.json at the bundle root, or "local"."""
    try:
        info = json.loads((_HERE.parents[2] / "BUILD_INFO.json").read_text())
    except (OSError, ValueError):
        return "local"
    sha = info.get("code_sha256") if isinstance(info, dict) else None
    return sha[:12] if isinstance(sha, str) and sha else "local"


VERSION = _build_version()


def _svc(module: str) -> Any:
    """A service module, loaded at call time (planner decision P1)."""
    return importlib.import_module(module)


class _Reject(Exception):
    """A request the dispatcher refuses: (statusCode, result body)."""

    def __init__(self, code: int, body: dict[str, Any]) -> None:
        super().__init__(body.get("error"))
        self.code, self.body = code, body


def _err(code: int, message: str, **extra: Any) -> _Reject:
    return _Reject(code, {"error": message, **extra})


def _envelope(code: int, result: Any, agent: str | None) -> dict[str, Any]:
    spec = registry.agent(agent)
    return {"statusCode": code, "result": result, "agent": agent,
            "runtime": spec.runtime_name if spec else None, "transport": TRANSPORT, "version": VERSION}


def _short(e: BaseException) -> str:
    return f"{type(e).__name__}: {str(e)[:200]}"


def _is(e: BaseException, name: str) -> bool:
    """Match domain exceptions by class name (authz.Forbidden, the modules' NotFound)."""
    return any(c.__name__ == name for c in type(e).__mro__)


# --- legacy router payload (local tests only, planner decision P11) -----------------

def _proxy_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Turn a legacy router payload into an API Gateway proxy event the Retail
    Ops handler understands (today's defaults; reachable only without an agent id)."""
    claims = payload.get("claims") or {}
    # dealer_ids is a csv string in the real authorizer context
    dealer_ids = claims.get("dealer_ids")
    if isinstance(dealer_ids, (list, tuple, set)):
        dealer_ids = ",".join(str(d) for d in dealer_ids)
    authorizer = {
        "user_id": claims.get("user_id", "agentcore"),
        "role": claims.get("role", "oem_dm"),
        "scope_node_id": claims.get("scope_node_id", "OEM"),
        "market_id": claims.get("market_id", "us"),
        "dealer_ids": dealer_ids or "",
        "is_oem": str(claims.get("is_oem", True)).lower(),
        "customer_id": claims.get("customer_id", ""),
    }
    body = payload.get("body")
    return {
        "path": payload.get("path", "/health"),
        "httpMethod": payload.get("method", "GET"),
        "queryStringParameters": payload.get("query") or None,
        "headers": {},
        "body": json.dumps(body) if body is not None else None,
        "requestContext": {"authorizer": authorizer},
    }


def _legacy(payload: dict[str, Any]) -> tuple[int, Any]:
    if registry.current_agent_id() is not None:
        raise _err(400, "router payloads are retired (ADR 0009)")
    resp = _svc(_LEGACY_ROUTER)._route(_proxy_event(payload))
    raw = resp.get("body")
    try:
        result = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        result = raw
    return resp.get("statusCode", 200), result


# --- validation (design §6.2, in table order) ----------------------------------------

def _agent_spec(payload: dict[str, Any]) -> registry.AgentSpec:
    raw = payload.get("agent")
    spec = registry.agent(raw if isinstance(raw, str) else None)
    if spec is None:
        raise _err(400, f"unknown agent {str(raw)[:16]!r}", known=sorted(registry.AGENTS))
    served = registry.current_agent_id()
    if served is not None and served != spec.code:
        raise _err(400, f"wrong agent: this runtime serves {served}")
    return spec


def _claims(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return registry.normalize_claims(payload.get("claims"))
    except registry.ClaimsError as e:
        raise _err(400, str(e), field=f"claims.{e.field}") from None


def _args(spec: registry.ToolSpec, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return registry.validate_args(spec, payload.get("args"))
    except registry.ArgError as e:
        raise _err(400, str(e), field=e.field) from None


def _correlation_id(payload: dict[str, Any]) -> str:
    """The caller's correlation id when well-formed; otherwise a fresh one (a
    tracing aid, never a reason to reject)."""
    raw = payload.get("correlation_id")
    if isinstance(raw, str) and _CORRELATION_ID.fullmatch(raw):
        return raw
    corr = _svc(_TRACE).new_correlation_id()
    if raw is not None:
        _log.info("replaced a malformed correlation_id with %s", corr)
    return corr


def _env_prefix() -> str | None:
    return os.environ.get("RETAIL_OPS_ENV_PREFIX")


# --- kinds ---------------------------------------------------------------------------

def _run_agent(spec: registry.AgentSpec, payload: dict[str, Any], corr: str) -> Any:
    """A reasoning turn; the reasoning entry writes its own trace row (ADR 0002)."""
    if spec.reasoning is None:
        raise _err(400, f"the {spec.display} has no reasoning engine yet (engine: {spec.engine})")
    claims = _claims(payload)
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise _err(400, "prompt is required")
    journey_id = payload.get("journey_id")
    if journey_id is not None and not (isinstance(journey_id, str)
                                       and re.fullmatch(registry.ID_PATTERN, journey_id)):
        raise _err(400, "journey_id has an invalid format", field="journey_id")
    os.environ.setdefault("RETAIL_OPS_A3_AGENT_FORCE", "1")   # the runtime IS the agent tier
    run = registry.resolve(spec.reasoning)
    return run(prompt, journey_id, claims, env_prefix=_env_prefix(), correlation_id=corr)


def _roles_allowed(claims: dict[str, Any], roles: frozenset[str]) -> bool:
    """Any held role (groups or the primary role) in the set, as routing.roles does."""
    held = set(claims.get("groups") or []) | {claims.get("role")}
    return bool(held & roles)


def _scope(tool: registry.ToolSpec, claims: dict[str, Any], args: dict[str, Any]) -> dict[str, Any] | None:
    """Role allowlist, then the scope check; returns the journey for journey tools."""
    if tool.roles and not _roles_allowed(claims, registry.resolve_roles(tool.roles)):
        raise _err(403, f"role {claims.get('role')!r} may not call {tool.name}")
    if tool.scope == "journey":
        authz = _svc("app.services.common.authz")
        journey = _svc("app.services.common.ddb").get_item(
            "DEALER_Journeys", {"journey_id": args["journey_id"]}, _env_prefix())
        if journey is None:
            raise _err(404, f"journey {args['journey_id']} not found")
        try:
            authz.check_journey_access(claims, journey)
        except authz.Forbidden as e:
            raise _err(403, str(e)[:200]) from None
        return journey
    if tool.scope == "oem":
        if not (claims.get("is_oem") is True or str(claims.get("role") or "").startswith("oem_")):
            raise _err(403, f"role {claims.get('role')!r} may not call OEM tools")
        if not claims.get("scope_node_id"):
            raise _err(400, "claims.scope_node_id is required for OEM tools", field="claims.scope_node_id")
    return None


def _run_tool(spec: registry.AgentSpec, payload: dict[str, Any], corr: str) -> Any:
    name = payload.get("tool")
    tool = registry.tool(spec.code, name)
    if tool is None:
        raise _err(404, f"unknown tool {str(name)[:64]!r}", known=[t.name for t in spec.tools])
    claims = _claims(payload)
    args = _args(tool, payload)
    journey = _scope(tool, claims, args)
    adapter = registry.resolve(tool.target)
    extra = {"journey": journey} if journey is not None else {}
    start, outcome = time.monotonic(), "error"
    try:
        result = adapter(claims, _env_prefix(), **extra, **args)
        outcome = "ok"
        return result
    finally:
        _span(f"runtime.{tool.name}", spec.code, corr, outcome, start, journey_id=args.get("journey_id"),
              market=claims.get("market_id"), caller=payload.get("caller"))


def _run_job(spec: registry.AgentSpec, payload: dict[str, Any], corr: str) -> Any:
    name = payload.get("job")
    job = next((j for j in spec.jobs if j.name == name), None)
    if job is None:
        raise _err(404, f"unknown job {str(name)[:64]!r}", known=[j.name for j in spec.jobs])
    try:
        p = registry.validate_job_payload(payload.get("payload"), job.passthrough)
    except registry.ArgError as e:
        raise _err(400, str(e), field=e.field) from None
    apply = payload.get("apply", True)
    if type(apply) is not bool:
        raise _err(400, "apply must be a boolean", field="apply")
    run = registry.resolve(job.target)
    passthrough = {k: p[k] for k in job.passthrough}
    start, outcome = time.monotonic(), "error"
    try:
        with _svc("app.services.platform.mode").override("local"):   # batch jobs make no model call (D6d)
            result = run(market=p["market"], apply=apply, limit=p["limit"], **passthrough)
        outcome = "ok"
        return result
    finally:
        _span(f"runtime.job.{job.name}", spec.code, corr, outcome, start, market=p["market"],
              caller=payload.get("caller"))


def _span(tool: str, agent: str, corr: str, outcome: str, start: float, *, journey_id: str | None = None,
          market: str | None = None, caller: Any = None) -> None:
    """One PLAT_AgentTraces row for a tool or job call (tagged with the transport)."""
    _svc(_TRACE).write_trace(
        tool, agent_id=agent, correlation_id=corr, journey_id=journey_id, outcome=outcome,
        latency_ms=int((time.monotonic() - start) * 1000), market=market or None,
        extra={"caller": caller} if isinstance(caller, str) and caller else None, env_prefix=_env_prefix())


def _ping() -> dict[str, Any]:
    served = registry.current_agent_id()
    spec = registry.agent(served)
    return {"agent": served, "runtime": spec.runtime_name if spec else None, "version": VERSION}


def _dispatch(payload: Any, ctx: dict[str, Any], corr: str) -> tuple[int, Any]:
    """Validate in the order of design §6.2 and run the request. Fills `ctx`
    (kind, agent, name) for the log line as it learns them."""
    if not isinstance(payload, dict):
        raise _err(400, "bad request: the body must be a JSON object")
    raw_kind = payload.get("kind")
    kind = raw_kind.lower() if isinstance(raw_kind, str) else None
    if kind is None and ("method" in payload or "path" in payload):
        ctx.update(kind="router", agent=None)
        return _legacy(payload)
    if kind not in KINDS:
        raise _err(400, "kind is required: one of " + ", ".join(KINDS))
    ctx["kind"] = kind
    if kind == "ping":
        return 200, _ping()
    spec = _agent_spec(payload)
    ctx["agent"] = spec.code
    if kind == "agent":
        return 200, _run_agent(spec, payload, corr)
    if kind == "tool":
        ctx["name"] = payload.get("tool")
        return 200, _run_tool(spec, payload, corr)
    ctx["name"] = payload.get("job")
    return 200, _run_job(spec, payload, corr)


def _failure(e: Exception, ctx: dict[str, Any], corr: str) -> tuple[int, dict[str, Any]]:
    """An exception from the reasoning entry, an adapter or a job -> status and body.
    No traceback in the body; it goes to the log with the correlation id."""
    if _is(e, "Forbidden"):
        return 403, {"error": _short(e)}
    if _is(e, "NotFound"):
        return 404, {"error": _short(e)}
    if isinstance(e, ValueError):
        return (404 if ctx["kind"] == "agent" else 400), {"error": str(e)[:200]}
    _log.error("runtime call failed: kind=%s agent=%s call=%s correlation_id=%s",
               ctx["kind"], ctx["agent"], ctx["name"], corr, exc_info=e)
    return 500, {"error": _short(e)}


# --- entry ---------------------------------------------------------------------------

def _invoke(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and run one request; always returns the response envelope."""
    start = time.monotonic()
    trace = _svc(_TRACE)
    corr = _correlation_id(payload) if isinstance(payload, dict) else trace.new_correlation_id()
    ctx: dict[str, Any] = {"kind": None, "agent": registry.current_agent_id(), "name": None}
    corr_token = trace.CURRENT_CORRELATION_ID.set(corr)
    transport_token = trace.CURRENT_TRANSPORT.set(TRANSPORT)
    try:
        code, result = _dispatch(payload, ctx, corr)
    except _Reject as r:
        _log.info("request refused: kind=%s agent=%s status=%s", ctx["kind"], ctx["agent"], r.code)
        code, result = r.code, r.body
    except Exception as e:  # noqa: BLE001 - the dispatcher answers every request with an envelope; _failure logs it
        code, result = _failure(e, ctx, corr)
    finally:
        trace.CURRENT_TRANSPORT.reset(transport_token)
        trace.CURRENT_CORRELATION_ID.reset(corr_token)
    _log_line(ctx, code, start, corr, payload.get("caller") if isinstance(payload, dict) else None)
    return _envelope(code, result, ctx["agent"])


def _log_line(ctx: dict[str, Any], status: int, start: float, corr: str | None, caller: Any) -> None:
    """One structured stdout line per invocation (the runtime log group)."""
    name = ctx.get("name") if isinstance(ctx.get("name"), str) else None
    line: dict[str, Any] = {"kind": ctx.get("kind"), "agent": ctx.get("agent")}
    line["job" if ctx.get("kind") == "job" else "tool"] = name
    line.update({"status": status, "latency_ms": int((time.monotonic() - start) * 1000),
                 "correlation_id": corr, "caller": caller if isinstance(caller, str) else None})
    print(json.dumps(line), flush=True)


# --- HTTP contract ----------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, body: dict[str, Any], *, close: bool = False) -> None:
        data = json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        if close:
            self.send_header("Connection", "close")   # also sets close_connection
        self.end_headers()
        self.wfile.write(data)

    def _refuse(self, code: int, error: str, start: float) -> None:
        """Answer before dispatch (bad length, oversized or unparsable body).

        The body may not have been read, so the answer says ``Connection: close``
        and the server closes the connection after it.
        """
        _log_line({"kind": None, "agent": registry.current_agent_id()}, code, start, None, None)
        self._send(code, {"error": error}, close=True)

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/") == "/ping" or self.path == "/":
            return self._send(200, {"status": "Healthy"})
        return self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if self.path.rstrip("/") != "/invocations":
            return self._send(404, {"error": "not found"})
        start = time.monotonic()
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0:
            return self._refuse(400, "bad request: invalid Content-Length", start)
        if length > registry.MAX_BODY_BYTES:
            return self._refuse(413, f"request body over {registry.MAX_BODY_BYTES} bytes", start)
        try:
            payload = json.loads(self.rfile.read(length) if length else b"{}")
        except ValueError:
            return self._refuse(400, "bad request: the body is not valid JSON", start)
        try:
            return self._send(200, _invoke(payload))
        except Exception as e:  # noqa: BLE001 - last line of defence: every request gets a JSON answer
            _log.error("runtime request failed: %s", type(e).__name__, exc_info=True)
            return self._send(500, {"ok": False, "error": _short(e)})

    def log_message(self, *a):  # quiet the default access log
        pass


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), _Handler)
    served = registry.current_agent_id()
    print(f"Retail Ops AgentCore runtime for {served or 'local'} listening on :{PORT} "
          "(POST /invocations, GET /ping)")
    server.serve_forever()


if __name__ == "__main__":
    main()
