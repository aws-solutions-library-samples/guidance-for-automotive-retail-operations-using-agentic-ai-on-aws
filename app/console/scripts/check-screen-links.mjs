#!/usr/bin/env node
// Screen-link gate (user decision 2026-10-28): the console offers a link only to
// roles that can open the screen behind it. A service advisor clicked the
// Assistant's hard-coded Lead Desk tab and got "role 'dealer_service' may not
// perform this action (GET /leads/queue)"; the sidebar already hid the Lead Desk
// because it reads the persona matrix (app/services/common/personas.py), which
// this check reads too.
//
// 1. Assistant tab strip (lib/nav.ts ASSISTANT_TABS): each tab names the matrix
//    screen of its href; the page renders tabs only through can(screen); every
//    role that opens the Assistant sees exactly the tabs whose screen it holds,
//    and the route behind each tab serves every role shown it (the Lead Desk is
//    the BDC's, so the service advisor sees Chat and Service Lane).
// 2. Repair-order tab strip (app/dealer/service/common.tsx TABS) and the gated
//    sidebar items: each names the matrix screen of its route, and every role
//    holding that screen is one the route's server role set serves.
// 3. Every hard-coded link in a page — href="/…", href={`/…`}, href: "/…",
//    router.push(…), follow(…) — that leads to a screen a role viewing the page
//    does not hold is either gated on that screen on the same line
//    (can("<screen>")) or listed in LEFT_ALONE with the reason it cannot end in a
//    refusal. A page's viewers are the roles holding its own screen (HOSTS for
//    shared components). An entry that matches nothing is stale and fails.
// 4. Source pins for the gates that are not links on a page: the agent panel's
//    disabled Interact, the Store Cockpit tile links (lib/nav.ts screenForHref),
//    the Shell's bottom Tasks tab.
// 5. Trade-In (decided 2026-10-28): every role holding dealer.trade_in is served
//    by the valuation and prospects routes (TRADE_IN), and the page's journey
//    actions are shown to exactly the roles the sales-only routes serve.
// 6. Landing (spec §5.18.1, decided 2026-10-28, amended 2026-10-29): every role's
//    home is in its own screen set and has a page; landingFor() sends `/`, sign-in
//    and unsafe `?next=` to the demo portal only for the admin roles in nav.ts
//    DEMO_LANDING_ROLES and to its home for every other role; View as persona
//    lands on that persona's home; a deep link the role can open is kept, and
//    `?next=/demo` is kept for every role (shell bar › Demo is open to all);
//    the app switcher stays inside each app on a held screen;
//    the sidebar shows only screens in the set and gates each item on its own
//    route's screen (OEM Comms and Reports); replays and the demo portal open
//    pages in apps their persona opens; source pins for the Shell, sign-in and
//    the refused-read signal.
//
//   node scripts/check-screen-links.mjs
//
// nav.ts is loaded with the console's own TypeScript compiler (no new deps).
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import ts from "typescript";

const SRC = new URL("../src", import.meta.url).pathname;
const APP = join(SRC, "app");
const NAV_TS = join(SRC, "lib/nav.ts");
const PERSONAS_PY = new URL("../../services/common/personas.py", import.meta.url).pathname;
const SALES_PY = new URL("../../services/api/routes/sales_floor.py", import.meta.url).pathname;
const SERVICE_PY = new URL("../../services/api/routes/service_desk.py", import.meta.url).pathname;
const USERS_JSON = new URL("../../../sample-data/synthetic/us/tables/PLAT_Users.json", import.meta.url).pathname;

const fail = [];

// ---- the persona matrix, read from personas.py --------------------------------

const py = readFileSync(PERSONAS_PY, "utf8");
const dictBlock = (name) => {
  const start = py.indexOf(`${name}: dict`);
  const open = py.indexOf("{\n", start);
  const close = py.indexOf("\n}\n", open);
  if (start < 0 || open < 0 || close < 0) throw new Error(`personas.py: cannot find the ${name} dict`);
  return py.slice(open + 2, close);
};
const SCREENS = Object.fromEntries([...dictBlock("SCREENS").matchAll(/^\s+"([a-z0-9_.]+)":\s*"([^"]+)",/gm)]
  .map((x) => [x[1], x[2]]));
