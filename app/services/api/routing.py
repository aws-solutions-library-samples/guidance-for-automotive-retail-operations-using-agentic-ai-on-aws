"""Shared helpers for table-driven API route modules (spec §6).

A route module declares

    ROUTES = [("GET", re.compile(r"^/fni/queue$"), handler_fn), ...]
    ERRORS = [(SomeDomainError, 409), ...]          # optional

where `handler_fn(event, claims, match) -> (status, body)`. Raise `Bad(code,
msg)` for request/authorization failures. `dispatch()` authenticates, runs the
first matching route, and maps exceptions to HTTP statuses.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Iterable

from ..common import ddb, personas

Respond = Callable[[int, dict[str, Any]], dict[str, Any]]
Handler = Callable[[dict[str, Any], dict[str, Any], "re.Match[str]"], tuple[int, Any]]
Route = tuple[str, "re.Pattern[str]", Handler]

STAFF_ROLES = personas.STAFF_ROLES
MANAGER_ROLES = frozenset({"dealer_manager", "dealer_service_manager", "oem_regional",
                           "admin_tenant", "admin_dealer", personas.PLATFORM_ADMIN})


class Bad(Exception):
    def __init__(self, code: int, msg: str, **extra: Any):
        self.code, self.msg, self.extra = code, msg, extra
        super().__init__(msg)


def body(event: dict[str, Any]) -> dict[str, Any]:
    try:
        b = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError as e:
        raise Bad(400, "invalid JSON body") from e
    if not isinstance(b, dict):
        raise Bad(400, "JSON body must be an object")
    return b


def qs(event: dict[str, Any]) -> dict[str, str]:
    return event.get("queryStringParameters") or {}


def require(b: dict[str, Any], *keys: str) -> None:
    missing = [k for k in keys if b.get(k) in (None, "")]
    if missing:
        raise Bad(400, f"missing required field(s): {', '.join(missing)}")


def staff(claims: dict[str, Any]) -> None:
    if claims.get("role") not in STAFF_ROLES:
        raise Bad(403, f"role {claims.get('role')!r} may not use this staff route")


def roles(claims: dict[str, Any], allowed: Iterable[str], allow_oem: bool = False) -> None:
    """Allow when ANY of the caller's groups is in `allowed` (a user may hold
    several roles — admin-assigned); `role` stays the primary group and the
    fallback when the groups claim is absent (old tokens, customer principals)."""
    allowed = frozenset(allowed)
    held = set(claims.get("groups") or []) | {claims.get("role")}
    if held & allowed or (allow_oem and claims.get("is_oem")):
        return
    raise Bad(403, f"role {claims.get('role')!r} may not perform this action")


def customer(claims: dict[str, Any]) -> str:
    if claims.get("role") != "customer" or not claims.get("customer_id"):
        raise Bad(403, "this is a customer self-service route")
    return claims["customer_id"]


def dealer_in_scope(claims: dict[str, Any], dealer_id: str | None) -> None:
    if claims.get("is_oem"):
        return
    if dealer_id not in claims.get("dealer_ids", set()):
        raise Bad(403, "resource outside caller scope")


def journey_in_scope(claims: dict[str, Any], journey_id: str) -> dict[str, Any]:
    j = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id})
    if j is None:
        raise Bad(404, f"journey {journey_id} not found")
    if claims.get("role") == "customer":
        if j.get("customer_id") != claims.get("customer_id"):
            raise Bad(403, "journey belongs to another customer")
        return j
    dealer_in_scope(claims, j.get("dealer_id"))
    return j


def service_journey_in_scope(claims: dict[str, Any], sj_id: str) -> dict[str, Any]:
    sj = ddb.get_item("DEALER_ServiceJourneys", {"service_journey_id": sj_id})
    if sj is None:
        raise Bad(404, f"service journey {sj_id} not found")
    if claims.get("role") == "customer":
        if sj.get("customer_id") != claims.get("customer_id"):
            raise Bad(403, "service journey belongs to another customer")
        return sj
    dealer_in_scope(claims, sj.get("dealer_id"))
    return sj


def scoped_rows(claims: dict[str, Any], rows: list[dict[str, Any]], key: str = "dealer_id") -> list[dict[str, Any]]:
    """Rows inside the caller's dealer scope (OEM roles see all)."""
    if claims.get("is_oem"):
        return rows
    ids = claims.get("dealer_ids", set())
    return [r for r in rows if r.get(key) in ids]


def dispatch(routes: Iterable[Route], errors: Iterable[tuple[type, int]], event: dict[str, Any],
             path: str, method: str, claims: dict[str, Any], respond: Respond) -> dict[str, Any] | None:
    for verb, rx, fn in routes:
        m = rx.match(path)
        if not m or verb != method:
            continue
        if not claims.get("user_id"):
            return respond(401, {"error": "unauthenticated"})
        try:
            code, out = fn(event, claims, m)
            return respond(code, out)
        except Bad as e:
            return respond(e.code, {"error": e.msg, **e.extra})
        except Exception as e:
            for etype, code in errors:
                if isinstance(e, etype):
                    return respond(code, {"error": str(e)})
            return respond(500, {"error": f"{type(e).__name__}: {e}"})
    return None
