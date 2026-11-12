"use client";

// Repair Order view (§5.20.5, design doc §3.1) — all service roles, role-aware.
// The technician payload arrives masked from the API (no customer contact,
// pricing, coverage split); the UI only hides controls the role cannot use.
// Handoff-to-sales creates the A2 purchase journey (shows as a chip on the
// sales Journeys kanban). JourneyActivityFeed + RO timeline; AI Log at bottom.

import { humanizeCode } from "@/lib/text";
import { useState } from "react";
import {
  ActivityItem, Async, Badge, Btn, Card, DataTable, DateTime, Distance, EmptyState, ErrorState, Field, FilterPills, IncentiveRow,
  InfoRow, JourneyActivityFeed, Modal, Money, SourceRef, StatusChip, Tabs, ThreeColumn, Panel, CustomerCard, useToast, vehicleLabel, stageLabel } from "@/components/app";
import { apiGet, apiPost } from "@/lib/api";
import { useApi, useMutation, useRouteId, timeAgo } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";
import { AgentActionsProvider } from "@/components/AgentBar";
import { ServiceFrame, isAdvisor, useServiceJourney } from "../common";

type Tab = "lines" | "parts" | "inspection" | "estimate" | "timeline" | "documents";

export default function RoView() {
  const id = useRouteId(2);
  const q = useServiceJourney(id);
  return <ServiceFrame id={id} q={q}>{(sj) => <RoBody sj={sj} reload={q.reload} />}</ServiceFrame>;
}

