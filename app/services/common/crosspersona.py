"""L4 — Cross-persona data boundary (spec retail-ops-quick-project.md §6).

The solution is one data layer, but the *personas* that read it are not equals:
each owner-prefixed table belongs to a persona (OEM / DEALER / CUST / PLAT), and
a caller from one persona must not read another persona's tables **directly**.
Cross-persona reads go through the **AgentCore Gateway** (MCP tools backed by a
scoped Lambda), which re-checks the caller's scope and returns only what they may
see, with citations. This module is the code-side half of that boundary:

  owner_of(table)                 -> "OEM" | "DEALER" | "CUST" | "PLAT"
  persona_of(scope)               -> "OEM" | "DEALER" | "CUST" | "PLAT" | None
  may_read_directly(scope, table) -> bool  (same-persona, or PLAT shared, or OEM read-across)
  assert_direct_read(scope, table)-> None | raises CrossPersonaViolation

Rule (§6, §9):
  * A caller may read a table their own persona owns.
  * Any caller may read PLAT_* (shared platform: users, org, config, knowledge).
  * OEM roles keep their read-across (they legitimately see dealer rows, §9).
  * A DEALER/CUST caller may NOT read OEM_* directly — that must route through
    the Gateway (which applies OEM's own guardrails before returning data).

The service handler already gates *routes* by role; this adds a gate at the
*data-access* layer so the boundary holds no matter which route reaches it.
"""
from __future__ import annotations

from typing import Any

# Owner persona for each owner-prefixed table namespace.
_OWNER_PREFIXES = ("OEM", "DEALER", "CUST", "PLAT")


class CrossPersonaViolation(Exception):
    """A caller tried to read another persona's table directly (bypassing the
    Gateway). Raised by assert_direct_read."""


def owner_of(bare_table: str) -> str | None:
    """"OEM_Accounts" -> "OEM". None if the table carries no known owner prefix.

    The env prefix (retail-ops-dev-) is not part of the bare name callers pass to the
    ddb layer, so we split on the first underscore of the owner prefix."""
    for pref in _OWNER_PREFIXES:
        if bare_table == pref or bare_table.startswith(pref + "_"):
            return pref
    return None


def persona_of(scope: dict[str, Any]) -> str | None:
    """Map a resolved caller scope to its owning persona.

    OEM roles / OEM-scoped callers -> OEM. Everyone else with a dealer scope is
    DEALER; a customer principal is CUST. Admins are treated as PLAT (platform
    operators) and may read across for configuration.

    The Administrator (`platform_admin`, SSO allowlist only, decided 2026-10-21)
    is PLAT whatever its scope: it is a platform operator, not a dealer persona,
    so it reads across directly and is not forced through the Gateway. Dealer
    personas are still forced through it, including an Administrator who is
    "viewing as" a dealer persona (the request then carries that persona's role)."""
    role = (scope.get("role") or "")
    if role == "platform_admin":
        return "PLAT"
    if scope.get("is_oem"):
        return "OEM"
    if role.startswith("oem_"):
        return "OEM"
    if role.startswith("admin_"):
        return "PLAT"
    if scope.get("customer_id") and not role:
        return "CUST"
    if role.startswith("dealer_"):
        return "DEALER"
    # a bare customer principal (garage self-service) with no staff role
    if scope.get("customer_id"):
        return "CUST"
    return None


def may_read_directly(scope: dict[str, Any], bare_table: str) -> bool:
    """True if this caller may read this table WITHOUT going through the Gateway."""
    owner = owner_of(bare_table)
    if owner is None or owner == "PLAT":
        return True  # unprefixed or shared platform tables: always readable
    caller = persona_of(scope)
    if caller == owner:
        return True  # same persona owns the table
    if caller == "OEM":
        return True  # OEM keeps its documented read-across (§9)
    if caller == "PLAT":
        return True  # platform admins read across for configuration
    return False


def assert_direct_read(scope: dict[str, Any], bare_table: str) -> None:
    """Raise CrossPersonaViolation if the caller may not read this table directly.

    The message names the Gateway as the sanctioned path so the caller (or a
    developer) knows the fix is to route the read through the Gateway MCP tool,
    not to widen a role."""
    if may_read_directly(scope, bare_table):
        return
    owner = owner_of(bare_table)
    caller = persona_of(scope) or "unknown"
    raise CrossPersonaViolation(
        f"{caller} persona may not read {owner}-owned table {bare_table!r} directly; "
        f"cross-persona reads must go through the AgentCore Gateway "
        f"(tool namespace '{(owner or '').lower()}-read')."
    )
