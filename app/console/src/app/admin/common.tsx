"use client";

// Admin Console building blocks: tenant-only guard, confirm dialog, change diff,
// file download, and the versioned-config editor used by Flags, Policies and
// Connectors (draft → diff → publish → rollback, §5.17/G-25). Lives in the
// admin route group — feature components stay out of src/components.

import { ReactNode, useState } from "react";
import { apiDelete, apiPost, apiPut } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";
import { Async, Badge, Btn, Card, DataTable, DateTime, ErrorState, Field, Modal, useToast } from "@/components/app";

export function useIsTenantAdmin(): boolean {
  const { persona } = usePersona();
  return persona?.role === "admin_tenant" || persona?.role === "platform_admin";
}

/** Renders children only for tenant admins; dealer admins get an explanation. */
export function TenantOnly({ children, what }: { children: ReactNode; what: string }) {
  const tenant = useIsTenantAdmin();
  if (!tenant) {
    return (
      <Card>
        <div className="muted text-sm">
          {what} is managed by the tenant administrator. Dealer admins have the store-scoped subset of the
          Admin Console (Overview, Users &amp; roles, Org hierarchy, Approvals).
        </div>
      </Card>
    );
  }
  return <>{children}</>;
}

export function ConfirmModal({ open, title, body, confirmLabel = "Confirm", danger, requireText, onConfirm, onClose, pending }: {
  open: boolean; title: ReactNode; body: ReactNode; confirmLabel?: string; danger?: boolean;
  requireText?: string; onConfirm: () => void; onClose: () => void; pending?: boolean }) {
  const [typed, setTyped] = useState("");
  const ok = !requireText || typed.trim() === requireText;
  return (
    <Modal open={open} onClose={() => { setTyped(""); onClose(); }} title={title}
           footer={<>
             <Btn kind="secondary" onClick={() => { setTyped(""); onClose(); }}>Cancel</Btn>
             <Btn kind={danger ? "danger" : "primary"} disabled={!ok || pending}
                  onClick={() => { onConfirm(); setTyped(""); }}>{pending ? "Working…" : confirmLabel}</Btn>
           </>}>
      <div className="text-sm">{body}</div>
      {requireText && (
        <Field label={<>Type <span className="mono">{requireText}</span> to confirm</>}>
          {(id) => <input id={id} className="input mono" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />}
        </Field>
      )}
    </Modal>
  );
}

export interface Change { path: string; change: "added" | "removed" | "changed"; from?: unknown; to?: unknown }

const fmt = (v: unknown) => (v === undefined ? "" : typeof v === "string" ? v : JSON.stringify(v));

