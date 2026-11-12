# ruff: noqa: S101
"""Service-side routing to the per-agent runtimes (ADR 0009, design §6.1/§6.4/§16):
the Sales Package, the two Campaign Agent POSTs, the Sales Agent turn and the
agent-owned jobs go to their runtime when one is configured and stay exactly as
before when none is. The loopback contract tests run the real client against
the real dispatcher in-process, so claims, args and payload shapes are checked
end to end."""
from __future__ import annotations

import datetime as dt
import importlib
import io
import json
import os
import re
import threading

import boto3
import pytest
from botocore import exceptions as bx

from app.agentcore import agent as runtime
from app.agentcore import client, registry
from app.services import devserver
from app.services.agents import a3_agent
from app.services.api import handler, job_handler
from app.services.common import ddb, trace
from app.services.journeys import nurture_tick, package, supervisor
from app.services.marketing import analytics
from app.services.oem import campaigns
from tests.app.conftest import _NoAws, api_event, call

ARNS = {code: f"arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/{spec.runtime_name}-abc"
        for code, spec in registry.AGENTS.items()}
CODE_BY_ARN = {arn: code for code, arn in ARNS.items()}

JID = "J-US-J2"                                         # dealer D-102
ADVISOR = {"user": "U-US-00002", "role": "dealer_advisor", "dealer_ids": "D-102"}
MARKETER = {"user": "U-US-00049", "role": "oem_marketing", "is_oem": True}
DM = {"user": "U-US-00044", "role": "oem_dm", "is_oem": True}
FROZEN = "2026-10-20T10:00:00Z"
ISO_SECOND = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

# Values that differ between two runs of the same call (clock, ids) or that
# only the runtime path adds.
_VOLATILE = {"transport", "requested_at", "ran_at", "bus_event", "event_id", "sent_at", "message_id"}

LOST = RuntimeError("connection reset after send")                       # send phase -> lost
UNAVAILABLE = bx.ClientError({"Error": {"Code": "AccessDeniedException", "Message": "x"}}, "InvokeAgentRuntime")


# --- helpers -------------------------------------------------------------------------

def _stable(value):
    if isinstance(value, dict):
        return {k: _stable(v) for k, v in value.items() if k not in _VOLATILE}
    if isinstance(value, list):
        return [_stable(v) for v in value]
    return value


def _json(value):
    return json.loads(json.dumps(value, default=str))


def _snapshot(fake):
    return {name: dict(rows) for name, rows in fake.tables.items()}


def _restore(fake, snap):
    fake.tables = {name: dict(rows) for name, rows in snap.items()}


class _DataPlane:
    """bedrock-agentcore stand-in. `answer(code, payload)` returns the runtime
    envelope or raises; every call is recorded as (code, payload)."""

    def __init__(self, answer) -> None:
        self.answer = answer
        self.calls: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    def invoke_agent_runtime(self, *, agentRuntimeArn, payload, **_kw):
        code = CODE_BY_ARN[agentRuntimeArn]
        body = json.loads(payload)
        with self._lock:
            self.calls.append((code, body))
        out = self.answer(code, body)
        return {"response": io.BytesIO(json.dumps(out, default=str).encode())}


def _loopback(code, payload):
    """The real dispatcher, serving as `code` (planner decision P2)."""
    with registry.acting_as(code):
        return runtime._invoke(payload)


def _answer(status, result):
    return lambda code, payload: {"statusCode": status, "result": result}


def _raise(exc):
    def answer(code, payload):
        raise exc
    return answer


@pytest.fixture
def runtimes(monkeypatch):
    monkeypatch.setenv("RETAIL_OPS_AGENT_RUNTIMES", json.dumps(ARNS))
    monkeypatch.setattr(client, "_CFG", client._CFG.with_name("absent-agentcore-config.json"))


@pytest.fixture
def plane(monkeypatch):
    """Install a data plane: plane(answer) -> _DataPlane."""
    def install(answer) -> _DataPlane:
        dp = _DataPlane(answer)
        monkeypatch.setattr(client, "_data_client", lambda timeout_s: dp)
        return dp
    return install


@pytest.fixture
def frozen_package_clock(monkeypatch):
    monkeypatch.setattr(package, "_now", lambda: FROZEN)


