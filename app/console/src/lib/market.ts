// Market formatters (spec §2.5) — the UI side of app/services/common/market.py.
// Components never hard-code a currency symbol or unit; they format through a
// MarketConfig fetched from GET /config/market (scripts/check-literals.mjs
// gates the source tree for literal units/currency).

import { useEffect, useState } from "react";
import { apiGet } from "./api";

export interface MarketConfig {
  market_id: string;
  locale: string;
  currency: string;
  currency_symbol: string;
  distance_unit: string;
  efficiency_unit_ice: string;
  efficiency_unit_ev: string;
  region_dimension?: string;
  list_price_includes_tax?: boolean;
  timezone_default?: string;
}

/** The zone every time in a market is shown in: the store's, whatever zone the viewer's
 *  browser is in, so staff and customer read the same "10:00 AM" for one booking. */
const DEFAULT_ZONE: Record<string, string> = { us: "America/New_York", de: "Europe/Berlin" };
// the signed-in persona's market (set by PersonaProvider): the default for any caller
// that has no record-level market in hand (toasts, chips, shell lists)
let activeMarket: string | null = null;
export function setActiveMarket(marketId: string | null | undefined): void { activeMarket = marketId || null; }
export function marketTimeZone(marketId?: string | null, cfg?: MarketConfig | null): string {
  const id = (marketId || cfg?.market_id || activeMarket || "us").toLowerCase();
  return cfg?.timezone_default || DEFAULT_ZONE[id] || DEFAULT_ZONE.us;
}

const cache = new Map<string, Promise<MarketConfig>>();

export function loadMarket(marketId: string): Promise<MarketConfig> {
  if (!cache.has(marketId)) {
    const p = apiGet<MarketConfig>(`/config/market?market_id=${encodeURIComponent(marketId)}`);
    // a failed load (a sign-in race, a blip) is not remembered: the next page retries,
    // instead of every page for that market rendering money as a bare number
    p.catch(() => cache.delete(marketId));
    cache.set(marketId, p);
  }
  return cache.get(marketId)!;
}

/** The currency a market's money is in, before (or without) its loaded config — so an
 *  amount never renders as a bare "505" while /config/market is in flight. */
const DEFAULT_CURRENCY: Record<string, { locale: string; currency: string }> = {
  us: { locale: "en-US", currency: "USD" }, de: { locale: "de-DE", currency: "EUR" },
};
export function fallbackMoneyConfig(marketId?: string | null): MarketConfig {
  const d = DEFAULT_CURRENCY[(marketId || "us").toLowerCase()] || DEFAULT_CURRENCY.us;
  return { market_id: (marketId || "us").toLowerCase(), locale: d.locale, currency: d.currency, currency_symbol: "",
           distance_unit: "", efficiency_unit_ice: "", efficiency_unit_ev: "", timezone_default: marketTimeZone(marketId) };
}

export function useMarket(marketId: string | null | undefined): MarketConfig | null {
  const [cfg, setCfg] = useState<MarketConfig | null>(null);
  useEffect(() => {
    if (!marketId) return;
    let live = true;
    loadMarket(marketId).then((c) => live && setCfg(c)).catch(() => live && setCfg(null));
    return () => { live = false; };
  }, [marketId]);
  return cfg;
}

export function fmtMoney(cfg: MarketConfig, value: number, decimals = 0): string {
  return new Intl.NumberFormat(cfg.locale, {
    style: "currency", currency: cfg.currency,
    minimumFractionDigits: decimals, maximumFractionDigits: decimals,
  }).format(value);
}

export function fmtNumber(cfg: MarketConfig, value: number, decimals = 0): string {
  return new Intl.NumberFormat(cfg.locale, {
    minimumFractionDigits: decimals, maximumFractionDigits: decimals,
  }).format(value);
}

export function fmtDistance(cfg: MarketConfig, value: number): string {
  return `${fmtNumber(cfg, value)} ${cfg.distance_unit}`;
}

export function fmtEfficiency(cfg: MarketConfig, value: number, powertrain: string): string {
  const ev = ["ev", "bev", "electric"].includes((powertrain || "").toLowerCase());
  const unit = ev ? cfg.efficiency_unit_ev : cfg.efficiency_unit_ice;
  return `${fmtNumber(cfg, value, unit.includes("/100") ? 1 : 0)} ${unit}`;
}

/** "14:05" (or "14:05:09") in the market's zone, with the date when it is not today
 *  there — for logs and chips that show a bare clock time. */
export function fmtClock(ts: string | undefined | null, marketId?: string | null, withSeconds = false): string {
  if (!ts) return "";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return String(ts).slice(0, withSeconds ? 19 : 16).replace("T", " ");
  const timeZone = marketTimeZone(marketId);
  const time = d.toLocaleTimeString([], { timeZone, hour: "2-digit", minute: "2-digit", hour12: false,
                                          ...(withSeconds ? { second: "2-digit" } : {}) });
  const day = (x: Date) => x.toLocaleDateString("en-CA", { timeZone });
  if (day(d) === day(new Date())) return time;
  return `${d.toLocaleDateString([], { timeZone, day: "numeric", month: "short" })} ${time}`;
}

/** "Nov 5, 10:00 AM" in the market's zone — for toasts and sentences. */
export function fmtStamp(ts: string | undefined | null, marketId?: string | null): string {
  if (!ts) return "";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return String(ts);
  return d.toLocaleString([], { timeZone: marketTimeZone(marketId), month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function zoneOffsetMs(utcMs: number, timeZone: string): number {
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone, hourCycle: "h23", year: "numeric", month: "2-digit",
    day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }).formatToParts(new Date(utcMs)).map((x) => [x.type, x.value]));
  return Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second) - utcMs;
}

/** A stored UTC time as a datetime-local value ("2026-11-05T10:00") on the STORE's clock. */
export function toZoneInput(ts: string | undefined | null, marketId?: string | null): string {
  if (!ts) return "";
  const ms = Date.parse(ts);
  if (Number.isNaN(ms)) return "";
  return new Date(ms + zoneOffsetMs(ms, marketTimeZone(marketId))).toISOString().slice(0, 16);
}

/** A datetime-local value read as the STORE's wall-clock time → ISO UTC ("…Z"). A browser
 *  in another zone picking 10:00 means 10:00 at the dealership, not 10:00 where it is. */
export function fromZoneInput(local: string, marketId?: string | null): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(local || "");
  if (!m) return new Date(local).toISOString();
  const wall = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
  const zone = marketTimeZone(marketId);
  let utc = wall - zoneOffsetMs(wall, zone);
  utc = wall - zoneOffsetMs(utc, zone);            // second pass settles a DST edge
  return new Date(utc).toISOString().replace(/\.\d{3}Z$/, "Z");
}

