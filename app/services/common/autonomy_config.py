"""Shared autonomy-level resolution (spec §4.10.1).

Agentic jobs read the active PLAT_ConfigAutonomy level for their action before
acting: it decides whether the job WRITES an effect, only RECOMMENDS it, or does
nothing. This is the single resolver both the equity monitor and the nurture
tick use, so the level semantics stay consistent.

Levels, least -> most autonomous:
  off · recommend · act_with_approval · act_and_notify · autonomous
"""
from __future__ import annotations

from . import ddb

# Levels at which an agentic job may WRITE its effect (send a message, write a
# recommendation card). 'recommend' writes a recommendation for a human to
# action; 'off' / 'act_with_approval' do not auto-write the effect.
WRITE_LEVELS = ("recommend", "act_and_notify", "autonomous")
# Levels at which the job may actually EXECUTE the downstream effect (e.g. send).
EXECUTE_LEVELS = ("act_and_notify", "autonomous")

LEVELS = ("off", "recommend", "act_with_approval", "act_and_notify", "autonomous")

# Hard rules (§4.10.1) sit ABOVE configuration: these actions can never be raised
# past the ceiling by any PLAT_ConfigAutonomy row — no automatic budget or
# incentive allocation (§3.5/§3.6), plan publishing stays human-approved, and the
# customer owns estimate approval (§3.7). The editor rejects the attempt and the
# resolver clamps at runtime (defense in depth against a hand-written row).
HARD_CEILINGS: dict[str, str] = {
    "a5.shift_budget": "act_with_approval",
    "a5.publish_campaign": "act_with_approval",
    "a5.recommend_budget_shift": "act_with_approval",
    "a6.publish_account_plan": "act_with_approval",
    "a6.publish_plan": "act_with_approval",
    "a6.simulate_incentive_mix": "recommend",
    "a6.allocate_incentives": "recommend",
    "a7.record_approval": "recommend",
    "a7.decide_estimate_line": "recommend",
}


def rank(level: str) -> int:
    return LEVELS.index(level) if level in LEVELS else 0


def ceiling(action_id: str) -> str:
    return HARD_CEILINGS.get(action_id, LEVELS[-1])


def clamp(action_id: str, level: str) -> str:
    cap = ceiling(action_id)
    return cap if rank(level) > rank(cap) else level


def active_level(action_id: str, market: str, env_prefix: str | None = None,
                 default: str = "recommend") -> str:
    """Highest active tenant-scope autonomy level for an action_id (or default)."""
    rows = ddb.scan_filter("PLAT_ConfigAutonomy", "action_id", action_id, env_prefix)
    active = [r for r in rows if r.get("is_active") and r.get("scope") == "tenant"
              and (r.get("market_id") in (market, None))]
    if not active:
        return default
    latest = max(active, key=lambda r: r.get("version") or 0)
    return clamp(action_id, latest.get("level") or default)
