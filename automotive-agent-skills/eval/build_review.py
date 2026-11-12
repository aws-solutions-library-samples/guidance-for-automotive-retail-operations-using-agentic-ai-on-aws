#!/usr/bin/env python3
"""Render eval/results/<version>/*.json into a single review.html for human inspection."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "eval" / "results"
DIMS = ["correctness", "critical_thinking", "compliance", "coherence"]

CSS = """
body{font:14px/1.45 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#1f2937;margin:2rem;max-width:1100px}
h1,h2,h3{color:#0b1f3a} table{border-collapse:collapse;width:100%;margin:.5rem 0 1.5rem}
th,td{border:1px solid #e5e7eb;padding:.4rem .6rem;text-align:left;vertical-align:top}
th{background:#0b1f3a;color:#fff} .win{color:#0f766e;font-weight:600} .loss{color:#b91c1c;font-weight:600}
.tie{color:#6b7280} details{margin:.5rem 0 1rem;border:1px solid #e5e7eb;border-radius:6px;padding:.5rem .8rem}
summary{cursor:pointer;font-weight:600} pre{white-space:pre-wrap;background:#f8fafc;padding:.6rem;border-radius:4px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:1rem} .muted{color:#6b7280;font-size:12px}
"""


def esc(s: str) -> str:
    return html.escape(s or "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    args = ap.parse_args()
    d = RESULTS / args.version
    summary = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    parts = [f"<html><head><meta charset='utf-8'><title>Skill eval {esc(args.version)}</title><style>{CSS}</style></head><body>"]
    parts.append(f"<h1>Skill evaluation — {esc(args.version)}</h1>")
    parts.append(f"<p class='muted'>provider {esc(summary['provider'])} · model {esc(summary['model'])} · judge {esc(summary['judge'])} · T={summary['temperature']} · blind pairwise, randomized A/B</p>")
    o = summary.get("overall", {})
    if o:
        parts.append(f"<h2>Overall: {o['wins']}/{o['n']} wins ({o['win_rate']:.0%}; {o['win_rate_excl_ties']:.0%} excl. ties)</h2>")
    parts.append("<table><tr><th>Skill</th><th>n</th><th>W / T / L</th><th>Win rate</th>" +
                 "".join(f"<th>Δ {d}</th>" for d in DIMS) + "".join(f"<th>d {d}</th>" for d in DIMS) + "</tr>")
    for skill, s in summary["skills"].items():
        parts.append(f"<tr><td>{esc(skill)}</td><td>{s['n']}</td><td>{s['wins']} / {s['ties']} / {s['losses']}</td>"
                     f"<td>{s['win_rate']:.0%}</td>" +
                     "".join(f"<td>{s['mean_delta'][d]:+.1f}</td>" for d in DIMS) +
                     "".join(f"<td>{s['cohen_d'][d]:+.2f}</td>" for d in DIMS) + "</tr>")
    parts.append("</table>")
    for skill in summary["skills"]:
        cases = json.loads((d / f"{skill}.json").read_text(encoding="utf-8"))
        parts.append(f"<h2>{esc(skill)}</h2>")
        for c in cases:
            cls = {"with": "win", "without": "loss", "tie": "tie"}[c["winner"]]
            label = {"with": "WITH skill wins", "without": "WITHOUT skill wins", "tie": "tie"}[c["winner"]]
            parts.append(f"<details><summary>{esc(c['prompt_id'])} — <span class='{cls}'>{label}</span> "
                         f"<span class='muted'>{esc(c['rationale'])}</span></summary>")
            parts.append(f"<p><b>Prompt.</b> {esc(c['prompt'])}</p>")
            parts.append("<table><tr><th></th>" + "".join(f"<th>{d}</th>" for d in DIMS) + "</tr>")
            parts.append("<tr><td>without</td>" + "".join(f"<td>{c['scores_without'][d]}</td>" for d in DIMS) + "</tr>")
            parts.append("<tr><td>with</td>" + "".join(f"<td>{c['scores_with'][d]}</td>" for d in DIMS) + "</tr></table>")
            parts.append("<div class='cols'><div><h4>Without skill</h4><pre>" + esc(c["answer_without"]) +
                         "</pre></div><div><h4>With skill</h4><pre>" + esc(c["answer_with"]) + "</pre></div></div></details>")
    parts.append("</body></html>")
    out = d / "review.html"
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
