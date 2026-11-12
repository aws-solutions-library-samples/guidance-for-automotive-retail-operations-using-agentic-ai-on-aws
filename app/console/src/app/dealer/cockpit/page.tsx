"use client";

// Store Cockpit (§5.14, G-03) — dealer principal / GM. DM Assistant pattern
// (KPI grid + [AI INSIGHT]) in the dark theme across nine tabs: Funnel, Sales,
// Inventory, Trade & Used, F&I, Service, CX, Alerts, OEM Comms. Tiles deep-link
// into the operating screens. Read models are materialized server-side and
// refreshed by domain events; the page renders them, never computes them.

import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import {
  Async, Badge, Btn, Card, DataTable, DateTime, MarketProvider, Money, Page, Pct, Tabs, ViewHeader,
  useToast, stageLabel } from "@/components/app";
import { ApiInsight, ApiKpiGrid, titleCase, useFollow } from "@/app/dealer/kpis";
import { useApi, useMutation, useQueryParam } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";

const ALL_TABS = ["funnel", "sales", "inventory", "trade", "fi", "service", "cx", "alerts", "comms"] as const;
type Tab = (typeof ALL_TABS)[number];
const LABEL: Record<Tab, string> = { funnel: "Funnel", sales: "Sales", inventory: "Inventory", trade: "Trade & Used",
  fi: "F&I", service: "Service", cx: "CX", alerts: "Alerts", comms: "OEM Comms" };
// tabs per role (server enforces the same table)
const ROLE_TABS: Record<string, Tab[]> = {
  dealer_manager: [...ALL_TABS], dealer_inventory: ["inventory", "trade"], dealer_service_manager: ["service"],
};

