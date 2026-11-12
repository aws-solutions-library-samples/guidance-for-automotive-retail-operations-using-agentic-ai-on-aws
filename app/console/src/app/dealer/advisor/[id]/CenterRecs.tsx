"use client";
import Link from "next/link";

// Advisor CENTER — AI Recommendations (§5.3): vehicle matches with Present,
// EV vs ICE compare-table (A1 build_tco_comparison), why-EV benefits + savings
// banner, talk tracks, cross-sell, Configuration / Finance / Trade-In cards.

import { useState } from "react";
import {
  BenefitItem, Btn, CompareTable, Confidence, CrossSellRow, Distance, Efficiency, InfoRow, Money, Panel, PtChip,
  RecCard, SavingsBanner, SourceRef, TalkTrack, VehicleItem, VTag, useToast,
} from "@/components/app";
import { useMutation } from "@/lib/hooks";
import { useCanOpen } from "@/lib/persona";

function TcoValue({ row, side }: { row: any; side: "ev" | "ice" }) {
  const v = row[side];
  if (v == null) return <>—</>;
  if (row.kind === "money") return <Money value={v} />;
  if (row.kind === "money_precise") return <Money value={v} decimals={3} />;
  if (row.kind === "distance") return <Distance value={v} />;
  if (row.kind === "efficiency") return <Efficiency value={v} powertrain={side === "ev" ? "ev" : "ice"} />;
  return <>{String(v)}</>;
}

function ErrorNote({ x }: { x: any }) {
  return x?.error ? <div className="dim text-xs">Unavailable: {x.error}</div> : null;
}

const BREAKDOWN_LABEL: Record<string, string> = { budget: "budget fit", powertrain: "powertrain", seats: "seats", availability: "in stock", asked_for: "asked for" };

const COMPLIANCE_LABEL: Record<string, string> = { ofac: "OFAC screening", red_flags: "Red Flags identity check", adverse_action: "adverse-action notice", credit_compliance: "credit compliance" };

