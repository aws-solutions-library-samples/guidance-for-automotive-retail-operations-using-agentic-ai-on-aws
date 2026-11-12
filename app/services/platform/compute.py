"""Audited compute interface — local audited calc | AgentCore Code Interpreter (AR-2).

The spec computes estimate totals, TCO, and equity/payment math in AgentCore
Code Interpreter and returns inputs + code + result for the audit trail
(acceptance #4/#27: every number traceable to a tool call). This interface —
`compute(name, inputs, expr)` -> {inputs, code, result} — has a `local` impl
that evaluates a restricted arithmetic expression and records the audit triple,
and an `aws` impl that runs the SAME expression in a real AgentCore Code
Interpreter session (`bedrock-agentcore` start/invoke/stop_code_interpreter_
session against the AWS built-in `aws.codeinterpreter.v1`, or a custom
interpreter via RETAIL_OPS_CODE_INTERPRETER_ID). One session is cached per Lambda
container and restarted on expiry; ANY failure falls back to the local audited
calc with an honest engine label, so a number is never un-auditable.
"""
from __future__ import annotations

import ast
import json
import operator
import os
from typing import Any

from .mode import integration_mode

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg,
    ast.Mod: operator.mod,
}


def _mode() -> str:
    return integration_mode()


def _safe_eval(node: ast.AST, vars: dict[str, float]) -> float:
    """Evaluate a restricted arithmetic AST (numbers, named inputs, + - * / ** %).
    No calls, no attributes, no names outside `vars` — safe for an audited calc."""
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body, vars)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError("only numeric constants allowed")
    if isinstance(node, ast.Name):
        if node.id in vars:
            return float(vars[node.id])
        raise ValueError(f"unknown input {node.id!r}")
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.left, vars), _safe_eval(node.right, vars))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.operand, vars))
    raise ValueError(f"disallowed expression element: {type(node).__name__}")


class ComputeEngine:
    def compute(self, name: str, inputs: dict[str, float], expr: str) -> dict[str, Any]:
        raise NotImplementedError


class LocalAuditedCompute(ComputeEngine):
    """Evaluate `expr` over `inputs` with a restricted AST and return the audit
    triple (inputs + code + result). This is what makes a number traceable: the
    caller stores this triple alongside the figure it produced."""

    def compute(self, name: str, inputs: dict[str, float], expr: str) -> dict[str, Any]:
        tree = ast.parse(expr, mode="eval")
        result = _safe_eval(tree, inputs)
        return {"name": name, "inputs": dict(inputs), "code": expr,
                "result": round(result, 4) if isinstance(result, float) else result,
                "engine": "local_audited",
                "source_ref": f"platform.LocalAuditedCompute:{name} (inputs+code+result retained)"}


# One interpreter session per Lambda container (module global): sessions are
# billed/bounded resources and start_code_interpreter_session is slow, so a
# warm container reuses its session until AWS expires it.
_SESSION: dict[str, Any] = {}


def _interpreter_id() -> str:
    """The AWS built-in public interpreter unless a custom one is configured."""
    return os.environ.get("RETAIL_OPS_CODE_INTERPRETER_ID", "aws.codeinterpreter.v1")


def _session_expired(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return "invalidsession" in text or "expired" in text or "session" in text and "not found" in text


def _stop_session() -> None:
    """Best-effort stop of the cached session (used when it is being replaced
    or after a failure, never on the hot path of a reused session)."""
    sid, client = _SESSION.pop("id", None), _SESSION.pop("client", None)
    if sid and client is not None:
        try:
            client.stop_code_interpreter_session(
                codeInterpreterIdentifier=_interpreter_id(), sessionId=sid)
        except Exception:
            pass


class CodeInterpreterCompute(ComputeEngine):
    """AgentCore Code Interpreter (start/invoke/stop_code_interpreter_session).
    Falls back to the local audited calc on ANY failure, preserving the
    audit-triple contract and reporting the real engine."""

    def _session(self) -> tuple[Any, str]:
        if _SESSION.get("id"):
            return _SESSION["client"], _SESSION["id"]
        import boto3

        client = boto3.client("bedrock-agentcore", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        resp = client.start_code_interpreter_session(
            codeInterpreterIdentifier=_interpreter_id(), name="retail-ops-audited-compute",
            sessionTimeoutSeconds=900)
        _SESSION["client"], _SESSION["id"] = client, resp["sessionId"]
        return client, resp["sessionId"]

    @staticmethod
    def _code(inputs: dict[str, float], expr: str) -> str:
        """The audited python snippet: `expr` over `inputs`, result as JSON on
        stdout. `expr` has already parsed as a restricted arithmetic expression
        locally (same contract as LocalAuditedCompute) before it is sent."""
        return (
            "import json\n"
            f"inputs = json.loads({json.dumps(json.dumps(inputs))})\n"
            f"result = eval(compile({expr!r}, '<expr>', 'eval'), {{'__builtins__': {{}}}}, dict(inputs))\n"
            "print(json.dumps({'result': result}))"
        )

    def _invoke(self, code: str) -> Any:
        client, sid = self._session()
        try:
            resp = client.invoke_code_interpreter(
                codeInterpreterIdentifier=_interpreter_id(), sessionId=sid, name="executeCode",
                arguments={"language": "python", "code": code})
        except Exception as e:
            if _session_expired(e):  # container outlived the session — restart once
                _stop_session()
                client, sid = self._session()
                resp = client.invoke_code_interpreter(
                    codeInterpreterIdentifier=_interpreter_id(), sessionId=sid, name="executeCode",
                    arguments={"language": "python", "code": code})
            else:
                raise
        stdout = ""
        for event in resp.get("stream") or []:
            for item in ((event.get("result") or {}).get("content") or []):
                if item.get("type") == "text" and item.get("text"):
                    stdout += item["text"]
            structured = (event.get("result") or {}).get("structuredContent") or {}
            if structured.get("stdout"):
                stdout += structured["stdout"]
            if (event.get("result") or {}).get("isError"):
                raise RuntimeError(f"code interpreter error: {stdout[:200]}")
        return json.loads(stdout.strip().splitlines()[-1])["result"], _SESSION.get("id")

    def compute(self, name: str, inputs: dict[str, float], expr: str) -> dict[str, Any]:
        # Validate the expression against the SAME restricted grammar first: the
        # interpreter must never widen what an audited calc may run.
        tree = ast.parse(expr, mode="eval")
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Expression, ast.Constant, ast.Name, ast.Load,
                                     ast.BinOp, ast.UnaryOp)) and type(node) not in _OPS:
                raise ValueError(f"disallowed expression element: {type(node).__name__}")
        code = self._code(inputs, expr)
        try:
            result, sid = self._invoke(code)
            return {"name": name, "inputs": dict(inputs), "code": code,
                    "result": round(result, 4) if isinstance(result, float) else result,
                    "engine": "agentcore_code_interpreter",
                    "source_ref": f"platform.CodeInterpreterCompute:{name} "
                                  f"(inputs+code+result retained; session {sid})"}
        except Exception as e:  # no access / throttle / parse — audited local twin
            try:
                _stop_session()  # don't leave a broken session cached
            finally:
                out = LocalAuditedCompute().compute(name, inputs, expr)
                out["engine"] = f"local_audited (Code Interpreter unavailable: {type(e).__name__})"
            return out


def get_compute_engine() -> ComputeEngine:
    return CodeInterpreterCompute() if _mode() == "aws" else LocalAuditedCompute()
