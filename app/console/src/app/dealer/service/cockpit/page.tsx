"use client";

// Service Cockpit (§5.20.7, design doc §3.4) — service manager. DM Assistant
// performance / composite / scenario patterns in dark tokens: KPI grid (lane
// load today/week · RO cycle time by wait type · MPI completion · close rate),
// sortable fixed-ops metrics table (recommended-work close, declined-work
// conversion, parts-wait ROs & hours lost, estimate approval turnaround, CSI,
// retention by vehicle age, repurchase handoffs & deliveries, technician
// efficiency), what-if sliders (repair-vs-replace threshold, labor rate —
// keyboard adjustable with the value as text) and [AI INSIGHT] with sources.
// The A7 fixed-ops automations live in the global AgentBar; this page binds the
// Threshold what-if's Run-now to the slider recompute call.
// Every tile deep-links to the filtered Service Board.

import { useRouter } from "next/navigation";
import { ReactNode, useEffect, useState } from "react";
import { AgentActionsProvider } from "@/components/AgentBar";
import {
  AiInsight, Async, Badge, Card, DataTable, KpiGrid, MarketProvider, Money, Page, Pct, ProgressBar, Slider,
  ViewHeader, stageLabel,
} from "@/components/app";
import { useApi } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";
import { insightTitle } from "../../kpis";

interface MetricRow { metric: string; value: ReactNode; sortValue: number | null; detail?: ReactNode; href?: string }

