"""Sales Agent (A3) tool adapters for its AgentCore runtime.

The reasoning turn (`kind: agent`) goes to `app.services.agents.a3_agent.run`
directly; this module holds the deterministic tool the Journey Agent calls.
"""
from __future__ import annotations

from typing import Any

from app.services.journeys import assist


def build_assist(claims: dict[str, Any], env_prefix: str | None, *,
                 journey: dict[str, Any] | None = None, journey_id: str | None = None) -> dict[str, Any]:
    """The advisor assist model for the journey (read-only)."""
    return assist.build_assist((journey or {}).get("journey_id") or journey_id, env_prefix)
