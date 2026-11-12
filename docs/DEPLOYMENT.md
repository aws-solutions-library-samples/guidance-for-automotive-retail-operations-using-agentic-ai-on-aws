# Deployment guide

Stand up the Automotive Retail Operations powered by Agentic AI reference implementation
in your own AWS account, verify it, operate it, and take it down. Everything in
this guide is scripted except five AWS Quick console steps that have no API
(section 8.1); a first deployment takes about 30 minutes, most of it waiting for AWS.

> Resource names use the prefix `retail-ops-<env>-` (an internal abbreviation, not a
> product name). `<env>` is the environment name: `--env <name>` on the scripts or
> `RETAIL_OPS_ENV`, default `dev` (2-8 lowercase letters and digits;
> `app/infra/naming.py`). The examples below show `dev`. The scripts target one
> account and one region at a time.
>
> **Supported deployment environment: Linux** (tested on Amazon Linux 2023 x86_64,
> the CodeBuild image `aws/codebuild/amazonlinux-x86_64-standard:5.0`); macOS works
> for local development. The one-click path is `deploy/guidance-deploy.sh`; the
> step-by-step commands are "Deploy the Guidance" in the root `README.md`.

## 1. What you will get

| Layer | Resources |
|---|---|
| Identity | Cognito user pool `retail-ops-dev-staff` (19 persona groups, seed users), a client-credentials client for AWS Quick |
| Data | 68 DynamoDB tables loaded from the synthetic packs; S3 event lake with Firehose → Glue → Athena (13 tables); the 538-document knowledge corpus in S3 |
| AI | Bedrock Guardrails (the main one on every call, and the store digest's own copy with a relaxed grounding check); Bedrock Knowledge Base on S3 Vectors; AgentCore runtimes, one per custom agent (A1, A2, A3 with the Strands agent, A4, A5, A7; ADR 0009); two AgentCore Gateways (console and AWS Quick) |
| Compute | REST API (API Gateway) with a token authorizer and one service Lambda; event subscriber Lambda; approvals Step Functions; twelve scheduled job Lambdas; CloudWatch alarms |
| Console | Next.js static site on CloudFront (private S3 origin, WAF) |
| Analytics | AWS Quick datasets over the lake and the Quick sharing groups (not row-scoped) |

Architecture and flows: `docs/as-built/architecture.md`, `docs/as-built/agent-flows.md`.

## 2. Prerequisites

**Accounts and permissions**

* An AWS account you can create resources in (IAM roles, Lambda, DynamoDB,
  S3, API Gateway, EventBridge, Step Functions, Cognito, Bedrock, Bedrock
  AgentCore, S3 Vectors, Glue, Athena, Firehose, CloudFront, WAF, Secrets
  Manager, CloudWatch, QuickSight). Administrator access is the simplest for a
  first deployment; the deployers themselves create least-privilege roles for
  the runtime components.
* **Region `us-east-1`** (the deployers' default; other regions are untested).
* **Amazon Bedrock model access** for Claude Opus 4.5, Claude Sonnet 4.5 and
  Titan Text Embeddings V2. `enable-bedrock.py` at the repository root grants it
  through the API (availability check, Anthropic's one-time use case form, the
  model agreement) and is idempotent:
  `python3 enable-bedrock.py 'anthropic.claude-opus-4-5-20251101-v1:0' 'us-east-1'`,
  then the same for `anthropic.claude-sonnet-4-5-20250929-v1:0` and
  `amazon.titan-embed-text-v2:0`.
* **AWS Quick / QuickSight** subscription in the account (Enterprise) if you
  want the analytics stage; the rest of the stack does not depend on it.

**Workstation**

* Python 3.12 with `venv` and `pip` (Amazon Linux 2023:
  `sudo dnf install -y python3.12 python3.12-pip`), and the AWS CLI v2 with credentials for the
  target account (`aws sts get-caller-identity` must work). Optional settings
  (region, SSO provider, WAF allowlist, models) go in `deploy/config.env`
  (copy `deploy/config.env.example`).
* Node.js 18 or newer (20 tested) and npm (console build).
* A Linux shell (bash 5) for deployment; macOS works for development. The
  scripts run unattended (no prompts, no pager) and stop at the first failing
  stage. The AgentCore bundle is built for Linux arm64 by pip's cross-platform
  wheel resolution, so no Docker or arm64 machine is needed.

```bash
git clone <repo-url> automotive-retail-operations
cd automotive-retail-operations
python3.12 -m venv .venv && source .venv/bin/activate
python3 -m pip install -r requirements-agentcore.txt "pytest>=8,<9"
(cd app/console && npm ci)
cp deploy/config.env.example deploy/config.env
# Edit deploy/config.env and set a unique development-only RETAIL_OPS_SEED_PASSWORD.
source deploy/config.env
aws sts get-caller-identity          # confirm you are in the intended account
```

## 3. First deployment (one command)

On a Linux machine (or AWS CodeBuild) with credentials for a fresh account,
`deploy/guidance-deploy.sh` does everything without input: it installs Python
3.12 if missing, clones the repository, creates `.venv`, enables the Bedrock
models, installs the console dependencies, generates the seed password, deploys
under a generated environment name with `--skip quick`, runs `deploy/verify.sh`,
switches the development token off, and prints the console URL, the API base
and the environment name (`GUIDANCE_ENV_NAME=…`). Running it in CodeBuild:
`deploy/guidance-validation/README.md`.

With a prepared workstation (section 2), the same deployment step by step:

`deploy/bootstrap.sh` is the front door for a fresh account: it checks the
tools, shows you the account it is about to use, publishes the optional JWT
layer, runs every stage in order, and verifies the result.

```bash
deploy/bootstrap.sh                       # DRY RUN: prerequisites + the account it would deploy to; changes nothing
deploy/bootstrap.sh --yes --enable-jobs   # deploy everything; also turn autonomy-promotion on (dev/demo)
```

Add `--markets us,de` to also load the German market, `--skip quick` if the
account has no AWS Quick subscription, or `--env <name>` to deploy a
separately named environment next to another one.

The last lines print the **API base URL** and the **console URL**. Keep both.

What the jobs do: every scheduled job the agent bar shows ships enabled, for
both markets (user decision 2026-10-22: the bar never claims a schedule that
does not fire). They run at autonomy level `recommend`, which writes
recommendation cards, leads, drafts and events but never messages a customer.
`store-digest` is the one job that calls Amazon Bedrock (the cockpit insights:
about 120 short calls a night per market plus one retry for each insight the
grounding check rejects, none for a store whose figures did not change). It
carries its own guardrail, `retail-ops-dev-guardrail-digest`: every policy of
the main one with the grounding check relaxed (ADR 0003 amendment); an insight
it blocks shows the figures-only text. `--enable-jobs` also enables `autonomy-promotion`. For a quiet
account run `python3 -m app.infra.deploy_l6 --apply --disable-writers` after the
deploy (it lasts until the next `l6` deploy) and run jobs on demand (section 6).

## 4. Stage-by-stage (when you want control)

`deploy/deploy.sh` runs the stages in dependency order. Every stage is
idempotent and dry-runs unless `--apply` is given.

```bash
deploy/deploy.sh                          # dry-run everything
deploy/deploy.sh --apply                  # deploy everything
deploy/deploy.sh --apply --only l4,console
deploy/deploy.sh --apply --skip data,quick
deploy/deploy.sh --apply --enable-jobs
```

| Order | Stage | Deployer | Creates | Depends on |
|---|---|---|---|---|
| 1 | `cognito` | `cognito_setup.py` | user pool, 19 groups, seed users (password supplied through `RETAIL_OPS_SEED_PASSWORD` and never written to generated config), Hosted-UI domain, the `retail-ops-quick-mcp` client-credentials client (secret in Secrets Manager `retail-ops-dev-quick-mcp-oauth`) | — |
| 2 | `data` | `l1_data/ddb_load.py` | 68 tables + rows from `sample-data/synthetic/us` | — |
| 3 | `sso-admins` | `sso_admins.py` | Administrator rows (`U-ADMIN-<alias>`, role `platform_admin`) in `PLAT_Users` for the aliases in `RETAIL_OPS_SSO_ADMINS`; removes delisted ones, never touches other rows; no change when the variable is unset | data |
| 4 | `events` | `deploy_events.py` | bus `app-bus-dev`, subscriber Lambda, rule, DLQ | — |
| 5 | `lake` | `deploy_lake.py`, `deploy_lake_analytics.py`, `deploy_lake_rollups.py` | lake bucket, Firehose, Glue db + 13 tables, Athena workgroup, derived roll-ups | events, data |
| 6 | `corpus` | `deploy_quick_index.py` | 538 Markdown docs under `quick-index/` in the lake bucket | lake |
| 7 | `guardrails` | `deploy_guardrails.py` | Bedrock Guardrails `retail-ops-dev-guardrail` and `retail-ops-dev-guardrail-digest` (`--which main\|digest\|all`), each with a published version | — |
| 8 | `kb` | `deploy_knowledge_base.py` | S3 Vectors bucket/index, KB role, Knowledge Base, data source, sidecars, ingestion | corpus |
| 9 | `l4` | `deploy_l4.py`, `iam_roles.py` | per-function IAM roles, authorizer + service Lambdas, REST API; wires guardrail / KB / gateway / runtime ids into env; `ENABLE_QUICK=true` on the service when the account has AWS Quick, and the console origin as the Quick embed domain (`RETAIL_OPS_QUICK_EMBED_DOMAINS`) and as the API's CORS origin (`RETAIL_OPS_CONSOLE_ORIGIN`; re-run `l4` once after the first `console` stage) (§8.1); `RETAIL_OPS_SES_FROM` from `deploy/config.env` as the outreach sender | cognito, guardrails, kb |
| 10 | `approvals` | `deploy_approvals.py` | Step Functions state machine + submit Lambda | l4 |
| 11 | `l6` | `deploy_l6.py` | twelve job Lambdas + schedules for both markets (the agent-bar jobs ship ENABLED; `--enable-jobs` adds autonomy-promotion) | data |
| 12 | `alarms` | `deploy_alarms.py` | Errors + Throttles alarm per Lambda; `runtime_job_unknown` metric filter + alarm per agent-owned job Lambda | l4, l6 |
| 13 | `agentcore` | `deploy_agentcore.py` | shared exec role, one code bundle per agent (arm64 wheels), six runtimes `retail_ops_dev_a1_discovery`, `…_a3_sales`, `…_a4_trade_in`, `…_a5_campaign`, `…_a7_service`, then `…_a2_journey` (ADR 0009); then re-runs `l4` and `l6` to wire `RETAIL_OPS_AGENT_RUNTIMES` | l4, l6 |
| 14 | `gateway` | `deploy_gateway.py`, `deploy_gateway_quick.py` | OEM-read Lambda, AWS_IAM gateway (console), CUSTOM_JWT gateway (AWS Quick) | cognito, l4 |
| 15 | `quick` | `deploy_quick_groups.py`, `deploy_quick_datasets.py`, `deploy_quick_content.py`, `deploy_quick.py` | Quick sharing groups + memberships, Athena data source + datasets (owned by `retail-ops-builders`); then the Quick content: two custom-SQL datasets, topic, 9 knowledge bases, space, Field Sales chat agent, 8 Flows, Campaign ROI dashboard, each shared with the groups (§8.1); then discovery of Flows and dashboards for the console | lake, corpus, gateway |
| 16 | `console` | `deploy_console.py` | static build against the live API, S3 bucket, CloudFront + OAC | l4, cognito |
| 17 | `waf` | `deploy_waf.py` | WAFv2 web ACL on the distribution | console |

Each deployer writes its handles to `app/infra/<name>-config.json`; later stages
read them. If you run a stage before its prerequisite, it reports what is
missing and the wiring completes on the next run of the dependent stage.

Useful single-deployer commands:

```bash
python3 -m app.infra.deploy_guardrails --calibrate          # 21-prompt allow/deny check; expect 0 mismatches
python3 -m app.infra.deploy_guardrails --calibrate --which digest   # the same check on the store digest's guardrail
python3 -m app.infra.deploy_knowledge_base --query "brake pad replacement" --kind repair_procedures
python3 -m app.infra.deploy_agentcore                       # dry run: one line per agent runtime, nothing changes
python3 -m app.infra.deploy_agentcore --invoke              # read-only ping to each agent runtime
python3 -m app.infra.deploy_l4 --disable-agents A2 --apply  # run that agent in-process again (codes or all)
python3 -m app.infra.deploy_l6 --disable-agents all --apply # same for the agent-owned job Lambdas
python3 -m app.infra.deploy_l4 --enable-agents A2 --apply   # undo; deploy_l6 takes the same flags
python3 -m app.infra.deploy_l6 --run-once stall-sweep       # run one job now
python3 -m app.infra.deploy_l6 --apply --disable-writers    # quiet the writer schedules again
python3 -m app.infra.cognito_setup --print-config           # pool, client, seed users, Quick client
```

## 5. Verify

One command runs every check below (smoke, acceptance, write loop, demo workflow 7
end to end, agent eval through the API, guardrail calibration, Knowledge Base query,
runtime and schedule status) and exits non-zero on any failure:

```bash
deploy/verify.sh            # everything (~3 min)
deploy/verify.sh --quick    # smoke + acceptance only (~30 s)
```

The individual checks, if you want to run them by hand:

```bash
API=https://<id>.execute-api.us-east-1.amazonaws.com/dev     # printed by the deploy

python3 -m app.demo.run_demo --smoke --api $API        # read/scope matrix (expect ALL PASS)
python3 -m app.demo.run_demo --acceptance --api $API   # asserted acceptance criteria (expect ALL PASS)
python3 -m app.demo.run_demo --api $API                # full write loop with throwaway DEMO-* rows, torn down
python3 -m app.demo.run_demo --workflow7 --api $API    # demo workflow 7: one buyer, web lead to delivery, under each
                                                        # persona (BDC, advisor, F&I, delivery, the buyer); a throwaway
                                                        # buyer, every row removed and the reserved unit put back (--keep to inspect)

# the reasoning agent through the deployed API (needs a real sign-in token)
: "${RETAIL_OPS_SEED_PASSWORD:?Set the development seed-user password}"
TOKEN=$(aws cognito-idp initiate-auth --auth-flow USER_PASSWORD_AUTH \
  --client-id $(python3 -c "import json;print(json.load(open('app/infra/cognito-config.json'))['client_id'])") \
  --auth-parameters "USERNAME=U-US-00002,PASSWORD=$RETAIL_OPS_SEED_PASSWORD" \
  --query AuthenticationResult.IdToken --output text)
python3 -m app.eval.run_eval --mode api --api $API --token "$TOKEN"   # expect 15/15
```

Offline checks that need no AWS (also what CI runs):

```bash
python3 -m pytest tests -q                       # 752 tests
python3 -m app.eval.run_eval --mode offline      # 36 golden prompts, must be 100 %
(cd app/console && npm run lint:units && npx tsc --noEmit -p .)
```

The smoke, acceptance and loop checks use the plain development tokens
(`user:<id>`), which the authorizer accepts by default. Once verified, refuse
them so only Cognito sign-in works; later `l4` deploys keep the setting:

```bash
python3 -m app.infra.deploy_l4 --dev-token off --apply    # --dev-token on to accept them again
```

Sign in to the console URL with any seed user. They share the development-only
password supplied through `RETAIL_OPS_SEED_PASSWORD`: `U-US-00002`
advisor, `U-US-00007` service advisor, `U-US-00001` store manager,
`U-US-00044` district manager, `U-US-00050` marketing, `U-US-00051` admin,
`U-US-00053` technician, `U-US-00055` parts, `U-US-00056` service manager.

## 6. Operate

**Redeploy after a code change**

```bash
deploy/deploy.sh --apply --only l4            # service / authorizer code
deploy/deploy.sh --apply --only l6            # job code (re-enables the agent-bar jobs; autonomy-promotion keeps its state)
deploy/deploy.sh --apply --only agentcore,l4  # agent code (runtime bundle) + re-wire
deploy/deploy.sh --apply --only console       # UI
```

**Jobs on demand** (safe: `apply:false` is a dry run)

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"apply":false,"market":"us","limit":5}' "$API/jobs/stall-sweep" | python3 -m json.tool
```

Jobs: `equity-monitor`, `nurture-tick`, `stall-sweep`, `rescore-profiles`,
`service-lane-monitor`, `shop-load`, `service-followup-tick`,
`nightly-attribution`, `district-cycle`.

**Autonomy levels** are data, not code: Admin console → Autonomy, or
`GET/PUT /admin/autonomy`. Every change is versioned and reversible; hard
ceilings (budget, allocation, plan publishing, estimate approval) cannot be raised.

**Guardrail or corpus change**

```bash
python3 -m app.infra.deploy_guardrails --apply && python3 -m app.infra.deploy_guardrails --calibrate
deploy/deploy.sh --apply --only l4,l6,agentcore      # new guardrail version → env

python3 -m app.infra.deploy_quick_index --apply       # corpus changed
python3 -m app.infra.deploy_knowledge_base --apply --sidecars --ingest
```

**Where to look when something fails**

| Symptom | Check |
|---|---|
| API returns 500 `AuthorizerConfigurationException` right after `l4` | transient while the authorizer re-wires; retry after ~30 s |
| `agentcore` ends `UPDATE_FAILED` with no logs | bundle contains non-arm64 binaries or exceeds 250 MB; `rm -rf /tmp/retail_ops_deps` and re-run |
| Agent answers say `engine: … (agent failed: AccessDeniedException)` | the runtime role lacks a Bedrock permission; the `agent_error` field names the action/resource |
| A benign question is refused by the guardrail | add it to `CALIBRATION_ALLOWED` in `deploy_guardrails.py`, refine the topic definition, `--apply`, `--calibrate` |
| Jobs write nothing | the schedule is DISABLED (`--disable-writers` ran after the last `l6` deploy) or the action's autonomy level is `off`; `store-digest` also writes nothing when Bedrock falls back |
| Lambda errors | CloudWatch alarms `retail-ops-dev-<function>-errors`; logs under `/aws/lambda/retail-ops-dev-*` |
| Runtime logs | one group per agent: `/aws/bedrock-agentcore/runtimes/<runtime_name>-*-DEFAULT`, for example `/aws/bedrock-agentcore/runtimes/retail_ops_dev_a3_sales-*-DEFAULT` |
| `agentcore --apply` fails building dependencies with `No module named pip` | the interpreter has no pip (for example a bare `uv run` environment); run the deployer with an interpreter that has pip, such as `uv run --with pip …` |
| A job returns `unknown_after_send` / alarm `<function>-runtime-unknown` fires | the job Lambda sent the job to its agent runtime and got no answer in time; the job may still have run. Check the runtime log group for the `correlation_id`; do not re-run it blindly |

## 7. Costs to expect (dev account, light use)

DynamoDB on-demand, Lambda, API Gateway, EventBridge, Step Functions and S3 are
pay-per-request and small. The items that cost when idle are: the CloudFront
distribution and WAF web ACL (a few dollars a month), the AWS Quick
subscription if enabled, and S3 Vectors storage (cents for this corpus). Model
usage: each Assistant answer in Agent mode is 3–6 Sonnet calls plus guardrail
units; the nightly jobs make no model calls except `store-digest` (about 120 short
calls per market on the first night, then only for figures that changed).

## 8. Optional managed integrations

### 8.1 AWS Quick

The deployment scripts create the Athena datasets, the five Quick sharing
groups (`docs/QUICK_BUILD.md` §P1), corpus, and CUSTOM_JWT AgentCore Gateway.
The `quick` stage creates the groups first, then grants owner on the data source and
every dataset to `retail-ops-builders` and to each builder user directly (the group's
members plus `RETAIL_OPS_QUICK_BUILDERS`), so builders see them in the Quick console.
Quick content is not row-scoped: row-level security was removed by user decision
on 2026-10-21 (spec §13), so anyone who can open a Quick dataset or dashboard sees
all of its rows. Set `RETAIL_OPS_QUICK_BUILDERS` to the Quick user names
of your authors before `--only quick` so they join `retail-ops-builders`. The application provider is
`app/services/platform/quick.py`; OEM routes list Flows and dashboards and
create registered-user embed URLs. With `RETAIL_OPS_QUICK_PER_USER_EMBED=1`, the
signed-in console user is registered as a Quick reader under their `user_id`
and added to the Quick group for their role, which decides the dashboards they
can open. That flag is off by default and no stage sets it (each registered reader
can add per-user subscription cost); with it off, embeds use the shared reader and
the `retail-ops-oem-*` groups stay empty.

The `quick` stage then runs `deploy_quick_content.py`, which creates the Quick
content through the Quick API and shares each resource (owner `retail-ops-builders`
and each builder user directly, because the Quick console lists spaces, chat agents and
Flows by direct user permission, not group membership; viewers the `retail-ops-oem-*`
groups; grants only add, never revoke): two custom-SQL Direct
query datasets, the topic `Retail Ops Journey Funnel`, an S3 knowledge source and 9
knowledge bases over `quick-index/`, the space `Automotive Retail Operations`, the chat
agent `Retail Ops Field Sales Agent`, 8 published `Retail Ops …` Flows and the published
dashboard `Retail Ops Campaign ROI & Funnel`. It is idempotent, `--status` lists what
exists, and `--delete` (a dry run unless `--apply`) removes it, dependents first. The
Flow ids Quick assigns go to the git-ignored `app/infra/quick-content-config.json`.

Five steps have no API and are done by hand, in the order of the "Console checklist"
in `docs/QUICK_BUILD.md` (status by build step in `docs/ACCEPTANCE.md`): create the MCP
connector `Retail Ops OEM Read Gateway` (`CreateActionConnector` rejects the MCP type),
re-run `deploy/deploy.sh --apply --only quick` so the connector is shared, added to the
space and linked to the agent, test the agent once, run one Flow once, and schedule the
District Roll-up. The stage ends with discovery, which you can also run alone:

```bash
python3 -m app.infra.deploy_quick --apply
```

The `l4` stage sets `ENABLE_QUICK=true` on the service when the account has an
AWS Quick subscription (override with `ENABLE_QUICK=true|false` in
`deploy/config.env`), and the `console` stage builds with
`NEXT_PUBLIC_ENABLE_QUICK=true`, so discovered content shows without manual steps.
`l4` also sets `RETAIL_OPS_QUICK_EMBED_DOMAINS` to the console URL from
`app/infra/console-config.json` (override it in `deploy/config.env`), and the embed
route passes it as `AllowedDomains`, so the console embeds dashboards without an entry
in the Quick admin's domain list. `l4` writes the same console URL to
`RETAIL_OPS_CONSOLE_ORIGIN`, the one origin the API answers CORS for (the API carries
a bearer token, so it is never `*`; the local console on `http://localhost:3100` is
always allowed too; override it in `deploy/config.env`). On a fresh account `console`
runs after `l4`, so `deploy.sh` runs `l4` once more right after `console` whenever the
console URL is new, which sets both (`deploy/deploy.sh --apply --only l4` does it by hand). Quick's MCP
connector authenticates to `retail-ops-dev-crosspersona-quick`; non-OEM application
reads use the separate `AWS_IAM` gateway so no surface reads another persona's tables
directly.

