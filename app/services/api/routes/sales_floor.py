"""Sales floor routes (spec §6; screens §5.3–§5.6, §5.8): Advisor, Discovery,
Trade-In, Assistant, Lead Desk, Service Lane, Customer 360.

  GET  /advisor/today                                  picker: the caller's active journeys + today's appointments
  GET  /journeys/{id}/advisor                          one-call Advisor read model (sections fail independently)
  GET  /journeys/{id}/tco?years=&annual_distance=      A1 build_tco_comparison
  GET  /journeys/{id}/lifecycle                        A3 get_lifecycle_context + get_recent_activity
  POST /journeys/{id}/present                          vehicle_presented event + accept recommendation
  POST /journeys/{id}/objection                        A3 classify_objection + strategy + shown recommendation
  POST /journeys/{id}/objection/outcome                A3 record_outcome (Use This / Alternative)
  POST /journeys/{id}/outreach                         NBA one-click: follow_up | finance_link | test_drive_link | callback
  POST /journeys/{id}/trade                            value + attach the trade to the journey
  POST /journeys/bulk/nudge                            "Nudge stalled" over the selected journeys
  POST /discovery/shortlist                            A1 lifestyle profile + ranked trims
  GET  /configurations/options?model=                  configurator (sandbox)
  GET  /configurations/similar?model=                  A1 get_similar_vehicles
  POST /configurations                                 A1 record_shortlist (save build & hand off)
  POST /trade-in/valuation                             A4 get_trade_valuation (by vin | customer_id)
  GET  /trade-in/prospects?dealer=                     A4 find_upgrade_prospects
  POST /trade-in/payment-delta                         A4 estimate_payment_delta
  POST /assistant/suggestions                          A3 generate_contextual_suggestions
  POST /assistant/chat                                 A3 cited chat ({"mode":"agent"} -> reasoning agent)
  POST /assistant/agent                                A3 reasoning agent (Strands on Bedrock; tools = agents.*)
  POST /assistant/flows/{flow}                         follow-up-draft | service-upsell | call-list
  GET  /assistant/shift-list · POST /assistant/shift-list/publish
  POST /leads (web-form / chat / phone intake) · GET /leads/queue · GET /leads/{id}/script
  POST /leads/{id}/respond · POST /leads/{id}/assign · POST /leads/{id}/appointment
  GET  /service/lane?date=                             Service Lane read view
  GET  /customers/{id}                                 Customer 360 (C4 fields never returned)
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any

from ...agents import a3_agent, assistant, configurator, deal, discovery, equity, intel, objection, prospects, tco, trade
from ...agents import suggestions as a3_suggestions
from ...common import authz, ddb
from ...dealer import leads
from ...journeys import actions, supervisor
from ...platform import recommendations as recs
from .. import routing as r

SALES = authz.ADVISOR_READ_ROLES
LANE = frozenset({"dealer_advisor", "dealer_bdc", "dealer_service", "dealer_service_manager", "dealer_manager"})
LEAD_ROLES = frozenset({"dealer_bdc", "dealer_advisor", "dealer_manager"})
TRADE_IN = SALES | {"dealer_service"}  # valuation + upgrade prospects: §5.2 gives the service advisor Trade-In
_C4 = ("credit_score", "ssn", "ssn_last4", "income", "fico", "date_of_birth")


def _sales(claims):
    r.roles(claims, SALES, allow_oem=True)


def _sales_write(claims):
    r.roles(claims, SALES)


def _journey(claims, jid):
    _sales(claims)
    return r.journey_in_scope(claims, jid)


def _safe(fn, *a, **k) -> Any:
    try:
        return fn(*a, **k)
    except Exception as e:   # one panel failing must not blank the Advisor
        return {"error": f"{type(e).__name__}: {e}"}


def _shown(agent, action, claims, j, context=None, reason=None):
    try:
        return recs.record_shown(agent, action, shown_to=claims.get("role") or "staff", journey_id=j["journey_id"],
                                 dealer_id=j.get("dealer_id"), context_hash=context, reason=reason)["recommendation_id"]
    except Exception:
        return None


# --- Advisor -------------------------------------------------------------------------

def _advisor_today(event, claims, _m):
    _sales(claims)
    today = _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    mine = r.qs(event).get("all") != "1"
    rows = [j for j in r.scoped_rows(claims, ddb.scan_all("DEALER_Journeys"))
            if j.get("stage") != "OWN" and (j.get("status") or "active") == "active"
            and (not mine or j.get("owner_user_id") == claims["user_id"])]
    custs = {c["customer_id"]: c for c in ddb.scan_all("DEALER_Customers")}
    appts = {}
    for a in ddb.scan_all("DEALER_Appointments"):
        if a.get("journey_id") and (a.get("slot_start") or "")[:10] == today:
            appts.setdefault(a["journey_id"], a)
    out = []
    for j in rows:
        c = custs.get(j.get("customer_id"), {})
        out.append({"journey_id": j["journey_id"], "stage": j.get("stage"), "dealer_id": j.get("dealer_id"),
                    "customer_name": " ".join(filter(None, [c.get("given_name"), c.get("family_name")])) or j.get("customer_id"),
                    "stalled": bool(j.get("stalled")), "idle_days": j.get("idle_days"), "source": j.get("source"),
                    "appointment": ({k: appts[j["journey_id"]].get(k) for k in ("type", "slot_start")}
                                    if j["journey_id"] in appts else None)})
    out.sort(key=lambda x: (x["appointment"] is None, -(1 if x["stalled"] else 0), x["journey_id"]))
    return 200, {"mine": mine, "count": len(out), "journeys": out[:200]}


def _advisor(event, claims, m):
    j = _journey(claims, m.group(1))
    jid = j["journey_id"]
    from ...journeys import assist
    # Independent sections run concurrently (several call Bedrock in AWS), so the
    # page meets the §11 #3 budget (< 6 s p95) without one slow section gating all.
    from concurrent.futures import ThreadPoolExecutor
    jobs = {
        "state": (supervisor.get_journey_state, (jid,)),
        "assist": (assist.build_assist, (jid,)),
        "matches": (discovery.rank_vehicles, (jid,)),
        "suggestions": (a3_suggestions.generate_contextual_suggestions, (j, "sales")),
        "lifecycle": (assistant.get_lifecycle_context, (j,)),
        "recent_activity": (assistant.get_recent_activity, (j.get("customer_id"), jid, 8)),
        "tco": (tco.build_tco_comparison, (jid,)),
        "equity": (equity.build_equity, (jid,)),
        "deal": (deal.structure_deal, (j,)),
        "cross_sell": (deal.get_cross_sell, (j,)),
        "incentives": (intel.get_incentives, (j,)),
        "competitive": (intel.get_competitive_intel, (j,)),
        "payment_delta": (trade.estimate_payment_delta, (jid,)),
    }
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {k: pool.submit(_safe, fn, *args) for k, (fn, args) in jobs.items()}
        res = {k: f.result() for k, f in futs.items()}
    state = res["state"] if "error" not in (res["state"] or {}) else supervisor.get_journey_state(jid)
    assist_m, matches, sug = res["assist"], res["matches"], res["suggestions"]
    nba = state.get("next_best_action") or {}
    out = {
        "journey": {**{k: j.get(k) for k in ("journey_id", "stage", "customer_id", "dealer_id", "owner_user_id",
                                             "market_id", "source", "stalled", "idle_days", "primary_model_id", "vin")},
                    "owner_name": state.get("owner_name"), "pending_handoff": state.get("pending_handoff")},
        "state": state,
        "customer_360": (assist_m or {}).get("customer_360") if isinstance(assist_m, dict) else None,
        "summary": (assist_m or {}).get("summary") if isinstance(assist_m, dict) else None,
        "matches": matches,
        "inventory": None,
        "next_best_action": nba,
        "recommendation_ids": {},
        **{k: res[k] for k in ("lifecycle", "recent_activity", "tco", "suggestions", "equity", "deal",
                               "cross_sell", "incentives", "competitive", "payment_delta")},
    }
    top = ((matches or {}).get("matches") or [None])[0] if isinstance(matches, dict) else None
    if top and top.get("model_id"):
        out["inventory"] = _safe(discovery.search_inventory, top["model_id"], top.get("trim_id"), j.get("dealer_id"),
                                 j.get("market_id") or "us")
    ids = out["recommendation_ids"]
    if nba.get("chosen"):
        ids["nba"] = _shown("A2", nba["chosen"]["action"], claims, j, nba.get("context_hash"), nba["chosen"].get("reason"))
    for s in (sug.get("suggestions") or [])[:3] if isinstance(sug, dict) else []:
        ids[f"suggestion:{s['action']}"] = _shown("A3", s["action"], claims, j, None, s.get("why"))
    for mt in ((matches or {}).get("matches") or [])[:3] if isinstance(matches, dict) else []:
        ids[f"match:{mt['trim_id']}"] = _shown("A1", f"present:{mt['trim_id']}", claims, j, None, "vehicle match")
    return 200, out


def _tco(event, claims, m):
    j = _journey(claims, m.group(1))
    q = r.qs(event)
    return 200, tco.build_tco_comparison(j["journey_id"], years=int(q.get("years") or 5),
                                         annual_distance=float(q["annual_distance"]) if q.get("annual_distance") else None)


def _lifecycle(event, claims, m):
    j = _journey(claims, m.group(1))
    return 200, {"lifecycle": assistant.get_lifecycle_context(j),
                 "recent_activity": assistant.get_recent_activity(j.get("customer_id"), j["journey_id"], 20)}


def _present(event, claims, m):
    _sales_write(claims)
    j = r.journey_in_scope(claims, m.group(1))
    b = r.body(event)
    r.require(b, "trim_id")
    ts = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    # the activity feed reads this evidence: the trim's name, not its catalog code
    trim = ddb.get_item("OEM_Catalog", {"trim_id": b["trim_id"]}) or {}
    model = ddb.get_item("OEM_CatalogModels", {"model_id": trim.get("model_id") or b.get("model_id")}) or {} \
        if (trim.get("model_id") or b.get("model_id")) else {}
    shown = " ".join(x for x in (model.get("name"), trim.get("trim_name")) if x) or "the recommended trim"
    ev = supervisor._event_row(j["journey_id"], "vehicle_presented", claims["user_id"],
                               f"presented {shown}{' VIN ' + b['vin'] if b.get('vin') else ''}",
                               {k: b.get(k) for k in ("model_id", "trim_id", "vin")}, ts)
    ddb.put_item("DEALER_JourneyEvents", ev)
    rid = b.get("recommendation_id")
    if rid:
        try:
            recs.accept(rid, claims["user_id"])
        except (recs.NotFound, recs.InvalidState):
            pass
    # The presented trim is the build: save it (PLAT_Configurations) so the Discover and
    # Configure gates read as met and Trade-In has a build to compare against. The stage
    # is left for the advisor to advance (advance=False).
    build = None
    model_id = b.get("model_id") or next(
        (t.get("model_id") for t in ddb.scan_filter("OEM_Catalog", "trim_id", b["trim_id"]) if t.get("model_id")), None)
    if model_id:
        try:
            saved = configurator.record_shortlist(j["journey_id"], model_id, b["trim_id"], claims["user_id"],
                                                  b.get("packages"), b.get("color"), advance=False)
            build = saved["configuration"]["config_id"]
        except configurator.NotFound:
            build = None   # a trim the configurator does not offer: the event still records it
    return 200, {"journey_id": j["journey_id"], "event_id": ev["event_id"], "presented": b.get("trim_id"),
                 "recommendation_id": rid, "config_id": build}


def objection_card(res, claims, j, utterance):
    """On a detected objection, add the strategy's success rate and the "shown"
    recommendation that Use This / Alternative record against. POST /objection and
    the SSE floor route (handler.py, /utterance-sse) both return these fields."""
    if res.get("objection_detected"):
        strat = assistant.get_objection_strategy(res["objection_type"], j.get("market_id") or "us") or {}
        res["success_rate"] = strat.get("success_rate")
        res["success_basis"] = strat.get("success_basis")
        res["recommendation_id"] = _shown("A3", f"objection:{res['objection_type']}", claims, j, None, utterance[:120])
    return res


def _objection(event, claims, m):
    j = _journey(claims, m.group(1))
    b = r.body(event)
    r.require(b, "utterance")
    res = objection.classify_objection(j["journey_id"], b["utterance"])
    return 200, objection_card(res, claims, j, b["utterance"])


def _objection_outcome(event, claims, m):
    _sales_write(claims)
    j = r.journey_in_scope(claims, m.group(1))
    b = r.body(event)
    r.require(b, "outcome")
    return 200, assistant.record_outcome(j["journey_id"], b.get("strategy_id"), b["outcome"], claims["user_id"],
                                         b.get("recommendation_id"))


def _outreach(event, claims, m):
    _sales_write(claims)
    j = r.journey_in_scope(claims, m.group(1))
    b = r.body(event)
    r.require(b, "kind")
    return 200, assistant.send_outreach(j["journey_id"], b["kind"], b.get("channel") or "email", claims["user_id"],
                                        body=b.get("body"), subject=b.get("subject"),
                                        recommendation_id=b.get("recommendation_id"))


def _journey_trade(event, claims, m):
    _sales_write(claims)
    j = r.journey_in_scope(claims, m.group(1))
    b = r.body(event)
    val = trade.get_trade_valuation(j["customer_id"], b.get("mileage"), b.get("condition"), persist=True)
    return 200, {"valuation": val, **trade.add_trade_to_journey(j["journey_id"], val, claims["user_id"])}


_REPLAY = {
    "price_too_high": "Honestly, the price is more than we wanted to spend.",
    "range_anxiety": "I'm worried about the range on our weekend road trips.",
    "payment_too_high": "What would the monthly payment look like? It needs to stay manageable.",
    "trade_value_low": "I was hoping to get more for my trade-in.",
    "competitor_better": "The other dealer had a competitor model that seemed better equipped.",
    "interest_rate": "The interest rate seems high compared to what my bank offers.",
    "timing_not_ready": "We're not ready to decide today, maybe in a few weeks.",
}


def _replay_script(event, claims, m):
    """Transcript-replay demo mode (synthetic-data plan D1): scripted customer
    lines that exercise the market's objection library for this journey."""
    j = _journey(claims, m.group(1))
    lib = {s.get("objection_type") for s in ddb.scan_filter("DEALER_ObjectionStrategies", "market_id", j.get("market_id") or "us")}
    lines = ["Thanks for showing us around — we like what we've seen so far."]
    lines += [t for k, t in _REPLAY.items() if k in lib][:4]
    return 200, {"journey_id": j["journey_id"], "lines": lines, "interval_ms": 3500,
                 "note": "demo replay — scripted lines matched to the objection library", "language": "en-US"}


