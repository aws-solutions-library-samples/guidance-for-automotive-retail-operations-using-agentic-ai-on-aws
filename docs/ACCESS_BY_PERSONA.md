# Access and administration guide

A guide to reaching Automotive Retail Operations powered by Agentic AI by persona,
understanding role/scope enforcement, and administering staff access. For what
each persona's agent panel does on each screen, see
[AGENT_PANEL_BY_PERSONA.md](AGENT_PANEL_BY_PERSONA.md). Resolve
the deployed URLs and identity identifiers from the generated, git-ignored
`app/infra/*-config.json` files rather than copying identifiers between accounts.

## Endpoints

| Surface | URL |
|---|---|
| REST API base | printed by `deploy/deploy.sh`; derive from the deployed API identifier and region |
| Console (hosted) | `url` in `app/infra/console-config.json` after deployment |
| Console (local) | `cd app/console && NEXT_PUBLIC_RETAIL_OPS_API=<base> npm run dev` → http://localhost:3100 |

## How authentication works

**Real sign-in (hosted console).** Open the deployed console URL, which routes
to `/login`, and sign in with a seeded username plus the password supplied through
`RETAIL_OPS_SEED_PASSWORD`. The console calls Cognito `InitiateAuth`
(`USER_PASSWORD_AUTH`) against the deployed staff pool, stores the returned
ID token, and sends it as `Authorization: Bearer <jwt>` on every API call. The
authorizer verifies the JWT (signature, issuer, audience, and expiry) and maps
the username to the caller's `PLAT_Users` scope.

**Demo users** (all use the development-only password supplied through
`RETAIL_OPS_SEED_PASSWORD` during deployment):

| Username | Persona | Lands on |
|---|---|---|
| `U-US-00044` | District Manager (`oem_dm`) | OEM Workspace |
| `U-US-00002` | Sales advisor (`dealer_advisor`) | Dealer Console · Journeys |
| `U-US-00007` | Service advisor (`dealer_service`) | Dealer Console · Service |
| `U-US-00001` | Store manager (`dealer_manager`) | Dealer Console · Cockpit |
| `U-US-00050` | Marketing / CX (`oem_cx`) | Marketing Studio |
| `U-US-00051` | Tenant admin (`admin_tenant`) | Admin Console |
| `U-US-00053` | Technician (`dealer_technician`) | Dealer Console · My ROs (masked: no customer contact or pricing) |
| `U-US-00055` | Parts (`dealer_parts`) | Dealer Console · Parts desk |
| `U-US-00056` | Service manager (`dealer_service_manager`) | Dealer Console · Service Cockpit |

### Home screen and landing (decided 2026-10-28, amended 2026-10-29)

Every way into the console lands on the persona's home screen, the first entry
of its row in `app/services/common/personas.py` (served by `GET /me/home`):
username/password and SSO sign-in, opening `/`, a dev-token link without a path
(`/?token=user:<id>`), starting or ending **View as persona**, and My Garage for
customers. The exception is the admins: the Administrator (`platform_admin`)
and the Tenant Admin (`admin_tenant`) land on the demo portal (`/demo`) instead,
for sign-in, `/`, a dev-token link without a path and **Return to my admin
view** (decided 2026-10-29). The Dealer Admin (`admin_dealer`) is store-scoped
and keeps `/admin`, because the portal switches personas across the tenant. The
role list is `DEMO_LANDING_ROLES` in `app/console/src/lib/nav.ts`. An admin
using **View as persona** lands on that persona's home.

A page asked for before signing in (`/login?next=<page>`) is kept when the role
can open it, for admins too. A page in an app the role cannot open, or a page
outside the role's screens whose API refuses it, sends the user to their home
instead of an error. The app switcher opens each app on the role's home when it
is in that app, otherwise on the first screen of that app the role holds (for
the admins, Admin Console → `/admin`). Every other persona opens the demo portal
with the shell bar's **Demo** button, which every staff role sees.

