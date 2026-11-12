#!/usr/bin/env python3
"""Agent evaluation harness — golden prompts with deterministic checks (+ optional LLM judge).

Why: the agentic changes (Strands agent, Knowledge Base, guardrails) change *answers*,
and the unit tests only cover *tools*. This harness asserts the behaviours the spec
promises on a fixed prompt set, so a regression in grounding, fences or citations is
caught before deploy — and so the A3 pilot can be promoted on evidence.

Modes
  offline  (default; CI)  deterministic paths over the in-memory DynamoDB seeded from
                          the synthetic packs. No AWS. Checks refusals, citations,
                          forbidden claims, guardrail rule ids, scope.
  agent                   A3 cases through a3_agent.run (Strands on Bedrock) against
                          the live dev tables; adds tool-use, latency and guardrail
                          checks. Needs INTEGRATION_MODE=aws + credentials.
  api                     A3 cases through a deployed API (--api <base>, --token ...),
                          POST /assistant/agent; same checks as agent mode.

Checks per case (from each case's `expect`):
  refused / forbidden / explained   the fence fired (or not) as expected
  min_citations                      at least N citations returned
  forbidden_regex                    none of these patterns appear in the answer
  required_regex_any                 (agent/api) at least one of these patterns appears (refusals too)
  numbers_grounded                   (agent, default on) every currency amount, day count and
                                     percentage in the answer was returned by a tool this turn
  tools_any                          (agent/api) at least one of these tools was used
  guardrail_any                      (A6) one of these rule ids blocked the ask
  max_latency_ms                     (agent/api) default 25000

--judge adds an LLM-as-judge pass (Bedrock) scoring groundedness, citation use and
tone 1–5 for non-refused answers; the score is reported, not gating, until a baseline
exists (write one with --baseline-out, compare with --baseline).

--uaef (agent/api) adds graded UAEF metrics per A3 case (app/eval/uaef_bridge.py): tool
selection vs the case's `expected_tools`, latency vs the harness budget, token efficiency,
prompt-injection detection; --uaef-llm adds relevance, hallucination judged against the
tool results, safety, bias, toxicity. The native Strands trace is kept in the report so
`python -m app.eval.uaef_bridge --report <report>` can re-score it without re-running.

Exit code 1 when the pass rate is below --min-pass (default 1.0 offline, 0.9 otherwise).
Report: JSON to --out (default app/eval/reports/<mode>-<ts>.json) + a summary table.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
GOLDEN = Path(__file__).resolve().parent / "golden"
REPORTS = Path(__file__).resolve().parent / "reports"


# --- fixtures ---------------------------------------------------------------------

def _offline_db() -> None:
    """Point the service layer at the in-memory DynamoDB (same as tests/app)."""
    os.environ.setdefault("INTEGRATION_MODE", "local")
    os.environ["RETAIL_OPS_EVENT_BUS_LIVE"] = "0"
    from app.services.common import authz, ddb
    from app.services.common.local_ddb import FakeDynamoDB
    fake = FakeDynamoDB()
    fake.seed_markets()
    ddb._client.cache_clear()
    ddb._client = lambda: fake          # type: ignore[assignment]
    authz._org_nodes_cached.cache_clear()
    import boto3

    class _NoAws:
        def __init__(self, svc): self.svc = svc
        def __getattr__(self, n):
            def _b(*a, **k): raise RuntimeError(f"offline eval: boto3 {self.svc}.{n} blocked")
            return _b
    boto3.client = lambda service, *a, **k: _NoAws(service)   # type: ignore[assignment]


def _claims(c: dict[str, Any]) -> dict[str, Any]:
    out = dict(c)
    if isinstance(out.get("dealer_ids"), list):
        out["dealer_ids"] = set(out["dealer_ids"])
    return out


# --- runners (one per agent) ---------------------------------------------------------

def run_a3(case: dict[str, Any], claims: dict[str, Any], mode: str, api: str | None, token: str | None,
           env_prefix: str | None, capture_trace: bool = False) -> dict[str, Any]:
    q, jid = case["question"], case.get("journey_id")
    if mode == "api":
        import urllib.request
        body = json.dumps({"question": q, "journey_id": jid}).encode()
        req = urllib.request.Request(f"{api}/assistant/agent", data=body, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return {"__status__": e.code, "error": e.read().decode()[:300]}
    from app.services.agents import a3_agent, assistant
    from app.services.common.authz import Forbidden
    try:
        if mode == "agent":
            return a3_agent.run(q, jid, claims, env_prefix=env_prefix, capture_trace=capture_trace)
        out = assistant.assistant_chat(q, jid, claims.get("role") or "dealer_advisor", env_prefix, claims=claims)
        if jid:                                            # the one-shot path checks scope in the route
            from app.services.common import ddb
            from app.services.common.authz import check_journey_access
            j = ddb.get_item("DEALER_Journeys", {"journey_id": jid}, env_prefix)
            check_journey_access(claims, j)
        out.setdefault("tools_used", [])
        return out
    except Forbidden as e:
        return {"__status__": 403, "error": str(e)}
    except ValueError as e:
        return {"__status__": 404, "error": str(e)}


class _CaptureGrounding:
    """Record the facts + passages a service hands to generate.ground (A6, and anything else that
    grounds through it) without changing the service's response contract. Eval-side only."""

    def __init__(self) -> None:
        self.facts: list[str] = []

    def __enter__(self):
        from app.services.platform import generate
        self._gen, self._orig = generate, generate.ground

        def wrapped(question, facts, passages, **kw):
            self.facts = [str(f) for f in facts] + [str(p) for p in passages]
            return self._orig(question, facts, passages, **kw)
        generate.ground = wrapped
        return self

    def __exit__(self, *exc):
        self._gen.ground = self._orig
        return False


