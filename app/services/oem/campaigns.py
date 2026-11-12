"""L3/L5 — A5 Campaign ROI Optimizer (spec §5, use-case A5).

Attributes marketing spend to delivered vehicles and computes ROI per campaign:
campaign -> journeys carrying that campaign -> completed deliveries. Rolls up
KPIs and per-campaign cost-per-delivery, and produces a Bedrock budget-shift
insight (the "drafts budget-shift approvals nightly" behaviour, here read-only).

Data note: campaign ids in OEM_Campaigns are zero-padded (CMP-US-00004) while
DEALER_Journeys reference the short form (CMP-US-04). We join on a normalized
trailing-integer key and surface `id_normalization_applied: true` so the
mismatch is visible, not silently papered over.

  build_campaign_roi(scope, market) -> per-campaign spend / deliveries / ROI + insight.
  recommend_budget_shift(scope, ...) -> the autonomy-gated budget-shift recommendation.
  run_nightly_attribution(market)    -> A5 nightly job: ROI recompute -> approval drafts.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
from typing import Any

from ..common import authz, ddb

BEDROCK_MODEL = os.environ.get("RETAIL_OPS_BEDROCK_MODEL", "us.anthropic.claude-opus-4-5-20251101-v1:0")
BEDROCK_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Assumed gross margin per delivered vehicle for a simple ROI proxy (the lake
# doesn't carry per-deal gross; this is a transparent stand-in, flagged in output).
_ASSUMED_GROSS_PER_DELIVERY = 3000


def spend_to_date(c: dict[str, Any], now: _dt.datetime | None = None) -> float:
    """Media spend so far: the budget paced evenly over the flight (start_at → end_at).

    ROI is return on money SPENT. Charging a campaign its whole flight budget on day 10
    (or charging a campaign that has not started at all) makes every campaign read as a
    loss. No flight dates → the whole budget (the conservative reading)."""
    budget = float(_num(c.get("budget")) or 0)
    # paced by whole days (as of today's start, UTC): the figures — and the insight written over
    # them — hold for the day instead of drifting with every request
    now = (now or _dt.datetime.now(_dt.timezone.utc)).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        start = _dt.datetime.fromisoformat(str(c["start_at"]))
        end = _dt.datetime.fromisoformat(str(c["end_at"]))
    except (KeyError, TypeError, ValueError):
        return budget
    if now <= start:
        return 0.0
    if now >= end or end <= start:
        return budget
    return round(budget * (now - start).total_seconds() / (end - start).total_seconds(), 2)


def _norm_campaign(cid: str | None) -> int | None:
    """CMP-US-00004 / CMP-US-04 -> 4 (trailing integer)."""
    if not cid:
        return None
    m = re.search(r"(\d+)\s*$", str(cid))
    return int(m.group(1)) if m else None


def build_campaign_roi(scope: dict[str, Any], market_id: str | None = None,
                       env_prefix: str | None = None) -> dict[str, Any]:
    market = market_id or scope.get("market_id") or "us"
    node = scope.get("scope_node_id") or "OEM"
    districts = authz.district_ids_under(node, env_prefix)

    campaigns = ddb.scan_filter("OEM_Campaigns", "market_id", market, env_prefix)
    if districts:
        campaigns = [c for c in campaigns if c.get("district_id") in districts]

    journeys = ddb.scan_filter("DEALER_Journeys", "market_id", market, env_prefix)
    deliveries = ddb.scan_all("DEALER_Deliveries", env_prefix)

    # journey_id -> normalized campaign key. Detect the id-format mismatch: a
    # journey campaign_id that does not literally equal any campaign's id but
    # matches one after normalization means the normalized join is load-bearing.
    campaign_ids_literal = {c.get("campaign_id") for c in campaigns}
    normalized = False
    jid_to_camp: dict[str, int | None] = {}
    for j in journeys:
        raw = j.get("campaign_id")
        if not raw:
            continue
        jid_to_camp[j.get("journey_id")] = _norm_campaign(raw)
        if raw not in campaign_ids_literal:
            normalized = True
    # count completed deliveries per normalized campaign key
    deliveries_by_camp: dict[int, int] = {}
    for d in deliveries:
        if d.get("status") != "completed":
            continue
        ck = jid_to_camp.get(d.get("journey_id"))
        if ck is not None:
            deliveries_by_camp[ck] = deliveries_by_camp.get(ck, 0) + 1

    rows = []
    now = _dt.datetime.now(_dt.timezone.utc)
    for c in campaigns:
        ck = _norm_campaign(c.get("campaign_id"))
        budget = _num(c.get("budget")) or 0
        spent = spend_to_date(c, now)
        delivered = deliveries_by_camp.get(ck, 0)
        gross = delivered * _ASSUMED_GROSS_PER_DELIVERY
        cost_per_delivery = round(spent / delivered, 0) if delivered and spent else None
        # return on media spent so far; a campaign that has spent nothing has no ROI yet
        roi = round((gross - spent) / spent, 2) if spent else None
        rows.append({
            "campaign_id": c.get("campaign_id"),
            "name": c.get("name"),
            "objective": c.get("objective"),
            "audience": c.get("audience"),
            "model_id": c.get("model_id"),
            "status": c.get("status"),
            "budget": budget,
            "spend_to_date": spent,
            "flight_elapsed": round(spent / budget, 2) if budget else None,
            "currency": c.get("currency") or ("EUR" if market == "de" else "USD"),
            "attributed_deliveries": delivered,
            "cost_per_delivery": cost_per_delivery,
            "assumed_gross": gross,
            "roi": roi,
            "source_ref": c.get("source_ref"),
        })
    rows.sort(key=lambda r: (r["roi"] if r["roi"] is not None else -999), reverse=True)

    total_budget = sum(r["budget"] for r in rows)
    total_spent = sum(r["spend_to_date"] for r in rows)
    total_delivered = sum(r["attributed_deliveries"] for r in rows)
    result = {
        "scope_node": node,
        "market_id": market,
        "kpis": {
            "campaigns": len(rows),
            "total_budget": total_budget,
            "attributed_deliveries": total_delivered,
            "spend_to_date": round(total_spent, 2),
            "blended_cost_per_delivery": round(total_spent / total_delivered, 0) if total_delivered and total_spent else None,
            "blended_roi": (round((total_delivered * _ASSUMED_GROSS_PER_DELIVERY - total_spent) / total_spent, 2)
                            if total_spent else None),
        },
        "campaigns": rows,
        "id_normalization_applied": normalized,
        "assumptions": {
            "gross_per_delivery": _ASSUMED_GROSS_PER_DELIVERY,
            "note": "ROI = (attributed deliveries × assumed flat gross − spend to date) ÷ spend to date; "
                    "spend to date paces each budget evenly over its flight. The lake carries no per-deal gross. "
                    "Campaign→journey ids joined on normalized trailing integer.",
        },
    }
    result["ai_insight"], result["insight_by"] = _insight(result, env_prefix=env_prefix)
    return result


def _insight(result: dict[str, Any], env_prefix: str | None = None) -> tuple[str, str]:
    rows = result["campaigns"]
    best = rows[0] if rows else None
    worst = rows[-1] if rows else None
    templated = (
        f"{result['kpis']['campaigns']} campaigns, "
        f"{result['kpis']['attributed_deliveries']} attributed deliveries. "
        + (f"Best ROI: {best['name']} ({best['roi']}). Consider shifting budget from "
           f"{worst['name']} ({worst['roi']})." if best and worst and best is not worst else "")
    )
    if not _gen.enabled():
        return templated, "fallback"
    facts = (f"kpis={result['kpis']}, campaigns="
             f"{[{k: r[k] for k in ('name', 'budget', 'attributed_deliveries', 'roi')} for r in rows[:6]]}")
    # The same figures always get the same sentence: the written insight (or the guardrail's
    # verdict on it) is kept against a hash of the exact figures it describes. Spend is paced by
    # whole days, so the figures hold all day and only the first load of the day asks the model
    # (it was ~6 s per call, twice per load). New figures → a new hash → a fresh insight.
    import hashlib
    cache_id = "INSIGHT-A5-ROI-" + hashlib.sha256(facts.encode()).hexdigest()[:20]
    try:
        cached = ddb.get_item("DEALER_Recommendations", {"recommendation_id": cache_id}, env_prefix)
    except Exception:  # noqa: BLE001 - a cache read failure only costs the model call
        cached = None
    if cached and cached.get("text"):
        return cached["text"], cached.get("insight_by") or "bedrock"
    try:
        import boto3
        rt = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
        # Phrased as a results summary: "recommend a budget shift" read as dealer allocation /
        # wholesale guidance to the Bedrock topic classifier in ~half of calls (2026-10-28 eval) and the
        # block message came back as the insight. The shift itself stays deterministic
        # (recommend_budget_shift); this line only reports the return on media spend.
        prompt = (
            "Summarize these OEM advertising campaign results for a marketing analyst in two "
            "sentences: the campaign with the best return on media spend and the one with the "
            "worst, with their figures. Use only the provided data; no fabricated, summed or otherwise "
            "derived figures.\n"
            f"{facts}"
        )
        resp = rt.converse(
            modelId=BEDROCK_MODEL,
            system=[{"text": "You are a concise OEM marketing-analytics assistant. "
                             "Recommend budget shifts from data only; no invented numbers."}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 160, "temperature": 0.2},
            **_gen.guardrail_kwargs(),
        )
        # plain text, as the digest insights are: the screen shows "**-0.04**" literally otherwise
        from ..dealer.store_digest import clean_text
        text = clean_text(resp["output"]["message"]["content"][0]["text"])
        if resp.get("stopReason") == "guardrail_intervened":   # never surface the block message as insight
            # the same figures would be blocked the same way: keep the outcome, so the landing page
            # does not ask the model again on every load just to fall back
            text, by = templated, "fallback (guardrail)"
        else:
            by = "bedrock"
        if text:
            try:
                ddb.put_item("DEALER_Recommendations", {
                    "recommendation_id": cache_id, "action": "insight_cache", "agent_id": "A5",
                    "market_id": result.get("market_id"), "scope_node": result.get("scope_node"),
                    "text": text, "insight_by": by, "facts": facts[:4000], "generated_at": _shift_now(),
                    "source_ref": "A5 campaign ROI insight (Bedrock; keyed by the figures it describes)"},
                    env_prefix)
            except Exception as e:  # noqa: BLE001 - not caching only means the next load asks again
                print(f"[A5 insight] cache write skipped ({type(e).__name__})")
        return (text or templated), (by if text else "fallback")
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


# --- recommend_budget_shift (A5, spec §3.5 -> approvals) --------------------

from ..common import autonomy_config as _autonomy   # noqa: E402
from ..platform import generate as _gen


def _shift_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


_SHIFT_ACTION_ID = "a5.shift_budget"
_SHIFT_FRACTION = 0.20   # move up to 20% of the worst performer's budget


def _rank_shift(rows: list[dict[str, Any]], budget_key: str) -> dict[str, Any] | None:
    """The A5 shift math over ROI-ranked rows (shared by the network and store
    views): move _SHIFT_FRACTION of the worst performer's budget to the best, and
    price the moved money at the TARGET's cost-per-delivery.

    Returns None when there is no clear from/to pair; `rows` must be ROI-desc."""
    if len(rows) < 2:
        return None
    best, worst = rows[0], rows[-1]
    if best["campaign_id"] == worst["campaign_id"] or best["roi"] <= worst["roi"]:
        return None
    if best["roi"] <= 0:
        # moving money into a campaign that returns less than it costs only loses it more slowly
        return None
    tgt_cpd = best.get("cost_per_delivery")
    src_cpd = worst.get("cost_per_delivery")
    # the shift is sized to what the winner can absorb: a flight does not double its results on a top-up,
    # so the extra deliveries are capped at half its current count, and the money moved is what buys them
    # at its cost per delivery (never more than 20% of the loser's budget)
    absorb = 0.5 * max(float(best.get("attributed_deliveries") or 0), 1.0)
    shift_amount = round((worst.get(budget_key) or 0) * _SHIFT_FRACTION, 0)
    if tgt_cpd:
        shift_amount = round(min(shift_amount, absorb * tgt_cpd), -2) or shift_amount
    extra = round(shift_amount / tgt_cpd, 1) if tgt_cpd else None
    # what the moved money would still have bought where it was (none when the source sold nothing)
    lost = round(shift_amount / src_cpd, 1) if src_cpd else 0.0
    lift = round(extra * _ASSUMED_GROSS_PER_DELIVERY, 0) if extra is not None else None
    return {"best": best, "worst": worst, "shift_amount": shift_amount,
            "expected_extra_deliveries": extra,
            # gross the moved money returns at the target (> the amount moved: the target's ROI is positive)
            "expected_lift_gross": lift,
            "expected_return_ratio": round(lift / shift_amount, 2) if lift is not None and shift_amount else None,
            # net of the deliveries the source campaign would still have bought with it
            "expected_net_gain": (round((extra - lost) * _ASSUMED_GROSS_PER_DELIVERY, 0)
                                  if extra is not None else None)}


def recommend_store_budget_shift(rows: list[dict[str, Any]], market: str, dealer_scope: list[str],
                                 env_prefix: str | None = None) -> dict[str, Any]:
    """Store-scoped A5 budget-shift recommendation (§5.7 dealer view).

    Same ranking and lift math as the network recommendation, run over THIS
    store's campaign rows (store share of spend, store deliveries) so the GM sees
    the shift that would help this store. Read-only and advisory: a store cannot
    move OEM campaign budget — it takes the recommendation to its District
    Manager — so nothing is written and no approval is raised here."""
    ranked = [r for r in rows if r.get("roi") is not None]
    level = _autonomy.active_level(_SHIFT_ACTION_ID, market, env_prefix)
    source_ref = ("A5.recommend_budget_shift scoped to store (store-share ROI ranking; "
                  "assumed gross flagged in store_campaigns)")
    base = {"market": market, "scope": "store", "dealer_scope": dealer_scope,
            "autonomy_level": level, "written": False, "source_ref": source_ref}
    if len(ranked) < 2:
        return {**base, "recommended": False,
                "note": "need at least two campaigns with computable ROI at this store to compare"}
    s = _rank_shift(ranked, "allocated_spend")
    if s is None:
        return {**base, "recommended": False,
                "note": "no campaign at this store returns more than it costs, or no clear ROI gap to act on"}
    best, worst = s["best"], s["worst"]
    rec = {"recommendation_id": f"REC-STORESHIFT-{worst['campaign_id']}-to-{best['campaign_id']}",
           "action": "budget_shift", "agent_id": "A5",
           "from_campaign": worst["campaign_id"], "to_campaign": best["campaign_id"],
           "shift_amount": s["shift_amount"], "currency": worst.get("currency", "USD"),
           "from_roi": worst["roi"], "to_roi": best["roi"],
           "expected_extra_deliveries": s["expected_extra_deliveries"],
           "expected_lift_gross": s["expected_lift_gross"],
           "expected_net_gain": s["expected_net_gain"],
           "autonomy_level": level, "source_ref": source_ref}
    return {**base, "recommended": True,
            "shift": {"from": {"campaign_id": worst["campaign_id"], "name": worst.get("name"), "roi": worst["roi"]},
                      "to": {"campaign_id": best["campaign_id"], "name": best.get("name"), "roi": best["roi"]},
                      "amount": s["shift_amount"], "currency": worst.get("currency", "USD"),
                      "expected_extra_deliveries": s["expected_extra_deliveries"],
                      "expected_lift_gross": s["expected_lift_gross"],
                      "expected_return_ratio": s["expected_return_ratio"],
                      "expected_net_gain": s["expected_net_gain"]},
            "recommendation": rec,
            "recommendation_id": rec["recommendation_id"],
            "note": "Store-scoped view of the Campaign Agent's shift. A store cannot move OEM campaign budget — "
                    "take this to your District Manager for co-op / C&I planning. Nothing is written "
                    "and no approval is raised from this view."}


def recommend_budget_shift(scope: dict[str, Any], market_id: str | None = None,
                           apply: bool = False, actor: str = "A5",
                           env_prefix: str | None = None,
                           roi: dict[str, Any] | None = None) -> dict[str, Any]:
    """Recommend moving budget from the lowest-ROI campaign to the highest, with
    expected lift (spec §3.5, acceptance #6).

    Computes the shift from the ROI ranking (build_campaign_roi), estimates the
    extra deliveries the moved dollars buy at the TARGET's cost-per-delivery, and
    — autonomy-gated on a5.shift_budget — writes a DEALER_Recommendations card
    (never auto-executes a budget change; a human approves it). Every figure is
    derived, none invented; if there's no clear from/to pair it says so."""
    # a caller that already built the ROI read model passes it (no second build, no second insight call)
    roi = roi or build_campaign_roi(scope, market_id, env_prefix)
    rows = [r for r in roi.get("campaigns", []) if r.get("roi") is not None]
    market = roi.get("market") or market_id or scope.get("market_id") or "us"
    if len(rows) < 2:
        return {"market": market, "recommended": False,
                "note": "need at least two campaigns with computable ROI to shift budget",
                "kpis": roi.get("kpis")}

    s = _rank_shift(rows, "budget")   # build_campaign_roi sorts ROI desc
    if s is None:
        return {"market": market, "recommended": False,
                "note": ("no campaign returns more than it costs — cut the weakest spend rather than move it"
                         if rows and rows[0]["roi"] <= 0 else "no clear ROI gap to act on"),
                "kpis": roi.get("kpis")}
    best, worst = s["best"], s["worst"]
    shift_amount = s["shift_amount"]
    expected_extra_deliveries = s["expected_extra_deliveries"]
    expected_lift_gross = s["expected_lift_gross"]

    level = _autonomy.active_level(_SHIFT_ACTION_ID, market, env_prefix)
    rec_id = f"REC-BUDGETSHIFT-{worst['campaign_id']}-to-{best['campaign_id']}"
    rec = {
        "recommendation_id": rec_id,
        "action": "budget_shift", "agent_id": "A5",
        "from_campaign": worst["campaign_id"], "to_campaign": best["campaign_id"],
        "shift_amount": shift_amount, "currency": worst.get("currency", "USD"),
        "from_roi": worst["roi"], "to_roi": best["roi"],
        "expected_extra_deliveries": expected_extra_deliveries,
        "expected_lift_gross": expected_lift_gross,
        "expected_net_gain": s["expected_net_gain"],
        "autonomy_level": level, "shown": 0, "accepted": 0, "converted": 0,
        "shown_at": _shift_now(),
        "source_ref": "A5.recommend_budget_shift (ROI ranking; assumed gross flagged)",
    }
    written = False
    if apply and level in _autonomy.WRITE_LEVELS:
        ddb.put_item("DEALER_Recommendations", rec, env_prefix)
        written = True

    return {
        "market": market, "recommended": True, "autonomy_level": level, "written": written,
        "shift": {"from": {"campaign_id": worst["campaign_id"], "name": worst.get("name"), "roi": worst["roi"]},
                  "to": {"campaign_id": best["campaign_id"], "name": best.get("name"), "roi": best["roi"]},
                  "amount": shift_amount, "currency": worst.get("currency", "USD"),
                  "expected_extra_deliveries": expected_extra_deliveries,
                  "expected_lift_gross": expected_lift_gross,
                  "expected_return_ratio": s["expected_return_ratio"],
                  "expected_net_gain": s["expected_net_gain"]},
        "recommendation_id": rec_id,
        "note": "The Campaign Agent never auto-executes a budget change; this is a recommendation for "
                "human approval in the Approvals inbox. ROI uses an assumed flat gross per delivery (see the attribution note).",
        "source_ref": rec["source_ref"],
    }


# --- run_nightly_attribution (A5 nightly job, §4.10.2) ----------------------
# Lives with the rest of A5 so the Campaign Agent's runtime bundle carries no A6
# code (ADR 0009); oem.district_cycle re-exports it for existing imports.

def run_nightly_attribution(market: str = "us", apply: bool = False,
                            lift_threshold: float = 5000.0, limit: int | None = None,
                            env_prefix: str | None = None) -> dict[str, Any]:
    """A5 nightly: recompute ROI and, when a budget shift projects lift >=
    threshold, draft a budget-shift approval request (act_with_approval; never
    changes spend)."""
    scope = {"market_id": market, "is_oem": True, "role": "oem_finance", "scope_node_id": "OEM"}
    roi = build_campaign_roi(scope, market_id=market, env_prefix=env_prefix)
    shift = recommend_budget_shift(scope, market_id=market, apply=False, env_prefix=env_prefix, roi=roi)
    lift = ((shift.get("shift") or {}).get("expected_lift_gross")) or 0
    level = _autonomy.active_level("a5.recommend_budget_shift", market, env_prefix, default="act_with_approval")
    drafted = None
    if lift and lift >= lift_threshold and apply:
        # re-run with apply to persist the recommendation/approval draft
        shift = recommend_budget_shift(scope, market_id=market, apply=True, env_prefix=env_prefix)
        drafted = shift.get("recommendation_id")
    return {"job": "nightly-attribution", "market": market, "publish_level": level,
            "campaigns_scored": len(roi.get("campaigns", [])),
            "projected_lift_gross": lift, "lift_threshold": lift_threshold,
            "approval_drafted": drafted, "applied": apply, "ran_at": _shift_now(),
            "source_ref": "A5.run_nightly_attribution (draft only; spend never auto-changed)"}
