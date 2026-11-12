"""A6 — draft_account_plan (spec §5.9 / GR-001..GR-008).

Drafts an OEM account plan for a dealer account from the account's real health
signals (sales/service health index, days-supply, tier), the open alerts on its
district, and active CI programs. Produces grounded objectives — each tied to a
concrete metric gap — and writes a DRAFT OEM_AccountPlans row that the §8 R2
approval workflow (submit -> Step Functions -> decide) then publishes.

  draft_account_plan(account_id, author) -> the draft plan (status=draft)

Guardrails honored (A6 GR-001..GR-008, spec §3.6.1): A6 NEVER auto-publishes,
never contacts a dealer, and never changes an allocation on its own. This drafts
only (status=draft, publish_level=recommend); a human submits/approves. Every
objective cites the metric that motivated it; nothing is invented.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from ..common import ddb

_ACCOUNTS = "OEM_Accounts"
_PLANS = "OEM_AccountPlans"
_ALERTS = "OEM_Alerts"
_CIPROGRAMS = "OEM_CIPrograms"

# health-index thresholds that trigger an objective (transparent, tunable)
_HEALTH_LOW = 0.80


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


class NotFound(Exception):
    pass


def draft_account_plan(account_id: str, author: str = "A6",
                       env_prefix: str | None = None) -> dict[str, Any]:
    """Draft an account plan for a dealer account (spec §5.9).

    Grounds each objective in a real signal: a low sales/service health index,
    an above-target days-supply, or an open district alert. Writes a DRAFT plan
    (never published — GR-001); a human runs it through the approval workflow."""
    acct = ddb.get_item(_ACCOUNTS, {"account_id": account_id}, env_prefix)
    if acct is None:
        raise NotFound(f"account {account_id} not found")

    dealer_id = acct.get("dealer_id")
    district_id = acct.get("district_id")
    market = acct.get("market_id") or "us"

    objectives: list[dict[str, Any]] = []

    sales_h = _num(acct.get("sales_health_index"))
    if sales_h is not None and sales_h < _HEALTH_LOW:
        objectives.append({
            "kpi": "sales_health_index", "current": sales_h, "target": round(_HEALTH_LOW + 0.05, 2),
            "action": "Sales process coaching + lead-response SLA focus",
            "why": f"sales health {sales_h} below {_HEALTH_LOW} threshold"})

    svc_h = _num(acct.get("service_health_index"))
    if svc_h is not None and svc_h < _HEALTH_LOW:
        objectives.append({
            "kpi": "service_health_index", "current": svc_h, "target": round(_HEALTH_LOW + 0.05, 2),
            "action": "Fixed-ops retention program (service-lane upsell + CSI)",
            "why": f"service health {svc_h} below {_HEALTH_LOW} threshold"})

    ds = _num(acct.get("days_supply"))
    if ds is not None and ds > 90:
        objectives.append({
            "kpi": "days_supply", "current": ds, "target": 60,
            "action": "Aged-inventory turn plan (merchandising + incentive alignment)",
            "why": f"days-supply {ds} above 90-day target"})

    # open district alerts that motivate objectives
    alerts = [a for a in ddb.scan_filter(_ALERTS, "market_id", market, env_prefix)
              if a.get("district_id") == district_id and a.get("status") != "resolved"
              and (a.get("dealer_id") in (dealer_id, None))]
    alert_kinds = sorted({a.get("kind") for a in alerts if a.get("kind")})
    for kind in alert_kinds:
        objectives.append({
            "kpi": f"alert:{kind}", "current": "open", "target": "resolved",
            "action": f"Address open {kind} alert(s)",
            "why": f"open district alert: {kind}"})

    # existing CI programs to align with (not duplicate)
    programs = [p for p in ddb.scan_filter(_CIPROGRAMS, "market_id", market, env_prefix)
                if p.get("district_id") == district_id and p.get("status") == "active"]

    ts = _now()
    plan_id = f"BRIEF-{market.upper()}-DRAFT-{account_id.split('-')[-1]}"
    # the draft is the next version of this dealer's plan, not another "v1"
    others = [p for p in (ddb.scan_filter(_PLANS, "dealer_id", dealer_id, env_prefix) if dealer_id else [])
              if p.get("plan_id") != plan_id]
    next_version = max((int(_num(p.get("version")) or 0) for p in others), default=0) + 1
    plan = {
        "plan_id": plan_id,
        "account_id": account_id,
        "dealer_id": dealer_id, "district_id": district_id, "market_id": market,
        "author_user_id": author,
        "status": "draft",                      # GR-001: never auto-published
        "approval_status": None,
        "publish_level": "recommend",           # a human decides to publish
        "version": next_version,
        "objectives": objectives,
        "allocations": [],                      # GR: A6 never changes allocations
        "aligned_programs": [p.get("program_id") for p in programs],
        "effective_from": None,
        "publish_action": None,
        "created_at": ts, "updated_at": ts,
        "source_ref": f"A6.draft_account_plan:{account_id} (health/alerts/CI grounded)",
    }
    ddb.put_item(_PLANS, plan, env_prefix)

    return {
        "plan_id": plan_id,
        "account_id": account_id,
        "account_name": acct.get("name"),
        "version": next_version, "created_at": ts, "updated_at": ts,
        "status": "draft",
        "publish_level": "recommend",
        "objectives": objectives,
        "objective_count": len(objectives),
        "aligned_programs": plan["aligned_programs"],
        "guardrail_note": "Draft only — the Field Sales Agent never auto-publishes, contacts a dealer, "
                          "or changes allocations (GR-001..GR-008). Submit via the "
                          "approval workflow to publish.",
        "source_ref": plan["source_ref"],
    }
