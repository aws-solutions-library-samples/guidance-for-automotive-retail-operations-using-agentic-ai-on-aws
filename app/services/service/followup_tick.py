"""A7 — hourly service follow-up tick (agentic job), spec §3.7 / §4.10.2 / §7.2.

The spec's `app-service-followup-tick` (hourly). Two sweeps, no user in the loop:

  1. Declined-work reminders — service-family NurturePlans written by
     `followup.schedule_declined_followup` whose `next_touch_at` is due. The
     reminder re-presents the ORIGINAL declined finding from the service
     template (no sales content). Stop conditions: `opt_out`, `work_booked`
     (an open service journey for the VIN), `converted`.
  2. Due-service reminders — delivered vehicles whose `predict_next_service`
     says service is due within `RETAIL_OPS_DUE_SERVICE_DAYS` (default 14) or overdue,
     with no open service journey: one `service_due` recommendation card per
     VIN (REC-DUE-<vin>, idempotent), the owner's nudge in My Garage.

Autonomy gate (`a7.send_due_service_reminder`, seeded; default `recommend`):
  * EXECUTE levels (act_and_notify / autonomous): send through OutreachProvider
    (consent + quiet hours re-checked), record CUST_Messages, advance the plan.
  * recommend: write a `declined_reminder` recommendation card for the advisor
    and still advance the plan so the cadence keeps moving.
  * off / act_with_approval: observe only.

  run_service_followup_tick(market, apply, limit) -> summary
"""
from __future__ import annotations

import datetime as _dt
import os
from typing import Any

from ..common import autonomy_config, ddb
from ..journeys import nurture
from . import booking

ACTION_ID = "a7.send_due_service_reminder"   # seeded PLAT_ConfigAutonomy action
SOURCE = "agentic:service_followup_tick (spec §3.7; hourly app-service-followup-tick)"
_PLANS = "DEALER_NurturePlans"
_SJ = "DEALER_ServiceJourneys"
_OPEN = ("BOOK", "CHECK_IN", "INSPECT", "DIAGNOSE", "APPROVE", "REPAIR", "READY")


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def _iso(d: _dt.datetime) -> str:
    return d.isoformat().replace("+00:00", "Z")


def _parse(ts: str | None) -> _dt.datetime | None:
    if not ts:
        return None
    try:
        return _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _due_window_days() -> int:
    try:
        return int(os.environ.get("RETAIL_OPS_DUE_SERVICE_DAYS", "14"))
    except ValueError:
        return 14


def _open_service_vins(market: str, env_prefix: str | None) -> dict[str, set[str]]:
    """vin -> ids of OPEN service journeys for that vin."""
    out: dict[str, set[str]] = {}
    for sj in ddb.scan_filter(_SJ, "market_id", market, env_prefix):
        if sj.get("stage") in _OPEN and sj.get("vin"):
            out.setdefault(sj["vin"], set()).add(sj.get("service_journey_id"))
    return out


def _stop_reason(plan: dict[str, Any], sj: dict[str, Any] | None, consent: dict[str, Any],
                 open_vins: dict[str, set[str]]) -> str | None:
    # Same opt-out semantics as the sales nurture tick (CUST_Consent flags).
    if consent.get("sms_opt_out_at") and not consent.get("email_opt_in"):
        return "opt_out"
    vin = (sj or {}).get("vin")
    others = open_vins.get(vin, set()) - {plan.get("service_journey_id")}
    if vin and others:
        return "work_booked"              # the customer booked the declined work
    if plan.get("converted"):
        return "converted"
    return None


