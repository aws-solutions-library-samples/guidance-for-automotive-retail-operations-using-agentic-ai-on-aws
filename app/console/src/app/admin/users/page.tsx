"use client";

// Admin › Users & roles (§5.17, §8.1): PLAT_Users with their §5.2 group, scope
// node, Quick provisioning, and status. Edits are authorized server-side —
// dealer admins only see and edit their own stores' users and cannot grant OEM
// or tenant-admin groups. Cognito group sync is a deploy step.

import { useMemo, useState } from "react";
import { apiPatch } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";
import { Async, Badge, Btn, Card, DataTable, Drawer, Field, FilterPills, Page, ViewHeader, useToast } from "@/components/app";
import { ConfirmModal } from "../common";

export default function UsersPage() {
  const toast = useToast();
  const { persona } = usePersona();
  const q = useApi<any>("/admin/users");
  const org = useApi<any>("/admin/org");
  const [group, setGroup] = useState<string>("all");
  const [edit, setEdit] = useState<any | null>(null);
  const [form, setForm] = useState<{ group: string; scope: string }>({ group: "", scope: "" });
  const [busy, setBusy] = useState(false);
  const [deact, setDeact] = useState<any | null>(null);
  const tenant = persona?.role === "admin_tenant" || persona?.role === "platform_admin";

  const nodes = useMemo(() => {
    const out: { id: string; label: string }[] = [];
    const walk = (n: any, depth: number) => {
      out.push({ id: n.node_id, label: `${"  ".repeat(depth)}${n.name || n.node_id} (${n.type})` });
      (n.children || []).forEach((c: any) => walk(c, depth + 1));
    };
    (org.data?.roots || []).forEach((r: any) => walk(r, 0));
    return out;
  }, [org.data]);

  const open = (u: any) => { setEdit(u); setForm({ group: (u.groups || [])[0] || "", scope: u.scope_node_id || "" }); };
  const save = async () => {
    if (!edit) return;
    setBusy(true);
    try {
      const changes: any = {};
      if (form.group && form.group !== (edit.groups || [])[0]) changes.groups = [form.group];
      if (form.scope && form.scope !== edit.scope_node_id) changes.scope_node_id = form.scope;
      if (Object.keys(changes).length === 0) { setEdit(null); return; }
      await apiPatch(`/admin/users/${encodeURIComponent(edit.user_id)}`, changes);
      toast(`Updated ${edit.user_id} — Cognito group sync runs with the next identity deploy`, "success");
      setEdit(null);
      q.reload();
    } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
  };
  const setActive = async (u: any, active: boolean) => {
    setBusy(true);
    try {
      await apiPatch(`/admin/users/${encodeURIComponent(u.user_id)}`, { active });
      toast(`${u.user_id} ${active ? "reactivated" : "deactivated"}`, "success");
      setDeact(null);
      q.reload();
    } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
  };

  return (
    <Page>
      <ViewHeader title="Users & roles" subtitle={tenant ? "Every staff user, their §5.2 group and org scope." : "Users in your stores."} />
      <Async q={q}>
        {(d) => {
          const groups = Object.keys(d.by_group || {}).sort();
          const rows = (d.users || []).filter((u: any) => group === "all" || (u.groups || []).includes(group));
          return (
            <Card>
              <FilterPills value={group} onChange={setGroup}
                options={[{ id: "all", label: `All (${d.count})` }, ...groups.map((g) => ({ id: g, label: `${g} (${d.by_group[g]})` }))]} />
              <DataTable rows={rows} rowKey={(u: any) => u.user_id}
                filterText={(u: any) => `${u.user_id} ${u.given_name} ${u.family_name} ${u.email} ${u.username}`}
                filterPlaceholder="Search name, email, id…" initialSort={{ key: "id", dir: "asc" }}
                columns={[
                  { key: "id", header: "User", sort: (u: any) => u.user_id,
                    render: (u: any) => <div><b>{[u.given_name, u.family_name].filter(Boolean).join(" ") || u.user_id}</b>
                      <div className="dim text-xs mono">{u.user_id} · {u.email}</div></div> },
                  { key: "groups", header: "Group", sort: (u: any) => (u.groups || [])[0],
                    render: (u: any) => (u.groups || []).map((g: string) => <Badge key={g} tone={g.startsWith("oem") ? "purple" : g.startsWith("admin") ? "red" : "blue"}>{g}</Badge>) },
                  { key: "scope", header: "Scope", sort: (u: any) => u.scope_node_id, render: (u: any) => <span className="mono text-xs">{u.scope_node_id}{u.district_id ? ` · ${u.district_id}` : ""}</span> },
                  { key: "fed", header: "Sign-in", render: (u: any) => <Badge tone={u.federated ? "purple" : "gray"}>{u.federated ? "Federate" : "Cognito"}</Badge> },
                  { key: "quick", header: "Quick", render: (u: any) => <Badge tone={u.quick_provisioned ? "green" : "gray"}>{u.quick_provisioned ? "provisioned" : "—"}</Badge> },
                  { key: "active", header: "Status", sort: (u: any) => (u.active === false ? 0 : 1),
                    render: (u: any) => <Badge tone={u.active === false ? "red" : "green"}>{u.active === false ? "inactive" : "active"}</Badge> },
                  { key: "act", header: "", render: (u: any) => (
                    <div className="flex gap-1">
                      <Btn kind="alt" size="sm" onClick={() => open(u)}>Edit</Btn>
                      {u.active === false
                        ? <Btn kind="alt" size="sm" disabled={busy} onClick={() => setActive(u, true)}>Reactivate</Btn>
                        : <Btn kind="ghost" size="sm" disabled={busy || u.user_id === persona?.userId} onClick={() => setDeact(u)}>Deactivate</Btn>}
                    </div>) },
                ]} />
            </Card>
          );
        }}
      </Async>
      <Drawer open={!!edit} onClose={() => setEdit(null)} title={`Edit ${edit?.user_id || ""}`}
        footer={<><Btn kind="secondary" onClick={() => setEdit(null)}>Cancel</Btn><Btn onClick={save} disabled={busy}>{busy ? "Saving…" : "Save"}</Btn></>}>
        {edit && (
          <>
            <div className="text-sm"><b>{[edit.given_name, edit.family_name].filter(Boolean).join(" ")}</b> · {edit.email}</div>
            <Field label="Group (persona)" hint={tenant ? "Determines home screen and permitted screens (§5.2)." : "Dealer admins cannot grant OEM or tenant-admin groups."}>
              {(id) => (
                <select id={id} className="select" value={form.group} onChange={(e) => setForm({ ...form, group: e.target.value })}>
                  {(q.data?.groups || []).filter((g: string) => tenant || !(g.startsWith("oem_") || g === "admin_tenant"))
                    .map((g: string) => <option key={g} value={g}>{g}</option>)}
                </select>)}
            </Field>
            <Field label="Scope node" hint="The org subtree this user acts within (dealer, group, district, region, OEM).">
              {(id) => (
                <select id={id} className="select mono" value={form.scope} onChange={(e) => setForm({ ...form, scope: e.target.value })}>
                  {nodes.map((n) => <option key={n.id} value={n.id}>{n.label}</option>)}
                </select>)}
            </Field>
          </>
        )}
      </Drawer>
      <ConfirmModal open={!!deact} danger title="Deactivate user" confirmLabel="Deactivate" pending={busy}
        onClose={() => setDeact(null)} onConfirm={() => deact && setActive(deact, false)}
        body={<p><b>{deact?.user_id}</b> loses access on their next request. Their history, tasks and handoffs are kept.</p>} />
    </Page>
  );
}
