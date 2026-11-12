"use client";

// F&I Desk queue (§5.13): journeys in FINANCE / TRADE_IN / AGREE with readiness
// badges (credit app in, lender decision, trade agreed, VIN reserved, compliance
// clear, pushed to DMS) and the attach-rate / PVR KPIs. Row → deal worksheet.

import { useRouter } from "next/navigation";
import { useState } from "react";
import {
  Async, Badge, Btn, Card, DataTable, DateTime, FilterPills, MarketProvider, Money, Page, ViewHeader, useToast, stageLabel } from "@/components/app";
import { ApiKpiGrid } from "@/app/dealer/kpis";
import { READINESS, ReadinessBadges } from "@/app/dealer/fni/parts";
import { useApi, useMutation } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";

export default function FniQueuePage() {
  const router = useRouter();
  const { persona } = usePersona();
  const q = useApi<any>("/fni/queue", { pollMs: 60000 });
  const toast = useToast();
  const desk = useMutation();
  const canDesk = ["dealer_fni", "dealer_manager"].includes(persona?.role || "");
  const [stage, setStage] = useState<"all" | "FINANCE" | "TRADE_IN" | "AGREE">("all");
  // a journey in the desk's stages with no deal yet: open the desk on it (deal from its saved build)
  const open = async (r: any) => {
    if (r.deal_id) { router.push(`/dealer/fni/${encodeURIComponent(r.deal_id)}`); return; }
    if (!canDesk) { toast("No deal desked yet for this journey", "error"); return; }
    const out = await desk.run(`/fni/journeys/${encodeURIComponent(r.journey_id)}/deal`);
    if (out?.deal?.deal_id) { toast(out.created ? "Desk opened from the saved build" : "Opened the existing deal", "success"); router.push(`/dealer/fni/${encodeURIComponent(out.deal.deal_id)}`); }
    else toast(desk.error || "Could not desk this journey", "error");
  };
  const rows: any[] = (q.data?.queue || []).filter((r: any) => stage === "all" || r.stage === stage);
  const k = q.data?.kpis;

  return (
    <MarketProvider marketId={q.data?.market_id || persona?.marketId}>
      <Page>
        <ViewHeader title="F&I Desk" subtitle="Deals in finance, trade and agreement — structure, protect, comply, and book."
                    actions={<Btn kind="secondary" size="sm" onClick={() => q.reload()}>Refresh</Btn>} />
        <Async q={q}>
          {() => (
            <>
              <ApiKpiGrid kpis={[
                { label: "Attach rate", value: k?.attach_rate, format: "pct", delta: `${k?.booked_deals ?? 0} booked deals` },
                { label: "PVR (F&I)", value: k?.pvr, format: "money" },
                { label: "Products / deal", value: k?.products_per_deal },
                { label: "In the desk", value: q.data?.count ?? 0 },
              ]} />
              <Card title="Deal queue" actions={<span className="dim text-xs" aria-live="polite">{rows.length} shown</span>}>
                <FilterPills value={stage} onChange={setStage} options={[
                  { id: "all", label: "All" }, { id: "FINANCE", label: "Finance" },
                  { id: "TRADE_IN", label: "Trade-in" }, { id: "AGREE", label: "Agree" },
                ]} />
                <DataTable<any>
                  rows={rows}
                  rowKey={(r) => r.journey_id}
                  onRowClick={(r) => void open(r)}
                  filterText={(r) => `${r.customer_name || ""} ${r.journey_id} ${r.deal_id || ""} ${r.model_id || ""}`}
                  filterPlaceholder="Filter by customer, deal, model…"
                  empty="No deals waiting in F&I."
                  columns={[
                    { key: "customer", header: "Customer", sort: (r) => r.customer_name,
                      render: (r) => <div><b>{r.customer_name || r.customer_id}</b><div className="dim text-xs mono">{r.journey_id}</div></div> },
                    { key: "stage", header: "Stage", sort: (r) => r.stage,
                      render: (r) => <Badge tone={r.stage === "AGREE" ? "purple" : r.stage === "TRADE_IN" ? "orange" : "blue"}>{stageLabel(r.stage)}</Badge> },
                    { key: "vehicle", header: "Vehicle", render: (r) => r.model_name || r.model_id || "—" },
                    { key: "price", header: "Deal", num: true, sort: (r) => r.price_total,
                      render: (r) => r.deal_id ? <Money value={r.price_total} /> : <Badge tone="orange">not desked</Badge> },
                    { key: "ready", header: `Readiness (${READINESS.length})`, sort: (r) => r.ready_count,
                      render: (r) => <ReadinessBadges readiness={r.readiness} compact /> },
                    { key: "updated", header: "Updated", sort: (r) => r.updated_at, render: (r) => <DateTime value={r.updated_at} mode="date" /> },
                  ]}
                />
              </Card>
            </>
          )}
        </Async>
      </Page>
    </MarketProvider>
  );
}
