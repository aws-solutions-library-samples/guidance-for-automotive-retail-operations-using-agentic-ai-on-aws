"""L5 — A4 Trade-In & Upgrade equity agent (spec §5.6, use-case A4).

Computes the customer's trade equity, a payment-delta scenario against the
journey's active finance offer, and an upgrade-timing signal — the numbers the
Advisor screen's Value Snapshot and the Service screen's equity flag reference.

Deterministic math (the spec runs this in AgentCore Code Interpreter for
auditability; here it is plain, testable Python). An optional Bedrock call adds
a one-line plain-language explanation, with the same always-answers fallback as
the objection agent. Every figure carries a source_ref so downstream citations
resolve.

  build_equity(journey_id) -> equity, payment-delta, upgrade signal, explanation.
"""
from __future__ import annotations

import os
from typing import Any

from ..common import ddb
from ..platform import generate as _gen

BEDROCK_MODEL = os.environ.get("RETAIL_OPS_BEDROCK_MODEL", "us.anthropic.claude-opus-4-5-20251101-v1:0")
BEDROCK_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Upgrade-timing thresholds (advisory signal, not a promise).
_LEASE_UPGRADE_WINDOW_MONTHS = 6      # lease ending within N months -> upgrade window
_POSITIVE_EQUITY_FLAG = 0             # equity strictly above this is "positive"


class NotFound(Exception):
    pass


def build_equity(journey_id: str, env_prefix: str | None = None, *, explain: bool = True) -> dict[str, Any]:
    """Assemble the A4 equity snapshot for a journey. Raises NotFound if unknown.

    explain=False skips the one-line Bedrock explanation (templated text instead): callers that phrase
    the answer themselves — the A3 agent's get_trade_equity tool — were paying ~5 s per call for a
    sentence they discard (2026-10-28 eval: 12 s of a 28 s turn)."""
    journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id}, env_prefix)
    if journey is None:
        raise NotFound(f"journey {journey_id} not found")
    customer_id = journey.get("customer_id")
    customer = ddb.get_item("DEALER_Customers", {"customer_id": customer_id}, env_prefix) if customer_id else None

    valuation = _latest_valuation(customer_id, env_prefix)
    offer = _active_offer(journey_id, env_prefix)
    cur = (customer or {}).get("current_vehicle") or {}

    equity_val = _num(valuation.get("equity")) if valuation else None
    trade_value = _num(valuation.get("trade_value")) if valuation else None
    payoff = _num(valuation.get("payoff")) if valuation else _num(cur.get("payoff"))
    currency = (valuation or {}).get("currency") or (offer or {}).get("currency") or "USD"

    payment_delta = _payment_delta(offer, cur)
    signal = _upgrade_signal(equity_val, cur)

    result: dict[str, Any] = {
        "journey_id": journey_id,
        "customer_id": customer_id,
        "currency": currency,
        "equity": equity_val,
        "trade_value": trade_value,
        "payoff": payoff,
        "equity_position": ("positive" if equity_val is not None and equity_val > _POSITIVE_EQUITY_FLAG
                            else "negative" if equity_val is not None else "unknown"),
        "current_vehicle": {k: cur.get(k) for k in ("make", "model", "year", "mileage",
                                                    "ownership", "months_remaining")} if cur else None,
        "finance_offer": ({"monthly_payment": _num(offer.get("monthly_payment")),
                           "apr_pct": offer.get("apr_pct"), "term_months": offer.get("term_months"),
                           "lender": offer.get("lender")} if offer else None),
        "payment_delta": payment_delta,
        "upgrade_signal": signal,
        "source_refs": {
            "equity": (valuation or {}).get("source_ref"),
            "finance_offer": (offer or {}).get("source_ref"),
        },
    }
    result["explanation"], result["explained_by"] = _explain(result) if explain else _explain_templated(result)
    return result


# --- math -------------------------------------------------------------------

def _latest_valuation(customer_id: str | None, env_prefix) -> dict[str, Any] | None:
    if not customer_id:
        return None
    vals = ddb.scan_filter("DEALER_Valuations", "customer_id", customer_id, env_prefix)
    if not vals:
        return None
    return max(vals, key=lambda v: v.get("valued_at") or "")


def _active_offer(journey_id: str, env_prefix) -> dict[str, Any] | None:
    offers = ddb.scan_filter("DEALER_FinanceOffers", "journey_id", journey_id, env_prefix)
    if not offers:
        return None
    # prefer a presented offer, else the newest by amount_financed presence
    presented = [o for o in offers if o.get("status") == "presented"]
    return (presented or offers)[0]


