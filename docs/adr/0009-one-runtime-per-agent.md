# ADR 0009 — One AgentCore runtime per custom agent

**Status:** Accepted (2026-10-20). Deployed: the six runtimes are `READY` in the
development account (created with `deploy/deploy.sh --apply --only alarms,agentcore,l4,l6`).
Amended 2026-10-22: a ninth agent-owned job, `predictive-scan` (Service Agent).

## Context

Until this change every custom agent shared one AgentCore runtime,
`retail_ops_agent`. Its entry point was a generic router: the service Lambda
could forward any request to it, but the runtime held the whole `app/` tree, one
environment, one version history and one log group. Only A3's reasoning turn
actually went there; the Sales Package, the A5 budget-shift step and the agents'
scheduled jobs ran in the service and job Lambdas.

The user asked for each agent to be independent. Spec §2.2.2 already names A1,
A2, A3, A4, A7 and A5's recommendation step as custom AgentCore agents, and
composition rule 4 describes A2 invoking A1, A3 and A4 as separate agents.

## Decision

* **D1 — six runtimes.** A1 `retail_ops_dev_a1_discovery`, A2
  `retail_ops_dev_a2_journey`, A3 `retail_ops_dev_a3_sales`, A4
  `retail_ops_dev_a4_trade_in`, A5 `retail_ops_dev_a5_campaign` (recommendation
  step only) and A7 `retail_ops_dev_a7_service`. Names follow
  `retail_ops_<env>_<code>_<slug>`. The Field Sales Agent (A6) stays in Amazon
  Quick and the Gateway stays the only path from Quick to OEM data.
* **D2 — deterministic tool agents at launch.** A3 keeps its Strands reasoning
  agent (ADR 0002). The others serve the same tested functions as tools and
  jobs, and each is promoted to Strands on its own when its golden suite shows
  parity (ADR 0006): A7 morning work first, then A2 next-best-action.
* **D3 — A2A-shaped requests over `InvokeAgentRuntime`.** A2 calls A1, A3 and A4
  in parallel for the Sales Package with a typed request (`kind: tool | agent |
  job`, caller claims forwarded, `correlation_id`, one self-citing result) over
  the HTTP protocol. The A2A wire protocol is a later per-runtime option. A2
  and A7 never call each other's runtime; they meet through events.
* **D4 — one owner per route and tool; one bundle per agent.** The agent
  registry (`app/agentcore/registry.py`) names each agent's tools, jobs and
  reasoning entry. Each bundle holds the static import closure of its own
  agent's code plus the runtime core, with dependency profile `tools` (boto3)
  or `strands` (A3).
* **D5 — one shared execution role (user decision).**
  `AmazonBedrockAgentCore-retail-ops-exec` keeps every statement of the old
  runtime role and adds `Outreach` (SES/SNS), one workload-identity entry per
  runtime name, and `InvokeAgentRuntime` on `runtime/retail_ops_dev_*`. No
  per-agent IAM roles. User-facing access controls are unchanged.
* **D6 — routing with an in-process fallback.** The service and job Lambdas read
  the JSON map `RETAIL_OPS_AGENT_RUNTIMES` (`{"A1": "arn…", …}`). Only agent
  work goes to a runtime: the A3 agent turn, the Sales Package, the two A5
  budget-shift POSTs and the nine agent-owned jobs. Every other route stays in
  the service Lambda. With no ARN configured, or with
  `RETAIL_OPS_AGENT_RUNTIME_DISABLE` set, the same function runs in-process. A
  call that was sent but not answered (`lost`) never starts a second effectful
  run. The three Bedrock helpers that ignored the mode (`equity._explain`,
  `discovery._rationale`, `campaigns._insight`) now return templated text unless
  `INTEGRATION_MODE=aws`.
* **D7 — AgentCore Memory: one shared resource as the target, not activated.**
  Runtimes keep using `PLAT_AgentMemory`; `RETAIL_OPS_MEMORY_ID` is never set on
  a runtime.
