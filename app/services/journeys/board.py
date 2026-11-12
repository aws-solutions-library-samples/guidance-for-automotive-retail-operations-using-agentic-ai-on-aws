"""L3 — the Journeys kanban read model (spec §5.4).

Lists the purchase journeys visible to the caller, grouped into the 8 purchase
stages, with the summary fields a kanban card shows. This is the sales-side
"one owner of the funnel" board: every active journey as a card in its stage
lane, with owner, model, stall/nurture flags.

Access pattern:
  journeys  scan DEALER_Journeys, filtered to the caller's dealer scope and market.

Scope is applied here (not just at the authorizer) because the board is a LIST:
a dealer advisor sees only their dealer's journeys; an OEM role sees the market.
Scans are fine at this size; the production refinement is a GSI on
(dealer_id, stage) — noted for L4.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from ..common import ddb
from .assist import JOURNEY_STAGES

# Fields a kanban card needs (keeps the payload small).
_CARD_FIELDS = (
    "journey_id", "stage", "customer_id", "dealer_id", "owner_user_id",
    "primary_model_id", "primary_trim_id", "source", "status",
    "stalled", "idle_days", "nurture_active", "golden_story",
    "missing_context",  # §5.4 missing-context warning shown on the card
    "summary", "last_event_at", "updated_at",
    "pending_handoff", "vin",  # who the journey is on its way to; the unit it is written against
)


def build_board(scope: dict[str, Any], market_id: str | None = None,
                env_prefix: str | None = None) -> dict[str, Any]:
    """Assemble the kanban for the caller's scope.

    scope: the resolved auth scope (role, dealer_ids, is_oem, market_id).
    market_id: optional explicit market filter; defaults to the caller's market.
    """
    market = market_id or scope.get("market_id") or "us"
    rows = ddb.scan_filter("DEALER_Journeys", "market_id", market, env_prefix)

    # Scope filter: dealer-scoped callers see only their dealers; OEM sees all.
    if not scope.get("is_oem"):
        allowed = scope.get("dealer_ids") or set()
        rows = [r for r in rows if r.get("dealer_id") in allowed]

    # Enrichment for the §5.4 card: customer + vehicle + advisor names, days in
    # stage (from the latest stage-entry event), and the live handoff gaps.
    ids = {r["journey_id"] for r in rows}
    entered: dict[str, str] = {}
    for e in ddb.scan_all("DEALER_JourneyEvents", env_prefix):
        if e.get("journey_id") in ids and e.get("event_type") in ("stage.advanced", "journey.created"):
            ts = e.get("event_ts") or ""
            if ts > entered.get(e["journey_id"], ""):
                entered[e["journey_id"]] = ts
    custs = {c["customer_id"]: c for c in ddb.scan_all("DEALER_Customers", env_prefix)}
    users = {u["user_id"]: u for u in ddb.scan_all("PLAT_Users", env_prefix)}
    models = {m["model_id"]: m.get("name") for m in ddb.scan_all("OEM_CatalogModels", env_prefix)}
    now = _dt.datetime.now(_dt.timezone.utc)
    from .supervisor import validate_handoff

    lanes: dict[str, list[dict[str, Any]]] = {st: [] for st in JOURNEY_STAGES}
    other: list[dict[str, Any]] = []
    for r in rows:
        card = {k: r.get(k) for k in _CARD_FIELDS}
        c = custs.get(r.get("customer_id"), {})
        u = users.get(r.get("owner_user_id"), {})
        card["customer_name"] = " ".join(filter(None, [c.get("given_name"), c.get("family_name")])) or None
        card["advisor_name"] = " ".join(filter(None, [u.get("given_name"), u.get("family_name")])) or None
        ph = r.get("pending_handoff") or None
        if ph:
            tu = users.get(ph.get("to_user"), {})
            card["pending_handoff"] = {**ph, "to_user_name": " ".join(filter(None, [tu.get("given_name"), tu.get("family_name")])) or ph.get("to_user")}
        card["vehicle_name"] = models.get(r.get("primary_model_id"))
        ent = entered.get(r["journey_id"])
        try:
            card["days_in_stage"] = round((now - _dt.datetime.fromisoformat(ent.replace("Z", "+00:00"))).total_seconds() / 86400, 1) if ent else None
        except ValueError:
            card["days_in_stage"] = None
        if r.get("stage") != "OWN":
            card["gaps"] = validate_handoff(r)["missing"]
        st = r.get("stage")
        if st in lanes:
            lanes[st].append(card)
        else:
            other.append(card)

    # newest activity first within each lane
    for st in lanes:
        lanes[st].sort(key=lambda c: c.get("last_event_at") or "", reverse=True)

    return {
        "market_id": market,
        "scope": {
            "role": scope.get("role"),
            "is_oem": scope.get("is_oem"),
            "dealer_ids": sorted(scope.get("dealer_ids") or []),
        },
        "stages": JOURNEY_STAGES,
        "lanes": lanes,
        "counts": {st: len(lanes[st]) for st in JOURNEY_STAGES},
        "total": sum(len(v) for v in lanes.values()),
        "unstaged": other,
    }
