"use client";

// Scenario Replay (demo) — scripted business processes that drive the real
// sandbox API step by step, switching persona per step and landing on the
// screen where that step of the process lives. Each step's `act` FILLS DATA
// through the same endpoints the UI uses; every call is defensive (a failed
// call becomes a note, never a dead end) so a demo always moves forward.
//
// Demo-only mechanics: persona switching swaps the local dev token
// (signInDevToken), which the sandbox devserver accepts when
// RETAIL_OPS_ALLOW_DEV_TOKEN=1. With a real Cognito session active, dev tokens are
// ignored — sign out before running a replay.

import { apiGet, apiPatch, apiPost, setActAs } from "./api";
import { hasCognitoSession, signInDevToken } from "./auth";
import { fmtClock } from "./market";

/** Switch the acting persona for demo purposes, in whichever way the current
 *  sign-in supports:
 *  - local dev (no Cognito session): swap the dev token — the sandbox/dev
 *    authorizer accepts `user:<id>` / `cust:<id>` directly.
 *  - real Cognito session: set the admin "View as" header (X-Act-As). The
 *    server honors it ONLY for admin callers, so hops on the deployed console
 *    need an admin sign-in; everyone else keeps their own persona.
 *  Returns the mechanism used so callers can hint accordingly. */

export function actAsPersona(token: string): "devtoken" | "actas" {
  if (hasCognitoSession()) {
    setActAs(token.startsWith("cust:") ? token : token.replace(/^user:/, ""));
    return "actas";
  }
  signInDevToken(token);
  return "devtoken";
}

/** Undo whatever actAsPersona set up (keeps the real sign-in). */
export function clearActAs(): void {
  if (hasCognitoSession()) setActAs(null);
}

export interface StepNote { ok: boolean; text: string }

/** Defensive API caller handed to every act(): failures become notes. */
export interface Caller {
  get: <T = any>(path: string, why?: string) => Promise<T | null>;
  post: <T = any>(path: string, body?: any, why?: string) => Promise<T | null>;
  patch: <T = any>(path: string, body?: any, why?: string) => Promise<T | null>;
  note: (text: string, ok?: boolean) => void;
}

export type Ctx = Record<string, string | undefined>;

export interface ReplayStep {
  id: string;
  title: string;                 // "BDC sends the first response"
  narrative: string;             // what this step means in the business process
  token: string | ((ctx: Ctx) => string);        // dev token for the persona acting this step
  personaLabel: string | ((ctx: Ctx) => string); // "Keisha (BDC agent)"
  screen: (ctx: Ctx) => string;  // where to watch it in the UI
  act?: (ctx: Ctx, call: Caller) => Promise<Ctx | void>;
  actLabel?: string;             // short label of what act does ("fills data")
}

export interface Scenario {
  id: string;
  name: string;
  process: "Process 1 · Sales" | "Process 2 · Service" | "Service → Sales";
  description: string;
  steps: ReplayStep[];
}

const T = {
  bdc: "user:U-US-00005",        // Keisha Baker, dealer_bdc (D-101)
  advisor: "user:U-US-00002",    // Elizabeth Thomas, dealer_advisor
  fni: "user:U-US-00006",        // Michael Lopez, dealer_fni
  inventory: "user:U-US-00008",  // Emma Washington, dealer_inventory
  delivery: "user:U-US-00009",   // Megan White, dealer_delivery
  service: "user:U-US-00007",    // Anthony Nguyen, dealer_service
  technician: "user:U-US-00053", // Camila Clark, dealer_technician
  parts: "user:U-US-00055",      // Andre Davis, dealer_parts
  svcManager: "user:U-US-00056", // Ethan Wilson, dealer_service_manager
  manager: "user:U-US-00001",    // Raj Nelson, dealer_manager
  dm: "user:U-US-00044",         // Casey Brennan, oem_dm (DIST-07)
  finance: "user:U-US-00048",    // Fatima Robinson, oem_finance
  marketing: "user:U-US-00049",  // William Scott, oem_marketing
  cx: "user:U-US-00050",         // Sarah Mitchell, oem_cx
  customer: "cust:C-US-J1",      // Jordan Alvarez
};

// demo_pack.py service-visit owners (cars due their scheduled service, not in the bay)
const SERVICE_OWNERS = Array.from({ length: 10 }, (_, i) => `C-US-DS${String(i + 1).padStart(2, "0")}`);
const soon = (h: number) => new Date(Date.now() + h * 3600_000).toISOString();
const OBJECTION_UTTERANCE = "Honestly, that monthly payment is more than we wanted to spend.";

// ---- Scenario 1 · New-car purchase (Process 1) --------------------------------

// The buyer the whole story follows. Step 1 posts her web form through the intake
// endpoint, so a fresh lead lands on the desk with its SLA clock running whatever
// state the sandbox is in; every later step reads ctx.journeyId / ctx.customerId,
// so F&I, delivery and My Garage are hers — never another customer's seeded deal.
// Each rehearsal is a NEW buyer: the CRM matches a lead to a customer by e-mail, so a fixed
// buyer turned every run after the first into another repeat purchase by the same person.
// The run takes the first of these who is not on the Lead Desk yet (reset the stack to start over).
const DEMO_BUYERS: [string, string, string][] = [
  ["Priya", "Natarajan", "(404) 555-0147"], ["Daniel", "Okafor", "(404) 555-0162"],
  ["Hannah", "Lindqvist", "(404) 555-0178"], ["Marcus", "Bell", "(404) 555-0183"],
  ["Sofia", "Restrepo", "(404) 555-0191"], ["Ethan", "Park", "(404) 555-0124"],
  ["Amara", "Diallo", "(404) 555-0135"], ["Lucas", "Moreau", "(404) 555-0149"],
  ["Grace", "Holloway", "(404) 555-0156"], ["Noah", "Castellano", "(404) 555-0168"],
];
const buyerLead = (given: string, family: string, phone: string) => ({
  ...WEB_LEAD, given_name: given, family_name: family, phone,
  email: `${given}.${family}@example.com`.toLowerCase(),
});
async function nextBuyer(c: Caller) {
  const q = await c.get<any>("/leads/queue", "which demo buyers are already on the Lead Desk");
  const taken = new Set((Object.values(q?.queue || {}).flat() as any[]).map((l: any) => String(l.customer_name || "").toLowerCase()));
  const free = DEMO_BUYERS.find(([g, f]) => !taken.has(`${g} ${f}`.toLowerCase()));
  if (!free) c.note("all 10 demo buyers have been used — run deploy/demo-reset.sh for a fresh set (re-using the last)", false);
  return buyerLead(...(free || DEMO_BUYERS[DEMO_BUYERS.length - 1]));
}
const WEB_LEAD = {
  source: "web_form",
  given_name: "Priya", family_name: "Natarajan", email: "priya.natarajan@example.com", phone: "(404) 555-0147",
  city: "Decatur", state: "GA", zip5: "30030",
  vehicle_of_interest_model_id: "MER-VOLT-C",
  comments: "Is the Ion C Extended in stock in blue or white? We'd like to see it this week — and what is my 2019 Trek 4 worth as a trade?",
  budget: { price_min: 32000, price_max: 42000, monthly_max: 560, down: 3000 },
  current_vehicle: { year: 2019, make: "Kestrel", model: "Trek 4", mileage: 61000, ownership: "loan", payoff: 8400, months_remaining: 11 },
};
const ADVISOR_ID = "U-US-00002";   // Elizabeth Thomas

/** Where the journey sits on the board right now. */
async function stageOf(c: Caller, journeyId: string): Promise<string> {
  const st = await c.get<any>(`/journeys/${encodeURIComponent(journeyId)}/state`, "read the journey's stage");
  return st?.stage || "";
}

