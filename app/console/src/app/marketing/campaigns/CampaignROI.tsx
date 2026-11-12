"use client";

// Campaign ROI (A5, §5.7 / design §5.1): ROI by campaign, spend & conversions by
// dealer and channel, funnel drop-off by source, and the A5 budget-shift
// recommendation card ("shift $X A→B, +Y deliveries") that is submitted as an
// approval and decided on /approvals — A5 never changes spend. The NL ask box
// calls `/marketing/ask`, which sends scoped analytics facts to Bedrock and
// renders the returned engine in an aria-live region.

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  AiInsight, Async, Badge, Btn, Card, DataTable, ErrorState, Field, Kpi, KpiGrid, Money, Pct, ProgressBar, RecCard, Tabs, useToast,
} from "@/components/app";
import { apiPost } from "@/lib/api";
import { useApi, useMutation, useQueryParam } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";
import { humanizeCode, stripMarkdown } from "@/lib/text";
import { QuickPanel } from "../QuickPanel";

const SUBMIT = ["oem_marketing", "oem_cx", "oem_finance", "oem_dm", "oem_regional"];
type Tab = "campaigns" | "dealers" | "channels" | "funnel";

/** Campaign ids are joined on the normalized trailing integer server-side, so a
 *  deep link may carry either form (e.g. CMP-7 vs CAMP-US-007). */
const sameCampaign = (a?: string | null, b?: string | null) => {
  if (!a || !b) return false;
  if (a === b) return true;
  const na = /(\d+)\s*$/.exec(a)?.[1], nb = /(\d+)\s*$/.exec(b)?.[1];
  return Boolean(na && nb && parseInt(na, 10) === parseInt(nb, 10));
};

/** Labeled natural-language ask box; the answer renders in a live region so
 *  screen readers hear it without losing focus (design §5.1 accessibility). */
