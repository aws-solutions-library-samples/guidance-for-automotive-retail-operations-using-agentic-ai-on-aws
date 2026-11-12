# Use Cases Summary

Companion to `intelligent-sales-service-orchestration.md` (Intelligent Sales & Service Orchestration, the orchestration layer of Automotive Retail Operations powered by Agentic AI; "Retail Ops" for short). Consolidated view of the seven use cases (the agents) with description, primary persona, data sources and their owning system, and the implementation stack. Authoritative detail lives in spec §2.2.1 (classification), §2.2.2 (delivery-model selection), §2.6 (feature → tech-stack matrix), and §3 (per-agent tools). Aligned with spec v1.3 (2026-09-23).

Two business processes: **Process 1 — Sales** (retail purchase journey, A1–A6) and **Process 2 — Service & ownership** (service journey, A7).

## Use cases

| # | Use case | Description | Primary persona | Data sources (owner) | Tech stack for implementation |
|---|---|---|---|---|---|
| A1 | Personalized Vehicle Discovery | Turns lifestyle, budget, and browsing history into ranked vehicle / trim / package recommendations with VIN-level availability, price build, incentives, and EV-vs-ICE TCO; re-scores saved builds when inventory or incentives change | Customer (web/app); dealer sales advisor | Configurator / product catalog (OEM); VIN inventory (dealer inventory system); web behaviour / CDP (CDP); pricing & incentives (`OEM_Incentives`, `OEM_StateRules`) | Custom agent on Amazon Bedrock AgentCore (Strands, Memory, Code Interpreter for TCO); Amazon Personalize (ranking, similar-items); Customer Web (Next.js, brand theme) |
| A2 | Purchase Journey Orchestration (supervisor + nurture) | Owns the purchase-journey state machine, validates handoffs, chooses next best action, runs Plays on triggers, and nurtures prospects over time across channels | System (schedules / events); customer (chat / email / SMS); dealer; OEM CX | CRM (Salesforce); CDP; configurator; F&I (CDK); trade valuation (Cox / KBB); DMS (CDK); journey state (`DEALER_Journeys`, `DEALER_NurturePlans`) | Custom agent on AgentCore (supervisor, AgentCore Memory); EventBridge + Scheduler; AgentCore Gateway → Quick for A3/A6 content; SES / SNS (Amazon Connect optional) |
| A3 | Dealer Sales Advisor | Contextual suggestions by lifecycle stage, cited Q&A, live-floor objection detection, deal structuring, and the service-lane upsell brief | Dealer sales rep, BDC agent, service advisor; OEM retail marketing | CRM; DMS; OEM incentive feeds; digital-retail & lead data; Quick Index (CRM notes, incentive bulletins, product guides) | **Custom agent on Amazon Bedrock AgentCore** — one agent for the streaming floor path and the assistant chat/flows; uses **Quick Index as a retrieval tool** (not a Quick-hosted agent); Amazon Transcribe / Polly; Next.js Assistant screen. *(Reframed 2026-09-23; was split Quick + AgentCore.)* **As built (2026-10-08):** the Assistant runs as a Strands agent on Amazon Bedrock (AgentCore Runtime) planning over the deterministic A3 tools, with a Bedrock Knowledge Base for retrieval and a Bedrock Guardrail attached; the floor path is request/response today, not streaming. |
| A4 | Trade-In & Upgrade Advisor | Valuation, equity, and payment delta; continuous equity monitoring on service events; upgrade-timing and used-vehicle acquisition recommendations | Dealer sales advisor, used-car / inventory manager; customer | DMS + service history (dealer); F&I (CDK); trade valuation APIs (Cox / KBB / Black Book); `DEALER_Vehicles`, `DEALER_Valuations` | Custom agent on AgentCore (Code Interpreter for equity / payment math); Next.js Trade Desk read models |
| A5 | Campaign ROI Optimizer | Attributes ad spend to delivered vehicles, computes ROI by channel / creative / dealer, drafts budget-shift approvals nightly, and refreshes audiences | OEM marketing analyst; OEM finance; dealer principal | Ad platforms (Adobe / Google / Meta); CDP; journey-event lake (S3); DMS deliveries; Athena over the lake | **AWS Quick / QuickSight** (dashboards, Q topics) + AgentCore for the recommendation step; Amazon Personalize (user segmentation) |
| A6 | OEM Sales Administrator | Generates account plans, opportunity briefs, win strategies, and alerts; runs a weekly district cycle; guardrailed district-manager chat | OEM district / regional manager; C&I planner; sales ops; finance | CRM; RDR / sales data lake; C&I incentive system; market intelligence (S&P Global Mobility, R.L. Polk); Quick Index (contact reports, comms, prior plans) | **AWS Quick** (Quick Research + Flows + Index over CRM / lake); Bedrock Guardrails (GR-001…008); exposed to A2 via AgentCore Gateway |
| A7 | Service Operations (service-journey supervisor) | Owns the service journey; morning work (dispatch, parts staging, briefs), inspection triage, sourced estimates, customer approval, promise re-forecast, declined-work follow-up, predictive-maintenance leads, hourly due-service reminders, and the repurchase/upgrade handoff (service → next sale) | Service advisor, technician, parts counter, service manager; customer (My Garage) | DMS (RO, service history); scheduling (Xtime / myKaarma); inspection / MPI tool; DTC / repair-procedure DB; labor-time guide; parts catalog + inventory; warranty / recall (OEM); connected-car telematics (optional) | Custom agent on AgentCore (service state machine, Code Interpreter for estimates); Quick Index for procedures / TSBs; Next.js Service Desk read models + technician tablet; Customer Web for approval |

