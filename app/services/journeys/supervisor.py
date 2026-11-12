"""A2 — Purchase Journey Orchestration (supervisor core), spec §2.1 / §3.2.

A2 owns the purchase-journey state machine. This module is the deterministic
spine of that agent — the parts that must be exact rather than judged:

  validate_handoff(journey)            -> per-stage required-context gate (§2.1)
  advance_journey(journey_id, ...)     -> guarded transition + append-only event
  select_next_best_action(journey)     -> grounded NBA (no fabricated numbers)
  get_journey_state(journey_id)        -> supervisor view (stepper + gaps + NBA)

Invariants honored (spec §2.1):
  * The stage order is DISCOVER→CONFIGURE→QUALIFY→FINANCE→TRADE_IN→AGREE→
    DELIVER→OWN. A2 advances one stage at a time.
  * Nothing advances without an event written to DEALER_JourneyEvents — the
    event is written BEFORE the journey row flips, so the audit trail can never
    lag the state.
  * validate_handoff blocks a transition whose CURRENT stage is missing its
    required context (100% context-carried, the §1.3 handoff-completeness KPI).
  * OWN is a resting state; A2 does not advance out of OWN (a repeat purchase is
    a NEW journey via create_journey with previous_journey_id — not modeled here).
  * Every fact shown comes from the record; A2 invents no numbers.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import os
from typing import Any

from ..common import ddb
from ..common import labels as _labels
from ..events import bus as _bus
from .assist import JOURNEY_STAGES

_JTABLE = "DEALER_Journeys"
_ETABLE = "DEALER_JourneyEvents"

# Per-stage required context: to LEAVE <stage>, these keys must be truthy in the
# journey's `context` map. Derived from the §2.1 exit criteria. AGREE also
# carries the credit-compliance gate (OFAC / Red Flags / adverse action).
REQUIRED_CONTEXT: dict[str, list[str]] = {
    "DISCOVER": ["shortlist"],                       # shortlist of <=3 saved
    "CONFIGURE": ["saved_build"],                    # build w/ trim+pack+price
    "QUALIFY": ["budget", "timeline"],               # budget + timeline (+trade intent)
    "FINANCE": ["finance_offer"],                    # pre-qualified offer presented
    "TRADE_IN": ["valuation"],                       # valuation + equity recorded
    "AGREE": ["deal_structure", "credit_compliance"],  # deal + compliance gate
    "DELIVER": ["delivered"],                        # vehicle delivered
    "OWN": [],                                        # resting state
}

# Idle days beyond which a journey counts as stalled if the record didn't say so.
_STALL_SLA_DAYS = float(os.environ.get("RETAIL_OPS_STALL_SLA_DAYS", "14"))


class NotFound(Exception):
    pass


class HandoffIncomplete(Exception):
    """Raised when a transition is blocked by missing required context."""

    def __init__(self, stage: str, missing: list[str]):
        self.stage, self.missing = stage, missing
        from ..common.labels import missing_message
        super().__init__(missing_message(stage, missing))


class InvalidTransition(Exception):
    pass


def _now() -> str:
    return _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _next_stage(stage: str) -> str | None:
    try:
        i = JOURNEY_STAGES.index(stage)
    except ValueError:
        return None
    return JOURNEY_STAGES[i + 1] if i + 1 < len(JOURNEY_STAGES) else None


# --- validate_handoff -------------------------------------------------------

def validate_handoff(journey: dict[str, Any]) -> dict[str, Any]:
    """Check the CURRENT stage's required context (§2.1).

    Prefers the record's own `missing_context` when present (the synthetic data
    encodes intended gaps there); otherwise derives gaps from the `context` map
    against REQUIRED_CONTEXT. Returns {stage, required, present, missing, ok}."""
    stage = journey.get("stage")
    required = REQUIRED_CONTEXT.get(stage, [])
    ctx = journey.get("context") or {}
    present = [k for k in required if ctx.get(k)]

    recorded = journey.get("missing_context")
    if isinstance(recorded, list) and recorded:
        # Respect explicitly-recorded gaps, unioned with derived ones — but a
        # recorded gap clears once its context flag is set truthy (so supplying
        # the missing evidence via context_updates actually unblocks the gate).
        recorded_open = [k for k in recorded if not ctx.get(k)]
        derived = [k for k in required if not ctx.get(k)]
        missing = sorted(set(recorded_open) | set(derived))
    else:
        missing = [k for k in required if not ctx.get(k)]

    result = {"stage": stage, "required": required, "present": present,
              "missing": missing, "ok": not missing}

    # AGREE additionally requires the REAL credit-compliance gate (§2.1): a
    # context flag alone is not enough — OFAC / Red Flags / adverse-action must
    # be recorded as cleared on the deal. This is a hard, non-fabricated gate.
    if stage == "AGREE":
        try:
            from ..agents import deal as _deal
            cg = _deal.compliance_gate(journey)
            result["compliance_gate"] = cg
            if not cg["ok"]:
                # surface the specific uncleared checks in missing
                for chk in cg["missing"]:
                    tag = f"compliance:{chk}"
                    if tag not in missing:
                        missing.append(tag)
                result["missing"] = missing
                result["ok"] = False
        except Exception as e:            # never let the gate lookup crash validation
            result["compliance_gate"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            if "compliance:unavailable" not in missing:
                missing.append("compliance:unavailable")
            result["missing"] = missing
            result["ok"] = False

    return result


# --- advance_journey --------------------------------------------------------

def _event_row(journey_id: str, event_type: str, actor: str, evidence: str,
               payload: dict[str, Any], ts: str) -> dict[str, Any]:
    """Compose a DEALER_JourneyEvents row (composite key journey_id + sk).

    sk mirrors the existing corpus: <event_ts>#<correlation_id>#<seq>."""
    corr = hashlib.sha1(f"{journey_id}{ts}{event_type}".encode()).hexdigest()[:16]
    return {
        "event_id": f"EV-{corr}",
        "journey_id": journey_id,
        "sk": f"{ts}#{corr}#0000",
        "event_ts": ts,
        "event_type": event_type,
        "correlation_id": corr,
        "actor": {"type": "agent", "agent": "A2", "user_id": actor},
        "evidence": evidence,
        "payload": payload,
    }


