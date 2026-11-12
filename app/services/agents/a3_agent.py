"""A3 — Dealer Sales Advisor as a reasoning agent (Strands on Amazon Bedrock).

ADR 0002 (decided 2026-10-08): the deterministic modules in this package stay
the *tools*; a Strands agent plans over them. This is the pilot of that pattern
on the Assistant screen. The agent:

  * picks its own tool sequence per question (lifecycle context, recent
    activity, suggestions, incentives, trade equity, knowledge search,
    objection strategy, customer memory, draft follow-up, create task,
    send outreach);
  * runs behind the same deterministic fences as the one-shot path — the A2
    refuse/escalate classifier runs BEFORE the agent (credit / legal / approval
    questions never reach the model), every effectful tool checks the caller's
    scope and the autonomy level, and the Bedrock Guardrail is attached to the
    model (app/infra/deploy_guardrails.py);
  * reports what it did: `tools_used`, `citations` (every source_ref a tool
    returned), `engine`, `guardrail`, and a PLAT_AgentTraces row per run.

`run()` is the single entry; it falls back to `assistant.assistant_chat` (the
deterministic facts + passages path) when Strands/Bedrock are unavailable, so
the Assistant always answers and says which engine answered.

Scope: tools read the caller's claims from a context variable set by `run()`;
a journey outside the caller's dealer scope raises before any data is read.
"""
from __future__ import annotations

import contextvars
import os
import time
from typing import Any

from ..common import autonomy_config, ddb, trace as _trace
from ..common.authz import Forbidden, check_journey_access
from ..platform import generate, knowledge, memory as _memory, tasks as _tasks
from . import assistant, equity, intel, suggestions as a3_suggestions

# The agent loop makes several model calls per question; a Sonnet-class profile keeps
# a 3–5 tool run inside the API Gateway budget. Override with RETAIL_OPS_A3_AGENT_MODEL.
MODEL = os.environ.get("RETAIL_OPS_A3_AGENT_MODEL",
                       os.environ.get("RETAIL_OPS_BEDROCK_MODEL_FAST", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"))
REGION = os.environ.get("AWS_REGION", "us-east-1")
OUTREACH_ACTION = "a2.schedule_outreach"
MAX_TOOL_CALLS = 8

# What each outreach kind promises the customer (mirrors assistant._OUTREACH_KIND;
# a plain follow_up promises nothing specific, so it records no memory).
_PROMISED_BY_KIND = {"finance_link": "to share payment options",
                     "test_drive_link": "to hold a test-drive time",
                     "callback": "a callback from the advisor"}

# Per-run context (claims, env_prefix, collected citations + tool log).
_CTX: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar("a3_agent_ctx")


def _ctx() -> dict[str, Any]:
    try:
        return _CTX.get()
    except LookupError:  # tool called outside run(); behave as an unscoped sandbox
        return {"claims": {"user_id": "sandbox", "role": "dealer_advisor", "is_oem": True},
                "env_prefix": None, "citations": [], "tools": []}


def _cite(label: str, ref: str | None) -> None:
    if ref:
        _ctx()["citations"].append({"label": label, "ref": ref})


def _log(tool: str, **kw: Any) -> None:
    _ctx()["tools"].append({"tool": tool, **kw})


def _journey(journey_id: str) -> dict[str, Any]:
    c = _ctx()
    j = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id}, c["env_prefix"])
    if j is None:
        raise ValueError(f"journey {journey_id} not found")
    check_journey_access(c["claims"], j)       # raises Forbidden outside scope
    return j


# --- tools (plain functions; wrapped with strands.tool inside run()) --------------

def get_lifecycle_context(journey_id: str) -> dict[str, Any]:
    """Where this purchase journey stands: stage, days in stage, what is missing
    before it can advance (the handoff gate), next appointment, open work."""
    j = _journey(journey_id)
    out = assistant.get_lifecycle_context(j, _ctx()["env_prefix"])
    _log("get_lifecycle_context", journey_id=journey_id)
    _cite("journey", f"DEALER_Journeys:{journey_id}")
    return out


def get_recent_activity(journey_id: str, limit: int = 8) -> list[dict[str, Any]]:
    """The customer's recent signals, journey events and messages, newest first, each with
    `days_ago`. Use it to anchor any suggestion in something that actually happened; quote
    recency from `days_ago`, never from your own date arithmetic."""
    j = _journey(journey_id)
    rows = assistant.get_recent_activity(j.get("customer_id"), journey_id, limit, _ctx()["env_prefix"])
    _log("get_recent_activity", journey_id=journey_id, n=len(rows))
    for r in rows[:limit]:
        _cite(r.get("type") or "activity", r.get("source_ref") or r.get("event_id") or r.get("signal_id"))
    return rows


