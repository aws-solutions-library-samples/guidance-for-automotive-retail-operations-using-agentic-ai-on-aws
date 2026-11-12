"""A2 — answer_customer_question: grounded customer-facing Q&A, spec §3.2.

The one A2 tool that speaks directly to a customer (via /journeys/{id}/chat,
signed-link auth). It answers from the customer's OWN records — saved build,
finance offer (payment ranges only), trade valuation, active incentives, dealer
hours — and REFUSES/ESCALATES on the three question classes the spec fences off:
legal, credit-decision, and safety-recall.

  answer_customer_question(question, journey) -> CustomerReply

CustomerReply = {
    text, citations[] (each {source, source_ref}), suggested_actions[]
    (book_test_drive|view_build|start_prequal|get_trade_estimate),
    escalate_to_human: bool, refused: bool, category
}

Design (mirrors agents/objection.py):
  * Guardrail FIRST, deterministic: the refuse/escalate classifier runs before
    any grounding or model call. Safety-critical decisions are not left to the
    LLM alone — a keyword gate catches legal / credit-decision / recall intent
    and returns an escalation without ever attempting an answer.
  * Grounded: the answer is anchored to the customer's real records, each
    carrying a source_ref used as a citation. No invented numbers.
  * Always-answers safely: if Bedrock is unavailable, a grounded template reply
    is returned (never a blank, never an ungrounded guess).
"""
from __future__ import annotations

import os
from typing import Any

from ..common import authz, ddb

BEDROCK_MODEL = os.environ.get("RETAIL_OPS_BEDROCK_MODEL", "us.anthropic.claude-opus-4-5-20251101-v1:0")
BEDROCK_REGION = os.environ.get("AWS_REGION", "us-east-1")

# --- refuse/escalate guardrail (spec §3.2) ----------------------------------
# These three classes MUST NOT be answered by the agent. Each escalates to a
# human with a safe holding message. Deterministic cues, checked before grounding.
_REFUSE_CUES: dict[str, tuple[str, ...]] = {
    # Legal advice / contract interpretation / disputes.
    "legal": ("legal", "lawyer", "attorney", "sue", "lawsuit", "arbitration",
              "contract mean", "am i liable", "my rights", "lemon law", "gdpr",
              "regulation", "is it legal"),
    # Credit decisions / approvals / scores — F&I gives ranges, never decisions.
    "credit_decision": ("approved", "will i qualify", "am i approved", "credit score",
                        "my credit", "denied", "decline my", "guarantee financing",
                        "will you finance me", "qualify for the loan", "get approved"),
    # Safety / recall / defect clearance — must route to service, never cleared by A2.
    "safety_recall": ("recall", "safe to drive", "airbag", "brakes fail", "catch fire",
                      "defect", "takata", "stop driving", "is it safe"),
}

_ESCALATION_TEXT = {
    "legal": ("That's a legal question I'm not able to advise on. I'm connecting you "
              "with your advisor, who can help or refer you appropriately."),
    "credit_decision": ("I can't make or predict a credit decision — that's handled "
                        "securely by the finance team. I can show you the payment "
                        "scenarios on your offer and start a secure prequal with your "
                        "advisor."),
    "safety_recall": ("For anything about vehicle safety or a recall, I want to get "
                      "this to the right people right away. I'm connecting you with "
                      "the service team, who can check your VIN and advise."),
}

# Actions the customer can take, keyed to intent cues in the question.
_ACTION_CUES: dict[str, tuple[str, ...]] = {
    "book_test_drive": ("test drive", "drive it", "come in", "see the car", "appointment"),
    "start_prequal": ("finance", "payment", "monthly", "apr", "lease", "loan", "prequal"),
    "get_trade_estimate": ("trade", "my current car", "worth", "trade-in"),
    "view_build": ("build", "configuration", "spec", "color", "package", "options"),
}

