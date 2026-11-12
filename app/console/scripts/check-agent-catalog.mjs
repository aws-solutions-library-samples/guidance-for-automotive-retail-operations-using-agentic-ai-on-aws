#!/usr/bin/env node
// Agent-bar catalog gate. Every persona has a bar: the admin roles got one, then
// the OEM field and analytics roles (user decisions 2026-10-22); then every
// automation's copy was rewritten to say what really runs (the agent-panel
// honesty audit, same day).
//
// 1. No structural regression: for every persona role that already had a bar
//    at 9cab45d (the last commit before those changes), the bar keeps its title
//    and its automations — same ids, same agents, same order — except the two
//    the audit retired for having no implementation (RETIRED). Interact opens
//    the chat it opened then (AGENT_CHAT[agent], unchanged) wherever that chat's
//    route accepts the role, and is hidden where it refuses it (the General
//    Manager's Campaign Agent, the OEM CX lead's Journey Agent; user decision
//    2026-10-28) — the bar's `chats` lists exactly the agents left. Run-now is
//    never withheld. AGENT_CHAT / AGENT_NAMES entries that existed then are unchanged;
//    the only addition is the Field Sales Agent (A6). Copy (name, does, when,
//    trigger, traceMatch, runs) may change: rule 4 holds it to the code.
// 2. Admin bars: the Administrator and the tenant admin carry every staff
//    automation (all seven agents), the dealer admin every store automation (the
//    six store agents), de-duplicated by id; chats, Run-now and the tenant trace
//    feed stay inside what the server routes accept from each role.
// 3. OEM field and analytics bars: each role has one, every chat it offers is a
//    route that accepts the role, and no title shows an agent code.
// 4. Honesty: a "scheduled" automation names an ENABLED job of
//    app/infra/deploy_l6.py JOBS in `runs` (`job.<name>`), only scheduled ones
//    name jobs, every other run name is one the server writes
//    (app/services/api/automation_traces.py or CODE_RUNS), an id reads the same
//    in every bar, and no copy shows an agent code.
//
//   node scripts/check-agent-catalog.mjs                                   check
//   node scripts/check-agent-catalog.mjs --write-baseline <agents.ts> [label]   capture
//
// scripts/agent-catalog.baseline.json was captured from the unmodified file:
//   git show 9cab45d:app/console/src/lib/agents.ts > /tmp/agents-9cab45d.ts
//   node scripts/check-agent-catalog.mjs --write-baseline /tmp/agents-9cab45d.ts \
//     "app/console/src/lib/agents.ts @ 9cab45d"
// agents.ts is loaded with the console's own TypeScript compiler (no new deps).
import { readFileSync, writeFileSync } from "node:fs";
import { isDeepStrictEqual } from "node:util";
import ts from "typescript";

const AGENTS_TS = new URL("../src/lib/agents.ts", import.meta.url).pathname;
const BASELINE = new URL("./agent-catalog.baseline.json", import.meta.url).pathname;
const PERSONAS_PY = new URL("../../services/common/personas.py", import.meta.url).pathname;
const JOBS_PY = new URL("../../infra/deploy_l6.py", import.meta.url).pathname;
const RUNS_PY = new URL("../../services/api/automation_traces.py", import.meta.url).pathname;

