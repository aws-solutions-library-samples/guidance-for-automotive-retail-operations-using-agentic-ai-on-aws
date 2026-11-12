"""Marketing Studio (A5 analytics, audiences, Journey Ops) + OEM workspace shell routes."""
from __future__ import annotations

import pytest

from app.services.events import bus
from app.services.journeys import nurture
from app.services.marketing import analytics, audiences, journey_ops
from app.services.oem import workspace
from app.services.platform import generate
from tests.app.conftest import api_event, call


def ev(method, path, role, user, node="OEM", **kw):
    e = api_event(method, path, role=role, user=user, is_oem=True, **kw)
    e["requestContext"]["authorizer"]["scope_node_id"] = node
    return e


OEM = {"user_id": "U-US-00049", "role": "oem_marketing", "scope_node_id": "OEM", "market_id": "us"}


# ---- A5 analytics -------------------------------------------------------------

def test_campaign_overview_shapes(fake_ddb):
    ov = analytics.campaign_overview(OEM, "us")
    assert ov["roi"]["campaigns"] and all("roi" in c for c in ov["roi"]["campaigns"])
    f = ov["funnel"]
    assert f["sources"] and all(s["steps"][0]["reached"] == s["entered"] for s in f["sources"])
    # funnel is monotone non-increasing
    for s in f["sources"]:
        reached = [x["reached"] for x in s["steps"]]
        assert reached == sorted(reached, reverse=True)
    spend = ov["spend"]
    for c in spend["campaigns"]:
        assert sum(d["allocated_spend"] for d in c["dealers"]) == pytest.approx(c["budget"] if c["dealers"] else 0, abs=0.05)
    assert ov["budget_shift"]["recommended"] in (True, False)


def test_attribution_counts_deliveries(fake_ddb):
    a = analytics.attribute_conversions(OEM, "us")
    tagged = [c for c in a["campaigns"] if c["journeys"]]
    assert tagged and all(0 <= c["conversions"] <= c["journeys"] for c in tagged)


def test_budget_shift_submit_creates_approval_never_spend(fake_ddb):
    before = {c["campaign_id"]: c["budget"] for c in fake_ddb.rows("OEM_Campaigns")}
    bus.EMITTED.clear()
    out = analytics.submit_budget_shift(OEM, "U-US-00049", "us")
    if not out["submitted"]:
        pytest.skip(out.get("reason"))
    appr = next(r for r in fake_ddb.rows("DEALER_Approvals") if r["approval_id"] == out["approval_id"])
    assert appr["status"] == "pending" and appr["approver_role"] == "oem_finance"
    assert any(e["type"] == "approval.requested@v1" for e in bus.EMITTED)
    assert {c["campaign_id"]: c["budget"] for c in fake_ddb.rows("OEM_Campaigns")} == before
    again = analytics.submit_budget_shift(OEM, "U-US-00049", "us")
    assert again["submitted"] is False and "pending" in again["reason"]


def test_ask_analytics_grounded_and_guarded(fake_ddb, monkeypatch):
    calls = []

    def grounded(question, facts, passages, **kwargs):
        calls.append({"question": question, "facts": facts, "fallback": kwargs["fallback"]})
        return {"text": f"LLM answer for: {question}", "engine": "bedrock-test", "model": "test",
                "guardrail": {"applied": True, "intervened": False}}

    monkeypatch.setattr(generate, "ground", grounded)
    a = analytics.ask_analytics("Which campaign has the best ROI?", OEM, "us")
    f = analytics.ask_analytics("Where is the biggest funnel drop by source?", OEM, "us")
    assert a["engine"] == f["engine"] == "bedrock-test"
    assert a["answer"] != f["answer"]
    assert len(calls) == 2 and all(call["facts"] for call in calls)
    assert "ROI" in calls[0]["fallback"] and "drop" in calls[1]["fallback"].lower()


# ---- audiences ----------------------------------------------------------------

