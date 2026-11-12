# ruff: noqa: S101
"""Admin › Agents: where each agent runs and its runtime counters (ADR 0009, planner P3).

Trace rows are written through `trace.write_trace`, the same path the runtime
dispatcher (CURRENT_TRANSPORT) and the client's fallback span use.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.agentcore import client
from app.services.admin import agents_health
from app.services.common import trace
from tests.app.conftest import api_event, call

ADMIN = {"user": "U-US-00051", "role": "admin_tenant", "is_oem": True}
ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/retail_ops_dev_{}-AbCdEf0123"


def _rows(h: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {a["agent_id"]: a for a in h["agents"]}


def _runtime_span(agent: str, tool: str, outcome: str = "ok", latency_ms: int = 40) -> None:
    token = trace.CURRENT_TRANSPORT.set("agentcore-runtime")
    try:
        trace.write_trace(tool, agent_id=agent, outcome=outcome, latency_ms=latency_ms, market="us")
    finally:
        trace.CURRENT_TRANSPORT.reset(token)


def _fallback_span(agent: str, outcome: str, call_name: str) -> None:
    trace.write_trace("runtime.fallback", agent_id=agent, outcome=outcome, latency_ms=90000, market="us",
                      extra={"transport": "in-process-fallback", "call": call_name})


def _set_map(monkeypatch: pytest.MonkeyPatch, codes: list[str]) -> None:
    client._parse_map.cache_clear()
    monkeypatch.setenv("RETAIL_OPS_AGENT_RUNTIMES",
                       json.dumps({c: ARN.format(c.lower()) for c in codes}))


def test_in_process_without_runtime_env(fake_ddb):
    rows = _rows(agents_health.health("us"))
    assert len(rows) == 7
    for code in ("A1", "A2", "A3", "A4", "A5", "A7"):
        rt = rows[code]["runtime"]
        assert rt["mode"] == "in_process"
        assert rt["runtime_calls"] == 0 and rt["fallbacks"] == 0
    assert rows["A3"]["runtime"]["engine"] == "reasoning"
    assert {rows[c]["runtime"]["engine"] for c in ("A1", "A2", "A4", "A5", "A7")} == {"tools"}
    assert rows["A6"]["runtime"] == {"mode": "quick", "engine": "quick", "runtime_calls": 0, "fallbacks": 0}


def test_agentcore_mode_follows_the_runtime_map(fake_ddb, monkeypatch):
    _set_map(monkeypatch, ["A1", "A3", "A6"])
    rows = _rows(agents_health.health("us"))
    assert rows["A1"]["runtime"]["mode"] == "agentcore"
    assert rows["A3"]["runtime"]["mode"] == "agentcore"
    assert rows["A2"]["runtime"]["mode"] == "in_process"
    assert rows["A7"]["runtime"]["mode"] == "in_process"
    # A6 lives in AWS Quick even if a map names it
    assert rows["A6"]["runtime"]["mode"] == "quick"


def test_disable_switch_reports_in_process(fake_ddb, monkeypatch):
    _set_map(monkeypatch, ["A1", "A3"])
    monkeypatch.setenv("RETAIL_OPS_AGENT_RUNTIME_DISABLE", "A1")
    rows = _rows(agents_health.health("us"))
    assert rows["A1"]["runtime"]["mode"] == "in_process"
    assert rows["A3"]["runtime"]["mode"] == "agentcore"
    monkeypatch.setenv("RETAIL_OPS_AGENT_RUNTIME_DISABLE", "1")
    rows = _rows(agents_health.health("us"))
    assert {rows[c]["runtime"]["mode"] for c in ("A1", "A3")} == {"in_process"}


def test_counters_count_tagged_rows_only(fake_ddb):
    before = _rows(agents_health.health("us"))
    _runtime_span("A1", "runtime.recommend_vehicles")
    _runtime_span("A1", "runtime.job.rescore")
    _runtime_span("A1", "inventory.lookup")                 # tagged, but neither a runtime span nor the A3 row
    trace.write_trace("runtime.recommend_vehicles", agent_id="A1", market="us")   # untagged
    _runtime_span("A3", "assistant_agent")
    _fallback_span("A1", "lost", "recommend_vehicles")
    _fallback_span("A1", "unavailable", "rescore")
    _fallback_span("A3", "unavailable", "agent")
    after = _rows(agents_health.health("us"))
    assert after["A1"]["runtime"]["runtime_calls"] == 2
    assert after["A1"]["runtime"]["fallbacks"] == 2
    assert after["A3"]["runtime"]["runtime_calls"] == 1
    assert after["A3"]["runtime"]["fallbacks"] == 1
    assert after["A4"]["runtime"] == before["A4"]["runtime"]
    # the four non-fallback A1 rows and the one A3 row are invocations; fallback spans are not
    assert after["A1"]["invocations"] == before["A1"]["invocations"] + 4
    assert after["A3"]["invocations"] == before["A3"]["invocations"] + 1


def test_fallback_rows_stay_out_of_errors_latency_and_tools(fake_ddb):
    before = _rows(agents_health.health("us", agent="A4"))["A4"]
    for _ in range(5):
        _fallback_span("A4", "lost", "trade_in_equity")
    after = _rows(agents_health.health("us", agent="A4"))["A4"]
    assert after["runtime"]["fallbacks"] == 5
    assert after["invocations"] == before["invocations"]
    assert after["errors"] == before["errors"]
    assert after["latency_p95_ms"] == before["latency_p95_ms"]
    assert all(t["tool"] != "runtime.fallback" for t in after["top_tools"])
    assert all(t["tool"] != "runtime.fallback" for t in after["recent_traces"])
    assert after["status"] == before["status"]


def test_route_returns_the_runtime_object(fake_ddb, monkeypatch):
    _set_map(monkeypatch, ["A2"])
    code, h = call(api_event("GET", "/admin/agents/health", **ADMIN))
    assert code == 200
    rows = _rows(h)
    assert rows["A2"]["runtime"]["mode"] == "agentcore"
    assert set(rows["A5"]["runtime"]) == {"mode", "engine", "runtime_calls", "fallbacks"}
