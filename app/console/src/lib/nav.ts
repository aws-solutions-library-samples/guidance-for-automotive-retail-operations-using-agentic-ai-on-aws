// Per-app navigation (spec §5.2.1, §5.9, §5.16, §5.17, §5.20). Each item names
// the persona-matrix screen it opens (app/services/common/personas.py); the
// shell shows an item only when GET /me/home lists that screen for the caller,
// so navigation and server-side authorization come from one table.

export type AppId = "dealer" | "oem" | "marketing" | "customer" | "admin";

export interface NavItem { href: string; label: string; screen?: string; icon?: string; flag?: "quick" }
export interface NavGroup { group?: string; items: NavItem[] }

export const APPS: Record<AppId, { name: string; theme: string; icon: string; subtitle: string }> = {
  dealer:    { name: "Dealer Console",   theme: "dark",      icon: "🏬", subtitle: "Sales floor · Service desk" },
  oem:       { name: "OEM Workspace",    theme: "light",     icon: "📊", subtitle: "District Manager Assistant" },
  marketing: { name: "Marketing Studio", theme: "marketing", icon: "📣", subtitle: "Campaigns · Journey Ops" },
  customer:  { name: "My Garage",        theme: "brand",     icon: "🚗", subtitle: "" },
  admin:     { name: "Admin Console",    theme: "neutral",   icon: "⚙️", subtitle: "Tenant administration" },
};

export const NAV: Record<AppId, NavGroup[]> = {
  dealer: [
    { group: "Store", items: [
      { href: "/dealer/cockpit", label: "Store Cockpit", screen: "dealer.cockpit", icon: "◧" },
      { href: "/dealer/campaigns", label: "Campaigns", screen: "dealer.campaigns", icon: "📣" },
    ] },
    { group: "Sales floor", items: [
      { href: "/dealer/journeys", label: "Journeys", screen: "dealer.journeys", icon: "▤" },
      { href: "/dealer/advisor", label: "Advisor (live)", screen: "dealer.advisor", icon: "●" },
      { href: "/dealer/discovery", label: "Discovery", screen: "dealer.discovery", icon: "⌕" },
      { href: "/dealer/assistant", label: "Assistant", screen: "dealer.assistant", icon: "✦" },
      { href: "/dealer/leads", label: "Lead Desk", screen: "dealer.leads", icon: "☎" },
      { href: "/dealer/assistant/service", label: "Service Lane", screen: "dealer.service_lane", icon: "↺" },
      { href: "/dealer/trade-in", label: "Trade-In", screen: "dealer.trade_in", icon: "⇄" },
      { href: "/dealer/inventory", label: "Inventory & Trade Desk", screen: "dealer.inventory", icon: "▦" },
      { href: "/dealer/fni", label: "F&I Desk", screen: "dealer.fni", icon: "§" },
      { href: "/dealer/delivery", label: "Delivery Board", screen: "dealer.delivery", icon: "⛟" },
    ] },
    { group: "Service desk", items: [
      { href: "/dealer/service", label: "Service Board", screen: "service.board", icon: "▥" },
      { href: "/dealer/service/my-ros", label: "My ROs", screen: "service.my_ros", icon: "🔧" },
      { href: "/dealer/service/book", label: "Book service", screen: "service.book", icon: "＋" },
      // Declined work has no screen of its own; GET /service/declined serves the
      // estimate roles (ADVISORS), so it follows the estimate screen.
      { href: "/dealer/service/declined", label: "Declined work", screen: "service.estimate", icon: "⏳" },
      { href: "/dealer/service/parts", label: "Parts desk", screen: "service.parts", icon: "⚙" },
      { href: "/dealer/service/cockpit", label: "Service Cockpit", screen: "service.cockpit", icon: "◨" },
    ] },
    { group: "Work", items: [
      { href: "/tasks", label: "Tasks", screen: "dealer.tasks", icon: "✓" },
      { href: "/approvals", label: "Approvals", screen: "approvals", icon: "⚖" },
    ] },
  ],
  oem: [
    { group: "Dashboard", items: [
      { href: "/oem", label: "Today", screen: "oem.today" },
      { href: "/oem/performance", label: "Dealer Performance", screen: "oem.today" },
      { href: "/oem/composite", label: "Composite Analysis", screen: "oem.today" },
      { href: "/oem/region", label: "Region View", screen: "oem.region" },
      { href: "/oem/accounts", label: "Accounts", screen: "oem.accounts" },
    ] },
    { group: "Field tools", items: [
      { href: "/oem/contact-reports", label: "Contact Reports", screen: "oem.today" },
      { href: "/oem/action-plans", label: "Action Plans", screen: "oem.today" },
      { href: "/oem/alerts", label: "AI Alerts", screen: "oem.alerts" },
      { href: "/oem/weekend", label: "Weekend Tracker", screen: "oem.today" },
      { href: "/oem/meeting", label: "Meeting in a Box", screen: "oem.today" },
    ] },
    { group: "Intelligence", items: [
      { href: "/oem/scenario", label: "Scenario Planning", screen: "oem.scenario" },
      { href: "/oem/conquest", label: "Conquest", screen: "oem.today" },
      { href: "/oem/ev-readiness", label: "EV Readiness", screen: "oem.today" },
      { href: "/oem/compliance", label: "Compliance", screen: "oem.today" },
      { href: "/oem/campaigns", label: "Campaign ROI", screen: "oem.today" },
      { href: "/oem/quick", label: "Amazon Quick", screen: "oem.today", flag: "quick" },
    ] },
    { group: "Admin", items: [
      { href: "/oem/comms", label: "Sales Ops Comms", screen: "oem.comms" },
      { href: "/oem/reports", label: "Report Library", screen: "oem.reports" },
      { href: "/oem/guardrails", label: "AI Guardrails", screen: "oem.today" },
    ] },
    { group: "Work", items: [{ href: "/approvals", label: "Approvals", screen: "approvals" }] },
  ],
  marketing: [
    { items: [
      { href: "/marketing/campaigns", label: "Campaigns", screen: "marketing.campaigns" },
      { href: "/marketing/audiences", label: "Audiences", screen: "marketing.audiences" },
      { href: "/marketing/journey-ops", label: "Journey Ops", screen: "marketing.journey_ops" },
      { href: "/marketing/quick", label: "Quick Dashboards", screen: "marketing.campaigns", flag: "quick" },
      { href: "/approvals", label: "Approvals", screen: "approvals" },
    ] },
  ],
  customer: [
    { items: [
      { href: "/garage", label: "My Garage", screen: "customer.garage" },
      { href: "/garage/service", label: "Service", screen: "customer.garage" },
      { href: "/shop", label: "Shop", screen: "customer.shop" },
      { href: "/shop/chat", label: "Chat", screen: "customer.chat" },
    ] },
  ],
  admin: [
    { group: "Identity", items: [
      { href: "/admin", label: "Overview", screen: "admin.home" },
      { href: "/admin/users", label: "Users & roles", screen: "admin.home" },
      { href: "/admin/org", label: "Org hierarchy", screen: "admin.home" },
    ] },
    { group: "Configuration", items: [
      { href: "/admin/market", label: "Market & brand", screen: "admin.home" },
      { href: "/admin/connectors", label: "Connectors", screen: "admin.home" },
      { href: "/admin/flags", label: "Feature flags", screen: "admin.home" },
      { href: "/admin/policies", label: "Guardrails & policies", screen: "admin.home" },
      { href: "/admin/autonomy", label: "Autonomy", screen: "admin.home" },
    ] },
    { group: "Operations", items: [
      { href: "/admin/agents", label: "Agents", screen: "admin.home" },
      { href: "/admin/audit", label: "Audit", screen: "admin.home" },
      { href: "/admin/data-requests", label: "Data requests", screen: "admin.home" },
      { href: "/admin/entities", label: "Entity merge queue", screen: "admin.home" },
      { href: "/admin/approvals", label: "Approvals", screen: "admin.approvals" },
    ] },
  ],
};

