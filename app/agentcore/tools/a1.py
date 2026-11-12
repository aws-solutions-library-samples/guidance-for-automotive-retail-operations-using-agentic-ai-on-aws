"""Discovery Agent (A1) tool adapters for its AgentCore runtime."""
from __future__ import annotations

from typing import Any

from app.services.agents import discovery


def rank_vehicles(claims: dict[str, Any], env_prefix: str | None, *,
                  journey: dict[str, Any] | None = None, journey_id: str | None = None) -> dict[str, Any]:
    """Ranked vehicle matches for the journey's customer (read-only)."""
    return discovery.rank_vehicles((journey or {}).get("journey_id") or journey_id, env_prefix)
