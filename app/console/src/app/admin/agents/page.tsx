"use client";

// Admin › Agents (§9A AR-9, §8, acceptance #34): per-agent health — invocations,
// tool error rate, latency p50/p95, guardrail blocks, recent traces — and per
// action the current autonomy level plus shown → accepted → converted from the
// outcome loop.

import { humanizeCode } from "@/lib/text";
import { useState } from "react";
import { useApi } from "@/lib/hooks";
import { Async, Badge, Card, DataTable, DateTime, KpiGrid, Page, Pct, ProgressBar, Tabs, ViewHeader } from "@/components/app";
import { MarketPicker, TenantOnly } from "../common";
import { agentLabel } from "@/lib/agents";

const STATUS: Record<string, any> = { healthy: "green", degraded: "orange", idle: "gray" };
// Where each agent runs (ADR 0009): its own AgentCore runtime, the service Lambda, or Amazon Quick.
const RUNS_ON: Record<string, string> = { agentcore: "AgentCore (own runtime)", in_process: "In-process", quick: "Amazon Quick" };

export default function AgentsPage() {
  const [market, setMarket] = useState("us");
  const q = useApi<any>(`/admin/agents/health?market_id=${market}`, { pollMs: 60000 });
  const [sel, setSel] = useState("A2");
  return (
    <Page>
      <ViewHeader title="Agents" subtitle="Health, latency, guardrail blocks, and the outcome loop per agent action."
        actions={<MarketPicker value={market} onChange={setMarket} />} />
      <TenantOnly what="The Agents panel">
        <Async q={q}>
          {(d) => {
            const a = d.agents.find((x: any) => x.agent_id === sel) || d.agents[0];
            return (
              <>
                <Card title="All agents">
                  <DataTable rows={d.agents} rowKey={(x: any) => x.agent_id} onRowClick={(x: any) => setSel(x.agent_id)}
                    columns={[
                      { key: "id", header: "Agent", render: (x: any) => <span><b>{agentLabel(x.agent_id)}</b> <span className="dim">{x.name}</span></span> },
                      { key: "s", header: "Status", sort: (x: any) => x.status, render: (x: any) => <Badge tone={STATUS[x.status]}>{humanizeCode(x.status)}</Badge> },
                      { key: "rt", header: "Runs on", sort: (x: any) => x.runtime?.mode ?? "", render: (x: any) => RUNS_ON[x.runtime?.mode] ?? "—" },
                      { key: "n", header: "Invocations", num: true, sort: (x: any) => x.invocations, render: (x: any) => x.invocations },
                      { key: "e", header: "Error rate", num: true, sort: (x: any) => x.error_rate ?? -1, render: (x: any) => <Pct value={x.error_rate} decimals={1} /> },
                      { key: "p50", header: "p50 ms", num: true, sort: (x: any) => x.latency_p50_ms ?? -1, render: (x: any) => x.latency_p50_ms ?? "—" },
                      { key: "p95", header: "p95 ms", num: true, sort: (x: any) => x.latency_p95_ms ?? -1, render: (x: any) => x.latency_p95_ms ?? "—" },
                      { key: "g", header: "Guardrail blocks", num: true, sort: (x: any) => x.guardrail_blocks, render: (x: any) => x.guardrail_blocks },
                      { key: "last", header: "Last activity", sort: (x: any) => x.last_activity, render: (x: any) => <DateTime value={x.last_activity} /> },
                    ]} />
                </Card>
                <Tabs value={a.agent_id} onChange={setSel} tabs={d.agents.map((x: any) => ({ id: x.agent_id, label: agentLabel(x.agent_id) }))} />
                <KpiGrid items={[
                  { label: "Invocations", value: a.invocations },
                  { label: "Error rate", value: <Pct value={a.error_rate} decimals={1} />, trend: (a.error_rate || 0) > 0.05 ? "down" : undefined },
                  { label: "Latency p50 / p95 (ms)", value: `${a.latency_p50_ms ?? "—"} / ${a.latency_p95_ms ?? "—"}` },
                  { label: "Guardrail blocks", value: a.guardrail_blocks },
                ]} />
                <div className="grid gap-4 md:grid-cols-2">
                  <Card title={`Actions · ${agentLabel(a.agent_id)}`}>
                    <DataTable dense rows={a.actions} rowKey={(x: any) => x.action_id} empty="No registered actions."
                      columns={[
                        { key: "a", header: "Action", sort: (x: any) => x.action_id, render: (x: any) => <span className="mono text-xs">{x.action_id}</span> },
                        { key: "l", header: "Autonomy", render: (x: any) => x.level ? <span><Badge tone="blue">{x.level}</Badge>{x.hard_rule && <Badge tone="red">≤ {x.ceiling}</Badge>}</span> : <span className="dim">—</span> },
                        { key: "s", header: "Shown", num: true, sort: (x: any) => x.shown, render: (x: any) => x.shown },
                        { key: "acc", header: "Accepted", num: true, sort: (x: any) => x.acceptance_rate ?? -1, render: (x: any) => <span>{x.accepted} <span className="dim">(<Pct value={x.acceptance_rate} />)</span></span> },
                        { key: "c", header: "Converted", num: true, sort: (x: any) => x.conversion_rate ?? -1,
                          render: (x: any) => <div className="flex items-center gap-2 justify-end">{x.converted}<div style={{ width: 60 }}><ProgressBar value={(x.conversion_rate || 0) * 100} /></div></div> },
                      ]} />
                  </Card>
                  <Card title="Top tools">
                    {a.top_tools.length === 0 ? <div className="dim text-sm">No activity.</div> : a.top_tools.map((t: any) => (
                      <div key={t.tool} className="flex items-center gap-3 py-1"><span className="mono text-xs flex-1">{t.tool}</span>
                        <div style={{ width: 120 }}><ProgressBar value={t.count} max={a.top_tools[0].count} tone="blue" /></div><span className="text-xs w-10 text-right">{t.count}</span></div>
                    ))}
                  </Card>
                </div>
                <Card title="Recent traces">
                  <DataTable dense rows={a.recent_traces} rowKey={(t: any) => t.trace_id}
                    columns={[
                      { key: "ts", header: "When", render: (t: any) => <DateTime value={t.ts} /> },
                      { key: "tool", header: "Tool", render: (t: any) => <span className="mono text-xs">{t.tool}</span> },
                      { key: "o", header: "Outcome", render: (t: any) => <Badge tone={t.outcome === "ok" ? "green" : "red"}>{t.outcome}</Badge> },
                      { key: "lat", header: "ms", num: true, render: (t: any) => t.latency_ms ?? "—" },
                      { key: "au", header: "Autonomy", render: (t: any) => t.autonomy_level || "—" },
                      { key: "ref", header: "Journey / correlation", render: (t: any) => <span className="mono text-xs">{t.journey_id || t.correlation_id || "—"}</span> },
                      { key: "why", header: "Routing", render: (t: any) => <span className="text-xs muted">{t.routing_decision}</span> },
                    ]} />
                </Card>
              </>
            );
          }}
        </Async>
      </TenantOnly>
    </Page>
  );
}
