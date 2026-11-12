"""UAEF bridge — graded metrics for the eval harness via the Universal Agent Evaluation Framework.

Why: run_eval.py's checks are pass/fail fences, and its --judge scores three 1–5 axes from a
CONTEXT that holds tool *names* only, so a grounded answer can be scored as fabricated (the
2026-10-08 baseline's a3-next-step: groundedness 2, "no actual tool outputs"). UAEF
(https://github.com/awslabs/uaef) adds framework-standard, graded metrics over the *native
Strands trace* — tool results included — so hallucination is judged against what the tools
actually returned, and tool selection against a per-case expected tool set.

Install (opt-in; ~550 MB of judge/integration dependencies, deliberately not in pyproject):
    git clone https://github.com/awslabs/uaef.git ../uaef && pip install -e ../uaef

Metric presets (pass names with --uaef-metrics to pick your own)
  DETERMINISTIC  no AWS call: tool_selection_accuracy, latency_score, prompt_injection_detection
  LLM            Bedrock judge (UAEF_LLM_MODEL_ID, default us.anthropic.claude-sonnet-4-6):
                 answer_relevance, hallucination_score, safety_score, bias_score, toxicity_score
  Token usage is reported raw (`tokens`); UAEF's token_efficiency needs a quality_score in the
  trace metadata, so it is not in a preset.

Ground truth comes from each golden case:
  expected_tools     the tools the agent SHOULD call for this question (ordered). Scored by
                     Jaccard (tool_selection_accuracy); `tools_any` stays the pass/fail gate.
                     Refusal cases have none, so tool metrics report no score for them.
  (tool_sequence_correctness is LCS order-sensitive; opt in once a case's order is pinned.)

Trace mapping
  agent mode   out["strands_trace"] (a3_agent.run(capture_trace=True)) -> StrandsAdapter:
               every toolUse/toolResult, token usage, cycles. Tool results become the
               hallucination judge's context.
  api / replay no native trace: a two-message AgentTrace (question, answer) with tool calls
               from tool_log / tools_used and the citations as context.

Replay (no agent run, no Bedrock unless you add LLM metrics): score a retained report
    python -m app.eval.uaef_bridge --report app/eval/reports/agent-baseline.json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("UAEF_LOG_LEVEL", "WARNING")       # UAEF logs INFO per metric otherwise

DETERMINISTIC = ["tool_selection_accuracy", "latency_score", "prompt_injection_detection"]
LLM = ["answer_relevance", "hallucination_score", "safety_score", "bias_score", "toxicity_score"]
# The A3 agent is a 3–5 tool loop; the API Gateway budget is 25 s and the baseline ran 5.7–18 s.
# UAEF's default 2 s threshold would score every turn near 0, so the harness scores against the
# same budget the `latency` check uses (full score under 10 s, linear decay to the cap).
LATENCY_THRESHOLD_MS = float(os.environ.get("RETAIL_OPS_EVAL_LATENCY_TARGET_MS", "10000"))
MAX_CONTEXT_CHARS = 4000                                   # per tool result, keeps the judge prompt bounded


def available() -> bool:
    try:
        import uaef  # noqa: F401
        return True
    except ImportError:
        return False


def _require():
    if not available():
        raise RuntimeError("UAEF is not installed: git clone https://github.com/awslabs/uaef.git ../uaef "
                           "&& pip install -e ../uaef   (see app/eval/uaef_bridge.py)")


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.UTC)


def _text(v: Any, limit: int = MAX_CONTEXT_CHARS) -> str:
    s = v if isinstance(v, str) else json.dumps(v, default=str, ensure_ascii=False)
    return s if len(s) <= limit else s[:limit] + " …"


# --- trace ----------------------------------------------------------------------------

def _question(case: dict[str, Any], out: dict[str, Any]) -> str:
    return str(case.get("question") or out.get("question") or case.get("id") or "")


def _meta(case: dict[str, Any], out: dict[str, Any]) -> dict[str, Any]:
    return {"agent": case.get("_agent", "A3"), "case_id": case.get("id"), "journey_id": case.get("journey_id"),
            "engine": out.get("engine"), "model": out.get("model"), "guardrail": out.get("guardrail"),
            "citations": out.get("citations") or [], "tools_used": out.get("tools_used") or [],
            "cycles": out.get("cycles"), "stop_reason": out.get("stop_reason")}


def to_trace(case: dict[str, Any], out: dict[str, Any]):
    """Canonical UAEF AgentTrace for one harness result (native Strands trace when present)."""
    _require()
    from uaef.models import AgentTrace, Message, MessageRole, ToolCall
    latency_s = out["latency_ms"] / 1000.0 if out.get("latency_ms") is not None else None
    native = out.get("strands_trace") or {}
    if native.get("messages"):
        from uaef.adapters import StrandsAdapter
        trace = StrandsAdapter().transform_to_canonical({**native, "session_id": case.get("id")})
        trace.session_id = case.get("id")
        if trace.latency is None:
            trace.latency = latency_s
        trace.metadata.update(_meta(case, out))
        return trace
    # No native trace (api mode, deterministic fallback, replay of an older report): rebuild the
    # turn from what the harness kept. Tool calls carry arguments when tool_log survived.
    ts = _now()
    calls = []
    for entry in out.get("tool_log") or []:
        if isinstance(entry, dict) and entry.get("tool"):
            calls.append(ToolCall(name=entry["tool"], arguments={k: v for k, v in entry.items() if k != "tool"},
                                  timestamp=ts))
    if not calls:
        calls = [ToolCall(name=t, arguments={}, timestamp=ts) for t in out.get("tools_used") or []
                 if t != "assistant_chat"]
    msgs = [Message(role=MessageRole.USER, content=_question(case, out), timestamp=ts),
            Message(role=MessageRole.ASSISTANT, content=str(out.get("answer") or ""), tool_calls=calls, timestamp=ts)]
    engine = str(out.get("engine") or "")
    framework = "strands" if engine.startswith("strands") else "bedrock" if engine.startswith("bedrock") else "deterministic"
    return AgentTrace(session_id=case.get("id"), messages=msgs, tool_calls=calls, latency=latency_s,
                      metadata=_meta(case, out), framework=framework, timestamp=ts)


def to_ground_truth(case: dict[str, Any]):
    """GroundTruth from the case's `expected_tools` (ordered); None when the case declares none."""
    _require()
    from uaef.models import GroundTruth, ToolCall
    expected = case.get("expected_tools")
    if expected is None:
        return None
    ts = _now()
    jid = case.get("journey_id")
    calls = [ToolCall(name=t, arguments={"journey_id": jid} if jid else {}, timestamp=ts) for t in expected]
    return GroundTruth(expected_tool_calls=calls, expected_output=case.get("reference_answer"))


