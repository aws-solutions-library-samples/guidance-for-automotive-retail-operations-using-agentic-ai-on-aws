# ruff: noqa: S101
"""AgentCore runtime dispatcher (ADR 0009, design §6.2): payload kinds, validation
order, scope checks, spans and the HTTP contract."""
from __future__ import annotations

import http.client
import json
import logging
import threading
from http.server import ThreadingHTTPServer

import pytest

from app.agentcore import agent as runtime
from app.agentcore import registry
from app.services.agents import a3_agent
from app.services.common import ddb, trace
from app.services.oem import campaigns

ADVISOR = {"user_id": "U-US-00002", "role": "dealer_advisor", "groups": ["dealer_advisor"], "extra_screens": [],
           "dealer_ids": ["D-102"], "is_oem": False, "market_id": "us", "scope_node_id": "D-102",
           "customer_id": ""}


def _oem(node: str, *, user: str = "U-US-00044", role: str = "oem_dm", market: str = "us") -> dict:
    return {"user_id": user, "role": role, "groups": [role], "extra_screens": [], "dealer_ids": [],
            "is_oem": True, "market_id": market, "scope_node_id": node, "customer_id": ""}


def _tool(agent: str, tool: str, args: dict | None = None, claims: dict | None = ADVISOR, **extra) -> dict:
    payload = {"kind": "tool", "agent": agent, "tool": tool, "args": args or {}, **extra}
    if claims is not None:
        payload["claims"] = claims
    return runtime._invoke(payload)


def _spans(fake_ddb, prefix: str = "runtime.") -> list[dict]:
    return [t for t in fake_ddb.rows("PLAT_AgentTraces") if str(t.get("tool", "")).startswith(prefix)]


# --- kind: tool -------------------------------------------------------------------

def test_tool_call_runs_the_adapter_and_writes_one_span(fake_ddb, capsys):
    out = _tool("A1", "rank_vehicles", {"journey_id": "J-US-J2"}, correlation_id="corr-dispatch-1", caller="A2")
    assert out["statusCode"] == 200 and out["result"]["journey_id"] == "J-US-J2" and out["result"]["matches"]
    assert out["agent"] == "A1" and out["runtime"] == "retail_ops_dev_a1_discovery"
    assert out["transport"] == "agentcore-runtime" and out["version"] == "local"      # no BUILD_INFO.json
    spans = _spans(fake_ddb)
    assert len(spans) == 1
    span = spans[0]
    assert span["tool"] == "runtime.rank_vehicles" and span["agent_id"] == "A1" and span["outcome"] == "ok"
    assert span["correlation_id"] == "corr-dispatch-1" and span["transport"] == "agentcore-runtime"
    assert span["caller"] == "A2" and span["journey_id"] == "J-US-J2"
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line == {"kind": "tool", "agent": "A1", "tool": "rank_vehicles", "status": 200,
                    "latency_ms": line["latency_ms"], "correlation_id": "corr-dispatch-1", "caller": "A2"}
    assert trace.CURRENT_TRANSPORT.get() is None and trace.CURRENT_CORRELATION_ID.get() is None


def test_tool_result_matches_the_in_process_call(fake_ddb):
    from app.services.agents import equity
    out = _tool("A4", "build_equity", {"journey_id": "J-US-J2"})
    assert out["statusCode"] == 200 and out["result"] == equity.build_equity("J-US-J2")


@pytest.mark.parametrize("payload, code, needle", [
    ({"kind": "tool", "agent": "A9", "tool": "x"}, 400, "unknown agent"),
    ({"kind": "agent", "agent": "A9"}, 400, "unknown agent"),                 # agent checked before claims
    ({"kind": "tool", "agent": "A1", "tool": "build_equity", "claims": ADVISOR}, 404, "unknown tool"),
    ({"kind": "job", "agent": "A5", "job": "district-cycle"}, 404, "unknown job"),
    ({"kind": "job", "agent": "A6", "job": "district-cycle"}, 400, "unknown agent"),
    ({"kind": "agent", "agent": "A1", "prompt": "x", "claims": ADVISOR}, 400, "no reasoning engine"),
    ({"kind": "explode", "agent": "A1"}, 400, "kind is required"),
    ({"agent": "A1"}, 400, "kind is required"),
])
def test_unknown_names_and_kinds_are_refused(fake_ddb, payload, code, needle):
    out = runtime._invoke(payload)
    assert out["statusCode"] == code and needle in out["result"]["error"]
    assert _spans(fake_ddb) == []


