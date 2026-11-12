# Automotive Retail Operations powered by Agentic AI — Use Cases (as built)

**What this document is.** The seven use cases (A1–A7) and the shared platform
capabilities, described from what the code in `app/` actually does today: which
module implements each one, what a user can do with it, which screens and API
routes expose it, what data it reads and writes, where a language model is
involved, and how autonomous it is allowed to be. For the target scope and the
rationale behind the delivery model, see `docs/specs/use-cases-summary.md` and
spec §2–§3. Current acceptance status and remaining boundaries are maintained in
`docs/ACCEPTANCE.md`.

**Two business processes.** Process 1, the **purchase journey**
(`DISCOVER → CONFIGURE → QUALIFY → FINANCE → TRADE_IN → AGREE → DELIVER → OWN`),
is owned by A2 and served by A1, A3, A4. Process 2, the **service journey**
(`BOOK → CHECK_IN → INSPECT → DIAGNOSE → APPROVE → REPAIR → READY → FOLLOW_UP →
COMPLETED`), is owned by A7. A5 (marketing analytics) and A6 (OEM field sales)
sit above both at the OEM level. The two processes meet through events
(`vehicle.delivered` opens the first service journey; `service.handoff_to_sales`
opens a new purchase journey), never by one driving the other.

---

## 1. Summary table

| # | Use case | What it does in this build | Primary users | Implementation | Model use | Autonomy |
|---|---|---|---|---|---|---|
| A1 | Personalized Vehicle Discovery | Turns a lifestyle/budget intake into a ranked, explainable shortlist of trims with VIN-level availability, price build, incentives and an EV-vs-ICE cost-of-ownership comparison; saving a build advances the journey; a daily re-score announces in-stock matches (`discovery.match_found`) | Customer (`/shop`), dealer advisor (`/dealer/discovery`) | `agents/discovery.py`, `configurator.py`, `tco.py`; `platform/ranker.py` | one-line "why this fits" (Bedrock, optional) | read/recommend only |
| A2 | Purchase Journey Orchestration | Owns the purchase state machine: validates each stage's required context before advancing, writes the event before the state, picks the next best action, aggregates the Sales Package, runs Plays on triggers, nurtures prospects on a consented cadence, detects intent shifts, answers customer questions from their own record | System (events, schedule); advisor; customer (`/shop/chat`) | `journeys/*` (supervisor, package, nurture, nurture_tick, plays, qa, intent_shift, actions, handoffs) | grounded customer Q&A after a deterministic refuse/escalate gate | `a2.schedule_outreach` default `recommend` (writes a card, does not send) |
| A3 | Dealer Sales Advisor | Stage-aware suggestions that cite a real recent activity, live-floor objection classification with a grounded suggested response, competitive/incentive panels, deal structuring with a credit-compliance gate, assistant chat and flows (follow-up draft, service upsell, call list / shift list), and the **Assistant reasoning agent** (Strands on Bedrock, ADR 0002) that plans over eleven tools | Advisor (`/dealer/advisor`, `/dealer/assistant` with *Agent mode*), BDC (`/dealer/leads`), service advisor (Service Lane) | `agents/assistant.py`, `a3_agent.py`, `suggestions.py`, `objection.py`, `intel.py`, `deal.py`; `dealer/leads.py` | objection response; assistant answers (grounded, Knowledge Base retrieval); agent loop (Sonnet 4.5) — all behind the Bedrock Guardrail | suggestions only; `send_outreach` tool held for human below `act_and_notify` |
| A4 | Trade-In & Upgrade Advisor | Valuation band, payoff, equity and payment delta with the formula; nightly equity monitor that flags customers crossing into positive equity (`equity.crossed_positive`) and lease-end windows (`lease.end_window_entered`); upgrade prospects and used-vehicle acquisition recommendations; trade dispositions | Advisor (`/dealer/trade-in`), inventory/used-car manager (`/dealer/inventory`), customer (My Garage trade) | `agents/trade.py`, `equity.py`, `equity_monitor.py`, `prospects.py`; `dealer/inventory.py` | one-line equity explanation (optional) | `a4.detect_equity` `recommend`; never contacts the customer |
| A5 | Campaign ROI Optimizer | Spend → journeys → deliveries attribution per campaign/dealer, funnel by source, rule-built audiences with hashed-id export, a budget-shift recommendation that becomes an approval request; nightly attribution job; analytics Q&A delegating to AWS Quick when enabled | OEM marketing/finance (`/marketing/*`), dealer principal (`/dealer/campaigns`) | `marketing/analytics.py`, `audiences.py`; `oem/campaigns.py`, `district_cycle.run_nightly_attribution` | budget-shift insight sentence | hard ceiling `act_with_approval`: spend is never changed by the agent |
| A6 | OEM Sales Administrator | District manager workspace: Today KPIs and alerts, account detail with composite/EV-readiness/compliance, region view, scenario planning, weekend projection, guardrail-checked `ask`, draft account plans that go through a human approval state machine, weekly district cycle | District / regional managers, C&I planning, sales ops (`/oem/*`, 22 screens) | `oem/workspace.py`, `today.py`, `alerts.py`, `planner.py`, `account_plan.py`, `guardrails.py`, `district_cycle.py`; AWS Quick agent/flows via `platform/quick.py` | Today insight; workspace `ask` (grounded) | hard ceilings: plan publishing `act_with_approval`, incentive allocation `recommend`; GR-001…008 rules on input and output |
| A7 | Service Operations | Owns the service state machine; predicts next service and books; opens the RO; records MPI findings; builds a priced, sourced estimate line by line; presents it to the customer by magic link; per-line approve/decline; parts tracking and line status; ready notification; declined-work follow-up on a service cadence; morning shop-load (dispatch, stage parts, re-forecast); service-lane equity monitor → handoff to sales; predictive maintenance leads | Service advisor, technician, parts, service manager (`/dealer/service/*`), customer (`/garage/service`) | `service/*` (supervisor, booking, inspection, estimates, repair, followup, morning_work, shop_load, lane_monitor, predictive, connectors, desk) | customer-safe explanation of a finding (grounded) | `a7.dispatch_ro` / `stage_parts` / `reforecast_promise` `recommend`; estimate approval is the customer's (hard ceiling) |

