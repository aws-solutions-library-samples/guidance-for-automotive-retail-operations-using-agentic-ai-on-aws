"use client";

// Lead Desk (BDC) — spec §5.8-ext / design §2.2. Three panes:
//   LEFT   inbound queue (DEALER_Leads) with live SLA countdowns (icon + text,
//          never color alone), source and vehicle of interest
//   CENTER A3 first-response draft (cited, editable) with [Send]
//   RIGHT  quick actions — log call / text / email, book appointment, assign
//          to a floor advisor (ownership moves through a handoff + task)
// Collapses to a single column on phones (§5.19 mobile-mandatory screen).

import { fmtClock, fmtStamp } from "@/lib/market";
import Link from "next/link";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { AgentAction, AgentActionsProvider } from "@/components/AgentBar";
import {
  Async, Badge, Btn, Field, FilterPills, Kpi, KpiGrid, Panel, Pct, SectionLabel, SourceRef,
  ThreeColumn, useToast, stageLabel } from "@/components/app";
import { apiGet, apiPost } from "@/lib/api";
import { notifyAgentActivity, timeAgo, useApi, useMutation } from "@/lib/hooks";
import { useCanOpen, usePersona } from "@/lib/persona";
import { humanizeCode } from "@/lib/text";

type Bucket = "new" | "breached" | "responded_in_sla" | "worked" | "closed";
const BUCKET_LABEL: Record<Bucket, string> = { new: "New", breached: "SLA breached", responded_in_sla: "Responded", worked: "Working", closed: "Closed" };

function useNow(ms = 1000) {
  const [n, setN] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setN(Date.now()), ms); return () => clearInterval(t); }, [ms]);
  return n;
}

/** SLA countdown with a non-color cue — icon + the word "overdue", per §2.2. */
function SlaClock({ due, responded }: { due?: string | null; responded?: string | null }) {
  const now = useNow();
  if (responded) return <Badge tone="green">✓ responded</Badge>;
  if (!due) return <span className="dim">—</span>;
  const left = Math.round((Date.parse(due) - now) / 1000);
  const neg = left < 0;
  const a = Math.abs(left);
  const txt = a >= 3600 ? `${Math.floor(a / 3600)}h ${Math.floor((a % 3600) / 60)}m` : `${Math.floor(a / 60)}:${String(a % 60).padStart(2, "0")}`;
  return (
    <span className="mono text-[.75rem]" role="timer"
          aria-label={neg ? `Overdue by ${txt}` : `${txt} until the first-response SLA`}
          style={{ color: neg ? "var(--danger)" : left < 300 ? "var(--warning)" : "var(--success)" }}>
      {neg ? <>⚠ {txt} overdue</> : <>⏱ {txt}</>}
    </span>
  );
}

/** What has happened on this lead — the desk's own feedback after Send / Book / Assign,
 *  whether the click came from this screen or from the Scenario Replay. */
