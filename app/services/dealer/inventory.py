"""Inventory & Trade Desk (spec §5.6 extended, G-06) — inventory / used-car manager.

  list_inventory(scope, market, ...)     -> VIN grid: age, cost, list price, market price
                                            comps, A1 demand score, reservation
  reserve(vin, journey_id, actor) / unreserve(vin, actor)
  trade_dispositions(scope, market)      -> accepted trades with an A4 keep-CPO /
                                            wholesale / auction recommendation + reasoning
  decide_disposition(id, decision, ...)  -> records the decision (DMS write in the live path)
  acquisitions(scope, market)            -> A4 acquisition recommendations (service-drive
                                            vehicles matching store demand) with offer range

Market price comps: median list price of the same trim across the market's
inventory (the valuation-connector comps in the live path). Demand score:
active journeys whose primary model/trim or latest saved build is this model/trim
(§5.6 "how many active journeys shortlist this model/trim"). Every computed
figure carries the inputs that produced it in `source_ref`.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from ..common import ddb, labels, vehicles
from ..events import bus as _bus

_INV = "DEALER_Inventory"
_TD = "DEALER_TradeDispositions"
DISPOSITIONS = ("keep_cpo", "retail", "wholesale", "auction")
_ACTIVE = {"DISCOVER", "CONFIGURE", "QUALIFY", "FINANCE", "TRADE_IN", "AGREE"}


class NotFound(Exception):
    pass


class InvalidState(Exception):
    pass


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None and not isinstance(v, bool) else None
    except (TypeError, ValueError):
        return None


def _median(xs: list[float]) -> float | None:
    xs = sorted(xs)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else round((xs[n // 2 - 1] + xs[n // 2]) / 2, 2)


def _in_scope(scope: dict[str, Any], dealer_id: str | None) -> bool:
    return bool(scope.get("is_oem")) or dealer_id in (scope.get("dealer_ids") or set())


def demand_by_trim(market: str, env_prefix: str | None = None) -> dict[str, dict[str, int]]:
    """{'model': {model_id: n}, 'trim': {trim_id: n}} over active journeys."""
    journeys = [j for j in ddb.scan_filter("DEALER_Journeys", "market_id", market, env_prefix)
                if j.get("stage") in _ACTIVE and (j.get("status") or "active") == "active"]
    active_ids = {j["journey_id"] for j in journeys}
    latest_cfg: dict[str, dict[str, Any]] = {}
    for c in ddb.scan_all("PLAT_Configurations", env_prefix):
        jid = c.get("journey_id")
        if jid in active_ids and (jid not in latest_cfg or (c.get("saved_at") or "") > (latest_cfg[jid].get("saved_at") or "")):
            latest_cfg[jid] = c
    model: dict[str, int] = {}
    trim: dict[str, int] = {}
    for j in journeys:
        cfg = latest_cfg.get(j["journey_id"], {})
        m = cfg.get("model_id") or j.get("primary_model_id")
        t = cfg.get("trim_id") or j.get("primary_trim_id")
        if m:
            model[m] = model.get(m, 0) + 1
        if t:
            trim[t] = trim.get(t, 0) + 1
    return {"model": model, "trim": trim, "active_journeys": len(journeys)}


def list_inventory(scope: dict[str, Any], market_id: str | None = None, status: str | None = None,
                   model_id: str | None = None, env_prefix: str | None = None) -> dict[str, Any]:
    market = market_id or scope.get("market_id") or "us"
    rows = ddb.scan_filter(_INV, "market_id", market, env_prefix)
    comps: dict[str, list[float]] = {}
    for v in rows:
        lp = _num(v.get("list_price"))
        if lp is not None and v.get("trim_id"):
            comps.setdefault(v["trim_id"], []).append(lp)
    demand = demand_by_trim(market, env_prefix)
    names = vehicles.model_names(env_prefix)
    out = []
    for v in rows:
        if not _in_scope(scope, v.get("dealer_id")):
            continue
        if status and v.get("status") != status:
            continue
        if model_id and v.get("model_id") != model_id:
            continue
        trim_comps = comps.get(v.get("trim_id") or "", [])
        market_price = _median(trim_comps)
        lp = _num(v.get("list_price"))
        out.append({
            "vin": v.get("vin"), "stock_number": v.get("stock_number"), "dealer_id": v.get("dealer_id"),
            "model_id": v.get("model_id"), "model_name": names.get(v.get("model_id") or "", v.get("model_id")),
            "trim_id": v.get("trim_id"), "trim_name": v.get("trim_name"),
            "powertrain": v.get("powertrain"), "color": v.get("color"), "status": v.get("status"),
            "age_days": _num(v.get("age_days")), "eta_days": _num(v.get("eta_days")),
            "cost": _num(v.get("cost")), "list_price": lp, "currency": v.get("currency") or ("EUR" if market == "de" else "USD"),
            "market_price": market_price,
            "price_vs_market": round(lp - market_price, 2) if lp is not None and market_price is not None else None,
            "comps_n": len(trim_comps),
            "demand_score": demand["trim"].get(v.get("trim_id") or "", 0) or demand["model"].get(v.get("model_id") or "", 0),
            # a hold only on a unit still for sale: a sold car is never shown as "reserved"
            "reserved_for_journey_id": v.get("reserved_for_journey_id") if v.get("status") != "sold" else None,
            "sold_to_journey_id": v.get("sold_to_journey_id"),
            "source_ref": f"{v.get('source_ref') or 'DEALER_Inventory'}; comps=median list of {len(trim_comps)} {v.get('trim_id')} units",
        })
    out.sort(key=lambda r: (-(r["age_days"] or 0)))
    in_stock = [r for r in out if r["status"] == "in_stock"]
    by_model: dict[str, dict[str, Any]] = {}
    for r in out:
        b = by_model.setdefault(r["model_id"] or "?", {"model_id": r["model_id"], "model_name": r.get("model_name"),
                                                       "units": 0, "aged_60": 0, "demand": 0})
        b["units"] += 1
        b["aged_60"] += 1 if (r["age_days"] or 0) >= 60 else 0
        b["demand"] = max(b["demand"], demand["model"].get(r["model_id"] or "", 0))
    return {
        "market_id": market, "count": len(out), "vehicles": out,
        "kpis": {"in_stock": len(in_stock), "in_transit": sum(1 for r in out if r["status"] == "in_transit"),
                 "reserved": sum(1 for r in out if r["reserved_for_journey_id"]),
                 "aged_60": sum(1 for r in in_stock if (r["age_days"] or 0) >= 60),
                 "avg_age_days": round(sum(r["age_days"] or 0 for r in in_stock) / len(in_stock), 1) if in_stock else None},
        "by_model": sorted(by_model.values(), key=lambda b: -b["units"]),
        "active_journeys": demand["active_journeys"],
        "source_ref": "InventoryDesk.list (DEALER_Inventory + A1 demand from journeys/configurations)",
    }


def _vehicle(vin: str, env_prefix: str | None) -> dict[str, Any]:
    v = ddb.get_item(_INV, {"vin": vin}, env_prefix)
    if v is None:
        raise NotFound(f"VIN {vin} not in inventory")
    return dict(v)


def reserve(vin: str, journey_id: str, actor: str, env_prefix: str | None = None) -> dict[str, Any]:
    v = _vehicle(vin, env_prefix)
    if v.get("status") not in ("in_stock", "in_transit", "reserved"):
        raise InvalidState(f"{vin} is {v.get('status')} and cannot be reserved")
    if v.get("reserved_for_journey_id") and v["reserved_for_journey_id"] != journey_id:
        raise InvalidState(f"{vin} is already reserved for {v['reserved_for_journey_id']}")
    j = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id}, env_prefix)
    if j is None:
        raise NotFound(f"journey {journey_id} not found")
    if j.get("dealer_id") != v.get("dealer_id"):
        raise InvalidState("a VIN can only be reserved for a journey at the same dealer (use a dealer trade)")
    prior = v.get("status")
    v.update({"reserved_for_journey_id": journey_id, "reserved_at": _now(), "reserved_by": actor,
              "status": "reserved", "status_before_reserve": prior if prior != "reserved" else v.get("status_before_reserve")})
    ddb.put_item(_INV, v, env_prefix)
    ev = _bus.emit("inventory.changed", {"vin": vin, "dealer_id": v.get("dealer_id"), "change": "reserved",
                                         "journey_id": journey_id, "model_id": v.get("model_id"), "trim_id": v.get("trim_id")},
                   actor=actor, resource_url=f"/r/vin/{vin}", env_prefix=env_prefix)
    return {"vin": vin, "status": "reserved", "reserved_for_journey_id": journey_id, "bus_event": ev}


def unreserve(vin: str, actor: str, env_prefix: str | None = None) -> dict[str, Any]:
    v = _vehicle(vin, env_prefix)
    if not v.get("reserved_for_journey_id"):
        raise InvalidState(f"{vin} is not reserved")
    jid = v.pop("reserved_for_journey_id")
    v["status"] = v.pop("status_before_reserve", None) or "in_stock"
    v["reserved_at"] = None
    v["unreserved_at"] = _now()
    v["unreserved_by"] = actor
    ddb.put_item(_INV, v, env_prefix)
    ev = _bus.emit("inventory.changed", {"vin": vin, "dealer_id": v.get("dealer_id"), "change": "unreserved",
                                         "journey_id": jid, "model_id": v.get("model_id"), "trim_id": v.get("trim_id")},
                   actor=actor, resource_url=f"/r/vin/{vin}", env_prefix=env_prefix)
    return {"vin": vin, "status": v["status"], "bus_event": ev}


# --- trade dispositions ---------------------------------------------------------

def _recommend(val: dict[str, Any] | None, demand_model: int, now_year: int,
               distance_unit: str = "") -> tuple[str, list[str]]:
    """A4 disposition recommendation from the trade's age, mileage, and demand."""
    if not val:
        return "wholesale", ["no valuation on file — default to wholesale until appraised"]
    age = now_year - int(_num(val.get("year")) or now_year)
    miles = _num(val.get("mileage")) or 0
    reasons = [f"{age} yr old", f"odometer {int(miles)} {distance_unit}".strip()]
    if age <= 5 and miles <= 60000:
        reasons.append("within CPO age/mileage window")
        return ("keep_cpo", reasons + ([f"store demand index {demand_model} active journeys per model"] if demand_model else []))
    if age > 10 or miles > 120000:
        reasons.append("beyond retail-reconditioning economics")
        return "auction", reasons
    if demand_model >= 3:
        reasons.append(f"store demand index {demand_model} active journeys per model")
        return "retail", reasons
    reasons.append("retail demand thin for this unit")
    return "wholesale", reasons


