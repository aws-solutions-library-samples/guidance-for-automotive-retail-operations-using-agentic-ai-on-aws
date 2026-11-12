"""L4 — API Gateway proxy-integration Lambda handler for the journeys service.

Routes the same read models the dev server exposes, but now behind the access
layer: the authorizer has already resolved the caller's claims (role + scope)
into `requestContext.authorizer`, and this handler ENFORCES scope on the
resolved resource before returning it.

Route:
  GET /journeys/{id}/assist  -> authz.check_journey_access then assist.build_assist

The same function is used two ways:
  * as an API Gateway Lambda proxy integration (lambda_handler), and
  * by the local dev server, which builds the same proxy event so local behavior
    matches the deployed one.
"""
from __future__ import annotations

import contextvars
import json
import re
from typing import Any

from ...agentcore import client as _agent_client
from ...agentcore import registry as _agent_registry
from ..agents import deal as a3_deal
from ..agents import intel as a3_intel
from ..agents import discovery, equity, objection, suggestions as a3_suggestions
from ..finance import prequalify as fni_prequalify
from ..approvals import plan_approval
from ..common import authz, ddb, trace as _trace
from ..journeys import actions as journey_actions
from ..journeys import intent_shift
from ..journeys import package as journey_package
from ..journeys import assist, board, nurture, qa, supervisor
from ..admin import autonomy as admin_autonomy
from ..customer import garage as cust_garage
from ..dealer import cockpit as dealer_cockpit
from ..dealer import delivery as dealer_delivery
from ..dealer import leads as dealer_leads
from ..dealer import notifications as notif_hub
from ..events import bus as event_bus
from ..oem import account_plan as oem_account_plan
from ..oem import alerts as oem_alerts
from ..oem import campaigns as oem_campaigns
from ..oem import guardrails as oem_guardrails
from ..oem import today as oem_today
from ..service import board as svc_board
from ..service import booking as svc_booking
from ..service import followup as svc_followup
from ..service import inspection as svc_inspection
from ..service import repair as svc_repair
from ..service import supervisor as svc_supervisor
from . import automation_traces as _automation_traces
from . import cors, job_handler, platform_routes, routes as domain_routes
from .routes import sales_floor as _sales_floor

_ASSIST_RE = re.compile(r"^/journeys/([^/]+)/assist$")
_BOARD_RE = re.compile(r"^/journeys/board$")
_UTTERANCE_RE = re.compile(r"^/journeys/([^/]+)/utterance$")
# SSE flavour of the floor path. Same auth, same payload — the body is one
# Server-Sent-Events document (`event: token` per generation delta, then
# `event: result`). API Gateway REST cannot stream, and neither can the managed
# python3.12 runtime, so this returns the buffered document in one flush — the
# wire format is identical to a true streaming transport, so the console client
# is drop-in when one lands (ADR 0008: the public Function URL transport was
# retired after the account's block-public-access control flagged it).
_UTTERANCE_SSE_RE = re.compile(r"^/journeys/([^/]+)/utterance-sse$")
_EQUITY_RE = re.compile(r"^/journeys/([^/]+)/equity$")
_REVALUE_RE = re.compile(r"^/journeys/([^/]+)/revalue$")
_DISCOVERY_RE = re.compile(r"^/journeys/([^/]+)/discovery$")
_LIFESTYLE_RE = re.compile(r"^/journeys/([^/]+)/lifestyle$")
_RECOMMEND_CONFIG_RE = re.compile(r"^/journeys/([^/]+)/recommend-config$")
_SUGGESTIONS_RE = re.compile(r"^/journeys/([^/]+)/suggestions$")
_PRICE_BUILD_RE = re.compile(r"^/journeys/([^/]+)/price-build$")
_INVENTORY_RE = re.compile(r"^/journeys/([^/]+)/inventory$")
_DEAL_RE = re.compile(r"^/journeys/([^/]+)/deal$")
_COMPINTEL_RE = re.compile(r"^/journeys/([^/]+)/competitive-intel$")
_INCENTIVES_RE = re.compile(r"^/journeys/([^/]+)/incentives$")
_CROSSSELL_RE = re.compile(r"^/journeys/([^/]+)/cross-sell$")
_PREQUAL_RE = re.compile(r"^/journeys/([^/]+)/prequalify$")
_STATE_RE = re.compile(r"^/journeys/([^/]+)/state$")
_ADVANCE_RE = re.compile(r"^/journeys/([^/]+)/advance$")
_NURTURE_RE = re.compile(r"^/journeys/([^/]+)/nurture$")
_NURTURE_PLAN_RE = re.compile(r"^/journeys/([^/]+)/nurture/plan$")
_CHAT_RE = re.compile(r"^/journeys/([^/]+)/chat$")
_NUDGE_RE = re.compile(r"^/journeys/([^/]+)/nudge$")
_NBA_RE = re.compile(r"^/journeys/([^/]+)/nba$")
_BOOKTD_RE = re.compile(r"^/journeys/([^/]+)/book-test-drive$")
_INTENT_RE = re.compile(r"^/journeys/([^/]+)/intent-shift$")
_TASK_RE = re.compile(r"^/journeys/([^/]+)/task$")
_PACKAGE_RE = re.compile(r"^/journeys/([^/]+)/package$")
_OEM_TODAY_RE = re.compile(r"^/oem/today$")
_OEM_CAMPAIGNS_RE = re.compile(r"^/oem/campaigns$")
_OEM_ALERTS_RE = re.compile(r"^/oem/alerts$")
_OEM_DRAFTPLAN_RE = re.compile(r"^/oem/accounts/([^/]+)/draft-plan$")
_OEM_BUDGETSHIFT_RE = re.compile(r"^/oem/campaigns/budget-shift$")
_OEM_GUARDRAILS_RE = re.compile(r"^/oem/guardrails$")
_OEM_GUARDRAILS_CHECK_RE = re.compile(r"^/oem/guardrails/check$")
# Tier A depth routes (planner suite, A4 prospects, Plays)
_OEM_COMPOSITE_RE = re.compile(r"^/oem/accounts/([^/]+)/composite$")
_OEM_EVREADY_RE = re.compile(r"^/oem/accounts/([^/]+)/ev-readiness$")
_OEM_COMPLIANCE_RE = re.compile(r"^/oem/accounts/([^/]+)/compliance$")
_OEM_SCENARIO_RE = re.compile(r"^/oem/scenario$")
_OEM_WEEKEND_RE = re.compile(r"^/oem/weekend$")
_A4_PROSPECTS_RE = re.compile(r"^/prospects$")
_A4_ACQUIRE_RE = re.compile(r"^/prospects/acquire/([^/]+)$")
_PLAY_RUN_RE = re.compile(r"^/journeys/([^/]+)/run-play$")
_KNOWLEDGE_RE = re.compile(r"^/knowledge/retrieve$")
_QUICK_FLOWS_RE = re.compile(r"^/oem/quick/flows$")
_QUICK_DASHBOARDS_RE = re.compile(r"^/oem/quick/dashboards$")
_QUICK_EMBED_RE = re.compile(r"^/oem/quick/dashboards/([^/]+)/embed-url$")
_JOB_RE = re.compile(r"^/jobs/([^/]+)$")
_GARAGE_RE = re.compile(r"^/garage$")
_GARAGE_ID_RE = re.compile(r"^/garage/([^/]+)$")
_ADMIN_AUTONOMY_RE = re.compile(r"^/admin/autonomy$")
_COCKPIT_RE = re.compile(r"^/dealer/cockpit$")
_DELIVERY_BOARD_RE = re.compile(r"^/dealer/delivery$")
_DELIVER_RE = re.compile(r"^/dealer/delivery/([^/]+)/deliver$")
_LEAD_DESK_RE = re.compile(r"^/dealer/leads$")
_LEAD_ASSIGN_RE = re.compile(r"^/dealer/leads/([^/]+)/assign$")
_NOTIFS_RE = re.compile(r"^/notifications$")
_NOTIF_READ_RE = re.compile(r"^/notifications/([^/]+)/read$")
_EVENTS_RE = re.compile(r"^/events$")
# When RETAIL_OPS_EVENT_BUS_LIVE=1 the /events route publishes to the real bus (rule
# fans out to the subscriber Lambda); otherwise it dispatches in-process so the
# local dev server exercises the same subscriber code without a bus.
import os as _os
_EVENT_BUS_LIVE = _os.environ.get("RETAIL_OPS_EVENT_BUS_LIVE", "0") == "1"
_SVC_BOARD_RE = re.compile(r"^/service/board$")
_SVC_STATE_RE = re.compile(r"^/service/([^/]+)/state$")
_SVC_ADVANCE_RE = re.compile(r"^/service/([^/]+)/advance$")
_SVC_APPROVE_RE = re.compile(r"^/service/([^/]+)/approve$")
_SVC_HANDOFF_RE = re.compile(r"^/service/([^/]+)/handoff-to-sales$")
_SVC_PREDICT_RE = re.compile(r"^/service/predict/([^/]+)$")
_SVC_BOOK_RE = re.compile(r"^/service/book$")
_SVC_INSPECTION_RE = re.compile(r"^/service/([^/]+)/inspection$")
_SVC_FINDINGS_RE = re.compile(r"^/service/([^/]+)/findings$")
_SVC_ESTIMATE_RE = re.compile(r"^/service/([^/]+)/estimate$")
_SVC_PARTS_RE = re.compile(r"^/service/([^/]+)/parts$")
_SVC_LINE_RE = re.compile(r"^/service/([^/]+)/line-status$")
_SVC_READY_RE = re.compile(r"^/service/([^/]+)/notify-ready$")
_SVC_DECLINE_RE = re.compile(r"^/service/([^/]+)/decline-followup$")
_SVC_DETAIL_RE = re.compile(r"^/service/([^/]+)$")
_APPROVAL_SUBMIT_RE = re.compile(r"^/approvals/plan/([^/]+)/submit$")
_APPROVAL_DECIDE_RE = re.compile(r"^/approvals/plan/([^/]+)/decide$")
_APPROVAL_STATUS_RE = re.compile(r"^/approvals/plan/([^/]+)$")
# Cross-persona read via the AgentCore Gateway (spec §6). A non-OEM persona
# (e.g. a Dealer advisor) reads OEM-owned data ONLY through this route, which
# routes to the Gateway MCP tools — never a direct retail-ops-dev-OEM_* read.
# GET /gateway/oem/{tool}?<args>  (tool = accounts|account|alerts|incentives)
_GATEWAY_OEM_RE = re.compile(r"^/gateway/oem/([^/]+)$")