export function DiffList({ changes, empty = "No changes." }: { changes: Change[] | null | undefined; empty?: string }) {
  if (!changes || changes.length === 0) return <div className="dim text-sm">{empty}</div>;
  return (
    <table className="data-table" aria-label="Changes">
      <thead><tr><th>Setting</th><th>Change</th><th>Before</th><th>After</th></tr></thead>
      <tbody>
        {changes.map((c) => (
          <tr key={c.path}>
            <td className="mono text-xs">{c.path}</td>
            <td><Badge tone={c.change === "added" ? "green" : c.change === "removed" ? "red" : "orange"}>{c.change}</Badge></td>
            <td className="mono text-xs" style={{ color: "var(--danger)" }}>{fmt(c.from)}</td>
            <td className="mono text-xs" style={{ color: "var(--success)" }}>{fmt(c.to)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function downloadFile(filename: string, text: string, mime = "text/csv") {
  const blob = new Blob([text], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function MarketPicker({ value, onChange }: { value: string; onChange: (m: string) => void }) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <span className="muted">Market</span>
      <select className="select" style={{ width: "auto" }} value={value} onChange={(e) => onChange(e.target.value)} aria-label="Market">
        <option value="us">us</option>
        <option value="de">de</option>
      </select>
    </label>
  );
}

/**
 * Versioned config editor shell. `renderEditor(content, setContent)` renders the
 * kind-specific form over a working copy; the shell handles draft save (server
 * validation + diff), publish, discard, history, and rollback with confirmation.
 */
export function VersionedConfig<T extends Record<string, any>>({ kind, market, title, renderEditor, extra }: {
  kind: string; market: string; title: ReactNode;
  renderEditor: (content: T, set: (next: T) => void) => ReactNode; extra?: (active: T) => ReactNode }) {
  const toast = useToast();
  const q = useApi<any>(`/config/${kind}?market_id=${market}`);
  const [work, setWork] = useState<T | null>(null);
  const [diff, setDiff] = useState<Change[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<null | { kind: "publish" } | { kind: "rollback"; version: number }>(null);
  const [note, setNote] = useState("");

  const base: T | null = q.data ? (q.data.draft?.content ?? q.data.content) : null;
  const content = (work ?? base) as T | null;
  const dirty = work !== null;

  const run = async (f: () => Promise<any>, ok: string) => {
    setBusy(true); setErr(null);
    try { const r = await f(); toast(ok, "success"); return r; }
    catch (e: any) { setErr(e.status === 422 ? `Hard rule: ${e.message}` : e.message); toast(e.message, "error"); return null; }
    finally { setBusy(false); }
  };
  const saveDraft = async () => {
    const r = await run(() => apiPut(`/config/${kind}`, { market_id: market, content: work }), "Draft saved — not live until published");
    if (r) { setDiff(r.diff); setWork(null); q.reload(); }
  };
  const publish = async () => {
    const r = await run(() => apiPost(`/config/${kind}/publish`, { market_id: market, note: note || undefined, ...(dirty ? { content: work } : {}) }),
                        "Published — agents use it on their next invocation");
    if (r) { setWork(null); setDiff(null); setNote(""); setConfirm(null); q.reload(); }
  };
  const discard = async () => {
    if (dirty) { setWork(null); return; }
    const r = await run(() => apiDelete(`/config/${kind}?market_id=${market}`), "Draft discarded");
    if (r) q.reload();
  };
  const rollback = async (version: number) => {
    const r = await run(() => apiPost(`/config/${kind}/rollback`, { market_id: market, to_version: version }), `Rolled back to v${version}`);
    if (r) { setConfirm(null); setWork(null); q.reload(); }
  };

  return (
    <Async q={q}>
      {(d) => (
        <div className="grid gap-4" style={{ gridTemplateColumns: "minmax(0,1fr) 320px" }}>
          <div className="min-w-0">
            <Card title={<span className="flex items-center gap-2">{title}
                <Badge tone="blue">v{d.active_version || "default"}</Badge>
                {d.draft && !dirty && <Badge tone="orange">Draft pending</Badge>}
                {dirty && <Badge tone="orange">Unsaved edits</Badge>}</span>}
                  actions={<>
                    <Btn kind="secondary" size="sm" disabled={busy || (!dirty && !d.draft)} onClick={discard}>Discard</Btn>
                    <Btn kind="secondary" size="sm" disabled={busy || !dirty} onClick={saveDraft}>Save draft</Btn>
                    <Btn kind="primary" size="sm" disabled={busy || (!dirty && !d.draft)} onClick={() => setConfirm({ kind: "publish" })}>Publish…</Btn>
                  </>}>
              {err && <div className="mb-3"><ErrorState error={err} /></div>}
              {content && renderEditor(content, (next) => setWork(next))}
            </Card>
            {(diff || d.draft?.diff) && !dirty && (
              <Card title="Draft vs live"><DiffList changes={diff || d.draft?.diff} /></Card>
            )}
            {extra && extra(d.content as T)}
          </div>
          <Card title="Version history">
            <DataTable rows={d.history || []} rowKey={(h: any) => String(h.version)} dense
              empty="Never published — defaults in effect."
              columns={[
                { key: "v", header: "v", render: (h: any) => <span className="mono">{h.version}{h.is_active ? " ●" : ""}</span> },
                { key: "when", header: "Published", render: (h: any) => <span className="text-xs"><DateTime value={h.effective_from} /><div className="dim">{h.published_by || ""}{h.note ? ` · ${h.note}` : ""}</div></span> },
                { key: "act", header: "", render: (h: any) => h.is_active ? <Badge tone="green">live</Badge>
                    : <Btn kind="alt" size="sm" disabled={busy} onClick={() => setConfirm({ kind: "rollback", version: h.version })}>Roll back</Btn> },
              ]} />
          </Card>
          <ConfirmModal open={confirm?.kind === "publish"} title={`Publish ${kind}`} pending={busy}
            confirmLabel="Publish" onClose={() => setConfirm(null)} onConfirm={publish}
            body={<div className="flex flex-col gap-3">
              <p>This becomes the live configuration for market <b>{market}</b>. Agents read it on their next invocation; the previous version stays in history for rollback.</p>
              <Field label="Change note (optional)">{(id) => <input id={id} className="input" value={note} onChange={(e) => setNote(e.target.value)} />}</Field>
            </div>} />
          <ConfirmModal open={confirm?.kind === "rollback"} title="Roll back" pending={busy} confirmLabel="Roll back"
            onClose={() => setConfirm(null)} onConfirm={() => confirm?.kind === "rollback" && rollback(confirm.version)}
            body={<p>Re-publish version {confirm?.kind === "rollback" ? confirm.version : ""} as a new live version. Nothing in history is deleted.</p>} />
        </div>
      )}
    </Async>
  );
}
