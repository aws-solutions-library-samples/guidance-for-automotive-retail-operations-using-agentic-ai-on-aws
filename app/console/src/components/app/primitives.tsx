"use client";

// Shared primitives (spec §5.1 "shared components in components/app/*"). Class
// names follow the reference HTML so every theme (dark dealer, light OEM, brand
// customer, neutral admin) renders them from the same CSS variables.

import { ReactNode, useEffect, useId, useRef } from "react";
import { agentsForRole } from "@/lib/agents";
import { useRegisterInsight } from "@/lib/insightDock";
import { sourceLabel } from "./format";
import { usePersona } from "@/lib/persona";

const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");
export { cx };

// ---- layout ---------------------------------------------------------------

export function Page({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx("page", className)}>{children}</div>;
}

export function ViewHeader({ title, subtitle, actions }: {
  title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="view-header">
      <div>
        <h1 className="section-title">{title}</h1>
        {subtitle && <p className="section-subtitle">{subtitle}</p>}
      </div>
      {actions && <div className="flex gap-2 flex-wrap items-center">{actions}</div>}
    </div>
  );
}

/** Advisor-style three-column grid (`main-layout`): 320 / 1fr / 340. */
export function ThreeColumn({ left, center, right, two }: {
  left?: ReactNode; center: ReactNode; right?: ReactNode; two?: boolean }) {
  return (
    <div className={cx("main-layout", two && "two")}>
      {left}{center}{right}
    </div>
  );
}

// Tone → class maps are spelled out as literals rather than `badge-${tone}`:
// Tailwind keeps a `@layer components` rule from globals.css only when the class
// name appears somewhere in src/, so a template string silently dropped
// .badge-purple, .badge-orange, .dot-red, .fill-green, … from every build.
const PANEL_BADGE = { live: "badge-live", ai: "badge-ai", active: "badge-active", danger: "badge-danger", neutral: "badge-neutral" } as const;
const BADGE = { blue: "badge-blue", green: "badge-green", orange: "badge-orange", red: "badge-red", purple: "badge-purple", gray: "badge-gray" } as const;
const DOT = { green: "dot-green", red: "dot-red", orange: "dot-orange", blue: "dot-blue" } as const;
const FILL = { green: "fill-green", blue: "fill-blue", orange: "fill-orange", red: "fill-red" } as const;

export function Panel({ title, badge, badgeKind = "ai", actions, children, className, ariaLive }: {
  title: ReactNode; badge?: ReactNode; badgeKind?: keyof typeof PANEL_BADGE;
  actions?: ReactNode; children: ReactNode; className?: string; ariaLive?: boolean }) {
  return (
    <section className={cx("panel", className)} aria-live={ariaLive ? "polite" : undefined}>
      <div className="panel-header">
        <h2>{title}</h2>
        <div className="flex items-center gap-2">
          {actions}
          {badge && <span className={cx("panel-badge", PANEL_BADGE[badgeKind])}>{badge}</span>}
        </div>
      </div>
      <div className="panel-body">{children}</div>
    </section>
  );
}

export function Card({ title, actions, children, className, accent }: {
  title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string;
  accent?: "primary" | "success" | "warning" | "danger" }) {
  const border = accent ? { borderLeft: `4px solid var(--${accent})` } : undefined;
  return (
    <div className={cx("card", className)} style={border}>
      {(title || actions) && (
        <div className="card-title"><span>{title}</span>{actions && <span className="flex gap-2">{actions}</span>}</div>
      )}
      {children}
    </div>
  );
}

export function SectionLabel({ children }: { children: ReactNode }) {
  return <div className="section-label">{children}</div>;
}

// ---- indicators -----------------------------------------------------------

type BadgeTone = keyof typeof BADGE;
export function Badge({ tone = "gray", children, title }: { tone?: BadgeTone; children: ReactNode; title?: string }) {
  return <span className={cx("badge", BADGE[tone])} title={title}>{children}</span>;
}

export function Tag({ tone, children }: { tone?: "green" | "yellow" | "red"; children: ReactNode }) {
  return <span className={cx("tag", tone)}>{children}</span>;
}

export function VTag({ tone, children }: { tone?: "yellow" | "red" | "blue"; children: ReactNode }) {
  return <span className={cx("vtag", tone)}>{children}</span>;
}

/** MPI / RAG status chip (green / yellow / red). */
export function StatusChip({ status }: { status: string }) {
  const s = (status || "").toLowerCase();
  const tone = s === "red" ? "red" : s === "yellow" ? "yellow" : s === "green" ? "green" : undefined;
  return <Tag tone={tone}>{status}</Tag>;
}

export function StatusDot({ tone }: { tone: keyof typeof DOT }) {
  return <span className={cx("status-dot", DOT[tone])} aria-hidden />;
}