def context_for(out: dict[str, Any], trace) -> list[str]:
    """What the hallucination judge may treat as ground: the tool results (A3) or the grounding facts
    the service handed the model (A2 Q&A, A6 ask). Citation *refs* are not content, so a turn with
    neither gets no context and the judge reports n/a rather than a false low score."""
    ctx: list[str] = []
    for tc in trace.tool_calls:
        if tc.result not in (None, ""):
            ctx.append(f"{tc.name}({_text(tc.arguments, 300)}) -> {_text(tc.result)}")
    if not ctx:
        ctx = [_text(f) for f in out.get("grounding") or [] if str(f).strip()]
    return ctx


# --- scoring --------------------------------------------------------------------------

_pinned = False


def _pin_metrics() -> None:
    """Swap in harness-tuned metric classes. UAEF's registry holds classes and instantiates them
    with no arguments, so a threshold change is a same-name subclass (idempotent, process-global)."""
    global _pinned
    if _pinned:
        return
    from uaef.metrics.performance import LatencyScoreMetric
    from uaef.metrics.registry import get_registry

    class _A3LatencyScore(LatencyScoreMetric):
        def __init__(self, threshold_ms: float = LATENCY_THRESHOLD_MS):
            super().__init__(threshold_ms=threshold_ms)

    reg = get_registry()
    reg.unregister("latency_score")
    reg.register(_A3LatencyScore)
    _pinned = True


