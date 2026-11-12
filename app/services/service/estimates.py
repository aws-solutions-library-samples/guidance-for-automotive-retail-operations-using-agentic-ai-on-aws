"""A7 estimate lines, per-line decisions, bundles, repair-vs-replace (spec §5.20.4, acc #26/#27/#29).

The estimate row (`DEALER_ServiceEstimates`) carries its `lines` — each line is
grounded in an inspection finding (`finding_ref`), a labor op from
`DEALER_LaborOps` (flat-rate hours × shop rate) and a parts price, with a
coverage decision and `source_ref`s. Seeded estimates that predate persisted
lines get their lines derived (read-only) from the same sources.

  estimate_view(estimate_id)               -> lines + totals + bundles + rvr card
  build_lines(sj_id, adjustments)          -> priced lines (labor guide + parts)
  decide_lines(estimate_id, decisions, by) -> per-line approve/decline; declined
                                              lines -> DEALER_DeclinedWork; events
  repair_vs_replace(sj, total)             -> card when total > threshold % of A4 value

No price is computed in the UI; every figure here carries its inputs.
"""
from __future__ import annotations

import datetime as _dt
import os
from typing import Any

from ..common import ddb
from ..events import bus as _bus
from . import supervisor as svc_supervisor

_SJ = "DEALER_ServiceJourneys"
_ESTIMATES = "DEALER_ServiceEstimates"
_INSPECTIONS = "DEALER_Inspections"
_LABOR = "DEALER_LaborOps"
_DECLINED = "DEALER_DeclinedWork"
_VEHICLES = "DEALER_Vehicles"
_VALUATIONS = "DEALER_Valuations"
_EVENTS = "DEALER_ServiceJourneyEvents"

# repair_vs_replace_threshold_pct (§2.1.1); dealer config overrides in production.
RVR_THRESHOLD_PCT = float(os.environ.get("RETAIL_OPS_RVR_THRESHOLD_PCT", "40"))
# OEM basic / HV-battery warranty terms used by the sandbox coverage rule.
_BASIC_WARRANTY_MONTHS = 36
# Wear-item adjustment: brakes, tires, wipers or filters worn out this early are a defect
_WEAR_ADJUST_MONTHS = 12
_WEAR_ADJUST_MILES = 12000
_HV_WARRANTY_MONTHS = 96
_HV_ITEMS = ("hv battery", "charge port", "hv")
_URGENCY_RANK = {"safety": 0, "soon": 1, "monitor": 2, "none": 3}

# Inspection item keyword -> labor op (DEALER_LaborOps) + parts (description, unit price, qty).
_ITEM_OPS: list[tuple[str, str, tuple[str, float, int] | None]] = [
    ("rear brake", "LOP-BRAKE-R", ("Rear brake pad & rotor kit", 210.0, 1)),
    ("front brake", "LOP-BRAKE-F", ("Front brake pad & rotor kit", 230.0, 1)),
    ("brake", "LOP-BRAKE-F", ("Brake pad & rotor kit", 220.0, 1)),
    ("hv battery", "LOP-EV-HV-INSPECT", None),
    ("charge", "LOP-EV-CHARGE-PORT", ("Charge-port seal kit", 65.0, 1)),
    ("coolant", "LOP-COOLANT", ("Coolant (OEM spec)", 48.0, 1)),
    ("battery", "LOP-BATT", ("12V battery", 189.0, 1)),
    ("cabin", "LOP-CABIN-FILTER", ("Cabin air filter", 42.0, 1)),
    # a worn tire finding (red/yellow tread) is a replacement, not a rotation
    ("tire", "LOP-TIRE-REPL", ("Tire (OE size)", 185.0, 2)),
    ("align", "LOP-ALIGN", None),
    ("oil", "LOP-OIL", ("Oil & filter kit", 45.0, 1)),
    ("trans", "LOP-TRANS", ("ATF kit", 95.0, 1)),
    ("wiper", "LOP-DIAG", ("Wiper blade pair", 38.0, 1)),
]
_FALLBACK_OP = "LOP-DIAG"


