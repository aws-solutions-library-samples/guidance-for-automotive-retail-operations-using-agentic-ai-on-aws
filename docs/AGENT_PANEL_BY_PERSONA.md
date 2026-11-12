# Agent panel by persona

Which agents work for each persona, on which screens, and how each automation on
the agent panel saves that person time. Companion to
[ACCESS_BY_PERSONA.md](ACCESS_BY_PERSONA.md) (demo logins, routes, access rules).
The source of truth is the code: the panel catalog in
`app/console/src/lib/agents.ts`, the persona screens in
`app/services/common/personas.py`, the Run-now buttons each page publishes, and the
scheduled jobs in `app/infra/deploy_l6.py`. As of 2026-10-22.

## How to read the agent panel

The agent panel is the strip of cards above every signed-in screen. Each card is
one automation: a task an agent does for this persona.

| Card part | Meaning |
|---|---|
| Agent name | The agent that owns the task (codes such as A3 never show to users) |
| **Live** | Runs with no click, when its screen loads or on the event named |
| **Scheduled** | A job on an enabled schedule, run for both markets (US, Germany) |
| **On demand** | Runs when you press its button |
| Run now | A button the current page offers for that automation (only on some pages) |
| Interact | Opens a chat with that agent, grounded on what the page has open. Disabled ("Not available for your role") where that agent's chat refuses your role |
| Last ran | The newest run: for the open journey or repair order, else for your store or market. The date shows when it was not today |

**Engine** in the tables below says what does the work:

- **Rules:** fixed logic over the system's records; no model.
- **Rules + Bedrock wording:** the logic is fixed; in the deployed system Amazon
  Bedrock writes the explanation or picks among the candidate actions.
- **Bedrock:** Amazon Bedrock writes the result from the records, through a
  guardrail.

Scheduled jobs run at the autonomy level the tenant publishes (Admin › Autonomy).
Today every one is at `recommend`: they write recommendation cards, leads and
drafts for a person to act on and send nothing to a customer.

### The seven agents

| Agent | Area | Where it runs |
|---|---|---|
| Discovery Agent | vehicle matching and demand | AgentCore runtime `retail_ops_dev_a1_discovery` (rules) |
| Journey Agent | the purchase journey: next step, outreach, nurture, delivery | AgentCore runtime `retail_ops_dev_a2_journey` (rules; calls Discovery, Sales and Trade-In for the Sales Package) |
| Sales Agent | the floor and F&I desk | AgentCore runtime `retail_ops_dev_a3_sales`, the one agent that reasons with a model (Strands on Bedrock), used by the Assistant page's agent mode |
| Trade-In Agent | trade value, equity, used-stock decisions | AgentCore runtime `retail_ops_dev_a4_trade_in` (rules) |
| Campaign Agent | campaign ROI, budgets, audiences, the store digest | AgentCore runtime `retail_ops_dev_a5_campaign` (rules); the nightly digest uses Bedrock |
| Field Sales Agent | the OEM district manager's dealers, alerts and plans | no runtime yet (planned in Amazon Quick); answers through `/oem/ask` with Bedrock |
| Service Agent | the service visit end to end | AgentCore runtime `retail_ops_dev_a7_service` (rules) |

## Persona overview

