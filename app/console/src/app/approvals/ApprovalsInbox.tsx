"use client";

// Approvals inbox (§4.8, G-23) — shared by /approvals (every approver persona)
// and /admin/approvals. Lists what the caller may see, highlights what they may
// decide (never their own requests; role + scope enforced server-side), and
// records approve / decline with an explicit confirmation that spells out the
// impact. Approving a budget shift or allocation records the decision only —
// nothing is applied automatically. Renders in the caller's own app theme.

import { agentLabel } from "@/lib/agents";
import { humanizeCode } from "@/lib/text";
import { type ReactNode, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiPost } from "@/lib/api";
import { useApi, useQueryParam } from "@/lib/hooks";
import { resolve, resourceUrl } from "@/lib/resourceUrl";
import { Async, Badge, Btn, Card, DataTable, DateTime, Drawer, Field, FilterPills, KpiGrid, Modal, Money, Page, ViewHeader, useToast } from "@/components/app";

const TONE: Record<string, any> = { pending: "orange", approved: "green", rejected: "red" };

/** Impact text shown in the confirm dialog, per approval type (§4.8, design §1.3). */
function impactText(a: any, decision: "approve" | "reject"): string {
  const t = a?.type;
  if (decision === "reject") return "Declining records the decision with your reason; the requester is notified and nothing changes.";
  if (t === "budget_shift") return "Approving records the decision only — campaign spend is shifted by marketing operations, never automatically (hard rule §4.10.1).";
  if (t === "ci_ask") return "Approving records the incentive decision; allocations are applied by planning, never automatically.";
  if (t === "goodwill") return "Approving authorizes the goodwill amount against the store's goodwill budget.";
  if (t === "action_plan_publish" || t === "district_action_plan") return "Approving publishes the plan to the dealer and district; its milestones become tracked tasks and overdue milestones escalate through the Notification Hub.";
  if (t === "deal_exception") return "Approving authorizes this one deal exception; the desk applies it to the worksheet and the exception is recorded against store policy.";
  if (t === "stage_parts") return "Approving authorizes the parts order above the staging cap; the parts desk places the order.";
  if (t === "agent_action") return "Approving lets the agent execute the prepared action exactly as shown in the payload, at act_with_approval autonomy.";
  return "Approving records your decision and notifies the requester; downstream actions run at their configured autonomy level.";
}

/** Expected-impact summary for an A5 budget-shift payload (§3.5): the shift and
 *  its projected lift in plain words, above the raw payload. */
function BudgetShiftSummary({ p }: { p: any }) {
  if (!p?.from || !p?.to) return null;
  return (
    <div className="ai-insight" role="note">
      <div className="ai-insight-title">◆ Expected impact</div>
      <p className="text-sm">
        Shift <b>{typeof p.amount === "number" ? <Money value={p.amount} /> : p.amount}</b> from{" "}
        <b>{p.from.name || p.from.campaign_id}</b> (ROI {p.from.roi ?? "—"}) to <b>{p.to.name || p.to.campaign_id}</b> (ROI {p.to.roi ?? "—"})
        {p.expected_extra_deliveries != null && <> — expected <b>+{p.expected_extra_deliveries}</b> deliveries</>}
        {p.expected_lift_gross != null && <> (<Money value={Number(p.expected_lift_gross)} /> projected gross{p.expected_net_gain != null ? <>, net <Money value={Number(p.expected_net_gain)} /></> : null})</>}.
      </p>
    </div>
  );
}

/** A request's payload as labelled rows — never a JSON dump. Nested objects read as
 *  "name · ROI 2" style one-liners; lists are joined; money keys render as money. */
