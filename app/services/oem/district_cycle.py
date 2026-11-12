"""A6 — weekly district cycle (agentic job, §4.10.2).

  run_district_cycle(market)   A6 weekly (Mon 05:00): refresh composites, detect
      alerts, draft action plans for red dealers — all as drafts (act_with_approval).

The A5 nightly attribution job (`run_nightly_attribution`) lives in
`oem.campaigns` with the rest of A5 (ADR 0009) and is re-exported here so
existing imports keep working.

Both jobs write only recommendation/draft rows; humans keep budget + publishing.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from ..common import ddb
from . import planner, account_plan
from .campaigns import run_nightly_attribution  # noqa: F401 (re-export)

_ACCOUNTS = "OEM_Accounts"
_RECS = "DEALER_Recommendations"
_HEALTH_LOW = 0.80


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_district_cycle(market: str = "us", district_id: str | None = None,
                       apply: bool = False, limit: int | None = None,
                       env_prefix: str | None = None) -> dict[str, Any]:
    """A6 weekly cycle: for each red dealer (health below threshold), draft an
    account plan; roll up composite gaps. All drafts (act_with_approval)."""
    accs = ddb.scan_filter(_ACCOUNTS, "market_id", market, env_prefix)
    if district_id:
        accs = [a for a in accs if a.get("district_id") == district_id]
    districts = sorted({a.get("district_id") for a in accs if a.get("district_id")})
    red = [a for a in accs if _f(a.get("sales_health_index")) < _HEALTH_LOW]
    if limit:
        red = red[:limit]

    drafted = []
    for a in red:
        if apply:
            try:
                plan = account_plan.draft_account_plan(a["account_id"], author="A6", env_prefix=env_prefix)
                drafted.append({"account_id": a["account_id"], "plan_id": plan.get("plan_id"),
                                "objectives": plan.get("objective_count")})
            except Exception as e:
                drafted.append({"account_id": a["account_id"], "error": str(e)[:80]})
        else:
            drafted.append({"account_id": a["account_id"], "would_draft": True})

    gaps = {d: planner.analyze_performance_gaps(d, market, env_prefix)["gaps"][:3] for d in districts}
    return {"job": "district-cycle", "market": market, "publish_level": "act_with_approval",
            "districts": districts, "red_dealers": len(red), "plans_drafted": drafted,
            "top_gaps_by_district": gaps, "applied": apply, "ran_at": _now(),
            "source_ref": "A6.run_district_cycle (weekly; drafts only)"}


def _f(v: Any, default: float = 1.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default
