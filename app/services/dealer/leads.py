"""Lead Desk read model + assign_lead (spec §5.4 / §5.18).

The BDC intake queue: leads land (DEALER_Leads), the desk shows first-response
SLA status (from DEALER_LeadSLA), and assigning a lead creates/links a purchase
journey in DISCOVER (the A2 create_journey handoff) so the funnel owner picks it
up.

  build_lead_desk(scope, market)         -> queue grouped by SLA status + KPIs
  create_lead(body, actor, scope)        -> web-form / chat / phone intake: resolve or create the
                                            customer, write the lead (status new) and start its
                                            first-response SLA clock; emits lead.received
  assign_lead(lead_id, assignee, actor)  -> assign + create/link a DISCOVER journey; when the
                                            assignee is not the current owner, ownership moves
                                            through a handoff (§5.18.3) with a receiver task
  respond_to_lead(lead_id, channel, ...) -> first response via OutreachProvider (call → task);
                                            stamps first_response_at and emits message.outbound

Grounding: SLA status comes from the recorded response vs. due time (breached is
a stored fact, not recomputed hopefully); assign_lead creates a real journey and
writes the lead->journey link + a journey.created event. The journey carries the
lead's stated request (model, comments) so A1's matches and A3's coach work from
what the customer actually asked for.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
from typing import Any

from ..common import ddb, labels, vehicles
from ..common import trace as _trace
from ..events import bus as _bus
from ..journeys import supervisor as journey_supervisor

_LEADS = "DEALER_Leads"
_LEAD_SLA = "DEALER_LeadSLA"
_JOURNEYS = "DEALER_Journeys"
_EVENTS = "DEALER_JourneyEvents"

# queue buckets by SLA state
_BUCKETS = ("new", "responded_in_sla", "breached", "worked", "closed")

# Where a lead can come from (spec §5.4 intake channels). Predictive leads are raised by A7
# (service/predictive.py), never keyed in.
INTAKE_SOURCES = ("web_form", "chat", "phone", "campaign", "walk_in", "referral", "third_party")
_DEFAULT_SLA_MINUTES = 30
# A journey in one of these stages is the customer's one active purchase (spec §4.2: at most one
# journey per customer in DISCOVER–DELIVER; OWN is a resting state a new lead never re-opens).
_ACTIVE_STAGES = ("DISCOVER", "CONFIGURE", "QUALIFY", "FINANCE", "TRADE_IN", "AGREE", "DELIVER")


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(dt: _dt.datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


class NotFound(Exception):
    pass


class AlreadyAssigned(Exception):
    pass


class InvalidLead(Exception):
    pass


def _vehicle_name(vin: str | None, env_prefix: str | None) -> str | None:
    """'2022 Meridian Ion M' for a VIN-master row (catalog names filled in), else None."""
    v = vehicles.display_fields(ddb.get_item("DEALER_Vehicles", {"vin": vin}, env_prefix) if vin else None, env_prefix)
    return " ".join(str(x) for x in (v.get("year"), v.get("make"), v.get("model")) if x) or None


def _user_name(uid: str | None, users: dict[str, dict[str, Any]]) -> str | None:
    u = users.get(uid) if uid else None
    return (" ".join(filter(None, [u.get("given_name"), u.get("family_name")])) or uid) if u else uid


def _active_journey(customer_id: str | None, env_prefix: str | None) -> dict[str, Any] | None:
    """The customer's one active purchase journey (DISCOVER–DELIVER), newest first; None when every
    journey is resting in OWN or closed."""
    if not customer_id:
        return None
    rows = [j for j in ddb.scan_filter(_JOURNEYS, "customer_id", customer_id, env_prefix)
            if (j.get("status") or "active") == "active" and j.get("stage") in _ACTIVE_STAGES]
    return sorted(rows, key=lambda j: j.get("updated_at") or j.get("created_at") or "", reverse=True)[0] if rows else None


def _sla_minutes(market: str, env_prefix: str | None) -> int:
    try:
        from ..admin import config_store
        v = (config_store.get("tenant", market, env_prefix).get("content") or {}).get("lead_sla_minutes")
        return int(v) if v else _DEFAULT_SLA_MINUTES
    except Exception:
        return _DEFAULT_SLA_MINUTES


def _in_scope(scope: dict[str, Any], row: dict[str, Any]) -> bool:
    if scope.get("is_oem"):
        return True
    return row.get("dealer_id") in (scope.get("dealer_ids") or set())


def _bucket(lead: dict[str, Any], sla: dict[str, Any] | None) -> str:
    status = (lead.get("status") or "").lower()
    if status in ("won", "lost", "closed"):
        return "closed"
    if status in ("working", "worked", "qualified"):
        return "worked"
    if not lead.get("first_response_at"):
        # not yet responded — new, unless the SLA clock already breached
        if sla and sla.get("breached"):
            return "breached"
        return "new"
    if sla and sla.get("breached"):
        return "breached"
    return "responded_in_sla"


def build_lead_desk(scope: dict[str, Any], market_id: str | None = None,
                    env_prefix: str | None = None) -> dict[str, Any]:
    """The BDC lead queue for the caller's dealer scope (spec §5.4/§5.18)."""
    market = market_id or scope.get("market_id") or "us"
    leads = [l for l in ddb.scan_filter(_LEADS, "market_id", market, env_prefix) if _in_scope(scope, l)]
    sla_by_lead = {s.get("lead_id"): s for s in ddb.scan_all(_LEAD_SLA, env_prefix)}

    custs = {c["customer_id"]: c for c in ddb.scan_all("DEALER_Customers", env_prefix)}
    models = {m["model_id"]: m.get("name") for m in ddb.scan_all("OEM_CatalogModels", env_prefix)}
    users = {u["user_id"]: u for u in ddb.scan_all("PLAT_Users", env_prefix)}
    # journey facts the desk shows after it acts: stage, owner, a pending handoff, the booked visit
    jids = {l.get("journey_id") for l in leads if l.get("journey_id")}
    journeys = {j["journey_id"]: j for j in ddb.scan_all(_JOURNEYS, env_prefix) if j.get("journey_id") in jids} if jids else {}
    appts: dict[str, dict[str, Any]] = {}
    if jids:
        now_iso = _iso(_now())
        for a in ddb.scan_all("DEALER_Appointments", env_prefix):
            jid = a.get("journey_id")
            if jid in jids and a.get("type") == "test_drive" and (a.get("slot_start") or "") >= now_iso \
                    and a.get("status") not in ("cancelled", "completed"):
                if jid not in appts or (a.get("slot_start") or "") < (appts[jid].get("slot_start") or ""):
                    appts[jid] = a
    buckets: dict[str, list[dict[str, Any]]] = {b: [] for b in _BUCKETS}
    breach_count = resp_minutes = resp_n = 0
    for l in leads:
        sla = sla_by_lead.get(l.get("lead_id"))
        b = _bucket(l, sla)
        j = journeys.get(l.get("journey_id")) or {}
        pending = j.get("pending_handoff") or None
        appt = appts.get(l.get("journey_id"))
        card = {
            "lead_id": l.get("lead_id"), "customer_id": l.get("customer_id"),
            "source": l.get("source"), "status": l.get("status"),
            "dealer_id": l.get("dealer_id"), "received_at": l.get("received_at"),
            "assigned_user_id": l.get("assigned_user_id"),
            "assigned_user_name": _user_name(l.get("assigned_user_id"), users),
            "vehicle_of_interest": l.get("vehicle_of_interest_model_id"),
            # a service heads-up is about one car on the lot's VIN master, not a model of interest
            "vehicle_name": (_vehicle_name(l.get("predicted_vin"), env_prefix) if l.get("predicted_vin") else None)
                            or models.get(l.get("vehicle_of_interest_model_id")),
            "customer_name": " ".join(filter(None, [custs.get(l.get("customer_id"), {}).get("given_name"),
                                                    custs.get(l.get("customer_id"), {}).get("family_name")])) or None,
            "comments": l.get("comments"), "journey_id": l.get("journey_id"),
            "journey_stage": j.get("stage"), "journey_owner_id": j.get("owner_user_id"),
            "journey_owner_name": _user_name(j.get("owner_user_id"), users),
            "pending_handoff": ({"to_user": pending.get("to_user"), "to_user_name": _user_name(pending.get("to_user"), users),
                                 "handoff_id": pending.get("handoff_id"), "created_at": pending.get("created_at")}
                                if pending else None),
            "appointment": ({"appointment_id": appt.get("appointment_id"), "slot_start": appt.get("slot_start"),
                             "slot_end": appt.get("slot_end"), "status": appt.get("status")} if appt else None),
            "first_response_at": l.get("first_response_at"),
            "first_response_channel": l.get("first_response_channel"),
            # a predictive lead is worked as a service booking (no journey, no test drive)
            "service_lead": is_service_lead(l),
            "service_journey_id": l.get("service_journey_id"),
            "service_appointment": l.get("service_appointment"),
            # the Service Agent's prediction, when this lead is one (Lead Desk badge + offer text)
            **({k: l.get(k) for k in ("predicted_part", "predicted_parts", "predicted_also_due", "predicted_due_mileage",
                                      "predicted_odometer", "predicted_overdue", "predicted_vin", "confidence", "offer_pct")}
               if l.get("source") == "predictive_maintenance" else {}),
            "sla_minutes": (sla or {}).get("sla_minutes"),
            "sla_due_at": (sla or {}).get("sla_due_at"),
            "response_minutes": (sla or {}).get("response_minutes"),
            "breached": bool((sla or {}).get("breached")),
        }
        buckets[b].append(card)
        if sla and sla.get("breached"):
            breach_count += 1
        rm = (sla or {}).get("response_minutes")
        if isinstance(rm, (int, float)):
            resp_minutes += rm
            resp_n += 1

    for b in buckets:
        buckets[b].sort(key=lambda c: c.get("received_at") or "")

    total = len(leads)
    # the floor advisors a lead can be handed to (the caller's stores; OEM callers see the market's)
    dealer_ids = set(scope.get("dealer_ids") or [])
    advisors = sorted(
        ({"user_id": u["user_id"], "name": _user_name(u["user_id"], users), "dealer_id": u.get("dealer_id")}
         for u in users.values()
         if "dealer_advisor" in (u.get("groups") or []) and u.get("active", True) and u.get("market_id", market) == market
         and (scope.get("is_oem") or u.get("dealer_id") in dealer_ids)),
        key=lambda a: (a["dealer_id"] or "", a["name"] or ""))
    # service leads go to the service drive, not the sales floor
    service_advisors = sorted(
        ({"user_id": u["user_id"], "name": _user_name(u["user_id"], users), "dealer_id": u.get("dealer_id")}
         for u in users.values()
         if "dealer_service" in (u.get("groups") or []) and u.get("active", True) and u.get("market_id", market) == market
         and (scope.get("is_oem") or u.get("dealer_id") in dealer_ids)),
        key=lambda a: (a["dealer_id"] or "", a["name"] or ""))
    return {
        "market_id": market,
        "scope": {"is_oem": scope.get("is_oem"), "dealer_ids": sorted(scope.get("dealer_ids") or [])},
        "buckets": list(_BUCKETS),
        "queue": buckets,
        "advisors": advisors,
        "service_advisors": service_advisors,
        "counts": {b: len(buckets[b]) for b in _BUCKETS},
        "total": total,
        "kpis": {
            "sla_breach_rate": round(breach_count / total, 3) if total else None,
            "avg_response_minutes": round(resp_minutes / resp_n, 1) if resp_n else None,
            "unworked": len(buckets["new"]) + len(buckets["breached"]),
        },
        "source_ref": "LeadDesk.build (§5.4/§5.18; DEALER_Leads + DEALER_LeadSLA)",
    }