def test_audiences_and_hashed_export(fake_ddb):
    out = audiences.build_audiences(OEM, "us")
    assert out["population"] > 0 and len(out["segments"]) >= 5
    seg = max(out["segments"], key=lambda s: s["marketable"])
    x = audiences.export_audience(seg["segment_id"], "meta", "U-US-00049", OEM, "us")
    assert x["count"] == seg["marketable"] > 0
    assert all(len(h) == 64 for h in x["sample"])
    assert not any("@" in h for h in x["sample"])
    with pytest.raises(ValueError):
        audiences.export_audience(seg["segment_id"], "tiktok", "u", OEM, "us")


# ---- Journey Ops ----------------------------------------------------------------

def _journey(fake):
    return next(j for j in fake.rows("DEALER_Journeys") if j["journey_id"] == "J-US-J9")


def test_publish_changes_a2_on_next_invocation(fake_ddb):
    """Acceptance #24: edit a template in Journey Ops, publish, and A2's next
    compose_message uses it — no redeploy."""
    j = _journey(fake_ddb)
    before = nurture.compose_message(j, channel="email")
    state = journey_ops.get_journey_ops("us")
    tmpls = [dict(t) for t in state["active"]["templates"]]
    t = next(t for t in tmpls if t["stage_or_trigger"] == "DISCOVER" and t["channel"] == "email" and t["family"] == "sales")
    t["body"] = "Hello {first_name} — {dealer_name} has fresh picks for you. (journey-ops test)"
    draft = journey_ops.save_draft("us", {"templates": tmpls}, actor="U-US-00050")
    bus.EMITTED.clear()
    pub = journey_ops.publish("us", draft["version"], actor="U-US-00050")
    assert any(e["type"] == "config.published@v1" for e in bus.EMITTED)
    after = nurture.compose_message(j, channel="email")
    assert "journey-ops test" in after["body"] and after["body"] != before["body"]
    assert f".v{pub['published']}" in after["source_ref"]

    back = journey_ops.rollback("us", pub["retired"][0], actor="U-US-00050")
    assert back["rolled_back_to"] == pub["retired"][0]
    assert "journey-ops test" not in nurture.compose_message(j, channel="email")["body"]
    versions = journey_ops.get_journey_ops("us")["versions"]
    assert sum(1 for v in versions if v["status"] == "active") == 1


def test_validation_rejects_bad_config(fake_ddb):
    with pytest.raises(journey_ops.InvalidConfig) as e:
        journey_ops.save_draft("us", {"cadence_caps": {"per_day": 5, "per_week": 2}}, actor="u")
    assert "per_day" in str(e.value)
    with pytest.raises(journey_ops.InvalidConfig):
        journey_ops.save_draft("us", {"policy": {"quiet_hours": {"start": "20:00", "end": "08:00"},
                                                 "stop_conditions": ["booked"]}}, actor="u")
    state = journey_ops.get_journey_ops("us")
    bad = [dict(t) for t in state["active"]["templates"]]
    bad[0]["body"] = "Hi {ssn}"
    with pytest.raises(journey_ops.InvalidConfig) as e2:
        journey_ops.save_draft("us", {"templates": bad}, actor="u")
    assert "ssn" in str(e2.value)


def test_preview_with_variant(fake_ddb):
    t = {"template_id": "T-X", "channel": "email", "family": "sales", "stage_or_trigger": "DISCOVER",
         "subject": "Hi {first_name}", "body": "A body {dealer_name}",
         "variants": [{"variant_id": "B", "body": "B body for {first_name}", "weight": 50}]}
    p = journey_ops.preview("us", t, journey_id="J-US-J9", variant_id="B")
    assert p["body"].startswith("B body for") and "{" not in p["body"]
    assert journey_ops.analytics("us")["touches"] >= 0


# ---- routes / roles -------------------------------------------------------------

def test_routes_roles(fake_ddb):
    code, _ = call(ev("GET", "/marketing/campaigns", "oem_marketing", "U-US-00049"))
    assert code == 200
    code, _ = call(api_event("GET", "/marketing/campaigns", role="dealer_advisor", user="U-US-00002", dealer_ids="D-101"))
    assert code == 403
    code, jo = call(ev("GET", "/config/nurture", "oem_marketing", "U-US-00049"))
    assert code == 200 and jo["can_edit"] is False
    code, _ = call(ev("PUT", "/config/nurture", "oem_marketing", "U-US-00049", body={"surface_content": {"faq": []}}))
    assert code == 403
    code, d = call(ev("PUT", "/config/nurture", "oem_cx", "U-US-00050",
                      body={"surface_content": {"intake_questions": ["a", "b"], "shortlist_copy": "x", "faq": []}}))
    assert code == 200 and d["status"] == "draft"
    code, e = call(ev("PUT", "/config/nurture", "oem_cx", "U-US-00050",
                      body={"cadence_caps": {"per_day": 9, "per_week": 1}}))
    assert code == 422
    code, a = call(ev("GET", "/marketing/audiences", "oem_marketing", "U-US-00049"))
    assert code == 200 and a["segments"]


