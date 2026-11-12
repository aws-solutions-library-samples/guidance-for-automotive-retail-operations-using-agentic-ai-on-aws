"""L3 — the `assist` read model for the Dealer Advisor screen (§5.3).

Assembles the Sales Package a Dealer Console Advisor sees when they open a
customer's live journey: Customer 360 (left column), AI recommendations
(center), and live-assistance context (right) — from the operational DynamoDB
tables loaded at L1. This is a pure READ model: it serves pre-computed data the
agents already wrote (rule R5 — no agent in the request path). Later, the live
objection panel calls L5; this read model backs the initial page render.

Access patterns:
  journey        get_item DEALER_Journeys       by journey_id
  customer       get_item DEALER_Customers      by customer_id (from journey)
  recommendations scan     DEALER_Recommendations filter journey_id
  sales package  scan     DEALER_SalesPackages    filter journey_id (latest)
  objections     scan     DEALER_ObjectionStrategies filter market_id

Scans on the small tables are fine for the prototype; the production refinement
is GSIs on journey_id / market_id (noted for L4).
"""
from __future__ import annotations

from typing import Any

from ..common import ddb

# Purchase-journey stages (spec §2.1 / architecture §4), in order.
JOURNEY_STAGES = [
    "DISCOVER", "CONFIGURE", "QUALIFY", "FINANCE",
    "TRADE_IN", "AGREE", "DELIVER", "OWN",
]


class NotFound(Exception):
    """Raised when the journey id does not resolve."""


def build_assist(journey_id: str, env_prefix: str | None = None) -> dict[str, Any]:
    """Assemble the assist payload for one journey. Raises NotFound if unknown."""
    journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id}, env_prefix)
    if journey is None:
        raise NotFound(f"journey {journey_id} not found")

    customer_id = journey.get("customer_id")
    market_id = journey.get("market_id", "us")

    customer = (
        ddb.get_item("DEALER_Customers", {"customer_id": customer_id}, env_prefix)
        if customer_id else None
    )
    recs = ddb.scan_filter("DEALER_Recommendations", "journey_id", journey_id, env_prefix)
    packages = ddb.scan_filter("DEALER_SalesPackages", "journey_id", journey_id, env_prefix)
    objections = ddb.scan_filter("DEALER_ObjectionStrategies", "market_id", market_id, env_prefix)

    latest_package = _latest(packages, "generated_at")

    return {
        "journey_id": journey_id,
        "stage": journey.get("stage"),
        "stepper": _stepper(journey.get("stage")),
        "customer_360": _customer_360(journey, customer),
        "recommendations": _recommendations(recs),
        "next_best_action": (latest_package or {}).get("next_best_action"),
        "summary": (latest_package or {}).get("summary") or journey.get("summary"),
        "objection_strategies": _objections(objections),
        "meta": {
            "market_id": market_id,
            "source": journey.get("source"),
            "golden_story": journey.get("golden_story"),
            "nurture_active": journey.get("nurture_active"),
            "stalled": journey.get("stalled"),
            "idle_days": journey.get("idle_days"),
        },
    }


# --- column assemblers ------------------------------------------------------

def _stepper(current: str | None) -> list[dict[str, Any]]:
    """The 8-stage stepper with the current stage flagged (left column)."""
    out = []
    reached = True
    for st in JOURNEY_STAGES:
        is_current = st == current
        out.append({"stage": st, "current": is_current, "reached": reached})
        if is_current:
            reached = False
    return out


def _customer_360(journey: dict[str, Any], customer: dict[str, Any] | None) -> dict[str, Any]:
    """Left column: who the customer is, budget, vehicle, context flags."""
    c = customer or {}
    return {
        "customer_id": journey.get("customer_id"),
        "name": _full_name(c),
        "budget": c.get("budget"),
        "commute_per_day": c.get("commute_per_day"),
        "lifestyle_profile": c.get("lifestyle_profile"),
        "current_vehicle": c.get("current_vehicle"),
        "contacts": {"email": c.get("email"), "phone": c.get("phone")},
        "location": (c.get("address") or {}),
        "data_class": c.get("data_class"),
        "context_present": journey.get("context"),
        "missing_context": journey.get("missing_context") or [],
    }


def _recommendations(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Center column: agent recommendations for this journey, newest first."""
    ranked = sorted(recs, key=lambda r: r.get("shown_at") or "", reverse=True)
    return [
        {
            "recommendation_id": r.get("recommendation_id"),
            "action": r.get("action"),
            "agent_id": r.get("agent_id"),
            "shown": r.get("shown"),
            "accepted": r.get("accepted"),
            "converted": r.get("converted"),
            "shown_at": r.get("shown_at"),
            "source_ref": r.get("source_ref"),
        }
        for r in ranked
    ]


def _objections(objs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Right column: objection→strategy library for this market."""
    return [
        {
            "objection_type": o.get("objection_type"),
            "strategy_id": o.get("strategy_id"),
            "example_phrasing": o.get("example_phrasing"),
            "source_ref": o.get("source_ref"),
        }
        for o in sorted(objs, key=lambda o: o.get("objection_type") or "")
    ]


# --- helpers ----------------------------------------------------------------

def _full_name(c: dict[str, Any]) -> str | None:
    given, family = c.get("given_name"), c.get("family_name")
    if given or family:
        return " ".join(p for p in (given, family) if p)
    return None


def _latest(rows: list[dict[str, Any]], ts_field: str) -> dict[str, Any] | None:
    if not rows:
        return None
    return max(rows, key=lambda r: r.get(ts_field) or "")