def _customers_and_valuations(trades: list[dict[str, Any]], env_prefix: str | None
                              ) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Customers by customer_id and Valuations grouped by customer_id, each read in
    a single scan, for the customers that appear on `trades`. Valuations keep the
    table's scan order within a customer, as the per-customer scan returned them."""
    wanted = {t.get("customer_id") for t in trades if t.get("customer_id")}
    if not wanted:
        return {}, {}
    customers = {c["customer_id"]: c for c in ddb.scan_all("DEALER_Customers", env_prefix)
                 if c.get("customer_id") in wanted}
    valuations: dict[str, list[dict[str, Any]]] = {}
    for v in ddb.scan_all("DEALER_Valuations", env_prefix):
        if v.get("customer_id") in wanted:
            valuations.setdefault(v["customer_id"], []).append(v)
    return customers, valuations


def trade_dispositions(scope: dict[str, Any], market_id: str | None = None, include_decided: bool = True,
                       env_prefix: str | None = None) -> dict[str, Any]:
    market = market_id or scope.get("market_id") or "us"
    from ..common import people
    names = people.directory(env_prefix)
    demand = demand_by_trim(market, env_prefix)["model"]
    avg_demand = sum(demand.values()) // max(len(demand), 1)
    unit = (ddb.get_item("OEM_Markets", {"market_id": market}, env_prefix) or {}).get("distance_unit", "")
    year = _dt.datetime.now().year
    trades = ddb.scan_all(_TD, env_prefix)
    # One read per table instead of one Customers get_item and one Valuations scan
    # per disposition: with every store in scope that was ~400 DynamoDB calls and
    # ~30 s, which made the Store Cockpit (it builds this tab on every load) slow.
    customers, valuations = _customers_and_valuations(trades, env_prefix)
    rows = []
    for t in trades:
        cid = t.get("customer_id")
        cust = customers.get(cid) if cid else None
        dealer_id = t.get("dealer_id") or (cust or {}).get("dealer_id")
        if (cust or {}).get("market_id", market) != market or not _in_scope(scope, dealer_id):
            continue
        vals = valuations.get(cid, []) if cid else []
        val = max(vals, key=lambda v: v.get("valued_at") or "") if vals else None
        rec, reasons = _recommend(val, avg_demand if val else 0, year, unit)
        decided = t.get("decided_at") is not None
        if decided and not include_decided:
            continue
        trade_value = _num((val or {}).get("trade_value"))
        recon = _num(t.get("recon_cost"))
        rows.append({
            "disposition_id": t.get("disposition_id"), "customer_id": t.get("customer_id"), "dealer_id": dealer_id,
            "vehicle": " ".join(str(x) for x in ((val or {}).get("year"), (val or {}).get("make"), (val or {}).get("model")) if x) or None,
            "mileage": _num((val or {}).get("mileage")), "trade_value": trade_value, "recon_cost": recon,
            "net_cost": round(trade_value + recon, 2) if trade_value is not None and recon is not None else None,
            "currency": t.get("currency"), "acquired_at": t.get("acquired_at"),
            "recommendation": rec, "recommendation_label": labels.disposition_label(rec), "reasoning": reasons,
            "status": "decided" if decided else "pending",
            "decision": t.get("decision") or (t.get("disposition") if decided else None),
            "decided_by": t.get("decided_by"), "decided_by_name": people.name(t.get("decided_by"), names),
            "decided_at": t.get("decided_at"),
            "source_ref": f"A4 disposition rules (age/mileage/demand) over {(val or {}).get('valuation_id') or 'no valuation'}",
        })
    rows.sort(key=lambda r: (r["status"] != "pending", r.get("acquired_at") or ""))
    return {"market_id": market, "count": len(rows), "pending": sum(1 for r in rows if r["status"] == "pending"),
            "dispositions": rows, "source_ref": "InventoryDesk.trade_dispositions (DEALER_TradeDispositions + Valuations)"}


