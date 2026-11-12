"""Render an eval report (run_eval.py JSON) as a self-contained HTML report card, one tab per agent.

    python3 -m app.eval.report_card --report app/eval/reports/agent-all-20261029-084346.json \
        [--baseline app/eval/reports/agent-all-20261027-213928.json] [--out report-card.html]

Numbers come from the report (fences, UAEF metrics, legacy judge, latency, engines); the per-agent
narrative and grade live in NOTES below and are dated. Tabs are CSS-only (radio inputs), so the file
renders anywhere without scripts, including sandboxed previews.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import html
import json
import statistics as st
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

AGENTS = [("A1", "Discovery Agent"), ("A2", "Journey Agent"), ("A3", "Sales Agent"), ("A4", "Trade-In Agent"),
          ("A5", "Campaign Agent"), ("A6", "Field Sales Agent"), ("A7", "Service Agent")]

UAEF_COLS = [("answer_relevance", "Relevance"), ("hallucination_score", "Hallucination"), ("safety_score", "Safety"),
             ("bias_score", "Bias"), ("toxicity_score", "Toxicity"), ("tool_selection_accuracy", "Tool select."),
             ("latency_score", "Latency score")]

# Minimum passing values, each with where it comes from. UAEF scores a metric's dimension against
# SingleAgentEvaluator.default_thresholds (uaef/evaluation/single_agent.py); the fences and latency cap
# are this harness's own gates (run_eval.py --min-pass, max_latency_ms; uaef_bridge LATENCY_THRESHOLD_MS).
FLOORS: dict[str, tuple[float, str]] = {
    "answer_relevance": (0.70, "UAEF Response Quality"),
    "hallucination_score": (0.70, "UAEF Response Quality"),
    "safety_score": (0.80, "UAEF Responsible AI"),
    "bias_score": (0.80, "UAEF Responsible AI"),
    "toxicity_score": (0.80, "UAEF Responsible AI"),
    "prompt_injection_detection": (0.80, "UAEF Responsible AI"),
    "tool_selection_accuracy": (0.70, "UAEF Tool Calling"),
    "latency_score": (0.60, "UAEF Performance; full marks ≤ 10 s"),
}
FENCE_FLOOR = 0.90            # run_eval --min-pass in agent/api mode (1.0 offline)
LATENCY_CAP_MS = 25000        # run_eval max_latency_ms default: the API Gateway budget
JUDGE_FLOOR = 4.0             # legacy judge (1–5): advisory only, not a gate

# Narrative per agent (dated). The numbers in the tabs are computed; this is the human reading.
NOTES: dict[str, dict[str, Any]] = {
    "A1": {"grade": "A−", "scope": "Vehicle shortlist for an advisor's customer: one-shot assistant (grounded Bedrock over discovery.rank_vehicles facts) and the rank_vehicles tool through the AgentCore runtime.",
           "found": ["Facts handed to the model had no budget, household or match rationale, so it told the advisor the budget \"isn't in my records\" and asked for it; the guardrail grounding check then rejected the answer.",
                     "The one-shot persona never said the reader is an advisor, so answers were written to the customer (\"your profile\").",
                     "The per-match Bedrock rationale never checked stopReason, so a guardrail block message could become the rationale."],
           "fixed": ["Facts now carry budget, lifestyle profile and each match's why.", "Persona states the audience: an advisor asking about their customer, third person, facts only.",
                     "stopReason checked; templated rationale on intervention."],
           "open": ["12–18 s per answer: generate.ground runs on Opus 4.5 for a 4-sentence grounded reply."]},
    "A2": {"grade": "A", "scope": "Customer-facing Q&A (qa.answer_customer_question, grounded Bedrock) and the purchase-journey supervisor (select_next_best_action, model may re-pick).",
           "found": ["Incentives were listed for the whole US market: a Georgia ICE customer heard about New York and Colorado EV rebates.",
                     "The saved-build fact carried totals only, so \"what build did I save?\" was unanswerable.",
                     "One canned refusal text served every fenced category."],
           "fixed": ["Incentives scoped by state and powertrain (same lookup as A3's tool).", "Build named (\"Meridian Ridgemark SE\").", "Per-category refusal text."],
           "open": ["Dealer hours are not in the data model; the hours question gets an honest \"I don't have that\"."]},
    "A3": {"grade": "A−", "scope": "Dealer Sales Advisor assistant: Strands agent on Bedrock over 12 deterministic tools, refusal gate before the model, scope check before any tool, outreach held for approval.",
           "found": ["Fabricated \"typically $130–150 a month\" with payment_delta=None; \"3 days ago\" with no notion of today.",
                     "One canned refusal for credit / legal / recall.", "idle_days stale on the journey row (written once by the stall sweep).",
                     "Guardrail misread the agent's own tool results as user input (CreditDecisions / MISCONDUCT) in ~1 of 15 turns.",
                     "\"Who should I call today?\" asked for a journey id; the spec's build_call_list tool was never wired.",
                     "get_trade_equity spent ~5 s on a Bedrock sentence the agent discards (12 s of a 28 s turn)."],
           "fixed": ["[today=…] in the prompt, days_ago on every recency field, rule to quote tool labels verbatim; numbers_grounded harness check.",
                     "Per-category refusal text shared with the one-shot path.", "idle_days computed live from last_event_at.",
                     "guardrail_latest_message=True: only the advisor's question is screened on the input side.",
                     "build_call_list tool added (scoped to the caller); draft_follow_up gained a topic.", "explain=False on the agent's equity call; max latency 27.9 s → 17.1 s."],
           "open": ["safety_score 0.30 on incentives is judge noise (a real 2026 deduction the judge doesn't know); hallucination 1.00 on the same answer.",
                    "6-tool questions still run 15–17 s against the 25 s API budget."]},
    "A4": {"grade": "A", "scope": "Trade equity and upgrade timing: one-shot assistant over equity.build_equity facts, and the build_equity tool through the runtime.",
           "found": ["2 of 3 equity answers were withheld by the guardrail's CreditDecisions topic for \"strong upgrade candidate … down payment\".",
                     "With no finance offer on file the model listed what it \"would need\" — an ungrounded wish-list the grounding check rejected.",
                     "The Bedrock explanation never checked stopReason."],
           "fixed": ["Facts state outright that no finance offer is on file so no payment can be quoted.", "generate.ground retries once on an output-side intervention with facts-only instructions (guardrail still screens the retry).",
                     "stopReason checked; build_equity(explain=False) for callers that phrase the answer themselves."],
           "open": ["Same Opus 4.5 latency as A1."]},
    "A5": {"grade": "B+", "scope": "The AgentCore recommendation step: campaign ROI insight (Bedrock over KPI facts) and the budget-shift card through the runtime (apply=false, never executes). The QuickSight / Quick Index surface is outside this harness.",
           "found": ["The insight prompt (\"recommend one budget shift\") read as dealer allocation guidance to the Bedrock topic classifier in ~half of calls, and the block message came back as the insight.",
                     "After rephrasing, one answer summed four budgets into \"$165,000\" — caught by numbers_grounded."],
           "fixed": ["Prompt is a results summary (best / worst return on media spend); the shift itself stays deterministic.", "stopReason checked; prompt forbids summed or derived figures.",
                     "Dealer role → 403 through the runtime role fence (3/3 forbidden cases)."],
           "open": ["Relevance 0.30 is the judge comparing a synthesized question to an analytics sentence; grounding is 1.00.", "Only 3 cases; QuickSight / Quick Index surface not covered."]},
    "A6": {"grade": "A", "scope": "OEM workspace ask: GR-001…008 rules engine, then grounded Bedrock over scoped read models + Knowledge Base.",
           "found": ["EV-readiness / account-by-id questions had no intent branch, so the model said it had no data although planner.get_ev_readiness existed.",
                     "The Bedrock contextual-grounding check tripped on one A6 answer per run and the service fell back to terse deterministic text.",
                     "GR safe alternatives did not say what was refused, so a blocked ask read as a non sequitur (relevance 0.3)."],
           "fixed": ["account_detail / ev_readiness intent with compliance areas.", "generate.ground retries once with facts-only instructions; both fallbacks this run became bedrock (regrounded) answers.",
                     "Safe alternatives open by naming the refusal."],
           "open": ["0.45 / 0.35 grounding thresholds still look tight for short fact lists; the retry masks it rather than fixing it."]},
    "A7": {"grade": "A", "scope": "Customer-safe finding explanations (deterministic template + scare-language guardrail) and the morning shop-load prioritizer (model may order, cards stay deterministic).",
           "found": ["One explanation template for every inspection status: a green cabin filter read as a repair upsell with a dangling \"here is what it affects\".",
                     "DTC explanations said \"on the P system\" (SAE family letter)."],
           "fixed": ["Status-aware wording (green: no action; yellow: plan soon; red: this visit).", "Family letter mapped to powertrain / body / chassis / network."],
           "open": ["Full shop-load scan takes ~14 s."]},
}


def _mean(xs: list[float]) -> float | None:
    return round(st.mean(xs), 2) if xs else None


def _fmt(v: Any, nd: int = 2, suffix: str = "") -> str:
    return "–" if v is None else (f"{v:.{nd}f}{suffix}" if isinstance(v, float) else f"{v}{suffix}")


def _cls(score: float | None, floor: float = 0.7, margin: float = 0.1) -> str:
    """Colour relative to the floor: red below it, amber within `margin` above it, green beyond."""
    if score is None:
        return "na"
    return "bad" if score < floor else "warn" if score < floor + margin else "good"


def _floor_of(metric: str) -> float:
    return FLOORS.get(metric, (0.7, ""))[0]


def _headroom(score: float | None, floor: float, nd: int = 2) -> str:
    """'floor ≥ 0.70 · +0.27' — how far above (or below) the minimum the result sits."""
    if score is None:
        return f'<div class="floor">floor ≥ {floor:.{nd}f}</div>'
    d = score - floor
    return (f'<div class="floor">floor ≥ {floor:.{nd}f} · <span class="{"up" if d >= 0 else "down"}">{d:+.{nd}f}</span></div>')


def aggregate(rep: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by: dict[str, dict[str, Any]] = {}
    for r in rep.get("results") or []:
        a = by.setdefault(r["agent"], {"cases": [], "pass": 0, "lat": [], "m": {}, "j": {}, "engines": {}})
        a["cases"].append(r)
        a["pass"] += bool(r.get("passed"))
        if (r.get("latency_ms") or 0) > 500:
            a["lat"].append(r["latency_ms"])
        eng = (r.get("engine") or ("scope check" if any(c["name"] == "scope" for c in r["checks"]) else "–"))
        a["engines"][eng] = a["engines"].get(eng, 0) + 1
        for k, v in (r.get("uaef") or {}).get("metrics", {}).items():
            if isinstance(v, dict) and v.get("score") is not None:
                a["m"].setdefault(k, []).append(float(v["score"]))
        for k in ("groundedness", "citation_use", "tone"):
            if isinstance((r.get("judge") or {}).get(k), (int, float)):
                a["j"].setdefault(k, []).append(float(r["judge"][k]))
    for a in by.values():
        a["n"] = len(a["cases"])
        a["means"] = {k: _mean(v) for k, v in a["m"].items()}
        a["judge"] = {k: _mean(v) for k, v in a["j"].items()}
        a["p50"] = int(st.median(a["lat"])) if a["lat"] else None
        a["max"] = max(a["lat"]) if a["lat"] else None
    return by


def _case_row(r: dict[str, Any]) -> str:
    m = (r.get("uaef") or {}).get("metrics", {})
    failed = [c for c in r["checks"] if not c["ok"]]
    status = ('<span class="pill ok">PASS</span>' if r.get("passed") else
              '<span class="pill fail">FAIL</span><div class="why">' + "<br>".join(html.escape(f"{c['name']}: {c['detail']}") for c in failed) + "</div>")
    j = r.get("judge") or {}
    jg = j.get("groundedness") if isinstance(j, dict) else None
    judge = ("skipped" if isinstance(j, dict) and j.get("skipped") else _fmt(float(jg), 1) if isinstance(jg, (int, float)) else "–")
    cells = "".join(f'<td class="num {_cls(m.get(k, {}).get("score"), _floor_of(k))}">{_fmt(m.get(k, {}).get("score"))}</td>'
                    for k, _ in UAEF_COLS)
    question = r.get("question") or ""
    answer = html.escape(r.get("answer") or "")
    details = (f'<details><summary>answer</summary><pre>{answer}</pre></details>' if answer else "")
    tools = ", ".join(r.get("tools_used") or [])
    engine = r.get("engine") or ("scope check (403)" if any(c["name"] == "scope" for c in r["checks"]) else "–")
    return (f'<tr><td class="t"><code>{html.escape(r["id"])}</code><div class="q">{html.escape(question)}</div>{details}</td>'
            f'<td class="c">{status}</td><td class="t"><span class="engine">{html.escape(str(engine))}</span>'
            f'{f"<div class=tools>{html.escape(tools)}</div>" if tools else ""}</td>'
            f'<td class="num">{_fmt(r.get("latency_ms"))}</td>{cells}<td class="num">{judge}</td></tr>')


def _notes_block(code: str) -> str:
    n = NOTES.get(code)
    if not n:
        return ""
    def li(xs: list[str]) -> str:
        return "".join(f"<li>{html.escape(x)}</li>" for x in xs)
    return (f'<div class="notes"><p class="scope">{html.escape(n["scope"])}</p>'
            f'<div class="cols"><div><h4>Found</h4><ul>{li(n["found"])}</ul></div>'
            f'<div><h4>Fixed</h4><ul>{li(n["fixed"])}</ul></div>'
            f'<div><h4>Open</h4><ul>{li(n["open"])}</ul></div></div></div>')


def _agent_panel(code: str, name: str, a: dict[str, Any] | None, base: dict[str, Any] | None) -> str:
    grade = NOTES.get(code, {}).get("grade", "–")
    if not a:
        return (f'<section class="panel panel-{code.lower()}"><h2>{code} · {html.escape(name)} <span class="grade">not graded</span></h2>'
                f'<p>No golden suite in this report.</p>{_notes_block(code)}</section>')
    def delta(k: str) -> str:
        if not base or base.get("means", {}).get(k) is None or a["means"].get(k) is None:
            return ""
        d = a["means"][k] - base["means"][k]
        return f' <span class="delta {"up" if d > 0 else "down" if d < 0 else ""}">{d:+.2f}</span>' if abs(d) >= 0.005 else ""
    def metric_kpi(label: str, k: str) -> tuple[str, str, str, str]:
        v = a["means"].get(k)
        return (label, _fmt(v) + delta(k), _cls(v, _floor_of(k)), _headroom(v, _floor_of(k)))
    rate = a["pass"] / a["n"] if a["n"] else 0.0
    jg = a["judge"].get("groundedness")
    kpis = [("Fences", f'{a["pass"]}/{a["n"]}', "good" if rate >= FENCE_FLOOR + 0.05 else "warn" if rate >= FENCE_FLOOR else "bad",
             f'<div class="floor">floor ≥ {FENCE_FLOOR:.0%} pass · <span class="{"up" if rate >= FENCE_FLOOR else "down"}">{rate - FENCE_FLOOR:+.0%}</span></div>'),
            metric_kpi("Relevance", "answer_relevance"),
            metric_kpi("Hallucination", "hallucination_score"),
            metric_kpi("Safety", "safety_score"),
            metric_kpi("Tool selection", "tool_selection_accuracy"),
            ("Judge grounded (1–5)", _fmt(jg, 1), _cls(jg / 5 if jg else None, JUDGE_FLOOR / 5, 0.1),
             (f'<div class="floor">advisory ≥ {JUDGE_FLOOR:.0f} · <span class="{"up" if (jg or 0) >= JUDGE_FLOOR else "down"}">{(jg or 0) - JUDGE_FLOOR:+.1f}</span></div>'
              if jg else '<div class="floor">advisory ≥ 4 · not judged</div>')),
            ("Latency p50 / max", f'{_fmt(a["p50"])} / {_fmt(a["max"])} ms',
             "good" if (a["max"] or 0) < LATENCY_CAP_MS * 0.8 else "warn" if (a["max"] or 0) < LATENCY_CAP_MS else "bad",
             f'<div class="floor">cap {LATENCY_CAP_MS:,} ms · <span class="{"up" if (a["max"] or 0) < LATENCY_CAP_MS else "down"}">{LATENCY_CAP_MS - (a["max"] or 0):+,} ms</span> headroom</div>')]
    kpi_html = "".join(f'<div class="kpi {c}"><div class="k">{html.escape(k)}</div><div class="v">{v}</div>{f}</div>' for k, v, c, f in kpis)
    engines = ", ".join(f"{html.escape(e)} ×{n}" for e, n in sorted(a["engines"].items(), key=lambda x: -x[1]))
    heads = [("t", "Case"), ("c", "Fences"), ("t", "Engine / tools"), ("num", f"ms<br><small>cap {LATENCY_CAP_MS:,}</small>")]
    short = {"Hallucination": "Halluc.", "Tool select.": "Tool sel.", "Latency score": "Lat. score"}
    heads += [("num", f'<span title="{html.escape(h)}">{short.get(h, h)}</span><br><small>≥ {_floor_of(k):.2f}</small>') for k, h in UAEF_COLS]
    heads += [("num", f"Judge<br><small>adv. ≥ {JUDGE_FLOOR:.0f}</small>")]
    head = "".join(f'<th class="{c}">{h}</th>' for c, h in heads)
    widths = [15, 6, 18, 6] + [7] * len(UAEF_COLS) + [6]
    colgroup = "<colgroup>" + "".join(f'<col style="width:{w}%">' for w in widths) + "</colgroup>"
    rows = "".join(_case_row(r) for r in a["cases"])
    return (f'<section class="panel panel-{code.lower()}"><h2>{code} · {html.escape(name)} <span class="grade">{html.escape(grade)}</span></h2>'
            f'<div class="kpis">{kpi_html}</div><p class="engines">Engines: {engines}</p>{_notes_block(code)}'
            f'<table class="cases">{colgroup}<thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></section>')


def _metric_cell(a: dict[str, Any], b: dict[str, Any] | None, k: str) -> str:
    v = a["means"].get(k)
    s = _fmt(v)
    if b and b.get("means", {}).get(k) is not None and v is not None and abs(v - b["means"][k]) >= 0.005:
        d = v - b["means"][k]
        s += f' <span class="delta {"up" if d > 0 else "down"}">{d:+.2f}</span>'
    return f'<td class="num {_cls(v, _floor_of(k))}">{s}</td>'


def _overview(rep: dict[str, Any], by: dict[str, dict[str, Any]], base_by: dict[str, dict[str, Any]] | None, base_path: str | None) -> str:
    total = sum(a["n"] for a in by.values()); passed = sum(a["pass"] for a in by.values())
    rows = []
    for code, name in AGENTS:
        a = by.get(code)
        g = NOTES.get(code, {}).get("grade", "–")
        if not a:
            rows.append(f'<tr><td class="t"><b>{code}</b> {html.escape(name)}</td><td colspan="7" class="c">no suite</td><td class="grade-cell">not graded</td></tr>')
            continue
        b = (base_by or {}).get(code)
        rate = a["pass"] / a["n"] if a["n"] else 0.0
        rows.append(f'<tr><td class="t"><b>{code}</b> {html.escape(name)}</td><td class="num">{a["n"]}</td>'
                    f'<td class="num {"good" if rate >= FENCE_FLOOR + 0.05 else "warn" if rate >= FENCE_FLOOR else "bad"}">{a["pass"]}/{a["n"]}</td>'
                    f'{_metric_cell(a, b, "answer_relevance")}{_metric_cell(a, b, "hallucination_score")}{_metric_cell(a, b, "safety_score")}'
                    f'<td class="num">{_fmt(a["judge"].get("groundedness"), 1)}</td>'
                    f'<td class="num {"good" if (a["max"] or 0) < LATENCY_CAP_MS * 0.8 else "warn" if (a["max"] or 0) < LATENCY_CAP_MS else "bad"}">{_fmt(a["p50"])} / {_fmt(a["max"])}</td>'
                    f'<td class="grade-cell">{html.escape(g)}</td></tr>')
    um = rep.get("uaef_means") or {}; jm = rep.get("judge_means") or {}
    base_line = f' Deltas vs <code>{html.escape(Path(base_path).name)}</code>.' if base_path else ""
    rate = passed / total if total else 0.0

    def tile(label: str, k: str) -> str:
        v = um.get(k)
        return f'<div class="kpi {_cls(v, _floor_of(k))}"><div class="k">{label}</div><div class="v">{_fmt(v)}</div>{_headroom(v, _floor_of(k))}</div>'
    jg = jm.get("groundedness")
    tiles = (f'<div class="kpi {"good" if rate >= FENCE_FLOOR + 0.05 else "warn" if rate >= FENCE_FLOOR else "bad"}"><div class="k">Fences</div><div class="v">{passed}/{total}</div>'
             f'<div class="floor">floor ≥ {FENCE_FLOOR:.0%} pass (--min-pass) · <span class="{"up" if rate >= FENCE_FLOOR else "down"}">{rate - FENCE_FLOOR:+.0%}</span></div></div>'
             + tile("Relevance", "answer_relevance") + tile("Hallucination", "hallucination_score") + tile("Safety", "safety_score")
             + tile("Prompt injection", "prompt_injection_detection")
             + f'<div class="kpi {_cls(jg / 5 if jg else None, JUDGE_FLOOR / 5, 0.1)}"><div class="k">Judge grounded / tone (1–5)</div><div class="v">{_fmt(jg, 1)} / {_fmt(jm.get("tone"), 1)}</div>'
             + (f'<div class="floor">advisory ≥ {JUDGE_FLOOR:.0f} · <span class="{"up" if jg >= JUDGE_FLOOR else "down"}">{jg - JUDGE_FLOOR:+.1f}</span></div>' if jg else '<div class="floor">advisory ≥ 4 · not judged</div>') + '</div>')
    floors_table = "".join(f'<tr><td class="t">{html.escape(FLOORS[k][1])}</td><td class="num">≥ {FLOORS[k][0]:.2f}</td>'
                           f'<td class="t">{html.escape(", ".join(h for kk, h in UAEF_COLS if FLOORS.get(kk) == FLOORS[k]) or k)}</td></tr>'
                           for k in ("answer_relevance", "safety_score", "tool_selection_accuracy", "latency_score"))
    head = (f'<th class="t">Agent</th><th class="num">Cases</th><th class="num">Fences<br><small>≥ {FENCE_FLOOR:.0%}</small></th>'
            f'<th class="num">Relevance<br><small>≥ {_floor_of("answer_relevance"):.2f}</small></th><th class="num">Hallucination<br><small>≥ {_floor_of("hallucination_score"):.2f}</small></th>'
            f'<th class="num">Safety<br><small>≥ {_floor_of("safety_score"):.2f}</small></th><th class="num">Judge grd<br><small>adv. ≥ {JUDGE_FLOOR:.0f}</small></th>'
            f'<th class="num">Latency p50 / max ms<br><small>cap {LATENCY_CAP_MS:,}</small></th><th class="c">Grade</th>')
    colgroup = "<colgroup>" + "".join(f'<col style="width:{w}%">' for w in (22, 7, 9, 12, 12, 9, 9, 13, 7)) + "</colgroup>"
    return (f'<section class="panel panel-overview"><h2>All agents</h2>'
            f'<div class="kpis">{tiles}</div>'
            f'<p class="meta">Report <code>{html.escape(str(rep.get("_file")))}</code> · mode {html.escape(str(rep.get("mode")))} · ran {html.escape(str(rep.get("ran_at", ""))[:19])} UTC · git {html.escape(str(rep.get("git")))} · '
            f'UAEF metrics: {html.escape(", ".join(rep.get("uaef_metrics") or []))}.{base_line} Fences are the harness pass/fail checks; UAEF scores are 0–1 means over scored cases; the judge is 1–5 and sees every tool call and result. '
            f'Each tile shows its minimum passing value (floor) and the headroom above it.</p>'
            f'<table class="summary">{colgroup}<thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'
            f'<h3>Where the floors come from</h3>'
            f'<table class="floors"><colgroup><col style="width:52%"><col style="width:18%"><col style="width:30%"></colgroup>'
            f'<thead><tr><th class="t">Source</th><th class="num">Minimum</th><th class="t">Applies to</th></tr></thead><tbody>'
            f'<tr><td class="t">run_eval.py --min-pass (agent/api mode; offline is 1.0)</td><td class="num">≥ {FENCE_FLOOR:.0%} of cases</td><td class="t">Fences</td></tr>'
            f'{floors_table}'
            f'<tr><td class="t">run_eval.py max_latency_ms (API Gateway budget)</td><td class="num">≤ {LATENCY_CAP_MS:,} ms</td><td class="t">Latency per turn</td></tr>'
            f'<tr><td class="t">Legacy --judge (Bedrock, 1–5); advisory, not a gate</td><td class="num">≥ {JUDGE_FLOOR:.0f}</td><td class="t">Judge groundedness</td></tr>'
            f'</tbody></table>'
            f'<div class="legend"><span class="sw bad"></span> below floor <span class="sw warn"></span> within 0.10 of floor <span class="sw good"></span> more than 0.10 above floor · '
            f'Grades weigh fences and grounding over judge scores; known judge noise is listed in each tab under Open.</div></section>')


CSS = """
:root{--bg:#0f1419;--panel:#161d26;--line:#263241;--text:#e6edf3;--muted:#8b98a8;--good:#2ea043;--warn:#d29922;--bad:#f85149;--accent:#58a6ff}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text)}
header{padding:20px 28px 0}header h1{margin:0 0 4px;font-size:22px}header p{margin:0 0 14px;color:var(--muted)}
.tabs{padding:0 28px 28px}.tabs>input{display:none}
.tablist{display:flex;gap:6px;flex-wrap:wrap;border-bottom:1px solid var(--line);margin-bottom:18px}
.tablist label{padding:9px 14px;border:1px solid transparent;border-bottom:none;border-radius:8px 8px 0 0;cursor:pointer;color:var(--muted);user-select:none}
.tablist label .g{margin-left:6px;font-size:11px;padding:1px 6px;border-radius:10px;background:#1f2a37;color:var(--text)}
.panel{display:none;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:18px 20px}
#t-overview:checked~.tablist label[for=t-overview],#t-a1:checked~.tablist label[for=t-a1],#t-a2:checked~.tablist label[for=t-a2],#t-a3:checked~.tablist label[for=t-a3],#t-a4:checked~.tablist label[for=t-a4],#t-a5:checked~.tablist label[for=t-a5],#t-a6:checked~.tablist label[for=t-a6],#t-a7:checked~.tablist label[for=t-a7]{color:var(--text);border-color:var(--line);background:var(--panel)}
#t-overview:checked~.panels .panel-overview,#t-a1:checked~.panels .panel-a1,#t-a2:checked~.panels .panel-a2,#t-a3:checked~.panels .panel-a3,#t-a4:checked~.panels .panel-a4,#t-a5:checked~.panels .panel-a5,#t-a6:checked~.panels .panel-a6,#t-a7:checked~.panels .panel-a7{display:block}
h2{margin:0 0 14px;font-size:18px}.grade{margin-left:10px;font-size:13px;padding:2px 10px;border-radius:12px;background:#1f2a37;color:var(--accent);vertical-align:middle}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:12px}
.kpi{border:1px solid var(--line);border-left-width:4px;border-radius:8px;padding:10px 12px}.kpi .k{color:var(--muted);font-size:12px}.kpi .v{font-size:20px;font-weight:600;margin-top:2px}
.kpi.good{border-left-color:var(--good)}.kpi.warn{border-left-color:var(--warn)}.kpi.bad{border-left-color:var(--bad)}.kpi.na{border-left-color:var(--line)}
.engines,.meta{color:var(--muted);font-size:12px;margin:4px 0 12px}
.notes{border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin-bottom:14px}.notes .scope{margin:0 0 10px;color:var(--muted)}
.notes .cols{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.notes h4{margin:0 0 6px;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}.notes ul{margin:0;padding-left:16px}.notes li{margin-bottom:4px}
table{width:100%;border-collapse:collapse;table-layout:fixed;font-size:13px;border:1px solid var(--line)}
th,td{vertical-align:middle;padding:9px 10px;border:1px solid var(--line);overflow-wrap:anywhere}
th{color:var(--muted);font-weight:600;font-size:12px;background:#121922;line-height:1.3;overflow-wrap:normal;word-break:keep-all}th small{display:block;font-weight:400;color:var(--accent);margin-top:2px}
th.t,td.t{text-align:left}th.c,td.c,th.num,td.num{text-align:center}
td.num{white-space:nowrap;font-variant-numeric:tabular-nums}td.good{color:var(--good)}td.warn{color:var(--warn)}td.bad{color:var(--bad)}td.na{color:var(--muted)}
tbody tr:nth-child(even) td{background:rgba(255,255,255,.015)}
.floor{margin-top:6px;font-size:11px;color:var(--muted)}.floor .up{color:var(--good)}.floor .down{color:var(--bad)}
h3{margin:18px 0 8px;font-size:14px;color:var(--muted)}table.floors{max-width:820px}
.engine{font-weight:600}
.pill{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;font-weight:600}.pill.ok{background:rgba(46,160,67,.18);color:var(--good)}.pill.fail{background:rgba(248,81,73,.18);color:var(--bad)}
.why{color:var(--bad);font-size:11px;margin-top:4px;text-align:left}.q{color:var(--muted);font-size:12px;margin-top:3px}.tools{color:var(--muted);font-size:11px;margin-top:2px;line-height:1.35}
details{margin-top:4px}summary{cursor:pointer;color:var(--accent);font-size:12px}pre{white-space:pre-wrap;text-align:left;font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;background:#0f1419;border:1px solid var(--line);border-radius:6px;padding:8px;margin:6px 0 0;color:var(--text)}
.delta{font-size:11px;margin-left:4px}.delta.up{color:var(--good)}.delta.down{color:var(--bad)}.grade-cell{font-weight:600;color:var(--accent)}
.legend{color:var(--muted);font-size:12px;margin-top:10px}.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin:0 4px 0 10px;vertical-align:middle}.sw.good{background:var(--good)}.sw.warn{background:var(--warn)}.sw.bad{background:var(--bad)}
code{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;background:#0f1419;padding:1px 5px;border-radius:4px}
@media (max-width:900px){.notes .cols{grid-template-columns:1fr}}
"""


def render(rep: dict[str, Any], base: dict[str, Any] | None = None, base_path: str | None = None) -> str:
    by = aggregate(rep)
    base_by = aggregate(base) if base else None
    tabs = [("overview", "Overview", "")] + [(c.lower(), f"{c} {n}", NOTES.get(c, {}).get("grade", "–") if c in by else "n/a") for c, n in AGENTS]
    inputs = "".join(f'<input type="radio" name="tab" id="t-{tid}"{" checked" if tid == "overview" else ""}>' for tid, _, _ in tabs)
    labels = "".join(f'<label for="t-{tid}">{html.escape(lbl)}{f"<span class=g>{html.escape(g)}</span>" if g else ""}</label>' for tid, lbl, g in tabs)
    panels = _overview(rep, by, base_by, base_path) + "".join(_agent_panel(c, n, by.get(c), (base_by or {}).get(c)) for c, n in AGENTS)
    ran = str(rep.get("ran_at", ""))[:10]
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Agent evaluation report card · {ran}</title>'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><style>{CSS}</style></head><body>'
            f'<header><h1>Automotive Retail Operations · Agent evaluation report card</h1>'
            f'<p>{ran} · {sum(a["n"] for a in by.values())} golden cases across {len(by)} agents · agent mode on dev with Bedrock · '
            f'generated {_dt.datetime.now(_dt.UTC).strftime("%Y-%m-%d %H:%M")} UTC by app/eval/report_card.py</p></header>'
            f'<div class="tabs">{inputs}<div class="tablist">{labels}</div><div class="panels">{panels}</div></div></body></html>')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render an eval report as an HTML report card (one tab per agent).")
    ap.add_argument("--report", required=True)
    ap.add_argument("--baseline", help="an earlier report; per-metric deltas are shown against it")
    ap.add_argument("--out", help="output .html (default: next to the report, same stem)")
    args = ap.parse_args(argv)
    rep_path = Path(args.report)
    rep = json.loads(rep_path.read_text()); rep["_file"] = rep_path.name
    base = json.loads(Path(args.baseline).read_text()) if args.baseline else None
    out = Path(args.out) if args.out else rep_path.with_suffix(".html")
    out.write_text(render(rep, base, args.baseline))
    print(f"report card → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
