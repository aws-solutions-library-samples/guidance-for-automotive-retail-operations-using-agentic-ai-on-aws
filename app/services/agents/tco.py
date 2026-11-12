"""A1 — build_tco_comparison: EV vs ICE N-year cost of ownership (spec §3.1, §5.3).

Feeds the Advisor's EV-vs-ICE compare-table, the "why go electric" cards, and
the savings banner. Every figure is computed from catalog rows + MarketConfig
(+ A1 price_build for tax and point-of-sale incentives) and returned with its
inputs and formula (acceptance #4). Nothing is market-literal: units, prices,
and the energy/fuel defaults come from OEM_Markets.

  build_tco_comparison(journey_id, years=5)            -> pick an EV + ICE pair
  compare_trims(ev_trim_id, ice_trim_id, market, ...)  -> explicit pair
"""
from __future__ import annotations

from typing import Any

from ..common import ddb, market as mkt
from . import discovery

_EV = ("EV", "BEV")


class NotFound(Exception):
    pass


def _trims(market_id: str, env_prefix: str | None) -> list[dict[str, Any]]:
    cur = "EUR" if market_id == "de" else "USD"
    return [t for t in ddb.scan_all("OEM_Catalog", env_prefix)
            if t.get("kind") is None and t.get("powertrain") and t.get("currency") == cur
            and t.get("efficiency")]


def _models(market_id: str, env_prefix: str | None) -> dict[str, dict[str, Any]]:
    return {m["model_id"]: m for m in ddb.scan_filter("OEM_CatalogModels", "market_id", market_id, env_prefix)}


def _family(model: dict[str, Any] | None) -> str:
    b = ((model or {}).get("body") or "").lower()
    return "suv" if ("suv" in b or "crossover" in b) else "pickup" if "pickup" in b else "car"


def _size(model: dict[str, Any] | None) -> str:
    """Vehicle size class from body + segment (works for both seed markets)."""
    txt = f"{(model or {}).get('body') or ''} {(model or {}).get('segment') or ''}".lower()
    if "3-row" in txt:
        return "large"
    if any(w in txt for w in ("compact", "small", "kompakt")):
        return "compact"
    if any(w in txt for w in ("mid", "standard", "mittel")):
        return "mid"
    return "other"


