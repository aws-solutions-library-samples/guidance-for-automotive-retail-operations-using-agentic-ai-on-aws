"use client";

// Assistant (A3) — spec §5.8. Role-aware (sales / BDC / service). LEFT: today's
// prioritized list (A2 publish_shift_list; service advisors see the service
// lane), each with stage chip + top contextual suggestion. CENTER: chat with A3
// scoped to the selected customer, answers cite the sources they came from.
// RIGHT: quick actions — draft follow-up, book test drive, open in Advisor,
// hand to A2 nurture.

import Link from "next/link";
import { useEffect, useState } from "react";
import { AgentAction, AgentActionsProvider } from "@/components/AgentBar";
import {
  Async, Badge, Btn, ChatPanel, Field, Panel, ThreeColumn, stageLabel, useToast,
} from "@/components/app";
import { apiPost } from "@/lib/api";
import { useApi, useMutation, useQueryParam } from "@/lib/hooks";
import { ASSISTANT_TABS } from "@/lib/nav";
import { useCanOpen, usePersona } from "@/lib/persona";

export default function AssistantPage() {
  const toast = useToast();
  const { persona } = usePersona();
  const can = useCanOpen();
  const initial = useQueryParam("journey");
  const [sel, setSel] = useState<string | null>(null);
  useEffect(() => { if (initial) setSel(initial); }, [initial]);
  const isService = persona?.role === "dealer_service";
  const list = useApi<any>(persona ? (isService ? "/service/lane" : "/assistant/shift-list") : null, { pollMs: 120000 });
  const publish = useMutation();
  const draft = useMutation();
  const send = useMutation();
  const act = useMutation();
  const [d, setD] = useState<any>(null);
  const [agentMode, setAgentMode] = useState(false);
  const [lastEngine, setLastEngine] = useState<string | null>(null);
  // Service advisors work from the service lane (sales-facing view of A7 journeys).
  const items: any[] = (list.data?.items || []).map((i: any) => isService ? ({
    journey_id: i.sales_journeys?.[0] || null, key: i.service_journey_id, customer_name: i.customer_name || i.customer_id,
    stage: i.stage, reasons: [...i.flags.map((f: any) => f.flag.replace(/_/g, " ")), ...(i.upsell?.items || []).slice(0, 1).map((u: any) => `${u.item} (${u.status})`)],
    top_suggestion: i.upsell?.talk_track ? { text: i.upsell.talk_track } : null,
  }) : ({ ...i, key: i.journey_id }));
  const current = items.find((i) => i.journey_id === sel);
  const writable = persona && !persona.isOem && persona.role !== "dealer_service";

  useEffect(() => { setD(null); }, [sel]);

  const doPublish = async () => {
    const out = await publish.run("/assistant/shift-list/publish");
    if (out) toast(`${out.published_tasks.length} call task(s) added to Tasks`, "success"); else toast(publish.error || "Failed", "error");
  };
  const doDraft = async () => {
    if (!sel) return;
    const out = await draft.run("/assistant/flows/follow-up-draft", { journey_id: sel, channel: "email" });
    if (out) setD(out); else toast(draft.error || "Draft failed", "error");
  };
  const doSend = async () => {
    if (!sel || !d) return;
    const out = await send.run(`/journeys/${encodeURIComponent(sel)}/outreach`, { kind: "follow_up", channel: d.channel, subject: d.subject, body: d.body });
    if (out) toast(out.sent ? "Follow-up sent" : out.reason === "no_consent" ? "Not sent — customer has not consented to this channel" : "Queued as a dealer task", out.reason ? "error" : "success");
    else toast(send.error || "Send failed", "error");
  };
  const quick = async (path: string, ok: string) => {
    const out = await act.run(path, {});
    if (out) toast(ok, "success"); else toast(act.error || "Failed", "error");
  };

  const left = (
    <Panel title="Today" badge={`${items.length}`} badgeKind="live"
           actions={writable && !isService && <Btn kind="alt" size="sm" onClick={doPublish} disabled={publish.pending}>Publish to Tasks</Btn>}>
      <Async q={list}>{() => items.length === 0 ? <div className="dim text-sm">Nothing needs you right now.</div> : (
        <div className="flex flex-col gap-2" role="listbox" aria-label="Prioritized customers">
          {items.map((i) => (
            <button key={i.key} role="option" aria-selected={sel === i.journey_id && !!sel} disabled={isService && !i.journey_id}
                    title={isService && !i.journey_id ? "No open sales journey for this owner" : undefined}
                    className="kanban-card" style={sel === i.journey_id ? { borderColor: "var(--border-active)" } : undefined}
                    onClick={() => setSel(i.journey_id)}>
              <div className="flex justify-between gap-2"><b className="truncate">{i.customer_name}</b><Badge tone="blue">{isService ? i.stage.replace(/_/g, " ") : stageLabel(i.stage)}</Badge></div>
              <div className="dim text-[.7rem] mt-1">{i.reasons.join(" · ")}</div>
              {i.top_suggestion && <div className="text-[.75rem] mt-1" style={{ color: "var(--primary-light)" }}>✦ {i.top_suggestion.text}</div>}
            </button>
          ))}
        </div>
      )}</Async>
    </Panel>
  );

  // Run-now bindings contributed to the global AgentBar (§1.6) — the persona's
  // named automations live there; these are this page's own quick actions.
  const agentActions: Record<string, AgentAction> = {};
  if (writable && !isService) agentActions["a2.call_list"] = { label: "Publish to Tasks", run: doPublish };
  if (writable && sel) {
    agentActions["a2.followup_drafter"] = { label: "Draft follow-up", run: doDraft };
    agentActions["a3.first_response"] = { label: "Draft follow-up", run: doDraft };
    agentActions["a2.appointment_booker"] = {
      label: "Book test drive",
      run: () => quick(`/journeys/${encodeURIComponent(sel)}/book-test-drive`, "Test drive booked"),
    };
  }

  // This screen IS the A3 assistant chat (§5.8) — it stays in the page body; the
  // bar's drawer is the per-agent side chat, not a replacement for it.
  // Agent mode (ADR 0002 pilot): the Strands reasoning agent plans over the
  // same tools; the one-shot path stays the default until the eval says otherwise.
  const center = (
    <section className="panel" aria-label="Assistant chat">
      <div className="flex justify-end items-center gap-2 px-3 pt-2 text-[.75rem]">
        <label className="flex items-center gap-1 cursor-pointer" title="Let the Sales Agent choose and run its own tools (Strands on Bedrock). Answers say which engine replied.">
          <input type="checkbox" checked={agentMode} onChange={(e) => setAgentMode(e.target.checked)} aria-label="Agent mode" />
          <span className="dim">Agent mode</span>
        </label>
        {lastEngine && <Badge tone={lastEngine.startsWith("strands") ? "blue" : "gray"}>{lastEngine}</Badge>}
      </div>
      <ChatPanel key={`${sel || "none"}-${agentMode}`} inline
        title={current ? `Sales Agent · ${current.customer_name}` : "Sales Agent"}
        intro={current ? `Ask me anything about ${current.customer_name}. I'll cite where each answer comes from.` : "Pick a customer on the left, or ask a general question."}
        suggestions={current ? ["What should I say to this customer next?", "What's blocking the next stage?", "Any incentives I should mention?"] : ["Which customers should I call first today?"]}
        send={async (question) => {
          const out = await apiPost<any>(agentMode ? "/assistant/agent" : "/assistant/chat", { question, journey_id: sel || undefined });
          setLastEngine(out.engine ? String(out.engine) + (out.tools_used?.length ? ` · ${out.tools_used.length} tools` : "")
            + (out.transport === "agentcore-runtime" ? " · AgentCore" : "") : null);
          return { answer: out.answer, guardrail: out.guardrail, engine: out.engine,
                   citations: (out.citations || []).map((c: any) => ({ label: c.label, ref: c.ref })),
                   references: out.references };
        }} />
    </section>
  );

  const right = (
    <Panel title="Quick actions" badge={current ? stageLabel(current.stage) : undefined} badgeKind="ai">
      {!sel ? <div className="dim text-sm">Select a customer to act.</div> : (
        <div className="flex flex-col gap-3">
          {can("dealer.advisor")
            ? <Link className="btn btn-primary" href={`/dealer/advisor/${encodeURIComponent(sel)}`}>Open in Advisor</Link>
            : can("dealer.service_lane") && <Link className="btn btn-primary" href="/dealer/assistant/service">Open Service Lane</Link>}
          {writable && <>
            <Btn kind="alt" onClick={doDraft} disabled={draft.pending}>{draft.pending ? "Drafting…" : "Draft follow-up"}</Btn>
            <Btn kind="alt" onClick={() => quick(`/journeys/${encodeURIComponent(sel)}/book-test-drive`, "Test drive booked")} disabled={act.pending}>Book test drive</Btn>
            <Btn kind="alt" onClick={() => quick(`/journeys/${encodeURIComponent(sel)}/nurture/plan`, "Handed to the Journey Agent's nurture")} disabled={act.pending}>Hand to nurture</Btn>
          </>}
          {d && (
            <div className="card" style={{ marginBottom: 0 }}>
              <Field label="Subject">{(id) => <input id={id} className="input" value={d.subject} onChange={(e) => setD({ ...d, subject: e.target.value })} />}</Field>
              <Field label="Message" hint={`Grounded on ${d.facts_used.length} record(s)`}>{(id) => (
                <textarea id={id} className="textarea" rows={7} value={d.body} onChange={(e) => setD({ ...d, body: e.target.value })} />)}</Field>
              <div className="flex gap-2"><Btn onClick={doSend} disabled={send.pending}>Send</Btn><Btn kind="ghost" onClick={() => setD(null)}>Discard</Btn></div>
            </div>
          )}
        </div>
      )}
    </Panel>
  );

  return (
    <AgentActionsProvider value={{ actions: agentActions, journeyId: sel }}>
      <div className="flex flex-col flex-1 min-h-0">
        <div className="top-bar">
          <div className="session-info"><span className="live-dot" aria-hidden /><span><strong>Assistant</strong> · {persona?.roleName || persona?.role}</span></div>
          {/* A tab shows only for a role that holds its screen (the Lead Desk is
              the BDC's, so a service advisor sees Chat and Service Lane). */}
          <nav className="nav-tabs">
            <span className="nav-tab active" aria-current="page">Chat</span>
            {ASSISTANT_TABS.filter((t) => t.screen && can(t.screen)).map((t) => (
              <Link key={t.href} className="nav-tab" href={t.href}>{t.label}</Link>
            ))}
          </nav>
        </div>
        <ThreeColumn left={left} center={center} right={right} />
      </div>
    </AgentActionsProvider>
  );
}
