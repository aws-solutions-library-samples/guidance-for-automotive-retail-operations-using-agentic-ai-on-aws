"use client";

// Estimate & Approval (§5.20.4, design doc §3.1/§1.4) — the Advisor screen
// one-for-one. Left: customer-card + vehicle/RO info rows + repair-vs-replace
// card (equity flag → handoff to sales). Center: one rec-card per line
// (urgency, evidence, coverage incentive-row with warranty/recall/customer-pay
// badge, labor/parts SourceRefs — the UI never computes a price),
// explain_finding plain language, good-better-best compare-table, coverage
// savings banner, Present (magic link — mirror of the My Garage approval,
// same rec-card/incentive-row primitives) + customer-facing mode. Right:
// pushback talk track, declined/due add-ons, Next Best Action. Declined lines
// land in DEALER_DeclinedWork with a follow-up cadence. AI Log at bottom
// (via ServiceFrame).

import { useState } from "react";
import {
  AiResponseCard, Badge, Btn, CompareTable, Confidence, CrossSellRow, CustomerCard, DateTime, Distance, EmptyState,
  IncentiveRow, InfoRow, Modal, Money, ObjectionAlert, Panel, RecCard, SavingsBanner, SourceRef, StatusChip,
  ThreeColumn, useToast, Async, cx, vehicleLabel,
} from "@/components/app";
import { useApi, useMutation, useRouteId } from "@/lib/hooks";
import { ServiceFrame, useServiceJourney } from "../../common";

export default function EstimateView() {
  const id = useRouteId(2);
  const q = useServiceJourney(id);
  return <ServiceFrame id={id} q={q}>{(sj) => <EstimateBody sj={sj} reloadSj={q.reload} />}</ServiceFrame>;
}

const URGENCY_TONE: Record<string, "danger" | "warn" | undefined> = { safety: "danger", soon: "warn" };

function coverageTone(payType?: string): "green" | "blue" | "gray" {
  return payType === "warranty" ? "green" : payType === "recall" ? "blue" : "gray";
}

