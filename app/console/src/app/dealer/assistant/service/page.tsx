"use client";

// Service Lane — spec §5.8 extended: the SALES-facing read view of today's
// service traffic (A7 service journeys, never raw DMS). Each vehicle shows the
// A3 upsell brief, A4 equity / upgrade flags (positive equity, lease-end ≤ 6
// months, high mileage) and the repurchase/upgrade handoff (A7
// handoff_to_sales → purchase journey in DISCOVER, source=service_lane).
// The sales floor never edits an RO here.

import Link from "next/link";
import { useState } from "react";
import {
  AiInsight, Async, Badge, Btn, Card, DateTime, Distance, FilterPills, Kpi, KpiGrid, MarketProvider, Money, Page,
  SourceRef, TalkTrack, ViewHeader, useToast,
} from "@/components/app";
import { useApi, useMutation } from "@/lib/hooks";
import { useCanOpen, usePersona } from "@/lib/persona";

const FLAG: Record<string, { label: string; tone: "green" | "orange" | "blue" }> = {
  positive_equity: { label: "Positive equity", tone: "green" },
  lease_end: { label: "Lease ending", tone: "orange" },
  high_mileage: { label: "High mileage", tone: "blue" },
};

export default function ServiceLanePage() {
  const toast = useToast();
  const { persona } = usePersona();
  const can = useCanOpen();
  const [today, setToday] = useState(true);
  const [filter, setFilter] = useState<"all" | "flagged">("flagged");
  const date = new Date().toISOString().slice(0, 10);
  const q = useApi<any>(`/service/lane${today ? `?date=${date}` : ""}`, { pollMs: 60000 });
  const ho = useMutation();
  const canHandoff = persona && ["dealer_service", "dealer_service_manager", "dealer_manager"].includes(persona.role);

  const items: any[] = (q.data?.items || []).filter((i: any) => filter === "all" || i.flags.length > 0);
  const handoff = async (i: any) => {
    const reason = i.flags.map((f: any) => f.flag).join(", ") || "customer interest";
    if (!window.confirm(`Hand ${i.customer_name || i.customer_id} to sales (${reason})?`)) return;
    const out = await ho.run(`/service/${encodeURIComponent(i.service_journey_id)}/handoff-to-sales`, { reason });
    if (out) { toast(out.created_journey === false ? `Attached to open journey ${out.new_journey_id}` : `Purchase journey ${out.new_journey_id} created`, "success"); q.reload(); }
    else toast(ho.error || "Handoff failed", "error");
  };

  const all: any[] = q.data?.items || [];
  const kpis: Kpi[] = [
    { label: "Vehicles in service", value: all.length },
    { label: "Equity positive", value: all.filter((i) => i.flags.some((f: any) => f.flag === "positive_equity")).length },
    { label: "Lease ending", value: all.filter((i) => i.flags.some((f: any) => f.flag === "lease_end")).length },
    { label: "Already with sales", value: all.filter((i) => i.sales_journeys.length > 0).length },
  ];

  return (
    <Page>
      <ViewHeader title="Service Lane" subtitle="Today's service traffic, seen by sales — upsell briefs and upgrade flags. Service owns the RO."
        actions={<label className="toggle"><input type="checkbox" checked={today} onChange={(e) => setToday(e.target.checked)} /> Promised by today</label>} />
      <KpiGrid items={kpis} />
      <FilterPills value={filter} onChange={setFilter} options={[{ id: "flagged", label: "Upgrade signals" }, { id: "all", label: "All vehicles" }]} />
      <Async q={q} rows={5}>{() => items.length === 0 ? <Card><div className="dim">No vehicles match.</div></Card> : (
        <div className="flex flex-col gap-3">
          {items.map((i) => (
            <MarketProvider key={i.service_journey_id} marketId={i.market_id}>
              <Card title={<span>{i.customer_name || i.customer_id} <span className="dim text-xs">· {i.vehicle_name || ""} {i.vin}</span></span>}
                    actions={<Badge tone="gray">{i.stage.replace(/_/g, " ")}</Badge>}>
                <div className="flex flex-wrap gap-2 mb-2">
                  {i.flags.map((f: any) => (
                    <Badge key={f.flag} tone={FLAG[f.flag]?.tone || "gray"}>
                      {FLAG[f.flag]?.label || f.flag}
                      {f.flag === "positive_equity" && <> · <Money value={f.value} /></>}
                      {f.flag === "high_mileage" && <> · <Distance value={f.value} /></>}
                      {f.flag === "lease_end" && <> · {f.months} mo</>}
                    </Badge>
                  ))}
                  {i.promised_at && <span className="dim text-xs">promised <DateTime value={i.promised_at} /></span>}
                </div>
                {i.upsell?.talk_track && <TalkTrack>“{i.upsell.talk_track}”</TalkTrack>}
                {i.upsell?.items?.length > 0 && (
                  <AiInsight title="Sales Agent upsell brief" sources={i.upsell.items.map((u: any) => u.ref)}>
                    {i.upsell.items.map((u: any) => (
                      <div key={u.ref}>{u.item} — {u.status}{u.amount != null ? <> · <Money value={u.amount} /></> : null}</div>
                    ))}
                  </AiInsight>
                )}
                {/* §5.8 ext: loyalty offer alongside the upsell brief — rendered when the
                    lane read model supplies one (field not yet emitted by the sandbox API). */}
                {i.loyalty_offer && (
                  <div className="incentive-row eligible">
                    <span aria-hidden>🎁</span>
                    <div className="incentive-info">
                      <div className="incentive-name">Loyalty offer · {i.loyalty_offer.name || i.loyalty_offer.offer || "owner loyalty"}</div>
                      <div className="incentive-amount">
                        {i.loyalty_offer.amount != null ? <Money value={i.loyalty_offer.amount} /> : i.loyalty_offer.detail || "see terms"}
                        {i.loyalty_offer.expires_at && <> · expires <DateTime value={i.loyalty_offer.expires_at} mode="date" /></>}
                      </div>
                    </div>
                    <SourceRef refId={i.loyalty_offer.ref || i.loyalty_offer.source_ref} />
                  </div>
                )}
                {/* The Advisor screen and Customer 360 are the sales floor's: the
                    service desk sees that a sales journey is open, without a link. */}
                <div className="flex gap-2 flex-wrap items-center">
                  {i.sales_journeys.length > 0
                    ? i.sales_journeys.map((j: string) => can("dealer.advisor")
                      ? <Link key={j} className="btn btn-alt btn-sm" href={`/dealer/advisor/${encodeURIComponent(j)}`}>Open sales journey {j}</Link>
                      : <Badge key={j} tone="gray" title="The sales floor works this journey">Sales journey {j} open</Badge>)
                    : canHandoff && <Btn size="sm" onClick={() => handoff(i)} disabled={ho.pending}>Hand to sales</Btn>}
                  {i.customer_id && can("dealer.customer360") && <Link className="btn btn-ghost btn-sm" href={`/dealer/customers/${encodeURIComponent(i.customer_id)}`}>Customer 360</Link>}
                  <SourceRef refId={i.flags[0]?.ref} />
                </div>
              </Card>
            </MarketProvider>
          ))}
        </div>
      )}</Async>
    </Page>
  );
}