// Every persona role at 9cab45d (app/services/common/personas.py), plus inputs
// that must keep returning no bar.
const ROLES_BEFORE = [
  "dealer_advisor", "dealer_bdc", "dealer_fni", "dealer_service", "dealer_technician", "dealer_parts",
  "dealer_service_manager", "dealer_inventory", "dealer_delivery", "dealer_manager",
  "oem_dm", "oem_regional", "oem_ci_planning", "oem_sales_ops", "oem_finance", "oem_marketing", "oem_cx",
  "admin_tenant", "admin_dealer", "customer", "platform_admin",
];
const EDGE_ROLES = ["", "not_a_role"];
// Roles that had no bar at 9cab45d and have one now.
const ADMIN = ["platform_admin", "admin_tenant", "admin_dealer"];
const OEM_FIELD = ["oem_dm", "oem_regional", "oem_ci_planning", "oem_sales_ops", "oem_finance"];
const NEW_BARS = [...ADMIN, ...OEM_FIELD];
const AGENTS = ["A1", "A2", "A3", "A4", "A5", "A6", "A7"];
const STORE_AGENTS = AGENTS.filter((a) => a !== "A6");
// Retired by the honesty audit: nothing implements them (see agents.ts header).
const RETIRED = new Set(["a7.supersession", "a7.goodwill_triage"]);
// Runs a service writes itself (not a route in automation_traces.py, not a job).
const CODE_RUNS = new Set(["refresh_audiences"]);
// Server role sets behind each chat route — keep in step with the Python:
//   POST /assistant/chat      routes/sales_floor.py _chat: r.roles(claims, SALES | LANE, allow_oem=False)
//   POST /marketing/ask       routes/marketing_oem.py _ask: r.roles(claims, MARKETING_READ = authz.OEM_VIEW_ROLES)
//   POST /oem/ask             routes/marketing_oem.py _oem_ask: r.roles(claims, OEM_VIEW = authz.OEM_VIEW_ROLES)
//   POST /knowledge/retrieve  handler.py (Tier C): any signed-in user
const SALES = ["dealer_advisor", "dealer_bdc", "dealer_fni", "dealer_manager", "dealer_delivery", "dealer_inventory"];
const LANE = ["dealer_advisor", "dealer_bdc", "dealer_service", "dealer_service_manager", "dealer_manager"];
const OEM_VIEW = ["oem_dm", "oem_regional", "oem_ci_planning", "oem_sales_ops", "oem_finance", "oem_cx",
  "oem_marketing", "platform_admin"];
const CHAT_ROUTE_ACCEPTS = {
  "/assistant/chat": (role) => SALES.includes(role) || LANE.includes(role),
  "/marketing/ask": (role) => OEM_VIEW.includes(role),
  "/oem/ask": (role) => OEM_VIEW.includes(role),
  "/knowledge/retrieve": () => true,
};
// Run-now each admin role may use (page binding AND bound endpoint accept it):
//   platform_admin  a7.threshold_whatif — Service Cockpit "Recompute" re-reads GET /service/cockpit
//                   (routes/service_desk.py _cockpit: allow_oem=True; the Administrator is OEM-scoped).
//                   Every other binding is a dealer-floor write it is not granted (spec §5.2), e.g.
//                   a7.approval_chaser → POST /service/estimates/re-present (ADVISORS, no OEM read-across).
//   admin_tenant / admin_dealer  none: their screens publish no Run-now binding.
const RUN_NOW = { platform_admin: ["a7.threshold_whatif"], admin_tenant: [], admin_dealer: [] };
// GET /admin/agents/health: routes/admin_console.py _admin(claims, "agents") — the
// dealer admin's store subset (_DEALER_ADMIN_OK) excludes it.
const TENANT_FEED = { platform_admin: "tenant", admin_tenant: "tenant", admin_dealer: undefined };

function load(file) {
  const { outputText } = ts.transpileModule(readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
    fileName: file,
  });
  const mod = { exports: {} };
  const noImports = (id) => { throw new Error(`agents.ts must stay import-free for this check (imports ${id})`); };
  new Function("module", "exports", "require", outputText)(mod, mod.exports, noImports);
  return mod.exports;
}

const plain = (x) => (x === undefined ? undefined : JSON.parse(JSON.stringify(x)));

function snapshot(m) {
  const roles = {};
  for (const r of [...ROLES_BEFORE, ...EDGE_ROLES]) roles[r] = plain(m.agentsForRole(r));
  return {
    roles,
    nullRole: plain(m.agentsForRole(null)),
    undefinedRole: plain(m.agentsForRole(undefined)) ?? null,
    AGENT_CHAT: plain(m.AGENT_CHAT),
    AGENT_LABEL: plain(m.AGENT_LABEL),
    AGENT_NAMES: plain(m.AGENT_NAMES),
  };
}

if (process.argv[2] === "--write-baseline") {
  const src = process.argv[3];
  if (!src) { console.error("usage: --write-baseline <agents.ts> [label]"); process.exit(2); }
  const out = { captured_from: process.argv[4] || src, ...snapshot(load(src)) };
  writeFileSync(BASELINE, JSON.stringify(out, null, 2) + "\n");
  console.log(`check-agent-catalog: baseline written from ${out.captured_from}`);
  process.exit(0);
}

