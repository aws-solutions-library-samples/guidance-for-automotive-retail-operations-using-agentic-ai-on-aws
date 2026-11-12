"""L3/L5 — A6 OEM Workspace "Today" dashboard (spec §5.9).

The district manager's home: a district-pulse KPI grid, the HIGH/MEDIUM/LOW alert
triage list, recent field activity, and one green AI-insight card. Scoped to the
OEM user's org node — a district manager sees their district, a regional manager
their region's districts, national sees all.

  build_today(scope, override_node) -> KPIs + alerts + activity + AI insight.

KPIs from OEM_Accounts (days supply, health indices, dealers at risk); alerts
from OEM_Alerts by severity; activity from OEM_ContactReports / OEM_AccountPlans.
The AI insight is a Bedrock summary with the same always-answers fallback used by
the other agents.
"""
from __future__ import annotations

import os
from typing import Any

from ..common import authz, ddb
from ..platform import generate as _gen

BEDROCK_MODEL = os.environ.get("RETAIL_OPS_BEDROCK_MODEL", "us.anthropic.claude-opus-4-5-20251101-v1:0")
BEDROCK_REGION = os.environ.get("AWS_REGION", "us-east-1")


def build_today(scope: dict[str, Any], market_id: str | None = None,
                env_prefix: str | None = None) -> dict[str, Any]:
    """Assemble the Today dashboard for the OEM caller's org scope."""
    market = market_id or scope.get("market_id") or "us"
    node = scope.get("scope_node_id") or "OEM"
    districts = authz.district_ids_under(node, env_prefix)

    accounts = ddb.scan_filter("OEM_Accounts", "market_id", market, env_prefix)
    alerts = ddb.scan_filter("OEM_Alerts", "market_id", market, env_prefix)
    reports = ddb.scan_filter("OEM_ContactReports", "market_id", market, env_prefix)
    plans = ddb.scan_filter("OEM_AccountPlans", "market_id", market, env_prefix)

    # scope by district unless national (OEM root covers all districts)
    if districts:
        accounts = [a for a in accounts if a.get("district_id") in districts]
        alerts = [a for a in alerts if a.get("district_id") in districts]
        reports = [r for r in reports if r.get("district_id") in districts]
        plans = [p for p in plans if p.get("district_id") in districts]

    # one count and one risk rule with the Accounts list: dealer accounts, gap-based risk
    from . import workspace as _ws
    gaps = _ws.peer_gaps(accounts)
    dealers = [a for a in accounts if a.get("account_type", "dealer") == "dealer"]
    risky = [a for a in dealers if _ws._at_risk(a, gaps.get(a.get("account_id"), (None, None))[1])]
    kpis = _kpis(dealers, risky)
    by_sev = {"high": [], "medium": [], "low": []}
    for a in alerts:
        if a.get("status") == "open":
            by_sev.setdefault(a.get("severity", "low"), by_sev["low"]).append(_alert(a))

    result = {
        "scope_node": node,
        "market_id": market,
        "district_count": len(districts) or 1,
        "kpis": kpis,
        "alerts": {
            "high": by_sev["high"], "medium": by_sev["medium"], "low": by_sev["low"],
            "counts": {k: len(v) for k, v in by_sev.items()},
            "open_total": sum(len(v) for v in by_sev.values()),
        },
        "activity": {
            "contact_reports": len(reports),
            "action_plans": len([p for p in plans if p.get("status") != "archived"]),
            "recent_reports": [_report(r) for r in
                               sorted(reports, key=lambda r: r.get("visit_ts") or "", reverse=True)[:3]],
        },
        "dealers_at_risk": [
            {"dealer_id": a.get("dealer_id"), "name": a.get("name"),
             "condition": a.get("condition"), "sales_health_index": a.get("sales_health_index"),
             "target_gap": gaps.get(a.get("account_id"), (None, None))[1]}
            for a in risky
        ],
    }
    result["ai_insight"], result["insight_by"] = _insight(result)
    return result


# --- kpis / shaping ---------------------------------------------------------

def _kpis(accounts: list[dict[str, Any]], at_risk: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    at_risk = at_risk or []
    ds = [_num(a.get("days_supply")) for a in accounts if _num(a.get("days_supply")) is not None]
    sh = [_num(a.get("sales_health_index")) for a in accounts if _num(a.get("sales_health_index")) is not None]
    return {
        "dealers": len(accounts),
        "avg_days_supply": round(sum(ds) / len(ds), 1) if ds else None,
        "avg_sales_health": round(sum(sh) / len(sh), 2) if sh else None,
        "dealers_at_risk": len(at_risk),
    }


def _alert(a: dict[str, Any]) -> dict[str, Any]:
    return {k: a.get(k) for k in ("alert_id", "title", "kind", "severity", "dealer_id",
                                  "district_id", "opened_at", "source_ref")}


def _report(r: dict[str, Any]) -> dict[str, Any]:
    out = {k: r.get(k) for k in ("report_id", "dealer_id", "visit_type", "sentiment",
                                 "summary", "next_action", "visit_ts")}
    # topics / follow-ups when the report carries them (reports filed through the
    # workspace do; the seeded corpus doesn't)
    out["topics"] = r.get("topics") or []
    out["followups"] = r.get("followups") or []
    return out


def _insight(result: dict[str, Any]) -> tuple[str, str]:
    k = result["kpis"]
    templated = (
        f"{k['dealers_at_risk']} of {k['dealers']} dealers need attention; "
        f"avg days-supply {k['avg_days_supply']}, avg sales health {k['avg_sales_health']}. "
        f"{result['alerts']['counts']['high']} high-severity alerts open."
    )
    try:
        import boto3
        rt = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
        prompt = (
            "In two sentences, give an OEM district manager the top priority for "
            "today from this district pulse. Factual, no fabricated numbers.\n"
            f"kpis={k}, high_alerts={result['alerts']['high'][:3]}, "
            f"dealers_at_risk={result['dealers_at_risk'][:5]}"
        )
        resp = rt.converse(
            modelId=BEDROCK_MODEL,
            system=[{"text": "You are a concise OEM field-sales assistant. Do not invent "
                             "figures; use only the provided data. No pricing directives to dealers."}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 160, "temperature": 0.2},
            **_gen.guardrail_kwargs(),
        )
        text = resp["output"]["message"]["content"][0]["text"].strip()
        return (text or templated), ("bedrock" if text else "fallback")
    except Exception:
        return templated, "fallback"


def _num(v: Any) -> float | int | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