| Persona (role) | App · home screen | Agents on the panel | Cards | Chat (Interact) |
|---|---|---|---|---|
| Sales Advisor (`dealer_advisor`) | Dealer Console · Journeys | Sales, Journey, Trade-In | 5 | all three |
| BDC Agent (`dealer_bdc`) | Dealer Console · Lead Desk | Sales, Journey, Service | 5 | all three |
| F&I Manager (`dealer_fni`) | Dealer Console · F&I Desk | Sales | 4 | Sales |
| Inventory Manager (`dealer_inventory`) | Dealer Console · Inventory & Trade Desk | Discovery, Trade-In | 3 | both |
| Delivery Coordinator (`dealer_delivery`) | Dealer Console · Delivery Board | Journey | 3 | Journey |
| General Manager (`dealer_manager`) | Dealer Console · Store Cockpit | Campaign, Journey, Service | 3 | Journey, Service; Campaign disabled (see known gaps) |
| Service Advisor (`dealer_service`) | Dealer Console · Service Board | Service | 7 | Service |
| Service Technician (`dealer_technician`) | Dealer Console · My ROs | Service | 3 | Service |
| Parts Specialist (`dealer_parts`) | Dealer Console · Parts desk | Service | 2 | Service |
| Service Manager (`dealer_service_manager`) | Dealer Console · Service Cockpit | Service | 2 | Service |
| OEM District Manager (`oem_dm`) | OEM Workspace · Today | Field Sales | 5 | Field Sales |
| OEM Regional Lead (`oem_regional`) | OEM Workspace · Region View | Field Sales | 5 | Field Sales |
| OEM C&I Planner (`oem_ci_planning`) | OEM Workspace · Accounts | Field Sales | 3 | Field Sales |
| OEM Sales Ops (`oem_sales_ops`) | OEM Workspace · Sales Ops Comms | Field Sales | 2 | Field Sales |
| OEM Finance Partner (`oem_finance`) | Marketing Studio · Campaigns | Campaign, Field Sales | 3 | both |
| OEM Marketing Lead (`oem_marketing`) | Marketing Studio · Campaigns | Campaign | 2 | Campaign |
| OEM CX Lead (`oem_cx`) | Marketing Studio · Journey Ops | Journey | 3 | none; Journey disabled (see known gaps) |
| Customer | My Garage | Discovery, Journey, Service | 3 | all three, through the shop chat |
| Administrator (`platform_admin`) | Admin Console · Overview | all seven | 48 | Campaign, Field Sales, Service |
| Tenant Admin (`admin_tenant`) | Admin Console · Overview | all seven | 48 | Service |
| Dealer Admin (`admin_dealer`) | Admin Console · Overview | the six store agents | 36 | Service |

51 different automations in total: 36 for store staff, 12 for OEM staff and 3
for customers. The admin panels list the staff ones as a view.

**Productivity and KPIs.** Each automation below names the success metric it is
built to move, from the spec's KPI table
([§1.3](specs/intelligent-sales-service-orchestration.md)). This document adds no
numbers of its own. Most §1.3 baselines are marked *estimate* and are re-baselined
with the pilot dealer's own data in Phase 0; the two with a primary source are
F&I products per deal (1.57, StoneEagle Q2 2025 F&I Performance Report) and
in-warranty service retention (54%, Cox Automotive Service Industry Study 2025).

## Dealer sales floor

### Sales Advisor (`dealer_advisor`)

Screens: Journeys (home), Advisor (live), Discovery, Assistant, Lead Desk, Trade-In,
Service Lane, Customer 360, Tasks. The panel is busiest on **Advisor (live)**, the
journey view used with the customer in front of you. On the Lead Desk (decided
2026-10-28) the advisor works leads with the page's own buttons (send, assign,
book); the Lead Desk automation cards are the BDC's.

| Automation | Agent | Trigger | Where it works · Run now | Engine | How it saves time | KPI it moves |
|---|---|---|---|---|---|---|
| Vehicle matcher | Sales | Live: each time you open the journey | Advisor (live) | Rules + Bedrock wording | Ranked trims and in-stock VINs for this customer, with the reasons, before you start searching | Show → close rate |
| Objection coach | Sales | Live: while you listen, about every 3 s | Advisor (live) | Rules + Bedrock wording | Spots the objection as the customer says it and offers a response with its recorded success rate | Show → close rate |
| Next best action | Journey | Live: on open and on each stage change | Advisor (live) · Run now "Do it: …" | Rules + Bedrock wording | One-click next step (test drive, finance link, follow-up) instead of working out what is due | Contact → appointment rate; journey stage drop-off |
| Trade equity watch | Trade-In | Scheduled: nightly, and on open | Advisor (live) | Rules (nightly scan) + Bedrock wording (on screen) | Equity and payment delta are already worked out; the nightly scan flags journeys that turn equity-positive or near lease end | Trade-in captured in same journey |
| Follow-up drafter | Journey | On demand: after a visit or call | Advisor (live) · Run now "Draft & send"; Assistant · "Draft follow-up" | Rules | The personalized follow-up is drafted and sent on the customer's consented channel in one click | Contact → appointment rate |

