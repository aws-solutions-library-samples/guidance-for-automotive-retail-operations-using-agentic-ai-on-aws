"""Admin Console — users & roles, org hierarchy, market & brand (spec §5.17, §8.1, §2.5).

Scope rules (enforced here, not in the UI):
  * admin_tenant administers everything; so does the Administrator
    (platform_admin), whose own rows only the SSO allowlist step may change.
    platform_admin is not one of the 19 assignable groups, so no one can be
    given it here.
  * admin_dealer is store-scoped: only users whose dealer is in its scope, may
    not grant OEM / tenant-admin groups, may not move users outside its stores,
    and has read-only access to the org tree below its stores; no market edits.

  list_users / update_user        PLAT_Users (19 groups of §5.2). Cognito group
                                  sync is a deploy step (app/infra/cognito_setup.py).
  org_tree / create_node / update_node   PLAT_OrgHierarchy (OEM ▸ region ▸ district
                                  ▸ dealer group ▸ dealer, PMA on dealers)
  get_market / update_market / update_state_rule   OEM_Markets + OEM_StateRules
"""
from __future__ import annotations

import re
from typing import Any

from ..common import authz, ddb, personas, sso_allowlist
from . import audit_log

_USERS = "PLAT_Users"
_ORG = "PLAT_OrgHierarchy"
_SECRET_FIELDS = frozenset({"test_password", "password", "federated_identity"})
_TENANT_ONLY_GROUPS = frozenset(g for g in personas.STAFF_GROUPS if g.startswith("oem_")) | {"admin_tenant"}
_PARENT_TYPES = {
    "region": {"oem"}, "district": {"region"}, "dealer_group": {"district"}, "dealer": {"district", "dealer_group"},
}
_NODE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,40}$")


class Forbidden(Exception):
    pass


class NotFound(Exception):
    pass


class Invalid(ValueError):
    pass


def _is_tenant(scope: dict[str, Any]) -> bool:
    return scope.get("role") in authz.TENANT_ADMIN_ROLES


def _public_user(u: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in u.items() if k not in _SECRET_FIELDS}
    out["federated"] = bool(u.get("federated_identity"))
    return out


def _nodes(env_prefix: str | None = None, market_id: str | None = None) -> dict[str, dict[str, Any]]:
    """node_id -> node, for ONE market (node ids repeat across markets)."""
    return {n["node_id"]: n for n in authz.org_nodes(env_prefix, market_id)}


def _dealer_of(node_id: str | None, nodes: dict[str, dict[str, Any]]) -> tuple[str | None, str | None]:
    """(dealer_id, district_id) implied by a scope node."""
    n = nodes.get(node_id or "")
    if not n:
        return None, None
    dealer = node_id if n.get("type") == "dealer" else None
    cur, district = n, None
    while cur:
        if cur.get("type") == "district":
            district = cur["node_id"]
            break
        cur = nodes.get(cur.get("parent_id") or "")
    return dealer, district


# --- users ------------------------------------------------------------------------

def list_users(scope: dict[str, Any], env_prefix: str | None = None) -> dict[str, Any]:
    rows = ddb.scan_all(_USERS, env_prefix)
    if not _is_tenant(scope):
        ids = scope.get("dealer_ids", set())
        rows = [u for u in rows if u.get("dealer_id") in ids]
    rows.sort(key=lambda u: u.get("user_id") or "")
    by_group: dict[str, int] = {}
    for u in rows:
        for g in u.get("groups") or []:
            by_group[g] = by_group.get(g, 0) + 1
    return {"count": len(rows), "by_group": by_group, "groups": list(personas.STAFF_GROUPS),
            "users": [_public_user(u) for u in rows]}


