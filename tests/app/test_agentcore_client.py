# ruff: noqa: S101
"""AgentCore runtime client (ADR 0009, design §6.3/§6.4): env-only resolution,
three-phase failure classification and the call-site helpers."""
from __future__ import annotations

import dataclasses
import io
import json
import logging

import pytest
from botocore import exceptions as bx

from app.agentcore import client, registry

ARNS = {code: f"arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/{spec.runtime_name}-abc"
        for code, spec in registry.AGENTS.items()}
ADVISOR = {"user_id": "U-US-00002", "role": "dealer_advisor", "groups": ["dealer_advisor"], "extra_screens": [],
           "dealer_ids": {"D-102", "D-101"}, "is_oem": False, "market_id": "us", "scope_node_id": "D-101",
           "customer_id": ""}
OEM = {"user_id": "U-US-00044", "role": "oem_dm", "groups": ["oem_dm"], "dealer_ids": set(), "is_oem": True,
       "market_id": "us", "scope_node_id": "DIST-07", "customer_id": ""}


class _FakeDataPlane:
    """Stands in for the bedrock-agentcore client: raises `behaviour` or answers it."""

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls: list[dict] = []

    def invoke_agent_runtime(self, **kw):
        self.calls.append(kw)
        if isinstance(self.behaviour, BaseException):
            raise self.behaviour
        raw = self.behaviour if isinstance(self.behaviour, bytes) else json.dumps(self.behaviour).encode()
        return {"response": io.BytesIO(raw)}


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("RETAIL_OPS_AGENT_RUNTIMES", json.dumps(ARNS))
    monkeypatch.setattr(client, "_CFG", client._CFG.with_name("absent-agentcore-config.json"))


def _answer(monkeypatch, behaviour) -> _FakeDataPlane:
    fake = _FakeDataPlane(behaviour)
    monkeypatch.setattr(client, "_data_client", lambda timeout_s: fake)
    return fake


def _client_error(code: str) -> bx.ClientError:
    return bx.ClientError({"Error": {"Code": code, "Message": "x"}}, "InvokeAgentRuntime")


# --- resolution --------------------------------------------------------------------

def test_nothing_resolves_without_runtime_variables_even_with_a_config_file(monkeypatch, tmp_path):
    cfg = tmp_path / "agentcore-config.json"
    cfg.write_text(json.dumps({"schema_version": 2, "agents": {c: {"runtime_arn": a} for c, a in ARNS.items()},
                               "agent_runtime_arn": ARNS["A3"]}))
    monkeypatch.setattr(client, "_CFG", cfg)
    for code in registry.AGENTS:
        assert client.runtime_arn(code) is None and client.enabled(code) is False
    monkeypatch.setenv("RETAIL_OPS_AGENT_RUNTIME_USE_CONFIG", "1")          # explicit developer opt-in
    assert {c: client.runtime_arn(c) for c in registry.AGENTS} == ARNS
    cfg.write_text(json.dumps({"agent_runtime_name": "legacy", "agent_runtime_arn": ARNS["A3"]}))   # v1 file
    assert client.runtime_arn("A3") == ARNS["A3"] and client.runtime_arn("A1") is None
    cfg.write_text("{not json")
    assert client.runtime_arn("A3") is None


def test_resolution_order_env_map_then_legacy_variable(monkeypatch):
    monkeypatch.setattr(client, "_CFG", client._CFG.with_name("absent-agentcore-config.json"))
    monkeypatch.setenv("RETAIL_OPS_AGENT_RUNTIME_ARN", "arn:legacy")
    assert client.runtime_arn() == "arn:legacy" and client.runtime_arn("A1") is None   # legacy var is A3's only
    monkeypatch.setenv("RETAIL_OPS_AGENT_RUNTIMES", json.dumps({"a1": ARNS["A1"]}))
    assert client.runtime_arn("A1") == ARNS["A1"] and client.runtime_arn("A3") == "arn:legacy"
    monkeypatch.setenv("RETAIL_OPS_AGENT_RUNTIMES", json.dumps({"A1": ARNS["A1"], "A3": ARNS["A3"]}))
    assert client.runtime_arn("A3") == ARNS["A3"]


def test_disable_switch_all_or_a_list(monkeypatch, configured):
    monkeypatch.setenv("RETAIL_OPS_AGENT_RUNTIME_DISABLE", "1")
    assert all(client.runtime_arn(c) is None for c in registry.AGENTS)
    monkeypatch.setenv("RETAIL_OPS_AGENT_RUNTIME_DISABLE", "A2, a5")
    assert client.runtime_arn("A2") is None and client.runtime_arn("A5") is None
    assert client.runtime_arn("A1") == ARNS["A1"] and client.enabled("A3") is True


