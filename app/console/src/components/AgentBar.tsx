"use client";

// AgentBar — the persona's agents as a horizontal hero strip under the app
// header, present on every authenticated screen (mounted once by the Shell).
//
//   [ AGENTS | ACTIVITY ]   ⬡ A3 Objection coach ● live   ⬡ A7 Estimate builder …
//
// Clicking an agent chip selects it and reveals what it does, when it runs, when
// it last ran, and its actions: Run now (when the page bound one) and Interact —
// which opens a chat drawer on the right scoped to THAT agent, so a question is
// always addressed to a named agent rather than a generic assistant.
//
// Run-now bindings are contributed by pages through <AgentActionsProvider/>, so
// the bar stays one component while each screen supplies the live endpoints it
// already calls. For the admin roles the bar is read-mostly: Run now and
// Interact show only where the server accepts that role (`chats` / `runNow` in
// lib/agents.ts), and the rest points to View as persona. A staff role whose
// chat route refuses it sees that Interact disabled ("Not available for your role").

import { ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { apiPost } from "@/lib/api";
import { AGENT_ACTIVITY_EVENT, useApi } from "@/lib/hooks";
import { fmtClock } from "@/lib/market";
import { usePersona } from "@/lib/persona";
import {
  AGENT_CHAT, AGENT_NAMES, AgentAutomation, AgentId, agentChatFor, agentLabel, agentsForRole, humanizeAgents, mayRunNow, toolLabel } from "@/lib/agents";
import { useDockedInsight } from "@/lib/insightDock";
import { Badge, Btn, ChatPanel, EmptyState, cx, sourceLabel, useToast } from "@/components/app";
import type { ChatAnswer } from "@/components/app";

// ---- page-contributed actions -------------------------------------------------

export interface AgentAction { label?: string; run: () => Promise<unknown> | unknown }
/** A page's sourced [AI INSIGHT], docked in the bar next to the agent chips. */
export interface AgentInsight { title?: string; text: string; sources?: (string | null | undefined)[] }
export interface AgentContext {
  actions?: Record<string, AgentAction>;
  journeyId?: string | null;
  serviceJourneyId?: string | null;
  /** a Lead Desk lead: its own runs (intake, draft, send, assign, booking) plus its journey's */
  leadId?: string | null;
  /** extra words appended to a chat question for grounding (VIN, RO, …) */
  chatContext?: string;
  insight?: AgentInsight | null;
}

// The bar is mounted by the Shell ABOVE the routed page, so a page cannot be its
// React ancestor — plain context would never reach it. Instead pages publish
// into this tiny module store and the bar subscribes. Same public API
// (<AgentActionsProvider value={…}>), but the data flows up, not down.
const store: { ctx: AgentContext; listeners: Set<() => void> } = { ctx: {}, listeners: new Set() };
const emit = () => store.listeners.forEach((l) => l());

/** Pages wrap (part of) their tree to give the bar live actions + trace scope. */
export function AgentActionsProvider({ value, children }: { value: AgentContext; children: ReactNode }) {
  // Re-notify only when the SHAPE changes (ids, action ids, labels) — the value
  // object itself is new on every render, so comparing it would loop forever.
  const sig = JSON.stringify([
    value.journeyId ?? null, value.serviceJourneyId ?? null, value.leadId ?? null, value.chatContext ?? null,
    value.insight?.title ?? null, value.insight?.text ?? null,
    Object.entries(value.actions || {}).map(([k, v]) => [k, v.label ?? null]).sort(),
  ]);
  const mine = useRef<AgentContext | null>(null);

  // After every render: keep the freshest closures (page state moves on without
  // changing the shape, and Run-now must call the current one).
  useEffect(() => { store.ctx = value; mine.current = value; });
  // On shape change: wake the bar.
  useEffect(() => { store.ctx = value; mine.current = value; emit(); }, [sig]);   // eslint-disable-line react-hooks/exhaustive-deps
  // On unmount: clear, unless a newly mounted page already published its own.
  useEffect(() => () => {
    if (store.ctx === mine.current) { store.ctx = {}; emit(); }
  }, []);

  return <>{children}</>;
}

/** The bar's view of whatever the current page published. */
export function useAgentContext(): AgentContext {
  const [, bump] = useState(0);
  useEffect(() => {
    const l = () => bump((n) => n + 1);
    store.listeners.add(l);
    return () => { store.listeners.delete(l); };
  }, []);
  return store.ctx;
}

// ---- the bar -------------------------------------------------------------------

// The roles that may use View as persona (the Shell's persona switcher).
const VIEW_AS_ROLES = ["admin_tenant", "admin_dealer", "platform_admin"];

const TRIGGER: Record<AgentAutomation["trigger"], { label: string; dot: string }> = {
  auto:      { label: "Live",      dot: "dot-live" },
  scheduled: { label: "Scheduled", dot: "dot-sched" },
  manual:    { label: "On demand", dot: "dot-manual" },
};

// Per-agent colour class on the chip meta. Spelled out as literals (not
// `ac-${code}`): Tailwind keeps a `@layer components` rule only when its class
// name appears in a source file, so a template string left every `.ac-a*` rule
// out of the build and the agent name fell back to the dim grey.
const AGENT_CLASS: Record<AgentId, string> = {
  A1: "ac-a1", A2: "ac-a2", A3: "ac-a3", A4: "ac-a4", A5: "ac-a5", A6: "ac-a6", A7: "ac-a7",
};

/** ISO ts → local "HH:MM" (chips) or "HH:MM:SS" (activity feed), prefixed with
 *  the date ("21 Oct 06:30") when it is not today, so an old run never reads as
 *  today's; "" if unparsable. */
function fmtTime(ts: string | undefined, withSeconds = false, marketId?: string | null): string {
  return fmtClock(ts, marketId, withSeconds);   // the store's zone, as every other screen
}

/** A trace's tool name for the Activity feed, without transport or agent-code
 *  prefixes (users see agent names only): "runtime.job.stall-sweep" →
 *  "stall-sweep (scheduled run)", "a2.send_outreach" → "send outreach". */

/** Admin › Agents health (GET /admin/agents/health) → one newest-first trace
 *  feed, each row tagged with its agent (the per-agent rows omit it). */
function tenantTraces(health: any, limit = 30): any[] {
  return (health?.agents || [])
    .flatMap((a: any) => (a.recent_traces || []).map((t: any) => ({ ...t, agent_id: a.agent_id })))
    .sort((x: any, y: any) => String(y.ts || "").localeCompare(String(x.ts || "")))
    .slice(0, limit);
}

export function AgentBar() {
  const { persona } = usePersona();
  const agentCtx = useAgentContext();
  const role = persona?.role;
  const catalog = agentsForRole(role);
  const [tab, setTab] = useState<"agents" | "activity">("agents");
  const [sel, setSel] = useState<string | null>(null);
  const [chatFor, setChatFor] = useState<AgentAutomation | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const toast = useToast();

  // a lead scope wins over its journey: the lead feed already includes the journey's traces
  const qs = agentCtx.leadId ? `lead_id=${encodeURIComponent(agentCtx.leadId)}`
    : agentCtx.journeyId ? `journey_id=${encodeURIComponent(agentCtx.journeyId)}`
    : agentCtx.serviceJourneyId ? `service_journey_id=${encodeURIComponent(agentCtx.serviceJourneyId)}` : null;
  const staff = Boolean(role && role !== "customer");
  // GET /traces is staff-only (trace detail carries routing decisions and rule ids)
  const traces = useApi<any>(staff && qs ? `/traces?${qs}&limit=30` : null, { pollMs: 20000, background: true });
  // Tenant-wide admin roles with no journey / RO in scope read the Admin › Agents
  // feed instead, so "last ran" still comes from PLAT_AgentTraces.
  const tenantFeed = !qs && catalog?.traceFeed === "tenant";
  const health = useApi<any>(
    tenantFeed ? `/admin/agents/health?market_id=${encodeURIComponent(persona?.marketId || "us")}` : null,
    { pollMs: 60000, background: true });
  const tenantItems = useMemo(() => tenantTraces(health.data), [health.data]);
  const items: any[] = tenantFeed ? tenantItems : traces.data?.traces || [];
  // Outside a journey, "last ran" comes from each automation's named runs (its
  // scheduled job, its on-demand route): GET /traces/latest, staff roles only.
  const runKeys = useMemo(() => Array.from(new Set((catalog?.automations || []).flatMap((a) => a.runs || []))).sort(),
    [catalog]);
  const latest = useApi<any>(role && role !== "customer" && runKeys.length
    ? `/traces/latest?runs=${encodeURIComponent(runKeys.join(","))}` : null, { pollMs: 60000, background: true });
  // a page action just finished (a draft, a send, an assignment…): show its run now
  useEffect(() => {
    const onActivity = () => { void traces.reload(); void latest.reload(); void health.reload(); };
    window.addEventListener(AGENT_ACTIVITY_EVENT, onActivity);
    return () => window.removeEventListener(AGENT_ACTIVITY_EVENT, onActivity);
  }, [traces.reload, latest.reload, health.reload]);

  // "last ran" per automation: the journey / RO trace feed when one is in scope
  // (the tenant feed spans every agent, so there a trace must also come from the
  // automation's agent), else the newest of its named runs in your stores.
  const lastRun = useMemo(() => {
    const m: Record<string, { ts: string; scope: string }> = {};
    const runs = latest.data?.runs || {};
    for (const a of catalog?.automations || []) {
      const hit = items.find((t) => (!tenantFeed || t.agent_id === a.agent)
        && (a.traceMatch || []).some((k) => String(t.tool || "").toLowerCase().includes(k)));
      // a journey / RO run wins outright; the tenant feed and the named runs are
      // both tenant-wide, so there the newer one wins
      if (hit?.ts && qs) { m[a.id] = { ts: hit.ts, scope: agentCtx.leadId ? "this lead" : agentCtx.journeyId ? "this journey" : "this repair order" }; continue; }
      const ts = (a.runs || []).map((k) => runs[k]?.ts).filter(Boolean).sort().pop();
      if (hit?.ts && (!ts || hit.ts > ts)) m[a.id] = { ts: hit.ts, scope: "tenant-wide" };
      else if (ts) m[a.id] = { ts, scope: role?.startsWith("dealer_") || role === "admin_dealer" ? "your store" : "your market" };
    }
    return m;
  }, [catalog, items, tenantFeed, latest.data, qs, agentCtx.journeyId, agentCtx.leadId, role]);

  const docked = useDockedInsight();

  if (!catalog) return null;
  const automations = catalog.automations;
  const selected = automations.find((a) => a.id === sel) || null;
  // Run-now needs a page binding AND a role the bound endpoint accepts; Interact
  // needs a chat route that accepts the role. Staff roles always pass both checks.
  const selAction = selected ? agentCtx.actions?.[selected.id] : undefined;
  const selCanRun = Boolean(selected && selAction && mayRunNow(role, selected.id));
  const selRunWithheld = Boolean(selAction) && !selCanRun;
  const selCanChat = Boolean(selected && agentChatFor(role, selected.agent));
  // The admin roles act for a persona through View as persona; everyone else
  // simply cannot use what the server refuses them.
  const viewAs = VIEW_AS_ROLES.includes(role || "");
  const count = (t: AgentAutomation["trigger"]) => automations.filter((a) => a.trigger === t).length;
  const selRan = selected ? lastRun[selected.id] : undefined;
  // A page-published insight (AgentContext) wins over an auto-docked <AiInsight/>.
  const insight = agentCtx.insight?.text
    ? { title: agentCtx.insight.title, body: agentCtx.insight.text, sources: agentCtx.insight.sources }
    : docked;

  const runNow = async (a: AgentAutomation) => {
    const act = agentCtx.actions?.[a.id];
    if (!act || !mayRunNow(role, a.id)) return;
    setBusyId(a.id);
    try { await act.run(); toast(`${a.name}: started`, "success"); }
    catch (e: any) { toast(`${a.name}: ${e?.message || "failed"}`, "error"); }
    finally { setBusyId(null); }
  };

  return (
    <>
      <div className="agent-bar" role="region" aria-label={catalog.title}>
        <div className="agent-bar-head">
          <span className="agent-bar-brand">
            <span className="agent-glyph" aria-hidden>⬡</span>
            <b>Agents</b>
            <span className="agent-bar-count">{automations.length}</span>
          </span>
          <div className="agent-tabs" role="tablist" aria-label="Agent bar view">
            {(["agents", "activity"] as const).map((t) => (
              <button key={t} role="tab" aria-selected={tab === t}
                      className={cx("agent-tab", tab === t && "active")} onClick={() => setTab(t)}>
                {t === "agents" ? "Agents" : "Activity"}
                {t === "activity" && items.length > 0 && <span className="agent-bar-count">{items.length}</span>}
              </button>
            ))}
          </div>
          <span className="agent-bar-note">
            {tab === "agents"
              ? <>{count("auto")} live · {count("scheduled")} scheduled · {count("manual")} on demand</>
              : <>live trace feed{qs ? "" : tenantFeed ? " — latest runs of every agent, tenant-wide" : " — open a lead, journey or RO for scope"}</>}
          </span>
        </div>

        {tab === "agents" && (
          <div className="agent-bar-main">
            <div className="agent-strip">
              {automations.map((a) => {
                const t = TRIGGER[a.trigger];
                const on = sel === a.id;
                const ran = lastRun[a.id];
                return (
                  <button key={a.id} className={cx("agent-chip", on && "active")} aria-expanded={on}
                          title={`${a.does} Trigger: ${a.when}.${ran ? ` Last ran ${fmtTime(ran.ts, false, persona?.marketId)} (${ran.scope}).` : ""}`}
                          onClick={() => setSel(on ? null : a.id)}>
                    <span className={cx("agent-dot", t.dot)} aria-hidden />
                    <span className="agent-chip-body">
                      <span className="agent-chip-name">{a.name}</span>
                      <span className="agent-chip-meta">
                        <span className={cx("agent-code", AGENT_CLASS[a.agent as AgentId])}>{agentLabel(a.agent)}</span>
                        {" · "}{t.label}{ran ? <span className="agent-chip-ran"> · {fmtTime(ran.ts, false, persona?.marketId)}</span> : ""}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
            {insight && (
              <aside className="agent-insight" role="note" aria-label="AI insight">
                <span className="agent-insight-title">◆ [AI INSIGHT]{insight.title ? <> {insight.title}</> : null}</span>
                <span className="agent-insight-text">{insight.body}</span>
                {(insight.sources || []).filter(Boolean).length > 0 && (
                  <span className="agent-insight-sources">
                    {(insight.sources || []).filter(Boolean).map((s) => (
                      <span key={String(s)} className="data-source" title={String(s)}>{sourceLabel(String(s))}</span>
                    ))}
                  </span>
                )}
              </aside>
            )}
          </div>
        )}

        {tab === "agents" && selected && (
          <div className="agent-detail" aria-live="polite">
            <span className="agent-detail-text">
              <Badge tone="purple" title={AGENT_NAMES[selected.agent]}>{agentLabel(selected.agent)}</Badge>{" "}
              <b>{selected.name}</b> — {selected.does}{" "}
              <span className="dim">
                Trigger: {selected.when}.{selRan ? ` Last ran ${fmtTime(selRan.ts, false, persona?.marketId)} (${selRan.scope}).` : ""}
              </span>
            </span>
            <span className="agent-detail-actions">
              {selCanRun && selAction && (
                <Btn kind="alt" size="sm" disabled={busyId === selected.id} onClick={() => runNow(selected)}>
                  {busyId === selected.id ? "Running…" : selAction.label || "Run now"}
                </Btn>
              )}
              {viewAs && (selRunWithheld || !selCanChat) && (
                <span className="dim text-[.72rem]">
                  {selRunWithheld && !selCanChat ? "To run it or chat, use View as persona"
                    : selRunWithheld ? "To run it, use View as persona" : "To chat, use View as persona"}
                </span>
              )}
              {selCanChat && <Btn kind="primary" size="sm" onClick={() => setChatFor(selected)}>Interact →</Btn>}
              {/* a staff role whose chat route refuses it (the GM's Campaign Agent,
                  the OEM CX lead's Journey Agent) sees Interact disabled */}
              {!selCanChat && !viewAs && (
                <span title="Not available for your role">
                  <Btn kind="primary" size="sm" disabled aria-label="Interact: not available for your role">Interact →</Btn>
                </span>
              )}
            </span>
          </div>
        )}

        {tab === "activity" && (
          <div className="agent-strip activity" aria-live="polite">
            {!qs && !tenantFeed && <span className="agent-empty">Open a lead, journey or repair order to scope the feed.</span>}
            {(qs || tenantFeed) && items.length === 0 && <span className="agent-empty">No agent activity yet.</span>}
            {items.map((t) => (
              <span key={t.trace_id} className="agent-trace"
                    title={t.correlation_id ? `correlation ${t.correlation_id}` : undefined}>
                <span className={cx("agent-dot", t.outcome && !["ok", "success"].includes(t.outcome) ? "dot-err" : "dot-live")} aria-hidden />
                <span className="mono agent-trace-time">{fmtTime(t.ts, true, persona?.marketId)}</span>
                <span className="agent-trace-tool" title={String(t.tool || "")}>{t.agent_id ? `${agentLabel(t.agent_id)} · ` : ""}{toolLabel(t.tool)}</span>
                {t.autonomy_level && <span className="dim">· {t.autonomy_level}</span>}
              </span>
            ))}
          </div>
        )}
      </div>

      {chatFor && agentChatFor(role, chatFor.agent) && (
        <AgentChatDrawer automation={chatFor} onClose={() => setChatFor(null)} />
      )}
    </>
  );
}

// ---- per-agent chat drawer ------------------------------------------------------

function AgentChatDrawer({ automation, onClose }: { automation: AgentAutomation; onClose: () => void }) {
  const { persona } = usePersona();
  const ctx = useAgentContext();
  const cfg = AGENT_CHAT[automation.agent as AgentId];

  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const send = async (q: string): Promise<ChatAnswer> => {
    const question = ctx.chatContext ? `${q} (${ctx.chatContext})` : q;
    if (persona?.role === "customer") {
      const d = await apiPost<any>("/shop/chat", { text: question });
      return {
        answer: d.answer,
        citations: (d.citations || []).map((c: any) => ({ label: c.label, ref: c.ref })),
        guardrail: d.refused ? String(d.category || "policy") : null,
        engine: d.engine || "customer-api",
      };
    }
    const body: Record<string, unknown> = cfg.synthesize
      ? { query: question, k: 3, synthesize: true, persona: cfg.persona }
      : { question, journey_id: ctx.journeyId || undefined,
          service_journey_id: ctx.serviceJourneyId || undefined,
          agent_id: automation.agent, view: cfg.view || "agent-bar" };
    const d = await apiPost<any>(cfg.path, body);
    const passages: any[] = d.passages || [];
    return {
      answer: d.answer || (passages.length
        ? "Here's what the knowledge base says — see the excerpts below."
        : "I could not ground an answer for that."),
      citations: (d.citations || passages.map((p: any) => ({ label: p.source_ref, ref: p.collection })) || [])
        .map((c: any) => ({ label: c.label, ref: c.ref })),
      references: d.references
        || passages.map((p: any) => ({ label: p.source_ref, text: String(p.passage || "").slice(0, 300) })),
      guardrail: d.guardrail && (Array.isArray(d.guardrail) ? d.guardrail.join(", ") : String(d.guardrail)),
      engine: d.engine || d.via,
    };
  };

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="agent-chat-drawer" role="dialog" aria-modal="true"
             aria-label={`Chat with ${automation.name}`}>
        <div className="agent-chat-head">
          <span className="min-w-0">
            <span className="flex items-center gap-2">
              <span className="agent-glyph" aria-hidden>⬡</span>
              <b className="text-[.9rem]">{automation.name}</b>
              <Badge tone="purple">{agentLabel(automation.agent)}</Badge>
            </span>
            <span className="block dim text-[.7rem] mt-0.5">
              {AGENT_NAMES[automation.agent as AgentId]} · {cfg.host ? `on ${cfg.host}` : "on Amazon Bedrock AgentCore"}
            </span>
          </span>
          <Btn kind="ghost" size="sm" onClick={onClose} aria-label="Close agent chat">✕</Btn>
        </div>
        <div className="agent-chat-does">{automation.does}</div>
        <div className="flex-1 min-h-0 flex flex-col">
          <ChatPanel
            inline
            title={`Ask ${automation.name}`}
            badge={agentLabel(automation.agent)}
            placeholder={cfg.placeholder}
            suggestions={cfg.prompts}
            language={persona?.marketId === "de" ? "de-DE" : "en-US"}
            intro={`You are talking to ${AGENT_NAMES[automation.agent as AgentId]}. Answers are grounded on ${cfg.grounding || `this ${ctx.serviceJourneyId ? "repair order" : ctx.journeyId ? "journey" : "store"}'s records`} and cite their source.`}
            send={send}
          />
        </div>
      </aside>
    </>
  );
}