def score(case: dict[str, Any], out: dict[str, Any], metrics: list[str]) -> dict[str, Any]:
    """Run UAEF over one harness result; compact per-metric scores plus the overall score."""
    _require()
    from uaef.api import evaluate
    _pin_metrics()
    trace = to_trace(case, out)
    gt = to_ground_truth(case)
    ctx = context_for(out, trace)
    if gt is None:                          # no expected_tools: skip the ground-truth metrics quietly
        from uaef.metrics.registry import get_registry
        needs_gt = set(get_registry().get_metrics_requiring_ground_truth())
        metrics = [m for m in metrics if m not in needs_gt]
    try:
        res = evaluate(trace=trace, ground_truth=gt, metrics=metrics, context=ctx)
    except Exception as e:  # noqa: BLE001 — a judge/adapter failure is reported per case, never aborts the run
        return {"error": f"{type(e).__name__}: {str(e)[:300]}", "metrics": {}}
    scores: dict[str, Any] = {}
    for dim in res.dimension_results:
        for ms in dim.metric_scores:
            scores[ms.metric_name] = {"score": None if ms.score is None else round(float(ms.score), 4),
                                      "reasoning": (ms.reasoning or "")[:400]}
    return {"overall_score": round(float(res.overall_score), 4), "passed": bool(res.passed),
            "failures": list(res.failures or [])[:6], "context_items": len(ctx),
            "tool_calls": len(trace.tool_calls), "tokens": [trace.input_tokens, trace.output_tokens],
            "metrics": scores}


def summarize(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Mean of every scored metric over the records that carry a `uaef` block (None scores skipped)."""
    acc: dict[str, list[float]] = {}
    n = 0
    for r in records:
        u = r.get("uaef")
        if not isinstance(u, dict) or not u.get("metrics"):
            continue
        n += 1
        for name, ms in u["metrics"].items():
            if isinstance(ms, dict) and ms.get("score") is not None:
                acc.setdefault(name, []).append(float(ms["score"]))
    if not n:
        return None
    return {"cases": n, **{k: round(sum(v) / len(v), 3) for k, v in sorted(acc.items())},
            **{f"{k}__n": len(v) for k, v in sorted(acc.items())}}


def metric_list(spec: str | None, llm: bool) -> list[str]:
    """Resolve CLI flags into a metric list (comma-separated override wins)."""
    if spec:
        return [m.strip() for m in spec.split(",") if m.strip()]
    return DETERMINISTIC + (LLM if llm else [])


def format_line(u: dict[str, Any]) -> str:
    if u.get("error"):
        return f"uaef: ERROR {u['error']}"
    parts = [f"{k}={v['score']:.2f}" for k, v in u.get("metrics", {}).items() if v.get("score") is not None]
    skipped = [k for k, v in u.get("metrics", {}).items() if v.get("score") is None]
    return f"uaef: {' '.join(parts) or 'no scored metrics'}" + (f"  (n/a: {', '.join(skipped)})" if skipped else "")


# --- replay ---------------------------------------------------------------------------

def _case_index() -> dict[str, dict[str, Any]]:
    idx: dict[str, dict[str, Any]] = {}
    for f in sorted((Path(__file__).resolve().parent / "golden").glob("*.json")):
        for c in json.loads(f.read_text()).get("cases") or []:
            idx[c["id"]] = c
    return idx


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Score a retained eval report with UAEF (no agent run).")
    ap.add_argument("--report", required=True, help="a report written by run_eval.py (agent or api mode)")
    ap.add_argument("--metrics", help="comma-separated UAEF metric names (default: deterministic preset)")
    ap.add_argument("--llm", action="store_true", help="add the Bedrock LLM-judge preset")
    ap.add_argument("--only", nargs="*", help="case ids")
    ap.add_argument("--out", help="write the report back with `uaef` blocks (default: alongside, -uaef suffix)")
    args = ap.parse_args(argv)
    _require()
    metrics = metric_list(args.metrics, args.llm)
    rep_path = Path(args.report)
    report = json.loads(rep_path.read_text())
    cases = _case_index()
    for rec in report.get("results") or []:
        if rec.get("agent") != "A3" or (args.only and rec["id"] not in args.only):
            continue
        case = cases.get(rec["id"]) or {"id": rec["id"]}
        case = {**case, "question": case.get("question") or rec.get("question")}
        if (case.get("expect") or {}).get("forbidden"):
            continue                                   # a 403 before any tool ran; nothing to score
        rec["uaef"] = score(case, rec, metrics)
        print(f"{rec['id']:<22} {format_line(rec['uaef'])}")
    report["uaef_means"] = summarize(report.get("results") or [])
    report["uaef_metrics"] = metrics
    out_path = Path(args.out) if args.out else rep_path.with_name(rep_path.stem + "-uaef.json")
    out_path.write_text(json.dumps(report, indent=1, default=str) + "\n")
    print(f"\nuaef means: {report['uaef_means']}\nreport {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
