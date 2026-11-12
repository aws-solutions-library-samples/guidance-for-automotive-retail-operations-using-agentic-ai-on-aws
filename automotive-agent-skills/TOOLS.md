# Tools Reference

Agent **tools** are the callable functions an agent runtime exposes (Strands `@tool`, AgentCore Gateway targets, Quick Flow steps). They are distinct from **skills**, which are `SKILL.md` knowledge modules that shape how an agent reasons. Skills in this repository reference these tools by name where a procedure step maps to a call.

Generated 2026-09-25 from the Intelligent Sales & Service Orchestration requirements spec v1.3 §3 by `scripts/extract_tools.py`. Do not edit by hand; change the spec and re-run.

**121 tools across 7 agents** (16 proactive, event- or schedule-triggered), plus `write_trace(...)` shared by all agents for the AI Log and audit trail.

## Conventions

- Every effectful tool passes `autonomy_level(action_id, scope)` before executing (observe / recommend / act_with_approval / act_and_notify). Hard rules (consent, quiet hours, OEM guardrails GR-001…008, customer-owns-approval, no automatic spend or allocation, technician data masking, HV certification) sit above configuration.
- Every figure a tool returns carries a `source_ref`; agents never quote a number a tool did not return.
- Every recommendation a tool surfaces is a row in `DEALER_Recommendations` (shown → accepted → converted).
- Tools marked **proactive** run on events or schedules with no user request.

## Summary

| Agent | Role | Delivery model | Process | Tools |
|---|---|---|---|---|
| A1 | Personalized Vehicle Discovery | Custom agent on Amazon Bedrock AgentCore | Process 1 — Retail purchase | 13 |
| A2 | Purchase Journey Orchestration (supervisor + nurture) | Custom agent on Amazon Bedrock AgentCore | Process 1 — Retail purchase | 21 |
| A3 | Dealer Sales Advisor | Custom agent on Amazon Bedrock AgentCore (floor streaming + assistant chat/flows in one agent; Quick Index as a retrieval tool) | Process 1 — Retail purchase | 13 |
| A4 | Trade-In & Upgrade Advisor | Custom agent on Amazon Bedrock AgentCore | Process 1 — Retail purchase | 8 |
| A5 | Campaign ROI Optimizer | Amazon Quick / QuickSight analytics + AgentCore for recommendations | Process 1 — Retail purchase | 9 |
| A6 | OEM Sales Administrator | Amazon Quick (agent + Flows + Index + Research) | Process 1 — Retail purchase | 32 |
| A7 | Service Operations (service-journey supervisor) | Custom agent on Amazon Bedrock AgentCore | Process 2 — Service & ownership | 25 |

## A1 — Personalized Vehicle Discovery

*Delivery model:* Custom agent on Amazon Bedrock AgentCore  
*Process:* Process 1 — Retail purchase  
*Tools:* 13

| Tool | Signature | Source / behaviour | Proactive |
|---|---|---|---|
| `build_tco_comparison` | `build_tco_comparison(vehicle_a, vehicle_b, usage, market_config)` | Internal calc EV vs ICE 5-year TCO in market units (US: $/mi, MPG vs MPGe, state fuel/energy prices, federal + state EV credits) |  |
| `get_configurator_options` | `get_configurator_options(model)` | Configurator connector Trims, packs, options, prices |  |
| `get_customer_profile` | `get_customer_profile(customer_id)` | CRM + CDP connector Unified profile, preferences, lifecycle |  |
| `get_incentives` | `get_incentives(vehicle_id, state, buyer_type)` | `OEM_Incentives` Federal 30D eligibility (MSRP cap, AGI cap, assembly), state rebates, utility rebates |  |
| `get_personalized_ranking` | `get_personalized_ranking(user_id, candidate_vehicle_ids)` | Amazon Personalize (Ranking recipe) Falls back to rules if no model |  |
| `get_session_signals` | `get_session_signals(anonymous_id)` | CDP connector Configs, compares, content read, dwell time |  |
| `get_similar_vehicles` | `get_similar_vehicles(vehicle_id)` | Personalize (Similar-Items) Cross-shop suggestions |  |
| `infer_lifestyle_profile` | `infer_lifestyle_profile(profile, signals, answers)` | Bedrock Produces `LifestyleProfile` with confidence per field; asks intake questions only for low-confidence fields |  |
| `price_build` | `price_build(config, state, incentives)` | Configurator + `OEM_Incentives` + `OEM_StateRules` MSRP, destination, est. tax/fees, applicable incentives, est. monthly buy/lease |  |
| `recommend_trim_and_packages` | `recommend_trim_and_packages(model, lifestyle_profile, budget)` | Configurator connector + Bedrock Picks trim and packages that satisfy lifestyle needs within budget; explains each package by need (e.g., tow package ↔ boat) |  |
| `record_shortlist` | `record_shortlist(journey_id, shortlist)` | Journey store Advances DISCOVER → CONFIGURE |  |
| `rescore_profiles` | `rescore_profiles(event)` | Subscriber to `inventory.changed`, `incentive.changed`, `signal.recorded` Re-ranks every saved profile / build the event touches (VIN arrived within radius, price or incentive change puts a build in budget, better-fit trim); emits `discovery.match_found { journey_id, reason, evidence_refs }` for A2 to act on. Never messages the customer itself | yes |
| `search_inventory` | `search_inventory(filters)` | Dealer Inventory connector (VIN-level) Exact-config and near-match VINs by dealer radius, in-transit ETA |  |

