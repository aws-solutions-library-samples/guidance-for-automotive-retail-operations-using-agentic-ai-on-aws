"use client";

// OEM Workspace · Scenario Planning (design §4.3): 4 keyboard-adjustable
// sliders with value text (Allocation −50…+50%, Incentive/Unit −$500…+$2,000,
// Days Supply ±15, Market Growth −5…+10%) → Projected Impact cards whose
// "estimate" disclaimer is programmatically associated via aria-describedby.
// Deterministic sandbox model (POST /oem/scenario); GR-001/005 apply — nothing
// here changes an allocation or incentive budget.

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AiInsight, Badge, Btn, Card, ErrorState, Kpi, KpiGrid, LoadingBlock, Money, Num, Slider,
} from "@/components/app";
import { apiPost } from "@/lib/api";
import { useMarket, fmtMoney } from "@/lib/market";
import { usePersona } from "@/lib/persona";
import { WorkspaceView } from "../_shared/WorkspaceView";

const DEFAULTS = { allocation_pct: 0, incentive_per_unit: 0, days_supply_delta: 0, market_growth_pct: 0 };
const CLAMP: Record<keyof typeof DEFAULTS, [number, number]> = {
  allocation_pct: [-50, 50], incentive_per_unit: [-500, 2000], days_supply_delta: [-15, 15], market_growth_pct: [-5, 10],
};

/** Levers pre-set from the URL (`?allocation=15&incentive=500&supply=-5&growth=2`), so a deep link — the demo
 *  replay's "Run scenario" step, a saved view — opens on the modelled scenario instead of the zero baseline. */
function leversFromQuery(): typeof DEFAULTS {
  if (typeof window === "undefined") return DEFAULTS;
  const q = new URLSearchParams(window.location.search);
  const pick = (key: keyof typeof DEFAULTS, ...names: string[]) => {
    for (const n of names) {
      const raw = q.get(n);
      if (raw === null || raw === "") continue;
      const v = Number(raw);
      if (!Number.isFinite(v)) continue;
      const [lo, hi] = CLAMP[key];
      return Math.min(hi, Math.max(lo, v));
    }
    return DEFAULTS[key];
  };
  return {
    allocation_pct: pick("allocation_pct", "allocation", "allocation_pct"),
    incentive_per_unit: pick("incentive_per_unit", "incentive", "incentive_per_unit"),
    days_supply_delta: pick("days_supply_delta", "supply", "days_supply_delta"),
    market_growth_pct: pick("market_growth_pct", "growth", "market_growth_pct"),
  };
}
const LABEL: Record<string, string> = {
  volume: "Volume", objective_pct: "Objective achievement", market_share: "Market share",
  gross_per_unit: "Gross per unit", days_supply: "Days supply",
};
const DISCLAIMER_ID = "scenario-estimate-disclaimer";