def _bulk_nudge(event, claims, _m):
    _sales_write(claims)
    b = r.body(event)
    ids = b.get("journey_ids") or []
    if not ids or len(ids) > 50:
        raise r.Bad(400, "journey_ids must list 1–50 journeys")
    results = []
    for jid in ids:
        try:
            j = r.journey_in_scope(claims, jid)
            out = assistant.send_outreach(jid, "follow_up", b.get("channel") or "email", claims["user_id"])
            results.append({"journey_id": jid, "ok": True, **{k: out.get(k) for k in ("sent", "message_id", "fallback_task", "reason")}})
        except r.Bad as e:
            results.append({"journey_id": jid, "ok": False, "error": e.msg})
        except Exception as e:
            results.append({"journey_id": jid, "ok": False, "error": f"{type(e).__name__}: {e}"})
    return 200, {"count": len(results), "sent": sum(1 for x in results if x.get("ok")), "results": results}


# --- Discovery / configurations -----------------------------------------------------

def _shortlist(event, claims, _m):
    _sales(claims)
    b = r.body(event)
    jid, cid = b.get("journey_id"), b.get("customer_id")
    market_id = None
    if jid:
        j = r.journey_in_scope(claims, jid)
        cid = cid or j.get("customer_id")
        market_id = j.get("market_id")
    elif cid:
        cust = ddb.get_item("DEALER_Customers", {"customer_id": cid})
        if cust is None:
            raise r.Bad(404, f"customer {cid} not found")
        r.dealer_in_scope(claims, cust.get("dealer_id"))
    out = configurator.shortlist(cid, b.get("answers"), jid)
    if not market_id and cid:
        market_id = (ddb.get_item("DEALER_Customers", {"customer_id": cid}) or {}).get("market_id")
    out["market_id"] = market_id
    if b.get("need"):
        out["need"] = b["need"]
    return 200, out


