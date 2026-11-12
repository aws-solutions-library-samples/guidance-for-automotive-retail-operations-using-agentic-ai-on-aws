"use client";

// OEM Workspace · AI Alerts (design §4.1): HIGH (immediate) / MEDIUM (this
// week) / LOW (monitor) triage with severity classes + text labels, an
// actionable per-alert recommendation, and open/resolved filters.
// Acknowledge / resolve emit alert.acknowledged / alert.resolved.

import { useState } from "react";
import {
  AiInsight, Async, Badge, Btn, Card, DateTime, FilterPills, Kpi, KpiGrid, useToast,
} from "@/components/app";
import { useApi, useMutation } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";
import { WorkspaceView, SEVERITY_TONE, sevClass } from "../_shared/WorkspaceView";

const ACTION_BY_KIND: Record<string, string> = {
  aged_inventory: "Propose a dealer trade or targeted incentive for aged units",
  ev_days_supply: "Review EV allocation and charging-readiness support",
  lead_sla_breach: "Coach BDC on first-response SLA; review lead routing",
  conquest_pressure: "Prepare conquest response (geo-boost / matching offer request)",
  csi_decline: "Schedule a CSI action plan review with the GM",
  volume_gap: "Agree a volume recovery action plan with milestones",
};
const SECTION = { HIGH: "Immediate action", MEDIUM: "Action this week", LOW: "Monitor" } as const;
const ACTORS = ["oem_dm", "oem_regional"];

export default function OemAlerts() {
  const toast = useToast();
  const { persona } = usePersona();
  const canAct = ACTORS.includes(persona?.role || "");
  const [status, setStatus] = useState<"open" | "resolved" | "all">("open");
  const q = useApi<any>(`/oem/alerts?status=${status}`, { pollMs: 60000 });
  const patch = useMutation("PATCH");
  const act = async (a: any, action: "acknowledge" | "resolve") => {
    const out = await patch.run(`/oem/alerts/${encodeURIComponent(a.alert_id)}`, { action });
    if (out) { toast(`${a.title}: ${action === "resolve" ? "resolved" : "acknowledged"}`, "success"); q.reload(); }
    else toast(patch.error || "Update failed", "error");
  };
  const c = q.data?.counts || {};
  const kpis: Kpi[] = [
    { label: "HIGH", value: c.HIGH ?? 0, trend: (c.HIGH ?? 0) > 0 ? "down" : "up",
      delta: (c.HIGH ?? 0) > 0 ? "immediate action needed" : "none open" },
    { label: "MEDIUM", value: c.MEDIUM ?? 0, delta: "action this week" },
    { label: "LOW", value: c.LOW ?? 0, delta: "monitor" },
    { label: "Total", value: q.data?.total ?? 0, delta: `showing ${status}` },
  ];
  const kinds = Object.entries(q.data?.by_kind || {}) as [string, number][];
  return (
    <WorkspaceView view="alerts" title="AI Alerts" subtitle="District health signals ranked by severity"
      prompts={["Which HIGH alerts should I handle today?", "Which dealers have repeat alerts?"]}
      actions={<FilterPills value={status} onChange={setStatus}
                options={[{ id: "open", label: "Open" }, { id: "resolved", label: "Resolved" }, { id: "all", label: "All" }]} />}>
      <Async q={q} rows={6}>
        {(d) => (
          <>
            <KpiGrid items={kpis} />
            <AiInsight title="Alert prioritization" sources={[d.source_ref || "OEM_Alerts"]}>
              <p>{d.total === 0 ? "No alerts in this view." :
                `${c.HIGH ?? 0} HIGH alerts need action today. Most frequent category: ${kinds[0] ? `${kinds[0][0].replace(/_/g, " ")} (${kinds[0][1]})` : "—"}. `
                + (kinds[1] ? `Next: ${kinds[1][0].replace(/_/g, " ")} (${kinds[1][1]}). ` : "")
                + "Acknowledge to take ownership; resolve once the action is agreed with the dealer."}</p>
            </AiInsight>
            {(["HIGH", "MEDIUM", "LOW"] as const).map((sev) => (
              <Card key={sev} title={<span><Badge tone={SEVERITY_TONE[sev]}>{sev}</Badge> {SECTION[sev]}</span>}
                    actions={<span className="text-xs dim">{d.by_severity[sev].length}</span>}>
                {d.by_severity[sev].length === 0 ? <div className="empty">None.</div> : (
                  <div aria-live="polite">
                    {d.by_severity[sev].map((a: any) => (
                      <div key={a.alert_id} className={`alert-item ${sevClass(sev)} flex flex-wrap items-start justify-between gap-3`}>
                        <div className="min-w-0 flex-1">
                          <div className="alert-title">{a.title}</div>
                          <div className="alert-desc"><b>{a.dealer_id}</b> · {String(a.kind || "").replace(/_/g, " ")} · {ACTION_BY_KIND[a.kind] || "Review with the dealer"}</div>
                          <div className="alert-time">Opened <DateTime value={a.opened_at} mode="date" /> · {a.status}{a.resolved_at ? <> · resolved <DateTime value={a.resolved_at} mode="date" /></> : null}</div>
                        </div>
                        {canAct && a.status !== "resolved" && (
                          <div className="flex gap-2">
                            {a.status !== "acknowledged" && <Btn kind="secondary" size="sm" disabled={patch.pending} onClick={() => act(a, "acknowledge")}>Acknowledge</Btn>}
                            <Btn kind="primary" size="sm" disabled={patch.pending} onClick={() => act(a, "resolve")}>Resolve</Btn>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </Card>
            ))}
          </>
        )}
      </Async>
    </WorkspaceView>
  );
}
