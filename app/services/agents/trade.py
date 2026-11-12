"""A4 — Trade-In & Upgrade tools for the Trade-In screen and the Advisor's
trade card (spec §3.4, §5.6).

  lookup_vehicle(vin)                         -> the owned vehicle + owner (DEALER_Vehicles)
  get_trade_valuation(customer_id, ...)       -> value range (condition band), payoff, equity
  get_loan_lease_status(customer_id)          -> ownership / payoff / months remaining
  estimate_payment_delta(journey_id, price)   -> new payment vs current obligation, with formula
  add_trade_to_journey(journey_id, valuation) -> TRADE_IN context + trade.valued event

The valuation band is the sandbox valuation connector: the latest guidebook-
anchored DEALER_Valuations row adjusted transparently for mileage and condition
(agents.equity.refresh_valuation); the live Cox/KBB/Black Book connector returns
the same shape.
"""
from __future__ import annotations

from typing import Any

from ..common import ddb
from ..events import bus
from . import equity

_COND = equity._CONDITION_MULTIPLIER  # excellent/good/fair/poor — the auditable band
_DEFAULT_TERM = 60


class NotFound(Exception):
    pass


def lookup_vehicle(vin: str, env_prefix: str | None = None) -> dict[str, Any]:
    v = ddb.get_item("DEALER_Vehicles", {"vin": vin}, env_prefix)
    if v is None:
        raise NotFound(f"no owned vehicle with VIN {vin}")
    return v


def get_loan_lease_status(customer_id: str, env_prefix: str | None = None) -> dict[str, Any]:
    c = ddb.get_item("DEALER_Customers", {"customer_id": customer_id}, env_prefix) or {}
    cur = c.get("current_vehicle") or {}
    val = equity._latest_valuation(customer_id, env_prefix) or {}
    own = cur.get("ownership")
    payoff = val.get("payoff", cur.get("payoff"))
    # what the payoff figure IS, so the screen never shows a lease with a "$0 payoff"
    kind = ("lease buyout" if own == "lease" else "loan payoff" if own == "loan" or (payoff or 0) > 0
            else "owned outright" if own == "owned" else None)
    return {"customer_id": customer_id, "ownership": own, "payoff": payoff, "payoff_kind": kind,
            "months_remaining": cur.get("months_remaining"),
            "lease_end_window": own == "lease" and (cur.get("months_remaining") or 99) <= 6,
            "source_ref": "A4.get_loan_lease_status (DEALER_Customers.current_vehicle + DEALER_Valuations)"}


