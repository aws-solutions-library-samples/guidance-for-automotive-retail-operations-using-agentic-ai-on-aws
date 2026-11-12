"""Generic approval workflow (spec §4.8, G-23): one inbox over every approval type.

`DEALER_Approvals { approval_id, type, requested_by, approver_role, scope,
payload, status, decided_by, decided_at, reason }` for: deal exceptions,
C&I / incentive asks, budget shifts, parts orders over cap, goodwill, and agent
`act_with_approval` actions. Account-plan publishing keeps its Step Functions
path (`plan_approval.py`); the inbox lists those plans too and delegates the
decision there, so the two never disagree.

Rules:
  * the requester can never decide their own request;
  * the decider must hold the type's approver role (or be a tenant admin) and,
    for dealer-scoped types, have the request's dealer in scope;
  * approving records the decision only — budget shifts and allocations are
    never applied automatically (§3.5/§3.6 hard rule);
  * every request/decision emits approval.requested / approval.decided.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import re
from typing import Any

from ..common import ddb
from . import plan_approval

_TABLE = "DEALER_Approvals"
DECISIONS = ("approve", "reject")

# type -> (approver roles, dealer-scoped?, label)
TYPES: dict[str, tuple[frozenset[str], bool, str]] = {
    "action_plan_publish": (frozenset({"oem_regional"}), False, "Action plan publish"),
    "district_action_plan": (frozenset({"oem_regional"}), False, "District action plan"),
    "ci_ask": (frozenset({"oem_finance", "oem_ci_planning"}), False, "C&I / incentive ask"),
    "budget_shift": (frozenset({"oem_finance"}), False, "Budget shift"),
    "deal_exception": (frozenset({"dealer_manager"}), True, "Deal exception"),
    "goodwill": (frozenset({"dealer_service_manager", "dealer_manager"}), True, "Service goodwill"),
    "stage_parts": (frozenset({"dealer_service_manager", "dealer_manager"}), True, "Parts order over cap"),
    "agent_action": (frozenset({"dealer_manager", "dealer_service_manager"}), True, "Agent action"),
}
_ALL_APPROVER = frozenset({"admin_tenant"})
# The Administrator (platform_admin, SSO allowlist) sees and decides every type
# like a tenant admin. It is checked directly rather than added to the listed
# `approver_roles`, so each request's listed approvers stay as they were.
_ADMIN_APPROVER = frozenset({"platform_admin"})


class NotFound(Exception):
    pass


class Forbidden(Exception):
    pass


class InvalidState(Exception):
    pass


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


_MARKET_IN_ID = re.compile(r"-(US|DE)-")


def _row_market(row: dict[str, Any], dealer_markets: dict[str, str] | None = None) -> str | None:
    """Which market a request belongs to: its own market_id, its scope's, its dealer's, or the
    market code in its id (APR-DE-00001, REC-BUDGETSHIFT-CMP-DE-…). None when nothing says."""
    scope = row.get("scope") or {}
    m = row.get("market_id") or scope.get("market_id") or (row.get("payload") or {}).get("market_id")
    if m:
        return str(m).lower()
    dealer = scope.get("dealer_id") or row.get("dealer_id")
    if dealer and dealer_markets and dealer in dealer_markets:
        return dealer_markets[dealer]
    hit = _MARKET_IN_ID.search(str(row.get("approval_id") or ""))
    return hit.group(1).lower() if hit else None


def _dealer_markets(env_prefix: str | None) -> dict[str, str]:
    return {a["dealer_id"]: str(a.get("market_id") or "").lower()
            for a in ddb.scan_all("OEM_Accounts", env_prefix) if a.get("dealer_id") and a.get("market_id")}


def _same_market(row: dict[str, Any], claims: dict[str, Any]) -> bool:
    """A request from another market is not yours to see or decide (US finance never gets a
    German budget shift). The platform Administrator spans markets."""
    if claims.get("role") in _ADMIN_APPROVER:
        return True
    mine = str(claims.get("market_id") or "").lower()
    theirs = row.get("market_id")
    return not mine or not theirs or theirs == mine


def _type_of(row: dict[str, Any]) -> str:
    return row.get("type") or row.get("action") or "agent_action"


def _norm(row: dict[str, Any]) -> dict[str, Any]:
    t = _type_of(row)
    roles, scoped, label = TYPES.get(t, TYPES["agent_action"])
    return {**row, "type": t, "type_label": label, "approver_roles": sorted(roles | _ALL_APPROVER),
            "dealer_scoped": scoped, "source": "approvals"}


def _plan_rows(env_prefix: str | None) -> list[dict[str, Any]]:
    out = []
    for p in ddb.scan_all("OEM_AccountPlans", env_prefix):
        if not p.get("approval_status"):
            continue
        roles, _, label = TYPES["action_plan_publish"]
        st = {"pending": "pending", "approved": "approved", "rejected": "rejected"}.get(p["approval_status"], p["approval_status"])
        out.append({"approval_id": p["plan_id"], "type": "action_plan_publish", "type_label": label,
                    "status": st, "requested_by": p.get("submitted_by"), "requested_at": p.get("submitted_at"),
                    "decided_by": p.get("decided_by"), "decided_at": p.get("decided_at"),
                    "scope": {"district_id": p.get("district_id"), "dealer_id": p.get("dealer_id")},
                    "market_id": str(p.get("market_id") or "").lower() or None,
                    "payload": {"plan_id": p["plan_id"], "publish_level": p.get("publish_level"),
                                "objectives": len(p.get("objectives") or [])},
                    "approver_roles": sorted(roles | _ALL_APPROVER), "dealer_scoped": False,
                    "source": "plan_approval"})
    return out


def can_decide(row: dict[str, Any], claims: dict[str, Any]) -> bool:
    role = claims.get("role")
    if row.get("requested_by") == claims.get("user_id"):
        return False
    if not _same_market(row, claims):
        return False
    if role not in set(row.get("approver_roles") or []) | _ADMIN_APPROVER:
        return False
    if row.get("dealer_scoped") and not claims.get("is_oem"):
        dealer = (row.get("scope") or {}).get("dealer_id") or row.get("dealer_id")
        return bool(dealer) and dealer in claims.get("dealer_ids", set())
    return True


def _visible(row: dict[str, Any], claims: dict[str, Any]) -> bool:
    if not _same_market(row, claims):
        return False
    if claims.get("role") in _ALL_APPROVER | _ADMIN_APPROVER or row.get("requested_by") == claims.get("user_id"):
        return True
    if claims.get("role") not in set(row.get("approver_roles") or []):
        return False
    if row.get("dealer_scoped") and not claims.get("is_oem"):
        dealer = (row.get("scope") or {}).get("dealer_id") or row.get("dealer_id")
        return dealer in claims.get("dealer_ids", set()) if dealer else True
    return True


def inbox(claims: dict[str, Any], status: str | None = None, env_prefix: str | None = None) -> dict[str, Any]:
    rows = [_norm(r) for r in ddb.scan_all(_TABLE, env_prefix)] + _plan_rows(env_prefix)
    dm = _dealer_markets(env_prefix)
    for r in rows:
        r["market_id"] = _row_market(r, dm)
    rows = [r for r in rows if _visible(r, claims)]
    # people are shown by name, never by user id (U-US-00007)
    users = {u["user_id"]: u for u in ddb.scan_all("PLAT_Users", env_prefix) if u.get("user_id")}

    def _name(uid: Any) -> str | None:
        u = users.get(uid) if uid else None
        return " ".join(filter(None, [u.get("given_name"), u.get("family_name")])) or None if u else None
    for r in rows:
        r["requested_by_name"] = _name(r.get("requested_by"))
        r["decided_by_name"] = _name(r.get("decided_by"))
    if status:
        rows = [r for r in rows if r.get("status") == status]
    rows.sort(key=lambda r: (r.get("status") != "pending", r.get("requested_at") or ""), reverse=False)
    for r in rows:
        r["can_decide"] = r.get("status") == "pending" and can_decide(r, claims)
    return {"count": len(rows), "pending": sum(1 for r in rows if r.get("status") == "pending"),
            "mine_to_decide": sum(1 for r in rows if r["can_decide"]),
            "types": {k: v[2] for k, v in TYPES.items()}, "approvals": rows}


def request(rtype: str, payload: dict[str, Any], requested_by: str, scope: dict[str, Any] | None = None,
            reason: str | None = None, env_prefix: str | None = None) -> dict[str, Any]:
    if rtype not in TYPES:
        raise ValueError(f"type must be one of {sorted(TYPES)}")
    roles, scoped, label = TYPES[rtype]
    if scoped and not (scope or {}).get("dealer_id"):
        raise ValueError(f"{label} approvals need scope.dealer_id")
    ts = _now()
    aid = "APR-" + hashlib.sha1(f"{rtype}|{requested_by}|{ts}|{payload}".encode()).hexdigest()[:10].upper()
    row = {"approval_id": aid, "type": rtype, "action": rtype, "status": "pending", "requested_by": requested_by,
           "requested_at": ts, "approver_role": sorted(roles)[0], "scope": scope or {},
           "dealer_id": (scope or {}).get("dealer_id"), "payload": payload or {}, "reason": reason,
           "market_id": str((scope or {}).get("market_id") or "").lower() or None,
           "source_ref": f"approvals.request:{requested_by}"}
    ddb.put_item(_TABLE, row, env_prefix)
    from ..events import bus
    bus.emit("approval.requested", {"approval_id": aid, "approval_type": rtype, "requested_by": requested_by,
                                    "dealer_id": row["dealer_id"]}, actor=requested_by,
             resource_url=f"/r/approval/{aid}", env_prefix=env_prefix)
    return _norm(row)


def decide(approval_id: str, decision: str, claims: dict[str, Any], reason: str | None = None,
           env_prefix: str | None = None) -> dict[str, Any]:
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}")
    actor = claims["user_id"]
    row = ddb.get_item(_TABLE, {"approval_id": approval_id}, env_prefix)
    if row is None:
        plan = next((p for p in _plan_rows(env_prefix) if p["approval_id"] == approval_id), None)
        if plan is None:
            raise NotFound(f"approval {approval_id} not found")
        if plan["status"] != "pending":
            raise InvalidState(f"approval {approval_id} is already {plan['status']}")
        if not can_decide(plan, claims):
            raise Forbidden("you may not decide this approval")
        out = plan_approval.decide_plan(approval_id, decision, actor, env_prefix=env_prefix)
        return {**plan, "status": out["approval_status"], "decided_by": actor, "plan": out}
    n = _norm(row)
    n["market_id"] = _row_market(n, _dealer_markets(env_prefix))
    if n.get("status") != "pending":
        raise InvalidState(f"approval {approval_id} is already {n.get('status')}")
    if not can_decide(n, claims):
        raise Forbidden("you may not decide this approval (requester, role, or scope)")
    if decision == "reject" and not (reason or "").strip():
        raise ValueError("a rejection needs a reason")
    row = dict(row)
    row.update({"status": "approved" if decision == "approve" else "rejected", "decided_by": actor,
                "decided_at": _now(), "decision_reason": reason,
                "applied": False,   # never auto-applied (spend / allocation hard rule)
                })
    ddb.put_item(_TABLE, row, env_prefix)
    from ..events import bus
    bus.emit("approval.decided", {"approval_id": approval_id, "approval_type": n["type"], "decision": row["status"],
                                  "decided_by": actor, "dealer_id": row.get("dealer_id")}, actor=actor,
             resource_url=f"/r/approval/{approval_id}", env_prefix=env_prefix)
    return _norm(row)
