"""L3 — Service Board + RO detail read models (A7, spec §5.20).

A7 is the service-journey supervisor (the second supervisor; A2 owns purchase).
Two read models over the loaded service tables:

  build_service_board(scope, market)  — service journeys grouped into the service
      stages, scope-enforced to the caller's dealer(s); the Service Board (§5.20.1).
  build_ro_detail(service_journey_id)  — one service journey with its repair order,
      estimate, inspection findings, parts orders, and declined work (§5.20.4/5).

All keyed on service_journey_id. Scans are fine at this size; GSIs on
(dealer_id, stage) and (service_journey_id) are the production refinement.
"""
from __future__ import annotations

from typing import Any

from ..common import ddb, vehicles
from . import metrics

# Service-journey stages (spec §2.1.1) plus the terminal COMPLETED present in data.
SERVICE_STAGES = [
    "BOOK", "CHECK_IN", "INSPECT", "DIAGNOSE", "APPROVE",
    "REPAIR", "READY", "FOLLOW_UP", "COMPLETED",
]

_CARD_FIELDS = (
    "service_journey_id", "stage", "vin", "dealer_id", "advisor_id", "technician_id",
    "customer_id", "purchase_journey_id", "promised_at", "projected_at",
    "parts_hold", "wait_type", "golden_story",
)


def build_service_board(scope: dict[str, Any], market_id: str | None = None,
                        env_prefix: str | None = None) -> dict[str, Any]:
    market = market_id or scope.get("market_id") or "us"
    rows = ddb.scan_filter("DEALER_ServiceJourneys", "market_id", market, env_prefix)

    if not scope.get("is_oem"):
        allowed = scope.get("dealer_ids") or set()
        rows = [r for r in rows if r.get("dealer_id") in allowed]

    lanes: dict[str, list[dict[str, Any]]] = {st: [] for st in SERVICE_STAGES}
    other: list[dict[str, Any]] = []
    for r in rows:
        card = {k: r.get(k) for k in _CARD_FIELDS}
        card["promise_at_risk"] = _at_risk(r)
        st = r.get("stage")
        (lanes[st] if st in lanes else other).append(card)

    for st in lanes:
        lanes[st].sort(key=lambda c: c.get("promised_at") or "", reverse=False)

    return {
        "market_id": market,
        "scope": {"role": scope.get("role"), "is_oem": scope.get("is_oem"),
                  "dealer_ids": sorted(scope.get("dealer_ids") or [])},
        "stages": SERVICE_STAGES,
        "lanes": lanes,
        "counts": {st: len(lanes[st]) for st in SERVICE_STAGES},
        "total": sum(len(v) for v in lanes.values()),
        "kpis": _kpis(rows, env_prefix),
        "unstaged": other,
    }


def build_ro_detail(service_journey_id: str, env_prefix: str | None = None) -> dict[str, Any] | None:
    """One service journey with its RO, estimate, inspections, parts, declined work."""
    sj = ddb.get_item("DEALER_ServiceJourneys", {"service_journey_id": service_journey_id}, env_prefix)
    if sj is None:
        return None

    ros = ddb.scan_filter("DEALER_RepairOrders", "service_journey_id", service_journey_id, env_prefix)
    ro = ros[0] if ros else None
    estimates = ddb.scan_filter("DEALER_ServiceEstimates", "service_journey_id", service_journey_id, env_prefix)
    inspections = ddb.scan_filter("DEALER_Inspections", "service_journey_id", service_journey_id, env_prefix)
    declined = ddb.scan_filter("DEALER_DeclinedWork", "service_journey_id", service_journey_id, env_prefix)
    parts = ddb.scan_filter("DEALER_PartsOrders", "ro_id", ro["ro_id"], env_prefix) if ro else []

    findings = sorted(inspections, key=lambda i: {"red": 0, "yellow": 1, "green": 2}.get(i.get("status"), 3))

    return {
        "service_journey_id": service_journey_id,
        "stage": sj.get("stage"),
        "stepper": _stepper(sj.get("stage")),
        "vehicle": vehicles.for_vin(sj.get("vin"), env_prefix),
        "customer_id": sj.get("customer_id"),
        "purchase_journey_id": sj.get("purchase_journey_id"),
        "advisor_id": sj.get("advisor_id"),
        "technician_id": sj.get("technician_id"),
        "promised_at": sj.get("promised_at"),
        "projected_at": sj.get("projected_at"),
        "promise_at_risk": _at_risk(sj),
        "parts_hold": sj.get("parts_hold"),
        "wait_type": sj.get("wait_type"),
        "repair_order": ro and {k: ro.get(k) for k in
                                ("ro_id", "ro_number", "status", "labor_total",
                                 "parts_total", "total", "currency")},
        "estimates": [{k: e.get(k) for k in ("estimate_id", "status", "total", "currency", "presented_at")}
                      for e in estimates],
        "inspection_findings": [{k: f.get(k) for k in
                                 ("inspection_id", "item", "status", "urgency", "measurement", "unit")}
                                for f in findings],
        "parts_orders": [{k: p.get(k) for k in ("parts_order_id", "status", "eta")} for p in parts],
        "declined_work": [{k: d.get(k) for k in ("declined_id", "item", "amount", "currency", "declined_at")}
                          for d in declined],
        "meta": {"market_id": sj.get("market_id"), "golden_story": sj.get("golden_story"),
                 "dealer_id": sj.get("dealer_id")},
    }


# --- helpers ----------------------------------------------------------------

def _at_risk(sj: dict[str, Any]) -> bool:
    """Past the promise or projected past it — the shared definition (service/metrics.py)."""
    import datetime as _dt
    return metrics.promise_state(sj, _dt.datetime.now(_dt.UTC)) in ("past", "at_risk")


def _stepper(current: str | None) -> list[dict[str, Any]]:
    out, reached = [], True
    for st in SERVICE_STAGES:
        is_cur = st == current
        out.append({"stage": st, "current": is_cur, "reached": reached})
        if is_cur:
            reached = False
    return out


def _kpis(rows: list[dict[str, Any]], env_prefix: str | None = None) -> dict[str, Any]:
    """The shared fixed-ops numbers (service/metrics.py) for these visits — the same the Store Cockpit
    and the Service Cockpit show, so no screen counts its own way."""
    shared = metrics.summary(rows, ddb.scan_all("DEALER_RepairOrders", env_prefix),
                             ddb.scan_all("DEALER_PartsOrders", env_prefix),
                             [e for e in ddb.scan_all("DEALER_ServiceEstimates", env_prefix)
                              if e.get("service_journey_id") in {r["service_journey_id"] for r in rows}])
    return {
        "lane_load": shared["lane_load"],
        "promise_at_risk": shared["at_risk"] + shared["past_promise"],
        "promise_past": shared["past_promise"],
        "parts_holds": shared["parts_hold"],
        "awaiting_approval": shared["waiting_approval"],
        "shared_metrics": shared,
    }
