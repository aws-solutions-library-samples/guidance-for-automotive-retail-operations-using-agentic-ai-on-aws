"""Admin Console — versioned config editors, hard rules, "config without redeploy" (#24)."""
from __future__ import annotations

import pytest

from app.services.admin import config_store
from app.services.agents import suggestions
from app.services.dealer import notifications
from app.services.events import bus
from app.services.oem import guardrails
from tests.app.conftest import api_event, call

ADMIN = dict(user="U-US-00051", role="admin_tenant", is_oem=True)


def _rules(**overrides):
    rows = config_store.get("guardrails", "us")["content"]["rules"]
    return [{**r, **({"action": overrides[r["rule_id"]]} if r["rule_id"] in overrides else {})} for r in rows]


def test_guardrail_edit_takes_effect_next_invocation_and_rolls_back(fake_ddb):
    text = "What is the legal interpretation of the franchise law here?"
    before = guardrails.check_guardrails(text, {"log": False})
    v1 = config_store.get("guardrails", "us")["active_version"]
    # tighten GR-006 from require_approval to block -> next check blocks (no redeploy)
    pub = config_store.publish("guardrails", "us", "U-US-00051", content={"rules": _rules(**{"GR-006": "block"})})
    assert pub["version"] == v1 + 1 and pub["changes"]
    assert before["outcome"] == "require_approval"
    after = guardrails.check_guardrails(text, {"log": False})
    assert after["outcome"] == "block"
    assert any(e["type"] == "config.published@v1" for e in bus.EMITTED)
    # rollback republishes the old content as a new version (history is append-only)
    rb = config_store.rollback("guardrails", "us", v1, "U-US-00051")
    assert rb["version"] == v1 + 2 and rb["rolled_back_to"] == v1
    assert guardrails.check_guardrails(text, {"log": False})["outcome"] == before["outcome"]
    hist = config_store.get("guardrails", "us")["history"]
    assert [h["is_active"] for h in hist].count(True) == 1


def test_guardrail_hard_rules_cannot_be_relaxed_or_removed(fake_ddb):
    with pytest.raises(config_store.HardRuleViolation):
        config_store.validate("guardrails", {"rules": _rules(**{"GR-001": "warn"})})
    with pytest.raises(config_store.HardRuleViolation):
        config_store.validate("guardrails", {"rules": [r for r in _rules() if r["rule_id"] != "GR-003"]})
    code, body = call(api_event("PUT", "/config/guardrails", **ADMIN,
                                body={"content": {"rules": _rules(**{"GR-002": "warn"})}}))
    assert code == 422


def test_draft_is_never_live(fake_ddb):
    text = "What is the legal interpretation of the franchise law here?"
    before = guardrails.check_guardrails(text, {"log": False})["outcome"]
    code, d = call(api_event("PUT", "/config/guardrails", **ADMIN,
                             body={"content": {"rules": _rules(**{"GR-006": "block", "GR-007": "block"})}}))
    assert code == 200 and d["diff"]
    assert guardrails.check_guardrails(text, {"log": False})["outcome"] == before
    got = config_store.get("guardrails", "us")
    assert got["draft"] and got["draft"]["diff"]
    code, pub = call(api_event("POST", "/config/guardrails/publish", **ADMIN, body={"note": "tighten legal"}))
    assert code == 200 and config_store.get("guardrails", "us")["draft"] is None


def test_suggestion_threshold_change_changes_behaviour(fake_ddb):
    j = next(x for x in fake_ddb.rows("DEALER_Journeys") if x["journey_id"] == "J-US-J1")
    base = suggestions.generate_contextual_suggestions(j)
    cfg = config_store.get("suggestions", "us")["content"]
    config_store.publish("suggestions", "us", "admin", content={**cfg, "confidence_threshold": 0.99})
    hi = suggestions.generate_contextual_suggestions(j)
    assert hi["confidence_threshold"] == 0.99
    assert len(hi["suggestions"]) <= len(base["suggestions"])
    assert all(s["confidence"] >= 0.99 for s in hi["suggestions"])


def test_suggestion_preview_compares_draft(fake_ddb):
    cfg = config_store.get("suggestions", "us")["content"]
    code, out = call(api_event("POST", "/config/suggestions/preview", **ADMIN,
                               body={"journey_id": "J-US-J1", "draft": {**cfg, "confidence_threshold": 0.0}}))
    assert code == 200 and "active" in out and "draft" in out
    assert len(out["draft"]["suggestions"]) >= len(out["active"]["suggestions"])


def test_notification_rule_publish_routes_next_event(fake_ddb):
    cfg = config_store.get("notifications", "us")["content"]
    rules = [r for r in cfg["rules"] if r["event"] != "vehicle.ready"] + [
        {"event": "vehicle.ready", "roles": ["dealer_manager"], "channels": ["push"], "quiet_hours_respect": False}]
    config_store.publish("notifications", "us", "admin", content={"rules": rules})
    out = notifications.notify("vehicle.ready", dealer_id="D-101", market="us")
    assert out["roles"] == ["dealer_manager"] and out["delivered"] >= 1
    with pytest.raises(config_store.InvalidConfig):
        config_store.validate("notifications", {"rules": [{"event": "x", "roles": ["wizard"], "channels": ["push"]}]})


def test_flags_versioned_and_validated(fake_ddb):
    code, d = call(api_event("GET", "/admin/flags", **ADMIN))
    assert code == 200 and d["content"]["flags"]["ENABLE_CONNECT"] is False and d["is_default"]
    code, _ = call(api_event("POST", "/admin/flags/publish", **ADMIN,
                             body={"content": {"flags": {"ENABLE_QUICK_EMBED": True}}}))
    assert code == 200 and config_store.flags("us")["ENABLE_QUICK_EMBED"] is True
    code, _ = call(api_event("PUT", "/admin/flags", **ADMIN, body={"content": {"flags": {"MAKE_IT_FAST": True}}}))
    assert code == 400


def test_guardrail_harness_and_sweep(fake_ddb):
    code, out = call(api_event("POST", "/config/guardrails/test", **ADMIN,
                               body={"text": "Tell the dealer to cut his wholesale order by 20 units",
                                     "draft_rules": _rules()}))
    assert code == 200 and "active" in out and "draft" in out
    assert out["active"]["logged_event_ids"] == []      # harness never logs
    code, sw = call(api_event("POST", "/config/guardrails/sweep", **ADMIN, body={"prompts": [
        {"text": "Summarize Decatur's CSI trend for the last quarter", "expect": "allow"},
        {"text": "Promise the dealer 30 extra allocation units next month", "expect": "block"}]}))
    assert code == 200 and sw["total"] == 2 and sw["false_positives"] == 0


def test_config_routes_require_tenant_admin(fake_ddb):
    assert call(api_event("GET", "/config/guardrails", role="dealer_manager", dealer_ids="D-101"))[0] == 403
    assert call(api_event("GET", "/config/guardrails", user="U-US-00052", role="admin_dealer", dealer_ids="D-101"))[0] == 403
    assert call(api_event("GET", "/config/nonsense", **ADMIN))[0] == 404
