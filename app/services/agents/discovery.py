"""L5 — A1 Personalized Vehicle Discovery agent (spec §5.5, use-case A1).

Ranks catalog trims for a journey's customer against budget and lifestyle, and
cross-references dealer inventory for VIN-level availability — the "Vehicle
matches (ranked)" list in the Advisor center column (§5.3).

Deterministic scoring (transparent, testable). An optional Bedrock call adds a
one-line "why this fits" rationale for the top match, with the same
always-answers fallback as the other agents. Each match carries a source_ref and
the components of its score so the ranking is explainable, never a black box.

  rank_vehicles(journey_id) -> ranked matches with score breakdown + availability.
"""
from __future__ import annotations

import os
from typing import Any

from ..common import ddb
from ..platform import generate as _gen

BEDROCK_MODEL = os.environ.get("RETAIL_OPS_BEDROCK_MODEL", "us.anthropic.claude-opus-4-5-20251101-v1:0")
BEDROCK_REGION = os.environ.get("AWS_REGION", "us-east-1")

_TOP_N = 5


class NotFound(Exception):
    pass


def rank_vehicles(journey_id: str, env_prefix: str | None = None) -> dict[str, Any]:
    """Rank catalog trims for the journey's customer. Raises NotFound if unknown."""
    journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id}, env_prefix)
    if journey is None:
        raise NotFound(f"journey {journey_id} not found")
    customer_id = journey.get("customer_id")
    dealer_id = journey.get("dealer_id")
    market = journey.get("market_id", "us")
    customer = ddb.get_item("DEALER_Customers", {"customer_id": customer_id}, env_prefix) if customer_id else None

    budget = (customer or {}).get("budget") or {}
    lifestyle = (customer or {}).get("lifestyle_profile") or {}
    # What the customer asked for comes first: the lead's vehicle of interest (carried into the
    # journey context by the Lead Desk) or the model they configured. A match list that ignores
    # "do you have the Trailhand in stock?" is not a match list.
    ctx = journey.get("context") or {}
    # only an explicit request counts (a lead's vehicle of interest): a model the customer merely
    # configured must not crowd the list with its own trims — the matcher still offers alternatives
    requested = ctx.get("requested_model_id")
    requested_name = None
    if requested:
        m = ddb.get_item("OEM_CatalogModels", {"model_id": requested}, env_prefix) or {}
        requested_name = m.get("name") or requested

    catalog = ddb.scan_filter("OEM_Catalog", "currency", budget.get("currency", "USD"), env_prefix) \
        if budget.get("currency") else ddb.scan_all("OEM_Catalog", env_prefix)
    # inventory at this dealer, indexed by trim for availability
    inv = ddb.scan_filter("DEALER_Inventory", "dealer_id", dealer_id, env_prefix) if dealer_id else []
    avail_by_trim: dict[str, list[dict[str, Any]]] = {}
    for v in inv:
        if v.get("status") == "in_stock":
            avail_by_trim.setdefault(v.get("trim_id"), []).append(v)

    scored = []
    for trim in catalog:
        if trim.get("kind") == "package":       # packages are options, not vehicles
            continue
        s = _score(trim, budget, lifestyle, avail_by_trim, requested)
        if s["score"] > 0:
            scored.append(s)
    scored.sort(key=lambda x: (x["score"], x.get("requested", False)), reverse=True)
    top = scored[:_TOP_N]
    # The customer's own request is always on the list, even when its budget fit is partial —
    # the advisor has to be able to answer the question that was asked.
    if requested and not any(t.get("requested") for t in top):
        asked = sorted((s for s in scored if s.get("requested")), key=lambda x: x["score"], reverse=True)
        if asked:
            top = (top[:_TOP_N - 1] + [asked[0]]) if len(top) >= _TOP_N else top + [asked[0]]

    if top:
        top[0]["rationale"], top[0]["rationale_by"] = _rationale(top[0], budget, lifestyle, requested_name)

    return {
        "journey_id": journey_id,
        "customer_id": customer_id,
        "dealer_id": dealer_id,
        "market_id": market,
        "budget": budget,
        "requested_model_id": requested,
        "requested_model_name": requested_name,
        "requested_from": "lead" if requested else None,
        "matches": top,
        "considered": len(scored),
    }