class NotFound(Exception):
    pass


class InvalidDecision(Exception):
    pass


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse(ts: str | None) -> _dt.datetime | None:
    if not ts:
        return None
    try:
        return _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _op_for(item: str) -> tuple[str, tuple[str, float, int] | None]:
    key = (item or "").lower()
    for kw, op, part in _ITEM_OPS:
        if kw in key:
            return op, part
    return _FALLBACK_OP, ("Service part", 80.0, 1)


def _labor(op_id: str, market: str, env_prefix: str | None) -> dict[str, Any]:
    rows = [r for r in ddb.scan_filter(_LABOR, "labor_op_id", op_id, env_prefix)
            if (r.get("market_id") or market) == market] or ddb.scan_filter(_LABOR, "labor_op_id", op_id, env_prefix)
    row = rows[0] if rows else None
    if row is None:
        return {"labor_op_id": op_id, "name": op_id, "hours": 1.0, "rate": None, "source_ref": None}
    return {"labor_op_id": op_id, "name": row.get("name"), "hours": float(row.get("flat_rate_hours") or 0),
            "rate": float(row.get("shop_rate") or 0), "currency": row.get("currency"),
            "source_ref": f"DEALER_LaborOps:{op_id}"}


def line_coverage(vehicle: dict[str, Any] | None, item: str) -> dict[str, Any]:
    """Sandbox coverage rule: HV items within the HV warranty and anything within
    the basic warranty are warranty; otherwise customer pay. Returns the rule
    inputs so the decision is auditable (acc #27)."""
    delivered = _parse((vehicle or {}).get("delivered_at"))
    age_m = None
    if delivered:
        now = _dt.datetime.now(_dt.timezone.utc)
        age_m = (now.year - delivered.year) * 12 + (now.month - delivered.month)
    key = (item or "").lower()
    hv = any(h in key for h in _HV_ITEMS)
    wear = any(w in key for w in ("brake", "tire", "wiper", "filter", "oil"))
    # Routine maintenance is customer pay whatever the age (a cabin filter, an oil change):
    # it is replaced on schedule, not because it failed, so no warranty or wear adjustment.
    maintenance = any(w in key for w in ("filter", "oil", "wiper", "coolant flush", "rotation", "alignment"))
    odo = (vehicle or {}).get("odometer")
    try:
        odo = float(odo) if odo is not None else None
    except (TypeError, ValueError):
        odo = None
    km = ((vehicle or {}).get("market_id") or "us") == "de"
    wear_cap = _WEAR_ADJUST_MILES * (1.609 if km else 1)
    unit = "km" if km else "mi"
    if age_m is not None and hv and age_m <= _HV_WARRANTY_MONTHS:
        pay = "warranty"; rule = f"HV component, vehicle {age_m} mo ≤ {_HV_WARRANTY_MONTHS} mo HV warranty"
    elif maintenance:
        pay = "customer_pay"; rule = "scheduled maintenance item (replaced on schedule, not a defect)"
    elif wear and age_m is not None and odo is not None and age_m <= _WEAR_ADJUST_MONTHS and odo <= wear_cap:
        # a wear item worn out on a nearly new car is a defect, not wear: the maker's
        # wear-item adjustment covers it (a $527 brake job at 412 mi is never customer pay)
        pay = "warranty"
        rule = (f"wear-item adjustment: {round(odo):,} {unit}, {age_m} mo ≤ "
                f"{_WEAR_ADJUST_MONTHS} mo / {round(wear_cap):,} {unit}")
    elif age_m is not None and not wear and age_m <= _BASIC_WARRANTY_MONTHS:
        pay = "warranty"; rule = f"non-wear item, vehicle {age_m} mo ≤ {_BASIC_WARRANTY_MONTHS} mo basic warranty"
    else:
        pay = "customer_pay"
        rule = "wear item" if wear else (f"vehicle {age_m} mo, outside warranty" if age_m is not None else "no in-service date")
    return {"pay_type": pay, "rule": rule, "vehicle_age_months": age_m,
            "source_ref": f"coverage_rule:{'hv' if hv else 'basic'}_warranty (DEALER_Vehicles.delivered_at)"}