def _package_spy(monkeypatch, *, fail: bool = False) -> list[dict]:
    calls: list[dict] = []
    real = package.build_sales_package

    def spy(journey, *a, **kw):
        calls.append(kw)
        if fail:
            raise AssertionError("build_sales_package must not run in-process here")
        return real(journey, *a, **kw)
    monkeypatch.setattr(package, "build_sales_package", spy)
    return calls


def _post_package(**who):
    return call(api_event("POST", f"/journeys/{JID}/package", **(who or ADVISOR)))


# --- POST /journeys/{id}/package -----------------------------------------------------------

def test_package_without_a_runtime_is_todays_in_process_build(fake_ddb, frozen_package_clock, monkeypatch):
    calls = _package_spy(monkeypatch)
    snap = _snapshot(fake_ddb)
    code, body = _post_package()
    assert code == 200 and calls == [{"actor": "U-US-00002"}]
    assert "agent_transport" not in body and "transport" not in body
    _restore(fake_ddb, snap)
    journey = ddb.get_item("DEALER_Journeys", {"journey_id": JID})
    assert body == _json(package.build_sales_package(journey, actor="U-US-00002"))


def test_package_ok_returns_the_journey_agent_result(fake_ddb, runtimes, plane, monkeypatch):
    calls = _package_spy(monkeypatch, fail=True)
    dp = plane(_answer(200, {"package_id": f"SP-{JID}", "agent_status": {"A1": True}}))
    code, body = _post_package()
    assert code == 200 and calls == []
    assert body == {"package_id": f"SP-{JID}", "agent_status": {"A1": True}, "transport": "agentcore-runtime"}
    (agent, payload), = dp.calls
    assert agent == "A2" and payload["kind"] == "tool" and payload["tool"] == "build_sales_package"
    assert payload["args"] == {"journey_id": JID}
    assert payload["claims"]["user_id"] == "U-US-00002" and payload["claims"]["dealer_ids"] == ["D-102"]


def test_package_lost_is_202_building_with_requested_at_and_no_local_build(fake_ddb, runtimes, plane, monkeypatch):
    calls = _package_spy(monkeypatch, fail=True)
    plane(_raise(LOST))
    resp = handler.lambda_handler(api_event("POST", f"/journeys/{JID}/package", **ADVISOR))
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 202 and calls == []
    assert body["status"] == "building" and body["journey_id"] == JID
    assert body["poll"] == f"/journeys/{JID}/package"
    assert ISO_SECOND.fullmatch(body["requested_at"]) and len(body["requested_at"]) == len(package._now())
    assert body["correlation_id"] == resp["headers"]["X-Correlation-Id"]


@pytest.mark.parametrize(("answer", "code", "body"), [
    (_answer(500, {"error": "KeyError: 'x'"}), 500,
     {"error": "the Journey Agent could not build the package", "agent": "A2"}),
    (_answer(403, {"error": "journey outside caller scope"}), 403,
     {"error": "journey outside caller scope", "transport": "agentcore-runtime"}),
])
def test_package_failed_and_caller_error(fake_ddb, runtimes, plane, monkeypatch, answer, code, body):
    calls = _package_spy(monkeypatch, fail=True)
    plane(answer)
    assert _post_package() == (code, body)
    assert calls == []


def test_package_unavailable_builds_in_process(fake_ddb, runtimes, plane, monkeypatch):
    calls = _package_spy(monkeypatch)
    dp = plane(_raise(UNAVAILABLE))
    code, body = _post_package()
    assert code == 200 and calls == [{"actor": "U-US-00002"}] and len(dp.calls) == 1
    assert set(body["agent_status"]) == {"A1", "A3", "A4"}


def test_disabling_the_journey_agent_builds_the_whole_package_locally(fake_ddb, runtimes, plane, monkeypatch):
    monkeypatch.setenv("RETAIL_OPS_AGENT_RUNTIME_DISABLE", "A2")
    calls = _package_spy(monkeypatch)
    dp = plane(_answer(200, {}))
    code, body = _post_package()
    assert code == 200 and dp.calls == []
    assert calls == [{"actor": "U-US-00002"}]                     # remote=False (the default)
    assert "agent_transport" not in body


# --- build_sales_package(remote=True) -------------------------------------------------------

