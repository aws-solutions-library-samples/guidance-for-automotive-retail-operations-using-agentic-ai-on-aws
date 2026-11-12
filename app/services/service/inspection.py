"""A7 — record_inspection + build_estimate + prioritize_findings (spec §3.7).

The middle of the service journey (INSPECT -> DIAGNOSE -> APPROVE content):

  record_inspection(sj_id, findings)   -> write MPI findings (green/yellow/red)
  prioritize_findings(sj_id)           -> red/yellow first, with recommended action
  build_estimate(sj_id)                -> priced estimate from red/yellow findings,
                                          each line with labor op + parts + PRICE SOURCE

Grounding / §2.1.1 rule: an estimate line is only created for a red/yellow
finding, and every line carries a labor op, a parts reference, and a price
source_ref — no line is priced without a source. The estimate total is the sum
of its sourced lines; nothing is invented. The estimate this writes is exactly
what the APPROVE gate (supervisor.validate_service_handoff) requires: a priced
estimate with a source_ref, pending a recorded customer decision.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from ..common import ddb
from ..events import bus as _bus
from . import supervisor as svc_supervisor

_SJ = "DEALER_ServiceJourneys"
_INSPECTIONS = "DEALER_Inspections"
_ESTIMATES = "DEALER_ServiceEstimates"
_PARTS = "DEALER_Parts"
_RO = "DEALER_RepairOrders"
_EVENTS = "DEALER_ServiceJourneyEvents"

_STATUSES = ("green", "yellow", "red")
_URGENCY_RANK = {"safety": 0, "soon": 1, "monitor": 2, "none": 3}

# Standard labor-op catalog for common findings (flat-rate hours + a labor rate).
# Transparent, guidebook-anchored; production reads the OEM labor-time guide.
_LABOR_RATE = {"USD": 175.0, "EUR": 160.0}
_LABOR_OPS = {
    "brakes": {"op": "Brake pad/rotor service", "hours": 1.8, "part": ("Brake pad set", 120.0, 1)},
    "tires": {"op": "Tire replacement", "hours": 0.8, "part": ("Tire", 190.0, 4)},
    "battery": {"op": "Battery replacement", "hours": 0.5, "part": ("Battery", 240.0, 1)},
    "fluid": {"op": "Fluid service", "hours": 0.6, "part": ("Fluid/filter kit", 55.0, 1)},
    "wiper": {"op": "Wiper replacement", "hours": 0.2, "part": ("Wiper blade", 28.0, 2)},
    "filter": {"op": "Filter replacement", "hours": 0.4, "part": ("Air/cabin filter", 42.0, 1)},
}
_DEFAULT_OP = {"op": "Inspect & repair", "hours": 1.0, "part": ("Service part", 80.0, 1)}


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


class NotFound(Exception):
    pass


class MissingEvidence(ValueError):
    """A yellow/red MPI item was submitted without photo/video/voice evidence (acc #28)."""

    def __init__(self, items: list[str]):
        self.items = items
        super().__init__(f"evidence required for yellow/red items: {', '.join(items)}")


def _ro_for_sj(sj_id: str, env_prefix: str | None) -> dict[str, Any] | None:
    ros = ddb.scan_filter(_RO, "service_journey_id", sj_id, env_prefix)
    return ros[0] if ros else None


def _labor_op_for(item: str) -> dict[str, Any]:
    key = (item or "").lower()
    for k, op in _LABOR_OPS.items():
        if k in key:
            return op
    return _DEFAULT_OP


def record_inspection(sj_id: str, findings: list[dict[str, Any]], actor: str = "A7",
                      env_prefix: str | None = None, require_evidence: bool = False,
                      template_id: str | None = None) -> dict[str, Any]:
    """Record MPI findings for a service journey (spec §3.7).

    Each finding: {item, status(green|yellow|red), urgency?, measurement?, unit?}.
    Writes one DEALER_Inspections row per finding + a service.inspection event."""
    sj = ddb.get_item(_SJ, {"service_journey_id": sj_id}, env_prefix)
    if sj is None:
        raise NotFound(f"service journey {sj_id} not found")
    if require_evidence:
        lacking = [str(f.get("item")) for f in findings
                   if str(f.get("status", "")).lower() in ("yellow", "red")
                   and not (f.get("evidence_doc_id") or f.get("evidence"))]
        if lacking:
            raise MissingEvidence(lacking)
    ro = _ro_for_sj(sj_id, env_prefix)
    ro_id = (ro or {}).get("ro_id")

    written = []
    ts = _now()
    # A visit has one current reading per item. Re-inspecting an item replaces the row on
    # file for it (same inspection_id; PutItem overwrites), so the board, the estimate and
    # the customer never see "Rear brakes" twice. The service role writes with Put only.
    by_item = {str(r.get("item") or "").strip().lower(): r["inspection_id"]
               for r in ddb.scan_filter(_INSPECTIONS, "service_journey_id", sj_id, env_prefix)
               if r.get("inspection_id")}
    taken: set[str] = set()
    for i, f in enumerate(findings):
        status = str(f.get("status", "green")).lower()
        if status not in _STATUSES:
            status = "green"
        key = str(f.get("item") or "").strip().lower()
        prior = by_item.get(key) if key else None
        if prior and prior not in taken:
            ins_id = prior
        else:
            # a new item: an id no other item's row uses (never overwrite a different finding)
            n = i
            while f"INS-{sj_id}-{n:02d}" in taken or f"INS-{sj_id}-{n:02d}" in by_item.values():
                n += 1
            ins_id = f"INS-{sj_id}-{n:02d}"
        taken.add(ins_id)
        row = {
            "inspection_id": ins_id, "service_journey_id": sj_id, "ro_id": ro_id,
            "item": f.get("item"), "status": status,
            "urgency": f.get("urgency") or ("safety" if status == "red" else "monitor" if status == "yellow" else "none"),
            "measurement": _num(f.get("measurement")), "unit": f.get("unit"),
            "evidence_doc_id": f.get("evidence_doc_id") or ((f.get("evidence") or [None])[0]),
            "evidence": f.get("evidence") or ([f["evidence_doc_id"]] if f.get("evidence_doc_id") else []),
            "note": f.get("note"), "dtc_codes": f.get("dtc_codes") or [],
            "technician_id": actor, "template_id": template_id, "recorded_at": ts,
            "source_ref": "A7.record_inspection (MPI/VSA)",
        }
        ddb.put_item(_INSPECTIONS, row, env_prefix)
        written.append({"inspection_id": ins_id, "item": row["item"], "status": status})

    ev = svc_supervisor._event_row(sj_id, "inspection.submitted", actor,
                                   f"{len(written)} findings recorded", {"count": len(written)}, ts)
    ddb.put_item(_EVENTS, ev, env_prefix)
    _bus.emit("inspection.submitted", {
        "service_journey_id": sj_id, "ro_id": ro_id, "count": len(written),
        "red": sum(1 for w in written if w["status"] == "red"),
        "yellow": sum(1 for w in written if w["status"] == "yellow")},
        actor=actor, resource_url=f"/r/service_journey/{sj_id}", env_prefix=env_prefix)
    return {"service_journey_id": sj_id, "ro_id": ro_id, "recorded": written,
            "counts": {s: sum(1 for w in written if w["status"] == s) for s in _STATUSES}}


def prioritize_findings(sj_id: str, env_prefix: str | None = None) -> dict[str, Any]:
    """Order the journey's findings by risk (red+safety first) for the advisor."""
    insp = ddb.scan_filter(_INSPECTIONS, "service_journey_id", sj_id, env_prefix)
    def _rank(f):
        st = {"red": 0, "yellow": 1, "green": 2}.get(f.get("status"), 3)
        ur = _URGENCY_RANK.get(f.get("urgency"), 3)
        return (st, ur)
    ordered = sorted(insp, key=_rank)
    return {
        "service_journey_id": sj_id,
        "findings": [{"item": f.get("item"), "status": f.get("status"),
                      "urgency": f.get("urgency"), "measurement": f.get("measurement"),
                      "unit": f.get("unit")} for f in ordered],
        "actionable": [f.get("item") for f in ordered if f.get("status") in ("red", "yellow")],
        "source_ref": "A7.prioritize_findings",
    }


def build_estimate(sj_id: str, actor: str = "A7", env_prefix: str | None = None,
                   adjustments: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Turn red/yellow findings into a priced, SOURCED estimate (spec §2.1.1).

    One line per actionable finding, each with a labor op (flat-rate hours ×
    labor rate) + a parts line (written to DEALER_Parts) + a price source_ref.
    Writes the DEALER_ServiceEstimates row (status=pending) the APPROVE gate
    needs. No line is priced without a source."""
    sj = ddb.get_item(_SJ, {"service_journey_id": sj_id}, env_prefix)
    if sj is None:
        raise NotFound(f"service journey {sj_id} not found")
    ro = _ro_for_sj(sj_id, env_prefix)
    ro_id = (ro or {}).get("ro_id") or f"RO-{sj_id}"
    currency = (ro or {}).get("currency") or ("EUR" if (sj.get("market_id") == "de") else "USD")

    from . import estimates as _est
    lines = _est.build_lines(sj, adjustments, env_prefix)
    for i, ln in enumerate(lines):
        for j, part in enumerate(ln["parts"]):
            # write the parts line on the RO (grounds the price; parts desk reads it)
            part_id = f"PRT-{sj_id}-{i:02d}{j}"
            part["part_id"] = part_id
            ddb.put_item(_PARTS, {
                "part_id": part_id, "ro_id": ro_id, "part_number": f"OP-{ln['labor_op_id'][4:12]}",
                "description": part["description"], "qty": part["qty"], "unit_price": part["unit_price"],
                "on_hand": True, "currency": currency, "line_id": ln["line_id"],
                "source_ref": part["source_ref"],
            }, env_prefix)
        # legacy fields kept for existing clients
        ln["part"] = ln["parts"][0] if ln["parts"] else None
        ln["line_total"] = ln["line_total"]
    totals = _est._totals(lines)
    labor_total, parts_total, total = totals["labor_total"], totals["parts_total"], totals["total"]
    est_id = f"EST-{sj_id}"
    ts = _now()
    ddb.put_item(_ESTIMATES, {
        "estimate_id": est_id, "service_journey_id": sj_id, "ro_id": ro_id,
        **totals, "currency": currency, "status": "pending", "created_at": ts, "updated_at": ts,
        "line_count": len(lines), "lines": lines,
        "source_ref": "A7.build_estimate (labor guide flat-rate + parts catalog)",
    }, env_prefix)

    ev = svc_supervisor._event_row(sj_id, "estimate.built", actor,
                                   f"estimate {est_id}: {total} {currency} over {len(lines)} lines",
                                   {"estimate_id": est_id, "total": total}, ts)
    ddb.put_item(_EVENTS, ev, env_prefix)

    return {
        "service_journey_id": sj_id, "estimate_id": est_id, "ro_id": ro_id,
        "currency": currency, "lines": lines,
        "labor_total": labor_total, "parts_total": parts_total,
        "total": total, "status": "pending", "covered_amount": totals["covered_amount"],
        "customer_total": totals["customer_total"],
        "all_lines_sourced": all(ln.get("price_source") and ln.get("labor_source_ref") for ln in lines),
        "source_ref": "A7.build_estimate",
    }