* **D8 — one registry-driven deployer.** `deploy_agentcore.py` plans and deploys
  every runtime from the registry, dry run by default, A2 last (it needs the
  others' ARNs). `deploy_l4` and `deploy_l6` wire and preserve the runtime map
  and the disable switch; teardown matches exact names only.
* **D9 — side-by-side migration.** Create the six runtimes next to the legacy
  one, rewire, verify, soak, then delete the legacy runtime with an explicit
  `--delete-legacy --apply`. The console gains a "Runs on" column in Admin ›
  Agents and an " · AgentCore" suffix on the Assistant engine badge.

## Options considered

* **One runtime with per-agent endpoints.** Smallest change, but endpoints share
  one bundle, environment and version history, so agents still cannot be
  deployed or rolled back on their own.
* **Five runtimes, A5's step in the service Lambda; or A5 co-hosted with A2.**
  Fewer runtimes, but the first leaves A5 as the only custom agent without its
  own identity, and the second mixes the OEM marketing persona into the dealer
  purchase supervisor.
* **A Strands reasoning agent in every runtime now.** Literal reading of spec §3,
  but five new prompts without golden suites, 6–18 s per turn, and model spend
  on every Sales Package and nightly job. Rejected by ADR 0006's promotion rule.
* **The A2A wire protocol (`serverProtocol: A2A`).** Spec-literal and
  interoperable, but a runtime has one protocol, so the service Lambda would also
  have to speak JSON-RPC, and every bundle would need an A2A server stack.
* **AgentCore Gateway MCP targets per agent; EventBridge for every call.** The
  first adds a target and a hop per agent; the second cannot return a Sales
  Package to a waiting caller. Events stay the channel between the supervisors.
* **Per-agent roles (exact table ARNs per bundle) or single-writer data
  ownership.** Least privilege per agent, but out of scope by user decision.
* **Whole `app/` in every bundle.** Simplest packaging, but each runtime could
  then run any agent's code.
* **Update `retail_ops_agent` in place, or delete it first.** A runtime name
  cannot change, and deleting first means an outage of agent mode.

## Implementation

```
app/agentcore/registry.py        agents, tools, jobs, payload/claims validation, runtime names (--names)
app/agentcore/agent.py           runtime entry: dispatches kind tool | agent | job for RETAIL_OPS_AGENT_ID
app/agentcore/client.py          RETAIL_OPS_AGENT_RUNTIMES resolution, invoke_tool / invoke_job / invoke_agent, call_or_local
app/agentcore/tools/a1..a7.py    thin per-agent adapters over the existing service functions
app/agentcore/bundle.py          per-agent import closure, reproducible zip, BUILD_INFO.json, aws_calls()
app/services/platform/mode.py    integration_mode() and override(); jobs run under override("local")
app/services/journeys/package.py Sales Package: A1/A3/A4 + next-best-action in parallel when remote
app/services/api/handler.py      POST /journeys/{id}/package, POST /oem/campaigns/budget-shift, POST /jobs/{job}
app/services/api/routes/marketing_oem.py   POST /marketing/campaigns/budget-shift/submit
app/services/api/job_handler.py  nine agent-owned jobs delegated to the owner runtime (kind: job)
app/services/admin/agents_health.py        runtime {mode, engine, runtime_calls, fallbacks} per agent
app/infra/deploy_agentcore.py    registry-driven plan / apply, --invoke, --delete, --delete-legacy
app/infra/deploy_l4.py, deploy_l6.py       wire RETAIL_OPS_AGENT_RUNTIMES; --disable-agents / --enable-agents
app/infra/deploy_alarms.py       runtime_job_unknown metric filters and alarms (one per agent job Lambda)
app/infra/iam_roles.py           worker role: InvokeAgentRuntime on runtime/retail_ops_dev_*
app/infra/teardown.py            exact runtime names only
deploy/deploy.sh, deploy/verify.sh         agentcore stage re-runs l4 and l6; verify checks six READY + pings
requirements-agentcore-tools.txt dependency profile for the tool agents
app/console/src/app/admin/agents/page.tsx, dealer/assistant/page.tsx   "Runs on" column, badge suffix
```

## Consequences

* **Cost.** No fixed fee per runtime. Runtime consumption is about $3/month at
  the assumed pilot volume, against about $4.4/month for the single runtime
  today; A3 model spend stays about $300/month; gating the three helpers saves
  about $50/month of discarded model text in the nightly `equity-monitor` job;
  the nine `runtime_job_unknown` alarms cost about $0.90/month. These are
  estimates; Bedrock token prices were not re-verified.
* **One shared execution role, by user decision.** Any runtime can reach every
  `retail-ops-dev-*` table and send email and SMS, as the service and worker
  Lambdas can today. A defect or compromise in one agent's runtime therefore has
  the blast radius of all six. A static test maps every AWS call any bundle can
  make to a statement of the role, so a new call fails a test rather than a
  production request.
* **SES/SNS on the role closes an existing gap.** `nurture-tick` and
  `service-followup-tick` keep sending from the A2 and A7 runtimes, and A3's
  `send_outreach` can now deliver at an execute autonomy level; before, the old
  runtime role had no SES/SNS and the provider turned the message into a dealer
  task.
* **Spec R1 is met for Runtime and Gateway, not for AgentCore Memory or Code
  Interpreter.** A1, A2, A3, A4 and A7 each have a runtime and the Gateway is
  unchanged. No role has the Memory actions, so the runtimes keep the
  `PLAT_AgentMemory` path, and `platform/compute.py` has no production caller and
  the role has no Code Interpreter actions. Strands is met for A3 only. These
  gaps existed before this change.
* **Model calls in jobs.** The three helpers now follow the mode, so a job (in a
  runtime or a Lambda) makes no model call, and a local run with AWS credentials
  gets templated text from them, consistent with `generate.enabled()`.
* **New 202 statuses.** `POST /journeys/{id}/package` returns `202
  {status: "building", requested_at, poll, correlation_id}` and `POST
  /jobs/{job}` returns `202 {status: "running", …}` when the runtime was sent the
  request but did not answer in time. The console calls neither route; the demo
  loop polls `GET /journeys/{id}/package` until `generated_at >= requested_at`.
  A scheduled job whose call is `lost` returns `unknown_after_send` instead of
  raising, so Lambda's async retry cannot start an overlapping run, and prints
  one `runtime_job_unknown` line that the job's alarm counts.
* **Trace tagging.** Rows a runtime writes carry `transport:
  agentcore-runtime` (the dispatcher's `runtime.<tool>` and `runtime.job.<job>`
  spans, and A3's `assistant_agent` row); a configured call that fell back writes
  one `runtime.fallback` span with `transport: in-process-fallback`. Admin ›
  Agents counts both and leaves fallback spans out of invocations, errors and
  latency. A3 still writes one `PLAT_AgentTraces` row per reasoning run.
* **Fences unchanged.** The refusal classifier runs first, every journey tool
  calls `check_journey_access`, autonomy levels and the Bedrock Guardrail apply as
  before (ADR 0002, ADR 0003), and the runtime re-validates the arguments and
  claims it receives.
* **Rollback.** `python3 -m app.infra.deploy_l4 --disable-agents <codes|all>
  --apply` and `python3 -m app.infra.deploy_l6 --disable-agents <codes|all>
  --apply` route calls back in-process at once; `--enable-agents` reverses it.
  Disabling A2 runs the whole Sales Package in-process.
* **Migration is operator work.** Steps 2 to 7 are not done by this change:
  create the runtimes and rewire (`deploy/deploy.sh --apply --only
  alarms,agentcore,l4,l6`), verify (`deploy/verify.sh`), gate on Sales Package
  latency, the A3 eval and the job alarms, soak for a week, delete the legacy
  runtime with `python3 -m app.infra.deploy_agentcore --delete-legacy --apply
  --account <id>`, then remove the legacy reader code.

## Revisit when

* An external client needs the A2A wire protocol (switch that runtime to
  `serverProtocol: A2A`).
* A second agent is promoted to Strands reasoning.
* The Sales Package p95 regresses by more than 2 s against the in-process
  baseline.
* The user asks for per-agent roles or per-agent table scoping.
