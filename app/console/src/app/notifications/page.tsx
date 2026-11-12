"use client";

// Notification Hub inbox (§5.18.4): every alert, handoff, nurture reply and task
// signal routed to the caller by the per-role rules. Severity badges, read /
// unread, mark all read, and open the subject through the resource-URL resolver
// (links are canonical, never screen paths). Renders in the caller's own app
// theme via the Shell. Mobile layout is mandatory (§5.19).

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { apiPost } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { resolve } from "@/lib/resourceUrl";
import { Async, Badge, Btn, Card, DateTime, EmptyState, FilterPills, Page, ViewHeader, useToast } from "@/components/app";

const SEV: Record<string, any> = { HIGH: "red", MEDIUM: "orange", LOW: "blue" };
const human = (e?: string) => (e || "notification").replace(/@v1$/, "").replace(/[._]/g, " ");

export default function NotificationsPage() {
  const toast = useToast();
  const router = useRouter();
  const q = useApi<any>("/notifications", { pollMs: 30000 });
  const [filter, setFilter] = useState("unread");
  const [type, setType] = useState("all");
  const [busy, setBusy] = useState(false);

  const items: any[] = q.data?.notifications || [];
  const types = useMemo(() => Array.from(new Set(items.map((n) => n.event).filter(Boolean))).sort(), [items]);
  const shown = items.filter((n) => (filter === "all" || !n.read) && (type === "all" || n.event === type));

  const markRead = async (n: any) => {
    if (n.read) return;
    try { await apiPost(`/notifications/${encodeURIComponent(n.notification_id)}/read`); q.reload(); }
    catch (e: any) { toast(e.message, "error"); }
  };
  const open = async (n: any) => {
    await markRead(n);
    if (!n.resource_url) return;
    const r = await resolve(n.resource_url);
    if (r.allowed && r.url) router.push(r.url); else toast(r.reason || "You can't open this item", "error");
  };
  const readAll = async () => {
    setBusy(true);
    try { const r = await apiPost<any>("/notifications/read-all"); toast(`${r.marked} marked read`, "success"); q.reload(); }
    catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
  };

  return (
    <Page>
      <ViewHeader title="Notifications" subtitle={q.data ? `${q.data.unread_count} unread of ${q.data.total}` : undefined}
        actions={<Btn kind="secondary" size="sm" onClick={readAll} disabled={busy || !q.data?.unread_count}>Mark all read</Btn>} />
      <Card>
        <FilterPills value={filter} onChange={setFilter} options={[{ id: "unread", label: "Unread" }, { id: "all", label: "All" }]} />
        {types.length > 1 && (
          <FilterPills value={type} onChange={setType} options={[{ id: "all", label: "All types" }, ...types.map((t) => ({ id: t, label: human(t) }))]} />
        )}
        <Async q={q}>
          {() => shown.length === 0 ? <EmptyState>{filter === "unread" ? "You're all caught up." : "No notifications."}</EmptyState> : (
            <ul className="list-none m-0 p-0 flex flex-col gap-2" aria-live="polite">
              {shown.map((n) => (
                <li key={n.notification_id} className={`alert-item ${n.severity === "HIGH" ? "severity-high" : n.severity === "LOW" ? "severity-low" : "severity-medium"}`}>
                  <div className="flex items-start gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="alert-title flex items-center gap-2 flex-wrap">
                        {!n.read && <span className="status-dot dot-blue" aria-label="unread" />}
                        {n.title && n.title !== n.event ? n.title : human(n.event)}
                        {n.severity && <Badge tone={SEV[n.severity] || "gray"}>{n.severity}</Badge>}
                      </div>
                      {n.body && <div className="alert-desc">{n.body}</div>}
                      <div className="alert-time"><DateTime value={n.created_at} />{n.journey_id ? <> · <span className="mono">{n.journey_id}</span></> : null}
                        {n.channels?.length ? <> · via {n.channels.join(", ")}</> : null}</div>
                    </div>
                    <div className="flex gap-1">
                      {n.resource_url && <Btn kind="alt" size="sm" onClick={() => open(n)}>Open</Btn>}
                      {!n.read && <Btn kind="ghost" size="sm" onClick={() => markRead(n)}>Mark read</Btn>}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Async>
      </Card>
    </Page>
  );
}
