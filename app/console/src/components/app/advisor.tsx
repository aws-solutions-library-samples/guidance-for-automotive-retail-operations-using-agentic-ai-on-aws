"use client";

// Advisor reference components (sales-advisor-v2.html). Also reused by the
// Service Desk operating screens per spec §5.20: rec-card → finding / estimate
// line, incentive-row → coverage row, compare-table → good-better-best, etc.

import { ReactNode } from "react";
import { Avatar, Btn, cx, ProbabilityBar } from "./primitives";

export function CustomerCard({ name, meta, score, right }: {
  name: string; meta?: ReactNode; score?: ReactNode; right?: ReactNode }) {
  return (
    <div className="customer-card">
      <Avatar name={name} />
      <div className="min-w-0 flex-1">
        <div className="customer-name truncate">{name}</div>
        {meta && <div className="customer-meta">{meta}</div>}
        {score != null && <div className="mt-1"><span className="lead-score">{score}</span></div>}
      </div>
      {right}
    </div>
  );
}

export function ActivityItem({ time, children, tone }: { time?: ReactNode; children: ReactNode; tone?: "success" | "warning" | "danger" }) {
  return (
    <div className="activity-item" style={tone ? { borderLeftColor: `var(--${tone})` } : undefined}>
      <span className="activity-time">{time}</span>
      <span className="activity-text">{children}</span>
    </div>
  );
}

export function RecCard({ icon, title, right, children, className }: {
  icon?: ReactNode; title: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={cx("rec-card", className)}>
      <div className="rec-card-header">
        <div className="rec-card-title">{icon && <span aria-hidden>{icon}</span>}{title}</div>
        {right}
      </div>
      <div className="rec-card-body">{children}</div>
    </div>
  );
}

export function Confidence({ children, tone }: { children: ReactNode; tone?: "warn" | "danger" }) {
  return <span className={cx("confidence", tone)}>{children}</span>;
}

export function VehicleItem({ rank, name, specs, tags, action }: {
  rank: number; name: ReactNode; specs?: ReactNode; tags?: ReactNode; action?: ReactNode }) {
  return (
    <div className="vehicle-item">
      <div className="vehicle-rank">{rank}</div>
      <div className="flex-1 min-w-0">
        <div className="vehicle-name">{name}</div>
        {specs && <div className="vehicle-specs">{specs}</div>}
        {tags && <div className="vehicle-tags">{tags}</div>}
      </div>
      {action}
    </div>
  );
}

export function TalkTrack({ children }: { children: ReactNode }) {
  return <blockquote className="talk-track m-0">{children}</blockquote>;
}

export function CrossSellRow({ name, detail, probability, action }: {
  name: ReactNode; detail?: ReactNode; probability?: number; action?: ReactNode }) {
  return (
    <div className="cross-sell-row">
      <div className="flex flex-col gap-[2px] min-w-0">
        <span className="cross-sell-name">{name}</span>
        {detail && <span className="cross-sell-detail">{detail}</span>}
      </div>
      <div className="flex items-center gap-2">
        {probability != null && <ProbabilityBar value={probability} />}
        {action}
      </div>
    </div>
  );
}

export function ObjectionAlert({ label = "Detected objection", quote, children }: {
  label?: ReactNode; quote?: ReactNode; children?: ReactNode }) {
  return (
    <div className="objection-alert" role="alert">
      <div className="objection-label">⚠ {label}</div>
      {quote && <div className="objection-quote">“{quote}”</div>}
      {children}
    </div>
  );
}

