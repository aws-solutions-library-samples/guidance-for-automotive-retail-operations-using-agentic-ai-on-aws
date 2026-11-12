"""A5 — Campaign ROI Optimizer, analytics half in the main repo (spec §3.5, §5.7).

The Quick/QuickSight dashboards and Q topics are the Quick App; these are the
deterministic read models the Marketing Studio renders today and the inputs the
A5 recommendation step reasons over:

  get_campaign_spend(scope, market)        -> budget per campaign, allocated to dealers
  get_journey_funnel(scope, market)        -> journeys reaching each stage, by source
  attribute_conversions(scope, market)     -> campaign-tagged journeys -> deliveries
  campaign_overview(scope, market)         -> one payload for /marketing/campaigns
  submit_budget_shift(scope, actor, ...)   -> recommendation card + DEALER_Approvals
                                              request (act_with_approval; spend is
                                              never changed by the agent)
  ask_analytics(question, scope)           -> guardrail-checked answer with citations

Every figure is computed from seeded rows and carries a source_ref. Campaign ids
are joined on the normalized trailing integer (see oem.campaigns).
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from ..common import authz, ddb
from ..oem import campaigns as oem_campaigns

STAGES = ["DISCOVER", "CONFIGURE", "QUALIFY", "FINANCE", "TRADE_IN", "AGREE", "DELIVER", "OWN"]

# Roles that may submit a budget-shift approval request. One set, read by the
# submit route and by the Campaign Agent runtime (agent registry, ADR 0009).
SUBMIT_ROLES = frozenset({"oem_marketing", "oem_cx", "oem_finance", "oem_dm", "oem_regional"})
_norm = oem_campaigns._norm_campaign


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _scoped(scope: dict[str, Any], market: str, env_prefix: str | None):
    node = scope.get("scope_node_id") or "OEM"
    districts = authz.district_ids_under(node, env_prefix)
    camps = ddb.scan_filter("OEM_Campaigns", "market_id", market, env_prefix)
    journeys = ddb.scan_filter("DEALER_Journeys", "market_id", market, env_prefix)
    if districts and node != "OEM":
        camps = [c for c in camps if c.get("district_id") in districts]
        journeys = [j for j in journeys if j.get("district_id") in districts or not j.get("district_id")]
    return camps, journeys


def _delivered_journeys(env_prefix: str | None) -> set[str]:
    return {d.get("journey_id") for d in ddb.scan_all("DEALER_Deliveries", env_prefix)
            if d.get("status") == "completed"}


def attribute_conversions(scope: dict[str, Any], market: str = "us",
                          env_prefix: str | None = None) -> dict[str, Any]:
    """Campaign-tagged journeys and their deliveries (attribution model: the
    campaign tag carried on the journey — first-touch; stated in the output)."""
    camps, journeys = _scoped(scope, market, env_prefix)
    delivered = _delivered_journeys(env_prefix)
    by_key: dict[int, dict[str, Any]] = {}
    for j in journeys:
        k = _norm(j.get("campaign_id"))
        if k is None:
            continue
        b = by_key.setdefault(k, {"journeys": 0, "conversions": 0, "by_dealer": {}})
        b["journeys"] += 1
        conv = j.get("journey_id") in delivered or j.get("stage") == "OWN"
        b["conversions"] += 1 if conv else 0
        d = b["by_dealer"].setdefault(j.get("dealer_id") or "?", {"journeys": 0, "conversions": 0})
        d["journeys"] += 1
        d["conversions"] += 1 if conv else 0
    rows = []
    for c in camps:
        b = by_key.get(_norm(c.get("campaign_id")), {"journeys": 0, "conversions": 0, "by_dealer": {}})
        rows.append({
            "campaign_id": c["campaign_id"], "name": c.get("name"),
            "journeys": b["journeys"], "conversions": b["conversions"],
            "conversion_rate": round(b["conversions"] / b["journeys"], 4) if b["journeys"] else None,
            "by_dealer": [{"dealer_id": k, **v} for k, v in sorted(b["by_dealer"].items())],
            "source_ref": f"DEALER_Journeys.campaign_id ~ {c['campaign_id']} + DEALER_Deliveries",
        })
    return {"market_id": market, "model": "campaign tag on journey (first-touch)", "campaigns": rows}


def get_campaign_spend(scope: dict[str, Any], market: str = "us",
                       env_prefix: str | None = None) -> dict[str, Any]:
    """Budget per campaign, allocated to dealers pro rata to attributed journeys
    (sandbox stand-in for ad-platform spend feeds; allocation method stated)."""
    camps, _ = _scoped(scope, market, env_prefix)
    attr = {r["campaign_id"]: r for r in attribute_conversions(scope, market, env_prefix)["campaigns"]}
    out, by_dealer = [], {}
    for c in camps:
        budget = float(c.get("budget") or 0)
        a = attr.get(c["campaign_id"], {})
        total_j = a.get("journeys") or 0
        dealers = []
        for d in a.get("by_dealer", []):
            share = budget * d["journeys"] / total_j if total_j else 0
            dealers.append({"dealer_id": d["dealer_id"], "allocated_spend": round(share, 2),
                            "journeys": d["journeys"], "conversions": d["conversions"]})
            agg = by_dealer.setdefault(d["dealer_id"], {"allocated_spend": 0.0, "journeys": 0, "conversions": 0})
            agg["allocated_spend"] += share
            agg["journeys"] += d["journeys"]
            agg["conversions"] += d["conversions"]
        out.append({"campaign_id": c["campaign_id"], "name": c.get("name"), "budget": budget,
                    "audience": c.get("audience"), "objective": c.get("objective"), "status": c.get("status"),
                    "start_at": c.get("start_at"), "end_at": c.get("end_at"), "dealers": dealers,
                    "source_ref": c.get("source_ref")})
    dealers = [{"dealer_id": k, "allocated_spend": round(v["allocated_spend"], 2), "journeys": v["journeys"],
                "conversions": v["conversions"],
                "cost_per_conversion": round(v["allocated_spend"] / v["conversions"], 2) if v["conversions"] else None}
               for k, v in sorted(by_dealer.items())]
    return {"market_id": market, "allocation": "budget allocated to dealers pro rata to attributed journeys",
            "campaigns": out, "by_dealer": dealers,
            "total_budget": round(sum(c["budget"] for c in out), 2)}


def get_journey_funnel(scope: dict[str, Any], market: str = "us",
                       env_prefix: str | None = None) -> dict[str, Any]:
    """Journeys reaching each stage, by acquisition source, with drop-off rates."""
    _, journeys = _scoped(scope, market, env_prefix)
    by_src: dict[str, list[int]] = {}
    for j in journeys:
        st = j.get("stage")
        if st not in STAGES:
            continue
        idx = STAGES.index(st)
        counts = by_src.setdefault(j.get("source") or "unknown", [0] * len(STAGES))
        for i in range(idx + 1):
            counts[i] += 1
    sources = []
    for src, counts in sorted(by_src.items(), key=lambda kv: -kv[1][0]):
        steps = []
        for i, st in enumerate(STAGES):
            prev = counts[i - 1] if i else counts[0]
            steps.append({"stage": st, "reached": counts[i],
                          "drop_off_rate": round(1 - counts[i] / prev, 4) if i and prev else 0.0})
        worst = max(steps[1:], key=lambda s: s["drop_off_rate"]) if len(steps) > 1 else None
        sources.append({"source": src, "entered": counts[0], "owned": counts[-1],
                        "conversion_rate": round(counts[-1] / counts[0], 4) if counts[0] else None,
                        "steps": steps, "largest_drop": worst})
    return {"market_id": market, "stages": STAGES, "sources": sources,
            "method": "a journey at stage N has passed every earlier stage",
            "source_ref": "DEALER_Journeys.stage x source"}


def campaign_overview(scope: dict[str, Any], market: str = "us",
                      env_prefix: str | None = None) -> dict[str, Any]:
    with ddb.read_memo():   # one read model: each table is scanned once, not once per helper
        return _campaign_overview(scope, market, env_prefix)


def _campaign_overview(scope: dict[str, Any], market: str, env_prefix: str | None) -> dict[str, Any]:
    roi = oem_campaigns.build_campaign_roi(scope, market, env_prefix)
    shift = oem_campaigns.recommend_budget_shift(scope, market, apply=False, env_prefix=env_prefix, roi=roi)
    funnel = get_journey_funnel(scope, market, env_prefix)
    spend = get_campaign_spend(scope, market, env_prefix)
    attr = attribute_conversions(scope, market, env_prefix)
    by_channel: dict[str, dict[str, int]] = {}
    tagged = {j["journey_id"] for j in _scoped(scope, market, env_prefix)[1] if j.get("campaign_id")}
    delivered = _delivered_journeys(env_prefix)
    for t in ddb.scan_all("DEALER_Touchpoints", env_prefix):
        if t.get("journey_id") not in tagged:
            continue
        b = by_channel.setdefault(t.get("channel") or "other", {"touchpoints": 0, "converted_journeys": 0})
        b["touchpoints"] += 1
    for ch, b in by_channel.items():
        js = {t.get("journey_id") for t in ddb.scan_filter("DEALER_Touchpoints", "channel", ch, env_prefix)
              if t.get("journey_id") in tagged}
        b["converted_journeys"] = len(js & delivered)
    insight_sources = [c.get("source_ref") for c in roi["campaigns"][:3]] + [funnel["source_ref"]]
    largest = max((s["largest_drop"] | {"source": s["source"]} for s in funnel["sources"] if s.get("largest_drop")),
                  key=lambda d: d["drop_off_rate"], default=None)
    return {
        "market_id": market, "roi": roi, "budget_shift": shift, "funnel": funnel, "spend": spend,
        "attribution": attr,
        "by_channel": [{"channel": k, **v} for k, v in sorted(by_channel.items())],
        "insight": {
            "text": roi.get("ai_insight"), "by": roi.get("insight_by"),
            "largest_drop": largest, "sources": [s for s in insight_sources if s],
        },
    }


def submit_budget_shift(scope: dict[str, Any], actor: str, market: str = "us",
                        note: str | None = None, env_prefix: str | None = None) -> dict[str, Any]:
    """Turn the current budget-shift recommendation into an approval request.

    Writes the recommendation card (autonomy-gated, a5.shift_budget) and a
    DEALER_Approvals row for oem_finance (§4.8). Spend is never changed here —
    the approval decision is the only path to a budget change (acceptance #36)."""
    from ..events import bus
    rec = oem_campaigns.recommend_budget_shift(scope, market, apply=True, actor=actor, env_prefix=env_prefix)
    if not rec.get("recommended"):
        return {"submitted": False, "reason": rec.get("note"), "recommendation": rec}
    approval_id = f"APR-{rec['recommendation_id']}"
    existing = ddb.get_item("DEALER_Approvals", {"approval_id": approval_id}, env_prefix)
    if existing and existing.get("status") == "pending":
        return {"submitted": False, "reason": "an approval for this shift is already pending",
                "approval_id": approval_id, "recommendation": rec}
    row = {
        "approval_id": approval_id, "type": "budget_shift", "action": "budget_shift",
        "requested_by": actor, "requested_at": _now(), "approver_role": "oem_finance",
        "scope": {"node": scope.get("scope_node_id") or "OEM", "market_id": market},
        "payload": rec["shift"], "recommendation_id": rec["recommendation_id"],
        "autonomy_level": rec.get("autonomy_level"), "status": "pending", "note": note,
        "source_ref": rec.get("source_ref"),
    }
    ddb.put_item("DEALER_Approvals", row, env_prefix)
    ev = bus.emit("approval.requested", {"approval_id": approval_id, "approval_type": "budget_shift",
                                         "requested_by": actor, "approver_role": "oem_finance",
                                         "recommendation_id": rec["recommendation_id"]},
                  actor=actor, resource_url=f"/r/approval/{approval_id}", env_prefix=env_prefix)
    return {"submitted": True, "approval_id": approval_id, "approval": row, "recommendation": rec,
            "bus_event": ev, "note": "Spend is unchanged until oem_finance approves."}


_ROLE_ASK = ("oem_marketing", "oem_cx", "oem_finance", "oem_dm", "oem_regional", "oem_ci_planning",
             "oem_sales_ops", "dealer_manager")


def ask_analytics(question: str, scope: dict[str, Any], market: str = "us",
                  env_prefix: str | None = None) -> dict[str, Any]:
    """Answer a natural-language analytics question through grounded Bedrock
    generation over computed, scoped read models. Guardrail-checked; every
    figure cites the row set it came from and the deterministic analytics text
    is used only as an explicit fallback."""
    from ..oem import guardrails
    from ..platform import generate
    gr = guardrails.check_guardrails(question, {"market_id": market, "user_id": scope.get("user_id"),
                                                "district_id": scope.get("scope_node_id"),
                                                "agent_id": "A5", "tool": "ask_quick"})
    if gr.get("outcome") == "block":
        return {"answer": gr.get("safe_alternative") or "That request is outside policy.",
                "guardrail": gr.get("violations"), "citations": [],
                "engine": "deterministic_guardrail", "model": None}
    q = question.lower()
    ov = campaign_overview(scope, market, env_prefix)
    roi = [c for c in ov["roi"]["campaigns"] if c.get("roi") is not None]
    cites: list[dict[str, str]] = []
    facts: list[str] = [f"Market={market}; campaigns with computable ROI={len(roi)}."]
    for campaign in roi[:10]:
        facts.append(
            f"Campaign {campaign.get('name')} ({campaign.get('campaign_id')}): "
            f"ROI={campaign.get('roi')}; attributed deliveries={campaign.get('attributed_deliveries')}; "
            f"spend={campaign.get('spend') or campaign.get('budget')}."
        )
    for source in ov["funnel"].get("sources", [])[:10]:
        facts.append(
            f"Funnel source {source.get('source')}: entered={source.get('entered')}; "
            f"conversion rate={source.get('conversion_rate')}; largest drop stage={source.get('largest_drop_stage')}; "
            f"drop-off rate={source.get('largest_drop_rate')}."
        )
    shift = ov.get("budget_shift") or {}
    if shift.get("recommended") and shift.get("shift"):
        proposal = shift["shift"]
        facts.append(
            f"Budget-shift proposal: move {proposal.get('amount')} {proposal.get('currency')} "
            f"from {proposal.get('from', {}).get('name')} to {proposal.get('to', {}).get('name')}; "
            f"expected extra deliveries={proposal.get('expected_extra_deliveries')}; finance approval required."
        )
    if any(w in q for w in ("funnel", "drop", "source", "stage")):
        d = ov["insight"]["largest_drop"]
        best = max(ov["funnel"]["sources"], key=lambda s: s.get("conversion_rate") or 0, default=None)
        parts = []
        if d:
            parts.append(f"The largest drop-off is {d['source']} journeys leaving {d['stage']} "
                         f"({d['drop_off_rate']:.0%} lost at that step).")
        if best:
            parts.append(f"Best-converting source: {best['source']} ({(best['conversion_rate'] or 0):.0%} reach OWN "
                         f"of {best['entered']} entered).")
        cites.append({"label": "journey funnel", "ref": ov["funnel"]["source_ref"]})
        answer = " ".join(parts) or "No funnel data in your scope."
    elif any(w in q for w in ("shift", "move", "budget", "reallocat")):
        s = ov["budget_shift"]
        if s.get("recommended"):
            sh = s["shift"]
            answer = (f"Move {sh['amount']:.0f} ({sh['currency']}) from {sh['from']['name']} (ROI {sh['from']['roi']}) "
                      f"to {sh['to']['name']} (ROI {sh['to']['roi']}); expected +{sh['expected_extra_deliveries']} "
                      "deliveries. This needs OEM finance approval — submit it from the recommendation card.")
            cites.append({"label": "Campaign Agent budget shift", "ref": s.get("source_ref", "")})
        else:
            answer = f"No budget shift recommended: {s.get('note')}."
    elif any(w in q for w in ("roi", "best", "worst", "perform", "campaign")) and roi:
        top, low = roi[0], roi[-1]
        answer = (f"{len(roi)} campaigns with computable ROI. Best: {top['name']} (ROI {top['roi']}, "
                  f"{top['attributed_deliveries']} deliveries). Weakest: {low['name']} (ROI {low['roi']}). "
                  f"ROI uses an assumed gross per delivery ({ov['roi']['assumptions']['gross_per_delivery']}).")
        cites += [{"label": c["campaign_id"], "ref": c.get("source_ref") or ""} for c in (top, low)]
    elif any(w in q for w in ("spend", "dealer", "cost")):
        d = sorted([x for x in ov["spend"]["by_dealer"] if x.get("cost_per_conversion")],
                   key=lambda x: x["cost_per_conversion"])
        answer = ("Lowest cost per conversion: " + ", ".join(f"{x['dealer_id']} ({x['cost_per_conversion']:.0f})" for x in d[:3])
                  + ". " + ov["spend"]["allocation"] + ".") if d else "No attributed conversions yet."
        cites.append({"label": "campaign spend allocation", "ref": "OEM_Campaigns.budget x DEALER_Journeys"})
    else:
        answer = ("I can answer questions about campaign ROI, budget shifts, spend by dealer, and funnel drop-off "
                  "by source for your scope.")
    generated = generate.ground(
        question, facts, [], persona="an OEM marketing analytics assistant",
        fallback=answer,
    )
    model_guardrail = generated.get("guardrail") or {}
    guardrail_badge = None
    if model_guardrail.get("intervened"):
        guardrail_badge = model_guardrail.get("matched") or ["BedrockGuardrail"]
    return {"answer": generated["text"], "citations": cites,
            "guardrail": guardrail_badge, "model_guardrail": model_guardrail,
            "engine": generated["engine"], "model": generated.get("model"),
            "via": f"{generated['engine']} over scoped marketing read models"}