def get_contextual_suggestions(journey_id: str) -> dict[str, Any]:
    """Stage-appropriate next steps for the advisor, each with the activity that
    justifies it ("why"). Only suggestions with evidence are returned."""
    j = _journey(journey_id)
    role = "service" if _ctx()["claims"].get("role") == "dealer_service" else "sales"
    out = a3_suggestions.generate_contextual_suggestions(j, role, _ctx()["env_prefix"])
    _log("get_contextual_suggestions", journey_id=journey_id, n=len(out.get("suggestions") or []))
    for s in out.get("suggestions") or []:
        _cite((s.get("evidence") or {}).get("type") or "activity", s.get("why"))
    return out


def get_incentives(journey_id: str) -> dict[str, Any]:
    """Active OEM incentives for the journey's model and market, with source refs.
    Never invent an incentive that is not returned here."""
    j = _journey(journey_id)
    out = intel.get_incentives(j, _ctx()["env_prefix"])
    _log("get_incentives", journey_id=journey_id)
    for inc in out.get("incentives") or []:
        _cite("incentive", inc.get("source_ref") or inc.get("incentive_id"))
    return out


def get_trade_equity(journey_id: str) -> dict[str, Any]:
    """Trade value, payoff, equity, payment delta and upgrade signal for the
    customer's current vehicle — every figure carries its source."""
    _journey(journey_id)
    out = equity.build_equity(journey_id, _ctx()["env_prefix"], explain=False)   # the agent phrases it
    _log("get_trade_equity", journey_id=journey_id)
    for ref in (out.get("source_refs") or {}).values():
        _cite("valuation", ref)
    return {k: out.get(k) for k in ("equity", "trade_value", "payoff", "currency", "equity_position",
                                    "payment_delta", "upgrade_signal", "current_vehicle", "explanation")}


def search_knowledge(query: str, collection: str | None = None, k: int = 3) -> dict[str, Any]:
    """Search the dealership knowledge corpus (CRM notes, bulletins, product guides,
    objection strategies, prior plans, transcripts). Optional collection filter.
    Returns passages with source_ref — cite them when you use them."""
    kb = knowledge.get_knowledge_base()
    market = _ctx()["claims"].get("market_id") or "us"
    try:
        out = kb.retrieve(query, collection, k, _ctx()["env_prefix"], market=market)
    except TypeError:                                   # local index has no market filter
        out = kb.retrieve(query, collection, k, _ctx()["env_prefix"])
    _log("search_knowledge", query=query[:80], engine=out.get("engine"), n=out.get("count"))
    for p in out.get("passages") or []:
        _cite("knowledge", p.get("source_ref"))
    return out


def get_objection_strategy(objection_type: str, journey_id: str | None = None) -> dict[str, Any]:
    """The curated response strategy for an objection type (price, payment,
    trade_value, timing, competitor, range_anxiety, ...), with its success rate."""
    market = _journey(journey_id).get("market_id", "us") if journey_id else _ctx()["claims"].get("market_id") or "us"
    out = assistant.get_objection_strategy(objection_type, market, _ctx()["env_prefix"]) or {}
    _log("get_objection_strategy", objection_type=objection_type, found=bool(out))
    _cite("objection_strategy", out.get("source_ref") or out.get("objection_strategy_id"))
    return out or {"found": False, "objection_type": objection_type}


def draft_follow_up(journey_id: str, channel: str = "email", topic: str | None = None) -> dict[str, Any]:
    """Draft (never send) a follow-up message for the journey on a channel
    (email | sms), rendered from the customer's record and the active template.
    topic="saved_build" opens with the configuration they saved; otherwise the newest signal."""
    j = _journey(journey_id)
    out = assistant.draft_follow_up(journey_id, channel, _ctx()["env_prefix"], topic=topic)
    _log("draft_follow_up", journey_id=journey_id, channel=channel, topic=topic)
    # The draft's template commits the advisor to a next step (durable-memory contract: promises
    # made are remembered so a later touch never contradicts this one).
    step = assistant._NEXT_STEP.get(j.get("stage"))
    if step and j.get("customer_id"):
        _memory.remember(j["customer_id"], "promise",
                         f"Follow-up draft ({channel}) promised: {step}",
                         source_ref=f"A3.draft_follow_up:{journey_id}", actor="A3-agent",
                         env_prefix=_ctx()["env_prefix"])
    return out