export function appForPath(pathname: string): AppId {
  if (pathname.startsWith("/oem")) return "oem";
  if (pathname.startsWith("/marketing")) return "marketing";
  if (pathname.startsWith("/garage") || pathname.startsWith("/shop")) return "customer";
  if (pathname.startsWith("/admin")) return "admin";
  return "dealer";
}

/** Apps a persona may open, derived from its permitted screens. */
export function appsForScreens(screens: string[]): AppId[] {
  const has = (p: string) => screens.some((s) => s.startsWith(p));
  const out: AppId[] = [];
  if (has("dealer.") || has("service.")) out.push("dealer");
  if (has("oem.")) out.push("oem");
  if (has("marketing.")) out.push("marketing");
  if (has("customer.")) out.push("customer");
  if (has("admin.")) out.push("admin");
  return out;
}

export function firstHref(app: AppId, screens: string[]): string {
  for (const g of NAV[app]) for (const i of g.items) if (!i.screen || screens.includes(i.screen)) return i.href;
  return "/";
}

// ---- landing (spec §5.18.1, decided 2026-10-28, amended 2026-10-29) --------------
// Every way in lands on the persona's home (GET /me/home `url`), except that the
// admin roles below land on the demo portal. The Shell applies these rules;
// sign-in and SSO pass the requested page on as `?next=`.