def advance_journey(journey_id: str, evidence: str, actor: str,
                    context_updates: dict[str, Any] | None = None,
                    to_stage: str | None = None,
                    env_prefix: str | None = None) -> dict[str, Any]:
    """Advance a journey one stage, guarded by validate_handoff.

    context_updates: optional context keys to set true BEFORE the gate check
      (e.g. the advisor just captured budget), so a transition can supply the
      very evidence that clears its gate.
    to_stage: optional explicit next stage; must equal the natural next stage
      (A2 advances one stage at a time — no skipping).

    Order of operations (audit can never lag state):
      1) apply context_updates,
      2) validate_handoff on the CURRENT stage — block if incomplete,
      3) write the stage.advanced event,
      4) flip the journey row.
    """
    j = ddb.get_item(_JTABLE, {"journey_id": journey_id}, env_prefix)
    if j is None:
        raise NotFound(f"journey {journey_id} not found")

    cur = j.get("stage")
    nxt = _next_stage(cur)
    if nxt is None:
        raise InvalidTransition(f"{journey_id} is at terminal stage {cur}; a repeat "
                                f"purchase is a new journey (create_journey)")
    if to_stage is not None and to_stage != nxt:
        raise InvalidTransition(f"can only advance {cur} -> {nxt} (one stage at a time), "
                                f"got requested {to_stage}")

    # 1) apply any context the caller is supplying with this transition
    ctx = dict(j.get("context") or {})
    if context_updates:
        for k, v in context_updates.items():
            ctx[k] = v
    j["context"] = ctx

    # 2) gate on the current stage
    check = validate_handoff(j)
    if not check["ok"]:
        raise HandoffIncomplete(cur, check["missing"])

    ts = _now()

    # 3) append-only event FIRST
    ev = _event_row(journey_id, "stage.advanced", actor, evidence,
                    {"from": cur, "to": nxt}, ts)
    ddb.put_item(_ETABLE, ev, env_prefix)

    # 4) flip the journey row
    j["previous_stage"] = cur
    j["stage"] = nxt
    j["last_event_at"] = ts
    j["updated_at"] = ts
    j["stalled"] = False
    j["idle_days"] = 0
    j.pop("stall_notified_at", None)   # a fresh stall may fire again later (stall_sweep)
    # Clearing recorded gaps we just satisfied keeps the board honest.
    if isinstance(j.get("missing_context"), list):
        j["missing_context"] = [m for m in j["missing_context"] if not ctx.get(m)]
    ddb.put_item(_JTABLE, j, env_prefix)

    bus_event = _bus.emit("journey.stage_changed", {
        "journey_id": journey_id, "customer_id": j.get("customer_id"),
        "dealer_id": j.get("dealer_id"), "market_id": j.get("market_id"),
        "from": cur, "to": nxt, "journey_event_id": ev["event_id"]},
        actor=actor, resource_url=f"/dealer/journeys/{journey_id}", env_prefix=env_prefix)

    return {"journey_id": journey_id, "from": cur, "to": nxt,
            "event_id": ev["event_id"], "advanced_at": ts, "bus_event": bus_event}


