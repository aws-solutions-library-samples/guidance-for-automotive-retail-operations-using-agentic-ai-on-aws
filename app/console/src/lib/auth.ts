// Real Cognito auth for the console (spec §8.1). Signs the user in against the
// Retail Ops staff user pool with USER_PASSWORD_AUTH, stores the returned IdToken
// (a verifiable JWT), and exposes it to the API client as a Bearer token. The
// API's custom authorizer verifies the JWT and maps the username to the
// caller's PLAT_Users scope — the same resolution path the dev token used, now
// backed by a real credential.
//
// Calls the public Cognito IDP JSON endpoint directly (no SDK) so the static
// export stays dependency-free. Tokens live in localStorage for the session.

const REGION = process.env.NEXT_PUBLIC_COGNITO_REGION || "us-east-1";
const CLIENT_ID = process.env.NEXT_PUBLIC_COGNITO_CLIENT_ID || "";
// SSO (Hosted UI) runs on its own app client of the same pool — the one with the
// OAuth code flow and callback URLs registered. Falls back to the native client
// so a single-client pool keeps working.
export const HOSTED_UI_CLIENT_ID =
  process.env.NEXT_PUBLIC_COGNITO_HOSTED_UI_CLIENT_ID || CLIENT_ID;
const IDP = `https://cognito-idp.${REGION}.amazonaws.com/`;
const STORE_KEY = "retail-ops.session";

export interface Session {
  idToken: string;
  username: string;
  expiresAt: number; // epoch ms
  // Mandated short lifetimes (§8.1): id token 1 h, refresh token 10 h. Both
  // sign-in paths store the refresh token; `federated` selects which app client
  // it must be redeemed against.
  refreshToken?: string;
  federated?: boolean;            // true = minted by the Hosted-UI (SSO) client
}

function decodeJwt(jwt: string): Record<string, any> {
  try {
    const payload = jwt.split(".")[1];
    const json = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
    return JSON.parse(json);
  } catch {
    return {};
  }
}

export function getSession(): Session | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (!raw) return null;
    const s = JSON.parse(raw) as Session;
    if (!s.idToken || s.expiresAt < Date.now()) {
      localStorage.removeItem(STORE_KEY);
      return null;
    }
    return s;
  } catch {
    return null;
  }
}

/** True when a Cognito session is stored at all — including one whose id token
 *  has lapsed but is refresh-capable (validIdToken() will renew it on the next
 *  API call). Use this, not getSession(), to decide "am I in Cognito mode":
 *  getSession()'s strict expiry check says null during the refresh window
 *  while requests still go out under the refreshed JWT. */
export function hasCognitoSession(): boolean {
  if (typeof window === "undefined") return false;
  try { return Boolean(JSON.parse(localStorage.getItem(STORE_KEY) || "null")?.idToken); }
  catch { return false; }
}

export function authConfigured(): boolean {
  return Boolean(CLIENT_ID);
}

export async function signIn(username: string, password: string): Promise<Session> {
  if (!CLIENT_ID) throw new Error("Cognito client id not configured (NEXT_PUBLIC_COGNITO_CLIENT_ID)");
  const res = await fetch(IDP, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-amz-json-1.1",
      "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
    },
    body: JSON.stringify({
      AuthFlow: "USER_PASSWORD_AUTH",
      ClientId: CLIENT_ID,
      AuthParameters: { USERNAME: username, PASSWORD: password },
    }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = body?.message || body?.__type || `sign-in failed (${res.status})`;
    throw new Error(msg);
  }
  const auth = body.AuthenticationResult;
  if (!auth?.IdToken) {
    // e.g. a challenge (NEW_PASSWORD_REQUIRED). Surface it rather than hang.
    throw new Error(body.ChallengeName
      ? `sign-in requires: ${body.ChallengeName}`
      : "no token returned");
  }
  const claims = decodeJwt(auth.IdToken);
  const session: Session = {
    idToken: auth.IdToken,
    username: claims["cognito:username"] || claims["username"] || username,
    expiresAt: (claims.exp ? claims.exp * 1000 : Date.now() + 3600_000),
    refreshToken: auth.RefreshToken,
  };
  localStorage.setItem(STORE_KEY, JSON.stringify(session));
  return session;
}

/** Refresh the id token with the refresh token.
 *
 *  ID tokens live 1 h and refresh tokens 10 h (the mandated short lifetimes), so
 *  a session that outlasts an hour depends on this path. The refresh token is
 *  bound to the app client that issued it — the Hosted-UI client for an SSO
 *  session, the native client otherwise — so it must be redeemed with that same
 *  client id or Cognito rejects it. */