def test_remote_package_fans_out_concurrently_and_maps_each_status(fake_ddb, monkeypatch):
    journey = ddb.get_item("DEALER_Journeys", {"journey_id": JID})
    claims = handler._claims(api_event("POST", "/x", **ADVISOR))
    barrier = threading.Barrier(4, timeout=5)
    seen: list[dict] = []
    outcomes = {"A1": client.RuntimeResult("lost", 0, {}, "ReadTimeoutError: x"),
                "A3": client.RuntimeResult("ok", 200, {"recommendations": [{"id": 1}], "transport": "agentcore-runtime"}),
                "A4": client.RuntimeResult("unavailable", 0, {}, "ClientError: AccessDenied")}

    def invoke_tool(agent, tool, args, *, claims, correlation_id=None, caller=None, timeout_s=None):
        seen.append({"agent": agent, "tool": tool, "args": args, "claims": claims, "corr": correlation_id,
                     "caller": caller, "timeout_s": timeout_s})
        barrier.wait()                       # all four (three specialists + NBA) must be in flight together
        return outcomes[agent]

    real_nba = supervisor.select_next_best_action

    def nba(journey, **kw):
        barrier.wait()
        return real_nba(journey, **kw)

    local: list[str] = []
    real_invoke = package.invoke_agent

    def local_invoke(agent, journey_id, env_prefix=None):
        local.append(agent)
        return real_invoke(agent, journey_id, env_prefix)

    monkeypatch.setattr(client, "invoke_tool", invoke_tool)
    monkeypatch.setattr(supervisor, "select_next_best_action", nba)
    monkeypatch.setattr(package, "invoke_agent", local_invoke)
    token = trace.CURRENT_CORRELATION_ID.set("corr-pkg-1")
    try:
        pkg = package.build_sales_package(journey, actor="A2", claims=claims, remote=True)
    finally:
        trace.CURRENT_CORRELATION_ID.reset(token)

    assert sorted(s["agent"] for s in seen) == ["A1", "A3", "A4"]
    assert {s["agent"]: s["tool"] for s in seen} == {"A1": "rank_vehicles", "A3": "build_assist", "A4": "build_equity"}
    assert all(s["corr"] == "corr-pkg-1" and s["caller"] == "A2" and s["timeout_s"] == 8 for s in seen)
    assert all(s["args"] == {"journey_id": JID} and s["claims"] is claims for s in seen)
    assert local == ["A4"]                                       # lost A1 is never re-run locally
    assert pkg["agent_status"] == {"A1": False, "A3": True, "A4": True}
    assert pkg["agents"]["A1"] == {"error": "the Discovery Agent did not answer in 8 s"}
    assert pkg["agents"]["A3"]["recommendations"] == [{"id": 1}]
    assert pkg["agent_transport"] == {"A1": "agentcore-runtime", "A3": "agentcore-runtime", "A4": "in-process"}
    assert ddb.get_item("DEALER_SalesPackages", {"package_id": f"SP-{JID}"})["summary"] == pkg["summary"]


def test_remote_package_error_cards_for_failed_and_caller_error(fake_ddb, monkeypatch):
    journey = ddb.get_item("DEALER_Journeys", {"journey_id": JID})
    claims = handler._claims(api_event("POST", "/x", **ADVISOR))
    outcomes = {"A1": client.RuntimeResult("failed", 500, {"error": "boom"}, "boom"),
                "A3": client.RuntimeResult("caller_error", 403, {"error": "outside scope"}, "outside scope"),
                "A4": client.RuntimeResult("not_configured")}
    monkeypatch.setattr(client, "invoke_tool", lambda agent, *a, **kw: outcomes[agent])
    pkg = package.build_sales_package(journey, claims=claims, remote=True)
    assert pkg["agent_status"] == {"A1": False, "A3": False, "A4": True}
    assert pkg["agents"]["A1"] == {"error": "boom"} and pkg["agents"]["A3"] == {"error": "outside scope"}
    assert pkg["agent_transport"]["A4"] == "in-process"
    with pytest.raises(ValueError, match="claims"):
        package.build_sales_package(journey, remote=True)


# --- POST /oem/campaigns/budget-shift (Campaign Agent recommendation) ---------------------------

def _recommend(body=None, **who):
    return call(api_event("POST", "/oem/campaigns/budget-shift", body=body, **(who or MARKETER)))