| Persona (role) | Seed user | Home screen | Path |
|---|---|---|---|
| Sales advisor (`dealer_advisor`) | `U-US-00002` | Journeys (`dealer.journeys`) | `/dealer/journeys` |
| BDC agent (`dealer_bdc`) | `U-US-00005` | Lead Desk (`dealer.leads`) | `/dealer/leads` |
| F&I manager (`dealer_fni`) | `U-US-00006` | F&I Desk (`dealer.fni`) | `/dealer/fni` |
| Service advisor (`dealer_service`) | `U-US-00007` | Service Board (`service.board`) | `/dealer/service` |
| Technician (`dealer_technician`) | `U-US-00053` | My ROs (`service.my_ros`) | `/dealer/service/my-ros` |
| Parts counter (`dealer_parts`) | `U-US-00055` | Parts desk (`service.parts`) | `/dealer/service/parts` |
| Service manager (`dealer_service_manager`) | `U-US-00056` | Service Cockpit (`service.cockpit`) | `/dealer/service/cockpit` |
| Inventory manager (`dealer_inventory`) | `U-US-00008` | Inventory & Trade Desk (`dealer.inventory`) | `/dealer/inventory` |
| Delivery coordinator (`dealer_delivery`) | `U-US-00009` | Delivery Board (`dealer.delivery`) | `/dealer/delivery` |
| General manager (`dealer_manager`) | `U-US-00001` | Store Cockpit (`dealer.cockpit`) | `/dealer/cockpit` |
| District manager (`oem_dm`) | `U-US-00044` | OEM Today (`oem.today`) | `/oem` |
| Regional lead (`oem_regional`) | `U-US-00045` | Region View (`oem.region`) | `/oem/region` |
| C&I planner (`oem_ci_planning`) | `U-US-00046` | Accounts (`oem.accounts`) | `/oem/accounts` |
| OEM Sales Ops (`oem_sales_ops`) | `U-US-00047` | Sales Ops Comms (`oem.comms`) | `/oem/comms` |
| OEM finance (`oem_finance`) | `U-US-00048` | Marketing Studio › Campaigns (`marketing.campaigns`) | `/marketing/campaigns` |
| OEM marketing (`oem_marketing`) | `U-US-00049` | Campaigns (`marketing.campaigns`) | `/marketing/campaigns` |
| OEM CX (`oem_cx`) | `U-US-00050` | Journey Ops (`marketing.journey_ops`) | `/marketing/journey-ops` |
| Tenant admin (`admin_tenant`) | `U-US-00051` | Admin Console (`admin.home`) | `/admin`; lands on `/demo` |
| Dealer admin (`admin_dealer`) | `U-US-00052` | Admin Console (`admin.home`) | `/admin` |
| Administrator (`platform_admin`) | `U-ADMIN-<alias>` (SSO allowlist) | Admin Console (`admin.home`) | `/admin`; lands on `/demo` |
| Customer | `cust:C-US-J1` | My Garage (`customer.garage`) | `/garage` |

**SSO sign-in (the enterprise IdP) and the Administrator.** When the pool has the
the enterprise IdP provider, the login page shows an SSO button. A federated
sign-in works only for an identity that has a `PLAT_Users` row; anyone else gets
`401 unknown user`. The IdP aliases listed in `RETAIL_OPS_SSO_ADMINS`
(git-ignored `deploy/config.env`, e.g. `export RETAIL_OPS_SSO_ADMINS=jdoe`)
get the **Administrator** role (`platform_admin`): every staff app (Dealer
Console, OEM Workspace, Marketing Studio, Admin Console), every district and
dealer of the deploy market. The customer app (My Garage, `/shop`) is not
included. The deploy stage `sso-admins` writes their rows as `U-ADMIN-<alias>`;
re-run it after editing the list:

