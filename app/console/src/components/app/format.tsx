"use client";

// Market-aware value renderers (§2.5, §5.11). Components render numbers through
// these — never with literal currency symbols or units (lint:units gate).

import { createContext, ReactNode, useContext } from "react";
import { fallbackMoneyConfig, fmtDistance, fmtEfficiency, fmtMoney, fmtNumber, MarketConfig, marketTimeZone, useMarket } from "@/lib/market";
import { usePersona } from "@/lib/persona";
import { humanizeAgents } from "@/lib/agents";

const MarketCtx = createContext<MarketConfig | null>(null);
// the market id a MarketProvider was given, known before its config has loaded
const MarketIdCtx = createContext<string | null>(null);

/** Provides the MarketConfig for a subtree (the journey's / customer's market). */
export function MarketProvider({ marketId, children }: { marketId?: string | null; children: ReactNode }) {
  const cfg = useMarket(marketId || null);
  return (
    <MarketIdCtx.Provider value={marketId || null}>
      <MarketCtx.Provider value={cfg}>{children}</MarketCtx.Provider>
    </MarketIdCtx.Provider>
  );
}
export const useMarketCtx = () => useContext(MarketCtx);

/** Money always reads as money: the loaded market config, else the provider's market
 *  currency (while /config/market is in flight or after it failed), never a bare number. */
export function Money({ value, decimals = 0, market }: { value?: number | null; decimals?: number; market?: MarketConfig | null }) {
  const ctx = useContext(MarketCtx);
  const marketId = useContext(MarketIdCtx);
  const cfg = market ?? ctx ?? fallbackMoneyConfig(marketId);
  if (value == null || Number.isNaN(Number(value))) return <>—</>;
  return <>{fmtMoney(cfg, Number(value), decimals)}</>;
}

export function Num({ value, decimals = 0 }: { value?: number | null; decimals?: number }) {
  const cfg = useContext(MarketCtx);
  if (value == null) return <>—</>;
  return <>{fmtNumber(cfg || ({ locale: "en-US" } as MarketConfig), Number(value), decimals)}</>;
}

export function Distance({ value }: { value?: number | null }) {
  const cfg = useContext(MarketCtx);
  if (value == null) return <>—</>;
  return <>{cfg ? fmtDistance(cfg, Number(value)) : fmtNumber({ locale: "en-US" } as MarketConfig, Number(value))}</>;
}

export function Efficiency({ value, powertrain }: { value?: number | null; powertrain: string }) {
  const cfg = useContext(MarketCtx);
  if (value == null || !cfg) return <>—</>;
  return <>{fmtEfficiency(cfg, Number(value), powertrain)}</>;
}

export function Pct({ value, decimals = 0 }: { value?: number | null; decimals?: number }) {
  if (value == null || Number.isNaN(Number(value))) return <>—</>;
  const v = Math.abs(Number(value)) <= 1.5 ? Number(value) * 100 : Number(value);
  return <>{v.toFixed(decimals)}%</>;
}

/** `timeZone`: show the time in a place's own zone (a store's opening hours read
 *  8:00 AM–4:00 PM wherever the browser is), else the browser's. */
export function DateTime({ value, mode = "datetime", timeZone }: { value?: string | null; mode?: "date" | "time" | "datetime"; timeZone?: string | null }) {
  const cfg = useContext(MarketCtx);
  const marketId = useContext(MarketIdCtx);
  const { persona } = usePersona();
  if (!value) return <>—</>;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return <>{value}</>;
  const base: Intl.DateTimeFormatOptions = mode === "date" ? { dateStyle: "medium" } : mode === "time" ? { timeStyle: "short" } : { dateStyle: "medium", timeStyle: "short" };
  // every screen shows a time in the store's zone (the market's), never the viewer's browser
  // zone — otherwise staff read 7:00 AM for the customer's 10:00 AM booking
  const zone = timeZone || marketTimeZone(marketId || persona?.marketId, cfg);
  let opts: Intl.DateTimeFormatOptions = { ...base, timeZone: zone };
  try { new Intl.DateTimeFormat(cfg?.locale || "en-US", opts); } catch { opts = base; }   // an unknown zone falls back
  return <time dateTime={value}>{new Intl.DateTimeFormat(cfg?.locale || "en-US", opts).format(d)}</time>;
}

/** A vehicle the way a person names it ("2026 Meridian Ion M"), falling back to
 *  the catalog id only when the API has no resolved name for it. */
