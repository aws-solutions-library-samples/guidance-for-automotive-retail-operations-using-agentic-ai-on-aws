"use client";

// Marketing Studio · Audiences (§3.5 get_personalize_segments / refresh_audiences,
// §5.16): segments with size and marketable (consented) reach, per-dealer
// breakdown, and export to an ad platform as hashed ids only (§8.2 — no contact
// fields leave Retail Ops). "Refresh segments" asks A5 to rebuild from journey
// outcomes; where that route is not deployed it degrades to the nightly run and
// says so.

import { useState } from "react";
import { apiPost } from "@/lib/api";
import {
  AiInsight, Async, Badge, Btn, Card, DataTable, Kpi, KpiGrid, Modal, ProgressBar, useToast,
} from "@/components/app";
import { useApi, useMutation } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";
import { WorkspaceView } from "../WorkspaceView";

const PLATFORM_LABEL: Record<string, string> = { google_ads: "Google Ads", meta: "Meta", adobe: "Adobe" };

export default function Audiences() {
  const toast = useToast();
  const { persona } = usePersona();
  const canExport = persona?.role === "oem_marketing";
  const q = useApi<any>("/marketing/audiences");
  const exp = useMutation();
  const [target, setTarget] = useState<any>(null);
  const [platform, setPlatform] = useState("google_ads");
  const [last, setLast] = useState<any>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshNote, setRefreshNote] = useState<string | null>(null);

  const doExport = async () => {
    const out: any = await exp.run(`/marketing/audiences/${encodeURIComponent(target.segment_id)}/export`, { platform });
    if (out) { setLast(out); toast(`Exported ${out.count} hashed ids to ${PLATFORM_LABEL[platform]}`, "success"); setTarget(null); }
    else toast(exp.error || "Export failed", "error");
  };

  // §3.5 refresh_audiences(): rebuild Personalize segments from journey outcomes.
  const doRefresh = async () => {
    setRefreshing(true);
    try {
      const r = await apiPost<any>("/marketing/audiences/refresh", {});
      toast(r?.note || "Segments refreshed from journey outcomes", "success");
      setRefreshNote(null);
      await q.reload();
    } catch (e: any) {
      const notDeployed = e?.status === 404 || e?.status === 405 || e?.status === 501;
      setRefreshNote(notDeployed
        ? "On-demand refresh is not available in this environment — the Campaign Agent's nightly run rebuilds the segments from journey outcomes and pushes ad-platform audience drafts for you to activate. The list below is the latest build."
        : e.message);
      toast(notDeployed ? "Refresh runs nightly here — see the note" : e.message, notDeployed ? "info" : "error");
      await q.reload();
    } finally { setRefreshing(false); }
  };

  return (
    <WorkspaceView view="audiences" askPath="/marketing/ask" chatTitle="Ask your campaign data"
      title="Audiences" subtitle="Segments for paid media and nurture targeting"
      actions={canExport ? <Btn kind="secondary" size="sm" disabled={refreshing} onClick={doRefresh}>{refreshing ? "Refreshing…" : "Refresh segments"}</Btn> : undefined}
      prompts={["Which campaign has the best ROI?", "Where is the biggest funnel drop by source?"]}>
      <Async q={q} rows={6}>
        {(d) => {
          const segs = d.segments as any[];
          const kpis: Kpi[] = [
            { label: "Customers in scope", value: d.population },
            { label: "Segments", value: segs.length },
            { label: "Largest segment", value: Math.max(0, ...segs.map((s) => s.size)) },
            { label: "Marketable reach", value: d.marketable_reach ?? segs.reduce((s, x) => s + x.marketable, 0),
              delta: d.in_any_segment != null ? `${d.in_any_segment} people in a segment, consented` : "consented, distinct people" },
          ];
          const top = [...segs].sort((a, b) => b.marketable - a.marketable)[0];
          return (
            <>
              {refreshNote && (
                <Card accent="warning">
                  <div className="text-sm flex items-start justify-between gap-3">
                    <span>{refreshNote}</span>
                    <Btn kind="ghost" size="sm" onClick={() => setRefreshNote(null)} aria-label="Dismiss refresh note">✕</Btn>
                  </div>
                </Card>
              )}
              <KpiGrid items={kpis} />
              <AiInsight title="Audience opportunities" sources={[segs[0]?.source_ref, d.engine]}>
                <p>{top ? `${top.name} is the largest reachable audience (${top.marketable} of ${top.size} consented). ` : ""}
                  Exports send SHA-256 hashed ids only and exclude customers who opted out.</p>
              </AiInsight>
              <Card title={<span>Segments <Badge tone="gray">{d.engine}</Badge></span>}>
                <DataTable rows={segs} rowKey={(s: any) => s.segment_id} initialSort={{ key: "size", dir: "desc" }}
                  columns={[
                    { key: "name", header: "Segment", sort: (s: any) => s.name, render: (s: any) => <span><b>{s.name}</b><div className="dim text-xs mono">{s.definition}</div></span> },
                    { key: "size", header: "Size", num: true, sort: (s: any) => s.size, render: (s: any) => s.size },
                    { key: "reach", header: "Marketable", sort: (s: any) => s.marketable,
                      render: (s: any) => <div className="flex items-center gap-2"><ProgressBar value={s.marketable} max={Math.max(1, s.size)} tone="blue" /><span className="text-xs">{s.marketable}</span></div> },
                    { key: "dealers", header: "Top dealers", render: (s: any) => <span className="text-xs">{Object.entries(s.by_dealer || {}).sort((a: any, b: any) => b[1] - a[1]).slice(0, 3).map(([k, v]) => `${k} (${v})`).join(", ") || "—"}</span> },
                    { key: "x", header: "", render: (s: any) => canExport ? <Btn kind="secondary" size="sm" disabled={!s.marketable} onClick={() => setTarget(s)}>Export</Btn> : null },
                  ]} />
                <div className="dim text-xs mt-2">Refreshed {d.refreshed_at}</div>
              </Card>
              {last && (
                <Card title="Last export" accent="success">
                  <div className="info-row"><span className="info-label">Export</span><span className="info-value mono">{last.export_id}</span></div>
                  <div className="info-row"><span className="info-label">Segment → platform</span><span className="info-value">{last.name} → {PLATFORM_LABEL[last.platform]}</span></div>
                  <div className="info-row"><span className="info-label">Members</span><span className="info-value">{last.count} ({last.id_type})</span></div>
                  <div className="info-row"><span className="info-label">Excluded</span><span className="info-value">{last.excluded}</span></div>
                  <div className="dim text-xs mt-2">{last.status}</div>
                </Card>
              )}
            </>
          );
        }}
      </Async>
      <Modal open={!!target} onClose={() => setTarget(null)} title={`Export “${target?.name}”`}
        footer={<><Btn kind="secondary" onClick={() => setTarget(null)}>Cancel</Btn><Btn kind="primary" onClick={doExport} disabled={exp.pending}>{exp.pending ? "Exporting…" : "Export"}</Btn></>}>
        <p className="text-sm">{target?.marketable} consented customers will be sent as hashed ids. No names, emails or phone numbers are exported.</p>
        <div className="filter-pills">
          {Object.keys(PLATFORM_LABEL).map((p) => (
            <button key={p} className={`filter-pill${platform === p ? " active" : ""}`} aria-pressed={platform === p} onClick={() => setPlatform(p)}>{PLATFORM_LABEL[p]}</button>
          ))}
        </div>
      </Modal>
    </WorkspaceView>
  );
}
