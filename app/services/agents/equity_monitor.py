"""L6 — Nightly trade-equity monitor (A4 agentic job, spec §6a / §4.10.2).

The agentic half of A4: nothing here starts with a user request. On a schedule
it scans active journeys, recomputes trade equity with the A4 agent, and for
customers who have crossed into positive equity it writes/refreshes a
DEALER_Recommendations row (action=equity_flag) — the "equity flag" the Advisor
and Service screens already surface. This is what makes the system agentic:
agents act on a clock, not only on a click.

Respects the autonomy level for `a4.detect_equity` (PLAT_ConfigAutonomy): at
level `recommend` (the affirmed default) it WRITES a recommendation card for a
human to action; it never auto-executes a downstream effect. The recommendation
id is deterministic per journey (REC-MON-<journey_id>), so repeated nightly runs
upsert rather than pile up (idempotent).

Events (spec §4.10.2 triggers, emitted here since this is the only place the
crossing is observed):
  * `equity.crossed_positive`   — the FIRST time a journey's equity is seen
    positive (no REC-MON row existed yet) -> A2 Upgrade Pitch play.
  * `lease.end_window_entered`  — the first time the customer's lease is within
    the upgrade window (REC-LEASE-<journey_id> card written once) -> same play.

  run_equity_monitor(market, apply, limit) -> summary of scanned/flagged/written.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from ..common import ddb
from ..events import bus as _bus
from . import equity

MONITOR_ACTION_ID = "a4.detect_equity"
MONITOR_SOURCE = "agentic:nightly_equity_monitor (plan §6a; autonomy a4.detect_equity)"


def _autonomy_level(market: str, env_prefix: str | None) -> str:
    """Resolve the active tenant-scope autonomy level for a4.detect_equity.

    Reads the versioned PLAT_ConfigAutonomy rows; picks the highest active
    version for the tenant scope. Defaults to 'recommend' if unset."""
    rows = ddb.scan_filter("PLAT_ConfigAutonomy", "action_id", MONITOR_ACTION_ID, env_prefix)
    active = [r for r in rows if r.get("is_active") and r.get("scope") == "tenant"
              and (r.get("market_id") in (market, None))]
    if not active:
        return "recommend"
    latest = max(active, key=lambda r: r.get("version") or 0)
    return latest.get("level") or "recommend"


def run_equity_monitor(market: str = "us", apply: bool = False,
                       limit: int | None = None, env_prefix: str | None = None) -> dict[str, Any]:
    """Scan journeys, recompute equity, flag positive-equity customers.

    apply=False is a dry run (computes + reports, writes nothing).
    """
    level = _autonomy_level(market, env_prefix)
    journeys = ddb.scan_filter("DEALER_Journeys", "market_id", market, env_prefix)
    if limit is not None:
        journeys = journeys[:limit]

    scanned = flagged = written = crossed = lease_window = 0
    now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    results: list[dict[str, Any]] = []

    may_write = apply and level in ("recommend", "act_and_notify", "autonomous")
    for j in journeys:
        jid = j.get("journey_id")
        if not jid:
            continue
        scanned += 1
        try:
            eq = equity.build_equity(jid, env_prefix)
        except equity.NotFound:
            continue
        # Lease end window (independent of equity sign): announce once per journey.
        cur = eq.get("current_vehicle") or {}
        months = cur.get("months_remaining")
        if cur.get("ownership") == "lease" and isinstance(months, (int, float)) \
                and months <= equity._LEASE_UPGRADE_WINDOW_MONTHS:
            lease_rec_id = f"REC-LEASE-{jid}"
            if ddb.get_item("DEALER_Recommendations", {"recommendation_id": lease_rec_id}, env_prefix) is None:
                lease_window += 1
                if may_write:
                    ddb.put_item("DEALER_Recommendations", {
                        "recommendation_id": lease_rec_id, "action": "lease_end_window",
                        "agent_id": "A4", "customer_id": eq.get("customer_id"), "journey_id": jid,
                        "shown": 0, "accepted": 0, "converted": 0, "shown_at": now,
                        "autonomy_level": level, "months_remaining": months,
                        "source_ref": MONITOR_SOURCE,
                    }, env_prefix)
                    _bus.emit("lease.end_window_entered", {
                        "journey_id": jid, "customer_id": eq.get("customer_id"),
                        "dealer_id": j.get("dealer_id"), "market_id": market,
                        "months_remaining": months, "window_months": equity._LEASE_UPGRADE_WINDOW_MONTHS,
                        "source_ref": MONITOR_SOURCE,
                    }, env_prefix=env_prefix, actor="A4")
        if eq.get("equity_position") != "positive":
            continue
        # the one upgrade rule (common/vehicles.upgrade_window): equity in a car still owned, a year
        # old or a lease ending — a journey that just delivered carries its trade-in's equity, not a nudge
        from ..common import vehicles as _veh
        if j.get("stage") == "OWN" and not _veh.upgrade_window(j.get("customer_id"), env_prefix):
            continue
        flagged += 1
        rec_id = f"REC-MON-{jid}"
        first_seen = ddb.get_item("DEALER_Recommendations", {"recommendation_id": rec_id}, env_prefix) is None
        row = {
            "recommendation_id": rec_id,
            "action": "equity_flag",
            "agent_id": "A4",
            "customer_id": eq.get("customer_id"),
            "journey_id": jid,
            "shown": 0, "accepted": 0, "converted": 0,
            "shown_at": now,
            "autonomy_level": level,
            "equity_value": eq.get("equity"),
            "source_ref": MONITOR_SOURCE,
        }
        results.append({"journey_id": jid, "equity": eq.get("equity"), "rec_id": rec_id})
        # Autonomy gate: 'recommend'/'act_and_notify'/'autonomous' all permit
        # WRITING the recommendation card; only an OFF/observe level would skip.
        if may_write:
            ddb.put_item("DEALER_Recommendations", row, env_prefix)
            written += 1
            if first_seen:
                # The crossing is observed exactly once (no card existed before):
                # that is the §4.10.2 trigger for the Upgrade Pitch play.
                crossed += 1
                _bus.emit("equity.crossed_positive", {
                    "journey_id": jid, "customer_id": eq.get("customer_id"),
                    "dealer_id": j.get("dealer_id"), "market_id": market,
                    "equity": eq.get("equity"), "currency": eq.get("currency"),
                    "recommendation_id": rec_id, "source_ref": MONITOR_SOURCE,
                }, env_prefix=env_prefix, actor="A4")

    return {
        "market": market,
        "autonomy_level": level,
        "scanned": scanned,
        "positive_equity_flagged": flagged,
        "recommendations_written": written,
        "equity_crossed_events": crossed,
        "lease_window_entered": lease_window,
        "applied": apply,
        "ran_at": now,
        "sample": results[:10],
    }
