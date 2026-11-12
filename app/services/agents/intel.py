"""A3 — get_competitive_intel + get_incentives (advisor right panel), spec §5.3.

Two grounded read models for the advisor's value/intel panel:

  get_competitive_intel(journey)  -> the customer's trim vs. comparable trims
  get_incentives(journey)         -> active incentives for the model/market

Honesty note: the spec references an OEM competitor feed (OEM_Catalog.competitors)
that is NOT present in the seed data, and the live competitor/market feed is a
Phase-6 connector. Rather than fabricate rival vehicles, get_competitive_intel
does an IN-CATALOG cross-shop comparison (same segment: seats + powertrain +
price band) against other models in the same market, and flags that external
comps are not wired. Every compared figure is a real catalog value.
"""
from __future__ import annotations

from typing import Any

from ..common import ddb

_CATALOG = "OEM_Catalog"
_INCENTIVES = "OEM_Incentives"
_JOURNEYS = "DEALER_Journeys"
_CUSTOMERS = "DEALER_Customers"

# compared spec attributes (real catalog fields)
_COMPARE_FIELDS = ("list_price", "efficiency", "efficiency_unit", "ev_range",
                   "range_unit", "power_kw", "seats", "co2_g_km", "drive")


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


class NotFound(Exception):
    pass


def _trims(cat: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in cat if r.get("kind") is None and r.get("model_id")]


