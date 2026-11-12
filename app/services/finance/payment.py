"""The one monthly-payment formula, and the one rule for which payment a screen shows.

Three modules each had their own `_monthly()` and 14 computed a monthly figure from their own
inputs (a default APR here, a credit-tier APR there, the deal's APR elsewhere), so one buyer read
$711 on her saved build, $356 under Financing and $387.61 at the F&I desk. The arithmetic is the
same; the inputs were not, and nothing said which number was the real one.

  * `monthly()` is the amortised payment every module calls.
  * `payment_of_record()` is the rule: once a deal is booked or contracted, its lender offer is
    the payment — every screen shows that and labels anything else an estimate; before a deal,
    the latest presented offer; before an offer, the saved build's estimate, labelled as such.
"""
from __future__ import annotations

from typing import Any

from ..common import ddb

DEFAULT_APR_PCT: dict[str, float] = {"USD": 6.4, "EUR": 4.9}
DEFAULT_TERM_MONTHS = 60
_FINAL = ("booked", "contracted", "signed")


def monthly(principal: float | None, apr_pct: float | None, term_months: int | None) -> float:
    """Standard amortised monthly payment, to the cent. 0 for nothing to finance or no term."""
    p = float(principal or 0)
    n = int(term_months or 0)
    if p <= 0 or n <= 0:
        return 0.0
    r = (float(apr_pct or 0) / 100) / 12
    if r == 0:
        return round(p / n, 2)
    return round(p * r / (1 - (1 + r) ** (-n)), 2)


def payment_of_record(journey_id: str | None, env_prefix: str | None = None) -> dict[str, Any] | None:
    """The payment a person should be shown for a journey, and where it comes from.

    {monthly, apr_pct, term_months, lender, basis: 'deal' | 'offer' | None, deal_id, offer_id,
     status}. `deal` once the deal is booked/contracted (its accepted or latest offer); `offer`
    for a presented offer before that; None when nothing has been presented."""
    if not journey_id:
        return None
    offers = ddb.scan_filter("DEALER_FinanceOffers", "journey_id", journey_id, env_prefix)
    deals = ddb.scan_filter("DEALER_Deals", "journey_id", journey_id, env_prefix)
    deal = next((d for d in sorted(deals, key=lambda d: d.get("updated_at") or d.get("created_at") or "", reverse=True)
                 if d.get("status") in _FINAL), None)
    if deal:
        chosen = next((o for o in offers if o.get("status") == "accepted"), None) \
            or next((o for o in offers if o.get("deal_id") == deal.get("deal_id")), None)
        pay = (chosen or {}).get("monthly_payment")
        if pay is None and deal.get("monthly_payment") is not None:
            pay = deal.get("monthly_payment")
        if pay is not None:
            return {"monthly": pay, "apr_pct": (chosen or deal).get("apr_pct"), "term_months": (chosen or deal).get("term_months"),
                    "lender": (chosen or deal).get("lender"), "basis": "deal", "deal_id": deal.get("deal_id"),
                    "offer_id": (chosen or {}).get("offer_id"), "status": deal.get("status"),
                    "currency": (chosen or deal).get("currency")}
    presented = sorted((o for o in offers if o.get("monthly_payment") is not None),
                       key=lambda o: o.get("presented_at") or "", reverse=True)
    if presented:
        o = presented[0]
        return {"monthly": o["monthly_payment"], "apr_pct": o.get("apr_pct"), "term_months": o.get("term_months"),
                "lender": o.get("lender"), "basis": "offer", "deal_id": o.get("deal_id"), "offer_id": o.get("offer_id"),
                "status": o.get("status"), "currency": o.get("currency")}
    return None
