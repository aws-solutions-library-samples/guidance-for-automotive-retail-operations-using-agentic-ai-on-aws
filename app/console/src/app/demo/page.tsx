"use client";

// Demo portal: the landing of the admin roles (nav.ts DEMO_LANDING_ROLES); every
// other persona lands on its home and opens it from shell bar › Demo (§5.18.1).
// Two ways to proceed, side by side:
//   ① Explore as a persona (one-click Enter into any of the 15 widgets)
//   ② Replay a scenario (a whole business process, auto-driven)
// The portal always shows WHO you are: arriving here resets any admin View-as
// impersonation back to the real signed-in user, so a demo never starts half
// inside the previous persona.

import { CSSProperties, useEffect, useState } from "react";
import { getActAs, setActAs } from "@/lib/api";
import { hasCognitoSession } from "@/lib/auth";
import { usePersona } from "@/lib/persona";
import { Btn, cx } from "@/components/app";
import { humanizeAgents } from "@/lib/agents";
import { ReplayState, SCENARIOS, actAsPersona, loadReplay, saveReplay, stepPersona } from "@/lib/replay";

type App = "Dealer Console" | "Service Desk" | "OEM Workspace" | "Marketing Studio" | "Admin" | "Customer Web";

interface PersonaWidget {
  icon: string; title: string; person: string; role: string; token: string;
  app: App; home: string; show: string[];
}

// Per-app gradient identity (drives ::before stripe, icon tile, chip + glow).
const APP_STYLE: Record<App, CSSProperties> = {
  "Dealer Console": { "--pc-grad": "linear-gradient(135deg,#6366f1,#8b5cf6)", "--pc-glow": "rgba(99,102,241,.3)", "--pc-line": "rgba(129,140,248,.6)" } as CSSProperties,
  "Service Desk":   { "--pc-grad": "linear-gradient(135deg,#10b981,#14b8a6)", "--pc-glow": "rgba(16,185,129,.3)", "--pc-line": "rgba(45,212,191,.6)" } as CSSProperties,
  "OEM Workspace":  { "--pc-grad": "linear-gradient(135deg,#8b5cf6,#d946ef)", "--pc-glow": "rgba(168,85,247,.3)", "--pc-line": "rgba(192,132,252,.6)" } as CSSProperties,
  "Marketing Studio": { "--pc-grad": "linear-gradient(135deg,#f59e0b,#f97316)", "--pc-glow": "rgba(245,158,11,.3)", "--pc-line": "rgba(251,191,36,.6)" } as CSSProperties,
  Admin:            { "--pc-grad": "linear-gradient(135deg,#64748b,#0f766e)", "--pc-glow": "rgba(15,118,110,.28)", "--pc-line": "rgba(94,234,212,.5)" } as CSSProperties,
  "Customer Web":   { "--pc-grad": "linear-gradient(135deg,#ec4899,#f43f5e)", "--pc-glow": "rgba(236,72,153,.3)", "--pc-line": "rgba(244,114,182,.6)" } as CSSProperties,
};

