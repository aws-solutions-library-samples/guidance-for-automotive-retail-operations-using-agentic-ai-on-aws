"""Gateway-side OEM read tools (spec retail-ops-quick-project.md §6).

These are the *sanctioned* cross-persona reads a non-OEM caller (e.g. a Dealer
advisor) may make against OEM-owned data — but only through the AgentCore
Gateway, never by touching retail-ops-dev-OEM_* directly. Each tool:

  * receives the caller's scope (claims) from the Gateway invocation,
  * returns only the OEM rows in the caller's dealer/district scope,
  * strips OEM-internal fields a dealer must not see (targets, allocations,
    composite internals, competitor intel), and
  * attaches a source_ref citation.

The Gateway Lambda (app/services/gateway/handler.py) dispatches MCP tool calls
to these functions. Because the Gateway is the owner of this boundary, the reads
here call ddb directly (they ARE the OEM-persona-authorized path); the
cross-persona guard lives on the *dealer's* direct-access path, not here.
"""
from __future__ import annotations

from typing import Any

from ..common import ddb

# OEM-internal fields a cross-persona (dealer) caller must never receive, even
# for a dealer in their own scope. OEM roles calling their own routes see these;
# the Gateway strips them for everyone else.
_OEM_ONLY_FIELDS = frozenset({
    "targets", "actuals", "composite", "incentive_programs_enrolled",
    "competitors_in_pma", "allocation", "wholesale", "ev_readiness",
    "compliance", "internal_notes",
})

_SRC = "gateway:oem-read (cross-persona via AgentCore Gateway; §6)"


def _dealer_scope(claims: dict[str, Any]) -> set[str]:
    """The dealer ids this caller may see. dealer_ids arrives as a csv string
    (from the authorizer context) or a list."""
    d = claims.get("dealer_ids")
    if isinstance(d, str):
        return set(filter(None, d.split(",")))
    if isinstance(d, (list, tuple, set)):
        return set(map(str, d))
    return set()


def _first(row: dict[str, Any], *fields: str) -> Any:
    """The first non-empty value among `fields` — the seeded column first, then
    the older or alternative names a row may carry instead."""
    for f in fields:
        v = row.get(f)
        if v not in (None, ""):
            return v
    return None