```bash
source deploy/config.env
deploy/deploy.sh --apply --only sso-admins     # adds listed aliases, removes delisted ones
```

Aliases are matched case-insensitively and an `@corp.example.com` suffix is ignored.
This assumes Federate's `sub` claim is the IdP alias. Username/password
sign-in and all personas above are unchanged.

**Dev token (fallback for curl / scripts).** The API also still accepts a plain
dev token, used by the demo harness and handy for curl. It is only a fallback —
the console prefers a real JWT session:

- **Staff**: `Authorization: user:<user_id>` · **Customer**: `Authorization: cust:<customer_id>`

```bash
BASE=https://<api-id>.execute-api.<region>.amazonaws.com/dev
CLIENT_ID=$(python3 -c "import json; print(json.load(open('app/infra/cognito-config.json'))['client_id'])")
: "${RETAIL_OPS_SEED_PASSWORD:?Set the development seed-user password}"
# real JWT (what the console sends):
JWT=$(aws cognito-idp initiate-auth --region <region> \
  --client-id "$CLIENT_ID" --auth-flow USER_PASSWORD_AUTH \
  --auth-parameters "USERNAME=U-US-00044,PASSWORD=$RETAIL_OPS_SEED_PASSWORD" \
  --query 'AuthenticationResult.IdToken' --output text)
curl -s -H "Authorization: Bearer $JWT" "$BASE/oem/today"
# dev-token fallback:
curl -s -H "Authorization: user:U-US-00044" "$BASE/oem/today"
```

> The dev token can be disabled in a locked-down environment by setting
> `RETAIL_OPS_ALLOW_DEV_TOKEN=0` on the authorizer Lambda; real JWT sign-in continues
> to work.

## Administer users, roles, and screen access

Screen access is role-derived. Administrators assign a persona role and an
organization scope; `app/services/common/personas.py` maps that role to its home
and permitted screens. The console does not offer arbitrary per-screen grants,
because screen visibility without matching API authorization would be unsafe.

### Tenant administrator

1. Sign in as `admin_tenant` and open **Admin → Users & roles**.
2. Search for the user and choose **Edit**.
3. Select the user's **Group (persona)**. This determines the home screen and
   effective screen set.
4. Select the **Scope node**—OEM, region, district, dealer group, or dealer.
5. Save. The API validates the role and scope, writes `PLAT_Users`, and records
   an admin audit event. Re-run the identity deployment when Cognito group
   membership also needs synchronization.

A tenant administrator may assign any staff persona and move a user anywhere in
the tenant hierarchy. A user cannot deactivate their own administrator account.
The Administrator (`platform_admin`) administers the same way. It is not one of
the 19 assignable groups: nobody can be given it here, and the `U-ADMIN-*` rows
can be changed only through `RETAIL_OPS_SSO_ADMINS` and the `sso-admins` stage
(the API answers `403` to an edit).

### Dealer administrator

A dealer administrator sees only users in their dealer subtree. They may assign
permitted dealer roles and move users only within their stores. The API rejects
OEM roles, `admin_tenant`, users outside the caller's stores, and out-of-scope
organization nodes even if a client bypasses the UI.

Use **Deactivate** to remove access while preserving history, tasks, handoffs,
and audit records. Screen links are filtered by role in the shell, while every
API call independently enforces role and organizational scope.

Equivalent API update:

```bash
curl -X PATCH "$BASE/admin/users/<user-id>" \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"groups":["dealer_service"],"scope_node_id":"<dealer-id>"}'
```

## Personas

Each row: the persona, a demo token that works today, the console screens they
land on, and the primary API routes their role may call. Roles are enforced —
a token outside a screen's allowed roles gets `403`.

### Dealer personas (retail sales + service floor)

