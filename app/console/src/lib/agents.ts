// Per-persona agent automation catalog (spec §2.2/§3, use-cases A1–A7).
//
// "Agent" in the UI is more than a chat box: each persona has named automations
// — concrete tasks the AgentCore agents run on events, on schedules, or on
// demand — surfaced in <AgentBar/> with their trigger, last activity (from
// PLAT_AgentTraces) and a Run-now binding supplied by the page.
//
// Scope rule (delivery-model R1–R7): custom application agents appear in the
// global bar. A6 (Field Sales Agent) keeps its persistent OEM Workspace
// assistant, routed through `/oem/ask`; the server synthesizes from scoped read
// models with Bedrock until the required Quick-native A6 agent is authored.
//
// The OEM field and analytics roles (district manager, regional lead, C&I
// planner, sales ops, finance) get the bar too (user decision 2026-10-22), so
// every persona has one: their Field Sales Agent automations, plus the Campaign
// Agent's budget-shift drafter for finance. Its Interact opens the same
// `/oem/ask` the workspace assistant uses.
//
// Admin roles get the bar too (user decision 2026-10-22), as a read-mostly view:
// the Administrator (`platform_admin`) and the tenant admin see every staff
// automation of the six custom agents, the dealer admin its store's. An admin
// catalog lists the chats and Run-now bindings the server accepts from that role
// (`chats`, `runNow`), so the bar hides an Interact or Run-now that would 403;
// with no journey or RO in scope, the tenant-wide roles take "last ran" from the
// Admin › Agents feed (`traceFeed`). Staff and customer catalogs set none of the
// three and behave exactly as before; persona actions stay behind View as persona.
// Two staff bars list `chats` too (user decision 2026-10-28), because a chat
// route refuses the role: the General Manager's Campaign Agent (`/marketing/ask`
// serves OEM roles only) and the OEM CX lead's Journey Agent (`/assistant/chat`
// serves dealer roles only). The bar shows their Interact disabled.
//
// Every entry says what really runs (agent-panel honesty audit, user decision
// 2026-10-22), and scripts/check-agent-catalog.mjs holds the catalog to it:
//   * `trigger` — "auto": runs with no click, when its screen loads or on the
//     event named in `when`; "scheduled": a job on an ENABLED schedule
//     (app/infra/deploy_l6.py JOBS), named in `runs` as `job.<name>`;
//     "manual": runs when you press its button.
//   * `runs` — the PLAT_AgentTraces run names that count as this automation
//     running, for "last ran" outside a journey (GET /traces/latest): scheduled
//     jobs (`job.<name>`), on-demand routes (app/services/api/automation_traces.py)
//     and the few runs a service writes itself.
//   * on-view automations write no run: their result is on the screen you load.
// Two automations had no implementation behind them and were retired:
// Supersession finder (no supersession data exists) and Goodwill triage (nothing
// raises or packages a goodwill request).

export type AgentId = "A1" | "A2" | "A3" | "A4" | "A5" | "A6" | "A7";
export type Trigger = "auto" | "scheduled" | "manual";

export interface AgentAutomation {
  id: string;            // stable id; pages bind Run-now actions to it
  agent: AgentId;
  name: string;
  does: string;          // what it does, as it runs today
  trigger: Trigger;
  when: string;          // human trigger detail ("each time the worksheet loads", "nightly")
  traceMatch?: string[]; // tool-name substrings matched in a journey / RO trace feed → "last ran"
  runs?: string[];       // run names for "last ran" outside a journey (GET /traces/latest)
}

/** What users see. The codes A1–A7 stay internal ids (API `agent_id`, traces,
 *  spec references); every label on screen uses these names instead. */
export const AGENT_LABEL: Record<string, string> = {
  A1: "Discovery Agent",
  A2: "Journey Agent",
  A3: "Sales Agent",
  A4: "Trade-In Agent",
  A5: "Campaign Agent",
  A6: "Field Sales Agent",
  A7: "Service Agent",
};

/** Display name for an agent code; anything else passes through unchanged. */
export function agentLabel(code?: string | null): string {
  return (code && AGENT_LABEL[code]) || code || "";
}