def create_task(journey_id: str, title: str, due_in_hours: int = 24) -> dict[str, Any]:
    """Open a task for the journey's owner (or the caller) so a person follows up.
    Use for anything that needs a human: callbacks, documents, approvals."""
    import datetime as _dt
    j = _journey(journey_id)
    c = _ctx()
    due = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=due_in_hours)).replace(microsecond=0)
    t = _tasks.create_task(title[:120], j.get("owner_user_id") or c["claims"]["user_id"],
                           created_by=f"A3-agent:{c['claims']['user_id']}", journey_id=journey_id,
                           dealer_id=j.get("dealer_id"), due_at=due.isoformat().replace("+00:00", "Z"),
                           source_agent="A3", env_prefix=c["env_prefix"])
    _log("create_task", journey_id=journey_id, task_id=t.get("task_id"))
    return {"task_id": t.get("task_id"), "assignee": t.get("assigned_user_id"), "due_at": t.get("due_at")}


def send_outreach(journey_id: str, kind: str = "follow_up", channel: str = "email") -> dict[str, Any]:
    """Send a consented follow-up to the customer (kind: follow_up | finance_link |
    test_drive_link | callback). Autonomy-gated: below 'act_and_notify' this does
    NOT send — it records a recommendation card for the advisor to approve."""
    j = _journey(journey_id)
    c = _ctx()
    market = j.get("market_id") or "us"
    level = autonomy_config.active_level(OUTREACH_ACTION, market, c["env_prefix"])
    if level in autonomy_config.EXECUTE_LEVELS:
        out = assistant.send_outreach(journey_id, kind, channel, c["claims"]["user_id"], env_prefix=c["env_prefix"])
        _log("send_outreach", journey_id=journey_id, kind=kind, channel=channel, status="sent", level=level)
        # A sent message of these kinds makes the customer a promise — remember
        # it (durable-memory contract) so later agents and advisors honor it.
        promised = _PROMISED_BY_KIND.get(kind)
        if promised and j.get("customer_id"):
            _memory.remember(j["customer_id"], "promise", f"Promised {promised} via {channel}",
                             source_ref=f"A3.send_outreach:{journey_id}", actor=c["claims"]["user_id"],
                             env_prefix=c["env_prefix"])
        return {"status": "sent", "autonomy_level": level, **{k: out.get(k) for k in ("message_id", "channel", "to")}}
    from ..platform import recommendations as _recs
    rec = _recs.record_shown("A3", "outreach_draft", shown_to=c["claims"]["user_id"], journey_id=journey_id,
                             dealer_id=j.get("dealer_id"), autonomy_level=level,
                             reason=f"The Sales Agent proposed a {kind} on {channel}; held for the advisor",
                             extra={"kind": kind, "channel": channel}, env_prefix=c["env_prefix"])
    _log("send_outreach", journey_id=journey_id, kind=kind, channel=channel, status="held_for_human", level=level)
    return {"status": "held_for_human", "autonomy_level": level,
            "recommendation_id": rec.get("recommendation_id"),
            # Precise about what exists, so the agent does not embellish ("a draft is waiting in your
            # queue" was judged unsupported, 2026-10-28 eval): a card, no message.
            "note": (f"Nothing was sent and no message was drafted. A recommendation card "
                     f"({rec.get('recommendation_id')}) proposing a {kind} by {channel} was recorded for the advisor; "
                     f"autonomy level '{level}' is below act_and_notify, so the advisor sends it themselves.")}


def build_call_list(limit: int = 5) -> dict[str, Any]:
    """Who to work today, ranked with the reason for each (appointment today, stalled, missing
    handoff context, late stage, days since last touch) and a top suggestion per journey. The
    caller's own journeys (a manager sees their stores'). Use it for 'who should I call first'
    questions, which need no journey_id."""
    c = _ctx()
    claims = c["claims"]
    dealer_ids = claims.get("dealer_ids")
    manager = claims.get("role") in ("dealer_manager", "dealer_gm", "dealer_principal")
    out = assistant.build_call_list(None if manager and dealer_ids else claims.get("user_id"),
                                    set(dealer_ids) if dealer_ids else None, limit=limit, env_prefix=c["env_prefix"])
    _log("build_call_list", n=out.get("count"))
    _cite("call_list", out.get("source_ref"))
    for it in out.get("items") or []:
        _cite("journey", f"DEALER_Journeys:{it.get('journey_id')}")
    return out


