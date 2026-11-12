"use client";

// Discovery (A1) — spec §5.5: need statement + ≤4 intake questions, shortlist
// cards (same vehicle-item component), configurator side panel, and "Save build
// & hand off to dealer" (record_shortlist → PLAT_Configurations + build.saved,
// advances the journey out of DISCOVER/CONFIGURE when the gate allows).

import { useEffect, useMemo, useState } from "react";
import {
  Async, Btn, Card, Distance, Field, MarketProvider, Modal, Money, Page, PtChip, RecCard, SourceRef, VehicleItem,
  ViewHeader, VTag, useToast, stageLabel } from "@/components/app";
import { useApi, useMutation, useQueryParam } from "@/lib/hooks";

const QUESTION_LABEL: Record<string, string> = {
  household_size: "How many people ride with you most days?", commute_mi_per_day: "How far is your daily drive?",
  road_trip_frequency: "How often do you take road trips?", towing_or_hauling: "Do you tow or haul?",
  powertrain_openness: "Open to electric?", tech_affinity: "How important is the latest tech?",
  safety_priority: "How important are safety features?", ownership_style: "Buy or lease?",
  outdoor_activities: "Outdoor activities?",
};

/** Customer-facing preview (§5.5): the shortlist exactly as the customer would
 *  see it on /shop — friendly reasons, prices and availability only. Internal
 *  signals (score breakdowns, demand vs stock) are deliberately not shown. */
function CustomerPreview({ open, onClose, result }: { open: boolean; onClose: () => void; result: any }) {
  const matches: any[] = result?.matches || [];
  return (
    <Modal open={open} onClose={onClose} title="What the customer sees · Shop preview (read-only)">
      <div className="dim text-xs">The same Discovery Agent shortlist, rendered with the customer-facing fields only.</div>
      {matches.length === 0 && <div className="dim text-sm">No matches to preview yet.</div>}
      {matches.map((m: any, i: number) => (
        <VehicleItem key={m.trim_id} rank={i + 1}
          name={<>{m.trim_name || m.trim_id} <PtChip powertrain={m.powertrain} /></>}
          specs={<>
            <Money value={m.list_price} />
            {m.est_monthly_buy != null && <> · est. <Money value={m.est_monthly_buy} />/mo</>}
            {m.ev_range ? <> · <Distance value={m.ev_range} /> range</> : null}
          </>}
          tags={m.in_stock_count > 0
            ? <VTag>{m.in_stock_count} available nearby</VTag>
            : <VTag tone="yellow">build to order</VTag>} />
      ))}
      {result?.profile?.overall_confidence != null && (
        <div className="dim text-xs">
          Matched to the customer&apos;s stated needs; prices before taxes, fees and incentives.
        </div>
      )}
    </Modal>
  );
}

/** Demand signals (§5.5): how store stock and active-journey demand line up for
 *  the shortlisted models. Hidden quietly when the caller cannot read inventory. */
function DemandSignals({ matches }: { matches: any[] }) {
  const inv = useApi<any>(matches.length ? "/inventory" : null);
  if (!matches.length || inv.error || !inv.data) return null;
  const ids = new Set(matches.map((m: any) => m.model_id));
  const rows = (inv.data.by_model || []).filter((b: any) => ids.has(b.model_id));
  if (!rows.length) return null;
  return (
    <RecCard icon="📈" title="Demand signals · shortlisted models">
      {rows.map((b: any) => (
        <div key={b.model_id} className="cross-sell-row">
          <div className="flex flex-col gap-[2px] min-w-0">
            <span className="cross-sell-name">{b.model_id}</span>
            <span className="cross-sell-detail">{b.units} in stock · {b.aged_60} aged 60+ days</span>
          </div>
          <VTag tone={b.demand > b.units ? "red" : b.demand >= Math.max(1, b.units / 2) ? "yellow" : "blue"}>
            {b.demand} active {b.demand === 1 ? "journey wants" : "journeys want"} it
          </VTag>
        </div>
      ))}
      <div className="dim text-[.7rem] mt-1">Discovery Agent demand = active journeys shortlisting the model at this store.</div>
    </RecCard>
  );
}