# --- scoring ----------------------------------------------------------------

_ASKED_FOR_MAX = 30   # the customer named this model (lead request / saved build)


def _score(trim: dict[str, Any], budget: dict[str, Any], lifestyle: dict[str, Any],
           avail_by_trim: dict[str, list[dict[str, Any]]], requested_model_id: str | None = None) -> dict[str, Any]:
    """Transparent additive score, normalized to 100. Components are returned for explainability.

    budget 40 · powertrain 25 · seats 15 · availability 20, plus `asked_for` 30 when the
    customer named a model: a trim of that model scores the full 30 and the rest 0, so what
    they asked about leads unless it does not fit the budget at all."""
    price = _num(trim.get("list_price"))
    comp: dict[str, float] = {}

    # Budget fit (max 40): within band = full; near = partial; outside = 0 and disqualified.
    pmin, pmax = _num(budget.get("price_min")), _num(budget.get("price_max"))
    if price is not None and pmax is not None:
        if pmin is not None and pmin <= price <= pmax:
            comp["budget"] = 40
        elif price <= pmax * 1.10:      # within 10% over the ceiling
            comp["budget"] = 20
        else:
            comp["budget"] = 0
    else:
        comp["budget"] = 10             # unknown budget: neutral small credit

    # Powertrain match (max 25): honor lifestyle powertrain_openness.
    openness = (lifestyle.get("powertrain_openness") or "").lower()
    pt = (trim.get("powertrain") or "").upper()
    if openness == "ev":
        comp["powertrain"] = 25 if pt == "EV" else (12 if pt in ("PHEV", "HEV") else 5)
    elif openness == "ice":
        comp["powertrain"] = 25 if pt == "ICE" else 8
    else:
        comp["powertrain"] = 15         # open/unknown

    # Seats vs household (max 15).
    seats = _num(trim.get("seats"))
    hh = _num(lifestyle.get("household_size"))
    if seats is not None and hh is not None:
        comp["seats"] = 15 if seats >= hh else 5
    else:
        comp["seats"] = 8

    # Availability at this dealer (max 20): in-stock VINs of this trim.
    vins = avail_by_trim.get(trim.get("trim_id"), [])
    comp["availability"] = 20 if vins else 0

    # The model the customer asked about (max 30) — only when they named one.
    is_requested = bool(requested_model_id) and trim.get("model_id") == requested_model_id
    max_total = 100
    if requested_model_id:
        comp["asked_for"] = _ASKED_FOR_MAX if is_requested else 0
        max_total += _ASKED_FOR_MAX

    total = round(sum(comp.values()) / max_total * 100)
    # a match with zero budget fit is disqualified regardless of other credit
    if comp["budget"] == 0:
        total = 0

    return {
        "model_id": trim.get("model_id"),
        "trim_id": trim.get("trim_id"),
        "trim_name": trim.get("trim_name"),
        "list_price": price,
        "currency": trim.get("currency"),
        "powertrain": trim.get("powertrain"),
        "seats": seats,
        "ev_range": trim.get("ev_range"),
        "features": (trim.get("features") or [])[:4],
        "score": total,
        "score_breakdown": comp,
        "requested": is_requested,
        "in_stock_count": len(vins),
        "example_vin": vins[0].get("vin") if vins else None,
        "source_ref": trim.get("source_ref"),
    }


