"""A5 — Audiences (spec §3.5 get_personalize_segments / refresh_audiences, §5.16).

Sandbox stand-in for Amazon Personalize user-segmentation: transparent,
rule-built segments over lifestyle profiles, journeys, owned vehicles, and
valuations. Same swap pattern as platform/ranker.py — the Personalize campaign
replaces `build_audiences` without changing the response shape.

  build_audiences(scope, market)               -> segments with size + definition
  export_audience(segment_id, platform, actor) -> ad-platform export manifest

Data classification (§8.2): exports carry only SHA-256-hashed customer ids — no
contact fields (C3) and nothing C4 — and refuse consent opt-outs.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
from typing import Any, Callable

from ..common import authz, ddb, trace

PLATFORMS = ("google_ads", "meta", "adobe")
# Last recorded build per (market, scope node) so a refresh can report movement.
# Process-local by design: the durable build history is the AI Log trace each
# refresh writes (a read-model table is the production step).
_LAST_BUILD: dict[tuple[str, str], list[dict[str, Any]]] = {}


class NotFound(Exception):
    pass


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _customers(scope: dict[str, Any], market: str, env_prefix: str | None) -> list[dict[str, Any]]:
    node = scope.get("scope_node_id") or "OEM"
    dealers = authz.dealer_leaves_under(node, env_prefix, market) if node != "OEM" else None
    rows = ddb.scan_filter("DEALER_Customers", "market_id", market, env_prefix)
    return [c for c in rows if dealers is None or c.get("dealer_id") in dealers]


def _segments(market: str, env_prefix: str | None) -> list[tuple[str, str, str, Callable[[dict, dict], bool]]]:
    brand = None
    m = ddb.get_item("OEM_Markets", {"market_id": market}, env_prefix)
    if m:
        brand = ((m.get("brand") or {}).get("name") or "").split()[0].lower() or None
    return [
        ("SEG-EV-INTENDERS", "EV intenders", "lifestyle_profile.powertrain_openness = ev",
         lambda c, x: (c.get("lifestyle_profile") or {}).get("powertrain_openness") == "ev"),
        ("SEG-LEASE-END", "Lease-end within 6 months",
         "current_vehicle.ownership = lease and months_remaining <= 6",
         lambda c, x: (c.get("current_vehicle") or {}).get("ownership") == "lease"
         and (c.get("current_vehicle") or {}).get("months_remaining") is not None
         and (c.get("current_vehicle") or {}).get("months_remaining") <= 6),
        ("SEG-CONQUEST", "Conquest — competitor owners", "current_vehicle.make is not the market brand",
         lambda c, x: bool((c.get("current_vehicle") or {}).get("make"))
         and (brand is None or brand not in str((c.get("current_vehicle") or {}).get("make")).lower())),
        ("SEG-POS-EQUITY", "Positive trade equity", "latest DEALER_Valuations.equity > 0",
         lambda c, x: (x["equity"].get(c["customer_id"]) or 0) > 0),
        ("SEG-STALLED-CONFIG", "Stalled in CONFIGURE", "active journey in CONFIGURE marked stalled or idle >= 14 days",
         lambda c, x: any(j.get("stage") == "CONFIGURE" and (j.get("stalled") or (j.get("idle_days") or 0) >= 14)
                          for j in x["journeys"].get(c["customer_id"], []))),
        ("SEG-FAMILY-SUV", "Family SUV shoppers", "household_size >= 4 and safety_priority = high",
         lambda c, x: (c.get("household_size") or 0) >= 4
         and (c.get("lifestyle_profile") or {}).get("safety_priority") == "high"),
    ]


def _context(market: str, env_prefix: str | None) -> dict[str, Any]:
    equity: dict[str, float] = {}
    latest: dict[str, str] = {}
    for v in ddb.scan_all("DEALER_Valuations", env_prefix):
        cid = v.get("customer_id")
        if cid and (v.get("valued_at") or "") >= latest.get(cid, ""):
            latest[cid] = v.get("valued_at") or ""
            equity[cid] = float(v.get("equity") or 0)
    journeys: dict[str, list[dict[str, Any]]] = {}
    for j in ddb.scan_filter("DEALER_Journeys", "market_id", market, env_prefix):
        journeys.setdefault(j.get("customer_id"), []).append(j)
    consent = {c.get("customer_id"): c for c in ddb.scan_filter("CUST_Consent", "market_id", market, env_prefix)}
    return {"equity": equity, "journeys": journeys, "consent": consent}


def _marketable(c: dict[str, Any], ctx: dict[str, Any]) -> bool:
    k = ctx["consent"].get(c["customer_id"]) or {}
    return bool(k.get("email_opt_in") or (k.get("sms_opt_in") and not k.get("sms_opt_out_at")))


def build_audiences(scope: dict[str, Any], market: str = "us", env_prefix: str | None = None) -> dict[str, Any]:
    custs = _customers(scope, market, env_prefix)
    ctx = _context(market, env_prefix)
    out = []
    in_any: set[str] = set()
    reach_any: set[str] = set()
    for sid, name, rule, pred in _segments(market, env_prefix):
        members = [c for c in custs if pred(c, ctx)]
        reachable = [c for c in members if _marketable(c, ctx)]
        in_any |= {c["customer_id"] for c in members}
        reach_any |= {c["customer_id"] for c in reachable}
        out.append({"segment_id": sid, "name": name, "definition": rule, "size": len(members),
                    "marketable": len(reachable),
                    "by_dealer": _count(members, "dealer_id"),
                    "source_ref": f"rules over DEALER_Customers/DEALER_Journeys/DEALER_Valuations ({market})"})
    # reach is people, not memberships: a customer in three segments is one person to reach, so the
    # distinct counts can never exceed the population (700 reach over 621 customers was the sum)
    return {"market_id": market, "engine": "rules (Amazon Personalize user-segmentation when configured)",
            "population": len(custs), "segments": out, "platforms": list(PLATFORMS),
            "in_any_segment": len(in_any), "marketable_reach": len(reach_any),
            "refreshed_at": _now().replace(microsecond=0).isoformat().replace("+00:00", "Z")}


def refresh_audiences(scope: dict[str, Any], actor: str, market: str = "us",
                      env_prefix: str | None = None) -> dict[str, Any]:
    """Rebuild the segments from the current journey outcomes (spec §3.5
    `refresh_audiences`).

    The segments are rules over live rows, so a refresh is a recompute: it
    re-evaluates every rule against the data as it stands now and reports the
    counts, per-segment movement against the last recorded build, and the
    marketable (consented) reach. Nothing is pushed to an ad platform here — an
    export is a separate, explicit action (`export_audience`). The Personalize
    segment job replaces the recompute without changing this response."""
    before = {s["segment_id"]: s for s in _LAST_BUILD.get((market, scope.get("scope_node_id") or "OEM"), [])}
    built = build_audiences(scope, market, env_prefix)
    segments = []
    for s in built["segments"]:
        prev = before.get(s["segment_id"])
        segments.append({"segment_id": s["segment_id"], "name": s["name"], "size": s["size"],
                         "marketable": s["marketable"],
                         "previous_size": (prev or {}).get("size"),
                         "delta": (s["size"] - prev["size"]) if prev else None,
                         "source_ref": s["source_ref"]})
    _LAST_BUILD[(market, scope.get("scope_node_id") or "OEM")] = built["segments"]
    total, marketable = sum(s["size"] for s in segments), built["marketable_reach"]
    moved = [s for s in segments if s.get("delta")]
    trace_id = trace.write_trace(tool="refresh_audiences", agent_id="A5", outcome="ok", market=market,
                                 routing_decision=f"recomputed {len(segments)} segments ({total} memberships)",
                                 extra={"actor": actor, "segments": len(segments), "memberships": total,
                                        "marketable": marketable})
    return {"refreshed": True, "market_id": market, "engine": built["engine"],
            "population": built["population"], "segment_count": len(segments),
            "memberships": total, "marketable": marketable, "in_any_segment": built["in_any_segment"],
            "segments": segments, "changed": len(moved),
            "refreshed_at": built["refreshed_at"], "refreshed_by": actor, "trace_id": trace_id,
            "note": (f"{len(segments)} segments recomputed from {built['population']} customers in scope — "
                     f"{built['in_any_segment']} in at least one segment, {marketable} of them marketable after consent "
                     f"({total} memberships across segments). "
                     + (f"{len(moved)} segment(s) changed size since the last refresh."
                        if moved else
                        "No earlier build to compare against yet — this is the baseline."
                        if not before else "Sizes are unchanged since the last refresh.")),
            "source_ref": "A5.refresh_audiences (rules recomputed over DEALER_Customers/"
                          "DEALER_Journeys/DEALER_Valuations/CUST_Consent)"}


def _count(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[str(r.get(key))] = out.get(str(r.get(key)), 0) + 1
    return dict(sorted(out.items()))


def _hash(cid: str) -> str:
    return hashlib.sha256(f"retail-ops-audience|{cid}".encode()).hexdigest()


def export_audience(segment_id: str, platform: str, actor: str, scope: dict[str, Any],
                    market: str = "us", env_prefix: str | None = None) -> dict[str, Any]:
    if platform not in PLATFORMS:
        raise ValueError(f"platform must be one of {PLATFORMS}")
    seg = next((s for s in _segments(market, env_prefix) if s[0] == segment_id), None)
    if seg is None:
        raise NotFound(f"segment {segment_id} not found")
    ctx = _context(market, env_prefix)
    members = [c for c in _customers(scope, market, env_prefix) if seg[3](c, ctx) and _marketable(c, ctx)]
    hashed = sorted(_hash(c["customer_id"]) for c in members)
    ts = _now().replace(microsecond=0).isoformat().replace("+00:00", "Z")
    export_id = "AUDX-" + hashlib.sha1(f"{segment_id}|{platform}|{ts}".encode()).hexdigest()[:10]
    trace_id = trace.write_trace(tool="export_audience", agent_id="A5", outcome="ok", market=market,
                                 routing_decision=f"{segment_id} -> {platform} ({len(hashed)} hashed ids)",
                                 extra={"export_id": export_id, "actor": actor, "segment_id": segment_id,
                                        "platform": platform, "count": len(hashed)})
    return {"export_id": export_id, "segment_id": segment_id, "name": seg[1], "platform": platform,
            "count": len(hashed), "id_type": "sha256(customer_id)", "sample": hashed[:3],
            "excluded": "consent opt-outs; no contact fields exported (§8.2)",
            "exported_at": ts, "trace_id": trace_id,
            "status": "sandbox — written to the AI Log; the ad-platform connector uploads in live mode"}