| Persona (role) | Demo token | Console screens | Key routes |
|---|---|---|---|
| Sales advisor (`dealer_advisor`) | `user:U-US-00002` | **Journeys** (home), Advisor, **Assistant (Agent mode)**, Discovery, Trade-In, **Lead Desk** (sidebar and Assistant tab, decided 2026-10-28) | `GET /journeys/board`, `/journeys/{id}/*`, `POST /assistant/chat`, **`POST /assistant/agent`** (Strands reasoning agent), `POST /assistant/flows/{flow}`, `POST /knowledge/retrieve`, `GET /leads/queue`, `POST /leads` (intake), `POST /leads/{id}/respond` · `/assign` · `/appointment`, `POST /handoffs/{id}/accept`, `GET /traces?lead_id=` |
| Store manager / GM (`dealer_manager`) | `user:U-US-00001` | Journeys, **Cockpit**, Delivery, Lead Desk | `GET /dealer/cockpit`, `/dealer/delivery`, `/journeys/*` |
| BDC (`dealer_bdc`) | `user:U-DE-00023` | **Lead Desk** (home), Journeys | `GET /journeys/board`, `GET /leads/queue`, `POST /leads` (intake) · `/respond` · `/assign` · `/appointment` |
| F&I (`dealer_fni`) | `user:U-US-00024` | Journeys | `GET /journeys/{id}/*` (finance context) |
| Delivery (`dealer_delivery`) | `user:U-US-00027` | Delivery | `GET /dealer/delivery`, `POST /dealer/delivery/{id}/deliver` |
| Service advisor (`dealer_service`) | `user:U-US-00007` | **Service Board** (home), **Trade-In** (valuation and upgrade prospects, decided 2026-10-28; adding the trade to a purchase journey stays with sales), Assistant › Service Lane | `GET /service/board`, `/service/{id}/*`, `POST /service/{id}/advance`, `POST /trade-in/valuation`, `GET /trade-in/prospects` |

Dealer personas are scoped to **their own dealer(s)** — they see only journeys,
leads, and service jobs for dealers in their org subtree. To read **OEM-owned**
data (accounts, alerts, C&I programs) they must go through the AgentCore Gateway
route, never OEM screens directly:

```bash
# allowed: cross-persona OEM read via the Gateway (scoped + field-stripped)
curl -s -H "Authorization: user:U-US-00002" "$BASE/gateway/oem/accounts?market_id=us"
# refused: a dealer hitting an OEM screen directly -> 403
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: user:U-US-00002" "$BASE/oem/today"
```

### OEM personas (field sales / marketing / analytics)

| Persona (role) | Demo token | Console screens | Key routes |
|---|---|---|---|
| District manager (`oem_dm`) | `user:U-US-00044` | **OEM**, Campaigns, Quick | `GET /oem/today`, `/oem/alerts`, `/oem/campaigns`, `POST /oem/accounts/{id}/draft-plan` |
| Regional manager (`oem_regional`) | `user:U-US-00045` | OEM, Campaigns, Quick | `GET /oem/*` (region scope), `POST /approvals/plan/{id}/decide` |
| C&I planning (`oem_ci_planning`) | `user:U-DE-00046` | OEM, Campaigns | `GET /oem/*`, incentive routes |
| OEM Sales Ops (`oem_sales_ops`) | `user:U-US-00047` | **Sales Ops Comms** (home), Report Library; no Admin Console (decided 2026-10-28: every admin route serves the admin roles only) | `GET /oem/today`, `POST /knowledge/retrieve`, `POST /oem/comms/forward` |
| Marketing (`oem_marketing`) | `user:U-DE-00049` | Campaigns, Quick | `GET /oem/campaigns`, Quick dashboards |
| OEM finance / CX (`oem_finance` / `oem_cx`) | `user:U-US-00050` | OEM, Campaigns | `GET /oem/*` (read) |

OEM roles read **across dealers** (documented read-across) and reach the Amazon
Quick screen (Flows + Quick Sight dashboards) when `ENABLE_QUICK=true`.

