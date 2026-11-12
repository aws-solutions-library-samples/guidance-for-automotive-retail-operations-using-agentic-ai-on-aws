"""My Garage sections (spec §5.12) — customer-safe read models + self-service writes.

Every function takes the customer_id the authorizer bound to the session; none
accepts a different customer. Responses are built from explicit field
whitelists (never a raw row spread), so staff-only fields — owner assignment,
lead scores, margins, internal notes, document storage URIs, data class — can't
reach a customer surface.

  journey(cid)                 tracker: 8-stage stepper, done / next, advisor
  builds(cid)                  saved configurations + "find this VIN near me"
  appointments(cid)            test drives, deliveries, service; reschedule / cancel
  finance(cid)                 prequal status, presented scenarios, disclosures
  trade(cid) / refresh_trade   valuation range, expiry, equity; mileage refresh
  documents(cid)               documents shared with the customer
  ownership(cid)               owned vehicles, next service due, upgrade signal
  get_consent / put_consent    channels, quiet hours, unsubscribe (stops A2 sends)
  request_data(cid, kind)      data download / delete request (→ Admin › Data requests)
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import re
from typing import Any

from ..common import authz, ddb
from ..finance import payment
from ..events import bus as _bus

STAGES = ["DISCOVER", "CONFIGURE", "QUALIFY", "FINANCE", "TRADE_IN", "AGREE", "DELIVER", "OWN"]
# What the customer sees as "next" for each stage (§2.1 exit criteria, customer voice).
_NEXT = {
    "DISCOVER": "Shortlist up to three vehicles you like.",
    "CONFIGURE": "Save a build with your trim, packages and price.",
    "QUALIFY": "Share your budget and timeline with your advisor — a test drive helps.",
    "FINANCE": "Review your payment options (soft pre-qualification, no credit decision).",
    "TRADE_IN": "Confirm your trade-in details so we can lock in its value.",
    "AGREE": "Review and agree your deal with your advisor.",
    "DELIVER": "Get ready for delivery day.",
    "OWN": "Enjoy your vehicle — we'll remind you when service is due.",
}
_TRADE_VALID_DAYS = 30
CONSENT_FIELDS = ("email_opt_in", "sms_opt_in", "voice_opt_in", "preferred_channel", "quiet_hours")
PREFERRED = ("email", "sms", "chat", "voice")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DATA_REQUEST_KINDS = ("download", "delete")


class NotFound(Exception):
    pass


class Invalid(ValueError):
    pass


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def _iso(d: _dt.datetime) -> str:
    return d.isoformat().replace("+00:00", "Z")


def _parse(ts: str | None) -> _dt.datetime | None:
    if not ts:
        return None
    try:
        return _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def customer(cid: str, env_prefix: str | None = None) -> dict[str, Any]:
    c = ddb.get_item("DEALER_Customers", {"customer_id": cid}, env_prefix)
    if c is None:
        raise NotFound(f"customer {cid} not found")
    return c


def _user_name(user_id: str | None, env_prefix: str | None) -> str | None:
    if not user_id:
        return None
    u = ddb.get_item("PLAT_Users", {"user_id": user_id}, env_prefix) or {}
    return " ".join(filter(None, [u.get("given_name"), u.get("family_name")])) or None


def _dealer_name(dealer_id: str | None, env_prefix: str | None,
                 market_id: str | None = None) -> str | None:
    """Dealer display name. node_id repeats across markets, so pass the market
    when it is known (the composite key of PLAT_OrgHierarchy)."""
    if not dealer_id:
        return None
    return (authz.org_node(dealer_id, market_id, env_prefix) or {}).get("name")


def _journeys(cid: str, env_prefix: str | None) -> list[dict[str, Any]]:
    return ddb.scan_filter("DEALER_Journeys", "customer_id", cid, env_prefix)


def active_journey(cid: str, env_prefix: str | None = None) -> dict[str, Any] | None:
    js = _journeys(cid, env_prefix)
    active = [j for j in js if (j.get("status") or "active") == "active" and j.get("stage") != "OWN"
              and not j.get("superseded_by")]
    pool = active or js
    return sorted(pool, key=lambda j: j.get("updated_at") or j.get("created_at") or "", reverse=True)[0] if pool else None


# --- journey tracker -----------------------------------------------------------

def journey(cid: str, env_prefix: str | None = None) -> dict[str, Any]:
    cust = customer(cid, env_prefix)
    j = active_journey(cid, env_prefix)
    if j is None:
        return {"customer_id": cid, "market_id": cust.get("market_id"), "journey": None,
                "name": cust.get("given_name")}
    stage = j.get("stage")
    idx = STAGES.index(stage) if stage in STAGES else -1
    advisor_id = j.get("owner_user_id") or cust.get("assigned_advisor_user_id")
    return {
        "customer_id": cid,
        "market_id": j.get("market_id") or cust.get("market_id"),
        "name": cust.get("given_name"),
        "journey": {
            "journey_id": j.get("journey_id"),
            "stage": stage,
            "steps": [{"stage": s, "state": "done" if i < idx else "current" if i == idx else "todo"}
                      for i, s in enumerate(STAGES)],
            "next": _NEXT.get(stage),
            "advisor": {"name": _user_name(advisor_id, env_prefix) or "Your advisor"} if advisor_id else None,
            "dealer": {"dealer_id": j.get("dealer_id"), "name": _dealer_name(j.get("dealer_id"), env_prefix, j.get("market_id") or cust.get("market_id"))},
            "updated_at": j.get("updated_at") or j.get("last_event_at"),
        },
    }


# --- saved builds ----------------------------------------------------------------

def _catalog_names(env_prefix: str | None) -> tuple[dict[str, str], dict[str, str]]:
    models, trims = {}, {}
    for m in ddb.scan_all("OEM_CatalogModels", env_prefix):
        models[m.get("model_id")] = m.get("name") or m.get("display_name") or m.get("body") or m.get("model_id")
    for t in ddb.scan_all("OEM_Catalog", env_prefix):
        if t.get("trim_id"):
            trims[t["trim_id"]] = t.get("trim_name") or t["trim_id"]
    return models, trims


def _incentive_names(ids: list[str], env_prefix: str | None, market: str = "us") -> list[dict[str, Any]]:
    """Each applied incentive with what it does to the deal: `price` (comes off the vehicle),
    `apr` (a captive-finance rate program), `informational` (claimed at tax time or by the utility)
    — so a build can list three incentives and honestly show $0 off the price."""
    from ..agents import discovery
    amount_field = "amount_eur" if market == "de" else "amount_usd"
    out = []
    for iid in ids or []:
        inc = ddb.get_item("OEM_Incentives", {"incentive_id": iid}, env_prefix) or {}
        amt = inc.get(amount_field)
        kind = "apr" if inc.get("apr_special") else ("price" if amt and discovery._reduces_vehicle_price(inc) else "informational")
        out.append({"incentive_id": iid, "name": inc.get("name") or iid, "kind": kind,
                    "amount": amt if kind != "apr" else None,
                    "how": {"price": "off the vehicle price", "apr": "a lower rate on the lender's offer",
                            "informational": "claimed separately (tax credit or utility rebate)"}[kind]})
    return out


def builds(cid: str, env_prefix: str | None = None) -> dict[str, Any]:
    customer(cid, env_prefix)
    models, trims = _catalog_names(env_prefix)
    rows = sorted(ddb.scan_filter("PLAT_Configurations", "customer_id", cid, env_prefix),
                  key=lambda c: c.get("saved_at") or "", reverse=True)
    return {"customer_id": cid, "builds": [{
        "config_id": c.get("config_id"),
        "model_id": c.get("model_id"), "model_name": models.get(c.get("model_id"), c.get("model_id")),
        "trim_id": c.get("trim_id"), "trim_name": trims.get(c.get("trim_id"), c.get("trim_id")),
        "color": c.get("color"), "packages": c.get("packages") or [],
        "price_vehicle": c.get("price_vehicle"), "price_tax_est": c.get("price_tax_est"),
        "incentives_total": c.get("incentives_total"),
        "incentives": _incentive_names(c.get("incentives_applied") or [], env_prefix, c.get("currency") == "EUR" and "de" or "us"),
        "price_total": c.get("price_total"),
        "est_monthly_buy": c.get("est_monthly_buy"), "est_monthly_lease": c.get("est_monthly_lease"),
        # the payment of record for this build's journey (the deal's once booked) — the estimate above is
        # an estimate, and the screen says so once a real number exists
        "payment": payment.payment_of_record(c.get("journey_id"), env_prefix),
        "currency": c.get("currency"), "saved_at": c.get("saved_at"),
        "share_token": c.get("share_token"), "journey_id": c.get("journey_id"),
        "source_ref": c.get("source_ref"),
    } for c in rows]}


def build_availability(cid: str, config_id: str, env_prefix: str | None = None) -> dict[str, Any]:
    """'Find this VIN near me' — VIN-level availability for a saved build (A1)."""
    from ..agents import discovery
    cfg = ddb.get_item("PLAT_Configurations", {"config_id": config_id}, env_prefix)
    if cfg is None or cfg.get("customer_id") != cid:
        raise NotFound(f"build {config_id} not found")
    cust = customer(cid, env_prefix)
    j = active_journey(cid, env_prefix) or {}
    inv = discovery.search_inventory(cfg.get("model_id"), cfg.get("trim_id"),
                                     dealer_id=j.get("dealer_id") or cust.get("dealer_id"),
                                     market=cust.get("market_id") or "us", env_prefix=env_prefix)
    names = {}
    def card(v):
        if not v:
            return None
        d = v.get("dealer_id")
        if d not in names:
            names[d] = _dealer_name(d, env_prefix, cust.get("market_id"))
        # customer view: no cost / internal status fields
        return {"vin": v.get("vin"), "dealer_id": d, "dealer_name": names[d], "trim_name": v.get("trim_name"),
                "color": v.get("color"), "list_price": v.get("list_price"), "eta_days": v.get("eta_days"),
                "match": v.get("match")}
    return {"config_id": config_id, "best_offer": card(inv.get("best_offer")),
            "options": [card(v) for v in (inv.get("exact", []) + inv.get("near_matches", [])
                                          + inv.get("nearby_dealers", []) + inv.get("in_transit", []))[:8]],
            "in_stock_count": inv.get("in_stock_count"), "source_ref": inv.get("source_ref")}


# --- appointments --------------------------------------------------------------------

def _appt_view(a: dict[str, Any], env_prefix: str | None) -> dict[str, Any]:
    start = _parse(a.get("slot_start"))
    upcoming = bool(start and start >= _now()) and a.get("status") not in ("cancelled", "completed")
    return {"appointment_id": a.get("appointment_id"), "type": a.get("type"), "status": a.get("status"),
            "slot_start": a.get("slot_start"), "slot_end": a.get("slot_end"),
            "dealer_id": a.get("dealer_id"), "dealer_name": _dealer_name(a.get("dealer_id"), env_prefix),
            "wait_type": a.get("wait_type"), "services": a.get("services"),
            "service_journey_id": a.get("service_journey_id"), "journey_id": a.get("journey_id"),
            "upcoming": upcoming, "can_change": upcoming}


def appointments(cid: str, env_prefix: str | None = None) -> dict[str, Any]:
    customer(cid, env_prefix)
    rows = [_appt_view(a, env_prefix) for a in ddb.scan_filter("DEALER_Appointments", "customer_id", cid, env_prefix)]
    rows.sort(key=lambda a: a.get("slot_start") or "", reverse=True)
    return {"customer_id": cid, "upcoming": [a for a in rows if a["upcoming"]][::-1],
            "past": [a for a in rows if not a["upcoming"]][:20]}


def change_appointment(cid: str, appointment_id: str, *, action: str, slot_start: str | None = None,
                       env_prefix: str | None = None) -> dict[str, Any]:
    a = ddb.get_item("DEALER_Appointments", {"appointment_id": appointment_id}, env_prefix)
    if a is None or a.get("customer_id") != cid:
        raise NotFound(f"appointment {appointment_id} not found")
    view = _appt_view(a, env_prefix)
    if not view["can_change"]:
        raise Invalid("only upcoming appointments can be changed")
    a = dict(a)
    service = (a.get("type") or "").startswith("service") or bool(a.get("service_journey_id"))
    if action == "cancel":
        a["status"] = "cancelled"
        a["cancelled_at"] = _iso(_now())
        a["cancelled_by"] = "customer"
        etype = "service.appointment.cancelled" if service else "appointment.cancelled"
    elif action == "reschedule":
        new = _parse(slot_start)
        if new is None or new <= _now():
            raise Invalid("slot_start must be a future ISO time")
        old_start, old_end = _parse(a.get("slot_start")), _parse(a.get("slot_end"))
        length = (old_end - old_start) if (old_start and old_end) else _dt.timedelta(hours=1)
        a["slot_start"] = _iso(new)
        a["slot_end"] = _iso(new + length)
        a["rescheduled_at"] = _iso(_now())
        a["rescheduled_by"] = "customer"
        etype = "service.appointment.changed" if service else "appointment.changed"
    else:
        raise Invalid("action must be cancel or reschedule")
    ddb.put_item("DEALER_Appointments", a, env_prefix)
    _bus.emit(etype, {"appointment_id": appointment_id, "customer_id": cid, "dealer_id": a.get("dealer_id"),
                      "journey_id": a.get("journey_id"), "service_journey_id": a.get("service_journey_id"),
                      "slot_start": a.get("slot_start"), "status": a.get("status")},
              actor={"type": "customer", "customer_id": cid},
              resource_url=f"/r/appointment/{appointment_id}", env_prefix=env_prefix)
    return _appt_view(a, env_prefix)


# --- financing -------------------------------------------------------------------------

def finance(cid: str, env_prefix: str | None = None) -> dict[str, Any]:
    cust = customer(cid, env_prefix)
    market = cust.get("market_id") or "us"
    mkt = ddb.get_item("OEM_Markets", {"market_id": market}, env_prefix) or {}
    jids = {j.get("journey_id") for j in _journeys(cid, env_prefix)}
    deals = [d for d in ddb.scan_filter("DEALER_Deals", "customer_id", cid, env_prefix)]
    offers = [o for jid in jids if jid for o in ddb.scan_filter("DEALER_FinanceOffers", "journey_id", jid, env_prefix)]
    deal = sorted(deals, key=lambda d: d.get("created_at") or "", reverse=True)[0] if deals else None
    prequal = "not_started"
    if offers:
        prequal = "offers_presented"
    if deal and deal.get("status") in ("booked", "contracted"):
        prequal = "deal_agreed"
    record = next((p for p in (payment.payment_of_record(j, env_prefix) for j in sorted(jids, key=str) if j) if p), None)
    return {
        "customer_id": cid, "market_id": market,
        "status": prequal,
        "payment": record,          # what the customer pays (deal) or was offered (offer); offers below are scenarios
        "offers": [{"offer_id": o.get("offer_id"), "lender": o.get("lender"), "term_months": o.get("term_months"),
                    "apr_pct": o.get("apr_pct"), "monthly_payment": o.get("monthly_payment"),
                    "down_payment": o.get("down_payment"), "amount_financed": o.get("amount_financed"),
                    "currency": o.get("currency"), "status": o.get("status"), "source_ref": o.get("source_ref")}
                   for o in sorted(offers, key=lambda o: (o.get("monthly_payment") or 0))],
        "deal": ({"deal_id": deal.get("deal_id"), "type": deal.get("type"), "status": deal.get("status"),
                  "price_total": deal.get("price_total"), "currency": deal.get("currency")} if deal else None),
        "disclosures": mkt.get("regulatory_disclosures") or [],
        "finance_products": [{"code": p.get("code"), "name": p.get("name"), "terms_months": p.get("terms_months")}
                             for p in (mkt.get("finance_products") or [])],
        "note": "Payment scenarios are illustrative. Pre-qualification is a soft check — not a credit decision.",
    }


def start_prequal(cid: str, *, amount: float | None = None, down: float = 0,
                  term_months: int | None = None, env_prefix: str | None = None) -> dict[str, Any]:
    """Customer-initiated soft prequal (hand-off to the F&I connector sandbox)."""
    from ..finance import prequalify as fni
    j = active_journey(cid, env_prefix)
    if j is None:
        raise Invalid("there is no active purchase journey to pre-qualify for")
    if amount is None:
        cfg = sorted(ddb.scan_filter("PLAT_Configurations", "customer_id", cid, env_prefix),
                     key=lambda c: c.get("saved_at") or "", reverse=True)
        amount = (cfg[0].get("price_total") if cfg else None)
    if not amount or float(amount) <= 0:
        raise Invalid("save a build (or give an amount) before pre-qualifying")
    out = fni.prequalify(j, float(amount), term_months=term_months, down=float(down or 0),
                         actor=f"customer:{cid}", env_prefix=env_prefix)
    return {"journey_id": out.get("journey_id"), "offer_id": out.get("offer_id"), "currency": out.get("currency"),
            "scenarios": out.get("scenarios"), "recommended": out.get("recommended"),
            "disclaimer": out.get("disclaimer"), "is_credit_decision": False}


# --- trade-in ------------------------------------------------------------------------------

def _valuations(cid: str, env_prefix: str | None) -> list[dict[str, Any]]:
    """The customer's valuations, newest first — a car already traded in is history, not 'current'."""
    return sorted((v for v in ddb.scan_filter("DEALER_Valuations", "customer_id", cid, env_prefix)
                   if v.get("disposition") != "traded"),
                  key=lambda v: v.get("valued_at") or "", reverse=True)