/** Replace agent codes inside display text that comes from data (source refs,
 *  trace rows, replay narration): "A3.get_incentives (…)" → "Sales Agent ·
 *  get_incentives (…)", "A7 predicts" → "Service Agent predicts". */
export function humanizeAgents(text: string): string;
export function humanizeAgents(text: string | null | undefined): string | null | undefined;
export function humanizeAgents(text: string | null | undefined) {
  if (!text) return text;
  return text
    .replace(/\bA([1-7])\./g, (_, n) => `${AGENT_LABEL[`A${n}`]} · `)
    .replace(/\bA([1-7])\b/g, (_, n) => AGENT_LABEL[`A${n}`]);
}

export const AGENT_NAMES: Record<AgentId, string> = {
  A1: "Vehicle Discovery",
  A2: "Purchase Journey Supervisor",
  A3: "Sales Advisor Agent",
  A4: "Trade-In & Upgrade Advisor",
  A5: "Campaign ROI Optimizer",
  A6: "OEM Sales Administrator",
  A7: "Service Operations Supervisor",
};

/** Per-agent conversational endpoint — what "Interact" talks to. `synthesize`
 *  marks the retrieval route that generates an answer over its passages. */
export interface AgentChat {
  path: string;
  placeholder: string;
  prompts: string[];
  synthesize?: boolean;
  persona?: string;
  /** Where the chat runs, shown under the agent name; absent = AgentCore. */
  host?: string;
  /** What answers are grounded on; absent = this journey / RO / store. */
  grounding?: string;
  /** `view` sent with each question; absent = "agent-bar". `/oem/ask` hands it
   *  to the model as the user's screen, so it must read naturally there. */
  view?: string;
}

export const AGENT_CHAT: Record<AgentId, AgentChat> = {
  A1: { path: "/assistant/chat", persona: "a vehicle discovery specialist",
        placeholder: "Ask about matches, trims, availability…",
        prompts: ["What should I show this customer?", "Which trims fit the budget?", "What is in stock nearby?"] },
  A2: { path: "/assistant/chat", persona: "a purchase journey supervisor",
        placeholder: "Ask about the journey, next step, outreach…",
        prompts: ["What is the next best action?", "Why is this journey stalled?", "Draft a follow-up"] },
  A3: { path: "/assistant/chat", persona: "a dealership sales advisor",
        placeholder: "Ask about the customer, deal, objections…",
        prompts: ["How do I handle a price objection?", "What incentives stack this month?", "Summarise this customer"] },
  A4: { path: "/assistant/chat", persona: "a trade-in and upgrade specialist",
        placeholder: "Ask about trade value, equity, upgrade timing…",
        prompts: ["Is this customer equity-positive?", "What is the payment delta to upgrade?", "Keep or wholesale?"] },
  A5: { path: "/marketing/ask", persona: "an OEM marketing analytics assistant",
        placeholder: "Ask about ROI, channels, audiences…",
        prompts: ["Which channel returns the most deliveries?", "Where is the funnel leaking?", "What should I shift?"] },
  A6: { path: "/oem/ask", host: "Amazon Bedrock in the OEM Workspace", view: "workspace",
        grounding: "the dealer accounts, alerts and plans in your scope",
        placeholder: "Ask about dealers, performance, alerts…",
        prompts: ["Which dealers are furthest below peers?", "Which alerts need my attention first?", "Who has the highest days supply?"] },
  A7: { path: "/knowledge/retrieve", synthesize: true, persona: "a service advisor's assistant",
        placeholder: "Ask about a DTC, procedure, TSB, part…",
        prompts: ["P0300 misfire causes", "Front brake pad procedure", "HV battery inspection steps"] },
};

export interface PersonaAgents {
  title: string;
  automations: AgentAutomation[];
  /** Agents whose Interact chat the role may open; absent = every agent in the
   *  bar. Set only where a chat route refuses the role (the admin roles, the
   *  General Manager, the OEM CX lead). */
  chats?: AgentId[];
  /** Automations whose page-supplied Run-now the role may use; absent = any
   *  binding the page supplies. Set only where the bound endpoint refuses the
   *  role (the admin roles). */
  runNow?: string[];
  /** "tenant": with no journey / RO in scope, "last ran" and Activity read the
   *  tenant-wide Admin › Agents feed (`GET /admin/agents/health`). */
  traceFeed?: "tenant";
}

