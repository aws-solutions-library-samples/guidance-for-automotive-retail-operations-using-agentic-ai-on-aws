"""Shared vehicle display resolution.

A DEALER_Vehicles row carries identity (vin, model_id, trim_id) but not always
denormalized display names. Screens show make/model/year, so resolve the
missing names from the OEM catalog (OEM_CatalogModels: brand -> make,
name -> model) instead of rendering an em dash.
"""
from __future__ import annotations

from typing import Any

from . import ddb


def display_fields(vehicle: dict[str, Any] | None, env_prefix: str | None = None) -> dict[str, Any]:
    """Fill make/model/year on a vehicle row from the model catalog when absent."""
    v = dict(vehicle or {})
    if (not v.get("make") or not v.get("model")) and v.get("model_id"):
        row = ddb.get_item("OEM_CatalogModels", {"model_id": v["model_id"]}, env_prefix) or {}
        v.setdefault("make", None)
        v.setdefault("model", None)
        brand, name = str(row.get("brand") or ""), str(row.get("name") or "")
        # "Meridian Ion C" under brand "Meridian Motors" reads as make Meridian · model Ion C,
        # never "Meridian Motors Meridian Ion C"
        short = brand.split()[0] if brand else ""
        if short and name.startswith(short + " "):
            brand, name = short, name[len(short) + 1:]
        v["make"] = v["make"] or brand or None
        v["model"] = v["model"] or name or None
    return v


def model_names(env_prefix: str | None = None) -> dict[str, str]:
    """model_id → catalog name ('MER-VOLT-C' → 'Meridian Ion C'): one scan per read model."""
    return {m["model_id"]: str(m.get("name") or m["model_id"]) for m in ddb.scan_all("OEM_CatalogModels", env_prefix)
            if m.get("model_id")}


def trim_names(env_prefix: str | None = None) -> dict[str, str]:
    """trim_id → catalog trim name ('MER-VOLT-C-T3' → 'Ion C Extended AWD')."""
    return {t["trim_id"]: str(t.get("trim_name") or t["trim_id"]) for t in ddb.scan_all("OEM_Catalog", env_prefix)
            if t.get("trim_id")}


def valuations_of(vin: str | None, customer_id: str | None, env_prefix: str | None = None) -> list[dict[str, Any]]:
    """Every A4 valuation row OF THIS CAR (matched as valuation_for does), oldest first."""
    if not vin or not customer_id:
        return []
    car = display_fields(ddb.get_item("DEALER_Vehicles", {"vin": vin}, env_prefix) or {}, env_prefix)
    make, model = str(car.get("make") or "").lower(), str(car.get("model") or "").lower()
    if not make or not model:
        return []

    def same(v: dict[str, Any]) -> bool:
        if v.get("vin"):
            return v["vin"] == vin
        if str(v.get("make") or "").lower() != make or str(v.get("model") or "").lower() != model:
            return False
        return not (v.get("year") and car.get("year")) or str(v["year"]) == str(car["year"])
    vals = [v for v in ddb.scan_filter("DEALER_Valuations", "customer_id", customer_id, env_prefix) if same(v)]
    return sorted(vals, key=lambda v: v.get("valued_at") or "")


UPGRADE_MIN_OWNERSHIP_DAYS = 365      # no "upgrade now" on a car bought this year
UPGRADE_LEASE_MONTHS_LEFT = 9         # …unless a lease is in its last months