**Agent bar for the OEM roles (user decision 2026-10-22).** Every persona has the
agent bar. The district manager and regional lead see the Field Sales Agent's
weekly district cycle, alert triage, account-plan drafter, Meeting in a Box and
District Manager Assistant; the C&I planner the plan drafter, scenario planner
and assistant; sales ops the comms digest and assistant; finance the Campaign
Agent's budget-shift drafter, the plan drafter and the assistant. The Field
Sales Agent's chat is `POST /oem/ask`, the same route as the OEM Workspace
assistant; finance also chats with the Campaign Agent (`POST /marketing/ask`).
The bar adds no Run-now on OEM screens, and no server access rule changed.

### Platform / customer

| Persona (role) | Demo token | Console screens | Key routes |
|---|---|---|---|
| Tenant admin (`admin_tenant`) | `user:U-US-00051` | **Admin** (autonomy, guardrails, config); agent bar (view) | `GET/POST /admin/autonomy`, `/admin/*`; agent bar: `POST /knowledge/retrieve`, `GET /admin/agents/health` |
| Dealer admin (`admin_dealer`) | `user:U-DE-00052` | Admin (dealer-scoped); agent bar (view, store agents) | `/admin/*`; agent bar: `POST /knowledge/retrieve` |
| Administrator (`platform_admin`, SSO allowlist only) | SSO sign-in; `user:U-ADMIN-<alias>` once the `sso-admins` stage has run | **Admin**, plus every Dealer, OEM and Marketing screen; agent bar (every agent) | every staff read route; every tenant-admin write (`/admin/*`, approvals decide, autonomy); persona actions through **View as persona**; agent bar: `POST /knowledge/retrieve`, `POST /marketing/ask`, `GET /admin/agents/health` |
| Customer (garage self-service) | `cust:C-US-J5` | Garage (own vehicles/journey) | `GET /garage` only |

The Administrator reads every module but does not perform persona actions
directly: drafting or submitting account plans, budget shifts, contact reports,
alert actions, Journey Ops edits, and dealer-floor writes (journeys, leads,
outreach, F&I, delivery, inventory, service desk) answer `403`. Use **View as
persona** for those; every request made that way is audited with the
Administrator's own id. Deal documents (`GET /documents`, sensitive C4 data)
stay with F&I, delivery, advisor and GM. The Administrator is a platform
operator at the Gateway boundary: it reads OEM data directly, while dealer
personas (including an Administrator viewing as one) still go through the Gateway.

**Agent bar for admins (user decision 2026-10-22).** The agent bar above every
screen also shows for the three admin roles, as a view. The Administrator and
the tenant admin see every staff automation of the seven agents (Discovery,
Journey, Sales, Trade-In, Campaign, Field Sales and Service Agent); the dealer
admin sees the store ones. Chat shows only where the chat route accepts the
role: the Service Agent (`POST /knowledge/retrieve`) for all three, and the
Campaign Agent (`POST /marketing/ask`) and Field Sales Agent (`POST /oem/ask`)
for the Administrator. Discovery, Journey, Sales and
Trade-In Agent chat (`POST /assistant/chat`) stays with the dealer roles. Run
now shows only where the page offers it and the route accepts the role. For the
Administrator that is the Service Cockpit's Threshold what-if recompute; the
tenant and dealer admins get none. "Last ran" comes from `PLAT_AgentTraces` for
the journey or repair order the page has open. Without one, the Administrator
and the tenant admin read the Admin › Agents feed (`GET /admin/agents/health`),
and every staff role, the dealer admin included, reads each automation's named
runs (`GET /traces/latest`). For anything the bar withholds, it points to
**View as persona**. No server access rule changed.

