"use client";

// Tasks (§5.18.4, G-17) — the unified work queue every agent writes to. Mine /
// Team tabs, open / done / overdue filters, SLA due time, handoff provenance,
// one-click open via the resource-URL resolver, mark-done, manager reassignment
// and cancellation, and a task detail drawer. The resolver maps the canonical
// `/r/task/{id}` to `/tasks?focus={id}`, so arriving with ?focus= opens the
// drawer directly (§5.18.2). Renders in the caller's own app theme via the
// Shell. Mobile layout is mandatory (§5.19).

import { humanizeCode } from "@/lib/text";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  Badge, Btn, Card, DataTable, DateTime, Drawer, Field, InfoRow, Kpi, KpiGrid, Modal, Page, Tabs, ViewHeader, useToast, Async,
} from "@/components/app";
import { useApi, useMutation, useQueryParam } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";
import { resolve } from "@/lib/resourceUrl";

const MANAGER = ["dealer_manager", "dealer_service_manager", "oem_regional", "admin_tenant", "admin_dealer",
                 "platform_admin"];
type Status = "open" | "done" | "overdue" | "all";

export default function TasksPage() {
  const router = useRouter();
  const toast = useToast();
  const { persona } = usePersona();
  const isManager = MANAGER.includes(persona?.role || "");
  const [tab, setTab] = useState<"mine" | "team">("mine");
  const [status, setStatus] = useState<Status>("open");
  const qs = new URLSearchParams();
  if (status === "open" || status === "done") qs.set("status", status);
  if (tab === "team") qs.set("team", "1");
  const q = useApi<any>(`/tasks?${qs.toString()}`, { pollMs: 60000 });
  const patch = useMutation("PATCH");

  // Task detail drawer — opened by row click or by the canonical /r/task link
  // (?focus=TSK-…). Fetched fresh so notes and status are current.
  const focus = useQueryParam("focus");
  const [sel, setSel] = useState<string | null>(null);
  useEffect(() => { if (focus) setSel(focus); }, [focus]);
  const detail = useApi<any>(sel ? `/tasks/${encodeURIComponent(sel)}` : null);
  const [reassign, setReassign] = useState(false);
  const [assignee, setAssignee] = useState("");
  const [note, setNote] = useState("");
  const [cancel, setCancel] = useState(false);

  const open = async (t: any) => {
    if (!t.resource_url) return;
    const r = await resolve(t.resource_url);
    if (r.allowed && r.url) router.push(r.url);
    else toast(r.reason || "You can't open this item", "error");
  };
  const complete = async (t: any) => {
    const out = await patch.run(`/tasks/${encodeURIComponent(t.task_id)}`, { status: "done" });
    if (out) { toast(`Completed “${t.title}”`, "success"); q.reload(); if (sel) detail.reload(); }
    else toast(patch.error || "Could not complete", "error");
  };
  const doReassign = async () => {
    if (!sel || !assignee.trim()) return;
    const out = await patch.run(`/tasks/${encodeURIComponent(sel)}`, { assignee: assignee.trim(), note: note.trim() || undefined });
    if (out) { toast(`Reassigned to ${assignee.trim()}`, "success"); setReassign(false); setAssignee(""); setNote(""); q.reload(); detail.reload(); }
    else toast(patch.error || "Could not reassign", "error");
  };
  const doCancel = async () => {
    if (!sel) return;
    const out = await patch.run(`/tasks/${encodeURIComponent(sel)}`, { status: "cancelled", note: note.trim() || undefined });
    if (out) { toast("Task cancelled", "success"); setCancel(false); setNote(""); setSel(null); q.reload(); }
    else toast(patch.error || "Could not cancel", "error");
  };

  const all: any[] = q.data?.tasks || [];
  const tasks = status === "overdue" ? all.filter((t) => t.overdue) : all;
  const kpis: Kpi[] = [
    { label: "Open", value: all.filter((t) => t.status === "open" || t.status === "in_progress").length },
    { label: "Overdue", value: q.data?.overdue ?? 0, trend: (q.data?.overdue ?? 0) > 0 ? "down" : undefined, delta: (q.data?.overdue ?? 0) > 0 ? "needs action" : "on track" },
    { label: "Due today", value: all.filter((t) => (t.due_at || "").slice(0, 10) === new Date().toISOString().slice(0, 10)).length },
    { label: "From agents", value: all.filter((t) => t.source_agent).length },
  ];
  // Ignore a previously-loaded task while a different one is being fetched.
  const t = detail.data && detail.data.task_id === sel ? detail.data : null;
  const mayManage = isManager;
  const mayComplete = t && (t.status === "open" || t.status === "in_progress") && (t.assigned_user_id === persona?.userId || mayManage);

  return (
    <Page>
      <ViewHeader title="Tasks" subtitle="Your unified work queue — agents, handoffs, and teammates all assign work here."
                  actions={<Btn kind="secondary" size="sm" onClick={() => q.reload()}>Refresh</Btn>} />
      <KpiGrid items={kpis} />
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Tabs value={tab} onChange={setTab} tabs={[{ id: "mine", label: "Mine" }, ...(isManager ? [{ id: "team" as const, label: "Team" }] : [])]} />
          <Tabs value={status} onChange={setStatus} tabs={[
            { id: "open", label: "Open" }, { id: "done", label: "Done" },
            { id: "overdue", label: "Overdue", count: q.data?.overdue ?? undefined }, { id: "all", label: "All" }]} />
        </div>
        <Async q={q}>
          {() => (
            <DataTable
              rows={tasks}
              rowKey={(t) => t.task_id}
              onRowClick={(row: any) => setSel(row.task_id)}
              filterText={(t) => `${t.title} ${t.journey_id || ""} ${t.service_journey_id || ""}`}
              filterPlaceholder="Filter tasks…"
              initialSort={{ key: "due", dir: "asc" }}
              empty={status === "overdue" ? "Nothing is overdue." : "No tasks."}
              columns={[
                { key: "title", header: "Task", sort: (t) => t.title,
                  render: (t) => (
                    <span>{t.overdue && <Badge tone="red">Overdue</Badge>} {t.title}</span>
                  ) },
                { key: "source", header: "Source", render: (t) => (
                    <span className="flex gap-1 flex-wrap">
                      <Badge tone={t.source_agent ? "purple" : "gray"}>{t.source_agent || t.created_by || "—"}</Badge>
                      {(t.handoff_id || t.payload?.handoff_id) && <Badge tone="orange" title={t.handoff_id || t.payload?.handoff_id}>handoff</Badge>}
                    </span>) },
                { key: "ref", header: "About", render: (t) => <span className="mono text-xs">{t.journey_id || t.service_journey_id || "—"}</span> },
                ...(tab === "team" ? [{ key: "assignee", header: "Assignee", sort: (t: any) => t.assigned_user_id, render: (t: any) => <span>{t.assigned_user_name || t.assigned_user_id}{t.team ? <span className="dim text-xs"> · {t.team}</span> : null}</span> }] : []),
                { key: "due", header: "SLA due", sort: (t) => t.sla_due_at || t.due_at, render: (t) => <DateTime value={t.sla_due_at || t.due_at} /> },
                { key: "status", header: "Status", render: (t) => <Badge tone={t.status === "done" ? "green" : t.status === "cancelled" ? "gray" : "blue"}>{humanizeCode(t.status)}</Badge> },
                { key: "act", header: "", render: (t) => (
                    <span className="flex gap-1" onClick={(e) => e.stopPropagation()}>
                      {t.resource_url && <Btn kind="ghost" size="sm" onClick={() => open(t)}>Open</Btn>}
                      {(t.status === "open" || t.status === "in_progress") && t.assigned_user_id === persona?.userId &&
                        <Btn kind="alt" size="sm" onClick={() => complete(t)} disabled={patch.pending}>Mark done</Btn>}
                    </span>) },
              ]}
            />
          )}
        </Async>
      </Card>

      <Drawer open={!!sel} onClose={() => setSel(null)} title={t?.title || "Task"}
        footer={t ? <>
          {mayManage && (t.status === "open" || t.status === "in_progress") &&
            <Btn kind="danger" onClick={() => { setNote(""); setCancel(true); }}>Cancel task…</Btn>}
          {(mayManage || t.assigned_user_id === persona?.userId) && (t.status === "open" || t.status === "in_progress") &&
            <Btn kind="secondary" onClick={() => { setAssignee(""); setNote(""); setReassign(true); }}>Reassign…</Btn>}
          {mayComplete && <Btn kind="primary" disabled={patch.pending} onClick={() => complete(t)}>Mark done</Btn>}
        </> : undefined}>
        <Async q={{ ...detail, data: t }} rows={5}>
          {(x: any) => (
            <>
              <div className="flex gap-2 flex-wrap">
                <Badge tone={x.status === "done" ? "green" : x.status === "cancelled" ? "gray" : "blue"}>{humanizeCode(x.status)}</Badge>
                {x.overdue && <Badge tone="red">Overdue</Badge>}
                <span className="mono text-xs">{x.task_id}</span>
              </div>
              <InfoRow label="Assignee" value={<span className="text-xs">{x.assigned_user_name || x.assigned_user_id}{x.team ? ` · ${x.team}` : ""}</span>} />
              <InfoRow label="Source" value={<span>{x.source_agent ? <Badge tone="purple">{x.source_agent}</Badge> : x.created_by || "—"}</span>} />
              <InfoRow label="SLA due" value={<DateTime value={x.sla_due_at || x.due_at} />} />
              <InfoRow label="Created" value={<DateTime value={x.created_at} />} />
              {(x.journey_id || x.service_journey_id) && <InfoRow label="About" value={<span className="mono text-xs">{x.journey_id || x.service_journey_id}</span>} />}
              {(x.handoff_id || x.payload?.handoff_id) && <InfoRow label="Handoff" value={<span className="mono text-xs">{x.handoff_id || x.payload?.handoff_id}</span>} />}
              {x.completed_at && <InfoRow label="Completed" value={<span>{x.completed_by} · <DateTime value={x.completed_at} /></span>} />}
              {x.resource_url && <Btn kind="alt" size="sm" onClick={() => open(x)}>Open subject</Btn>}
              {(x.notes || []).length > 0 && (
                <>
                  <div className="section-label">Notes</div>
                  <ul className="list-none m-0 p-0 flex flex-col gap-1">
                    {(x.notes || []).map((n: any, i: number) => (
                      <li key={i} className="text-sm">{typeof n === "string" ? n : <span>{n.text || n.note}<span className="dim text-xs"> {n.by ? `— ${n.by}` : ""}</span></span>}</li>
                    ))}
                  </ul>
                </>
              )}
            </>
          )}
        </Async>
      </Drawer>

      <Modal open={reassign} onClose={() => setReassign(false)} title="Reassign task"
        footer={<>
          <Btn kind="secondary" onClick={() => setReassign(false)}>Cancel</Btn>
          <Btn kind="primary" disabled={patch.pending || !assignee.trim()} onClick={doReassign}>{patch.pending ? "Reassigning…" : "Reassign"}</Btn>
        </>}>
        <p className="text-sm">The new assignee is notified and the task appears in their queue; the SLA clock does not reset.</p>
        <Field label="New assignee (user id)" hint="e.g. U-ADV-01 — their persona must be able to open the task's subject.">
          {(id) => <input id={id} className="input mono" value={assignee} onChange={(e) => setAssignee(e.target.value)} autoComplete="off" />}
        </Field>
        <Field label="Note (optional)">{(id) => <input id={id} className="input" value={note} onChange={(e) => setNote(e.target.value)} />}</Field>
      </Modal>

      <Modal open={cancel} onClose={() => setCancel(false)} title="Cancel task"
        footer={<>
          <Btn kind="secondary" onClick={() => setCancel(false)}>Keep task</Btn>
          <Btn kind="danger" disabled={patch.pending} onClick={doCancel}>{patch.pending ? "Cancelling…" : "Cancel task"}</Btn>
        </>}>
        <p className="text-sm">Cancelling removes <b>{t?.title}</b> from {t?.assigned_user_name || "the assignee"}&rsquo;s queue. The task is kept in history as cancelled; nothing else changes.</p>
        <Field label="Reason (optional, recorded on the task)">{(id) => <input id={id} className="input" value={note} onChange={(e) => setNote(e.target.value)} />}</Field>
      </Modal>
    </Page>
  );
}
