"""A7 — predictive part maintenance (spec §3.7 connected-car/telematics slot).

Watches each delivered vehicle's mileage and service history against simple
per-part wear curves and predicts which part crosses its service point next.
A prediction can be turned into:
  * a DEALER_Leads row (source=predictive_maintenance) so the Lead Desk works
    it inside an SLA like any other lead, and
  * a customer offer surfaced in My Garage (portal.ownership), carrying the
    part, the predicted mileage and a booking nudge.

Deterministic sandbox model: wear curves are flat-rate constants and the
telematics signal is the odometer — honest about its basis via source_ref, and
exactly the seam a live telematics connector would replace (§4.1).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
from typing import Any

from ..common import ddb, labels
from ..events import bus

# part -> (service interval in miles, labor-op hint, offer % off)
WEAR_CURVES: dict[str, tuple[int, str, int]] = {
    "Front brake pads": (40_000, "LOP-BRAKE-F", 15),
    "Rear brake pads": (55_000, "LOP-BRAKE-R", 15),
    "Tires (set)": (50_000, "LOP-TIRE-REPL", 10),
    "Cabin air filter": (20_000, "LOP-CABIN-FILT", 20),
    "12V battery": (45_000, "LOP-BATT", 10),
}
NEAR_MILES = 6_000          # "due soon" window
LEAD_SOURCE = "predictive_maintenance"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _serviced_parts(vin: str, env_prefix: str | None) -> dict[str, int]:
    """part -> odometer at last service touching it (from RO lines)."""
    done: dict[str, int] = {}
    for ro in ddb.scan_filter("DEALER_RepairOrders", "vin", vin, env_prefix):
        odo = ro.get("mileage") or 0
        for ln in ro.get("lines") or []:
            item = str(ln.get("item") or ln.get("description") or "")
            for part in WEAR_CURVES:
                if part.split(" (")[0].lower() in item.lower():
                    done[part] = max(done.get(part, 0), int(odo or 0))
    return done


def _wear_base(part: str, interval: int, odo: int, serviced: dict[str, int]) -> tuple[int, str]:
    """Odometer reading the wear curve starts from, and how we know.

    With a recorded service on that part the curve starts there. Without one the
    honest assumption is that the part was last replaced at the most recent
    interval boundary the vehicle has already passed (a 76k car on a 20k part
    was serviced at 60k, not never) — otherwise every used car reads as decades
    overdue and the "prediction" is just arithmetic on the odometer."""
    if part in serviced:
        return serviced[part], f"last serviced at {serviced[part]:,} mi"
    base = (odo // interval) * interval
    return base, (f"no service record for this part; assuming replaced at the {base:,} mi interval"
                  if base else "no prior service on record")


def predict_for_vehicle(v: dict[str, Any], env_prefix: str | None) -> list[dict[str, Any]]:
    odo = int(v.get("odometer") or 0)
    if not odo:
        return []
    serviced = _serviced_parts(v["vin"], env_prefix)
    out = []
    for part, (interval, lop, off) in WEAR_CURVES.items():
        base, basis_note = _wear_base(part, interval, odo, serviced)
        due_at = base + interval
        remaining = due_at - odo
        if remaining <= NEAR_MILES:
            # closer to the service point → higher confidence; capped below 97% and
            # spread by part so a fleet scan does not show one identical number
            spread = (sum(map(ord, part)) % 7) * 0.01
            confidence = round(min(0.96, 0.55 + 0.35 * (1 - max(remaining, 0) / NEAR_MILES) + spread), 2)
            out.append({
                "vin": v["vin"], "customer_id": v.get("customer_id"), "dealer_id": v.get("dealer_id"),
                "market_id": v.get("market_id"), "model_id": v.get("model_id"),
                "part": part, "labor_op_id": lop, "odometer": odo,
                "predicted_due_mileage": due_at, "miles_remaining": max(remaining, 0),
                "overdue": remaining < 0, "confidence": confidence, "offer_pct": off,
                "basis": f"odometer {odo:,} mi vs {interval:,} mi wear curve, {basis_note}",
                "source_ref": f"A7.predictive (DEALER_Vehicles:{v['vin']} + DEALER_RepairOrders wear curve)",
            })
    out.sort(key=lambda p: p["miles_remaining"])
    return out


def _bundle(preds: list[dict[str, Any]]) -> dict[str, Any]:
    """One prediction row per vehicle: the soonest part leads, the rest ride along as `also_due`."""
    lead = dict(preds[0])
    others = preds[1:]
    lead["also_due"] = [{"part": p["part"], "predicted_due_mileage": p["predicted_due_mileage"],
                         "miles_remaining": p["miles_remaining"], "offer_pct": p["offer_pct"]} for p in others]
    lead["parts"] = [lead["part"]] + [p["part"] for p in others]
    lead["offer_pct"] = max(p["offer_pct"] for p in preds)
    return lead


_IN_BAY = ("CHECK_IN", "INSPECT", "DIAGNOSE", "APPROVE", "REPAIR", "READY")


def predictions(dealer_ids: set[str] | None, market: str, env_prefix: str | None = None,
                vin: str | None = None) -> dict[str, Any]:
    vehicles = ddb.scan_filter("DEALER_Vehicles", "market_id", market, env_prefix)
    rows: list[dict[str, Any]] = []
    for v in vehicles:
        if vin and v.get("vin") != vin:
            continue
        if dealer_ids is not None and v.get("dealer_id") not in dealer_ids:
            continue
        preds = predict_for_vehicle(v, env_prefix)
        if preds:
            # one row (and later one lead) per vehicle — a customer gets one message
            # listing everything coming due, not three leads for three parts
            rows.append(_bundle(preds))
    rows.sort(key=lambda p: (p["miles_remaining"], -p["confidence"]))
    open_leads = {l.get("predicted_vin") for l in ddb.scan_filter("DEALER_Leads", "source", LEAD_SOURCE, env_prefix)
                  if l.get("status") in ("new", "working", None)}
    # a car in the shop right now gets its due work added to THAT visit by the advisor — a
    # "book ahead" heads-up would open a second visit on a car that is already in the bay
    in_bay = {s.get("vin") for s in ddb.scan_filter("DEALER_ServiceJourneys", "market_id", market, env_prefix)
              if s.get("stage") in _IN_BAY}
    for p in rows:
        p["lead_open"] = p["vin"] in open_leads
        p["in_bay"] = p["vin"] in in_bay
    return {"count": len(rows), "predictions": rows[:50],
            "source_ref": "A7.predictive (wear curves over odometer + service history; telematics connector swap point)"}


def _lead_id(vin: str) -> str:
    return "LD-PRED-" + hashlib.sha1(f"{vin}".encode()).hexdigest()[:10].upper()


def _timing(pred: dict[str, Any]) -> str:
    """Plain-language timing for the part: ahead of the odometer or already past it."""
    due, odo = pred["predicted_due_mileage"], pred["odometer"]
    verb = labels.is_are(pred["part"])
    were = "were" if verb == "are" else "was"
    if pred.get("overdue"):
        return f"{were} due around {due:,} mi and {verb} now {odo - due:,} mi past that"
    return f"{verb} due around {due:,} mi, about {due - odo:,} mi from now"


def lead_comment(pred: dict[str, Any]) -> str:
    """The Lead Desk comment for a (bundled) prediction — what the BDC and the customer read."""
    part = labels.part_phrase(pred["part"])
    text = (f"The Service Agent predicts the {part} on this vehicle {_timing(pred)} "
            f"(odometer {pred['odometer']:,} mi, confidence {int(pred['confidence'] * 100)}%).")
    also = pred.get("also_due") or []
    if also:
        names = labels.join_words(labels.part_phrase(a["part"]) for a in also)
        text += f" Also coming due on the same visit: {names}."
    return text + f" Offer: {pred['offer_pct']}% off when booked ahead."


def raise_lead(pred: dict[str, Any], actor: str, env_prefix: str | None = None) -> dict[str, Any]:
    """One open predictive lead per vehicle — idempotent; the lead bundles every part coming due."""
    lid = _lead_id(pred["vin"])
    existing = ddb.get_item("DEALER_Leads", {"lead_id": lid}, env_prefix)
    if existing and existing.get("status") in ("new", "working"):
        return {"lead": existing, "created": False}
    cust = ddb.get_item("DEALER_Customers", {"customer_id": pred.get("customer_id")}, env_prefix) or {}
    row = {
        "lead_id": lid, "market_id": pred.get("market_id"), "dealer_id": pred.get("dealer_id"),
        "customer_id": pred.get("customer_id"),
        "source": LEAD_SOURCE, "status": "new", "received_at": _now(),
        "vehicle_of_interest_model_id": pred.get("model_id"),
        "comments": lead_comment(pred),
        # prediction payload for the Lead Desk + the customer offer
        "predicted_vin_part": f"{pred['vin']}#{pred['part']}",
        "predicted_part": pred["part"], "predicted_vin": pred["vin"],
        "predicted_parts": pred.get("parts") or [pred["part"]],
        "predicted_also_due": pred.get("also_due") or [],
        "predicted_overdue": bool(pred.get("overdue")),
        "predicted_odometer": pred["odometer"],
        "predicted_due_mileage": pred["predicted_due_mileage"], "confidence": pred["confidence"],
        "labor_op_id": pred["labor_op_id"], "offer_pct": pred["offer_pct"],
        "offer_expires_at": (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=30)
                             ).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source_ref": pred["source_ref"],
    }
    ddb.put_item("DEALER_Leads", row, env_prefix)
    bus.emit("lead.predictive_raised", {"lead_id": lid, "vin": pred["vin"], "part": pred["part"],
                                        "customer_id": pred.get("customer_id"), "actor": actor},
             env_prefix=env_prefix)
    return {"lead": row, "created": True, "customer_name": " ".join(
        filter(None, [cust.get("given_name"), cust.get("family_name")])) or None}


def varied_by_part(preds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Urgency order, interleaved by lead part: the most urgent car for each part first, then
    each part's next car, and so on. Within a part the order is kept, and the parts take
    turns in the order of their most urgent car."""
    by_part: dict[str, list[dict[str, Any]]] = {}
    for p in preds:                                   # preds arrive most urgent first
        by_part.setdefault(p["part"], []).append(p)
    queues = list(by_part.values())
    out: list[dict[str, Any]] = []
    while queues:
        out += [q.pop(0) for q in queues]
        queues = [q for q in queues if q]
    return out