---

## 2. Use cases in detail

### A1 — Personalized Vehicle Discovery

* **Entry points.** Customer: `POST /shop/intake` (≤4 follow-up questions
  inferred from the lifestyle profile), `POST /shop/shortlist`, `POST
  /shop/builds`, `POST /shop/test-drive`. Advisor: `GET
  /journeys/{id}/discovery|lifestyle|recommend-config|price-build|inventory`,
  `POST /discovery/shortlist`, `GET /configurations/options|similar`, `POST
  /configurations`, `GET /journeys/{id}/tco`.
* **How it ranks.** `discovery.rank_vehicles` scores catalog trims against
  budget and the lifestyle profile; every match returns its score components
  and a `source_ref`, and cross-references `DEALER_Inventory` for VIN-level
  availability. The ranker is an interface: the default is the transparent rules
  ranker; with `INTEGRATION_MODE=aws` and a Personalize campaign ARN it calls
  `GetPersonalizedRanking` and falls back to rules on any error.
* **Cost of ownership.** `tco.build_tco_comparison` picks an EV and an ICE
  trim and computes an N-year cost from catalog rows and `OEM_Markets`
  (energy/fuel defaults, units, tax rule), returning inputs and formula. No
  market literal lives in code.
* **Writes.** Saving a build writes `PLAT_Configurations`, emits
  `build.saved`, and advances the journey out of `DISCOVER`/`CONFIGURE` when
  the gate allows.
* **Screens.** `/shop`, `/dealer/discovery`, Advisor centre column.

### A2 — Purchase Journey Orchestration (supervisor + nurture)