function LeadActivity({ l, advisorName, can }: { l: any; advisorName: (id?: string | null) => string; can: (screen: string) => boolean }) {
  const rows: { icon: string; text: ReactNode; tone?: "green" | "orange" | "blue" }[] = [];
  rows.push({ icon: "📨", text: <>Received {timeAgo(l.received_at)} · {humanizeCode(l.source)}{l.sla_due_at && !l.first_response_at ? <> · first response due <span className="mono">{fmtClock(l.sla_due_at)}</span></> : null}</> });
  if (l.first_response_at) rows.push({ icon: "✓", tone: "green", text: <>First response sent {timeAgo(l.first_response_at)}{l.first_response_channel ? ` by ${l.first_response_channel}` : ""}{l.response_minutes != null ? ` · ${l.response_minutes} min after receipt` : ""}</> });
  else rows.push({ icon: "⏱", tone: "orange", text: "No first response yet — the SLA clock is running" });
  if (l.journey_id) {
    rows.push({ icon: "🧭", tone: "blue", text: <>Journey <Link className="mono underline" href={`/dealer/journeys?focus=${encodeURIComponent(l.journey_id)}`}>{l.journey_id}</Link>{l.journey_stage ? ` · ${stageLabel(l.journey_stage)}` : ""} · owner {l.journey_owner_name || l.journey_owner_id || "—"}
      {l.pending_handoff && <> <Badge tone="orange">→ {l.pending_handoff.to_user_name || advisorName(l.pending_handoff.to_user)} · handoff pending</Badge></>}</> });
  } else if (l.assigned_user_id) rows.push({ icon: "👤", text: <>Assigned to {l.assigned_user_name || advisorName(l.assigned_user_id)}{l.service_lead && !l.service_appointment ? " · next: book the service visit" : ""}</> });
  if (l.service_appointment) rows.push({ icon: "🔧", tone: "green", text: <>Service visit booked{l.service_appointment.booked_by === "customer" ? " by the customer" : ""} {fmtStamp(l.service_appointment.slot_start)}{can("service.journey") ? <> · <Link className="underline" href={`/dealer/service/${encodeURIComponent(l.service_appointment.service_journey_id)}`}>open the visit</Link></> : " · on the service board"}</> });
  else if (l.appointment) rows.push({ icon: "📅", tone: "green", text: <>Test drive booked {fmtStamp(l.appointment.slot_start)}</> });
  return (
    <div className="flex flex-col gap-1 text-xs mt-2" aria-label="Lead activity">
      {rows.map((r, i) => (
        <div key={i} className="flex items-start gap-2">
          <span aria-hidden style={{ color: r.tone === "green" ? "var(--success)" : r.tone === "orange" ? "var(--warning)" : undefined }}>{r.icon}</span>
          <span className={r.tone ? "" : "muted"}>{r.text}</span>
        </div>
      ))}
    </div>
  );
}

