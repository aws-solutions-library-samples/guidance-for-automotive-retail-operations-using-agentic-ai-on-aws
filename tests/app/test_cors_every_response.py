"""Every API response carries the console's CORS headers (2026-10-29).

The SSE objection route (POST /journeys/{id}/utterance-sse) built its own header
dict without Access-Control-Allow-Origin: the preflight passed, the browser then
blocked the 200 and the Advisor screen showed "Objection check failed: Failed to
fetch". Every response now takes its headers from app/services/api/cors.py.
"""
from __future__ import annotations

import ast
import http.client
import json
import re
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from app.services import devserver
from app.services.agents import objection
from app.services.api import handler, platform_routes, routes

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "app" / "services" / "api"
CONSOLE = "https://d1234567890abc.cloudfront.net"
OTHER = "https://example.com"
PAYMENT = "Honestly, that monthly payment is more than we wanted to spend."


def _assert_cors(resp: dict, where: str = "") -> None:
    h = resp["headers"]
    assert h.get("Access-Control-Allow-Origin") == CONSOLE, where       # never OTHER, never *
    assert h.get("Vary") == "Origin", where
    assert h.get("Access-Control-Allow-Headers") == "Authorization,Content-Type,X-Correlation-Id,X-Act-As", where
    assert h.get("Access-Control-Allow-Methods") == "GET,POST,PUT,PATCH,DELETE,OPTIONS", where
    assert h.get("Access-Control-Expose-Headers") == "X-Correlation-Id", where
    assert h.get("X-Correlation-Id"), where


def _event(method: str, path: str, origin: str, *, user: str | None = "U-US-00002",
           role: str = "dealer_advisor", dealer_ids: str = "D-101", body=None) -> dict:
    auth = {"user_id": user, "role": role, "dealer_ids": dealer_ids, "is_oem": "false",
            "market_id": "us", "scope_node_id": ""} if user else {}
    return {"httpMethod": method, "path": path, "headers": {"Origin": origin},
            "body": body if isinstance(body, str) or body is None else json.dumps(body),
            "requestContext": {"authorizer": auth}}


def _sample(pattern: str) -> str:
    """A concrete path the route pattern matches."""
    s = pattern.lstrip("^").rstrip("$")
    s = re.sub(r"\(([\w-]+)(?:\|[\w-]+)+\)", r"\1", s)        # (a|b|c) -> a
    return s.replace("([^/]+)", "X-1").replace("([a-z-]+)", "x")


def _routes() -> list[tuple[str, str, str]]:
    """(method, sample path, pattern) for every route the service Lambda answers:
    the per-domain modules, the platform table and the handler's own patterns."""
    out = {(v, _sample(rx.pattern), rx.pattern) for v, rx, _fn in routes.all_routes()}
    out |= {(v, _sample(rx.pattern), rx.pattern) for v, rx, _fn in platform_routes.ROUTES}
    for obj in vars(handler).values():
        if isinstance(obj, re.Pattern):
            out |= {(v, _sample(obj.pattern), obj.pattern) for v in ("GET", "POST")}
    return sorted(out)


ROUTES = _routes()


def test_every_route_pattern_has_a_matching_sample_path():
    assert len(ROUTES) > 300
    assert [r for r in ROUTES if not re.match(r[2], r[1])] == []


@pytest.mark.parametrize("origin", [CONSOLE, OTHER])
def test_every_route_answers_with_the_console_cors_headers(fake_ddb, monkeypatch, origin):
    """Unauthenticated (401s, the shared refusal) and as a Sales Advisor (the
    routes' own replies and errors): every response names the console origin,
    and another origin is never echoed."""
    monkeypatch.setenv(handler.CONSOLE_ORIGIN_ENV, CONSOLE)
    bad = []
    for method, path, _p in ROUTES:
        for user in (None, "U-US-00002"):
            resp = handler.lambda_handler(_event(method, path, origin, user=user, body={}))
            try:
                _assert_cors(resp)
            except AssertionError:
                bad.append((method, path, user, resp["statusCode"], resp["headers"]))
    assert bad == []


def test_only_the_shared_helpers_build_a_response_dict():
    """No route builds its own header dict: the only `"statusCode"` literals under
    app/services/api are handler._response and handler._sse_response (both take
    cors.headers()). stream_handler.py is the documented exception (the Function
    URL's own CORS config sets its headers)."""
    found = set()
    for path in sorted(API.rglob("*.py")):
        if path.name == "stream_handler.py":
            continue
        tree = ast.parse(path.read_text())
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(fn):
                    if isinstance(node, ast.Dict) and any(
                            isinstance(k, ast.Constant) and k.value == "statusCode" for k in node.keys):
                        found.add(f"{path.relative_to(API)}:{fn.name}")
    assert found == {"handler.py:_response", "handler.py:_sse_response"}
    assert "Access-Control" not in (ROOT / "app" / "services" / "devserver.py").read_text()


