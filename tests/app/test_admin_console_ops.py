"""Admin Console — users/org/market scope, connectors, agents panel, audit,
data requests (#25), entity merges, approvals inbox, notifications."""
from __future__ import annotations

from app.services.admin import data_requests
from app.services.events import bus
from tests.app.conftest import api_event, call

ADMIN = dict(user="U-US-00051", role="admin_tenant", is_oem=True)
DADMIN = dict(user="U-US-00052", role="admin_dealer", dealer_ids="D-101")


def test_users_scope_and_secrets(fake_ddb):
    code, t = call(api_event("GET", "/admin/users", **ADMIN))
    assert code == 200 and t["count"] >= 50 and len(t["groups"]) == 19
    assert all("test_password" not in u for u in t["users"])
    code, d = call(api_event("GET", "/admin/users", **DADMIN))
    assert code == 200 and d["count"] < t["count"] and all(u["dealer_id"] == "D-101" for u in d["users"])


def test_dealer_admin_cannot_grant_oem_or_leave_store(fake_ddb):
    assert call(api_event("PATCH", "/admin/users/U-US-00003", **DADMIN, body={"groups": ["oem_dm"]}))[0] == 403
    assert call(api_event("PATCH", "/admin/users/U-US-00003", **DADMIN, body={"scope_node_id": "D-102"}))[0] == 403
    assert call(api_event("PATCH", "/admin/users/U-US-00011", **DADMIN, body={"active": False}))[0] == 403
    code, u = call(api_event("PATCH", "/admin/users/U-US-00003", **DADMIN, body={"groups": ["dealer_bdc"]}))
    assert code == 200 and u["groups"] == ["dealer_bdc"]
    assert call(api_event("PATCH", "/admin/users/U-US-00052", **DADMIN, body={"active": False}))[0] == 400


def test_tenant_admin_moves_user_and_derives_scope(fake_ddb):
    code, u = call(api_event("PATCH", "/admin/users/U-US-00003", **ADMIN, body={"scope_node_id": "D-105"}))
    assert code == 200 and u["dealer_id"] == "D-105" and u["district_id"]
    assert call(api_event("PATCH", "/admin/users/U-US-00003", **ADMIN, body={"groups": ["wizard"]}))[0] == 400


def test_org_tree_and_edits(fake_ddb):
    code, tree = call(api_event("GET", "/admin/org", **ADMIN))
    assert code == 200 and tree["editable"] and tree["roots"][0]["node_id"] == "OEM"
    code, d = call(api_event("GET", "/admin/org", **DADMIN))
    assert code == 200 and not d["editable"] and d["count"] < tree["count"]
    assert call(api_event("POST", "/admin/org", **DADMIN, body={"node_id": "D-199"}))[0] == 403
    code, n = call(api_event("POST", "/admin/org", **ADMIN,
                             body={"node_id": "D-199", "type": "dealer", "parent_id": "DG-01", "name": "New Store", "pma": "PMA-NEW"}))
    parent_district = next(x for x in fake_ddb.rows("PLAT_OrgHierarchy") if x["node_id"] == "DG-01")["parent_id"]
    assert code == 201 and n["district_id"] == parent_district and n["dealer_group_id"] == "DG-01"
    assert call(api_event("POST", "/admin/org", **ADMIN, body={"node_id": "D-198", "type": "dealer", "parent_id": "OEM"}))[0] == 400
    assert call(api_event("PATCH", "/admin/org/REG-SE", **ADMIN, body={"parent_id": "DIST-07"}))[0] == 400


def test_market_edit_validates(fake_ddb):
    code, m = call(api_event("GET", "/admin/market", **ADMIN, query={"market_id": "us"}))
    assert code == 200 and len(m["state_rules"]) == 51
    code, out = call(api_event("PUT", "/admin/market", **ADMIN,
                               body={"market_id": "us", "brand": {"colors": {"primary": "#112233"}}}))
    assert code == 200 and out["market"]["brand"]["colors"]["primary"] == "#112233"
    assert call(api_event("PUT", "/admin/market", **ADMIN, body={"market_id": "us", "brand": {"colors": {"primary": "red"}}}))[0] == 400
    assert call(api_event("PUT", "/admin/market", **DADMIN, body={"market_id": "us"}))[0] == 403
    code, sr = call(api_event("PUT", "/admin/market/state-rules/GA", **ADMIN, body={"market_id": "us", "sales_tax_pct": 4.5}))
    assert code == 200 and sr["sales_tax_pct"] == 4.5


