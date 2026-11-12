# ruff: noqa: S101
"""platform.mode: one integration-mode reader with a per-context override (design D6d)."""
from __future__ import annotations

import threading

import pytest

from app.services.agents import discovery, equity
from app.services.oem import campaigns
from app.services.platform import compute, generate, knowledge, memory, mode, ranker

_EQUITY = {"equity": 4200, "currency": "USD", "equity_position": "positive",
           "current_vehicle": {"year": 2021, "make": "Acme", "model": "Roadster"},
           "upgrade_signal": {"recommend_upgrade": True}}
_MATCH = {"trim_name": "Touring", "powertrain": "EV", "list_price": 41000, "currency": "USD", "seats": 5,
          "in_stock_count": 2}
_BUDGET = {"currency": "USD", "price_min": 30000, "price_max": 45000}
_ROI = {"kpis": {"campaigns": 2, "attributed_deliveries": 9},
        "campaigns": [{"name": "Spring EV", "budget": 10000, "attributed_deliveries": 6, "roi": 0.8},
                      {"name": "Winter tyres", "budget": 9000, "attributed_deliveries": 3, "roi": -0.1}]}


def _helpers() -> list[tuple[str, str]]:
    return [equity._explain(_EQUITY), discovery._rationale(_MATCH, _BUDGET, {"powertrain_openness": "ev"}),
            campaigns._insight(_ROI)]


@pytest.fixture
def model_clients(monkeypatch) -> list[str]:
    """Record every boto3 client a helper creates; each one then raises."""
    import boto3
    created: list[str] = []

    def _client(service, *_a, **_k):
        created.append(service)
        raise RuntimeError(f"offline test: {service} is not available")

    monkeypatch.setattr(boto3, "client", _client)
    return created


def test_mode_follows_the_environment_without_an_override(monkeypatch):
    monkeypatch.delenv("INTEGRATION_MODE", raising=False)
    assert mode.integration_mode() == "local" and generate.enabled() is False
    monkeypatch.setenv("INTEGRATION_MODE", "AWS")
    assert mode.integration_mode() == "aws" and generate.enabled() is True
    assert ranker._mode() == knowledge._mode() == compute._mode() == "aws"


def test_override_local_reaches_every_reader_and_the_three_helpers(monkeypatch, model_clients):
    monkeypatch.setenv("INTEGRATION_MODE", "aws")
    monkeypatch.setenv("RETAIL_OPS_MEMORY_ID", "mem-test")
    assert memory.enabled() is True
    with mode.override("local"):
        assert mode.integration_mode() == "local"
        assert generate.enabled() is False and memory.enabled() is False
        assert ranker._mode() == knowledge._mode() == compute._mode() == "local"
        texts = _helpers()
    assert [by for _, by in texts] == ["fallback"] * 3
    assert "bedrock-runtime" not in model_clients
    assert texts[0][0].startswith("Trade equity on the 2021 Acme Roadster")
    # outside the override the same helpers do try the model (the counter sees it)
    assert [by for _, by in _helpers()] == ["fallback"] * 3
    assert model_clients.count("bedrock-runtime") == 3


def test_helpers_skip_the_model_in_local_mode(monkeypatch, model_clients):
    monkeypatch.delenv("INTEGRATION_MODE", raising=False)
    assert [by for _, by in _helpers()] == ["fallback"] * 3
    assert model_clients == []


def test_override_is_restored_after_an_exception_and_nests(monkeypatch):
    monkeypatch.setenv("INTEGRATION_MODE", "aws")
    with pytest.raises(RuntimeError), mode.override("local"):
        raise RuntimeError("boom")
    assert mode.integration_mode() == "aws"
    with mode.override("local"):
        with mode.override("aws"):
            assert mode.integration_mode() == "aws"
        assert mode.integration_mode() == "local"
    assert mode.integration_mode() == "aws"


def test_override_is_not_seen_by_a_concurrent_thread(monkeypatch):
    monkeypatch.setenv("INTEGRATION_MODE", "aws")
    barrier = threading.Barrier(2, timeout=10)
    seen: dict[str, str] = {}

    def _job_request():
        with mode.override("local"):
            barrier.wait()                    # both requests are in flight
            seen["job"] = mode.integration_mode()
            barrier.wait()

    def _user_request():
        barrier.wait()
        seen["user"] = mode.integration_mode()
        seen["user_generate"] = str(generate.enabled())
        barrier.wait()

    threads = [threading.Thread(target=_job_request), threading.Thread(target=_user_request)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert seen == {"job": "local", "user": "aws", "user_generate": "True"}