def test_recommend_without_a_runtime_is_todays_response(fake_ddb):
    scope = handler._claims(api_event("POST", "/x", **MARKETER))
    for body, market in ((None, None), ({"market": "de"}, "de"), ({"market": ""}, "")):
        code, out = _recommend(body)
        assert code == 200, body
        assert out == _json(campaigns.recommend_budget_shift(scope, market_id=market, apply=False,
                                                             actor="U-US-00049"))


def test_recommend_routes_to_the_campaign_agent(fake_ddb, runtimes, plane):
    dp = plane(_answer(200, {"recommended": True, "recommendation_id": "REC-1"}))
    assert _recommend({"apply": True}) == (200, {"recommended": True, "recommendation_id": "REC-1",
                                                 "transport": "agentcore-runtime"})
    assert _recommend({"market": None})[0] == 200 and _recommend({"market": "de"})[0] == 200
    sent = [p for _c, p in dp.calls]
    assert [p["args"] for p in sent] == [{"apply": True}, {"apply": False}, {"market": "de", "apply": False}]
    assert all(c == "A5" and p["tool"] == "recommend_budget_shift" for c, p in dp.calls)
    assert sent[0]["claims"]["scope_node_id"] == "OEM"            # unscoped OEM user: OEM-wide, as in-process


@pytest.mark.parametrize("configured", [False, True])
def test_recommend_rejects_a_bad_market_on_both_paths(fake_ddb, monkeypatch, plane, configured):
    if configured:
        monkeypatch.setenv("RETAIL_OPS_AGENT_RUNTIMES", json.dumps(ARNS))
    dp = plane(_answer(200, {}))
    code, out = _recommend({"market": "US"})
    assert code == 400 and out["field"] == "market" and dp.calls == []


def test_recommend_failed_caller_error_and_fallbacks(fake_ddb, runtimes, plane):
    scope = handler._claims(api_event("POST", "/x", **MARKETER))
    local = _json(campaigns.recommend_budget_shift(scope, market_id=None, apply=False, actor="U-US-00049"))
    plane(_answer(500, {"error": "TypeError: x"}))
    assert _recommend() == (500, {"error": "TypeError: x", "transport": "agentcore-runtime"})
    plane(_answer(403, {"error": "role may not call recommend_budget_shift"}))
    assert _recommend()[0] == 403
    for exc in (LOST, UNAVAILABLE):                              # idempotent: re-run locally
        plane(_raise(exc))
        assert _recommend() == (200, local)


# --- POST /marketing/campaigns/budget-shift/submit ----------------------------------------------

def _submit(body=None, query=None, **who):
    return call(api_event("POST", "/marketing/campaigns/budget-shift/submit", body=body, query=query,
                          **(who or MARKETER)))


def test_submit_without_a_runtime_is_todays_response(fake_ddb):
    snap = _snapshot(fake_ddb)
    code, out = _submit({"note": "spring event"})
    assert code == 201 and out["submitted"] is True
    assert _submit()[0] == 409                                   # already pending, as today
    _restore(fake_ddb, snap)
    scope = {**handler._claims(api_event("POST", "/x", **MARKETER)), "scope_node_id": "OEM"}
    direct = analytics.submit_budget_shift(scope, "U-US-00049", "us", note="spring event")
    assert _stable(out) == _stable(_json(direct))


def test_submit_maps_runtime_answers_to_201_409_and_passes_errors(fake_ddb, runtimes, plane):
    dp = plane(_answer(200, {"submitted": True, "approval_id": "APR-1"}))
    assert _submit({"note": "x"}) == (201, {"submitted": True, "approval_id": "APR-1",
                                            "transport": "agentcore-runtime"})
    assert dp.calls[0][1]["args"] == {"market": "us", "note": "x"}
    assert dp.calls[0][1]["claims"]["scope_node_id"] == "OEM"
    plane(_answer(200, {"submitted": False, "reason": "an approval for this shift is already pending"}))
    assert _submit()[0] == 409
    plane(_answer(500, {"error": "KeyError: 'shift'"}))
    assert _submit() == (500, {"error": "KeyError: 'shift'", "transport": "agentcore-runtime"})
    plane(_answer(403, {"error": "role may not call submit_budget_shift"}))
    assert _submit()[0] == 403