def customer_memory(journey_id: str, k: int = 8) -> list[dict[str, Any]]:
    """What we already know about this customer across journeys and sessions —
    preferences stated, objections raised, promises made (newest first, each
    with its source). Read-only; use it so you never contradict a promise."""
    j = _journey(journey_id)
    rows = _memory.recall(j.get("customer_id"), k=k, env_prefix=_ctx()["env_prefix"])
    _log("customer_memory", journey_id=journey_id, n=len(rows))
    for r in rows:
        _cite(f"memory:{r.get('kind')}", r.get("source_ref"))
    return rows


TOOL_FUNCS = [get_lifecycle_context, get_recent_activity, get_contextual_suggestions, get_incentives,
              get_trade_equity, search_knowledge, get_objection_strategy, customer_memory,
              build_call_list, draft_follow_up, create_task, send_outreach]

SYSTEM_PROMPT = (
    "You are the Dealer Sales Advisor assistant inside a dealership system. You help a sales "
    "advisor with ONE customer journey at a time; the message carries its [journey_id=...]. Without "
    "one, you can still answer 'who should I work today' with build_call_list — do not ask for an id.\n"
    "Rules:\n"
    "1. Use the tools to get facts. Never state a price, payment, incentive, date, availability, "
    "equity figure or policy that a tool did not return — no estimates, typical ranges or rules of "
    "thumb either (if a tool returns no payment delta, there is no payment figure to give). Quote each "
    "figure with the label and unit the tool gave it: do not relabel it (a deduction cap is 'up to "
    "$10,000 deductible', not '$10,000 in benefits'), turn it into a range, or combine figures. If a "
    "tool has no data, say so and name the next step.\n"
    "   The message starts with [today=YYYY-MM-DD] so you can name dates ('tomorrow', 'by Friday'). "
    "Elapsed time is a tool field: quote days_ago, idle_days or days_in_stage as returned; never "
    "compute 'N days ago' yourself, and never convert a 0-1 confidence into a percentage.\n"
    "2. Cite: when you use a fact, mention where it came from in plain words (e.g. 'per the trade "
    "valuation', 'from the CRM note').\n"
    "3. No credit, legal or approval decisions — hand those to a person (create_task).\n"
    "4. Prefer the smallest next commitment (a test drive, a callback, a document) over a pitch.\n"
    "   Check customer_memory for preferences stated, objections raised and promises already made, "
    "so you never repeat a pitch they objected to or contradict a promise.\n"
    "5. Sending to the customer requires send_outreach, which may be held for human approval; "
    "say when that happened. Never claim something was sent unless the tool said 'sent'.\n"
    "6. Be concise: lead with the answer, then at most 4 short bullets."
)


def available() -> bool:
    """Strands importable and generation enabled (INTEGRATION_MODE=aws)?"""
    if not generate.enabled() and os.environ.get("RETAIL_OPS_A3_AGENT_FORCE") != "1":
        return False
    try:
        import strands  # noqa: F401
        return True
    except Exception:
        return False


def _build_agent():
    from strands import Agent, tool
    from strands.models import BedrockModel
    gcfg = generate.guardrail_config() or {}
    model_kw: dict[str, Any] = {"model_id": MODEL, "region_name": REGION, "temperature": 0.2, "max_tokens": 700}
    if gcfg:
        # guardrail_latest_message: screen the advisor's question (guardContent) on the input side, not
        # the tool results Strands feeds back as a user turn. Those are our own DynamoDB rows, and the
        # STANDARD-tier topic classifier misread them as a CreditDecisions / MISCONDUCT *input* in 1 of
        # ~15 turns (2026-10-27 eval, a3-trade and a3-no-fabrication) and redacted the whole turn. The
        # model's answer is still screened on the output side.
        model_kw.update(guardrail_id=gcfg["guardrailIdentifier"], guardrail_version=gcfg["guardrailVersion"],
                        guardrail_trace="enabled", guardrail_latest_message=True)
    model = BedrockModel(**model_kw)
    tools = [tool(f) for f in TOOL_FUNCS]
    return Agent(model=model, tools=tools, system_prompt=SYSTEM_PROMPT, callback_handler=None)