def _config_options(event, claims, _m):
    _sales(claims)
    model = r.qs(event).get("model")
    if not model:
        raise r.Bad(400, "query must include model")
    return 200, configurator.get_configurator_options(model)


def _config_similar(event, claims, _m):
    _sales(claims)
    model = r.qs(event).get("model")
    if not model:
        raise r.Bad(400, "query must include model")
    return 200, {"model_id": model, "similar": configurator.get_similar_vehicles(model)}


def _config_save(event, claims, _m):
    _sales_write(claims)
    b = r.body(event)
    r.require(b, "journey_id", "model_id", "trim_id")
    r.journey_in_scope(claims, b["journey_id"])
    return 201, configurator.record_shortlist(b["journey_id"], b["model_id"], b["trim_id"], claims["user_id"],
                                              b.get("packages"), b.get("color"))


# --- Trade-in ------------------------------------------------------------------------

def _valuation(event, claims, _m):
    r.roles(claims, TRADE_IN, allow_oem=True)
    b = r.body(event)
    cid, vehicle = b.get("customer_id"), None
    if b.get("vin"):
        vehicle = trade.lookup_vehicle(b["vin"].strip().upper())
        cid = vehicle.get("customer_id")
    if not cid:
        raise r.Bad(400, "body must include vin or customer_id")
    cust = ddb.get_item("DEALER_Customers", {"customer_id": cid}) or {}
    r.dealer_in_scope(claims, (vehicle or {}).get("dealer_id") or cust.get("dealer_id"))
    # by VIN: value THIS car (an owner's latest valuation may be a different car they traded)
    val = trade.get_trade_valuation(cid, b.get("mileage"), b.get("condition"),
                                    persist=bool(b.get("persist")) and claims.get("role") in SALES,
                                    vin=(vehicle or {}).get("vin"))
    journeys = [j["journey_id"] for j in ddb.scan_filter("DEALER_Journeys", "customer_id", cid)
                if j.get("stage") != "OWN" and (j.get("status") or "active") == "active"]
    return 200, {**val, "owned_vehicle": vehicle, "loan_lease": trade.get_loan_lease_status(cid),
                 "customer_name": " ".join(filter(None, [cust.get("given_name"), cust.get("family_name")])) or cid,
                 "active_journeys": journeys}