### 8.2 External identity provider

Native Cognito username/password sign-in is always available. SSO is an
optional OIDC or SAML provider layered on the same staff pool and `PLAT_Users`
role/scope model.

1. Register the application with the customer identity provider.
2. Store OIDC settings (`client_id`, `client_secret`, issuer, and scopes) in
   Secrets Manager and set `RETAIL_OPS_IDP_SECRET`, or configure
   `RETAIL_OPS_IDP_PROTOCOL=saml` and `RETAIL_OPS_IDP_METADATA_URL`.
3. Run:

   ```bash
   python3 -m app.infra.cognito_setup --apply --federate
   deploy/deploy.sh --apply --only l4,console
   ```

4. Register the Cognito redirect URI with the provider:
   `https://<domain>.auth.us-east-1.amazoncognito.com/oauth2/idpresponse`
   (`federate.idp_redirect_uri` in `app/infra/cognito-config.json`; the
   `cognito_setup --federate` run also prints it). Cognito collects the
   provider's authorization code there. `<console-url>/auth/callback` is the
   app client's own callback, which `cognito_setup` registers on the Cognito
   side; it is not the URI the provider needs. The console discovers configured
   external providers and renders one sign-in button per provider.
5. Choose who may sign in with SSO. A federated sign-in works only for an
   identity that resolves to a `PLAT_Users` row; anyone else is refused
   (`401 unknown user`). For the enterprise IdP, list the IdP aliases that get
   the Administrator role (every staff module, tenant-wide; spec §5.2, §8.1) in
   the git-ignored `deploy/config.env`, then run the `sso-admins` stage:

   ```bash
   export RETAIL_OPS_SSO_ADMINS=alias1,alias2      # in deploy/config.env
   source deploy/config.env
   deploy/deploy.sh --apply --only sso-admins
   ```

   The stage writes `U-ADMIN-<alias>` rows (role `platform_admin`, marker
   `provisioned_by=sso-allowlist`), deletes marker rows whose alias is no longer
   listed, and never touches any other row. Unset means no change; an empty
   value removes every Administrator. Aliases are matched case-insensitively
   and an `@corp.example.com` suffix is ignored; this assumes Federate's `sub` claim
   is the IdP alias (Cognito maps it to `preferred_username`).

