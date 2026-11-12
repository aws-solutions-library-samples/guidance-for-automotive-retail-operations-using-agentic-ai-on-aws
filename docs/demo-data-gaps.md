# Demo data gaps — what weakened the demo, what was fixed, what is still open

Two passes. The first came from capturing the persona-guide screenshots with
`docs/presentation/capture_screens.py` (every Scenario Replay step on the local sandbox);
the second from the dry run of the deck (handoff that created nothing, frozen dates,
predictive leads "due at 20,000 mi", a 2019 Verdant Pulse 6, F&I numbers that disagreed across
screens, raw codes on screen). Everything in the first two sections is fixed and is what
the current capture shows. The last section is what still needs a data or environment
change.

Counts are from `sample-data/synthetic/us/` after regeneration (`--seed 42`). The seed
changed, so `tests/golden/us_manifest_hashes.json` was re-pinned and the tests that
hard-coded generated ids now look rows up by property. All 948 tests pass.

Companion deck: `docs/presentation/Automotive Retail Operations — Persona Demo Guides.pptx`
(built by `build_persona_guides.py`; one "Demo data" slide per persona).

## Fixed in code

| Reported | Cause | Fix | Where |
|---|---|---|---|
| "Hand to sales" did nothing; From-service-lane count stayed 7 | The lane counted only `source=service_lane` journeys as "already with sales", so the button showed for owners with any other open journey; the §2.1 single-active rule then attached the handoff to that journey and created nothing | "Already with sales" counts any active purchase journey; the button appears only when a handoff would create one | `api/routes/sales_floor.py` |
| Predictive lead "due around 20,000 mi (now 76,494 mi)", every lead 97%, 3–4 leads per customer | Wear curve started at 0 mi when no service record; confidence capped at the same ceiling; one lead per part | Curve starts at the last interval boundary already passed (a 76k car on a 20k part was serviced at 60k); confidence spread by part and distance to the service point; one lead per vehicle with the other parts bundled as "also due"; past-due wording when the due mileage is behind the odometer | `service/predictive.py` |
| Predictive draft "about the the vehicle you asked about… I can hold a test-drive slot" | One first-response template for every source | Predictive leads get a service heads-up draft (part, timing, offer, wait/drop/collect); source labels humanised | `agents/assistant.py` |
| Customer estimate "0 lines" with a $1,963 total; coolant in 32nds; "Cabin filter" three times; worn tires priced as a rotation | Seed drew item, unit and status independently and estimates had no lines; the estimate mapped any tire finding to the rotation labor op | See seed fixes; worn tires map to a new `LOP-TIRE-REPL` (mount & balance per pair) | `service/estimates.py` |
| F&I: monthly $756 vs lender $711.76; same APR for every term; desk Compliance ✓ vs journey "compliance missing" | Seed used simple interest, desk amortises; the term grid copied the deal APR; the desk resolved the deal by `journey_id`, the journey gate by "first deal for the customer" | One amortisation formula in seed and desk; presenting an offer writes its APR/term/monthly onto the deal; term grid steps APR by the captive rate card (36 → 72 mo); one deal resolution for desk and gate | `dealer/fni.py`, `agents/deal.py` |
| Objection reply generic, ignored 35 mi/day; "no recorded outcomes yet" | Prompt and fallback carried no customer facts; seed had no strategy-level outcomes | Coach reads commute, household, budget ceiling, current vehicle and the model's range from the journey/customer and uses them in the prompt and in the deterministic line; seed carries 5–8 outcomes per strategy | `agents/objection.py` |
| "No open service journeys in your scope" next to a full board | The insight dock snapshotted the card on first render (before data) and the in-page text used `lane_load`, which excludes FOLLOW_UP | Dock re-registers when the insight text changes; the text counts the board it sits next to | `lib/insightDock.ts`, `dealer/service/page.tsx` |
| Store Cockpit 10 handoffs vs Service Cockpit 0 | Two definitions (service-lane journeys vs runtime handoff records) | Both count journeys sourced from the service lane in scope; handoff records reported alongside | `service/desk.py` |
| Guardrail refusal did not name GR-001 | Safe alternative had no rule reference | Refusal ends with "(Guardrail GR-001 — Wholesale reduction recommendations.)" | `oem/guardrails.py` |
| "Dealers at risk" included a top performer | `_at_risk` treated any non-empty `condition` as risk | Healthy conditions (`top_performer`, `normal`, `strong_weekend_weak_monday`) are not risk; 5 of 11, not 11 of 17 | `oem/today.py`, `oem/workspace.py` |
| Raw codes: `web_form`, `MER-TRAILHAND`, `epa_fueleconomy:agg…`, `**` markdown, "Step Functions" | Codes rendered as-is | `humanizeCode` for sources; model names on F&I queue and delivery cards; `SourceRef` shows a readable provenance label (EPA fuel economy, Synthetic data, Labor ops · LOP-BRAKE-F) with the raw ref in the tooltip; chat bubbles strip markdown; campaign note names the Approvals inbox | `lib/text.ts`, `components/app/format.tsx`, `ChatPanel.tsx`, `dealer/leads/page.tsx`, `dealer/fni.py`, `dealer/delivery.py`, `oem/campaigns.py` |
| Frozen dates everywhere | Seed anchored to 2026-09-10 | Sandbox rebases full ISO timestamps to now (`RETAIL_OPS_LOCAL_REBASE=1`, default on) | `common/local_ddb.py`, `devserver.py` |
| Advisor "—", Technician "unassigned", mileage 40,120 on a 68,000-mile car | Check-in did not record the actor; replay hard-coded mileage | Check-in records the advisor; the technician claims the RO; replay reads the odometer | `service/desk.py`, `replay.ts` |
| Screens captured on the baseline (what-if sliders at 40/0/0, scenario planner at +0) | Replay acted through the API; the page did not reflect it | Deep links: `/dealer/service/cockpit?rvr=65&rate=15&close=5`, `/oem/scenario?allocation=15&…`, `/dealer/leads?lead=<id>`, `/dealer/advisor/<id>?say=<utterance>` | pages + `replay.ts` |