const MONEY_KEY = /(amount|total|price|gross|gain|budget|cost|value|spend)$/i;
function payloadValue(k: string, v: any): ReactNode {
  if (v == null || v === "") return "—";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "number") return MONEY_KEY.test(k) ? <Money value={v} /> : v.toLocaleString();
  if (Array.isArray(v)) return v.length ? v.map((x) => (typeof x === "object" ? Object.values(x || {}).filter((y) => typeof y !== "object").join(" · ") : String(x))).join(", ") : "—";
  if (typeof v === "object") {
    const name = v.name || v.title || v.label;
    const rest = Object.entries(v).filter(([kk, vv]) => !["name", "title", "label"].includes(kk) && vv != null && typeof vv !== "object");
    return <>{name ? <b>{String(name)}</b> : null}{rest.map(([kk, vv], i) => <span key={kk}>{name || i ? " · " : ""}{humanizeCode(kk)} {typeof vv === "number" && MONEY_KEY.test(kk) ? <Money value={vv} /> : String(vv)}</span>)}</>;
  }
  const str = String(v);
  return /^[a-z]+(_[a-z]+)+$/.test(str) ? humanizeCode(str) : str;
}
function PayloadView({ p }: { p: Record<string, any> | null | undefined }) {
  const rows = Object.entries(p || {}).filter(([, v]) => v !== undefined);
  if (!rows.length) return <div className="dim text-sm">No details recorded.</div>;
  return (
    <table className="data-table"><tbody>
      {rows.map(([k, v]) => <tr key={k}><td className="muted">{humanizeCode(k)}</td><td className="text-xs">{payloadValue(k, v)}</td></tr>)}
    </tbody></table>
  );
}

