"use client";

// Customer Web · /garage/service/{id} — the ESTIMATE APPROVAL experience, the
// flagship customer screen (design §6.1 primary flow; spec §5.12, §5.20.4
// customer mode, §5.19). Live tracker (8 service stages, promised time, parts
// hold) and line-by-line review: evidence, plain-language explain_finding,
// coverage badge, good-better-best, approve/decline per line, "ask a question"
// (aria-live, escalates to the advisor). Approvals confirm; declined lines are
// recorded with "book it now" later. Mobile-first single column, large targets.

import { humanizeCode } from "@/lib/text";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  Async, Badge, Btn, Card, DateTime, EmptyState, InfoRow, Modal, Money, SavingsBanner,
  SectionLabel, ServiceStepper, StatusChip, stageLabel, useToast,
} from "@/components/app";
import { apiPost } from "@/lib/api";
import { useApi, useMutation, useRouteId } from "@/lib/hooks";
import { BrandScope } from "../../Brand";
import { Page, ViewHeader } from "@/components/app";
import { EstimateLine, Line } from "./EstimateLine";

export default function ServiceVisitView() {
  return (
    <BrandScope>
      <VisitView />
    </BrandScope>
  );
}

function VisitView() {
  const id = useRouteId(2);
  const q = useApi<any>(id ? `/garage/service/${encodeURIComponent(id)}` : null, { pollMs: 20000 });
  return (
    <Page className="max-w-3xl mx-auto w-full">
      <ViewHeader title="Your service visit" subtitle={q.data?.dealer_name}
                  actions={<Link href="/garage/service" className="btn btn-secondary btn-sm">← All service</Link>} />
      <Async q={q}>
        {(d) => (
          <>
            {/* ---- live tracker --------------------------------------------- */}
            <Card title="Status" actions={d.parts_hold
              ? <Badge tone="orange">Waiting on parts</Badge>
              : <Badge tone="blue">{stageLabel(d.stage)}</Badge>}>
              <div className="overflow-x-auto"><ServiceStepper current={d.stage} /></div>
              <div className="grid gap-2 mt-3 sm:grid-cols-2 text-sm">
                {d.promised_at && <InfoRow label="Promised" value={<DateTime value={d.promised_at} timeZone={d.timezone} />} />}
                {d.projected_at && d.projected_at !== d.promised_at && (
                  <InfoRow label="Current estimate" value={<DateTime value={d.projected_at} timeZone={d.timezone} />} />
                )}
                <InfoRow label="While you wait" value={(d.wait_type || "—").replace(/_/g, " ")} />
                {d.ro && <InfoRow label="Repair order" value={`${d.ro.ro_number} · ${d.ro.status}`} />}
              </div>
            </Card>

            {/* ---- the approval --------------------------------------------- */}
            {d.estimate ? <EstimateApproval visit={d} onDone={q.reload} /> : (
              <Card title="Your estimate">
                <EmptyState>We&apos;ll send your estimate here the moment the inspection is done — nothing happens without your OK.</EmptyState>
              </Card>
            )}

            {/* ---- inspection report ----------------------------------------- */}
            <Card title="Inspection report">
              {d.inspection.length === 0 ? <EmptyState>Inspection not completed yet.</EmptyState> : (
                <div className="flex flex-col">
                  {d.inspection.map((f: any, i: number) => (
                    <InfoRow key={i} label={<><StatusChip status={f.status} /> {f.item}</>}
                             value={<span className="dim text-xs">
                               {f.measurement != null ? `${f.measurement}${f.unit ? ` ${f.unit}` : ""}` : ""}
                               {f.evidence_doc_id ? " · 📷 photo" : ""}
                             </span>} />
                  ))}
                </div>
              )}
            </Card>

            <div className="grid gap-4 sm:grid-cols-2">
              <Card title="Documents">
                {(d.documents || []).length === 0 ? <EmptyState>Your invoice and inspection report will appear here.</EmptyState>
                  : d.documents.map((doc: any) => (
                    <InfoRow key={doc.kind || doc.label} label={<>📄 {doc.label}</>}
                             value={doc.url
                               ? <a className="underline" href={doc.url} target="_blank" rel="noreferrer">Open</a>
                               : doc.available ? <Badge tone="green">Ready — ask your advisor</Badge> : <Badge tone="gray">After your visit</Badge>} />
                  ))}
              </Card>
              <Card title="Timeline">
                {(d.timeline || []).length === 0 ? <EmptyState>No updates yet.</EmptyState> : (
                  <ol className="list-none m-0 p-0 flex flex-col gap-2" aria-live="polite">
                    {d.timeline.slice(0, 12).map((t: any, i: number) => (
                      <li key={i} className="activity-item">
                        <span className="activity-time"><DateTime value={t.ts} mode="date" /></span>
                        <span className="activity-text">{t.label}</span>
                      </li>
                    ))}
                  </ol>
                )}
              </Card>
            </div>
          </>
        )}
      </Async>
    </Page>
  );
}

