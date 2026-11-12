# Roadmap

Two tracks. **Publish** skills encode domain methodology from the Intelligent Sales & Service Orchestration spec (Retail Ops) that any automotive agent benefits from. **Build** skills encode the non-obvious implementation patterns *our own* coding agents need to build Retail Ops; they stay in this repo under `skills-build/` and are candidates to upstream to [aws/agent-toolkit-for-aws](https://github.com/aws/agent-toolkit-for-aws) where generic.

Landscape check (2026-09-23): no public Agent Skills collection covers automotive **retail or fixed operations**; the only "automotive" skills repo on GitHub is embedded/ADAS/AUTOSAR engineering. AWS already ships generic build skills (`aws-core`, `aws-agents`, `aws-data-analytics` in agent-toolkit-for-aws; Strands `AgentSkills` plugin; AgentCore harness skill catalog), so we do not duplicate those. See `docs/LANDSCAPE.md`.

## Track 1 — Publish (domain skills)

Priority by gap value: P0 = base models get it wrong and it has compliance or money consequences; P1 = methodology the spec defines that is not written down elsewhere; P2 = useful but partly known.

| Pri | Skill | Category | Scope | Primary tools | Spec |
|---|---|---|---|---|---|
| ✅ | `service-estimate-integrity` | reasoning | shipped | | §3.7 |
| ✅ | `multi-point-inspection-triage` | reasoning | shipped | | §3.7 |
| ✅ | `ev-incentive-eligibility` | reasoning | shipped | | §2.5 |
| ✅ | `trade-equity-and-upgrade-timing` | reasoning | shipped | | §3.4 |
| ✅ | `oem-field-sales-guardrails` | reasoning | shipped | | §3.6 |
| ✅ | `agentic-delivery-model-selection` | reasoning | shipped | | §2.2.2 |
| P0 | `dealer-advertising-and-price-transparency` *(new from research)* | reasoning | What an agent may put in a price, payment, or listing after the CARS Rule was vacated (5th Cir. 2025-01-27) and withdrawn (91 FR 6507, 2026-02-12) but FTC Act §5 enforcement resumed (97 warning letters 2026-03-13): all mandatory dealer charges in the headline price, conditional discounts not as the primary price, rebates only if the reader qualifies, sold vehicles removed promptly, financing-contingent terms disclosed up front; state add-on rules | `price_build`, `compose_message`, `structure_deal` | §3.1, §3.2 |
| P0 | `deal-structuring-and-disclosure` | reasoning | Payment scenarios, state tax and doc-fee caps, TILA / Reg Z disclosure text, F&I menu presentation, credit-compliance gate (OFAC, Red Flags, ECOA adverse action) as a handoff block, Safeguards Rule data handling in F&I | `structure_deal`, `push_deal_to_dms`, `validate_handoff` | §3.3, §5.13 |
| P0 | `lead-response-and-nurture-policy` | reasoning | Four-stage funnel, 2-minute first response, ≤ 2 touches/week, quiet hours, consent by channel (TCPA including the one-to-one consent status, CAN-SPAM, GDPR for `de`), stop conditions, human handoff on credit / legal / recall | `plan_nurture`, `compose_message`, `handoff_to_human`, `select_next_best_action` | §3.2 |
| P1 | `service-morning-work` | reasoning | 05:00 shop-load: dispatch by skill, certification, capacity, promised time, parts ETA; parts staging (on-hand = 0, confidence ≥ 0.8, daily cap); briefs; promised-time re-forecast, customer told first | `dispatch_ro`, `stage_parts`, `prepare_ro_brief`, `reforecast_promise` | §3.7, §4.10.2 |
| P1 | `service-retention-and-declined-work` | reasoning | Retention by vehicle age (54% in-warranty), 30/60/90 cadence, service vs sales templates, recall proactive booking, loaner / mobile capacity | `schedule_declined_followup`, `book_service`, `predict_next_service` | §3.7, §1.3 |
| P1 | `objection-handling-on-the-floor` | reasoning | Taxonomy incl. service objections (repair cost, can it wait, second opinion, warranty dispute); sub-2 s contract; success rates from outcomes | `classify_objection`, `get_objection_strategy`, `record_outcome` | §3.3 |
| P1 | `dealer-account-plan-generation` | reasoning | Composite inputs, gap analysis, milestones, meeting package; every figure cited; drafts only, DM publishes | `generate_account_plan`, `compute_composite`, `analyze_performance_gaps` | §3.6 |
| P1 | `campaign-attribution-to-delivery` | reasoning | Attribution models, delivered-vehicle join, budget shift as approval, never auto-spend | `attribute_conversions`, `recommend_budget_shift` | §3.5 |
| P1 | `autonomy-ladder-and-hard-rules` | reasoning | Action registry, four levels, per-dealer scope, observation fortnight, constraints, non-configurable rules | `autonomy_level` | §4.10.1 |
| P1 | `two-supervisor-orchestration` | reasoning | One supervisor per state machine, specialists as tools, supervisors meet through events, handoff validation, journey supersession | `create_journey`, `validate_handoff`, `handoff_to_sales` | §2.1, §2.1.1 |
| P2 | `ev-vs-ice-tco` | reasoning | Regional prices, market units, active incentives only, announced separated | `build_tco_comparison` | §3.1 |
| P2 | `ev-battery-health-communication` *(new from research)* | reasoning | Reporting SoH with method and warranty threshold (commonly 70% / 8 yr / 100k mi), degradation vs defect, charge-port and thermal findings, what not to say | `record_inspection`, `explain_finding` | §3.7 |
| P2 | `technician-knowledge-capture` *(new from research)* | reasoning | Turning senior-technician diagnostic notes into reusable, cited findings and TSB links as the technician workforce retires; what counts as evidence vs anecdote | `lookup_dtc`, `get_repair_procedure` | §3.7 |
| P2 | `open-data-vehicle-anchors` | pipeline | Synthetic catalogs and anchors from EPA / AFDC / EIA / KBA / EEA / BNetzA / Eurostat with `source_ref` | seed generator | §2.5.1 |
| P2 | `market-intelligence-from-open-data` | pipeline | Registration and share proxies, PMA construction, allow-listed competitor offers | connectors | §2.5.1 |
| P2 | `repair-order-dms-mirroring` | pipeline | RO create / update / close / invoice against DMS connectors; idempotency; system-of-record rules | `open_repair_order`, `update_line_status` | §4.1 |
| P2 | `agent-outcome-loop` | pipeline | Recommendation logging, context hashing, ranking from aggregates, hashed lake export | recommendations writer | §4.10.3 |

## Track 2 — Build (skills for our own coding agents)

Reuse first: install `aws-core`, `aws-agents`, and `aws-data-analytics` from agent-toolkit-for-aws for CDK / CloudFormation, Bedrock, AgentCore Runtime / Gateway / Memory / harness, Strands, Glue / Athena. Write only what those do not cover: Retail Ops-specific patterns and the seams between services.

| Pri | Skill | Category | What it teaches the coding agent | Why AWS toolkit does not cover it |
|---|---|---|---|---|
| P0 | `app-connector-pattern` | pipeline | `Connector` interface with `SandboxConnector` (DynamoDB-seeded) and `LiveConnector` (Secrets Manager creds), `INTEGRATION_MODE` switch, boundary id translation via `PLAT_EntityMap`, DMS as system of record | Project convention |
| P0 | `app-journey-state-machines` | pipeline | Purchase and service state machines, OWN as resting state, `previous_journey_id` / `superseded_by`, `validate_handoff` gates incl. credit compliance, event write before every transition | Project convention |
| P0 | `app-autonomy-and-recommendations` | pipeline | `autonomy_level()` decorator, action registry JSON, `PLAT_ConfigAutonomy` versions with `effective_from`, recommendations writer (shown / accepted / converted), hard-rule exclusion list enforced in the editor | Novel pattern |
| P0 | `cognito-native-plus-federate-oidc` | pipeline | Hosted UI with `identity_provider=CorpDirectory`, auth-code exchange, pre-token-generation Lambda mapping federated users → groups from `PLAT_Users`, 8 h vs 1 h sessions, callback URLs per CloudFront app; `--federate` bootstrap flag | Toolkit covers Cognito generically, not the Federate + group-mapping shape |
| P0 | `quick-embedding-with-shell-navigation` | pipeline | Identity Center SAML from Cognito, JIT provisioning into role-based Quick sharing groups, registered-user embed URL, `postMessage` navigation using the resource-URL contract, correlation id through the embed | Combines four docs into one working seam |
| P1 | `agentcore-gateway-to-quick` | pipeline | Two patterns: (a) Gateway Lambda target calling the A6 Quick agent / Flow APIs with caller scope and citations; (b) Quick **Index** retrieval as an AgentCore tool for A3 and A7. How A2 pulls A6 content and how A3/A7 ground on indexed documents | Toolkit covers Gateway, not the Quick bridge or Index-as-tool |
| P1 | `event-driven-agent-triggers` | pipeline | EventBridge rules R7–R9, Schema Registry versioning, idempotency keys, DLQ per subscriber, Scheduler rules (nurture 15 min, service hourly, 05:00 local shop-load per dealer time zone, nightly, weekly Mon 05:00), Streams bridge | Project-specific rule set |
| P1 | `strands-agent-app-conventions` | pipeline | Agent container layout, `market_config` in every payload, `write_trace` contract, `source_ref` on every figure, Code Interpreter for money math with inputs + code + result retained, Bedrock Guardrails attachment per agent | Extends `aws-agents` with our conventions |
| P1 | `synthetic-seed-from-open-data` | pipeline | Generator layout, `SEED_NOW` time-relative dates, deterministic RNG, storylines J1–J10 / S1–S8, `source_ref` propagation, reserved-WMI VINs, validation invariants | Project-specific |
| ✅ | `skill-eval-harness` | pipeline | Pairwise with/without-skill evaluation on a prompt pack, blind judge, per-dimension deltas (correctness, critical thinking, coherence), review HTML; modelled on hcls-agent-skills `eval/` and adewale/skill-eval-harness | Needed before this repo claims any win rate |
| P2 | `role-field-masking-at-the-api` | pipeline | Lambda authorizer scope resolution, field-level masking by role on `/service/ros/*`, tests that a technician token receives no contact fields | Small but easy to get wrong in the UI instead |
| P2 | `dms-ro-connector-cdk-reynolds` | pipeline | RO create / line update / close / invoice call shapes, idempotency, retry, mirror table | Vendor-specific; sandbox first |

Build skills live in `skills-build/` (not installed by `install.sh --target kiro` by default; use `--include-build`). **Shipped:** `skill-eval-harness` (with the working `eval/` harness and 33-prompt pack, 2026-09-23). Generic ones (`skill-eval-harness`, `quick-embedding-with-shell-navigation`, `cognito-native-plus-federate-oidc`) are candidates to propose upstream once proven.
