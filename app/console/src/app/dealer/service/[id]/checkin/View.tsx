"use client";

// Check-in / walk-around (§5.20.2, design doc §3.1) — tablet-first: arrival,
// vehicle/VIN confirm, mileage, concerns typed or dictated (useVoiceAssistant,
// AR-7), walk-around photos with damage tags (camera capture; queued offline
// and retried), approval preference, promised time, prior declined work
// one-tap "add to RO", then "Open RO" (A7 open_repair_order mirrors the DMS RO).

import { fromZoneInput, toZoneInput } from "@/lib/market";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Badge, Btn, Card, DateTime, Distance, Field, FilterPills, IncentiveRow, LoadingBlock, Money, SourceRef, ThreeColumn, Panel, useToast, stageLabel } from "@/components/app";
import { apiPost } from "@/lib/api";
import { useApi, useRouteId } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";
import { useVoiceAssistant } from "@/lib/voice";
import { ServiceFrame, useServiceJourney } from "../../common";

interface Photo { name: string; tag: string; note?: string; thumb: string }
const QUEUE_KEY = "retail-ops.checkin.queue";
const DAMAGE_TAGS = ["no damage", "scratch", "dent", "chip", "curb rash", "crack", "other"];

async function toThumb(file: File, max = 320): Promise<string> {
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((ok, bad) => { const i = new Image(); i.onload = () => ok(i); i.onerror = bad; i.src = url; });
    const scale = Math.min(1, max / Math.max(img.width, img.height));
    const c = document.createElement("canvas");
    c.width = Math.round(img.width * scale); c.height = Math.round(img.height * scale);
    c.getContext("2d")!.drawImage(img, 0, 0, c.width, c.height);
    return c.toDataURL("image/jpeg", 0.6);
  } finally { URL.revokeObjectURL(url); }
}

/** A7 check-in brief (§3.7 prepare_ro_brief): due items, open recalls,
 *  coverage, and pre-drafted lines prepared at BOOK / 05:00 — not at check-in.
 *  Degrades to the recorded items when the brief endpoint isn't available. */
function RoBrief({ sj }: { sj: any }) {
  const q = useApi<any>(`/service/journeys/${encodeURIComponent(sj.service_journey_id)}/brief`);
  const b = q.data;
  return (
    <div aria-live="polite">
      <div className="section-label flex items-center gap-2"><span>Service Agent check-in brief</span><span className="panel-badge badge-ai">Service Agent</span></div>
      {q.loading && <LoadingBlock rows={2} />}
      {q.error && !b && (
        <div className="dim text-xs">
          Service Agent brief not available from the API yet ({q.error}) — showing recorded concerns and declined work only.
        </div>
      )}
      {b && (
        <div className="flex flex-col gap-2">
          {b.summary && <div className="insight-box"><div className="insight-label">Brief</div><div className="text-sm">{b.summary}</div></div>}
          {(b.due_items || []).map((it: any, i: number) => (
            <IncentiveRow key={`due-${i}`} eligible name={<>Due: {it.item || it.name}</>}
                          amount={<SourceRef refId={it.source_ref} />} />
          ))}
          {(b.open_recalls || []).map((r: any, i: number) => (
            <IncentiveRow key={`rc-${i}`} eligible name={<><span aria-hidden>⚠</span> Recall: {r.title || r.recall_id}</>}
                          amount={<SourceRef refId={r.source_ref} />} />
          ))}
          {(b.predrafted_lines || []).length > 0 && (
            <div className="text-xs muted">
              {b.predrafted_lines.length} estimate line(s) pre-drafted from known items — review them on the Estimate tab after the inspection.
            </div>
          )}
          {b.coverage?.name && <div className="text-xs muted">Coverage on file: {b.coverage.name} <SourceRef refId={b.coverage.source_ref} /></div>}
          {b.prepared_at && <div className="dim text-[.68rem]">Prepared <DateTime value={b.prepared_at} /> by the Service Agent</div>}
        </div>
      )}
    </div>
  );
}

export default function CheckinView() {
  const id = useRouteId(2);
  const q = useServiceJourney(id, 0);
  return <ServiceFrame id={id} q={q}>{(sj) => <Checkin sj={sj} reload={q.reload} />}</ServiceFrame>;
}

