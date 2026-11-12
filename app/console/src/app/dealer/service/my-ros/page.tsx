"use client";

// Technician home — Service Desk › My ROs (§5.2, §5.20.3, design doc §3.2).
// Tablet-first, large touch targets: assigned RO lines filtered to me with
// inline line-status updates (in progress / complete / QC passed), plus
// unassigned ROs to pick up. The API masks customer data and pricing for this
// role (server-side scope claim — the UI just renders what it gets).

import Link from "next/link";
import { useState } from "react";
import { AgentActionsProvider } from "@/components/AgentBar";
import { Async, Badge, Btn, DateTime, EmptyState, Page, StatusChip, ViewHeader, useToast } from "@/components/app";
import { useApi, useMutation } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";
import { CardBadges, StageBadge } from "../common";

const NEXT: Record<string, { seg: string; label: string }> = {
  CHECK_IN: { seg: "inspect", label: "Start inspection" },
  INSPECT: { seg: "inspect", label: "Continue inspection" },
  DIAGNOSE: { seg: "inspect", label: "Diagnose" },
  APPROVE: { seg: "", label: "Waiting on customer" },
  REPAIR: { seg: "", label: "Work lines" },
};

export default function MyRos() {
  const toast = useToast();
  const { persona } = usePersona();
  const isTech = persona?.role === "dealer_technician";
  const q = useApi<any>(isTech ? "/service/journeys?unassigned=1" : "/service/journeys?mine=1", { pollMs: 30000 });
  const claim = useMutation();
  const cards: any[] = q.data?.cards || [];
  const mine = isTech ? cards.filter((c) => c.technician_id === persona?.userId) : cards;
  const open = isTech ? cards.filter((c) => !c.technician_id) : [];

  const pick = async (c: any) => {
    const out = await claim.run(`/service/journeys/${encodeURIComponent(c.service_journey_id)}/claim`, {});
    if (out) { toast(`Picked up ${c.service_journey_id}`, "success"); q.reload(); } else toast(claim.error || "Could not pick up", "error");
  };

  const Row = ({ c, pickable }: { c: any; pickable?: boolean }) => {
    const [showLines, setShowLines] = useState(false);
    const nxt = NEXT[c.stage];
    const href = `/dealer/service/${encodeURIComponent(c.service_journey_id)}${nxt?.seg ? `/${nxt.seg}` : ""}`;
    const workable = !pickable && (c.stage === "REPAIR" || c.stage === "READY");
    return (
      <div className="rec-card">
        <div className="rec-card-header">
          <div className="rec-card-title"><span className="mono">{c.vin}</span> <StageBadge stage={c.stage} /></div>
          <span className="dim text-xs">⏱ <DateTime value={c.promised_at} mode="time" /></span>
        </div>
        <div className="rec-card-body flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="vehicle-name">{c.vehicle_name || "Vehicle"}</div>
            <div className="vehicle-specs">{c.wait_type?.replace("_", " ")} · tag {c.service_journey_id.split("-").pop()}</div>
            <div className="mt-1"><CardBadges b={c.badges} /></div>
          </div>
          <div className="flex gap-2 flex-wrap">
            {workable && (
              <Btn kind="secondary" size="lg" onClick={() => setShowLines((s) => !s)} aria-expanded={showLines}>
                {showLines ? "Hide lines" : "My lines"}
              </Btn>
            )}
            {pickable
              ? <Btn kind="primary" size="lg" onClick={() => pick(c)} disabled={claim.pending}>Pick up RO</Btn>
              : <Link className="btn btn-primary btn-lg" href={href}>{nxt?.label || "Open RO"}</Link>}
          </div>
        </div>
        {showLines && <Lines journeyId={c.service_journey_id} />}
      </div>
    );
  };

  return (
    // Bay agents live in the global AgentBar; scope its activity + "last ran" to
    // the first RO assigned to me.
    <AgentActionsProvider value={{ serviceJourneyId: mine[0]?.service_journey_id || null }}>
    <Page>
      <ViewHeader title="My ROs" subtitle={isTech ? "Your assigned repair orders, then open work to pick up." : "Service journeys you advise."} />
      <Async q={q} rows={4}>
        {() => (
          <div className="flex flex-col gap-6">
            <section aria-label="Assigned to me">
              <h2 className="section-label mb-2">Assigned to me ({mine.length})</h2>
              {mine.length ? <div className="grid gap-3 md:grid-cols-2">{mine.map((c) => <Row key={c.service_journey_id} c={c} />)}</div>
                : <EmptyState>No ROs assigned to you right now.</EmptyState>}
            </section>
            {isTech && (
              <section aria-label="Open work">
                <h2 className="section-label mb-2">Open work to pick up ({open.length}) <Badge tone="gray">customer data hidden</Badge></h2>
                {open.length ? <div className="grid gap-3 md:grid-cols-2">{open.map((c) => <Row key={c.service_journey_id} c={c} pickable />)}</div>
                  : <EmptyState>No unassigned ROs.</EmptyState>}
              </section>
            )}
          </div>
        )}
      </Async>
    </Page>
    </AgentActionsProvider>
  );
}

/** Approved lines on this RO with large-target status updates (REPAIR flow). */
function Lines({ journeyId }: { journeyId: string }) {
  const toast = useToast();
  const q = useApi<any>(`/service/journeys/${encodeURIComponent(journeyId)}`);
  const patch = useMutation("PATCH");
  const update = async (roId: string, lineId: string, status: string) => {
    const out = await patch.run(`/service/ros/${encodeURIComponent(roId)}/lines/${encodeURIComponent(lineId)}/status`, { status });
    if (out) { toast(`Line → ${status.replace("_", " ")}`, "success"); q.reload(); }
    else toast(patch.error || "Update failed", "error");
  };
  return (
    <div className="rec-card-body" style={{ borderTop: "1px solid var(--border)" }}>
      <Async q={q} rows={2}>
        {(sj) => {
          const lines = (sj.lines || []).filter((l: any) => l.decision === "approve");
          const roId = sj.repair_order?.ro_id;
          if (!lines.length) return <EmptyState>No approved lines to work yet.</EmptyState>;
          return (
            <div className="flex flex-col gap-2">
              {lines.map((l: any) => (
                <div key={l.line_id} className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-sm"><StatusChip status={l.finding_status} /> {l.item} <Badge tone="blue">{(l.line_status || "pending").replace("_", " ")}</Badge></span>
                  <span className="flex gap-1 flex-wrap">
                    {["in_progress", "complete", "qc_passed"].map((st) => (
                      <Btn key={st} kind={l.line_status === st ? "success" : "alt"} size="lg" disabled={patch.pending || l.line_status === st || !roId}
                           onClick={() => roId && update(roId, l.line_id, st)}>{st.replace("_", " ")}</Btn>
                    ))}
                  </span>
                </div>
              ))}
            </div>
          );
        }}
      </Async>
    </div>
  );
}