def _get_td(disposition_id: str, env_prefix: str | None) -> dict[str, Any] | None:
    # the table key resolves to disposition_id (seed) or trade_disposition_id
    # (PK override) depending on the loader, so look up by attribute.
    rows = ddb.scan_filter(_TD, "disposition_id", disposition_id, env_prefix)
    return rows[0] if rows else None


def decide_disposition(disposition_id: str, decision: str, actor: str, reason: str | None = None,
                       env_prefix: str | None = None) -> dict[str, Any]:
    if decision not in DISPOSITIONS:
        raise ValueError(f"decision must be one of {DISPOSITIONS}")
    t = _get_td(disposition_id, env_prefix)
    if t is None:
        raise NotFound(f"trade disposition {disposition_id} not found")
    if t.get("decided_at"):
        raise InvalidState(f"{disposition_id} was already decided ({t.get('decision')})")
    t = dict(t)
    t.update({"trade_disposition_id": disposition_id,
              "decision": decision, "disposition": decision, "decided_by": actor, "decided_at": _now(),
              "decision_reason": reason, "dms_status": "written (sandbox)"})
    ddb.put_item(_TD, t, env_prefix)
    cust = ddb.get_item("DEALER_Customers", {"customer_id": t.get("customer_id")}, env_prefix) or {}
    ev = _bus.emit("inventory.changed", {"change": f"trade_{decision}", "disposition_id": disposition_id,
                                         "dealer_id": t.get("dealer_id") or cust.get("dealer_id"),
                                         "customer_id": t.get("customer_id")},
                   actor=actor, env_prefix=env_prefix)
    return {"disposition_id": disposition_id, "decision": decision, "bus_event": ev}


