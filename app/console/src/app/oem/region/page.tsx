"use client";

// OEM Workspace · Region View (design §4.2): labeled District ▸ Region ▸
// National scope switcher, district comparison table (ranked rows with bars +
// numeric labels), DM activity (contact reports filed, action plans on track),
// roll-up of HIGH alerts, and the region [AI INSIGHT].

import { useRouter } from "next/navigation";
import { useState } from "react";
import {
  AiInsight, Async, Badge, Btn, Card, DataTable, DateTime, EmptyState, Kpi, KpiGrid, Pct, ProgressBar, Tabs,
} from "@/components/app";
import { useApi } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";
import { WorkspaceView, sevClass } from "../_shared/WorkspaceView";

type Scope = "district" | "region" | "national";

export default function OemRegion() {
  const router = useRouter();
  const { persona } = usePersona();
  const [scope, setScope] = useState<Scope>("region");
  const q = useApi<any>("/oem/region", { pollMs: 120000 });
  const district = persona?.scopeNodeId && persona.scopeNodeId.startsWith("DIST") ? persona.scopeNodeId : null;
  return (
    <WorkspaceView view="region" title="Region View"
      subtitle={q.data ? `${q.data.scope_name} · ${q.data.districts.length} districts · scope: ${scope}` : "District roll-up"}
      prompts={["Which district needs my coaching time?", "Which HIGH alerts are open across the region?"]}
      actions={
        <div className="flex items-center gap-2">
          <span className="text-xs dim" id="scope-switch-label">Scope</span>
          <span role="group" aria-labelledby="scope-switch-label">
            <Tabs value={scope} onChange={setScope} tabs={[
              { id: "district", label: "District" }, { id: "region", label: "Region" }, { id: "national", label: "National" }]} />
          </span>
        </div>
      }>
      <Async q={q} rows={8}>
        {(d) => {
          const ds = d.districts as any[];
          const withData = ds.filter((x) => x.avg_sales_health != null);
          const weakest = [...withData].sort((a, b) => a.avg_sales_health - b.avg_sales_health)[0];
          const kpis: Kpi[] = [
            { label: "Districts", value: ds.length, delta: `${ds.reduce((s, x) => s + x.dealers, 0)} dealers` },
            { label: "Dealers at risk", value: ds.reduce((s, x) => s + x.dealers_at_risk, 0),
              trend: ds.some((x) => x.dealers_at_risk) ? "down" : "up",
              delta: ds.some((x) => x.dealers_at_risk) ? "action needed" : "none at risk" },
            { label: "HIGH alerts", value: ds.reduce((s, x) => s + x.alerts_high, 0),
              trend: ds.some((x) => x.alerts_high) ? "down" : "up", delta: "open across region" },
            { label: "Plans published", value: ds.reduce((s, x) => s + x.plans_published, 0),
              delta: `of ${ds.reduce((s, x) => s + x.action_plans, 0)} active plans` },
          ];
          return (
            <>
              <KpiGrid items={kpis} />
              <AiInsight title="Region priorities" sources={[d.source_ref]}>
                <p>{weakest ? `${weakest.name} has the lowest average sales health (${(weakest.avg_sales_health * 100).toFixed(0)}%) with ${weakest.dealers_at_risk} dealer(s) at risk and ${weakest.alerts_high} HIGH alert(s). ` : ""}
                  {ds.filter((x) => x.dealers === 0).map((x) => x.name).join(", ") ? `No dealer data yet for ${ds.filter((x) => x.dealers === 0).map((x) => x.name).join(", ")}. ` : ""}
                  Review DM activity (contact reports, published plans) before the region call.</p>
              </AiInsight>
              {scope === "district" && (
                <Card title="District scope">
                  <p className="text-sm">You are re-scoped to your district{district ? <> (<b>{district}</b>)</> : null}. Open the district views directly — every view re-scopes with you:</p>
                  <div className="flex flex-wrap gap-2 mt-2">
                    <Btn kind="primary" size="sm" onClick={() => router.push("/oem")}>Today</Btn>
                    <Btn kind="secondary" size="sm" onClick={() => router.push("/oem/performance")}>Dealer Performance</Btn>
                    <Btn kind="secondary" size="sm" onClick={() => router.push(district ? `/oem/accounts?district=${encodeURIComponent(district)}` : "/oem/accounts")}>Accounts</Btn>
                  </div>
                </Card>
              )}
              {scope === "national" && (
                <EmptyState>National roll-up is not materialized for this market yet — the region comparison covers every district in your scope. Switch back to Region.</EmptyState>
              )}
              {scope === "region" && (
                <>
                  <Card title="District comparison">
                    <DataTable rows={ds} rowKey={(x: any) => x.district_id} initialSort={{ key: "health", dir: "asc" }}
                      columns={[
                        { key: "name", header: "District", sort: (x: any) => x.name, render: (x: any) => <span><b>{x.name}</b> <span className="dim text-xs">{x.district_id}</span></span> },
                        { key: "health", header: "Objective %", sort: (x: any) => x.avg_sales_health,
                          render: (x: any) => x.avg_sales_health == null ? "—" : <div className="flex items-center gap-2"><ProgressBar value={x.avg_sales_health * 100} /><span className="text-xs"><Pct value={x.avg_sales_health} /></span></div> },
                        { key: "dealers", header: "Dealers", num: true, sort: (x: any) => x.dealers, render: (x: any) => x.dealers },
                        { key: "ds", header: "Days supply", num: true, sort: (x: any) => x.avg_days_supply, render: (x: any) => x.avg_days_supply ?? "—" },
                        { key: "risk", header: "At risk", num: true, sort: (x: any) => x.dealers_at_risk, render: (x: any) => x.dealers_at_risk },
                        { key: "alerts", header: "Alerts open", num: true, sort: (x: any) => x.alerts_open,
                          render: (x: any) => <span>{x.alerts_open}{x.alerts_high ? <Badge tone="red">{x.alerts_high} HIGH</Badge> : null}</span> },
                        { key: "dm", header: "DM activity", render: (x: any) => <span className="text-xs">{x.contact_reports} reports filed · {x.plans_published}/{x.action_plans} plans on track</span> },
                        { key: "open", header: "", render: (x: any) => <Btn kind="secondary" size="sm" onClick={() => router.push(`/oem/accounts?district=${encodeURIComponent(x.district_id)}`)}>Open district</Btn> },
                      ]} />
                  </Card>
                  <Card title="HIGH alerts across the region">
                    {d.high_alerts.length === 0 ? <EmptyState>No HIGH alerts open.</EmptyState> : d.high_alerts.map((a: any) => (
                      <div key={a.alert_id} className={`alert-item ${sevClass("HIGH")}`}>
                        <div className="alert-title"><Badge tone="red">HIGH</Badge> {a.title}</div>
                        <div className="alert-desc">{a.district_id} · {a.dealer_id} · {String(a.kind || "").replace(/_/g, " ")}</div>
                        <div className="alert-time">Opened <DateTime value={a.opened_at} mode="date" /> · {a.status}</div>
                      </div>
                    ))}
                  </Card>
                </>
              )}
            </>
          );
        }}
      </Async>
    </WorkspaceView>
  );
}
