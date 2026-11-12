"""Campaign Agent (A5) tool adapters for its AgentCore runtime.

The caller sends OEM claims with a non-empty `scope_node_id` (the service side
applies today's `scope_node_id or "OEM"` default before sending), so a
district-scoped user keeps their district on both paths.
"""
from __future__ import annotations

from typing import Any

from app.services.marketing import analytics
from app.services.oem import campaigns


def recommend_budget_shift(claims: dict[str, Any], env_prefix: str | None, *,
                           journey: dict[str, Any] | None = None, market: str | None = None,
                           apply: bool = False) -> dict[str, Any]:
    """The budget-shift recommendation; writes the card only with `apply` at a write level."""
    return campaigns.recommend_budget_shift(claims, market_id=market, apply=apply, actor=claims["user_id"],
                                            env_prefix=env_prefix)


def submit_budget_shift(claims: dict[str, Any], env_prefix: str | None, *,
                        journey: dict[str, Any] | None = None, market: str | None = None,
                        note: str | None = None) -> dict[str, Any]:
    """Turn the current recommendation into an approval request (idempotent by approval id)."""
    return analytics.submit_budget_shift(claims, claims["user_id"], market or claims.get("market_id") or "us",
                                         note=note, env_prefix=env_prefix)
