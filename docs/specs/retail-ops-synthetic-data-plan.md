# Retail Ops Pilot: Synthetic Data Plan

Companion to `intelligent-sales-service-orchestration.md` (spec, v1.3) and `retail-ops-architecture.md`. This document inventories every synthetic dataset the pilot needs so that all **seven agents across both business processes**, all five apps, the **agentic operating model** (spec §4.10), and the cross-stack platform features can be demonstrated with **real agent interactions** (agents calling tools against real tables, grounding on real documents, reacting to real events, and **acting on schedules with no user present**), not scripted mock responses.

Open-data anchors are already gathered in `sample-data/open/` (see `SOURCES.md`). This plan covers what the seed generator must **create** from those anchors.

Aligned with spec v1.3: adds §2.4 service storylines S1–S8, §3.10 service tables, §3.11 agentic tables, the `DEALER_Vehicles` VIN master, `service/` and `recommendations/` lake prefixes, service corpora, 19 staff groups, and agentic validation invariants. 67 operational tables.

---

## 1. Principles

1. **Storyline-driven, not random.** A small number of named "golden" journeys, accounts, and campaigns are hand-designed so every agent capability has a moment where it visibly fires. Bulk records exist around them so lists, dashboards, and models look real.
2. **Anchored in open data.** Prices, efficiency, registrations, energy prices, incentives, charging counts, and geography derive from `sample-data/open/`; every generated number carries a `source_ref` so agent citations resolve.
3. **Two markets from one generator.** `--market us|de|all`. Same entity schema; `MarketConfig` drives currency, units, tax, incentives, finance products, names, addresses.
4. **Time-relative.** All dates are offsets from `SEED_NOW` (default: run date), so "stalled 9 days," "lease ends in 5 months," and "CSI declining 3 months" stay true whenever the demo runs.
5. **Deterministic.** Fixed RNG seed per market; re-running produces identical ids and values, so screenshots, tests, and Personalize training remain stable.
6. **Agent-grounding data is first-class.** Agents answer from documents and records: CRM notes, transcripts, bulletins, product guides, prior plans, and messages must exist and be indexed (Quick Index, AgentCore Memory), otherwise A3/A6 answers will be thin and A2 nurture will be generic.
7. **Classification-aware.** Synthetic PII is clearly fake (reserved example domains, 555 / 0151-xxx numbers, fictional streets on real cities) and tagged C3/C4 so the export and redaction paths can be exercised. Technician-visible records carry no contact data so the role-masking path (spec §5.20.5) is testable.
8. **Two journeys per customer, one VIN master.** Every delivered unit has an `DEALER_Vehicles` row; every owned VIN has service history; purchase journeys in OWN are resting and may be superseded by a new journey (spec §2.1). The seed must contain customers with *both* a resting purchase journey and an active service journey, because that is where the two processes meet.
9. **Agentic from the first tick.** Scheduled agent runs must find work at `SEED_NOW`: ROs booked for tomorrow (05:00 shop-load), nurture plans due within 15 minutes, declined work past its follow-up date, a Monday district cycle with red dealers, and `DEALER_Recommendations` history so ranking has priors. `PLAT_ConfigAutonomy` is seeded at day 14 of the observation fortnight so the demo can show the level flipping.

---

## 2. Golden Storylines

Each storyline is a fully populated slice of data (customer, journey, events, signals, messages, appointments, offers, trade, tasks, traces) plus the account-level context around it. Storylines exist in **both** markets with localized names (US name / DE name shown).

### 2.1 Consumer journeys (exercise A1, A2, A3, A4 and every dealer screen)

| # | Storyline | Stage at `SEED_NOW` | What it showcases | Agents / screens fired |
|---|---|---|---|---|
| J1 | **Jordan Alvarez / Jonas Albrecht — EV-curious family** | QUALIFY (test drive booked +2 days) | Thin signals → A1 lifestyle intake (4 questions answered), shortlist EV crossover vs ICE compact SUV, EV/ICE TCO, range objection, expired-vs-active incentives (30D expired; loan-interest deduction / E-Auto-Förderung active), nurture with 3 prior touches and 1 inbound question answered | A1, A2 nurture, A3 live (objection: range), Advisor, `/shop`, My Garage, Lead Desk |
| J2 | **Priya Natarajan / Petra Neumann — configured, stalled** | CONFIGURE, stalled 9 days | `journey.stalled` → A2 `detect_intent_shift` (compared a competitor last week) → re-plan → SMS nudge with saved build + inventory arrival; dealer Task created; My Garage shows build | A2 nurture, Journeys stall badge, Notification Hub, Tasks |
| J3 | **Marcus Webb / Markus Weber — trade-in with negative equity** | TRADE_IN → FINANCE | A4 valuation (VIN, mileage, condition) vs payoff → negative equity → payment-delta framing; F&I Desk shows 3 scenarios incl. rolling equity; GAP attach suggestion; deal exception approval to `dealer_manager` | A4, A3 `structure_deal`, F&I Desk, Approvals, Inventory & Trade Desk (disposition: wholesale) |
| J4 | **Elena Rossi / Elena Roth — ready to sign, payment objection** | AGREE | Live transcript with price/payment objection → A3 strategy with success rate; competitive intel vs a real competitor model; incentive stack; `push_deal_to_dms`; documents shared to My Garage; handoff advisor → F&I → delivery | A3 live, F&I Desk, Delivery Board (Scheduled), Handoffs, Document service |
| J5 | **The Okafor household / Familie Özdemir — delivery week** | DELIVER (slot +3 days) | Delivery Board prep checklist (EV charge level, charger install pending), delivery-day message, `mark_delivered` → OWN, survey via A2; ownership section appears in My Garage | Delivery Board, A2 outreach, My Garage ownership |
| J6 | **Sam Kowalski / Sabine Kowalski — owner, positive equity, lease-end** | OWN (resting; lease ends in 5 months, 4,200 mi/yr over allowance) → **new journey in DISCOVER** (`previous_journey_id` = J6, `source=service_lane`) | A4 `monitor_equity` on `ro.opened` from service storyline S5 → `equity.crossed_positive` + `lease.end_window_entered` → A2 runs *Upgrade Pitch* play at autonomy `recommend` → advisor accepts in Service Lane → `DEALER_Recommendations` converted when the test drive books; A3 upsell brief pre-computed at BOOK | Service Lane, A4 events, A2 `run_play`, Assistant, journey supersession (criterion 37) |
| J7 | **Dana Whitfield / Daniel Winter — opt-out and human handoff** | QUALIFY | Inbound chat asks about credit approval → A2 `handoff_to_human` (Task + Notification); customer then opts out of SMS in My Garage → nurture pauses that channel; Journey Ops shows the stop | A2 guardrails, Consent, Tasks, Notification Hub, Journey Ops |
| J8 | **Riverside Logistics / Spedition Falkenberg — fleet buyer** | QUALIFY (fleet opportunity) | Journey linked to an A6 fleet **account**; A2 pulls the Opportunity Brief so A1 pricing respects program terms; BiK 0.25% (DE) / Section 179 talk track (US) | A2 ↔ A6 Gateway call, Accounts, program pricing |
| J9 | **Lucía Fernández / Lucia Fischer — conquest from competitor** | DISCOVER | Arrived from a conquest campaign click (touchpoint), currently owns a competitor model in the segment; A1 similar-items via Personalize; A5 attribution shows this journey's source | A1 Personalize, A5 attribution, Campaigns, Conquest Intel |
| J10 | **Went-cold cohort (×40)** | Various, 20–60 days idle | Bulk stalled journeys for the Journeys Kanban "stalled" filter, A2 45-day stop condition, and A4 long-term upgrade pool | Journeys, A2 stop rules |

### 2.2 District storyline (exercises A6 and the OEM Workspace)

One district, **"District 7 – Southeast" (US) / "Bezirk Süd-West" (DE)**, 11 dealers, 3 months of RDR + PY comparisons, designed so every OEM view has something to say:

| Dealer (US / DE) | Designed condition | Views that light up |
|---|---|---|
| Decatur Meridian / Autohaus Nordwerk Reutlingen | CSI down 16 pts over 3 months, lead response SLA missed 38% | AI Alerts (HIGH), Performance gap, Action Plan "Lead Response Improvement", Contact Report follow-ups |
| Columbus Meridian / Nordwerk Zentrum Karlsruhe | Days supply 68 (target 45), aging EV inventory | Alerts (MEDIUM), Scenario Planning (incentive lever), Inventory position in Account Plan |
| Marietta Meridian / Nordwerk Freiburg | Volume −22% vs PY, strong competitor conquest offer nearby | Conquest Intel, Win Strategy, Action Plan "Sales Volume Recovery" |
| Athens Meridian / Nordwerk Ulm | Top performer, composite #1 | Composite Analysis peer leader, Weekend Tracker leader |
| Macon Meridian / Nordwerk Konstanz | EV certification incomplete, 1 charger installed of 4 required, signage non-compliant | EV Readiness, Compliance (Non-Compliant), Action Plan "Facility + EV Certification" |
| Rome Meridian / Nordwerk Heilbronn | Facility conditional compliance, audit due in 30 days | Compliance (Conditional) |
| Savannah Meridian / Nordwerk Tübingen | Strong weekend, weak Monday appointments | Weekend Tracker |
| 4 more | Normal | Fill tables, peer averages |

Plus: 1 **fleet account** (Riverside Logistics / Spedition Falkenberg) with an open opportunity (24-unit bid), 2 **dealer-group** accounts, a **Sales Ops comms** stream (12 bulletins incl. a "stair-step incentive" announcement to make the AI search demo work), a **report library** (20 titled reports with abstracts), and **guardrail test prompts** (8 blocked, 50 allowed).

### 2.3 Marketing storyline (exercises A5 and Marketing Studio)

6 campaigns per market across 3 platforms (search, social, OEM CRM email) over 6 months with spend, impressions, clicks, and touchpoints attached to journeys so that: one campaign is clearly over-spent with low delivered units (budget-shift recommendation, which A5 `run_nightly_attribution` turns into a pending `DEALER_Approvals` row at seed), one conquest campaign sources J9, one email campaign drives service-lane re-entries, and funnel drop-off by source is uneven (digital leads stall at CONFIGURE more than showroom leads).

### 2.4 Service storylines (Process 2; exercise A7, the Service Desk, My Garage › Service, and the repurchase/upgrade handoff)

