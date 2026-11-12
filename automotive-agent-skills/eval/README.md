# Skill evaluation

Measures whether a `SKILL.md` improves the agent's answer, using the same design as awslabs/hcls-agent-skills: **pairwise, blind, judged**.

- Same prompt, same model, same temperature, answered **without** and **with** the skill body in the system prompt.
- A separate judge model sees both answers in randomized A/B order with a per-prompt rubric and returns 1–10 scores on four dimensions plus a winner.
- Dimensions: **correctness** (facts, thresholds, dates), **critical thinking** (branch points, conditions, what would change the answer), **compliance** (hard rules, no fabricated figures, no prohibited or scare language), **coherence** (structure, length discipline, no narrated reasoning).
- Outputs per skill: win / tie / loss counts, mean score deltas, Cohen's d; `review.html` shows every pair side by side.

## Run

```bash
# offline smoke test of the harness itself (no network, no credentials)
python3 eval/run.py --provider mock --version smoke
python3 eval/build_review.py --version smoke

# real run against Amazon Bedrock (needs credentials in the environment or profile)
python3 eval/run.py --provider bedrock --version v1 \
  --model us.anthropic.claude-sonnet-4-20250514 \
  --judge us.anthropic.claude-opus-4-20250514
python3 eval/build_review.py --version v1 && open eval/results/v1/review.html
```

Use a **different, stronger model as judge** than the one generating answers, and keep the judge at temperature 0. Never let the judge see which answer used the skill.

## Prompt packs

`eval/prompts/<skill>.json`: 5–6 prompts per skill, each with an `id`, a realistic `prompt`, and a `rubric` the judge sees. Prompts target what the base model gets wrong (expired programs, coverage order, HV gate, pricing direction), not what it already knows. Add prompts with a skill; keep at least one "allowed" prompt per guardrail-style skill to measure false positives.

## Reading results

| Signal | Meaning |
|---|---|
| Win rate ≥ 70%, d ≥ 0.5 on correctness or compliance | Skill teaches something the model lacked |
| Coherence delta negative | Skill is too long or narrates trees; tighten Response Format |
| Win rate ≈ 50% | Content the model already knows; cut it (see `SKILL_DESIGN_GUIDE.md`) |
| Losses on "allowed" prompts | Over-refusal; soften to rewrite-not-refuse |

Results in `eval/results/<version>/` are git-ignored except `summary.json`, which we commit so the README can cite a number that anyone can reproduce.

## Honesty rule

The repository README makes **no** performance claim until a `bedrock` run over all shipped skills exists and its `summary.json` is committed. The mock provider proves the harness runs; it proves nothing about the skills.
