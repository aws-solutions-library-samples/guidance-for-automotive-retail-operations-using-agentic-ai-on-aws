"""Ranking interface — local rules ranker | Amazon Personalize (spec §3.1 A1).

The spec's A1 `get_personalized_ranking` uses Amazon Personalize (Ranking recipe)
with a rules fallback. This is that interface: `rank(user_id, candidates,
context)` returns candidates ordered by score. The `local` impl is the
transparent rules ranker (works today, every score explainable); the `aws` impl
is the swap point for a trained Personalize campaign.
"""
from __future__ import annotations

import os
from typing import Any

from .mode import integration_mode


def _mode() -> str:
    return integration_mode()


class Ranker:
    """rank(user_id, candidates, context) -> [{id, score, reason}], best first."""

    def rank(self, user_id: str, candidates: list[dict[str, Any]],
             context: dict[str, Any] | None = None) -> dict[str, Any]:
        raise NotImplementedError


class LocalRulesRanker(Ranker):
    """Transparent additive scoring — the reference ranker A1 uses when no
    Personalize model is live. Every score decomposes into named reasons."""

    def rank(self, user_id: str, candidates: list[dict[str, Any]],
             context: dict[str, Any] | None = None) -> dict[str, Any]:
        ctx = context or {}
        budget_max = ctx.get("budget_msrp_max")
        powertrain = (ctx.get("powertrain_openness") or "").lower()
        ranked = []
        for c in candidates:
            score = 0.5
            reasons = []
            price = c.get("msrp") or c.get("price")
            if budget_max and price is not None:
                if float(price) <= float(budget_max):
                    score += 0.3; reasons.append("within budget")
                else:
                    score -= 0.2; reasons.append("over budget")
            pt = (c.get("powertrain") or c.get("ev_ice_flag") or "").lower()
            if powertrain and powertrain != "undecided" and powertrain in pt:
                score += 0.2; reasons.append(f"matches powertrain preference ({powertrain})")
            ranked.append({**c, "score": round(min(max(score, 0), 1), 3), "reasons": reasons})
        ranked.sort(key=lambda x: x["score"], reverse=True)
        return {"ranked": ranked, "model": "local_rules",
                "source_ref": "platform.LocalRulesRanker (transparent additive; Personalize swap-in)"}


class PersonalizeRanker(Ranker):
    """Amazon Personalize Ranking recipe. Requires a trained campaign ARN in
    RETAIL_OPS_PERSONALIZE_CAMPAIGN_ARN. Falls back to the rules ranker if unset or on
    error, so selecting aws mode is safe before a model is trained."""

    def rank(self, user_id: str, candidates: list[dict[str, Any]],
             context: dict[str, Any] | None = None) -> dict[str, Any]:
        arn = os.environ.get("RETAIL_OPS_PERSONALIZE_CAMPAIGN_ARN")
        if not arn:
            out = LocalRulesRanker().rank(user_id, candidates, context)
            out["model"] = "local_rules (Personalize campaign not configured)"
            return out
        try:
            import boto3
            rt = boto3.client("personalize-runtime")
            item_ids = [str(c.get("id") or c.get("vin") or c.get("model_id")) for c in candidates]
            resp = rt.get_personalized_ranking(campaignArn=arn, userId=user_id, inputList=item_ids)
            order = {r["itemId"]: i for i, r in enumerate(resp.get("personalizedRanking", []))}
            ranked = sorted(candidates, key=lambda c: order.get(
                str(c.get("id") or c.get("vin") or c.get("model_id")), 999))
            return {"ranked": ranked, "model": "personalize",
                    "source_ref": f"Amazon Personalize campaign {arn.split('/')[-1]}"}
        except Exception as e:
            out = LocalRulesRanker().rank(user_id, candidates, context)
            out["model"] = f"local_rules (Personalize error: {type(e).__name__})"
            return out


def get_ranker() -> Ranker:
    return PersonalizeRanker() if _mode() == "aws" else LocalRulesRanker()