def _payment_delta(offer: dict[str, Any] | None, cur: dict[str, Any]) -> dict[str, Any] | None:
    """Delta between the new-vehicle offer payment and the current obligation.

    The current monthly obligation isn't stored directly; when the customer owns
    outright (payoff 0, not a lease) the current payment is treated as 0. For a
    lease we don't have the lease payment, so delta is reported against 0 with a
    note. This keeps the number honest rather than inventing a figure."""
    if not offer or offer.get("monthly_payment") is None:
        return None
    new_pay = _num(offer.get("monthly_payment"))
    ownership = (cur or {}).get("ownership")
    if ownership == "owned" and _num(cur.get("payoff")) in (0, None):
        current_pay = 0
        note = "current vehicle owned outright (no monthly obligation)"
    else:
        current_pay = 0
        note = "current monthly obligation not on file; delta shown vs 0"
    return {"new_monthly": new_pay, "current_monthly": current_pay,
            "delta_monthly": new_pay - current_pay, "note": note}


def _upgrade_signal(equity: float | int | None, cur: dict[str, Any]) -> dict[str, Any]:
    """Advisory upgrade-timing signal. Positive equity and/or a lease nearing end."""
    reasons = []
    recommend = False
    if equity is not None and equity > _POSITIVE_EQUITY_FLAG:
        reasons.append(f"positive trade equity")
        recommend = True
    months = _num((cur or {}).get("months_remaining"))
    if (cur or {}).get("ownership") == "lease" and months is not None and months <= _LEASE_UPGRADE_WINDOW_MONTHS:
        reasons.append(f"lease ends in {months} months (within {_LEASE_UPGRADE_WINDOW_MONTHS}-month window)")
        recommend = True
    return {"recommend_upgrade": recommend,
            "reasons": reasons or ["no strong upgrade trigger right now"]}


def _explain_templated(result: dict[str, Any]) -> tuple[str, str]:
    eq = result.get("equity")
    cur = result.get("current_vehicle") or {}
    veh = f"{cur.get('year','')} {cur.get('make','')} {cur.get('model','')}".strip()
    templated = (
        f"Trade equity on the {veh or 'current vehicle'} is "
        f"{result['currency']} {eq:,} ({result['equity_position']}). "
        + ("Good time to discuss an upgrade." if result["upgrade_signal"]["recommend_upgrade"]
           else "No strong upgrade trigger yet.")
    ) if eq is not None else "No current valuation on file for this customer."
    return templated, "fallback"


def _explain(result: dict[str, Any]) -> tuple[str, str]:
    """One-line advisor-facing explanation. Bedrock if available, else templated."""
    eq = result.get("equity")
    cur = result.get("current_vehicle") or {}
    veh = f"{cur.get('year','')} {cur.get('make','')} {cur.get('model','')}".strip()
    templated, _ = _explain_templated(result)
    if not _gen.enabled():
        return templated, "fallback"

    try:
        import boto3
        rt = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
        prompt = (
            "In one or two sentences, tell a sales advisor how to use this trade "
            "equity with the customer. Be factual, no specific price/rate promises.\n"
            f"equity={eq} {result['currency']}, position={result['equity_position']}, "
            f"vehicle={veh}, upgrade_signal={result['upgrade_signal']}"
        )
        resp = rt.converse(
            modelId=BEDROCK_MODEL,
            system=[{"text": "You are a concise dealership trade-in assistant. "
                             "No price, rate, or approval promises."}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 120, "temperature": 0.2},
            **_gen.guardrail_kwargs(),
        )
        text = resp["output"]["message"]["content"][0]["text"].strip()
        if resp.get("stopReason") == "guardrail_intervened":   # never surface the block message as advice
            return templated, "fallback (guardrail)"
        return (text or templated), ("bedrock" if text else "fallback")
    except Exception:
        return templated, "fallback"


def _num(v: Any) -> float | int | None:
    if isinstance(v, bool) or v is None:
        return None if v is None else v
    if isinstance(v, (int, float)):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --- refresh_valuation (A4, spec §5.12 My Garage trade refresh) -------------

import datetime as _dt

# Transparent depreciation model (advisory, guidebook-anchored in production).
# Per-mile depreciation and condition multipliers are the auditable inputs; the
# result is not a black box. Production swaps in a live valuation connector
# (Cox/KBB) behind the same interface.
_DEPRECIATION_PER_MILE = {"USD": 0.08, "EUR": 0.06}      # value lost per extra mile/km
_CONDITION_MULTIPLIER = {"excellent": 1.06, "good": 1.0, "fair": 0.90, "poor": 0.78}


class RefreshError(Exception):
    pass


def _median(xs: list[float]) -> float | None:
    xs = sorted(xs)
    if not xs:
        return None
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2


