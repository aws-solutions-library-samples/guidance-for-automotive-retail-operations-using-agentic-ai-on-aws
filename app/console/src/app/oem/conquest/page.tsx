"use client";

// OEM Workspace · Conquest (design §4.1 intelligence): competitive pressure
// and market intelligence, mined from conquest_pressure alerts until the
// competitive-registrations read model lands.

import {
  AiInsight, Async, Badge, Btn, Card, DataTable, DateTime, EmptyState, Kpi, KpiGrid, Money, Num, useToast,
} from "@/components/app";
import { useApi, useMutation } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";
import { WorkspaceView, SEVERITY_TONE, sevClass } from "../_shared/WorkspaceView";

const ACTORS = ["oem_dm", "oem_regional"];

export default function OemConquest() {
  const toast = useToast();
  const { persona } = usePersona();
  const canAct = ACTORS.includes(persona?.role || "");
  const q = useApi<any>("/oem/alerts?status=all", { pollMs: 120000 });
  // Designed read model (§3.6.1 get_competitive_offers): render the competitor
  // offer table when the route lands; until then the page degrades to the
  // conquest_pressure alerts below.
  const offersQ = useApi<any>("/oem/conquest/offers");
  const offers = (offersQ.data?.offers || []) as any[];
  const patch = useMutation("PATCH");
  const act = async (a: any, action: "acknowledge" | "resolve") => {
    const out = await patch.run(`/oem/alerts/${encodeURIComponent(a.alert_id)}`, { action });
    if (out) { toast(`${a.title}: ${action}d`, "success"); q.reload(); }
    else toast(patch.error || "Update failed", "error");
  };
  return (
    <WorkspaceView view="conquest" title="Conquest"
      subtitle="Competitive registrations and market pressure across your district"
      prompts={["Where is conquest pressure strongest?", "What response should I prepare for the GM?"]}>
      <Async q={q} rows={6}>
        {(d) => {
          const all = (["HIGH", "MEDIUM", "LOW"] as const).flatMap((s) => (d.by_severity?.[s] || []));
          const conquest = all.filter((a: any) => a.kind === "conquest_pressure");
          const open = conquest.filter((a: any) => a.status !== "resolved");
          const dealers = new Set(open.map((a: any) => a.dealer_id));
          const kpis: Kpi[] = [
            { label: "Conquest signals", value: conquest.length, delta: "all time in scope" },
            { label: "Open", value: open.length, trend: open.length ? "down" : "up",
              delta: open.length ? "response needed" : "none open" },
            { label: "Dealers under pressure", value: dealers.size, delta: "with open conquest alerts" },
            { label: "HIGH severity", value: open.filter((a: any) => String(a.severity).toUpperCase() === "HIGH").length,
              trend: open.some((a: any) => String(a.severity).toUpperCase() === "HIGH") ? "down" : "up",
              delta: "prepare matching-offer request" },
          ];
          return (
            <>
              <KpiGrid items={kpis} />
              <AiInsight title="Competitive read" sources={["OEM_Alerts (conquest_pressure)", "market intelligence feed — pending"]}>
                <p>{open.length
                  ? `${open.length} open conquest signal(s) across ${dealers.size} dealer(s). Typical response: geo-boost the affected PMA, request a matching offer through C&I, and brief the GM with the registration trend before the weekend.`
                  : "No open conquest pressure right now. The competitive-registrations read model will add brand-level registration share when it lands; until then signals surface here from AI alerts."}</p>
              </AiInsight>
              <Card title="Competitor dealers near your stores"
                    actions={offers.length > 0 ? <Badge tone="gray">market-intel + ads connectors</Badge> : undefined}>
                {offers.length === 0 ? (
                  <EmptyState>
                    {offersQ.loading
                      ? "Checking the competitive-offers read model…"
                      : "The competitive-offers read model (advertised APR / lease / cash, est. spend, impressions vs our offer) is not deployed yet — conquest pressure surfaces from AI alerts below until it lands."}
                  </EmptyState>
                ) : (
                  <DataTable rows={offers} rowKey={(o: any) => o.offer_id || `${o.competitor_dealer}-${o.make}`}
                    initialSort={{ key: "spend", dir: "desc" }}
                    columns={[
                      { key: "comp", header: "Competitor dealer", sort: (o: any) => o.competitor_dealer, render: (o: any) => <b>{o.competitor_dealer}</b> },
                      { key: "make", header: "Make", render: (o: any) => o.make },
                      { key: "near", header: "Nearest our store", render: (o: any) => o.nearest_store || "—" },
                      { key: "offer", header: "Advertised offer", render: (o: any) => <span className="text-xs">{o.advertised_offer || "—"}</span> },
                      { key: "spend", header: "Est. monthly spend", num: true, sort: (o: any) => o.est_monthly_spend,
                        render: (o: any) => o.est_monthly_spend != null ? <Money value={o.est_monthly_spend} /> : "—" },
                      { key: "imp", header: "Impressions", num: true, sort: (o: any) => o.impressions,
                        render: (o: any) => o.impressions != null ? <Num value={o.impressions} /> : "—" },
                      { key: "vs", header: "vs our offer", render: (o: any) => o.vs_our_offer
                        ? <Badge tone={String(o.vs_our_offer).toLowerCase().includes("better") ? "red" : "green"}>{o.vs_our_offer}</Badge> : "—" },
                      { key: "exp", header: "Expires", sort: (o: any) => o.expires_at,
                        render: (o: any) => o.expires_at ? <DateTime value={o.expires_at} mode="date" /> : "—" },
                    ]} />
                )}
              </Card>
              <Card title="Conquest pressure by dealer">
                {open.length === 0 ? <EmptyState>No open conquest signals — competitive registrations for your PMA will appear here as the intelligence feed lands.</EmptyState> : (
                  open.map((a: any) => (
                    <div key={a.alert_id} className={`alert-item ${sevClass(String(a.severity).toUpperCase())} flex flex-wrap items-start justify-between gap-3`}>
                      <div className="min-w-0 flex-1">
                        <div className="alert-title"><Badge tone={SEVERITY_TONE[String(a.severity).toUpperCase()] || "blue"}>{String(a.severity).toUpperCase()}</Badge> {a.title}</div>
                        <div className="alert-desc"><b>{a.dealer_id}</b> · prepare conquest response (geo-boost / matching offer request)</div>
                        <div className="alert-time">Opened <DateTime value={a.opened_at} mode="date" /> · {a.status}</div>
                      </div>
                      {canAct && (
                        <div className="flex gap-2">
                          {a.status !== "acknowledged" && <Btn kind="secondary" size="sm" disabled={patch.pending} onClick={() => act(a, "acknowledge")}>Acknowledge</Btn>}
                          <Btn kind="primary" size="sm" disabled={patch.pending} onClick={() => act(a, "resolve")}>Resolve</Btn>
                        </div>
                      )}
                    </div>
                  ))
                )}
              </Card>
            </>
          );
        }}
      </Async>
    </WorkspaceView>
  );
}
