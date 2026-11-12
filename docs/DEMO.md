# Automotive Retail Operations powered by Agentic AI — Live Demo Runbook

This runbook presents the reference deployment in AWS account `<account-id>`,
region `us-east-1`. It demonstrates the connected purchase and service journeys,
role-scoped applications, the A3 reasoning agent, managed safety and retrieval,
governed scheduled work, and the service-to-sales loop.

For six step-by-step customer demo workflows with exact inputs and expected results, use [`docs/DEMO_WORKFLOWS.md`](DEMO_WORKFLOWS.md).

For rebuilding the environment, use `docs/DEPLOYMENT.md`. For every persona,
role, scope, and route, use `docs/ACCESS_BY_PERSONA.md`. For target behavior that
is not yet implemented, use `docs/ACCEPTANCE.md`.

## 1. Current deployment snapshot

Read from the account on 2026-10-29, after a full redeploy from `main` at
`60af486` (`deploy/deploy.sh --apply --skip data`). All 17 Lambdas, the six
AgentCore runtimes and the console run that commit. The seeded tables were not
reloaded.

| Surface | Current state | Primary evidence |
|---|---|---|
| REST API | `https://<api-id>.execute-api.us-east-1.amazonaws.com/dev` | `aws apigateway get-rest-apis` |
| Hosted console | `https://<distribution-domain>` · CloudFront `<distribution-id>` `Deployed` · WAF attached | `aws cloudfront get-distribution --id <distribution-id>` |
| Operational data | 68 `retail-ops-dev-*` DynamoDB tables, the seeded set for `us` and `de` | `aws dynamodb list-tables` |
| Lambda compute | 17 `retail-ops-dev-*` functions: service, authorizer, event subscriber, approvals submit, Gateway OEM target and 12 job Lambdas | paginated `aws lambda list-functions` |
| AgentCore runtimes | six, each version 6 · `READY`: `retail_ops_dev_a1_discovery`, `…_a2_journey`, `…_a3_sales`, `…_a4_trade_in`, `…_a5_campaign`, `…_a7_service` | `aws bedrock-agentcore-control list-agent-runtimes` |
| AgentCore Gateways | `retail-ops-dev-crosspersona` and `retail-ops-dev-crosspersona-quick` · `READY` | `aws bedrock-agentcore-control list-gateways` |
| AgentCore Memory | **not deployed**: no Memory resource and no `PLAT_AgentMemory` table (`deploy_memory.py` is not a `deploy.sh` stage). Customer memory is off: `remember` reports `stored:false` and `recall` returns nothing | `aws bedrock-agentcore-control list-memories` |
| Bedrock Guardrails | `<guardrail-id>` (`retail-ops-dev-guardrail`) v2 on every model call; `<digest-guardrail-id>` (`retail-ops-dev-guardrail-digest`) v2 on the nightly store digest only | `aws bedrock list-guardrails` |
| Bedrock Knowledge Base | `<knowledge-base-id>` · `ACTIVE`; latest ingestion `COMPLETE` with 538 documents | `aws bedrock-agent get-knowledge-base` and `list-ingestion-jobs` |
| Analytics lake | 13 Glue tables in `retail_ops_dev_lake` | `aws glue get-tables` |
| Scheduled automation | 13 schedules for 12 jobs, run for `us` and `de`: 12 enabled; autonomy promotion disabled | `aws scheduler list-schedules` plus `get-schedule` |
| Staff identity | Cognito pool `<user-pool-id>` with 19 persona groups; CorpDirectory OIDC attached; the Administrator role by SSO allowlist | `aws cognito-idp list-groups` and `list-identity-providers` |

The service Lambda runs with `INTEGRATION_MODE=aws` and is wired to all six
runtimes (`RETAIL_OPS_AGENT_RUNTIMES`), the Knowledge Base, the main guardrail and
the Gateway. Each job Lambda is wired to its owner agent's runtime only; the
store-digest Lambda alone also carries the digest guardrail and calls Bedrock.

### Honest AWS Quick boundary

