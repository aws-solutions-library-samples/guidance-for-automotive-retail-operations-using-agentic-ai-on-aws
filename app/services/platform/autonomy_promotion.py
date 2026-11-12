"""Autonomy ladder as a feedback loop (spec §4.10.1 + §4.10.3).

Until this module, raising an agent action's autonomy level was a purely manual
Admin-Console act. This closes the loop the spec implies: the outcome funnel
(DEALER_Recommendations shown → accepted → converted, platform/recommendations)
is the evidence, and a weekly job turns sustained acceptance into a PROPOSED
level change through the exact same guarded publish path a human uses
(admin/autonomy.set_autonomy) — so hard-rule ceilings, versioning, the audit
log and the `autonomy.published` event all still apply.

Thresholds (constants below, deliberately conservative):
  * promote  recommend → act_and_notify  when shown >= MIN_SHOWN and
    acceptance_rate >= PROMOTE_ACCEPTANCE — humans accepted this card often
    enough that executing-and-notifying saves work without losing oversight.
  * demote   act_and_notify → recommend  when shown >= MIN_SHOWN and
    acceptance_rate < DEMOTE_ACCEPTANCE — the guard rail back down: a level
    that was earned can be un-earned.
  * Hard-rule ceilings (autonomy_config.HARD_CEILINGS) are never crossed; a
    qualifying action whose ceiling sits below act_and_notify is skipped and
    the reason recorded.

Each change publishes a new PLAT_ConfigAutonomy version whose note carries the
stats ("promoted by outcome loop: 41 shown, 68% accepted" — visible in the
/admin/autonomy history), writes an audit row, emits autonomy.promoted /
autonomy.demoted, and opens a task for the tenant admin. Idempotent: once the
level changed, the action no longer sits at the source level, so re-running
with the same data proposes nothing.

  run(market="us", env_prefix=None, dry=False) -> summary (dry evaluates only)
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from ..common import autonomy_config, ddb
from . import recommendations

ACTOR = "autonomy-promotion-loop"

# Evidence thresholds. MIN_SHOWN keeps one lucky week from promoting an action;
# the 0.6/0.3 band leaves a hysteresis gap so a borderline action does not
# oscillate between levels week over week.
MIN_SHOWN = 25
PROMOTE_ACCEPTANCE = 0.6
DEMOTE_ACCEPTANCE = 0.3
PROMOTE_FROM = "recommend"
PROMOTE_TO = "act_and_notify"

# Recommendation rows name the card's action; autonomy config names the agent
# action id. Where the two differ, this alias map joins them; otherwise the id
# is derived as "<agent>.<action>" and must exist in the autonomy config.
ACTION_ALIASES: dict[str, str] = {
    "nurture_touch": "a2.schedule_outreach",      # nurture_tick's recommend-mode card
    "outreach_draft": "a2.schedule_outreach",     # A3 agent's held send_outreach
    "next_best_action": "a2.select_next_best_action",
    "objection_response": "a3.suggest_response",
}


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _autonomy_id(agent_id: str, action: str) -> str:
    return ACTION_ALIASES.get(action) or f"{(agent_id or '').lower()}.{action}"


def _outcomes_by_autonomy_id(env_prefix: str | None) -> dict[str, dict[str, int]]:
    """shown/accepted totals from the outcome loop, keyed by autonomy action id."""
    agg: dict[str, dict[str, int]] = {}
    for row in recommendations.stats(env_prefix=env_prefix)["actions"]:
        aid = _autonomy_id(row.get("agent_id"), row.get("action"))
        b = agg.setdefault(aid, {"shown": 0, "accepted": 0})
        b["shown"] += int(row.get("shown") or 0)
        b["accepted"] += int(row.get("accepted") or 0)
    return agg


def _admin_assignee(env_prefix: str | None) -> str:
    """The tenant admin who should review a level change (fallback: a dealer
    admin, then a literal 'admin' so the task is never silently dropped)."""
    users = ddb.scan_all("PLAT_Users", env_prefix)
    for group in ("admin_tenant", "admin_dealer"):
        for u in users:
            if group in (u.get("groups") or []):
                return u.get("user_id")
    return "admin"


def _apply_change(action_id: str, to_level: str, direction: str, shown: int, rate: float,
                  market: str, env_prefix: str | None) -> dict[str, Any]:
    """Publish the new level through the human publish path + full bookkeeping."""
    from ..admin import audit_log, autonomy as admin_autonomy
    from ..events import bus
    from . import tasks as _tasks
    pct = round(rate * 100)
    note = f"{direction}d by outcome loop: {shown} shown, {pct}% accepted"
    published = admin_autonomy.set_autonomy(action_id, to_level, ACTOR, market=market,
                                            note=note, env_prefix=env_prefix)
    audit_log.record(ACTOR, f"autonomy.{direction}d", action_id,
                     {"to_level": to_level, "shown": shown, "acceptance_rate": round(rate, 4),
                      "version": published["new_version"], "market_id": market},
                     env_prefix=env_prefix)
    bus.emit(f"autonomy.{direction}d", {
        "action_id": action_id, "level": to_level, "version": published["new_version"],
        "market_id": market, "shown": shown, "acceptance_rate": round(rate, 4),
        "source_ref": f"platform.autonomy_promotion:{action_id}"},
        actor=ACTOR, env_prefix=env_prefix)
    task = _tasks.create_task(
        f"Autonomy {direction}d: {action_id} → {to_level} ({shown} shown, {pct}% accepted) — review",
        _admin_assignee(env_prefix), created_by=ACTOR, dealer_id=None, sla_hours=72,
        resource_url="/admin/autonomy", source_agent="autonomy_promotion", env_prefix=env_prefix)
    return {"action_id": action_id, "to_level": to_level, "direction": direction,
            "shown": shown, "acceptance_rate": round(rate, 4),
            "version": published["new_version"], "task_id": task.get("task_id"), "note": note}


def run(market: str = "us", env_prefix: str | None = None, dry: bool = False) -> dict[str, Any]:
    """Evaluate every configured action against the outcome loop and propose
    level changes. dry=True evaluates and reports but publishes nothing."""
    from ..admin import autonomy as admin_autonomy
    cfg = admin_autonomy.list_autonomy(market, env_prefix)
    outcomes = _outcomes_by_autonomy_id(env_prefix)

    promoted: list[dict[str, Any]] = []
    demoted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for action in cfg["actions"]:
        aid, level = action["action_id"], action.get("level")
        st = outcomes.get(aid)
        if not st or st["shown"] < MIN_SHOWN:
            continue  # not enough evidence either way — leave the level alone
        rate = st["accepted"] / st["shown"]
        if level == PROMOTE_FROM and rate >= PROMOTE_ACCEPTANCE:
            ceiling = autonomy_config.ceiling(aid)
            if autonomy_config.rank(PROMOTE_TO) > autonomy_config.rank(ceiling):
                # Hard rule stays above learning: record why, never cross it.
                skipped.append({"action_id": aid, "reason": f"hard-rule ceiling {ceiling!r}",
                                "shown": st["shown"], "acceptance_rate": round(rate, 4)})
                continue
            if dry:
                promoted.append({"action_id": aid, "to_level": PROMOTE_TO, "dry": True,
                                 "shown": st["shown"], "acceptance_rate": round(rate, 4)})
            else:
                promoted.append(_apply_change(aid, PROMOTE_TO, "promote", st["shown"], rate,
                                              market, env_prefix))
        elif level == PROMOTE_TO and rate < DEMOTE_ACCEPTANCE:
            if dry:
                demoted.append({"action_id": aid, "to_level": PROMOTE_FROM, "dry": True,
                                "shown": st["shown"], "acceptance_rate": round(rate, 4)})
            else:
                demoted.append(_apply_change(aid, PROMOTE_FROM, "demote", st["shown"], rate,
                                             market, env_prefix))

    return {"job": "autonomy-promotion", "market": market, "dry": dry,
            "evaluated": len(cfg["actions"]),
            "thresholds": {"min_shown": MIN_SHOWN, "promote_acceptance": PROMOTE_ACCEPTANCE,
                           "demote_acceptance": DEMOTE_ACCEPTANCE},
            "promoted": promoted, "demoted": demoted, "skipped": skipped,
            "ran_at": _now(),
            "source_ref": "platform.autonomy_promotion (outcome loop §4.10.3 → autonomy §4.10.1)"}