def _val_view(v: dict[str, Any]) -> dict[str, Any]:
    valued = _parse(v.get("valued_at"))
    expires = valued + _dt.timedelta(days=_TRADE_VALID_DAYS) if valued else None
    value = v.get("trade_value")
    # range: the guidebook spread the sandbox valuation implies (±5 %)
    rng = {"low": round(value * 0.95), "high": round(value * 1.05)} if isinstance(value, (int, float)) else None
    return {"valuation_id": v.get("valuation_id"), "year": v.get("year"), "make": v.get("make"),
            "model": v.get("model"), "mileage": v.get("mileage"), "trade_value": value, "range": rng,
            "payoff": v.get("payoff"), "equity": v.get("equity"), "currency": v.get("currency"),
            "valued_at": v.get("valued_at"), "expires_at": _iso(expires) if expires else None,
            "expired": bool(expires and expires < _now()), "condition": v.get("condition"),
            "source_ref": v.get("source_ref")}


def trade(cid: str, env_prefix: str | None = None) -> dict[str, Any]:
    cust = customer(cid, env_prefix)
    vals = _valuations(cid, env_prefix)
    return {"customer_id": cid, "market_id": cust.get("market_id"),
            "current": _val_view(vals[0]) if vals else None,
            "history": [_val_view(v) for v in vals[1:6]],
            "positive_equity": bool(vals and (vals[0].get("equity") or 0) > 0)}


