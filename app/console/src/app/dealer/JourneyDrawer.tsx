"use client";

// Journey drawer (§5.4): stepper + validate_handoff gaps + guarded advance +
// Run Play + the shared activity feed. Used by Journeys and Customer 360.

import Link from "next/link";
import { useState } from "react";
import { humanizeCode, contextLabel } from "@/lib/text";
import {
  Async, Badge, Btn, Drawer, ErrorState, Field, InfoRow, JourneyActivityFeed, SectionLabel, Stepper, stageLabel, useToast,
} from "@/components/app";
import { useApi, useMutation } from "@/lib/hooks";

const CONTEXT_LABELS: Record<string, string> = {
  shortlist: "Shortlist saved", saved_build: "Build saved", budget: "Budget captured", timeline: "Timeline captured",
  finance_offer: "Finance offer presented", valuation: "Trade valued", deal_structure: "Deal structured",
  credit_compliance: "Credit compliance cleared", delivered: "Vehicle delivered", trade_condition: "Trade condition",
};

export function JourneyDrawer({ journeyId, onClose, onChanged }: {
  journeyId: string | null; onClose: () => void; onChanged?: () => void }) {
  const toast = useToast();
  const q = useApi<any>(journeyId ? `/journeys/${encodeURIComponent(journeyId)}/state` : null);
  const advance = useMutation();
  const play = useMutation();
  const [evidence, setEvidence] = useState("");
  const [supplied, setSupplied] = useState<Record<string, boolean>>({});

  const doAdvance = async () => {
    if (!journeyId) return;
    const ctx = Object.fromEntries(Object.entries(supplied).filter(([, v]) => v));
    const out = await advance.run(`/journeys/${encodeURIComponent(journeyId)}/advance`,
      { evidence: evidence || "advisor advanced from Journeys", context_updates: Object.keys(ctx).length ? ctx : undefined });
    if (out) {
      toast(`Advanced ${stageLabel(out.from)} → ${stageLabel(out.to)}`, "success");
      setEvidence(""); setSupplied({}); q.reload(); onChanged?.();
    } else toast(advance.error || "Blocked", "error");
  };
  const doPlay = async (trigger?: string) => {
    if (!journeyId) return;
    const out = await play.run(`/journeys/${encodeURIComponent(journeyId)}/run-play`, { trigger, apply: true });
    if (out) toast(`Play “${out.play_name || out.play_id}” ran at ${out.autonomy_level}${out.written ? "" : " (nothing written)"}`, "success");
    else toast(play.error || "Play failed", "error");
  };

  return (
    <Drawer open={Boolean(journeyId)} onClose={onClose} title={`Journey ${journeyId || ""}`}
            footer={<Link className="btn btn-primary" href={`/dealer/advisor/${encodeURIComponent(journeyId || "")}`}>Open in Advisor</Link>}>
      <Async q={q}>
        {(s) => {
          const gaps: string[] = s.handoff?.missing || [];
          return (
            <>
              <Stepper current={s.stage} blocked={gaps.length > 0} />
              <div>
                <InfoRow label="Stage" value={stageLabel(s.stage)} />
                <InfoRow label="Owner" value={s.pending_handoff
                  ? <span>{s.owner_name || s.owner_user_id} <span className="dim">→</span> {s.pending_handoff.to_user_name || s.pending_handoff.to_user} <Badge tone="orange">handoff pending</Badge></span>
                  : (s.owner_name || s.owner_user_id || "Unassigned")} />
                {s.vin && <InfoRow label="VIN" value={<span className="mono">{s.vin}</span>} />}
                <InfoRow label="Source" value={s.source === "service_lane" ? <Badge tone="purple">service lane</Badge> : humanizeCode(s.source)} />
                <InfoRow label="Idle" value={s.idle_days != null ? `${Math.round(s.idle_days)} d${s.stalled ? " · stalled" : ""}` : "—"} />
                <InfoRow label="Next best action" value={s.next_best_action?.chosen?.action?.replace(/_/g, " ")} />
              </div>
              <SectionLabel>Handoff gate — leaving {stageLabel(s.stage)}</SectionLabel>
              {gaps.length === 0 ? <div className="text-sm" style={{ color: "var(--success)" }}>✓ Required context complete</div> : (
                <div className="flex flex-col gap-2" role="group" aria-label="Missing context">
                  {gaps.map((g) => g.startsWith("compliance:") ? (
                    <div key={g} className="incentive-row ineligible"><span aria-hidden>⛔</span>
                      <div className="incentive-info"><div className="incentive-name">{g.replace("compliance:", "")} not cleared</div>
                        <div className="incentive-amount">Recorded by the F&amp;I connector — cannot be set here</div></div></div>
                  ) : (
                    <label key={g} className="toggle">
                      <input type="checkbox" checked={!!supplied[g]} onChange={(e) => setSupplied({ ...supplied, [g]: e.target.checked })} />
                      {CONTEXT_LABELS[g] || contextLabel(g)} — captured now
                    </label>
                  ))}
                </div>
              )}
              {s.next_stage && (
                <>
                  <Field label="Evidence for the audit event">{(id) => (
                    <input id={id} className="input" value={evidence} onChange={(e) => setEvidence(e.target.value)}
                           placeholder="e.g. customer confirmed budget on the phone" />)}</Field>
                  <div className="flex gap-2 flex-wrap">
                    <Btn onClick={doAdvance} disabled={advance.pending}>Advance to {stageLabel(s.next_stage)}</Btn>
                    <Btn kind="secondary" onClick={() => doPlay(s.stalled ? "journey.stalled" : undefined)} disabled={play.pending}>Run play</Btn>
                  </div>
                  {advance.error && <ErrorState error={advance.error} />}
                </>
              )}
              <SectionLabel>Activity</SectionLabel>
              <JourneyActivityFeed journeyId={journeyId} />
            </>
          );
        }}
      </Async>
    </Drawer>
  );
}
