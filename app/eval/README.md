# Agent evaluation harness

Golden prompts per agent with deterministic checks, plus an optional LLM-as-judge
pass. It exists so that the agentic layer (Strands agent, Knowledge Base,
guardrails) can be changed and promoted on evidence instead of a gut feel.

```
app/eval/golden/*.json     the cases (A3 assistant, A2 customer Q&A, A6 workspace ask, A7 explain)
app/eval/run_eval.py       the runner
app/eval/html_report.py    renders a report JSON as one self-contained HTML page
app/eval/reports/          JSON reports (agent-baseline.json is the kept baseline)
```

## Running

```bash
# offline (CI gate): deterministic paths over the in-memory DynamoDB — no AWS
python3 -m app.eval.run_eval --mode offline

# agent mode: every suite against the dev tables with Bedrock on — A3 through the Strands
# agent, A2 customer Q&A and A6 workspace ask through grounded generation, the two supervisor
# suites with the model allowed to re-pick/order (`expect_aws` relaxes `chosen_by` / `decision_engine`)
INTEGRATION_MODE=aws python3 -m app.eval.run_eval --mode agent --judge \
    --baseline app/eval/reports/agent-baseline.json
INTEGRATION_MODE=aws python3 -m app.eval.run_eval --mode agent --only a2_customer_qa a6_workspace_ask

# against a deployed API (POST /assistant/agent)
python3 -m app.eval.run_eval --mode api --api https://<id>.execute-api.us-east-1.amazonaws.com/dev \
    --token "$IDTOKEN"
```

Exit code 1 when the pass rate is below `--min-pass` (1.0 offline, 0.9 otherwise).

```bash
# a readable page from any report (no script, no external asset; opens from disk)
python3 -m app.eval.html_report app/eval/reports/agent-uaef-20261027-150234.json   # -> same path, .html
```
The page shows the harness gate per case and check, the UAEF metric means and
per-case heat map when the report carries `uaef` blocks, the cases UAEF marked
failed with the judge's reasoning, and per case the question, answer, citations,
tools called against the golden `expected_tools`, and the Strands tool-execution
stats. It also flags tool-selection scores that predate the current golden.

## What a case asserts

| key in `expect` | meaning |
|---|---|
| `refused` / `explained` / `forbidden` | the fence (refusal classifier, grounding check, scope) fired or did not |
| `category`, `guardrail_any` | which refusal category / GR rule id fired |
| `min_citations` | at least N citations returned with the answer |
| `forbidden_regex` | none of these may appear in the answer (fabricated prices, "I sent", scare words) |
| `required_regex_any` | (agent/api) at least one phrasing must appear (e.g. "held for approval"; refusal cases name their owner) |
| `numbers_grounded` | (agent; default on, `false` to opt out) every currency amount, day count and percentage in the answer was given to the model this turn — A3's tool results, or the grounding facts the runner captures for A2 Q&A (`qa._gather_facts`) and A6 ask (wrapping `generate.ground`). 1 % tolerance; `$8K` ±500; `88%` matches `0.88`; `54 days` matches a tool timestamp 54 days before the run or a `54d` reason. The deterministic net under the LLM judges, which passed a "typically $130–150 a month" with `payment_delta: None` |
| `one_of` / `expect_aws` | `one_of: {field: [values]}` membership check; `expect_aws` is merged over `expect` in agent/api mode, so a suite can pin `deterministic` offline and allow `bedrock` live |

Suite-level `"uaef_llm": false` keeps the UAEF LLM judges off a suite whose "answer" is a payload, not prose (the two supervisor suites).
| `tools_any` | (agent/api) at least one of these tools was used |
| `max_latency_ms` | (agent/api) default 25 000 — the API Gateway budget |

## UAEF metrics (graded, per A3 case)

`--uaef` layers the [Universal Agent Evaluation Framework](https://github.com/awslabs/uaef)
over the A3 cases in agent/api mode. Where the checks above are pass/fail fences and
`--judge` sees only tool *names*, UAEF scores the **native Strands trace** — every
`toolUse`/`toolResult`, token usage, cycles — so hallucination is judged against what the
tools actually returned. `app/eval/uaef_bridge.py` does the mapping.

```bash
# one-time, opt-in (≈550 MB of judge/integration deps; deliberately not in pyproject)
git clone https://github.com/awslabs/uaef.git ../uaef && pip install -e ../uaef

# deterministic metrics only (no extra Bedrock calls beyond the agent run)
INTEGRATION_MODE=aws python3 -m app.eval.run_eval --mode agent --uaef

# + Bedrock LLM-judge metrics (UAEF_LLM_MODEL_ID, default us.anthropic.claude-sonnet-4-6)
INTEGRATION_MODE=aws python3 -m app.eval.run_eval --mode agent --uaef --uaef-llm

# re-score a retained report without re-running the agent (the report keeps `strands_trace`)
python3 -m app.eval.uaef_bridge --report app/eval/reports/agent-20261027-120000.json --llm
```

| preset | metrics | needs |
|---|---|---|
| deterministic (default) | `tool_selection_accuracy` (Jaccard vs the case's `expected_tools`), `latency_score` (full marks ≤ 10 s, `RETAIL_OPS_EVAL_LATENCY_TARGET_MS`), `prompt_injection_detection` | nothing |
| `--uaef-llm` | `answer_relevance`, `hallucination_score` (context = tool results), `safety_score`, `bias_score`, `toxicity_score` | Bedrock |

Pick your own with `--uaef-metrics a,b,c` (any name from `uaef.metrics.list_metrics()`;
`tool_sequence_correctness` is LCS order-sensitive, so pin a case's order first).

Ground truth: each case's `expected_tools` (ordered) is what the agent *should* call;
`tools_any` stays the gate. Refusal cases declare none, so tool metrics report no score
for them. Scores are reported, not gating; `uaef_means` lands in the report next to
`judge_means` and is compared by `--baseline`.

## Report card (HTML, one tab per agent)

```bash
python3 -m app.eval.report_card --report app/eval/reports/agent-all-<ts>.json \
    --baseline app/eval/reports/agent-all-<earlier>.json      # optional: shows per-metric deltas
```

Self-contained HTML (CSS-only tabs, no scripts): an overview table plus a tab per agent with KPIs,
engines used, the dated found / fixed / open notes from `report_card.NOTES`, and every case with its
fences, UAEF scores, judge groundedness and the full answer behind a disclosure. Numbers come from the
report; only the narrative and grades are hand-maintained.

## Baseline (2026-10-08, dev data, `us.anthropic.claude-sonnet-4-5`)

Agent mode, 15 A3 cases: **15/15 pass**, latency 5.7–18.0 s, judge means
groundedness 4.0 · citation use 3.4 · tone 4.6 (1–5). Offline: 36/36.

Agent-mode runs write real rows in the dev account (a task from `a3-task`, a held
outreach card from `a3-send-held`); that is intended — it exercises the autonomy
gate — but do not point `--mode agent` at anything but a dev prefix.

## Adding a case

Add an object to the suite's `cases`; keep expectations deterministic (regexes,
counts, booleans). If a case needs a new fence, add the fence to the code first —
the harness measures behaviour, it does not define it.