export function vehicleLabel(v?: { year?: number | string | null; make?: string | null; model?: string | null; model_name?: string | null; model_id?: string | null } | null): string | null {
  if (!v) return null;
  const named = [v.year, v.make, v.model || v.model_name].filter(Boolean).join(" ").trim();
  return named || v.model_id || null;
}

/** Inline source citation chip for a number's `source_ref` (acceptance #4). */
// Readable labels for the source families a source_ref can point at. The raw ref stays in the
// tooltip; the chip reads as the provenance a person recognises, not an internal key.
const SOURCE_FAMILY: [RegExp, string][] = [
  [/^epa_fueleconomy/i, "EPA fuel economy"],
  [/^eea[_:]/i, "EEA CO₂ registrations"],
  [/^kba[_:]/i, "KBA registrations"],
  [/^nhtsa/i, "NHTSA"],
  [/^fueleconomy\.gov/i, "fueleconomy.gov"],
  [/^curated:/i, "Curated reference"],
  [/^synthetic:/i, "Synthetic data"],
  [/^F&I connector sandbox/i, "Lender rate card"],
  [/^Bedrock/i, "AI answer, grounded in the data shown"],
  [/sandbox valuation connector/i, "Appraisal guide comps"],
  [/valuation connector/i, "Appraisal guide"],
  [/^A4\.refresh_valuation/i, "Trade-In Agent re-valuation"],
  [/\(sandbox\)/i, "Sandbox"],
  [/^coverage_rule:/i, "Coverage rule"],
  [/^parts_catalog:/i, "Parts catalog"],
  [/^connector:/i, "Connector"],
  [/^spec:/i, "Spec"],
  [/^plan:/i, "Data plan"],
];

export function sourceLabel(refId: string): string {
  for (const [re, label] of SOURCE_FAMILY) {
    if (re.test(refId)) return label;
  }
  // OEM_Accounts:ACC-US-00001 → "Accounts · ACC-US-00001"; DEALER_LaborOps:LOP-BRAKE-F → "Labor ops · LOP-BRAKE-F"
  // ...and "DEALER_Journeys.stage/stalled/source" (a table + the fields read) → "Journeys"
  const m = /^(OEM|DEALER|CUST|PLAT)_([A-Za-z]+)(?:\.[\w/]+)?(?::(.+))?$/.exec(refId);
  if (m) {
    const table = m[2].replace(/([a-z])([A-Z])/g, (_s, a: string, b: string) => `${a} ${b}`).toLowerCase();
    const t = table.charAt(0).toUpperCase() + table.slice(1);
    // the record id after the colon says nothing to a reader (the full ref stays in the chip's title);
    // a descriptive tail ("stage/stalled/source") is kept
    const tail = m[3] && !/^[A-Z]{1,6}-[A-Z0-9-]+$/.test(m[3]) ? ` · ${m[3]}` : "";
    return `${t}${tail}`;
  }
  // agent codes to names, then tool / rule names as words: "A6.detect_alerts feed" →
  // "Field Sales Agent · detect alerts feed", "validate_handoff" → "validate handoff"; a table
  // name anywhere inside ("(OEM_Alerts; district-scoped)", "DEALER_Journeys.stage x source") reads
  // as the table's words ("alerts", "journeys stage x source")
  return humanizeAgents(refId)
    .replace(/\b(OEM|DEALER|CUST|PLAT)_([A-Z][A-Za-z]+)/g, (_s, _o: string, t: string) => t.replace(/([a-z])([A-Z])/g, (_m, a: string, b: string) => `${a} ${b}`).toLowerCase())
    // record ids inside a provenance chip say nothing to a reader; the full ref stays in the title
    .replace(/\s*[·:]?\s*\b(ACC|DIST|REG|CMP|INS|DOC|GR|EST|PO|SJ|RO|VAL|REC|J)-[A-Z0-9-]+\b/g, "")
    .replace(/\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/g, (w) => w.replace(/_/g, " "))
    .replace(/\(\s*\)/g, "").replace(/\s{2,}/g, " ").trim();
}

export function SourceRef({ refId }: { refId?: string | null }) {
  if (!refId) return null;
  const shown = sourceLabel(refId);
  return <span className="data-source" title={refId}>{shown.length > 30 ? `${shown.slice(0, 28)}…` : shown}</span>;
}
