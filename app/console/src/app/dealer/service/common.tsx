"use client";

import { missingText } from "@/lib/text";

// Shared Service Desk pieces (spec §5.20, design doc §3): the operating-screen
// frame (top-bar with RO header + promised-time timer + nav tabs, service
// stepper, AI Log), the A7 chat (grounded on the knowledge index), and badge
// helpers. Lives inside the route directory — the design-system components
// stay in @/components/app.

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ReactNode } from "react";
import {
  AiLog, Badge, ChatAnswer, ChatPanel, DateTime, ErrorState, LoadingBlock,
  MarketProvider, ServiceStepper, cx, stageLabel } from "@/components/app";
import { apiPost } from "@/lib/api";
import { Query, useApi } from "@/lib/hooks";
import { useCanOpen } from "@/lib/persona";

export const ADVISOR_ROLES = ["dealer_service", "dealer_service_manager", "dealer_manager"];
export const isAdvisor = (role?: string | null) => ADVISOR_ROLES.includes(role || "");

export function useServiceJourney(id: string, pollMs = 20000): Query<any> {
  return useApi<any>(id ? `/service/journeys/${encodeURIComponent(id)}` : null, { pollMs });
}

// A tab with a `screen` shows only to roles that hold it, so nobody gets a tab
// the server refuses: the estimate is read by the estimate roles only (GET
// /service/estimates/{id}: ADVISORS), check-in is theirs too (POST …/checkin), and
// the parts counter may not submit an inspection (POST …/inspection: INSPECTORS).
const TABS: { seg: string; label: string; screen?: string }[] = [
  { seg: "", label: "Repair order" },
  { seg: "checkin", label: "Check-in", screen: "service.checkin" },
  { seg: "inspect", label: "Inspect & diagnose", screen: "service.inspect" },
  { seg: "estimate", label: "Estimate & approval", screen: "service.estimate" },
];

/** Full-height operating-screen frame (sales-advisor-v2 layout, §5.20). */
export function ServiceFrame({ id, q, children, hideTabs }: {
  id: string; q: Query<any>; children: (sj: any) => ReactNode; hideTabs?: string[] }) {
  const path = usePathname() || "";
  const can = useCanOpen();
  if (!id) return <div className="page"><LoadingBlock /></div>;
  if (q.error && !q.data) return <div className="page"><ErrorState error={q.error} onRetry={q.reload} /></div>;
  if (!q.data) return <div className="page"><LoadingBlock rows={6} /></div>;
  const sj = q.data;
  const base = `/dealer/service/${encodeURIComponent(id)}`;
  const v = sj.vehicle || {};
  return (
    <MarketProvider marketId={sj.market_id}>
      <div className="flex flex-col flex-1 min-h-0">
        <div className="top-bar flex-wrap">
          <div className="session-info flex-wrap">
            <span className="live-dot" aria-hidden />
            <span><strong>{sj.repair_order?.ro_number || sj.service_journey_id}</strong></span>
            <span>{v.model_id || "Vehicle"} · <span className="mono">{v.vin}</span></span>
            {sj.customer?.name && <span>· {sj.customer.name}</span>}
            {sj.masked && <Badge tone="gray">technician view</Badge>}
            {sj.parts_hold && <Badge tone="orange">🧩 parts hold</Badge>}
            {sj.overdue_promise && <Badge tone="red">⏰ past promise</Badge>}
          </div>
          <div className="flex items-center gap-3 flex-wrap">
            <nav className="nav-tabs" aria-label="Repair order views">
              {TABS.filter((t) => !(hideTabs || []).includes(t.seg) && (!t.screen || can(t.screen))).map((t) => {
                const href = t.seg ? `${base}/${t.seg}` : base;
                const active = t.seg ? path.endsWith(`/${t.seg}`) : path.replace(/\/$/, "") === base;
                return <Link key={t.seg} href={href} className={cx("nav-tab", active && "active")} aria-current={active ? "page" : undefined}>{t.label}</Link>;
              })}
            </nav>
            <span className="timer" title="Promised time (projected in brackets)">
              ⏱ <DateTime value={sj.promised_at} mode="time" />
              {sj.projected_at && sj.projected_at !== sj.promised_at && <span className="text-xs opacity-80"> ({<DateTime value={sj.projected_at} mode="time" />})</span>}
            </span>
          </div>
        </div>
        <div className="px-4 py-2" style={{ borderBottom: "1px solid var(--border)" }}>
          <ServiceStepper current={sj.stage} blocked={!sj.handoff?.ok && sj.stage === "APPROVE"} />
          {!sj.handoff?.ok && sj.handoff?.missing?.length > 0 && (
            <div className="text-[.72rem] mt-1" style={{ color: "var(--warning)" }} role="status">
              To leave {stageLabel(sj.stage)}, still needed: {missingText(sj.handoff.missing)}
            </div>
          )}
        </div>
        <div className="flex-1 flex flex-col min-h-0">{children(sj)}</div>
        <AiLog serviceJourneyId={sj.service_journey_id} />
      </div>
    </MarketProvider>
  );
}

