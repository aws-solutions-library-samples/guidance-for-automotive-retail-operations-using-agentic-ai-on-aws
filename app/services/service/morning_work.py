"""A7 — morning-work named tools (spec §3.7 / §6a, acceptance #32).

The spec names five morning-work tools. shop_load.run_shop_load sweeps the
booked ROs and calls these per journey; they are also individually callable
(and reachable through the API + AgentCore). Each is autonomy-gated on its own
PLAT_ConfigAutonomy action and, at a WRITE level, records a DEALER_Recommendations
card carrying the level in force.

  dispatch_ro(sj)          a7.dispatch_ro      — assign a qualified technician
                                                 (EV HV lines only to certified)
  stage_parts(sj)          a7.stage_parts      — stage predicted parts before the appt
  reforecast_promise(sj)   a7.reforecast_promise — slip detection + customer notice
  auto_present_estimate(sj)a7.auto_present_estimate — present maintenance lines under cap
  prepare_ro_brief(sj)     (read)              — advisor brief: history + declined + equity
  prioritize_cards(cards)  (read)              — one guarded Converse call orders the
                                                 morning cards (supervisor-ordering constraint: ordering only;
                                                 deterministic order is the fallback)
"""
from __future__ import annotations

import datetime as _dt
import json as _json
import time as _time
from typing import Any

from ..common import ddb, autonomy_config, trace as _trace
from ..platform import generate as _gen
from . import booking as svc_booking

_USERS = "PLAT_Users"
_RECS = "DEALER_Recommendations"
_TECH_ROLES = frozenset({"dealer_technician", "dealer_service"})
_SOURCE = "agentic:morning_work (spec §6a)"
_RECALL_SOURCE_REF = "warranty/recall connector (sandbox: no open campaigns)"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse(ts: str | None):
    if not ts:
        return None
    try:
        return _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _level(action: str, market: str, env_prefix: str | None) -> str:
    return autonomy_config.active_level(action, market, env_prefix)


def _write_rec(rec_id: str, action: str, level: str, sj: dict[str, Any], reason: str,
               extra: dict[str, Any], apply: bool, env_prefix: str | None) -> bool:
    """Write a DEALER_Recommendations card iff apply and level is a WRITE level."""
    if not (apply and level in autonomy_config.WRITE_LEVELS):
        return False
    row = {"recommendation_id": rec_id, "action": action, "agent_id": "A7",
           "service_journey_id": sj.get("service_journey_id"), "customer_id": sj.get("customer_id"),
           "dealer_id": sj.get("dealer_id"), "shown": 0, "accepted": 0, "converted": 0,
           "shown_at": _now(), "autonomy_level": level, "reason": reason,
           "source_ref": _SOURCE}
    row.update(extra)
    ddb.put_item(_RECS, row, env_prefix)
    return True


def _technicians(dealer_id: str, env_prefix: str | None) -> list[dict[str, Any]]:
    out = []
    for u in ddb.scan_all(_USERS, env_prefix):
        groups = set(u.get("groups") or [])
        if groups & _TECH_ROLES and (u.get("dealer_id") == dealer_id or u.get("scope_node_id") == dealer_id):
            out.append(u)
    return out


def dispatch_ro(sj: dict[str, Any], apply: bool = False, env_prefix: str | None = None) -> dict[str, Any]:
    """Assign a qualified technician to a booked RO. EV HV work (EV models) is a
    hard gate: only HV-certified technicians (skills contains 'hv_certified')."""
    market = (sj.get("market_id") or "us")
    level = _level("a7.dispatch_ro", market, env_prefix)
    sj_id = sj.get("service_journey_id")
    if sj.get("technician_id"):
        return {"action": "dispatch_ro", "skipped": "already dispatched", "level": level}
    techs = _technicians(sj.get("dealer_id"), env_prefix)
    is_ev = "VOLT" in str(sj.get("vin", "")).upper() or "ev" in str(sj.get("service_type", "")).lower()
    if is_ev:
        techs = [t for t in techs if "hv_certified" in (t.get("skills") or [])]
        if not techs:
            return {"action": "dispatch_ro", "skipped": "no HV-certified technician",
                    "ev_hv_gated": True, "level": level}
    if not techs:
        return {"action": "dispatch_ro", "skipped": "no eligible technician", "level": level}
    pick = techs[hash(sj_id) % len(techs)]
    tech_id = pick.get("user_id")
    reason = f"assign technician {tech_id}" + (" (HV-certified, EV line)" if is_ev else "")
    wrote = _write_rec(f"REC-MW-DISP-{sj_id}", "dispatch_ro", level, sj, reason,
                       {"recommended_technician_id": tech_id, "ev_hv_gated": is_ev}, apply, env_prefix)
    return {"action": "dispatch_ro", "technician_id": tech_id, "ev_hv_gated": is_ev,
            "level": level, "written": wrote}