### BDC Agent (`dealer_bdc`)

Screens: Lead Desk (home), Assistant, Journeys, Advisor (live), Customer 360, Tasks.

| Automation | Agent | Trigger | Where it works · Run now | Engine | How it saves time | KPI it moves |
|---|---|---|---|---|---|---|
| First-response drafter | Sales | Live: each time you open a lead | Lead Desk | Rules | A ready first reply (vehicle, request, test-drive offer, qualifying questions), so the lead is answered inside its SLA | Lead response time; lead → contact rate |
| Shift call list | Journey | On demand: at shift start | Lead Desk, Assistant · Run now "Publish to Tasks" | Rules | Today's calls in priority order with the reason for each, published as Tasks | Lead → contact rate |
| Appointment booker | Journey | On demand: one click per lead | Lead Desk · "Book appointment"; Assistant · "Book test drive" | Rules | Books the test drive on the lead's journey without re-keying | Contact → appointment rate |
| Predictive part watch | Service | Scheduled: nightly, or Scan now | Lead Desk · Run now "Scan now" | Rules | Service-due leads with a customer offer arrive on the desk ready to call | In-warranty service retention |
| Lead router | Journey | On demand: on assign | Lead Desk · "Assign to …" | Rules | Assigns the lead and opens its journey; a change of owner hands over with a Task, so context is not lost | Configurator → deal-desk handoff completeness |

### F&I Manager (`dealer_fni`)

Screens: F&I Desk (home), deal worksheet, Journeys, Delivery Board, Tasks. Every
card works on the **deal worksheet**.

| Automation | Agent | Trigger | Where it works · Run now | Engine | How it saves time | KPI it moves |
|---|---|---|---|---|---|---|
| Deal structurer | Sales | Live: each time the worksheet loads | Deal worksheet · Run now "Rebuild scenarios" | Rules | Term, rate and payment scenarios across lender offers, with the math shown | F&I products per deal |
| Menu attach recommender | Sales | Live: each time the worksheet loads | Deal worksheet | Rules | Likelihood and reason for each F&I product, so the menu leads with what fits | F&I products per deal |
| Lender decision request | Sales | On demand: when you submit the credit application | Deal worksheet · "Request decision" | Rules (sandbox connector) | Runs OFAC and Red Flags checks, submits to the lender panel and records every decision in one step | — |
| Compliance sentry | Sales | Live: on load and before the DMS push | Deal worksheet | Rules | Checks disclosures, state doc-fee rule and adverse-action status, and blocks the push until clear | — |

### Inventory Manager (`dealer_inventory`)

Screens: Inventory & Trade Desk (home), VIN detail, Trade-In, Discovery, Store
Cockpit (Inventory and Trade tabs), Tasks.

| Automation | Agent | Trigger | Where it works · Run now | Engine | How it saves time | KPI it moves |
|---|---|---|---|---|---|---|
| Demand scorer | Discovery | Live: each time the grid loads | Inventory grid | Rules | Active-journey demand next to every model and trim, so hot and aging stock stand out | — |
| Disposition recommender | Trade-In | Live: each time the Trades tab loads | Trades tab | Rules | Keep-for-CPO, retail, wholesale or auction for every accepted trade, with the reasoning | Trade-in captured in same journey |
| Acquisition scout | Trade-In | Live: each time the Acquisitions tab loads | Acquisitions tab | Rules | Service-drive vehicles worth buying, with an offer range | Repeat / upgrade journeys sourced from OWN |

### Delivery Coordinator (`dealer_delivery`)

Screens: Delivery Board (home), Journeys, Tasks.

| Automation | Agent | Trigger | Where it works · Run now | Engine | How it saves time | KPI it moves |
|---|---|---|---|---|---|---|
| Reschedule notifier | Journey | Live: when you change a delivery slot | Delivery Board · Run now "Re-notify slot" | Rules | The customer hears about the new time on their preferred channel without a separate message | — |
| Delivery-day messenger | Journey | On demand: from the Delivery Board | Delivery Board · Run now "Send message" | Rules | Checklist, document status and arrival details sent in one click | — |
| Post-delivery survey | Journey | Live: when you mark the vehicle delivered | Delivery Board | Rules | Closes the journey (DELIVER → OWN), sends the survey and opens the first service journey | In-warranty service retention |