def run_assistant(agent: str, case: dict[str, Any], claims: dict[str, Any], env_prefix: str | None) -> dict[str, Any]:
    """A1 / A4 through the one-shot assistant (grounded generation in aws mode, deterministic facts
    offline), with the same scope check the /assistant route applies and the facts captured."""
    from app.services.agents import assistant
    from app.services.common import ddb
    from app.services.common.authz import Forbidden, check_journey_access
    jid = case.get("journey_id")
    try:
        if jid:
            j = ddb.get_item("DEALER_Journeys", {"journey_id": jid}, env_prefix)
            if j is None:
                return {"__status__": 404, "error": f"journey {jid} not found"}
            check_journey_access(claims, j)
        with _CaptureGrounding() as cap:
            out = assistant.assistant_chat(case["question"], jid, claims.get("role") or "dealer_advisor", env_prefix,
                                           agent_id=agent, claims=claims)
    except Forbidden as e:
        return {"__status__": 403, "error": str(e)}
    mg = out.get("model_guardrail") or {}
    return {"answer": out.get("answer"), "citations": out.get("citations") or [], "refused": bool(out.get("guardrail")),
            "category": out.get("guardrail"), "engine": out.get("engine"), "model": out.get("model"),
            "model_guardrail": {"applied": bool(mg.get("applied")), "intervened": bool(mg.get("intervened"))},
            "grounding": cap.facts, "tools_used": []}


def run_tool(agent: str, tool: str, case: dict[str, Any], claims: dict[str, Any], env_prefix: str | None) -> dict[str, Any]:
    """A deterministic tool through the AgentCore runtime dispatch (arg validation, role and journey
    scope fences, trace span) — the same path another agent's A2A call takes."""
    from app.agentcore import agent as runtime
    args = dict(case.get("args") or {})
    if case.get("journey_id") and "journey_id" not in args:
        args["journey_id"] = case["journey_id"]
    env = runtime._invoke({"kind": "tool", "agent": agent, "tool": tool, "args": args,
                           "claims": {**claims, "dealer_ids": sorted(claims.get("dealer_ids") or [])}})
    code, res = env.get("statusCode"), env.get("result") or {}
    if code != 200:
        return {"__status__": code, "error": (res or {}).get("error") if isinstance(res, dict) else str(res)}
    flat = {k: v for k, v in res.items() if not isinstance(v, (dict, list))}
    cites = []
    for ref in ([res.get("source_ref")] + list((res.get("source_refs") or {}).values())
                + [m.get("source_ref") for m in (res.get("matches") or []) if isinstance(m, dict)]):
        if ref:
            cites.append({"ref": ref})
    return {**res, **flat, "question": f"{agent}.{tool}({json.dumps(args)})",
            "answer": res.get("ai_insight") or res.get("explanation") or res.get("note") or json.dumps(flat, default=str)[:400],
            "citations": cites, "engine": res.get("insight_by") or "deterministic", "tools_used": [tool]}