def build_lines(sj: dict[str, Any], adjustments: dict[str, dict[str, Any]] | None = None,
                env_prefix: str | None = None) -> list[dict[str, Any]]:
    """Priced lines for every red/yellow finding of a service journey.

    adjustments: {finding_ref: {labor_hours?, part_qty?, include?}} — advisor
    edits; every change is recorded in the line's `audit`."""
    sj_id = sj["service_journey_id"]
    market = sj.get("market_id") or "us"
    vehicle = ddb.get_item(_VEHICLES, {"vin": sj.get("vin")}, env_prefix) if sj.get("vin") else None
    findings = ddb.scan_filter(_INSPECTIONS, "service_journey_id", sj_id, env_prefix)
    actionable = sorted([f for f in findings if f.get("status") in ("red", "yellow")],
                        key=lambda f: ({"red": 0, "yellow": 1}.get(f.get("status"), 2),
                                       _URGENCY_RANK.get(f.get("urgency"), 3)))
    adjustments = adjustments or {}
    lines = []
    for f in actionable:
        ref = f.get("inspection_id")
        adj = adjustments.get(ref, {})
        if adj.get("include") is False:
            continue
        op_id, part = _op_for(f.get("item"))
        lab = _labor(op_id, market, env_prefix)
        hours = float(adj.get("labor_hours", lab["hours"]))
        rate = lab["rate"] or 0.0
        labor_amount = round(hours * rate, 2)
        parts = []
        if part:
            qty = int(adj.get("part_qty", part[2]))
            parts.append({"description": part[0], "qty": qty, "unit_price": part[1],
                          "amount": round(part[1] * qty, 2),
                          "source_ref": f"parts_catalog:{op_id}"})
        parts_amount = round(sum(p["amount"] for p in parts), 2)
        cov = line_coverage(vehicle, f.get("item"))
        total = round(labor_amount + parts_amount, 2)
        audit = [{"change": k, "value": v} for k, v in adj.items() if k != "include"]
        lines.append({
            "line_id": f"LN-{ref}",
            "finding_ref": ref, "item": f.get("item"), "finding_status": f.get("status"),
            "urgency": f.get("urgency"), "measurement": f.get("measurement"), "unit": f.get("unit"),
            "evidence_doc_id": f.get("evidence_doc_id"),
            "labor_op_id": op_id, "labor_op": lab["name"], "labor_hours": hours, "labor_rate": rate,
            "labor_amount": labor_amount, "labor_source_ref": lab["source_ref"],
            "parts": parts, "parts_amount": parts_amount,
            "coverage": cov, "customer_amount": 0.0 if cov["pay_type"] == "warranty" else total,
            "line_total": total,
            "price_source": "labor guide flat-rate × shop rate + parts catalog",
            "formula": f"{hours} h × {rate} + {parts_amount}",
            "decision": None, "decided_by": None, "decided_at": None, "decision_reason": None,
            "line_status": "pending", "audit": audit,
        })
    return lines


def _totals(lines: list[dict[str, Any]]) -> dict[str, float]:
    labor = round(sum(l["labor_amount"] for l in lines), 2)
    parts = round(sum(l["parts_amount"] for l in lines), 2)
    covered = round(sum(l["line_total"] for l in lines if l["coverage"]["pay_type"] == "warranty"), 2)
    total = round(labor + parts, 2)
    return {"labor_total": labor, "parts_total": parts, "total": total, "covered_amount": covered,
            "customer_total": round(total - covered, 2)}