const sales: Scenario = {
  id: "ev-purchase",
  name: "New-car purchase — web lead to delivery",
  process: "Process 1 · Sales",
  description: "One buyer, start to finish: her web lead lands on the Lead Desk, the BDC answers and books, the advisor presents the EV she asked about and handles the objection, her trade is valued, F&I desks and books the deal on a reserved VIN, delivery hands over the car, and her My Garage shows ownership.",
  steps: [
    { id: "lead", title: "A web lead lands on the Lead Desk", personaLabel: "Keisha (BDC)", token: T.bdc,
      actLabel: "web form → intake",
      narrative: "A new buyer's web form arrives: the Ion C she asked about, her budget and the Trek 4 she would trade. A1/A2 score it and the 30-minute first-response clock starts.",
      screen: (ctx) => ctx.leadId ? `/dealer/leads?lead=${encodeURIComponent(ctx.leadId)}` : "/dealer/leads",
      act: async (_ctx, c) => {
        const lead = await nextBuyer(c);
        const out = await c.post<any>("/leads", lead, `web form from ${lead.given_name} ${lead.family_name} posted to the lead intake`);
        if (!out?.lead?.lead_id) { c.note("lead intake failed — is the sandbox devserver up to date?", false); return; }
        c.note(`${out.customer_name || "customer"} · ${out.lead.lead_id} · first response due ${fmtClock(out.sla?.sla_due_at)}`);
        return { leadId: out.lead.lead_id, customerId: out.customer_id, customerName: out.customer_name || `${lead.given_name} ${lead.family_name}` };
      } },
    { id: "respond", title: "A3 drafts the first response; BDC sends it", personaLabel: "Keisha (BDC)", token: T.bdc,
      actLabel: "sends the cited A3 draft",
      narrative: "The First-response drafter wrote a reply from her own request (the Ion C, the trade question) inside the SLA; the clock stops when it goes out.",
      screen: (ctx) => ctx.leadId ? `/dealer/leads?lead=${encodeURIComponent(ctx.leadId)}` : "/dealer/leads",
      act: async (ctx, c) => {
        if (!ctx.leadId) { c.note("no lead captured", false); return; }
        const s = await c.get<any>(`/leads/${ctx.leadId}/script`, "A3 first-response draft");
        const r = await c.post<any>(`/leads/${ctx.leadId}/respond`,
          { channel: "email", body: s ? `${s.opening} ${s.body}` : undefined }, "send first response (email)");
        if (r) c.note(r.sent ? "e-mail sent" : r.fallback_task ? "e-mail queued as a dealer task (no SES in the sandbox)" : `not sent: ${r.reason || "see task"}`, Boolean(r.sent || r.fallback_task));
      } },
    { id: "appointment", title: "Appointment booked, lead handed to the floor", personaLabel: "Keisha (BDC)", token: T.bdc,
      actLabel: "assigns + books",
      narrative: "Assigning opens her DISCOVER journey and writes an ownership handoff to Elizabeth with a Task; A2 books the visit against that journey.",
      screen: (ctx) => ctx.leadId ? `/dealer/leads?lead=${encodeURIComponent(ctx.leadId)}` : "/dealer/leads",
      act: async (ctx, c) => {
        if (!ctx.leadId) { c.note("no lead captured", false); return; }
        const a = await c.post<any>(`/leads/${ctx.leadId}/assign`, { assignee: ADVISOR_ID }, "assign to Elizabeth (advisor) — handoff + task");
        const jid = a?.journey_id || ctx.journeyId;
        if (a?.handoff_id) c.note(`handoff ${a.handoff_id} pending Elizabeth's accept`);
        await c.post(`/leads/${ctx.leadId}/appointment`, { slot_start: soon(24), slot_end: soon(24.5) }, "book the test drive");
        if (jid) return { journeyId: String(jid), handoffId: a?.handoff_id };
      } },
    { id: "present", title: "Advisor live: A1 leads with the Ion C she asked about", personaLabel: "Elizabeth (Sales advisor)", token: T.advisor,
      actLabel: "accepts the handoff · presents · saves the build",
      narrative: "Elizabeth accepts the handoff (she is the owner now). The flagship 3-column floor screen ranks matches with her request first; presenting the top trim saves it as the build.",
      screen: (ctx) => `/dealer/advisor/${ctx.journeyId || ""}`,
      act: async (ctx, c) => {
        if (!ctx.journeyId) { c.note("no journey id captured", false); return; }
        if (ctx.handoffId) await c.post(`/handoffs/${ctx.handoffId}/accept`, {}, "accept the handoff → Elizabeth owns the journey");
        const a = await c.get<any>(`/journeys/${ctx.journeyId}/advisor`, "advisor read model");
        const matches: any[] = a?.matches?.matches || [];
        const top = matches.find((m) => m.requested) || matches[0];
        if (!top) { c.note("no vehicle matches yet — present skipped", false); return; }
        const recId = a?.recommendation_ids?.[`match:${top.trim_id}`];
        await c.post(`/journeys/${ctx.journeyId}/present`,
          { trim_id: top.trim_id, model_id: top.model_id, recommendation_id: recId }, `present ${top.trim_name || top.trim_id}`);
        const saved = await c.post<any>("/configurations",
          { journey_id: ctx.journeyId, model_id: top.model_id, trim_id: top.trim_id, color: "Meridian Blue" },
          `save the build (${top.trim_name || top.trim_id}) → CONFIGURE`);
        return { trimId: top.trim_id, modelId: top.model_id, configId: saved?.configuration?.config_id };
      } },
    { id: "objection", title: "Price objection detected and answered", personaLabel: "Elizabeth (Sales advisor)", token: T.advisor,
      actLabel: "classifies the utterance",
      narrative: "The Objection coach hears the customer live and answers with her own facts (budget, commute, the Ion C's range) and the strategy's success rate.",
      // the screen re-runs the same utterance through the coach panel so the reply is on screen
      screen: (ctx) => `/dealer/advisor/${ctx.journeyId || ""}?say=${encodeURIComponent(OBJECTION_UTTERANCE)}`,
      act: async (ctx, c) => {
        await c.post(`/journeys/${ctx.journeyId}/objection`, { utterance: OBJECTION_UTTERANCE, channel: "floor" }, "classify objection");
      } },
    { id: "qualify", title: "Floor session wraps: qualified, on to finance", personaLabel: "Elizabeth (Sales advisor)", token: T.advisor,
      actLabel: "advances CONFIGURE → QUALIFY → FINANCE",
      narrative: "Budget and timeline captured on the floor; A2's handoff gates check the saved build, then budget + timeline, before the card moves to Finance.",
      screen: (ctx) => `/dealer/journeys${ctx.journeyId ? `?focus=${encodeURIComponent(ctx.journeyId)}` : ""}`,
      act: async (ctx, c) => {
        if (!ctx.journeyId) { c.note("no journey id captured", false); return; }
        for (let i = 0; i < 3; i++) {
          const stage = await stageOf(c, ctx.journeyId);
          if (stage === "DISCOVER" || stage === "CONFIGURE") {
            await c.post(`/journeys/${ctx.journeyId}/advance`,
              { evidence: "build saved on the floor (replay)", context_updates: { shortlist: true, saved_build: true } },
              `A2 advances from ${stage}`);
          } else if (stage === "QUALIFY") {
            await c.post(`/journeys/${ctx.journeyId}/advance`,
              { evidence: "budget confirmed and timeline (this month) captured on the floor (replay)",
                context_updates: { budget: true, timeline: true } }, "A2 advances from QUALIFY → FINANCE");
          } else { c.note(`journey is in ${stage}`); break; }
        }
      } },
    { id: "trade", title: "Trade-in valued; equity on the deal", personaLabel: "Elizabeth (Sales advisor)", token: T.advisor,
      actLabel: "runs A4 valuation on her Trek 4",
      narrative: "A4 values the Trek 4 she typed into the form (anchored to comparable appraisals), nets the loan payoff, and attaches the equity to the journey.",
      screen: (ctx) => `/dealer/trade-in${ctx.journeyId ? `?journey=${encodeURIComponent(ctx.journeyId)}` : ""}`,
      act: async (ctx, c) => {
        if (!ctx.journeyId) { c.note("no journey id captured", false); return; }
        const t = await c.post<any>(`/journeys/${ctx.journeyId}/trade`, { condition: "good" }, "value the trade and attach it to the journey");
        if (t?.valuation) c.note(`${t.valuation.vehicle?.year || ""} ${t.valuation.vehicle?.make || ""} ${t.valuation.vehicle?.model || ""}: value ${t.valuation.trade_value}, payoff ${t.valuation.payoff}, equity ${t.valuation.equity}`);
      } },
    { id: "fni", title: "F&I desks the deal, reserves the VIN, books it", personaLabel: "Michael (F&I manager)", token: T.fni,
      actLabel: "desk from the build · lender decision · reserve VIN · push to DMS",
      narrative: "The desk opens on her saved build (vehicle + tax − incentives = deal total), the lender panel answers — the captive quotes the OEM APR special for a qualifying tier — a VIN of her trim is reserved, and the deal books in the DMS against it.",
      screen: (ctx) => ctx.dealId ? `/dealer/fni/${ctx.dealId}` : `/dealer/fni/${ctx.journeyId || ""}`,
      act: async (ctx, c) => {
        if (!ctx.journeyId) { c.note("no journey id captured", false); return; }
        const d = await c.post<any>(`/fni/journeys/${ctx.journeyId}/deal`, {}, "open the desk on this journey (deal from the saved build)");
        const dealId = d?.deal?.deal_id;
        if (!dealId) { c.note("could not desk the deal", false); return; }
        await c.post(`/fni/deals/${dealId}/lender-decision`, {}, "request lender decision");
        // FINANCE → TRADE_IN (offer presented) → AGREE (valuation recorded)
        for (let i = 0; i < 2; i++) {
          const stage = await stageOf(c, ctx.journeyId);
          if (stage === "FINANCE" || stage === "TRADE_IN") await c.post(`/fni/journeys/${ctx.journeyId}/advance`, {}, `F&I advances from ${stage}`);
          else break;
        }
        // reserve a unit of the presented trim at this store
        const inv = await c.get<any>(`/inventory?model=${encodeURIComponent(ctx.modelId || "MER-VOLT-C")}&status=in_stock`, "in-stock units of her model");
        const units: any[] = inv?.vehicles || inv?.items || inv?.rows || [];
        const unit = units.find((v) => v.trim_id === ctx.trimId && v.dealer_id === "D-101") || units.find((v) => v.dealer_id === "D-101") || units[0];
        if (unit?.vin) await c.post(`/inventory/${unit.vin}/reserve`, { journey_id: ctx.journeyId }, `reserve ${unit.vin} (${unit.trim_name || unit.trim_id}, ${unit.color || ""})`);
        else c.note("no in-stock unit to reserve — the DMS push will be blocked", false);
        const p = await c.post<any>(`/fni/deals/${dealId}/push`, {}, "push deal to DMS");
        if (p?.dms_deal_number) c.note(`booked ${p.dms_deal_number} on VIN ${p.vin}`);
        return { dealId, vin: unit?.vin };
      } },
    { id: "advance", title: "Journey advances toward delivery", personaLabel: "Michael (F&I manager)", token: T.fni,
      actLabel: "closes AGREE → DELIVER",
      narrative: "A2 validates the handoff (deal booked, compliance recorded, VIN on the deal) before the gate opens; the delivery card is created and handed to Megan.",
      screen: (ctx) => `/dealer/journeys${ctx.journeyId ? `?focus=${encodeURIComponent(ctx.journeyId)}` : ""}`,
      act: async (ctx, c) => {
        if (!ctx.journeyId) { c.note("no journey id captured", false); return; }
        const stage = await stageOf(c, ctx.journeyId);
        if (stage === "AGREE") {
          const out = await c.post<any>(`/fni/journeys/${ctx.journeyId}/advance`, {}, "F&I closes AGREE → DELIVER");
          if (out?.handoff?.to_user) c.note(`delivery handoff to ${out.handoff.to_user_name || "the delivery coordinator"}`);
        } else c.note(`journey is in ${stage}; nothing to advance here`, stage === "DELIVER");
      } },
    { id: "deliver", title: "Delivery day — card moves to Delivered", personaLabel: "Megan (Delivery)", token: T.delivery,
      actLabel: "marks delivered",
      narrative: "Prep checklist done (charger install for the EV), docs signed, customer confirmed; marking delivered moves DELIVER → OWN and A2 sends the survey.",
      screen: () => "/dealer/delivery",
      act: async (ctx, c) => {
        if (!ctx.journeyId) { c.note("no journey id captured", false); return; }
        const d = await c.get<any>("/deliveries", "delivery board");
        const lanes = d?.lanes || {};
        const open = [...(lanes.docs_ready || []), ...(lanes.prepped || []), ...(lanes.scheduled || [])];
        const card = open.find((x: any) => x.journey_id === ctx.journeyId);
        if (!card) { c.note(`no delivery card for ${ctx.customerName || ctx.journeyId} — did AGREE close?`, false); return; }
        const items: string[] = Object.keys(card.checklist || {});
        const checklist = Object.fromEntries(items.map((k) => [k, true]));
        const patch: any = { checklist, docs_signed: true, customer_confirmed: true, scheduled_at: soon(2) };
        if (items.includes("charger_install")) patch.charger_install = "installed";
        await c.patch(`/deliveries/${card.journey_id}`, patch, `prep complete (${items.length} items) · docs signed · customer confirmed`);
        await c.post(`/deliveries/${card.journey_id}/message`, {}, "delivery-day message");
        await c.post(`/deliveries/${card.journey_id}/deliver`, {}, "mark delivered → OWN");
      } },
    { id: "own", title: "The customer's My Garage shows ownership", personaLabel: (ctx) => `${(ctx.customerName || "The buyer").split(" ")[0]} (Customer)`,
      token: (ctx) => ctx.customerId ? `cust:${ctx.customerId}` : T.customer,
      narrative: "The same journey from the buyer's side: her new vehicle, documents, next service due — and A1 keeps re-scoring saved builds.",
      screen: () => "/garage" },
  ],
};