def test_submit_lost_after_the_runtime_wrote_the_approval_reruns_locally_to_409(fake_ddb, runtimes, plane):
    def answer(code, payload):
        _loopback(code, payload)             # the runtime finished and wrote the approval ...
        raise LOST                           # ... but its answer never arrived
    plane(answer)
    code, out = _submit()
    assert code == 409 and "already pending" in out["reason"]
    pending = [a for a in fake_ddb.rows("DEALER_Approvals") if a["approval_id"] == out["approval_id"]]
    assert len(pending) == 1 and pending[0]["status"] == "pending"


def test_submit_rejects_a_bad_note_or_market(fake_ddb, runtimes, plane):
    dp = plane(_answer(200, {"submitted": True}))
    code, out = _submit({"note": "x" * 501})
    assert code == 400 and out["field"] == "note"
    code, out = _submit({"market": "US"})
    assert code == 400 and out["field"] == "market"
    assert dp.calls == []


def test_submit_market_from_the_query_string_reaches_the_runtime(fake_ddb, runtimes, plane):
    snap = _snapshot(fake_ddb)
    dp = plane(_loopback)
    code, out = _submit(query={"market": "de"})
    assert dp.calls[0][1]["args"] == {"market": "de"}
    _restore(fake_ddb, snap)
    plane(_raise(UNAVAILABLE))               # same request, in-process
    local_code, local = _submit(query={"market": "de"})
    assert (code, _stable(out)) == (local_code, _stable(local))
    assert out["recommendation"]["market"] == "de"


# --- POST /assistant/agent (Sales Agent turn; already routed, kept working) ------------------------

def _agent_spy(monkeypatch) -> list[str]:
    calls: list[str] = []

    def run(question, journey_id, claims, **kw):
        calls.append(question)
        return {"answer": "local", "engine": "test"}
    monkeypatch.setattr(a3_agent, "run", run)
    return calls


def _turn():
    return call(api_event("POST", "/assistant/agent", body={"question": "What should I lead with?"}, **ADVISOR))


@pytest.mark.parametrize(("status", "error"), [(500, "x"), (403, "journey outside caller scope")])
def test_sales_agent_error_body_is_returned_without_a_rerun(fake_ddb, runtimes, plane, monkeypatch, status, error):
    calls = _agent_spy(monkeypatch)
    plane(_answer(status, {"error": error}))
    assert _turn() == (200, {"error": error, "transport": "agentcore-runtime"})
    assert calls == []


@pytest.mark.parametrize("exc", [LOST, UNAVAILABLE])
def test_sales_agent_runs_in_process_when_the_runtime_did_not_answer(fake_ddb, runtimes, plane, monkeypatch, exc):
    calls = _agent_spy(monkeypatch)
    plane(_raise(exc))
    assert _turn() == (200, {"answer": "local", "engine": "test"})
    assert calls == ["What should I lead with?"]


# --- jobs: run_job, the job Lambda and POST /jobs/{job} -------------------------------------------

def _job(job, body=None, **who):
    return call(api_event("POST", f"/jobs/{job}", body=body or {}, **(who or DM)))


def test_job_payload_keeps_only_the_runner_keys():
    assert job_handler.job_payload("equity-monitor", {"job": "equity-monitor", "market": "us", "apply": False,
                                                      "local": True}) == {"market": "us", "limit": None}
    assert job_handler.job_payload("shop-load", {"dealer_id": "D-101", "apply": False}) == \
        {"market": "us", "limit": None, "dealer_id": "D-101"}
    with pytest.raises(registry.ArgError) as e:
        job_handler.job_payload("equity-monitor", {"limit": "5"})
    assert e.value.field == "limit"


def test_run_job_delegates_when_configured_and_runs_in_process_when_not(fake_ddb, runtimes, plane, monkeypatch):
    dp = plane(_answer(200, {"market": "us", "flagged": 3}))
    out = job_handler.run_job("equity-monitor", {"market": "us", "limit": 2}, apply=False)
    assert out == job_handler.JobOutcome("ok", 200, {"market": "us", "flagged": 3, "transport": "agentcore-runtime"})
    (agent, payload), = dp.calls
    assert agent == "A4" and payload["kind"] == "job" and payload["job"] == "equity-monitor"
    assert payload["payload"] == {"market": "us", "limit": 2} and payload["apply"] is False
    assert payload["correlation_id"].startswith("corr-")
    out = job_handler.run_job("equity-monitor", {"market": "us", "limit": 2, "local": True}, apply=False)
    assert out.status == "local" and len(dp.calls) == 1              # local: true stays in-process
    monkeypatch.delenv("RETAIL_OPS_AGENT_RUNTIMES")
    out = job_handler.run_job("stall-sweep", {"market": "us", "limit": 3}, apply=False)
    assert out.status == "local" and out.code == 200 and out.body["applied"] is False
    with pytest.raises(ValueError, match="unknown job"):
        job_handler.run_job("nope", {})