## Fixed in the seed (generator)

| Reported | Change | File |
|---|---|---|
| Delivery board empty at the demo store | Journeys in DELIVER get an upcoming delivery (1–6 days out) with the checklist part-way through and `docs_signed`/`customer_confirmed` drawn, so Scheduled / Prepped / Docs ready have cards; J5 (delivery week, EV) moved to D-101 | `commerce.py`, `storylines.py` |
| Only J1 at the demo store | J4 (F&I, AGREE), J5 (DELIVER) and J6 (service-lane owner) moved to `dealer_idx 0`; J1's car pinned to APPROVE with a presented estimate so My Garage opens on "Estimate waiting for you" | `storylines.py` |
| No equity-positive owner in the lane without a sales journey | J6 is now a 2023 Meridian Ion C (EV), lease ending in 5 months, positive equity, in today's lane at INSPECT with no open sales journey — the handoff creates `J-US-SVC-…` | `storylines.py`, `commerce.py` |
| "2019 Verdant Pulse 6" | `FIRST_MODEL_YEAR` per conquest nameplate; a drawn model year never pre-dates launch | `core.py` |
| Inspection units and duplicates | `MPI_ITEMS` with unit, thresholds and EV/non-EV applicability; each item once; status derived from the reading; APPROVE journeys get 2–3 actionable findings (never a car where everything is red) | `commerce.py` |
| Estimates without lines | Lines priced the way `build_estimate` prices them (LaborOps flat rate × shop rate + part); total = sum of lines; parts on the RO are the parts the lines call for | `commerce.py` |
| Lead queue "New" bucket empty; status inconsistent with response | `status=new` ⇔ no `first_response_at`; three fresh leads at the demo store received 6–25 minutes ago from customers with no journey (so assigning creates the DISCOVER journey); comments name the model and budget | `core.py` |
| Objection outcomes / curated lines | 17 real curated phrasings per market; 5–8 `DEALER_Outcomes` per strategy | `commerce.py` |
| Identical EV range per trim | Range and battery scale by trim (Standard 0.86×, Extended 1.12×, AWD/Performance −0.06) | `core.py` |
| Campaign attribution only on 2 of 6 campaigns | Campaign-source journeys spread over 01/02/05, service-lane over 03/04/06 | `core.py` |
| Deal payment ≠ lender payment | Seed uses the same amortisation as the desk | `commerce.py` |
| Jordan's predictive lead | J1 odometer 77,500 mi, so front pads and cabin filter (40k/20k curves) are ~2,500 mi out | `storylines.py` |
| Declared storyline documents missing | J4's `documents` always emitted and shared to the customer | `commerce.py` |