def stage_parts(sj: dict[str, Any], apply: bool = False, env_prefix: str | None = None) -> dict[str, Any]:
    """Stage predicted-service parts before the appointment (within daily cap)."""
    market = (sj.get("market_id") or "us")
    level = _level("a7.stage_parts", market, env_prefix)
    sj_id = sj.get("service_journey_id")
    if sj.get("parts_hold"):
        return {"action": "stage_parts", "skipped": "already on parts hold", "level": level}
    try:
        pred = svc_booking.predict_next_service(sj.get("vin"), env_prefix) if sj.get("vin") else None
    except Exception:
        pred = None
    if not pred or not pred.get("recommended_services"):
        return {"action": "stage_parts", "skipped": "no predicted services", "level": level}
    reason = f"stage parts for {pred['recommended_services']}" + (" (overdue)" if pred.get("overdue") else "")
    wrote = _write_rec(f"REC-MW-PARTS-{sj_id}", "stage_parts", level, sj, reason,
                       {"predicted_services": pred["recommended_services"], "overdue": bool(pred.get("overdue"))},
                       apply, env_prefix)
    return {"action": "stage_parts", "predicted_services": pred["recommended_services"],
            "level": level, "written": wrote}


def reforecast_promise(sj: dict[str, Any], apply: bool = False, env_prefix: str | None = None) -> dict[str, Any]:
    """Detect a promise slip (projected > promised) and recommend a customer
    notice BEFORE the promise passes (spec §6a / acc #32)."""
    market = (sj.get("market_id") or "us")
    level = _level("a7.reforecast_promise", market, env_prefix)
    sj_id = sj.get("service_journey_id")
    promised, projected = _parse(sj.get("promised_at")), _parse(sj.get("projected_at"))
    if not (promised and projected and projected > promised):
        return {"action": "reforecast_promise", "skipped": "no slip", "level": level}
    slip = int((projected - promised).total_seconds() // 60)
    reason = f"projected runs {slip} min past promised — notify customer with new time"
    wrote = _write_rec(f"REC-MW-PROMISE-{sj_id}", "reforecast_promise", level, sj, reason,
                       {"promised_at": sj.get("promised_at"), "projected_at": sj.get("projected_at"),
                        "slip_minutes": slip}, apply, env_prefix)
    return {"action": "reforecast_promise", "slip_minutes": slip, "level": level, "written": wrote}


def auto_present_estimate(sj: dict[str, Any], max_amount: float = 400.0,
                          apply: bool = False, env_prefix: str | None = None) -> dict[str, Any]:
    """Auto-present maintenance lines under the dealer threshold (spec §4.10.1).
    Hard rule: customer still owns the approval — this only presents, never
    approves. Recommends presentation when a pending estimate is under the cap."""
    market = (sj.get("market_id") or "us")
    level = _level("a7.auto_present_estimate", market, env_prefix)
    sj_id = sj.get("service_journey_id")
    ests = ddb.scan_filter("DEALER_ServiceEstimates", "service_journey_id", sj_id, env_prefix)
    pending = [e for e in ests if e.get("status") in (None, "pending", "draft")]
    under = [e for e in pending if e.get("total") is not None and float(e["total"]) <= max_amount]
    if not under:
        return {"action": "auto_present_estimate", "skipped": "no pending estimate under cap", "level": level}
    e = under[0]
    reason = f"maintenance estimate {e.get('total')} under {max_amount} cap — auto-present (customer approves)"
    wrote = _write_rec(f"REC-MW-PRESENT-{sj_id}", "auto_present_estimate", level, sj, reason,
                       {"estimate_id": e.get("estimate_id"), "total": e.get("total"),
                        "max_amount": max_amount}, apply, env_prefix)
    return {"action": "auto_present_estimate", "estimate_id": e.get("estimate_id"),
            "total": e.get("total"), "level": level, "written": wrote}


# --- prioritize_cards (A7 supervisor promotion, supervisor-ordering constraint) ----------------------

_PRIORITIZE_SYSTEM = (
    "You order a service department's morning work cards. You are given the cards as JSON; "
    "each has an \"id\". Return ONLY JSON of the shape "
    "{\"order\": [\"<id>\", ...], \"rationales\": {\"<id>\": \"<one line>\"}} where order contains "
    "every id exactly once (most urgent first: promise slips before parts staging before dispatch; "
    "more actions on one RO means more urgency). Never add, remove, merge or edit a card. "
    "Rationales must only reference what the card itself says."
)


def prioritize_cards(cards: list[dict[str, Any]], *, market: str = "us",
                     env_prefix: str | None = None) -> tuple[list[dict[str, Any]], str]:
    """Order the morning-work cards with ONE guarded Converse call (supervisor-ordering constraint).

    The cards themselves stay deterministic — the model may only ORDER them and
    say why, never add/remove/edit one (the same fence pattern as D1: judgment
    on top, facts underneath). Returns (cards, decision_engine). The model's
    output is applied only when it is strict JSON naming every card id exactly
    once; anything else — local mode, throttle, a hallucinated id — keeps the
    deterministic order, and the run is traced either way."""
    if not _gen.enabled() or len(cards) < 2:
        return cards, "deterministic"
    ids = [c.get("service_journey_id") or f"card-{i}" for i, c in enumerate(cards)]
    t0 = _time.time()
    engine, rationales = "deterministic", {}
    try:
        import boto3
        rt = boto3.client("bedrock-runtime", region_name=_gen.REGION)
        payload = [{"id": i, **{k: c.get(k) for k in ("dealer_id", "actions")}}
                   for i, c in zip(ids, cards)]
        resp = rt.converse(
            modelId=_gen.MODEL,
            system=[{"text": _PRIORITIZE_SYSTEM}],
            messages=[{"role": "user", "content": [{"text": _json.dumps({"cards": payload})}]}],
            inferenceConfig={"maxTokens": 800, "temperature": 0},
            **_gen.guardrail_kwargs(),
        )
        text = resp["output"]["message"]["content"][0]["text"]
        out = _json.loads(text[text.index("{"): text.rindex("}") + 1])
        order = [str(i) for i in out.get("order") or []]
        if sorted(order) != sorted(ids):           # must be a permutation — ordering ONLY
            raise ValueError("model order is not a permutation of the card ids")
        by_id = dict(zip(ids, cards))
        rationales = out.get("rationales") or {}
        cards = [by_id[i] for i in order]
        for rank, (i, c) in enumerate(zip(order, cards), 1):
            c["agent_rank"] = rank
            if rationales.get(i):
                c["agent_rationale"] = str(rationales[i])[:200]
        engine = "bedrock"
    except Exception as e:                          # any failure keeps the deterministic order
        engine = f"deterministic ({type(e).__name__})"
    _trace.write_trace("a7.morning_prioritize", agent_id="A7",
                       outcome="ok" if engine == "bedrock" else "fallback",
                       latency_ms=int((_time.time() - t0) * 1000),
                       routing_decision=engine, market=market,
                       extra={"cards": len(cards), "source_ref": _SOURCE}, env_prefix=env_prefix)
    return cards, engine


def prepare_ro_brief(sj: dict[str, Any], env_prefix: str | None = None) -> dict[str, Any]:
    """Advisor brief per RO (spec §3.7 prepare_ro_brief; §1.3 "morning work done
    before shift start"): what is due, open recalls, the coverage snapshot, the
    lines already pre-drafted from known items, prior history, declined work and
    the A4 equity snapshot. Read-only — prepared at BOOK / 05:00, not at the
    counter. Every item carries the row it came from; nothing is invented."""
    from . import connectors as svc_connectors
    from .followup import declined_items

    sj_id = sj.get("service_journey_id")
    cust = sj.get("customer_id")
    vin = sj.get("vin")
    market = sj.get("market_id") or "us"

    # declined work still open for this customer/VIN (one entry per declined line)
    declined_open: list[dict[str, Any]] = []
    for row in (ddb.scan_filter("DEALER_DeclinedWork", "customer_id", cust, env_prefix) if cust else []):
        for d in declined_items(row):
            if (d.get("status") or "open") != "open":
                continue
            if vin and d.get("vin") and d.get("vin") != vin:
                continue
            declined_open.append(d)

    history = [s for s in ddb.scan_all("DEALER_ServiceJourneys", env_prefix)
               if s.get("vin") == vin and s.get("service_journey_id") != sj_id] if vin else []

    # due items: the predicted maintenance services + each open declined line
    due_items: list[dict[str, Any]] = []
    predicted = None
    if vin:
        try:
            predicted = svc_booking.predict_next_service(vin, env_prefix)
        except Exception:
            predicted = None
    if predicted:
        for name in predicted.get("recommended_services") or []:
            due_items.append({"item": name, "basis": predicted.get("basis"),
                              "due_by": predicted.get("due_by"), "overdue": bool(predicted.get("overdue")),
                              "source_ref": predicted.get("source_ref")})
    for d in declined_open:
        due_items.append({"item": f"Previously declined: {d.get('item')}",
                          "amount": d.get("amount"), "currency": d.get("currency"),
                          "declined_at": d.get("declined_at"),
                          "source_ref": f"DEALER_DeclinedWork:{d.get('declined_item_id')}"})

    # coverage snapshot (prepaid maintenance / warranty) for the VIN
    coverage: dict[str, Any] | None = None
    if vin:
        cov = svc_connectors.check_coverage(vin, "maint", market=market, env_prefix=env_prefix)
        inner = cov.get("coverage") or {}
        coverage = {"covered": cov.get("covered"), "pay_type": cov.get("pay_type"),
                    "name": inner.get("name"), "term_months": inner.get("term_months"),
                    "source_ref": cov.get("source_ref")}

    # lines already pre-drafted on the latest estimate for this RO
    ests = ddb.scan_filter("DEALER_ServiceEstimates", "service_journey_id", sj_id, env_prefix) if sj_id else []
    latest = max(ests, key=lambda e: e.get("updated_at") or e.get("created_at") or "") if ests else None
    predrafted = [{"line_id": l.get("line_id"), "item": l.get("item"),
                   "finding_status": l.get("finding_status"), "urgency": l.get("urgency"),
                   "source_ref": f"DEALER_ServiceEstimates:{latest.get('estimate_id')}"}
                  for l in ((latest or {}).get("lines") or [])]

    # equity in the car on this visit — not the customer's latest valuation (after a sale
    # that is the car they traded in, which is sales data, not this RO's)
    equity = None
    if cust and vin:
        from ..common import vehicles
        v = vehicles.valuation_for(vin, cust, env_prefix)
        if v:
            equity = {"equity": v.get("equity"), "currency": v.get("currency", "USD"),
                      "source_ref": f"DEALER_Valuations:{v.get('valuation_id')}"}

    source_refs = sorted({r for r in (
        [d.get("source_ref") for d in due_items]
        + [(coverage or {}).get("source_ref"), (equity or {}).get("source_ref")]
        + [p.get("source_ref") for p in predrafted]
        + ["DEALER_ServiceJourneys", _RECALL_SOURCE_REF]) if r})

    summary = "; ".join(filter(None, [
        f"{len(due_items)} item(s) due or previously declined" if due_items else "nothing recorded as due",
        f"{len(history)} prior visit(s) on this VIN",
        f"{len(predrafted)} line(s) already pre-drafted" if predrafted else None,
        (f"coverage on file: {coverage['name']}" if (coverage or {}).get("name") else None),
        (f"positive equity {equity['currency']} {equity['equity']}"
         if equity and (equity.get("equity") or 0) > 0 else None),
    ])) + "."

    return {"service_journey_id": sj_id, "vin": vin, "customer_id": cust,
            "summary": summary,
            "due_items": due_items,
            # No recall campaign table ships in the sandbox; the warranty/recall
            # connector is the live source. Reported honestly as empty, not faked.
            "open_recalls": [], "recalls_source_ref": _RECALL_SOURCE_REF,
            "coverage": coverage,
            "predrafted_lines": predrafted,
            "prior_visits": len(history), "declined_work_open": len(declined_open),
            "equity_snapshot": equity,
            "brief": (f"{len(history)} prior visits; {len(declined_open)} declined items; "
                      f"equity {equity.get('equity') if equity else 'n/a'}"),
            "prepared_at": _now(),
            "source_refs": source_refs,
            "source_ref": "A7.prepare_ro_brief (due + recalls + coverage + pre-drafted lines + A4 equity)"}