def bundles(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """Good-better-best: good = safety/red, better = + yellow, best = everything."""
    good = [l for l in lines if l["finding_status"] == "red" or l.get("urgency") == "safety"]
    better = [l for l in lines if l["finding_status"] in ("red", "yellow")]
    best = lines
    def pack(ls):
        t = _totals(ls)
        return {"line_ids": [l["line_id"] for l in ls], "count": len(ls), **t}
    return {"good": pack(good), "better": pack(better), "best": pack(best),
            "source_ref": "A7.bundles (urgency tiers over priced lines)"}


def _latest_valuation(customer_id: str | None, env_prefix: str | None) -> dict[str, Any] | None:
    if not customer_id:
        return None
    vals = ddb.scan_filter(_VALUATIONS, "customer_id", customer_id, env_prefix)
    return max(vals, key=lambda v: v.get("valued_at") or "") if vals else None


def repair_vs_replace(sj: dict[str, Any], customer_total: float, env_prefix: str | None = None,
                      threshold_pct: float | None = None) -> dict[str, Any] | None:
    """Repair-vs-replace card when the estimate crosses `threshold_pct` of the
    vehicle's value (default the dealer-configured RVR_THRESHOLD_PCT; the Service
    Cockpit what-if passes its own threshold to re-count without changing config)."""
    # the value of the car being repaired — not the customer's latest valuation (after a
    # sale that is the car they traded in)
    from ..common import vehicles
    val = vehicles.valuation_for(sj.get("vin"), sj.get("customer_id"), env_prefix)
    if not val or not val.get("trade_value"):
        return None
    threshold = float(threshold_pct if threshold_pct is not None else RVR_THRESHOLD_PCT)
    value = float(val["trade_value"])
    pct = round(customer_total / value * 100, 1) if value else None
    return {"triggered": bool(pct is not None and pct >= threshold),
            "estimate_customer_total": customer_total, "vehicle_value": value,
            "equity": val.get("equity"), "pct_of_value": pct, "threshold_pct": threshold,
            "formula": f"{customer_total} / {value} × 100",
            "source_ref": f"DEALER_Valuations:{val.get('valuation_id')}"}


def _get_estimate(estimate_id: str, env_prefix: str | None) -> dict[str, Any]:
    est = ddb.get_item(_ESTIMATES, {"estimate_id": estimate_id}, env_prefix)
    if est is None:
        raise NotFound(f"estimate {estimate_id} not found")
    return dict(est)


def lines_for(est: dict[str, Any], env_prefix: str | None = None) -> tuple[list[dict[str, Any]], bool]:
    """(lines, persisted). Seeded estimates without lines get derived lines."""
    if est.get("lines"):
        lines = [dict(l) for l in est["lines"]]
        _overlay_portal_decisions(est, lines)
        return lines, True
    sj = ddb.get_item(_SJ, {"service_journey_id": est.get("service_journey_id")}, env_prefix)
    if not sj:
        return [], False
    lines = build_lines(sj, env_prefix=env_prefix)
    status = est.get("status")
    if status in ("approved", "declined"):
        for l in lines:
            l["decision"] = "approve" if status == "approved" else "decline"
            l["decided_by"] = est.get("decided_by")
    return lines, False


def estimate_view(estimate_id: str, env_prefix: str | None = None) -> dict[str, Any]:
    est = _get_estimate(estimate_id, env_prefix)
    sj = ddb.get_item(_SJ, {"service_journey_id": est.get("service_journey_id")}, env_prefix) or {}
    lines, persisted = lines_for(est, env_prefix)
    totals = _totals(lines) if lines else {k: est.get(k) for k in ("labor_total", "parts_total", "total")}
    approved = [l for l in lines if l.get("decision") == "approve"]
    presented_at = _parse(est.get("presented_at"))
    waiting_min = None
    if est.get("status") == "presented" and presented_at:
        waiting_min = int((_dt.datetime.now(_dt.timezone.utc) - presented_at).total_seconds() // 60)
    return {
        "estimate_id": estimate_id, "service_journey_id": est.get("service_journey_id"),
        "ro_id": est.get("ro_id"), "status": est.get("status"), "currency": est.get("currency"),
        "market_id": sj.get("market_id"), "customer_id": sj.get("customer_id"), "vin": sj.get("vin"),
        "stage": sj.get("stage"), "presented_at": est.get("presented_at"),
        "minutes_waiting": waiting_min, "lines": lines, "lines_persisted": persisted,
        **totals, "approved_total": _totals(approved)["total"] if approved else 0.0,
        "bundles": bundles(lines) if lines else None,
        "repair_vs_replace": repair_vs_replace(sj, totals.get("customer_total") or totals.get("total") or 0, env_prefix) if sj else None,
        "audit": est.get("audit") or [],
        "source_ref": est.get("source_ref"),
    }


def rebuild(estimate_id: str, adjustments: dict[str, dict[str, Any]], actor: str,
            env_prefix: str | None = None) -> dict[str, Any]:
    """Advisor edit: re-run pricing with adjustments, keep the audit trail."""
    est = _get_estimate(estimate_id, env_prefix)
    if est.get("status") in ("approved", "declined", "partially_approved"):
        raise InvalidDecision("estimate already decided; build a new estimate to change lines")
    sj = ddb.get_item(_SJ, {"service_journey_id": est.get("service_journey_id")}, env_prefix)
    lines = build_lines(sj, adjustments, env_prefix)
    est.update(_totals(lines))
    est["lines"] = lines
    est["line_count"] = len(lines)
    est["updated_at"] = _now()
    est["audit"] = list(est.get("audit") or []) + [{"by": actor, "at": est["updated_at"], "adjustments": adjustments}]
    ddb.put_item(_ESTIMATES, est, env_prefix)
    return estimate_view(estimate_id, env_prefix)


# prioritize_findings tiers (§5.20.4): safety work first, then warranty-covered
# work (no customer cost), then maintenance. Only the ORDER changes — never a
# price, a coverage decision, or a line's own recorded urgency/status.
_PRIORITY_TIERS = ("safety", "warranty", "maintenance")


def _priority_tier(line: dict[str, Any]) -> str:
    if line.get("urgency") == "safety" or line.get("finding_status") == "red":
        return "safety"
    if (line.get("coverage") or {}).get("pay_type") == "warranty":
        return "warranty"
    return "maintenance"


def _priority_key(line: dict[str, Any]) -> tuple:
    return (_PRIORITY_TIERS.index(_priority_tier(line)),
            _URGENCY_RANK.get(line.get("urgency"), 3),
            {"red": 0, "yellow": 1}.get(line.get("finding_status"), 2),
            line.get("line_id") or "")


def prioritize_findings(estimate_id: str, actor: str, env_prefix: str | None = None) -> dict[str, Any]:
    """Reorder an estimate's lines by urgency (spec §5.20.4 prioritize_findings).

    Safety work first, then warranty-covered work, then maintenance — using the
    finding status / severity / coverage each line already carries. Persists the
    new order on the estimate row with an audit entry and returns the estimate."""
    est = persist_lines(estimate_id, env_prefix)
    lines = [dict(l) for l in est.get("lines") or []]
    if not lines:
        raise NotFound(f"estimate {estimate_id} has no lines to prioritize")
    ordered = sorted(lines, key=_priority_key)
    for i, l in enumerate(ordered):
        l["priority_rank"] = i + 1
        l["priority_tier"] = _priority_tier(l)
    changed = [l["line_id"] for l in ordered] != [l["line_id"] for l in lines]
    est["lines"] = ordered
    est["updated_at"] = _now()
    est["line_order"] = [l["line_id"] for l in ordered]
    est["prioritized_at"] = est["updated_at"]
    est["audit"] = list(est.get("audit") or []) + [
        {"by": actor, "at": est["updated_at"], "action": "prioritize_findings",
         "order": est["line_order"], "reordered": changed,
         "basis": "safety > warranty > maintenance (finding status/severity + coverage)"}]
    ddb.put_item(_ESTIMATES, est, env_prefix)
    return {**estimate_view(estimate_id, env_prefix), "reordered": changed,
            "line_order": est["line_order"], "prioritized_at": est["prioritized_at"],
            "basis": "safety > warranty > maintenance (finding status/severity + coverage)",
            "source_ref": "A7.prioritize_findings (urgency tiers over persisted lines)"}


def persist_lines(estimate_id: str, env_prefix: str | None = None) -> dict[str, Any]:
    """Freeze derived lines onto the estimate row (done at build and before decisions)."""
    est = _get_estimate(estimate_id, env_prefix)
    lines, persisted = lines_for(est, env_prefix)
    if not persisted or lines != est.get("lines"):
        est["lines"] = lines
        est["line_count"] = len(lines)
        est.update(_totals(lines))
        ddb.put_item(_ESTIMATES, est, env_prefix)
    return est


def _overlay_portal_decisions(est: dict[str, Any], lines: list[dict[str, Any]]) -> None:
    """Apply decisions recorded by the customer portal (line_decisions /
    approved_line_ids / declined_line_ids) so both writers agree on each line."""
    recorded = est.get("line_decisions") or {}
    approved = set(est.get("approved_line_ids") or [])
    declined = set(est.get("declined_line_ids") or [])
    for l in lines:
        d = recorded.get(l["line_id"]) or {}
        dec = d.get("decision") or ("approved" if l["line_id"] in approved else "declined" if l["line_id"] in declined else None)
        if dec and not l.get("decision"):
            l["decision"] = "approve" if dec.startswith("approve") else "decline"
            l["decided_by"] = d.get("decided_by") or "customer"
            l["decided_at"] = d.get("decided_at")
            l["decision_reason"] = d.get("reason")
            if l["decision"] == "approve" and l.get("line_status") in (None, "pending"):
                l["line_status"] = "dispatched"


def _sync_portal_fields(est: dict[str, Any]) -> None:
    lines = est.get("lines") or []
    ld = dict(est.get("line_decisions") or {})
    for l in lines:
        if l.get("decision"):
            ld[l["line_id"]] = {**(ld.get(l["line_id"]) or {}),   # keep audit extras (e.g. presented snapshot)
                                "decision": "approved" if l["decision"] == "approve" else "declined",
                                "decided_by": l.get("decided_by"), "decided_at": l.get("decided_at"),
                                "reason": l.get("decision_reason")}
    est["line_decisions"] = ld
    est["approved_line_ids"] = [l["line_id"] for l in lines if l.get("decision") == "approve"]
    est["declined_line_ids"] = [l["line_id"] for l in lines if l.get("decision") == "decline"]
    est["approved_total"] = round(sum(l["customer_amount"] for l in lines if l.get("decision") == "approve"), 2)


def decide_lines(estimate_id: str, decisions: list[dict[str, Any]], actor: str,
                 decided_by: str, env_prefix: str | None = None) -> dict[str, Any]:
    """Record per-line approve/decline (acc #27).

    decided_by: 'customer' | 'advisor'. Advisor-recorded (verbal) decisions need
    a reason. Approved lines are dispatched (exactly those lines); declined
    lines go to DEALER_DeclinedWork with their evidence snapshot. The estimate
    status becomes approved / partially_approved / declined once every line has
    a decision, so the APPROVE gate (validate_service_handoff) can clear."""
    if decided_by not in ("customer", "advisor"):
        raise InvalidDecision("decided_by must be customer or advisor")
    if not decisions:
        raise InvalidDecision("no decisions supplied")
    est = persist_lines(estimate_id, env_prefix)
    lines = {l["line_id"]: dict(l) for l in est.get("lines") or []}
    sj = ddb.get_item(_SJ, {"service_journey_id": est.get("service_journey_id")}, env_prefix) or {}
    ts = _now()
    changed = []
    for d in decisions:
        lid, dec = d.get("line_id"), (d.get("decision") or "").lower()
        if lid not in lines:
            raise InvalidDecision(f"line {lid} is not on estimate {estimate_id}")
        if dec in ("approved",):
            dec = "approve"
        if dec in ("declined",):
            dec = "decline"
        if dec not in ("approve", "decline"):
            raise InvalidDecision(f"decision for {lid} must be approve or decline")
        if decided_by == "advisor" and not (d.get("reason") or "").strip():
            raise InvalidDecision("an advisor-recorded decision needs a reason")
        line = lines[lid]
        if line.get("decision"):
            continue   # decisions are final; idempotent re-submit
        line.update({"decision": dec, "decided_by": decided_by, "decided_by_user": actor,
                     "decided_at": ts, "decision_reason": d.get("reason")})
        if dec == "approve":
            line["line_status"] = "dispatched"
        changed.append(line)

    est["lines"] = [lines[l["line_id"]] for l in est["lines"]]
    _sync_portal_fields(est)
    decided = [l for l in est["lines"] if l.get("decision")]
    approved = [l for l in est["lines"] if l.get("decision") == "approve"]
    if len(decided) == len(est["lines"]) and est["lines"]:
        est["status"] = "approved" if len(approved) == len(decided) else ("declined" if not approved else "partially_approved")
        est["decided_at"] = ts
        est["decided_by"] = decided_by
    est["updated_at"] = ts
    ddb.put_item(_ESTIMATES, est, env_prefix)

    sj_id = est.get("service_journey_id")
    declined_rows = []
    for line in changed:
        ev = svc_supervisor._event_row(sj_id, "estimate.line_decided", actor,
                                       f"{line['item']} {line['decision']} by {decided_by}",
                                       {"estimate_id": estimate_id, "line_id": line["line_id"],
                                        "decision": line["decision"], "reason": line.get("decision_reason")}, ts)
        ev["sk"] = f"{ts}#estimate.line_decided#{line['line_id']}"
        ddb.put_item(_EVENTS, ev, env_prefix)
        _bus.emit("estimate.line_decided", {
            "service_journey_id": sj_id, "estimate_id": estimate_id, "line_id": line["line_id"],
            "decision": "approved" if line["decision"] == "approve" else "declined",
            "decided_by": decided_by, "dealer_id": sj.get("dealer_id"), "customer_id": sj.get("customer_id")},
            actor=actor, resource_url=f"/r/estimate/{estimate_id}", env_prefix=env_prefix)
        if line["decision"] == "decline":
            item = {
                "declined_item_id": f"DW-{line['line_id']}", "line_id": line["line_id"],
                "dealer_id": sj.get("dealer_id"), "service_journey_id": sj_id, "estimate_id": estimate_id,
                "vin": sj.get("vin"), "item": line["item"], "amount": line["customer_amount"],
                "line_total": line.get("line_total"), "pay_type": (line.get("coverage") or {}).get("pay_type"),
                "currency": est.get("currency"),
                "evidence_snapshot": {"finding_ref": line["finding_ref"], "status": line["finding_status"],
                                      "measurement": line.get("measurement"), "unit": line.get("unit"),
                                      "evidence_doc_id": line.get("evidence_doc_id")},
                "declined_at": ts, "decided_by": decided_by, "reason": line.get("decision_reason"),
                "status": "open", "follow_up_cadence_days": 30, "market_id": sj.get("market_id"),
                "source_ref": f"A7.decide_lines:{estimate_id}",
            }
            from .followup import add_declined_items
            add_declined_items(sj.get("customer_id"), [item], env_prefix)
            declined_rows.append(item["declined_item_id"])
            _bus.emit("service.declined_work.recorded", {
                "service_journey_id": sj_id, "customer_id": sj.get("customer_id"),
                "declined_id": item["declined_item_id"], "lines": 1, "dealer_id": sj.get("dealer_id")},
                actor=actor, resource_url=f"/r/service_journey/{sj_id}", env_prefix=env_prefix)
    return {**estimate_view(estimate_id, env_prefix), "changed": [l["line_id"] for l in changed],
            "declined_work": declined_rows,
            "dispatched": [l["line_id"] for l in changed if l["decision"] == "approve"]}