function RoBody({ sj, reload }: { sj: any; reload: () => void }) {
  const toast = useToast();
  const { persona } = usePersona();
  const role = persona?.role || "";
  const advisor = isAdvisor(role);
  const [tab, setTab] = useState<Tab>("lines");
  const [msgOpen, setMsgOpen] = useState(false);
  const [handOpen, setHandOpen] = useState(false);
  const [readyOpen, setReadyOpen] = useState(false);
  const [goodwillOpen, setGoodwillOpen] = useState(false);
  const post = useMutation();
  const patch = useMutation("PATCH");
  const ro = sj.repair_order;

  const act = async (label: string, fn: () => Promise<any>) => {
    const out = await fn();
    if (out) { toast(label, "success"); reload(); } else toast((post.error || patch.error) ?? "Action failed", "error");
  };
  const lineStatus = (lineId: string, status: string) => act(`Line → ${status.replace("_", " ")}`,
    () => patch.run(`/service/ros/${encodeURIComponent(ro.ro_id)}/lines/${encodeURIComponent(lineId)}/status`, { status }));
  const advance = () => act("Stage advanced", () => post.run(`/service/${encodeURIComponent(sj.service_journey_id)}/advance`, { evidence: `advanced from RO view by ${persona?.userId}` }));
  // §5.20.5 notify_ready: close the RO, share the invoice, and offer a pickup /
  // delivery slot. The slot is optional and degrades when scheduling is down.
  const notifyReady = (pickup?: { type: string; slot_start: string } | null) =>
    act("Customer notified — vehicle ready", () => post.run(`/service/ros/${encodeURIComponent(ro.ro_id)}/close`, pickup ? { pickup } : {}));

  const approvedLines = (sj.lines || []).filter((l: any) => l.decision === "approve");
  const allDone = approvedLines.length > 0 && approvedLines.every((l: any) => ["complete", "qc_passed"].includes(l.line_status));

  // A7 scope for the global AgentBar: this RO's traces, its VIN as chat
  // grounding, and Run-now reusing the page's own actions (§1.6).
  return (
    <AgentActionsProvider value={{
      serviceJourneyId: sj.service_journey_id,
      chatContext: sj.vehicle?.vin,
      actions: advisor ? {
        "a7.repurchase_handoff": { label: "Hand to sales", run: async () => setHandOpen(true) },
        ...(sj.estimate ? {
          "a7.approval_chaser": {
            label: "Re-present",
            run: async () => {
              await apiPost("/service/estimates/re-present", { estimate_ids: [sj.estimate.estimate_id] });
              reload();
            },
          },
        } : {}),
      } : {},
    }}>
    <ThreeColumn
      left={
        <Panel title={sj.masked ? "Vehicle" : "Customer & vehicle"} badge={sj.stage?.replace("_", " ")} badgeKind="live">
          {sj.customer ? (
            <CustomerCard name={sj.customer.name || sj.customer.customer_id} meta={<>{sj.customer.email}<br />{sj.customer.phone}</>}
                          score={sj.customer.approval_preference ? `prefers ${sj.customer.approval_preference.replace("_", " ")}` : undefined} />
          ) : <div className="insight-box">Customer contact data is not shown to technicians.</div>}
          <div>
            <InfoRow label="VIN" value={<span className="mono">{sj.vehicle?.vin}</span>} />
            <InfoRow label="Model" value={vehicleLabel(sj.vehicle)} />
            <InfoRow label="Tag" value={sj.tag_number} />
            <InfoRow label="Mileage in" value={<Distance value={sj.vehicle?.mileage_in ?? sj.vehicle?.odometer} />} />
            <InfoRow label="RO" value={ro ? <span title={ro.dms_ref}>{ro.ro_number} · {ro.status}</span> : "not opened"} />
            <InfoRow label="Advisor" value={sj.advisor?.name} />
            <InfoRow label="Technician" value={sj.technician?.name || "unassigned"} />
            <InfoRow label="Promised" value={<DateTime value={sj.promised_at} />} />
            <InfoRow label="Projected" value={<span style={{ color: sj.overdue_promise ? "var(--danger)" : undefined }}><DateTime value={sj.projected_at} /></span>} />
          </div>
          <div className="section-label">Customer concerns</div>
          {(sj.concerns || []).length ? (sj.concerns.map((c: string, i: number) => <ActivityItem key={i}>{c}</ActivityItem>)) : <div className="dim text-sm">None recorded.</div>}
          {sj.service_lane && advisor && (
            <>
              <div className="section-label">Service lane (sales view)</div>
              {sj.service_lane.equity ? <IncentiveRow eligible name="Positive equity — upgrade candidate" amount={<Money value={sj.service_lane.equity.equity} />} />
                : <IncentiveRow eligible={false} name="No positive equity on file" />}
              {sj.service_lane.upsell.map((u: any) => <IncentiveRow key={u.finding_ref} eligible name={`Upsell: ${u.item}`} amount="yellow finding" />)}
              <div className="dim text-[.68rem]"><SourceRef refId={sj.service_lane.source_ref} /></div>
            </>
          )}
        </Panel>
      }
      center={
        <section className="panel">
          <div className="panel-header"><h2>Repair order</h2><Tabs pill={false} value={tab} onChange={setTab} tabs={[
            { id: "lines", label: "Lines", count: (sj.lines || []).length }, { id: "parts", label: "Parts", count: (sj.parts || []).length },
            { id: "inspection", label: "Inspection" }, { id: "estimate", label: "Estimate" },
            { id: "timeline", label: "Timeline" }, ...(sj.masked ? [] : [{ id: "documents" as const, label: "Documents" }]),
          ]} /></div>
          <div className="panel-body">
            {tab === "lines" && ((sj.lines || []).length ? (sj.lines.map((l: any) => (
              <div key={l.line_id} className="rec-card">
                <div className="rec-card-header">
                  <div className="rec-card-title"><StatusChip status={l.finding_status} /> {l.item}</div>
                  <span className="flex gap-1 items-center">
                    <Badge tone={l.decision === "approve" ? "green" : l.decision === "decline" ? "red" : "gray"}>{l.decision || "awaiting decision"}</Badge>
                    <Badge tone="blue">{(l.line_status || "pending").replace("_", " ")}</Badge>
                  </span>
                </div>
                <div className="rec-card-body flex flex-wrap items-center justify-between gap-2">
                  <div className="text-sm">
                    {l.labor_op} · {l.labor_hours} h {l.coverage?.pay_type && <Badge tone={l.coverage.pay_type === "warranty" ? "green" : "gray"}>{l.coverage.pay_type.replace("_", " ")}</Badge>}
                    {!sj.masked && <> · <Money value={l.line_total} decimals={2} /></>}
                    <div className="dim text-xs"><SourceRef refId={l.labor_source_ref} /></div>
                  </div>
                  {l.decision === "approve" && role !== "dealer_parts" && (
                    <div className="flex gap-1 flex-wrap">
                      {["in_progress", "complete", "qc_passed"].map((st) => (
                        <Btn key={st} kind={l.line_status === st ? "success" : "alt"} size="sm" disabled={patch.pending || l.line_status === st}
                             onClick={() => lineStatus(l.line_id, st)}>{st.replace("_", " ")}</Btn>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))) : <EmptyState>No lines yet — the estimate is built from the inspection.</EmptyState>)}

            {tab === "parts" && <PartsTab sj={sj} canOrder={["dealer_parts", "dealer_service", "dealer_service_manager", "dealer_manager"].includes(role)} onChange={reload} />}

            {tab === "inspection" && ((sj.inspection || []).length ? (
              <DataTable rows={sj.inspection} rowKey={(i: any) => i.inspection_id} columns={[
                { key: "item", header: "Item", sort: (i: any) => i.item },
                { key: "status", header: "Status", sort: (i: any) => ({ red: 0, yellow: 1, green: 2 } as any)[i.status], render: (i: any) => <StatusChip status={i.status} /> },
                { key: "m", header: "Measurement", render: (i: any) => i.measurement != null ? `${i.measurement} ${i.unit || ""}` : "—" },
                { key: "urgency", header: "Urgency", render: (i: any) => i.urgency },
                { key: "ev", header: "Evidence", render: (i: any) => i.evidence_doc_id ? <span className="data-source">{i.evidence_doc_id}</span> : "—" },
              ]} />
            ) : <EmptyState>No inspection submitted.</EmptyState>)}

            {tab === "estimate" && (sj.estimate ? (
              <Card title={`Estimate ${sj.estimate.estimate_id}`} actions={<Badge tone="blue">{humanizeCode(sj.estimate.status)}</Badge>}>
                {!sj.masked && <InfoRow label="Total" value={<Money value={sj.estimate.total} decimals={2} />} />}
                {sj.estimate.presented_at && <InfoRow label="Presented" value={<DateTime value={sj.estimate.presented_at} />} />}
                {advisor && <a className="btn btn-primary mt-3" href={`/dealer/service/${encodeURIComponent(sj.service_journey_id)}/estimate`}>Open Estimate & Approval</a>}
                {sj.masked && <p className="dim text-sm mt-2">Read-only for technicians.</p>}
              </Card>
            ) : <EmptyState>No estimate yet.</EmptyState>)}

            {tab === "timeline" && (
              <>
                {sj.journey_id && (
                  <>
                    <div className="section-label">Journey activity</div>
                    <JourneyActivityFeed journeyId={sj.journey_id} />
                  </>
                )}
                <div className="section-label">RO events</div>
                {(sj.timeline || []).length ? sj.timeline.map((e: any, i: number) => (
                  <ActivityItem key={i} time={timeAgo(e.ts)}><b>{(e.type || "").replace(/[._]/g, " ")}</b> <span className="muted">{e.evidence}</span>{e.actor && <span className="dim"> · {e.actor}</span>}</ActivityItem>
                )) : <EmptyState>No events yet.</EmptyState>}
              </>
            )}

            {tab === "documents" && ((sj.documents || []).length ? (
              <DataTable rows={sj.documents} rowKey={(d: any) => d.document_id} columns={[
                { key: "kind", header: "Document", render: (d: any) => (d.kind || "").replace(/_/g, " ") },
                { key: "tag", header: "Tag", render: (d: any) => d.tag || "—" },
                { key: "created", header: "Added", render: (d: any) => <DateTime value={d.created_at} /> },
                { key: "uri", header: "", render: (d: any) => <span className="data-source">{d.document_id}</span> },
              ]} />
            ) : <EmptyState>No documents yet (RO, inspection report, invoice appear here).</EmptyState>)}
          </div>
        </section>
      }
      right={
        <Panel title="Actions" badge={stageLabel(sj.stage)} badgeKind="active">
          {advisor && <Btn kind="primary" size="lg" onClick={advance} disabled={post.pending}>Advance stage →</Btn>}
          {advisor && sj.stage === "REPAIR" && (
            <Btn kind={allDone ? "success" : "secondary"} size="lg" onClick={() => setReadyOpen(true)} disabled={post.pending || !ro}
                 title={allDone ? "" : "Lines still in progress"}>Notify ready</Btn>
          )}
          {advisor && <Btn kind="secondary" size="lg" onClick={() => setMsgOpen(true)}>Message customer</Btn>}
          {advisor && <Btn kind="secondary" size="lg" onClick={() => setHandOpen(true)}>Hand to sales</Btn>}
          {advisor && !sj.masked && <Btn kind="secondary" size="lg" onClick={() => setGoodwillOpen(true)}>Request goodwill</Btn>}
          {role === "dealer_technician" && <a className="btn btn-primary btn-lg" href={`/dealer/service/${encodeURIComponent(sj.service_journey_id)}/inspect`}>Inspect & diagnose</a>}
          {!sj.handoff?.ok && <div className="objection-alert"><div className="objection-label">Gate</div>{sj.handoff.missing.join(", ").replace(/_/g, " ")}</div>}
          <div className="section-label">Parts orders</div>
          {(sj.parts_orders || []).length ? sj.parts_orders.map((o: any) => (
            <IncentiveRow key={o.parts_order_id} eligible={o.status === "received"} name={`${o.description || o.part_number || "Part"} · ${String(o.status).replace(/_/g, " ")}`} amount={<>ETA <DateTime value={o.eta} mode="date" /></>} />
          )) : <div className="dim text-sm">No orders.</div>}
          {(sj.declined_work || []).length > 0 && advisor && (
            <>
              <div className="section-label">Prior declined work (this VIN)</div>
              {sj.declined_work.map((d: any) => <IncentiveRow key={d.declined_id} eligible={false} name={d.item} amount={<Money value={d.amount} />} />)}
            </>
          )}
          <a className="btn btn-ghost" href={`/dealer/service/history/${encodeURIComponent(sj.vehicle?.vin || "")}`}>Service history →</a>
          {msgOpen && <MessageModal sj={sj} onClose={() => setMsgOpen(false)} />}
          {handOpen && <HandoffModal sj={sj} onClose={() => { setHandOpen(false); reload(); }} />}
          {goodwillOpen && <GoodwillModal sj={sj} onClose={() => setGoodwillOpen(false)} />}
          {readyOpen && <ReadyModal sj={sj} pending={post.pending} onClose={() => setReadyOpen(false)}
                                    onConfirm={async (pickup) => { setReadyOpen(false); await notifyReady(pickup); }} />}
        </Panel>
      }
    />
    </AgentActionsProvider>
  );
}

function PartsTab({ sj, canOrder, onChange }: { sj: any; canOrder: boolean; onChange: () => void }) {
  const toast = useToast();
  const [pn, setPn] = useState("");
  const [qty, setQty] = useState(1);
  const [eta, setEta] = useState("");
  const m = useMutation();
  const order = async () => {
    const out = await m.run("/service/parts/orders", { ro_id: sj.repair_order?.ro_id, part_number: pn, qty, eta: eta ? `${eta}T09:00:00Z` : null });
    if (out) { toast(`Ordered ${pn}`, "success"); setPn(""); onChange(); } else toast(m.error || "Order failed", "error");
  };
  return (
    <>
      {(sj.parts || []).length ? (
        <DataTable rows={sj.parts} rowKey={(p: any) => p.part_id} columns={[
          { key: "desc", header: "Part", render: (p: any) => <>{p.description} <span className="dim mono text-xs">{p.part_number}</span></> },
          { key: "qty", header: "Qty", num: true, render: (p: any) => p.qty },
          { key: "hand", header: "On hand", render: (p: any) => <Badge tone={p.on_hand ? "green" : "orange"}>{p.on_hand ? "on hand" : "not stocked"}</Badge> },
          ...(sj.masked ? [] : [{ key: "price", header: "Unit price", num: true, render: (p: any) => <Money value={p.unit_price} decimals={2} /> }]),
          { key: "ls", header: "Line status", render: (p: any) => p.line_status },
        ]} />
      ) : <EmptyState>No parts on this RO.</EmptyState>}
      {canOrder && sj.repair_order && (
        <div className="card mt-3">
          <div className="card-title">Order a part</div>
          <div className="flex gap-2 flex-wrap items-end">
            <input className="input" style={{ maxWidth: 200 }} placeholder="Part number" value={pn} onChange={(e) => setPn(e.target.value)} aria-label="Part number" />
            <input className="input" style={{ maxWidth: 90 }} type="number" min={1} value={qty} onChange={(e) => setQty(Number(e.target.value))} aria-label="Quantity" />
            <input className="input" style={{ maxWidth: 170 }} type="date" value={eta} onChange={(e) => setEta(e.target.value)} aria-label="ETA" />
            <Btn kind="primary" onClick={order} disabled={!pn || m.pending}>Order</Btn>
          </div>
        </div>
      )}
    </>
  );
}

/** Notify ready (§5.20.5): shares the invoice to My Garage Documents and offers
 *  a pickup / delivery slot from the scheduling connector. Degrades to a plain
 *  ready notification when slot availability isn't reachable. */
function ReadyModal({ sj, pending, onClose, onConfirm }: {
  sj: any; pending: boolean; onClose: () => void; onConfirm: (pickup: { type: string; slot_start: string } | null) => void }) {
  const [mode, setMode] = useState<"none" | "pickup" | "delivery">("none");
  const [slot, setSlot] = useState<string | null>(null);
  const date = new Date().toISOString().slice(0, 10);
  const dealer = sj.dealer_id || sj.vehicle?.dealer_id;
  const avail = useApi<any>(mode !== "none" && dealer ? `/service/availability?dealer=${encodeURIComponent(dealer)}&date=${date}` : null);
  const needsSlot = mode !== "none";
  return (
    <Modal open onClose={onClose} title="Notify ready"
           footer={<>
             <Btn kind="ghost" onClick={onClose}>Cancel</Btn>
             <Btn kind="primary" disabled={pending || (needsSlot && avail.loading)}
                  onClick={() => onConfirm(needsSlot && slot ? { type: mode, slot_start: slot } : null)}>
               {needsSlot && !slot ? "Notify without a slot" : "Notify ready"}
             </Btn>
           </>}>
      <p className="text-sm muted">Closes the RO in the DMS, shares the invoice to My Garage Documents, and messages the customer. Optionally offer a pickup or delivery slot.</p>
      <FilterPills value={mode} onChange={setMode} options={[
        { id: "none", label: "Customer arranges" }, { id: "pickup", label: "Pickup slot" }, { id: "delivery", label: "Delivery slot" },
      ]} />
      {needsSlot && (
        avail.error ? (
          <div className="dim text-xs mt-2" role="status">Scheduling connector unavailable ({avail.error}) — the ready message will ask the customer to arrange a time.</div>
        ) : (
          <Async q={avail} rows={2}>
            {(a) => (
              <div className="grid gap-2 mt-2" style={{ gridTemplateColumns: "repeat(auto-fill,minmax(110px,1fr))" }} role="radiogroup" aria-label={`${mode} slot`}>
                {(a.slots || []).filter((s: any) => s.available).map((s: any) => (
                  <button key={s.slot_start} role="radio" aria-checked={slot === s.slot_start}
                          className={`btn ${slot === s.slot_start ? "btn-primary" : "btn-secondary"}`}
                          onClick={() => setSlot(s.slot_start)}>
                    <DateTime value={s.slot_start} mode="time" />
                  </button>
                ))}
                {!(a.slots || []).some((s: any) => s.available) && <div className="dim text-sm">No slots left today — notify without a slot.</div>}
              </div>
            )}
          </Async>
        )
      )}
    </Modal>
  );
}

function MessageModal({ sj, onClose }: { sj: any; onClose: () => void }) {
  const toast = useToast();
  const [purpose, setPurpose] = useState("status");
  const [draft, setDraft] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const send = useMutation();
  const load = async (p: string) => {
    setPurpose(p);
    try { setDraft(await apiGet<any>(`/service/journeys/${encodeURIComponent(sj.service_journey_id)}/message?purpose=${p}`)); }
    catch (e: any) { setErr(e.message); }
  };
  if (!draft && !err) load(purpose);
  const go = async () => {
    const out = await send.run(`/service/journeys/${encodeURIComponent(sj.service_journey_id)}/message`, draft);
    if (out) { toast(out.sent ? "Message sent" : out.reason === "no_consent" ? "Not sent — customer has not consented to this channel" : "Queued as a dealer task", out.sent ? "success" : "info"); onClose(); }
    else toast(send.error || "Send failed", "error");
  };
  return (
    <Modal open onClose={onClose} title="Message customer" footer={<><Btn kind="ghost" onClick={onClose}>Cancel</Btn><Btn kind="primary" onClick={go} disabled={!draft || send.pending}>Approve & send</Btn></>}>
      {err && <ErrorState error={err} />}
      <Tabs value={purpose} onChange={(p) => { setDraft(null); load(p); }} tabs={[{ id: "status", label: "Status" }, { id: "estimate", label: "Estimate ready" }, { id: "delay", label: "Delay" }, { id: "ready", label: "Ready" }]} />
      {draft ? (
        <>
          <div className="text-xs muted">The Service Agent drafted this · channel {draft.channel} · you approve before it sends</div>
          <select className="select" value={draft.channel} onChange={(e) => setDraft({ ...draft, channel: e.target.value })} aria-label="Channel">
            <option value="email">Email</option><option value="sms">SMS</option>
          </select>
          <textarea className="textarea" value={draft.body} onChange={(e) => setDraft({ ...draft, body: e.target.value })} aria-label="Message" />
        </>
      ) : <div className="dim">Drafting…</div>}
    </Modal>
  );
}

/** Ask the service manager (or GM) to cover part of this visit as goodwill. The
 *  request lands in /approvals as "Service goodwill"; the requester can never
 *  approve their own, and the decision is audited there. */
function GoodwillModal({ sj, onClose }: { sj: any; onClose: () => void }) {
  const toast = useToast();
  const currency = sj.estimate?.currency || (sj.market_id === "de" ? "EUR" : "USD");
  const [amount, setAmount] = useState(() => String(Math.round(Number(sj.estimate?.total || 0) * 0.25) || ""));
  const [reason, setReason] = useState("");
  const m = useMutation();
  const value = Number(amount);
  const valid = Number.isFinite(value) && value > 0 && reason.trim().length >= 5;
  const go = async () => {
    const out: any = await m.run("/approvals", {
      type: "goodwill",
      scope: { dealer_id: sj.dealer_id },
      payload: { service_journey_id: sj.service_journey_id, ro_id: sj.repair_order?.ro_id || null,
                 vin: sj.vehicle?.vin || null, amount: value, currency, reason: reason.trim() },
      reason: reason.trim(),
    });
    if (out?.approval_id) { toast(`Goodwill request ${out.approval_id} sent to the service manager`, "success"); onClose(); }
    else toast(m.error || "Could not send the goodwill request", "error");
  };
  return (
    <Modal open onClose={onClose} title="Request goodwill"
      footer={<><Btn kind="ghost" onClick={onClose}>Cancel</Btn><Btn kind="primary" onClick={go} disabled={!valid || m.pending}>Send for approval</Btn></>}>
      <p className="text-sm muted">The service manager (or the GM) decides it in their Approvals inbox. You can&apos;t approve your own request.</p>
      <Field label={`Amount (${currency})`}
             hint={sj.estimate?.total ? <>Estimate total <Money value={sj.estimate.total} decimals={2} /></> : "No estimate on file."}>
        {(id) => <input id={id} className="input" type="number" min={1} step={1} inputMode="decimal" value={amount}
                        onChange={(e) => setAmount(e.target.value)} />}
      </Field>
      <Field label="Reason" hint="At least a few words — the service manager sees it with the request.">
        {(id) => <textarea id={id} className="textarea" value={reason} onChange={(e) => setReason(e.target.value)}
                           placeholder="e.g. long-tenure customer; repair ran over the estimate" />}
      </Field>
    </Modal>
  );
}

function HandoffModal({ sj, onClose }: { sj: any; onClose: () => void }) {
  const toast = useToast();
  const [reason, setReason] = useState("positive equity at service visit");
  const m = useMutation();
  const go = async () => {
    const out = await m.run(`/service/${encodeURIComponent(sj.service_journey_id)}/handoff-to-sales`, { reason });
    if (out) { toast(`Handed to sales — journey ${out.new_journey_id}${out.created_journey ? " created" : " updated"}`, "success"); onClose(); }
    else toast(m.error || "Handoff failed", "error");
  };
  return (
    <Modal open onClose={onClose} title="Repurchase / upgrade handoff" footer={<><Btn kind="ghost" onClick={onClose}>Cancel</Btn><Btn kind="primary" onClick={go} disabled={!reason.trim() || m.pending}>Hand to sales</Btn></>}>
      <p className="text-sm muted">Creates (or updates) the customer&apos;s purchase journey with source <b>service lane</b> and the service context, and assigns a task to the sales advisor. It appears as a chip on the sales Journeys kanban.</p>
      <textarea className="textarea" value={reason} onChange={(e) => setReason(e.target.value)} aria-label="Reason" />
    </Modal>
  );
}