def _rationale(match: dict[str, Any], budget: dict[str, Any], lifestyle: dict[str, Any],
               requested_name: str | None = None) -> tuple[str, str]:
    asked = f"the {requested_name} they asked about — " if requested_name and match.get("requested") else ""
    budget_txt = (f"fits the {budget.get('currency','')} {budget.get('price_min','')}-{budget.get('price_max','')} budget"
                  if budget.get("price_max") else "no budget on file yet")
    templated = (
        f"{asked}{match.get('trim_name','')} ({match.get('powertrain','')}) {budget_txt}"
        + (f"; {match['in_stock_count']} in stock now." if match.get("in_stock_count") else "; available to order.")
    )
    if requested_name and not match.get("requested"):
        templated += f" (They asked about the {requested_name}; it is on the list below.)"
    if not _gen.enabled():
        return templated, "fallback"
    try:
        import boto3
        rt = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
        prompt = (
            "In one sentence, tell a sales advisor why this vehicle fits this "
            "customer. Factual, no price/discount promises.\n"
            f"trim={match.get('trim_name')} powertrain={match.get('powertrain')} "
            f"price={match.get('list_price')} {match.get('currency')} seats={match.get('seats')} "
            f"in_stock={match.get('in_stock_count')} lifestyle={lifestyle}"
            + (f" customer_asked_about={requested_name} (this IS that model)" if requested_name and match.get("requested")
               else f" customer_asked_about={requested_name} (this is a different model; say so)" if requested_name else "")
        )
        resp = rt.converse(
            modelId=BEDROCK_MODEL,
            system=[{"text": "You are a concise dealership discovery assistant. "
                             "No price or discount promises."}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 100, "temperature": 0.3},
            **_gen.guardrail_kwargs(),
        )
        text = resp["output"]["message"]["content"][0]["text"].strip()
        if resp.get("stopReason") == "guardrail_intervened":   # never surface the block message as a rationale
            return templated, "fallback (guardrail)"
        return (text or templated), ("bedrock" if text else "fallback")
    except Exception:
        return templated, "fallback"


def _num(v: Any) -> float | int | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --- infer_lifestyle_profile (A1, spec §3.1) --------------------------------

# The LifestyleProfile fields A1 reasons over, and the intake question to ask
# when a field is low-confidence (the ≤4 questions surface on /shop).
_LIFESTYLE_FIELDS = (
    "household_size", "commute_mi_per_day", "road_trip_frequency", "towing_or_hauling",
    "outdoor_activities", "tech_affinity", "safety_priority", "powertrain_openness",
    "ownership_style",
)
_INTAKE_QUESTIONS = {
    "household_size": "How many people do you usually need to seat?",
    "commute_mi_per_day": "Roughly how far is your daily drive?",
    "towing_or_hauling": "Do you tow or haul anything (trailer, boat, gear)?",
    "powertrain_openness": "Are you open to electric or hybrid, or prefer gas?",
    "ownership_style": "Are you looking to buy or to lease?",
    "road_trip_frequency": "How often do you take long road trips?",
    "outdoor_activities": "Any outdoor activities the vehicle should support?",
    "tech_affinity": "How important are the latest tech features to you?",
    "safety_priority": "How high a priority is advanced safety tech?",
}
_CONFIDENCE_ASK_BELOW = 0.5  # fields at/below this confidence get an intake question

# Browsing signals that inform specific fields (grounding the inference).
_EV_CONTENT = ("ev", "electric", "charging", "battery", "range")


def _confidence(field: str, value: Any, sources: list[str]) -> float:
    """Confidence for an inferred field: an explicit answer is certain; a value
    from the stored profile or a supporting signal is medium; a default guess is
    low. Grounded, not arbitrary — `sources` records where the value came from."""
    if "answer" in sources:
        return 1.0
    if value is None or value == [] or value == "undecided":
        return 0.25
    if "profile" in sources and "signal" in sources:
        return 0.85
    if "profile" in sources or "signal" in sources:
        return 0.7
    return 0.4