export default function OemScenario() {
  const { persona } = usePersona();
  const market = useMarket(persona?.marketId);
  const district = persona?.scopeNodeId && persona.scopeNodeId.startsWith("DIST") ? persona.scopeNodeId : "DIST-07";
  const [lv, setLv] = useState(DEFAULTS);
  const [out, setOut] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const seq = useRef(0);

  // read deep-link levers after mount (window is not available during SSR)
  useEffect(() => { const q = leversFromQuery(); if (q !== DEFAULTS) setLv(q); }, []);

  useEffect(() => {
    const n = ++seq.current;
    setBusy(true);
    const t = setTimeout(() => {
      apiPost<any>("/oem/scenario", { district_id: district, levers: lv })
        .then((d) => { if (n === seq.current) { setOut(d); setErr(null); } })
        .catch((e) => n === seq.current && setErr(e.message))
        .finally(() => n === seq.current && setBusy(false));
    }, 250);
    return () => clearTimeout(t);
  }, [lv, district]);

  const fmt = (p: any, v: number) => {
    if (p.metric === "gross_per_unit") return <Money value={v} market={market} />;
    if (p.metric === "objective_pct" || p.metric === "market_share") return `${(v * 100).toFixed(1)}%`;
    if (p.metric === "volume") return <Num value={v} />;
    return v.toFixed(1);
  };
  const narrative = useMemo(() => {
    if (!out) return null;
    const p = Object.fromEntries((out.projections || []).map((x: any) => [x.metric, x]));
    const parts = [];
    if (p.volume) parts.push(`volume ${p.volume.delta >= 0 ? "+" : ""}${p.volume.delta.toFixed(0)} units`);
    if (p.gross_per_unit) parts.push(`gross per unit ${p.gross_per_unit.delta >= 0 ? "+" : ""}${market ? fmtMoney(market, p.gross_per_unit.delta) : p.gross_per_unit.delta.toFixed(0)}`);
    if (p.days_supply) parts.push(`days supply ${p.days_supply.delta >= 0 ? "+" : ""}${p.days_supply.delta.toFixed(1)}`);
    return `With these levers the model projects ${parts.join(", ")}. Treat as an estimate: allocation and incentive changes still go through C&I planning and approval.`;
  }, [out, market]);

  const baselineKpis: Kpi[] = (out?.projections || []).slice(0, 4).map((p: any) => ({
    label: `${LABEL[p.metric] || p.metric} (base)`,
    value: fmt(p, p.baseline),
    delta: "current baseline",
  }));

  return (
    <WorkspaceView view="scenario" title="Scenario Planning" subtitle={`District ${district} · deterministic elasticity model`}
      prompts={["What lever moves volume most?", "Which dealers carry the highest days supply?"]}
      actions={<><Badge tone="orange">estimate</Badge><Btn kind="secondary" size="sm" onClick={() => setLv(DEFAULTS)}>Reset</Btn></>}>
      {baselineKpis.length === 4
        ? <KpiGrid items={baselineKpis} />
        : <KpiGrid items={[
            { label: "District", value: district, delta: "scenario scope" },
            { label: "Levers", value: 4, delta: "allocation · incentive · supply · growth" },
            { label: "Model", value: "Deterministic", delta: "sandbox elasticity" },
            { label: "Status", value: busy ? "Running…" : "Ready", delta: "projections are estimates" },
          ]} />}
      <div className="grid gap-4 xl:grid-cols-[minmax(0,360px)_1fr]">
        <Card title="Scenario builder">
          <Slider label="Allocation change" value={lv.allocation_pct} min={-50} max={50}
                  onChange={(v) => setLv({ ...lv, allocation_pct: v })} format={(v) => `${v > 0 ? "+" : ""}${v}%`} />
          <Slider label="Incentive per unit" value={lv.incentive_per_unit} min={-500} max={2000} step={50}
                  onChange={(v) => setLv({ ...lv, incentive_per_unit: v })} format={(v) => (market ? fmtMoney(market, v) : String(v))} />
          <Slider label="Days supply" value={lv.days_supply_delta} min={-15} max={15}
                  onChange={(v) => setLv({ ...lv, days_supply_delta: v })} format={(v) => `${v > 0 ? "+" : ""}${v} days`} />
          <Slider label="Market growth" value={lv.market_growth_pct} min={-5} max={10}
                  onChange={(v) => setLv({ ...lv, market_growth_pct: v })} format={(v) => `${v > 0 ? "+" : ""}${v}%`} />
          <p className="dim text-xs mt-2">Sliders are keyboard-adjustable (arrow keys) and announce their value.</p>
        </Card>
        <div aria-live="polite" aria-busy={busy}>
          {err && <ErrorState error={err} />}
          {!out && !err && <LoadingBlock rows={5} />}
          {out && (
            <>
              <p id={DISCLAIMER_ID} className="dim text-xs mb-2">
                Projected impact is an <b>estimate</b> from a deterministic elasticity model — not a commitment.
                Allocation and incentive changes still go through C&amp;I planning and approval (the wholesale-reduction and allocation-promise guardrails).
              </p>
              <div className="kpi-grid five">
                {out.projections.map((p: any) => (
                  <div key={p.metric} className="kpi-card" aria-describedby={DISCLAIMER_ID}>
                    <div className="kpi-value">{fmt(p, p.projected)}</div>
                    <div className="kpi-label">{LABEL[p.metric] || p.metric}</div>
                    <div className={`kpi-delta ${p.metric === "days_supply" ? (p.delta <= 0 ? "up" : "down") : (p.delta >= 0 ? "up" : "down")}`}>
                      {p.delta >= 0 ? "▲ +" : "▼ "}{fmt(p, p.delta)} vs base {fmt(p, p.baseline)}
                    </div>
                    <div className="mt-1"><Badge tone="orange">estimate</Badge></div>
                  </div>
                ))}
              </div>
              <AiInsight title="Scenario planning guidance" sources={[out.source_ref]}><p>{narrative}</p></AiInsight>
            </>
          )}
        </div>
      </div>
    </WorkspaceView>
  );
}
