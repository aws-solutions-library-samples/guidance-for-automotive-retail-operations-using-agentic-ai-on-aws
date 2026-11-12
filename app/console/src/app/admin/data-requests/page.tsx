"use client";

// Admin › Data requests (§8.2, acceptance #25): customer download / delete
// requests filed from My Garage (or by an admin on the customer's behalf).
// Download assembles every customer-linked row with C4 fields excluded; delete
// anonymises the customer across stores (irreversible — typed confirmation) and
// lists the Personalize / Quick Index purge steps that run in those services.

import { humanizeCode } from "@/lib/text";
import { useState } from "react";
import { apiPost } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Async, Badge, Btn, Card, DataTable, DateTime, Field, FilterPills, Modal, Page, ViewHeader, useToast } from "@/components/app";
import { ConfirmModal, downloadFile, TenantOnly } from "../common";

const TONE: Record<string, any> = { received: "orange", in_progress: "blue", completed: "green", failed: "red" };

export default function DataRequestsPage() {
  const toast = useToast();
  const [status, setStatus] = useState("all");
  const q = useApi<any>(`/admin/data-requests${status === "all" ? "" : `?status=${status}`}`);
  const [run, setRun] = useState<any | null>(null);
  const [steps, setSteps] = useState<any | null>(null);
  const [create, setCreate] = useState(false);
  const [form, setForm] = useState({ customer_id: "", type: "download" });
  const [busy, setBusy] = useState(false);

  const execute = async (r: any) => {
    setBusy(true);
    try {
      const out = await apiPost<any>(`/admin/data-requests/${encodeURIComponent(r.request_id)}/run`,
                                     r.type === "delete" ? { confirm: r.customer_id } : {});
      if (out.export) {
        downloadFile(`retail-ops-data-${r.customer_id}.json`, JSON.stringify(out.export, null, 2), "application/json");
        toast(`Export ready: ${out.export.row_count} rows; ${out.export.c4_fields_excluded.length} sensitive field types excluded`, "success");
      } else {
        toast(out.status === "completed" ? `Customer anonymised; ${out.pending_external_steps} external purge step(s) pending` : `Deletion failed — see steps`,
              out.status === "completed" ? "success" : "error");
      }
      setRun(null); setSteps(out); q.reload();
    } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
  };
  const submit = async () => {
    setBusy(true);
    try {
      await apiPost("/admin/data-requests", form);
      toast("Request filed", "success"); setCreate(false); q.reload();
    } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
  };

  return (
    <Page>
      <ViewHeader title="Data requests" subtitle="Customer data download and deletion across every store."
        actions={<Btn size="sm" onClick={() => setCreate(true)}>＋ File request</Btn>} />
      <TenantOnly what="Data-subject requests">
        <Card>
          <FilterPills value={status} onChange={setStatus} options={[
            { id: "all", label: "All" }, { id: "received", label: "Received" }, { id: "in_progress", label: "In progress" },
            { id: "completed", label: "Completed" }, { id: "failed", label: "Failed" }]} />
          <Async q={q}>
            {(d) => (
              <DataTable rows={d.requests} rowKey={(r: any) => r.request_id} empty="No data requests."
                initialSort={{ key: "when", dir: "desc" }}
                columns={[
                  { key: "id", header: "Request", render: (r: any) => <span className="mono text-xs">{r.request_id}</span> },
                  { key: "cust", header: "Customer", sort: (r: any) => r.customer_id, render: (r: any) => <span className="mono">{r.customer_id}</span> },
                  { key: "type", header: "Type", render: (r: any) => <Badge tone={r.type === "delete" ? "red" : "blue"}>{humanizeCode(r.type)}</Badge> },
                  { key: "ch", header: "Filed via", render: (r: any) => <span className="text-xs">{r.channel}{r.requested_by && r.requested_by !== r.customer_id ? ` · ${r.requested_by}` : ""}</span> },
                  { key: "when", header: "Requested", sort: (r: any) => r.requested_at, render: (r: any) => <DateTime value={r.requested_at} /> },
                  { key: "st", header: "Status", sort: (r: any) => r.status, render: (r: any) => (
                    <span><Badge tone={TONE[r.status]}>{r.status.replace("_", " ")}</Badge>
                      {r.pending_external_steps ? <Badge tone="orange">{r.pending_external_steps} external pending</Badge> : null}</span>) },
                  { key: "act", header: "", render: (r: any) => (
                    <div className="flex gap-1">
                      {(r.status === "received" || r.status === "failed") &&
                        <Btn kind={r.type === "delete" ? "danger" : "alt"} size="sm" disabled={busy}
                             onClick={() => (r.type === "delete" ? setRun(r) : execute(r))}>{r.type === "delete" ? "Erase…" : "Run export"}</Btn>}
                      {r.steps?.length > 0 && <Btn kind="ghost" size="sm" onClick={() => setSteps(r)}>Steps</Btn>}
                    </div>) },
                ]} />
            )}
          </Async>
        </Card>
      </TenantOnly>
      <ConfirmModal open={!!run} danger title="Erase customer data" confirmLabel="Erase permanently" pending={busy}
        requireText={run?.customer_id} onClose={() => setRun(null)} onConfirm={() => run && execute(run)}
        body={<div className="flex flex-col gap-2">
          <p>Anonymises <b className="mono">{run?.customer_id}</b> across every operational table: names, contact details and free text are erased, sensitive (C4) fields dropped, and all channels opted out.</p>
          <p className="muted">Deals and repair orders are kept for legal retention without personal data. This cannot be undone.</p>
        </div>} />
      <Modal open={!!steps} onClose={() => setSteps(null)} title={`Steps · ${steps?.request_id || ""}`}>
        <table className="data-table"><thead><tr><th>System</th><th>Action</th><th>Rows</th><th>Status</th></tr></thead>
          <tbody>{(steps?.steps || []).map((s: any, i: number) => (
            <tr key={i}><td>{s.system}</td><td className="text-xs">{s.action}</td><td className="num">{s.rows ?? "—"}</td>
              <td><Badge tone={s.status === "done" ? "green" : s.status === "failed" ? "red" : "orange"}>{s.status.replace("_", " ")}</Badge>{s.error && <div className="text-xs" style={{ color: "var(--danger)" }}>{s.error}</div>}</td></tr>
          ))}</tbody></table>
      </Modal>
      <Modal open={create} onClose={() => setCreate(false)} title="File a data request"
        footer={<><Btn kind="secondary" onClick={() => setCreate(false)}>Cancel</Btn><Btn disabled={busy || !form.customer_id.trim()} onClick={submit}>File</Btn></>}>
        <Field label="Customer id">{(id) => <input id={id} className="input mono" value={form.customer_id} onChange={(e) => setForm({ ...form, customer_id: e.target.value.trim() })} />}</Field>
        <Field label="Type">{(id) => (
          <select id={id} className="select" value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}>
            <option value="download">Download my data</option><option value="delete">Delete my data</option>
          </select>)}</Field>
      </Modal>
    </Page>
  );
}
