// Resource URL contract (spec §5.18.2) — the TS twin of
// app/services/common/resource_url.py. Links in the console are minted ONLY
// here, and resolved to a screen by the API (GET /nav/resolve) for the caller's
// role, so a link can never route a user somewhere their role cannot open.

import { apiGet } from "./api";

export const RESOURCE_TYPES = [
  "journey", "customer", "dealer", "account", "deal", "alert", "task", "plan",
  "campaign", "vin", "appointment", "service_journey", "ro", "estimate", "approval",
] as const;
export type ResourceType = (typeof RESOURCE_TYPES)[number];

const CANON = /^\/r\/([a-z_]+)\/([^?#]+)(?:\?ctx=([A-Za-z0-9_\-.]+))?$/;

export function resourceUrl(type: ResourceType, id: string, ctx?: string): string {
  if (!RESOURCE_TYPES.includes(type)) throw new Error(`unknown resource type ${type}`);
  if (!id) throw new Error("resource id is required");
  const url = `/r/${type}/${encodeURIComponent(id)}`;
  return ctx ? `${url}?ctx=${ctx}` : url;
}

export function parseResourceUrl(url: string): { type: ResourceType; id: string; ctx?: string } | null {
  const m = CANON.exec(url || "");
  if (!m || !RESOURCE_TYPES.includes(m[1] as ResourceType)) return null;
  return { type: m[1] as ResourceType, id: decodeURIComponent(m[2]), ctx: m[3] };
}

export interface Resolution {
  allowed: boolean;
  url: string | null;
  screen: string | null;
  home?: string | null;
  reason?: string;
  context?: { because: string };
}

// Resolve a canonical URL (or type+id) to the caller's best permitted screen.
export async function resolve(urlOrType: string, id?: string): Promise<Resolution> {
  const parsed = id ? { type: urlOrType, id, ctx: undefined } : parseResourceUrl(urlOrType);
  if (!parsed) throw new Error(`not a canonical resource URL: ${urlOrType}`);
  const q = new URLSearchParams({ type: parsed.type, id: parsed.id });
  if (parsed.ctx) q.set("ctx", parsed.ctx);
  try {
    return await apiGet<Resolution>(`/nav/resolve?${q.toString()}`);
  } catch (e: any) {
    return { allowed: false, url: null, screen: null, reason: e?.message };
  }
}