def get_trade_valuation(customer_id: str, mileage: float | None = None, condition: str | None = None,
                        persist: bool = False, env_prefix: str | None = None,
                        vin: str | None = None) -> dict[str, Any]:
    """The car's trade value. The same car at the same mileage and condition is worth the same
    every time: the figure is computed from the car's anchor valuation (equity.revalue), never
    from the last re-valuation, and with a VIN only that car's valuations count."""
    anchor, latest = equity.valuation_chain(customer_id, env_prefix, vin=vin)
    if anchor is None and vin:
        anchor, latest = equity.valuation_chain(customer_id, env_prefix)   # car not matched: owner's record
    if anchor is None:
        # an owner who told us their vehicle (web form) but has never been appraised: anchor to comparables
        anchor = latest = equity.baseline_valuation(customer_id, env_prefix)
    if anchor is None:
        raise NotFound(f"no baseline valuation for {customer_id}")
    base = latest or anchor
    if persist and (mileage is not None or condition):
        fresh = equity.refresh_valuation(customer_id, mileage, condition, env_prefix, vin=vin)
        value, payoff, vid, adj = fresh["new_trade_value"], fresh["payoff"], fresh["valuation_id"], fresh["adjustments"]
        condition = condition or None
    elif mileage is None and not condition:
        # nothing new: the recorded value of this car as it stands
        value = float(base.get("trade_value") or 0)
        payoff = float(base.get("payoff") or 0)
        vid, adj = base.get("valuation_id"), list(base.get("adjustments") or [])
        condition = base.get("condition")
    else:
        if condition and condition.lower() not in _COND:
            raise ValueError(f"condition must be one of {sorted(_COND)}")
        rv = equity.revalue(anchor, mileage if mileage is not None else equity._num(base.get("mileage")),
                            condition or base.get("condition"))
        value, adj, condition = rv["value"], rv["adjustments"], rv["condition"]
        payoff = float(base.get("payoff") or 0)
        vid = anchor.get("valuation_id")
    # band = the same vehicle at poor .. excellent condition (relative to the 'good' anchor)
    anchor = value / _COND.get((condition or "good").lower(), 1.0)
    low, high = round(anchor * _COND["poor"], 2), round(anchor * _COND["excellent"], 2)
    eq = round(value - payoff, 2)
    out = {"customer_id": customer_id, "valuation_id": vid, "currency": base.get("currency"),
           "vehicle": {k: base.get(k) for k in ("year", "make", "model", "mileage")},
           "trade_value": round(value, 2), "range": {"low": low, "high": high, "basis": "condition band poor..excellent"},
           "payoff": payoff, "equity": eq, "equity_position": "positive" if eq > 0 else "negative",
           "adjustments": adj, "valued_at": base.get("valued_at"),
           "source_ref": base.get("source_ref") or "A4.get_trade_valuation"}
    out["talk_track"] = build_talk_track(out)
    return out


def _amount(v: Any, currency: str | None) -> str:
    try:
        return f"{currency or ''} {float(v):,.0f}".strip()
    except (TypeError, ValueError):
        return str(v)


def build_talk_track(val: dict[str, Any]) -> dict[str, Any]:
    """The advisor's trade talk track (spec §3.4 `TradeInResult.talk_track`).

    A template over the figures already in the valuation — value, the condition
    band, payoff and equity — so the advisor says the same numbers the screen
    shows. Nothing here is generated: swap the figures and the sentence changes
    with them."""
    cur = val.get("currency")
    veh = val.get("vehicle") or {}
    name = " ".join(str(x) for x in (veh.get("year"), veh.get("make"), veh.get("model")) if x) or "your vehicle"
    value, payoff, eq = val.get("trade_value") or 0, val.get("payoff") or 0, val.get("equity") or 0
    band = val.get("range") or {}
    opening = (f"{name} appraises at {_amount(value, cur)} today — the condition band runs "
               f"{_amount(band.get('low'), cur)} to {_amount(band.get('high'), cur)}.")
    if payoff <= 0:
        middle = f"You own it outright, so the whole {_amount(value, cur)} goes toward the next vehicle."
        close = "That is your down payment already covered — let's put it against the build you liked."
    elif eq > 0:
        middle = (f"With {_amount(payoff, cur)} left to pay off, that leaves {_amount(eq, cur)} of equity "
                  "you can move straight into the next vehicle.")
        close = "Equity like that is the best time to upgrade — it shrinks the new payment before we start."
    else:
        middle = (f"The payoff is {_amount(payoff, cur)}, which is {_amount(abs(eq), cur)} more than the car is "
                  "worth today, so that gap has to go somewhere.")
        close = ("We can roll it into the new term, cover part of it with incentives, or keep the car a few "
                 "more months until the gap closes — your call, and I'll show you each one.")
    adjustments = [f"{a.get('factor')}: {a.get('delta') if a.get('delta') is not None else a.get('multiplier')}"
                   for a in val.get("adjustments") or []]
    return {"text": " ".join([opening, middle, close]),
            "opening": opening, "equity_line": middle, "close": close,
            "equity_position": val.get("equity_position"),
            "figures": {"trade_value": value, "payoff": payoff, "equity": eq,
                        "range_low": band.get("low"), "range_high": band.get("high"), "currency": cur},
            "adjustments_applied": adjustments,
            "source_ref": (f"A3 talk track over A4 valuation "
                           f"{val.get('valuation_id') or ''}".strip() + " (template over the figures)")}