# One correlation id per in-flight request, set by lambda_handler and readable by
# any downstream helper that wants to tie its own trace spans to this request.
# Context variables, not module globals: Lambda runs one request per container, but
# the local devserver is a ThreadingHTTPServer, and with globals a finishing request
# cleared the Origin under a concurrent one, whose CORS reply then fell back to the
# first allowed origin (http://localhost:3100) and was refused by a console served
# from 127.0.0.1:3100 ("Failed to fetch" on every multi-fetch page, 2026-10-30).
_CURRENT_CORRELATION_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar("api_correlation_id", default=None)
# The request's Origin header, set by lambda_handler for the CORS reply below.
_CURRENT_ORIGIN: contextvars.ContextVar[str | None] = contextvars.ContextVar("api_origin", default=None)

# CORS: the console's origin only, never `*` (app/services/api/cors.py). Every
# response this handler returns gets its headers from cors.headers(), through
# _response or _sse_response; none builds its own header dict.
CONSOLE_ORIGIN_ENV = cors.CONSOLE_ORIGIN_ENV
allowed_origins = cors.allowed_origins
cors_origin = cors.cors_origin


def current_correlation_id() -> str | None:
    """The correlation id of the request currently being handled (or None)."""
    return _CURRENT_CORRELATION_ID.get()


def current_origin() -> str | None:
    """The Origin header of the request currently being handled (or None)."""
    return _CURRENT_ORIGIN.get()


def _response(code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": code,
        "headers": cors.headers(_CURRENT_ORIGIN.get(), _CURRENT_CORRELATION_ID.get()),
        "body": "" if code == 204 else json.dumps(body, default=str),
    }


def _sse_response(doc: str) -> dict[str, Any]:
    """A 200 Server-Sent-Events document with the same CORS headers as _response."""
    headers = cors.headers(_CURRENT_ORIGIN.get(), _CURRENT_CORRELATION_ID.get(),
                           content_type="text/event-stream; charset=utf-8")
    headers["Cache-Control"] = "no-cache"
    return {"statusCode": 200, "headers": headers, "body": doc}


def _header(event: dict[str, Any], name: str) -> str | None:
    """Case-insensitive lookup of a request header (API GW preserves case, but
    clients/proxies vary)."""
    headers = event.get("headers") or {}
    want = name.lower()
    for k, v in headers.items():
        if k.lower() == want:
            return v
    return None


def _claims(event: dict[str, Any]) -> dict[str, Any]:
    ctx = (event.get("requestContext") or {}).get("authorizer") or {}
    dealer_ids = ctx.get("dealer_ids") or ""
    claims = {
        "user_id": ctx.get("user_id") or ctx.get("principalId"),
        "role": ctx.get("role"),
        "groups": list(filter(None, (ctx.get("groups") or ctx.get("role") or "").split(","))),
        "extra_screens": list(filter(None, (ctx.get("extra_screens") or "").split(","))),
        "scope_node_id": ctx.get("scope_node_id"),
        "market_id": ctx.get("market_id"),
        "dealer_ids": set(filter(None, dealer_ids.split(","))),
        "is_oem": str(ctx.get("is_oem")).lower() == "true",
        "customer_id": ctx.get("customer_id") or "",
    }
    return _maybe_impersonate(event, claims)


def _maybe_impersonate(event: dict[str, Any], claims: dict[str, Any]) -> dict[str, Any]:
    """Admin "View as persona" (Design A): when an authenticated admin sends an
    `X-Act-As: <role>` (or `<user_id>`) header, substitute that persona's scope
    for the rest of the request so the admin sees the app exactly as that persona
    does. Server-enforced — the swap happens ONLY if the REAL authenticated caller
    is an admin; a non-admin's header is ignored. Every switch is audited.

    The admin's true identity is preserved in `impersonated_by` so handlers and
    the audit trail always know who is really acting."""
    target = _header(event, "X-Act-As")
    if not target:
        return claims
    target = target.strip()
    # Gate on the REAL caller's role (from the verified authorizer context).
    if claims.get("role") not in authz.ADMIN_ROLES:
        return claims  # silently ignore for non-admins; no privilege escalation
    admin_id = claims.get("user_id")
    # Customer impersonation: `X-Act-As: cust:<customer_id>` gives the admin the
    # customer principal (self-scope only — strictly FEWER privileges than the
    # admin's own), so demos can walk the My Garage side of a journey. Audited
    # like every switch; the customer must exist.
    if target.startswith("cust:"):
        cid = target[len("cust:"):]
        cust = ddb.get_item("DEALER_Customers", {"customer_id": cid}) if cid else None
        if not cust:
            return claims
        impersonated = {
            "user_id": cid, "role": "customer", "groups": ["customer"], "extra_screens": [],
            "scope_node_id": "",
            "market_id": cust.get("market_id") or "", "dealer_ids": set(),
            "is_oem": False, "customer_id": cid,
            "impersonated_by": admin_id, "impersonating_role": "customer",
        }
        _audit_impersonation(admin_id, cid, "customer", event)
        return impersonated
    try:
        from ..common import personas
        # Resolve the target. A role name maps to a representative seed user of
        # that role; a user_id maps to that specific user. Either way we resolve
        # a concrete PLAT_Users row so scope is real, not synthesized.
        user = authz.get_user(target)
        if user is None and target in personas.PERSONAS:
            user = _first_user_with_role(target)
        if user is None:
            return claims  # unknown target -> stay as admin (handler will behave normally)
        scope = authz.resolve_scope(user)
    except Exception:
        return claims
    if scope["role"] == authz.PLATFORM_ADMIN:
        # Nobody "views as" the Administrator: that role comes only from the SSO
        # allowlist, so impersonating it would let a dealer admin step up.
        return claims
    impersonated = {
        "user_id": scope["user_id"],
        "role": scope["role"] or "",
        "groups": list(scope.get("groups") or ([scope["role"]] if scope.get("role") else [])),
        "extra_screens": list(user.get("extra_screens") or []),
        "scope_node_id": scope.get("scope_node_id") or "",
        "market_id": scope.get("market_id") or "",
        "dealer_ids": set(scope.get("dealer_ids") or []),
        "is_oem": bool(scope.get("is_oem")),
        "customer_id": "",
        "impersonated_by": admin_id,          # the real admin behind the view
        "impersonating_role": scope["role"],
    }
    _audit_impersonation(admin_id, impersonated["user_id"], impersonated["role"], event)
    return impersonated


def _first_user_with_role(role: str) -> dict[str, Any] | None:
    """A representative active PLAT_Users row carrying `role` as its first group,
    so "view as <role>" resolves to a real scope without needing a user_id."""
    try:
        for u in ddb.scan_all("PLAT_Users"):
            groups = u.get("groups") or []
            if groups and groups[0] == role and u.get("active", True):
                return u
    except Exception:
        return None
    return None


def _audit_impersonation(admin_id: str | None, as_user: str, as_role: str,
                         event: dict[str, Any]) -> None:
    """Record one audit row per impersonated request (who, as-whom, which route)."""
    try:
        from ..admin import audit_log
        path = event.get("path") or event.get("rawPath") or ""
        method = (event.get("httpMethod")
                  or (event.get("requestContext", {}).get("http", {}) or {}).get("method") or "GET")
        audit_log.record(admin_id or "unknown", "impersonation.view_as", as_user,
                         {"as_role": as_role, "route": f"{method} {path}"})
    except Exception:
        pass  # auditing must never block the request


def _package_on_runtime(journey_id: str, scope: dict[str, Any]) -> dict[str, Any] | None:
    """POST /journeys/{id}/package on the Journey Agent (A2) runtime (ADR 0009,
    design §6.4). Returns the response, or None when the runtime is not
    configured or did not run the call (the caller builds in-process). A lost
    answer is 202 `building` with `requested_at` (the poll target is GET
    /journeys/{id}/package): the build may still finish there, so no local
    rebuild."""
    corr = _trace.CURRENT_CORRELATION_ID.get()
    requested_at = journey_package._now()
    res = _agent_client.invoke_tool("A2", "build_sales_package", {"journey_id": journey_id}, claims=scope,
                                    correlation_id=corr, timeout_s=20)
    if res.status == "ok":
        return _response(200, res.body)
    if res.status == "lost":
        return _response(202, {"status": "building", "journey_id": journey_id,
                               "poll": f"/journeys/{journey_id}/package", "requested_at": requested_at,
                               "correlation_id": corr})
    if res.status == "failed":
        return _response(500, {"error": "the Journey Agent could not build the package", "agent": "A2"})
    if res.status == "caller_error":
        return _response(res.code, res.body)
    return None                     # not_configured / unavailable: nothing ran; build in-process