def _clean_str(v: Any, limit: int = 400) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s[:limit] or None


def _resolve_customer(body: dict[str, Any], dealer_id: str, market: str, ts: _dt.datetime,
                      env_prefix: str | None) -> tuple[dict[str, Any], bool]:
    """The customer behind an inbound lead: by id, else by e-mail (CRM dedupe), else a new
    DEALER_Customers row from the form's contact fields. Returns (customer, created)."""
    cid = _clean_str(body.get("customer_id"), 64)
    if cid:
        c = ddb.get_item("DEALER_Customers", {"customer_id": cid}, env_prefix)
        if c is None:
            raise NotFound(f"customer {cid} not found")
        return dict(c), False
    email = (_clean_str(body.get("email"), 160) or "").lower()
    given, family = _clean_str(body.get("given_name"), 80), _clean_str(body.get("family_name"), 80)
    if not email and not (given and family):
        raise InvalidLead("a lead needs customer_id, or an e-mail / a first and last name")
    if email:
        for c in ddb.scan_filter("DEALER_Customers", "market_id", market, env_prefix):
            if (c.get("email") or "").lower() == email:
                return dict(c), False
    new_id = "C-" + market.upper() + "-WEB-" + hashlib.sha1(f"{email}|{given}|{family}|{_iso(ts)}".encode()).hexdigest()[:10].upper()
    addr = {k: _clean_str(body.get(k), 80) for k in ("street", "city", "state", "zip5", "country")}
    addr = {k: v for k, v in addr.items() if v} or None
    if addr and not addr.get("country"):
        addr["country"] = market.upper()
    customer = {
        "customer_id": new_id, "given_name": given, "family_name": family,
        "email": email or None, "phone": _clean_str(body.get("phone"), 40),
        "address": addr, "region_code": (addr or {}).get("state") or _clean_str(body.get("region_code"), 8),
        "market_id": market, "dealer_id": dealer_id, "assigned_advisor_user_id": None,
        "lifestyle_profile": None, "current_vehicle": None, "budget": None, "notes": [],
        "created_at": _iso(ts), "data_class": "C3", "source_ref": f"LeadDesk.create_lead (web intake {_iso(ts)})",
    }
    return customer, True


