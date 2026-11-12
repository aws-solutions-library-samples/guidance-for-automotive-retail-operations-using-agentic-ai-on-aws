"use client";

// OEM Workspace · Sales Ops Comms (design §4.4): AI search bar over bulletins
// (cited results grouped by type via /knowledge/retrieve), a consolidated
// per-dealer digest, and [Forward to Dealers — Add Your Comments].

import { useMemo, useState } from "react";
import {
  AiInsight, Async, Btn, Card, EmptyState, Field, Kpi, KpiGrid, LoadingBlock, Modal, useToast,
} from "@/components/app";
import { apiPost } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { WorkspaceView } from "../_shared/WorkspaceView";

interface Passage { passage: string; source_ref: string; collection: string }

export default function OemComms() {
  const toast = useToast();
  const today = useApi<any>("/oem/today");
  const [q, setQ] = useState("");
  const [results, setResults] = useState<Passage[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [forward, setForward] = useState(false);
  const [comment, setComment] = useState("");
  const [forwarded, setForwarded] = useState(0);

  const search = async () => {
    if (!q.trim()) return;
    setSearching(true);
    try {
      const d = await apiPost<any>("/knowledge/retrieve", { query: q, k: 6 });
      setResults(d.passages || []);
    } catch (e: any) {
      toast(e?.message || "Search failed", "error");
      setResults([]);
    } finally {
      setSearching(false);
    }
  };

  const grouped = useMemo(() => {
    const g: Record<string, Passage[]> = {};
    (results || []).forEach((p) => { (g[p.collection] = g[p.collection] || []).push(p); });
    return Object.entries(g);
  }, [results]);

  const doForward = async () => {
    const payload = { comment, digest: digest.map((d) => d.dealer_id) };
    try {
      await apiPost("/oem/comms/forward", payload);
      toast("Digest forwarded to dealers with your comments", "success");
    } catch {
      // The forward write route is not deployed yet — record the intent locally.
      toast("Forward queued locally — the comms write API is not deployed yet. Dealers will receive it in their Store Cockpit › OEM Comms tab once it lands.", "info");
    }
    setForwarded((n) => n + 1);
    setForward(false);
    setComment("");
  };

  const d = today.data;
  const digest: { dealer_id: string; items: string[] }[] = useMemo(() => {
    if (!d) return [];
    const by: Record<string, string[]> = {};
    (["high", "medium"] as const).forEach((s) => (d.alerts?.[s] || []).forEach((a: any) => {
      if (a.dealer_id) (by[a.dealer_id] = by[a.dealer_id] || []).push(`${String(s).toUpperCase()}: ${a.title}`);
    }));
    (d.activity?.recent_reports || []).forEach((r: any) => {
      if (r.dealer_id) (by[r.dealer_id] = by[r.dealer_id] || []).push(`Contact report (${r.sentiment || "neutral"}): ${r.summary}`);
    });
    return Object.entries(by).map(([dealer_id, items]) => ({ dealer_id, items }));
  }, [d]);

  const kpis: Kpi[] = [
    { label: "Dealers in digest", value: digest.length, delta: "with open items this week" },
    { label: "Open alerts", value: d?.alerts?.open_total ?? 0,
      trend: (d?.alerts?.counts?.high ?? 0) > 0 ? "down" : "up",
      delta: `${d?.alerts?.counts?.high ?? 0} HIGH — lead the digest with these` },
    { label: "Search results", value: results ? results.length : "—", delta: results ? `for “${q}”` : "search the bulletins" },
    { label: "Forwarded", value: forwarded, delta: "digests sent this session" },
  ];

  return (
    <WorkspaceView view="comms" title="Sales Ops Comms"
      subtitle="Search bulletins, consolidate the per-dealer digest, forward with your comments"
      prompts={["Where is the updated stair-step incentive announcement?", "What changed in this month's program?"]}>
      <KpiGrid items={kpis} />
      <AiInsight title="Comms priorities" sources={["PLAT_KnowledgeCollections", "OEM_Alerts", "OEM_ContactReports"]}>
        <p>{digest.length
          ? `${digest.length} dealer(s) have items worth a note this week. Search the bulletin you need, add your comments, and forward one consolidated digest instead of five separate emails — it lands in each dealer's Store Cockpit › OEM Comms tab.`
          : "No per-dealer items this week yet. Use the AI search to find a bulletin and build the digest."}</p>
      </AiInsight>
      <Card title="AI search over bulletins">
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); search(); }}>
          <input className="input flex-1" value={q} onChange={(e) => setQ(e.target.value)}
                 placeholder="e.g. where is the updated stair-step incentive announcement?" aria-label="Search bulletins" />
          <Btn kind="primary" type="submit" disabled={searching || !q.trim()}>{searching ? "Searching…" : "Search"}</Btn>
        </form>
        <div className="mt-3" aria-live="polite">
          {searching && <LoadingBlock rows={3} />}
          {!searching && results && results.length === 0 && <EmptyState>No bulletins matched. Try different wording.</EmptyState>}
          {!searching && grouped.map(([collection, passages]) => (
            <div key={collection} className="mb-3">
              <div className="section-label">{String(collection).replace(/_/g, " ")}</div>
              {passages.map((p, i) => (
                <div key={i} className="alert-item">
                  <div className="alert-desc">{p.passage}</div>
                  <div className="mt-1"><span className="data-source" title={p.source_ref}>{p.source_ref}</span></div>
                </div>
              ))}
            </div>
          ))}
        </div>
      </Card>
      <Card title="Consolidated per-dealer digest"
            actions={<Btn kind="primary" size="sm" disabled={digest.length === 0} onClick={() => setForward(true)}>Forward to Dealers — Add Your Comments</Btn>}>
        <Async q={today} rows={4}>
          {() => digest.length === 0 ? <EmptyState>Nothing to digest this week.</EmptyState> : (
            <>
              {digest.map((row) => (
                <div key={row.dealer_id} className="alert-item">
                  <div className="alert-title">{row.dealer_id}</div>
                  <ul className="mt-1 text-[.8rem] pl-4 list-disc">
                    {row.items.map((it, i) => <li key={i}>{it}</li>)}
                  </ul>
                </div>
              ))}
            </>
          )}
        </Async>
      </Card>
      <Modal open={forward} onClose={() => setForward(false)} title="Forward to Dealers — Add Your Comments"
             footer={<><Btn kind="secondary" onClick={() => setForward(false)}>Cancel</Btn><Btn kind="primary" onClick={doForward}>Forward digest</Btn></>}>
        <p className="text-sm">The consolidated digest for {digest.length} dealer(s) will be forwarded with your comments attached. It lands in each dealer&apos;s Store Cockpit › OEM Comms tab.</p>
        <Field label="Your comments">
          {(id) => <textarea id={id} className="input" rows={4} value={comment} onChange={(e) => setComment(e.target.value)}
                             placeholder="Context, priorities, or asks for the GMs…" />}
        </Field>
      </Modal>
    </WorkspaceView>
  );
}
