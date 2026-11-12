# Personas and benefits

This document walks through every persona the solution serves, what each one
does in the console, which agents work for them, and how that work saves time
or improves an outcome. It is written for dealer and OEM business owners,
process leads and evaluators. The use cases behind the automations are in
[01-overview-and-use-cases.md](01-overview-and-use-cases.md).

There are 21 personas: 19 staff roles that an administrator assigns, the
Administrator, and the customer. A person can hold more than one role; their
screens are then the union of both. Each persona signs in to its home screen,
named first in its **Screens** list below, and a page outside its screens
returns it there.

## How to read the agent panel

Every signed-in screen has an agent panel: a strip of cards, one per
automation, each a task an agent does for that persona.

| Card part | Meaning |
|---|---|
| Agent name | The agent that owns the task |
| Live | Runs with no click, when its screen loads or when the named event happens |
| Scheduled | A job on an enabled schedule, run for each market |
| On demand | Runs when you press its button |
| Run now | A button the current page adds for that automation (on some pages only) |
| Interact | Opens a chat with the agent, grounded on what the page has open |
| Last ran | The newest run for the open journey or repair order, otherwise for your store or market |

The **Engine** column in the tables below says what does the work:

- **Rules**: fixed, tested logic over the system's records, with no model.
- **Rules + model wording**: the logic is fixed; Amazon Bedrock writes the
  explanation or orders the candidate actions the rules produced, and cannot add
  or change an action.
- **Model**: Amazon Bedrock writes the result from the records, through a
  safety guardrail, with every figure cited.

Scheduled jobs run at the autonomy level the tenant publishes in Admin ›
Autonomy. Every action ships at **recommend**: the jobs write recommendation
cards, leads and drafts for a person to act on and send nothing to a customer
until an administrator raises that action's level.

The **KPI it moves** column names the success measure each automation is built
to move. A pilot measures these against the dealer's own baseline; this
document adds no estimated improvements. Two of the measures have public
industry baselines, shown with the F&I Manager and Service Advisor below.

The solution has 51 automations: 36 for store staff, 12 for OEM staff and 3 for
customers. Administrator panels show the staff automations as a read-mostly
view.

## Persona summary

| Persona | App and home screen | Agents on the panel | Cards | Chat from the panel |
|---|---|---|---|---|
| Sales Advisor | Dealer Console · Journeys | Sales, Journey, Trade-In | 5 | all three |
| BDC Agent | Dealer Console · Lead Desk | Sales, Journey, Service | 5 | all three |
| F&I Manager | Dealer Console · F&I Desk | Sales | 4 | Sales |
| Inventory Manager | Dealer Console · Inventory & Trade Desk | Discovery, Trade-In | 3 | both |
| Delivery Coordinator | Dealer Console · Delivery Board | Journey | 3 | Journey |
| General Manager | Dealer Console · Store Cockpit | Campaign, Journey, Service | 3 | Journey, Service |
| Service Advisor | Dealer Console · Service Board | Service | 7 | Service |
| Service Technician | Dealer Console · My ROs | Service | 3 | Service |
| Parts Specialist | Dealer Console · Parts desk | Service | 2 | Service |
| Service Manager | Dealer Console · Service Cockpit | Service | 2 | Service |
| OEM District Manager | OEM Workspace · Today | Field Sales | 5 | Field Sales |
| OEM Regional Lead | OEM Workspace · Region View | Field Sales | 5 | Field Sales |
| OEM C&I Planner | OEM Workspace · Accounts | Field Sales | 3 | Field Sales |
| OEM Sales Ops | OEM Workspace · Sales Ops Comms | Field Sales | 2 | Field Sales |
| OEM Finance Partner | Marketing Studio · Campaigns | Campaign, Field Sales | 3 | both |
| OEM Marketing Lead | Marketing Studio · Campaigns | Campaign | 2 | Campaign |
| OEM CX Lead | Marketing Studio · Journey Ops | Journey | 3 | none in the current release |
| Administrator | Admin Console · Overview | all seven | 48 | Campaign, Field Sales, Service |
| Tenant Admin | Admin Console · Overview | all seven | 48 | Service |
| Dealer Admin | Admin Console · Overview | the six store agents | 36 | Service |
| Customer | Customer Web · My Garage | Discovery, Journey, Service | 3 | all three, through the shop chat |

