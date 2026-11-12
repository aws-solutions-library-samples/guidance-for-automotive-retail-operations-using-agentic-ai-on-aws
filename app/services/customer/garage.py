"""L3 — My Garage read model (Customer Web, spec §5.12).

The buyer/owner-facing view. Assembles ONLY a customer-safe subset of their own
data: vehicles, purchase-journey status (stage + summary, no staff internals),
service history (RO number/status/total, no labor/parts margin breakdown),
documents (kind only, not storage URIs), and communication preferences.

Safety is enforced by explicit field WHITELISTS — this module never spreads a
raw DynamoDB row into the response, so staff-only fields (owner assignment,
lead/health scores, margins, internal recommendations, document URIs, data
classification) can never leak to a customer surface.

  build_garage(customer_id) -> customer-safe My Garage payload.
"""
from __future__ import annotations

from typing import Any

from ..common import ddb
from ..common import vehicles as vehicle_names

# Purchase stages a customer may see (same labels; a customer sees status, not the
# internal machinery).
_PURCHASE_STAGES = ["DISCOVER", "CONFIGURE", "QUALIFY", "FINANCE",
                    "TRADE_IN", "AGREE", "DELIVER", "OWN"]


class NotFound(Exception):
    pass


def build_garage(customer_id: str, env_prefix: str | None = None) -> dict[str, Any]:
    """Assemble the customer-safe My Garage for one customer."""
    customer = ddb.get_item("DEALER_Customers", {"customer_id": customer_id}, env_prefix)
    # A customer may exist only via CUST_ tables; don't hard-fail if the dealer
    # profile is absent, but require *some* footprint.
    vehicles = ddb.scan_filter("DEALER_Vehicles", "customer_id", customer_id, env_prefix)
    journeys = ddb.scan_filter("DEALER_Journeys", "customer_id", customer_id, env_prefix)
    ros = ddb.scan_filter("DEALER_RepairOrders", "customer_id", customer_id, env_prefix)
    docs = ddb.scan_filter("CUST_Documents", "customer_id", customer_id, env_prefix)
    consent = ddb.get_item("CUST_Consent", {"customer_id": customer_id}, env_prefix)

    if customer is None and not (vehicles or journeys or ros):
        raise NotFound(f"no garage for customer {customer_id}")

    return {
        "customer_id": customer_id,
        "market_id": (customer or {}).get("market_id"),
        "name": _safe_name(customer),
        "vehicles": [_vehicle(v, env_prefix) for v in vehicles],
        "purchase": _purchase(journeys),
        "service": _service(customer_id, env_prefix),
        "service_history": [_ro(r) for r in
                            sorted(ros, key=lambda r: r.get("opened_at") or "", reverse=True)],
        "documents": [_doc(d) for d in
                      sorted(docs, key=lambda d: d.get("created_at") or "", reverse=True)],
        "preferences": _prefs(consent),
        "disclaimer": "Customer view — pricing shown is the total presented to you; "
                      "internal cost details are not part of this view.",
    }


# --- service section (§5.12 My Garage › Service) ----------------------------

_SERVICE_STAGES = ["BOOK", "CHECK_IN", "INSPECT", "DIAGNOSE", "APPROVE",
                   "REPAIR", "READY", "FOLLOW_UP", "COMPLETED"]


def _service(customer_id: str, env_prefix: str | None) -> dict[str, Any]:
    """The customer-facing service view: their active service journeys with a
    stage stepper, the estimate total + line items they can approve/decline, and
    declined work. Leak-safe: no technician assignment, labor margin, or internal
    notes — only what the customer needs to act."""
    sjs = ddb.scan_filter("DEALER_ServiceJourneys", "customer_id", customer_id, env_prefix)
    active = [s for s in sjs if s.get("stage") != "COMPLETED"]
    out = []
    for sj in sorted(active, key=lambda s: s.get("opened_at") or "", reverse=True):
        sjid = sj.get("service_journey_id")
        stage = sj.get("stage")
        ests = ddb.scan_filter("DEALER_ServiceEstimates", "service_journey_id", sjid, env_prefix)
        est = ests[0] if ests else None
        # estimate is only actionable at APPROVE
        out.append({
            "service_journey_id": sjid,
            "vin": sj.get("vin"),
            "stage": stage,
            "stepper": [{"stage": s, "current": s == stage} for s in _SERVICE_STAGES],
            "promised_at": sj.get("promised_at"),
            "wait_type": sj.get("wait_type"),
            "estimate": ({"estimate_id": est.get("estimate_id"),
                          "total": est.get("total"), "currency": est.get("currency"),
                          "status": est.get("status"),
                          "awaiting_your_approval": stage == "APPROVE" and est.get("status") == "pending",
                          "can_approve": stage == "APPROVE"} if est else None),
        })
    declined = ddb.get_item("DEALER_DeclinedWork", {"customer_id": customer_id}, env_prefix)
    return {
        "active_journeys": out,
        "declined_work": ([{"item": d.get("item"), "amount": d.get("amount"),
                            "currency": d.get("currency"), "declined_at": d.get("declined_at")}
                           for d in [declined]] if declined else []),
        "note": "Approve or decline your estimate from the service journey when it "
                "reaches Approval; declined work is kept and can be booked later.",
    }


# --- customer-safe field whitelists ----------------------------------------

def _safe_name(c: dict[str, Any] | None) -> str | None:
    if not c:
        return None
    parts = [c.get("given_name"), c.get("family_name")]
    return " ".join(p for p in parts if p) or None


def _vehicle(v: dict[str, Any], env_prefix: str | None = None) -> dict[str, Any]:
    v = vehicle_names.display_fields(v, env_prefix)
    return {k: v.get(k) for k in ("vin", "make", "model", "year", "odometer",
                                  "ownership", "delivered_at")}


def _purchase(journeys: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Active (or most recent) journey, safe fields only."""
    if not journeys:
        return None
    active = [j for j in journeys if j.get("status") == "active"]
    j = (active or sorted(journeys, key=lambda x: x.get("updated_at") or "", reverse=True))[0]
    stage = j.get("stage")
    return {
        "stage": stage,
        "status": j.get("status"),
        "summary": j.get("summary"),
        "stepper": [{"stage": s, "current": s == stage} for s in _PURCHASE_STAGES],
        # deliberately omitted: owner_user_id, team, idle_days, campaign_id,
        # district_id, config_id, lead scoring, demo_missing_context.
    }


def _ro(r: dict[str, Any]) -> dict[str, Any]:
    # RO number, status, and the TOTAL the customer was shown — no labor/parts
    # margin breakdown.
    return {"ro_number": r.get("ro_number"), "status": r.get("status"),
            "total": r.get("total"), "currency": r.get("currency"),
            "opened_at": r.get("opened_at")}


def _doc(d: dict[str, Any]) -> dict[str, Any]:
    # kind + date only; never the internal storage uri or data_class.
    return {"kind": d.get("kind"), "created_at": d.get("created_at")}


def _prefs(c: dict[str, Any] | None) -> dict[str, Any] | None:
    if not c:
        return None
    return {k: c.get(k) for k in ("preferred_channel", "email_opt_in", "sms_opt_in",
                                  "voice_opt_in", "quiet_hours")}
