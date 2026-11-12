"""F&I Desk (spec §5.13, G-02) — queue, deal worksheet, menu, lender decision,
compliance gate, push to DMS, and stage advancement.

  fni_queue(scope, market)              -> FINANCE/AGREE journeys + readiness badges + KPIs
  deal_worksheet(deal_id)               -> vehicle, price, trade equity, incentives,
                                           lender offers side-by-side, payment scenarios,
                                           F&I menu with attach suggestions, compliance panel
  update_deal(deal_id, patch, actor)    -> term / down / selected offer -> recomputed payment
  set_menu(deal_id, accepted, actor)    -> F&I product attach / detach
  request_lender_decision(deal_id, ...) -> sandbox F&I connector: OFAC + Red Flags + lender decisions
  send_adverse_action(deal_id, actor)   -> records the adverse-action notice as sent
  push_deal_to_dms(deal_id, actor)      -> DMS push + booking (blocked unless the gate is clear)
  advance_from_desk(journey_id, actor)  -> advance the journey one stage, supplying the
                                           context this desk evidences; AGREE->DELIVER also
                                           schedules the delivery and hands off to delivery
  ensure_deal(journey_id, actor)        -> the DEALER_Deals row for a journey, desked on first
                                           touch from its saved build (price, tax, incentives)

Every figure comes from DEALER_Deals / FinanceOffers / FIProducts / Valuations /
PLAT_Configurations / OEM_Markets / OEM_StateRules rows and carries a
`source_ref`. The F&I connector is a sandbox (CDK F&I is the live path); its
lender decisions are deterministic functions of the deal so results are
reproducible, and they are recorded exactly as a connector would record them.

Two rules keep the numbers on the worksheet agreeing with each other:
  * the deal total reconciles to the build: vehicle + estimated tax + doc fee −
    point-of-sale incentives (the worksheet shows that arithmetic as `price.breakdown`);
  * an OEM APR program is a rate, not a discount: it shows with the tier it requires and
    whether it applied to the captive lender's offer, never as a price line with no amount.
A deal cannot be pushed to the DMS, and so a journey cannot leave AGREE, without the
VIN it is written against (spec §4.2 `vin` set at AGREE).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
from typing import Any

from ..agents import deal as a3_deal
from ..common import ddb
from ..events import bus as _bus
from ..finance.prequalify import _monthly

_DEALS = "DEALER_Deals"
_OFFERS = "DEALER_FinanceOffers"
_FIP = "DEALER_FIProducts"
_JOURNEYS = "DEALER_Journeys"

FI_STAGES = ("FINANCE", "TRADE_IN", "AGREE")

# The menu the desk presents (§5.13: GAP, VSC, tire & wheel, prepaid maintenance,
# charger install). Prices come from DEALER_FIProducts when the deal carries the
# product; otherwise the product is offered at the store's median price for it.
MENU = (
    ("vsc", "Vehicle service contract"),
    ("gap", "GAP protection"),
    ("tire_wheel", "Tire & wheel"),
    ("maint", "Prepaid maintenance"),
    ("charger", "Home charger install"),
    ("appearance", "Appearance protection"),
)

# Sandbox lender panel: (lender suffix, APR spread vs the deal tier, max LTV).
_LENDERS = (("captive", 0.0, 1.25), ("credit_union", 0.6, 1.10), ("national_bank", 1.1, 1.20))


class NotFound(Exception):
    pass


class GateBlocked(Exception):
    """Raised when an action needs the credit-compliance gate (§2.1 AGREE)."""

    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__(f"credit-compliance gate not clear: {', '.join(missing)}")


class InvalidState(Exception):
    pass


class NoVehicle(Exception):
    """Raised when a deal would be pushed without the VIN it is written against."""


_DEFAULT_TERM = 60
# Credit-tier rank for "requires Prime or better" style program rules (US + DE tier names).
_TIER_RANK = {"super prime": 0, "sehr gut": 0, "prime": 1, "gut": 1, "near prime": 2, "befriedigend": 2,
              "subprime": 3, "ausreichend": 3}


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _today() -> str:
    return _dt.datetime.now(_dt.timezone.utc).date().isoformat()


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None and not isinstance(v, bool) else None
    except (TypeError, ValueError):
        return None


def _deal(deal_id: str, env_prefix: str | None) -> dict[str, Any]:
    d = ddb.get_item(_DEALS, {"deal_id": deal_id}, env_prefix)
    if d is None:
        raise NotFound(f"deal {deal_id} not found")
    return d


def _journey_for(deal: dict[str, Any], env_prefix: str | None) -> dict[str, Any] | None:
    jid = deal.get("journey_id")
    return ddb.get_item(_JOURNEYS, {"journey_id": jid}, env_prefix) if jid else None


def deal_for_journey(journey: dict[str, Any], env_prefix: str | None = None) -> dict[str, Any] | None:
    # one resolution for desk and gate (see a3_deal._deal_for_journey)
    return a3_deal._deal_for_journey(journey, env_prefix)


def _customer_name(cid: str | None, env_prefix: str | None) -> str | None:
    c = ddb.get_item("DEALER_Customers", {"customer_id": cid}, env_prefix) if cid else None
    if not c:
        return None
    return " ".join(filter(None, [c.get("given_name"), c.get("family_name")])) or cid


def _latest_valuation(cid: str | None, env_prefix: str | None) -> dict[str, Any] | None:
    vals = ddb.scan_filter("DEALER_Valuations", "customer_id", cid, env_prefix) if cid else []
    return max(vals, key=lambda v: v.get("valued_at") or "") if vals else None


def _record_trade(deal: dict[str, Any], env_prefix: str | None) -> None:
    """Put the customer's latest trade valuation on the deal (once): the deal is then
    financed net of its equity. Positive equity lowers the amount financed; negative
    equity is rolled in. The worksheet's trade block shows the same valuation."""
    if deal.get("trade_valuation_id"):
        return
    val = _latest_valuation(deal.get("customer_id"), env_prefix)
    if val and _num(val.get("equity")) is not None:
        deal["trade_valuation_id"] = val.get("valuation_id")
        deal["trade_equity"] = round(_num(val.get("equity")) or 0.0, 2)


def _principal(deal: dict[str, Any]) -> float:
    """Amount financed: price − down payment − trade equity recorded on the deal."""
    price = _num(deal.get("price_total")) or 0
    down = _num(deal.get("down_payment")) or 0
    equity = (_num(deal.get("trade_equity")) or 0) if deal.get("trade_valuation_id") else 0
    return round(max(0.0, price - down - equity), 2)


def _budget_check(deal: dict[str, Any], env_prefix: str | None) -> dict[str, Any] | None:
    """Does the deal's payment fit the customer's stated monthly ceiling? None without one."""
    cust = ddb.get_item("DEALER_Customers", {"customer_id": deal.get("customer_id")}, env_prefix) or {}
    ceiling = _num((cust.get("budget") or {}).get("monthly_max"))
    pay = _num(deal.get("monthly_payment"))
    if ceiling is None or pay is None:
        return None
    return {"monthly_max": ceiling, "monthly_payment": pay, "fits": pay <= ceiling,
            "over_by": round(max(0.0, pay - ceiling), 2), "source_ref": f"DEALER_Customers:{deal.get('customer_id')}.budget"}