To detach an external provider without removing native users:

```bash
python3 -m app.infra.cognito_setup --remove-idp --apply
deploy/deploy.sh --apply --only console
```

The authorizer maps native and federated identities to the same `PLAT_Users`
row, so role and organization scope stay consistent across sign-in methods.
The `U-ADMIN-*` Administrator rows are the exception: the allowlist matches only
sign-ins from the configured IdP, the one recorded on each row
(`federated_identity`, taken from `RETAIL_OPS_IDP_NAME`, default
`CorpDirectory`, when the `sso-admins` stage ran). The Cognito username must be
`<IdP>_<alias>`; a native sign-in, or a sign-in through any other provider, is
`401 unknown user` even when its username or `preferred_username` equals a
listed alias. Detaching the provider leaves the rows in place but unreachable
while no provider with that name is attached; remove them with an empty
`RETAIL_OPS_SSO_ADMINS=` and the `sso-admins` stage. Before attaching a
different provider, run the `sso-admins` stage with `RETAIL_OPS_SSO_ADMINS=`
(empty), or set `RETAIL_OPS_IDP_NAME` accordingly: give the new provider its own
name, so it never carries the name the rows record (`cognito_setup` also
defaults to `CorpDirectory`).

### 8.3 Durable customer memory

Customer memory (preferences, objections and promises the Sales and Journey
Agents recall across journeys) is off after a standard deploy:
`deploy_memory.py` is not a `deploy.sh` stage, so neither the
`retail-ops-dev-PLAT_AgentMemory` table nor the AgentCore Memory exists, and
`remember` reports `stored: false`. To turn it on:

```bash
python3 -m app.infra.deploy_memory            # dry run
python3 -m app.infra.deploy_memory --apply    # table + AgentCore Memory + app/infra/memory-config.json
deploy/deploy.sh --apply --only l4,l6         # wire RETAIL_OPS_MEMORY_ID onto the Lambdas
```

That makes the DynamoDB table work at once (the service and job roles already
write every `PLAT_` table). The AgentCore Memory half needs one more change: the
Lambda roles in `iam_roles.py` do not grant the Memory data-plane actions
(`bedrock-agentcore:CreateEvent`, `RetrieveMemoryRecords`), so those calls are
denied and `memory.py` degrades to the table without an error. The agent
runtimes are not given the Memory id, so inside them memory is the table only.

## 9. Tear down

`deploy/destroy.sh` finds the solution's resources by name and deletes them,
dependents first. It is a dry run unless you pass `--confirm` together with
`--account <id>`, which must match the account your credentials belong to.

```bash
deploy/destroy.sh                                          # dry run: list everything it found (env dev)
deploy/destroy.sh --env g4f2a91                            # dry run of another environment
deploy/destroy.sh --confirm --account <id>                 # delete; keep DynamoDB tables + event lake
deploy/destroy.sh --confirm --account <id> --include-data  # also delete the 69 tables and the lake bucket
deploy/destroy.sh --confirm --account <id> --only quick,alarms   # selected stages
```