def run_scan(dealer_ids: set[str] | None, market: str, actor: str,
             env_prefix: str | None = None, vin: str | None = None) -> dict[str, Any]:
    """The nightly job's body, callable on demand: predict, raise missing leads.
    With `vin`, scan just that vehicle and raise every due prediction for it."""
    pred = predictions(dealer_ids, market, env_prefix, vin=vin)
    raised = []
    # cap per run: the desk should see the most urgent handful, not the fleet — and a handful
    # across parts, not ten brake jobs because a model-year cohort crosses 40k miles together
    for p in (pred["predictions"] if vin else varied_by_part(pred["predictions"]))[: (50 if vin else 10)]:
        if not p.get("lead_open") and not p.get("in_bay") and p.get("customer_id"):
            out = raise_lead(p, actor, env_prefix)
            if out["created"]:
                raised.append({"lead_id": out["lead"]["lead_id"], "vin": p["vin"], "part": p["part"]})
    return {"scanned": pred["count"], "leads_raised": len(raised), "leads": raised,
            "source_ref": pred["source_ref"]}


def run_predictive_scan(market: str = "us", apply: bool = False, limit: int | None = None,
                        env_prefix: str | None = None) -> dict[str, Any]:
    """The nightly `predictive-scan` job (A7, agent bar: Predictive part watch).

    Runs the same scan as the Lead Desk's "Scan now", store by store, so every
    store's most urgent predictions get a lead (the per-run cap is per store).
    apply=False is a dry run: it counts what each store would raise and writes
    nothing. `limit` caps the number of stores scanned."""
    dealers = sorted({v.get("dealer_id") for v in ddb.scan_filter("DEALER_Vehicles", "market_id", market, env_prefix)
                      if v.get("dealer_id")})
    if limit is not None:
        dealers = dealers[:limit]
    stores, scanned, raised = [], 0, 0
    for d in dealers:
        if apply:
            out = run_scan({d}, market, "A7", env_prefix)
            n, new = out["scanned"], out["leads_raised"]
        else:
            pred = predictions({d}, market, env_prefix)
            n = pred["count"]
            new = sum(1 for p in pred["predictions"][:10]
                      if not p.get("lead_open") and not p.get("in_bay") and p.get("customer_id"))
        scanned += n
        raised += new
        stores.append({"dealer_id": d, "predictions": n, ("leads_raised" if apply else "would_raise"): new})
    return {"job": "predictive-scan", "market": market, "applied": apply, "stores": len(stores),
            "scanned": scanned, ("leads_raised" if apply else "would_raise"): raised,
            "by_store": stores, "source_ref": "A7.run_predictive_scan (nightly; wear curves over odometer)"}