* **State machine.** `journeys/supervisor.py`: `REQUIRED_CONTEXT` per stage
  (`DISCOVER: shortlist`, `CONFIGURE: saved_build`, `QUALIFY: budget +
  timeline`, `FINANCE: finance_offer`, `TRADE_IN: valuation`, `AGREE:
  deal_structure + credit_compliance`, `DELIVER: delivered`).
  `advance_journey` moves one stage, writes `DEALER_JourneyEvents` first, then
  flips `DEALER_Journeys`, then emits `journey.stage_changed`. The Delivery
  Board's deliver action (`dealer/delivery.py`) is what emits
  `vehicle.delivered` once the prep checklist and documents are complete. The `AGREE` gate consumes A3's
  compliance gate (OFAC, Red Flags, adverse action) — a check with no recorded
  result is `pending` and blocks.
* **Next best action.** A per-stage table maps the stage to the agent and
  action that should happen next (e.g. `QUALIFY → A3 book_test_drive`,
  `TRADE_IN → A4 compute_equity`); `log_nba` persists the choice to
  `DEALER_Recommendations`.
* **Sales Package.** `package.build_sales_package` calls A1/A3/A4 and finance
  in-process (the spec's agent-to-agent hop collapsed into a function call),
  stores `DEALER_SalesPackages`, polled by `GET /journeys/{id}/package`.
* **Nurture.** `nurture.plan_nurture` builds a `DEALER_NurturePlans` row from
  the active `PLAT_ConfigNurture` version (cadence caps, templates, A/B
  variants, stop conditions `opt_out · converted · stage_AGREE_reached`).
  `compose_message` renders a versioned template with facts from the record.
  `nurture_tick.run_nurture_tick` (scheduled every 6 h) finds due plans,
  re-checks consent (`CUST_Consent`), quiet hours and caps, and at the default
  `recommend` level writes a `nurture_touch` recommendation card rather than
  sending; at execute levels it sends through `OutreachProvider` (SES/SNS).
* **Plays.** `plays.run_play` maps trigger events to templates seeded in
  `DEALER_Plays` (`journey.stalled → Callback Brief`, `discovery.match_found →
  Recommend Vehicle`, `service.handoff_to_sales → Full Prep`,
  `lease.end_window_entered` / `equity.crossed_positive → Upgrade Pitch`) and
  records `DEALER_PlayRuns` plus a recommendation card.
* **Intent shift.** `intent_shift.detect_intent_shift` reads events, messages
  and `DEALER_Signals` and flags budget change / new competitor / gone cold /
  ready to buy, each citing the signal; `create_task` opens a `DEALER_Tasks`
  row for a human.
* **Customer Q&A.** `qa.answer_customer_question` (behind `/shop/chat` and
  `/journeys/{id}/chat`) runs a deterministic classifier first — legal,
  credit-decision and safety-recall questions are refused or escalated — and
  only then grounds an answer in the customer's own build, offer (payment
  ranges only), valuation, incentives and dealer hours.
* **Ownership.** Journeys change owner only through `DEALER_Handoffs`
  (`POST /handoffs`, `/accept`), which creates a task for the receiver.
* **Screens.** `/dealer/journeys` (kanban), `/dealer/advisor/[id]`,
  `/shop/chat`, `/marketing/journey-ops` (the OEM CX editor for the nurture
  policy).

### A3 — Dealer Sales Advisor

* **Suggestions.** `suggestions.generate_contextual_suggestions`: a
  per-stage matrix plus the active `PLAT_ConfigSuggestions` row
  (`precompute_stages`, `confidence_threshold`); a suggestion is emitted only
  when a rule fires *and* there is a recent activity record to cite.
* **Live floor.** `POST /journeys/{id}/utterance` → `objection.classify_objection`
  matches the utterance to `DEALER_ObjectionStrategies` for the market and asks
  Bedrock for a short response anchored to the strategy's example phrasing and
  `source_ref` (system prompt forbids pricing promises and disparagement); if
  the model is unavailable the curated phrasing is returned. `POST
  /journeys/{id}/objection/outcome` records "Use This / Alternative" in
  `DEALER_Outcomes` and the recommendation loop.