def infer_lifestyle_profile(customer: dict[str, Any] | None,
                            signals: list[dict[str, Any]] | None = None,
                            answers: dict[str, Any] | None = None) -> dict[str, Any]:
    """Produce a LifestyleProfile with per-field confidence (spec §3.1).

    Merges three grounded inputs, in increasing authority:
      1. the customer's stored lifestyle_profile / CRM attributes,
      2. browsing signals (EV content reads -> powertrain openness, compares ->
         active shopping, configure_saves -> ownership intent),
      3. explicit intake answers (authoritative).
    Returns {profile, confidence{field:score}, intake_questions[<=4], ready}.
    `intake_questions` are the lowest-confidence fields still worth asking; A1
    asks ONLY those, honoring the "<=4 questions" rule."""
    cust = customer or {}
    stored = dict(cust.get("lifestyle_profile") or {})
    answers = answers or {}
    signals = signals or []

    # signal-derived hints
    sig_types = [s.get("type") for s in signals]
    content_titles = " ".join((s.get("content_title") or "").lower() for s in signals)
    ev_interest = any(w in content_titles for w in _EV_CONTENT) or \
        any((s.get("type") == "model_view" and "ev" in (s.get("model_id") or "").lower()) for s in signals)

    profile: dict[str, Any] = {}
    confidence: dict[str, float] = {}

    # seed from stored profile + CRM
    seed = {
        "household_size": stored.get("household_size") or cust.get("household_size"),
        "commute_mi_per_day": stored.get("commute_per_day") or cust.get("commute_per_day"),
        "road_trip_frequency": stored.get("road_trip_frequency"),
        "towing_or_hauling": stored.get("towing_or_hauling"),
        "outdoor_activities": stored.get("outdoor_activities") or [],
        "tech_affinity": stored.get("tech_affinity"),
        "safety_priority": stored.get("safety_priority"),
        "powertrain_openness": stored.get("powertrain_openness"),
        "ownership_style": stored.get("ownership_style")
            or (cust.get("current_vehicle") or {}).get("ownership"),
    }

    for field in _LIFESTYLE_FIELDS:
        sources: list[str] = []
        value = seed.get(field)
        if value not in (None, [], ""):
            sources.append("profile")
        # signal enrichment for the fields browsing can inform
        if field == "powertrain_openness" and ev_interest:
            value = value or "ev"
            sources.append("signal")
        if field == "ownership_style" and "configure_save" in sig_types and not value:
            value = "buy"
            sources.append("signal")
        # explicit answer wins
        if field in answers:
            value = answers[field]
            sources = ["answer"]
        profile[field] = value
        confidence[field] = _confidence(field, value, sources)

    # budget + ownership carried straight from CRM (authoritative there)
    profile["budget"] = cust.get("budget") or {}

    # the intake questions: the lowest-confidence fields, capped at 4
    low = sorted((f for f in _LIFESTYLE_FIELDS if confidence[f] <= _CONFIDENCE_ASK_BELOW),
                 key=lambda f: confidence[f])
    intake = [{"field": f, "question": _INTAKE_QUESTIONS[f]} for f in low[:4]]

    overall = round(sum(confidence.values()) / len(confidence), 2)
    return {
        "customer_id": cust.get("customer_id"),
        "profile": profile,
        "confidence": confidence,
        "overall_confidence": overall,
        "intake_questions": intake,
        "ready": len(intake) == 0,          # enough confidence to rank without asking
        "source_ref": "A1.infer_lifestyle_profile (profile+signals+answers)",
    }


# --- recommend_trim_and_packages (A1, spec §3.1) ----------------------------

# Map a package's addresses_need to the LifestyleProfile signal that justifies it,
# and the human explanation A1 attaches ("explains each package by need").
_NEED_EXPLAIN = {
    "towing_or_hauling": ("towing_or_hauling", "you tow or haul"),
    "tech_affinity": ("tech_affinity", "you value the latest tech"),
    "commute": ("commute_mi_per_day", "your regular commute"),
    "household_size": ("household_size", "your household size"),
    "charging_access": ("powertrain_openness", "home charging for an EV"),
    "safety_priority": ("safety_priority", "safety is a priority"),
}


def _need_is_active(need: str, lifestyle: dict[str, Any]) -> bool:
    """True if the lifestyle profile justifies a package addressing `need`."""
    field, _ = _NEED_EXPLAIN.get(need, (None, None))
    if field is None:
        return False
    val = lifestyle.get(field)
    if need == "towing_or_hauling":
        return bool(val)
    if need == "tech_affinity":
        return str(val).lower() in ("high", "medium")
    if need == "safety_priority":
        return str(val).lower() in ("high", "medium")
    if need == "commute":
        return _num(val) is not None and _num(val) >= 20  # meaningful daily miles
    if need == "household_size":
        return _num(val) is not None and _num(val) >= 4
    if need == "charging_access":
        return str(val).lower() in ("ev", "phev", "hybrid")
    return bool(val)


