# Retail Ops Console

The Next.js (App Router, static export) front end for all five Retail Ops apps behind
one Unified Shell (spec §5.2, §5.18): **Dealer Console** (sales floor + service
desk, dark), **OEM Workspace** (light), **Marketing Studio** (light),
**Customer Web / My Garage** (OEM brand), **Admin Console** (neutral). It is a
thin client over the Retail Ops API; every number shown comes from an API response.

## Run locally (no AWS needed)

```bash
# 1. API with in-memory data seeded from sample-data/synthetic/{us,de}
RETAIL_OPS_LOCAL_DATA=1 PORT=8080 python3 -m app.services.devserver      # from the repo root

# 2. Console
cd app/console && npm install
NEXT_PUBLIC_RETAIL_OPS_API=http://127.0.0.1:8080 npm run dev                # http://localhost:3100
```

On `/login`, use the **dev token** box (local only) to pick a persona:
`user:U-US-00002` advisor · `user:U-US-00007` service advisor ·
`user:U-US-00006` F&I · `user:U-US-00001` dealer principal ·
`user:U-US-00044` district manager · `user:U-US-00050` OEM CX ·
`user:U-US-00051` tenant admin · `cust:C-US-J1` customer.
The shell sends you to that persona's home screen (`GET /me/home`).

Against a deployed API: `NEXT_PUBLIC_RETAIL_OPS_API=<api base>` plus

| Env var | Turns on |
|---|---|
| `NEXT_PUBLIC_COGNITO_CLIENT_ID` | native username/password sign-in (`retail-ops-dev-staff-web` client) |
| `NEXT_PUBLIC_COGNITO_HOSTED_UI` | the Hosted-UI base used by the `/auth/callback` code exchange |
| `NEXT_PUBLIC_COGNITO_HOSTED_UI_CLIENT_ID` | the SSO app client (`retail-ops-dev-staff-hostedui`); falls back to the native client id |
| `NEXT_PUBLIC_SSO_IDPS` | **the SSO gate** — comma-separated *external* IdPs on the pool (e.g. `CorpDirectory`). One sign-in button per entry |

`deploy/deploy.sh --only console` discovers all of these from the pool and bakes
them, so you normally don't set them by hand.

**Why `NEXT_PUBLIC_SSO_IDPS` and not just "hosted UI is configured".** With no
external IdP the Hosted UI renders Cognito's own username/password form, so an
"SSO" button would just send the user to a second password prompt for the
credentials this page already collects. The gate is the presence of an external
directory; `COGNITO` is filtered out of the list. Each button pins
`identity_provider=<name>` so the user lands on that directory directly.

The button's label comes from the provider's name on the pool (`CorpDirectory`
renders as "Corporate Directory"; any other name is shown verbatim), so pointing the
console at a customer's directory needs no front-end change — name the provider
what you want users to read.

## Build and deploy

```bash
npm run lint:units                     # market-literal gate (also runs before every build) + agent-bar catalog check
RETAIL_OPS_STATIC_EXPORT=1 NEXT_PUBLIC_RETAIL_OPS_API=<api> npm run build   # → out/
python3 -m app.infra.deploy_console --apply                      # S3 + CloudFront (+ URL-rewrite function)
```

## Conventions (read before adding a screen)

**Design system.** Tokens are CSS variables per theme in `src/app/globals.css`
(§5.1 dealer dark verbatim, §5.9 OEM light, marketing, brand, neutral). The
shell sets `data-theme` per app. Component classes keep the reference HTML
names (`panel`, `rec-card`, `kpi-card`, `ai-insight`, `chat-panel`, …) from
`docs/use-cases/sales-advisor-v2.html` and `DM_Assistant_Demo.html`, so
a screen maps one-for-one to its reference. Tailwind utilities are themed via
the same variables (`text-fg-2`, `bg-surface/5`, `text-success`, …).

**Components.** Use `@/components/app` (primitives, Advisor components, KPI /
[AI INSIGHT], DataTable, Kanban, steppers, ChatPanel, AiLog, Drawer/Modal,
toasts, market formatters). Domain-specific components live in
`src/components/<domain>/`.

**Data.** `useApi(path, {pollMs})` for reads, `useMutation()` for writes, toasts
for every outcome, `<Async q={...}>` for loading / error / empty states.

**Market formatting.** Never write a currency symbol or unit. Wrap a subtree in
`<MarketProvider marketId=…>` and render `<Money/>`, `<Distance/>`,
`<Efficiency/>`, `<DateTime/>`. `npm run lint:units` fails the build otherwise.

**Links.** Deep links are canonical resource URLs (`resourceUrl("journey", id)`)
resolved per role by `resolve()` → `GET /nav/resolve`. Never hard-code another
screen's path in agent output, tasks, or notifications.

**Roles.** Navigation shows only screens the persona matrix
(`app/services/common/personas.py`, via `GET /me/home`) allows; `useCan(screen)`
for conditional UI. The API enforces role and scope server-side — the UI only
hides what the server would refuse.

**Agent bar.** `src/lib/agents.ts` holds each role's automations; admin roles
also list the chats and Run-now bindings their server routes accept. `npm run
lint:agents` (part of `lint:units`) checks every pre-existing role's bar against
`scripts/agent-catalog.baseline.json`, captured at 9cab45d before admin roles
had a bar, and the admin bars against the server role sets recorded in the
script. Re-capture the baseline only for an intended change to a staff bar.

**Dynamic routes under static export.** `src/app/x/[id]/page.tsx` is a server
file exporting `generateStaticParams() → [{ id: "_" }]` and
`dynamicParams = false`; it renders a client `View.tsx` that reads the real id
with `useRouteId(segment)`. CloudFront rewrites `/x/<id>` to the placeholder.

**Accessibility (§5.11).** Keyboard reachable controls, `aria-live` on objection
/ AI Log / chat, visible focus, WCAG AA contrast on the dark theme
(`--text-dim` is lifted for this), `prefers-reduced-motion` respected.

**Responsive (§5.19).** Shell collapses to a bottom tab bar ≤ 480px; tablet-first
technician screens use `btn-lg` touch targets.