// Field Sales Agent (A6) automations, shared by the OEM field and analytics roles
// so one automation reads the same in every bar it appears in.
const A6_DISTRICT_CYCLE: AgentAutomation = {
  id: "a6.district_cycle", agent: "A6", name: "Weekly district cycle", trigger: "scheduled", when: "Mondays, 05:00 UTC",
  does: "Rolls up the top performance gaps per district and drafts an account plan for every dealer below the health threshold. Drafts wait for your review; it never publishes.",
  traceMatch: ["district_cycle", "draft_account_plan"], runs: ["job.district-cycle"] };
const A6_ALERT_TRIAGE: AgentAutomation = {
  id: "a6.alert_triage", agent: "A6", name: "Alert triage", trigger: "auto", when: "each time AI Alerts or Today loads",
  does: "Groups the open dealer alerts in your scope HIGH / MEDIUM / LOW, newest first, with a count by kind.",
  traceMatch: ["alert"] };
const A6_PLAN_DRAFTER: AgentAutomation = {
  id: "a6.plan_drafter", agent: "A6", name: "Account-plan drafter", trigger: "manual", when: "from Accounts or Action Plans",
  does: "Generates a sourced account plan or opportunity brief for a dealer or fleet account, ready for you to edit and publish.",
  traceMatch: ["account_plan", "draft_plan"], runs: ["a6.draft_account_plan"] };
const A6_MEETING_PREP: AgentAutomation = {
  id: "a6.meeting_prep", agent: "A6", name: "Meeting in a Box", trigger: "manual", when: "before a dealer or district meeting",
  does: "Assembles the agenda, scorecard and talking points for the meeting type you pick, from live district data.",
  traceMatch: ["meeting"] };
const A6_SCENARIO: AgentAutomation = {
  id: "a6.scenario_planner", agent: "A6", name: "Scenario planner", trigger: "manual", when: "on slider change",
  does: "Projects volume, objective % and share for allocation, incentive-per-unit, days-supply and market-growth changes.",
  traceMatch: ["scenario"] };
const A6_COMMS_DIGEST: AgentAutomation = {
  id: "a6.comms_digest", agent: "A6", name: "Comms digest", trigger: "manual", when: "when you forward to dealers",
  does: "Builds a per-dealer digest of sales-ops announcements and checks your comment against the OEM guardrails before it goes out.",
  traceMatch: ["forward_comms", "comms"], runs: ["a6.forward_comms"] };
const A6_ASSISTANT: AgentAutomation = {
  id: "a6.assistant", agent: "A6", name: "District Manager Assistant", trigger: "manual", when: "when you ask",
  does: "Answers questions about the dealers in your scope from live OEM read models, phrased by Amazon Bedrock with a citation for every figure.",
  traceMatch: ["oem_ask"], runs: ["a6.oem_ask"] };
// Shared by OEM marketing and finance, who reviews these drafts.
const A5_BUDGET_SHIFTS: AgentAutomation = {
  id: "a5.budget_shifts", agent: "A5", name: "Budget-shift drafter", trigger: "scheduled", when: "nightly",
  does: "Attributes campaign spend to delivered vehicles and drafts budget-shift requests into the finance Approvals queue. It never changes spend.",
  traceMatch: ["budget"], runs: ["job.nightly-attribution"] };
// Shared by the Lead Desk and the service lane. The leads it raises are the
// BDC's to work, so the copy does not point the service advisor at the Lead Desk.
const A7_PREDICTIVE_WATCH: AgentAutomation = {
  id: "a7.predictive_watch", agent: "A7", name: "Predictive part watch", trigger: "scheduled", when: "nightly, or Scan now",
  does: "Projects each delivered vehicle's next part service from its mileage and service history, and raises a lead with a customer offer for each store's most urgent predictions. The BDC works these leads; the owner sees the offer in My Garage.",
  traceMatch: ["predictive"], runs: ["job.predictive-scan", "a7.predictive_scan"] };