### General Manager (`dealer_manager`)

Screens: every Dealer Console screen; home is the **Store Cockpit** (Funnel,
Sales, Inventory, Trade & Used, F&I, Service, CX, Alerts, OEM Comms tabs), plus
Approvals.

| Automation | Agent | Trigger | Where it works · Run now | Engine | How it saves time | KPI it moves |
|---|---|---|---|---|---|---|
| Store insight digest | Campaign | Scheduled: nightly, 06:30 UTC | Store Cockpit, every tab's [AI INSIGHT] | Bedrock (store-digest guardrail) | Each tab opens with a written summary of what the numbers say and the action they point to; until one is written the tab shows the figures-only summary | Journey stage drop-off and the tab's own KPIs |
| Stalled-journey nudger | Journey | Scheduled: every morning, 05:30 UTC | Journeys · Run now "Nudge stalled (n)" | Rules | Journeys idle 7+ days get a Callback Brief recommendation on the owner's desk; Run now sends a follow-up to the stalled journeys shown | Journey stage drop-off |
| Fixed-ops watch | Service | Live: each time the cockpit or service desk loads | Store Cockpit Service tab; Service Board | Rules | ROs past their promise, on parts hold or waiting on approval, counted where you look | Promised-time adherence |

## Dealer service desk

### Service Advisor (`dealer_service`)

Screens: Service Board (home), repair order view, estimate, service history, book
service, check-in, Declined work, Service Lane, Trade-In (valuation and upgrade
prospects; adding the trade to a purchase journey stays with sales), Assistant, Tasks.

| Automation | Agent | Trigger | Where it works · Run now | Engine | How it saves time | KPI it moves |
|---|---|---|---|---|---|---|
| Morning dispatch | Service | Scheduled: every morning, 05:00 local | Service Board (recommendation cards) | Rules | Technician picks, parts to stage and promise checks are ready before the lane opens | Advisor morning work done before shift start; RO cycle time |
| Estimate builder | Service | On demand: after the inspection | Repair order view | Rules | Yellow and red findings become a priced estimate with a source on every number | Findings presentation rate |
| Approval chaser | Service | On demand: when an estimate waits on the customer | Service Board · Run now "Re-present (n)"; RO view · "Re-present" | Rules | Re-sends waiting estimates by secure link in one click | Estimate approval turnaround; approval rate when presented |
| Promise re-forecaster | Service | Scheduled: every morning, with Morning dispatch | Service Board | Rules | Flags ROs already running past the promise, so the customer hears early | Promised-time adherence |
| Declined-work follow-up | Service | Scheduled: hourly, on the 30 / 60 / 90-day cadence | Declined work; recommendation cards | Rules | Due reminders re-present the original finding; at `recommend` they land on your desk to send | Declined-work follow-up conversion |
| Predictive part watch | Service | Scheduled: nightly | Leads for the BDC to work; My Garage offers | Rules | Customers are invited back before a part is due | In-warranty service retention |
| Repurchase handoff | Service | Scheduled: nightly, and Hand to sales | RO view · Run now "Hand to sales" | Rules | Equity-positive owners in follow-up are flagged; Hand to sales opens the sales journey with the context | Repurchase/upgrade handoffs per 100 ROs |

### Service Technician (`dealer_technician`)

Screens: My ROs (home), Service Board, repair order view, inspection, Tasks.
Customer contact details are masked for this role.

| Automation | Agent | Trigger | Where it works · Run now | Engine | How it saves time | KPI it moves |
|---|---|---|---|---|---|---|
| DTC diagnoser | Service | On demand: on DTC entry | Repair order view | Rules | Description, likely labor operations, tests and any indexed TSB without leaving the bay | RO cycle time |
| Draft-line builder | Service | On demand: per confirmed cause | Repair order view | Rules | Procedure, labor time and parts for the cause go straight into a draft estimate line | Findings presentation rate |
| Evidence checker | Service | Live: when you submit the inspection | Inspection | Rules | Yellow and red items cannot be submitted without photo, video or voice evidence, so nothing comes back for rework | Inspection (MPI) completion rate; approval rate when presented |