def _prospects(event, claims, _m):
    r.roles(claims, TRADE_IN, allow_oem=True)
    q = r.qs(event)
    dealer = q.get("dealer")
    if dealer:
        r.dealer_in_scope(claims, dealer)
    elif not claims.get("is_oem"):
        dealer = sorted(claims.get("dealer_ids") or [None])[0]
    return 200, prospects.find_upgrade_prospects(dealer, q.get("market") or claims.get("market_id") or "us")


def _payment_delta(event, claims, _m):
    _sales(claims)
    b = r.body(event)
    r.require(b, "journey_id")
    r.journey_in_scope(claims, b["journey_id"])
    return 200, trade.estimate_payment_delta(b["journey_id"], b.get("vehicle_price"),
                                             int(b.get("term_months") or 60), b.get("down"))


# --- Assistant -----------------------------------------------------------------------

def _suggestions(event, claims, _m):
    _sales(claims)
    b = r.body(event)
    r.require(b, "journey_id")
    j = r.journey_in_scope(claims, b["journey_id"])
    role = "service" if claims.get("role") == "dealer_service" else b.get("role") or "sales"
    return 200, a3_suggestions.generate_contextual_suggestions(j, role)


def _chat(event, claims, _m):
    r.roles(claims, SALES | LANE, allow_oem=False)
    b = r.body(event)
    r.require(b, "question")
    if b.get("journey_id"):
        r.journey_in_scope(claims, b["journey_id"])
    if (b.get("mode") or "").lower() == "agent":
        return _agent(event, claims, _m)
    agent_id = (b.get("agent_id") or "A3").upper()
    if agent_id not in {"A1", "A2", "A3", "A4"}:
        raise r.Bad(400, f"unsupported assistant agent {agent_id!r}")
    return 200, assistant.assistant_chat(
        b["question"], b.get("journey_id"), claims.get("role"), agent_id=agent_id, claims=claims)