export default function DiscoveryPage() {
  const toast = useToast();
  const qj = useQueryParam("journey");
  const [journeyId, setJourneyId] = useState("");
  useEffect(() => { if (qj) setJourneyId(qj); }, [qj]);
  const [need, setNeed] = useState("");
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [result, setResult] = useState<any>(null);
  const [model, setModel] = useState<string | null>(null);
  const [trim, setTrim] = useState<string | null>(null);
  const [pkgs, setPkgs] = useState<string[]>([]);
  const [preview, setPreview] = useState(false);
  const run = useMutation();
  const save = useMutation();
  const picker = useApi<any>(journeyId ? null : "/advisor/today?all=1");
  const opts = useApi<any>(model ? `/configurations/options?model=${encodeURIComponent(model)}` : null);
  const similar = useApi<any>(model ? `/configurations/similar?model=${encodeURIComponent(model)}` : null);
  const state = useApi<any>(journeyId ? `/journeys/${encodeURIComponent(journeyId)}/state` : null);

  const go = async () => {
    const out = await run.run("/discovery/shortlist", { journey_id: journeyId, need: need || undefined, answers });
    if (out) setResult(out); else toast(run.error || "Shortlist failed", "error");
  };
  useEffect(() => { if (journeyId) go(); }, [journeyId]); // eslint-disable-line react-hooks/exhaustive-deps

  const selectedTrim = useMemo(() => (opts.data?.trims || []).find((t: any) => t.trim_id === trim), [opts.data, trim]);
  const total = (selectedTrim?.list_price || 0) + (opts.data?.packages || []).filter((p: any) => pkgs.includes(p.trim_id))
    .reduce((s: number, p: any) => s + (p.list_price || 0), 0);

  const saveBuild = async () => {
    if (!model || !trim) return;
    const out = await save.run("/configurations", { journey_id: journeyId, model_id: model, trim_id: trim, packages: pkgs });
    if (out) {
      const adv = out.journey_advance;
      toast(adv?.to ? `Build saved — journey moved to ${adv.to}` : adv?.blocked ? `Build saved — ${adv.blocked}` : "Build saved", "success");
      state.reload();
    } else toast(save.error || "Save failed", "error");
  };

  const questions: string[] = result?.profile?.intake_questions || [];
  return (
    <MarketProvider marketId={result?.market_id}>
      <Page>
        <ViewHeader title="Discovery" subtitle="Turn the customer's lifestyle into a shortlist, then save the build and hand off." />
        {!journeyId ? (
          <Card title="Whose discovery?">
            <Async q={picker}>{(d) => (
              <select className="select max-w-md" onChange={(e) => setJourneyId(e.target.value)} defaultValue="" aria-label="Journey">
                <option value="" disabled>Select a customer…</option>
                {d.journeys.map((j: any) => <option key={j.journey_id} value={j.journey_id}>{j.customer_name} · {stageLabel(j.stage)} · {j.journey_id}</option>)}
              </select>
            )}</Async>
          </Card>
        ) : (
          <div className="grid gap-4 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_360px]">
            <div>
              <Card title={<>Lifestyle intake <span className="dim text-xs">· {journeyId} · {stageLabel(state.data?.stage || "")}</span></>}
                    actions={<Btn kind="ghost" size="sm" onClick={() => { setJourneyId(""); setResult(null); setModel(null); }}>Change customer</Btn>}>
                <Field label="What are they looking for? (need statement)">{(id) => (
                  <textarea id={id} className="textarea" value={need} onChange={(e) => setNeed(e.target.value)}
                    placeholder="e.g. family of four, weekend trips to the lake, wants to try electric" />)}</Field>
                {questions.slice(0, 4).map((k) => (
                  <Field key={k} label={QUESTION_LABEL[k] || k}>{(id) => (
                    <input id={id} className="input" value={answers[k] || ""} onChange={(e) => setAnswers({ ...answers, [k]: e.target.value })} />)}</Field>
                ))}
                <div className="flex items-center gap-3">
                  <Btn onClick={go} disabled={run.pending}>{run.pending ? "Ranking…" : "Update shortlist"}</Btn>
                  {result?.profile && <span className="dim text-xs">Profile confidence {Math.round((result.profile.overall_confidence || 0) * 100)}% · {questions.length ? `${Math.min(4, questions.length)} question(s) left` : "ready"}</span>}
                </div>
              </Card>
              <RecCard icon="🚗" title="Shortlist"
                       right={result?.matches?.length > 0 && (
                         <Btn kind="ghost" size="sm" onClick={() => setPreview(true)}>Preview as customer</Btn>)}>
                {!result && <div className="dim text-sm">Ranking…</div>}
                {result?.matches?.length === 0 && <div className="dim text-sm">Nothing in the catalog fits the stated budget.</div>}
                {(result?.matches || []).map((m: any, i: number) => (
                  <VehicleItem key={m.trim_id} rank={i + 1} name={<>{m.trim_name} <PtChip powertrain={m.powertrain} /></>}
                    specs={<><Money value={m.list_price} />{m.ev_range ? <> · <Distance value={m.ev_range} /></> : null} · why: {Object.entries(m.score_breakdown || {}).filter(([, v]) => (v as number) > 10).map(([k]) => k).join(", ")}</>}
                    tags={m.in_stock_count ? <VTag>{m.in_stock_count} in stock</VTag> : <VTag tone="yellow">order</VTag>}
                    action={<Btn kind={model === m.model_id && trim === m.trim_id ? "use" : "alt"} size="sm"
                                 onClick={() => { setModel(m.model_id); setTrim(m.trim_id); setPkgs([]); }}>Configure</Btn>} />
                ))}
              </RecCard>
              <DemandSignals matches={result?.matches || []} />
              <CustomerPreview open={preview} onClose={() => setPreview(false)} result={result} />
            </div>
            <aside className="card" aria-label="Configurator" style={{ alignSelf: "start", position: "sticky", top: 64 }}>
              <div className="card-title">Configurator</div>
              {!model ? <div className="dim text-sm">Pick a shortlist vehicle to configure.</div> : (
                <Async q={opts}>{(o) => (
                  <div className="flex flex-col gap-3">
                    <div className="font-semibold">{o.name} <span className="dim text-xs">{o.body}</span></div>
                    <Field label="Trim">{(id) => (
                      <select id={id} className="select" value={trim || ""} onChange={(e) => setTrim(e.target.value)}>
                        {o.trims.map((t: any) => <option key={t.trim_id} value={t.trim_id}>{t.trim_name} · {t.powertrain}</option>)}
                      </select>)}</Field>
                    {selectedTrim && <div className="text-sm"><Money value={selectedTrim.list_price} /> <SourceRef refId={selectedTrim.source_ref} /></div>}
                    <div className="section-label">Packages</div>
                    {o.packages.length === 0 && <div className="dim text-xs">No packages for this model.</div>}
                    {o.packages.map((p: any) => (
                      <label key={p.trim_id} className="toggle">
                        <input type="checkbox" checked={pkgs.includes(p.trim_id)}
                               onChange={(e) => setPkgs(e.target.checked ? [...pkgs, p.trim_id] : pkgs.filter((x) => x !== p.trim_id))} />
                        {p.package_name} · <Money value={p.list_price} />{p.addresses_need ? <span className="dim text-xs"> ({p.addresses_need.replace(/_/g, " ")})</span> : null}
                      </label>
                    ))}
                    <div className="savings-banner"><span className="sb-label">Build total (before tax & incentives)</span><span className="sb-value"><Money value={total} /></span></div>
                    <Btn onClick={saveBuild} disabled={save.pending || !trim}>{save.pending ? "Saving…" : "Save build & hand off to dealer"}</Btn>
                    {(similar.data?.similar || []).length > 0 && (
                      <div className="dim text-xs">Similar: {similar.data.similar.map((s: any) => s.name).join(", ")}</div>
                    )}
                  </div>
                )}</Async>
              )}
            </aside>
          </div>
        )}
      </Page>
    </MarketProvider>
  );
}
