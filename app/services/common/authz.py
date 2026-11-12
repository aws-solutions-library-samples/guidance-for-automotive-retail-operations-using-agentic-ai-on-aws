"""L4 — Authorization: role + scope resolution from PLAT_OrgHierarchy / PLAT_Users.

The access layer's job (architecture §9): given an authenticated user, resolve
their role (group) and the org-scope subtree they may act within, and enforce
that a requested resource falls inside that scope.

Model (from the loaded data):
  PLAT_Users        user_id, groups[], scope_node_id, dealer_id, district_id, market_id
  PLAT_OrgHierarchy node_id, parent_id, type (oem|region|district|group|dealer), name

A user's scope is the subtree rooted at their `scope_node_id`. For a dealer-scoped
user that is a single dealer; for a district manager it is every dealer under the
district; for OEM/region it broadens up the tree. Resource access (e.g. a journey)
is allowed iff the resource's `dealer_id` is one of the dealer leaves in that
subtree (or the user is OEM-scoped).

Pure resolution over the same DynamoDB tables the read models use; no secrets.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from . import ddb
from .personas import PLATFORM_ADMIN

# Roles permitted to read the Dealer Advisor assist model (spec §5.2 personas).
ADVISOR_READ_ROLES = frozenset({
    "dealer_advisor", "dealer_bdc", "dealer_fni", "dealer_manager",
    "dealer_delivery", "dealer_inventory",
})

# Roles permitted to read the Service Desk / board (§5.20 personas).
SERVICE_READ_ROLES = frozenset({
    "dealer_service", "dealer_technician", "dealer_parts",
    "dealer_service_manager", "dealer_manager",
})

# OEM Workspace roles (§5.9 personas).
OEM_READ_ROLES = frozenset({
    "oem_dm", "oem_regional", "oem_ci_planning", "oem_sales_ops", "oem_finance",
    "oem_cx", "oem_marketing",
})

# Admin Console roles (§5, autonomy/agent config). The Administrator
# (platform_admin, §5.2: SSO allowlist only) administers like a tenant admin.
ADMIN_ROLES = frozenset({"admin_tenant", "admin_dealer", PLATFORM_ADMIN})

# Tenant-wide administration (every store, the org tree, market & brand, any
# approval): the tenant admin and the Administrator.
TENANT_ADMIN_ROLES = frozenset({"admin_tenant", PLATFORM_ADMIN})

# READ-only OEM Workspace / Marketing Studio routes: the OEM roles plus the
# Administrator, who opens every staff module. OEM author actions (draft and
# submit plans, budget shifts, jobs) stay on OEM_READ_ROLES.
OEM_VIEW_ROLES = OEM_READ_ROLES | {PLATFORM_ADMIN}

# Store Cockpit (§5.14): dealer principal / GM only (plus OEM read-across).
COCKPIT_ROLES = frozenset({"dealer_manager"})

# §8 R2 — OEM account-plan approval: who may submit vs. who may decide.
# An OEM author (district manager, etc.) submits; a regional lead or a tenant
# admin approves/rejects (a submitter should not also be the sole approver).
APPROVAL_SUBMIT_ROLES = OEM_READ_ROLES
APPROVAL_DECIDE_ROLES = frozenset({"oem_regional"}) | ADMIN_ROLES


def district_ids_under(node_id: str, env_prefix: str | None = None) -> set[str]:
    """Every district-type node_id in the subtree rooted at node_id.

    If node_id IS a district, returns {node_id}. If it's a region/OEM root,
    returns all districts beneath it. Used to scope OEM Workspace views."""
    nodes = _org_nodes_cached(env_prefix)
    by_parent: dict[str | None, list[tuple[str, str | None, str]]] = {}
    node_type: dict[str, str] = {}
    for nid, pid, typ in nodes:
        by_parent.setdefault(pid, []).append((nid, pid, typ))
        node_type[nid] = typ
    out: set[str] = set()
    stack = [node_id]
    while stack:
        cur = stack.pop()
        if node_type.get(cur) == "district":
            out.add(cur)
        for child_id, _pid, _typ in by_parent.get(cur, []):
            stack.append(child_id)
    return out


class Unauthenticated(Exception):
    """No/!invalid principal."""


class Forbidden(Exception):
    """Authenticated but out of role or scope."""


def get_user(user_id: str, env_prefix: str | None = None) -> dict[str, Any] | None:
    return ddb.get_item("PLAT_Users", {"user_id": user_id}, env_prefix)


def org_nodes(env_prefix: str | None = None, market_id: str | None = None) -> list[dict[str, Any]]:
    """Org nodes for ONE market. `node_id` (OEM, DG-01, …) is only unique within
    a market — every market has its own OEM root and dealer groups — so a
    market-blind read would splice two companies' trees together. Rows without a
    market_id are treated as belonging to the requested market (pre-stamp data)."""
    nodes = ddb.scan_all("PLAT_OrgHierarchy", env_prefix)
    if not market_id:
        return nodes
    return [n for n in nodes if (n.get("market_id") or market_id) == market_id]


def org_node(node_id: str | None, market_id: str | None,
             env_prefix: str | None = None) -> dict[str, Any] | None:
    """A single org node by (market, node_id) — the table's composite key."""
    if not node_id:
        return None
    if market_id:
        hit = ddb.get_item("PLAT_OrgHierarchy", {"market_id": market_id, "node_id": node_id}, env_prefix)
        if hit:
            return hit
    # market unknown, or pre-stamp row: fall back to a scan match.
    return next((n for n in org_nodes(env_prefix, market_id) if n.get("node_id") == node_id), None)