const ids = Object.keys(SCREENS);
const literals = (s) => [...s.matchAll(/"([a-z0-9_.]+)"/g)].map((x) => x[1]);
const byPrefix = (prefixes) => ids.filter((k) => prefixes.some((p) => k.startsWith(p)));
// The two shorthands personas.py builds from SCREENS; fail loudly if they change.
const SHORTHAND = {
  _DEALER_ALL: ['_DEALER_ALL = frozenset(k for k in SCREENS if k.startswith(("dealer.", "service.")))', ["dealer.", "service."]],
  _OEM_ALL: ['_OEM_ALL = frozenset(k for k in SCREENS if k.startswith("oem."))', ["oem."]],
};
for (const [name, [line]] of Object.entries(SHORTHAND)) {
  if (!py.includes(line)) fail.push(`personas.py no longer defines ${name} as this check expects: ${line}`);
}
function splitTop(expr) {
  const out = [];
  let depth = 0, cur = "";
  for (const ch of expr) {
    if ("({[".includes(ch)) depth += 1;
    if (")}]".includes(ch)) depth -= 1;
    if (ch === "|" && depth === 0) { out.push(cur); cur = ""; } else cur += ch;
  }
  return [...out, cur];
}
function evalScreens(role, expr) {
  let e = expr.trim();
  const fz = /^frozenset\(([\s\S]*)\)$/.exec(e);
  if (fz) e = fz[1].trim();
  const out = new Set();
  for (const raw of splitTop(e)) {
    const t = raw.trim().replace(/\s+/g, " ");
    const comp = /^\{ ?k for k in SCREENS if k\.startswith\((.*)\) ?\}$/.exec(t);
    if (SHORTHAND[t]) byPrefix(SHORTHAND[t][1]).forEach((s) => out.add(s));
    else if (comp) byPrefix(literals(comp[1])).forEach((s) => out.add(s));
    else if (/^\{( ?"[a-z0-9_.]+" ?,?)* ?\}$/.test(t)) literals(t).forEach((s) => out.add(s));
    else throw new Error(`personas.py ${role}: cannot read the screen-set term ${t}`);
  }
  return out;
}
const MATRIX = {};
const HOME = { platform_admin: "admin.home" };
{
  const body = dictBlock("PERSONAS");
  const starts = [...body.matchAll(/^ {4}"([a-z_]+)": \("([a-z0-9_.]+)",/gm)];
  starts.forEach((m, i) => {
    const end = i + 1 < starts.length ? starts[i + 1].index : body.length;
    const expr = body.slice(m.index + m[0].length, end).trim().replace(/\),$/, "");
    MATRIX[m[1]] = evalScreens(m[1], expr);
    HOME[m[1]] = m[2];
  });
  const ALL_NOTES = 'PERSONAS = {r: (h, frozenset(sc | {"notifications"})) for r, (h, sc) in PERSONAS.items()}';
  const ADMIN = 'PERSONAS[PLATFORM_ADMIN] = ("admin.home", frozenset().union(*(PERSONAS[g][1] for g in STAFF_GROUPS)))';
  const STAFF = 'STAFF_GROUPS: tuple[str, ...] = tuple(r for r in PERSONAS if r != "customer")';
  for (const line of [ALL_NOTES, ADMIN, STAFF]) {
    if (!py.includes(line)) fail.push(`personas.py no longer has, as this check expects: ${line}`);
  }
  for (const r of Object.keys(MATRIX)) MATRIX[r].add("notifications");
  MATRIX.platform_admin = new Set(Object.keys(MATRIX).filter((r) => r !== "customer").flatMap((r) => [...MATRIX[r]]));
  for (const [r, sc] of Object.entries(MATRIX)) {
    for (const s of sc) if (!SCREENS[s]) fail.push(`personas.py ${r}: screen ${s} is not in SCREENS`);
  }
  if (Object.keys(MATRIX).length < 20) fail.push(`read only ${Object.keys(MATRIX).length} personas from personas.py`);
}
const holders = (screen) => Object.keys(MATRIX).filter((r) => MATRIX[r].has(screen));

// ---- server role sets this check compares against (read, never changed) --------

function roleSet(file, name) {
  const src = readFileSync(file, "utf8");
  const m = new RegExp(`^${name} = frozenset\\(\\{([^}]*)\\}\\)`, "m").exec(src);
  if (!m) { fail.push(`${relative(SRC, file)}: cannot read ${name}`); return new Set(); }
  return new Set(literals(m[1]));
}
const ADVISOR_READ = new Set(["dealer_advisor", "dealer_bdc", "dealer_fni", "dealer_manager", "dealer_delivery", "dealer_inventory"]);
const API = {
  // routes/sales_floor.py: GET /leads/queue r.roles(LEAD_ROLES, allow_oem=True); GET /service/lane r.roles(LANE, allow_oem=True)
  LEAD_ROLES: roleSet(SALES_PY, "LEAD_ROLES"),
  LANE: roleSet(SALES_PY, "LANE"),
  // routes/service_desk.py: estimates, check-in, bookings, declined work = ADVISORS; inspection submit = INSPECTORS
  ADVISORS: roleSet(SERVICE_PY, "ADVISORS"),
  INSPECTORS: roleSet(SERVICE_PY, "INSPECTORS"),
};
const salesSrc = readFileSync(SALES_PY, "utf8");
if (!salesSrc.includes("SALES = authz.ADVISOR_READ_ROLES")) {
  fail.push("routes/sales_floor.py: SALES is no longer authz.ADVISOR_READ_ROLES — update ADVISOR_READ here");
}
// POST /trade-in/valuation and GET /trade-in/prospects serve TRADE_IN (decided 2026-10-28).
{
  const m = /^TRADE_IN = SALES \| \{([^}]*)\}/m.exec(salesSrc);
  if (!m) fail.push("routes/sales_floor.py: cannot read TRADE_IN = SALES | {…}");
  API.TRADE_IN = new Set([...ADVISOR_READ, ...(m ? literals(m[1]) : [])]);
  for (const fn of ["_valuation", "_prospects"]) {
    if (!new RegExp(`def ${fn}\\(event, claims, _m\\):\\n    r\\.roles\\(claims, TRADE_IN, allow_oem=True\\)`).test(salesSrc)) {
      fail.push(`routes/sales_floor.py: ${fn} must check r.roles(claims, TRADE_IN, allow_oem=True)`);
    }
  }
}
// The admin roles hold every staff screen; the matrix grants it, so it is no viewer to gate for.
const VIEWER_ROLES = Object.keys(MATRIX).filter((r) => r !== "platform_admin");

// ---- lib/nav.ts ------------------------------------------------------------------

function loadTs(file) {
  const { outputText } = ts.transpileModule(readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 }, fileName: file,
  });
  const mod = { exports: {} };
  const noImports = (id) => { throw new Error(`${relative(SRC, file)} must stay import-free for this check (imports ${id})`); };
  new Function("module", "exports", "require", outputText)(mod, mod.exports, noImports);
  return mod.exports;
}
const nav = loadTs(NAV_TS);
for (const [app, groups] of Object.entries(nav.NAV)) {
  for (const g of groups) for (const i of g.items) {
    if (i.screen && !SCREENS[i.screen]) fail.push(`nav.ts ${app} ${i.label}: screen ${i.screen} is not in the persona matrix`);
  }
}

