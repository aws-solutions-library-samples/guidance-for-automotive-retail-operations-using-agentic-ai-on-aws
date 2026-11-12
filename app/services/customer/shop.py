"""Customer Discovery (`/shop`) and nurture chat (`/shop/chat`) — spec §5.10, §3.1, §3.2.

The same A1 / A2 agents the advisor uses, called on the customer's own behalf:

  intake(cid, answers)        A1 infer_lifestyle_profile → ≤4 questions still worth asking
  shortlist(cid, answers, zip) top-3 ranked trims, each reason tied to a profile field,
                              recommended trim/packages, price build with est. monthly,
                              VIN availability (acceptance #12)
  save_build(cid, …)          PLAT_Configurations row + journey created / advanced
                              (build.saved), the §2.1 single-active rule respected
  test_drive(cid, slot)       A2 book_test_drive on the customer's journey
  chat(cid, text)             A2 answer_customer_question (grounded, cited); credit /
                              out-of-scope questions escalate to a human task
  thread(cid)                 recent chat messages (resume from any link)
"""
from __future__ import annotations

import datetime as _dt
import hashlib
from typing import Any

from ..common import ddb
from ..events import bus as _bus
from . import portal

_TOP_N = 3


class Invalid(ValueError):
    pass


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def _iso(d: _dt.datetime) -> str:
    return d.isoformat().replace("+00:00", "Z")


_ANSWER_FIELDS = {"household_size", "commute_mi_per_day", "road_trip_frequency", "towing_or_hauling",
                  "outdoor_activities", "tech_affinity", "safety_priority", "powertrain_openness",
                  "ownership_style"}


def _clean_answers(answers: dict[str, Any] | None) -> dict[str, Any]:
    out = {}
    for k, v in (answers or {}).items():
        if k not in _ANSWER_FIELDS or v in (None, ""):
            continue
        if k in ("household_size", "commute_mi_per_day"):
            try:
                v = int(float(v))
            except (TypeError, ValueError) as e:
                raise Invalid(f"{k} must be a number") from e
            if v < 0 or v > 2000:
                raise Invalid(f"{k} is out of range")
        out[k] = v
    return out


def _remember_preferences(cid: str, answers: dict[str, Any], cust: dict[str, Any],
                          env_prefix: str | None) -> None:
    """Record an explicitly stated preference where the signal is born so it
    where the signal is born (the intake answer), so A3/A2 recall it later.
    Only answers the customer just gave are written (plus the CRM budget when
    one is on file), never inferred values; remember() itself never raises."""
    from ..platform import memory as _memory
    stmts: list[str] = []
    if answers.get("powertrain_openness"):
        stmts.append(f"Powertrain preference: {answers['powertrain_openness']}")
    if answers.get("household_size"):
        stmts.append(f"Needs seating for a household of {answers['household_size']}")
    budget = (cust or {}).get("budget") or {}
    if budget.get("price_min") or budget.get("price_max"):
        stmts.append(f"Budget {budget.get('currency', 'USD')} "
                     f"{budget.get('price_min', '?')}-{budget.get('price_max', '?')}")
    for s in stmts:
        _memory.remember(cid, "preference", s, source_ref=f"A1.intake:/shop:{cid}",
                         actor="A1", env_prefix=env_prefix)


def intake(cid: str, answers: dict[str, Any] | None = None, env_prefix: str | None = None) -> dict[str, Any]:
    from ..agents import discovery
    cust = portal.customer(cid, env_prefix)
    signals = ddb.query_hash("DEALER_Signals", "customer_id", cid, env_prefix)
    cleaned = _clean_answers(answers)
    prof = discovery.infer_lifestyle_profile(cust, signals, cleaned)
    if cleaned:  # a submitted answer is a durable preference statement (durable-memory contract)
        _remember_preferences(cid, cleaned, cust, env_prefix)
    return {"customer_id": cid, "market_id": cust.get("market_id"), "profile": prof["profile"],
            "questions": prof["intake_questions"][:4], "ready": prof["ready"],
            "overall_confidence": prof["overall_confidence"], "source_ref": prof["source_ref"]}