def run_a5_roi(case: dict[str, Any], claims: dict[str, Any], env_prefix: str | None) -> dict[str, Any]:
    """A5 campaign ROI read model: the ai_insight text (Bedrock in aws mode) over the KPI facts."""
    from app.services.oem import campaigns
    out = campaigns.build_campaign_roi(claims, case.get("market") or claims.get("market_id") or "us", env_prefix=env_prefix)
    k = out.get("kpis") or {}
    facts = [f"{kk}={vv}" for kk, vv in k.items()] + [
        f"campaign {c.get('name')} ({c.get('campaign_id')}): budget={c.get('budget')} deliveries={c.get('attributed_deliveries')} roi={c.get('roi')}"
        for c in (out.get("campaigns") or []) if isinstance(c, dict)]
    facts.append(str((out.get("assumptions") or {}).get("note") or ""))
    return {"question": f"Summarize campaign ROI for {out.get('scope_node')} and where to shift budget",
            "answer": out.get("ai_insight"), "engine": out.get("insight_by"), "insight_by": out.get("insight_by"),
            "kpis": k, "campaigns": len(out.get("campaigns") or []),
            "citations": [{"ref": f"OEM_Campaigns:{out.get('market_id')}:{out.get('scope_node')}"}],
            "grounding": facts, "tools_used": ["build_campaign_roi"]}


def run_a2(case: dict[str, Any], env_prefix: str | None) -> dict[str, Any]:
    from app.services.common import ddb
    from app.services.journeys import qa
    j = ddb.get_item("DEALER_Journeys", {"journey_id": case["journey_id"]}, env_prefix)
    if j is None:
        return {"__status__": 404, "error": f"journey {case['journey_id']} not found"}
    out = qa.answer_customer_question(case["question"], j, env_prefix)
    rec = {"answer": out.get("text"), "citations": out.get("citations") or [], "refused": out.get("refused"),
           "escalate": out.get("escalate_to_human"), "category": out.get("category"),
           "engine": out.get("generated_by"), "model": out.get("model")}
    if not out.get("refused"):                    # the same facts the model was given, for grounding checks
        rec["grounding"], _ = qa._gather_facts(j, env_prefix)
    return rec


def run_a6(case: dict[str, Any], claims: dict[str, Any], env_prefix: str | None) -> dict[str, Any]:
    from app.services.oem import workspace
    with _CaptureGrounding() as cap:
        out = workspace.ask(case["question"], claims, view=case.get("view"), dealer_id=case.get("dealer_id"),
                            market=claims.get("market_id", "us"), env_prefix=env_prefix)
    # `guardrail` carries GR-00x rule ids on a deterministic block, or the Bedrock badge when the model
    # guardrail's grounding check intervened and the service fell back to its deterministic text. Only
    # the former is a refusal; the latter still answers and is reported as model_guardrail.
    rules = [g for g in (out.get("guardrail") or []) if str(g).startswith("GR-")]
    mg = out.get("model_guardrail") or {}
    return {"answer": out.get("answer"), "citations": out.get("citations") or [],
            "refused": bool(rules), "guardrail_rules": rules, "engine": out.get("engine"), "model": out.get("model"),
            "model_guardrail": {"applied": bool(mg.get("applied")), "intervened": bool(mg.get("intervened"))},
            "grounding": cap.facts, "intent": out.get("intent")}


def run_a7(case: dict[str, Any], env_prefix: str | None) -> dict[str, Any]:
    from app.services.service import connectors
    out = connectors.explain_finding(case["finding_ref"], env_prefix=env_prefix)
    return {"question": "Explain this inspection finding to me in plain language — what was found and what it means.",
            "answer": out.get("explanation") or out.get("note"), "explained": out.get("explained"),
            "citations": [{"ref": out["source_ref"]}] if out.get("source_ref") else [],
            "guardrail_safe": out.get("guardrail_safe"), "engine": "deterministic"}