export default function CockpitPage() {
  const { persona } = usePersona();
  const dealerParam = useQueryParam("dealer");
  const tabParam = useQueryParam("tab") as Tab | null;
  const tabs: Tab[] = persona?.isOem ? [...ALL_TABS] : ROLE_TABS[persona?.role || ""] || [];
  const [tab, setTab] = useState<Tab>("funnel");
  useEffect(() => {
    if (tabParam && tabs.includes(tabParam)) setTab(tabParam);
    else if (tabs.length && !tabs.includes(tab)) setTab(tabs[0]);
  }, [tabParam, tabs.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps
  const dealer = dealerParam || (persona?.dealerIds.length === 1 ? persona.dealerIds[0] : null);
  const qs = dealerParam ? `?dealer=${encodeURIComponent(dealerParam)}` : "";
  // the initial "funnel" tab is not every role's: fetch only a tab this role may open
  const active: Tab | null = tabs.includes(tab) ? tab : tabs[0] ?? null;
  const q = useApi<any>(active ? `/dealer/cockpit/${active}${qs}` : null, { pollMs: 120000 });

  if (!tabs.length) return <Page><div className="error-box">Your role cannot open the Store Cockpit.</div></Page>;

  return (
    <MarketProvider marketId={q.data?.market_id || persona?.marketId}>
      <Page>
        <ViewHeader title="Store Cockpit"
                    subtitle={<>{dealer ? <>Store <b>{dealer}</b></> : "All stores in scope"}{q.data?.materialized_at && <> · materialized <DateTime value={q.data.materialized_at} mode="time" /></>}</>}
                    actions={<Btn kind="secondary" size="sm" onClick={() => q.reload()}>Refresh</Btn>} />
        <Tabs value={active as Tab} onChange={setTab} tabs={tabs.map((t) => ({ id: t, label: LABEL[t] }))} />
        <div aria-live="polite">
          <Async q={q} rows={6}>
            {(d: any) => (
              <>
                <ApiKpiGrid kpis={d.kpis} />
                <ApiInsight insight={d.insight} title={`${LABEL[active as Tab]} — what to do next`} />
                <TabBody tab={active as Tab} d={d} reload={q.reload} canAck={persona?.role === "dealer_manager"} />
              </>
            )}
          </Async>
        </div>
      </Page>
    </MarketProvider>
  );
}

const axis = { stroke: "var(--text-dim)", fontSize: 11 };
function Bars({ data, x, y, height = 220 }: { data: any[]; x: string; y: string; height?: number }) {
  return (
    <div style={{ width: "100%", height }} role="img" aria-label={`${y} by ${x}`}>
      <ResponsiveContainer>
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
          <CartesianGrid stroke="rgba(148,163,184,.12)" vertical={false} />
          <XAxis dataKey={x} tick={axis} tickLine={false} axisLine={false} />
          <YAxis tick={axis} tickLine={false} axisLine={false} allowDecimals={false} />
          <Tooltip contentStyle={{ background: "#0f1529", border: "1px solid rgba(255,255,255,.1)", borderRadius: 8, fontSize: 12 }} />
          <Bar dataKey={y} fill="var(--primary-light)" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function TabBody({ tab, d, reload, canAck }: { tab: Tab; d: any; reload: () => void; canAck: boolean }) {
  const follow = useFollow();
  const toast = useToast();
  const ack = useMutation();
  switch (tab) {
    case "funnel":
      return (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="Journeys by stage"><Bars data={d.by_stage} x="stage" y="count" /></Card>
          <Card title="Conversion by source">
            <DataTable<any> rows={d.by_source} rowKey={(r) => r.source} dense columns={[
              { key: "s", header: "Source", render: (r) => titleCase(r.source) },
              { key: "j", header: "Journeys", num: true, sort: (r) => r.journeys, render: (r) => r.journeys },
              { key: "a", header: "Reached agree", num: true, render: (r) => r.reached_agree },
              { key: "c", header: "Delivered", num: true, sort: (r) => r.conversion, render: (r) => <Pct value={r.conversion} /> },
            ]} />
          </Card>
          <Card title="Most stalled" className="lg:col-span-2">
            <DataTable<any> rows={d.stalled} rowKey={(r) => r.journey_id} onRowClick={(r) => follow(r.link)} empty="Nothing stalled." dense columns={[
              { key: "j", header: "Journey", render: (r) => <span className="mono">{r.journey_id}</span> },
              { key: "s", header: "Stage", render: (r) => <Badge tone="blue">{stageLabel(r.stage)}</Badge> },
              { key: "i", header: "Idle days", num: true, render: (r) => r.idle_days ?? "—" },
              { key: "o", header: "Owner", render: (r) => r.owner_name || r.owner_user_id || "—" },
            ]} />
          </Card>
        </div>
      );
    case "sales":
      return (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="Units booked by month"><Bars data={d.by_month} x="month" y="units" /></Card>
          <Card title="Advisor leaderboard">
            <DataTable<any> rows={d.leaderboard} rowKey={(r) => r.user_id} dense columns={[
              { key: "n", header: "Advisor", render: (r) => r.name },
              { key: "b", header: "Booked", num: true, sort: (r) => r.booked, render: (r) => r.booked },
              { key: "a", header: "Active", num: true, sort: (r) => r.active, render: (r) => r.active },
              { key: "g", header: "Revenue", num: true, sort: (r) => r.gross, render: (r) => <Money value={r.gross} /> },
            ]} />
          </Card>
        </div>
      );
    case "inventory":
      return (
        <div className="grid gap-4 lg:grid-cols-3">
          <Card title="Aging"><Bars data={d.aging} x="bucket" y="units" height={200} /></Card>
          <Card title="Days supply & demand by model" className="lg:col-span-2">
            <DataTable<any> rows={d.by_model} rowKey={(r) => r.model_id} onRowClick={(r) => follow(r.link)} dense columns={[
              { key: "m", header: "Model", render: (r) => r.model_id },
              { key: "s", header: "In stock", num: true, sort: (r) => r.in_stock, render: (r) => r.in_stock },
              { key: "d", header: "Days supply", num: true, sort: (r) => r.days_supply, render: (r) => r.days_supply ?? "—" },
              { key: "a", header: "Aged 60+", num: true, render: (r) => r.aged_60 },
              { key: "dm", header: "Active demand", num: true, sort: (r) => r.demand, render: (r) => r.demand },
              { key: "r", header: "Demand / stock", num: true, sort: (r) => r.demand_vs_stock,
                render: (r) => <span className={(r.demand_vs_stock || 0) > 1 ? "text-success" : ""}>{r.demand_vs_stock ?? "—"}</span> },
            ]} />
          </Card>
        </div>
      );
    case "trade":
      return (
        <Card title="Trade dispositions">
          <DataTable<any> rows={d.dispositions} rowKey={(r) => r.decision} dense empty="No trades." columns={[
            { key: "d", header: "Decision", render: (r) => titleCase(String(r.decision).replace("pending:", "pending · Trade-In Agent: ")) },
            { key: "c", header: "Trades", num: true, render: (r) => r.count },
          ]} />
          <Btn kind="alt" size="sm" className="mt-3" onClick={() => follow("/dealer/inventory?tab=trades")}>Open the trade desk</Btn>
        </Card>
      );
    case "fi":
      return (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="F&I product mix (booked deals)">
            {d.product_mix?.length ? <Bars data={d.product_mix} x="code" y="count" /> : <div className="dim">No products attached yet.</div>}
          </Card>
          <Card title="Attach by product">
            <DataTable<any> rows={d.product_mix || []} rowKey={(r) => r.code} dense empty="No products attached yet."
              initialSort={{ key: "c", dir: "desc" }} columns={[
                { key: "p", header: "Product", sort: (r) => r.code, render: (r) => r.code },
                { key: "c", header: "Attached", num: true, sort: (r) => r.count, render: (r) => r.count },
              ]} />
            <Btn kind="alt" size="sm" className="mt-3" onClick={() => follow("/dealer/fni")}>Open the F&amp;I Desk</Btn>
          </Card>
        </div>
      );
    case "service":
      return (
        <Card title="Retention">
          <div className="grid grid-cols-3 gap-3 text-center">
            <div className="mini-stat"><div className="mini-stat-value">{d.retention?.owned_vehicles ?? "—"}</div><div className="mini-stat-label">Owned vehicles</div></div>
            <div className="mini-stat"><div className="mini-stat-value">{d.retention?.serviced ?? "—"}</div><div className="mini-stat-label">Serviced here</div></div>
            <div className="mini-stat"><div className="mini-stat-value"><Pct value={d.retention?.rate} /></div><div className="mini-stat-label">Service retention</div></div>
          </div>
          <Btn kind="alt" size="sm" className="mt-3" onClick={() => follow("/dealer/service/cockpit")}>Open the Service Cockpit</Btn>
        </Card>
      );
    case "cx":
      return (
        <Card title="Messaging">
          <div className="grid grid-cols-2 gap-3">
            <div className="mini-stat"><div className="mini-stat-value">{d.messages?.outbound}</div><div className="mini-stat-label">Outbound nurture</div></div>
            <div className="mini-stat"><div className="mini-stat-value">{d.messages?.inbound}</div><div className="mini-stat-label">Customer replies</div></div>
          </div>
        </Card>
      );
    case "alerts":
      return (
        <Card title="Alerts from your District Manager">
          {d.alerts.length === 0 && <div className="dim">No open alerts.</div>}
          {d.alerts.map((a: any) => (
            <div key={a.alert_id} className={`alert-item severity-${String(a.severity || "low").toLowerCase()}`}>
              <div className="flex justify-between gap-3 flex-wrap">
                <div>
                  <div className="alert-title">{a.title}</div>
                  <div className="alert-desc">{titleCase(a.kind)} · <Badge tone={a.severity === "high" ? "red" : a.severity === "medium" ? "orange" : "blue"}>{a.severity}</Badge> · {a.status}{a.acknowledged_by && ` by ${a.acknowledged_by}`}</div>
                  <div className="alert-time">opened <DateTime value={a.opened_at} mode="date" /></div>
                </div>
                {canAck && a.status !== "acknowledged" && (
                  <Btn kind="present" disabled={ack.pending} onClick={async () => {
                    const r = await ack.run(`/dealer/cockpit/alerts/${encodeURIComponent(a.alert_id)}/ack`, {});
                    if (r) { toast("Alert acknowledged — your DM sees it", "success"); reload(); } else toast(ack.error || "Failed", "error");
                  }}>Acknowledge</Btn>
                )}
              </div>
            </div>
          ))}
        </Card>
      );
    case "comms":
      return (
        <Card title="OEM communications digest">
          {d.comms.map((m: any) => (
            <div key={m.comm_id} className="alert-item" style={{ borderLeft: `4px solid ${m.guardrail_rule ? "var(--danger)" : "var(--primary)"}` }}>
              <div className="alert-title">{m.subject} {m.guardrail_rule && <Badge tone="red" title="Withheld by an OEM guardrail">🛡 {m.guardrail_rule}</Badge>}</div>
              <div className="alert-desc">{m.guardrail_rule ? "Content withheld: this bulletin crosses an OEM guardrail and was not forwarded." : m.body}</div>
              <div className="alert-time">{titleCase(m.channel)} · {titleCase(m.audience)} · <DateTime value={m.sent_at} mode="date" /></div>
            </div>
          ))}
        </Card>
      );
  }
}
