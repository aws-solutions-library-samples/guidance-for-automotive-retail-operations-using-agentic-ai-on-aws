"use client";

// Delivery Board (§5.15, G-07): Scheduled → Prepped → Docs ready → Delivered.
// The column is derived server-side from the prep checklist (PDI, detail,
// accessories, charge / fuel level, EV home-charger install) and signed docs —
// it is never dragged by hand. Card → drawer: checklist, documents, customer
// confirmation, reschedule (notifies the customer), delivery-day message, and
// the gated "Mark delivered" (DELIVER → OWN, vehicle.delivered → first service).

import { fromZoneInput, toZoneInput } from "@/lib/market";
import { useEffect, useState } from "react";
import { AgentAction, AgentActionsProvider } from "@/components/AgentBar";
import {
  AiLog, Async, Badge, Btn, DateTime, Drawer, Field, InfoRow, JourneyActivityFeed, Kanban,
  MarketProvider, Page, ProgressBar, SectionLabel, ViewHeader, useToast,
} from "@/components/app";
import { ApiKpiGrid, titleCase } from "@/app/dealer/kpis";
import { useApi, useMutation, useQueryParam } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";
import { ViewAsCustomer } from "@/components/ViewAsCustomer";

const ITEM_LABEL: Record<string, string> = {
  pdi: "Pre-delivery inspection (PDI)", detail: "Detail", accessories: "Accessories installed",
  charge_level: "Charge / fuel level", charger_install: "Home charger install",
};
const WRITE = ["dealer_delivery", "dealer_fni", "dealer_manager"];

export default function DeliveryBoardPage() {
  const { persona } = usePersona();
  const canWrite = WRITE.includes(persona?.role || "");
  const focus = useQueryParam("focus");
  const q = useApi<any>("/deliveries", { pollMs: 60000 });
  const [openId, setOpenId] = useState<string | null>(null);
  useEffect(() => { if (focus) setOpenId(focus); }, [focus]);
  const cards: any[] = q.data ? Object.values(q.data.lanes).flat() as any[] : [];
  const open = cards.find((c) => c.journey_id === openId) || null;

  // Run-now bindings the global AgentBar picks up for the Delivery agents —
  // scoped to the selected card and reusing the same endpoints the drawer calls
  // (message / reschedule-notify).
  const agentMsg = useMutation();
  const agentNotify = useMutation("PATCH");
  const agentActions: Record<string, AgentAction> = {};
  if (open && canWrite && open.lane !== "delivered") {
    const jid = encodeURIComponent(open.journey_id);
    agentActions["a2.delivery_day"] = {
      label: "Send message",
      run: async () => {
        const r = await agentMsg.run(`/deliveries/${jid}/message`, {});
        if (!r) throw new Error("Send failed");
        q.reload();
      },
    };
    if (open.scheduled_at) agentActions["a2.delivery_notifier"] = {
      label: "Re-notify slot",
      run: async () => {
        const r = await agentNotify.run(`/deliveries/${jid}`, { scheduled_at: open.scheduled_at });
        if (!r) throw new Error("Notify failed");
        q.reload();
      },
    };
  }

  return (
    <MarketProvider marketId={q.data?.market_id || persona?.marketId}>
      <AgentActionsProvider value={{ actions: agentActions, journeyId: open?.journey_id || null }}>
      <Page>
        <ViewHeader title="Delivery Board" subtitle="Every sold vehicle from booked deal to keys in hand."
                    actions={<Btn kind="secondary" size="sm" onClick={() => q.reload()}>Refresh</Btn>} />
        <Async q={q}>
          {(d: any) => (
            <>
              <ApiKpiGrid kpis={[
                { label: "Scheduled", value: d.counts.scheduled }, { label: "Prepped", value: d.counts.prepped },
                { label: "Docs ready", value: d.counts.docs_ready },
                { label: "Unconfirmed", value: d.unconfirmed, trend: d.unconfirmed ? "down" : null, delta: `${d.today} today` },
              ]} />
              <Kanban
                columns={d.lane_order.map((l: string) => ({ id: l, title: d.lane_labels[l], items: d.lanes[l] }))}
                empty="No deliveries"
                renderCard={(c: any) => (
                  <button key={c.delivery_id} className="kanban-card" onClick={() => setOpenId(c.journey_id)}
                          aria-label={`Open delivery for ${c.customer_name || c.customer_id}`}>
                    <div className="flex justify-between gap-2"><b className="truncate">{c.customer_name || c.customer_id}</b>
                      {c.customer_confirmed ? <Badge tone="green">confirmed</Badge> : c.lane !== "delivered" && <Badge tone="orange">unconfirmed</Badge>}</div>
                    <div className="dim text-xs mt-1">{c.vehicle_name || c.vehicle_model_id}{c.vin && <> · <span className="mono">{c.vin.slice(-8)}</span></>}</div>
                    <div className="dim text-xs">{c.lane === "delivered" ? <>Delivered <DateTime value={c.delivered_at || c.scheduled_at} /></> : c.scheduled_at ? <DateTime value={c.scheduled_at} /> : "Not scheduled"}</div>
                    {c.lane !== "delivered" && (
                      <div className="mt-2 flex items-center gap-2">
                        <div className="flex-1"><ProgressBar value={c.prep_done} max={c.prep_total} /></div>
                        <span className="dim text-[.65rem]">{c.prep_done}/{c.prep_total}</span>
                        {c.docs_signed && <Badge tone="blue">docs</Badge>}
                      </div>
                    )}
                  </button>
                )}
              />
            </>
          )}
        </Async>
        {open && <DeliveryDrawer card={open} canWrite={canWrite} onClose={() => setOpenId(null)} onChange={q.reload} />}
      </Page>
      {/* Bottom-bar AI Log (§5.2.1) — agent traces for the selected delivery's journey. */}
      <AiLog journeyId={open?.journey_id || null} />
      </AgentActionsProvider>
    </MarketProvider>
  );
}

