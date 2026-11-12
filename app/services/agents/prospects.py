"""A4 — find_upgrade_prospects + recommend_acquisition (spec §3.4 / §4.10.2).

The prospecting half of A4 (the port of the old prospecting use case) and the
used-car acquisition moment:

  find_upgrade_prospects(dealer_id, market)  -> owned vehicles that are upgrade
      candidates (high mileage, lease-end proxy, positive equity), scored + reasoned
  recommend_acquisition(vin, market)         -> compare a service-drive/trade
      vehicle against the store's stock need; emit an offer-range recommendation

Grounded in DEALER_Vehicles (odometer, ownership), DEALER_Valuations (equity),
and A1 shortlist demand (DEALER_Signals). A4 never contacts the customer — it
writes recommendation cards / desk items only.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from ..common import ddb, autonomy_config

_VEHICLES = "DEALER_Vehicles"
_VALUATIONS = "DEALER_Valuations"
_SIGNALS = "DEALER_Signals"
_RECS = "DEALER_Recommendations"
_TRADE = "DEALER_TradeDispositions"

_HIGH_MILEAGE = 60000
_ACQ_ACTION = "a4.detect_equity"
_SOURCE = "agentic:a4_prospects (spec §3.4)"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _num(v: Any):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def find_upgrade_prospects(dealer_id: str | None = None, market: str = "us",
                           limit: int | None = None,
                           env_prefix: str | None = None) -> dict[str, Any]:
    """Owned vehicles that are upgrade candidates, scored + reasoned (spec §3.4)."""
    veh = [v for v in ddb.scan_filter(_VEHICLES, "market_id", market, env_prefix)
           if str(v.get("ownership", "")).lower() in ("owned", "own", "active", "")]
    if dealer_id:
        veh = [v for v in veh if v.get("dealer_id") == dealer_id]
    custs = {c["customer_id"]: c for c in ddb.scan_filter("DEALER_Customers", "market_id", market, env_prefix)}
    prospects = []
    from ..common import vehicles as _vehicles
    for v in veh:
        if just_sold(v):
            continue   # the store just sold this car; it is not an upgrade prospect yet
        reasons, signals = [], {}
        score = 0.0
        odo = _num(v.get("odometer")) or 0
        if odo >= _HIGH_MILEAGE:
            reasons.append("high mileage"); signals["odometer"] = int(odo); score += 0.4
        cust = v.get("customer_id")
        c = custs.get(cust, {})
        # the valuation of THIS car, not the customer's latest (often a traded-in car)
        val = _vehicles.valuation_for(v.get("vin"), cust, env_prefix) if cust else None
        equity = _num(val.get("equity")) if val else None
        if equity is not None and equity > 0:
            reasons.append("positive equity"); signals["equity"] = equity; score += 0.4
        # age proxy from model year
        yr = _num(v.get("year")) or _num((val or {}).get("year"))
        if yr and yr <= (_dt.datetime.now().year - 4):
            reasons.append("aging vehicle"); signals["year"] = int(yr); score += 0.2
        cv = c.get("current_vehicle") or {}
        months = _num(cv.get("months_remaining"))
        if cv.get("ownership") == "lease" and months is not None and months <= 6:
            reasons.append("lease ending"); signals["lease_months_remaining"] = int(months); score += 0.4
        if reasons:
            prospects.append({"vin": v.get("vin"), "customer_id": cust,
                              "name": " ".join(filter(None, [c.get("given_name"), c.get("family_name")])) or None,
                              "dealer_id": v.get("dealer_id"), "model": v.get("model") or (val or {}).get("model"),
                              "vehicle": {"year": yr and int(yr), "make": (val or {}).get("make"),
                                          "model": (val or {}).get("model") or v.get("model")},
                              "score": round(min(score, 1.0), 2), "reasons": reasons, "signals": signals,
                              "equity": equity, "currency": (val or {}).get("currency"),
                              "source_ref": (val or {}).get("source_ref") or "DEALER_Vehicles"})
    prospects.sort(key=lambda p: p["score"], reverse=True)
    if limit:
        prospects = prospects[:limit]
    return {"market": market, "dealer_id": dealer_id, "count": len(prospects),
            "prospects": prospects,
            "source_ref": "A4.find_upgrade_prospects (DEALER_Vehicles + DEALER_Valuations)"}


_JUST_SOLD_DAYS = 180     # a car the store delivered this recently is not a buy-back target
_JUST_SOLD_MILES = 1000   # nor is a near-new car (delivery miles only)


def just_sold(veh: dict[str, Any] | None, now: _dt.datetime | None = None) -> str | None:
    """Why this car must not be scouted for acquisition, or None.

    A car the store itself delivered recently (or one still on delivery miles) is the
    store's own fresh sale: offering to buy it back at trade value days after the customer
    paid retail is wrong on every screen that shows it."""
    if not veh:
        return None
    now = now or _dt.datetime.now(_dt.timezone.utc)
    delivered = str(veh.get("delivered_at") or "")
    if delivered:
        try:
            when = _dt.datetime.fromisoformat(delivered)
            if when.tzinfo is None:
                when = when.replace(tzinfo=_dt.timezone.utc)
            if (now - when).days < _JUST_SOLD_DAYS:
                return f"delivered by the store {max((now - when).days, 0)} days ago"
        except ValueError:
            pass
    odo = _num(veh.get("odometer"))
    if odo is not None and odo < _JUST_SOLD_MILES and (delivered or veh.get("deal_id")):
        return f"near-new ({int(odo)} mi), sold by the store"
    return None


def recommend_acquisition(vin: str, market: str = "us", apply: bool = False,
                          env_prefix: str | None = None) -> dict[str, Any]:
    """Compare a service-drive/trade vehicle against the store's used-inventory
    demand and emit an offer-range recommendation (spec §3.4, §4.10.2)."""
    from ..common import vehicles as _vehicles
    veh = ddb.get_item(_VEHICLES, {"vin": vin}, env_prefix)
    if veh is None:
        return {"vin": vin, "found": False}
    model_id = veh.get("model_id")
    skip = just_sold(veh)
    if skip:
        return {"vin": vin, "model_id": model_id, "recommend_acquire": False, "skipped": skip,
                "offer_range": None,
                "source_ref": "A4.recommend_acquisition (DEALER_Vehicles.delivered_at — store's own recent sale)"}
    # demand proxy: how many recent A1 shortlist/compare signals reference this model
    signals = [s for s in ddb.scan_all(_SIGNALS, env_prefix, limit=500)
               if s.get("model_id") == model_id]
    demand = len(signals)
    # the valuation OF THIS CAR — never the customer's latest one (often the car they traded in)
    val = _vehicles.valuation_for(vin, veh.get("customer_id"), env_prefix)
    trade_value = _num(val.get("trade_value")) if val else None
    # offer range: anchored on trade value +/- a demand-adjusted band
    level = autonomy_config.active_level(_ACQ_ACTION, market, env_prefix)
    offer_low = round(trade_value * 0.95, 0) if trade_value else None
    offer_high = round(trade_value * (1.02 + min(demand, 20) * 0.003), 0) if trade_value else None
    rec = {"vin": vin, "model_id": model_id, "demand_signals": demand,
           "recommend_acquire": demand >= 3 and trade_value is not None,
           "offer_range": {"low": offer_low, "high": offer_high} if trade_value else None,
           "autonomy_level": level,
           "source_ref": "A4.recommend_acquisition (DEALER_Signals demand + DEALER_Valuations)"}
    if apply and rec["recommend_acquire"] and level in autonomy_config.WRITE_LEVELS:
        rid = f"REC-ACQ-{vin.replace('-', '')[-10:]}"
        ddb.put_item(_RECS, {"recommendation_id": rid, "action": "acquisition_recommended",
                             "agent_id": "A4", "dealer_id": veh.get("dealer_id"),
                             "shown": 0, "accepted": 0, "converted": 0, "shown_at": _now(),
                             "autonomy_level": level, "vin": vin,
                             "reason": f"{demand} demand signals; offer {offer_low}-{offer_high}",
                             "source_ref": _SOURCE}, env_prefix)
        rec["recommendation_written"] = rid
    return rec
