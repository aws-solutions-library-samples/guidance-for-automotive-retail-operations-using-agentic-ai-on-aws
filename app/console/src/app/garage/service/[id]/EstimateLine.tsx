"use client";

// One estimate line in the customer-facing approval (design §6.1, spec §5.20.4
// customer mode, §5.12). Built from the same rec-card / confidence /
// incentive-row primitives the service advisor presents with, so what the
// customer approves is visibly the same thing the advisor presented — one
// component structure, two themes. Photo/video evidence carries alt text and
// captions; coverage (warranty / recall / customer pay) is a labeled row, and
// decisions are large touch targets.

import { useState } from "react";
import { Badge, Btn, Confidence, IncentiveRow, Money, RecCard, StatusChip, cx } from "@/components/app";

export interface Line {
  line_id: string; item: string; status: string; urgency?: string; measurement?: number | null; unit?: string | null;
  finding_ref?: string | null; evidence_doc_id?: string | null; evidence_url?: string | null;
  evidence_video_url?: string | null; evidence_caption?: string | null; work?: string | null;
  labor_amount?: number | null; parts_amount?: number | null; line_total: number; customer_pays: number; currency: string;
  coverage: { pay_type: string; label: string; source_ref?: string }; price_source?: string | null;
  explanation?: string | null; decision?: "approved" | "declined" | null;
  options?: { tier: string; label: string; total: number; detail?: string }[];
}

export function EstimateLine({ line, choice, onChoose, disabled, onAsk }: {
  line: Line; choice?: "approve" | "decline" | null; onChoose?: (c: "approve" | "decline") => void;
  disabled?: boolean; onAsk?: (line: Line) => void }) {
  const decided = line.decision;
  const urgencyTone = line.status === "red" ? "danger" : line.status === "yellow" ? "warn" : undefined;
  const urgencyLabel = line.status === "red" ? "Needs attention now" : line.status === "yellow" ? "Recommended soon" : "Good";
  const payType = line.coverage?.pay_type || "customer";
  const covered = payType === "warranty" || payType === "recall";
  const coverageLabel = line.coverage?.label
    || (payType === "warranty" ? "Covered by warranty" : payType === "recall" ? "Open recall — no charge" : "Customer pay");
  return (
    <RecCard
      icon={line.status === "red" ? "🔴" : line.status === "yellow" ? "🟡" : "🟢"}
      title={<span>{line.item}{line.work ? <span className="dim font-normal text-[.8rem]"> · {line.work}</span> : null}</span>}
      right={<Confidence tone={urgencyTone}>{urgencyLabel}</Confidence>}
      className={cx(decided === "declined" && "opacity-70")}
    >
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2 text-[.8rem]">
          <StatusChip status={line.status} />
          {line.measurement != null && <span className="muted">Measured {line.measurement}{line.unit ? ` ${line.unit}` : ""}</span>}
        </div>

        <Evidence line={line} />

        {line.explanation && <p className="m-0 text-[.85rem] leading-relaxed">{line.explanation}</p>}

        {/* coverage: warranty / recall / customer pay — label + amount, not color alone */}
        <IncentiveRow eligible={covered} name={coverageLabel}
                      amount={covered ? <>Saves you <Money value={line.line_total} decimals={2} /></> : line.price_source || undefined}
                      right={<span className="font-semibold"><Money value={line.customer_pays} decimals={2} /></span>} />

        {/* good-better-best options for this line, when the shop offers them */}
        {!decided && (line.options || []).length > 1 && (
          <div className="grid gap-2 sm:grid-cols-3" role="group" aria-label={`Options for ${line.item}`}>
            {line.options!.map((o) => (
              <div key={o.tier} className="rec-card p-3">
                <div className="font-semibold text-sm">{o.label}</div>
                {o.detail && <div className="text-xs muted">{o.detail}</div>}
                <div className="text-sm mt-1"><Money value={o.total} decimals={2} /></div>
              </div>
            ))}
          </div>
        )}

        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-[.72rem] dim">
            Labor <Money value={line.labor_amount ?? null} decimals={2} /> · Parts <Money value={line.parts_amount ?? null} decimals={2} />
          </div>
          {decided ? (
            <Badge tone={decided === "approved" ? "green" : "gray"}>
              {decided === "approved" ? "✓ Approved" : "Declined — saved so you can book it later"}
            </Badge>
          ) : onChoose ? (
            <div className="flex gap-2" role="radiogroup" aria-label={`Decision for ${line.item}`}>
              <Btn kind={choice === "approve" ? "success" : "alt"} size="lg" aria-pressed={choice === "approve"}
                   disabled={disabled} onClick={() => onChoose("approve")}>Approve</Btn>
              <Btn kind={choice === "decline" ? "secondary" : "alt"} size="lg" aria-pressed={choice === "decline"}
                   disabled={disabled} onClick={() => onChoose("decline")}
                   style={choice === "decline" ? { borderColor: "var(--text-secondary)", color: "var(--text-primary)" } : undefined}>
                Not now
              </Btn>
            </div>
          ) : null}
        </div>
        {onAsk && !decided && (
          <button className="self-start text-[.78rem] underline muted min-h-[44px]" onClick={() => onAsk(line)}>
            Ask a question about this
          </button>
        )}
      </div>
    </RecCard>
  );
}

/** Photo/video evidence with alt text and captions; degrades to a labeled badge
 *  when the technician's media isn't directly viewable. */
function Evidence({ line }: { line: Line }) {
  const [broken, setBroken] = useState(false);
  const caption = line.evidence_caption || `Technician photo of ${line.item.toLowerCase()}`;
  if (line.evidence_video_url) {
    return (
      <figure className="m-0">
        {/* captions track comes from the shop's media service when available */}
        <video className="w-full rounded-[var(--radius-sm)]" controls preload="metadata"
               src={line.evidence_video_url} aria-label={caption} />
        <figcaption className="text-xs muted mt-1">{caption}</figcaption>
      </figure>
    );
  }
  if (line.evidence_url && !broken) {
    return (
      <figure className="m-0">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img className="w-full max-h-64 object-cover rounded-[var(--radius-sm)]" src={line.evidence_url}
             alt={caption} onError={() => setBroken(true)} />
        <figcaption className="text-xs muted mt-1">{caption}</figcaption>
      </figure>
    );
  }
  if (line.evidence_doc_id) {
    return <div><Badge tone="blue" title={line.evidence_doc_id}>📷 Photo on file — your advisor can share it</Badge></div>;
  }
  return null;
}
