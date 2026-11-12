# ruff: noqa: S101
"""Batch jobs make no model call, in a runtime or in the job Lambda (design D6d);
the matching user-facing tool still does, in aws mode."""
from __future__ import annotations

import pytest

from app.agentcore import agent as runtime
from app.services.agents import discovery, equity
from app.services.api import job_handler
from app.services.oem import campaigns

ADVISOR = {"user_id": "U-US-00002", "role": "dealer_advisor", "groups": ["dealer_advisor"], "dealer_ids": ["D-102"],
           "is_oem": False, "market_id": "us", "scope_node_id": "D-102", "customer_id": ""}
OEM = {"user_id": "U-US-00044", "role": "oem_dm", "groups": ["oem_dm"], "dealer_ids": [], "is_oem": True,
       "market_id": "us", "scope_node_id": "OEM", "customer_id": ""}

# (agent, job, gated helper reached by the job, agent of the tool, tool, args, claims)
CASES = [
    ("A4", "equity-monitor", (equity, "_explain"), "build_equity", {"journey_id": "J-US-J2"}, ADVISOR),
    ("A1", "rescore-profiles", (discovery, "_rationale"), "rank_vehicles", {"journey_id": "J-US-J2"}, ADVISOR),
    ("A5", "nightly-attribution", (campaigns, "_insight"), "recommend_budget_shift", {}, OEM),
]


@pytest.fixture
def clients(monkeypatch) -> list[str]:
    """Every boto3 client created, by service name; each creation raises."""
    import boto3
    created: list[str] = []

    def _client(service, *_a, **_k):
        created.append(service)
        raise RuntimeError(f"offline test: {service} is not available")

    monkeypatch.setattr(boto3, "client", _client)
    return created


def _count_helper(monkeypatch, target) -> list[int]:
    """Count calls to a gated helper while keeping its behaviour."""
    module, name = target
    original, calls = getattr(module, name), []

    def _wrapped(*a, **k):
        calls.append(1)
        return original(*a, **k)

    monkeypatch.setattr(module, name, _wrapped)
    return calls


@pytest.mark.parametrize("agent, job, helper, tool, args, claims", CASES, ids=[c[1] for c in CASES])
def test_job_in_a_runtime_makes_no_model_call(fake_ddb, monkeypatch, clients, agent, job, helper, tool,
                                              args, claims):
    monkeypatch.setenv("INTEGRATION_MODE", "aws")          # as on a runtime
    reached = _count_helper(monkeypatch, helper)
    out = runtime._invoke({"kind": "job", "agent": agent, "job": job,
                           "payload": {"market": "us", "limit": 5}, "apply": False})
    assert out["statusCode"] == 200, out
    assert reached, f"{job} never reached {helper[1]}; the check would prove nothing"
    assert clients.count("bedrock-runtime") == 0


@pytest.mark.parametrize("agent, job, helper, tool, args, claims", CASES, ids=[c[1] for c in CASES])
def test_job_in_the_job_lambda_makes_no_model_call(fake_ddb, monkeypatch, clients, agent, job, helper, tool,
                                                   args, claims):
    monkeypatch.delenv("INTEGRATION_MODE", raising=False)   # job Lambdas run without it
    reached = _count_helper(monkeypatch, helper)
    job_handler.run_job(job, {"market": "us", "limit": 5}, apply=False)
    assert reached and clients.count("bedrock-runtime") == 0


@pytest.mark.parametrize("agent, job, helper, tool, args, claims", CASES, ids=[c[3] for c in CASES])
def test_user_facing_tool_keeps_its_model_call(fake_ddb, monkeypatch, clients, agent, job, helper, tool,
                                               args, claims):
    monkeypatch.setenv("INTEGRATION_MODE", "aws")
    out = runtime._invoke({"kind": "tool", "agent": agent, "tool": tool, "args": args, "claims": claims})
    assert out["statusCode"] == 200, out
    assert clients.count("bedrock-runtime") == 1
