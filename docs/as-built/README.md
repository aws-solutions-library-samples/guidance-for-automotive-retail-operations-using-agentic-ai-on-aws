# As-built documentation

Bottom-up documentation of the Automotive Retail Operations powered by Agentic AI reference
implementation (orchestration layer: Intelligent Sales & Service Orchestration; see
the name mapping in the root `README.md`), written from the code in `app/` and the deployed
development resources. These documents describe current behavior; documents in
`docs/specs/` describe the target and requirements.

| Document | Read it to… |
|---|---|
| [`architecture.md`](architecture.md) | understand the components, request paths, deployment topology, data flow, and security boundaries |
| [`use-cases.md`](use-cases.md) | understand A1–A7 as they operate today: users, screens, routes, data, model use, and autonomy |
| [`agent-flows.md`](agent-flows.md) | follow state machines, gates, scheduled jobs, event subscribers, guarded answers, approvals, and external-agent entry points |

**One home per fact.** Each kind of information lives in exactly one place, and
the as-built documents link to it rather than restating it:

| Kind of fact | Single home |
|---|---|
| Target requirements and target architecture (what the system should do) | `docs/specs/` — the requirements spec and `retail-ops-architecture.md` (16 target diagrams); `use-cases-summary.md` holds the delivery-model rules and technology rationale |
| Current implementation (what the code does, traced to modules) | this folder |
| How to run, deploy, verify, roll back, tear down | [`docs/DEPLOYMENT.md`](../DEPLOYMENT.md), `deploy/README.md`, the root `README.md` |
| Live resource inventory and demo script for the development account | [`docs/DEMO.md`](../DEMO.md) |
| Verification status, acceptance evidence, remaining boundaries | [`docs/ACCEPTANCE.md`](../ACCEPTANCE.md) |
| Amazon Quick console build steps | [`docs/QUICK_BUILD.md`](../QUICK_BUILD.md) |
| Why a decision was taken | [`docs/adr/`](../adr/README.md) |
| Agent behavior and promotion evidence | [`app/eval/README.md`](../../app/eval/README.md) |

The as-built documents therefore carry no command listings, no live resource
counts or versions, and no status tables; when one of those appears here it is a
defect to fix by replacing it with a link.

Conventions: statements are traceable to a module path, deployer, or verification
command. Personalize and Amazon Connect are labelled as integration boundaries
until configured. AgentCore Memory and Code Interpreter have real AWS paths with
local deterministic fallbacks. DMS, labor, and parts operations use the shared
Connector interface; live endpoints remain environment configuration. Mermaid
diagrams include text fallbacks. Resource names use the technical prefix
`retail-ops` / `retail-ops-dev-`, not a customer-facing product abbreviation.
