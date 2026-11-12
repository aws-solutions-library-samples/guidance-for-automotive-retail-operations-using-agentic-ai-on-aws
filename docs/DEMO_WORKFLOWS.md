# Demo workflows: step by step

Seven presenter-led workflows for **Automotive Retail Operations powered by Agentic
AI**. Each one shows an end customer where an agent has already done work that a
person does by hand today. Every sign-in, click, input and expected value below
comes from the seeded US data and was checked against the code and the dev stack
on 28 October 2026. Each workflow ends with a short "Checked" note that says which
steps were read from the live stack and which were run offline against the same
data (the write steps).

> **Build note (3 Nov 2026).** Workflows 1–6 were checked against the deployed dev
> stack on 28 October, which still runs the 22 October seed and code; their figures are
> unchanged. Workflow 7 and the changes listed under
> [Changes in the 3 Nov build](#changes-in-the-3-nov-build) were verified on the local
> sandbox and reach the dev stack at the next deploy and reseed.

Companion documents: [DEMO.md](DEMO.md) (runbook, environment snapshot, safety
rules), [ACCESS_BY_PERSONA.md](ACCESS_BY_PERSONA.md) (sign-in and roles),
[AGENT_PANEL_BY_PERSONA.md](AGENT_PANEL_BY_PERSONA.md) (what each agent-panel
card does), [QUICK_BUILD.md](QUICK_BUILD.md) (the AWS Quick content).

## Overview

| # | Workflow | Persona (seed user) | Agents | Run time | Takeaway |
|---|---|---|---|---|---|
| 1 | [The service lane is planned before the advisor arrives](#1-the-service-lane-is-planned-before-the-advisor-arrives) | Service Advisor Anthony Nguyen (`U-US-00007`), Service Manager Ethan Wilson (`U-US-00056`) | Service Agent | 6 min | The 05:00 run has already recommended a technician, the parts to stage and a new promise for today's booked repair order, before anyone signs in. |
| 2 | [A service visit becomes a sale](#2-a-service-visit-becomes-a-sale) | Service Advisor `U-US-00007`, then Sales Advisor Elizabeth Thomas (`U-US-00002`) | Service Agent, Trade-In Agent, Journey Agent, Discovery Agent | 7 min | One click turns a positive-equity service customer into a sales journey with the service context attached. |
| 3 | [Live floor: from customer question to cited answer](#3-live-floor-from-customer-question-to-cited-answer) | Sales Advisor `U-US-00002` | Journey Agent, Discovery Agent, Sales Agent, Trade-In Agent | 8 min | One screen holds matches, equity, incentives and the next step; objections and questions get grounded, cited answers; the credit-compliance gate cannot be skipped. |
| 4 | [Lead to first response](#4-lead-to-first-response) | BDC Agent Keisha Baker (`U-US-00005`) | Sales Agent, Journey Agent, Service Agent | 5 min | Every lead opens with a drafted first response, and stalled journeys and predicted service needs reach the desk overnight. |
| 5 | [The cockpit explains itself](#5-the-cockpit-explains-itself) | General Manager Raj Nelson (`U-US-00001`) | Campaign Agent, Journey Agent, Service Agent | 5 min | Each cockpit tab opens with a written reading of its own figures, labelled with the agent that wrote it and when. |
| 6 | [Monday district prep in AWS Quick, connected to the app](#6-monday-district-prep-in-amazon-quick-connected-to-the-app) | OEM District Manager Casey Brennan (`U-US-00044`) | Field Sales Agent | 9 min | The week starts with draft plans already in the app, and Quick answers from live app data with a citation for each figure. |
| 7 | [One buyer, web lead to delivery](#7-one-buyer-web-lead-to-delivery) | BDC Keisha Baker (`U-US-00005`) → Sales Advisor Elizabeth Thomas (`U-US-00002`) → F&I Michael Lopez (`U-US-00006`) → Delivery Megan White (`U-US-00009`) → the buyer in My Garage | Sales Agent, Journey Agent, Discovery Agent, Trade-In Agent | 10 min | One web lead is followed by name through every desk: the drafted reply, the handoff, matches that lead with the model she asked about, her trade, a deal that adds up on a reserved VIN, delivery, and her own My Garage. |

## Before the demo

### Where things are

- `<console-url>` is the "Hosted console" row of [DEMO.md](DEMO.md) §1.
- `$API` is the "REST API" row of [DEMO.md](DEMO.md) §1. You need it only for
  the terminal steps.
- The password for every seed user is the seed password, `RETAIL_OPS_SEED_PASSWORD`,
  kept in the git-ignored `deploy/config.env`. Type it; don't show it on screen.

### Sign-ins

| Seed user | Persona (role) | Name | Lands on | How to sign in |
|---|---|---|---|---|
| `U-US-00007` | Service Advisor (`dealer_service`) | Anthony Nguyen | Service Board | `<console-url>/login`, username and seed password |
| `U-US-00056` | Service Manager (`dealer_service_manager`) | Ethan Wilson | Service Cockpit | same |
| `U-US-00002` | Sales Advisor (`dealer_advisor`) | Elizabeth Thomas | Journeys | same |
| `U-US-00005` | BDC Agent (`dealer_bdc`) | Keisha Baker | Lead Desk | `<console-url>/login`, username and seed password (the dev-token link `/?token=user:U-US-00005` still works on the dev stack) |
| `U-US-00006` | F&I Manager (`dealer_fni`) | Michael Lopez | F&I Desk | `<console-url>/login` |
| `U-US-00008` | Inventory Manager (`dealer_inventory`) | Emma Washington | Inventory & Trade Desk | `<console-url>/login` |
| `U-US-00009` | Delivery Coordinator (`dealer_delivery`) | Megan White | Delivery Board | `<console-url>/login` |
| `U-US-00045` / `U-US-00046` / `U-US-00048` / `U-US-00049` | Regional Manager / C&I Planner / OEM Finance / Marketing Analyst | Morgan Ellis / Robert Okafor / Fatima Robinson / William Scott | their persona home | `<console-url>/login` |
| `U-US-00001` | General Manager (`dealer_manager`) | Raj Nelson | Store Cockpit | `<console-url>/login` |
| `U-US-00044` | OEM District Manager (`oem_dm`) | Casey Brennan | OEM Workspace · Today | `<console-url>/login` |
| Your Quick user | member of `retail-ops-oem-dm` (run) or `retail-ops-builders` (owner) | — | AWS Quick console, us-east-1 | your own Quick sign-in ([QUICK_BUILD.md](QUICK_BUILD.md) §P1) |

Each sign-in lands on the "Lands on" screen, the persona's home (decided 28
October 2026); for these personas the demo portal is the shell bar's **Demo**
button. The admins are the exception (decided 29 October 2026): the Tenant Admin
(`U-US-00051`) and the Administrator land on the demo portal itself, so a
presenter signed in as an admin starts there and enters each persona from its
card. The sales
advisor also has the Lead Desk, and the service advisor Trade-In.

All dealer users work at Decatur Meridian (`D-101`). The district manager covers
District 7 – Southeast (`DIST-07`). Market: US, currency USD.

### Checklist

- [ ] Browser: use one private window per persona, or sign out (avatar menu →
      **Sign out**) between personas. A stored sign-in takes precedence over
      `?token=`, so the BDC window must have no Cognito session.
- [ ] Times on screen show in your browser's time zone. The schedules below are UTC.
- [ ] The overnight jobs ran. Run
      `get U-US-00007 "/traces/latest?runs=job.shop-load,job.stall-sweep,job.predictive-scan,job.service-lane-monitor,job.store-digest,job.district-cycle"`
      (helpers below). Each run name shows a `ts` from the last 24 hours, and
      `job.district-cycle` shows the last Monday.
- [ ] Workflow 2 is not left over from an earlier demo: the check in
      [Reset after the demo](#reset-after-the-demo) reports `J-US-SVC-00005` absent.
- [ ] Read-only pre-flight: `python3 -m app.demo.run_demo --smoke --api "$API"`
      ends with `SMOKE RESULT: ALL PASS` ([DEMO.md](DEMO.md) §3). Don't present
      in the minutes after an `l4` deploy.
- [ ] AWS Quick: `.venv/bin/python -m app.infra.deploy_quick_content --status`
      lists the space `Automotive Retail Operations`, the chat agent
      `Retail Ops Field Sales Agent`, the connector `Retail Ops OEM Read Gateway`,
      8 `Retail Ops …` Flows as `PUBLISHED` and the dashboard
      `Retail Ops Campaign ROI & Funnel`.

What the overnight jobs leave for the demo (US market; the last runs seen on 27 October 2026):

| Job | Schedule (UTC) | Agent | Leaves for the demo | Last run seen (finished, run time) |
|---|---|---|---|---|
| `shop-load` | 10:00 daily | Service Agent | morning-work cards for today's booked repair orders | 27 Oct 10:00:19, 1,538 ms |
| `stall-sweep` | 05:30 daily | Journey Agent | stalled flags and a "Re-engage stalled journey" card per newly stalled journey | 27 Oct 05:30:53, 702 ms |
| `predictive-scan` | 06:15 daily | Service Agent | predictive service leads on the Lead Desk | 27 Oct 06:15:09, 4,978 ms |
| `service-lane-monitor` | 07:00 daily | Service Agent | sales-handoff cards for owners in follow-up with positive equity | 27 Oct 07:00:22, 961 ms |
| `store-digest` | 06:30 daily | Campaign Agent, Service Agent | the cockpits' [AI INSIGHT] text | 27 Oct 06:32:24, 97,922 ms |
| `district-cycle` | 05:00 Mondays | Field Sales Agent | draft account plans for at-risk dealers | 26 Oct 05:00:49, 1,684 ms |

### Terminal helpers

Run these once in the repository root. They print no password or token.

```bash
set -a; . deploy/config.env; set +a        # seed password and AWS settings (git-ignored)
API='<the REST API row of docs/DEMO.md §1>'
CLIENT_ID=$(python3 -c "import json; print(json.load(open('app/infra/cognito-config.json'))['client_id'])")
tok() {   # tok <seed user id>: a Cognito ID token for that user; use it, don't echo it
  aws cognito-idp initiate-auth --region us-east-1 --client-id "$CLIENT_ID" \
    --auth-flow USER_PASSWORD_AUTH \
    --auth-parameters "USERNAME=$1,PASSWORD=$RETAIL_OPS_SEED_PASSWORD" \
    --query AuthenticationResult.IdToken --output text; }
get() {   # get <seed user id> <path>: a GET as that user
  curl -sS -H "Authorization: Bearer $(tok "$1")" "$API$2" | python3 -m json.tool; }
runs() {  # runs <trace tool>: the last three runs as time, latency_ms, outcome
  aws dynamodb scan --region us-east-1 --table-name retail-ops-dev-PLAT_AgentTraces \
    --filter-expression "tool = :t AND market_id = :m" \
    --expression-attribute-values "{\":t\":{\"S\":\"$1\"},\":m\":{\"S\":\"us\"}}" \
    --query 'Items[].[ts.S,latency_ms.N,outcome.S]' --output text | sort | tail -3; }
```

Every staff persona in the table above has a Cognito login (since 4 Nov 2026), so the same
`USER_PASSWORD_AUTH` call works for the BDC user too. The dev token
`curl -sS -H "Authorization: user:U-US-00005" "$API<path>"` still works on the dev stack.

### Suggested 30-minute running order

| Minutes | Workflow | Why in this slot |
|---|---|---|
| 0–6 | 1. The service lane is planned | Opens on work done before anyone signed in |
| 6–13 | 2. A service visit becomes a sale | Carries the same store from service into sales |
| 13–21 | 3. Live floor | The sales advisor works the customer with every agent on one screen |
| 21–30 | 6. Monday district prep | Ends at the OEM level, in the app and in AWS Quick |

For a dealer-only audience, replace workflow 6 with workflows 4 and 5 (5 minutes
each). All six in order take about 40 minutes.

---

## 1. The service lane is planned before the advisor arrives

**Persona and screens.** Service Advisor Anthony Nguyen (`U-US-00007`), then
Service Manager Ethan Wilson (`U-US-00056`), at Decatur Meridian (`D-101`).
Agent: Service Agent. Screens: Service Board, Check-in, Service Lane, Service
Cockpit. Run time: 6 minutes.

**Why it matters.** Customers leave the dealership's service lane when visits
bring surprises: unexpected costs, and no word when a car will be late. The
Service Agent prepares the day overnight, so the advisor opens the board to a
plan instead of building one at the counter.

- 45% of owners were dissatisfied with their dealership service experience,
  mainly because of unexpected costs and poor communication.
  - Source: [Cox Automotive Service Industry Study 2025 (November 2025)](https://www.coxautoinc.com/insights/new-cox-automotive-study-finds-dealerships-have-lost-12-of-service-visits-to-competition-since-2018/)

**Setup.**

- The data: Decatur has one booked repair order, `SJ-US-00010` (Susan Mitchell,
  VIN `7M7VIN-US-00354`, promised 14 Aug 2026 20:18 UTC, projected 16 Aug 22:18
  UTC). The seed dates are fixed, so this visit reads as overdue every morning and
  the morning run always has work.
- The morning run already happened: the `shop-load` schedule runs every day at
  10:00 UTC (the code calls it the 05:00-local run). Confirm with
  `runs runtime.job.shop-load`.
- To run it again on the spot: `python3 -m app.infra.deploy_l6 --run-once shop-load`.
  It invokes the deployed job for both markets and rewrites the same card ids
  (`REC-MW-DISP-`, `REC-MW-PARTS-`, `REC-MW-PROMISE-<repair order>`). Safe to
  repeat; nothing to reset. The technician it names can change from run to run.
- Everything else in this workflow is read-only. Don't press **Re-present pending
  estimates**, **＋ Book service** or the check-in submit button.

**Steps.**

| Step | Where (URL path / menu) | Action | Actual input | What you should see |
|---|---|---|---|---|
| 1 | `<console-url>/login` | Sign in | Username `U-US-00007`, the seed password | **Service Board** (`/dealer/service`), subtitle "Every open service journey by stage · 6 shown". |
| 2 | Service Board, tiles | Read the tiles | — | "Lane load 6", "Promised-time at risk 2" (re-forecast), "Waiting approval 1" (needs follow-up), "Parts holds 0". |
| 3 | Service Board, kanban | Point at the Book and Approve columns | — | **Book**: "Susan Mitchell", wait, `7M7VIN-US-00354`, "Unassigned · Sarah Green", badge "⏰ overdue promise". **Approve**: "Jordan Alvarez", pickup delivery, badges "⏳ waiting approval", "⏰ overdue promise", "↗ equity". |
| 4 | Right rail | Read both panels | — | "Today's appts — not arrived" (1): `7M7VIN-US-00354`, "Service · Check in →". "Needs attention" (4): "SJ-US-00010 past promised time", "SJ-US-00001 past promised time", "Estimate EST-US-00001 waiting for a decision", "Back-order PO-US-00001 on RO-US-00001". |
| 5 | Agent panel (strip above the page) | Click **Morning dispatch** | — | "Service Agent · Morning dispatch — Before the lane opens it recommends a technician for each booked RO, flags likely parts to stage and re-forecasts promises that already slip…", then "Trigger: every morning, 05:00 local. Last ran …(your store)", where the time is the 10:00 UTC run in your time zone. **Promise re-forecaster** shows the same run. |
| 6 | Terminal (optional; the cards have no list screen) | Show what the run wrote for this store | `aws dynamodb scan --region us-east-1 --table-name retail-ops-dev-DEALER_Recommendations --filter-expression "begins_with(recommendation_id, :p) AND dealer_id = :d" --expression-attribute-values '{":p":{"S":"REC-MW-"},":d":{"S":"D-101"}}' --query 'Items[].[recommendation_id.S,autonomy_level.S,reason.S]' --output text` | Three cards, all `recommend`: `REC-MW-DISP-SJ-US-00010` "assign technician U-US-00007" (the pick can differ per run), `REC-MW-PARTS-SJ-US-00010` "stage parts for ['Multi-point inspection', 'Oil & filter', 'Tire rotation', 'Brake inspection']", `REC-MW-PROMISE-SJ-US-00010` "projected runs 3000 min past promised — notify customer with new time". |
| 7 | Right rail → **Check in →** | Click | — | `/dealer/service/SJ-US-00010/checkin`. Section "Service Agent check-in brief", box **Brief**: "5 item(s) due or previously declined; 0 prior visit(s) on this VIN; coverage on file: Prepaid maintenance." Rows "Due: Multi-point inspection", "Due: Oil & filter", "Due: Tire rotation", "Due: Brake inspection", "Due: Previously declined: Coolant flush". Line "Coverage on file: Prepaid maintenance". |
| 8 | Nav Sales floor → **Service Lane** | Open | — | Tiles "Vehicles in service 2", "Equity positive 1", "Lease ending 0", "Already with sales 2". Card "Jordan Alvarez · 2017 Halden Alder 7M7VIN-US-00194": "Positive equity · $7,743", "High mileage · 68,000 mi"; talk track "While you're here, the inspection flagged Tires — want me to price it with today's visit?"; "Sales Agent upsell brief": "Tires — yellow", "Cabin filter — declined · $141"; "Loyalty offer · Owner loyalty — equity upgrade", $7,743. |
| 9 | Service Lane, pill **All vehicles** | Click | — | Adds "Susan Mitchell · 2022 Larkspur Fenland 7M7VIN-US-00354": upsell brief "HV battery SoH — red", "Wipers — yellow", "Coolant flush — declined · $299". |
| 10 | Avatar → **Sign out**, then `/login` | Sign in | `U-US-00056`, the seed password | **Service Cockpit**: "Lane load today 2", "RO cycle time (avg hours) 31", "MPI completion 100%", "Close rate 20%", "Promise kept 67%", "Parts-wait ROs 1", "Handoffs to sales 0". |
| 11 | Service Cockpit, [AI INSIGHT] | Read | — | "Fixed-ops priorities · written by the Service Agent …d ago" (written 22 Oct 21:53 UTC, re-checked nightly): "Recommended-work close rate is 20% over 5 decided estimates, and 5 declined items remain open where a 30-day service reminder recovers the most of these. Only 20% of owned vehicles in the 25-48-month cohort came back for service, showing retention leaks earlier as vehicles age." |
| 12 | What-if card | Move two sliders | "Labor rate change" to `+5%`; "Recommended-work close rate" to `+10 pts` | "Labor revenue today $6,633", "Projected labor revenue $6,965", "Pending estimates $2,075", "0 · Estimates over 40% of value → sales handoff". The insight drops "written by…" and shows the figures-only text, which follows the sliders. |

**Productivity shown.**

| Work | Before (spec) | After (what the agent already did) | Read it live |
|---|---|---|---|
| Morning prep for a booked repair order | The advisor works across separate systems: scheduling, DMS repair orders, inspection tablets, parts. Scheduling can't see equity or prior declined work; parts can't see which repair orders wait on a part (spec §1.2, service table). "Morning work done before shift start" is a spec KPI (§1.3). | 3 cards for `SJ-US-00010` at `recommend`: a technician, 4 services to stage, a promise slip of 3,000 minutes | Step 6, or `get U-US-00007 "/recommendations/stats?action_id=dispatch_ro&dealer=D-101"` (repeat for `stage_parts`, `reforecast_promise`) → `"rows": 1` each |
| The same run, all US stores | — | 22 cards for 8 booked repair orders on 27 Oct | Step 6 without the `dealer_id` condition |
| Time from trigger to ready | — | Schedule fires 10:00:00 UTC; run finished 10:00:19 UTC; job time 1,538 ms in the Service Agent runtime | `runs runtime.job.shop-load` |
| Check-in preparation | History, declined work, coverage and equity are looked up at the counter (spec §1.2) | 5 due or declined items and the coverage on one panel when the page opens (the API also returns the equity snapshot, -$1,839) | `get U-US-00007 /service/journeys/SJ-US-00010/brief` |

**Talking points.**

- No one clicked anything: the Service Agent ran on its schedule, and every card
  carries the autonomy level `recommend`. A person assigns the technician, orders
  the parts and messages the customer.
- Every item is sourced: the brief cites `DEALER_DeclinedWork:DW-US-00003` and
  `DEALER_Coverage:maint`, and the cockpit insight lists the tables it read.
- The model only writes the words. The cards and the what-if are rules; the
  cockpit insight was written overnight by Amazon Bedrock through the digest
  guardrail, from the cockpit's own figures.
- A slipping promise is flagged with the size of the slip, before the customer
  calls to ask.

**If something goes wrong.**

- "Last ran" is missing or older than a day: check `runs runtime.job.shop-load`,
  then run `python3 -m app.infra.deploy_l6 --run-once shop-load`.
- The check-in page says "Service Agent brief not available from the API yet":
  a transient API error; reload the page.
- The cockpit insight has no "written by": a slider is off zero (set both back to
  0), or the digest has not re-checked within 26 hours (`runs job.store-digest`).

*Checked 28 Oct 2026.* Live, signed in as each seed user: steps 1–5 and 7–12
(`/me/home`, `/service/journeys`, `/service/attention`, `/service/arrivals`,
`/traces/latest`, `/service/journeys/SJ-US-00010/brief`, `/service/lane`,
`/service/cockpit` with and without slider values) and the step 6 scan. Offline
(local data mode): the job body, `POST /jobs/shop-load` with `apply` (8 booked,
22 cards; `D-101`: 1 booked, 3 cards). The `--run-once` command was not run
against the dev stack.

---

## 2. A service visit becomes a sale

**Persona and screens.** Service Advisor Anthony Nguyen (`U-US-00007`), then Sales
Advisor Elizabeth Thomas (`U-US-00002`). Agents: Service Agent, Trade-In Agent,
Journey Agent, Discovery Agent. Screens: Service Lane, Repair order view, Estimate
& Approval, Journeys, Advisor. Run time: 7 minutes.

**Why it matters.** The service visit is the dealer's best moment to start the
next sale, but the service lane and the sales floor don't share the customer. Here
the Trade-In Agent's equity sits on the repair order, and one click hands the owner
to sales with the context attached.

- Service feeds the next sale: owners who service their car at the dealership are
  much more likely to buy their next car there (74% in the Cox study).
  - Source: [Cox Automotive Service Industry Study 2025 (November 2025)](https://www.coxautoinc.com/insights/new-cox-automotive-study-finds-dealerships-have-lost-12-of-service-visits-to-competition-since-2018/)

**Setup.**

- The data is the seeded repurchase storyline (S5) at Decatur: `SJ-US-00005`, Linda
  Scott (`C-US-00003`), a 2019 Verdant Pulse 6, VIN `7M7VIN-US-00197`, visit completed,
  valuation `VAL-US-00005` (trade value $7,046, payoff $0). She has no purchase
  journey, so the hand-off creates a new one, `J-US-SVC-00005`.
- Check that `J-US-SVC-00005` does not exist yet (see
  [Reset after the demo](#reset-after-the-demo)).
- This workflow writes: the journey, a hand-off, a task, a service event, and the
  "shown" records the Advisor screen keeps. It is not safe to repeat without the
  reset: a second click attaches to the open journey ("…journey J-US-SVC-00005
  updated") instead of creating it. Run the reset after the demo.

**Steps.**

| Step | Where (URL path / menu) | Action | Actual input | What you should see |
|---|---|---|---|---|
| 1 | `<console-url>/login` | Sign in | `U-US-00007`, the seed password | Service Board. |
| 2 | Nav Sales floor → **Service Lane** (pill "Upgrade signals") | Open | — | "Jordan Alvarez", "Positive equity · $7,743", badge "Sales journey J-US-J1 open": this owner already has a sales journey, so the lane offers no hand-off (the Advisor screen is the sales floor's, so the service advisor gets no link to it). |
| 3 | Address bar | Open the repair order | `<console-url>/dealer/service/SJ-US-00005` | Repair order view, badge "COMPLETED". "Customer & vehicle": Linda Scott, linda.scott@example.net, (229) 555-3593; VIN `7M7VIN-US-00197`; Model "2019 Verdant Pulse 6"; Mileage in "64,150 mi"; Advisor "Joseph White". "Service lane (sales view)": "Positive equity — upgrade candidate", $7,046. |
| 4 | Tab **Estimate** → **Open Estimate & Approval** | Click | — | Tab shows "Estimate EST-US-00005", declined, total $1,963.00. The Estimate & Approval page (`/dealer/service/SJ-US-00005/estimate`) shows "Repair vs replace: Estimate is 27.9% of the vehicle's value ($7,046; equity $7,046). Threshold 40%." with source `DEALER_Valuations:VAL-US-00005`, and "↗ Positive equity — upgrade conversation", $7,046. The estimate is under the threshold, so the hand-off is the advisor's call. |
| 5 | Back to the repair order → Actions panel → **Hand to sales** (or agent panel → **Repurchase handoff** → "Hand to sales") | Click, keep the reason, click **Hand to sales** | Reason (prefilled): `positive equity at service visit` | Modal "Repurchase / upgrade handoff", then the toast "Handed to sales — journey J-US-SVC-00005 created". |
| 6 | Agent panel → **Repurchase handoff** | Click | — | "Last ran …(this repair order)" with the time you clicked. |
| 7 | Avatar → **Sign out**, then `/login` | Sign in | `U-US-00002`, the seed password | **Journeys**. "From service lane" goes from 7 to 8 and "Active journeys" from 23 to 24. **Discover** column: new card "Linda Scott", "No vehicle yet", badges "Missing 1" and "service lane", footer "Joseph White · D-101". |
| 8 | Click the Linda Scott card | — | — | Drawer "Journey J-US-SVC-00005": Stage Discover, Owner `U-US-00004`, Source "service lane", Next best action "capture context" (offline result; live, the Journey Agent picks among candidate actions and may choose another). "Handoff gate — leaving Discover": "Shortlist saved — captured now". Activity includes "handoff created — service_lane handoff: positive equity at service visit" → `U-US-00004`, and "task open — Accept handoff: service_lane handoff: positive equity at service visit". |
| 9 | Drawer → **Open in Advisor** | Click | — | `/dealer/advisor/J-US-SVC-00005`: "Live session · Linda Scott · J-US-SVC-00005 · D-101 · US"; current vehicle "2019 Verdant Pulse 6"; "Vehicle matches · EV & ICE" from the Discovery Agent (offline: three trims at 83% fit, the Crosstrail Limited and Summit and the Ridgeline S); Trade-in card "Value $7,046", "Payoff $0", "Equity $7,046" from the Trade-In Agent; the summary box shows the Trade-In Agent's equity note (live, Amazon Bedrock writes it; offline the template "Trade equity on the 2019 Verdant Pulse 6 is USD 7,046 (positive). Good time to discuss an upgrade.") and "Trade-In Agent: positive trade equity". |

**Productivity shown.**

| Work | Before (spec) | After (what the agent already did) | Read it live |
|---|---|---|---|
| Spot the upgrade candidate | The service lane and the sales floor don't share the customer; scheduling can't see equity or the purchase journey; nobody brings the owner back to the next purchase (spec §1.2). §2.1.1 defines the hand-off that should do this. | Equity ($7,046) and repair-vs-replace (27.9% of value) are already on the repair order, with their source | `get U-US-00007 /service/estimates/EST-US-00005` → `repair_vs_replace` |
| Hand the owner to sales | — | One click created `J-US-SVC-00005` in DISCOVER with `source=service_lane`, hand-off `HANDOFF-SJ-US-00005`, a task for the repair order's advisor and the event `service.handoff_to_sales` | `get U-US-00002 /journeys/J-US-SVC-00005/activity` |
| Time from click to journey | — | the `latency_ms` of the hand-off's trace row, tool `…handoff_to_sales` (offline: 22 ms) | `get U-US-00007 "/traces?service_journey_id=SJ-US-00005&limit=5"` |
| Nightly candidates | — | The 07:00 UTC lane monitor scans visits in follow-up and writes a hand-off card for each owner with positive equity (offline on the seed: 7 scanned, 6 flagged across US stores); 961 ms on 27 Oct | `runs runtime.job.service-lane-monitor` |

**Talking points.**

- The Service Agent doesn't sell. It hands off; the Journey Agent owns the new
  purchase journey, and a person accepts the task.
- The figures behind the decision are on the screen with their source: the
  valuation `DEALER_Valuations:VAL-US-00005` and the formula `1963 / 7046.0 × 100`.
- One active journey per customer: when the owner already has one (Jordan
  Alvarez, `J-US-J1`), the hand-off attaches to it instead of creating a duplicate.
- Every step leaves a record: the service event, the hand-off, the task and the
  agent trace.

**If something goes wrong.**

- The toast says "updated" instead of "created": an earlier demo left the journey.
  Run the reset, then reload.
- The repair order is not on the Service Board: completed visits have no board
  column. Open it by URL (step 3).
- The new card is missing on Journeys: the board refreshes every 60 seconds; press
  **Refresh**.

*Checked 28 Oct 2026.* Live: steps 1–4 (`/service/lane`, `/service/journeys/SJ-US-00005`,
`/service/estimates/EST-US-00005`, `/journeys/board` counts before the hand-off) and
that `J-US-SVC-00005` and `HANDOFF-SJ-US-00005` don't exist. Offline (local data
mode, same seed): steps 5–9 (`POST /service/SJ-US-00005/handoff-to-sales`, then the
board, `/state`, `/activity` and `/advisor` for `J-US-SVC-00005`) and the reset.

---

## 3. Live floor: from customer question to cited answer

**Persona and screens.** Sales Advisor Elizabeth Thomas (`U-US-00002`). Agents:
Journey Agent, Discovery Agent, Sales Agent, Trade-In Agent. Screens: Journeys,
Advisor (live), Assistant, the journey drawer. Run time: 8 minutes.

**Why it matters.** Most buyers move between online steps and the showroom, and
the advisor has to pick up where the website left off. On the Advisor screen the
agents have already matched vehicles, valued the trade and picked the next step,
and every answer cites its record.

- Most buyers want a mix: 63% of recent buyers said the ideal purchase combines
  online and in-person steps, while 7% bought entirely online.
  - Source: [Cox Automotive, 2025 Car Buyer Journey Study (January 2026)](https://www.coxautoinc.com/insights/cox-automotive-car-buyer-journey-study-finds-efficiency-digital-tools-and-ai-drive-record-satisfaction/)

**Setup.**

- The data: `J-US-J1`, Jordan Alvarez (seeded storyline J1, "EV-curious family …
  range objection"), stage QUALIFY, owner `U-US-00002`. `J-US-00166`, Jamal Clark,
  stage AGREE, deal `DL-US-00038`, with OFAC, Red Flags and adverse action still
  pending.
- What writes: opening the Advisor screen records which suggestions were shown
  (normal page use). Each objection **Check** records one more "shown" card; these
  are not reset and do no harm. Don't press **Use This** or **Alternative** on the
  suggested response: each adds an outcome to the strategy's success rate. The
  optional Sales Package step writes `SP-J-US-J1`; the reset removes it. The
  blocked advance in step 13 writes nothing and is safe to repeat.

**Steps.**

| Step | Where (URL path / menu) | Action | Actual input | What you should see |
|---|---|---|---|---|
| 1 | `<console-url>/login` | Sign in | `U-US-00002`, the seed password | **Journeys**: "Active journeys 23", "Stalled 23", "Missing context 1", "From service lane 7". |
| 2 | Qualify column | Double-click **Jordan Alvarez** (Meridian Ion C; badges Stalled, service lane, nurture) | — | Advisor `/dealer/advisor/J-US-J1`: "Live session · Jordan Alvarez · J-US-J1 · D-101 · US" and a session timer. |
| 3 | Left, **Customer 360** | Read | — | Budget "$31,000–$43,000 · ≤$570/mo", Location "Decatur, GA", Daily drive "~35 mi/day", Current vehicle "2017 Halden Alder". Summary box: "EV-curious family; thin signals → lifestyle intake; range objection; expired vs active incentives" and "Trade-In Agent: positive trade equity". |
| 4 | Center, **AI Recommendations** | Read | — | "Vehicle matches · EV & ICE", "100% fit": 1 Ion C Standard, 2 Ion C Extended, 3 Ion M Standard. Trade-in: "2017 Halden Alder", Value $7,743, Payoff $0, Equity $7,743. |
| 5 | Right, **Live Assistance** | Read | — | "✦ Next best action · Journey Agent": "book test drive — stage QUALIFY default play", button "Do it: Book test drive" (don't press). Competitive intel: Rheinmark Eco 4 "60% win", Solari Cove Electric "50% win", Brightwater Tide EV "49% win". Value snapshot "In stock 39". Eligible incentives: the loan-interest deduction $10,000 ("tax deduction, not a price reduction"), "Utility Level 2 home charger rebate (generic template)" $500, "Meridian Motors captive-finance APR special (synthetic)" terms vary. |
| 6 | Right, box "Type what the customer said…" | Type, click **Check** | `I'm worried about the range on our weekend road trips.` | "Detected objection · range anxiety" with the quote. A response card "range anxiety" with a short suggested reply, the line "no recorded outcomes yet · grounded on OBJ-US-range_anxiety", and the round trip in ms next to the listening controls (about 5,000 ms live on 29 Oct). Live, Amazon Bedrock phrases the reply from that strategy, so the wording varies; offline (no model), or when the guardrail withholds the model's wording, it is the library line "Acknowledge the range anxiety concern, reframe on value, offer a concrete next step." A "% success" badge appears once the strategy has five recorded uses. |
| 7 | Same panel, **Demo transcript** → **● Start listening** (optional) | Click | five scripted lines, one every 3.5 s, e.g. "Honestly, the price is more than we wanted to spend." | Each line is checked as it arrives; an objection shows the same card. |
| 8 | Terminal (optional: the Sales Package has no button) | Ask the Journey Agent for the Sales Package | `curl -sS -X POST -H "Authorization: Bearer $(tok U-US-00002)" "$API/journeys/J-US-J1/package" \| python3 -m json.tool` | 200 with `agent_status` true for all three specialists (Discovery, Sales, Trade-In) and `summary` "QUALIFY journey; 5 vehicle matches; positive equity; 1 live recommendations; NBA: book_test_drive" (offline result), or 202 `"status": "building"`; then poll `GET /journeys/J-US-J1/package`. Reload the Advisor: the summary box shows the new summary. |
| 9 | Advisor top bar → **Assistant** | Click | — | `/dealer/assistant?journey=J-US-J1`, chat titled "Sales Agent · Jordan Alvarez". |
| 10 | Tick **Agent mode**, then ask | Type, send | `Summarise this customer and tell me the next step. Do not send anything.` | A summary (QUALIFY, stalled, service-lane source, no appointment) and the next step "Book a test drive", with citations. Badge "strands · 4 tools · AgentCore". On 28 Oct the Sales Agent used customer memory, contextual suggestions, lifecycle context and recent activity, in 10.9 s. |
| 11 | Untick **Agent mode**, then ask | Type, send | `Will this customer get approved for the loan at 2.9%?` | "That needs a person — credit, legal, and approval decisions are made by the F&I team. I can draft a handoff note instead." No model is called. |
| 12 | Nav → **Journeys** → Agree column → **Jamal Clark** | Single-click | — | Drawer "Journey J-US-00166", "Handoff gate — leaving Agree": checkboxes "Deal structured — captured now" and "Credit compliance cleared — captured now", then "⛔ ofac not cleared", "⛔ red_flags not cleared", "⛔ adverse_action not cleared", each "Recorded by the F&I connector — cannot be set here". |
| 13 | Drawer → **Advance to Deliver** (no box ticked) | Click | — | Refused: "cannot leave AGREE: missing ['deal_structure', 'credit_compliance', 'compliance:ofac', 'compliance:red_flags', 'compliance:adverse_action']". Nothing changes. |
| 14 | Drawer → **Open in Advisor** → Finance card (optional) | Read | — | "Payment $756/mo", "APR · term 3.49% · 48 mo", "Compliance: ofac, red_flags, adverse_action pending". |

**Productivity shown.**

| Work | Before (spec) | After (what the agent already did) | Read it live |
|---|---|---|---|
| Prepare for the customer | The CRM can't see configurator intent, F&I readiness or trade equity; the DMS can't see browsing intent; inventory can't see preference history (spec §1.2, retail table). The advisor pulls these together by hand. | One Advisor load: 3 ranked trims with 39 in stock, equity $7,743, 3 incentives, 3 competitor cards and the next step | `get U-US-00002 /journeys/J-US-J1/advisor` |
| Answer a question about the customer | — | Agent mode chose 4 tools and answered with citations in 10,928 ms (28 Oct) | `runs assistant_agent` (recent agent-mode runs and their time) |
| Build the Sales Package | — | The Journey Agent's runtime called the three specialists: 4,327–7,858 ms per build (21–22 Oct) | `runs runtime.build_sales_package` |
| Credit compliance before delivery | — | AGREE → DELIVER refused, naming the three pending checks | Step 13 |

**Talking points.**

- The advisor decides; the agents prepare. Next best action, the test drive and
  any message are buttons a person presses, at autonomy level `recommend`.
- Answers are grounded: the objection reply is anchored to a named strategy, and
  agent mode lists the records it read.
- Fixed rules come before the model: a credit-approval question goes to a person
  without a model call, and the deal cannot leave AGREE until the F&I connector
  records OFAC, Red Flags and adverse action.
- Every model call goes through the managed Amazon Bedrock guardrail.

**If something goes wrong.**

- The objection card doesn't appear: the sentence held no objection. Use the step
  6 input exactly, or the demo transcript.
- Agent mode replies "agent unavailable": the Sales Agent runtime didn't answer, so
  the one-shot path replied. Ask again; the badge then shows "strands".
- The package call returns 202: the build is still running on the Journey Agent
  runtime; poll `GET /journeys/J-US-J1/package` until `generated_at` is newer than
  `requested_at`.

*Checked 28 Oct 2026; step 6 checked live 29 Oct.* Live: steps 1–5 (`/journeys/board`, `/journeys/J-US-J1/advisor`,
`/journeys/J-US-J1/package` header, `/journeys/J-US-J1/replay-script`), 6 (`POST
/journeys/J-US-J1/utterance-sse`, and the Advisor screen in a browser), 10 and 11
(`POST /assistant/agent`, `POST /assistant/chat`), 12 and 13 (`/journeys/J-US-00166/state`
and the refused advance, which writes nothing). Offline: step 8 (the Sales Package
build) and step 14.

---

## 4. Lead to first response

**Persona and screens.** BDC Agent Keisha Baker (`U-US-00005`). Agents: Sales Agent,
Journey Agent, Service Agent. Screens: Lead Desk, Journeys. Run time: 5 minutes.

**Why it matters.** Leads wait while someone writes the first reply. On the Lead
Desk the Sales Agent has a first response drafted the moment a lead opens, and
overnight the Journey Agent and the Service Agent add stalled journeys and
service-due owners to the desk. (The spec has no primary-sourced benchmark for
lead response time, so no industry figure is quoted here.)

**Setup.**

- Sign-in: `<console-url>/login` as `U-US-00005` with the seed password. (The
  dev-token link `?token=user:U-US-00005` still works on the dev stack.)
- The data at Decatur: 34 leads. 10 are new predictive service leads, raised on
  22 Oct; 11 were answered inside the SLA; 8 breached it; 5 are closed. Every
  seeded sales lead already has a recorded first response, so the clocks show
  "✓ responded" rather than a running countdown (see
  [Known limits](#known-limits-during-the-demo)).
- Read-only as long as you don't press **Send · email**, **Send · text**, **Call**,
  **Text**, **Email**, **Book appointment**, **Assign to floor advisor**, **Scan
  now** or **Nudge stalled**. Safe to repeat.

**Steps.**

| Step | Where (URL path / menu) | Action | Actual input | What you should see |
|---|---|---|---|---|
| 1 | Private window | Open the Lead Desk | `<console-url>/dealer/leads?token=user:U-US-00005` | "Lead Desk · first-response SLA live". Tiles "Unworked 18", "SLA breach rate 32%", "Avg first response 132 min", "Total leads 34". |
| 2 | "Inbound queue" pills | Read | — | "All", "New (10)", "SLA breached (8)", "Responded (11)", "Working (0)", "Closed (5)". |
| 3 | Pill **SLA breached (8)** → **Olivia Thomas** | Click | — | "Meridian Ion S · web_form", "“Looking to buy before end of month”", badges "SLA breached" and `U-US-00004`, clock "✓ responded". |
| 4 | Center, **Sales Agent first response** | Read | — | "✦ AI suggested first response": "Hi Olivia, thanks for reaching out about the Meridian Ion S. I saw your web_form request: “Looking to buy before end of month”. I can hold a test-drive slot and have the Meridian Ion S ready — would today or tomorrow work?" "Discovery questions to work in": "What's your timeline to decide?", "Will you be trading in a vehicle?", "Any must-have features or budget range I should know?" Source chip "Sales Agent · first_respon…". You may edit the draft; don't send it. |
| 5 | Pill **New (10)** | Click | — | Ten leads with badges such as "🔮 Predictive · Front brake pads", for Joseph Roberts and Susan Mitchell, e.g. "The Service Agent predicts front brake pads due around 40,000 mi (now 76,494 mi, confidence 97%). Offer: 15% off when booked ahead." Clock "—" (these leads have no SLA timer). |
| 6 | Agent panel | Click **Predictive part watch**, then **First-response drafter** | — | Predictive part watch: Service Agent, "Trigger: nightly, or Scan now. Last ran …(your store)" (the 06:15 UTC run). First-response drafter: Sales Agent, "Trigger: each time you open a lead." |
| 7 | Same window | Open the stalled journey | `<console-url>/dealer/journeys?token=user:U-US-00005&focus=J-US-00021` | Journeys tile "Stalled 23 · need a nudge". Drawer "Journey J-US-00021": Stage Qualify, Owner `U-US-00002`, Idle "74 d · stalled" (on 28 Oct; one more each day). Activity includes "recommendation — nurture_touch" (25 Oct, the nurture tick) and "recommendation — play:PLAY-STALLED-US" (23 Oct, the stall sweep's card), both from the Journey Agent. Don't press **Nudge stalled (23)**: it sends real follow-ups. |

**Productivity shown.**

| Work | Before (spec) | After (what the agent already did) | Read it live |
|---|---|---|---|
| Write the first reply | Leads sit in the CRM, which can't see configurator intent, F&I readiness or trade equity (spec §1.2); each reply is written by hand | A draft with the customer's own request and three discovery questions, ready when the lead opens | `curl -sS -H "Authorization: user:U-US-00005" "$API/leads/LD-US-00218/script"` |
| Find the stalled journeys | — | On 23 Oct at 05:31 UTC the sweep flagged all 23 active Decatur journeys and wrote 23 "Re-engage stalled journey" cards at `recommend` (319 across US stores), in 18,278 ms | `runs runtime.job.stall-sweep` |
| Invite owners back before a part is due | — | 10 predictive leads at Decatur, each with an offer | Pill "New (10)" |

**Talking points.**

- The first response is a draft. The person edits it and chooses the channel;
  consent is checked before anything is sent.
- The stall sweep runs every morning with no one asking, and at `recommend` it
  leaves a card on the owner's desk instead of messaging the customer.
- The predictive lead states its basis: the odometer, the wear interval and a
  confidence, so the agent can explain the call.

**If something goes wrong.**

- The page sends you to `/login`, or shows another persona: a stored sign-in is
  winning over the dev token. Use a fresh private window.
- After clicking a nav link the page loses the BDC persona: the link dropped
  `?token=`. Add `?token=user:U-US-00005` to the URL again.
- A predictive lead's draft reads "thanks for reaching out about the the vehicle
  you asked about": the drafter uses the sales template for service leads. Use
  Olivia Thomas's lead for the draft.

*Checked 28 Oct 2026.* Live, with the dev token: steps 1–5 (`/leads/queue`,
`/leads/LD-US-00218/script`, `/leads/LD-PRED-E297A09B96/script`) and step 7
(`/journeys/J-US-00021/activity`, `/journeys/board`), the stall cards (scan of
`REC-PLAY-PLAY-STALLED-US-` cards: 23 for `D-101`, 319 for the US) and the trace
times. Step 6 by reading `app/console/src/lib/agents.ts` and the `/traces/latest` data.

---

## 5. The cockpit explains itself

**Persona and screens.** General Manager Raj Nelson (`U-US-00001`). Agents:
Campaign Agent (the store digest), Journey Agent (stalled-journey nudger), Service
Agent (fixed-ops watch). Screens: Store Cockpit, Advisor, Service Cockpit. Run
time: 5 minutes.

**Why it matters.** The general manager needs sales and service read side by side,
and fixed operations carry much of the store's profit. Every Store Cockpit tab
opens with a short written reading of its figures, written overnight and labelled
with the agent and the date.

- Fixed operations generated 52.8% of total dealership gross profit in the second
  quarter of 2026.
  - Source: [Presidio-NCM Average Dealership Performance Benchmark, Q2 2026](https://thepresidiogroup.com/q2-2026-presidio-ncm-average-dealership-performance-benchmark/)

**Setup.**

- The `store-digest` job writes the insights nightly at 06:30 UTC. When a tab's
  figures haven't changed it re-checks the old text without a model call, so the
  "written … ago" age can be several days. Confirm with `runs job.store-digest`.
- The quoted insight texts are from 28 October 2026. The wording changes when
  the figures change; the figures on the tiles are what the text describes.
- Read-only. Don't press **Acknowledge** on the Alerts tab (it changes the alert).
  **Interact** on Store insight digest is disabled for the GM (see Known limits).

**Steps.**

| Step | Where (URL path / menu) | Action | Actual input | What you should see |
|---|---|---|---|---|
| 1 | `<console-url>/login` | Sign in | `U-US-00001`, the seed password | **Store Cockpit**, "Store D-101 · materialized …"; tabs Funnel, Sales, Inventory, Trade & Used, F&I, Service, CX, Alerts, OEM Comms. |
| 2 | Tab **Funnel** | Read | — | "Active journeys 23", "Stalled 23 · 100% of active", "Missing context 0", "Owned (delivered) 13". Insight "Funnel — what to do next · written by the Campaign Agent …d ago" (written 23 Oct 06:30 UTC): "All 23 active journeys are currently stalled, with the largest concentration at CONFIGURE stage (11 journeys). Walk-in converts best at 60% to delivery, so weight follow-up there first." |
| 3 | Tab **Sales** | Read | — | "Units booked (latest month) 6", "Gross (all booked) $488,904", "Avg deal $37,608", "PVR (F&I) $444". Insight "Sales — what to do next · written by the Campaign Agent …d ago" (written 22 Oct 21:53 UTC), starting "13 deals booked totaling 488,904 USD with an average deal of 37,608 USD and PVR of 444 USD…". |
| 4 | Tab **Inventory** | Read | — | "In stock 88", "Days supply 609", "Aged 60+ days 17", "In transit 11". Insight "Inventory — what to do next" with no "written by": "Demand exceeds stock for MER-ATLASX, MER-CROSSTRAIL, MER-RIDGELINE. MER-CROSSTRAIL carries the most aged units (4 over 60 days) — consider a dealer trade or a targeted incentive." This is the figures-only text: the digest guardrail holds back advice on a dealer's stocking. |
| 5 | Tab **Service** | Read | — | "Open service journeys 2", "Promise at risk 2", "Repurchase handoffs 10". Insight "Service — what to do next · written by the Campaign Agent …d ago" (written 22 Oct 21:53 UTC): "2 open service journeys with 2 projected past their promise time and a 20% close rate on recommended work. 5 declined items are queued for follow-up." Retention: "Owned vehicles 31", "Serviced here 6", "Service retention 19%". |
| 6 | Agent panel | Click **Store insight digest**, then **Stalled-journey nudger** | — | Digest: Campaign Agent, "Trigger: nightly. Last ran …(your store)" (06:32 UTC run). Nudger: Journey Agent, "Flags journeys idle past the 7-day stall SLA and puts a Callback Brief recommendation on the owning advisor's desk…", "Trigger: every morning. Last ran …(your store)" (05:30 UTC run). |
| 7 | Tab **Funnel**, table "Most stalled" | Click the row `J-US-00021` (Qualify, owner `U-US-00002`) | — | Opens the operating screen for that journey, `/dealer/advisor/J-US-00021` (customer Raj Lewis). |
| 8 | Back; tab **Service** → **Open the Service Cockpit** | Click | — | Service Cockpit, insight "Fixed-ops priorities · written by the Service Agent …d ago" (workflow 1, step 11). |

**Productivity shown.**

| Work | Before (spec) | After (what the agent already did) | Read it live |
|---|---|---|---|
| Read the store | Each system owns a silo: CRM, DMS and F&I, inventory, marketing, service scheduling, repair orders, parts (spec §1.2); the GM pulls a report from each | 9 tabs on one screen; 8 open with a Campaign Agent insight written 22–23 Oct and re-checked 27 Oct 06:30 UTC; Inventory shows figures only | `get U-US-00001 /dealer/cockpit/funnel` → `insight.written_by`, `written_at`, `checked_at` (repeat per tab) |
| Time | — | The nightly digest for every US store ran 97,922 ms on 27 Oct, finishing 06:32:24 UTC | `runs job.store-digest` |
| From a figure to the work | — | A "Most stalled" row opens that journey's Advisor screen in one click | Step 7 |

**Talking points.**

- The insight was written overnight, not on page load, and says who wrote it and
  when.
- It is grounded: the model sees only the tab's own figures, through the digest
  guardrail. When the guardrail holds a topic back, as on Inventory, the tab shows
  the figures-only text.
- The stalled-journey nudger works at `recommend`: it puts a card on the owner's
  desk and messages no one.

**If something goes wrong.**

- A tab shows no "written by": its insight aged out or was held back; the
  figures-only text is correct. Check `runs job.store-digest`.
- A tile click opens an unfiltered screen: the Funnel "Stalled" and two Service
  tab tiles link with a filter the target page ignores. Use the "Most stalled"
  rows and the Service Cockpit tiles.
- Interact on Store insight digest is disabled for the GM: known limit; skip it.

*Checked 28 Oct 2026.* Live: steps 1–5 and 7 (`/dealer/cockpit/{funnel,sales,inventory,trade,fi,service,cx,alerts,comms}`,
`/nav/resolve?type=journey&id=J-US-00021` → `/dealer/advisor/J-US-00021`) and the
digest traces. Step 6 by reading `agents.ts` against `/traces/latest`.

---

## 6. Monday district prep in AWS Quick, connected to the app

**Persona and screens.** OEM District Manager Casey Brennan (`U-US-00044`),
District 7 – Southeast (`DIST-07`). Agent: Field Sales Agent. Screens in the
console: OEM Workspace Today, AI Alerts, Accounts, AWS Quick. In AWS Quick:
the space, the chat agent, two Flows. Run time: 9 minutes.

**Why it matters.** A district manager prepares for dealer visits from many
separate reports. Here the app drafts the week's plans overnight, and AWS Quick
answers from the same live records, with a citation for each figure and the OEM
guardrails applied.

**Setup.**

- Console data: 17 accounts in the US market, all in `DIST-07` (11 dealers, 2
  dealer groups, 1 fleet account, 3 prospects); 28 open alerts. The Monday
  `district-cycle` run on 26 Oct wrote 8 draft plans in the district.
- Quick content exists ([Before the demo](#before-the-demo), last check). Sign in to
  Quick as a user in `retail-ops-oem-dm` (run) or `retail-ops-builders` (owner).
- Each Quick chat answer and each Flow run is metered in agent hours. A Flow
  schedule is private to the person who creates it and keeps running weekly.
- Nothing in the app changes. Delete the schedule after the demo unless the
  district manager wants to keep it.

**Steps.**

| Step | Where (URL path / menu) | Action | Actual input | What you should see |
|---|---|---|---|---|
| 1 | `<console-url>/login` | Sign in | `U-US-00044`, the seed password | OEM Workspace **Today**: "Good morning, Casey" (the greeting follows your clock), subtitle "… · DIST-07 · 17 dealers"; tiles "Objective % 77%", "Days Supply 61", "Dealers at Risk 11 · 8 HIGH alerts — action needed". "District priorities" insight, written by Amazon Bedrock on page load (wording varies; on 28 Oct it named Decatur Meridian, D-101, sales health 0.55, first). |
| 2 | Nav Field tools → **AI Alerts** | Open | — | "HIGH 8", "MEDIUM 7", "LOW 13", "Total 28 · showing open". |
| 3 | Agent panel → **Weekly district cycle** | Click | — | Field Sales Agent, "Trigger: Mondays, 05:00 UTC. Last ran …(your market)" (26 Oct). |
| 4 | Nav Dashboard → **Accounts** → **Decatur Meridian** | Click | (`/oem/accounts/ACC-US-00001`) | "Decatur Meridian", "dealer · DIST-07 · tier B". Tiles "Sales health 55% · -22 pts vs peers", "Service health 64%", "Days supply 55", "Open alerts 12 · 3 HIGH — action needed". "Account read-out": "Decatur Meridian is flagged at risk (csi decline lead sla). 4 plan version(s) on file; latest status published. Aligned C&I program: Lead-response SLA recovery." |
| 5 | Tab **Action Plans** → "Version history" | Click | — | `AP-US-00957` v2 published, `AP-US-00956` v1 published, `BRIEF-US-DRAFT-00001` v1 draft (written by Monday's district cycle, 26 Oct 05:00:49 UTC), `BRIEF-US-00001` v1 brief. Nothing is published without a person. |
| 6 | Nav Intelligence → **AWS Quick** | Open | (`/oem/quick`) | "Quick Flows": 15 names with ids, including the 8 Retail Ops Flows (Account Plan, Opportunity Brief, Win Strategy, Action Plan, Contact Report, Meeting Package, District Roll-up, Sales Ops Digest). "Quick Sight dashboards": 4, including "Retail Ops Campaign ROI & Funnel". |
| 7 | "Retail Ops Campaign ROI & Funnel" → **Embed** | Click | — | A "Dashboard" card with the Quick Sight dashboard in a frame (registered-user embed). |
| 8 | AWS Quick console (us-east-1) | Sign in, open **Spaces** | your Quick user | The space "Automotive Retail Operations". |
| 9 | **Chat agents** → **Retail Ops Field Sales Agent** | Open | — | Welcome "Ask about your dealers, performance, alerts, or programs. I'll cite every figure and draft plans for your review." Suggested prompts "Which of my dealers are below objective this month?", "Summarize open HIGH alerts for my district", "Draft an account plan for D-105". |
| 10 | Chat | Ask | `List my accounts and their open alerts` | *Expected, from the records it reads (not a captured transcript):* it calls `oem_list_accounts` and `oem_get_alerts` for market `us` and lists the 17 accounts and the 28 open alerts, 12 of them on Decatur Meridian (D-101), citing ids such as `ACC-US-00001` and `AL-US-00008`. If it asks for a market, answer `us`. |
| 11 | Chat | Ask | `Should we cut D-105's wholesale next month?` | *Expected, from the agent's instructions:* a refusal naming GR-001 (and GR-005 for allocations), with a performance-gap analysis offered instead. D-105 is Macon Meridian (`ACC-US-00005`), with 2 open alerts: `AL-US-00020` "Conquest share loss to competitor" (medium) and `AL-US-00016` "Aged units (>90 days) above threshold" (low). |
| 12 | Chat | Ask | `What does the EV charging bulletin say?` | *Expected:* an answer that cites `entity:COMM-US-00004` from the Retail Ops Bulletins knowledge base ("EV charging partner update", sent 22 Aug 2026). The bulletin body is one line ("EV charging partner update — details enclosed."), so the answer is short. |
| 13 | **Flows** → **Retail Ops Account Plan** → Run mode | Run | `Account ID` = `ACC-US-00001` (the default) | *Expected, from the records:* step "OEM facts" returns `ACC-US-00001` Decatur Meridian (tier B, sales health 0.55, service health 0.64, days supply 55, condition `csi_decline_lead_sla`) and its 12 open alerts, 3 HIGH (`AL-US-00008` Sales volume below district plan, `AL-US-00009` Conquest share loss to competitor, `AL-US-00025` EV days-supply above target). "Notes" returns CRM notes and prior plans. "Account plan draft" has the sections Performance vs objective, Gaps, Opportunities, Recommended actions, Risks, cites ids and calls itself a draft. |
| 14 | **Flows** → **Retail Ops District Roll-up** → Run mode → scheduling icon → **Create schedule** | Fill in, **Save** | Name `District roll-up weekly`; Repeat Weekly, Monday, 5:00 AM, `America/New_York`; `District ID` = `DIST-07` | The schedule appears under **Flows → My schedules**. |

**How the app and AWS Quick are connected today.** Say this plainly.

| Direction | What exists | What the audience sees |
|---|---|---|
| App → Quick | The console's `/oem/quick` page lists the Quick Flows (Quick `ListFlows`) and embeds the dashboard `Retail Ops Campaign ROI & Funnel` with a registered-user embed URL | Steps 6 and 7. The Flow names are a list, not links into Quick. |
| Quick → app data | The MCP connector `Retail Ops OEM Read Gateway` reaches the app's OEM data through an AgentCore Gateway, with a client-credentials token scoped `retail-ops-oem/read`. Four read tools: `oem_list_accounts`, `oem_get_account`, `oem_get_alerts`, `oem_get_incentive_programs`. Every record carries a `source_ref` (`gateway:oem-read (cross-persona via AgentCore Gateway; §6)`). | Steps 10, 11 and 13 read the same live records the console shows. |
| Quick → app writes | None. Quick does not write back into the app. Every Flow output is a draft for review inside Quick. | — |
| Chat in the console | The console has no panel for the Quick chat agent; users open the agent in Quick. The OEM Workspace's own assistant (`/oem/ask`) is a separate, Bedrock-backed assistant over the same read models. | — |

A draft-only write-back and deep links between the two are open decisions. Don't
present either as planned.

**Productivity shown.**

| Work | Before (spec) | After (what the agent already did) | Read it live |
|---|---|---|---|
| Monday plan prep | A plan per dealer is a manually assembled deck (spec §3.6, Purpose) | The 26 Oct 05:00 UTC district cycle wrote 8 draft plans in `DIST-07` at `recommend`, in 1,684 ms | `runs job.district-cycle`; Accounts → Action Plans → Version history |
| Triage the district | — | 28 open alerts grouped HIGH 8, MEDIUM 7, LOW 13 when AI Alerts loads | Step 2 |
| Facts for a Quick answer | — | 4 read tools return the live records, each with a `source_ref` | The connector smoke test in [QUICK_BUILD.md](QUICK_BUILD.md) Appendix A (the `CUSTOM_JWT` gateway section) |

**Talking points.**

- Everything the Field Sales Agent produces is a draft; it never publishes a plan,
  contacts a dealer or changes an allocation.
- The OEM guardrails GR-001 to GR-008 are in the agent's instructions, and the
  console applies the same rules to `/oem/ask`.
- Quick reads live app data through a scoped, read-only connector, and cites the
  record behind each figure.
- The app drafted the plans at 05:00 on Monday; the district manager reviews them.

**If something goes wrong.**

- The chat agent or Flows are missing in Quick: your Quick user lacks the grant.
  Re-run `deploy_quick_content --apply --only space,agent` and reload the console
  ([QUICK_BUILD.md](QUICK_BUILD.md), "After a deploy, reload the Quick console").
- A Flow step shows "Invalid reference": re-insert the `@` reference from the menu,
  then Save and Publish ([QUICK_BUILD.md](QUICK_BUILD.md), checklist step 4).
- The embed card shows an error: the embed URL is short-lived; click **Embed**
  again.

*Checked 28 Oct 2026.* Live in the console as `U-US-00044`: steps 1–6 and the
embed URL (`/oem/today`, `/oem/alerts`, `/oem/accounts/ACC-US-00001`,
`/oem/quick/flows`, `/oem/quick/dashboards`,
`/oem/quick/dashboards/retail-ops-campaign-roi-funnel/embed-url`).
`deploy_quick_content --status` listed the space, chat agent, connector, 8
published Flows and the dashboard. Through the Quick gateway with a
client-credentials token: `tools/list` (4 read tools) and `oem_get_account` for
`ACC-US-00001`, `oem_get_alerts` and `oem_list_accounts` for market `us`. Steps
9–14 can't be run from an API (no chat, Flow-run or schedule call), so their
"what you should see" is grounded in those records and in the agent and Flow
definitions in `app/infra/deploy_quick_content.py`.

---

## 7. One buyer, web lead to delivery

**Persona and screens.** Keisha Baker (BDC, `U-US-00005`) → Elizabeth Thomas (sales
advisor, `U-US-00002`) → Michael Lopez (F&I, `U-US-00006`) → Megan White (delivery,
`U-US-00009`) → the buyer in My Garage. Agents: Sales Agent, Journey Agent,
Discovery Agent, Trade-In Agent. Screens: Lead Desk, Advisor, Journeys, Trade-In,
F&I Desk, Delivery Board, My Garage. Run time: 10 minutes. This is the **New-car
purchase** scenario in the demo portal's Scenario Replay, which runs every step below
for you under the right persona; the table says what to point at.

**Why it matters.** Every desk in a dealership holds a different slice of the same
buyer. Here the same person, Priya Natarajan, is on every screen: the request she
typed into the web form, the model she asked about, the car she would trade, the unit
her deal is written against, and her own ownership view at the end.

**Setup.**
- Start from a fresh sandbox or a freshly seeded stack. Step 1 creates the customer,
  so re-running the scenario adds a second lead from the same e-mail (the same
  customer) and, once her first journey is in OWN, opens a new journey that records
  the old one as `previous_journey_id`.
- What writes: everything. The scenario creates a customer, lead, journey, build,
  deal, reserved VIN, delivery and vehicle record. Run it on a sandbox or reseed
  afterwards.

**Steps.**
| Step | Where (URL path / menu) | Action | Actual input | What you should see |
|---|---|---|---|---|
| 1 | Demo portal → **New-car purchase** → **Start** (Keisha) | Run step | web form: Priya Natarajan, Ion C, "Is the Ion C Extended in stock in blue or white?", budget $32,000–$42,000 and ≤$560/mo, a 2019 Kestrel Trek 4 (61,000 mi, $8,400 loan payoff) | `/dealer/leads?lead=LD-US-WEB-…`: New (+1); the card shows "Priya Natarajan · Meridian Ion C · Web form" and a running first-response clock (30 min). Center: the request is quoted in the draft. The activity strip reads "Received just now · Web form · first response due hh:mm UTC" and "No first response yet — the SLA clock is running". |
| 2 | Same lead | Run step | the A3 draft, sent by e-mail | Strip: "First response sent just now by email · 0 min after receipt"; the card moves to Working. Agent bar → Activity: "a3.draft_first_response", "a3.send_first_response". Offline the e-mail is queued as a dealer task. |
| 3 | Same lead | Run step | assign to Elizabeth Thomas, book a test drive | Toasts say ownership moves when Elizabeth accepts. Strip: "Journey J-US-LEAD-… · DISCOVER · owner Keisha Baker" with the badge "→ Elizabeth Thomas · handoff pending", and "Test drive booked …". Journeys: the card footer reads "Keisha Baker → Elizabeth Thomas (pending)". |
| 4 | `/dealer/advisor/J-US-LEAD-…` | Run step (Elizabeth) | accept the handoff, present the top trim, save it as the build | Ownership row: "Elizabeth Thomas" (no pending badge). Matches: "Customer asked about the Meridian Ion C (from the lead) — ranked first when it fits", Ion C Standard first with the tags "asked for", "budget fit", "in stock". Stage CONFIGURE. |
| 5 | Same screen, `?say=` | Run step | "Honestly, that monthly payment is more than we wanted to spend." | Detected objection "payment too high"; the reply uses her budget and the Ion C's range. |
| 6 | `/dealer/journeys?focus=J-US-LEAD-…` | Run step | advance CONFIGURE → QUALIFY → FINANCE | The card moves to Finance; each move writes a stage event, and the handoff gate named the missing context before it opened. |
| 7 | `/dealer/trade-in?journey=J-US-LEAD-…` | Run step | value the trade on the journey | No VIN prompt: "From journey J-US-LEAD-… · Priya Natarajan". 2019 Kestrel Trek 4; trade value $24,954, loan payoff $8,400, equity $16,554 (a first appraisal anchored to nine comparable Trek 4 appraisals, mileage-adjusted; the source line says so). |
| 8 | `/dealer/fni/DL-US-…` (Michael) | Run step | desk from the build, lender decision, advance, reserve a VIN, push to DMS | Worksheet: "$34,000 vehicle + $2,513 est. tax = $36,513"; the captive lender's offer reads 2.9% · 60 mo with the badge "OEM APR special"; the incentive row says "2.9% APR applied"; the loan-interest deduction and the charger rebate are labelled informational. Before a VIN is reserved the vehicle card says "Reserve a VIN · required before the DMS push and delivery" and **Push to DMS** is disabled. After: "Booked in DMS" and the VIN. |
| 9 | `/dealer/journeys?focus=…` | Run step | close AGREE → DELIVER | The card moves to Deliver; a delivery card and a handoff to Megan White are created. |
| 10 | `/dealer/delivery` (Megan) | Run step | prep complete (including the charger install), docs signed, customer confirmed, mark delivered | "Priya Natarajan" with her VIN under Delivered; the journey moves to OWN. |
| 11 | `/garage` (Priya, her own token) | Run step | — | "Welcome, Priya"; 1 vehicle (Meridian Ion C), journey stage OWN, a delivery survey, and a service visit already opened by the Service Agent. |

**Productivity shown.**
| Work | Before (spec) | After (what the agents did) | Read it live |
|---|---|---|---|
| Carry the request to the floor | The BDC's notes live in the CRM; the advisor re-asks | The lead's model and words ride into the journey, and the matches lead with the model she asked about | `get U-US-00002 /journeys/{id}/advisor` → `matches.requested_model_name` |
| See what happened on a lead | Check three systems | One strip on the card and the agent bar's Activity feed for the lead | `get U-US-00005 /traces?lead_id={id}` |
| Value a trade the customer typed in | Look it up by VIN | A first appraisal from comparable appraisals, with its source | `post U-US-00002 /trade-in/valuation {customer_id}` |
| Make the deal add up | Reconcile the price, tax, incentives and rate by hand | `price.breakdown` with a reconciles flag; the APR program applied only when the buyer's tier qualifies | `get U-US-00006 /fni/deals/{id}` |

**Talking points.**
- The agents prepare and a person presses each button: the reply, the handoff
  accept, the VIN reservation and the DMS push are all human actions.
- Ownership moves only when the receiver accepts. Until then the card says who the
  journey is on its way to.
- An OEM APR program is a rate, not a discount: the worksheet shows the rate on the
  lender's offer and says why it did or did not apply (credit tier, term).
- No VIN, no booking: the deal is written against a unit, so it cannot reach the DMS
  or delivery without one.

**If something goes wrong.**
- Step 8 reports "no in-stock unit to reserve": the store has no unit of the trim she
  was shown. Reserve one of the same model in Inventory (the F&I link prefills the
  journey), then push again.
- The replay stops on a step: open **History** on the replay bar; each call is listed
  with its error.
- Step 11 shows a different customer: the replay swaps to the buyer's own token, so
  sign out of any stored session before starting.

*Checked 3 Nov 2026 on the local sandbox (`RETAIL_OPS_LOCAL_DATA=1`).* All eleven
steps ran through the Scenario Replay with no failed call and were captured in a
browser; the same sequence is covered by `tests/app/test_sales_floor_api.py`
(`test_one_buyer_follows_the_lead_through_assign_matches_trade_and_the_desk`).
*Run on the dev stack 3 Nov 2026* after the reseed and redeploy, by
`python3 -m app.demo.run_demo --workflow7 --api <api-base>` (also a `deploy/verify.sh`
check): all eleven steps under their personas, every expectation above held — the
first response queued as a dealer task (no SES sender configured), the Trek 4 at
$24,954 / $8,400 / $16,554, the deal at $36,513 with the 2.9% APR program applied
for a Prime buyer, the DMS push refused until a VIN was reserved, delivery to OWN, and
the service visit opened. The check uses a throwaway buyer and removes its rows, so
Priya's story stays free for the presenter.

---

## Known limits during the demo

Route the demo around these. Each was seen in the code or the dev stack on 28
October 2026.

| Limit | Where | Route around it |
|---|---|---|
| The General Manager has no Campaign Agent chat: `/marketing/ask` refuses `dealer_manager`, so **Interact** on Store insight digest is disabled ("Not available for your role") | Workflow 5 | Read the insight on the tab. |
| The OEM CX Lead has no Journey Agent chat: `/assistant/chat` refuses OEM roles, so **Interact** on the Journey Agent cards is disabled ("Not available for your role") | Not in these workflows | Don't demo `U-US-00050` chat. |
| On the Assistant page, the First-response drafter's **Run now** runs "Draft follow-up", not the lead's first response | Assistant page | Show first responses on the Lead Desk (workflow 4). |
| The morning-work, lane-monitor and stall-play cards are stored as recommendation records; no console screen lists them | Workflows 1, 2, 4 | Show "last ran" on the agent panel, the tiles they explain, and the terminal counts. The stall cards do appear in the journey drawer's Activity. |
| The dispatch card can name a service advisor (the roster includes the service-advisor role), and the person named can change between runs | Workflow 1 | Say "a recommended technician"; don't name one in advance. |
| The shop-load run doesn't pre-draft estimate lines or auto-present estimates; the check-in brief is assembled when the page opens, so its "Prepared" time is the page-load time | Workflow 1 | Present the brief as ready on arrival, not as written at 05:00. |
| No play is seeded for `service.handoff_to_sales`, so the Full Prep step finds nothing to run; the preparation is the Advisor screen the sales advisor opens | Workflow 2 | Show the Advisor screen (step 9). |
| The hand-off's owner and task go to the repair order's advisor (Joseph White, `U-US-00004`, who has no sign-in), not to the sales advisor you sign in as | Workflow 2 | Say so; Elizabeth Thomas sees the journey on the store's board. |
| The Service Lane hides **Hand to sales** when the owner already has a sales journey, which is true for both open Decatur visits | Workflow 2 | Use the repair order view for `SJ-US-00005`. |
| The Advisor screen doesn't call the Journey Agent's Sales Package, and no button does | Workflow 3 | Optional terminal step 8. |
| On the live stack the next best action is picked by Amazon Bedrock among candidate actions, so it can differ from the offline pick | Workflows 2, 3 | Read the action from the screen. |
| No seeded Decatur sales lead lacks a first response, so no countdown runs on the Lead Desk. *Fixed in the 3 Nov build:* `POST /leads` creates a lead with a running 30-minute clock (workflow 7, step 1) | Workflow 4 | Until the redeploy, show the breach rate, the average response time and the draft; after it, post a lead. |
| Predictive service leads get the sales first-response template ("…about the the vehicle you asked about … test-drive slot…") | Workflow 4 | Draft from Olivia Thomas's lead. |
| ~~The BDC user has no Cognito login~~ | Workflow 4 | Fixed 4 November 2026: every staff persona the demo uses (BDC, F&I, inventory, delivery, regional, C&I, OEM finance, marketing) signs in at `/login` with the seed password. |
| The journey drawer's Activity shows the acting agent's internal code (for example A2) and raw action ids such as `play:PLAY-STALLED-US` | Workflows 2, 4 | Name the agent aloud. |
| Store Cockpit links: the Funnel "Stalled" tile opens Journeys without the filter, and the Service tab's "Awaiting approval" and "Promise at risk" tiles use a filter the Service Board ignores | Workflow 5 | Use the "Most stalled" rows and the Service Cockpit tiles. |
| The Inventory tab always shows the figures-only text: the digest guardrail treats stocking advice as a denied topic | Workflow 5 | Present it as the guardrail working. |
| The dev data's dates are fixed at the seed time (10 Sep 2026), so promises are from August and an estimate reads "presented 104777 min ago" (and counting) | All | Say the data is a fixed snapshot; the agents act on it every night. |
| `/oem/quick` lists every Flow and dashboard in the Quick account (15 Flows, 4 dashboards, including Quick's samples), as names without links | Workflow 6 | Point at the eight Retail Ops Flows and the one Retail Ops dashboard. |
| The account page's "District Sales vs Target" card is a placeholder ("QuickSight embed placeholder"), not a Quick dashboard | Workflow 6 | Use the `/oem/quick` embed. |
| The Quick connector's token is one OEM service principal with read-across, so "my accounts" returns every account in the market, not one person's scope | Workflow 6 | Say that scoping on this path is the agent's account filter. |
| The Quick chat answers and the Flow run are not yet confirmed in the console ([QUICK_BUILD.md](QUICK_BUILD.md) checklist steps 3 and 4: cited answers, the GR-001/GR-005 refusal and Flow 1's run are open) | Workflow 6 | Rehearse steps 10–13 once before the demo. |
| Quick doesn't write back into the app, and the console has no Quick chat panel | Workflow 6 | State it as it is. |

## Changes in the 3 Nov build

These change what workflows 1–6 show once the build is deployed and the stack is
reseeded. Their quoted figures stay as checked until then.

| Where | Change | Effect on this document |
|---|---|---|
| Lead Desk | `POST /leads` intake; a lead activity strip; a named advisor picker; ownership shows "→ advisor · handoff pending" until accepted | Workflow 4: a running countdown is now possible; the "Assign to floor advisor" step takes a name, not an id |
| Advisor / Discovery | A lead's model is copied into the journey and ranked first | Jordan Alvarez has no lead, so this build does not change his matches. They already differ from workflow 3, step 4 on the committed seed (the third trim is Ion C Extended AWD, and his trade is $20,116 with no payoff, not $7,743), a change that predates this build and reaches the dev stack with the reseed |
| Seed | A lease's payoff is its buyout (not $0); a saved build's total subtracts only point-of-sale incentives | The leased Ion C owner (storyline J6, Sam Kowalski) shows a $16,099 buyout and $3,066 equity (was $0 and $19,165); Elena Rossi's build and deal totals are $50,700 (were $46,700); her 9.8% rate stays, now labelled a Near Prime estimate |
| F&I | A deal needs a reserved VIN to book; the finance card of a journey shows only its own deal | Workflow 3, step 14: a journey with no deal of its own reads "No finance scenario yet" instead of another deal's figures |
| Trade-In | `/dealer/trade-in?journey=` values that journey's owner | The Advisor's trade card links to it (sales roles with Trade-In access) |

## Reset after the demo

| Workflow | Writes | Reset |
|---|---|---|
| 1 | Only `--run-once`, which rewrites the same card ids as the 10:00 UTC schedule | None. |
| 2 | Journey `J-US-SVC-00005`, hand-off `HANDOFF-SJ-US-00005`, its task, the `service.handoff_to_sales` event on `SJ-US-00005`, "shown" records and a Sales Package for the new journey | Required before the next run: the script below. |
| 3 | Sales Package `SP-J-US-J1` (optional step 8); "shown" records from page loads and objection checks | The script below removes the package. The "shown" records stay (normal page use). If you pressed **Use This** or **Alternative**, that outcome stays in the success rate. |
| 4, 5 | Nothing, if you pressed none of the buttons the setup lists | None. |
| 6 | The Quick schedule `District roll-up weekly` | In Quick, **Flows → My schedules** → delete it, unless it stays. |

Check for leftovers (read-only; each line prints `absent` or `present`):

```bash
chk() { echo -n "$1 $3: "; aws dynamodb get-item --region us-east-1 --table-name "retail-ops-dev-$1" \
  --key "{\"$2\":{\"S\":\"$3\"}}" --output json | python3 -c \
  "import sys,json; t=sys.stdin.read().strip(); print('present' if t and json.loads(t).get('Item') else 'absent')"; }
chk DEALER_Journeys journey_id J-US-SVC-00005
chk DEALER_Handoffs handoff_id HANDOFF-SJ-US-00005
chk DEALER_SalesPackages package_id SP-J-US-J1
```

Remove the workflow 2 and 3 writes. This deletes only rows the demo created; the
seeded rows for `SJ-US-00005` and `J-US-J1` stay. Agent traces stay as history.

```bash
set -a; . deploy/config.env; set +a
PYTHONPATH=. .venv/bin/python - <<'EOF'
import boto3
from app.services.common import ddb

c = boto3.client("dynamodb", region_name="us-east-1")


def rm(table, **key):
    c.delete_item(TableName=ddb.table_name(table), Key={k: {"S": v} for k, v in key.items()})


SJ, JID = "SJ-US-00005", "J-US-SVC-00005"          # workflow 2: the hand-off
for t in ddb.scan_filter("DEALER_Tasks", "journey_id", JID):
    rm("DEALER_Tasks", task_id=t["task_id"])
for n in ddb.scan_filter("DEALER_Notifications", "journey_id", JID):
    rm("DEALER_Notifications", notification_id=n["notification_id"])
for r in ddb.scan_filter("DEALER_Recommendations", "journey_id", JID):
    rm("DEALER_Recommendations", recommendation_id=r["recommendation_id"])
for e in ddb.query_hash("DEALER_JourneyEvents", "journey_id", JID):
    rm("DEALER_JourneyEvents", journey_id=JID, sk=e["sk"])
for e in ddb.query_hash("DEALER_ServiceJourneyEvents", "service_journey_id", SJ):
    if e.get("event_type") == "service.handoff_to_sales":
        rm("DEALER_ServiceJourneyEvents", service_journey_id=SJ, sk=e["sk"])
rm("DEALER_Handoffs", handoff_id="HANDOFF-" + SJ)
rm("DEALER_SalesPackages", package_id="SP-" + JID)
rm("DEALER_Journeys", journey_id=JID)
rm("DEALER_SalesPackages", package_id="SP-J-US-J1")   # workflow 3: the Sales Package build
print("reset done")
EOF
```

Run `chk` again: all three lines print `absent`. The reset was tested in local
data mode after running workflows 2 and 3 there: afterwards only agent traces
differed from the seed, and a second hand-off created `J-US-SVC-00005` again.
