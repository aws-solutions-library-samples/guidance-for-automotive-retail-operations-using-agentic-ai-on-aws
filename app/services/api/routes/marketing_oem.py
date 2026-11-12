"""Marketing Studio + OEM Workspace shell routes (spec §5.7, §5.9, §5.16, §6).

  GET   /marketing/campaigns                       A5 ROI + funnel + spend + attribution + shift
  POST  /marketing/campaigns/budget-shift/submit   recommendation -> DEALER_Approvals (oem_finance)
  POST  /marketing/ask                             analytics Q&A (Quick Q topics when enabled)
  GET   /marketing/audiences                       rule-built segments (Personalize when configured)
  POST  /marketing/audiences/{segment_id}/export   hashed-id export manifest
  GET   /config/nurture                            Journey Ops: active + draft + versions
  PUT   /config/nurture                            save draft sections
  POST  /config/nurture/publish                    {version}
  POST  /config/nurture/rollback                   {to_version}
  POST  /config/nurture/preview                    {template, journey_id?, variant_id?}
  GET   /config/nurture/analytics
  GET   /oem/accounts · GET /oem/accounts/{id}     account list / detail
  GET   /oem/region                                Region View
  PATCH /oem/alerts/{id}                           {action: acknowledge|resolve, note?}
  POST  /oem/ask                                   workspace chat (Quick A6 agent when enabled)
"""
from __future__ import annotations

import datetime as _dt
import re

from ....agentcore import client, registry
from ...common import authz, ddb, trace
from ...marketing import analytics, audiences, journey_ops
from ...oem import account_plan as _account_plan
from ...oem import workspace
from .. import routing as r


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

OEM_ROLES = authz.OEM_READ_ROLES
# Read-only views also open to the Administrator (platform_admin, §5.2); the
# write routes keep the OEM role sets.
OEM_VIEW = authz.OEM_VIEW_ROLES
MARKETING_READ = authz.OEM_VIEW_ROLES
SHIFT_SUBMIT = analytics.SUBMIT_ROLES
AUDIENCE_ROLES = frozenset({"oem_marketing", "oem_cx"})
AUDIENCE_READ = AUDIENCE_ROLES | {authz.PLATFORM_ADMIN}
JOURNEY_OPS_READ = frozenset({"oem_cx", "oem_marketing", authz.PLATFORM_ADMIN})
JOURNEY_OPS_EDIT = frozenset({"oem_cx"})
ALERT_ACTORS = frozenset({"oem_dm", "oem_regional"})
# field roles that own dealer contact and district comms (§5.9)
CONTACT_REPORT_AUTHORS = frozenset({"oem_dm", "oem_regional", "oem_cx"})
COMMS_FORWARDERS = frozenset({"oem_dm", "oem_regional", "oem_sales_ops", "oem_marketing"})


def _market(event, claims) -> str:
    return r.qs(event).get("market") or claims.get("market_id") or "us"


def _scope(claims) -> dict:
    return {**claims, "scope_node_id": claims.get("scope_node_id") or "OEM"}


# ---- marketing ---------------------------------------------------------------

def _campaigns(event, claims, _m):
    r.roles(claims, MARKETING_READ)
    return 200, analytics.campaign_overview(_scope(claims), _market(event, claims))


def _shift_submit(event, claims, _m):
    """Submit the Campaign Agent's (A5) budget-shift recommendation for approval:
    on the A5 runtime when one is configured (ADR 0009), else in-process. The
    call is idempotent by approval id, so an unanswered runtime call is re-run
    locally (a finished one then answers 409 "already pending", as today)."""
    r.roles(claims, SHIFT_SUBMIT)
    b = r.body(event)
    market = b.get("market") or _market(event, claims)
    note = b.get("note")
    args = {"market": market} if note is None else {"market": market, "note": note}
    try:
        registry.validate_args(registry.tool("A5", "submit_budget_shift"), args)
    except registry.ArgError as e:
        raise r.Bad(400, str(e), field=e.field) from None
    code, out = client.call_or_local(
        "A5", "submit_budget_shift", args, claims=_scope(claims),
        correlation_id=trace.CURRENT_CORRELATION_ID.get(),
        local=lambda: (200, analytics.submit_budget_shift(_scope(claims), claims["user_id"], market, note=note)))
    if code != 200:
        return code, out                 # caller_error / failed from the runtime, unchanged
    return (201 if out.get("submitted") else 409), out


