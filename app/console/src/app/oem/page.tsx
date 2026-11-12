"use client";

// OEM Workspace · Today (design §4.1): District Pulse KPI grid (MTD retail,
// objective %, days supply, dealers at risk — deltas carry text, not just
// arrows), This Week at a Glance, Quick Actions, [AI INSIGHT] district
// priorities, and the persistent right A6 chat with per-view prompts.

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AiInsight, Async, Badge, Btn, Card, DataTable, DateTime, Kpi, KpiGrid, Num, Pct, ProgressBar,
} from "@/components/app";
import { useApi } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";
import { WorkspaceView, SEVERITY_TONE, sevClass } from "./_shared/WorkspaceView";

const PROMPTS = [
  "Which dealers trend below objective a 2nd month?",
  "Which alerts need my attention first?",
  "Who has the highest days supply?",
];

export default function OemToday() {
  const router = useRouter();
  const { persona } = usePersona();
  const today = useApi<any>("/oem/today", { pollMs: 120000 });
  const district = persona?.scopeNodeId && persona.scopeNodeId.startsWith("DIST") ? persona.scopeNodeId : "DIST-07";
  const weekend = useApi<any>(`/oem/weekend?district=${district}`);
  const d = today.data;
  const k = d?.kpis || {};
  // District Pulse: MTD retail is estimated from the weekend projection model
  // until the retail read model lands; the other three come from /oem/today.
  const mtdRetail = weekend.data
    ? Math.round((weekend.data.projections || []).reduce((s: number, p: any) => s + (p.projected_weekend_units || 0), 0) * 4)
    : null;
  const kpis: Kpi[] = [
    { label: "MTD Retail", value: mtdRetail != null ? <Num value={mtdRetail} /> : "—",
      trend: mtdRetail != null ? "up" : undefined,
      delta: mtdRetail != null ? "vs PY — modeled estimate" : "awaiting retail feed" },
    { label: "Objective %", value: k.avg_sales_health != null ? <Pct value={k.avg_sales_health} /> : "—",
      trend: k.avg_sales_health != null ? (k.avg_sales_health >= 0.7 ? "up" : "down") : undefined,
      delta: k.avg_sales_health != null ? (k.avg_sales_health >= 0.7 ? "at or above 70% target vs PY" : "below 70% target — action needed") : undefined },
    { label: "Days Supply", value: k.avg_days_supply != null ? <Num value={k.avg_days_supply} /> : "—",
      trend: k.avg_days_supply != null ? (k.avg_days_supply <= 60 ? "up" : "down") : undefined,
      delta: k.avg_days_supply != null ? (k.avg_days_supply <= 60 ? "within 60-day target" : "above 60-day target") : undefined },
    { label: "Dealers at Risk", value: k.dealers_at_risk ?? 0, trend: (k.dealers_at_risk ?? 0) > 0 ? "down" : "up",
      delta: (k.dealers_at_risk ?? 0) > 0 ? `${d?.alerts?.counts?.high ?? 0} HIGH alerts — action needed` : "none at risk",
      onClick: () => router.push("/oem/accounts") },
  ];
  const greeting = (() => { const h = new Date().getHours(); return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening"; })();
  return (
    <WorkspaceView view="today" prompts={PROMPTS}
      title={`${greeting}${persona?.name ? `, ${persona.name.split(" ")[0]}` : ""}`}
      subtitle={<><DateTime value={new Date().toISOString()} mode="date" /> · {d?.scope_node || persona?.scopeNodeId || "—"} · {k.dealers ?? "—"} dealers</>}
      actions={<Btn kind="secondary" size="sm" onClick={() => today.reload()}>Refresh</Btn>}>
      <Async q={today} rows={6}>
        {(d) => (
          <>
            <KpiGrid items={kpis} />
            <AiInsight title="District priorities" sources={["OEM_Accounts", "OEM_Alerts", "OEM_ContactReports", d.insight_by === "bedrock" ? "Bedrock (grounded)" : "templated"]}>
              <p>{d.ai_insight}</p>
            </AiInsight>
            <div className="grid gap-4 xl:grid-cols-2">
              <Card title="Quick actions">
                <div className="flex flex-wrap gap-2">
                  <Link className="btn btn-primary" href="/oem/contact-reports">New contact report</Link>
                  <Link className="btn btn-secondary" href="/oem/action-plans">New action plan</Link>
                  <Link className="btn btn-secondary" href="/oem/meeting">Meeting in a box</Link>
                  <Link className="btn btn-secondary" href="/oem/scenario">Run scenario</Link>
                </div>
              </Card>
              <Card title="This week at a glance">
                <div className="info-row"><span className="info-label">Contact reports filed</span><span className="info-value">{d.activity?.contact_reports ?? 0}</span></div>
                <div className="info-row"><span className="info-label">Active action plans</span><span className="info-value">{d.activity?.action_plans ?? 0}</span></div>
                <div className="info-row"><span className="info-label">Open alerts</span><span className="info-value">{d.alerts?.open_total ?? 0}</span></div>
                <Async q={weekend} rows={1}>
                  {(w) => (
                    <div className="info-row"><span className="info-label">Projected weekend units <Badge tone="orange">estimate</Badge></span>
                      <span className="info-value">{(w.projections || []).reduce((s: number, p: any) => s + (p.projected_weekend_units || 0), 0).toFixed(1)}</span></div>
                  )}
                </Async>
              </Card>
            </div>
            <Card title="Your week, prepared by the Field Sales Agent"
                  actions={<Badge tone="gray">drafts for review — the Field Sales Agent never publishes</Badge>}>
              <p className="text-sm">The weekly district cycle (Mondays 05:00) triages alerts, drafts plans for dealers below threshold and preps your dealer visits. Review and publish what you agree with:</p>
              <div className="info-row"><span className="info-label">Alerts triaged</span>
                <span className="info-value">{d.alerts?.open_total ?? 0} open ({d.alerts?.counts?.high ?? 0} HIGH) · <Link className="underline" href="/oem/alerts">triage</Link></span></div>
              <div className="info-row"><span className="info-label">Action plans drafted</span>
                <span className="info-value">{d.activity?.action_plans ?? 0} active · <Link className="underline" href="/oem/action-plans">review and publish</Link></span></div>
              <div className="info-row"><span className="info-label">Meeting packages</span>
                <span className="info-value"><Link className="underline" href="/oem/meeting">build for this week&apos;s visits</Link></span></div>
              <div className="info-row"><span className="info-label">Milestones due</span>
                <span className="info-value"><Link className="underline" href="/oem/action-plans">chase on each plan</Link></span></div>
            </Card>
            <Card title="Dealers at risk" actions={<Link href="/oem/performance" className="text-xs underline">Dealer performance</Link>}>
              <DataTable rows={d.dealers_at_risk || []} rowKey={(r: any) => r.dealer_id || r.name}
                empty="No dealers at risk in your scope."
                initialSort={{ key: "health", dir: "asc" }}
                columns={[
                  { key: "name", header: "Dealer", sort: (r: any) => r.name, render: (r: any) => <b>{r.name}</b> },
                  { key: "gap", header: "Vs peers", sort: (r: any) => r.target_gap, render: (r: any) => r.target_gap == null ? "—" : <span style={{ color: r.target_gap < 0 ? "var(--down, var(--danger))" : undefined }}>{r.target_gap > 0 ? "+" : ""}{(r.target_gap * 100).toFixed(0)} pts</span> },
                  { key: "cond", header: "Watch", render: (r: any) => r.condition && !["normal", "top_performer"].includes(r.condition) ? <Badge tone="orange">{String(r.condition).replace(/_/g, " ")}</Badge> : "—" },
                  { key: "health", header: "Sales health", sort: (r: any) => r.sales_health_index,
                    render: (r: any) => <div className="flex items-center gap-2"><ProgressBar value={(r.sales_health_index || 0) * 100} /><span className="text-xs"><Pct value={r.sales_health_index} /></span></div> },
                ]} />
            </Card>
            <Card title="Top alerts" actions={<Link href="/oem/alerts" className="text-xs underline">All alerts</Link>}>
              {(["high", "medium"] as const).flatMap((s) => (d.alerts?.[s] || []).slice(0, 3)).map((a: any) => (
                <div key={a.alert_id} className={`alert-item ${sevClass(a.severity)}`}>
                  <div className="alert-title"><Badge tone={SEVERITY_TONE[String(a.severity).toUpperCase()] || "blue"}>{String(a.severity).toUpperCase()}</Badge> {a.title}</div>
                  <div className="alert-desc">{a.dealer_id} · {String(a.kind || "").replace(/_/g, " ")}</div>
                </div>
              ))}
              {(d.alerts?.open_total ?? 0) === 0 && <div className="empty">No open alerts.</div>}
            </Card>
          </>
        )}
      </Async>
    </WorkspaceView>
  );
}