/** The roles whose landing is the demo portal (`/demo`) rather than their home
 *  (user decision 2026-10-29): the Administrator and the Tenant Admin. The Dealer
 *  Admin is store-scoped and keeps `/admin`, because the portal switches personas
 *  across the tenant. Everyone else opens the portal from shell bar › Demo. */
export const DEMO_LANDING_ROLES: readonly string[] = ["platform_admin", "admin_tenant"];

/** Where a persona lands when no page was asked for: the demo portal for a
 *  DEMO_LANDING_ROLES admin signed in as itself, else its home. An admin viewing
 *  as a persona carries that persona's claims, so it lands on that persona's home. */
export function defaultLanding(p: { role?: string; home: string; impersonating?: boolean }): string {
  return p.role && !p.impersonating && DEMO_LANDING_ROLES.includes(p.role) ? "/demo" : p.home;
}

/** Route of each persona-matrix screen: a copy of SCREENS in
 *  app/services/common/personas.py (scripts/check-screen-links.mjs fails when the
 *  two differ). `{id}` is a path segment; `?key={id}` is a query the screen needs. */
export const SCREEN_ROUTES: Record<string, string> = {
  "dealer.cockpit": "/dealer/cockpit",
  "dealer.journeys": "/dealer/journeys",
  "dealer.journey": "/dealer/journeys?focus={id}",
  "dealer.discovery": "/dealer/discovery",
  "dealer.advisor": "/dealer/advisor/{id}",
  "dealer.customer360": "/dealer/customers/{id}",
  "dealer.assistant": "/dealer/assistant",
  "dealer.leads": "/dealer/leads",
  "dealer.service_lane": "/dealer/assistant/service",
  "dealer.fni": "/dealer/fni",
  "dealer.fni_deal": "/dealer/fni/{id}",
  "dealer.trade_in": "/dealer/trade-in",
  "dealer.inventory": "/dealer/inventory",
  "dealer.inventory_vin": "/dealer/inventory?vin={id}",
  "dealer.delivery": "/dealer/delivery",
  "dealer.campaigns": "/dealer/campaigns",
  "dealer.tasks": "/tasks",
  "dealer.task": "/tasks?focus={id}",
  "service.board": "/dealer/service",
  "service.my_ros": "/dealer/service/my-ros",
  "service.parts": "/dealer/service/parts",
  "service.cockpit": "/dealer/service/cockpit",
  "service.journey": "/dealer/service/{id}",
  "service.estimate": "/dealer/service/{id}/estimate",
  "service.inspect": "/dealer/service/{id}/inspect",
  "service.history": "/dealer/service/history/{id}",
  "service.book": "/dealer/service/book",
  "service.checkin": "/dealer/service/{id}/checkin",
  "oem.today": "/oem",
  "oem.region": "/oem/region",
  "oem.accounts": "/oem/accounts",
  "oem.account": "/oem/accounts/{id}",
  "oem.alerts": "/oem/alerts",
  "oem.alert": "/oem/alerts?focus={id}",
  "oem.scenario": "/oem/scenario",
  "oem.comms": "/oem/comms",
  "oem.reports": "/oem/reports",
  "oem.plan": "/oem/accounts/{id}",
  "marketing.campaigns": "/marketing/campaigns",
  "marketing.campaign": "/marketing/campaigns?focus={id}",
  "marketing.audiences": "/marketing/audiences",
  "marketing.journey_ops": "/marketing/journey-ops",
  "admin.home": "/admin",
  "admin.approvals": "/admin/approvals",
  "notifications": "/notifications",
  "customer.garage": "/garage",
  "customer.garage_service": "/garage/service/{id}",
  "customer.shop": "/shop",
  "customer.chat": "/shop/chat",
  "approvals": "/approvals",
};

const segs = (p: string) => p.split("/").filter(Boolean);

/** The persona-matrix screens a console page belongs to (any of them opens it):
 *  a literal route first ("/dealer/service/parts"), then a sidebar destination
 *  ("/oem/performance" → oem.today), then a route with an id
 *  ("/dealer/service/SJ-1/estimate" → service.estimate). [] for a page with no
 *  screen (the demo portal, sign-in). */
export function screensForPath(href: string): string[] {
  const [pathPart, query = ""] = href.split("#")[0].split("?");
  const path = segs(pathPart);
  const keys = new Set([...new URLSearchParams(query).keys()]);
  const fit = (literal: boolean) => Object.entries(SCREEN_ROUTES).filter(([, tmpl]) => {
    const [tp, tq] = tmpl.split("?");
    const t = segs(tp);
    if (t.length !== path.length || t.includes("{id}") === literal) return false;
    if (tq && !keys.has(tq.split("=")[0])) return false;
    return t.every((s, i) => s === path[i] || (s === "{id}" && Boolean(path[i])));
  }).map(([id]) => id);
  const literal = fit(true);
  if (literal.length) return literal;
  const base = "/" + path.join("/");
  const nav = [...new Set(Object.values(NAV).flatMap((gs) => gs.flatMap((g) => g.items))
    .filter((i) => i.href === base && i.screen).map((i) => i.screen as string))];
  return nav.length ? nav : fit(false);
}