export default function LeadDeskPage() {
  const toast = useToast();
  const { persona } = usePersona();
  const can = useCanOpen();
  const q = useApi<any>("/leads/queue", { pollMs: 30000 });
  const [bucket, setBucket] = useState<Bucket | "all">("all");
  const [sel, setSel] = useState<any>(null);
  const [script, setScript] = useState<any>(null);
  const [draft, setDraft] = useState("");
  const [assignee, setAssignee] = useState("");
  const respond = useMutation();
  const assign = useMutation();
  const book = useMutation();
  const publish = useMutation();
  const writable = Boolean(persona && !persona.isOem);

  const leads: any[] = useMemo(() => {
    const qd = q.data?.queue || {};
    const all = bucket === "all" ? Object.entries(qd).flatMap(([b, xs]: any) => xs.map((x: any) => ({ ...x, bucket: b })))
      : (qd[bucket] || []).map((x: any) => ({ ...x, bucket }));
    return [...all].sort((a, b) => (a.received_at || "").localeCompare(b.received_at || ""));
  }, [q.data, bucket]);

  // Deep link: /dealer/leads?lead=<id> opens that lead (a notification, the demo replay, a shared link)
  useEffect(() => {
    if (!leads.length) return;
    const want = sel?.lead_id || new URLSearchParams(window.location.search).get("lead");
    if (!want) return;
    const hit = leads.find((l) => l.lead_id === want);
    // keep the selected card in step with the queue (status, handoff, appointment change after each action)
    if (hit && (!sel || JSON.stringify(hit) !== JSON.stringify(sel))) setSel(hit);
  }, [leads]); // eslint-disable-line react-hooks/exhaustive-deps

  // Load the A3 first-response script when a lead is picked; seed the draft.
  useEffect(() => {
    setScript(null); setDraft(""); setAssignee("");
    if (sel) apiGet<any>(`/leads/${encodeURIComponent(sel.lead_id)}/script`)
      .then((s) => { setScript(s); setDraft(`${s.opening} ${s.body}`); notifyAgentActivity(); })   // the draft is an A3 run
      .catch(() => setScript(null));
  }, [sel?.lead_id]); // eslint-disable-line react-hooks/exhaustive-deps

  const doRespond = async (channel: "call" | "sms" | "email") => {
    if (!sel) return;
    const out = await respond.run(`/leads/${encodeURIComponent(sel.lead_id)}/respond`,
      { channel, body: channel === "call" ? undefined : draft || undefined });
    if (out) {
      toast(channel === "call" ? "Call logged as a task" : out.sent ? `${channel.toUpperCase()} sent` : out.reason === "no_consent" ? "Not sent — no consent on this channel" : "Queued as a dealer task",
            out.reason === "no_consent" ? "error" : "success");
      q.reload();
    } else toast(respond.error || "Failed", "error");
  };
  const serviceLead = Boolean(sel?.service_lead);
  const salesAdvisors: any[] = q.data?.advisors || [];
  const serviceAdvisors: any[] = q.data?.service_advisors || [];
  // a predictive service lead goes to the service drive, never to the sales floor
  const advisors: any[] = serviceLead ? serviceAdvisors : salesAdvisors;
  const advisorName = (id?: string | null) =>
    [...salesAdvisors, ...serviceAdvisors].find((a) => a.user_id === id)?.name || id || "";
  const doAssign = async () => {
    if (!sel) return;
    const to = assignee || persona?.userId;
    const out = await assign.run(`/leads/${encodeURIComponent(sel.lead_id)}/assign`, { assignee: to });
    if (out) {
      if (out.service_lead) toast(to === persona?.userId ? "Assigned to you — book the service visit next"
        : `Handed to ${advisorName(to)} — they have a task to book the service visit`, "success");
      else toast(out.handoff_id
        ? `Handed to ${advisorName(to)} — ${out.journey_created ? "journey opened, " : ""}ownership moves when they accept the task`
        : `Assigned to you (journey ${out.journey_id})`, "success");
      setSel({ ...sel, journey_id: out.journey_id }); q.reload();
    } else toast(assign.error || "Assign failed", "error");
  };
  const doBook = async () => {
    if (!sel) return;
    const out = await book.run(`/leads/${encodeURIComponent(sel.lead_id)}/appointment`, {});
    if (out) {
      toast(out.already_booked ? `Already booked by the customer for ${fmtStamp(out.slot_start, persona?.marketId)} — the predicted work was added to that visit`
        : out.type === "service" ? `Service visit booked ${fmtStamp(out.slot_start, persona?.marketId)}`
        : `Test drive booked ${fmtStamp(out.slot_start, persona?.marketId)}`, "success");
      q.reload();
    } else toast(book.error || "Booking failed", "error");
  };
  // A2 shift call list — same endpoint the Assistant uses to publish to Tasks.
  const doPublish = async () => {
    const out = await publish.run("/assistant/shift-list/publish");
    if (out) toast(`${out.published_tasks.length} call task(s) added to Tasks`, "success");
    else toast(publish.error || "Publish failed", "error");
  };

  // Run-now bindings the global AgentBar picks up for the Lead desk agents —
  // the per-lead ones are omitted when no lead is selected.
  const agentActions: Record<string, AgentAction> = {};
  if (writable) {
    agentActions["a2.call_list"] = { label: "Publish to Tasks", run: doPublish };
    agentActions["a7.predictive_watch"] = { label: "Scan now", run: async () => {
      const out = await apiPost<any>("/service/predictive/run", {});
      toast(`Predictive scan: ${out.scanned} vehicles, ${out.leads_raised} new lead(s)`, "success");
      await q.reload();
    } };
    if (sel) agentActions["a2.lead_router"] = { label: assignee ? `Hand to ${advisorName(assignee)}` : "Assign to me", run: doAssign };
    if (sel?.journey_id || serviceLead) agentActions["a2.appointment_booker"] = { label: serviceLead ? "Book service visit" : "Book appointment", run: doBook };
  }

  const k = q.data?.kpis || {};
  const kpis: Kpi[] = [
    { label: "Unworked", value: k.unworked ?? "—", trend: k.unworked ? "down" : undefined, onClick: () => setBucket("new") },
    { label: "SLA breach rate", value: k.sla_breach_rate != null ? <Pct value={k.sla_breach_rate} /> : "—", onClick: () => setBucket("breached") },
    { label: "Avg first response", value: k.avg_response_minutes != null ? `${Math.round(k.avg_response_minutes)} min` : "—" },
    { label: "Total leads", value: q.data?.total ?? "—" },
  ];

  const left = (
    <Panel title="Inbound queue" badge={`${leads.length}`} badgeKind="live">
      <FilterPills value={bucket} onChange={setBucket}
        options={[{ id: "all", label: "All" }, ...(Object.keys(BUCKET_LABEL) as Bucket[]).map((b) => ({ id: b, label: `${BUCKET_LABEL[b]} (${q.data?.counts?.[b] ?? 0})` }))]} />
      <Async q={q} rows={6}>{() => leads.length === 0 ? <div className="dim text-sm">No leads in this view.</div> : (
        <div className="flex flex-col gap-2" role="listbox" aria-label="Inbound leads">
          {leads.map((l) => (
            <button key={l.lead_id} role="option" aria-selected={sel?.lead_id === l.lead_id} className="kanban-card"
                    style={sel?.lead_id === l.lead_id ? { borderColor: "var(--border-active)" } : undefined}
                    onClick={() => setSel(l)}>
              <div className="flex justify-between gap-2"><b className="truncate">{l.customer_name || l.customer_id}</b><SlaClock due={l.sla_due_at} responded={l.first_response_at} /></div>
              <div className="muted text-xs mt-1">{l.vehicle_name || l.vehicle_of_interest || "Any vehicle"} · {humanizeCode(l.source)}</div>
              {l.comments && <div className="text-xs mt-1 italic truncate">“{l.comments}”</div>}
              <div className="flex gap-1 mt-2 flex-wrap">
                <Badge tone={l.bucket === "breached" ? "red" : l.bucket === "new" ? "orange" : "gray"}>{BUCKET_LABEL[l.bucket as Bucket]}</Badge>
                {l.source === "predictive_maintenance" && (
                  <Badge tone="purple" title={`The Service Agent predicts ${l.predicted_part} due near ${(l.predicted_due_mileage || 0).toLocaleString()} on the odometer (confidence ${Math.round((l.confidence || 0) * 100)}%)`}>
                    🔮 Predictive · {l.predicted_part}
                  </Badge>
                )}
                {l.pending_handoff ? <Badge tone="orange">→ {l.pending_handoff.to_user_name || l.pending_handoff.to_user} · pending</Badge>
                  : l.assigned_user_id && <Badge tone="blue">{l.assigned_user_name || l.assigned_user_id}</Badge>}
                {l.service_appointment ? <Badge tone="green">🔧 {fmtStamp(l.service_appointment.slot_start)}</Badge>
                  : l.appointment && <Badge tone="green">📅 {fmtStamp(l.appointment.slot_start)}</Badge>}
              </div>
            </button>
          ))}
        </div>
      )}</Async>
    </Panel>
  );

  const center = (
    <Panel title="Sales Agent first response" badge="AI drafted" badgeKind="ai" className="center-panel" ariaLive>
      {!sel ? <div className="dim text-sm">Pick the highest-SLA lead on the left — the Sales Agent drafts the first response, cited.</div> : (
        <>
          <div className="flex justify-between items-center gap-2 flex-wrap">
            <b>{sel.customer_name || sel.customer_id}</b>
            <span className="dim text-xs">{sel.vehicle_name || sel.vehicle_of_interest || "Any vehicle"} · {humanizeCode(sel.source)} · {sel.dealer_id}</span>
          </div>
          <LeadActivity l={sel} advisorName={advisorName} can={can} />
          {!script ? <div className="dim text-sm">Drafting…</div> : (
            <div className="ai-response-card">
              <div className="ai-response-label"><span className="ai-tag">✦ AI suggested first response</span></div>
              <Field label="Draft (editable before sending)">{(id) => (
                <textarea id={id} className="textarea" rows={5} value={draft} onChange={(e) => setDraft(e.target.value)} />)}</Field>
              <SectionLabel>Discovery questions to work in</SectionLabel>
              <ul className="text-xs list-disc pl-5 muted">{script.questions.map((x: string) => <li key={x}>{x}</li>)}</ul>
              <div className="mt-2"><SourceRef refId={script.source_ref} /></div>
              {writable && (
                <div className="response-btns">
                  <Btn kind="use" onClick={() => doRespond("email")} disabled={respond.pending || !draft.trim()}>Send · email</Btn>
                  <Btn kind="alt" onClick={() => doRespond("sms")} disabled={respond.pending || !draft.trim()}>Send · text</Btn>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </Panel>
  );

  const right = (
    <Panel title="Quick actions" badge={sel ? BUCKET_LABEL[sel.bucket as Bucket] : undefined} badgeKind="active">
      {!sel ? <div className="dim text-sm">Select a lead to act.</div> : !writable ? <div className="dim text-sm">Read-only for your role.</div> : (
        <div className="flex flex-col gap-3">
          <div className="flex gap-2 flex-wrap">
            <Btn size="lg" onClick={() => doRespond("call")} disabled={respond.pending}>📞 Call</Btn>
            <Btn size="lg" kind="alt" onClick={() => doRespond("sms")} disabled={respond.pending}>💬 Text</Btn>
            <Btn size="lg" kind="alt" onClick={() => doRespond("email")} disabled={respond.pending}>✉ Email</Btn>
          </div>
          {serviceLead
            ? <Btn kind="alt" onClick={doBook} disabled={book.pending}
                   title={`Books ${(sel.predicted_parts || [sel.predicted_part]).filter(Boolean).join(", ")} on the customer's car`}>
                🔧 {sel.service_appointment ? "Rebook service visit" : "Book service visit"}</Btn>
            : <Btn kind="alt" onClick={doBook} disabled={book.pending || !sel.journey_id}
                   title={!sel.journey_id ? "Assign first to open a journey" : undefined}>📅 Book appointment</Btn>}
          <SectionLabel>{serviceLead ? "Hand to the service drive" : "Hand to the floor"}</SectionLabel>
          <Field label={serviceLead ? "Service advisor" : "Floor advisor"}
                 hint={serviceLead ? "A service lead is booked as a service visit; the advisor gets a Task to book it"
                   : "Ownership moves through a handoff: the advisor gets a Task and owns the journey once they accept"}>{(id) => (
            <select id={id} className="select" value={assignee} onChange={(e) => setAssignee(e.target.value)}>
              <option value="">Take it myself</option>
              {advisors.map((a) => <option key={a.user_id} value={a.user_id}>{a.name}{advisors.some((x) => x.dealer_id !== a.dealer_id) ? ` · ${a.dealer_id}` : ""}</option>)}
            </select>)}</Field>
          <Btn onClick={doAssign} disabled={assign.pending}>➡ {assignee ? `Hand to ${advisorName(assignee)}` : "Assign to me"}</Btn>
        </div>
      )}
    </Panel>
  );

  return (
    <AgentActionsProvider value={{ actions: agentActions, journeyId: sel?.journey_id || null, leadId: sel?.lead_id || null }}>
      <div className="flex flex-col flex-1 min-h-0">
        <div className="top-bar">
          <div className="session-info"><span className="live-dot" aria-hidden /><span><strong>Lead Desk</strong> · first-response SLA live</span></div>
        </div>
        <div className="px-6 pt-4"><KpiGrid items={kpis} /></div>
        <ThreeColumn left={left} center={center} right={right} />
      </div>
    </AgentActionsProvider>
  );
}