## A2 — Purchase Journey Orchestration (supervisor + nurture)

*Delivery model:* Custom agent on Amazon Bedrock AgentCore  
*Process:* Process 1 — Retail purchase  
*Tools:* 21

| Tool | Signature | Source / behaviour | Proactive |
|---|---|---|---|
| `aggregate_package` | `aggregate_package(results)` | Combine into `SalesPackage` |  |
| `answer_customer_question` | `answer_customer_question(question, journey)` | Grounded Q&A: pulls from configurator (specs, packages), `OEM_Incentives`, F&I connector (payment ranges, no credit decisions), inventory, trade valuation, dealer hours. Refuses/escalates on legal, credit-decision, or safety-recall questions |  |
| `book_test_drive` | `book_test_drive(journey, dealer_id, slot)` | Dealer calendar connector (sandbox: `DEALER_Appointments`); writes `visit_booked` |  |
| `compose_message` | `compose_message(journey, intent, channel, market_config)` | Personalized message grounded in the customer's saved build, incentives expiring, inventory arrival, trade value change; channel-specific length/format; brand voice from OEM config |  |
| `create_journey` | `create_journey(customer_id, source, context, previous_journey_id?)` | Creates a purchase journey in DISCOVER; enforces the single-active rule (§2.1), marks the customer's OWN journey `superseded_by`; called by A1 shortlist save, Lead Desk intake, and A7 `handoff_to_sales` (`source=service_lane`) |  |
| `create_task` | `create_task(assignee, journey_id, text, due)` | Dealer task list |  |
| `detect_intent_shift` | `detect_intent_shift(events, messages)` | Flags budget change, new competitor considered, went-cold, ready-to-buy; triggers re-plan and dealer alert |  |
| `get_customer_memory` | `get_customer_memory(customer_id)` | AgentCore Memory: lifestyle profile, stated objections, preferred channel/time, vehicles considered, promises made |  |
| `get_journey` | `get_journey(journey_id)` | State machine with audit |  |
| `handoff_to_human` | `handoff_to_human(journey, reason, transcript)` | Creates dealer/BDC task with full context; pauses automated nurture |  |
| `invoke_agent` | `invoke_agent(agent_name, payload)` | A2A call to A1/A3/A4/A5; Gateway tool call for Quick-hosted A6 (parallel where independent) |  |
| `nudge` | `nudge(journey, target_commitment)` | Chooses the smallest next commitment and attaches a one-click action (test-drive slots from dealer calendar, prequal link, trade-estimate link) |  |
| `plan_nurture` | `plan_nurture(journey, memory, consent)` | Builds/refreshes `NurturePlan`; respects quiet hours, frequency caps, TCPA/CAN-SPAM consent flags |  |
| `propose_policy_change` | `propose_policy_change(evidence)` | Weekly: drafts template, cadence, or suggestion-matrix changes where outcomes under-perform, into Journey Ops as a review item (never self-publishes) | yes |
| `publish_shift_list` | `publish_shift_list(dealer_id, role)` | At shift start creates the ranked call / follow-up list as Tasks for BDC and advisors (replaces the on-demand Quick Flow as the source; the Flow remains for ad-hoc questions) | yes |
| `run_play` | `run_play(play_id, journey_id, trigger)` | Executes an AR-10 Play without a click when a trigger event fires: `journey.stalled` → *Callback Brief*, `discovery.match_found` → *Recommend Vehicle*, `service.handoff_to_sales` → *Full Prep*, `lease.end_window_entered` / `equity.crossed_positive` → *Upgrade Pitch*; autonomy level per play | yes |
| `schedule_outreach` | `schedule_outreach(customer_id, channel, template, when)` | Channel-agnostic. With `ENABLE_CONNECT=true` → Amazon Connect outbound (voice/SMS/chat). Default → SES email / SNS SMS + dealer task. Channel availability read from `OutreachConfig` |  |
| `select_next_best_action` | `select_next_best_action(journey)` | Chooses message / channel / timing / play by comparing candidate actions against `DEALER_Recommendations` aggregates for similar `context_hash` (stage, source, days-in-stage, signals) — bandit-style exploration inside Journey Ops policy bounds; the chosen action and its alternatives are logged as recommendations | yes |
| `update_customer_memory` | `update_customer_memory(customer_id, facts)` | AgentCore Memory: lifestyle profile, stated objections, preferred channel/time, vehicles considered, promises made |  |
| `update_journey_stage` | `update_journey_stage(journey_id, stage, evidence)` | State machine with audit |  |
| `validate_handoff` | `validate_handoff(journey)` | Checks required fields per stage (see §2.1); blocks transitions with missing context; at AGREE also requires the credit-compliance gate (OFAC, Red Flags, adverse action) |  |

