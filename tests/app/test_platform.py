"""Platform: event catalog, autonomy hard rules, URL contract, personas, routes."""
from __future__ import annotations

import pytest

from app.services.admin import autonomy
from app.services.common import autonomy_config, personas, resource_url
from app.services.events import bus
from tests.app.conftest import api_event, call


# --- events -----------------------------------------------------------------

def test_emit_rejects_uncatalogued_type():
    with pytest.raises(bus.UnknownEventType):
        bus.emit("journey.teleported", {"journey_id": "J"})


def test_envelope_shape_and_idempotent_dispatch(fake_ddb):
    out = bus.emit("journey.stalled", {"journey_id": "J-US-J7", "dealer_id": "D-106"})
    env = bus.EMITTED[-1]
    for k in ("event_id", "type", "ts", "actor", "scope", "idempotency_key"):
        assert k in env
    again = bus.dispatch_local("journey.stalled", env)
    assert again.get("duplicate") is True, out


def test_subscriber_failure_isolated(fake_ddb, monkeypatch):
    def boom(detail, env_prefix=None):
        raise RuntimeError("subscriber down")
    monkeypatch.setitem(bus._EXTRA_SUBSCRIBERS, "deal.booked", [boom])
    out = bus.dispatch_local("deal.booked", {"journey_id": "J-US-J4"})
    assert out["failures"][0]["error"].startswith("RuntimeError")


# --- autonomy hard rules (§4.10.1, acceptance #33) ---------------------------

def test_hard_rule_cannot_be_raised(fake_ddb):
    with pytest.raises(autonomy.HardRuleViolation):
        autonomy.set_autonomy("a5.shift_budget", "act_and_notify", actor="admin")


def test_runtime_clamp_ignores_hand_written_row(fake_ddb):
    rows = [r for r in fake_ddb.rows("PLAT_ConfigAutonomy") if r["action_id"] == "a5.shift_budget"]
    row = dict(max(rows, key=lambda r: r["version"]))
    row.update(version=row["version"] + 10, level="autonomous", is_active=True, scope="tenant")
    fake_ddb.seed("PLAT_ConfigAutonomy", [row])
    assert autonomy_config.active_level("a5.shift_budget", "us") == "act_with_approval"


def test_set_and_rollback_versions(fake_ddb):
    first = autonomy.set_autonomy("a7.stage_parts", "observe" if False else "recommend", actor="admin")
    second = autonomy.set_autonomy("a7.stage_parts", "act_and_notify", actor="admin")
    assert second["new_version"] == first["new_version"] + 1
    back = autonomy.rollback_autonomy("a7.stage_parts", first["new_version"], actor="admin")
    assert back["new_level"] == "recommend" and back["rolled_back_to"] == first["new_version"]
    assert autonomy_config.active_level("a7.stage_parts", "us") == "recommend"


def test_admin_route_returns_422_for_hard_rule(fake_ddb):
    code, body = call(api_event("POST", "/admin/autonomy", role="admin_tenant",
                                body={"action_id": "a6.publish_account_plan", "level": "act_and_notify"}))
    assert code == 422 and body["hard_rule"] is True


# --- resource URL contract (§5.18.2) -----------------------------------------

def test_resource_url_roundtrip_and_role_resolution():
    url = resource_url.resource_url("journey", "J-US-J1")
    assert url == "/r/journey/J-US-J1" and resource_url.is_canonical(url)
    adv = resource_url.resolve("journey", "J-US-J1", "dealer_advisor")
    assert adv["allowed"] and adv["url"] == "/dealer/advisor/J-US-J1"
    fni = resource_url.resolve("journey", "J-US-J1", "dealer_fni")
    assert fni["url"] == "/dealer/fni/J-US-J1"
    cust = resource_url.resolve("journey", "J-US-J1", "customer")
    assert cust["url"] == "/garage"
    deny = resource_url.resolve("deal", "DL-1", "oem_marketing")
    assert not deny["allowed"] and deny["home"] == "/marketing/campaigns"


def test_ctx_envelope_signed_and_tamper_evident():
    tok = resource_url.sign_ctx("oem", "alerts", "alert", "AL-123", scope={"dealer_id": "D-101"})
    claims = resource_url.verify_ctx(tok)
    assert claims["reason_id"] == "AL-123"
    head, body, sig = tok.split(".")
    with pytest.raises(resource_url.InvalidContext):
        resource_url.verify_ctx(f"{head}.{body}.{sig[:-2]}xx")
    out = resource_url.resolve("dealer", "D-101", "dealer_manager", ctx=tok)
    assert "AL-123" in out["context"]["because"] and "ctx=" in out["url"]