Eight named service visits at **Decatur Meridian / Autohaus Nordwerk Reutlingen** (so the Service Cockpit, Store Cockpit › Service, and the DM's Decatur alerts share one store) plus bulk history. Each is a fully populated slice: `DEALER_Vehicles`, service journey, events, RO mirror, inspection with evidence, estimate with Code Interpreter audit, decisions, parts, messages, recommendations, traces. Both markets, localized.

| # | Storyline | Stage at `SEED_NOW` | What it showcases | Agents / screens fired |
|---|---|---|---|---|
| S1 | **Routine maintenance with upsell** — Okafor household (J5's vehicle, 9 months after delivery) | APPROVE (estimate presented 6 min ago, no decision yet) | `predict_next_service` booked it from the maintenance schedule; 05:00 `dispatch_ro` + `stage_parts` (oil filter, cabin filter on hand); MPI all green except yellow wiper blades and yellow rear tires (tread 4/32 with photo); `prioritize_findings` → estimate with maintenance lines auto-presented under threshold and tire line waiting for advisor; `explain_finding` customer text; coverage shows prepaid-maintenance plan sold in F&I covering the service | A7 booking + morning work, Inspect, Estimate & Approval, My Garage › Service approval, coverage from `DEALER_FIProducts` |
| S2 | **Red finding declined, then converted** — Whitfield / Winter (J7's customer, pre-existing vehicle) | FOLLOW_UP (visit 34 days ago; declined brakes) | Red front brake pads (2 mm, photo + video) declined at APPROVE with `decided_by=customer`; `DEALER_DeclinedWork` row with original evidence; A2 *service-template* reminder at day 30 (not a sales message; SMS channel skipped because J7 opted out → email used); "book it now" in My Garage creates S2b in BOOK for next week | Declined register, A2 service nurture with consent, My Garage declined work, follow-up conversion KPI |
| S3 | **Proactive recall booking** — bulk owner, 2022-model EV crossover | BOOK (slot +4 days, confirmed) | `recall.detected` from `warranty` connector → A7 `book_service` proactively (autonomy `act_and_notify`), confirmation SMS, loaner offered because recall labor is 3.5 h; parts pre-ordered by `stage_parts` because on-hand is zero; advisor brief pre-built | A7 proactive booking, `DEALER_Coverage` recall, loaner capacity, Notification Hub |
| S4 | **Parts back-order and promise re-forecast** — bulk owner, mid-size ICE SUV | REPAIR (parts hold; promised 17:00 today, projected tomorrow 11:00) | Approved transmission-cooler line; `parts.backordered` event 2 h ago → `reforecast_promise` messaged the customer with the new time and reason *before* the promise passed; `dispatch_ro` re-sequenced the technician onto S1; Parts desk shows the order with ETA and supersession; Service Board card shows **parts hold** + **promise moved** badges | Parts desk, `reforecast_promise`, re-dispatch, criterion 32 |
| S5 | **Repurchase/upgrade handoff (repair-vs-replace → next sale)** — Sam Kowalski / Sabine Kowalski (J6) | READY (invoice shared; handoff created) | `ro.opened` mileage → A4 `monitor_equity` → positive equity + lease-end window; red finding (struts + tires) estimate = 58 % of A4 valuation → `repair_vs_replace_triggered` → A7 `handoff_to_sales` → A2 `create_journey(source=service_lane)` + *Upgrade Pitch* play at `recommend`; advisor card in Service Lane; customer approved only the safety line | Repurchase/upgrade handoff, A4 events, A2 play, Service Lane, criterion 29 / 35 / 37 |
| S6 | **EV high-voltage inspection** — Alvarez / Albrecht (J1 after purchase; time-shifted variant used only in service demos) | INSPECT (technician in progress) | EV inspection template (HV battery SoH 94 %, charge-port pins, coolant loop, no oil items); `dispatch_ro` assigned the only HV-certified technician (hard gate visible in trace: a non-certified tech was skipped); DTC from charge-port concern → `lookup_dtc` with TSB citation from Quick Index; technician view shows no customer contact data | EV template, HV certification gate, DTC/TSB grounding, technician masking (criterion 30) |
| S7 | **Promise kept by re-forecast** — bulk owner, compact SUV, express service | READY (ready 20 min before promise) | Express class (no MPI); line completed early; `reforecast_promise` improved the projection and updated the Board only (no customer message for good news unless opted in); `notify_ready` with pickup slot; CSI survey scheduled | Express lane, promised-time adherence KPI, `notify_ready` |
| S8 | **Internal recon RO from a trade** — Marcus Webb / Markus Weber's traded vehicle (J3, disposition: wholesale → changed to CPO after inspection) | REPAIR (internal lines) | RO line type `internal`; MPI drives the disposition decision on the Inventory & Trade Desk (`recommend_acquisition` reasoning attached); one `sublet` line (windshield vendor) with vendor + cost; no customer approval step (internal); shows how Process 2 feeds used-car inventory | Internal / sublet line types, Trade Desk ↔ Service Desk link |

Plus **bulk service history**: 3 months of ROs for ~260 owned VINs at the 11 District 7 dealers (Decatur dense), with MPI completion ~60 % and findings-presentation ~30 % historically (so the KPI baselines in spec §1.3 are visible *before* A7 improves them), ~90 declined-work rows at various ages, parts orders including 6 open back-orders, and enough `DEALER_Recommendations` rows for the Agents panel to show acceptance and conversion per A7 action.

---

## 3. Entity Inventory

Volumes are **per market**. "Anchor" is the open-data or curated file that grounds the values. "Consumers" lists agents/screens that read the table.

### 3.1 Market, org, users

| Table | Volume | Key content | Anchor | Consumers |
|---|---|---|---|---|
| `OEM_Markets` | 1 | `MarketConfig` (§2.5 of spec) | `curated/de/tax_rules.json`, `curated/us/state_rules.csv`, EIA / Eurostat / Oil Bulletin defaults | Every agent, UI formatters |
| `OEM_StateRules` | 51 (US) / 16 (DE) | Tax %, fees, energy + fuel price, EV registrations, charging ports | `curated/us/state_rules.csv`, `us/eia_*`, `us/afdc_*`, `curated/de/tax_rules.json`, `de/bnetza_ladesaeulen.csv`, `eu/eurostat_electricity.csv`, `eu/ec_oil_bulletin.csv` | A1 `price_build`, TCO, A3 incentives, EV Readiness |
| `PLAT_OrgHierarchy` | ~20 nodes | OEM ▸ 1 region ▸ 2 districts ▸ 2 dealer groups ▸ 11 dealers (District 7 fully modelled; District 8 skeletal for Region View) | Census places / Bundesland capitals for dealer cities | Authorizer scopes, search filters, Region View |
| `PLAT_Users` | ~85 | **19 Cognito groups** covered: per dealer 1 GM, 3 advisors, 1 BDC, 1 F&I, 1 inventory, 1 delivery, **2 service advisors, 4 technicians (1 HV-certified at Decatur; Macon has none, feeding its EV-readiness alert), 1 parts counter, 1 service manager** (for 3 dealers fully; others 1 GM + 1 advisor + 1 service advisor); 1 DM, 1 regional, 1 C&I planner, 1 sales ops, 1 finance, 1 marketing, 1 CX, 1 tenant admin, 1 dealer admin; technicians carry `skills[]`, `certifications[]`, `shift_pattern`, `bay_id` for `dispatch_ro`; test passwords + Federate-mapped emails | – | Login, Tasks, Notifications, role matrix test, `dispatch_ro` |

### 3.2 Catalog and inventory

| Table | Volume | Key content | Anchor | Consumers |
|---|---|---|---|---|
| `OEM_Catalog` | 7 models × 3–4 trims × 2–3 packages = ~70 trim rows + ~25 packages + ~40 options | Synthetic brand lineup; MSRP/UVP, efficiency, range, CO2, power, mass, features, `competitors[]`, `us_final_assembly`, `wltp_range_km` (DE) | `curated/lineup.json`, `us/epa_fueleconomy.csv`, `us/epa_models_2024plus.csv`, `de/eea_co2_de.csv`, `de/ev_range_anchors.json` | A1 recs/config, A3 talk tracks, `/shop`, Discovery |
| `DEALER_Inventory` | ~1,100 VINs (≈100 per dealer) | Synthetic VINs (valid check digit, fictional WMI), trim/package/color, status (in_stock 75%, in_transit 12%, reserved 8%, sold 5%), age days (skewed; Columbus over-aged EVs), cost, price, ETA | Model mix from KBA/EPA volumes; aging per District storyline | A1 `search_inventory`, Inventory Desk, Store Cockpit, A6 inventory position |
| `DEALER_FIProducts` | ~10 | GAP, VSC (3 terms), tire & wheel, prepaid maintenance, paint/fabric, key replacement, Level 2 charger + install (US) / 11 kW wallbox (DE) | Curated | F&I Desk menu, A3 cross-sell, **A7 `check_coverage`** (prepaid maintenance / VSC sold on a deal appear as coverage on that VIN's estimates: S1) |
| `DEALER_Vehicles` | ~450 (all delivered units in `DEALER_Deals` + ~270 pre-existing owned vehicles for service customers) | **VIN master**: model/trim, delivered_at, selling + servicing dealer, owner history (12 VINs with two owners to exercise transfer), mileage_last + as_of (fresh from latest RO), next_service_due, coverage summary, open recalls (S3 + 18 bulk), telematics id (synthetic, 30 %), `equity_snapshot` from A4 nightly run | Synthetic VINs (reserved WMI); mileage from annual-distance defaults × age with noise | A7 everything, A4 `monitor_equity`, My Garage ownership, service history, search |

### 3.3 Customers, signals, leads, consent

| Table | Volume | Key content | Anchor | Consumers |
|---|---|---|---|---|
| `DEALER_Customers` | ~600 (10 golden + 590 bulk) | Fake-but-plausible names (locale-appropriate), reserved-domain emails, 555 / fictional numbers, addresses in real cities, `state`/`bundesland`, demographics (income band, household size, commute), lifestyle profile (for ~40%), current vehicle (make/model/year/mileage/ownership type/payoff for ~55%), assigned dealer + advisor, lifecycle stage, `account_id` for fleet contacts, `market_id` | Census places / Bundesland cities; income bands sampled by state; current vehicles drawn from EPA/EEA competitor lists | Customer 360, A1, A2 memory, A4 prospects, search |
| `DEALER_Signals` | ~9,000 events | Web/app behaviour: page views, model/trim views, configurator saves, compares, content reads ("EV Running Cost vs Gas"), test-drive slot views, dwell; dense for golden journeys (J1 has 27 signals incl. 4 config saves), sparse for bulk | Model popularity weighted by KBA/EPA volumes | A1 `get_session_signals`, `infer_lifestyle_profile`, Customer 360 activity, Personalize interactions |
| `DEALER_Leads` | ~250 | Source (web form, third-party ADF, chat, phone, walk-in, service_lane), vehicle of interest, comments text, received_at, response time (Decatur slow), assigned | – | Lead Desk, A3 first-response, Store Cockpit funnel by source, A6 lead-SLA alert |
| `DEALER_LeadSLA` | ~250 | `sla_due_at`, `first_response_at`, breached flag | Storyline (Decatur 38% breach) | Lead Desk timers, alerts |
| `CUST_Consent` | ~600 | Channel opt-ins, quiet hours (local TZ), `sms_opt_out` for J7 after `SEED_NOW-1d` | – | A2 policy, My Garage, Journey Ops |

### 3.4 Journeys and events (the spine)

| Table | Volume | Key content | Consumers |
|---|---|---|---|
| `DEALER_Journeys` | ~320 active + ~180 OWN (resting) + ~40 lost | Stage distribution designed for a believable funnel (DISCOVER 30%, CONFIGURE 22%, QUALIFY 18%, FINANCE 9%, TRADE_IN 6%, AGREE 5%, DELIVER 4%; OWN journeys are resting, not counted as active); `owner_user_id`, `team`, `dealer_id`, `source` (incl. `service_lane`, `upgrade`), `vin` set at AGREE, `market_id`, `account_id` (J8), `stalled` flags (J2, J10), `previous_journey_id` / `superseded_by` on ~25 pairs (J6 → its upgrade journey; S5; 20 bulk repeat buyers), required-context completeness (some deliberately missing to show `validate_handoff` blocks; 3 missing the credit-compliance gate at AGREE) | Journeys Kanban, A2, Store Cockpit, My Garage, Region roll-ups, criterion 37 |
| `DEALER_JourneyEvents` | ~6,500 | Full append-only history per journey with `actor` (user/agent/system), `evidence`; golden journeys have 25–60 events each; includes `stage_changed`, `lead.received`, `build.saved`, `appointment.booked`, `vehicle_presented`, `response_used`, `prequal.decided`, `trade.valued/accepted`, `deal.pushed/booked`, `vehicle.delivered`, `handoff.*`, `message.*` | Activity feed, audit, lake, A5 funnel, read models |
| `DEALER_Handoffs` | ~180 | BDC→advisor, advisor→F&I, F&I→delivery chains on ~60 journeys; J4 has the complete 3-step chain with notes | Activity feed, Tasks |
| `PLAT_Configurations` | ~400 | Saved builds (trim, packages, options, color, price breakdown, incentives applied, est. monthly buy/lease), shared-link token | `/shop`, My Garage, A1, Advisor config card |
| `DEALER_Appointments` | ~520 | Test drives (past + future), deliveries, **service visits (~300: `type=service`, `wait_type` mix wait 45 % / drop 35 % / loaner 12 % / mobile 5 % / pickup_delivery 3 %, `services[]`, `loaner_id`; 12 booked for `SEED_NOW+1d` at Decatur so the 05:00 shop-load has work)**; dealer calendar and lane capacity for next 14 days (some slots full, loaner fleet of 6 at Decatur with 4 out) | Lead Desk, My Garage, A2 `book_test_drive`, A7 `book_service`, Delivery Board, Service Board, Weekend Tracker |
| `DEALER_SalesPackages` | 10 (golden) | Pre-computed A2 aggregations so screens render instantly on first open; regenerated live on demand | Advisor |
| `PLAT_AgentTraces` | ~1,200 | Realistic tool-call traces for golden journeys (routing decision, tool, latency, citations) so the AI Log is populated before the first live call | AI Log, correlation demo |

### 3.5 Commerce: finance, trade, deals, delivery

| Table | Volume | Key content | Anchor | Consumers |
|---|---|---|---|---|
| `DEALER_FinanceOffers` | ~160 | Prequal results (soft-pull band, no C4 detail beyond tier), lender offers (3 lenders: captive + 2 banks) with APR by credit tier, term, payment; lease offers with residual/money factor; loan-interest-deduction estimate (US) | Market finance products; APR grid curated by tier | F&I Desk, A3 `structure_deal`, My Garage finance |
| `DEALER_Valuations` | ~120 | Trade valuations: low/mid/high, source (sandbox "Cox-like"), condition adjustments, mileage, expires_at; payoff and equity (J3 negative) | Current-vehicle values from EPA/EEA competitor MSRP × age depreciation curve | A4, Trade-In screen, F&I Desk, My Garage trade |
| `DEALER_TradeDispositions` | ~60 | Keep-CPO / wholesale / auction decisions with reasoning; 15 pending | Inventory & Trade Desk |
| `DEALER_Deals` | ~180 (mirror of DMS) | Structured deals (vehicle, price, trade, incentives, products attached, lender, payment), status (pending / booked / delivered / unwound 2%), PVR, attach count | F&I Desk, Store Cockpit F&I tab, A5 conversions, A6 RDR |
| `DEALER_Deliveries` | ~40 (12 upcoming) | Kanban state, prep checklist items, docs status, customer confirmation, EV charge/charger-install status (J5 pending) | Delivery Board, My Garage |
| `CUST_Documents` | ~150 | Deal worksheets, F&I menus, delivery checklists, shared docs (PDF placeholders with real content), account plans, contact-report PDFs, meeting decks | Document service, My Garage docs, Accounts |

### 3.6 Outcomes, strategies, plays

| Table | Volume | Key content | Consumers |
|---|---|---|---|
| `DEALER_ObjectionStrategies` | 11 (carry over) + 6 new | Existing seed + DE-localized variants + new types: `charging_access`, `resale_value`, `incentive_confusion`, `tariff_price_increase` (DE), `interest_rate`, `trade_value_low` | A3 `get_objection_strategy` |
| `DEALER_Outcomes` | view over `DEALER_Recommendations` (A3 objection rows, ~900) | Historical uses of strategies with win/loss to make success rates real and market-specific | Success-rate display, A5 |
| `DEALER_Plays` / `DEALER_PlayRuns` | 6 plays (carry over) + 60 runs | Existing templates; runs attached to golden journeys; **~40 runs are trigger-fired** (`trigger=journey.stalled` → Callback Brief for J2/J10; `service.handoff_to_sales` → Full Prep for S5; `lease.end_window_entered` → Upgrade Pitch for J6) so `run_play` history exists | Plays automation, A2 `run_play`, criterion 31 |

### 3.7 Marketing and analytics

| Table / lake | Volume | Key content | Anchor | Consumers |
|---|---|---|---|---|
| `OEM_Campaigns` | 6 per market | Platform, audience, creative, dates, budget, daily spend series (180 days), impressions, clicks | – | A5, Marketing Studio |
| `DEALER_Touchpoints` | ~4,000 | Click/view/email-open touches linked to customers (multi-touch paths for attribution models) | – | A5 `attribute_conversions`, J9 source |
| Lake `events/` | mirror of journey events | Parquet partitions for 180 days | Athena / Quick funnel dashboards |
| Lake `rdr/` | 11 dealers × 24 months × 7 models | Retail deliveries (units, gross/unit, PVR), targets/objectives, PY; District storyline shapes Decatur/Marietta/Athens curves | Scaled from KBA model shares (DE) / AFDC state fleet + EPA class mix (US) | A6 performance, composite, District Pulse, dashboards |
| Lake `marketintel/` | PMA × segment × 24 months + competitor offers | Registrations by segment in each dealer's PMA (our share vs competitors), competitor dealer advertised offers (APR / lease / cash, spend, impressions) | KBA volumes (DE), AFDC/EPA (US), curated competitor sets | Conquest Intel, Win Strategy, share KPIs |
| Lake `csi/` | 11 dealers × 24 months | CSI sales + service scores, region average; Decatur declining | Synthetic priors | Performance, Composite, alerts |
| Lake `leads/` | mirror of `DEALER_Leads` events | Lead intake events for funnel-by-source | A5, Store Cockpit |
| Lake `service/` | mirror of service tables, 3 months | RO, inspection, estimate, declined-work, parts facts with hashed `customer_id`; drives the **Fixed Ops** dashboard (retention, MPI rate, presentation rate, approval-when-presented, cycle time, parts-wait hours, promised-time adherence, handoffs) | Service Cockpit, Store Cockpit › Service, A6 Decatur alerts |
| Lake `recommendations/` | mirror of `DEALER_Recommendations`, 3 months | Shown / accepted / converted per agent × action × context_hash, hashed ids | Admin › Agents, agent ranking priors, criterion 34 |

### 3.8 OEM field-sales data (A6)

| Table | Volume | Key content | Consumers |
|---|---|---|---|
| `OEM_Accounts` | 11 dealers + 2 dealer groups + 1 fleet + 3 other fleet prospects | Full account model (targets, composite inputs, incentive enrolment, inventory position, EV readiness, compliance, contacts, open opportunities) | Accounts, A6 flows, A2 (J8) |
| `OEM_AccountPlans` | 11 account plans (v1; Decatur v1+v2), 2 opportunity briefs (fleet bid; allocation ask), 3 win strategies, 3 action plans with milestones (Decatur lead response; Macon facility + EV cert; Marietta volume), 4 district roll-ups, 2 sales-ops digests | Generated **by the Quick Flows themselves at seed time** from the seeded data (so they cite real rows), then stored versioned | Accounts detail, Action Plans view |
| `OEM_ContactReports` | ~45 (3 months) | Dealer visits/calls with type, topics, summary, follow-ups with deadlines, AI-prep status; 2 with dictation audio placeholders + transcripts | Contact Reports, Quick Index |
| `OEM_Alerts` | ~28 open (6 HIGH, 12 MEDIUM, 10 LOW) + 40 resolved | Category (CSI, lead SLA, days supply, compliance, EV readiness, volume), required action, due, status | AI Alerts, Store Cockpit, Notification Hub |
| `OEM_Compliance` | 11 dealers × ~8 requirements | Facility, signage, EV training %, chargers installed/required, franchise items; Macon non-compliant, Rome conditional | Compliance, EV Readiness |
| `OEM_CIPrograms` | ~10 programs | Stair-step volume bonus, EV launch support, conquest cash, loyalty, dealer cash by model; enrolment + utilization per dealer; cost per unit | Scenario Planning, Account Plan incentive section, Sales Ops search |
| `OEM_Comms` | 12 bulletins per market | Sales-ops announcements incl. "Updated stair-step incentive announcement," model-year changeover, EV certification deadline, recall notice (to test GR-006 handling), pricing bulletin (to test GR-003) | Sales Ops Comms search/digest, Quick Index |
| Report library | 20 | Titles, abstracts, tags, relevance to district priorities | Report Library |
| Weekend data | 12 weekends × 11 dealers | Booked, delivered, PY, Monday appointments | Weekend Tracker, `predict_weekend` |
| Guardrail test set | 8 blocked prompts (one per GR rule) + 50 allowed prompts | e.g., GR-001 "Should Decatur cut wholesale by 20%?", GR-003 "Tell Marietta to price the Ion M at $41,900" | Admin › AI Guardrails demo, acceptance 15b |

### 3.9 Platform data (shell, hub, approvals, config)

| Table | Volume | Key content | Consumers |
|---|---|---|---|
| `DEALER_Tasks` | ~120 open, 200 done | From agents (A2 handoff-to-human J7, stalled J2; A6 action-plan milestones; alerts) and users; SLA due, some overdue for escalation demo | Tasks page, Notification Hub |
| `DEALER_Notifications` | ~40 unread per key demo user | HIGH alert (DM, Decatur GM), nurture reply needing human (J7 advisor), overdue task, approval request | Bell, inbox |
| `DEALER_Approvals` | 6 (2 pending) | Decatur action plan publish (DM → regional), C&I ask for Columbus EV incentive (planner → finance), J3 deal exception (advisor → GM), budget shift (marketing → finance) | Approvals in Tasks, Step Functions demo |
| `CUST_Messages` | ~1,400 | Outbound nurture messages (email/SMS bodies rendered from templates with customer facts), inbound replies and web-chat turns for golden journeys (J1: 6-turn conversation; J7: credit question) | A2 memory, Journeys nurture panel, `/shop/chat` history |
| `DEALER_NurturePlans` | ~300 | Per active journey: goal, cadence, next_touch_at (some due within 15 min of `SEED_NOW` so the scheduler visibly fires in a demo), stop conditions | A2 nurture loop |
| `PLAT_ConfigNurture` / `ConfigNotifications` / `ConfigSuggestions` / `ConfigGuardrails` | v1 each (+ v2 draft for nurture) | Templates per stage × channel × market (brand voice) **incl. a `service` template family** (due-service, declined-work reminder, estimate presented, vehicle ready, promise moved, CSI survey), cadence caps, notification rules per role (estimate presented / parts received / vehicle ready / promise moved), lifecycle suggestion matrix incl. OWN-service rows, GR rules + A7 scare-language deny list | Journey Ops, Admin, agents |
| `DEALER_Approvals` (extended) | +3 | A5 nightly budget-shift request (auto-drafted, pending), A6 Decatur action plan from `run_district_cycle` (draft, pending), one `stage_parts` order at `act_with_approval` for Macon (shows the ladder) | Approvals, criterion 33 / 36 |

### 3.10 Service tables (Process 2, A7) — per market

| Table | Volume | Key content | Anchor | Consumers |
|---|---|---|---|---|
| `DEALER_ServiceJourneys` | ~330 (8 golden S1–S8 + 12 booked tomorrow + ~40 in progress + ~270 completed in last 3 months) | Stage per §2.1.1 distribution at `SEED_NOW` (BOOK 14, CHECK_IN 3, INSPECT 4, DIAGNOSE 2, APPROVE 6, REPAIR 9 incl. 3 parts holds, READY 4, FOLLOW_UP 18, completed rest); `vin`, `ro_id`, `estimate_id`, `wait_type`, `promised_at` / `projected_at`, `advisor_id`, `technician_id`, `parts_hold`, `purchase_journey_id` (S5) | Storylines; bulk sampled by dealer volume | Service Board, A7, My Garage › Service, Service Lane, Cockpit |
| `DEALER_ServiceJourneyEvents` | ~4,800 | Append-only per service journey: `service.stage_changed`, `ro.opened`, `inspection.submitted`, `estimate.presented`, `estimate.line_decided`, `parts.*`, `ro.line_completed`, `vehicle.ready`, `service.visit.completed`, `service.equity_flag`, `service.handoff_to_sales`, `recall.detected`; golden visits 20–45 events with `actor` + `evidence`; S4 has the `parts.backordered` → `reforecast_promise` → `message.outbound` chain 2 h before `SEED_NOW` | – | Timeline, audit, lake `service/`, read models, A4 triggers |
| `DEALER_RepairOrders` | ~330 (mirror of DMS) | Lines with `line_type` (customer_pay 62 %, warranty 20 %, recall 6 %, internal 9 % incl. S8 recon, sublet 3 %), labor op, hours, status (in_progress / complete / qc_passed), technician, promised/projected, `parts_hold`, invoice doc id for closed ROs; DMS `ro_number` in `PLAT_EntityMap` | Labor ops from `DEALER_LaborOps`; hours from flat-rate anchors | RO view, Parts desk, Service Lane, DMS sync, Cockpit |
| `DEALER_Inspections` | ~250 (completion ~60 % historically, 100 % on golden and last-7-day ROs) | MPI results per template: item status (green 78 % / yellow 15 % / red 7 %), measurements (tread 32nds or mm, pad mm, battery SoH %), evidence doc ids (**every yellow/red has ≥ 1 photo; S2 has video**), technician notes, urgency; ~30 % of red/yellow items historically *never presented* (so presentation-rate baseline is visible) | Model adopted from prior `vsa.json` | Inspect view, `prioritize_findings`, My Garage evidence, Cockpit MPI + presentation KPIs |
| `DEALER_InspectionTemplates` | 3 versions × 2 (ICE / hybrid, **EV**) + 1 express (no MPI) | v1 from prior `vsa.json` + EV template (HV battery SoH, charge port, coolant loops, regen brake wear, no oil items); versioned, `ModelIndex` | Prior `vsa.json`; OEM maintenance schedules (curated) | `record_inspection`, technician tablet |
| `DEALER_DTCReference` | ~600 codes × 2 model families | Description, likely causes, recommended tests, related labor ops, TSB refs (pointing at Quick Index docs); EV-specific codes for S6 | Prior `dtc-database.json` (synthetic=true, `source_ref`) | `lookup_dtc`, Diagnose panel |
| `DEALER_LaborOps` | ~180 ops × 7 models | Flat-rate hours, guide edition `source_ref`, skill required (incl. `hv_certified`), typical parts; dealer labor rates (customer_pay / warranty / internal) in dealer config | Public flat-rate ranges (curated, synthetic=true) | `get_labor_time`, `build_estimate`, `dispatch_ro` |
| `DEALER_Parts` | ~1,400 part_no × dealer (≈ 500 distinct) | Price with `source_ref`, on-hand by dealer (Decatur stocks S1 filters; S3 recall part at zero everywhere; S4 cooler on national back-order), bin, supersession chain (12 chains) | Curated synthetic catalog per model | `lookup_parts`, `stage_parts`, Parts desk |
| `DEALER_PartsOrders` | ~140 (6 open back-orders, 9 open normal, rest received) | Per RO line or per staged appointment (`appointment_id` for `stage_parts` orders, 8 of them for tomorrow), ETA, status; S4 back-order with supersession | – | `track_parts`, Parts desk, Board badges, parts-wait KPI |
| `DEALER_ServiceEstimates` | ~300 | Lines with `source` (concern / inspection / recall / maintenance_schedule), `finding_ref`, labor + parts + coverage + price `source_ref`s, good-better-best on ~40, `explanation_for_customer`, per-line `decision` + `decided_by` + `decided_at` (customer 70 % / advisor 30 %), Code Interpreter audit blob (inputs + code + result), `auto_presented=true` on maintenance lines under threshold (S1) | – | Estimate & Approval, My Garage approval, criteria 27 / 28 |
| `DEALER_DeclinedWork` | ~90 (S2 at day 34; 20 past follow-up date so the hourly tick has work; 15 converted) | Declined / deferred lines with original evidence refs, `followup_due_at`, cadence, conversion (`converted_ro_id`) | – | Declined register, A2 service nurture, follow-up conversion KPI, My Garage |
| `DEALER_Coverage` | ~450 (one per `DEALER_Vehicles`) | Factory warranty windows, powertrain, EV battery warranty, open recalls (S3 + 18), service contracts incl. prepaid maintenance from `DEALER_Deals` (S1), goodwill eligibility flag | Curated warranty terms per synthetic brand | `check_coverage`, My Garage, Cockpit |

### 3.11 Agentic operating model tables (spec §4.10) — per tenant

| Table | Volume | Key content | Consumers |
|---|---|---|---|
| `PLAT_ConfigAutonomy` | v1 (active) + v2 (`effective_from = SEED_NOW + 1d`) + v3 (Decatur override draft) | v1 = every action `recommend` (observation fortnight; `SEED_NOW` is seeded as **day 14**); v2 = spec §4.10.1 pilot defaults (A7 morning work `act_and_notify`, A5/A6 publishing `act_with_approval`, A1 match / A4 acquisition `recommend`) with constraints (`a7.stage_parts`: confidence ≥ 0.8, on_hand = 0, daily_cap from trailing-30-day parts spend; `a7.auto_present_estimate.max_amount` = market-appropriate); v3 = Decatur raises `a1.match_touch` to `act_and_notify` (draft, unpublished) so the editor has something to show. Hard rules are absent by design | `autonomy_level()`, Admin › Autonomy, criterion 33 |
| Action registry (`/agents/actions`, static JSON in `agents/common/`) | ~45 actions | `agent.action_id`, default level, constraints schema, description, undo-able flag | Admin editor, agents |
| `DEALER_Recommendations` | ~6,000 over 3 months | Every agent × action × context_hash with realistic shown → accepted → converted funnels: A3 suggestions and objection strategies (~2,500), A2 NBA choices with alternatives (~1,200), A7 dispatch / staging / auto-present / re-forecast (~1,400 incl. 60 foreman overrides), A4 equity and acquisition (~300), A1 matches (~400), A5/A6 approvals (~60); acceptance ~55 %, conversion ~22 % with deliberate variance by `context_hash` so `select_next_best_action` visibly prefers a channel; 40 rows for `SEED_NOW` day with `shown_at` only | Agent ranking, Admin › Agents, criterion 34, lake |
| `PLAT_EntityMap` | ~1,800 | CRM/DMS/CDP external ids for every customer, dealer, deal (sandbox systems) + 12 low-confidence duplicates for the merge queue | Connectors, Admin entities |
| `OEM_GuardrailEvents` | ~30 | Past blocks by rule for "blocks this week" KPI | Admin guardrails view |
| Search index | all entities | Customers, VINs, dealers, journeys, campaigns, reports | ⌘K |

---

## 4. Unstructured and Conversational Data (what makes agent interactions real)

| Corpus | Volume | Purpose | Where it lives |
|---|---|---|---|
| **Live-floor transcripts** | 10 golden journeys × 3–6 minute scripted conversations (advisor + customer turns, timestamps), each containing 2–3 objections of different types and 1 competitor mention; US English + German | Feed `/journeys/{id}/utterance` in demo replay mode; A3 objection detection fires on real text | `sample-data/synthetic/<market>/transcripts/*.jsonl` |
| **Customer chat / email / SMS threads** | J1 (6 turns), J2 (2 inbound), J7 (credit question), J9 (competitor comparison ask) + 300 bulk one-liners | A2 `answer_customer_question`, memory extraction, nurture history | `CUST_Messages` |
| **CRM notes and activities** | ~1,500 short notes across customers and accounts (visit notes, call summaries, "customer mentioned boat," "spouse decides," "wants delivery before school year") | A3 Quick agent grounding; A1 lifestyle inference; Customer 360 | `DEALER_Customers.notes[]`, exported to Quick Index |
| **Contact reports** | 45 structured + 2 dictation transcripts | A6 Quick Index, action-plan generation, next-visit suggestions | `OEM_ContactReports`, Quick Index |
| **Sales-ops bulletins** | 12 per market, 200–600 words each, with dates and audiences | Sales Ops Comms AI search ("where is the updated stair-step incentive announcement?"), digests, GR tests | `OEM_Comms`, Quick Index |
| **Product and trim guides** | 7 models × 1 guide (features, packages, competitive positioning, FAQs), EV charging guide, incentive explainer per market | A1/A3 talk tracks and Q&A citations | S3 docs → Quick Index |
| **Incentive bulletins** | Derived from curated incentives: one bulletin per active program with eligibility text | A3 `get_incentives` explanations; Q&A | Quick Index |
| **Competitive intel cards** | 7 models × 3–4 competitors, "why we win / why we lose" bullets, sourced specs | A3 competitive intel panel; A6 Win Strategy | `OEM_Catalog.competitors[].intel`, Quick Index |
| **Objection strategy scripts** | 17 strategies × 2 markets with example phrasing | A3 suggested responses | `DEALER_ObjectionStrategies` |
| **Prior account plans and reports** | 11 plans, 20 report abstracts | A6 `ask` over history; Report Library ranking | `OEM_AccountPlans`, Quick Index |
| **Nurture templates** | ~40 (stage × channel × intent × market) | A2 `compose_message` | `PLAT_ConfigNurture` |
| **Personalize interactions** | ~60,000 rows (view / configure / compare / test_drive / purchase) synthesized from `DEALER_Signals` + `DEALER_Deals` with realistic co-view patterns | Train ranking, similar-items, segmentation | S3 import for Personalize |
| **Service corpora (Process 2)** | | | |
| Check-in dictation transcripts | S1–S8 + 30 bulk: advisor walk-around dictation ("customer reports grinding at low speed, front left; wants text updates; needs car by 5") US English + German | A7 `open_repair_order` concern extraction; AR-7 voice demo | `DEALER_RepairOrders.concerns[]`, `sample-data/synthetic/<market>/service_dictation/*.jsonl` |
| Technician notes and voice notes | ~250 inspections × 1–4 notes; S6 has 3 voice notes | `prioritize_findings`, `explain_finding` grounding; technician tablet | `DEALER_Inspections.items[].notes` |
| Inspection evidence | ~600 photo placeholders (tread, pads, leaks, battery SoH screen) + 2 videos (S2, S6), tagged to items | Evidence requirement; My Garage approval; C2 sharing test | S3 `app-docs` via Document service |
| Customer estimate explanations | ~300 (one per presented estimate), customer-safe, urgency in inspection terms | `explain_finding` examples; Guardrail deny-list test (10 scare-language prompts + 50 allowed) | `DEALER_ServiceEstimates.explanation_for_customer`, guardrail test set |
| Estimate pushback dialogues | 12 (advisor ↔ customer on price / necessity / "can it wait") | A3 `classify_objection` service talk tracks; 4 new objection types (`repair_cost`, `can_it_wait`, `second_opinion`, `warranty_dispute`) | Transcripts + `DEALER_ObjectionStrategies` |
| Repair procedures, TSBs, warranty bulletins | 7 models × ~12 procedures (synthetic, structured), ~40 TSBs (incl. EV charge-port TSB for S6), 6 warranty bulletins, owner-manual maintenance schedules | `get_repair_procedure`, `lookup_dtc` TSB citations, `check_coverage` text; `KnowledgeBase` in dev, Quick Index in prod | S3 docs → Quick Index / ChromaDB |
| Service nurture templates | ~18 (due-service, declined reminder ×3 ages, estimate presented, promise moved, ready, survey, recall) × market | A2 `plan_nurture(template_family=service)` | `PLAT_ConfigNurture` |
| Service-lane upsell briefs | pre-computed for every RO in BOOK/CHECK_IN | A3 `precompute_suggestions` output so Service Lane renders instantly | `DEALER_Recommendations` + cache |

---

## 5. Capability → Data Coverage Matrix

Every capability from the spec's feature matrix (§2.6) mapped to the storyline that demonstrates it and the data it depends on.

| Capability | Demonstrated by | Requires |
|---|---|---|
| A1 lifestyle intake + shortlist + trim/packages | J1 (thin signals), J9 (conquest) | `DEALER_Signals` sparse/dense, catalog packages, Personalize model, competitor current vehicle |
| A1 price build with incentives, VIN availability, EV/ICE TCO | J1, `/shop` for any bulk customer | `OEM_StateRules`, curated incentives (expired + active), inventory by dealer radius, energy/fuel prices |
| A2 routing + `validate_handoff` block | J3 (missing trade condition), 15 bulk journeys with missing context | Journeys with deliberately incomplete required fields |
| A2 nurture cadence, quiet hours, consent, stop | J1 history, J2 stall, J7 opt-out, J10 cold cohort, plans due at `SEED_NOW+15m` | `DEALER_NurturePlans`, `CUST_Consent`, `CUST_Messages`, templates |
| A2 grounded Q&A + human handoff | J1 (payments question), J7 (credit question) | Configurator, incentives, F&I ranges, dealer hours; GR-style refusal rules |
| A2 intent shift | J2 (competitor compare), J9 | Recent signals, competitor catalog |
| A3 live objections < 2 s | J1 (range), J4 (payment), J3 (trade value) via transcript replay | Transcripts, strategies with success rates from outcomes |
| A3 contextual suggestions by lifecycle | All stages across J1–J6, Service Lane | Suggestion matrix config, recent activity, lifecycle context |
| A3 (AgentCore) cited answers, ad-hoc questions, service brief via Quick Index retrieval | Lead Desk (Decatur queue), Service Lane (S5 RO) | CRM notes, leads with SLA, service journeys, Quick Index |
| A2 `publish_shift_list` (replaces on-demand call list) | Decatur BDC login at shift start | Ranked Tasks created by the scheduler from journeys + recommendations |
| A4 valuation, equity, payment delta, disposition | J3 (negative), J6 / S5 (positive) | Current vehicles, payoff, depreciation curve, dispositions |
| A4 `monitor_equity` events, `recommend_acquisition` | S5 (`ro.opened` → `equity.crossed_positive`), S8 (recon → CPO), Trade Desk acquisition card | `DEALER_Vehicles` equity snapshots, nightly run, stock-need signal from A1 shortlist counts |
| A5 attribution, ROI, budget shift, ask; nightly auto-drafted approval | Campaign storyline, J9; pending `DEALER_Approvals` budget-shift row | Campaigns, touchpoints, deliveries, spend series |
| **A7 booking, `predict_next_service`, proactive recall, loaner / mobile capacity** | S1, S3 | `DEALER_Vehicles` next_service_due, `DEALER_Coverage` recalls, lane + loaner capacity |
| **A7 morning work: `dispatch_ro`, `stage_parts`, `prepare_ro_brief`** | 12 ROs booked for `SEED_NOW+1d` at Decatur; S6 HV gate | Technician roster + skills, parts on-hand, predicted work, autonomy v1 (`recommend`) vs v2 (`act_and_notify`) |
| **A7 check-in, RO mirror, dictation** | S1–S8 | Dictation transcripts, DMS sandbox RO ids |
| **A7 inspection + `prioritize_findings` + evidence rule** | S1 (yellow), S2 (red + video), S6 (EV) | Templates incl. EV, evidence docs |
| **A7 diagnose: DTC, procedure, labor, parts, coverage** | S6 (charge-port DTC + TSB), S4 (cooler) | `DEALER_DTCReference`, TSB docs, labor ops, parts, coverage |
| **A7 estimate in Code Interpreter, auto-present, customer approval, declined register** | S1 (auto-present + pending tire line), S2 (declined → converted), S5 (partial approval) | Estimates with audit, My Garage approval, consent (J7 opt-out reused in S2) |
| **A7 parts tracking, re-dispatch, `reforecast_promise`** | S4 (back-order, promise moved), S7 (early, Board-only update) | Parts orders, events 2 h before `SEED_NOW`, messages |
| **A7 ready, invoice, pickup, survey, follow-up tick** | S5, S7; 20 declined-work rows past due | Documents, appointments, `DEALER_DeclinedWork`, service templates |
| **Repurchase/upgrade handoff (service → next sale)** | S5 → J6 upgrade journey; S8 → Trade Desk | `previous_journey_id`, `DEALER_Handoffs`, Play runs, recommendations converted |
| **Technician masking** | S6 technician login → RO view | Role field masking on `/service/ros/*` |
| **Service Cockpit / Fixed Ops dashboard / Store Cockpit › Service** | Decatur service manager login | Lake `service/`, event read models; baselines visible (MPI ~60 %, presentation ~30 %) |
| **Autonomy ladder** | Same `a7.stage_parts` at v1 `recommend` vs v2 `act_and_notify`; Macon `act_with_approval` row | `PLAT_ConfigAutonomy` v1/v2/v3, `DEALER_Approvals` |
| **Outcome loop + Admin › Agents** | Any agent action; `select_next_best_action` channel preference for a `context_hash` | `DEALER_Recommendations` 3-month history with variance |
| **Event-driven triggers with no user action** | Publish `inventory.changed` for J2's saved build → `discovery.match_found` → A2 card; `recall.detected` → S3 booking; Monday 05:00 → A6 drafts | Seeded builds in radius, coverage recalls, red dealers, scheduler rules |
| A6 all 15 views | District storyline | RDR, CSI, compliance, EV readiness, comms, reports, weekend, competitor offers, C&I programs |
| A6 plans / briefs / strategies with citations | Decatur, Marietta, fleet bid | Accounts, RDR, market intel, C&I, contact reports, research allow-list |
| A6 guardrails | Test prompt set | GR rules config, guardrail events |
| A2 ↔ A6 account-linked pricing | J8 | Fleet account + opportunity brief |
| F&I Desk, menu, lender decision, push to DMS | J3, J4 | Finance offers, F&I products, deals, disclosures |
| Delivery Board, delivered → OWN, survey | J5 | Deliveries, appointments, documents |
| Store Cockpit tabs | Decatur GM login | Event-materialized read models from all of the above |
| My Garage full lifecycle | J1–J6 customer logins | Every customer-facing table + shared documents |
| Unified shell: switcher, scope, ⌘K, tasks, bell | DM login → Decatur → J1 | Org hierarchy, users, notifications, tasks, search index |
| Deep links + context strip + handoffs | Alert AL-Decatur-CSI → Store Cockpit → J1 → nurture | Alerts, resource URLs in all agent outputs, handoffs |
| Approvals | Decatur action plan, J3 exception, C&I ask, budget shift | `DEALER_Approvals` pending |
| Config without redeploy | Edit J1 nurture template; edit GR rule | Config tables versioned |
| Data classification | J3 prequal C4 fields; Quick Index export | Tagged fields, hashed exports |
| Market switch | Same storylines in `us` and `de` | Both seed packs |

---

## 6. Generation Approach

```
scripts/seed/
  fetch_open_data.py          # done: open anchors → sample-data/open/
  generate/                    # new
    __init__.py
    config.py                  # SEED_NOW, RNG seed, volumes per market
    anchors.py                 # loaders for sample-data/open/* and curated/*
    market.py                  # names, addresses, phone/email formats, locale formatting per market
    storylines/
      journeys.py              # J1–J10 definitions (hand-written), both markets
      service.py               # S1–S8 service visits (hand-written), both markets
      district.py              # District 7 / Bezirk Süd-West dealer conditions
      marketing.py             # campaign storyline
    entities/
      org_users.py  catalog.py  inventory.py  vehicles.py  customers.py  signals.py  leads.py
      journeys.py   commerce.py marketing.py  oem.py       platform.py
      service_ref.py           # templates, DTC reference, labor ops, parts catalog, coverage terms
      service.py               # service journeys, ROs, inspections, estimates, parts orders, declined work
      agentic.py               # PLAT_ConfigAutonomy v1/v2/v3, action registry, DEALER_Recommendations history
    corpora/
      transcripts.py  messages.py  notes.py  bulletins.py  guides.py  templates.py
      service_corpora.py       # dictation, technician notes, evidence placeholders, explanations, procedures/TSBs
    exporters/
      dynamodb.py  s3_lake.py  quick_index.py  personalize.py  search_index.py  docs_bucket.py
    validate.py                # invariants (below)
  generate_synthetic.py        # CLI: --market us|de|all --out local|aws --seed 42 --now 2026-09-22
```

**Rules**
- Hand-written storylines override generated defaults by id; bulk records are sampled around anchors with market-appropriate distributions.
- Generation order respects dependencies: org → users (incl. technician skills) → catalog → inventory → customers → **vehicles (VIN master)** → signals/leads → journeys/events → commerce → **service_ref → service (journeys, ROs, inspections, estimates, parts, declined, coverage)** → marketing → OEM → platform → **agentic (autonomy configs, recommendations history joined to journey/service events)** → corpora → derived (Personalize, lake incl. `service/` + `recommendations/`, search, docs bucket).
- Service history is generated *before* `DEALER_Recommendations` so every A7 recommendation row can point at a real RO, inspection item, or estimate line; A4 equity snapshots are computed from the seeded mileage series so `equity.crossed_positive` events have real numbers behind them.
- `--out local` writes JSONL/Parquet to `sample-data/synthetic/<market>/` for review and tests; `--out aws` writes to DynamoDB (batch), S3 lake, Quick Index ingest bucket, and Personalize import bucket for the configured stage.
- **Quick-generated artifacts** (account plans, briefs, call lists, meeting decks) are produced by invoking the real Quick Flows after base data is loaded (`--with-quick-artifacts`), so they cite real rows; a fallback writes template-based placeholders when Quick is not yet provisioned.
- Personalize interactions are exported only from `DEALER_Signals` + `DEALER_Deals` (no C3 beyond hashed ids).

**Validation invariants (`validate.py`, run after generation and in CI)**
- Every `journey.stage` has the required context for that stage unless flagged `demo_missing_context=true`.
- Every event has `actor` and `evidence`; event timestamps are monotonic per journey and all ≤ `SEED_NOW` except scheduled items.
- Every incentive applied in a configuration is `status=active` on the configuration date; expired ones appear only in history.
- Every VIN in a deal/delivery exists in inventory with a consistent status; no VIN reserved by two journeys.
- Every `source_ref` in catalog/prices/registrations resolves to `sample-data/open/`.
- Every resource URL in seeded notifications, tasks, and traces resolves via `/nav/resolve` for its recipient's role.
- No real OEM trim name appears in synthetic brand catalogs; no real person names (checked against a denylist of public figures).
- C4 fields exist only in `DEALER_FinanceOffers` / `DEALER_Deals`, never in traces, messages, or exports.
- Both markets produce identical storyline ids and event counts (localized content only).

**Process 2 and agentic invariants (added 2026-09-22)**
- Every delivered or pre-owned VIN has exactly one `DEALER_Vehicles` row; every service journey, RO, inspection, estimate, declined-work, and coverage row resolves to it; at most one service journey per VIN is in a non-terminal stage.
- Every yellow/red inspection item has ≥ 1 evidence document; every estimate line has `finding_ref`, labor op, price `source_ref`s, and a coverage result; every line in APPROVE or later has a decision with `decided_by`.
- Every RO in REPAIR or later has a technician; every HV labor op is assigned only to a technician with `hv_certified`.
- Every `DEALER_Recommendations` row has `shown_at`; `accepted_at` ≥ `shown_at`; `converted_at` ≥ `accepted_at` and points at a real conversion event; `autonomy_level_at_time` matches the config version active at `shown_at`.
- `PLAT_ConfigAutonomy` contains no hard-rule actions (validator has the exclusion list); every action in the registry has a level in the active version.
- At most one purchase journey per customer in DISCOVER–DELIVER; every `superseded_by` points at a journey with `previous_journey_id` pointing back; OWN journeys are excluded from the active count.
- Technician-scoped exports of RO / inspection data contain no customer contact fields.
- At `SEED_NOW`: ≥ 10 service appointments tomorrow at Decatur, ≥ 15 declined-work rows past due, ≥ 5 nurture plans due within 15 min, ≥ 1 red dealer for the district cycle, ≥ 1 open back-order, so every scheduled agent run has visible work on first tick.

**Volumes summary (per market):** ~600 customers, ~450 VINs in the master, ~540 purchase journeys (320 active, 180 resting, 40 lost), ~6.5k journey events, ~330 service journeys / ROs, ~4.8k service events, ~250 inspections, ~300 estimates, ~90 declined-work rows, ~9k signals, ~1.1k inventory VINs, ~180 deals, ~1.4k messages, 11 dealers / 14 accounts, 24 months RDR/CSI, 3 months service lake, 6 campaigns / 4k touchpoints, ~6k recommendations, ~60k Personalize interactions, ~2.6k documents/notes/bulletins/procedures in Quick Index. Small enough to seed in minutes and reason about; large enough for lists, charts, models, and agent ranking to look real.

---

## 7. Gaps and Decisions Needed

(Renumbered D1–D6 on 2026-09-22 so that S1–S8 can name the service storylines in §2.4, matching the spec.)

| # | Item | Proposal |
|---|---|---|
| D1 | Transcript audio for the live-floor demo | Ship text transcripts + a replay mode that streams them to `/utterance`; optionally synthesize audio with Amazon Polly (US/DE voices) for the Transcribe path. Default: text replay. Same mechanism replays check-in dictation for the Service Desk demo. |
| D2 | Quick-generated artifacts at seed time need Quick provisioned | Two-pass seed: base data now, `--with-quick-artifacts` after Phase 4b; placeholders until then. Repair procedures / TSBs load into the ChromaDB `KnowledgeBase` in dev so A7 grounding works before Quick Index exists. |
| D3 | Personalize needs ≥ 1,000 users / 25 interactions each for good models | ~60k synthetic interactions meets the minimum; accept weaker ranking quality in pilot, rules ranker remains fallback. |
| D4 | German-language content (transcripts, templates, bulletins, dictation, explanations) | Generate DE corpora natively in German (not translated), reviewed by a German speaker before demo. |
| D5 | Fictional VINs must not decode to real vehicles | Use an unassigned WMI prefix (e.g., `7M7` / `7N7` style reserved for synthetic) and valid check digits; document in `market.py`. Applies to `DEALER_Vehicles` too. |
| D6 | Volume knobs for a larger demo | Config-driven multipliers; storylines unaffected. |
| D7 | Inspection evidence images | Placeholder images with burned-in labels (item, measurement, VIN last-6) rather than stock photos; 2 short synthetic videos. Optional: generate via an image model for realism, but never real vehicles or plates. |
| D8 | `DEALER_Recommendations` history realism | Seed funnels with deliberate per-`context_hash` variance so `select_next_best_action` demonstrably changes its choice (criterion 34); document the variance table in `agentic.py` so demos can explain *why* the agent prefers a channel. |

---

## 8. Definition of Done for the Seed

1. `generate_synthetic.py --market all --out local` runs deterministically and `validate.py` passes, including the Process 2 and agentic invariants.
2. `--out aws` populates all 68 tables, the lake prefixes (`events/ rdr/ marketintel/ leads/ csi/ service/ recommendations/`), the search index, the docs bucket (evidence, procedures), and the Personalize import bucket for `dev`.
3. Logging in as each of the 19 staff groups (plus a customer) shows populated home screens with the District / Journey / Service storylines visible; the technician login shows no customer contact data.
4. Every row in the Capability → Data matrix (§5) can be demonstrated live with agents calling tools against the seeded data, in both markets.
5. With no user action, the first scheduler tick after seeding produces visible agent work: 05:00 shop-load dispatches Decatur's ROs and stages parts (at the level in force), the hourly service tick sends declined-work reminders, the nurture tick sends due touches, the nightly runs update equity snapshots and draft the A5 approval, and Monday 05:00 drafts A6's Decatur action plan. Flipping `PLAT_ConfigAutonomy` from v1 to v2 changes the observable behaviour of `a7.stage_parts` on the next tick.