def test_a_runtime_never_resolves_itself(monkeypatch, configured):
    with registry.acting_as("A1"):
        assert client.runtime_arn("A1") is None and client.runtime_arn("A3") == ARNS["A3"]
    monkeypatch.setenv("RETAIL_OPS_AGENT_ID", "A2")
    assert client.runtime_arn("A2") is None and client.runtime_arn("A1") == ARNS["A1"]


def test_malformed_map_logs_once_and_resolves_nothing(monkeypatch, caplog):
    client._parse_map.cache_clear()
    monkeypatch.setattr(client, "_CFG", client._CFG.with_name("absent-agentcore-config.json"))
    monkeypatch.setenv("RETAIL_OPS_AGENT_RUNTIMES", "{A1: not-json")
    with caplog.at_level(logging.ERROR, logger="app.agentcore.client"):
        assert client.runtime_arn("A1") is None
        assert client.runtime_arn("A1") is None
    assert len([r for r in caplog.records if "RETAIL_OPS_AGENT_RUNTIMES" in r.getMessage()]) == 1
    monkeypatch.setenv("RETAIL_OPS_AGENT_RUNTIMES", json.dumps(["A1"]))
    assert client.runtime_arn("A1") is None


# --- classification (design §6.4, row by row) --------------------------------------------

@pytest.mark.parametrize("behaviour, status", [
    (_client_error("AccessDeniedException"), "unavailable"),
    (_client_error("ThrottlingException"), "unavailable"),
    (_client_error("ResourceNotFoundException"), "unavailable"),
    (_client_error("ValidationException"), "unavailable"),
    (bx.EndpointConnectionError(endpoint_url="https://runtime"), "unavailable"),
    (bx.ConnectTimeoutError(endpoint_url="https://runtime"), "unavailable"),
    (bx.NoCredentialsError(), "unavailable"),
    (bx.ParamValidationError(report="bad"), "unavailable"),
    (bx.ReadTimeoutError(endpoint_url="https://runtime"), "lost"),
    (_client_error("InternalServerException"), "lost"),
    (_client_error("RuntimeClientError"), "lost"),
    (RuntimeError("offline test: boto3 bedrock-agentcore.invoke_agent_runtime is not available"), "lost"),
    (b"<html>not json", "lost"),
    ({"result": {"ok": True}}, "lost"),
    ({"statusCode": "200", "result": {}}, "lost"),
    ({"statusCode": 200, "result": {"matches": []}}, "ok"),
    ({"statusCode": 403, "result": {"error": "Forbidden: out of scope"}}, "caller_error"),
    ({"statusCode": 409, "result": {"error": "already pending"}}, "caller_error"),
    ({"statusCode": 500, "result": {"error": "RuntimeError: boom"}}, "failed"),
])
def test_each_outcome_is_classified_by_phase_and_type(monkeypatch, configured, behaviour, status):
    fake = _answer(monkeypatch, behaviour)
    res = client.invoke_tool("A1", "rank_vehicles", {"journey_id": "J-US-J2"}, claims=ADVISOR, correlation_id="c-1")
    assert res.status == status and len(fake.calls) == 1
    if status in ("ok", "caller_error", "failed"):
        assert res.code == behaviour["statusCode"] and res.body["transport"] == "agentcore-runtime"
    else:
        assert res.code == 0 and res.error


def test_prepare_failures_are_unavailable_and_send_nothing(monkeypatch, configured):
    def _no_boto3(timeout_s):
        raise ImportError("No module named 'boto3'")

    monkeypatch.setattr(client, "_data_client", _no_boto3)
    assert client.invoke_tool("A1", "rank_vehicles", {"journey_id": "J-US-J2"}, claims=ADVISOR).status == "unavailable"
    fake = _answer(monkeypatch, {"statusCode": 200, "result": {}})
    bad_claims = {**ADVISOR, "user_id": None}
    res = client.invoke_tool("A1", "rank_vehicles", {"journey_id": "J-US-J2"}, claims=bad_claims)
    assert res.status == "unavailable" and "ClaimsError" in res.error
    big = {"journey_id": "J-US-J2", "pad": "x" * (registry.MAX_BODY_BYTES + 1)}
    res = client.invoke_tool("A1", "rank_vehicles", big, claims=ADVISOR)
    assert res.status == "unavailable" and fake.calls == []


