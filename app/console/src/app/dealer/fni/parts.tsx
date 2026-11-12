"use client";

// F&I Desk building blocks (§5.13): readiness badges, the customer-facing menu
// presentation (brand theme, same rows the manager sees — §5.20 "same component
// in two themes" rule), and the compliance gate panel.

import { ReactNode } from "react";
import { Badge, Btn, IncentiveRow, Money, cx } from "@/components/app";

export const READINESS: { key: string; label: string }[] = [
  { key: "credit_app_in", label: "Credit app" },
  { key: "lender_decision", label: "Lender decision" },
  { key: "trade_agreed", label: "Trade" },
  { key: "vin_reserved", label: "VIN reserved" },
  { key: "compliance_clear", label: "Compliance" },
  { key: "pushed_to_dms", label: "DMS" },
];

export function ReadinessBadges({ readiness, compact }: { readiness?: Record<string, boolean> | null; compact?: boolean }) {
  if (!readiness) return null;
  return (
    <div className="flex flex-wrap gap-1" role="list" aria-label="Readiness">
      {READINESS.map((r) => (
        <span role="listitem" key={r.key} title={`${r.label}: ${readiness[r.key] ? "done" : "open"}`}>
          <Badge tone={readiness[r.key] ? "green" : "gray"}>{readiness[r.key] ? "✓" : "○"}{compact ? "" : " "}{compact ? r.label.split(" ")[0] : r.label}</Badge>
        </span>
      ))}
    </div>
  );
}

const CHECK_LABEL: Record<string, string> = {
  ofac: "OFAC sanctions screen", red_flags: "Red Flags identity verification", adverse_action: "Adverse-action notice",
};

export function ComplianceGate({ gate }: { gate: any }) {
  if (!gate) return null;
  return (
    <div className="flex flex-col gap-2" aria-live="polite">
      {Object.entries(gate.checks || {}).map(([k, v]: [string, any]) => {
        const ok = (gate.cleared || []).includes(k);
        return <IncentiveRow key={k} eligible={ok} name={CHECK_LABEL[k] || k}
                             amount={`status: ${String(v).replace(/_/g, " ")}`}
                             right={<Badge tone={ok ? "green" : "red"}>{ok ? "clear" : "blocking"}</Badge>} />;
      })}
      <div className={cx("text-xs", gate.ok ? "text-success" : "text-danger")}>{gate.note}</div>
    </div>
  );
}

/** Full-screen customer-facing menu (second screen / tablet handover). */
export function CustomerMenu({ ws, selected, onToggle, onClose, onSend, sending, brand }: {
  ws: any; selected: Set<string>; onToggle: (code: string) => void; onClose: () => void;
  onSend: () => void; sending: boolean; brand?: ReactNode }) {
  const chosen = (ws.menu || []).filter((m: any) => selected.has(m.code));
  const monthly = (ws.price?.monthly_payment || 0) + chosen.reduce((a: number, m: any) => a + (m.monthly_delta || 0), 0);
  return (
    <div data-theme="brand" className="fixed inset-0 z-[80] overflow-auto" role="dialog" aria-modal="true"
         aria-label="Protection menu" style={{ background: "var(--app-bg)", color: "var(--text-primary)" }}>
      <header className="app-header" style={{ minHeight: 64 }}>
        <span className="title">{brand || "Your protection options"}</span>
        <span className="subtitle ml-2">{ws.customer_name}</span>
        <Btn kind="ghost" className="ml-auto" style={{ color: "var(--app-header-text)" }} onClick={onClose} aria-label="Exit customer view">✕ Exit</Btn>
      </header>
      <div className="max-w-4xl mx-auto p-6">
        <h1 className="section-title">Protect your {ws.vehicle?.model_name || "new vehicle"}</h1>
        <p className="section-subtitle">Choose the coverage that fits how you drive. Every option is optional and can be removed before you sign.</p>
        <div className="grid gap-3 md:grid-cols-2">
          {(ws.menu || []).map((m: any) => {
            const on = selected.has(m.code);
            return (
              <button key={m.code} onClick={() => onToggle(m.code)} aria-pressed={on}
                      className={cx("rec-card text-left p-4 transition", on && "ring-2")}
                      style={on ? { borderColor: "var(--accent)" } : undefined}>
                <div className="flex items-center justify-between gap-2">
                  <b>{m.name}</b>
                  <span className={cx("badge", on ? "badge-green" : "badge-gray")}>{on ? "Included" : "Add"}</span>
                </div>
                {m.ai_attach?.why && <div className="muted text-sm mt-1">{m.ai_attach.why}</div>}
                <div className="mt-3 flex items-baseline justify-between">
                  <span className="text-lg font-bold"><Money value={m.price} /></span>
                  {m.monthly_delta != null && <span className="muted text-sm">+<Money value={m.monthly_delta} decimals={2} /> / month</span>}
                </div>
              </button>
            );
          })}
        </div>
        <div className="card mt-6 flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="dim text-xs uppercase tracking-wide">Estimated monthly payment</div>
            <div className="text-2xl font-bold"><Money value={monthly} decimals={2} /></div>
            <div className="dim text-xs">{ws.price?.term_months} months at {ws.price?.apr_pct}% APR · {chosen.length} protection product(s). Final terms on your contract.</div>
          </div>
          <Btn kind="primary" size="lg" onClick={onSend} disabled={sending}>{sending ? "Sending…" : "Send this menu to My Garage"}</Btn>
        </div>
      </div>
    </div>
  );
}
