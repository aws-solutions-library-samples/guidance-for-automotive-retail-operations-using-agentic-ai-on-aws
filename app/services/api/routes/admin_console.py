"""Admin Console, approvals inbox, and notification routes (spec §5.17, §4.8, §5.18.4, §6).

Admin (admin_tenant everywhere; admin_dealer store-scoped: overview, users, org read):
  GET   /admin/overview
  GET   /admin/users                    PATCH /admin/users/{user_id}
  GET   /admin/org                      POST  /admin/org        PATCH /admin/org/{node_id}
  GET   /admin/market?market_id=        PUT   /admin/market     PUT /admin/market/state-rules/{code}
  GET   /admin/connectors               POST  /admin/connectors/{id}/test | /admin/connectors/{id}/rotate
  GET/PUT /admin/flags                  POST  /admin/flags/publish         (alias of /config/flags)
  GET/PUT/DELETE /config/{kind}         POST  /config/{kind}/publish | /rollback   GET /config/{kind}/versions
        kind ∈ guardrails | suggestions | notifications | flags | connectors | tenant
  POST  /config/guardrails/test         POST  /config/guardrails/sweep     POST /config/suggestions/preview
  GET   /admin/autonomy/history?action_id=
  GET   /admin/agents/health?agent=
  GET   /admin/audit?kind=&since=&until=&q=     GET /admin/audit/export (csv)
  GET   /admin/data-requests            POST  /admin/data-requests  POST|PATCH /admin/data-requests/{id}/run
  GET   /admin/entities?status=         POST|PATCH /admin/entities/decide
Tenant admin, dev demo stack only:
  GET   /admin/demo                     POST  /admin/demo/reset {confirm:"reset"}   (202: started)
Any staff:
  GET   /approvals?status=              POST  /approvals            POST /approvals/{id}/decide
Any signed-in user:
  POST  /notifications/read-all
"""
from __future__ import annotations

import re
from typing import Any

from ...admin import (agents_health, audit_log, autonomy, config_store, connectors, data_requests, demo_data,
                      directory, entities, harness)
from ...approvals import generic as approvals
from ...common import authz, personas
from ...dealer import notifications as notif
from ...oem import guardrails
from .. import routing as r

_DEALER_ADMIN_OK = frozenset({"overview", "users", "org_read", "approvals"})


def _admin(claims: dict[str, Any], what: str = "") -> None:
    role = claims.get("role")
    if role not in authz.ADMIN_ROLES:
        raise r.Bad(403, f"role {role!r} may not use the Admin Console")
    if role == "admin_dealer" and what not in _DEALER_ADMIN_OK:
        raise r.Bad(403, "dealer admins have a store-scoped subset of the Admin Console")


def _market(event: dict[str, Any], claims: dict[str, Any], body: dict[str, Any] | None = None) -> str:
    return (body or {}).get("market_id") or r.qs(event).get("market_id") or r.qs(event).get("market") \
        or claims.get("market_id") or "us"


# --- overview -------------------------------------------------------------------

def _overview(event, claims, _m):
    _admin(claims, "overview")
    market = _market(event, claims)
    tenant = claims.get("role") in authz.TENANT_ADMIN_ROLES
    users = directory.list_users(claims)
    out: dict[str, Any] = {"market_id": market, "scope": "tenant" if tenant else "store",
                           "users": {"count": users["count"], "by_group": users["by_group"],
                                     "group_labels": {g: personas.display_name(g) for g in users["by_group"]},
                                     "inactive": sum(1 for u in users["users"] if u.get("active") is False)},
                           "approvals": {k: v for k, v in approvals.inbox(claims).items() if k in ("pending", "mine_to_decide")}}
    if tenant:
        out["connectors"] = connectors.list_connectors(market)["summary"]
        out["flags"] = config_store.flags(market)
        out["data_requests"] = {"pending": data_requests.list_requests()["pending"]}
        out["guardrail_blocks_7d"] = guardrails.recent_blocks(market)
        out["entities_pending"] = entities.queue(market=market)["pending"]
        out["recent_admin_actions"] = [{k: a.get(k) for k in ("ts", "actor", "tool", "target", "outcome")}
                                       for a in audit_log.recent(10)]
    return 200, out


# --- users / org / market --------------------------------------------------------

def _users(event, claims, _m):
    _admin(claims, "users")
    return 200, directory.list_users(claims)


def _user_patch(event, claims, m):
    _admin(claims, "users")
    return 200, directory.update_user(m.group(1), r.body(event), claims)