def _reserved_vin(journey_id: str, env_prefix: str | None) -> dict[str, Any] | None:
    rows = ddb.scan_filter("DEALER_Inventory", "reserved_for_journey_id", journey_id, env_prefix)
    return rows[0] if rows else None


def _products(deal_id: str, env_prefix: str | None) -> list[dict[str, Any]]:
    return ddb.scan_filter(_FIP, "deal_id", deal_id, env_prefix)


def lender_offers(deal: dict[str, Any], env_prefix: str | None = None) -> list[dict[str, Any]]:
    """Every lender response on a deal. DEALER_FinanceOffers is keyed by deal_id
    (one presented/accepted offer per deal), so the full lender-panel response
    lives on the deal row (`lender_decisions`); the offer table carries the one
    the desk presents. Falls back to the offer row for deals desked elsewhere."""
    rows = list(deal.get("lender_decisions") or [])
    known = {r.get("offer_id") for r in rows}
    for o in ddb.scan_filter(_OFFERS, "deal_id", deal["deal_id"], env_prefix):
        if o.get("offer_id") not in known and not any(r.get("lender") == o.get("lender") for r in rows):
            rows.append({**o, "decision": o.get("decision") or o.get("status")})
    return rows


# --- KPIs --------------------------------------------------------------------

def desk_kpis(dealer_ids: set[str] | None, env_prefix: str | None = None) -> dict[str, Any]:
    """Attach rate, PVR, and products per deal over booked deals (§1.3, §5.13)."""
    deals = [d for d in ddb.scan_all(_DEALS, env_prefix)
             if dealer_ids is None or d.get("dealer_id") in dealer_ids]
    booked = [d for d in deals if d.get("status") == "booked"]
    booked_ids = {d["deal_id"] for d in booked}
    accepted = [p for p in ddb.scan_all(_FIP, env_prefix) if p.get("deal_id") in booked_ids and p.get("accepted")]
    with_product = {p["deal_id"] for p in accepted}
    revenue = sum(_num(p.get("price")) or 0 for p in accepted)
    n = len(booked)
    mix: dict[str, int] = {}
    for p in accepted:
        mix[p.get("code") or "other"] = mix.get(p.get("code") or "other", 0) + 1
    return {
        "booked_deals": n,
        "attach_rate": round(len(with_product) / n, 3) if n else None,
        "pvr": round(revenue / n, 2) if n else None,
        "products_per_deal": round(len(accepted) / n, 2) if n else None,
        "product_mix": mix,
        "in_desk": sum(1 for d in deals if d.get("status") in ("desking", "pending", "signed")),
        "currency": (booked[0].get("currency") if booked else None),
        "source_ref": "F&I.desk_kpis (DEALER_Deals booked × DEALER_FIProducts accepted)",
    }


# --- queue -------------------------------------------------------------------

def readiness(journey: dict[str, Any], deal: dict[str, Any] | None, env_prefix: str | None) -> dict[str, Any]:
    jid = journey["journey_id"]
    offers = lender_offers(deal, env_prefix) if deal else []
    gate = a3_deal.compliance_gate(journey, deal=deal or {}, env_prefix=env_prefix)
    val = _latest_valuation(journey.get("customer_id"), env_prefix)
    ctx = journey.get("context") or {}
    return {
        "credit_app_in": bool(offers),
        "lender_decision": any(o.get("decision") in ("approved", "conditioned") for o in offers),
        "trade_agreed": bool(ctx.get("valuation") or ctx.get("trade_condition")) or val is not None,
        "vin_reserved": _reserved_vin(jid, env_prefix) is not None,
        "compliance_clear": gate["ok"],
        "pushed_to_dms": bool(deal and deal.get("status") in ("pushed", "booked")),
    }


def fni_queue(scope: dict[str, Any], market_id: str | None = None,
              env_prefix: str | None = None) -> dict[str, Any]:
    market = market_id or scope.get("market_id") or "us"
    dealers = None if scope.get("is_oem") else set(scope.get("dealer_ids") or [])
    journeys = [j for j in ddb.scan_filter(_JOURNEYS, "market_id", market, env_prefix)
                if j.get("stage") in FI_STAGES and (dealers is None or j.get("dealer_id") in dealers)]
    rows = []
    names = {m["model_id"]: m.get("name") for m in ddb.scan_all("OEM_CatalogModels", env_prefix)}
    for j in journeys:
        d = deal_for_journey(j, env_prefix)
        r = readiness(j, d, env_prefix)
        rows.append({
            "journey_id": j["journey_id"], "stage": j.get("stage"), "dealer_id": j.get("dealer_id"),
            "customer_id": j.get("customer_id"), "customer_name": _customer_name(j.get("customer_id"), env_prefix),
            "owner_user_id": j.get("owner_user_id"), "model_id": j.get("primary_model_id"),
            "model_name": names.get(j.get("primary_model_id")),
            "deal_id": (d or {}).get("deal_id"), "deal_status": (d or {}).get("status"),
            "price_total": _num((d or {}).get("price_total")), "currency": (d or {}).get("currency"),
            "readiness": r, "ready_count": sum(1 for v in r.values() if v),
            "updated_at": j.get("updated_at") or j.get("last_event_at"),
        })
    order = {s: i for i, s in enumerate(("AGREE", "TRADE_IN", "FINANCE"))}
    rows.sort(key=lambda r: (order.get(r["stage"], 9), -r["ready_count"]))
    return {"market_id": market, "count": len(rows), "queue": rows,
            "kpis": desk_kpis(dealers, env_prefix),
            "source_ref": "F&I.fni_queue (journeys in FINANCE/TRADE_IN/AGREE)"}


# --- worksheet -----------------------------------------------------------------

def _market(market: str, env_prefix: str | None) -> dict[str, Any]:
    return ddb.get_item("OEM_Markets", {"market_id": market}, env_prefix) or {}


def _region_code(journey: dict[str, Any] | None, deal: dict[str, Any], env_prefix: str | None) -> str | None:
    cid = deal.get("customer_id") or (journey or {}).get("customer_id")
    c = ddb.get_item("DEALER_Customers", {"customer_id": cid}, env_prefix) if cid else None
    if c:
        return c.get("region_code") or (c.get("address") or {}).get("state")
    return None