# --- the SSE floor route: success and every early error ---------------------------------------

def _sse(origin: str = CONSOLE, **kw) -> dict:
    kw.setdefault("body", {"utterance": PAYMENT})
    return handler.lambda_handler(_event("POST", kw.pop("path", "/journeys/J-US-J1/utterance-sse"), origin, **kw))


@pytest.mark.parametrize("origin", [CONSOLE, OTHER])
def test_sse_success_carries_the_cors_headers(fake_ddb, monkeypatch, origin):
    monkeypatch.setenv(handler.CONSOLE_ORIGIN_ENV, CONSOLE)
    resp = _sse(origin)
    assert resp["statusCode"] == 200
    assert resp["headers"]["Content-Type"] == "text/event-stream; charset=utf-8"
    assert resp["headers"]["Cache-Control"] == "no-cache"
    _assert_cors(resp)
    assert "event: token" in resp["body"] and "event: result" in resp["body"]


def test_sse_result_carries_the_rest_card_fields(fake_ddb, monkeypatch):
    """The SSE result has the fields POST /objection adds (success rate and the
    "shown" recommendation Use This / Alternative record against)."""
    monkeypatch.setenv(handler.CONSOLE_ORIGIN_ENV, CONSOLE)
    frame = handler.lambda_handler(_event("POST", "/journeys/J-US-J1/utterance-sse", CONSOLE,
                                          body={"utterance": PAYMENT}))["body"].split("event: result\ndata: ")[1]
    result = json.loads(frame)
    rest = json.loads(handler.lambda_handler(_event("POST", "/journeys/J-US-J1/objection", CONSOLE,
                                                    body={"utterance": PAYMENT}))["body"])
    assert result["objection_type"] == rest["objection_type"] == "payment_too_high"
    for key in ("success_rate", "success_basis", "recommendation_id", "strategy_id", "source_ref"):
        assert key in result and key in rest
    assert result["recommendation_id"]


@pytest.mark.parametrize("case,want", [
    ({"user": None}, 401),
    ({"user": "U-US-00011", "dealer_ids": "D-102"}, 403),
    ({"path": "/journeys/J-NOPE/utterance-sse"}, 404),
    ({"body": "{not json"}, 400),
    ({"body": {"utterance": "  "}}, 400),
])
@pytest.mark.parametrize("origin", [CONSOLE, OTHER])
def test_sse_errors_carry_the_cors_headers(fake_ddb, monkeypatch, case, want, origin):
    monkeypatch.setenv(handler.CONSOLE_ORIGIN_ENV, CONSOLE)
    resp = _sse(origin, **dict(case))
    assert resp["statusCode"] == want
    _assert_cors(resp)


@pytest.mark.parametrize("origin", [CONSOLE, OTHER])
def test_sse_generation_failure_is_a_500_with_the_cors_headers(fake_ddb, monkeypatch, origin):
    monkeypatch.setenv(handler.CONSOLE_ORIGIN_ENV, CONSOLE)

    def _boom(*_a, **_k):
        raise RuntimeError("strategy table unavailable")
    monkeypatch.setattr(objection, "classify_objection_stream", _boom)
    resp = _sse(origin)
    assert resp["statusCode"] == 500
    assert json.loads(resp["body"])["error"] == "RuntimeError: strategy table unavailable"
    _assert_cors(resp)


# --- the local dev server forwards the handler's headers --------------------------------------

def test_devserver_forwards_the_handler_cors_headers_and_the_sse_body(fake_ddb, monkeypatch):
    monkeypatch.setenv(handler.CONSOLE_ORIGIN_ENV, CONSOLE)
    monkeypatch.setattr(devserver, "_REQUIRE_AUTH", False)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), devserver.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        def call(method, path, origin, body=None):
            conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=20)
            conn.request(method, path, body=json.dumps(body) if body else None,
                         headers={"Origin": origin, "Content-Type": "application/json"})
            r = conn.getresponse()
            out = (r.status, dict(r.getheaders()), r.read().decode())
            conn.close()
            return out
        status, hdrs, body = call("POST", "/journeys/J-US-J1/utterance-sse", "http://localhost:3100",
                                  {"utterance": PAYMENT})
        assert status == 200 and hdrs["Content-Type"].startswith("text/event-stream")
        assert hdrs["Access-Control-Allow-Origin"] == "http://localhost:3100" and hdrs["Vary"] == "Origin"
        assert "event: result" in body
        for method, path in (("OPTIONS", "/journeys/J-US-J1/utterance-sse"), ("GET", "/health")):
            status, hdrs, _ = call(method, path, OTHER)
            assert hdrs["Access-Control-Allow-Origin"] == CONSOLE, (method, path)
            assert hdrs["Vary"] == "Origin"
    finally:
        srv.shutdown()
        srv.server_close()