def create_lead(body: dict[str, Any], actor: str, scope: dict[str, Any],
                env_prefix: str | None = None) -> dict[str, Any]:
    """Inbound lead intake (spec §5.4): a web form, chat transcript, phone enquiry or campaign
    click lands on the Lead Desk with its first-response SLA clock running.

    Resolves the customer (id → e-mail → new row), records what they asked for (vehicle of
    interest, comments, stated budget, the vehicle they would trade) and writes the DEALER_Leads
    row as `new` plus its DEALER_LeadSLA clock (`sla_due_at = received + lead_sla_minutes`).
    A brand-new customer from a form that ticked "contact me" gets a CUST_Consent row for the
    channels it ticked. Emits lead.received and writes an A2 intake trace."""
    source = _clean_str(body.get("source"), 32) or "web_form"
    if source not in INTAKE_SOURCES:
        raise InvalidLead(f"source must be one of {INTAKE_SOURCES}")
    dealer_id = _clean_str(body.get("dealer_id"), 32)
    if not dealer_id:
        ids = sorted(scope.get("dealer_ids") or [])
        if len(ids) != 1:
            raise InvalidLead("dealer_id is required when the caller's scope spans more than one store")
        dealer_id = ids[0]
    market = (_clean_str(body.get("market_id"), 8) or scope.get("market_id") or "us").lower()
    ts = _now()
    customer, customer_created = _resolve_customer(body, dealer_id, market, ts, env_prefix)
    cust_changed = customer_created

    model_id = _clean_str(body.get("vehicle_of_interest_model_id"), 64)
    if model_id and ddb.get_item("OEM_CatalogModels", {"model_id": model_id}, env_prefix) is None:
        raise InvalidLead(f"unknown vehicle_of_interest_model_id {model_id}")
    # what the form said about budget and the current vehicle is the newest signal on file
    budget = body.get("budget")
    if isinstance(budget, dict):
        clean = {k: budget[k] for k in ("price_min", "price_max", "monthly_max", "down")
                 if isinstance(budget.get(k), (int, float)) and not isinstance(budget.get(k), bool)}
        if clean:
            customer["budget"] = {"currency": "EUR" if market == "de" else "USD", **(customer.get("budget") or {}), **clean}
            cust_changed = True
    cur = body.get("current_vehicle")
    if isinstance(cur, dict) and (cur.get("make") or cur.get("model")) and not customer.get("current_vehicle"):
        customer["current_vehicle"] = {
            "make": _clean_str(cur.get("make"), 40), "model": _clean_str(cur.get("model"), 60),
            "year": int(cur["year"]) if isinstance(cur.get("year"), (int, float)) else None,
            "mileage": int(cur["mileage"]) if isinstance(cur.get("mileage"), (int, float)) else None,
            "distance_unit": "km" if market == "de" else "mi",
            "ownership": cur.get("ownership") if cur.get("ownership") in ("owned", "loan", "lease") else "owned",
            "payoff": float(cur["payoff"]) if isinstance(cur.get("payoff"), (int, float)) else 0,
            "months_remaining": int(cur["months_remaining"]) if isinstance(cur.get("months_remaining"), (int, float)) else 0,
        }
        cust_changed = True
    if cust_changed:
        customer["updated_at"] = _iso(ts)
        ddb.put_item("DEALER_Customers", customer, env_prefix)
    if customer_created and body.get("consent", True):
        channels = body.get("consent_channels") or ["email", "sms"]
        ddb.put_item("CUST_Consent", {
            "customer_id": customer["customer_id"], "market_id": market, "status": "opted_in",
            "email_opt_in": "email" in channels, "sms_opt_in": "sms" in channels, "voice_opt_in": "call" in channels,
            "preferred_channel": channels[0] if channels else "email", "updated_at": _iso(ts),
            "source_ref": f"LeadDesk.create_lead consent ({source})"}, env_prefix)

    # time-derived id; a second enquiry from the same customer in the same second gets its own row
    seq = 0
    while True:
        lead_id = "LD-" + market.upper() + "-WEB-" + hashlib.sha1(
            f"{customer['customer_id']}|{source}|{_iso(ts)}|{seq}".encode()).hexdigest()[:10].upper()
        if ddb.get_item(_LEADS, {"lead_id": lead_id}, env_prefix) is None:
            break
        seq += 1
    lead = {
        "lead_id": lead_id, "market_id": market, "dealer_id": dealer_id, "customer_id": customer["customer_id"],
        "source": source, "status": "new", "received_at": _iso(ts), "first_response_at": None,
        "assigned_user_id": None, "journey_id": None,
        "vehicle_of_interest_model_id": model_id, "vehicle_of_interest_trim_id": _clean_str(body.get("trim_id"), 64),
        "comments": _clean_str(body.get("comments"), 600), "campaign_id": _clean_str(body.get("campaign_id"), 64),
        "source_ref": f"LeadDesk.create_lead ({source} intake)",
    }
    ddb.put_item(_LEADS, lead, env_prefix)
    sla_min = _sla_minutes(market, env_prefix)
    sla = {"lead_id": lead_id, "dealer_id": dealer_id, "market_id": market, "sla_minutes": sla_min,
           "sla_due_at": _iso(ts + _dt.timedelta(minutes=sla_min)), "first_response_at": None,
           "response_minutes": None, "breached": False, "source_ref": "LeadDesk.create_lead (SLA clock)"}
    ddb.put_item(_LEAD_SLA, sla, env_prefix)
    _bus.emit("lead.received", {"lead_id": lead_id, "customer_id": customer["customer_id"], "dealer_id": dealer_id,
                                "source": source, "vehicle_of_interest_model_id": model_id,
                                "sla_due_at": sla["sla_due_at"]},
              actor=actor, resource_url=f"/dealer/leads?lead={lead_id}", env_prefix=env_prefix)
    _trace.write_trace("a2.lead_intake", agent_id="A2", market=market,
                       routing_decision=f"{source} lead → Lead Desk, SLA {sla_min} min",
                       extra={"lead_id": lead_id, "dealer_id": dealer_id, "source": source,
                              "customer_created": customer_created, "user_id": actor})
    return {"lead": lead, "sla": sla, "customer_id": customer["customer_id"], "customer_created": customer_created,
            "customer_name": " ".join(filter(None, [customer.get("given_name"), customer.get("family_name")])) or None,
            "source_ref": "LeadDesk.create_lead"}