def refresh_trade(cid: str, *, mileage: float | None, condition: str | None = None,
                  env_prefix: str | None = None) -> dict[str, Any]:
    from ..agents import equity
    if mileage is not None and float(mileage) < 0:
        raise Invalid("mileage must be positive")
    if condition is not None and condition not in ("excellent", "good", "fair", "poor"):
        raise Invalid("condition must be excellent, good, fair or poor")
    try:
        equity.refresh_valuation(cid, mileage=mileage, condition=condition, env_prefix=env_prefix)
    except equity.RefreshError as e:
        raise Invalid(str(e)) from e
    out = trade(cid, env_prefix)
    cur = out.get("current") or {}
    _bus.emit("trade.valued", {"customer_id": cid, "valuation_id": cur.get("valuation_id"),
                               "trade_value": cur.get("trade_value"), "equity": cur.get("equity"),
                               "source": "customer_refresh"},
              actor={"type": "customer", "customer_id": cid}, env_prefix=env_prefix)
    return out


# --- documents -------------------------------------------------------------------------------

_DOC_LABEL = {"contract": "Purchase contract", "insurance": "Insurance card", "invoice": "Invoice",
              "id_verification": "ID verification", "inspection_report": "Inspection report",
              "delivery_checklist": "Delivery checklist", "proposal": "Proposal"}


