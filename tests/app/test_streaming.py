"""Streaming floor path (spec R2): platform/streaming.py local mode, the
objection agent's streaming twin, and the SSE Function-URL handler."""
from __future__ import annotations

import json

import pytest

from app.services.agents import objection
from app.services.api import stream_handler
from app.services.platform import streaming


def _collect() -> tuple[list[str], object]:
    tokens: list[str] = []
    return tokens, tokens.append


def test_local_mode_emits_one_token_with_an_honest_engine(monkeypatch):
    monkeypatch.delenv("INTEGRATION_MODE", raising=False)
    tokens, on_token = _collect()
    out = streaming.stream_ground("Why this trim?", ["Trim LX has the tow package"], [],
                                  persona="an advisor assistant", fallback="Deterministic answer.",
                                  on_token=on_token)
    assert tokens == ["Deterministic answer."]          # exactly one token
    assert out["engine"] == "deterministic" and out["text"] == "Deterministic answer."
    assert out["model"] is None and out["guardrail"] == {"applied": False, "intervened": False}


def test_stream_ground_streams_deltas_in_aws_mode(monkeypatch):
    import boto3

    class _Rt:
        def converse_stream(self, **kw):
            assert kw["modelId"] and kw["messages"]
            return {"stream": [
                {"contentBlockDelta": {"delta": {"text": "Let's "}}},
                {"contentBlockDelta": {"delta": {"text": "look at the numbers."}}},
                {"messageStop": {"stopReason": "end_turn"}},
            ]}

    monkeypatch.setenv("INTEGRATION_MODE", "aws")
    monkeypatch.setattr(boto3, "client", lambda *a, **k: _Rt())
    tokens, on_token = _collect()
    out = streaming.stream_ground("q", ["a fact"], [], persona="p", fallback="fb", on_token=on_token)
    assert tokens == ["Let's ", "look at the numbers."]
    assert out["text"] == "Let's look at the numbers." and out["engine"] == "bedrock"


def test_classify_objection_stream_falls_back_offline(fake_ddb):
    """Offline (conftest blocks boto3): classification stays deterministic and
    the suggested response is the curated phrasing, emitted as one token."""
    tokens, on_token = _collect()
    out = objection.classify_objection_stream("J-US-J1", "That price is just too expensive", on_token)
    assert out["objection_detected"] is True and out["objection_type"] == "price_too_high"
    assert out["generated_by"] == "fallback" and out["model"] is None
    assert tokens == [out["suggested_response"]]
    assert out["suggested_response"] == out["grounded_on"]
    assert out["engine"].startswith("deterministic")


def test_classify_objection_stream_no_objection(fake_ddb):
    tokens, on_token = _collect()
    out = objection.classify_objection_stream("J-US-J1", "Nice weather today", on_token)
    assert out["objection_detected"] is False and tokens == []


# --- guardrail interventions (2026-10-29: the blocked message was streamed as the answer) ------

PAYMENT = "Honestly, that monthly payment is more than we wanted to spend."
BLOCKED = ("That answer was withheld by the safety check. Please ask a dealership team member, "
           "or rephrase the question around your own vehicle, build, or appointment.")
_GROUNDING_TRACE = {"guardrail": {"outputAssessments": {"gr-test": [{"contextualGroundingPolicy": {
    "filters": [{"type": "GROUNDING", "threshold": 0.45, "score": 0.22, "action": "BLOCKED", "detected": True}]}}]}}}
_TOPIC_TRACE = {"guardrail": {"inputAssessment": {"gr-test": {"topicPolicy": {
    "topics": [{"name": "CreditDecisions", "type": "DENY", "action": "BLOCKED"}]}}}}}