export function ProgressBar({ value, max = 100, tone }: { value: number; max?: number;
  tone?: keyof typeof FILL }) {
  const pct = Math.max(0, Math.min(100, max ? (value / max) * 100 : 0));
  const t: keyof typeof FILL = tone || (pct >= 90 ? "green" : pct >= 75 ? "blue" : pct >= 60 ? "orange" : "red");
  return (
    <div className="progress-bar" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
      <div className={cx("progress-fill", FILL[t])} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function ProbabilityBar({ value }: { value: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, value > 1 ? value / 100 : value)) * 100);
  return (
    <div className="probability-bar" aria-label={`${pct}% likely`}>
      <div className="prob-fill"><div className="prob-fill-inner" style={{ width: `${pct}%` }} /></div>
      <span className="prob-text">{pct}%</span>
    </div>
  );
}

export function Avatar({ name, size = 48 }: { name?: string; size?: number }) {
  const initials = (name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((s) => s[0]).join("").toUpperCase();
  return <div className="avatar" style={{ width: size, height: size, fontSize: size / 3 }} aria-hidden>{initials}</div>;
}

// ---- KPI + AI insight (DM Assistant pattern) -------------------------------

export interface Kpi { label: string; value: ReactNode; delta?: ReactNode; trend?: "up" | "down"; onClick?: () => void; href?: string }

export function KpiGrid({ items, five }: { items: Kpi[]; five?: boolean }) {
  return (
    <div className={cx("kpi-grid", five && "five")}>
      {items.map((k) => (
        <div key={k.label} className={cx("kpi-card", k.onClick && "link")} onClick={k.onClick}
             role={k.onClick ? "button" : undefined} tabIndex={k.onClick ? 0 : undefined}
             onKeyDown={(e) => k.onClick && (e.key === "Enter" || e.key === " ") && k.onClick()}>
          <div className="kpi-value">{k.value ?? "—"}</div>
          <div className="kpi-label">{k.label}</div>
          {k.delta != null && <div className={cx("kpi-delta", k.trend)}>{k.trend === "up" ? "▲ " : k.trend === "down" ? "▼ " : ""}{k.delta}</div>}
        </div>
      ))}
    </div>
  );
}

/** [AI INSIGHT] card. `sources` render as data-source chips (every insight is sourced).
 *  When the signed-in persona has an agent panel, the page's first insight docks
 *  into the AgentBar (next to the agent chips) instead of rendering here;
 *  `inline` opts a card out of docking. */
export function AiInsight({ title = "AI Insight", children, sources, inline = false }: {
  title?: ReactNode; children: ReactNode; sources?: (string | null | undefined)[]; inline?: boolean }) {
  const { persona } = usePersona();
  const hasPanel = !!agentsForRole(persona?.role);
  const docked = useRegisterInsight(!inline && hasPanel ? { title, body: children, sources } : null);
  if (docked) return null;
  return (
    <div className="ai-insight" role="note">
      <div className="ai-insight-title">◆ [AI INSIGHT] {title}</div>
      <div className="text-[.82rem] leading-relaxed">{children}</div>
      {sources && sources.filter(Boolean).length > 0 && (
        <div className="mt-2">{sources.filter(Boolean).map((s) => <span key={String(s)} className="data-source" title={String(s)}>{sourceLabel(String(s))}</span>)}</div>
      )}
    </div>
  );
}

/** Dark-theme insight box (Advisor left panel). */
export function InsightBox({ label = "AI Insight", children }: { label?: string; children: ReactNode }) {
  return <div className="insight-box"><div className="insight-label">{label}</div>{children}</div>;
}

export function MiniStat({ value, label }: { value: ReactNode; label: string }) {
  return <div className="mini-stat"><div className="mini-stat-value">{value ?? "—"}</div><div className="mini-stat-label">{label}</div></div>;
}
export function StatGrid({ children }: { children: ReactNode }) {
  return <div className="stat-grid">{children}</div>;
}

export function InfoRow({ label, value }: { label: ReactNode; value: ReactNode }) {
  return <div className="info-row"><span className="info-label">{label}</span><span className="info-value">{value ?? "—"}</span></div>;
}

// ---- states -----------------------------------------------------------------

export function EmptyState({ children = "Nothing here yet." }: { children?: ReactNode }) {
  return <div className="empty">{children}</div>;
}
export function ErrorState({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <div className="error-box" role="alert">
      {error}{onRetry && <> · <button className="underline" onClick={onRetry}>Retry</button></>}
    </div>
  );
}
export function Skeleton({ h = 16, w = "100%", className }: { h?: number; w?: number | string; className?: string }) {
  return <div className={cx("skeleton", className)} style={{ height: h, width: w }} aria-hidden />;
}
export function LoadingBlock({ rows = 4 }: { rows?: number }) {
  return <div className="flex flex-col gap-2" aria-busy="true">{Array.from({ length: rows }, (_, i) => <Skeleton key={i} h={18} />)}</div>;
}

/** Renders loading / error / content for a useApi() result. */
export function Async<T>({ q, children, rows }: {
  q: { data: T | null; error: string | null; loading: boolean; reload: () => void };
  children: (data: T) => ReactNode; rows?: number }) {
  if (q.error) return <ErrorState error={q.error} onRetry={q.reload} />;
  if (q.loading && q.data == null) return <LoadingBlock rows={rows} />;
  if (q.data == null) return <EmptyState />;
  return <>{children(q.data)}</>;
}

// ---- buttons --------------------------------------------------------------

type BtnKind = "primary" | "present" | "use" | "alt" | "secondary" | "danger" | "ghost" | "success";
export function Btn({ kind = "primary", size, used, className, ...rest }:
  React.ButtonHTMLAttributes<HTMLButtonElement> & { kind?: BtnKind; size?: "sm" | "lg"; used?: boolean }) {
  return <button type="button" className={cx("btn", `btn-${kind}`, size && `btn-${size}`, used && "used", className)} {...rest} />;
}

// ---- forms ----------------------------------------------------------------

export function Field({ label, hint, error, children }: { label: ReactNode; hint?: ReactNode; error?: string | null; children: (id: string) => ReactNode }) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children(id)}
      {hint && <span className="hint">{hint}</span>}
      {error && <span className="err" role="alert">{error}</span>}
    </div>
  );
}