// ---- Scenario 2 · Service visit with mobile approval (Process 2) ---------------

const service: Scenario = {
  id: "service-visit",
  name: "Service visit — booking to pickup, approval on the phone",
  process: "Process 2 · Service",
  description: "An owner books the 40k service in My Garage; check-in, technician MPI with a yellow brake finding, sourced estimate, line-by-line approval on the phone, parts, repair, pickup — and the declined-work register.",
  steps: [
    { id: "pick", title: "Today's customer: an owner due the 40k service", personaLabel: "Anthony (Service advisor)", token: T.service,
      actLabel: "finds the next owner due service",
      narrative: "One of the store's owners whose car is due its scheduled service and is not in the bay — the first such owner, so no car ever carries two open visits (each run hands the car back at the end).",
      screen: () => "/dealer/service",
      act: async (_ctx, c) => {
        const j = await c.get<any>("/service/journeys", "cars already booked or in the bay");
        const open = ["BOOK", "CHECK_IN", "INSPECT", "DIAGNOSE", "APPROVE", "REPAIR", "READY"];
        const busy = new Set(((j?.cards || []) as any[]).filter((x: any) => open.includes(x.stage)).map((x: any) => x.customer_id));
        const id = SERVICE_OWNERS.find((cid) => !busy.has(cid));
        if (!id) { c.note("all 10 service-visit owners have a visit open — run deploy/demo-reset.sh", false); return; }
        const p = await c.get<any>(`/customers/${encodeURIComponent(id)}`, "the owner's Customer 360");
        const name = [p?.customer?.given_name, p?.customer?.family_name].filter(Boolean).join(" ");
        c.note(`${name || id} — ${(p?.vehicles || [])[0]?.odometer?.toLocaleString?.() || "?"} on the odometer`);
        return { customerId: id, customerName: name };
      } },
    { id: "book", title: "Customer books service from My Garage",
      personaLabel: (ctx) => `${(ctx.customerName || "The owner").split(" ")[0]} (Customer)`,
      token: (ctx) => ctx.customerId ? `cust:${ctx.customerId}` : T.customer,
      actLabel: "creates the booking",
      narrative: "Due items and open recalls pre-fill; the slot picker shows lane capacity (A7 book_service).",
      screen: () => "/garage/service",
      act: async (_ctx, c) => {
        const own = await c.get<any>("/garage/ownership", "find the vehicle");
        const vin = own?.vehicles?.[0]?.vin;
        const slots = await c.get<any>("/garage/service/slots" + (vin ? `?vin=${encodeURIComponent(vin)}` : ""), "slot picker");
        const slot = slots?.slots?.find((s: any) => (s.capacity ?? 1) > 0) || slots?.slots?.[0];
        const due = await c.get<any>("/garage/service", "due items");
        const services = (due?.due || []).slice(0, 2).map((d: any) => d.service || d.name || d.title).filter(Boolean);
        const r = await c.post<any>("/garage/service/book", {
          vin, slot_start: slot?.start || slot?.slot_start || soon(26), wait_type: "wait",
          services: services.length ? services : ["40,000 mi service", "Brake inspection"],
        }, "book the visit");
        const sid = r?.service_journey_id || r?.booking?.service_journey_id || r?.booking_id;
        const odometer = String(Number(own?.vehicles?.[0]?.odometer) || "");
        if (sid) return { serviceJourneyId: String(sid), vin, odometer };
        c.note("booking did not return a service journey id", false);
        return { vin, odometer };
      } },
    { id: "checkin", title: "Lane check-in with dictated concerns", personaLabel: "Anthony (Service advisor)", token: T.service,
      actLabel: "records the check-in",
      narrative: "Mileage, concerns, walk-around, approval preference; A7 sets the promised time.",
      screen: (ctx) => `/dealer/service/${ctx.serviceJourneyId || ""}/checkin`,
      act: async (ctx, c) => {
        if (!ctx.serviceJourneyId) { c.note("no service journey captured", false); return; }
        // mileage in = last recorded odometer plus a few days of driving, so the RO agrees with the vehicle record
        const mileage = (Number(ctx.odometer) || 40000) + 143;
        const ci = await c.post<any>(`/service/journeys/${ctx.serviceJourneyId}/checkin`, {
          mileage, approval_preference: "my_garage", promised_at: soon(8),
          concerns: ["Squeal from the front brakes at low speed", `${Math.round(mileage / 1000)}k scheduled maintenance`],
        }, "check-in");
        // check-in opens the RO (A7 open_repair_order) and returns it
        const ro = ci?.ro?.ro_id || ci?.ro_id;
        if (ro) return { roId: String(ro) };
        c.note("RO id not found yet", false);
      } },
    { id: "inspect", title: "Technician MPI — front brakes come back yellow", personaLabel: "Camila (Technician, tablet)", token: T.technician,
      actLabel: "claims the RO, submits the inspection",
      narrative: "G/Y/R items with measurements and photo evidence; the DTC/diagnose panel cites TSBs from the index.",
      screen: (ctx) => `/dealer/service/${ctx.serviceJourneyId || ""}/inspect`,
      act: async (ctx, c) => {
        if (!ctx.roId) { c.note("no RO id — inspection skipped", false); return; }
        // the technician takes the RO from My ROs first, so the journey carries a technician from here on
        try { await c.post(`/service/journeys/${ctx.serviceJourneyId}/claim`, {}, "claim the RO (My ROs)"); } catch { c.note("RO already claimed"); }
        const t = await c.get<any>("/service/inspection-templates", "MPI template");
        const template = t?.templates?.[0];
        await c.post(`/service/ros/${ctx.roId}/inspection`, {
          template_id: template?.template_id || template?.id || "mpi-standard",
          findings: [
            { item: "Front brake pads", status: "yellow", measurement: "4 mm", evidence: ["photo:front-pads.jpg"] },
            { item: "Tire tread depth", status: "green", measurement: "7/32 in" },
            { item: "Cabin air filter", status: "yellow", evidence: ["photo:cabin-filter.jpg"] },
          ],
        }, "record inspection");
      } },
    { id: "estimate", title: "A7 builds the sourced estimate", personaLabel: "Anthony (Service advisor)", token: T.service,
      actLabel: "build_estimate",
      narrative: "Findings become lines with procedures, labor times, parts and coverage — every number carries a source_ref.",
      screen: (ctx) => `/dealer/service/${ctx.serviceJourneyId || ""}/estimate`,
      act: async (ctx, c) => {
        const r = await c.post<any>(`/service/ros/${ctx.roId}/estimate`, {}, "build estimate");
        const eid = r?.estimate_id || r?.estimate?.estimate_id;
        if (eid) return { estimateId: String(eid) };
        const sj = await c.get<any>(`/service/journeys/${ctx.serviceJourneyId}`, "find estimate id");
        const e2 = sj?.estimate?.estimate_id || sj?.estimate_id;
        if (e2) return { estimateId: String(e2) };
        c.note("estimate id not found", false);
      } },
    { id: "presentEst", title: "Estimate presented to the customer", personaLabel: "Anthony (Service advisor)", token: T.service,
      actLabel: "sends the magic link",
      narrative: "The customer gets the same rec-card/coverage components the advisor sees — one source of truth.",
      screen: (ctx) => `/dealer/service/${ctx.serviceJourneyId || ""}/estimate`,
      act: async (ctx, c) => { await c.post(`/service/estimates/${ctx.estimateId}/present`, {}, "present estimate"); } },
    { id: "approve", title: "Customer approves line-by-line on the phone",
      personaLabel: (ctx) => `${(ctx.customerName || "The owner").split(" ")[0]} (Customer)`,
      token: (ctx) => ctx.customerId ? `cust:${ctx.customerId}` : T.customer,
      actLabel: "records decisions",
      narrative: "Photo evidence, plain-language explain_finding, coverage badges, good-better-best; one line is declined.",
      screen: (ctx) => `/garage/service/${ctx.serviceJourneyId || ""}`,
      act: async (ctx, c) => {
        const d = await c.get<any>(`/garage/service/${ctx.serviceJourneyId}`, "read estimate lines");
        const lines: any[] = d?.estimate?.lines || d?.lines || [];
        if (!lines.length) { c.note("no estimate lines visible to the customer", false); return; }
        const decisions = lines.map((l: any, i: number) => ({
          line_id: l.line_id || l.id, decision: i < lines.length - 1 ? "approve" : "decline",
        }));
        await c.post(`/garage/service/estimates/${ctx.estimateId}/decisions`, { decisions }, `approve ${decisions.length - 1}, decline 1`);
      } },
    { id: "parts", title: "Parts ordered; RO resumes when they land", personaLabel: "Andre (Parts counter)", token: T.parts,
      actLabel: "order + receive",
      narrative: "The ETA tracker watches the order against the promised time; receiving emits parts.received and A7 resumes REPAIR.",
      screen: () => "/dealer/service/parts",
      act: async (ctx, c) => {
        // order a part the RO actually needs and does not have on the shelf — never one already on hand
        const ro = await c.get<any>(`/service/journeys/${ctx.serviceJourneyId}`, "the RO's parts");
        const need = (ro?.parts || []).find((p: any) => !p.on_hand) || (ro?.parts || [])[0];
        const partNumber = need?.part_number || ctx.partNumber || "BRK-PAD-F-OEM";
        const o = await c.post<any>("/service/parts/orders", { ro_id: ctx.roId, part_number: partNumber, qty: need?.qty || 1, eta: soon(2) },
          `order ${need?.description || partNumber}${need && !need.on_hand ? " (not on hand)" : ""}`);
        const oid = o?.parts_order_id || o?.order_id || o?.order?.parts_order_id || o?.order?.order_id;
        if (oid) await c.patch(`/service/parts/orders/${oid}`, { status: "received" }, "mark received → notify advisor");
      } },
    { id: "ready", title: "Repair done — vehicle ready, pickup offered", personaLabel: "Anthony (Service advisor)", token: T.service,
      actLabel: "closes the RO",
      narrative: "Line status flows to READY; notify_ready sends the invoice and offers a pickup slot.",
      screen: (ctx) => `/dealer/service/${ctx.serviceJourneyId || ""}`,
      act: async (ctx, c) => {
        // the customer's decision is recorded; A7's estimate-decision gate now lets the RO into REPAIR
        await c.post(`/service/${ctx.serviceJourneyId}/advance`, { evidence: "customer approved the estimate in My Garage (replay)" }, "APPROVE → REPAIR");
        const sj = await c.get<any>(`/service/journeys/${ctx.serviceJourneyId}`, "read lines");
        const line = (sj?.ro?.lines || sj?.lines || [])[0];
        if (line) await c.patch(`/service/ros/${ctx.roId}/lines/${line.line_id || line.id}/status`, { status: "complete" }, "line complete");
        await c.post(`/service/ros/${ctx.roId}/close`, {}, "notify ready + invoice");
        // the customer collects the car: READY → FOLLOW_UP, so the visit leaves the lane and the
        // technician's list, and the next booking of this car opens a new visit
        await c.post(`/service/${ctx.serviceJourneyId}/advance`, { evidence: "customer picked up the vehicle (replay)" }, "READY → FOLLOW_UP (picked up)");
      } },
    { id: "declined", title: "Declined work enters the follow-up register", personaLabel: "Anthony (Service advisor)", token: T.service,
      narrative: "The declined line is scheduled on the 30/60/90-day cadence with a one-tap booking link.",
      screen: () => "/dealer/service/declined" },
  ],
};

