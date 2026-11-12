"use client";

// Journey components: the 8-stage purchase stepper (§2.1), the service stepper
// (§2.1.1), a generic Kanban, the shared <JourneyActivityFeed> (§5.18.6), and
// the "You're here because…" context strip (§5.18.3).

import { humanizeAgents } from "@/lib/agents";
import { ReactNode } from "react";
import { useApi, timeAgo } from "@/lib/hooks";
import { ActivityItem } from "./advisor";
import { Async, Badge, cx, EmptyState } from "./primitives";

export const PURCHASE_STAGES = ["DISCOVER", "CONFIGURE", "QUALIFY", "FINANCE", "TRADE_IN", "AGREE", "DELIVER", "OWN"] as const;
export const SERVICE_STAGES = ["BOOK", "CHECK_IN", "INSPECT", "DIAGNOSE", "APPROVE", "REPAIR", "READY", "FOLLOW_UP"] as const;

const LABEL: Record<string, string> = {
  TRADE_IN: "Trade-in", CHECK_IN: "Check-in", FOLLOW_UP: "Follow-up",
};
export const stageLabel = (s: string) => LABEL[s] || (s ? s[0] + s.slice(1).toLowerCase() : s);

export function Stepper({ stages = PURCHASE_STAGES as readonly string[], current, blocked, compact }: {
  stages?: readonly string[]; current?: string | null; blocked?: boolean; compact?: boolean }) {
  const idx = current ? stages.indexOf(current) : -1;
  return (
    <ol className="stepper list-none m-0 p-0" aria-label="Journey stage">
      {stages.map((s, i) => (
        <li key={s} aria-current={i === idx ? "step" : undefined}
            className={cx("step", i < idx && "reached", i === idx && (blocked ? "blocked" : "current"))}
            title={stageLabel(s)}>
          {compact ? stageLabel(s).slice(0, 3) : stageLabel(s)}
        </li>
      ))}
    </ol>
  );
}

export function ServiceStepper(props: { current?: string | null; blocked?: boolean; compact?: boolean }) {
  return <Stepper stages={SERVICE_STAGES} {...props} />;
}

export interface KanbanColumn<T> { id: string; title: ReactNode; items: T[]; tone?: string }
export function Kanban<T>({ columns, renderCard, empty = "—" }: {
  columns: KanbanColumn<T>[]; renderCard: (item: T) => ReactNode; empty?: ReactNode }) {
  return (
    <div className="kanban" role="list">
      {columns.map((c) => (
        <section key={c.id} className="kanban-col" role="listitem" aria-label={typeof c.title === "string" ? c.title : c.id}>
          <div className="kanban-col-head"><span>{c.title}</span><span className="badge badge-gray">{c.items.length}</span></div>
          <div className="kanban-col-body">
            {c.items.length === 0 ? <div className="dim text-xs p-2">{empty}</div> : c.items.map(renderCard)}
          </div>
        </section>
      ))}
    </div>
  );
}

/** Shared activity feed (events + handoffs + tasks + agent actions). */
export function JourneyActivityFeed({ journeyId, audience = "staff", limit = 20 }: {
  journeyId: string | null | undefined; audience?: "staff" | "customer"; limit?: number }) {
  const q = useApi<any>(journeyId ? `/journeys/${encodeURIComponent(journeyId)}/activity?audience=${audience}` : null, { pollMs: 30000, background: true });
  return (
    <Async q={q} rows={3}>
      {(d) => d.items?.length ? (
        <div className="flex flex-col gap-2">
          {d.items.slice(0, limit).map((i: any, n: number) => (
            <ActivityItem key={n} time={timeAgo(i.ts)}
                          tone={i.kind === "handoff" ? "warning" : i.kind === "agent" ? undefined : undefined}>
              <span className="font-medium">{humanType(i.type)}</span>
              {i.summary && <span className="muted"> — {i.summary}</span>}
              {audience === "staff" && (i.actor_name || i.actor) && <span className="dim"> · {i.actor_name || humanizeAgents(typeof i.actor === "object" ? (i.actor.agent || i.actor.user_id) : String(i.actor))}</span>}
              {i.kind === "handoff" && (i.to_name || i.to) && <Badge tone="orange">→ {i.to_name || i.to}</Badge>}
            </ActivityItem>
          ))}
        </div>
      ) : <EmptyState>No activity yet.</EmptyState>}
    </Async>
  );
}

const humanType = (t?: string) => (t || "").replace(/[._]/g, " ").replace(/@v1$/, "");

/** "You're here because…" strip rendered from a verified ?ctx= envelope. */
export function ContextStrip({ because, onDismiss }: { because?: string | null; onDismiss: () => void }) {
  if (!because) return null;
  return (
    <div className="context-strip" role="status">
      <span aria-hidden>↪</span><span><b>You&apos;re here because</b> {because}</span>
      <button className="ml-auto btn btn-ghost btn-sm" onClick={onDismiss} aria-label="Dismiss">✕</button>
    </div>
  );
}