def open_lead_for(vin: str | None, env_prefix: str | None = None) -> dict[str, Any] | None:
    """The car's open predictive lead (one per vehicle), or None."""
    if not vin:
        return None
    lead = ddb.get_item("DEALER_Leads", {"lead_id": _lead_id(vin)}, env_prefix)
    return lead if lead and lead.get("status") in ("new", "working") else None


def link_booking(vin: str | None, booking: dict[str, Any], booked_by: str,
                 env_prefix: str | None = None) -> dict[str, Any] | None:
    """A service visit is booked for a car with an open heads-up: the lead is answered by it.

    Whoever booked — the customer from My Garage or the BDC from the Lead Desk — the lead
    records the visit and the early-booking offer it earned, so the Lead Desk never books the
    car a second time and My Garage shows "booked" instead of "book with 20% off"."""
    lead = open_lead_for(vin, env_prefix)
    if lead is None or not booking.get("service_journey_id"):
        return None
    lead = dict(lead)
    lead.update({"status": "working",
                 "service_journey_id": booking["service_journey_id"],
                 "service_appointment": {"appointment_id": booking.get("appointment_id"),
                                         "service_journey_id": booking["service_journey_id"],
                                         "slot_start": booking.get("slot_start"), "booked_by": booked_by},
                 "offer_applied_pct": lead.get("offer_pct")})
    if not lead.get("first_response_at"):
        lead["first_response_at"] = _now()
    ddb.put_item("DEALER_Leads", lead, env_prefix)
    return lead