// ---- screen of a console path -----------------------------------------------------

const TEMPLATES = Object.entries(SCREENS).map(([id, tmpl]) => {
  const [p, q] = tmpl.split("?");
  return { id, segs: p.split("/").filter(Boolean), query: q ? q.split("=")[0] : null };
});
const NAV_HREF = new Map();
for (const groups of Object.values(nav.NAV)) for (const g of groups) for (const i of g.items) {
  if (i.screen && !NAV_HREF.has(i.href)) NAV_HREF.set(i.href, i.screen);
}
/** "/dealer/service/{id}/estimate?x=1" → "service.estimate"; undefined if no screen. */
function screenOf(link) {
  const [pathPart, rest = ""] = link.split("?");
  const path = pathPart.split("#")[0].replace(/(\{id\})+/g, "{id}").replace(/\/$/, "") || "/";
  const keys = new Set([...rest.matchAll(/([a-z_]+)=/g)].map((x) => x[1]));
  const segs = path.split("/").filter(Boolean);
  const fits = TEMPLATES.filter((t) => t.segs.length === segs.length
    && t.segs.every((x, i) => x === "{id}" || (segs[i] !== "{id}" && x === segs[i])));
  const exact = fits.filter((t) => !t.segs.includes("{id}") || t.segs.every((x, i) => (x === "{id}") === (segs[i] === "{id}")));
  const pick = (list) => list.find((t) => t.query && keys.has(t.query)) || list.find((t) => !t.query) || list[0];
  if (exact.length) return pick(exact).id;
  if (NAV_HREF.has(path)) return NAV_HREF.get(path);
  const loose = fits.sort((a, b) => b.segs.filter((x) => x !== "{id}").length - a.segs.filter((x) => x !== "{id}").length);
  return loose.length ? pick(loose.filter((t) => t.segs.join("/") === loose[0].segs.join("/"))).id : undefined;
}

// ---- 1. the Assistant tab strip -------------------------------------------------

const TABS_SEEN = {};
for (const t of nav.ASSISTANT_TABS || []) {
  if (!t.screen || !SCREENS[t.screen]) { fail.push(`ASSISTANT_TABS ${t.label}: no persona-matrix screen`); continue; }
  if (SCREENS[t.screen] !== t.href) fail.push(`ASSISTANT_TABS ${t.label}: ${t.href} is not the route of ${t.screen} (${SCREENS[t.screen]})`);
}
const TAB_API = { "dealer.leads": ["GET /leads/queue", API.LEAD_ROLES], "dealer.service_lane": ["GET /service/lane", API.LANE] };
for (const r of holders("dealer.assistant").filter((x) => x !== "platform_admin")) {
  const shown = (nav.ASSISTANT_TABS || []).filter((t) => nav.canOpen([...MATRIX[r]], t.screen));
  TABS_SEEN[r] = shown.map((t) => t.label);
  for (const t of shown) {
    const [route, set] = TAB_API[t.screen] || [];
    if (!route) fail.push(`ASSISTANT_TABS ${t.label}: record the server role set of its route here`);
    else if (!set.has(r)) fail.push(`Assistant: ${r} is shown the ${t.label} tab, but ${route} refuses the role`);
  }
}
const TABS_WANT = { dealer_service: ["Service Lane"], dealer_bdc: ["Lead Desk"],
  dealer_advisor: ["Lead Desk", "Service Lane"], dealer_manager: ["Lead Desk", "Service Lane"] };
for (const [r, want] of Object.entries(TABS_WANT)) {
  if (JSON.stringify(TABS_SEEN[r]) !== JSON.stringify(want)) {
    fail.push(`Assistant: ${r} sees tabs [${TABS_SEEN[r] || "none"}], expected Chat + [${want}]`);
  }
}
const assistantSrc = readFileSync(join(APP, "dealer/assistant/page.tsx"), "utf8");
if (!/ASSISTANT_TABS\.filter\(\(t\) => t\.screen && can\(t\.screen\)\)/.test(assistantSrc)) {
  fail.push("dealer/assistant/page.tsx: the tab strip must render ASSISTANT_TABS through can(t.screen)");
}

// ---- 2. the repair-order tabs and the gated sidebar items -------------------------