def update_user(user_id: str, changes: dict[str, Any], scope: dict[str, Any],
                env_prefix: str | None = None) -> dict[str, Any]:
    u = ddb.get_item(_USERS, {"user_id": user_id}, env_prefix)
    if u is None:
        raise NotFound(f"user {user_id} not found")
    if u.get("provisioned_by") == sso_allowlist.MARKER:
        # The Administrator rows belong to the SSO allowlist deploy step; an
        # edit here would be overwritten on its next run.
        raise Forbidden(f"{user_id} is managed by the SSO allowlist ({sso_allowlist.ENV_VAR})")
    tenant = _is_tenant(scope)
    if not tenant and u.get("dealer_id") not in scope.get("dealer_ids", set()):
        raise Forbidden("user outside your stores")
    if user_id == scope.get("user_id") and changes.get("active") is False:
        raise Invalid("you cannot deactivate your own account")
    u = dict(u)
    before = {k: u.get(k) for k in ("groups", "scope_node_id", "dealer_id", "district_id", "active")}
    if "groups" in changes:
        groups = changes["groups"]
        if not isinstance(groups, list) or not groups:
            raise Invalid("groups must be a non-empty list")
        bad = [g for g in groups if g not in personas.STAFF_GROUPS]
        if bad:
            raise Invalid(f"unknown group(s) {bad}")
        if not tenant and set(groups) & _TENANT_ONLY_GROUPS:
            raise Forbidden("dealer admins cannot grant OEM or tenant-admin groups")
        if user_id == scope.get("user_id") and not set(groups) & {"admin_tenant", "admin_dealer"}:
            raise Invalid("you cannot remove your own admin group")
        u["groups"] = list(dict.fromkeys(groups))
    if "scope_node_id" in changes:
        nodes = _nodes(env_prefix, scope.get("market_id"))
        node = changes["scope_node_id"]
        if node not in nodes:
            raise Invalid(f"unknown org node {node!r}")
        dealer, district = _dealer_of(node, nodes)
        if not tenant:
            leaves = authz.dealer_leaves_under(node, env_prefix, scope.get("market_id"))
            if not leaves or not leaves <= scope.get("dealer_ids", set()):
                raise Forbidden("dealer admins may only scope users to their own stores")
        u["scope_node_id"] = node
        u["dealer_id"] = dealer
        u["district_id"] = district
    if "active" in changes:
        u["active"] = bool(changes["active"])
    for k in ("given_name", "family_name", "email"):
        if k in changes and isinstance(changes[k], str):
            u[k] = changes[k].strip()
    ddb.put_item(_USERS, u, env_prefix)
    after = {k: u.get(k) for k in before}
    audit_log.record(scope["user_id"], "user.update", user_id,
                     {"fields": sorted(k for k in before if before[k] != after[k])}, env_prefix=env_prefix)
    return {**_public_user(u), "cognito_sync": "pending (run app/infra/cognito_setup.py to sync groups)"}


# --- org hierarchy -----------------------------------------------------------------

def org_tree(scope: dict[str, Any], env_prefix: str | None = None) -> dict[str, Any]:
    nodes = _nodes(env_prefix, scope.get("market_id"))
    users = ddb.scan_all(_USERS, env_prefix)
    visible = set(nodes)
    if not _is_tenant(scope):
        ids = scope.get("dealer_ids", set())
        visible = set()
        for d in ids:
            cur = nodes.get(d)
            while cur:                      # dealers + their ancestors (read-only context)
                visible.add(cur["node_id"])
                cur = nodes.get(cur.get("parent_id") or "")

    def build(nid: str) -> dict[str, Any]:
        n = nodes[nid]
        kids = sorted((k for k, v in nodes.items() if v.get("parent_id") == nid and k in visible),
                      key=lambda k: (nodes[k].get("type") != "dealer_group", nodes[k].get("name") or k))
        return {"node_id": nid, "name": n.get("name"), "type": n.get("type"), "pma": n.get("pma"),
                "region_code": n.get("region_code"), "tier": n.get("tier"),
                "users": sum(1 for u in users if u.get("scope_node_id") == nid),
                "children": [build(k) for k in kids]}

    roots = [k for k, v in nodes.items() if not v.get("parent_id") and k in visible]
    return {"editable": _is_tenant(scope), "roots": [build(r) for r in roots], "count": len(visible)}