# --- select_next_best_action ------------------------------------------------

# Stage -> the specialist agent A2 routes to, and the default action (§2.1 owners).
_STAGE_ROUTING: dict[str, dict[str, str]] = {
    "DISCOVER":  {"agent": "A1", "action": "recommend_vehicle", "channel": "web"},
    "CONFIGURE": {"agent": "A1", "action": "save_build",        "channel": "web"},
    "QUALIFY":   {"agent": "A3", "action": "book_test_drive",   "channel": "advisor"},
    "FINANCE":   {"agent": "A2", "action": "present_offer",     "channel": "advisor"},
    "TRADE_IN":  {"agent": "A4", "action": "compute_equity",    "channel": "advisor"},
    "AGREE":     {"agent": "A3", "action": "structure_deal",    "channel": "advisor"},
    "DELIVER":   {"agent": "A2", "action": "schedule_delivery", "channel": "email"},
    "OWN":       {"agent": "A4", "action": "monitor_equity",    "channel": "email"},
}


def _context_hash(journey: dict[str, Any]) -> str:
    """Stable bucket key for a journey's situation (stage, source, stall band,
    signal set) — the §3.2 `select_next_best_action` groups recommendations by
    this to compare candidate actions. Deterministic, no PII."""
    stage = journey.get("stage") or ""
    source = journey.get("source") or ""
    idle = journey.get("idle_days") or 0
    band = "fresh" if idle < 3 else "warm" if idle < 14 else "cold"
    raw = f"{stage}|{source}|{band}|stalled={bool(journey.get('stalled'))}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


# Candidates that are hard priorities (a gap or a stalled-and-blocked journey
# needs a person) are never reordered by learned outcomes.
_PRIORITY_ACTIONS = frozenset({"capture_context", "handoff_to_human"})
# Minimum conversion-rate edge before outcomes override the policy default.
_LEARN_MARGIN = float(os.environ.get("RETAIL_OPS_NBA_LEARN_MARGIN", "0.05"))

# Supervisor promotion (supervisor-promotion constraint): in aws mode the model may pick among the
# ALREADY-GATED candidates. It sees ids + reasons only, answers strict JSON at
# temperature 0, and any invalid/missing pick keeps the deterministic choice.
_NBA_PICK_SYSTEM = (
    "You choose the next best action for one dealership purchase journey. You are given the "
    "already-vetted candidate actions as JSON, each with an \"id\" and the reason it qualifies. "
    "Return ONLY JSON {\"pick\": \"<id>\", \"rationale\": \"<one line>\"} naming exactly one of "
    "the given ids. Prefer closing a context gap or involving a person over a nudge when both "
    "are offered. The rationale must only reference the candidates' own reasons."
)