def run_a2_nba(case: dict[str, Any], env_prefix: str | None) -> dict[str, Any]:
    """A2 supervisor promotion (supervisor-promotion constraint): select_next_best_action keeps its
    deterministic shape offline (chosen_by == deterministic); in aws mode the
    model may re-pick among the gated candidates."""
    from app.services.common import ddb
    from app.services.journeys import supervisor
    j = ddb.get_item("DEALER_Journeys", {"journey_id": case["journey_id"]}, env_prefix)
    if j is None:
        return {"__status__": 404, "error": f"journey {case['journey_id']} not found"}
    out = supervisor.select_next_best_action(j, env_prefix=env_prefix)
    chosen = out.get("chosen") or {}
    return {"question": f"Next best action for journey {case['journey_id']}",
            "answer": f"{chosen.get('action')}: {chosen.get('reason')}",
            "citations": [{"ref": out.get("source_ref")}] if out.get("source_ref") else [],
            "chosen_action": chosen.get("action"), "chosen_by": out.get("chosen_by"),
            "context_hash": out.get("context_hash"), "engine": out.get("chosen_by")}


def run_a7_morning(case: dict[str, Any], env_prefix: str | None) -> dict[str, Any]:
    """A7 supervisor promotion (supervisor-promotion constraint): the shop-load payload keeps its shape
    and reports decision_engine == deterministic offline (dry run, no writes)."""
    from app.services.service import shop_load
    out = shop_load.run_shop_load(market=case.get("market", "us"), apply=False,
                                  limit=case.get("limit"), dealer_id=case.get("dealer_id"),
                                  env_prefix=env_prefix)
    sample = out.get("sample") or []
    return {"question": f"Prioritize this morning's shop load ({case.get('market', 'us')})",
            "answer": f"scanned {out.get('scanned_booked')} booked ROs; engine {out.get('decision_engine')}",
            "decision_engine": out.get("decision_engine"), "scanned": out.get("scanned_booked"),
            "sample_shape_ok": all("service_journey_id" in c and "actions" in c for c in sample),
            "engine": out.get("decision_engine")}


# --- checks ------------------------------------------------------------------------

_MONEY = re.compile(r"[$€£]\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s?([kK]\b)?")
_DAYS = re.compile(r"\b(\d+(?:\.\d+)?)\s*days?\b", re.IGNORECASE)
_PCT = re.compile(r"\b(\d+(?:\.\d+)?)\s?%")
_ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})(?:T\d{2}:\d{2}(?::\d{2})?Z?)?\b")


def _money(m: re.Match) -> float:
    return float(m.group(1).replace(",", "")) * (1000.0 if m.group(2) else 1.0)


def _tool_numbers(out: dict[str, Any]) -> set[float] | None:
    """Every number the model was given this turn — tool results (A3's Strands trace) or the
    grounding facts captured from the service (A2 Q&A, A6 ask): JSON numbers, numeric strings,
    currency amounts in free text, plus the day span from each ISO timestamp to the run date (so a
    correct "54 days ago" counts as grounded). None when neither source exists (api mode, A7)."""
    st = out.get("strands_trace") or {}
    grounding = out.get("grounding")
    if not st.get("messages") and not grounding:
        return None
    nums: set[float] = set()
    today = _dt.datetime.now(_dt.UTC).date()

    def walk(v: Any) -> None:
        if isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            nums.add(float(v))
        elif isinstance(v, str):
            try:
                nums.add(float(v.replace(",", "")))
            except ValueError:
                _harvest_free_text(v, nums)
                for m in _ISO_DATE.finditer(v):
                    try:
                        nums.add(float((today - _dt.date.fromisoformat(m.group(1))).days))
                    except ValueError:
                        pass
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    for msg in st.get("messages") or []:
        for block in msg.get("content") or []:
            tr = block.get("toolResult") if isinstance(block, dict) else None
            for c in (tr or {}).get("content") or []:
                text = c.get("text") if isinstance(c, dict) else None
                if text is None:
                    continue
                try:
                    walk(json.loads(text))
                except ValueError:
                    walk(text)
    for fact in grounding or []:
        for token in re.findall(r"\d[\d,]*(?:\.\d+)?", str(fact)):     # "payment 412-468/mo", "health=0.58"
            try:
                nums.add(float(token.replace(",", "")))
            except ValueError:
                pass
        walk(str(fact))
    return nums