## A3 — Dealer Sales Advisor

*Delivery model:* Custom agent on Amazon Bedrock AgentCore (floor streaming + assistant chat/flows in one agent; Quick Index as a retrieval tool)  
*Process:* Process 1 — Retail purchase  
*Tools:* 13

| Tool | Signature | Source / behaviour | Proactive |
|---|---|---|---|
| `build_call_list` | `build_call_list(advisor, date)` | Quick Flow over CRM + DMS + journeys; prioritized with talking points |  |
| `classify_objection` | `classify_objection(utterance)` | Existing `ObjectionStrategies` table |  |
| `draft_follow_up` | `draft_follow_up(customer, channel, intent)` | Quick Flow; email/SMS/call script in OEM brand voice |  |
| `generate_contextual_suggestions` | `generate_contextual_suggestions(customer_360, lifecycle, activity, role)` | Quick agent / Bedrock; applies the suggestion matrix; every suggestion carries evidence refs |  |
| `get_competitive_intel` | `get_competitive_intel(vehicle_id, competitor)` | Internal KB |  |
| `get_cross_sell` | `get_cross_sell(vehicle_id, profile)` | Personalize + F&I product catalog (GAP, VSC, tire & wheel, prepaid maintenance, home charger install) |  |
| `get_incentives` | `get_incentives(vehicle_id, customer, state)` | `OEM_Incentives` + OEM incentive feed (connector); federal/state/utility/OEM tiers |  |
| `get_lifecycle_context` | `get_lifecycle_context(customer_id)` | Journey stage or ownership stage (DMS: in-service, months on lease, mileage, recalls) |  |
| `get_objection_strategy` | `get_objection_strategy(type, context)` | Existing `ObjectionStrategies` table |  |
| `get_recent_activity` | `get_recent_activity(customer_id, window)` | CRM activities, digital-retail/lead events, DMS RO history, campaign clicks |  |
| `push_deal_to_dms` | `push_deal_to_dms(journey_id, deal)` | DMS connector – advances AGREE |  |
| `record_outcome` | `record_outcome(journey_id, outcome)` | Outcomes table (feeds A5 + strategy success rates) |  |
| `structure_deal` | `structure_deal(vehicle, finance_offer, trade_equity, incentives, state)` | F&I connector (CDK) – returns monthly payment scenarios with state sales tax, doc fees, and TILA/Reg Z disclosure text from `market_config` |  |

