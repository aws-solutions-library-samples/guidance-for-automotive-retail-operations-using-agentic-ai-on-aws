"""§2.5 — Market parameterization: formatters and TCO driven by `MarketConfig`.

No market literal (currency symbol, unit, grouping, tax rule) may appear in
agent or UI code; it comes from the `OEM_Markets` row for the journey's
`market_id`. This module is the Python side of that rule (the UI side is
`app/console/src/lib/market.ts`):

  MarketConfig.load(market_id)      -> the OEM_Markets row (refuses unknown ids)
  fmt_money(cfg, 32450)             -> "$32,450" (us) | "32.450 €" (de)
  fmt_distance(cfg, 13500)          -> "13,500 mi" | "13.500 km"
  fmt_efficiency(cfg, 31, "ice")    -> "31 mpg" | "6,4 l/100km"
  tco(cfg, vehicles, years=5)       -> per-vehicle 5-year cost of ownership with
                                       inputs + formula + result (audit, AR-2)

Every function takes the config explicitly and raises without one, so a caller
cannot silently fall back to a hard-coded market.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import ddb

# kWh per US gallon-equivalent (EPA MPGe definition).
_KWH_PER_GALLON_E = 33.705

# Number grouping/decimal separators and currency placement per locale.
_LOCALES: dict[str, dict[str, Any]] = {
    "en-US": {"group": ",", "decimal": ".", "currency_after": False, "space": ""},
    "de-DE": {"group": ".", "decimal": ",", "currency_after": True, "space": " "},
}


class MissingMarketConfig(ValueError):
    pass


@dataclass(frozen=True)
class MarketConfig:
    market_id: str
    locale: str
    currency: str
    currency_symbol: str
    distance_unit: str
    efficiency_unit_ice: str
    efficiency_unit_ev: str
    fuel_price_default: float
    energy_price_default: float
    annual_distance_default: float
    list_price_includes_tax: bool = False
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "MarketConfig":
        missing = [k for k in ("market_id", "locale", "currency", "currency_symbol", "distance_unit",
                               "efficiency_unit_ice", "efficiency_unit_ev", "fuel_price_default",
                               "energy_price_default", "annual_distance_default") if row.get(k) is None]
        if missing:
            raise MissingMarketConfig(f"OEM_Markets row {row.get('market_id')!r} missing {missing}")
        if row["locale"] not in _LOCALES:
            raise MissingMarketConfig(f"no number format for locale {row['locale']!r}")
        return cls(
            market_id=row["market_id"], locale=row["locale"], currency=row["currency"],
            currency_symbol=row["currency_symbol"], distance_unit=row["distance_unit"],
            efficiency_unit_ice=row["efficiency_unit_ice"], efficiency_unit_ev=row["efficiency_unit_ev"],
            fuel_price_default=float(row["fuel_price_default"]),
            energy_price_default=float(row["energy_price_default"]),
            annual_distance_default=float(row["annual_distance_default"]),
            list_price_includes_tax=bool(row.get("list_price_includes_tax")), raw=row)

    @classmethod
    def load(cls, market_id: str | None, env_prefix: str | None = None) -> "MarketConfig":
        if not market_id:
            raise MissingMarketConfig("market_id is required (no default market in agent code)")
        row = ddb.get_item("OEM_Markets", {"market_id": market_id}, env_prefix)
        if row is None:
            raise MissingMarketConfig(f"unknown market_id {market_id!r}")
        return cls.from_row(row)


def _require(cfg: MarketConfig | None) -> MarketConfig:
    if not isinstance(cfg, MarketConfig):
        raise MissingMarketConfig("a MarketConfig is required")
    return cfg


def fmt_number(cfg: MarketConfig, value: float, decimals: int = 0) -> str:
    loc = _LOCALES[_require(cfg).locale]
    neg = value < 0
    s = f"{abs(value):,.{decimals}f}"            # 32,450.50 (en grouping)
    whole, _, frac = s.partition(".")
    whole = whole.replace(",", loc["group"])
    out = whole + (loc["decimal"] + frac if frac else "")
    return ("-" if neg else "") + out


def fmt_money(cfg: MarketConfig, value: float, decimals: int = 0) -> str:
    loc = _LOCALES[_require(cfg).locale]
    num = fmt_number(cfg, value, decimals)
    if loc["currency_after"]:
        return f"{num}{loc['space']}{cfg.currency_symbol}"
    sign, digits = ("-", num[1:]) if num.startswith("-") else ("", num)
    return f"{sign}{cfg.currency_symbol}{digits}"


def fmt_distance(cfg: MarketConfig, value: float, decimals: int = 0) -> str:
    return f"{fmt_number(_require(cfg), value, decimals)} {cfg.distance_unit}"


def fmt_efficiency(cfg: MarketConfig, value: float, powertrain: str) -> str:
    unit = cfg.efficiency_unit_ev if _is_ev(powertrain) else _require(cfg).efficiency_unit_ice
    decimals = 1 if "/100" in unit else 0
    return f"{fmt_number(cfg, value, decimals)} {unit}"


def _is_ev(powertrain: str | None) -> bool:
    return (powertrain or "").lower() in ("ev", "bev", "electric")


def annual_energy_cost(cfg: MarketConfig, efficiency: float, powertrain: str,
                       annual_distance: float | None = None, fuel_price: float | None = None,
                       energy_price: float | None = None) -> dict[str, Any]:
    """One year of fuel/energy cost in market units, with its formula."""
    _require(cfg)
    dist = float(annual_distance if annual_distance is not None else cfg.annual_distance_default)
    if efficiency <= 0:
        raise ValueError("efficiency must be positive")
    ev = _is_ev(powertrain)
    unit = cfg.efficiency_unit_ev if ev else cfg.efficiency_unit_ice
    price = float(energy_price if ev and energy_price is not None else
                  fuel_price if not ev and fuel_price is not None else
                  cfg.energy_price_default if ev else cfg.fuel_price_default)
    if "/100" in unit:                          # consumption: l/100km, kWh/100km
        qty = dist / 100.0 * efficiency
        formula = f"{dist} / 100 * {efficiency} * {price}"
    elif ev:                                    # MPGe: miles per 33.705 kWh
        qty = dist / efficiency * _KWH_PER_GALLON_E
        formula = f"{dist} / {efficiency} * {_KWH_PER_GALLON_E} * {price}"
    else:                                       # MPG
        qty = dist / efficiency
        formula = f"{dist} / {efficiency} * {price}"
    return {"annual_distance": dist, "efficiency": efficiency, "efficiency_unit": unit,
            "unit_price": price, "quantity": round(qty, 3), "formula": formula,
            "annual_cost": round(qty * price, 2)}


def tco(cfg: MarketConfig, vehicles: list[dict[str, Any]], years: int = 5,
        annual_distance: float | None = None) -> dict[str, Any]:
    """N-year total cost of ownership per vehicle: price − incentives + energy +
    maintenance (+ residual credit when supplied). Each vehicle dict needs
    `vehicle_id`, `price`, `efficiency`, `powertrain`; optional `incentives`,
    `annual_maintenance`, `residual_value`, `source_ref`. Every output figure is
    returned with the inputs and formula that produced it (acceptance #4)."""
    _require(cfg)
    if years <= 0:
        raise ValueError("years must be positive")
    rows = []
    for v in vehicles:
        energy = annual_energy_cost(cfg, float(v["efficiency"]), v.get("powertrain") or "ice",
                                    annual_distance=annual_distance)
        price = float(v["price"])
        incentives = float(v.get("incentives") or 0)
        maint = float(v.get("annual_maintenance") or 0)
        residual = float(v.get("residual_value") or 0)
        total = price - incentives + years * (energy["annual_cost"] + maint) - residual
        rows.append({
            "vehicle_id": v["vehicle_id"], "powertrain": v.get("powertrain"),
            "inputs": {"price": price, "incentives": incentives, "annual_maintenance": maint,
                       "residual_value": residual, "years": years, "energy": energy},
            "formula": f"{price} - {incentives} + {years} * ({energy['annual_cost']} + {maint}) - {residual}",
            "total": round(total, 2),
            "total_display": fmt_money(cfg, total),
            "per_distance": round(total / (years * energy["annual_distance"]), 4),
            "source_ref": v.get("source_ref"),
        })
    best = min(rows, key=lambda r: r["total"]) if rows else None
    return {"market_id": cfg.market_id, "currency": cfg.currency, "years": years,
            "distance_unit": cfg.distance_unit, "vehicles": rows,
            "lowest_tco": best["vehicle_id"] if best else None,
            "source_ref": f"market.tco (OEM_Markets:{cfg.market_id})"}