**What every automation really does (agent-panel honesty audit, user decision
2026-10-22).** Each of the 51 automations on the bar says what runs today.
*Live* ones run with no click: when their screen loads (vehicle matcher, deal
worksheet, inventory demand, fixed-ops watch, …) or on the event named (survey
on mark delivered, evidence check on inspection submit). *Scheduled* ones are
jobs on an enabled schedule, for both markets: the stalled-journey sweep, nurture
tick, build re-scorer, equity monitor, shop load (morning dispatch and promise
re-forecast), declined-work follow-up, service-lane repurchase monitor,
predictive part watch, budget-shift drafts, the weekly district cycle and the
nightly store digest. They run at the tenant's autonomy level, which today is
`recommend` for all of them: they write recommendation cards, leads and drafts,
never customer messages. The store digest is the one job that calls Amazon
Bedrock, through its own guardrail (the main guardrail's topics and filters,
with a relaxed grounding check): it writes the Store Cockpit tab insights (Campaign Agent) and the
Service Cockpit insight (Service Agent), which the cockpits show with who wrote
them and when, falling back to the figures-only summary. *On demand* ones run
when you press their button, and that request is recorded as the agent's run.
"Last ran" shows the date when the run was not today. Two automations had
nothing behind them and were retired: Supersession finder (no supersession data
exists) and Goodwill triage (nothing raises a goodwill request).
`GET /traces/latest?runs=` is staff-only and returns, per run name, the newest
run in the caller's market — from the caller's own stores, plus market-wide
scheduled runs.

## Console UI — five apps, one Unified Shell (hosted)

The deployed console is the specification's five apps joined by one shell bar
(distinct from the API routes in the tables above — those are backend endpoints):

| App (theme) | Console UI route | Screens | Who the app switcher offers it to |
|---|---|---|---|
| Dealer Console (dark) | `/dealer/journeys` `/dealer/cockpit` `/dealer/service` `/dealer/leads` `/dealer/delivery` | Journeys, Store Cockpit, Service Desk, Lead Desk, Delivery | dealer roles; OEM/admin (read); Administrator |
| OEM Workspace (light) | `/oem` `/oem/campaigns` `/oem/quick` | Today/Alerts/Scenario, Campaign ROI, Amazon Quick | OEM roles; admin; Administrator |
| Marketing Studio (light) | `/marketing/campaigns` `/marketing/quick` | Campaign ROI, Quick dashboards | `oem_cx`/marketing, manager, admin; Administrator |
| Customer Web (brand) | `/garage` | My Garage | customer (`cust:` token) only |
| Admin Console (neutral) | `/admin` | Autonomy, guardrails, connectors | `admin_tenant` / `admin_dealer` / Administrator (`platform_admin`) |

Open any route with `?token=<token>`; the shell reads it, the API enforces it.
`/?token=<token>` opens that persona's home (the demo portal for the
Administrator and the Tenant Admin), and a route in an app the persona cannot
open sends it home (see *Home screen and landing* above).
The app switcher (grid icon) lists only the apps the current persona may open.
(The pre-shell flat routes `/journeys`, `/cockpit`, … now redirect to the app
shell.)

## Notes

- **Markets:** `US` and `DE` are seeded. US users are `U-US-*`, German users
  `U-DE-*`; a user only sees their own market. The Administrator's home market
  is the deploy `--market` (default `us`); its dealer-scope checks pass in any
  market, and market-aware views take `?market=`.
- **403 is expected** when a token is used outside its role/scope — that is the
  access model working, not a bug (e.g. advisor → `/oem/today` = 403). In the
  console the user never sees it: a page whose API refuses the role sends it to
  its home screen (e.g. the service advisor on `/dealer/leads` lands on
  `/dealer/service`).
- **Full user list:** `aws dynamodb scan --table-name retail-ops-dev-PLAT_Users
  --region us-east-1 --query 'Items[].{u:user_id.S,g:groups.L[0].S,scope:scope_node_id.S}'`.
- **Health check across personas:** `python3 -m app.demo.run_demo --smoke --api $BASE`
  exercises every screen/role (and the negative 403 cases) in one run.