def documents(cid: str, env_prefix: str | None = None) -> dict[str, Any]:
    customer(cid, env_prefix)
    rows = sorted(ddb.scan_filter("CUST_Documents", "customer_id", cid, env_prefix),
                  key=lambda d: d.get("created_at") or "", reverse=True)
    return {"customer_id": cid, "documents": [{
        "document_id": d.get("document_id"), "kind": d.get("kind"),
        "label": _DOC_LABEL.get(d.get("kind"), (d.get("kind") or "document").replace("_", " ").title()),
        "created_at": d.get("created_at"),
        # the pre-signed viewer is the Document service (§4.9); until it is
        # deployed the customer can request a copy from their advisor.
        "viewer": None,
    } for d in rows]}


# --- ownership ---------------------------------------------------------------------------------

def ownership(cid: str, env_prefix: str | None = None) -> dict[str, Any]:
    from ..service import booking
    customer(cid, env_prefix)
    models, trims = _catalog_names(env_prefix)
    vehicles = ddb.scan_filter("DEALER_Vehicles", "customer_id", cid, env_prefix)
    out = []
    for v in sorted(vehicles, key=lambda v: v.get("delivered_at") or "", reverse=True):
        try:
            due = booking.predict_next_service(v["vin"], env_prefix)
            next_due = {"due_by": due.get("due_by"), "next_service_mileage": due.get("next_service_mileage"),
                        "overdue": due.get("overdue"), "recommended_services": due.get("recommended_services"),
                        "source_ref": due.get("source_ref")}
        except Exception:
            next_due = None
        ros = [r for r in ddb.scan_filter("DEALER_RepairOrders", "customer_id", cid, env_prefix)]
        out.append({
            "vin": v.get("vin"), "year": v.get("year"),
            "make": v.get("make"), "model": v.get("model") or models.get(v.get("model_id"), v.get("model_id")),
            "trim": trims.get(v.get("trim_id"), v.get("trim_id")),
            "odometer": v.get("odometer"), "ownership": v.get("ownership"), "delivered_at": v.get("delivered_at"),
            "next_service": next_due,
            "service_visits": len([r for r in ros if r.get("service_journey_id")]),
            # no recall feed in the sandbox (OEM warranty/recall connector, §3.7)
            "recalls": [],
        })
    # the one upgrade rule (common/vehicles.upgrade_window): equity in a car the customer still owns,
    # a year old or a lease in its last months — never on the day they bought it
    from ..common import vehicles as _veh
    w = _veh.upgrade_window(cid, env_prefix)
    upgrade = ({"equity": w["equity"], "currency": w["currency"], "vin": w["vin"], "why": w["why"],
                "message": "Your car is worth more than you owe — ask your advisor about upgrading."}
               if w else None)
    # A7 predictive-maintenance offers (lead-backed): the customer side of the
    # Lead Desk's predictive leads — part, predicted mileage, early-bird offer.
    from ..service import predictive
    try:
        offers = predictive.customer_offers(cid, env_prefix)
    except Exception:
        offers = []
    by_vin: dict[str, list] = {}
    for o in offers:
        by_vin.setdefault(o.get("vin") or "", []).append(o)
    # the delivery survey's state lives on the record, not in a browser: the card shows for a delivery in
    # the last 30 days until THIS customer has answered for THIS car (a dismissal is per session only)
    surveyed = {m.get("vin") for m in ddb.scan_filter("CUST_Messages", "customer_id", cid, env_prefix)
                if m.get("kind") == "delivery_survey"}
    now = _dt.datetime.now(_dt.UTC)
    for v in out:
        v["service_offers"] = by_vin.get(v.get("vin") or "", [])
        delivered = _parse(v.get("delivered_at"))
        recent = bool(delivered and (now - delivered).days <= 30)
        v["delivery_survey"] = ("submitted" if v.get("vin") in surveyed else "pending") if recent else None
    return {"customer_id": cid, "vehicles": out, "upgrade_offer": upgrade,
            "service_offers": offers,
            "recall_feed": "not connected (sandbox)"}