def _why(match: dict[str, Any], profile: dict[str, Any]) -> list[dict[str, str]]:
    """Customer-facing reasons, each naming the profile field it rests on."""
    b = match.get("score_breakdown") or {}
    budget = profile.get("budget") or {}
    out = []
    if b.get("budget", 0) >= 40:
        out.append({"field": "budget", "text": "Fits the budget you gave us."})
    elif b.get("budget", 0) >= 20:
        out.append({"field": "budget", "text": "Just above your budget — packages can be trimmed."})
    pt = (match.get("powertrain") or "").upper()
    opn = (profile.get("powertrain_openness") or "").lower()
    if opn == "ev" and pt == "EV":
        out.append({"field": "powertrain_openness", "text": "Fully electric, as you asked."})
    elif opn == "ice" and pt == "ICE":
        out.append({"field": "powertrain_openness", "text": "Gas powertrain, as you prefer."})
    elif opn in ("open", "", None) or not opn:
        out.append({"field": "powertrain_openness", "text": f"{pt.title() or 'This'} powertrain — you're open to options."})
    hh = profile.get("household_size")
    if hh and (match.get("seats") or 0) >= hh:
        out.append({"field": "household_size", "text": f"Seats {match.get('seats')} — room for your household of {hh}."})
    if match.get("in_stock_count"):
        out.append({"field": "availability", "text": f"{match['in_stock_count']} available at your dealer now."})
    if not budget:
        out.append({"field": "budget", "text": "Tell us a budget to sharpen these picks."})
    return out


def shortlist(cid: str, answers: dict[str, Any] | None = None, zip5: str | None = None,
              env_prefix: str | None = None) -> dict[str, Any]:
    from ..agents import discovery
    cust = portal.customer(cid, env_prefix)
    market = cust.get("market_id") or "us"
    signals = ddb.query_hash("DEALER_Signals", "customer_id", cid, env_prefix)
    prof = discovery.infer_lifestyle_profile(cust, signals, _clean_answers(answers))
    profile = prof["profile"]
    budget = profile.get("budget") or {}
    j = portal.active_journey(cid, env_prefix) or {}
    dealer_id = j.get("dealer_id") or cust.get("dealer_id")
    inv = ddb.scan_filter("DEALER_Inventory", "dealer_id", dealer_id, env_prefix) if dealer_id else []
    avail: dict[str, list[dict[str, Any]]] = {}
    for v in inv:
        if v.get("status") == "in_stock":
            avail.setdefault(v.get("trim_id"), []).append(v)
    currency = budget.get("currency") or ("EUR" if market == "de" else "USD")
    catalog = [t for t in ddb.scan_all("OEM_Catalog", env_prefix) if t.get("kind") is None and t.get("currency") == currency]
    scored = [discovery._score(t, budget, profile, avail) for t in catalog]
    scored = [s for s in scored if s["score"] > 0]
    scored.sort(key=lambda s: s["score"], reverse=True)
    # one trim per model so the shortlist offers real choice
    picks, seen = [], set()
    for s in scored:
        if s["model_id"] in seen:
            continue
        seen.add(s["model_id"])
        picks.append(s)
        if len(picks) == _TOP_N:
            break
    models = {m["model_id"]: m for m in ddb.scan_all("OEM_CatalogModels", env_prefix)}
    state = (cust.get("address") or {}).get("state") or cust.get("region_code")
    out = []
    for rank, s in enumerate(picks, 1):
        rec = discovery.recommend_trim_and_packages(s["model_id"], profile, budget, env_prefix)
        cfg = rec["recommended_config"]
        price = discovery.price_build(cfg["config_total"], s.get("powertrain") or "ICE", market=market,
                                      state=state if market == "us" else None, down=budget.get("down") or 0,
                                      env_prefix=env_prefix)
        stock = discovery.search_inventory(s["model_id"], cfg.get("trim_id"), dealer_id=dealer_id,
                                           market=market, env_prefix=env_prefix)
        best = stock.get("best_offer")
        out.append({
            "rank": rank, "model_id": s["model_id"], "model_name": (models.get(s["model_id"]) or {}).get("name", s["model_id"]),
            "body": (models.get(s["model_id"]) or {}).get("body"),
            "powertrain": s.get("powertrain"), "seats": s.get("seats"), "ev_range": s.get("ev_range"),
            "features": s.get("features"), "why": _why(s, profile),
            "recommended": {"trim_id": cfg.get("trim_id"), "trim_name": cfg.get("trim_name"),
                            "packages": cfg.get("packages"), "config_total": cfg.get("config_total"),
                            "within_budget": cfg.get("within_budget"), "currency": cfg.get("currency")},
            "price": {"total_out_the_door": price["total_out_the_door"], "incentives_total": price["incentives_total"],
                      "incentives": [{"name": i["name"], "amount": i["amount"], "reduces_price": i["reduces_price"],
                                      "kind": i.get("kind")} for i in price["incentives_applied"]],
                      "tax": price["tax"], "est_monthly": price["est_monthly"], "currency": price["currency"],
                      "source_ref": price["source_ref"]},
            "availability": {"best": ({"vin": best.get("vin"), "dealer_id": best.get("dealer_id"),
                                       "dealer_name": portal._dealer_name(best.get("dealer_id"), env_prefix),
                                       "eta_days": best.get("eta_days"), "match": best.get("match"),
                                       "color": best.get("color")} if best else None),
                             "in_stock_count": stock.get("in_stock_count"),
                             # ZIP narrows nothing in the sandbox (no dealer geo index);
                             # availability is the home dealer + market-wide stock.
                             "zip": zip5},
            "source_ref": s.get("source_ref"),
        })
    return {"customer_id": cid, "market_id": market, "profile": profile,
            "questions": prof["intake_questions"][:4], "shortlist": out,
            "source_ref": "A1 (infer_lifestyle_profile → rank → recommend_trim_and_packages → price_build → search_inventory)"}


