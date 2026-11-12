"""A3 — structure_deal + credit-compliance gate (spec §5.13, §2.1 AGREE gate).

Assembles the F&I deal desk view for a journey (finance offer + trade + F&I
product menu with good-better-best attach) and computes the credit-compliance
gate the purchase state machine requires to leave AGREE: OFAC screen, Red Flags
identity verification, and adverse-action notice status.

  structure_deal(journey)          -> deal desk view (offer, trade, F&I menu, totals)
  compliance_gate(journey)         -> {ofac, red_flags, adverse_action, ok, missing}

CRITICAL correctness rule (spec §2.1): the compliance gate is NOT assumed to
pass. Each check reports its RECORDED status from the deal/compliance data; a
check with no recorded result is `pending` and BLOCKS the AGREE->DELIVER
transition. A2.validate_handoff(AGREE) consumes compliance_gate().ok. Nothing
here fabricates a "cleared" status.
"""
from __future__ import annotations

from typing import Any

from ..common import ddb

_DEALS = "DEALER_Deals"
_OFFERS = "DEALER_FinanceOffers"
_FIPRODUCTS = "DEALER_FIProducts"

# The three checks that gate AGREE (spec §2.1 credit-compliance gate).
_COMPLIANCE_CHECKS = ("ofac", "red_flags", "adverse_action")

# F&I menu presentation order (good-better-best is built from these).
_MENU_ORDER = ("vsc", "gap", "maint", "tw", "charger")


def _num(v: Any) -> float | int | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class NotFound(Exception):
    pass


def _deal_for_journey(journey: dict[str, Any], env_prefix: str | None) -> dict[str, Any] | None:
    """The one deal a journey means — the SAME resolution the F&I desk uses, so the
    compliance ticks on the desk and the gate on the journey board never disagree.
    Order: the newest deal keyed to this journey; else the deal behind the journey's
    presented finance offer; else the customer's newest deal."""
    jid = journey.get("journey_id")
    cid = journey.get("customer_id")
    own = ddb.scan_filter(_DEALS, "journey_id", jid, env_prefix) if jid else []
    if own:
        return sorted(own, key=lambda d: d.get("created_at") or "", reverse=True)[0]
    offers = ddb.scan_filter(_OFFERS, "journey_id", jid, env_prefix) if jid else []
    if offers:
        did = offers[0].get("deal_id")
        if did:
            d = ddb.get_item(_DEALS, {"deal_id": did}, env_prefix)
            if d:
                return d
    # a deal keyed to ANOTHER journey of the same customer (their last purchase, resting in OWN)
    # is not this journey's deal — a repeat buyer starts a new desk, not their old one
    deals = [d for d in ddb.scan_filter(_DEALS, "customer_id", cid, env_prefix)
             if not d.get("journey_id") or d.get("journey_id") == jid] if cid else []
    return sorted(deals, key=lambda d: d.get("created_at") or "", reverse=True)[0] if deals else None


def structure_deal(journey: dict[str, Any], env_prefix: str | None = None) -> dict[str, Any]:
    """Assemble the F&I deal desk view for a journey (spec §5.13).

    Pulls the finance offer, the deal totals, and the F&I product menu (with
    which products are attached), and computes the compliance gate. Every figure
    comes from the deal/offer/product rows — no invented terms."""
    jid = journey.get("journey_id")
    deal = _deal_for_journey(journey, env_prefix)
    offers = ddb.scan_filter(_OFFERS, "journey_id", jid, env_prefix)
    offer = None
    if offers:
        offer = next((o for o in offers if o.get("status") in ("presented", "accepted")), offers[0])

    did = (deal or {}).get("deal_id") or (offer or {}).get("deal_id")
    products = ddb.scan_filter(_FIPRODUCTS, "deal_id", did, env_prefix) if did else []
    menu = [{"code": p.get("code"), "name": p.get("name"), "price": _num(p.get("price")),
             "currency": p.get("currency"), "attached": bool(p.get("accepted"))}
            for p in sorted(products, key=lambda p: _MENU_ORDER.index(p.get("code"))
                            if p.get("code") in _MENU_ORDER else 99)]
    attached_total = sum(m["price"] for m in menu if m["attached"] and m["price"])
    products_per_deal = sum(1 for m in menu if m["attached"])

    gate = compliance_gate(journey, deal=deal, env_prefix=env_prefix)

    return {
        "journey_id": jid,
        "deal_id": did,
        "customer_id": journey.get("customer_id"),
        "finance_offer": ({"offer_id": offer.get("offer_id"), "lender": offer.get("lender"),
                           "apr_pct": offer.get("apr_pct"), "term_months": offer.get("term_months"),
                           "monthly_payment": _num(offer.get("monthly_payment")),
                           "amount_financed": _num(offer.get("amount_financed")),
                           "down_payment": _num(offer.get("down_payment")),
                           "status": offer.get("status"), "currency": offer.get("currency"),
                           "source_ref": offer.get("source_ref")} if offer else None),
        "deal_totals": ({"price_total": _num(deal.get("price_total")),
                         "monthly_payment": _num(deal.get("monthly_payment")),
                         "apr_pct": deal.get("apr_pct"), "term_months": deal.get("term_months"),
                         "status": deal.get("status"), "currency": deal.get("currency")} if deal else None),
        "fi_menu": menu,
        "fi_products_attached": products_per_deal,
        "fi_attached_total": round(attached_total, 2) if attached_total else 0,
        "compliance_gate": gate,
        "source_ref": "A3.structure_deal (DEALER_Deals + FinanceOffers + FIProducts)",
    }


