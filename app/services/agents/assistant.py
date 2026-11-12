"""A3 — Dealer Sales Advisor tools for the Assistant, Advisor, and Lead Desk
(spec §3.3, §5.3, §5.8; acceptance #14).

  get_lifecycle_context(journey)              -> stage, days in stage, gaps, next appointments, open work
  get_recent_activity(customer_id, journey_id) -> merged signals + journey events + messages (newest first)
  get_objection_strategy(type, market)         -> library strategy + measured success rate
  record_outcome(journey_id, strategy_id, ...) -> DEALER_Outcomes row (+ accepts the recommendation)
  draft_follow_up(journey_id, channel)         -> grounded follow-up draft (never sent here)
  send_outreach(journey_id, kind, channel)     -> OutreachProvider send + message.outbound event
  build_call_list(user, dealers)               -> prioritized call list with reasons + top suggestion
  publish_shift_list(user, dealers)            -> the call list written as Tasks (A2 publish_shift_list)
  assistant_chat(journey_id, question, role)   -> cited answer (KnowledgeBase + journey facts)
  first_response_script(lead)                  -> A3 first-response script for a lead
  service_upsell_brief(service_journey)        -> A3 service-lane upsell brief from A7 findings

Every fact comes from a record and carries a ref; nothing here invents a price.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import re
from typing import Any

from ..common import ddb
from ..events import bus
from ..journeys import supervisor
from ..platform import generate, knowledge, outreach, recommendations, tasks
from . import suggestions as a3_suggestions

_OUTCOMES = "DEALER_Outcomes"

# memory rows read "Raised payment_too_high: \"…\"" — machine-friendly, not
# chat-friendly. Rewrite kind + slug into plain words for the one-shot answer.
_MEMORY_KIND_LABEL = {"objection": "Objection on record", "preference": "Stated preference",
                      "promise": "Promise made", "fact": "Known"}
_MEMORY_PREFIX_RE = re.compile(r"^(?:Raised|Noted|Logged)\s+([a-z0-9_]+):\s*")


def _memory_fact(kind: str, text: str) -> str:
    label = _MEMORY_KIND_LABEL.get(kind, f"Known {kind}")
    m = _MEMORY_PREFIX_RE.match(text or "")
    if m:
        return f"{label} — {m.group(1).replace('_', ' ')}: {text[m.end():].strip()}"
    return f"{label}: {text}"


class NotFound(Exception):
    pass


# Advisor-facing refusal per fenced category (qa.classify_refusal); the customer-facing wording is
# qa._ESCALATION_TEXT. One generic F&I line for every category read as irrelevant to a recall or
# legal question (2026-10-27 eval: answer_relevance 0.0 / 0.3), so each category names its owner.
_REFUSAL_TEXT = {
    "credit_decision": ("That needs a person — credit and approval decisions are made by the F&I team. "
                        "I can draft a handoff note or open a task for F&I instead."),
    "legal": ("That's a legal or compliance question I can't answer — route it to the sales manager or the "
              "compliance contact. I can draft a handoff note instead."),
    "safety_recall": ("Vehicle safety and recall questions go to the service team: they check the VIN against the "
                      "open-recall record and advise the customer. I can't clear a vehicle as safe to drive, but I "
                      "can open a task for service or draft a note to the customer."),
}
_REFUSAL_DEFAULT = "That needs a person rather than this assistant. I can draft a handoff note instead."


def refusal_text(category: str | None) -> str:
    """The advisor-facing holding message for a fenced question category."""
    return _REFUSAL_TEXT.get(category or "", _REFUSAL_DEFAULT)


def _now_dt() -> _dt.datetime:
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


def _journey(journey_id: str, env_prefix: str | None) -> dict[str, Any]:
    j = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id}, env_prefix)
    if j is None:
        raise NotFound(f"journey {journey_id} not found")
    return j


def _customer(cid: str | None, env_prefix: str | None) -> dict[str, Any]:
    return (ddb.get_item("DEALER_Customers", {"customer_id": cid}, env_prefix) if cid else None) or {}


def _name(c: dict[str, Any]) -> str:
    return " ".join(filter(None, [c.get("given_name"), c.get("family_name")])) or c.get("customer_id") or "customer"


def _model_names(env_prefix: str | None) -> dict[str, str]:
    return {m["model_id"]: m.get("name") or m["model_id"] for m in ddb.scan_all("OEM_CatalogModels", env_prefix)}


# --- lifecycle + activity ------------------------------------------------------

def get_lifecycle_context(journey: dict[str, Any], env_prefix: str | None = None) -> dict[str, Any]:
    jid = journey["journey_id"]
    events = ddb.query_hash("DEALER_JourneyEvents", "journey_id", jid, env_prefix)
    entered = None
    for e in sorted(events, key=lambda e: e.get("event_ts") or ""):
        if e.get("event_type") in ("stage.advanced", "journey.created"):
            entered = e.get("event_ts")
    now = _now_dt()
    days_in_stage = round((now - _parse(entered)).total_seconds() / 86400, 1) if _parse(entered) else None
    appts = sorted([a for a in ddb.scan_filter("DEALER_Appointments", "journey_id", jid, env_prefix)
                    if a.get("status") in ("scheduled", "booked") and (_parse(a.get("slot_start")) or now) >= now],
                   key=lambda a: a.get("slot_start") or "")
    open_tasks = [t for t in ddb.scan_filter("DEALER_Tasks", "journey_id", jid, env_prefix)
                  if t.get("status") in ("open", "in_progress")]
    msgs = sorted(ddb.scan_filter("CUST_Messages", "customer_id", journey.get("customer_id"), env_prefix)
                  if journey.get("customer_id") else [], key=lambda m: m.get("sent_at") or "", reverse=True)
    handoff = supervisor.validate_handoff(journey)
    # idle_days live, by the stall sweep's own definition (days since last_event_at): the sweep
    # writes the stored value once when it flags the journey and never revisits it, so the row said
    # 45 while the newest activity was 48 days old (2026-10-27 eval). Stored value is the fallback.
    idle_days = _days_ago(journey.get("last_event_at"), now)
    if idle_days is None:
        idle_days = journey.get("idle_days")
    return {
        "journey_id": jid, "stage": journey.get("stage"), "stage_entered_at": entered,
        "days_in_stage": days_in_stage, "idle_days": idle_days,
        "stalled": bool(journey.get("stalled")), "source": journey.get("source"),
        "owner_user_id": journey.get("owner_user_id"), "handoff": handoff,
        "next_appointment": ({k: appts[0].get(k) for k in ("appointment_id", "type", "slot_start", "status")}
                             if appts else None),
        "open_tasks": len(open_tasks),
        # days_ago alongside each ts: the agent quotes recency from a tool field, never its own
        # date arithmetic (2026-10-27 eval: "54 days" computed from today was unverifiable).
        "last_inbound": next(({"ts": m.get("sent_at"), "channel": m.get("channel"), "message_id": m.get("message_id"),
                               "days_ago": _days_ago(m.get("sent_at"), now)}
                              for m in msgs if m.get("direction") == "inbound"), None),
        "last_outbound": next(({"ts": m.get("sent_at"), "channel": m.get("channel"), "message_id": m.get("message_id"),
                                "days_ago": _days_ago(m.get("sent_at"), now)}
                               for m in msgs if m.get("direction") == "outbound"), None),
        "nurture_active": bool(journey.get("nurture_active")),
        "source_ref": f"A3.get_lifecycle_context:{jid}",
    }


_SIGNAL_TEXT = {
    "page_view": "Viewed a page", "model_view": "Viewed model", "trim_view": "Compared trims of",
    "content_read": "Read", "price_check": "Checked price on", "configure_save": "Saved a build of",
    "compare": "Compared", "test_drive_slot_view": "Looked at test-drive slots for",
}


def get_recent_activity(customer_id: str | None, journey_id: str | None = None, limit: int = 10,
                        env_prefix: str | None = None) -> list[dict[str, Any]]:
    names = _model_names(env_prefix)
    items: list[dict[str, Any]] = []
    if customer_id:
        for s in ddb.query_hash("DEALER_Signals", "customer_id", customer_id, env_prefix):
            what = names.get(s.get("model_id"), s.get("model_id") or "")
            text = f"{_SIGNAL_TEXT.get(s.get('type'), s.get('type'))} {s.get('content_title') or what}".strip()
            items.append({"ts": s.get("signal_ts"), "kind": "signal", "type": s.get("type"),
                          "text": text, "ref": f"DEALER_Signals:{customer_id}:{s.get('signal_ts')}"})
        for m in ddb.scan_filter("CUST_Messages", "customer_id", customer_id, env_prefix):
            items.append({"ts": m.get("sent_at"), "kind": "message", "type": f"message.{m.get('direction')}",
                          "text": f"{(m.get('direction') or '').capitalize()} {m.get('channel')}: {m.get('subject') or (m.get('body') or '')[:60]}",
                          "ref": f"CUST_Messages:{m.get('message_id')}"})
    if journey_id:
        for e in ddb.query_hash("DEALER_JourneyEvents", "journey_id", journey_id, env_prefix):
            if (e.get("event_type") or "").startswith("message."):
                continue    # already represented by CUST_Messages
            items.append({"ts": e.get("event_ts"), "kind": "event", "type": e.get("event_type"),
                          "text": _event_text(e, env_prefix),
                          "ref": f"DEALER_JourneyEvents:{journey_id}:{e.get('sk') or e.get('event_ts')}"})
    items.sort(key=lambda i: i.get("ts") or "", reverse=True)
    # Recency as a number, on the same clock as days_in_stage: the model has no reliable notion
    # of "today", so a bare timestamp invites a guessed "3 days ago" (2026-10-27 eval finding).
    now = _now_dt()
    for i in items:
        i["days_ago"] = _days_ago(i.get("ts"), now)
    return items[:limit]


def _event_text(e: dict[str, Any], env_prefix: str | None) -> str:
    """A journey event as a line a person reads: the evidence in words ('present:MER-VOLT-C-T3' is
    'presented the Ion C Extended'), else the event type in words."""
    from ..common import labels, vehicles
    ev = str(e.get("evidence") or "")
    return labels.evidence_label(ev, vehicles.trim_names(env_prefix) if ev.startswith("present:") else None) \
        or labels.humanize(e.get("event_type")).lower()


def _days_ago(ts: Any, now: _dt.datetime) -> float | None:
    d = _parse(ts)
    return round((now - d).total_seconds() / 86400, 1) if d else None


# --- objection strategies + outcomes ------------------------------------------

def get_objection_strategy(objection_type: str, market: str, env_prefix: str | None = None) -> dict[str, Any] | None:
    strat = next((s for s in ddb.scan_filter("DEALER_ObjectionStrategies", "market_id", market, env_prefix)
                  if s.get("objection_type") == objection_type), None)
    if strat is None:
        return None
    outs = ddb.scan_filter(_OUTCOMES, "strategy_id", strat.get("strategy_id"), env_prefix)
    used = [o for o in outs if o.get("outcome") in ("used", "converted")]
    converted = [o for o in outs if o.get("outcome") == "converted"]
    return {**strat, "uses": len(used),
            "success_rate": round(len(converted) / len(used), 3) if len(used) >= 5 else None,
            "success_basis": f"{len(converted)}/{len(used)} recorded outcomes" if used else "no recorded outcomes yet"}


OUTCOMES = ("used", "alternative", "dismissed", "converted")


def record_outcome(journey_id: str, strategy_id: str | None, outcome: str, actor: str,
                   recommendation_id: str | None = None, env_prefix: str | None = None) -> dict[str, Any]:
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}")
    ts = _iso(_now_dt())
    oid = "OUT-" + hashlib.sha1(f"{journey_id}|{strategy_id}|{outcome}|{ts}".encode()).hexdigest()[:12]
    row = {"outcome_id": oid, "journey_id": journey_id, "strategy_id": strategy_id, "outcome": outcome,
           "recommendation_id": recommendation_id, "recorded_at": ts, "recorded_by": actor,
           "source_ref": "A3.record_outcome"}
    ddb.put_item(_OUTCOMES, row, env_prefix)
    if recommendation_id:
        try:
            if outcome in ("used", "converted"):
                recommendations.accept(recommendation_id, actor, env_prefix)
            elif outcome == "dismissed":
                recommendations.dismiss(recommendation_id, actor, env_prefix=env_prefix)
        except (recommendations.NotFound, recommendations.InvalidState):
            pass
    return row


# --- follow-up draft + outreach ---------------------------------------------------

_NEXT_STEP = {
    "DISCOVER": "pick two models to test drive", "CONFIGURE": "lock in the build you saved",
    "QUALIFY": "book a test drive", "FINANCE": "review your payment options",
    "TRADE_IN": "confirm your trade-in value", "AGREE": "review the final paperwork",
    "DELIVER": "confirm your delivery time", "OWN": "book your first service",
}


def draft_follow_up(journey_id: str, channel: str = "email", env_prefix: str | None = None,
                    topic: str | None = None) -> dict[str, Any]:
    """topic: what the note opens with — "saved_build" anchors on the saved configuration; anything
    else (or None) anchors on the newest signal. Added after the 2026-10-28 eval: "draft a note about
    the saved build" produced a test-drive hook because the newest signal always won."""
    j = _journey(journey_id, env_prefix)
    c = _customer(j.get("customer_id"), env_prefix)
    names = _model_names(env_prefix)
    act = get_recent_activity(j.get("customer_id"), journey_id, 5, env_prefix)
    cfgs = sorted(ddb.scan_filter("PLAT_Configurations", "journey_id", journey_id, env_prefix),
                  key=lambda x: x.get("saved_at") or "", reverse=True)
    model = names.get((cfgs[0] if cfgs else {}).get("model_id") or j.get("primary_model_id") or "", "")
    first = c.get("given_name") or "there"
    step = _NEXT_STEP.get(j.get("stage"), "take the next step")
    fact = next((a for a in act if a["kind"] == "signal"), None)
    if topic == "saved_build" and cfgs:
        fact = {"text": f"saved a {model or 'build'} configuration", "ref": f"PLAT_Configurations:{cfgs[0].get('config_id')}"}
    hook = f"I saw you {fact['text'][0].lower() + fact['text'][1:]}" if fact else "Thanks again for your time"
    body = (f"Hi {first},\n\n{hook}{' — the ' + model if model and fact and model not in fact['text'] else ''}. "
            f"When you're ready, the easiest next step is to {step}. "
            f"Reply here or pick a time that suits you and I'll have everything prepared.\n\nBest regards")
    if channel == "sms":
        body = f"Hi {first}, {hook.lower() if fact else 'thanks for your time'}. Want to {step}? Reply with a time that works."
    return {"journey_id": journey_id, "channel": channel,
            "subject": f"Next step{' on your ' + model if model else ''}", "body": body,
            "facts_used": [a["ref"] for a in act[:3]] + ([f"PLAT_Configurations:{cfgs[0]['config_id']}"] if cfgs else []),
            "source_ref": "A3.draft_follow_up (template over journey facts)"}