def disclosures(market: str, region_code: str | None, deal: dict[str, Any], env_prefix: str | None) -> dict[str, Any]:
    """TILA/Reg Z (or market equivalent) + state doc-fee / tax rules (§5.13 compliance panel)."""
    mkt = _market(market, env_prefix)
    rule = ddb.get_item("OEM_StateRules", {"region_code": region_code}, env_prefix) if region_code else None
    items = [{"name": d, "source_ref": f"OEM_Markets:{market}.regulatory_disclosures"}
             for d in (mkt.get("regulatory_disclosures") or [])]
    state = None
    if rule:
        state = {"region_code": region_code, "region_name": rule.get("region_name"),
                 "sales_tax_pct": _num(rule.get("sales_tax_pct")),
                 "avg_local_tax_pct": _num(rule.get("avg_local_tax_pct")),
                 "doc_fee_cap": _num(rule.get("doc_fee_cap")),
                 "ev_annual_fee": _num(rule.get("ev_annual_fee")),
                 "note": rule.get("vehicle_tax_note"),
                 "source_ref": f"OEM_StateRules:{region_code}"}
    doc_fee = _num(deal.get("doc_fee"))
    warnings = []
    if state and state.get("doc_fee_cap") is not None and doc_fee is not None and doc_fee > state["doc_fee_cap"]:
        warnings.append(f"doc fee {doc_fee} exceeds the {region_code} cap {state['doc_fee_cap']}")
    return {"market": market, "items": items, "state": state, "warnings": warnings}


def _scenarios(deal: dict[str, Any], market: str, env_prefix: str | None) -> list[dict[str, Any]]:
    """Term/rate/payment grid across the market's retail terms.

    The deal's APR is the rate for the deal's own term; other terms move along the
    captive rate card (shorter terms buy down the rate, longer terms cost more), so the
    grid is a real trade-off rather than one rate copied four times."""
    mkt = _market(market, env_prefix)
    retail = next((p for p in (mkt.get("finance_products") or []) if p.get("code") == "RETAIL"), {})
    terms = retail.get("terms_months") or [36, 48, 60, 72]
    apr = _num(deal.get("apr_pct")) or 0
    sel_term = int(_num(deal.get("term_months")) or 60)
    principal = _principal(deal)
    out = []
    for t in terms:
        t = int(t)
        step = _RATE_CARD_STEP.get(t, 0.0) - _RATE_CARD_STEP.get(sel_term, 0.0)
        t_apr = round(max(0.0, apr + step), 2) if apr else apr
        out.append({"term_months": t, "apr_pct": t_apr, "amount_financed": round(principal, 2),
                    "monthly_payment": _monthly(principal, t_apr, t),
                    "total_interest": round(_monthly(principal, t_apr, t) * t - principal, 2) if principal else 0.0,
                    "selected": t == sel_term,
                    "source_ref": f"F&I.scenarios (deal APR {apr}% at {sel_term} mo ± captive rate card × OEM_Markets:{market} retail terms)"})
    return out


# Captive rate card: APR adjustment by term relative to the 60-month rate (sandbox; the
# live connector returns the lender's own card).
_RATE_CARD_STEP = {24: -0.75, 36: -0.50, 48: -0.25, 60: 0.0, 72: 0.50, 84: 1.00}