def compliance_gate(journey: dict[str, Any], deal: dict[str, Any] | None = None,
                    env_prefix: str | None = None) -> dict[str, Any]:
    """The credit-compliance gate for AGREE->DELIVER (spec §2.1).

    Reports each of OFAC / Red Flags / adverse-action from its RECORDED status
    on the deal (fields `ofac_status`, `red_flags_status`, `adverse_action_status`
    written by the F&I connector). A check with no recorded result is `pending`
    and blocks the transition. `ok` is true only when all three are cleared —
    never assumed."""
    if deal is None:
        deal = _deal_for_journey(journey, env_prefix)
    deal = deal or {}

    checks: dict[str, str] = {}
    missing: list[str] = []
    for chk in _COMPLIANCE_CHECKS:
        status = deal.get(f"{chk}_status")
        # cleared vocab: "clear"/"cleared"/"passed"/"provided"/"not_required"
        cleared = str(status).lower() in ("clear", "cleared", "passed", "provided",
                                          "verified", "not_required", "sent")
        checks[chk] = status or "pending"
        if not cleared:
            missing.append(chk)

    return {
        "checks": checks,
        "cleared": [c for c in _COMPLIANCE_CHECKS if c not in missing],
        "missing": missing,
        "ok": len(missing) == 0,
        "note": ("all credit-compliance checks cleared" if not missing
                 else f"blocked: {', '.join(missing)} not cleared (F&I connector must record status)"),
        "source_ref": "A3.compliance_gate (deal ofac/red_flags/adverse_action status)",
    }


# --- get_cross_sell (A3, advisor center panel), spec §5.3 -------------------

# The F&I product catalog A3 recommends against, with a grounded rationale for
# why each fits. Only products NOT already attached are recommended.
_FI_CATALOG = {
    "vsc": {"name": "Vehicle service contract",
            "why": "covers major repairs after the factory warranty ends"},
    "gap": {"name": "GAP protection",
            "why": "covers the gap between payoff and value on a financed vehicle"},
    "maint": {"name": "Prepaid maintenance",
              "why": "locks in scheduled service at today's price"},
    "tire_wheel": {"name": "Tire & wheel",
                   "why": "covers road-hazard tire and wheel damage"},
    "appearance": {"name": "Appearance protection",
                   "why": "protects paint and interior surfaces"},
}


def get_cross_sell(journey: dict[str, Any], env_prefix: str | None = None) -> dict[str, Any]:
    """Recommend un-attached F&I products for the advisor center panel (§5.3).

    Reads what's already on the deal, then recommends the remaining products
    with a rationale weighted to the deal (GAP ranks high on a financed deal;
    VSC on an EV or longer term). Grounded — only real catalog products, and it
    never recommends something already attached."""
    jid = journey.get("journey_id")
    deal = _deal_for_journey(journey, env_prefix)
    did = (deal or {}).get("deal_id")
    products = ddb.scan_filter(_FIPRODUCTS, "deal_id", did, env_prefix) if did else []
    attached_codes = {p.get("code") for p in products if p.get("accepted")}

    # deal signals to weight the pitch
    offers = ddb.scan_filter(_OFFERS, "journey_id", jid, env_prefix)
    offer = offers[0] if offers else None
    financed = bool(offer and _num(offer.get("amount_financed")))
    term = _num((offer or {}).get("term_months")) or 0
    # rank weight per product given the deal context
    def _weight(code: str) -> float:
        w = 0.5
        if code == "gap" and financed:
            w = 0.9
        if code == "vsc" and term >= 60:
            w = 0.85
        if code == "maint":
            w = 0.7
        if code == "tire_wheel":
            w = 0.55
        if code == "appearance":
            w = 0.5
        return w

    recommendations = []
    for code, meta in _FI_CATALOG.items():
        if code in attached_codes:
            continue
        recommendations.append({
            "code": code, "name": meta["name"], "why": meta["why"],
            "weight": _weight(code),
        })
    recommendations.sort(key=lambda r: r["weight"], reverse=True)

    return {
        "journey_id": jid,
        "deal_id": did,
        "attached": sorted(attached_codes),
        "recommendations": recommendations,
        "deal_context": {"financed": financed, "term_months": term},
        "source_ref": "A3.get_cross_sell (DEALER_FIProducts attach state + deal context)",
    }
