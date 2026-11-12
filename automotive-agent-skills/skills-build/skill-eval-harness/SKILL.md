---
name: skill-eval-harness
description: >
  Pipeline skill for measuring whether an Agent Skill (SKILL.md) actually improves an agent's answers,
  using a blind pairwise evaluation with a separate judge model, per-dimension scores, and effect sizes.
  Use when adding or changing a skill, before publishing a win-rate claim, when a skill seems to hurt
  coherence, or when choosing which content to cut. Triggers include "evaluate skill", "skill eval",
  "pairwise evaluation", "with and without skill", "blind judge", "LLM as judge", "win rate", "Cohen's d",
  "prompt pack", "rubric", "review.html", "eval harness", "skill regression", "coherence regression",
  "does the skill help", "A/B skill test", "judge model", "Bedrock converse eval".
usage: Invoke when you need evidence that a SKILL.md helps, or to diagnose one that does not.
version: 1.0.0
tags: [skill, category:pipeline, evaluation, agent-skills, automotive]
validated_against:
  date: 2026-09-23
  packages: {python: "3.9+", boto3: "1.34+"}
---

# Skill Eval Harness — Pipeline Skill

## When to Use

- A new skill is written and someone wants to say it "helps".
- A skill was edited and you need to know whether the edit improved or regressed it.
- A skill wins on correctness but the answers got longer and worse to read (coherence regression).

## Framework Selection

| Situation | Run | Why |
|---|---|---|
| Harness or CI change | `--provider mock` | Proves the pipeline runs; proves nothing about skills |
| New or changed skill | `--provider bedrock --skill <name>` | Real generation and judging on that skill's prompt pack |
| Release claim | `--provider bedrock` over all skills, commit `summary.json` | The README may cite only committed, reproducible numbers |
| Suspected over-refusal (guardrail skills) | Inspect "allowed" prompts in `review.html` | Losses on allowed prompts mean the skill refuses what it should rewrite |

## Response Format

- Lead with the number that matters: win rate excluding ties and the dimension with the largest effect, then the weakest dimension.
- Then: per-skill table (n, W/T/L, mean Δ, d per dimension) → the two or three prompts that lost, with the judge's rationale → the specific edit each loss suggests.
- Never report mock-provider numbers as results.

## Core Procedure

1. **Write the prompt pack first**, `eval/prompts/<skill>.json`: 5–6 prompts with `id`, realistic `prompt`, and a `rubric` describing the correct answer's properties (thresholds, dates, hard rules, what must not appear).
2. **Target what the base model gets wrong.** Before writing a prompt, ask the model without the skill; if it already answers well, the prompt measures nothing. Keep at most one such control prompt per pack.
3. **For guardrail or refusal skills, include at least one "allowed" prompt** that should pass unchanged. It measures false positives.
4. **Use a different, stronger judge than the generator** (for example Sonnet generates, Opus judges). Judge temperature 0. Generator temperature 0.2 so answers vary slightly but not wildly.
5. **Run the mock provider once** after any harness change: `python3 eval/run.py --provider mock --version smoke`. Then delete `eval/results/smoke/`.
6. **Run the real evaluation:**
   ```bash
   python3 eval/run.py --provider bedrock --version v1 \
     --model us.anthropic.claude-sonnet-4-20250514 \
     --judge  us.anthropic.claude-opus-4-20250514
   python3 eval/build_review.py --version v1
   ```
7. **The harness randomizes A/B per prompt** and un-blinds after judging. Do not "fix" this; do not pass skill names or hints into the judge prompt.
8. **Read `summary.json`**: `win_rate_excl_ties`, `mean_delta`, and `cohen_d` per dimension. `d` is undefined (reported as 0) when every delta is identical; with real models that means the judge is not discriminating; check the rubric.
9. **Interpret by pattern:**
   ```
   win ≥ 70% and d ≥ 0.5 on correctness or compliance → skill teaches something missing; keep
   win ≈ 50%                                          → content already known; cut the recited parts
   coherence Δ < 0 while correctness Δ > 0            → too long or narrates trees; tighten Response Format, add "internal reasoning only"
   losses on allowed prompts                           → over-refusal; change refuse → rewrite with safe alternative
   ```