* **Panels.** `intel.get_competitive_intel` does an in-catalog cross-shop
  (same segment) and says explicitly that an external competitor feed is not
  wired; `get_incentives` reads `OEM_Incentives` for model and market.
* **Deal desk.** `deal.structure_deal` assembles offer, trade, F&I menu and
  totals; `compliance_gate` reports recorded OFAC / Red Flags / adverse-action
  status without assuming a pass.
* **Assistant agent (pilot).** `POST /assistant/agent` (or `/assistant/chat`
  with `{"mode":"agent"}`; the *Agent mode* toggle on the screen) runs
  `a3_agent.run`: a Strands agent on Bedrock that chooses among eleven tools —
  lifecycle context, recent activity, suggestions, incentives, trade equity,
  knowledge search (Bedrock Knowledge Base, market-filtered), objection
  strategy, customer memory, draft follow-up, create task, send outreach — each scope-checked
  against the caller's claims; the A2 refuse/escalate classifier runs before
  the model, `send_outreach` is held for a human below `act_and_notify`, every
  tool's `source_ref` becomes a citation, and a `PLAT_AgentTraces` row records
  the tools used. The service Lambda delegates the call to the Sales Agent runtime
  (`retail_ops_dev_a3_sales`, ADR 0009) and falls back to the one-shot path if the runtime is unavailable, saying so
  in `engine`. Baseline (dev data): 15/15 golden cases, 6–18 s per answer.
* **Assistant.** `POST /assistant/chat` (grounded answer over lifecycle context,
  activity, knowledge passages from the Bedrock Knowledge Base), `POST /assistant/flows/{follow-up-draft |
  service-upsell | call-list}`, `GET/POST /assistant/shift-list[/publish]`
  (call list → `DEALER_Tasks`), `POST /journeys/{id}/outreach` (consent-checked
  send through SES/SNS, recorded to `CUST_Messages`, `message.outbound`).
* **Lead Desk.** `dealer/leads.py`: SLA status from `DEALER_LeadSLA`, assign
  creates/links a `DISCOVER` journey (handoff when the owner changes), first
  response via `OutreachProvider`.
* **Service Lane.** `GET /service/lane`: today's service visits with the A4
  equity flag and an upsell brief for the advisor.
* **Screens.** `/dealer/advisor`, `/dealer/assistant`,
  `/dealer/assistant/service`, `/dealer/leads`, `/dealer/customers/[id]`.

### A4 — Trade-In & Upgrade Advisor

* **Valuation and equity.** `trade.get_trade_valuation` takes the latest
  guidebook-anchored `DEALER_Valuations` row and adjusts transparently for
  mileage and condition; `equity.build_equity` computes equity, payment delta
  against the active finance offer, and an upgrade-timing signal, every figure
  with inputs, formula and `source_ref` (`platform/compute.py` records the
  audit triple). `add_trade_to_journey` writes `TRADE_IN` context and emits
  `trade.valued`.
* **Nightly monitor.** `equity_monitor.run_equity_monitor` (Lambda
  `retail-ops-dev-equity-monitor`, 06:00 UTC) scans active journeys, recomputes
  equity, and upserts a deterministic `REC-MON-<journey>` recommendation card
  (`equity_flag`) for customers crossing into positive equity. The first time a
  journey is seen positive it emits `equity.crossed_positive`, and the first
  time a lease is inside the upgrade window it writes a `REC-LEASE-<journey>`
  card and emits `lease.end_window_entered`; both trigger the A2 Upgrade Pitch
  play through the bus.
* **Prospecting and acquisition.** `prospects.find_upgrade_prospects`
  (mileage, lease-end proxy, equity) and `recommend_acquisition` (service-drive
  vehicle vs store demand from A1 shortlist signals); surfaced on the Inventory
  & Trade Desk (`/inventory/acquisitions`, `/trades/dispositions`).
