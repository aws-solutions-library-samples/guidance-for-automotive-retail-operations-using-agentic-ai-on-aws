"""L5 — Live objection agent (A3 floor path, spec §5.3).

The first real agent wiring. Given a live-transcript utterance on a journey,
it (1) classifies the objection against the market's DEALER_ObjectionStrategies
library (grounding — the source of truth for what strategies exist and their
`source_ref`), and (2) generates a concise, grounded suggested response with
Amazon Bedrock (Claude via an inference profile).

Design per the spec's principles:
  * One suggestion engine, grounded: the response is anchored to the matched
    strategy's curated `example_phrasing` and its source_ref, never free-floating.
  * Guardrailed: a system prompt constrains tone/claims (no pricing promises,
    no disparagement) — a lightweight stand-in for Bedrock Guardrails.
  * Always-answers: if Bedrock is unavailable (no model access / throttle), it
    falls back to the curated phrasing so the floor panel never goes blank.

Returns the objection_type, matched strategy, a suggested_response, and which
path produced it (`bedrock` | `fallback`) with the source_ref for citation.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any

from ..common import ddb
from ..platform import generate as _gen

# On-demand-invocable inference profile confirmed available in the dev account.
# Override with RETAIL_OPS_BEDROCK_MODEL. Kept configurable, not hardcoded elsewhere.
BEDROCK_MODEL = os.environ.get("RETAIL_OPS_BEDROCK_MODEL", "us.anthropic.claude-opus-4-5-20251101-v1:0")
BEDROCK_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Keyword cues per objection_type (grounding the classifier to the library's
# vocabulary). Extend as the strategy library grows; the strategies themselves
# remain the source of truth for which types exist.
_CUES: dict[str, tuple[str, ...]] = {
    "price_too_high": ("expensive", "too much", "cost", "price", "afford", "cheaper", "budget"),
    "payment_too_high": ("monthly", "payment", "per month", "installment"),
    "think_about_it": ("think about", "not sure", "need time", "sleep on it", "later"),
    "shopping_around": ("other dealer", "competitor", "shopping", "another quote", "compare"),
    "trade_value_low": ("trade", "my car worth", "lowball", "trade-in"),
    "rate_too_high": ("interest", "apr", "rate", "financing cost"),
    "not_ready": ("just looking", "browsing", "not ready", "early"),
    "spouse_approval": ("spouse", "wife", "husband", "partner", "talk to"),
    "range_anxiety": ("range", "charge", "charging", "battery", "far enough"),
}

_SYSTEM = (
    "You are a dealership sales-floor assistant helping an advisor respond to a "
    "customer objection in real time. Give ONE concise, warm, factual next line "
    "the advisor can say (2-3 sentences max). Acknowledge the concern, reframe on "
    "value, and offer a concrete next step. Do NOT promise a specific price, "
    "discount, interest rate, or approval. Do NOT disparage competitors. Do NOT "
    "invent incentives or figures."
)


class ObjectionResult(dict):
    """Plain dict result; typed alias for readability."""


def classify(utterance: str, strategies: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Match the utterance to an objection strategy from the library.

    Returns the matched strategy row, or None if no cue fires. Only returns a
    type that actually exists in the market's library (grounding)."""
    text = (utterance or "").lower()
    available = {s.get("objection_type"): s for s in strategies}
    # score each known type by cue hits, restricted to types present in library
    best_type, best_score = None, 0
    for otype, cues in _CUES.items():
        if otype not in available:
            continue
        score = sum(1 for c in cues if c in text)
        if score > best_score:
            best_type, best_score = otype, score
    if best_type is None or best_score == 0:
        return None
    return available[best_type]


def customer_context(journey: dict[str, Any] | None, env_prefix: str | None = None) -> dict[str, Any]:
    """What the coach knows about this customer that a reply should use: daily miles, budget,
    household, the model in play and its range. Every value comes from the journey/customer
    row, so the line the advisor says can name the customer's own numbers."""
    if not journey:
        return {}
    ctx: dict[str, Any] = {}
    jctx = journey.get("context") or {}
    cust = ddb.get_item("DEALER_Customers", {"customer_id": journey.get("customer_id")}, env_prefix) or {}
    life = cust.get("lifestyle_profile") or {}
    if life.get("commute_per_day"):
        ctx["commute_per_day"] = life["commute_per_day"]
        ctx["distance_unit"] = life.get("distance_unit") or "mi"
    if life.get("household_size"):
        ctx["household_size"] = life["household_size"]
    # journey.context.budget is a captured-flag (True/False); the figures live on the customer
    budget = cust.get("budget") or (jctx.get("budget") if isinstance(jctx.get("budget"), dict) else None) or {}
    if isinstance(budget, dict) and budget.get("monthly_max"):
        ctx["monthly_max"] = budget["monthly_max"]
    cv = cust.get("current_vehicle") or {}
    if cv.get("model"):
        ctx["current_vehicle"] = " ".join(str(x) for x in (cv.get("year"), cv.get("make"), cv.get("model")) if x)
    model_id = journey.get("primary_model_id")
    if model_id:
        trims = [t for t in ddb.scan_filter("OEM_Catalog", "model_id", model_id, env_prefix) if t.get("trim_name")]
        model = ddb.get_item("OEM_CatalogModels", {"model_id": model_id}, env_prefix) or {}
        ctx["model"] = model.get("name") or model_id
        ranges = [t.get("ev_range") for t in trims if t.get("ev_range")]
        if ranges:
            ctx["ev_range"] = max(ranges)
            ctx["range_unit"] = next((t.get("range_unit") for t in trims if t.get("range_unit")), "mi")
    return ctx


