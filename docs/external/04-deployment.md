# Deploying into your own AWS account

This guide is for architects and operators who want to stand up Automotive
Retail Operations powered by Agentic AI in their own AWS account, verify it,
run it and take it down. What gets deployed is described in
[03-technical-architecture.md](03-technical-architecture.md). Placeholders such
as `<account-id>`, `<region>`, `<env>` and `<your-console-url>` stand for your
own values.

Everything is scripted except a few AWS Quick console steps that have no
public API, and AWS Quick itself is an optional add-on. Every script is a dry
run unless you tell it otherwise, prints the account it is acting on, runs
unattended, stops at the first failing step, and is safe to re-run.

**Supported deployment environment: Linux** (tested on Amazon Linux 2023
x86_64). macOS works for local development; Windows is not supported.

## One-click deployment

`deploy/guidance-deploy.sh` deploys the whole solution into a fresh account
without any input, on a Linux machine or in AWS CodeBuild (image
`aws/codebuild/amazonlinux-x86_64-standard:5.0`). It installs Python 3.12 if
it is missing, clones the repository, creates a Python virtual environment,
enables Amazon Bedrock model access, installs the console dependencies,
generates the seed-user password, deploys under a generated environment name
with AWS Quick skipped, runs the verification suite, switches the
development token off, and prints the console URL, the API base URL and the
environment name. The sections below are the same steps by hand, with the
options the script does not use.

## Before you start

### Tools

- Python 3.12 with `venv` and `pip`. On Amazon Linux 2023, if `python3.12` is
  missing: `sudo dnf install -y python3.12 python3.12-pip`.
- AWS CLI v2 with credentials for the target account
  (`aws sts get-caller-identity` must work).
- Node.js 18 or newer (20 tested) and npm, for the console build.
- git and a Linux shell (bash). No Docker, CDK, CloudFormation, certificate,
  domain or third-party API key is needed: the agent bundles are built for
  Linux arm64 through pip's cross-platform wheel resolution.

### Permissions

Use an AWS account where you can create resources in IAM, Lambda, DynamoDB, S3,
API Gateway, EventBridge and EventBridge Scheduler, Step Functions, SQS,
Cognito, Secrets Manager, Bedrock, Bedrock AgentCore, S3 Vectors, Data Firehose,
Glue, Athena, CloudFront, WAF, CloudWatch, SES, SNS and AWS Quick.
Administrator access is the simplest for a first deployment; the deployers then
create least-privilege roles for the runtime components (one role per Lambda
with enumerated table ARNs). Start in a dedicated, non-production account.

### Region

The deployers are tested in `us-east-1`. Another region needs the same
services, the Bedrock models and AWS Quick to be available there.

### Bedrock model access

The solution uses Claude Opus 4.5 and Claude Sonnet 4.5 (through their `us.`
cross-Region inference profiles) and Titan Text Embeddings V2.
`enable-bedrock.py` at the repository root makes each base model usable through
the API: it checks the model's availability, submits Anthropic's one-time use
case form if the account has not, creates the model agreement, and waits until
the model is available. It is safe to re-run and exits non-zero with the reason
if a model cannot be enabled.

```bash
pip install --upgrade boto3
python3 enable-bedrock.py 'anthropic.claude-opus-4-5-20251101-v1:0' 'us-east-1'
python3 enable-bedrock.py 'anthropic.claude-sonnet-4-5-20250929-v1:0' 'us-east-1'
python3 enable-bedrock.py 'amazon.titan-embed-text-v2:0' 'us-east-1'
```

The use case form's company name, website and text come from
`BEDROCK_USE_CASE_COMPANY`, `BEDROCK_USE_CASE_WEBSITE` and
`BEDROCK_USE_CASE_TEXT`; set them to your own before the first run. Set
`BEDROCK_USE_CASE_EMAIL` to add a contact address to the text; it is optional,
and no address is included by default.

### AWS Quick (for analytics and the Field Sales Agent)