// ---- Scenario 3 · Service → Sales repurchase handoff ---------------------------

const s2s: Scenario = {
  id: "equity-upgrade",
  name: "Service lane to showroom — the repurchase handoff",
  process: "Service → Sales",
  description: "A7 flags an equity-positive car in today's lane; A3 writes the upsell brief; the handoff creates a DISCOVER journey (source=service_lane) that an advisor picks up — the moment Process 2 feeds Process 1.",
  steps: [
    { id: "lane", title: "Equity flag in today's Service Lane", personaLabel: "Elizabeth (Sales advisor)", token: T.advisor,
      actLabel: "reads the lane",
      narrative: "The sales-facing read view over A7's traffic: equity flags and the A3 upsell brief. The sales floor never edits an RO.",
      screen: () => "/dealer/assistant/service",
      act: async (_ctx, c) => {
        const lane = await c.get<any>("/service/lane", "today's lane");
        const rows: any[] = lane?.items || [];
        const equity = (r: any) => (r.flags || []).some((f: any) => f.flag === "positive_equity");
        // an equity-positive owner who is NOT already with sales: that is the one a handoff creates a journey for
        const hit = rows.find((r: any) => equity(r) && !(r.sales_journeys || []).length) || rows.find(equity) || rows[0];
        if (!hit) { c.note("lane is empty today", false); return; }
        const eq = (hit.flags || []).find((f: any) => f.flag === "positive_equity");
        c.note(`candidate: ${hit.customer_name || "customer"} · ${hit.vehicle_name || hit.vin || ""}${eq ? ` · equity ${eq.value}` : ""}`);
        return { laneId: String(hit.service_journey_id || ""), vin: hit.vin };
      } },
    { id: "handoff", title: "Hand to sales — a DISCOVER journey is born", personaLabel: "Anthony (Service advisor)", token: T.service,
      actLabel: "creates the handoff",
      narrative: "A7 hands the customer to A2 with source=service_lane; the two supervisors meet only through events and explicit handoffs.",
      screen: () => "/dealer/assistant/service",
      act: async (ctx, c) => {
        const r = await c.post<any>(`/service/${ctx.laneId}/handoff-to-sales`, { reason: "equity_positive" }, "handoff to sales");
        const jid = r?.journey_id || r?.journey?.journey_id;
        if (jid) return { newJourneyId: String(jid) };
      } },
    { id: "board", title: "The journey appears on the kanban with its chip", personaLabel: "Elizabeth (Sales advisor)", token: T.advisor,
      actLabel: "finds it on the board",
      narrative: "DISCOVER lane, source=service_lane chip; the receiving advisor got a Task.",
      screen: () => "/dealer/journeys",
      act: async (ctx, c) => {
        if (ctx.newJourneyId) return;
        const b = await c.get<any>("/journeys/board", "journeys board");
        const rows: any[] = Object.values(b?.lanes || {}).flat() as any[];
        const j = rows.find((x: any) => (x.source || x.origin) === "service_lane");
        if (j) return { newJourneyId: String(j.journey_id) };
        c.note("no service_lane journey found on the board", false);
      } },
    { id: "advisor", title: "Advisor engages the upgrade conversation", personaLabel: "Elizabeth (Sales advisor)", token: T.advisor,
      narrative: "A1 ranks upgrade matches against the equity position; A4 keeps the payment delta honest.",
      screen: (ctx) => ctx.newJourneyId ? `/dealer/advisor/${ctx.newJourneyId}` : "/dealer/advisor" },
    { id: "pulse", title: "The GM sees it land in the store pulse", personaLabel: "Raj (Dealer principal)", token: T.manager,
      narrative: "Store Cockpit · Service tab counts the repurchase handoff; the funnel shows where it came from.",
      screen: () => "/dealer/cockpit" },
  ],
};