# --- acquisitions (A4 recommend_acquisition) ----------------------------------------

def acquisitions(scope: dict[str, Any], market_id: str | None = None, limit: int = 12,
                 env_prefix: str | None = None) -> dict[str, Any]:
    from ..agents import prospects
    market = market_id or scope.get("market_id") or "us"
    cards = []
    for r in ddb.scan_all("DEALER_Recommendations", env_prefix):
        if r.get("action") == "acquisition_recommended" and _in_scope(scope, r.get("dealer_id")):
            if r.get("vin") and prospects.just_sold(ddb.get_item("DEALER_Vehicles", {"vin": r["vin"]}, env_prefix)):
                continue   # the store's own fresh sale is never a buy-back card
            cards.append({"recommendation_id": r.get("recommendation_id"), "vin": r.get("vin"),
                          "dealer_id": r.get("dealer_id"), "reason": r.get("reason"), "origin": "agent",
                          "accepted_at": r.get("accepted_at"), "autonomy_level": r.get("autonomy_level"),
                          "source_ref": r.get("source_ref")})
    # live: vehicles in today's service drive (open service journeys) in scope
    seen = {c["vin"] for c in cards}
    for sj in ddb.scan_filter("DEALER_ServiceJourneys", "market_id", market, env_prefix):
        if len(cards) >= limit:
            break
        vin = sj.get("vin")
        if not vin or vin in seen or sj.get("stage") == "COMPLETED" or not _in_scope(scope, sj.get("dealer_id")):
            continue
        rec = prospects.recommend_acquisition(vin, market=market, apply=False, env_prefix=env_prefix)
        if rec.get("recommend_acquire"):
            veh = vehicles.display_fields(ddb.get_item("DEALER_Vehicles", {"vin": vin}, env_prefix), env_prefix)
            cards.append({"vin": vin, "dealer_id": sj.get("dealer_id"), "origin": "service_drive",
                          "service_journey_id": sj.get("service_journey_id"),
                          "vehicle": " ".join(str(x) for x in (veh.get("year"), veh.get("make"), veh.get("model")) if x) or None,
                          "odometer": _num(veh.get("odometer")),
                          "demand_signals": rec.get("demand_signals"), "offer_range": rec.get("offer_range"),
                          "autonomy_level": rec.get("autonomy_level"), "source_ref": rec.get("source_ref")})
            seen.add(vin)
    return {"market_id": market, "count": len(cards), "acquisitions": cards,
            "source_ref": "A4.recommend_acquisition (service-drive vehicles × store demand)"}
