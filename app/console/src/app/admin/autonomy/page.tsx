"use client";

// Admin › Autonomy (§4.10.1, acceptance #33): the level each agent action runs
// at — observe / recommend / act_with_approval / act_and_notify — versioned per
// action with history and rollback. Hard rules (no automatic spend or
// allocation, plan publishing, customer-owned estimate decisions) carry a
// ceiling: levels above it are not offered, and the server rejects them (422).

import { useState } from "react";
import { apiPost } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Async, Badge, Btn, Card, DataTable, DateTime, Drawer, ErrorState, FilterPills, Page, ViewHeader, useToast } from "@/components/app";
import { ConfirmModal, MarketPicker, TenantOnly } from "../common";

const LEVELS = ["off", "recommend", "act_with_approval", "act_and_notify", "autonomous"];
const TONE: Record<string, any> = { off: "gray", recommend: "blue", act_with_approval: "orange", act_and_notify: "green", autonomous: "purple" };

export default function AutonomyPage() {
  const toast = useToast();
  const [market, setMarket] = useState("us");
  const [agent, setAgent] = useState("all");
  const q = useApi<any>(`/admin/autonomy?market=${market}`);
  const [hist, setHist] = useState<string | null>(null);
  const h = useApi<any>(hist ? `/admin/autonomy/history?action_id=${encodeURIComponent(hist)}&market_id=${market}` : null);
  const [pending, setPending] = useState<{ action_id: string; level: string; from: string } | null>(null);
  const [rb, setRb] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const apply = async () => {
    if (!pending) return;
    setBusy(true); setErr(null);
    try {
      const r = await apiPost<any>("/admin/autonomy", { action_id: pending.action_id, level: pending.level, market });
      toast(`${pending.action_id} → ${pending.level} (v${r.new_version}); effective on next invocation`, "success");
      setPending(null); q.reload(); if (hist) h.reload();
    } catch (e: any) {
      const msg = e.status === 422 ? `Hard rule: ${e.message}` : e.message;
      setErr(msg); toast(msg, "error"); setPending(null);
    } finally { setBusy(false); }
  };
  const rollback = async () => {
    if (!hist || rb == null) return;
    setBusy(true);
    try {
      await apiPost("/admin/autonomy/rollback", { action_id: hist, to_version: rb, market });
      toast(`${hist} rolled back to v${rb}`, "success"); setRb(null); q.reload(); h.reload();
    } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
  };

  return (
    <Page>
      <ViewHeader title="Agent autonomy" subtitle="How much each agent action may do on its own. Hard rules sit above configuration."
        actions={<MarketPicker value={market} onChange={setMarket} />} />
      <TenantOnly what="Agent autonomy">
        {err && <div className="mb-3"><ErrorState error={err} /></div>}
        <Async q={q}>
          {(d) => {
            const agents = Array.from(new Set((d.actions || []).map((a: any) => a.action_id.split(".")[0].toUpperCase()))).sort() as string[];
            const rows = (d.actions || []).filter((a: any) => agent === "all" || a.action_id.toUpperCase().startsWith(agent + "."));
            return (
              <Card>
                <FilterPills value={agent} onChange={setAgent} options={[{ id: "all", label: "All agents" }, ...agents.map((a) => ({ id: a, label: a }))]} />
                <DataTable rows={rows} rowKey={(a: any) => a.action_id} filterText={(a: any) => a.action_id}
                  initialSort={{ key: "action", dir: "asc" }}
                  columns={[
                    { key: "action", header: "Action", sort: (a: any) => a.action_id, render: (a: any) => <span className="mono text-sm">{a.action_id}</span> },
                    { key: "level", header: "Level", sort: (a: any) => LEVELS.indexOf(a.level), render: (a: any) => {
                      const cap = LEVELS.indexOf(a.ceiling || "autonomous");
                      return (
                        <select className="select" style={{ width: "auto" }} value={a.level} aria-label={`${a.action_id} level`}
                                onChange={(e) => setPending({ action_id: a.action_id, level: e.target.value, from: a.level })}>
                          {LEVELS.map((l, i) => <option key={l} value={l} disabled={i > cap}>{l}{i > cap ? " (hard rule)" : ""}</option>)}
                        </select>);
                    } },
                    { key: "badge", header: "", render: (a: any) => <Badge tone={TONE[a.level]}>{a.level}</Badge> },
                    { key: "ceiling", header: "Ceiling", render: (a: any) => a.hard_rule ? <Badge tone="red">≤ {a.ceiling}</Badge> : <span className="dim text-xs">—</span> },
                    { key: "v", header: "Version", num: true, sort: (a: any) => a.version, render: (a: any) => <span className="mono">v{a.version} <span className="dim">/ {a.versions}</span></span> },
                    { key: "from", header: "Effective", sort: (a: any) => a.effective_from, render: (a: any) => <DateTime value={a.effective_from} /> },
                    { key: "h", header: "", render: (a: any) => <Btn kind="alt" size="sm" onClick={() => setHist(a.action_id)}>History</Btn> },
                  ]} />
              </Card>
            );
          }}
        </Async>
      </TenantOnly>
      <ConfirmModal open={!!pending} title="Change autonomy level" confirmLabel="Publish level" pending={busy}
        onClose={() => setPending(null)} onConfirm={apply}
        body={pending && <p><span className="mono">{pending.action_id}</span>: <Badge tone={TONE[pending.from]}>{pending.from}</Badge> → <Badge tone={TONE[pending.level]}>{pending.level}</Badge>.
          A new version becomes active; the previous one stays in history for rollback.</p>} />
      <Drawer open={!!hist} onClose={() => setHist(null)} title={`History · ${hist || ""}`}>
        <Async q={h}>
          {(d) => (
            <>
              {d.hard_rule && <div className="text-sm"><Badge tone="red">hard rule</Badge> ceiling {d.ceiling}</div>}
              <DataTable rows={d.versions} rowKey={(v: any) => String(v.version)} dense
                columns={[
                  { key: "v", header: "v", render: (v: any) => <span className="mono">{v.version}</span> },
                  { key: "l", header: "Level", render: (v: any) => <Badge tone={TONE[v.level]}>{v.level}</Badge> },
                  { key: "f", header: "Effective", render: (v: any) => <span className="text-xs"><DateTime value={v.effective_from} /><div className="dim">{v.source_ref}</div></span> },
                  { key: "a", header: "", render: (v: any) => v.is_active ? <Badge tone="green">live</Badge> : <Btn kind="alt" size="sm" onClick={() => setRb(v.version)}>Roll back</Btn> },
                ]} />
            </>
          )}
        </Async>
      </Drawer>
      <ConfirmModal open={rb != null} title="Roll back" confirmLabel="Roll back" pending={busy} onClose={() => setRb(null)} onConfirm={rollback}
        body={<p>Re-publish v{rb} of <span className="mono">{hist}</span> as a new active version.</p>} />
    </Page>
  );
}