def test_no_reasoning_engine_message_uses_the_display_name():
    out = runtime._invoke({"kind": "agent", "agent": "A1", "prompt": "x", "claims": ADVISOR})
    assert out["result"]["error"] == "the Discovery Agent has no reasoning engine yet (engine: tools)"
    assert runtime._invoke({"kind": "agent", "agent": "A9"})["result"]["known"] == sorted(registry.AGENTS)


@pytest.mark.parametrize("claims, field", [
    (None, "claims.user_id"),
    ({k: v for k, v in ADVISOR.items() if k != "user_id"}, "claims.user_id"),
    ({k: v for k, v in ADVISOR.items() if k != "role"}, "claims.role"),
    ({**ADVISOR, "dealer_ids": ["D 102"]}, "claims.dealer_ids"),
])
def test_claims_are_required_and_never_defaulted(fake_ddb, claims, field):
    out = _tool("A1", "rank_vehicles", {"journey_id": "J-US-J2"}, claims=claims)
    assert out["statusCode"] == 400 and out["result"]["field"] == field
    assert _spans(fake_ddb) == []


@pytest.mark.parametrize("args, field", [
    ({"journey_id": "J-US-J2", "limit": 3}, "limit"),
    ({}, "journey_id"),
    ({"journey_id": "J US J2"}, "journey_id"),
])
def test_arguments_are_validated(fake_ddb, args, field):
    out = _tool("A1", "rank_vehicles", args)
    assert out["statusCode"] == 400 and out["result"]["field"] == field


def test_journey_scope_is_checked_before_the_adapter(fake_ddb):
    out = _tool("A3", "build_assist", {"journey_id": "J-US-J2"},
                claims={**ADVISOR, "dealer_ids": ["D-999"], "scope_node_id": "D-999"})
    assert out["statusCode"] == 403 and "outside caller scope" in out["result"]["error"]
    out = _tool("A3", "build_assist", {"journey_id": "J-US-NOPE"})
    assert out["statusCode"] == 404
    customer = {"user_id": "C-US-J2", "role": "customer", "customer_id": "C-US-J2", "dealer_ids": []}
    assert _tool("A3", "build_assist", {"journey_id": "J-US-J2"}, claims=customer)["statusCode"] == 403
    assert _spans(fake_ddb) == []


def test_adapter_failures_are_mapped_without_a_traceback(fake_ddb, monkeypatch, caplog):
    from app.agentcore.tools import a1
    from app.services.agents import discovery

    def _boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(a1, "rank_vehicles", _boom)
    with caplog.at_level(logging.ERROR, logger="retail_ops.agent_runtime"):
        out = _tool("A1", "rank_vehicles", {"journey_id": "J-US-J2"}, correlation_id="corr-boom")
    assert out["statusCode"] == 500 and out["result"] == {"error": "RuntimeError: boom"}
    assert "Traceback" not in json.dumps(out)
    rec = [r for r in caplog.records if "corr-boom" in r.getMessage()]
    assert rec and rec[0].exc_info is not None and "U-US-00002" not in rec[0].getMessage()
    assert [s["outcome"] for s in _spans(fake_ddb)] == ["error"]

    def _missing(*_a, **_k):
        raise discovery.NotFound("journey gone")

    monkeypatch.setattr(a1, "rank_vehicles", _missing)
    assert _tool("A1", "rank_vehicles", {"journey_id": "J-US-J2"})["statusCode"] == 404


def test_a_malformed_correlation_id_is_replaced_not_rejected(fake_ddb):
    out = _tool("A1", "rank_vehicles", {"journey_id": "J-US-J2"}, correlation_id="not a valid id!")
    assert out["statusCode"] == 200
    corr = _spans(fake_ddb)[0]["correlation_id"]
    assert corr != "not a valid id!" and corr.startswith("corr-")


# --- runtime identity -------------------------------------------------------------

def test_a_runtime_serves_only_its_own_agent(fake_ddb, monkeypatch):
    with registry.acting_as("A1"):
        out = _tool("A4", "build_equity", {"journey_id": "J-US-J2"})
        assert out["statusCode"] == 400 and out["result"]["error"] == "wrong agent: this runtime serves A1"
        assert _tool("A1", "rank_vehicles", {"journey_id": "J-US-J2"})["statusCode"] == 200
    monkeypatch.setenv("RETAIL_OPS_AGENT_ID", "A4")
    assert _tool("A1", "rank_vehicles", {"journey_id": "J-US-J2"})["statusCode"] == 400
    assert _tool("a4", "build_equity", {"journey_id": "J-US-J2"})["statusCode"] == 200


