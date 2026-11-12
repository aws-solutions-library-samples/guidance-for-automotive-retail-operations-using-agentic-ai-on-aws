"""API Gateway's own error replies carry the console's CORS headers (2026-11-03).

Replies the gateway builds itself never reach the service Lambda, so they skip
app/services/api/cors.py: the TOKEN authorizer's 401 / 403, a missing-token 403,
throttling and gateway 5xx. With no Access-Control-Allow-Origin the browser hid
them as "Failed to fetch", even for a correctly signed-in failure. deploy_l4 now
puts the same headers on the DEFAULT_4XX / DEFAULT_5XX gateway responses.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.infra import deploy_l4
from app.services.api import cors

CONSOLE = "https://d1234567890abc.cloudfront.net"


def _fake_agw() -> MagicMock:
    agw = MagicMock()
    agw.get_rest_apis.return_value = {"items": []}
    agw.create_rest_api.return_value = {"id": "api123"}
    agw.get_resources.return_value = {"items": [{"id": "root", "path": "/"}]}
    agw.create_authorizer.return_value = {"id": "auth1"}
    counter = iter(range(10_000))
    agw.create_resource.side_effect = lambda **_: {"id": f"r{next(counter)}"}
    return agw


class _NotFound(Exception):
    pass


def _fake_lam() -> MagicMock:
    lam = MagicMock()
    lam.exceptions.ResourceNotFoundException = _NotFound
    lam.exceptions.ResourceConflictException = _NotFound
    lam.get_policy.side_effect = _NotFound()
    return lam


def test_origin_prefers_this_deploy_then_the_lambda_then_local():
    assert deploy_l4.gateway_cors_origin({deploy_l4.CONSOLE_ORIGIN: f"{CONSOLE}/,https://b.example"}) == CONSOLE
    assert deploy_l4.gateway_cors_origin({}, CONSOLE) == CONSOLE
    assert deploy_l4.gateway_cors_origin({}, None) == cors.LOCAL_ORIGINS[0]
    assert deploy_l4.gateway_cors_origin({deploy_l4.CONSOLE_ORIGIN: " "}, "") == cors.LOCAL_ORIGINS[0]


def test_parameters_match_the_lambda_reply_headers(monkeypatch):
    monkeypatch.setenv(cors.CONSOLE_ORIGIN_ENV, CONSOLE)
    params = deploy_l4.gateway_cors_parameters(CONSOLE)
    want = {k: v for k, v in cors.headers(CONSOLE).items() if k != "Content-Type"}
    assert params == {f"gatewayresponse.header.{k}": f"'{v}'" for k, v in want.items()}
    assert params["gatewayresponse.header.Access-Control-Allow-Origin"] == f"'{CONSOLE}'"  # never '*'


@pytest.mark.parametrize("bad", ["*", "https://x.net'", "ftp://x.net", "http://evil.example"])
def test_parameters_refuse_an_unsafe_origin(bad):
    with pytest.raises(SystemExit):
        deploy_l4.gateway_cors_parameters(bad)


def test_ensure_api_sets_both_default_responses_before_deploying():
    agw, lam = _fake_agw(), _fake_lam()
    deploy_l4.ensure_api(agw, lam, "111122223333", "arn:auth", "arn:svc", apply=True, cors_origin=CONSOLE)
    names = [c[0] for c in agw.method_calls]
    puts = [c.kwargs for c in agw.put_gateway_response.call_args_list]
    assert {p["responseType"] for p in puts} == {"DEFAULT_4XX", "DEFAULT_5XX"}
    for p in puts:
        assert p["restApiId"] == "api123"
        assert p["responseParameters"]["gatewayresponse.header.Access-Control-Allow-Origin"] == f"'{CONSOLE}'"
    # the stage deployment publishes them, so they must be in place first
    assert max(i for i, n in enumerate(names) if n == "put_gateway_response") < names.index("create_deployment")


def test_ensure_api_without_a_console_origin_still_allows_the_local_console():
    agw = _fake_agw()
    deploy_l4.ensure_api(agw, _fake_lam(), "111122223333", "arn:auth", "arn:svc", apply=True)
    origins = {c.kwargs["responseParameters"]["gatewayresponse.header.Access-Control-Allow-Origin"]
               for c in agw.put_gateway_response.call_args_list}
    assert origins == {f"'{cors.LOCAL_ORIGINS[0]}'"}


def test_dry_run_touches_nothing():
    agw = _fake_agw()
    deploy_l4.ensure_api(agw, _fake_lam(), "111122223333", "arn:auth", "arn:svc", apply=False, cors_origin=CONSOLE)
    agw.put_gateway_response.assert_not_called()
    agw.create_deployment.assert_not_called()