def run_service_followup_tick(market: str = "us", apply: bool = False, limit: int | None = None,
                              env_prefix: str | None = None) -> dict[str, Any]:
    level = autonomy_config.active_level(ACTION_ID, market, env_prefix)
    at = _now()
    open_vins = _open_service_vins(market, env_prefix)

    # --- 1) declined-work reminders ------------------------------------------
    plans = [p for p in ddb.scan_all(_PLANS, env_prefix)
             if p.get("family") == "service" and p.get("active", True)]
    due = []
    for p in plans:
        nt = _parse(p.get("next_touch_at"))
        if nt is None or nt > at:
            continue
        sj = ddb.get_item(_SJ, {"service_journey_id": p.get("service_journey_id")}, env_prefix) \
            if p.get("service_journey_id") else None
        if sj is not None and (sj.get("market_id") or "us") != market:
            continue
        due.append((p, sj))
    if limit is not None:
        due = due[:limit]
    sent = recommended = stopped = suppressed = 0
    reminders: list[dict[str, Any]] = []
    for plan, sj in due:
        cid = plan.get("customer_id")
        consent = ddb.get_item("CUST_Consent", {"customer_id": cid}, env_prefix) or {}
        stop = _stop_reason(plan, sj, consent, open_vins)
        entry = {"nurture_plan_id": plan.get("nurture_plan_id"), "service_journey_id": plan.get("service_journey_id")}
        if stop:
            stopped += 1
            entry["outcome"] = f"stopped:{stop}"
            if apply:
                row = dict(plan); row["active"] = False; row["blocked_reason"] = f"stopped:{stop}"
                row["updated_at"] = _iso(at); ddb.put_item(_PLANS, row, env_prefix)
            reminders.append(entry); continue
        channel, gate = None, None
        for ch in [plan.get("channel") or "email"] + [c for c in ("email", "sms") if c != (plan.get("channel") or "email")]:
            g = nurture.check_consent(cid, ch, at, env_prefix)
            if g["ok"]:
                channel, gate = ch, g; break
        if channel is None:
            suppressed += 1; entry["outcome"] = "suppressed:consent/quiet-hours/caps"
            reminders.append(entry); continue
        render_ctx = {"journey_id": plan.get("service_journey_id"), "customer_id": cid, "market_id": market,
                      "dealer_id": (sj or {}).get("dealer_id"), "owner_user_id": (sj or {}).get("advisor_id"),
                      "primary_model_id": None, "stage": "OWN", "context": {}}
        try:
            msg = nurture.compose_message(render_ctx, channel=channel, intent=plan.get("template_trigger"),
                                          family="service", env_prefix=env_prefix)
        except nurture.NotFound as e:
            suppressed += 1; entry["outcome"] = f"suppressed:no template ({e})"
            reminders.append(entry); continue
        cadence_days = nurture._cadence_days(plan.get("cadence") or "every_30_days")
        next_touch = _iso(at + _dt.timedelta(days=cadence_days))
        outcome = "candidate"
        if apply and level in autonomy_config.EXECUTE_LEVELS:
            from ..platform import outreach as _outreach
            cust = ddb.get_item("DEALER_Customers", {"customer_id": cid}, env_prefix) or {}
            to = cust.get("email") if channel == "email" else cust.get("phone")
            _outreach.get_outreach_provider().send(
                channel=channel, to=to or "", subject=msg.get("subject") or "Service reminder",
                body=msg["body"], customer_id=cid, dealer_id=(sj or {}).get("dealer_id"), env_prefix=env_prefix)
            row = dict(plan); row["next_touch_at"] = next_touch; row["updated_at"] = _iso(at)
            ddb.put_item(_PLANS, row, env_prefix); sent += 1; outcome = "sent"
        elif apply and level in autonomy_config.WRITE_LEVELS:
            ddb.put_item("DEALER_Recommendations", {
                "recommendation_id": f"REC-DECLINED-{plan.get('service_journey_id')}",
                "action": "declined_reminder", "agent_id": "A7", "customer_id": cid,
                "service_journey_id": plan.get("service_journey_id"), "journey_id": plan.get("journey_id"),
                "shown": 0, "accepted": 0, "converted": 0, "shown_at": _iso(at),
                "autonomy_level": level, "channel": channel, "message_preview": msg["body"][:280],
                "template_id": msg.get("template_id"), "declined_items": plan.get("declined_items"),
                "source_ref": SOURCE,
            }, env_prefix)
            row = dict(plan); row["next_touch_at"] = next_touch; row["updated_at"] = _iso(at)
            ddb.put_item(_PLANS, row, env_prefix); recommended += 1; outcome = "recommended"
        entry.update({"outcome": outcome, "channel": channel, "next_touch_at": next_touch})
        reminders.append(entry)

    # --- 2) due-service reminders ---------------------------------------------
    window = _due_window_days()
    horizon = at + _dt.timedelta(days=window)
    vehicles = [v for v in ddb.scan_filter("DEALER_Vehicles", "market_id", market, env_prefix)
                if v.get("vin") and v.get("customer_id")]
    due_cards = 0
    due_sample: list[dict[str, Any]] = []
    for v in vehicles if limit is None else vehicles[:limit]:
        vin = v["vin"]
        if vin in open_vins:
            continue
        try:
            pred = booking.predict_next_service(vin, env_prefix)
        except Exception:
            continue
        due_by = _parse(pred.get("due_by"))
        if not (pred.get("overdue") or (due_by is not None and due_by <= horizon)):
            continue
        rec_id = f"REC-DUE-{vin}"
        if ddb.get_item("DEALER_Recommendations", {"recommendation_id": rec_id}, env_prefix) is not None:
            continue
        due_sample.append({"vin": vin, "due_by": pred.get("due_by"), "overdue": pred.get("overdue")})
        if apply and level in autonomy_config.WRITE_LEVELS:
            ddb.put_item("DEALER_Recommendations", {
                "recommendation_id": rec_id, "action": "service_due", "agent_id": "A7",
                "customer_id": v.get("customer_id"), "dealer_id": v.get("dealer_id"), "vin": vin,
                "shown": 0, "accepted": 0, "converted": 0, "shown_at": _iso(at),
                "autonomy_level": level, "due_by": pred.get("due_by"), "overdue": pred.get("overdue"),
                "recommended_services": pred.get("recommended_services"),
                "source_ref": pred.get("source_ref"),
            }, env_prefix)
            due_cards += 1

    return {
        "market": market, "autonomy_level": level, "applied": apply, "ran_at": _iso(at),
        "declined_due": len(due), "sent": sent, "recommended": recommended,
        "stopped": stopped, "suppressed": suppressed,
        "due_service_candidates": len(due_sample), "due_service_cards_written": due_cards,
        "sample": {"reminders": reminders[:10], "due_service": due_sample[:10]},
    }