export async function refreshSession(): Promise<Session | null> {
  const raw = typeof window !== "undefined" ? localStorage.getItem(STORE_KEY) : null;
  if (!raw) return null;
  const s = JSON.parse(raw) as Session;
  const clientId = s.federated ? HOSTED_UI_CLIENT_ID : CLIENT_ID;
  if (!clientId || !s.refreshToken) return null;
  const res = await fetch(IDP, {
    method: "POST",
    headers: { "Content-Type": "application/x-amz-json-1.1",
               "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth" },
    body: JSON.stringify({ AuthFlow: "REFRESH_TOKEN_AUTH", ClientId: clientId,
                           AuthParameters: { REFRESH_TOKEN: s.refreshToken } }),
  });
  const body = await res.json().catch(() => ({}));
  const tok = body?.AuthenticationResult?.IdToken;
  if (!res.ok || !tok) { signOut(); return null; }
  const claims = decodeJwt(tok);
  const next: Session = { ...s, idToken: tok, expiresAt: claims.exp ? claims.exp * 1000 : Date.now() + 3600_000 };
  localStorage.setItem(STORE_KEY, JSON.stringify(next));
  return next;
}

/** A valid id token, refreshing a native session that is about to expire. */
export async function validIdToken(): Promise<string | null> {
  if (typeof window === "undefined") return null;
  const raw = localStorage.getItem(STORE_KEY);
  if (!raw) return null;
  const s = JSON.parse(raw) as Session;
  if (s.expiresAt - Date.now() > 120_000) return s.idToken;
  const r = await refreshSession().catch(() => null);
  return r?.idToken || null;
}

/** SSO via the Cognito Hosted UI: exchange the auth code for tokens (public
 *  client, no secret). The id token lives 1 h; the returned refresh token is
 *  kept so `refreshSession()` can carry the session to the 10 h refresh limit
 *  without bouncing the user back through the IdP. */
export async function completeFederatedSignIn(code: string, redirectUri: string): Promise<Session> {
  const base = (process.env.NEXT_PUBLIC_COGNITO_HOSTED_UI || "").replace(/\/$/, "");
  if (!base || !HOSTED_UI_CLIENT_ID) throw new Error("Hosted UI not configured");
  const res = await fetch(`${base}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "authorization_code", client_id: HOSTED_UI_CLIENT_ID, code, redirect_uri: redirectUri }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok || !body.id_token) throw new Error(body.error_description || body.error || `token exchange failed (${res.status})`);
  const claims = decodeJwt(body.id_token);
  const session: Session = {
    idToken: body.id_token,
    username: claims["cognito:username"] || claims["preferred_username"]
              || claims["username"] || claims["email"],
    expiresAt: claims.exp ? claims.exp * 1000 : Date.now() + 3600_000,
    refreshToken: body.refresh_token,
    federated: true,
  };
  localStorage.setItem(STORE_KEY, JSON.stringify(session));
  return session;
}

/** Local development only: a plain dev token (`user:<id>` / `cust:<id>`) the dev
 *  authorizer accepts only when RETAIL_OPS_ALLOW_DEV_TOKEN=1 (local/dev; must be off in demo/prod). */
export function signInDevToken(token: string): void {
  localStorage.setItem("retail-ops.devToken", token);
}

/** Forget every identity this browser holds: the Cognito session, a dev token, and an
 *  admin's "View as persona" choice. Leaving the View-as key behind made the next
 *  sign-in (as someone else) still act as the old persona, so Sign out looked like it
 *  had not happened. */
export function signOut(): void {
  if (typeof window === "undefined") return;
  for (const k of [STORE_KEY, "retail-ops.devToken", "retail-ops.actAs"]) localStorage.removeItem(k);
  sessionStorage.removeItem(NEXT_KEY);
}

// The page asked for before SSO sign-in (`/login?next=`), kept across the Hosted
// UI round trip: the redirect URI is fixed, so it cannot travel in the URL.
const NEXT_KEY = "retail-ops.next";
export function rememberNext(next: string | null): void {
  if (typeof window === "undefined") return;
  if (next) sessionStorage.setItem(NEXT_KEY, next);
  else sessionStorage.removeItem(NEXT_KEY);
}
export function takeNext(): string | null {
  if (typeof window === "undefined") return null;
  const next = sessionStorage.getItem(NEXT_KEY);
  sessionStorage.removeItem(NEXT_KEY);
  return next;
}
