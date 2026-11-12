// Thin client for the Retail Ops API (API Gateway). The console is a pure front end
// over the same routes the HTML screens and the demo harness use, so the
// migration doesn't change the backend contract at all.

export function apiBase(): string {
  // Runtime override via ?api=, else the build-time NEXT_PUBLIC_RETAIL_OPS_API.
  if (typeof window !== "undefined") {
    const q = new URLSearchParams(window.location.search).get("api");
    if (q) return q;
  }
  return process.env.NEXT_PUBLIC_RETAIL_OPS_API || "http://127.0.0.1:8080";
}

export function authToken(): string | null {
  if (typeof window !== "undefined") {
    // 1) a real Cognito session (JWT) is the primary credential.
    try {
      const raw = localStorage.getItem("retail-ops.session");
      if (raw) {
        const s = JSON.parse(raw);
        if (s?.idToken && (!s.expiresAt || s.expiresAt > Date.now())) return s.idToken;
      }
    } catch {
      /* fall through to dev token */
    }
    // 2) dev fallback: ?token=user:<id> or a dev token chosen on /login.
    const q = new URLSearchParams(window.location.search).get("token");
    if (q) return q;
    const dev = localStorage.getItem("retail-ops.devToken");
    if (dev) return dev;
  }
  return process.env.NEXT_PUBLIC_RETAIL_OPS_TOKEN || null;
}

let corrSeq = 0;

/** Admin "View as" selection: the role (or user_id) to impersonate for this
 *  browser. Server ignores it unless the real signed-in user is an admin. */
const ACT_AS_KEY = "retail-ops.actAs";
export function getActAs(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(ACT_AS_KEY);
}
export function setActAs(roleOrUserId: string | null): void {
  if (typeof window === "undefined") return;
  if (roleOrUserId) localStorage.setItem(ACT_AS_KEY, roleOrUserId);
  else localStorage.removeItem(ACT_AS_KEY);
}

async function headersAsync(): Promise<Record<string, string>> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  // refresh a native Cognito session that is about to expire (§8.1)
  const { validIdToken } = await import("./auth");
  const t = (await validIdToken()) || authToken();
  if (t) h["Authorization"] = `Bearer ${t}`;
  // Admin "View as persona": the server applies this only for admin callers.
  const actAs = getActAs();
  if (actAs) h["X-Act-As"] = actAs;
  // one correlation id per user action (§8, G-26)
  h["X-Correlation-Id"] = `ui-${Date.now().toString(36)}-${(corrSeq++).toString(36)}`;
  return h;
}

async function call<T>(method: string, path: string, payload?: unknown): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, {
    method, headers: await headersAsync(), cache: "no-store",
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error((body as any).error || (body as any).message || `${res.status}`) as Error & { status?: number; body?: any };
    err.status = res.status;
    err.body = body;
    throw err;
  }
  return body as T;
}

export const apiGet = <T = any>(path: string) => call<T>("GET", path);
export const apiPost = <T = any>(path: string, payload: unknown = {}) => call<T>("POST", path, payload);
export const apiPatch = <T = any>(path: string, payload: unknown = {}) => call<T>("PATCH", path, payload);
export const apiPut = <T = any>(path: string, payload: unknown = {}) => call<T>("PUT", path, payload);
export const apiDelete = <T = any>(path: string) => call<T>("DELETE", path);