export function ApprovalsInbox({ title = "Approvals" }: { title?: string }) {
  const toast = useToast();
  const router = useRouter();
  const [status, setStatus] = useState("pending");
  // `?status=approved|rejected|all` deep-links a filter (notifications, the demo replay); default stays Pending
  const statusParam = useQueryParam("status");
  useEffect(() => { if (statusParam && ["pending", "approved", "rejected", "all"].includes(statusParam)) setStatus(statusParam); }, [statusParam]);
  const q = useApi<any>(`/approvals${status === "all" ? "" : `?status=${status}`}`, { pollMs: 60000 });
  const [sel, setSel] = useState<any | null>(null);
  const [reason, setReason] = useState("");
  const [confirm, setConfirm] = useState<"approve" | "reject" | null>(null);
  const [busy, setBusy] = useState(false);

  const decide = async () => {
    if (!sel || !confirm) return;
    setBusy(true);
    try {
      await apiPost(`/approvals/${encodeURIComponent(sel.approval_id)}/decide`, { decision: confirm, reason: reason || undefined });
      toast(`${sel.type_label} ${confirm === "approve" ? "approved" : "declined"}`, "success");
      setSel(null); setReason(""); setConfirm(null); q.reload();
    } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
  };
  const openSubject = async (a: any) => {
    const p = a.payload || {};
    const ref = p.plan_id ? resourceUrl("plan", p.plan_id) : p.deal_id ? resourceUrl("deal", p.deal_id)
      : p.journey_id ? resourceUrl("journey", p.journey_id) : p.service_journey_id ? resourceUrl("service_journey", p.service_journey_id) : null;
    if (!ref) return;
    const r = await resolve(ref);
    if (r.allowed && r.url) router.push(r.url); else toast(r.reason || "You can't open this item", "error");
  };

  return (
    <Page>
      <ViewHeader title={title} subtitle="Human decisions on agent drafts, plan publishing, incentive asks, goodwill, deal exceptions and budget shifts." />
      <Async q={q}>
        {(d) => (
          <>
            <KpiGrid items={[
              { label: "Pending", value: d.pending }, { label: "Waiting on you", value: d.mine_to_decide, trend: d.mine_to_decide ? "down" : undefined },
              { label: "Shown", value: d.count }, { label: "Types", value: Object.keys(d.types || {}).length },
            ]} />
            <Card>
              <FilterPills value={status} onChange={setStatus} options={[
                { id: "pending", label: "Pending" }, { id: "approved", label: "Approved" }, { id: "rejected", label: "Declined" }, { id: "all", label: "All" }]} />
              <DataTable rows={d.approvals} rowKey={(a: any) => a.approval_id} onRowClick={(a: any) => setSel(a)} empty="Nothing to approve."
                columns={[
                  { key: "type", header: "Type", sort: (a: any) => a.type_label, render: (a: any) => <Badge tone="blue">{a.type_label}</Badge> },
                  { key: "id", header: "Request", render: (a: any) => <span className="mono text-xs">{a.approval_id}</span> },
                  { key: "by", header: "Requested by", render: (a: any) => <span className="text-xs">{a.requested_by_name || agentLabel(a.requested_by) || "—"}</span> },
                  { key: "when", header: "Requested", sort: (a: any) => a.requested_at, render: (a: any) => <DateTime value={a.requested_at} /> },
                  { key: "scope", header: "Scope", render: (a: any) => <span className="mono text-xs">{a.scope?.dealer_id || a.scope?.district_id || a.dealer_id || "—"}</span> },
                  { key: "st", header: "Status", sort: (a: any) => a.status, render: (a: any) => <Badge tone={TONE[a.status] || "gray"}>{humanizeCode(a.status)}</Badge> },
                  { key: "me", header: "", render: (a: any) => a.can_decide ? <Badge tone="orange">your decision</Badge> : null },
                ]} />
            </Card>
          </>
        )}
      </Async>
      <Drawer open={!!sel} onClose={() => { setSel(null); setConfirm(null); }} title={sel?.type_label || "Approval"}
        footer={sel?.can_decide ? <>
          <Btn kind="danger" disabled={busy || !reason.trim()} onClick={() => setConfirm("reject")} title="A decline needs a reason">Decline…</Btn>
          <Btn kind="primary" disabled={busy} onClick={() => setConfirm("approve")}>Approve…</Btn></> : undefined}>
        {sel && (
          <>
            <div className="flex gap-2 flex-wrap"><Badge tone={TONE[sel.status] || "gray"}>{humanizeCode(sel.status)}</Badge><span className="mono text-xs">{sel.approval_id}</span></div>
            <table className="data-table"><tbody>
              <tr><td className="muted">Requested by</td><td>{sel.requested_by_name || agentLabel(sel.requested_by) || "—"}</td></tr>
              <tr><td className="muted">Requested</td><td><DateTime value={sel.requested_at} /></td></tr>
              <tr><td className="muted">Approver roles</td><td>{(sel.approver_roles || []).map((r: string) => <Badge key={r} tone="gray">{humanizeCode(r)}</Badge>)}</td></tr>
              {sel.decided_by && <tr><td className="muted">Decided</td><td>{sel.decided_by_name || agentLabel(sel.decided_by)} · <DateTime value={sel.decided_at} />{sel.decision_reason ? ` — ${sel.decision_reason}` : ""}</td></tr>}
              {sel.reason && <tr><td className="muted">Justification</td><td>{sel.reason}</td></tr>}
            </tbody></table>
            {sel.type === "budget_shift" && <BudgetShiftSummary p={sel.payload} />}
            <div className="section-label">Details</div>
            <PayloadView p={sel.payload} />
            {(sel.payload?.plan_id || sel.payload?.deal_id || sel.payload?.journey_id || sel.payload?.service_journey_id) &&
              <Btn kind="alt" size="sm" onClick={() => openSubject(sel)}>Open subject</Btn>}
            {(sel.type === "budget_shift" || sel.type === "ci_ask") && <div className="dim text-xs">Approval records the decision; spend and allocations are never changed automatically.</div>}
            {sel.can_decide
              ? <Field label="Reason" hint="Required to decline; recorded with the decision.">{(id) => <textarea id={id} className="textarea" value={reason} onChange={(e) => setReason(e.target.value)} />}</Field>
              : sel.status === "pending" && <div className="dim text-sm">{sel.requested_by ? "You can't decide this request (your own request, or outside your role or scope)." : ""}</div>}
          </>
        )}
      </Drawer>
      <Modal open={!!confirm} onClose={() => setConfirm(null)}
        title={confirm === "approve" ? `Approve ${sel?.type_label || ""}?` : `Decline ${sel?.type_label || ""}?`}
        footer={<>
          <Btn kind="secondary" onClick={() => setConfirm(null)}>Cancel</Btn>
          <Btn kind={confirm === "reject" ? "danger" : "primary"} disabled={busy} onClick={decide}>
            {busy ? "Recording…" : confirm === "approve" ? "Approve" : "Decline"}</Btn>
        </>}>
        <p className="text-sm"><b className="mono">{sel?.approval_id}</b> · {sel?.type_label}</p>
        <p className="text-sm">{sel && confirm ? impactText(sel, confirm) : ""}</p>
        {confirm === "reject" && <p className="dim text-xs">Reason recorded: “{reason}”</p>}
      </Modal>
    </Page>
  );
}