def baseline_valuation(customer_id: str, env_prefix: str | None = None) -> dict[str, Any] | None:
    """Sandbox valuation connector for an owner with a current vehicle but no appraisal on file
    (a customer who arrived as a web lead and typed their trade into the form).

    Anchors the appraisal to the market's comparable appraisals — the same make and model,
    preferring the nearest model years — at the median, then adjusts for the mileage difference
    at the transparent per-mile rate. Payoff is what the customer stated. Persists an append-only
    DEALER_Valuations row whose source_ref names the comparables, so the number is traceable and
    the next refresh has a baseline. None when there is no current vehicle or no comparable; the
    live Cox/KBB connector returns the same shape."""
    c = ddb.get_item("DEALER_Customers", {"customer_id": customer_id}, env_prefix) or {}
    cur = c.get("current_vehicle") or {}
    make, model = (cur.get("make") or "").strip(), (cur.get("model") or "").strip()
    if not make or not model:
        return None
    currency = "EUR" if (c.get("market_id") or "us").lower() == "de" else "USD"
    # comparables are other owners' guidebook appraisals only: re-valuations and earlier comparables
    # estimates would move the median every time someone is valued
    comps = [v for v in ddb.scan_all("DEALER_Valuations", env_prefix)
             if (v.get("make") or "").lower() == make.lower() and (v.get("model") or "").lower() == model.lower()
             and (v.get("currency") or "USD") == currency and _num(v.get("trade_value"))
             and v.get("customer_id") != customer_id and not _is_derived(v)]
    if not comps:
        return None
    year = _num(cur.get("year"))
    if year is not None:
        near = [v for v in comps if _num(v.get("year")) is not None and abs(_num(v["year"]) - year) <= 2]
        comps = near or comps
    anchor = _median([float(v["trade_value"]) for v in comps]) or 0.0
    comp_miles = _median([float(_num(v.get("mileage")) or 0) for v in comps]) or 0.0
    mileage = _num(cur.get("mileage"))
    rate = _DEPRECIATION_PER_MILE.get(currency, 0.08)
    adj = round((float(mileage) - comp_miles) * rate, 2) if mileage is not None and comp_miles else 0.0
    value = round(max(500.0, anchor - adj), 2)
    payoff = float(_num(cur.get("payoff")) or 0)
    _ts = _dt.datetime.now(_dt.timezone.utc)
    row = {
        "valuation_id": f"VAL-{customer_id}-COMPS-{int(_ts.timestamp())}",
        "customer_id": customer_id, "dealer_id": c.get("dealer_id"),
        "make": make, "model": model, "year": int(year) if year is not None else None,
        "mileage": mileage, "trade_value": value, "payoff": payoff, "equity": round(value - payoff, 2),
        "currency": currency, "valued_at": _ts.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "adjustments": [{"factor": "comparables", "count": len(comps), "median_value": anchor, "median_mileage": comp_miles},
                        {"factor": "mileage", "per_unit": rate, "delta": -adj}],
        "source_ref": (f"sandbox valuation connector: median of {len(comps)} {make} {model} appraisals "
                       f"(DEALER_Valuations), mileage-adjusted at {rate}/{'km' if currency == 'EUR' else 'mi'}"),
    }
    ddb.put_item("DEALER_Valuations", row, env_prefix)
    return row


def _is_derived(v: dict[str, Any]) -> bool:
    """A re-valuation (mileage / condition applied to an anchor) or a comparables estimate —
    never itself an anchor or a comparable, or values drift every time someone re-values."""
    return bool(v.get("baseline_valuation_id")) or str(v.get("source_ref") or "").startswith(
        ("A4.refresh_valuation", "sandbox valuation connector"))