def _monthly(principal: float, apr_pct: float, n: int) -> float:
    from ..finance import payment
    return payment.monthly(principal, apr_pct, n)         # the one formula (finance/payment.py)


def estimate_payment_delta(journey_id: str, vehicle_price: float | None = None, term_months: int = _DEFAULT_TERM,
                           down: float | None = None, env_prefix: str | None = None) -> dict[str, Any]:
    j = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id}, env_prefix)
    if j is None:
        raise NotFound(f"journey {journey_id} not found")
    snap = equity.build_equity(journey_id, env_prefix)
    c = ddb.get_item("DEALER_Customers", {"customer_id": j.get("customer_id")}, env_prefix) or {}
    mk = ddb.get_item("OEM_Markets", {"market_id": j.get("market_id") or c.get("market_id")}, env_prefix) or {}
    tiers = mk.get("credit_tiers") or []
    tier = next((t for t in tiers if t.get("tier") in ("Prime", "Gut")), tiers[0] if tiers else {"apr": 6.4, "tier": "default"})
    if vehicle_price is None:
        cfgs = sorted(ddb.scan_filter("PLAT_Configurations", "journey_id", journey_id, env_prefix),
                      key=lambda x: x.get("saved_at") or "", reverse=True)
        if not cfgs:
            raise NotFound("no saved build or vehicle price to estimate against")
        vehicle_price = float(cfgs[0].get("price_total") or cfgs[0].get("price_vehicle"))
    dn = float(down if down is not None else (c.get("budget") or {}).get("down") or 0)
    eq = float(snap.get("equity") or 0)
    principal = max(0.0, float(vehicle_price) - dn - max(eq, 0) + max(-eq, 0))
    new = _monthly(principal, float(tier["apr"]), term_months)
    current = 0.0   # current obligation is not on file (see equity._payment_delta); stated, not invented
    return {"journey_id": journey_id, "currency": snap.get("currency"),
            "inputs": {"vehicle_price": float(vehicle_price), "down": dn, "trade_equity": eq,
                       "apr_pct": float(tier["apr"]), "credit_tier": tier.get("tier"), "term_months": term_months},
            "formula": f"amortize({vehicle_price} - {dn} - equity({eq}), {tier['apr']}%, {term_months})",
            "new_monthly": new, "current_monthly": current, "delta_monthly": round(new - current, 2),
            "note": "current monthly obligation not on file; delta shown vs 0" if current == 0 else None,
            "source_ref": f"A4.estimate_payment_delta (OEM_Markets#{mk.get('market_id')}.credit_tiers + equity)"}


def add_trade_to_journey(journey_id: str, valuation: dict[str, Any], actor: str, env_prefix: str | None = None) -> dict[str, Any]:
    j = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id}, env_prefix)
    if j is None:
        raise NotFound(f"journey {journey_id} not found")
    j = dict(j)
    ctx = dict(j.get("context") or {})
    ctx.update({"valuation": True, "trade_condition": True, "trade_valuation_id": valuation.get("valuation_id")})
    j["context"] = ctx
    ddb.put_item("DEALER_Journeys", j, env_prefix)
    bus.emit("trade.valued", {"journey_id": journey_id, "customer_id": j.get("customer_id"),
                              "dealer_id": j.get("dealer_id"), "valuation_id": valuation.get("valuation_id"),
                              "trade_value": valuation.get("trade_value"), "equity": valuation.get("equity")},
             actor=actor, resource_url=f"/r/journey/{journey_id}", env_prefix=env_prefix)
    return {"journey_id": journey_id, "context": ctx, "valuation_id": valuation.get("valuation_id")}
