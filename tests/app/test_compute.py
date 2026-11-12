"""Audited compute (platform/compute.py): local mode unchanged; aws mode runs a
real AgentCore Code Interpreter session and falls back honestly when it can't."""
from __future__ import annotations

import pytest

from app.services.platform import compute


def test_local_mode_unchanged(monkeypatch):
    monkeypatch.delenv("INTEGRATION_MODE", raising=False)
    eng = compute.get_compute_engine()
    assert isinstance(eng, compute.LocalAuditedCompute)
    out = eng.compute("tco_total", {"a": 2, "b": 3}, "a * b + 1")
    assert out["result"] == 7.0 and out["engine"] == "local_audited"
    assert out["inputs"] == {"a": 2, "b": 3} and out["code"] == "a * b + 1"


def test_aws_mode_falls_back_when_interpreter_unreachable(monkeypatch):
    monkeypatch.setenv("INTEGRATION_MODE", "aws")
    compute._SESSION.clear()
    eng = compute.get_compute_engine()
    assert isinstance(eng, compute.CodeInterpreterCompute)
    # conftest's offline guard makes every boto3 client call raise RuntimeError,
    # so the engine must land on the audited local twin and say so.
    out = eng.compute("estimate_total", {"labor": 297.0, "parts": 120.5}, "labor + parts")
    assert out["result"] == 417.5
    assert out["engine"] == "local_audited (Code Interpreter unavailable: RuntimeError)"
    assert "LocalAuditedCompute" in out["source_ref"]


class _FakeAgentCore:
    """Shape of the bedrock-agentcore code-interpreter API, success path."""

    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0

    def start_code_interpreter_session(self, **kw):
        self.started += 1
        assert kw["codeInterpreterIdentifier"] == "aws.codeinterpreter.v1"
        return {"sessionId": f"cis-{self.started}"}

    def invoke_code_interpreter(self, **kw):
        assert kw["arguments"]["language"] == "python"
        scope: dict = {}
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exec(kw["arguments"]["code"], scope)  # the snippet is self-contained
        return {"stream": [{"result": {"content": [{"type": "text", "text": buf.getvalue()}]}}]}

    def stop_code_interpreter_session(self, **kw):
        self.stopped += 1
        return {}


def test_aws_mode_runs_in_session_and_reuses_it(monkeypatch):
    import boto3
    monkeypatch.setenv("INTEGRATION_MODE", "aws")
    compute._SESSION.clear()
    fake = _FakeAgentCore()
    monkeypatch.setattr(boto3, "client", lambda *a, **k: fake)
    eng = compute.get_compute_engine()
    out = eng.compute("equity", {"value": 31000, "payoff": 24500}, "value - payoff")
    assert out["result"] == 6500 and out["engine"] == "agentcore_code_interpreter"
    assert "session cis-1" in out["source_ref"]       # session id in the audit ref
    out2 = eng.compute("payment", {"p": 100, "n": 4}, "p / n")
    assert out2["result"] == 25.0 and fake.started == 1  # session reused
    compute._SESSION.clear()


def test_disallowed_expression_still_rejected_in_aws_mode(monkeypatch):
    monkeypatch.setenv("INTEGRATION_MODE", "aws")
    compute._SESSION.clear()
    with pytest.raises(SyntaxError):
        compute.CodeInterpreterCompute().compute("bad", {}, "import os")
    with pytest.raises(ValueError):
        compute.CodeInterpreterCompute().compute("bad", {}, "__import__('os')")