def _strands_trace(agent: Any, result: Any) -> dict[str, Any]:
    """Native Strands telemetry for the eval harness (app/eval/uaef_bridge.py): the full
    conversation (toolUse / toolResult blocks) plus the EventLoopMetrics summary, in the
    shape UAEF's StrandsAdapter consumes. Only run(capture_trace=True) returns it; the API
    response never carries it (tool results can be several KB per turn)."""
    try:
        summary = result.metrics.get_summary()
    except Exception:  # noqa: BLE001 — fake agents in tests have no metrics; telemetry is best-effort
        summary = {}
    return {"messages": list(getattr(agent, "messages", None) or []),
            "metrics_summary": summary if isinstance(summary, dict) else {},
            "stop_reason": getattr(result, "stop_reason", None), "model": MODEL}


def run(question: str, journey_id: str | None, claims: dict[str, Any], *,
        env_prefix: str | None = None, correlation_id: str | None = None,
        capture_trace: bool = False) -> dict[str, Any]:
    """Answer one advisor question with the reasoning agent; deterministic fallback.

    capture_trace=True adds `strands_trace` (native messages + metrics) for evaluation."""
    q = (question or "").strip()
    if not q:
        raise ValueError("question is required")
    from ..journeys import qa
    refusal = qa.classify_refusal(q)
    if refusal:                                   # deterministic fence BEFORE the model
        return {"answer": assistant.refusal_text(refusal), "citations": [], "tools_used": [],
                "guardrail": {"applied": True, "intervened": False, "refusal": refusal},
                "engine": "deterministic (refusal gate)", "model": None, "actions": ["handoff_to_human"],
                "source_ref": "A3.agent (refusal gate)"}
    role = claims.get("role") or "dealer_advisor"
    if journey_id:
        j = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id}, env_prefix)
        if j is None:
            raise ValueError(f"journey {journey_id} not found")
        check_journey_access(claims, j)          # scope first, before any tool runs
    if not available():
        det = assistant.assistant_chat(q, journey_id, role, env_prefix, claims=claims)
        det["engine"] = f"{det.get('engine')} (agent unavailable)"
        det["tools_used"] = ["assistant_chat"]
        return det
    token = _CTX.set({"claims": claims, "env_prefix": env_prefix, "citations": [], "tools": []})
    t0 = time.time()
    try:
        agent = _build_agent()
        today = assistant._now_dt().date().isoformat()
        prompt = f"[today={today}] " + (f"[journey_id={journey_id}] " if journey_id else "") + q
        result = agent(prompt)
        text = str(result).strip()
        c = _CTX.get()
        seen, citations = set(), []
        for cit in c["citations"]:
            key = (cit["label"], cit["ref"])
            if key not in seen:
                seen.add(key); citations.append(cit)
        metrics = getattr(result, "metrics", None)
        tool_names = sorted({t["tool"] for t in c["tools"]})
        gr = {"applied": generate.guardrail_config() is not None,
              "intervened": getattr(result, "stop_reason", "") == "guardrail_intervened"}
        out = {"answer": text, "citations": citations, "tools_used": tool_names, "tool_log": c["tools"][:MAX_TOOL_CALLS * 2],
               "engine": "strands", "model": MODEL, "guardrail": gr, "stop_reason": getattr(result, "stop_reason", None),
               "latency_ms": int((time.time() - t0) * 1000),
               "cycles": getattr(getattr(metrics, "accumulated_metrics", None), "get", lambda *_: None)("cycleCount"),
               "source_ref": "A3.agent (Strands on Bedrock; tools = agents.*)"}
        if capture_trace:
            out["strands_trace"] = _strands_trace(agent, result)
        _trace.write_trace("assistant_agent", agent_id="A3", outcome="ok" if text else "empty",
                           latency_ms=out["latency_ms"], journey_id=journey_id, correlation_id=correlation_id,
                           citations=[c_["ref"] for c_ in citations][:10],
                           routing_decision="strands:" + ",".join(tool_names),
                           extra={"source_ref": out["source_ref"], "cycles": out["cycles"]}, env_prefix=env_prefix)
        return out
    except Forbidden:
        raise
    except Exception as e:
        det = assistant.assistant_chat(q, journey_id, role, env_prefix, claims=claims)
        det["engine"] = f"{det.get('engine')} (agent failed: {type(e).__name__})"
        det["tools_used"] = ["assistant_chat"]
        det["agent_error"] = str(e)[:400]          # operators need the reason; no PII in these messages
        _trace.write_trace("assistant_agent", agent_id="A3", outcome=f"fallback:{type(e).__name__}",
                           latency_ms=int((time.time() - t0) * 1000), journey_id=journey_id,
                           correlation_id=correlation_id, env_prefix=env_prefix)
        return det
    finally:
        _CTX.reset(token)