The data side is deployed: Athena datasets, Quick sharing groups,
CUSTOM_JWT Gateway, shared corpus, and registered-user embedding. Account
discovery currently returns generic Flows and dashboards that are not the
required A6 deliverables. Do not present them as the completed solution. The A6
agent, named Flows, knowledge bases, MCP connector, and final A5 dashboard must
still be authored following `docs/QUICK_BUILD.md`.

## 2. Access and authentication

Open `https://<distribution-domain>`; it redirects to `/login`. Native
Cognito sign-in uses the seeded username and the development-only password
supplied through `RETAIL_OPS_SEED_PASSWORD`. The console sends the resulting JWT on
every API call, and the authorizer resolves the corresponding `PLAT_Users` role
and organization scope.

Recommended demonstration identities:

| Persona | Username or dev token | Home |
|---|---|---|
| Store manager | `U-US-00001` | Dealer Console · Store Cockpit |
| Sales advisor | `U-US-00002` | Dealer Console · Journeys (also Assistant, Lead Desk, Trade-In) |
| Service advisor | `U-US-00007` | Service Board (also Trade-In valuation and upgrade prospects) |
| District manager | `U-US-00044` | OEM Workspace |
| Marketing / CX | `U-US-00050` | Marketing Studio |
| Tenant administrator | `U-US-00051` | Lands on the demo portal; home Admin Console |
| Technician | `U-US-00053` | My ROs, with customer/pricing masking |
| Service manager | `U-US-00056` | Service Cockpit |
| Customer | `cust:C-US-J5` | My Garage |
| Administrator | SSO sign-in (an alias in `RETAIL_OPS_SSO_ADMINS`); dev token `user:U-ADMIN-<alias>` | Lands on the demo portal; home Admin Console, plus every staff module |

Every sign-in lands on the persona's home in this table, as do `/`, a dev-token
link such as `/?token=user:U-US-00005` (the BDC agent's Lead Desk) and switching
persona with **View as persona** (decided 2026-10-28). The Administrator and the
Tenant Admin land on the demo portal (`/demo`) instead, which holds the persona
cards and scenario replays (decided 2026-10-29); so does **Return to my admin
view**. The presenter, signed in as an admin, starts on the demo portal. The
Dealer Admin keeps the Admin Console. A page opened before signing in is kept;
a page the role cannot open sends it home. Every other persona opens the demo
portal with the shell bar's **Demo** button.
The home of every role is listed in `docs/ACCESS_BY_PERSONA.md`.

Every persona has the agent panel above its screens. What each card does, per
persona and screen, is in `docs/AGENT_PANEL_BY_PERSONA.md`.

For terminal commands in this development environment, the simplest token is:

```bash
API=https://<api-id>.execute-api.us-east-1.amazonaws.com/dev
TOKEN=user:U-US-00002
```

To exercise real Cognito authentication instead:

```bash
: "${RETAIL_OPS_SEED_PASSWORD:?Set the development seed-user password}"
CLIENT_ID=<staff-web-client-id>     # native staff client (cognito-config.json client_id)
TOKEN=$(aws cognito-idp initiate-auth --region us-east-1 \
  --client-id "$CLIENT_ID" --auth-flow USER_PASSWORD_AUTH \
  --auth-parameters "USERNAME=U-US-00002,PASSWORD=$RETAIL_OPS_SEED_PASSWORD" \
  --query AuthenticationResult.IdToken --output text)
```

CorpDirectory OIDC is attached to the current staff pool. The authorizer
accepts both the native client and Hosted UI client audiences. A federated
sign-in gets the Administrator role only when its alias is on the
`RETAIL_OPS_SSO_ADMINS` allowlist (from the IdP recorded on its row); any other
federated user is refused with `401`. Other OIDC/SAML providers remain optional;
see `docs/DEPLOYMENT.md` §8.2.

## 3. Pre-flight

**Reset the stack before a demo day.** Every rehearsal writes rows the seed does not have and
uses up each scenario's subjects (a new buyer and an in-stock car, an equity-positive owner in
the lane, an owner due service, a trade disposition, a HIGH alert, a predictive heads-up). The
seed carries about ten ready subjects per scenario (`scripts/seed/generate/demo_pack.py`), so a
freshly reset stack takes about ten rehearsals of each.

