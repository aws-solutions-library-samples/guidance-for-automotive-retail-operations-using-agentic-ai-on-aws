"use client";

// Inventory & Trade Desk (§5.6 extended, G-06): VIN grid with age, cost, market
// price comps and A1 demand score, reserve / unreserve against a journey; the
// trade disposition queue (keep-CPO / retail / wholesale / auction with A4
// reasoning); and A4 acquisition recommendations from the service drive.

import { useEffect, useState } from "react";
import {
  Async, Badge, Btn, Card, DataTable, DateTime, Field, FilterPills, MarketProvider, Modal, Money, Page,
  SourceRef, Tabs, ViewHeader, useToast,
} from "@/components/app";
import { ApiInsight, ApiKpiGrid, titleCase } from "@/app/dealer/kpis";
import { useApi, useMutation, useQueryParam } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";

type Tab = "inventory" | "trades" | "acquisitions";
const DECISIONS = ["keep_cpo", "retail", "wholesale", "auction"] as const;
const WRITE = ["dealer_inventory", "dealer_manager", "dealer_advisor", "dealer_fni"];
const DECIDE = ["dealer_inventory", "dealer_manager"];

export default function InventoryDeskPage() {
  const { persona } = usePersona();
  const tabParam = useQueryParam("tab");
  const modelParam = useQueryParam("model");
  const statusParam = useQueryParam("status");
  const agedParam = useQueryParam("aged");
  const vinParam = useQueryParam("vin");
  const journeyParam = useQueryParam("journey");   // F&I "Reserve a VIN" link: the journey to hold the unit for
  const [tab, setTab] = useState<Tab>("inventory");
  useEffect(() => { if (tabParam === "trades" || tabParam === "acquisitions") setTab(tabParam); }, [tabParam]);

  return (
    <MarketProvider marketId={persona?.marketId}>
      <Page>
        <ViewHeader title="Inventory & Trade Desk" subtitle="Stock vs. demand, reservations, trade dispositions, and service-drive acquisitions." />
        <Tabs value={tab} onChange={setTab} tabs={[
          { id: "inventory", label: "Inventory" }, { id: "trades", label: "Trade dispositions" },
          { id: "acquisitions", label: "Acquisitions" },
        ]} />
        {tab === "inventory" && <InventoryTab model={modelParam} status={statusParam} aged={agedParam} vin={vinParam}
                                              journey={journeyParam} canWrite={WRITE.includes(persona?.role || "")} />}
        {tab === "trades" && <TradesTab canDecide={DECIDE.includes(persona?.role || "")} />}
        {tab === "acquisitions" && <AcquisitionsTab />}
      </Page>
    </MarketProvider>
  );
}