export function AiResponseCard({ approach, successRate, children, onUse, onAlt, used, useLabel = "Use This", altLabel = "Alternative" }: {
  approach?: ReactNode; successRate?: number | null; children: ReactNode;
  onUse?: () => void; onAlt?: () => void; used?: boolean; useLabel?: string; altLabel?: string }) {
  return (
    <div className="ai-response-card">
      <div className="ai-response-label">
        <span className="ai-tag">✦ AI suggested response{approach ? <> · <span style={{ color: "var(--primary-light)" }}>{approach}</span></> : null}</span>
        {successRate != null && <span className="success-rate">{Math.round(successRate > 1 ? successRate : successRate * 100)}% success</span>}
      </div>
      <div className="ai-response-text">{children}</div>
      {(onUse || onAlt) && (
        <div className="response-btns">
          {onUse && <Btn kind="use" used={used} onClick={onUse} disabled={used}>{used ? "✓ Used" : useLabel}</Btn>}
          {onAlt && <Btn kind="alt" onClick={onAlt}>{altLabel}</Btn>}
        </div>
      )}
    </div>
  );
}

export function CompeteCard({ name, win, pros, cons }: {
  name: ReactNode; win?: ReactNode; pros?: ReactNode[]; cons?: ReactNode[] }) {
  return (
    <div className="compete-card">
      <div className="compete-header"><span className="compete-name">{name}</span>{win != null && <span className="win-badge">{win}</span>}</div>
      <ul className="compete-list">
        {(pros || []).map((p, i) => <li key={`p${i}`}>{p}</li>)}
        {(cons || []).map((c, i) => <li key={`c${i}`} className="weaker">{c}</li>)}
      </ul>
    </div>
  );
}

export function IncentiveRow({ eligible, name, amount, right }: {
  eligible: boolean; name: ReactNode; amount?: ReactNode; right?: ReactNode }) {
  return (
    <div className={cx("incentive-row", eligible ? "eligible" : "ineligible")}>
      <span aria-hidden>{eligible ? "✅" : "○"}</span>
      <div className="incentive-info">
        <div className="incentive-name">{name}</div>
        {amount && <div className="incentive-amount">{amount}</div>}
      </div>
      {right}
    </div>
  );
}

export interface CompareRow { label: ReactNode; a: ReactNode; b: ReactNode; winner?: "a" | "b" }
/** Side-by-side compare-table (EV vs ICE, good-better-best, repair-vs-replace). */
export function CompareTable({ aLabel, bLabel, rows, footnote, aClass = "col-ev", bClass = "col-ice" }: {
  aLabel: ReactNode; bLabel: ReactNode; rows: CompareRow[]; footnote?: ReactNode; aClass?: string; bClass?: string }) {
  return (
    <div>
      <table className="compare-table">
        <thead><tr><th scope="col"></th><th scope="col">{aLabel}</th><th scope="col">{bLabel}</th></tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td>{r.label}</td>
              <td className={aClass}>{r.a}{r.winner === "a" && <span className="compare-winner">WIN</span>}</td>
              <td className={bClass}>{r.b}{r.winner === "b" && <span className="compare-winner">WIN</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {footnote && <div className="dim text-[.7rem] mt-2">{footnote}</div>}
    </div>
  );
}

export function BenefitItem({ icon, title, children }: { icon?: ReactNode; title: ReactNode; children?: ReactNode }) {
  return (
    <div className="benefit-item">
      {icon && <span className="text-lg leading-tight" aria-hidden>{icon}</span>}
      <div><div className="benefit-title">{title}</div>{children && <div className="benefit-detail">{children}</div>}</div>
    </div>
  );
}

export function SavingsBanner({ label, value }: { label: ReactNode; value: ReactNode }) {
  return <div className="savings-banner"><span className="sb-label">{label}</span><span className="sb-value">{value}</span></div>;
}

export function PtChip({ powertrain }: { powertrain?: string }) {
  const ev = ["ev", "bev", "electric", "phev"].includes((powertrain || "").toLowerCase());
  return <span className={cx("pt-chip", ev ? "pt-ev" : "pt-ice")}>{ev ? (powertrain || "EV").toUpperCase() : (powertrain || "ICE").toUpperCase()}</span>;
}
