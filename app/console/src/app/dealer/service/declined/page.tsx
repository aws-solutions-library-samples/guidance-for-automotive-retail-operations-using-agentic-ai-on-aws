"use client";

// Declined-work register (§2.6.2a "Service Desk › Declined", §3.7
// schedule_declined_followup) — every open declined line across VINs with its
// follow-up cadence, age, and conversion state. Declined work older than 90
// days routes to A2 nurture (service templates, §2.1.1); re-presenting always
// uses the original finding + evidence. Amounts come from the stored estimate
// line (A7 build_estimate) — the UI never computes a price.

import Link from "next/link";
import { useMemo, useState } from "react";
import {
  AiInsight, Async, Badge, Btn, Card, DataTable, DateTime, EmptyState, FilterPills, KpiGrid, MarketProvider, Money,
  Page, SourceRef, ViewHeader, useToast,
} from "@/components/app";
import { AgentActionsProvider } from "@/components/AgentBar";
import { useApi, useMutation, timeAgo } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";

type Scope = "open" | "scheduled" | "booked" | "all";

const ageDays = (ts?: string | null) => {
  const d = ts ? Date.parse(ts) : NaN;
  return Number.isNaN(d) ? null : Math.floor((Date.now() - d) / 86400000);
};

export default function DeclinedRegister() {
  const toast = useToast();
  const { persona } = usePersona();
  const [scope, setScope] = useState<Scope>("open");
  const q = useApi<any>("/service/declined", { pollMs: 60000 });
  const patch = useMutation("PATCH");

  const items: any[] = useMemo(() => {
    const xs: any[] = q.data?.items || (Array.isArray(q.data) ? q.data : []);
    if (scope === "all") return xs;
    if (scope === "booked") return xs.filter((d) => ["booked", "added_to_ro", "converted"].includes(d.status));
    if (scope === "scheduled") return xs.filter((d) => d.followup_at && !["booked", "added_to_ro", "converted"].includes(d.status));
    return xs.filter((d) => !["booked", "added_to_ro", "converted"].includes(d.status));
  }, [q.data, scope]);

  const all: any[] = q.data?.items || (Array.isArray(q.data) ? q.data : []);
  const open = all.filter((d) => !["booked", "added_to_ro", "converted"].includes(d.status));
  const over90 = open.filter((d) => (ageDays(d.declined_at) ?? 0) > 90);
  const booked = all.filter((d) => ["booked", "added_to_ro", "converted"].includes(d.status));
  const quoted = open.reduce((n, d) => n + (typeof d.amount === "number" ? d.amount : 0), 0);

  const setCadence = async (d: any, days: number) => {
    const out = await patch.run(`/service/declined/${encodeURIComponent(d.declined_id)}`, { cadence_days: days });
    if (out) { toast(`Follow-up for ${d.item} in ${days} days — the Journey Agent's nurture (service template)`, "success"); q.reload(); }
    else toast(patch.error || "Could not schedule the follow-up", "error");
  };

  // Backend-dependent: the register read model may not be deployed yet.
  const notDeployed = Boolean(q.error && !q.data);

  return (
    <MarketProvider marketId={persona?.marketId}>
      {/* Chat scope for the global AgentBar — A7's drawer answers about this
          register stay grounded on declined-work follow-up. */}
      <AgentActionsProvider value={{ chatContext: "declined work follow-up" }}>
      <Page>
        <ViewHeader title="Declined work" subtitle="Every declined estimate line across VINs — schedule the follow-up, re-present with the original evidence, or book the visit." />
        {notDeployed ? (
          <Card>
            <EmptyState>
              The declined-work register isn&apos;t available from the API yet ({q.error}). Declined lines still appear
              on each RO&apos;s Estimate view and in the per-VIN service history.
            </EmptyState>
            <div className="flex justify-center"><Btn kind="secondary" onClick={q.reload}>Retry</Btn></div>
          </Card>
        ) : (
          <Async q={q} rows={6}>
            {() => (
              <>
                <KpiGrid items={[
                  { label: "Open declined items", value: open.length, onClick: () => setScope("open") },
                  { label: "Older than 90 days", value: over90.length, trend: over90.length ? "down" : undefined, delta: over90.length ? "Journey Agent nurture due" : "none", onClick: () => setScope("open") },
                  { label: "Follow-up scheduled", value: open.filter((d) => d.followup_at).length, onClick: () => setScope("scheduled") },
                  { label: "Booked / converted", value: booked.length, onClick: () => setScope("booked") },
                ]} />
                <div className="grid gap-4 lg:grid-cols-3">
                  <Card className="lg:col-span-2" title="Register"
                        actions={<FilterPills value={scope} onChange={setScope} options={[
                          { id: "open", label: "Open" }, { id: "scheduled", label: "Scheduled" },
                          { id: "booked", label: "Booked" }, { id: "all", label: "All" },
                        ]} />}>
                    {items.length ? (
                      <DataTable rows={items} rowKey={(d: any, i) => d.declined_id || String(i)}
                                 initialSort={{ key: "age", dir: "desc" }}
                                 filterText={(d: any) => `${d.item} ${d.vin || ""} ${d.customer_name || ""}`}
                                 columns={[
                                   { key: "item", header: "Item", sort: (d: any) => d.item, render: (d: any) => (
                                     <>{d.item} {d.finding_ref && <SourceRef refId={d.finding_ref} />}</>) },
                                   { key: "vin", header: "VIN", render: (d: any) => d.vin ? (
                                     <Link className="underline mono" href={`/dealer/service/history/${encodeURIComponent(d.vin)}`}>{d.vin}</Link>) : "—" },
                                   { key: "age", header: "Declined", sort: (d: any) => d.declined_at || "", render: (d: any) => {
                                     const a = ageDays(d.declined_at);
                                     return (<span className="flex gap-1 items-center"><DateTime value={d.declined_at} mode="date" />
                                       {a != null && a > 90 && <Badge tone="orange"><span aria-hidden>⏳</span> {a}d — nurture</Badge>}</span>);
                                   } },
                                   { key: "amt", header: "Quoted", num: true, sort: (d: any) => d.amount ?? 0, render: (d: any) => <Money value={d.amount} /> },
                                   { key: "next", header: "Next follow-up", sort: (d: any) => d.followup_at || "", render: (d: any) =>
                                     d.followup_at ? <>{timeAgo(d.followup_at)} (<DateTime value={d.followup_at} mode="date" />)</> : <span className="dim">not scheduled</span> },
                                   { key: "st", header: "Status", render: (d: any) => (
                                     <Badge tone={["booked", "added_to_ro", "converted"].includes(d.status) ? "green" : "orange"}>{(d.status || "open").replace(/_/g, " ")}</Badge>) },
                                   { key: "act", header: "", render: (d: any) => !["booked", "added_to_ro", "converted"].includes(d.status) ? (
                                     <span className="flex gap-1 flex-wrap items-center">
                                       {[30, 60, 90].map((n) => (
                                         <Btn key={n} kind={d.cadence_days === n ? "primary" : "alt"} size="sm" disabled={patch.pending}
                                              onClick={() => setCadence(d, n)}>{n}d</Btn>
                                       ))}
                                       {d.vin && <Link className="btn btn-secondary btn-sm" href={`/dealer/service/book?vin=${encodeURIComponent(d.vin)}`}>Book</Link>}
                                     </span>) : null },
                                 ]} />
                    ) : <EmptyState>No declined work in this view.</EmptyState>}
                  </Card>
                  <AiInsight title="Declined-work follow-up" sources={["DEALER_DeclinedWork", "DEALER_NurturePlans"]}>
                    {open.length ? (
                      <p>{open.length} open item(s) worth <Money value={quoted} />.{" "}
                        {over90.length ? `${over90.length} are older than 90 days and route to the Journey Agent's nurture (service template, never a sales message). ` : ""}
                        Re-presenting always shows the original finding and evidence.</p>
                    ) : <p>No open declined work — every finding was approved, booked, or converted.</p>}
                  </AiInsight>
                </div>
              </>
            )}
          </Async>
        )}
      </Page>
      </AgentActionsProvider>
    </MarketProvider>
  );
}
