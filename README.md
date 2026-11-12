# Automotive Retail Operations powered by Agentic AI

A deployable reference implementation for connected automotive retail sales, service, ownership, OEM field operations, and marketing workflows. It combines deterministic domain services with agentic reasoning, governed automation, shared identity and scope controls, and a unified web console.

The repository contains application source, synthetic demonstration data, automated deployment tooling, evaluation assets, architecture decisions, and operator guidance. Presentation source and exported decks are intentionally maintained outside the published code repository.

**Use case walkthrough:** [`docs/use-case-walkthrough.html`](docs/use-case-walkthrough.html)
puts the business use case and the architecture on one page: two short
interactive stories (one buyer's journey from lead to service, and the OEM's
eight AWS Quick Flows) next to the architecture diagrams that implement them,
the seven agents, and what a deployment creates and costs. GitHub shows HTML
files as source, so open it locally after cloning, or from GitHub Pages.

## Table of Contents

1. [Overview](#overview)
    - [Cost](#cost)
2. [Name and how it maps to the orchestration layer](#name-and-how-it-maps-to-the-orchestration-layer)
3. [Start here](#start-here)
4. [Repository layout](#repository-layout)
5. [Architecture at a glance](#architecture-at-a-glance)
6. [Local validation](#local-validation)
7. [Deploy the Guidance](#deploy-the-guidance) (prerequisites and deployment steps)
8. [Deployment Validation](#deployment-validation)
9. [Running the Guidance](#running-the-guidance)
10. [Next Steps](#next-steps)
11. [Cleanup](#cleanup)
12. [FAQ, known issues, additional considerations, and limitations](#faq-known-issues-additional-considerations-and-limitations)
13. [Documentation ownership](#documentation-ownership)
14. [Generated and local-only files](#generated-and-local-only-files)
15. [Notices](#notices)

## Overview

A vehicle purchase and its service visits cross many systems that each optimize
their own step: the CRM owns the lead, the dealer management system (DMS) owns
the deal and the repair order, valuation tools own the trade-in, marketing
platforms own the campaign, and scheduling and inspection tools own the service
visit. None of them sees the whole customer, so context is lost at each hand-off:
from the website to the showroom, from the sales floor to the F&I office, from
delivery to the first service, and from the service lane back to the next
purchase.

This Guidance shows how an OEM and its franchised dealers can close those gaps
with agentic AI on AWS, in their own account:

- **Seven agents** on Amazon Bedrock AgentCore work across the sales process,
  the service and ownership process, and the OEM level above both. Each action
  has an autonomy level, and agents start at *recommend*: they write cards,
  leads and drafts for a person to approve.
- **Deterministic domain services** (pricing, incentives, finance, trade-in
  valuation, estimates) produce every number, with its source. The agents reason
  and phrase; Amazon Bedrock Guardrails check every model call.
- **One web console** joins the Dealer Console, OEM Workspace, Marketing Studio,
  Customer Web and Admin Console behind a single Amazon Cognito sign-in, with role
  and organizational-scope authorization.
- **Scheduled and event-driven automation** (Amazon EventBridge, EventBridge
  Scheduler, AWS Step Functions) runs the nightly and hourly agent jobs and carries
  the hand-offs between sales and service.
- **Synthetic data** for two markets (United States and Germany) with fictional
  brands. No real customer records are included.

![End-to-end overview: the sales journey (DISCOVER to OWN) and the service journey (BOOK to FOLLOW-UP) run as two independent state machines that communicate only through Amazon EventBridge events and recorded handoffs, with a read-only service-lane projection for the sales floor](assets/images/architecture-01-end-to-end-overview.png)

1. **Sales state machine.** The Purchase Journey supervisor advances an 8-state
   journey (DISCOVER → … → OWN) persisted in the journeys table; AWS Lambda
   validates every transition server-side.
2. **Service state machine.** The Service Journey supervisor runs a separate
   8-state repair order lifecycle (BOOK → … → FOLLOW-UP); the two machines share
   no synchronous call path.
3. **Cross-domain read model.** The sales floor queries a read-only projection of
   open repair orders; the handler role has no write access to repair orders.
4. **Event handoff.** An equity event on the application bus matches an Amazon
   EventBridge rule that invokes AWS Lambda to open a journey and a task.
5. **Ownership coupling.** The DELIVER → OWN transition emits an event that seeds
   service eligibility.
6. **Handoff record.** The handoffs table, keyed by `handoff_id`, carries the
   `correlation_id`; the writer Lambda is idempotent on event replay.

The flow-level diagrams are under [Architecture at a glance](#architecture-at-a-glance).
For the request paths, trust boundaries, data flow and deployment topology, see
[`docs/as-built/architecture.md`](docs/as-built/architecture.md). The business
use cases are in
[`docs/external/01-overview-and-use-cases.md`](docs/external/01-overview-and-use-cases.md).

### Cost

_You are responsible for the cost of the AWS services used while running this
Guidance. As of November 2026, the cost for running this Guidance with the
default settings in the US East (N. Virginia) Region is approximately $693.14
per month for a pilot with one OEM (Meridian Motors, US), 11 dealers and 72
active users._

The estimate covers only the AWS resources the default deployment
([`deploy/guidance-deploy.sh`](deploy/guidance-deploy.sh)) creates and the usage
they generate. Amazon Bedrock models and guardrails are about 96% of it, and they
grow with dealer activity: about $61–63 per dealer per month in both scenarios
below. The fixed monthly items are small: the AWS WAF web ACL and rules,
CloudWatch alarms and one Secrets Manager secret. Everything else is
pay-per-request, so an idle deployment costs little. To keep an idle development
account quiet, disable the writer jobs and run them on demand (see
[`docs/external/04-deployment.md`](docs/external/04-deployment.md#operating)).

The full estimate, with every assumption and unit price as an editable input, is
in [`docs/cost/cost-estimate.xlsx`](docs/cost/cost-estimate.xlsx) (PDF:
[`docs/cost/cost-estimate.pdf`](docs/cost/cost-estimate.pdf)).

_We recommend creating a [Budget](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html)
through [AWS Cost Explorer](https://aws.amazon.com/aws-cost-management/aws-cost-explorer/)
to help manage costs. Prices are subject to change. For full details, refer to
the pricing webpage for each AWS service used in this Guidance._

### Sample Cost Table

The following table provides a sample cost breakdown for deploying this Guidance
with the default parameters in the US East (N. Virginia) Region for one month,
for two scenarios built on the fictional OEMs and dealer network in
`sample-data/synthetic/`:

- **Pilot:** Meridian Motors (US), 1 region, 2 districts, 2 dealer groups,
  11 dealers, 72 monthly active users.
- **Regional rollout:** Meridian Motors (US) and Nordwerk Automobile (DE),
  100 dealers (50 per market), 1,000 monthly active users, deployed with
  `--markets us,de`.

Main assumptions, per dealer per business day (22 a month): 20 Sales Agent
questions, averaging 4.5 Claude Sonnet 4.5 calls of about 5,000 input and 250
output tokens each; 60 grounded-phrasing calls on Claude Opus 4.5 of about 1,200
input and 200 output tokens each; 30 Code Interpreter calculations. Per user per
business day: 300 API requests. Claude prices include the 10% premium for the
`us.` cross-Region inference profiles the solution calls. Free tiers are not
applied, except Amazon Cognito's 10,000 monthly active users.

| AWS service | Deployed resources and usage | Pilot [USD] | Regional rollout [USD] |
| ----------- | ---------------------------- | ----------: | ---------------------: |
| Amazon Bedrock: Claude Sonnet 4.5 | Sales Agent reasoning loop: 4,840 answers a month in the pilot (44,000 in the rollout) | $449.21 | $4,083.75 |
| Amazon Bedrock: Claude Opus 4.5 | Grounded phrasing in every agent (14,960 calls a month in the pilot) and the nightly store digest | $188.69 | $1,639.16 |
| Amazon Bedrock Guardrails | 2 guardrails: content, denied-topic, PII and contextual-grounding text units on every model call | $29.68 | $260.00 |
| Amazon Bedrock Knowledge Bases | 1 Knowledge Base on an S3 Vectors index: Titan query embeddings, vector queries and storage | $0.02 | $0.13 |
| Amazon Bedrock AgentCore | 6 agent runtimes, 2 Gateways, built-in Code Interpreter for deal and estimate math | $3.28 | $27.63 |
| AWS Lambda | 17 functions: API requests (300 ms, 512 MB) and about 1,100 scheduled job runs a month | $1.55 | $18.10 |
| Amazon API Gateway | 1 REST API: 475,200 requests a month in the pilot | $1.66 | $23.10 |
| Amazon DynamoDB | 68 on-demand tables: read and write request units, storage | $1.14 | $14.88 |
| Amazon EventBridge, EventBridge Scheduler, AWS Step Functions, Amazon SQS | 1 event bus and rules, the job schedules, the plan-approval state machine, a dead-letter queue | $0.48 | $4.37 |
| Amazon Data Firehose, AWS Glue, Amazon Athena, Amazon S3 | Event lake (Firehose stream, 13 Glue tables, Athena workgroup); S3 for the lake, corpus and console | $0.10 | $1.02 |
| Amazon CloudFront | 1 distribution for the console | $0.93 | $12.90 |
| AWS WAF | 1 web ACL with 4 rules | $9.19 | $11.64 |
| Amazon Cognito | 1 user pool, within the 10,000 MAU free tier | $0.00 | $0.00 |
| AWS Secrets Manager, Amazon CloudWatch | 1 secret; log ingestion; 43 alarms | $7.20 | $24.70 |
| **Total per month** | | **$693.14** | **$6,121.38** |

The levers that move the total most are the number of Sales Agent questions, the
input tokens per Sales Agent call, and the grounded-phrasing model
(`RETAIL_OPS_BEDROCK_MODEL`). Bedrock global cross-Region inference profiles
cost 10% less than the `us.` profiles.

## Name and how it maps to the orchestration layer

**Automotive Retail Operations powered by Agentic AI** is the product name used in
titles, the console, and customer-facing material. The product runs on one
orchestration layer, **Intelligent Sales & Service Orchestration**: the name the
specifications, agents, and code use. The two names describe the same system at
two levels:

| Retail operation (product view) | Orchestration it translates into (spec and code) | Owner |
|---|---|---|
| Vehicle sales: discovery, configuration, finance, trade-in, delivery | **Process 1 — Sales**: purchase journey `DISCOVER → … → OWN` | Journey Agent supervises; Discovery, Sales and Trade-In Agents serve |
| Service and ownership: booking, inspection, estimate, repair, follow-up | **Process 2 — Service & ownership**: service journey `BOOK → … → COMPLETED` | Service Agent supervises |
| Hand-offs between sales and service | events `vehicle.delivered` (opens the first service journey) and `service.handoff_to_sales` (opens a new purchase journey) | Journey Agent ↔ Service Agent, never one driving the other |
| OEM field sales and marketing | OEM level above both processes | Field Sales Agent, Campaign Agent |
| Agentic AI | seven agents on Amazon Bedrock AgentCore and AWS Quick, governed by per-action autonomy levels and guardrails | all seven agents |

The console and AWS Quick show agents by name only. The specs and code identify them by id:
Discovery Agent = A1, Journey Agent = A2, Sales Agent = A3, Trade-In Agent = A4, Campaign Agent = A5,
Field Sales Agent = A6, Service Agent = A7 (the display names live in `app/console/src/lib/agents.ts`,
`AGENT_LABEL`).

Technical names use the stem `retail-ops`: AWS resources are `retail-ops-<env>-…`
(`retail_ops_<env>_…` where only underscores are allowed; the environment name
is `dev` unless `RETAIL_OPS_ENV` sets another), settings are `RETAIL_OPS_…`.
The main spec file keeps the name
`intelligent-sales-service-orchestration`, and tables keep their data-owner
prefixes `OEM_` / `DEALER_` / `CUST_` / `PLAT_`. The earlier stem "isso" is retired;
a stack deployed before 2026-10-19 still carries it until it is removed with
`deploy/destroy.sh --stem isso` and recreated (see `docs/DEPLOYMENT.md`).

## Start here

| Goal | Document or command |
|---|---|
| See the business use case and the architecture on one page | [`docs/use-case-walkthrough.html`](docs/use-case-walkthrough.html) (open in a browser) |
| Read the documentation for external audiences (customers, partners, evaluators) | [`docs/external/README.md`](docs/external/README.md) |
| Understand the deployed implementation | [`docs/as-built/README.md`](docs/as-built/README.md) |
| Review requirements and target behavior | [`docs/specs/intelligent-sales-service-orchestration.md`](docs/specs/intelligent-sales-service-orchestration.md) |
| Deploy a new environment | [Deploy the Guidance](#deploy-the-guidance), then [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) and [`deploy/README.md`](deploy/README.md) |
| Run the demonstration | [`docs/DEMO.md`](docs/DEMO.md) |
| Present the step-by-step customer demo workflows | [`docs/DEMO_WORKFLOWS.md`](docs/DEMO_WORKFLOWS.md) |
| Understand persona access | [`docs/ACCESS_BY_PERSONA.md`](docs/ACCESS_BY_PERSONA.md) |
| Review acceptance evidence | [`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md) |
| Review architecture decisions | [`docs/adr/README.md`](docs/adr/README.md) |

## Repository layout

| Path | Purpose |
|---|---|
| `app/services/` | Python service layer, APIs, deterministic tools, authorization, journeys, agent orchestration, events, and admin capabilities |
| `app/agentcore/` | Amazon Bedrock AgentCore runtime entry point, agent registry and client |
| `app/console/` | Next.js console covering dealer, service, OEM, marketing, customer, and administration experiences |
| `app/infra/` | Idempotent boto3 deployment modules; generated account-specific `*-config.json` files are ignored |
| `app/eval/` | Offline and deployed-agent evaluation harness with a retained baseline |
| `app/demo/` | Smoke, acceptance, and end-to-end demonstration runner |
| `deploy/` | Safe deployment, verification, and teardown orchestration |
| `sample-data/synthetic/` | Committed deterministic data packs required for local execution and fresh-account deployment |
| `scripts/seed/generate/` | Synthetic-data generator, exporters, and validation logic |
| `tests/` | Offline Python test suite |
| `docs/specs/` | Authoritative requirements and target architecture |
| `docs/as-built/` | Bottom-up documentation of the current implementation |
| `docs/adr/` | Architecture decision records |
| `automotive-agent-skills/` | Publishable automotive agent skills and generated tools reference |

## Architecture at a glance

The reference build uses:

- Python 3.12 Lambda functions behind Amazon API Gateway
- Next.js 14 hosted through Amazon S3 and CloudFront
- Amazon Cognito authentication with role and organizational-scope authorization
- Amazon Bedrock AgentCore with a Strands reasoning agent layered over deterministic tools
- Amazon Bedrock Guardrails on model calls
- Amazon Bedrock Knowledge Bases over the solution corpus
- Amazon DynamoDB for operational data
- Amazon EventBridge, scheduled workers, and approval workflows for proactive automation
- AWS Quick datasets and sharing groups for analytics and field workflows

See [`docs/as-built/architecture.md`](docs/as-built/architecture.md) for request paths, trust boundaries, data flow, and deployment topology.

### Architecture diagrams

Every request takes the same edge path: Amazon CloudFront, Amazon Cognito,
Amazon API Gateway, AWS Lambda and Amazon DynamoDB. The numbered steps for each
flow are in the panel on the right of each diagram.

**Sales flow.** A dealership captures a lead, prepares vehicle, trade-in and
finance options, and completes a vehicle delivery.

![Sales flow architecture: lead response with Amazon Bedrock and Amazon SES or SNS, live floor coaching with Amazon Transcribe, Amazon Bedrock and Amazon SageMaker AI, trade and finance with Amazon Bedrock AgentCore Code Interpreter and AWS Secrets Manager, journey orchestration with Amazon EventBridge, EventBridge Scheduler and AWS Step Functions, delivery, and audit and analytics with Amazon Data Firehose and Amazon Athena](assets/images/architecture-02-sales-flow.png)

**Service flow.** A dealership books, inspects and estimates a service visit,
the customer approves it on a mobile device, and the repair is completed.

![Service flow architecture: booking with Amazon DynamoDB and AWS Lambda, check-in and inspection with Amazon Transcribe and Amazon Bedrock, estimate with Amazon Bedrock AgentCore, customer approval through an Amazon Cognito magic link, repair to ready driven by Amazon EventBridge with Amazon SES or SNS notifications, and follow-up and audit with EventBridge Scheduler and Amazon Athena](assets/images/architecture-03-service-flow.png)

**Service to sales handoff.** A vehicle-equity signal in the service lane becomes
a recorded handoff that opens a new sales journey.

![Service to sales handoff architecture: a nightly EventBridge Scheduler job computes equity, a service-lane-monitor job scores today's repair orders, Amazon Bedrock writes an upsell brief, AWS Lambda records the handoff in Amazon DynamoDB and publishes it on Amazon EventBridge, a new DISCOVER journey opens, and Amazon Data Firehose and Amazon Athena attribute the handoff to the eventual delivery](assets/images/architecture-04-service-to-sales-handoff.png)

**Marketing analytics.** The event-lake pipeline attributes marketing spend to
delivered vehicles and publishes approved customer nurture policy.

![Marketing analytics architecture: Amazon Data Firehose lands events in Amazon S3 catalogued by AWS Glue, a nightly attribution job queries Amazon Athena, Amazon Bedrock and Amazon Personalize recommend budget shifts, AWS Step Functions routes approval, approved policy is published to Amazon DynamoDB and AWS Quick through the AgentCore Gateway, and a nurture job executes it through Amazon SES or SNS](assets/images/architecture-05-marketing-analytics.png)

**OEM field sales.** OEM district managers use AWS Quick to plan dealer visits
from live, scoped dealer data, the event lake and a knowledge corpus.

![OEM field sales architecture: the OEM field team opens AWS Quick, whose shared space holds the Field Sales chat agent, eight Flows, a topic, a dashboard and nine knowledge bases; an MCP connector authenticates with Amazon Cognito and AWS Secrets Manager and reads scoped dealer data through the Amazon Bedrock AgentCore Gateway, AWS Lambda and Amazon DynamoDB; the event lake flows from Amazon Data Firehose to Amazon S3, AWS Glue and Amazon Athena](assets/images/architecture-06-oem-field-sales.png)

## Local validation

From the repository root:

```bash
python3 -m pytest tests -q
python3 -m app.eval.run_eval --mode offline
```

Run the console locally after installing its pinned dependencies:

```bash
cd app/console
npm ci
NEXT_PUBLIC_RETAIL_OPS_API=http://localhost:8080 npm run dev
```

In another terminal, start the local API with the committed synthetic data:

```bash
RETAIL_OPS_LOCAL_DATA=1 PORT=8080 python3 -m app.services.devserver
```

## Deploy the Guidance

Supported deployment environment: **Linux** (tested on Amazon Linux 2023 x86_64,
the AWS CodeBuild image `aws/codebuild/amazonlinux-x86_64-standard:5.0`). macOS
works for local development. Windows is not supported.

[`deploy/guidance-deploy.sh`](deploy/guidance-deploy.sh) runs every step below,
in this order, unattended and without input, in a fresh account (the AWS
Guidance one-click deployment). The commands below are the same steps by hand.
Every script is safe to re-run, and stops at the first failure with the name of
the step.

### Prerequisites

- A dedicated, non-production AWS account and credentials with administrator
  access (the deployment creates IAM roles and resources in about twenty
  services). Region `us-east-1`.
- On the deployment machine: git, AWS CLI v2, Node.js 18 or newer with npm (all
  three are preinstalled in the CodeBuild image), and Python 3.12 with `venv`
  and `pip`. On Amazon Linux 2023, if `python3.12` is missing:

  ```bash
  sudo dnf install -y python3.12 python3.12-pip
  ```

- Nothing else: no Docker, CDK, CloudFormation, ACM certificate, Route 53
  domain or third-party API key. The agent bundles are built for Linux arm64
  through pip's cross-platform wheel download, so any x86_64 machine works.

### 1. Region, account and environment name

Every resource is named `retail-ops-<env>-…` (`retail_ops_<env>_…` where only
underscores are allowed, `app-bus-<env>` for the event bus, and the account id
in every S3 bucket name), so several deployments can share an account. The
environment name is 2-8 lowercase letters and digits; the one-click script
generates `g` plus six random hex characters. Without it, the name is `dev`.

```bash
export AWS_REGION="us-east-1"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export RETAIL_OPS_ENV="g$(od -An -N3 -tx1 /dev/urandom | tr -d ' \n')"
export ENABLE_QUICK="false"            # no AWS Quick: the console hides its Quick panels
export RETAIL_OPS_ENABLE_SSO="0"       # no federated sign-in (needs your identity provider)
export BEDROCK_USE_CASE_EMAIL="<your-contact-email>"   # contact on Anthropic's one-time use case form (step 4)
echo "environment: $RETAIL_OPS_ENV"    # keep it: teardown needs it
```

### 2. Clone the repository

```bash
git clone --depth 1 --branch main https://github.com/aws-solutions-library-samples/guidance-for-automotive-retail-operations-using-agentic-ai-on-aws.git
cd guidance-for-automotive-retail-operations-using-agentic-ai-on-aws
```

### 3. Python virtual environment and requirements

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements-agentcore.txt
```

### 4. Amazon Bedrock model access

The solution calls Claude Opus 4.5 (grounded phrasing) and Claude Sonnet 4.5
(the Sales Agent's reasoning loop) through their `us.` cross-Region inference
profiles, and Titan Text Embeddings V2 for the Knowledge Base.
[`enable-bedrock.py`](enable-bedrock.py) makes each base model usable: it checks
the model's availability, submits Anthropic's one-time use case form when the
account has not, creates the model agreement, and waits until the model is
available. It exits non-zero with the reason otherwise. The form's contact
address is `BEDROCK_USE_CASE_EMAIL` (and `BEDROCK_USE_CASE_COMPANY`,
`BEDROCK_USE_CASE_WEBSITE`, `BEDROCK_USE_CASE_TEXT` override the neutral
defaults).

```bash
pip install --upgrade boto3
python3 enable-bedrock.py 'anthropic.claude-opus-4-5-20251101-v1:0' 'us-east-1'
python3 enable-bedrock.py 'anthropic.claude-sonnet-4-5-20250929-v1:0' 'us-east-1'
python3 enable-bedrock.py 'amazon.titan-embed-text-v2:0' 'us-east-1'
```

### 5. Console dependencies

```bash
(cd app/console && npm ci --no-audit --no-fund)
```

### 6. Seed-user password

The development seed users share one password. Generate a random one that meets
the user pool's policy (lower and upper case, a digit and a symbol), keep it in
the environment and never print it:

```bash
export RETAIL_OPS_SEED_PASSWORD="$(python3 -c 'import secrets,string; r=secrets.SystemRandom(); s="-_.%"; c=[r.choice(string.ascii_lowercase),r.choice(string.ascii_uppercase),r.choice(string.digits),r.choice(s)]+[r.choice(string.ascii_letters+string.digits+s) for _ in range(20)]; r.shuffle(c); print("".join(c))')"
```

### 7. Deploy

```bash
deploy/bootstrap.sh --env "$RETAIL_OPS_ENV"          # optional dry run: tools, account, nothing changes
deploy/bootstrap.sh --yes --env "$RETAIL_OPS_ENV" --skip quick --markets us
```

`bootstrap.sh` publishes the JWT verification layer, runs the 17 ordered stages
of `deploy/deploy.sh --apply` (AWS Quick skipped), and runs the smoke suite.
Most of the time goes to waiting for AWS (the Knowledge Base ingestion, the
AgentCore runtimes and the CloudFront distribution).

### 8. Verify

```bash
deploy/verify.sh --env "$RETAIL_OPS_ENV"             # ends with VERIFY: ALL PASS
```

### 9. Switch the development token off

The smoke, acceptance and loop checks sign in with plain development tokens
(`user:<id>`). Refuse them once verification is done, so only Cognito sign-in
works (later `l4` deploys keep this setting):

```bash
python3 -m app.infra.deploy_l4 --dev-token off --apply
```

### 10. Console URL and API base

```bash
python3 -c 'import json; from app.infra import naming; print(json.load(open(naming.config_path("console")))["url"])'
API_ID="$(aws apigateway get-rest-apis --query "items[?name=='retail-ops-${RETAIL_OPS_ENV}-journeys-api'].id | [0]" --output text)"
echo "https://${API_ID}.execute-api.${AWS_REGION}.amazonaws.com/dev"
```

Sign in to the console with a seed user id (for example `U-US-00002`) and the
seed password; [`docs/ACCESS_BY_PERSONA.md`](docs/ACCESS_BY_PERSONA.md) lists
the personas.

### Security considerations

- The API answers CORS only for the console's origin (`RETAIL_OPS_CONSOLE_ORIGIN`,
  set by the `l4` stage from the deployed console URL) and the local console on
  `http://localhost:3100`; it never returns `*`. If you serve the console from
  another domain, set the variable in `deploy/config.env` and re-run `l4`.
- Customer emails are sent from `RETAIL_OPS_SES_FROM`, an SES identity you verify
  in the account. There is no default sender: unset, every email is recorded as
  undeliverable and becomes a dealer task.
- The agent runtimes' Bedrock grant covers the Claude model family, the account's
  inference and guardrail profiles, the guardrail and the knowledge base, not
  every Bedrock resource (`app/infra/deploy_agentcore.py`). Widen it deliberately
  if you switch model families.
- Switch the development tokens off (step 9) before anyone else can reach the
  deployment.

To remove the deployment, see [Cleanup](#cleanup).

### Optional: AWS Quick add-on

The Field Sales Agent's chat and Flows and the analytics dashboards run in
AWS Quick. They need an **AWS Quick Enterprise** subscription in the
account (a paid, account-level subscription) and a five-step console checklist,
four of whose steps have no API (creating the MCP connector, one test chat, one
Flow test run and the District Roll-up schedule; a knowledge-base "Sync now" is
also manual whenever the corpus changes), so the one-click deployment skips
them. To add them later:

```bash
export ENABLE_QUICK="true"
deploy/deploy.sh --apply --env "$RETAIL_OPS_ENV" --only quick
deploy/deploy.sh --apply --env "$RETAIL_OPS_ENV" --only l4,console
```

then follow the console checklist in [`docs/QUICK_BUILD.md`](docs/QUICK_BUILD.md).
AWS Quick ids carry no environment name, so one account supports one Quick
deployment; a second environment with Quick in the same account is not
supported yet.

### More

- Settings, the ordered stages, operating and troubleshooting:
  [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) and [`deploy/README.md`](deploy/README.md).
- Running the one-click deployment in AWS CodeBuild (the Guidance validation
  environment): [`deploy/guidance-validation/README.md`](deploy/guidance-validation/README.md).

## Deployment Validation

`deploy/verify.sh` resolves the live API and the Cognito client, signs in as a
seed user (it needs `RETAIL_OPS_SEED_PASSWORD` in the environment), and runs
eight checks:

1. **smoke**: the read and scope matrix over the REST API, including the expected
   refusals
2. **acceptance**: the asserted acceptance criteria, including a scan of function
   roles for wildcard table access
3. **loop**: the full write loop with throwaway rows that it removes afterwards
4. **agent**: the Sales Agent's golden cases through the deployed API
5. **guardrail**: the calibration lists against both guardrails
6. **knowledge**: one retrieval against the Knowledge Base
7. **runtime**: each agent runtime is ready and the schedules exist
8. **agents**: a read-only ping to each agent runtime

```bash
deploy/verify.sh --env "$RETAIL_OPS_ENV"                      # every check
deploy/verify.sh --env "$RETAIL_OPS_ENV" --quick              # smoke and acceptance only
```

A successful deployment ends with `VERIFY: ALL PASS`. On failure the script names
the checks that failed and exits non-zero. It is read-only except for the
self-cleaning loop and the agent check, which can leave a task and a held
outreach card for the seed user by design.

You can also confirm the main resources directly:

```bash
aws apigateway get-rest-apis --query "items[?name=='retail-ops-${RETAIL_OPS_ENV}-journeys-api'].[name,id]" --output table
aws dynamodb list-tables --query "TableNames[?starts_with(@, 'retail-ops-${RETAIL_OPS_ENV}-')]" --output table
aws lambda list-functions --query "Functions[?starts_with(FunctionName, 'retail-ops-${RETAIL_OPS_ENV}-')].FunctionName" --output table
```

## Running the Guidance

**Inputs.** The deployment loads the committed synthetic data packs from
`sample-data/synthetic/`: dealers, customers, vehicles, journeys, repair orders
and campaigns for the US and German markets. The seed users cover every persona;
[`docs/ACCESS_BY_PERSONA.md`](docs/ACCESS_BY_PERSONA.md) lists their ids and what
each one sees.

**Sign in to the console.** Open the console URL from
[step 10](#10-console-url-and-api-base) and sign in with a seed user id (for
example `U-US-00002`) and the seed password. Each persona lands on the home
screen for their role and sees the agents that work for them.

**Run the end-to-end story.** With `API` set to the API base from step 10:

```bash
python3 -m app.demo.run_demo --api "$API"
```

The driver creates throwaway `DEMO-*` records and shows, in order:

1. The Journey Agent loads a purchase journey and blocks a stage transition that
   is missing context.
2. Once the context is supplied, the Journey Agent advances the journey and builds
   a Sales Package from the Discovery, Sales and Trade-In Agents' outputs.
3. A `vehicle.delivered` event crosses the event bus and opens the first service
   journey for the Service Agent.
4. The Service Agent enforces the estimate-decision gate before REPAIR.
5. The Service Agent hands an upgrade opportunity back to the Journey Agent as a
   new DISCOVER journey.
6. The driver removes its throwaway records (pass `--keep` to leave them for a
   screen walkthrough).

**Output.** Each section of the run prints a `[PASS]` or `[FAIL]` line per check
and ends with a result line such as `=== SMOKE RESULT: ALL PASS ===`. Run with
`--keep` to see the same records on the Dealer Console's sales floor and
service lane.
[`docs/DEMO.md`](docs/DEMO.md) is the full screen-by-screen runbook, and
[`docs/DEMO_WORKFLOWS.md`](docs/DEMO_WORKFLOWS.md) gives the step-by-step customer
workflows with the expected value on every screen.

## Next Steps

- **Connect your identity provider.** Set `RETAIL_OPS_ENABLE_SSO=1` and the
  identity-provider settings in `deploy/config.env`, assign roles and scopes in
  Admin › Users, then remove the seed users from the pool.
- **Raise agent autonomy deliberately.** Agents start at *recommend*. Raise an
  action's autonomy level only after you review its output; customer messages go
  out only to consented channels.
- **Send real email.** Verify an SES identity, set `RETAIL_OPS_SES_FROM`, and
  request SES production access.
- **Restrict network access.** Set `RETAIL_OPS_WAF_ALLOW_CIDRS` to your network
  ranges and tune `RETAIL_OPS_WAF_RATE_LIMIT` (default 2,000 requests per five
  minutes per IP).
- **Add AWS Quick** for analytics dashboards and the Field Sales Agent's chat
  and Flows (see [Optional: AWS Quick add-on](#optional-amazon-quick-add-on)).
- **Extend the agents and corpus.** Update the guardrail calibration lists or the
  Knowledge Base corpus and redeploy the stage (see
  [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)); the reusable automotive skills are
  in [`automotive-agent-skills/`](automotive-agent-skills/).
- **Replace the synthetic data.** Before loading real data, complete the
  classification review in
  [`docs/external/03-technical-architecture.md`](docs/external/03-technical-architecture.md#current-scope-and-options).

## Cleanup

`deploy/destroy.sh` finds the environment's resources by name and deletes them,
dependents first. It is a dry run unless you pass `--confirm` together with
`--account <account-id>`, which must match the account of your credentials. It
refuses an environment name that contains "prod". Review the dry run, then
delete:

```bash
deploy/destroy.sh --env "$RETAIL_OPS_ENV"                                                      # dry run
deploy/destroy.sh --env "$RETAIL_OPS_ENV" --confirm --account "$ACCOUNT_ID" --include-data      # delete
```

- It removes the schedules, alarms, CloudFront distribution, WAF web ACL, Amazon
  Quick content and datasets, AgentCore gateways and runtimes, the Knowledge Base
  with its vector store, guardrails, REST API, state machine, Lambda functions and
  layers, EventBridge rules and bus, SQS, Firehose, Glue and Athena resources,
  secrets, the Cognito user pool, IAM roles, S3 buckets and log groups.
- Without `--include-data`, the DynamoDB tables and the event lake bucket are kept.
- Secrets are deleted with a 7-day recovery window, so the same names cannot be
  reused until it ends.
- The CloudFront distribution is disabled first and then deleted. If it is still
  disabling when the run ends, run the delete command again about 15–20 minutes
  later.
- The Amazon Bedrock model access granted in step 4 is account-level and is not
  removed.

## FAQ, known issues, additional considerations, and limitations

**Known issues**

- *The CloudFront distribution is still disabling after teardown.* Re-run
  `deploy/destroy.sh` with the same arguments after 15–20 minutes.
- *`agentcore --apply` fails with `No module named pip`.* The interpreter has no
  pip (for example a bare `uv run` environment). Run the deployer with an
  interpreter that has pip, such as `uv run --with pip …`.
- *A job returns `unknown_after_send`, or the `<function>-runtime-unknown` alarm
  fires.* The job was sent to its agent runtime and no answer came back in time;
  it may still have run. Look up the `correlation_id` in the runtime's log group
  before re-running it.
- *A stack deployed before 2026-10-19 uses the `isso` stem.* Remove it with
  `deploy/destroy.sh --stem isso` and redeploy.

**Additional considerations**

- Deploy into a dedicated, non-production account. The deployment creates IAM
  roles and resources in about twenty services.
- The seed users share one password and the API accepts development tokens until
  you switch them off ([step 9](#9-switch-the-development-token-off)). Do this
  before anyone else can reach the deployment.
- The 12 EventBridge Scheduler schedules ship enabled, and the `store-digest` job
  calls Amazon Bedrock nightly. Disable the writer jobs in an idle account.
- In a new account Amazon SES is in its sandbox and sends only to verified
  addresses.
- AWS Quick content has no row-level security: anyone who can open a Quick
  dataset or dashboard sees all of its rows. Share it deliberately.

**Limitations**

- The deployment is tested in `us-east-1` only, from Linux (Amazon Linux 2023
  x86_64). macOS works for local development; Windows is not supported.
- AWS Quick ids carry no environment name, so an account supports one Amazon
  Quick deployment.
- Five AWS Quick setup steps have no API and are done by hand (see
  [`docs/QUICK_BUILD.md`](docs/QUICK_BUILD.md)).

For feedback, questions or suggestions, please use the issues tab of this
repository.

## Documentation ownership

- `docs/specs/` describes required and target behavior.
- `docs/as-built/` describes what the code implements now.
- `docs/adr/` explains accepted architectural choices and rejected alternatives.

`automotive-agent-skills/TOOLS.md` is generated from the authoritative tool tables. Update the specification first, then regenerate the tools reference rather than editing it by hand.

## Generated and local-only files

The following stay local and are excluded from Git:

- `docs/presentation/` — presentation source, icons, and exported decks
- `backup/` — retired documents and one-off scratch artifacts
- `sample-data/generated/` — reproducible generated evidence images
- `app/infra/*-config.json` (`*-config.<env>.json` for an environment other than `dev`) and `app/infra/l4-api.yaml` — account-specific deployment state
- `deploy/config.env` — local deployment settings and possible identity-provider secrets
- console build output, Python caches, virtual environments, logs, and evaluation reports other than the retained baseline

Never commit credentials or locally generated deployment state. Use `deploy/config.env.example` as the configuration template.

## Notices

*Customers are responsible for making their own independent assessment of the information in this Guidance. This Guidance: (a) is for informational purposes only, (b) represents AWS current product offerings and practices, which are subject to change without notice, and (c) does not create any commitments or assurances from AWS and its affiliates, suppliers or licensors. AWS products or services are provided “as is” without warranties, representations, or conditions of any kind, whether express or implied. AWS responsibilities and liabilities to its customers are controlled by AWS agreements, and this Guidance is not part of, nor does it modify, any agreement between AWS and its customers.*