### Parts Specialist (`dealer_parts`)

Screens: Parts desk (home), Service Board, repair order view, Inventory, Tasks.

| Automation | Agent | Trigger | Where it works · Run now | Engine | How it saves time | KPI it moves |
|---|---|---|---|---|---|---|
| Parts ETA tracker | Service | Live: each time the parts desk loads | Parts desk | Rules | Open orders sorted by ETA and held ROs grouped, soonest first | Parts-wait ROs and hours lost |
| Repair resumer | Service | Live: when you mark a part received | Parts desk | Rules | The RO's parts hold clears the moment its last part arrives, so the board shows it ready | Parts-wait ROs and hours lost; RO cycle time |

### Service Manager (`dealer_service_manager`)

Screens: every service screen; home is the **Service Cockpit**; also the Store
Cockpit Service tab, Service Lane, Tasks, Approvals.

| Automation | Agent | Trigger | Where it works · Run now | Engine | How it saves time | KPI it moves |
|---|---|---|---|---|---|---|
| Cockpit insight | Service | Scheduled: nightly, 06:30 UTC | Service Cockpit [AI INSIGHT] | Bedrock (store-digest guardrail) | A written read of the fixed-ops figures and the next action; the figures-only summary shows while a what-if slider is moved | the cockpit's KPIs (close rate, parts wait, retention) |
| Threshold what-if | Service | On demand: on slider change | Service Cockpit · Run now "Recompute" | Rules | Labor-rate, close-rate and repair-vs-replace what-ifs on current lane data, with the formula shown | Repurchase/upgrade handoffs per 100 ROs |

## OEM Workspace and Marketing Studio

### OEM District Manager (`oem_dm`) and Regional Lead (`oem_regional`)

Screens: every OEM Workspace screen (Today, Dealer Performance, Composite, Region
View, Accounts, Contact Reports, Action Plans, AI Alerts, Weekend Tracker, Meeting
in a Box, Scenario, Conquest, EV Readiness, Compliance, Campaign ROI, Comms,
Reports, AI Guardrails), the dealers' Store Cockpits, Approvals. Both roles have
the same five cards; the regional lead sees the region and decides plan approvals.

| Automation | Agent | Trigger | Where it works · Run now | Engine | How it saves time | KPI it moves |
|---|---|---|---|---|---|---|
| Weekly district cycle | Field Sales | Scheduled: Mondays, 05:00 UTC | Action Plans (drafts); Today | Rules | The week starts with the gaps rolled up and a draft plan for every dealer below the health threshold; nothing publishes without you | — |
| Alert triage | Field Sales | Live: each time AI Alerts or Today loads | AI Alerts; Today | Rules | Open dealer alerts grouped HIGH / MEDIUM / LOW, newest first | — |
| Account-plan drafter | Field Sales | On demand | Accounts; Action Plans | Rules | A sourced draft plan or brief to edit and publish, instead of a blank page | — |
| Meeting in a Box | Field Sales | On demand: before a meeting | Meeting in a Box | Rules + Bedrock wording | Agenda, scorecard and talking points for the meeting type, from live district data | — |
| District Manager Assistant | Field Sales | On demand: when you ask | Workspace assistant; agent panel Interact | Bedrock | Questions about your dealers answered from live read models, with a citation per figure | — |

### OEM C&I Planner (`oem_ci_planning`)

Screens: Accounts (home), account detail, plans, Scenario Planning, Approvals.

| Automation | Agent | Trigger | Where it works · Run now | Engine | How it saves time | KPI it moves |
|---|---|---|---|---|---|---|
| Account-plan drafter | Field Sales | On demand | Accounts | Rules | As for the district manager | — |
| Scenario planner | Field Sales | On demand: on slider change | Scenario Planning | Rules | Volume, objective % and share for allocation, incentive, days-supply and growth changes, labelled *estimate* | — |
| District Manager Assistant | Field Sales | On demand | Agent panel Interact | Bedrock | As for the district manager | — |

### OEM Sales Ops (`oem_sales_ops`)

Screens: Sales Ops Comms (home), Report Library. No Admin Console (decided
2026-10-28: the admin routes serve the admin roles only).