export const AGENT_CATALOG: Record<string, PersonaAgents> = {
  dealer_advisor: {
    title: "Your floor agents",
    automations: [
      { id: "a3.recommendations", agent: "A3", name: "Vehicle matcher", trigger: "auto", when: "each time you open the journey",
        does: "Ranks vehicle and trim matches for the customer in front of you and checks in-stock VINs for the top match.",
        traceMatch: ["rank_vehicles", "match_vehicles"] },
      { id: "a3.objection_coach", agent: "A3", name: "Objection coach", trigger: "auto", when: "while you listen, about every 3 s",
        does: "Classifies objections in the floor conversation as you listen and suggests a response with its recorded success rate.",
        traceMatch: ["classify_objection"] },
      { id: "a2.next_best_action", agent: "A2", name: "Next best action", trigger: "auto", when: "each time you open the journey or its stage changes",
        does: "Keeps the one-click next step current (book a test drive, send the finance link, schedule a follow-up) from the journey's stage and history.",
        traceMatch: ["select_next_best_action"] },
      { id: "a4.equity_watch", agent: "A4", name: "Trade equity watch", trigger: "scheduled", when: "nightly, and each time you open the journey",
        does: "Recomputes the customer's trade equity and payment delta on the journey. The nightly scan flags journeys that turn equity-positive or enter the lease-end window.",
        traceMatch: ["build_equity", "detect_equity", "flag_upgrade_timing"], runs: ["job.equity-monitor"] },
      { id: "a2.followup_drafter", agent: "A2", name: "Follow-up drafter", trigger: "manual", when: "after a visit or call",
        does: "Drafts the personalized follow-up for this journey and sends it on the customer's preferred channel, when they have consented to it.",
        traceMatch: ["send_outreach", "compose_message"], runs: ["a2.send_outreach"] },
    ],
  },
  dealer_bdc: {
    title: "Lead desk agents",
    automations: [
      { id: "a3.first_response", agent: "A3", name: "First-response drafter", trigger: "auto", when: "each time you open a lead",
        does: "Drafts the first response for the lead (greeting, the vehicle and request they sent, a test-drive offer and qualifying questions), ready to send inside its SLA.",
        traceMatch: ["first_response"], runs: ["a3.draft_first_response", "a3.send_first_response"] },
      { id: "a2.call_list", agent: "A2", name: "Shift call list", trigger: "manual", when: "at shift start, when you publish it",
        does: "Builds today's prioritized call list from your journeys, each with the reason it made the list. Publishing it creates a Task for each call.",
        traceMatch: ["publish_shift_list"], runs: ["a2.publish_shift_list"] },
      { id: "a2.appointment_booker", agent: "A2", name: "Appointment booker", trigger: "manual", when: "one click per lead",
        does: "Books a test-drive appointment on the lead's journey.",
        traceMatch: ["book_appointment", "book_test_drive"], runs: ["a2.book_appointment"] },
      A7_PREDICTIVE_WATCH,
      { id: "a2.lead_router", agent: "A2", name: "Lead router", trigger: "manual", when: "on assign",
        does: "Assigns the lead to an advisor and opens or links its DISCOVER journey. A change of owner goes through a handoff with a Task for the receiver.",
        traceMatch: ["assign_lead"], runs: ["a2.assign_lead"] },
    ],
  },
  dealer_fni: {
    title: "F&I desk agents",
    automations: [
      { id: "a3.deal_structurer", agent: "A3", name: "Deal structurer", trigger: "auto", when: "each time the deal worksheet loads",
        does: "Builds term, rate and payment scenarios across the lender offers on the worksheet, with the math shown.",
        traceMatch: ["structure_deal"] },
      { id: "a3.menu_attach", agent: "A3", name: "Menu attach recommender", trigger: "auto", when: "each time the deal worksheet loads",
        does: "Scores how likely this customer is to take each F&I product on the menu, with the reason.",
        traceMatch: ["attach"] },
      { id: "a3.lender_chaser", agent: "A3", name: "Lender decision request", trigger: "manual", when: "when you submit the credit application",
        does: "Runs the OFAC and Red Flags checks when they are not on file, submits the credit application to the lender panel and records each lender's decision. If every lender declines, the adverse-action notice becomes required.",
        traceMatch: ["request_lender_decision"], runs: ["a3.request_lender_decision"] },
      { id: "a3.compliance_sentry", agent: "A3", name: "Compliance sentry", trigger: "auto", when: "each time the worksheet loads, and before the DMS push",
        does: "Checks the recorded OFAC, Red Flags and adverse-action status, the market's required disclosures and the state doc-fee rule, and blocks the DMS push until they are clear.",
        traceMatch: ["compliance"] },
    ],
  },
  dealer_inventory: {
    title: "Inventory & trade agents",
    automations: [
      { id: "a1.demand_scorer", agent: "A1", name: "Demand scorer", trigger: "auto", when: "each time the inventory grid loads",
        does: "Counts active-journey demand for every model and trim against stock, so hot models and aging units stand out in the grid.",
        traceMatch: ["demand"] },
      { id: "a4.disposition", agent: "A4", name: "Disposition recommender", trigger: "auto", when: "each time the Trades tab loads",
        does: "Recommends keep-for-CPO, retail, wholesale or auction for every accepted trade, with the reasoning from comps, age and demand.",
        traceMatch: ["disposition"] },
      { id: "a4.acquisition_scout", agent: "A4", name: "Acquisition scout", trigger: "auto", when: "each time the Acquisitions tab loads",
        does: "Checks the vehicles in your service drive against used-stock demand and lists the ones worth acquiring, with an offer range.",
        traceMatch: ["acquisition"] },
    ],
  },
  dealer_delivery: {
    title: "Delivery agents",
    automations: [
      { id: "a2.delivery_notifier", agent: "A2", name: "Reschedule notifier", trigger: "auto", when: "when you change a delivery slot",
        does: "Tells the customer about the new delivery time through their preferred channel and updates the appointment.",
        traceMatch: ["reschedule"] },
      { id: "a2.delivery_day", agent: "A2", name: "Delivery-day messenger", trigger: "manual", when: "when you send it from the Delivery Board",
        does: "Sends the delivery-day message (prep checklist, document status and arrival details) through the customer's preferred channel.",
        traceMatch: ["delivery_day_message"], runs: ["a2.delivery_day_message"] },
      { id: "a2.survey_sender", agent: "A2", name: "Post-delivery survey", trigger: "auto", when: "when you mark the vehicle delivered",
        does: "Moves the journey DELIVER → OWN once prep and documents are complete, and sends the post-delivery survey.",
        traceMatch: ["mark_delivered"], runs: ["a2.mark_delivered"] },
    ],
  },
  dealer_manager: {
    title: "Store agents",
    automations: [
      { id: "a5.store_digest", agent: "A5", name: "Store insight digest", trigger: "scheduled", when: "nightly",
        does: "Writes each Store Cockpit tab's [AI INSIGHT] with Amazon Bedrock from the store's own figures. Until a current one is written, the tab shows the figures-only summary.",
        runs: ["job.store-digest"] },
      { id: "a2.stalled_nudger", agent: "A2", name: "Stalled-journey nudger", trigger: "scheduled", when: "every morning",
        does: "Flags journeys idle past the 7-day stall SLA and puts a Callback Brief recommendation on the owning advisor's desk. Run now on the Journeys page sends a follow-up to the stalled journeys shown there.",
        traceMatch: ["bulk_nudge"], runs: ["job.stall-sweep", "a2.bulk_nudge"] },
      { id: "a7.fixed_ops_watch", agent: "A7", name: "Fixed-ops watch", trigger: "auto", when: "each time the cockpit or service desk loads",
        does: "Counts the ROs projected past their promised time, held for parts or waiting on estimate approval, on the cockpit's Service tab and the service desk's attention list." },
    ],
    // POST /marketing/ask (Campaign Agent) serves OEM roles only.
    chats: ["A2", "A7"],
  },
  dealer_service: {
    title: "Service lane agents",
    automations: [
      { id: "a7.morning_dispatch", agent: "A7", name: "Morning dispatch", trigger: "scheduled", when: "every morning, 05:00 local",
        does: "Before the lane opens it recommends a technician for each booked RO, flags likely parts to stage and re-forecasts promises that already slip, as recommendation cards for the service desk.",
        traceMatch: ["dispatch", "stage_parts"], runs: ["job.shop-load"] },
      { id: "a7.estimate_builder", agent: "A7", name: "Estimate builder", trigger: "manual", when: "after the inspection, one click",
        does: "Turns the inspection's yellow and red findings into a priced estimate (procedures, labor times, parts and coverage) with a source on every number.",
        traceMatch: ["build_estimate"], runs: ["a7.build_estimate"] },
      { id: "a7.approval_chaser", agent: "A7", name: "Approval chaser", trigger: "manual", when: "when an estimate is waiting on the customer",
        does: "Re-sends the waiting estimates to the customer by secure link when you press Re-present. The Service Board flags every estimate waiting for approval.",
        traceMatch: ["re_present", "auto_present_estimate"], runs: ["a7.re_present_estimates"] },
      { id: "a7.promise_forecaster", agent: "A7", name: "Promise re-forecaster", trigger: "scheduled", when: "every morning, with Morning dispatch",
        does: "Flags ROs whose projected-ready time already runs past the promised time and recommends a new promise before the customer is surprised.",
        traceMatch: ["reforecast_promise"], runs: ["job.shop-load"] },
      { id: "a7.declined_followup", agent: "A7", name: "Declined-work follow-up", trigger: "scheduled", when: "hourly, on the 30 / 60 / 90-day cadence",
        does: "Follows up declined work when its reminder falls due, re-presenting the original finding. At the recommend autonomy level the reminder goes to the advisor's desk instead of being sent.",
        traceMatch: ["declined"], runs: ["job.service-followup-tick"] },
      A7_PREDICTIVE_WATCH,
      { id: "a7.repurchase_handoff", agent: "A7", name: "Repurchase handoff", trigger: "scheduled", when: "nightly, and Hand to sales on the RO",
        does: "Each night it flags vehicles in follow-up whose owner is now equity-positive as a sales-handoff card. Hand to sales opens the DISCOVER journey for the sales desk (source = service lane).",
        traceMatch: ["handoff_to_sales"], runs: ["job.service-lane-monitor", "a7.handoff_to_sales"] },
    ],
  },
  dealer_technician: {
    title: "Bay agents",
    automations: [
      { id: "a7.dtc_diagnoser", agent: "A7", name: "DTC diagnoser", trigger: "manual", when: "on DTC entry",
        does: "Looks up each code's description, the likely labor operations, recommended tests and any indexed TSB." },
      { id: "a7.line_drafter", agent: "A7", name: "Draft-line builder", trigger: "manual", when: "per confirmed cause",
        does: "Pulls the repair procedure, labor time and parts for the selected cause into a draft estimate line." },
      { id: "a7.evidence_checker", agent: "A7", name: "Evidence checker", trigger: "auto", when: "when you submit the inspection",
        does: "Blocks yellow and red inspection items from submission until photo, video or voice evidence is attached.",
        traceMatch: ["submit_inspection"], runs: ["a7.submit_inspection"] },
    ],
  },
  dealer_parts: {
    title: "Parts agents",
    automations: [
      { id: "a7.eta_tracker", agent: "A7", name: "Parts ETA tracker", trigger: "auto", when: "each time the parts desk loads",
        does: "Lists every open parts order by ETA and groups the ROs held on parts, soonest ETA first." },
      { id: "a7.repair_resumer", agent: "A7", name: "Repair resumer", trigger: "auto", when: "when you mark a part received",
        does: "Clears the RO's parts hold the moment its last held part is received, so the Service Board shows it ready to resume." },
    ],
  },
  dealer_service_manager: {
    title: "Fixed-ops agents",
    automations: [
      { id: "a7.cockpit_insight", agent: "A7", name: "Cockpit insight", trigger: "scheduled", when: "nightly",
        does: "Writes the Service Cockpit's [AI INSIGHT] with Amazon Bedrock from the store's fixed-ops figures. Until a current one is written, or while a what-if slider is moved, the cockpit shows the figures-only summary.",
        runs: ["job.store-digest"] },
      { id: "a7.threshold_whatif", agent: "A7", name: "Threshold what-if", trigger: "manual", when: "on slider change",
        does: "Recomputes labor revenue, recovered pending work and the repair-vs-replace count for the slider values over current lane data, with the formula shown." },
    ],
  },
  oem_dm: {
    title: "Your Field Sales Agent",
    automations: [A6_DISTRICT_CYCLE, A6_ALERT_TRIAGE, A6_PLAN_DRAFTER, A6_MEETING_PREP, A6_ASSISTANT],
  },
  oem_regional: {
    title: "Field Sales Agent across your region",
    automations: [A6_DISTRICT_CYCLE, A6_ALERT_TRIAGE, A6_PLAN_DRAFTER, A6_MEETING_PREP, A6_ASSISTANT],
  },
  oem_ci_planning: {
    title: "Planning agents",
    automations: [A6_PLAN_DRAFTER, A6_SCENARIO, A6_ASSISTANT],
  },
  oem_sales_ops: {
    title: "Sales-ops agents",
    automations: [A6_COMMS_DIGEST, A6_ASSISTANT],
  },
  oem_finance: {
    title: "Finance agents",
    automations: [A5_BUDGET_SHIFTS, A6_PLAN_DRAFTER, A6_ASSISTANT],
  },
  oem_marketing: {
    title: "Campaign agents (AgentCore step — dashboards stay in Quick)",
    automations: [
      A5_BUDGET_SHIFTS,
      { id: "a5.audience_refresh", agent: "A5", name: "Audience refresher", trigger: "manual", when: "when you press Refresh",
        does: "Recomputes every audience segment from the latest journey outcomes and reports the movement and consented reach. Exporting to an ad platform is a separate step.",
        traceMatch: ["audience"], runs: ["refresh_audiences"] },
    ],
  },
  oem_cx: {
    title: "Journey policy agents",
    automations: [
      { id: "a2.nurture_executor", agent: "A2", name: "Nurture executor", trigger: "scheduled", when: "every 6 hours",
        does: "Applies your published nurture policy (cadence caps, quiet hours, stop conditions) to every journey that is due. At the recommend autonomy level each touch goes to the advisor's desk as a recommendation instead of being sent.",
        traceMatch: ["nurture"], runs: ["job.nurture-tick"] },
      { id: "a2.preview_simulator", agent: "A2", name: "Policy previewer", trigger: "manual", when: "before publish",
        does: "Renders a draft policy or template against a seeded customer so you see exactly what the Journey Agent would send.",
        traceMatch: ["preview"], runs: ["a2.preview_policy"] },
      { id: "a2.consent_guard", agent: "A2", name: "Consent guard", trigger: "auto", when: "on every send",
        does: "Checks the customer's channel opt-in, quiet hours and your cadence caps before every outbound touch, and holds the send when any of them says no.",
        traceMatch: ["consent"] },
    ],
    // POST /assistant/chat (Journey Agent) serves dealer roles only.
    chats: [],
  },
  customer: {
    title: "Working for you behind the scenes",
    automations: [
      { id: "a1.build_rescorer", agent: "A1", name: "Build re-scorer", trigger: "scheduled", when: "every morning",
        does: "Re-ranks vehicles against what you are shopping for each morning and, when a better match is in stock at your dealer, lets your advisor know.",
        traceMatch: ["rescore"], runs: ["job.rescore-profiles"] },
      { id: "a2.journey_guide", agent: "A2", name: "Journey guide", trigger: "auto", when: "whenever you visit",
        does: "Keeps your next steps current and passes you to a person at the dealership whenever you ask.",
        traceMatch: ["next_best_action", "nurture"] },
      { id: "a7.service_concierge", agent: "A7", name: "Service concierge", trigger: "auto", when: "during service visits",
        does: "Tracks your repair, explains each finding in plain language, and shows your promised and projected ready times.",
        traceMatch: ["explain"] },
    ],
  },
};