def _model_pick(candidates: list[dict[str, Any]], journey: dict[str, Any],
                env_prefix: str | None) -> tuple[int, str] | None:
    """One guarded Converse call that picks among the gated candidates.

    Returns (index, rationale) or None (local mode, single candidate, bad JSON,
    hallucinated id, throttle, …) — None means the deterministic choice stands.
    Traced per run so the AI Log shows which engine chose."""
    if not candidates or len(candidates) < 2:
        return None
    from ..platform import generate as _gen
    if not _gen.enabled():
        return None
    import json as _json
    import time as _time
    ids = [f"c{i}" for i in range(len(candidates))]
    t0 = _time.time()
    picked: tuple[int, str] | None = None
    try:
        import boto3
        rt = boto3.client("bedrock-runtime", region_name=_gen.REGION)
        payload = [{"id": i, "action": c.get("action"), "route_to": c.get("route_to"),
                    "channel": c.get("channel"), "reason": c.get("reason")}
                   for i, c in zip(ids, candidates)]
        resp = rt.converse(
            modelId=_gen.MODEL,
            system=[{"text": _NBA_PICK_SYSTEM}],
            messages=[{"role": "user", "content": [{"text": _json.dumps({"candidates": payload})}]}],
            inferenceConfig={"maxTokens": 150, "temperature": 0},
            **_gen.guardrail_kwargs(),
        )
        text = resp["output"]["message"]["content"][0]["text"]
        out = _json.loads(text[text.index("{"): text.rindex("}") + 1])
        if out.get("pick") in ids:                  # ids only — anything else falls back
            picked = (ids.index(out["pick"]), str(out.get("rationale") or "")[:200])
    except Exception:
        picked = None                               # deterministic choice stands
    from ..common import trace as _trace
    _trace.write_trace("a2.select_next_best_action", agent_id="A2",
                       outcome="ok" if picked else "fallback",
                       latency_ms=int((_time.time() - t0) * 1000),
                       journey_id=journey.get("journey_id"), market=journey.get("market_id"),
                       routing_decision="bedrock" if picked else "deterministic",
                       extra={"candidates": len(candidates)}, env_prefix=env_prefix)
    return picked


def select_next_best_action(journey: dict[str, Any],
                            outcomes: dict[str, float] | None = None,
                            env_prefix: str | None = None) -> dict[str, Any]:
    """Choose the next action for a journey — the grounded, rules-bounded NBA.

    This is the deterministic policy floor of the §3.2 bandit: it picks the
    stage-appropriate play, but escalates to a human handoff when the journey is
    stalled AND missing context (the two signals that most need a person). The
    chosen action plus its alternatives and the context bucket are returned so
    the caller can log them as recommendations (source_ref = this journey).

    Supervisor promotion (supervisor-promotion constraint): candidate generation and every hard gate
    stay deterministic; in aws mode the model then picks AMONG the gated
    candidates (ids only, strict JSON, temperature 0). `chosen_by` reports
    which engine chose; any invalid/missing pick keeps the deterministic
    choice, which is also the local-mode behavior."""
    stage = journey.get("stage")
    route = _STAGE_ROUTING.get(stage, {"agent": "A2", "action": "review", "channel": "advisor"})
    handoff = validate_handoff(journey)
    stalled = bool(journey.get("stalled")) or (journey.get("idle_days") or 0) >= _STALL_SLA_DAYS

    candidates: list[dict[str, Any]] = []
    # Primary: the stage-owning specialist's default play.
    candidates.append({"action": route["action"], "route_to": route["agent"],
                       "channel": route["channel"], "reason": f"stage {stage} default play"})
    # If context is missing, the highest-value action is to close the gap.
    if not handoff["ok"]:
        candidates.insert(0, {"action": "capture_context", "route_to": "A3",
                              "channel": "advisor",
                              "reason": _labels.missing_message(stage, handoff["missing"]),
                              "needs": handoff["missing"]})
    # If stalled, a nurture nudge (or human handoff when also gap-blocked).
    if stalled:
        if not handoff["ok"]:
            candidates.insert(0, {"action": "handoff_to_human", "route_to": "dealer",
                                  "channel": "task",
                                  "reason": "stalled AND missing context — needs a person"})
        else:
            candidates.insert(0, {"action": "nudge", "route_to": "A2", "channel": "email",
                                  "reason": f"stalled {round(journey.get('idle_days') or 0,1)}d, context complete"})

    # Outcome learning (§4.10.3): when the outcome loop has conversion rates for
    # this context bucket, prefer the non-priority candidate that converts best —
    # by at least _LEARN_MARGIN over the policy default — so seeded outcomes can
    # change the choice (acceptance #34). Priority candidates stay on top.
    learned = None
    if outcomes and candidates[0]["action"] not in _PRIORITY_ACTIONS:
        if stalled:
            candidates.append({"action": "nudge", "route_to": "A2", "channel": "sms",
                               "reason": "stalled; alternative channel"})
        default_rate = outcomes.get(candidates[0]["action"], 0.0)
        best = max(candidates, key=lambda c: outcomes.get(c["action"], -1.0))
        if best is not candidates[0] and outcomes.get(best["action"], 0.0) >= default_rate + _LEARN_MARGIN:
            candidates.remove(best)
            candidates.insert(0, {**best, "reason": f"{best['reason']}; converts best in this context "
                                  f"({outcomes[best['action']]:.0%} vs {default_rate:.0%})"})
            learned = {"overrode": candidates[1]["action"], "rates": outcomes}

    # The model may re-pick among these ALREADY-GATED candidates (supervisor-promotion constraint); a
    # None result (local mode, bad pick, failure) keeps the deterministic head.
    chosen_by, rationale = "deterministic", None
    pick = _model_pick(candidates, journey, env_prefix)
    if pick is not None:
        idx, rationale = pick
        if idx != 0:
            candidates.insert(0, candidates.pop(idx))
        chosen_by = "bedrock"

    chosen = candidates[0]
    return {
        "learned_from_outcomes": learned,
        "journey_id": journey.get("journey_id"),
        "stage": stage,
        "context_hash": _context_hash(journey),
        "chosen": chosen,
        "chosen_by": chosen_by,
        "rationale": rationale,
        "alternatives": candidates[1:],
        "handoff": handoff,
        "source_ref": journey.get("journey_id"),
    }