def test_job_lambda_lost_returns_unknown_after_send_and_logs_one_line(fake_ddb, runtimes, plane, capsys):
    plane(_raise(LOST))
    out = job_handler.lambda_handler({"job": "equity-monitor", "market": "us"})
    assert out["ok"] is False and out["status"] == "unknown_after_send"
    assert out["job"] == "equity-monitor" and out["agent"] == "A4" and out["correlation_id"].startswith("corr-")
    lines = [json.loads(ln) for ln in capsys.readouterr().out.splitlines() if "runtime_job_unknown" in ln]
    assert lines == [{"event": "runtime_job_unknown", "level": "ERROR", "job": "equity-monitor", "agent": "A4",
                      "correlation_id": out["correlation_id"]}]
    again = job_handler.run_job_dict("equity-monitor", {"market": "us"})
    assert {k: v for k, v in again.items() if k != "correlation_id"} == \
        {k: v for k, v in out.items() if k != "correlation_id"}


@pytest.mark.parametrize("answer", [_answer(500, {"error": "KeyError: 'x'"}), _answer(400, {"error": "limit bad"})])
def test_job_lambda_raises_when_the_runtime_answered_with_an_error(fake_ddb, runtimes, plane, answer):
    plane(answer)
    with pytest.raises(job_handler.RuntimeJobError, match="equity-monitor"):
        job_handler.lambda_handler({"job": "equity-monitor", "market": "us"})
    with pytest.raises(job_handler.RuntimeJobError):
        job_handler.run_job_dict("equity-monitor", {"market": "us"})


def test_on_demand_job_status_mapping(fake_ddb, runtimes, plane):
    plane(_raise(LOST))
    code, body = _job("equity-monitor")
    assert code == 202 and body["status"] == "running" and body["agent"] == "A4" and body["job"] == "equity-monitor"
    assert body["correlation_id"].startswith("corr-")
    plane(_answer(500, {"error": "KeyError: 'x'"}))
    assert _job("equity-monitor") == (500, {"error": "KeyError: 'x'", "agent": "A4", "job": "equity-monitor"})
    plane(_answer(404, {"error": "unknown job 'x'"}))
    assert _job("equity-monitor") == (404, {"error": "unknown job 'x'", "transport": "agentcore-runtime"})
    plane(_answer(200, {"applied": False}))
    assert _job("equity-monitor", {"apply": False}) == (200, {"applied": False, "transport": "agentcore-runtime"})
    assert _job("nope")[0] == 404


def test_on_demand_job_without_a_runtime_is_todays_response(fake_ddb):
    code, body = _job("stall-sweep", {"apply": False, "limit": 3})
    expected = job_handler.JOBS["stall-sweep"]({"market": "us", "limit": 3}, False)
    assert code == 200 and _stable(body) == _stable(_json(expected))


def test_platform_jobs_never_call_a_runtime(fake_ddb, runtimes, plane):
    dp = plane(_answer(200, {}))
    code, body = _job("district-cycle", {"apply": False, "limit": 1})
    assert code == 200 and "transport" not in body and dp.calls == []
    assert job_handler.run_job("autonomy-promotion", {"market": "us"}, apply=False).status == "local"
    assert dp.calls == []


# --- devserver guard ----------------------------------------------------------------------------

def test_local_runtime_guard_disables_runtimes_for_local_data():
    env = {"RETAIL_OPS_LOCAL_DATA": "1", "RETAIL_OPS_AGENT_RUNTIMES": "{}"}
    devserver._local_runtime_guard(env)
    assert env == {"RETAIL_OPS_LOCAL_DATA": "1", "RETAIL_OPS_AGENT_RUNTIMES": "{}",
                   "RETAIL_OPS_AGENT_RUNTIME_DISABLE": "1"}
    for env in ({}, {"RETAIL_OPS_LOCAL_DATA": "0"}, {"RETAIL_OPS_LOCAL_DATA": "true"}):
        devserver._local_runtime_guard(env)
        assert "RETAIL_OPS_AGENT_RUNTIME_DISABLE" not in env