const commonSrc = readFileSync(join(APP, "dealer/service/common.tsx"), "utf8");
const RO_TABS = [...commonSrc.matchAll(/\{ seg: "([a-z]*)", label: "([^"]+)"(?:, screen: "([a-z0-9_.]+)")? \}/g)]
  .map((x) => ({ seg: x[1], label: x[2], screen: x[3] }));
// Who the server serves behind each tab (routes/service_desk.py); the repair
// order itself is SERVICE_READ_ROLES, which every service.journey holder is.
const RO_TAB_API = { checkin: "ADVISORS", inspect: "INSPECTORS", estimate: "ADVISORS" };
if (RO_TABS.length !== 4) fail.push(`common.tsx: read ${RO_TABS.length} repair-order tabs, expected 4`);
for (const t of RO_TABS) {
  if (!t.seg) continue;
  const want = screenOf(`/dealer/service/{id}/${t.seg}`);
  if (t.screen !== want) fail.push(`common.tsx tab ${t.label}: screen ${t.screen || "none"}, expected ${want}`);
  const set = API[RO_TAB_API[t.seg]];
  const refused = holders(want).filter((r) => r !== "platform_admin" && !set?.has(r));
  if (refused.length) fail.push(`common.tsx tab ${t.label}: ${RO_TAB_API[t.seg]} refuses ${refused.join(", ")}, who hold ${want}`);
}
if (!/TABS\.filter\(\(t\) => [^\n]*\(!t\.screen \|\| can\(t\.screen\)\)\)/.test(commonSrc)) {
  fail.push("common.tsx: the repair-order tabs must render through can(t.screen)");
}
// Sidebar items fixed because their page or its main action refused a role
// that held the item's old screen (both are ADVISORS routes in service_desk.py).
const NAV_API = { "/dealer/service/book": ["POST /service/bookings", "ADVISORS"],
  "/dealer/service/declined": ["GET /service/declined", "ADVISORS"] };
for (const [href, [route, setName]] of Object.entries(NAV_API)) {
  const screen = NAV_HREF.get(href);
  const refused = holders(screen).filter((r) => r !== "platform_admin" && !API[setName].has(r));
  if (!screen) fail.push(`nav.ts: no sidebar item for ${href}`);
  else if (refused.length) fail.push(`nav.ts ${href}: screen ${screen} is held by ${refused.join(", ")}, but ${route} refuses them`);
}

// ---- 3. hard-coded links in pages -------------------------------------------------

// Shared components and the screens of the pages that render them.
const HOSTS = {
  "dealer/JourneyDrawer.tsx": ["dealer.journeys", "dealer.customer360"],
  "dealer/kpis.tsx": ["dealer.cockpit", "dealer.campaigns"],
  "dealer/service/common.tsx": ["service.journey"],
};
// Links whose target screen some viewer does not hold, checked and left as they
// are: the route behind each serves every such viewer, or the link is gated by
// an equivalent role test (`gate`, which must then be on the line). Files are
// relative to src/app.
const SALES_ADVISOR_READ = "GET /journeys/{id}/advisor serves SALES (= ADVISOR_READ_ROLES) and OEM read-across";
const OEM_VIEW = "the report pages read OEM_VIEW_ROLES routes, which serve every OEM role";
const LEFT_ALONE = [
  { file: "dealer/JourneyDrawer.tsx", screen: "dealer.advisor",
    why: `F&I, delivery and the OEM district manager open Journeys; ${SALES_ADVISOR_READ}, and the Advisor screen opens read-only for them` },
  { file: "dealer/journeys/page.tsx", screen: "dealer.advisor",
    why: `F&I, delivery and the OEM district manager; ${SALES_ADVISOR_READ}; the screen opens read-only` },
  { file: "dealer/advisor/[id]/View.tsx", screen: "dealer.discovery",
    why: "the BDC agent: the Discovery routes (/discovery/shortlist, /configurations) serve SALES, which includes dealer_bdc" },
  { file: "dealer/advisor/[id]/View.tsx", screen: "dealer.trade_in",
    why: "the BDC agent: the Trade-In routes (/trade-in/*) serve SALES, which includes dealer_bdc" },
  { file: "dealer/cockpit/page.tsx", screen: "dealer.inventory",
    why: "Trade tab, shown to the inventory manager, GM and OEM roles only (COCKPIT_TABS); GET /inventory serves them (INV_READ, allow_oem)" },
  { file: "dealer/cockpit/page.tsx", screen: "dealer.fni",
    why: "F&I tab, shown to the GM and OEM roles only; GET /fni/queue serves them (FNI_READ, allow_oem)" },
  { file: "dealer/cockpit/page.tsx", screen: "service.cockpit",
    why: "Service tab, shown to the service manager, GM and OEM roles only; GET /service/cockpit serves them" },
  { file: "dealer/fni/[id]/View.tsx", screen: "dealer.inventory",
    why: "the F&I manager: GET /inventory and POST /inventory/{vin}/reserve serve dealer_fni (INV_READ, INV_WRITE)" },
  { file: "dealer/service/[id]/View.tsx", screen: "service.estimate", gate: /\badvisor && </,
    why: "already gated by isAdvisor: the estimate roles (ADVISORS)" },
  { file: "dealer/service/[id]/View.tsx", screen: "service.inspect", gate: /role === "dealer_technician" && </,
    why: "already shown to the technician only" },
  { file: "dealer/service/[id]/View.tsx", screen: "service.history",
    why: "technician and parts: GET /service/history/{vin} serves every service role (SERVICE_READ_ROLES); the page is read-only" },
  { file: "dealer/service/page.tsx", screen: "service.book", gate: /isAdvisor\(persona\?\.role\) && </,
    why: "already gated by isAdvisor: the booking roles (POST /service/bookings: ADVISORS)" },
  { file: "dealer/trade-in/page.tsx", screen: "dealer.advisor", gate: /\bsales && journey && </,
    why: `shown to the sales roles only (sales = holds the Advisor or the Inventory desk, checked below): inventory manager: ${SALES_ADVISOR_READ}, read-only screen; never the service advisor` },
  { file: "dealer/trade-in/page.tsx", screen: "dealer.customer360",
    why: "inventory manager and service advisor (upgrade prospects): GET /customers/{id} serves SALES | LANE, checked below" },
  { file: "marketing/campaigns/CampaignROI.tsx", screen: "approvals",
    why: "shown after a budget-shift submit; GET /approvals serves every staff role (r.staff)" },
  { file: "oem/accounts/[id]/View.tsx", screen: "oem.today",
    why: "C&I planner and finance: Contact Reports reads GET /oem/today and /oem/accounts (OEM_VIEW_ROLES)" },
  { file: "oem/reports/page.tsx", screen: "oem.alerts", why: `OEM sales ops: ${OEM_VIEW}` },
  { file: "oem/reports/page.tsx", screen: "oem.today", why: `OEM sales ops: ${OEM_VIEW}` },
  { file: "oem/reports/page.tsx", screen: "oem.region", why: `OEM sales ops: ${OEM_VIEW}` },
  { file: "admin/page.tsx", screen: "admin.approvals",
    why: "dealer admin: Admin › Approvals is the shared inbox (GET /approvals serves every staff role)" },
];

/** Reads a JS string literal at s[i] (quote, apostrophe or backtick); ${…} parts become {id}. */
function readString(s, i) {
  const q = s[i];
  let out = "", j = i + 1;
  while (j < s.length && s[j] !== q) {
    if (s[j] === "\\") { out += s[j + 1] || ""; j += 2; continue; }
    if (q === "`" && s[j] === "$" && s[j + 1] === "{") {
      let depth = 1; j += 2;
      while (j < s.length && depth) {
        if (s[j] === "{") depth += 1;
        else if (s[j] === "}") depth -= 1;
        else if ("\"'`".includes(s[j])) { j = readString(s, j).end; continue; }
        j += 1;
      }
      out += "{id}";
      continue;
    }
    out += s[j]; j += 1;
  }
  return { text: out, end: j + 1 };
}
const LINK_AT = /(?:\bhref=\{?\s*|\bhref:\s*|\brouter\.(?:push|replace)\(\s*|\bfollow\(\s*)(?=["'`]\/)/g;

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) yield* walk(p);
    else if (/\.tsx?$/.test(name)) yield p;
  }
}
function hostScreens(rel) {
  if (HOSTS[rel]) return HOSTS[rel];
  const route = "/" + dirname(rel).replace(/^\.$/, "").replace(/\[[a-z]+\]/g, "{id}");
  const s = screenOf(route);
  return s ? [s] : [];
}

const used = new Set();
let scanned = 0, links = 0, gated = 0, noHost = [];
for (const file of walk(APP)) {
  const rel = relative(APP, file);
  const hosts = hostScreens(rel);
  const src = readFileSync(file, "utf8");
  const lines = src.split("\n");
  let fileLinks = 0;
  lines.forEach((line, n) => {
    if (/^\s*(\/\/|\*|\/\*)/.test(line)) return;
    for (const m of line.matchAll(LINK_AT)) {
      const { text } = readString(line, m.index + m[0].length);
      const target = screenOf(text);
      fileLinks += 1;
      if (!target || !hosts.length) continue;
      links += 1;
      const viewers = [...new Set(hosts.flatMap(holders))].filter((r) => VIEWER_ROLES.includes(r));
      const lacking = viewers.filter((r) => !MATRIX[r].has(target));
      if (!lacking.length) continue;
      // the gate is on the link's line, or ends the line before a `? <Link …` branch
      const scope = /^\s*\?/.test(line) && n > 0 ? `${lines[n - 1]}\n${line}` : line;
      if (new RegExp(`\\bcan\\(\\s*["']${target.replace(/\./g, "\\.")}["']\\s*\\)`).test(scope)) { gated += 1; continue; }
      const entry = LEFT_ALONE.find((e) => e.file === rel && e.screen === target);
      if (entry && (!entry.gate || entry.gate.test(scope))) { used.add(entry); continue; }
      fail.push(`src/app/${rel}:${n + 1}: link to ${text} (${target}) is shown to ${lacking.join(", ")}, `
        + `who cannot open ${target} — gate it with can("${target}") or record why it is safe in LEFT_ALONE`);
    }
  });
  scanned += 1;
  if (fileLinks && !hosts.length) noHost.push(rel);
}
for (const e of LEFT_ALONE) if (!used.has(e)) fail.push(`LEFT_ALONE ${e.file} ${e.screen}: matches no link any more — remove it`);

// ---- 4. source pins ------------------------------------------------------------------

const PINS = [
  ["components/AgentBar.tsx", /title="Not available for your role"[\s\S]{0,200}disabled/,
    "the agent panel shows Interact disabled when the chat route refuses a staff role"],
  ["app/dealer/kpis.tsx", /screenForHref\(link\)[\s\S]{0,80}canOpen\(persona\?\.screens, screen\)/,
    "Store Cockpit tile links need the screen they open (useCanFollow)"],
  ["app/dealer/kpis.tsx", /onClick: canFollow\(k\.link\)/, "a tile is clickable only when its link can be followed"],
  ["components/Shell.tsx", /persona\.role !== "customer" && <Link href="\/tasks">/,
    "the bottom Tasks tab is staff-only (GET /tasks refuses customers)"],
  ["app/dealer/service/page.tsx", /i\.kind === "estimate_waiting" && can\("service\.estimate"\)/,
    "a waiting estimate on Needs attention opens Estimate & Approval only for the estimate roles"],
];
for (const [file, re, what] of PINS) {
  if (!re.test(readFileSync(join(SRC, file), "utf8"))) fail.push(`src/${file}: ${what}`);
}
// The inventory manager's cockpit Trade tab links the Service Lane (GET /service/lane: LANE).
if (nav.canOpen([...MATRIX.dealer_inventory], nav.screenForHref("/dealer/assistant/service?x=1"))
    || API.LANE.has("dealer_inventory")) {
  fail.push("dealer_inventory: expected the Service Lane tile links to be unfollowable for the inventory manager");
}

// ---- 5. Trade-In: every holder is served; sales-only actions stay with sales -------

const tradeSrc = readFileSync(join(APP, "dealer/trade-in/page.tsx"), "utf8");
const TRADE_VIEWERS = holders("dealer.trade_in").filter((r) => r !== "platform_admin");
for (const r of TRADE_VIEWERS) {
  if (!API.TRADE_IN.has(r)) fail.push(`Trade-In: ${r} holds dealer.trade_in, but POST /trade-in/valuation and GET /trade-in/prospects refuse it`);
  // `sales` on the page = holds the Advisor or the Inventory desk; it must be exactly
  // the roles the sales-only routes serve (POST /journeys/{id}/trade, /trade-in/payment-delta: SALES).
  const sales = MATRIX[r].has("dealer.advisor") || MATRIX[r].has("dealer.inventory");
  if (sales !== ADVISOR_READ.has(r)) {
    fail.push(`Trade-In: ${r} ${sales ? "is shown" : "is not shown"} the journey actions, but SALES ${ADVISOR_READ.has(r) ? "serves" : "refuses"} it`);
  }
  if (!ADVISOR_READ.has(r) && !API.LANE.has(r)) {
    fail.push(`Trade-In: ${r} sees the prospects' Customer 360 links, but GET /customers/{id} (SALES | LANE) refuses it`);
  }
}
if (!TRADE_VIEWERS.includes("dealer_service")) fail.push("Trade-In: the service advisor no longer holds dealer.trade_in");
for (const [re, what] of [
  [/const sales = can\("dealer\.advisor"\) \|\| can\("dealer\.inventory"\);/, "defines sales from the Advisor and Inventory screens"],
  [/setDelta\(j && sales \? await pd\.run\("\/trade-in\/payment-delta"/, "asks for the payment comparison only for sales"],
  [/\{sales && \(\s*<div className="flex gap-2 mt-3[\s\S]{0,900}?<Btn onClick=\{attach\}[^>]*>Add to journey/, "shows Add to journey only to sales"],
]) {
  if (!re.test(tradeSrc)) fail.push(`src/app/dealer/trade-in/page.tsx: the page ${what}`);
}

// ---- 6. Landing: each persona's home, the sidebar, the app switcher ---------------

{
  const routes = nav.SCREEN_ROUTES || {};
  const diff = [...new Set([...Object.keys(routes), ...ids])].filter((k) => routes[k] !== SCREENS[k]);
  if (diff.length) fail.push(`nav.ts SCREEN_ROUTES differs from personas.py SCREENS: ${diff.join(", ")}`);
}
const pageFile = (route) => join(APP, ...route.split("?")[0].split("/").filter(Boolean)
  .map((s) => (s === "{id}" ? "[id]" : s)), "page.tsx");
// every sidebar item names a matrix screen (the Shell shows an item only when the
// persona holds it), and an item whose href is a screen's own route names that screen
const LITERAL_ROUTE = new Map();
for (const [id, t] of Object.entries(SCREENS)) if (!t.includes("{id}")) {
  if (!LITERAL_ROUTE.has(t)) LITERAL_ROUTE.set(t, new Set());
  LITERAL_ROUTE.get(t).add(id);
}
for (const [app, groups] of Object.entries(nav.NAV)) for (const g of groups) for (const i of g.items) {
  if (!i.screen) fail.push(`nav.ts ${app} ${i.label}: no screen, so the sidebar would show it to every role`);
  const own = LITERAL_ROUTE.get(i.href);
  if (own && !own.has(i.screen)) fail.push(`nav.ts ${app} ${i.label}: gated on ${i.screen}, but ${i.href} is the route of ${[...own].join(" / ")}`);
  if (!existsSync(pageFile(i.href))) fail.push(`nav.ts ${app} ${i.label}: no page for ${i.href}`);
}
for (const [href, screen] of [["/oem/comms", "oem.comms"], ["/oem/reports", "oem.reports"]]) {
  if (NAV_HREF.get(href) !== screen) fail.push(`nav.ts ${href}: must be gated on its own screen ${screen}, not ${NAV_HREF.get(href)}`);
}
const shellSrc = readFileSync(join(SRC, "components/Shell.tsx"), "utf8");
if (!/g\.items\.filter\(\(i\) => \(!i\.screen \|\| persona\.screens\.includes\(i\.screen\)\)/.test(shellSrc)) {
  fail.push("components/Shell.tsx: the sidebar must show an item only when persona.screens holds its screen");
}
const sidebarOf = (screens) => Object.fromEntries(Object.entries(nav.NAV).map(([app, gs]) => [app,
  gs.flatMap((g) => g.items).filter((i) => !i.screen || screens.includes(i.screen)).map((i) => i.label)]).filter(([, l]) => l.length));
// The demo-portal landing (user decision 2026-10-29): `/` goes to /demo only for
// the roles in nav.ts DEMO_LANDING_ROLES, which must be admin roles (home
// admin.home) and must not be empty; every other role goes to its home.
const DEMO_ROLES = [...(nav.DEMO_LANDING_ROLES || [])];
if (!DEMO_ROLES.length) fail.push("nav.ts DEMO_LANDING_ROLES: empty, so no admin lands on the demo portal (decided 2026-10-29)");
for (const r of DEMO_ROLES) {
  if (!MATRIX[r]) fail.push(`nav.ts DEMO_LANDING_ROLES: ${r} is not a persona-matrix role`);
  else if (HOME[r] !== "admin.home") fail.push(`nav.ts DEMO_LANDING_ROLES: ${r} is not an admin role (home ${HOME[r]}); only admins land on the demo portal`);
}
const LANDED = {};
for (const r of Object.keys(MATRIX)) {
  const screens = [...MATRIX[r]];
  const home = HOME[r];
  const homePath = SCREENS[home];
  const p = { role: r, home: homePath, screens, impersonating: false };
  if (!MATRIX[r].has(home)) fail.push(`personas.py ${r}: home ${home} is not in its own screen set`);
  if (!homePath || homePath.includes("{id}")) { fail.push(`personas.py ${r}: home ${home} has no fixed route`); continue; }
  if (!existsSync(pageFile(homePath))) fail.push(`personas.py ${r}: home ${homePath} has no console page`);
  if (!nav.screensForPath(homePath).includes(home)) fail.push(`nav.ts screensForPath(${homePath}) misses ${r}'s home ${home}`);
  // no page asked for (sign-in, `/`, a dev-token link, Return to my admin view) or an unsafe one
  const landing = DEMO_ROLES.includes(r) ? "/demo" : homePath;
  for (const [asked, want] of [[null, landing], ["/", landing], ["https://example.com/x", landing], ["//example.com", landing],
    ["/login?next=/x", landing], [homePath, homePath], ["/demo", "/demo"], ["/demo/", "/demo/"]]) {
    const got = nav.landingFor(p, asked);
    if (got !== want) fail.push(`nav.ts landingFor(${r}, ${asked}) = ${got}, expected ${want}`);
  }
  if (nav.landingFor(p, "/notifications") !== "/notifications") fail.push(`nav.ts landingFor(${r}): /notifications is every persona's`);
  // View as persona: an admin viewing as r carries r's claims, so it lands on r's home
  const viewAs = nav.landingFor({ ...p, impersonating: true }, null);
  if (viewAs !== homePath) fail.push(`nav.ts landingFor: View as ${r} lands on ${viewAs}, expected that persona's home ${homePath}`);
  // a deep link the role can open is kept (the first screen of each of its apps);
  // one in an app it cannot open goes home
  for (const app of Object.keys(nav.NAV)) {
    const to = nav.appsForScreens(screens).includes(app) ? nav.appLanding(app, p) : nav.NAV[app][0].items[0].href;
    const want = nav.appsForScreens(screens).includes(app) ? to : homePath;
    const got = nav.landingFor(p, to);
    if (got !== want) fail.push(`nav.ts landingFor(${r}, ${to}) = ${got}, expected ${want}`);
  }
  if (DEMO_ROLES.includes(r) && nav.landingFor(p, "/admin/users") !== "/admin/users") {
    fail.push(`nav.ts landingFor(${r}, /admin/users): an admin's deep link must be kept, not replaced by the demo portal`);
  }
  // the sidebar shows only screens in the set
  for (const [app, labels] of Object.entries(sidebarOf(screens))) for (const label of labels) {
    const item = nav.NAV[app].flatMap((g) => g.items).find((i) => i.label === label);
    if (!MATRIX[r].has(item.screen)) fail.push(`${r}: sidebar ${app} › ${label} (${item.screen}) is not in its screen set`);
  }
  const switcher = nav.appsForScreens(screens).map((app) => {
    const to = nav.appLanding(app, p);
    if (nav.appForPath(to) !== app) fail.push(`${r}: the app switcher's ${app} opens ${to}, outside that app`);
    if (!nav.holdsPath(screens, to)) fail.push(`${r}: the app switcher's ${app} opens ${to}, a page the role holds no screen for`);
    return `${app}→${to}`;
  });
  LANDED[r] = `${landing === homePath ? homePath : `${landing} (home ${homePath})`} [${switcher.join(", ")}]`;
}
if (MATRIX.dealer_advisor && !MATRIX.dealer_advisor.has("dealer.leads")) fail.push("personas.py dealer_advisor: no dealer.leads (Lead Desk, decided 2026-10-28)");
if (!API.LEAD_ROLES.has("dealer_advisor")) fail.push("routes/sales_floor.py: LEAD_ROLES must serve dealer_advisor");
if ([...(MATRIX.oem_sales_ops || [])].some((s) => s.startsWith("admin."))) fail.push("personas.py oem_sales_ops: holds an admin.* screen, but every admin route refuses it");
if (JSON.stringify(sidebarOf([...(MATRIX.oem_sales_ops || [])])) !== JSON.stringify({ oem: ["Sales Ops Comms", "Report Library"] })) {
  fail.push(`oem_sales_ops: sidebar ${JSON.stringify(sidebarOf([...(MATRIX.oem_sales_ops || [])]))}, expected OEM › Sales Ops Comms + Report Library`);
}
// the checker's own page→screen reading and nav.ts's runtime one agree on every page
for (const file of walk(APP)) {
  if (!file.endsWith("/page.tsx")) continue;
  const route = "/" + relative(APP, dirname(file)).replace(/^\.$/, "").replace(/\[[a-z]+\]/g, "{id}");
  const mine = screenOf(route);
  const concrete = route.replace(/\{id\}/g, "X-1");
  if (mine && !nav.screensForPath(concrete).includes(mine)) {
    fail.push(`nav.ts screensForPath(${concrete}) = [${nav.screensForPath(concrete)}], expected it to include ${mine}`);
  }
}
for (const [path, want] of [["/dealer/service/declined", ["service.estimate"]], ["/dealer/inventory", ["dealer.inventory"]],
  ["/dealer/inventory?vin=X", ["dealer.inventory", "dealer.inventory_vin"]], ["/dealer/leads/", ["dealer.leads"]], ["/demo", []]]) {
  const got = nav.screensForPath(path).sort();
  if (JSON.stringify(got) !== JSON.stringify(want)) fail.push(`nav.ts screensForPath(${path}) = [${got}], expected [${want}]`);
}
if (nav.holdsPath([...MATRIX.dealer_service], "/dealer/leads")) fail.push("nav.ts holdsPath: the service advisor must not hold /dealer/leads");

// Landing source pins: one place (the Shell) applies nav.ts's rules.
const LANDING_PINS = [
  ["components/Shell.tsx", /pathname === "\/" \? "\/login" : `\/login\?next=\$\{encodeURIComponent\(here\)\}`/, "signed out → /login?next=<page>"],
  ["components/Shell.tsx", /if \(pathname === "\/"\) router\.replace\(landingFor\(persona, /, "/ → landingFor(persona, ?next=)"],
  ["components/Shell.tsx", /else if \(!allowedApp\) router\.replace\(persona\.home\)/, "an app the role cannot open → its home"],
  ["components/Shell.tsx", /if \(!holdsPath\(persona\.screens, window\.location\.pathname \+ window\.location\.search\)\) router\.replace\(persona\.home\)/,
    "a refused page outside the role's screens → its home"],
  ["components/Shell.tsx", /window\.addEventListener\(FORBIDDEN_EVENT, onForbidden\)/, "the Shell listens for refused page reads"],
  ["components/Shell.tsx", /href=\{appLanding\(id, persona\)\}/, "the app switcher opens appLanding()"],
  ["components/Shell.tsx", /setActAs\(null\); window\.location\.assign\("\/"\)/, "ending View as lands on the admin's landing via /"],
  ["components/Shell.tsx", /^\s*<Link href="\/demo" className="shell-btn"/m, "the shell bar's Demo button is shown to every persona, ungated"],
  ["components/Shell.tsx", /const allowedApp = Boolean\(persona && \(isPortal \|\| /, "the demo portal opens for every signed-in persona"],
  ["components/Shell.tsx", /reload\(\);\s*window\.location\.assign\("\/"\);/, "View as lands on the persona's home via /"],
  ["lib/hooks.ts", /e\?\.status === 403 && !background\) \{\s*window\.dispatchEvent\(new CustomEvent\(FORBIDDEN_EVENT/, "useApi signals a refused page read"],
  ["components/AgentBar.tsx", /\/traces\?\$\{qs\}&limit=30` : null, \{ pollMs: 20000, background: true \}/, "the agent panel's reads are background"],
  ["components/app/AiLog.tsx", /\{ pollMs, background: true \}/, "the AI log's reads are background"],
  ["app/login/page.tsx", /const land = \(\) => window\.location\.replace\(requested\(\) \|\| "\/"\)/, "sign-in lands on ?next= or /"],
  ["app/login/page.tsx", /onClick=\{\(\) => rememberNext\(requested\(\)\)\}/, "SSO keeps ?next= across the Hosted UI"],
  ["app/auth/callback/page.tsx", /window\.location\.replace\(safeNext\(takeNext\(\)\) \|\| "\/"\)/, "the SSO callback lands on the kept page or /"],
];
for (const [file, re, what] of LANDING_PINS) {
  if (!re.test(readFileSync(join(SRC, file), "utf8"))) fail.push(`src/${file}: ${what}`);
}
// The demo-portal landing lives in one place, nav.ts landingFor (DEMO_LANDING_ROLES):
// no component sends anyone to /demo on its own.
for (const file of ["components/Shell.tsx", "app/login/page.tsx", "app/auth/callback/page.tsx", "app/page.tsx"]) {
  if (/(?:replace|assign|push)\(\s*["'`]\/demo/.test(readFileSync(join(SRC, file), "utf8"))) {
    fail.push(`src/${file}: sends the user to /demo itself; the demo-portal landing belongs to nav.ts landingFor (DEMO_LANDING_ROLES)`);
  }
}

// Replays and the demo portal open each page as a persona: the page must be in an
// app that persona opens, or the Shell sends it home mid-scenario.
{
  const users = JSON.parse(readFileSync(USERS_JSON, "utf8"));
  const roleOf = (token) => token.startsWith("cust:") ? "customer"
    : (users.find((u) => u.user_id === token.replace(/^user:/, "")) || {}).groups?.[0];
  const replaySrc = readFileSync(join(SRC, "lib/replay.ts"), "utf8");
  const T = Object.fromEntries([...replaySrc.matchAll(/^\s+([a-z]+): "((?:user|cust):[A-Z0-9-]+)",/gm)].map((x) => [x[1], x[2]]));
  const steps = [...replaySrc.matchAll(/token: T\.([a-z]+),[\s\S]*?screen: \([a-z]*\) => ([^\n]+)/g)];
  let seen = 0;
  for (const [, key, expr] of steps) {
    const lit = /["`](\/[^"`$?]*)/.exec(expr);
    const role = roleOf(T[key] || "");
    if (!lit || !role || !MATRIX[role]) { fail.push(`replay.ts: cannot read the step for T.${key}: ${expr.slice(0, 60)}`); continue; }
    seen += 1;
    const path = lit[1];
    if (path === "/demo" || nav.isShellPage(path)) continue;
    if (!nav.appsForScreens([...MATRIX[role]]).includes(nav.appForPath(path))) {
      fail.push(`replay.ts: a ${role} step opens ${path}, in an app the role cannot open`);
    }
  }
  if (seen < 30) fail.push(`replay.ts: read only ${seen} steps`);
  const demoSrc = readFileSync(join(APP, "demo/page.tsx"), "utf8");
  for (const [, token, home] of demoSrc.matchAll(/token: "((?:user|cust):[A-Z0-9-]+)",\s*app: "[^"]+", home: "([^"]+)"/g)) {
    const role = roleOf(token);
    if (!role || !nav.appsForScreens([...MATRIX[role]]).includes(nav.appForPath(home))) {
      fail.push(`demo/page.tsx: the ${token} widget enters ${home}, in an app ${role} cannot open`);
    }
  }
}

if (fail.length) {
  console.error(`check-screen-links: ${fail.length} problem(s)\n`);
  for (const f of fail) console.error("  " + f);
  process.exit(1);
}
console.log(`check-screen-links: OK — ${scanned} files, ${links} links to persona-matrix screens; `
  + `${gated} gated with can(screen), ${used.size} recorded as safe (LEFT_ALONE)`);
console.log(`  Assistant tabs: ${Object.entries(TABS_SEEN).map(([r, t]) => `${r} Chat${t.map((x) => ` + ${x}`).join("")}`).join("; ")}`);
console.log(`  repair-order tabs: ${RO_TABS.filter((t) => t.screen).map((t) => `${t.label} → ${t.screen}`).join(", ")}`);
console.log(`  Trade-In: served ${TRADE_VIEWERS.join(", ")}; journey actions for ${TRADE_VIEWERS.filter((r) => ADVISOR_READ.has(r)).join(", ")}`);
console.log(`  landing on / (home when it differs) [app switcher]:\n${Object.entries(LANDED).map(([r, l]) => `    ${r}: ${l}`).join("\n")}`);
if (noHost.length) console.log(`  not scanned (no screen of their own): ${noHost.join(", ")}`);