def _outcomes_for(journey: dict[str, Any], env_prefix: str | None) -> dict[str, float]:
    try:
        from ..platform import recommendations
        return recommendations.conversion_rates(_context_hash(journey), env_prefix)
    except Exception:          # learning is advisory; never break the view
        return {}


# --- get_journey_state (supervisor view) ------------------------------------

def _stage_stepper(current: str) -> list[dict[str, Any]]:
    out, reached = [], True
    for st in JOURNEY_STAGES:
        is_current = st == current
        out.append({"stage": st, "current": is_current, "reached": reached})
        if is_current:
            reached = False
    return out


def _user_name(uid: str | None, env_prefix: str | None) -> str | None:
    if not uid:
        return None
    u = ddb.get_item("PLAT_Users", {"user_id": uid}, env_prefix) or {}
    return " ".join(filter(None, [u.get("given_name"), u.get("family_name")])) or uid


def get_journey_state(journey_id: str, event_limit: int = 10,
                      env_prefix: str | None = None) -> dict[str, Any]:
    """The A2 supervisor view of one journey: the stage stepper, the handoff
    gate for the current stage, the recommended next action, and the most
    recent events (newest first)."""
    j = ddb.get_item(_JTABLE, {"journey_id": journey_id}, env_prefix)
    if j is None:
        raise NotFound(f"journey {journey_id} not found")

    events = ddb.query_hash(_ETABLE, "journey_id", journey_id, env_prefix)
    events.sort(key=lambda e: e.get("event_ts") or "", reverse=True)
    recent = [{"event_ts": e.get("event_ts"), "event_type": e.get("event_type"),
               "actor": e.get("actor"), "evidence": e.get("evidence"),
               "payload": e.get("payload")} for e in events[:event_limit]]

    return {
        "journey_id": journey_id,
        "stage": j.get("stage"),
        "previous_stage": j.get("previous_stage"),
        "customer_id": j.get("customer_id"),
        "dealer_id": j.get("dealer_id"),
        "market_id": j.get("market_id"),
        "owner_user_id": j.get("owner_user_id"),
        "owner_name": _user_name(j.get("owner_user_id"), env_prefix),
        "pending_handoff": ({**j["pending_handoff"], "to_user_name": _user_name(j["pending_handoff"].get("to_user"), env_prefix)}
                            if j.get("pending_handoff") else None),
        "vin": j.get("vin"),
        "source": j.get("source"),
        "status": j.get("status"),
        "stalled": bool(j.get("stalled")),
        "idle_days": j.get("idle_days"),
        "nurture_active": bool(j.get("nurture_active")),
        "stepper": _stage_stepper(j.get("stage")),
        "handoff": validate_handoff(j),
        "next_best_action": select_next_best_action(j, outcomes=_outcomes_for(j, env_prefix),
                                                    env_prefix=env_prefix),
        "next_stage": _next_stage(j.get("stage")),
        "event_count": len(events),
        "recent_events": recent,
    }