const PERSONAS: PersonaWidget[] = [
  { icon: "👔", title: "Dealer principal / GM", person: "Raj Nelson", role: "dealer_manager", token: "user:U-US-00001",
    app: "Dealer Console", home: "/dealer/cockpit",
    show: ["Store Cockpit: 9 tabs, tiles deep-link", "[AI INSIGHT] per tab", "Alerts & OEM comms"] },
  { icon: "🤝", title: "Sales advisor", person: "Elizabeth Thomas", role: "dealer_advisor", token: "user:U-US-00002",
    app: "Dealer Console", home: "/dealer/journeys",
    show: ["Advisor (live): objection coach", "EV-vs-ICE TCO + talk tracks", "Journeys kanban → drawer"] },
  { icon: "☎️", title: "BDC agent", person: "Keisha Baker", role: "dealer_bdc", token: "user:U-US-00005",
    app: "Dealer Console", home: "/dealer/leads",
    show: ["Lead Desk SLA queue", "Sales Agent first response, cited", "Assign = handoff + task"] },
  { icon: "📑", title: "F&I manager", person: "Michael Lopez", role: "dealer_fni", token: "user:U-US-00006",
    app: "Dealer Console", home: "/dealer/fni",
    show: ["Worksheet + lender offers", "AI attach menu, customer mode", "Compliance rail"] },
  { icon: "🚗", title: "Inventory manager", person: "Emma Washington", role: "dealer_inventory", token: "user:U-US-00008",
    app: "Dealer Console", home: "/dealer/inventory",
    show: ["Age vs Discovery Agent demand score", "Trade-In Agent dispositions + reasoning", "Service-drive acquisitions"] },
  { icon: "🚚", title: "Delivery coordinator", person: "Megan White", role: "dealer_delivery", token: "user:U-US-00009",
    app: "Dealer Console", home: "/dealer/delivery",
    show: ["Kanban to Delivered", "Prep checklist incl. EV", "Delivered → survey via the Journey Agent"] },
  { icon: "🔧", title: "Service advisor", person: "Anthony Nguyen", role: "dealer_service", token: "user:U-US-00007",
    app: "Service Desk", home: "/dealer/service",
    show: ["Board + Service Agent attention list", "Sourced estimate, source_refs", "Phone approval flow"] },
  { icon: "🛠", title: "Technician / Parts", person: "via Anthony", role: "dealer_service", token: "user:U-US-00007",
    app: "Service Desk", home: "/dealer/service/my-ros",
    show: ["My ROs, tablet targets", "MPI with evidence gate", "Parts: receive → RO resumes"] },
  { icon: "📊", title: "District manager", person: "Casey Brennan", role: "oem_dm", token: "user:U-US-00044",
    app: "OEM Workspace", home: "/oem",
    show: ["Week prepared by the Field Sales Agent", "Alert triage HIGH/MED/LOW", "Scenario sliders"] },
  { icon: "🗺", title: "Regional manager", person: "Morgan Ellis", role: "oem_regional", token: "user:U-US-00045",
    app: "OEM Workspace", home: "/oem/region",
    show: ["Ranked district comparison", "One-click open-district", "HIGH-alert roll-up"] },
  { icon: "📈", title: "C&I planner", person: "Robert Okafor", role: "oem_ci_planning", token: "user:U-US-00046",
    app: "OEM Workspace", home: "/oem/accounts",
    show: ["Versioned account plans", "Inline citations + publish", "Scenario planning"] },
  { icon: "📣", title: "Marketing analyst", person: "William Scott", role: "oem_marketing", token: "user:U-US-00049",
    app: "Marketing Studio", home: "/marketing/campaigns",
    show: ["ROI + funnel drop-off", "Campaign Agent shift → finance approval", "Audience refresh + export"] },
  { icon: "💬", title: "CX / journey ops", person: "Sarah Mitchell", role: "oem_cx", token: "user:U-US-00050",
    app: "Marketing Studio", home: "/marketing/journey-ops",
    show: ["Nurture policy the Journey Agent executes", "Seeded-customer preview", "Journey Agent proposals tab"] },
  { icon: "⚙️", title: "Tenant admin", person: "Andre Wright", role: "admin_tenant", token: "user:U-US-00051",
    app: "Admin", home: "/admin",
    show: ["Guardrail test harness", "Autonomy matrix + ceilings", "View as any persona"] },
  { icon: "🏠", title: "Customer", person: "Jordan Alvarez", role: "customer", token: "cust:C-US-J1",
    app: "Customer Web", home: "/garage",
    show: ["My Garage: journey + builds", "Estimate approval (mobile)", "Shop discovery + chat"] },
];

const PROCESS_STYLE: Record<string, CSSProperties> = {
  "Process 1 · Sales": APP_STYLE["Dealer Console"],
  "Process 2 · Service": APP_STYLE["Service Desk"],
  "Service → Sales": APP_STYLE["OEM Workspace"],
};

