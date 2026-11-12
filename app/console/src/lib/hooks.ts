"use client";

// Data hooks shared by every screen: GET with reload/polling, mutations with
// toasts, and route-id resolution that works under static export.

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { apiBase, apiGet, apiPatch, apiPost } from "./api";

export interface Query<T> { data: T | null; error: string | null; loading: boolean; reload: () => Promise<void> }

/** Dispatched on window when a page's GET is refused (403). The Shell sends the
 *  user home when the page is outside the persona's screens (spec §5.18.1). */
export const FORBIDDEN_EVENT = "retail-ops:forbidden";

/** GET `path` (null = skip). `pollMs` re-fetches on an interval (read models are
 *  event-materialized server-side; polling only picks up new state).
 *  `background` marks a call made by shared chrome (agent panel, logs) rather
 *  than by the page itself: its 403 never sends the user home. */
export function useApi<T = any>(path: string | null, opts: { pollMs?: number; background?: boolean } = {}): Query<T> {
  // Data is kept with the path it was fetched for: after the path changes (a tab switch,
  // a new id) the previous path's body is never handed to the page as the new one's.
  // Rendering the Funnel tab's body as the Sales tab crashed on `d.by_month.map`.
  const [state, setState] = useState<{ path: string | null; data: T | null }>({ path: null, data: null });
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(path));
  const live = useRef(true);
  const current = useRef(path);
  current.current = path;
  const background = Boolean(opts.background);

  const reload = useCallback(async () => {
    if (!path) { setLoading(false); return; }
    setLoading(true);
    setError(null);
    try {
      const d = await apiGet<T>(path);
      // a response for a path the page has since left (a slow earlier tab) is dropped
      if (live.current && current.current === path) setState({ path, data: d });
    } catch (e: any) {
      if (live.current && current.current === path) {
        // the person reads what went wrong; the request detail is for the console, not the screen
        console.error(`GET ${path} @ ${apiBase()}:`, e);
        setError(String(e?.message || e));
      }
      if (live.current && e?.status === 403 && !background) {
        window.dispatchEvent(new CustomEvent(FORBIDDEN_EVENT, { detail: { path } }));
      }
    } finally {
      if (live.current && current.current === path) setLoading(false);
    }
  }, [path, background]);
  const data = state.path === path ? state.data : null;

  useEffect(() => { live.current = true; reload(); return () => { live.current = false; }; }, [reload]);
  useEffect(() => {
    if (!opts.pollMs || !path) return;
    const t = setInterval(() => { if (document.visibilityState === "visible") reload(); }, opts.pollMs);
    return () => clearInterval(t);
  }, [opts.pollMs, path, reload]);

  // no body yet for THIS path (just switched) reads as loading, not as an empty page
  const pending = Boolean(path) && state.path !== path && !error;
  return { data, error, loading: loading || pending, reload };
}

type Method = "POST" | "PATCH";
/** A write action with pending/error state. Returns the response body. */
/** A page action an agent may have traced (draft, send, assign, book…) just finished:
 *  the Agent bar's Activity feed reloads now instead of on its next 20 s poll. */
export const AGENT_ACTIVITY_EVENT = "retail-ops:agent-activity";
export function notifyAgentActivity(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(AGENT_ACTIVITY_EVENT));
}

export function useMutation<B = any, R = any>(method: Method = "POST") {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(async (path: string, body?: B): Promise<R | null> => {
    setPending(true);
    setError(null);
    try {
      const out = await (method === "PATCH" ? apiPatch<R>(path, body ?? {}) : apiPost<R>(path, body ?? {}));
      notifyAgentActivity();
      return out;
    } catch (e: any) {
      setError(e?.message || String(e));
      return null;
    } finally {
      setPending(false);
    }
  }, [method]);
  return { run, pending, error, setError };
}

/**
 * The `{id}` segment of a dynamic route. Under static export every dynamic page
 * is generated once with the placeholder id "_" and CloudFront rewrites
 * `/dealer/advisor/<id>` to it, so the real id is read from the browser path.
 * `segment` is the 0-based index of the id in the path (e.g. 2 for
 * /dealer/advisor/J-1).
 */
export function useRouteId(segment: number): string {
  const pathname = usePathname() || "";
  const [id, setId] = useState("");
  useEffect(() => {
    const parts = (typeof window !== "undefined" ? window.location.pathname : pathname)
      .split("/").filter(Boolean);
    setId(decodeURIComponent(parts[segment] || ""));
  }, [pathname, segment]);
  return id === "_" ? "" : id;
}

export function useQueryParam(name: string): string | null {
  const [v, setV] = useState<string | null>(null);
  useEffect(() => { setV(new URLSearchParams(window.location.search).get(name)); }, [name]);
  return v;
}

/** Elapsed mm:ss since `startMs` (Advisor live-session timer, AR-8). */
export function useElapsed(startMs: number | null): string {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  if (!startMs) return "00:00";
  const s = Math.max(0, Math.floor((now - startMs) / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

export function timeAgo(ts?: string | null): string {
  if (!ts) return "";
  const d = Date.parse(ts);
  if (Number.isNaN(d)) return ts;
  const s = Math.round((Date.now() - d) / 1000);
  const abs = Math.abs(s);
  const fmt = (n: number, u: string) => `${n}${u}${s < 0 ? " from now" : " ago"}`;
  if (abs < 60) return s < 0 ? "soon" : "just now";
  if (abs < 3600) return fmt(Math.round(abs / 60), "m");
  if (abs < 86400) return fmt(Math.round(abs / 3600), "h");
  return fmt(Math.round(abs / 86400), "d");
}