def _pick_pair(journey: dict[str, Any], customer: dict[str, Any] | None,
               market_id: str, env_prefix: str | None) -> tuple[dict, dict, str]:
    """EV + ICE trims to compare: anchor on the customer's latest saved build
    (else the journey's primary model), then take the other powertrain's trim
    closest in price within the same body family when possible."""
    trims = _trims(market_id, env_prefix)
    if not trims:
        raise NotFound(f"no catalog trims for market {market_id}")
    by_id = {t["trim_id"]: t for t in trims}
    cfgs = sorted(ddb.scan_filter("PLAT_Configurations", "journey_id", journey["journey_id"], env_prefix),
                  key=lambda c: c.get("saved_at") or "", reverse=True)
    anchor = next((by_id[c["trim_id"]] for c in cfgs if c.get("trim_id") in by_id), None)
    why = "latest saved build"
    if anchor is None and journey.get("primary_model_id"):
        anchor = next((t for t in trims if t["model_id"] == journey["primary_model_id"]), None)
        why = "journey primary model"
    if anchor is None:
        budget = (customer or {}).get("budget") or {}
        target = float(budget.get("price_max") or 0) or sorted(float(t["list_price"]) for t in trims)[len(trims) // 2]
        anchor = min(trims, key=lambda t: abs(float(t["list_price"]) - target))
        why = "closest to the customer's budget"
    models = _models(market_id, env_prefix)
    want_ev = (anchor.get("powertrain") or "").upper() not in _EV
    others = [t for t in trims if ((t.get("powertrain") or "").upper() in _EV) == want_ev]
    if not others:
        raise NotFound("catalog has no trim of the opposite powertrain to compare")
    a_size, a_family = _size(models.get(anchor["model_id"])), _family(models.get(anchor["model_id"]))

    def score(t: dict) -> tuple:
        m = models.get(t["model_id"])
        return (0 if _size(m) == a_size else 1, 0 if _family(m) == a_family else 1,
                abs(float(t["list_price"]) - float(anchor["list_price"])))
    other = min(others, key=score)
    ev, ice = (other, anchor) if want_ev else (anchor, other)
    return ev, ice, why


def compare_trims(ev: dict[str, Any], ice: dict[str, Any], market_id: str, state: str | None = None,
                  years: int = 5, annual_distance: float | None = None,
                  env_prefix: str | None = None) -> dict[str, Any]:
    cfg = mkt.MarketConfig.load(market_id, env_prefix)
    priced = {}
    vehicles = []
    for side, t in (("ev", ev), ("ice", ice)):
        pb = discovery.price_build(t["list_price"], t.get("powertrain") or "", market_id, state, 0, env_prefix)
        priced[side] = pb
        vehicles.append({"vehicle_id": t["trim_id"], "price": pb["total_out_the_door"] + pb["incentives_total"],
                         "incentives": pb["incentives_total"], "efficiency": t["efficiency"],
                         "powertrain": "ev" if side == "ev" else "ice", "source_ref": t.get("source_ref")})
    out = mkt.tco(cfg, vehicles, years=years, annual_distance=annual_distance)
    e, i = out["vehicles"]
    e_energy, i_energy = e["inputs"]["energy"], i["inputs"]["energy"]
    savings_total = round(i["total"] - e["total"], 2)
    savings_year = round(i_energy["annual_cost"] - e_energy["annual_cost"], 2)

    def row(key: str, label: str, kind: str, ev_v: Any, ice_v: Any, lower_wins: bool = True,
            ev_unit: str | None = None, ice_unit: str | None = None) -> dict[str, Any]:
        winner = None
        if isinstance(ev_v, (int, float)) and isinstance(ice_v, (int, float)) and ev_v != ice_v:
            winner = ("ev" if ev_v < ice_v else "ice") if lower_wins else ("ev" if ev_v > ice_v else "ice")
        return {"key": key, "label": label, "kind": kind, "ev": ev_v, "ice": ice_v, "winner": winner,
                "ev_unit": ev_unit, "ice_unit": ice_unit}

    region_tax = next((ln for ln in priced["ice"]["tax"]), {})
    rows = [
        row("price", "List price", "money", float(ev["list_price"]), float(ice["list_price"])),
        row("incentives", "Point-of-sale incentives", "money", priced["ev"]["incentives_total"],
            priced["ice"]["incentives_total"], lower_wins=False),
        row("tax", "Tax" if region_tax.get("kind") != "vat_included" else "VAT (included)", "money",
            sum(x.get("amount") or 0 for x in priced["ev"]["tax"]),
            sum(x.get("amount") or 0 for x in priced["ice"]["tax"])),
        row("efficiency", "Efficiency", "efficiency", float(ev["efficiency"]), float(ice["efficiency"]),
            lower_wins="/100" in cfg.efficiency_unit_ice, ev_unit="ev", ice_unit="ice"),
        row("energy_year", "Annual fuel / energy", "money", e_energy["annual_cost"], i_energy["annual_cost"]),
        row("per_distance", f"Cost per {cfg.distance_unit}", "money_precise",
            round(e_energy["annual_cost"] / e_energy["annual_distance"], 3),
            round(i_energy["annual_cost"] / i_energy["annual_distance"], 3)),
        row("range", "Range", "distance", ev.get("ev_range"), ice.get("ev_range"), lower_wins=False),
        row("tco", f"{years}-year cost of ownership", "money", e["total"], i["total"]),
    ]
    benefits = []
    if savings_year > 0:
        benefits.append({"key": "energy", "title": "Lower running cost", "amount": savings_year,
                         "detail": "per year on energy vs fuel at market defaults"})
    if priced["ev"]["incentives_total"] > 0:
        benefits.append({"key": "incentives", "title": "Incentives at purchase",
                         "amount": priced["ev"]["incentives_total"],
                         "detail": "point-of-sale incentives that apply in this region",
                         "incentives": [a for a in priced["ev"]["incentives_applied"] if a.get("reduces_price")]})
    benefits.append({"key": "home_charging", "title": "Fuel up at home", "amount": e_energy["unit_price"],
                     "amount_kind": "per_kwh", "detail": "per kWh home-charging rate (market default, editable)"})
    return {
        "market_id": cfg.market_id, "currency": cfg.currency, "years": years,
        "ev": {"trim_id": ev["trim_id"], "model_id": ev["model_id"], "trim_name": ev.get("trim_name"),
               "powertrain": ev.get("powertrain"), "source_ref": ev.get("source_ref")},
        "ice": {"trim_id": ice["trim_id"], "model_id": ice["model_id"], "trim_name": ice.get("trim_name"),
                "powertrain": ice.get("powertrain"), "source_ref": ice.get("source_ref")},
        "rows": rows,
        "savings": {"total": savings_total, "per_year": savings_year, "winner": "ev" if savings_total > 0 else "ice"},
        "benefits": benefits,
        "assumptions": {"annual_distance": e_energy["annual_distance"], "distance_unit": cfg.distance_unit,
                        "energy_price": e_energy["unit_price"], "fuel_price": i_energy["unit_price"],
                        "state": state, "editable": True},
        "audit": {"tco": out, "price_build": priced},
        "source_ref": f"A1.build_tco_comparison (OEM_Catalog + OEM_Markets#{cfg.market_id} + price_build)",
    }


def build_tco_comparison(journey_id: str, years: int = 5, annual_distance: float | None = None,
                         env_prefix: str | None = None) -> dict[str, Any]:
    journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id}, env_prefix)
    if journey is None:
        raise NotFound(f"journey {journey_id} not found")
    customer = ddb.get_item("DEALER_Customers", {"customer_id": journey.get("customer_id")}, env_prefix) \
        if journey.get("customer_id") else None
    market_id = journey.get("market_id") or (customer or {}).get("market_id")
    state = ((customer or {}).get("address") or {}).get("state") or (customer or {}).get("region_code")
    ev, ice, why = _pick_pair(journey, customer, market_id, env_prefix)
    out = compare_trims(ev, ice, market_id, state, years, annual_distance, env_prefix)
    out.update({"journey_id": journey_id, "pair_basis": why})
    return out