def _ensure_journey(cid: str, env_prefix: str | None) -> tuple[dict[str, Any], bool]:
    j = portal.active_journey(cid, env_prefix)
    if j and j.get("stage") != "OWN" and not j.get("superseded_by") and (j.get("status") or "active") == "active":
        return j, False
    cust = portal.customer(cid, env_prefix)
    ts = _iso(_now())
    jid = f"J-{(cust.get('market_id') or 'us').upper()}-WEB-{hashlib.sha1(f'{cid}{ts}'.encode()).hexdigest()[:8]}"
    prev = j.get("journey_id") if j else None
    new = {"journey_id": jid, "stage": "DISCOVER", "status": "active", "source": "web",
           "customer_id": cid, "dealer_id": cust.get("dealer_id"), "market_id": cust.get("market_id"),
           "owner_user_id": cust.get("assigned_advisor_user_id"), "previous_journey_id": prev,
           "context": {}, "created_at": ts, "updated_at": ts, "last_event_at": ts,
           "source_ref": "customer_web:/shop"}
    ddb.put_item("DEALER_Journeys", new, env_prefix)
    if j and j.get("stage") == "OWN" and not j.get("superseded_by"):
        old = dict(j)
        old["superseded_by"] = jid
        ddb.put_item("DEALER_Journeys", old, env_prefix)
    _bus.emit("journey.created", {"journey_id": jid, "customer_id": cid, "dealer_id": new["dealer_id"],
                                  "source": "web", "previous_journey_id": prev},
              actor={"type": "customer", "customer_id": cid}, resource_url=f"/r/journey/{jid}", env_prefix=env_prefix)
    return new, True