def _agent(event, claims, _m):
    """A3 reasoning agent (ADR 0002). Same roles/scope as /assistant/chat.
    The agent plans over the deterministic tools; falls back to the one-shot
    path and says so in `engine` when Strands/Bedrock are unavailable. When an
    AgentCore runtime is configured the call is delegated to it (the Lambda
    carries no agent framework), otherwise it runs in-process."""
    r.roles(claims, SALES | LANE, allow_oem=False)
    b = r.body(event)
    r.require(b, "question")
    if b.get("journey_id"):
        r.journey_in_scope(claims, b["journey_id"])
    from ....agentcore import client as _rt
    corr = (event.get("headers") or {}).get("X-Correlation-Id") or (event.get("headers") or {}).get("x-correlation-id")
    if _rt.enabled() and not b.get("local"):
        out = _rt.invoke_agent("A3", b["question"], journey_id=b.get("journey_id"), claims=claims,
                               correlation_id=corr)
        if out is not None:
            return 200, out
    try:
        return 200, a3_agent.run(b["question"], b.get("journey_id"), claims, correlation_id=corr)
    except ValueError as e:
        raise r.Bad(404, str(e))
    except authz.Forbidden as e:
        raise r.Bad(403, str(e))


def _flows(event, claims, m):
    flow = m.group(1)
    b = r.body(event)
    if flow == "follow-up-draft":
        _sales(claims)
        r.require(b, "journey_id")
        r.journey_in_scope(claims, b["journey_id"])
        return 200, assistant.draft_follow_up(b["journey_id"], b.get("channel") or "email")
    if flow == "service-upsell":
        r.roles(claims, LANE)
        r.require(b, "service_journey_id")
        sj = r.service_journey_in_scope(claims, b["service_journey_id"])
        return 200, assistant.service_upsell_brief(sj)
    if flow == "call-list":
        _sales(claims)
        return 200, assistant.build_call_list(claims["user_id"], None)
    raise r.Bad(404, f"unknown assistant flow {flow!r}")


def _shift_list(event, claims, _m):
    _sales(claims)
    team = r.qs(event).get("team") == "1" and claims.get("role") in r.MANAGER_ROLES
    return 200, assistant.build_call_list(None if team else claims["user_id"],
                                          None if claims.get("is_oem") else claims.get("dealer_ids"))


def _shift_publish(event, claims, _m):
    _sales_write(claims)
    return 200, assistant.publish_shift_list(claims["user_id"], claims.get("dealer_ids"), claims["user_id"])


# --- Lead Desk -----------------------------------------------------------------------

def _lead(claims, lead_id):
    r.roles(claims, LEAD_ROLES, allow_oem=True)
    lead = ddb.get_item("DEALER_Leads", {"lead_id": lead_id})
    if lead is None:
        raise r.Bad(404, f"lead {lead_id} not found")
    r.dealer_in_scope(claims, lead.get("dealer_id"))
    return lead


def _lead_queue(event, claims, _m):
    r.roles(claims, LEAD_ROLES, allow_oem=True)
    return 200, leads.build_lead_desk(claims, r.qs(event).get("market"))