function Checkin({ sj, reload }: { sj: any; reload: () => void }) {
  const router = useRouter();
  const toast = useToast();
  const { persona } = usePersona();
  const [mileage, setMileage] = useState<string>(sj.vehicle?.mileage_in ? String(sj.vehicle.mileage_in) : "");
  const [concerns, setConcerns] = useState<string[]>(sj.concerns?.length ? sj.concerns : [""]);
  const [pref, setPref] = useState<string>(sj.customer?.approval_preference || "text");
  const [promised, setPromised] = useState<string>(toZoneInput(sj.promised_at, sj.market_id));
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [addDeclined, setAddDeclined] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [queued, setQueued] = useState(0);
  const file = useRef<HTMLInputElement>(null);
  const locale = persona?.marketId === "de" ? "de-DE" : "en-US";
  const voice = useVoiceAssistant({ language: locale, continuous: true,
    onFinal: (t) => setConcerns((cs) => { const n = [...cs]; const last = n.length - 1; n[last] = n[last] ? `${n[last]} ${t}` : t; return n; }) });

  const readQueue = (): any[] => { try { return JSON.parse(localStorage.getItem(QUEUE_KEY) || "[]"); } catch { return []; } };
  const flush = async () => {
    const items = readQueue();
    if (!items.length) return;
    const left: any[] = [];
    for (const it of items) {
      try { await apiPost(`/service/journeys/${encodeURIComponent(it.sj)}/checkin`, it.body); toast(`Queued check-in for ${it.sj} sent`, "success"); }
      catch (e: any) { if (e?.status) toast(`Queued check-in rejected: ${e.message}`, "error"); else left.push(it); }
    }
    localStorage.setItem(QUEUE_KEY, JSON.stringify(left));
    setQueued(left.length);
    if (left.length !== items.length) reload();
  };
  useEffect(() => {
    setQueued(readQueue().length);
    window.addEventListener("online", flush);
    return () => window.removeEventListener("online", flush);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const addPhotos = async (files: FileList | null) => {
    if (!files) return;
    const next: Photo[] = [];
    for (const f of Array.from(files)) {
      try { next.push({ name: f.name, tag: "no damage", thumb: await toThumb(f) }); }
      catch { toast(`Could not read ${f.name}`, "error"); }
    }
    setPhotos((p) => [...p, ...next]);
  };

  const submit = async () => {
    const body = {
      mileage: Number(mileage), concerns: concerns.filter((c) => c.trim()), approval_preference: pref,
      promised_at: promised ? fromZoneInput(promised, sj.market_id) : undefined,
      photos: photos.map(({ name, tag, note, thumb }) => ({ name, tag, note, thumb })), add_declined: addDeclined,
    };
    setBusy(true);
    try {
      const out = await apiPost<any>(`/service/journeys/${encodeURIComponent(sj.service_journey_id)}/checkin`, body);
      toast(`Checked in — RO ${out.ro?.ro_id} opened`, "success");
      router.push(`/dealer/service/${encodeURIComponent(sj.service_journey_id)}`);
    } catch (e: any) {
      if (!e?.status) {   // network failure → offline queue
        const items = readQueue();
        items.push({ sj: sj.service_journey_id, body, at: Date.now() });
        localStorage.setItem(QUEUE_KEY, JSON.stringify(items));
        setQueued(items.length);
        toast("You're offline — check-in saved and will send when you reconnect", "info");
      } else toast(e.message || "Check-in failed", "error");
    } finally { setBusy(false); }
  };

  const valid = Number(mileage) > 0;
  const atStage = ["BOOK", "CHECK_IN"].includes(sj.stage);

  return (
    <ThreeColumn
      left={
        <Panel title="Vehicle & customer" badge={stageLabel(sj.stage)} badgeKind="live">
          <div className="customer-card"><div>
            <div className="customer-name">{sj.customer?.name || "Customer"}</div>
            <div className="customer-meta">{sj.customer?.phone} · {sj.customer?.email}</div>
          </div></div>
          <div className="info-row"><span className="info-label">VIN</span><span className="info-value mono">{sj.vehicle?.vin}</span></div>
          <div className="info-row"><span className="info-label">Last odometer</span><span className="info-value"><Distance value={sj.vehicle?.odometer} /></span></div>
          <div className="info-row"><span className="info-label">Wait type</span><span className="info-value">{sj.wait_type?.replace("_", " ")}</span></div>
          {(sj.declined_work || []).length > 0 && (
            <>
              <div className="section-label">Previously declined — add to this RO?</div>
              {sj.declined_work.filter((d: any) => d.status !== "added_to_ro").map((d: any) => {
                const on = addDeclined.includes(d.declined_id);
                return (
                  <IncentiveRow key={d.declined_id} eligible={on} name={d.item} amount={<Money value={d.amount} />}
                    right={<Btn kind={on ? "success" : "alt"} size="sm" onClick={() => setAddDeclined((xs) => on ? xs.filter((x) => x !== d.declined_id) : [...xs, d.declined_id])}>{on ? "✓ Added" : "Add to RO"}</Btn>} />
                );
              })}
            </>
          )}
          {queued > 0 && <div className="objection-alert"><div className="objection-label">Offline queue</div>{queued} check-in(s) waiting to send · <button className="underline" onClick={flush}>Retry now</button></div>}
          <RoBrief sj={sj} />
        </Panel>
      }
      center={
        <section className="panel"><div className="panel-header"><h2>Walk-around</h2>{!atStage && <Badge tone="orange">already past check-in</Badge>}</div>
          <div className="panel-body">
            <Field label="Mileage in" error={mileage && !valid ? "Enter the odometer reading" : null}>{(fid) => (
              <input id={fid} className="input" style={{ minHeight: 52, fontSize: "1.2rem" }} inputMode="numeric" value={mileage}
                     onChange={(e) => setMileage(e.target.value.replace(/[^\d.]/g, ""))} placeholder="Odometer" />
            )}</Field>

            <div className="section-label flex items-center justify-between">
              <span>Customer concerns</span>
              {voice.engine !== "none" && (
                <button className={`mic-btn${voice.listening ? " listening" : ""}`} onClick={voice.toggle} aria-pressed={voice.listening}
                        aria-label={voice.listening ? "Stop dictation" : "Dictate concerns"}>🎤</button>
              )}
            </div>
            {voice.listening && <div className="text-xs" aria-live="polite" style={{ color: "var(--primary-light)" }}>Listening… {voice.interim}</div>}
            {concerns.map((c, i) => (
              <div key={i} className="flex gap-2">
                <textarea className="textarea" style={{ minHeight: 64 }} value={c} aria-label={`Concern ${i + 1}`}
                          onChange={(e) => setConcerns((cs) => cs.map((x, j) => (j === i ? e.target.value : x)))} placeholder="Describe the concern in the customer's words" />
                {concerns.length > 1 && <Btn kind="ghost" onClick={() => setConcerns((cs) => cs.filter((_, j) => j !== i))} aria-label="Remove concern">✕</Btn>}
              </div>
            ))}
            <Btn kind="alt" size="lg" onClick={() => setConcerns((cs) => [...cs, ""])}>＋ Add concern</Btn>

            <div className="section-label">Walk-around photos</div>
            <input ref={file} type="file" accept="image/*" capture="environment" multiple className="sr-only" onChange={(e) => { addPhotos(e.target.files); e.target.value = ""; }} />
            <Btn kind="secondary" size="lg" onClick={() => file.current?.click()}>📷 Capture photo</Btn>
            <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill,minmax(150px,1fr))" }}>
              {photos.map((p, i) => (
                <figure key={i} className="rec-card m-0">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={p.thumb} alt={`Walk-around ${i + 1}: ${p.tag}`} className="w-full h-24 object-cover" />
                  <figcaption className="p-2 flex flex-col gap-1">
                    <select className="select" value={p.tag} aria-label="Damage tag" onChange={(e) => setPhotos((ps) => ps.map((x, j) => j === i ? { ...x, tag: e.target.value } : x))}>
                      {DAMAGE_TAGS.map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
                    <Btn kind="ghost" size="sm" onClick={() => setPhotos((ps) => ps.filter((_, j) => j !== i))}>Remove</Btn>
                  </figcaption>
                </figure>
              ))}
            </div>
          </div>
        </section>
      }
      right={
        <Panel title="Approval & promise" badge="Open RO" badgeKind="active">
          <div className="section-label">How should we get approval?</div>
          <FilterPills value={pref} onChange={setPref} options={[
            { id: "text", label: "Text" }, { id: "call", label: "Call" }, { id: "my_garage", label: "My Garage" }, { id: "email", label: "Email" },
          ]} />
          <Field label="Promised time">{(fid) => <input id={fid} type="datetime-local" className="input" style={{ minHeight: 48 }} value={promised} onChange={(e) => setPromised(e.target.value)} />}</Field>
          <Card>
            <div className="text-sm">Opening the RO mirrors it in the DMS and moves the journey to <b>CHECK-IN</b>. The technician then sees it in <b>My ROs</b>.</div>
          </Card>
          <Btn kind="primary" size="lg" onClick={submit} disabled={!valid || busy || !atStage}>{busy ? "Opening RO…" : "Open RO"}</Btn>
        </Panel>
      }
    />
  );
}