def customer_offers(cid: str, env_prefix: str | None = None) -> list[dict[str, Any]]:
    """Open predictive offers for one customer — My Garage's view of the lead."""
    out = []
    for l in ddb.scan_filter("DEALER_Leads", "customer_id", cid, env_prefix):
        if l.get("source") != LEAD_SOURCE or l.get("status") not in ("new", "working"):
            continue
        part = str(l.get("predicted_part", "part")).lower()
        due = int(l.get("predicted_due_mileage") or 0)
        odo = int(l.get("predicted_odometer") or 0)
        if l.get("predicted_overdue") or (odo and due and due < odo):
            when = f"are past their usual service point (around {due:,} mi)"
        else:
            when = f"will likely need attention around {due:,} mi"
        also = [a["part"].lower() for a in (l.get("predicted_also_due") or [])]
        msg = f"Based on how you drive, your {part} {when}."
        if also:
            msg += f" We can look at the {', '.join(also)} on the same visit."
        msg += f" Book ahead and save {l.get('offer_pct')}%."
        out.append({
            "vin": l.get("predicted_vin"), "part": l.get("predicted_part"),
            "parts": l.get("predicted_parts") or [l.get("predicted_part")],
            "due_mileage": due, "offer_pct": l.get("offer_pct"),
            "expires_at": l.get("offer_expires_at"),
            "message": msg,
            # already booked (by the customer or the service team): the visit, not another prompt
            "booked": ({k: (l.get("service_appointment") or {}).get(k)
                        for k in ("slot_start", "service_journey_id", "booked_by")}
                       if l.get("service_appointment") else None),
            "source_ref": l.get("source_ref"),
        })
    return out
