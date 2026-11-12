"""F&I — prequalify (sandbox connector), spec §3.2 / §5.13.

A SOFT pre-qualification: it presents payment scenarios across the market's
credit tiers and terms and records a presented finance offer. It never returns a
hard credit decision or an approval — the spec is explicit that A2/F&I surface
payment RANGES, never credit decisions (that fence is enforced in A2 Q&A too).

It also records the credit-compliance statuses on the deal (OFAC screen, Red
Flags identity verification, adverse-action notice) that the AGREE gate
(deal.compliance_gate) consumes — modeling the F&I connector writing those
results. In the sandbox these are recorded as the connector would report them;
production swaps in the real CDK/F&I connector behind the same interface.

  prequalify(journey, amount, fico?, tier?, term_months?, down?) ->
      {scenarios[], recommended, offer_id, compliance_recorded}
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from ..common import ddb
from . import payment
from ..events import bus as _bus

_MARKETS = "OEM_Markets"
_OFFERS = "DEALER_FinanceOffers"
_DEALS = "DEALER_Deals"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _monthly(principal: float, apr_pct: float, term: int) -> float:
    return payment.monthly(principal, apr_pct, term)      # the one formula (finance/payment.py)


def _lender_name(mkt: dict[str, Any]) -> str:
    """The captive lender label. `brand` may be a string or a structured object,
    so coerce defensively rather than assume a string."""
    brand = mkt.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name") or brand.get("captive") or brand.get("oem")
    if not isinstance(brand, str) or not brand:
        brand = "Captive"
    return f"{brand} Financial"


def _tier_for_fico(tiers: list[dict[str, Any]], fico: int | None) -> dict[str, Any] | None:
    if fico is None:
        return None
    # highest tier whose fico_min <= fico
    eligible = [t for t in tiers if _num(t.get("fico_min")) is not None and fico >= t["fico_min"]]
    return max(eligible, key=lambda t: t["fico_min"]) if eligible else None


def prequalify(journey: dict[str, Any], amount: float | int,
               fico: int | None = None, tier: str | None = None,
               term_months: int | None = None, down: float | int = 0,
               actor: str = "F&I", env_prefix: str | None = None) -> dict[str, Any]:
    """Soft prequal: present payment scenarios (no credit decision) and record a
    presented offer + the deal's compliance statuses.

    Scenarios span the market credit tiers (or just the caller's tier if given)
    and the retail terms. The `recommended` scenario is the one matching the
    caller's fico/tier, else Prime."""
    market = (journey.get("market_id") or "us").lower()
    mkt = ddb.get_item(_MARKETS, {"market_id": market}, env_prefix) or {}
    currency = mkt.get("currency") or ("EUR" if market == "de" else "USD")
    tiers = mkt.get("credit_tiers") or []
    retail = next((p for p in (mkt.get("finance_products") or []) if p.get("code") == "RETAIL"), {})
    terms = [term_months] if term_months else (retail.get("terms_months") or [60])

    principal = max(0.0, float(amount) - float(down))

    # which tiers to present
    selected = tiers
    if tier:
        selected = [t for t in tiers if str(t.get("tier")).lower() == tier.lower()] or tiers
    matched_tier = _tier_for_fico(tiers, fico) or \
        (selected[0] if tier else next((t for t in tiers if t.get("tier") == "Prime"), tiers[0] if tiers else None))

    scenarios = []
    for t in selected:
        for term in terms:
            apr = _num(t.get("apr")) or 6.4
            scenarios.append({
                "tier": t.get("tier"), "apr_pct": apr, "term_months": term,
                "monthly_payment": _monthly(principal, apr, term),
                "amount_financed": round(principal, 2), "down_payment": float(down),
            })

    recommended = None
    if matched_tier:
        pref_term = term_months or 60
        recs = [s for s in scenarios if s["tier"] == matched_tier.get("tier")]
        recommended = next((s for s in recs if s["term_months"] == pref_term), recs[0] if recs else None)

    # record a presented offer (soft — status 'presented', not 'accepted')
    jid = journey.get("journey_id")
    deal_id = None
    existing = ddb.scan_filter(_OFFERS, "journey_id", jid, env_prefix)
    deal_id = (existing[0].get("deal_id") if existing else None) or f"DL-{jid}"
    offer_id = f"FO-{jid}"
    r = recommended or (scenarios[0] if scenarios else {"apr_pct": None, "term_months": None,
                                                        "monthly_payment": None, "amount_financed": principal})
    if env_prefix is None or True:      # write the presented offer
        ddb.put_item(_OFFERS, {
            "deal_id": deal_id, "journey_id": jid, "offer_id": offer_id,
            "lender": _lender_name(mkt), "status": "presented",
            "apr_pct": r.get("apr_pct"), "term_months": r.get("term_months"),
            "monthly_payment": r.get("monthly_payment"), "amount_financed": r.get("amount_financed"),
            "down_payment": float(down), "currency": currency,
            "presented_at": _now(),
            "source_ref": "F&I.prequalify (soft; market credit_tiers; no credit decision)",
        }, env_prefix)

    # record the credit-compliance statuses on the deal (what the F&I connector
    # reports) so the AGREE gate can consume them. A soft prequal clears OFAC +
    # Red Flags (identity) and marks adverse-action not_required (no denial).
    deal = ddb.get_item(_DEALS, {"deal_id": deal_id}, env_prefix) or {
        "deal_id": deal_id, "customer_id": journey.get("customer_id"),
        "dealer_id": journey.get("dealer_id"), "currency": currency, "status": "desking"}
    deal.update({
        "ofac_status": "clear",
        "red_flags_status": "verified",
        "adverse_action_status": "not_required",
        "compliance_recorded_at": _now(),
        "compliance_recorded_by": actor,
    })
    ddb.put_item(_DEALS, deal, env_prefix)

    _bus.emit("prequal.decided", {"journey_id": jid, "deal_id": deal_id, "offer_id": offer_id,
                                  "dealer_id": deal.get("dealer_id"), "is_credit_decision": False},
              actor=actor, resource_url=f"/r/deal/{deal_id}", env_prefix=env_prefix)
    return {
        "journey_id": jid,
        "deal_id": deal_id,
        "offer_id": offer_id,
        "market": market, "currency": currency,
        "amount": float(amount), "down": float(down), "amount_financed": round(principal, 2),
        "scenarios": scenarios,
        "recommended": recommended,
        "is_credit_decision": False,        # explicit: this is NOT an approval
        "disclaimer": "Illustrative payment scenarios only — not a credit decision or approval.",
        "compliance_recorded": {"ofac": "clear", "red_flags": "verified",
                                "adverse_action": "not_required"},
        "source_ref": "F&I.prequalify (sandbox)",
    }
