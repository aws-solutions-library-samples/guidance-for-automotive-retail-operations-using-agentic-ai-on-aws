# ADR 0002 — Deterministic tools with a reasoning agent on top

**Status:** Accepted (2026-10-08). Piloted on the A3 Dealer Sales Advisor assistant.

## Context

The specification asks for seven agents, each a Strands agent on Amazon Bedrock
AgentCore, calling one another and keeping memory. The reference build instead
grew a set of **deterministic Python modules** — one function per spec tool —
behind a single router, with Amazon Bedrock used only to phrase answers. That
gave exact, testable behaviour (state machines, pricing, equity math, scope
checks) but nothing that could *plan*: the system was agentic only through
schedules.

## Decision

Keep the deterministic functions as the **tools**. Put a **reasoning agent**
(Strands `Agent` on Bedrock) on top that chooses and sequences those tools, and
introduce it **one agent at a time**, promoting each only when the evaluation
harness (ADR 0006) shows it matches or beats the one-shot path. The first is the
A3 Assistant.

Every fence stays deterministic and runs around the model, not inside it:

| Fence | Where |
|---|---|
| Refuse / escalate credit, legal and safety-recall questions before any model call | `journeys/qa.classify_refusal`, called first in `a3_agent.run` |
| Caller scope — a tool refuses a journey outside the caller's dealers | `check_journey_access` inside every tool (`a3_agent._journey`) |
| Autonomy level — `send_outreach` holds the message for a human below `act_and_notify` | `autonomy_config.active_level("a2.schedule_outreach")` |
| Model-tier guardrail (denied topics, filters, grounding) | ADR 0003, attached to the Strands `BedrockModel` |
| Citations — every `source_ref` a tool returns is collected and returned | per-run context in `a3_agent` |
| Trace — one `PLAT_AgentTraces` row per run with tools used and latency | `common/trace.write_trace` |

## Options considered

1. **Deterministic functions only.** Cheapest; but the use cases that need
   planning (R1/R2 in the spec) are never met, and "agentic" means only
   "scheduled".
2. **Rewrite all seven agents as Strands agents with A2A and Memory**, as the
   spec is written. Weeks of work before any answer improves, and it discards a
   tested tool layer.
3. **Tools + one reasoning agent at a time** *(chosen)*. Reuses all tested code,
   puts the model where it adds value (choosing tools, phrasing), keeps every
   fence deterministic, and makes the upgrade measurable.

## Implementation

```
app/services/agents/a3_agent.py      the agent: TOOL_FUNCS (11 tools), SYSTEM_PROMPT, run()
app/services/api/routes/sales_floor.py   POST /assistant/agent, or /assistant/chat {"mode":"agent"}
app/agentcore/agent.py                runtime entry: payload {"kind":"agent","agent":"A3",...} (ADR 0009)
app/agentcore/client.py               service Lambda -> InvokeAgentRuntime (falls back in-process)
app/infra/deploy_agentcore.py         bundles strands-agents (arm64 wheels) into the A3 runtime zip
app/console/src/app/dealer/assistant/page.tsx   "Agent mode" toggle + engine badge
tests/app/test_a3_agent.py            offline tests (tools, scope, fences, fallback, transport)
app/eval/golden/a3_assistant.json     15 golden cases; app/eval/reports/agent-baseline.json
```

Tools exposed to the agent: `get_lifecycle_context`, `get_recent_activity`,
`get_contextual_suggestions`, `get_incentives`, `get_trade_equity`,
`search_knowledge`, `get_objection_strategy`, `customer_memory`, `draft_follow_up`,
`create_task`, `send_outreach`. Each wraps an existing module function; the `@tool` docstring is
what the model reads.

Model: a Sonnet-class inference profile (`RETAIL_OPS_A3_AGENT_MODEL`, default
`us.anthropic.claude-sonnet-4-5-20250929-v1:0`) so a 3–5-tool run stays inside
the API Gateway budget (service Lambda timeout 29 s). The one-shot phrasing path
keeps `RETAIL_OPS_BEDROCK_MODEL`.

Where it runs: in the Sales Agent's own AgentCore runtime `retail_ops_dev_a3_sales`
(ADR 0009). The service Lambda carries no agent framework; it delegates with
`InvokeAgentRuntime` when `RETAIL_OPS_AGENT_RUNTIMES` names A3 (written by
`deploy_l4` from the deployer's config) and otherwise runs the agent in-process if
Strands is importable, otherwise answers with the one-shot path and says so in
`engine`.

## Consequences

* The Assistant has two engines and tells you which answered (`engine`,
  `tools_used`, `transport`). The Strands path is deployed for Agent mode; the
  one-shot path remains an explicit fallback.
* Adding a tool is adding a function with a docstring to `TOOL_FUNCS`; the
  function must enforce scope itself.
* Next candidates, in order: A7 morning work (outputs are already
  recommendation cards), A2 next-best-action, A6 workspace ask. Each gets a
  golden set first.
* Durable customer context now uses `platform/memory.py`: every memory keeps
  provenance in `PLAT_AgentMemory`, and AWS mode also writes and recalls through
  AgentCore Memory. This enrichment degrades safely to DynamoDB.

## Evidence

Dev data, 2026-10-08: 15/15 golden cases pass in agent mode through the deployed
API; 6–18 s per answer; LLM-judge means groundedness 4.0, citation use 3.4,
tone 4.6 (out of 5). Baseline kept in `app/eval/reports/agent-baseline.json`.

## Revisit when

* Latency must drop below ~5 s (consider streaming or a smaller model).
* A second agent is promoted (factor the per-run context and tool wrapping into
  a shared helper).