## A4 — Trade-In & Upgrade Advisor

*Delivery model:* Custom agent on Amazon Bedrock AgentCore  
*Process:* Process 1 — Retail purchase  
*Tools:* 8

| Tool | Signature | Source / behaviour | Proactive |
|---|---|---|---|
| `compute_equity` | `compute_equity(valuation, payoff)` | Internal |  |
| `estimate_payment_delta` | `estimate_payment_delta(current, target, equity, offer)` | F&I connector |  |
| `find_upgrade_prospects` | `find_upgrade_prospects(criteria)` | Customers + DMS service history (existing prospecting logic) |  |
| `get_loan_lease_status` | `get_loan_lease_status(customer_id)` | F&I / DMS connector |  |
| `get_trade_valuation` | `get_trade_valuation(vin, mileage, condition, zip5)` | Trade-In Valuation API connector (Cox vAuto/KBB, Black Book); VIN decode via NHTSA vPIC (free) in sandbox; sandbox fake values |  |
| `monitor_equity` | `monitor_equity(vin \| batch)` | Nightly over `DEALER_Vehicles` and on `ro.opened` (fresh mileage), `inspection.submitted` (condition), `service.visit.completed`, market-value refresh; writes `equity_snapshot` to `DEALER_Vehicles`; emits `equity.crossed_positive`, `lease.end_window_entered` (≤ 6 months), `repair_vs_replace_triggered` (estimate ≥ `repair_vs_replace_threshold_pct`) | yes |
| `recommend_acquisition` | `recommend_acquisition(vin, store_stock_need)` | Compares a service-drive or trade vehicle against the store's used-inventory demand (A1 shortlist counts, days supply by model) and emits `acquisition.recommended` with a suggested offer range to the Inventory & Trade Desk (default autonomy `recommend`) | yes |
| `record_trade` | `record_trade(journey_id, trade)` | Advances TRADE_IN |  |

## A5 — Campaign ROI Optimizer

*Delivery model:* Amazon Quick / QuickSight analytics + AgentCore for recommendations  
*Process:* Process 1 — Retail purchase  
*Tools:* 9

| Tool | Signature | Source / behaviour | Proactive |
|---|---|---|---|
| `ask_quick` | `ask_quick(question)` | Amazon Quick APIs |  |
| `attribute_conversions` | `attribute_conversions(model = last_touch\|linear\|position)` | Internal |  |
| `create_quick_dashboard` | `create_quick_dashboard(spec)` | Amazon Quick APIs |  |
| `get_campaign_spend` | `get_campaign_spend(range, platform)` | Ad Platforms connector (Adobe, Google, Meta) |  |
| `get_journey_funnel` | `get_journey_funnel(range, dims)` | Athena/Quick over S3 lake |  |
| `get_personalize_segments` | `get_personalize_segments()` | Personalize (User-Segmentation) for audience recommendations |  |
| `recommend_budget_shift` | `recommend_budget_shift(constraints)` | Bedrock reasoning over ROI table |  |
| `refresh_audiences` | `refresh_audiences()` | Rebuilds Personalize segments from journey outcomes and pushes to the ad-platform connectors as audience *drafts* (marketing analyst activates) | yes |
| `run_nightly_attribution` | `run_nightly_attribution()` | Scheduled: recomputes attribution over the lake, refreshes the Campaign ROI dashboard, and when `recommend_budget_shift` projects lift ≥ threshold creates an `DEALER_Approvals` request (type *budget shift*) with the evidence attached — `act_with_approval`, never applies spend | yes |

## A6 — OEM Sales Administrator

*Delivery model:* Amazon Quick (agent + Flows + Index + Research)  
*Process:* Process 1 — Retail purchase  
*Tools:* 32

