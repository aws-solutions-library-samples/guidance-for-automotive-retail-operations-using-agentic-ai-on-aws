"use client";

// The Unified Shell (spec §5.18): one 56px bar over every app — app switcher,
// brand, scope pill, ⌘K global search, Tasks, Notifications, user menu — with a
// neutral palette; each app renders its own themed sidebar + body underneath.
// Mobile (≤480px) collapses to a bottom tab bar (§5.19).

import { fmtStamp } from "@/lib/market";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { apiGet, apiPatch, apiPost, getActAs, setActAs } from "@/lib/api";
import { hasCognitoSession, signInDevToken, signOut } from "@/lib/auth";
import { quickEnabled } from "@/lib/flags";
import { FORBIDDEN_EVENT } from "@/lib/hooks";
import { APPS, AppId, NAV, appForPath, appLanding, holdsPath, isShellPage, landingFor } from "@/lib/nav";
import { PersonaProvider, hasSession, usePersona } from "@/lib/persona";
import { resolve } from "@/lib/resourceUrl";
import { ContextStrip, ToastProvider, cx } from "@/components/app";
import { ReplayController } from "@/components/ReplayController";
import { AgentBar } from "@/components/AgentBar";

const PUBLIC = ["/login", "/auth/callback", "/garage/login"];
// Portal pages stand on their own: authenticated, but NOT inside any app — no
// app sidebar, no agent bar, neutral chrome. /demo is the demo portal (shell bar
// › Demo), so it must not look like a screen of the Dealer Console.
const PORTAL_PAGES = ["/demo"];

/** A dev-token link (`?token=user:<id>` / `cust:<id>`) signs this browser in, so
 *  the identity survives the redirect to the persona's home and later clicks. */
function keepDevTokenLink() {
  const token = new URLSearchParams(window.location.search).get("token");
  if (token && /^(user|cust):/.test(token) && !hasCognitoSession()) signInDevToken(token);
}

export function Shell({ children }: { children: ReactNode }) {
  return (
    <ToastProvider>
      <PersonaProvider>
        <ShellFrame>{children}</ShellFrame>
      </PersonaProvider>
    </ToastProvider>
  );
}

