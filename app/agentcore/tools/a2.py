"""Journey Agent (A2) tool adapters for its AgentCore runtime."""
from __future__ import annotations

from typing import Any

from app.services.journeys import package


def build_sales_package(claims: dict[str, Any], env_prefix: str | None, *,
                        journey: dict[str, Any] | None = None, journey_id: str | None = None) -> dict[str, Any]:
    """Build and persist the Sales Package for the journey (upserts SP-{journey_id}),
    calling the Discovery, Sales and Trade-In Agents on their own runtimes."""
    if journey is None:
        raise ValueError(f"journey {journey_id} not loaded")
    return package.build_sales_package(journey, actor=claims["user_id"], env_prefix=env_prefix,
                                       claims=claims, remote=True)