def test_not_configured_makes_no_call(monkeypatch):
    fake = _answer(monkeypatch, {"statusCode": 200, "result": {}})
    res = client.invoke_tool("A1", "rank_vehicles", {"journey_id": "J-US-J2"}, claims=ADVISOR)
    assert res == client.RuntimeResult("not_configured") and fake.calls == []
    assert client.invoke_job("A4", "equity-monitor", {"market": "us", "limit": None}, apply=True).status == \
        "not_configured"
    with pytest.raises(ValueError):
        client.invoke_tool("A1", "build_equity", {}, claims=ADVISOR)          # A1 has no such tool


def test_payload_shapes_and_sessions(monkeypatch, configured):
    fake = _answer(monkeypatch, {"statusCode": 200, "result": {"ok": True}})
    client.invoke_tool("A1", "rank_vehicles", {"journey_id": "J-US-J2"}, claims={**ADVISOR, "extra": 1},
                       correlation_id="corr-1", caller="A2")
    sent = fake.calls[0]
    body = json.loads(sent["payload"])
    assert sent["agentRuntimeArn"] == ARNS["A1"] and sent["contentType"] == "application/json"
    assert body["kind"] == "tool" and body["agent"] == "A1" and body["tool"] == "rank_vehicles"
    assert body["caller"] == "A2" and body["correlation_id"] == "corr-1"
    assert body["claims"]["dealer_ids"] == ["D-101", "D-102"] and "extra" not in body["claims"]
    assert registry.normalize_claims(body["claims"]) == ADVISOR
    assert sent["runtimeSessionId"].startswith("A1-") and len(sent["runtimeSessionId"]) >= 33
    client.invoke_job("A4", "equity-monitor", {"market": "us", "limit": 5}, apply=False, correlation_id="corr-2")
    job = json.loads(fake.calls[1]["payload"])
    assert job == {"kind": "job", "agent": "A4", "job": "equity-monitor", "payload": {"market": "us", "limit": 5},
                   "apply": False, "correlation_id": "corr-2"}
    assert fake.calls[1]["runtimeSessionId"].startswith("A4-job-equity-monitor-")
    client.invoke_job("A4", "equity-monitor", {"market": "us", "limit": 5}, apply=False)
    assert fake.calls[2]["runtimeSessionId"] != fake.calls[1]["runtimeSessionId"]


def test_session_id_is_stable_within_a_bucket_and_distinct_otherwise():
    t = 1_800_000_000.0
    sid = client._session_id("A1", "U-1", "J-1", now=t)
    assert len(sid) >= 33 and sid == client._session_id("A1", "U-1", "J-1", now=t + 30)
    assert sid != client._session_id("A1", "U-1", "J-1", now=t + 600)
    assert sid != client._session_id("A1", "U-2", "J-1", now=t)
    assert sid != client._session_id("A1", "U-1", "J-2", now=t)
    assert sid != client._session_id("A3", "U-1", "J-1", now=t)


def test_every_data_client_has_bounded_timeouts_and_one_attempt(monkeypatch, configured):
    import boto3
    built: list[dict] = []

    def _client(service, *_a, **kw):
        built.append({"service": service, **kw})
        return _FakeDataPlane(bx.ConnectTimeoutError(endpoint_url="https://runtime"))

    monkeypatch.setattr(boto3, "client", _client)
    res = client.invoke_tool("A1", "rank_vehicles", {"journey_id": "J-US-J2"}, claims=ADVISOR)
    assert res.status == "unavailable"                         # nothing was sent
    client.invoke_tool("A5", "recommend_budget_shift", {}, claims=OEM, timeout_s=20)
    client.invoke_job("A4", "equity-monitor", {"market": "us", "limit": None}, apply=True)
    client.invoke_agent("A3", "next?", journey_id=None, claims=ADVISOR)
    assert [b["service"] for b in built] == ["bedrock-agentcore"] * 4
    assert [b["config"].read_timeout for b in built] == [8, 20, 280, 26]
    for b in built:
        assert b["config"].connect_timeout == 3 and b["config"].retries == {"max_attempts": 1}


# --- invoke_agent (unchanged contract) ------------------------------------------------------

def test_invoke_agent_returns_the_runtime_body_for_answered_errors(monkeypatch, configured):
    _answer(monkeypatch, {"statusCode": 500, "result": {"error": "x"}})
    assert client.invoke_agent("A3", "q", journey_id="J-US-J2", claims=ADVISOR) == \
        {"error": "x", "transport": "agentcore-runtime"}
    _answer(monkeypatch, {"statusCode": 403, "result": {"error": "Forbidden: no"}})
    assert client.invoke_agent("A3", "q", journey_id="J-US-J2", claims=ADVISOR)["error"] == "Forbidden: no"
    _answer(monkeypatch, {"statusCode": 200, "result": {"answer": "hi", "engine": "strands"}})
    assert client.invoke_agent("A3", "q", journey_id=None, claims=ADVISOR) == \
        {"answer": "hi", "engine": "strands", "transport": "agentcore-runtime"}