Regenerate with `python -m scripts.seed.generate.generate_synthetic --market all --seed 42`,
validate with `python -m scripts.seed.generate.validate --market us`.

## Third pass — the story follows one buyer

From the dry run of the New-car purchase replay: the story jumped customers (Joseph's lead,
Elena's deal, Jordan's garage), the Lead Desk gave no feedback, matches ignored the model the
lead asked about, Trade-In asked for a VIN, and the F&I numbers disagreed with each other.

| Reported | Cause | Fix | Where |
|---|---|---|---|
| No fresh web lead; the replay worked whichever lead it found | Nothing could create a lead at runtime; the replay fell back to the queue's first row, then to other customers' seeded deals and deliveries | `POST /leads` (web-form / chat / phone intake): resolves the customer by id → e-mail → new row, writes the lead as `new` with its SLA clock, emits `lead.received`. The replay posts Priya Natarajan's form at step 1 and every later step uses her journey (`ctx.journeyId`) and, for My Garage, her customer token | `dealer/leads.py`, `routes/sales_floor.py`, `lib/replay.ts` |
| F&I and delivery used Elena Rossi | The journey born from a lead had no deal and no delivery card | `POST /fni/journeys/{id}/deal` desks the journey from its saved build (or the presented trim, or the reserved VIN); the F&I queue shows "not desked — click to desk"; AGREE → DELIVER creates her card | `dealer/fni.py`, `routes/store_ops.py`, `dealer/fni/*` |
| Lead Desk gave no feedback; Activity "No agent activity yet"; owner still Keisha | Lead actions traced with no lead or journey id; ownership moves only on handoff accept, and nothing said so | Lead routes trace with `lead_id` (and the journey once it exists); `GET /traces?lead_id=`; the desk shows a lead activity strip (received / SLA due / first response / journey + owner / handoff pending → Elizabeth / booked slot), an advisor picker with names, and the journey row carries `pending_handoff` so Journeys, the drawer and the Advisor read "Keisha Baker → Elizabeth Thomas (handoff pending)" with an Accept button for the receiver | `dealer/leads.py`, `journeys/handoffs.py`, `api/automation_traces.py`, `routes/shell.py`, `dealer/leads/page.tsx`, `Left360.tsx`, `JourneyDrawer.tsx`, `AgentBar.tsx` |
| Matches ignored the Trailhand the lead asked about | `rank_vehicles` scored budget / powertrain / seats / stock only | Assign copies the lead's model and comments into the journey context; A1 adds an `asked_for` component (30 of 130, normalized to 100) and keeps the requested model on the list; the rationale names it | `agents/discovery.py`, `dealer/leads.py`, `CenterRecs.tsx` |
| Trade-In asked for a VIN; a lease showed $0 payoff and $21.8k equity | No journey deep link; the seed gave every lease a $0 payoff; a web-lead owner had no appraisal | `/dealer/trade-in?journey=<id>` values that journey's owner (Advisor card links to it); seed: lease payoff = buyout anchored to the appraisal (0.78 + 0.012 × months left); `payoff_kind` labels loan payoff / lease buyout; sandbox valuation connector anchors a first appraisal to comparable appraisals (same make/model, mileage-adjusted) | `trade-in/page.tsx`, `commerce.py`, `agents/trade.py`, `agents/equity.py` |
| 9.8% APR with the APR special "active"; deal total ≠ vehicle price; no VIN yet the deal went to delivery | The APR program was listed as a price incentive with no amount; the seed subtracted a $4,000 "incentive" that was a loan-interest deduction cap; nothing required a unit | The program carries its rate card and `min_tier`; the captive offer quotes it when the tier qualifies (2.9% at 60 mo for Prime) and the worksheet says why it did or did not apply; `price.breakdown` shows vehicle + tax + doc fee − incentives = total and flags a mismatch; the seed subtracts point-of-sale incentives only; `push_deal_to_dms` needs a reserved VIN, writes it on the deal and the journey, and delivery records the VIN master row and retires the unit | `dealer/fni.py`, `core.py`, `oem.py`, `curated/us/incentives.json`, `dealer/delivery.py`, `fni/[id]/View.tsx` |

