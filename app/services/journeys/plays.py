"""A2 — Plays engine: run_play (spec AR-10 / §4.10.2).

A Play is a named A2 orchestration template (DEALER_Plays: play_id, name,
trigger, steps[]). run_play executes it for a journey WITHOUT a click when a
trigger event fires, recording a DEALER_PlayRuns row and a DEALER_Recommendations
card at the play's autonomy level. It never sends to the customer directly at
`recommend` — it produces the card the advisor actions.

  run_play(play_id | trigger, journey_id, trigger_event) -> the play run summary

Trigger -> play mapping (spec §4.10.2):
  journey.stalled            -> Callback Brief
  discovery.match_found      -> Recommend Vehicle
  service.handoff_to_sales   -> Full Prep
  lease.end_window_entered / equity.crossed_positive -> Upgrade Pitch
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from ..common import ddb, autonomy_config

_PLAYS = "DEALER_Plays"
_PLAY_RUNS = "DEALER_PlayRuns"
_RECS = "DEALER_Recommendations"
_JOURNEYS = "DEALER_Journeys"
_PLAY_ACTION = "a2.select_next_best_action"

# spec §4.10.2 trigger -> play trigger tag in the seed
# Each event may match several seed tags: the generator's DEALER_Plays rows tag
# the stalled play "idle_days>7" and the upgrade play "positive_equity"; the
# names on the left of each tuple are the spec's §4.10.2 vocabulary.
_TRIGGER_MAP: dict[str, tuple[str, ...]] = {
    "journey.stalled": ("stalled", "idle_days>7"),
    "discovery.match_found": ("match_found", "discovery_match"),
    "service.handoff_to_sales": ("handoff", "service_handoff", "full_prep"),
    "lease.end_window_entered": ("lease_end", "positive_equity"),
    "equity.crossed_positive": ("positive_equity",),
    "campaign.lead": ("campaign_lead",),
}


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _find_play(play_id: str | None, trigger: str | None, market: str,
               env_prefix: str | None) -> dict[str, Any] | None:
    plays = ddb.scan_filter(_PLAYS, "market_id", market, env_prefix)
    if play_id:
        return next((p for p in plays if p.get("play_id") == play_id), None)
    if trigger:
        tags = _TRIGGER_MAP.get(trigger, (trigger,))
        for tag in tags:                                  # first tag with a play wins
            hit = next((p for p in plays if p.get("trigger") == tag), None)
            if hit is not None:
                return hit
    return None


def run_play(journey_id: str, play_id: str | None = None, trigger: str | None = None,
             trigger_event_id: str | None = None, apply: bool = False,
             env_prefix: str | None = None) -> dict[str, Any]:
    """Execute a Play for a journey on a trigger (AR-10). Writes a DEALER_PlayRuns
    row + a recommendation card at the play's autonomy level."""
    journey = ddb.get_item(_JOURNEYS, {"journey_id": journey_id}, env_prefix)
    if journey is None:
        return {"journey_id": journey_id, "error": "journey not found"}
    market = journey.get("market_id") or "us"
    play = _find_play(play_id, trigger, market, env_prefix)
    if play is None:
        return {"journey_id": journey_id, "error": f"no play for play_id={play_id} trigger={trigger}"}

    level = autonomy_config.active_level(_PLAY_ACTION, market, env_prefix)
    ts = _now()
    run_id = f"PLAYRUN-{play['play_id']}-{journey_id}"
    steps = play.get("steps") or []
    # deterministic step outcomes (identify -> compose -> [send only at EXECUTE] -> measure)
    executed = []
    for step in steps:
        if step == "send" and level not in autonomy_config.EXECUTE_LEVELS:
            executed.append({"step": step, "status": "held_for_human"})
        else:
            executed.append({"step": step, "status": "done"})

    run_row = {"play_run_id": run_id, "play_id": play["play_id"], "journey_id": journey_id,
               "trigger": trigger, "trigger_event_id": trigger_event_id,
               "autonomy_level": level, "steps": executed, "status": "completed",
               "ran_at": ts, "source_ref": f"A2.run_play:{play['play_id']} (AR-10)"}
    rec_id = f"REC-PLAY-{play['play_id']}-{journey_id}"
    rec_row = {"recommendation_id": rec_id, "action": f"play:{play['play_id']}",
               "agent_id": "A2", "journey_id": journey_id, "customer_id": journey.get("customer_id"),
               "dealer_id": journey.get("dealer_id"), "shown": 0, "accepted": 0, "converted": 0,
               "shown_at": ts, "autonomy_level": level,
               "reason": f"Play '{play.get('name')}' fired on {trigger}",
               "source_ref": f"A2.run_play:{play['play_id']}"}

    wrote = False
    if apply and level in autonomy_config.WRITE_LEVELS:
        ddb.put_item(_PLAY_RUNS, run_row, env_prefix)
        ddb.put_item(_RECS, rec_row, env_prefix)
        wrote = True

    return {"journey_id": journey_id, "play_id": play["play_id"], "play_name": play.get("name"),
            "trigger": trigger, "autonomy_level": level, "steps": executed,
            "written": wrote, "play_run_id": run_id if wrote else None,
            "source_ref": run_row["source_ref"]}