export default function DemoPage() {
  const { persona, reload } = usePersona();
  const [active, setActive] = useState<ReplayState | null>(null);
  useEffect(() => { setActive(loadReplay()); }, []);

  // Arriving at the portal RESETS impersonation: the demo always starts as the
  // real signed-in user, visibly. (Dev-token mode has no underlying user to
  // return to, so there the current token simply stays what it is.)
  useEffect(() => {
    if (hasCognitoSession() && getActAs()) { setActAs(null); reload(); }
  }, [reload]);

  const isAdminish = !!persona && (["admin_tenant", "admin_dealer", "platform_admin"].includes(persona.role) || persona.impersonating);
  const needsAdmin = hasCognitoSession() && !!persona && !isAdminish;

  const enter = (p: PersonaWidget) => {
    actAsPersona(p.token);
    window.location.assign(p.home);
  };
  const start = (id: string, auto: boolean) => {
    const s: ReplayState = { scenarioId: id, next: 0, ctx: {}, notes: {}, log: [], auto };
    saveReplay(s);
    setActive(s);
    window.location.reload();
  };

  return (
    <div className="page">
      {/* hero: what this is + WHO YOU ARE, always visible */}
      <div className="portal-hero">
        {/* who-you-are lives in the shell bar's user menu (top right) — not repeated here */}
        <h1>Automotive Retail Operations — demo portal</h1>
        <p>One system · five apps · seven agents · two business processes. Pick a persona, or let a scenario drive itself.</p>
        {needsAdmin && (
          <p className="mt-2" style={{ color: "#fde68a", fontSize: ".76rem" }}>
            ⚠ Persona switching uses admin View-as — sign in as the tenant admin (andre.wright…) to enter other personas.
          </p>
        )}
      </div>

      {active && (
        <div className="card" style={{ borderLeft: "4px solid var(--primary)" }} role="status">
          A replay is in progress — use the floating controller below, or{" "}
          <button className="underline" onClick={() => { saveReplay(null); setActive(null); }}>end it</button>{" "}
          to start another.
        </div>
      )}

      {/* the two ways in, side by side */}
      <div className="portal-cols">
        <section className="portal-path" aria-labelledby="path-personas">
          <div className="portal-path-head">
            <span className="portal-path-num" style={{ background: "linear-gradient(135deg,#6366f1,#8b5cf6)" }}>1</span>
            <h2 id="path-personas">Explore as a persona</h2>
          </div>
          <p className="portal-path-sub">15 roles across the five apps — one click lands on that persona&apos;s home screen with their agents live.</p>
          <div className="portal-grid">
            {PERSONAS.map((p) => (
              <div key={p.title} className="pcard" style={APP_STYLE[p.app]}>
                <div className="pcard-top">
                  <span className="pcard-ico" aria-hidden>{p.icon}</span>
                  <span className="min-w-0">
                    <span className="pcard-title block">{p.title}</span>
                    <span className="pcard-sub">{p.person}</span>
                  </span>
                  <span className="pcard-app">{p.app.replace(" Console", "").replace(" Workspace", "")}</span>
                </div>
                <ul>{p.show.map((x) => <li key={x}>{x}</li>)}</ul>
                <Btn kind="primary" size="sm" onClick={() => enter(p)}>Enter →</Btn>
              </div>
            ))}
          </div>
        </section>

        <section className="portal-path" aria-labelledby="path-scenarios">
          <div className="portal-path-head">
            <span className="portal-path-num" style={{ background: "linear-gradient(135deg,#10b981,#14b8a6)" }}>2</span>
            <h2 id="path-scenarios">Replay a scenario</h2>
          </div>
          <p className="portal-path-sub">A whole business process end to end — each step fills real data through the live API, switching persona as it goes.</p>
          {SCENARIOS.map((sc) => (
            <div key={sc.id} className="scard" style={PROCESS_STYLE[sc.process] || APP_STYLE["Marketing Studio"]}>
              <div className="scard-head">
                <span className="scard-title">{sc.name}</span>
                <span className="pcard-app">{sc.process.replace("Process 1 · ", "").replace("Process 2 · ", "")}</span>
              </div>
              <div className="scard-steps">{sc.steps.map((st, i) => `${i + 1}. ${humanizeAgents(st.title.split("—")[0].split(":")[0].trim())}`).join("  ·  ")}</div>
              <div className="scard-persona-row">
                {[...new Set(sc.steps.map((st) => stepPersona(st, {}).split(" (")[0]))].map((n) => (
                  <span key={n} className="scard-pchip">{n}</span>
                ))}
              </div>
              <div className="flex gap-2">
                <Btn kind="primary" size="sm" disabled={!!active} onClick={() => start(sc.id, true)}>▶ Auto-play</Btn>
                <Btn kind="secondary" size="sm" disabled={!!active} onClick={() => start(sc.id, false)}>Step by step</Btn>
              </div>
            </div>
          ))}
          <p className={cx("dim text-[.68rem] m-0 mt-1")}>
            ☰ History in the floating controller shows every executed step, acting persona and API result.
          </p>
        </section>
      </div>
    </div>
  );
}