// ---- Scenario 4 · Inventory manager's morning (A1 + A4) ------------------------

const inventory: Scenario = {
  id: "inventory-morning",
  name: "Inventory manager — aging vs demand, trade dispositions",
  process: "Process 1 · Sales",
  description: "Emma scans the VIN grid where A1's demand scores meet aging stock, reserves a hot VIN against an active journey, decides an A4 trade disposition, and checks the service-drive acquisition targets.",
  steps: [
    { id: "grid", title: "Morning scan: aging vs A1 demand", personaLabel: "Emma (Inventory manager)", token: T.inventory,
      actLabel: "reads the VIN grid",
      narrative: "The Demand scorer re-ranked every VIN overnight against active journey shortlists.",
      screen: () => "/dealer/inventory",
      act: async (_ctx, c) => {
        const inv = await c.get<any>("/inventory", "VIN grid");
        const rows: any[] = inv?.vehicles || inv?.items || inv?.inventory || [];
        const hot = rows.find((r: any) => (r.demand_score ?? r.demand ?? 0) > 0) || rows[0];
        if (!hot) { c.note("no inventory rows", false); return; }
        c.note(`hot VIN ${hot.vin} · demand ${hot.demand_score ?? hot.demand ?? "—"} · age ${hot.age_days ?? "—"}d`);
        return { vinHot: hot.vin, modelHot: hot.model_id };
      } },
    { id: "reserve", title: "Reserve the in-demand VIN", personaLabel: "Emma (Inventory manager)", token: T.inventory,
      actLabel: "reserves it",
      narrative: "Reserving against an active journey keeps the floor from selling the same car twice.",
      screen: () => "/dealer/inventory?status=reserved",
      act: async (ctx, c) => {
        const b = await c.get<any>("/journeys/board", "find a journey shopping this model");
        const lanes = b?.lanes || {};
        const cands = [...(lanes.CONFIGURE || []), ...(lanes.QUALIFY || []), ...(lanes.DISCOVER || [])];
        const j = cands.find((x: any) => ctx.modelHot && x.primary_model_id === ctx.modelHot) || cands[0];
        if (!j) { c.note("no active journey to reserve against", false); return; }
        await c.post(`/inventory/${ctx.vinHot}/reserve`, { journey_id: j.journey_id }, `reserve ${ctx.vinHot} for ${j.customer_name || j.journey_id}`);
      } },
    { id: "disposition", title: "Decide an A4 trade disposition", personaLabel: "Emma (Inventory manager)", token: T.inventory,
      actLabel: "keep for CPO",
      narrative: "A4 recommends keep-for-CPO vs wholesale vs auction with reasoning from comps, age and demand.",
      screen: () => "/dealer/inventory?tab=trades",
      act: async (_ctx, c) => {
        const d = await c.get<any>("/trades/dispositions", "disposition queue");
        const row = (d?.dispositions || d?.items || d?.queue || [])[0];
        if (!row) { c.note("disposition queue is empty", false); return; }
        await c.post(`/trades/${row.disposition_id || row.trade_id || row.id}/decide`,
          { decision: row.recommendation || "retail" }, `decide ${row.disposition_id || ""} → ${row.recommendation || "retail"}`);
      } },
    { id: "acquire", title: "Acquisition scout: buy from the service drive", personaLabel: "Emma (Inventory manager)", token: T.inventory,
      narrative: "Equity-positive, high-demand vehicles sitting in today's service lane are the cheapest used inventory you can buy.",
      screen: () => "/dealer/inventory?tab=acquisitions",
      act: async (_ctx, c) => {
        const a = await c.get<any>("/inventory/acquisitions", "acquisition targets");
        const n = (a?.acquisitions || a?.items || []).length;
        c.note(n ? `${n} service-drive acquisition target${n === 1 ? "" : "s"} today` : "no acquisition targets today");
      } },
  ],
};