_OUTREACH_KIND = {
    "follow_up": ("Following up", None),
    "finance_link": ("Your payment options", "/garage?section=finance"),
    "test_drive_link": ("Pick a test-drive time", "/garage?section=appointments"),
    "callback": ("We'll call you back", None),
}


def send_outreach(journey_id: str, kind: str, channel: str, actor: str, body: str | None = None,
                  subject: str | None = None, recommendation_id: str | None = None,
                  env_prefix: str | None = None) -> dict[str, Any]:
    if kind not in _OUTREACH_KIND:
        raise ValueError(f"kind must be one of {sorted(_OUTREACH_KIND)}")
    j = _journey(journey_id, env_prefix)
    c = _customer(j.get("customer_id"), env_prefix)
    default_subject, link = _OUTREACH_KIND[kind]
    draft = draft_follow_up(journey_id, channel if channel in ("email", "sms") else "email", env_prefix)
    text = body or draft["body"]
    if link:
        text = f"{text}\n\n{link}"
    if kind == "callback":
        res = tasks.create_task(f"Call back {_name(c)}", j.get("owner_user_id") or actor, created_by=actor,
                                journey_id=journey_id, dealer_id=j.get("dealer_id"), sla_hours=4,
                                source_agent="A2.schedule_outreach", env_prefix=env_prefix)
        out = {"sent": False, "channel": "task", "task_id": res["task_id"]}
    else:
        to = c.get("email") if channel == "email" else c.get("phone")
        out = outreach.get_outreach_provider().send(channel, to, subject or default_subject, text,
                                                    customer_id=c.get("customer_id"), dealer_id=j.get("dealer_id"),
                                                    env_prefix=env_prefix)
        if out.get("message_id"):
            bus.emit("message.outbound", {"journey_id": journey_id, "customer_id": c.get("customer_id"),
                                          "dealer_id": j.get("dealer_id"), "channel": channel, "kind": kind,
                                          "message_id": out.get("message_id"), "delivered": out.get("sent")},
                     actor=actor, resource_url=f"/r/journey/{journey_id}", env_prefix=env_prefix)
    if recommendation_id:
        try:
            recommendations.accept(recommendation_id, actor, env_prefix)
        except (recommendations.NotFound, recommendations.InvalidState):
            pass
    return {"journey_id": journey_id, "kind": kind, **out}