class _Rt:
    """bedrock-runtime stub. `intervene` replays what Bedrock sends when the
    guardrail withholds the answer: the blocked message as ordinary text deltas,
    then stopReason guardrail_intervened and the trace naming the policy."""

    def __init__(self, intervene: bool = False, trace: dict | None = None):
        self.intervene, self.trace, self.calls = intervene, trace or _GROUNDING_TRACE, []

    def converse_stream(self, **kw):
        self.calls.append(("converse_stream", kw))
        if self.intervene:
            return {"stream": [{"contentBlockDelta": {"delta": {"text": BLOCKED[:40]}}},
                               {"contentBlockDelta": {"delta": {"text": BLOCKED[40:]}}},
                               {"messageStop": {"stopReason": "guardrail_intervened"}},
                               {"metadata": {"trace": self.trace}}]}
        return {"stream": [{"contentBlockDelta": {"delta": {"text": "I hear you on the payment. "}}},
                           {"contentBlockDelta": {"delta": {"text": "Let's look at the term together."}}},
                           {"messageStop": {"stopReason": "end_turn"}}]}

    def converse(self, **kw):
        self.calls.append(("converse", kw))
        if self.intervene:
            return {"stopReason": "guardrail_intervened", "trace": self.trace,
                    "output": {"message": {"content": [{"text": BLOCKED}]}}}
        return {"stopReason": "end_turn", "output": {"message": {"content": [{"text": "I hear you."}]}}}


@pytest.fixture
def aws(monkeypatch):
    """aws integration mode with a provisioned guardrail; returns a setter for the stub."""
    import boto3

    monkeypatch.setenv("INTEGRATION_MODE", "aws")
    monkeypatch.setenv("RETAIL_OPS_GUARDRAIL_ID", "gr-test")
    monkeypatch.setenv("RETAIL_OPS_GUARDRAIL_VERSION", "7")

    def use(rt: _Rt) -> _Rt:
        monkeypatch.setattr(boto3, "client", lambda *a, **k: rt)
        return rt
    return use


@pytest.mark.parametrize("trace,matched", [
    (_GROUNDING_TRACE, ["contextualGroundingPolicy:GROUNDING"]),
    (_TOPIC_TRACE, ["topicPolicy:CreditDecisions"]),
])
def test_guardrail_intervention_streams_the_fallback_not_the_blocked_message(fake_ddb, aws, trace, matched):
    aws(_Rt(intervene=True, trace=trace))
    tokens, on_token = _collect()
    out = objection.classify_objection_stream("J-US-J1", PAYMENT, on_token)
    assert out["objection_type"] == "payment_too_high"
    assert tokens == [out["grounded_on"]]                    # the curated phrasing, as one token
    assert "withheld" not in "".join(tokens)
    assert out["suggested_response"] == out["grounded_on"]
    assert out["generated_by"] == "fallback" and out["model"] is None
    assert out["engine"] == "deterministic (guardrail intervened)"
    gr = out["guardrail"]
    assert gr["applied"] is True and gr["intervened"] is True and gr["matched"] == matched
    assert (gr["id"], gr["version"]) == ("gr-test", "7")


def test_rest_path_returns_the_same_fallback_on_an_intervention(fake_ddb, aws):
    aws(_Rt(intervene=True))
    out = objection.classify_objection("J-US-J1", PAYMENT)
    assert out["suggested_response"] == out["grounded_on"] and "withheld" not in out["suggested_response"]
    assert out["generated_by"] == "fallback"
    assert out["guardrail"]["intervened"] is True
    assert out["guardrail"]["matched"] == ["contextualGroundingPolicy:GROUNDING"]


def test_stream_and_rest_send_the_same_guarded_request(fake_ddb, aws):
    """Same model, system prompt, user prompt and guardrail on both transports;
    no grounding-source tagging (an objection reply is not an answer over facts)."""
    rt = aws(_Rt())
    tokens, on_token = _collect()
    streamed = objection.classify_objection_stream("J-US-J1", PAYMENT, on_token)
    rest = objection.classify_objection("J-US-J1", PAYMENT)
    (_, s), (_, r) = rt.calls
    assert tokens == ["I hear you on the payment. ", "Let's look at the term together."]
    assert streamed["generated_by"] == rest["generated_by"] == "bedrock"
    assert streamed["guardrail"]["intervened"] is rest["guardrail"]["intervened"] is False
    for key in ("modelId", "system", "messages", "inferenceConfig"):
        assert s[key] == r[key], key
    assert "guardContent" not in json.dumps(s["messages"])
    assert s["guardrailConfig"] == {**r["guardrailConfig"], "streamProcessingMode": "sync"}
    assert r["guardrailConfig"] == {"guardrailIdentifier": "gr-test", "guardrailVersion": "7", "trace": "enabled"}


