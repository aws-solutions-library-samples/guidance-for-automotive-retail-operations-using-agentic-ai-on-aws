"use client";

// F&I deal worksheet (§5.13) on the Advisor three-column layout:
//   left   — deal queue with readiness badges
//   center — worksheet: vehicle, price, trade equity, incentives, lender offers
//            side-by-side, term/rate/payment scenarios, F&I menu with AI attach
//   right  — compliance (TILA/Reg Z + state rules + the OFAC / Red Flags /
//            adverse-action gate), actions, documents, deal history
// Bottom: AI Log. Every number comes from the API with its source_ref.

import { humanizeCode } from "@/lib/text";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { AgentActionsProvider } from "@/components/AgentBar";
import {
  AiLog, Async, Badge, Btn, CrossSellRow, ErrorState, IncentiveRow, InfoRow, JourneyActivityFeed,
  LoadingBlock, MarketProvider, Money, Panel, RecCard, SectionLabel, SourceRef, Stepper, ThreeColumn, cx, useToast,
  vehicleLabel, stageLabel } from "@/components/app";
import { ComplianceGate, CustomerMenu, ReadinessBadges } from "@/app/dealer/fni/parts";
import { useApi, useMutation, useRouteId } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";

const ADVANCE_LABEL: Record<string, string> = {
  FINANCE: "Finance complete → Trade-in", TRADE_IN: "Trade recorded → Agree", AGREE: "Close AGREE → Delivery",
};
const WRITE_ROLES = ["dealer_fni", "dealer_manager"];

