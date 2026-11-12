"use client";

// Service Board (design doc §3.1 / spec §5.20.1) — service advisor / manager
// home. DM Assistant overview pattern in dark tokens: service-journey stepper,
// KPI strip (lane load · promised-time at-risk · waiting approval · parts
// holds), 8-stage kanban with icon+text badges, filters, right rail (today's
// arrivals, A7 attention list aria-live), bottom AI Log. The A7 automations and
// their grounded chat live in the global AgentBar; this page contributes the
// run-now bindings and the lane's trace scope. Card click → RO view;
// bulk re-present pending estimates (also bound to the Approval chaser agent).

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { AgentActionsProvider } from "@/components/AgentBar";
import {
  AiInsight, AiLog, Async, Badge, Btn, DateTime, EmptyState, FilterPills, Kanban, KpiGrid, MarketProvider, Panel,
  SERVICE_STAGES, ServiceStepper, stageLabel, useToast,
} from "@/components/app";
import { useApi, useMutation, timeAgo } from "@/lib/hooks";
import { useCanOpen, usePersona } from "@/lib/persona";
import { CardBadges, isAdvisor } from "./common";

type Day = "all" | "today" | "tomorrow";

export default function ServiceBoard() {
  const router = useRouter();
  const toast = useToast();
  const { persona } = usePersona();
  const can = useCanOpen();
  const [advisor, setAdvisor] = useState("");
  const [tech, setTech] = useState("");
  const [wait, setWait] = useState("");
  const [day, setDay] = useState<Day>("all");
  const [mine, setMine] = useState(false);

  useEffect(() => {
    // deep links from the Service Cockpit carry filters (?stage=, ?badge=)
    const p = new URLSearchParams(window.location.search);
    if (p.get("day")) setDay(p.get("day") as Day);
    if (p.get("wait_type")) setWait(p.get("wait_type")!);
  }, []);
  const badgeFilter = typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("badge") : null;
  const stageFilter = typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("stage") : null;

  const qs = new URLSearchParams();
  if (advisor) qs.set("advisor_id", advisor);
  if (tech) qs.set("technician_id", tech);
  if (wait) qs.set("wait_type", wait);
  if (day !== "all") qs.set("day", day);
  if (mine) qs.set("mine", "1");
  const board = useApi<any>(`/service/journeys?${qs.toString()}`, { pollMs: 30000 });
  const attention = useApi<any>(persona?.role === "dealer_technician" ? null : "/service/attention", { pollMs: 30000 });
  const arrivals = useApi<any>("/service/arrivals", { pollMs: 60000 });
  const represent = useMutation();

  const cards: any[] = useMemo(() => {
    let cs: any[] = board.data?.cards || [];
    if (badgeFilter) cs = cs.filter((c) => c.badges?.[badgeFilter]);
    if (stageFilter) cs = cs.filter((c) => c.stage === stageFilter);
    return cs;
  }, [board.data, badgeFilter, stageFilter]);
  const k = board.data?.kpis || {};
  const waitingEstimates: string[] = (attention.data?.items || []).filter((i: any) => i.kind === "estimate_waiting").map((i: any) => i.estimate_id);

  const rePresent = async () => {
    const out = await represent.run("/service/estimates/re-present", { estimate_ids: waitingEstimates });
    if (out) { toast(`Re-presented ${out.re_presented.length} estimate(s)${out.failed.length ? `, ${out.failed.length} skipped` : ""}`, "success"); attention.reload(); board.reload(); }
    else toast(represent.error || "Re-present failed", "error");
  };

  const filters = board.data?.filters || { advisors: [], technicians: [], wait_types: [] };
  const columns = SERVICE_STAGES.map((st) => ({ id: st, title: stageLabel(st), items: cards.filter((c) => c.stage === st) }));

  return (
    <MarketProvider marketId={persona?.marketId}>
      <AgentActionsProvider value={{
        serviceJourneyId: cards[0]?.service_journey_id || null,
        actions: waitingEstimates.length ? {
          "a7.approval_chaser": { label: `Re-present (${waitingEstimates.length})`, run: rePresent },
        } : {},
      }}>
      <div className="flex flex-col flex-1 min-h-0">
        <div className="main-layout two flex-1">
          <div className="panel">
            <div className="page">
              <div className="view-header">
                <div>
                  <h1 className="section-title">Service Board</h1>
                  <p className="section-subtitle">Every open service journey by stage · {cards.length} shown{board.data?.masked ? " · technician view (customer data hidden)" : ""}</p>
                </div>
                <div className="flex gap-2 flex-wrap">
                  {isAdvisor(persona?.role) && <Link href="/dealer/service/book" className="btn btn-primary">＋ Book service</Link>}
                  {isAdvisor(persona?.role) && (
                    <Btn kind="secondary" onClick={rePresent} disabled={!waitingEstimates.length || represent.pending}>
                      Re-present pending estimates ({waitingEstimates.length})
                    </Btn>
                  )}
                </div>
              </div>
              <div className="mb-3"><ServiceStepper compact /></div>
              <KpiGrid items={[
                { label: "Lane load", value: k.lane_load, onClick: () => router.push("/dealer/service") },
                { label: "Promised-time at risk", value: k.overdue_promise, trend: k.overdue_promise ? "down" : "up", delta: k.overdue_promise ? `${k.promise_past ?? 0} past · ${k.promise_at_risk ?? 0} projected late` : "on time", onClick: () => router.push("/dealer/service?badge=overdue_promise") },
                { label: "Waiting approval", value: k.waiting_approval, trend: k.waiting_approval ? "down" : undefined, delta: k.waiting_approval ? "needs follow-up" : "clear", onClick: () => router.push("/dealer/service?badge=waiting_approval") },
                { label: "Parts holds", value: k.parts_hold, onClick: () => router.push("/dealer/service?badge=parts_hold") },
              ]} />
              {(badgeFilter || stageFilter) && (
                <div className="mb-3 text-sm muted">Filtered: {badgeFilter?.replace(/_/g, " ") || stageLabel(stageFilter || "")} · <Link className="underline" href="/dealer/service">clear</Link></div>
              )}
              <div className="flex flex-wrap gap-3 items-end mb-3">
                <label className="field mb-0"><span className="text-xs muted">Advisor</span>
                  <select className="select" value={advisor} onChange={(e) => setAdvisor(e.target.value)}>
                    <option value="">All advisors</option>
                    {filters.advisors.map((a: any) => <option key={a.id} value={a.id}>{a.name || a.id}</option>)}
                  </select></label>
                <label className="field mb-0"><span className="text-xs muted">Technician</span>
                  <select className="select" value={tech} onChange={(e) => setTech(e.target.value)}>
                    <option value="">All technicians</option>
                    {filters.technicians.map((a: any) => <option key={a.id} value={a.id}>{a.name || a.id}</option>)}
                  </select></label>
                <label className="field mb-0"><span className="text-xs muted">Wait type</span>
                  <select className="select" value={wait} onChange={(e) => setWait(e.target.value)}>
                    <option value="">Any</option>
                    {["wait", "drop", "loaner", "mobile", "pickup_delivery"].map((w) => <option key={w} value={w}>{w.replace("_", " ")}</option>)}
                  </select></label>
                <FilterPills value={day} onChange={setDay} options={[{ id: "all", label: "All" }, { id: "today", label: "Today" }, { id: "tomorrow", label: "Tomorrow" }]} />
                {isAdvisor(persona?.role) && (
                  <label className="toggle"><input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> My journeys</label>
                )}
              </div>
              <Async q={board} rows={6}>
                {() => (
                  <Kanban columns={columns} empty="No journeys" renderCard={(c: any) => (
                    <Link key={c.service_journey_id} href={`/dealer/service/${encodeURIComponent(c.service_journey_id)}`} className="kanban-card block">
                      <div className="flex justify-between gap-2"><b className="truncate">{c.customer_name || c.vin}</b><span className="dim text-xs">{c.wait_type?.replace("_", " ")}</span></div>
                      <div className="muted text-xs truncate">{c.vehicle_name || "—"} · <span className="mono">{c.vin}</span></div>
                      <div className="text-xs mt-1">⏱ <DateTime value={c.promised_at} mode="datetime" /></div>
                      <div className="text-xs dim">{c.technician_name ? `Tech ${c.technician_name}` : "Unassigned"}{c.advisor_name ? ` · ${c.advisor_name}` : ""}</div>
                      <div className="mt-1"><CardBadges b={c.badges} /></div>
                    </Link>
                  )} />
                )}
              </Async>
              <AiInsight title="Service lane today" sources={["DEALER_ServiceJourneys", "DEALER_ServiceEstimates", "DEALER_PartsOrders"]}>
                {(() => {
                  // the insight counts what the board shows: lane_load excludes FOLLOW_UP, so name those separately
                  const total = (board.data?.cards || []).length;
                  const followUp = total - (k.lane_load || 0);
                  if (!total) return <p>No open service journeys in your scope.</p>;
                  return (
                    <p>{k.lane_load ? `${k.lane_load} journey${k.lane_load === 1 ? "" : "s"} in the lane` : "Nothing in the lane right now"}
                      {followUp ? `${k.lane_load ? ", plus" : ";"} ${followUp} in follow-up` : ""}.{" "}
                      {k.waiting_approval ? `${k.waiting_approval} estimate(s) wait on a customer decision — re-present or call. ` : ""}
                      {k.parts_hold ? `${k.parts_hold} RO(s) are held for parts; check the Parts desk for ETAs. ` : ""}
                      {k.overdue_promise ? `${k.overdue_promise} promise(s) are at risk — message those customers with a new time before it passes.` : "All promises on track."}</p>
                  );
                })()}
              </AiInsight>
            </div>
          </div>
          <div className="panel">
            <Panel title={<>Today&apos;s appts — not arrived</>} badge={arrivals.data?.items?.length ?? "…"} badgeKind="active" className="shrink-0">
              <Async q={arrivals} rows={2}>
                {(d) => d.items.length ? (
                  <div className="flex flex-col gap-2">{d.items.slice(0, 8).map((a: any) => {
                    const body = (checkin: boolean) => (<>
                      <div className="alert-title"><DateTime value={a.slot_start} /> · {a.wait_type?.replace("_", " ")}</div>
                      <div className="alert-desc mono">{a.vin}</div>
                      <div className="alert-time">{(a.services || []).join(", ") || "Service"}{checkin ? " · Check in →" : ""}</div>
                    </>);
                    // check-in is the service advisor's (POST …/checkin: ADVISORS)
                    return can("service.checkin")
                      ? <Link key={a.service_journey_id} href={`/dealer/service/${encodeURIComponent(a.service_journey_id)}/checkin`} className="alert-item block">{body(true)}</Link>
                      : <div key={a.service_journey_id} className="alert-item">{body(false)}</div>;
                  })}</div>
                ) : <EmptyState>No arrivals pending.</EmptyState>}
              </Async>
            </Panel>
            {persona?.role !== "dealer_technician" && (
              <Panel title="Needs attention" badge={attention.data?.count ?? "…"} badgeKind="danger" ariaLive className="shrink-0">
                <Async q={attention} rows={3}>
                  {(d) => d.items.length ? (
                    <div className="flex flex-col gap-2">{d.items.slice(0, 10).map((i: any, n: number) => (
                      // a waiting estimate opens Estimate & Approval for the estimate
                      // roles; everyone else (the parts counter) opens the repair order
                      <Link key={n} href={`/dealer/service/${encodeURIComponent(i.service_journey_id)}${i.kind === "estimate_waiting" && can("service.estimate") ? "/estimate" : ""}`}
                            className={`alert-item block severity-${i.severity}`}>
                        <div className="alert-title">{i.title}</div>
                        {/* promise times in the store's own hours, never the raw UTC string */}
                        <div className="alert-desc">{i.kind === "past_promise" && i.promised_at
                          ? <>promised <DateTime value={i.promised_at} /> · projected {i.projected_at ? <DateTime value={i.projected_at} /> : "—"}</>
                          : i.kind === "backorder" && i.eta && !Number.isNaN(Date.parse(i.eta))
                            ? <>ETA <DateTime value={i.eta} /></>
                            : i.detail}</div>
                        <div className="alert-time">{timeAgo(i.since)}</div>
                      </Link>
                    ))}</div>
                  ) : <EmptyState>Nothing needs attention.</EmptyState>}
                </Async>
              </Panel>
            )}
            {board.data?.masked && <div className="p-4"><Badge tone="gray">Customer contact data hidden for technicians</Badge></div>}
          </div>
        </div>
        <AiLog serviceJourneyId={cards[0]?.service_journey_id || null} />
      </div>
      </AgentActionsProvider>
    </MarketProvider>
  );
}