export default function ServiceCockpit() {
  const router = useRouter();
  const { persona } = usePersona();
  const [rate, setRate] = useState(0);
  const [close, setClose] = useState(0);
  // null = the store's configured repair-vs-replace threshold (the server's
  // default, echoed back as what_if.rvr_threshold_pct) until the slider moves.
  const [rvr, setRvr] = useState<number | null>(null);
  const [debounced, setDebounced] = useState<{ rate: number; close: number; rvr: number | null }>({ rate: 0, close: 0, rvr: null });
  // deep-link what-if levers (?rvr=65&rate=15&close=5) so a saved view or the demo replay opens
  // on the modelled scenario rather than the baseline; clamped to the slider ranges
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const num = (k: string, lo: number, hi: number) => { const v = Number(q.get(k)); return q.get(k) !== null && Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : null; };
    const r = num("rvr", 30, 90), ra = num("rate", -10, 20), cl = num("close", -10, 25);
    if (r !== null) setRvr(r);
    if (ra !== null) setRate(ra);
    if (cl !== null) setClose(cl);
  }, []);
  useEffect(() => { const t = setTimeout(() => setDebounced({ rate, close, rvr }), 300); return () => clearTimeout(t); }, [rate, close, rvr]);
  const q = useApi<any>(`/service/cockpit?labor_rate_delta_pct=${debounced.rate}&close_rate_delta_pts=${debounced.close}`
    + (debounced.rvr != null ? `&rvr_threshold_pct=${debounced.rvr}` : ""), { pollMs: 60000 });

  return (
    <MarketProvider marketId={persona?.marketId}>
      {/* Run-now on the Threshold what-if replays the slider recompute call. */}
      <AgentActionsProvider value={{
        actions: {
          "a7.threshold_whatif": {
            label: "Recompute",
            run: async () => { setDebounced({ rate, close, rvr }); await q.reload(); },
          },
        },
      }}>
      <Page>
        <ViewHeader title="Service Cockpit" subtitle="Fixed-ops health for your store · tiles open the Service Board filtered" />
        <Async q={q} rows={8}>
          {(c) => {
            const t = c.tiles;
            const rvrShown = rvr ?? c.what_if.rvr_threshold_pct ?? 40;
            const avgCycle = (() => {
              const vals = Object.values(t.cycle_time_hours_by_wait_type || {}) as number[];
              return vals.length ? Math.round((vals.reduce((a, b) => a + b, 0) / vals.length) * 10) / 10 : null;
            })();
            const metrics: MetricRow[] = [
              { metric: "Recommended-work close rate", value: <Pct value={t.recommended_work_close_rate} />, sortValue: t.recommended_work_close_rate, href: "/dealer/service?badge=waiting_approval" },
              { metric: "Declined-work follow-up conversion", value: <Pct value={t.declined_followup_conversion} />, sortValue: t.declined_followup_conversion, detail: `${t.declined_open ?? 0} open items` },
              { metric: "Parts-wait ROs & hours lost", value: `${t.parts_wait_ros ?? 0} ROs`, sortValue: t.parts_wait_ros, detail: `${t.parts_wait_hours ?? 0} hours to ETA`, href: "/dealer/service?badge=parts_hold" },
              { metric: "Estimate approval turnaround", value: t.estimate_turnaround_min != null ? `${t.estimate_turnaround_min} min` : "—", sortValue: t.estimate_turnaround_min },
              { metric: "CSI", value: t.csi?.score != null ? t.csi.score : "—", sortValue: t.csi?.score ?? null,
                detail: t.csi?.score != null ? `${t.csi.delta >= 0 ? "+" : ""}${t.csi.delta} vs prior month · ${t.csi.responses} surveys` : "no survey results on file" },
              { metric: "Retention by vehicle age", value: t.retention_by_vehicle_age != null ? <Pct value={t.retention_by_vehicle_age} /> : "—", sortValue: t.retention_by_vehicle_age ?? null, detail: t.retention_by_vehicle_age == null ? "not yet connected" : undefined },
              { metric: "Repurchase handoffs & deliveries", value: t.handoffs_to_sales ?? 0, sortValue: t.handoffs_to_sales, detail: t.handoff_deliveries != null ? `${t.handoff_deliveries} delivered` : "deliveries not yet connected" },
              { metric: "Technician efficiency (billed/clocked)", value: t.technician_efficiency != null ? <Pct value={t.technician_efficiency} /> : "—", sortValue: t.technician_efficiency ?? null, detail: t.technician_efficiency == null ? "not yet connected" : undefined },
              { metric: "RO revenue", value: <Money value={t.ro_revenue} />, sortValue: t.ro_revenue },
            ];
            return (
              <>
                <KpiGrid items={[
                  { label: "In the lane now", value: t.lane_load.now ?? t.lane_load.open, onClick: () => router.push("/dealer/service") },
                  { label: "Promised this week", value: t.lane_load.promised_this_week ?? t.lane_load.week, delta: `${t.lane_load.promised_today ?? 0} today`, onClick: () => router.push("/dealer/service?day=today") },
                  { label: "RO cycle time (avg hours)", value: avgCycle ?? "—", delta: "by wait type below" },
                  { label: "MPI completion", value: <Pct value={t.mpi_completion_rate} />, trend: (t.mpi_completion_rate ?? 1) < 0.9 ? "down" : "up", delta: "target 90%" },
                  { label: "Close rate", value: <Pct value={t.recommended_work_close_rate} />, onClick: () => router.push("/dealer/service?badge=waiting_approval") },
                ] as any} five />
                <KpiGrid items={[
                  { label: "Promise on track", value: <Pct value={t.promise_kept_rate} />, delta: `${t.promise_past ?? 0} past · ${t.promise_at_risk ?? 0} at risk`, trend: (t.promise_past || t.promise_at_risk) ? "down" : undefined, onClick: () => router.push("/dealer/service?badge=overdue_promise") },
                  { label: "Parts-wait ROs", value: t.parts_wait_ros, delta: `${t.parts_wait_hours} h to ETA`, trend: t.parts_wait_ros ? "down" : undefined, onClick: () => router.push("/dealer/service?badge=parts_hold") },
                  { label: "Estimate turnaround", value: t.estimate_turnaround_min != null ? `${t.estimate_turnaround_min} min` : "—" },
                  { label: "Handoffs to sales", value: t.handoffs_to_sales },
                ]} />
                <AiInsight title={insightTitle("Fixed-ops priorities", c.insight)} sources={c.insight.sources}><p>{c.insight.text}</p></AiInsight>
                <Card title="Fixed-ops metrics">
                  <DataTable rows={metrics} rowKey={(r) => r.metric} dense
                             onRowClick={(r) => r.href && router.push(r.href)}
                             columns={[
                               { key: "metric", header: "Metric", sort: (r) => r.metric, render: (r) => r.metric },
                               { key: "value", header: "Value", num: true, sort: (r) => r.sortValue, render: (r) => r.value },
                               { key: "detail", header: "Detail", render: (r) => r.detail ?? "—" },
                             ]} />
                </Card>
                <div className="grid gap-4 lg:grid-cols-2">
                  <Card title="Lane load by stage">
                    <DataTable rows={Object.entries(t.lane_load.by_stage).map(([stage, n]) => ({ stage, n: n as number }))}
                               rowKey={(r: any) => r.stage} onRowClick={(r: any) => router.push(`/dealer/service?stage=${r.stage}`)}
                               columns={[
                                 { key: "stage", header: "Stage", render: (r: any) => stageLabel(r.stage) },
                                 { key: "n", header: "ROs", num: true, sort: (r: any) => r.n, render: (r: any) => r.n },
                                 { key: "bar", header: "", render: (r: any) => <ProgressBar value={r.n} max={Math.max(1, t.lane_load.open)} tone="blue" /> },
                               ]} />
                  </Card>
                  <Card title="RO cycle time by wait type (hours)">
                    {Object.keys(t.cycle_time_hours_by_wait_type).length ? (
                      <DataTable rows={Object.entries(t.cycle_time_hours_by_wait_type).map(([w, h]) => ({ w, h: h as number }))}
                                 rowKey={(r: any) => r.w} initialSort={{ key: "h", dir: "desc" }}
                                 onRowClick={(r: any) => router.push(`/dealer/service?wait_type=${r.w}`)}
                                 columns={[
                                   { key: "w", header: "Wait type", render: (r: any) => r.w.replace("_", " ") },
                                   { key: "h", header: "Avg hours", num: true, sort: (r: any) => r.h, render: (r: any) => r.h },
                                   { key: "bar", header: "", render: (r: any) => <ProgressBar value={r.h} max={48} tone={r.h > 24 ? "red" : r.h > 8 ? "orange" : "green"} /> },
                                 ]} />
                    ) : <div className="dim text-sm">No completed cycles.</div>}
                  </Card>
                </div>
                <Card title={<>What-if <Badge tone="orange">estimate</Badge></>}>
                  <div className="grid gap-6 md:grid-cols-2">
                    <div>
                      <Slider label="Repair-vs-replace threshold" value={rvrShown} min={30} max={90} step={5} onChange={setRvr} format={(v) => `${v}% of value`} />
                      <Slider label="Labor rate change" value={rate} min={-10} max={20} onChange={setRate} format={(v) => `${v > 0 ? "+" : ""}${v}%`} />
                      <Slider label="Recommended-work close rate" value={close} min={-10} max={25} onChange={setClose} format={(v) => `${v > 0 ? "+" : ""}${v} pts`} />
                    </div>
                    <div className="stat-grid">
                      <div className="mini-stat"><div className="mini-stat-value"><Money value={c.what_if.labor_revenue} /></div><div className="mini-stat-label">Labor revenue today</div></div>
                      <div className="mini-stat"><div className="mini-stat-value"><Money value={c.what_if.projected_labor_revenue} /></div><div className="mini-stat-label">Projected labor revenue</div></div>
                      <div className="mini-stat"><div className="mini-stat-value"><Money value={c.what_if.pending_estimate_value} /></div><div className="mini-stat-label">Pending estimates</div></div>
                      <div className="mini-stat"><div className="mini-stat-value">{c.what_if.rvr_triggered_count != null ? c.what_if.rvr_triggered_count : <Money value={c.what_if.projected_recovered_from_pending} />}</div>
                        <div className="mini-stat-label">{c.what_if.rvr_triggered_count != null ? `Estimates over ${c.what_if.rvr_threshold_pct ?? rvrShown}% of value → sales handoff` : "Recovered at new close rate"}</div></div>
                    </div>
                  </div>
                  <div className="dim text-xs mt-2">{c.what_if.formula} · projections are estimates, not commitments</div>
                </Card>
                <div className="dim text-xs">{c.note}</div>
              </>
            );
          }}
        </Async>
      </Page>
      </AgentActionsProvider>
    </MarketProvider>
  );
}