@lru_cache(maxsize=32)
def _org_nodes_cached(env_prefix: str | None,
                      market_id: str | None = None) -> tuple[tuple[str, str | None, str], ...]:
    """(node_id, parent_id, type) tuples — cached; org tree is tiny and static."""
    return tuple((n["node_id"], n.get("parent_id"), n.get("type"))
                 for n in org_nodes(env_prefix, market_id))


def dealer_leaves_under(node_id: str, env_prefix: str | None = None,
                        market_id: str | None = None) -> set[str]:
    """Every dealer-type node_id in the subtree rooted at `node_id`.

    If node_id is itself a dealer, returns {node_id}. If it is the OEM root,
    returns all dealers IN THAT MARKET. Uses the parent_id edges to walk down."""
    nodes = _org_nodes_cached(env_prefix, market_id)
    by_parent: dict[str | None, list[tuple[str, str | None, str]]] = {}
    node_type: dict[str, str] = {}
    for nid, pid, typ in nodes:
        by_parent.setdefault(pid, []).append((nid, pid, typ))
        node_type[nid] = typ

    leaves: set[str] = set()
    stack = [node_id]
    while stack:
        cur = stack.pop()
        if node_type.get(cur) == "dealer":
            leaves.add(cur)
        for child_id, _pid, _typ in by_parent.get(cur, []):
            stack.append(child_id)
    return leaves


def resolve_scope(user: dict[str, Any], env_prefix: str | None = None) -> dict[str, Any]:
    """Return {role, scope_node_id, market_id, dealer_ids, is_oem} for a user."""
    if not user or not user.get("active", True):
        raise Forbidden("user inactive or unknown")
    groups = user.get("groups") or []
    role = groups[0] if groups else None
    scope_node = user.get("scope_node_id") or user.get("dealer_id")
    is_oem = bool(scope_node and str(scope_node).upper() == "OEM") or \
        any(g.startswith("oem_") for g in groups)
    dealer_ids = dealer_leaves_under(scope_node, env_prefix, user.get("market_id")) if scope_node else set()
    return {
        "user_id": user.get("user_id"),
        "role": role,
        "groups": groups,
        "scope_node_id": scope_node,
        "market_id": user.get("market_id"),
        "dealer_ids": dealer_ids,
        "is_oem": is_oem,
    }


def check_journey_access(scope: dict[str, Any], journey: dict[str, Any]) -> None:
    """Raise Forbidden unless the caller's role and scope permit this journey."""
    if scope.get("role") not in ADVISOR_READ_ROLES and not scope.get("is_oem"):
        raise Forbidden(f"role {scope.get('role')!r} may not read the advisor assist model")
    if scope.get("is_oem"):
        return  # OEM roles read across dealers (read-only per §9)
    dealer_id = journey.get("dealer_id")
    if dealer_id not in scope.get("dealer_ids", set()):
        raise Forbidden(f"journey dealer {dealer_id!r} is outside caller scope "
                        f"{scope.get('scope_node_id')!r}")