def upgrade_window(customer_id: str | None, env_prefix: str | None = None) -> dict[str, Any] | None:
    """Whether an upgrade nudge is warranted for this customer, and on what. One rule for My Garage,
    the service lane's loyalty offer and the equity monitor: the equity must be in a car the customer
    STILL OWNS (a traded-in car's valuation is not theirs any more), and that car must be at least a
    year old or a lease in its last months — Hannah's day-0 'upgrade with $16,554 equity' was her
    traded-in Trek 4's valuation, read against the car she had just bought."""
    import datetime as _dt
    if not customer_id:
        return None
    now = _dt.datetime.now(_dt.UTC)
    # a customer already shopping (an open purchase journey) is weighing this very trade: the equity
    # is part of the deal in front of them, whatever the car's age
    shopping = any(j.get("stage") not in ("OWN", None) and (j.get("status") or "active") == "active"
                   for j in ddb.scan_filter("DEALER_Journeys", "customer_id", customer_id, env_prefix))
    if shopping:
        # the trade on the table: their current car's valuation (not one already traded in)
        vals = [v for v in ddb.scan_filter("DEALER_Valuations", "customer_id", customer_id, env_prefix)
                if (v.get("equity") or 0) > 0 and v.get("disposition") != "traded"]
        if vals:
            val = max(vals, key=lambda v: v.get("valued_at") or "")
            return {"vin": val.get("vin"), "equity": val.get("equity"), "currency": val.get("currency"),
                    "valuation_id": val.get("valuation_id"), "owned_days": None, "lease_ending": False,
                    "shopping": True, "why": "trade on the deal in progress, positive equity"}
    for car in sorted(ddb.scan_filter("DEALER_Vehicles", "customer_id", customer_id, env_prefix),
                      key=lambda v: v.get("delivered_at") or "", reverse=True):
        val = valuation_for(car.get("vin"), customer_id, env_prefix)
        if not val or (val.get("equity") or 0) <= 0 or val.get("disposition") == "traded":
            continue
        try:
            delivered = _dt.datetime.fromisoformat(str(car.get("delivered_at")))
            delivered = delivered if delivered.tzinfo else delivered.replace(tzinfo=_dt.UTC)
            owned_days = (now - delivered).days
        except (TypeError, ValueError):
            owned_days = None
        months_left = (ddb.get_item("DEALER_Customers", {"customer_id": customer_id}, env_prefix) or {}) \
            .get("current_vehicle", {}).get("months_remaining") if car.get("ownership") == "lease" else None
        lease_ending = months_left is not None and int(months_left) <= UPGRADE_LEASE_MONTHS_LEFT
        if owned_days is not None and owned_days < UPGRADE_MIN_OWNERSHIP_DAYS and not lease_ending and not shopping:
            continue
        why = "lease ending" if lease_ending else ("trade on the deal in progress" if shopping and (owned_days or 0) < UPGRADE_MIN_OWNERSHIP_DAYS
                                                  else f"owned {(owned_days or 0) // 365} year(s)")
        return {"vin": car.get("vin"), "equity": val.get("equity"), "currency": val.get("currency"),
                "valuation_id": val.get("valuation_id"), "owned_days": owned_days, "lease_ending": lease_ending,
                "shopping": shopping, "why": why + ", positive equity"}
    return None


def valuation_for(vin: str | None, customer_id: str | None, env_prefix: str | None = None) -> dict[str, Any] | None:
    """The latest A4 valuation OF THIS CAR (the one on the service visit), or None.

    Valuations are written per customer and carry the valued car's make/model/year, not
    a VIN. A customer's latest valuation is often the car they traded IN (a sale's trade),
    so service must never read it as the value of the car in the bay: match the valued
    car to this VIN's make + model (and year when both have one)."""
    if not vin or not customer_id:
        return None
    car = display_fields(ddb.get_item("DEALER_Vehicles", {"vin": vin}, env_prefix) or {}, env_prefix)
    make, model = str(car.get("make") or "").lower(), str(car.get("model") or "").lower()
    if not make or not model:
        return None
    def same(v: dict[str, Any]) -> bool:
        if v.get("vin"):
            return v["vin"] == vin
        if str(v.get("make") or "").lower() != make or str(v.get("model") or "").lower() != model:
            return False
        return not (v.get("year") and car.get("year")) or str(v["year"]) == str(car["year"])
    vals = [v for v in ddb.scan_filter("DEALER_Valuations", "customer_id", customer_id, env_prefix) if same(v)]
    return max(vals, key=lambda v: v.get("valued_at") or "") if vals else None


def for_vin(vin: str | None, env_prefix: str | None = None) -> dict[str, Any]:
    """Vehicle display block for a VIN: identity + resolved names."""
    if not vin:
        return {"vin": None}
    row = ddb.get_item("DEALER_Vehicles", {"vin": vin}, env_prefix) or {"vin": vin}
    v = display_fields(row, env_prefix)
    return {k: v.get(k) for k in ("vin", "year", "make", "model", "model_id", "trim_id", "odometer")}