def _ask(event, claims, _m):
    r.roles(claims, MARKETING_READ)
    b = r.body(event)
    r.require(b, "question")
    return 200, analytics.ask_analytics(b["question"], _scope(claims), _market(event, claims))


def _audiences(event, claims, _m):
    r.roles(claims, AUDIENCE_READ)
    return 200, audiences.build_audiences(_scope(claims), _market(event, claims))


def _audiences_refresh(event, claims, _m):
    """A5 refresh_audiences: recompute the segments and report the counts."""
    r.roles(claims, AUDIENCE_ROLES)
    return 200, audiences.refresh_audiences(_scope(claims), claims["user_id"], _market(event, claims))


def _audience_export(event, claims, m):
    r.roles(claims, {"oem_marketing"})
    b = r.body(event)
    r.require(b, "platform")
    return 201, audiences.export_audience(m.group(1), b["platform"], claims["user_id"], _scope(claims),
                                          _market(event, claims))


# ---- Journey Ops (PLAT_ConfigNurture) ------------------------------------------

def _jo_get(event, claims, _m):
    r.roles(claims, JOURNEY_OPS_READ)
    out = journey_ops.get_journey_ops(_market(event, claims))
    out["can_edit"] = claims.get("role") in JOURNEY_OPS_EDIT
    return 200, out


def _jo_put(event, claims, _m):
    r.roles(claims, JOURNEY_OPS_EDIT)
    b = r.body(event)
    changes = {k: v for k, v in b.items() if k in journey_ops.SECTIONS}
    if not changes:
        raise r.Bad(400, f"body must include at least one of {list(journey_ops.SECTIONS)}")
    return 200, journey_ops.save_draft(b.get("market") or _market(event, claims), changes, claims["user_id"],
                                       note=b.get("note"))


def _jo_publish(event, claims, _m):
    r.roles(claims, JOURNEY_OPS_EDIT)
    b = r.body(event)
    r.require(b, "version")
    return 200, journey_ops.publish(b.get("market") or _market(event, claims), int(b["version"]), claims["user_id"])


def _jo_rollback(event, claims, _m):
    r.roles(claims, JOURNEY_OPS_EDIT)
    b = r.body(event)
    r.require(b, "to_version")
    return 200, journey_ops.rollback(b.get("market") or _market(event, claims), int(b["to_version"]), claims["user_id"])


def _jo_preview(event, claims, _m):
    r.roles(claims, JOURNEY_OPS_READ)
    b = r.body(event)
    r.require(b, "template")
    if not isinstance(b["template"], dict):
        raise r.Bad(400, "template must be an object")
    return 200, journey_ops.preview(b.get("market") or _market(event, claims), b["template"],
                                    journey_id=b.get("journey_id"), variant_id=b.get("variant_id"))


def _jo_analytics(event, claims, _m):
    r.roles(claims, JOURNEY_OPS_READ)
    return 200, journey_ops.analytics(_market(event, claims))


def _jo_proposals(event, claims, _m):
    """A2's propose_policy_change review feed (read-only; a human applies them)."""
    r.roles(claims, JOURNEY_OPS_READ)
    out = journey_ops.propose_policy_changes(_market(event, claims))
    out["can_apply"] = claims.get("role") in JOURNEY_OPS_EDIT
    return 200, out


# ---- OEM workspace ------------------------------------------------------------

def _accounts(event, claims, _m):
    r.roles(claims, OEM_VIEW)
    return 200, workspace.list_accounts(_scope(claims), _market(event, claims))


def _account(event, claims, m):
    r.roles(claims, OEM_VIEW)
    return 200, workspace.account_detail(m.group(1), _scope(claims), _market(event, claims))


def _region(event, claims, _m):
    r.roles(claims, OEM_VIEW)
    return 200, workspace.region_view(_scope(claims), _market(event, claims))


