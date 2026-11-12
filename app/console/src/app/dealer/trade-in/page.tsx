"use client";

// Trade-In (A4) — spec §5.6: VIN lookup, valuation range gauge, payoff +
// equity, upgrade recommendation with payment delta, "Add to journey".
// Tab 2: Upgrade Prospects (lease-end, aging, service frequency) with scores.

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  AiInsight, Async, Badge, Btn, Card, DataTable, ErrorState, Field, InfoRow, MarketProvider, Money, Page, ProgressBar,
  SourceRef, Tabs, ViewHeader, useToast,
} from "@/components/app";
import { apiGet } from "@/lib/api";
import { useApi, useMutation } from "@/lib/hooks";
import { useCanOpen } from "@/lib/persona";

function RangeGauge({ low, high, value }: { low: number; high: number; value: number }) {
  const pct = high > low ? ((value - low) / (high - low)) * 100 : 50;
  return (
    <div aria-label="Valuation range">
      <div className="relative h-3 rounded-full" style={{ background: "linear-gradient(90deg, var(--danger), var(--warning), var(--success))", opacity: 0.85 }}>
        <div className="absolute -top-1 h-5 w-1.5 rounded bg-white shadow" style={{ left: `calc(${Math.max(0, Math.min(100, pct))}% - 3px)` }} />
      </div>
      <div className="flex justify-between text-xs mt-1 dim"><span><Money value={low} /> poor</span><span><Money value={high} /> excellent</span></div>
    </div>
  );
}