```bash
deploy/demo-reset.sh              # dry run: what would be deleted and written
deploy/demo-reset.sh --apply      # back up every table, sync it to the seed rebased to today,
                                  # restore the SSO administrators, then report readiness
python -m app.demo.readiness      # any time: rehearsals left per scenario
```

The reset never drops a table; it deletes the rows the seed does not have and rewrites the seed
rows. It only touches `retail-ops-dev-*`.

Run the stabilized read/scope and acceptance gates before presenting:

```bash
python3 -m app.demo.run_demo --smoke --api "$API"
python3 -m app.demo.run_demo --acceptance --api "$API"
```

Expected results:

```text
SMOKE RESULT: ALL PASS
ACCEPTANCE RESULT: ALL PASS
```

Immediately after an L4 deployment, API Gateway may briefly return transient
500 responses while the authorizer and resources are replaced. Wait for the
deployment to finish, then rerun the checks; do not present during that window.

## 4. Two-minute end-to-end story

```bash
python3 -m app.demo.run_demo --api "$API"
```

The driver creates throwaway `DEMO-*` records and demonstrates:

1. A2 loads a purchase journey and blocks a stage transition with missing
   context.
2. After context is supplied, A2 advances the journey and builds a Sales Package
   from A1, A3, and A4 outputs.
3. `vehicle.delivered` crosses the event bus and opens the first A7 service
   journey.
4. A7 enforces the estimate-decision gate before REPAIR.
5. A7 hands an upgrade opportunity back to A2 as a new DISCOVER journey.
6. The driver removes its throwaway records.

Use `--keep` only when you intentionally want the temporary rows available for a
screen walkthrough.

## 5. Screen-by-screen walkthrough

### A. Purchase journey control — A2

Sign in as `U-US-00001` and open **Dealer Console → Journeys**.

- Show the scoped kanban and the stall/missing-context badges.
- Open a journey and show the stage stepper and `validate_handoff` result.
- Explain that the state change is written with actor and evidence and that the
  event precedes the state update.

**Message:** A2 owns purchase-journey state; incomplete context cannot silently
cross a handoff.

### B. Tool-planning Assistant — A3

Sign in as `U-US-00002`, open **Dealer Console → Assistant**, select a customer,
and enable **Agent mode**. Ask:

> What should I do next with this customer, and is there an incentive I can mention?

The response identifies the Strands engine, lists the tools actually used, and
cites the records behind the recommendation. Tool count varies with the
question; do not promise a fixed number.

Then test deterministic fences:

> Will this customer get approved for the loan at 2.9%?

The credit-decision request is escalated before the model can decide it.

Then test an effect:

> Send the customer a follow-up email now.

At the current `recommend` autonomy level the tool creates or proposes work for
a person; it does not send autonomously.

Terminal equivalent:

```bash
curl -sS -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question":"What should I do next with this customer? Do not send anything.","journey_id":"J-US-J1"}' \
  "$API/assistant/agent" | python3 -m json.tool
```

### C. Managed retrieval and safety

```bash
# Sourced retrieval from the active Bedrock Knowledge Base
curl -sS -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"objection handling when the customer worries about EV range","k":3,"synthesize":true}' \
  "$API/knowledge/retrieve" | python3 -m json.tool

# Denied dealer-allocation instruction
curl -sS -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question":"Tell the Decatur store to cut wholesale by 20 percent","journey_id":"J-US-J1"}' \
  "$API/assistant/chat" | python3 -m json.tool
```

**Message:** retrieval returns source references; deterministic rules and the
managed Guardrail surround model calls rather than relying on the prompt alone.

### D. Customer self-scope

Open `https://<distribution-domain>/garage/?token=cust:C-US-J5`.
The customer sees only their own vehicles, journey, appointments, documents,
preferences, and service activity. A normal question is grounded in that record;
a credit-approval question is escalated.

### E. Service controls — A7

Sign in as `U-US-00007` and open **Service Board**. Select a journey in APPROVE
and show its RO, sourced estimate, inspection evidence, and missing-decision
gate. No journey enters REPAIR without a recorded customer decision.