_SYSTEM = (
    "You are a helpful, warm assistant for a car dealership, answering a "
    "customer's question in a chat. Answer ONLY from the grounded facts provided "
    "(their saved build, offer payment ranges, trade estimate, active incentives, "
    "dealer hours). Keep it to 2-4 sentences. You MUST NOT: make or predict a "
    "credit decision or approval, give legal advice, clear a safety or recall "
    "concern, or invent any price, rate, incentive, or figure not in the facts. "
    "If the facts don't cover the question, say you'll connect them with their "
    "advisor. Never promise a specific discount."
)


class NotFound(Exception):
    pass


# --- guardrail --------------------------------------------------------------

def classify_refusal(question: str) -> str | None:
    """Return the refuse/escalate category if the question is fenced, else None."""
    q = (question or "").lower()
    for category, cues in _REFUSE_CUES.items():
        if any(c in q for c in cues):
            return category
    return None


def _suggested_actions(question: str, journey: dict[str, Any]) -> list[str]:
    q = (question or "").lower()
    actions = [a for a, cues in _ACTION_CUES.items() if any(c in q for c in cues)]
    # Always give at least one sensible next step by stage if none matched.
    if not actions:
        stage = journey.get("stage")
        actions = ["book_test_drive"] if stage in ("DISCOVER", "CONFIGURE", "QUALIFY") \
            else ["start_prequal"] if stage in ("FINANCE",) \
            else ["get_trade_estimate"] if stage in ("TRADE_IN",) else ["view_build"]
    return actions


# --- grounding --------------------------------------------------------------

def _build_name(cfg: dict[str, Any], env_prefix: str | None) -> str:
    """'Meridian Ridgeline Touring' from a configuration row, falling back to the ids."""
    model_id, trim_id = cfg.get("model_id"), cfg.get("trim_id")
    name = None
    try:
        from ..agents.assistant import _model_names
        name = _model_names(env_prefix).get(model_id)
    except Exception:  # noqa: BLE001 — a naming lookup must never cost the customer an answer
        name = None
    trim = ddb.get_item("OEM_Catalog", {"trim_id": trim_id}, env_prefix) if trim_id else None
    trim_name = (trim or {}).get("trim_name") or (trim or {}).get("name")
    return " ".join(str(p) for p in (name or model_id, trim_name) if p) or "your configuration"


def _gather_facts(journey: dict[str, Any], env_prefix: str | None) -> tuple[list[str], list[dict]]:
    """Collect grounded facts + citations from the customer's own records.

    Payment facts are RANGES/scenarios from an existing offer — never a new
    credit decision. Every fact line carries a citation with its source_ref."""
    facts: list[str] = []
    cites: list[dict] = []
    jid = journey.get("journey_id")
    cid = journey.get("customer_id")

    # saved build / configuration — named, so "what build did I save?" is answerable
    # (2026-10-27 eval: the fact carried only the totals and the model could not say which car)
    for cfg in ddb.scan_filter("PLAT_Configurations", "journey_id", jid, env_prefix):
        price = cfg.get("price_total")
        monthly = cfg.get("est_monthly_buy")
        inc = cfg.get("incentives_total")
        facts.append(f"Saved build: {_build_name(cfg, env_prefix)}, total {price} {cfg.get('currency','')}, "
                     f"est. monthly (buy) {monthly}, incentives applied {inc}.")
        cites.append({"source": "saved_build", "source_ref": cfg.get("source_ref"),
                      "config_id": cfg.get("config_id")})
        break

    # finance offer — payment scenarios only, explicitly not a decision
    for off in ddb.scan_filter("DEALER_FinanceOffers", "journey_id", jid, env_prefix):
        facts.append(f"Existing finance offer (illustrative, not a credit decision): "
                     f"{off.get('monthly_payment')} {off.get('currency','')}/mo over "
                     f"{off.get('term_months')} months at {off.get('apr_pct')}% APR, "
                     f"status {off.get('status')}.")
        cites.append({"source": "finance_offer", "source_ref": off.get("source_ref"),
                      "offer_id": off.get("offer_id")})
        break

    # trade valuation
    for val in ddb.scan_filter("DEALER_Valuations", "customer_id", cid, env_prefix):
        facts.append(f"Trade estimate: {val.get('trade_value')} {val.get('currency','')} "
                     f"for the {val.get('year')} {val.get('make')} {val.get('model')} "
                     f"(equity {val.get('equity')} after payoff {val.get('payoff')}).")
        cites.append({"source": "trade_valuation", "source_ref": val.get("source_ref"),
                      "valuation_id": val.get("valuation_id")})
        break

    # active incentives — scoped by market, the build's powertrain and the customer's state, the
    # same way the advisor's A3 tool is (2026-10-27 eval: the unscoped list told a Georgia ICE
    # customer about New York and Colorado EV rebates)
    from ..agents import intel
    active_inc = intel.get_incentives(journey, env_prefix).get("incentives") or []
    if active_inc:
        names = ", ".join(i.get("name", "") for i in active_inc[:3])
        facts.append(f"Active incentives you may qualify for: {names}.")
        cites.append({"source": "incentives", "source_ref": active_inc[0].get("source_ref"),
                      "count": len(active_inc)})

    # dealer hours / location
    dealer = authz.org_node(journey.get("dealer_id"), journey.get("market_id"), env_prefix)
    if dealer:
        facts.append(f"Your dealership is {dealer.get('name')} in "
                     f"{dealer.get('city','')} ({dealer.get('timezone','')}).")
        cites.append({"source": "dealer", "source_ref": f"org:{dealer.get('node_id')}",
                      "dealer_id": dealer.get("node_id")})

    return facts, cites