def assign_lead(lead_id: str, assignee: str, actor: str = "bdc",
                env_prefix: str | None = None) -> dict[str, Any]:
    """Assign a lead and create/link a DISCOVER purchase journey (A2 create_journey).

    Sets the lead's assigned_user_id + status=working, then creates a journey in
    DISCOVER (source=lead_desk) linked to the lead's customer, writing the
    journey.created event. If the customer already has an active journey
    (DISCOVER–DELIVER), links to it; a journey resting in OWN is never re-opened —
    the new one records it as previous_journey_id (spec §2.1 repeat purchase)."""
    lead = ddb.get_item(_LEADS, {"lead_id": lead_id}, env_prefix)
    if lead is None:
        raise NotFound(f"lead {lead_id} not found")
    if is_service_lead(lead):
        return _assign_service_lead(lead, assignee, actor, env_prefix)

    ts = _now()
    customer_id = lead.get("customer_id")
    market = lead.get("market_id") or "us"

    # reuse the customer's active journey if present, else create
    existing = _active_journey(customer_id, env_prefix)
    if existing:
        journey = dict(existing)
        created = False
        # the lead's request rides along even when the journey already existed
        ctx = dict(journey.get("context") or {})
        ctx.setdefault("lead_id", lead_id)
        if lead.get("comments"):
            ctx["lead_comments"] = lead["comments"]
        if lead.get("vehicle_of_interest_model_id"):
            ctx["requested_model_id"] = lead["vehicle_of_interest_model_id"]
            if not journey.get("primary_model_id"):
                journey["primary_model_id"] = lead["vehicle_of_interest_model_id"]
        journey["context"] = ctx
        journey["updated_at"] = _iso(ts)
        ddb.put_item(_JOURNEYS, journey, env_prefix)
    else:
        assignee_is_actor = assignee == actor
        prior = sorted([j for j in ddb.scan_filter(_JOURNEYS, "customer_id", customer_id, env_prefix)],
                       key=lambda j: j.get("updated_at") or "", reverse=True) if customer_id else []
        jid = f"J-{market.upper()}-LEAD-{lead_id.split('-')[-1]}"
        journey = {
            "journey_id": jid, "stage": "DISCOVER", "source": "lead_desk", "status": "active",
            "customer_id": customer_id, "dealer_id": lead.get("dealer_id"), "market_id": market,
            # created by the desk; the floor advisor takes ownership via the handoff below
            "owner_user_id": actor if not assignee_is_actor else assignee,
            "primary_model_id": lead.get("vehicle_of_interest_model_id"),
            "primary_trim_id": lead.get("vehicle_of_interest_trim_id"),
            "previous_journey_id": prior[0]["journey_id"] if prior else None,
            "context": {"lead_id": lead_id, "lead_source": lead.get("source"),
                        **({"lead_comments": lead["comments"]} if lead.get("comments") else {}),
                        **({"requested_model_id": lead["vehicle_of_interest_model_id"]}
                           if lead.get("vehicle_of_interest_model_id") else {})},
            "created_at": _iso(ts), "updated_at": _iso(ts), "last_event_at": _iso(ts),
            "source_ref": f"LeadDesk.assign_lead:{lead_id}",
        }
        ddb.put_item(_JOURNEYS, journey, env_prefix)
        # journey.created event
        ev = journey_supervisor._event_row(jid, "journey.created", actor,
                                           f"created from lead {lead_id}",
                                           {"lead_id": lead_id, "source": "lead_desk"}, _iso(ts))
        ddb.put_item(_EVENTS, ev, env_prefix)
        created = True

    # update the lead: assign + mark working + first response now (if unset)
    lead = dict(lead)
    lead["assigned_user_id"] = assignee
    lead["status"] = "working"
    if not lead.get("first_response_at"):
        lead["first_response_at"] = _iso(ts)
    lead["journey_id"] = journey["journey_id"]
    ddb.put_item(_LEADS, lead, env_prefix)

    handoff = None
    if journey.get("owner_user_id") != assignee:
        from ..journeys import handoffs
        handoff = handoffs.create_handoff(journey["journey_id"], assignee, from_user=actor,
                                          note=f"Lead {lead_id} ({lead.get('source')}) → floor",
                                          env_prefix=env_prefix)
    if created:
        _bus.emit("journey.created", {"journey_id": journey["journey_id"], "customer_id": customer_id,
                                      "dealer_id": lead.get("dealer_id"), "source": "lead_desk", "lead_id": lead_id},
                  actor=actor, resource_url=f"/r/journey/{journey['journey_id']}", env_prefix=env_prefix)
    return {
        "handoff_id": (handoff or {}).get("handoff_id"), "task_id": (handoff or {}).get("task_id"),
        "lead_id": lead_id, "assigned_user_id": assignee,
        "journey_id": journey["journey_id"], "journey_created": created,
        "journey_stage": journey.get("stage"),
        "source_ref": "LeadDesk.assign_lead",
    }


