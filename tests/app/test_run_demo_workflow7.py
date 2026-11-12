"""run_demo --workflow7: demo workflow 7 (one buyer, web lead to delivery) driven the way
verify.sh drives it on a deployed stack, here through the Lambda handler over the
in-memory DynamoDB. The harness's HTTP client is swapped for one that resolves the
persona token with the real authorizer and routes the call through the handler, so
the eleven steps, their expectations and the ids the run collects are all exercised
offline. Teardown is covered separately: the fake table has no delete, so the run
keeps its rows and the test checks what it left."""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest

from app.demo import run_demo
from app.services.api import authorizer, handler


class _HandlerClient:
    """run_demo.Client with the same signature, answered by handler.lambda_handler."""

    def __init__(self, base: str, token: str) -> None:
        self.base, self.token = base, token

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        parsed = urlparse(path)
        qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        event = {"path": parsed.path, "httpMethod": method, "headers": {"Authorization": self.token},
                 "queryStringParameters": qs or None, "requestContext": {},
                 "body": json.dumps(body) if body is not None else None}
        try:
            event["requestContext"]["authorizer"] = authorizer.resolve_principal(self.token)
        except Exception as e:  # noqa: BLE001 — the deployed authorizer answers 401 the same way
            return 401, {"error": f"unauthorized: {e}"}
        resp = handler.lambda_handler(event)
        return resp["statusCode"], json.loads(resp["body"] or "{}")


@pytest.fixture
def harness(fake_ddb, monkeypatch):
    monkeypatch.setattr(run_demo, "Client", _HandlerClient)
    monkeypatch.setenv("RETAIL_OPS_ALLOW_DEV_TOKEN", "1")
    return fake_ddb


def test_workflow7_runs_end_to_end_and_every_expectation_holds(harness, capsys):
    assert run_demo.run_workflow7("http://offline", keep=True) == 0
    out = capsys.readouterr().out
    assert "WORKFLOW 7 RESULT: ALL PASS" in out and "[FAIL]" not in out
    # every persona took its turn
    for step in ("(Keisha)", "(Elizabeth)", "(Michael)", "(Megan)", "(the buyer)"):
        assert step in out
    # the run left exactly one buyer, whose journey rests in OWN against a delivered VIN
    custs = [c for c in harness.rows("DEALER_Customers") if str(c.get("email", "")).startswith("verify.buyer.")]
    assert len(custs) == 1
    js = [j for j in harness.rows("DEALER_Journeys") if j.get("customer_id") == custs[0]["customer_id"]]
    assert len(js) == 1 and js[0]["stage"] == "OWN" and js[0]["owner_user_id"] == run_demo.W7_ADVISOR_ID
    deals = [d for d in harness.rows("DEALER_Deals") if d.get("journey_id") == js[0]["journey_id"]]
    assert len(deals) == 1 and deals[0].get("vin")
    unit = next(u for u in harness.rows("DEALER_Inventory") if u["vin"] == deals[0]["vin"])
    assert unit["dealer_id"] == run_demo.DEALER and unit["status"] != "in_stock"
    assert any(v["vin"] == deals[0]["vin"] for v in harness.rows("DEALER_Vehicles"))
    assert "(--keep set" in out and js[0]["journey_id"] in out


def test_workflow7_web_lead_is_a_throwaway_buyer_per_run():
    a, b = run_demo.w7_web_lead("abc123"), run_demo.w7_web_lead("def456")
    assert a["email"] != b["email"] and a["email"].endswith("@example.test")
    assert a["vehicle_of_interest_model_id"] == run_demo.W7_MODEL == "MER-VOLT-C"
    assert a["current_vehicle"]["payoff"] == 8400 and a["budget"]["monthly_max"] == 560


def test_workflow7_stops_cleanly_when_the_intake_is_refused(harness, monkeypatch, capsys):
    class _Refusing(_HandlerClient):
        def call(self, method, path, body=None):
            if path == "/leads":
                return 403, {"error": "forbidden"}
            return super().call(method, path, body)
    monkeypatch.setattr(run_demo, "Client", _Refusing)
    assert run_demo.run_workflow7("http://offline", keep=True) == 1
    out = capsys.readouterr().out
    assert "[FAIL] lead intake" in out and "WORKFLOW 7 RESULT: SOME FAILURES" in out
