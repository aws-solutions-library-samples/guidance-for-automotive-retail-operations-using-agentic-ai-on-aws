"""A7 — schedule_declined_followup (service -> nurture loop), spec §3.7.

When a customer declines estimate lines, A7 records the declined work and hands
it to the A2 nurture engine on a SERVICE cadence (30/60/90-day declined
reminders) — never a sales message. This is the service-lane equivalent of the
purchase nurture: the same NurturePlan machinery, but family=service and a
declined-reminder template.

  schedule_declined_followup(sj_id, declined_lines?, cadence_days?) ->
      {declined_recorded[], nurture_plan, preview}

Grounding: declined lines come from the estimate/inspection (or are passed
explicitly); the reminder re-presents the ORIGINAL finding, and the message is
rendered from the service declined_reminder template (no sales content). The
NurturePlan is written for the customer with stop-conditions incl. work_booked.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from ..common import ddb
from ..events import bus as _bus
from ..journeys import nurture

_SJ = "DEALER_ServiceJourneys"
_DECLINED = "DEALER_DeclinedWork"
_ESTIMATES = "DEALER_ServiceEstimates"
_INSPECTIONS = "DEALER_Inspections"
_PLANS = "DEALER_NurturePlans"

# Declined-reminder cadence -> which service template trigger to use.
_REMINDER_TEMPLATE = {30: "declined_reminder_30d", 60: "declined_reminder_60d",
                      90: "declined_reminder_90d"}


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(dt: _dt.datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


class NotFound(Exception):
    pass


def _legacy_items(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Items of a declined-work row; seed rows hold a single item at top level."""
    if isinstance(row.get("items"), list) and row["items"] and all(isinstance(i, dict) for i in row["items"]) \
            and any(i.get("declined_item_id") for i in row["items"]):
        return [dict(i) for i in row["items"]]
    if row.get("item") or row.get("items"):
        base = {k: row.get(k) for k in ("item", "amount", "currency", "service_journey_id", "estimate_id",
                                        "declined_at", "vin", "dealer_id")}
        legacy = [dict(i) for i in (row.get("items") or []) if isinstance(i, dict)] or [base]
        out = []
        for n, it in enumerate(legacy):
            merged = {**base, **it}
            merged.setdefault("declined_item_id", f"{row.get('declined_id') or 'DW-' + str(row.get('customer_id'))}"
                                                  f"{'' if n == 0 else f'-{n}'}")
            merged.setdefault("status", row.get("status") or "open")
            merged.setdefault("follow_up_cadence_days", row.get("follow_up_cadence_days") or 30)
            out.append(merged)
        return out
    return []