## Data-source owners

Every external system is reached through one connector interface (`SandboxConnector` = DynamoDB-seeded; `LiveConnector` = real API, credentials in Secrets Manager); selected by `INTEGRATION_MODE`. See spec §4.1.

| Owner | Systems |
|---|---|
| OEM | Configurator / product catalog, OEM incentive feeds, warranty / recall, RDR / sales data lake, C&I incentive system, connected-car telematics, MPI / DTC / labor references (via OEM service info) |
| Dealer (via ISVs) | CRM (Salesforce), DMS + F&I (CDK / Reynolds), inventory, scheduling (Xtime / myKaarma), parts catalog + inventory |
| Third-party (licensed) | Trade valuation (Cox vAuto / KBB, Black Book), ad platforms (Adobe / Google / Meta), market intelligence (S&P Global Mobility, R.L. Polk) |
| Solution-owned operational stores | 67 owner-prefixed DynamoDB tables (`OEM_*` / `DEALER_*` / `CUST_*` / `PLAT_*`); S3 event lake (Firehose → Glue → Athena); Amazon Personalize datasets; Quick Index; AgentCore Memory |

## Delivery-model selection (why the stack differs per use case)

The stack is chosen per use case by the rules in spec §2.2.2 (first matching rule wins), not one-size-fits-all:

| Rule | If the use case needs… | Delivery model | Use cases |
|---|---|---|---|
| R1 | Stateful, long-running orchestration over a journey | Custom agent on Amazon Bedrock AgentCore | A1, A2, A4, A7 |
| R2 | Sub-2 s streaming on a live conversation | Custom agent on AgentCore (streaming path) | A3 (whole agent) |
| R3 | Document generation and cited Q&A for a knowledge worker (no streaming or shared state) | AWS Quick (agent, Flows, Index, Research) | A6. *(A3 uses Quick Index as a retrieval tool but is a custom agent, since it also needs R2 streaming.)* |
| R4 | Analytics, dashboards, natural-language questions on the lake | AWS Quick / QuickSight; AgentCore for the recommendation step | A5 |
| R5 | Deterministic boards, queues, worksheets, approvals | Next.js + Lambda read models (agents pre-compute) | Cockpits, desks, boards |
| R6 | Customer self-service on brand web / mobile | Customer Web on the same AgentCore agents | Customer surfaces for A1, A2, A7 |
| R7 | Voice or telephony as a channel | `OutreachProvider` (SES / SNS default; Amazon Connect optional) | A2, A7 outreach |

## Technology rationale (why each stack)

The package is deliberately multi-stack. Each technology is chosen for what it is best at; the rationale below is the "why" behind the rules above.