| Automation | Agent | Trigger | Where it works · Run now | Engine | How it saves time | KPI it moves |
|---|---|---|---|---|---|---|
| Comms digest | Field Sales | On demand: when you forward to dealers | Sales Ops Comms | Rules | Per-dealer digest of announcements; your comment is checked against the OEM guardrails before it goes out | — |
| District Manager Assistant | Field Sales | On demand | Agent panel Interact | Bedrock | As for the district manager | — |

### OEM Finance Partner (`oem_finance`)

Screens: Campaigns (home), campaign detail, Accounts, plans, Approvals.

| Automation | Agent | Trigger | Where it works · Run now | Engine | How it saves time | KPI it moves |
|---|---|---|---|---|---|---|
| Budget-shift drafter | Campaign | Scheduled: nightly, 08:00 UTC | Approvals (drafts); Campaigns | Rules | Spend is attributed to delivered vehicles overnight and shifts arrive as approval drafts; spend never changes on its own | Campaign spend attributable to delivered vehicles |
| Account-plan drafter | Field Sales | On demand | Accounts | Rules | As for the district manager | — |
| District Manager Assistant | Field Sales | On demand | Agent panel Interact | Bedrock | As for the district manager | — |

### OEM Marketing Lead (`oem_marketing`)

Screens: Campaigns (home), campaign detail, Audiences, Journey Ops, Quick dashboards.

| Automation | Agent | Trigger | Where it works · Run now | Engine | How it saves time | KPI it moves |
|---|---|---|---|---|---|---|
| Budget-shift drafter | Campaign | Scheduled: nightly | Campaigns; Approvals | Rules | As for finance; marketing sees the drafts it will defend | Campaign spend attributable to delivered vehicles |
| Audience refresher | Campaign | On demand: when you press Refresh | Audiences | Rules | Every segment recomputed with movement and consented reach; exporting stays a separate step | Campaign spend attributable to delivered vehicles |

### OEM CX Lead (`oem_cx`)

Screens: Journey Ops (home), Campaigns, campaign detail.

| Automation | Agent | Trigger | Where it works · Run now | Engine | How it saves time | KPI it moves |
|---|---|---|---|---|---|---|
| Nurture executor | Journey | Scheduled: every 6 hours | applies the published policy to every due journey | Rules | Your policy (cadence caps, quiet hours, stop rules) runs across every journey without a redeploy; at `recommend` each touch goes to the advisor's desk | Journey stage drop-off; lead → contact rate |
| Policy previewer | Journey | On demand: before publish | Journey Ops | Rules | Shows exactly what the Journey Agent would send to a seeded customer before you publish | — |
| Consent guard | Journey | Live: on every send | every outbound touch | Rules | Channel opt-in, quiet hours and cadence caps are checked before each send, so nothing goes out that should not | — |

## Customer (My Garage)

Screens: My Garage (home), Service, Shop, Chat. The customer sees three cards
that explain what works for them behind the scenes; Interact opens the shop chat.

| Automation | Agent | Trigger | Where it works | Engine | How it helps | KPI it moves |
|---|---|---|---|---|---|---|
| Build re-scorer | Discovery | Scheduled: every morning | My Garage builds | Rules | When a better match comes into stock at the dealer, the advisor hears about it | Contact → appointment rate |
| Journey guide | Journey | Live: whenever you visit | My Garage; Chat | Rules + Bedrock wording (chat) | Next steps kept current and a person whenever asked | Journey stage drop-off |
| Service concierge | Service | Live: during service visits | My Garage › Service | Rules | Repair status, each finding in plain language, promised and projected ready times | Approval rate when presented; promised-time adherence |

## Admin roles

The admin panels are a view of the staff automations above; persona actions go
through **View as persona** (every request audited).

| Persona | Cards | Interact | Run now | Last ran without a journey open |
|---|---|---|---|---|
| Administrator (`platform_admin`) | 48 (all seven agents) | Campaign, Field Sales, Service | Service Cockpit "Recompute" only | tenant-wide Admin › Agents feed and named runs |
| Tenant Admin (`admin_tenant`) | 48 | Service | none | tenant-wide Admin › Agents feed and named runs |
| Dealer Admin (`admin_dealer`) | 36 (the store agents) | Service | none | named runs for its store |