# --- delivery survey (§5.11 My Garage) -------------------------------------------------------------

SURVEY_RATING_MIN, SURVEY_RATING_MAX = 1, 5
_SURVEY_LABELS = {1: "poor", 2: "fair", 3: "good", 4: "great", 5: "excellent"}


def submit_delivery_survey(cid: str, *, rating: Any, comment: str | None = None, vin: str | None = None,
                           env_prefix: str | None = None) -> dict[str, Any]:
    """Record a customer's delivery survey (rating + optional comment) for one of
    their own vehicles.

    Self-scope: the VIN must be a vehicle this customer owns (an unspecified VIN
    resolves to their most recently delivered one). The response lands as an
    inbound `survey` message on the customer's record — where the dealer's CX
    tab already reads customer replies — and as an event on the purchase journey
    that delivered the vehicle, so the feedback sits on the journey timeline the
    advisor reads. The CSI survey connector stays the system of record for CSI
    scoring; this is the first-party capture."""
    customer(cid, env_prefix)
    try:
        score = int(rating)
    except (TypeError, ValueError):
        raise Invalid(f"rating must be an integer {SURVEY_RATING_MIN}-{SURVEY_RATING_MAX}")
    if not SURVEY_RATING_MIN <= score <= SURVEY_RATING_MAX:
        raise Invalid(f"rating must be between {SURVEY_RATING_MIN} and {SURVEY_RATING_MAX}")
    note = (comment or "").strip()[:2000]

    vehicles = ddb.scan_filter("DEALER_Vehicles", "customer_id", cid, env_prefix)
    if vin:
        vehicle = next((v for v in vehicles if v.get("vin") == vin), None)
        if vehicle is None:
            raise NotFound(f"{vin} is not one of your vehicles")
    else:
        vehicle = max(vehicles, key=lambda v: v.get("delivered_at") or "", default=None)
        if vehicle is None:
            raise NotFound("no delivered vehicle to survey")
    vin = vehicle["vin"]
    label = _SURVEY_LABELS.get(score, str(score))
    name = " ".join(str(x) for x in (vehicle.get("year"), vehicle.get("make"), vehicle.get("model")) if x) or vin

    ts = _iso(_now())
    survey_id = "SVY-" + hashlib.sha1(f"{cid}|{vin}|delivery|{ts}".encode()).hexdigest()[:12].upper()
    body = (f"Delivery survey for my {name}: {score} out of 5 ({label})."
            + (f" {note}" if note else ""))
    dealer_id = vehicle.get("dealer_id")
    ddb.put_item("CUST_Messages", {
        "message_id": survey_id, "customer_id": cid, "direction": "inbound", "channel": "survey",
        "sent_at": ts, "body": body, "kind": "delivery_survey", "rating": score, "rating_label": label,
        "comment": note or None, "vin": vin, "dealer_id": dealer_id,
        "source_ref": "My Garage delivery survey (customer-submitted)",
    }, env_prefix)

    # The journey that delivered this vehicle, so the survey sits on its timeline:
    # the delivery record names it; otherwise fall back to the customer's owned
    # (OWN-stage) journey. No match leaves the link null rather than guessing.
    journeys = sorted(_journeys(cid, env_prefix), key=lambda j: j.get("updated_at") or "", reverse=True)
    by_id = {j["journey_id"]: j for j in journeys}
    deliveries = [d for d in ddb.scan_filter("DEALER_Deliveries", "customer_id", cid, env_prefix)
                  if d.get("journey_id") in by_id]
    model = vehicle.get("model_id")
    journey = next((by_id[d["journey_id"]] for d in deliveries
                    if d.get("status") == "completed" and model and d.get("vehicle_model_id") == model), None)
    if journey is None:
        journey = next((by_id[d["journey_id"]] for d in deliveries if d.get("status") == "completed"), None)
    if journey is None:
        journey = next((j for j in journeys if j.get("stage") == "OWN" or j.get("vin") == vin), None)
    event_id = None
    if journey:
        from ..journeys import supervisor as _sup
        ev = _sup._event_row(journey["journey_id"], "delivery.survey_submitted",
                             f"customer:{cid}", f"delivery rated {score}/5 ({label})",
                             {"survey_id": survey_id, "vin": vin, "rating": score, "comment": note or None}, ts)
        ev["actor"] = {"type": "customer", "customer_id": cid}
        ddb.put_item("DEALER_JourneyEvents", ev, env_prefix)
        event_id = ev.get("event_id")

    _bus.emit("signal.recorded",
              {"kind": "delivery_survey", "survey_id": survey_id, "customer_id": cid, "vin": vin,
               "dealer_id": dealer_id, "journey_id": (journey or {}).get("journey_id"), "rating": score,
               "has_comment": bool(note)},
              actor={"type": "customer", "customer_id": cid},
              resource_url=f"/r/journey/{journey['journey_id']}" if journey else None,
              env_prefix=env_prefix)

    return {"survey_id": survey_id, "customer_id": cid, "vin": vin, "vehicle": name,
            "rating": score, "rating_label": label, "comment": note or None,
            "dealer_id": dealer_id, "journey_id": (journey or {}).get("journey_id"),
            "journey_event_id": event_id, "submitted_at": ts, "recorded": True,
            "message": "Thank you — your feedback went to your dealer.",
            "source_ref": "My Garage delivery survey -> CUST_Messages + DEALER_JourneyEvents"}