def test_sse_route_sends_the_fallback_when_the_guardrail_intervenes(fake_ddb, aws):
    from app.services.api import handler

    aws(_Rt(intervene=True))
    resp = handler.lambda_handler({
        "httpMethod": "POST", "path": "/journeys/J-US-J1/utterance-sse",
        "headers": {"Origin": "http://localhost:3100"}, "body": json.dumps({"utterance": PAYMENT}),
        "requestContext": {"authorizer": {"user_id": "U-US-00002", "role": "dealer_advisor",
                                          "dealer_ids": "D-101", "market_id": "us"}}})
    assert resp["statusCode"] == 200
    assert resp["headers"]["Access-Control-Allow-Origin"] == "http://localhost:3100"
    frames = _frames(resp["body"])
    assert [n for n, _ in frames] == ["token", "result"]
    result = frames[1][1]
    assert frames[0][1]["text"] == result["suggested_response"] == result["grounded_on"]
    assert result["guardrail"]["intervened"] is True and result["guardrail"]["applied"] is True


def test_stream_ground_holds_deltas_until_the_guardrail_verdict(aws):
    aws(_Rt(intervene=True))
    tokens, on_token = _collect()
    out = streaming.stream_ground("q", ["a fact"], [], persona="p", fallback="fb", on_token=on_token)
    assert tokens == ["fb"] and out["text"] == "fb"
    assert out["engine"] == "deterministic (guardrail grounding check)"


def _url_event(path: str, body: dict | None, token: str | None = "user:U-US-00002",
               method: str = "POST") -> dict:
    return {"requestContext": {"http": {"method": method, "path": path}},
            "headers": {"Authorization": f"Bearer {token}"} if token else {},
            "body": json.dumps(body) if body is not None else None}


def _frames(sse: str) -> list[tuple[str, dict]]:
    out = []
    for frame in sse.split("\n\n"):
        if not frame.strip():
            continue
        name = frame.split("event: ", 1)[1].split("\n", 1)[0]
        data = json.loads(frame.split("data: ", 1)[1])
        out.append((name, data))
    return out


def _advisor(fake_ddb, dealer_id: str, same: bool = True) -> str:
    """A dealer_advisor user_id at (or away from) one store."""
    return next(u["user_id"] for u in fake_ddb.rows("PLAT_Users")
                if "dealer_advisor" in (u.get("groups") or [])
                and ((u.get("dealer_id") == dealer_id) if same else
                     (u.get("dealer_id") not in (None, dealer_id))))


def test_stream_handler_emits_sse_token_then_result(fake_ddb):
    j = fake_ddb.rows("DEALER_Journeys")[0]
    resp = stream_handler.lambda_handler(
        _url_event(f"/journeys/{j['journey_id']}/utterance",
                   {"utterance": "That monthly payment is too high"},
                   token=f"user:{_advisor(fake_ddb, j['dealer_id'])}"))
    assert resp["statusCode"] == 200
    assert resp["headers"]["Content-Type"] == "text/event-stream"
    frames = _frames(resp["body"])
    assert [n for n, _ in frames] == ["token", "result"]
    assert frames[0][1]["text"] and frames[1][1]["objection_detected"] is True


def test_stream_handler_rejects_bad_token_and_out_of_scope(fake_ddb):
    j = fake_ddb.rows("DEALER_Journeys")[0]
    path = f"/journeys/{j['journey_id']}/utterance"
    assert stream_handler.lambda_handler(_url_event(path, {"utterance": "x"}, token=None))["statusCode"] == 401
    assert stream_handler.lambda_handler(_url_event(path, {"utterance": "x"}, token="user:nope"))["statusCode"] == 401
    other = _advisor(fake_ddb, j["dealer_id"], same=False)
    assert stream_handler.lambda_handler(
        _url_event(path, {"utterance": "x"}, token=f"user:{other}"))["statusCode"] == 403


def test_stream_handler_validates_route_and_body(fake_ddb):
    j = fake_ddb.rows("DEALER_Journeys")[0]
    tok = f"user:{_advisor(fake_ddb, j['dealer_id'])}"
    assert stream_handler.lambda_handler(_url_event("/journeys/board", {}, token=tok))["statusCode"] == 404
    assert stream_handler.lambda_handler(
        _url_event("/journeys/J-NOPE/utterance", {"utterance": "x"}, token=tok))["statusCode"] == 404
    assert stream_handler.lambda_handler(
        _url_event(f"/journeys/{j['journey_id']}/utterance", {}, token=tok))["statusCode"] == 400