## Screen index

Where each automation shows up, screen first. "Run now" lists the buttons the
page adds to the panel.

| Screen | Personas | Automations working here | Run now on this page |
|---|---|---|---|
| Journeys | advisor, BDC, F&I, delivery, GM, OEM DM | Stalled-journey nudger | Nudge stalled (n) |
| Advisor (live) | advisor, BDC, GM | Vehicle matcher, Objection coach, Next best action, Trade equity watch, Follow-up drafter | Draft & send; Do it: … |
| Assistant | advisor, BDC, service advisor, GM | Shift call list, Follow-up drafter, Appointment booker | Publish to Tasks; Draft follow-up; Book test drive |
| Lead Desk | BDC, sales advisor, GM | First-response drafter, Shift call list, Appointment booker, Predictive part watch, Lead router (on the BDC's panel) | Publish to Tasks; Scan now; Assign to …; Book appointment |
| Trade-In | advisor, service advisor, inventory, GM | — (valuation and upgrade prospects on the page; Add to journey for the sales roles) | — |
| F&I deal worksheet | F&I, GM | Deal structurer, Menu attach recommender, Lender decision request, Compliance sentry | Rebuild scenarios; Request decision |
| Inventory & Trade Desk | inventory, GM, parts | Demand scorer, Disposition recommender, Acquisition scout | — |
| Delivery Board | delivery, F&I, GM | Reschedule notifier, Delivery-day messenger, Post-delivery survey | Send message; Re-notify slot |
| Store Cockpit | GM, inventory, service manager, OEM DM / regional, Administrator | Store insight digest, Fixed-ops watch | — |
| Service Board | service advisor, technician, parts, service manager, GM | Morning dispatch, Promise re-forecaster, Approval chaser, Fixed-ops watch | Re-present (n) |
| Repair order view | service roles, GM | Estimate builder, Approval chaser, Repurchase handoff, DTC diagnoser, Draft-line builder | Hand to sales; Re-present |
| Inspection | technician, service manager, GM | Evidence checker | — |
| Declined work | service roles, GM | Declined-work follow-up | — |
| Parts desk | parts, service manager, GM | Parts ETA tracker, Repair resumer | — |
| Service Cockpit | service manager, GM, Administrator | Cockpit insight, Threshold what-if | Recompute |
| OEM Today / AI Alerts | OEM DM, regional | Alert triage, Weekly district cycle | — |
| OEM Accounts / Action Plans | OEM DM, regional, C&I, finance | Account-plan drafter, Weekly district cycle | — |
| Meeting in a Box | OEM DM, regional | Meeting in a Box | — |
| Scenario Planning | OEM DM, regional, C&I | Scenario planner | — |
| Sales Ops Comms | OEM sales ops (home), DM, regional | Comms digest | — |
| Report Library | OEM sales ops, DM, regional | — | — |
| Marketing Campaigns / Approvals | marketing, finance | Budget-shift drafter | — |
| Audiences | marketing | Audience refresher | — |
| Journey Ops | CX, marketing | Nurture executor, Policy previewer, Consent guard | — |
| My Garage | customer | Build re-scorer, Journey guide, Service concierge | — |

## Known gaps

- **General Manager, Campaign Agent chat:** `/marketing/ask` accepts OEM roles
  and the Administrator only, so Interact on the Store insight digest is
  disabled for the GM ("Not available for your role"). To offer the chat, the
  route would have to answer store-scoped questions for `dealer_manager`.
- **OEM CX Lead, Journey Agent chat:** `/assistant/chat` accepts dealer roles
  only, so Interact on the CX lead's Journey Agent cards is disabled. To offer
  it, OEM roles would need a policy-scoped chat.
- **First-response drafter on the Assistant page:** its Run now ("Draft
  follow-up") drafts a follow-up, not the lead's first response. The Lead Desk
  is where the first response is drafted and sent.
- **Inventory tab insight:** the store digest's guardrail blocks the Inventory
  tab insight in every store (advice on stocking is a denied topic), so that tab
  always shows the figures-only summary.
- **Field Sales Agent:** has no AgentCore runtime; its Quick-native agent is not
  built yet (`docs/specs/retail-ops-quick-project.md`).