function EstimateBody({ sj, reloadSj }: { sj: any; reloadSj: () => void }) {
  const toast = useToast();
  const estId = sj.estimate?.estimate_id;
  const [explain, setExplain] = useState(false);
  const eq = useApi<any>(estId ? `/service/estimates/${encodeURIComponent(estId)}${explain ? "?explain=1" : ""}` : null,
                         { pollMs: sj.estimate?.status === "presented" ? 5000 : 0 });
  const post = useMutation();
  const patch = useMutation("PATCH");
  const [verbal, setVerbal] = useState<{ line: any; decision: "approve" | "decline" } | null>(null);
  const [reason, setReason] = useState("");
  const [customerMode, setCustomerMode] = useState(false);
  const [presented, setPresented] = useState<any>(null);
  const [edit, setEdit] = useState<Record<string, any>>({});
  const [utt, setUtt] = useState("");
  const [push, setPush] = useState<any>(null);
  const [cadence, setCadence] = useState<Record<string, number>>({});
  const [localOrder, setLocalOrder] = useState(false);

  if (!estId) {
    return <div className="page"><EmptyState>No estimate yet. The technician submits the inspection to draft one.</EmptyState></div>;
  }
  const e = eq.data;
  const done = (msg: string, ok: any, err: string | null) => { if (ok) { toast(msg, "success"); eq.reload(); reloadSj(); } else toast(err || "Action failed", "error"); };

  const present = async () => {
    const out = await post.run(`/service/estimates/${encodeURIComponent(estId)}/present`, { channel: "magic_link" });
    if (out) setPresented(out);
    done(out?.approval_link ? "Estimate presented — approval link sent" : "Estimate presented (no customer link on file)", out, post.error);
  };
  const decide = async () => {
    if (!verbal) return;
    const out = await post.run(`/service/estimates/${encodeURIComponent(estId)}/decisions`, {
      decisions: [{ line_id: verbal.line.line_id, decision: verbal.decision, reason }], advance: true });
    done(`${verbal.line.item}: ${verbal.decision}d (recorded by advisor)`, out, post.error);
    setVerbal(null); setReason("");
  };
  const reprice = async (line: any) => {
    const adj = edit[line.finding_ref];
    if (!adj) return;
    const out = await patch.run(`/service/estimates/${encodeURIComponent(estId)}`, { adjustments: { [line.finding_ref]: adj } });
    done(`Re-priced ${line.item}`, out, patch.error);
    setEdit((x) => { const n = { ...x }; delete n[line.finding_ref]; return n; });
  };
  const pushback = async () => {
    const out = await post.run(`/service/estimates/${encodeURIComponent(estId)}/pushback`, { utterance: utt });
    if (out) setPush(out); else toast(post.error || "Could not classify", "error");
  };
  const setDeclinedCadence = async (line: any, days: number) => {
    setCadence((c) => ({ ...c, [line.line_id]: days }));
    const out = await patch.run(`/service/declined/${encodeURIComponent(`DW-${line.line_id}`)}`, { cadence_days: days });
    done(`Follow-up for ${line.item} in ${days} days`, out, patch.error);
  };
  // §5.20.4 "reorder by urgency (prioritize_findings)". Try the A7 tool; if the
  // route isn't deployed, fall back to a display-only sort by the urgency each
  // line already carries (no prices touched).
  const prioritize = async () => {
    const out = await post.run(`/service/estimates/${encodeURIComponent(estId)}/prioritize`, {});
    if (out) { setLocalOrder(false); toast("Lines reordered by the Service Agent (prioritize findings)", "success"); eq.reload(); }
    else { setLocalOrder(true); toast("Prioritize route unavailable — ordered by each line's recorded urgency (display only)", "info"); }
  };
  const URGENCY_RANK: Record<string, number> = { safety: 0, now: 0, soon: 1, monitor: 2 };
  const orderedLines = (lines: any[]) => localOrder
    ? [...lines].sort((a, b) => (URGENCY_RANK[a.urgency] ?? 3) - (URGENCY_RANK[b.urgency] ?? 3))
    : lines;

  const handToSales = async () => {
    const out = await post.run(`/service/${encodeURIComponent(sj.service_journey_id)}/handoff-to-sales`,
      { reason: `repair-vs-replace: estimate ${e?.repair_vs_replace?.pct_of_value}% of vehicle value` });
    done(out ? `Handed to sales — journey ${out.new_journey_id}` : "", out, post.error);
  };

  const editable = e && ["pending", "presented"].includes(e.status) && !e.lines?.some((l: any) => l.decision);
  const b = e?.bundles;
  const rvr = e?.repair_vs_replace;
  const waiting = e?.status === "presented";

  return (
    <ThreeColumn
      left={
        <Panel title="Customer & RO" badge={e?.status || "…"} badgeKind={waiting ? "active" : "live"}>
          <CustomerCard name={sj.customer?.name || "Customer"} meta={<>{sj.customer?.phone}<br />{sj.customer?.email}</>}
                        score={sj.customer?.approval_preference ? `approval by ${sj.customer.approval_preference.replace("_", " ")}` : undefined} />
          <div>
            <InfoRow label="RO" value={sj.repair_order?.ro_number} />
            <InfoRow label="Vehicle" value={vehicleLabel(sj.vehicle)} />
            <InfoRow label="Mileage" value={<Distance value={sj.vehicle?.mileage_in ?? sj.vehicle?.odometer} />} />
            <InfoRow label="Estimate no." value={<span className="mono">{String(estId).replace(/^EST-[A-Z]{2}-/, "")}</span>} />
            <InfoRow label="Presented" value={e?.presented_at ? <DateTime value={e.presented_at} /> : "not yet"} />
            {e?.minutes_waiting != null && <InfoRow label="Waiting" value={<span style={{ color: e.minutes_waiting > 20 ? "var(--danger)" : undefined }}>{e.minutes_waiting} min</span>} />}
          </div>
          {rvr && (
            <div className={cx(rvr.triggered ? "objection-alert" : "insight-box")}>
              <div className={rvr.triggered ? "objection-label" : "insight-label"}>Repair vs replace</div>
              <div className="text-sm">Estimate is <b>{rvr.pct_of_value}%</b> of the vehicle&apos;s value (<Money value={rvr.vehicle_value} />; equity <Money value={rvr.equity} />). Threshold {rvr.threshold_pct}%.</div>
              <SourceRef refId={rvr.source_ref} />
              {rvr.triggered && <Btn kind="primary" className="mt-2" onClick={handToSales} disabled={post.pending}>Hand to sales</Btn>}
            </div>
          )}
          {sj.service_lane?.equity && !rvr?.triggered && <IncentiveRow eligible name={<><span aria-hidden>↗</span> Positive equity — upgrade conversation</>} amount={<Money value={sj.service_lane.equity.equity} />} />}
        </Panel>
      }
      center={
        <section className="panel" aria-live="polite">
          <div className="panel-header">
            <h2>Estimate lines</h2>
            <div className="flex gap-2 items-center">
              <Btn kind="alt" size="sm" onClick={prioritize} disabled={post.pending || !e?.lines?.length} title="Service Agent: prioritize findings">
                ⇅ Order by urgency
              </Btn>
              <label className="toggle text-xs"><input type="checkbox" checked={explain} onChange={(x) => setExplain(x.target.checked)} /> Customer explanations</label>
              <span className="panel-badge badge-ai">{e?.lines?.length ?? 0} lines</span>
            </div>
          </div>
          <div className="panel-body">
            <Async q={eq} rows={5}>
              {(est) => (
                <>
                  {est.covered_amount > 0 && <SavingsBanner label="Covered by warranty" value={<Money value={est.covered_amount} decimals={2} />} />}
                  {est.lines.length === 0 && <EmptyState>No actionable findings — nothing to approve.</EmptyState>}
                  {orderedLines(est.lines).map((l: any) => (
                    <RecCard key={l.line_id} icon={<StatusChip status={l.finding_status} />} title={l.item}
                             right={<span className="flex gap-1 items-center">
                               <Confidence tone={URGENCY_TONE[l.urgency]}>{l.urgency || "review"}</Confidence>
                               {l.decision && <Badge tone={l.decision === "approve" ? "green" : "red"}>{l.decision}d · {l.decided_by}</Badge>}
                             </span>}>
                      <div className="flex flex-col gap-2">
                        <div className="text-xs muted">
                          {l.measurement != null && <>Measured {l.measurement} {l.unit} · </>}
                          {l.evidence_doc_id ? <span className="data-source" title={l.evidence_doc_id}>inspection photo on file</span> : <span className="dim">no photo on file</span>}
                          {" "}<span className="data-source" title={l.finding_ref}>from the inspection</span>
                        </div>
                        <IncentiveRow eligible={["warranty", "recall"].includes(l.coverage?.pay_type)}
                                      name={<><Badge tone={coverageTone(l.coverage?.pay_type)}>{(l.coverage?.pay_type || "customer_pay").replace("_", " ")}</Badge>{" "}
                                        {l.coverage?.pay_type === "warranty" ? "Warranty covers this line" : l.coverage?.pay_type === "recall" ? "Open recall — no charge" : "Customer pay"}</>}
                                      amount={<>{l.coverage?.rule} <SourceRef refId={l.coverage?.source_ref} /></>}
                                      right={<b><Money value={l.customer_amount} decimals={2} /></b>} />
                        <div className="text-xs muted">
                          {l.labor_op}: {l.labor_hours} h × <Money value={l.labor_rate} /> = <Money value={l.labor_amount} decimals={2} /> <SourceRef refId={l.labor_source_ref} />
                          {(l.parts || []).map((p: any, i: number) => <span key={i}> · {p.description} ×{p.qty} <Money value={p.amount} decimals={2} /></span>)}
                          {(l.audit || []).length > 0 && <Badge tone="purple">edited</Badge>}
                        </div>
                        {explain && l.explanation?.explained && (
                          <AiResponseCard approach="explain_finding">
                            {l.explanation.explanation} {!l.explanation.guardrail_safe && <Badge tone="red">guardrail flagged</Badge>}
                          </AiResponseCard>
                        )}
                        {editable && (
                          <div className="flex flex-wrap gap-2 items-center text-xs">
                            <label>Labor h <input className="input" style={{ width: 70 }} type="number" step="0.1" defaultValue={l.labor_hours}
                                   onChange={(x) => setEdit((s) => ({ ...s, [l.finding_ref]: { ...s[l.finding_ref], labor_hours: Number(x.target.value) } }))} /></label>
                            {l.parts?.[0] && <label>Qty <input className="input" style={{ width: 60 }} type="number" min={1} defaultValue={l.parts[0].qty}
                                   onChange={(x) => setEdit((s) => ({ ...s, [l.finding_ref]: { ...s[l.finding_ref], part_qty: Number(x.target.value) } }))} /></label>}
                            <Btn kind="alt" size="sm" disabled={!edit[l.finding_ref] || patch.pending} onClick={() => reprice(l)}>Re-price</Btn>
                            <Btn kind="ghost" size="sm" onClick={() => { setEdit((s) => ({ ...s, [l.finding_ref]: { include: false } })); }}>Remove line</Btn>
                          </div>
                        )}
                        {!l.decision && waiting && (
                          <div className="response-btns">
                            <Btn kind="use" size="sm" onClick={() => setVerbal({ line: l, decision: "approve" })}>Record approval</Btn>
                            <Btn kind="alt" size="sm" onClick={() => setVerbal({ line: l, decision: "decline" })}>Record decline</Btn>
                          </div>
                        )}
                        {l.decision === "decline" && (
                          <div className="flex items-center gap-2 text-xs">Follow up in
                            {[30, 60, 90].map((d) => (
                              <Btn key={d} kind={(cadence[l.line_id] || 30) === d ? "primary" : "alt"} size="sm" onClick={() => setDeclinedCadence(l, d)}>{d} days</Btn>
                            ))}
                          </div>
                        )}
                      </div>
                    </RecCard>
                  ))}
                  {b && est.lines.length > 0 && (
                    <RecCard icon="⚖" title="Good · Better · Best">
                      <CompareTable aLabel={`Good (${b.good.count})`} bLabel={`Better (${b.better.count}) · Best (${b.best.count})`} rows={[
                        { label: "Customer pays", a: <Money value={b.good.customer_total} decimals={2} />, b: <><Money value={b.better.customer_total} decimals={2} /> · <Money value={b.best.customer_total} decimals={2} /></> },
                        { label: "Warranty covers", a: <Money value={b.good.covered_amount} decimals={2} />, b: <><Money value={b.better.covered_amount} decimals={2} /> · <Money value={b.best.covered_amount} decimals={2} /></> },
                        { label: "Lines", a: "Safety items only", b: "Safety + recommended · everything found" },
                      ]} footnote={<>Totals from the labor guide and parts catalog · <SourceRef refId={b.source_ref} /></>} />
                    </RecCard>
                  )}
                  <div className="flex gap-2 flex-wrap items-center">
                    <span className="text-sm">Total <b><Money value={est.total} decimals={2} /></b> · customer <b><Money value={est.customer_total} decimals={2} /></b>{est.approved_total ? <> · approved <b><Money value={est.approved_total} decimals={2} /></b></> : null}</span>
                    <span className="flex-1" />
                    {Object.keys(edit).length > 0 && editable && (
                      <Btn kind="secondary" onClick={async () => {
                        const out = await patch.run(`/service/estimates/${encodeURIComponent(estId)}`, { adjustments: edit });
                        done("Estimate re-priced", out, patch.error); setEdit({});
                      }}>Apply all edits</Btn>
                    )}
                    <Btn kind="secondary" onClick={() => setCustomerMode(true)}>Customer-facing mode</Btn>
                    <Btn kind="present" used={est.status !== "pending"} onClick={present} disabled={post.pending || !["pending", "presented"].includes(est.status)}>
                      {est.status === "pending" ? "Present estimate" : est.status === "presented" ? "Re-send link" : `Decided: ${est.status.replace("_", " ")}`}
                    </Btn>
                  </div>
                  {presented?.approval_link && <div className="text-xs muted">Approval link: <span className="mono">{presented.approval_link}</span></div>}
                </>
              )}
            </Async>
          </div>
        </section>
      }
      right={
        <Panel title="Live assistance" badge={waiting ? "awaiting customer" : e?.status || "…"} badgeKind="active">
          <form className="flex flex-col gap-2" onSubmit={(x) => { x.preventDefault(); if (utt.trim()) pushback(); }}>
            <label className="section-label" htmlFor="pushback">Customer pushback</label>
            <input id="pushback" className="input" value={utt} onChange={(x) => setUtt(x.target.value)} placeholder="e.g. “That's too expensive”" />
            <Btn kind="alt" type="submit" disabled={!utt.trim() || post.pending}>Get talk track</Btn>
          </form>
          {push && (
            <>
              <ObjectionAlert label={push.objection_type ? push.objection_type.replace(/_/g, " ") : "concern"} quote={push.utterance} />
              <AiResponseCard approach="service talk track" onUse={() => toast("Talk track marked used", "success")}>
                {push.talk_track} {push.good_bundle_total != null && <>(<Money value={push.good_bundle_total} decimals={2} />)</>}
              </AiResponseCard>
            </>
          )}
          <div className="section-label">Add-ons (declined before / due)</div>
          {(sj.declined_work || []).length ? sj.declined_work.map((d: any) => (
            <CrossSellRow key={d.declined_id} name={d.item} detail={<>declined <DateTime value={d.declined_at} mode="date" /></>} action={<Money value={d.amount} />} />
          )) : <div className="dim text-sm">No prior declined work.</div>}
          <div className="section-label">Next best action</div>
          {waiting && (e?.minutes_waiting ?? 0) > 20 ? (
            <AiResponseCard approach="follow up">No decision after {e.minutes_waiting} min — call the customer or re-send the link.</AiResponseCard>
          ) : rvr?.triggered ? (
            <AiResponseCard approach="upgrade">Repair cost is high relative to value — introduce the sales advisor.</AiResponseCard>
          ) : e?.status === "pending" ? (
            <AiResponseCard approach="present">Review lines, then present — the customer gets a link to approve line by line.</AiResponseCard>
          ) : <div className="dim text-sm">Nothing pending.</div>}
          {verbal && (
            <Modal open onClose={() => setVerbal(null)} title={`Record ${verbal.decision} — ${verbal.line.item}`}
                   footer={<><Btn kind="ghost" onClick={() => setVerbal(null)}>Cancel</Btn><Btn kind="primary" onClick={decide} disabled={!reason.trim() || post.pending}>Record</Btn></>}>
              <p className="text-sm muted">Use this when the customer decides verbally. It is recorded as decided by the advisor, with your reason. Declined lines go to DEALER_DeclinedWork for follow-up.</p>
              <textarea className="textarea" value={reason} onChange={(x) => setReason(x.target.value)} placeholder="e.g. Customer approved by phone at 10:42" aria-label="Reason" />
            </Modal>
          )}
          {customerMode && e && <CustomerMode est={e} onClose={() => setCustomerMode(false)} />}
        </Panel>
      }
    />
  );
}