# --- consent & preferences -----------------------------------------------------------------------

def get_consent(cid: str, env_prefix: str | None = None) -> dict[str, Any]:
    c = ddb.get_item("CUST_Consent", {"customer_id": cid}, env_prefix) or {"customer_id": cid}
    return {"customer_id": cid, **{k: c.get(k) for k in CONSENT_FIELDS},
            "sms_opted_out": bool(c.get("sms_opt_out_at")), "unsubscribed_all": bool(c.get("unsubscribed_at")),
            "updated_at": c.get("updated_at"),
            "data_requests": [{k: r.get(k) for k in ("request_id", "kind", "status", "requested_at")}
                              for r in (c.get("data_requests") or [])]}


def put_consent(cid: str, changes: dict[str, Any], *, actor: dict[str, Any] | str,
                env_prefix: str | None = None) -> dict[str, Any]:
    """Apply consent changes. Opting out of a channel stops A2 sends on it on the
    next nurture cycle (nurture.check_consent reads these exact fields)."""
    cur = dict(ddb.get_item("CUST_Consent", {"customer_id": cid}, env_prefix) or {"customer_id": cid})
    if "market_id" not in cur:
        cur["market_id"] = (ddb.get_item("DEALER_Customers", {"customer_id": cid}, env_prefix) or {}).get("market_id")
    before = {k: cur.get(k) for k in (*CONSENT_FIELDS, "sms_opt_out_at", "unsubscribed_at")}
    now = _iso(_now())
    for k in ("email_opt_in", "sms_opt_in", "voice_opt_in"):
        if k in changes:
            if not isinstance(changes[k], bool):
                raise Invalid(f"{k} must be true or false")
            cur[k] = changes[k]
    if "sms_opt_in" in changes:
        cur["sms_opt_out_at"] = None if changes["sms_opt_in"] else now
    if "preferred_channel" in changes:
        if changes["preferred_channel"] not in PREFERRED:
            raise Invalid(f"preferred_channel must be one of {PREFERRED}")
        cur["preferred_channel"] = changes["preferred_channel"]
    if "quiet_hours" in changes:
        qh = changes["quiet_hours"]
        if qh is None:
            cur["quiet_hours"] = None
        else:
            if not isinstance(qh, dict) or not _HHMM.match(str(qh.get("start", ""))) or not _HHMM.match(str(qh.get("end", ""))):
                raise Invalid("quiet_hours needs start and end as HH:MM")
            cur["quiet_hours"] = {"start": qh["start"], "end": qh["end"],
                                  "tz": qh.get("tz") or (cur.get("quiet_hours") or {}).get("tz")}
    if changes.get("unsubscribe_all"):
        cur.update(email_opt_in=False, sms_opt_in=False, voice_opt_in=False, sms_opt_out_at=now,
                   unsubscribed_at=now)
    elif changes.get("unsubscribe_all") is False and cur.get("unsubscribed_at"):
        cur["unsubscribed_at"] = None
    cur["updated_at"] = now
    after = {k: cur.get(k) for k in before}
    changed = sorted(k for k in before if before[k] != after[k])
    ddb.put_item("CUST_Consent", cur, env_prefix)
    if changed:
        _bus.emit("consent.changed", {"customer_id": cid, "changed": changed,
                                      "email_opt_in": cur.get("email_opt_in"), "sms_opt_in": cur.get("sms_opt_in"),
                                      "voice_opt_in": cur.get("voice_opt_in")},
                  actor=actor, env_prefix=env_prefix)
    return {**get_consent(cid, env_prefix), "changed": changed}