def test_legacy_router_payload_only_without_an_agent_identity(fake_ddb, monkeypatch):
    assert runtime._invoke({"method": "GET", "path": "/health"})["result"] == {"ok": True}
    with registry.acting_as("A3"):
        out = runtime._invoke({"method": "GET", "path": "/health"})
    assert out["statusCode"] == 400 and out["result"]["error"] == "router payloads are retired (ADR 0009)"
    monkeypatch.setenv("RETAIL_OPS_AGENT_ID", "A1")
    assert runtime._invoke({"method": "GET", "path": "/health"})["statusCode"] == 400


def test_ping_reports_identity_and_touches_no_table(monkeypatch):
    touched: list[int] = []

    def _no_table():
        touched.append(1)
        raise RuntimeError("ping must not touch DynamoDB")

    monkeypatch.setattr(ddb, "_client", _no_table)
    out = runtime._invoke({"kind": "ping"})
    assert out["statusCode"] == 200 and out["result"] == {"agent": None, "runtime": None, "version": "local"}
    with registry.acting_as("A7"):
        out = runtime._invoke({"kind": "ping"})
    assert out["result"] == {"agent": "A7", "runtime": "retail_ops_dev_a7_service", "version": "local"}
    assert out["agent"] == "A7" and touched == []


# --- kind: agent ------------------------------------------------------------------

def test_agent_turn_writes_only_the_agents_own_trace_row(fake_ddb, monkeypatch):
    monkeypatch.setenv("INTEGRATION_MODE", "aws")
    monkeypatch.setattr(a3_agent, "available", lambda: True)

    class _Result:
        stop_reason, metrics = "end_turn", None

        def __str__(self):
            return "Book the test drive next."

    monkeypatch.setattr(a3_agent, "_build_agent", lambda: (lambda prompt: _Result()))
    out = runtime._invoke({"kind": "agent", "agent": "A3", "prompt": "next?", "journey_id": "J-US-J2",
                           "claims": ADVISOR, "correlation_id": "corr-turn"})
    assert out["statusCode"] == 200 and out["result"]["engine"] == "strands"
    rows = [t for t in fake_ddb.rows("PLAT_AgentTraces") if t.get("correlation_id") == "corr-turn"]
    assert [r["tool"] for r in rows] == ["assistant_agent"] and rows[0]["transport"] == "agentcore-runtime"


@pytest.mark.parametrize("payload, code", [
    ({"prompt": ""}, 400),
    ({"prompt": "x", "journey_id": "J US"}, 400),
    ({"prompt": "x", "journey_id": "J-US-NOPE"}, 404),
    ({"prompt": "x", "journey_id": "J-US-J2", "claims": {**ADVISOR, "dealer_ids": ["D-999"]}}, 403),
])
def test_agent_turn_validation_and_errors(fake_ddb, monkeypatch, payload, code):
    monkeypatch.setenv("INTEGRATION_MODE", "local")
    out = runtime._invoke({"kind": "agent", "agent": "A3", "claims": ADVISOR, **payload})
    assert out["statusCode"] == code


# --- kind: job --------------------------------------------------------------------

def test_job_runs_with_its_declared_payload_and_one_span(fake_ddb):
    out = runtime._invoke({"kind": "job", "agent": "A1", "job": "rescore-profiles",
                           "payload": {"market": "us", "limit": 2}, "apply": False, "correlation_id": "corr-job"})
    assert out["statusCode"] == 200 and out["result"]["applied"] is False
    spans = _spans(fake_ddb)
    assert [s["tool"] for s in spans] == ["runtime.job.rescore-profiles"]
    assert spans[0]["transport"] == "agentcore-runtime" and spans[0]["correlation_id"] == "corr-job"


def test_job_passthrough_reaches_the_runner(fake_ddb, monkeypatch):
    from app.services.service import shop_load
    seen: list[dict] = []
    monkeypatch.setattr(shop_load, "run_shop_load", lambda **kw: seen.append(kw) or {"ok": True})
    out = runtime._invoke({"kind": "job", "agent": "A7", "job": "shop-load", "payload": {"dealer_id": "D-101"}})
    assert out["statusCode"] == 200
    assert seen == [{"market": "us", "apply": True, "limit": None, "dealer_id": "D-101"}]   # apply defaults to true