def recommend_trim_and_packages(model_id: str, lifestyle: dict[str, Any] | None,
                                budget: dict[str, Any] | None,
                                env_prefix: str | None = None) -> dict[str, Any]:
    """Pick a trim + needs-justified packages for a model, within budget (§3.1).

    Chooses the highest trim whose price fits the budget ceiling, then adds only
    packages whose addresses_need is active in the lifestyle profile, each with a
    by-need explanation, while the running total stays under the ceiling. Returns
    the recommended_config with price_delta_vs_base and per-package reasons."""
    lifestyle = lifestyle or {}
    budget = budget or {}
    cat = ddb.scan_all("OEM_Catalog", env_prefix)
    trims = [r for r in cat if r.get("kind") is None and r.get("model_id") == model_id]
    packages = [r for r in cat if r.get("kind") == "package" and r.get("model_id") == model_id]
    if not trims:
        raise NotFound(f"no trims for model {model_id}")

    trims.sort(key=lambda t: _num(t.get("list_price")) or 0)
    base_price = _num(trims[0].get("list_price")) or 0
    ceiling = _num(budget.get("price_max"))

    # highest trim within the ceiling (else the base trim)
    affordable = [t for t in trims if ceiling is None or (_num(t.get("list_price")) or 0) <= ceiling]
    chosen_trim = (affordable or trims)[0] if not affordable else affordable[-1]
    trim_price = _num(chosen_trim.get("list_price")) or base_price

    # add needs-justified packages while staying under the ceiling
    running = trim_price
    chosen_pkgs: list[dict[str, Any]] = []
    for p in sorted(packages, key=lambda x: _num(x.get("list_price")) or 0):
        need = p.get("addresses_need")
        if not _need_is_active(need, lifestyle):
            continue
        price = _num(p.get("list_price")) or 0
        if ceiling is not None and running + price > ceiling:
            continue
        _, why = _NEED_EXPLAIN.get(need, (None, "your needs"))
        chosen_pkgs.append({"package_name": p.get("package_name"), "addresses_need": need,
                            "price": price, "reason": f"Recommended because {why}."})
        running += price

    return {
        "model_id": model_id,
        "recommended_config": {
            "trim_id": chosen_trim.get("trim_id"),
            "trim_name": chosen_trim.get("trim_name"),
            "trim_price": trim_price,
            "packages": chosen_pkgs,
            "options_total": sum(p["price"] for p in chosen_pkgs),
            "config_total": running,
            "price_delta_vs_base": round(running - base_price, 2),
            "currency": chosen_trim.get("currency"),
            "within_budget": ceiling is None or running <= ceiling,
        },
        "considered_trims": len(trims),
        "considered_packages": len(packages),
        "source_ref": "A1.recommend_trim_and_packages (OEM_Catalog trims+packages)",
    }


# --- price_build (A1, spec §3.1 / §2.5 market pricing) ----------------------

import datetime as _dt

# Simple, transparent monthly estimators (production uses the F&I connector).
from ..finance.payment import DEFAULT_APR_PCT as _DEFAULT_APR_PCT, DEFAULT_TERM_MONTHS as _DEFAULT_TERM_MONTHS  # noqa: E402
_LEASE_RESIDUAL_PCT = 0.55        # 3-yr residual assumption for a lease estimate
_LEASE_TERM_MONTHS = 36


def _today() -> str:
    return _dt.datetime.now(_dt.timezone.utc).date().isoformat()


# Incentives that are NOT a point-of-sale reduction of the VEHICLE price:
#  - loan-interest deductions (a tax deduction on interest, not cash off the car)
#  - home-charger / equipment rebates (real money, but not a vehicle-price cut)
# These are surfaced as informational (reduces_price=False) and never subtracted
# from the out-the-door price — subtracting them would overstate the deal.
_NON_PRICE_INCENTIVE_IDS = {"US-FED-LOAN-INTEREST", "US-UTIL-HOME-CHARGER"}
_NON_PRICE_KEYWORDS = ("loan interest", "interest deduction", "home charger", "charger rebate", "wallbox")


def _reduces_vehicle_price(inc: dict[str, Any]) -> bool:
    if inc.get("incentive_id") in _NON_PRICE_INCENTIVE_IDS:
        return False
    name = (inc.get("name") or "").lower()
    return not any(k in name for k in _NON_PRICE_KEYWORDS)


def _incentive_applies(inc: dict[str, Any], powertrain: str, state: str | None, today: str) -> bool:
    pts = [p.upper() for p in (inc.get("powertrains") or [])]
    if pts and powertrain and powertrain.upper() not in pts:
        return False
    scope = (inc.get("scope") or "").upper()
    if scope and scope not in ("US", "DE", "") and state and scope != state.upper():
        return False
    vf, vt = inc.get("valid_from"), inc.get("valid_to")
    if vf and today < vf:
        return False
    if vt and today > vt:
        return False
    return inc.get("status") == "active"


