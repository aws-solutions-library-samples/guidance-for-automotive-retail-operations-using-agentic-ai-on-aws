#!/usr/bin/env python3
"""Pairwise skill evaluation: does a SKILL.md make the agent's answer better?

Design (modelled on awslabs/hcls-agent-skills eval and adewale/skill-eval-harness):

  1. For each prompt in eval/prompts/<skill>.json, generate two answers with the same
     model, temperature, and system prompt: one WITHOUT the skill and one WITH the
     SKILL.md body prepended to the system prompt.
  2. A separate judge model sees both answers in RANDOM A/B order (blind), plus the
     prompt and a rubric, and returns per-dimension scores (1–10) and a winner.
  3. The harness un-blinds, records deltas, and writes eval/results/<version>/*.json.
     `build_review.py` turns results into an HTML review page.

Dimensions: correctness (facts, thresholds, dates), critical_thinking (branching,
caveats, what would change the answer), compliance (hard rules, no fabricated numbers,
no scare or prohibited language), coherence (structure, length discipline).

Providers:
  bedrock  – Amazon Bedrock Converse API (boto3). Needs credentials.
  mock     – deterministic offline stub for CI and harness tests. No network.

Usage:
  python3 eval/run.py --provider mock --version smoke
  python3 eval/run.py --provider bedrock --skill service-estimate-integrity --version v1 \
      --model us.anthropic.claude-sonnet-4-20250514 --judge us.anthropic.claude-opus-4-20250514
  python3 eval/build_review.py --version v1 && open eval/results/v1/review.html
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
PROMPTS = ROOT / "eval" / "prompts"
RESULTS = ROOT / "eval" / "results"

DIMENSIONS = ["correctness", "critical_thinking", "compliance", "coherence"]

BASE_SYSTEM = (
    "You are an assistant helping automotive dealer and OEM staff. Answer the user's request "
    "directly and concretely. Do not invent numbers you cannot source. Keep the answer under 400 words."
)

JUDGE_SYSTEM = (
    "You are a strict, blind evaluator of two assistant answers to the same automotive-domain prompt. "
    "You do not know which answer used additional instructions. Score each answer 1–10 on four dimensions "
    "and pick a winner. Correctness: facts, thresholds, dates, jurisdiction right; no fabricated figures. "
    "Critical thinking: identifies the decision points, conditions, and what would change the answer. "
    "Compliance: respects hard rules (consent, customer owns approval, no pricing direction to dealers, "
    "no scare language, expired programs named as expired). Coherence: structured, proportionate length, "
    "no narrated internal reasoning. Return ONLY JSON: "
    '{"A": {"correctness": n, "critical_thinking": n, "compliance": n, "coherence": n}, '
    '"B": {...same...}, "winner": "A"|"B"|"tie", "rationale": "one sentence"}'
)


# --------------------------------------------------------------------------- providers
class Provider:
    def complete(self, system: str, user: str, model: str, temperature: float) -> str:  # pragma: no cover
        raise NotImplementedError


class MockProvider(Provider):
    """Deterministic stub. Answers echo whether skill text was present so the harness can be tested
    end to end without network. Judge output is derived from that signal plus a stable hash."""

    def complete(self, system: str, user: str, model: str, temperature: float) -> str:
        if system.startswith(JUDGE_SYSTEM[:40]):
            has_a = "[skill-informed]" in user.split("=== ANSWER B ===")[0]
            has_b = "[skill-informed]" in user.split("=== ANSWER B ===")[1]
            h = int(hashlib.sha256(user.encode()).hexdigest(), 16) % 3
            def score(informed: bool) -> dict:
                base = 8 if informed else 6
                return {d: max(1, min(10, base + (h - 1 if d == "coherence" else 0))) for d in DIMENSIONS}
            winner = "A" if has_a and not has_b else "B" if has_b and not has_a else "tie"
            return json.dumps({"A": score(has_a), "B": score(has_b), "winner": winner,
                               "rationale": "mock judge: skill-informed answer preferred"})
        informed = "## Core Procedure" in system
        tag = "[skill-informed] " if informed else ""
        return f"{tag}Mock answer to: {user[:80]}..."


class BedrockProvider(Provider):
    def __init__(self, region: str):
        import boto3  # local import so mock mode needs no boto3
        self.client = boto3.client("bedrock-runtime", region_name=region)

    def complete(self, system: str, user: str, model: str, temperature: float) -> str:
        for attempt in range(5):
            try:
                resp = self.client.converse(
                    modelId=model,
                    system=[{"text": system}],
                    messages=[{"role": "user", "content": [{"text": user}]}],
                    inferenceConfig={"temperature": temperature, "maxTokens": 1500},
                )
                return resp["output"]["message"]["content"][0]["text"]
            except Exception as e:  # throttling, transient
                if attempt == 4:
                    raise
                time.sleep(2 ** attempt)
        return ""


# --------------------------------------------------------------------------- data
@dataclass
class Case:
    skill: str
    prompt_id: str
    prompt: str
    scores_without: dict
    scores_with: dict
    winner: str  # "with" | "without" | "tie"
    rationale: str
    answer_without: str
    answer_with: str


def load_skill_body(skill: str) -> str:
    text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
    end = text.find("\n---", 4)
    return text[end + 4:].strip() if text.startswith("---") and end != -1 else text


def load_prompts(skill: str) -> list[dict]:
    p = PROMPTS / f"{skill}.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))["prompts"]


def parse_judge(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError(f"judge returned no JSON: {raw[:200]}")
    return json.loads(m.group(0))


# --------------------------------------------------------------------------- core
def evaluate_skill(skill: str, provider: Provider, model: str, judge: str, temperature: float,
                   rng: random.Random, verbose: bool) -> list[Case]:
    body = load_skill_body(skill)
    prompts = load_prompts(skill)
    cases: list[Case] = []
    for pr in prompts:
        ans_without = provider.complete(BASE_SYSTEM, pr["prompt"], model, temperature)
        ans_with = provider.complete(BASE_SYSTEM + "\n\n# Skill\n\n" + body, pr["prompt"], model, temperature)
        # blind randomisation
        a_is_with = rng.random() < 0.5
        A, B = (ans_with, ans_without) if a_is_with else (ans_without, ans_with)
        judge_user = (f"PROMPT:\n{pr['prompt']}\n\nRUBRIC NOTES:\n{pr.get('rubric', '')}\n\n"
                      f"=== ANSWER A ===\n{A}\n\n=== ANSWER B ===\n{B}")
        verdict = parse_judge(provider.complete(JUDGE_SYSTEM, judge_user, judge, 0.0))
        s_with = verdict["A"] if a_is_with else verdict["B"]
        s_without = verdict["B"] if a_is_with else verdict["A"]
        w = verdict.get("winner", "tie")
        winner = "tie" if w == "tie" else ("with" if (w == "A") == a_is_with else "without")
        cases.append(Case(skill, pr["id"], pr["prompt"], s_without, s_with, winner,
                          verdict.get("rationale", ""), ans_without, ans_with))
        if verbose:
            print(f"  {pr['id']}: {winner}")
    return cases


def summarize(cases: list[Case]) -> dict:
    n = len(cases)
    wins = sum(c.winner == "with" for c in cases)
    ties = sum(c.winner == "tie" for c in cases)
    deltas = {d: [c.scores_with[d] - c.scores_without[d] for c in cases] for d in DIMENSIONS}
    def cohen_d(xs: list[float]) -> float:
        if len(xs) < 2 or statistics.pstdev(xs) == 0:
            return 0.0
        return statistics.mean(xs) / statistics.pstdev(xs)
    return {
        "n": n, "wins": wins, "ties": ties, "losses": n - wins - ties,
        "win_rate": round(wins / n, 3) if n else 0.0,
        "win_rate_excl_ties": round(wins / (n - ties), 3) if n - ties else 0.0,
        "mean_delta": {d: round(statistics.mean(v), 2) if v else 0.0 for d, v in deltas.items()},
        "cohen_d": {d: round(cohen_d(v), 2) for d, v in deltas.items()},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["bedrock", "mock"], default="mock")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--model", default="us.anthropic.claude-sonnet-4-20250514")
    ap.add_argument("--judge", default="us.anthropic.claude-opus-4-20250514")
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--skill", help="one skill; default all with a prompt pack")
    ap.add_argument("--version", required=True, help="results folder name, e.g. v1")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    provider = MockProvider() if args.provider == "mock" else BedrockProvider(args.region)
    rng = random.Random(args.seed)
    skills = [args.skill] if args.skill else sorted(p.stem for p in PROMPTS.glob("*.json"))
    out_dir = RESULTS / args.version
    out_dir.mkdir(parents=True, exist_ok=True)

    overall: list[Case] = []
    summary: dict = {"version": args.version, "provider": args.provider, "model": args.model,
                     "judge": args.judge, "temperature": args.temperature, "skills": {}}
    for skill in skills:
        if not (SKILLS / skill / "SKILL.md").exists():
            print(f"skip {skill}: no SKILL.md"); continue
        print(f"{skill}")
        cases = evaluate_skill(skill, provider, args.model, args.judge, args.temperature, rng, args.verbose)
        if not cases:
            print("  no prompts"); continue
        (out_dir / f"{skill}.json").write_text(json.dumps([asdict(c) for c in cases], indent=2), encoding="utf-8")
        s = summarize(cases)
        summary["skills"][skill] = s
        overall.extend(cases)
        print(f"  win {s['wins']}/{s['n']} (ties {s['ties']})  Δ " +
              " ".join(f"{d}:{s['mean_delta'][d]:+.1f}" for d in DIMENSIONS))
    if overall:
        summary["overall"] = summarize(overall)
        (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        o = summary["overall"]
        print(f"\nOVERALL {o['wins']}/{o['n']} wins ({o['win_rate']:.0%}; {o['win_rate_excl_ties']:.0%} excl. ties)  "
              f"d: " + " ".join(f"{d}={o['cohen_d'][d]:+.2f}" for d in DIMENSIONS))
        print(f"results: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
