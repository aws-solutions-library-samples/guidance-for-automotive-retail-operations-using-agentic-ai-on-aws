# Architecture decision records

Focused records of decisions that shape the reference implementation. Each
record explains why the code follows its current pattern, the alternatives
considered, the implementation location, consequences, and revisit conditions.
Current capability status belongs in `docs/ACCEPTANCE.md`, not in an ADR.

| # | Decision | Status |
|---|---|---|
| [0002](0002-tools-plus-reasoning-agent.md) | Keep deterministic domain tools and introduce reasoning agents one at a time, promoted on evaluation evidence | Accepted · A3 promoted |
| [0003](0003-model-safety-guardrail.md) | Run every model call behind one managed Bedrock Guardrail in addition to code-level fences; amended 2026-10-22 so the nightly store digest carries its own relaxed-grounding copy | Accepted · deployed · amended |
| [0004](0004-retrieval-knowledge-base.md) | Use a Bedrock Knowledge Base over the S3 corpus with the keyword index as fallback | Accepted · deployed |
| [0005](0005-agentic-jobs-events-autonomy.md) | Drive proactive behavior through scheduled jobs and event subscribers gated by per-action autonomy | Accepted · deployed |
| [0006](0006-evaluation-as-promotion-gate.md) | Measure behavior with a golden-prompt harness before promotion | Accepted · in CI |
| [0007](0007-deployment-approach.md) | Provision with idempotent, dry-run-first Python deployers and direct arm64 AgentCore code deployment | Accepted · deployed |
| [0008](0008-no-public-entry-points.md) | Prohibit public Lambda entry points and carry the SSE floor response through authenticated infrastructure | Accepted · enforced in CI |
| [0009](0009-one-runtime-per-agent.md) | Run each custom agent in its own AgentCore runtime, called with A2A-shaped requests over `InvokeAgentRuntime`, with an in-process fallback | Accepted · deployed (six runtimes) |

The former ADR 0001 was removed because it combined many temporary implementation
gaps into one document and became stale as those gaps closed. Durable customer
memory, connector interfaces, Code Interpreter, and streaming status are now
described in the as-built architecture and acceptance map.

Related reading:

- `docs/as-built/architecture.md` — current components and trust boundaries
- `docs/as-built/agent-flows.md` — current execution paths
- `docs/specs/` — target requirements
- `docs/ACCEPTANCE.md` — current verification status and remaining boundaries
- `docs/DEPLOYMENT.md` — deployment and optional integration setup