def save_build(cid: str, model_id: str, trim_id: str | None = None, packages: list[str] | None = None,
               color: str | None = None, env_prefix: str | None = None) -> dict[str, Any]:
    from ..agents import discovery
    from ..journeys import supervisor
    cust = portal.customer(cid, env_prefix)
    market = cust.get("market_id") or "us"
    trims = [t for t in ddb.scan_all("OEM_Catalog", env_prefix) if t.get("model_id") == model_id and t.get("kind") is None]
    if not trims:
        raise Invalid(f"unknown model {model_id}")
    trim = next((t for t in trims if t.get("trim_id") == trim_id), None) if trim_id else None
    if trim_id and trim is None:
        raise Invalid(f"unknown trim {trim_id}")
    trim = trim or sorted(trims, key=lambda t: t.get("list_price") or 0)[0]
    pkg_rows = [p for p in ddb.scan_all("OEM_Catalog", env_prefix)
                if p.get("kind") == "package" and p.get("model_id") == model_id and p.get("package_name") in (packages or [])]
    config_total = (trim.get("list_price") or 0) + sum(p.get("list_price") or 0 for p in pkg_rows)
    state = (cust.get("address") or {}).get("state") if market == "us" else None
    price = discovery.price_build(config_total, trim.get("powertrain") or "ICE", market=market, state=state,
                                  down=(cust.get("budget") or {}).get("down") or 0, env_prefix=env_prefix)
    journey, created = _ensure_journey(cid, env_prefix)
    ts = _iso(_now())
    cfg_id = "CFG-WEB-" + hashlib.sha1(f"{cid}|{trim['trim_id']}|{ts}".encode()).hexdigest()[:10]
    row = {"config_id": cfg_id, "customer_id": cid, "journey_id": journey["journey_id"], "model_id": model_id,
           "trim_id": trim["trim_id"], "packages": [p.get("package_name") for p in pkg_rows], "color": color,
           "price_vehicle": config_total, "price_tax_est": sum(t.get("amount") or 0 for t in price["tax"] if t.get("kind") == "sales_tax"),
           "incentives_applied": [i["incentive_id"] for i in price["incentives_applied"]],   # every applicable; the total sums price ones
           "incentives_total": price["incentives_total"], "price_total": price["total_out_the_door"],
           "est_monthly_buy": price["est_monthly"]["buy"], "est_monthly_lease": price["est_monthly"]["lease"],
           "currency": price["currency"], "saved_at": ts,
           "share_token": hashlib.sha1(cfg_id.encode()).hexdigest()[:10], "source_ref": price["source_ref"]}
    ddb.put_item("PLAT_Configurations", row, env_prefix)
    # journey context: the build is saved (CONFIGURE exit) and the model is shortlisted (DISCOVER exit)
    j = dict(ddb.get_item("DEALER_Journeys", {"journey_id": journey["journey_id"]}, env_prefix) or journey)
    ctx = dict(j.get("context") or {})
    ctx.update(shortlist=True, saved_build=True)
    j["context"] = ctx
    j["config_id"] = cfg_id
    j["updated_at"] = ts
    ddb.put_item("DEALER_Journeys", j, env_prefix)
    advanced = None
    if j.get("stage") in ("DISCOVER", "CONFIGURE"):
        try:
            advanced = supervisor.advance_journey(j["journey_id"], evidence=f"customer saved build {cfg_id}",
                                                  actor=f"customer:{cid}", env_prefix=env_prefix)
        except (supervisor.HandoffIncomplete, supervisor.InvalidTransition):
            advanced = None
    _bus.emit("build.saved", {"config_id": cfg_id, "journey_id": j["journey_id"], "customer_id": cid,
                              "dealer_id": j.get("dealer_id"), "model_id": model_id, "trim_id": trim["trim_id"]},
              actor={"type": "customer", "customer_id": cid}, resource_url=f"/r/journey/{j['journey_id']}",
              env_prefix=env_prefix)
    return {"config_id": cfg_id, "journey_id": j["journey_id"], "journey_created": created,
            "stage": (advanced or {}).get("to") or j.get("stage"),
            "price_total": row["price_total"], "currency": row["currency"],
            "est_monthly_buy": row["est_monthly_buy"], "share_token": row["share_token"]}


def test_drive(cid: str, slot_start: str | None = None, env_prefix: str | None = None) -> dict[str, Any]:
    from ..journeys import actions
    if slot_start:
        try:
            when = _dt.datetime.fromisoformat(slot_start.replace("Z", "+00:00"))
        except ValueError as e:
            raise Invalid("slot_start must be an ISO time") from e
        if when <= _now():
            raise Invalid("pick a time in the future")
    journey, _ = _ensure_journey(cid, env_prefix)
    end = None
    if slot_start:
        end = _iso(_dt.datetime.fromisoformat(slot_start.replace("Z", "+00:00")) + _dt.timedelta(hours=1))
    out = actions.book_test_drive(journey, slot_start=slot_start, slot_end=end, actor=f"customer:{cid}",
                                  env_prefix=env_prefix)
    return {**out, "dealer_name": portal._dealer_name(journey.get("dealer_id"), env_prefix)}


