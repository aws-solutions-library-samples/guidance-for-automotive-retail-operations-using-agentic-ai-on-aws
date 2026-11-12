"use client";

// Book service (§5.20.2) — tablet-first. VIN lookup (global search), due items,
// open recalls, and prior declined work pre-filled by A7 (predict_next_service,
// check_coverage); lane capacity per slot; wait / drop / loaner / mobile;
// confirmation via the outreach provider. Ends on the check-in screen.

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  Async, Badge, Btn, Card, DateTime, Distance, EmptyState, ErrorState, Field, FilterPills, IncentiveRow, MarketProvider,
  Money, Page, ViewHeader, useToast,
} from "@/components/app";
import { apiGet } from "@/lib/api";
import { useApi, useMutation } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";

type Wait = "wait" | "drop" | "loaner" | "mobile" | "pickup_delivery";

export default function BookService() {
  const toast = useToast();
  const { persona } = usePersona();
  const [term, setTerm] = useState("");
  const [hits, setHits] = useState<any[]>([]);
  const [vin, setVin] = useState<string | null>(null);
  const [date, setDate] = useState(() => new Date(Date.now() + 86400000).toISOString().slice(0, 10));
  const [slot, setSlot] = useState<string | null>(null);
  const [wait, setWait] = useState<Wait>("drop");
  const [services, setServices] = useState<string[]>([]);
  const [extra, setExtra] = useState("");
  const [booked, setBooked] = useState<any>(null);
  const book = useMutation();

  useEffect(() => {
    const p = new URLSearchParams(window.location.search).get("vin");
    if (p) setVin(p);
  }, []);
  useEffect(() => {
    if (term.trim().length < 3) { setHits([]); return; }
    const t = setTimeout(() => apiGet<any>(`/search?q=${encodeURIComponent(term.trim())}&types=vin`)
      .then((d) => setHits((d.results || []).filter((r: any) => r.type === "vin"))).catch(() => setHits([])), 250);
    return () => clearTimeout(t);
  }, [term]);

  const due = useApi<any>(vin ? `/service/due?vin=${encodeURIComponent(vin)}` : null);
  const dealer = due.data?.vehicle?.dealer_id;
  const avail = useApi<any>(dealer ? `/service/availability?dealer=${dealer}&date=${date}` : null);
  // §3.7 book_service: `express` service class — fixed menu, no MPI. The menu
  // comes from the scheduling connector; degrade to free selection without it.
  const [svcClass, setSvcClass] = useState<"standard" | "express">("standard");
  const exMenu = useApi<any>(svcClass === "express" && dealer ? `/service/express-menu?dealer=${dealer}` : null);

  useEffect(() => {
    const d = due.data;
    if (!d || svcClass !== "standard") return;
    const pre = ["Scheduled maintenance"];
    for (const r of d.open_recalls || []) pre.push(`Recall: ${r.title || r.recall_id}`);
    for (const dw of d.declined_open || []) pre.push(`Declined: ${dw.item}`);
    setServices(Array.from(new Set(pre.filter(Boolean))));
  }, [due.data, svcClass]);
  useEffect(() => {
    const items: any[] = exMenu.data?.items || [];
    if (svcClass === "express" && items.length) setServices(items.map((m: any) => m.name || String(m)));
  }, [svcClass, exMenu.data]);

  // §3.7: offer only wait types with capacity on that slot (loaner count,
  // mobile van, pickup-delivery driver) — shown when the connector reports them.
  const capZero =
    (wait === "loaner" && avail.data?.loaners?.available === 0) ||
    (wait === "mobile" && avail.data?.mobile?.available === 0) ||
    (wait === "pickup_delivery" && avail.data?.drivers?.available === 0);

  const confirm = async () => {
    if (!vin || !slot) return;
    const all = [...services, ...extra.split(",").map((s) => s.trim()).filter(Boolean)];
    const out = await book.run("/service/bookings", { vin, services: all, wait_type: wait, slot_start: slot, service_class: svcClass });
    if (out) { setBooked(out); toast("Service booked — confirmation sent to the customer", "success"); }
    else toast(book.error || "Booking failed", "error");
  };

  if (booked) {
    return (
      <Page>
        <ViewHeader title="Booked" subtitle={<>Appointment {booked.appointment_id} · <DateTime value={booked.slot_start} timeZone={avail.data?.timezone} /></>} />
        <Card accent="success">
          <p className="mb-3">Service journey <b>{booked.service_journey_id}</b> is in BOOK. The customer receives the confirmation on their preferred channel.</p>
          <div className="flex gap-2 flex-wrap">
            <Link className="btn btn-primary btn-lg" href={`/dealer/service/${encodeURIComponent(booked.service_journey_id)}/checkin`}>Check in now</Link>
            <Link className="btn btn-secondary btn-lg" href="/dealer/service">Back to board</Link>
            <Btn kind="ghost" onClick={() => { setBooked(null); setVin(null); setSlot(null); setTerm(""); }}>Book another</Btn>
          </div>
        </Card>
      </Page>
    );
  }

  return (
    <MarketProvider marketId={persona?.marketId}>
      <Page>
        <ViewHeader title="Book service" subtitle={<>Find the vehicle, confirm what&apos;s due, pick a slot.</>} />
        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="1 · Vehicle">
            <Field label="VIN or stock number">{(id) => (
              <input id={id} className="input" style={{ minHeight: 44 }} value={term} placeholder="Type at least 3 characters…"
                     onChange={(e) => setTerm(e.target.value)} autoFocus />
            )}</Field>
            {hits.length > 0 && !vin && (
              <div className="flex flex-col gap-1" role="listbox" aria-label="Vehicles">
                {hits.slice(0, 8).map((h) => (
                  <button key={h.id} role="option" aria-selected={false} className="vehicle-item text-left" onClick={() => setVin(h.id)}>
                    <span className="mono">{h.label}</span><span className="muted text-xs ml-2">{h.detail}</span>
                  </button>
                ))}
              </div>
            )}
            {vin && (
              <div className="flex items-center justify-between gap-2 mt-2">
                <div><span className="mono">{vin}</span> {due.data?.vehicle?.name && <Badge tone="blue">{due.data.vehicle.name}</Badge>}</div>
                <Btn kind="ghost" size="sm" onClick={() => { setVin(null); setSlot(null); }}>Change</Btn>
              </div>
            )}
            {vin && (
              <Async q={due} rows={3}>
                {(d) => (
                  <div className="flex flex-col gap-2 mt-3">
                    <div className="section-label">Due & open items</div>
                    {d.next_service && (
                      <IncentiveRow eligible name={<>Scheduled maintenance at <Distance value={d.next_service.next_service_mileage} /></>}
                                    amount={<>due by <DateTime value={d.next_service.due_by} mode="date" /> · basis {d.next_service.basis}</>} />
                    )}
                    <IncentiveRow eligible={!!d.coverage?.covered} name={d.coverage?.covered ? `Covered: ${d.coverage.coverage?.name}` : "No prepaid maintenance on file"}
                                  amount={<span className="data-source">{d.coverage?.source_ref}</span>} />
                    {(d.open_recalls || []).map((r: any) => (
                      <IncentiveRow key={r.recall_id || r.title} eligible name={<><span aria-hidden>⚠</span> Open recall: {r.title || r.recall_id}</>}
                                    amount={<>{r.remedy || "remedy at dealer"} {r.source_ref && <span className="data-source">{r.source_ref}</span>}</>} />
                    ))}
                    {(d.declined_open || []).map((dw: any) => (
                      <IncentiveRow key={dw.declined_id} eligible={false} name={`Previously declined: ${dw.item}`} amount={<Money value={dw.amount} />} />
                    ))}
                    {d.open_recalls?.length === 0 && <div className="dim text-xs">No open recalls · {d.recalls_source_ref}</div>}
                  </div>
                )}
              </Async>
            )}
          </Card>

          <Card title="2 · Services & wait type">
            <div className="section-label mb-2">Service class</div>
            <FilterPills value={svcClass} onChange={setSvcClass} options={[
              { id: "standard", label: "Standard" }, { id: "express", label: "Express (no MPI)" },
            ]} />
            {svcClass === "express" && (
              <div className="text-xs muted mb-2" role="status">
                Express: fixed-menu work only, no multi-point inspection.
                {exMenu.error && <> Express menu not connected ({exMenu.error}) — pick services manually.</>}
              </div>
            )}
            {services.length === 0 && <EmptyState>Select a vehicle to pre-fill due items.</EmptyState>}
            <div className="flex flex-col gap-2">
              {services.map((s) => (
                <label key={s} className="toggle" style={{ minHeight: 40 }}>
                  <input type="checkbox" checked onChange={() => setServices((xs) => xs.filter((x) => x !== s))} /> {s}
                </label>
              ))}
            </div>
            <Field label="Customer concern / extra services" hint="Comma-separated">{(id) => (
              <input id={id} className="input" value={extra} onChange={(e) => setExtra(e.target.value)} placeholder="e.g. brake noise, tire rotation" />
            )}</Field>
            <div className="section-label mb-2">Wait type</div>
            <FilterPills value={wait} onChange={setWait} options={[
              { id: "wait", label: "Wait" }, { id: "drop", label: "Drop-off" },
              { id: "loaner", label: `Loaner${avail.data?.loaners ? ` (${avail.data.loaners.available} left)` : ""}` },
              { id: "mobile", label: `Mobile${avail.data?.mobile ? ` (${avail.data.mobile.available} vans)` : ""}` },
              { id: "pickup_delivery", label: `Pickup & delivery${avail.data?.drivers ? ` (${avail.data.drivers.available} drivers)` : ""}` },
            ]} />
            {capZero && <ErrorState error={`No ${wait.replace("_", " ")} capacity that day — pick another wait type or date.`} />}
            {["mobile", "pickup_delivery"].includes(wait) && avail.data && !avail.data.mobile && !avail.data.drivers && (
              <div className="dim text-xs">Mobile / driver capacity isn&apos;t reported by the scheduling connector yet — the advisor confirms it manually.</div>
            )}
          </Card>
        </div>

        <Card title="3 · Slot">
          <div className="flex items-end gap-3 mb-3">
            <Field label="Date">{(id) => <input id={id} type="date" className="input" value={date} onChange={(e) => { setDate(e.target.value); setSlot(null); }} />}</Field>
          </div>
          {!dealer ? <EmptyState>Select a vehicle first.</EmptyState> : (
            <Async q={avail} rows={2}>
              {(a) => (
                <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fill,minmax(120px,1fr))" }} role="radiogroup" aria-label="Appointment slot">
                  {a.slots.map((s: any) => (
                    <button key={s.slot_start} role="radio" aria-checked={slot === s.slot_start} disabled={!s.available}
                            className={`btn ${slot === s.slot_start ? "btn-primary" : "btn-secondary"} btn-lg flex-col`}
                            onClick={() => setSlot(s.slot_start)}>
                      <DateTime value={s.slot_start} mode="time" timeZone={avail.data?.timezone} />
                      <span className="text-[.65rem] opacity-80">{s.available}/{s.capacity} bays</span>
                    </button>
                  ))}
                </div>
              )}
            </Async>
          )}
          <div className="flex justify-end mt-4">
            <Btn kind="primary" size="lg" onClick={confirm} disabled={!vin || !slot || book.pending || capZero}>
              {book.pending ? "Booking…" : "Confirm booking"}
            </Btn>
          </div>
        </Card>
      </Page>
    </MarketProvider>
  );
}
