"use client";

// Customer Web brand scope (design §6, spec §5.2 "OEM brand (light)"). The
// Shell sets data-theme="brand"; this wrapper overrides the --brand-* tokens at
// runtime from OEM_Markets.brand (GET /config/market) for the customer's own
// market and provides the market formatters to the subtree. Customer-facing
// copy uses the full product name or the OEM brand — never internal shorthand.

import { CSSProperties, ReactNode, createContext, useContext } from "react";
import { MarketProvider } from "@/components/app";
import { useApi } from "@/lib/hooks";

export interface Brand {
  name?: string; short?: string; tagline?: string; captive_finance?: string;
  colors?: { primary?: string; accent?: string };
}
const BrandCtx = createContext<{ brand: Brand | null; marketId: string | null }>({ brand: null, marketId: null });
export const useBrand = () => useContext(BrandCtx);

/** The brand (or full product) name for customer-facing copy. */
export function useBrandName(): string {
  const { brand } = useBrand();
  return brand?.name || "Automotive Retail Operations";
}

export function BrandScope({ children }: { children: ReactNode }) {
  // the journey read is cheap and carries the customer's market
  const j = useApi<any>("/garage/journey");
  const marketId = j.data?.market_id || null;
  const m = useApi<any>(marketId ? `/config/market?market_id=${encodeURIComponent(marketId)}` : null);
  const brand: Brand | null = m.data?.brand || null;
  const style = brand?.colors
    ? ({ "--brand-primary": brand.colors.primary, "--brand-accent": brand.colors.accent } as CSSProperties)
    : undefined;
  return (
    <BrandCtx.Provider value={{ brand, marketId }}>
      <MarketProvider marketId={marketId}>
        <div style={style}>{children}</div>
      </MarketProvider>
    </BrandCtx.Provider>
  );
}