def valuation_chain(customer_id: str | None, env_prefix: str | None = None,
                    vin: str | None = None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(anchor, latest) for a customer's car: the anchor is the guidebook (or comparables) row
    every re-valuation is computed FROM; latest is the newest row (it carries the last recorded
    mileage and condition). With a VIN, only that car's rows count."""
    if not customer_id:
        return None, None
    if vin:
        from ..common import vehicles
        vals = vehicles.valuations_of(vin, customer_id, env_prefix)
    else:
        vals = sorted(ddb.scan_filter("DEALER_Valuations", "customer_id", customer_id, env_prefix),
                      key=lambda v: v.get("valued_at") or "")
    if not vals:
        return None, None
    latest = vals[-1]
    by_id = {v.get("valuation_id"): v for v in vals}
    anchor, seen = latest, set()
    while anchor.get("baseline_valuation_id") and anchor["baseline_valuation_id"] in by_id \
            and anchor["valuation_id"] not in seen:
        seen.add(anchor["valuation_id"])
        anchor = by_id[anchor["baseline_valuation_id"]]
    if anchor.get("baseline_valuation_id"):   # chain leaves this car's rows: newest underived row
        anchor = next((v for v in reversed(vals) if not v.get("baseline_valuation_id")), anchor)
    return anchor, latest


def revalue(anchor: dict[str, Any], mileage: float | int | None, condition: str | None) -> dict[str, Any]:
    """The same car at a given mileage and condition, computed from its anchor alone.

    Pure and deterministic: the same car, mileage and condition always give the same value,
    however many times it is re-valued (a re-valuation never compounds on the last one)."""
    currency = anchor.get("currency") or "USD"
    anchor_cond = str(anchor.get("condition") or "good").lower()
    good_value = (_num(anchor.get("trade_value")) or 0) / _CONDITION_MULTIPLIER.get(anchor_cond, 1.0)
    base_mileage = _num(anchor.get("mileage")) or 0
    adjustments: list[dict[str, Any]] = []
    value = good_value
    new_mileage = _num(mileage) if mileage is not None else base_mileage
    if new_mileage is not None and new_mileage > base_mileage:
        extra = new_mileage - base_mileage
        rate = _DEPRECIATION_PER_MILE.get(currency, 0.08)
        drop = round(extra * rate, 2)
        value = max(0.0, value - drop)
        adjustments.append({"factor": "mileage", "extra_distance": extra, "per_unit": rate, "delta": -drop})
    cond = (condition or anchor_cond).lower()
    mult = _CONDITION_MULTIPLIER.get(cond)
    if mult is None:
        raise RefreshError(f"condition must be one of {sorted(_CONDITION_MULTIPLIER)}")
    before = value
    value = round(value * mult, 2)
    if condition or mult != 1.0:
        adjustments.append({"factor": "condition", "condition": cond, "multiplier": mult,
                            "delta": round(value - before, 2)})
    return {"value": round(value, 2), "mileage": new_mileage, "condition": cond, "adjustments": adjustments,
            "currency": currency}


def refresh_valuation(customer_id: str, mileage: float | int | None = None,
                      condition: str | None = None,
                      env_prefix: str | None = None, vin: str | None = None) -> dict[str, Any]:
    """Re-value a customer's trade after a mileage/condition update (§5.12).

    Baselines on the customer's latest valuation, applies a transparent
    mileage-delta depreciation and a condition multiplier, and writes a NEW
    append-only DEALER_Valuations row (history preserved). Recomputes equity =
    trade_value - payoff. Returns the new valuation with the adjustment inputs
    so the change is explainable.

    Computed from the car's ANCHOR valuation (valuation_chain), never from the last
    re-valuation, so re-valuing the same car at the same mileage and condition returns the
    same number every time. Inputs left out keep the last recorded mileage / condition."""
    base, latest = valuation_chain(customer_id, env_prefix, vin=vin)
    if base is None:
        raise RefreshError(f"no baseline valuation for {customer_id}; cannot refresh")
    if mileage is None:
        mileage = _num((latest or {}).get("mileage"))
    if not condition and (latest or {}).get("condition"):
        condition = latest["condition"]
    rv = revalue(base, mileage, condition)
    currency, value, new_mileage, adjustments = rv["currency"], rv["value"], rv["mileage"], rv["adjustments"]
    base_value = _num(base.get("trade_value")) or 0
    payoff = _num((latest or base).get("payoff")) or 0
    equity = round(value - payoff, 2)
    _ts = _dt.datetime.now(_dt.timezone.utc)
    now = _ts.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    # microsecond-precision suffix so two refreshes in the same second don't
    # collide on the valuation id (history is append-only, never overwritten).
    new_id = f"VAL-{customer_id}-{int(_ts.timestamp() * 1000)}-{_ts.microsecond % 1000:03d}"
    row = {
        "valuation_id": new_id,
        "customer_id": customer_id,
        "dealer_id": base.get("dealer_id"),
        "make": base.get("make"), "model": base.get("model"), "year": base.get("year"),
        "mileage": new_mileage, "condition": rv["condition"],
        "trade_value": value, "payoff": payoff, "equity": equity, "currency": currency,
        "valued_at": now,
        "baseline_valuation_id": base.get("valuation_id"),
        "adjustments": adjustments,
        "source_ref": "A4.refresh_valuation (transparent depreciation model)",
    }
    ddb.put_item("DEALER_Valuations", row, env_prefix)
    return {
        "valuation_id": new_id,
        "customer_id": customer_id,
        "baseline_valuation_id": base.get("valuation_id"),
        "baseline_trade_value": base_value,
        "new_trade_value": value,
        "payoff": payoff,
        "equity": equity,
        "equity_position": "positive" if equity > _POSITIVE_EQUITY_FLAG else "negative",
        "currency": currency,
        "adjustments": adjustments,
        "valued_at": now,
        "source_ref": row["source_ref"],
    }