def declined_items(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [{**i, "customer_id": row.get("customer_id")} for i in _legacy_items(row)]


def add_declined_items(customer_id: str | None, items: list[dict[str, Any]],
                       env_prefix: str | None = None) -> dict[str, Any]:
    """Merge declined items into the customer's DEALER_DeclinedWork row (upsert by declined_item_id)."""
    if not customer_id:
        raise NotFound("declined work needs a customer")
    row = dict(ddb.get_item(_DECLINED, {"customer_id": customer_id}, env_prefix) or {"customer_id": customer_id})
    current = {i["declined_item_id"]: i for i in _legacy_items(row)}
    for it in items:
        current[it["declined_item_id"]] = {**current.get(it["declined_item_id"], {}), **it}
    merged = list(current.values())
    row["items"] = merged
    latest = max(merged, key=lambda i: i.get("declined_at") or "") if merged else {}
    # keep the legacy single-item fields pointing at the latest entry
    for k in ("item", "amount", "currency", "service_journey_id", "estimate_id", "declined_at", "dealer_id", "vin"):
        row[k] = latest.get(k)
    row["declined_id"] = latest.get("declined_item_id")
    row["updated_at"] = _iso(_now())
    row["source_ref"] = "A7 declined-work register"
    ddb.put_item(_DECLINED, row, env_prefix)
    return row


def schedule_declined_followup(sj_id: str, declined_lines: list[dict[str, Any]] | None = None,
                               cadence_days: int = 30, actor: str = "A7",
                               env_prefix: str | None = None) -> dict[str, Any]:
    """Record declined work and schedule a SERVICE-family nurture followup (§3.7).

    declined_lines: optional [{item, amount, currency}]; if omitted, derives from
    the journey's yellow/red inspection findings that weren't approved.
    cadence_days: 30 | 60 | 90 — picks the declined-reminder template + next touch."""
    sj = ddb.get_item(_SJ, {"service_journey_id": sj_id}, env_prefix)
    if sj is None:
        raise NotFound(f"service journey {sj_id} not found")
    customer_id = sj.get("customer_id")
    market = sj.get("market_id") or "us"

    # derive declined lines from actionable findings if not supplied
    if not declined_lines:
        findings = ddb.scan_filter(_INSPECTIONS, "service_journey_id", sj_id, env_prefix)
        declined_lines = [{"item": f.get("item"), "amount": None,
                           "currency": ("EUR" if market == "de" else "USD")}
                          for f in findings if f.get("status") in ("red", "yellow")]

    ts = _now()
    ests = ddb.scan_filter(_ESTIMATES, "service_journey_id", sj_id, env_prefix)
    est_id = ests[0].get("estimate_id") if ests else None

    # record declined work — DEALER_DeclinedWork is keyed by customer_id (one row
    # per customer), so each declined line is an entry in the row's `items`.
    items = []
    for i, ln in enumerate(declined_lines):
        items.append({**ln, "declined_item_id": ln.get("declined_item_id") or f"DW-{ln.get('line_id') or f'{sj_id}-{i:02d}'}",
                      "service_journey_id": sj_id, "estimate_id": est_id, "vin": sj.get("vin"),
                      "dealer_id": sj.get("dealer_id"), "declined_at": ln.get("declined_at") or _iso(ts),
                      "status": "open", "follow_up_cadence_days": cadence_days,
                      "currency": ln.get("currency") or ("EUR" if market == "de" else "USD")})
    declined_row = add_declined_items(customer_id, items, env_prefix)
    declined_row["declined_id"] = items[0]["declined_item_id"] if items else declined_row.get("declined_id")

    # compose the reminder preview from the SERVICE template (no sales content)
    trigger = _REMINDER_TEMPLATE.get(cadence_days, "declined_reminder_30d")
    consent = ddb.get_item("CUST_Consent", {"customer_id": customer_id}, env_prefix) or {}
    channel = consent.get("preferred_channel") or "email"
    if channel == "chat":
        channel = "email"                 # declined reminders go email/sms, not chat
    # build a lightweight journey-like context for the renderer (service family)
    render_ctx = {"journey_id": sj_id, "customer_id": customer_id, "market_id": market,
                  "dealer_id": sj.get("dealer_id"), "owner_user_id": sj.get("advisor_id"),
                  "primary_model_id": None, "stage": "OWN", "context": {}}
    try:
        preview = nurture.compose_message(render_ctx, channel=channel, intent=trigger,
                                          family="service", env_prefix=env_prefix)
    except nurture.NotFound as e:
        preview = {"error": str(e)}

    # write the NurturePlan (service family, declined cadence)
    plan_id = f"NP-DECLINED-{sj_id}"
    next_touch = _iso(ts + _dt.timedelta(days=cadence_days))
    plan = {
        "nurture_plan_id": plan_id,
        "journey_id": sj.get("purchase_journey_id"),   # link to the owner's purchase journey if any
        "service_journey_id": sj_id,
        "customer_id": customer_id,
        "goal": "recover_declined_work",
        "family": "service",
        "template_trigger": trigger,
        "cadence": f"every_{cadence_days}_days",
        "channel": channel,
        "next_touch_at": next_touch,
        "stop_conditions": ["opt_out", "work_booked", "converted"],
        "active": True,
        "declined_items": declined_lines,
        "updated_at": _iso(ts),
        "updated_by": actor,
        "source_ref": "A7.schedule_declined_followup -> A2 nurture (service templates)",
    }
    ddb.put_item(_PLANS, plan, env_prefix)

    _bus.emit("service.declined_work.recorded", {
        "service_journey_id": sj_id, "customer_id": customer_id,
        "declined_id": declined_row["declined_id"], "lines": len(declined_lines),
        "nurture_plan_id": plan_id}, actor=actor,
        resource_url=f"/r/service_journey/{sj_id}", env_prefix=env_prefix)
    return {
        "service_journey_id": sj_id,
        "customer_id": customer_id,
        "declined_recorded": declined_lines,
        "declined_id": declined_row["declined_id"],
        "nurture_plan": {"nurture_plan_id": plan_id, "family": "service",
                         "template_trigger": trigger, "cadence": plan["cadence"],
                         "channel": channel, "next_touch_at": next_touch,
                         "stop_conditions": plan["stop_conditions"]},
        "preview": {"channel": preview.get("channel"), "subject": preview.get("subject"),
                    "body": preview.get("body"), "template_id": preview.get("template_id"),
                    "family": preview.get("family")} if "error" not in preview else preview,
        "source_ref": "A7.schedule_declined_followup",
    }
