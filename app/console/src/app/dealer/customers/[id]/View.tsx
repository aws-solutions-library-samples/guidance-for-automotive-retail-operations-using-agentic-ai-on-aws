"use client";

// Customer 360 — profile, journeys (incl. previous/superseded laps), owned
// vehicles, service journeys, saved builds, trade valuation, consent, and recent
// activity. Contact data is shown only to roles the server lets through
// (technician/parts get 403); credit (C4) fields are never returned.

import Link from "next/link";
import { useState } from "react";
import {
  ActivityItem, Async, Badge, Card, CustomerCard, DataTable, DateTime, Distance, ErrorState, InfoRow, MarketProvider,
  Money, Page, SectionLabel, Tag, stageLabel,
} from "@/components/app";
import { JourneyDrawer } from "@/app/dealer/JourneyDrawer";
import { ViewAsCustomer } from "@/components/ViewAsCustomer";
import { timeAgo, useApi, useRouteId } from "@/lib/hooks";

export default function CustomerView() {
  const id = useRouteId(2);
  const q = useApi<any>(id ? `/customers/${encodeURIComponent(id)}` : null);
  const [open, setOpen] = useState<string | null>(null);
  if (!id) return <div className="page"><ErrorState error="No customer selected." /></div>;
  return (
    <Page>
      <Async q={q} rows={8}>{(d) => {
        const c = d.customer;
        const name = [c.given_name, c.family_name].filter(Boolean).join(" ") || c.customer_id;
        const lp = c.lifestyle_profile || {};
        return (
          <MarketProvider marketId={c.market_id}>
            <div className="grid gap-4 grid-cols-1 lg:grid-cols-[340px_minmax(0,1fr)]">
              <div className="flex flex-col gap-4">
                <CustomerCard name={name} meta={<>{c.customer_id} · {c.dealer_id}</>}
                  score={c.budget ? <><Money value={c.budget.price_min} />–<Money value={c.budget.price_max} /></> : undefined} />
                <ViewAsCustomer customerId={c.customer_id} name={name} />
                <Card title="Contact & profile">
                  <InfoRow label="Email" value={c.email} />
                  <InfoRow label="Phone" value={c.phone} />
                  <InfoRow label="Address" value={[c.address?.city, c.address?.state || c.address?.bundesland].filter(Boolean).join(", ")} />
                  <InfoRow label="Daily drive" value={c.commute_per_day ? <Distance value={c.commute_per_day} /> : "—"} />
                  <InfoRow label="Household" value={c.household_size} />
                  <InfoRow label="Advisor" value={c.assigned_advisor_user_id} />
                  <InfoRow label="Data class" value={<Badge tone="orange">{c.data_class || "C3"}</Badge>} />
                  {Object.keys(lp).length > 0 && (<><SectionLabel>Lifestyle</SectionLabel>
                    <div className="tag-group mt-2">{Object.entries(lp).filter(([k, v]) => v && !["confidence", "distance_unit", "commute_per_day"].includes(k))
                      .map(([k, v]) => <Tag key={k}>{k.replace(/_/g, " ")}: {Array.isArray(v) ? v.join(", ") : String(v)}</Tag>)}</div></>)}
                </Card>
                <Card title="Consent">
                  {d.consent ? (<>
                    <InfoRow label="Email" value={d.consent.email_opt_in ? "opted in" : "opted out"} />
                    <InfoRow label="SMS" value={d.consent.sms_opt_in ? "opted in" : "opted out"} />
                    <InfoRow label="Voice" value={d.consent.voice_opt_in ? "opted in" : "opted out"} />
                    <InfoRow label="Quiet hours" value={d.consent.quiet_hours ? `${d.consent.quiet_hours.start}–${d.consent.quiet_hours.end}` : "—"} />
                  </>) : <div className="dim text-sm">No consent record.</div>}
                </Card>
                {d.valuation && (
                  <Card title="Trade valuation">
                    <InfoRow label="Vehicle" value={`${d.valuation.year} ${d.valuation.make} ${d.valuation.model}`} />
                    <InfoRow label="Value" value={<Money value={d.valuation.trade_value} />} />
                    <InfoRow label="Equity" value={<Money value={d.valuation.equity} />} />
                    <InfoRow label="Valued" value={<DateTime value={d.valuation.valued_at} mode="date" />} />
                  </Card>
                )}
              </div>
              <div className="flex flex-col gap-4">
                <Card title="Purchase journeys">
                  <DataTable rows={d.journeys} rowKey={(j: any) => j.journey_id} onRowClick={(j: any) => setOpen(j.journey_id)}
                    columns={[
                      { key: "id", header: "Journey", render: (j: any) => <span className="mono">{j.journey_id}</span> },
                      { key: "stage", header: "Stage", render: (j: any) => stageLabel(j.stage) },
                      { key: "source", header: "Source", render: (j: any) => j.source === "service_lane" ? <Badge tone="purple">service lane</Badge> : j.source },
                      { key: "lap", header: "Lap", render: (j: any) => <>{j.previous_journey_id && <span className="dim text-xs">from {j.previous_journey_id}</span>}{j.superseded_by && <Badge tone="gray">superseded → {j.superseded_by}</Badge>}</> },
                      { key: "act", header: "", render: (j: any) => j.stage !== "OWN" ? <Link className="btn btn-alt btn-sm" href={`/dealer/advisor/${encodeURIComponent(j.journey_id)}`} onClick={(e) => e.stopPropagation()}>Advisor</Link> : null },
                    ]} />
                </Card>
                <Card title="Vehicles & service">
                  {(d.vehicles || []).map((v: any) => (
                    <InfoRow key={v.vin} label={<span className="mono">{v.vin}</span>} value={<>{v.model_id} · <Distance value={v.odometer} /> · {v.ownership}</>} />
                  ))}
                  {(d.service_journeys || []).map((s: any) => (
                    <InfoRow key={s.service_journey_id} label={s.service_journey_id} value={<>{s.stage.replace(/_/g, " ")} {s.promised_at && <span className="dim">· <DateTime value={s.promised_at} /></span>}</>} />
                  ))}
                  {!(d.vehicles || []).length && !(d.service_journeys || []).length && <div className="dim text-sm">No owned vehicles on file.</div>}
                </Card>
                <Card title="Saved builds">
                  {(d.configurations || []).length === 0 ? <div className="dim text-sm">None.</div> : d.configurations.map((cfg: any) => (
                    <InfoRow key={cfg.config_id} label={`${cfg.model_name || cfg.model_id} · ${cfg.trim_name || "trim on file"}`} value={<><Money value={cfg.price_total} /> · <Money value={cfg.est_monthly_buy} />/mo</>} />
                  ))}
                </Card>
                <Card title="Recent activity">
                  <div className="flex flex-col gap-2">
                    {(d.recent_activity || []).map((x: any, i: number) => <ActivityItem key={i} time={timeAgo(x.ts)}>{x.text}</ActivityItem>)}
                  </div>
                </Card>
              </div>
            </div>
            <JourneyDrawer journeyId={open} onClose={() => setOpen(null)} onChanged={() => q.reload()} />
          </MarketProvider>
        );
      }}</Async>
    </Page>
  );
}