| Tool | Signature | Source / behaviour | Proactive |
|---|---|---|---|
| `analyze_performance_gaps` | `analyze_performance_gaps(account_id \| district)` | Bedrock over RDR/composite Ranks gaps vs objective, PY, peer; explains drivers with evidence |  |
| `ask` | `ask(question, scope)` | Quick agent over Quick Index (CRM notes, C&I bulletins, market reports, past plans, contact reports) The right-hand chat panel; every answer guardrail-checked and cited |  |
| `build_meeting_package` | `build_meeting_package(type, scope, period)` | Quick Flow → PPTX/PDF in S3 Types: dealer/district/area/national month-end close, QBR, new-model launch prep, EV readiness, CSI action, fixed-ops monthly/quarterly, parts report |  |
| `check_guardrails` | `check_guardrails(text, context)` | Bedrock Guardrails + rules Lambda GR-001…GR-008; logs to `OEM_GuardrailEvents` |  |
| `compute_composite` | `compute_composite(account_id, peer_group)` | RDR lake + DMS + CSI feed Composite score across sales efficiency, market penetration, customer experience, facility, digital lead conversion, gross/unit, CSI; peer = same-tier dealers in region |  |
| `consolidate_comms` | `consolidate_comms(dealer_id)` | Quick Index over Sales Ops announcements + SES AI search over bulletins; per-dealer digest; forward with comments |  |
| `create_contact_report` | `create_contact_report(dealer_id, type, topics, summary_or_audio, followups)` | Quick Flow + Transcribe (dictation) Structures the report, extracts follow-ups with deadlines, writes CRM activity + `DEALER_Tasks`, suggests next-visit focus |  |
| `detect_alerts` | `detect_alerts(district)` | Rules + Bedrock pattern detection Emits HIGH/MEDIUM/LOW alerts (e.g., CSI 3-month decline, days supply > target, lead response SLA miss, compliance lapse) with required action + timeframe → `OEM_Alerts` |  |
| `forward_comms` | `forward_comms(dealer_id, items[], dm_comments)` | Quick Index over Sales Ops announcements + SES AI search over bulletins; per-dealer digest; forward with comments |  |
| `generate_account_plan` | `generate_account_plan(account, actuals, market, incentives, research)` | Quick Flow Structured document with citations to every number |  |
| `generate_action_plan` | `generate_action_plan(account_id, issue)` | Quick Flow Milestones, owners, due dates, success metric; stored as `OEM_AccountPlans` type `action_plan` |  |
| `generate_opportunity_brief` | `generate_opportunity_brief(opportunity_id)` | Quick Flow |  |
| `generate_win_strategy` | `generate_win_strategy(account_id, competitor_set)` | Quick Flow |  |
| `get_account` | `get_account(account_id)` | CRM connector (Salesforce accounts, contacts, opportunities) |  |
| `get_competitive_offers` | `get_competitive_offers(pma)` | Market-intel + ads connectors Competitor dealer offers (APR / lease / cash), est. spend, impressions vs our advertised offer |  |
| `get_compliance` | `get_compliance(account_id)` | Facility / franchise compliance feed (sandbox `OEM_Compliance`) Fully / conditional / non-compliant by requirement |  |
| `get_ev_readiness` | `get_ev_readiness(account_id)` | DMS + training LMS feed + facility system Certification, training %, chargers installed, signage, EV units MTD, EV % of sales |  |
| `get_incentive_programs` | `get_incentive_programs(account_id)` | C&I incentive system connector Enrolled programs, utilization, cost per unit; what-if lift estimate |  |
| `get_inventory_position` | `get_inventory_position(account_id)` | Dealer Inventory connector (VIN-level) Days supply, aging, model mix vs. demand |  |
| `get_market_forecast` | `get_market_forecast(segment, region)` | S&P Global Mobility connector Segment demand outlook used in win strategy |  |
| `get_market_registrations` | `get_market_registrations(pma, range)` | Market intelligence connector (R.L. Polk / S&P Global Mobility) Segment share, competitor registrations in the account's PMA; sandbox = synthetic |  |
| `get_rdr_actuals` | `get_rdr_actuals(account_id, range, by_model)` | RDR / sales data lake (Athena) Retail deliveries, fleet units, trend |  |
| `list_accounts` | `list_accounts(region, district)` | CRM connector (Salesforce accounts, contacts, opportunities) |  |
| `predict_weekend` | `predict_weekend(district, weekend)` | DMS deliveries + CRM appointments, PY weekend Booked vs projected delivered per dealer; Monday appointment carry-over |  |
| `publish_plan` | `publish_plan(plan, reviewers[])` | `OEM_AccountPlans` + CRM attach + email Versioned; review status |  |
| `rank_reports` | `rank_reports(district_priorities)` | Quick Index over report catalog Report Library ordering |  |
| `research_account` | `research_account(account_name)` | Quick Research External signals: news, expansions, ownership changes, competitor openings; each finding cited |  |
| `run_district_cycle` | `run_district_cycle(district_id)` | Weekly scheduler (Mon 05:00 local) + on `rdr.updated` Refreshes composites, runs `detect_alerts`, drafts action plans for dealers below threshold, assembles the meeting package for every dealer visit on the DM's calendar in the next 7 days, rolls up to Region View; everything lands as *drafts for review* (`act_with_approval`) | yes |
| `search_comms` | `search_comms(query)` | Quick Index over Sales Ops announcements + SES AI search over bulletins; per-dealer digest; forward with comments |  |
| `simulate_incentive_mix` | `simulate_incentive_mix(programs, budget)` | C&I incentive system connector Enrolled programs, utilization, cost per unit; what-if lift estimate |  |
| `simulate_scenario` | `simulate_scenario(account_id \| district, levers {allocation_pct, incentive_per_unit, days_supply_delta, market_growth_pct})` | Deterministic elasticity model + Bedrock narrative Projected volume, objective %, share, gross, days supply with deltas; labeled *estimate*; GR-001/GR-005 checked |  |
| `track_milestones` | `track_milestones()` | Daily Raises `action_plan.milestone_due` and overdue tasks for dealer-owned milestones; drafts the follow-up note | yes |