def _org(event, claims, _m):
    _admin(claims, "org_read")
    return 200, directory.org_tree(claims)


def _org_create(event, claims, _m):
    _admin(claims, "org_write")
    return 201, directory.create_node(r.body(event), claims)


def _org_patch(event, claims, m):
    _admin(claims, "org_write")
    return 200, directory.update_node(m.group(1), r.body(event), claims)


def _market_get(event, claims, _m):
    _admin(claims, "market")
    return 200, directory.get_market(_market(event, claims))


def _market_put(event, claims, _m):
    _admin(claims, "market")
    b = r.body(event)
    r.require(b, "market_id")
    return 200, directory.update_market(b["market_id"], b, claims)


def _state_rule_put(event, claims, m):
    _admin(claims, "market")
    b = r.body(event)
    return 200, directory.update_state_rule(_market(event, claims, b), m.group(1), b, claims)


# --- connectors / flags / config ---------------------------------------------------

def _connectors(event, claims, _m):
    _admin(claims, "connectors")
    return 200, connectors.list_connectors(_market(event, claims))


def _connector_test(event, claims, m):
    _admin(claims, "connectors")
    return 200, connectors.test_connection(m.group(1), _market(event, claims, r.body(event)), claims["user_id"])


def _connector_rotate(event, claims, m):
    """Mark a credential rotation requested on the registry entry (§5.17)."""
    _admin(claims, "connectors")
    return 200, connectors.request_rotation(m.group(1), _market(event, claims, r.body(event)), claims["user_id"])


def _cfg_get(event, claims, m):
    _admin(claims, "config")
    return 200, config_store.get(m.group(1), _market(event, claims))


def _cfg_versions(event, claims, m):
    _admin(claims, "config")
    return 200, {"kind": m.group(1), "versions": config_store.get(m.group(1), _market(event, claims))["history"]}


def _cfg_put(event, claims, m):
    _admin(claims, "config")
    b = r.body(event)
    content = b.get("content", b)
    return 200, config_store.save_draft(m.group(1), _market(event, claims, b), content, claims["user_id"])


def _cfg_discard(event, claims, m):
    _admin(claims, "config")
    config_store.discard_draft(m.group(1), _market(event, claims), claims["user_id"])
    return 200, {"discarded": True}


def _cfg_publish(event, claims, m):
    _admin(claims, "config")
    b = r.body(event)
    return 200, config_store.publish(m.group(1), _market(event, claims, b), claims["user_id"],
                                     content=b.get("content"), note=b.get("note"))


def _cfg_rollback(event, claims, m):
    _admin(claims, "config")
    b = r.body(event)
    r.require(b, "to_version")
    return 200, config_store.rollback(m.group(1), _market(event, claims, b), int(b["to_version"]), claims["user_id"])


def _flags_get(event, claims, _m):
    _admin(claims, "config")
    return 200, config_store.get("flags", _market(event, claims))


def _flags_put(event, claims, _m):
    _admin(claims, "config")
    b = r.body(event)
    return 200, config_store.save_draft("flags", _market(event, claims, b), b.get("content", b), claims["user_id"])


def _flags_publish(event, claims, _m):
    _admin(claims, "config")
    b = r.body(event)
    return 200, config_store.publish("flags", _market(event, claims, b), claims["user_id"],
                                     content=b.get("content"), note=b.get("note"))


def _gr_test(event, claims, _m):
    _admin(claims, "config")
    b = r.body(event)
    r.require(b, "text")
    draft = b.get("draft_rules")
    if draft is not None:
        draft = config_store.validate("guardrails", {"rules": draft})["rules"]
    return 200, harness.test_guardrails(b["text"], _market(event, claims, b), draft)


def _gr_sweep(event, claims, _m):
    _admin(claims, "config")
    b = r.body(event)
    prompts = b.get("prompts")
    if not isinstance(prompts, list) or not prompts:
        raise r.Bad(400, "prompts must be a non-empty list of {text, expect}")
    return 200, harness.sweep_guardrails(prompts, _market(event, claims, b))


def _sugg_preview(event, claims, _m):
    _admin(claims, "config")
    b = r.body(event)
    r.require(b, "journey_id")
    draft = b.get("draft")
    if draft is not None:
        draft = config_store.validate("suggestions", draft)
    try:
        return 200, harness.preview_suggestions(b["journey_id"], draft)
    except KeyError as e:
        raise r.Bad(404, str(e).strip("'")) from e