// ---- Scenario 5 · Service manager's cockpit hour (A7) --------------------------

const svcManager: Scenario = {
  id: "service-cockpit",
  name: "Service manager — cockpit, what-if, goodwill",
  process: "Process 2 · Service",
  description: "Fixed-ops hour: read the Service Cockpit KPIs, drill a problem tile into the filtered board, run the repair-vs-replace what-if, and clear the approvals queue (goodwill).",
  steps: [
    { id: "cockpit", title: "Fixed-ops pulse on the Service Cockpit", personaLabel: "Ethan (Service manager)", token: T.svcManager,
      actLabel: "reads the cockpit",
      narrative: "Lane load, cycle time by wait type, MPI completion, close rate — plus A7's sourced [AI INSIGHT].",
      screen: () => "/dealer/service/cockpit",
      act: async (_ctx, c) => {
        const d = await c.get<any>("/service/cockpit", "cockpit read model");
        const t = d?.tiles || {};
        const close = t.recommended_work_close_rate ?? t.close_rate ?? d?.close_rate;
        c.note(`lane load ${t.lane_load?.today ?? "—"} · close rate ${typeof close === "number" ? `${Math.round(close * 100)}%` : close ?? "—"}`);
      } },
    { id: "drill", title: "Drill the at-risk tile into the Board", personaLabel: "Ethan (Service manager)", token: T.svcManager,
      narrative: "Every cockpit tile deep-links to the Board with the matching filter applied.",
      screen: () => "/dealer/service?badge=overdue_promise" },
    { id: "whatif", title: "What-if: raise the repair-vs-replace threshold", personaLabel: "Ethan (Service manager)", token: T.svcManager,
      actLabel: "recomputes the what-if",
      narrative: "A7's Code Interpreter recomputes recovered revenue at the new threshold — auditable math, not a guess.",
      screen: () => "/dealer/service/cockpit?rvr=65&rate=15&close=5",
      act: async (_ctx, c) => { await c.get("/service/cockpit?rvr_threshold_pct=65&labor_rate_delta_pct=15&close_rate_delta_pts=5", "what-if at 65% / +15% labor / +5 pts close"); } },
    { id: "goodwill-ask", title: "Advisor asks for goodwill on a long-tenure customer", personaLabel: "Anthony (Service advisor)", token: T.service,
      actLabel: "raises the request",
      narrative: "A7 packages the ask with service history, coverage and margin context; the advisor can never approve it himself.",
      screen: () => "/dealer/service",
      act: async (_ctx, c) => {
        const q = await c.get<any>("/approvals?status=pending", "pending approvals");
        const open = (q?.approvals || q?.items || []).find((a: any) => (a.type || a.kind || "") === "goodwill");
        if (open) { c.note(`goodwill request ${open.approval_id} already pending`); return { approvalId: String(open.approval_id) }; }
        // goodwill is asked on real work: the board's visit whose RO has lines and a part on back-order
        const board = await c.get<any>("/service/board", "the lane");
        const cards: any[] = Object.values(board?.lanes || {}).flat() as any[];
        const held = cards.find((x: any) => x.badges?.parts_hold) || cards.find((x: any) => ["REPAIR", "APPROVE"].includes(x.stage));
        const detail = held ? await c.get<any>(`/service/journeys/${held.service_journey_id}`, "its repair order") : null;
        const lines: any[] = detail?.lines || [];
        const line = lines.find((l: any) => (l.customer_amount || l.line_total) > 0) || lines[0];
        const amount = line ? Math.round(((line.customer_amount || line.line_total) as number) * 0.5 * 100) / 100 : 263.5;
        const g = await c.post<any>("/approvals", {
          type: "goodwill", scope: { dealer_id: "D-101" },
          reason: `${held?.customer_name || "Long-tenure customer"}, out of warranty by 2 months — cover 50% of ${line?.item || line?.labor_op || "the brake job"}`,
          payload: { vin: held?.vin || "7M7UYZ8R7SL000194", customer_id: held?.customer_id || "C-US-J1", service_journey_id: held?.service_journey_id,
                     ro_id: detail?.repair_order?.ro_id, line: line?.item || line?.labor_op || "Front brake pads & rotors", amount, currency: "USD",
                     pct_of_line: 50, coverage: "basic warranty expired", margin_after: 0.31 },
        }, `request goodwill ($${amount})`);
        const id = g?.approval_id || g?.approval?.approval_id;
        if (id) return { approvalId: String(id) };
        c.note("goodwill request did not return an id", false);
      } },
    { id: "goodwill", title: "Service manager clears the goodwill approval", personaLabel: "Ethan (Service manager)", token: T.svcManager,
      actLabel: "approves it",
      narrative: "The decision is audited; approving records the decision only — the credit is applied on the RO by the advisor.",
      screen: () => "/approvals?status=all",
      act: async (ctx, c) => {
        let id = ctx.approvalId;
        if (!id) {
          const q = await c.get<any>("/approvals?status=pending", "approvals queue");
          id = (q?.approvals || q?.items || []).find((a: any) => (a.type || a.kind || "") === "goodwill")?.approval_id;
        }
        if (!id) { c.note("no goodwill requests pending", false); return; }
        await c.post(`/approvals/${id}/decide`, { decision: "approve", reason: "retention — long-tenure customer" }, "approve goodwill");
      } },
  ],
};