function InventoryTab({ model, status, aged, vin, journey, canWrite }: {
  model: string | null; status: string | null; aged: string | null; vin: string | null; journey?: string | null; canWrite: boolean }) {
  const toast = useToast();
  const [st, setSt] = useState<string>("all");
  useEffect(() => { if (status) setSt(status); else if (journey) setSt("in_stock"); }, [status, journey]);
  const qs = new URLSearchParams();
  if (model) qs.set("model", model);
  const q = useApi<any>(`/inventory?${qs.toString()}`);
  const reserve = useMutation();
  const [target, setTarget] = useState<any | null>(null);
  const [journeyId, setJourneyId] = useState(journey || "");
  // ?journey= is read after the first render (useQueryParam), so the initial state above
  // is empty on a deep link from the F&I desk: fill the field once the parameter arrives
  useEffect(() => { if (journey) setJourneyId(journey); }, [journey]);

  const doReserve = async (v: any, body: any, label: string) => {
    const r = await reserve.run(`/inventory/${encodeURIComponent(v.vin)}/reserve`, body);
    if (r) { toast(label, "success"); setTarget(null); setJourneyId(journey || ""); q.reload(); } else toast(reserve.error || "Failed", "error");
  };

  return (
    <Async q={q}>
      {(d: any) => {
        let rows: any[] = d.vehicles;
        if (st !== "all") rows = rows.filter((v) => v.status === st);
        if (aged) rows = rows.filter((v) => (v.age_days || 0) >= Number(aged));
        if (vin) rows = rows.filter((v) => v.vin === vin || v.stock_number === vin);
        return (
          <>
            <ApiKpiGrid kpis={[
              { label: "In stock", value: d.kpis.in_stock }, { label: "In transit", value: d.kpis.in_transit },
              { label: "Reserved", value: d.kpis.reserved },
              { label: "Aged 60+ days", value: d.kpis.aged_60, trend: d.kpis.aged_60 ? "down" : null, delta: `avg age ${d.kpis.avg_age_days ?? "—"} days` },
            ]} />
            <Card title={model ? `Vehicles · ${model}` : "Vehicles"} actions={<span className="dim text-xs">{d.active_journeys} active journeys drive demand</span>}>
              <FilterPills value={st} onChange={setSt} options={[
                { id: "all", label: "All" }, { id: "in_stock", label: "In stock" }, { id: "in_transit", label: "In transit" },
                { id: "reserved", label: "Reserved" },
              ]} />
              <DataTable<any>
                rows={rows} rowKey={(v) => v.vin}
                filterText={(v) => `${v.vin} ${v.stock_number} ${v.model_id} ${v.model_name || ""} ${v.trim_name} ${v.color}`}
                filterPlaceholder="VIN, stock #, model…"
                initialSort={{ key: "age", dir: "desc" }}
                columns={[
                  { key: "vin", header: "VIN / stock", sort: (v) => v.vin,
                    render: (v) => <div><span className="mono text-xs">{v.vin}</span><div className="dim text-xs">#{v.stock_number}</div></div> },
                  { key: "model", header: "Vehicle", sort: (v) => v.model_id,
                    render: (v) => <div>{v.model_name || v.model_id} <span className="dim">{v.trim_name}</span><div className="dim text-xs">{v.color} · {v.powertrain}</div></div> },
                  { key: "age", header: "Age (days)", num: true, sort: (v) => v.age_days,
                    render: (v) => <span className={(v.age_days || 0) >= 90 ? "text-danger" : (v.age_days || 0) >= 60 ? "text-warning" : ""}>{v.age_days ?? "—"}</span> },
                  { key: "cost", header: "Cost", num: true, sort: (v) => v.cost, render: (v) => <Money value={v.cost} /> },
                  { key: "list", header: "List", num: true, sort: (v) => v.list_price, render: (v) => <Money value={v.list_price} /> },
                  { key: "market", header: "Market (comps)", num: true, sort: (v) => v.market_price,
                    render: (v) => <span title={v.source_ref}><Money value={v.market_price} /> <span className="dim text-xs">n={v.comps_n}</span></span> },
                  { key: "demand", header: "Demand", num: true, sort: (v) => v.demand_score,
                    render: (v) => <Badge tone={v.demand_score >= 10 ? "green" : v.demand_score >= 3 ? "blue" : "gray"}>{v.demand_score}</Badge> },
                  { key: "status", header: "Status", sort: (v) => v.status,
                    render: (v) => v.reserved_for_journey_id
                      ? <Badge tone="purple" title={v.reserved_for_journey_id}>reserved · {v.reserved_for_journey_id}</Badge>
                      : <Badge tone={v.status === "in_stock" ? "green" : "orange"}>{titleCase(v.status)}</Badge> },
                  { key: "act", header: "", render: (v) => !canWrite ? null : v.reserved_for_journey_id
                      ? <Btn kind="alt" size="sm" disabled={reserve.pending} onClick={() => doReserve(v, { reserve: false }, `${v.vin} released`)}>Release</Btn>
                      : (v.status === "in_stock" || v.status === "in_transit") && <Btn kind="present" onClick={() => setTarget(v)}>Reserve</Btn> },
                ]}
              />
            </Card>
            <Card title="Demand vs. stock by model">
              <DataTable<any> rows={d.by_model} rowKey={(b) => b.model_id} dense
                columns={[
                  { key: "m", header: "Model", render: (b) => b.model_name || b.model_id },
                  { key: "u", header: "Units", num: true, sort: (b) => b.units, render: (b) => b.units },
                  { key: "a", header: "Aged 60+", num: true, sort: (b) => b.aged_60, render: (b) => b.aged_60 },
                  { key: "d", header: "Active demand", num: true, sort: (b) => b.demand, render: (b) => b.demand },
                ]} />
            </Card>
            <Modal open={Boolean(target)} onClose={() => setTarget(null)} title={`Reserve ${target?.vin || ""}`}
                   footer={<><Btn kind="secondary" onClick={() => setTarget(null)}>Cancel</Btn>
                     <Btn kind="primary" disabled={!journeyId.trim() || reserve.pending}
                          onClick={() => doReserve(target, { journey_id: journeyId.trim() }, `${target.vin} reserved for ${journeyId.trim()}`)}>Reserve</Btn></>}>
              <p className="muted text-sm">Hold this vehicle for a customer journey at this store. Demand and Discovery Agent matches update immediately.</p>
              <Field label="Journey ID" hint="e.g. J-US-J1 — copy from the Journeys board or F&I Desk">
                {(id) => <input id={id} className="input mono" value={journeyId} onChange={(e) => setJourneyId(e.target.value)} autoFocus />}
              </Field>
            </Modal>
          </>
        );
      }}
    </Async>
  );
}

function TradesTab({ canDecide }: { canDecide: boolean }) {
  const toast = useToast();
  const q = useApi<any>("/trades/dispositions");
  const decide = useMutation();
  const [only, setOnly] = useState<"pending" | "all">("pending");
  const run = async (t: any, decision: string) => {
    const r = await decide.run(`/trades/${encodeURIComponent(t.disposition_id)}/decide`, { decision, reason: t.reasoning?.join("; ") });
    if (r) { toast(`${t.vehicle || t.disposition_id}: ${titleCase(decision)}`, "success"); q.reload(); } else toast(decide.error || "Failed", "error");
  };
  return (
    <Async q={q}>
      {(d: any) => {
        const rows = d.dispositions.filter((t: any) => only === "all" || t.status === "pending");
        return (
          <Card title="Trade disposition queue" actions={<Badge tone={d.pending ? "orange" : "green"}>{d.pending} pending</Badge>}>
            <FilterPills value={only} onChange={setOnly} options={[{ id: "pending", label: "Pending" }, { id: "all", label: "All" }]} />
            <DataTable<any> rows={rows} rowKey={(t) => t.disposition_id} empty="No trades awaiting a decision."
              columns={[
                { key: "v", header: "Trade", sort: (t) => t.vehicle,
                  render: (t) => <div><b>{t.vehicle || "—"}</b><div className="dim text-xs">acquired <DateTime value={t.acquired_at} mode="date" /></div></div> },
                { key: "tv", header: "Trade value", num: true, sort: (t) => t.trade_value, render: (t) => <Money value={t.trade_value} /> },
                { key: "rc", header: "Recon", num: true, render: (t) => <Money value={t.recon_cost} /> },
                { key: "rec", header: "Trade-In Agent recommends", render: (t) => (
                  <div><Badge tone="purple">{t.recommendation_label || titleCase(t.recommendation)}</Badge>
                    <div className="dim text-xs mt-1">{t.reasoning?.join(" · ")}</div><SourceRef refId={t.source_ref} /></div>) },
                { key: "dec", header: "Decision", render: (t) => t.status === "decided"
                    ? <Badge tone="green">{titleCase(t.decision)} · {t.decided_by_name || "staff"}</Badge>
                    : canDecide ? (
                      <div className="flex flex-wrap gap-1">
                        {DECISIONS.map((x) => <Btn key={x} kind={x === t.recommendation ? "present" : "alt"} size="sm"
                                                   disabled={decide.pending} onClick={() => run(t, x)}>{titleCase(x)}</Btn>)}
                      </div>) : <span className="dim">pending</span> },
              ]} />
          </Card>
        );
      }}
    </Async>
  );
}

function AcquisitionsTab() {
  const q = useApi<any>("/inventory/acquisitions");
  return (
    <Async q={q}>
      {(d: any) => (
        <>
          <ApiInsight insight={{ text: d.count ? `${d.count} service-drive or agent-flagged vehicles match current store demand. Make an offer before the customer leaves the lane.` : "No acquisition opportunities match current demand right now.", sources: [d.source_ref] }} title="Acquisition opportunities" />
          <Card title="Acquisition recommendations">
            <DataTable<any> rows={d.acquisitions} rowKey={(a, i) => `${a.vin}-${i}`} empty="Nothing to acquire right now."
              columns={[
                { key: "vin", header: "Vehicle", render: (a) => <div><b>{a.vehicle || a.vin}</b><div className="mono dim text-xs">{a.vin}</div></div> },
                { key: "o", header: "Origin", render: (a) => <Badge tone={a.origin === "agent" ? "purple" : "blue"}>{titleCase(a.origin)}</Badge> },
                { key: "d", header: "Demand signals", num: true, render: (a) => a.demand_signals ?? "—" },
                { key: "r", header: "Offer range", render: (a) => a.offer_range
                    ? <span><Money value={a.offer_range.low} /> – <Money value={a.offer_range.high} /></span> : (a.reason || "—") },
                { key: "l", header: "Autonomy", render: (a) => <Badge tone="gray">{a.autonomy_level || "—"}</Badge> },
                { key: "s", header: "Evidence", render: (a) => <SourceRef refId={a.source_ref} /> },
              ]} />
          </Card>
        </>
      )}
    </Async>
  );
}