def _pick_trim(cat: list[dict[str, Any]], model_id: str, trim_id: str | None) -> dict[str, Any] | None:
    trims = [t for t in _trims(cat) if t.get("model_id") == model_id]
    if not trims:
        return None
    if trim_id:
        exact = next((t for t in trims if t.get("trim_id") == trim_id), None)
        if exact:
            return exact
    # else the mid trim by price
    trims.sort(key=lambda t: _num(t.get("list_price")) or 0)
    return trims[len(trims) // 2]


def get_competitive_intel(journey: dict[str, Any], env_prefix: str | None = None) -> dict[str, Any]:
    """Cross-shop the customer's trim against comparable in-catalog trims (§5.3).

    Comparables = other MODELS' trims in the same market with the same powertrain
    and a similar seat count, within ±20% price. Each comparison line is real
    catalog data. External competitor feed is flagged as not-wired (Phase 6)."""
    market = journey.get("market_id") or "us"
    model_id = journey.get("primary_model_id")
    if not model_id:
        raise NotFound("journey has no primary_model_id to compare")
    currency = "EUR" if market == "de" else "USD"
    cat = [r for r in ddb.scan_all(_CATALOG, env_prefix) if r.get("currency") in (None, currency)]
    subject = _pick_trim(cat, model_id, journey.get("primary_trim_id"))
    if subject is None:
        raise NotFound(f"no catalog trim for model {model_id}")

    s_price = _num(subject.get("list_price")) or 0
    s_pt = (subject.get("powertrain") or "").upper()
    s_seats = _num(subject.get("seats"))

    comps = []
    for t in _trims(cat):
        if t.get("model_id") == model_id:
            continue                       # skip same model
        if (t.get("powertrain") or "").upper() != s_pt:
            continue                       # same powertrain segment
        price = _num(t.get("list_price")) or 0
        if s_price and abs(price - s_price) > 0.2 * s_price:
            continue                       # within ±20% price band
        if s_seats is not None and _num(t.get("seats")) is not None and abs(_num(t.get("seats")) - s_seats) > 1:
            continue                       # similar seating
        comps.append(t)

    # rank comparables by closeness in price
    comps.sort(key=lambda t: abs((_num(t.get("list_price")) or 0) - s_price))

    def _row(t):
        return {k: t.get(k) for k in ("model_id", "trim_id", "trim_name") + _COMPARE_FIELDS}

    # advantage flags: where the subject beats a comparable on price/efficiency/range
    def _advantages(t):
        adv = []
        cp = _num(t.get("list_price"))
        if cp is not None and s_price and s_price < cp:
            adv.append(f"lower price by {round(cp - s_price)}")
        se, ce = _num(subject.get("efficiency")), _num(t.get("efficiency"))
        if se is not None and ce is not None and se > ce and subject.get("efficiency_unit") == t.get("efficiency_unit"):
            adv.append("better efficiency")
        sr, cr = _num(subject.get("ev_range")), _num(t.get("ev_range"))
        if sr is not None and cr is not None and sr > cr:
            adv.append("longer range")
        return adv

    # Named segment competitors (OEM_CatalogModels.competitors: real makes/models,
    # public base prices, win rate and why-we-win / why-we-lose from field intel).
    model_row = next((m for m in ddb.scan_filter("OEM_CatalogModels", "market_id", market, env_prefix)
                      if m.get("model_id") == model_id), {}) or {}
    competitors = [{
        "name": f"{c.get('make')} {c.get('model')}", "base_price": c.get("base_price"),
        "win_rate": (c.get("intel") or {}).get("win_rate"),
        "why_we_win": (c.get("intel") or {}).get("why_we_win") or [],
        "why_we_lose": (c.get("intel") or {}).get("why_we_lose") or [],
        "price_delta": (round(s_price - float(c["base_price"])) if c.get("base_price") and s_price else None),
        "source_ref": c.get("source_url") or model_row.get("source_ref"),
    } for c in (model_row.get("competitors") or [])]
    competitors.sort(key=lambda c: -(c["win_rate"] or 0))

    return {
        "journey_id": journey.get("journey_id"),
        "market": market,
        "currency": currency,
        "competitors": competitors,
        "subject": _row(subject),
        "comparables": [{**_row(t), "our_advantages": _advantages(t)} for t in comps[:4]],
        "comparable_count": len(comps),
        "external_feed": {"wired": False,
                          "note": "OEM competitor / market-intel feed is a Phase-6 connector; "
                                  "comparison uses in-catalog same-segment trims (real data)."},
        "source_ref": "A3.get_competitive_intel (OEM_Catalog in-catalog cross-shop)",
    }


def _incentive_applies(inc: dict[str, Any], powertrain: str | None, state: str | None) -> bool:
    if inc.get("status") != "active":
        return False
    pts = [p.upper() for p in (inc.get("powertrains") or [])]
    if pts and powertrain and powertrain.upper() not in pts:
        return False
    scope = (inc.get("scope") or "").upper()
    if scope and scope not in ("US", "DE", "") and state and scope != state.upper():
        return False
    return True


def get_incentives(journey: dict[str, Any], env_prefix: str | None = None) -> dict[str, Any]:
    """Active incentives for the journey's model/market (§5.3).

    Scoped by market, the trim's powertrain, and the customer's state; each
    carries its amount + source_ref. Reuses the same matching discipline as
    price_build (no fabricated programs)."""
    market = journey.get("market_id") or "us"
    cat = ddb.scan_all(_CATALOG, env_prefix)
    subject = _pick_trim(cat, journey.get("primary_model_id"), journey.get("primary_trim_id"))
    powertrain = (subject or {}).get("powertrain")
    customer = ddb.get_item(_CUSTOMERS, {"customer_id": journey.get("customer_id")}, env_prefix)
    state = (customer or {}).get("region_code")
    amount_field = "amount_eur" if market == "de" else "amount_usd"

    rows = []
    for inc in ddb.scan_filter(_INCENTIVES, "market_id", market, env_prefix):
        if not _incentive_applies(inc, powertrain, state):
            continue
        rows.append({"incentive_id": inc.get("incentive_id"), "name": inc.get("name"),
                     "tier": inc.get("tier"), "amount": _num(inc.get(amount_field)),
                     "powertrains": inc.get("powertrains"), "scope": inc.get("scope"),
                     "valid_to": inc.get("valid_to"), "source_ref": inc.get("source_ref")})
    rows.sort(key=lambda r: r.get("amount") or 0, reverse=True)
    return {
        "journey_id": journey.get("journey_id"),
        "market": market, "powertrain": powertrain, "state": state,
        "incentives": rows,
        "count": len(rows),
        "source_ref": "A3.get_incentives (OEM_Incentives, market/powertrain/state scoped)",
    }