def _lead_create(event, claims, _m):
    """Inbound lead intake: the web form / chat / phone enquiry lands on the desk with its SLA
    clock running. Staff key it in (or the web channel posts it) for one of the caller's stores."""
    r.roles(claims, LEAD_ROLES)
    b = r.body(event)
    if b.get("dealer_id"):
        r.dealer_in_scope(claims, b["dealer_id"])
    try:
        return 201, leads.create_lead(b, claims["user_id"], claims)
    except leads.InvalidLead as e:
        raise r.Bad(400, str(e))


def _lead_script(event, claims, m):
    return 200, assistant.first_response_script(_lead(claims, m.group(1)))


def _lead_respond(event, claims, m):
    _lead(claims, m.group(1))
    r.roles(claims, LEAD_ROLES)
    b = r.body(event)
    r.require(b, "channel")
    return 200, leads.respond_to_lead(m.group(1), b["channel"], claims["user_id"], b.get("body"))


def _lead_assign(event, claims, m):
    _lead(claims, m.group(1))
    r.roles(claims, LEAD_ROLES)
    b = r.body(event)
    return 200, leads.assign_lead(m.group(1), b.get("assignee") or claims["user_id"], actor=claims["user_id"])


def _lead_appointment(event, claims, m):
    lead = _lead(claims, m.group(1))
    r.roles(claims, LEAD_ROLES)
    if leads.is_service_lead(lead):
        # a predictive service lead books a service visit on the car — never a test drive
        b = r.body(event)
        try:
            return 200, leads.book_service_for_lead(m.group(1), claims["user_id"], slot_start=b.get("slot_start"),
                                                    wait_type=b.get("wait_type") or "drop")
        except Exception as e:  # ValueError / booking.InvalidWaitType / booking.NotFound; anything else re-raised
            from ...service import booking
            if isinstance(e, booking.NotFound):
                raise r.Bad(404, str(e))
            if isinstance(e, (ValueError, booking.InvalidWaitType)):
                raise r.Bad(400, str(e))
            raise
    if not lead.get("journey_id"):
        raise r.Bad(409, "assign the lead first — booking needs a journey")
    j = r.journey_in_scope(claims, lead["journey_id"])
    b = r.body(event)
    return 200, actions.book_test_drive(j, slot_start=b.get("slot_start"), slot_end=b.get("slot_end"),
                                        actor=claims["user_id"])


# --- Service Lane ----------------------------------------------------------------------

_LOYALTY_AGE_MONTHS = 48


def _vehicle_age_months(vehicle: dict[str, Any]) -> int | None:
    """Months since the owned vehicle went in service (DEALER_Vehicles.delivered_at)."""
    delivered = vehicle.get("delivered_at")
    if not delivered:
        return None
    try:
        d = _dt.datetime.fromisoformat(str(delivered).replace("Z", "+00:00"))
    except ValueError:
        return None
    now = _dt.datetime.now(_dt.timezone.utc)
    return max(0, (now.year - d.year) * 12 + (now.month - d.month))


def _loyalty_offer(flags: list[dict[str, Any]], vehicle: dict[str, Any],
                   valuation: dict[str, Any]) -> dict[str, Any] | None:
    """Owner-loyalty offer for a service-lane customer who qualifies (§5.8 ext).

    Qualifies on a signal already on the record — positive trade equity (A4) or
    an owned vehicle past the loyalty age — so the offer always cites the row it
    came from. No qualifying signal returns None rather than a generic offer."""
    equity_flag = next((f for f in flags if f.get("flag") == "positive_equity"), None)
    if equity_flag:
        return {"name": "Owner loyalty — equity upgrade",
                "label": "Owner loyalty — equity upgrade",
                "detail": "Positive trade equity today; loyalty terms apply to an upgrade from this visit.",
                "amount": equity_flag.get("value"), "currency": equity_flag.get("currency"),
                "basis_code": "positive_equity",
                "ref": equity_flag.get("ref"),
                "source_ref": equity_flag.get("ref")
                or f"DEALER_Valuations:{valuation.get('valuation_id')}"}
    age = _vehicle_age_months(vehicle or {})
    if age is not None and age > _LOYALTY_AGE_MONTHS:
        return {"name": "Owner loyalty — repeat-owner service credit",
                "label": "Owner loyalty — repeat-owner service credit",
                "detail": f"Vehicle in service {age} months (over {_LOYALTY_AGE_MONTHS}); "
                          "loyalty service credit applies on this visit.",
                "basis_code": "vehicle_age_months",
                "vehicle_age_months": age,
                "ref": f"DEALER_Vehicles:{vehicle.get('vin')}.delivered_at",
                "source_ref": f"DEALER_Vehicles:{vehicle.get('vin')}.delivered_at"}
    return None


