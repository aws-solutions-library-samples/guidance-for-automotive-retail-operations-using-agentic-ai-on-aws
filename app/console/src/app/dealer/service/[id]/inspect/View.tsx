"use client";

// Inspect & Diagnose (§5.20.3, design doc §3.2) — technician tablet, three
// columns. Left: MPI checklist for this model/powertrain (EV template includes
// HV battery SoH, charge port, coolant loops) with green / yellow / red chips
// carrying text labels, measurement, and evidence (photo/video/voice —
// required for yellow/red; the server rejects without it). Center: concerns
// and the Diagnose panel (DTC → lookup_dtc desc·causes·cited TSBs → labor op →
// get_repair_procedure/get_labor_time/lookup_parts → draft line). Right:
// accumulating draft estimate with coverage badge, read-only pricing. Submit →
// record_inspection + build_estimate. Large touch targets (btn-lg).

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ActivityItem, Async, Badge, Btn, EmptyState, ErrorState, IncentiveRow, Panel, StatusChip, ThreeColumn, cx, useToast,
} from "@/components/app";
import { apiGet, apiPost } from "@/lib/api";
import { useApi, useRouteId } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";
import { useVoiceAssistant } from "@/lib/voice";
import { ServiceFrame, useServiceJourney } from "../../common";

type Status = "green" | "yellow" | "red";
interface Row { item: string; unit?: string | null; group?: string; hv?: boolean; status?: Status; measurement?: string;
  note?: string; evidence: string[]; dtc_codes?: string[]; custom?: boolean }

export default function InspectView() {
  const id = useRouteId(2);
  const q = useServiceJourney(id, 0);
  return <ServiceFrame id={id} q={q}>{(sj) => <Inspect sj={sj} />}</ServiceFrame>;
}

