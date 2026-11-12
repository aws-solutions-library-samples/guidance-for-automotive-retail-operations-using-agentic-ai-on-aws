"use client";
// Root: the Shell redirects a signed-in user to the page in `?next=` when the
// role can open it, else to its landing (nav.ts landingFor, §5.18.1): the demo
// portal for the admin roles, the persona's home screen (GET /me/home) for
// everyone else; unauthenticated visitors are sent to /login.
//
// CloudFront also serves this page for a path the console does not have (S3's 404 falls
// back to /index.html), and the Shell only redirects from `/` — so an old bookmark such as
// /marketing/attribution/ sat on "Taking you to your home screen…" forever. Here an unknown
// path goes to the screen that replaced it, else back to `/`, which lands the persona home.
import { useEffect } from "react";
import { legacyTarget } from "@/lib/nav";

export default function Home() {
  useEffect(() => {
    const path = window.location.pathname.replace(/\/+$/, "") || "/";
    if (path === "/") return;
    window.location.replace(legacyTarget(path) || "/");
  }, []);
  return <div className="page dim">Taking you to your home screen…</div>;
}