def test_invoke_agent_returns_none_when_unavailable_or_lost(monkeypatch, configured):
    _answer(monkeypatch, _client_error("AccessDeniedException"))
    assert client.invoke_agent("A3", "q", journey_id=None, claims=ADVISOR) is None
    _answer(monkeypatch, bx.ReadTimeoutError(endpoint_url="https://runtime"))
    assert client.invoke_agent("A3", "q", journey_id=None, claims=ADVISOR) is None
    monkeypatch.setenv("RETAIL_OPS_AGENT_RUNTIME_STRICT", "1")
    assert "ReadTimeoutError" in client.invoke_agent("A3", "q", journey_id=None, claims=ADVISOR)["__error__"]
    monkeypatch.delenv("RETAIL_OPS_AGENT_RUNTIMES")
    assert client.invoke_agent("A3", "q", journey_id=None, claims=ADVISOR) is None   # not configured


# --- call_or_local --------------------------------------------------------------------------

def _local_calls():
    calls: list[int] = []

    def local():
        calls.append(1)
        return 200, {"where": "local"}

    return calls, local


@pytest.mark.parametrize("behaviour", [_client_error("ThrottlingException"),
                                       bx.ReadTimeoutError(endpoint_url="https://runtime")])
def test_call_or_local_reruns_locally_when_unavailable_or_lost(monkeypatch, configured, behaviour):
    _answer(monkeypatch, behaviour)
    calls, local = _local_calls()
    assert client.call_or_local("A5", "recommend_budget_shift", {}, claims=OEM, local=local) == \
        (200, {"where": "local"})
    assert calls == [1]


def test_call_or_local_answers_from_the_runtime_otherwise(monkeypatch, configured):
    calls, local = _local_calls()
    _answer(monkeypatch, {"statusCode": 200, "result": {"recommended": True}})
    assert client.call_or_local("A5", "recommend_budget_shift", {}, claims=OEM, local=local) == \
        (200, {"recommended": True, "transport": "agentcore-runtime"})
    _answer(monkeypatch, {"statusCode": 500, "result": {"error": "RuntimeError: boom"}})
    code, body = client.call_or_local("A5", "submit_budget_shift", {}, claims=OEM, local=local)
    assert code == 500 and body["error"] == "RuntimeError: boom"
    _answer(monkeypatch, {"statusCode": 403, "result": {"error": "role may not"}})
    code, body = client.call_or_local("A5", "submit_budget_shift", {}, claims=OEM, local=local)
    assert code == 403 and body["error"] == "role may not"
    assert calls == []
    monkeypatch.delenv("RETAIL_OPS_AGENT_RUNTIMES")
    assert client.call_or_local("A5", "submit_budget_shift", {}, claims=OEM, local=local)[1] == {"where": "local"}


def test_call_or_local_refuses_effectful_tools(monkeypatch, configured):
    effectful = dataclasses.replace(registry.tool("A5", "submit_budget_shift"), read_only=False, idempotent=False)
    monkeypatch.setattr(registry, "tool", lambda code, name: effectful)
    with pytest.raises(ValueError):
        client.call_or_local("A5", "submit_budget_shift", {}, claims=OEM, local=lambda: (200, {}))


# --- fallback span (planner decision P3) -------------------------------------------------------

def test_a_fallback_span_is_written_for_configured_calls_that_did_not_answer(fake_ddb, monkeypatch, configured):
    _answer(monkeypatch, bx.ReadTimeoutError(endpoint_url="https://runtime"))
    client.invoke_tool("A1", "rank_vehicles", {"journey_id": "J-US-J2"}, claims=ADVISOR, correlation_id="corr-fb")
    _answer(monkeypatch, {"statusCode": 200, "result": {}})
    client.invoke_tool("A1", "rank_vehicles", {"journey_id": "J-US-J2"}, claims=ADVISOR, correlation_id="corr-ok")
    monkeypatch.delenv("RETAIL_OPS_AGENT_RUNTIMES")
    client.invoke_tool("A1", "rank_vehicles", {"journey_id": "J-US-J2"}, claims=ADVISOR, correlation_id="corr-nc")
    rows = [t for t in fake_ddb.rows("PLAT_AgentTraces") if t.get("tool") == "runtime.fallback"]
    assert len(rows) == 1
    assert rows[0]["correlation_id"] == "corr-fb" and rows[0]["agent_id"] == "A1" and rows[0]["outcome"] == "lost"
    assert rows[0]["transport"] == "in-process-fallback" and rows[0]["call"] == "rank_vehicles"