def _cite(out: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """Attach the citations: `source_ref` names this Gateway path (unchanged for
    callers); `data_source_ref` keeps the row's own citation, which the Gateway
    used to overwrite."""
    if row.get("source_ref"):
        out["data_source_ref"] = row["source_ref"]
    out["source_ref"] = _SRC
    return out


def _window(start: Any, end: Any) -> str | None:
    """A readable date window from ISO timestamps ("2026-08-08 to 2026-09-30",
    or "from 2026-08-08 (no end date)" while a program is open-ended)."""
    s, e = (str(start)[:10] if start else None), (str(end)[:10] if end else None)
    if s and e:
        return f"{s} to {e}"
    if s:
        return f"from {s} (no end date)"
    return f"until {e}" if e else None


def _public_account(acct: dict[str, Any]) -> dict[str, Any]:
    """Drop OEM-internal fields; keep what a dealer legitimately needs (identity,
    region/district, tier, account type, health indices, days supply)."""
    out = {k: v for k, v in acct.items() if k not in _OEM_ONLY_FIELDS and k != "source_ref"}
    return _cite(out, acct)


def _public_alert(a: dict[str, Any]) -> dict[str, Any]:
    """One alert as the Gateway returns it. OEM_Alerts rows carry `title` and
    `kind`; `message`/`summary`/`category` are kept as fallbacks for rows that
    use those names. `required_action`/`due_at` appear only when a row has them
    (the seeded rows do not)."""
    out = {"alert_id": a.get("alert_id"), "severity": a.get("severity"),
           "message": _first(a, "message", "summary", "title"),
           "dealer_id": a.get("dealer_id"),
           "title": _first(a, "title", "message", "summary"),
           "category": _first(a, "category", "kind"),
           "status": a.get("status"),
           "district_id": a.get("district_id"),
           "opened_at": _first(a, "opened_at", "created_at"),
           "resolved_at": a.get("resolved_at")}
    action = _first(a, "required_action", "action")
    if action is not None:
        out["required_action"] = action
    due = _first(a, "due_at", "due_date")
    if due is not None:
        out["due_at"] = due
    return _cite(out, a)


def _public_program(p: dict[str, Any], dealers: set[str]) -> dict[str, Any]:
    """One C&I program. OEM_CIPrograms rows carry `started_at`, `goal` and
    `status` (no `window`/`period`/`summary`), so the window is built from the
    dates and the summary falls back to the goal. A program aimed at one dealer
    names that dealer only to a caller who may see it (OEM scope, or the
    caller's own dealer); otherwise `dealer_id` is null."""
    start = _first(p, "started_at", "start_date", "starts_at")
    end = _first(p, "ended_at", "end_date", "ends_at")
    dealer = p.get("dealer_id")
    out = {"program_id": p.get("program_id"), "name": p.get("name"),
           "window": _first(p, "window", "period") or _window(start, end),
           "summary": _first(p, "summary", "description", "goal"),
           "started_at": start, "ended_at": end,
           "goal": p.get("goal"), "status": p.get("status"),
           "district_id": p.get("district_id"),
           "dealer_id": dealer if (not dealers or dealer in dealers) else None}
    return _cite(out, p)


def get_account(claims: dict[str, Any], account_id: str,
                env_prefix: str | None = None) -> dict[str, Any]:
    """One OEM account, scoped + field-stripped for a cross-persona caller."""
    acct = ddb.get_item("OEM_Accounts", {"account_id": account_id}, env_prefix)
    if acct is None:
        return {"error": f"account {account_id} not found", "source_ref": _SRC}
    dealers = _dealer_scope(claims)
    # a dealer caller may only see accounts tied to a dealer in their scope
    if dealers and acct.get("dealer_id") and acct["dealer_id"] not in dealers:
        return {"error": "account outside caller scope", "source_ref": _SRC}
    return {"account": _public_account(acct)}


def list_accounts(claims: dict[str, Any], market_id: str | None = None,
                  env_prefix: str | None = None) -> dict[str, Any]:
    """OEM accounts visible to this cross-persona caller (their dealers only)."""
    market = market_id or claims.get("market_id") or "us"
    accounts = ddb.scan_filter("OEM_Accounts", "market_id", market, env_prefix)
    dealers = _dealer_scope(claims)
    if dealers:
        accounts = [a for a in accounts if a.get("dealer_id") in dealers]
    return {"count": len(accounts),
            "accounts": [_public_account(a) for a in accounts],
            "source_ref": _SRC}


def get_alerts(claims: dict[str, Any], market_id: str | None = None,
               env_prefix: str | None = None) -> dict[str, Any]:
    """OEM alerts for the caller's dealers: severity, message (the alert title),
    category, status, dealer/district and the open/resolved dates."""
    market = market_id or claims.get("market_id") or "us"
    alerts = ddb.scan_filter("OEM_Alerts", "market_id", market, env_prefix)
    dealers = _dealer_scope(claims)
    if dealers:
        alerts = [a for a in alerts if a.get("dealer_id") in dealers or a.get("account_id") in dealers]
    slim = [_public_alert(a) for a in alerts]
    return {"count": len(slim), "alerts": slim, "source_ref": _SRC}


def get_incentive_programs(claims: dict[str, Any], market_id: str | None = None,
                           env_prefix: str | None = None) -> dict[str, Any]:
    """OEM C&I programs for the caller's market: name, window and start/end
    dates, goal, status and district — no dealer-specific allocation."""
    market = market_id or claims.get("market_id") or "us"
    programs = ddb.scan_filter("OEM_CIPrograms", "market_id", market, env_prefix)
    dealers = _dealer_scope(claims)
    slim = [_public_program(p, dealers) for p in programs]
    return {"count": len(slim), "programs": slim, "source_ref": _SRC}


# MCP tool registry: tool name -> (callable, {arg: required?}). The Gateway
# handler and the deployer's toolSchema are both generated from this so they
# never drift.
TOOLS: dict[str, dict[str, Any]] = {
    "oem_get_account": {
        "fn": get_account,
        "description": "Get one OEM account (dealer/group/fleet) the caller is scoped to, OEM-internal fields removed.",
        "args": {"account_id": {"type": "string", "required": True}},
    },
    "oem_list_accounts": {
        "fn": list_accounts,
        "description": "List OEM accounts in the caller's dealer/district scope, OEM-internal fields removed.",
        "args": {"market_id": {"type": "string", "required": False}},
    },
    "oem_get_alerts": {
        "fn": get_alerts,
        "description": "OEM alerts (severity + message) for the caller's dealers.",
        "args": {"market_id": {"type": "string", "required": False}},
    },
    "oem_get_incentive_programs": {
        "fn": get_incentive_programs,
        "description": "Active OEM C&I incentive programs for the caller's market (no dealer allocation detail).",
        "args": {"market_id": {"type": "string", "required": False}},
    },
}


def call_tool(name: str, claims: dict[str, Any], arguments: dict[str, Any],
              env_prefix: str | None = None) -> dict[str, Any]:
    """Dispatch one MCP tool call to its function with the caller's claims."""
    spec = TOOLS.get(name)
    if spec is None:
        return {"error": f"unknown tool {name!r}", "available": sorted(TOOLS)}
    kwargs = {k: v for k, v in (arguments or {}).items() if k in spec["args"]}
    return spec["fn"](claims, env_prefix=env_prefix, **kwargs)
