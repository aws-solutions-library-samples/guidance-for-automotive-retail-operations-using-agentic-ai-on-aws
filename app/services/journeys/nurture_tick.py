"""A2 — automated nurture tick (agentic job), spec §3.2 / §4.10.2.

The scheduler-driven half of A2 nurture: no user in the loop. On each tick it
finds NurturePlans whose `next_touch_at` is due, and for each one that still
passes consent + quiet-hours + cadence caps, it composes the stage-appropriate
message, "sends" it (writes an outbound CUST_Messages row), and advances the
plan's next_touch_at by its cadence. Plans that hit a stop-condition are
deactivated instead of sent.

Autonomy gate (PLAT_ConfigAutonomy `a2.schedule_outreach`):
  * EXECUTE levels (act_and_notify / autonomous): actually write the outbound
    message (the send) and advance the plan.
  * WRITE-recommend level (recommend, the affirmed default): write a
    recommendation card (action=nurture_touch) for a human to approve/send,
    and still advance the schedule so the cadence doesn't stall.
  * off / act_with_approval: log the candidate, write nothing.

  run_nurture_tick(market, apply, limit) -> summary of due/sent/recommended/skipped.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from ..common import ddb
from ..common import autonomy_config
from . import nurture

TICK_ACTION_ID = "a2.schedule_outreach"
TICK_SOURCE = "agentic:nurture_tick (plan §3.2; autonomy a2.schedule_outreach)"
_PLANS = "DEALER_NurturePlans"
_MESSAGES = "CUST_Messages"

# Stop-conditions that deactivate a plan (mirrors NurturePlan.stop_conditions).
_STAGE_STOP = "AGREE"  # once a journey reaches AGREE, nurture stops (deal in progress)


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _due(plan: dict[str, Any], at: _dt.datetime) -> bool:
    if not plan.get("active", True):
        return False
    nt = nurture._parse_ts(plan.get("next_touch_at"))
    return nt is None or nt <= at  # no schedule yet, or past due


def _stop_reason(plan: dict[str, Any], journey: dict[str, Any] | None,
                 consent: dict[str, Any]) -> str | None:
    """Return a stop-condition reason if the plan should deactivate, else None."""
    conds = set(plan.get("stop_conditions") or [])
    if "opt_out" in conds and consent.get("sms_opt_out_at") and not consent.get("email_opt_in"):
        return "opt_out"
    if journey is not None:
        stage = journey.get("stage")
        if journey.get("status") in ("closed_won", "converted") or stage == "OWN":
            return "converted"
        if ("stage_AGREE_reached" in conds or "converted" in conds) and stage == _STAGE_STOP:
            return "stage_AGREE_reached"
    return None


def _next_message_id(market: str) -> str:
    return f"MSG-{market.upper()}-TICK-{int(_now().timestamp())}"


def run_nurture_tick(market: str = "us", apply: bool = False,
                     limit: int | None = None, env_prefix: str | None = None) -> dict[str, Any]:
    """Process due nurture plans for a market.

    apply=False is a dry run: it decides what WOULD happen (due/permitted/stop)
    and writes nothing.
    """
    level = autonomy_config.active_level(TICK_ACTION_ID, market, env_prefix)
    at = _now()
    plans = ddb.scan_all(_PLANS, env_prefix)
    # scope to the market via the plan's journey (plans carry no market_id)
    due_plans = []
    for p in plans:
        if not _due(p, at):
            continue
        j = ddb.get_item("DEALER_Journeys", {"journey_id": p.get("journey_id")}, env_prefix)
        if j is None or (j.get("market_id") or "us") != market:
            continue
        due_plans.append((p, j))
    if limit is not None:
        due_plans = due_plans[:limit]

    sent = recommended = stopped = suppressed = 0
    results: list[dict[str, Any]] = []

    for plan, journey in due_plans:
        customer_id = plan.get("customer_id")
        consent = ddb.get_item("CUST_Consent", {"customer_id": customer_id}, env_prefix) or {}

        # 1) stop-conditions deactivate the plan (no send).
        stop = _stop_reason(plan, journey, consent)
        if stop:
            stopped += 1
            results.append({"journey_id": plan.get("journey_id"), "outcome": "stopped", "reason": stop})
            if apply:
                p = dict(plan); p["active"] = False; p["blocked_reason"] = f"stopped:{stop}"
                p["updated_at"] = nurture._iso(at)
                ddb.put_item(_PLANS, p, env_prefix)
            continue

        # 2) choose a permitted channel now (consent + quiet hours + caps).
        channel, gate = None, None
        preferred = consent.get("preferred_channel") or plan.get("channel") or "email"
        for ch in [preferred] + [c for c in nurture.CHANNELS if c != preferred]:
            g = nurture.check_consent(customer_id, ch, at, env_prefix)
            if g["ok"]:
                channel, gate = ch, g
                break
        if channel is None:
            suppressed += 1
            results.append({"journey_id": plan.get("journey_id"), "outcome": "suppressed",
                            "reason": "no permitted channel (consent/quiet-hours/caps)"})
            continue  # leave next_touch_at as-is; a later tick retries

        # 3) compose the message from the active template.
        try:
            msg = nurture.compose_message(journey, channel=channel, env_prefix=env_prefix)
        except nurture.NotFound as e:
            suppressed += 1
            results.append({"journey_id": plan.get("journey_id"), "outcome": "suppressed",
                            "reason": f"no template: {e}"})
            continue

        # advance the schedule by the plan's cadence regardless of write mode,
        # so the cadence keeps moving (a recommend-mode human still gets a card).
        cadence_days = nurture._cadence_days(plan.get("cadence") or "every_3_days")
        next_touch = nurture._iso(at + _dt.timedelta(days=cadence_days))

        outcome = "candidate"
        if apply and level in autonomy_config.EXECUTE_LEVELS:
            # SEND: deliver through the OutreachProvider (SES/SNS with a dealer-
            # task fallback; it records CUST_Messages + re-checks consent), then
            # advance the plan. The provider is chosen by ENABLE_CONNECT.
            from ..platform import outreach as _outreach
            to = (journey or {}).get("contact_email") or (journey or {}).get("contact_phone") or ""
            _outreach.get_outreach_provider().send(
                channel=channel, to=to, subject=msg.get("subject") or "Update",
                body=msg["body"], customer_id=customer_id,
                dealer_id=(journey or {}).get("dealer_id"), env_prefix=env_prefix)
            # The template's intent is the promise this touch makes ("offer_ready",
            # "delivery_prep", …) — remember it (durable-memory contract) so the next agent or
            # advisor who reaches this customer honors it. One call site: the
            # tick is the only path where a nurture message is actually sent.
            from ..platform import memory as _memory
            _memory.remember(customer_id, "promise",
                             f"Nurture {channel} sent, intent \"{msg.get('intent') or journey.get('stage')}\"",
                             source_ref=msg.get("source_ref") or TICK_SOURCE, actor="A2",
                             env_prefix=env_prefix)
            p = dict(plan); p["next_touch_at"] = next_touch; p["updated_at"] = nurture._iso(at)
            ddb.put_item(_PLANS, p, env_prefix)
            sent += 1
            outcome = "sent"
        elif apply and level in autonomy_config.WRITE_LEVELS:
            # RECOMMEND: write a recommendation card, still advance the schedule.
            ddb.put_item("DEALER_Recommendations", {
                "recommendation_id": f"REC-NURTURE-{plan.get('journey_id')}",
                "action": "nurture_touch", "agent_id": "A2",
                "customer_id": customer_id, "journey_id": plan.get("journey_id"),
                "shown": 0, "accepted": 0, "converted": 0, "shown_at": nurture._iso(at),
                "autonomy_level": level, "channel": channel,
                "message_preview": msg["body"][:280], "template_id": msg.get("template_id"),
                "source_ref": TICK_SOURCE,
            }, env_prefix)
            p = dict(plan); p["next_touch_at"] = next_touch; p["updated_at"] = nurture._iso(at)
            ddb.put_item(_PLANS, p, env_prefix)
            recommended += 1
            outcome = "recommended"
        # off / act_with_approval: leave everything; just report the candidate.

        results.append({"journey_id": plan.get("journey_id"), "channel": channel,
                        "outcome": outcome, "template_id": msg.get("template_id"),
                        "next_touch_at": next_touch})

    return {
        "market": market,
        "autonomy_level": level,
        "due_plans": len(due_plans),
        "sent": sent,
        "recommended": recommended,
        "stopped": stopped,
        "suppressed": suppressed,
        "applied": apply,
        "ran_at": nurture._iso(at),
        "sample": results[:12],
    }