# --- personas (§5.2, acceptance #17) -----------------------------------------

def test_every_group_has_a_home_it_can_open():
    assert len(personas.STAFF_GROUPS) == 19
    for role, (home, screens) in personas.PERSONAS.items():
        assert home in screens, role
        for s in screens:
            assert s in personas.SCREENS, (role, s)


@pytest.mark.parametrize("role,screen,allowed", [
    ("dealer_bdc", "oem.accounts", False),          # acceptance #16
    ("oem_sales_ops", "dealer.advisor", False),     # acceptance #16
    ("dealer_technician", "service.estimate", False),  # acceptance #30
    ("dealer_service_manager", "service.estimate", True),
    ("dealer_manager", "dealer.fni", True),
])
def test_role_matrix(role, screen, allowed):
    assert personas.may_open(role, screen) is allowed


# --- routes -----------------------------------------------------------------

def test_tasks_route_flow(fake_ddb):
    code, t = call(api_event("POST", "/tasks", user="U-US-00002", dealer_ids="D-101",
                             body={"title": "Call back", "journey_id": "J-US-J1"}))
    assert code == 201 and t["resource_url"] == "/r/journey/J-US-J1"
    code, lst = call(api_event("GET", "/tasks", user="U-US-00002", dealer_ids="D-101"))
    assert code == 200 and any(x["task_id"] == t["task_id"] for x in lst["tasks"])
    code, _ = call(api_event("PATCH", f"/tasks/{t['task_id']}", user="U-US-00099",
                             dealer_ids="D-101", body={"status": "done"}))
    assert code == 403
    code, done = call(api_event("PATCH", f"/tasks/{t['task_id']}", user="U-US-00002",
                                dealer_ids="D-101", body={"status": "done"}))
    assert code == 200 and done["status"] == "done"


def test_tasks_scope_enforced(fake_ddb):
    code, _ = call(api_event("POST", "/tasks", user="U-US-00011", dealer_ids="D-102",
                             body={"title": "x", "journey_id": "J-US-J1"}))
    assert code == 403


def test_customer_cannot_use_staff_routes(fake_ddb):
    code, _ = call(api_event("GET", "/tasks", user="C-US-J1", role="customer", customer_id="C-US-J1"))
    assert code == 403


def test_nav_resolve_route(fake_ddb):
    code, body = call(api_event("GET", "/nav/resolve", role="dealer_service",
                                query={"type": "estimate", "id": "EST-1"}))
    assert code == 200 and body["url"] == "/dealer/service/EST-1/estimate"
    code, body = call(api_event("GET", "/nav/resolve", role="dealer_technician",
                                query={"type": "estimate", "id": "EST-1"}))
    assert code == 403


def test_recommendation_outcome_loop_via_routes(fake_ddb):
    code, rec = call(api_event("POST", "/recommendations", user="U-US-00002", dealer_ids="D-101",
                               body={"agent_id": "A2", "action": "book_test_drive",
                                     "journey_id": "J-US-J1", "context_hash": "ctx-t"}))
    assert code == 201 and rec["shown_at"] and rec["accepted_at"] is None
    rid = rec["recommendation_id"]
    code, acc = call(api_event("POST", f"/recommendations/{rid}/accept", user="U-US-00002",
                               dealer_ids="D-101"))
    assert code == 200 and acc["accepted_by"] == "U-US-00002"

    from app.services.journeys import actions
    journey = next(j for j in fake_ddb.rows("DEALER_Journeys") if j["journey_id"] == "J-US-J1")
    actions.book_test_drive(journey, actor="U-US-00002")
    row = next(r for r in fake_ddb.rows("DEALER_Recommendations") if r["recommendation_id"] == rid)
    assert row["converted_at"] and row["conversion_event"].startswith("EVT-")
    assert not ({"email", "phone"} & set(row))

    code, st = call(api_event("GET", "/recommendations/stats", user="U-US-00002", dealer_ids="D-101",
                              query={"agent": "A2", "action_id": "book_test_drive"}))
    assert code == 200 and st["actions"][0]["converted"] >= 1


def test_me_home(fake_ddb):
    code, body = call(api_event("GET", "/me/home", role="dealer_parts"))
    assert code == 200 and body["url"] == "/dealer/service/parts"
