#!/usr/bin/env python3
"""Generate TOOLS.md (the agent tool reference) from the Intelligent Sales & Service Orchestration requirements spec.

The spec is the source of truth for tool signatures. This script parses the
`## 3. Agent Specifications` section, collects every tool row of the form

    | `tool_name(args)` | source | notes |

per agent, and writes a grouped, alphabetised reference with the agent's
purpose line and the delivery model from the use-case classification.

Usage:
    python3 scripts/extract_tools.py [--spec PATH] [--out TOOLS.md]
"""
from __future__ import annotations

import argparse
import re
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_SPEC = HERE.parent.parent / "docs" / "specs" / "intelligent-sales-service-orchestration.md"

AGENT_META = {
    "1": ("A1", "Personalized Vehicle Discovery", "Custom agent on Amazon Bedrock AgentCore", "Process 1 — Retail purchase"),
    "2": ("A2", "Purchase Journey Orchestration (supervisor + nurture)", "Custom agent on Amazon Bedrock AgentCore", "Process 1 — Retail purchase"),
    "3": ("A3", "Dealer Sales Advisor", "Custom agent on Amazon Bedrock AgentCore (floor streaming + assistant chat/flows in one agent; Quick Index as a retrieval tool)", "Process 1 — Retail purchase"),
    "4": ("A4", "Trade-In & Upgrade Advisor", "Custom agent on Amazon Bedrock AgentCore", "Process 1 — Retail purchase"),
    "5": ("A5", "Campaign ROI Optimizer", "Amazon Quick / QuickSight analytics + AgentCore for recommendations", "Process 1 — Retail purchase"),
    "6": ("A6", "OEM Sales Administrator", "Amazon Quick (agent + Flows + Index + Research)", "Process 1 — Retail purchase"),
    "7": ("A7", "Service Operations (service-journey supervisor)", "Custom agent on Amazon Bedrock AgentCore", "Process 2 — Service & ownership"),
}

# A tool cell may hold one tool or several separated by " / ":
#   | `a(x)` | ... |            | `a(x)` / `b(y)` / `c(z)` | ... |
TOOL_RE = re.compile(r"`([a-z_]+)\(([^`]*)\)`(?: \*\(([^)]*)\)\*)?")


def md_escape(s: str) -> str:
    return s.replace("|", "\\|").strip()


def split_cells(line: str) -> list[str]:
    """Split a markdown table row on '|' that are outside backticks."""
    cells, buf, in_code = [], [], False
    for ch in line.strip().strip("|"):
        if ch == "`":
            in_code = not in_code
        if ch == "|" and not in_code:
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    cells.append("".join(buf).strip())
    return cells


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", default=str(DEFAULT_SPEC))
    ap.add_argument("--out", default=str(HERE.parent / "TOOLS.md"))
    args = ap.parse_args()

    txt = Path(args.spec).read_text(encoding="utf-8")
    start = txt.index("## 3. Agent Specifications")
    end = txt.index("## 4. Data Sources")
    sec = txt[start:end]

    parts = re.split(r"\n### 3\.(\d)(?:\.\d)? ", sec)
    per_agent: dict[str, dict[str, tuple[str, str, str]]] = {}
    for i in range(1, len(parts), 2):
        n, body = parts[i], parts[i + 1]
        bucket = per_agent.setdefault(n, {})
        for line in body.splitlines():
            if not line.startswith("| `"):
                continue
            cells = split_cells(line)
            if len(cells) < 2:
                continue
            tool_cell, rest = cells[0], cells[1:]
            desc = " ".join(c for c in rest if c).strip()
            for t in TOOL_RE.finditer(tool_cell):
                name, sig, flag = t.groups()
                if name == "write_trace":
                    continue
                bucket.setdefault(name, (sig.strip(), flag or "", desc))

    total = sum(len(v) for v in per_agent.values())
    proactive = sum(1 for v in per_agent.values() for (_, f, _) in v.values() if "proactive" in f or "2026-09-22" in f)

    out: list[str] = []
    out.append("# Tools Reference\n")
    out.append(
        "Agent **tools** are the callable functions an agent runtime exposes (Strands `@tool`, "
        "AgentCore Gateway targets, Quick Flow steps). They are distinct from **skills**, which are "
        "`SKILL.md` knowledge modules that shape how an agent reasons. Skills in this repository "
        "reference these tools by name where a procedure step maps to a call.\n"
    )
    out.append(
        f"Generated {date.today().isoformat()} from the Intelligent Sales & Service Orchestration requirements spec v1.3 §3 by "
        "`scripts/extract_tools.py`. Do not edit by hand; change the spec and re-run.\n"
    )
    out.append(f"**{total} tools across 7 agents** ({proactive} proactive, event- or schedule-triggered), "
               "plus `write_trace(...)` shared by all agents for the AI Log and audit trail.\n")
    out.append("## Conventions\n")
    out.append("- Every effectful tool passes `autonomy_level(action_id, scope)` before executing "
               "(observe / recommend / act_with_approval / act_and_notify). Hard rules (consent, quiet hours, "
               "OEM guardrails GR-001…008, customer-owns-approval, no automatic spend or allocation, technician "
               "data masking, HV certification) sit above configuration.\n"
               "- Every figure a tool returns carries a `source_ref`; agents never quote a number a tool did not return.\n"
               "- Every recommendation a tool surfaces is a row in `DEALER_Recommendations` (shown → accepted → converted).\n"
               "- Tools marked **proactive** run on events or schedules with no user request.\n")

    out.append("## Summary\n")
    out.append("| Agent | Role | Delivery model | Process | Tools |")
    out.append("|---|---|---|---|---|")
    for n in sorted(per_agent):
        code, role, model, proc = AGENT_META[n]
        out.append(f"| {code} | {role} | {model} | {proc} | {len(per_agent[n])} |")
    out.append("")

    for n in sorted(per_agent):
        code, role, model, proc = AGENT_META[n]
        tools = per_agent[n]
        out.append(f"## {code} — {role}\n")
        out.append(f"*Delivery model:* {model}  \n*Process:* {proc}  \n*Tools:* {len(tools)}\n")
        out.append("| Tool | Signature | Source / behaviour | Proactive |")
        out.append("|---|---|---|---|")
        for name in sorted(tools):
            sig, flag, desc = tools[name]
            pro = "yes" if flag else ""
            out.append(f"| `{name}` | `{name}({md_escape(sig)})` | {md_escape(desc)} | {pro} |")
        out.append("")

    out.append("## Shared\n")
    out.append("| Tool | Signature | Source / behaviour |")
    out.append("|---|---|---|")
    out.append("| `write_trace` | `write_trace(journey_id \\| service_journey_id, span)` | `PLAT_AgentTraces`; powers the AI Log; carries `correlation_id`, autonomy level in force, and tool citations |")
    out.append("")

    Path(args.out).write_text("\n".join(out), encoding="utf-8")
    print(f"wrote {args.out}: {total} tools, {proactive} proactive, {len(per_agent)} agents")


if __name__ == "__main__":
    main()