def test_connectors_health_and_live_mode(fake_ddb):
    code, c = call(api_event("GET", "/admin/connectors", **ADMIN))
    assert code == 200 and len(c["connectors"]) >= 20
    assert all("secret" not in str(x.get("credential_ref", "")).lower() or "/" in x["credential_ref"] for x in c["connectors"])
    code, t = call(api_event("POST", "/admin/connectors/dms/test", **ADMIN))
    assert code == 200 and t["status"] == "ok"
    reg = c["connectors"]
    for x in reg:
        if x["connector_id"] == "crm":
            x["mode"] = "live"
    code, _ = call(api_event("POST", "/config/connectors/publish", **ADMIN, body={"content": {"connectors": reg}}))
    assert code == 200
    code, t = call(api_event("POST", "/admin/connectors/crm/test", **ADMIN))
    assert t["status"] == "not_configured"
    bad = [{**reg[0], "credential_ref": "password=hunter2"}] + reg[1:]
    assert call(api_event("PUT", "/config/connectors", **ADMIN, body={"content": {"connectors": bad}}))[0] == 400


def test_agents_panel(fake_ddb):
    code, h = call(api_event("GET", "/admin/agents/health", **ADMIN))
    assert code == 200 and len(h["agents"]) == 7
    a7 = next(a for a in h["agents"] if a["agent_id"] == "A7")
    assert a7["invocations"] > 0 and a7["latency_p95_ms"] >= a7["latency_p50_ms"]
    assert any(x["action_id"] == "a7.stage_parts" and x["level"] for x in a7["actions"])
    a5 = next(a for a in h["agents"] if a["agent_id"] == "A5")
    assert any(x["action_id"] == "a5.shift_budget" and x["hard_rule"] for x in a5["actions"])


def test_audit_and_export(fake_ddb):
    call(api_event("PATCH", "/admin/users/U-US-00003", **ADMIN, body={"active": True}))
    code, a = call(api_event("GET", "/admin/audit", **ADMIN, query={"kind": "admin_actions"}))
    assert code == 200 and any(r["tool"] == "user.update" for r in a["rows"])
    code, g = call(api_event("GET", "/admin/audit/export", **ADMIN, query={"kind": "guardrail_events"}))
    assert code == 200 and g["csv"].splitlines()[0].startswith("evaluated_at,rule_id")
    assert call(api_event("GET", "/admin/audit", **ADMIN, query={"kind": "everything"}))[0] == 400


def test_data_request_download_excludes_c4_and_delete_needs_confirm(fake_ddb):
    code, req = call(api_event("POST", "/admin/data-requests", **ADMIN, body={"customer_id": "C-US-J1", "type": "download"}))
    assert code == 201 and req["status"] == "received"
    assert call(api_event("POST", "/admin/data-requests", **ADMIN, body={"customer_id": "C-US-J1", "type": "download"}))[0] == 409
    code, out = call(api_event("POST", f"/admin/data-requests/{req['request_id']}/run", **ADMIN))
    assert code == 200 and out["status"] == "completed"
    export = out["export"]
    assert export["row_count"] > 0
    for rows in export["tables"].values():
        for r in rows:
            assert not (set(r) & data_requests.C4_FIELDS)
    assert call(api_event("POST", f"/admin/data-requests/{req['request_id']}/run", **ADMIN))[0] == 409

    code, dreq = call(api_event("POST", "/admin/data-requests", **ADMIN, body={"customer_id": "C-US-00007", "type": "delete"}))
    code, _ = call(api_event("POST", f"/admin/data-requests/{dreq['request_id']}/run", **ADMIN))
    assert code == 400
    code, done = call(api_event("POST", f"/admin/data-requests/{dreq['request_id']}/run", **ADMIN, body={"confirm": "C-US-00007"}))
    assert code == 200 and done["status"] == "completed" and done["pending_external_steps"] >= 2
    cust = next(c for c in fake_ddb.rows("DEALER_Customers") if c["customer_id"] == "C-US-00007")
    consent = next(c for c in fake_ddb.rows("CUST_Consent") if c["customer_id"] == "C-US-00007")
    assert consent["email_opt_in"] is False and consent["data_requests"][-1]["status"] == "completed"
    assert cust["email"] == "[erased]" and cust["phone"] == "[erased]" and "income_band" not in cust
    assert any(s["system"] == "Amazon Personalize" and s["status"] == "pending_external" for s in done["steps"])
    code, lst = call(api_event("GET", "/admin/data-requests", **ADMIN))
    assert code == 200 and lst["count"] >= 2 and "export" not in lst["requests"][0]