def test_devserver_module_sets_the_disable_switch_in_local_data_mode(monkeypatch):
    monkeypatch.setenv("RETAIL_OPS_LOCAL_DATA", "1")
    monkeypatch.setenv("RETAIL_OPS_AGENT_RUNTIME_DISABLE", "")        # restored by monkeypatch afterwards
    try:
        importlib.reload(devserver)
        assert os.environ["RETAIL_OPS_AGENT_RUNTIME_DISABLE"] == "1"
        monkeypatch.setenv("RETAIL_OPS_AGENT_RUNTIMES", json.dumps(ARNS))
        assert client.enabled("A2") is False and client.enabled("A3") is False
    finally:
        monkeypatch.setenv("RETAIL_OPS_LOCAL_DATA", "0")
        importlib.reload(devserver)


# --- loopback contract tests: the real client against the real dispatcher -------------------------

def test_loopback_scheduled_equity_monitor_matches_in_process(fake_ddb, runtimes, plane, monkeypatch):
    snap = _snapshot(fake_ddb)
    monkeypatch.delenv("RETAIL_OPS_AGENT_RUNTIMES")
    local = job_handler.lambda_handler({"job": "equity-monitor", "market": "us"})
    _restore(fake_ddb, snap)
    monkeypatch.setenv("RETAIL_OPS_AGENT_RUNTIMES", json.dumps(ARNS))
    dp = plane(_loopback)
    remote = job_handler.lambda_handler({"job": "equity-monitor", "market": "us"})
    assert [c for c, _p in dp.calls] == ["A4"]
    assert remote["ok"] is True and local["ok"] is True
    assert remote["summary"]["transport"] == "agentcore-runtime"
    assert set(remote["summary"]) - {"transport"} == set(local["summary"])
    assert _stable(remote) == _stable(_json(local))


def test_loopback_on_demand_shop_load_with_dealer_id(fake_ddb, runtimes, plane):
    dp = plane(_loopback)
    code, remote = _job("shop-load", {"apply": False, "dealer_id": "D-101"})
    assert code == 200 and remote["transport"] == "agentcore-runtime"
    (agent, payload), = dp.calls
    assert agent == "A7" and payload["payload"] == {"market": "us", "limit": None, "dealer_id": "D-101"}
    plane(_raise(UNAVAILABLE))
    local_code, local = _job("shop-load", {"apply": False, "dealer_id": "D-101"})
    assert (code, _stable(remote)) == (local_code, _stable(local))


def test_loopback_string_limit_is_400_naming_limit_on_both_paths(fake_ddb, monkeypatch, plane):
    dp = plane(_loopback)
    assert _job("equity-monitor", {"limit": "5"}) == (400, {"error": "limit must be an integer", "field": "limit"})
    monkeypatch.setenv("RETAIL_OPS_AGENT_RUNTIMES", json.dumps(ARNS))
    assert _job("equity-monitor", {"limit": "5"}) == (400, {"error": "limit must be an integer", "field": "limit"})
    assert dp.calls == []                                     # rejected before choosing a path
    out = _loopback("A4", {"kind": "job", "agent": "A4", "job": "equity-monitor", "payload": {"limit": "5"}})
    assert out["statusCode"] == 400 and out["result"]["field"] == "limit"   # and the runtime refuses it too


def test_loopback_campaign_agent_posts_match_in_process(fake_ddb, runtimes, plane):
    snap = _snapshot(fake_ddb)
    dp = plane(_loopback)
    rec = _recommend({"market": "us"})
    sub = _submit({"note": "Move spend to the spring event."})
    assert [(c, p["tool"]) for c, p in dp.calls] == [("A5", "recommend_budget_shift"), ("A5", "submit_budget_shift")]
    assert rec[0] == 200 and sub[0] == 201
    _restore(fake_ddb, snap)
    plane(_raise(UNAVAILABLE))
    local_rec = _recommend({"market": "us"})
    assert (rec[0], _stable(rec[1])) == (local_rec[0], _stable(local_rec[1]))
    local_sub = _submit({"note": "Move spend to the spring event."})
    assert (sub[0], _stable(sub[1])) == (local_sub[0], _stable(local_sub[1]))


