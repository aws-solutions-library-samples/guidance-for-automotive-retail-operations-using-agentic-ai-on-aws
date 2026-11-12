"use client";

// OEM Workspace · Report Library (design §4.4): the workspace's reports ranked
// by relevance to this week's district state, with an [AI INSIGHT] reading the
// priorities. Ranking is deterministic over the Today payload.

import Link from "next/link";
import {
  AiInsight, Async, Badge, Card, Kpi, KpiGrid,
} from "@/components/app";
import { useApi } from "@/lib/hooks";
import { WorkspaceView } from "../_shared/WorkspaceView";

interface Report {
  id: string; name: string; desc: string; href: string;
  score: (d: any) => number; reason: (d: any) => string;
}

const REPORTS: Report[] = [
  { id: "alerts", name: "AI Alerts triage", desc: "HIGH / MEDIUM / LOW alerts with required actions", href: "/oem/alerts",
    score: (d) => 10 * (d?.alerts?.counts?.high ?? 0) + 3 * (d?.alerts?.counts?.medium ?? 0),
    reason: (d) => `${d?.alerts?.counts?.high ?? 0} HIGH alerts open` },
  { id: "performance", name: "Dealer Performance", desc: "Objective %, supply, CSI and alerts by dealer", href: "/oem/performance",
    score: (d) => 8 * (d?.kpis?.dealers_at_risk ?? 0),
    reason: (d) => `${d?.kpis?.dealers_at_risk ?? 0} dealer(s) at risk` },
  { id: "composite", name: "Composite Analysis", desc: "Financial composite vs district peers", href: "/oem/composite",
    score: (d) => (d?.kpis?.avg_sales_health != null && d.kpis.avg_sales_health < 0.7 ? 20 : 4),
    reason: (d) => d?.kpis?.avg_sales_health != null ? `district average at ${(d.kpis.avg_sales_health * 100).toFixed(0)}%` : "no composite data" },
  { id: "weekend", name: "Weekend Tracker", desc: "Projected weekend units by dealer (estimate)", href: "/oem/weekend",
    score: () => [5, 6].includes(new Date().getDay()) ? 25 : 5,
    reason: () => [5, 6].includes(new Date().getDay()) ? "it is the weekend push" : "plan ahead for Friday" },
  { id: "region", name: "Region View", desc: "District comparison and HIGH-alert roll-up", href: "/oem/region",
    score: () => 6, reason: () => "weekly region call prep" },
  { id: "campaigns", name: "Campaign ROI", desc: "ROI by campaign / platform / dealer, funnel drop-off", href: "/oem/campaigns",
    score: () => 5, reason: () => "budget-shift recommendations pending review" },
  { id: "contact", name: "Contact Reports", desc: "Visit coverage and sentiment this week", href: "/oem/contact-reports",
    score: (d) => ((d?.activity?.contact_reports ?? 0) === 0 ? 15 : 3),
    reason: (d) => `${d?.activity?.contact_reports ?? 0} filed this week` },
  { id: "plans", name: "Action Plans", desc: "On-track / at-risk plans and drafting", href: "/oem/action-plans",
    score: (d) => 2 * (d?.activity?.action_plans ?? 0),
    reason: (d) => `${d?.activity?.action_plans ?? 0} active plan(s)` },
  { id: "ev", name: "EV Readiness", desc: "Chargers, certified techs, EV inventory mix", href: "/oem/ev-readiness",
    score: () => 3, reason: () => "pre-allocation review" },
  { id: "compliance", name: "Compliance", desc: "Dealer standards checklist and status", href: "/oem/compliance",
    score: () => 2, reason: () => "franchise review prep" },
];

export default function OemReports() {
  const today = useApi<any>("/oem/today");
  return (
    <WorkspaceView view="reports" title="Report Library"
      subtitle="Everything in the workspace, ranked by what deserves your time this week"
      prompts={["Which report should I read first?", "What changed since last week?"]}>
      <Async q={today} rows={6}>
        {(d) => {
          const ranked = [...REPORTS].map((r) => ({ ...r, s: r.score(d) })).sort((a, b) => b.s - a.s);
          const top = ranked[0];
          const kpis: Kpi[] = [
            { label: "Reports", value: REPORTS.length, delta: "in the library" },
            { label: "Top priority", value: top.name, delta: top.reason(d) },
            { label: "HIGH alerts", value: d?.alerts?.counts?.high ?? 0,
              trend: (d?.alerts?.counts?.high ?? 0) > 0 ? "down" : "up",
              delta: (d?.alerts?.counts?.high ?? 0) > 0 ? "driving today's ranking" : "none open" },
            { label: "Dealers at risk", value: d?.kpis?.dealers_at_risk ?? 0,
              trend: (d?.kpis?.dealers_at_risk ?? 0) > 0 ? "down" : "up", delta: "weighted in ranking" },
          ];
          return (
            <>
              <KpiGrid items={kpis} />
              <AiInsight title="Reading priorities" sources={["OEM_Alerts", "OEM_Accounts", "OEM_ContactReports"]}>
                <p>Start with <b>{ranked[0].name}</b> ({ranked[0].reason(d)}), then <b>{ranked[1].name}</b> ({ranked[1].reason(d)}). The ranking is recomputed from this week&apos;s alerts, risk flags and activity every time you open the library.</p>
              </AiInsight>
              <Card title="Ranked by relevance">
                {ranked.map((r, i) => (
                  <div key={r.id} className="alert-item flex flex-wrap items-center justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="alert-title"><Badge tone={i === 0 ? "green" : "gray"}>#{i + 1}</Badge> <Link className="underline" href={r.href}>{r.name}</Link></div>
                      <div className="alert-desc">{r.desc}</div>
                      <div className="alert-time">Why now: {r.reason(d)}</div>
                    </div>
                    <Link className="btn btn-secondary btn-sm" href={r.href}>Open</Link>
                  </div>
                ))}
              </Card>
            </>
          );
        }}
      </Async>
    </WorkspaceView>
  );
}