// ---- line-by-line estimate approval -----------------------------------------------

function EstimateApproval({ visit, onDone }: { visit: any; onDone: () => void }) {
  const est = visit.estimate;
  const lines: Line[] = est.lines || [];
  const toast = useToast();
  const m = useMutation();
  const [choice, setChoice] = useState<Record<string, "approve" | "decline">>({});
  const [ask, setAsk] = useState<Line | null>(null);
  const [confirmed, setConfirmed] = useState<{ approved: number; declined: number; total: number } | null>(null);
  useEffect(() => { setChoice({}); setConfirmed(null); }, [est.estimate_id]);

  const open = lines.filter((l) => !l.decision);
  const chosenTotal = useMemo(
    () => lines.reduce((s, l) => s + ((l.decision === "approved" || choice[l.line_id] === "approve") ? l.customer_pays : 0), 0),
    [lines, choice]);
  const allChosen = open.length > 0 && open.every((l) => choice[l.line_id]);
  const applyBundle = (ids: string[]) =>
    setChoice(Object.fromEntries(open.map((l) => [l.line_id, ids.includes(l.line_id) ? "approve" : "decline"])));

  const submit = async () => {
    const decisions = open.map((l) => ({ line_id: l.line_id, decision: choice[l.line_id] }));
    const out: any = await m.run(`/garage/service/estimates/${encodeURIComponent(est.estimate_id)}/decisions`, { decisions });
    if (out) {
      const approved = decisions.filter((x) => x.decision === "approve").length;
      setConfirmed({ approved, declined: decisions.length - approved, total: chosenTotal });
      toast(approved ? "Thanks — we'll start on the approved work." : "Got it — nothing will be done without your OK.", "success");
      onDone();
    } else toast(m.error || "Could not save your decision", "error");
  };

  return (
    <Card title="Your estimate" accent={est.decidable ? "warning" : undefined}
          actions={est.decidable ? <Badge tone="orange">Waiting for you</Badge> : <Badge tone="green">{humanizeCode(est.status)}</Badge>}>
      <div className="flex flex-col gap-3">
        {/* confirmation after sending decisions */}
        {confirmed && (
          <div className="card m-0" style={{ borderLeft: "4px solid var(--success)" }} role="status" aria-live="polite">
            <b>You&apos;re all set.</b>{" "}
            {confirmed.approved > 0 && <>We&apos;ll start on the {confirmed.approved} approved item{confirmed.approved === 1 ? "" : "s"} (<Money value={confirmed.total} decimals={2} />) and keep you posted here.{" "}</>}
            {confirmed.declined > 0 && <>The {confirmed.declined} item{confirmed.declined === 1 ? "" : "s"} you passed on {confirmed.declined === 1 ? "is" : "are"} saved under{" "}
              <Link href="/garage/service" className="underline">Work you put off</Link> — book {confirmed.declined === 1 ? "it" : "them"} any time.</>}
          </div>
        )}

        {est.covered > 0 && <SavingsBanner label="Covered for you (warranty & recalls)" value={<Money value={est.covered} decimals={2} />} />}

        {/* good-better-best bundles across the estimate */}
        {est.decidable && (est.bundles || []).length > 1 && open.length > 0 && (
          <div>
            <SectionLabel>Quick choices — good · better · best</SectionLabel>
            <div className="grid gap-2 sm:grid-cols-3" role="group" aria-label="Estimate bundles">
              {est.bundles.map((b: any) => (
                <button key={b.tier} className="rec-card p-3 text-left min-h-[56px]" onClick={() => applyBundle(b.line_ids)}>
                  <div className="font-semibold text-sm">{b.label}</div>
                  {b.detail && <div className="text-xs muted">{b.detail}</div>}
                  <div className="text-sm"><Money value={b.total} decimals={2} /></div>
                </button>
              ))}
            </div>
          </div>
        )}

        {lines.length === 0 ? <EmptyState>Good news — nothing needs attention.</EmptyState> : lines.map((l) => (
          <EstimateLine key={l.line_id} line={l} choice={choice[l.line_id] || null} disabled={!est.decidable || m.pending}
                        onChoose={est.decidable ? (c) => setChoice((x) => ({ ...x, [l.line_id]: c })) : undefined}
                        onAsk={est.decidable ? setAsk : undefined} />
        ))}

        {/* sticky decision bar — the primary mobile action */}
        <div className="sticky bottom-0 -mx-5 px-5 py-3 flex flex-wrap items-center justify-between gap-3"
             style={{ background: "var(--glass-strong)", backdropFilter: "blur(var(--blur))", borderTop: "1px solid var(--border)" }}>
          <div className="text-sm">
            <span className="muted">You approve</span> <b className="text-lg"><Money value={chosenTotal} decimals={2} /></b>
            <span className="dim text-xs"> of <Money value={est.you_pay} decimals={2} /></span>
          </div>
          {est.decidable && open.length > 0 && (
            <Btn kind="primary" size="lg" onClick={submit} disabled={!allChosen || m.pending}>
              {m.pending ? "Sending…" : allChosen ? "Send my decision" : `Choose for ${open.filter((l) => !choice[l.line_id]).length} more`}
            </Btn>
          )}
        </div>
        <p className="text-[.7rem] dim m-0">
          Prices come from your dealer&apos;s labor guide and parts catalog. Declined items are
          saved — nothing is done without your approval, and you can book them later.
        </p>
      </div>
      {ask && <AskSheet line={ask} sj={visit.service_journey_id} onClose={() => setAsk(null)} />}
    </Card>
  );
}