def is_service_lead(lead: dict[str, Any] | None) -> bool:
    """A lead the Service Agent raised (predicted part wear on an owned car). It is worked as
    a service booking: never a purchase journey, never a test drive."""
    return bool(lead) and lead.get("source") == "predictive_maintenance"


def _assign_service_lead(lead: dict[str, Any], assignee: str, actor: str,
                         env_prefix: str | None) -> dict[str, Any]:
    """Assign a predictive service lead: the service advisor owns it and books the visit.
    No purchase journey is opened; a hand-to-someone-else leaves them a task."""
    ts = _iso(_now())
    lead = dict(lead)
    lead.update({"assigned_user_id": assignee, "status": "working"})
    if not lead.get("first_response_at"):
        lead["first_response_at"] = ts
    ddb.put_item(_LEADS, lead, env_prefix)
    task = None
    if assignee != actor:
        from ..platform import tasks
        part = lead.get("predicted_part") or "predicted service"
        task = tasks.create_task(f"Book a service visit: {part} (lead {lead['lead_id']})", assignee,
                                 created_by=actor, dealer_id=lead.get("dealer_id"), sla_hours=24,
                                 source_agent="lead_desk", env_prefix=env_prefix)
    return {"lead_id": lead["lead_id"], "assigned_user_id": assignee, "service_lead": True,
            "journey_id": None, "journey_created": False, "handoff_id": None,
            "task_id": (task or {}).get("task_id"), "next": "book_service",
            "source_ref": "LeadDesk.assign_lead (predictive service lead → service booking)"}