def _check_parent(node_type: str, parent_id: str | None, nodes: dict[str, dict[str, Any]]) -> None:
    if node_type == "oem":
        raise Invalid("only one OEM root exists")
    if node_type not in _PARENT_TYPES:
        raise Invalid(f"type must be one of {sorted(_PARENT_TYPES)}")
    p = nodes.get(parent_id or "")
    if not p or p.get("type") not in _PARENT_TYPES[node_type]:
        raise Invalid(f"a {node_type} must sit under {sorted(_PARENT_TYPES[node_type])}")


def create_node(body: dict[str, Any], scope: dict[str, Any], env_prefix: str | None = None) -> dict[str, Any]:
    if not _is_tenant(scope):
        raise Forbidden("only tenant admins edit the org hierarchy")
    nodes = _nodes(env_prefix, scope.get("market_id"))
    nid, ntype, parent = body.get("node_id"), body.get("type"), body.get("parent_id")
    if not nid or not _NODE_ID.match(nid):
        raise Invalid("node_id must be 2–41 letters, digits, '-' or '_'")
    if nid in nodes:
        raise Invalid(f"node {nid} already exists")
    _check_parent(ntype, parent, nodes)
    row = {"node_id": nid, "market_id": scope.get("market_id"), "type": ntype,
           "parent_id": parent, "name": (body.get("name") or nid).strip()}
    for k in ("pma", "region_code", "tier", "city", "timezone"):
        if body.get(k):
            row[k] = body[k]
    if ntype == "dealer":
        _, district = _dealer_of(parent, nodes)
        row["district_id"] = district
        row["dealer_group_id"] = parent if nodes[parent].get("type") == "dealer_group" else None
    ddb.put_item(_ORG, row, env_prefix)
    authz._org_nodes_cached.cache_clear()
    audit_log.record(scope["user_id"], "org.create", nid, {"type": ntype, "parent_id": parent}, env_prefix=env_prefix)
    return row


def update_node(node_id: str, body: dict[str, Any], scope: dict[str, Any], env_prefix: str | None = None) -> dict[str, Any]:
    if not _is_tenant(scope):
        raise Forbidden("only tenant admins edit the org hierarchy")
    nodes = _nodes(env_prefix, scope.get("market_id"))
    n = nodes.get(node_id)
    if n is None:
        raise NotFound(f"node {node_id} not found")
    n = dict(n)
    if "parent_id" in body and body["parent_id"] != n.get("parent_id"):
        new_parent = body["parent_id"]
        _check_parent(n.get("type"), new_parent, nodes)
        cur = nodes.get(new_parent)
        while cur:                                   # no cycles
            if cur["node_id"] == node_id:
                raise Invalid("cannot move a node under its own descendant")
            cur = nodes.get(cur.get("parent_id") or "")
        n["parent_id"] = new_parent
        if n.get("type") == "dealer":
            nodes[node_id] = n
            _, n["district_id"] = _dealer_of(new_parent, nodes)
            n["dealer_group_id"] = new_parent if nodes[new_parent].get("type") == "dealer_group" else None
    for k in ("name", "pma", "region_code", "tier", "city", "timezone"):
        if k in body:
            n[k] = body[k]
    n.setdefault("market_id", scope.get("market_id"))
    ddb.put_item(_ORG, n, env_prefix)
    authz._org_nodes_cached.cache_clear()
    audit_log.record(scope["user_id"], "org.update", node_id, {"fields": sorted(body)}, env_prefix=env_prefix)
    return n


# --- market & brand ------------------------------------------------------------------

_MARKET_EDITABLE = {"fuel_price_default": float, "energy_price_default": float, "annual_distance_default": float,
                    "regulatory_disclosures": list, "timezone_default": str, "phone_format": str}
_BRAND_EDITABLE = ("name", "short", "tagline", "captive_finance")
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_STATE_EDITABLE = ("sales_tax_pct", "avg_local_tax_pct", "doc_fee_cap", "ev_annual_fee", "fuel_price",
                   "energy_price", "vehicle_tax_note")


