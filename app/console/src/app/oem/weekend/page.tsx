"use client";

// OEM Workspace · Weekend Tracker (design §4.1 field tools): weekend sales
// projection per dealer from the deterministic model over OEM_Accounts
// (labelled an estimate — no fabricated forecast API).

import {
  AiInsight, Async, Badge, Card, DataTable, Kpi, KpiGrid, Num, Pct, ProgressBar,
} from "@/components/app";
import { useApi } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";
import { WorkspaceView } from "../_shared/WorkspaceView";

export default function OemWeekend() {
  const { persona } = usePersona();
  const district = persona?.scopeNodeId && persona.scopeNodeId.startsWith("DIST") ? persona.scopeNodeId : "DIST-07";
  const q = useApi<any>(`/oem/weekend?district=${district}`, { pollMs: 300000 });
  return (
    <WorkspaceView view="weekend" title="Weekend Tracker"
      subtitle={`District ${district} · projected weekend units by dealer`}
      actions={<Badge tone="orange">estimate</Badge>}
      prompts={["Which dealer should carry the weekend push?", "Who is projected lowest this weekend?"]}>
      <Async q={q} rows={6}>
        {(d) => {
          const rows = (d.projections || []) as any[];
          const total = rows.reduce((s, p) => s + (p.projected_weekend_units || 0), 0);
          const top = rows[0];
          const low = rows[rows.length - 1];
          const max = Math.max(1, ...rows.map((p) => p.projected_weekend_units || 0));
          // Designed feed (§5.9): booked / delivered / PY / Monday appointments
          // come from DMS deliveries + CRM appointments. Render them when the
          // feed carries them; degrade to the projection-only model otherwise.
          const hasActuals = rows.some((p) => p.booked != null || p.delivered != null);
          const sum = (k: string) => rows.reduce((s, p) => s + (p[k] || 0), 0);
          const kpis: Kpi[] = hasActuals ? [
            { label: "Weekend booked", value: sum("booked"), trend: "up", delta: "DMS + CRM appointments" },
            { label: "Weekend delivered", value: sum("delivered"),
              trend: sum("delivered") >= sum("booked") * 0.8 ? "up" : "down", delta: "vs booked" },
            { label: "PY weekend", value: sum("py_weekend"), delta: "same weekend prior year" },
            { label: "Pending deliveries", value: Math.max(0, sum("booked") - sum("delivered")),
              trend: "down", delta: "booked, not yet delivered" },
          ] : [
            { label: "Projected units", value: total.toFixed(1), trend: "up", delta: "this weekend — modeled estimate" },
            { label: "Dealers tracked", value: rows.length, delta: `district ${d.district_id}` },
            { label: "Top dealer", value: top ? top.projected_weekend_units.toFixed(1) : "—", trend: top ? "up" : undefined,
              delta: top ? top.name : "no projections" },
            { label: "Lowest dealer", value: low ? low.projected_weekend_units.toFixed(1) : "—", trend: low ? "down" : undefined,
              delta: low ? `${low.name} — check readiness` : "no projections" },
          ];
          return (
            <>
              <KpiGrid items={kpis} />
              <AiInsight title="Weekend plan" sources={[d.source_ref]}>
                <p>{rows.length
                  ? `The model projects ${total.toFixed(1)} weekend units across ${rows.length} dealers, led by ${top.name}. ${low && low !== top ? `${low.name} projects lowest — confirm staffing, demo units and weekend ad support before Friday.` : ""} Projections are deterministic estimates from sales health and days supply, not commitments.`
                  : "No dealers in this district have projection inputs yet."}</p>
              </AiInsight>
              <Card title="Projected weekend units by dealer">
                <DataTable rows={rows} rowKey={(p: any) => p.account_id} empty="No weekend projections for this district."
                  initialSort={{ key: "proj", dir: "desc" }}
                  columns={[
                    { key: "name", header: "Dealer", sort: (p: any) => p.name, render: (p: any) => <b>{p.name}</b> },
                    ...(hasActuals ? [
                      { key: "booked", header: "Booked", num: true, sort: (p: any) => p.booked, render: (p: any) => p.booked ?? "—" },
                      { key: "delivered", header: "Delivered", num: true, sort: (p: any) => p.delivered, render: (p: any) => p.delivered ?? "—" },
                      { key: "py", header: "PY", num: true, sort: (p: any) => p.py_weekend, render: (p: any) => p.py_weekend ?? "—" },
                      { key: "mon", header: "Appointments set Mon", num: true, sort: (p: any) => p.appointments_monday, render: (p: any) => p.appointments_monday ?? "—" },
                    ] : []),
                    { key: "proj", header: "Projected units", sort: (p: any) => p.projected_weekend_units,
                      render: (p: any) => <div className="flex items-center gap-2"><ProgressBar value={p.projected_weekend_units} max={max} /><span className="text-xs">{p.projected_weekend_units.toFixed(1)}</span></div> },
                    { key: "health", header: "Sales health", num: true, sort: (p: any) => p.inputs?.sales_health_index,
                      render: (p: any) => p.inputs?.sales_health_index != null ? <Pct value={p.inputs.sales_health_index} /> : "—" },
                    { key: "ds", header: "Days supply", num: true, sort: (p: any) => p.inputs?.days_supply,
                      render: (p: any) => p.inputs?.days_supply != null ? <Num value={p.inputs.days_supply} /> : "—" },
                    { key: "est", header: "", render: () => <Badge tone="orange">estimate</Badge> },
                  ]} />
              </Card>
            </>
          );
        }}
      </Async>
    </WorkspaceView>
  );
}