export function CenterRecs({ a, journeyId, readOnly, onChanged }: {
  a: any; journeyId: string; readOnly: boolean; onChanged: () => void }) {
  const toast = useToast();
  const can = useCanOpen();
  const present = useMutation();
  const [presented, setPresented] = useState<Record<string, boolean>>({});
  const matches: any[] = a.matches?.matches || [];
  const tco = a.tco;
  const sug: any[] = a.suggestions?.suggestions || [];
  const cross: any[] = a.cross_sell?.recommendations || [];
  const deal = a.deal || {};
  const eq = a.equity || {};
  const pd = a.payment_delta || {};
  const inv = a.inventory || {};

  const doPresent = async (m: any) => {
    const vin = (inv.exact || inv.near_matches || []).find((v: any) => v.trim_id === m.trim_id)?.vin;
    const out = await present.run(`/journeys/${encodeURIComponent(journeyId)}/present`, {
      model_id: m.model_id, trim_id: m.trim_id, vin, recommendation_id: a.recommendation_ids?.[`match:${m.trim_id}`] });
    if (out) { setPresented((p) => ({ ...p, [m.trim_id]: true })); toast(`Presented ${m.trim_name || m.trim_id}`, "success"); onChanged(); }
    else toast(present.error || "Could not record", "error");
  };

  return (
    <Panel title="AI Recommendations" badge="AI powered" badgeKind="ai" className="center-panel">
      <RecCard icon="🚗" title="Vehicle matches · EV & ICE" right={matches[0] && <Confidence>{matches[0].score}% fit</Confidence>}>
        <ErrorNote x={a.matches} />
        {a.matches?.requested_model_name && (
          <div className="muted text-xs px-3 pb-1">Customer asked about the <b>{a.matches.requested_model_name}</b>{" (from the lead)"} — ranked first when it fits.</div>
        )}
        {matches.length === 0 && !a.matches?.error && <div className="dim text-sm">No catalog trims fit the stated budget yet.</div>}
        {matches.map((m, i) => (
          <VehicleItem key={m.trim_id} rank={i + 1}
            name={<>{m.trim_name || m.trim_id} <PtChip powertrain={m.powertrain} /></>}
            specs={<><Money value={m.list_price} />{m.ev_range ? <> · <Distance value={m.ev_range} /> range</> : null}{m.seats ? ` · ${m.seats} seats` : ""}</>}
            tags={<>
              {m.in_stock_count > 0 ? <VTag>{m.in_stock_count} in stock</VTag> : <VTag tone="yellow">order</VTag>}
              {m.requested && <VTag>asked for</VTag>}
              {Object.entries(m.score_breakdown || {}).filter(([k, v]) => k !== "asked_for" && (v as number) >= 15).map(([k]) => <VTag key={k} tone="blue">{BREAKDOWN_LABEL[k] || k}</VTag>)}
            </>}
            action={!readOnly && (
              <Btn kind="present" used={presented[m.trim_id]} disabled={presented[m.trim_id] || present.pending} onClick={() => doPresent(m)}>
                {presented[m.trim_id] ? "✓ Presented" : "Present"}
              </Btn>)} />
        ))}
        {matches[0]?.rationale && <div className="muted text-xs mt-2 px-3">{matches[0].rationale}</div>}
      </RecCard>

      <RecCard icon="⚖" title={tco && !tco.error ? <>EV vs ICE · {tco.years}-year side-by-side</> : "EV vs ICE"}
               right={tco?.savings && <Confidence>{tco.savings.winner === "ev" ? "EV" : "ICE"} wins</Confidence>}>
        <ErrorNote x={tco} />
        {tco && !tco.error && (
          <CompareTable aLabel={<><span className="pt-chip pt-ev">EV</span> {tco.ev.trim_name}</>}
                        bLabel={<><span className="pt-chip pt-ice">{tco.ice.powertrain}</span> {tco.ice.trim_name}</>}
            rows={tco.rows.map((r: any) => ({ label: r.label, a: <TcoValue row={r} side="ev" />, b: <TcoValue row={r} side="ice" />,
                                              winner: r.winner === "ev" ? "a" : r.winner === "ice" ? "b" : undefined }))}
            footnote={<>*Based on ~<Distance value={tco.assumptions.annual_distance} />/yr, home energy <Money value={tco.assumptions.energy_price} decimals={2} />/kWh vs fuel <Money value={tco.assumptions.fuel_price} decimals={2} /> (market defaults, editable){tco.assumptions.state ? ` · ${tco.assumptions.state} tax` : ""}. <SourceRef refId={tco.source_ref} /></>} />
        )}
      </RecCard>

      {tco && !tco.error && (
        <RecCard icon="⚡" title={`Why ${(a.customer_360?.name || "they").split(" ")[0]} should go electric`}>
          <div className="flex flex-col gap-2">
            {tco.benefits.map((b: any) => (
              <BenefitItem key={b.key} icon={b.key === "energy" ? "💸" : b.key === "incentives" ? "🏷" : "🔌"} title={b.title}>
                {b.amount != null && <strong><Money value={b.amount} decimals={b.amount_kind === "per_kwh" ? 2 : 0} /></strong>} {b.detail}
              </BenefitItem>
            ))}
            {tco.savings.total > 0 && <SavingsBanner label={`Estimated ${tco.years}-year savings`} value={<Money value={tco.savings.total} />} />}
          </div>
        </RecCard>
      )}

      <RecCard icon="💬" title="Talk tracks">
        <div className="flex flex-col gap-2">
          {sug.length === 0 && <div className="dim text-sm">No suggestions above the confidence threshold for this stage.</div>}
          {sug.map((s) => (
            <div key={s.action}>
              <TalkTrack>“{s.text}”</TalkTrack>
              <div className="dim text-[.7rem] mt-1 ml-3">because {s.why} · {Math.round(s.confidence * 100)}%</div>
            </div>
          ))}
        </div>
      </RecCard>

      <RecCard icon="💰" title="Cross-sell opportunities">
        <ErrorNote x={a.cross_sell} />
        {cross.map((x) => <CrossSellRow key={x.code} name={x.name} detail={x.why} probability={x.weight} />)}
      </RecCard>

      <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
        <RecCard icon="🛠" title="Configuration">
          {(() => {
            const cfg = a.configuration || null;
            const m0 = matches[0];
            return m0 ? (<>
              <InfoRow label="Recommended" value={m0.trim_name || m0.trim_id} />
              <InfoRow label="List price" value={<Money value={m0.list_price} />} />
              <InfoRow label="vs budget max" value={a.customer_360?.budget?.price_max ? <Money value={(a.customer_360.budget.price_max) - m0.list_price} /> : "—"} />
              {cfg && <InfoRow label="Saved build" value={cfg.config_id} />}
            </>) : <div className="dim text-sm">No build yet — use Discovery.</div>;
          })()}
        </RecCard>
        <RecCard icon="🏦" title="Finance">
          <ErrorNote x={a.payment_delta} />
          {deal.deal_totals ? (<>
            <InfoRow label="Payment" value={<><Money value={deal.deal_totals.monthly_payment} />/mo</>} />
            <InfoRow label="APR · term" value={`${deal.deal_totals.apr_pct}% · ${deal.deal_totals.term_months} mo`} />
          </>) : !pd.error && pd.new_monthly != null ? (<>
            <InfoRow label="Est. payment" value={<><Money value={pd.new_monthly} />/mo</>} />
            <InfoRow label="APR · term" value={`${pd.inputs.apr_pct}% (${pd.inputs.credit_tier}) · ${pd.inputs.term_months} mo`} />
            <div className="dim text-[.68rem] mt-1">{pd.formula} · estimate, not a credit decision</div>
          </>) : <div className="dim text-sm">No finance scenario yet.</div>}
          {deal.compliance_gate && !deal.compliance_gate.ok && <div className="dim text-[.7rem] mt-2">Compliance: {deal.compliance_gate.missing.map((m: string) => COMPLIANCE_LABEL[m] || m.replace(/_/g, " ")).join(", ")} pending</div>}
        </RecCard>
        <RecCard icon="⇄" title="Trade-in" right={can("dealer.trade_in") && <Link className="btn btn-ghost btn-sm" href={`/dealer/trade-in?journey=${encodeURIComponent(journeyId)}`}>Trade-In desk →</Link>}>
          <ErrorNote x={eq} />
          {eq.trade_value != null ? (<>
            <InfoRow label="Vehicle" value={eq.current_vehicle ? `${eq.current_vehicle.year} ${eq.current_vehicle.make} ${eq.current_vehicle.model}` : "—"} />
            <InfoRow label="Value" value={<Money value={eq.trade_value} />} />
            <InfoRow label={eq.current_vehicle?.ownership === "lease" ? "Lease buyout" : "Payoff"} value={<Money value={eq.payoff} />} />
            <InfoRow label="Equity" value={<span style={{ color: eq.equity >= 0 ? "var(--success)" : "var(--danger)" }}><Money value={eq.equity} /></span>} />
            <div className="mt-1"><SourceRef refId={eq.source_refs?.equity} /></div>
          </>) : !eq.error && <div className="dim text-sm">{eq.current_vehicle ? <>{eq.current_vehicle.year} {eq.current_vehicle.make} {eq.current_vehicle.model} on file — not appraised yet.</> : "No trade vehicle on file."}</div>}
        </RecCard>
      </div>
    </Panel>
  );
}