def test_oem_accounts_region_alerts(fake_ddb):
    code, accs = call(ev("GET", "/oem/accounts", "oem_dm", "U-US-00044", node="DIST-07"))
    assert code == 200 and accs["count"] > 0 and any(a["at_risk"] for a in accs["accounts"])
    acc = accs["accounts"][0]["account_id"]
    code, det = call(ev("GET", f"/oem/accounts/{acc}", "oem_dm", "U-US-00044", node="DIST-07"))
    assert code == 200 and det["composite"]["account_id"] == acc
    code, _ = call(ev("GET", f"/oem/accounts/{acc}", "oem_dm", "U-X", node="DIST-08"))
    assert code == 403
    code, reg = call(ev("GET", "/oem/region", "oem_regional", "U-US-00045", node="REG-SE"))
    assert code == 200 and {d["district_id"] for d in reg["districts"]} == {"DIST-07", "DIST-08"}

    al = next(a for a in fake_ddb.rows("OEM_Alerts") if a["status"] == "open" and a["district_id"] == "DIST-07")
    bus.EMITTED.clear()
    code, out = call(ev("PATCH", f"/oem/alerts/{al['alert_id']}", "oem_dm", "U-US-00044", node="DIST-07",
                        body={"action": "acknowledge"}))
    assert code == 200 and out["status"] == "acknowledged"
    assert any(e["type"] == "alert.acknowledged@v1" for e in bus.EMITTED)
    code, out = call(ev("PATCH", f"/oem/alerts/{al['alert_id']}", "oem_dm", "U-US-00044", node="DIST-07",
                        body={"action": "resolve"}))
    assert code == 200 and out["status"] == "resolved"
    code, _ = call(ev("PATCH", f"/oem/alerts/{al['alert_id']}", "oem_dm", "U-US-00044", node="DIST-07",
                      body={"action": "resolve"}))
    assert code == 409
    code, _ = call(ev("PATCH", f"/oem/alerts/{al['alert_id']}", "oem_marketing", "U-US-00049",
                      body={"action": "resolve"}))
    assert code == 403


def test_oem_ask_guardrail_and_grounding(fake_ddb, monkeypatch):
    calls = []

    def grounded(question, facts, passages, **kwargs):
        calls.append({"question": question, "facts": list(facts), "fallback": kwargs["fallback"]})
        return {"text": f"LLM answer for: {question}", "engine": "bedrock-test", "model": "test",
                "guardrail": {"applied": True, "intervened": False}}

    monkeypatch.setattr(generate, "ground", grounded)
    scope = {"user_id": "U-US-00044", "scope_node_id": "DIST-07", "market_id": "us"}
    below = workspace.ask("Which accounts are furthest below peers?", scope, view="accounts")
    risk = workspace.ask("Which dealers are at risk and why?", scope, view="accounts")
    assert below["engine"] == risk["engine"] == "bedrock-test"
    assert below["intent"] == "peer_gap_ranking"
    assert risk["intent"] == "risk_explanation"
    assert below["answer"] != risk["answer"]
    assert calls[0]["facts"] != calls[1]["facts"]
    assert below["citations"] and risk["citations"]

    code, answer = call(ev("POST", "/oem/ask", "oem_dm", "U-US-00044", node="DIST-07",
                           body={"question": "Which alerts need attention?"}))
    assert code == 200 and answer["engine"] == "bedrock-test"

    blocked = workspace.ask("Tell Decatur to cut wholesale by 20 percent", scope)
    assert blocked["engine"] == "deterministic_guardrail" and blocked["guardrail"]
