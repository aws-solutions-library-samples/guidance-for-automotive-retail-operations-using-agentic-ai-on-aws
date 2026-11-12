"use client";

// OEM Workspace · Dealer Performance (design §4): sortable dealer table
// (objective % · share vs peers · days supply · CSI · alerts) over the
// /oem/accounts read model, with drill-through to each dealer's Store Cockpit
// via the resource-URL contract (§5.18.2).

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AiInsight, Async, Badge, Card, DataTable, Kpi, KpiGrid, Num, Pct, ProgressBar,
} from "@/components/app";
import { useApi } from "@/lib/hooks";
import { resourceUrl } from "@/lib/resourceUrl";
import { WorkspaceView } from "../_shared/WorkspaceView";

export default function OemPerformance() {
  const router = useRouter();
  const q = useApi<any>("/oem/accounts", { pollMs: 120000 });
  return (
    <WorkspaceView view="performance" title="Dealer Performance"
      subtitle="Objective achievement, supply and alerts for every dealer in your scope"
      prompts={["Which dealers trend below objective a 2nd month?", "Who has the highest days supply?", "Which dealer should I visit this week?"]}>
      <Async q={q} rows={8}>
        {(d) => {
          const dealers = (d.accounts as any[]).filter((a) => a.account_type === "dealer");
          // Designed scorecard fields (§5.9): retail units, vs PY, gross/unit,
          // digital lead conversion. Columns appear when the read model carries
          // the field on any dealer; otherwise the table degrades gracefully.
          const has = (k: string) => dealers.some((a) => a[k] != null);
          const below = dealers.filter((a) => a.sales_health_index != null && a.sales_health_index < 0.7);
          const avgDs = dealers.filter((a) => a.days_supply != null);
          const avg = avgDs.length ? avgDs.reduce((s, a) => s + a.days_supply, 0) / avgDs.length : null;
          const worst = [...dealers].filter((a) => a.target_gap != null).sort((a, b) => a.target_gap - b.target_gap)[0];
          const kpis: Kpi[] = [
            { label: "Dealers", value: dealers.length, delta: `${d.at_risk} at risk` },
            { label: "Below objective", value: below.length, trend: below.length ? "down" : "up",
              delta: below.length ? "under 70% — action needed" : "all at or above 70%" },
            { label: "Avg days supply", value: avg != null ? <Num value={avg} /> : "—",
              trend: avg != null ? (avg <= 60 ? "up" : "down") : undefined,
              delta: avg != null ? (avg <= 60 ? "within 60-day target" : "above 60-day target") : undefined },
            { label: "HIGH alerts", value: dealers.reduce((s, a) => s + (a.high_alerts || 0), 0),
              trend: dealers.some((a) => a.high_alerts) ? "down" : "up", delta: "open across dealers",
              onClick: () => router.push("/oem/alerts") },
          ];
          return (
            <>
              <KpiGrid items={kpis} />
              <AiInsight title="Performance read-out" sources={["OEM_Accounts", "OEM_Alerts"]}>
                <p>{worst
                  ? `${worst.name} is furthest below district peers (${Math.abs(worst.target_gap * 100).toFixed(0)} pts). ${below.length} dealer(s) sit under the 70% objective line — open the Store Cockpit of each to see what is driving it, then file a contact report or draft an action plan.`
                  : "No dealer performance data in scope yet."}</p>
              </AiInsight>
              <Card title="Dealers" actions={<Link href="/oem/composite" className="text-xs underline">Composite analysis</Link>}>
                <DataTable rows={dealers} rowKey={(a: any) => a.account_id}
                  filterText={(a: any) => `${a.name} ${a.dealer_id || ""} ${a.district_id || ""}`} filterPlaceholder="Filter dealers…"
                  initialSort={{ key: "obj", dir: "asc" }}
                  columns={[
                    { key: "name", header: "Dealer", sort: (a: any) => a.name,
                      render: (a: any) => <span><b>{a.name}</b> <span className="dim text-xs">{a.dealer_id}</span></span> },
                    { key: "obj", header: "Objective %", sort: (a: any) => a.sales_health_index,
                      render: (a: any) => a.sales_health_index == null ? "—" :
                        <div className="flex items-center gap-2"><ProgressBar value={a.sales_health_index * 100} /><span className="text-xs"><Pct value={a.sales_health_index} /></span></div> },
                    ...(has("retail_units_mtd") ? [{ key: "units", header: "Retail units MTD", num: true,
                      sort: (a: any) => a.retail_units_mtd, render: (a: any) => a.retail_units_mtd ?? "—" }] : []),
                    ...(has("vs_py_pct") ? [{ key: "py", header: "vs PY", num: true, sort: (a: any) => a.vs_py_pct,
                      render: (a: any) => a.vs_py_pct == null ? "—" :
                        <span style={{ color: a.vs_py_pct < 0 ? "var(--down, var(--danger))" : "var(--up, var(--success))" }}>{a.vs_py_pct > 0 ? "+" : ""}{(a.vs_py_pct * 100).toFixed(0)}%</span> }] : []),
                    ...(has("gross_per_unit") ? [{ key: "gross", header: "Gross/unit", num: true,
                      sort: (a: any) => a.gross_per_unit, render: (a: any) => a.gross_per_unit != null ? <Num value={a.gross_per_unit} /> : "—" }] : []),
                    ...(has("digital_lead_conv") ? [{ key: "dlc", header: "Digital lead conv", num: true,
                      sort: (a: any) => a.digital_lead_conv, render: (a: any) => a.digital_lead_conv != null ? <Pct value={a.digital_lead_conv} /> : "—" }] : []),
                    { key: "share", header: "Share vs peers", num: true, sort: (a: any) => a.target_gap,
                      render: (a: any) => a.target_gap == null ? "—" :
                        <span style={{ color: a.target_gap < 0 ? "var(--down, var(--danger))" : "var(--up, var(--success))" }}>{a.target_gap > 0 ? "+" : ""}{(a.target_gap * 100).toFixed(0)} pts</span> },
                    { key: "ds", header: "Days supply", num: true, sort: (a: any) => a.days_supply,
                      render: (a: any) => a.days_supply != null ? <Num value={a.days_supply} /> : "—" },
                    { key: "csi", header: "CSI", num: true, sort: (a: any) => a.service_health_index,
                      render: (a: any) => a.service_health_index != null ? <Pct value={a.service_health_index} /> : "—" },
                    { key: "alerts", header: "Alerts", num: true, sort: (a: any) => a.open_alerts,
                      render: (a: any) => a.open_alerts ? <span>{a.open_alerts}{a.high_alerts ? <Badge tone="red">{a.high_alerts} HIGH</Badge> : null}</span> : "0" },
                    { key: "cockpit", header: "", render: (a: any) => a.dealer_id
                      ? <Link className="btn btn-secondary btn-sm" href={resourceUrl("dealer", a.dealer_id)} onClick={(e) => e.stopPropagation()}>Store Cockpit</Link>
                      : null },
                  ]} />
                <div className="dim text-xs mt-2">{d.method}. CSI shown from the service-health read model.
                  {!has("retail_units_mtd") && " Retail units, vs PY, gross/unit and digital lead conversion columns appear when the RDR scorecard feed lands."}</div>
              </Card>
            </>
          );
        }}
      </Async>
    </WorkspaceView>
  );
}
