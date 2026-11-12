"use client";

// Journeys (A2) — spec §5.4. Kanban of the 8 purchase stages; card = customer,
// vehicle, days in stage, missing-context badge, advisor, service-lane chip.
// Filters: dealer, advisor, source, stalled. Card → drawer (guarded advance,
// Run Play, activity); "Open in Advisor". Bulk: Nudge stalled (A2 outreach).

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { AgentActionsProvider } from "@/components/AgentBar";
import {
  Async, Badge, Btn, FilterPills, Kanban, Kpi, KpiGrid, Page, PURCHASE_STAGES, ViewHeader, stageLabel, useToast,
} from "@/components/app";
import { JourneyDrawer } from "@/app/dealer/JourneyDrawer";
import { useApi, useMutation, useQueryParam } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";

interface Card {
  journey_id: string; stage: string; dealer_id?: string; customer_name?: string; customer_id?: string;
  vehicle_name?: string; primary_model_id?: string; advisor_name?: string; owner_user_id?: string;
  pending_handoff?: { to_user: string; to_user_name?: string } | null;
  source?: string; stalled?: boolean; idle_days?: number | null; days_in_stage?: number | null; gaps?: string[];
  nurture_active?: boolean;
}

export default function JourneysPage() {
  const router = useRouter();
  const toast = useToast();
  const { persona } = usePersona();
  const focus = useQueryParam("focus");
  const q = useApi<any>("/journeys/board", { pollMs: 60000 });
  const nudge = useMutation();
  const [dealer, setDealer] = useState("all");
  const [advisor, setAdvisor] = useState("all");
  const [source, setSource] = useState("all");
  const [onlyStalled, setOnlyStalled] = useState(false);
  const [showOwn, setShowOwn] = useState(false);
  const [open, setOpen] = useState<string | null | undefined>(undefined);   // undefined = follow ?focus=
  const openId = open === undefined ? focus : open;

  const all: Card[] = useMemo(() => Object.values(q.data?.lanes || {}).flat() as Card[], [q.data]);
  const opts = (k: keyof Card) => Array.from(new Set(all.map((c) => c[k]).filter(Boolean) as string[])).sort();
  const shown = all.filter((c) => (dealer === "all" || c.dealer_id === dealer)
    && (advisor === "all" || c.owner_user_id === advisor) && (source === "all" || c.source === source)
    && (!onlyStalled || c.stalled));
  const stalled = shown.filter((c) => c.stalled && c.stage !== "OWN");
  const stages = PURCHASE_STAGES.filter((s) => showOwn || s !== "OWN");
  const columns = stages.map((s) => ({ id: s, title: stageLabel(s), items: shown.filter((c) => c.stage === s) }));
  const canWrite = persona && !persona.isOem;

  const bulkNudge = async () => {
    if (!stalled.length) return;
    if (!window.confirm(`Send a follow-up to ${stalled.length} stalled journey(s) through the active outreach provider?`)) return;
    const out = await nudge.run("/journeys/bulk/nudge", { journey_ids: stalled.slice(0, 50).map((c) => c.journey_id) });
    if (out) toast(`Nudged ${out.sent}/${out.count} stalled journeys`, out.sent ? "success" : "error");
    else toast(nudge.error || "Nudge failed", "error");
  };

  const kpis: Kpi[] = [
    { label: "Active journeys", value: shown.filter((c) => c.stage !== "OWN").length },
    { label: "Stalled", value: stalled.length, trend: stalled.length ? "down" : undefined,
      delta: stalled.length ? "need a nudge" : "none", onClick: () => setOnlyStalled((x) => !x) },
    { label: "Missing context", value: shown.filter((c) => (c.gaps || []).length > 0).length },
    { label: "From service lane", value: shown.filter((c) => c.source === "service_lane" && c.stage !== "OWN").length },
  ];

  // The board contributes its bulk-nudge endpoint and the open card's trace
  // scope to the global AgentBar (§1.6).
  return (
    <AgentActionsProvider value={{
      journeyId: openId || null,
      actions: canWrite && stalled.length ? {
        "a2.stalled_nudger": { label: `Nudge stalled (${stalled.length})`, run: bulkNudge },
      } : undefined,
    }}>
    <Page>
      <ViewHeader title="Journeys" subtitle="Every active purchase journey in your scope — the Journey Agent owns the stage machine."
        actions={<>
          {canWrite && <Btn kind="secondary" size="sm" onClick={bulkNudge} disabled={!stalled.length || nudge.pending}>Nudge stalled ({stalled.length})</Btn>}
          <Btn kind="ghost" size="sm" onClick={() => q.reload()}>Refresh</Btn>
        </>} />
      <KpiGrid items={kpis} />
      <div className="flex flex-wrap gap-3 items-center mb-3" role="group" aria-label="Filters">
        <select className="select w-auto" value={dealer} onChange={(e) => setDealer(e.target.value)} aria-label="Dealer">
          <option value="all">All dealers</option>{opts("dealer_id").map((d) => <option key={d}>{d}</option>)}
        </select>
        <select className="select w-auto" value={advisor} onChange={(e) => setAdvisor(e.target.value)} aria-label="Advisor">
          <option value="all">All advisors</option>
          {opts("owner_user_id").map((d) => <option key={d} value={d}>{all.find((c) => c.owner_user_id === d)?.advisor_name || d}</option>)}
        </select>
        <FilterPills value={source} onChange={setSource}
          options={[{ id: "all", label: "Any source" }, ...opts("source").map((s) => ({ id: s, label: s.replace(/_/g, " ") }))]} />
        <label className="toggle"><input type="checkbox" checked={onlyStalled} onChange={(e) => setOnlyStalled(e.target.checked)} /> Stalled only</label>
        <label className="toggle"><input type="checkbox" checked={showOwn} onChange={(e) => setShowOwn(e.target.checked)} /> Show OWN</label>
      </div>
      <Async q={q} rows={6}>
        {() => (
          <Kanban columns={columns} renderCard={(c: Card) => (
            <button key={c.journey_id} className="kanban-card" onClick={() => setOpen(c.journey_id)}
                    onDoubleClick={() => router.push(`/dealer/advisor/${encodeURIComponent(c.journey_id)}`)}
                    aria-label={`${c.customer_name || c.journey_id}, ${stageLabel(c.stage)}`}>
              <div className="flex justify-between gap-2">
                <b className="truncate">{c.customer_name || c.customer_id}</b>
                {c.days_in_stage != null && <span className="dim text-xs whitespace-nowrap">{Math.round(c.days_in_stage)}d</span>}
              </div>
              <div className="muted text-xs truncate">{c.vehicle_name || c.primary_model_id || "No vehicle yet"}</div>
              <div className="flex flex-wrap gap-1 mt-2">
                {c.stalled && <Badge tone="red">Stalled</Badge>}
                {(c.gaps || []).length > 0 && <Badge tone="orange" title={(c.gaps || []).join(", ")}>Missing {(c.gaps || []).length}</Badge>}
                {c.source === "service_lane" && <Badge tone="purple">service lane</Badge>}
                {c.nurture_active && <Badge tone="blue">nurture</Badge>}
              </div>
              <div className="dim text-[.68rem] mt-1 truncate">{c.advisor_name || c.owner_user_id || "Unassigned"}{c.pending_handoff ? ` → ${c.pending_handoff.to_user_name || c.pending_handoff.to_user} (pending)` : ""} · {c.dealer_id}</div>
            </button>
          )} />
        )}
      </Async>
      <JourneyDrawer journeyId={openId} onClose={() => setOpen(null)} onChanged={() => q.reload()} />
    </Page>
    </AgentActionsProvider>
  );
}