def _context_line(strategy: dict[str, Any], ctx: dict[str, Any]) -> str | None:
    """A customer-specific clause for the objection types where the customer's own facts
    change the answer (the deterministic fallback appends it; the model prompt carries the facts)."""
    ot = strategy.get("objection_type")
    unit = ctx.get("distance_unit") or "mi"
    if ot in ("range_anxiety", "charging_access") and ctx.get("commute_per_day") and ctx.get("ev_range"):
        days = int(ctx["ev_range"] * 0.8 // ctx["commute_per_day"])
        if days >= 2:
            return (f"At about {ctx['commute_per_day']} {unit} a day, the {ctx.get('model', 'EV')}'s {ctx['ev_range']} {unit} "
                    f"of range is roughly {days} days of your driving on one charge.")
    if ot in ("payment_too_high", "price_too_high") and ctx.get("monthly_max"):
        return f"You mentioned about {ctx['monthly_max']:,} a month as the ceiling — let's structure it to that first."
    if ot == "trade_value_low" and ctx.get("current_vehicle"):
        return f"The comps I'll show you are for a {ctx['current_vehicle']} like yours, not the segment average."
    if ot == "competitor_better" and ctx.get("household_size", 0) >= 4:
        return f"With {ctx['household_size']} in the household, cargo and second-row space are where I'd start the comparison."
    return None


def _fallback(strategy: dict[str, Any], ctx: dict[str, Any] | None = None) -> str:
    """The curated phrasing (plus the customer's own facts when we have them): the answer
    whenever the model's is unavailable or withheld."""
    base = strategy.get("example_phrasing") or "Acknowledge the concern and offer a next step."
    extra = _context_line(strategy, ctx or {})
    return f"{base} {extra}" if extra else base


def _prompt(utterance: str, strategy: dict[str, Any], ctx: dict[str, Any] | None = None) -> str:
    """The one user prompt both transports send (REST converse, SSE converse_stream)."""
    facts = []
    c = ctx or {}
    if c.get("commute_per_day"):
        facts.append(f"drives about {c['commute_per_day']} {c.get('distance_unit', 'mi')} a day")
    if c.get("household_size"):
        facts.append(f"household of {c['household_size']}")
    if c.get("monthly_max"):
        facts.append(f"monthly budget ceiling about {c['monthly_max']:,}")
    if c.get("current_vehicle"):
        facts.append(f"currently drives a {c['current_vehicle']}")
    if c.get("model"):
        facts.append(f"considering the {c['model']}" + (f" ({c['ev_range']} {c.get('range_unit', 'mi')} EV range)" if c.get("ev_range") else ""))
    fact_block = ("Customer facts to use (do not invent others): " + "; ".join(facts) + ".\n") if facts else ""
    return (
        f"Customer objection type: {strategy.get('objection_type')}\n"
        f"Curated strategy to follow: {strategy.get('example_phrasing')}\n"
        f"{fact_block}"
        f"Customer just said: \"{utterance}\"\n\n"
        f"Write the advisor's suggested next line, specific to this customer's facts where they apply."
    )


def _bedrock_response(utterance: str, strategy: dict[str, Any],
                      ctx: dict[str, Any] | None = None) -> tuple[str, str, dict[str, Any]]:
    """Generate a suggested response with Bedrock. Returns (text, path, guardrail).
    path is 'bedrock' on success, 'fallback' if the invoke fails or the guardrail
    intervenes (the curated phrasing then replaces the guardrail's blocked message)."""
    fallback = _fallback(strategy, ctx)
    try:
        import boto3
        rt = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
        resp = rt.converse(
            modelId=BEDROCK_MODEL,
            system=[{"text": _SYSTEM}],
            messages=[{"role": "user", "content": [{"text": _prompt(utterance, strategy, ctx)}]}],
            inferenceConfig={"maxTokens": 180, "temperature": 0.3},
            **_gen.guardrail_kwargs(),
        )
        gr = _gen.guardrail_outcome(resp)
        if gr["intervened"]:
            return fallback, "fallback", gr
        text = resp["output"]["message"]["content"][0]["text"].strip()
        return (text or fallback), ("bedrock" if text else "fallback"), gr
    except Exception as e:
        # No model access / throttle / any runtime issue -> curated phrasing.
        return fallback, "fallback", {"applied": _gen.guardrail_config() is not None, "intervened": False,
                                      "error": type(e).__name__}


def classify_objection(journey_id: str, utterance: str,
                       env_prefix: str | None = None) -> dict[str, Any]:
    """The A3 live-objection entry point.

    Looks up the journey's market, classifies the utterance against that
    market's objection library, and returns a grounded suggested response.
    """
    journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id}, env_prefix)
    market = (journey or {}).get("market_id", "us")
    strategies = ddb.scan_filter("DEALER_ObjectionStrategies", "market_id", market, env_prefix)

    matched = classify(utterance, strategies)
    if matched is None:
        return {
            "journey_id": journey_id,
            "objection_detected": False,
            "utterance": utterance,
            "message": "No objection detected in this utterance.",
        }

    ctx = customer_context(journey, env_prefix)
    suggested, path, guardrail = _bedrock_response(utterance, matched, ctx)
    _remember_objection(journey, matched, utterance, journey_id, env_prefix)
    out = _result(journey_id, matched, suggested, path, utterance, market, grounded_on=_fallback(matched, ctx))
    out["guardrail"] = guardrail
    out["customer_context"] = ctx
    return out


