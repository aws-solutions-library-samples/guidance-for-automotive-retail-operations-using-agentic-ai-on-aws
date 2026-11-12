"use client";

// Hosted UI redirect target for SSO sign-in — the enterprise IdP (SSO) or any
// other IdP registered on the pool (§8.1).
import { useEffect, useState } from "react";
import { completeFederatedSignIn, takeNext } from "@/lib/auth";
import { safeNext } from "@/lib/nav";

export default function AuthCallback() {
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    const code = p.get("code");
    if (p.get("error")) { setErr(p.get("error_description") || p.get("error")); return; }
    if (!code) { setErr("Missing authorization code."); return; }
    // the page asked for before sign-in, else `/` → the persona's landing (§5.18.1)
    completeFederatedSignIn(code, `${window.location.origin}/auth/callback`)
      .then(() => window.location.replace(safeNext(takeNext()) || "/"))
      .catch((e) => setErr(e.message));
  }, []);
  return (
    <div className="page">
      {err ? <div className="error-box">Single sign-on failed: {err} · <a className="underline" href="/login">Back to sign-in</a></div>
           : <div className="dim">Completing sign-in…</div>}
    </div>
  );
}