Regenerated with `--seed 42`; only `DEALER_Customers` / `DEALER_Valuations` (lease payoff), `PLAT_Configurations` / `DEALER_Deals` / `DEALER_FinanceOffers` (incentive total) and `OEM_Incentives` (rate card) changed; the golden foundation hashes did not. `capture_screens.py` reads step personas with a regex that now accepts the one dynamic step ("own": the buyer's own token). `docs/DEMO_WORKFLOWS.md` gains workflow 7 (this story) and a "Changes in the 3 Nov build" table; workflows 1–6 keep their 28 Oct figures until the dev stack is redeployed and reseeded. The persona-guide deck was rebuilt from a fresh capture of every scenario (no failed calls).

## Still open — needs a data or environment change

### 1. Agent memory is empty
- **Screen:** every advisor session and My Garage; agents never say "last time you mentioned…".
- **Fact:** no `PLAT_AgentMemory` seed; AgentCore Memory not deployed in dev.
- **Change:** add a `PLAT_AgentMemory` phase in `platform.py` — 5–10 rows for C-US-J1 (EV preference, home charging, prior payment objection, prefers My Garage approvals, family of 4), 2–3 per other golden customer; register in `OWNER_MAP`.

### 2. Vehicle trim is null; no dealer hours
- **Screen:** "2017 Halden Alder" with no trim everywhere; the Journey guide cannot answer opening hours.
- **Fact:** 424/424 `DEALER_Vehicles` have `trim: None`; no hours on `OEM_Accounts`.
- **Change:** populate trim from the catalog for Meridian vehicles and a small per-make list for conquest vehicles; add `hours`/`service_hours` to each dealer row and read them in `booking.py` slot generation and A2 dealer facts.

### 3. Embedded dashboards are placeholders
- **Screen:** "QuickSight embed placeholder" on the account page; "AWS Quick off" on Campaigns.
- **Change (environment):** `NEXT_PUBLIC_ENABLE_QUICK=true` / `ENABLE_QUICK=true` with the seeded A5 lake prefixes, or hide the card when Quick is off.

### 4. A journey born from a fresh lead has no saved build
- **Screen:** advisor Finance card reads "Unavailable: no saved build or vehicle price to estimate against" minutes after the lead was assigned.
- **Change:** have the Configure step (or the replay) save a build before the floor session, or render the configurator call-to-action instead of an error state.

### 5. Bedrock is not reachable from the sandbox
- **Screen:** objection coach, first response and A6 answers show the curated/deterministic text (labelled as such).
- **Change:** present from the dev stack (Bedrock + Guardrails configured) or give the sandbox AWS credentials. The curated lines are now good enough to present on their own.

### 6. Feeds that read "not yet connected"
CSI, technician efficiency (billed/clocked), live auction comps, supplier ETAs. Out of scope for the demo; the label is by design.

### 7. The deployed dev stack still carries the frozen clock
The sandbox rebases seed timestamps to today; the dev DynamoDB does not. Re-seed the dev stack from the regenerated pack on a schedule, or rebase at load in the L1 loader.

### 8. Smaller items
| Where | What | Change |
|---|---|---|
| Lead cards | Owner shown as `U-US-00004` | Resolve to the advisor's name on the card |
| Store Cockpit › Most stalled | Stalled FINANCE journeys with no follow-up touchpoint | Seed a `DEALER_Touchpoints` row per stalled journey so the nudger shows "last touch N days ago" |
| Service board | Seeded `promised_at` on some journeys is date-only and escapes the rebase | Seed full ISO timestamps for every service `promised_at` |

## Checklist when applying further generator changes

1. Edit the phase in `scripts/seed/generate/`; keep every new number carrying a `source_ref`.
2. `python -m scripts.seed.generate.generate_synthetic --market all --seed 42`, then `validate --market us`.
3. Re-pin `tests/golden/us_manifest_hashes.json` with the skeleton helper in `tests/test_u1_skeleton.py` (`_generate` → `_manifest_hashes`); run `pytest tests -q`.
4. Restart the sandbox, re-capture (`capture_screens.py --homes`), rebuild the deck (`build_persona_guides.py`).