// ---- admin roles (user decision 2026-10-22) -------------------------------------
// The admin bars reuse the staff automations above, so an automation reads the
// same wherever it shows. What each admin role may do from the bar mirrors the
// server (app/services/common/authz.py and the route modules):
//
//   Chat  A1–A4 → POST /assistant/chat      SALES | LANE dealer roles only (no OEM read-across): hidden
//         A5    → POST /marketing/ask       OEM_VIEW_ROLES, which include the Administrator
//         A6    → POST /oem/ask             OEM_VIEW_ROLES, which include the Administrator
//         A7    → POST /knowledge/retrieve  any signed-in user
//   Run   a7.threshold_whatif (Service Cockpit "Recompute") re-reads GET /service/cockpit,
//         which accepts the OEM-scoped Administrator. Every other binding a page
//         supplies is a dealer-floor write the Administrator is not granted (spec
//         §5.2; e.g. POST /service/estimates/re-present is ADVISORS only), and the
//         tenant / dealer admin screens supply none.
//   Feed  GET /admin/agents/health is tenant admin + Administrator only; the
//         dealer admin's store subset of the Admin Console excludes it.

const AGENT_ORDER: AgentId[] = ["A1", "A2", "A3", "A4", "A5", "A6", "A7"];

/** The automations of `roles`, de-duplicated by id (one automation can serve two
 *  roles, e.g. Predictive part watch) and grouped by agent, Discovery → Service. */