// ---- Scenario 6 · District manager's Monday (A6 shell — reads + field tools) ----

const dmWeek: Scenario = {
  id: "dm-monday",
  name: "District manager — the Monday cycle",
  process: "Process 1 · Sales",
  description: "Casey's week, prepared by A6: read Today's district pulse, triage the HIGH alerts, file a contact report after a dealer visit, draft an action plan for the at-risk account, and sanity-check an allocation scenario. (A6 itself is Quick-hosted; this play drives the workspace shell.)",
  steps: [
    { id: "today", title: "District pulse — Today", personaLabel: "Casey (District manager)", token: T.dm,
      actLabel: "reads Today",
      narrative: "MTD retail, objective attainment, days supply, dealers at risk — and the week A6 prepared.",
      screen: () => "/oem",
      act: async (_ctx, c) => {
        const d = await c.get<any>("/oem/today", "district pulse");
        const risk = d?.dealers_at_risk?.length ?? d?.at_risk?.length;
        c.note(risk != null ? `${risk} dealer(s) at risk this week` : "pulse loaded");
        const acct = (d?.dealers_at_risk || d?.at_risk || [])[0];
        if (!acct) return undefined;
        // Today's at-risk rows carry the dealer id; /oem/accounts/{id} and draft-plan key on the
        // account id (ACC-…), so resolve it through the scoped account list.
        const dealerId = String(acct.dealer_id || "");
        let accountId = String(acct.account_id || acct.id || "");
        if (!accountId && dealerId) {
          const list = await c.get<any>("/oem/accounts", "resolve account id for the at-risk dealer");
          accountId = String((list?.accounts || []).find((a: any) => a.dealer_id === dealerId)?.account_id || "");
        }
        return { accountId, dealerId };
      } },
    { id: "alerts", title: "Triage the HIGH alerts", personaLabel: "Casey (District manager)", token: T.dm,
      actLabel: "acknowledges one",
      narrative: "HIGH means act today; the alert carries its recommended action and deep link.",
      screen: () => "/oem/alerts",
      act: async (_ctx, c) => {
        const d = await c.get<any>("/oem/alerts?status=open", "open alerts");
        // the route groups by severity (by_severity.HIGH); older shapes carried a flat list
        const high = (d?.by_severity?.HIGH || d?.alerts || d?.items || [])
          .find((a: any) => (a.severity || "HIGH").toUpperCase() === "HIGH");
        if (!high) { c.note("no HIGH alerts open"); return; }
        await c.patch(`/oem/alerts/${high.alert_id || high.id}`, { action: "acknowledge" }, `acknowledge: ${high.title || high.kind || "alert"}`);
      } },
    { id: "report", title: "File the contact report (dictated)", personaLabel: "Casey (District manager)", token: T.dm,
      actLabel: "files the report",
      narrative: "Voice-dictated in the field; follow-up items become dealer tasks with deadlines.",
      screen: () => "/oem/contact-reports",
      act: async (ctx, c) => {
        await c.post("/oem/contact-reports", {
          dealer_id: ctx.dealerId || "D-101",
          summary: "Visited the store: used-car reconditioning backlog is the constraint; agreed on a 10-day turn target.",
          topics: ["used-car ops", "reconditioning backlog"],
          followups: [{ text: "Share recon benchmark pack", due: soon(72).slice(0, 10) }],
        }, "file contact report");
      } },
    { id: "plan", title: "Draft the action plan for the at-risk account", personaLabel: "Casey (District manager)", token: T.dm,
      actLabel: "drafts via A6",
      narrative: "A6 drafts; the DM reviews and publishes — the agent never publishes on its own.",
      screen: (ctx) => ctx.accountId ? `/oem/accounts/${ctx.accountId}` : "/oem/action-plans",
      act: async (ctx, c) => {
        if (!ctx.accountId) { c.note("no at-risk account captured", false); return; }
        await c.post(`/oem/accounts/${ctx.accountId}/draft-plan`, {}, "draft action plan");
      } },
    { id: "scenario", title: "Sanity-check an allocation scenario", personaLabel: "Casey (District manager)", token: T.dm,
      actLabel: "runs the projection",
      narrative: "Sliders → projected impact, always labeled an estimate (GR-001/GR-005).",
      screen: () => "/oem/scenario?allocation=15&incentive=500&supply=-5&growth=2",
      act: async (_ctx, c) => {
        await c.post("/oem/scenario", { allocation_pct: 15, incentive_per_unit: 500, days_supply_delta: -5, market_growth_pct: 2 }, "project +15% allocation / +$500 incentive");
      } },
  ],
};

// ---- Scenario 7 · Marketing → Finance → CX chain (A5 + A2 policy) ----------------

const marketingChain: Scenario = {
  id: "roi-chain",
  name: "Marketing studio — ROI to approved budget shift",
  process: "Process 1 · Sales",
  description: "William reads attribution and submits A5's nightly budget-shift draft; Fatima (finance) approves it with the expected impact in front of her; Sarah (CX) reviews the nurture policy that will spend the shifted budget.",
  steps: [
    { id: "roi", title: "Attribution: where the money actually sells cars", personaLabel: "William (Marketing analyst)", token: T.marketing,
      actLabel: "reads ROI",
      narrative: "ROI by campaign and dealer, funnel drop-off by source — and A5's shift recommendation.",
      screen: () => "/marketing/campaigns",
      act: async (_ctx, c) => {
        const d = await c.get<any>("/marketing/campaigns", "campaign ROI");
        const rec = d?.budget_shift || d?.recommendation;
        const sh = rec?.shift || rec;
        const name = (x: any) => (x && typeof x === "object" ? x.name || x.campaign_id : x) || "?";
        c.note(rec ? `A5 recommends: ${rec.summary || `shift ${sh?.amount ? `${sh.currency || ""} ${Math.round(sh.amount).toLocaleString()} `.trim() + " " : ""}from ${name(sh?.from)} to ${name(sh?.to)}`}` : "no shift recommendation in the read model");
      } },
    { id: "submit", title: "Submit the budget shift for finance approval", personaLabel: "William (Marketing analyst)", token: T.marketing,
      actLabel: "routes to Approvals",
      narrative: "The recommendation step is agent work (AgentCore) — traceable, guardrailed, and routed to approval. Dashboards stay in Quick.",
      screen: () => "/marketing/campaigns",
      act: async (_ctx, c) => { await c.post("/marketing/campaigns/budget-shift/submit", {}, "submit budget shift"); } },
    { id: "approve", title: "Finance approves with the impact in view", personaLabel: "Fatima (OEM finance)", token: T.finance,
      actLabel: "decides the approval",
      narrative: "The approval card carries the expected impact: shift amount, ROI, projected deliveries.",
      screen: () => "/approvals",
      act: async (_ctx, c) => {
        const q = await c.get<any>("/approvals", "approvals queue");
        const b = (q?.approvals || q?.items || []).find((a: any) => (a.type || a.kind || "").includes("budget"));
        if (!b) { c.note("no budget-shift approval pending", false); return; }
        await c.post(`/approvals/${b.approval_id || b.id}/decide`, { decision: "approve", reason: "ROI-positive reallocation" }, "approve budget shift");
      } },
    { id: "audience", title: "Refresh the audiences the budget will buy", personaLabel: "William (Marketing analyst)", token: T.marketing,
      narrative: "Personalize segments refresh nightly; exports go to the ad platforms as hashed ids only.",
      screen: () => "/marketing/audiences" },
    { id: "policy", title: "CX reviews the nurture policy that spends it", personaLabel: "Sarah (CX / journey ops)", token: T.cx,
      narrative: "Cadence caps, quiet hours, stop conditions — the policy A2 executes on every journey, versioned and previewable.",
      screen: () => "/marketing/journey-ops" },
  ],
};