The analytics dashboards and the Field Sales Agent's chat and Flows need an
**AWS Quick Enterprise** subscription in the account (MCP connectors require
Enterprise). The rest of the solution does not depend on it: without Quick,
deploy with `--skip quick` and `ENABLE_QUICK=false`, and the console's Quick
panels stay off. Quick resource ids carry no environment name, so one account
supports one Quick deployment.

### Workstation setup

```bash
git clone <repo-url> automotive-retail-operations
cd automotive-retail-operations
python3.12 -m venv .venv && source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements-agentcore.txt
(cd app/console && npm ci --no-audit --no-fund)
cp deploy/config.env.example deploy/config.env
# Edit deploy/config.env: set a unique, development-only RETAIL_OPS_SEED_PASSWORD
# (or generate one; see the root README.md), and optionally RETAIL_OPS_ENV.
source deploy/config.env
aws sts get-caller-identity          # confirm the intended account
```

`deploy/config.env` is git-ignored. Keep it, and the generated
`app/infra/*-config.json` files, out of version control: they are specific to
your account.

## Deploy with one command

`deploy/bootstrap.sh` is the front door for a fresh account.

```bash
deploy/bootstrap.sh --env <env>                       # dry run: checks tools, shows the target account, changes nothing
deploy/bootstrap.sh --yes --env <env> --skip quick    # deploy the whole stack into that account, without AWS Quick
deploy/verify.sh --env <env>                          # prove it works
python3 -m app.infra.deploy_l4 --dev-token off --apply   # then refuse the development tokens
```

Without `--yes` it only checks prerequisites (AWS CLI, credentials, Python and
`boto3`), prints the account and identity it would use, and says whether a stack
already exists there. With `--yes` it:

1. publishes an optional JWT verification layer for the authorizer (best
   effort; the authorizer has an equivalent built-in fallback);
2. runs every stage in order (`deploy/deploy.sh --apply`, described below);
3. loads additional markets if you asked for them;
4. runs the smoke suite against the new API and prints the API base URL and
   `<your-console-url>`.

| Option | Effect |
|---|---|
| `--yes` | Deploy; without it nothing changes |
| `--env <env>` | Environment name (default `RETAIL_OPS_ENV`, else `dev`): every resource is named `retail-ops-<env>-…`, so several environments can share an account |
| `--region <region>` | Target region (default `us-east-1`) |
| `--markets us,de` | Also load the German market (the US market is always loaded) |
| `--skip <stage,…>` | Skip stages, for example `--skip quick` without an AWS Quick subscription |
| `--enable-jobs` | Also enable the weekly autonomy-promotion job (every other job ships enabled) |

On a fresh account the console stage runs after the API stage, so the deploy
runs the API stage once more right after the console stage. That sets the new
console URL as the one origin the API answers CORS for and as the allowed
origin for embedded AWS Quick dashboards. To set it again by hand:

```bash
deploy/deploy.sh --apply --only l4
```

## The ordered stages

`deploy/deploy.sh` runs 17 stages in dependency order. It is a dry run without
`--apply`; `--only` and `--skip` pick stages. Each stage is an idempotent Python
deployer under `app/infra/` (most also offer `--status` and `--delete`), and
each writes the handles later stages need to a git-ignored
`app/infra/<name>-config.json`.