# --- call list / shift list --------------------------------------------------------

def build_call_list(user_id: str | None, dealer_ids: set[str] | None, limit: int = 15,
                    env_prefix: str | None = None) -> dict[str, Any]:
    """Prioritized list of journeys to work today: owned by the user (or every
    journey in the dealers for a manager), scored by explicit, explainable reasons."""
    now = _now_dt()
    today = now.date().isoformat()
    rows = [j for j in ddb.scan_all("DEALER_Journeys", env_prefix)
            if j.get("stage") != "OWN" and (j.get("status") or "active") == "active"
            and (j.get("owner_user_id") == user_id if user_id else (dealer_ids is None or j.get("dealer_id") in dealer_ids))]
    appts = {}
    for a in ddb.scan_all("DEALER_Appointments", env_prefix):
        if a.get("journey_id") and (a.get("slot_start") or "")[:10] == today and a.get("status") in ("scheduled", "booked"):
            appts[a["journey_id"]] = a
    custs = {c["customer_id"]: c for c in ddb.scan_all("DEALER_Customers", env_prefix)}
    items = []
    for j in rows:
        reasons, score = [], 0
        if j["journey_id"] in appts:
            score += 30; reasons.append(f"appointment today {appts[j['journey_id']].get('slot_start', '')[11:16]}")
        if j.get("stalled"):
            score += 25; reasons.append(f"stalled {round(j.get('idle_days') or 0)}d")
        gaps = supervisor.validate_handoff(j)
        if not gaps["ok"]:
            # shown on the advisor's day plan: "missing saved build, valuation", not field codes
            score += 15; reasons.append("missing " + ", ".join(str(g).replace("_", " ") for g in gaps["missing"][:2]))
        if j.get("stage") in ("FINANCE", "AGREE", "DELIVER"):
            score += 20; reasons.append(f"late stage ({j.get('stage')})")
        idle = j.get("idle_days") or 0
        if 3 <= idle < 14 and not j.get("stalled"):
            score += 10; reasons.append(f"{round(idle)}d since last touch")
        if not reasons:
            continue
        c = custs.get(j.get("customer_id"), {})
        items.append({"journey_id": j["journey_id"], "customer_id": j.get("customer_id"), "customer_name": _name(c),
                      "stage": j.get("stage"), "dealer_id": j.get("dealer_id"), "score": score,
                      "reasons": reasons, "source": j.get("source")})
    items.sort(key=lambda i: (-i["score"], i["journey_id"]))
    items = items[:limit]
    for it in items:   # top suggestion for the shown rows only (bounded cost)
        try:
            sug = a3_suggestions.generate_contextual_suggestions(
                ddb.get_item("DEALER_Journeys", {"journey_id": it["journey_id"]}, env_prefix), "sales", env_prefix)
            top = (sug.get("suggestions") or [None])[0]
            it["top_suggestion"] = {k: top.get(k) for k in ("text", "action", "why", "confidence")} if top else None
        except Exception:
            it["top_suggestion"] = None
    return {"user_id": user_id, "generated_at": _iso(now), "count": len(items), "items": items,
            "source_ref": "A3.build_call_list (journeys + appointments + validate_handoff)"}


