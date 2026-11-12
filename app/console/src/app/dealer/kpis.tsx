"use client";

// Store-ops helpers: render API KPI objects ({label, value, format, link, delta,
// trend, source_ref}) through the shared KpiGrid with market formatting, and
// follow their links — canonical /r/... links go through the role-aware
// resolver, screen paths navigate when the role can open that screen.

import { useRouter } from "next/navigation";
import { ReactNode } from "react";
import { AiInsight, Kpi, KpiGrid, Money, Pct, useToast } from "@/components/app";
import { agentLabel } from "@/lib/agents";
import { timeAgo } from "@/lib/hooks";
import { canOpen, screenForHref } from "@/lib/nav";
import { usePersona } from "@/lib/persona";
import { resolve } from "@/lib/resourceUrl";

export interface ApiKpi {
  label: string; value: any; link?: string | null; delta?: string | null; trend?: "up" | "down" | null;
  format?: "number" | "money" | "pct" | "text"; source_ref?: string | null;
}

export function fmtValue(k: ApiKpi): ReactNode {
  if (k.value == null) return "—";
  if (k.format === "money") return <Money value={Number(k.value)} />;
  if (k.format === "pct") return <Pct value={Number(k.value)} />;
  if (typeof k.value === "number") return Number.isInteger(k.value) ? k.value : k.value.toFixed(1);
  return String(k.value);
}

/** Whether a tile or row link leads somewhere this persona can open. A /r/ link
 *  is checked by the resolver when followed. A screen path needs the screen it
 *  opens, as in the sidebar (the inventory manager's Trade tab links to the
 *  Service Lane, which it cannot open); OEM roles keep their drill-downs, since
 *  the dealer read routes behind these links serve OEM read-across. */
export function useCanFollow() {
  const { persona } = usePersona();
  return (link?: string | null): boolean => {
    if (!link) return false;
    if (link.startsWith("/r/") || persona?.isOem) return true;
    const screen = screenForHref(link);
    return !screen || canOpen(persona?.screens, screen);
  };
}

export function useFollow() {
  const router = useRouter();
  const toast = useToast();
  const canFollow = useCanFollow();
  return async (link?: string | null) => {
    if (!link) return;
    if (link.startsWith("/r/")) {
      const r = await resolve(link);
      if (r.allowed && r.url) router.push(r.url);
      else toast(r.reason || "You can't open this item", "error");
    } else if (canFollow(link)) router.push(link);
    else toast("Not available for your role", "error");
  };
}

export function ApiKpiGrid({ kpis }: { kpis: ApiKpi[] }) {
  const follow = useFollow();
  const canFollow = useCanFollow();
  const items: Kpi[] = kpis.map((k) => ({
    label: k.label, value: fmtValue(k), delta: k.delta || undefined, trend: k.trend || undefined,
    onClick: canFollow(k.link) ? () => follow(k.link) : undefined,
  }));
  return <KpiGrid items={items} five={items.length === 5} />;
}

/** An API [AI INSIGHT]: figures-only text composed on request, or — when the
 *  nightly store digest wrote one — model text with its writer and time. */
export interface ApiInsightData {
  text: string; sources?: string[]; written_by?: string | null; written_at?: string | null;
}
/** The insight title, naming the agent that wrote it and when, if one did. */
export function insightTitle(title: string | undefined, insight?: ApiInsightData | null): string | undefined {
  if (!insight?.written_by) return title;
  const by = `written by the ${agentLabel(insight.written_by)}${insight.written_at ? ` ${timeAgo(insight.written_at)}` : ""}`;
  return title ? `${title} · ${by}` : by;
}
export function ApiInsight({ insight, title }: { insight?: ApiInsightData | null; title?: string }) {
  if (!insight?.text) return null;
  return <AiInsight title={insightTitle(title, insight)} sources={insight.sources}>{insight.text}</AiInsight>;
}

export const titleCase = (s?: string | null) => (s || "").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