def _contact_report_post(event, claims, _m):
    """File a dealer contact report; each follow-up becomes a dealer task."""
    r.roles(claims, CONTACT_REPORT_AUTHORS)
    b = r.body(event)
    r.require(b, "dealer_id", "summary")
    if b.get("visit_type") and b["visit_type"] not in workspace._VISIT_TYPES:
        raise r.Bad(400, f"visit_type must be one of {list(workspace._VISIT_TYPES)}")
    if b.get("sentiment") and b["sentiment"] not in workspace._SENTIMENTS:
        raise r.Bad(400, f"sentiment must be one of {list(workspace._SENTIMENTS)}")
    for key, kind in (("topics", list), ("followups", list)):
        if b.get(key) is not None and not isinstance(b[key], kind):
            raise r.Bad(400, f"{key} must be a list")
    for f in b.get("followups") or []:
        if not isinstance(f, dict):
            raise r.Bad(400, "each followup must be an object {text, due?}")
    return 201, workspace.file_contact_report(b, claims["user_id"], _scope(claims), _market(event, claims))


def _comms_forward(event, claims, _m):
    """Forward the comms digest to dealers with the DM's comments."""
    r.roles(claims, COMMS_FORWARDERS)
    b = r.body(event)
    dealers = b.get("digest") if b.get("digest") is not None else b.get("dealers")
    if not isinstance(dealers, list) or not [d for d in dealers if str(d or "").strip()]:
        raise r.Bad(400, "digest must be a non-empty list of dealer ids")
    return 201, workspace.forward_comms_digest(b, claims["user_id"], _scope(claims), _market(event, claims))


def _conquest_offers(event, claims, _m):
    r.roles(claims, OEM_VIEW)
    return 200, workspace.conquest_offers(_scope(claims), _market(event, claims))


PLAN_TYPES = ("account_plan", "action_plan", "opportunity_brief", "win_strategy")
# account_plan / action_plan are the saved, versioned OEM_AccountPlans artifacts;
# the other two are narrative artifacts the workspace agent writes on demand.
_PLAN_PROMPTS = {
    "opportunity_brief": ("Draft an opportunity brief for {name}: where is the volume and share "
                          "opportunity, grounded in the composite and the open alerts?"),
    "win_strategy": "Draft a win strategy for {name}: how do we close the target gap this quarter?",
}


def _plan_generate(event, claims, m):
    """Generate one of the account's A6 artifacts (§5.9 / design §4.3).

    An alias over the generation that already exists: the saved plan types go
    through A6 `draft_account_plan` (guardrail-checked, DRAFT only — A6 never
    publishes), and the narrative types through the guardrail-checked workspace
    agent. Returns the versioned-artifact shape the account detail reads."""
    from ...oem import guardrails as oem_guardrails
    oem_account_plan = _account_plan
    r.roles(claims, OEM_ROLES)
    account_id, plan_type = m.group(1), m.group(2)
    if plan_type not in PLAN_TYPES:
        raise r.Bad(404, f"unknown plan type {plan_type!r} (expected one of {list(PLAN_TYPES)})")
    market = _market(event, claims)
    scope = _scope(claims)
    # scope + existence check (raises NotFound / Forbidden mapped in ERRORS)
    account = workspace.account_detail(account_id, scope, market)["account"]

    if plan_type in ("account_plan", "action_plan"):
        plan = oem_account_plan.draft_account_plan(account_id, author=claims["user_id"])
        drafted_text = " ".join(f"{o.get('action', '')} {o.get('why', '')}" for o in plan.get("objectives") or [])
        gr = oem_guardrails.check_guardrails(drafted_text, {
            "market_id": market, "user_id": claims.get("user_id"),
            "district_id": claims.get("scope_node_id"), "agent_id": "A6",
            "tool": "draft_account_plan"})
        if gr["outcome"] == "block":
            return 200, {"blocked": True, "plan_type": plan_type, "account_id": account_id,
                         "guardrail": {"outcome": gr["outcome"], "violations": gr["violations"]},
                         "safe_alternative": gr["safe_alternative"]}
        saved = ddb.get_item("OEM_AccountPlans", {"plan_id": plan["plan_id"]}) or {}
        return 200, {**plan, "plan_type": plan_type,
                     "version": saved.get("version", 1), "approval_status": saved.get("approval_status"),
                     "author_user_id": saved.get("author_user_id") or claims["user_id"],
                     "effective_from": saved.get("effective_from"), "updated_at": saved.get("updated_at"),
                     "guardrail": {"outcome": gr["outcome"], "violations": gr["violations"]}}

    question = _PLAN_PROMPTS[plan_type].format(name=account.get("name") or account_id)
    answer = workspace.ask(question, scope, view=f"account_{plan_type}",
                           dealer_id=account.get("dealer_id"), market=market)
    if answer.get("guardrail"):
        return 200, {"blocked": True, "plan_type": plan_type, "account_id": account_id,
                     "guardrail": answer["guardrail"], "safe_alternative": answer.get("answer")}
    return 200, {"plan_type": plan_type, "account_id": account_id,
                 "generated_text": answer.get("answer"), "citations": answer.get("citations") or [],
                 "guardrail": None, "prompt": question, "via": answer.get("via"),
                 "generated_at": _now_iso(), "generated_by": claims["user_id"],
                 "status": "generated", "persisted": False,
                 "note": "Narrative artifact generated on demand — not a saved plan version; "
                         "the saved, publishable artifacts are account_plan and action_plan.",
                 "source_ref": f"A6 workspace generation for {account_id} ({plan_type})"}