/** Props for the grounded A7 chat — rendered directly by <ServiceChat/> where a
 *  screen's chat is a primary workspace feature. The agent-side-panel copy of
 *  this chat now lives in the AgentBar drawer (AGENT_CHAT.A7). */
export function serviceChatProps(context?: string): Omit<Parameters<typeof ChatPanel>[0], "inline"> {
  const send = async (q: string): Promise<ChatAnswer> => {
    // `synthesize` makes A7 answer in its own voice over the retrieved
    // passages; the server falls back to the passages themselves offline.
    const r = await apiPost<any>("/knowledge/retrieve", {
      query: context ? `${q} ${context}` : q, k: 3,
      synthesize: true, persona: "a service advisor's assistant",
    });
    const passages: any[] = r.passages || [];
    if (!r.answer && !passages.length) return { answer: "I couldn't find an indexed procedure, TSB, or record for that. Try a DTC code, labor op, or item name." };
    return {
      answer: r.answer || "Here's what the knowledge base says — see the excerpts below.",
      citations: passages.map((p) => ({ label: p.source_ref, ref: p.collection })),
      references: passages.map((p) => ({ label: p.source_ref, text: String(p.passage || "").slice(0, 300) })),
      engine: r.engine || r.retrieval,
    };
  };
  return {
    title: "Service Agent",
    badge: "Grounded",
    placeholder: "Ask about a DTC, procedure, TSB, or RO…",
    suggestions: ["P0300 misfire TSB", "Front brake procedure", "HV battery inspection"],
    intro: "Ask about diagnostics, procedures, and TSBs. Every answer cites the indexed source.",
    send,
  };
}

/** A7 chat for advisors (and the dictation surface), grounded on the index. */
export function ServiceChat({ context, inline }: { context?: string; inline?: boolean }) {
  return <ChatPanel {...serviceChatProps(context)} inline={inline} />;
}

export function StageBadge({ stage }: { stage?: string }) {
  return <Badge tone={stage === "APPROVE" ? "orange" : stage === "READY" ? "green" : "blue"}>{(stage || "").replace("_", " ")}</Badge>;
}

/** Card badges — icon + text, never color alone (§3.1 accessibility). */
export function CardBadges({ b }: { b: any }) {
  if (!b) return null;
  return (
    <span className="flex flex-wrap gap-1">
      {b.waiting_approval && <Badge tone="orange"><span aria-hidden>⏳</span> waiting approval</Badge>}
      {b.parts_hold && <Badge tone="purple"><span aria-hidden>🧩</span> parts hold</Badge>}
      {b.overdue_promise && <Badge tone="red"><span aria-hidden>⏰</span> overdue promise</Badge>}
      {b.equity_flag && <Badge tone="green"><span aria-hidden>↗</span> equity</Badge>}
      {b.ev && <Badge tone="blue"><span aria-hidden>⚡</span> EV</Badge>}
    </span>
  );
}