/** Tablet handover / second screen: the same lines in the brand theme (what
 *  the customer sees in My Garage › Service — same rec-card / incentive-row
 *  primitives). Read-only; the customer decides on their own device via the link. */
function CustomerMode({ est, onClose }: { est: any; onClose: () => void }) {
  return (
    <Modal open onClose={onClose} title="Your estimate">
      <div data-theme="brand" className="flex flex-col gap-3 p-3 rounded-lg" style={{ background: "var(--bg-dark)", color: "var(--text-primary)" }}>
        {est.lines.map((l: any) => (
          <div key={l.line_id} className="rec-card">
            <div className="rec-card-header"><div className="rec-card-title"><StatusChip status={l.finding_status} /> {l.item}</div><b><Money value={l.customer_amount} decimals={2} /></b></div>
            <div className="rec-card-body text-sm">
              {l.explanation?.explanation || `Our technician rated this ${l.finding_status}.`}
              {l.coverage?.pay_type === "warranty" && <div className="mt-1"><Badge tone="green">Covered by warranty</Badge></div>}
              {l.coverage?.pay_type === "recall" && <div className="mt-1"><Badge tone="blue">Open recall — no charge</Badge></div>}
            </div>
          </div>
        ))}
        <SavingsBanner label="You pay" value={<Money value={est.customer_total} decimals={2} />} />
      </div>
    </Modal>
  );
}