def test_loopback_sales_package_matches_in_process(fake_ddb, runtimes, plane, frozen_package_clock, monkeypatch):
    snap = _snapshot(fake_ddb)
    dp = plane(_loopback)
    code, remote = _post_package()
    assert code == 200
    assert sorted(c for c, _p in dp.calls) == ["A1", "A2", "A3", "A4"]
    assert all(p["caller"] == "A2" for c, p in dp.calls if c != "A2")
    assert remote["agent_transport"] == {"A1": "agentcore-runtime", "A3": "agentcore-runtime",
                                         "A4": "agentcore-runtime"}
    assert all(remote["agent_status"].values())
    _restore(fake_ddb, snap)
    monkeypatch.delenv("RETAIL_OPS_AGENT_RUNTIMES")
    local_code, local = _post_package()
    remote.pop("agent_transport")
    assert (code, _stable(remote)) == (local_code, _stable(local))

    # build_sales_package(remote=True) called directly loops back the same way
    _restore(fake_ddb, snap)
    monkeypatch.setenv("RETAIL_OPS_AGENT_RUNTIMES", json.dumps(ARNS))
    journey = ddb.get_item("DEALER_Journeys", {"journey_id": JID})
    claims = handler._claims(api_event("POST", "/x", **ADVISOR))
    direct = package.build_sales_package(journey, actor="U-US-00002", claims=claims, remote=True)
    direct.pop("agent_transport")
    assert _stable(_json(direct)) == _stable(local)


def test_loopback_nurture_tick_sends_the_same_messages(fake_ddb, runtimes, plane, monkeypatch):
    rows = [r for r in fake_ddb.rows("PLAT_ConfigAutonomy")
            if r["action_id"] == nurture_tick.TICK_ACTION_ID and r["market_id"] == "us" and r["scope"] == "tenant"]
    row = dict(max(rows, key=lambda r: r["version"]))
    row.update(version=row["version"] + 10, level="act_and_notify", is_active=True)        # an execute level
    fake_ddb.seed("PLAT_ConfigAutonomy", [row])
    journeys = fake_ddb.rows("DEALER_Journeys")
    fake_ddb.seed("DEALER_Journeys", [{**j, "contact_email": f"{j['journey_id'].lower()}@example.test",
                                       "contact_phone": "+15550100"} for j in journeys])
    monkeypatch.setattr(nurture_tick, "_now", lambda: dt.datetime(2026, 10, 20, 17, 0, tzinfo=dt.UTC))
    sends: list[tuple] = []

    class _Outbound:
        def __init__(self, service: str) -> None:
            self.service = service

        def send_email(self, **kw):
            sends.append(("ses", kw["Destination"]["ToAddresses"][0], kw["Message"]["Subject"]["Data"],
                          kw["Message"]["Body"]["Text"]["Data"]))
            return {"MessageId": "m-1"}

        def publish(self, **kw):
            sends.append(("sns", kw["PhoneNumber"], kw["Message"]))
            return {"MessageId": "m-2"}

    monkeypatch.setattr(boto3, "client", lambda service, *a, **k: _Outbound(service)
                        if service in ("ses", "sns") else _NoAws(service))

    def outbound():
        return sorted((m["customer_id"], m["channel"], m.get("subject"), m["body"], m["status"])
                      for m in fake_ddb.rows("CUST_Messages") if m.get("source_ref") == "platform.BasicOutreachProvider")

    snap = _snapshot(fake_ddb)
    before = outbound()
    monkeypatch.delenv("RETAIL_OPS_AGENT_RUNTIMES")
    local = job_handler.run_job("nurture-tick", {"market": "us", "limit": 25}, apply=True)
    local_sends, local_messages = sorted(sends), outbound()
    assert local.status == "local" and local.body["sent"] > 0 and local_sends

    _restore(fake_ddb, snap)
    sends.clear()
    monkeypatch.setenv("RETAIL_OPS_AGENT_RUNTIMES", json.dumps(ARNS))
    dp = plane(_loopback)
    remote = job_handler.run_job("nurture-tick", {"market": "us", "limit": 25}, apply=True)
    assert remote.status == "ok" and [c for c, _p in dp.calls] == ["A2"]
    assert sorted(sends) == local_sends                           # same messages, same recipients
    assert outbound() == local_messages and outbound() != before
    assert _stable(remote.body) == _stable(_json(local.body))
