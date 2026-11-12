"use client";

// Admin › Org hierarchy (§5.17, §8.1): OEM ▸ region ▸ district ▸ dealer group ▸
// dealer (with PMA). The tree is the scope-claim source for RBAC and search
// filters; AWS Quick dashboards are not row-scoped (spec §13, 2026-10-21).
// Tenant admins add and move nodes; dealer admins read their branch.

import { useState } from "react";
import { apiPatch, apiPost } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Async, Badge, Btn, Card, Field, Modal, Page, ViewHeader, useToast } from "@/components/app";

const CHILD: Record<string, string[]> = { oem: ["region"], region: ["district"], district: ["dealer_group", "dealer"], dealer_group: ["dealer"] };
const TONE: Record<string, any> = { oem: "red", region: "purple", district: "blue", dealer_group: "orange", dealer: "green" };

export default function OrgPage() {
  const toast = useToast();
  const q = useApi<any>("/admin/org");
  const [open, setOpen] = useState<Record<string, boolean>>({ OEM: true });
  const [add, setAdd] = useState<any | null>(null);
  const [move, setMove] = useState<any | null>(null);
  const [form, setForm] = useState<any>({});
  const [busy, setBusy] = useState(false);

  const flat: any[] = [];
  const walk = (n: any, parent: string | null) => { flat.push({ ...n, parent }); (n.children || []).forEach((c: any) => walk(c, n.node_id)); };
  (q.data?.roots || []).forEach((r: any) => walk(r, null));

  const submitAdd = async () => {
    setBusy(true);
    try {
      await apiPost("/admin/org", { ...form, parent_id: add.node_id });
      toast(`Created ${form.node_id}`, "success"); setAdd(null); q.reload();
    } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
  };
  const submitMove = async () => {
    setBusy(true);
    try {
      await apiPatch(`/admin/org/${encodeURIComponent(move.node_id)}`, { parent_id: form.parent_id, name: form.name, pma: form.pma });
      toast(`Updated ${move.node_id} — scopes recompute on next sign-in`, "success"); setMove(null); q.reload();
    } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
  };

  const Node = ({ n, depth }: { n: any; depth: number }) => {
    const kids = n.children || [];
    const isOpen = open[n.node_id] ?? depth < 2;
    return (
      <li>
        <div className="flex items-center gap-2 py-1.5 px-2 rounded hover:bg-black/5" style={{ paddingLeft: depth * 20 + 8 }}>
          <button className="w-5 text-center dim" aria-label={isOpen ? "Collapse" : "Expand"} aria-expanded={isOpen}
                  disabled={!kids.length} onClick={() => setOpen({ ...open, [n.node_id]: !isOpen })}>{kids.length ? (isOpen ? "▾" : "▸") : "·"}</button>
          <Badge tone={TONE[n.type]}>{n.type.replace("_", " ")}</Badge>
          <b className="text-sm">{n.name || n.node_id}</b>
          <span className="mono text-xs dim">{n.node_id}</span>
          {n.pma && <Badge tone="gray">{n.pma}</Badge>}
          {n.users > 0 && <span className="dim text-xs">{n.users} user{n.users === 1 ? "" : "s"}</span>}
          {q.data?.editable && (
            <span className="ml-auto flex gap-1">
              {CHILD[n.type] && <Btn kind="alt" size="sm" onClick={() => { setAdd(n); setForm({ type: CHILD[n.type][0] }); }}>＋ Child</Btn>}
              {n.type !== "oem" && <Btn kind="ghost" size="sm" onClick={() => { setMove(n); setForm({ parent_id: n.parent, name: n.name, pma: n.pma }); }}>Edit / move</Btn>}
            </span>
          )}
        </div>
        {isOpen && kids.length > 0 && <ul className="list-none m-0 p-0">{kids.map((c: any) => <Node key={c.node_id} n={c} depth={depth + 1} />)}</ul>}
      </li>
    );
  };

  return (
    <Page>
      <ViewHeader title="Org hierarchy" subtitle="The scope-claim source for role-based access and search filters. AWS Quick dashboards are not row-scoped: users with access see all rows." />
      <Async q={q}>
        {(d) => (
          <Card title={`${d.count} nodes`} actions={!d.editable ? <Badge tone="gray">read-only</Badge> : undefined}>
            <ul className="list-none m-0 p-0" role="tree">{d.roots.map((r: any) => <Node key={r.node_id} n={r} depth={0} />)}</ul>
          </Card>
        )}
      </Async>
      <Modal open={!!add} onClose={() => setAdd(null)} title={`Add under ${add?.name || ""}`}
        footer={<><Btn kind="secondary" onClick={() => setAdd(null)}>Cancel</Btn><Btn disabled={busy || !form.node_id} onClick={submitAdd}>Create</Btn></>}>
        <Field label="Type">{(id) => (
          <select id={id} className="select" value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}>
            {(CHILD[add?.type] || []).map((t) => <option key={t} value={t}>{t}</option>)}
          </select>)}</Field>
        <Field label="Node id" hint="e.g. D-112 · stable, used in scope claims">{(id) => <input id={id} className="input mono" value={form.node_id || ""} onChange={(e) => setForm({ ...form, node_id: e.target.value })} />}</Field>
        <Field label="Name">{(id) => <input id={id} className="input" value={form.name || ""} onChange={(e) => setForm({ ...form, name: e.target.value })} />}</Field>
        {form.type === "dealer" && <Field label="PMA">{(id) => <input id={id} className="input mono" value={form.pma || ""} onChange={(e) => setForm({ ...form, pma: e.target.value })} />}</Field>}
      </Modal>
      <Modal open={!!move} onClose={() => setMove(null)} title={`Edit ${move?.node_id || ""}`}
        footer={<><Btn kind="secondary" onClick={() => setMove(null)}>Cancel</Btn><Btn disabled={busy} onClick={submitMove}>Save</Btn></>}>
        <Field label="Name">{(id) => <input id={id} className="input" value={form.name || ""} onChange={(e) => setForm({ ...form, name: e.target.value })} />}</Field>
        <Field label="Parent" hint="Moving a node re-scopes every user and dealer below it.">{(id) => (
          <select id={id} className="select mono" value={form.parent_id || ""} onChange={(e) => setForm({ ...form, parent_id: e.target.value })}>
            {flat.filter((n) => move && n.node_id !== move.node_id && (CHILD[n.type] || []).includes(move.type))
              .map((n) => <option key={n.node_id} value={n.node_id}>{n.name} ({n.node_id})</option>)}
          </select>)}</Field>
        {move?.type === "dealer" && <Field label="PMA">{(id) => <input id={id} className="input mono" value={form.pma || ""} onChange={(e) => setForm({ ...form, pma: e.target.value })} />}</Field>}
      </Modal>
    </Page>
  );
}