Every persona also has Tasks and the Notification inbox in the shared top bar.
Dealer staff see only the stores in their organizational scope, OEM staff see
the dealers below their node of the OEM hierarchy, and customers see only their
own records.

## Dealer sales floor

### Sales Advisor

- **Job.** Works each shopper from the first visit to a signed deal.
- **Screens.** Journeys (home), Advisor live view, Discovery, Assistant,
  Lead Desk (the store's web leads), Trade-In, Service Lane, Customer 360,
  vehicle detail, Tasks.
- **Agents.** Sales Agent, Journey Agent, Trade-In Agent. The panel is busiest
  on the Advisor live view, the journey screen used with the customer present.

| Automation | Agent | Trigger | What it does for you | Engine | KPI it moves |
|---|---|---|---|---|---|
| Vehicle matcher | Sales | Live: each time you open the journey | Ranks trims for the customer in front of you and checks in-stock vehicles for the top match, with the reasons | Rules + model wording | Show-to-close rate |
| Objection coach | Sales | Live: while you listen, about every 3 seconds | Spots an objection as the customer says it and suggests a response with its recorded success rate | Rules + model wording | Show-to-close rate |
| Next best action | Journey | Live: on open and on each stage change | Keeps a one-click next step current: book a test drive, send the finance link, schedule a follow-up | Rules + model wording | Contact-to-appointment rate; journey stage drop-off |
| Trade equity watch | Trade-In | Scheduled: nightly, and on open | Equity and payment change are already worked out; the nightly scan flags journeys that turn equity-positive or enter the lease-end window | Rules (nightly) and rules + model wording (on screen) | Trade-in captured in the same journey |
| Follow-up drafter | Journey | On demand: after a visit or call | Drafts the personalized follow-up and sends it on the customer's consented channel in one click | Rules | Contact-to-appointment rate |

**How it helps.** The advisor starts each conversation with the matches, the
trade position and the next step already prepared, and gets objection help in
the moment instead of after the customer leaves. The Assistant answers
questions about the customer, incentives and products with citations, and in
agent mode it plans its own lookups; any customer message it drafts waits for
the advisor.

### BDC Agent

- **Job.** Answers new leads inside their response window and books
  appointments.
- **Screens.** Lead Desk (home), Assistant, Journeys, Advisor live view,
  Customer 360, Tasks.
- **Agents.** Sales Agent, Journey Agent, Service Agent.

| Automation | Agent | Trigger | What it does for you | Engine | KPI it moves |
|---|---|---|---|---|---|
| First-response drafter | Sales | Live: each time you open a lead | A ready first reply with the vehicle and request the lead sent, a test-drive offer and qualifying questions | Rules | Lead response time; lead-to-contact rate |
| Shift call list | Journey | On demand: at shift start | Today's calls in priority order with the reason for each, published as Tasks | Rules | Lead-to-contact rate |
| Appointment booker | Journey | On demand: one click per lead | Books the test drive on the lead's journey without re-keying | Rules | Contact-to-appointment rate |
| Predictive part watch | Service | Scheduled: nightly, or Scan now | Service-due leads with a customer offer arrive on the desk ready to call | Rules | In-warranty service retention |
| Lead router | Journey | On demand: on assign | Assigns the lead and opens its journey; a change of owner hands over with a task, so context is kept | Rules | Handoff completeness |

**How it helps.** Every lead opens with a draft reply, the shift starts with a
prioritized list, and leads are routed with their context attached rather than
re-told.

### F&I Manager

- **Job.** Structures financing, presents the F&I menu and keeps every deal
  compliant before it is pushed to the DMS.
- **Screens.** F&I Desk (home), deal worksheet, Journeys, Delivery Board,
  Tasks.
- **Agents.** Sales Agent, working on the deal worksheet.

| Automation | Agent | Trigger | What it does for you | Engine | KPI it moves |
|---|---|---|---|---|---|
| Deal structurer | Sales | Live: each time the worksheet loads | Term, rate and payment scenarios across the lender offers, with the math shown | Rules | F&I products per deal |
| Menu attach recommender | Sales | Live: each time the worksheet loads | The likelihood and the reason for each F&I product, so the menu leads with what fits | Rules | F&I products per deal |
| Lender decision request | Sales | On demand: when you submit the credit application | Runs the OFAC and identity-theft Red Flags checks when they are not on file, submits to the lender panel and records each decision; if every lender declines, the adverse-action notice becomes required | Rules | — |
| Compliance sentry | Sales | Live: on load and before the DMS push | Checks the recorded OFAC, Red Flags and adverse-action status, the market's required disclosures and the state documentation-fee rule, and blocks the push until they are clear | Rules | — |

Public baseline for the KPI:

- F&I products per deal averaged 1.57 in the second quarter of 2025, up from
  1.52 a year earlier.
  - Source: [StoneEagle, Q2 2025 StoneEagleDATA F&I Benchmark Report (September 2025)](https://www.se-fi.com/post/stoneeagle-f-i-resilience-steadies-dealer-profitability-in-q2)

**How it helps.** Scenarios and menu recommendations are ready when the
worksheet opens, lender submission and decision tracking are one step, and the
compliance checks gate the DMS push instead of relying on memory. The deal
worksheet is one of the few places where sensitive data such as credit results
and trade payoff is shown; that data stays on the F&I desk and in the
customer's own view.

### Inventory Manager

- **Job.** Decides which vehicles to stock, keep, sell on or acquire.
- **Screens.** Inventory & Trade Desk (home), vehicle detail, Trade-In,
  Discovery, Store Cockpit (inventory and trade tabs), Tasks.
- **Agents.** Discovery Agent, Trade-In Agent.

| Automation | Agent | Trigger | What it does for you | Engine | KPI it moves |
|---|---|---|---|---|---|
| Demand scorer | Discovery | Live: each time the inventory grid loads | Active-journey demand next to every model and trim, so hot and aging stock stand out | Rules | — |
| Disposition recommender | Trade-In | Live: each time the Trades tab loads | Keep for certified pre-owned, retail, wholesale or auction for every accepted trade, with the reasoning from comparables, age and demand | Rules | Trade-in captured in the same journey |
| Acquisition scout | Trade-In | Live: each time the Acquisitions tab loads | Service-drive vehicles worth acquiring, with an offer range | Rules | Repeat and upgrade journeys sourced from ownership |

**How it helps.** Stock decisions are made against live shopper demand, and the
service drive becomes a source of used inventory.

### Delivery Coordinator

- **Job.** Prepares and hands over each sold vehicle.
- **Screens.** Delivery Board (home), Journeys, Tasks.
- **Agents.** Journey Agent.

| Automation | Agent | Trigger | What it does for you | Engine | KPI it moves |
|---|---|---|---|---|---|
| Reschedule notifier | Journey | Live: when you change a delivery slot | The customer hears about the new time on their preferred channel and the appointment is updated | Rules | — |
| Delivery-day messenger | Journey | On demand: from the Delivery Board | Prep checklist, document status and arrival details sent in one click | Rules | — |
| Post-delivery survey | Journey | Live: when you mark the vehicle delivered | Moves the journey to Own once prep and documents are complete, sends the survey, and opens the vehicle's first service journey | Rules | In-warranty service retention |

**How it helps.** Customer messages around delivery need no separate step, and
the move into ownership, including the first service journey, happens the
moment the vehicle is handed over.

### General Manager

- **Job.** Runs the store's sales and service performance.
- **Screens.** Store Cockpit (home; tabs for funnel, sales, inventory, trade and
  used, F&I, service, customer experience, alerts and OEM comms), every other
  Dealer Console and service screen, Approvals.
- **Agents.** Campaign Agent, Journey Agent, Service Agent.

| Automation | Agent | Trigger | What it does for you | Engine | KPI it moves |
|---|---|---|---|---|---|
| Store insight digest | Campaign | Scheduled: nightly, 06:30 UTC | Each cockpit tab opens with a written summary of what the figures say and the action they point to, labelled with its writer and time; until one is written the tab shows a figures-only summary | Model (store-digest guardrail) | Journey stage drop-off and each tab's own KPIs |
| Stalled-journey nudger | Journey | Scheduled: every morning, 05:30 UTC | Journeys idle past the stall threshold (7 days by default) get a callback brief on the owner's desk; Run now on Journeys sends a follow-up to the stalled journeys shown | Rules | Journey stage drop-off |
| Fixed-ops watch | Service | Live: each time the cockpit or service desk loads | Repair orders past their promised time, on parts hold or waiting on approval, counted where you look | Rules | Promised-time adherence |

**How it helps.** The day starts with a written read of the store and the
stalled journeys already assigned back to their owners. The inventory tab
always shows the figures-only summary: advice to a dealer on stocking levels is
a blocked topic for the safety guardrail, by design. The General Manager also
decides approvals such as deal exceptions in the shared approval inbox.

## Dealer service and fixed operations

### Service Advisor

- **Job.** Owns each customer's service visit from booking to pickup and
  follow-up.
- **Screens.** Service Board (home), repair order view, check-in, book service,
  estimate, service history, Declined work, Service Lane, Trade-In (trade
  valuation and upgrade prospects; the sales team adds the trade to the
  purchase), Assistant, Tasks.
- **Agents.** Service Agent.

| Automation | Agent | Trigger | What it does for you | Engine | KPI it moves |
|---|---|---|---|---|---|
| Morning dispatch | Service | Scheduled: every morning, 05:00 local | Technician picks, parts to stage and promise checks are ready as recommendation cards before the lane opens | Rules | Advisor morning work done before shift start; repair-order cycle time |
| Estimate builder | Service | On demand: after the inspection | Yellow and red findings become a priced estimate with a source on every number | Rules | Findings presentation rate |
| Approval chaser | Service | On demand: when an estimate waits on the customer | Re-sends waiting estimates by secure link in one click; the board flags every estimate waiting for approval | Rules | Estimate approval turnaround; approval rate when presented |
| Promise re-forecaster | Service | Scheduled: every morning, with morning dispatch | Flags repair orders already running past the promise and recommends a new one, so the customer hears early | Rules | Promised-time adherence |
| Declined-work follow-up | Service | Scheduled: hourly, on a 30, 60 and 90-day cadence | Due reminders re-present the original finding; at recommend they land on your desk to send | Rules | Declined-work follow-up conversion |
| Predictive part watch | Service | Scheduled: nightly | Customers are invited back before a part is due, through a Lead Desk lead and a My Garage offer | Rules | In-warranty service retention |
| Repurchase handoff | Service | Scheduled: nightly, and Hand to sales on the repair order | Equity-positive owners in follow-up are flagged; Hand to sales opens the sales journey with the service context | Rules | Repurchase and upgrade handoffs per 100 repair orders |

Public baseline for the retention KPI:

- In 2025, 54% of owners of vehicles two years old or newer had them serviced
  at the selling dealership, down from 72% in 2023.
  - Source: [Cox Automotive Service Industry Study 2025 (November 2025)](https://www.coxautoinc.com/insights/new-cox-automotive-study-finds-dealerships-have-lost-12-of-service-visits-to-competition-since-2018/)

**How it helps.** The morning work is done before the first customer arrives,
estimates are built from the inspection with sources the customer can see,
waiting approvals and declined work are chased in one click, and a service
visit can turn into a sales conversation without leaving the screen.

### Service Technician

- **Job.** Inspects, diagnoses and repairs the vehicles assigned to them.
- **Screens.** My ROs (home), Service Board, repair order view, inspection,
  Tasks. Customer contact details, pricing and the coverage split are hidden
  for this role by the server, not just the screen.
- **Agents.** Service Agent.

| Automation | Agent | Trigger | What it does for you | Engine | KPI it moves |
|---|---|---|---|---|---|
| DTC diagnoser | Service | On demand: on diagnostic trouble code entry | Description, likely labor operations, recommended tests and any indexed technical service bulletin, without leaving the bay | Rules | Repair-order cycle time |
| Draft-line builder | Service | On demand: per confirmed cause | Procedure, labor time and parts for the cause go straight into a draft estimate line | Rules | Findings presentation rate |
| Evidence checker | Service | Live: when you submit the inspection | Yellow and red items cannot be submitted without photo, video or voice evidence, so nothing comes back for rework | Rules | Inspection completion rate; approval rate when presented |

**How it helps.** Diagnosis references and draft estimate lines arrive at the
bay, and inspection evidence is complete the first time.

### Parts Specialist

- **Job.** Orders, receives and stages parts for open repair orders.
- **Screens.** Parts desk (home), Service Board, repair order view, Inventory,
  Tasks. Parts staff manage orders but not repair-line status.
- **Agents.** Service Agent.

| Automation | Agent | Trigger | What it does for you | Engine | KPI it moves |
|---|---|---|---|---|---|
| Parts ETA tracker | Service | Live: each time the parts desk loads | Open orders sorted by arrival time and held repair orders grouped, soonest first | Rules | Parts-wait repair orders and hours lost |
| Repair resumer | Service | Live: when you mark a part received | The repair order's parts hold clears the moment its last part arrives, so the board shows it ready | Rules | Parts-wait repair orders; repair-order cycle time |

**How it helps.** Parts waits are visible in one list, and repairs resume
without a phone call to the shop.

### Service Manager

- **Job.** Runs the service department's capacity, quality and profitability.
- **Screens.** Service Cockpit (home), every service screen, the Store Cockpit
  service tab, Service Lane, Tasks, Approvals.
- **Agents.** Service Agent.

| Automation | Agent | Trigger | What it does for you | Engine | KPI it moves |
|---|---|---|---|---|---|
| Cockpit insight | Service | Scheduled: nightly, 06:30 UTC | A written read of the fixed-operations figures and the next action; the figures-only summary shows while a what-if slider is moved | Model (store-digest guardrail) | The cockpit's KPIs: close rate, parts wait, retention |
| Threshold what-if | Service | On demand: on slider change | Labor-rate, close-rate and repair-versus-replace what-ifs on current lane data, with the formula shown | Rules | Repurchase and upgrade handoffs per 100 repair orders |

**How it helps.** The manager sees where the department stands and can test a
policy change against current data before making it.

## OEM field and analytics

OEM staff read across the dealers in their part of the OEM hierarchy. The OEM
Workspace screens are Today, Dealer Performance, Composite Analysis, Region
View, Accounts, Contact Reports, Action Plans, AI Alerts, Weekend Tracker,
Meeting in a Box, Scenario Planning, Conquest, EV Readiness, Compliance,
Campaign ROI, Sales Ops Comms, Report Library, AI Guardrails, and an Amazon
Quick screen that embeds the Quick dashboards and Flows.

### OEM District Manager and OEM Regional Lead

- **Job.** The district manager coaches a set of dealers toward their
  objectives; the regional lead does the same across districts and decides
  account-plan approvals.
- **Screens.** Every OEM Workspace screen, the dealers' Store Cockpits and
  Approvals (the district manager also opens dealer journeys). Home is Today
  for the district manager and Region View for the regional lead.
- **Agents.** Field Sales Agent.

| Automation | Agent | Trigger | What it does for you | Engine | KPI it moves |
|---|---|---|---|---|---|
| Weekly district cycle | Field Sales | Scheduled: Mondays, 05:00 UTC | The week starts with the gaps rolled up and a draft plan for every dealer below the health threshold; nothing publishes without you | Rules | — |
| Alert triage | Field Sales | Live: each time AI Alerts or Today loads | Open dealer alerts grouped high, medium and low, newest first | Rules | — |
| Account-plan drafter | Field Sales | On demand: from Accounts or Action Plans | A sourced draft plan or opportunity brief to edit and submit, instead of a blank page | Rules | — |
| Meeting in a Box | Field Sales | On demand: before a meeting | Agenda, scorecard and talking points for the meeting type, from live district data | Rules + model wording | — |
| District Manager Assistant | Field Sales | On demand: when you ask | Questions about your dealers answered from live data, with a citation for every figure, inside the OEM policy rules | Model | — |

**How it helps.** Visit preparation moves from assembling data to reviewing a
draft. Plans go through an approval workflow, so nothing reaches a dealer
without a person's decision. In AWS Quick, the same Field Sales Agent is
available as a chat agent with eight document Flows (account plan, opportunity
brief, win strategy, action plan, contact report, meeting package, district
roll-up, sales-ops digest); each manager can schedule the weekly district
roll-up for their own district.

### OEM C&I Planner

- **Job.** Plans the OEM incentive (C&I) programs that support dealer accounts.
- **Screens.** Accounts (home), account detail and plans, Scenario Planning,
  Approvals.
- **Agents.** Field Sales Agent.

| Automation | Agent | Trigger | What it does for you | Engine | KPI it moves |
|---|---|---|---|---|---|
| Account-plan drafter | Field Sales | On demand | As for the district manager | Rules | — |
| Scenario planner | Field Sales | On demand: on slider change | Projected volume, objective attainment and share for allocation, incentive-per-unit, days-supply and market-growth changes, labelled as estimates | Rules | — |
| District Manager Assistant | Field Sales | On demand | As for the district manager | Model | — |

**How it helps.** Program changes are tested against dealer data before they
are proposed. Incentive allocation itself always stays a human decision.

### OEM Sales Ops

- **Job.** Sends announcements and reports to the dealer network.
- **Screens.** Sales Ops Comms (home), Report Library.
- **Agents.** Field Sales Agent.

| Automation | Agent | Trigger | What it does for you | Engine | KPI it moves |
|---|---|---|---|---|---|
| Comms digest | Field Sales | On demand: when you forward to dealers | A per-dealer digest of announcements; your comment is checked against the OEM policy rules before it goes out | Rules | — |
| District Manager Assistant | Field Sales | On demand | As for the district manager | Model | — |

**How it helps.** Dealers get one relevant digest instead of a stream of
bulletins, and policy-sensitive wording is caught before it is sent.

### OEM Finance Partner

- **Job.** Reviews and approves marketing budget changes and account plans.
- **Screens.** Marketing Studio Campaigns (home), campaign detail, Accounts,
  plans, Approvals.
- **Agents.** Campaign Agent, Field Sales Agent.

| Automation | Agent | Trigger | What it does for you | Engine | KPI it moves |
|---|---|---|---|---|---|
| Budget-shift drafter | Campaign | Scheduled: nightly, 08:00 UTC | Spend is attributed to delivered vehicles overnight and budget shifts arrive as approval drafts with their evidence; spend never changes on its own | Rules | Campaign spend attributable to delivered vehicles |
| Account-plan drafter | Field Sales | On demand | As for the district manager | Rules | — |
| District Manager Assistant | Field Sales | On demand | As for the district manager | Model | — |

**How it helps.** Budget requests arrive with the attribution behind them, and
approving one records a decision without moving money automatically.

### OEM Marketing Lead

- **Job.** Plans campaigns and audiences and defends the budget.
- **Screens.** Campaigns (home), campaign detail, Audiences, Journey Ops, the
  AWS Quick dashboards.
- **Agents.** Campaign Agent.

| Automation | Agent | Trigger | What it does for you | Engine | KPI it moves |
|---|---|---|---|---|---|
| Budget-shift drafter | Campaign | Scheduled: nightly | As for finance; marketing sees the drafts it will defend | Rules | Campaign spend attributable to delivered vehicles |
| Audience refresher | Campaign | On demand: when you press Refresh | Every segment recomputed with its movement and consented reach; exporting to an ad platform stays a separate step with hashed identifiers only | Rules | Campaign spend attributable to delivered vehicles |

**How it helps.** Campaign return is measured in delivered vehicles, and the
Campaign ROI dashboard and natural-language questions in AWS Quick work over
the same data.

### OEM CX Lead

- **Job.** Sets the policy for how and when prospects are contacted.
- **Screens.** Journey Ops (home), Campaigns, campaign detail.
- **Agents.** Journey Agent.

| Automation | Agent | Trigger | What it does for you | Engine | KPI it moves |
|---|---|---|---|---|---|
| Nurture executor | Journey | Scheduled: every 6 hours | Your published policy (cadence caps, quiet hours, stop rules) runs across every due journey without a redeploy; at recommend each touch goes to the advisor's desk | Rules | Journey stage drop-off; lead-to-contact rate |
| Policy previewer | Journey | On demand: before publish | Shows exactly what the Journey Agent would send to a sample customer before you publish | Rules | — |
| Consent guard | Journey | Live: on every send | Channel opt-in, quiet hours and cadence caps are checked before each send, and the send is held when any says no | Rules | — |

**How it helps.** Contact policy is a published, versioned setting with a
preview and a rollback, not a request to IT. In the current release this role
has no chat from the agent panel; Journey Ops is its working surface.

## Platform administration

### Administrator

- **Job.** Operates the platform across every app, dealer and district.
- **Screens.** The Admin Console (home) plus every staff screen of every app;
  no customer screens. The role is granted only to identities on the
  administrator allowlist set at deployment. It cannot be assigned in the
  console and nobody can act as it.
- **Agents.** All seven, as a view: 48 cards. Chat with the Campaign, Field
  Sales and Service Agents; Run now only for the Service Cockpit what-if.

**How it helps.** One role reads every module and does everything a tenant
administrator can. Persona actions, such as submitting a plan or writing to a
journey, go through **View as persona**, and every request made that way is
audited under the Administrator's own identity. That keeps each write
attributable to the persona whose job it is, and keeps one person from being
both the submitter and the approver.

### Tenant Admin

- **Job.** Administers users, organization and agent behaviour for the whole
  tenant.
- **Screens.** Admin Console: Overview, Users and roles, Org hierarchy, Market
  and brand, Connectors, Feature flags, Guardrails and policies (with a test
  harness), Autonomy, Agents, Audit, Data requests, Entity merge queue,
  Approvals.
- **Agents.** All seven, as a view: 48 cards. Chat with the Service Agent.

**How it helps.**

- Assigning a persona and an organizational scope sets a person's home screen,
  screens and data access in one step; every API call enforces the same role
  and scope.
- Autonomy is set per action, with history and rollback; hard limits cannot be
  raised.
- Guardrail rules, the suggestion matrix, notification rules, feature flags,
  connectors and the nurture policy are versioned settings that take effect on
  the next agent run without a redeploy.
- The Agents panel shows invocations, error rate, latency, guardrail blocks,
  and acceptance and conversion per action: the evidence for raising or
  lowering an autonomy level.
- Customer data requests (download or delete) are handled in one place.

### Dealer Admin

- **Job.** Administers staff access for their own stores.
- **Screens.** The Admin Console, limited to their stores.
- **Agents.** The six store agents, as a view: 36 cards. Chat with the Service
  Agent.

**How it helps.** Store-level user changes do not wait for the OEM. A dealer
administrator can assign dealer roles and move users only within their stores;
the server refuses OEM roles, tenant administration and anything outside their
stores, even if a client bypasses the screen.

## The customer

- **Job.** Shops for a vehicle, then owns and services it.
- **Screens.** My Garage (home), service status and estimate approval, Shop,
  Chat. Customers sign in with a one-time link sent to them; no password.
- **Agents.** Discovery Agent, Journey Agent, Service Agent.

| Automation | Agent | Trigger | What it does for you | Engine | KPI it moves |
|---|---|---|---|---|---|
| Build re-scorer | Discovery | Scheduled: every morning | Re-ranks vehicles against what you are shopping for and, when a better match is in stock at your dealer, lets your advisor know | Rules | Contact-to-appointment rate |
| Journey guide | Journey | Live: whenever you visit | Keeps your next steps current and passes you to a person at the dealership whenever you ask | Rules + model wording (chat) | Journey stage drop-off |
| Service concierge | Service | Live: during service visits | Tracks your repair, explains each finding in plain language, and shows the promised and projected ready times | Rules | Approval rate when presented; promised-time adherence |

**How it helps.** The customer sees the same shortlist and estimate the dealer
sees, approves or declines each repair line on their phone with the evidence
in front of them, and gets answers from their own record. Questions about
credit decisions, legal matters or safety recalls go to a person. Messages
respect the customer's consent, channel and quiet hours, and the customer sees
only their own data.