* **Customer view.** `GET /garage/trade`, `POST /garage/trade/refresh`.
* **Screens.** `/dealer/trade-in`, `/dealer/inventory`, Advisor "Value
  Snapshot", My Garage.

### A5 — Campaign ROI Optimizer

* **Attribution.** `marketing/analytics.py` and `oem/campaigns.py`: spend from
  `OEM_Campaigns` allocated to dealers, journeys tagged with the campaign,
  completed deliveries, cost per delivery and ROI; funnel by source. A known id
  mismatch between campaign tables is normalised and flagged in the payload
  rather than hidden.
* **Budget shift.** `submit_budget_shift` writes a recommendation card and a
  `DEALER_Approvals` request for `oem_finance`; `run_nightly_attribution`
  drafts the request automatically when projected lift crosses a threshold.
  Approving records a decision only — spend is never changed by code (hard
  ceiling `act_with_approval`).
* **Audiences.** `audiences.build_audiences` builds transparent rule segments
  over profiles, journeys, vehicles and valuations; `export_audience` emits a
  manifest with SHA-256 hashed ids only and refuses consent opt-outs.
* **Analytics Q&A and dashboards.** `POST /marketing/ask` answers from the
  read models and delegates to AWS Quick when `ENABLE_QUICK=true`;
  dashboards are authored in AWS Quick over the lake datasets and embedded
  through `GET /oem/quick/dashboards/{id}/embed-url` (per-user reader
  registration + group sharing; not row-scoped).
* **Screens.** `/marketing/campaigns`, `/marketing/audiences`,
  `/marketing/quick`, `/dealer/campaigns`, `/oem/campaigns`.

### A6 — OEM Sales Administrator

* **Workspace.** `oem/today.py` (district-pulse KPIs from `OEM_Accounts`,
  alert triage from `OEM_Alerts`, recent activity, one Bedrock insight card),
  `workspace.list_accounts / account_detail / region_view / update_alert`,
  `planner.compute_composite / analyze_performance_gaps / get_ev_readiness /
  get_compliance / predict_weekend / simulate_scenario` — all deterministic over
  the seeded OEM tables, every number with a `source_ref`. Scope is the user's
  org node (district → dealers, region → districts, national → all).
* **Guardrails.** `guardrails.check_guardrails` applies GR-001…GR-008 (wholesale
  reduction, termination language, specific pricing guidance, hiring/firing,
  allocation promises → block; legal and financial interpretation →
  require approval; competitive disparagement → warn) to the user's text and to
  any generated output, returns a safe alternative on block, and writes
  `OEM_GuardrailEvents`. Rules come from the active `PLAT_ConfigGuardrails`
  version; the Admin console has a test harness and sweep.
* **Account plans.** `account_plan.draft_account_plan` writes a `draft`
  `OEM_AccountPlans` row with objectives tied to metric gaps; `POST
  /approvals/plan/{id}/submit` starts the Step Functions execution and `decide`
  resumes it. A6 never publishes, contacts a dealer, or changes an allocation.
* **District cycle.** `district_cycle.run_district_cycle` (the `district-cycle`
  job, scheduled weekly on Monday 05:00 UTC for both markets) drafts plans for
  red dealers and rolls up gaps.
* **AWS Quick.** The document workbench (chat agent, eight Flows,
  knowledge bases, Quick Research) is authored in the AWS Quick console and
  reaches operational data through the CUSTOM_JWT Gateway's four read tools;
  `GET /oem/quick/flows` lists what exists and `/oem/quick` embeds it.
* **Screens.** 20 under `/oem/*` (Today, Performance, Composite, Region,
  Accounts, Contact Reports, Action Plans, Alerts, Weekend, Meeting in a Box,
  Scenario, Conquest, EV Readiness, Compliance, Campaign ROI, Quick, Comms,
  Report Library, Guardrails).

### A7 — Service Operations

* **State machine.** `service/supervisor.py` mirrors A2: gates at `CHECK_IN`
  (a repair order exists), `INSPECT` (findings recorded) and `APPROVE` (a priced
  estimate with `source_ref` *and* a recorded customer decision on the current
  estimate); event written to `DEALER_ServiceJourneyEvents` before the row
  flips; `service.stage_changed` emitted.