def get_market(market_id: str, env_prefix: str | None = None) -> dict[str, Any]:
    m = ddb.get_item("OEM_Markets", {"market_id": market_id}, env_prefix)
    if m is None:
        raise NotFound(f"market {market_id} not found")
    rules = sorted(ddb.scan_filter("OEM_StateRules", "market_id", market_id, env_prefix),
                   key=lambda r: r.get("region_code") or "")
    markets = sorted(r["market_id"] for r in ddb.scan_all("OEM_Markets", env_prefix))
    return {"market": m, "state_rules": rules, "markets": markets,
            "editable_fields": sorted(_MARKET_EDITABLE), "editable_brand": list(_BRAND_EDITABLE),
            "editable_state_fields": list(_STATE_EDITABLE)}


def update_market(market_id: str, body: dict[str, Any], scope: dict[str, Any],
                  env_prefix: str | None = None) -> dict[str, Any]:
    if not _is_tenant(scope):
        raise Forbidden("only tenant admins edit market & brand")
    m = ddb.get_item("OEM_Markets", {"market_id": market_id}, env_prefix)
    if m is None:
        raise NotFound(f"market {market_id} not found")
    m = dict(m)
    changed = []
    for k, typ in _MARKET_EDITABLE.items():
        if k in body:
            v = body[k]
            try:
                v = typ(v) if typ is not list else list(v)
            except (TypeError, ValueError) as e:
                raise Invalid(f"{k} must be {typ.__name__}") from e
            if typ is float and v < 0:
                raise Invalid(f"{k} must be non-negative")
            m[k] = v
            changed.append(k)
    if "brand" in body:
        b = dict(m.get("brand") or {})
        nb = body["brand"] or {}
        for k in _BRAND_EDITABLE:
            if k in nb:
                b[k] = str(nb[k])[:120]
                changed.append(f"brand.{k}")
        if "colors" in nb:
            colors = dict(b.get("colors") or {})
            for ck, cv in (nb["colors"] or {}).items():
                if ck not in ("primary", "accent", "oem_header", "oem_sidebar", "oem_accent"):
                    raise Invalid(f"unknown brand color {ck!r}")
                if not _HEX.match(str(cv)):
                    raise Invalid(f"brand color {ck} must be #RRGGBB")
                colors[ck] = cv
                changed.append(f"brand.colors.{ck}")
            b["colors"] = colors
        m["brand"] = b
    ddb.put_item("OEM_Markets", m, env_prefix)
    audit_log.record(scope["user_id"], "market.update", market_id, {"fields": changed}, env_prefix=env_prefix)
    return {"market": m, "changed": changed}


def update_state_rule(market_id: str, region_code: str, body: dict[str, Any], scope: dict[str, Any],
                      env_prefix: str | None = None) -> dict[str, Any]:
    if not _is_tenant(scope):
        raise Forbidden("only tenant admins edit state rules")
    rows = [r for r in ddb.scan_filter("OEM_StateRules", "region_code", region_code, env_prefix)
            if r.get("market_id") == market_id]
    if not rows:
        raise NotFound(f"no rule for {region_code} in {market_id}")
    r = dict(rows[0])
    changed = []
    for k in _STATE_EDITABLE:
        if k in body:
            v = body[k]
            if k != "vehicle_tax_note" and v is not None:
                try:
                    v = float(v)
                except (TypeError, ValueError) as e:
                    raise Invalid(f"{k} must be a number") from e
                if v < 0 or (k.endswith("_pct") and v > 30):
                    raise Invalid(f"{k} out of range")
            r[k] = v
            changed.append(k)
    r["verify"] = False
    ddb.put_item("OEM_StateRules", r, env_prefix)
    audit_log.record(scope["user_id"], "state_rule.update", f"{market_id}:{region_code}", {"fields": changed},
                     env_prefix=env_prefix)
    return r
