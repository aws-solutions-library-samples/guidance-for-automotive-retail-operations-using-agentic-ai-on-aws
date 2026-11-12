"""A3 reasoning agent (Strands pilot): tools, scope, fences, fallback, transport."""
from __future__ import annotations

import json

import pytest

from app.agentcore import agent as runtime
from app.services.agents import a3_agent
from app.services.common.authz import Forbidden
from tests.app.conftest import api_event, call

ADVISOR = {"user_id": "U-US-00002", "role": "dealer_advisor", "dealer_ids": {"D-102"}, "is_oem": False,
           "market_id": "us", "scope_node_id": "D-102"}


def _with_ctx(claims=ADVISOR):
    return a3_agent._CTX.set({"claims": claims, "env_prefix": None, "citations": [], "tools": []})


def test_tools_collect_citations_and_log(fake_ddb):
    tok = _with_ctx()
    try:
        lc = a3_agent.get_lifecycle_context("J-US-J2")
        assert lc["stage"] and "handoff" in lc
        act = a3_agent.get_recent_activity("J-US-J2", limit=3)
        assert isinstance(act, list)
        sug = a3_agent.get_contextual_suggestions("J-US-J2")
        assert "suggestions" in sug
        inc = a3_agent.get_incentives("J-US-J2")
        assert "incentives" in inc
        eq = a3_agent.get_trade_equity("J-US-J2")
        assert "equity_position" in eq
        kb = a3_agent.search_knowledge("EV range anxiety", k=2)
        assert kb["engine"].startswith("local_keyword")
        c = a3_agent._CTX.get()
        assert {t["tool"] for t in c["tools"]} >= {"get_lifecycle_context", "get_recent_activity",
                                                  "get_contextual_suggestions", "get_incentives",
                                                  "get_trade_equity", "search_knowledge"}
        assert any(cit["ref"] == "DEALER_Journeys:J-US-J2" for cit in c["citations"])
    finally:
        a3_agent._CTX.reset(tok)


def test_tools_refuse_journeys_outside_scope(fake_ddb):
    other = {**ADVISOR, "dealer_ids": {"D-999"}, "scope_node_id": "D-999"}
    tok = _with_ctx(other)
    try:
        with pytest.raises(Forbidden):
            a3_agent.get_lifecycle_context("J-US-J2")
        assert a3_agent._CTX.get()["tools"] == []        # nothing ran
    finally:
        a3_agent._CTX.reset(tok)


def test_send_outreach_is_held_at_recommend_level(fake_ddb):
    tok = _with_ctx()
    try:
        out = a3_agent.send_outreach("J-US-J2", "follow_up", "email")
        assert out["status"] == "held_for_human" and out["autonomy_level"] == "recommend"
        rec = [r for r in fake_ddb.rows("DEALER_Recommendations") if r["recommendation_id"] == out["recommendation_id"]]
        assert rec and rec[0]["action"] == "outreach_draft" and rec[0]["agent_id"] == "A3"
        assert not any(m.get("journey_id") == "J-US-J2" and m.get("direction") == "outbound"
                       and "A3 agent" in json.dumps(m) for m in fake_ddb.rows("CUST_Messages"))
    finally:
        a3_agent._CTX.reset(tok)


def test_create_task_assigns_owner(fake_ddb):
    tok = _with_ctx()
    try:
        out = a3_agent.create_task("J-US-J2", "Call back about the trade appraisal", due_in_hours=4)
        assert out["task_id"] and out["assignee"]
        assert any(t["task_id"] == out["task_id"] for t in fake_ddb.rows("DEALER_Tasks"))
    finally:
        a3_agent._CTX.reset(tok)


def test_run_refusal_gate_runs_before_the_model(fake_ddb, monkeypatch):
    monkeypatch.setenv("INTEGRATION_MODE", "aws")
    out = a3_agent.run("Will this customer get approved for the loan?", "J-US-J2", ADVISOR)
    assert out["engine"] == "deterministic (refusal gate)"
    assert out["actions"] == ["handoff_to_human"] and out["tools_used"] == []


def test_run_falls_back_to_one_shot_when_agent_unavailable(fake_ddb, monkeypatch):
    monkeypatch.setenv("INTEGRATION_MODE", "local")       # agent off in local mode
    out = a3_agent.run("What should I do next with this customer?", "J-US-J2", ADVISOR)
    assert "(agent unavailable)" in out["engine"]
    assert out["tools_used"] == ["assistant_chat"] and out["answer"]


def test_run_enforces_scope_before_any_tool(fake_ddb, monkeypatch):
    monkeypatch.setenv("INTEGRATION_MODE", "aws")
    other = {**ADVISOR, "dealer_ids": {"D-999"}, "scope_node_id": "D-999"}
    with pytest.raises(Forbidden):
        a3_agent.run("next step?", "J-US-J2", other)