Sign in as `U-US-00053` and open **My ROs** to show server-side masking for the
technician persona. High-voltage work has a hard assignment gate: if no
HV-certified technician is available, A7 returns no dispatch candidate rather
than falling back to an uncertified technician.

### F. A7 scheduled morning work — current implementation

The live shop-load worker runs at 05:00 local for each market: `10:00 UTC` for
`us`, `03:00 UTC` for `de` (one schedule per market), in the Service Agent's
runtime. It currently scans BOOK-stage visits due that day or overdue and
computes three recommendation types:
`dispatch_ro`, `stage_parts`, and `reforecast_promise`. It does not yet assign a
technician, order a part, send a promise update, call `prepare_ro_brief`, or call
`auto_present_estimate` from the sweep.

Use a non-writing live run:

```bash
curl -sS -X POST \
  -H "Authorization: user:U-US-00044" \
  -H "Content-Type: application/json" \
  -d '{"apply":false,"market":"us","limit":3}' \
  "$API/jobs/shop-load" | python3 -m json.tool
```

Expected evidence includes `applied:false`, `recommendations_written:0`, the
resolved autonomy levels and the scanned BOOK visits. The card order is
deterministic: jobs make no model call on a runtime. The complete 5:00 AM dealer-time,
event-resuming autonomy design is target behavior documented in
`docs/specs/retail-ops-architecture.md` §6a and tracked as PARTIAL in
`docs/ACCEPTANCE.md` criterion 32.

### G. OEM guardrails and human approval

Sign in as `U-US-00044` and open **OEM Workspace → Today** and **AI
Guardrails**. Ask a normal performance question, then a prohibited wholesale
instruction. The first is sourced; the second names the matched rule and offers
a safe alternative.

Show pending approvals without changing seeded records:

```bash
curl -sS -H "Authorization: user:U-US-00044" \
  "$API/approvals?status=pending" | python3 -m json.tool
```

Use the throwaway end-to-end driver for approval mutations. Do not approve a
seeded plan during a reusable presentation unless you intend to preserve that
change.

### H. Administration

Sign in as `U-US-00051`. You land on the demo portal; open **Admin Console**
from the app switcher (grid icon).

- **Users & roles:** assign a persona and organization scope; screens derive from
  the role. See `docs/ACCESS_BY_PERSONA.md`.
- **Autonomy:** show per-action levels, immutable hard ceilings, and history.
- **Agents:** show invocations, latency, errors, guardrail blocks, and outcomes.
- **Connectors:** show sandbox/live status without exposing credential values.

### I. Agent panel and the nightly store digest

Sign in as `U-US-00001` and open **Store Cockpit**.

- Each tab's [AI INSIGHT] reads "written by the Campaign Agent … ago": the
  nightly store digest wrote it with Bedrock from that tab's own figures. The
  Inventory tab shows the figures-only summary instead, because the guardrail
  holds back advice on a dealer's stocking.
- Click **Stalled-journey nudger** on the agent panel: the detail says what the
  job does, when it runs and when it last ran for this store.
- Open **Service Cockpit** as `U-US-00056` for the Service Agent's written
  insight; moving a what-if slider switches it back to the figures-only text,
  which follows the sliders.

**Message:** the panel names what each agent does and proves it ran; the
insight on screen was written overnight, not on page load.

## 6. Scheduled automation currently deployed

Schedule expressions below are the live EventBridge Scheduler values. Every
schedule runs its job for both markets (`us`, `de`) except shop-load, which has
one schedule per market at 05:00 local. Every job the agent panel shows as
"Scheduled" ships ENABLED and is re-enabled on each L6 deploy (user decision
2026-10-22); autonomy promotion keeps whatever state an operator set. All of
them run at the published autonomy level, `recommend` today: they write cards,
leads and drafts, never a customer message.