| Technology | Chosen when | Why (what only it gives you) | Not used when | Used by |
|---|---|---|---|---|
| **Custom agent on Amazon Bedrock AgentCore** | The use case owns a state machine, runs long, and calls operational systems on events/schedules | Durable per-customer Memory, agent-to-agent calls, sandboxed Code Interpreter for auditable math, event/schedule triggers, and the per-action autonomy check around every effectful tool | The work is pure document generation or analytics with no state and no streaming (that is Quick's job) | A1, A2, A3, A4, A7 |
| **AgentCore real-time path** | Sub-2 s streaming on a live conversation (objection detection on a live transcript) | Streaming token-by-token inside a 2-second budget while the advisor is talking | The response can be request/response (then it is just the same agent's non-streaming path) | A3 (floor half of the one agent) |
| **AWS Quick** (agent + Flows + Index + Research) | Document-style generation and cited Q&A for a knowledge worker who lives in chat/documents **and needs neither streaming nor shared journey state** | Semantic index, citations, multi-step document Flows, and external research out of the box | The same persona also needs streaming or shared state — then R1/R2 win and it is a custom agent (this is what moved A3 off Quick) | A6 |
| **AWS Quick / QuickSight** (analytics) | Dashboards and natural-language questions over the S3 lake | Visualization and Q topics on analytics — QuickSight's core competence (not row-scoped here: access by Quick group, decision 2026-10-21) | The step must be traceable, guardrailed, and routed to approval — that recommendation step is agent work | A5 dashboards; Store/Service Cockpit tiles |
| **Amazon Bedrock (models) + Guardrails** | Every generative step, under both stacks | Claude Sonnet family via Strands for generation; Guardrails enforce PII, off-topic, scare-language, and GR-001…008 OEM policy | — (it is the model tier, not a separate runtime; AgentCore is the runtime around it, Quick uses its own hosted models) | A1–A5, A7 (A6 via Quick's models) |
| **Quick Index as a retrieval tool** (consumed by AgentCore agents) | A custom agent needs grounded, cited answers over indexed documents | One shared index and one set of citations without standing up a second (Quick-hosted) runtime | The whole use case is document work with no state/streaming (then it is a Quick agent, i.e. A6) | A3 (CRM notes, bulletins, guides), A7 (repair procedures, TSBs) |
| **Next.js + Lambda read models** | Deterministic boards, queues, worksheets, approval screens | Millisecond render and auditability; no agent in the request path | The screen genuinely needs live generation (rare; then it calls an agent asynchronously) | Cockpits, F&I Desk, Lead Desk, Service Desk, Journeys, Delivery Board |
| **Customer Web (brand theme) on the same agents** | Anything a customer touches (shop, chat, approve, book) | The brand surface; the agent behind it is the same one staff use, so what the customer approves is what the advisor presented | Staff-only tools (they can use Quick or the console directly) | Customer surfaces of A1, A2, A7 (My Garage) |
| **OutreachProvider** (SES/SNS; Amazon Connect optional) | Voice / SMS / email / telephony as a channel | Channel behind an interface, so enabling Connect changes no agent or UI code | — | A2, A7 outreach |

**Three principles that drive every choice**

1. **First matching rule wins.** A use case with two needs resolves to the higher rule and turns the lower need into a *tool*, not a second runtime. A3 needs streaming (R2) and documents (R3), so it is a custom agent that uses Quick Index as a tool.
2. **One suggestion engine per domain.** The floor path and the assistant are the same agent so advice cannot diverge; Quick Index is shared so citations stay consistent.
3. **Compose, don't fragment.** One sign-in, one shell, one event bus, one AgentCore Gateway bridge, one `correlation_id` trace, and market/autonomy as configuration — so a multi-stack solution stays one system.

**Anti-patterns the rationale rejects** (each is the inverse of a rule): A6's document workbench as a custom agent (re-implements Quick); a streaming floor path inside Quick (cannot stream); splitting one persona's agent across two runtimes when they share state (the A3 correction, §13); Quick in front of customers (brand and licensing); an agent computing dashboard tiles at page load (latency, cost, non-determinism); one global autonomy switch (dealers need per-action control).

## Notes

- **As built vs target:** this summary describes the target delivery model. Current behavior by use case is documented in `docs/as-built/use-cases.md`; verification status and remaining environment dependencies are maintained in `docs/ACCEPTANCE.md`.

- **A3 is one custom AgentCore agent** (reframed 2026-09-23, spec §13): the streaming floor path and the assistant chat/flows are the same agent, so a suggestion and its live-floor delivery cannot diverge. It uses Quick **Index** as a retrieval tool for grounding, the same way A7 uses it for procedures/TSBs; it is not a Quick-hosted agent. Quick-hosted agents/flows apply to A6 only.
- **A6's "customers" are dealers as accounts,** not consumers; guardrails GR-001…008 constrain what its output may say to or about a franchised dealer.
- **Two supervisors, not one:** A2 owns the purchase journey, A7 owns the service journey; they meet through events, never by driving each other.
- **KPI baselines** marked *estimate* in spec §1.3 (lead close, presentation rate, approval-when-presented, declined-work conversion) come from vendor / trade sources and are re-baselined with the pilot dealer's own data in Phase 0.
