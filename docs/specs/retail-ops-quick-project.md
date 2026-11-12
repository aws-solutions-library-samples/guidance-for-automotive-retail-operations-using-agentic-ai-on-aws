# Retail Ops Quick Project — Amazon Quick-native scope (builds outside this codebase)

Companion to `intelligent-sales-service-orchestration.md` (the main spec, v1.3, the source of truth) and `use-cases-summary.md`. This document carves out the parts of Intelligent Sales & Service Orchestration that are **built and configured inside the Amazon Quick environment** (Quick Suite agents, Quick Flows, Quick Index, Quick Research, QuickSight) rather than in the main Next.js + AgentCore + DynamoDB repository.

**Why a separate spec.** The main baseline codebase is Strands + AgentCore agents, Lambda/API Gateway, Next.js UI, and DynamoDB. The Quick-native pieces are authored in the Amazon Quick console/admin surfaces (Quick agents, Flows, Index collections, QuickSight datasets/dashboards, sharing groups), deployed and versioned on a different lifecycle, and owned by a different build track. Separating them keeps each project's build, permissions, and delivery independent while the two integrate through one contract (the AgentCore Gateway → Quick bridge).

> **Naming.** The product is *Automotive Retail Operations powered by Agentic AI*; "Retail Ops" is its short form and the stem of every technical name (`retail-ops-…` resources, `RETAIL_OPS_…` settings). Agents are shown to users by name, never by code. The technical namespace uses data-owner prefixes (`OEM_*` / `DEALER_*` / `CUST_*` / `PLAT_*` tables, `app-*` infra); `IRPO_` / `ISSO_` / `SSO_` are retired.

> **Authority.** Where this file and the main spec disagree, the main spec wins. Section references in brackets (e.g. §3.6) point to the main spec. This file is a scoped extract plus the integration contract; it does not add product scope.

> **Concrete handles.** This spec names dependencies by their **bare** table names (`OEM_Accounts`) and describes the integration contract in the abstract. For the actual **resolvable handles** a Quick builder connects to — the `retail-ops-dev-`prefixed table names + key schemas, the AgentCore runtime ARN and its invoke contract, the QuickSight embed-user ARN, Cognito/IAM identifiers, and IAM roles — see **`docs/QUICK_BUILD.md` Appendix A** (the live deployed-handles reference). The S3 event lake (§4) and the AgentCore Gateway cross-persona bridge (§6) are now **deployed** — see those sections for the live handles and the enforced persona boundary.

---

## 1. Scope

**In this project (Quick-native):**