// ---- "ask a question" (A7, escalates to the advisor) ------------------------------

function AskSheet({ line, sj, onClose }: { line: Line; sj: string; onClose: () => void }) {
  const [text, setText] = useState(`About "${line.item}": `);
  const [answer, setAnswer] = useState<any | null>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const send = async (escalate = false) => {
    setBusy(true);
    try { setAnswer(await apiPost("/shop/chat", { text: `[service ${sj}] ${text}`, ...(escalate ? { escalate: true } : {}) })); }
    catch (e: any) { toast(e.message, "error"); }
    finally { setBusy(false); }
  };
  return (
    <Modal open onClose={onClose} title="Ask a question">
      <textarea className="textarea min-h-[88px]" value={text} onChange={(e) => setText(e.target.value)} aria-label="Your question" />
      <div className="flex gap-2 flex-wrap">
        <Btn kind="primary" size="lg" onClick={() => send()} disabled={busy || text.trim().length < 5}>{busy ? "Sending…" : "Send"}</Btn>
        <Btn kind="alt" size="lg" onClick={() => send(true)} disabled={busy}>Ask my advisor directly</Btn>
      </div>
      <div aria-live="polite">
        {answer && (
          <div className="ai-response-card">
            <div className="ai-response-text m-0 whitespace-pre-wrap">{answer.answer}</div>
            {answer.escalated && (
              <div className="text-xs mt-2">
                <Badge tone="orange">Passed to {answer.escalated.advisor || "your advisor"}</Badge> — they&apos;ll reply shortly.
              </div>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}
