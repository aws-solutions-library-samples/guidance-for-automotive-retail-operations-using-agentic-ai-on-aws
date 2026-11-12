"""A6 — OEM planner analytic tools (spec §3.6.1 / §5.9), plain-engineering.

The deterministic analytics behind the DM-Assistant views, grounded in the
seeded OEM tables (the Quick-hosted versions are the production upgrade path;
these run the same logic in Python so the views work today). Every number
carries a source_ref to the row it came from (acceptance #15/#15a).

  compute_composite(account_id)   -> dealer vs peer composite metrics
  analyze_performance_gaps(district) -> ranked gaps across the district
  get_ev_readiness(account_id)    -> EV certification/training/charger status
  get_compliance(account_id)      -> DND/facility/signage/training compliance
  predict_weekend(district)       -> weekend booked/delivered projection
  simulate_scenario(district, levers) -> 5 projected-impact cards + narrative
"""
from __future__ import annotations

from typing import Any

from ..common import ddb

_ACCOUNTS = "OEM_Accounts"
_COMPLIANCE = "OEM_Compliance"


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _accounts(market: str, district_id: str | None, env_prefix: str | None) -> list[dict[str, Any]]:
    # stores only: dealer groups, fleet and prospects are accounts but not peers of a store
    accs = [a for a in ddb.scan_filter(_ACCOUNTS, "market_id", market, env_prefix)
            if a.get("account_type", "dealer") == "dealer"]
    if district_id:
        accs = [a for a in accs if a.get("district_id") == district_id]
    return accs


def compute_composite(account_id: str, market: str = "us",
                      env_prefix: str | None = None) -> dict[str, Any]:
    """Dealer composite metrics vs its district peer group (spec §5.9)."""
    acct = ddb.get_item(_ACCOUNTS, {"account_id": account_id}, env_prefix)
    if acct is None:
        return {"account_id": account_id, "found": False}
    peers = _accounts(market, acct.get("district_id"), env_prefix)
    def peer_avg(field: str) -> float:
        vals = [_num(p.get(field)) for p in peers if p.get(field) is not None]
        return round(sum(vals) / len(vals), 3) if vals else 0.0
    metrics = {}
    for field in ("sales_health_index", "service_health_index", "days_supply"):
        metrics[field] = {"dealer": _num(acct.get(field)), "peer_avg": peer_avg(field),
                          "delta": round(_num(acct.get(field)) - peer_avg(field), 3)}
    return {"account_id": account_id, "name": acct.get("name"), "tier": acct.get("tier"),
            "peer_group_size": len(peers), "composite": metrics,
            "source_ref": "OEM_Accounts (this store against its district's peers)"}


def analyze_performance_gaps(district_id: str, market: str = "us",
                             env_prefix: str | None = None) -> dict[str, Any]:
    """Rank dealers in a district by their largest gap below the peer average."""
    accs = _accounts(market, district_id, env_prefix)
    if not accs:
        return {"district_id": district_id, "gaps": []}
    avg_sales = sum(_num(a.get("sales_health_index")) for a in accs) / len(accs)
    gaps = []
    for a in accs:
        gap = round(avg_sales - _num(a.get("sales_health_index")), 3)
        if gap > 0:
            gaps.append({"account_id": a.get("account_id"), "name": a.get("name"),
                         "sales_health_index": _num(a.get("sales_health_index")),
                         "gap_below_peer_avg": gap})
    gaps.sort(key=lambda g: g["gap_below_peer_avg"], reverse=True)
    return {"district_id": district_id, "peer_avg_sales_health": round(avg_sales, 3),
            "gaps": gaps, "source_ref": f"OEM_Accounts district {district_id}"}


def get_ev_readiness(account_id: str, env_prefix: str | None = None) -> dict[str, Any]:
    """EV readiness for an account, derived from its compliance rows (EV area)."""
    acct = ddb.get_item(_ACCOUNTS, {"account_id": account_id}, env_prefix)
    if acct is None:
        return {"account_id": account_id, "found": False}
    comps = [c for c in ddb.scan_filter(_COMPLIANCE, "dealer_id", acct.get("dealer_id"), env_prefix)]
    ev = [c for c in comps if "ev" in str(c.get("area", "")).lower()
          or "charg" in str(c.get("area", "")).lower() or "train" in str(c.get("area", "")).lower()]
    certified = any(str(c.get("status")).lower() in ("compliant", "fully") for c in ev)
    return {"account_id": account_id, "dealer_id": acct.get("dealer_id"),
            "ev_certified": certified,
            "ev_areas": [{"area": c.get("area"), "status": c.get("status")} for c in ev],
            "source_ref": f"OEM_Compliance dealer {acct.get('dealer_id')} (EV areas)"}