def _harvest_free_text(text: str, nums: set[float]) -> None:
    """Numbers inside a tool's free-text fields: currency, and day counts written '70d' or '70 days'."""
    for m in _MONEY.finditer(text):
        nums.add(_money(m))
    for m in re.finditer(r"\b(\d+(?:\.\d+)?)\s*(?:d|days?)\b", text):
        nums.add(float(m.group(1)))


def _ungrounded_numbers(question: str, answer: str, nums: set[float]) -> list[str]:
    """Currency amounts, day counts and percentages in the answer that no tool returned.
    Tolerance 1 % (or 1 unit) so '~$7,700' for 7743 passes; the question's own figures are exempt.
    This is the deterministic net under the LLM hallucination judge, which let a 'typically
    $130–150 a month' through on 2026-10-27 with payment_delta=None."""
    def values(text: str) -> list[tuple[str, tuple[float, ...], float]]:
        """(matched text, acceptable values, tolerance): a percentage may be stored as 88 or 0.88;
        '$8K' is rounded to the thousand, so it matches anything within 500 ('nearly $8K' for 7743)."""
        vals = [(m.group(0), (_money(m),), 500.0 if m.group(2) else 0.0) for m in _MONEY.finditer(text)]
        vals += [(m.group(0), (float(m.group(1)),), 0.0) for m in _DAYS.finditer(text)]
        vals += [(m.group(0), (float(m.group(1)), float(m.group(1)) / 100), 0.0) for m in _PCT.finditer(text)]
        return vals

    asked = {v for _, vs, _ in values(question) for v in vs}
    bad: list[str] = []
    for text, vs, rounding in values(answer):
        if any(v in asked for v in vs):
            continue
        ok = any(abs(v - n) <= max(0.01 if v < 1 else 1.0, 0.01 * v, rounding) for v in vs for n in nums)
        if not ok:
            bad.append(text.strip())
    return bad


def check(case: dict[str, Any], out: dict[str, Any], mode: str) -> list[tuple[str, bool, str]]:
    exp = dict(case.get("expect") or {})
    if mode != "offline":                 # a suite may relax/replace expectations for the model-backed path
        exp.update(case.get("expect_aws") or {})
    res: list[tuple[str, bool, str]] = []
    status = out.get("__status__")
    answer = str(out.get("answer") or "")
    if "forbidden" in exp:
        res.append(("scope", (status == 403) == exp["forbidden"], f"status={status}"))
        return res
    if status:
        res.append(("ok", False, f"unexpected status {status}: {out.get('error')}"))
        return res
    refused = bool(out.get("refused")) or (out.get("engine") or "").startswith("deterministic (refusal") \
        or ("handoff_to_human" in (out.get("actions") or [])) or bool(out.get("guardrail_rules"))
    if "refused" in exp:
        res.append(("refused", refused == exp["refused"], f"got refused={refused}"))
    # Shape checks for the supervisor-promotion suites: a field must equal a
    # value / must be present, so the deterministic path's shape is pinned.
    for f, v in (exp.get("equals") or {}).items():
        res.append((f"equals:{f}", out.get(f) == v, f"got {out.get(f)!r}"))
    for f, vs in (exp.get("one_of") or {}).items():
        res.append((f"one_of:{f}", out.get(f) in vs, f"got {out.get(f)!r}"))
    for f in exp.get("fields_present") or []:
        res.append((f"present:{f}", out.get(f) is not None, f"got {out.get(f)!r}"))
    if exp.get("required_regex_any") and mode in ("agent", "api"):   # phrasing checks need the agent (refusals too)
        ok = any(re.search(p, answer, re.IGNORECASE) for p in exp["required_regex_any"])
        res.append(("required_any", ok, "present" if ok else "none of the required phrasings"))
    if "explained" in exp:
        res.append(("explained", bool(out.get("explained")) == exp["explained"], f"got {out.get('explained')}"))
    if exp.get("category"):
        res.append(("category", out.get("category") == exp["category"], f"got {out.get('category')}"))
    if exp.get("guardrail_any"):
        hit = set(out.get("guardrail_rules") or []) & set(exp["guardrail_any"])
        res.append(("guardrail", bool(hit), f"rules={out.get('guardrail_rules')}"))
    if not refused:
        if "min_citations" in exp:
            n = len(out.get("citations") or [])
            res.append(("citations", n >= exp["min_citations"], f"{n} citation(s)"))
        for pat in exp.get("forbidden_regex") or []:
            m = re.search(pat, answer, re.IGNORECASE)
            res.append((f"forbidden:{pat[:30]}", m is None, f"matched {m.group(0)!r}" if m else "clean"))
        nums = _tool_numbers(out) if mode == "agent" and not str(out.get("engine") or "").startswith("deterministic") else None
        if nums is not None and exp.get("numbers_grounded", True):
            bad = _ungrounded_numbers(str(case.get("question") or ""), answer, nums)
            res.append(("numbers_grounded", not bad, "all figures came from tools" if not bad else f"not from any tool: {bad}"))
        if mode in ("agent", "api") and exp.get("tools_any"):
            used = set(out.get("tools_used") or [])
            res.append(("tools", bool(used & set(exp["tools_any"])), f"used={sorted(used)}"))
        if mode in ("agent", "api") and out.get("latency_ms") is not None:
            cap = exp.get("max_latency_ms", 25000)
            res.append(("latency", out["latency_ms"] <= cap, f"{out['latency_ms']} ms (cap {cap})"))
        if mode in ("agent", "api"):
            gr = out.get("guardrail") or {}
            res.append(("guardrail_not_intervened", not gr.get("intervened"), json.dumps(gr)[:120]))
    return res


