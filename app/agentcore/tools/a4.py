"""Trade-In Agent (A4) tool adapters for its AgentCore runtime."""
from __future__ import annotations

from typing import Any

from app.services.agents import equity


def build_equity(claims: dict[str, Any], env_prefix: str | None, *,
                 journey: dict[str, Any] | None = None, journey_id: str | None = None) -> dict[str, Any]:
    """The trade equity snapshot for the journey (read-only)."""
    return equity.build_equity((journey or {}).get("journey_id") or journey_id, env_prefix)
