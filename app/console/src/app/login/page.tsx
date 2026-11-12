"use client";

// Sign-in (spec §8.1, acceptance #10b): two staff paths — Cognito-native
// username/password and the enterprise IdP (SSO) through the Hosted UI — both
// ending on the persona's landing (§5.18.1). Customers sign in passwordless from a magic
// link (My Garage); local development can use a dev token against the dev server.

import { useEffect, useRef, useState } from "react";
import { authConfigured, getSession, rememberNext, signIn, signInDevToken } from "@/lib/auth";
import { ssoOptions } from "@/lib/flags";
import { safeNext } from "@/lib/nav";
import { Btn, Field } from "@/components/app";

const DEV = process.env.NODE_ENV !== "production";

/** After sign-in: the page the user asked for (`?next=`), else `/`, which the
 *  Shell turns into the persona's landing (§5.18.1). */
const requested = () => safeNext(new URLSearchParams(window.location.search).get("next"));
const land = () => window.location.replace(requested() || "/");

export default function LoginPage() {
  const [devToken, setDevToken] = useState("user:U-US-00002");
  const [busy, setBusy] = useState(false);
  // Enabled once the page has hydrated (a second at most), never gated on React state:
  // before hydration a click would do a native form submit (a reload that loses what
  // was typed); after it, the handler reads the inputs themselves (see submit).
  const [ready, setReady] = useState(false);
  useEffect(() => setReady(true), []);
  const userRef = useRef<HTMLInputElement>(null);
  const passRef = useRef<HTMLInputElement>(null);
  const [err, setErr] = useState<string | null>(null);
  // One button per external identity provider on the pool. Empty unless a real
  // external directory is wired, so we never send the user to the Hosted UI's
  // own password form — that would be a second prompt for the same credentials
  // this page already collects. Read after mount: it depends on window.origin.
  const [sso, setSso] = useState<{ idp: string; label: string; url: string }[]>([]);
  useEffect(() => setSso(ssoOptions()), []);

  useEffect(() => { if (getSession()) land(); }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    // The inputs are UNCONTROLLED and read here: what a password manager autofills, or what
    // was typed before the page hydrated, stays in the field (a controlled input is reset
    // to its empty state at hydration, and a state-gated button stayed disabled on a
    // visibly filled form). No `name`, so even a native submit never puts them in a URL.
    const u = (userRef.current?.value || "").trim();
    const p = passRef.current?.value || "";
    if (!u || !p) { setErr("Enter your username and password."); return; }
    setBusy(true); setErr(null);
    try {
      await signIn(u, p);
      land();
    } catch (e: any) {
      setErr(e.message || "Sign-in failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-6" style={{ background: "linear-gradient(135deg,#0a0e1a,#1e1b4b)" }}>
      <div className="w-full max-w-md">
        <div className="text-center mb-6">
          <div className="text-2xl font-extrabold tracking-[.04em]" style={{ background: "linear-gradient(135deg,#818cf8,#c084fc)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>Automotive Retail Operations</div>
          <div className="text-slate-400 text-sm mt-1">powered by Agentic AI · Sales &amp; Service Orchestration</div>
        </div>
        <div className="card" style={{ background: "#ffffff" }}>
          <h1 className="text-lg font-semibold mb-1" style={{ color: "#111827" }}>Sign in</h1>
          <p className="text-sm mb-4" style={{ color: "#4b5563" }}>One sign-in for the Dealer Console, OEM Workspace, Marketing Studio and Admin.</p>

          {sso.length > 0 && (
            <>
              {sso.map((o) => (
                <a key={o.idp} href={o.url} onClick={() => rememberNext(requested())} className="btn btn-primary w-full mb-2" style={{ padding: ".7rem", background: "#232f3e" }}>
                  Sign in with {o.label}
                </a>
              ))}
              <div className="flex items-center gap-3 my-4 text-xs" style={{ color: "#6b7280" }}><span className="flex-1 h-px bg-gray-200" />or<span className="flex-1 h-px bg-gray-200" /></div>
            </>
          )}

          <form onSubmit={submit} aria-label="Sign in with username and password">
            {!authConfigured() && <div className="error-box mb-3">Cognito client not configured (NEXT_PUBLIC_COGNITO_CLIENT_ID).</div>}
            <Field label="Username">{(id) => <input id={id} ref={userRef} className="input" style={{ background: "#fff", color: "#111827" }} autoComplete="username" autoFocus />}</Field>
            <Field label="Password">{(id) => <input id={id} ref={passRef} className="input" style={{ background: "#fff", color: "#111827" }} type="password" autoComplete="current-password" />}</Field>
            {err && <div className="error-box mb-3" role="alert">{err}</div>}
            <Btn kind="primary" type="submit" className="w-full" style={{ padding: ".7rem", background: "#4f46e5" }} disabled={!ready || busy} aria-busy={busy}>
              {busy ? "Signing in…" : "Sign in"}
            </Btn>
          </form>
          <p className="text-xs mt-4" style={{ color: "#6b7280" }}>Customers: open the link in your latest message from your dealer to reach My Garage.</p>
        </div>

        {DEV && (
          <div className="card mt-4" style={{ background: "rgba(255,255,255,.06)", color: "#e5e7eb" }}>
            <div className="text-xs font-semibold mb-2 text-slate-300">Local development · dev-server token</div>
            <div className="flex gap-2">
              <input className="input" value={devToken} onChange={(e) => setDevToken(e.target.value)} aria-label="Dev token" />
              <Btn kind="secondary" onClick={() => { signInDevToken(devToken.trim()); land(); }}>Use</Btn>
            </div>
            <div className="text-[.7rem] text-slate-400 mt-2">user:&lt;PLAT_Users id&gt; or cust:&lt;customer id&gt;. Accepted only where RETAIL_OPS_ALLOW_DEV_TOKEN=1 (local / dev); must be off in demo and prod.</div>
          </div>
        )}
      </div>
    </div>
  );
}