What it removes: schedules, alarms, the CloudFront distribution (disabled first,
then deleted; if it is still disabling after 20 minutes, re-run the command),
its function and origin-access controls, the WAF web ACL, the AWS Quick content
(stage `quick_content`: the 8 `Retail Ops …` Flows, chat agent, space, dashboard and
analysis, topic, knowledge bases and their S3 source, the custom-SQL datasets, and the
console-built MCP connector `Retail Ops OEM Read Gateway`, in that order), AWS Quick
datasets, data source, groups and the service-role policy, AgentCore gateways, runtime,
memory and workload identities, the Bedrock knowledge base with its S3 Vectors
store and the guardrail, the REST API, Step Functions, Lambdas and layers,
EventBridge rules (and the `app-bus-<env>` bus once no other rules use it), SQS,
Firehose, Glue and Athena, secrets (7-day recovery window), the Cognito pool
with its domain, clients and identity provider, IAM roles, S3 buckets, and log
groups. `--include-quick-users` also removes the Quick readers the console
registered. Quick content is matched by the repo's ids (`<stem>-…`) and, for the Flows
and the connector, by their exact names, so other Quick content in the account (sample
Flows, other dashboards, datasets and agents) is never touched. Review just the Quick
part with `deploy/destroy.sh --only quick_content,quick` (dry run). A
Cognito pool or table with deletion protection ON is skipped and reported. After
a successful run the local config files of that environment (`app/infra/*-config.json`
for `dev`, `*-config.<env>.json` otherwise) move to
`backup/infra-config-<stem>-<timestamp>/`, so the next deploy starts clean.

