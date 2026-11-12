"""Admin Console "Demo data" card: GET /admin/demo, POST /admin/demo/reset, and the
demo-reset Lambda's handler (app/demo/reset_handler.py)."""
from __future__ import annotations

import datetime as dt

import pytest

from app.demo import readiness, reset, reset_handler
from app.services.admin import demo_data
from tests.app.conftest import api_event, call
from tests.app.test_demo_reset import FakeClient

ADMIN = dict(user="U-US-00051", role="admin_tenant", is_oem=True)
PLATFORM = dict(user="U-ADMIN-someone", role="platform_admin", is_oem=True)
DADMIN = dict(user="U-US-00052", role="admin_dealer", dealer_ids="D-101")
PREFIX = "retail-ops-dev-"


class FakeLambda:
    def __init__(self, fail: bool = False):
        self.calls: list[dict] = []
        self.fail = fail

    def invoke(self, **kw):
        if self.fail:
            raise RuntimeError("ResourceNotFoundException")
        self.calls.append(kw)
        return {"StatusCode": 202}


@pytest.fixture
def demo(fake_ddb, monkeypatch):
    monkeypatch.setenv("RETAIL_OPS_ENV", "dev")
    monkeypatch.delenv("RETAIL_OPS_ENV_PREFIX", raising=False)
    client = FakeClient({f"{PREFIX}PLAT_AgentTraces": (["trace_id"], [])})
    lam = FakeLambda()
    monkeypatch.setattr(demo_data, "_ddb_client", lambda: client)
    monkeypatch.setattr(demo_data, "_lambda_client", lambda: lam)
    monkeypatch.setattr(readiness, "report", lambda env_prefix=None: [
        readiness.Scenario("ev-purchase", "New-car purchase", 9, "a buyer", "9 of 10 unused")])
    return client, lam


def test_tenant_and_platform_admins_see_the_card_and_dealer_admins_do_not(demo):
    code, out = call(api_event("GET", "/admin/demo", **ADMIN))
    assert code == 200 and out["enabled"] and out["last_reset"] is None
    assert out["readiness"][0]["scenario_id"] == "ev-purchase" and out["readiness"][0]["runs_left"] == 9
    assert call(api_event("GET", "/admin/demo", **PLATFORM))[0] == 200
    assert call(api_event("GET", "/admin/demo", **DADMIN))[0] == 403
    assert call(api_event("POST", "/admin/demo/reset", **DADMIN, body={"confirm": "reset"}))[0] == 403
    assert call(api_event("GET", "/admin/demo", user="U-US-00002", role="dealer_advisor", dealer_ids="D-101"))[0] == 403


def test_reset_needs_the_typed_confirmation(demo):
    _, lam = demo
    code, _ = call(api_event("POST", "/admin/demo/reset", **ADMIN, body={}))
    assert code == 400 and not lam.calls
    assert call(api_event("POST", "/admin/demo/reset", **ADMIN, body={"confirm": "yes"}))[0] == 400


def test_reset_starts_the_lambda_async_and_refuses_a_second_while_running(demo):
    client, lam = demo
    code, out = call(api_event("POST", "/admin/demo/reset", **ADMIN, body={"confirm": "reset"}))
    assert code == 202 and out["started"] and out["status"]["state"] == "running"
    (inv,) = lam.calls
    assert inv["FunctionName"] == f"{PREFIX}demo-reset" and inv["InvocationType"] == "Event"
    assert reset.read_status(client, PREFIX)["actor"] == "U-US-00051"
    assert call(api_event("POST", "/admin/demo/reset", **PLATFORM, body={"confirm": "reset"}))[0] == 409
    code, out = call(api_event("GET", "/admin/demo", **ADMIN))
    assert out["last_reset"]["state"] == "running"


def test_a_running_reset_older_than_the_lambda_limit_is_stale_and_can_rerun(demo):
    client, _ = demo
    old = (dt.datetime.now(dt.UTC) - dt.timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
    reset.write_status(client, PREFIX, "running", actor="x", started_at=old)
    assert demo_data.last_reset(client)["state"] == "stale"
    assert call(api_event("POST", "/admin/demo/reset", **ADMIN, body={"confirm": "Reset"}))[0] == 202


def test_a_lambda_that_cannot_start_records_failed(demo, monkeypatch):
    client, _ = demo
    monkeypatch.setattr(demo_data, "_lambda_client", lambda: FakeLambda(fail=True))
    assert call(api_event("POST", "/admin/demo/reset", **ADMIN, body={"confirm": "reset"}))[0] == 503
    assert reset.read_status(client, PREFIX)["state"] == "failed"


def test_off_the_dev_stack_the_card_is_disabled_and_reset_refused(demo, monkeypatch):
    monkeypatch.setenv("RETAIL_OPS_ENV", "prod")
    code, out = call(api_event("GET", "/admin/demo", **ADMIN))
    assert code == 200 and not out["enabled"] and "readiness" not in out
    assert call(api_event("POST", "/admin/demo/reset", **ADMIN, body={"confirm": "reset"}))[0] == 403


def test_handler_records_done_with_counts_and_failed_with_the_error(monkeypatch):
    monkeypatch.setenv("RETAIL_OPS_ENV", "dev")
    client = FakeClient({f"{PREFIX}PLAT_AgentTraces": (["trace_id"], [])})
    monkeypatch.setattr(reset_handler, "_client", lambda: client)
    monkeypatch.setattr(reset_handler.ddb, "DEFAULT_ENV_PREFIX", PREFIX)
    monkeypatch.setattr(reset, "run", lambda c, p, backup=True, log=None: {"removed": 7, "written": 9, "tables": 2})
    out = reset_handler.lambda_handler({"actor": "U-US-00051"}, None)
    assert out["ok"] and out["removed"] == 7
    s = reset.read_status(client, PREFIX)
    assert s["state"] == "done" and s["removed"] == 7 and s["actor"] == "U-US-00051" and s["finished_at"]

    def boom(*a, **k):
        raise RuntimeError("throttled")
    monkeypatch.setattr(reset, "run", boom)
    out = reset_handler.lambda_handler({"actor": "U-US-00051"}, None)
    assert not out["ok"]
    s = reset.read_status(client, PREFIX)
    assert s["state"] == "failed" and "throttled" in s["error"]


def test_handler_refuses_a_non_demo_prefix(monkeypatch):
    client = FakeClient({"retail-ops-prod-PLAT_AgentTraces": (["trace_id"], [])})
    monkeypatch.setattr(reset_handler, "_client", lambda: client)
    monkeypatch.setattr(reset_handler.ddb, "DEFAULT_ENV_PREFIX", "retail-ops-prod-")
    out = reset_handler.lambda_handler({}, None)
    assert not out["ok"] and "refusing" in out["error"]
    assert reset.read_status(client, "retail-ops-prod-") is None      # nothing written there