def get_compliance(account_id: str, env_prefix: str | None = None) -> dict[str, Any]:
    """Franchise/facility/signage/training compliance for an account (spec §5.9)."""
    acct = ddb.get_item(_ACCOUNTS, {"account_id": account_id}, env_prefix)
    if acct is None:
        return {"account_id": account_id, "found": False}
    comps = ddb.scan_filter(_COMPLIANCE, "dealer_id", acct.get("dealer_id"), env_prefix)
    by_area = [{"area": c.get("area"), "status": c.get("status"),
                "last_reviewed_at": c.get("last_reviewed_at")} for c in comps]
    noncompliant = [c for c in by_area if str(c["status"]).lower() in ("non-compliant", "non_compliant", "conditional")]
    overall = "non-compliant" if any(str(c["status"]).lower().startswith("non") for c in by_area) \
        else ("conditional" if noncompliant else "fully")
    return {"account_id": account_id, "dealer_id": acct.get("dealer_id"),
            "overall": overall, "by_area": by_area, "needs_action": noncompliant,
            "source_ref": f"OEM_Compliance dealer {acct.get('dealer_id')}"}


def predict_weekend(district_id: str, market: str = "us",
                    env_prefix: str | None = None) -> dict[str, Any]:
    """Weekend booked/delivered projection per dealer in a district. Grounds on
    each account's sales-health and days-supply as the deterministic model
    inputs (labelled an estimate — no fabricated forecast API)."""
    accs = _accounts(market, district_id, env_prefix)
    projections = []
    for a in accs:
        health = _num(a.get("sales_health_index"), 0.8)
        # simple transparent model: healthier + tighter supply -> more weekend units
        base = 8.0
        ds = _num(a.get("days_supply"), 60)
        projected = round(base * health * (60.0 / max(ds, 20.0)), 1)
        projections.append({"account_id": a.get("account_id"), "name": a.get("name"),
                            "projected_weekend_units": projected,
                            "inputs": {"sales_health_index": health, "days_supply": ds}})
    projections.sort(key=lambda p: p["projected_weekend_units"], reverse=True)
    return {"district_id": district_id, "estimate": True, "projections": projections,
            "source_ref": f"deterministic model over OEM_Accounts district {district_id}"}


def simulate_scenario(district_id: str, levers: dict[str, Any] | None = None,
                      market: str = "us", env_prefix: str | None = None) -> dict[str, Any]:
    """Scenario Planning: 4 levers -> 5 projected-impact cards (spec §5.9).

    Levers: allocation_pct (-50..+50), incentive_per_unit (-500..+2000),
    days_supply_delta (±15), market_growth_pct (-5..+10). Deterministic model;
    every card is labelled an estimate."""
    lv = levers or {}
    alloc = _num(lv.get("allocation_pct"), 0) / 100.0
    incentive = _num(lv.get("incentive_per_unit"), 0)
    ds_delta = _num(lv.get("days_supply_delta"), 0)
    growth = _num(lv.get("market_growth_pct"), 0) / 100.0

    accs = _accounts(market, district_id, env_prefix)
    base_units = sum(_num(a.get("sales_health_index"), 0.8) * 100 for a in accs)
    base_ds = (sum(_num(a.get("days_supply"), 60) for a in accs) / len(accs)) if accs else 60
    # The objective baseline is the district's objective attainment as the Today view
    # reports it (oem/today.py `avg_sales_health`, the "Objective %" tile): the mean
    # sales-health index of the district's accounts — not a constant — so both screens
    # open on the same number and the levers move it from there.
    health = [_num(a.get("sales_health_index")) for a in accs if a.get("sales_health_index") is not None]
    base_objective = round(sum(health) / len(health), 2) if health else 0.9

    # transparent elasticity model
    volume = base_units * (1 + alloc * 0.3 + growth + (incentive / 2000.0) * 0.15)
    objective_pct = min(1.2, base_objective * (1 + alloc * 0.2 + (incentive / 2000.0) * 0.1))
    # share follows OUR units against the district market: baseline share 12% implies a market of
    # base_units / 0.12; market growth grows that denominator too. Extra units the levers add move the
    # share — "+88 units, share +0.0%" came from a share formula that ignored the volume card.
    market_units = (base_units / 0.12) * (1 + growth) if base_units else 0
    share = (volume / market_units) if market_units else 0.12
    gross = (2500 - incentive * 0.4)
    days_supply = max(20, base_ds + ds_delta - alloc * 10)

    def card(name, base, proj, unit=""):
        return {"metric": name, "baseline": round(base, 2), "projected": round(proj, 2),
                "delta": round(proj - base, 2), "unit": unit, "estimate": True}
    return {"district_id": district_id, "levers": {"allocation_pct": lv.get("allocation_pct", 0),
            "incentive_per_unit": lv.get("incentive_per_unit", 0),
            "days_supply_delta": lv.get("days_supply_delta", 0),
            "market_growth_pct": lv.get("market_growth_pct", 0)},
            "projections": [
                card("volume", base_units, volume, "units"),
                card("objective_pct", base_objective, objective_pct, "%"),
                card("market_share", 0.12, share, "%"),
                card("gross_per_unit", 2500, gross, "$"),
                card("days_supply", base_ds, days_supply, "days"),
            ],
            "source_ref": f"deterministic scenario model over OEM_Accounts district {district_id}"}