| Piece | What it is | Main-spec ref |
|---|---|---|
| **Retail Ops Field Sales Agent** | The only fully Quick-hosted agent. Quick agent (planner Q&A), Quick Flows (Account Plan, Opportunity Brief, Win Strategy, Action Plan, Contact Report, Meeting Package, District Roll-up, Sales Ops Digest), Quick Research (external account research), and the 15-view OEM Field Sales Workspace. | §3.6, §3.6.1, §5.9 |
| **A5 — Campaign ROI (analytics half)** | QuickSight dashboards, datasets, and Q topics; natural-language `ask_quick`. (A5's *recommendation* step runs on AgentCore in the main repo and is out of scope here — see §5.) | §3.5, §5.7 |
| **Quick Index** | Shared semantic index over CRM notes/activities, DMS service-history summaries, OEM incentive bulletins, product/trim guides, C&I program docs, market-intel reports, prior account plans, and repair procedures/TSBs. Consumed by the A6 Quick agent **and** by the A3/A7 AgentCore agents as a retrieval tool. | §4.3.1 |
| **Quick Research** | A6 external account research over allow-listed domains, every finding cited. | §4.3.1 |
| **QuickSight** | A5 dashboards, A6 roll-up visuals; registered-user embedding into the Next.js consoles. | §4.3.1 |
| **DM guardrail policy (GR-001…008)** | Enforced before any A6 answer or artifact is returned. | §3.6.1 |
| **AgentCore Gateway → Quick bridge** | The contract by which A2 calls the A6 Quick agent/Flows as tools, and A3/A7 query Quick Index (`retrieve_context`). | §4.3.1 |

**Out of scope (built in the main repo, integrated via the bridge):**

- A1, A2, A3, A4, A7 — custom AgentCore agents. A3 and A7 **consume Quick Index** as a tool but are not Quick-hosted.
- A5's recommendation step (`recommend_budget_shift`, `run_nightly_attribution` writing `DEALER_Approvals`) — AgentCore.
- The Next.js OEM Workspace **shell** (routing, auth, chrome) that *embeds* Quick — main repo; the embedded Quick agent/Flows/QuickSight content is this project.
- All owner-prefixed DynamoDB tables (`OEM_*` / `DEALER_*` / `CUST_*` / `PLAT_*`) and the S3 event lake (owned by the main repo / seeded by the synthetic-data generator). This project *reads* them via Athena/connectors.

---

## 2. Retail Ops Field Sales Agent (Quick-hosted)  [→ §3.6]

**Purpose.** Give OEM field sales (district/regional managers) and C&I planners a generated, sourced plan per dealer or fleet account, plus the daily field toolkit (contact reports, action plans, alerts, scenario planning, meeting prep). A6 works at the **account** level (dealer, dealer group, fleet/commercial) and feeds A2 when a consumer journey belongs to a strategic account.

**Primary persona.** District Manager (owns 8–15 dealers, weekly visits; measured on objective achievement, market share, days supply, CSI, EV readiness; bound by franchise-law/policy constraints). Secondary: Regional Sales Manager, C&I planner, Sales Ops comms, OEM finance.

**Account model.** `Account { account_id, type (dealer|dealer_group|fleet|commercial), region, district, tier, pma, targets{...}, actuals(RDR MTD/YTD/PY), composite{...}, incentive_programs_enrolled[], inventory_position, competitors_in_pma[], ev_readiness{...}, compliance{...}, contacts[], open_opportunities[], active_action_plans[] }` stored in `OEM_Accounts` (deployed as `retail-ops-dev-OEM_Accounts`, PK `account_id`; all table names carry the `retail-ops-dev-` env prefix in the live account — see `docs/QUICK_BUILD.md` Appendix A1); RDR actuals + registrations from the sales data lake via Athena. [→ §3.6] *(The journey-event lake is deployed — Athena `retail_ops_dev_lake.journey_events`, see §4; RDR/registration feeds beyond journey events remain synthetic-seeded in the DynamoDB tables.)*

### 2.1 A6 tools  [→ §3.6]

Data/analysis: `get_account` / `list_accounts` (CRM), `get_rdr_actuals` (lake/Athena), `get_market_registrations` / `get_market_forecast` (S&P Global Mobility / R.L. Polk), `get_incentive_programs` / `simulate_incentive_mix` (C&I), `get_inventory_position` (inventory connector), `compute_composite`, `analyze_performance_gaps`, `detect_alerts` (→ `OEM_Alerts`), `predict_weekend`, `get_competitive_offers`, `get_ev_readiness`, `get_compliance`, `simulate_scenario` (deterministic elasticity model + Bedrock narrative; labeled *estimate*; GR-001/GR-005 checked), `research_account` (Quick Research).

Generation (Quick Flows, each versioned in `OEM_AccountPlans` unless noted): `generate_account_plan`, `generate_opportunity_brief`, `generate_win_strategy`, `generate_action_plan`, `create_contact_report` (Quick Flow + Transcribe dictation → structured report → CRM activity + `DEALER_Tasks`), `build_meeting_package` (→ PPTX/PDF in S3).

Comms/knowledge: `search_comms` / `consolidate_comms` / `forward_comms` (Quick Index over Sales Ops bulletins + SES), `rank_reports` (Quick Index over the report catalog), `publish_plan`.

Chat + safety: `ask(question, scope)` (the right-hand chat panel; Quick agent over Quick Index; every answer cited and guardrail-checked), `check_guardrails(text, context)` (→ `OEM_GuardrailEvents`).

Proactive (§4.10.2): `run_district_cycle(district_id)` — weekly Mon 05:00 local + on `rdr.updated`; refreshes composites, runs `detect_alerts`, drafts action plans for dealers below threshold, assembles meeting packages for the DM's next-7-day visits, rolls up to Region View — everything as **drafts for review** (`act_with_approval`). `track_milestones()` — daily; raises `action_plan.milestone_due` / overdue tasks and drafts the follow-up note.

**Proactive stance.** A6 prepares the week (alerts triaged, plans drafted, packages built, milestones chased); the human reviews and publishes. A6 never publishes, never contacts a dealer, and never touches allocations on its own.

### 2.2 A6 outputs (Quick Flows)  [→ §3.6]

Account Plan (quarterly + on request), Opportunity Brief (on opportunity creation), Win Strategy (on demand / pre-meeting), Action Plan (from alerts/contact reports/request), Contact Report (per contact), Meeting Package (per meeting), District Roll-up / District Pulse (daily/weekly), Sales Ops Digest (weekly/on demand). Each figure cites its source (RDR row, Polk dataset, C&I program id, composite input, research URL).

### 2.3 DM guardrail policy — GR-001…008  [→ §3.6.1]

Enforced on **both** the user query and the generated output before anything is returned. The eight rules and what each blocks are defined once, in the main spec §3.6.1 (table "GR-001 … GR-008"); this project applies them unchanged. Implementation on the Quick side: Bedrock Guardrails (denied topics) + a rules Lambda (GR-001/GR-005 pattern matching on inventory/allocation language). Blocks logged to `OEM_GuardrailEvents` (rule id, query hash, user, timestamp). Blocked answers return a safe alternative.

---

## 3. OEM Field Sales Workspace — 15 views  [→ §5.9]

Light theme (reference `docs/use-cases/DM_Assistant_Demo.html`). Fixed 56px brand header ("District Manager Assistant"), 220px slate sidebar (grouped nav), scrolling center, persistent 340px right **AI chat panel** (mic + "Ask about dealers, performance, strategy…"). Every view opens with a **KPI grid (4 cards)** and includes ≥1 green **[AI INSIGHT]** card. OEM-only (`oem_sales_ops`, `oem_ci_planning`, `oem_finance`, `oem_dm`, `oem_regional`). Routes under `/dashboard/oem/*`.

| Nav group | Route | View | A6 capability / Quick component |
|---|---|---|---|
| Dashboard | `/oem/today` | Today | `district_pulse` Flow + QuickSight KPIs |
| Dashboard | `/oem/performance` | Dealer Performance | `get_rdr_actuals` + `analyze_performance_gaps` |
| Dashboard | `/oem/composite` | Composite Analysis | `compute_composite` |
| Field Tools | `/oem/contacts` | Contact Reports | `create_contact_report` Flow (dictation → structured report → CRM + tasks) |
| Field Tools | `/oem/action-plans` | Action Plans | `generate_action_plan` Flow; `OEM_AccountPlans` type `action_plan` |
| Field Tools | `/oem/alerts` | AI Alerts | `detect_alerts` (HIGH/MEDIUM/LOW) |
| Field Tools | `/oem/weekend` | Weekend Tracker | `predict_weekend(district)` |
| Intelligence | `/oem/conquest` | Conquest Intel | `get_competitive_offers(pma)` |
| Intelligence | `/oem/ev-readiness` | EV Readiness | `get_ev_readiness(account)` |
| Intelligence | `/oem/compliance` | Compliance | `get_compliance(account)` |
| Intelligence | `/oem/scenario` | Scenario Planning | `simulate_scenario(levers)` (estimate; GR-001/005 checked) |
| Intelligence | `/oem/meeting-box` | Meeting in a Box | `build_meeting_package(type, scope)` → PPTX/PDF |
| Admin | `/oem/comms` | Sales Ops Comms | Quick Index over C&I bulletins + `consolidate_comms` |
| Admin | `/oem/reports` | Report Library | Quick Index ranking (`rank_reports`) |
| Admin | `/oem/guardrails` | AI Guardrails | Bedrock Guardrails + policy layer; GR-001…008 |
| Accounts | `/oem/accounts`, `/oem/accounts/{id}` | Account Plans | Account list + detail tabs (Account Plan, Opportunity Briefs, Win Strategy, Action Plans, Contact Reports); embedded QuickSight *District Sales vs Target* |

**Right-hand AI chat (all views).** A6 `ask` scoped to the current view + selected dealer; voice input; answers cite sources and show a shield icon when a guardrail rewrote the answer. Suggested prompts change per view.

**Design tokens (OEM workspace theme).** `--oem-header` (navy `#002C5F`), `--oem-sidebar` (`#1a2332`), `--oem-sidebar-text`, `--oem-accent` (cyan `#00aad2`), `--oem-kpi`, `--oem-up: #2e7d32`, `--oem-down: #c62828`, `--oem-insight-bg: #f0fff4`, `--oem-insight-border: #c8e6c9`; radius 8px; card shadow `0 1px 3px rgba(0,0,0,.08)`. Each OEM overrides via `OEM_Markets`/brand config. Dealer console (dark) and OEM workspace (light) share component primitives but not palettes. The Next.js shell hosting these views is main-repo; the embedded Quick content is this project.

---

## 4. A5 — Campaign ROI, analytics half (Quick/QuickSight)  [→ §3.5, §5.7]

**Purpose.** Attribute ad spend to delivered vehicles, compute ROI per channel/creative/dealer, surface funnel drop-off, and answer natural-language analytics questions — the visualization and NL-analytics layer.

**Quick-native tools:** `get_journey_funnel(range, dims)` (Athena/Quick over the S3 lake), `create_quick_dashboard(spec)` / `ask_quick(question)` (Amazon Quick APIs), plus the QuickSight datasets, Q topics, and the embedded **Campaigns** dashboard (§5.7): ROI table by campaign/platform/dealer, funnel drop-off by source, NL ask box → `ask_quick`.

**Boundary.** `get_campaign_spend`, `attribute_conversions`, `recommend_budget_shift`, `get_personalize_segments`, `run_nightly_attribution` (writes `DEALER_Approvals`), and `refresh_audiences` are the **AgentCore/recommendation** half and live in the main repo (R4: recommendations that must be traceable and approvable are agent work). This project provides the dashboards, datasets, Q topics, and the `quick_dashboard_url` those steps reference.

> **Data source (dev).** The "S3 lake via Athena" is provisioned by `python3 -m app.infra.deploy_lake --apply --backfill` (verify with `--status`): EventBridge `app-bus-dev` (source `retail-ops.journeys`) → Firehose `retail-ops-dev-event-lake` → `s3://retail-ops-dev-event-lake-<account>/journey-events/` (Hive `dt=` partitions) → Glue table `retail_ops_dev_lake.journey_events` → Athena workgroup `retail-ops-dev-lake`. The backfill loads the journey events already in DynamoDB. Point A5 QuickSight datasets at that Athena source. The live handles of the current deployment are recorded in `docs/QUICK_BUILD.md` Appendix A3 once the stack is created.

---

## 5. Quick Index — shared retrieval service  [→ §4.3.1]

Semantic index over: CRM notes/activities, DMS service-history summaries, OEM incentive bulletins, product/trim guides, C&I program docs, market-intel reports, prior account plans, and repair procedures/TSBs.

- **Ingestion.** S3 connector (nightly export) + Salesforce connector; **PII masked at export**. *Corpus source today: `retail-ops-dev-PLAT_KnowledgeCollections` (538 docs, each with a `source_ref`) — see `docs/QUICK_BUILD.md` Appendix A1.*
- **Consumers.** The A6 Quick agent (native), and the **A3 and A7 AgentCore agents** via `retrieve_context` (main repo) — through a Gateway retrieval target or the Quick Index API. Results carry citations.
- **Security.** Quick content is **not row-scoped**: row-level security was removed by user decision on 2026-10-21 (main spec §13). Access to dashboards, datasets and the other shared content is controlled by Quick group membership only (knowledge bases can add document-level ACLs at creation, `docs/QUICK_BUILD.md` §2b), and anyone with access to a dataset or dashboard sees all its rows, every district and dealer. *(**Deployed:** the five sharing groups and their memberships, and per-user embedding that registers the signed-in user as a Quick Sight reader under their `user_id` and adds them to the sharing group for their role (`RETAIL_OPS_QUICK_PER_USER_EMBED=1`) — see `docs/QUICK_BUILD.md` §P1, §8 + Appendix A4/A7.)*

---

## 6. Integration contract — AgentCore Gateway → Quick  [→ §4.3.1]

The single bridge between the two projects.

> **Bridge (dev).** The **AgentCore Gateway** is provisioned by `python3 -m app.infra.deploy_gateway --apply` (verify with `--status`): `retail-ops-dev-crosspersona` (protocol MCP, authorizer AWS_IAM), MCP endpoint `https://retail-ops-dev-crosspersona-<id>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp`, with a Lambda target `oem-read` (`retail-ops-dev-gateway-oem`) exposing scoped, field-stripped, cited OEM read tools (`oem_get_account`, `oem_list_accounts`, `oem_get_alerts`, `oem_get_incentive_programs`). This is the **enforced cross-persona boundary**: a non-OEM persona (e.g. Dealer) reads OEM-owned data **only** through this Gateway — never a direct `retail-ops-dev-OEM_*` read (`app/services/common/crosspersona.py`; live route `GET /gateway/oem/{tool}`; a Dealer on a direct `/oem/*` route gets 403). Same-persona reads and the A2↔A6 Quick bridge may still invoke the runtime directly (`InvokeAgentRuntime`; contract `POST /invocations {path,method,query,body,claims}` → `{statusCode,result}`). The live handles (incl. the CUSTOM_JWT gateway Amazon Quick uses) are recorded in `docs/QUICK_BUILD.md` Appendix A5/A6 once the stack is created.

| Direction | Caller (main repo) | Target (this project) | Mechanism |
|---|---|---|---|
| A2 → A6 | A2 supervisor pulls the current Opportunity Brief / A6 content into Sales Packages when a journey's `account_id` is set (fleet/employee-pricing) | A6 Quick agent / Flows | Gateway target: Lambda calling Quick agent/flow APIs with the caller's scope; results cited |
| A3 → Quick Index | A3 floor/assistant agent grounds answers (CRM notes, bulletins, product guides) | Quick Index `retrieve_context` | Gateway retrieval target or Quick Index API |
| A7 → Quick Index | A7 service agent grounds answers (repair procedures, TSBs, warranty docs) | Quick Index `retrieve_context` | same |
| Console → QuickSight | Next.js OEM Workspace / Marketing Studio embeds dashboards | QuickSight registered-user embedding | Cognito → IAM Identity Center reader mapping |
| Dealer/CUST → OEM data | any non-OEM persona needing OEM-owned reads (account, alerts, C&I programs) | Gateway `oem-read` tools | **Deployed**: AgentCore Gateway `retail-ops-dev-crosspersona` MCP tools (scoped + field-stripped + cited); direct `retail-ops-dev-OEM_*` read is refused |

**Contract rules:** every cross-project call carries the caller's scope; every Quick answer/artifact returns citations; guardrail enforcement (GR-001…008) happens **inside** this project before results cross the bridge back; PII is masked at Quick Index export, not at the boundary. **Cross-persona data reads (e.g. Dealer→OEM) MUST traverse the AgentCore Gateway** — the boundary is enforced in code (`crosspersona.py`), not by convention: a non-OEM persona cannot read another persona's owner-prefixed tables directly.

---

## 7. Build track (Quick-hosted)  [→ main spec §"Phase 4b – Quick-hosted agents"]

This project's build is the main spec's **Phase 4b (Quick-hosted agents)** plus the QuickSight and Quick Index setup that A3/A7/A5 depend on. It is provisioned in the Amazon Quick environment (agents, Flows, Index collections, sharing, QuickSight datasets/dashboards/Q topics), on its own lifecycle, and integrated only through the §6 contract.

**Native build, shared data — the load-bearing distinction.** Only the *build surface* is native to Amazon Quick (agents, Flows, Index config, sharing, QuickSight authoring live in the Quick console on their own lifecycle). The *data* is not siloed: the whole solution is one tightly-integrated system on one data layer, one event bus, and one identity fabric. The **same synthetic dataset** (`docs/specs/retail-ops-synthetic-data-plan.md`) seeds what this project reads — and the plan already scopes it: the District storyline (§2.2) exists specifically to exercise A6 and the OEM Workspace (11 dealers, 3 months RDR + PY, the fleet/dealer-group accounts, the Sales Ops comms stream, the report library, and the guardrail test prompts), and the corpora feed Quick Index. So the generator is **not** an AgentCore-only concern: it must produce the A6 accounts/RDR/alerts/plans, the A5 journey-event lake prefixes for QuickSight, and the Quick Index corpora (CRM notes, bulletins, product guides, prior plans, repair procedures/TSBs), all with `source_ref`s so Quick citations resolve. Concretely, this project consumes `OEM_Accounts`, `OEM_Alerts`, `OEM_AccountPlans`, `OEM_GuardrailEvents`, the journey-event tables (the RDR/sales lake substitute today), and the Quick Index corpora — all produced by the main repo and one synthetic-data run. The **deployed** names (`retail-ops-dev-` prefixed), key schemas, and connection handles for every one of these are enumerated in `docs/QUICK_BUILD.md` Appendix A.

---

## 8. Open items / cross-references

- The **A6 data-model tables** (`OEM_Accounts`, `OEM_Alerts`, `OEM_AccountPlans`, `OEM_GuardrailEvents`, C&I/compliance/EV-readiness sandbox tables) are defined in the main spec §4 and seeded by the synthetic-data plan; this project consumes them. Their **deployed handles** (names, keys, ARNs) plus the AgentCore/embed/identity/IAM handles are in **`docs/QUICK_BUILD.md`** (Appendix A) — the consolidated build guide + handles reference a Quick builder uses to actually connect.
- The **OEM Workspace UI/UX per persona** (District manager, Regional sales manager, C&I planner, Sales ops comms, OEM finance) is defined in the main spec §5.9 and the persona matrix in §5.2; those requirements govern this Quick-hosted workspace.
- Any change here that touches shared behavior must be reconciled to the main spec, which wins.