def _route(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    path = event.get("path") or event.get("rawPath") or ""
    method = (event.get("httpMethod")
              or (event.get("requestContext", {}).get("http", {}) or {}).get("method")
              or "GET")

    if path == "/health" and method == "GET":
        return _response(200, {"ok": True})

    # CORS preflight (the console sends Authorization + X-Correlation-Id).
    if method == "OPTIONS":
        return _response(204, {})

    # Platform routes (Tasks, Handoffs, /nav/resolve, outcome loop, …) — table-driven.
    platform_resp = platform_routes.dispatch(event, path, method, _claims(event), _response)
    if platform_resp is not None:
        return platform_resp
    # Per-domain route modules (app/services/api/routes/*.py).
    domain_resp = domain_routes.dispatch(event, path, method, _claims(event), _response)
    if domain_resp is not None:
        return domain_resp

    # Notification Hub — the caller's own inbox + mark-read (any authenticated staff).
    if _NOTIFS_RE.match(path) and method == "GET":
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        qs = event.get("queryStringParameters") or {}
        try:
            return _response(200, notif_hub.get_inbox(
                scope["user_id"], unread_only=(qs.get("unread") == "1")))
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    mnr = _NOTIF_READ_RE.match(path)
    if mnr and method == "POST":
        nid = mnr.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        try:
            return _response(200, notif_hub.mark_read(nid, scope["user_id"]))
        except notif_hub.NotFound as e:
            return _response(404, {"error": str(e)})
        except notif_hub.Forbidden as e:
            return _response(403, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # Customer Web: a customer reads THEIR OWN garage (self-scope only).
    if _GARAGE_RE.match(path) and method == "GET":
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        if scope.get("role") != "customer" or not scope.get("customer_id"):
            return _response(403, {"error": "the garage is a customer self-service view"})
        try:
            return _response(200, cust_garage.build_garage(scope["customer_id"]))
        except cust_garage.NotFound as e:
            return _response(404, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # Staff read-only mirror of a customer's garage ("what the customer sees",
    # §5.12) — advisor/service roles, scoped to that customer's dealer.
    mg = _GARAGE_ID_RE.match(path)
    if mg and method == "GET":
        cid = mg.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        roles = authz.ADVISOR_READ_ROLES | authz.SERVICE_READ_ROLES
        if scope.get("role") not in roles and not scope.get("is_oem"):
            return _response(403, {"error": f"role {scope.get('role')!r} may not view a customer garage"})
        cust = ddb.get_item("DEALER_Customers", {"customer_id": cid})
        if cust and not scope.get("is_oem") and cust.get("dealer_id") not in scope.get("dealer_ids", set()):
            return _response(403, {"error": "customer outside caller scope"})
        try:
            return _response(200, cust_garage.build_garage(cid))
        except cust_garage.NotFound as e:
            return _response(404, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    if _BOARD_RE.match(path) and method == "GET":
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        if scope.get("role") not in authz.ADVISOR_READ_ROLES and not scope.get("is_oem"):
            return _response(403, {"error": f"role {scope.get('role')!r} may not read the journeys board"})
        market = (event.get("queryStringParameters") or {}).get("market")
        try:
            payload = board.build_board(scope, market_id=market)
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})
        return _response(200, payload)

    msse = _UTTERANCE_SSE_RE.match(path)
    if msse and method == "POST":
        journey_id = msse.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id})
        if journey is None:
            return _response(404, {"error": f"journey {journey_id} not found"})
        try:
            authz.check_journey_access(scope, journey)
        except authz.Forbidden as e:
            return _response(403, {"error": str(e)})
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return _response(400, {"error": "invalid JSON body"})
        utterance = (body.get("utterance") or "").strip()
        if not utterance:
            return _response(400, {"error": "body must include utterance"})

        chunks: list[str] = []

        def _sse(name: str, payload) -> str:
            return f"event: {name}\ndata: {json.dumps(payload, default=str)}\n\n"

        try:
            result = objection.classify_objection_stream(
                journey_id, utterance, on_token=lambda t: chunks.append(_sse("token", {"text": t})))
            # The same card fields as POST /journeys/{id}/objection (success rate, the
            # "shown" recommendation the Use This / Alternative buttons record against).
            result = _sales_floor.objection_card(result, scope, journey, utterance)
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})
        return _sse_response("".join(chunks) + _sse("result", result))

    mu = _UTTERANCE_RE.match(path)
    if mu and method == "POST":
        journey_id = mu.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id})
        if journey is None:
            return _response(404, {"error": f"journey {journey_id} not found"})
        try:
            authz.check_journey_access(scope, journey)
        except authz.Forbidden as e:
            return _response(403, {"error": str(e)})
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return _response(400, {"error": "invalid JSON body"})
        utterance = (body or {}).get("utterance", "")
        if not utterance:
            return _response(400, {"error": "body must include 'utterance'"})
        try:
            result = objection.classify_objection(journey_id, utterance)
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})
        return _response(200, result)

    if _OEM_TODAY_RE.match(path) and method == "GET":
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        if scope.get("role") not in authz.OEM_VIEW_ROLES:
            return _response(403, {"error": f"role {scope.get('role')!r} may not read the OEM workspace"})
        market = (event.get("queryStringParameters") or {}).get("market")
        try:
            return _response(200, oem_today.build_today(scope, market_id=market))
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # Store Cockpit — dealer principal dashboard (dealer_manager or OEM).
    if _COCKPIT_RE.match(path) and method == "GET":
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        if scope.get("role") not in authz.COCKPIT_ROLES and not scope.get("is_oem"):
            return _response(403, {"error": f"role {scope.get('role')!r} may not view the store cockpit"})
        qs = event.get("queryStringParameters") or {}
        try:
            return _response(200, dealer_cockpit.build_cockpit(
                scope, dealer_id=qs.get("dealer"), market_id=qs.get("market")))
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # Delivery Board — DELIVER-stage kanban (dealer_delivery/manager/OEM).
    if _DELIVERY_BOARD_RE.match(path) and method == "GET":
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        if scope.get("role") not in authz.ADVISOR_READ_ROLES and not scope.get("is_oem"):
            return _response(403, {"error": f"role {scope.get('role')!r} may not view the delivery board"})
        qs = event.get("queryStringParameters") or {}
        try:
            return _response(200, dealer_delivery.build_delivery_board(scope, market_id=qs.get("market")))
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    mdlv = _DELIVER_RE.match(path)
    if mdlv and method == "POST":
        delivery_id = mdlv.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        if scope.get("role") not in authz.ADVISOR_READ_ROLES and not scope.get("is_oem"):
            return _response(403, {"error": f"role {scope.get('role')!r} may not mark deliveries"})
        d = ddb.get_item("DEALER_Deliveries", {"delivery_id": delivery_id})
        if d is None:
            return _response(404, {"error": f"delivery {delivery_id} not found"})
        if not scope.get("is_oem") and d.get("dealer_id") not in scope.get("dealer_ids", set()):
            return _response(403, {"error": "delivery outside caller scope"})
        try:
            return _response(200, dealer_delivery.mark_delivered(delivery_id, actor=scope["user_id"]))
        except dealer_delivery.AlreadyDelivered as e:
            return _response(409, {"error": str(e)})
        except dealer_delivery.NotFound as e:
            return _response(404, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # Lead Desk — BDC intake queue (advisor/bdc/manager/OEM).
    if _LEAD_DESK_RE.match(path) and method == "GET":
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        if scope.get("role") not in authz.ADVISOR_READ_ROLES and not scope.get("is_oem"):
            return _response(403, {"error": f"role {scope.get('role')!r} may not view the lead desk"})
        qs = event.get("queryStringParameters") or {}
        try:
            return _response(200, dealer_leads.build_lead_desk(scope, market_id=qs.get("market")))
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    mla = _LEAD_ASSIGN_RE.match(path)
    if mla and method == "POST":
        lead_id = mla.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        if scope.get("role") not in authz.ADVISOR_READ_ROLES and not scope.get("is_oem"):
            return _response(403, {"error": f"role {scope.get('role')!r} may not assign leads"})
        lead = ddb.get_item("DEALER_Leads", {"lead_id": lead_id})
        if lead is None:
            return _response(404, {"error": f"lead {lead_id} not found"})
        if not scope.get("is_oem") and lead.get("dealer_id") not in scope.get("dealer_ids", set()):
            return _response(403, {"error": "lead outside caller scope"})
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return _response(400, {"error": "invalid JSON body"})
        assignee = (body or {}).get("assignee") or scope["user_id"]
        try:
            return _response(200, dealer_leads.assign_lead(lead_id, assignee, actor=scope["user_id"]))
        except dealer_leads.NotFound as e:
            return _response(404, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    if _ADMIN_AUTONOMY_RE.match(path):
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        if scope.get("role") not in authz.ADMIN_ROLES:
            return _response(403, {"error": f"role {scope.get('role')!r} may not administer autonomy"})
        market = (event.get("queryStringParameters") or {}).get("market", "us")
        if method == "GET":
            try:
                return _response(200, admin_autonomy.list_autonomy(market))
            except Exception as e:
                return _response(500, {"error": f"{type(e).__name__}: {e}"})
        if method == "POST":
            try:
                body = json.loads(event.get("body") or "{}")
            except json.JSONDecodeError:
                return _response(400, {"error": "invalid JSON body"})
            action_id, level = body.get("action_id"), body.get("level")
            if not action_id or not level:
                return _response(400, {"error": "body must include action_id and level"})
            try:
                return _response(200, admin_autonomy.set_autonomy(
                    action_id, level, actor=scope["user_id"], market=body.get("market", market),
                    scope=body.get("scope", "tenant"), scope_id=body.get("scope_id")))
            except admin_autonomy.InvalidLevel as e:
                return _response(400, {"error": str(e)})
            except admin_autonomy.HardRuleViolation as e:
                return _response(422, {"error": str(e), "hard_rule": True})
            except admin_autonomy.NotFound as e:
                return _response(404, {"error": str(e)})
            except Exception as e:
                return _response(500, {"error": f"{type(e).__name__}: {e}"})
        return _response(405, {"error": "method not allowed"})

    if _EVENTS_RE.match(path) and method == "POST":
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        # Staff-only (any staff role) may publish domain events for the demo.
        if scope.get("role") == "customer" or not scope.get("role"):
            return _response(403, {"error": "only staff may publish events"})
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return _response(400, {"error": "invalid JSON body"})
        detail_type = body.get("detail_type")
        detail = body.get("detail") or {}
        if not detail_type:
            return _response(400, {"error": "body must include detail_type"})
        try:
            if _EVENT_BUS_LIVE:
                pub = event_bus.publish(detail_type, detail)
                return _response(202, {"published": pub, "mode": "bus"})
            # local: dispatch the subscriber in-process
            result = event_bus.dispatch_local(detail_type, detail)
            return _response(200, {"mode": "local-dispatch", "result": result})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    mj = _JOB_RE.match(path)
    if mj and method == "POST":
        job = mj.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        # Agentic jobs are OEM/admin-triggered on demand (the scheduler runs them
        # otherwise). Dealer roles may not kick district-wide jobs.
        if scope.get("role") not in authz.OEM_READ_ROLES:
            return _response(403, {"error": f"role {scope.get('role')!r} may not run jobs"})
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return _response(400, {"error": "invalid JSON body"})
        try:
            out = job_handler.run_job(job, body, apply=bool(body.get("apply", True)), timeout_s=25)
        except _agent_registry.ArgError as e:
            return _response(400, {"error": str(e), "field": e.field})
        except ValueError as e:
            return _response(404, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})
        if out.status == "failed":
            return _response(500, {"error": out.body.get("error"), "agent": _agent_registry.job_owner(job),
                                   "job": job})
        # ok / local -> 200; unknown_after_send -> 202 (still running on the
        # runtime); caller_error -> the runtime's own status and body
        return _response(out.code, out.body)

    # A6 GR-001..GR-008 guardrail check (Admin › AI Guardrails demo, acceptance 15b).
    # POST /oem/guardrails/check {text, phase?} -> guardrail decision (logged).
    if _OEM_GUARDRAILS_CHECK_RE.match(path) and method == "POST":
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        if scope.get("role") not in authz.OEM_READ_ROLES and scope.get("role") not in authz.ADMIN_ROLES:
            return _response(403, {"error": f"role {scope.get('role')!r} may not run guardrail checks"})
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return _response(400, {"error": "invalid JSON body"})
        text = body.get("text")
        if not text:
            return _response(400, {"error": "body must include text"})
        ctx = {"market_id": body.get("market_id") or scope.get("market_id") or "us",
               "user_id": scope.get("user_id"), "district_id": scope.get("scope_node_id"),
               "agent_id": "A6", "tool": body.get("phase") or "check_guardrails",
               "log": bool(body.get("log", True))}
        try:
            return _response(200, oem_guardrails.check_guardrails(text, ctx))
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # GET /oem/guardrails -> active rule table + "blocks this week" rollup.
    if _OEM_GUARDRAILS_RE.match(path) and method == "GET":
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        if scope.get("role") not in authz.OEM_READ_ROLES and scope.get("role") not in authz.ADMIN_ROLES:
            return _response(403, {"error": f"role {scope.get('role')!r} may not read guardrails"})
        market = (event.get("queryStringParameters") or {}).get("market") or scope.get("market_id") or "us"
        try:
            return _response(200, {"rules": oem_guardrails.guardrail_rules(market),
                                   "blocks": oem_guardrails.recent_blocks(market)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    mdp = _OEM_DRAFTPLAN_RE.match(path)
    if mdp and method == "POST":
        account_id = mdp.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        if scope.get("role") not in authz.OEM_READ_ROLES:
            return _response(403, {"error": f"role {scope.get('role')!r} may not draft account plans"})
        try:
            plan = oem_account_plan.draft_account_plan(account_id, author=scope["user_id"])
            # GR-001..GR-008: guardrail-check the generated artifact before returning
            # it. A block strips the plan and returns the safe alternative.
            drafted_text = " ".join(
                str(o.get("action", "")) + " " + str(o.get("why", ""))
                for o in plan.get("objectives", []))
            gr = oem_guardrails.check_guardrails(drafted_text, {
                "market_id": scope.get("market_id") or "us", "user_id": scope.get("user_id"),
                "district_id": scope.get("scope_node_id"), "agent_id": "A6",
                "tool": "draft_account_plan"})
            plan["guardrail"] = {"outcome": gr["outcome"], "violations": gr["violations"]}
            if gr["outcome"] == "block":
                return _response(200, {"blocked": True, "guardrail": plan["guardrail"],
                                       "safe_alternative": gr["safe_alternative"],
                                       "account_id": account_id})
            return _response(200, plan)
        except oem_account_plan.NotFound as e:
            return _response(404, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    if _OEM_ALERTS_RE.match(path) and method == "GET":
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        if scope.get("role") not in authz.OEM_VIEW_ROLES:
            return _response(403, {"error": f"role {scope.get('role')!r} may not read OEM alerts"})
        qs = event.get("queryStringParameters") or {}
        try:
            return _response(200, oem_alerts.build_alerts(
                scope, market_id=qs.get("market"), status=qs.get("status", "open")))
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    if _OEM_BUDGETSHIFT_RE.match(path) and method == "POST":
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        if scope.get("role") not in authz.OEM_READ_ROLES:
            return _response(403, {"error": f"role {scope.get('role')!r} may not recommend budget shifts"})
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            body = {}
        body = body or {}
        market = body.get("market")
        apply = bool(body.get("apply", False))
        # The Campaign Agent (A5) recommendation step runs on its runtime when
        # one is configured (ADR 0009), else in-process with today's arguments.
        # A missing, null or empty market is absent; a present one is checked
        # against the tool's ArgSpec so both paths accept the same input.
        args: dict[str, Any] = {"apply": apply}
        if market not in (None, ""):
            args["market"] = market
        try:
            _agent_registry.validate_args(
                _agent_registry.tool("A5", "recommend_budget_shift"), args)
        except _agent_registry.ArgError as e:
            return _response(400, {"error": str(e), "field": e.field})
        try:
            code, out = _agent_client.call_or_local(
                "A5", "recommend_budget_shift", args,
                claims={**scope, "scope_node_id": scope.get("scope_node_id") or "OEM"},
                correlation_id=_trace.CURRENT_CORRELATION_ID.get(),
                local=lambda: (200, oem_campaigns.recommend_budget_shift(
                    scope, market_id=market, apply=apply, actor=scope["user_id"])))
            return _response(code, out)
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    if _OEM_CAMPAIGNS_RE.match(path) and method == "GET":
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        if scope.get("role") not in authz.OEM_VIEW_ROLES:
            return _response(403, {"error": f"role {scope.get('role')!r} may not read OEM campaigns"})
        market = (event.get("queryStringParameters") or {}).get("market")
        try:
            return _response(200, oem_campaigns.build_campaign_roi(scope, market_id=market))
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    if _SVC_BOARD_RE.match(path) and method == "GET":
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        if scope.get("role") not in authz.SERVICE_READ_ROLES and not scope.get("is_oem"):
            return _response(403, {"error": f"role {scope.get('role')!r} may not read the service board"})
        market = (event.get("queryStringParameters") or {}).get("market")
        try:
            return _response(200, svc_board.build_service_board(scope, market_id=market))
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # A7 predict_next_service — next-due prediction for a VIN (staff/OEM).
    msp = _SVC_PREDICT_RE.match(path)
    if msp and method == "GET":
        vin = msp.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        if scope.get("role") not in authz.SERVICE_READ_ROLES and not scope.get("is_oem"):
            return _response(403, {"error": f"role {scope.get('role')!r} may not read service predictions"})
        try:
            veh = ddb.get_item("DEALER_Vehicles", {"vin": vin})
            if veh and not scope.get("is_oem") and veh.get("dealer_id") not in scope.get("dealer_ids", set()):
                return _response(403, {"error": "vehicle outside caller scope"})
            return _response(200, svc_booking.predict_next_service(vin))
        except svc_booking.NotFound as e:
            return _response(404, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # A7 book_service — open a service journey in BOOK (staff/OEM).
    if _SVC_BOOK_RE.match(path) and method == "POST":
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        if scope.get("role") not in authz.SERVICE_READ_ROLES and not scope.get("is_oem"):
            return _response(403, {"error": f"role {scope.get('role')!r} may not book service"})
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return _response(400, {"error": "invalid JSON body"})
        vin = (body or {}).get("vin")
        if not vin:
            return _response(400, {"error": "body must include 'vin'"})
        veh = ddb.get_item("DEALER_Vehicles", {"vin": vin})
        if veh is None:
            return _response(404, {"error": f"vehicle {vin} not found"})
        if not scope.get("is_oem") and veh.get("dealer_id") not in scope.get("dealer_ids", set()):
            return _response(403, {"error": "vehicle outside caller scope"})
        try:
            return _response(200, svc_booking.book_service(
                vin, services=body.get("services"), wait_type=body.get("wait_type", "drop"),
                preferred_slot=body.get("preferred_slot"), actor=scope["user_id"]))
        except svc_booking.InvalidWaitType as e:
            return _response(400, {"error": str(e)})
        except svc_booking.NotFound as e:
            return _response(404, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # A7 record_inspection / findings / build_estimate — service journey middle.
    def _svc_scope_or_error(sjid):
        sc = _claims(event)
        if not sc.get("user_id"):
            return None, _response(401, {"error": "unauthenticated"})
        if sc.get("role") not in authz.SERVICE_READ_ROLES and not sc.get("is_oem"):
            return None, _response(403, {"error": f"role {sc.get('role')!r} may not act on service journeys"})
        sjrow = ddb.get_item("DEALER_ServiceJourneys", {"service_journey_id": sjid})
        if sjrow is None:
            return None, _response(404, {"error": f"service journey {sjid} not found"})
        if not sc.get("is_oem") and sjrow.get("dealer_id") not in sc.get("dealer_ids", set()):
            return None, _response(403, {"error": "service journey outside caller scope"})
        return sc, None

    misp = _SVC_INSPECTION_RE.match(path)
    if misp and method == "POST":
        sjid = misp.group(1)
        sc, err = _svc_scope_or_error(sjid)
        if err:
            return err
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return _response(400, {"error": "invalid JSON body"})
        findings = (body or {}).get("findings")
        if not isinstance(findings, list) or not findings:
            return _response(400, {"error": "body must include a non-empty 'findings' list"})
        try:
            return _response(200, svc_inspection.record_inspection(sjid, findings, actor=sc["user_id"]))
        except svc_inspection.NotFound as e:
            return _response(404, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    mfnd = _SVC_FINDINGS_RE.match(path)
    if mfnd and method == "GET":
        sjid = mfnd.group(1)
        sc, err = _svc_scope_or_error(sjid)
        if err:
            return err
        try:
            return _response(200, svc_inspection.prioritize_findings(sjid))
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    mest = _SVC_ESTIMATE_RE.match(path)
    if mest and method == "POST":
        sjid = mest.group(1)
        sc, err = _svc_scope_or_error(sjid)
        if err:
            return err
        try:
            return _response(200, svc_inspection.build_estimate(sjid, actor=sc["user_id"]))
        except svc_inspection.NotFound as e:
            return _response(404, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    mprt = _SVC_PARTS_RE.match(path)
    if mprt and method == "GET":
        sjid = mprt.group(1)
        sc, err = _svc_scope_or_error(sjid)
        if err:
            return err
        try:
            return _response(200, svc_repair.track_parts(sjid))
        except svc_repair.NotFound as e:
            return _response(404, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    mln = _SVC_LINE_RE.match(path)
    if mln and method == "POST":
        sjid = mln.group(1)
        sc, err = _svc_scope_or_error(sjid)
        if err:
            return err
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return _response(400, {"error": "invalid JSON body"})
        line_id, status = (body or {}).get("line_id"), (body or {}).get("status")
        if not line_id or not status:
            return _response(400, {"error": "body must include line_id and status"})
        try:
            return _response(200, svc_repair.update_line_status(sjid, line_id, status, actor=sc["user_id"]))
        except svc_repair.NotReady as e:
            return _response(400, {"error": str(e)})
        except svc_repair.NotFound as e:
            return _response(404, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    mrdy = _SVC_READY_RE.match(path)
    if mrdy and method == "POST":
        sjid = mrdy.group(1)
        sc, err = _svc_scope_or_error(sjid)
        if err:
            return err
        try:
            return _response(200, svc_repair.notify_ready(sjid, actor=sc["user_id"]))
        except svc_repair.NotReady as e:
            return _response(409, {"error": str(e)})
        except svc_repair.NotFound as e:
            return _response(404, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    mdf = _SVC_DECLINE_RE.match(path)
    if mdf and method == "POST":
        sjid = mdf.group(1)
        sc, err = _svc_scope_or_error(sjid)
        if err:
            return err
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return _response(400, {"error": "invalid JSON body"})
        try:
            return _response(200, svc_followup.schedule_declined_followup(
                sjid, declined_lines=body.get("declined_lines"),
                cadence_days=int(body.get("cadence_days", 30) or 30), actor=sc["user_id"]))
        except svc_followup.NotFound as e:
            return _response(404, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # A7 service supervisor — state view (stepper + handoff gate + events).
    mss = _SVC_STATE_RE.match(path)
    if mss and method == "GET":
        sjid = mss.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        if scope.get("role") not in authz.SERVICE_READ_ROLES and not scope.get("is_oem"):
            return _response(403, {"error": f"role {scope.get('role')!r} may not read service state"})
        sj = ddb.get_item("DEALER_ServiceJourneys", {"service_journey_id": sjid})
        if sj is None:
            return _response(404, {"error": f"service journey {sjid} not found"})
        if not scope.get("is_oem") and sj.get("dealer_id") not in scope.get("dealer_ids", set()):
            return _response(403, {"error": "service journey outside caller scope"})
        try:
            return _response(200, svc_supervisor.get_service_state(sjid))
        except svc_supervisor.NotFound as e:
            return _response(404, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # A7 service supervisor — advance the service journey one stage.
    msa = _SVC_ADVANCE_RE.match(path)
    if msa and method == "POST":
        sjid = msa.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        if scope.get("role") not in authz.SERVICE_READ_ROLES and not scope.get("is_oem"):
            return _response(403, {"error": f"role {scope.get('role')!r} may not advance service journeys"})
        sj = ddb.get_item("DEALER_ServiceJourneys", {"service_journey_id": sjid})
        if sj is None:
            return _response(404, {"error": f"service journey {sjid} not found"})
        if not scope.get("is_oem") and sj.get("dealer_id") not in scope.get("dealer_ids", set()):
            return _response(403, {"error": "service journey outside caller scope"})
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return _response(400, {"error": "invalid JSON body"})
        evidence = (body or {}).get("evidence")
        if not evidence:
            return _response(400, {"error": "body must include 'evidence'"})
        try:
            return _response(200, svc_supervisor.advance_service_journey(
                sjid, evidence=evidence, actor=scope["user_id"], to_stage=body.get("to_stage")))
        except svc_supervisor.HandoffIncomplete as e:
            return _response(409, {"error": str(e), "stage": e.stage, "missing": e.missing})
        except svc_supervisor.InvalidTransition as e:
            return _response(409, {"error": str(e)})
        except svc_supervisor.NotFound as e:
            return _response(404, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # A7 service supervisor — record estimate decision (service staff or customer-self).
    msap = _SVC_APPROVE_RE.match(path)
    if msap and method == "POST":
        sjid = msap.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        sj = ddb.get_item("DEALER_ServiceJourneys", {"service_journey_id": sjid})
        if sj is None:
            return _response(404, {"error": f"service journey {sjid} not found"})
        if scope.get("role") == "customer":
            if not scope.get("customer_id") or sj.get("customer_id") != scope.get("customer_id"):
                return _response(403, {"error": "customers may only decide on their own service journey"})
        elif scope.get("role") not in authz.SERVICE_READ_ROLES and not scope.get("is_oem"):
            return _response(403, {"error": f"role {scope.get('role')!r} may not record approvals"})
        elif not scope.get("is_oem") and sj.get("dealer_id") not in scope.get("dealer_ids", set()):
            return _response(403, {"error": "service journey outside caller scope"})
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return _response(400, {"error": "invalid JSON body"})
        decision = (body or {}).get("decision")
        if decision not in svc_supervisor.DECISIONS:
            return _response(400, {"error": f"decision must be one of {svc_supervisor.DECISIONS}"})
        try:
            return _response(200, svc_supervisor.record_approval(
                sjid, decision, actor=scope["user_id"], estimate_id=body.get("estimate_id")))
        except svc_supervisor.NotFound as e:
            return _response(404, {"error": str(e)})
        except svc_supervisor.InvalidTransition as e:
            return _response(400, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # A7 -> A2 repurchase/upgrade handoff (§2.1.1): create a purchase journey.
    msh = _SVC_HANDOFF_RE.match(path)
    if msh and method == "POST":
        sjid = msh.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        if scope.get("role") not in authz.SERVICE_READ_ROLES and not scope.get("is_oem"):
            return _response(403, {"error": f"role {scope.get('role')!r} may not hand off to sales"})
        sj = ddb.get_item("DEALER_ServiceJourneys", {"service_journey_id": sjid})
        if sj is None:
            return _response(404, {"error": f"service journey {sjid} not found"})
        if not scope.get("is_oem") and sj.get("dealer_id") not in scope.get("dealer_ids", set()):
            return _response(403, {"error": "service journey outside caller scope"})
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return _response(400, {"error": "invalid JSON body"})
        reason = (body or {}).get("reason", "repurchase/upgrade opportunity")
        try:
            return _response(200, svc_supervisor.handoff_to_sales(sjid, reason, actor=scope["user_id"]))
        except svc_supervisor.NotFound as e:
            return _response(404, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    ms = _SVC_DETAIL_RE.match(path)
    if ms and method == "GET":
        sjid = ms.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        if scope.get("role") not in authz.SERVICE_READ_ROLES and not scope.get("is_oem"):
            return _response(403, {"error": f"role {scope.get('role')!r} may not read service detail"})
        try:
            detail = svc_board.build_ro_detail(sjid)
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})
        if detail is None:
            return _response(404, {"error": f"service journey {sjid} not found"})
        # scope: the service journey's dealer must be in the caller's scope
        if not scope.get("is_oem") and detail.get("meta", {}).get("dealer_id") not in scope.get("dealer_ids", set()):
            return _response(403, {"error": "service journey outside caller scope"})
        return _response(200, detail)

    # A2 supervisor — the journey state machine view (stepper + handoff gate + NBA).
    mst = _STATE_RE.match(path)
    if mst and method == "GET":
        journey_id = mst.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id})
        if journey is None:
            return _response(404, {"error": f"journey {journey_id} not found"})
        try:
            authz.check_journey_access(scope, journey)
        except authz.Forbidden as e:
            return _response(403, {"error": str(e)})
        try:
            return _response(200, supervisor.get_journey_state(journey_id))
        except supervisor.NotFound as e:
            return _response(404, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # A2 supervisor — advance the journey one stage, guarded by validate_handoff.
    madv = _ADVANCE_RE.match(path)
    if madv and method == "POST":
        journey_id = madv.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id})
        if journey is None:
            return _response(404, {"error": f"journey {journey_id} not found"})
        try:
            authz.check_journey_access(scope, journey)
        except authz.Forbidden as e:
            return _response(403, {"error": str(e)})
        # Only staff who own the floor may advance a journey (not customers).
        if scope.get("role") not in authz.ADVISOR_READ_ROLES and not scope.get("is_oem"):
            return _response(403, {"error": f"role {scope.get('role')!r} may not advance journeys"})
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return _response(400, {"error": "invalid JSON body"})
        evidence = (body or {}).get("evidence")
        if not evidence:
            return _response(400, {"error": "body must include 'evidence' for the audit event"})
        try:
            return _response(200, supervisor.advance_journey(
                journey_id, evidence=evidence, actor=scope["user_id"],
                context_updates=body.get("context_updates"), to_stage=body.get("to_stage")))
        except supervisor.HandoffIncomplete as e:
            return _response(409, {"error": str(e), "stage": e.stage, "missing": e.missing})
        except supervisor.InvalidTransition as e:
            return _response(409, {"error": str(e)})
        except supervisor.NotFound as e:
            return _response(404, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # A2 nurture — the read model (plan + consent + channel eligibility + preview).
    mnr = _NURTURE_RE.match(path)
    if mnr and method == "GET":
        journey_id = mnr.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id})
        if journey is None:
            return _response(404, {"error": f"journey {journey_id} not found"})
        try:
            authz.check_journey_access(scope, journey)
        except authz.Forbidden as e:
            return _response(403, {"error": str(e)})
        try:
            return _response(200, nurture.get_nurture_state(journey))
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # A2 nurture — build/refresh the NurturePlan for this journey (staff).
    mnp = _NURTURE_PLAN_RE.match(path)
    if mnp and method == "POST":
        journey_id = mnp.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id})
        if journey is None:
            return _response(404, {"error": f"journey {journey_id} not found"})
        try:
            authz.check_journey_access(scope, journey)
        except authz.Forbidden as e:
            return _response(403, {"error": str(e)})
        if scope.get("role") not in authz.ADVISOR_READ_ROLES and not scope.get("is_oem"):
            return _response(403, {"error": f"role {scope.get('role')!r} may not plan nurture"})
        try:
            return _response(200, nurture.plan_nurture(journey, actor=scope["user_id"]))
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # A2 customer chat — grounded Q&A with refuse/escalate guardrails (§3.2).
    # Customer-facing: the customer (self-scope) OR staff on the journey may use it.
    mch = _CHAT_RE.match(path)
    if mch and method == "POST":
        journey_id = mch.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id})
        if journey is None:
            return _response(404, {"error": f"journey {journey_id} not found"})
        # Auth: a customer may chat only on THEIR OWN journey; staff use scope.
        if scope.get("role") == "customer":
            if not scope.get("customer_id") or journey.get("customer_id") != scope.get("customer_id"):
                return _response(403, {"error": "customers may only chat on their own journey"})
        else:
            try:
                authz.check_journey_access(scope, journey)
            except authz.Forbidden as e:
                return _response(403, {"error": str(e)})
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return _response(400, {"error": "invalid JSON body"})
        question = (body or {}).get("question") or (body or {}).get("message")
        if not question:
            return _response(400, {"error": "body must include 'question'"})
        try:
            return _response(200, qa.answer_customer_question(question, journey))
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # A2 nudge — smallest next commitment + one-click action (staff/OEM; read-only).
    mnu = _NUDGE_RE.match(path)
    if mnu and method == "GET":
        journey_id = mnu.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id})
        if journey is None:
            return _response(404, {"error": f"journey {journey_id} not found"})
        try:
            authz.check_journey_access(scope, journey)
        except authz.Forbidden as e:
            return _response(403, {"error": str(e)})
        target = (event.get("queryStringParameters") or {}).get("commitment")
        try:
            return _response(200, journey_actions.nudge(journey, target_commitment=target))
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # A2 next-best-action — compute + log a recommendation (staff/OEM).
    mnb = _NBA_RE.match(path)
    if mnb and method == "POST":
        journey_id = mnb.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id})
        if journey is None:
            return _response(404, {"error": f"journey {journey_id} not found"})
        try:
            authz.check_journey_access(scope, journey)
        except authz.Forbidden as e:
            return _response(403, {"error": str(e)})
        if scope.get("role") not in authz.ADVISOR_READ_ROLES and not scope.get("is_oem"):
            return _response(403, {"error": f"role {scope.get('role')!r} may not log NBA"})
        try:
            return _response(200, journey_actions.log_nba(journey, actor=scope["user_id"]))
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # A2 book test drive — writes an appointment + visit_booked (staff or customer-self).
    mbt = _BOOKTD_RE.match(path)
    if mbt and method == "POST":
        journey_id = mbt.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id})
        if journey is None:
            return _response(404, {"error": f"journey {journey_id} not found"})
        if scope.get("role") == "customer":
            if not scope.get("customer_id") or journey.get("customer_id") != scope.get("customer_id"):
                return _response(403, {"error": "customers may only book on their own journey"})
        else:
            try:
                authz.check_journey_access(scope, journey)
            except authz.Forbidden as e:
                return _response(403, {"error": str(e)})
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return _response(400, {"error": "invalid JSON body"})
        try:
            return _response(200, journey_actions.book_test_drive(
                journey, slot_start=body.get("slot_start"), slot_end=body.get("slot_end"),
                actor=scope["user_id"]))
        except journey_actions.InvalidSlot as e:
            return _response(400, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # A2 detect_intent_shift — flag budget/competitor/cold/ready shifts (staff/OEM).
    mis = _INTENT_RE.match(path)
    if mis and method == "GET":
        journey_id = mis.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id})
        if journey is None:
            return _response(404, {"error": f"journey {journey_id} not found"})
        try:
            authz.check_journey_access(scope, journey)
        except authz.Forbidden as e:
            return _response(403, {"error": str(e)})
        try:
            return _response(200, intent_shift.detect_intent_shift(journey))
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # A2 create_task — open a dealer alert/task for a journey (staff/OEM).
    mtk = _TASK_RE.match(path)
    if mtk and method == "POST":
        journey_id = mtk.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id})
        if journey is None:
            return _response(404, {"error": f"journey {journey_id} not found"})
        try:
            authz.check_journey_access(scope, journey)
        except authz.Forbidden as e:
            return _response(403, {"error": str(e)})
        if scope.get("role") not in authz.ADVISOR_READ_ROLES and not scope.get("is_oem"):
            return _response(403, {"error": f"role {scope.get('role')!r} may not create tasks"})
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return _response(400, {"error": "invalid JSON body"})
        title = (body or {}).get("title")
        if not title:
            return _response(400, {"error": "body must include 'title'"})
        assignee = body.get("assignee") or journey.get("owner_user_id") or scope["user_id"]
        try:
            return _response(200, intent_shift.create_task(
                assignee, journey_id, title, due_at=body.get("due_at"), actor=scope["user_id"]))
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # A2 Sales Package — aggregate A1/A3/A4 into one package (staff/OEM).
    mpk = _PACKAGE_RE.match(path)
    if mpk and method in ("GET", "POST"):
        journey_id = mpk.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id})
        if journey is None:
            return _response(404, {"error": f"journey {journey_id} not found"})
        try:
            authz.check_journey_access(scope, journey)
        except authz.Forbidden as e:
            return _response(403, {"error": str(e)})
        try:
            if method == "POST":
                if _agent_client.enabled("A2"):
                    remote = _package_on_runtime(journey_id, scope)
                    if remote is not None:
                        return remote
                return _response(200, journey_package.build_sales_package(journey, actor=scope["user_id"]))
            pkg = journey_package.get_package(journey_id)
            if pkg is None:
                return _response(404, {"error": "no package yet; POST to build one"})
            return _response(200, pkg)
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # A1 infer_lifestyle_profile — the /shop intake gate (staff, OEM, or customer-self).
    mls = _LIFESTYLE_RE.match(path)
    if mls and method in ("GET", "POST"):
        journey_id = mls.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id})
        if journey is None:
            return _response(404, {"error": f"journey {journey_id} not found"})
        # customer may infer on their own journey; staff via scope.
        if scope.get("role") == "customer":
            if not scope.get("customer_id") or journey.get("customer_id") != scope.get("customer_id"):
                return _response(403, {"error": "customers may only infer on their own journey"})
        else:
            try:
                authz.check_journey_access(scope, journey)
            except authz.Forbidden as e:
                return _response(403, {"error": str(e)})
        customer = ddb.get_item("DEALER_Customers", {"customer_id": journey.get("customer_id")})
        signals = ddb.scan_filter("DEALER_Signals", "customer_id", journey.get("customer_id")) \
            if journey.get("customer_id") else []
        answers = None
        if method == "POST":
            try:
                answers = (json.loads(event.get("body") or "{}") or {}).get("answers")
            except json.JSONDecodeError:
                return _response(400, {"error": "invalid JSON body"})
        try:
            return _response(200, discovery.infer_lifestyle_profile(customer, signals, answers))
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # A1 recommend_trim_and_packages — needs-justified config for a model (staff/OEM/customer-self).
    mrc = _RECOMMEND_CONFIG_RE.match(path)
    if mrc and method == "GET":
        journey_id = mrc.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id})
        if journey is None:
            return _response(404, {"error": f"journey {journey_id} not found"})
        if scope.get("role") == "customer":
            if not scope.get("customer_id") or journey.get("customer_id") != scope.get("customer_id"):
                return _response(403, {"error": "customers may only configure on their own journey"})
        else:
            try:
                authz.check_journey_access(scope, journey)
            except authz.Forbidden as e:
                return _response(403, {"error": str(e)})
        model_id = (event.get("queryStringParameters") or {}).get("model") or journey.get("primary_model_id")
        if not model_id:
            return _response(400, {"error": "no model: pass ?model=<model_id> or set primary_model_id"})
        customer = ddb.get_item("DEALER_Customers", {"customer_id": journey.get("customer_id")})
        lifestyle = (customer or {}).get("lifestyle_profile") or {}
        budget = (customer or {}).get("budget") or {}
        try:
            return _response(200, discovery.recommend_trim_and_packages(model_id, lifestyle, budget))
        except discovery.NotFound as e:
            return _response(404, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # A3 generate_contextual_suggestions — advisor floor strip (staff/OEM).
    msg = _SUGGESTIONS_RE.match(path)
    if msg and method == "GET":
        journey_id = msg.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id})
        if journey is None:
            return _response(404, {"error": f"journey {journey_id} not found"})
        try:
            authz.check_journey_access(scope, journey)
        except authz.Forbidden as e:
            return _response(403, {"error": str(e)})
        role = (event.get("queryStringParameters") or {}).get("role", "sales")
        try:
            return _response(200, a3_suggestions.generate_contextual_suggestions(journey, role=role))
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # A1 price_build — market-correct incentive/tax/monthly for a config (staff/OEM/customer-self).
    mpb = _PRICE_BUILD_RE.match(path)
    if mpb and method == "GET":
        journey_id = mpb.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id})
        if journey is None:
            return _response(404, {"error": f"journey {journey_id} not found"})
        if scope.get("role") == "customer":
            if not scope.get("customer_id") or journey.get("customer_id") != scope.get("customer_id"):
                return _response(403, {"error": "customers may only price their own journey"})
        else:
            try:
                authz.check_journey_access(scope, journey)
            except authz.Forbidden as e:
                return _response(403, {"error": str(e)})
        qs = event.get("queryStringParameters") or {}
        market = journey.get("market_id") or "us"
        model_id = qs.get("model") or journey.get("primary_model_id")
        if not model_id:
            return _response(400, {"error": "no model to price: pass ?model= or set primary_model_id"})
        customer = ddb.get_item("DEALER_Customers", {"customer_id": journey.get("customer_id")})
        lifestyle = (customer or {}).get("lifestyle_profile") or {}
        budget = (customer or {}).get("budget") or {}
        state = qs.get("state") or (customer or {}).get("region_code")
        try:
            cfg = discovery.recommend_trim_and_packages(model_id, lifestyle, budget)
            rc = cfg["recommended_config"]
            powertrain = qs.get("powertrain") or lifestyle.get("powertrain_openness") or "ICE"
            price = discovery.price_build(rc["config_total"], powertrain, market=market,
                                          state=state, down=float(qs.get("down", 0) or 0))
            price["recommended_config"] = rc
            return _response(200, price)
        except discovery.NotFound as e:
            return _response(404, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # A1 search_inventory — VIN-level availability (staff/OEM/customer-self).
    minv = _INVENTORY_RE.match(path)
    if minv and method == "GET":
        journey_id = minv.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id})
        if journey is None:
            return _response(404, {"error": f"journey {journey_id} not found"})
        if scope.get("role") == "customer":
            if not scope.get("customer_id") or journey.get("customer_id") != scope.get("customer_id"):
                return _response(403, {"error": "customers may only search inventory on their own journey"})
        else:
            try:
                authz.check_journey_access(scope, journey)
            except authz.Forbidden as e:
                return _response(403, {"error": str(e)})
        qs = event.get("queryStringParameters") or {}
        model_id = qs.get("model") or journey.get("primary_model_id")
        if not model_id:
            return _response(400, {"error": "no model: pass ?model= or set primary_model_id"})
        try:
            return _response(200, discovery.search_inventory(
                model_id, trim_id=qs.get("trim") or journey.get("primary_trim_id"),
                dealer_id=journey.get("dealer_id"), market=journey.get("market_id") or "us"))
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # A3 get_competitive_intel — in-catalog cross-shop (staff/OEM).
    mci = _COMPINTEL_RE.match(path)
    if mci and method == "GET":
        journey_id = mci.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id})
        if journey is None:
            return _response(404, {"error": f"journey {journey_id} not found"})
        try:
            authz.check_journey_access(scope, journey)
        except authz.Forbidden as e:
            return _response(403, {"error": str(e)})
        try:
            return _response(200, a3_intel.get_competitive_intel(journey))
        except a3_intel.NotFound as e:
            return _response(404, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # A3 get_incentives — active incentives for the model/market (staff/OEM/customer-self).
    minc = _INCENTIVES_RE.match(path)
    if minc and method == "GET":
        journey_id = minc.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id})
        if journey is None:
            return _response(404, {"error": f"journey {journey_id} not found"})
        if scope.get("role") == "customer":
            if not scope.get("customer_id") or journey.get("customer_id") != scope.get("customer_id"):
                return _response(403, {"error": "customers may only view their own journey incentives"})
        else:
            try:
                authz.check_journey_access(scope, journey)
            except authz.Forbidden as e:
                return _response(403, {"error": str(e)})
        try:
            return _response(200, a3_intel.get_incentives(journey))
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # A3 get_cross_sell — F&I attach recommendations (staff/OEM).
    mcs = _CROSSSELL_RE.match(path)
    if mcs and method == "GET":
        journey_id = mcs.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id})
        if journey is None:
            return _response(404, {"error": f"journey {journey_id} not found"})
        try:
            authz.check_journey_access(scope, journey)
        except authz.Forbidden as e:
            return _response(403, {"error": str(e)})
        try:
            return _response(200, a3_deal.get_cross_sell(journey))
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # A3 structure_deal — F&I deal desk view + credit-compliance gate (staff/OEM).
    mdl = _DEAL_RE.match(path)
    if mdl and method == "GET":
        journey_id = mdl.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id})
        if journey is None:
            return _response(404, {"error": f"journey {journey_id} not found"})
        try:
            authz.check_journey_access(scope, journey)
        except authz.Forbidden as e:
            return _response(403, {"error": str(e)})
        try:
            return _response(200, a3_deal.structure_deal(journey))
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # F&I prequalify — soft prequal scenarios + records compliance (staff or customer-self).
    mpq = _PREQUAL_RE.match(path)
    if mpq and method == "POST":
        journey_id = mpq.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id})
        if journey is None:
            return _response(404, {"error": f"journey {journey_id} not found"})
        if scope.get("role") == "customer":
            if not scope.get("customer_id") or journey.get("customer_id") != scope.get("customer_id"):
                return _response(403, {"error": "customers may only prequalify on their own journey"})
        else:
            try:
                authz.check_journey_access(scope, journey)
            except authz.Forbidden as e:
                return _response(403, {"error": str(e)})
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return _response(400, {"error": "invalid JSON body"})
        amount = body.get("amount")
        if amount is None:
            return _response(400, {"error": "body must include 'amount'"})
        try:
            return _response(200, fni_prequalify.prequalify(
                journey, amount=amount, fico=body.get("fico"), tier=body.get("tier"),
                term_months=body.get("term_months"), down=body.get("down", 0),
                actor=scope["user_id"]))
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    md = _DISCOVERY_RE.match(path)
    if md and method == "GET":
        journey_id = md.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id})
        if journey is None:
            return _response(404, {"error": f"journey {journey_id} not found"})
        try:
            authz.check_journey_access(scope, journey)
        except authz.Forbidden as e:
            return _response(403, {"error": str(e)})
        try:
            return _response(200, discovery.rank_vehicles(journey_id))
        except discovery.NotFound as e:
            return _response(404, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # A4 refresh_valuation — My Garage trade refresh (customer-self or staff).
    mrv = _REVALUE_RE.match(path)
    if mrv and method == "POST":
        journey_id = mrv.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id})
        if journey is None:
            return _response(404, {"error": f"journey {journey_id} not found"})
        if scope.get("role") == "customer":
            if not scope.get("customer_id") or journey.get("customer_id") != scope.get("customer_id"):
                return _response(403, {"error": "customers may only revalue on their own journey"})
        else:
            try:
                authz.check_journey_access(scope, journey)
            except authz.Forbidden as e:
                return _response(403, {"error": str(e)})
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return _response(400, {"error": "invalid JSON body"})
        try:
            return _response(200, equity.refresh_valuation(
                journey.get("customer_id"), mileage=body.get("mileage"),
                condition=body.get("condition")))
        except equity.RefreshError as e:
            return _response(400, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    me = _EQUITY_RE.match(path)
    if me and method == "GET":
        journey_id = me.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id})
        if journey is None:
            return _response(404, {"error": f"journey {journey_id} not found"})
        try:
            authz.check_journey_access(scope, journey)
        except authz.Forbidden as e:
            return _response(403, {"error": str(e)})
        try:
            return _response(200, equity.build_equity(journey_id))
        except equity.NotFound as e:
            return _response(404, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    # §8 R2 — OEM account-plan approval workflow.
    #   POST /approvals/plan/{id}/submit  -> OEM author submits for approval
    #   POST /approvals/plan/{id}/decide  -> regional/admin approves|rejects
    #   GET  /approvals/plan/{id}         -> current approval state
    map_ = _APPROVAL_SUBMIT_RE.match(path)
    if map_ and method == "POST":
        plan_id = map_.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        if scope.get("role") not in authz.APPROVAL_SUBMIT_ROLES:
            return _response(403, {"error": f"role {scope.get('role')!r} may not submit account plans"})
        try:
            return _response(200, plan_approval.submit_plan(plan_id, actor=scope["user_id"]))
        except plan_approval.NotFound as e:
            return _response(404, {"error": str(e)})
        except plan_approval.InvalidState as e:
            return _response(409, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    mad = _APPROVAL_DECIDE_RE.match(path)
    if mad and method == "POST":
        plan_id = mad.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        if scope.get("role") not in authz.APPROVAL_DECIDE_ROLES:
            return _response(403, {"error": f"role {scope.get('role')!r} may not decide account plans"})
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return _response(400, {"error": "invalid JSON body"})
        decision = (body or {}).get("decision")
        if decision not in plan_approval.DECISIONS:
            return _response(400, {"error": f"decision must be one of {plan_approval.DECISIONS}"})
        try:
            return _response(200, plan_approval.decide_plan(plan_id, decision, actor=scope["user_id"]))
        except plan_approval.NotFound as e:
            return _response(404, {"error": str(e)})
        except plan_approval.InvalidState as e:
            return _response(409, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    mas = _APPROVAL_STATUS_RE.match(path)
    if mas and method == "GET":
        plan_id = mas.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        if scope.get("role") not in (authz.APPROVAL_SUBMIT_ROLES | authz.APPROVAL_DECIDE_ROLES):
            return _response(403, {"error": f"role {scope.get('role')!r} may not view account-plan approvals"})
        try:
            return _response(200, plan_approval.plan_status(plan_id))
        except plan_approval.NotFound as e:
            return _response(404, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})

    m = _ASSIST_RE.match(path)
    if m and method == "GET":
        journey_id = m.group(1)
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        # Fetch the journey first so scope can be checked against its dealer.
        journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id})
        if journey is None:
            return _response(404, {"error": f"journey {journey_id} not found"})
        try:
            authz.check_journey_access(scope, journey)
        except authz.Forbidden as e:
            return _response(403, {"error": str(e)})
        try:
            payload = assist.build_assist(journey_id)
        except assist.NotFound as e:
            return _response(404, {"error": str(e)})
        except Exception as e:
            return _response(500, {"error": f"{type(e).__name__}: {e}"})
        return _response(200, payload)

    # --- Tier A depth: A6 planner suite (OEM read roles / admin) ---
    def _oem_gate(ev):
        sc = _claims(ev)
        if not sc.get("user_id"):
            return None, _response(401, {"error": "unauthenticated"})
        if sc.get("role") not in authz.OEM_READ_ROLES and sc.get("role") not in authz.ADMIN_ROLES:
            return None, _response(403, {"error": f"role {sc.get('role')!r} not permitted"})
        return sc, None

    mc = _OEM_COMPOSITE_RE.match(path)
    if mc and method == "GET":
        sc, err = _oem_gate(event)
        if err:
            return err
        from ..oem import planner
        return _response(200, planner.compute_composite(mc.group(1), market=sc.get("market_id") or "us"))

    mev = _OEM_EVREADY_RE.match(path)
    if mev and method == "GET":
        sc, err = _oem_gate(event)
        if err:
            return err
        from ..oem import planner
        return _response(200, planner.get_ev_readiness(mev.group(1)))

    mco = _OEM_COMPLIANCE_RE.match(path)
    if mco and method == "GET":
        sc, err = _oem_gate(event)
        if err:
            return err
        from ..oem import planner
        return _response(200, planner.get_compliance(mco.group(1)))

    if _OEM_SCENARIO_RE.match(path) and method == "POST":
        sc, err = _oem_gate(event)
        if err:
            return err
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return _response(400, {"error": "invalid JSON body"})
        from ..oem import planner
        return _response(200, planner.simulate_scenario(body.get("district_id", "DIST-07"),
                        body.get("levers"), market=sc.get("market_id") or "us"))

    if _OEM_WEEKEND_RE.match(path) and method == "GET":
        sc, err = _oem_gate(event)
        if err:
            return err
        qs = event.get("queryStringParameters") or {}
        from ..oem import planner
        return _response(200, planner.predict_weekend(qs.get("district", "DIST-07"),
                        market=sc.get("market_id") or "us"))

    # --- A4 prospects / acquisition (advisor, inventory, oem) ---
    if _A4_PROSPECTS_RE.match(path) and method == "GET":
        sc = _claims(event)
        if not sc.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        qs = event.get("queryStringParameters") or {}
        from ..agents import prospects
        return _response(200, prospects.find_upgrade_prospects(
            dealer_id=qs.get("dealer"), market=sc.get("market_id") or "us",
            limit=int(qs["limit"]) if qs.get("limit") else None))

    macq = _A4_ACQUIRE_RE.match(path)
    if macq and method == "POST":
        sc = _claims(event)
        if not sc.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        from ..agents import prospects
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            body = {}
        return _response(200, prospects.recommend_acquisition(
            macq.group(1), market=sc.get("market_id") or "us", apply=bool(body.get("apply", False))))

    # --- A2 Plays: run a play for a journey (AR-10) ---
    mrp = _PLAY_RUN_RE.match(path)
    if mrp and method == "POST":
        sc = _claims(event)
        if not sc.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            body = {}
        from ..journeys import plays
        return _response(200, plays.run_play(mrp.group(1), play_id=body.get("play_id"),
                        trigger=body.get("trigger"), apply=bool(body.get("apply", False))))

    # --- Tier C: knowledge grounding (retrieve cited passages) ---
    if _KNOWLEDGE_RE.match(path) and method == "POST":
        sc = _claims(event)
        if not sc.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return _response(400, {"error": "invalid JSON body"})
        q = body.get("query")
        if not q:
            return _response(400, {"error": "body must include query"})
        from ..platform import knowledge
        out = knowledge.get_knowledge_base().retrieve(
            q, collection=body.get("collection"), k=int(body.get("k", 3)))
        # `synthesize` turns retrieval into a grounded answer in the agent's own
        # voice (A3/A7 chat). Citations stay the retrieved source_refs, so the
        # answer remains attributable; falls back to the passages when the model
        # is unreachable.
        if body.get("synthesize"):
            from ..platform import generate
            passages = [p["passage"] for p in out.get("passages", [])]
            fallback = (" · ".join(x[:160] for x in passages[:2])
                        if passages else "Nothing in the indexed procedures, bulletins or history matches that yet.")
            gen = generate.ground(q, [], passages,
                                  persona=str(body.get("persona") or "a service advisor's assistant"),
                                  fallback=fallback)
            out["answer"] = gen["text"]
            out["engine"] = gen["engine"]
            out["model"] = gen.get("model")
            out["retrieval"] = knowledge.get_knowledge_base().__class__.__name__
        return _response(200, out)

    # --- Amazon Quick (quicksight) — plug-and-play; disabled unless ENABLE_QUICK ---
    if _QUICK_FLOWS_RE.match(path) and method == "GET":
        sc, err = _oem_gate(event)
        if err:
            return err
        from ..platform import quick
        return _response(200, quick.get_quick().list_flows())

    if _QUICK_DASHBOARDS_RE.match(path) and method == "GET":
        sc, err = _oem_gate(event)
        if err:
            return err
        from ..platform import quick
        return _response(200, quick.get_quick().list_dashboards())

    meq = _QUICK_EMBED_RE.match(path)
    if meq and method == "GET":
        sc, err = _oem_gate(event)
        if err:
            return err
        from ..platform import quick
        # Pass the caller scope so per-user embedding (RETAIL_OPS_QUICK_PER_USER_EMBED=1)
        # can register them as a Quick Sight reader in their role's sharing group (spec §8).
        caller = {"user_id": sc.get("user_id"), "role": sc.get("role"),
                  "scope_node_id": sc.get("scope_node_id"),
                  "dealer_ids": ",".join(sorted(sc.get("dealer_ids", set()))),
                  "is_oem": sc.get("is_oem")}
        return _response(200, quick.get_quick().embed_url(meq.group(1), caller))

    # Cross-persona read via the AgentCore Gateway (spec §6). This is the ONLY
    # sanctioned way for a non-OEM persona to read OEM-owned data: the request is
    # forwarded to the Gateway's scoped MCP tools, never a direct OEM table read.
    # OEM callers should use their own /oem/* routes instead.
    mgw = _GATEWAY_OEM_RE.match(path)
    if mgw and method == "GET":
        scope = _claims(event)
        if not scope.get("user_id"):
            return _response(401, {"error": "unauthenticated"})
        tool = {
            "accounts": "oem_list_accounts",
            "account": "oem_get_account",
            "alerts": "oem_get_alerts",
            "incentives": "oem_get_incentive_programs",
        }.get(mgw.group(1))
        if tool is None:
            return _response(404, {"error": f"unknown gateway tool {mgw.group(1)!r}"})
        qs = event.get("queryStringParameters") or {}
        claims = {
            "user_id": scope.get("user_id"), "role": scope.get("role"),
            "scope_node_id": scope.get("scope_node_id"), "market_id": scope.get("market_id"),
            "dealer_ids": ",".join(sorted(scope.get("dealer_ids", set()))),
            "is_oem": scope.get("is_oem"), "customer_id": scope.get("customer_id", ""),
        }
        args = {k: v for k, v in qs.items() if k in ("account_id", "market_id")}
        from ..gateway import client as gw_client
        try:
            result = gw_client.call(tool, claims, **args)
        except Exception as e:
            return _response(502, {"error": f"gateway call failed: {type(e).__name__}: {e}"})
        code = 503 if result.get("enabled", True) is False else 200
        return _response(code, {"via": "agentcore-gateway", "tool": tool, "result": result})

    return _response(404, {"error": f"no route for {method} {path}"})


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """API Gateway proxy entrypoint. Wraps the router with a per-request
    correlation id (AR-6): one id threads the request across any agent/tool it
    fans out to, rides back on the X-Correlation-Id response header, and anchors
    a single PLAT_AgentTraces span for the request. Tracing is best-effort and
    never changes the response."""
    # Honor an inbound correlation id (client-provided) so a trace can span the
    # caller too; otherwise mint one.
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    corr = headers.get("x-correlation-id") or _trace.new_correlation_id()
    _id_token = _CURRENT_CORRELATION_ID.set(corr)
    _origin_token = _CURRENT_ORIGIN.set(headers.get("origin"))
    _corr_token = _trace.CURRENT_CORRELATION_ID.set(corr)

    import time as _time
    path = event.get("path") or event.get("rawPath") or ""
    method = (event.get("httpMethod")
              or (event.get("requestContext", {}).get("http", {}) or {}).get("method") or "GET")
    start = _time.monotonic()
    outcome = "ok"
    status = 500
    try:
        resp = _route(event, context)
        status = int(resp.get("statusCode", 200)) if isinstance(resp, dict) else 200
        if status >= 500:
            outcome = "error"
        return resp
    except Exception:
        outcome = "error"
        raise
    finally:
        if _trace.enabled():
            _write_request_trace(event, method, path, corr, outcome, status,
                                 int((_time.monotonic() - start) * 1000))
        _CURRENT_CORRELATION_ID.reset(_id_token)
        _CURRENT_ORIGIN.reset(_origin_token)
        _trace.CURRENT_CORRELATION_ID.reset(_corr_token)