* **Front door.** `booking.predict_next_service` (odometer + delivery date +
  market annual-distance default + interval, inputs returned) and
  `book_service` (writes `DEALER_ServiceJourneys` + `DEALER_Appointments`,
  emits `service.journey.created` and `service.appointment.booked`); customer path `GET /garage/service/slots`, `POST
  /garage/service/book`.
* **Inspection to estimate.** `inspection.record_inspection` (MPI
  green/yellow/red against `DEALER_InspectionTemplates`), `prioritize_findings`,
  `build_estimate` / `estimates.build_lines`: one line per red/yellow finding,
  each with a labor op (`DEALER_LaborOps` flat-rate × shop rate), parts
  (`DEALER_Parts`), coverage decision (`DEALER_Coverage`) and price
  `source_ref`; `repair_vs_replace` card when the total exceeds a share of the
  A4 vehicle value; `connectors.lookup_dtc / get_labor_time / lookup_parts /
  check_coverage` are the sandbox DMS/reference lookups.
* **Customer approval.** `present_estimate` marks the estimate presented and
  issues a magic link; the customer decides per line (`POST
  /garage/service/estimates/{id}/decisions`), declined lines land in
  `DEALER_DeclinedWork` and `followup.schedule_declined_followup` opens a
  service-family nurture plan (30/60/90-day reminders, no sales content).
  `connectors.explain_finding` gives a guardrail-checked, customer-safe
  explanation.
* **Repair to ready.** `repair.track_parts` (back-order sets a parts hold),
  `update_line_status` (`in_progress → complete → qc_passed`), `notify_ready`
  (closes the RO, schedules pickup, notifies the customer, `service.ready`).
* **Hourly follow-up tick.** `followup_tick.run_service_followup_tick`
  (job, hourly): sends or recommends the due declined-work reminders from the
  service-family nurture plans (consent, quiet hours and caps re-checked;
  stopped when the work gets booked) and writes one `service_due` card per
  vehicle whose predicted service is due within `RETAIL_OPS_DUE_SERVICE_DAYS` and has
  no open service journey. Autonomy action `a7.send_due_service_reminder`.
* **Morning work.** `shop_load.run_shop_load` (job, 10:00 UTC schedule, ENABLED
  in dev at level `recommend`) runs `dispatch_ro` (eligible technician from the
  `PLAT_Users` skills roster, EV high-voltage lines only to certified staff),
  `stage_parts`, `reforecast_promise`, `auto_present_estimate` and
  `prepare_ro_brief`, each gated on its own autonomy action and written as a
  recommendation card at `recommend`.
* **Service → sale.** `lane_monitor.run_service_lane_monitor` flags
  `FOLLOW_UP` journeys crossing into positive equity; an advisor actions the
  card into `POST /service/{id}/handoff-to-sales`, which creates a new purchase
  journey A2 owns and emits `service.handoff_to_sales` (→ Full Prep play).
* **Predictive maintenance.** `predictive.py` applies per-part wear curves to
  odometer and history, raising `DEALER_Leads` rows
  (`source=predictive_maintenance`) and My Garage offers; it is explicitly the
  seam where a telematics connector would plug in.
* **Role masking.** Technicians see no customer contact, pricing or coverage
  split; parts staff manage orders but not line status; estimates are
  advisor/manager (and the owning customer) only.
* **Screens.** `/dealer/service` (board), `/dealer/service/[id]` (+ `checkin`,
  `inspect`, `estimate`), `/dealer/service/book`, `my-ros`, `parts`,
  `declined`, `history/[vin]`, `cockpit`; customer `/garage/service[/id]`.

---

## 3. Platform capabilities every use case relies on

