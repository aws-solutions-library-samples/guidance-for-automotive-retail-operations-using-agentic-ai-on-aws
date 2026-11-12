"use client";

// OEM Workspace · EV Readiness (design §4.1 intelligence): dealer EV readiness
// — chargers, certified techs, inventory mix — from the per-account readiness
// read model, with EV days-supply alerts folded in.

import { humanizeCode } from "@/lib/text";
import { useState } from "react";
import {
  AiInsight, Async, Badge, Card, DataTable, EmptyState, Kpi, KpiGrid, Num,
} from "@/components/app";
import { useApi } from "@/lib/hooks";
import { WorkspaceView } from "../_shared/WorkspaceView";

export default function OemEvReadiness() {
  const accounts = useApi<any>("/oem/accounts");
  const alerts = useApi<any>("/oem/alerts?status=open");
  const [sel, setSel] = useState<any>(null);
  const detail = useApi<any>(sel ? `/oem/accounts/${encodeURIComponent(sel.account_id)}/ev-readiness` : null);
  return (
    <WorkspaceView view="ev-readiness" title="EV Readiness" dealerId={sel?.dealer_id}
      subtitle="Chargers, certified technicians and EV inventory mix by dealer"
      prompts={["Which dealers are not EV certified?", "Where is EV days supply out of line?"]}>
      <Async q={accounts} rows={8}>
        {(d) => {
          const dealers = (d.accounts as any[]).filter((a) => a.account_type === "dealer");
          const evAlerts = (["HIGH", "MEDIUM", "LOW"] as const)
            .flatMap((s) => (alerts.data?.by_severity?.[s] || []))
            .filter((a: any) => a.kind === "ev_days_supply");
          const kpis: Kpi[] = [
            { label: "Dealers", value: dealers.length, delta: "in scope" },
            { label: "EV supply alerts", value: evAlerts.length, trend: evAlerts.length ? "down" : "up",
              delta: evAlerts.length ? "review allocation — action needed" : "none open" },
            { label: "Certified", value: sel && detail.data ? (detail.data.ev_certified ? "Yes" : "No") : "—",
              delta: sel ? sel.name : "select a dealer" },
            { label: "Readiness areas", value: sel && detail.data ? (detail.data.ev_areas?.length ?? 0) : "—",
              delta: "chargers · techs · inventory mix" },
          ];
          return (
            <>
              <KpiGrid items={kpis} />
              <AiInsight title="EV readiness priorities" sources={["OEM_Accounts", "OEM_Alerts (ev_days_supply)", "EV readiness read model"]}>
                <p>{evAlerts.length
                  ? `${evAlerts.length} dealer(s) have EV days-supply alerts open — pair the allocation review with a charging-readiness check. Select a dealer below to see certification, charger install and technician status.`
                  : "No EV supply alerts open. Select a dealer to review charger installs, certified technicians and EV inventory mix before the next allocation round."}</p>
              </AiInsight>
              <Card title="Dealers">
                <DataTable rows={dealers} rowKey={(a: any) => a.account_id}
                  onRowClick={(a: any) => setSel(a)}
                  filterText={(a: any) => `${a.name} ${a.dealer_id || ""}`} filterPlaceholder="Filter dealers…"
                  columns={[
                    { key: "name", header: "Dealer", sort: (a: any) => a.name,
                      render: (a: any) => <span><b>{a.name}</b> <span className="dim text-xs">{a.dealer_id}</span></span> },
                    { key: "ds", header: "Days supply", num: true, sort: (a: any) => a.days_supply,
                      render: (a: any) => a.days_supply != null ? <Num value={a.days_supply} /> : "—" },
                    { key: "ev", header: "EV supply alert", render: (a: any) =>
                      evAlerts.some((x: any) => x.dealer_id === a.dealer_id)
                        ? <Badge tone="orange">EV days supply</Badge> : <span className="dim text-xs">none</span> },
                    { key: "open", header: "", render: () => <span className="text-xs underline">EV readiness →</span> },
                  ]} />
              </Card>
              {sel && (
                <Card title={<span>{sel.name} — EV readiness {detail.data && <Badge tone={detail.data.ev_certified ? "green" : "orange"}>{detail.data.ev_certified ? "certified" : "not certified"}</Badge>}</span>}
                      actions={<button className="text-xs underline" onClick={() => setSel(null)}>Close</button>}>
                  <Async q={detail} rows={4}>
                    {(r) => !r.ev_areas?.length ? <EmptyState>No EV readiness areas on file for this dealer.</EmptyState> : (
                      <>
                        {r.ev_areas.map((c: any) => (
                          <div key={c.area} className="info-row">
                            <span className="info-label">{String(c.area).replace(/_/g, " ")}</span>
                            <span className="info-value"><Badge tone={String(c.status).toLowerCase().includes("complete") || String(c.status).toLowerCase() === "ready" ? "green" : "orange"}>{humanizeCode(c.status)}</Badge></span>
                          </div>
                        ))}
                        {r.source_ref && <div className="dim text-xs mt-2">{r.source_ref}</div>}
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
