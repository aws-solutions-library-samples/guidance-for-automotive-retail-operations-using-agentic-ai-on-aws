"use client";

// Admin › Entity merge queue (§4.7): low-confidence matches between external
// ids (CRM / CDP / DMS) and solution-minted ids. Approve confirms the link; reject
// breaks it so the external record is treated as a distinct entity.

import { useState } from "react";
import { apiPost } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Async, Badge, Btn, Card, DataTable, DateTime, Field, FilterPills, Modal, Page, Pct, ViewHeader, useToast } from "@/components/app";
import { TenantOnly } from "../common";

export default function EntitiesPage() {
  const toast = useToast();
  const [status, setStatus] = useState("pending");
  const q = useApi<any>(`/admin/entities?status=${status}`);
  const [dec, setDec] = useState<{ item: any; decision: "approve" | "reject" } | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  const decide = async () => {
    if (!dec) return;
    setBusy(true);
    try {
      await apiPost("/admin/entities/decide", { item_key: dec.item.item_key, system: dec.item.system, decision: dec.decision, reason: reason || undefined });
      toast(`${dec.item.app_id}: ${dec.decision === "approve" ? "link confirmed" : "link broken"}`, "success");
      setDec(null); setReason(""); q.reload();
    } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
  };

  return (
    <Page>
      <ViewHeader title="Entity merge queue" subtitle="Human review of low-confidence ID matches across CRM, CDP and DMS." />
      <TenantOnly what="Entity resolution">
        <Card>
          <FilterPills value={status} onChange={setStatus} options={[
            { id: "pending", label: "Pending" }, { id: "approved", label: "Approved" }, { id: "rejected", label: "Rejected" }, { id: "all", label: "All" }]} />
          <Async q={q}>
            {(d) => (
              <>
                <div className="dim text-xs mb-2">Threshold {Math.round(d.threshold * 100)}% · {d.pending} pending</div>
                <DataTable rows={d.items} rowKey={(r: any) => `${r.item_key}|${r.system}`} empty="Queue is clear."
                  columns={[
                    { key: "type", header: "Entity", render: (r: any) => <Badge tone="blue">{r.entity_type}</Badge> },
                    { key: "app", header: "Solution record", render: (r: any) => <div><span className="mono">{r.app_id}</span>{r.app && <div className="dim text-xs">{r.app.label} · {r.app.dealer_id}</div>}</div> },
                    { key: "cand", header: "Possible duplicate of", render: (r: any) => r.duplicate_of ? <div><span className="mono">{r.duplicate_of}</span>{r.candidate && <div className="dim text-xs">{r.candidate.label} · {r.candidate.dealer_id}</div>}</div> : <span className="dim">—</span> },
                    { key: "ext", header: "External id", render: (r: any) => <span className="mono text-xs">{r.system.toUpperCase()} · {r.external_id}</span> },
                    { key: "conf", header: "Confidence", num: true, sort: (r: any) => r.confidence_before ?? r.confidence, render: (r: any) => <Pct value={r.confidence_before ?? r.confidence} /> },
                    { key: "st", header: "Status", render: (r: any) => (
                      <span><Badge tone={r.merge_status === "approved" ? "green" : r.merge_status === "rejected" ? "red" : "orange"}>{r.merge_status}</Badge>
                        {r.decided_at && <div className="dim text-xs">{r.decided_by} · <DateTime value={r.decided_at} /></div>}</span>) },
                    { key: "act", header: "", render: (r: any) => r.merge_status === "pending" && (
                      <div className="flex gap-1">
                        <Btn kind="alt" size="sm" onClick={() => setDec({ item: r, decision: "approve" })}>Confirm</Btn>
                        <Btn kind="ghost" size="sm" onClick={() => setDec({ item: r, decision: "reject" })}>Reject</Btn>
                      </div>) },
                  ]} />
              </>
            )}
          </Async>
        </Card>
      </TenantOnly>
      <Modal open={!!dec} onClose={() => setDec(null)} title={dec?.decision === "approve" ? "Confirm match" : "Reject match"}
        footer={<><Btn kind="secondary" onClick={() => setDec(null)}>Cancel</Btn>
          <Btn kind={dec?.decision === "reject" ? "danger" : "primary"} disabled={busy} onClick={decide}>{dec?.decision === "approve" ? "Confirm link" : "Break link"}</Btn></>}>
        <p className="text-sm">{dec?.decision === "approve"
          ? <>Treat <span className="mono">{dec?.item.system}:{dec?.item.external_id}</span> as <span className="mono">{dec?.item.app_id}</span>{dec?.item.duplicate_of ? <> (duplicate of <span className="mono">{dec?.item.duplicate_of}</span>)</> : null}.</>
          : <>Keep <span className="mono">{dec?.item.app_id}</span> separate from <span className="mono">{dec?.item.duplicate_of}</span>.</>}</p>
        <Field label="Reason (optional)">{(id) => <input id={id} className="input" value={reason} onChange={(e) => setReason(e.target.value)} />}</Field>
      </Modal>
    </Page>
  );
}