def _alert_patch(event, claims, m):
    r.roles(claims, ALERT_ACTORS)
    b = r.body(event)
    r.require(b, "action")
    return 200, workspace.update_alert(m.group(1), b["action"], claims["user_id"], _scope(claims), note=b.get("note"))


def _oem_ask(event, claims, _m):
    r.roles(claims, OEM_VIEW)
    b = r.body(event)
    r.require(b, "question")
    return 200, workspace.ask(b["question"], _scope(claims), view=b.get("view"), dealer_id=b.get("dealer_id"),
                              market=_market(event, claims))


ROUTES = [
    ("GET", re.compile(r"^/marketing/campaigns$"), _campaigns),
    ("POST", re.compile(r"^/marketing/campaigns/budget-shift/submit$"), _shift_submit),
    ("POST", re.compile(r"^/marketing/ask$"), _ask),
    ("GET", re.compile(r"^/marketing/audiences$"), _audiences),
    ("POST", re.compile(r"^/marketing/audiences/refresh$"), _audiences_refresh),
    ("POST", re.compile(r"^/marketing/audiences/([^/]+)/export$"), _audience_export),
    ("GET", re.compile(r"^/config/nurture$"), _jo_get),
    ("PUT", re.compile(r"^/config/nurture$"), _jo_put),
    ("POST", re.compile(r"^/config/nurture/publish$"), _jo_publish),
    ("POST", re.compile(r"^/config/nurture/rollback$"), _jo_rollback),
    ("POST", re.compile(r"^/config/nurture/preview$"), _jo_preview),
    ("GET", re.compile(r"^/config/nurture/analytics$"), _jo_analytics),
    ("GET", re.compile(r"^/config/nurture/proposals$"), _jo_proposals),
    ("GET", re.compile(r"^/oem/accounts$"), _accounts),
    ("GET", re.compile(r"^/oem/accounts/([^/]+)$"), _account),
    ("GET", re.compile(r"^/oem/region$"), _region),
    ("POST", re.compile(r"^/oem/contact-reports$"), _contact_report_post),
    ("POST", re.compile(r"^/oem/comms/forward$"), _comms_forward),
    ("GET", re.compile(r"^/oem/conquest/offers$"), _conquest_offers),
    ("POST", re.compile(r"^/accounts/([^/]+)/plans/([^/]+)/generate$"), _plan_generate),
    ("PATCH", re.compile(r"^/oem/alerts/([^/]+)$"), _alert_patch),
    ("POST", re.compile(r"^/oem/ask$"), _oem_ask),
]

ERRORS = [
    (journey_ops.NotFound, 404), (journey_ops.Conflict, 409), (journey_ops.InvalidConfig, 422),
    (audiences.NotFound, 404),
    (workspace.NotFound, 404), (workspace.Forbidden, 403), (workspace.InvalidAction, 409),
    (_account_plan.NotFound, 404),
]