const fail = [];
const base = JSON.parse(readFileSync(BASELINE, "utf8"));
const m = load(AGENTS_TS);
const now = snapshot(m);
const chatPath = (ag) => (now.AGENT_CHAT[ag] || {}).path;
const chatAccepts = (role, ag) => Boolean(CHAT_ROUTE_ACCEPTS[chatPath(ag)]?.(role));
const hiddenChats = [];
const shape = (c) => c && { title: c.title, automations: c.automations.map((a) => ({ id: a.id, agent: a.agent })) };
const withoutRetired = (c) => c && { ...c, automations: c.automations.filter((a) => !RETIRED.has(a.id)) };

// 0. The role list really is every persona the server defines.
const declared = new Set([...readFileSync(PERSONAS_PY, "utf8").matchAll(/^ {4}"([a-z_]+)":\s*\("/gm)].map((x) => x[1]));
declared.add("platform_admin");
for (const r of declared) {
  if (!ROLES_BEFORE.includes(r)) fail.push(`personas.py defines ${r}: decide its agent bar, then add it here`);
}

// 1. Roles that already had a bar: same title and automations (ids, agents, order).
let unchanged = 0, withBar = 0;
for (const r of [...ROLES_BEFORE, ...EDGE_ROLES]) {
  if (!(r in base.roles)) { fail.push(`baseline has no entry for role "${r}"`); continue; }
  if (NEW_BARS.includes(r)) {
    if (base.roles[r] !== null) fail.push(`${r}: expected no bar in the baseline`);
    continue;
  }
  if (!isDeepStrictEqual(shape(now.roles[r]), shape(withoutRetired(base.roles[r])))) {
    fail.push(`${r || "(empty role)"}: the bar's title or automations (ids, agents, order) changed`);
    continue;
  }
  unchanged += 1;
  if (!base.roles[r]) continue;
  withBar += 1;
  for (const a of now.roles[r].automations) {
    // The customer's Interact always talks to /shop/chat (AgentChatDrawer).
    const want = r === "customer" || chatAccepts(r, a.agent) ? base.AGENT_CHAT[a.agent] : null;
    if (!isDeepStrictEqual(plain(m.agentChatFor(r, a.agent)) ?? null, want)) {
      fail.push(want ? `${r} ${a.id}: Interact no longer opens the ${a.agent} chat it opened before`
        : `${r} ${a.id}: Interact opens ${chatPath(a.agent)}, which refuses the role — hide it`);
    }
    if (m.mayRunNow(r, a.id) !== true) fail.push(`${r} ${a.id}: Run-now is now withheld`);
  }
  for (const k of ["runNow", "traceFeed"]) {
    if (now.roles[r][k] !== undefined) fail.push(`${r}: sets ${k} — pre-existing bars leave it to the server`);
  }
  if (now.roles[r].chats !== undefined) {
    const refused = [...new Set(now.roles[r].automations.map((a) => a.agent))].filter((ag) => !chatAccepts(r, ag));
    if (r === "customer" || !refused.length) fail.push(`${r}: sets chats, but every chat its bar opens accepts the role`);
    else hiddenChats.push(`${r}: Interact hidden for ${refused.map((ag) => now.AGENT_LABEL[ag]).join(", ")} (${refused.map(chatPath).join(", ")} refuses the role)`);
  }
}
// The two Interact buttons that used to end in a 403 (docs/DEMO_WORKFLOWS.md).
if (m.agentChatFor("dealer_manager", "A5")) fail.push("dealer_manager: Campaign Agent Interact is back, but /marketing/ask refuses the GM");
if (m.agentChatFor("oem_cx", "A2")) fail.push("oem_cx: Journey Agent Interact is back, but /assistant/chat refuses OEM roles");
for (const k of ["nullRole", "undefinedRole", "AGENT_LABEL"]) {
  if (!isDeepStrictEqual(now[k], base[k])) fail.push(`${k} changed`);
}
for (const k of ["AGENT_CHAT", "AGENT_NAMES"]) {
  for (const ag of Object.keys(base[k])) {
    if (!isDeepStrictEqual(now[k][ag], base[k][ag])) fail.push(`${k}.${ag} changed`);
  }
  const added = Object.keys(now[k]).filter((ag) => !(ag in base[k]));
  if (!isDeepStrictEqual(added, ["A6"])) fail.push(`${k}: added [${added}] — expected only A6`);
}
for (const ag of AGENTS) {
  if (!now.AGENT_CHAT[ag]) { fail.push(`AGENT_CHAT has no ${ag}`); continue; }
  if (!CHAT_ROUTE_ACCEPTS[chatPath(ag)]) fail.push(`no server role set recorded for chat route ${chatPath(ag)}`);
}

const automationRules = (r, c) => {
  const ids = c.automations.map((a) => a.id);
  if (new Set(ids).size !== ids.length) fail.push(`${r}: duplicate automation ids`);
  const order = c.automations.map((a) => AGENTS.indexOf(a.agent));
  if (order.some((v) => v < 0)) fail.push(`${r}: automation for an unknown agent`);
  if (/\bA[1-7]\b/.test(c.title)) fail.push(`${r}: title shows an agent code (users see agent names only)`);
  return { ids, order };
};
const chatRules = (r) => {
  for (const ag of AGENTS) {
    const accepts = CHAT_ROUTE_ACCEPTS[chatPath(ag)];
    if (!accepts) continue;
    const shown = Boolean(m.agentChatFor(r, ag));
    const inBar = now.roles[r].automations.some((a) => a.agent === ag);
    // Interact only exists on an automation in the bar: for each agent the bar
    // shows, the chat appears exactly when its route accepts the role.
    if (!inBar) continue;
    if (shown && !accepts(r)) fail.push(`${r} ${ag}: chat shown but ${chatPath(ag)} refuses the role`);
    if (accepts(r) && !shown) fail.push(`${r} ${ag}: ${chatPath(ag)} accepts the role but the chat is hidden`);
  }
  return AGENTS.filter((ag) => now.roles[r].automations.some((a) => a.agent === ag) && m.agentChatFor(r, ag));
};
const summary = [];

// 2. Admin bars: the union of the current staff bars.
const staffRoles = ROLES_BEFORE.filter((r) => now.roles[r] && r !== "customer" && !ADMIN.includes(r));
const storeRoles = staffRoles.filter((r) => r.startsWith("dealer_"));
const unionOf = (roles) => {
  const byId = new Map();
  for (const r of roles) for (const a of now.roles[r].automations) if (!byId.has(a.id)) byId.set(a.id, a);
  return byId;
};
const WANT = { platform_admin: unionOf(staffRoles), admin_tenant: unionOf(staffRoles), admin_dealer: unionOf(storeRoles) };
const COVER = { platform_admin: AGENTS, admin_tenant: AGENTS, admin_dealer: STORE_AGENTS };
for (const r of ADMIN) {
  const c = now.roles[r];
  if (!c) { fail.push(`${r}: no agent bar`); continue; }
  const want = WANT[r];
  const { ids, order } = automationRules(r, c);
  if (ids.length !== want.size || ids.some((id) => !want.has(id))) fail.push(`${r}: automations are not the expected union`);
  for (const a of c.automations) {
    if (want.has(a.id) && !isDeepStrictEqual(a, want.get(a.id))) fail.push(`${r} ${a.id}: differs from the staff definition`);
  }
  if (order.some((v, i) => i > 0 && v < order[i - 1])) fail.push(`${r}: automations not grouped by agent`);
  const covered = AGENTS.filter((ag) => c.automations.some((a) => a.agent === ag));
  if (!isDeepStrictEqual(covered, COVER[r])) fail.push(`${r}: covers ${covered.join(", ")} — expected ${COVER[r].join(", ")}`);
  const chats = chatRules(r);
  const runs = c.automations.filter((a) => m.mayRunNow(r, a.id)).map((a) => a.id);
  if (!isDeepStrictEqual(runs, RUN_NOW[r])) fail.push(`${r}: Run-now allowed for [${runs}] — expected [${RUN_NOW[r]}]`);
  if (c.traceFeed !== TENANT_FEED[r]) fail.push(`${r}: traceFeed ${c.traceFeed} — expected ${TENANT_FEED[r]}`);
  summary.push(`${r}: ${ids.length} automations / ${covered.length} agents, chat ${chats.map((ag) => now.AGENT_LABEL[ag]).join(" + ") || "none"}, `
    + `Run now ${runs.length ? runs.join(", ") : "none"}, last ran ${c.traceFeed === "tenant" ? "page scope or tenant feed" : "page scope only"}`);
}

// 3. OEM field and analytics bars.
for (const r of OEM_FIELD) {
  const c = now.roles[r];
  if (!c) { fail.push(`${r}: no agent bar`); continue; }
  const { ids } = automationRules(r, c);
  if (!c.automations.some((a) => a.agent === "A6")) fail.push(`${r}: no Field Sales Agent automation`);
  if (c.chats || c.runNow || c.traceFeed) fail.push(`${r}: sets chats / runNow / traceFeed — staff bars leave them to the server`);
  const chats = chatRules(r);
  summary.push(`${r}: ${ids.length} automations, chat ${chats.map((ag) => now.AGENT_LABEL[ag]).join(" + ") || "none"}`);
}

// 4. Honesty: every automation's trigger and runs match the code.
const jobsPy = readFileSync(JOBS_PY, "utf8");
const JOBS = new Map([...jobsPy.matchAll(
  /\("([a-z-]+)",\s*_n\("[a-z-]+"\),\s*_n\("[a-z-]+"\),\s*"((?:cron|rate)\([^"]*\))",\s*"(?:[^"\\]|\\.)*",\s*(True|False)\)/g,
)].map((x) => [x[1], { expr: x[2], enabled: x[3] === "True" }]));
if (JOBS.size < 10) fail.push(`read only ${JOBS.size} jobs from deploy_l6.py JOBS — the pattern no longer matches it`);
const ROUTE_RUNS = new Set([...readFileSync(RUNS_PY, "utf8").matchAll(/"A[1-7]",\s*"([a-z0-9_.]+)",\s*(?:"journey"|"service_journey"|"lead"|None)\)/g)]
  .map((x) => x[1]));
if (ROUTE_RUNS.size < 10) fail.push(`read only ${ROUTE_RUNS.size} run names from automation_traces.py — the pattern no longer matches it`);
const seen = new Map();
let checked = 0, scheduled = 0;
for (const r of ROLES_BEFORE) {
  for (const a of now.roles[r]?.automations || []) {
    if (RETIRED.has(a.id)) fail.push(`${r} ${a.id}: retired automation is back — implement it first`);
    if (seen.has(a.id)) {
      if (!isDeepStrictEqual(seen.get(a.id), a)) fail.push(`${a.id}: reads differently in ${r} than in another bar`);
      continue;
    }
    seen.set(a.id, a);
    checked += 1;
    if (!["auto", "scheduled", "manual"].includes(a.trigger)) fail.push(`${a.id}: unknown trigger ${a.trigger}`);
    if (!a.when || !a.does || !a.name) fail.push(`${a.id}: name, does and when are required`);
    if (/\bA[1-7]\b/.test(`${a.name} ${a.does} ${a.when}`)) fail.push(`${a.id}: copy shows an agent code`);
    const runs = a.runs || [];
    const jobs = runs.filter((k) => k.startsWith("job.")).map((k) => k.slice(4));
    if (a.trigger === "scheduled") {
      scheduled += 1;
      if (!jobs.length) fail.push(`${a.id}: scheduled, but names no job in runs`);
    } else if (jobs.length) {
      fail.push(`${a.id}: names job runs but is not "scheduled"`);
    }
    for (const j of jobs) {
      if (!JOBS.has(j)) fail.push(`${a.id}: runs job.${j}, which deploy_l6.JOBS does not define`);
      else if (!JOBS.get(j).enabled) fail.push(`${a.id}: says scheduled, but job ${j} ships DISABLED`);
    }
    for (const k of runs.filter((k) => !k.startsWith("job."))) {
      if (!ROUTE_RUNS.has(k) && !CODE_RUNS.has(k)) fail.push(`${a.id}: run ${k} is written nowhere (automation_traces.py / CODE_RUNS)`);
    }
  }
}

if (fail.length) {
  console.error(`check-agent-catalog: ${fail.length} problem(s)\n`);
  for (const f of fail) console.error("  " + f);
  console.error(`\nbaseline: ${base.captured_from}`);
  process.exit(1);
}
console.log(`check-agent-catalog: OK — ${unchanged} pre-existing role inputs keep their bars from ${base.captured_from} `
  + `(${withBar} with a bar; ids, agents and Run-now unchanged; Interact unchanged where its route accepts the role; `
  + `retired: ${[...RETIRED].join(", ")})`);
for (const s of hiddenChats) console.log("  " + s);
for (const s of summary) console.log("  " + s);
console.log(`  honesty: ${checked} automations checked, ${scheduled} scheduled — each on an enabled job; `
  + `every run name is written by the server`);