def publish_shift_list(user_id: str, dealer_ids: set[str] | None, actor: str, limit: int = 10,
                       env_prefix: str | None = None) -> dict[str, Any]:
    """A2 publish_shift_list: write today's call list as Tasks (idempotent per day)."""
    cl = build_call_list(user_id, dealer_ids, limit, env_prefix)
    day = cl["generated_at"][:10]
    created = []
    for it in cl["items"]:
        tid = f"SHIFT-{day}-{it['journey_id']}"
        if ddb.get_item("DEALER_Tasks", {"task_id": tid}, env_prefix):
            continue
        tasks.create_task(f"Call {it['customer_name']} — {'; '.join(it['reasons'])}", user_id, created_by=actor,
                          journey_id=it["journey_id"], dealer_id=it.get("dealer_id"), sla_hours=10,
                          source_agent="A2.publish_shift_list", task_id=tid, env_prefix=env_prefix)
        created.append(tid)
    return {**cl, "published_tasks": created}


# --- assistant chat ---------------------------------------------------------------

_WHO_TO_WORK = ("call first", "call today", "who should i call", "who should i work", "work first", "work today",
                "prioritize", "prioritise", "call list", "start with today", "my list")


def _asks_who_to_work(q: str) -> bool:
    ql = q.lower()
    return any(t in ql for t in _WHO_TO_WORK)