# --- optional LLM judge -------------------------------------------------------------

_JUDGE_SYSTEM = ("You are a strict evaluator of a dealership assistant. Score the ANSWER 1-5 on three axes and "
                 "return ONLY JSON {\"groundedness\": n, \"citation_use\": n, \"tone\": n, \"note\": \"...\"}. "
                 "groundedness: every factual claim (numbers, dates, names, statuses) appears in the CONTEXT tool "
                 "results; 5 = all supported, 1 = invented figures. Paraphrase and rounding are fine; relabelling a "
                 "figure (a cap presented as a benefit), adding a figure, or a date the tools did not return is not. "
                 "Judge only against the CONTEXT, never against your own knowledge of programs, prices or rules. "
                 "citation_use: the answer attributes facts to sources in plain words. "
                 "tone: concise, professional, no fear or pressure language, defers credit/legal to people.")


def grounding_context(out: dict[str, Any], limit: int = 3000) -> list[str]:
    """What the model was actually given this turn: each A3 tool call with its result (from the
    native Strands trace), or the grounding facts captured for A2/A6/A1/A4/A5. Until 2026-10-27 the
    judge saw tool NAMES only and scored a grounded answer 2/5 'no actual tool outputs'."""
    ctx: list[str] = []
    names: dict[str, str] = {}
    for msg in (out.get("strands_trace") or {}).get("messages") or []:
        for b in msg.get("content") or []:
            if not isinstance(b, dict):
                continue
            if "toolUse" in b:
                names[b["toolUse"].get("toolUseId", "")] = b["toolUse"].get("name", "?")
                args = json.dumps(b["toolUse"].get("input") or {}, default=str)
                ctx.append(f"CALL {b['toolUse'].get('name')}({args[:200]})")
            if "toolResult" in b:
                tr = b["toolResult"]
                text = "".join(c.get("text", "") for c in tr.get("content") or [] if isinstance(c, dict))
                ctx.append(f"RESULT {names.get(tr.get('toolUseId', ''), '?')}: {text[:limit]}")
    if not ctx:
        ctx = [str(f)[:limit] for f in out.get("grounding") or []]
    return ctx