def _menu(deal: dict[str, Any], journey: dict[str, Any] | None, env_prefix: str | None) -> list[dict[str, Any]]:
    have = {p.get("code"): p for p in _products(deal["deal_id"], env_prefix)}
    # store median price per product (fallback when this deal doesn't carry it yet)
    all_p = [p for p in ddb.scan_all(_FIP, env_prefix) if p.get("currency") == deal.get("currency")]
    med: dict[str, float] = {}
    for code, _ in MENU:
        prices = sorted(_num(p.get("price")) or 0 for p in all_p if p.get("code") == code and _num(p.get("price")))
        if prices:
            med[code] = prices[len(prices) // 2]
    suggest = {}
    if journey:
        cs = a3_deal.get_cross_sell(journey, env_prefix)
        suggest = {r["code"]: r for r in cs.get("recommendations", [])}
    is_ev = _is_ev_deal(deal, journey, env_prefix)
    out = []
    for code, name in MENU:
        if code == "charger" and not is_ev:
            continue
        p = have.get(code)
        price = _num(p.get("price")) if p else med.get(code)
        s = suggest.get(code) or (suggest.get("tire_wheel") if code == "tire_wheel" else None)
        weight = s["weight"] if s else (0.8 if code == "charger" and is_ev else None)
        apr = _num(deal.get("apr_pct")) or 0
        term = int(_num(deal.get("term_months")) or 60)
        out.append({
            "code": code, "name": (p or {}).get("name") or name, "price": price,
            "monthly_delta": _monthly(price, apr, term) if price else None,
            "currency": deal.get("currency"), "attached": bool(p and p.get("accepted")),
            "on_deal": p is not None,
            "ai_attach": ({"likelihood": weight, "why": (s or {}).get("why") or
                           "EV buyers typically install a Level 2 home charger"} if weight else None),
            "source_ref": (p or {}).get("source_ref") or ("DEALER_FIProducts median (store)" if price else None),
        })
    return out


def _is_ev_deal(deal: dict[str, Any], journey: dict[str, Any] | None, env_prefix: str | None) -> bool:
    mid = (journey or {}).get("primary_model_id")
    if not mid:
        return False
    m = ddb.get_item("OEM_CatalogModels", {"model_id": mid}, env_prefix) or {}
    pts = m.get("powertrains") or ([m["powertrain"]] if m.get("powertrain") else [])
    return any(str(p).upper() in ("EV", "BEV") for p in pts)


def _vehicle(journey: dict[str, Any] | None, env_prefix: str | None) -> dict[str, Any] | None:
    if not journey:
        return None
    inv = _reserved_vin(journey["journey_id"], env_prefix)
    cfgs = ddb.scan_filter("PLAT_Configurations", "journey_id", journey["journey_id"], env_prefix)
    cfg = max(cfgs, key=lambda c: c.get("saved_at") or "") if cfgs else None
    model = ddb.get_item("OEM_CatalogModels", {"model_id": (cfg or {}).get("model_id") or journey.get("primary_model_id")},
                         env_prefix) if ((cfg or {}).get("model_id") or journey.get("primary_model_id")) else None
    trim_id = (cfg or {}).get("trim_id") or journey.get("primary_trim_id")
    trim = ddb.get_item("OEM_Catalog", {"trim_id": trim_id}, env_prefix) if trim_id else None
    return {
        "model_id": (cfg or {}).get("model_id") or journey.get("primary_model_id"),
        "model_name": (model or {}).get("name") or (model or {}).get("model_name"),
        "trim_id": trim_id,
        # the screen shows the trim's name ("Long Range"), never its catalog code
        "trim_name": (inv or {}).get("trim_name") or (trim or {}).get("trim_name"),
        "color": (cfg or {}).get("color"),
        "vin": (inv or {}).get("vin"), "stock_number": (inv or {}).get("stock_number"),
        "price_vehicle": _num((cfg or {}).get("price_vehicle")),
        "price_tax_est": _num((cfg or {}).get("price_tax_est")),
        "incentives_applied": (cfg or {}).get("incentives_applied") or [],
        "incentives_total": _num((cfg or {}).get("incentives_total")),
        "config_id": (cfg or {}).get("config_id"),
        "source_ref": (cfg or {}).get("source_ref") or "PLAT_Configurations",
    }


def _incentive_kind(row: dict[str, Any]) -> str:
    """price (cash off the vehicle) · apr (a captive-finance rate program) · informational
    (real money that is not a vehicle-price reduction: a tax deduction, a charger rebate)."""
    from ..agents.discovery import _reduces_vehicle_price
    if row.get("apr_special"):
        return "apr"
    return "price" if _reduces_vehicle_price(row) else "informational"


def _apr_special(market: str, powertrain: str | None, env_prefix: str | None) -> dict[str, Any] | None:
    """The active OEM APR program that covers this powertrain today, with its rate card, or None."""
    today = _today()
    for inc in ddb.scan_filter("OEM_Incentives", "market_id", market, env_prefix):
        sp = inc.get("apr_special")
        if not sp or inc.get("status") != "active":
            continue
        pts = [str(p).upper() for p in (inc.get("powertrains") or [])]
        if pts and powertrain and str(powertrain).upper() not in pts:
            continue
        if (inc.get("valid_from") and today < inc["valid_from"]) or (inc.get("valid_to") and today > inc["valid_to"]):
            continue
        return {"incentive_id": inc.get("incentive_id"), "name": inc.get("name"),
                "apr_by_term": {int(k): _num(v) for k, v in (sp.get("apr_by_term") or {}).items() if _num(v) is not None},
                "min_tier": sp.get("min_tier"), "lender": sp.get("lender") or "captive",
                "valid_to": inc.get("valid_to"), "source_ref": inc.get("source_ref") or f"OEM_Incentives:{inc.get('incentive_id')}"}
    return None


def _special_applies(special: dict[str, Any] | None, tier: str | None, term: int) -> tuple[float | None, str]:
    """(promotional APR for this term, why / why not). The program's own terms decide: the
    buyer's credit tier must meet `min_tier` and the term must be on the rate card."""
    if not special:
        return None, "no APR program active for this vehicle today"
    need = (special.get("min_tier") or "").lower()
    have = (tier or "").lower()
    if need and (have not in _TIER_RANK or _TIER_RANK[have] > _TIER_RANK.get(need, 99)):
        return None, f"requires {special['min_tier']} or better — this deal tiered {tier or 'unknown'}"
    rate = special.get("apr_by_term", {}).get(int(term))
    if rate is None:
        terms = ", ".join(str(t) for t in sorted(special.get("apr_by_term", {})))
        return None, f"program terms are {terms} months — this deal is {term} months"
    return rate, f"{rate}% APR for {term} months through {special.get('lender', 'captive')} finance (tier {tier})"


def _incentives(ids: list[str], env_prefix: str | None, deal: dict[str, Any] | None = None,
                market: str = "us", powertrain: str | None = None) -> list[dict[str, Any]]:
    """The incentives on the saved build, each labelled by what it does to THIS deal, so the
    worksheet never lists an APR program as an active price incentive with no amount."""
    tier = (deal or {}).get("credit_tier")
    term = int(_num((deal or {}).get("term_months")) or _DEFAULT_TERM)
    special = _apr_special(market, powertrain, env_prefix) if deal is not None else None
    out = []
    for iid in ids:
        row = ddb.get_item("OEM_Incentives", {"incentive_id": iid}, env_prefix) or {}
        kind = _incentive_kind(row) if row else "price"
        item = {"incentive_id": iid, "name": row.get("name") or iid, "kind": kind,
                "amount": _num(row.get("amount") or row.get("amount_usd") or row.get("amount_eur")),
                "status": row.get("status"),
                "source_ref": row.get("source_ref") or f"OEM_Incentives:{iid}"}
        if kind == "apr":
            rate, why = _special_applies(special if special and special["incentive_id"] == iid else None, tier, term)
            # "applied" means the presented / selected lender offer IS the program rate
            # (request_lender_decision stamps `apr_program` on the captive offer, _present_offer
            # copies it onto the deal when that offer is the one on the worksheet)
            applied = ((deal or {}).get("apr_program") or {}).get("incentive_id") == iid
            if deal is None:
                why = "rate program (applies to the captive lender's offer)"
            elif applied:
                why = f"applied — {(deal or {}).get('apr_pct')}% APR on the {(deal or {}).get('lender')} offer"
            elif rate is not None and not (deal or {}).get("lender_decisions"):
                why = f"{why}; applies when the credit application comes back"
            item.update({"apr_pct": rate, "applies": rate is not None, "applied_to_deal": applied, "note": why})
        elif kind == "informational":
            item["note"] = "informational — not a vehicle-price reduction"
        else:
            item["note"] = "reduces the vehicle price"
        out.append(item)
    return out


def _price_breakdown(deal: dict[str, Any], vehicle: dict[str, Any] | None) -> dict[str, Any]:
    """vehicle + est. tax + doc fee − point-of-sale incentives = deal total, and whether the deal
    row's own total reconciles to it (a mismatch is shown, never papered over)."""
    v = vehicle or {}
    veh, tax = _num(v.get("price_vehicle")), _num(v.get("price_tax_est"))
    inc, fee = _num(v.get("incentives_total")) or 0.0, _num(deal.get("doc_fee")) or 0.0
    total = _num(deal.get("price_total"))
    computed = round((veh or 0) + (tax or 0) + fee - inc, 2) if veh is not None else None
    return {"vehicle_price": veh, "tax_est": tax, "doc_fee": fee, "incentives_total": inc,
            "computed_total": computed, "deal_total": total,
            "reconciles": (computed is not None and total is not None and abs(computed - total) < 1.0),
            "formula": "vehicle price + est. tax + doc fee − point-of-sale incentives",
            "source_ref": v.get("source_ref") or "PLAT_Configurations (saved build)"}


def _powertrain_of(vehicle: dict[str, Any] | None, env_prefix: str | None) -> str | None:
    tid = (vehicle or {}).get("trim_id")
    if tid:
        t = ddb.get_item("OEM_Catalog", {"trim_id": tid}, env_prefix) or {}
        if t.get("powertrain"):
            return str(t["powertrain"])
    mid = (vehicle or {}).get("model_id")
    m = ddb.get_item("OEM_CatalogModels", {"model_id": mid}, env_prefix) if mid else None
    pts = (m or {}).get("powertrains") or ([m["powertrain"]] if m and m.get("powertrain") else [])
    return str(pts[0]) if pts else None


def deal_worksheet(deal_id: str, env_prefix: str | None = None) -> dict[str, Any]:
    deal = _deal(deal_id, env_prefix)
    journey = _journey_for(deal, env_prefix)
    market = (deal.get("market_id") or (journey or {}).get("market_id") or "us").lower()
    vehicle = _vehicle(journey, env_prefix)
    val = _latest_valuation(deal.get("customer_id"), env_prefix)
    offers = sorted(lender_offers(deal, env_prefix),
                    key=lambda o: (o.get("decision") == "declined", _num(o.get("monthly_payment")) or 9e9))
    gate = a3_deal.compliance_gate(journey or {}, deal=deal, env_prefix=env_prefix)
    menu = _menu(deal, journey, env_prefix)
    attached_total = sum(m["price"] or 0 for m in menu if m["attached"])
    tier, _apr = _tier_apr(deal, market, env_prefix)
    deal_t = {**deal, "credit_tier": deal.get("credit_tier") or tier}
    return {
        "deal_id": deal_id, "journey_id": deal.get("journey_id"), "market_id": market,
        "dealer_id": deal.get("dealer_id"), "customer_id": deal.get("customer_id"),
        "customer_name": _customer_name(deal.get("customer_id"), env_prefix),
        "stage": (journey or {}).get("stage"), "status": deal.get("status"),
        "currency": deal.get("currency"),
        "vehicle": vehicle,
        "incentives": _incentives((vehicle or {}).get("incentives_applied") or [], env_prefix, deal_t, market,
                                  _powertrain_of(vehicle, env_prefix)),
        "price": {"price_total": _num(deal.get("price_total")), "down_payment": _num(deal.get("down_payment")),
                  "apr_pct": _num(deal.get("apr_pct")), "term_months": _num(deal.get("term_months")),
                  "monthly_payment": _num(deal.get("monthly_payment")),
                  "amount_financed": _principal(deal),
                  "trade_equity_applied": (_num(deal.get("trade_equity")) if deal.get("trade_valuation_id") else None),
                  "budget": _budget_check(deal, env_prefix),
                  "credit_tier": deal_t.get("credit_tier"),
                  "apr_basis": ("lender offer" if deal.get("selected_offer_id") or deal.get("lender_decisions")
                                else f"{deal_t.get('credit_tier') or 'tier'} rate-card estimate — not a credit decision"),
                  "apr_program": deal.get("apr_program"),
                  "fi_products_total": round(attached_total, 2),
                  "breakdown": _price_breakdown(deal, vehicle),
                  "source_ref": deal.get("source_ref") or f"DEALER_Deals:{deal_id}"},
        "trade": ({"valuation_id": val.get("valuation_id"), "vehicle": " ".join(str(x) for x in (val.get("year"), val.get("make"), val.get("model")) if x),
                   "mileage": _num(val.get("mileage")), "trade_value": _num(val.get("trade_value")),
                   "payoff": _num(val.get("payoff")), "equity": _num(val.get("equity")),
                   "valued_at": val.get("valued_at"), "source_ref": val.get("source_ref") or f"DEALER_Valuations:{val.get('valuation_id')}"}
                  if val else None),
        "lender_offers": [{"offer_id": o.get("offer_id"), "lender": o.get("lender"), "decision": o.get("decision") or o.get("status"),
                           "apr_pct": _num(o.get("apr_pct")), "term_months": _num(o.get("term_months")),
                           "monthly_payment": _num(o.get("monthly_payment")), "amount_financed": _num(o.get("amount_financed")),
                           "stipulations": o.get("stipulations") or [], "selected": o.get("offer_id") == deal.get("selected_offer_id"),
                           "source_ref": o.get("source_ref")} for o in offers],
        "scenarios": _scenarios(deal, market, env_prefix),
        "menu": menu,
        "compliance": {"gate": gate, "disclosures": disclosures(market, _region_code(journey, deal, env_prefix), deal, env_prefix)},
        "readiness": readiness(journey, deal, env_prefix) if journey else None,
        "dms": {"pushed_at": deal.get("pushed_at"), "dms_deal_number": deal.get("dms_deal_number"), "booked_at": deal.get("booked_at")},
        "source_ref": "F&I.deal_worksheet (§5.13)",
    }


# --- writes ------------------------------------------------------------------------

def _emit(etype: str, deal: dict[str, Any], actor: str, env_prefix: str | None, **extra: Any) -> dict[str, Any]:
    return _bus.emit(etype, {"deal_id": deal["deal_id"], "journey_id": deal.get("journey_id"),
                             "dealer_id": deal.get("dealer_id"), "customer_id": deal.get("customer_id"), **extra},
                     actor=actor, resource_url=f"/r/deal/{deal['deal_id']}", env_prefix=env_prefix)


def update_deal(deal_id: str, patch: dict[str, Any], actor: str, env_prefix: str | None = None) -> dict[str, Any]:
    deal = dict(_deal(deal_id, env_prefix))
    if deal.get("status") in ("pushed", "booked"):
        raise InvalidState(f"deal {deal_id} is {deal['status']} in the DMS; unwind it there first")
    if "selected_offer_id" in patch and patch["selected_offer_id"]:
        offer = next((o for o in lender_offers(deal, env_prefix) if o.get("offer_id") == patch["selected_offer_id"]), None)
        if not offer:
            raise ValueError("selected_offer_id is not an offer on this deal")
        if offer.get("decision") == "declined":
            raise InvalidState("a declined offer cannot be selected")
        deal["selected_offer_id"] = offer["offer_id"]
        deal["apr_pct"] = _num(offer.get("apr_pct"))
        deal["term_months"] = _num(offer.get("term_months")) or deal.get("term_months")
        deal["lender"] = offer.get("lender")
        _present_offer(deal, offer, "accepted", env_prefix)
    for k in ("term_months", "down_payment", "doc_fee"):
        if k in patch and patch[k] is not None:
            v = _num(patch[k])
            if v is None or v < 0:
                raise ValueError(f"{k} must be a non-negative number")
            deal[k] = int(v) if k == "term_months" else v
    principal = _principal(deal)
    deal["monthly_payment"] = _monthly(principal, _num(deal.get("apr_pct")) or 0, int(_num(deal.get("term_months")) or 60))
    deal["updated_at"] = _now()
    deal["updated_by"] = actor
    ddb.put_item(_DEALS, deal, env_prefix)
    ev = _emit("deal.structured", deal, actor, env_prefix, monthly_payment=deal["monthly_payment"])
    return {"deal_id": deal_id, "monthly_payment": deal["monthly_payment"], "term_months": deal.get("term_months"),
            "apr_pct": deal.get("apr_pct"), "down_payment": deal.get("down_payment"),
            "selected_offer_id": deal.get("selected_offer_id"), "bus_event": ev,
            "source_ref": "F&I.update_deal (amortization over deal APR/term)"}


def set_menu(deal_id: str, accepted: list[str], actor: str, env_prefix: str | None = None) -> dict[str, Any]:
    deal = _deal(deal_id, env_prefix)
    if deal.get("status") in ("pushed", "booked"):
        raise InvalidState(f"deal {deal_id} is already {deal['status']}")
    valid = {c for c, _ in MENU}
    bad = [c for c in accepted if c not in valid]
    if bad:
        raise ValueError(f"unknown F&I product code(s): {bad}")
    journey = _journey_for(deal, env_prefix)
    menu = {m["code"]: m for m in _menu(deal, journey, env_prefix)}
    have = {p.get("code"): p for p in _products(deal_id, env_prefix)}
    for code in valid:
        want = code in accepted
        row = have.get(code)
        if row is None and not want:
            continue
        if row is None:
            if code not in menu or menu[code]["price"] is None:
                raise ValueError(f"{code} has no price on file for this store")
            row = {"fi_product_id": f"FIP-{deal_id}-{code}", "deal_id": deal_id, "code": code,
                   "name": menu[code]["name"], "price": menu[code]["price"], "currency": deal.get("currency"),
                   "source_ref": menu[code]["source_ref"]}
        row = dict(row)
        row["accepted"] = want
        row["decided_at"] = _now()
        row["decided_by"] = actor
        ddb.put_item(_FIP, row, env_prefix)
    ev = _emit("deal.structured", deal, actor, env_prefix, fi_products=sorted(accepted))
    return {"deal_id": deal_id, "attached": sorted(accepted),
            "fi_products_total": round(sum(menu[c]["price"] or 0 for c in accepted if c in menu), 2),
            "bus_event": ev}


def _tier_apr(deal: dict[str, Any], market: str, env_prefix: str | None) -> tuple[str | None, float]:
    tiers = _market(market, env_prefix).get("credit_tiers") or []
    # the tier rate card the deal was desked on; deal["apr_pct"] becomes the PRESENTED offer's
    # rate (a captive special can be 2.9%), so pricing the panel off it moved every lender
    apr = _num(deal.get("rate_card_apr"))
    if apr is None and deal.get("credit_tier"):
        apr = next((_num(t.get("apr")) for t in tiers if t.get("tier") == deal["credit_tier"]), None)
    if apr is None:
        apr = _num(deal.get("apr_pct"))
    if apr is None and tiers:
        apr = _num(tiers[min(1, len(tiers) - 1)].get("apr")) or 6.4
    best = min(tiers, key=lambda t: abs((_num(t.get("apr")) or 0) - (apr or 0))) if tiers else None
    return (best or {}).get("tier"), apr or 6.4


def _present_offer(deal: dict[str, Any], offer: dict[str, Any], status: str, env_prefix: str | None) -> None:
    """Write the offer the desk presents/accepts to DEALER_FinanceOffers (one per deal) and
    make the deal's own figures match it — the worksheet's monthly, APR and term ARE the
    presented offer's, never a stale number next to a different lender figure."""
    keep = ("offer_id", "lender", "apr_pct", "term_months", "monthly_payment", "amount_financed",
            "down_payment", "currency", "decision", "stipulations", "source_ref")
    ddb.put_item(_OFFERS, {**{k: offer.get(k) for k in keep}, "deal_id": deal["deal_id"],
                           "journey_id": deal.get("journey_id"), "status": status, "presented_at": _now()},
                 env_prefix)
    if offer.get("apr_pct") is not None:
        deal["apr_pct"] = offer["apr_pct"]
        deal["term_months"] = offer.get("term_months") or deal.get("term_months")
        deal["monthly_payment"] = offer.get("monthly_payment")
        deal["lender"] = offer.get("lender")
        deal["selected_offer_id"] = offer.get("offer_id")
        # the worksheet says whether the OEM APR program is the rate on this deal
        if offer.get("apr_program"):
            deal["apr_program"] = {**offer["apr_program"], "offer_id": offer.get("offer_id")}
        else:
            deal.pop("apr_program", None)
        ddb.put_item(_DEALS, deal, env_prefix)


def request_lender_decision(deal_id: str, actor: str, env_prefix: str | None = None) -> dict[str, Any]:
    """Sandbox F&I connector: run OFAC + Red Flags (identity) when not yet
    recorded, then submit the credit application to the lender panel and record
    each lender's decision as a DEALER_FinanceOffers row. Decisions are a
    deterministic function of the deal (tier APR, loan-to-value), so a rerun
    gives the same answer. If every lender declines, an adverse-action notice
    becomes REQUIRED and the AGREE gate stays blocked until it is sent."""
    deal = dict(_deal(deal_id, env_prefix))
    journey = _journey_for(deal, env_prefix) or {}
    market = (deal.get("market_id") or journey.get("market_id") or "us").lower()
    mkt = _market(market, env_prefix)
    brand = mkt.get("brand") if isinstance(mkt.get("brand"), dict) else {}
    lender_names = {"captive": brand.get("captive_finance") or "Captive Finance",
                    "credit_union": "Regional Credit Union", "national_bank": "National Bank"}
    price = _num(deal.get("price_total")) or 0
    down = _num(deal.get("down_payment")) or 0
    _record_trade(deal, env_prefix)          # the trade on the deal is financed net of its equity
    principal = _principal(deal)
    vehicle = _vehicle(journey, env_prefix)
    vehicle_value = (vehicle or {}).get("price_vehicle") or price
    ltv = principal / vehicle_value if vehicle_value else 1.0
    tier, base_apr = _tier_apr(deal, market, env_prefix)
    deal.setdefault("rate_card_apr", base_apr)   # a rerun prices the panel the same way
    term = int(_num(deal.get("term_months")) or _DEFAULT_TERM)
    ts = _now()
    # the OEM APR program, if one covers this vehicle today: the captive lender quotes its rate
    # when the buyer's tier qualifies (the program's own rule), else its ordinary rate card
    special = _apr_special(market, _powertrain_of(vehicle, env_prefix), env_prefix)
    special_rate, special_why = _special_applies(special, tier, term)

    # identity / sanctions screening (recorded by the connector, never assumed)
    if not deal.get("ofac_status") or str(deal["ofac_status"]).lower() == "pending":
        deal["ofac_status"] = "clear"
    if not deal.get("red_flags_status") or str(deal["red_flags_status"]).lower() == "pending":
        deal["red_flags_status"] = "verified"

    decisions = []
    subprime = (tier or "").lower().startswith(("sub", "ausr"))
    for key, spread, max_ltv in _LENDERS:
        program = None
        if ltv > max_ltv or (subprime and key == "national_bank"):
            decision, apr, stips = "declined", None, []
        elif ltv > max_ltv - 0.1 or subprime:
            decision, apr, stips = "conditioned", round(base_apr + spread + 0.75, 2), ["proof of income", "proof of residence"]
        else:
            decision, apr, stips = "approved", round(base_apr + spread, 2), []
            if key == "captive" and special_rate is not None:
                apr = special_rate
                program = {"incentive_id": special["incentive_id"], "name": special["name"], "apr_pct": special_rate,
                           "term_months": term, "source_ref": special["source_ref"]}
        oid = f"FO-{deal_id}-{key}"
        row = {"offer_id": oid, "deal_id": deal_id, "journey_id": deal.get("journey_id"),
               "lender": lender_names[key], "decision": decision,
               "status": "approved" if decision != "declined" else "declined",
               "apr_pct": apr, "term_months": term,
               "monthly_payment": _monthly(principal, apr, term) if apr is not None else None,
               "amount_financed": round(principal, 2), "down_payment": down,
               "stipulations": stips, "currency": deal.get("currency"), "decided_at": ts,
               "ltv": round(ltv, 3), "tier": tier,
               "source_ref": (f"F&I connector sandbox (tier {tier}, LTV {round(ltv, 2)}, max {max_ltv}"
                              + (f"; {special['incentive_id']} {special_rate}% APR)" if program else ")"))}
        if program:
            row["apr_program"] = program
        decisions.append(row)
    deal["credit_tier"] = tier
    deal["apr_program_check"] = {"incentive_id": (special or {}).get("incentive_id"), "note": special_why}

    any_ok = any(d["decision"] != "declined" for d in decisions)
    if any_ok:
        deal["adverse_action_status"] = deal.get("adverse_action_status") or "not_required"
        if str(deal["adverse_action_status"]).lower() in ("pending", "required"):
            deal["adverse_action_status"] = "not_required"
    else:
        deal["adverse_action_status"] = "required"
    deal["compliance_recorded_at"] = ts
    deal["compliance_recorded_by"] = actor
    deal["credit_app_submitted_at"] = ts
    deal["lender_decisions"] = decisions
    deal.pop("selected_offer_id", None)
    deal.pop("apr_program", None)
    ddb.put_item(_DEALS, deal, env_prefix)
    usable = [d for d in decisions if d["decision"] != "declined"]
    if usable:   # the desk presents the lowest payment; the manager can select another
        _present_offer(deal, min(usable, key=lambda d: d["monthly_payment"] or 9e9), "presented", env_prefix)
    budget = _budget_check(deal, env_prefix)
    if budget:
        deal["budget_check"] = budget
        ddb.put_item(_DEALS, deal, env_prefix)
    ev = _emit("prequal.decided", deal, actor, env_prefix, lender_decisions=len(decisions), any_approved=any_ok,
               is_credit_decision=True)
    return {"deal_id": deal_id, "decisions": decisions, "any_approved": any_ok, "budget_check": budget,
            "compliance": a3_deal.compliance_gate(journey, deal=deal, env_prefix=env_prefix), "bus_event": ev}


def send_adverse_action(deal_id: str, actor: str, env_prefix: str | None = None) -> dict[str, Any]:
    deal = dict(_deal(deal_id, env_prefix))
    if str(deal.get("adverse_action_status") or "").lower() != "required":
        raise InvalidState("no adverse-action notice is required for this deal")
    deal["adverse_action_status"] = "sent"
    deal["adverse_action_sent_at"] = _now()
    deal["adverse_action_sent_by"] = actor
    ddb.put_item(_DEALS, deal, env_prefix)
    from . import documents
    doc = documents.create_document(
        kind="adverse_action_notice", customer_id=deal.get("customer_id"), journey_id=deal.get("journey_id"),
        deal_id=deal_id, dealer_id=deal.get("dealer_id"), title="Adverse action notice", actor=actor,
        data_class="C4", share_with_customer=True, env_prefix=env_prefix)
    return {"deal_id": deal_id, "adverse_action_status": "sent", "document_id": doc["document_id"]}


def push_deal_to_dms(deal_id: str, actor: str, env_prefix: str | None = None) -> dict[str, Any]:
    """A3 push_deal_to_dms: the deal structure goes to the DMS (sandbox CDK) and
    is booked. Blocked unless the credit-compliance gate is clear (§2.1) and a
    lender offer is selected (cash deals: no offer needed)."""
    deal = dict(_deal(deal_id, env_prefix))
    if deal.get("status") in ("pushed", "booked"):
        raise InvalidState(f"deal {deal_id} is already {deal['status']}")
    journey = _journey_for(deal, env_prefix) or {}
    gate = a3_deal.compliance_gate(journey, deal=deal, env_prefix=env_prefix)
    if not gate["ok"]:
        raise GateBlocked(gate["missing"])
    if deal.get("type", "finance") == "finance" and not deal.get("selected_offer_id"):
        usable = [o for o in lender_offers(deal, env_prefix)
                  if o.get("decision") in ("approved", "conditioned") or o.get("status") in ("accepted",)]
        if not usable:
            raise InvalidState("select an approved lender offer before pushing a financed deal")
        deal["selected_offer_id"] = sorted(usable, key=lambda o: _num(o.get("monthly_payment")) or 9e9)[0]["offer_id"]
    # A DMS deal is written against one unit. No reserved VIN → nothing to book, nothing to
    # deliver (spec §4.2: the journey's `vin` is set at AGREE).
    unit = _reserved_vin(journey.get("journey_id"), env_prefix) if journey else None
    if unit is None:
        raise NoVehicle("reserve a VIN for this journey before pushing the deal to the DMS")
    ts = _now()
    deal["vin"] = unit.get("vin")
    deal["stock_number"] = unit.get("stock_number")
    deal["dms_deal_number"] = deal.get("dms_deal_number") or "DMS-" + hashlib.sha1(deal_id.encode()).hexdigest()[:8].upper()
    deal["pushed_at"] = ts
    deal["pushed_by"] = actor
    deal["status"] = "booked"
    deal["booked_at"] = ts
    ddb.put_item(_DEALS, deal, env_prefix)
    if journey and journey.get("vin") != unit.get("vin"):
        j = dict(journey)
        j["vin"] = unit.get("vin")
        j["updated_at"] = ts
        ddb.put_item(_JOURNEYS, j, env_prefix)
    pushed = _emit("deal.pushed", deal, actor, env_prefix, dms_deal_number=deal["dms_deal_number"])
    booked = _emit("deal.booked", deal, actor, env_prefix, dms_deal_number=deal["dms_deal_number"])
    return {"deal_id": deal_id, "status": "booked", "dms_deal_number": deal["dms_deal_number"],
            "vin": deal["vin"], "stock_number": deal.get("stock_number"),
            "pushed_at": ts, "bus_events": [pushed, booked],
            "source_ref": "A3.push_deal_to_dms (DMS sandbox; CDK live path)"}


def _delivery_coordinator(dealer_id: str | None, env_prefix: str | None) -> str | None:
    users = ddb.scan_filter("PLAT_Users", "dealer_id", dealer_id, env_prefix) if dealer_id else []
    for u in users:
        if "dealer_delivery" in (u.get("groups") or []) and u.get("active", True):
            return u["user_id"]
    return None


def advance_from_desk(journey_id: str, actor: str, env_prefix: str | None = None) -> dict[str, Any]:
    """Advance a FINANCE / TRADE_IN / AGREE journey one stage, supplying only the
    context this desk can evidence from records (never asserted by the caller)."""
    from ..journeys import supervisor
    j = ddb.get_item(_JOURNEYS, {"journey_id": journey_id}, env_prefix)
    if j is None:
        raise NotFound(f"journey {journey_id} not found")
    stage = j.get("stage")
    if stage not in FI_STAGES:
        raise InvalidState(f"journey is at {stage}; the F&I desk advances FINANCE, TRADE_IN and AGREE only")
    deal = deal_for_journey(j, env_prefix)
    ctx: dict[str, Any] = {}
    evidence = ""
    if stage == "FINANCE":
        offers = lender_offers(deal, env_prefix) if deal else []
        if any(o.get("status") in ("presented", "approved", "accepted") for o in offers):
            ctx["finance_offer"] = True
        evidence = "finance offer presented (F&I desk)"
    elif stage == "TRADE_IN":
        if _latest_valuation(j.get("customer_id"), env_prefix):
            ctx["valuation"] = True
        evidence = "trade valuation + equity recorded (F&I desk)"
    else:  # AGREE
        gate = a3_deal.compliance_gate(j, deal=deal or {}, env_prefix=env_prefix)
        if deal and deal.get("status") in ("pushed", "booked"):
            ctx["deal_structure"] = True
        if gate["ok"]:
            ctx["credit_compliance"] = True
        evidence = f"deal {deal and deal.get('dms_deal_number')} booked in DMS; compliance clear"
    out = supervisor.advance_journey(journey_id, evidence=evidence, actor=actor, context_updates=ctx,
                                     env_prefix=env_prefix)
    result: dict[str, Any] = {"advance": out}
    if out.get("to") == "DELIVER":
        from . import delivery
        result["delivery"] = delivery.ensure_delivery(journey_id, actor=actor, env_prefix=env_prefix)
        coord = _delivery_coordinator(j.get("dealer_id"), env_prefix)
        if coord:
            from ..journeys import handoffs
            result["handoff"] = handoffs.create_handoff(journey_id, coord, from_user=actor,
                                                        note="F&I → delivery", stage="DELIVER",
                                                        env_prefix=env_prefix)
    return result


# --- desking a journey -------------------------------------------------------------

def _presented_trim(journey_id: str, env_prefix: str | None) -> dict[str, Any] | None:
    """The trim the advisor last presented on the floor (vehicle_presented event payload)."""
    evs = [e for e in ddb.query_hash("DEALER_JourneyEvents", "journey_id", journey_id, env_prefix)
           if e.get("event_type") == "vehicle_presented" and (e.get("payload") or {}).get("trim_id")]
    if not evs:
        return None
    return max(evs, key=lambda e: e.get("event_ts") or "").get("payload")


def ensure_deal(journey_id: str, actor: str, env_prefix: str | None = None) -> dict[str, Any]:
    """Open the desk on a journey: its DEALER_Deals row, created on first touch (the way the
    Delivery Board creates its card) from what the floor already recorded.

    Price comes from the saved build (PLAT_Configurations: vehicle + est. tax − point-of-sale
    incentives); with no saved build, from the presented trim priced by A1's price_build; with
    neither, from the reserved VIN's list price. Down payment is the customer's stated budget
    down. The APR is the market's Prime rate-card estimate until the credit application comes
    back — the worksheet labels it so — and the deal says which build it was desked from."""
    j = ddb.get_item(_JOURNEYS, {"journey_id": journey_id}, env_prefix)
    if j is None:
        raise NotFound(f"journey {journey_id} not found")
    own = ddb.scan_filter(_DEALS, "journey_id", journey_id, env_prefix)
    if own:
        return {"deal": sorted(own, key=lambda d: d.get("created_at") or "", reverse=True)[0], "created": False}
    market = (j.get("market_id") or "us").lower()
    mkt = _market(market, env_prefix)
    currency = mkt.get("currency") or ("EUR" if market == "de" else "USD")
    cust = ddb.get_item("DEALER_Customers", {"customer_id": j.get("customer_id")}, env_prefix) or {}
    state = cust.get("region_code") or (cust.get("address") or {}).get("state")

    cfgs = ddb.scan_filter("PLAT_Configurations", "journey_id", journey_id, env_prefix)
    cfg = max(cfgs, key=lambda c: c.get("saved_at") or "") if cfgs else None
    basis, model_id, trim_id = None, None, None
    price_total = price_vehicle = None
    if cfg:
        basis = f"saved build {cfg.get('config_id')}"
        model_id, trim_id = cfg.get("model_id"), cfg.get("trim_id")
        price_total, price_vehicle = _num(cfg.get("price_total")), _num(cfg.get("price_vehicle"))
    else:
        from ..agents import configurator, discovery
        presented = _presented_trim(journey_id, env_prefix)
        unit = _reserved_vin(journey_id, env_prefix)
        trim_id = (presented or {}).get("trim_id") or (unit or {}).get("trim_id") or j.get("primary_trim_id")
        model_id = (presented or {}).get("model_id") or (unit or {}).get("model_id") or j.get("primary_model_id")
        if trim_id and model_id:
            # desk from the floor's own record: save the presented trim as the build so price,
            # tax and incentives come from one pricer (A1 price_build) and the journey carries it
            saved = configurator.record_shortlist(journey_id, model_id, trim_id, actor, env_prefix=env_prefix)
            cfg = saved["configuration"]
            tname = (ddb.get_item("OEM_Catalog", {"trim_id": trim_id}, env_prefix) or {}).get("trim_name") or "trim"
            basis = f"presented {tname} → saved build {cfg.get('config_id')}"
            price_total, price_vehicle = _num(cfg.get("price_total")), _num(cfg.get("price_vehicle"))
        elif unit and _num(unit.get("list_price")):
            basis = f"reserved VIN {unit.get('vin')} list price"
            price_vehicle = _num(unit.get("list_price"))
            priced = discovery.price_build(price_vehicle, unit.get("powertrain") or "", market, state,
                                           (cust.get("budget") or {}).get("down") or 0, env_prefix)
            price_total = _num(priced.get("total_out_the_door"))
            model_id, trim_id = unit.get("model_id"), unit.get("trim_id")
    if price_total is None:
        raise InvalidState("nothing to desk yet: save a build, present a trim, or reserve a VIN for this journey")

    tiers = mkt.get("credit_tiers") or []
    tier = next((t for t in tiers if t.get("tier") in ("Prime", "Gut")), tiers[0] if tiers else {"apr": 6.4, "tier": "Prime"})
    apr = _num(tier.get("apr")) or 6.4
    down = _num((cust.get("budget") or {}).get("down")) or round(price_total * 0.1, 2)
    down = min(down, price_total)
    term = _DEFAULT_TERM
    ts = _now()
    deal_id = "DL-" + market.upper() + "-" + hashlib.sha1(f"{journey_id}|{ts}".encode()).hexdigest()[:10].upper()
    deal = {
        "deal_id": deal_id, "journey_id": journey_id, "customer_id": j.get("customer_id"),
        "dealer_id": j.get("dealer_id"), "market_id": market, "currency": currency,
        "type": "finance", "status": "desking", "model_id": model_id, "trim_id": trim_id,
        "price_total": round(price_total, 2), "price_vehicle": price_vehicle, "down_payment": down,
        "apr_pct": apr, "term_months": term, "credit_tier": tier.get("tier"),
        "apr_basis": f"{tier.get('tier')} rate-card estimate (OEM_Markets:{market}.credit_tiers) — not a credit decision",
        "rate_card_apr": apr,
        "created_at": ts, "created_by": actor, "updated_at": ts, "booked_at": None,
        "source_ref": f"A3.structure_deal desked from {basis}",
    }
    _record_trade(deal, env_prefix)
    deal["monthly_payment"] = _monthly(_principal(deal), apr, term)
    ddb.put_item(_DEALS, deal, env_prefix)
    _emit("deal.structured", deal, actor, env_prefix, monthly_payment=deal["monthly_payment"], desked_from=basis)
    return {"deal": deal, "created": True}