def assistant_chat(question: str, journey_id: str | None, role: str, env_prefix: str | None = None,
                   agent_id: str | None = None, claims: dict[str, Any] | None = None) -> dict[str, Any]:
    """claims (optional): the caller's user_id / dealer_ids, needed only for the no-journey
    "who should I call today" answer; without them that ask falls through to the generic text."""
    from ..journeys import qa
    q = (question or "").strip()
    if not q:
        raise ValueError("question is required")
    agent = (agent_id or "A3").upper()
    # The reader is a dealership advisor asking about THEIR customer (the facts name the customer);
    # without saying so the model wrote to the customer ("your profile", "could you share your
    # budget") and the grounding check scored those lines as drift (2026-10-28 eval).
    audience = (" answering a dealership advisor's question about their customer; speak about the customer "
                "in the third person and only from the facts")
    personas = {
        "A1": "a vehicle discovery specialist" + audience,
        "A2": "a purchase journey supervisor" + audience,
        "A3": "a dealership sales advisor's assistant" + audience,
        "A4": "a trade-in and upgrade specialist" + audience,
    }
    if agent not in personas:
        raise ValueError(f"unsupported assistant agent {agent!r}")
    refusal = qa.classify_refusal(q)
    if refusal:
        return {"answer": refusal_text(refusal), "citations": [], "guardrail": refusal,
                "actions": ["handoff_to_human"]}
    facts, citations = [], []
    if journey_id:
        j = _journey(journey_id, env_prefix)
        c = _customer(j.get("customer_id"), env_prefix)
        # Durable customer memory first (durable-memory contract): what was already stated,
        # objected to, or promised anchors the one-shot answer the same way the
        # agent path's customer_memory tool does. Empty recall adds nothing.
        from ..platform import memory as _memory
        for m in _memory.recall(j.get("customer_id"), k=3, env_prefix=env_prefix):
            facts.append(f"{_memory_fact(m['kind'], m['text'])}.")
            citations.append({"label": f"memory:{m['kind']}", "ref": m.get("source_ref")})
        lc = get_lifecycle_context(j, env_prefix)
        facts.append(f"{_name(c)} is in {j.get('stage')} ({lc['days_in_stage'] or '?'} days in stage).")
        if not lc["handoff"]["ok"]:
            facts.append(f"Before moving on, capture: {', '.join(lc['handoff']['missing'])}.")

        try:
            if agent == "A1":
                from . import discovery
                ranked = discovery.rank_vehicles(journey_id, env_prefix)
                # Budget and household are WHY a match fits; without them the model told the
                # advisor the budget "isn't in my records" and asked for it (2026-10-28 eval).
                b = ranked.get("budget") or {}
                if b:
                    facts.append(f"Customer budget on file: {b.get('price_min')}-{b.get('price_max')} {b.get('currency', '')} "
                                 f"price, up to {b.get('monthly_max')} {b.get('currency', '')}/month, {b.get('down')} down.")
                life = (c or {}).get("lifestyle_profile") or {}
                if life:
                    facts.append("Household/lifestyle: " + "; ".join(f"{k.replace('_', ' ')}={v}" for k, v in life.items()
                                                                     if v not in (None, "", [], {})) + ".")
                for match in (ranked.get("matches") or [])[:3]:
                    facts.append(
                        f"Vehicle match: {match.get('trim_name') or match.get('model_id')} ({match.get('powertrain')}, "
                        f"{match.get('seats')} seats) score={match.get('score')}; price={match.get('list_price')} "
                        f"{match.get('currency')}; in stock={match.get('in_stock_count')}; features={match.get('features')}"
                        + (f"; why: {match['rationale']}" if match.get("rationale") else "") + "."
                    )
                    citations.append({"label": match.get("trim_id") or "vehicle match",
                                      "ref": match.get("source_ref")})
            elif agent == "A2":
                nba = supervisor.select_next_best_action(j, env_prefix=env_prefix)
                chosen = nba.get("chosen") or {}
                facts.append(f"Next best action: {chosen.get('action')} — {chosen.get('reason')}.")
                for alternative in (nba.get("alternatives") or [])[:3]:
                    facts.append(f"Alternative action: {alternative.get('action')} — {alternative.get('reason')}.")
                citations.append({"label": "journey state",
                                  "ref": nba.get("source_ref") or f"DEALER_Journeys:{journey_id}"})
            elif agent == "A4":
                from . import equity
                snapshot = equity.build_equity(journey_id, env_prefix)
                facts.append(
                    f"Trade equity: position={snapshot.get('equity_position')}; equity={snapshot.get('equity')} "
                    f"{snapshot.get('currency')}; trade value={snapshot.get('trade_value')}; "
                    f"payoff={snapshot.get('payoff')}; upgrade signal={snapshot.get('upgrade_signal')}."
                )
                if snapshot.get("payment_delta"):
                    facts.append(f"Payment delta: {snapshot['payment_delta']}.")
                elif snapshot.get("finance_offer"):
                    facts.append(f"Finance offer on file: {snapshot['finance_offer']}; no payment delta computed.")
                else:
                    # Said explicitly, so the model reports it instead of listing what it would need
                    # (an ungrounded wish-list the grounding check rejected, 2026-10-28 eval).
                    facts.append("No finance offer on file for this journey, so no monthly payment or payment "
                                 "change can be quoted; an advisor would need to present an offer first.")
                for label, ref in (snapshot.get("source_refs") or {}).items():
                    if ref:
                        citations.append({"label": f"equity:{label}", "ref": ref})
            else:
                suggestions = a3_suggestions.generate_contextual_suggestions(
                    j, "service" if role == "dealer_service" else "sales", env_prefix)
                for suggestion in (suggestions.get("suggestions") or [])[:2]:
                    facts.append(f"Suggested: {suggestion['text']} — because {suggestion['why']}.")
                    citations.append({"label": (suggestion.get("evidence") or {}).get("type") or "activity",
                                      "ref": suggestion["why"]})
        except Exception as exc:
            facts.append(f"The {agent} specialist data is temporarily unavailable ({type(exc).__name__}); "
                         "answer from the remaining journey facts only.")

        if lc["next_appointment"]:
            facts.append(f"Next appointment: {lc['next_appointment']['type']} at {lc['next_appointment']['slot_start']}.")
            citations.append({"label": "appointment", "ref": f"DEALER_Appointments:{lc['next_appointment']['appointment_id']}"})
    elif agent == "A3" and claims and claims.get("user_id") and _asks_who_to_work(q):
        # No journey, but a "who should I call today" ask: the ranked call list is the answer, the
        # same one the agent path's build_call_list tool returns (2026-10-27 eval, a3-no-journey).
        cl = build_call_list(claims["user_id"], None, limit=5, env_prefix=env_prefix)
        for it in cl.get("items") or []:
            facts.append(f"Work {it.get('customer_name') or it.get('journey_id')} ({it.get('stage')}): "
                         f"{'; '.join(it.get('reasons') or [])}.")
            citations.append({"label": "journey", "ref": f"DEALER_Journeys:{it.get('journey_id')}"})
        if not facts:
            facts.append("Nothing on your list needs a call today.")
        citations.append({"label": "call_list", "ref": cl.get("source_ref")})
    kb = knowledge.get_knowledge_base().retrieve(q, k=3, env_prefix=env_prefix)
    for p in kb.get("passages", []):
        citations.append({"label": p["source_ref"].split(":")[0], "ref": p["source_ref"]})
    passages = [knowledge.strip_chunk_metadata(p["passage"]) for p in kb.get("passages", [])]
    # Readable fallback: one fact per line, KB passages stay OUT of the answer —
    # the UI renders them from `references` so the chat reads as prose, not a dump.
    deterministic = "\n".join(f"• {f}" for f in facts) if facts else (
        f"I couldn't tie this {agent} question to a journey. Open a customer or journey "
        "to provide scoped facts."
    )
    # Grounded generation over the same facts + passages the citations point at,
    # so the answer stays attributable; falls back to the deterministic text.
    gen = generate.ground(
        q, facts, passages, persona=personas[agent], fallback=deterministic)
    return {"answer": gen["text"], "citations": citations, "guardrail": None,
            "references": [{"label": p["source_ref"], "text": text[:300]}
                           for p, text in zip(kb.get("passages", []), passages) if text],
            "model_guardrail": gen.get("guardrail"),      # Bedrock Guardrail outcome (applied/intervened)
            "engine": gen["engine"], "model": gen.get("model"), "retrieval": kb.get("engine"),
            "agent_id": agent,
            "source_ref": f"{agent}.assistant_chat (agent-specific journey facts + KnowledgeBase)"}