def book_service_for_lead(lead_id: str, actor: str, slot_start: str | None = None,
                          wait_type: str = "drop", env_prefix: str | None = None) -> dict[str, Any]:
    """Book the service visit a predictive lead asks for (A7 book_service): the predicted parts
    become the visit's services, on the car the prediction is about."""
    from ..service import booking
    lead = ddb.get_item(_LEADS, {"lead_id": lead_id}, env_prefix)
    if lead is None:
        raise NotFound(f"lead {lead_id} not found")
    if not is_service_lead(lead):
        raise ValueError("not a service lead — book a test drive on its journey instead")
    vin = lead.get("predicted_vin")
    if not vin:
        raise ValueError("the lead names no vehicle to book")
    def _part(p: Any) -> str | None:
        # predicted_also_due holds records ({"part": …, "miles_remaining": …}); a service is its name
        return str(p.get("part") or "") or None if isinstance(p, dict) else (str(p) if p else None)
    services: list[str] = []
    for p in [*(lead.get("predicted_parts") or [lead.get("predicted_part")]), *(lead.get("predicted_also_due") or [])]:
        name = _part(p)
        if name and name not in services:
            services.append(name)
    from ..service import predictive
    booked = _booked_visit(vin, env_prefix)
    if booked and not slot_start:
        # the customer (or a colleague) already booked this car: add the predicted work to that
        # visit at its booked time — never move a slot someone chose
        sj, appt = booked
        merged = list(dict.fromkeys([*(sj.get("services") or []), *services]))
        ddb.put_item("DEALER_ServiceJourneys", {**sj, "services": merged, "updated_at": _iso(_now())}, env_prefix)
        ddb.put_item("DEALER_Appointments", {**appt, "services": merged}, env_prefix)
        out = {"service_journey_id": sj["service_journey_id"], "appointment_id": appt["appointment_id"],
               "slot_start": appt.get("slot_start"), "services": merged, "vin": vin, "already_booked": True}
    else:
        out = booking.book_service(vin, services=services or None, wait_type=wait_type,
                                   preferred_slot=slot_start, actor=actor, env_prefix=env_prefix)
    predictive.link_booking(vin, out, "lead_desk", env_prefix)
    lead = dict(ddb.get_item(_LEADS, {"lead_id": lead_id}, env_prefix) or lead)
    if not lead.get("assigned_user_id"):
        lead["assigned_user_id"] = actor
        ddb.put_item(_LEADS, lead, env_prefix)
    return {**out, "lead_id": lead_id, "type": "service",
            "source_ref": "LeadDesk.book_service_for_lead (A7.book_service)"}


