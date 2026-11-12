# Solution Package Specification: Intelligent Sales & Service Orchestration

*The orchestration layer of **Automotive Retail Operations powered by Agentic AI**.*

| Field | Value |
|---|---|
| Product name | **Automotive Retail Operations powered by Agentic AI** (2026-10-19): the name for titles, the console, and customer-facing material. Retail operations map onto the two orchestrated processes below — vehicle sales → Process 1 (purchase journey, A2), service and ownership → Process 2 (service journey, A7); the full mapping is in the repository `README.md`. See §13 |
| Package name | **Intelligent Sales & Service Orchestration**, the orchestration layer this specification defines, previously abbreviated *ISSO*; since 2026-10-19 the technical stem is `retail-ops` (`retail-ops-…` resources, `RETAIL_OPS_…` settings) — renamed 2026-09-23 from *Intelligent Retail & Purchase Orchestration (IRPO)*; see §13. The technical namespace uses **data-owner prefixes** — tables `OEM_*` / `DEALER_*` / `CUST_*` / `PLAT_*` (by who owns the data), infrastructure `app-*`, paths `/app`, generic fields `app_id`. The retired `IRPO_` / `ISSO_` / `SSO_` prefixes are not used for identifiers (renamed 2026-09-25) |
| Version | 1.3 (redesign of `automotive-sales-advisor`; 1.1 added Process 2 — Service & ownership; 1.2 added the agentic operating model §4.10, corrected and sourced KPIs, and the OWN / VIN-master correctness fixes; 1.3 renames the product to ISSO and adds the delivery-model selection requirement §2.2.2 and the external skills package, 2026-09-23) |
| Status | Draft – for build execution |
| Target enterprise | OEM, Dealer Network |
| Reference UI (dealer floor, A3) | `docs/use-cases/sales-advisor-v2.html` (layout and design system only; market content re-targeted to US) |
| Reference UI (OEM field sales, A6) | `docs/use-cases/DM_Assistant_Demo.html` (District Manager Assistant; information architecture, views, guardrails, and AI-insight pattern; OEM branding replaced by configurable brand tokens) |
| Reference UI (Service Desk, A7) | Same two files, no third mock: `sales-advisor-v2.html` for the operating screens (check-in, inspect, estimate, RO, parts), `DM_Assistant_Demo.html` for the Board, Cockpit, and chat/dictation panel; component mapping in §5.20 |
| Baseline codebase | This repository (Strands + AgentCore agents, Lambda/API GW, Next.js UI, DynamoDB) |
| Quick-native project | `docs/specs/retail-ops-quick-project.md` — the Amazon Quick-hosted scope that builds **outside this codebase**: A6 (Quick agent/Flows), A5 QuickSight dashboards, Quick Index, and the AgentCore Gateway → Quick bridge. Extract of §3.5, §3.6, §3.6.1, §4.3.1, §5.7, §5.9; this main spec remains the source of truth |
| Default market | **US** (`en-US`, USD, miles, MPG/MPGe, state-level incentives). Market is a first-class config; see §2.5 |
| Amazon Connect | **Optional module** (`ENABLE_CONNECT=false` by default); see §4.5 |
| Architecture flow | `docs/specs/retail-ops-architecture.md` (16 diagrams: context, layers, runtime, purchase journey, service journey, orchestration, nurture loop, A7 morning work, A6/Quick, events, identity, data, deployment, two continuity examples, agentic operating model). Updated 2026-09-22 for Process 2 and §4.10 (A7, service state machine, agentic triggers, 67 tables) |
| Synthetic data plan | `docs/specs/retail-ops-synthetic-data-plan.md` (golden storylines J1–J10 + District + Marketing, entity inventory for the operational tables and lake prefixes, conversational corpora, capability → data coverage matrix, generator design, validation invariants). Updated 2026-09-22 for Process 2 and §4.10: service storylines S1–S8, `DEALER_Vehicles`, 12 service tables, `PLAT_ConfigAutonomy` / `DEALER_Recommendations` seed, `service/` + `recommendations/` lake prefixes |
| Business processes | **Process 1 — Retail purchase** (DISCOVER → OWN, A1–A6, §2.1) and **Process 2 — Service & ownership** (BOOK → FOLLOW_UP, A7, §2.1.1), added 2026-09-22 |
| Open data gathered | `sample-data/open/SOURCES.md` (14 sources, auto-generated from `manifest.json`) + `sample-data/open/curated/` |

---

## 1. Business Context

### 1.1 Business Process

The package covers two connected business processes:

**Process 1 — Retail purchase (the funnel).** End-to-end retail journey: **vehicle discovery → configuration → financing → trade-in → delivery**. The package connects marketing, the sales floor, Finance & Insurance (F&I), and trade-in into a single orchestrated journey with one owner of the funnel: the Purchase Journey Orchestration Agent.

**Process 2 — Service & ownership (fixed ops).** End-to-end service visit: **service booking → check-in → multi-point inspection → diagnosis → estimate & approval → repair → vehicle ready → follow-up**. The package connects the service drive, technicians, parts, and the customer into a single orchestrated **service journey**, owned by the Service Operations Agent (A7), and links every visit back to the ownership record so the OWN stage of the purchase funnel is fed with real service data (equity, mileage, declined work, loyalty). Service is the retention engine that produces the next purchase journey; the two processes share one customer, one vehicle record, one console, and one event bus.

### 1.2 Business Problem

Three in four buyers want to complete most of the purchase online and nine in ten do at least some steps online (Cox Automotive Car Buyer Journey Study 2025). Internet-lead close rates are commonly cited around 10% (best stores near 15%), but the figures circulate in vendor material without a primary source, so treat them as unverified until re-baselined with the pilot dealer's own CRM. The loss happens at the handoffs between online and store, and between departments inside the store. Each ISV owns a silo and optimizes it locally:

| Silo | Typical ISV | What it owns | What it cannot see |
|---|---|---|---|
| CRM / Leads | Salesforce | Lead, contact, opportunity | Configurator intent, F&I readiness, trade equity |
| DMS / F&I | CDK | Deal desk, financing, delivery | Marketing touchpoints, digital browsing intent |
| Inventory / Valuation | Cox (vAuto, KBB) | Stock, pricing, trade values | Customer preference history, campaign source |
| Marketing | Adobe | Campaigns, audiences, ad spend | Whether spend converted to a delivered vehicle |

Nobody owns the full funnel; the customer falls through the gaps at every handoff.

The same fragmentation exists on the service side. Fixed operations produce more than half of dealer gross profit on roughly 13% of revenue (NADA 2025; Presidio-NCM Q1 2026 puts it at 53.8%, a record), yet dealers' share of service visits keeps falling: **only 54% of owners of vehicles two years old or newer still service at the selling dealer, down from 72% in 2023**, and dealers hold about 29% of all service visits (Cox Automotive Service Industry Study 2025). Widely repeated service-lane figures (roughly 70% of customers never shown inspection findings, about half approving when they are, 15–20% of decliners approving within 90 days if re-contacted) come from vendor and trade sources rather than a primary study; they are directionally useful but are treated as estimates to be re-baselined with the pilot dealer's own repair-order data (see §1.3). What is not in dispute: most stores do not re-present declined work, and the service lane and the sales floor do not share the customer:

| Silo | Typical ISV | What it owns | What it cannot see |
|---|---|---|---|
| Service scheduling | Xtime, myKaarma | Appointments, lane capacity | Vehicle equity, purchase journey, prior declined work |
| DMS / Repair orders | CDK, Reynolds | RO, labor ops, parts on RO, invoicing | Inspection findings in structured form, customer intent |
| Inspection / MPI | Dealer tablets, OEM tools | Multi-point inspection results, photos | Whether recommended work was ever sold or followed up |
| Diagnostics | OEM scan tools, Mitchell / ALLDATA | DTC lookup, repair procedures | Customer history, warranty status, parts availability |
| Parts | DMS parts module, OEM parts catalog | Stock, back-orders, pricing | Which pending ROs are waiting on a part |

Nobody owns the service visit end to end, and nobody brings the owner back around to the next purchase when the vehicle tells them it is time (the **repurchase/upgrade handoff**: service is the ownership phase that loops back to a new sale).

### 1.3 Success Metrics (KPIs)

Baselines carry a source the way incentive records do (§2.5.1). Rows marked *estimate* have no public benchmark and must be re-baselined with the pilot dealer's own data in Phase 0. Every KPI is computed from `DEALER_JourneyEvents` / `DEALER_ServiceJourneyEvents` / `DEALER_Recommendations` (§4.10), never entered by hand. Corrected 2026-09-22 after industry validation.

**Retail KPIs (Process 1)** — the lead funnel uses the four stages the industry measures, each against the prior stage.

| KPI | Owner | Baseline (industry) | Target | Source |
|---|---|---|---|---|
| Lead response time (first human-quality response) | A2 | hours (*estimate*) | < 2 min | Industry consensus; no primary benchmark — target is the design goal, baseline to confirm with pilot dealer |
| Lead → contact rate | A2 | ~60% (*estimate*) | ≥ 85% | LeadLocate four-stage funnel definition; baseline to verify with pilot dealer |
| Contact → appointment rate | A2 | ~35% (*estimate*) | ≥ 50% | same |
| Appointment → show rate | A2 / A3 | ~55% (*estimate*) | ≥ 70% | same |
| Show → close rate | A3 | ≈ 10% overall, ≈ 15% top quartile (*estimate*) | ≥ 15% end-to-end (lead → sold) | Vendor blogs only (Webtonic, DemandLocal); no primary source — **re-baseline with pilot dealer CRM in Phase 0** |
| Configurator → deal-desk handoff completeness | A2 | partial (*estimate*) | 100% context carried (`validate_handoff`) | Internal definition |
| Trade-in captured in same journey | A4 | ~35% (*estimate*) | ≥ 60% | To verify with pilot dealer DMS |
| F&I products per deal | A3 | **1.57** | **≥ 2.0** | StoneEagle Q2 2025 F&I Performance Report (1.57, up from 1.52 YoY) |
| Campaign spend attributable to delivered vehicles | A5 | < 30% (*estimate*) | ≥ 80% | To verify with pilot OEM marketing |
| Journey stage drop-off (any stage) | A2 | unmeasured | measured + alerting | Internal |
| Repeat / upgrade journeys sourced from OWN (service lane, equity, lease-end) per 100 delivered | A4 / A7 | unmeasured | ≥ 8 | Internal; new 2026-09-22 |

**Service & ownership KPIs (Process 2)**

| KPI | Owner | Baseline (industry) | Target | Source |
|---|---|---|---|---|
| In-warranty service retention (vehicles ≤ 2 yrs serviced at selling dealer) | A7 | **54%** (was 72% in 2023) | ≥ 70% | Cox Automotive Service Industry Study 2025 |
| Dealer share of all service visits (all vehicle ages) | A7 | ~29% | ≥ 35% for the store's own sold units | Cox 2025 (share down 12% vs 2018) |
| Inspection (MPI) completion rate on eligible ROs | A7 | ~60% (*estimate*) | ≥ 90% | Tracked by scheduling vendors (myKaarma ASR); no public median |
| **Findings presentation rate** (red/yellow items presented ÷ found) | A7 | **~30%** (*estimate*) | **≥ 90%** | Vendor source only (BG Products: ~70% never shown); **re-baseline with pilot dealer RO data in Phase 0**. Still the lever A7 controls directly |
| **Approval rate when presented** (approved ÷ presented, same visit) | A7 | **~50%** (*estimate*) | **≥ 60%** | Vendor source only (BG Products); **re-baseline with pilot dealer RO data**. Evidence + `explain_finding` targets the uplift |
| Declined-work follow-up conversion (approved within 90 days) | A7 → A2 | < 10% typical; 15–20% when re-contacted (*estimate*) | ≥ 20% (assumes 100% follow-up coverage) | Vendor / trade sources only (DealerDepot, VoiceDrop); **re-baseline with pilot dealer data** |
| Estimate approval turnaround (presented → decision) | A7 | hours (*estimate*) | < 20 min | Internal |
| RO cycle time (check-in → ready), maintenance visits | A7 | ~3.5 h (*estimate*) | ≤ 2.5 h | To verify with pilot dealer |
| Promised-time adherence (ready ≤ promised) | A7 | unmeasured | ≥ 90% | Internal; new 2026-09-22 (`reforecast_promise`) |
| Parts-wait ROs and hours lost | A7 | unmeasured | measured; ≥ 50% reduction via `stage_parts` by end of pilot | Internal; new 2026-09-22 |
| Repurchase/upgrade handoffs per 100 ROs (service → next sale) | A7 / A4 | ~1 (*estimate*) | ≥ 5 | Internal |
| Advisor "morning work" done before shift start (dispatch, staged parts, prepared briefs) | A7 | 0% | 100% of booked ROs | Numa 2026: top-vs-bottom advisor gap driven by morning workflow, not experience |

---

## 2. Solution Overview

### 2.1 Journey Stages (canonical state machine)

Every customer has at most one **active** Journey (stage DISCOVER … DELIVER). **OWN is a resting state**: a journey in OWN does not count toward the single-active rule, so a repeat purchase, a lease-end upgrade, or an A7 **repurchase/upgrade handoff** (the service-to-next-sale loop) creates a *new* journey in DISCOVER with `previous_journey_id` set, and the OWN journey is marked `superseded_by` (it is kept for history and A5 attribution). Agents act on and advance the journey; nothing advances without an event written to the journey record. (Rule corrected 2026-09-22; before, OWN counted as active and the handoff in §2.1.1 was unrepresentable.)

```
DISCOVER → CONFIGURE → QUALIFY → FINANCE → TRADE_IN → AGREE → DELIVER → OWN
    ▲          ▲           ▲          ▲          ▲         ▲         ▲
 Discovery  Configurator  Dealer   F&I Agent  Trade-In  Dealer   DMS +
 Agent      + Discovery   Assistant           Advisor   Assistant Outreach
```

| Stage | Entry criterion | Exit criterion | Owning agent |
|---|---|---|---|
| DISCOVER | Any identified touch (ad click, web visit, CRM lead) | Shortlist of ≤ 3 vehicles saved | Personalized Vehicle Discovery |
| CONFIGURE | Vehicle selected | Build saved with trim/pack/options + indicative price | Discovery (config recs) |
| QUALIFY | Dealer assigned or showroom visit / call | Budget, timeline, trade intent captured | Dealer Sales Advisor |
| FINANCE | Payment preference stated | Pre-qualified offer(s) presented | Orchestration Agent (F&I tools) |
| TRADE_IN | Customer has a vehicle to trade | Valuation + equity position recorded | Trade-In & Upgrade Advisor |
| AGREE | Financing + trade accepted | Deal structure pushed to DMS | Dealer Sales Advisor |
| DELIVER | Deal booked in DMS | Vehicle delivered, survey sent | Orchestration Agent (outreach via Connect if enabled, else email/SMS) |
| OWN | Delivered | Ownership events feed future upgrade prospecting | Trade-In & Upgrade Advisor |

### 2.1.1 Service Journey Stages (second state machine)

A customer in OWN can have any number of **Service Journeys** over the life of the vehicle, at most one active per VIN. A service journey is a separate record (`DEALER_ServiceJourneys`, `journey_type=service`) with its own stage machine, owned by A7. It never replaces the purchase journey; it writes ownership events to it (`service.visit.completed`, `service.declined_work`, `service.equity_flag`) and can spawn a new purchase journey via the **repurchase/upgrade handoff** (the second and later laps of the vehicle lifecycle: sale → ownership/service → next sale; the first lap is always sale → first service).

```
BOOK → CHECK_IN → INSPECT → DIAGNOSE → APPROVE → REPAIR → READY → FOLLOW_UP
  ▲        ▲          ▲          ▲          ▲         ▲        ▲         ▲
 A7 +    Service   Technician  Technician  Customer  Shop /  Service   A7 +
 customer advisor   (MPI)       (DTC)      via A7    Parts   advisor   A2 / A4
```

| Stage | Entry criterion | Exit criterion | Owning agent / role |
|---|---|---|---|
| BOOK | Customer request (My Garage, chat, phone, OEM connected-car alert, recall) | Appointment slot + services + loaner/wait/drop-off recorded | A7 `book_service` |
| CHECK_IN | Vehicle arrives; advisor opens RO in DMS | Mileage, concerns, walk-around photos, contact/approval preferences captured; RO id mirrored | Service advisor + A7 `open_repair_order` |
| INSPECT | RO assigned to technician | Multi-point inspection (VSA) submitted: each item green / yellow / red with evidence | Technician; A7 `record_inspection` |
| DIAGNOSE | Customer concern or red item needs cause | DTC lookup + repair procedure + labor op + parts list attached to each line | Technician; A7 `lookup_dtc`, `build_estimate` |
| APPROVE | Estimate composed | Customer approved / declined each line (digital approval in My Garage or by advisor); declined lines stored | A7 `present_estimate`, `record_approval` |
| REPAIR | Approved lines dispatched | All approved lines completed; parts received; QC passed | Shop; A7 `track_parts`, `update_line_status` |
| READY | RO closed in DMS | Invoice shared, pickup/delivery scheduled, customer notified | Service advisor; A7 `notify_ready` |
| FOLLOW_UP | Vehicle picked up | CSI survey, declined-work reminders scheduled, next-service prediction stored, equity/upgrade flag sent to A4, repurchase/upgrade handoff if triggered | A7 → A2 (nurture), A4 (equity) |

Rules that mirror the purchase journey: no stage advances without an event in `DEALER_ServiceJourneyEvents`; `validate_handoff` semantics apply (an RO cannot enter APPROVE with an estimate line that has no labor op, parts, or price source); every number shown comes from a tool call (DMS, parts, labor guide) and carries a `source_ref`.

**Where the two journeys meet** — the vehicle lifecycle is a loop: sale → ownership/service → next sale. The first lap runs sale → first service (`vehicle.delivered` opens the ownership record). Later laps run service → next sale via the **repurchase/upgrade handoff**.

| Trigger from service journey | Effect on purchase funnel |
|---|---|
| FOLLOW_UP with positive equity, lease-end ≤ 6 months, or repair estimate > X% of vehicle value | **Repurchase/upgrade handoff**: A4 `compute_equity` → A7 `handoff_to_sales` creates a purchase journey in DISCOVER with `source=service_lane` and the service context attached |
| Declined work older than 90 days | A2 nurture message (service template), not a sales message |
| Recall or connected-car alert on an owned VIN | A7 `book_service` proactive outreach through `OutreachProvider` |
| Vehicle delivered (purchase journey DELIVER → OWN) | A7 creates the ownership record and schedules the first service reminder |

### 2.2 Agent Roster

| # | Agent | Role in funnel | Primary users |
|---|---|---|---|
| A1 | Personalized Vehicle Discovery | Turns lifestyle, budget, and browsing history (CDP) into AI-generated recommendations of vehicles, trims, and packages, with VIN-level availability and pricing/incentives | Customer (web/app), Dealer |
| A2 | Purchase Journey Orchestration Agent | Supervisor and long-running nurture agent. Owns the journey state, engages prospects over time with personalized messages, answers their questions, nudges toward test drive / purchase, routes work to specialists, enforces handoff completeness | Customer (chat/email/SMS), System, Dealer, OEM CX |
| A3 | Dealer – Sales Advisor | Contextual suggestions for sales reps, BDC agents, and service advisors based on customer lifecycle stage and recent activity; live floor copilot (Customer 360, objections, incentives, deal structuring) | Dealer sales + BDC teams, service advisors, OEM retail marketing |
| A4 | Trade-In & Upgrade Advisor | Valuation, equity, upgrade timing, lease-end and aging-vehicle prospecting | Dealer, Customer |
| A5 | Campaign ROI Optimizer | Attributes ad spend to journey outcomes, recommends budget shifts, generates Quick dashboards | OEM marketing, Dealer principal |
| A6 | OEM – Sales Administrator | Generates account plans, opportunity briefs, and win strategies for dealer/fleet accounts from CRM + RDR sales lake + C&I incentive system + external market intelligence | OEM regional/district sales ops, C&I planning, Finance |
| A7 | Service Operations Agent (fixed ops) | Owns the service journey (§2.1.1): books and prepares visits, mirrors the DMS repair order, turns inspection and DTC findings into a priced, sourced estimate, drives customer approval, tracks parts and line status, notifies ready, schedules follow-up, and hands equity/upgrade signals to A4/A2 | Service advisors, technicians, parts counter, service manager, Customer (My Garage) |

### 2.2.1 Use-Case Classification

Source: solution-package use-case matrix (2026-09-10). Rows for A4/A5 are inferred to the same schema and marked *(inferred)*.

| Agent | Primary AI category | Delivery model (tech preference) | Target enterprise | Functional area | Target / potential customers | Multi-agent workflow | Data sources |
|---|---|---|---|---|---|---|---|
| A1 Personalized Vehicle Discovery | Gen AI | **Custom app on AgentCore** | OEM, Dealer NW | Marketing, Digital Commerce, Sales | OEM digital retail teams; dealer-group e-commerce | No (invoked by A2, but single-agent internally) | Configurator / product catalog, VIN inventory, web behavior / CDP, pricing and incentives |
| A2 Purchase Journey Orchestration | **Agentic** | **Custom app on AgentCore** | OEM, Dealer NW | Sales, Marketing, Digital Commerce | OEM digital retail and CX; dealer groups | **Yes** (supervisor of the purchase journey over A1/A3/A4/A5/A6; peers with A7 via events) | CRM, CDP, configurator, captive finance / F&I, trade-in valuation, DMS |
| A3 Dealer Sales Advisor *(renamed from "Dealer Sales Assistant" 2026-09-23; single custom agent, was split Quick + AgentCore)* | Gen AI + tools | **Custom app on AgentCore** (Strands; real-time floor path + assistant chat and flows in one agent; **Quick Index used as a retrieval tool** for CRM notes / bulletins / product guides, same pattern as A7) | Dealer NW, OEM | Sales, Marketing | Dealer sales and BDC teams; OEM retail marketing | No | CRM, DMS, OEM incentive feeds, digital retail and lead data; Quick Index (documents) |
| A4 Trade-In & Upgrade Advisor *(inferred)* | Gen AI + tools | Custom app on AgentCore | Dealer NW, OEM, Fleet Owner | Sales, Finance | Dealer used-car / trade desks; OEM captive finance | No | DMS, F&I, trade-in valuation APIs, service history |
| A5 Campaign ROI Optimizer *(inferred)* | Gen AI over analytics | Amazon Quick (dashboards, Q topics) + AgentCore for recommendations | OEM, Dealer NW | Marketing, Finance | OEM marketing; dealer principals | No | Ad platforms, CDP, journey event lake, DMS deliveries |
| A6 OEM Sales Administrator | Gen AI | **Amazon Quick** (Quick Research + Quick Flows + Quick Index over CRM/lake) | OEM | Sales, Sales Operations, Finance | OEM regional / district sales operations; C&I planning teams | No | CRM, RDR / sales data lake, incentive (C&I) system, market intelligence (S&P Global Mobility, R.L. Polk) |
| A7 Service Operations Agent *(added 2026-09-22)* | **Agentic** | **Custom app on AgentCore** (stateful service journey, tool-heavy; Quick Index used for repair procedures / TSB / warranty docs) | Dealer NW, OEM | Service (fixed ops), After-sales, Parts | Dealer service departments; OEM after-sales / customer-care | **Yes** (supervisor over the service journey; calls A4 for equity, A2 for nurture, A3 for talk tracks) | DMS (RO, service history), scheduling system, inspection (MPI/VSA) tool, DTC / repair-procedure database, labor-time guide, parts catalog + inventory, warranty / recall (OEM), connected-car alerts |

**What the two delivery models mean in this package**

| | Custom app on AgentCore | Amazon Quick |
|---|---|---|
| Runtime | Strands agent container on AgentCore Runtime; tools call connectors directly | Quick Suite agent (chat), Quick Flows (multi-step generation), Quick Index (semantic index over CRM/DMS/lake documents), Quick Research (external web + internal synthesis), QuickSight (visuals) |
| Where it shows up | Next.js console panels and customer-facing web/app | Embedded Quick experience inside the console (registered-user embedding) and native Quick UI for OEM users |
| Orchestration | Participates in A2A; A2 can invoke it | Exposed to A2 through a thin AgentCore Gateway tool that calls the Quick agent / flow API, so A2 can still pull an A6 opportunity brief or an A3 suggestion into a Sales Package |
| Real-time needs | Sub-2 s streaming (objection detection, live transcript) | Not designed for streaming floor assistance. Since 2026-09-23 A3 is a single custom AgentCore agent (streaming floor path + chat/flows) that uses Quick **Index** as a retrieval tool; Quick-hosted *agents/flows* are used only by A6 |
| Data access | Connectors (§4.1) | Quick data sources: Athena over the S3 lake, DynamoDB via Athena connector, Salesforce and S3 connectors; Quick Index ingests CRM notes, incentive bulletins, market reports |

### 2.2.2 Delivery-Model Selection: one solution composed from the right stack per persona and use case (added 2026-09-22)

Retail Ops is deliberately **not** built on one AI stack. Personas differ in how they work (a technician on a tablet in a bay, a District Manager reading a weekly package, a customer approving an estimate on a phone, an advisor mid-conversation on the floor), and the right technology for each differs. The package composes **five delivery models** into one system, joined by the Unified Shell, one identity fabric, the resource-URL contract, the event bus, and the AgentCore Gateway. This is a requirement, not an implementation detail: a proposal that puts every use case on a single stack does not meet the spec.

**Selection rules** (applied per use case; the first matching rule wins)

| # | If the use case needs… | Then the delivery model is… | Because |
|---|---|---|---|
| R1 | **Stateful, long-running orchestration** over a journey with tool calls into operational systems, schedules, and events (owns a state machine) | **Custom agent on Amazon Bedrock AgentCore** (Strands; Runtime + Memory + Gateway + Code Interpreter) | Only AgentCore gives durable per-customer memory, A2A between agents, sandboxed computation, event/schedule triggers, and the autonomy check (§4.10) around every effectful tool |
| R2 | **Sub-2 s streaming** on a live conversation (objection detection, live transcript) | **Custom agent on AgentCore** (real-time path), even if the rest of the use case is Quick-hosted | Quick is not designed for streaming floor assistance; the floor path calls Quick through the Gateway for suggestion *content* so there is one suggestion engine |
| R3 | **Document-style generation and grounded Q&A over enterprise content** (plans, briefs, contact reports) for a knowledge-worker persona who lives in chat and documents **and does not also need sub-2 s streaming or a shared state machine** | **Amazon Quick** (Quick agent + Quick Flows + Quick Index, Quick Research where external synthesis is needed) | Quick provides the index, citations, and multi-step document flows out of the box; building a *pure* document/analytics workbench on AgentCore would re-implement Quick. Applies to A6. **Note:** when the same persona *also* needs streaming or shared journey state (A3), R1/R2 win and the agent is custom AgentCore, using Quick **Index** as a retrieval tool rather than Quick-hosted agents/flows |
| R4 | **Analytics, dashboards, natural-language questions on the lake** | **Amazon Quick / QuickSight** (datasets, dashboards, Q topics), with AgentCore only for the *recommendation* step that reasons over the analytics | Visualization and NL-analytics are Quick's core; recommendations that must be traceable and approvable are agent work |
| R5 | **Deterministic read models and forms** (kanban boards, queues, worksheets, approval screens) | **Next.js + Lambda** with no agent in the request path; agents write to the tables the screen reads | Screens must render in milliseconds and be auditable; agents pre-compute (§4.10.2) rather than compute on page load |
| R6 | **Customer-facing self-service** on brand web / mobile (shop, chat, approve, book) | **Customer Web (Next.js, OEM brand theme)** calling the same AgentCore agents through the API layer | Customers never see Quick; they see the brand. The agent is the same one the advisor uses, so what the customer approves is what the advisor presented |
| R7 | **Voice or telephony outreach** as a channel | **`OutreachProvider`** (SES/SNS default; Amazon Connect optional) | Channel is a provider behind an interface so enabling Connect changes no agent or UI code |

**Result per agent and persona**

| Agent | Persona and access pattern | Primary model | Secondary model (why) |
|---|---|---|---|
| A1 Discovery | Customer on brand web/mobile; advisor in Discovery tab | AgentCore (R1: stateful profile, Personalize, TCO in Code Interpreter) | Customer Web (R6); Personalize for ranking |
| A2 Purchase Orchestration | System (schedules/events), customer chat, advisor via Journeys | AgentCore (R1: supervisor, nurture, Plays, NBA) | Gateway → Quick to pull A3/A6 content into Sales Packages |
| A3 Dealer Sales Advisor | Advisor / BDC in chat; **advisor on the floor mid-conversation** | **Custom agent on AgentCore** (R1 + R2: one agent for streaming objection detection, contextual suggestions, cited chat, call list, upsell brief) | Quick **Index** as a retrieval tool for CRM notes / bulletins / guides (not Quick-hosted agents/flows); Next.js Assistant screen |
| A4 Trade-In & Upgrade | Advisor, used-car manager, customer (My Garage trade) | AgentCore (R1: equity math in Code Interpreter, continuous monitoring on events) | Next.js Trade Desk read models (R5) |
| A5 Campaign ROI | OEM marketing analyst, finance, dealer principal | **Quick / QuickSight** (R4: dashboards, Q topics) | AgentCore for `recommend_budget_shift` and nightly auto-drafted approvals (R4 second half) |
| A6 OEM Sales Administrator | District Manager in a weekly package, chat, dictation | **Quick** (R3: Quick Research, Flows, Index) with the DM Assistant reference UI | AgentCore only through the Gateway when A2 needs an account brief; A6's weekly cycle runs as a scheduled Quick Flow orchestrated by a Lambda |
| A7 Service Operations | Service advisor + technician on tablets, parts counter, customer approving on phone | AgentCore (R1: service state machine, morning work, Code Interpreter estimates) | Quick Index for procedures/TSBs (R3 content); Customer Web for approval (R6); Next.js Service Board / Parts desk (R5) |
| Store / Service Cockpit, Delivery Board, F&I Desk, Lead Desk, Journeys | Dealer operators | Next.js + Lambda read models (R5) | QuickSight tiles for trends (R4); agents pre-compute suggestions |
| Customer Web / My Garage | Customer | Customer Web (R6) | A1, A2, A7 behind the API; OutreachProvider for messages (R7) |

**Composition rules** (how the models become one solution)

1. One sign-in (Cognito staff pool with native + Federate; Identity Center federation for Quick; customer pool for My Garage) and one scope model drive RBAC and OpenSearch filters, so the API and search never disagree on visibility. Amazon Quick content is not row-scoped: Quick group membership decides who can open a dashboard or dataset, and anyone with access sees all rows (§8.1; decision 2026-10-21, §13).
2. One shell (app switcher, scope pill, ⌘K, Tasks, Notifications) wraps Next.js apps and embedded Quick alike; Quick embeds navigate the shell with `postMessage` and the resource-URL contract (§5.18).
3. One event bus: every stack publishes and subscribes the same event types; Quick-hosted agents publish through the Gateway Lambda (§4.6).
4. One agent bridge: AgentCore Gateway exposes the Quick-hosted A6 agent and its Flows as tools so A2 can pull an A6 account brief without knowing where it runs; A3 is a native AgentCore agent invoked over A2A like A1/A4 (§4.3.1). Each custom agent (A1, A2, A3, A4, A7, and A5's recommendation step) runs in its own AgentCore runtime. Agent-to-agent calls are A2A-shaped requests (`kind: tool | agent | job`, caller claims forwarded, one self-citing result) carried by `InvokeAgentRuntime`; adopting the A2A wire protocol per runtime is a later option (ADR 0009).
5. One suggestion engine per domain: A3 is that single engine for the sales floor (its streaming path and its chat/flows are one agent, so a suggestion and its live-floor delivery cannot diverge); it grounds every answer in Quick Index / `KnowledgeBase` retrieval rather than free text, the same way A7's `explain_finding` cites indexed procedures.
6. One trace: `correlation_id` spans API, AgentCore, Gateway, Quick, and events (§8), and every recommendation from any stack lands in `DEALER_Recommendations` (§4.10.3).
7. Market and autonomy are configuration, not stack: `MarketConfig` (§2.5) and `PLAT_ConfigAutonomy` (§4.10.1) apply identically across AgentCore, Quick, and Next.js surfaces.

**Anti-patterns this rule set rejects:** building A6's pure document/research workbench as a custom agent (re-implements Quick); running any streaming floor path inside Quick (cannot stream); **splitting one persona's agent across two runtimes when the streaming path and the chat/flows share the same suggestions and state** (the reason A3 was consolidated onto AgentCore on 2026-09-23; see §13); putting Quick in front of customers (brand and licensing); computing Cockpit tiles with an agent at page load (latency, cost, non-determinism); one autonomy setting for the whole system (dealers need per-action control).

### 2.3 High-Level Architecture

```
┌────────────────────────────────────────────────────────────────────────────┐
│                           CHANNELS                                          │
│  OEM Web / App (Discovery)   Dealer Console (Sales Floor + Service Desk)     │
│  Next.js static (CloudFront)  Next.js static (CloudFront)   Outreach        │
│  + customer chat (A2)         + embedded Quick (A6)         SES/SNS default │
│  + My Garage service (A7)     + technician tablet (A7)      Connect (opt.)  │
└───────────────┬───────────────────────────┬───────────────────────┬────────┘
                │ Cognito (JWT)              │                       │
┌───────────────▼───────────────────────────▼───────────────────────▼────────┐
│                     API Gateway (REST) + Lambda adapters                    │
│  /journeys /chat /discovery /configurations /finance /trade-in /campaigns   │
│  /accounts /service/*  (bookings, repair-orders, inspections, estimates,    │
│                          parts, follow-ups)                                 │
└───────────────────────────────┬────────────────────────────────────────────┘
                                │ InvokeAgentRuntime
┌───────────────────────────────▼────────────────────────────────────────────┐
│               Amazon Bedrock AgentCore  (Runtime + Memory + Gateway)        │
│                                                                              │
│   ┌───────────────────────────────────────┐ ┌──────────────────────────┐    │
│   │ A2  Purchase Journey Orchestration    │ │ A7  Service Operations   │    │
│   │     (Supervisor + Nurture)            │◀▶│     (Service journey     │    │
│   │     AgentCore Memory: customer prefs  │ │      supervisor)         │    │
│   └───┬──────────┬──────────┬──────┬─────┘ └──┬────────┬──────────────┘    │
│       ▼          ▼          ▼      ▼           ▼        ▼  EventBridge      │
│   A1 Discovery  A3 Floor  A4 Trade  A5 Camp.  A4 equity  A2 nurture         │
│   (Personalize) (stream)  (Value)  (Bedrock)  A3 talk    Gateway → Quick    │
└──────┬───────────────┬───────────────┬──────────────┬────────┬──────────────┘
       │               │               │              │        │
       │               │               │              │   ┌────▼────────────────────────────┐
       │               │               │              │   │ Amazon Quick Suite               │
       │               │               │              │   │  A6 OEM Sales Administrator      │
       │               │               │              │   │   (Quick agent + Research +      │
       │               │               │              │   │    Flows)                        │
       │               │               │              │   │  Quick Index (retrieval tool     │
       │               │               │              │   │   for A3 & A7 AgentCore agents)  │
       │               │               │              │   │  A5 dashboards / Q topics        │
       │               │               │              │   └────┬────────────────────────────┘
┌──────▼───────────────▼───────────────▼──────────────▼────────▼──────────────┐
│                 Integration Layer (AgentCore Gateway / Lambda connectors)    │
│  CRM (Salesforce)  CDP  Configurator  F&I (CDK)  Trade-In (Cox/KBB)          │
│  DMS (CDK)  Ad Platforms (Adobe/Google/Meta)  Dealer Inventory (VIN-level)   │
│  RDR / sales data lake  C&I incentive system  Market intel (S&P, Polk)       │
│  Service scheduling  Inspection (MPI)  DTC / labor guide  Parts  Warranty    │
│  ── all behind a Connector interface; sandbox = DynamoDB-backed fakes ──     │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Data: DynamoDB (operational)  S3 (events/lake)  Amazon Personalize          │
│        Amazon Quick (analytics)  AgentCore Memory (conversation/prefs)       │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.4 Tech Stack Mapping

| Capability | Service | Used by |
|---|---|---|
| Agent runtime, memory, gateway, observability | Amazon Bedrock AgentCore | A1, A2, A3, A4, A5, A7 |
| Reasoning / generation | Amazon Bedrock (Claude Sonnet family via Strands) | A1, A2, A3, A4, A5, A7 (A6 uses Quick's hosted models) |
| Repair-procedure / TSB / warranty document retrieval | Amazon Quick Index (documents) + `KnowledgeBase` (AR-1) | A7 |
| Financial and estimate math (equity, repair-vs-replace, estimate totals) | AgentCore Code Interpreter (AR-2) | A1, A3, A4, A7 |
| Personalized ranking, similar-items, user segmentation | Amazon Personalize | A1, A5 |
| Analytics, dashboards, natural-language Q&A on funnel data | Amazon Quick (Quick Suite / QuickSight) | A5, OEM ops views |
| Omnichannel outreach (voice, chat, SMS callbacks) — **optional** | Amazon Connect (+ Contact Lens). When disabled, A2/A7 fall back to dealer tasks + email/SMS via SES/SNS | A2, A3, A7 (estimate presented, vehicle ready, proactive booking) |
| Auth | Amazon Cognito | UI, API |
| API | API Gateway REST + Lambda (Python 3.12) | All channels |
| Operational store | DynamoDB (on-demand) | All |
| Event stream / lake | DynamoDB Streams → Kinesis Firehose → S3 (Parquet) | A5, Quick |
| Static hosting | S3 + CloudFront | UI |
| Speech (live floor, technician voice notes, advisor walk-around dictation) | Amazon Transcribe Streaming, Polly (existing) | A3, A7 |
| Photo / video evidence on inspections | S3 (`app-docs`) via Document service, Amazon Rekognition (optional label check) | A7 |
| Fallback outreach (when Connect disabled) | Amazon SES (email), Amazon SNS (SMS) | A2 |

### 2.5 Market Parameterization

The solution is market-agnostic; **US is the default and the only market shipped in v1 seed data**. The reference UI's India/Mahindra content is treated as a layout reference only. No market-specific literal (currency symbol, unit, tax rule, incentive program, OEM brand) may appear in agent prompts, tool code, or UI components; all come from a `MarketConfig`.

**`MarketConfig` (stored in `OEM_Markets`, one row per `market_id`; default `us`)**

| Field | US default | Notes |
|---|---|---|
| `market_id` | `us` | |
| `locale` | `en-US` | Drives `Intl.NumberFormat` / `Intl.DateTimeFormat` in UI and Python `babel` in agents |
| `currency` | `USD` | Formatting: `$32,450`, no lakh/crore grouping |
| `distance_unit` | `mi` | |
| `fuel_economy_unit` | `mpg` (ICE), `mpge` (EV) | |
| `energy_price_default` | `$0.16 / kWh` | Home charging; overridable per state |
| `fuel_price_default` | `$3.60 / gal` | Overridable per state |
| `annual_distance_default` | `13,500 mi` | For TCO calcs |
| `tax_model` | `state_sales_tax` | Rate looked up by `state` code; registration fees per state |
| `incentive_programs` | Federal EV Clean Vehicle Credit (IRC 30D, point-of-sale), state rebates (e.g., CA CVRP, CO, NY Drive Clean), utility charger rebates, OEM cash/APR/lease specials | Sourced from `OEM_Incentives` filtered by `market_id` + `state` |
| `finance_products` | Retail installment (36/48/60/72), lease (24/36/39), balloon | F&I connector maps to lender program codes |
| `credit_tiers` | Super Prime / Prime / Near Prime / Subprime (FICO bands) | Used by `prequalify` |
| `trade_valuation_sources` | Cox (vAuto / KBB), Black Book, J.D. Power (sandbox fake in dev) | |
| `regulatory_disclosures` | TILA/Reg Z payment disclosure text, state-specific doc fees | Injected into `structure_deal` outputs |
| `region_dimension` | `state` (2-letter) | Used for incentives, tax, inventory radius, campaign geo |
| `phone_format` | E.164, display `(xxx) xxx-xxxx` | |
| `address_schema` | street, city, state, zip5 | |

**Enforcement**
- Agents receive `market_config` in every invocation payload (A2 attaches it from the journey's `market_id`).
- `agents/common/market.py` exposes `fmt_money`, `fmt_distance`, `fmt_efficiency`, `tco()` and refuses to run without a config.
- UI: `MarketContext` (replaces/extends `OemContext`) provides formatters; ESLint rule bans hard-coded `$`, `₹`, `km`, `mi` literals in `components/app/**`.
- Seed data generator takes `--market us|de` and emits the market's synthetic OEM catalog, dealers, contacts, incentives, and rules (§2.5.1).
- Adding a market = new `OEM_Markets` row + seed pack + optional connector overrides. No code changes in agents or UI.

### 2.5.1 Seed Markets and Open-Data Sourcing

Two markets ship in v1. Each has a **synthetic brand** (fictional name and logo) whose lineup, trims, prices, ranges, efficiency, and competitor set are **derived from real public data** so demos feel authentic without using a real OEM's trademarks. Market intelligence for A6 also comes only from open data; licensed feeds (S&P Global Mobility, R.L. Polk) remain `LiveConnector` stubs.

| | US market `us` | Europe market `de` (Germany as representative) |
|---|---|---|
| Synthetic brand | **Meridian Motors** | **Nordwerk Automobile** |
| Locale / currency / units | `en-US`, USD, mi, MPG / MPGe | `de-DE`, EUR, km, l/100 km / kWh/100 km, WLTP range |
| Region dimension | `state` (50 + DC) | `bundesland` (16) — `OEM_StateRules` reused with `region_code` |
| Tax model | State sales tax + registration fees | 19 % VAT included in list price; Kfz-Steuer by CO2 and displacement; EV exemption |
| Incentive tiers | Federal 30D credit, state rebates, utility charger rebates, OEM specials | THG-Quote payout, regional/municipal EV and wallbox grants, OEM specials (federal Umweltbonus ended; modelled as historical) |
| Finance products | Retail 36/48/60/72, lease 24/36/39, balloon | Leasing (24/36/48), Ballonfinanzierung, Drei-Wege-Finanzierung |
| Lineup (7 models, mirrored across markets) | Compact SUV (ICE/hybrid), mid-size SUV (ICE/hybrid), 3-row SUV, mid-size pickup, compact EV crossover, mid-size EV SUV, EV sedan | Kompakt-SUV, Mittelklasse-SUV, Kombi (estate), Kompaktwagen, EV Kompakt-Crossover, EV Mittelklasse-SUV, EV Limousine |
| Price/spec anchors | Segment median MSRP, range, MPG/MPGe from EPA data for the segment's top sellers | Segment median list price, WLTP range/CO2, consumption from EEA per-registration records |
| Competitor set per model | Real segment leaders (named, public data) | Real segment leaders (named, public data) |

**Open data sources gathered (2026-09-10; all free / public; full provenance in `sample-data/open/SOURCES.md`)**

| Purpose | US (`sample-data/open/us/`) | Europe (`sample-data/open/de/`, `eu/`) |
|---|---|---|
| Vehicle specs, efficiency, range | `epa_fueleconomy` — EPA fueleconomy.gov: segment anchors (median MPG/MPGe, EV range, kWh/100mi, fuel cost per EPA class × powertrain) and `epa_models_2024plus` competitor list | `eea_co2_de` / `eea_co2_eu` — EEA CO2 monitoring (Reg. 2019/631) aggregated per make/model/fuel via Discodata SQL: WLTP CO2, Wh/km, l/100 km, kW, mass, displacement; BEV range from `curated/de/ev_range_anchors.json` (manufacturer WLTP) |
| Registrations / market share (RDR & PMA proxies) | `afdc_ev_registrations` (EV by state), `afdc_vehicle_registration` (fleet by state × fuel type) | `kba_fz10` — KBA FZ 10.1 Dec 2025: registrations by brand/model, month + YTD, Diesel / Hybrid / PHEV / BEV splits |
| Fuel / energy prices for TCO | `eia_gasoline_weekly` (US + PADD, 2026-09-07), `eia_electricity_state` (EPM Table 5.6.A residential ¢/kWh by state, June 2026) | `ec_oil_bulletin` (EU history + per-country weekly Euro-super 95 / diesel incl. taxes, 2026-09-07), `eurostat_electricity` (nrg_pc_204 band DC incl. taxes, 2025-S2, DE + 9 countries) |
| Incentives | `irs_30d_list` (fueleconomy.gov tax REST: 30D eligible vehicles, historical) + `curated/us/incentives.json` (30D/25E expired 2025-09-30; car-loan interest deduction 2025-2028 active; CO, NY, MA, IL state programs; utility template; synthetic OEM) | `curated/de/incentives.json` (Umweltbonus expired; E-Auto-Förderung 2026 active; Kfz-Steuer BEV rules incl. announced 2026-2030 extension; 0.25% BiK; Sonder-AfA; THG-Quote; wallbox template; synthetic OEM) |
| Tax and fee rules | `curated/us/state_rules.csv` (51: sales tax %, vehicle-tax note, local %, EV fee, doc-fee cap) | `curated/de/tax_rules.json` (19% VAT, Kfz-Steuer displacement + CO2 bands, BEV weight rate, BiK, finance products, 16 Bundesländer) |
| Charging infrastructure (EV readiness) | `afdc_charging_ports` (public/private ports by state; replaces the network-blocked NREL API) | `bnetza_ladesaeulen` — Bundesnetzagentur register aggregated per Bundesland: stations, charge points, ≥50 kW, ≥150 kW, total kW |
| Geography / dealers | `census_gazetteer_places` (largest places per state with lat/lon) | Bundesland capitals + population in `curated/de/tax_rules.json`; real cities for synthetic dealers |
| Lineup anchors | `curated/lineup.json` — Meridian Motors 7 models with real competitor base MSRPs (verify) | `curated/lineup.json` — Nordwerk Automobile 7 models with real competitor UVPs and EEA/KBA anchor keys |
| Demographics for CDP profiles | Synthetic sampling by state income bands (Census ACS priors, not downloaded) | Synthetic sampling by Bundesland (Destatis priors, not downloaded) |
| CSI proxy | Synthetic, seeded from public segment averages as priors | Synthetic, seeded from public satisfaction-survey priors |

Dropped from the original plan: NHTSA vPIC (network-blocked; not needed for seed), ACEA press data (KBA + EEA cover it), FHWA MV-1 (AFDC suffices). Everything the seed needs that is not in an open feed is in `sample-data/open/curated/` with `source_urls` and a `verify` flag. What must be **generated** from these anchors is specified in `docs/specs/retail-ops-synthetic-data-plan.md`.

**Sourcing rules**
- `scripts/seed/fetch_open_data.py` downloads raw files to git-ignored `sample-data/open/raw/`, derives compact snapshots into `sample-data/open/{us,de,eu}/`, and writes `manifest.json` + `SOURCES.md` (URL, license, retrieval UTC, sha256, rows). Snapshots (~1 MB) are committed so seeding is reproducible offline; raw (~67 MB) is re-fetchable.
- Real brand/model names appear **only** in competitor fields and as anonymised spec anchors; the synthetic brand's own trims never copy a real trim name.
- Every derived number (MSRP anchor, range, incentive amount) carries a `source_ref` so A1/A3/A6 citations resolve to a real dataset row.
- Both markets share the same catalog schema; the EU pack adds `wltp_range_km`, `co2_g_km`, `kfz_steuer_eur`, `vat_included=true`.

### 2.6 Feature → Tech Stack Matrix

Consolidated view of every feature in this spec and the stack that implements it. Columns: **UI** (where the user sees it), **Agent / Logic** (what computes it), **Data** (where state lives), **Integration** (external systems or AWS services it depends on). Section references point to the detailed spec.

#### 2.6.1 Customer-facing features (Customer Web app)

| Feature | UI | Agent / Logic | Data | Integration | Spec |
|---|---|---|---|---|---|
| Lifestyle intake (≤ 4 questions) | Next.js `/shop` | A1 `infer_lifestyle_profile` (Bedrock, Strands, AgentCore Runtime) | AgentCore Memory, `DEALER_Signals` | CDP connector | §3.1 |
| Personalized vehicle / trim / package shortlist | `/shop` | A1 + Amazon Personalize (ranking, similar-items); rules fallback | `OEM_Catalog`, `DEALER_Inventory` | Configurator connector | §3.1, §4.4 |
| Price build with incentives + est. monthly | `/shop`, Advisor | A1 `price_build` | `OEM_Incentives`, `OEM_StateRules`, `OEM_Markets` | Configurator, F&I connector | §3.1, §2.5 |
| VIN availability by ZIP / dealer radius | `/shop`, Advisor | A1 `search_inventory` | `DEALER_Inventory` | Dealer Inventory connector | §3.1 |
| EV vs ICE 5-year TCO | `/shop`, Advisor | A1 `build_tco_comparison` (market units) | `OEM_StateRules` | – | §3.1, §5.3 |
| Save build → journey created / advanced | `/shop` | A1 `record_shortlist` → A2 | `PLAT_Configurations`, `DEALER_Journeys` | EventBridge `build.saved` | §2.1, §4.6 |
| Book test drive | `/shop`, My Garage, Lead Desk | A2 `book_test_drive` | `DEALER_Appointments` | Dealer calendar connector | §3.2 |
| Nurture conversation (grounded Q&A, nudges) | `/shop/chat` (magic-link resume) | A2 `answer_customer_question`, `nudge` | AgentCore Memory, `CUST_Messages`, `DEALER_NurturePlans` | SES / SNS (default), Connect (optional) | §3.2, §4.5 |
| Automated nurture messages (cadence, quiet hours, consent) | Email / SMS / chat | A2 `plan_nurture`, `compose_message` on EventBridge Scheduler tick | `DEALER_NurturePlans`, `CUST_Consent`, `PLAT_ConfigNurture` | SES, SNS, Connect (opt.) | §3.2 |
| My Garage: journey tracker, builds, appointments, finance status, trade offer, documents, ownership | Next.js `/garage/*` (customer Cognito pool, passwordless) | Read models + A2 chat escalation | `DEALER_Journeys`, `PLAT_Configurations`, `DEALER_Appointments`, `DEALER_FinanceOffers`, `DEALER_Valuations`, `CUST_Documents` | DMS connector (ownership) | §5.12 |
| Consent & preferences (channels, quiet hours, opt-out, data request) | My Garage | Consent service | `CUST_Consent` | Admin data-requests | §5.12, §8.2 |
| F&I menu presentation (customer-facing mode) | My Garage, F&I Desk second screen | `structure_deal` output | `DEALER_FIProducts`, `DEALER_Deals` | F&I connector | §5.13 |
| Delivery-day updates & survey | Email / SMS / My Garage | A2 outreach on `vehicle.delivered` | `DEALER_Deliveries` | SES / SNS / Connect (opt.) | §5.15 |

#### 2.6.2 Dealer Console features (dark theme, Next.js + Lambda + AgentCore)

| Feature | UI | Agent / Logic | Data | Integration | Spec |
|---|---|---|---|---|---|
| Journeys Kanban (8 stages, stall, missing-context, nudge) | `/dealer/journeys` | A2 supervisor, `validate_handoff` | `DEALER_Journeys`, `DEALER_JourneyEvents` | EventBridge | §5.4 |
| Journey ownership + handoffs + activity feed | All journey screens | Handoff service | `DEALER_Handoffs`, `DEALER_Tasks` | Notification Hub | §5.18.3, §5.18.6 |
| Live Advisor: Customer 360 + journey stepper | `/dealer/advisor/{id}` left panel | A3 `get_customer_360`, `get_lifecycle_context` | `DEALER_Customers`, `DEALER_Signals`, `DEALER_Journeys` | CRM, CDP, DMS connectors | §5.3 |
| Live Advisor: recommendations, EV/ICE table, talk tracks, cross-sell, config / finance / trade cards | Center panel | A1 (recs), A3 `get_cross_sell`, `structure_deal`, A4 valuation | `DEALER_Inventory`, `DEALER_FIProducts`, `DEALER_Valuations` | Personalize, F&I, valuation connectors | §5.3 |
| Objection detection from live transcript (< 2 s) | Right panel | A3 floor path (AgentCore streaming) `classify_objection`, `get_objection_strategy` | `DEALER_ObjectionStrategies`, `DEALER_Outcomes` | Amazon Transcribe Streaming | §3.3, §5.3 |
| Competitive intel, incentives, value snapshot | Right panel | A3 `get_competitive_intel`, `get_incentives` | `OEM_Catalog.competitors`, `OEM_Incentives` | OEM incentive feed | §5.3 |
| Contextual suggestions by lifecycle stage | Advisor strip, Assistant | A3 `generate_contextual_suggestions` (AgentCore; matrix in `PLAT_ConfigSuggestions`; grounds on Quick Index via `retrieve_context`) | `DEALER_Journeys`, CRM/DMS activity | AgentCore Runtime; Quick Index (retrieval) | §3.3 |
| Next Best Action (one-click) | Right panel | A2 `nudge`, `schedule_outreach` | `DEALER_Tasks`, `DEALER_Appointments` | OutreachProvider | §5.3 |
| AI Log (agent trace) | Bottom bar | `write_trace` | `PLAT_AgentTraces` | AgentCore observability | §5.2.1 |
| "View as customer" mirror | Advisor | Server-side read-only My Garage render | same as My Garage | – | §5.12 |
| Assistant chat (cited answers) | `/dealer/assistant` | A3 (AgentCore) grounding on Quick Index via `retrieve_context` | Quick Index (CRM notes, DMS summaries, incentive bulletins, product guides) | AgentCore Runtime; Quick Index (retrieval) | §5.8, §4.3.1 |
| Service-lane upsell brief / Follow-up draft / ad-hoc call query | Assistant | A3 (AgentCore) tools | CRM, DMS, journeys via Athena; Quick Index | AgentCore Runtime | §3.3 |
| Lead Desk (inbound queue, SLA, first-response script, assign) | `/dealer/assistant/leads` | A3 + lead service | `DEALER_Leads`, `DEALER_LeadSLA` | Lead-provider connector (ADF), OutreachProvider | §5.8 ext. |
| Service Lane (today's ROs, upsell, equity flag, repurchase/upgrade handoff) | `/dealer/assistant/service` | A3 brief + A4 equity | `DEALER_Journeys` (source=service_lane) | DMS connector | §5.8 ext. |
| Trade-in valuation, payoff, equity, payment delta | `/dealer/trade-in`, Advisor card | A4 `get_trade_valuation`, `compute_equity`, `estimate_payment_delta` | `DEALER_Valuations` | Valuation connector (Cox/KBB/Black Book), NHTSA vPIC, F&I | §3.4 |
| Upgrade prospects (lease-end, aging, service frequency) | Trade-In › Prospects | A4 `find_upgrade_prospects` | `DEALER_Customers`, DMS history | DMS connector | §3.4 |
| Inventory grid with demand score; trade disposition | `/dealer/inventory` | A1 shortlist counts, A4 disposition recommendation | `DEALER_Inventory`, `DEALER_TradeDispositions` | Inventory, valuation, DMS connectors | §5.6 ext. |
| F&I Desk (queue, worksheet, lender offers, menu, compliance, push to DMS) | `/dealer/fni` | A3 `structure_deal`, `push_deal_to_dms` | `DEALER_Deals`, `DEALER_FinanceOffers`, `DEALER_FIProducts`, `OEM_Markets` (disclosures) | F&I (CDK) and DMS connectors | §5.13 |
| Delivery Board (kanban, prep checklist, docs, delivered → OWN) | `/dealer/delivery` | Delivery service + A2 outreach | `DEALER_Deliveries`, `CUST_Documents`, `DEALER_Appointments` | DMS `mark_delivered`, EventBridge | §5.15 |
| Store Cockpit (funnel, sales, inventory, trade, F&I, CX, alerts, comms) | `/dealer/cockpit` | Event-materialized read models; A6 alerts | Read-model tables fed by EventBridge subscribers; `OEM_Alerts`, `OEM_Comms` | EventBridge | §5.14 |
| Store-scoped campaign ROI | `/dealer/campaigns` | A5 | Lake via Athena | QuickSight embed | §5.7 |

#### 2.6.2a Dealer Console — Service Desk features (Process 2, A7; dark theme, Next.js + Lambda + AgentCore)

| Feature | UI | Agent / Logic | Data | Integration | Spec |
|---|---|---|---|---|---|
| Service booking (customer, advisor, proactive recall / connected-car) | My Garage › Service, `/dealer/service/book`, chat | A7 `book_service`, `predict_next_service` | `DEALER_ServiceJourneys`, `DEALER_Appointments` (type=service) | Scheduling connector, OutreachProvider | §3.7, §5.20 |
| Service journey board (8 service stages, held-for-parts, waiting-approval, overdue) | `/dealer/service` | A7 supervisor, `validate_service_handoff` | `DEALER_ServiceJourneys`, `DEALER_ServiceJourneyEvents` | EventBridge | §5.20.1 |
| Check-in / walk-around (mileage, concerns, photos, dictation, approval prefs) | `/dealer/service/{id}/checkin` (tablet) | A7 `open_repair_order`, voice hook (AR-7) | `DEALER_RepairOrders`, `CUST_Documents` | DMS connector (RO create/mirror), Transcribe | §5.20.2 |
| Multi-point inspection (green / yellow / red, evidence, technician notes) | `/dealer/service/{id}/inspect` (technician tablet) | A7 `record_inspection`, `prioritize_findings` | `DEALER_Inspections`, `DEALER_InspectionTemplates` | Inspection (MPI/VSA) connector or native form | §5.20.3 |
| DTC lookup, repair procedure, labor time, parts list per line | Inspect / Diagnose view | A7 `lookup_dtc`, `get_repair_procedure`, `get_labor_time`, `lookup_parts` | `DEALER_DTCReference`, `DEALER_LaborOps`, `DEALER_Parts` | DTC / repair-procedure connector, labor guide, parts catalog, Quick Index (TSBs) | §3.7 |
| Estimate builder (lines, price sources, warranty / recall coverage, good-better-best) | `/dealer/service/{id}/estimate` | A7 `build_estimate`, Code Interpreter totals (AR-2) | `DEALER_ServiceEstimates` | Parts + labor connectors, warranty connector | §5.20.4 |
| Digital estimate approval (line by line, photo evidence, advisor script) | My Garage › Service, SMS magic link, advisor view | A7 `present_estimate`, `record_approval`, `explain_finding` | `DEALER_ServiceEstimates`, `CUST_Consent` | SES / SNS, Connect (opt.) | §5.20.4, §5.12 |
| Declined-work register + follow-up cadence | Service Desk › Declined, Journey Ops | A7 `schedule_declined_followup` → A2 nurture (service templates) | `DEALER_DeclinedWork`, `DEALER_NurturePlans` | EventBridge Scheduler | §3.7 |
| Parts status on RO (on hand, ordered, ETA, back-order, held ROs) | RO view, Service board | A7 `track_parts` | `DEALER_Parts`, `DEALER_PartsOrders` | Parts inventory / OEM parts connector | §5.20.5 |
| Line status, QC, ready notification, pickup / delivery scheduling | RO view | A7 `update_line_status`, `notify_ready` | `DEALER_RepairOrders`, `DEALER_Appointments` | DMS (RO close), OutreachProvider | §5.20.5 |
| Repurchase/upgrade handoff (equity, lease-end, repair-vs-replace) | RO view, Service Lane strip | A7 `handoff_to_sales` → A4 `compute_equity` → A2 purchase journey (`source=service_lane`) | `DEALER_Journeys`, `DEALER_Handoffs` | EventBridge | §2.1.1, §5.8 ext. |
| Service history per VIN (ROs, inspections, declined work, recalls) | `/dealer/service/history/{vin}`, My Garage › Ownership | Read model | `DEALER_RepairOrders`, `DEALER_Inspections`, `DEALER_DeclinedWork` | DMS connector (historical import) | §5.20.6 |
| Service Manager cockpit (lane load, RO cycle time, MPI rate, close rate, parts-wait, CSI) | `/dealer/service/cockpit`, Store Cockpit › Service tab | Read models (event subscribers) | Lake via Athena | QuickSight tiles | §5.20.7, §5.14 |
| Service-lane upsell brief | Assistant › Service Lane | A3 (AgentCore) tool reading A7 findings, grounded on Quick Index | `DEALER_Inspections`, `DEALER_DeclinedWork` | AgentCore Runtime; Quick Index | §3.3, §5.8 ext. |

#### 2.6.3 OEM Workspace features (light theme, Next.js shell + embedded Amazon Quick)

| Feature | UI | Agent / Logic | Data | Integration | Spec |
|---|---|---|---|---|---|
| Today: District Pulse KPIs, week at a glance, quick actions | `/oem/today` | A6 `district_pulse` Flow | RDR lake (Athena), `OEM_Alerts` | QuickSight KPIs | §5.9 |
| Dealer Performance scorecard + gap analysis | `/oem/performance` | A6 `get_rdr_actuals`, `analyze_performance_gaps` | `rdr/` lake, `csi/` lake | Quick Flow, Bedrock | §5.9 |
| Composite Analysis vs peer | `/oem/composite` | A6 `compute_composite` | RDR, DMS, CSI | Quick | §5.9 |
| Contact Reports (dictation → structured → CRM) | `/oem/contacts` | A6 `create_contact_report` Flow | `OEM_ContactReports`, `DEALER_Tasks` | Amazon Transcribe, CRM connector | §5.9 |
| Action Plans (milestones, owners, status) | `/oem/action-plans` | A6 `generate_action_plan` Flow | `OEM_AccountPlans` (type action_plan) | Approvals (publish) | §5.9 |
| AI Alerts (HIGH / MEDIUM / LOW) | `/oem/alerts`, Store Cockpit | A6 `detect_alerts` (rules + Bedrock) | `OEM_Alerts` | EventBridge `alert.raised`, Notification Hub | §5.9 |
| Weekend Tracker + prediction | `/oem/weekend` | A6 `predict_weekend` | DMS deliveries, CRM appointments, PY | Quick | §5.9 |
| Conquest Intel (competitor offers) | `/oem/conquest` | A6 `get_competitive_offers` | `marketintel/` lake | Market-intel + ads connectors | §5.9 |
| EV Readiness | `/oem/ev-readiness` | A6 `get_ev_readiness` | `OEM_Compliance`, DMS | Training LMS / facility connector | §5.9 |
| Compliance (DND / franchise) | `/oem/compliance` | A6 `get_compliance` | `OEM_Compliance` | Compliance connector | §5.9 |
| Scenario Planning (4 levers → 5 projections) | `/oem/scenario` | A6 `simulate_scenario` (deterministic model + Bedrock narrative) | RDR, inventory | Quick; GR-001/005 checked | §5.9 |
| Meeting in a Box (10 meeting types → deck) | `/oem/meeting-box` | A6 `build_meeting_package` Flow | `CUST_Documents`, S3 `app-docs` | Quick Flow → PPTX/PDF | §5.9 |
| Sales Ops Comms (AI search, digest, forward) | `/oem/comms` | A6 `search_comms`, `consolidate_comms`, `forward_comms` | `OEM_Comms`, Quick Index | SES | §5.9 |
| Report Library (relevance ranking) | `/oem/reports` | A6 `rank_reports` | Quick Index | Quick | §5.9 |
| AI Guardrails admin (rules, blocks, demo) | `/oem/guardrails` | `check_guardrails` (Bedrock Guardrails + rules Lambda) | `OEM_GuardrailEvents`, `PLAT_ConfigGuardrails` | Bedrock Guardrails | §3.6.1 |
| Account Plan / Opportunity Brief / Win Strategy (versioned, cited) | `/oem/accounts/{id}` | A6 Quick Flows + Quick Research | `OEM_Accounts`, `OEM_AccountPlans`, `OEM_CIPrograms` | CRM, RDR, C&I, market-intel connectors; Quick Research | §3.6 |
| Planner Q&A chat (persistent right panel, voice) | All OEM views | A6 `ask` (Quick agent) | Quick Index | Amazon Quick, Transcribe | §5.9 |
| Region View + scope switcher | `/oem/region` | A6 roll-ups | RDR, `OEM_Alerts`, `OEM_ContactReports` | Scope switcher filters the view (Quick content is not row-scoped) | §5.9 ext. |
| District Sales vs Target dashboard | Embedded | – | Lake via Athena | QuickSight | §4.3 |

#### 2.6.4 Marketing Studio features (Next.js + QuickSight + AgentCore)

| Feature | UI | Agent / Logic | Data | Integration | Spec |
|---|---|---|---|---|---|
| Campaign ROI by campaign / platform / dealer; funnel drop-off by source | `/marketing/campaigns` | A5 `get_campaign_spend`, `get_journey_funnel`, `attribute_conversions` | `OEM_Campaigns`, `DEALER_Touchpoints`, `events/` lake | Ad-platform connectors, Athena, QuickSight embed | §3.5, §5.7 |
| Budget-shift recommendations with expected lift | Campaigns | A5 `recommend_budget_shift` (Bedrock) → Approval | `DEALER_Approvals` | Step Functions | §3.5, §4.8 |
| Natural-language analytics ("ask") | Campaigns | A5 `ask_quick` | Quick Q topics | Amazon Quick | §3.5 |
| Audiences (Personalize segments → ad export) | `/marketing/audiences` | A5 `get_personalize_segments` | Personalize user-segmentation | Ad-platform connectors | §3.5, §4.4 |
| Journey Ops: nurture policy, templates (preview, A/B), consent rules, customer surface content, nurture analytics | `/marketing/journey-ops` | Config service; A2 reads active version | `PLAT_ConfigNurture` (versioned) | EventBridge `config.published` | §5.16 |

#### 2.6.5 Admin Console features (Next.js)

| Feature | UI | Logic | Data | Integration | Spec |
|---|---|---|---|---|---|
| Users & roles, Quick provisioning status | `/admin/users` | Cognito group mgmt; JIT/SCIM to Identity Center | `PLAT_Users`, Cognito `app-staff` | IAM Identity Center | §5.17, §8.1 |
| Org hierarchy (OEM ▸ region ▸ district ▸ group ▸ dealer, PMA) | `/admin/org` | Scope claim source | `PLAT_OrgHierarchy` | Lambda authorizer, OpenSearch filters | §5.17, §8.1 |
| Market & brand config (both themes) | `/admin/market` | MarketConfig service | `OEM_Markets`, `OEM_StateRules` | – | §2.5 |
| Connectors (sandbox/live, credentials, health, last sync) | `/admin/connectors` | Connector registry | Secrets Manager | EventBridge `connector.sync_failed` | §4.1 |
| Feature flags (`ENABLE_CONNECT`, Personalize, Quick embed) | `/admin/flags` | Config service | `PLAT_Config*` tables; deploy-time environment variables (§7.4) | – | §7.4 |
| Guardrail rules + suggestion-matrix editors with test harness | `/admin/policies` | `check_guardrails` | `PLAT_ConfigGuardrails`, `PLAT_ConfigSuggestions` | Bedrock Guardrails | §3.6.1, §5.17 |
| Audit export (journey events, guardrail events, admin actions) | `/admin/audit` | Athena query → S3 export | `DEALER_JourneyEvents`, `OEM_GuardrailEvents`, lake | Athena | §8 |
| Data-subject requests (download / delete across stores) | `/admin/data-requests` | Step Functions across DynamoDB, Personalize, Quick Index | `CUST_Consent` | Personalize, Quick APIs | §8.2 |
| Entity merge queue (low-confidence ID matches) | `/admin/entities` | Entity registry | `PLAT_EntityMap` | – | §4.7 |

#### 2.6.6 Cross-cutting platform features (all apps)

| Feature | Implementation | Data | AWS services | Spec |
|---|---|---|---|---|
| Unified Shell (bar, app switcher, scope pill, user menu) | React package `ui/src/shell/*` mounted by every authenticated app; neutral palette | `PLAT_Users` | – | §5.18 |
| Resource URL contract + role-aware resolver | `resourceUrl()` (TS + Python), `GET /nav/resolve`, lint rule | – | API Gateway, Lambda | §5.18.2 |
| Context envelope ("You're here because…") | Signed JWT `?ctx=` | `DEALER_Handoffs` | – | §5.18.3 |
| Notification Hub (in-shell bell/inbox + email/SMS/push) | Event subscribers + rules | `DEALER_Notifications`, `PLAT_ConfigNotifications` | EventBridge, SES, SNS, Connect (opt.) | §5.18.4 |
| Tasks (unified, SLA, escalation) | `POST /tasks` used by A2/A3/A6 | `DEALER_Tasks` | EventBridge `task.*` | §5.18.4 |
| Global search (⌘K) | Streams → index; scope-filtered | OpenSearch Serverless (structured) + Quick Index (documents) | OpenSearch Serverless, DynamoDB Streams | §5.18.5 |
| Approval workflow | Step Functions state machine; Tasks UI | `DEALER_Approvals` | Step Functions, EventBridge | §4.8 |
| Document service (viewer, sharing, retention) | Pre-signed viewer component | `CUST_Documents`, S3 `app-docs` (KMS, versioning, lifecycle) | S3, KMS | §4.9 |
| Event backbone + catalog | Bus `app-{stage}`, Schema Registry, Streams bridge, DLQs, archive to lake | `events/` in S3 | EventBridge, Lambda, Firehose | §4.6 |
| Entity registry / ID mapping | Connector boundary translation | `PLAT_EntityMap` | – | §4.7 |
| Identity fabric (staff SSO, Quick embed SSO, customer passwordless) | Cognito → Identity Center SAML; Lambda authorizer with scope claims | Cognito `app-staff`, `app-customers`, `PLAT_OrgHierarchy` | Cognito, IAM Identity Center | §8.1 |
| RBAC + scope authorization | `(role, action, scope)` evaluated once per token; mirrored to search filters | `PLAT_OrgHierarchy` | Lambda authorizer | §8.1 |
| Market parameterization | `MarketConfig` in every agent payload; `market.py`; `MarketContext`; ESLint no-literal-units | `OEM_Markets`, `OEM_StateRules` | – | §2.5 |
| Outreach abstraction | `OutreachProvider` (Basic = SES/SNS/tasks; Connect optional) | `CUST_Messages`, `CUST_Consent` | SES, SNS, Amazon Connect (opt.) | §4.5 |
| Guardrails (agent output safety) | Bedrock Guardrails on A1/A3/A6/A7 + GR-001…008 rules Lambda | `OEM_GuardrailEvents` | Bedrock Guardrails | §3.6.1, §3.7, §8 |
| Autonomy levels per agent action (observe / recommend / act_with_approval / act_and_notify) | `autonomy_level(action_id, scope)` check before every effectful tool; Admin › Autonomy editor; hard rules excluded | `PLAT_ConfigAutonomy` | – | §4.10.1 |
| Event-driven agent triggers | Per-agent subscription table; schedules (`app-nurture-tick`, `app-service-followup-tick`, 05:00 local shop-load, weekly district cycle, nightly predictive scan and store digest); every schedule the agent panel shows as "Scheduled" ships enabled for every market | – | EventBridge, EventBridge Scheduler | §4.10.2 |
| Outcome loop (every recommendation shown → accepted → converted) | Shell writes on render/click; subscriber joins conversion events; agents rank by aggregates | `DEALER_Recommendations` | EventBridge, lake `recommendations/` | §4.10.3 |
| Data classification C1–C4 enforcement | Field tags; export jobs drop/hash C3, refuse C4; trace redaction | – | – | §8.2 |
| Correlation id across stacks | `x-app-correlation-id` from shell through API, agents, Gateway, Quick, events | – | CloudWatch Logs Insights, AgentCore observability | §8 |
| Observability & alarms | Dashboards: agent latency, tool errors, objection latency, stalled journeys, DLQ depth, connector failures | – | CloudWatch, AgentCore observability | §8 |
| Analytics lake | Streams → Firehose → S3 Parquet; Glue; Athena db `app`; prefixes `events/ rdr/ marketintel/ leads/ csi/` | S3 lake | Firehose, Glue, Athena, QuickSight | §4.3 |
| Personalization models | Dataset group `app-vehicles`: ranking, similar-items, user-segmentation | `DEALER_Signals`, `OEM_Catalog` | Amazon Personalize | §4.4 |
| Agent-to-agent orchestration | A2 supervisor (A2A to AgentCore agents; Gateway tool to Quick agents/flows) | `DEALER_SalesPackages`, `PLAT_AgentTraces` | AgentCore Runtime, Memory, Gateway | §3.2, §4.3.1 |
| RAG knowledge base (semantic grounding) | `KnowledgeBase` interface (`app/services/platform/knowledge.py`): `LocalKnowledgeBase` (keyword overlap over the DynamoDB knowledge collections, offline) / `BedrockKnowledgeBase` (Amazon Bedrock Knowledge Bases `Retrieve` over the 538-document corpus, Titan Text Embeddings V2, S3 Vectors store) when `INTEGRATION_MODE=aws` | Vector store | Amazon Bedrock Knowledge Bases, Amazon S3 Vectors | §9A AR-1 |
| Financial computation sandbox | A1 TCO, A4 equity/payment-delta, A3 payment math run in-sandbox with inputs+code+result returned | – | AgentCore Code Interpreter | §9A AR-2 |
| Live market data | A4 market comps, A6 external research (when Quick Research absent) | – | AgentCore Browser (allow-listed) | §9A AR-3 |
| Voice assistant | Shared `useVoiceAssistant` hook: A3 live transcript, A6 dictation, optional chat voice input; transcript-replay demo mode | – | Amazon Transcribe Streaming, Amazon Polly | §9A AR-7 |
| Plays (orchestration templates) | Named multi-agent workflows ("Full Prep", "Handle Objection", …) triggerable on a journey → Sales Package | `DEALER_Plays`, `DEALER_PlayRuns` | AgentCore (A2) | §9A AR-10 |
| Mobile layouts | Responsive breakpoints; shell → bottom tab bar | – | – | §5.19 |
| Accessibility | WCAG 2.1 AA, keyboard, `aria-live` for objection/AI Log | – | – | §5.11 |
| Deployment | `deploy/deploy.sh`: ordered, idempotent stages (`cognito` … `waf`), dry run unless `--apply`; `deploy/bootstrap.sh` (fresh account), `deploy/verify.sh`, `deploy/destroy.sh`; `deploy/guidance-deploy.sh` one-click; per-deployer state in `app/infra/*-config.json`, no script patching | – | Python boto3 direct-API deployers (`app/infra/`); agents as zipped code bundles on the AgentCore managed Python 3.12 runtime (no Docker, no ECR) | §7 |

#### 2.6.7 Stack summary by layer

| Layer | Technology | Used by |
|---|---|---|
| Front-end | Next.js 14 (static export), React 18, Tailwind + design tokens (dark dealer / light OEM / brand customer), Mantine/Radix primitives, framer-motion, Recharts | All five apps + shell |
| Embedded analytics / agents | Amazon Quick Suite: Quick agent + Flows + Research (A6), Quick Index (retrieval for A3/A7 AgentCore agents), QuickSight (registered-user embedding; access by Quick group, not row-scoped) | OEM Workspace (A6), Marketing Studio (A5), Store Cockpit tiles; A3 Assistant is native Next.js over AgentCore |
| Agents | Strands Agents SDK (Python 3.12), one zipped code bundle per agent (Linux arm64 wheels) on the Amazon Bedrock AgentCore managed runtime; AgentCore Gateway, Code Interpreter, Observability; AgentCore Memory (target) | A1, A2, A3 (floor + assistant), A4, A5 recommendations, A7 |
| Foundation models | Amazon Bedrock (Claude Sonnet family), Bedrock Guardrails | All agents |
| ML | Amazon Personalize (ranking, similar-items, segmentation) | A1, A5 |
| Speech | Amazon Transcribe Streaming (floor, dictation), Amazon Polly | A3, A6 |
| API | API Gateway REST, Lambda (Python 3.12) with shared layer, Lambda authorizer | All apps |
| Orchestration / eventing | EventBridge (bus, Schema Registry, Scheduler), Step Functions (approvals, data requests), DynamoDB Streams | Platform |
| Operational data | DynamoDB on-demand (68 tables, PITR), AgentCore Memory | All |
| Search | OpenSearch Serverless (entities), Quick Index (documents) | Shell global search, A3/A6 |
| Analytics data | S3 lake (Parquet) via Kinesis Data Firehose, AWS Glue, Amazon Athena | A5, A6, Store Cockpit, Admin audit |
| Documents | S3 `app-docs` (SSE-KMS, versioning, lifecycle) | A6 decks/plans, F&I, Delivery, My Garage |
| Identity | Amazon Cognito (staff + customer pools), IAM Identity Center (Quick federation), optional corporate IdP federation | All |
| Messaging | Amazon SES, Amazon SNS; Amazon Connect + Contact Lens (optional module) | A2 outreach, Notification Hub |
| Hosting / edge | S3 + CloudFront (+ CloudFront Function URL rewrite) | All Next.js apps |
| Secrets / config | AWS Secrets Manager (connector credentials), `app/infra/*-config.json` deploy handles passed as environment variables, `deploy/config.env` operator settings, `PLAT_Config*` versioned tables | Connectors, agents, Journey Ops, Admin |
| Integrations (behind `Connector` interface, sandbox + live) | Salesforce (CRM), CDP, Configurator, CDK (F&I, DMS), Cox/KBB/Black Book (valuation), NHTSA vPIC, Adobe/Google/Meta (ads), lead providers (ADF), dealer calendar, OEM incentive feed, RDR lake, C&I system, S&P Global Mobility / R.L. Polk, CSI survey, training LMS / facility compliance, Sales Ops comms | Agents and Lambdas |
| Observability | CloudWatch (logs, dashboards, alarms), AgentCore observability, correlation id | Platform |
| Deployment | Python boto3 direct-API deployers in `app/infra/`, ordered by `deploy/deploy.sh`; agent code bundles on the AgentCore managed runtime (no Docker, no ECR) | – |

Not every component in this target stack is provisioned by `deploy/`. Amazon Personalize, Amazon Transcribe Streaming, Amazon Polly, OpenSearch Serverless, Amazon Connect, IAM Identity Center and AgentCore Memory are target or optional components: the code falls back to a local implementation when they are absent. [`docs/as-built/architecture.md`](../as-built/architecture.md) lists what a deployment creates.

---

## 3. Agent Specifications

All agents: Python 3.12, Strands Agents SDK as the target, deployed to AgentCore Runtime. Each custom agent (A1, A2, A3, A4, A7, and A5's recommendation step) is its own runtime, `retail_ops_<env>_<code>_<slug>`, packaged as a Linux arm64 direct code deployment zip rather than a container (ADR 0007, ADR 0009); A6 stays in Amazon Quick. Each agent exposes an A2A-compatible `invoke` and is registered with the supervisor: the request is the agent registry's payload (`kind: tool` with `tool` and `args`, `kind: agent` for a reasoning turn, or `kind: job` for a scheduled run), carries the caller's claims and a `correlation_id`, and returns one self-citing result. At launch A3 is the Strands reasoning agent and the other agents serve their tools and jobs deterministically; each is promoted to Strands reasoning on its own when its golden suite shows parity (ADR 0002, ADR 0006). Every tool result is the only allowed source of facts (no fabrication rule inherited from the current `objection_handler` prompt).

### 3.1 A1 – Personalized Vehicle Discovery

| | |
|---|---|
| Use case | AI-generated recommendations of vehicles, trims, and packages based on lifestyle, budget, and browsing history |
| AI category / delivery | Gen AI / Custom app on AgentCore |
| Target | OEM digital retail teams; dealer-group e-commerce (Marketing, Digital Commerce, Sales) |
| Data sources | Configurator / product catalog, VIN inventory, web behavior / CDP, pricing and incentives |

**Purpose.** Convert lifestyle, budget, and browsing history (CDP profile, session signals, campaign source, stated needs) into a ranked shortlist with reasons, a recommended trim + package configuration, and VIN-level availability. Serves both the customer-facing OEM web/app and the dealer console.

**Lifestyle model.** A1 infers a `LifestyleProfile` before ranking: `{ household_size, commute_mi_per_day, road_trip_frequency, towing_or_hauling, outdoor_activities[], tech_affinity, safety_priority, powertrain_openness (ice|hybrid|ev|undecided), budget {msrp_max, monthly_max, down}, ownership_style (buy|lease) }`. Derived from CDP attributes, browsing (content read, configs, compares), a short conversational intake (≤ 4 questions when signals are thin), and CRM data when the customer is known. The profile is stored in AgentCore Memory and reused by A2/A3.

**Inputs.** `customer_id | anonymous_id`, `session_context` (pages, configs, compares, dwell), optional natural-language need statement, `market_config`, `state`, dealer radius.

**Outputs.** `DiscoveryResult { lifestyle_profile, shortlist[≤3]{model, trim, packages[], score, reasons[] (lifestyle-anchored), msrp, est_monthly {buy, lease}, incentives_applied[], ev_ice_flag, vin_availability {in_stock_count, nearest_dealer, eta_days}}, recommended_config {trim, packages, options, price_delta_vs_base}, comparison_table, talk_tracks[], next_best_action (save_build | book_test_drive | request_quote) }`

**Tools.**
| Tool | Source | Notes |
|---|---|---|
| `get_customer_profile(customer_id)` | CRM + CDP connector | Unified profile, preferences, lifecycle |
| `get_session_signals(anonymous_id)` | CDP connector | Configs, compares, content read, dwell time |
| `infer_lifestyle_profile(profile, signals, answers)` | Bedrock | Produces `LifestyleProfile` with confidence per field; asks intake questions only for low-confidence fields |
| `get_personalized_ranking(user_id, candidate_vehicle_ids)` | Amazon Personalize (Ranking recipe) | Falls back to rules if no model |
| `get_similar_vehicles(vehicle_id)` | Personalize (Similar-Items) | Cross-shop suggestions |
| `recommend_trim_and_packages(model, lifestyle_profile, budget)` | Configurator connector + Bedrock | Picks trim and packages that satisfy lifestyle needs within budget; explains each package by need (e.g., tow package ↔ boat) |
| `search_inventory(filters)` | Dealer Inventory connector (VIN-level) | Exact-config and near-match VINs by dealer radius, in-transit ETA |
| `get_configurator_options(model)` | Configurator connector | Trims, packs, options, prices |
| `price_build(config, state, incentives)` | Configurator + `OEM_Incentives` + `OEM_StateRules` | MSRP, destination, est. tax/fees, applicable incentives, est. monthly buy/lease |
| `build_tco_comparison(vehicle_a, vehicle_b, usage, market_config)` | Internal calc | EV vs ICE 5-year TCO in market units (US: $/mi, MPG vs MPGe, state fuel/energy prices, federal + state EV credits) |
| `get_incentives(vehicle_id, state, buyer_type)` | `OEM_Incentives` | Federal 30D eligibility (MSRP cap, AGI cap, assembly), state rebates, utility rebates |
| `record_shortlist(journey_id, shortlist)` | Journey store | Advances DISCOVER → CONFIGURE |
| `rescore_profiles(event)` *(proactive, 2026-09-22)* | Subscriber to `inventory.changed`, `incentive.changed`, `signal.recorded` | Re-ranks every saved profile / build the event touches (VIN arrived within radius, price or incentive change puts a build in budget, better-fit trim); emits `discovery.match_found { journey_id, reason, evidence_refs }` for A2 to act on. Never messages the customer itself |

**Proactive behaviour (§4.10.2).** A1 is not only a session responder. Every saved profile is re-scored on every relevant event; matches become `discovery.match_found` events that A2 turns into a touch (default autonomy `recommend`: shown to the advisor as a card; dealer may raise to `act_and_notify`). Each shortlist and match is a row in `DEALER_Recommendations` so A1 learns which reasons convert.

**Guardrails.** Never quote a price not returned by a tool. Always show at least one in-budget option. Mark EV/ICE explicitly. All figures formatted via `market_config` (no hard-coded currency or units).

### 3.2 A2 – Purchase Journey Orchestration Agent (Supervisor + Nurture)

| | |
|---|---|
| Use case | Engages prospects over time with personalized messages, answers questions, and nudges them toward purchase or test drive |
| AI category / delivery | **Agentic** / Custom app on AgentCore; **multi-agent workflow: yes** |
| Target | OEM digital retail and CX; dealer groups (Sales, Marketing, Digital Commerce) |
| Data sources | CRM, CDP, configurator, captive finance / F&I, trade-in valuation, DMS |

**Purpose.** Single owner of the funnel across weeks, not just a request/response router. A2 has two faces:

1. **Supervisor** (synchronous): maintains journey state, decides which specialist acts next, validates handoff completeness, assembles Sales Packages for the dealer console.
2. **Nurture agent** (asynchronous, long-running): for every open journey it holds a per-customer plan, sends personalized messages on the customer's preferred channel, answers inbound questions with grounded facts, and nudges toward the next commitment (save build → book test drive → get pre-qualified → accept trade → sign). It runs on a scheduler (EventBridge) and on inbound events; state and memory live in AgentCore Memory + `DEALER_Journeys`.

**Inputs.** Journey events (`stage_changed`, `lead_created`, `config_saved`, `visit_booked`, `finance_requested`, `trade_submitted`, `deal_booked`, `delivered`), inbound customer messages (web chat, email reply, SMS reply, Connect chat when enabled), advisor queries, scheduler ticks, consent/preference changes.

**Outputs.**
- `OrchestrationPlan { journey_id, current_stage, next_stage, assigned_agent, required_context[], missing_context[], actions[] }`
- `SalesPackage` (aggregated specialist outputs) for the dealer console
- `NurturePlan { journey_id, goal (test_drive|prequal|trade|sign), cadence, next_touch_at, channel, message_intent, stop_conditions[] }`
- `CustomerReply { text, citations[] (tool results), suggested_actions[] (book_test_drive, view_build, start_prequal, get_trade_estimate), escalate_to_human: bool }`

**Tools.**
| Tool | Purpose |
|---|---|
| `get_journey(journey_id)` / `update_journey_stage(journey_id, stage, evidence)` | State machine with audit |
| `create_journey(customer_id, source, context, previous_journey_id?)` | Creates a purchase journey in DISCOVER; enforces the single-active rule (§2.1), marks the customer's OWN journey `superseded_by`; called by A1 shortlist save, Lead Desk intake, and A7 `handoff_to_sales` (`source=service_lane`) |
| `validate_handoff(journey)` | Checks required fields per stage (see §2.1); blocks transitions with missing context; at AGREE also requires the credit-compliance gate (OFAC, Red Flags, adverse action) |
| `invoke_agent(agent_name, payload)` | A2A call to A1/A3/A4/A5; Gateway tool call for Quick-hosted A6 (parallel where independent) |
| `aggregate_package(results)` | Combine into `SalesPackage` |
| `get_customer_memory(customer_id)` / `update_customer_memory(customer_id, facts)` | AgentCore Memory: lifestyle profile, stated objections, preferred channel/time, vehicles considered, promises made |
| `plan_nurture(journey, memory, consent)` | Builds/refreshes `NurturePlan`; respects quiet hours, frequency caps, TCPA/CAN-SPAM consent flags |
| `compose_message(journey, intent, channel, market_config)` | Personalized message grounded in the customer's saved build, incentives expiring, inventory arrival, trade value change; channel-specific length/format; brand voice from OEM config |
| `answer_customer_question(question, journey)` | Grounded Q&A: pulls from configurator (specs, packages), `OEM_Incentives`, F&I connector (payment ranges, no credit decisions), inventory, trade valuation, dealer hours. Refuses/escalates on legal, credit-decision, or safety-recall questions |
| `nudge(journey, target_commitment)` | Chooses the smallest next commitment and attaches a one-click action (test-drive slots from dealer calendar, prequal link, trade-estimate link) |
| `book_test_drive(journey, dealer_id, slot)` | Dealer calendar connector (sandbox: `DEALER_Appointments`); writes `visit_booked` |
| `schedule_outreach(customer_id, channel, template, when)` | Channel-agnostic. With `ENABLE_CONNECT=true` → Amazon Connect outbound (voice/SMS/chat). Default → SES email / SNS SMS + dealer task. Channel availability read from `OutreachConfig` |
| `detect_intent_shift(events, messages)` | Flags budget change, new competitor considered, went-cold, ready-to-buy; triggers re-plan and dealer alert |
| `create_task(assignee, journey_id, text, due)` | Dealer task list |
| `handoff_to_human(journey, reason, transcript)` | Creates dealer/BDC task with full context; pauses automated nurture |
| `select_next_best_action(journey)` *(2026-09-22)* | Chooses message / channel / timing / play by comparing candidate actions against `DEALER_Recommendations` aggregates for similar `context_hash` (stage, source, days-in-stage, signals) — bandit-style exploration inside Journey Ops policy bounds; the chosen action and its alternatives are logged as recommendations |
| `run_play(play_id, journey_id, trigger)` *(2026-09-22)* | Executes an AR-10 Play without a click when a trigger event fires: `journey.stalled` → *Callback Brief*, `discovery.match_found` → *Recommend Vehicle*, `service.handoff_to_sales` → *Full Prep*, `lease.end_window_entered` / `equity.crossed_positive` → *Upgrade Pitch*; autonomy level per play |
| `publish_shift_list(dealer_id, role)` *(2026-09-22)* | At shift start creates the ranked call / follow-up list as Tasks for BDC and advisors (replaces the on-demand Quick Flow as the source; the Flow remains for ad-hoc questions) |
| `propose_policy_change(evidence)` *(2026-09-22)* | Weekly: drafts template, cadence, or suggestion-matrix changes where outcomes under-perform, into Journey Ops as a review item (never self-publishes) |
| `write_trace(journey_id, span)` | AgentTraces table for the UI's AI Log |

**Nurture policy (deterministic guardrails around the LLM).**
- Max 2 automated touches / week per customer; none during quiet hours (local time from `state`); honor unsubscribe within one message.
- Every message must reference at least one customer-specific fact from memory or journey (no generic blasts).
- Stop conditions: `deal_booked`, explicit opt-out, `handoff_to_human`, competitor purchase detected, 45 days without any engagement (then move to A4 long-term upgrade pool).
- Any question touching credit approval, legal terms, or safety recalls → `handoff_to_human`.

**Routing rules (deterministic before LLM).**
- Stage DISCOVER/CONFIGURE → A1
- Advisor live query, objection, incentive, deal structure → A3 (real-time path on AgentCore; suggestion content via Quick A3 Gateway tool)
- Any trade/lease/upgrade intent → A4 (run in parallel with A3 when both apply)
- Campaign/attribution/budget question → A5
- Dealer or fleet account-level question (account plan, opportunity brief, win strategy) → A6 via Gateway tool
- Inbound customer message → `answer_customer_question` → `nudge`
- Scheduler tick → `plan_nurture` → `compose_message` → `schedule_outreach`
- Stalled journey (no event > SLA) → `detect_intent_shift` → `schedule_outreach` + dealer task

### 3.3 A3 – Dealer Sales Advisor

| | |
|---|---|
| Use case | Contextual suggestions for sales reps or service agents based on customer lifecycle stage and recent activity |
| AI category / delivery | Gen AI + tools / **Custom app on AgentCore** — one Strands agent covering both the live-floor streaming path and the assistant chat/flows; **Quick Index used as a retrieval tool** for grounding (not a Quick-hosted agent). Reframed 2026-09-23 (§13); was split Quick + AgentCore |
| Target | Dealer sales and BDC teams; OEM retail marketing (Sales, Marketing) |
| Data sources | CRM, DMS, OEM incentive feeds, digital retail and lead data; Quick Index (documents) |

**Purpose.** Give every dealer-facing role the right next suggestion for the customer in front of them, based on where that customer is in the lifecycle and what they just did. **One AgentCore agent, one suggestion engine, two surfaces:**

1. **Assistant** (chat + flows): answers "what should I say / do next for this customer," drafts follow-ups, summarizes account history, and runs the *Service-lane upsell brief* and ad-hoc "who should I call about X" queries. Grounds every answer in **Quick Index** retrieval (`retrieve_context`, R3 content as a tool) over CRM notes, DMS service-history summaries, OEM incentive bulletins, product guides, and lead data, so answers cite source records. The *Daily BDC call list* is produced by A2 `publish_shift_list` as Tasks (§4.10.2).
2. **Live floor copilot**: the real-time path driving the reference UI (`sales-advisor-v2.html`): Customer 360, recommendations, objection detection from the live transcript, competitive intel, incentives, cross-sell, deal structuring, with sub-2-second streaming. It shares the same suggestion logic and Quick Index grounding as the assistant surface, so a suggestion and its live-floor delivery cannot diverge.

Both surfaces are the same deployed agent; the difference is the transport (streaming vs request/response), not the brain. This is the R1+R2 case in §2.2.2: because the two surfaces share suggestions and (via A2) journey state, and one needs sub-2 s streaming, the whole agent is custom AgentCore and consumes Quick Index rather than being Quick-hosted (the A7 pattern for procedures/TSBs).

**Lifecycle-stage suggestion matrix.** Suggestions are conditioned on `journey.stage` (or, for owners, the ownership lifecycle) and the most recent activity:

| Lifecycle stage | Role | Trigger (recent activity) | Suggestion types |
|---|---|---|---|
| New lead (DISCOVER) | BDC | Web lead, chat transcript, campaign click | First-response script referencing the exact vehicle/content viewed; best time to call; test-drive offer |
| CONFIGURE | Sales | Saved build, repeated compares | Configuration talk track; in-stock near-match VINs; incentive expiring |
| QUALIFY | Sales | Showroom visit, test drive | Discovery questions still unanswered; objection pre-emption; payment range to open with |
| FINANCE / TRADE_IN | Sales / F&I | Prequal started, trade estimate viewed | Deal structure options; trade equity framing; F&I product fit (GAP, VSC) |
| AGREE / DELIVER | Sales | Deal pending, delivery scheduled | Delivery checklist; accessory add-ons; referral ask |
| OWN – active | Service advisor | Service appointment booked, recall notice | Service-lane upsell brief; loyalty offer; EV charging tips |
| OWN – equity / lease-end | BDC / Sales | Mileage, months remaining, positive equity (from A4) | Upgrade outreach with payment-delta script |
| Went cold | BDC | 14+ days no activity | Re-engagement angle from last known interest; hand to A2 nurture |

**Inputs.** `journey_id` or `customer_id`, role (`sales | bdc | service`), live transcript chunks (Transcribe, floor path only), advisor prompts, presented-vehicle events, lifecycle context from `DEALER_Journeys` / DMS.

**Outputs.** `LiveAssistPayload { customer_360, lifecycle_stage, contextual_suggestions[] {text, why (evidence refs), action}, recommendations (from A1), objection {type, quote, suggested_response, success_rate, alternatives[]}, competitive_intel[], incentives[], cross_sell[], deal_options[], ai_log[] }` and, on Quick, `SuggestionBrief` / `CallList` / `ServiceUpsellBrief` documents.

**Tools.**
| Tool | Source |
|---|---|
| `get_customer_360(customer_id)` | CRM + CDP + DMS + Journey (merged) |
| `get_lifecycle_context(customer_id)` | Journey stage or ownership stage (DMS: in-service, months on lease, mileage, recalls) |
| `get_recent_activity(customer_id, window)` | CRM activities, digital-retail/lead events, DMS RO history, campaign clicks |
| `generate_contextual_suggestions(customer_360, lifecycle, activity, role)` | Quick agent / Bedrock; applies the suggestion matrix; every suggestion carries evidence refs |
| `draft_follow_up(customer, channel, intent)` | Quick Flow; email/SMS/call script in OEM brand voice |
| `build_call_list(advisor, date)` | Quick Flow over CRM + DMS + journeys; prioritized with talking points |
| `classify_objection(utterance)` / `get_objection_strategy(type, context)` | Existing `ObjectionStrategies` table |
| `get_competitive_intel(vehicle_id, competitor)` | Internal KB |
| `get_incentives(vehicle_id, customer, state)` | `OEM_Incentives` + OEM incentive feed (connector); federal/state/utility/OEM tiers |
| `get_cross_sell(vehicle_id, profile)` | Personalize + F&I product catalog (GAP, VSC, tire & wheel, prepaid maintenance, home charger install) |
| `structure_deal(vehicle, finance_offer, trade_equity, incentives, state)` | F&I connector (CDK) – returns monthly payment scenarios with state sales tax, doc fees, and TILA/Reg Z disclosure text from `market_config` |
| `push_deal_to_dms(journey_id, deal)` | DMS connector – advances AGREE |
| `record_outcome(journey_id, outcome)` | Outcomes table (feeds A5 + strategy success rates) |

**Real-time behaviour.** Streams partial results; UI updates panel-by-panel. Objection detection runs on every transcript chunk with < 2 s latency budget.

**Proactive behaviour (2026-09-22, §4.10.2).** The floor path stays reactive by design. Around it, A3 works ahead: `precompute_suggestions(journey_id | ro_id)` runs on `journey.stage_changed`, `appointment.booked`, and `ro.opened` so the contextual-suggestion strip and the service upsell brief are ready before the screen opens (no Flow run on demand); every suggestion shown, "Use This," and "Present" is a row in `DEALER_Recommendations`; objection-strategy success rates re-rank live from that table; weekly `propose_matrix_changes()` drafts `PLAT_ConfigSuggestions` edits with evidence for a human to publish. The Daily BDC call list is now produced by A2 `publish_shift_list` as Tasks; the A3 Quick Flow answers ad-hoc "who should I call about X" questions.

### 3.4 A4 – Trade-In & Upgrade Advisor

**Purpose.** Bring trade-in into the same journey; surface upgrade opportunities from the owned fleet (absorbs current `prospecting` use cases: lease expiration, aging vehicle, service frequency).

**Inputs.** `customer_id`, `current_vehicle {vin|make/model/year, mileage, condition}`, target vehicle, lease/loan data.

**Outputs.** `TradeInResult { valuation {low, mid, high, source, expires_at}, payoff, equity_position, upgrade_recommendation, payment_delta, talk_track }`, and for prospecting: `UpgradeProspect[]` with scores and reasons.

**Tools.**
| Tool | Source |
|---|---|
| `get_trade_valuation(vin, mileage, condition, zip5)` | Trade-In Valuation API connector (Cox vAuto/KBB, Black Book); VIN decode via NHTSA vPIC (free) in sandbox; sandbox fake values |
| `get_loan_lease_status(customer_id)` | F&I / DMS connector |
| `compute_equity(valuation, payoff)` | Internal |
| `find_upgrade_prospects(criteria)` | Customers + DMS service history (existing prospecting logic) |
| `estimate_payment_delta(current, target, equity, offer)` | F&I connector |
| `record_trade(journey_id, trade)` | Advances TRADE_IN |
| `monitor_equity(vin | batch)` *(proactive, 2026-09-22)* | Nightly over `DEALER_Vehicles` and on `ro.opened` (fresh mileage), `inspection.submitted` (condition), `service.visit.completed`, market-value refresh; writes `equity_snapshot` to `DEALER_Vehicles`; emits `equity.crossed_positive`, `lease.end_window_entered` (≤ 6 months), `repair_vs_replace_triggered` (estimate ≥ `repair_vs_replace_threshold_pct`) |
| `recommend_acquisition(vin, store_stock_need)` *(2026-09-22)* | Compares a service-drive or trade vehicle against the store's used-inventory demand (A1 shortlist counts, days supply by model) and emits `acquisition.recommended` with a suggested offer range to the Inventory & Trade Desk (default autonomy `recommend`) |

**Proactive behaviour (§4.10.2).** A4 no longer waits for a valuation request or a batch prospecting run. It owns detection of the *upgrade moment* for every owned VIN and the *acquisition moment* for the used-car desk. Events go to A2 (nurture / Upgrade Pitch play) and A7 (repair-vs-replace card); A4 never contacts the customer directly.

### 3.5 A5 – Campaign ROI Optimizer

**Purpose.** Close the loop between ad spend and delivered vehicles. Attribute journeys to campaigns, compute ROI per channel/creative/dealer, recommend budget reallocation, and answer natural-language analytics questions via Amazon Quick.

**Inputs.** Campaign/spend data (Ad Platforms connector), journey events (S3 lake), delivered deals (DMS).

**Outputs.** `CampaignInsight { attribution_model, roi_by_campaign[], stage_dropoff_by_source[], recommendations[] {shift_from, shift_to, expected_lift}, quick_dashboard_url }`

**Tools.**
| Tool | Source |
|---|---|
| `get_campaign_spend(range, platform)` | Ad Platforms connector (Adobe, Google, Meta) |
| `get_journey_funnel(range, dims)` | Athena/Quick over S3 lake |
| `attribute_conversions(model = last_touch|linear|position)` | Internal |
| `recommend_budget_shift(constraints)` | Bedrock reasoning over ROI table |
| `create_quick_dashboard(spec)` / `ask_quick(question)` | Amazon Quick APIs |
| `get_personalize_segments()` | Personalize (User-Segmentation) for audience recommendations |
| `run_nightly_attribution()` *(proactive, 2026-09-22)* | Scheduled: recomputes attribution over the lake, refreshes the Campaign ROI dashboard, and when `recommend_budget_shift` projects lift ≥ threshold creates an `DEALER_Approvals` request (type *budget shift*) with the evidence attached — `act_with_approval`, never applies spend |
| `refresh_audiences()` *(2026-09-22)* | Rebuilds Personalize segments from journey outcomes and pushes to the ad-platform connectors as audience *drafts* (marketing analyst activates) |

**Proactive behaviour (§4.10.2).** A5 runs every night and drafts the decisions; humans keep the budget. That is deliberate: spend changes are a brand and regulatory matter, and the hard rule in §4.10.1 excludes them from `act_and_notify`.

### 3.6 A6 – OEM Sales Administrator

| | |
|---|---|
| Use case | Generate account plans, opportunity briefs, and win strategies based on CRM + external data |
| AI category / delivery | Gen AI / **Amazon Quick** (Quick Research + Quick Flows + Quick Index over CRM and the sales lake) |
| Target | OEM regional / district sales operations; C&I (commercial & incentive) planning teams; Finance (Sales, Sales Operations, Finance) |
| Data sources | CRM, RDR / sales data lake, incentive (C&I) system, market intelligence (S&P Global Mobility, R.L. Polk) |
| Reference UI | `docs/use-cases/DM_Assistant_Demo.html` — "District Manager Assistant" (see §3.6.1) |

**Purpose.** Give OEM field sales (district / regional managers) and C&I planners a generated, sourced plan for each dealer or fleet account instead of a manually assembled deck, plus the day-to-day field toolkit the reference demo shows (contact reports, action plans, alerts, scenario planning, meeting prep). Where A1–A4 work one consumer journey at a time, A6 works at the **account** level (dealer, dealer group, fleet/commercial buyer) and feeds A2 when a consumer journey belongs to a strategic account (e.g., a fleet employee program).

**Persona.** Primary user is the **District Manager (DM)**: owns 8–15 dealers, visits them weekly, is measured on district objective achievement, market share, days supply, CSI, and EV readiness; must comply with franchise-law and internal-policy constraints on what they may say to a dealer. Secondary users: Regional Sales Manager, C&I planner, Sales Ops communications.

**Account model.** `Account { account_id, type (dealer|dealer_group|fleet|commercial), region, district, tier, pma, targets {retail_units, fleet_units, share, csi, days_supply, ev_mix}, actuals (RDR MTD/YTD, PY), composite {sales_efficiency, market_penetration, customer_experience, facility_score, digital_lead_conv, gross_per_unit, csi}, incentive_programs_enrolled[], inventory_position, competitors_in_pma[], ev_readiness {certified, training_pct, chargers_installed, signage}, compliance {dnd_status, facility, signage, training}, contacts[], open_opportunities[], active_action_plans[] }` stored in `OEM_Accounts`; RDR (retail delivery report) actuals and market registrations come from the sales data lake via Athena.

### 3.6.1 What the reference demo establishes (DM Assistant)

The demo is a light-theme OEM workspace: fixed brand header, left sidebar with four nav groups, a center view area, and a persistent right-hand **AI chat panel** ("Ask about dealers, performance, strategy...") with voice dictation. Every view carries an **[AI INSIGHT]** card that explains what the numbers mean and recommends actions. A6 adopts this information architecture; the OEM brand colors become configurable tokens.

| Nav group | View (demo) | What it does | A6 capability / Quick component |
|---|---|---|---|
| Dashboard | **Today** | District Pulse KPIs (MTD retail, objective %, days supply, dealers at risk), "This Week at a Glance," Quick Actions | `district_pulse` Flow + QuickSight KPIs |
| Dashboard | **Dealer Performance** | June scorecard: retail units, obj %, vs PY, vs peer, gross/unit, CSI, digital lead conv; ranked table; *Performance Gap Analysis* insight | `get_rdr_actuals` + `analyze_performance_gaps` |
| Dashboard | **Composite Analysis** | Dealer vs peer group on composite metrics (sales efficiency, market penetration, customer experience, facility, digital, gross/unit, CSI); *Composite Peer Comparison* insight | `compute_composite(account, peer_group)` |
| Field Tools | **Contact Reports** | New Contact Report form (dealer, type, key topics, discussion summary, follow-up items with deadlines, voice dictate); recent reports table; *Contact Report Recommendations* insight | `create_contact_report` Flow (dictation → structured report → CRM activity + tasks) |
| Field Tools | **Action Plans** | Active plans per dealer (e.g., Lead Response Improvement, Facility + EV Certification, Sales Volume Recovery) with milestones, owners, due dates, status; *Action Plan Recommendations* insight | `generate_action_plan` Flow; `OEM_AccountPlans` type `action_plan` |
| Field Tools | **AI Alerts** | Alerts bucketed HIGH / MEDIUM / LOW severity with required action and timeframe; *Alert Prioritization* insight | `detect_alerts` (pattern detection over RDR, CSI, days supply, lead response, compliance) |
| Field Tools | **Weekend Tracker** | Weekend booked vs delivered by dealer, PY weekend, appointments set Monday; *AI Weekend Sales Prediction* | `predict_weekend(district)` over DMS/CRM appointment + delivery feeds |
| Intelligence | **Conquest Intel** | Competitor dealers near each store: make, advertised offer (APR / lease / cash), est. spend, impressions, vs our offer; *AI Competitive Analysis* | `get_competitive_offers(pma)` via market-intel + ads connectors |
| Intelligence | **EV Readiness** | EV certified dealers, training complete, chargers installed, EV units MTD, EV % of sales; per-dealer certification status; *EV Readiness Priorities* | `get_ev_readiness(account)` from DMS/training/facility feeds |
| Intelligence | **DND Compliance** | Dealer compliance (fully / conditional / non-compliant) across facility, signage, training, franchise requirements; *DND Compliance Action Plan* | `get_compliance(account)`; feeds action plans |
| Intelligence | **Scenario Planning** | Sliders: allocation (−50…+50%), incentive per unit (−$500…+$2,000), days supply (±15), market growth (−5…+10%) → projected volume, objective %, share, gross, days supply with deltas; *Scenario Planning Guidance* | `simulate_scenario(levers)` (deterministic model + LLM narrative; estimates only) |
| Intelligence | **Meeting in a Box** | Pick meeting type (dealer / district / area / national month-end close, QBR, new-model launch prep, EV readiness, CSI action, fixed-ops monthly/quarterly, parts report) → AI-assembled slide deck with preview grid | `build_meeting_package(type, scope)` Flow → PPTX/PDF |
| Admin | **Sales Ops Comms** | AI search over sales-ops announcements ("where is the updated stair-step incentive announcement?"); consolidated per-dealer digest; forward to dealers with DM comments | Quick Index over C&I bulletins + `consolidate_comms(dealer)` |
| Admin | **Report Library** | Reports ranked by relevance to district priorities; *Report Reading Priorities* insight | Quick Index ranking over report catalog |
| Admin | **AI Guardrails** | Active rules, blocks this week, last update; rule table GR-001…GR-008; guardrail demo; *Guardrail Effectiveness* insight | Bedrock Guardrails + policy layer (see below) |

**DM guardrail rules (carried over verbatim as policy; enforced before any A6 answer or artifact is returned).**

| Rule | Blocks |
|---|---|
| GR-001 | Wholesale reduction recommendations |
| GR-002 | Dealer termination / separation language |
| GR-003 | Specific pricing guidance to dealers |
| GR-004 | Personnel hiring / firing recommendations |
| GR-005 | Allocation promises or commitments |
| GR-006 | Legal / regulatory interpretations |
| GR-007 | Financial advice beyond standard metrics |
| GR-008 | Competitive disparagement |

Implementation: a `GuardrailPolicy` evaluated on both the user query and the generated output (Bedrock Guardrails denied topics + a rules Lambda for GR-001/GR-005 pattern matching on inventory/allocation language). Blocks are logged to `OEM_GuardrailEvents` (rule id, query hash, user, timestamp) to power the Admin view's "blocks this week" and effectiveness insight. Blocked answers return a safe alternative ("I can show current days supply and objective status; allocation decisions follow the published process").

**Outputs (Quick Flows, each a versioned document in `OEM_AccountPlans` unless noted).**

| Artifact | Contents | Cadence |
|---|---|---|
| **Account Plan** | Objectives vs. targets, sales trend (RDR), share of PMA (Polk registrations), composite vs peer, inventory days-supply by model, incentive utilization, EV readiness and compliance gaps, relationship map, risks, 90-day action plan | Quarterly + on request |
| **Opportunity Brief** | For one opportunity (fleet bid, dealer allocation ask, conquest program): buyer needs, competitive set with S&P forecast context, pricing/incentive levers available from C&I, proposed offer, approval path | On opportunity creation |
| **Win Strategy** | Why we win / why we lose for this account, competitor moves (from market intel and conquest offers), recommended C&I program mix and budget ask with expected units, talk track for the DM (guardrail-checked) | On demand / before key meetings |
| **Action Plan** | Dealer-specific improvement plan (e.g., lead response, facility + EV certification, volume recovery): milestones, owners, due dates, success metric | Created from alerts, contact reports, or DM request |
| **Contact Report** | Structured record of a dealer visit/call: type, topics, summary, follow-ups with deadlines; synced to CRM; suggested next visit focus | Per dealer contact |
| **Meeting Package** | Slide deck for the selected meeting type with district/dealer data, insights, and talking points | Per meeting |
| **District Roll-up / District Pulse** | Aggregation across accounts: target gaps, at-risk dealers, incentive spend vs. lift, weekend performance, alerts summary | Daily pulse; weekly roll-up |
| **Sales Ops Digest** | Consolidated announcements relevant to one dealer with DM comments, ready to forward | Weekly / on demand |

**Tools.**
| Tool | Source | Notes |
|---|---|---|
| `get_account(account_id)` / `list_accounts(region, district)` | CRM connector (Salesforce accounts, contacts, opportunities) | |
| `get_rdr_actuals(account_id, range, by_model)` | RDR / sales data lake (Athena) | Retail deliveries, fleet units, trend |
| `get_market_registrations(pma, range)` | Market intelligence connector (R.L. Polk / S&P Global Mobility) | Segment share, competitor registrations in the account's PMA; sandbox = synthetic |
| `get_market_forecast(segment, region)` | S&P Global Mobility connector | Segment demand outlook used in win strategy |
| `get_incentive_programs(account_id)` / `simulate_incentive_mix(programs, budget)` | C&I incentive system connector | Enrolled programs, utilization, cost per unit; what-if lift estimate |
| `get_inventory_position(account_id)` | Dealer Inventory connector (VIN-level) | Days supply, aging, model mix vs. demand |
| `research_account(account_name)` | Quick Research | External signals: news, expansions, ownership changes, competitor openings; each finding cited |
| `compute_composite(account_id, peer_group)` | RDR lake + DMS + CSI feed | Composite score across sales efficiency, market penetration, customer experience, facility, digital lead conversion, gross/unit, CSI; peer = same-tier dealers in region |
| `analyze_performance_gaps(account_id | district)` | Bedrock over RDR/composite | Ranks gaps vs objective, PY, peer; explains drivers with evidence |
| `detect_alerts(district)` | Rules + Bedrock pattern detection | Emits HIGH/MEDIUM/LOW alerts (e.g., CSI 3-month decline, days supply > target, lead response SLA miss, compliance lapse) with required action + timeframe → `OEM_Alerts` |
| `predict_weekend(district, weekend)` | DMS deliveries + CRM appointments, PY weekend | Booked vs projected delivered per dealer; Monday appointment carry-over |
| `get_competitive_offers(pma)` | Market-intel + ads connectors | Competitor dealer offers (APR / lease / cash), est. spend, impressions vs our advertised offer |
| `get_ev_readiness(account_id)` | DMS + training LMS feed + facility system | Certification, training %, chargers installed, signage, EV units MTD, EV % of sales |
| `get_compliance(account_id)` | Facility / franchise compliance feed (sandbox `OEM_Compliance`) | Fully / conditional / non-compliant by requirement |
| `simulate_scenario(account_id | district, levers {allocation_pct, incentive_per_unit, days_supply_delta, market_growth_pct})` | Deterministic elasticity model + Bedrock narrative | Projected volume, objective %, share, gross, days supply with deltas; labeled *estimate*; GR-001/GR-005 checked |
| `create_contact_report(dealer_id, type, topics, summary_or_audio, followups)` | Quick Flow + Transcribe (dictation) | Structures the report, extracts follow-ups with deadlines, writes CRM activity + `DEALER_Tasks`, suggests next-visit focus |
| `generate_action_plan(account_id, issue)` | Quick Flow | Milestones, owners, due dates, success metric; stored as `OEM_AccountPlans` type `action_plan` |
| `generate_account_plan(account, actuals, market, incentives, research)` | Quick Flow | Structured document with citations to every number |
| `generate_opportunity_brief(opportunity_id)` | Quick Flow | |
| `generate_win_strategy(account_id, competitor_set)` | Quick Flow | |
| `build_meeting_package(type, scope, period)` | Quick Flow → PPTX/PDF in S3 | Types: dealer/district/area/national month-end close, QBR, new-model launch prep, EV readiness, CSI action, fixed-ops monthly/quarterly, parts report |
| `search_comms(query)` / `consolidate_comms(dealer_id)` / `forward_comms(dealer_id, items[], dm_comments)` | Quick Index over Sales Ops announcements + SES | AI search over bulletins; per-dealer digest; forward with comments |
| `rank_reports(district_priorities)` | Quick Index over report catalog | Report Library ordering |
| `publish_plan(plan, reviewers[])` | `OEM_AccountPlans` + CRM attach + email | Versioned; review status |
| `ask(question, scope)` | Quick agent over Quick Index (CRM notes, C&I bulletins, market reports, past plans, contact reports) | The right-hand chat panel; every answer guardrail-checked and cited |
| `check_guardrails(text, context)` | Bedrock Guardrails + rules Lambda | GR-001…GR-008; logs to `OEM_GuardrailEvents` |
| `run_district_cycle(district_id)` *(proactive, 2026-09-22)* | Weekly scheduler (Mon 05:00 local) + on `rdr.updated` | Refreshes composites, runs `detect_alerts`, drafts action plans for dealers below threshold, assembles the meeting package for every dealer visit on the DM's calendar in the next 7 days, rolls up to Region View; everything lands as *drafts for review* (`act_with_approval`) |
| `track_milestones()` *(2026-09-22)* | Daily | Raises `action_plan.milestone_due` and overdue tasks for dealer-owned milestones; drafts the follow-up note |

**Proactive behaviour (§4.10.2).** The DM opens Monday's workspace to find the week already prepared: alerts triaged, plans drafted, meeting packages built, milestones chased. The DM reviews and publishes; A6 never publishes, never contacts a dealer, and never touches allocations on its own (GR-001…GR-008 stay hard rules).

**Guardrails.** GR-001…GR-008 (table above) on every query and output. Every figure cites its source (RDR row, Polk dataset, C&I program id, composite input, research URL). Scenario and incentive simulations are labeled as estimates and never auto-submit allocation or budget changes. External research is restricted to the market-intelligence connectors and Quick Research allow-listed domains. Contact-report dictation audio is not retained after transcription.

**Interaction with A2.** When a consumer journey's `account_id` is set (fleet/commercial buyer, employee-pricing program), A2 pulls the current Opportunity Brief through the Gateway tool so dealer-facing suggestions (A3) and pricing (A1) respect the account's program terms.

### 3.7 A7 – Service Operations Agent (Process 2 supervisor)

| | |
|---|---|
| Use case | Own the service visit end to end (§2.1.1) and feed ownership signals back into the purchase funnel |
| AI category / delivery | **Agentic** / **Custom app on AgentCore** (Strands, stateful, tool-heavy); Quick Index for repair procedures, TSBs, warranty bulletins, owner-manual content |
| Target | Dealer service departments (service advisor, technician, parts counter, service manager); OEM after-sales / customer care (read) |
| Data sources | DMS (RO, service history, invoicing), service scheduling, inspection (MPI / VSA) tool, DTC and repair-procedure database, labor-time guide, parts catalog + inventory, OEM warranty / recall, connected-car alerts (optional) |
| Reference | Prior codebase `dashboard/work-orders`, `dashboard/inspections` (`vsa.json`), `dashboard/diagnostics` (`dtc-database.json`), `dashboard/service-history` — adopted under AR-17/18/19/22 (§9A.5) as the concrete starting point for RO, MPI, DTC, and history models and screens |

**Purpose.** Where A2 owns one purchase, A7 owns every service visit for a VIN over its life. It books the visit, mirrors the DMS repair order (the DMS stays system of record for the RO and invoice; A7 is system of record for the *service journey*, inspection findings, estimate decisions, and declined work), turns inspection and diagnostic findings into a sourced estimate the customer can approve line by line, keeps the shop, parts, and customer in sync, and at FOLLOW_UP hands equity and declined-work signals to A4 and A2. It is the second supervisor in the system: it calls A4 (equity), A3 (talk tracks, upsell brief), and A2 (nurture, purchase journey creation) as tools or through events; A2 never drives the service journey.

**Personas.** Service advisor (owns CHECK_IN, APPROVE, READY), technician (INSPECT, DIAGNOSE, REPAIR), parts counter (parts on RO, orders, back-orders), service manager (lane load, cycle time, close rates), customer (BOOK, APPROVE, FOLLOW_UP through My Garage / chat / SMS).

**Inputs.** `vin`, `customer_id`, `service_journey_id`, appointment request or DMS RO id, inspection submissions, technician notes / DTC codes, customer approval decisions, parts events, `market_config` (labor rate display, currency, distance units, tax on labor/parts by region).

**Outputs.**
`ServiceJourney { service_journey_id, vin, customer_id, dealer_id, stage, ro_id, appointment, advisor_id, technician_id, wait_type (wait|drop|loaner|mobile), promised_at, estimate_id, approval_status, parts_hold, ready_at, followups[] }`
`Inspection { inspection_id, ro_id, template_id, items[] { code, label, status (green|yellow|red), measurement, evidence[] (photo|video|note), recommendation, urgency (now|soon|monitor) }, technician_id, submitted_at }`
`ServiceEstimate { estimate_id, ro_id, lines[] { line_id, source (concern|inspection|recall|maintenance_schedule), finding_ref, labor_op, labor_hours, labor_price, parts[] { part_no, qty, price, availability, eta }, coverage (customer_pay|warranty|recall|service_contract|goodwill), total, price_source_refs[], decision (pending|approved|declined|deferred), decided_at, decided_by } , subtotal, tax, total, good_better_best[], explanation_for_customer }`
`ServiceHandoff { to (sales|nurture), reason (positive_equity|lease_end|repair_vs_replace|high_mileage|declined_work), evidence_refs[], created_journey_id }`

**Tools.**

| Tool | Source | Notes |
|---|---|---|
| `book_service(customer_id, vin, services[], preferred_slots, wait_type)` | Scheduling connector; `DEALER_Appointments` (type=service); **loaner / mobile-unit availability** from the `scheduling` connector (`get_loaner_availability`, `get_mobile_slots`) | Creates the service journey in BOOK; offers only wait types with capacity on that slot (loaner count, mobile van, pickup-delivery driver); `express` service class (no MPI, fixed menu) bookable separately; proactive variant triggered by recall / connected-car alert / `predict_next_service` |
| `predict_next_service(vin)` | DMS history + OEM maintenance schedule + mileage trend | Returns due items and a date window with `source_ref` to the schedule row |
| `open_repair_order(service_journey_id, mileage, concerns[], photos[], approval_prefs)` | DMS connector (`create_ro` / `get_ro`) | Mirrors `ro_id` into `DEALER_RepairOrders`; advances CHECK_IN |
| `record_inspection(ro_id, template_id, items[])` | Inspection connector or native form → `DEALER_Inspections` | Enforces every red/yellow item has evidence; advances INSPECT |
| `prioritize_findings(inspection_id)` | Bedrock over findings + history + warranty | Produces urgency ranking and plain-language explanation; never invents a finding not in the inspection |
| `lookup_dtc(codes[], vin)` | DTC / repair-procedure connector (`DEALER_DTCReference` in sandbox) | Returns description, likely causes, related TSBs (Quick Index), recommended tests |
| `get_repair_procedure(vin, labor_op)` | Quick Index (procedures, TSBs) via Gateway | Cited excerpts only |
| `get_labor_time(vin, labor_op)` | Labor-time guide connector (`DEALER_LaborOps` sandbox) | Hours + guide edition as `source_ref` |
| `lookup_parts(vin, labor_op | part_nos[])` | Parts catalog + parts inventory connector (`DEALER_Parts`) | On hand / ordered / ETA / superseded part numbers |
| `check_coverage(vin, line)` | Warranty / recall connector; `DEALER_FIProducts` + `DEALER_Deals` for **prepaid maintenance and VSC sold in F&I** | Warranty, recall, service contract (incl. prepaid maintenance plans sold by this store), goodwill eligibility per line; coverage type drives **RO line type** `customer_pay | warranty | internal | sublet` (internal = used-car recon / PDI, linked to `DEALER_TradeDispositions`; sublet = outside vendor line with vendor + cost) |
| `build_estimate(ro_id, lines[])` | Internal + AgentCore Code Interpreter (AR-2) | Totals, tax per `market_config`, good-better-best bundles; returns inputs + code + result |
| `present_estimate(estimate_id, channel)` | OutreachProvider (magic link to My Garage), advisor view | Advances APPROVE; respects `CUST_Consent` |
| `explain_finding(line_id, audience)` | Bedrock over finding + evidence + procedure excerpt | Customer-safe explanation; cites evidence and procedure |
| `record_approval(estimate_id, decisions[])` | Internal; DMS connector (`update_ro_lines`) | Approved lines dispatched; declined/deferred lines written to `DEALER_DeclinedWork` |
| `track_parts(ro_id)` | Parts connector; `DEALER_PartsOrders` | Flags `parts_hold`, updates promised time, notifies advisor and customer |
| `update_line_status(ro_id, line_id, status)` | DMS connector / technician tablet | in_progress → complete → qc_passed; advances REPAIR |
| `notify_ready(ro_id)` | OutreachProvider; DMS (`close_ro`, invoice) | Shares invoice to My Garage Documents; schedules pickup/delivery; advances READY |
| `schedule_declined_followup(declined_work_id, cadence)` | A2 `plan_nurture` (service templates) | Never a sales message; re-presents the finding with original evidence |
| `handoff_to_sales(service_journey_id, reason, evidence)` | A4 `compute_equity`, A2 `create_journey(source=service_lane)` | Creates purchase journey in DISCOVER with service context attached; creates `DEALER_Handoffs` row and advisor task |
| `get_service_history(vin)` | `DEALER_RepairOrders`, `DEALER_Inspections`, `DEALER_DeclinedWork`; DMS backfill | Read model for advisor, technician, My Garage |
| `dispatch_ro(dealer_id, date | ro_id)` *(proactive, 2026-09-22)* | Technician roster + skills/certifications (EV high-voltage, diesel, diagnostics), clocked capacity, promised times, parts availability, `DEALER_RepairOrders` | 05:00 shop-load run assigns today's booked ROs to technicians and sequences them; re-dispatches on `parts.backordered`, walk-ins, or technician absence. Foreman can override; overrides are recommendations rows so the model learns. Default `act_and_notify` |
| `stage_parts(appointment_id | date)` *(proactive, 2026-09-22)* | `predict_next_service`, declined work, open recalls, `lookup_parts`, `DEALER_PartsOrders` | Pre-pulls on-hand parts and pre-orders missing ones for booked work above a confidence threshold so the RO is not held for parts; ties orders to the appointment; cancels if the customer no-shows. Default `act_and_notify` with a dealer-set daily spend cap |
| `reforecast_promise(ro_id)` *(proactive, 2026-09-22)* | Line status, technician clock, parts ETA, shop load | Recomputes projected ready time on every line / parts event; if projected > promised, messages the customer with the new time and reason *before* the promise is missed (respecting consent) and flags the advisor. Default `act_and_notify` |
| `auto_present_estimate(ro_id)` *(2026-09-22)* | `build_estimate`, `PLAT_ConfigAutonomy.constraints.max_amount` | For maintenance-schedule and recall lines under the dealer's threshold, presents to the customer without waiting for the advisor; everything else waits for advisor review. Customer decision remains the customer's |
| `prepare_ro_brief(ro_id)` *(2026-09-22)* | History, declined work, recalls, coverage, A3 upsell brief, A4 equity | Builds the advisor's check-in brief and pre-drafts estimate lines from known items at BOOK / 05:00, not at check-in |
| `write_trace(...)` | `PLAT_AgentTraces` | Same trace contract as A1–A6; powers AI Log on Service Desk |

**Morning work (§4.10.2).** The 05:00 local shop-load run is A7's signature behaviour: dispatch today's ROs, stage parts, pre-draft estimates, prepare briefs. When the advisor arrives the lane is already planned. Throughout the day A7 keeps promised times honest and re-plans on every parts or line event. KPI: "advisor morning work done before shift start" (§1.3).

**Orchestration.** A7 runs the §2.1.1 state machine. `validate_service_handoff(stage)` blocks: APPROVE without an estimate whose every line has labor op, price source, and coverage; REPAIR without customer decision on every line; READY without RO closed in DMS and invoice document. Long-running: a service journey may sit in REPAIR for days (parts hold); A7 receives `parts.received`, `line.completed`, and `ro.closed` events from the bus and resumes. FOLLOW_UP runs on the EventBridge Scheduler tick like A2 nurture.

**Interaction with A2 / A4 / A3.**
- A7 → A4: `compute_equity(vin, mileage, condition_from_inspection)` at FOLLOW_UP and whenever an estimate exceeds a configurable share of vehicle value (`repair_vs_replace_threshold_pct`, default 50%).
- A7 → A2: `create_journey(source=service_lane, context={service_journey_id, equity, declined_work, estimate})` for a sales handoff; `plan_nurture(template_family=service)` for declined-work and due-service reminders. A2 owns the resulting purchase journey; A7 keeps the service journey.
- A2 → A7: on `vehicle.delivered`, A2 emits the event; A7 creates the ownership record and the first-service reminder. A2 never calls A7 tools directly.
- A7 → A3: `get_service_upsell_brief(ro_id)` (Quick Flow) for advisor talk tracks; `classify_objection` on estimate pushback in the advisor's live view.

**Guardrails.** Never recommend work not traceable to an inspection item, DTC, recall, or maintenance-schedule row. Never quote a labor or parts price not returned by a tool. Coverage (warranty / recall) must be checked before presenting a customer-pay line. Customer explanations state urgency in the inspection's own terms (now / soon / monitor) and never use fear language; Bedrock Guardrails denied topics cover safety scare tactics and unverified failure predictions. Approval decisions are the customer's; A7 cannot approve on their behalf. Technician evidence (photos, video) is C2 data shared with the customer only for their own VIN. All figures formatted via `market_config` (labor rates, parts prices, tax on labor vs parts by region).

**Real-time behaviour.** Technician tablet and advisor views poll or stream line status; estimate approval push (SMS / My Garage) must reach the customer within 60 s of `present_estimate`. No sub-2 s requirement; A7 is not a floor-copilot.

---

## 4. Data Sources & Integration Layer

### 4.1 Connector Contract

Every external system is accessed through a `Connector` interface (`integrations/<system>/connector.py`) with two implementations: `SandboxConnector` (DynamoDB-backed, seeded) and `LiveConnector` (real API, credentials from Secrets Manager). Selected by `INTEGRATION_MODE=sandbox|live`.

| Source | Connector | Key operations | Sandbox table |
|---|---|---|---|
| CRM (Salesforce) | `crm` | get_lead, upsert_lead, get_opportunity, log_activity | `DEALER_Customers` |
| CDP | `cdp` | get_profile, get_events, get_segments | `DEALER_Signals` |
| Configurator | `configurator` | get_options, price_build, save_build | `PLAT_Configurations`, `OEM_Catalog` |
| F&I (CDK) | `fni` | prequalify, get_offers, get_products, structure_deal | `DEALER_FinanceOffers` |
| Trade-In Valuation (Cox/KBB) | `valuation` | get_valuation, get_market_comps | `DEALER_Valuations` |
| DMS (CDK) | `dms` | push_deal, get_deal_status, get_service_history, mark_delivered | `DEALER_Deals` |
| Ad Platforms (Adobe/Google/Meta) | `ads` | get_campaigns, get_spend, get_touchpoints | `OEM_Campaigns`, `DEALER_Touchpoints` |
| Dealer Inventory (VIN-level) | `inventory` | search, get_vehicle, get_near_matches, get_in_transit, reserve | `DEALER_Inventory` |
| Digital retail / lead providers | `leads` | get_leads, get_lead_events (ADF/XML lead intake, chat transcripts) | `DEALER_Leads` |
| OEM incentive feed | `incentives` | get_programs(model, state, date), get_eligibility_rules | `OEM_Incentives` |
| Dealer calendar | `calendar` | get_slots, book, cancel (test drives, service) | `DEALER_Appointments` |
| RDR / sales data lake | `rdr` | get_actuals(account, range), get_targets | Athena over `s3://app-lake/rdr/` (synthetic in sandbox) |
| C&I incentive system | `ci` | get_programs(account), get_utilization, simulate_mix | `OEM_CIPrograms` |
| Market intelligence (S&P Global Mobility, R.L. Polk) | `marketintel` | get_registrations(pma, range), get_forecast(segment, region), get_competitor_offers(pma) | Athena over `s3://app-lake/marketintel/` (synthetic in sandbox) |
| CSI / customer experience survey | `csi` | get_scores(account, range), get_trend | Athena over `s3://app-lake/csi/` (synthetic) |
| Training LMS + facility / compliance systems | `compliance` | get_ev_certification(account), get_training_pct, get_facility_status, get_franchise_requirements | `OEM_Compliance` |
| Sales Ops communications | `comms` | list_announcements(audience, since), get_announcement | `OEM_Comms` (also ingested to Quick Index) |
| **Service (Process 2, A7)** | | | |
| DMS – repair orders (CDK, Reynolds) | `dms` (extended) | create_ro, get_ro, update_ro_lines, close_ro, get_invoice, get_service_history(vin) | `DEALER_RepairOrders` (mirror), DMS stays system of record for RO + invoice |
| Service scheduling (Xtime, myKaarma, DMS scheduler) | `scheduling` | get_lane_capacity(dealer, date), get_slots(services, wait_type), book, reschedule, cancel | `DEALER_Appointments` (type=service); reuses `calendar` in sandbox |
| Inspection / MPI tool (OEM MPI app, dealer tablet) | `inspection` | get_templates(model_year), submit_inspection, get_inspection | `DEALER_InspectionTemplates`, `DEALER_Inspections` (native form is the sandbox) |
| DTC / repair procedures (OEM service info, Mitchell 1, ALLDATA) | `dtc` | lookup_dtc(codes, vin), get_procedure(vin, labor_op), get_tsbs(vin) | `DEALER_DTCReference` (seeded from prior `dtc-database.json`); procedures/TSBs in Quick Index |
| Labor-time guide (OEM flat-rate manual, Mitchell) | `labor` | get_labor_time(vin, labor_op), get_labor_rate(dealer, type) | `DEALER_LaborOps` |
| Parts catalog + inventory (OEM EPC, DMS parts) | `parts` | lookup_parts(vin, labor_op), get_availability(part_no, dealer), create_order, get_order_status, get_supersession | `DEALER_Parts`, `DEALER_PartsOrders` |
| Warranty / recall / service contract (OEM warranty system, NHTSA recalls) | `warranty` | get_coverage(vin, line), get_open_recalls(vin), get_service_contract(vin) | `DEALER_Coverage` (sandbox); NHTSA recalls API free in live |
| Connected-car alerts (OEM telematics) — optional | `telematics` | get_alerts(vin), get_odometer(vin) | Synthetic in sandbox; behind `ENABLE_TELEMATICS=false` |

### 4.2 DynamoDB Tables (operational)

Naming: `<OWNER>_<Entity>_<stage>` where `<OWNER>` is the data-owner prefix (`OEM` / `DEALER` / `CUST` / `PLAT`), on-demand billing, PITR enabled.

| Table | PK | SK | GSIs | Notes |
|---|---|---|---|---|
| `DEALER_Journeys` | `journey_id` | – | `CustomerIndex(customer_id, stage)`, `StageIndex(stage, updated_at)`, `DealerIndex(dealer_id, updated_at)`, `VinIndex(vin)` | At most one journey per customer in an *active* stage (DISCOVER–DELIVER); OWN journeys are resting (§2.1). Fields `previous_journey_id`, `superseded_by`, `source` (`web|lead|service_lane|upgrade|…`), `vin` (set at AGREE) |
| `DEALER_Vehicles` | `vin` | – | `OwnerIndex(customer_id)`, `DealerIndex(selling_dealer_id)`, `NextServiceIndex(dealer_id, next_service_due)` | **VIN master** (added 2026-09-22): model/trim, delivered_at, selling and servicing dealer, owner history `[ {customer_id, from, to} ]`, mileage_last + as_of, next_service_due, coverage summary, open recalls, telematics id, equity snapshot (from A4). Created at `vehicle.delivered` or by DMS backfill; keyed by every service, coverage, equity, and telematics record. `DEALER_Customers.vehicles_owned[]` is a projection of this table |
| `DEALER_JourneyEvents` | `journey_id` | `event_ts` | `TypeIndex(event_type, event_ts)` | Append-only; Streams → Firehose → S3 |
| `DEALER_Customers` | `customer_id` | – | `EmailIndex`, `DealerIndex`, `AdvisorIndex` | Unified profile (CRM + CDP merge) |
| `DEALER_Signals` | `customer_id` | `signal_ts` | – | Browsing/config/compare events, TTL 180d |
| `OEM_Catalog` | `model_id` | `trim_id` | – | OEM model/trim/pack/option master |
| `DEALER_Inventory` | `vehicle_id` | – | `StatusIndex(status, price)`, `DealerModelIndex(dealer_id, model_id)` | |
| `PLAT_Configurations` | `config_id` | – | `JourneyIndex(journey_id)` | Saved builds |
| `DEALER_FinanceOffers` | `offer_id` | – | `JourneyIndex(journey_id)` | Pre-qual + offers, TTL 30d |
| `DEALER_Valuations` | `valuation_id` | – | `JourneyIndex(journey_id)` | Trade-in quotes, TTL 14d |
| `DEALER_Deals` | `deal_id` | – | `JourneyIndex(journey_id)`, `DealerIndex(dealer_id, booked_at)` | Mirror of DMS deal |
| `OEM_Campaigns` | `campaign_id` | – | `PlatformIndex(platform, start_date)` | |
| `DEALER_Touchpoints` | `customer_id` | `touch_ts` | `CampaignIndex(campaign_id, touch_ts)` | Attribution inputs |
| `DEALER_ObjectionStrategies` | `strategy_id` | – | `ObjectionTypeIndex` | Reuse existing seed |
| `DEALER_Outcomes` | `outcome_id` | – | `JourneyIndex`, `StrategyIndex(strategy_id)` | A3 objection-strategy outcomes; since 2026-09-22 a view over `DEALER_Recommendations` (§4.10.3) |
| `DEALER_Recommendations` | `recommendation_id` | – | `AgentActionIndex(agent#action_id, shown_at)`, `JourneyIndex(journey_id)`, `ServiceJourneyIndex`, `ContextIndex(context_hash, shown_at)` | Every agent recommendation: shown → accepted → converted → value (§4.10.3); Streams → lake `recommendations/`; TTL 2y |
| `PLAT_ConfigAutonomy` | `config_key` | `version` | `ActiveIndex(config_key, is_active)` | Autonomy level per agent action and scope (§4.10.1); versioned with publish/rollback like other `PLAT_Config*` |
| `OEM_Incentives` | `incentive_id` | – | `MarketStateModelIndex(market_state, model_id)` | `market_state = "us#CA"`; `tier` = federal / state / utility / oem; eligibility rules as JSON |
| `OEM_Markets` | `market_id` | – | – | `MarketConfig` rows (§2.5); seeded with `us` |
| `OEM_StateRules` | `market_id` | `state` | – | Sales-tax rate, doc-fee cap, EV registration fee, default fuel/energy prices |
| `DEALER_Tasks` | `task_id` | – | `AssigneeIndex(assignee, due_at)` | Dealer to-dos from A2 |
| `PLAT_AgentTraces` | `trace_id` | – | `JourneyIndex(journey_id, ts)` | TTL 7d; powers AI Log |
| `DEALER_SalesPackages` | `package_id` | – | `JourneyIndex` | Aggregated A2 output |
| `DEALER_Leads` | `lead_id` | – | `CustomerIndex`, `SourceIndex(source, received_at)` | Digital-retail / third-party lead intake (ADF), chat transcripts |
| `DEALER_Appointments` | `appointment_id` | – | `DealerSlotIndex(dealer_id, slot_start)`, `JourneyIndex`, `ServiceJourneyIndex` | `type` (`test_drive|delivery|service`), `wait_type` (`wait|drop|loaner|mobile|pickup_delivery`), `services[]`, `loaner_id`; test drives, deliveries, service (sandbox calendar) |
| `DEALER_NurturePlans` | `journey_id` | – | `NextTouchIndex(next_touch_at)` | A2 nurture state; scheduler scans by `next_touch_at` |
| `CUST_Messages` | `journey_id` | `sent_at` | `ChannelIndex(channel, sent_at)` | Outbound/inbound message log with consent snapshot; TTL 2y |
| `CUST_Consent` | `customer_id` | – | – | Channel opt-in/opt-out, quiet hours, TCPA/CAN-SPAM flags |
| `OEM_Accounts` | `account_id` | – | `RegionDistrictIndex(region, district)`, `TypeIndex(type)` | Dealer / dealer-group / fleet accounts (A6) |
| `OEM_AccountPlans` | `account_id` | `version` | `TypeIndex(artifact_type, generated_at)` | Account plans, opportunity briefs, win strategies, roll-ups (A6) |
| `OEM_CIPrograms` | `program_id` | – | `AccountIndex(account_id)` | C&I incentive programs, enrollment, utilization (sandbox) |
| `OEM_Alerts` | `alert_id` | – | `DistrictSeverityIndex(district, severity)`, `AccountIndex(account_id, created_at)` | A6 alerts (HIGH/MEDIUM/LOW) with required action, timeframe, status |
| `OEM_ContactReports` | `report_id` | – | `AccountIndex(account_id, contact_date)`, `DMIndex(dm_user, contact_date)` | Structured dealer contact reports; mirrored to CRM activity |
| `OEM_Compliance` | `account_id` | `requirement` | – | Facility / signage / training / franchise status (sandbox for compliance feed) |
| `OEM_GuardrailEvents` | `event_id` | – | `RuleIndex(rule_id, ts)`, `UserIndex(user, ts)` | GR-00x blocks; powers Admin › AI Guardrails view; TTL 1y |
| `OEM_Comms` | `comm_id` | – | `AudienceIndex(audience, published_at)` | Sales Ops announcements / bulletins (also ingested to Quick Index) |
| `PLAT_OrgHierarchy` | `node_id` | – | `ParentIndex(parent_id)`, `TypeIndex(type)` | OEM ▸ region ▸ district ▸ dealer group ▸ dealer; PMA; drives scope claims and search filters |
| `PLAT_Users` | `user_id` | – | `DealerIndex(dealer_id)`, `DistrictIndex(district)` | Profile, groups mirror, scope nodes, Quick provisioning status, preferences |
| `PLAT_EntityMap` | `entity_type#app_id` | `system` | `ExternalIndex(system, external_id)` | ID mapping to CRM/DMS/CDP/lead ids (§4.7) |
| `DEALER_Handoffs` | `handoff_id` | – | `JourneyIndex(journey_id)`, `ToUserIndex(to_user, created_at)` | Person-to-person journey handoffs (§5.18.3) |
| `DEALER_Notifications` | `user_id` | `created_at` | `UnreadIndex(user_id, read_at)` | Notification Hub (§5.18.4); TTL 180d |
| `DEALER_Approvals` | `approval_id` | – | `ApproverIndex(approver_role#scope, status)`, `RequesterIndex(requested_by)` | Approval workflow (§4.8) |
| `CUST_Documents` | `document_id` | – | `ResourceIndex(resource_url)`, `OwnerIndex(owner_scope, created_at)` | Document service metadata (§4.9) |
| `PLAT_ConfigNurture` / `PLAT_ConfigNotifications` / `PLAT_ConfigSuggestions` / `PLAT_ConfigGuardrails` | `config_key` | `version` | `ActiveIndex(config_key, is_active)` | Versioned, editable config (Journey Ops, Admin); agents read active version at invocation |
| `DEALER_FIProducts` | `product_id` | – | `MarketIndex(market_id)` | F&I product catalog for menu presentation (GAP, VSC, T&W, maintenance, charger) |
| `DEALER_Plays` / `DEALER_PlayRuns` | `play_id` / `run_id` | – | `JourneyIndex(journey_id, started_at)` | A2 orchestration templates + runs (AR-10); 6 carried templates |
| `PLAT_KnowledgeCollections` | `collection_id` | `doc_id` | `KindIndex(kind)` | RAG vector-store metadata / manifest (AR-1); actual vectors in ChromaDB (dev) or Bedrock KB (prod) |
| `DEALER_TradeDispositions` | `trade_id` | – | `DealerStatusIndex(dealer_id, status)` | Keep / CPO / wholesale / auction decisions |
| `DEALER_Deliveries` | `journey_id` | – | `DealerSlotIndex(dealer_id, delivery_slot)` | Delivery Board state and prep checklist |
| `DEALER_LeadSLA` | `lead_id` | – | `DealerDueIndex(dealer_id, sla_due_at)` | Lead Desk SLA timers and first-response tracking |
| **Service (Process 2, A7) — 12 tables** | | | | |
| `DEALER_ServiceJourneys` | `service_journey_id` | – | `VinIndex(vin, created_at)`, `CustomerIndex(customer_id)`, `DealerStageIndex(dealer_id, stage)`, `PromisedIndex(dealer_id, promised_at)` | One active per VIN (§2.1.1); `journey_type=service`; links `ro_id`, `estimate_id`, `purchase_journey_id` when spawned |
| `DEALER_ServiceJourneyEvents` | `service_journey_id` | `event_ts` | `TypeIndex(event_type, event_ts)` | Append-only; Streams → Firehose → S3 `events/` |
| `DEALER_RepairOrders` | `ro_id` | – | `ServiceJourneyIndex`, `DealerStatusIndex(dealer_id, status)`, `VinIndex(vin, opened_at)` | Mirror of DMS RO: lines, technician, promised_at, status, parts_hold, invoice doc id; DMS is system of record |
| `DEALER_Inspections` | `inspection_id` | – | `RoIndex(ro_id)`, `VinIndex(vin, submitted_at)` | MPI results (items with status, measurement, evidence doc ids); model adopted from prior `vsa.json` (AR-19) |
| `DEALER_InspectionTemplates` | `template_id` | `version` | `ModelIndex(model_id, is_active)` | Versioned MPI checklists per model / powertrain (EV template omits oil, adds HV battery + charge port) |
| `DEALER_DTCReference` | `dtc_code` | `make#model_family` | – | Sandbox DTC database (adopted from prior `dtc-database.json`, AR-18): description, causes, tests, related labor ops, TSB refs |
| `DEALER_LaborOps` | `labor_op` | `model_id` | – | Flat-rate hours + guide edition (`source_ref`); dealer labor rates in `OEM_StateRules` / dealer config |
| `DEALER_Parts` | `part_no` | `dealer_id` | `SupersedesIndex(superseded_by)` | Catalog + on-hand + bin; price with `source_ref`; supersession chain |
| `DEALER_PartsOrders` | `order_id` | – | `RoIndex(ro_id)`, `DealerEtaIndex(dealer_id, eta)` | Orders / back-orders per RO line; drives `parts_hold` |
| `DEALER_ServiceEstimates` | `estimate_id` | – | `RoIndex(ro_id)`, `StatusIndex(approval_status, presented_at)` | Lines with coverage, price sources, customer decisions, Code Interpreter audit (inputs + code + result); TTL 2y |
| `DEALER_DeclinedWork` | `vin` | `declined_at` | `DealerDueIndex(dealer_id, followup_due_at)`, `CustomerIndex(customer_id)` | Declined / deferred lines with original evidence; feeds A2 service nurture and A3 upsell brief |
| `DEALER_Coverage` | `vin` | – | `RecallIndex(recall_id)` | Sandbox warranty / recall / service-contract facts; live via `warranty` connector |

`DEALER_Handoffs` gains `from_service_journey_id`; `DEALER_Customers.vehicles_owned[]` is a projection of `DEALER_Vehicles`. Total operational tables: **65** (50 from §1–§9A + `DEALER_Vehicles` + 12 service + `PLAT_ConfigAutonomy` + `DEALER_Recommendations`, the last two from §4.10).

### 4.3 Analytics Lake

- `DEALER_JourneyEvents`, `DEALER_ServiceJourneyEvents`, and `DEALER_Touchpoints` streams → Kinesis Data Firehose → `s3://app-lake-<account>/events/` (Parquet, partitioned by `dt`).
- Additional lake prefixes: `rdr/` (retail delivery actuals + targets), `marketintel/` (registrations, forecasts), `leads/` (lead events), `service/` (RO, inspection, estimate, declined-work facts with hashed `customer_id`). Synthetic in sandbox, partner drops in live.
- Glue crawler + Athena database `app`.
- Amazon Quick dataset over Athena; dashboards: **Funnel by Stage**, **Campaign ROI**, **Dealer Performance**, **Trade-In Capture**, **District Sales vs Target** (A6), **Fixed Ops** (retention, MPI rate, close rate, declined-work conversion, RO cycle time, parts-wait; A7 / Service Manager). Quick Q topics enabled for natural-language questions used by A5, A6, and the Service Manager cockpit.

### 4.3.1 Amazon Quick Suite Components (A6 agent/flows; A3 & A7 use Quick Index only; A5 dashboards)

Quick-hosted **agents and Flows** are used by **A6** only. **A3 and A7 are custom AgentCore agents that consume Quick Index as a retrieval tool** (documents, citations), not Quick-hosted agents. A5 uses QuickSight dashboards and Q topics.

| Component | Used for | Configuration |
|---|---|---|
| Quick agents | A6 planner Q&A (Quick-hosted). *A3 is not a Quick agent since 2026-09-23; it retrieves from Quick Index instead* | One agent per role with scoped Quick Index collections; Quick data is not row-scoped (access by Quick group, §13 2026-10-21) |
| Quick Flows | A6: *Account Plan*, *Opportunity Brief*, *Win Strategy*, *District Roll-up*. *(A3's former Quick Flows are now AgentCore tools or A2 `publish_shift_list`)* | Flows call Athena/DynamoDB data sources and connector Lambdas as actions; outputs stored to `OEM_AccountPlans` / returned to console |
| Quick Index | Semantic index over CRM notes and activities, DMS service history summaries, OEM incentive bulletins, product/trim guides, C&I program docs, market-intel reports, prior account plans, repair procedures / TSBs. Retrieved by the A6 Quick agent **and by the A3 and A7 AgentCore agents as a tool** (`retrieve_context`) | Ingested via S3 connector (nightly export) + Salesforce connector; PII fields masked at export; AgentCore agents query it through a Gateway retrieval target or the Quick Index API |
| Quick Research | A6 external account research | Allow-listed domains (OEM, dealer news, business registries, S&P/Polk portals) |
| QuickSight | A5 dashboards; A6 roll-up visuals; embedded in console | Registered-user embedding; dealer users provisioned as Quick readers via Cognito → IAM Identity Center mapping |
| AgentCore Gateway → Quick | Lets A2 call the A6 Quick agent/flows as tools, and lets the A3 and A7 AgentCore agents query Quick Index (`retrieve_context`) | Gateway target: Lambda that calls Quick agent/flow or Quick Index APIs with the caller's scope; results carry citations |

### 4.4 Amazon Personalize

| Dataset group | Recipe | Interactions source | Items |
|---|---|---|---|
| `app-vehicles` | `aws-personalized-ranking` | `DEALER_Signals` (view, configure, compare, test_drive, purchase) | `OEM_Catalog` trims |
| `app-vehicles` | `aws-similar-items` | same | same |
| `app-vehicles` | `aws-user-segmentation` (Item-Affinity) | same | same |

Cold start: rules-based ranker (budget fit, body style, powertrain preference, region incentives) until the campaign is trained; A1 reports which ranker was used.

### 4.5 Outreach Channels (Amazon Connect optional)

Outreach is behind an `OutreachProvider` interface in `app/services/platform/outreach.py`. The active provider is selected by the `ENABLE_CONNECT` environment variable (default `false`, the SES/SNS provider).

| Mode | Provider | Channels | Setup |
|---|---|---|---|
| **Default** (`ENABLE_CONNECT=false`) | `BasicOutreachProvider` | Email via Amazon SES (verified sender), SMS via Amazon SNS (opt-in required), dealer task in `DEALER_Tasks` | Created by bootstrap; SES sandbox needs verified recipients in dev |
| Optional (`ENABLE_CONNECT=true`) | `ConnectOutreachProvider` | Outbound voice callback, chat, SMS; Contact Lens transcripts written back as `DEALER_JourneyEvents` (`type=contact_completed`) | Connect instance ARN supplied in config (`connect.instanceArn`, `connect.contactFlowIds`); flows `RetailOps-Outbound-Callback`, `RetailOps-Delivery-Update`, `RetailOps-TestDrive-Confirm` provisioned by `deploy.sh --connect` |

Both providers expose the same `send(customer, channel, template, context, when)` and `schedule(...)` methods; A2 does not know which one is active. UI shows available channels from `GET /config/outreach`. Enabling Connect later requires no agent or UI changes.

### 4.6 Event Backbone (G-21)

One Amazon EventBridge bus `app-{stage}` is the integration spine between apps, agents, and stacks. DynamoDB Streams on the journey/event tables are bridged onto the bus by a Lambda; agents and Lambdas publish directly; Quick-hosted agents publish via the Gateway Lambda. Every event carries `{ event_id, type, ts, actor {user|agent|system}, tenant, scope {dealer_id, district, region}, resource_url, correlation_id, payload }`.

**Event catalog (v1)**

| Domain | Types | Main subscribers |
|---|---|---|
| Journey | `journey.created`, `journey.stage_changed`, `journey.stalled`, `journey.owner_changed`, `handoff.created`, `handoff.accepted` | A2, Notification Hub, Store Cockpit read models, lake |
| Customer | `lead.received`, `signal.recorded`, `consent.changed`, `message.inbound`, `message.outbound` | A2, A3 Lead Desk, Marketing Studio analytics |
| Commerce | `build.saved`, `appointment.booked/changed/cancelled`, `prequal.requested/decided`, `trade.valued/accepted`, `deal.structured/pushed/booked`, `vehicle.delivered` | A2, F&I Desk, Delivery Board, My Garage, DMS sync |
| Service (Process 2) | `service.journey.created`, `service.stage_changed`, `service.appointment.booked/changed/cancelled`, `ro.opened/updated/closed`, `inspection.submitted`, `estimate.presented`, `estimate.line_decided`, `service.declined_work.recorded`, `parts.ordered/received/backordered`, `ro.line_completed`, `vehicle.ready`, `service.visit.completed`, `service.equity_flag`, `service.handoff_to_sales`, `recall.detected` | A7, A2 (nurture + journey creation on `service.handoff_to_sales`), A4 (`service.equity_flag`), Service Desk read models, Store Cockpit › Service tab, My Garage, Notification Hub, lake |
| Field (OEM) | `alert.raised/acknowledged/resolved`, `contact_report.filed`, `action_plan.created/milestone_updated/milestone_due`, `plan.generated/published`, `guardrail.blocked`, `rdr.updated` | Notification Hub, Store Cockpit alerts, A6 roll-ups |
| Agentic (added 2026-09-22) | `discovery.match_found`, `equity.crossed_positive`, `lease.end_window_entered`, `repair_vs_replace_triggered`, `acquisition.recommended`, `recommendation.shown/accepted/converted`, `autonomy.published`, `inventory.changed`, `incentive.changed`, `telematics.alert`, `campaign.spend_updated` | A2 (NBA + Plays), A4, A7, Inventory & Trade Desk, `DEALER_Recommendations` subscriber, Admin › Agents |
| System | `notification.requested`, `task.created/assigned/completed/overdue`, `approval.requested/decided`, `document.created`, `connector.sync_failed`, `config.published` | Notification Hub, Tasks, Approvals, Admin Console |

Rules: schema-registered (EventBridge Schema Registry), versioned `type@v1`, at-least-once delivery with idempotency keys, DLQ per subscriber, all events archived to the S3 lake (`events/`). Read models for Store Cockpit and Region View are materialized by subscribers, not computed on page load.

### 4.7 Entity Registry and ID Mapping (G-22)

`PLAT_EntityMap { entity_type, app_id, system, external_id, confidence, updated_at }` with GSI `(system, external_id)`. Golden-record rules: `customer_id` is solution-minted; CRM contact id, DMS customer number, CDP profile id, and lead-provider ids map to it (match on email/phone/name+zip, human merge queue in Admin for low-confidence). `dealer_id` is solution-minted and mapped to OEM dealer code, DMS store id, CRM account id. `vin` is natural. `deal_id` is DMS-minted and mirrored. Connectors must translate at the boundary; no external id appears in UI URLs.

### 4.8 Approval Workflow (G-23)

`DEALER_Approvals { approval_id, type, requested_by, approver_role, scope, payload, status, decided_by, decided_at, reason }` driven by an AWS Step Functions state machine (request → notify → wait for decision / timeout escalation → apply or reject → event). v1 approval types: action plan publish (DM → regional), incentive/C&I ask (C&I planner → OEM finance), deal exception (advisor → dealer_manager), budget shift recommendation (marketing → OEM finance), guardrail rule change (admin → security). Surfaced as Tasks with Approve / Reject / Comment; every decision is an event and an audit row.

### 4.9 Document Service (G-24)

`CUST_Documents { document_id, type, owner_scope, resource_url, s3_key, mime, version, retention_class, created_by, created_at, shared_with[] }`. Types: meeting package, account plan export, contact report PDF, deal worksheet, F&I menu, delivery checklist, customer-shared documents. S3 bucket `app-docs-{account}` with SSE-KMS, versioning, lifecycle by retention class (`operational` 2y, `deal` 7y, `transient` 30d). Pre-signed viewer component in the shell; sharing to a customer appears in My Garage › Documents. E-signature is out of scope for v1 (§12) but the document model reserves `signature_status`.

### 4.10 Agentic Operating Model: autonomy levels, event subscriptions, and the outcome loop (added 2026-09-22)

The 2026-09-22 review found that only A2 was goal-driven; the other agents waited to be asked. Three platform mechanisms make every agent proactive while keeping the dealer and OEM in control.

#### 4.10.1 Autonomy levels (`PLAT_ConfigAutonomy`)

Every agent action that has an external effect (a message, a booking, a dispatch, an order, a document, a data change) is registered with a **default autonomy level** and can be overridden per tenant, dealer, or role in Admin › Autonomy (versioned like the other `PLAT_Config*` tables; published, with rollback).

| Level | Meaning | Human sees |
|---|---|---|
| `observe` | Agent computes and logs only | Nothing in-shell; visible in AI Log and Agents panel |
| `recommend` | Agent produces a recommendation card / task; a human acts | Card with "Do it" / "Dismiss" and the reasoning |
| `act_with_approval` | Agent prepares the action fully and creates an approval (§4.8) or one-tap confirm | Approval task with the exact payload |
| `act_and_notify` | Agent executes and posts a notification with undo where possible | Notification Hub entry, activity feed row |

`PLAT_ConfigAutonomy { config_key="autonomy", version, scope (tenant|dealer_id|role), action_id, level, constraints { max_amount, channels[], quiet_hours, requires_consent, daily_cap }, is_active }`. Agents call `autonomy_level(action_id, scope)` before any effectful tool and behave accordingly; the resolved level is written into the trace. **Hard rules are not autonomy-configurable**: consent and quiet hours (§3.2), GR-001…GR-008 (§3.6.1), customer-owns-approval on estimates (§3.7), no automatic budget or incentive allocation (§3.5, §3.6), technician data masking (§5.20.5).

Default levels shipped (pilot): everything customer-facing that *sends* is `act_and_notify` only for A2 nurture (already policy-bounded) and A7 service reminders / ready notifications; A7 `stage_parts`, `dispatch_ro`, and `auto_present_estimate` (maintenance lines under the dealer threshold) are `act_and_notify`; A5 budget shifts and A6 plan publishing are `act_with_approval`; A4 acquisition offers and A1 proactive match touches are `recommend` until the dealer raises them. Acceptance criterion 33 tests the ladder.

#### 4.10.2 Event subscriptions per agent

Agents are triggered by events, schedules, and user requests. The table makes triggers explicit and testable (acceptance criterion 31). Events are those in §4.6.

| Agent | Subscribes to | Schedule | Proactive behaviour |
|---|---|---|---|
| A1 | `inventory.changed`, `incentive.changed`, `signal.recorded`, `build.saved` | – | Re-score every saved profile/build touched by the event; emit `discovery.match_found` (VIN arrived in radius, price/incentive makes a build in-budget, better-fit trim) |
| A2 | `journey.*`, `lead.received`, `message.inbound`, `discovery.match_found`, `service.handoff_to_sales`, `equity.crossed_positive`, `lease.end_window_entered`, `approval.decided` | `app-nurture-tick` (15 min) | Next best action chosen against `DEALER_Recommendations` outcomes (§4.10.3); trigger-able Plays (AR-10): `journey.stalled` → *Callback Brief*, `discovery.match_found` → *Recommend Vehicle*, `service.handoff_to_sales` → *Full Prep*, `lease.end_window_entered` → *Conquest/Upgrade Pitch*; publishes shift-start call lists as Tasks |
| A3 | `journey.stage_changed`, `appointment.booked`, `ro.opened` (service lane), `recommendation.outcome` | Weekly | Floor path stays reactive (sub-2 s). Pre-computes contextual suggestions and the service upsell brief on stage change / RO open so they are ready when the screen opens; weekly proposes suggestion-matrix and objection-strategy re-rankings from outcomes (human publishes) |
| A4 | `ro.opened` (fresh mileage), `service.visit.completed`, `inspection.submitted` (condition), `vehicle.delivered`, `trade.valued`, market-value refresh | Nightly equity refresh per `DEALER_Vehicles` | Continuous equity monitoring: emit `equity.crossed_positive`, `lease.end_window_entered`, `repair_vs_replace_triggered`, and `acquisition.recommended` (service-drive vehicle fits the store's stock need) without being asked |
| A5 | `vehicle.delivered`, `journey.created`, `touchpoint.recorded`, `campaign.spend_updated` | Nightly attribution; **nightly store digest** (06:30 UTC) | When projected lift of a budget shift ≥ threshold, auto-create the `DEALER_Approvals` request with evidence; refresh Personalize audiences from journey outcomes; the store digest writes each Store Cockpit tab's [AI INSIGHT] with Bedrock from that tab's own figures (its own guardrail, §13 2026-10-22), skips figures unchanged since the last run, and leaves the figures-only text in place when the guardrail holds an insight back |
| A6 | `rdr.updated`, `alert.raised`, `action_plan.milestone_due`, `contact_report.filed`, `guardrail.blocked` | Weekly district cycle (Mon 05:00 local) + daily alert scan | Refresh composites, detect alerts, draft action plans for red dealers, assemble the meeting package before each scheduled dealer visit, nag on overdue milestones, roll up to region; all publishing `act_with_approval` |
| A7 | `vehicle.delivered`, `recall.detected`, `telematics.alert`, `service.appointment.booked`, `ro.opened`, `inspection.submitted`, `estimate.line_decided`, `parts.ordered/received/backordered`, `ro.line_completed`, `ro.closed` | `app-service-followup-tick` (hourly); **05:00 local shop-load run**; nightly predictive scan; the Service Cockpit [AI INSIGHT] is written by the nightly store digest | Nightly predictive scan: the Lead Desk's predictive part watch run store by store, raising a lead for each store's most urgent predicted repairs (per-store cap). Morning work before shift start: `dispatch_ro` for today's booked ROs, `stage_parts` for predicted work, pre-built estimate drafts from declined work + recalls + due items, service upsell brief per RO; during the day `reforecast_promise` on every line/parts event and message the customer *before* a promise is missed; auto-present maintenance lines under threshold; declined-work and due-service follow-ups |

#### 4.10.3 Outcome loop (`DEALER_Recommendations`)

`DEALER_Outcomes` (objection strategies only) is generalised. **Every recommendation any agent surfaces** is a row: `DEALER_Recommendations { recommendation_id, agent, action_id, journey_id | service_journey_id | account_id, context_hash, shown_at, shown_to (role|customer), accepted_at, accepted_by, converted_at, conversion_event, value, autonomy_level_at_time, trace_id }`. Written by the shell on render (`shown_at`), on click (`accepted_at`), and by an event subscriber that joins later journey events (`converted_at`, e.g. `appointment.booked`, `estimate.line_decided=approved`, `vehicle.delivered`). Streams → lake `recommendations/` → Quick. Agents read aggregates by `(agent, action_id, context_hash)` to rank alternatives; Admin › Agents shows acceptance and conversion per action. `DEALER_Outcomes` remains as A3's objection-specific view over this table. Data classification: C2 (no contact data; `customer_id` hashed in the lake).

---

## 5. UI Specification (derived from `docs/use-cases/sales-advisor-v2.html`)

### 5.1 Design System (tokens carried over verbatim)

```css
--primary:#6366f1; --primary-light:#818cf8; --secondary:#8b5cf6; --accent:#ec4899;
--success:#10b981; --warning:#f59e0b; --danger:#ef4444;
--bg-dark:#0a0e1a; --bg-card:rgba(255,255,255,.04); --bg-card-hover:rgba(255,255,255,.07);
--text-primary:#f1f5f9; --text-secondary:#94a3b8; --text-dim:#64748b;
--border:rgba(255,255,255,.08); --border-active:rgba(99,102,241,.4);
--radius:12px; --radius-sm:8px; --radius-lg:16px;
```

- Dark theme only for the dealer console; font stack `-apple-system, Inter, Segoe UI`.
- Card: `bg-card` + 1px `border`, hover → `border-active`.
- Badges: `badge-live` (success), `badge-ai` (primary), `badge-active` (warning).
- Gradient primary buttons (`primary → secondary`), success state turns solid `success`.
- Motion: `slideIn` for log items, `pulse-dot` for live indicator. Respect `prefers-reduced-motion`.

Implementation: Tailwind theme extension in `ui/tailwind.config.ts` mapping these tokens; shared components in `ui/src/components/app/*`.

### 5.2 System Map: Personas → Apps → Stacks

The solution is **one system composed of five apps on different stacks**, joined by a Unified Shell (§5.18), one identity fabric (§8.1), a resource URL contract (§5.18.2), and an event bus (§4.6). A user moves between apps without re-authenticating or losing context.

| App | Stack | Theme | Who lives here | Primary agents |
|---|---|---|---|---|
| **Dealer Console** | Next.js (static, CloudFront) + Lambda/API GW + AgentCore | Dark (reference `sales-advisor-v2.html`) | Sales advisor, BDC, F&I manager, inventory/used-car manager, delivery coordinator, dealer principal/GM; **Service Desk**: service advisor, technician, parts counter, service manager | A1, A2, A3 (floor), A4, **A7** |
| **OEM Workspace** | Next.js shell + embedded Amazon Quick (agent, Flows, QuickSight) | Light (reference `DM_Assistant_Demo.html`) | District manager, regional sales manager, C&I planner, sales ops comms, OEM finance | A6 (the only Quick-hosted agent), A5 dashboards |
| **Marketing Studio** | Next.js + embedded QuickSight + AgentCore | Light | OEM marketing analyst, OEM CX / journey ops lead | A5, A2 (policy) |
| **Customer Web** (`/shop`, **My Garage**) | Next.js (separate route group, customer Cognito pool, passwordless) | OEM brand (light) | Prospect, buyer, owner | A1, A2, A7 (My Garage › Service) |
| **Admin Console** | Next.js | Neutral | Tenant admin, dealer admin, Administrator (SSO allowlist), security/compliance | – |
| *(optional)* **Agent Desktop** | Amazon Connect agent workspace with Retail Ops context panel | Connect | BDC / contact-center agents | A2, A3 |

**Persona → home screen → other screens they can reach**

| Persona | Cognito group | Home | Also uses |
|---|---|---|---|
| Sales advisor | `dealer_advisor` | Advisor (live) | Journeys, Discovery, Trade-In, Assistant, Assistant › Lead Desk (the store's web leads), Tasks, My customers' My Garage (read-only mirror) |
| BDC agent | `dealer_bdc` | Assistant › Lead Desk | Journeys, Appointments, Tasks, Advisor (read) |
| F&I manager | `dealer_fni` | F&I Desk | Journeys (FINANCE/AGREE lanes), Delivery Board (docs), Tasks |
| Service advisor | `dealer_service` | Service Desk › Board (§5.20.1) | Check-in, Estimate & Approval, RO view, Service history, Assistant › Service Lane (upsell brief), Trade-In (valuation and upgrade prospects; adding the trade to a purchase journey stays with sales), Tasks, customer's My Garage › Service (read-only mirror) |
| Technician | `dealer_technician` | Service Desk › My ROs (tablet) | Inspection, Diagnose, line status; read-only estimate; no customer contact data |
| Parts counter | `dealer_parts` | Service Desk › Parts | Parts on RO, orders, back-orders, held ROs; Inventory (parts view) |
| Service manager | `dealer_service_manager` | Service Desk › Cockpit (§5.20.7) | Everything in Service Desk; Store Cockpit › Service tab; Tasks; Approvals (goodwill) |
| Inventory / used-car manager | `dealer_inventory` | Inventory & Trade Desk | Discovery demand signals, Store Cockpit (inventory tab) |
| Delivery coordinator | `dealer_delivery` | Delivery Board | Journeys (DELIVER lane), Tasks |
| Dealer principal / GM | `dealer_manager` | Store Cockpit | Everything in Dealer Console; Campaigns (store scope); OEM comms digest |
| District manager | `oem_dm` | OEM Workspace › Today | All 15 A6 views; Store Cockpit of any dealer in district (read); Journeys (district scope, read) |
| Regional sales manager | `oem_regional` | OEM Workspace › Region View | All A6 views at region scope |
| C&I planner | `oem_ci_planning` | OEM Workspace › Accounts | Scenario Planning, Approvals (incentive asks) |
| Sales ops comms | `oem_sales_ops` | OEM Workspace › Sales Ops Comms | Report Library (no Admin Console, §13 2026-10-28) |
| OEM finance | `oem_finance` | Marketing Studio › ROI + OEM Workspace › Accounts | Approvals (budget shifts) |
| OEM marketing analyst | `oem_marketing` | Marketing Studio › Campaigns | Audiences (Personalize segments), Journey Ops (read) |
| OEM CX / journey ops lead | `oem_cx` | Marketing Studio › Journey Ops | Nurture policy, templates, consent rules, customer surface content |
| Tenant admin | `admin_tenant` | Admin Console | Users & roles, dealer hierarchy, market/brand, connectors, feature flags, audit |
| Dealer admin | `admin_dealer` | Admin Console (store scope) | Users in store, calendar, store settings |
| Administrator | `platform_admin` (role in `PLAT_Users`, not a Cognito group; SSO allowlist only, §8.1) | Admin Console | Every staff screen of the Dealer Console, OEM Workspace, Marketing Studio and Admin Console, tenant-wide (every district and dealer); not My Garage / `/shop` |
| Customer | customer pool | My Garage | `/shop`, `/shop/chat` |

**Administrator (`platform_admin`, decided 2026-10-21).** The role exists for the
people who build and run the solution. Only the the enterprise IdP aliases
listed in `RETAIL_OPS_SSO_ADMINS` receive it (§8.1); it cannot be assigned in
Admin › Users and cannot be selected in "View as persona". Its screens are the
union of every staff group's screens, which excludes the customer self-service
screens (My Garage, `/shop`, `/shop/chat`: those stay tied to customer
accounts; like a tenant admin, it can only mirror one named customer through
"View as", which gives it that customer's own self-scope). Scope is the OEM root
of its home market (the deploy `--market`), so it reads every district and
dealer like an OEM role. What it may do:

| Area | Administrator |
|---|---|
| Reads | Every staff read route of every module: dealer and service reads (OEM read-across), OEM Workspace views and assistant, Marketing Studio campaigns, audiences, Journey Ops (read), Quick flows and dashboards. Exception: deal documents (`GET /documents`, C4 per §8.2) stay with F&I, delivery, advisor and GM. |
| Writes | Everything a tenant admin may do: Admin Console (users, org, market & brand, connectors, flags, guardrail and suggestion config, autonomy and rollback, data requests, entity merge), deciding approvals of every type (never its own request) and account-plan decisions, task reassignment, consent and magic links, guardrail checks, the planner suite. |
| Not granted | Persona actions: OEM author actions (draft or submit account plans, budget shifts, jobs, contact reports, alert actions, comms forwarding, audience refresh/export, Journey Ops edits) and dealer-floor actions (journeys, leads, outreach, F&I, delivery, inventory, service desk, technician). The Administrator performs these through "View as persona", which swaps in that persona's claims and audits every request. |

### 5.2.1 Dealer Console Shell (dark)

**Top bar** is the Unified Shell bar (§5.18). Below it, the console's own tab strip:

| Tab | Route | Primary agent | Visible to |
|---|---|---|---|
| Store Cockpit | `/dealer/cockpit` | A3/A4/A6 read models | dealer_manager |
| Journeys | `/dealer/journeys` | A2 | dealer_*, oem_dm (read) |
| Discovery | `/dealer/discovery` | A1 | dealer_* |
| Advisor (live) | `/dealer/advisor/{journey_id}` | A3 (floor path) | dealer_advisor, dealer_manager |
| Assistant | `/dealer/assistant` (Lead Desk, Service Lane, Chat) | A3 (AgentCore agent; Quick Index retrieval) | dealer_advisor, dealer_bdc, dealer_service, dealer_manager |
| **Service Desk** | `/dealer/service/*` (Board, Book, Check-in, Inspect, Estimate, RO, Parts, History, Cockpit) — §5.20 | **A7** (+ A4 equity, A3 upsell brief) | dealer_service, dealer_technician, dealer_parts, dealer_service_manager, dealer_manager |
| F&I Desk | `/dealer/fni` | A3 tools + F&I connector | dealer_fni, dealer_manager |
| Trade-In & Inventory | `/dealer/trade-in`, `/dealer/inventory` | A4 | dealer_advisor, dealer_inventory, dealer_manager |
| Delivery Board | `/dealer/delivery` | A2 outreach | dealer_delivery, dealer_fni, dealer_manager |
| Campaigns (store) | `/dealer/campaigns` | A5 | dealer_manager |

**Bottom bar**: horizontally scrolling **AI Log** (`PLAT_AgentTraces` for the active journey or service journey), newest first, mono timestamp + green dot. Present on Advisor, F&I Desk, Delivery Board, Service Desk RO and Estimate views.

Customer-facing surface (separate Next.js route group, customer Cognito pool): `/shop` (Discovery for consumers, A1), `/shop/chat` (A2 nurture conversation, resumable by magic link), `/garage/*` (My Garage, §5.12).

### 5.3 Screen: Advisor (live) – the reference design

3-column grid `320px 1fr 340px` (≥1200), `280px 1fr 300px` (900–1200), single column < 900.

**Left – Customer 360** (`badge-live`)
- Customer card: avatar initials (gradient), name, budget range, lead score chip.
- Contact rows (email, phone, location, daily drive).
- Recent Activity list (from `DEALER_Signals`): time-ago + text, left accent border.
- Preference tags.
- AI Insight box (A3 `customer_360.insight`).
- **New vs reference:** Journey stage stepper (8 stages) at top of panel; missing-context warnings from `validate_handoff`.

**Center – AI Recommendations** (`badge-ai`)
- Vehicle matches card: EV / ICE chips, ranked rows (rank bubble, name, specs, tags, **Present** button → emits `vehicle_presented` event; button turns success "Presented").
- EV vs ICE side-by-side table with per-row winner chip and footnote assumptions (from `build_tco_comparison`).
- "Why go electric" benefit cards + savings banner.
- Talk Tracks (italic quotes, left accent).
- Cross-Sell rows with probability bars.
- **New vs reference:** Configuration card (recommended build with price delta), Finance card (2–3 payment scenarios from `structure_deal`), Trade-In card (valuation + equity, from A4).

**Right – Live Assistance** (`badge-active`)
- Detected Objection alert (danger tint) with quote.
- AI Suggested Response card: approach name in `primary-light`, success rate, **Use This** / **Alternative** buttons; "Use This" records outcome.
- Competitive Intel cards with win %, +/- bullet list.
- Value Snapshot 2×2 mini-stats.
- Eligible Incentives list (eligible = success accent, ineligible = dimmed).
- **New vs reference:** "Next Best Action" from A2 with one-click execution (book test drive, send finance link, schedule callback via the active outreach provider; the Connect voice option only appears when `ENABLE_CONNECT=true`).

**US content mapping for the reference panels.** The reference mockup shows India-market content; the built screen renders the same components with US data from `MarketConfig`:

| Reference element | US rendering |
|---|---|
| `Budget: ₹18L–₹28L` | `Budget: $32k–$45k` |
| `Daily Drive ~45 km/day` | `Daily Drive ~35 mi/day` |
| EV vs ICE table rows | Ex-showroom → MSRP; Running cost/km → Cost per mile ($/mi); Fuel/energy/yr → Annual fuel/energy ($); Range/tank → Range (mi); 0-100 km/h → 0-60 mph; Road tax (MH) → State sales tax + EV registration fee; Refuel time unchanged; footnote → "*Based on ~13,500 mi/yr, $0.16/kWh home charging vs gasoline at $3.60/gal (state defaults, editable)" |
| Incentives (MH road-tax waiver, FAME) | Federal Clean Vehicle Credit ($7,500 point-of-sale, eligibility-checked), state rebate (by `state`), utility charger rebate, OEM APR/cash special |
| `Cost / km` mini-stat | `Cost / mile` |
| Cross-sell (home charger ₹55,000) | Level 2 home charger + install ($1,400), GAP, VSC, prepaid maintenance |
| Competitive intel (Tata Harrier EV) | Segment competitors from `OEM_Catalog.competitors` (e.g., Tesla Model Y, Ford Mustang Mach-E, Hyundai Ioniq 5 for a mid-size EV SUV) |

**Data flow.** Page loads → `POST /journeys/{id}/assist` (A2 → A3 + A4 in parallel) → SSE/poll updates panels as specialists return. Live transcript via existing `aws-transcribe-streaming.ts` → `POST /journeys/{id}/utterance` every ~3 s → objection panel updates.

### 5.4 Screen: Journeys (A2)

Kanban of the 8 stages; card = customer, vehicle, days-in-stage, missing-context badge, assigned advisor, `source=service_lane` chip when spawned by A7. Filters: dealer, advisor, source campaign, stalled. Row click → Advisor screen. Bulk action: "Nudge stalled" (A2 `schedule_outreach` via the active `OutreachProvider`; Connect only when enabled).

### 5.5 Screen: Discovery (A1)

Customer-facing preview + dealer view. Need statement input, shortlist cards (same vehicle-item component), configurator side panel, "Save build & hand off to dealer" (creates/advances journey).

### 5.6 Screen: Trade-In (A4)

Vehicle entry (VIN lookup), valuation range gauge, payoff and equity, upgrade recommendation with payment delta, "Add to journey". Second tab: Upgrade Prospects list (lease-end, aging, service-frequency) with scores.

### 5.7 Screen: Campaigns (A5)

Embedded Amazon Quick dashboard (registered-user embedding), ROI table by campaign/platform/dealer, funnel drop-off by source, recommendation cards ("shift $X from A to B, expected +Y deliveries"), natural-language ask box → `ask_quick`.

### 5.8 Screen: Assistant (A3, Next.js over the AgentCore agent)

Role-aware (sales / BDC / service). Native Next.js console screen (dark tokens), not a Quick embed. Left: today's prioritized list (A2 `publish_shift_list` Tasks, or the service lane), each row showing lifecycle stage chip and the top contextual suggestion. Center: chat with the A3 AgentCore agent scoped to the selected customer; answers cite CRM / DMS / incentive sources retrieved from Quick Index. Right: quick actions (draft follow-up, book test drive, open in Advisor, hand to A2 nurture). Same agent as the live-floor Advisor path (§5.3), different transport. (Was a Quick embed before 2026-09-23; see §13.)

### 5.9 Screen group: OEM Field Sales Workspace (A6, Quick-hosted) — reference `DM_Assistant_Demo.html`

OEM-only (`oem_sales_ops`, `oem_ci_planning`, `oem_finance`). Unlike the dealer console this is a **light-theme** workspace, matching the reference: fixed 56px brand header (brand name + "District Manager Assistant" subtitle; colors from OEM brand tokens, defaults navy `#002C5F` header, dark slate `#1a2332` sidebar, accent cyan `#00aad2`), 220px left sidebar with grouped nav, scrolling center view, and a persistent 340px right **AI chat panel** with mic button and "Ask about dealers, performance, strategy..." input. Every view opens with a KPI grid (4 cards: value, uppercase label, up/down delta) and includes at least one **[AI INSIGHT]** card (green-tinted, bold title, 2–4 sentences with recommended actions). Tables use ranked rows, progress bars (green/blue/orange/red fills), and status badges.

Routes under `/dashboard/oem/*`:

| Nav group | Route | View | Key elements (from reference) |
|---|---|---|---|
| Dashboard | `/oem/today` | Today | District Pulse KPIs (MTD Retail, Obj Achievement, Days Supply, Dealers at Risk); This Week at a Glance; Quick Actions (new contact report, new action plan, meeting in a box, run scenario) |
| Dashboard | `/oem/performance` | Dealer Performance | Monthly scorecard table (Dealer, Retail Units, Obj %, vs PY, vs Peer, Gross/Unit, CSI, Digital Lead Conv, Trend); [AI INSIGHT] Performance Gap Analysis |
| Dashboard | `/oem/composite` | Composite Analysis | Dealer vs Peer Group on composite metrics with bars; [AI INSIGHT] Composite Peer Comparison |
| Field Tools | `/oem/contacts` | Contact Reports | New Contact Report form (dealer select, contact type, key topics, discussion summary with voice dictate, follow-up items with deadlines, submit); Recent Contact Reports table with AI Prep status; [AI INSIGHT] Contact Report Recommendations |
| Field Tools | `/oem/action-plans` | Action Plans | Dealer filter; Active Plan cards (title, milestones with owner / due / status, progress bar); New Action Plan; [AI INSIGHT] Action Plan Recommendations |
| Field Tools | `/oem/alerts` | AI Alerts | Three severity sections (HIGH: immediate action; MEDIUM: action this week; LOW: monitor) each a table of Dealer, Category, Alert, Required Action, Due; [AI INSIGHT] Alert Prioritization |
| Field Tools | `/oem/weekend` | Weekend Tracker | KPIs (Weekend Booked, Weekend Delivered, PY Weekend, Pending Deliveries); Weekend Detail by Dealer (Booked, Delivered, Projected, PY, Appointments Set Mon); [AI INSIGHT] Weekend Sales Prediction |
| Intelligence | `/oem/conquest` | Conquest Intel | Competitor dealer table (Competitor Dealer, Make, Nearest Our Store, Advertised Offer, APR / Lease / Cash-Bonus, Est. Monthly Spend, Impressions, vs Our Offer, Expires); [AI INSIGHT] Competitive Analysis |
| Intelligence | `/oem/ev-readiness` | EV Readiness | KPIs (EV Certified Dealers, EV Training Complete, Charging Stations, EV Units MTD); Dealer EV Certification Status table (Certification, Training, Chargers Installed, Signage, EV % of Sales, Overall); [AI INSIGHT] EV Readiness Priorities |
| Intelligence | `/oem/compliance` | Compliance | KPIs (Fully Compliant, Conditional, Non-Compliant, Compliance Rate, Next Audit Cycle); Franchise Compliance Detail (Dealer Requirement, Status, Required By, Our Response); [AI INSIGHT] Compliance Action Plan |
| Intelligence | `/oem/scenario` | Scenario Planning | Scenario Builder sliders: Allocation Change (−50…+50%), Incentive per Unit (−$500…+$2,000), Days Supply (±15), Market Growth (−5…+10%); Projected Impact cards (Volume, Obj %, Share, Gross, Days Supply, each with delta); [AI INSIGHT] Scenario Planning Guidance; "estimate" disclaimer |
| Intelligence | `/oem/meeting-box` | Meeting in a Box | Select Meeting Type grid (Dealer / District / Area / National month-end close, QBR, New Model Launch Prep, EV Readiness, CSI Action, Fixed Ops Monthly / Quarterly, Parts Report); slide preview grid with labels; Generate / Download / Presentation mode |
| Admin | `/oem/comms` | Sales Ops Comms | AI Search bar (e.g., "where is the updated stair-step incentive announcement?"); Consolidated per-dealer digest; "Forward to Dealers — Add Your Comments" |
| Admin | `/oem/reports` | Report Library | Reports Ranked by Relevance to District Priorities; [AI INSIGHT] Report Reading Priorities |
| Admin | `/oem/guardrails` | AI Guardrails | KPIs (Active Rules, Blocks This Week, Last Rule Update); Key Guardrail Rules table (Rule ID, Description, Category, Status); Guardrail Demo (enter a query, see block + safe alternative); [AI INSIGHT] Guardrail Effectiveness |
| Accounts | `/oem/accounts`, `/oem/accounts/{id}` | Account Plans | Account list with target-gap / at-risk indicators; detail tabs **Account Plan**, **Opportunity Briefs**, **Win Strategy**, **Action Plans**, **Contact Reports**, each rendering the latest versioned artifact with inline-expandable citations, Regenerate, Publish for review; embedded QuickSight *District Sales vs Target* |

**Right-hand AI chat (all views).** A6 `ask` scoped to the current view and selected dealer; supports voice input; answers cite sources and show a shield icon when a guardrail rewrote the answer. Suggested prompts change per view (e.g., on Performance: "Which dealers are trending below objective for the second month?").

**Design tokens (OEM workspace theme).** `--oem-header`, `--oem-sidebar`, `--oem-sidebar-text`, `--oem-accent`, `--oem-kpi`, `--oem-up: #2e7d32`, `--oem-down: #c62828`, `--oem-insight-bg: #f0fff4`, `--oem-insight-border: #c8e6c9`; radius 8px; card shadow `0 1px 3px rgba(0,0,0,.08)`. Defaults come from the reference; each OEM overrides via `OEM_Markets`/brand config. The dealer console (dark) and the OEM workspace (light) share component primitives but not palettes.

### 5.10 Customer Surfaces (`/shop`, `/shop/chat`)

Light theme allowed (OEM brand tokens); same component primitives. `/shop`: lifestyle intake (≤ 4 questions), shortlist cards with "why this fits you," trim/package recommendation with price and est. monthly, VIN availability by ZIP, save build, book test drive. `/shop/chat`: A2 conversation with grounded answers and one-click actions; conversation resumes from any nurture email/SMS via signed link; consent and channel preferences editable in-thread.

### 5.12 Screen: My Garage (Customer, G-01)

`/garage` in the Customer Web app. Passwordless login (email/SMS OTP) or magic link from any nurture message. One page per journey stage from the customer's side:

| Section | Shows | Backed by |
|---|---|---|
| Journey tracker | 8-stage stepper with what's done, what's next, who their advisor is, "message my advisor" (→ A2 chat, escalates to human) | `DEALER_Journeys`, A2 |
| Saved builds | Configurations with price, incentives applied, est. monthly; "find this VIN near me"; share link | `PLAT_Configurations`, A1 |
| Appointments | Test drives, delivery, service; reschedule/cancel | `DEALER_Appointments` |
| **Service** (Process 2) | Book service (due items from `predict_next_service`, open recalls, slot picker, wait / drop / loaner / mobile); live service-journey tracker (8 service stages, promised time, parts hold); **estimate approval** line by line with photo / video evidence, plain-language explanation (`explain_finding`), coverage badge (warranty / recall / customer pay), good-better-best, "ask a question" (→ A7 chat, escalates to advisor); invoice + inspection report in Documents; declined-work list with "book it now"; service history per owned VIN | `DEALER_ServiceJourneys`, `DEALER_ServiceEstimates`, `DEALER_Inspections`, `DEALER_DeclinedWork`, `DEALER_Coverage`, A7 |
| Financing | Prequal status, offers presented (payment scenarios), disclosures; "start prequal" (secure hand-off to F&I connector) | `DEALER_FinanceOffers` |
| Trade-in | Valuation range, expiry, equity vs current payoff; upload photos / update mileage to refresh | `DEALER_Valuations`, A4 |
| Documents | Deal jacket items shared by the dealer, e-sign status (v2), delivery checklist | Document service (§4.9) |
| Ownership | After DELIVER: vehicle(s) owned, mileage, next service due, service history summary (from A7), recalls, upgrade offer when equity turns positive, charger install status | `DEALER_Customers.vehicles_owned`, A7 `get_service_history`, A4 |
| Preferences & consent | Channels, quiet hours, unsubscribe, data download/delete request | `CUST_Consent` |

Dealer roles see a **read-only mirror** of a customer's My Garage inside Advisor ("what the customer sees"), which is how a floor advisor and customer stay in sync.

### 5.13 Screen: F&I Desk (G-02)

`/dealer/fni`. Left: queue of journeys in FINANCE/AGREE with readiness badges (credit app in, trade agreed, VIN reserved). Center: deal worksheet (vehicle, price, trade equity, incentives, lender offers side-by-side, term/rate/payment scenarios from `structure_deal`), **menu presentation** of F&I products (GAP, VSC, tire & wheel, prepaid maintenance, charger install) with AI attach suggestions and customer-facing menu mode (present on second screen / send to My Garage). Right: compliance panel (TILA/Reg Z disclosures from `market_config`, state doc-fee caps, adverse-action reminders), deal history, tasks. Actions: request lender decision (F&I connector), push to DMS (`push_deal_to_dms`), send documents to My Garage, mark AGREE. Attach-rate and PVR KPIs at top (feed §1.3).

### 5.14 Screen: Store Cockpit (Dealer Principal / GM, G-03)

`/dealer/cockpit`. Store-scoped read models, same KPI-grid + [AI INSIGHT] pattern as the OEM workspace but in the dark theme. Tabs: **Funnel** (journeys by stage, stall counts, conversion by source), **Sales** (MTD units vs objective, gross/unit, PVR, advisor leaderboard), **Inventory** (days supply by model, aging, A1 demand signals vs stock), **Trade & Used** (trade capture rate, equity mined from service lane, dispositions), **F&I** (attach rate, product mix), **Service** (fixed-ops summary from §5.20.7: RO count, cycle time, MPI rate, close rate, parts-wait ROs, repurchase/upgrade handoffs, retention), **CX** (CSI trend, lead response SLA, nurture engagement), **Alerts** (A6 alerts for this store, actionable), **OEM Comms** (digest forwarded by the DM). Every tile deep-links into the operating screen (e.g., stalled journeys → Journeys filtered).

### 5.8 (extended) Assistant: Lead Desk and Service Lane (G-04, G-05)

- **Lead Desk** (`/dealer/assistant/leads`): inbound queue from `DEALER_Leads` (web, third-party, chat, phone) with SLA countdown, source, vehicle of interest, A3 first-response script, one-click call/text/email via `OutreachProvider`, book appointment, assign to floor advisor (creates journey ownership handoff). Daily call list Quick Flow lives here.
- **Service Lane** (`/dealer/assistant/service`): the *sales-facing* view of today's service traffic. Lists today's service journeys (from A7, not raw DMS), each with A3 upsell brief, A4 equity/upgrade flag (positive equity, lease-end ≤ 6 months, high mileage, repair-vs-replace), loyalty offer, and **repurchase/upgrade handoff** (A7 `handoff_to_sales` → purchase journey in DISCOVER with `source=service_lane`, notifies an advisor). The *service-facing* operating screens (check-in, inspection, estimate, RO, parts) live in the **Service Desk** (§5.20); Service Lane is a read view over them so the sales floor never edits an RO.

### 5.6 (extended) Inventory & Trade Desk (G-06)

`/dealer/inventory`. Inventory manager view: VIN grid with age, cost, market price (valuation connector comps), A1 demand score (how many active journeys shortlist this model/trim), reserve/unreserve. **Trade disposition** queue: each accepted trade from A4 with keep-for-CPO / wholesale / auction recommendation and reasoning; decision writes to DMS.

### 5.15 Screen: Delivery Board (G-07)

`/dealer/delivery`. Kanban: Scheduled → Prepped → Docs Ready → Delivered. Card: customer, VIN, delivery slot, prep checklist (PDI, detail, accessories, EV charge level, charger install status), document status, customer confirmation state. Actions: reschedule (updates `DEALER_Appointments` and notifies via A2), send delivery-day message, mark delivered (→ DMS `mark_delivered`, stage DELIVER → OWN, triggers survey via A2).

### 5.16 Marketing Studio: Journey Ops (G-08)

`/marketing/journey-ops` for `oem_cx`. **Nurture policy** editor (cadence caps, quiet hours, stop conditions, escalation rules) with versioning; **Templates** (message intents per stage/channel, brand voice, preview with a seeded customer, A/B variants); **Consent rules** by state; **Customer surface content** (intake questions, shortlist copy, FAQ used by `answer_customer_question`); **Nurture analytics** (touches, replies, conversions by intent). Changes publish to `PLAT_ConfigNurture` (versioned) and are picked up by A2 without redeploy. Campaigns (§5.7) and Audiences (Personalize segments → ad platform export) sit alongside in the same app.

### 5.9 (extended) OEM Workspace: Region View (G-09)

Scope switcher in the workspace header (District ▸ Region ▸ National, per role). **Region View** adds: district comparison table (obj %, share, days supply, CSI, alerts open), DM activity (contact reports filed, action plans on track), roll-up of HIGH alerts, region scenario planning, and one-click "open district" which re-scopes every other view.

### 5.17 Admin Console (G-10)

`/admin`. **Users & roles** (Cognito groups, dealer/district assignment, Quick reader provisioning status), **Org hierarchy** (OEM ▸ region ▸ district ▸ dealer group ▸ dealer; PMA), **Market & brand** (`OEM_Markets`, state rules, brand tokens for both themes), **Connectors** (sandbox/live per system, credential rotation via Secrets Manager, health checks, last sync), **Feature flags** (`ENABLE_CONNECT`, Personalize on/off, Quick embedding on/off), **Guardrails & policies** (GR rules editor with test harness, suggestion matrix editor), **Audit** (journey event export, guardrail events, admin actions), **Data requests** (customer download/delete). Dealer admins get the store-scoped subset.

### 5.18 Unified Shell (G-12 … G-19)

One React shell package (`ui/src/shell/*`) mounted by every authenticated app. It owns the top 56px bar; each app renders its own themed body underneath.

**Bar contents (left → right):** app switcher (grid icon → Dealer Console / OEM Workspace / Marketing Studio / Admin, filtered by role), brand, **scope pill** (current dealer / district / region), **global search** (⌘K), **Tasks** badge, **Notifications** bell, help, user menu (profile, theme where allowed, sign out). The bar keeps its own neutral palette so it reads the same over dark and light apps.

#### 5.18.1 App switching
Switching apps preserves the **scope** and the **focused resource** (e.g., viewing dealer D-102 in OEM Workspace → switching to Dealer Console opens D-102's Store Cockpit if the role allows, else the nearest permitted screen). Implemented by the resolver below. From the app switcher, an app opens on the persona's home when the home is in that app, otherwise on the first screen of that app the role holds.

**Landing (decided 2026-10-28, amended 2026-10-29).** Every way into the console lands on the persona's home screen from the §5.2 table (`GET /me/home`): username/password and SSO sign-in, `/`, a dev-token link, starting or ending "View as persona", and My Garage for customers. The admin roles are the exception: the Administrator (`platform_admin`) and the Tenant Admin (`admin_tenant`) land on the demo portal (`/demo`) when no page was asked for, which covers sign-in, `/`, a dev-token link without a path and ending "View as persona". The Dealer Admin (`admin_dealer`) is store-scoped and keeps the Admin Console, because the portal switches personas across the tenant. An admin viewing as a persona carries that persona's claims and lands on that persona's home. Every other persona opens the demo portal from the shell bar's **Demo** button, which every staff role sees. A deep link the user asked for (`?next=`, or the page opened before signing in) is kept, for admins too. A page in an app the role cannot open, or a page outside the role's screens whose read API refuses the role, sends the user to their home instead of an error.

#### 5.18.2 Resource URL contract (deep links)
Every entity has one canonical URL, generated only via `resourceUrl(type, id, ctx)` (TS + Python helper) and resolved by `GET /nav/resolve?type=&id=&ctx=` which returns the best screen for the *caller's* role.

| Type | Canonical | Resolves to (by role) |
|---|---|---|
| `journey` | `/r/journey/{journey_id}` | Advisor (dealer), Journeys card (oem read), My Garage (customer) |
| `customer` | `/r/customer/{customer_id}` | Customer 360 (dealer), Assistant chat scoped (bdc), My Garage (self) |
| `account` / `dealer` | `/r/dealer/{dealer_id}` | Store Cockpit (dealer_manager), Account detail (oem) |
| `deal` | `/r/deal/{deal_id}` | F&I Desk worksheet |
| `alert` | `/r/alert/{alert_id}` | OEM Alerts (dm), Store Cockpit › Alerts (dealer) |
| `task` | `/r/task/{task_id}` | Task detail drawer over the source screen |
| `plan` | `/r/plan/{account_id}/{type}/{version}` | Account artifact viewer |
| `campaign` | `/r/campaign/{campaign_id}` | Marketing Studio |
| `vin` | `/r/vin/{vin}` | Inventory Desk (dealer), Discovery result (customer) |
| `appointment` | `/r/appointment/{id}` | Lead Desk / Delivery Board / My Garage |

All agent outputs, notifications, emails, Quick insights, and tasks must use these URLs (lint rule + API validation). Quick-embedded views receive the contract through embed parameters and emit navigation via `postMessage` to the shell.

#### 5.18.3 Context envelope and handoffs
Deep links may carry `?ctx=<signed JWT>` with `{ from_app, from_screen, reason_type, reason_id, scope }`. The target screen renders a dismissible **"You're here because…"** strip (e.g., "From OEM Alerts: AL-123 CSI declining at Decatur"). Cross-person handoffs (BDC → advisor, advisor → F&I, F&I → delivery) are explicit records in `DEALER_Handoffs { journey_id, from_user, to_user, stage, note, created_at, accepted_at }` shown in the journey activity feed and as a Task for the receiver.

#### 5.18.4 Notification Hub and Tasks
- `DEALER_Notifications { user_id, created_at, type, severity, title, body, resource_url, read_at, delivered_via[] }`. Producers publish `notification.requested` events; the hub applies per-role rules (`PLAT_ConfigNotifications`) and delivers in-shell (bell + inbox page), plus email/SMS/push through `OutreachProvider` for HIGH severity or when the user is offline.
- **Tasks** (`DEALER_Tasks`, extended): `source {agent|user}`, `resource_url`, `sla_due_at`, `assignee`, `team`, `status`, `handoff_id`. Task list page per user/team with filters; task drawer opens over any screen; overdue tasks escalate via notification rules. A2, A3, A6 create tasks only through `POST /tasks`.

#### 5.18.5 Global search
⌘K palette searching customers (name, phone, email), VINs/stock numbers, dealers/accounts, journeys, campaigns, and reports; results grouped by type with role-filtered actions; powered by OpenSearch Serverless index fed from Streams (or Quick Index for documents). Selecting a result uses the resource URL contract.

#### 5.18.6 Journey ownership and activity feed
`DEALER_Journeys.owner_user_id` + `team`; ownership changes only through handoffs. A shared `<JourneyActivityFeed>` component (events, messages, handoffs, agent actions, documents) appears on Advisor, F&I Desk, Delivery Board, Journeys card, and read-only in My Garage (customer-safe subset).

### 5.19 Mobile Layouts (G-20)

Responsive web, no native apps. Mandatory mobile (≤ 480px) layouts: OEM Workspace **Today**, **Alerts**, **Contact Reports** (dictation-first), **Weekend Tracker**; Dealer Console **Assistant › Lead Desk**, **Tasks**, **Advisor quick-look** (Customer 360 + top suggestion + objection response only); Customer Web entirely mobile-first, including **estimate approval** (the most common mobile action in the system). **Tablet (≤ 1024px, landscape)** layouts are mandatory for the Service Desk **Check-in** walk-around and **Inspection / Diagnose** technician views (§5.20.2–3): large touch targets, camera capture, offline queue for photos, voice notes. Shell collapses to bottom tab bar (Home, Search, Tasks, Alerts, Menu).

### 5.20 Screen group: Service Desk (Process 2, A7) — `/dealer/service/*`

**Reference UI (decided 2026-09-22, closes open question 4).** The Service Desk reuses the two existing reference designs rather than introducing a third:

- **Operating screens (5.20.2–5.20.6)** follow `docs/use-cases/sales-advisor-v2.html`: dark dealer theme (§5.1 tokens), `top-bar` with `session-info` + `timer` (re-labelled *promised time*), `nav-tabs`, the three-column `main-layout` (`panel` / `panel-header` / `panel-body`), and the `bottom-bar` AI Log (`log-item`, `log-time`, `log-dot`). Component mapping: `customer-card` + `avatar` + `info-row` → vehicle/RO header; `rec-card` (+ `confidence`, `probability-bar`) → inspection finding and estimate line cards; `incentive-row eligible` + `incentive-check` + `incentive-amount` → coverage rows (warranty / recall / service contract) and estimate line totals; `objection-alert` + `objection-quote` + `response-btns` → estimate pushback (A3 `classify_objection`, service talk track); `compare-table` (`col-ice` / `col-ev`) → good-better-best and repair-vs-replace comparison; `cross-sell-row` → declined-work and due-item add-ons; `btn-present` / `btn-use` → "Present estimate" / "Add to RO"; `savings-banner` → coverage savings ("$412 covered by warranty"); `tag` / `vtag` → green / yellow / red MPI status chips; `activity-item` → service-journey timeline; `ai-response-card` → `explain_finding` preview.
- **Board and Cockpit (5.20.1, 5.20.7)** follow `docs/use-cases/DM_Assistant_Demo.html` re-skinned to the dark tokens: `kpi-grid` / `kpi-card` / `kpi-value` / `kpi-delta` for the fixed-ops KPI strip; `ai-insight` + `ai-insight-title` for the sourced [AI INSIGHT] card on every view (A7 over the same data); `alert-title` / `alert-desc` / `alert-time` rows for the "attention" list (estimates waiting > 20 min, past promised time, back-orders); `cat-filter` / `source-filter` chips for advisor / technician / wait-type filters; `slider-group` / `slider-value` for the repair-vs-replace and labor-rate what-if in the Cockpit; `<table>` pattern from the performance view for the RO list; the persistent right-hand `chat-panel` (`chat-messages`, `chat-input`, `chat-mic`, `voice-dictate`) as the A7 chat for advisors and the dictation surface for check-in concerns and technician notes (AR-7). The demo's guardrail view pattern (`view-guardrails`) is reused for the Admin › AI Guardrails listing of A7 blocks.
- **Customer-facing estimate approval** (My Garage › Service, §5.12) uses the OEM-brand light theme but keeps the `rec-card` / `incentive-row` structure so what the advisor presents and what the customer sees are the same component in two themes (same rule as the F&I menu customer-facing mode, §5.13).

Every screen shows the service-journey stepper (§2.1.1) at top (same component as the 8-stage purchase stepper in §5.3) and the AI Log at bottom. Numbers come from A7 tool calls with `source_ref`; the UI never computes a price. Prior-codebase screens `dashboard/work-orders`, `dashboard/inspections`, `dashboard/diagnostics`, `dashboard/service-history` supply the *information architecture* (fields, states, flows) only; their visual treatment is replaced by the two references above (AR-17/18/19/22).

#### 5.20.1 Service Board (`/dealer/service`) — service advisor / manager home
Layout: DM demo `overview` view pattern (KPI strip + cards + right chat) in dark tokens. Kanban of the 8 service stages; card = customer, vehicle, wait type, promised time, technician, badges (**waiting approval**, **parts hold**, **overdue promise**, **equity flag**). Filters: advisor, technician, wait type, today / tomorrow. Right rail: today's appointments not yet arrived (BOOK), and A7 "attention" list (estimates presented > 20 min with no decision, ROs past promised time, back-ordered parts). Card click → RO view. Bulk: "re-present pending estimates".

#### 5.20.2 Book & Check-in (`/dealer/service/book`, `/dealer/service/{id}/checkin`) — tablet-first
Book: customer / VIN lookup (global search), due items and open recalls pre-filled by `predict_next_service` and `check_coverage`, lane capacity from `scheduling`, wait / drop / loaner / mobile, confirmation via `OutreachProvider`. Check-in: mileage, customer concerns (typed or dictated, AR-7), walk-around photo capture with damage tags, approval preference (text / call / My Garage), promised time; "Open RO" → `open_repair_order` mirrors the DMS RO id. Shows prior declined work for this VIN with one-tap "add to RO".

#### 5.20.3 Inspect & Diagnose (`/dealer/service/{id}/inspect`) — technician tablet
Layout: Advisor three-column `main-layout` (left panel = checklist, center = diagnose, right = draft estimate), touch-sized. Left: MPI template for this model / powertrain (`DEALER_InspectionTemplates`; EV template includes HV battery state of health, charge port, coolant loops). Each item: green / yellow / red, measurement (tread depth, pad thickness, battery SoH), photo / video / voice note evidence (required for yellow / red). Center: customer concerns → **Diagnose** panel: enter DTCs or pull from scan tool connector, `lookup_dtc` shows description, likely causes, related TSBs (cited from Quick Index), recommended tests; technician selects cause → `get_repair_procedure`, `get_labor_time`, `lookup_parts` populate a draft line. Right: draft estimate lines accumulating with coverage badge from `check_coverage`. Submit → `record_inspection` (INSPECT) and `build_estimate` draft (DIAGNOSE). Technician never sees customer contact data or pricing margins.

#### 5.20.4 Estimate & Approval (`/dealer/service/{id}/estimate`) — service advisor
Layout: Advisor screen one-for-one — left `customer-card` + vehicle/RO `info-row`s + stepper; center `rec-card` per estimate line with `confidence` (urgency), `incentive-row` coverage, `compare-table` good-better-best, `btn-present`; right `objection-alert` for pushback, `cross-sell-row` for declined/due items, Next Best Action; `bottom-bar` AI Log. Estimate builder over the technician draft: reorder by urgency (`prioritize_findings`), edit labor / parts (any change re-runs `build_estimate` in Code Interpreter; audit retained), assign coverage, build good-better-best bundles, add customer explanation preview (`explain_finding`). "Present" → `present_estimate` sends the magic link / SMS and mirrors a **customer-facing mode** (second screen or tablet handover) identical to My Garage › Service. Live decision status per line; advisor can record verbal decisions with `decided_by=advisor` and reason. Declined lines → `DEALER_DeclinedWork` with follow-up cadence picker. Objection pushback ("that's too expensive") → A3 `classify_objection` with a service talk track. Repair-vs-replace card appears when estimate exceeds `repair_vs_replace_threshold_pct` of A4 valuation, with a **repurchase/upgrade handoff** action ("Hand to sales", → `handoff_to_sales`).

#### 5.20.5 Repair Order view (`/dealer/service/{id}`) — all service roles, role-aware
Header is role-aware: advisor / manager / parts see customer name and contact; **technician sees VIN, model, tag number, concerns, and lines only** (no customer contact data, no pricing margins, no coverage cost split), enforced server-side by the scope claim, not by hiding fields in the UI. Header fields: customer (masked for technician), VIN, RO id (deep-link to DMS), stage, promised vs **projected** time (A7 `reforecast_promise`, §3.7). Tabs: **Lines** (status in_progress / complete / qc_passed, technician, `update_line_status`), **Parts** (per line: on hand / ordered / ETA / back-order, `track_parts`; parts counter can create orders here), **Inspection** (read-only MPI report), **Estimate** (decisions), **Timeline** (`DEALER_ServiceJourneyEvents` + messages), **Documents** (RO, inspection PDF, invoice). Actions: "Notify ready" (`notify_ready` → READY: shares invoice, offers pickup / delivery slot), "Hand to sales" (repurchase/upgrade handoff), "Message customer" (A7 composes, advisor approves). Service Lane strip (A3 upsell brief, A4 equity) shown to advisor only.

#### 5.20.6 Parts desk (`/dealer/service/parts`) and Service History (`/dealer/service/history/{vin}`)
Parts desk: ROs held for parts, open orders by ETA, back-orders with supersessions, "notify advisor" when received (emits `parts.received` so A7 resumes REPAIR). Service history: all ROs, inspections, declined work, recalls, and prior estimates for a VIN across dealers in the tenant; export to PDF for the customer; feeds A4 `find_upgrade_prospects` and A3 upsell brief.

#### 5.20.7 Service Cockpit (`/dealer/service/cockpit`) — service manager
Layout: DM demo `performance` + `composite` + `scenario` view patterns (KPI grid, sortable table, sliders, [AI INSIGHT]) in dark tokens with the persistent A7 chat panel. Tiles (event-driven read models, QuickSight embed for trends): lane load today / week, RO cycle time by wait type, MPI completion rate, recommended-work close rate, declined-work follow-up conversion, parts-wait ROs and hours lost, estimate approval turnaround, CSI, retention by vehicle age, repurchase/upgrade handoffs and resulting deliveries, technician efficiency (hours billed / clocked). Each tile deep-links to the Board with the filter applied. [AI INSIGHT] card from A7 over the same data, sourced.

### 5.11 Accessibility & i18n

- WCAG 2.1 AA colour contrast on dark theme (verify `text-dim` usage on `bg-card`).
- All interactive elements keyboard reachable; live regions (`aria-live=polite`) for objection and AI Log updates.
- All currency, distance, efficiency, date, and phone formatting via `MarketContext` formatters backed by `Intl.*` with the market's `locale`. Default `en-US` / USD / mi. No literal currency symbols or units in components (lint-enforced).

---

## 6. API Layer

REST, Cognito JWT authorizer on all routes except `/health`. Lambdas in `infra/lambdas/<name>/handler.py`, Python 3.12, shared layer for DynamoDB helpers.

| Method | Route | Lambda | Backing agent/table |
|---|---|---|---|
| GET/POST | `/journeys` | `journeys` | `DEALER_Journeys` |
| GET/PATCH | `/journeys/{id}` | `journeys` | A2 for stage transitions |
| POST | `/journeys/{id}/assist` | `assist` | A2 → A3/A4 (async, returns package_id) |
| GET | `/journeys/{id}/package` | `assist` | `DEALER_SalesPackages` (poll) |
| POST | `/journeys/{id}/utterance` | `assist` | A3 objection detection |
| POST | `/journeys/{id}/events` | `journeys` | Append event (`vehicle_presented`, `response_used`, …) |
| POST | `/discovery/shortlist` | `discovery` | A1 |
| GET | `/configurations/options?model=` | `discovery` | Configurator connector |
| POST | `/configurations` | `discovery` | Save build |
| POST | `/finance/prequalify` | `finance` | F&I connector |
| POST | `/finance/structure` | `finance` | A3 `structure_deal` |
| POST | `/trade-in/valuation` | `tradein` | A4 |
| GET | `/trade-in/prospects` | `tradein` | A4 prospecting |
| GET | `/campaigns/roi` | `campaigns` | A5 |
| POST | `/campaigns/ask` | `campaigns` | A5 `ask_quick` |
| POST | `/journeys/{id}/chat` | `chat` | A2 `answer_customer_question` + `nudge` (customer-facing; signed-link auth) |
| GET/PUT | `/journeys/{id}/nurture` | `journeys` | `DEALER_NurturePlans` (view / pause / resume) |
| GET/PUT | `/customers/{id}/consent` | `customers` | `CUST_Consent` |
| GET | `/appointments/slots?dealer_id=&type=` / POST `/appointments` | `appointments` | Dealer calendar connector; A2 `book_test_drive` |
| POST | `/assistant/suggestions` | `assistant` | A3 `generate_contextual_suggestions` (role, customer) |
| POST | `/assistant/flows/{flow}` | `assistant` | A3 (AgentCore) tools: `service-upsell`, `follow-up-draft` (the `call-list` is A2 `publish_shift_list`) |
| GET | `/accounts`, `/accounts/{id}` | `accounts` | `OEM_Accounts` (OEM roles only) |
| GET | `/accounts/{id}/plans?type=` | `accounts` | Latest `OEM_AccountPlans` artifacts |
| POST | `/accounts/{id}/plans/{type}/generate` | `accounts` | A6 Quick Flow (account_plan / opportunity_brief / win_strategy) |
| POST | `/accounts/ask` | `accounts` | A6 `ask` (guardrail-checked, scoped to view/dealer) |
| GET | `/oem/district/pulse`, `/oem/district/performance`, `/oem/district/composite` | `oem` | A6 `district_pulse`, `get_rdr_actuals`, `compute_composite` |
| GET/POST | `/oem/contact-reports` | `oem` | `OEM_ContactReports`; POST accepts text or audio (dictation) → `create_contact_report` |
| GET/POST/PATCH | `/oem/action-plans` | `oem` | `generate_action_plan`; milestone updates |
| GET/PATCH | `/oem/alerts?district=&severity=` | `oem` | `OEM_Alerts`; acknowledge / resolve |
| GET | `/oem/weekend?district=&weekend=` | `oem` | `predict_weekend` |
| GET | `/oem/conquest?pma=` | `oem` | `get_competitive_offers` |
| GET | `/oem/ev-readiness`, `/oem/compliance` | `oem` | `get_ev_readiness`, `get_compliance` |
| POST | `/oem/scenario` | `oem` | `simulate_scenario` (levers → projections; estimate flag) |
| POST | `/oem/meeting-package` | `oem` | `build_meeting_package` → S3 pre-signed URL |
| GET/POST | `/oem/comms/search`, `/oem/comms/consolidate`, `/oem/comms/forward` | `oem` | Sales Ops Comms |
| GET | `/oem/reports?district=` | `oem` | `rank_reports` |
| GET | `/oem/guardrails/rules`, `/oem/guardrails/events` | `oem` | Rules + `OEM_GuardrailEvents` |
| POST | `/oem/guardrails/test` | `oem` | `check_guardrails` (demo) |
| GET | `/nav/resolve?type=&id=&ctx=` | `nav` | Resource URL contract resolver (§5.18.2), role-aware |
| GET | `/search?q=&types=` | `search` | Global search (OpenSearch Serverless / Quick Index), scope-filtered |
| GET/PATCH | `/notifications`, `/notifications/{id}` | `hub` | Notification Hub inbox, mark read |
| GET/POST/PATCH | `/tasks`, `/tasks/{id}` | `tasks` | Unified Tasks (all agents create tasks here) |
| POST/PATCH | `/handoffs`, `/handoffs/{id}/accept` | `journeys` | Journey ownership handoffs |
| GET/POST/PATCH | `/approvals`, `/approvals/{id}/decide` | `approvals` | Step Functions-backed approvals |
| GET/POST | `/documents`, `/documents/{id}/url`, `/documents/{id}/share` | `documents` | Document service; pre-signed view; share to My Garage |
| GET | `/garage/journey`, `/garage/builds`, `/garage/appointments`, `/garage/finance`, `/garage/trade`, `/garage/documents`, `/garage/ownership` | `garage` | My Garage (customer pool JWT; self scope only) |
| GET/POST/PATCH | `/fni/queue`, `/fni/deals/{deal_id}`, `/fni/deals/{deal_id}/menu`, `/fni/deals/{deal_id}/lender-decision`, `/fni/deals/{deal_id}/push`, `/fni/journeys/{journey_id}/deal` | `fni` | F&I Desk. `POST /fni/journeys/{id}/deal` opens the desk on a journey (deal desked from its saved build; idempotent). `push` (`push_deal_to_dms`) is refused (409, `gate: vehicle`) until a VIN is reserved for the journey, and writes that `vin` on the deal and journey. The worksheet returns `price.breakdown` (vehicle + tax + doc fee − point-of-sale incentives = total) and labels each incentive `price` / `apr` / `informational`; an OEM APR program applies to the captive offer when the buyer's tier meets `min_tier` |
| GET | `/dealer/cockpit/*` (funnel, sales, inventory, trade, fni, cx, alerts) | `cockpit` | Store Cockpit read models (materialized from events) |
| GET/POST/PATCH | `/leads` (POST: web-form / chat / phone intake — resolves the customer by id → e-mail → new row, writes the lead `new` with its SLA clock, emits `lead.received`), `/leads/queue`, `/leads/{id}/script`, `/leads/{id}/assign`, `/leads/{id}/respond`, `/leads/{id}/appointment` | `leads` | Lead Desk with SLA. Queue cards carry journey stage/owner, `pending_handoff` (ownership moves on accept) and the booked appointment; assign copies the lead's model and comments into the journey so A1 ranks the requested model first |
| GET | `/traces?journey_id=\|service_journey_id=\|lead_id=\|correlation_id=` | `traces` | AI Log / Activity feed; `lead_id` returns the lead's own runs plus its journey's |
| GET | `/service/lane?date=` | `service` | Service Lane (sales-facing read view over A7 service journeys + A3/A4 flags) |
| GET/POST/PATCH | `/service/journeys`, `/service/journeys/{id}`, `/service/journeys/{id}/events` | `service` | Service Board; service-journey state (A7 `validate_service_handoff` on PATCH stage) |
| GET/POST | `/service/availability?dealer&date&services`, `/service/bookings`, `/service/bookings/{id}` (PATCH/DELETE) | `service` | Booking via `scheduling` connector; `predict_next_service` + open recalls in GET |
| POST/GET/PATCH | `/service/ros`, `/service/ros/{ro_id}`, `/service/ros/{ro_id}/lines/{line_id}/status`, `/service/ros/{ro_id}/close` | `service` | RO mirror; `open_repair_order`, `update_line_status`, `notify_ready` |
| GET/POST | `/service/ros/{ro_id}/inspection`, `/service/inspection-templates?model_id=` | `service` | MPI submit / read; templates |
| GET | `/service/dtc?codes=&vin=`, `/service/procedures?vin=&labor_op=`, `/service/labor?vin=&labor_op=`, `/service/parts?vin=&labor_op=|part_no=`, `/service/coverage?vin=` | `service` | Diagnose lookups (dtc, labor, parts, warranty connectors; Quick Index for procedures/TSBs) |
| POST/GET/PATCH | `/service/ros/{ro_id}/estimate`, `/service/estimates/{id}`, `/service/estimates/{id}/present`, `/service/estimates/{id}/decisions` | `service` | `build_estimate`, `present_estimate`, `record_approval` (staff); decisions also via `/garage/service/...` |
| GET/POST/PATCH | `/service/parts/orders`, `/service/parts/orders/{id}`, `/service/parts/held-ros` | `service` | Parts desk; emits `parts.*` events |
| GET/PATCH | `/service/declined?dealer&due_before=`, `/service/declined/{id}` | `service` | Declined-work register and follow-up cadence |
| GET | `/service/history/{vin}` | `service` | Service history read model |
| POST | `/service/journeys/{id}/handoff-to-sales` | `service` | A7 `handoff_to_sales` → A4 equity → A2 purchase journey (`source=service_lane`) |
| GET | `/service/cockpit/*` (lane, cycle, mpi, close-rate, declined, parts-wait, retention, handoffs) | `service-cockpit` | Service Cockpit read models |
| GET/POST | `/garage/service`, `/garage/service/book`, `/garage/service/{id}`, `/garage/service/estimates/{id}/decisions`, `/garage/service/history` | `garage` | My Garage › Service (customer pool JWT; self VINs only; magic-link scoped token for estimate decisions) |
| GET/PATCH | `/inventory`, `/inventory/{vin}/reserve`, `/trades/dispositions`, `/trades/{id}/decide` | `inventory` | Inventory & Trade Desk |
| GET/PATCH | `/deliveries`, `/deliveries/{journey_id}` | `delivery` | Delivery Board |
| GET/PUT | `/config/nurture`, `/config/notifications`, `/config/suggestions`, `/config/guardrails`, `/config/autonomy` (+ `/publish`) | `config` | Versioned config editors (Journey Ops, Admin); `/config/autonomy` = autonomy levels per action and scope (§4.10.1) |
| POST/PATCH/GET | `/recommendations` (shown), `/recommendations/{id}/accept`, `/recommendations/{id}/dismiss`, `/recommendations/stats?agent&action_id&scope` | `recommendations` | Outcome loop writes from the shell and aggregate reads for agents and Admin › Agents (§4.10.3) |
| GET | `/agents/actions` | `agents` | Registry of agent action ids, default autonomy levels, constraints schema (drives the Admin editor) |
| GET/POST/PATCH | `/admin/users`, `/admin/org`, `/admin/connectors`, `/admin/flags`, `/admin/audit/export`, `/admin/data-requests` | `admin` | Admin Console |
| GET | `/config/market?market_id=us` | `config` | `OEM_Markets` + `OEM_StateRules` (public, cached) |
| GET | `/config/outreach` | `config` | Active outreach provider + available channels |
| POST | `/outreach/send` | `outreach` | A2 `schedule_outreach` (provider-agnostic) |
| POST | `/a2a/{agent}/messages` | `a2a-adapter` | Existing adapter, retained |
| GET | `/customers/search`, `/customers/{id}` | `customers` | Existing, retargeted to `DEALER_Customers` |

---

## 7. Infrastructure & Deployment
The deployment is implemented in `deploy/` and `app/infra/`. [`docs/DEPLOYMENT.md`](../DEPLOYMENT.md) and [`deploy/README.md`](../../deploy/README.md) are the operator guides, [`docs/as-built/architecture.md`](../as-built/architecture.md) describes the deployed topology, and [ADR 0007](../adr/0007-deployment-approach.md) records the approach. This section states the requirements that tooling meets.
### 7.1 Principles
- **Direct-API Python deployers, no templates.** One boto3 deployer per stage in `app/infra/` (dry run by default; `--apply`, `--delete`, usually `--status`). `deploy/deploy.sh` only orders them and passes flags through; any deployer can run alone. No Docker, CDK, CloudFormation or script patching.
- **Idempotent, ordered stages:** `cognito`, `data`, `sso-admins`, `events`, `lake`, `corpus`, `guardrails`, `kb`, `l4`, `approvals`, `l6`, `demo-reset`, `alarms`, `agentcore`, `gateway`, `quick`, `console`, `waf`. `--only` / `--skip` pick stages; every script is safe to re-run and stops at the first failing stage with its name.
- **Entry points:** `deploy/bootstrap.sh` (fresh account: preflight, JWT layer, `deploy.sh --apply`, extra markets, smoke), `deploy/verify.sh` (smoke, acceptance, write loop, agent eval, guardrail calibration, Knowledge Base, runtime and agent checks; ends `VERIFY: ALL PASS`), `deploy/destroy.sh` (teardown, §9A AR-12) and `deploy/guidance-deploy.sh` (unattended one-click deployment for the AWS Guidance, Linux / CodeBuild).
- **Amazon Quick is an optional stage.** `quick` needs an Amazon Quick Enterprise subscription and five manual console steps (`docs/QUICK_BUILD.md`); the one-click deployment skips it and the console hides its Quick panels (`ENABLE_QUICK=false`).
- Region default `us-east-1`. Markets: `--markets us` (default) or `us,de` on `bootstrap.sh` (`--market` on `deploy.sh`) loads the synthetic packs.
- Federated staff sign-in is optional: `RETAIL_OPS_ENABLE_SSO=1` with the `RETAIL_OPS_IDP_*` settings in `deploy/config.env` adds an OIDC or SAML identity provider to the staff pool; the Administrator allowlist is applied by the `sso-admins` stage (§8.1).
- The agent jobs' schedules ship enabled at *recommend* autonomy; `--enable-jobs` also enables autonomy promotion.
- Process 2 (Service) is **not** a flag: A7, the service tables, the Service Desk routes and the service seed pack always deploy. It is one product with two processes, not a module.
### 7.2 A deployment creates
The stage table in [`docs/external/04-deployment.md`](../external/04-deployment.md#the-ordered-stages) is authoritative. In summary: the Cognito staff pool `retail-ops-<env>-staff` with the role groups of §5.2 (the Administrator role `platform_admin` is deliberately not a group: the `sso-admins` stage writes it into `PLAT_Users` for allowlisted identities only, §8.1), development seed users, the web and hosted-UI clients and the client-credentials client for the Quick MCP connector (its secret in Secrets Manager); 68 on-demand DynamoDB tables loaded from the synthetic packs; the event bus `app-bus-<env>` with its subscriber Lambda, rule and dead-letter queue; the event lake (S3 bucket, Firehose stream, Glue database with 13 tables, Athena workgroup, analytics roll-ups); the 538-document corpus; two Bedrock Guardrails (main and store digest); the Bedrock Knowledge Base on an S3 Vectors index; per-function IAM roles, the Lambda authorizer, service Lambdas and the REST API; the plan-approval Step Functions state machine; the job Lambdas and EventBridge Scheduler schedules; CloudWatch alarms; six AgentCore runtimes; the IAM-authenticated AgentCore Gateway for the console and the JWT-authenticated Gateway for Amazon Quick; the console's S3 bucket, CloudFront distribution, origin access control and URL-rewrite function; and the AWS WAF web ACL.

**Target, not provisioned by `deploy/`:** a customer user pool with email OTP and magic links, DynamoDB Streams and an EventBridge Schema Registry, an OpenSearch Serverless collection for global search, the `app-docs` document bucket, an SES sender identity and SNS SMS topic (outreach falls back to dealer tasks until `RETAIL_OPS_SES_FROM` names a verified identity), Amazon Connect (`ENABLE_CONNECT`), the telematics connector, Amazon Personalize, AgentCore Memory, and Secrets Manager entries for live connectors.
### 7.3 Agents
Agent code lives in `app/services/agents/` with the AgentCore entry point, registry and client in `app/agentcore/`. The `agentcore` stage builds one zipped code bundle per agent (Linux arm64 wheels through pip's cross-platform download, so any x86_64 machine can build it), uploads it to S3 and runs it on the AgentCore managed Python 3.12 runtime: one runtime per agent ([ADR 0009](../adr/0009-one-runtime-per-agent.md)), the Journey Agent last because it calls the others. The stage then re-runs `l4` and `l6` to wire the runtime ARNs into the API and job Lambdas.
### 7.4 Configuration
Each deployer writes the handles later stages need to `app/infra/<name>-config.json` (`<name>-config.<env>.json` for an environment other than `dev`), and later stages read them; Lambdas and agents receive them as environment variables at deploy time (no in-place script patching). Optional operator settings go in `deploy/config.env`, copied from `deploy/config.env.example`. Both are account-specific and git-ignored.
### 7.5 Environments
Every resource is named from one environment name (`retail-ops-<env>-…`, `app/infra/naming.py`), set with `--env <name>` or `RETAIL_OPS_ENV` (default `dev`; 2–8 lowercase letters and digits), so several environments can share an account. `deploy/destroy.sh` refuses an environment name that contains "prod". A production environment additionally requires live connectors, point-in-time recovery on the tables and identity-provider sign-in with the seed users removed (target).
---
## 8. Security, Privacy, Observability

- Least-privilege IAM per role: replace `Resource: "*"` in current policies with table/agent ARNs (see R-4).
- PII (contact details, credit prequal results) encrypted at rest (DynamoDB default KMS), redacted in traces and Quick datasets (hash `customer_id`, drop contact fields).
- Bedrock Guardrails attached to A1/A3 for PII-in-output and off-topic filtering; to A7 for scare-tactic / unverified-failure language in customer-facing estimate explanations (§3.7 guardrails).
- Journey event log and service-journey event log are the audit trail; every stage transition records `actor`, `agent`, `evidence`. Every estimate line decision records `decided_by` (customer vs advisor) and the presented evidence snapshot.
- AgentCore observability + CloudWatch dashboards: agent latency p50/p95, tool error rate, objection-detection latency, handoff-blocked count, stalled-journey count, estimate-approval push latency (target ≤ 60 s), ROs past promised time, parts-hold count.
- Alarms: Lambda errors > 1%, agent invocation failures, Firehose delivery failures, event bus DLQ depth, connector sync failures.
- **Correlation id** (G-26): the shell mints `x-app-correlation-id` per user action; propagated through API Gateway, Lambdas, AgentCore invocations (as session attribute), Gateway → Quick calls, and EventBridge events. CloudWatch Logs Insights and AgentCore traces join on it; the AI Log shows it on hover for support.

### 8.1 Identity Fabric (G-14)

| Population | IdP | Session | Notes |
|---|---|---|---|
| Dealer + OEM staff | **Cognito User Pool `app-staff`** (groups in §5.2) with **two sign-in paths from day one**: (a) Cognito-native username/password (`amazon-cognito-identity-js`, existing `CognitoAuthService.login`), and (b) **AWS federated via the enterprise OIDC/SAML IdP** through the Cognito Hosted UI (`identity_provider=CorpDirectory`, auth-code exchange at `/oauth2/token`, existing `initiateSsoLogin` / `handleOAuthCallback`). Login page shows both buttons; federated users are mapped to groups via a pre-token-generation Lambda reading `PLAT_Users`. | One JWT used by all Next.js apps via the shell; refresh shared; federated sessions 8 h, native 1 h + refresh (existing behaviour) | Bootstrap must create the Cognito domain, the `CorpDirectory` OIDC IdP (issuer, client id/secret from Secrets Manager per internal Federate registration guidance), and app-client callback/logout URLs for each CloudFront app. Optional later: OEM corporate IdP (Okta/Entra) and dealer-group IdPs as additional Cognito IdPs. |
| Quick embedding | **IAM Identity Center** with Cognito as the external IdP (SAML) | Shell requests a Quick embed URL for the signed-in user; no second login | Quick users auto-provisioned on first access (SCIM or JIT Lambda) and added to the Quick group for their role; Quick content is not row-scoped: group membership decides what a user can open, and they see all rows of it (§13, 2026-10-21) |
| Customers | **Cognito User Pool `app-customers`**, passwordless: **email OTP + magic links** in the pilot; SMS OTP enabled once 10DLC is approved | Separate cookie domain; no staff access | Journey/customer claims bind the session to `customer_id`; dealer "view as customer" is a server-side read-only mirror, never the customer's session |
| Agents (system) | IAM roles per agent; AgentCore identity | – | Agents act with a service principal and record `actor.user` from the originating request for audit |
| Connect agents (optional) | Connect SSO federated from Identity Center | Agent workspace opens Retail Ops context panel with the same JWT | |

**SSO allowlist and the Administrator (decided 2026-10-21).** A federated
sign-in is accepted only when it resolves to a `PLAT_Users` row; otherwise the
authorizer answers "unknown user" (401). The aliases listed in
`RETAIL_OPS_SSO_ADMINS` (comma-separated, in the git-ignored
`deploy/config.env`) get the Administrator role (`platform_admin`, §5.2). The
deploy stage `sso-admins` (`app/infra/sso_admins.py`, after `data`) syncs one
row per listed alias: `user_id` `U-ADMIN-<alias>`, `username` `<alias>`,
`groups` `[platform_admin]`, scope the OEM root of the deploy market, marker
`provisioned_by = "sso-allowlist"`. A marker row whose alias is no longer listed
is deleted; rows without the marker are never changed (writes are conditional on
the marker). An unset variable changes nothing; an empty value removes every
Administrator. Username/password sign-in and every other persona are unchanged.

- *Assumption:* the enterprise IdP's `sub` is the IdP alias. Cognito maps it to
  `preferred_username`, and the federated Cognito username is
  `CorpDirectory_<sub>`. Aliases are matched case-insensitively and an
  `@corp.example.com` suffix is ignored, both in the allowlist and at sign-in.
- *Matching rule:* an allowlist row matches only a sign-in from the IdP recorded
  on it (`federated_identity` = `<IdP>:<alias>`, from `RETAIL_OPS_IDP_NAME`,
  default `CorpDirectory`): the Cognito username's prefix before the first `_`
  must equal that IdP, and the token's `preferred_username`, `username` or
  `<sub>` must name the row's alias. A native (unprefixed) token, or a token
  from any other IdP, never resolves to an Administrator row ("unknown user",
  401). Before a different provider is attached, the allowlist is emptied
  (`RETAIL_OPS_SSO_ADMINS=` and the `sso-admins` stage) or the new provider gets
  its own `RETAIL_OPS_IDP_NAME`.
- *Resolution:* the authorizer's existing `preferred_username` → `PLAT_Users.username`
  match finds the row; a last step accepts the same alias claims of a federated
  token after lower-casing and dropping `@corp.example.com`, and only for rows that
  carry the allowlist marker. Whichever step finds an allowlist row, the
  matching rule above is checked before the row is accepted.
- *Gateway boundary:* the Administrator is a platform operator (persona `PLAT`),
  not a dealer persona, so it reads across directly like the admin roles. Dealer
  personas are still forced through the AgentCore Gateway, including an
  Administrator viewing as a dealer persona.
- *Quick:* the Administrator joins the `retail-ops-builders` sharing group as a
  reader when the per-user embed registers it.
- *Control:* the allowlist. An allowlisted alias can read and administer across
  every district and dealer, so the list stays short and is reviewed like any
  other administrator grant.

Authorization: **RBAC + scope**. Every API call is evaluated as `(role, action, resource scope: tenant ▸ region ▸ district ▸ dealer ▸ self)`. Scope claims come from `PLAT_OrgHierarchy`; a Lambda authorizer resolves them once per token and caches. OpenSearch filters are derived from the same claims so the API and search never disagree on visibility. Amazon Quick content is not row-scoped (§13, 2026-10-21): Quick group membership decides who can open a dashboard or dataset, and anyone with access sees all rows.

### 8.2 Data Classification and Movement (G-27)

| Class | Examples | Allowed in | Never in |
|---|---|---|---|
| **C1 Public** | Catalog, MSRP, incentives, brand content | Everywhere incl. Quick Index, Personalize | – |
| **C2 Internal** | RDR actuals, composite scores, alerts, plans, comms | Console, Quick (group access, not row-scoped), lake (partitioned by scope) | Customer Web |
| **C3 Personal (PII)** | Names, contact details, addresses, signals, messages | DynamoDB (KMS), Console with scope, My Garage (self only) | Quick Index (masked/hashed only), Personalize (hashed user ids), lake (hashed) |
| **C4 Sensitive** | Credit prequal results, SSN fragments, income, trade payoff, contract terms | F&I Desk and My Garage (self) only; F&I connector | Quick, Personalize, lake, agent memory, traces, notifications |

Enforcement: field-level tags in the shared data models; the Quick/lake export jobs drop or hash C3 and refuse C4; `write_trace` redacts C3/C4; A2 memory stores preferences, not C4. Customer data-subject requests (download/delete) run from Admin › Data requests across all stores including Personalize datasets and Quick Index.

---

## 9. Migration from Current Codebase

| ID | Current asset | Action |
|---|---|---|
| R-1 | `agents/prospecting` (lease/aging/service use cases) | Fold into **A4 Trade-In & Upgrade Advisor** as `find_upgrade_prospects` |
| R-2 | `agents/recommendation` + `agents/objection_handler` | Split: recommendations → **A1**; live objection/incentive/deal → **A3** |
| R-3 | `infra/bootstrap.py` "Patch Deploy Scripts" step | **Remove.** It corrupted three deploy scripts during the 2026-09-10 deploy (broken f-strings). Replace with env-var injection from `bootstrap-config.json` |
| R-4 | IAM policies with `Resource: "*"` | Scope to specific ARNs |
| R-5 | `agents/supervisor` | Becomes **A2**; add journey state machine + handoff validation + Connect tools |
| R-6 | `SalesAdvisor_*` tables | Migrate seed data into the owner-prefixed schema (`OEM_*` / `DEALER_*` / `CUST_*` / `PLAT_*`) via the seed loader; keep old tables until cutover |
| R-7 | `ui/src/app/dashboard/*` (production, transfers, work-orders, inspections, etc.) | Remove non-retail routes; keep customers, prospects (→ trade-in), plays (→ journeys automation), settings |
| R-8 | `ui/src/lib/*-service.ts` | Reuse `cognito.ts`, `agentcore-service.ts`, `lambda.ts`, `aws-transcribe-streaming.ts`; add `journeys-service.ts`, `discovery-service.ts`, `finance-service.ts`, `tradein-service.ts`, `campaigns-service.ts` |
| R-9 | `scripts/seed_csv_data.py` followups rows missing `followup_id` | Fix in new seed (generate ids) |
| R-10 | `ObjectionStrategies` seed and `SalesPlays` templates | Carry forward unchanged |

---

## 9A. Add-On Requirements from the Existing Codebase

The prior `automotive-sales-advisor` codebase implements capabilities that the Retail Ops requirements (§1–8) either under-specify or omit. This section promotes them to explicit requirements so a from-scratch build carries them deliberately rather than by accident. Each has a disposition:

- **Adopt** — becomes a first-class Retail Ops requirement now.
- **Optional** — build if pilot time allows; behind a feature flag.
- **Deferred** — recorded so it is not lost; not in pilot scope.
- **Drop** — explicitly out of scope (see §12); listed here only to close the loop.

### 9A.1 Agent runtime building blocks (AgentCore primitives already proven in code)

| ID | Capability | Source in code | Disposition | Retail Ops mapping |
|---|---|---|---|---|
| AR-1 | **RAG knowledge base (vector store).** In-process ChromaDB with 6 collections (customers, objection_strategies, competitor_intel, segment_patterns, vehicle_inventory, sentiment_lexicon), local MiniLM-L6-v2 embeddings, no external keys. | `agents/objection_handler/rag/knowledge_base.py` | **Adopt** | Grounding layer for A1/A3 alongside Quick Index. Pilot: keep the ChromaDB pattern for local/dev and single-agent semantic recall; production path is Amazon Bedrock Knowledge Bases (OpenSearch Serverless vector) so it aligns with §5.18.5. Add a `KnowledgeBase` interface with `local_chroma` and `bedrock_kb` implementations, selected by `INTEGRATION_MODE`. Collections map to `DEALER_ObjectionStrategies`, `OEM_Catalog.competitors`, `DEALER_Outcomes`, `DEALER_Customers`, `DEALER_Inventory`. |
| AR-2 | **AgentCore Code Interpreter for financial math.** Equity position, cost trajectory, ROI, keep-vs-upgrade computed in a sandboxed code environment for accuracy and auditability. | `agents/prospecting/src/sales_prospecting/tools/code_interpreter_tools.py` | **Adopt** | A4 `compute_equity`, `estimate_payment_delta` and A1 `build_tco_comparison` run in AgentCore Code Interpreter; A3 `structure_deal` may use it for payment math. Every computation returns inputs + code + result for the audit trail (satisfies acceptance criterion 4, "traceable to a tool call"). |
| AR-3 | **AgentCore Browser for live market data.** Tools for vehicle reviews / market comps, mocked in demo, designed to scrape live via AgentCore Browser in production. | `agents/recommendation/src/vehicle_recommendation/tools/browser_tools.py` | **Optional** | A4 `get_market_comps` and A6 `research_account` (where Quick Research is not licensed) use AgentCore Browser with an allow-list. Pilot default: sandbox connectors return open-data-anchored values; Browser is the live upgrade path. |
| AR-4 | **AgentCore Memory tools.** Store/retrieve recommendation and prospect records and history. | `agents/*/tools/memory_tools.py` | **Adopt** | Already implied by A2 `get_customer_memory` (§3.2). Requirement: use AgentCore Memory (not a bespoke table) for per-customer lifestyle profile, stated objections, channel prefs, promises; the code's `memory_tools.py` is the reference implementation. |
| AR-5 | **AgentCore Gateway client + "Agent OS" client abstraction.** Front-end/service clients that call agents and Gateway tools uniformly. | `ui/src/lib/agent-os-service.ts`, `agent-os-constants.ts`, `gateway-service.ts`, `agentcore-service.ts` | **Adopt** | These become the shell's agent-invocation layer behind the resource URL / A2A contracts (§3.2, §4.3.1). Requirement: one client module the five apps share for `InvokeAgentRuntime` and Gateway→Quick calls, carrying `correlation_id`. |
| AR-6 | **AgentCore Observability (X-Ray + CloudWatch spans).** Structured trace spans, logs, and metrics for every agent step. | `agents/prospecting/src/sales_prospecting/observability.py` | **Adopt** | Realizes §8 observability. Requirement: every agent emits X-Ray subsegments + CloudWatch EMF metrics keyed on `correlation_id`; the `observability.py` context-manager pattern is the reference. |

### 9A.2 Voice and real-time interaction

| ID | Capability | Source in code | Disposition | Retail Ops mapping |
|---|---|---|---|---|
| AR-7 | **Full voice-assistant pipeline.** Streaming mic → Amazon Transcribe Streaming → agent → Amazon Polly response, with a React hook and service layer. | `ui/src/lib/aws-transcribe-streaming.ts`, `transcribe-service.ts`, `voice-service.ts`, `ui/src/hooks/use-voice-assistant.tsx` | **Adopt** | The spec lists Transcribe/Polly for the A3 floor path and A6 dictation but not a reusable voice hook. Requirement: a shared `useVoiceAssistant` hook powering (a) A3 live-transcript objection detection, (b) A6 contact-report dictation, (c) optional voice input on the A2 customer chat and A6 planner chat. Provides the transcript-replay demo mode (`docs/specs/retail-ops-synthetic-data-plan.md` decision D1). |
| AR-8 | **Live Session screen.** A dedicated real-time session view (timer, transcript, live panels). | `ui/src/app/dashboard/live-session/` | **Adopt** | This is the concrete implementation behind the Advisor (live) screen (§5.3). Requirement: `/dealer/advisor/{journey_id}` reuses the live-session layout and timer. |

### 9A.3 Operator and observability screens

| ID | Capability | Source in code | Disposition | Retail Ops mapping |
|---|---|---|---|---|
| AR-9 | **Agent status / diagnostics screen.** UI to view agent health and activity. | `ui/src/app/dashboard/agents/`, `dashboard/diagnostics/` | **Optional** | Add an **Agents** panel to the Admin Console (§5.17): per-agent health, recent traces, tool error rate, latency p50/p95, guardrail blocks — a UI surface over §8 metrics. Useful for demos ("watch the agents work"). |
| AR-10 | **Sales Plays automation engine.** Reusable multi-step "plays" (Full Prep, Handle Objection, Recommend Vehicle, Conquest Pitch, Callback Brief, Close the Deal) with `SalesPlays` / `PlayRuns` tables and a runner. | `infra/create-plays-table.py`, `infra/lambdas/plays/`, `ui/src/lib/plays-service.ts`, `dashboard/plays/` | **Adopt (reframed)** | R-7 mapped plays vaguely to "journeys automation." Firm requirement: Plays become **A2 orchestration templates** — named, parameterized multi-agent workflows the DM/advisor can trigger on a journey (e.g., "Full Prep" = A1 + A3 + A4 in parallel → Sales Package). Carry the 6 templates (R-10); store as `DEALER_Plays` / `DEALER_PlayRuns`; surface a "Run Play" action on Journeys and Advisor. |
| AR-11 | **Proposal / sales-package generation.** Structured customer-facing proposal document. | `ui/src/lib/proposal-data.ts` | **Adopt** | Feeds `DEALER_SalesPackages` (§3.2) and the Document service (§4.9). Requirement: A2 `aggregate_package` produces a shareable proposal PDF to My Garage (§5.12) using this data shape. |

### 9A.4 Infrastructure and operations

| ID | Capability | Source in code | Disposition | Retail Ops mapping |
|---|---|---|---|---|
| AR-12 | **Teardown / destroy tooling.** Selective and full teardown of agents, CloudFront, and stack resources. | `infra/destroy-agents.py`, `destroy-cloudfront.py`, `scripts/destroy.sh` | **Adopted** | Implemented as `deploy/destroy.sh`: finds one environment's resources by name and deletes them, dependents first; dry run unless `--confirm --account <id>` matches the caller's account; `--only` selects stages; DynamoDB tables and the lake bucket only with `--include-data`; refuses an environment name containing "prod". Needed for repeatable pilot environments. |
| AR-13 | **Agent resource policies for browser invocation.** Sets AgentCore resource policies so agents are invokable from the browser via Cognito Identity Pool creds. | `infra/set-agent-resource-policies.py` | **Adopt** | Requirement of the identity fabric (§8.1): bootstrap sets agent resource policies scoped to the authenticated Cognito role; retained as a deploy step. |
| AR-14 | **Local agent test harnesses.** Per-agent `test_local.py` and a `test-lambda` route. | `agents/*/test_local.py`, `ui/src/app/test-lambda/` | **Optional** | Keep a local invoke harness per agent for dev; the `test-lambda` UI route is dev-only and excluded from production builds. |
| AR-15 | **OpenClaw / Bedrock integration.** A Bedrock integration config and doc. | `infra/openclaw-bedrock.yaml`, `docs/OPENCLAW-INTEGRATION.md` | **Deferred** | Not referenced by Retail Ops. Record and park; revisit only if the specific integration it provides is needed. Requires an owner to explain intent before adoption. |

### 9A.5 Service, parts, and manufacturing operations

The prior codebase includes a substantial **service / parts / manufacturing operations** product. On 2026-09-10 this was drawn outside the retail boundary. On **2026-09-22 Service & ownership became Process 2** (§1.1, §2.1.1, §3.7), so the dealer-service capabilities are re-dispositioned below. The test is now: *does it serve the service journey (BOOK → FOLLOW_UP) or the retail funnel?* Manufacturing and procurement still fail that test.

| ID | Capability | Source in code | Disposition (2026-09-10 → 2026-09-22) | Retail Ops mapping |
|---|---|---|---|---|
| AR-16 | Production scheduling & optimization | `dashboard/production`, `production-optimizer`, `production-service.ts`, `production-schedule.json` | **Drop** (unchanged) | OEM plant scheduling. Not a dealer retail or service concern; no persona in §5.2. Out of scope (§12). |
| AR-17 | Work-order management | `dashboard/work-orders` | Drop → **Adopt (reframed)** | Becomes the **Repair Order view** (§5.20.5) and `DEALER_RepairOrders` mirror. Reframe: the DMS stays system of record for the RO and invoice; Retail Ops owns the *service journey*, line status visibility, parts hold, and customer communication around the RO. Work-order CRUD that duplicates the DMS is not carried. |
| AR-18 | Diagnostics / DTC lookup | `dashboard/diagnostics`, `dtc-database.json` | Drop → **Adopt** | A7 `lookup_dtc` / `get_repair_procedure` and the Diagnose panel (§5.20.3). `dtc-database.json` seeds `DEALER_DTCReference` for the sandbox `dtc` connector; live path is OEM service info / Mitchell / ALLDATA. |
| AR-19 | Vehicle inspections (VSA) | `dashboard/inspections`, `vsa.json` | Drop → **Adopt** | A7 `record_inspection` and the technician Inspect view (§5.20.3). `vsa.json` becomes the first `DEALER_InspectionTemplates` version; adds EV template, evidence requirement on yellow/red, and the estimate hand-over that the prior screen lacked. |
| AR-20 | Inter-store inventory transfers | `dashboard/transfers`, `transfer-service.ts`, `transfer-orders.json` | **Deferred** (unchanged) | Vehicle transfers between stores are a dealer-group inventory feature, not a service-journey step. Revisit with dealer-group scope post-pilot. Parts transfers between stores are covered by the `parts` connector's availability lookup, not by this feature. |
| AR-21 | Supplier / group-purchasing (GPO) & parts | `supplier-service.ts`, `gpo-service.ts`, `components.json` | Drop → **Split**: parts-on-RO **Adopt**, GPO / supplier procurement **Drop** | Parts *availability, ordering, and back-order status for an RO* is a service-journey concern (A7 `lookup_parts`, `track_parts`; `DEALER_Parts`, `DEALER_PartsOrders`; Parts desk §5.20.6). Supplier negotiation, group purchasing, and component sourcing remain procurement and stay out (§12). |
| AR-22 | Full service-history management | `dashboard/service-history` | Deferred → **Adopt** | Service History screen (§5.20.6) and My Garage › Ownership / Service (§5.12), fed by `DEALER_RepairOrders`, `DEALER_Inspections`, `DEALER_DeclinedWork` with DMS backfill. Also feeds A4 `find_upgrade_prospects`. |

**Decision recorded (2026-09-22):** the service-department capabilities have their use case (Process 2) and personas (service advisor, technician, parts counter, service manager, owner). AR-17/18/19/22 and the parts half of AR-21 are in pilot scope under A7. AR-16, AR-20, and the procurement half of AR-21 remain outside. See §13.

### 9A.6 Impact on the rest of the spec

- Tech-stack matrix (§2.6): add RAG/Knowledge Base (AR-1), AgentCore Code Interpreter (AR-2), AgentCore Browser (AR-3), voice hook (AR-7) as named components; §2.6.2a lists the Service Desk features that realise AR-17/18/19/21(parts)/22.
- Build plan (§10): AR-1/AR-2/AR-4/AR-6 land in **Phase 1–2** (agent runtime), AR-7/AR-8 in **Phase 2** (floor), AR-10/AR-11 in **Phase 2b** (plays/proposal), AR-9 in **Phase 4c** (Admin), AR-12/AR-13 in **Phase 0** (infra), **AR-17/18/19/21(parts)/22 in Phase 3b** (Service Desk).
- Acceptance criteria (§11): AR-2 reinforces criterion 4 (every number traceable, now including estimate lines); AR-6 reinforces criterion 18 (one `correlation_id` across the path); criteria 26–30 cover the service journey.
- Cross-cutting reuse: AR-7 voice hook powers technician voice notes and advisor walk-around dictation; AR-2 Code Interpreter computes estimate totals and repair-vs-replace; AR-1 `KnowledgeBase` holds repair procedures / TSBs for the sandbox where Quick Index is not yet provisioned; AR-11 proposal shape is reused for the customer-facing estimate PDF.

---

## 10. Build Plan (execution order)

Each phase ends with a deployable increment. Checkboxes are the execution checklist.

### Phase 0 – Foundations (1 week)
- [x] Create `docs/specs/` (this file), ADR for connector pattern
- [ ] `agents/common/` package: `Connector` base, sandbox/live switch, `JourneyClient`, `Tracer`, `market.py` (formatters + TCO), `outreach.py` (`OutreachProvider` with `BasicOutreachProvider`; Connect provider stubbed), `autonomy.py` (`autonomy_level(action_id, scope)` + action registry decorator), `recommendations.py` (write shown/accepted/converted)
- [ ] Re-baseline every KPI marked *estimate* in §1.3 with the pilot dealer's own DMS / CRM data; record `source` per row
- [ ] New `infra/bootstrap.py` (Retail Ops tables incl. `OEM_Markets`/`OEM_StateRules`, roles, Streams, lake bucket, SES/SNS) without script patching; `--market`, `--enable-connect` flags
- [ ] Tailwind token mapping + `components/app/` primitives (Card, Badge, Panel, VehicleItem, ProbabilityBar, MiniStat, LogItem); `MarketContext` with `en-US` formatters; ESLint no-literal-units rule
- [x] Open-data snapshots into `sample-data/open/{us,de,eu}/` with auto-generated `SOURCES.md` + `manifest.json` (URL, license, retrieval UTC, sha256, rows) via `scripts/seed/fetch_open_data.py` (14 sources: EPA fueleconomy.gov, AFDC EV registrations / fleet by state / charging ports, EIA gasoline weekly + EPM electricity by state, IRS 30D eligible list, Census places; KBA FZ 10, EEA CO2 monitoring (DE + EU aggregates via Discodata SQL), Bundesnetzagentur Ladesäulenregister, Eurostat household electricity, EC Weekly Oil Bulletin). Curated rule sets in `sample-data/open/curated/`: US + DE incentives with valid_from/to and status (30D expired 2025-09-30; US car-loan interest deduction 2025-2028; DE E-Auto-Förderung 2026), US state tax rules (51), DE tax rules (VAT, Kfz-Steuer bands, BEV rules, 16 Bundesländer), DE EV range anchors, and `lineup.json` (Meridian Motors / Nordwerk Automobile 7-model lineups with real competitor anchors). NHTSA vPIC dropped (network-blocked; not needed for seed). ACEA replaced by KBA + EEA. ~1 MB committed; raw ~67 MB git-ignored.
- [x] Synthetic data plan (`docs/specs/retail-ops-synthetic-data-plan.md`): golden storylines J1–J10, District 7 / Bezirk Süd-West, marketing storyline; entity inventory with volumes for the retail tables + lake; conversational corpora; capability → data matrix; generator layout; validation invariants
- [x] Synthetic data plan **Process 2 + §4.10 extension** (2026-09-22): service storylines S1–S8, `DEALER_Vehicles`, the 12 service tables, `PLAT_ConfigAutonomy` / `DEALER_Recommendations` seed, `service/` + `recommendations/` lake prefixes, inspection templates, DTC/labor/parts reference volumes, technician roster, technician/advisor/approval corpora, agentic validation invariants
- [ ] `scripts/seed/generate/` per the plan: anchors loader, market formatter, storylines, entity generators, corpora generators, exporters (DynamoDB, S3 lake, Quick Index, Personalize, search), `validate.py`; CLI `generate_synthetic.py --market us|de|all --out local|aws`
- [ ] Seed pack `us` (**Meridian Motors**): `OEM_Markets` row, 51-state `OEM_StateRules`, 7-model catalog from EPA anchors + `lineup.json`, 11 dealers in District 7 (Census places), ~600 customers, signals, leads, journeys J1–J10 + bulk, commerce, campaigns, `rdr/`/`marketintel/`/`csi/` lake, OEM field data, platform data, corpora
- [ ] Seed pack `de` (**Nordwerk Automobile**): same storylines localized natively in German; 16-Bundesland rules, EEA/KBA anchors, E-Auto-Förderung + Kfz-Steuer rules, leasing/balloon products
- [ ] Transcript replay mode for the live-floor demo (text → `/utterance`; optional Polly audio)
- [ ] Market-switch test harness: same storyline ids rendered under `us` and `de` (acceptance criterion 10)

### Phase 0b – System spine (1–2 weeks, parallel with Phase 1)
- [ ] Identity fabric: `app-staff` + `app-customers` pools, groups, `PLAT_OrgHierarchy`, `PLAT_Users`, Lambda authorizer with scope claims
- [ ] Dual staff sign-in: Hosted UI domain + OAuth app-client settings in bootstrap; `CorpDirectory` OIDC IdP via `--federate` (client id/secret from Secrets Manager per internal Federate registration); pre-token-generation Lambda mapping federated users → groups from `PLAT_Users`; port `initiateSsoLogin` / `handleOAuthCallback` from `ui/src/lib/cognito.ts` into the shell auth module; login page with "Sign in with the enterprise IdP" and native form; callback/logout URLs registered for all five CloudFront apps
- [ ] Customer passwordless: Cognito custom auth (define/create/verify challenge Lambdas) for email OTP via SES; magic-link issuer for nurture messages
- [ ] EventBridge bus + Schema Registry + Streams bridge + event catalog v1; correlation-id propagation in shell/API/agents
- [ ] **Unified Shell** package: bar, app switcher, scope pill, ⌘K search (stub), Tasks + Notifications (stub), user menu; neutral palette over dark/light apps
- [ ] Resource URL contract (`resourceUrl()` TS + Python, `/nav/resolve`), context envelope, lint rule; `DEALER_Handoffs`
- [ ] Notification Hub + Tasks (`DEALER_Notifications`, `DEALER_Tasks` extended, `/tasks`, `/notifications`, rules config)
- [ ] `PLAT_EntityMap` + connector boundary translation; Document service (`CUST_Documents`, `app-docs` bucket, viewer)
- [x] Teardown: `deploy/destroy.sh` with `--only` stage selection and the `--confirm --account` guard (AR-12)
- [ ] Infra add-ons: agent resource policies for browser invocation (AR-13); AgentCore Observability X-Ray/CloudWatch span helper reused by all agents (AR-6)

### Phase 1 – Journey spine (1–2 weeks)
- [ ] `DEALER_Journeys` + `DEALER_JourneyEvents` + `journeys` Lambda; ownership + `<JourneyActivityFeed>`
- [ ] A2 supervisor with state machine, `validate_handoff`, tracing; AgentCore Memory store for customer memory; no nurture yet
- [ ] Journeys Kanban screen; AI Log bottom bar wired to traces

### Phase 2 – Sales floor (2 weeks)
- [ ] A3 floor path on AgentCore (port objection handler + incentives + cross-sell; add `structure_deal` sandbox); `get_lifecycle_context`, `get_recent_activity`, `generate_contextual_suggestions` with the lifecycle matrix
- [ ] A1 Discovery with `infer_lifestyle_profile`, `recommend_trim_and_packages`, `price_build`, VIN-level `search_inventory`, rules-based ranker + TCO comparison
- [ ] Advisor screen per §5.3 (3-column reference layout) with polling, Present/Use-This events, contextual-suggestions strip; reuse the Live Session layout + timer (AR-8)
- [ ] Live transcript → utterance endpoint; shared `useVoiceAssistant` hook (Transcribe Streaming + Polly) with transcript-replay demo mode (AR-7)
- [ ] `/shop` customer Discovery surface (lifestyle intake, shortlist, save build, book test drive via `DEALER_Appointments`)
- [ ] Agent runtime add-ons: `KnowledgeBase` interface (`local_chroma` dev) for A1/A3 grounding (AR-1); AgentCore Code Interpreter for A1 TCO and A4 equity/payment math with inputs+code+result returned (AR-2); AgentCore Memory via `memory_tools` pattern (AR-4)

### Phase 2b – Nurture (1–2 weeks, can overlap Phase 3)
- [ ] `DEALER_NurturePlans`, `CUST_Messages`, `CUST_Consent`; EventBridge `app-nurture-tick`
- [ ] A2 nurture tools: `plan_nurture`, `compose_message`, `answer_customer_question`, `nudge`, `detect_intent_shift`, `handoff_to_human`; nurture policy guardrails
- [ ] `/shop/chat` with signed-link resume; `/journeys/{id}/chat` endpoint; consent UI
- [ ] Nurture visibility in Journeys screen (next touch, last message, pause/resume)
- [ ] **Plays** as A2 orchestration templates (AR-10): `DEALER_Plays`/`DEALER_PlayRuns`, 6 carried templates, "Run Play" action on Journeys + Advisor, **and trigger-able via `run_play` on events** (§4.10.2)
- [ ] **Agentic operating model (§4.10)**: `PLAT_ConfigAutonomy` + `autonomy_level()` helper in `agents/common/`, action registry (`/agents/actions`), `DEALER_Recommendations` + shell write hooks + conversion subscriber, Admin › Autonomy editor; A2 `select_next_best_action`, `publish_shift_list`, `propose_policy_change`; A1 `rescore_profiles`; A3 `precompute_suggestions`, `propose_matrix_changes`
- [ ] Proposal / Sales Package generation → shareable PDF to My Garage via Document service (AR-11)

### Phase 2c – Dealer role screens (2 weeks, overlaps Phase 3)
- [ ] Assistant › **Lead Desk** (`DEALER_LeadSLA`, queue, assign → handoff) and **Service Lane** (DMS ROs + A3/A4 flags, warm handoff)
- [ ] **Store Cockpit** read models (event subscribers) + 8 tabs
- [ ] **Delivery Board** (`DEALER_Deliveries`, kanban, delivered → OWN + survey)
- [ ] **My Garage** customer portal (journey tracker, builds, appointments, consent) — finance/trade/documents/ownership sections land with Phase 3
- [ ] Mobile layouts for Lead Desk, Tasks, Advisor quick-look, Customer Web

### Phase 3 – Trade-in & finance (1–2 weeks)
- [ ] A4 with sandbox valuation connector, equity calc, prospecting port
- [ ] `finance` Lambda + F&I sandbox connector (prequalify, offers, lender decision); `DEALER_FIProducts`
- [ ] **F&I Desk** (queue, worksheet, menu presentation incl. customer-facing mode, compliance panel, push to DMS); **credit-compliance gate**: `validate_handoff(AGREE)` blocks without recorded OFAC check, Red Flags identity verification, and adverse-action notice status from the F&I connector (added 2026-09-22; was a panel only)
- [ ] A4 proactive: `monitor_equity` nightly + event-driven, `recommend_acquisition` → Inventory & Trade Desk (§3.4, §4.10.2)
- [ ] **Inventory & Trade Desk** (`DEALER_TradeDispositions`, demand score from A1 shortlists)
- [ ] Trade-In screen; Finance and Trade-In cards in Advisor center panel; My Garage finance/trade/documents sections

### Phase 3b – Service Desk / Process 2 (3 weeks, starts after Phase 1, overlaps Phases 2c–3)
- [ ] `agents/common/`: `ServiceJourneyClient` (state machine §2.1.1, `validate_service_handoff`), service event types on the bus (§4.6), `app-service-followup-tick` scheduler
- [ ] Sandbox connectors: `dms` RO extension, `scheduling`, `inspection`, `dtc` (seeded from prior `dtc-database.json`), `labor`, `parts`, `warranty`; `telematics` stub behind `--enable-telematics`
- [ ] 12 service tables (§4.2) + `DEALER_Appointments`/`DEALER_Handoffs`/`DEALER_Customers` field extensions; `service/` lake prefix; Fixed Ops Quick dashboard
- [ ] **A7 Service Operations Agent** on AgentCore: booking + `predict_next_service`; `open_repair_order`; `record_inspection` + `prioritize_findings`; `lookup_dtc` / `get_repair_procedure` / `get_labor_time` / `lookup_parts` / `check_coverage`; `build_estimate` in Code Interpreter (AR-2) with audit; `present_estimate` / `explain_finding` / `record_approval`; `track_parts` / `update_line_status` / `notify_ready`; `schedule_declined_followup` → A2 service templates; `handoff_to_sales` → A4 equity → A2 purchase journey; A7 Bedrock Guardrails
- [ ] Seed: `DEALER_InspectionTemplates` v1 from prior `vsa.json` + EV template; labor ops + parts for the 7-model lineup per market; service storylines S1–S8 per the synthetic-data plan §2.4 (maintenance visit with upsell, red-item declined then converted, recall proactive booking, parts back-order + promise re-forecast, repair-vs-replace repurchase/upgrade handoff, EV HV-battery inspection, express lane promise kept, internal recon RO from a trade); 3 months of RO history for District 7 / Bezirk Süd-West owners
- [ ] Service Desk screens §5.20.1–7: Board, Book & Check-in (tablet, voice via AR-7, photo capture), Inspect & Diagnose (technician tablet), Estimate & Approval with customer-facing mode, RO view, Parts desk + Service History, Service Cockpit read models; Cognito groups `dealer_technician`, `dealer_parts`, `dealer_service_manager`
- [ ] My Garage › Service (book, tracker, estimate approval via magic link, documents, declined work, history); Store Cockpit › Service tab; Assistant › Service Lane re-pointed to A7 service journeys
- [ ] `/service/*` and `/garage/service/*` routes (§6); resource URLs for service journey / RO / estimate; Notification Hub rules for estimate presented / parts received / vehicle ready
- [ ] Market check: labor and parts tax rules per `market_config` (US: parts taxable, labor varies by state; DE: 19 % VAT on both), km/mi in mileage and service intervals, EUR/USD formatting; run storylines S1–S8 under `us` and `de`
- [ ] `DEALER_Vehicles` VIN master + backfill from DMS; `previous_journey_id` / `superseded_by` on `DEALER_Journeys` (OWN as resting state, §2.1)
- [ ] **A7 morning work**: 05:00 shop-load scheduler; `dispatch_ro` (technician roster + skills in dealer config), `stage_parts` (confidence threshold + daily cap), `reforecast_promise`, `auto_present_estimate`, `prepare_ro_brief`; RO line types customer_pay / warranty / internal / sublet; loaner and mobile capacity in `book_service`; storylines S4 (back-order + re-forecast), S7 (express, promise kept), S8 (recon RO from a trade) exercise these

### Phase 4 – Personalization & analytics (2 weeks)
- [ ] Firehose → S3 → Glue/Athena; Quick datasets + 5 dashboards; `rdr/`, `marketintel/`, `leads/` synthetic lake data
- [ ] Personalize dataset group, import, train ranking + similar-items; A1 switches ranker when live
- [ ] A5 Campaign ROI Optimizer + Campaigns screen with embedded Quick

### Phase 4b – Quick-hosted agents (2 weeks)
- [ ] `deploy.sh --quick`: Quick Index collections (CRM notes, DMS summaries, incentive bulletins, product guides, C&I docs, market reports), nightly S3 export with PII masking
- [ ] Quick Index **retrieval target** (Gateway or Quick Index API) so the A3 and A7 AgentCore agents can ground answers in indexed CRM notes / bulletins / guides / procedures (A3 is not a Quick-hosted agent, §13)
- [ ] A6 OEM Sales Administrator core: `OEM_Accounts`, `OEM_AccountPlans`, `OEM_CIPrograms`, `OEM_Alerts`, `OEM_ContactReports`, `OEM_Compliance`, `OEM_GuardrailEvents`, `OEM_Comms` seed (one district, 10–12 dealers, 3 months RDR, PY weekend, competitor offers, EV readiness, compliance); connectors `rdr`, `ci`, `marketintel` (sandbox); Quick Research allow-list
- [ ] A6 guardrail policy GR-001…GR-008 (Bedrock Guardrails denied topics + rules Lambda), event logging, safe-alternative responses
- [ ] A6 Quick Flows: Account Plan, Opportunity Brief, Win Strategy, Action Plan, Contact Report (with dictation), Meeting Package (PPTX), District Pulse / Roll-up, Sales Ops Digest
- [ ] A6 analytic tools: `compute_composite`, `analyze_performance_gaps`, `detect_alerts`, `predict_weekend`, `get_competitive_offers`, `get_ev_readiness`, `get_compliance`, `simulate_scenario`
- [ ] OEM Field Sales Workspace (§5.9) light-theme shell + 15 views per reference; persistent AI chat panel with voice; *District Sales vs Target* dashboard embed; **Region View** + scope switcher; mobile layouts for Today/Alerts/Contact Reports/Weekend
- [ ] Identity Center SAML federation from Cognito; Quick JIT provisioning into role groups; Quick embed ↔ shell `postMessage` navigation using the resource URL contract
- [ ] Approval workflow (Step Functions, `DEALER_Approvals`) for action plans, C&I asks, deal exceptions, budget shifts
- [ ] A2 routing to A6 for account-linked journeys

### Phase 4c – Marketing Studio & Admin (1–2 weeks)
- [ ] **Marketing Studio** app shell: Campaigns (A5), Audiences (Personalize segments export), **Journey Ops** (nurture policy, templates with preview/A-B, consent rules, customer surface content, nurture analytics) → `PLAT_ConfigNurture`
- [ ] **Admin Console**: users & roles, org hierarchy, market & brand, connectors (health, credentials), feature flags, guardrails & suggestion-matrix editors with test harness, audit export, data-subject requests
- [ ] Global search index (OpenSearch Serverless fed from Streams) and ⌘K palette fully wired
- [ ] Data classification enforcement: export jobs drop/hash C3, refuse C4; trace redaction tests
- [ ] Admin › **Agents** panel (AR-9, now required): per-agent health, recent traces, tool error rate, latency p50/p95, guardrail blocks over §8 metrics, **plus acceptance / conversion per action from `DEALER_Recommendations`** and the current autonomy level per action
- [ ] A5 `run_nightly_attribution` → auto-drafted budget-shift approvals, `refresh_audiences`; A6 `run_district_cycle` (weekly) + `track_milestones` (§4.10.2)
- [ ] AgentCore Browser tools for A4 market comps / A6 research live-upgrade path (AR-3, optional); Bedrock Knowledge Bases production `KnowledgeBase` implementation (AR-1)

### Phase 5 – Outreach & hardening (1 week)
- [ ] A2 `schedule_outreach` via `BasicOutreachProvider` (SES email, SNS SMS, dealer task); stalled-journey SLA timer
- [ ] Bedrock Guardrails, IAM scoping, alarms, PII redaction in lake
- [ ] `demo` environment with frozen US seed; demo script in `docs/DEMO.md`

### Phase 6 – Live connectors (per customer engagement)
- [ ] Salesforce, CDK (F&I + DMS), Cox valuation, Adobe connectors behind the same interface; credential onboarding runbook

### Phase 7 – Optional: Amazon Connect (when a customer requires voice/chat)
- [ ] `ConnectOutreachProvider`; `deploy.sh --connect` provisions flows `RetailOps-Outbound-Callback`, `RetailOps-Delivery-Update`, `RetailOps-TestDrive-Confirm`
- [ ] Contact Lens transcript write-back to `DEALER_JourneyEvents`
- [ ] Flip `ENABLE_CONNECT=true`; verify no agent/UI code change was needed

---

## 11. Acceptance Criteria

1. A customer can move DISCOVER → DELIVER entirely through the console with every stage transition recorded in `DEALER_JourneyEvents` including actor and evidence.
2. A2 blocks any stage transition with missing required context and surfaces the gap in the Customer 360 panel.
3. Advisor screen renders the three panels from live agent output for a seeded customer in < 6 s end-to-end (p95), objection response in < 2 s after utterance.
4. Every price, rate, valuation, and success rate shown in the UI is traceable to a tool call in the trace log (no fabricated numbers).
5. Trade-in valuation and finance offer both appear on the same Sales Package for a journey.
6. Campaigns screen shows ROI per campaign for seeded data and A5 produces at least one budget-shift recommendation with expected lift.
7. `deploy/bootstrap.sh --yes` (or the unattended `deploy/guidance-deploy.sh`) succeeds on a fresh account with no manual file edits and no Docker, and `deploy/verify.sh` then ends with `VERIFY: ALL PASS`.
8. All IAM policies reference explicit resource ARNs; no `Resource: "*"` on DynamoDB actions.
9. With the default `us` market, every displayed figure uses USD, miles, MPG/MPGe, and state-based tax/incentives; a grep of `components/app/**` and `agents/**` for `₹`, `lakh`, `km/`, or hard-coded `$` string literals returns nothing (lint gate).
10. Changing `market_id` on a journey from `us` to `de` re-renders Advisor, `/shop`, My Garage, and F&I Desk correctly with no code change: EUR with VAT-inclusive prices, km and l/100 km or kWh/100 km, WLTP range, Kfz-Steuer and THG incentives, leasing/balloon products; the same test in reverse for `us`.
10a. **Open-data provenance:** every catalog price/spec anchor, registration proxy, and incentive amount in both seed packs carries a `source_ref` that resolves to a row in `sample-data/open/<source>/` listed in `SOURCES.md` with URL, license, and retrieval date; no real OEM trim name appears in either synthetic brand's own catalog.
10b. **Dual staff sign-in:** the same `dealer_manager` user can sign in with a Cognito-native password and, as a separate federated identity, with the enterprise IdP through the Hosted UI; both land on the same home screen with the same group claims and scope; federated session honours the 8 h expiry and native the 1 h + refresh, as in the existing implementation.
11. With `ENABLE_CONNECT=false` (default), stalled-journey outreach still executes via SES/SNS and creates a dealer task; the UI never shows Connect-only channels. Flipping the flag and running `--connect` enables voice callbacks with no agent or UI redeploy.
12. **A1:** for a seeded customer with thin signals, A1 asks ≤ 4 intake questions, produces a `LifestyleProfile`, and every shortlist reason references a profile field; the recommended trim/packages fit within stated budget and at least one exact or near-match VIN is shown with dealer and ETA.
13. **A2 nurture:** over a simulated 30-day journey the scheduler sends ≤ 2 touches/week, none in quiet hours, each citing a customer-specific fact; an inbound question about payments returns a grounded range with citations and a one-click prequal action; a question about credit approval triggers `handoff_to_human`; opt-out stops all automated messages within one cycle.
14. **A3:** for each lifecycle stage in the matrix, `generate_contextual_suggestions` returns at least one suggestion whose `why` cites a recent activity record; the A3 agent answers "what should I say to this customer next" with source citations retrieved from Quick Index; A2 `publish_shift_list` produces a prioritized call list for a seeded advisor.
15. **A6:** for a seeded dealer account, the Account Plan Quick Flow produces a document where every number links to an RDR row, market-intel record, C&I program, composite input, or research URL; an Opportunity Brief and Win Strategy generate for a seeded fleet opportunity; no incentive budget or allocation is changed automatically.
15a. **A6 workspace parity with reference:** all 15 views in §5.9 render with seeded district data; each view shows a KPI grid and at least one [AI INSIGHT] card generated (not hard-coded) from the underlying data; the Scenario sliders change the five projected-impact cards with deltas and an "estimate" label; Meeting in a Box produces a downloadable deck for every listed meeting type; a dictated Contact Report yields structured follow-ups with deadlines and a CRM activity.
15b. **A6 guardrails:** each of GR-001…GR-008 blocks a matching test prompt in the chat panel and in generated artifacts, returns a safe alternative, and writes an `OEM_GuardrailEvents` row; the Admin › AI Guardrails view reflects the block count; zero false positives on a 50-prompt allow set.
16. Role-based visibility: a `dealer_bdc` user cannot open Accounts; an `oem_sales_ops` user cannot open the live Advisor; a user opens Amazon Quick content only through their role's Quick group, and Quick content is not row-scoped (§13, 2026-10-21).
17. **Every persona has a home** (§5.2): signing in as each of the 19 staff groups (plus the customer pool) lands on the specified home screen, and each can reach every screen listed in "Also uses" and none outside it (automated role matrix test).
18. **Cross-stack continuity:** a DM signs in once, opens an alert in the Quick-embedded OEM Workspace, follows its link to the dealer's Store Cockpit in the Dealer Console, then to a customer's Journeys card, then hands off to A2 nurture. No re-authentication; scope pill stays on the dealer; the "You're here because…" strip names the alert; the whole path shares one `correlation_id` in logs.
19. **Resource URL contract:** every link emitted by agents, notifications, Quick insights, emails, and tasks in a full seeded run resolves via `/nav/resolve` to a permitted screen for the recipient; zero hard-coded screen paths (lint + runtime validation).
20. **Handoffs:** BDC → advisor → F&I → delivery on one journey produces four `DEALER_Handoffs` rows, four accepted tasks, and the activity feed shows the chain identically on Advisor, F&I Desk, Delivery Board, and (customer-safe) My Garage.
21. **Notification Hub:** a HIGH alert, a nurture reply requiring a human, and an overdue task each reach the right user in-shell within 30 s and by email/SMS per rules; marking read in one app clears it everywhere.
22. **My Garage:** a customer with a seeded journey sees stage, builds, appointment, finance status, trade offer, and shared documents; changing consent in My Garage stops A2 messages on that channel; the dealer's "view as customer" shows the same content read-only.
23. **F&I Desk / Delivery Board:** a journey moves FINANCE → AGREE → DELIVER → OWN entirely through these screens with `push_deal_to_dms`, document sharing, and `mark_delivered` events on the bus; attach-rate and PVR KPIs update in Store Cockpit without page reload logic (event-driven read model).
24. **Config without redeploy:** editing a nurture template in Journey Ops, a guardrail rule in Admin, or a suggestion-matrix row changes agent behaviour on the next invocation after publish, with version history and rollback.
25. **Data classification:** export to Quick Index and lake contains no C4 fields and only hashed C3; a trace search for a seeded customer's phone number returns nothing.

**Process 2 — Service & ownership (added 2026-09-22)**

26. **Service journey end to end:** for a seeded owner, a service visit moves BOOK → CHECK_IN → INSPECT → DIAGNOSE → APPROVE → REPAIR → READY → FOLLOW_UP entirely through the Service Desk and My Garage, with every transition in `DEALER_ServiceJourneyEvents` (actor, evidence) and the DMS RO mirrored (`ro_id` present from CHECK_IN, closed at READY). `validate_service_handoff` blocks APPROVE when any estimate line lacks a labor op, price `source_ref`, or coverage check, and surfaces the gap on the Estimate screen.
27. **Estimate integrity and approval:** every labor hour, part price, coverage decision, and total on a presented estimate traces to a tool call (labor guide, parts, warranty connector, Code Interpreter audit with inputs + code + result). The customer receives the approval link within 60 s of `present_estimate`; approving 2 of 3 lines in My Garage dispatches exactly those 2 lines, writes the third to `DEALER_DeclinedWork` with its original evidence, and updates the advisor's screen without reload. Advisor-recorded verbal decisions carry `decided_by=advisor` and a reason.
28. **Inspection and diagnosis grounding:** a submitted MPI with yellow/red items is rejected if any such item lacks evidence; `prioritize_findings` never emits a recommendation whose `finding_ref` is not an inspection item, DTC, recall, or maintenance-schedule row; `lookup_dtc` output cites a `DEALER_DTCReference` row (sandbox) and any TSB excerpt cites a Quick Index / `KnowledgeBase` document. A 50-prompt test of customer-facing explanations produces zero Guardrail blocks for scare language on the allow set and blocks all 10 prompts in the deny set.
29. **Repurchase/upgrade handoff and follow-up:** a seeded storyline where the estimate exceeds `repair_vs_replace_threshold_pct` of the A4 valuation produces an `DEALER_Handoffs` row, a purchase journey in DISCOVER with `source=service_lane` and the service context attached, and an advisor task, and appears in Assistant › Service Lane and Store Cockpit › Service within 30 s. Declined work older than the configured cadence generates an A2 *service-template* message (never a sales message) and stops when the customer books or opts out. `vehicle.delivered` on a purchase journey creates the ownership record and first-service reminder.
30. **Service parity across markets and roles** (role masking enforced server-side; a technician token calling `/service/ros/{id}` receives no customer contact fields): the same service storylines render under `us` and `de` with correct tax on labor vs parts, mileage units, and currency (no code change); a `dealer_technician` cannot see customer contact data or pricing margins and cannot open Estimate & Approval; a `dealer_parts` user can create a parts order but cannot change line status; the Service Cockpit tiles (MPI rate, close rate, cycle time, parts-wait, handoffs) update from events without page-load computation.

**Agentic operating model and correctness fixes (added 2026-09-22, §4.10)** — these test *unprompted* behaviour: nothing in the "Given" is a user action.

31. **Event-driven triggers:** for every row in §4.10.2, publishing the listed event (or advancing the clock to the schedule) with no user action causes the named agent to act within one scheduler cycle, visible as a trace with the triggering `event_id`; a seeded `inventory.changed` that puts a saved build in radius produces `discovery.match_found` and an A2 recommendation card within 60 s.
32. **Morning work:** with 12 ROs booked for tomorrow and no user action, at 05:00 local A7 has dispatched every RO to a qualified technician (EV HV lines only to certified technicians), staged or ordered parts for predicted work above the confidence threshold within the daily cap, pre-drafted estimate lines from declined work / recalls / due items, and prepared an advisor brief per RO; the Service Board shows all of it before the first login. A simulated `parts.backordered` during the day re-dispatches the affected RO and `reforecast_promise` messages the customer with a new time *before* the original promise passes.
33. **Autonomy ladder:** the same A7 `stage_parts` action set to `observe`, `recommend`, `act_with_approval`, and `act_and_notify` in `PLAT_ConfigAutonomy` respectively produces only a trace, a recommendation card, an approval task with the exact order payload, and an executed order plus notification; the level in force is recorded in the trace and the `DEALER_Recommendations` row; changing the level takes effect on the next invocation after publish with rollback. Hard rules (consent, quiet hours, GR-001…008, customer-owns-approval, no automatic spend or allocation) cannot be raised to `act_and_notify` by any configuration and the editor rejects the attempt.
34. **Outcome loop:** every recommendation rendered by any agent in a full seeded run has an `DEALER_Recommendations` row with `shown_at`; clicking "Use This," "Present," or "Do it" sets `accepted_at`; a later `appointment.booked`, `estimate.line_decided=approved`, or `vehicle.delivered` sets `converted_at` via the subscriber; Admin › Agents shows acceptance and conversion per action; A2 `select_next_best_action` demonstrably changes its choice for a `context_hash` after seeded outcomes favour a different channel; the lake `recommendations/` prefix contains no contact fields.
35. **Continuous equity and acquisition:** an `ro.opened` event carrying new mileage on a seeded VIN causes A4 to update `DEALER_Vehicles.equity_snapshot` and, when the threshold is crossed, emit `equity.crossed_positive` → A2 runs *Upgrade Pitch* at the configured autonomy level; a service-drive vehicle matching a seeded stock need produces `acquisition.recommended` on the Inventory & Trade Desk with a suggested offer range and evidence.
36. **Weekly OEM cycle and nightly attribution:** advancing to Monday 05:00 with no user action leaves the DM's workspace with refreshed composites, triaged alerts, drafted action plans for every red dealer, and a meeting package for each visit in the next 7 days, all in draft (`act_with_approval`); nightly A5 attribution creates a budget-shift approval request only when projected lift ≥ threshold and never changes spend.
37. **Journey lifecycle and VIN master:** a seeded customer in OWN receives a repurchase/upgrade handoff; a new journey is created in DISCOVER with `previous_journey_id`, the OWN journey shows `superseded_by`, the single-active constraint holds, and A5 attribution can still see both; `DEALER_Vehicles` has exactly one row per delivered or backfilled VIN with owner history, and every service, coverage, and equity record for that VIN resolves to it. Credit-compliance gate: `validate_handoff(AGREE)` blocks a seeded deal missing OFAC / Red Flags / adverse-action status and surfaces the gap in F&I Desk.

---

## 11A. System-Level Gap Analysis (2026-09-10 re-read; Process 2 notes added 2026-09-22)

The spec so far is agent-centric: seven agents (six retail plus A7), most with a screen. Re-reading it as **one system that many personas move through, built on several stacks (custom Next.js on AgentCore, Quick-hosted, customer web, optional Connect) that must feel connected**, exposes gaps in four areas. Each gap below is numbered (G-nn), states what is missing, why it matters, and where the fix lands in this document.

### 11A.1 Persona coverage gaps

The journey state machine (§2.1) has eight stages, but only some of the people who own those stages have a screen.

| Gap | Persona (missing or under-served) | Owns in the journey | What they need that the spec lacked | Fix |
|---|---|---|---|---|
| G-01 | **Customer** (prospect → buyer → owner) | Every stage from their side | Only `/shop` and `/shop/chat` existed. No way to see *their own* journey: saved builds, test-drive appointments, finance status, trade offer, deal documents, delivery schedule, ownership (service, upgrade offers). | New **My Garage** customer portal (§5.12) |
| G-02 | **F&I Manager** | FINANCE, AGREE (products, contracts) | `structure_deal` and F&I connector exist, but there is no F&I desk screen: queue of deals ready for F&I, menu presentation, product attach, lender offers, compliance disclosures. Attach-rate KPI (§1.3) has no operator. | New **F&I Desk** screen (§5.13) |
| G-03 | **Dealer Principal / GM** | Accountability for the whole store | Only `dealer_manager` sees Campaigns + Journeys. No store-level cockpit: funnel, gross, days supply, advisor leaderboard, trade capture, attach rate, alerts. The A6 DM sees the dealer better than the dealer does. | New **Store Cockpit** (§5.14) |
| G-04 | **BDC Agent** | DISCOVER → QUALIFY (inbound leads, appointments) | Covered as a role inside A3's Assistant but no first-class inbound queue: lead intake, SLA timers, appointment book, handoff-to-floor. | **Lead Desk** view inside Assistant (§5.8 extended) |
| G-05 | **Service Advisor** | OWN (service lane) → now owner of the service journey (Process 2) | Only a row in A3's lifecycle matrix. 2026-09-10 fix was a read-only Service Lane. 2026-09-22 re-read: a read view cannot own check-in, estimate, approval, or ready; the advisor needs an operating screen set. | **Service Desk** (§5.20) with A7; Service Lane stays as the sales-facing read view (§5.8 ext.) |
| G-05a | **Technician** | INSPECT, DIAGNOSE, REPAIR | No persona at all before 2026-09-22; inspections and DTC work were dropped (AR-18/19). | `dealer_technician` group; Inspect & Diagnose tablet view (§5.20.3), line status on RO view |
| G-05b | **Parts counter** | REPAIR (parts hold) | No persona; parts were procurement-only (AR-21). | `dealer_parts` group; Parts desk (§5.20.6); `parts.*` events resume A7 |
| G-05c | **Service manager** | Fixed-ops oversight | No persona; Store Cockpit had no service tab. | `dealer_service_manager` group; Service Cockpit (§5.20.7); Store Cockpit › Service tab |
| G-05d | **Owner (customer) during service** | BOOK, APPROVE, FOLLOW_UP | My Garage › Ownership showed a history summary only; no booking, no digital estimate approval. | My Garage › Service (§5.12) |
| G-06 | **Inventory / Used-Car Manager** | Trade-in disposition, VIN allocation | A4 values trades but nobody decides keep/wholesale/CPO or reconciles inventory with A1's demand signals. | **Inventory & Trade Desk** view (§5.6 extended) |
| G-07 | **Delivery Coordinator** | DELIVER | Stage exists, no screen: delivery schedule, prep checklist, document status, customer confirmation, EV charger install status. | **Delivery Board** (§5.15) |
| G-08 | **OEM Marketing Analyst** vs **OEM CX lead** | Campaigns; nurture policy | A5 serves marketing analytics; nobody owns nurture policy (cadence, templates, consent) or the customer surface content. | **Journey Ops** admin screens (§5.16) |
| G-09 | **Regional Sales Manager** | Above DM | A6 workspace is DM-scoped; regional roll-up across districts and DM coaching is implied by "District Roll-up" only. | Region scope + **Region View** in A6 workspace (§5.9 extended) |
| G-10 | **System / Tenant Administrator** | Everything | No admin: users & roles, dealer hierarchy, market/brand config, connector credentials & health, feature flags (`ENABLE_CONNECT`), audit export. | **Admin Console** (§5.17) |
| G-11 | **Fleet / Commercial Buyer** | Fleet opportunities | Exists only as an A6 *account*. Open question 6 left it unresolved. | Decision: v1 keeps fleet buyers as accounts; a fleet buyer portal is v2 (§12). |

### 11A.2 Cross-stack navigation and continuity gaps

Users cross stack boundaries constantly (e.g., DM reads an alert in Quick-embedded workspace → opens the dealer's Store Cockpit in the Next.js console → opens a specific customer's Advisor view → hands to A2 nurture). Nothing in the spec made those transitions seamless.

| Gap | Missing | Fix |
|---|---|---|
| G-12 | **Unified shell / app switcher.** Two visual shells (dark dealer console, light OEM workspace) plus Quick embeds and the customer web with no common frame, global nav, or way to switch app without losing context. | **Unified Shell** (§5.18): one top bar across all authenticated apps with app switcher, global search, notifications, tasks, user menu; per-app theme underneath. |
| G-13 | **Deep-link contract.** No canonical URLs for journey, customer, account, deal, alert, so Quick insights, emails, tasks, and agents cannot link into the right screen with context. | **Resource URL contract** (§5.18.2): `/{app}/{resource}/{id}?ctx=` with resolver Lambda; used by every agent output, notification, and Quick insight. |
| G-14 | **Single sign-on across stacks.** Cognito for console, Identity Center for Quick embedding, signed links for customers, Connect agent login (optional) were listed separately with no SSO story. | **Identity fabric** (§8.1): Cognito User Pool as IdP → Identity Center (Quick) via SAML/OIDC federation; one login, session shared by shell; customer identity is a separate Cognito pool with passwordless. |
| G-15 | **Context handoff between apps.** Moving from Quick A6 → console → Advisor should carry `dealer_id`, `customer_id`, `journey_id`, and *why you came* (alert id). No mechanism existed. | **Context envelope** in deep links + `DEALER_Handoffs` (§5.18.3) so the target screen can show "You arrived from Alert AL-123: CSI declining." |
| G-16 | **Notifications & inbox.** Alerts (A6), nurture replies (A2), handoff-to-human, tasks, approvals live in separate tables with no unified delivery to the person who must act. | **Notification Hub** (§5.18.4): `DEALER_Notifications`, in-shell bell + inbox, email/SMS/push via `OutreachProvider`, per-role rules. |
| G-17 | **Task system.** `DEALER_Tasks` existed but no task UI, ownership rules, SLA, or cross-app source linking. | **Tasks** as a first-class shell surface with source deep link, SLA, reassignment; A2/A3/A6 all create tasks through one API. |
| G-18 | **Global search.** No way to type a customer name, VIN, dealer, or account and land on the right screen in the right app. | **Global search** in shell over `DEALER_Customers`, `DEALER_Inventory`, `OEM_Accounts`, `DEALER_Journeys` (OpenSearch Serverless or DynamoDB + Quick Index). |
| G-19 | **Presence / collaboration on a journey.** Two people (BDC then floor advisor then F&I) touch one journey; no ownership, lock, or activity feed visible across screens. | Journey **ownership + activity feed** component reused on Advisor, F&I Desk, Delivery Board, My Garage (read-only). |
| G-20 | **Mobile for field and floor.** DM is in the field; advisors walk the lot. "Responsive web only" is stated but no mobile layouts for DM Today/Alerts/Contact Report dictation or advisor quick-look. | Mobile breakpoints for those views specified (§5.19); native apps stay out of scope. |

### 11A.3 System plumbing gaps

| Gap | Missing | Fix |
|---|---|---|
| G-21 | **Event backbone.** Agents write to tables; cross-app reactions (alert → notification → task → nurture pause) were implied, not designed. | **EventBridge event bus `app`** with a published event catalog (§4.6); all apps and agents publish/subscribe; Streams → bus bridge. |
| G-22 | **Canonical IDs and entity ownership.** `customer_id`, `account_id`, `dealer_id`, `deal_id` are used across CRM, DMS, DynamoDB with no master/ID-mapping rule. | **Entity registry & ID mapping** (§4.7): `PLAT_EntityMap` (system, external_id ↔ app_id), golden-record rules. |
| G-23 | **Approvals.** Action plans, incentive asks, deal exceptions, budget shifts need human approval flows; only "publish for review" existed. | **Approval workflow** (§4.8): `DEALER_Approvals`, Step Functions, surfaced in Tasks/Notifications. |
| G-24 | **Document generation & storage.** Meeting decks, account plans, deal jackets, delivery docs go to "S3" with no service, retention, or viewer. | **Document service** (§4.9): S3 + metadata table, pre-signed viewer component, retention classes. |
| G-25 | **Configuration surface for non-engineers.** Nurture templates, suggestion matrix, guardrail rules, incentive rules, brand tokens live in code/seed. | Editable via Admin Console and Journey Ops (§5.16, §5.17) backed by `PLAT_Config*` tables with versioning. |
| G-26 | **Cross-stack observability.** Agent traces exist per journey; nothing correlates a user's path across shell → console → Quick → agent. | **Correlation id** propagated in shell, API, Gateway, Quick calls; CloudWatch + AgentCore observability joined on `trace_id`. |
| G-27 | **Data residency for Quick Index.** Quick Index ingests CRM notes; export/masking was mentioned but not the classification of what may leave DynamoDB. | Data classification table (§8.2). |

### 11A.4 Stage coverage check (who has a screen at each stage)

| Stage | Customer | Dealer roles | OEM roles | Agent | Status after fixes |
|---|---|---|---|---|---|
| DISCOVER | `/shop` + My Garage | BDC Lead Desk | Marketing (Campaigns) | A1, A2 | ✔ |
| CONFIGURE | `/shop` builds in My Garage | Advisor (Discovery tab) | – | A1 | ✔ |
| QUALIFY | My Garage appointments | Advisor (live), Lead Desk | – | A3, A2 | ✔ |
| FINANCE | My Garage finance status | **F&I Desk** (new), Advisor finance card | – | A3 tools, F&I connector | ✔ (was ✘) |
| TRADE_IN | My Garage trade offer | Trade-In + **Inventory & Trade Desk** | – | A4 | ✔ (was partial) |
| AGREE | My Garage documents | Advisor deal, **F&I Desk** | – | A3 | ✔ (was ✘) |
| DELIVER | My Garage delivery | **Delivery Board** (new) | – | A2 outreach | ✔ (was ✘) |
| OWN | My Garage ownership + **My Garage › Service** | **Service Desk** (A7), **Service Lane** (sales view), Trade-In prospects | – | A7, A3, A4 | ✔ (was partial; Process 2 added 2026-09-22) |
| Service BOOK → FOLLOW_UP (Process 2) | My Garage › Service (book, tracker, approve) | Service Board, Check-in, Inspect & Diagnose, Estimate & Approval, RO view, Parts desk, Service Cockpit | OEM after-sales (read, v2) | A7 (+ A4, A2, A3) | ✔ (new) |
| Store oversight | – | **Store Cockpit** (new) | DM workspace | A6 | ✔ (was ✘) |
| Network oversight | – | – | DM + **Region View** | A6 | ✔ (was partial) |
| System | – | – | **Admin Console**, **Journey Ops** | – | ✔ (was ✘) |

---

## 12. Out of Scope (v1)

- Payments / e-contracting and e-signature (document model reserves `signature_status`).
- Multi-OEM tenancy in one deployment (one OEM per stack; org hierarchy is single-tenant).
- Mobile native apps (responsive web only; mandatory mobile layouts per §5.19).
- Real-time bidding integrations with ad platforms (A5 recommends; humans apply budgets).
- Fleet / commercial buyer portal (fleet buyers are A6 *accounts* in v1; a buyer-facing portal is v2 — closes open question 6).
- OEM after-sales / warranty analyst persona and views (network-level fixed-ops KPIs, recall completion, warranty claim analytics). `oem_aftersales` Cognito group name reserved; no screens in v1 (closes open question 7, 2026-09-22).
- Tier-1/X supplier personas (listed in the target-enterprise column but no use case in the matrix; revisit when a supplier use case is defined).
- ~~Service scheduling and repair-order workflow beyond the Service Lane read view~~ — **removed 2026-09-22**; Service & ownership is Process 2 (§1.1, §2.1.1, §3.7, §5.20). What stays out of the service scope: the DMS remains system of record for the RO and invoice (Retail Ops mirrors, never replaces it); technician time-clocking and payroll (skills and shifts are dealer config for `dispatch_ro`; HR / time-clock integration is v2); warranty *claim submission* to the OEM (coverage lookup is in, claim filing is v2); body-shop / collision estimating; loaner-fleet *management* (availability is read for `book_service`; contracts, fuel, damage, and return tracking are v2); OEM plant production scheduling (AR-16); supplier / GPO procurement (AR-21 procurement half); inter-store vehicle transfers (AR-20).

---

## 13. Decisions Log

| Date | Decision | Rationale |
|---|---|---|
| 2026-09-10 | **US is the default market from day one**, implemented as a `MarketConfig` abstraction (§2.5) rather than hard-coding. India content in the reference mockup is layout-only. | Target enterprises are US OEM/dealer networks; abstraction keeps additional markets a data change, not a code change. |
| 2026-09-10 | **Amazon Connect is optional** (`ENABLE_CONNECT=false` default) behind an `OutreachProvider` interface; SES/SNS + dealer tasks are the default outreach path. | Avoids Connect instance provisioning and telephony cost in dev/demo; customers with an existing Connect footprint can enable it without redeploying agents or UI. |
| 2026-09-10 | **Adopt the use-case matrix classification** (§2.2.1): A1/A2 are custom AgentCore apps; A3 and the new **A6 OEM Sales Administrator** are Amazon Quick-hosted (agents + Flows + Index + Research), exposed to A2 through an AgentCore Gateway tool. A2 is the only multi-agent workflow *(until A7 was added 2026-09-22)* and gains a long-running nurture role. | Matches the stated tech preference per use case, keeps document-style generation (plans, briefs, call lists) in Quick where it fits, and keeps streaming / stateful orchestration on AgentCore. |
| 2026-09-10 | A3's real-time floor path stays on AgentCore even though A3's tech preference is Quick. | Quick is not designed for sub-2 s streaming over a live transcript; the floor path calls the Quick agent for suggestion content so there is one suggestion engine. *(Superseded 2026-09-23, below.)* |
| 2026-09-23 | **A3 is one custom AgentCore agent, not split across Quick + AgentCore.** The former Quick-hosted assistant (chat + Flows) is folded into the same AgentCore agent that runs the streaming floor path; it uses **Quick Index as a retrieval tool** (`retrieve_context`, the A7 pattern) instead of being a Quick-hosted agent. Quick-hosted agents/flows now apply to **A6 only**. The *Daily BDC call list* moves to A2 `publish_shift_list`; the *upsell brief* and *follow-up draft* become A3 tools. Touches §2.2.1, §2.2.2 (R3 + result table + composition #4/#5 + anti-patterns), §2.3, §3.3, §4.3.1, §5.8, §5.2.1, §2.6, Phase 4b. | The two A3 surfaces share the same suggestions and (via A2) journey state, and one needs sub-2 s streaming, so R1+R2 outrank R3. Splitting one agent across two runtimes and two suggestion engines was the actual anti-pattern; a single agent that retrieves from Quick Index keeps Quick's index, citations, and RLS *(RLS removed 2026-10-21, below)* without a second runtime, and guarantees the floor and the chat cannot give different advice. Quick Index (not Quick agents) is the reusable piece, exactly as A7 uses it for procedures/TSBs. |
| 2026-09-23 | **A3 display name changed from "Dealer Sales Assistant" to "Dealer Sales Advisor."** Identifier `A3`, the `/dealer/assistant` route, the `assistant` Lambda, and the "Assistant" console screen (§5.8) are unchanged. | "Advisor" reads consistently with A4 (Advisor) and the advisor persona; a display-name change only, no code impact. The screen keeps the name "Assistant" because it hosts Lead Desk / Service Lane / Chat, not just A3. |
| 2026-09-10 | **Treat the package as one system of five apps** (Dealer Console, OEM Workspace, Marketing Studio, Customer Web / My Garage, Admin Console) on different stacks, joined by a Unified Shell, one identity fabric, a resource URL contract, an event bus, and a Notification/Task hub (§11A). Added 11 persona homes and 7 new screens. | Gap analysis showed 4 of 8 journey stages had no operator screen (F&I, AGREE, DELIVER, store oversight), customers could not see their own journey, and nothing connected Quick-embedded views to console screens or carried context across them. |
| 2026-09-10 | Fleet buyers remain A6 *accounts* in v1; Tier-1/X suppliers out of scope until a use case exists. | Closes open questions 6; keeps v1 focused on the OEM ↔ dealer ↔ consumer funnel. |
| 2026-09-10 | **Pilot scope.** v1 is a pilot: no scale, HA, or multi-region engineering beyond what the managed services give by default. Single-store `dealer_manager` scope (no dealer-group aggregation). OpenSearch Serverless kept for global search (simplest structured entity search). Customer passwordless = email OTP + magic links; SMS OTP added when 10DLC is approved. SES stays sandboxed with verified demo inboxes. | Removes five open questions (Q2 licensing, Q4 SES, Q8 search, Q9 SMS, Q10 group scope) and keeps Phase 0 unblocked. |
| 2026-09-10 | **Staff login supports both Cognito-native and AWS federated (the enterprise OIDC/SAML IdP)** from day one, reusing the existing implementation in `ui/src/lib/cognito.ts` (Hosted UI with `identity_provider=CorpDirectory`, code exchange, 8 h session) and the internal wiki guidance for Federate client registration. Bootstrap gains: Cognito domain, `CorpDirectory` OIDC IdP, app-client callback URLs, `NEXT_PUBLIC_COGNITO_DOMAIN` / `NEXT_PUBLIC_SSO_CLIENT_ID` in generated `.env.local`. Customer-facing OEM IdPs (Okta/Entra) remain optional later. | Existing code already proves the pattern; internal demo users sign in with the enterprise IdP, external pilot users with Cognito-native accounts. |
| 2026-09-10 | **Quick licensing assumed provided** for the pilot; all dealer and OEM roles that need embedded Quick are provisioned as Identity Center users via JIT. | Removes the licensing constraint from design; revisit for production rollout sizing. |
| 2026-09-10 | **Two seed markets: US (`us`) and Europe (`de`, Germany as representative)**, each with a **synthetic OEM brand whose lineup, specs, and prices are derived from real public market data** (see §2.5.1). Fictional names avoid trademark issues while keeping realistic MSRPs, ranges, efficiency, and segment competitors. | User direction; also exercises the market abstraction (currency, units, tax, incentives) with two real configurations instead of one. |
| 2026-09-10 | **Market intelligence from open data only** (§2.5.1): US registrations/EV counts from DOE AFDC and state DMV open portals, specs from EPA fueleconomy.gov and NHTSA vPIC; EU registrations from KBA (Germany) and ACEA, per-vehicle CO2/WLTP records from the EEA monitoring dataset, prices from public configurators. S&P Global Mobility / R.L. Polk connectors stay as `LiveConnector` stubs for customers who license them. | No partner data licensing needed for the pilot; the `marketintel` connector interface is unchanged. |
| 2026-09-10 | **Incentive modelling reflects verified 2026 policy**: US federal 30D / 25E credits are seeded as *expired* (vehicles acquired after 2025-09-30, per IRS / P.L. 119-21); the *car-loan interest deduction* (up to $10k/yr, US final assembly, 2025-2028, final regs 2026-09-08) is the active federal lever surfaced in F&I Desk and `price_build`. Germany: Umweltbonus expired 2023-12-17; the socially graduated *E-Auto-Förderung 2026* (up to EUR 6,000, applications since 2026-05-19, retroactive to 2026-01-01) is active; Kfz-Steuer exemption for BEVs registered 2026-2030 is carried as *announced* with a conservative 50%-rate fallback in TCO. All incentive records carry `status`, `valid_from/to`, `source_urls`, `verify`. | Demos must not show a $7,500 credit that no longer exists; time-bounded records let A3/A1 explain expired vs. active incentives and let Journey Ops update amounts without code changes. |
| 2026-09-10 | **A6 adopts `DM_Assistant_Demo.html` as its reference**: District Manager persona, 15-view light-theme workspace with persistent AI chat, [AI INSIGHT] pattern on every view, and the GR-001…GR-008 guardrail rule set carried over as enforced policy. OEM brand replaced by configurable tokens. | The demo already encodes field-sales workflows (contact reports, action plans, alerts, weekend tracker, conquest, EV readiness, compliance, scenario planning, meeting in a box, comms) that "account plans / briefs / win strategies" depend on; reusing its IA avoids re-deriving the DM job-to-be-done. |
| 2026-09-10 | **Promote existing-codebase capabilities to add-on requirements (§9A, AR-1…AR-22).** Adopt the proven AgentCore primitives (RAG/ChromaDB → Bedrock KB, Code Interpreter, Browser, Memory, Gateway/Agent-OS client, X-Ray observability), the voice-assistant pipeline and Live Session screen, the Plays engine (reframed as A2 orchestration templates), proposal generation, teardown tooling, and agent resource policies. Drop the service/parts/manufacturing app (production, work-orders, diagnostics, inspections, GPO/suppliers); defer transfers and full service-history. *(Service half superseded 2026-09-22, below.)* | A from-scratch build should carry the working, non-obvious pieces deliberately and draw an explicit boundary around the non-retail product rather than silently re-deriving or reintroducing it. |
| 2026-09-22 | **Add Service & ownership as Process 2, a second main requirement** (§1.1). New agent **A7 Service Operations** (§3.7) owns a second state machine, the service journey BOOK → CHECK_IN → INSPECT → DIAGNOSE → APPROVE → REPAIR → READY → FOLLOW_UP (§2.1.1), with its own tables (12), connectors (scheduling, inspection, dtc, labor, parts, warranty, telematics-optional), Service Desk screen group (§5.20), My Garage › Service, four new personas (technician, parts counter, service manager, owner-in-service), routes (§6), Phase 3b, and acceptance criteria 26–30. | Fixed ops is ~half of dealer gross and the retention engine that produces the next purchase journey; the retail funnel's OWN stage had no operating owner. User direction 2026-09-22. |
| 2026-09-22 | **Two supervisors, not one.** A7 supervises the service journey and calls A4 (equity), A3 (talk tracks / upsell brief), and A2 (nurture, purchase-journey creation) as tools or via events; A2 supervises the purchase journey and never drives A7. The two meet through events (`service.handoff_to_sales`, `service.equity_flag`, `vehicle.delivered`) and the shared customer / VIN record. | The service journey has a different owner (service advisor, not sales), a different cadence (days in REPAIR, years of visits per VIN), and a different system of record (DMS RO). Folding it under A2 would make the purchase supervisor responsible for repair-order state it has no business owning. |
| 2026-09-22 | **Re-disposition §9A.5.** AR-17 work orders → Adopt (reframed as RO mirror + service journey; DMS stays system of record), AR-18 DTC → Adopt, AR-19 inspections → Adopt, AR-22 service history → Adopt, AR-21 split (parts-on-RO Adopt; GPO / supplier procurement Drop). AR-16 production scheduling and AR-20 inter-store vehicle transfers unchanged (Drop / Deferred). | With Process 2 defined, the "needs its own use case and persona" condition from 2026-09-10 is met for the dealer-service capabilities and still not met for manufacturing or procurement. |
| 2026-09-22 | **Service Desk reuses the two existing reference UIs** (§5.20): `sales-advisor-v2.html` supplies the operating-screen layout and components (three-column panel, `rec-card`, `incentive-row`, `objection-alert`, `compare-table`, `btn-present`, AI Log); `DM_Assistant_Demo.html` supplies the Board/Cockpit pattern (`kpi-card` grid, `ai-insight`, alert rows, filters, sliders, persistent chat with dictation). Prior-codebase service screens contribute information architecture only. Customer estimate approval is the same components in the light OEM theme. | Advisors and service advisors work in one console; a third visual language would cost design time and make service feel bolted on. The estimate-approval flow is structurally the F&I menu presentation, and the Cockpit is structurally the DM performance view, so both already have a proven pattern. |
| 2026-09-22 | **Service is not a feature flag.** A7, its tables, routes, and seed pack always deploy; only the `telematics` connector is behind `--enable-telematics`. | One product with two processes; a retail-only deployment would leave OWN unowned again. |
| 2026-09-22 | **Correctness fixes from the v1.1 review.** (a) OWN is a resting state; repeat, upgrade, and service-lane journeys create a new `DEALER_Journeys` row with `previous_journey_id` / `superseded_by` (§2.1). (b) `DEALER_Vehicles` VIN master added; `vehicles_owned[]` becomes a projection (§4.2). (c) Technician masking is server-side on the RO endpoint, not a UI hide (§5.20.5, criterion 30). (d) Credit-compliance (OFAC, Red Flags, adverse action) is a `validate_handoff(AGREE)` gate, not a panel (§10 Phase 3). (e) Prepaid maintenance / VSC sold in F&I feed `check_coverage`; RO line types customer_pay / warranty / internal / sublet; loaner and mobile capacity in `book_service` (§3.7). | The single-active-journey rule made the service handoff in §2.1.1 unrepresentable; nothing owned the vehicle after delivery; two compliance behaviours were UI-only. |
| 2026-09-22 | **KPI baselines corrected and sourced** (§1.2, §1.3). F&I products per deal 1.57 → target 2.0 (StoneEagle Q2 2025); in-warranty service retention 54% (Cox 2025, down from 72% in 2023) and dealer share of visits ~29%; "OEMs lose 75% of online buyers" replaced with the sourced Cox demand figure and ~10% internet-lead close; "online-to-showroom conversion" replaced by the industry four-stage lead funnel; recommended-work close split into *presentation rate* (~30% → ≥90%) and *approval-when-presented* (~50% → ≥60%). Every KPI row has an owner agent and a source; rows without a public benchmark are marked *estimate* and re-baselined in Phase 0. | The old baselines understated F&I, overstated in-warranty retention, used one unsourced headline claim and one non-standard metric, and hid the fact that the biggest service loss is findings never presented, which is the lever A7 controls. |
| 2026-09-22 | **Agentic operating model (§4.10).** Three platform mechanisms: `PLAT_ConfigAutonomy` (observe / recommend / act_with_approval / act_and_notify per action, per dealer, with hard rules excluded), an explicit event-subscription table per agent with schedules (05:00 shop-load, weekly district cycle, nightly attribution), and `DEALER_Recommendations` generalising the outcome loop to every agent. Each agent gains proactive tools: A1 `rescore_profiles`; A2 `select_next_best_action`, `run_play` on triggers, `publish_shift_list`, `propose_policy_change`; A3 `precompute_suggestions`, `propose_matrix_changes`; A4 `monitor_equity`, `recommend_acquisition`; A5 `run_nightly_attribution`, `refresh_audiences`; A6 `run_district_cycle`, `track_milestones`; A7 `dispatch_ro`, `stage_parts`, `reforecast_promise`, `auto_present_estimate`, `prepare_ro_brief`. Acceptance criteria 31–37 test unprompted behaviour. AR-9 Agents panel becomes required. | The review found only A2 was goal-driven; the rest waited to be asked. Dealers pay for systems that do the morning work before anyone logs in (Numa 2026). The autonomy ladder lets a store adopt that gradually and keeps spend, allocation, consent, and customer approval as human decisions by construction. |
| 2026-09-22 | **Delivery-model selection is a requirement** (§2.2.2). Seven selection rules (R1–R7) decide per use case between custom AgentCore agent, AgentCore real-time path, Amazon Quick agent/Flows/Index/Research, Quick/QuickSight analytics, Next.js read models, Customer Web, and `OutreachProvider`; seven composition rules make the result one system (one sign-in, one shell, one bus, one agent bridge, one suggestion engine per domain, one trace, config not stack for market and autonomy). Explicit anti-patterns listed. | Personas differ in how they work and are reached; the classification table in §2.2.1 recorded *what* each agent runs on but not *why* or how the pieces compose, so a single-stack proposal could have claimed compliance. |
| 2026-09-23 | **Package renamed to Intelligent Sales & Service Orchestration** (abbreviated ISSO in documents; the abbreviation is not a brand and is not used in customer-facing material) from Intelligent Retail & Purchase Orchestration (IRPO). Prose, titles, file names (`intelligent-sales-service-orchestration.md`, `isso-architecture.md`, `isso-synthetic-data-plan.md`, `ISSO_Executive_Overview.pptx`), the deck, and the skills package are renamed by `scripts/rename_product.py`. The technical namespace was initially kept as `IRPO_`, then (2026-09-25, see the row below) **retired and re-prefixed by data owner**: DynamoDB tables `OEM_*` / `DEALER_*` / `CUST_*` / `PLAT_*`, `app-*` bus / bucket / scheduler / pool names, `/app` paths, and tool references in `TOOLS.md`. | The old name described one of two processes. The prose rename was done first to stop the two names diverging; the namespace was later re-prefixed by data owner so each table name states who governs its data. |
| 2026-10-19 | **Technical stem `isso` retired; `retail-ops` everywhere.** AWS resources `retail-ops-dev-…` (`retail_ops_…` where only underscores are allowed: Glue database, AgentCore runtime and memory), settings `RETAIL_OPS_…`, Quick ids and groups `retail-ops-…`, OAuth scope `retail-ops-oem/read`, header `X-Retail-Ops-Token`, companion specs `retail-ops-*.md`. The main spec file and the repository keep their names. An existing `isso-dev-…` stack is removed with `deploy/destroy.sh --stem isso` and recreated. | One stem for the product and every technical name; resource names that AWS cannot rename are recreated rather than migrated, because the dev data is synthetic and reseedable. |
| 2026-10-19 | **Product name: Automotive Retail Operations powered by Agentic AI.** The product-level name for titles, the console and customer-facing material. *Intelligent Sales & Service Orchestration* stays the name of the orchestration layer this specification defines, so requirements, section numbers, tool references and skill citations are unchanged. Retail operations translate into the two processes: vehicle sales → Process 1, service and ownership → Process 2. | A product name buyers recognise (retail operations) without renaming the specification, the code, or the technical namespace. Repository, spec file, `retail-ops` resource names and table prefixes stay as they are. |
| 2026-09-23 | **Publish skills and the tools reference externally** as `automotive-agent-skills/`, following awslabs/hcls-agent-skills conventions. Tools (§3 tables, 121 across A1–A7) are extracted mechanically into `TOOLS.md`; skills are `SKILL.md` reasoning modules that encode the procedures the spec describes in prose (estimate integrity, inspection triage, incentive eligibility, trade equity, OEM guardrails, delivery-model selection), each with dated, cited thresholds and ≥ 8 common mistakes. `create_journey` added to A2's tool table because A7 referenced it without a definition. | The spec's methodology is reusable by any agent on any platform; publishing it as skills makes that reuse portable and versioned, and the tools reference gives builders the contract without reading 2,000 lines of spec. |
| 2026-09-22 | **Open questions 5–11 closed by adopting their defaults** (§14). Sandbox DTC / labor / parts are synthetic with `source_ref` and live stubs; `repair_vs_replace_threshold_pct=50` tenant default; OEM after-sales persona is v2; **autonomy starts at `recommend` for every action for 14 days, then the §4.10.1 pilot defaults apply** unless the dealer set levels; technician skills / certifications / shifts live in `PLAT_Users` via Admin (HV certification is a hard gate in `dispatch_ro`); `stage_parts` orders only at zero on-hand with confidence ≥ 0.8 under a trailing-30-day daily cap; `DEALER_Recommendations` is C2 with 2-year TTL. | None of these blocked the build, and each had a defensible default. The 14-day observation fortnight gives a dealer principal evidence (acceptance / conversion per action in Admin › Agents) before autonomy rises, which is the adoption pattern the review recommended. |
| 2026-10-20 | **One AgentCore runtime per custom agent** (ADR 0009). A1, A2, A3, A4, A7 and A5's recommendation step each run in their own runtime, named `retail_ops_<env>_<code>_<slug>` (for example `retail_ops_dev_a3_sales`); A6 stays in Amazon Quick. Agent-to-agent calls (A2 → A1/A3/A4 for the Sales Package) are A2A-shaped requests carried by `InvokeAgentRuntime`; the A2A wire protocol is a later per-runtime option. The two supervisors still meet only through events. A3 keeps Strands reasoning; the other agents start as deterministic tool agents and are promoted one at a time on eval evidence. All six runtimes share one execution role (the union of the scopes the moved code uses today, including SES/SNS), by user decision. | The user asked for each agent to be independent. Separate runtimes give each agent its own deployment, version, logs and failure domain without changing what users see; the shared role keeps today's access model, and the in-process fallback keeps local development and offline CI free of AWS. |
| 2026-10-21 | **Quick RLS removed (user decision).** Amazon Quick content is not row-scoped: the deployment no longer creates row-level security rule sets, RLS datasets or `lake_rls_*` tables, and `deploy_quick_rls.py` is deleted. Access to Quick dashboards, datasets and other shared content is controlled by Quick group membership only (the five sharing groups, now created by `deploy_quick_groups.py`); anyone with access sees all rows. The per-user embed still registers each console user as a Quick reader in their role's group. | The user decided the solution does not need RLS. Trade-off: district and dealer scoping is no longer enforced inside Quick, so a user who can open a Quick dashboard or dataset sees every district and dealer in it. RBAC and scope checks in the API, the AgentCore Gateway boundary and OpenSearch filters are unchanged. |
| 2026-10-21 | **SSO allowlist grants the Administrator role (user decision).** A new role `platform_admin` ("Administrator") opens every staff module (Dealer Console, OEM Workspace, Marketing Studio, Admin Console; not My Garage or `/shop`), tenant-wide. Only the the enterprise IdP aliases listed in `RETAIL_OPS_SSO_ADMINS` get it, written to `PLAT_Users` by the `sso-admins` deploy stage; any other SSO sign-in is refused as before, and username/password sign-in and the existing personas are unchanged (§5.2, §8.1). It reads every module and does everything a tenant admin may do; persona actions (OEM author and dealer-floor writes) go through "View as persona". It is not a Cognito group, cannot be assigned in Admin › Users, cannot be impersonated, and is a `PLAT` persona at the Gateway boundary. | The user asked that SSO give an admin role with access to all modules, limited to an explicit allowlist (option A). Trade-off: an allowlisted alias can read and administer across every dealer and district; the allowlist in the git-ignored `deploy/config.env` is the control. Keeping persona actions behind the audited "View as" keeps each write attributable to the persona whose job it is, and keeps the Administrator from being both submitter and approver of a plan. |
| 2026-10-22 | **Agent panel on every signed-in screen, and every automation it lists does what it says (user decision).** Each staff persona (dealer floor, service, OEM field and analytics, Administrator) sees a strip of cards above its screens, one per automation: the agent's name (never its code), the trigger (*Live*, *Scheduled* or *On demand*), Run now where the page offers it, Interact, and the last run read from `PLAT_AgentTraces`. An audit of all 51 cards changed the build where a card over-claimed: every *Scheduled* card now maps to a schedule that ships **enabled for every market**, two jobs were added (A7 `predictive-scan`, nightly; `store-digest`, nightly, written for the Store Cockpit by A5 and the Service Cockpit by A7), two cards with no code behind them were retired (supersession finder, goodwill triage), and "Lender decision chaser" became "Lender decision request" because it only sends the request. Only `autonomy-promotion`, which is not on the panel, ships disabled. Scheduled jobs run at the tenant's published autonomy level (§4.10.1), `recommend` for all of them today. The persona-by-persona map is `docs/AGENT_PANEL_BY_PERSONA.md`. | A panel that names a schedule which never fires, or a task no agent performs, misleads the person relying on it. Trade-off: writer jobs now run by default in every deployed environment; the autonomy level keeps them to cards, leads and drafts, never customer messages, and `deploy_l6 --disable-writers` quiets them until the next deploy. |
| 2026-10-22 | **The store digest carries its own guardrail (user decision, ADR 0003 amendment).** `retail-ops-<env>-guardrail-digest` keeps every policy of the main guardrail (denied topics, content and word filters, PII handling, tier, blocked messages) with contextual grounding **0.2** and the relevance check **off**; only the nightly store digest uses it, and if it is missing the digest uses the main guardrail, never none. The digest retries a grounding fallback once, does not re-send figures the guardrail already held unless the figures or the guardrail change, and states the market currency to the model. The Inventory tab stays figures-only because the dealer wholesale/allocation/pricing topic blocks its insight in every store. Every other model call keeps the main guardrail (§3.6.1, §8). | Against the main grounding check most of the digest's insights were held back, almost all for paraphrasing figures that are all present rather than inventing any; after the change 221 of 240 were written, and the 19 still held are all Inventory-tab insights. Loosening the shared thresholds would also weaken customer chat and the Sales Agent, and relaxing the denied topics is a policy matter. Trade-off: digest text may add mild inference to the figures it quotes; the figures-only text stays one click away and every written insight names its writer and time. |
| 2026-10-28 | **The service advisor uses Trade-In (user decision).** `POST /trade-in/valuation` and `GET /trade-in/prospects` now serve `dealer_service`, which the §5.2 matrix already gave the Trade-In screen. Adding the trade to a purchase journey, the payment comparison and the Advisor link stay with the sales roles, and the console shows them only to roles that open the Advisor or the Inventory desk. | A service advisor opening Trade-In was refused (403). Valuing an owner's car in the service drive and spotting upgrade prospects is the service-to-sales hand-off; the purchase journey itself stays with sales. |
| 2026-10-28 | **Sales advisors work the Lead Desk (user decision).** `dealer_advisor` holds the `dealer.leads` screen: the sidebar item and the Assistant's Lead Desk tab appear for the sales advisor, who sees the store's web leads, sends first responses, assigns them and books appointments. | Matches the API's existing Lead Desk role set (`dealer_bdc`, `dealer_advisor`, `dealer_manager`); the matrix was the only place that left the advisor out. |
| 2026-10-28 | **OEM Sales Ops has no Admin Console screen (user decision).** `oem_sales_ops` opens Sales Ops Comms (home) and the Report Library only; "Admin (comms templates)" is removed from its §5.2 row. The OEM Workspace sidebar gates each item on its own screen, so Sales Ops sees both of its screens. | Every Admin Console route serves the admin roles only, so the app switcher offered Sales Ops an app that refused it; the sidebar hid its own two screens behind `oem.today`. |
| 2026-10-28 | **Each persona lands on its own home (user decision, §5.18.1).** Sign-in, `/`, dev-token links, "View as persona" and the app switcher open the role's home screen from the §5.2 table; a requested deep link is kept when the role can open it, and a page the role cannot open sends it home. The demo portal stays one click away in the shell bar. | Users landed on the demo portal or on an error page, not on the screen they work in. |
| 2026-10-29 | **Admins land on the demo portal; everyone else on their home (user decision, §5.18.1).** The Administrator (`platform_admin`) and the Tenant Admin (`admin_tenant`) land on `/demo` after sign-in, on `/`, from a dev-token link without a path and on "Return to my admin view"; a deep link they can open is kept, the app switcher still opens each app's landing, and "View as persona" lands on that persona's home. The Dealer Admin and every other role keep their home; the shell bar's **Demo** button stays open to every staff role. The role list is one constant (`DEMO_LANDING_ROLES`, console `lib/nav.ts`). | The demo portal is where an admin presenter starts and switches personas; the Dealer Admin is store-scoped, and the portal switches personas across the tenant. Amends the 2026-10-28 landing decision. |

## 14. Open Questions

All ten original questions are resolved (see Decisions Log, 2026-09-10 "pilot scope" entries). Remaining items are confirmations, not blockers:

1. Europe seed market: Germany (`de`) chosen as the representative EU pack (KBA + EEA open data, EUR, km, WLTP). Confirm, or name a different first EU country (UK would need GBP/miles and a separate incentive set).
2. Synthetic brand names: `Meridian Motors` (US) and `Nordwerk Automobile` (EU) proposed. Confirm or supply preferred names before seed generation.
3. the enterprise OIDC/SAML IdP in Cognito: the app code already supports it, but `infra/bootstrap.py` does not create the Cognito domain or the `CorpDirectory` identity provider. Confirm the Federate client registration (client id/secret, issuer) can be reused from the existing `sales-advisor-v2-40207957` domain or needs a new registration for this account.

Questions 4–11 (Process 2 and the agentic operating model) were **all resolved on 2026-09-22 by adopting their stated defaults** (see Decisions Log). They are kept here struck through so the defaults stay visible; each is now a config value or a dated decision, not an open item.

4. ~~Service reference UI.~~ **Resolved:** the Service Desk reuses `sales-advisor-v2.html` (operating screens) and `DM_Assistant_Demo.html` (Board, Cockpit, chat/dictation) with the component mapping in §5.20; no third reference.
5. ~~Sandbox DTC / labor / parts data.~~ **Resolved:** seed `DEALER_DTCReference` from the prior `dtc-database.json`; generate labor ops and parts for the 7 synthetic models per market from public flat-rate ranges; all rows `synthetic=true` with `source_ref`. No licensed labor guide in the pilot; the `labor` and `dtc` connectors keep `LiveConnector` stubs for Mitchell / ALLDATA / OEM service info.
6. ~~Repair-vs-replace threshold.~~ **Resolved:** `repair_vs_replace_threshold_pct=50` of A4 valuation is the tenant default, overridable per dealer in Admin › Autonomy constraints.
7. ~~OEM after-sales persona.~~ **Resolved:** out of the pilot; recorded in §12 as v2 (`oem_aftersales` group reserved, no screens).
8. ~~Default autonomy levels.~~ **Resolved:** every store starts with **all agent actions at `recommend` for the first 14 days** (the "observation fortnight"); on day 15 the §4.10.1 pilot defaults apply automatically unless the dealer principal has set a level per action in Admin › Autonomy. Hard rules are never configurable. Bootstrap seeds both versions of `PLAT_ConfigAutonomy` with `effective_from` dates.
9. ~~Technician roster and skills source.~~ **Resolved:** dealer config in Admin › Users (`PLAT_Users` extension: `skills[]`, `certifications[]` incl. EV high-voltage, `shift_pattern`, `bay_id`); DMS time-clock / HR integration is v2. `dispatch_ro` refuses to assign an HV line to a technician without the certification, regardless of config.
10. ~~Parts staging spend cap.~~ **Resolved:** `stage_parts` orders only when on-hand is zero and predicted-work confidence ≥ 0.8; daily cap defaults to the store's trailing-30-day average daily parts purchase (computed nightly from `DEALER_PartsOrders`, floor set in Admin); both are `PLAT_ConfigAutonomy.constraints` on action `a7.stage_parts`.
11. ~~`DEALER_Recommendations` retention.~~ **Resolved:** 2-year TTL; classification C2; `customer_id` hashed and contact fields absent in the lake `recommendations/` prefix; security review to confirm in Phase 0b alongside the existing C1–C4 export tests (criterion 25).

**Still open (confirmations from 2026-09-10):** items 1–3 above.

12. ~~Package name.~~ **Resolved 2026-09-23: renamed to Intelligent Sales & Service Orchestration (ISSO).** The v1.0 name, *Intelligent Retail & Purchase Orchestration (IRPO)*, named only Process 1 once Process 2 (service journey, A7) and the OEM field-sales layer (A6) were added. Candidates considered: Automotive Lifecycle Orchestration (generic), Sales & Service Journey Orchestration (accurate but a four-letter acronym), Dealer Journey Orchestration (omits the customer surface), Purchase & Service Journey Orchestration (consumer-side wording). ISSO keeps the original "Intelligent … Orchestration" pattern, names both processes in the words dealers use (sales, service), and reads correctly for OEM, dealer, and customer audiences. Tagline: *one owner for the sales journey, one for the service journey, connected.* The technical namespace was later retired from `IRPO_` and re-prefixed by data owner (`OEM_*` / `DEALER_*` / `CUST_*` / `PLAT_*`, with `app-*` / `/app` infra); see §13. **"ISSO" is a working abbreviation for internal documents, not a brand or product name**; external and customer-facing material uses the full descriptive name or the customer's own program name, so no trademark clearance is sought for the acronym.