# --- lead first response ------------------------------------------------------------

_SOURCE_LABEL = {
    "web_form": "website", "web": "website", "campaign": "campaign reply", "phone": "call",
    "walk_in": "visit", "third_party_lead": "enquiry", "service_lane": "service visit", "chat": "chat",
}


def first_response_script(lead: dict[str, Any], env_prefix: str | None = None) -> dict[str, Any]:
    c = _customer(lead.get("customer_id"), env_prefix)
    first = c.get("given_name") or "there"
    if lead.get("source") == "predictive_maintenance":
        # a service heads-up, not a sales enquiry: name the part, the timing and the offer —
        # never a test-drive pitch for a brake-pad lead
        from ..common import labels, vehicles
        raw_part = str(lead.get("predicted_part") or "wear item")
        part = labels.part_phrase(raw_part)
        verb = labels.is_are(raw_part)
        also = [labels.part_phrase(a.get("part")) for a in (lead.get("predicted_also_due") or []) if a.get("part")]
        due = int(lead.get("predicted_due_mileage") or 0)
        odo = int(lead.get("predicted_odometer") or 0)
        # the car the prediction is about (its VIN-master row), else the customer's current vehicle
        v = vehicles.display_fields(ddb.get_item("DEALER_Vehicles", {"vin": lead.get("predicted_vin")}, env_prefix)
                                    if lead.get("predicted_vin") else None, env_prefix)
        cv = c.get("current_vehicle") or {}
        car = (" ".join(str(x) for x in (v.get("year"), v.get("make"), v.get("model")) if x)
               or " ".join(str(x) for x in (cv.get("year"), cv.get("make"), cv.get("model")) if x) or "your vehicle")
        timing = (f"{verb} past the usual service point" if (lead.get("predicted_overdue") or (odo and due and due < odo))
                  else f"{verb} coming up around {due:,} miles")
        more = f" We'd also check the {labels.join_words(also)} on the same visit." if also else ""
        off = lead.get("offer_pct")
        return {
            "lead_id": lead.get("lead_id"),
            "opening": f"Hi {first}, a quick heads-up from our service team about your {car}.",
            "body": (f"Based on your mileage and service history, the {part} {timing}.{more} "
                     f"If you book ahead we'll take {off}% off the work — would a morning or afternoon slot this week suit?"
                     if off else
                     f"Based on your mileage and service history, the {part} {timing}.{more} "
                     "Would a morning or afternoon slot this week suit?"),
            "questions": ["Would you prefer to wait, drop off, or have the car collected?",
                          "Anything else you've noticed we should look at while it's in?"],
            "source_ref": "A3.first_response_script (A7 predictive lead + customer vehicle)",
        }
    model = _model_names(env_prefix).get(lead.get("vehicle_of_interest_model_id") or "", "the vehicle you asked about")
    src = _SOURCE_LABEL.get(str(lead.get("source") or ""), "enquiry")
    return {
        "lead_id": lead.get("lead_id"),
        "opening": f"Hi {first}, thanks for reaching out about the {model}.",
        "body": (f"I saw your {src} request{(': “' + lead['comments'] + '”') if lead.get('comments') else ''}. "
                 f"I can hold a test-drive slot and have the {model} ready — would today or tomorrow work?"),
        "questions": ["What's your timeline to decide?", "Will you be trading in a vehicle?",
                      "Any must-have features or budget range I should know?"],
        "source_ref": "A3.first_response_script (lead + customer facts)",
    }