# --- chat ----------------------------------------------------------------------------

_ACTION_LABELS = {"book_test_drive": "Book a test drive", "start_prequal": "Check my payment options",
                  "get_trade_estimate": "Value my trade-in", "view_build": "See my saved build",
                  "book_service": "Book service"}


def _log_message(cid: str, direction: str, body: str, journey_id: str | None, env_prefix: str | None,
                 extra: dict[str, Any] | None = None) -> dict[str, Any]:
    ts = _iso(_now())
    mid = "MSG-WEB-" + hashlib.sha1(f"{cid}|{direction}|{ts}|{body}".encode()).hexdigest()[:12]
    row = {"message_id": mid, "customer_id": cid, "journey_id": journey_id, "channel": "chat",
           "direction": direction, "body": body, "sent_at": ts, **(extra or {})}
    ddb.put_item("CUST_Messages", row, env_prefix)
    _bus.emit("message.inbound" if direction == "inbound" else "message.outbound",
              {"message_id": mid, "customer_id": cid, "journey_id": journey_id, "channel": "chat"},
              actor={"type": "customer", "customer_id": cid} if direction == "inbound" else {"type": "agent", "agent": "A2"},
              env_prefix=env_prefix)
    return row


def chat(cid: str, text: str, env_prefix: str | None = None) -> dict[str, Any]:
    from ..journeys import qa
    from ..platform import tasks
    text = (text or "").strip()
    if not text:
        raise Invalid("message is empty")
    if len(text) > 2000:
        raise Invalid("message is too long")
    journey, _ = _ensure_journey(cid, env_prefix)
    _log_message(cid, "inbound", text, journey["journey_id"], env_prefix)
    reply = qa.answer_customer_question(text, journey, env_prefix)
    escalated = None
    if reply.get("escalate_to_human"):
        cust = portal.customer(cid, env_prefix)
        assignee = journey.get("owner_user_id") or cust.get("assigned_advisor_user_id")
        if assignee:
            t = tasks.create_task(f"Customer needs a person: {reply.get('category')}", assignee,
                                  created_by="A2", journey_id=journey["journey_id"], dealer_id=journey.get("dealer_id"),
                                  sla_hours=2, source_agent="A2.handoff_to_human",
                                  payload={"question_category": reply.get("category")}, env_prefix=env_prefix)
            escalated = {"task_id": t["task_id"], "advisor": portal._user_name(assignee, env_prefix) or "your advisor"}
    _log_message(cid, "outbound", reply.get("text") or "", journey["journey_id"], env_prefix,
                 {"generated_by": reply.get("generated_by"), "category": reply.get("category")})
    return {"journey_id": journey["journey_id"], "answer": reply.get("text"),
            "citations": [{"label": c.get("source"), "ref": c.get("source_ref")} for c in reply.get("citations") or []],
            "actions": [{"id": a, "label": _ACTION_LABELS.get(a, a.replace("_", " "))} for a in reply.get("suggested_actions") or []],
            "escalated": escalated, "refused": reply.get("refused", False), "category": reply.get("category"),
            "engine": reply.get("generated_by"), "model": reply.get("model")}


def thread(cid: str, limit: int = 40, env_prefix: str | None = None) -> dict[str, Any]:
    portal.customer(cid, env_prefix)
    msgs = sorted(ddb.scan_filter("CUST_Messages", "customer_id", cid, env_prefix), key=lambda m: m.get("sent_at") or "")
    msgs = [m for m in msgs if m.get("channel") == "chat"][-limit:]
    return {"customer_id": cid, "messages": [{"message_id": m.get("message_id"), "direction": m.get("direction"),
                                              "body": m.get("body"), "sent_at": m.get("sent_at")} for m in msgs]}
