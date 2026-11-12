"""A7 — service connector tools (spec §3.7), grounded in the reference tables.

The sandbox connectors the spec's A7 tool list names. Each reads a real
reference/DMS-mirror table so nothing is fabricated (acceptance #27/#28):

  open_repair_order(sj_id)            -> create the DMS RO mirror at CHECK_IN
  lookup_dtc(code)                    -> DEALER_DTCReference row (system + desc)
  get_labor_time(labor_op_id)         -> DEALER_LaborOps flat-rate hours + shop rate
  lookup_parts(labor_op_id | part_nos)-> DEALER_Parts availability + price
  check_coverage(vin, code?)          -> DEALER_Coverage / prepaid-maintenance match
  present_estimate(sj_id)             -> mark the estimate presented + magic link + event
  explain_finding(finding_ref)        -> customer-safe explanation (guardrail-checked)

These are the "every line traces to a tool call" sources the estimate is built
from; build_estimate (inspection.py) and the supervisor gate consume them.

Connector migration wave 1: `lookup_dtc`, `get_labor_time`, and `lookup_parts`
delegate through the shared interface in `app/services/connectors/`; sandbox
uses the same DynamoDB reads as before, while live mode remains a typed,
not-configured boundary until an endpoint is supplied. `PLAT_EntityMap`
translates identifiers at the boundary. Signatures are unchanged.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from ..common import ddb
from ..connectors import get_connector
# NotFound lives in the Connector base; re-exported so route modules'
# `connectors.NotFound` handlers keep working unchanged.
from ..connectors.base import NotFound  # noqa: F401
from ..events import bus as _bus
from . import supervisor as svc_supervisor

_SJ = "DEALER_ServiceJourneys"
_RO = "DEALER_RepairOrders"
_COVERAGE = "DEALER_Coverage"
_ESTIMATES = "DEALER_ServiceEstimates"
_EVENTS = "DEALER_ServiceJourneyEvents"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def open_repair_order(sj_id: str, actor: str = "A7",
                      env_prefix: str | None = None) -> dict[str, Any]:
    """Open the DMS RO mirror for a service journey (spec §2.1.1: ro_id present
    from CHECK_IN). Idempotent per journey (deterministic ro_id). This is the
    tool the CHECK_IN->INSPECT gate requires — before, nothing created the RO."""
    sj = ddb.get_item(_SJ, {"service_journey_id": sj_id}, env_prefix)
    if sj is None:
        raise NotFound(f"service journey {sj_id} not found")
    existing = ddb.scan_filter(_RO, "service_journey_id", sj_id, env_prefix)
    if existing:
        return {"ro_id": existing[0]["ro_id"], "service_journey_id": sj_id,
                "status": existing[0].get("status", "open"), "created": False}
    market = (sj.get("market_id") or "us").upper()
    ro_id = f"RO-{market}-{sj_id.split('-')[-1]}"
    ts = _now()
    ro = {"ro_id": ro_id, "service_journey_id": sj_id, "ro_number": ro_id,
          "status": "open", "total": 0, "labor_total": 0, "parts_total": 0,
          "currency": "USD" if market == "US" else "EUR",
          "customer_id": sj.get("customer_id"), "dealer_id": sj.get("dealer_id"),
          "opened_at": ts, "source_ref": "A7.open_repair_order (DMS RO mirror)"}
    ddb.put_item(_RO, ro, env_prefix)
    ev = svc_supervisor._event_row(sj_id, "ro.opened", actor, f"RO {ro_id} opened",
                                   {"ro_id": ro_id}, ts)
    ddb.put_item(_EVENTS, ev, env_prefix)
    _bus.emit("ro.opened", {"ro_id": ro_id, "service_journey_id": sj_id, "vin": sj.get("vin"),
                            "customer_id": sj.get("customer_id"), "dealer_id": sj.get("dealer_id"),
                            "mileage": sj.get("mileage")},
              actor=actor, resource_url=f"/r/ro/{ro_id}", env_prefix=env_prefix)
    return {"ro_id": ro_id, "service_journey_id": sj_id, "status": "open", "created": True,
            "source_ref": ro["source_ref"]}


# The reference lookups below delegate through app/services/connectors/:
# sandbox mode retains the DynamoDB logic in connectors/sandbox.py; live mode
# is a typed boundary until an endpoint is configured. Signatures are unchanged
# so routes and tests keep their imports.

def lookup_dtc(code: str, env_prefix: str | None = None,
               market: str | None = None) -> dict[str, Any]:
    """Diagnostic trouble code -> DEALER_DTCReference row (spec §3.7, acc #28)."""
    return get_connector("dms", env_prefix).lookup_dtc(code, market=market)


def get_labor_time(labor_op_id: str, env_prefix: str | None = None,
                   market: str | None = None) -> dict[str, Any]:
    """Flat-rate labor hours + shop rate for a labor op (spec §3.7)."""
    return get_connector("labor", env_prefix).get_labor_op(labor_op_id, market=market)


def lookup_parts(labor_op_id: str | None = None, part_numbers: list[str] | None = None,
                 ro_id: str | None = None, env_prefix: str | None = None) -> dict[str, Any]:
    """Parts availability + price. Looks up by RO, by part numbers, or (fallback)
    returns the catalog slice for a labor op tag (spec §3.7)."""
    return get_connector("parts", env_prefix).lookup_parts(
        part_numbers=part_numbers, labor_op_id=labor_op_id, ro_id=ro_id)


def check_coverage(vin: str, code: str | None = None,
                   market: str = "us", env_prefix: str | None = None) -> dict[str, Any]:
    """Prepaid-maintenance / VSC / warranty coverage for a VIN (spec §3.7).

    Sandbox: matches the vehicle's coverage codes against DEALER_Coverage. A code
    with a match returns the covered term/price; no match = customer_pay."""
    covs = ddb.scan_filter(_COVERAGE, "market_id", market, env_prefix)
    product = next((c for c in covs if c.get("code") == code), None) if code else None
    # DEALER_Coverage is the market's product CATALOG; a car is covered only when its own
    # deal bought the product (an accepted DEALER_FIProducts row on the deal the VIN was
    # sold on). Offering prepaid maintenance is not the customer owning it.
    bought = _bought_product(vin, code, env_prefix) if product else None
    match = product if bought else None
    return {"vin": vin, "code": code, "covered": bool(match),
            "coverage": {"name": match.get("name"), "term_months": match.get("term_months"),
                         "price_from": match.get("price_from"),
                         "currency": match.get("currency", "USD"),
                         "deal_id": bought.get("deal_id")} if match else None,
            "pay_type": "warranty" if match else "customer_pay",
            "source_ref": (f"DEALER_FIProducts:{bought.get('fi_product_id')} ({code} on deal {bought.get('deal_id')})"
                           if match else f"DEALER_FIProducts (no {code or 'product'} bought on this VIN's deal)")}


def _bought_product(vin: str, code: str | None, env_prefix: str | None) -> dict[str, Any] | None:
    """The accepted F&I product `code` on the deal this VIN was sold on, or None."""
    if not vin or not code:
        return None
    veh = ddb.get_item("DEALER_Vehicles", {"vin": vin}, env_prefix) or {}
    deal_ids = {veh["deal_id"]} if veh.get("deal_id") else set()
    if not deal_ids:
        deal_ids = {d["deal_id"] for d in ddb.scan_filter("DEALER_Deals", "vin", vin, env_prefix) if d.get("deal_id")}
    for deal_id in deal_ids:
        for p in ddb.scan_filter("DEALER_FIProducts", "deal_id", deal_id, env_prefix):
            if p.get("code") == code and p.get("accepted"):
                return p
    return None


def present_estimate(sj_id: str, actor: str = "A7", channel: str = "magic_link",
                     env_prefix: str | None = None) -> dict[str, Any]:
    """Mark the estimate presented to the customer + issue a decision link
    (spec §3.7, acc #27: link within 60s of present). Writes estimate.presented."""
    sj = ddb.get_item(_SJ, {"service_journey_id": sj_id}, env_prefix)
    if sj is None:
        raise NotFound(f"service journey {sj_id} not found")
    ests = ddb.scan_filter(_ESTIMATES, "service_journey_id", sj_id, env_prefix)
    if not ests:
        raise NotFound(f"no estimate for {sj_id} to present")
    est = dict(sorted(ests, key=lambda e: e.get("updated_at") or e.get("created_at") or "", reverse=True)[0])
    ts = _now()
    est["status"] = "presented"
    est["presented_at"] = ts
    ddb.put_item(_ESTIMATES, est, env_prefix)
    # customer decision magic link (My Garage approve/decline)
    link, link_expires_at, link_error = None, None, None
    try:
        from ..customer.magic_link import issue_magic_link
        ml = issue_magic_link(sj.get("customer_id"), f"estimate:{sj_id}", env_prefix=env_prefix)
        link, link_expires_at = ml["url"], ml.get("expires_at")
    except Exception as e:  # no customer on file → advisor presents in person
        link = None
        link_error = f"{type(e).__name__}: {e}"
    ev = svc_supervisor._event_row(sj_id, "estimate.presented", actor,
                                   f"estimate {est.get('estimate_id')} presented via {channel}",
                                   {"estimate_id": est.get("estimate_id"), "channel": channel,
                                    "approval_link": link}, ts)
    ddb.put_item(_EVENTS, ev, env_prefix)
    _bus.emit("estimate.presented", {"service_journey_id": sj_id, "estimate_id": est.get("estimate_id"),
                                     "channel": channel, "customer_id": sj.get("customer_id"),
                                     "dealer_id": sj.get("dealer_id"), "total": est.get("total")},
              actor=actor, resource_url=f"/r/estimate/{est.get('estimate_id')}", env_prefix=env_prefix)
    return {"service_journey_id": sj_id, "estimate_id": est.get("estimate_id"),
            "status": "presented", "approval_link": link, "link_expires_at": link_expires_at, "link_error": link_error, "channel": channel,
            "presented_at": ts, "total": est.get("total"), "currency": est.get("currency", "USD")}


def explain_finding(finding_ref: str, sj_id: str | None = None,
                    env_prefix: str | None = None) -> dict[str, Any]:
    """A customer-safe explanation of a finding, guardrail-checked for scare
    language (spec §3.7 / acc #28). The explanation cites the finding source and
    is run through the A7 scare-language guardrail before return."""
    insp = ddb.get_item("DEALER_Inspections", {"inspection_id": finding_ref}, env_prefix)
    dtc = None if insp else lookup_dtc(finding_ref, env_prefix)
    if insp:
        item = insp.get("item", finding_ref)
        status = str(insp.get("status") or "yellow").lower()
        measured = (f" (measured {insp['measurement']} {insp.get('unit') or ''})".rstrip(" )") + ")"
                    if insp.get("measurement") is not None else "")
        # Status-aware wording: one template said "we recommend addressing it" for every status, so
        # a green item read as a repair upsell with a dangling "here is what it affects"
        # (2026-10-27 eval, a7-inspection relevance 0.0-0.3). Plain language, no scare words.
        if status == "green":
            base = (f"Our inspection checked the {item}{measured} and it is in good condition (green). "
                    f"No action is needed now; we will check it again at your next visit.")
        elif status == "red":
            base = (f"Our inspection found the {item}{measured} needs attention now (red). We recommend taking "
                    f"care of it during this visit. Your advisor can explain the repair, the cost, and whether "
                    f"coverage applies, and you can approve or defer it.")
        else:
            base = (f"Our inspection noted the {item}{measured} is showing wear (yellow). It is not urgent, but "
                    f"worth planning for soon. Your advisor can explain the options and cost; you can approve it "
                    f"today or defer it to a later visit.")
        source = f"DEALER_Inspections:{finding_ref}"
    elif dtc and dtc.get("found"):
        # the reference row stores the SAE code family letter; say the system in words
        system = {"P": "powertrain", "B": "body", "C": "chassis", "U": "network"}.get(
            str(dtc.get("system") or "")[:1].upper(), dtc.get("system") or "vehicle")
        base = (f"A diagnostic code ({finding_ref}) was read on the {system} system: "
                f"{dtc.get('description')}. We can explain the repair and coverage options.")
        source = dtc["source_ref"]
    else:
        return {"finding_ref": finding_ref, "explained": False,
                "note": "no inspection item or DTC matched — cannot explain an ungrounded finding"}

    # scare-language guardrail (reuse the A7 scare denylist in PLAT_ConfigGuardrails)
    safe = True
    try:
        from ..oem import guardrails as gr
        chk = gr.check_guardrails(base, {"market_id": "us", "agent_id": "A7",
                                         "tool": "explain_finding", "log": False}, env_prefix)
        safe = chk.get("outcome") != "block"
    except Exception:
        pass
    return {"finding_ref": finding_ref, "explained": True, "explanation": base,
            "guardrail_safe": safe, "source_ref": source}