def _autonomy_history(event, claims, _m):
    _admin(claims, "autonomy")
    q = r.qs(event)
    if not q.get("action_id"):
        raise r.Bad(400, "query must include action_id")
    return 200, autonomy.autonomy_history(q["action_id"], _market(event, claims), q.get("scope", "tenant"),
                                          q.get("scope_id"))


# --- agents / audit -------------------------------------------------------------------

def _agents(event, claims, _m):
    _admin(claims, "agents")
    return 200, agents_health.health(_market(event, claims), agent=r.qs(event).get("agent"))


def _audit_args(event):
    q = r.qs(event)
    return dict(kind=q.get("kind", "admin_actions"), since=q.get("since"), until=q.get("until"), q=q.get("q"),
                limit=max(1, min(int(q.get("limit") or 500), 5000)))


def _audit(event, claims, _m):
    _admin(claims, "audit")
    return 200, audit_log.query(**_audit_args(event))


def _audit_export(event, claims, _m):
    _admin(claims, "audit")
    res = audit_log.query(**_audit_args(event))
    audit_log.record(claims["user_id"], "audit.export", res["kind"], {"rows": len(res["rows"])})
    return 200, {"kind": res["kind"], "rows": len(res["rows"]), "truncated": res["truncated"],
                 "filename": f"retail-ops-audit-{res['kind']}.csv", "csv": audit_log.to_csv(res)}


# --- data requests / entities -----------------------------------------------------------

def _dsr_list(event, claims, _m):
    _admin(claims, "data_requests")
    return 200, data_requests.list_requests(r.qs(event).get("status"))


def _dsr_create(event, claims, _m):
    _admin(claims, "data_requests")
    b = r.body(event)
    r.require(b, "customer_id", "type")
    try:
        return 201, data_requests.create_request(b["customer_id"], b["type"], claims["user_id"], channel="admin")
    except data_requests.NotFound as e:
        raise r.Bad(404, str(e)) from e


def _dsr_run(event, claims, m):
    _admin(claims, "data_requests")
    b = r.body(event)
    req = next((x for x in data_requests.list_requests()["requests"] if x["request_id"] == m.group(1)), None)
    if req is None:
        raise r.Bad(404, f"data request {m.group(1)} not found")
    if req["type"] == "delete" and b.get("confirm") != req["customer_id"]:
        raise r.Bad(400, "deletion is irreversible: confirm with the customer id", confirm_required=True)
    return 200, data_requests.run_request(m.group(1), claims["user_id"])


def _entities(event, claims, _m):
    _admin(claims, "entities")
    return 200, entities.queue(r.qs(event).get("status", "pending"), market=r.qs(event).get("market"))


def _entity_decide(event, claims, _m):
    _admin(claims, "entities")
    b = r.body(event)
    r.require(b, "item_key", "system", "decision")
    return 200, entities.decide(b["item_key"], b["system"], b["decision"], claims["user_id"], b.get("reason"))


# --- approvals / notifications ------------------------------------------------------------

def _approvals(event, claims, _m):
    r.staff(claims)
    return 200, approvals.inbox(claims, r.qs(event).get("status"))


def _approval_request(event, claims, _m):
    r.staff(claims)
    b = r.body(event)
    r.require(b, "type")
    scope = b.get("scope") or {}
    if scope.get("dealer_id"):
        r.dealer_in_scope(claims, scope["dealer_id"])
    # a request belongs to the requester's market; the inbox never shows it across markets
    scope = {**scope, "market_id": claims.get("market_id") or scope.get("market_id")}
    return 201, approvals.request(b["type"], b.get("payload") or {}, claims["user_id"], scope, b.get("reason"))


def _approval_decide(event, claims, m):
    r.staff(claims)
    b = r.body(event)
    r.require(b, "decision")
    out = approvals.decide(m.group(1), b["decision"], claims, b.get("reason"))
    audit_log.record(claims["user_id"], f"approval.{b['decision']}", m.group(1), {"type": out.get("type")})
    return 200, out


def _notif_read_all(event, claims, _m):
    return 200, notif.mark_all_read(claims["user_id"])


# --- demo data (dev demo stack only) ------------------------------------------------------

def _tenant_admin(claims: dict[str, Any]) -> None:
    _admin(claims, "demo")
    if claims.get("role") not in authz.TENANT_ADMIN_ROLES:
        raise r.Bad(403, "only tenant administrators may reset the demo data")


def _demo_get(event, claims, _m):
    _tenant_admin(claims)
    return 200, demo_data.overview()