@pytest.mark.parametrize("extra, field", [
    ({"payload": {"limit": "5"}}, "limit"),
    ({"payload": {"limit": True}}, "limit"),
    ({"payload": {"market": "US"}}, "market"),
    ({"payload": {"job": "equity-monitor"}}, "job"),
    ({"payload": {"dealer_id": "D-101"}}, "dealer_id"),
    ({"apply": "yes"}, "apply"),
])
def test_job_payload_is_validated_on_receipt(fake_ddb, extra, field):
    out = runtime._invoke({"kind": "job", "agent": "A4", "job": "equity-monitor", **extra})
    assert out["statusCode"] == 400 and out["result"]["field"] == field
    assert _spans(fake_ddb) == []


# --- A5 scope parity (review H2) -----------------------------------------------------

def test_district_scope_is_kept_for_recommend_budget_shift(fake_ddb):
    for node in ("DIST-08", "DIST-07"):
        claims = _oem(node)
        out = _tool("A5", "recommend_budget_shift", {}, claims=claims)
        local = campaigns.recommend_budget_shift(registry.normalize_claims(claims), market_id=None, apply=False,
                                                 actor=claims["user_id"])
        assert out["statusCode"] == 200 and out["result"] == local, node
    assert _tool("A5", "recommend_budget_shift", {}, claims=_oem("DIST-08"))["result"]["recommended"] is False
    assert _tool("A5", "recommend_budget_shift", {}, claims=_oem("OEM"))["result"]["recommended"] is True


def test_submit_budget_shift_writes_the_callers_district(fake_ddb):
    out = _tool("A5", "submit_budget_shift", {"note": "Move spend to the spring event."}, claims=_oem("DIST-07"))
    assert out["statusCode"] == 200 and out["result"]["submitted"] is True
    row = next(a for a in fake_ddb.rows("DEALER_Approvals") if a["approval_id"] == out["result"]["approval_id"])
    assert row["scope"]["node"] == "DIST-07" and row["note"] == "Move spend to the spring event."
    assert row["requested_by"] == "U-US-00044"


def test_oem_tools_need_a_scope_node_and_an_allowed_role(fake_ddb):
    out = _tool("A5", "recommend_budget_shift", {}, claims=_oem(""))
    assert out["statusCode"] == 400 and out["result"]["field"] == "claims.scope_node_id"
    assert _tool("A5", "recommend_budget_shift", {}, claims=_oem("OEM", role="oem_ci_planning"))["statusCode"] == 200
    assert _tool("A5", "submit_budget_shift", {}, claims=_oem("OEM", role="oem_ci_planning"))["statusCode"] == 403
    assert _tool("A5", "recommend_budget_shift", {}, claims=ADVISOR)["statusCode"] == 403
    assert _tool("A5", "recommend_budget_shift", {"market": "US"}, claims=_oem("OEM"))["statusCode"] == 400


def test_market_claim_reaches_the_campaign_agent(fake_ddb):
    out = _tool("A5", "recommend_budget_shift", {}, claims=_oem("OEM", user="U-DE-00044", market="de"))
    assert out["statusCode"] == 200 and out["result"]["market"] == "de"
    assert "CMP-DE" in out["result"]["recommendation_id"]


# --- HTTP contract -----------------------------------------------------------------

@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), runtime._Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _post(port: int, body: bytes, path: str = "/invocations") -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read() or b"{}")
    finally:
        conn.close()


def test_http_contract(server):
    status, body = _post(server, json.dumps({"kind": "ping"}).encode())
    assert status == 200 and body["statusCode"] == 200 and body["result"]["version"] == "local"
    # Oversized: declare the length but send no body, so the early 413 is never
    # raced by a reset from unread request bytes.
    conn = http.client.HTTPConnection("127.0.0.1", server, timeout=10)
    try:
        conn.putrequest("POST", "/invocations")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(registry.MAX_BODY_BYTES + 1))
        conn.endheaders()
        resp = conn.getresponse()
        assert resp.status == 413 and resp.getheader("Connection") == "close"
        assert "over" in json.loads(resp.read())["error"]
    finally:
        conn.close()
    status, body = _post(server, b"{not json")
    assert status == 400 and "not valid JSON" in body["error"]
    status, body = _post(server, json.dumps(["a list"]).encode())
    assert status == 200 and body["statusCode"] == 400
    status, _ = _post(server, b"{}", path="/elsewhere")
    assert status == 404
    conn = http.client.HTTPConnection("127.0.0.1", server, timeout=10)
    conn.request("GET", "/ping")
    assert json.loads(conn.getresponse().read()) == {"status": "Healthy"}
    conn.close()