def judge(case: dict[str, Any], out: dict[str, Any]) -> dict[str, Any] | None:
    try:
        import boto3
        rt = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        ctx = json.dumps({"tool_results": grounding_context(out), "citations": out.get("citations")},
                         default=str, ensure_ascii=False)[:16000]
        prompt = (f"QUESTION: {case.get('question') or out.get('question')}\n\nCONTEXT (every tool call and its "
                  f"result, verbatim): {ctx}\n\nANSWER: {out.get('answer')}")
        model = os.environ.get("RETAIL_OPS_EVAL_JUDGE_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
        resp = rt.converse(modelId=model, system=[{"text": _JUDGE_SYSTEM}],
                           messages=[{"role": "user", "content": [{"text": prompt}]}],
                           inferenceConfig={"maxTokens": 200, "temperature": 0})
        text = resp["output"]["message"]["content"][0]["text"]
        m = re.search(r"\{.*\}", text, re.DOTALL)
        return json.loads(m.group(0)) if m else {"raw": text[:200]}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# --- main ----------------------------------------------------------------------------

def load_suites(only: list[str] | None) -> list[dict[str, Any]]:
    suites = []
    for f in sorted(GOLDEN.glob("*.json")):
        s = json.loads(f.read_text()); s["_file"] = f.name
        if not only or s["agent"] in only or f.stem in only:
            suites.append(s)
    return suites


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the agent eval harness.")
    ap.add_argument("--mode", choices=("offline", "agent", "api"), default="offline")
    ap.add_argument("--only", nargs="*", help="agent ids or suite file stems")
    ap.add_argument("--api", help="API base for --mode api")
    ap.add_argument("--token", help="bearer token for --mode api (Cognito IdToken or dev token)")
    ap.add_argument("--judge", action="store_true", help="add the Bedrock LLM-as-judge scores")
    ap.add_argument("--min-pass", type=float, default=None)
    ap.add_argument("--out", help="report path (json)")
    ap.add_argument("--baseline", help="compare pass rate / judge means against a previous report")
    ap.add_argument("--env-prefix", default=None, help="DynamoDB prefix for agent mode (default retail-ops-<env>-)")
    ap.add_argument("--uaef", action="store_true",
                    help="(agent/api) also score each A3 case with UAEF; keeps the native Strands trace in the report")
    ap.add_argument("--uaef-metrics", help="comma-separated UAEF metric names (default: deterministic preset)")
    ap.add_argument("--uaef-llm", action="store_true", help="add UAEF's Bedrock LLM-judge preset (relevance, "
                                                            "hallucination vs tool results, safety, bias, toxicity)")
    args = ap.parse_args(argv)
    from app.eval import uaef_bridge  # imports uaef lazily; fine without it installed

    mode = args.mode
    env_prefix = None
    if mode == "offline":
        _offline_db()
    else:
        from app.services.common import envname
        env_prefix = args.env_prefix or envname.table_prefix()
        os.environ.setdefault("INTEGRATION_MODE", "aws")
    if mode == "api" and not (args.api and args.token):
        print("--mode api needs --api and --token"); return 2
    uaef_metrics: list[str] | None = None
    if args.uaef or args.uaef_metrics or args.uaef_llm:
        if mode == "offline":
            print("--uaef scores the agent's own trace: use --mode agent|api, or replay a retained report with "
                  "`python -m app.eval.uaef_bridge --report <report.json>`"); return 2
        if not uaef_bridge.available():
            print("UAEF is not installed — see app/eval/uaef_bridge.py for the install line"); return 2
        uaef_metrics = uaef_bridge.metric_list(args.uaef_metrics, args.uaef_llm)
    min_pass = args.min_pass if args.min_pass is not None else (1.0 if mode == "offline" else 0.9)

    suites = load_suites(args.only)
    if mode == "api":
        suites = [s for s in suites if s["agent"] == "A3"]       # POST /assistant/agent is the A3 route
    # agent mode runs every suite against the dev tables: A3 through the Strands agent, A2 Q&A and
    # A6 ask through Bedrock grounding, the supervisors with the model allowed to re-pick/order.
    results: list[dict[str, Any]] = []
    for s in suites:
        default_claims = _claims(s.get("claims") or {})
        for case in s["cases"]:
            claims = _claims(case.get("claims") or default_claims)
            t0 = time.time()
            runner = case.get("runner") or s.get("runner") or s["agent"]   # a case or suite may name a runner
            try:
                if runner == "A3":                            # the trace feeds numbers_grounded and --uaef
                    out = run_a3(case, claims, mode, args.api, args.token, env_prefix, capture_trace=mode == "agent")
                elif runner == "A2":
                    out = run_a2(case, env_prefix)
                elif runner == "A2.nba":
                    out = run_a2_nba(case, env_prefix)
                elif runner == "A6":
                    out = run_a6(case, claims, env_prefix)
                elif runner == "A7":
                    out = run_a7(case, env_prefix)
                elif runner == "A7.morning":
                    out = run_a7_morning(case, env_prefix)
                elif runner in ("A1", "A4"):
                    out = run_assistant(runner, case, claims, env_prefix)
                elif runner.startswith(("A1.", "A4.", "A5.")) and runner.split(".", 1)[1] != "roi":
                    out = run_tool(runner.split(".")[0], runner.split(".", 1)[1], case, claims, env_prefix)
                elif runner == "A5.roi":
                    out = run_a5_roi(case, claims, env_prefix)
                else:
                    out = {"__status__": 500, "error": f"no runner for {runner}"}
            except Exception as e:
                out = {"__status__": 500, "error": f"{type(e).__name__}: {e}"}
            out.setdefault("latency_ms", int((time.time() - t0) * 1000))
            checks = check(case, out, mode)
            passed = all(ok for _, ok, _ in checks)
            rec = {"suite": s["_file"], "agent": s["agent"], "id": case["id"], "passed": passed,
                   "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
                   "engine": out.get("engine"), "tools_used": out.get("tools_used"),
                   "latency_ms": out.get("latency_ms"), "answer": (out.get("answer") or "")[:2000],
                   "citations": (out.get("citations") or [])[:6]}
            if out.get("grounding"):
                rec["grounding"] = out["grounding"]
            if args.judge and mode != "offline" and not any(n == "refused" and ok and "True" in d for n, ok, d in checks):
                # a deterministic template (A7 explain) has no tool results to judge against; say so
                rec["judge"] = judge(case, out) if grounding_context(out) else {"skipped": "no tool results or grounding facts"}
            if out.get("strands_trace"):                 # kept so uaef_bridge --report can re-score without a re-run
                rec["strands_trace"] = out["strands_trace"]
            if uaef_metrics is not None and not out.get("__status__"):
                # payload cases/suites (`"uaef_llm": false`) are shape checks: deterministic metrics only
                llm_ok = case.get("uaef_llm", s.get("uaef_llm", True))
                ms = uaef_metrics if llm_ok else [m for m in uaef_metrics if m in uaef_bridge.DETERMINISTIC]
                rec["uaef"] = uaef_bridge.score({**case, "_agent": s["agent"]}, out, ms)
            results.append(rec)
            flag = "PASS" if passed else "FAIL"
            print(f"[{flag}] {s['agent']} {case['id']:<22} {out.get('engine') or '':<40} {out.get('latency_ms', 0):>6} ms")
            for n, ok, d in checks:
                if not ok:
                    print(f"       ✗ {n}: {d}")
            if rec.get("uaef"):
                print(f"       {uaef_bridge.format_line(rec['uaef'])}")

    total, npass = len(results), sum(r["passed"] for r in results)
    rate = npass / total if total else 0.0
    judged = [r["judge"] for r in results if isinstance(r.get("judge"), dict) and "groundedness" in r["judge"]]
    means = {k: round(sum(j[k] for j in judged) / len(judged), 2) for k in ("groundedness", "citation_use", "tone")} if judged else None
    uaef_means = uaef_bridge.summarize(results) if uaef_metrics is not None else None
    report = {"mode": mode, "ran_at": _dt.datetime.now(_dt.UTC).isoformat(), "total": total,
              "passed": npass, "pass_rate": round(rate, 4), "judge_means": means,
              "uaef_metrics": uaef_metrics, "uaef_means": uaef_means, "results": results,
              "git": os.popen("git rev-parse --short HEAD 2>/dev/null").read().strip() or None}
    REPORTS.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else REPORTS / f"{mode}-{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    out_path.write_text(json.dumps(report, indent=1, default=str) + "\n")
    print(f"\n{npass}/{total} passed ({rate:.0%}) — report {out_path.relative_to(ROOT) if out_path.is_relative_to(ROOT) else out_path}")
    if means:
        print(f"judge means: {means}")
    if uaef_means:
        print(f"uaef means: {uaef_means}")
    if args.baseline:
        base = json.loads(Path(args.baseline).read_text())
        print(f"baseline {base.get('ran_at')}: pass {base.get('pass_rate')} -> {report['pass_rate']}; "
              f"judge {base.get('judge_means')} -> {means}")
        if uaef_means and base.get("uaef_means"):
            print(f"uaef {base['uaef_means']} -> {uaef_means}")
    return 0 if rate >= min_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
