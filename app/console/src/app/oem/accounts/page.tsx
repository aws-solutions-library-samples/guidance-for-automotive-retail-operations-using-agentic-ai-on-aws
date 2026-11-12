"use client";

// OEM Workspace · Account Plans (design §4.3): account list with target-gap and
// at-risk indicators; row → account detail (plan artifacts, briefs, strategy).

import { useRouter } from "next/navigation";
import { useState } from "react";
import {
  AiInsight, Async, Badge, Card, DataTable, FilterPills, Kpi, KpiGrid, Pct, ProgressBar,
} from "@/components/app";
import { useApi, useQueryParam } from "@/lib/hooks";
import { WorkspaceView } from "../_shared/WorkspaceView";

export default function OemAccounts() {
  const router = useRouter();
  const [type, setType] = useState<"all" | "dealer" | "dealer_group" | "fleet" | "prospect">("all");
  const q = useApi<any>("/oem/accounts");
  const district = useQueryParam("district");
  return (
    <WorkspaceView view="accounts" title="Account Plans"
      subtitle={district ? <>District {district} · <a className="underline" href="/oem/accounts">clear district</a></> : "Dealer, dealer-group, fleet and prospect accounts in your scope"}
      prompts={["Which accounts are furthest below peers?", "Which dealers are at risk and why?"]}>
      <Async q={q} rows={8}>
        {(d) => {
          const rows = (d.accounts as any[]).filter((a) => (type === "all" || a.account_type === type)
            && (!district || a.district_id === district));
          const dealers = (d.accounts as any[]).filter((a) => a.account_type === "dealer");
          const worst = [...dealers].filter((a) => a.target_gap != null).sort((a, b) => a.target_gap - b.target_gap)[0];
          const kpis: Kpi[] = [
            { label: "Dealers", value: d.dealers ?? dealers.length, delta: `in scope · ${d.count} accounts incl. groups, fleet, prospects` },
            { label: "At risk", value: d.at_risk, trend: d.at_risk ? "down" : "up",
              delta: d.at_risk ? "5+ pts below peers or health < 60% — action needed" : "none at risk" },
            { label: "HIGH alerts", value: dealers.reduce((s, a) => s + a.high_alerts, 0),
              trend: dealers.some((a) => a.high_alerts) ? "down" : "up", delta: "open on dealer accounts" },
            { label: "Largest gap", value: worst ? `${(worst.target_gap * 100).toFixed(0)} pts` : "—",
              trend: worst ? "down" : undefined, delta: worst ? `${worst.name} vs peers` : "no gap data" },
          ];
          return (
            <>
              <KpiGrid items={kpis} />
              <AiInsight title="Account priorities" sources={["OEM_Accounts", "OEM_Alerts"]}>
                <p>{worst ? `${worst.name} is ${Math.abs(worst.target_gap * 100).toFixed(0)} points below its district peers on sales health`
                  + (worst.condition ? ` (${String(worst.condition).replace(/_/g, " ")})` : "")
                  + `. ${d.at_risk} of ${d.dealers ?? dealers.length} dealers are at risk — open one to generate the account plan, opportunity brief and win strategy, then publish for review.` : "No dealer accounts in scope."}</p>
              </AiInsight>
              <Card>
                <FilterPills value={type} onChange={setType} options={[
                  { id: "all", label: "All" }, { id: "dealer", label: "Dealers" }, { id: "dealer_group", label: "Groups" },
                  { id: "fleet", label: "Fleet" }, { id: "prospect", label: "Prospects" }]} />
                <DataTable rows={rows} rowKey={(a: any) => a.account_id}
                  onRowClick={(a: any) => router.push(`/oem/accounts/${encodeURIComponent(a.account_id)}`)}
                  filterText={(a: any) => `${a.name} ${a.account_id} ${a.dealer_id || ""}`} filterPlaceholder="Filter accounts…"
                  initialSort={{ key: "gap", dir: "asc" }}
                  columns={[
                    { key: "name", header: "Account", sort: (a: any) => a.name,
                      render: (a: any) => <span><b>{a.name}</b> <span className="dim text-xs">{a.dealer_id || a.account_id}</span></span> },
                    { key: "type", header: "Type", render: (a: any) => <Badge tone="gray">{String(a.account_type).replace("_", " ")}</Badge> },
                    { key: "tier", header: "Tier", sort: (a: any) => a.tier, render: (a: any) => a.tier || "—" },
                    { key: "health", header: "Sales health", sort: (a: any) => a.sales_health_index,
                      render: (a: any) => a.sales_health_index == null ? "—" :
                        <div className="flex items-center gap-2"><ProgressBar value={a.sales_health_index * 100} /><span className="text-xs"><Pct value={a.sales_health_index} /></span></div> },
                    { key: "gap", header: "Target gap", num: true, sort: (a: any) => a.target_gap,
                      render: (a: any) => a.target_gap == null ? "—" : <span style={{ color: a.target_gap < 0 ? "var(--down, var(--danger))" : "var(--up, var(--success))" }}>{a.target_gap > 0 ? "+" : ""}{(a.target_gap * 100).toFixed(0)} pts</span> },
                    { key: "ds", header: "Days supply", num: true, sort: (a: any) => a.days_supply, render: (a: any) => a.days_supply ?? "—" },
                    { key: "alerts", header: "Alerts", num: true, sort: (a: any) => a.open_alerts,
                      render: (a: any) => a.open_alerts ? <span>{a.open_alerts}{a.high_alerts ? <Badge tone="red">{a.high_alerts} HIGH</Badge> : null}</span> : "0" },
                    { key: "risk", header: "Status", render: (a: any) => <span className="flex gap-1 flex-wrap">{a.at_risk ? <Badge tone="red">At risk</Badge> : <Badge tone="green">On track</Badge>}
                        {a.watch ? <Badge tone="orange" title="Flagged condition to watch — not the risk label">watch · {a.watch}</Badge> : null}</span> },
                  ]} />
                <div className="dim text-xs mt-2">{d.method}</div>
              </Card>
            </>
          );
        }}
      </Async>
    </WorkspaceView>
  );
}