function DeliveryDrawer({ card, canWrite, onClose, onChange }: { card: any; canWrite: boolean; onClose: () => void; onChange: () => void }) {
  const toast = useToast();
  const patch = useMutation("PATCH");
  const post = useMutation();
  const [slot, setSlot] = useState(toZoneInput(card.scheduled_at, card.market_id));
  const [note, setNote] = useState("");
  const jid = encodeURIComponent(card.journey_id);
  const done = card.lane === "delivered";

  const update = async (body: any, label: string) => {
    const r = await patch.run(`/deliveries/${jid}`, body);
    if (r) {
      toast(label, "success");
      if (r.customer_notice && !r.customer_notice.sent) toast(r.customer_notice.reason === "no_consent" ? "Customer has opted out of that channel — notify them by phone." : "Customer notice queued as a follow-up task.", "info");
      onChange();
    } else toast(patch.error || "Update failed", "error");
  };
  const message = async () => {
    const r = await post.run(`/deliveries/${jid}/message`, { note: note || undefined });
    if (r) { toast(r.message?.sent ? "Delivery-day message sent" : "Message queued as a follow-up task", r.message?.sent ? "success" : "info"); setNote(""); onChange(); }
    else toast(post.error || "Send failed", "error");
  };
  const deliver = async () => {
    const r = await post.run(`/deliveries/${jid}/deliver`);
    if (r) { toast("Delivered — journey moved to OWN and the first service reminder is scheduled", "success"); onChange(); onClose(); }
    else toast(post.error || "Cannot mark delivered", "error");
  };
  const missing = [...Object.entries(card.checklist).filter(([, v]) => !v).map(([k]) => ITEM_LABEL[k] || k), ...(card.docs_signed ? [] : ["Signed documents"])];

  // Explicit, keyboard-operable "move to lane" (§2.5 — drag is not the only
  // path). Lanes are derived server-side from prep/docs state, so moving a card
  // means completing the state that lane requires.
  const [moveTo, setMoveTo] = useState("");
  const applyMove = async () => {
    if (!moveTo) return;
    if (moveTo === "prepped" || moveTo === "docs_ready") {
      const checklist = Object.fromEntries(Object.keys(card.checklist).filter((k) => k !== "charger_install").map((k) => [k, true]));
      await update({ checklist, ...(moveTo === "docs_ready" ? { docs_signed: true } : {}) },
        moveTo === "docs_ready" ? "Prep completed and documents marked signed — card moves to Docs ready" : "Prep checklist completed — card moves to Prepped");
    } else if (moveTo === "delivered") {
      await deliver();
    }
    setMoveTo("");
  };

  return (
    <Drawer open onClose={onClose} title={`Delivery · ${card.customer_name || card.customer_id}`}
            footer={canWrite && !done && (<>
              {/* what still blocks the handover, in view — a disabled button with a hover tooltip read as "broken" */}
              {missing.length > 0 && <span className="muted text-xs mr-2">Still open: {missing.join(", ")}</span>}
              <Btn kind="use" disabled={missing.length > 0 || post.pending} onClick={deliver}
                   title={missing.length ? `Still open: ${missing.join(", ")}` : undefined}>Mark delivered</Btn></>)}>
      <div>
        <InfoRow label="Journey" value={<span className="mono">{card.journey_id}</span>} />
        <InfoRow label="Vehicle" value={card.vehicle_name || card.vehicle_model_id} />
        <InfoRow label="VIN" value={card.vin ? <span className="mono">{card.vin}</span> : "not reserved"} />
        <InfoRow label="Slot" value={<DateTime value={card.scheduled_at} />} />
        <InfoRow label="Status" value={<Badge tone={done ? "green" : "blue"}>{titleCase(card.lane)}</Badge>} />
      </div>
      {/* after the handover, see it from the buyer's side: ownership, documents, delivery survey */}
      {done && <ViewAsCustomer customerId={card.customer_id} name={card.customer_name} />}

      {canWrite && !done && (
        <form className="flex gap-2 items-end" onSubmit={(e) => { e.preventDefault(); applyMove(); }}>
          <Field label="Move to lane" hint="Completes the prep / document state the lane requires">
            {(id) => (
              <select id={id} className="select" value={moveTo} onChange={(e) => setMoveTo(e.target.value)}>
                <option value="">Choose a lane…</option>
                <option value="prepped">Prepped — complete prep checklist</option>
                <option value="docs_ready">Docs ready — prep done + docs signed</option>
                <option value="delivered" disabled={missing.length > 0}>Delivered — hand over the keys</option>
              </select>
            )}
          </Field>
          <Btn kind="alt" type="submit" disabled={!moveTo || patch.pending || post.pending} className="mb-[.85rem]">Move</Btn>
        </form>
      )}

      <SectionLabel>Prep checklist</SectionLabel>
      <div className="flex flex-col gap-2" role="group" aria-label="Prep checklist">
        {Object.entries(card.checklist).filter(([k]) => k !== "charger_install").map(([k, v]) => (
          <label key={k} className="toggle">
            <input type="checkbox" checked={Boolean(v)} disabled={!canWrite || done || patch.pending}
                   onChange={(e) => update({ checklist: { [k]: e.target.checked } }, `${ITEM_LABEL[k] || k} ${e.target.checked ? "done" : "reopened"}`)} />
            {ITEM_LABEL[k] || k}
          </label>
        ))}
        {"charger_install" in card.checklist && (
          <Field label="Home charger install">
            {(id) => (
              <select id={id} className="select" value={card.charger_install || ""} disabled={!canWrite || done}
                      onChange={(e) => update({ charger_install: e.target.value }, "Charger install status updated")}>
                <option value="" disabled>Select…</option>
                <option value="not_needed">Not needed</option><option value="scheduled">Scheduled</option><option value="installed">Installed</option>
              </select>
            )}
          </Field>
        )}
      </div>

      <SectionLabel>Documents</SectionLabel>
      <label className="toggle">
        <input type="checkbox" checked={card.docs_signed} disabled={!canWrite || done}
               onChange={(e) => update({ docs_signed: e.target.checked }, e.target.checked ? "Documents marked signed" : "Documents reopened")} />
        All delivery documents signed
      </label>
      {card.documents.length ? card.documents.map((x: any) => (
        <InfoRow key={x.document_id} label={x.title || titleCase(x.kind)}
                 value={(x.shared_with || []).includes("customer") ? <Badge tone="green">in My Garage</Badge> : <Badge tone="gray">internal</Badge>} />
      )) : <div className="dim text-sm">No documents on this journey yet.</div>}

      <SectionLabel>Customer</SectionLabel>
      <label className="toggle">
        <input type="checkbox" checked={card.customer_confirmed} disabled={!canWrite || done}
               onChange={(e) => update({ customer_confirmed: e.target.checked }, e.target.checked ? "Customer confirmed" : "Confirmation cleared")} />
        Customer confirmed the delivery slot {card.confirmed_at && <span className="dim">(<DateTime value={card.confirmed_at} />)</span>}
      </label>
      {canWrite && !done && (
        <>
          <form className="flex gap-2 items-end" onSubmit={(e) => { e.preventDefault(); if (slot) update({ scheduled_at: fromZoneInput(slot, card.market_id) }, "Delivery rescheduled — customer notified"); }}>
            <Field label="Reschedule">{(id) => <input id={id} type="datetime-local" className="input" value={slot} onChange={(e) => setSlot(e.target.value)} />}</Field>
            <Btn kind="alt" type="submit" disabled={!slot || patch.pending} className="mb-[.85rem]">Reschedule</Btn>
          </form>
          <Field label="Delivery-day message" hint="Sent through the customer's preferred channel; consent and quiet hours apply.">
            {(id) => <textarea id={id} className="textarea" placeholder="Optional personal note" value={note} onChange={(e) => setNote(e.target.value)} />}
          </Field>
          <Btn kind="alt" onClick={message} disabled={post.pending}>Send delivery-day message</Btn>
        </>
      )}
      {missing.length > 0 && !done && <div className="dim text-xs" aria-live="polite">Before delivery: {missing.join(" · ")}</div>}

      <SectionLabel>Activity</SectionLabel>
      <JourneyActivityFeed journeyId={card.journey_id} limit={10} />
    </Drawer>
  );
}