export default function FniDealView() {
  const id = useRouteId(2);
  const router = useRouter();
  const toast = useToast();
  const { persona } = usePersona();
  const canWrite = WRITE_ROLES.includes(persona?.role || "");
  const ws = useApi<any>(id ? `/fni/deals/${encodeURIComponent(id)}` : null);
  const queue = useApi<any>("/fni/queue", { pollMs: 60000 });
  const docs = useApi<any>(ws.data?.deal_id ? `/documents?deal_id=${encodeURIComponent(ws.data.deal_id)}` : null);
  const post = useMutation();
  const patch = useMutation("PATCH");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [present, setPresent] = useState(false);
  const [down, setDown] = useState<string>("");

  const d = ws.data;
  useEffect(() => {
    if (d?.menu) setSelected(new Set(d.menu.filter((m: any) => m.attached).map((m: any) => m.code)));
    if (d?.price?.down_payment != null) setDown(String(d.price.down_payment));
  }, [d]);
  const menuDirty = useMemo(() => {
    if (!d?.menu) return false;
    const now = new Set(d.menu.filter((m: any) => m.attached).map((m: any) => m.code));
    return now.size !== selected.size || Array.from(selected).some((c) => !now.has(c));
  }, [d, selected]);

  const act = async (label: string, fn: () => Promise<any>, after?: (r: any) => void) => {
    const r = await fn();
    if (r) { toast(label, "success"); after?.(r); ws.reload(); queue.reload(); docs.reload(); }
    return r;
  };
  const err = (m: { error: string | null }) => m.error && toast(m.error, "error");
  // a journey in the queue with no deal yet: open the desk on it (deal from its saved build)
  const startDesking = async (journeyId: string) => {
    const r = await post.run(`/fni/journeys/${encodeURIComponent(journeyId)}/deal`);
    if (r?.deal?.deal_id) { toast(r.created ? "Desk opened from the saved build" : "Opened the existing deal", "success"); router.push(`/dealer/fni/${encodeURIComponent(r.deal.deal_id)}`); }
    else err(post);
  };

  if (!id) return <div className="page"><LoadingBlock /></div>;
  if (ws.error) return <div className="page"><ErrorState error={ws.error} onRetry={ws.reload} /></div>;
  if (!d) return <div className="page"><LoadingBlock rows={8} /></div>;

  const gate = d.compliance?.gate;
  const dealId = encodeURIComponent(d.deal_id);
  const booked = ["pushed", "booked"].includes(d.status);
  const toggle = (code: string) => setSelected((s) => { const n = new Set(s); n.has(code) ? n.delete(code) : n.add(code); return n; });

  const saveMenu = () => act("F&I menu saved", async () => {
    const r = await post.run(`/fni/deals/${dealId}/menu`, { accepted: Array.from(selected) }); err(post); return r;
  });
  const sendMenu = () => act("Menu sent to the customer's My Garage", async () => {
    if (menuDirty) { const m = await post.run(`/fni/deals/${dealId}/menu`, { accepted: Array.from(selected) }); if (!m) { err(post); return null; } }
    const r = await post.run(`/fni/deals/${dealId}/documents`, { kind: "fi_menu", share: true }); err(post); return r;
  }, () => setPresent(false));

  // The F&I desk agents live in the global AgentBar (§1.6); Run-now reuses the
  // worksheet's own endpoints.
  return (
    <MarketProvider marketId={d.market_id}>
      <AgentActionsProvider value={{
        journeyId: d.journey_id,
        actions: canWrite && !booked ? {
          "a3.deal_structurer": { label: "Rebuild scenarios", run: async () => { ws.reload(); } },
          "a3.lender_chaser": {
            label: d.lender_offers?.length ? "Re-request decisions" : "Request decision",
            run: () => act("Credit application submitted to the lender panel", async () => {
              const r = await post.run(`/fni/deals/${dealId}/lender-decision`); err(post); return r;
            }),
          },
        } : undefined,
      }}>
      <div className="flex flex-col min-h-0 flex-1">
        <div className="top-bar">
          <div className="session-info">
            <span className="live-dot" aria-hidden />
            <span><strong>{d.customer_name || d.customer_id}</strong> · deal <span className="mono">{d.deal_id}</span></span>
            <Badge tone={booked ? "green" : "blue"}>{humanizeCode(d.status)}</Badge>
            {d.dms?.dms_deal_number && <Badge tone="purple">DMS {d.dms.dms_deal_number}</Badge>}
          </div>
          <div className="flex items-center gap-2">
            {d.journey_id && <Link className="btn btn-alt btn-sm" href={`/dealer/journeys?focus=${encodeURIComponent(d.journey_id)}`}>Journey</Link>}
            <Btn kind="alt" size="sm" onClick={() => setPresent(true)}>Present menu to customer</Btn>
          </div>
        </div>
        <div className="px-6 py-2" style={{ background: "var(--bg-dark)", borderBottom: "1px solid var(--border)" }}>
          <Stepper current={d.stage} blocked={d.stage === "AGREE" && !gate?.ok} />
        </div>

        <ThreeColumn
          left={
            <Panel title="Deal queue" badge="Live" badgeKind="live">
              <Async q={queue}>
                {(qd: any) => (
                  <div className="flex flex-col gap-2" role="list">
                    {qd.queue.map((r: any) => {
                      const cur = r.deal_id === d.deal_id;
                      return (
                        <button key={r.journey_id} role="listitem" aria-current={cur ? "true" : undefined}
                                onClick={() => r.deal_id ? router.push(`/dealer/fni/${encodeURIComponent(r.deal_id)}`) : startDesking(r.journey_id)}
                                className={cx("kanban-card", cur && "ring-1")} style={cur ? { borderColor: "var(--border-active)" } : undefined}>
                          <div className="flex justify-between gap-2"><b className="truncate">{r.customer_name || r.customer_id}</b><Badge tone="gray">{stageLabel(r.stage)}</Badge></div>
                          <div className="dim text-xs mt-1 flex justify-between"><span>{r.model_name || r.model_id || "—"}</span><span>{r.deal_id ? <Money value={r.price_total} /> : <Badge tone="orange">not desked — click to desk</Badge>}</span></div>
                          <div className="mt-2"><ReadinessBadges readiness={r.readiness} compact /></div>
                        </button>
                      );
                    })}
                    {qd.queue.length === 0 && <div className="dim text-sm">Queue is empty.</div>}
                  </div>
                )}
              </Async>
            </Panel>
          }
          center={
            <Panel title="Deal worksheet" badge="AI-assisted" badgeKind="ai">
              <RecCard icon="🚗" title="Vehicle" right={<SourceRef refId={d.vehicle?.source_ref} />}>
                <InfoRow label="Model" value={vehicleLabel(d.vehicle)} />
                <InfoRow label="Trim" value={d.vehicle?.trim_name || "—"} />
                <InfoRow label="VIN" value={d.vehicle?.vin ? <span className="mono">{d.vehicle.vin}{d.vehicle.stock_number ? <span className="dim"> · stock {d.vehicle.stock_number}</span> : null}</span>
                  : <span><Link className="underline" href={`/dealer/inventory?model=${encodeURIComponent(d.vehicle?.model_id || "")}&journey=${encodeURIComponent(d.journey_id || "")}`}>Reserve a VIN</Link> <span className="text-warning text-xs">· required before the DMS push and delivery</span></span>} />
                <InfoRow label="Vehicle price" value={<Money value={d.vehicle?.price_vehicle} />} />
                <InfoRow label="Est. tax" value={<Money value={d.vehicle?.price_tax_est} />} />
              </RecCard>

              <div className="grid gap-4 md:grid-cols-2">
                <RecCard icon="💵" title="Price & payment" right={<SourceRef refId={d.price?.source_ref} />}>
                  {d.price?.breakdown?.vehicle_price != null && (
                    <div className="text-xs muted mb-1" title={d.price.breakdown.formula}>
                      <Money value={d.price.breakdown.vehicle_price} /> vehicle + <Money value={d.price.breakdown.tax_est} /> est. tax
                      {d.price.breakdown.doc_fee ? <> + <Money value={d.price.breakdown.doc_fee} /> doc fee</> : null}
                      {d.price.breakdown.incentives_total ? <> − <Money value={d.price.breakdown.incentives_total} /> incentives</> : null}
                      {" = "}<Money value={d.price.breakdown.computed_total} />
                      {!d.price.breakdown.reconciles && <span className="text-warning"> · does not match the deal total below — re-desk from the build</span>}
                    </div>
                  )}
                  <InfoRow label="Deal total" value={<Money value={d.price?.price_total} />} />
                  <InfoRow label="APR / term" value={<span>{d.price?.apr_pct ?? "—"}% · {d.price?.term_months ?? "—"} mo
                    {d.price?.apr_program ? <Badge tone="green">OEM APR special</Badge> : d.price?.apr_basis?.includes("estimate") ? <span className="dim text-xs"> · {d.price.credit_tier} estimate, not a credit decision</span> : null}</span>} />
                  {d.price?.amount_financed != null && (
                    <InfoRow label="Amount financed" value={<span><Money value={d.price.amount_financed} />
                      {d.price.trade_equity_applied ? <span className="dim text-xs"> · net of <Money value={d.price.trade_equity_applied} /> trade equity</span> : null}</span>} />
                  )}
                  <InfoRow label="Monthly" value={<b><Money value={d.price?.monthly_payment} decimals={2} /></b>} />
                  {d.price?.budget && (
                    <div className={d.price.budget.fits ? "dim text-xs" : "text-warning text-xs"} role={d.price.budget.fits ? undefined : "status"}>
                      {d.price.budget.fits
                        ? <>Within her budget of <Money value={d.price.budget.monthly_max} />/mo</>
                        : <>Over her budget of <Money value={d.price.budget.monthly_max} />/mo by <Money value={d.price.budget.over_by} decimals={2} /> — adjust term or down payment before presenting</>}
                    </div>
                  )}
                  <InfoRow label="F&I products" value={<Money value={d.price?.fi_products_total} />} />
                  {canWrite && !booked && (
                    <form className="flex gap-2 mt-3 items-end" onSubmit={(e) => { e.preventDefault();
                      act("Down payment updated", async () => { const r = await patch.run(`/fni/deals/${dealId}`, { down_payment: Number(down) }); err(patch); return r; }); }}>
                      <label className="field m-0 flex-1"><span className="text-xs muted">Down payment</span>
                        <input className="input" inputMode="decimal" value={down} onChange={(e) => setDown(e.target.value)} aria-label="Down payment" /></label>
                      <Btn kind="alt" size="sm" type="submit" disabled={patch.pending}>Apply</Btn>
                    </form>
                  )}
                </RecCard>
                <RecCard icon="⇄" title="Trade-in" right={d.trade && <SourceRef refId={d.trade.source_ref} />}>
                  {d.trade ? (
                    <>
                      <InfoRow label="Vehicle" value={d.trade.vehicle} />
                      <InfoRow label="Trade value" value={<Money value={d.trade.trade_value} />} />
                      <InfoRow label="Payoff" value={<Money value={d.trade.payoff} />} />
                      <InfoRow label="Equity" value={<span className={(d.trade.equity ?? 0) >= 0 ? "text-success" : "text-danger"}><Money value={d.trade.equity} /></span>} />
                    </>
                  ) : <div className="dim text-sm">No trade on this deal.</div>}
                </RecCard>
              </div>

              {d.incentives?.length > 0 && (
                <RecCard icon="🏷" title="Incentives applied">
                  <div className="flex flex-col gap-1">
                    {d.incentives.map((i: any) => (
                      <IncentiveRow key={i.incentive_id} eligible={i.status !== "expired" && (i.kind !== "apr" || i.applies)} name={i.name}
                                    amount={i.kind === "apr"
                                      ? <>{i.applied_to_deal ? `${i.apr_pct}% APR applied` : i.applies ? `${i.apr_pct}% APR available` : "not applied"} · {i.note}</>
                                      : i.kind === "informational" ? <><Money value={i.amount} /> · {i.note}</>
                                      : <><Money value={i.amount} /> off the price · {i.status || "active"}</>}
                                    right={<SourceRef refId={i.incentive_id} />} />
                    ))}
                  </div>
                </RecCard>
              )}

              <RecCard icon="🏦" title="Lender offers" right={canWrite && !booked && (
                <Btn kind="present" disabled={post.pending}
                     onClick={() => act("Credit application submitted to the lender panel", async () => { const r = await post.run(`/fni/deals/${dealId}/lender-decision`); err(post); return r; })}>
                  {d.lender_offers?.length ? "Re-request decisions" : "Request lender decision"}
                </Btn>)}>
                {d.lender_offers?.length ? (
                  <div className="grid gap-2 md:grid-cols-3" role="list">
                    {d.lender_offers.map((o: any) => (
                      <div key={o.offer_id} role="listitem" className={cx("compete-card", o.selected && "ring-1")} style={o.selected ? { borderColor: "var(--success)" } : undefined}>
                        <div className="compete-header">
                          <span className="compete-name">{o.lender}</span>
                          <Badge tone={o.decision === "declined" ? "red" : o.decision === "conditioned" ? "orange" : "green"}>{humanizeCode(o.decision)}</Badge>
                        </div>
                        <div className="text-xl font-bold">{o.monthly_payment != null ? <Money value={o.monthly_payment} decimals={2} /> : "—"}</div>
                        <div className="dim text-xs">{o.apr_pct ?? "—"}% APR · {o.term_months} mo · financed <Money value={o.amount_financed} /></div>
                        {o.stipulations?.length > 0 && <div className="text-xs mt-1 text-warning">Stips: {o.stipulations.join(", ")}</div>}
                        {canWrite && !booked && o.decision !== "declined" && (
                          <Btn kind={o.selected ? "success" : "alt"} size="sm" className="mt-2 w-full" disabled={o.selected || patch.pending}
                               onClick={() => act(`${o.lender} selected`, async () => { const r = await patch.run(`/fni/deals/${dealId}`, { selected_offer_id: o.offer_id }); err(patch); return r; })}>
                            {o.selected ? "✓ Selected" : "Select"}
                          </Btn>
                        )}
                        <div className="mt-1"><SourceRef refId={o.source_ref} /></div>
                      </div>
                    ))}
                  </div>
                ) : <div className="dim text-sm">No credit application submitted yet.</div>}
              </RecCard>

              <RecCard icon="📅" title="Term / rate / payment scenarios">
                <table className="compare-table">
                  <thead><tr><th scope="col">Term</th><th scope="col">APR</th><th scope="col">Financed</th><th scope="col">Monthly</th><th scope="col"></th></tr></thead>
                  <tbody>
                    {d.scenarios.map((s: any) => (
                      <tr key={s.term_months}>
                        <td>{s.term_months} mo</td><td>{s.apr_pct}%</td><td><Money value={s.amount_financed} /></td>
                        <td className={s.selected ? "col-ev" : ""}><Money value={s.monthly_payment} decimals={2} />{s.selected && <span className="compare-winner">CURRENT</span>}</td>
                        <td>{canWrite && !booked && !s.selected && (
                          <Btn kind="alt" size="sm" disabled={patch.pending}
                               onClick={() => act(`Term set to ${s.term_months} months`, async () => { const r = await patch.run(`/fni/deals/${dealId}`, { term_months: s.term_months }); err(patch); return r; })}>Use</Btn>)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div className="dim text-[.7rem] mt-2"><SourceRef refId={d.scenarios[0]?.source_ref} /></div>
              </RecCard>

              <RecCard icon="🛡" title="F&I protection menu" right={
                <div className="flex gap-2">
                  {canWrite && !booked && <Btn kind="present" disabled={!menuDirty || post.pending} onClick={saveMenu}>Save menu</Btn>}
                </div>}>
                {d.menu.map((m: any) => (
                  <CrossSellRow key={m.code}
                    name={<label className="toggle"><input type="checkbox" checked={selected.has(m.code)} disabled={!canWrite || booked}
                                                           onChange={() => toggle(m.code)} aria-label={`Include ${m.name}`} /> {m.name}</label>}
                    detail={<><Money value={m.price} />{m.monthly_delta != null && <> · +<Money value={m.monthly_delta} decimals={2} />/mo</>}{m.ai_attach?.why ? ` · ${m.ai_attach.why}` : ""}</>}
                    probability={m.ai_attach?.likelihood ?? undefined}
                  />
                ))}
                <div className="dim text-[.7rem] mt-1">AI attach likelihood from the Sales Agent (get_cross_sell; deal context: financed, term).</div>
              </RecCard>
            </Panel>
          }
          right={
            <Panel title="Compliance & actions" badge={gate?.ok ? "Clear" : "Gate blocked"} badgeKind={gate?.ok ? "live" : "danger"} ariaLive>
              <SectionLabel>Credit-compliance gate</SectionLabel>
              <ComplianceGate gate={gate} />
              {gate?.checks?.adverse_action === "required" && canWrite && (
                <Btn kind="danger" size="sm" disabled={post.pending}
                     onClick={() => act("Adverse-action notice sent and shared", async () => { const r = await post.run(`/fni/deals/${dealId}/adverse-action`); err(post); return r; })}>
                  Send adverse-action notice
                </Btn>
              )}

              <SectionLabel>Disclosures</SectionLabel>
              {d.compliance?.disclosures?.items?.map((i: any) => <IncentiveRow key={i.name} eligible name={i.name} amount={<SourceRef refId={i.source_ref} />} />)}
              {d.compliance?.disclosures?.state && (
                <div>
                  <InfoRow label={`${d.compliance.disclosures.state.region_name} sales tax`} value={`${d.compliance.disclosures.state.sales_tax_pct ?? "—"}%`} />
                  <InfoRow label="Doc-fee cap" value={d.compliance.disclosures.state.doc_fee_cap != null ? <Money value={d.compliance.disclosures.state.doc_fee_cap} /> : "none"} />
                  {d.compliance.disclosures.state.note && <div className="dim text-xs mt-1">{d.compliance.disclosures.state.note}</div>}
                </div>
              )}
              {d.compliance?.disclosures?.warnings?.map((w: string) => <div key={w} className="error-box">{w}</div>)}

              {canWrite && (
                <>
                  <SectionLabel>Actions</SectionLabel>
                  <div className="flex flex-col gap-2">
                    <Btn kind="use" disabled={booked || !gate?.ok || !d.vehicle?.vin || post.pending}
                         title={!gate?.ok ? `Blocked: ${gate?.missing?.join(", ")}` : !d.vehicle?.vin ? "Blocked: reserve a VIN for this journey first" : undefined}
                         onClick={() => act("Deal pushed to the DMS and booked", async () => { const r = await post.run(`/fni/deals/${dealId}/push`); err(post); return r; })}>
                      {booked ? "✓ Booked in DMS" : "Push to DMS"}
                    </Btn>
                    {!booked && !d.vehicle?.vin && <div className="dim text-[.7rem]">No VIN reserved — the deal cannot book or go to delivery without the unit it is written against.</div>}
                    {ADVANCE_LABEL[d.stage] && (
                      <Btn kind="alt" disabled={post.pending}
                           onClick={() => act("Journey advanced", async () => {
                             const r = await post.run(`/fni/journeys/${encodeURIComponent(d.journey_id)}/advance`);
                             err(post); return r;
                           }, (r) => r?.handoff && toast(`Handed to delivery${r.handoff.to_user_name ? ` (${r.handoff.to_user_name})` : ""}`, "info"))}>
                        {ADVANCE_LABEL[d.stage]}
                      </Btn>
                    )}
                    <Btn kind="alt" disabled={post.pending}
                         onClick={() => act("Deal worksheet sent to My Garage", async () => { const r = await post.run(`/fni/deals/${dealId}/documents`, { kind: "buyers_order", share: true }); err(post); return r; })}>
                      Send buyer&apos;s order to My Garage
                    </Btn>
                  </div>
                </>
              )}

              <SectionLabel>Documents</SectionLabel>
              <Async q={docs} rows={2}>
                {(dd: any) => dd.documents.length ? dd.documents.map((x: any) => (
                  <div key={x.document_id} className="info-row">
                    <span className="info-label">{x.title} <span className="dim">v{x.version}</span></span>
                    <span className="info-value">{(x.shared_with || []).includes("customer") ? <Badge tone="green">in My Garage</Badge> : <Badge tone="gray">internal</Badge>}</span>
                  </div>
                )) : <div className="dim text-sm">No documents yet.</div>}
              </Async>

              <SectionLabel>Deal history</SectionLabel>
              <JourneyActivityFeed journeyId={d.journey_id} limit={12} />
            </Panel>
          }
        />
        <AiLog journeyId={d.journey_id} />
      </div>
      </AgentActionsProvider>
      {present && <CustomerMenu ws={d} selected={selected} onToggle={toggle} onClose={() => setPresent(false)}
                                onSend={sendMenu} sending={post.pending} />}
    </MarketProvider>
  );
}
