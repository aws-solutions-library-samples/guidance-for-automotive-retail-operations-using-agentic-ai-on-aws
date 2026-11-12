"use client";

// OEM Workspace · Composite Analysis (design §4): financial composite table for
// every dealer with trend bars, and a dealer drill-down that loads the
// composite-vs-peer breakdown from /oem/accounts/{id}/composite.

import { useState } from "react";
import {
  AiInsight, Async, Card, DataTable, EmptyState, Kpi, KpiGrid, Num, Pct, ProgressBar,
} from "@/components/app";
import { useApi } from "@/lib/hooks";
import { WorkspaceView } from "../_shared/WorkspaceView";

const METRIC: Record<string, string> = {
  sales_health_index: "Sales health", service_health_index: "Service health", days_supply: "Days supply",
};

export default function OemComposite() {
  const q = useApi<any>("/oem/accounts");
  const [sel, setSel] = useState<any>(null);
  const detail = useApi<any>(sel ? `/oem/accounts/${encodeURIComponent(sel.account_id)}/composite` : null);
  return (
    <WorkspaceView view="composite" title="Composite Analysis" dealerId={sel?.dealer_id}
      subtitle="Financial composite across the district — click a dealer for the peer breakdown"
      prompts={["Which dealer composite worsened most?", "Who is strongest on service health?"]}>
      <Async q={q} rows={8}>
        {(d) => {
          const dealers = (d.accounts as any[]).filter((a) => a.account_type === "dealer");
          const withSh = dealers.filter((a) => a.sales_health_index != null);
          const avgSales = withSh.length ? withSh.reduce((s, a) => s + a.sales_health_index, 0) / withSh.length : null;
          const withSvc = dealers.filter((a) => a.service_health_index != null);
          const avgSvc = withSvc.length ? withSvc.reduce((s, a) => s + a.service_health_index, 0) / withSvc.length : null;
          const withDs = dealers.filter((a) => a.days_supply != null);
          const avgDs = withDs.length ? withDs.reduce((s, a) => s + a.days_supply, 0) / withDs.length : null;
          const spread = withSh.length
            ? Math.max(...withSh.map((a) => a.sales_health_index)) - Math.min(...withSh.map((a) => a.sales_health_index)) : null;
          const kpis: Kpi[] = [
            { label: "Dealers in composite", value: dealers.length, delta: `${d.at_risk} at risk` },
            { label: "Avg sales composite", value: avgSales != null ? <Pct value={avgSales} /> : "—",
              trend: avgSales != null ? (avgSales >= 0.7 ? "up" : "down") : undefined,
              delta: avgSales != null ? (avgSales >= 0.7 ? "at or above 70% benchmark" : "below 70% benchmark") : undefined },
            { label: "Avg service composite", value: avgSvc != null ? <Pct value={avgSvc} /> : "—",
              trend: avgSvc != null ? (avgSvc >= 0.7 ? "up" : "down") : undefined, delta: "vs 70% benchmark" },
            { label: "Avg days supply", value: avgDs != null ? <Num value={avgDs} /> : "—",
              trend: avgDs != null ? (avgDs <= 60 ? "up" : "down") : undefined, delta: "vs 60-day target" },
          ];
          const weakest = [...withSh].sort((a, b) => a.sales_health_index - b.sales_health_index)[0];
          return (
            <>
              <KpiGrid items={kpis} />
              <AiInsight title="Composite priorities" sources={["OEM_Accounts", "deterministic composite over district peers"]}>
                <p>{weakest
                  ? `${weakest.name} carries the weakest composite (${(weakest.sales_health_index * 100).toFixed(0)}% sales health). The district spread is ${spread != null ? `${(spread * 100).toFixed(0)} points` : "—"} — click a row to compare the dealer against its district peers metric by metric.`
                  : "No composite data yet for your scope."}</p>
              </AiInsight>
              <Card title="Financial composite by dealer">
                <DataTable rows={dealers} rowKey={(a: any) => a.account_id}
                  onRowClick={(a: any) => setSel(a)}
                  filterText={(a: any) => `${a.name} ${a.dealer_id || ""}`} filterPlaceholder="Filter dealers…"
                  initialSort={{ key: "sales", dir: "asc" }}
                  columns={[
                    { key: "name", header: "Dealer", sort: (a: any) => a.name,
                      render: (a: any) => <span><b>{a.name}</b> <span className="dim text-xs">{a.dealer_id}</span></span> },
                    { key: "sales", header: "Sales composite", sort: (a: any) => a.sales_health_index,
                      render: (a: any) => a.sales_health_index == null ? "—" :
                        <div className="flex items-center gap-2"><ProgressBar value={a.sales_health_index * 100} /><span className="text-xs"><Pct value={a.sales_health_index} /></span></div> },
                    { key: "svc", header: "Service composite", sort: (a: any) => a.service_health_index,
                      render: (a: any) => a.service_health_index == null ? "—" :
                        <div className="flex items-center gap-2"><ProgressBar value={a.service_health_index * 100} /><span className="text-xs"><Pct value={a.service_health_index} /></span></div> },
                    { key: "ds", header: "Days supply", sort: (a: any) => a.days_supply,
                      render: (a: any) => a.days_supply == null ? "—" :
                        <div className="flex items-center gap-2"><ProgressBar value={Math.min(100, (a.days_supply / 120) * 100)} tone={a.days_supply <= 60 ? "green" : "red"} /><span className="text-xs"><Num value={a.days_supply} /> days</span></div> },
                    { key: "gap", header: "vs peers", num: true, sort: (a: any) => a.target_gap,
                      render: (a: any) => a.target_gap == null ? "—" : <span>{a.target_gap > 0 ? "+" : ""}{(a.target_gap * 100).toFixed(0)} pts</span> },
                  ]} />
              </Card>
              {sel && (
                <Card title={`${sel.name} — composite vs district peers`}
                      actions={<button className="text-xs underline" onClick={() => setSel(null)}>Close</button>}>
                  <Async q={detail} rows={4}>
                    {(c) => !c.composite ? <EmptyState>No composite for this dealer.</EmptyState> : (
                      <>
                        {Object.entries(c.composite).map(([k, v]: [string, any]) => (
                          <div key={k} className="info-row items-center">
                            <span className="info-label w-40">{METRIC[k] || k}</span>
                            <span className="flex-1 flex items-center gap-3">
                              <ProgressBar value={k === "days_supply" ? Math.min(100, (v.dealer / 120) * 100) : v.dealer * 100}
                                           tone={(k === "days_supply" ? v.delta <= 0 : v.delta >= 0) ? "green" : "red"} />
                              <span className="text-xs whitespace-nowrap">
                                {k === "days_supply" ? <Num value={v.dealer} /> : <Pct value={v.dealer} />} vs peer {k === "days_supply" ? <Num value={v.peer_avg} /> : <Pct value={v.peer_avg} />}
                              </span>
                            </span>
                          </div>
                        ))}
                        {c.source_ref && <div className="dim text-xs mt-2">{c.source_ref}</div>}
                      </>
                    )}
                  </Async>
                </Card>
              )}
            </>
          );
        }}
      </Async>
    </WorkspaceView>
  );
}