function Inspect({ sj }: { sj: any }) {
  const router = useRouter();
  const toast = useToast();
  const { persona } = usePersona();
  const tpl = useApi<any>(`/service/inspection-templates?model_id=${encodeURIComponent(sj.vehicle?.model_id || "")}&market=${sj.market_id || "us"}`);
  const [templateId, setTemplateId] = useState<string>("");
  const [rows, setRows] = useState<Row[]>([]);
  const [busy, setBusy] = useState(false);
  const [active, setActive] = useState<number | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const audioRef = useRef<HTMLInputElement>(null);
  const voice = useVoiceAssistant({ language: sj.market_id === "de" ? "de-DE" : "en-US", continuous: true,
    onFinal: (t) => active != null && setRows((rs) => rs.map((r, i) => i === active ? { ...r, note: r.note ? `${r.note} ${t}` : t, evidence: Array.from(new Set([...r.evidence, `voice:${r.item}`])) } : r)) });

  const template = useMemo(() => (tpl.data?.templates || []).find((t: any) => t.template_id === (templateId || tpl.data?.recommended)), [tpl.data, templateId]);

  useEffect(() => {
    if (!template) return;
    const prior = new Map<string, any>((sj.inspection || []).map((i: any) => [i.item, i]));
    setRows(template.items.map((it: any) => {
      const p = prior.get(it.item);
      return { ...it, status: p?.status, measurement: p?.measurement != null ? String(p.measurement) : "", note: p?.note || "",
               evidence: p?.evidence_doc_id ? [p.evidence_doc_id] : [] };
    }));
  }, [template, sj.inspection]);

  const set = (i: number, patch: Partial<Row>) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const missingEvidence = rows.filter((r) => (r.status === "yellow" || r.status === "red") && r.evidence.length === 0);
  const unrated = rows.filter((r) => !r.status);
  const actionable = rows.filter((r) => r.status === "yellow" || r.status === "red");

  const capture = (files: FileList | null) => {
    if (!files || active == null) return;
    const ids = Array.from(files).map((f) => `${f.type.startsWith("video") ? "video" : f.type.startsWith("audio") ? "voice" : "photo"}:${Date.now().toString(36)}-${f.name}`);
    set(active, { evidence: Array.from(new Set([...rows[active].evidence, ...ids])) });
    toast(`${ids.length} file(s) attached to ${rows[active].item}`, "success");
  };

  const submit = async () => {
    if (!sj.repair_order?.ro_id) { toast("Open the RO at check-in first", "error"); return; }
    setBusy(true);
    try {
      const findings = rows.filter((r) => r.status).map((r) => ({
        item: r.item, status: r.status, measurement: r.measurement ? Number(r.measurement) : undefined, unit: r.unit,
        note: r.note || undefined, evidence: r.evidence, dtc_codes: r.dtc_codes, urgency: r.status === "red" ? "safety" : r.status === "yellow" ? "soon" : "none",
      }));
      await apiPost(`/service/ros/${encodeURIComponent(sj.repair_order.ro_id)}/inspection`, { findings, template_id: template?.template_id });
      const est = await apiPost<any>(`/service/ros/${encodeURIComponent(sj.repair_order.ro_id)}/estimate`, {});
      toast(`Inspection submitted · draft estimate with ${est.lines?.length ?? 0} line(s)`, "success");
      router.push(`/dealer/service/${encodeURIComponent(sj.service_journey_id)}`);
    } catch (e: any) {
      toast(e?.message || "Submit failed", "error");
    } finally { setBusy(false); }
  };

  return (
    <ThreeColumn
      left={
        <Panel title="Multi-point inspection" badge={template?.name || "template"} badgeKind="live"
               actions={(tpl.data?.templates || []).length > 1 ? (
                 <select className="select" style={{ width: 150 }} aria-label="Template" value={template?.template_id || ""} onChange={(e) => setTemplateId(e.target.value)}>
                   {tpl.data.templates.filter((t: any) => t.has_mpi).map((t: any) => <option key={t.template_id} value={t.template_id}>{t.name}</option>)}
                 </select>) : null}>
          <Async q={tpl} rows={6}>
            {() => rows.length ? (
              <div className="flex flex-col gap-2">
                {rows.map((r, i) => (
                  <div key={r.item} className={cx("rec-card", active === i && "border-indigo-400")} onFocus={() => setActive(i)} onClick={() => setActive(i)}>
                    <div className="rec-card-header">
                      <div className="rec-card-title">{r.hv && <Badge tone="orange">HV</Badge>} {r.item}</div>
                      {r.status && <StatusChip status={r.status} />}
                    </div>
                    <div className="rec-card-body flex flex-col gap-2">
                      <div className="flex gap-2" role="radiogroup" aria-label={`${r.item} status`}>
                        {(["green", "yellow", "red"] as Status[]).map((st) => (
                          <button key={st} role="radio" aria-checked={r.status === st} onClick={() => set(i, { status: st })}
                                  className={cx("btn btn-lg flex-1", r.status === st ? (st === "green" ? "btn-success" : st === "red" ? "btn-danger" : "btn-primary") : "btn-secondary")}
                                  style={r.status === st && st === "yellow" ? { background: "var(--warning)", color: "#111" } : undefined}>
                            {st}
                          </button>
                        ))}
                      </div>
                      {r.unit && (
                        <label className="flex items-center gap-2 text-sm">Measurement
                          <input className="input" style={{ maxWidth: 110, minHeight: 44 }} inputMode="decimal" value={r.measurement || ""}
                                 onChange={(e) => set(i, { measurement: e.target.value })} aria-label={`${r.item} measurement`} /> {r.unit}
                        </label>
                      )}
                      {(r.status === "yellow" || r.status === "red") && (
                        <div className="flex flex-wrap items-center gap-2">
                          <Btn kind="alt" size="sm" onClick={() => { setActive(i); fileRef.current?.click(); }}>📷 Photo / video</Btn>
                          {voice.engine !== "none" && <Btn kind="alt" size="sm" onClick={() => { setActive(i); voice.toggle(); }}>{voice.listening && active === i ? "■ Stop note" : "🎤 Voice note"}</Btn>}
                          {/* no microphone (a demo browser, a denied permission): attach a recording, or the typed note */}
                          <Btn kind="alt" size="sm" onClick={() => { setActive(i); audioRef.current?.click(); }}>🎧 Attach audio</Btn>
                          <Btn kind="alt" size="sm" disabled={!(r.note || "").trim() || r.evidence.includes(`note:${r.item}`)}
                               title={(r.note || "").trim() ? "Record the typed note as this finding's evidence" : "Type a technician note first"}
                               onClick={() => { set(i, { evidence: Array.from(new Set([...r.evidence, `note:${r.item}`])) }); toast(`Note attached to ${r.item}`, "success"); }}>
                            🗒 Note as evidence</Btn>
                          {r.evidence.length ? <Badge tone="green">{r.evidence.length} evidence</Badge> : <Badge tone="red">evidence required</Badge>}
                        </div>
                      )}
                      {voice.error && active === i && (
                        <div className="dim text-xs" role="status">Voice note unavailable ({voice.error}) — attach an audio file or use the typed note as evidence.</div>
                      )}
                      {(r.status === "yellow" || r.status === "red" || r.note) && (
                        <textarea className="textarea" style={{ minHeight: 48 }} placeholder="Technician note" value={r.note || ""}
                                  onChange={(e) => set(i, { note: e.target.value })} aria-label={`${r.item} note`} />
                      )}
                    </div>
                  </div>
                ))}
                <input ref={fileRef} type="file" accept="image/*,video/*" capture="environment" multiple className="sr-only" onChange={(e) => { capture(e.target.files); e.target.value = ""; }} />
                <input ref={audioRef} type="file" accept="audio/*" multiple className="sr-only" aria-label="Attach audio note" onChange={(e) => { capture(e.target.files); e.target.value = ""; }} />
              </div>
            ) : <EmptyState>No template items.</EmptyState>}
          </Async>
        </Panel>
      }
      center={
        <section className="panel">
          <div className="panel-header"><h2>Concerns & diagnose</h2><span className="panel-badge badge-ai">Service Agent</span></div>
          <div className="panel-body">
            <div className="section-label">Customer concerns</div>
            {(sj.concerns || []).length ? sj.concerns.map((c: string, i: number) => <ActivityItem key={i}>{c}</ActivityItem>) : <div className="dim text-sm">No concerns recorded at check-in.</div>}
            <Diagnose vin={sj.vehicle?.vin} isTech={persona?.role === "dealer_technician"}
                      onAdd={(row) => { setRows((rs) => [...rs, row]); toast(`Added ${row.item} to the draft`, "success"); }} />
          </div>
        </section>
      }
      right={
        <Panel title="Draft estimate" badge={`${actionable.length} line(s)`} badgeKind="active" ariaLive>
          {actionable.length ? actionable.map((r) => <DraftLine key={r.item} vin={sj.vehicle?.vin} row={r} />) : <EmptyState>Yellow and red items become estimate lines.</EmptyState>}
          <div className="text-xs dim">Pricing is applied by the labor guide and parts catalog when the advisor opens the estimate — read-only here.</div>
          {unrated.length > 0 && <div className="text-xs" style={{ color: "var(--warning)" }}>{unrated.length} item(s) not rated yet.</div>}
          {missingEvidence.length > 0 && (
            <div className="objection-alert"><div className="objection-label">Evidence required</div>{missingEvidence.map((r) => r.item).join(", ")}</div>
          )}
          <Btn kind="primary" size="lg" onClick={submit} disabled={busy || missingEvidence.length > 0 || rows.every((r) => !r.status)}>
            {busy ? "Submitting…" : "Submit inspection & build estimate"}
          </Btn>
        </Panel>
      }
    />
  );
}