function ValuationTab() {
  const toast = useToast();
  const [vin, setVin] = useState("");
  const [mileage, setMileage] = useState("");
  const [condition, setCondition] = useState("good");
  const [journey, setJourney] = useState("");
  const [v, setV] = useState<any>(null);
  const [delta, setDelta] = useState<any>(null);
  const val = useMutation();
  const pd = useMutation();
  const add = useMutation();
  // The purchase-journey half (payment comparison, Add to journey, Advisor link)
  // stays with sales: those routes serve the sales roles (SALES), which open the
  // Advisor or the Inventory desk. The service advisor, who values trades and
  // works upgrade prospects here, sees the valuation only.
  const can = useCanOpen();
  const sales = can("dealer.advisor") || can("dealer.inventory");

  const [fromJourney, setFromJourney] = useState<string | null>(null);
  const show = async (out: any, preferJourney?: string | null) => {
    setV(out);
    const j = (preferJourney && out.active_journeys?.includes(preferJourney) ? preferJourney : out.active_journeys?.[0]) || "";
    setJourney(j);
    setDelta(j && sales ? await pd.run("/trade-in/payment-delta", { journey_id: j }) : null);
  };
  const lookup = async () => {
    setFromJourney(null);
    const out = await val.run("/trade-in/valuation", { vin: vin.trim(), mileage: mileage ? Number(mileage) : undefined, condition });
    if (out) await show(out);
    else toast(val.error || "Lookup failed", "error");
  };
  // Deep link: /dealer/trade-in?journey=<id> values the trade of THAT journey's customer (the
  // Advisor's "Trade-In desk" link, the Scenario Replay) instead of asking for a VIN.
  useEffect(() => {
    const jid = new URLSearchParams(window.location.search).get("journey");
    if (!jid) return;
    (async () => {
      try {
        const eq = await apiGet<any>(`/journeys/${encodeURIComponent(jid)}/equity`);
        if (!eq?.customer_id) { toast("That journey has no customer on file", "error"); return; }
        setFromJourney(jid);
        const out = await val.run("/trade-in/valuation", { customer_id: eq.customer_id });
        if (out) await show(out, jid);
        else toast(val.error || "This customer has no vehicle to value", "error");
      } catch (e: any) { toast(e?.message || "Could not load the journey", "error"); }
    })();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const attach = async () => {
    if (!journey) return;
    const out = await add.run(`/journeys/${encodeURIComponent(journey)}/trade`, { mileage: mileage ? Number(mileage) : undefined, condition });
    if (out) toast(`Trade recorded on ${journey} (valuation ${out.valuation_id})`, "success");
    else toast(add.error || "Could not add", "error");
  };

  return (
    <div className="grid gap-4 grid-cols-1 lg:grid-cols-[360px_minmax(0,1fr)]">
      <Card title="Vehicle">
        {fromJourney && (
          <div className="ai-response-card mb-3" role="note">
            <div className="text-xs"><b>From journey</b> {can("dealer.advisor") ? <Link className="mono underline" href={`/dealer/advisor/${encodeURIComponent(fromJourney)}`}>{fromJourney}</Link> : <span className="mono">{fromJourney}</span>}
              {v?.customer_name ? <> · {v.customer_name}</> : null} — the Trade-In Agent valued the owner&apos;s vehicle on file. Enter a VIN below to value a different car.</div>
          </div>
        )}
        <form onSubmit={(e) => { e.preventDefault(); lookup(); }}>
          <Field label="VIN">{(id) => <input id={id} className="input mono" value={vin} onChange={(e) => setVin(e.target.value.toUpperCase())} placeholder="17-character VIN" required />}</Field>
          <Field label="Current odometer" hint="Leave blank to use the last recorded reading">{(id) => <input id={id} className="input" inputMode="numeric" value={mileage} onChange={(e) => setMileage(e.target.value.replace(/\D/g, ""))} />}</Field>
          <Field label="Condition">{(id) => (
            <select id={id} className="select" value={condition} onChange={(e) => setCondition(e.target.value)}>
              {["excellent", "good", "fair", "poor"].map((c) => <option key={c}>{c}</option>)}
            </select>)}</Field>
          <Btn type="submit" disabled={val.pending || !vin}>{val.pending ? "Valuing…" : "Get valuation"}</Btn>
        </form>
        {val.error && <div className="mt-3"><ErrorState error={val.error} /></div>}
      </Card>
      {v ? (
        <MarketProvider marketId={v.currency === "EUR" ? "de" : "us"}>
          <div className="flex flex-col gap-4">
            <Card title={<>{v.vehicle?.year} {v.vehicle?.make} {v.vehicle?.model} <span className="dim text-xs">· {v.customer_name}</span></>}
                  actions={<SourceRef refId={v.source_ref} />}>
              <div className="kpi-grid">
                <div className="kpi-card"><div className="kpi-value"><Money value={v.trade_value} /></div><div className="kpi-label">Trade value</div></div>
                <div className="kpi-card"><div className="kpi-value"><Money value={v.payoff} /></div><div className="kpi-label">{v.loan_lease?.payoff_kind === "lease buyout" ? "Lease buyout" : v.loan_lease?.payoff_kind === "owned outright" ? "Payoff · owned outright" : "Loan payoff"}</div></div>
                <div className="kpi-card"><div className="kpi-value" style={{ color: v.equity >= 0 ? "var(--up)" : "var(--down)" }}><Money value={v.equity} /></div><div className="kpi-label">Equity</div></div>
                <div className="kpi-card"><div className="kpi-value">{v.loan_lease?.ownership || "—"}</div><div className="kpi-label">Ownership</div>
                  {v.loan_lease?.months_remaining ? <div className="kpi-delta">{v.loan_lease.months_remaining} mo left</div> : null}</div>
              </div>
              <RangeGauge low={v.range.low} high={v.range.high} value={v.trade_value} />
              {v.adjustments?.length > 0 && <div className="dim text-xs mt-2">Adjustments: {v.adjustments.map((a: any) => `${a.factor}${a.multiplier ? ` ×${a.multiplier}` : ""}`).join(", ")}</div>}
            </Card>
            <Card title="Upgrade recommendation">
              {!sales ? (
                <div className="dim text-sm">
                  {v.active_journeys?.length > 0 ? "This owner has an active purchase journey with sales; the payment comparison and adding the trade happen there."
                    : "No active purchase journey for this owner. Hand the owner to sales to start one."}
                </div>
              ) : delta && !delta.error ? (
                <>
                  <AiInsight title={v.equity > 0 ? "Positive equity — good upgrade timing" : "Equity is negative — consider timing"} sources={[delta.source_ref, v.source_ref]}>
                    Applying <Money value={Math.max(v.equity, 0)} /> of trade equity, the saved build comes to about <b><Money value={delta.new_monthly} />/mo</b> over {delta.inputs.term_months} months at {delta.inputs.apr_pct}% ({delta.inputs.credit_tier} tier estimate — not a credit decision).
                    {delta.note ? ` ${delta.note}.` : ""}
                  </AiInsight>
                  <InfoRow label="Formula" value={<span className="mono text-xs">{delta.formula}</span>} />
                </>
              ) : <div className="dim text-sm">{journey ? "No saved build to compare against yet." : "No active purchase journey for this owner."}</div>}
              {sales && (
                <div className="flex gap-2 mt-3 flex-wrap items-center">
                  {v.active_journeys?.length > 0 && (
                    <select className="select w-auto" value={journey} onChange={(e) => setJourney(e.target.value)} aria-label="Journey">
                      {v.active_journeys.map((j: string) => <option key={j}>{j}</option>)}
                    </select>
                  )}
                  <Btn onClick={attach} disabled={!journey || add.pending}>Add to journey</Btn>
                  {sales && journey && <Link className="btn btn-alt" href={`/dealer/advisor/${encodeURIComponent(journey)}`}>Open in Advisor</Link>}
                </div>
              )}
            </Card>
          </div>
        </MarketProvider>
      ) : <Card><div className="dim text-sm">{val.pending ? "Valuing…" : fromJourney ? "No vehicle on file for this journey's customer — enter a VIN to value a trade." : <>Enter a VIN from the service drive or an owner&apos;s vehicle to value the trade.</>}</div></Card>}
    </div>
  );
}

function ProspectsTab() {
  const q = useApi<any>("/trade-in/prospects");
  return (
    <Card>
      <Async q={q}>{(d) => {
        const rows = d.prospects || d.items || [];
        return (
          <DataTable rows={rows} rowKey={(p: any, i) => `${p.customer_id}-${i}`} filterText={(p: any) => `${p.name || ""} ${p.customer_id} ${(p.reasons || []).join(" ")}`}
            initialSort={{ key: "score", dir: "desc" }} empty="No upgrade prospects right now."
            columns={[
              { key: "customer", header: "Customer", sort: (p: any) => p.name || p.customer_id, render: (p: any) => <Link className="hover:underline" href={`/dealer/customers/${encodeURIComponent(p.customer_id)}`}>{p.name || p.customer_id}</Link> },
              { key: "vehicle", header: "Vehicle", render: (p: any) => p.vehicle ? `${p.vehicle.year || ""} ${p.vehicle.make || ""} ${p.vehicle.model || ""}` : (p.vin || "—") },
              { key: "reasons", header: "Why", render: (p: any) => <div className="flex flex-wrap gap-1">{(p.reasons || p.triggers || []).map((r: string) => <Badge key={r} tone="blue">{r}</Badge>)}</div> },
              { key: "equity", header: "Equity", num: true, sort: (p: any) => p.equity, render: (p: any) => <MarketProvider marketId={p.currency === "EUR" ? "de" : "us"}><Money value={p.equity} /></MarketProvider> },
              { key: "score", header: "Score", num: true, sort: (p: any) => p.score, render: (p: any) => p.score != null ? <div className="flex items-center gap-2 justify-end"><ProgressBar value={p.score > 1 ? p.score : p.score * 100} />{Math.round(p.score > 1 ? p.score : p.score * 100)}</div> : "—" },
            ]} />
        );
      }}</Async>
    </Card>
  );
}

export default function TradeInPage() {
  const [tab, setTab] = useState<"value" | "prospects">("value");
  return (
    <Page>
      <ViewHeader title="Trade-In & Upgrade" subtitle="The Trade-In Agent values the trade, computes equity and payment delta, and finds owners ready to upgrade." />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "value", label: "Valuation" }, { id: "prospects", label: "Upgrade prospects" }]} />
      {tab === "value" ? <ValuationTab /> : <ProspectsTab />}
    </Page>
  );
}
