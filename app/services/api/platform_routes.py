"""L4 — Platform routes (spec §6): Tasks, Handoffs + activity feed, the resource-URL
resolver, the outcome loop, the agent-action registry, and autonomy rollback.

Table-driven so new platform routes don't grow the main handler's if-chain:
`dispatch(event, path, method, claims)` returns a response dict, or None when no
route here matches (the main router then continues).

  GET/POST     /tasks                         · GET/PATCH /tasks/{id}
  POST         /handoffs                      · POST /handoffs/{id}/accept
  GET          /journeys/{id}/activity        (?audience=customer for the safe subset)
  GET          /nav/resolve?type=&id=&ctx=    · POST /nav/ctx (mint a context envelope)
  GET          /me/home                       (persona home screen, §5.2)
  POST         /recommendations               · POST /recommendations/{id}/accept|dismiss
  GET          /recommendations/stats?agent=&action_id=&dealer=&context_hash=
  GET          /agents/actions                (action registry + autonomy + ceilings)
  POST         /admin/autonomy/rollback       {action_id, to_version}
  GET          /config/market?market_id=us    (MarketConfig for UI formatters; any caller)
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from ..admin import autonomy as admin_autonomy
from ..common import authz, autonomy_config, ddb, market, personas, resource_url
from ..journeys import handoffs
from ..platform import recommendations as recs
from ..platform import tasks
from .routing import Bad as _Bad, Respond, body as _body, qs as _qs, journey_in_scope as _journey_in_scope

# Roles that may reassign/cancel others' tasks (team leads).
TASK_MANAGER_ROLES = frozenset({"dealer_manager", "dealer_service_manager", "oem_regional",
                                "admin_tenant", "admin_dealer", personas.PLATFORM_ADMIN})
_STAFF = personas.STAFF_ROLES


def _staff(claims: dict[str, Any]) -> None:
    if claims.get("role") not in _STAFF:
        raise _Bad(403, f"role {claims.get('role')!r} may not use this staff route")


# --- handlers ---------------------------------------------------------------

def _tasks_list(event, claims, _m):
    _staff(claims)
    qs = _qs(event)
    if qs.get("team") == "1" and claims.get("role") in TASK_MANAGER_ROLES:
        return 200, tasks.list_tasks(dealer_ids=None if claims.get("is_oem") else claims.get("dealer_ids"),
                                     status=qs.get("status"))
    return 200, tasks.list_tasks(user_id=claims["user_id"], status=qs.get("status"))


def _tasks_create(event, claims, _m):
    _staff(claims)
    b = _body(event)
    if b.get("journey_id"):
        _journey_in_scope(claims, b["journey_id"])
    return 201, tasks.create_task(
        b.get("title") or "", b.get("assignee") or claims["user_id"], created_by=claims["user_id"],
        journey_id=b.get("journey_id"), service_journey_id=b.get("service_journey_id"),
        dealer_id=b.get("dealer_id"), due_at=b.get("due_at"), sla_hours=b.get("sla_hours"),
        resource_url=b.get("resource_url"), source_agent=b.get("source_agent"))


def _task_get(event, claims, m):
    _staff(claims)
    t = tasks.task_view(m.group(1))
    if t.get("assigned_user_id") != claims["user_id"] and claims.get("role") not in TASK_MANAGER_ROLES:
        raise _Bad(403, "task belongs to another user")
    return 200, t


def _task_patch(event, claims, m):
    _staff(claims)
    b = _body(event)
    return 200, tasks.update_task(m.group(1), actor=claims["user_id"], status=b.get("status"),
                                  assignee=b.get("assignee"), note=b.get("note"),
                                  may_manage=claims.get("role") in TASK_MANAGER_ROLES)


def _handoff_create(event, claims, _m):
    _staff(claims)
    b = _body(event)
    if not b.get("journey_id") or not b.get("to_user"):
        raise _Bad(400, "body must include journey_id and to_user")
    _journey_in_scope(claims, b["journey_id"])
    return 201, handoffs.create_handoff(b["journey_id"], b["to_user"], from_user=claims["user_id"],
                                        note=b.get("note") or "", stage=b.get("stage"))


def _handoff_accept(event, claims, m):
    _staff(claims)
    return 200, handoffs.accept_handoff(m.group(1), claims["user_id"])


def _activity(event, claims, m):
    j = _journey_in_scope(claims, m.group(1))
    audience = "customer" if claims.get("role") == "customer" else _qs(event).get("audience", "staff")
    return 200, handoffs.activity_feed(j["journey_id"], audience=audience)


def _nav_resolve(event, claims, _m):
    qs = _qs(event)
    rtype, rid = qs.get("type"), qs.get("id")
    if not rtype or not rid:
        raise _Bad(400, "query must include type and id")
    try:
        out = resource_url.resolve(rtype, rid, claims.get("role"), ctx=qs.get("ctx"))
    except resource_url.UnknownType as e:
        raise _Bad(400, str(e)) from e
    return (200 if out["allowed"] else 403), out


def _nav_ctx(event, claims, _m):
    _staff(claims)
    b = _body(event)
    missing = [k for k in ("from_app", "from_screen", "reason_type", "reason_id") if not b.get(k)]
    if missing:
        raise _Bad(400, f"body missing {missing}")
    tok = resource_url.sign_ctx(b["from_app"], b["from_screen"], b["reason_type"], b["reason_id"],
                                scope=b.get("scope"))
    out = {"ctx": tok}
    if b.get("type") and b.get("id"):
        out["url"] = resource_url.resource_url(b["type"], b["id"], ctx=tok)
    return 200, out


def _me_personas(event, claims, _m):
    """Admin "View as" catalog: every staff persona with its display name + home.
    Admin-only, since only admins may impersonate (enforced server-side in the
    handler's claims resolution too)."""
    if claims.get("role") not in authz.ADMIN_ROLES:
        raise _Bad(403, f"role {claims.get('role')!r} may not list personas")
    return 200, {"personas": personas.catalog(include_customer=False),
                 "acting_as": claims.get("impersonating_role"),
                 "impersonated_by": claims.get("impersonated_by")}


def _me_home(event, claims, _m):
    role = claims.get("role")
    if role not in personas.PERSONAS:
        raise _Bad(403, f"role {role!r} has no home screen")
    screen = personas.home(role)
    user = authz.get_user(claims["user_id"]) if role != "customer" else None
    cust = ddb.get_item("DEALER_Customers", {"customer_id": claims.get("customer_id")}) \
        if role == "customer" and claims.get("customer_id") else None
    who = user or cust or {}
    name = " ".join(filter(None, [who.get("given_name") or who.get("first_name"),
                                  who.get("family_name") or who.get("last_name")])) or claims["user_id"]
    groups = list(claims.get("groups") or [role])
    screens = personas.screens_for(groups, claims.get("extra_screens"))
    return 200, {"role": role, "role_name": personas.display_name(role),
                 "home": screen, "url": personas.route(screen),
                 "groups": groups,
                 "screens": sorted(screens or personas.PERSONAS[role][1]),
                 "user_id": claims["user_id"], "name": name,
                 "scope_node_id": claims.get("scope_node_id") or who.get("scope_node_id") or who.get("dealer_id"),
                 "dealer_ids": sorted(claims.get("dealer_ids") or []),
                 "market_id": claims.get("market_id") or who.get("market_id"),
                 "is_oem": bool(claims.get("is_oem")), "customer_id": claims.get("customer_id") or None,
                 # Impersonation surface so the UI can show a "Viewing as …" banner.
                 "impersonating": bool(claims.get("impersonated_by")),
                 "impersonated_by": claims.get("impersonated_by")}


def _nav_ctx_verify(event, claims, _m):
    tok = _qs(event).get("ctx")
    if not tok:
        raise _Bad(400, "query must include ctx")
    try:
        c = resource_url.verify_ctx(tok)
    except resource_url.InvalidContext as e:
        raise _Bad(400, str(e)) from e
    return 200, {**c, "because": resource_url.because(c)}


def _rec_shown(event, claims, _m):
    _staff(claims)
    b = _body(event)
    if not b.get("agent_id") or not b.get("action"):
        raise _Bad(400, "body must include agent_id and action")
    if b.get("journey_id"):
        _journey_in_scope(claims, b["journey_id"])
    try:
        row = recs.record_shown(
            b["agent_id"], b["action"], shown_to=claims.get("role") or "staff",
            journey_id=b.get("journey_id"), service_journey_id=b.get("service_journey_id"),
            account_id=b.get("account_id"), dealer_id=b.get("dealer_id"),
            context_hash=b.get("context_hash"), autonomy_level=b.get("autonomy_level"),
            trace_id=b.get("trace_id"), reason=b.get("reason"),
            recommendation_id=b.get("recommendation_id"))
    except ValueError as e:
        raise _Bad(400, str(e)) from e
    return 201, row


def _rec_scope(claims, rec_id):
    row = ddb.get_item("DEALER_Recommendations", {"recommendation_id": rec_id})
    if row is None:
        raise _Bad(404, f"recommendation {rec_id} not found")
    if row.get("journey_id"):
        _journey_in_scope(claims, row["journey_id"])
    elif row.get("dealer_id") and not claims.get("is_oem") \
            and row["dealer_id"] not in claims.get("dealer_ids", set()):
        raise _Bad(403, "recommendation outside caller scope")


def _rec_accept(event, claims, m):
    _staff(claims)
    _rec_scope(claims, m.group(1))
    return 200, recs.accept(m.group(1), claims["user_id"])


def _rec_dismiss(event, claims, m):
    _staff(claims)
    _rec_scope(claims, m.group(1))
    return 200, recs.dismiss(m.group(1), claims["user_id"], reason=_body(event).get("reason"))


def _rec_stats(event, claims, _m):
    _staff(claims)
    qs = _qs(event)
    dealer = qs.get("dealer")
    if dealer and not claims.get("is_oem") and dealer not in claims.get("dealer_ids", set()):
        raise _Bad(403, "dealer outside caller scope")
    return 200, recs.stats(agent_id=qs.get("agent"), action=qs.get("action_id"),
                           dealer_id=dealer, context_hash=qs.get("context_hash"))


def _agent_actions(event, claims, _m):
    _staff(claims)
    market = _qs(event).get("market", claims.get("market_id") or "us")
    cfg = admin_autonomy.list_autonomy(market)
    actions = [{**a, "agent": (a["action_id"] or "").split(".")[0].upper()} for a in cfg["actions"]]
    return 200, {"market_id": market, "levels": list(autonomy_config.LEVELS),
                 "hard_ceilings": autonomy_config.HARD_CEILINGS, "actions": actions,
                 "count": len(actions)}


def _autonomy_rollback(event, claims, _m):
    if claims.get("role") not in authz.ADMIN_ROLES:
        raise _Bad(403, f"role {claims.get('role')!r} may not administer autonomy")
    b = _body(event)
    if not b.get("action_id") or b.get("to_version") is None:
        raise _Bad(400, "body must include action_id and to_version")
    return 200, admin_autonomy.rollback_autonomy(
        b["action_id"], int(b["to_version"]), claims["user_id"], market=b.get("market", "us"),
        scope=b.get("scope", "tenant"), scope_id=b.get("scope_id"))


_MARKET_FIELDS = ("market_id", "locale", "currency", "currency_symbol", "distance_unit",
                  "efficiency_unit_ice", "efficiency_unit_ev", "fuel_unit", "region_dimension",
                  "list_price_includes_tax", "finance_products", "regulatory_disclosures",
                  "timezone_default", "brand")


def _config_market(event, claims, _m):
    mid = _qs(event).get("market_id") or claims.get("market_id")
    try:
        cfg = market.MarketConfig.load(mid)
    except market.MissingMarketConfig as e:
        raise _Bad(404, str(e)) from e
    return 200, {k: cfg.raw.get(k) for k in _MARKET_FIELDS}


ROUTES: list[tuple[str, re.Pattern[str], Callable]] = [
    ("GET", re.compile(r"^/config/market$"), _config_market),
    ("GET", re.compile(r"^/tasks$"), _tasks_list),
    ("POST", re.compile(r"^/tasks$"), _tasks_create),
    ("GET", re.compile(r"^/tasks/([^/]+)$"), _task_get),
    ("PATCH", re.compile(r"^/tasks/([^/]+)$"), _task_patch),
    ("POST", re.compile(r"^/handoffs$"), _handoff_create),
    ("POST", re.compile(r"^/handoffs/([^/]+)/accept$"), _handoff_accept),
    ("PATCH", re.compile(r"^/handoffs/([^/]+)/accept$"), _handoff_accept),
    ("GET", re.compile(r"^/journeys/([^/]+)/activity$"), _activity),
    ("GET", re.compile(r"^/nav/resolve$"), _nav_resolve),
    ("POST", re.compile(r"^/nav/ctx$"), _nav_ctx),
    ("GET", re.compile(r"^/nav/ctx/verify$"), _nav_ctx_verify),
    ("GET", re.compile(r"^/me/home$"), _me_home),
    ("GET", re.compile(r"^/me/personas$"), _me_personas),
    ("POST", re.compile(r"^/recommendations$"), _rec_shown),
    ("POST", re.compile(r"^/recommendations/([^/]+)/accept$"), _rec_accept),
    ("PATCH", re.compile(r"^/recommendations/([^/]+)/accept$"), _rec_accept),
    ("POST", re.compile(r"^/recommendations/([^/]+)/dismiss$"), _rec_dismiss),
    ("PATCH", re.compile(r"^/recommendations/([^/]+)/dismiss$"), _rec_dismiss),
    ("GET", re.compile(r"^/recommendations/stats$"), _rec_stats),
    ("GET", re.compile(r"^/agents/actions$"), _agent_actions),
    ("POST", re.compile(r"^/admin/autonomy/rollback$"), _autonomy_rollback),
]

# Domain exceptions -> HTTP status.
_ERRORS: list[tuple[type, int]] = [
    (tasks.NotFound, 404), (handoffs.NotFound, 404), (recs.NotFound, 404),
    (admin_autonomy.NotFound, 404),
    (tasks.Forbidden, 403), (handoffs.Forbidden, 403),
    (tasks.InvalidTransition, 409), (handoffs.InvalidState, 409), (recs.InvalidState, 409),
    (admin_autonomy.HardRuleViolation, 422), (admin_autonomy.InvalidLevel, 400),
    (ValueError, 400),
]


def dispatch(event: dict[str, Any], path: str, method: str, claims: dict[str, Any],
             respond: Respond) -> dict[str, Any] | None:
    for verb, rx, fn in ROUTES:
        m = rx.match(path)
        if not m or verb != method:
            continue
        if not claims.get("user_id"):
            return respond(401, {"error": "unauthenticated"})
        try:
            code, body = fn(event, claims, m)
            return respond(code, body)
        except _Bad as e:
            return respond(e.code, {"error": e.msg})
        except Exception as e:
            for etype, code in _ERRORS:
                if isinstance(e, etype):
                    return respond(code, {"error": str(e)})
            return respond(500, {"error": f"{type(e).__name__}: {e}"})
    return None
