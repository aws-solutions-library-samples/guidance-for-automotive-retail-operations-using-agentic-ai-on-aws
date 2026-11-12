# ruff: noqa: S101
"""The Administrator role (`platform_admin`, spec §5.2 / §8.1, decided 2026-10-21).

Granted only through the SSO allowlist. It opens every staff module (never the
customer self-service screens), is never refused what a tenant admin may do,
and leaves every existing role exactly as it was.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.agentcore import agent as runtime
from app.agentcore import registry
from app.services.api import authorizer, handler, platform_routes
from app.services.api import routes as domain_routes
from app.services.approvals import generic as approvals
from app.services.common import authz, crosspersona, ddb, personas, sso_allowlist
from tests.app.conftest import call

ROOT = Path(__file__).resolve().parents[2]
PA = personas.PLATFORM_ADMIN
ADMIN_ID = sso_allowlist.admin_user_id("jdoe")


@pytest.fixture
def admin(fake_ddb):
    """One allowlisted Administrator row, as the sso-admins deploy step writes it."""
    ddb.put_item("PLAT_Users", sso_allowlist.admin_row("jdoe", "us"))
    return ADMIN_ID


def _principal(user_id: str) -> dict:
    """The authorizer context for a user, exactly as API Gateway hands it on."""
    return authorizer.resolve_principal(f"Bearer user:{user_id}")


def _event(method: str, path: str, principal: dict, *, body=None, query=None, headers=None) -> dict:
    return {"httpMethod": method, "path": path, "queryStringParameters": query, "headers": headers or {},
            "body": json.dumps(body) if body is not None else None,
            "requestContext": {"authorizer": dict(principal)}}


# --- the persona matrix ---------------------------------------------------------------

def test_existing_roles_are_byte_identical_to_main():
    """Golden snapshot of every existing role's home, screens and label, taken
    from personas.py on main before the Administrator existed; updated for the
    2026-10-28 decisions (sales advisor + dealer.leads, OEM Sales Ops without
    admin.home; spec §13)."""
    golden = json.loads((ROOT / "tests" / "golden" / "personas_role_screens.json").read_text())
    assert list(personas.STAFF_GROUPS) == golden["staff_groups"]
    assert len(personas.STAFF_GROUPS) == 19 and PA not in personas.STAFF_GROUPS
    for role, want in golden["personas"].items():
        home, screens = personas.PERSONAS[role]
        assert {"home": home, "screens": sorted(screens)} == want, role
        assert personas.DISPLAY_NAMES[role] == golden["display_names"][role]
    assert set(personas.PERSONAS) == set(golden["personas"]) | {PA}


def test_existing_role_sets_are_unchanged():
    assert authz.ADMIN_ROLES - {PA} == {"admin_tenant", "admin_dealer"}
    assert PA not in authz.OEM_READ_ROLES and PA not in authz.APPROVAL_SUBMIT_ROLES
    assert authz.APPROVAL_DECIDE_ROLES - {PA} == {"oem_regional", "admin_tenant", "admin_dealer"}
    assert PA not in authz.ADVISOR_READ_ROLES | authz.SERVICE_READ_ROLES | authz.COCKPIT_ROLES
    assert authz.TENANT_ADMIN_ROLES == {"admin_tenant", PA}
    assert authz.OEM_VIEW_ROLES == authz.OEM_READ_ROLES | {PA}


def test_administrator_opens_every_staff_screen_and_no_customer_screen():
    home, screens = personas.PERSONAS[PA]
    staff_union = frozenset().union(*(personas.PERSONAS[g][1] for g in personas.STAFF_GROUPS))
    assert home == "admin.home" and screens == staff_union
    assert screens == {s for s in personas.SCREENS if not s.startswith("customer.")}
    assert not any(s.startswith("customer.") for s in screens)
    assert personas.display_name(PA) == "Administrator"
    assert PA in personas.STAFF_ROLES and personas.STAFF_ROLES == set(personas.STAFF_GROUPS) | {PA}


def test_me_home_offers_every_staff_app(admin):
    code, me = call(_event("GET", "/me/home", _principal(admin)))
    assert code == 200 and me["role"] == PA and me["role_name"] == "Administrator"
    assert me["url"] == "/admin" and me["is_oem"] is True and me["scope_node_id"] == "OEM"
    for prefix in ("dealer.", "service.", "oem.", "marketing.", "admin."):
        assert any(s.startswith(prefix) for s in me["screens"]), prefix
    assert not any(s.startswith("customer.") for s in me["screens"])
    us_dealers = authz.dealer_leaves_under("OEM", None, "us")
    assert set(me["dealer_ids"]) == us_dealers and us_dealers


def test_view_as_catalog_never_offers_the_administrator(admin):
    code, out = call(_event("GET", "/me/personas", _principal(admin)))
    assert code == 200 and out["personas"] and PA not in {p["role"] for p in out["personas"]}


# --- the API: superset of the tenant admin, every staff read --------------------------

_GROUP = re.compile(r"\((?:\?:)?[^)]*\)")


def _samples(fake) -> dict[str, str]:
    def first(table: str, key: str) -> str:
        rows = [r for r in fake.rows(table) if (r.get("market_id") or "us") == "us"
                and r.get("dealer_id") in (None, "D-101")] or fake.rows(table)
        return str(rows[0][key]) if rows else "X"
    sj = first("DEALER_ServiceJourneys", "service_journey_id")
    vin = first("DEALER_Vehicles", "vin")
    return {"journeys": "J-US-J1", "service/journeys": sj, "service": sj,
            "ros": first("DEALER_RepairOrders", "ro_id"), "estimates": first("DEALER_ServiceEstimates", "estimate_id"),
            "explain": first("DEALER_ServiceEstimates", "estimate_id"),
            "accounts": first("OEM_Accounts", "account_id"), "customers": first("DEALER_Customers", "customer_id"),
            "garage": first("DEALER_Customers", "customer_id"), "leads": first("DEALER_Leads", "lead_id"),
            "deals": first("DEALER_Deals", "deal_id"), "history": vin, "predict": vin,
            "oem": "accounts", "cockpit": "sales"}


def _concrete(rx: re.Pattern, samples: dict[str, str]) -> str:
    parts = rx.pattern.strip("^$").split("/")
    out = []
    for i, seg in enumerate(parts):
        if not seg.startswith("("):
            out.append(seg.replace("\\", ""))
            continue
        if "|" in seg:
            out.append(seg.strip("()").split("|")[0])
            continue
        out.append(samples.get("/".join(parts[max(0, i - 2):i])) or samples.get(parts[i - 1]) or "X")
    return "/".join(out)


def _routes(samples: dict[str, str]) -> list[tuple[str, str]]:
    table = [(v, rx) for v, rx, _ in platform_routes.ROUTES] + [(v, rx) for v, rx, _ in domain_routes.all_routes()]
    for name in dir(handler):
        rx = getattr(handler, name)
        if name.endswith("_RE") and isinstance(rx, re.Pattern):
            table += [("GET", rx), ("POST", rx)]
    out: list[tuple[str, str]] = []
    for verb, rx in table:
        path = "/" + _concrete(rx, samples).lstrip("/")
        if rx.match(path) and (verb, path) not in out:
            out.append((verb, path))
    return out


# Staff reads the Administrator is refused on purpose: deal documents are C4
# (spec §8.2) and stay with F&I / delivery / advisor / GM, like for OEM roles and
# the tenant admin. An Administrator reaches them with "View as persona".
_EXCLUDED_READS = frozenset({"/documents"})


def test_administrator_route_sweep(fake_ddb, admin):
    """Every registered route: the Administrator is never refused (403) where the
    tenant admin passes, and every staff GET that any staff role may read is
    open to it (except the documented C4 exclusion)."""
    staff_users: dict[str, str] = {}
    for u in fake_ddb.rows("PLAT_Users"):
        g = (u.get("groups") or [None])[0]
        if g in personas.STAFF_GROUPS and g not in staff_users and u.get("market_id") == "us":
            staff_users[g] = u["user_id"]
    assert set(staff_users) == set(personas.STAFF_GROUPS)
    principals = {r: _principal(uid) for r, uid in staff_users.items()} | {PA: _principal(admin)}
    base = {n: dict(rows) for n, rows in fake_ddb.tables.items()}

    def status(role: str, verb: str, path: str) -> int:
        fake_ddb.tables = {n: dict(rows) for n, rows in base.items()}   # every call sees the same data
        authz._org_nodes_cached.cache_clear()
        body = None if verb == "GET" else {}
        return call(_event(verb, path, principals[role], body=body))[0]

    routes = _routes(_samples(fake_ddb))
    assert len(routes) > 150
    refused_reads = set()
    for verb, path in routes:
        if status(PA, verb, path) != 403:
            continue
        assert status("admin_tenant", verb, path) == 403, f"{verb} {path}: tenant admin passes, Administrator 403"
        if verb == "GET" and any(status(r, verb, path) != 403 for r in personas.STAFF_GROUPS):
            refused_reads.add(path)
    assert refused_reads == _EXCLUDED_READS


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/oem/accounts/X/draft-plan", {}),                     # OEM author action
    ("POST", "/oem/campaigns/budget-shift", {}),
    ("POST", "/marketing/campaigns/budget-shift/submit", {}),
    ("POST", "/approvals/plan/X/submit", {}),
    ("POST", "/marketing/audiences/refresh", {}),
    ("PUT", "/config/nurture", {"templates": {}}),
    ("POST", "/oem/contact-reports", {"dealer_id": "D-101", "summary": "x"}),
    ("POST", "/journeys/J-US-J1/outreach", {"channel": "email"}),   # dealer-floor action
    ("POST", "/leads/{lead}/assign", {"to": "U-US-00002"}),
])
def test_persona_actions_are_not_granted(fake_ddb, admin, method, path, body):
    """Persona-specific writes stay with their owners (use "View as persona")."""
    lead = next(r["lead_id"] for r in fake_ddb.rows("DEALER_Leads") if r.get("dealer_id") == "D-101")
    assert call(_event(method, path.format(lead=lead), _principal(admin), body=body))[0] == 403


def test_plan_approval_decide_but_never_submit(admin):
    pr = _principal(admin)
    assert call(_event("POST", "/approvals/plan/PLAN-X/submit", pr, body={}))[0] == 403
    assert call(_event("POST", "/approvals/plan/PLAN-X/decide", pr, body={"decision": "approve"}))[0] != 403
    assert call(_event("GET", "/approvals/plan/PLAN-X", pr))[0] != 403


def test_generic_approvals_like_a_tenant_admin(fake_ddb, admin):
    row = approvals.request("budget_shift", {"amount": 1}, requested_by="U-US-00049", scope={"market_id": "us"})
    assert row["approver_roles"] == ["admin_tenant", "oem_finance"]        # listed approvers unchanged
    pa = {"user_id": admin, "role": PA, "is_oem": True}
    inbox = approvals.inbox(pa)
    mine = next(r for r in inbox["approvals"] if r["approval_id"] == row["approval_id"])
    assert mine["can_decide"] is True
    assert approvals.decide(row["approval_id"], "approve", pa)["status"] == "approved"
    own = approvals.request("budget_shift", {"amount": 2}, requested_by=admin, scope={"market_id": "us"})
    with pytest.raises(approvals.Forbidden):
        approvals.decide(own["approval_id"], "approve", pa)


# --- View as ------------------------------------------------------------------------------

def test_administrator_may_view_as_a_persona(admin):
    ev = _event("GET", "/me/home", _principal(admin), headers={"X-Act-As": "dealer_advisor"})
    code, me = call(ev)
    assert code == 200 and me["role"] == "dealer_advisor" and me["impersonated_by"] == admin


@pytest.mark.parametrize("target", [PA, ADMIN_ID])
def test_nobody_views_as_the_administrator(admin, target):
    dealer_admin = _principal("U-US-00052")
    code, me = call(_event("GET", "/me/home", dealer_admin, headers={"X-Act-As": target}))
    assert code == 200 and me["role"] == "admin_dealer" and not me["impersonating"]


# --- Admin › Users --------------------------------------------------------------------------

def test_directory_cannot_grant_or_edit_the_administrator(admin):
    tenant = _principal("U-US-00051")
    code, out = call(_event("PATCH", "/admin/users/U-US-00002", tenant, body={"groups": [PA]}))
    assert code == 400 and "unknown group" in out["error"]
    code, out = call(_event("PATCH", f"/admin/users/{admin}", tenant, body={"active": False}))
    assert code == 403 and sso_allowlist.ENV_VAR in out["error"]
    code, users = call(_event("GET", "/admin/users", tenant))
    assert code == 200 and PA not in users["groups"]


def test_administrator_administers_the_whole_tenant(fake_ddb, admin):
    pr = _principal(admin)
    code, users = call(_event("GET", "/admin/users", pr))
    assert code == 200 and users["count"] == len(fake_ddb.rows("PLAT_Users"))
    code, ov = call(_event("GET", "/admin/overview", pr))
    assert code == 200 and ov["scope"] == "tenant" and "connectors" in ov
    code, out = call(_event("PATCH", "/admin/users/U-US-00002", pr, body={"groups": ["dealer_bdc"]}))
    assert code == 200 and out["groups"] == ["dealer_bdc"]


# --- Gateway boundary -------------------------------------------------------------------------

def test_gateway_boundary_decision():
    pa = {"role": PA, "is_oem": True, "scope_node_id": "OEM"}
    assert crosspersona.persona_of(pa) == "PLAT"
    assert crosspersona.may_read_directly(pa, "OEM_Accounts")
    assert crosspersona.may_read_directly({**pa, "is_oem": False}, "OEM_Accounts")
    dealer = {"role": "dealer_advisor", "is_oem": False, "dealer_ids": {"D-101"}}
    assert crosspersona.persona_of(dealer) == "DEALER"
    with pytest.raises(crosspersona.CrossPersonaViolation):
        crosspersona.assert_direct_read(dealer, "OEM_Accounts")
    assert crosspersona.persona_of({"role": "admin_tenant", "is_oem": True}) == "OEM"   # unchanged


# --- across the AgentCore runtime boundary ---------------------------------------------------

def test_claims_pass_the_runtime_dispatcher(admin):
    claims = handler._claims(_event("GET", "/x", _principal(admin)))
    wire = registry.serialize_claims(claims)
    norm = registry.normalize_claims(wire)
    assert norm["role"] == PA and norm["groups"] == [PA] and norm["is_oem"] is True
    assert norm["dealer_ids"] == authz.dealer_leaves_under("OEM", None, "us")
    out = runtime._invoke({"kind": "tool", "agent": "A1", "tool": "rank_vehicles",
                           "args": {"journey_id": "J-US-J2"}, "claims": wire})
    assert out["statusCode"] == 200, out
    out = runtime._invoke({"kind": "tool", "agent": "A5", "tool": "recommend_budget_shift",
                           "args": {}, "claims": wire})
    assert out["statusCode"] == 403          # OEM author action, same rule as the route