export function AskBox({ askPath = "/marketing/ask", view = "campaigns" }: { askPath?: string; view?: string }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const ask = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!q.trim() || busy) return;
    setBusy(true); setErr(null);
    try { setRes(await apiPost<any>(askPath, { question: q.trim(), view })); }
    catch (ex: any) { setErr(ex.message); setRes(null); }
    finally { setBusy(false); }
  };
  return (
    <Card title="Ask your campaign data">
      <form className="flex flex-wrap items-end gap-2" onSubmit={ask}>
        <div className="flex-1 min-w-[240px]">
          <Field label="Question" hint="e.g. Which campaign has the best ROI? Where is the biggest funnel drop?">
            {(id) => <input id={id} className="input" value={q} onChange={(e) => setQ(e.target.value)}
                            placeholder="Ask in plain language…" />}
          </Field>
        </div>
        <div className="field"><Btn type="submit" size="sm" disabled={busy || !q.trim()}>{busy ? "Asking…" : "Ask"}</Btn></div>
      </form>
      <div aria-live="polite">
        {err && <ErrorState error={err} />}
        {res && (
          <div className="ai-insight">
            <div className="ai-insight-title">◆ Answer</div>
            <p className="text-sm whitespace-pre-wrap">{res.answer}</p>
            {res.guardrail && <Badge tone="orange">guardrail: {Array.isArray(res.guardrail) ? res.guardrail.join(", ") : String(res.guardrail)}</Badge>}
            {(res.citations || []).length > 0 && (
              <div className="mt-2">{res.citations.map((c: any, i: number) => <span key={i} className="data-source" title={c.ref}>{c.label}</span>)}</div>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}

export function CampaignROI() {
  const toast = useToast();
  const { persona } = usePersona();
  const q = useApi<any>("/marketing/campaigns");
  const submit = useMutation();
  const [tab, setTab] = useState<Tab>("campaigns");
  const [submitted, setSubmitted] = useState<any>(null);
  // Deep-link focus (§5.18.2): /r/campaign/{id} resolves here as ?focus={id}.
  const focusParam = useQueryParam("focus");
  const [focus, setFocus] = useState<string | null>(null);
  useEffect(() => { if (focusParam) setFocus(focusParam); }, [focusParam]);

  const doSubmit = async () => {
    const out: any = await submit.run("/marketing/campaigns/budget-shift/submit", {});
    if (out?.submitted) { setSubmitted(out); toast(`Approval ${out.approval_id} requested from OEM finance`, "success"); }
    else toast(out?.reason || submit.error || "Could not submit", "error");
  };

  return (
    <Async q={q} rows={8}>
      {(d) => {
        const roi = d.roi, k = roi.kpis || {}, shift = d.budget_shift;
        const best = (roi.campaigns as any[]).find((c) => c.roi != null);
        const kpis: Kpi[] = [
          { label: "Campaigns", value: k.campaigns },
          { label: "Total budget", value: <Money value={k.total_budget} /> },
          { label: "Attributed deliveries", value: k.attributed_deliveries, trend: "up" },
          { label: "Blended cost / delivery", value: <Money value={k.blended_cost_per_delivery} />, delta: best ? `best ${best.name}` : undefined },
        ];
        const maxRoi = Math.max(1, ...roi.campaigns.map((c: any) => Math.abs(c.roi || 0)));
        const focused = focus ? (roi.campaigns as any[]).find((c) => sameCampaign(c.campaign_id, focus)) : null;
        return (
          <>
            {focus && (
              <Card accent={focused ? "primary" : "warning"}>
                <div className="text-sm flex items-start justify-between gap-3">
                  <span>
                    {focused
                      ? <>Focused on campaign <b title={focused.campaign_id}>{focused.name}</b> from a deep link — it is highlighted in the table below.</>
                      : <>The linked campaign <span className="mono text-xs">{focus}</span> is not in your scope or no longer exists; showing all campaigns instead.</>}
                  </span>
                  <Btn kind="ghost" size="sm" onClick={() => setFocus(null)} aria-label="Dismiss campaign focus">✕</Btn>
                </div>
              </Card>
            )}
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
                <div className="flex gap-2 mt-3 items-center flex-wrap">
                  {SUBMIT.includes(persona?.role || "") && !submitted &&
                    <Btn kind="present" disabled={submit.pending} onClick={doSubmit}>{submit.pending ? "Submitting…" : "Submit for finance approval"}</Btn>}
                  {submitted && (
                    <>
                      <Badge tone="green">✓ {submitted.approval_id} pending OEM finance</Badge>
                      <Link className="underline text-sm" href="/approvals">Review in Approvals →</Link>
                    </>
                  )}
                </div>
              </RecCard>
            )}
            {shift && !shift.recommended && <div className="card dim text-sm">No budget shift recommended: {shift.note}</div>}

            <AskBox />

            <Card>
              <Tabs value={tab} onChange={setTab} tabs={[
                { id: "campaigns", label: "By campaign" }, { id: "dealers", label: "By dealer" },
                { id: "channels", label: "By channel" }, { id: "funnel", label: "Funnel by source" }]} />
              {tab === "campaigns" && (
                <DataTable rows={roi.campaigns} rowKey={(c: any) => c.campaign_id} initialSort={{ key: "roi", dir: "desc" }}
                  columns={[
                    { key: "name", header: "Campaign", sort: (c: any) => c.name, render: (c: any) => (
                      <span>{focus && sameCampaign(c.campaign_id, focus) && <Badge tone="blue">linked</Badge>} <b title={c.campaign_id}>{c.name}</b></span>) },
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
                    { key: "ch", header: "Channel (touchpoints on campaign journeys)", render: (r: any) => <b>{String(r.channel).replace("_", " ")}</b> },
                    { key: "t", header: "Touchpoints", num: true, sort: (r: any) => r.touchpoints, render: (r: any) => r.touchpoints },
                    { key: "c", header: "Converted journeys", num: true, sort: (r: any) => r.converted_journeys, render: (r: any) => r.converted_journeys },
                  ]} />
              )}
              {tab === "funnel" && (
                <div className="overflow-x-auto">
                  <table className="data-table">
                    <thead><tr><th>Source</th>{d.funnel.stages.map((s: string) => <th key={s} className="num">{s}</th>)}<th className="num">Conv.</th></tr></thead>
                    <tbody>
                      {d.funnel.sources.map((s: any) => (
                        <tr key={s.source}>
                          <td><b>{String(s.source).replace(/_/g, " ")}</b></td>
                          {s.steps.map((st: any) => (
                            <td key={st.stage} className="num" title={`${(st.drop_off_rate * 100).toFixed(0)}% drop-off`}
                                style={s.largest_drop?.stage === st.stage ? { color: "var(--down)", fontWeight: 700 } : undefined}>
                              {st.reached}
                            </td>
                          ))}
                          <td className="num"><Pct value={s.conversion_rate} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div className="dim text-xs mt-2">{d.funnel.method}. Red = largest drop-off for that source.</div>
                </div>
              )}
            </Card>
            <QuickPanel showFlows={false} />
          </>
        );
      }}
    </Async>
  );
}