def _monthly(principal: float, apr_pct: float, term: int) -> float:
    from ..finance import payment
    return payment.monthly(principal, apr_pct, term)      # the one formula (finance/payment.py)


def price_build(config_total: float | int, powertrain: str, market: str = "us",
                state: str | None = None, down: float | int = 0,
                env_prefix: str | None = None) -> dict[str, Any]:
    """Price a configured build, market-correct (spec §2.5).

    US: list price is pre-tax -> add state (+ local) sales tax; incentives in USD.
    DE: list price is VAT-inclusive (19%) -> show the VAT component, don't add it
        again; incentives in EUR.
    Applies only incentives whose powertrain, scope (state/country), and valid
    dates match. Returns the price breakdown + est. monthly (buy & lease), every
    figure traceable to the rule/incentive that produced it."""
    market = (market or "us").lower()
    mkt = ddb.get_item("OEM_Markets", {"market_id": market}, env_prefix) or {}
    currency = mkt.get("currency") or ("EUR" if market == "de" else "USD")
    base = float(config_total)
    today = _today()

    # incentives (scope + powertrain + date matched)
    amount_field = "amount_eur" if market == "de" else "amount_usd"
    applied = []
    incentive_total = 0.0
    for inc in ddb.scan_filter("OEM_Incentives", "market_id", market, env_prefix):
        if not _incentive_applies(inc, powertrain, state, today):
            continue
        amt = _num(inc.get(amount_field))
        if inc.get("apr_special"):
            # a captive-finance rate program: no cash amount; the F&I desk applies it to the
            # lender offer when the buyer's tier qualifies (fni._special_applies)
            applied.append({"incentive_id": inc.get("incentive_id"), "name": inc.get("name"),
                            "tier": inc.get("tier"), "amount": None, "reduces_price": False, "kind": "apr",
                            "apr_special": inc["apr_special"],
                            "note": "APR program — applies to the captive lender's offer, not the price",
                            "source_ref": inc.get("source_ref")})
            continue
        if not amt:
            continue
        reduces = _reduces_vehicle_price(inc)
        applied.append({"incentive_id": inc.get("incentive_id"), "name": inc.get("name"),
                        "tier": inc.get("tier"), "amount": amt, "reduces_price": reduces,
                        "kind": "price" if reduces else "informational",
                        "note": None if reduces else "informational — not a vehicle-price reduction",
                        "source_ref": inc.get("source_ref")})
        if reduces:                     # only point-of-sale reductions hit the price
            incentive_total += amt

    # tax, market-correct
    tax_lines = []
    if market == "de" or mkt.get("list_price_includes_tax"):
        vat_pct = _num(mkt.get("vat_pct")) or 19
        vat_component = round(base - base / (1 + vat_pct / 100), 2)   # VAT already inside base
        tax_lines.append({"kind": "vat_included", "pct": vat_pct, "amount": vat_component})
        price_after_incentives = round(base - incentive_total, 2)
        total_out_the_door = price_after_incentives                  # VAT already in
    else:
        sr = ddb.get_item("OEM_StateRules", {"region_code": (state or "").upper()}, env_prefix) if state else None
        sales_pct = _num((sr or {}).get("sales_tax_pct")) or 0.0
        local_pct = _num((sr or {}).get("avg_local_tax_pct")) or 0.0
        taxable = base                                                # tax on price before incentives (typical US)
        tax_amt = round(taxable * (sales_pct + local_pct) / 100, 2)
        tax_lines.append({"kind": "sales_tax", "state": state, "pct": sales_pct + local_pct,
                          "amount": tax_amt, "source_ref": (sr or {}).get("source_refs")})
        price_after_incentives = round(base + tax_amt - incentive_total, 2)
        total_out_the_door = price_after_incentives

    financed = max(0.0, total_out_the_door - float(down))
    apr = _DEFAULT_APR_PCT.get(currency, 6.4)
    est_monthly_buy = _monthly(financed, apr, _DEFAULT_TERM_MONTHS)
    # lease: finance the depreciation (cap cost - residual) + rent charge
    residual = round(base * _LEASE_RESIDUAL_PCT, 2)
    lease_principal = max(0.0, total_out_the_door - residual - float(down))
    est_monthly_lease = _monthly(lease_principal, apr, _LEASE_TERM_MONTHS)

    return {
        "market": market,
        "currency": currency,
        "powertrain": powertrain,
        "state": state,
        "config_total": round(base, 2),
        "incentives_applied": applied,
        "incentives_total": round(incentive_total, 2),
        "tax": tax_lines,
        "price_after_incentives": price_after_incentives,
        "total_out_the_door": round(total_out_the_door, 2),
        "down": float(down),
        "amount_financed": round(financed, 2),
        "est_monthly": {
            "buy": est_monthly_buy, "buy_term_months": _DEFAULT_TERM_MONTHS,
            "lease": est_monthly_lease, "lease_term_months": _LEASE_TERM_MONTHS,
            "apr_pct": apr,
        },
        "source_ref": f"A1.price_build (OEM_Markets#{market} + state rules + incentives)",
    }