function DraftLine({ vin, row }: { vin?: string; row: Row }) {
  const cov = useApi<any>(vin ? `/service/coverage?vin=${encodeURIComponent(vin)}&item=${encodeURIComponent(row.item)}` : null);
  const pay = cov.data?.line?.pay_type;
  return (
    <IncentiveRow eligible={row.status === "red"} name={<><StatusChip status={row.status || ""} /> {row.item}</>}
      amount={<>{row.dtc_codes?.length ? `DTC ${row.dtc_codes.join(", ")} · ` : ""}{row.evidence.length} evidence</>}
      right={pay ? <Badge tone={pay === "warranty" ? "green" : "gray"} title={cov.data?.line?.rule}>{pay.replace("_", " ")}</Badge> : null} />
  );
}

function Diagnose({ vin, isTech, onAdd }: { vin?: string; isTech: boolean; onAdd: (r: Row) => void }) {
  const [codes, setCodes] = useState("");
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [proc, setProc] = useState<any>(null);
  const [parts, setParts] = useState<any>(null);
  const ops = useApi<any>("/service/labor");

  const lookup = async () => {
    setErr(null); setProc(null);
    try { setRes(await apiGet<any>(`/service/dtc?codes=${encodeURIComponent(codes)}&vin=${encodeURIComponent(vin || "")}`)); }
    catch (e: any) { setErr(e.message); }
  };
  const pick = async (op: string) => {
    setErr(null);
    try {
      const [p, pt] = await Promise.all([apiGet<any>(`/service/procedures?labor_op=${op}&vin=${encodeURIComponent(vin || "")}`),
                                         apiGet<any>(`/service/parts?labor_op=${op}`)]);
      setProc(p); setParts(pt);
    } catch (e: any) { setErr(e.message); }
  };

  return (
    <div className="rec-card">
      <div className="rec-card-header"><div className="rec-card-title">🔍 Diagnose</div><span className="dim text-xs">DTC → TSB → labor op → procedure</span></div>
      <div className="rec-card-body flex flex-col gap-3">
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); lookup(); }}>
          <input className="input" style={{ minHeight: 44 }} value={codes} onChange={(e) => setCodes(e.target.value.toUpperCase())} placeholder="DTC codes, e.g. P0300, B1318" aria-label="DTC codes" />
          <Btn kind="primary" type="submit" size="lg" disabled={!codes.trim()}>Look up</Btn>
        </form>
        {err && <ErrorState error={err} />}
        {res?.codes?.map((c: any) => (
          <div key={c.dtc} className={cx("incentive-row", c.found ? "eligible" : "ineligible")}>
            <div className="incentive-info">
              <div className="incentive-name">{c.dtc} — {c.found ? c.description : "not in the DTC reference"}</div>
              <div className="incentive-amount">
                {c.found && <span className="data-source">{c.source_ref}</span>}
                {c.tsb && <span className="data-source" title={c.tsb.title}>TSB {c.tsb.doc_id}</span>}
                {c.recommended_tests?.length ? ` · tests: ${c.recommended_tests.join("; ")}` : ""}
              </div>
              {c.found && (
                <div className="flex gap-1 flex-wrap mt-1">
                  {c.likely_labor_ops.map((op: string) => <Btn key={op} kind="alt" size="sm" onClick={() => pick(op)}>{op}</Btn>)}
                  <Btn kind="secondary" size="sm" onClick={() => onAdd({ item: `${c.description} (${c.dtc})`, status: "red", evidence: [`dtc:${c.dtc}`], dtc_codes: [c.dtc], custom: true })}>Add as finding</Btn>
                </div>
              )}
            </div>
          </div>
        ))}
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs muted">Or pick a labor op:</span>
          <select className="select" style={{ maxWidth: 260, minHeight: 44 }} onChange={(e) => e.target.value && pick(e.target.value)} defaultValue="" aria-label="Labor op">
            <option value="">Choose…</option>
            {(ops.data?.labor_ops || []).map((o: any) => <option key={o.labor_op_id} value={o.labor_op_id}>{o.name} · {o.flat_rate_hours} h</option>)}
          </select>
        </div>
        {proc && (
          <div className="ai-response-card">
            <div className="ai-response-label"><span className="ai-tag">Procedure · {proc.name}</span><span className="success-rate">{proc.flat_rate_hours} h</span></div>
            <ol className="text-sm list-decimal pl-5">{proc.steps.map((s: string, i: number) => <li key={i}>{s}</li>)}</ol>
            <div className="mt-2">{proc.citation ? <span className="data-source" title={proc.citation.title}>{proc.citation.source_ref}</span> : <span className="data-source">{proc.source_ref}</span>}</div>
            {parts?.parts?.length > 0 && (
              <div className="mt-2 text-xs muted">Parts: {parts.parts.slice(0, 4).map((p: any) => `${p.description}${p.on_hand ? "" : " (order)"}`).join(", ")}{isTech ? "" : ""}</div>
            )}
            <div className="response-btns mt-2">
              <Btn kind="use" onClick={() => onAdd({ item: proc.name, status: "red", evidence: [`procedure:${proc.labor_op_id}`], custom: true })}>Add line to draft</Btn>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