# --- service lane upsell brief ---------------------------------------------------------

def service_upsell_brief(sj: dict[str, Any], env_prefix: str | None = None) -> dict[str, Any]:
    sid = sj["service_journey_id"]
    # an upsell is work the customer would pay for: a finding the warranty covers (a charge
    # port on a new EV) is a repair the dealer does anyway, never a sales talking point
    from ..service import estimates as _est
    car = ddb.get_item("DEALER_Vehicles", {"vin": sj.get("vin")}, env_prefix) if sj.get("vin") else None
    findings = [i for i in ddb.scan_filter("DEALER_Inspections", "service_journey_id", sid, env_prefix)
                if i.get("status") in ("yellow", "red")
                and _est.line_coverage(car, i.get("item"))["pay_type"] != "warranty"]
    declined = [d for d in ddb.scan_filter("DEALER_DeclinedWork", "customer_id", sj.get("customer_id"), env_prefix)
                if sj.get("customer_id")]
    lines = [{"item": f.get("item"), "status": f.get("status"), "urgency": f.get("urgency"),
              "ref": f"DEALER_Inspections:{f.get('inspection_id')}"} for f in findings]
    # one line per item: a declined item the inspection flagged again is already listed
    seen = {str(ln["item"] or "").strip().lower() for ln in lines}
    for d in declined:
        key = str(d.get("item") or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            lines.append({"item": d.get("item"), "status": "declined", "amount": d.get("amount"),
                          "currency": d.get("currency"), "declined_at": d.get("declined_at"),
                          "ref": f"DEALER_DeclinedWork:{d.get('declined_id')}"})
    talk = None
    if findings:
        talk = f"While you're here, the inspection flagged {findings[0].get('item')} — want me to price it with today's visit?"
    elif declined:
        talk = f"Last time we noted {declined[0].get('item')}; we can take care of it today while the car's in."
    return {"service_journey_id": sid, "items": lines, "talk_track": talk,
            "source_ref": "A3.service_upsell_brief (A7 findings + declined work)"}