# --- search_inventory (A1, spec §3.1 — VIN-level availability) --------------

_SELLABLE = ("in_stock", "in_transit")   # reserved/sold are not offerable


def _vin_card(v: dict[str, Any], match: str) -> dict[str, Any]:
    return {
        "vin": v.get("vin"),
        "stock_number": v.get("stock_number"),
        "dealer_id": v.get("dealer_id"),
        "model_id": v.get("model_id"),
        "trim_id": v.get("trim_id"),
        "trim_name": v.get("trim_name"),
        "color": v.get("color"),
        "status": v.get("status"),
        "eta_days": _num(v.get("eta_days")) or 0,
        "list_price": _num(v.get("list_price")),
        "match": match,                   # exact | near | in_transit
    }


def search_inventory(model_id: str, trim_id: str | None = None,
                     dealer_id: str | None = None, market: str = "us",
                     radius_dealers: int = 6, env_prefix: str | None = None) -> dict[str, Any]:
    """VIN-level availability for a model/trim (spec §3.1).

    Tiers, in preference order:
      1. exact      — model + trim, in_stock, at the home dealer
      2. near       — model, different trim, in_stock, at the home dealer
      3. nearby     — model (+trim if given), in_stock, at other dealers in market
      4. in_transit — model (+trim if given), in_transit, with ETA
    Returns the best available VIN per tier plus counts, so the UI can always
    show at least one exact or near-match VIN with its dealer and ETA
    (acceptance #12). Never invents a VIN — only rows present in inventory."""
    rows = [v for v in ddb.scan_filter("DEALER_Inventory", "market_id", market, env_prefix)
            if v.get("model_id") == model_id and v.get("status") in _SELLABLE]

    exact, near, nearby, transit = [], [], [], []
    for v in rows:
        same_trim = (trim_id is None) or (v.get("trim_id") == trim_id)
        at_home = dealer_id is not None and v.get("dealer_id") == dealer_id
        if v.get("status") == "in_transit":
            if same_trim:
                transit.append(_vin_card(v, "in_transit"))
            continue
        # in_stock
        if at_home and same_trim:
            exact.append(_vin_card(v, "exact"))
        elif at_home and not same_trim:
            near.append(_vin_card(v, "near"))
        elif same_trim:
            nearby.append(_vin_card(v, "nearby"))

    # sort each tier: soonest ETA, then lowest price
    def _rank(c): return (c["eta_days"], c["list_price"] if c["list_price"] is not None else 1e12)
    for tier in (exact, near, nearby, transit):
        tier.sort(key=_rank)

    # the single best offer to surface (exact > near > nearby > in_transit)
    best = (exact or near or nearby or transit)
    best_vin = best[0] if best else None

    return {
        "model_id": model_id,
        "trim_id": trim_id,
        "home_dealer_id": dealer_id,
        "market": market,
        "in_stock_count": len(exact) + len(near) + len(nearby),
        "exact": exact[:5],
        "near_matches": near[:5],
        "nearby_dealers": nearby[:radius_dealers],
        "in_transit": transit[:5],
        "best_offer": best_vin,
        "nearest_dealer": (best_vin or {}).get("dealer_id"),
        "eta_days": (best_vin or {}).get("eta_days"),
        "source_ref": "A1.search_inventory (DEALER_Inventory VIN-level)",
    }
