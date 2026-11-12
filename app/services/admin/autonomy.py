"""L3 — Admin Console: agent autonomy configuration (spec §4.10.1, §14 Q8).

The autonomy surface the dealer principal / tenant admin uses to see and change
how much each agent action may do on its own — the `PLAT_ConfigAutonomy` levels
that the agentic layer (L6) reads before acting. Reading shows the current
active level per action; changing it is an append-only, versioned write: a new
version becomes active and the prior active version is retired, so the full
history is preserved and the change is auditable.

  list_autonomy(market)                    -> current active level per action_id
  set_autonomy(action_id, level, actor, …) -> append a new active version
  rollback_autonomy(action_id, to_version)  -> re-activate a prior version's level
                                              as a NEW version (history is never
                                              rewritten)

Hard rules (§4.10.1): an action listed in autonomy_config.HARD_CEILINGS cannot
be set above its ceiling — set_autonomy raises HardRuleViolation.

Levels (spec §4.10.1), least → most autonomous:
  off · recommend · act_with_approval · act_and_notify · autonomous
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from ..common import autonomy_config, ddb
from ..events import bus as _bus

LEVELS = list(autonomy_config.LEVELS)
_TABLE = "PLAT_ConfigAutonomy"


class InvalidLevel(Exception):
    pass


class NotFound(Exception):
    pass


class HardRuleViolation(Exception):
    """The requested level exceeds a hard-rule ceiling (§4.10.1, acceptance #33)."""


def _item_key(market: str, scope: str, scope_id: str | None, action_id: str) -> str:
    return f"autonomy#{market}#{scope}#{scope_id or '-'}#{action_id}"


def list_autonomy(market: str = "us", env_prefix: str | None = None) -> dict[str, Any]:
    """Current active autonomy level for each action, at tenant scope."""
    rows = ddb.scan_filter(_TABLE, "market_id", market, env_prefix)
    # group by action_id (tenant scope), pick the active row (or highest version)
    by_action: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r.get("scope") != "tenant":
            continue
        by_action.setdefault(r.get("action_id"), []).append(r)

    actions = []
    for action_id, versions in sorted(by_action.items()):
        active = [v for v in versions if v.get("is_active")]
        chosen = (active or sorted(versions, key=lambda v: v.get("version") or 0, reverse=True))[0]
        actions.append({
            "action_id": action_id,
            "level": chosen.get("level"),
            "version": chosen.get("version"),
            "scope": "tenant",
            "effective_from": chosen.get("effective_from"),
            "undoable": chosen.get("undoable"),
            "versions": len(versions),
            "ceiling": autonomy_config.ceiling(action_id),
            "hard_rule": action_id in autonomy_config.HARD_CEILINGS,
        })
    return {"market_id": market, "levels": LEVELS, "actions": actions, "count": len(actions)}


def set_autonomy(action_id: str, level: str, actor: str, market: str = "us",
                 scope: str = "tenant", scope_id: str | None = None,
                 note: str | None = None,
                 env_prefix: str | None = None) -> dict[str, Any]:
    """Append a new active autonomy version for an action and retire the prior one.

    Append-only: never mutates an existing version's fields except flipping the
    prior active row's is_active flag off. Returns the new version record.
    `note` overrides the version's source_ref so a non-console publisher (the
    autonomy promotion loop) can say WHY in the version history.
    """
    if level not in LEVELS:
        raise InvalidLevel(f"level must be one of {LEVELS}, got {level!r}")
    cap = autonomy_config.ceiling(action_id)
    if autonomy_config.rank(level) > autonomy_config.rank(cap):
        raise HardRuleViolation(f"{action_id} is bounded by a hard rule: maximum level is {cap!r}")

    item_key = _item_key(market, scope, scope_id, action_id)
    versions = ddb.query_hash(_TABLE, "item_key", item_key, env_prefix)
    if not versions:
        raise NotFound(f"no autonomy config for {action_id} (item_key {item_key})")

    prior_active = [v for v in versions if v.get("is_active")]
    max_version = max((v.get("version") or 0) for v in versions)
    new_version = max_version + 1
    now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    # 1) retire the prior active version(s) — rewrite with is_active=False.
    for pa in prior_active:
        retired = dict(pa)
        retired["is_active"] = False
        ddb.put_item(_TABLE, retired, env_prefix)

    # 2) append the new active version (copy stable fields from the latest known).
    template = max(versions, key=lambda v: v.get("version") or 0)
    new_row = {
        "item_key": item_key,
        "version": new_version,
        "action_id": action_id,
        "config_key": "autonomy",
        "level": level,
        "scope": scope,
        "scope_id": scope_id,
        "market_id": market,
        "is_active": True,
        "effective_from": now,
        "undoable": template.get("undoable", True),
        "constraints": template.get("constraints"),
        "source_ref": note or f"admin-console:set_autonomy by {actor} at {now}",
    }
    ddb.put_item(_TABLE, new_row, env_prefix)
    bus_event = _bus.emit("autonomy.published", {
        "action_id": action_id, "level": level, "version": new_version, "scope": scope,
        "scope_id": scope_id, "market_id": market, "actor": actor}, actor=actor,
        env_prefix=env_prefix)
    from . import audit_log
    audit_log.record(actor, "autonomy.set", action_id,
                     {"level": level, "version": new_version, "scope": scope, "scope_id": scope_id,
                      "market_id": market}, env_prefix=env_prefix)
    return {
        "bus_event": bus_event,
        "action_id": action_id, "new_level": level, "new_version": new_version,
        "retired_versions": [pa.get("version") for pa in prior_active],
        "effective_from": now, "actor": actor,
    }


def rollback_autonomy(action_id: str, to_version: int, actor: str, market: str = "us",
                      scope: str = "tenant", scope_id: str | None = None,
                      env_prefix: str | None = None) -> dict[str, Any]:
    """Roll back to a prior version's level by publishing it as a new version."""
    versions = ddb.query_hash(_TABLE, "item_key", _item_key(market, scope, scope_id, action_id),
                              env_prefix)
    target = next((v for v in versions if int(v.get("version") or 0) == int(to_version)), None)
    if target is None:
        raise NotFound(f"{action_id} has no version {to_version}")
    out = set_autonomy(action_id, target.get("level"), actor, market=market, scope=scope,
                       scope_id=scope_id, env_prefix=env_prefix)
    return {**out, "rolled_back_to": int(to_version)}


def autonomy_history(action_id: str, market: str = "us", scope: str = "tenant",
                     scope_id: str | None = None, env_prefix: str | None = None) -> dict[str, Any]:
    """Every version of one action's autonomy level, newest first (editor history)."""
    from ..common import autonomy_config
    versions = ddb.query_hash(_TABLE, "item_key", _item_key(market, scope, scope_id, action_id), env_prefix)
    versions.sort(key=lambda v: int(v.get("version") or 0), reverse=True)
    return {"action_id": action_id, "market_id": market, "scope": scope, "scope_id": scope_id,
            "ceiling": autonomy_config.ceiling(action_id),
            "hard_rule": action_id in autonomy_config.HARD_CEILINGS,
            "versions": [{"version": v.get("version"), "level": v.get("level"), "is_active": bool(v.get("is_active")),
                          "effective_from": v.get("effective_from"), "source_ref": v.get("source_ref"),
                          "constraints": v.get("constraints")} for v in versions]}