function ShellFrame({ children }: { children: ReactNode }) {
  const pathname = usePathname() || "/";
  const router = useRouter();
  const { persona, loading, error } = usePersona();
  // Shell-level pages (Tasks, Notifications, Approvals) belong to every app:
  // render them inside the persona's own home app and theme.
  const shellLevel = isShellPage(pathname);
  const isPortal = PORTAL_PAGES.some((p) => pathname === p || pathname.startsWith(p + "/"));
  const app = shellLevel && persona ? appForPath(persona.home) : appForPath(pathname);
  const isPublic = PUBLIC.some((p) => pathname.startsWith(p));
  const allowedApp = Boolean(persona && (isPortal || shellLevel || persona.apps.includes(app)));
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  // Landing (§5.18.1): signed out → sign-in, keeping the page asked for as
  // ?next=; `/` → the requested page, else landingFor's default (the demo portal
  // for the admin roles in nav.ts DEMO_LANDING_ROLES, the persona's home for
  // everyone else); an app the role cannot open → its home.
  useEffect(() => {
    if (!mounted || isPublic) return;
    if (!hasSession()) {
      const here = window.location.pathname + window.location.search;
      router.replace(pathname === "/" ? "/login" : `/login?next=${encodeURIComponent(here)}`);
      return;
    }
    if (!persona) return;
    keepDevTokenLink();
    if (pathname === "/") router.replace(landingFor(persona, new URLSearchParams(window.location.search).get("next")));
    else if (!allowedApp) router.replace(persona.home);
  }, [mounted, isPublic, persona, pathname, router, allowedApp]);

  // A page outside the persona's screens whose read API refuses the role (403)
  // sends the user home rather than leaving them on an error.
  useEffect(() => {
    if (!persona) return;
    const onForbidden = () => {
      const trim = (p: string) => p.split(/[?#]/)[0].replace(/\/$/, "") || "/";
      if (trim(window.location.pathname) === trim(persona.home)) return;
      if (!holdsPath(persona.screens, window.location.pathname + window.location.search)) router.replace(persona.home);
    };
    window.addEventListener(FORBIDDEN_EVENT, onForbidden);
    return () => window.removeEventListener(FORBIDDEN_EVENT, onForbidden);
  }, [persona, router]);

  if (isPublic) return <div className="shell-root" data-theme="neutral">{children}</div>;
  if (!mounted || loading || !persona) {
    return (
      <div className="shell-root" data-theme="neutral">
        <div className="page">{error ? <div className="error-box">Could not load your profile: {error}</div> : <div className="dim">Loading…</div>}</div>
      </div>
    );
  }

  const theme = APPS[app].theme;
  const groups = NAV[app]
    .map((g) => ({ ...g, items: g.items.filter((i) => (!i.screen || persona.screens.includes(i.screen))
                                                    && (!i.flag || (i.flag === "quick" && quickEnabled()))) }))
    .filter((g) => g.items.length > 0);
  // the single longest nav href that is the current path or a prefix of it
  const activeHref = groups.flatMap((g) => g.items.map((i) => i.href))
    .filter((h) => pathname === h || pathname.startsWith(h + "/"))
    .sort((a, b) => b.length - a.length)[0];

  // The demo portal is not a screen of any app: neutral chrome, no app sidebar,
  // no agent bar — it is the landing page that shows what is available.
  if (isPortal) {
    return (
      <div className="shell-root" data-theme="neutral">
        <ShellBar app={app} />
        <div className="app-shell">
          <main className="app-main" id="main">{children}</main>
        </div>
        <ReplayController />
      </div>
    );
  }

  return (
    <div className="shell-root" data-theme={theme}>
      <ShellBar app={app} />
      {persona.impersonating && (
        <div className="flex items-center justify-center gap-3 px-4 py-1.5 text-sm"
             style={{ background: "#78350f", color: "#fde68a" }} role="status">
          <span>👁 Viewing as <b>{persona.roleName}</b> ({persona.name}) — your actions use this persona&apos;s permissions.</span>
          <button className="underline font-semibold"
                  onClick={() => { setActAs(null); window.location.assign("/"); }}>Return to my admin view</button>
        </div>
      )}
      <ContextFromUrl />
      <div className="app-shell">
        {groups.length > 0 && <Sidebar app={app} groups={groups} activeHref={activeHref} />}
        <main className="app-main" id="main">
          {/* The agent bar is the hero agent surface: one horizontal strip over
              every screen, with per-agent chat in a side drawer. Pages feed it
              run-now actions and trace scope via <AgentActionsProvider/>. */}
          <AgentBar />
          {allowedApp ? children : <div className="page dim">Taking you to your home screen…</div>}
        </main>
      </div>
      <BottomTabs />
      <ReplayController />
    </div>
  );
}

// ---- sidebar (collapsible) ----------------------------------------------------

const NAV_COLLAPSED_KEY = "retail-ops.navCollapsed";

function Sidebar({ app, groups, activeHref }: {
  app: AppId; groups: { group?: string; items: { href: string; label: string; icon?: string }[] }[];
  activeHref?: string }) {
  const [collapsed, setCollapsed] = useState(false);
  useEffect(() => { setCollapsed(localStorage.getItem(NAV_COLLAPSED_KEY) === "1"); }, []);
  const toggle = () => setCollapsed((c) => {
    localStorage.setItem(NAV_COLLAPSED_KEY, c ? "0" : "1");
    return !c;
  });
  return (
    <nav className={cx("app-sidebar", collapsed && "collapsed")} aria-label={`${APPS[app].name} navigation`}>
      <div className="flex items-center justify-between gap-1 pr-2">
        <div className="app-name nav-text">{APPS[app].name}</div>
        <button className="nav-toggle" onClick={toggle} aria-expanded={!collapsed}
                aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}
                title={collapsed ? "Expand navigation" : "Collapse navigation"}>
          {collapsed ? "»" : "«"}
        </button>
      </div>
      {groups.map((g, gi) => (
        <div key={gi}>
          {g.group && <div className="sidebar-group nav-text">{g.group}</div>}
          {g.items.map((i) => {
            const active = i.href === activeHref;
            return (
              <Link key={i.href} href={i.href} className={cx("nav-item", active && "active")}
                    aria-current={active ? "page" : undefined} title={collapsed ? i.label : undefined}>
                <span className="nav-icon" aria-hidden>{i.icon || i.label[0]}</span>
                <span className="nav-text">{i.label}</span>
              </Link>
            );
          })}
        </div>
      ))}
    </nav>
  );
}

// ---- bar --------------------------------------------------------------------

type Menu = null | "apps" | "notes" | "user" | "tasks";

function ShellBar({ app }: { app: AppId }) {
  const { persona } = usePersona();
  const [menu, setMenu] = useState<Menu>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const toggle = (m: Menu) => setMenu((x) => (x === m ? null : m));
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setSearchOpen(true); }
      if (e.key === "Escape") setMenu(null);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);
  if (!persona) return null;
  const scope = persona.isOem ? (persona.scopeNodeId || "OEM") : persona.dealerIds.length === 1 ? persona.dealerIds[0] : persona.scopeNodeId || "—";
  return (
    <header className="shell-bar">
      <a href="#main" className="sr-only">Skip to content</a>
      <div className="relative">
        <button className="shell-btn" aria-label="Switch app" aria-expanded={menu === "apps"} onClick={() => toggle("apps")}>⠿</button>
        {menu === "apps" && <AppSwitcher active={app} onClose={() => setMenu(null)} />}
      </div>
      <Link href={persona.home} className="shell-brand whitespace-nowrap" title="Automotive Retail Operations powered by Agentic AI">Automotive Retail Operations</Link>
      <span className="shell-hide-sm text-[.72rem] text-slate-400">{APPS[app].name}</span>
      <span className="scope-pill" title="Current scope">{scope} · {persona.marketId.toUpperCase()}</span>
      <button className="shell-search" onClick={() => setSearchOpen(true)} aria-label="Global search">
        ⌕ Search customers, VINs, journeys, dealers… <kbd>⌘K</kbd>
      </button>
      <div className="ml-auto flex items-center gap-1">
        <Link href="/demo" className="shell-btn" title="Demo portal — personas & scenarios" aria-label="Demo portal">🎬<span className="shell-hide-sm">Demo</span></Link>
        {persona.role !== "customer" && (
          <div className="relative"><TasksButton open={menu === "tasks"} onToggle={() => toggle("tasks")} onClose={() => setMenu(null)} /></div>
        )}
        <div className="relative"><NotificationsButton open={menu === "notes"} onToggle={() => toggle("notes")} onClose={() => setMenu(null)} /></div>
        <div className="relative">
          <button className="shell-btn" aria-expanded={menu === "user"} onClick={() => toggle("user")}>
            <span className="shell-hide-sm">{persona.name}</span><span className="badge badge-gray">{persona.roleName}</span>
          </button>
          {menu === "user" && (
            <div className="popover right-0 top-10">
              <div className="pop-row"><b>{persona.name}</b><div className="text-slate-400 text-xs">{persona.userId}</div></div>
              <div className="pop-row text-slate-400 text-xs">{persona.roleName} · scope {scope} · market {persona.marketId}</div>
              <PersonaSwitcher onClose={() => setMenu(null)} />
              <button type="button" className="pop-row click w-full text-left"
                      onClick={(e) => { e.stopPropagation(); signOut(); window.location.replace("/login"); }}>Sign out</button>
            </div>
          )}
        </div>
      </div>
      {searchOpen && <SearchPalette onClose={() => setSearchOpen(false)} />}
    </header>
  );
}