def _bedrock_answer(question: str, facts: list[str]) -> tuple[str, str]:
    """Grounded answer via Bedrock; (text, path). Falls back to a grounded
    template if the model is unavailable."""
    fact_block = "\n".join(f"- {f}" for f in facts) or "- (no specific records on file yet)"
    fallback = ("Here's what I have on your journey:\n" + fact_block +
                "\nWant me to connect you with your advisor for anything else?")
    try:
        import boto3
        rt = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
        prompt = (f"Grounded facts about this customer's journey:\n{fact_block}\n\n"
                  f"Customer question: \"{question}\"\n\nAnswer from the facts only.")
        from ..platform import generate as _gen
        resp = rt.converse(
            modelId=BEDROCK_MODEL, system=[{"text": _SYSTEM}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 260, "temperature": 0.3},
            **_gen.guardrail_kwargs())
        text = resp["output"]["message"]["content"][0]["text"].strip()
        if resp.get("stopReason") == "guardrail_intervened":
            return (text or fallback), "bedrock (guardrail intervened)"
        return (text or fallback), ("bedrock" if text else "fallback")
    except Exception:
        return fallback, "fallback"


def answer_customer_question(question: str, journey: dict[str, Any],
                             env_prefix: str | None = None) -> dict[str, Any]:
    """The A2 grounded-Q&A entry point. Returns a CustomerReply.

    Order: (1) refuse/escalate guardrail, (2) gather grounded facts,
    (3) grounded answer, (4) attach suggested actions."""
    jid = journey.get("journey_id")

    # 1) guardrail — fenced classes never reach grounding or the model.
    category = classify_refusal(question)
    if category:
        return {
            "journey_id": jid,
            "question": question,
            "refused": True,
            "category": category,
            "escalate_to_human": True,
            "text": _ESCALATION_TEXT[category],
            "citations": [],
            "suggested_actions": (["start_prequal"] if category == "credit_decision" else []),
            "generated_by": "guardrail",
        }

    # 2) grounded facts + citations
    facts, cites = _gather_facts(journey, env_prefix)

    # 3) grounded answer
    text, path = _bedrock_answer(question, facts)

    # 4) actions + reply
    return {
        "journey_id": jid,
        "question": question,
        "refused": False,
        "category": "answered",
        "escalate_to_human": False,
        "text": text,
        "citations": cites,
        "suggested_actions": _suggested_actions(question, journey),
        "generated_by": path,               # 'bedrock' | 'fallback'
        "model": BEDROCK_MODEL if path == "bedrock" else None,
    }