export function Slider({ label, value, min, max, step = 1, onChange, format }: {
  label: string; value: number; min: number; max: number; step?: number;
  onChange: (v: number) => void; format?: (v: number) => string }) {
  const id = useId();
  return (
    <div className="slider-group">
      <label htmlFor={id}><span>{label}</span><span className="slider-value">{format ? format(value) : value}</span></label>
      <input id={id} type="range" min={min} max={max} step={step} value={value}
             onChange={(e) => onChange(Number(e.target.value))} />
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange, pill = true }: {
  tabs: { id: T; label: ReactNode; count?: number }[]; value: T; onChange: (t: T) => void; pill?: boolean }) {
  return (
    <div className={pill ? "tab-row" : "nav-tabs"} role="tablist">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={value === t.id}
                className={cx(pill ? "tab-btn" : "nav-tab", value === t.id && "active")}
                onClick={() => onChange(t.id)}>
          {t.label}{t.count != null && <span className="ml-1 opacity-70">({t.count})</span>}
        </button>
      ))}
    </div>
  );
}

export function FilterPills<T extends string>({ options, value, onChange, multi }: {
  options: { id: T; label: ReactNode }[]; value: T | T[]; onChange: (v: any) => void; multi?: boolean }) {
  const sel = Array.isArray(value) ? value : [value];
  return (
    <div className="filter-pills" role="group">
      {options.map((o) => (
        <button key={o.id} className={cx("filter-pill", sel.includes(o.id) && "active")} aria-pressed={sel.includes(o.id)}
                onClick={() => multi ? onChange(sel.includes(o.id) ? sel.filter((x) => x !== o.id) : [...sel, o.id]) : onChange(o.id)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

// ---- overlays ---------------------------------------------------------------

function useEscape(onClose: () => void) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
}

export function Drawer({ open, onClose, title, children, footer }: {
  open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; footer?: ReactNode }) {
  useEscape(onClose);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { if (open) ref.current?.focus(); }, [open]);
  if (!open) return null;
  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" tabIndex={-1} ref={ref}>
        <div className="panel-header"><h2>{title}</h2><Btn kind="ghost" size="sm" onClick={onClose} aria-label="Close">✕</Btn></div>
        <div className="flex-1 overflow-y-auto p-5 flex flex-col gap-4">{children}</div>
        {footer && <div className="p-4 border-t border-line/10 flex gap-2 justify-end">{footer}</div>}
      </aside>
    </>
  );
}

export function Modal({ open, onClose, title, children, footer }: {
  open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; footer?: ReactNode }) {
  useEscape(onClose);
  if (!open) return null;
  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <div className="modal" role="dialog" aria-modal="true">
        <div className="panel-header"><h2>{title}</h2><Btn kind="ghost" size="sm" onClick={onClose} aria-label="Close">✕</Btn></div>
        <div className="p-5 flex flex-col gap-4">{children}</div>
        {footer && <div className="p-4 flex gap-2 justify-end" style={{ borderTop: "1px solid var(--border)" }}>{footer}</div>}
      </div>
    </>
  );
}