def _demo_reset(event, claims, _m):
    _tenant_admin(claims)
    b = r.body(event)
    try:
        out = demo_data.start(claims["user_id"], b.get("confirm"))
    except demo_data.Refused as e:
        raise r.Bad(e.status, str(e), confirm_required=e.status == 400) from e
    audit_log.record(claims["user_id"], "admin.demo_reset", "demo-data", {"env": demo_data.envname.env()})
    return 202, out


_K = r"(guardrails|suggestions|notifications|flags|connectors|tenant)"
ROUTES = [
    ("GET", re.compile(r"^/admin/overview$"), _overview),
    ("GET", re.compile(r"^/admin/users$"), _users),
    ("PATCH", re.compile(r"^/admin/users/([^/]+)$"), _user_patch),
    ("GET", re.compile(r"^/admin/org$"), _org),
    ("POST", re.compile(r"^/admin/org$"), _org_create),
    ("PATCH", re.compile(r"^/admin/org/([^/]+)$"), _org_patch),
    ("GET", re.compile(r"^/admin/market$"), _market_get),
    ("PUT", re.compile(r"^/admin/market$"), _market_put),
    ("PUT", re.compile(r"^/admin/market/state-rules/([^/]+)$"), _state_rule_put),
    ("GET", re.compile(r"^/admin/connectors$"), _connectors),
    ("POST", re.compile(r"^/admin/connectors/([^/]+)/test$"), _connector_test),
    ("POST", re.compile(r"^/admin/connectors/([^/]+)/rotate$"), _connector_rotate),
    ("GET", re.compile(r"^/admin/flags$"), _flags_get),
    ("PUT", re.compile(r"^/admin/flags$"), _flags_put),
    ("POST", re.compile(r"^/admin/flags/publish$"), _flags_publish),
    ("POST", re.compile(r"^/config/guardrails/test$"), _gr_test),
    ("POST", re.compile(r"^/config/guardrails/sweep$"), _gr_sweep),
    ("POST", re.compile(r"^/config/suggestions/preview$"), _sugg_preview),
    ("GET", re.compile(rf"^/config/{_K}/versions$"), _cfg_versions),
    ("POST", re.compile(rf"^/config/{_K}/publish$"), _cfg_publish),
    ("POST", re.compile(rf"^/config/{_K}/rollback$"), _cfg_rollback),
    ("GET", re.compile(rf"^/config/{_K}$"), _cfg_get),
    ("PUT", re.compile(rf"^/config/{_K}$"), _cfg_put),
    ("DELETE", re.compile(rf"^/config/{_K}$"), _cfg_discard),
    ("GET", re.compile(r"^/admin/autonomy/history$"), _autonomy_history),
    ("GET", re.compile(r"^/admin/agents/health$"), _agents),
    ("GET", re.compile(r"^/admin/audit$"), _audit),
    ("GET", re.compile(r"^/admin/audit/export$"), _audit_export),
    ("GET", re.compile(r"^/admin/data-requests$"), _dsr_list),
    ("POST", re.compile(r"^/admin/data-requests$"), _dsr_create),
    ("POST", re.compile(r"^/admin/data-requests/([^/]+)/run$"), _dsr_run),
    ("PATCH", re.compile(r"^/admin/data-requests/([^/]+)/run$"), _dsr_run),
    ("GET", re.compile(r"^/admin/entities$"), _entities),
    ("POST", re.compile(r"^/admin/entities/decide$"), _entity_decide),
    ("PATCH", re.compile(r"^/admin/entities/decide$"), _entity_decide),
    ("GET", re.compile(r"^/approvals$"), _approvals),
    ("POST", re.compile(r"^/approvals$"), _approval_request),
    ("POST", re.compile(r"^/approvals/([^/]+)/decide$"), _approval_decide),
    ("POST", re.compile(r"^/notifications/read-all$"), _notif_read_all),
    ("GET", re.compile(r"^/admin/demo$"), _demo_get),
    ("POST", re.compile(r"^/admin/demo/reset$"), _demo_reset),
]

ERRORS = [
    (config_store.UnknownKind, 404), (config_store.HardRuleViolation, 422), (config_store.InvalidConfig, 400),
    (config_store.NotFound, 404),
    (connectors.UnknownConnector, 404), (connectors.InvalidRegistry, 400),
    (directory.Forbidden, 403), (directory.NotFound, 404), (directory.Invalid, 400),
    (data_requests.NotFound, 404), (data_requests.InvalidState, 409),
    (entities.NotFound, 404), (entities.InvalidState, 409),
    (approvals.NotFound, 404), (approvals.Forbidden, 403), (approvals.InvalidState, 409),
]