def test_run_with_strands_stub_collects_tools_and_citations(fake_ddb, monkeypatch):
    """Drive run() with a fake Agent that calls two tools, so the orchestration
    (context var, citations, tool log, trace) is covered without Bedrock."""
    monkeypatch.setenv("INTEGRATION_MODE", "aws")
    monkeypatch.setattr(a3_agent, "available", lambda: True)

    class _Result:
        stop_reason = "end_turn"
        metrics = None

        def __str__(self):
            return "Per the journey, the customer is in CONFIGURE; book the test drive next."

    class _FakeAgent:
        def __call__(self, prompt):
            assert "J-US-J2" in prompt
            a3_agent.get_lifecycle_context("J-US-J2")
            a3_agent.get_incentives("J-US-J2")
            return _Result()

    monkeypatch.setattr(a3_agent, "_build_agent", lambda: _FakeAgent())
    out = a3_agent.run("What next?", "J-US-J2", ADVISOR, correlation_id="corr-1")
    assert out["engine"] == "strands" and out["tools_used"] == ["get_incentives", "get_lifecycle_context"]
    assert any(c["ref"] == "DEALER_Journeys:J-US-J2" for c in out["citations"])
    assert out["answer"].startswith("Per the journey")
    traces = [t for t in fake_ddb.rows("PLAT_AgentTraces") if t.get("tool") == "assistant_agent"]
    assert traces and traces[-1]["correlation_id"] == "corr-1"


def test_route_assistant_agent_and_chat_mode_switch(fake_ddb, monkeypatch):
    monkeypatch.setenv("INTEGRATION_MODE", "local")
    monkeypatch.setenv("RETAIL_OPS_AGENT_RUNTIME_DISABLE", "1")
    code, body = call(api_event("POST", "/assistant/agent", user="U-US-00002", role="dealer_advisor",
                                dealer_ids="D-102", body={"question": "next step?", "journey_id": "J-US-J2"}))
    assert code == 200 and "(agent unavailable)" in body["engine"]
    code, body = call(api_event("POST", "/assistant/chat", user="U-US-00002", role="dealer_advisor",
                                dealer_ids="D-102", body={"question": "next step?", "journey_id": "J-US-J2",
                                                          "mode": "agent"}))
    assert code == 200 and body["tools_used"] == ["assistant_chat"]
    code, _ = call(api_event("POST", "/assistant/agent", user="U-US-00002", role="dealer_advisor",
                             dealer_ids="D-999", body={"question": "x", "journey_id": "J-US-J2"}))
    assert code == 403
    code, _ = call(api_event("POST", "/assistant/agent", user="U-US-00044", role="oem_dm", is_oem=True,
                             body={"question": "x"}))
    assert code == 403                                   # OEM roles do not use the dealer assistant


def test_runtime_entrypoint_routes_agent_payload(fake_ddb, monkeypatch):
    monkeypatch.setenv("INTEGRATION_MODE", "local")
    monkeypatch.delenv("RETAIL_OPS_A3_AGENT_FORCE", raising=False)
    monkeypatch.setattr(a3_agent, "available", lambda: False)
    out = runtime._invoke({"kind": "agent", "agent": "A3", "prompt": "next step?", "journey_id": "J-US-J2",
                           "claims": {"user_id": "U-US-00002", "role": "dealer_advisor",
                                      "dealer_ids": "D-102", "is_oem": "false", "market_id": "us"}})
    assert out["statusCode"] == 200 and out["result"]["tools_used"] == ["assistant_chat"]
    bad = runtime._invoke({"kind": "agent", "agent": "A9", "prompt": "x"})
    assert bad["statusCode"] == 400
    # the classic tool-call payload still routes through the shared handler
    tool = runtime._invoke({"method": "GET", "path": "/health"})
    assert tool["statusCode"] == 200 and tool["result"] == {"ok": True}


def test_runtime_client_is_quiet_when_unconfigured(monkeypatch):
    from app.agentcore import client
    monkeypatch.delenv("RETAIL_OPS_AGENT_RUNTIME_ARN", raising=False)
    monkeypatch.setattr(client, "_CFG", client._CFG.with_name("nope.json"))
    assert client.enabled() is False
    assert client.invoke_agent("A3", "x", journey_id=None, claims=ADVISOR) is None
    monkeypatch.setenv("RETAIL_OPS_AGENT_RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:1:runtime/x")
    assert client.enabled() is True
    assert client.invoke_agent("A3", "x", journey_id=None, claims=ADVISOR) is None   # boto3 stubbed -> None