### 9.1 Recreate a stack deployed before 2026-10-19 under the new name

Stacks deployed before 2026-10-19 use the retired stem `isso` (`isso-dev-…`).
The code now creates `retail-ops-dev-…` resources, so such a stack is removed
and deployed again rather than renamed:

1. `deploy/destroy.sh --stem isso` and review the list.
2. `deploy/destroy.sh --stem isso --confirm --account <id> --include-data --include-quick-users`.
   The data is synthetic and is reseeded by the deploy; agent memory, demo
   journeys, approvals and traces created at runtime are not kept.
3. `deploy/bootstrap.sh --yes --enable-jobs` (set `RETAIL_OPS_SEED_PASSWORD` first).
4. New identifiers to carry forward: the REST API URL, the CloudFront URL, the
   Cognito pool, app clients and Hosted UI domain. For SSO, register the new
   redirect URI `https://retail-ops-dev-<pool-suffix>.auth.<region>.amazoncognito.com/oauth2/idpresponse`
   with your identity provider, then run `cognito_setup --federate --apply` (§8.2).
   For AWS Quick, the `quick` stage re-creates the content; then follow the
   "Console checklist" of `docs/QUICK_BUILD.md` for the MCP connector against the new
   gateway and scope `retail-ops-oem/read`.
5. `deploy/verify.sh` and refresh `docs/DEMO.md` from the new live state.

## 10. Adapting to your environment

* **Environment name**: `--env <name>` (or `RETAIL_OPS_ENV`) names every resource
  `retail-ops-<env>-…`, so environments share an account without sharing a
  resource; `dev` keeps the original names. AWS Quick ids carry no environment
  name, so only one environment per account can use Quick. The region
  (`us-east-1`) is a constant in the deployers.
* **Markets**: `--market de` on the `data` stage loads the German pack; both can
  coexist (the authorizer scopes by `market_id`).
* **Identity provider**: `python3 -m app.infra.cognito_setup --federate --apply`
  adds an OIDC/SAML provider and the console shows a "Sign in with …" button;
  `--remove-idp` detaches it. See §8.2 for the complete setup.
* **Models**: `RETAIL_OPS_BEDROCK_MODEL` (phrasing) and `RETAIL_OPS_A3_AGENT_MODEL` (agent
  loop) are environment variables set by the deployers; both default to Claude
  inference profiles available in `us-east-1`.
