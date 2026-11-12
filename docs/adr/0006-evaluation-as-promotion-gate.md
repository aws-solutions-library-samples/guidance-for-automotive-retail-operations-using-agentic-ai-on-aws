# ADR 0006 — Behaviour is measured by a golden-prompt harness before it is promoted

**Status:** Accepted (2026-10-08). Offline suite runs in CI; agent baseline kept.

## Context

Unit tests cover the tools (state machines, pricing, scope), not the *answers*.
Introducing a reasoning agent, a new retrieval engine and a model-tier guardrail
changes answers, and "it looks better" is not a promotion criterion. We needed a
fixed prompt set with deterministic expectations that runs without AWS in CI and
with AWS against the live agent.

## Decision

A golden-prompt evaluation harness with three modes and deterministic checks,
plus an optional LLM-as-judge score that is reported but not gating until a
baseline exists.

| Mode | Runs | Used for |
|---|---|---|
| `offline` | deterministic paths over the in-memory DynamoDB seeded from the synthetic packs; no AWS | CI gate (must be 100 %) |
| `agent` | A3 cases through `a3_agent.run` against the dev tables and Bedrock | scoring the pilot against the kept baseline |
| `api` | A3 cases through a deployed API (`POST /assistant/agent`) with a real token | post-deploy verification |

Checks per case (from each case's `expect`): refused / explained / scope,
refusal category or guardrail rule id, minimum citations, forbidden phrases
(fabricated prices, "I sent", scare words), required phrasings (agent/api only),
tools used (agent/api only), latency cap (default 25 s), guardrail did not
intervene on an allowed ask.

## Options considered

* **Manual spot checks.** Fast once, useless the second time.
* **LLM-as-judge only.** Non-deterministic and cannot run in CI without
  credentials; kept as an advisory score.
* **Deterministic golden checks, judge advisory** *(chosen)*.

## Implementation

```
app/eval/run_eval.py                  the runner (--mode, --only, --judge, --baseline, --min-pass, --out)
app/eval/golden/a3_assistant.json     15 cases (A3; journey J-US-J1, the seed advisor's own store)
app/eval/golden/a2_customer_qa.json   7 cases (customer Q&A fences + citations)
app/eval/golden/a6_workspace_ask.json 7 cases (GR-001…008 blocks with rule ids)
app/eval/golden/a7_explain.json       3 cases (finding explanations, scare language)
app/eval/reports/agent-baseline.json  the kept baseline (tracked); other reports are ignored
app/eval/README.md                    how to run, how to add a case
.gitlab-ci.yml                        stages: test (pytest) · eval (offline, min-pass 1.0) · console (lint:units, tsc)
```

## Consequences

* A regression in fences, citations or forbidden claims fails CI before deploy.
* The harness found two real gaps on its first run: a GR-002 pattern that missed
  "termination notice for the Marietta store", and (in deployment) that the
  guardrail did not evaluate the question unless it was tagged `guard_content`.
  Both were fixed and are now covered.
* Agent-mode runs write real rows in the target account (a task, a held outreach
  card) because they exercise the autonomy gate; point them only at a dev prefix.
* Promotion rule for a new agent: offline 100 %, agent mode ≥ baseline pass
  rate and judge means, no latency-cap failures.

## Baseline (2026-10-08, dev data, Sonnet 4.5)

Agent mode 15/15, latency 5.7–18.0 s, judge groundedness 4.0 · citation use 3.4
· tone 4.6 (1–5). API mode 15/15 through the deployed runtime. Offline 36/36.

## Revisit when

* A second agent is added: add its golden suite first, then its runner in
  `run_eval.py`.
* The judge is trusted enough to gate: set a floor per axis and fail on drop.