def _write_request_trace(event: dict[str, Any], method: str, path: str, corr: str, outcome: str,
                         status: int, latency_ms: int) -> None:
    """The request's one PLAT_AgentTraces span: an agent automation's run when the
    route is one (automation_traces) and it succeeded, else an access-layer row."""
    claims = _claims(event)
    who = {"user_id": claims.get("user_id"), "role": claims.get("role")}
    run = _automation_traces.match(method, path) if status < 400 else None
    if run is None:
        _trace.write_trace(tool=f"{method} {path}", agent_id="L4", correlation_id=corr,
                           outcome=outcome, latency_ms=latency_ms, market=claims.get("market_id"),
                           routing_decision=f"access-layer route {method} {path}", extra=who)
        return
    dealers = sorted(claims.get("dealer_ids") or [])
    extra = {**who, "route": f"{method} {path}", "status": status,
             "dealer_id": dealers[0] if len(dealers) == 1 else None}
    if run.service_journey_id:
        extra["service_journey_id"] = run.service_journey_id
    journey_id = run.journey_id
    if run.lead_id:
        # a Lead Desk run lands on the lead's feed, and on its journey's once the lead has one
        extra["lead_id"] = run.lead_id
        try:
            lead = ddb.get_item("DEALER_Leads", {"lead_id": run.lead_id})
            journey_id = (lead or {}).get("journey_id") or None
        except Exception:
            journey_id = None
    _trace.write_trace(tool=run.tool, agent_id=run.agent_id, correlation_id=corr, outcome=outcome,
                       latency_ms=latency_ms, market=claims.get("market_id"), journey_id=journey_id,
                       routing_decision=f"on-demand automation via {method} {path}", extra=extra)