// ---- admin "View as persona" switcher (Design A) --------------------------------
// Only admins may impersonate; the server enforces this (a non-admin's X-Act-As
// header is ignored). We show the control when the current persona is an admin
// role OR when already impersonating (so the admin can return to themselves).

const ADMIN_ROLES = ["admin_tenant", "admin_dealer", "platform_admin"];

function PersonaSwitcher({ onClose }: { onClose: () => void }) {
  const { persona, reload } = usePersona();
  const [personas, setPersonas] = useState<any[] | null>(null);
  const [open, setOpen] = useState(false);
  const canImpersonate = Boolean(persona && (ADMIN_ROLES.includes(persona.role) || persona.impersonating));
  const current = getActAs();
  useEffect(() => {
    if (!open || personas) return;
    apiGet<any>("/me/personas").then((d) => setPersonas(d.personas || [])).catch(() => setPersonas([]));
  }, [open, personas]);
  if (!canImpersonate) return null;
  const choose = (role: string | null) => {
    setActAs(role);
    onClose();
    // Reload the persona (and the page) so every view reflects the new scope.
    reload();
    window.location.assign("/");
  };
  return (
    <>
      <div className="pop-row click flex items-center justify-between" onClick={() => setOpen((o) => !o)}>
        <span>👤 View as persona…</span><span className="text-slate-400">{open ? "▴" : "▾"}</span>
      </div>
      {open && (
        <div className="max-h-64 overflow-auto border-t border-white/10">
          {current && (
            <div className="pop-row click text-amber-300" onClick={() => choose(null)}>↩ Return to my admin view</div>
          )}
          {personas === null && <div className="pop-row text-slate-400 text-xs">Loading personas…</div>}
          {personas?.map((p) => (
            <div key={p.role}
                 className={cx("pop-row click flex items-center justify-between", current === p.role && "bg-white/10")}
                 onClick={() => choose(p.role)}>
              <span>{p.name}</span><span className="text-slate-500 text-[.68rem]">{p.role}</span>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

function AppSwitcher({ active, onClose }: { active: AppId; onClose: () => void }) {
  const { persona } = usePersona();
  if (!persona) return null;
  return (
    <div className="popover left-0 top-10" onMouseLeave={onClose}>
      <div className="pop-head">Apps</div>
      {persona.apps.map((id) => (
        <Link key={id} href={appLanding(id, persona)} onClick={onClose}
              className={cx("pop-row click flex items-center gap-3", id === active && "bg-white/5")}>
          <span className="text-lg" aria-hidden>{APPS[id].icon}</span>
          <span><b>{APPS[id].name}</b><div className="text-slate-400 text-xs">{APPS[id].subtitle}</div></span>
        </Link>
      ))}
    </div>
  );
}

// ---- Tasks (§5.18.4) ----------------------------------------------------------

function useOpenTasks(poll = 60000) {
  const [d, setD] = useState<any>(null);
  const load = useCallback(() => apiGet<any>("/tasks?status=open").then(setD).catch(() => setD(null)), []);
  useEffect(() => { load(); const t = setInterval(load, poll); return () => clearInterval(t); }, [load, poll]);
  return { d, load };
}

function TasksButton({ open, onToggle, onClose }: { open: boolean; onToggle: () => void; onClose: () => void }) {
  const router = useRouter();
  const { d, load } = useOpenTasks();
  const go = async (t: any) => {
    if (!t.resource_url) return;
    const r = await resolve(t.resource_url);
    if (r.allowed && r.url) { onClose(); router.push(r.url); }
  };
  return (
    <>
      <button className="shell-btn" aria-expanded={open} onClick={onToggle} aria-label="Tasks">
        ✓ <span className="shell-hide-sm">Tasks</span>
        {d?.count ? <span className={cx("shell-count", d.overdue && "danger")}>{d.count}</span> : null}
      </button>
      {open && (
        <div className="popover right-0 top-10">
          <div className="pop-head flex justify-between"><span>Open tasks</span><Link href="/tasks" onClick={onClose} className="underline">All tasks</Link></div>
          {!d && <div className="pop-row text-slate-400">Unavailable</div>}
          {d?.tasks?.length === 0 && <div className="pop-row text-slate-400">Nothing open.</div>}
          {d?.tasks?.slice(0, 8).map((t: any) => (
            <div key={t.task_id} className="pop-row">
              <div className={cx(t.resource_url && "cursor-pointer hover:underline")} onClick={() => go(t)}>{t.overdue ? "⚠ " : ""}{t.title}</div>
              <div className="text-xs text-slate-400 flex gap-2">due {fmtStamp(t.due_at)}
                <button className="underline" onClick={async () => { await apiPatch(`/tasks/${encodeURIComponent(t.task_id)}`, { status: "done" }).catch(() => {}); load(); }}>done</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

// ---- Notifications (§5.18.4) ----------------------------------------------------

function NotificationsButton({ open, onToggle, onClose }: { open: boolean; onToggle: () => void; onClose: () => void }) {
  const router = useRouter();
  const [items, setItems] = useState<any[] | null>(null);
  const load = useCallback(() => apiGet<any>("/notifications").then((d) => setItems(d.notifications || d.items || [])).catch(() => setItems(null)), []);
  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t); }, [load]);
  const unread = (items || []).filter((n) => !n.read && !n.read_at).length;
  const openOne = async (n: any) => {
    await apiPost(`/notifications/${encodeURIComponent(n.notification_id)}/read`).catch(() => {});
    load();
    if (n.resource_url) {
      const r = await resolve(n.resource_url);
      if (r.allowed && r.url) { onClose(); router.push(r.url); }
    }
  };
  return (
    <>
      <button className="shell-btn" aria-expanded={open} onClick={onToggle} aria-label={`Notifications (${unread} unread)`}>
        🔔{unread ? <span className="shell-count danger">{unread}</span> : null}
      </button>
      {open && (
        <div className="popover right-0 top-10">
          <div className="pop-head flex justify-between"><span>Notifications</span><Link href="/notifications" onClick={onClose} className="underline">All notifications</Link></div>
          {items === null && <div className="pop-row text-slate-400">Unavailable</div>}
          {items?.length === 0 && <div className="pop-row text-slate-400">You&apos;re all caught up.</div>}
          {items?.slice(0, 10).map((n, i) => (
            <div key={n.notification_id || i} className={cx("pop-row click", !n.read && !n.read_at && "border-l-2 border-indigo-400")} onClick={() => openOne(n)}>
              <div className="text-[.8rem]">{n.title || (n.event || n.type || "notification").replace(/[._]/g, " ")}</div>
              <div className="text-xs text-slate-400">{n.body || n.journey_id || ""} {n.severity ? `· ${n.severity}` : ""}</div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

// ---- ⌘K global search (§5.18.5) ------------------------------------------------

function SearchPalette({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [res, setRes] = useState<any[]>([]);
  const [sel, setSel] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { input.current?.focus(); }, []);
  useEffect(() => {
    if (q.trim().length < 2) { setRes([]); return; }
    const t = setTimeout(() => {
      apiGet<any>(`/search?q=${encodeURIComponent(q.trim())}`)
        .then((d) => { setRes(d.results || []); setSel(0); setErr(null); })
        .catch((e) => setErr(e.message));
    }, 200);
    return () => clearTimeout(t);
  }, [q]);
  const go = async (r: any) => {
    const out = await resolve(r.type, r.id);
    if (out.allowed && out.url) { onClose(); router.push(out.url); } else setErr(out.reason || "not permitted");
  };
  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <div className="modal" style={{ top: "18%", transform: "translate(-50%,0)", background: "#0f1529", color: "#e5e7eb" }} role="dialog" aria-label="Global search">
        <input ref={input} className="w-full bg-transparent outline-none text-base px-5 py-4 border-b border-white/10"
               placeholder="Search customers, VINs, journeys, dealers, accounts, campaigns…" value={q}
               onChange={(e) => setQ(e.target.value)}
               onKeyDown={(e) => {
                 if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(s + 1, res.length - 1)); }
                 if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
                 if (e.key === "Enter" && res[sel]) go(res[sel]);
               }} aria-label="Search query" />
        <div className="max-h-[50vh] overflow-auto p-2" role="listbox">
          {err && <div className="pop-row text-red-300">{err}</div>}
          {q.trim().length >= 2 && res.length === 0 && !err && <div className="pop-row text-slate-400">No matches.</div>}
          {res.map((r, i) => (
            <div key={`${r.type}:${r.id}`} role="option" aria-selected={i === sel}
                 className={cx("pop-row click flex items-center gap-3", i === sel && "bg-white/10")} onMouseEnter={() => setSel(i)} onClick={() => go(r)}>
              <span className="badge badge-gray uppercase">{r.type.replace("_", " ")}</span>
              <span className="flex-1 min-w-0"><b>{r.label}</b>{r.detail && <span className="text-slate-400 text-xs"> · {r.detail}</span>}</span>
            </div>
          ))}
        </div>
        <div className="px-5 py-2 text-[.68rem] text-slate-500 border-t border-white/10">↑↓ to move · Enter to open · Esc to close · results limited to your scope</div>
      </div>
    </>
  );
}

// ---- context strip (?ctx=) ------------------------------------------------------

function ContextFromUrl() {
  const pathname = usePathname();
  const [because, setBecause] = useState<string | null>(null);
  useEffect(() => {
    const ctx = new URLSearchParams(window.location.search).get("ctx");
    if (!ctx) { setBecause(null); return; }
    apiGet<any>(`/nav/ctx/verify?ctx=${encodeURIComponent(ctx)}`).then((d) => setBecause(d.because)).catch(() => setBecause(null));
  }, [pathname]);
  return <ContextStrip because={because} onDismiss={() => setBecause(null)} />;
}

// ---- mobile bottom tabs (§5.19) --------------------------------------------------

function BottomTabs() {
  const { persona } = usePersona();
  if (!persona) return null;
  return (
    <nav className="bottom-tabs" aria-label="Primary">
      <Link href={persona.home}><span aria-hidden>⌂</span>Home</Link>
      <button onClick={() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", metaKey: true }))}><span aria-hidden>⌕</span>Search</button>
      {/* Tasks are staff work (GET /tasks refuses customers), as in the top bar */}
      {persona.role !== "customer" && <Link href="/tasks"><span aria-hidden>✓</span>Tasks</Link>}
      <Link href={persona.isOem && persona.screens.includes("oem.alerts") ? "/oem/alerts" : "/notifications"}><span aria-hidden>🔔</span>Alerts</Link>
      <Link href={persona.home}><span aria-hidden>☰</span>Menu</Link>
    </nav>
  );
}