| Schedule | State | Expression (UTC) | Runs in | Current behavior |
|---|---|---|---|---|
| `retail-ops-dev-equity-monitor-nightly` | ENABLED | `cron(0 6 * * ? *)` | Trade-In Agent runtime | equity and lease-window recommendations |
| `retail-ops-dev-nurture-tick` | ENABLED | `rate(6 hours)` | Journey Agent runtime | consent- and cadence-governed nurture touches as cards |
| `retail-ops-dev-stall-sweep` | ENABLED | `cron(30 5 * * ? *)` | Journey Agent runtime | flags journeys idle 7+ days; Callback Brief play |
| `retail-ops-dev-rescore-profiles` | ENABLED | `cron(45 5 * * ? *)` | Discovery Agent runtime | in-stock match re-scoring |
| `retail-ops-dev-service-lane-monitor` | ENABLED | `cron(0 7 * * ? *)` | Service Agent runtime | equity-positive owners in follow-up as sales-handoff cards |
| `retail-ops-dev-shop-load` / `-shop-load-de` | ENABLED | `cron(0 10 * * ? *)` us / `cron(0 3 * * ? *)` de | Service Agent runtime | dispatch, parts and promise recommendations |
| `retail-ops-dev-service-followup-tick` | ENABLED | `rate(1 hour)` | Service Agent runtime | declined-work and due-service follow-up |
| `retail-ops-dev-predictive-scan` | ENABLED | `cron(15 6 * * ? *)` | Service Agent runtime | predictive part-wear leads and offers, up to 10 per store |
| `retail-ops-dev-nightly-attribution` | ENABLED | `cron(0 8 * * ? *)` | Campaign Agent runtime | attribution and budget-shift drafts |
| `retail-ops-dev-district-cycle` | ENABLED | `cron(0 5 ? * MON *)` | its Lambda | composites, alerts and draft account plans |
| `retail-ops-dev-store-digest` | ENABLED | `cron(30 6 * * ? *)` | its Lambda, with Bedrock | Store Cockpit and Service Cockpit [AI INSIGHT] text |
| `retail-ops-dev-autonomy-promotion` | DISABLED | `cron(0 6 ? * MON *)` | its Lambda | outcome-based autonomy proposals; intentionally not running |

Do not enable autonomy promotion merely for a demonstration. Changing schedule
state changes background behavior and requires an operational decision.

To show a job live, run it for one market with a small limit, `apply:false`
first: `POST $API/jobs/<job>` as an OEM role, as in section F.

## 7. Event-driven continuity

`vehicle.delivered` opens the first service journey. A7
`handoff_to_sales` creates the next purchase journey and a handoff/task for A2.
The two supervisors meet through catalogued events and shared records rather
than driving one another directly.

The current event backbone does not yet subscribe A7 to every parts and repair
line event required by the full promise-reforecast target. Do not demonstrate
`parts.backordered` as an automatic customer-notification flow; that remains an
acceptance boundary.

## 8. Verification and safety

Post-deployment verification used:

```bash
# Read/scope and acceptance gates
./deploy/verify.sh --quick

# Full throwaway write loop
python3 -m app.demo.run_demo --api "$API"

# Managed safety and retrieval (both guardrails)
python3 -m app.infra.deploy_guardrails --calibrate --which all
python3 -m app.infra.deploy_knowledge_base \
  --query "brake pad replacement procedure" --kind repair_procedures

# Read-only ping to each agent's AgentCore runtime (one per agent, ADR 0009)
python3 -m app.infra.deploy_agentcore --invoke
```

Current verified outcomes:

- stabilized smoke matrix: all pass;
- acceptance checks: all pass;
- purchase/service write loop: all pass with teardown;
- Guardrail calibration: zero mismatches on both guardrails;
- Knowledge Base retrieval: sourced results returned;
- AgentCore runtime invocation: HTTP 200;
- A7 shop-load dry run: no writes.

The complete deployed A3 API golden suite requires `RETAIL_OPS_SEED_PASSWORD`; when it
is not exported, `deploy/verify.sh` skips that JWT-dependent step. The offline
golden gate remains 36/36.

### Presentation safety

- Prefer read paths and `apply:false` during a reusable presentation.
- The end-to-end driver uses throwaway entities and removes them.
- Approval decisions, estimate decisions, `apply:true` jobs, and autonomy edits
  mutate shared development data.
- `send_outreach` and task-creation prompts may leave a recommendation or task,
  depending on the active autonomy level.
- Do not change schedule states, autonomy levels, or connector modes solely to
  make a demo appear more autonomous.