def test_entity_merge_queue(fake_ddb):
    code, q = call(api_event("GET", "/admin/entities", **ADMIN))
    assert code == 200 and q["pending"] >= 10
    it = q["items"][0]
    code, d = call(api_event("POST", "/admin/entities/decide", **ADMIN,
                             body={"item_key": it["item_key"], "system": it["system"], "decision": "reject", "reason": "different person"}))
    assert code == 200 and d["merge_status"] == "rejected" and d["duplicate_of"] is None
    assert call(api_event("POST", "/admin/entities/decide", **ADMIN,
                          body={"item_key": it["item_key"], "system": it["system"], "decision": "approve"}))[0] == 409


def test_approvals_inbox_rules(fake_ddb):
    mgr = dict(user="U-US-00001", role="dealer_manager", dealer_ids="D-101")
    adv = dict(user="U-US-00002", role="dealer_advisor", dealer_ids="D-101")
    seeded_pending = call(api_event("GET", "/approvals", **ADMIN, query={"status": "pending"}))[1]["pending"]
    assert seeded_pending >= 1                       # the seed leaves real, decidable requests in the inbox
    code, a = call(api_event("POST", "/approvals", **adv, body={"type": "deal_exception", "scope": {"dealer_id": "D-101"},
                                                                 "payload": {"deal_id": "DL-1", "discount_pct": 4}}))
    assert code == 201 and a["status"] == "pending"
    assert any(e["type"] == "approval.requested@v1" for e in bus.EMITTED)
    assert call(api_event("POST", f"/approvals/{a['approval_id']}/decide", **adv, body={"decision": "approve"}))[0] == 403
    other = dict(user="U-US-00010", role="dealer_manager", dealer_ids="D-102")
    assert call(api_event("POST", f"/approvals/{a['approval_id']}/decide", **other, body={"decision": "approve"}))[0] == 403
    assert call(api_event("POST", f"/approvals/{a['approval_id']}/decide", **mgr, body={"decision": "reject"}))[0] == 400
    code, d = call(api_event("POST", f"/approvals/{a['approval_id']}/decide", **mgr, body={"decision": "approve"}))
    assert code == 200 and d["status"] == "approved" and d["applied"] is False
    code, inbox = call(api_event("GET", "/approvals", **mgr))
    assert code == 200 and any(x["approval_id"] == a["approval_id"] for x in inbox["approvals"])
    code, adm = call(api_event("GET", "/approvals", **ADMIN, query={"status": "pending"}))
    # the seed's pending requests (its budget shift is decided history) + the one above, now decided
    assert code == 200 and adm["pending"] == seeded_pending and all(x["can_decide"] for x in adm["approvals"])


def test_notifications_read_all(fake_ddb):
    staff = {u["user_id"] for u in fake_ddb.rows("PLAT_Users") if u.get("dealer_id") == "D-101"}
    uid = next(n["user_id"] for n in fake_ddb.rows("DEALER_Notifications") if n["user_id"] in staff and not n.get("read"))
    code, out = call(api_event("POST", "/notifications/read-all", user=uid, dealer_ids="D-101"))
    assert code == 200 and out["marked"] >= 1
    code, inbox = call(api_event("GET", "/notifications", user=uid, dealer_ids="D-101"))
    assert code == 200 and inbox["unread_count"] == 0
    assert all(n["resource_url"] is None or n["resource_url"].startswith("/r/") for n in inbox["notifications"])


def test_overview_tenant_vs_store(fake_ddb):
    code, t = call(api_event("GET", "/admin/overview", **ADMIN))
    assert code == 200 and "connectors" in t and "flags" in t
    code, s = call(api_event("GET", "/admin/overview", **DADMIN))
    assert code == 200 and s["scope"] == "store" and "connectors" not in s
    assert call(api_event("GET", "/admin/overview", role="dealer_manager", dealer_ids="D-101"))[0] == 403


def test_admin_sees_customer_filed_request(fake_ddb):
    from app.services.customer import portal
    req = portal.request_data("C-US-J2", "download")
    code, lst = call(api_event("GET", "/admin/data-requests", **ADMIN))
    assert code == 200 and any(r["request_id"] == req["request_id"] and r["channel"] == "my_garage" for r in lst["requests"])
    code, out = call(api_event("POST", f"/admin/data-requests/{req['request_id']}/run", **ADMIN))
    assert code == 200 and out["status"] == "completed"
    consent = next(c for c in fake_ddb.rows("CUST_Consent") if c["customer_id"] == "C-US-J2")
    assert next(r for r in consent["data_requests"] if r["request_id"] == req["request_id"])["status"] == "completed"