| Order | Stage | What it creates or updates |
|---|---|---|
| 1 | `cognito` | Staff user pool `retail-ops-<env>-staff` with 19 role groups and development seed users, the web app client, the hosted-UI domain and client for federation, and a resource server plus client-credentials client for AWS Quick (secret in Secrets Manager) |
| 2 | `data` | The 68 DynamoDB tables, loaded from the synthetic US pack |
| 3 | `sso-admins` | Administrator user records for the identities on the administrator allowlist; removes records for identities no longer listed and never touches other records |
| 4 | `events` | The event bus `app-bus-<env>`, the subscriber Lambda, its rule and a dead-letter queue |
| 5 | `lake` | Lake bucket, Firehose stream and rule, Glue database with 13 tables, Athena workgroup, analytics mirrors and roll-ups, and a backfill of existing events |
| 6 | `corpus` | The 538-document knowledge corpus exported to the lake bucket |
| 7 | `guardrails` | The main Bedrock Guardrail and the store digest's copy, each with a published version |
| 8 | `kb` | An S3 Vectors bucket and index, the Knowledge Base, its data source and metadata files, and the first ingestion |
| 9 | `l4` | Per-function IAM roles, the authorizer and service Lambdas, and the REST API; wires guardrail, Knowledge Base, Gateway, agent runtimes, AWS Quick and the embed origin into the service settings |
| 10 | `approvals` | The plan-approval Step Functions state machine and its submit Lambda |
| 11 | `l6` | Twelve job Lambdas, the scheduler role and 13 schedules for both markets |
| 12 | `alarms` | Errors and throttles alarms for every Lambda, and an unknown-outcome alarm per agent-owned job |
| 13 | `agentcore` | The shared runtime execution role, one code bundle per agent and the six AgentCore runtimes (the Journey Agent last, because it calls the others); then re-runs `l4` and `l6` to wire the runtimes in |
| 14 | `gateway` | The OEM read Lambda and role, the IAM-authenticated Gateway for the console and the JWT-authenticated Gateway for AWS Quick |
| 15 | `quick` | Quick sharing groups and memberships, the Athena data source and lake datasets, then the Quick content (see [AWS Quick](#amazon-quick)) and discovery of Flows and dashboards for the console |
| 16 | `console` | The static console build against the live API and Cognito clients, its S3 bucket, CloudFront distribution, origin access control and URL-rewrite function |
| 17 | `waf` | The WAF web ACL on the distribution |

```bash
deploy/deploy.sh                          # dry run of every stage
deploy/deploy.sh --apply                  # deploy every stage
deploy/deploy.sh --apply --skip data      # redeploy without re-loading data
deploy/deploy.sh --apply --only l4,console
```

The data load is a one-time step for a fresh account; use `--skip data` on
redeploys. If you run a stage before its prerequisite, it reports what is
missing, and the wiring completes the next time the dependent stage runs.

## Configuration

Settings live in `deploy/config.env` (copied from
[deploy/config.env.example](../../deploy/config.env.example)); source it before
running the scripts. Only the seed password is required.

| Setting | Default | What it does |
|---|---|---|
| `AWS_REGION` | `us-east-1` | Target region |
| `AWS_PROFILE` | unset | Named AWS CLI profile, if you use one |
| `RETAIL_OPS_ENV` | `dev` | Environment name, 2-8 lowercase letters and digits (not containing `aws`, `amazon` or `cognito`); names every resource and the local `app/infra/<name>-config.<env>.json` state files |
| `RETAIL_OPS_SEED_PASSWORD` | none (required with `--apply`) | Password for the development seed users; must meet the Cognito password policy; keep it only in `config.env` |
| `RETAIL_OPS_ENABLE_SSO` | `1` | Provisions the hosted UI and its app client; the console shows an SSO button only once an external identity provider is attached |
| `RETAIL_OPS_IDP_SECRET` | unset | Secrets Manager id holding your OIDC provider's client id, secret, issuer and scopes (preferred) |
| `RETAIL_OPS_IDP_CLIENT_ID`, `RETAIL_OPS_IDP_CLIENT_SECRET` | unset | OIDC client credentials given directly instead of the secret |
| `RETAIL_OPS_IDP_NAME`, `RETAIL_OPS_IDP_PROTOCOL`, `RETAIL_OPS_IDP_METADATA_URL` | provider name, `oidc` | Name of the identity provider, `oidc` or `saml`, and the SAML metadata URL |
| `RETAIL_OPS_SSO_ADMINS` | unset | The administrator allowlist: comma-separated user names from your identity provider that get the Administrator role; unset changes nothing, an empty value removes every Administrator |
| `ENABLE_QUICK` | set by the `l4` stage | `true` when the account has AWS Quick; set it to force `true` or `false` |
| `RETAIL_OPS_QUICK_EMBED_DOMAINS` | `<your-console-url>` | The origins allowed to embed Quick dashboards (up to three https origins) |
| `RETAIL_OPS_QUICK_PER_USER_EMBED` | off; no stage sets it | Registers each console user who opens a dashboard as their own Quick reader and adds them to their role's group; each reader can add per-user subscription cost |
| `RETAIL_OPS_QUICK_BUILDERS` | unset | Quick user names of your authors, added to the builders group and given owner access to the Quick content |
| `RETAIL_OPS_WAF_ALLOW_CIDRS` | off | Restricts the console to these CIDR ranges; anyone else is blocked, including you if a range is wrong |
| `RETAIL_OPS_WAF_RATE_LIMIT` | 2000 | Requests per five minutes per IP before WAF blocks |
| `RETAIL_OPS_BEDROCK_MODEL` | Claude Opus 4.5 inference profile | Model for grounded phrasing |
| Sales Agent loop model (the second model line in the example file) | Claude Sonnet 4.5 inference profile | Model for the Sales Agent's reasoning loop |
| `RETAIL_OPS_STALL_SLA_DAYS` | 7 | Days idle before the stall sweep flags a journey |
| `RETAIL_OPS_DUE_SERVICE_DAYS` | 14 | Horizon for due-service reminders |

Two choices are flags rather than settings:

- **Jobs and autonomy promotion.** Every job the agent panel shows as scheduled
  ships enabled, at autonomy level recommend. `--enable-jobs` (bootstrap or
  deploy) also enables the weekly autonomy-promotion job.
- **Environment name.** Resource names use the prefix `retail-ops-<env>-` from
  `--env` or `RETAIL_OPS_ENV`; the region `us-east-1` is a constant in the
  deployers. See [Adapting to another environment](#adapting-to-another-environment).

## Verify

```bash
deploy/verify.sh            # every check
deploy/verify.sh --quick    # smoke and acceptance only
deploy/verify.sh --skip loop,agent
```

`deploy/verify.sh` resolves the live API and the Cognito client, signs in as a
seed user (it needs `RETAIL_OPS_SEED_PASSWORD`), and runs eight checks:

1. **smoke**: the read and scope matrix over the REST API, including the
   expected refusals;
2. **acceptance**: the asserted acceptance criteria, including a scan of
   function roles for wildcard table access;
3. **loop**: the full write loop with throwaway rows that it removes afterwards;
4. **agent**: the Sales Agent's golden cases through the deployed API;
5. **guardrail**: the calibration lists against both guardrails;
6. **knowledge**: one retrieval against the Knowledge Base;
7. **runtime**: each agent runtime is ready and the schedules exist;
8. **agents**: a read-only ping to each agent runtime.

It ends with `VERIFY: ALL PASS` or names the checks that failed, and exits
non-zero on failure. It is read-only except for the self-cleaning loop and the
agent check, which can leave a task and a held outreach card for the seed user
by design.

The offline checks need no AWS and are what CI runs:

```bash
python3 -m pytest tests -q
python3 -m app.eval.run_eval --mode offline     # every golden case must pass
(cd app/console && npm run lint:units && npx tsc --noEmit -p .)
```

## AWS Quick

### What the scripts create

The `quick` stage creates, shares and keeps up to date:

- five sharing groups: a builders group (owners) and four OEM viewer groups
  (district managers, regional leads, C&I planning, marketing);
- an Athena data source and a direct-query dataset for each lake table, plus two
  custom-SQL datasets (the journey funnel, and campaign spend allocated over
  the journeys each campaign started);
- the topic `Retail Ops Journey Funnel` for natural-language questions;
- an S3 knowledge source and nine knowledge bases over the corpus;
- the space `Automotive Retail Operations`;
- the chat agent `Retail Ops Field Sales Agent`, whose instructions carry the
  OEM policy rules;
- eight published Flows named `Retail Ops …` (Account Plan, Opportunity Brief,
  Win Strategy, Action Plan, Contact Report, Meeting Package, District Roll-up,
  Sales Ops Digest);
- the analysis and published dashboard `Retail Ops Campaign ROI & Funnel`;
- discovery of the Flows and dashboards for the console's Quick screens.

Set `RETAIL_OPS_QUICK_BUILDERS` to your authors' Quick user names before the
stage runs, so they own the content. The stage grants owners directly as well as
through the group, because the Quick console lists spaces, chat agents and Flows
by direct permission. Grants only add, never revoke. Quick content is not
row-scoped: anyone who can open a dataset or dashboard sees all of its rows, so
share it only with groups that may see every district and dealer.

### Console checklist

Five steps, in order, signed in to the AWS Quick console as a member of the
builders group. Steps 1, 3, 4 and 5 have no public API; step 2 is a command.

1. **Create the MCP connector.** *Connectors → Create for your team → Model
   Context Protocol (MCP)*:
   - Name: `Retail Ops OEM Read Gateway` (exactly; the scripts find it by name).
   - MCP server endpoint: the Quick gateway's endpoint, `mcp_endpoint` in
     `app/infra/gateway-quick-config.json`. Connection type: public network.
   - Authentication: **service authentication (service-to-service)**, not user
     OAuth.
   - Client ID and token URL: `quick_mcp.client_id` and `quick_mcp.token_url`
     in `app/infra/cognito-config.json` (or run
     `python3 -m app.infra.cognito_setup --print-config`).
   - Client secret: the `client_secret` value of the Secrets Manager secret
     named in `quick_mcp.secret_name`. Paste it; do not save it elsewhere.
   - Scope, if asked: `retail-ops-oem/read`.
   - Turn on the four OEM read actions (list accounts, get account, get alerts,
     get incentive programs) and leave the Gateway's built-in search action off.

   ```bash
   python3 -c "import json; print(json.load(open('app/infra/gateway-quick-config.json'))['mcp_endpoint'])"
   aws secretsmanager get-secret-value --secret-id "<secret-name>" --query SecretString --output text
   ```

2. **Link the connector.** Run the Quick stage again. It shares the connector
   with the groups, adds it to the space and links it to the chat agent; the
   Flows reach OEM data through that agent.

   ```bash
   deploy/deploy.sh --apply --only quick
   ```

3. **Test the chat agent once.** *Chat agents → Retail Ops Field Sales Agent*:
   ask it to list your accounts and their open alerts (the answer should cite
   account or alert ids), and ask it whether to cut a dealer's wholesale orders
   next month (it should decline, name the policy, and offer a gap analysis).
4. **Run one Flow once.** *Flows → Retail Ops Account Plan → Run*, with an
   account id from your data. The `OEM facts` step should return the account and
   its alerts, and the draft should have its five sections, cite every figure
   and call itself a draft. If a step shows "Invalid reference" or "Invalid
   dependencies", reload the Flow; if it persists, delete the reference in that
   step, re-insert it with `@`, then save and publish. A later scripted update
   of the Flows overwrites console edits.
5. **Schedule the District Roll-up.** *Flows → Retail Ops District Roll-up →
   Run mode → schedule*: weekly, Monday morning, with the district id as input.
   Schedules are private, so each district manager creates their own.

After a deployment, reload the Quick console to see newly granted content. When
the corpus changes, open each `Retail Ops …` knowledge base and choose **Sync
now**; Quick has no sync API.

Chat, Flow runs and scheduled Flows consume AWS Quick agent hours (see
[Costs](#costs)).

## Operating

### Redeploy after a change

```bash
deploy/deploy.sh --apply --only l4              # API and service code
deploy/deploy.sh --apply --only agentcore,l4,l6 # agent code, then re-wire the runtimes
deploy/deploy.sh --apply --only l6              # job code (re-enables the agent-panel jobs)
deploy/deploy.sh --apply --only console         # console
deploy/deploy.sh --apply --only sso-admins      # after editing the administrator allowlist
deploy/verify.sh --quick
```

### Run a job now

```bash
python3 -m app.infra.deploy_l6 --run-once store-digest   # runs for both markets and waits
```

The jobs are `equity-monitor`, `nurture-tick`, `stall-sweep`,
`rescore-profiles`, `service-lane-monitor`, `shop-load`,
`service-followup-tick`, `predictive-scan`, `nightly-attribution`,
`district-cycle`, `store-digest` and `autonomy-promotion`. Signed-in OEM staff
can also run a job through the API with `POST /jobs/<job>`; the body
`{"apply": false, "market": "us"}` makes it a dry run (without it, the run
applies).

### Quiet the writer jobs

```bash
python3 -m app.infra.deploy_l6 --apply --disable-writers   # until the next l6 deploy
python3 -m app.infra.deploy_l6 --apply --enable-all        # everything on, including autonomy promotion
```

A later `l6` deployment re-enables the jobs the agent panel shows as
scheduled, so the panel never names a schedule that does not fire.

### Fall back to in-process agents

```bash
python3 -m app.infra.deploy_l4 --disable-agents all --apply   # service Lambda runs agent work itself
python3 -m app.infra.deploy_l6 --disable-agents all --apply   # job Lambdas run agent jobs themselves
python3 -m app.infra.deploy_l4 --enable-agents all --apply    # undo; deploy_l6 takes the same flag
```

The flag also takes a single agent from the agent registry, and the choice
survives redeploys.

### Autonomy levels

Autonomy is data, not code: change it in **Admin Console › Autonomy**. Every
change is versioned with history and rollback, and hard ceilings (budget shifts,
plan publishing, incentive allocation, the customer's estimate decision) cannot
be raised. Raise an action one level at a time, using the acceptance and
conversion figures in **Admin › Agents** as the evidence.

### Guardrail or corpus changes

```bash
python3 -m app.infra.deploy_guardrails --apply && python3 -m app.infra.deploy_guardrails --calibrate
deploy/deploy.sh --apply --only l4,l6,agentcore           # carry the new guardrail version

python3 -m app.infra.deploy_quick_index --apply           # corpus changed
python3 -m app.infra.deploy_knowledge_base --apply --sidecars --ingest
```

If a benign question is refused, add it to the calibration list in
`app/infra/deploy_guardrails.py`, refine the topic definition, apply, and
calibrate again.

### Where to look when something fails

| Symptom | Check |
|---|---|
| The API returns an authorizer configuration error right after an `l4` deploy | Transient while the authorizer is re-wired; retry after about 30 seconds |
| The `agentcore` stage ends in a failed update with no logs | The bundle contains a non-arm64 binary or is too large; clear the local dependency cache and re-run |
| An answer says the agent failed with access denied | The runtime role lacks a Bedrock permission; the error field names the action and resource |
| Jobs write nothing | The schedule was disabled with `--disable-writers`, or the action's autonomy level is off |
| A job reports an unknown outcome and its alarm fires | The job was sent to its agent runtime and no answer came back in time; it may still have run. Look up its correlation id in the runtime's log group before re-running it |
| Lambda errors | CloudWatch alarms per function; logs under `/aws/lambda/retail-ops-<env>-*` |
| Runtime logs | One log group per agent runtime under `/aws/bedrock-agentcore/runtimes/` |

## Costs

The solution is mostly pay-per-request. Plan for these categories:

| Category | What drives it |
|---|---|
| Models and safety | Bedrock model tokens (each Sales Agent answer in agent mode makes several model calls; the nightly store digest makes one call per changed insight), guardrail text units, Knowledge Base retrieval and embeddings |
| Agents | AgentCore Runtime (billed on actual CPU and memory use per session), Gateway calls, Code Interpreter sessions |
| Compute and requests | Lambda, API Gateway, DynamoDB on-demand, EventBridge and Scheduler, Step Functions, SQS, Data Firehose, Athena queries |
| Storage | S3 (console, lake, corpus), S3 Vectors, DynamoDB storage, CloudWatch logs |
| Always on | The CloudFront distribution and the WAF web ACL, Secrets Manager secrets, and the AWS Quick subscription |
| Identity and messaging | Cognito monthly active users, SES email and SNS SMS |
| AWS Quick | Per-user subscriptions, the per-account infrastructure fee, agent hours for chat and Flows, and index storage; per-user embedding adds a reader for every console user who opens a dashboard |

Two AWS Quick figures that affect planning:

- Quick Enterprise costs $40 per user per month plus a $250 infrastructure fee
  per account per month.
  - Source: [AWS Quick pricing](https://aws.amazon.com/quick/pricing/) (checked October 2026)
- An Enterprise license includes 18 agent hours a month; overage is $3 per
  agent hour.
  - Source: [AWS Quick pricing](https://aws.amazon.com/quick/pricing/) (checked October 2026)

For everything else, use the primary pricing pages and the
[AWS Pricing Calculator](https://calculator.aws/):
[Amazon Bedrock](https://aws.amazon.com/bedrock/pricing/),
[Amazon Bedrock AgentCore](https://aws.amazon.com/bedrock/agentcore/pricing/),
[AWS Lambda](https://aws.amazon.com/lambda/pricing/),
[Amazon API Gateway](https://aws.amazon.com/api-gateway/pricing/),
[Amazon DynamoDB](https://aws.amazon.com/dynamodb/pricing/),
[Amazon S3](https://aws.amazon.com/s3/pricing/),
[Amazon EventBridge](https://aws.amazon.com/eventbridge/pricing/),
[AWS Step Functions](https://aws.amazon.com/step-functions/pricing/),
[Amazon SQS](https://aws.amazon.com/sqs/pricing/),
[Amazon Data Firehose](https://aws.amazon.com/firehose/pricing/),
[AWS Glue](https://aws.amazon.com/glue/pricing/),
[Amazon Athena](https://aws.amazon.com/athena/pricing/),
[Amazon CloudFront](https://aws.amazon.com/cloudfront/pricing/),
[AWS WAF](https://aws.amazon.com/waf/pricing/),
[Amazon Cognito](https://aws.amazon.com/cognito/pricing/),
[AWS Secrets Manager](https://aws.amazon.com/secrets-manager/pricing/),
[Amazon CloudWatch](https://aws.amazon.com/cloudwatch/pricing/),
[Amazon SES](https://aws.amazon.com/ses/pricing/) and
[Amazon SNS](https://aws.amazon.com/sns/pricing/).

To keep an idle development account quiet, disable the writer jobs (see
[Operating](#operating)) and run jobs on demand.

## Security notes

- **Use a dedicated, non-production account first.** The teardown script refuses
  an environment name that contains "prod".
- **Seed users are for development.** They share the password from
  `RETAIL_OPS_SEED_PASSWORD`. Before a pilot with real people, sign in through
  your identity provider, assign roles and scopes in Admin › Users, and remove
  the seed users from the pool.
- **Turn off the development token outside development.** For scripted checks,
  the authorizer also accepts a plain development token, which is on by
  default. Once you have verified the deployment, run
  `python3 -m app.infra.deploy_l4 --dev-token off --apply`; later `l4` deploys
  keep the setting, and the one-click script does this for you. Cognito sign-in
  is unaffected.
- **Keep the administrator allowlist short.** An Administrator can read and
  administer every dealer and district. Review the list like any administrator
  grant; any federated sign-in without a user record is refused.
- **Restrict the console if it is not public.** Set `RETAIL_OPS_WAF_ALLOW_CIDRS`
  to your network ranges, and test the ranges before relying on them.
- **Share Quick content deliberately.** There is no row-level security in
  AWS Quick; group membership decides who can open a dashboard or dataset,
  and they see all of its rows.
- **Secrets stay in Secrets Manager.** The Quick machine client's secret and an
  optional identity-provider secret are stored there; the connector registry
  holds references, never values. Never commit `deploy/config.env` or the
  generated `app/infra/*-config.json` files.
- **No public entry points.** Every request passes the API authorizer or the
  console's CloudFront origin; the deployers create no public Lambda URLs.
- **Agents start at recommend.** The writer jobs ship enabled, but at recommend
  they only write cards, leads and drafts. Customer messages go out only after
  an action's level is raised, and only to consented channels. In a new account
  Amazon SES is in its sandbox and sends only to verified addresses until you
  request production access.
- **Before loading real data,** complete the classification review described in
  [03-technical-architecture.md](03-technical-architecture.md#current-scope-and-options).

## Tear down

`deploy/destroy.sh` finds the solution's resources by name and deletes them,
dependents first. It is a dry run unless you pass `--confirm` together with
`--account <account-id>`, which must match the account your credentials belong
to.

```bash
deploy/destroy.sh --env <env>                                       # dry run of one environment
deploy/destroy.sh --env <env> --confirm --account <account-id> --include-data   # delete all of it
deploy/destroy.sh                                                   # dry run: list what it found (env dev)
deploy/destroy.sh --confirm --account <account-id>                  # delete; keep tables and the lake
deploy/destroy.sh --confirm --account <account-id> --include-data   # also delete the tables and the lake bucket
deploy/destroy.sh --only quick_content,quick                        # review only the AWS Quick part (dry run)
```

- It removes schedules, alarms, the CloudFront distribution (disabled first,
  then deleted; re-run if it is still disabling after 20 minutes), the WAF web
  ACL, the AWS Quick content and datasets, the AgentCore gateways and
  runtimes, the Knowledge Base with its vector store, the guardrails, the REST
  API, the state machine, Lambdas and layers, EventBridge rules and the bus once
  it is empty, SQS, Firehose, Glue and Athena, secrets (with a 7-day recovery
  window), the Cognito pool with its domain, clients and identity provider, IAM
  roles, S3 buckets and log groups.
- DynamoDB tables and the event lake are kept unless you pass `--include-data`.
  `--include-quick-users` also removes the Quick readers the console registered.
- Quick content is matched by the solution's own ids and exact names, so other
  content in your Quick account is never touched.
- A Cognito pool or table with deletion protection on is skipped and reported,
  never switched off.
- After a successful run, the local `app/infra/*-config.json` files move to a
  timestamped folder under `backup/`, so the next deployment starts clean.

## Adapting to another environment

- **Environment name and region.** `--env <env>` (or `RETAIL_OPS_ENV`) names
  every resource `retail-ops-<env>-…`, so a second environment in the same
  account shares nothing with the first; the default `dev` keeps the original
  names. AWS Quick resource ids carry no environment name, so only one
  environment per account can use Quick. The region (`us-east-1`) is a constant
  in the deployers.
- **Markets.** The US and German packs can coexist; load the second with
  `--markets us,de` at bootstrap or `--market de` on the data stage. A new
  market is a new market record and seed pack, with no code change.
- **Identity provider.** Attach your OIDC or SAML provider with
  `python3 -m app.infra.cognito_setup --federate --apply`, then run
  `deploy/deploy.sh --apply --only l4,console`. Register the Cognito redirect
  URI that the command prints (also stored as `federate.idp_redirect_uri` in
  `app/infra/cognito-config.json`) with your provider. Detach a provider with
  `python3 -m app.infra.cognito_setup --remove-idp --apply`. Before you attach a
  different provider, empty the administrator allowlist and run the
  `sso-admins` stage, or give the new provider its own `RETAIL_OPS_IDP_NAME`.
- **Models.** `RETAIL_OPS_BEDROCK_MODEL` and the Sales Agent loop model are
  settings; choose models available in your region.
- **Without AWS Quick.** Deploy with `--skip quick`; the console hides its
  Quick panels and the rest works unchanged.
- **Your own systems.** Live DMS, labor and parts endpoints are configured as
  connector entries (endpoint and secret references) in Admin › Connectors;
  replace the synthetic data with your own only after the classification review.
- **Customer memory.** Optional and off after a standard deployment; enabling it
  is described in [03-technical-architecture.md](03-technical-architecture.md#current-scope-and-options).
- **CI.** The repository's pipeline runs the offline checks only; deployment
  stays an operator step with credentials for the target account.