| Capability | Where | What it gives the use cases |
|---|---|---|
| Unified Shell and persona homes | `common/personas.py`, `GET /me/home`, `/nav/resolve`, `common/resource_url.py` | one canonical URL per entity (`/r/<type>/<id>`), resolved to the best screen the caller's role may open; context envelope "you're here because…" |
| Tasks and handoffs | `platform/tasks.py`, `journeys/handoffs.py` | one task queue with SLAs for every agent and screen; ownership changes only through accepted handoffs |
| Notifications | `dealer/notifications.py` | event → role routing via versioned rules; bell and inbox in the shell |
| Approvals | `approvals/generic.py`, `plan_approval.py` | one inbox; Step Functions for plan publishing |
| Recommendation outcome loop | `platform/recommendations.py` | shown/accepted/dismissed/converted per agent action; stats feed the Admin Agents panel |
| Autonomy configuration | `admin/autonomy.py`, `common/autonomy_config.py` | per-action level with history, rollback and hard ceilings |
| Versioned configuration | `admin/config_store.py`, `marketing/journey_ops.py` | guardrails, suggestion matrix, notification rules, flags, connectors, nurture policy — publish/rollback without redeploy |
| Market parameterisation | `common/market.py`, `console/src/lib/market.ts` | currency, units, tax and energy defaults from `OEM_Markets` (`us`, `de` seeded) |
| Knowledge corpus | `PLAT_KnowledgeCollections` + 538 documents exported to S3 + Bedrock Knowledge Base `retail-ops-dev-quick-index` (S3 Vectors) | CRM notes, templates, transcripts, objection strategies, bulletins, prior plans, product guides, repair procedures, TSBs — semantically retrieved, filtered by kind/market, cited by `source_ref`, for A3/A6/A7 and the AWS Quick knowledge bases |
| Durable customer memory | `platform/memory.py`, `deploy_memory.py`, `PLAT_AgentMemory`, AgentCore Memory | preferences, objections, promises, and sourced facts persist across journeys; AWS failures degrade to the provenance-preserving DynamoDB store. Not deployed in the development account (`deploy_memory.py` is not a `deploy.sh` stage), so memory is off there |
| Audited computation | `platform/compute.py` | AgentCore Code Interpreter executes calculation expressions in AWS mode; the restricted local evaluator preserves the same inputs/code/result audit contract as fallback |
| Connector boundary | `connectors/{base,sandbox,live}.py` | DMS, labor, and parts operations share one sandbox/live interface; live endpoints and credentials are environment configuration |
| Model-tier guardrail | `app/infra/deploy_guardrails.py`, `platform/generate.py` | a Bedrock Guardrail on every Converse call (denied topics, filters, PII, grounding), calibrated against 21 prompts; the nightly store digest has its own copy with grounding 0.2 and no relevance check, every other call uses the main one |
| Evaluation harness | `app/eval/` | golden prompts per agent; offline suite gates CI, agent/API modes score the Strands pilot against a kept baseline |
| Event lake and analytics | `deploy_lake*.py`, `deploy_quick_*.py` | Athena tables and AWS Quick datasets (shared by Quick group, not row-scoped) for A5 and Store/Service cockpits |
| Admin Console | `admin/*`, `/admin/*` (13 screens) | users and roles, org tree, market and state rules, connectors, flags, guardrail harness, autonomy, agents health, audit, data-subject requests, entity merge queue |
| Synthetic data and offline mode | `scripts/seed/generate`, `sample-data/synthetic`, `common/local_ddb.py` | two markets (US, DE), 68 tables, storylines; every screen and the 752 tests run without AWS |

---

## 4. Verification and boundaries

What has been verified against the deployed development stack, how (the
`run_demo` write loop, smoke and acceptance suites, the eval harness in API
mode), and which boundaries remain (deterministic orchestration for A2, A6 and
A7; buffered rather than streamed floor responses; live CRM/DMS, Personalize,
Amazon Connect and console-authored AWS Quick content as environment-specific
setup) are recorded once, in `docs/ACCEPTANCE.md`. The live inventory of the
development account is in `docs/DEMO.md`; the AWS Quick console runbook is
`docs/QUICK_BUILD.md`. This document does not repeat them.