/** Old console URLs (bookmarks, earlier demo scripts) and the screen that replaced each. */
export const LEGACY_PATHS: Record<string, string> = {
  "/marketing/attribution": "/marketing/campaigns",     // attribution + ROI live on Campaigns
};

/** The current screen for an old path (exact or a sub-path of one), or null. */
export function legacyTarget(path: string): string | null {
  const bare = path.replace(/\/+$/, "") || "/";
  const hit = Object.keys(LEGACY_PATHS).find((old) => bare === old || bare.startsWith(old + "/"));
  return hit ? LEGACY_PATHS[hit] : null;
}

/** False only when the page belongs to screens and the persona holds none of them. */
export function holdsPath(screens: readonly string[], href: string): boolean {
  const own = screensForPath(href);
  return own.length === 0 || own.some((s) => screens.includes(s));
}

/** Shell-level pages (Tasks, Notifications, Approvals) belong to every app. */
export const SHELL_PAGES = ["/tasks", "/notifications", "/approvals"];
export const isShellPage = (path: string) => SHELL_PAGES.some((p) => path === p || path.startsWith(p + "/"));

/** A same-origin console path from `?next=`, or null (absolute URLs, `//host`,
 *  and the sign-in pages themselves are refused). */
export function safeNext(next: string | null | undefined): string | null {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.includes("\\")) return null;
  const path = next.split(/[?#]/)[0];
  if (path === "/" || ["/login", "/auth/callback", "/garage/login"].some((p) => path === p || path.startsWith(p + "/"))) return null;
  return next;
}

/** Where a signed-in persona lands. No page asked for (or an unsafe one):
 *  defaultLanding, i.e. the demo portal for the admin roles, else its home. A page
 *  asked for: kept when it is the demo portal, a shell page or in an app the
 *  persona can open, otherwise its home. A requested page the role holds no screen
 *  for still opens when its API serves the role; the Shell sends the user home if
 *  that API refuses (see holdsPath). */
export function landingFor(p: { role?: string; home: string; screens: readonly string[]; impersonating?: boolean },
                           requested?: string | null): string {
  const want = safeNext(requested);
  if (!want) return defaultLanding(p);
  const path = want.split(/[?#]/)[0];
  const staff = !p.screens.some((s) => s.startsWith("customer."));
  // the demo portal is open to every persona (shell bar › Demo); the static
  // export serves it as /demo/
  if (path === "/demo" || path === "/demo/" || (isShellPage(path) && (staff || path.startsWith("/notifications")))) return want;
  // a page in an app the role cannot open is refused, and refused pages go home (as in the Shell)
  return appsForScreens([...p.screens]).includes(appForPath(path)) ? want : p.home;
}

/** The app switcher's target: the persona's home when it is in that app, else the
 *  first sidebar screen of the app the persona holds, else the home. */
export function appLanding(app: AppId, p: { home: string; screens: readonly string[] }): string {
  if (appForPath(p.home) === app) return p.home;
  const first = firstHref(app, [...p.screens]);
  return first === "/" ? p.home : first;
}

/** Whether a persona with these screens may open a persona-matrix screen id: the
 *  same test the sidebar applies. Pages show a link only when it passes
 *  (`useCanOpen` in lib/persona.tsx; scripts/check-screen-links.mjs checks it). */
export function canOpen(screens: readonly string[] | null | undefined, screen: string): boolean {
  return Boolean(screens?.includes(screen));
}

/** The persona-matrix screen a sidebar destination opens (query and hash
 *  ignored), e.g. "/dealer/assistant/service?x=1" → "dealer.service_lane";
 *  undefined for a path that is not a sidebar destination. */
export function screenForHref(href: string): string | undefined {
  const path = href.split(/[?#]/)[0].replace(/\/$/, "") || "/";
  for (const app of Object.keys(NAV) as AppId[]) {
    for (const g of NAV[app]) for (const i of g.items) if (i.href === path && i.screen) return i.screen;
  }
  return undefined;
}

/** The Assistant's tab strip (§5.8 extended) after the Chat tab, each shown only
 *  to a role that holds its screen: the Lead Desk is the BDC's and the sales
 *  advisor's, the Service Lane the sales floor's and the service desk's. */
export const ASSISTANT_TABS: NavItem[] = [
  { href: "/dealer/leads", label: "Lead Desk", screen: "dealer.leads" },
  { href: "/dealer/assistant/service", label: "Service Lane", screen: "dealer.service_lane" },
];