def _service_lane(event, claims, _m):
    r.roles(claims, LANE, allow_oem=True)
    day = r.qs(event).get("date")
    rows = [s for s in r.scoped_rows(claims, ddb.scan_all("DEALER_ServiceJourneys"))
            if s.get("stage") not in ("COMPLETED",)
            and (not day or (s.get("promised_at") or s.get("opened_at") or "")[:10] <= day)]
    # one card per car in the lane: when a VIN has more than one open visit, the one furthest
    # along (a stale BOOK booking never shadows the visit actually in the bay)
    order = {st: i for i, st in enumerate(("BOOK", "CHECK_IN", "INSPECT", "APPROVE", "REPAIR", "READY", "FOLLOW_UP"))}
    best: dict[str, dict] = {}
    for s in rows:
        k = s.get("vin") or s["service_journey_id"]
        cur = best.get(k)
        if cur is None or (order.get(s.get("stage"), -1), s.get("opened_at") or "") > \
                (order.get(cur.get("stage"), -1), cur.get("opened_at") or ""):
            best[k] = s
    rows = list(best.values())
    custs = {c["customer_id"]: c for c in ddb.scan_all("DEALER_Customers")}
    vehicles = {v["vin"]: v for v in ddb.scan_all("DEALER_Vehicles")}
    names = {m["model_id"]: m.get("name") for m in ddb.scan_all("OEM_CatalogModels")}
    out = []
    for s in rows:
        c = custs.get(s.get("customer_id"), {})
        cv = c.get("current_vehicle") or {}
        # equity in THE CAR IN THE LANE — after a sale the customer's latest valuation is the
        # car they traded in (Priya's $16,554 on the Trek 4), which says nothing about this one
        from ...common import vehicles as _veh
        val = _veh.valuation_for(s.get("vin"), s.get("customer_id")) or {}
        flags = []
        # …and only when the upgrade rule says so (the car is a year old or a lease is ending): the one
        # rule My Garage and the equity monitor apply, so a day-old car never gets an upgrade pitch
        window = _veh.upgrade_window(s.get("customer_id")) if (val.get("equity") or 0) > 0 else None
        if window and window.get("vin") == s.get("vin"):
            flags.append({"flag": "positive_equity", "value": val.get("equity"), "currency": val.get("currency"),
                          "why": window.get("why"), "ref": f"DEALER_Valuations:{val.get('valuation_id')}"})
        if cv.get("ownership") == "lease" and (cv.get("months_remaining") or 99) <= 6:
            flags.append({"flag": "lease_end", "months": cv.get("months_remaining")})
        veh = vehicles.get(s.get("vin"), {})
        if (veh.get("odometer") or cv.get("mileage") or 0) >= 60000:
            flags.append({"flag": "high_mileage", "value": veh.get("odometer") or cv.get("mileage")})
        brief = assistant.service_upsell_brief(s)
        # any active purchase journey counts as "already with sales": the §2.1 single-active
        # rule means a handoff would attach to it rather than open a second one, so showing
        # the button next to an open journey only produces a click that changes nothing
        open_sales = [j["journey_id"] for j in ddb.scan_filter("DEALER_Journeys", "customer_id", s.get("customer_id"))
                      if j.get("stage") != "OWN" and (j.get("status") or "active") == "active"
                      and not j.get("superseded_by")] if s.get("customer_id") else []
        out.append({"loyalty_offer": _loyalty_offer(flags, veh, val),
                    "service_journey_id": s["service_journey_id"], "stage": s.get("stage"), "vin": s.get("vin"),
                    "vehicle_name": names.get(veh.get("model_id")) or " ".join(
                        str(x) for x in (cv.get("year"), cv.get("make"), cv.get("model")) if x) or None,
                    "customer_id": s.get("customer_id"),
                    "customer_name": " ".join(filter(None, [c.get("given_name"), c.get("family_name")])) or None,
                    "dealer_id": s.get("dealer_id"), "advisor_id": s.get("advisor_id"), "market_id": s.get("market_id"),
                    "promised_at": s.get("promised_at"), "wait_type": s.get("wait_type"),
                    "flags": flags, "upsell": brief, "sales_journeys": open_sales})
    out.sort(key=lambda x: (-len(x["flags"]), x.get("promised_at") or ""))
    return 200, {"date": day, "count": len(out), "items": out,
                 "source_ref": "ServiceLane (A7 service journeys + A3 brief + A4 valuation flags)"}