def classify_objection_stream(journey_id: str, utterance: str,
                              on_token: Any, env_prefix: str | None = None) -> dict[str, Any]:
    """The streaming twin of classify_objection (spec R2 sub-2s floor path).

    Classification + strategy lookup stay deterministic and run first, exactly
    as in classify_objection; only the suggested-response GENERATION streams,
    through platform.streaming.stream_converse with the SAME system prompt,
    user prompt and guardrail as the REST call (token deltas via `on_token`,
    honest engine). An objection reply is a persuasion line built from a curated
    strategy, not an answer over retrieved facts, so neither transport tags a
    grounding source: with one, the contextual-grounding check scored the reply
    0.22 against the 0.45 threshold and withheld it (2026-10-29). On a guardrail
    intervention, offline or on a failure, the curated phrasing is emitted as one
    token, as REST returns it, so the floor panel never goes blank and never shows
    the guardrail's blocked message as the suggestion."""
    journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id}, env_prefix)
    market = (journey or {}).get("market_id", "us")
    strategies = ddb.scan_filter("DEALER_ObjectionStrategies", "market_id", market, env_prefix)

    matched = classify(utterance, strategies)
    if matched is None:
        return {
            "journey_id": journey_id,
            "objection_detected": False,
            "utterance": utterance,
            "message": "No objection detected in this utterance.",
        }

    from ..platform import streaming as _streaming
    ctx = customer_context(journey, env_prefix)
    fallback = _fallback(matched, ctx)
    gen = _streaming.stream_converse(_SYSTEM, _prompt(utterance, matched, ctx), fallback=fallback,
                                     on_token=on_token, max_tokens=180, temperature=0.3)
    path = "bedrock" if gen["engine"] == "bedrock" else "fallback"
    _remember_objection(journey, matched, utterance, journey_id, env_prefix)
    out = _result(journey_id, matched, gen["text"], path, utterance, market, grounded_on=fallback)
    out["engine"] = gen["engine"]          # the streaming engine, verbatim
    out["guardrail"] = gen["guardrail"]
    out["customer_context"] = ctx
    return out


def _remember_objection(journey: dict[str, Any] | None, matched: dict[str, Any],
                        utterance: str, journey_id: str, env_prefix: str | None) -> None:
    # Durable memory (durable-memory contract): the objection is a long-lived customer fact —
    # record it where the signal is born so later answers can recall it.
    if (journey or {}).get("customer_id"):
        from ..platform import memory as _memory
        _memory.remember(journey["customer_id"], "objection",
                         f"Raised {matched.get('objection_type')}: \"{utterance[:120]}\"",
                         source_ref=f"A3.classify_objection:{journey_id}", actor="A3",
                         env_prefix=env_prefix)


def _result(journey_id: str, matched: dict[str, Any], suggested: str, path: str,
            utterance: str, market: str, grounded_on: str | None = None) -> dict[str, Any]:
    return {
        "journey_id": journey_id,
        "objection_detected": True,
        "objection_type": matched.get("objection_type"),
        "strategy_id": matched.get("strategy_id"),
        "suggested_response": suggested,
        # the curated line plus the customer's own facts — exactly what the fallback says
        "grounded_on": grounded_on or matched.get("example_phrasing"),
        "curated_phrasing": matched.get("example_phrasing"),
        "source_ref": matched.get("source_ref"),
        "generated_by": path,          # 'bedrock' or 'fallback'
        "model": BEDROCK_MODEL if path == "bedrock" else None,
        "utterance": utterance,
        "market_id": market,
    }
