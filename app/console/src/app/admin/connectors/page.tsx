"use client";

// Admin › Connectors (§4.1, §5.17): every external system behind the Connector
// interface — sandbox or live, the Secrets Manager secret NAME for its
// credentials (never the value), health and last sync. "Test" runs the health
// check; a failure emits connector.sync_failed for the Notification Hub.
// "Rotate" asks the service to rotate the secret in Secrets Manager (§5.17
// credential rotation); where that route is not deployed it degrades to the
// documented manual path and says so.

import { useState } from "react";
import { apiPost } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Async, Badge, Btn, Card, DataTable, DateTime, KpiGrid, Page, ViewHeader, useToast } from "@/components/app";
import { ConfirmModal, MarketPicker, TenantOnly, VersionedConfig } from "../common";

const TONE: Record<string, any> = { ok: "green", degraded: "orange", failed: "red", not_configured: "gray", unknown: "gray" };

export default function ConnectorsPage() {
  const toast = useToast();
  const [market, setMarket] = useState("us");
  const q = useApi<any>(`/admin/connectors?market_id=${market}`);
  const [testing, setTesting] = useState<string | null>(null);
  const [rotate, setRotate] = useState<any | null>(null);
  const [rotating, setRotating] = useState(false);
  const [rotateNote, setRotateNote] = useState<string | null>(null);

  const test = async (id: string) => {
    setTesting(id);
    try {
      const r = await apiPost<any>(`/admin/connectors/${id}/test`, { market_id: market });
      toast(`${id}: ${r.status} — ${r.detail}`, r.status === "ok" ? "success" : r.status === "failed" ? "error" : "info");
      q.reload();
    } catch (e: any) { toast(e.message, "error"); } finally { setTesting(null); }
  };
  const testAll = async () => { for (const c of q.data?.connectors || []) await test(c.connector_id); };

  const doRotate = async () => {
    if (!rotate) return;
    setRotating(true);
    try {
      // Designed route (§5.17): the service drives Secrets Manager RotateSecret
      // on the connector's secret and re-runs the health check.
      const r = await apiPost<any>(`/admin/connectors/${rotate.connector_id}/rotate`, { market_id: market });
      toast(`${rotate.connector_id}: rotation ${r.status || "started"}${r.detail ? ` — ${r.detail}` : ""}`,
            r.status === "failed" ? "error" : "success");
      setRotateNote(null);
      q.reload();
    } catch (e: any) {
      // Graceful degrade: the rotation route is not deployed in this environment.
      const notDeployed = e?.status === 404 || e?.status === 405 || e?.status === 501;
      setRotateNote(notDeployed
        ? `Rotation service route is not available here (${e.status}). Rotate the secret ` +
          `"${rotate.credential_ref || "(unset)"}" in AWS Secrets Manager directly — the secret NAME in the ` +
          `registry stays stable and connectors read the value at call time, so no registry change is needed. ` +
          `Run "Test" afterwards to confirm the new credentials.`
        : `${rotate.connector_id}: ${e.message}`);
      toast(notDeployed ? "Rotation route unavailable — manual Secrets Manager path shown below" : e.message,
            notDeployed ? "info" : "error");
    } finally { setRotating(false); setRotate(null); }
  };

  return (
    <Page>
      <ViewHeader title="Connectors" subtitle="Sandbox and live integrations behind one Connector interface."
        actions={<><MarketPicker value={market} onChange={setMarket} /><Btn kind="secondary" size="sm" onClick={testAll} disabled={!!testing}>Test all</Btn></>} />
      <TenantOnly what="Connector configuration">
        <Async q={q}>
          {(d) => (
            <>
              <KpiGrid five items={["ok", "degraded", "failed", "not_configured", "unknown"].map((s) => ({
                label: s.replace("_", " "), value: d.summary[s] || 0, trend: s === "failed" && d.summary[s] ? "down" as const : undefined }))} />
              {rotateNote && (
                <Card accent="warning">
                  <div className="text-sm flex items-start justify-between gap-3">
                    <span>{rotateNote}</span>
                    <Btn kind="ghost" size="sm" onClick={() => setRotateNote(null)} aria-label="Dismiss rotation note">✕</Btn>
                  </div>
                </Card>
              )}
              <Card title="Health">
                <DataTable rows={d.connectors} rowKey={(c: any) => c.connector_id}
                  filterText={(c: any) => `${c.connector_id} ${c.system} ${c.vendor}`}
                  columns={[
                    { key: "sys", header: "System", sort: (c: any) => c.system, render: (c: any) => <div><b>{c.system}</b><div className="dim text-xs">{c.vendor}</div></div> },
                    { key: "mode", header: "Mode", sort: (c: any) => c.mode, render: (c: any) => <Badge tone={c.mode === "live" ? "purple" : "blue"}>{c.mode}</Badge> },
                    { key: "cred", header: "Credential reference", render: (c: any) => <span className="mono text-xs">{c.credential_ref || "—"}</span> },
                    { key: "health", header: "Health", sort: (c: any) => c.health, render: (c: any) => <span title={c.detail || ""}><Badge tone={TONE[c.health]}>{c.health.replace("_", " ")}</Badge></span> },
                    { key: "checked", header: "Last checked", sort: (c: any) => c.last_checked_at, render: (c: any) => <DateTime value={c.last_checked_at} /> },
                    { key: "sync", header: "Last sync", render: (c: any) => <DateTime value={c.last_sync_at} /> },
                    { key: "act", header: "", render: (c: any) => (
                      <div className="flex gap-1">
                        <Btn kind="alt" size="sm" disabled={testing === c.connector_id} onClick={() => test(c.connector_id)}>{testing === c.connector_id ? "Testing…" : "Test"}</Btn>
                        <Btn kind="ghost" size="sm" disabled={rotating || !c.credential_ref} title={c.credential_ref ? undefined : "No credential reference configured"}
                             onClick={() => setRotate(c)}>Rotate…</Btn>
                      </div>) },
                  ]} />
              </Card>
            </>
          )}
        </Async>
        <VersionedConfig<{ connectors: any[] }> kind="connectors" market={market} title="Registry (versioned)"
          renderEditor={(c, set) => (
            <table className="data-table">
              <thead><tr><th>Connector</th><th>Enabled</th><th>Mode</th><th>Secrets Manager secret name</th></tr></thead>
              <tbody>{c.connectors.map((x, i) => {
                const upd = (patch: any) => set({ connectors: c.connectors.map((y, j) => (j === i ? { ...y, ...patch } : y)) });
                return (
                  <tr key={x.connector_id}>
                    <td><b>{x.system}</b> <span className="mono dim text-xs">{x.connector_id}</span></td>
                    <td><input type="checkbox" checked={!!x.enabled} onChange={(e) => upd({ enabled: e.target.checked })} aria-label={`${x.connector_id} enabled`} /></td>
                    <td><select className="select" value={x.mode} onChange={(e) => upd({ mode: e.target.value })} aria-label={`${x.connector_id} mode`}>
                      <option value="sandbox">sandbox</option><option value="live">live</option></select></td>
                    <td><input className="input mono" value={x.credential_ref || ""} onChange={(e) => upd({ credential_ref: e.target.value })} aria-label={`${x.connector_id} credential reference`} /></td>
                  </tr>
                );
              })}</tbody>
            </table>
          )} />
        <ConfirmModal open={!!rotate} title={`Rotate credentials · ${rotate?.connector_id || ""}`} pending={rotating}
          confirmLabel="Rotate" onClose={() => setRotate(null)} onConfirm={doRotate}
          body={<div className="flex flex-col gap-2">
            <p>Rotates the secret <span className="mono">{rotate?.credential_ref}</span> in AWS Secrets Manager and re-runs the
              health check. The secret name stays stable, so the registry does not change; connectors read the new value on
              their next call.</p>
            {rotate?.mode === "live" && <p className="muted">This connector is <b>live</b> — a rotation that the external
              system does not accept will fail its next sync and raise <span className="mono">connector.sync_failed</span>.</p>}
          </div>} />
      </TenantOnly>
    </Page>
  );
}