def request_data(cid: str, kind: str, env_prefix: str | None = None) -> dict[str, Any]:
    """Record a data-subject request (§8.2). Admin › Data requests runs it across
    all stores; the customer sees its status here."""
    if kind not in DATA_REQUEST_KINDS:
        raise Invalid(f"kind must be one of {DATA_REQUEST_KINDS}")
    cur = dict(ddb.get_item("CUST_Consent", {"customer_id": cid}, env_prefix) or {"customer_id": cid})
    reqs = list(cur.get("data_requests") or [])
    if any(r.get("kind") == kind and r.get("status") in ("received", "in_progress") for r in reqs):
        raise Invalid(f"a {kind} request is already in progress")
    now = _iso(_now())
    rid = "DSR-" + hashlib.sha1(f"{cid}|{kind}|{now}".encode()).hexdigest()[:10]
    req = {"request_id": rid, "kind": kind, "status": "received", "requested_at": now}
    cur["data_requests"] = reqs + [req]
    cur["updated_at"] = now
    ddb.put_item("CUST_Consent", cur, env_prefix)
    _bus.emit("notification.requested", {"kind": "data_request", "request_id": rid, "request_kind": kind,
                                         "customer_id": cid, "audience": "admin_tenant"},
              actor={"type": "customer", "customer_id": cid}, env_prefix=env_prefix)
    return req