// ---- Scenario 8 · Predictive maintenance → lead → customer offer ----------------

const predictive: Scenario = {
  id: "predictive-offer",
  name: "Predictive maintenance — a part prediction becomes a customer offer",
  process: "Process 2 · Service",
  description: "A7's predictive watch reads mileage and wear curves, raises a lead on the Lead Desk before the part fails, the BDC sends the offer, and Jordan sees it in My Garage and books with the discount.",
  steps: [
    { id: "scan", title: "A7 predicts the part before it fails", personaLabel: "A7 (Keisha runs the scan)", token: T.bdc,
      actLabel: "runs the predictive scan",
      narrative: "Mileage vs wear curves plus service history — a front-brake prediction with its confidence and basis.",
      screen: (ctx) => ctx.leadId ? `/dealer/leads?lead=${encodeURIComponent(ctx.leadId)}` : "/dealer/leads",
      act: async (_ctx, c) => {
        const out = await c.post<any>("/service/predictive/run", {}, "predictive scan (fleet)");
        c.note(`${out?.scanned ?? "?"} vehicles scanned, ${out?.leads_raised ?? 0} lead(s) raised`);
        // make sure the demo customer's own vehicle is covered regardless of fleet ranking
        await c.post("/service/predictive/run", { vin: "7M7UYZ8R7SL000194" }, "scan Jordan's vehicle");
        const q = await c.get<any>("/leads/queue", "find the predictive lead");
        const rows: any[] = Object.values(q?.queue || {}).flat() as any[];
        // a heads-up nobody has booked yet: Jordan's when his is still open, else the next one
        // (a booked heads-up shows "booked" in My Garage, so it cannot be demoed again)
        const preds = rows.filter((l: any) => l.source === "predictive_maintenance" && !l.service_appointment
          && l.bucket !== "closed" && !["won", "lost", "closed"].includes(String(l.status || "")));
        const lead = preds.find((l: any) => l.customer_id === "C-US-J1") || preds[0];
        if (!lead) { c.note("every predictive heads-up is already booked — run deploy/demo-reset.sh", false); return; }
        const part = lead.predicted_part || (String(lead.comments || "").match(/predicts ([^.]+?) due/) || [])[1] || "predicted part";
        c.note(`lead ${lead.lead_id}: ${part} for ${lead.customer_name || lead.customer_id}`);
        return { leadId: lead.lead_id, customerId: String(lead.customer_id || ""), customerName: lead.customer_name || "" };
      } },
    { id: "offer-out", title: "BDC works the lead inside its SLA", personaLabel: "Keisha (BDC)", token: T.bdc,
      actLabel: "sends the offer",
      narrative: "The queue shows a 🔮 Predictive badge with the part and confidence; A3 drafts the outreach around the offer.",
      screen: (ctx) => ctx.leadId ? `/dealer/leads?lead=${encodeURIComponent(ctx.leadId)}` : "/dealer/leads",
      act: async (ctx, c) => {
        if (!ctx.leadId) { c.note("no lead captured", false); return; }
        await c.post(`/leads/${ctx.leadId}/respond`, { channel: "email" }, "send predictive offer");
      } },
    { id: "garage", title: "Jordan sees the heads-up in My Garage", personaLabel: (ctx) => `${(ctx.customerName || "Jordan").split(" ")[0]} (Customer)`,
      token: (ctx) => ctx.customerId ? `cust:${ctx.customerId}` : T.customer,
      narrative: "The same lead, from the customer's side: the part, the predicted mileage, and the early-bird discount.",
      screen: () => "/garage" },
    { id: "book", title: "Booked ahead — with the discount", personaLabel: (ctx) => `${(ctx.customerName || "Jordan").split(" ")[0]} (Customer)`,
      token: (ctx) => ctx.customerId ? `cust:${ctx.customerId}` : T.customer,
      actLabel: "books the service",
      narrative: "The booking carries the predicted service, so the lane is staged before the car arrives.",
      screen: () => "/garage/service",
      act: async (_ctx, c) => {
        const own = await c.get<any>("/garage/ownership", "find the offer");
        const offer = (own?.service_offers || []).find((o: any) => !o.booked);
        if (!offer) { c.note("no open, unbooked offer", false); return; }
        const slots = await c.get<any>(`/garage/service/slots?vin=${encodeURIComponent(offer.vin)}`, "slot picker");
        const slot = slots?.slots?.[0];
        const r = await c.post<any>("/garage/service/book", {
          vin: offer.vin, slot_start: slot?.start || slot?.slot_start || soon(30),
          wait_type: "drop", services: offer.parts?.length ? offer.parts : [offer.part],
        }, `book ${(offer.parts?.length ? offer.parts : [offer.part]).join(", ")} with the ${offer.offer_pct}% offer`);
        const sid = r?.service_journey_id || r?.booking?.service_journey_id;
        if (sid) return { serviceJourneyId: String(sid) };
      } },
    { id: "board", title: "The lane sees it coming", personaLabel: "Anthony (Service advisor)", token: T.service,
      narrative: "The booking lands on the Service Board with the predicted work already on it — Process 2 started by a prediction, not a breakdown.",
      screen: () => "/dealer/service" },
  ],
};

export const SCENARIOS: Scenario[] = [sales, service, s2s, predictive, inventory, svcManager, dmWeek, marketingChain];

// ---- replay state (localStorage) ------------------------------------------------

const KEY = "retail-ops.replay";

/** A step's persona token / label for the current run context. */
export function stepToken(step: ReplayStep, ctx: Ctx): string {
  return typeof step.token === "function" ? step.token(ctx) : step.token;
}
export function stepPersona(step: ReplayStep, ctx: Ctx): string {
  return typeof step.personaLabel === "function" ? step.personaLabel(ctx) : step.personaLabel;
}

/** One executed step, for the history view. */
export interface RunLogEntry { stepId: string; title: string; persona: string; at: string; notes: StepNote[] }

export interface ReplayState {
  scenarioId: string;
  next: number;                        // index of the next step to run
  ctx: Ctx;
  notes: Record<string, StepNote[]>;   // per executed step id
  log?: RunLogEntry[];                 // execution history, oldest first
  auto: boolean;
}

export function loadReplay(): ReplayState | null {
  if (typeof window === "undefined") return null;
  try { const raw = localStorage.getItem(KEY); return raw ? JSON.parse(raw) : null; } catch { return null; }
}
export function saveReplay(s: ReplayState | null): void {
  if (s) localStorage.setItem(KEY, JSON.stringify(s));
  else localStorage.removeItem(KEY);
}
export function scenarioById(id: string): Scenario | null {
  return SCENARIOS.find((s) => s.id === id) || null;
}

/** Caller used by the controller: wraps the api client, collecting notes. */
export function makeCaller(notes: StepNote[]): Caller {
  const wrap = async <T,>(fn: () => Promise<T>, label: string): Promise<T | null> => {
    try { const r = await fn(); notes.push({ ok: true, text: label }); return r; }
    catch (e: any) { notes.push({ ok: false, text: `${label} — ${e?.message || "failed"}` }); return null; }
  };
  return {
    get: (p, why) => wrap(() => apiGet(p), why || `GET ${p}`),
    post: (p, b, why) => wrap(() => apiPost(p, b ?? {}), why || `POST ${p}`),
    patch: (p, b, why) => wrap(() => apiPatch(p, b ?? {}), why || `PATCH ${p}`),
    note: (text, ok = true) => notes.push({ ok, text }),
  };
}