10. **Open `review.html` and read every loss.** The judge's one-sentence rationale plus the two answers side by side tells you which step or table caused it.
11. **Edit the skill, bump `version`, re-run with a new `--version` tag.** Never overwrite a results folder; the diff between versions is the evidence.
12. **Commit only `eval/results/<version>/summary.json`.** Full answers can contain long model output; keep them local. Never commit mock results.
13. **Cite in the README only a committed bedrock `summary.json`**, with model, judge, date, and n. If none exists, the README says so.
14. **Re-run when the model changes.** Skill benefit shrinks as base models improve (the HCLS data shows r ≈ −0.59 between baseline strength and skill benefit); a skill that helped last quarter may be dead weight now.
15. **Budget:** 33 prompts × (2 generations + 1 judge) ≈ 100 calls per full run; run per-skill during authoring and full only for releases.

## Reference Tables

| File | Purpose |
|---|---|
| `eval/run.py` | Generation, blind judging, summary JSON |
| `eval/build_review.py` | `review.html` with side-by-side answers and scores |
| `eval/prompts/<skill>.json` | Prompt pack with rubrics |
| `eval/results/<version>/summary.json` | The only artifact that is committed |

| Dimension | Judge looks for |
|---|---|
| correctness | Facts, thresholds, dates, jurisdiction; no fabricated figures |
| critical_thinking | Decision points, conditions, what would change the answer |
| compliance | Hard rules honoured; expired programs named; no prohibited or scare language |
| coherence | Structure, proportionate length, no narrated internal reasoning |

## Worked Example

`ev-incentive-eligibility` v1.0.0, 5 prompts. Result: 4 wins, 1 tie; correctness Δ +2.4 (d 1.1), coherence Δ −0.6. Loss inspection: `evi-04-tco` tied because both answers invented dollar totals; the rubric said not to. Fix: add step "do not produce precise totals without a tool result; give the method and inputs" to the skill, bump to 1.0.1, re-run as v2. (Illustrative; replace with real committed numbers.)

## Common Mistakes

1. **Wrong:** Quoting mock-provider results.
   **Right:** Mock proves the harness runs; delete its results.
   **Why:** The mock judge is rigged to prefer skill-tagged answers by construction.

2. **Wrong:** Same model as generator and judge.
   **Right:** Stronger, different judge at temperature 0.
   **Why:** Self-preference bias inflates wins.

3. **Wrong:** Telling the judge which answer used the skill.
   **Right:** Randomized A/B, un-blind after.
   **Why:** The whole design depends on blindness.

4. **Wrong:** Prompts the base model already answers correctly.
   **Right:** Prompts on expired programs, coverage order, HV gate, pricing direction.
   **Why:** You measure the skill's marginal value, not the model's baseline.

5. **Wrong:** No "allowed" prompts for a guardrail skill.
   **Right:** At least one prompt that should pass unchanged.
   **Why:** Over-refusal is invisible otherwise.

6. **Wrong:** Reading only the win rate.
   **Right:** Read per-dimension deltas and every loss in `review.html`.
   **Why:** A skill can win on correctness and lose the user on coherence.

7. **Wrong:** Overwriting the results folder on re-run.
   **Right:** New `--version` per run.
   **Why:** Version-to-version diff is the evidence the edit helped.

8. **Wrong:** Committing full result JSON with model outputs.
   **Right:** Commit `summary.json` only.
   **Why:** Size, noise, and occasional sensitive content in generated text.

9. **Wrong:** One full run at release, never again.
   **Right:** Re-run when the base model changes.
   **Why:** Skill benefit decays as models improve.

10. **Wrong:** Treating `d = 0` as "no effect" when all deltas are equal.
    **Right:** Check whether the judge is discriminating; fix the rubric.
    **Why:** Zero variance, not zero effect, produces that number.

## When to Escalate to a Human

- Any prompt where the "with skill" answer is *less* compliant (introduces a prohibited statement); that is a skill bug to fix before anything else.
- Judge rationales that contradict the rubric consistently; the rubric or judge prompt needs review by the skill author.

## References

- awslabs/hcls-agent-skills `eval/` (pairwise design, dimensions, review page) and `SKILL_DESIGN_GUIDE.md` (baseline-strength correlation, coherence trade-off)
- adewale/skill-eval-harness (paired-variant causal-lift design, leakage checks)
- Amazon Bedrock Converse API (boto3 `bedrock-runtime.converse`)
