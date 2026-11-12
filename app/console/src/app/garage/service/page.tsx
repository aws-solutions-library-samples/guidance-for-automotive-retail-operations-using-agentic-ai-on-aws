"use client";

// Customer Web · My Garage › Service (★ Process 2, A7 — design §6.1, spec
// §5.12, §2.1.1): book service (due items + open recalls, slot picker,
// wait / drop / loaner / mobile options), the live service-journey tracker
// (8 stages, promised time, parts hold), declined work with "book it now",
// service history per VIN with invoice + inspection report links. Mobile-first.

import { humanizeCode } from "@/lib/text";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  Async, Badge, Btn, Card, DateTime, Distance, Drawer, EmptyState, InfoRow, Money,
  SectionLabel, ServiceStepper, stageLabel, useToast,
} from "@/components/app";
import { useApi, useMutation } from "@/lib/hooks";
import { BrandScope } from "../Brand";
import { Page, ViewHeader } from "@/components/app";

const WAIT_LABEL: Record<string, string> = {
  wait: "I'll wait at the dealership", drop: "Drop off", loaner: "Loaner vehicle",
  mobile: "Mobile service at my place", pickup_delivery: "Pickup & delivery",
};

export default function GarageServicePage() {
  return (
    <BrandScope>
      <ServiceView />
    </BrandScope>
  );
}