# --- Customer 360 ---------------------------------------------------------------------
def _named_configs(cfgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Saved builds with their model / trim names, so the screen never shows a catalog code."""
    out = []
    for c in cfgs:
        trim = ddb.get_item("OEM_Catalog", {"trim_id": c["trim_id"]}) if c.get("trim_id") else None
        model = ddb.get_item("OEM_CatalogModels", {"model_id": c["model_id"]}) if c.get("model_id") else None
        out.append({**c, "trim_name": (trim or {}).get("trim_name"), "model_name": (model or {}).get("name")})
    return out



def _customer(event, claims, m):
    r.roles(claims, SALES | LANE, allow_oem=True)
    cid = m.group(1)
    c = ddb.get_item("DEALER_Customers", {"customer_id": cid})
    if c is None:
        raise r.Bad(404, f"customer {cid} not found")
    r.dealer_in_scope(claims, c.get("dealer_id"))
    journeys = sorted(ddb.scan_filter("DEALER_Journeys", "customer_id", cid), key=lambda j: j.get("updated_at") or "", reverse=True)
    return 200, {
        "customer": {k: v for k, v in c.items() if k not in _C4},
        "journeys": [{k: j.get(k) for k in ("journey_id", "stage", "status", "source", "dealer_id", "owner_user_id",
                                            "updated_at", "previous_journey_id", "superseded_by")} for j in journeys],
        "vehicles": ddb.scan_filter("DEALER_Vehicles", "customer_id", cid),
        "service_journeys": [{k: s.get(k) for k in ("service_journey_id", "stage", "vin", "promised_at")}
                             for s in ddb.scan_filter("DEALER_ServiceJourneys", "customer_id", cid)],
        "valuation": equity._latest_valuation(cid, None),
        "configurations": _named_configs(ddb.scan_filter("PLAT_Configurations", "customer_id", cid)),
        "recent_activity": assistant.get_recent_activity(cid, journeys[0]["journey_id"] if journeys else None, 15),
        "consent": (ddb.scan_filter("CUST_Consent", "customer_id", cid) or [None])[0],
    }


_J = r"^/journeys/([^/]+)"
ROUTES = [
    ("GET", re.compile(r"^/advisor/today$"), _advisor_today),
    ("POST", re.compile(r"^/journeys/bulk/nudge$"), _bulk_nudge),
    ("GET", re.compile(_J + r"/advisor$"), _advisor),
    ("GET", re.compile(_J + r"/tco$"), _tco),
    ("GET", re.compile(_J + r"/lifecycle$"), _lifecycle),
    ("GET", re.compile(_J + r"/replay-script$"), _replay_script),
    ("POST", re.compile(_J + r"/present$"), _present),
    ("POST", re.compile(_J + r"/objection$"), _objection),
    ("POST", re.compile(_J + r"/objection/outcome$"), _objection_outcome),
    ("POST", re.compile(_J + r"/outreach$"), _outreach),
    ("POST", re.compile(_J + r"/trade$"), _journey_trade),
    ("POST", re.compile(r"^/discovery/shortlist$"), _shortlist),
    ("GET", re.compile(r"^/configurations/options$"), _config_options),
    ("GET", re.compile(r"^/configurations/similar$"), _config_similar),
    ("POST", re.compile(r"^/configurations$"), _config_save),
    ("POST", re.compile(r"^/trade-in/valuation$"), _valuation),
    ("GET", re.compile(r"^/trade-in/prospects$"), _prospects),
    ("POST", re.compile(r"^/trade-in/payment-delta$"), _payment_delta),
    ("POST", re.compile(r"^/assistant/suggestions$"), _suggestions),
    ("POST", re.compile(r"^/assistant/chat$"), _chat),
    ("POST", re.compile(r"^/assistant/agent$"), _agent),
    ("POST", re.compile(r"^/assistant/flows/([a-z-]+)$"), _flows),
    ("GET", re.compile(r"^/assistant/shift-list$"), _shift_list),
    ("POST", re.compile(r"^/assistant/shift-list/publish$"), _shift_publish),
    ("POST", re.compile(r"^/leads$"), _lead_create),
    ("GET", re.compile(r"^/leads/queue$"), _lead_queue),
    ("GET", re.compile(r"^/leads/([^/]+)/script$"), _lead_script),
    ("POST", re.compile(r"^/leads/([^/]+)/respond$"), _lead_respond),
    ("POST", re.compile(r"^/leads/([^/]+)/assign$"), _lead_assign),
    ("PATCH", re.compile(r"^/leads/([^/]+)/assign$"), _lead_assign),
    ("POST", re.compile(r"^/leads/([^/]+)/appointment$"), _lead_appointment),
    ("GET", re.compile(r"^/service/lane$"), _service_lane),
    ("GET", re.compile(r"^/customers/([^/]+)$"), _customer),
]

ERRORS = [
    (tco.NotFound, 404), (trade.NotFound, 404), (configurator.NotFound, 404), (assistant.NotFound, 404),
    (leads.NotFound, 404), (equity.NotFound, 404),
    (equity.RefreshError, 409), (supervisor.NotFound, 404), (actions.InvalidSlot, 400),
    (recs.NotFound, 404), (recs.InvalidState, 409),
]
