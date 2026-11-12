"use client";

// OEM Workspace · Campaign ROI (A5, design §4.5): ROI by campaign / platform /
// dealer, funnel drop-off by source, the budget-shift recommendation card
// ("shift $X from A to B, expected +Y deliveries") routed to /approvals, and a
// natural-language ask box (the right chat panel asks the campaign data).

import Link from "next/link";
import { useState } from "react";
import {
  AiInsight, Async, Badge, Btn, Card, DataTable, Kpi, KpiGrid, Money, Pct, ProgressBar, RecCard, Tabs, useToast,
} from "@/components/app";
import { useApi, useMutation } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";
import { humanizeCode, stripMarkdown } from "@/lib/text";
import { quickEnabled } from "@/lib/flags";
import { QuickPanel } from "../_shared/QuickPanel";
import { WorkspaceView } from "../_shared/WorkspaceView";

const SUBMIT = ["oem_marketing", "oem_cx", "oem_finance", "oem_dm", "oem_regional"];
type Tab = "campaigns" | "dealers" | "channels" | "funnel";

export default function OemCampaigns() {
  const toast = useToast();
  const { persona } = usePersona();
  const q = useApi<any>("/marketing/campaigns");
  const submit = useMutation();
  const [tab, setTab] = useState<Tab>("campaigns");
  const [submitted, setSubmitted] = useState<any>(null);

  const doSubmit = async () => {
    const out: any = await submit.run("/marketing/campaigns/budget-shift/submit", {});
    if (out?.submitted) { setSubmitted(out); toast(`Approval ${out.approval_id} requested from OEM finance`, "success"); }
    else toast(out?.reason || submit.error || "Could not submit", "error");
  };

  return (
    <WorkspaceView view="campaigns" askPath="/marketing/ask" chatTitle="Ask your campaign data"
      title="Campaign ROI" subtitle="Campaign performance for your scope — the Campaign Agent recommends, finance approves"
      prompts={["Which campaign has the best ROI?", "Should I shift budget?", "Where is the funnel leaking?"]}>
      <Async q={q} rows={8}>
        {(d) => {
          const roi = d.roi, k = roi.kpis || {}, shift = d.budget_shift;
          const best = (roi.campaigns as any[]).find((c) => c.roi != null);
          const kpis: Kpi[] = [
            { label: "Campaigns", value: k.campaigns, delta: "active in scope" },
            { label: "Total budget", value: <Money value={k.total_budget} />, delta: "allocated" },
            { label: "Attributed deliveries", value: k.attributed_deliveries, trend: "up", delta: "vs PY attribution model" },
            { label: "Blended cost / delivery", value: <Money value={k.blended_cost_per_delivery} />,
              delta: best ? `best: ${best.name}` : "no ROI data" },
          ];
          const maxRoi = Math.max(1, ...roi.campaigns.map((c: any) => Math.abs(c.roi || 0)));
          return (
            <>
              <KpiGrid items={kpis} />
              <AiInsight title="Budget efficiency" sources={d.insight.sources}>
                <p>{stripMarkdown(d.insight.text)}</p>
                {d.insight.largest_drop && (
                  <p>Largest funnel leak: <b>{humanizeCode(d.insight.largest_drop.source)}</b> journeys at <b>{humanizeCode(String(d.insight.largest_drop.stage || "").toLowerCase())}</b> ({(d.insight.largest_drop.drop_off_rate * 100).toFixed(0)}% drop).</p>
                )}
                <p className="dim text-xs">{roi.assumptions?.note}</p>
              </AiInsight>

              {shift?.recommended && (
                <RecCard icon="⇄" title="Recommended budget shift"
                  right={<Badge tone="orange">act with approval · {shift.autonomy_level}</Badge>}>
                  <p className="text-sm">
                    Shift <b><Money value={shift.shift.amount} /></b> from <b>{shift.shift.from.name}</b> (ROI {shift.shift.from.roi}) to{" "}
                    <b>{shift.shift.to.name}</b> (ROI {shift.shift.to.roi}) — expected <b>+{shift.shift.expected_extra_deliveries}</b> deliveries
                    (<Money value={shift.shift.expected_lift_gross} /> gross{shift.shift.expected_net_gain != null ? <>, net <Money value={shift.shift.expected_net_gain} /> after what it would have earned where it is</> : null}).
                  </p>
                  <div className="dim text-xs mt-1">{shift.note}</div>
                  <div className="flex gap-2 mt-3 items-center">
                    {SUBMIT.includes(persona?.role || "") && !submitted &&
                      <Btn kind="present" disabled={submit.pending} onClick={doSubmit}>{submit.pending ? "Submitting…" : "Approve → send to Approvals"}</Btn>}
                    {submitted && <Badge tone="green">✓ {submitted.approval_id} pending OEM finance — <Link className="underline" href="/approvals">open Approvals</Link></Badge>}
                  </div>
                </RecCard>
              )}
              {shift && !shift.recommended && <div className="card dim text-sm">No budget shift recommended: {shift.note}</div>}

              <Card>
                <Tabs value={tab} onChange={setTab} tabs={[
                  { id: "campaigns", label: "By campaign" }, { id: "dealers", label: "By dealer" },
                  { id: "channels", label: "By platform" }, { id: "funnel", label: "Funnel by source" }]} />
                {tab === "campaigns" && (
                  <DataTable rows={roi.campaigns} rowKey={(c: any) => c.campaign_id} initialSort={{ key: "roi", dir: "desc" }}
                    columns={[
                      { key: "name", header: "Campaign", sort: (c: any) => c.name, render: (c: any) => <span><b>{c.name}</b> <span className="dim text-xs">{c.campaign_id}</span></span> },
                      { key: "obj", header: "Objective", render: (c: any) => <Badge tone="blue">{String(c.objective || "").replace(/_/g, " ")}</Badge> },
                      { key: "aud", header: "Audience", render: (c: any) => <span className="text-xs">{String(c.audience || "").replace(/_/g, " ")}</span> },
                      { key: "budget", header: "Budget", num: true, sort: (c: any) => c.budget, render: (c: any) => <Money value={c.budget} /> },
                      { key: "spent", header: "Spend to date", num: true, sort: (c: any) => c.spend_to_date, render: (c: any) => <span title={c.flight_elapsed != null ? `${Math.round(c.flight_elapsed * 100)}% of the flight` : undefined}><Money value={c.spend_to_date ?? c.budget} /></span> },
                      { key: "del", header: "Deliveries", num: true, sort: (c: any) => c.attributed_deliveries, render: (c: any) => c.attributed_deliveries },
                      { key: "cpd", header: "Cost / delivery", num: true, sort: (c: any) => c.cost_per_delivery, render: (c: any) => <Money value={c.cost_per_delivery} /> },
                      { key: "roi", header: "ROI", sort: (c: any) => c.roi,
                        render: (c: any) => c.roi == null ? "—" : <div className="flex items-center gap-2"><ProgressBar value={Math.abs(c.roi)} max={maxRoi} tone={c.roi >= 0 ? "green" : "red"} /><span className="text-xs">{c.roi}</span></div> },
                    ]} />
                )}
                {tab === "dealers" && (
                  <>
                    <DataTable rows={d.spend.by_dealer} rowKey={(r: any) => r.dealer_id} initialSort={{ key: "cpc", dir: "asc" }}
                      columns={[
                        { key: "dealer", header: "Dealer", sort: (r: any) => r.dealer_id, render: (r: any) => <b>{r.dealer_id}</b> },
                        { key: "spend", header: "Allocated spend", num: true, sort: (r: any) => r.allocated_spend, render: (r: any) => <Money value={r.allocated_spend} /> },
                        { key: "j", header: "Journeys", num: true, sort: (r: any) => r.journeys, render: (r: any) => r.journeys },
                        { key: "c", header: "Conversions", num: true, sort: (r: any) => r.conversions, render: (r: any) => r.conversions },
                        { key: "cpc", header: "Cost / conversion", num: true, sort: (r: any) => r.cost_per_conversion, render: (r: any) => <Money value={r.cost_per_conversion} /> },
                      ]} />
                    <div className="dim text-xs mt-2">{d.spend.allocation}</div>
                  </>
                )}
                {tab === "channels" && (
                  <DataTable rows={d.by_channel} rowKey={(r: any) => r.channel}
                    columns={[
                      { key: "ch", header: "Platform (touchpoints on campaign journeys)", render: (r: any) => <b>{String(r.channel).replace("_", " ")}</b> },
                      { key: "t", header: "Touchpoints", num: true, sort: (r: any) => r.touchpoints, render: (r: any) => r.touchpoints },
                      { key: "c", header: "Converted journeys", num: true, sort: (r: any) => r.converted_journeys, render: (r: any) => r.converted_journeys },
                    ]} />
                )}
                {tab === "funnel" && (
                  <div className="overflow-x-auto">
                    <table className="data-table">
                      <thead><tr><th scope="col">Source</th>{d.funnel.stages.map((s: string) => <th key={s} scope="col" className="num">{s}</th>)}<th scope="col" className="num">Conv.</th></tr></thead>
                      <tbody>
                        {d.funnel.sources.map((s: any) => (
                          <tr key={s.source}>
                            <td><b>{String(s.source).replace(/_/g, " ")}</b></td>
                            {s.steps.map((st: any) => (
                              <td key={st.stage} className="num" title={`${(st.drop_off_rate * 100).toFixed(0)}% drop-off`}
                                  style={s.largest_drop?.stage === st.stage ? { color: "var(--down, var(--danger))", fontWeight: 700 } : undefined}>
                                {st.reached}
                              </td>
                            ))}
                            <td className="num"><Pct value={s.conversion_rate} /></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <div className="dim text-xs mt-2">{d.funnel.method}. Highlighted cell = largest drop-off for that source.</div>
                  </div>
                )}
              </Card>
              {quickEnabled() && <QuickPanel showFlows={false} />}
            </>
          );
        }}
      </Async>
    </WorkspaceView>
  );
}