function automationsOf(roles: string[]): AgentAutomation[] {
  const byId = new Map<string, AgentAutomation>();
  for (const role of roles) {
    for (const a of AGENT_CATALOG[role]?.automations || []) if (!byId.has(a.id)) byId.set(a.id, a);
  }
  return Array.from(byId.values())
    .sort((x, y) => AGENT_ORDER.indexOf(x.agent) - AGENT_ORDER.indexOf(y.agent));
}

const STAFF_CATALOG_ROLES = Object.keys(AGENT_CATALOG).filter((r) => r !== "customer");
const STORE_CATALOG_ROLES = STAFF_CATALOG_ROLES.filter((r) => r.startsWith("dealer_"));

AGENT_CATALOG.platform_admin = {
  title: "Every agent, every app",
  automations: automationsOf(STAFF_CATALOG_ROLES),
  chats: ["A5", "A6", "A7"],
  runNow: ["a7.threshold_whatif"],
  traceFeed: "tenant",
};
AGENT_CATALOG.admin_tenant = {
  title: "Every agent across the tenant",
  automations: automationsOf(STAFF_CATALOG_ROLES),
  chats: ["A7"],
  runNow: [],
  traceFeed: "tenant",
};
AGENT_CATALOG.admin_dealer = {
  title: "Your store's agents",
  automations: automationsOf(STORE_CATALOG_ROLES),
  chats: ["A7"],
  runNow: [],
};

