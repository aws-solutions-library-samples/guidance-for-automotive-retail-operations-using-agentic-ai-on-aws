"""A3 — generate_contextual_suggestions (lifecycle suggestion matrix), spec §3.3.

The advisor floor copilot: given a journey's lifecycle stage and the customer's
recent activity, return stage-appropriate suggestions — each with a `why` that
CITES a concrete recent activity record (acceptance criterion #14). The
per-stage matrix lives here; the ACTIVE PLAT_ConfigSuggestions row supplies the
precompute_stages allow-list and the confidence_threshold (the versioned,
Admin-editable policy layer, read at invocation).

  generate_contextual_suggestions(journey, role) -> {stage, suggestions[], ...}

Grounding: a suggestion is only emitted when the matrix rule for the stage fires
AND there is a recent activity to cite; the `why` names that activity + its
timestamp. No activity to anchor it -> the suggestion is held back, never
fabricated. Confidence below the config threshold is likewise dropped.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from ..common import ddb
from ..journeys.assist import JOURNEY_STAGES

_CONFIG = "PLAT_ConfigSuggestions"

# The stage -> candidate suggestions matrix (spec §3.3). Each candidate names
# the activity signal that justifies it and a base confidence. `signal_types`
# are DEALER_Signals types / JourneyEvent types that make the suggestion
# relevant; `action` is the one-click the UI attaches.
_MATRIX: dict[str, list[dict[str, Any]]] = {
    "DISCOVER": [
        {"text": "Send a personalized shortlist based on what they browsed.",
         "signal_types": ("model_view", "trim_view", "page_view"), "action": "share_shortlist", "confidence": 0.82},
        {"text": "Invite to build & price the model they viewed most.",
         "signal_types": ("model_view", "content_read"), "action": "invite_configure", "confidence": 0.78},
    ],
    "CONFIGURE": [
        {"text": "Offer to lock the saved build with current incentives before they expire.",
         "signal_types": ("configure_save", "price_check"), "action": "lock_build", "confidence": 0.85},
        {"text": "Suggest the package that fits the need they showed interest in.",
         "signal_types": ("configure_save", "compare"), "action": "recommend_package", "confidence": 0.76},
    ],
    "QUALIFY": [
        {"text": "Offer a test drive of the configured trim.",
         "signal_types": ("test_drive_slot_view", "configure_save"), "action": "book_test_drive", "confidence": 0.88},
        {"text": "Start a soft pre-qualification to firm up budget.",
         "signal_types": ("price_check", "configure_save"), "action": "start_prequal", "confidence": 0.74},
    ],
    "FINANCE": [
        {"text": "Present the payment scenarios on their offer and answer rate questions.",
         "signal_types": ("price_check", "prequal.requested", "prequal.decided"), "action": "present_offer", "confidence": 0.8},
    ],
    "TRADE_IN": [
        {"text": "Refresh their trade estimate with current mileage.",
         "signal_types": ("price_check", "trade.valued"), "action": "get_trade_estimate", "confidence": 0.79},
    ],
    "AGREE": [
        {"text": "Walk the F&I menu (good-better-best) tied to how they'll use the vehicle.",
         "signal_types": ("deal.structured", "configure_save"), "action": "present_menu", "confidence": 0.77},
    ],
    "DELIVER": [
        {"text": "Confirm the delivery appointment and prep the walk-through.",
         "signal_types": ("deal.booked", "appointment.booked"), "action": "schedule_delivery", "confidence": 0.83},
    ],
    # service-lane stages (A7 shares A3's suggestion strip on the service floor)
    "BOOK": [
        {"text": "Offer the earliest slot with a loaner if they've booked before.",
         "signal_types": ("appointment.booked",), "action": "book_service", "confidence": 0.75},
    ],
    "CHECK_IN": [
        {"text": "Surface the declined work from the last visit to re-present.",
         "signal_types": ("service.declined_work", "inspection.submitted"), "action": "represent_declined", "confidence": 0.78},
    ],
}


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _parse(ts: str | None) -> _dt.datetime | None:
    if not ts:
        return None
    try:
        return _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _active_config(market: str, env_prefix: str | None) -> dict[str, Any]:
    versions = ddb.query_hash(_CONFIG, "item_key", f"suggestions#{market}", env_prefix)
    if not versions:
        return {"precompute_stages": JOURNEY_STAGES, "confidence_threshold": 0.0}
    active = [v for v in versions if v.get("is_active")]
    return (active or sorted(versions, key=lambda v: v.get("version") or 0, reverse=True))[0]


def _recent_activity(customer_id: str, journey_id: str, env_prefix: str | None) -> list[dict[str, Any]]:
    """Merged, time-sorted recent activity: CDP signals + journey events."""
    acts: list[dict[str, Any]] = []
    for s in ddb.scan_filter("DEALER_Signals", "customer_id", customer_id, env_prefix) if customer_id else []:
        acts.append({"type": s.get("type"), "ts": s.get("signal_ts"),
                     "detail": s.get("content_title") or s.get("model_id") or s.get("compare_with"),
                     "kind": "signal"})
    for e in ddb.query_hash("DEALER_JourneyEvents", "journey_id", journey_id, env_prefix):
        acts.append({"type": e.get("event_type"), "ts": e.get("event_ts"),
                     "detail": e.get("evidence"), "kind": "event"})
    acts.sort(key=lambda a: a.get("ts") or "", reverse=True)
    return acts


def generate_contextual_suggestions(journey: dict[str, Any], role: str = "sales",
                                    env_prefix: str | None = None) -> dict[str, Any]:
    """Return lifecycle-stage suggestions for a journey, each citing a recent
    activity (spec §3.3, acceptance #14).

    A suggestion is emitted only when: (1) its stage is in the active config's
    precompute_stages, (2) the matrix rule fires, (3) a recent activity of a
    matching type exists to cite, and (4) confidence >= the config threshold."""
    stage = journey.get("stage")
    customer_id = journey.get("customer_id")
    journey_id = journey.get("journey_id")
    market = journey.get("market_id") or "us"

    cfg = _active_config(market, env_prefix)
    threshold = float(cfg.get("confidence_threshold") or 0.0)
    allowed_stages = set(cfg.get("precompute_stages") or JOURNEY_STAGES)

    activity = _recent_activity(customer_id, journey_id, env_prefix)
    activity_by_type: dict[str, dict[str, Any]] = {}
    for a in activity:                      # newest first, so first seen per type wins
        activity_by_type.setdefault(a.get("type"), a)

    suggestions: list[dict[str, Any]] = []
    in_scope = stage in allowed_stages
    for cand in _MATRIX.get(stage, []):
        if cand["confidence"] < threshold:
            continue
        # find the most recent activity of a type this suggestion keys on
        cited = next((activity_by_type[t] for t in cand["signal_types"] if t in activity_by_type), None)
        if cited is None:
            continue                        # no activity to ground it -> hold back
        when = (cited.get("ts") or "")[:10]
        suggestions.append({
            "text": cand["text"],
            "action": cand["action"],
            "confidence": cand["confidence"],
            "why": f"{cited.get('type')} on {when}" + (f" ({cited['detail']})" if cited.get("detail") else ""),
            "evidence": {"type": cited.get("type"), "ts": cited.get("ts"), "kind": cited.get("kind")},
        })

    suggestions.sort(key=lambda s: s["confidence"], reverse=True)
    return {
        "journey_id": journey_id,
        "customer_id": customer_id,
        "stage": stage,
        "role": role,
        "in_precompute_scope": in_scope,
        "confidence_threshold": threshold,
        "suggestions": suggestions,
        "activity_considered": len(activity),
        "source_ref": f"A3.generate_contextual_suggestions (matrix + suggestions#{market}.v{cfg.get('version')})",
    }
