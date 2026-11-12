"use client";

import { contextLabel, humanizeCode } from "@/lib/text";

// Advisor LEFT — Customer 360 (§5.3): stepper + missing-context warnings,
// customer card, contact rows, recent activity, preference tags, AI insight,
// plus journey ownership and the shared activity feed (§5.18.3/§5.18.6 — the
// same feed F&I Desk and the Delivery Board render, so handoffs are visible
// on every journey screen).

import Link from "next/link";
import {
  ActivityItem, Badge, Btn, CustomerCard, Distance, InfoRow, InsightBox, JourneyActivityFeed, Money, Panel, SectionLabel,
  Stepper, Tag, useToast, stageLabel } from "@/components/app";
import { timeAgo, useMutation } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";

const PREF_LABEL: Record<string, (v: any) => string | null> = {
  powertrain_openness: (v) => v ? `${String(v).toUpperCase()}-curious` : null,
  household_size: (v) => v ? `Household of ${v}` : null,
  road_trip_frequency: (v) => v ? `Road trips ${v}` : null,
  safety_priority: (v) => v === "high" ? "Safety first" : null,
  tech_affinity: (v) => v ? `Tech ${v}` : null,
  ownership_style: (v) => v ? `Prefers ${v}` : null,
  towing_or_hauling: (v) => v ? "Tows / hauls" : null,
};

export function Left360({ a, onViewAsCustomer, onChanged }: { a: any; onViewAsCustomer: () => void; onChanged?: () => void }) {
  const toast = useToast();
  const { persona } = usePersona();
  const accept = useMutation();
  const c = a.customer_360 || {};
  const j = a.journey || {};
  const ph = j.pending_handoff || null;
  const mine = Boolean(ph && persona?.userId && ph.to_user === persona.userId);
  const doAccept = async () => {
    const out = await accept.run(`/handoffs/${encodeURIComponent(ph.handoff_id)}/accept`, {});
    if (out) { toast("Handoff accepted — you own this journey", "success"); onChanged?.(); }
    else toast(accept.error || "Could not accept", "error");
  };
  const st = a.state || {};
  const gaps: string[] = st.handoff?.missing || [];
  const lp = c.lifestyle_profile || {};
  const prefs = [
    ...Object.entries(PREF_LABEL).map(([k, f]) => f(lp[k])).filter(Boolean) as string[],
    ...((lp.outdoor_activities || []) as string[]),
  ];
  const budget = c.budget || {};
  return (
    <Panel title="Customer 360" badge="Live" badgeKind="live">
      <Stepper current={st.stage} blocked={gaps.length > 0} compact />
      {gaps.length > 0 && (
        <div className="objection-alert" role="alert">
          <div className="objection-label">Still needed to leave {stageLabel(st.stage)}</div>
          <div className="text-[.8rem]">{gaps.map((g) => g.startsWith("compliance:") ? `${humanizeCode(g.slice(11))} compliance check` : contextLabel(g)).join(", ")}</div>
        </div>
      )}
      <CustomerCard name={c.name || a.journey?.customer_id}
        meta={<>Budget <Money value={budget.price_min} />–<Money value={budget.price_max} />{budget.monthly_max ? <> · ≤<Money value={budget.monthly_max} />/mo</> : null}</>}
        score={a.lifecycle?.stalled ? "Stalled" : `${stageLabel(st.stage || "")} · ${Math.round(a.lifecycle?.days_in_stage ?? 0)}d`} />
      <div>
        <SectionLabel>Contact</SectionLabel>
        <InfoRow label="Email" value={c.contacts?.email} />
        <InfoRow label="Phone" value={c.contacts?.phone} />
        <InfoRow label="Location" value={[c.location?.city, c.location?.state || c.location?.bundesland].filter(Boolean).join(", ")} />
        <InfoRow label="Daily drive" value={c.commute_per_day ? <>~<Distance value={c.commute_per_day} />/day</> : "—"} />
        <InfoRow label="Current vehicle" value={c.current_vehicle ? `${c.current_vehicle.year || ""} ${c.current_vehicle.make || ""} ${c.current_vehicle.model || ""}`.trim() : "—"} />
      </div>
      <div>
        <SectionLabel>Ownership</SectionLabel>
        <InfoRow label="Owner" value={ph
          ? <span>{j.owner_name || j.owner_user_id || "Unassigned"} <span className="dim">→</span> {ph.to_user_name || ph.to_user} <Badge tone="orange">handoff pending</Badge></span>
          : (j.owner_name || j.owner_user_id || "Unassigned")} />
        {mine && (
          <div className="mt-1 flex items-center gap-2">
            <Btn size="sm" onClick={doAccept} disabled={accept.pending}>✓ Accept handoff</Btn>
            <span className="dim text-[.7rem]">handed over {timeAgo(ph.created_at)}; ownership moves to you on accept</span>
          </div>
        )}
        {j.vin && <InfoRow label="VIN" value={<span className="mono">{j.vin}</span>} />}
        <InfoRow label="Source" value={a.journey?.source === "service_lane"
          ? <Badge tone="purple">service lane</Badge> : (a.journey?.source || "—")} />
      </div>
      <div>
        <SectionLabel>Recent activity</SectionLabel>
        <div className="flex flex-col gap-2 mt-2">
          {(Array.isArray(a.recent_activity) ? a.recent_activity : []).slice(0, 6).map((x: any, i: number) => (
            <ActivityItem key={i} time={timeAgo(x.ts)}>{x.text}</ActivityItem>
          ))}
          {(!Array.isArray(a.recent_activity) || a.recent_activity.length === 0) && <div className="dim text-xs">No recent activity.</div>}
        </div>
      </div>
      {prefs.length > 0 && (
        <div>
          <SectionLabel>Preferences</SectionLabel>
          <div className="tag-group mt-2">{prefs.map((p) => <Tag key={p}>{p}</Tag>)}</div>
        </div>
      )}
      <div>
        <SectionLabel>Journey activity &amp; handoffs</SectionLabel>
        <div className="mt-2">
          <JourneyActivityFeed journeyId={a.journey?.journey_id} limit={5} />
        </div>
      </div>
      <InsightBox>
        {a.summary || a.equity?.explanation || "No summary yet."}
        {a.equity?.upgrade_signal?.recommend_upgrade && <div className="mt-1 dim">Trade-In Agent: {a.equity.upgrade_signal.reasons.join("; ")}</div>}
      </InsightBox>
      <div className="flex gap-2 flex-wrap">
        <button className="btn btn-alt btn-sm" onClick={onViewAsCustomer}>View as customer</button>
        {a.journey?.customer_id && <Link className="btn btn-ghost btn-sm" href={`/dealer/customers/${encodeURIComponent(a.journey.customer_id)}`}>Full profile</Link>}
      </div>
    </Panel>
  );
}