/** Catalog for a persona role; null only for an unknown role. */
export function agentsForRole(role?: string | null): PersonaAgents | null {
  if (!role) return null;
  return AGENT_CATALOG[role] || null;
}

/** The Interact chat a role may open for an agent; null when the role has no
 *  bar or the agent's chat route would refuse it (the bar hides Interact). */
export function agentChatFor(role: string | null | undefined, agent: AgentId): AgentChat | null {
  const catalog = agentsForRole(role);
  if (!catalog || (catalog.chats && !catalog.chats.includes(agent))) return null;
  return AGENT_CHAT[agent] || null;
}

/** Whether the role may use a page-supplied Run-now binding for an automation. */
export function mayRunNow(role: string | null | undefined, automationId: string): boolean {
  const catalog = agentsForRole(role);
  if (!catalog) return false;
  return !catalog.runNow || catalog.runNow.includes(automationId);
}

/** An agent tool id as words: "runtime.a3.classify_objection" → "classify objection",
 *  "job.nightly-attribution" → "nightly attribution (scheduled run)". Never the raw id. */
export function toolLabel(tool: unknown): string {
  const t = String(tool || "").replace(/^runtime\./, "").replace(/^a[1-7]\./i, "");
  if (t.startsWith("job.")) return `${t.slice(4).replace(/[-_]/g, " ")} (scheduled run)`;
  return t.replace(/[._]/g, " ");
}
