"""Render an eval report (run_eval.py / uaef_bridge.py JSON) as one self-contained HTML page.

    python3 -m app.eval.html_report app/eval/reports/agent-uaef-20261027-150234.json
    python3 -m app.eval.html_report <report.json> --out <report.html>

The page has no script and no external asset, so it opens from disk and can be
attached to a review as-is. It shows the harness gate (pass / fail per case and
check), the UAEF scores when the report carries `uaef` blocks (metric means,
per-case heat map, the cases UAEF marked failed and why), and, per case, the
question, the answer, the citations, the tools called against the golden
`expected_tools`, and the Strands tool-execution stats kept in `strands_trace`.
Stdlib only.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import html
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any

try:
    from app.eval.uaef_bridge import DETERMINISTIC, LATENCY_THRESHOLD_MS, LLM
except ImportError:  # run as a script from app/eval/
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.eval.uaef_bridge import DETERMINISTIC, LATENCY_THRESHOLD_MS, LLM

GOLDEN = Path(__file__).resolve().parent / "golden"
UAEF_PASS = 0.70          # UAEF's per-category pass threshold (the `failures` strings quote it)
GOOD, OK = 0.90, UAEF_PASS

METRIC_LABEL = {
    "tool_selection_accuracy": "Tool selection", "latency_score": "Latency",
    "prompt_injection_detection": "Prompt injection", "answer_relevance": "Relevance",
    "hallucination_score": "Hallucination", "safety_score": "Safety", "bias_score": "Bias",
    "toxicity_score": "Toxicity", "tool_sequence_correctness": "Tool sequence",
}

CSS = """
:root{--ink:#1f2328;--muted:#656d76;--line:#d0d7de;--bg:#f6f8fa;--good:#1a7f37;--good-bg:#dafbe1;
--ok:#9a6700;--ok-bg:#fff8c5;--bad:#cf222e;--bad-bg:#ffebe9;--na:#8c959f;--na-bg:#eaeef2}
*{box-sizing:border-box}body{margin:0;padding:32px 40px;font:14px/1.5 -apple-system,BlinkMacSystemFont,
"Segoe UI",Helvetica,Arial,sans-serif;color:var(--ink);max-width:1320px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:36px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--line)}
h3{font-size:15px;margin:0 0 6px}.sub{color:var(--muted);margin:0 0 20px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.card{border:1px solid var(--line);border-radius:8px;padding:12px 14px;background:#fff}
.card .k{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.03em}
.card .v{font-size:24px;font-weight:600;margin-top:2px}.card .n{color:var(--muted);font-size:12px}
table{border-collapse:collapse;width:100%;background:#fff}th,td{border:1px solid var(--line);padding:6px 8px;
text-align:left;vertical-align:top}th{background:var(--bg);font-weight:600;white-space:nowrap}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600;white-space:nowrap}
.good{color:var(--good);background:var(--good-bg)}.ok{color:var(--ok);background:var(--ok-bg)}
.bad{color:var(--bad);background:var(--bad-bg)}.na{color:var(--na);background:var(--na-bg)}
td.score{text-align:center;font-variant-numeric:tabular-nums;font-weight:600;width:64px}
.bar{position:relative;height:8px;background:var(--na-bg);border-radius:4px;min-width:140px}
.bar i{position:absolute;left:0;top:0;bottom:0;border-radius:4px;background:var(--good)}
.bar.ok i{background:var(--ok)}.bar.bad i{background:var(--bad)}
code{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--bg);padding:1px 4px;border-radius:4px}
.mono{font:12px ui-monospace,SFMono-Regular,Menlo,monospace}
details{border:1px solid var(--line);border-radius:8px;margin:10px 0;background:#fff}
summary{cursor:pointer;padding:10px 14px;font-weight:600;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
summary .q{font-weight:400;color:var(--muted);flex:1;min-width:240px}
.body{padding:4px 14px 14px;border-top:1px solid var(--line)}
.answer{white-space:pre-wrap;background:var(--bg);border-radius:6px;padding:10px 12px;margin:6px 0 14px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:900px){.grid2{grid-template-columns:1fr}}
.miss{color:var(--bad)}.extra{color:var(--ok)}.small{font-size:12px;color:var(--muted)}
.reason{color:var(--muted);font-size:12px}ul.tight{margin:4px 0;padding-left:18px}
footer{margin-top:40px;color:var(--muted);font-size:12px}
"""


# --- helpers ------------------------------------------------------------------------------

def esc(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def md_inline(text: str) -> str:
    """Escaped text with `**bold**` rendered; everything else stays literal."""
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc(text))


def cls(score: float | None) -> str:
    if score is None:
        return "na"
    return "good" if score >= GOOD else "ok" if score >= OK else "bad"


def fmt(score: float | None, digits: int = 2) -> str:
    return "n/a" if score is None else f"{score:.{digits}f}"


def pill(text: str, kind: str) -> str:
    return f'<span class="pill {kind}">{esc(text)}</span>'


def passfail(ok: bool | None, yes: str = "pass", no: str = "fail") -> str:
    if ok is None:
        return pill("n/a", "na")
    return pill(yes, "good") if ok else pill(no, "bad")


def bar(score: float | None) -> str:
    if score is None:
        return '<div class="bar"></div>'
    return f'<div class="bar {cls(score)}"><i style="width:{max(0.0, min(1.0, score)) * 100:.0f}%"></i></div>'


def golden_cases() -> dict[str, dict[str, Any]]:
    idx: dict[str, dict[str, Any]] = {}
    for f in sorted(GOLDEN.glob("*.json")):
        try:
            for c in json.loads(f.read_text()).get("cases") or []:
                idx[c["id"]] = c
        except (OSError, ValueError):
            continue
    return idx


def question_of(r: dict[str, Any], golden: dict[str, Any] | None) -> str:
    if golden and golden.get("question"):
        return str(golden["question"])
    for m in (r.get("strands_trace") or {}).get("messages") or []:
        if m.get("role") == "user":
            for c in m.get("content") or []:
                if "text" in c:
                    return re.sub(r"^\[journey_id=[^\]]*\]\s*", "", c["text"])
    return ""


def metric_order(report: dict[str, Any]) -> list[str]:
    """Report order: the run's metric list, else deterministic then LLM, then any other seen."""
    seen: list[str] = list(report.get("uaef_metrics") or [])
    for r in report.get("results") or []:
        for name in ((r.get("uaef") or {}).get("metrics") or {}):
            if name not in seen:
                seen.append(name)
    known = [m for m in DETERMINISTIC + LLM if m in seen]
    return known + [m for m in seen if m not in known]


def tool_diff(used: list[str], expected: list[str] | None) -> tuple[list[str], list[str]]:
    """(missing, extra) against the golden `expected_tools`; nothing when the case declares none."""
    if not expected:
        return [], []
    return sorted(set(expected) - set(used)), sorted(set(used) - set(expected))


_JACCARD = re.compile(r"Jaccard [\d.]+: (\d+) matched(?:, (\d+) missing)?(?:, (\d+) extra)?")


def stale_tool_score(u: dict[str, Any] | None, missing: list[str], extra: list[str]) -> bool:
    """True when UAEF's stored tool-selection reasoning counts a different miss/extra than the
    golden on disk gives now: the ground truth changed after the report was scored."""
    reasoning = ((u or {}).get("metrics") or {}).get("tool_selection_accuracy", {}).get("reasoning") or ""
    m = _JACCARD.search(reasoning)
    if not m:
        return False
    return (int(m.group(2) or 0), int(m.group(3) or 0)) != (len(missing), len(extra))


# --- sections -----------------------------------------------------------------------------

def header(report: dict[str, Any], results: list[dict[str, Any]], src: Path) -> str:
    agents = sorted({r.get("agent") for r in results if r.get("agent")})
    suites = sorted({r.get("suite") for r in results if r.get("suite")})
    models = sorted({(r.get("strands_trace") or {}).get("model") for r in results
                     if (r.get("strands_trace") or {}).get("model")})
    engines = sorted({r.get("engine") for r in results if r.get("engine")})
    title = f"{' / '.join(agents) or 'Agent'} evaluation report"
    ran = report.get("ran_at") or ""
    try:
        ran = _dt.datetime.fromisoformat(ran).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        pass
    meta = [("Run", f"{report.get('mode', '?')} mode, {ran}"), ("Suite", ", ".join(suites)),
            ("Engine", ", ".join(engines)), ("Model", ", ".join(models) or "—"),
            ("Git", report.get("git") or "—"), ("Source", src.name)]
    rows = "".join(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in meta)
    return f"<h1>{esc(title)}</h1><p class='sub'>Harness gate and UAEF scores per case.</p>" \
           f"<table style='max-width:720px'>{rows}</table>"


def summary_cards(report: dict[str, Any], results: list[dict[str, Any]]) -> str:
    total, passed = int(report.get("total") or len(results)), int(report.get("passed") or 0)
    scored = [r for r in results if isinstance(r.get("uaef"), dict) and r["uaef"].get("metrics")]
    overall = [r["uaef"]["overall_score"] for r in scored if r["uaef"].get("overall_score") is not None]
    uaef_passed = sum(1 for r in scored if r["uaef"].get("passed"))
    lat = [r["latency_ms"] for r in results if r.get("latency_ms")]
    tok_in = sum((r.get("uaef") or {}).get("tokens", [None, None])[0] or 0 for r in scored)
    tok_out = sum((r.get("uaef") or {}).get("tokens", [None, None])[1] or 0 for r in scored)
    cards = [
        ("Harness gate", f"{passed}/{total}", f"{(report.get('pass_rate') or 0) * 100:.0f}% of cases pass every check",
         "good" if passed == total else "bad"),
    ]
    if scored:
        mean = statistics.fmean(overall) if overall else None
        cards += [
            ("UAEF overall", fmt(mean), f"mean of {len(overall)} scored cases (min {fmt(min(overall))})", cls(mean)),
            ("UAEF passed", f"{uaef_passed}/{len(scored)}",
             f"every category ≥ {UAEF_PASS:.2f}; scores are reported, not gating",
             "good" if uaef_passed == len(scored) else "ok"),
            ("Tokens", f"{tok_in + tok_out:,}", f"{tok_in:,} in · {tok_out:,} out, across {len(scored)} cases", "na"),
        ]
    if lat:
        note = (f"median · max {max(lat) / 1000:.1f} s · UAEF target {LATENCY_THRESHOLD_MS / 1000:.0f} s "
                f"({len(lat)} timed cases)")
        cards.append(("Latency", f"{statistics.median(lat) / 1000:.1f} s", note, "na"))
    out = []
    for k, v, n, kind in cards:
        color = {"good": "var(--good)", "ok": "var(--ok)", "bad": "var(--bad)"}.get(kind, "var(--ink)")
        out.append(f"<div class='card'><div class='k'>{esc(k)}</div><div class='v' style='color:{color}'>{esc(v)}</div>"
                   f"<div class='n'>{esc(n)}</div></div>")
    return f"<h2>Summary</h2><div class='cards'>{''.join(out)}</div>"


def means_table(report: dict[str, Any], metrics: list[str]) -> str:
    means = report.get("uaef_means") or {}
    if not means:
        return ""
    rows = []
    for m in metrics:
        v, n = means.get(m), means.get(f"{m}__n")
        if v is None and n is None:
            continue
        kind = "deterministic" if m in DETERMINISTIC else "LLM judge" if m in LLM else "custom"
        rows.append(f"<tr><td>{esc(METRIC_LABEL.get(m, m))} <span class='small'>{esc(kind)}</span></td>"
                    f"<td class='mono small'>{esc(m)}</td><td class='score {cls(v)}'>{fmt(v)}</td>"
                    f"<td>{bar(v)}</td><td class='num'>{esc(n)}</td></tr>")
    note = (f"Means over the cases that returned a score for the metric (n). UAEF's latency metric scores the "
            f"model latency against {LATENCY_THRESHOLD_MS / 1000:.0f} s; tool selection is Jaccard against the "
            f"golden <code>expected_tools</code>, so refusal cases (which declare none) carry no tool or "
            f"hallucination score. Green ≥ {GOOD:.2f}, amber ≥ {OK:.2f}, red below.")
    return (f"<h2>UAEF metric means</h2><p class='small'>{note}</p><table style='max-width:900px'>"
            f"<tr><th>Metric</th><th>Key</th><th>Mean</th><th></th><th class='num'>n</th></tr>{''.join(rows)}</table>")


def case_table(results: list[dict[str, Any]], metrics: list[str], golden: dict[str, dict[str, Any]]) -> str:
    head = "".join(f"<th title='{esc(m)}'>{esc(METRIC_LABEL.get(m, m))}</th>" for m in metrics)
    rows = []
    for r in results:
        u = r.get("uaef") if isinstance(r.get("uaef"), dict) else None
        g = golden.get(r.get("id", ""))
        missing, extra = tool_diff(r.get("tools_used") or [], (g or {}).get("expected_tools"))
        tools = f"{len(r.get('tools_used') or [])}"
        if missing or extra:
            tools += " <span class='small'>(" + ", ".join(
                [f"<span class='miss'>−{esc(t)}</span>" for t in missing] +
                [f"<span class='extra'>+{esc(t)}</span>" for t in extra]) + ")</span>"
        cells = "".join(
            f"<td class='score {cls((u or {}).get('metrics', {}).get(m, {}).get('score') if u else None)}'>"
            f"{fmt((u or {}).get('metrics', {}).get(m, {}).get('score') if u else None)}</td>" for m in metrics)
        lat = r.get("latency_ms")
        toks = (u or {}).get("tokens") or [None, None]
        rows.append(
            f"<tr><td><a href='#case-{esc(r.get('id'))}' class='mono'>{esc(r.get('id'))}</a></td>"
            f"<td>{passfail(r.get('passed'))}</td>"
            f"<td class='score {cls((u or {}).get('overall_score') if u else None)}'>"
            f"{fmt((u or {}).get('overall_score') if u else None)}</td>"
            f"<td>{passfail(u.get('passed') if u and u.get('metrics') else None)}</td>"
            f"{cells}<td>{tools}</td><td class='num'>{f'{lat / 1000:.1f} s' if lat else '—'}</td>"
            f"<td class='num'>{esc(f'{toks[0]:,} / {toks[1]:,}') if toks[0] is not None else '—'}</td></tr>")
    legend = ("Harness = the golden expectations (refusal, citations, tools, latency cap, guardrail). UAEF = every "
              "category at or above the threshold. Tools: count called, with <span class='miss'>−missing</span> / "
              "<span class='extra'>+extra</span> against <code>expected_tools</code>. Tokens: input / output.")
    return (f"<h2>Cases</h2><p class='small'>{legend}</p><table>"
            f"<tr><th>Case</th><th>Harness</th><th>UAEF</th><th>UAEF pass</th>{head}<th>Tools</th>"
            f"<th class='num'>Latency</th><th class='num'>Tokens</th></tr>{''.join(rows)}</table>")


def findings(results: list[dict[str, Any]], golden: dict[str, dict[str, Any]]) -> str:
    items = []
    stale: list[str] = []
    for r in results:
        u = r.get("uaef") if isinstance(r.get("uaef"), dict) else None
        g = golden.get(r.get("id", ""), {})
        notes: list[str] = []
        if not r.get("passed"):
            bad = [c for c in r.get("checks") or [] if not c.get("ok")]
            notes.append("Harness checks failed: " + "; ".join(
                f"<code>{esc(c.get('name'))}</code> {esc(c.get('detail'))}" for c in bad))
        if u and u.get("error"):
            notes.append(f"UAEF did not score this case: <code>{esc(u['error'])}</code>")
        elif u and u.get("metrics"):
            for f in u.get("failures") or []:
                notes.append(esc(f))
            # Every metric under the threshold, whether or not UAEF failed the case: UAEF
            # averages a category (safety+bias+toxicity), so one low judge can hide behind two 1.0s.
            low = [(m, s) for m, s in (u.get("metrics") or {}).items()
                   if s.get("score") is not None and s["score"] < UAEF_PASS]
            for m, s in low:
                hidden = "" if not u.get("passed") else " <span class='small'>(case still passed UAEF: the category mean cleared the threshold)</span>"
                notes.append(f"<code>{esc(m)}</code> = {fmt(s['score'])}{hidden}: "
                             f"<span class='reason'>{esc(s.get('reasoning'))}</span>")
            if g.get("expect", {}).get("refused") and any(m == "answer_relevance" for m, _ in low):
                notes.append("<span class='small'>The harness expects a refusal here (<code>expect.refused</code> "
                             "is true), so the relevance judge is scoring a fence, not an answer.</span>")
        missing, extra = tool_diff(r.get("tools_used") or [], g.get("expected_tools"))
        if missing or extra:
            notes.append("Tools vs <code>expected_tools</code>: " + ", ".join(
                [f"<span class='miss'>missing {esc(t)}</span>" for t in missing] +
                [f"<span class='extra'>extra {esc(t)}</span>" for t in extra]))
        if stale_tool_score(u, missing, extra):
            stale.append(r.get("id", ""))
            notes.append("<span class='small'>The stored tool-selection score was computed against an earlier "
                         "<code>expected_tools</code>; the golden on disk now gives "
                         f"{len(missing)} missing / {len(extra)} extra.</span>")
        if notes:
            items.append(f"<li><a href='#case-{esc(r.get('id'))}' class='mono'>{esc(r.get('id'))}</a>"
                         f"<ul class='tight'>{''.join(f'<li>{n}</li>' for n in notes)}</ul></li>")
    if not items:
        return "<h2>Findings</h2><p>Every case passed the harness and UAEF with no tool mismatch.</p>"
    note = ""
    if stale:
        note = (f"<p class='small'>Tool-selection scores for {len(stale)} case(s) predate the current golden "
                f"<code>expected_tools</code>. Re-score the report without re-running the agent: "
                f"<code>python3 -m app.eval.uaef_bridge --report &lt;report.json&gt; --llm</code>.</p>")
    return f"<h2>Findings</h2>{note}<ul>{''.join(items)}</ul>"


def tool_stats(trace: dict[str, Any] | None) -> str:
    usage = ((trace or {}).get("metrics_summary") or {}).get("tool_usage") or {}
    if not usage:
        return ""
    rows = []
    for name, info in usage.items():
        st = info.get("execution_stats") or {}
        params = (info.get("tool_info") or {}).get("input_params") or {}
        rows.append(f"<tr><td class='mono'>{esc(name)}</td><td class='mono small'>{esc(json.dumps(params))}</td>"
                    f"<td class='num'>{esc(st.get('call_count'))}</td><td class='num'>{esc(st.get('error_count'))}</td>"
                    f"<td class='num'>{(st.get('average_time') or 0) * 1000:.0f} ms</td></tr>")
    ms = trace.get("metrics_summary") or {}
    acc = ms.get("accumulated_metrics") or {}
    cap = (f"{ms.get('total_cycles')} model cycle(s), model latency "
           f"{(acc.get('latencyMs') or 0) / 1000:.1f} s, stop reason <code>{esc(trace.get('stop_reason'))}</code>")
    return (f"<h3>Tool execution</h3><p class='small'>{cap}</p><table>"
            f"<tr><th>Tool</th><th>Input</th><th class='num'>Calls</th><th class='num'>Errors</th>"
            f"<th class='num'>Avg time</th></tr>{''.join(rows)}</table>")


def case_detail(r: dict[str, Any], metrics: list[str], golden: dict[str, Any] | None) -> str:
    u = r.get("uaef") if isinstance(r.get("uaef"), dict) else None
    cid = r.get("id", "")
    q = question_of(r, golden)
    expect = (golden or {}).get("expect") or {}
    status = passfail(r.get("passed"), "harness pass", "harness fail")
    if u and u.get("metrics"):
        status += " " + passfail(u.get("passed"), f"UAEF {fmt(u.get('overall_score'))}", f"UAEF {fmt(u.get('overall_score'))}")
    checks = "".join(f"<tr><td class='mono'>{esc(c.get('name'))}</td><td>{passfail(c.get('ok'), 'ok', 'fail')}</td>"
                     f"<td class='small'>{esc(c.get('detail'))}</td></tr>" for c in r.get("checks") or [])
    cites = "".join(f"<li><code>{esc(c.get('label'))}</code> {esc(c.get('ref'))}</li>" for c in r.get("citations") or [])
    expected = (golden or {}).get("expected_tools")
    missing, extra = tool_diff(r.get("tools_used") or [], expected)
    tools_html = ", ".join(f"<code>{esc(t)}</code>" for t in r.get("tools_used") or []) or "<span class='small'>none</span>"
    if expected is not None:
        tools_html += f"<div class='small'>expected: {', '.join(f'<code>{esc(t)}</code>' for t in expected) or 'none'}"
        if missing:
            tools_html += f" · <span class='miss'>missing {', '.join(esc(t) for t in missing)}</span>"
        if extra:
            tools_html += f" · <span class='extra'>extra {', '.join(esc(t) for t in extra)}</span>"
        tools_html += "</div>"
    uaef_rows = ""
    if u and u.get("metrics"):
        uaef_rows = "".join(
            f"<tr><td>{esc(METRIC_LABEL.get(m, m))}<div class='mono small'>{esc(m)}</div></td>"
            f"<td class='score {cls(u['metrics'][m].get('score'))}'>{fmt(u['metrics'][m].get('score'))}</td>"
            f"<td class='reason'>{esc(u['metrics'][m].get('reasoning'))}</td></tr>"
            for m in metrics if m in u["metrics"])
    elif u and u.get("error"):
        uaef_rows = f"<tr><td colspan='3' class='bad'>UAEF error: {esc(u['error'])}</td></tr>"
    expect_txt = esc(json.dumps(expect, sort_keys=True)) if expect else "—"
    raw_answer = r.get("answer") or ""
    answer = md_inline(raw_answer) or "<span class='small'>(empty response)</span>"
    if len(raw_answer) >= 500:              # run_eval keeps the first 500 characters of the answer
        answer += " <span class='small'>[… answer truncated to 500 characters by the harness]</span>"
    body = f"""
<div class='body'>
  <p><strong>Question</strong> {esc(q) or '—'} <span class='small'>· expect <code>{expect_txt}</code>
     · journey <code>{esc((golden or {}).get('journey_id') or '—')}</code></span></p>
  <div class='answer'>{answer}</div>
  <div class='grid2'>
    <div><h3>Harness checks</h3><table><tr><th>Check</th><th></th><th>Detail</th></tr>{checks or "<tr><td colspan='3' class='small'>none</td></tr>"}</table></div>
    <div><h3>Citations</h3>{f"<ul class='tight'>{cites}</ul>" if cites else "<p class='small'>none</p>"}
         <h3 style='margin-top:12px'>Tools called</h3><p>{tools_html}</p></div>
  </div>
  {f"<h3>UAEF metrics</h3><table><tr><th>Metric</th><th>Score</th><th>Reasoning</th></tr>{uaef_rows}</table>" if uaef_rows else ""}
  {tool_stats(r.get("strands_trace"))}
</div>"""
    lat = r.get("latency_ms")
    summ = (f"<summary><a id='case-{esc(cid)}'></a><span class='mono'>{esc(cid)}</span>"
            f"<span class='q'>{esc(q)}</span>{status}"
            f"<span class='small'>{f'{lat / 1000:.1f} s' if lat else ''}</span></summary>")
    return f"<details>{summ}{body}</details>"


def render(report: dict[str, Any], src: Path) -> str:
    results = report.get("results") or []
    metrics = metric_order(report)
    golden = golden_cases()
    parts = [header(report, results, src), summary_cards(report, results), means_table(report, metrics),
             case_table(results, metrics, golden), findings(results, golden), "<h2>Case details</h2>"]
    parts += [case_detail(r, metrics, golden.get(r.get("id", ""))) for r in results]
    parts.append(f"<footer>Rendered {_dt.datetime.now(_dt.UTC).strftime('%Y-%m-%d %H:%M UTC')} by "
                 f"<code>app/eval/html_report.py</code> from <code>{esc(src.name)}</code>.</footer>")
    title = esc(f"{report.get('mode', 'eval')} eval · {src.stem}")
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{title}</title>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'><style>{CSS}</style></head>"
            f"<body>{''.join(parts)}</body></html>\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render an eval report JSON as a self-contained HTML page.")
    ap.add_argument("report", help="a report written by run_eval.py or uaef_bridge.py")
    ap.add_argument("--out", help="output path (default: the report path with .html)")
    args = ap.parse_args(argv)
    src = Path(args.report)
    try:
        report = json.loads(src.read_text())
    except (OSError, ValueError) as e:
        print(f"cannot read {src}: {e}", file=sys.stderr)
        return 2
    if not isinstance(report, dict) or "results" not in report:
        print(f"{src} is not an eval report (no `results`)", file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else src.with_suffix(".html")
    out.write_text(render(report, src))
    n = len(report["results"])
    scored = sum(1 for r in report["results"] if isinstance(r.get("uaef"), dict) and r["uaef"].get("metrics"))
    print(f"{out}  ({n} cases, {scored} with UAEF scores)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
