"use client";

// Store-scoped campaign ROI (§5.7 dealer view, dealer_manager): the OEM
// campaigns this store's journeys came from, attributed deliveries for this
// store only, spend allocated by the store's share of each campaign (flagged
// as an estimate), funnel drop-off per campaign, and — when the caller's role
// can read A5's network view — the budget-shift recommendation card.
// QuickSight trend embeds and the ask_quick box belong to the Quick App.

import {
  AiInsight, Async, Badge, Card, DataTable, MarketProvider, Money, Page, Pct, PURCHASE_STAGES, stageLabel, ViewHeader,
} from "@/components/app";
import { ApiInsight, ApiKpiGrid, titleCase, useFollow } from "@/app/dealer/kpis";
import { useApi, useQueryParam } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";

/** Cumulative "reached stage" counts from a current-stage distribution — purely
 *  presentational arithmetic on the funnel the API already returns per campaign. */
function reachedByStage(funnel: Record<string, number> | undefined): number[] {
  const counts = PURCHASE_STAGES.map((s) => funnel?.[s] || 0);
  const reached: number[] = new Array(counts.length).fill(0);
  for (let i = counts.length - 1; i >= 0; i--) reached[i] = counts[i] + (reached[i + 1] || 0);
  return reached;
}

/** §5.7 funnel drop-off, store-scoped: for each campaign, how many journeys have
 *  reached each stage, with the biggest drop flagged. */
function FunnelDropOff({ campaigns }: { campaigns: any[] }) {
  const rows = campaigns.filter((c) => c.funnel);
  if (!rows.length) return null;
  return (
    <Card title="Funnel drop-off by campaign">
      <div className="overflow-x-auto">
        <table className="compare-table">
          <thead>
            <tr>
              <th scope="col">Campaign</th>
              {PURCHASE_STAGES.map((s) => <th key={s} scope="col">{stageLabel(s)}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => {
              const reached = reachedByStage(c.funnel);
              // biggest relative drop between adjacent stages (ignoring empty tails)
              let worst = -1; let worstRate = 0;
              for (let i = 0; i + 1 < reached.length; i++) {
                if (!reached[i]) continue;
                const rate = (reached[i] - reached[i + 1]) / reached[i];
                if (rate > worstRate) { worstRate = rate; worst = i + 1; }
              }
              return (
                <tr key={c.campaign_id}>
                  <td><b>{c.name}</b></td>
                  {reached.map((n, i) => (
                    <td key={PURCHASE_STAGES[i]} className={i === worst && worstRate > 0 ? "col-ice" : undefined}>
                      {n}{i === worst && worstRate > 0 && (
                        <span className="dim text-[.65rem]"> ▾{Math.round(worstRate * 100)}%</span>
                      )}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="dim text-xs mt-2">Journeys that reached each stage (this store&apos;s journeys only); ▾ marks the largest drop-off.</p>
    </Card>
  );
}

/** A5 budget-shift recommendation (§5.7 "shift $X from A to B, expected +Y
 *  deliveries"). The designed route is A5's network view (/marketing/campaigns);
 *  it is role-gated to OEM/marketing readers, so this section hides quietly for
 *  store roles the server refuses — no store-scoped A5 endpoint exists yet. */
function A5ShiftRecommendation() {
  const { persona } = usePersona();
  // GET /marketing/campaigns is OEM_VIEW_ROLES only; store roles skip the call instead of eating a 403
  const canRead = Boolean(persona?.isOem || persona?.role === "platform_admin");
  const q = useApi<any>(canRead ? "/marketing/campaigns" : null);
  const s = q.data?.budget_shift;
  if (q.error || !s?.recommended) return null;
  const r = s.recommendation || s;
  return (
    <AiInsight title="Campaign Agent recommendation · budget shift (network level)" sources={[r.source_ref || s.source_ref]}>
      Shift <b><Money value={r.shift_amount} /></b> from <b>{r.from_campaign}</b> (ROI {r.from_roi}) to{" "}
      <b>{r.to_campaign}</b> (ROI {r.to_roi})
      {r.expected_extra_deliveries != null && <> — expected <b>+{r.expected_extra_deliveries} deliveries</b></>}
      {r.expected_lift_gross != null && <> (≈<Money value={r.expected_lift_gross} /> gross, assumed per-delivery gross)</>}.
      {" "}Submitted for approval by marketing — autonomy {r.autonomy_level || "recommend"}.
    </AiInsight>
  );
}

export default function StoreCampaignsPage() {
  const { persona } = usePersona();
  const follow = useFollow();
  const dealer = useQueryParam("dealer");
  const q = useApi<any>(`/dealer/campaigns${dealer ? `?dealer=${encodeURIComponent(dealer)}` : ""}`);
  return (
    <MarketProvider marketId={q.data?.market_id || persona?.marketId}>
      <Page>
        <ViewHeader title="Campaigns (store)" subtitle="Which OEM campaigns are bringing buyers to this store — and what each delivery cost." />
        <Async q={q}>
          {(d: any) => (
            <>
              <ApiKpiGrid kpis={d.kpis} />
              <ApiInsight insight={d.insight} title="Campaign performance" />
              <A5ShiftRecommendation />
              <Card title="Campaign ROI for this store">
                <DataTable<any> rows={d.campaigns} rowKey={(c) => c.campaign_id} onRowClick={(c) => follow(c.link)}
                  empty="No OEM campaign journeys at this store yet."
                  initialSort={{ key: "roi", dir: "desc" }}
                  columns={[
                    { key: "n", header: "Campaign", sort: (c) => c.name,
                      render: (c) => <div><b>{c.name}</b><div className="dim text-xs">{titleCase(c.objective)} · {c.model_id}</div></div> },
                    { key: "j", header: "Journeys", num: true, sort: (c) => c.journeys, render: (c) => c.journeys },
                    { key: "sh", header: "Store share", num: true, render: (c) => <Pct value={c.share_of_campaign} /> },
                    { key: "sp", header: "Allocated spend", num: true, sort: (c) => c.allocated_spend, render: (c) => <Money value={c.allocated_spend} /> },
                    { key: "d", header: "Deliveries", num: true, sort: (c) => c.deliveries, render: (c) => c.deliveries },
                    { key: "cpd", header: "Cost / delivery", num: true, sort: (c) => c.cost_per_delivery, render: (c) => <Money value={c.cost_per_delivery} /> },
                    { key: "roi", header: "ROI", num: true, sort: (c) => c.roi,
                      render: (c) => <Badge tone={c.roi == null ? "gray" : c.roi >= 0 ? "green" : "red"}>{c.roi ?? "—"}</Badge> },
                  ]} />
                <p className="dim text-xs mt-3">
                  Assumptions: {d.assumptions.spend_allocation}; gross per delivery <Money value={d.assumptions.gross_per_delivery} /> (no per-deal gross in the lake).
                </p>
              </Card>
              <FunnelDropOff campaigns={d.campaigns || []} />
            </>
          )}
        </Async>
      </Page>
    </MarketProvider>
  );
}