def _booked_visit(vin: str, env_prefix: str | None) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The car's open visit that already has a booked service appointment, if any."""
    for sj in ddb.scan_filter("DEALER_ServiceJourneys", "vin", vin, env_prefix):
        if sj.get("stage") != "BOOK":
            continue
        appt = next((a for a in ddb.scan_filter("DEALER_Appointments", "service_journey_id", sj["service_journey_id"],
                                               env_prefix)
                     if a.get("type") == "service" and a.get("status") == "booked"), None)
        if appt:
            return sj, appt
    return None


_CHANNELS = ("email", "sms", "call")


def respond_to_lead(lead_id: str, channel: str, actor: str, body: str | None = None,
                    env_prefix: str | None = None) -> dict[str, Any]:
    """First response to a lead through the active OutreachProvider. A call is
    logged as a task for the caller (the dialer is outside Retail Ops). Stamps the
    lead's first_response_at and updates its SLA row with the response time."""
    if channel not in _CHANNELS:
        raise ValueError(f"channel must be one of {_CHANNELS}")
    lead = ddb.get_item(_LEADS, {"lead_id": lead_id}, env_prefix)
    if lead is None:
        raise NotFound(f"lead {lead_id} not found")
    from ..agents import assistant
    from ..platform import outreach, tasks
    script = assistant.first_response_script(lead, env_prefix)
    text = body or f"{script['opening']} {script['body']}"
    cust = ddb.get_item("DEALER_Customers", {"customer_id": lead.get("customer_id")}, env_prefix) or {}
    if channel == "call":
        t = tasks.create_task(f"Call lead {lead_id}", actor, created_by=actor, journey_id=lead.get("journey_id"),
                              dealer_id=lead.get("dealer_id"), sla_hours=1, source_agent="lead_desk",
                              env_prefix=env_prefix)
        sent = {"sent": False, "channel": "call", "task_id": t["task_id"]}
    else:
        to = cust.get("email") if channel == "email" else cust.get("phone")
        sent = outreach.get_outreach_provider().send(channel, to, "Thanks for your enquiry", text,
                                                     customer_id=lead.get("customer_id"),
                                                     dealer_id=lead.get("dealer_id"), env_prefix=env_prefix)
    now = _now()
    lead = dict(lead)
    first = not lead.get("first_response_at")
    if first:
        lead["first_response_at"] = _iso(now)
        lead["first_response_channel"] = channel
        lead["first_response_by"] = actor
    if (lead.get("status") or "new") == "new":
        lead["status"] = "working"
    ddb.put_item(_LEADS, lead, env_prefix)
    sla = ddb.scan_filter(_LEAD_SLA, "lead_id", lead_id, env_prefix)
    if first and sla:
        row = dict(sla[0])
        recv = lead.get("received_at")
        try:
            mins = round((now - _dt.datetime.fromisoformat(recv.replace("Z", "+00:00"))).total_seconds() / 60)
        except Exception:
            mins = None
        row.update({"first_response_at": lead["first_response_at"], "response_minutes": mins,
                    "breached": bool(mins is not None and row.get("sla_minutes") and mins > row["sla_minutes"])})
        ddb.put_item(_LEAD_SLA, row, env_prefix)
    if sent.get("message_id"):
        _bus.emit("message.outbound", {"lead_id": lead_id, "journey_id": lead.get("journey_id"),
                                       "customer_id": lead.get("customer_id"), "dealer_id": lead.get("dealer_id"),
                                       "channel": channel, "message_id": sent.get("message_id")},
                  actor=actor, env_prefix=env_prefix)
    return {"lead_id": lead_id, "channel": channel, "first_response": first, **sent,
            "first_response_at": lead.get("first_response_at")}