function ServiceView() {
  const q = useApi<any>("/garage/service", { pollMs: 60000 });
  const hist = useApi<any>("/garage/service/history");
  // the service team's heads-ups (A7 predictive leads) — shown where the customer books
  const own = useApi<any>("/garage/ownership");
  const offersFor = (vin: string): any[] => (own.data?.vehicles || []).find((x: any) => x.vin === vin)?.service_offers || [];
  const [bookFor, setBookFor] = useState<{ vin: string; services: string[]; menu?: string[] } | null>(null);
  return (
    <Page>
      <ViewHeader title="Service" subtitle="Book a visit, follow it live, and approve work from your phone."
                  actions={<Link href="/garage" className="btn btn-secondary btn-sm">My Garage</Link>} />
      <Async q={q}>
        {(d) => (
          <>
            {/* ---- live tracker ------------------------------------------ */}
            {d.active.length > 0 && (
              <Card title="In progress">
                <div className="flex flex-col gap-3">
                  {d.active.map((s: any) => (
                    <Link key={s.service_journey_id} href={`/garage/service/${encodeURIComponent(s.service_journey_id)}`}
                          className="rec-card p-4 flex flex-col gap-2 hover:no-underline">
                      <div className="flex justify-between flex-wrap gap-2">
                        <b>{s.dealer_name || "Your dealer"}</b>
                        {s.awaiting_approval ? <Badge tone="orange">Estimate waiting for you</Badge>
                          : s.parts_hold ? <Badge tone="orange">Waiting on parts</Badge>
                          : <Badge tone="blue">{stageLabel(s.stage)}</Badge>}
                      </div>
                      <div className="overflow-x-auto"><ServiceStepper current={s.stage} compact /></div>
                      {(s.promised_at || s.projected_at) && (
                        <div className="text-sm muted">Promised by <DateTime value={s.projected_at || s.promised_at} timeZone={s.timezone} /></div>
                      )}
                    </Link>
                  ))}
                </div>
              </Card>
            )}

            {/* ---- book service: due items + open recalls ----------------- */}
            <Card title="Book service">
              {d.vehicles.length === 0 ? <EmptyState>Vehicles you own will appear here after delivery.</EmptyState> : (
                <div className="grid gap-3 md:grid-cols-2">
                  {d.vehicles.map((v: any) => {
                    const recalls: any[] = v.recalls || [];
                    const due: string[] = v.recommended_services || [];
                    return (
                      <div key={v.vin} className="rec-card p-4 flex flex-col gap-2">
                        <div className="flex justify-between gap-2 flex-wrap">
                          <b>{v.year} {v.make} {v.model}</b>
                          {v.overdue && <Badge tone="red">Overdue</Badge>}
                        </div>
                        <InfoRow label="Next service" value={<DateTime value={v.due_by} mode="date" />} />
                        <InfoRow label="At" value={<Distance value={v.next_service_mileage} />} />
                        {due.length > 0 && <div className="text-xs muted">Due: {due.join(", ")}</div>}
                        {offersFor(v.vin).map((o: any) => (
                          <div key={o.part} className="ai-insight m-0" role="note">
                            <div className="ai-insight-title">🔮 Heads-up from your service team — {o.part}</div>
                            <p className="m-0 text-sm">{o.message}</p>
                            {o.booked
                              ? <div className="mt-2"><Badge tone="green">Booked for <DateTime value={o.booked.slot_start} /> · {o.offer_pct}% off applied</Badge></div>
                              : <div className="mt-2"><Btn kind="primary" size="sm"
                                  onClick={() => setBookFor({ vin: v.vin, services: Array.from(new Set([...(o.parts || [o.part]), ...due])), menu: v.bookable_services })}>
                                  Book with {o.offer_pct}% off</Btn>
                                  {o.expires_at && <span className="dim text-xs ml-2">Offer valid until <DateTime value={o.expires_at} mode="date" /></span>}</div>}
                          </div>
                        ))}
                        {recalls.length > 0 && (
                          <div className="text-xs" style={{ color: "var(--danger)" }}>
                            <b>{recalls.length} open recall{recalls.length === 1 ? "" : "s"}</b> — no charge to you:{" "}
                            {recalls.map((r: any) => r.title || r.campaign_id || r).join("; ")}
                          </div>
                        )}
                        <Btn kind="primary" size="lg"
                             onClick={() => setBookFor({ vin: v.vin, services: [...due, ...recalls.map((r: any) => `Recall: ${r.title || r.campaign_id || r}`)] })}>
                          Book this vehicle
                        </Btn>
                      </div>
                    );
                  })}
                </div>
              )}
            </Card>

            {/* ---- declined work ------------------------------------------ */}
            <Card title="Work you put off">
              {d.declined_work.length === 0 ? <EmptyState>Nothing waiting — you&apos;re all caught up.</EmptyState> : (
                <div className="flex flex-col gap-2">
                  {d.declined_work.map((w: any, i: number) => (
                    <div key={i} className="cross-sell-row" style={{ border: "1px solid var(--border)" }}>
                      <div>
                        <div className="cross-sell-name">{w.item}</div>
                        <div className="cross-sell-detail">Recommended <DateTime value={w.declined_at} mode="date" /></div>
                      </div>
                      <div className="flex items-center gap-2">
                        {w.amount != null && <Money value={w.amount} />}
                        {w.booked ? <Badge tone="green">Booked</Badge> : d.vehicles[0] && (
                          <Btn kind="present" onClick={() => setBookFor({ vin: w.vin || d.vehicles[0].vin, services: [w.item], menu: (d.vehicles.find((x: any) => x.vin === w.vin) || d.vehicles[0])?.bookable_services })}>Book it now</Btn>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          </>
        )}
      </Async>

      {/* ---- history per VIN with invoice + inspection links --------------- */}
      <Card title="Service history">
        <Async q={hist}>
          {(h) => h.vehicles.length === 0 ? <EmptyState>No service visits yet.</EmptyState> : h.vehicles.map((v: any) => (
            <div key={v.vin} className="mb-3">
              <SectionLabel><span className="mono">{v.vin}</span></SectionLabel>
              {v.visits.map((r: any) => (
                <InfoRow key={r.ro_number}
                         label={<span className="flex items-center gap-2 flex-wrap">
                           <DateTime value={r.opened_at} mode="date" /> · RO {r.ro_number} <Badge tone="gray">{humanizeCode(r.status)}</Badge>
                           {r.service_journey_id && (
                             <Link className="underline text-xs" href={`/garage/service/${encodeURIComponent(r.service_journey_id)}`}>
                               Invoice &amp; inspection report
                             </Link>
                           )}
                         </span>}
                         value={<Money value={r.total} />} />
              ))}
            </div>
          ))}
        </Async>
      </Card>

      {bookFor && (
        <BookingSheet vin={bookFor.vin} preset={bookFor.services} menu={bookFor.menu}
                      onClose={() => setBookFor(null)} onBooked={() => { setBookFor(null); q.reload(); own.reload(); }} />
      )}
    </Page>
  );
}

// ---- booking sheet: services, wait options, slot picker -------------------------

function BookingSheet({ vin, preset, menu, onClose, onBooked }: {
  vin: string; preset: string[]; menu?: string[]; onClose: () => void; onBooked: () => void }) {
  const slots = useApi<any>(`/garage/service/slots?vin=${encodeURIComponent(vin)}`);
  const m = useMutation();
  const toast = useToast();
  const [services, setServices] = useState<string[]>(preset);
  const [wait, setWait] = useState("drop");
  const [slot, setSlot] = useState<string>("");
  const days = useMemo(() => Array.from(new Set((slots.data?.slots || []).map((s: any) => s.date))) as string[], [slots.data]);
  const [day, setDay] = useState<string>("");
  useEffect(() => { if (!day && days[0]) setDay(days[0]); }, [days, day]);
  const toggle = (s: string) => setServices((xs) => (xs.includes(s) ? xs.filter((x) => x !== s) : [...xs, s]));
  const submit = async () => {
    const out: any = await m.run("/garage/service/book", { vin, services, wait_type: wait, slot_start: slot || undefined });
    if (out) {
      toast(out.offer_applied_pct ? `Service booked with ${out.offer_applied_pct}% off — we'll confirm by your preferred channel`
        : "Service booked — we'll confirm by your preferred channel", "success");
      onBooked();
    }
    else toast(m.error || "Could not book", "error");
  };
  // the menu is the car's (the server knows its powertrain): an EV is never offered an oil change
  const all = Array.from(new Set([...preset, ...(menu && menu.length ? menu : ["Multi-point inspection", "Tire rotation", "Brake inspection"])]));
  return (
    <Drawer open onClose={onClose} title="Book service"
            footer={<>
              <Btn kind="alt" onClick={onClose}>Cancel</Btn>
              <Btn kind="primary" size="lg" onClick={submit} disabled={m.pending || services.length === 0 || !slot}>
                {m.pending ? "Booking…" : "Confirm booking"}
              </Btn>
            </>}>
      <fieldset className="border-0 p-0 m-0">
        <legend className="text-xs font-semibold muted mb-2">What do you need?</legend>
        <div className="flex flex-col gap-2">
          {all.map((s) => (
            <label key={s} className="toggle min-h-[44px]">
              <input type="checkbox" checked={services.includes(s)} onChange={() => toggle(s)} /> {s}
              {s.startsWith("Recall:") && <Badge tone="red">No charge</Badge>}
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset className="border-0 p-0 m-0">
        <legend className="text-xs font-semibold muted mb-2">While we work on it</legend>
        <div className="flex flex-col gap-2">
          {Object.entries(WAIT_LABEL).map(([k, l]) => (
            <label key={k} className="toggle min-h-[44px]">
              <input type="radio" name="wait" checked={wait === k} onChange={() => setWait(k)} /> {l}
            </label>
          ))}
        </div>
      </fieldset>
      <div>
        <div className="text-xs font-semibold muted mb-2">When{slots.data?.dealer_name ? ` at ${slots.data.dealer_name}` : ""}</div>
        <Async q={slots}>
          {() => (
            <>
              <div className="flex gap-2 overflow-x-auto pb-2">
                {days.map((d) => (
                  <button key={d} className={`filter-pill min-h-[44px] ${d === day ? "active" : ""}`} onClick={() => { setDay(d); setSlot(""); }}>
                    <DateTime value={`${d}T12:00:00Z`} mode="date" />
                  </button>
                ))}
              </div>
              <div className="grid grid-cols-3 gap-2">
                {(slots.data?.slots || []).filter((s: any) => s.date === day).map((s: any) => (
                  <button key={s.slot_start} className={`filter-pill min-h-[44px] ${slot === s.slot_start ? "active" : ""}`}
                          onClick={() => setSlot(s.slot_start)} aria-pressed={slot === s.slot_start}>
                    <DateTime value={s.slot_start} mode="time" timeZone={slots.data?.timezone} />
                  </button>
                ))}
              </div>
            </>
          )}
        </Async>
      </div>
    </Drawer>
  );
}
