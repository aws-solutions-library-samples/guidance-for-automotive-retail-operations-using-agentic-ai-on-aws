"""A1 — rescore_profiles (agentic job), spec §3.1 / §4.10.2.

The proactive half of Discovery. Nothing here starts with a user request: on a
schedule (or on `inventory.changed` / `incentive.changed`) it re-ranks the
catalog for every journey still shopping (DISCOVER / CONFIGURE), and when the
top match is **in stock at the home dealer** and differs from what the journey
last saw, it records the match on the journey and emits `discovery.match_found`
— the trigger for the A2 *Recommend Vehicle* play.

  run_rescore_profiles(market, apply, limit) -> summary

Grounding / invariants:
  * ranking is `discovery.rank_vehicles` — the same deterministic, explainable
    scorer the Advisor screen shows, so the proactive match never disagrees with
    the on-screen list.
  * a match is only announced when a real VIN is in stock (`in_stock_count > 0`);
    "available to order" is not a match event.
  * idempotent per (journey, trim): the journey row remembers
    `context.discovery_match_trim`; the same trim is not announced twice.
  * autonomy: `a1.match_vehicles` (seeded) — at `off` the job observes only.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from ..common import autonomy_config, ddb
from ..events import bus
from . import discovery

ACTION_ID = "a1.match_vehicles"          # seeded PLAT_ConfigAutonomy action for A1 matching
SOURCE = "agentic:rescore_profiles (spec §3.1; trigger discovery.match_found)"
_SHOPPING = ("DISCOVER", "CONFIGURE")


def _iso(d: _dt.datetime) -> str:
    return d.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_rescore_profiles(market: str = "us", apply: bool = False, limit: int | None = None,
                         env_prefix: str | None = None) -> dict[str, Any]:
    level = autonomy_config.active_level(ACTION_ID, market, env_prefix)
    at = _dt.datetime.now(_dt.timezone.utc)
    journeys = [j for j in ddb.scan_filter("DEALER_Journeys", "market_id", market, env_prefix)
                if j.get("stage") in _SHOPPING]
    if limit is not None:
        journeys = journeys[:limit]
    scanned = matched = announced = 0
    sample: list[dict[str, Any]] = []
    for j in journeys:
        jid = j.get("journey_id")
        if not jid:
            continue
        scanned += 1
        try:
            ranked = discovery.rank_vehicles(jid, env_prefix)
        except discovery.NotFound:
            continue
        top = (ranked.get("matches") or [None])[0]
        if not top or not top.get("in_stock_count"):
            continue
        matched += 1
        ctx = dict(j.get("context") or {})
        if ctx.get("discovery_match_trim") == top.get("trim_id"):
            continue                                   # already announced this trim
        entry = {"journey_id": jid, "trim_id": top.get("trim_id"), "trim_name": top.get("trim_name"),
                 "in_stock_count": top.get("in_stock_count"), "example_vin": top.get("example_vin"),
                 "score": top.get("score")}
        if apply and level != "off":
            row = dict(j)
            ctx["discovery_match_trim"] = top.get("trim_id")
            ctx["discovery_match_at"] = _iso(at)
            row["context"] = ctx
            row["updated_at"] = _iso(at)
            ddb.put_item("DEALER_Journeys", row, env_prefix)
            ev = bus.emit("discovery.match_found", {
                "journey_id": jid, "customer_id": j.get("customer_id"), "dealer_id": j.get("dealer_id"),
                "market_id": market, "stage": j.get("stage"),
                "model_id": top.get("model_id"), "trim_id": top.get("trim_id"),
                "trim_name": top.get("trim_name"), "example_vin": top.get("example_vin"),
                "in_stock_count": top.get("in_stock_count"), "score": top.get("score"),
                "autonomy_level": level, "source_ref": SOURCE,
            }, env_prefix=env_prefix, actor="A1")
            entry["event_id"] = ev.get("event_id")
            announced += 1
        sample.append(entry)
    return {
        "market": market, "autonomy_level": level, "scanned": scanned,
        "in_stock_matches": matched, "announced": announced, "applied": apply,
        "ran_at": _iso(at), "sample": sample[:12],
    }
