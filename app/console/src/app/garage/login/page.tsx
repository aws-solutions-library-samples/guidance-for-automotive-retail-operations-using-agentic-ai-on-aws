"use client";

// Magic-link landing (spec §8.1 customer passwordless). Every nurture message,
// estimate-ready SMS and chat resume link opens /garage/login?token=mlk:<link>.
// The API client presents the link token from the URL; this page exchanges it
// for a short-lived customer session, stores it, and opens the link's page.

import { useEffect, useState } from "react";
import { apiBase } from "@/lib/api";
import { signInDevToken, signOut } from "@/lib/auth";

export default function MagicLinkLanding() {
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    const link = p.get("token") || "";
    if (!link.startsWith("mlk:")) { setErr("This link is missing its sign-in code."); return; }
    // A customer link means "be this customer". Any identity already in this browser — a
    // staff session, a dev token, an admin's View-as — would be sent instead of the link
    // (it takes precedence in the API client) and the redeem then fails as Unauthorized.
    signOut();
    fetch(`${apiBase()}/auth/magic-link/redeem`, {
      method: "POST", cache: "no-store",
      // the link itself is the credential for this one call (Bearer mlk:<link>)
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${link}` },
      body: JSON.stringify({ token: link }),
    })
      .then(async (res) => {
        const s = await res.json().catch(() => ({}));
        if (!res.ok) {
          setErr(res.status === 401 || res.status === 403
            ? "This link has expired or was already replaced. Ask your dealer to send a new one."
            : (s as any).error || `Sign-in failed (${res.status})`);
          return;
        }
        // the session token is what the API client sends from now on
        signInDevToken((s as any).token);
        window.location.replace((s as any).landing || "/garage");
      })
      .catch((e) => setErr(e?.message || "Sign-in failed"));
  }, []);
  return (
    <div className="page flex items-center justify-center">
      <div className="card max-w-md text-center">
        {err ? (
          <>
            <h1 className="text-lg font-semibold mb-2">We couldn&apos;t sign you in</h1>
            <p className="muted text-sm m-0" role="alert">{err}</p>
          </>
        ) : (
          <p className="muted m-0" aria-live="polite">Signing you in…</p>
        )}
      </div>
    </div>
  );
}