## A7 — Service Operations (service-journey supervisor)

*Delivery model:* Custom agent on Amazon Bedrock AgentCore  
*Process:* Process 2 — Service & ownership  
*Tools:* 25

| Tool | Signature | Source / behaviour | Proactive |
|---|---|---|---|
| `auto_present_estimate` | `auto_present_estimate(ro_id)` | `build_estimate`, `PLAT_ConfigAutonomy.constraints.max_amount` For maintenance-schedule and recall lines under the dealer's threshold, presents to the customer without waiting for the advisor; everything else waits for advisor review. Customer decision remains the customer's | yes |
| `book_service` | `book_service(customer_id, vin, services[], preferred_slots, wait_type)` | Scheduling connector; `DEALER_Appointments` (type=service); **loaner / mobile-unit availability** from the `scheduling` connector (`get_loaner_availability`, `get_mobile_slots`) Creates the service journey in BOOK; offers only wait types with capacity on that slot (loaner count, mobile van, pickup-delivery driver); `express` service class (no MPI, fixed menu) bookable separately; proactive variant triggered by recall / connected-car alert / `predict_next_service` |  |
| `build_estimate` | `build_estimate(ro_id, lines[])` | Internal + AgentCore Code Interpreter (AR-2) Totals, tax per `market_config`, good-better-best bundles; returns inputs + code + result |  |
| `check_coverage` | `check_coverage(vin, line)` | Warranty / recall connector; `DEALER_FIProducts` + `DEALER_Deals` for **prepaid maintenance and VSC sold in F&I** Warranty, recall, service contract (incl. prepaid maintenance plans sold by this store), goodwill eligibility per line; coverage type drives **RO line type** `customer_pay \| warranty \| internal \| sublet` (internal = used-car recon / PDI, linked to `DEALER_TradeDispositions`; sublet = outside vendor line with vendor + cost) |  |
| `dispatch_ro` | `dispatch_ro(dealer_id, date \| ro_id)` | Technician roster + skills/certifications (EV high-voltage, diesel, diagnostics), clocked capacity, promised times, parts availability, `DEALER_RepairOrders` 05:00 shop-load run assigns today's booked ROs to technicians and sequences them; re-dispatches on `parts.backordered`, walk-ins, or technician absence. Foreman can override; overrides are recommendations rows so the model learns. Default `act_and_notify` | yes |
| `explain_finding` | `explain_finding(line_id, audience)` | Bedrock over finding + evidence + procedure excerpt Customer-safe explanation; cites evidence and procedure |  |
| `get_labor_time` | `get_labor_time(vin, labor_op)` | Labor-time guide connector (`DEALER_LaborOps` sandbox) Hours + guide edition as `source_ref` |  |
| `get_repair_procedure` | `get_repair_procedure(vin, labor_op)` | Quick Index (procedures, TSBs) via Gateway Cited excerpts only |  |
| `get_service_history` | `get_service_history(vin)` | `DEALER_RepairOrders`, `DEALER_Inspections`, `DEALER_DeclinedWork`; DMS backfill Read model for advisor, technician, My Garage |  |
| `handoff_to_sales` | `handoff_to_sales(service_journey_id, reason, evidence)` | A4 `compute_equity`, A2 `create_journey(source=service_lane)` Creates purchase journey in DISCOVER with service context attached; creates `DEALER_Handoffs` row and advisor task |  |
| `lookup_dtc` | `lookup_dtc(codes[], vin)` | DTC / repair-procedure connector (`DEALER_DTCReference` in sandbox) Returns description, likely causes, related TSBs (Quick Index), recommended tests |  |
| `lookup_parts` | `lookup_parts(vin, labor_op \| part_nos[])` | Parts catalog + parts inventory connector (`DEALER_Parts`) On hand / ordered / ETA / superseded part numbers |  |
| `notify_ready` | `notify_ready(ro_id)` | OutreachProvider; DMS (`close_ro`, invoice) Shares invoice to My Garage Documents; schedules pickup/delivery; advances READY |  |
| `open_repair_order` | `open_repair_order(service_journey_id, mileage, concerns[], photos[], approval_prefs)` | DMS connector (`create_ro` / `get_ro`) Mirrors `ro_id` into `DEALER_RepairOrders`; advances CHECK_IN |  |
| `predict_next_service` | `predict_next_service(vin)` | DMS history + OEM maintenance schedule + mileage trend Returns due items and a date window with `source_ref` to the schedule row |  |
| `prepare_ro_brief` | `prepare_ro_brief(ro_id)` | History, declined work, recalls, coverage, A3 upsell brief, A4 equity Builds the advisor's check-in brief and pre-drafts estimate lines from known items at BOOK / 05:00, not at check-in | yes |
| `present_estimate` | `present_estimate(estimate_id, channel)` | OutreachProvider (magic link to My Garage), advisor view Advances APPROVE; respects `CUST_Consent` |  |
| `prioritize_findings` | `prioritize_findings(inspection_id)` | Bedrock over findings + history + warranty Produces urgency ranking and plain-language explanation; never invents a finding not in the inspection |  |
| `record_approval` | `record_approval(estimate_id, decisions[])` | Internal; DMS connector (`update_ro_lines`) Approved lines dispatched; declined/deferred lines written to `DEALER_DeclinedWork` |  |
| `record_inspection` | `record_inspection(ro_id, template_id, items[])` | Inspection connector or native form → `DEALER_Inspections` Enforces every red/yellow item has evidence; advances INSPECT |  |
| `reforecast_promise` | `reforecast_promise(ro_id)` | Line status, technician clock, parts ETA, shop load Recomputes projected ready time on every line / parts event; if projected > promised, messages the customer with the new time and reason *before* the promise is missed (respecting consent) and flags the advisor. Default `act_and_notify` | yes |
| `schedule_declined_followup` | `schedule_declined_followup(declined_work_id, cadence)` | A2 `plan_nurture` (service templates) Never a sales message; re-presents the finding with original evidence |  |
| `stage_parts` | `stage_parts(appointment_id \| date)` | `predict_next_service`, declined work, open recalls, `lookup_parts`, `DEALER_PartsOrders` Pre-pulls on-hand parts and pre-orders missing ones for booked work above a confidence threshold so the RO is not held for parts; ties orders to the appointment; cancels if the customer no-shows. Default `act_and_notify` with a dealer-set daily spend cap | yes |
| `track_parts` | `track_parts(ro_id)` | Parts connector; `DEALER_PartsOrders` Flags `parts_hold`, updates promised time, notifies advisor and customer |  |
| `update_line_status` | `update_line_status(ro_id, line_id, status)` | DMS connector / technician tablet in_progress → complete → qc_passed; advances REPAIR |  |

## Shared

| Tool | Signature | Source / behaviour |
|---|---|---|
| `write_trace` | `write_trace(journey_id \| service_journey_id, span)` | `PLAT_AgentTraces`; powers the AI Log; carries `correlation_id`, autonomy level in force, and tool citations |
