# AWS Quick build runbook

**Target:** AWS Quick in AWS account `<account-id>`, region `us-east-1` (dev)
**Scope spec:** `docs/specs/retail-ops-quick-project.md` (what A5/A6/Quick Index are — the "what/why").
**This file** is the single **build guide + deployed-handles reference + chat prompts**
(the "how") for the Quick-native side: what the repo scripts create (next section), the
one **Console checklist** of the steps that have no API, the full per-step detail
(§P–§9), the live handles (Appendix A), and the console chat prompts (Appendix B). The
script-side provisioning is summarised in `docs/DEPLOYMENT.md` §8.1; which build steps
are done and which remain is tracked once, in `docs/ACCEPTANCE.md` ("AWS Quick
console content"), not here.

---

## What the scripts create

`deploy/deploy.sh --apply --only quick` builds all of it (a fresh account gets it from
`deploy.sh --apply`). Every command below dry-runs without `--apply`; `deploy_quick_content`
is `python3 -m app.infra.deploy_quick_content`. Live in the dev account on 2026-10-23;
check with `python3 -m app.infra.deploy_quick_content --status`.

| Resource | Name | Id | Command |
|---|---|---|---|
| Sharing groups (§P1) | `retail-ops-builders` (owner) · `retail-ops-oem-dm` · `retail-ops-oem-regional` · `retail-ops-oem-ci-planning` · `retail-ops-oem-marketing` | same as the name | `deploy_quick_groups --apply` |
| Athena data source + 13 lake datasets (§1) | `Retail Ops event lake (Athena)` · `Retail Ops · <table>` | `retail-ops-lake` · `retail-ops-lake-<table>` | `deploy_quick_datasets --apply` |
| Custom-SQL datasets, Direct query (§1a, §7) | `Retail Ops · journey_events (funnel)` · `Retail Ops · campaign ROI` | `retail-ops-journey-events-sql` · `retail-ops-campaign-roi-sql` | `deploy_quick_content --apply --only datasets` |
| Topic (§1b) | `Retail Ops Journey Funnel` | `retail-ops-journey-funnel` | `… --only topic` |
| Knowledge S3 source + 9 knowledge bases (§2b) | `Retail Ops Knowledge S3` · `Retail Ops CRM Notes` … `Retail Ops TSBs` | `retail-ops-knowledge-s3` · `retail-ops-kb-<kind>` | `… --only knowledge` |
| Space (§0, §4) | `Automotive Retail Operations` | `retail-ops-space` | `… --only space` |
| Chat agent (§5) | `Retail Ops Field Sales Agent` | `retail-ops-field-sales-agent` | `… --only agent` |
| 8 Flows, published (§6) | `Retail Ops Account Plan` … `Retail Ops Sales Ops Digest` | assigned by Quick (Appendix A9) | `… --only flows` |
| Analysis + dashboard, published (§7) | `Retail Ops Campaign ROI & Funnel` | `retail-ops-campaign-roi-funnel` | `… --only dashboard` |
| Sharing (§P1, §6.3, §8) | owner set to `retail-ops-builders` **and to each builder user directly** (the group's members plus `RETAIL_OPS_QUICK_BUILDERS`) on all, including the 13 lake datasets and the connector; viewers per §P1 (groups only); dashboard also the embed user `default/admin` | — | every step above |
| Embed domain (§7) | the console origin, passed as `AllowedDomains` on each embed URL | `RETAIL_OPS_QUICK_EMBED_DOMAINS` on `retail-ops-dev-journeys-service` | `deploy.sh --apply --only l4` |
| Discovery for `/quick` (§9) | — | `app/infra/quick-config.json` | `deploy_quick --apply` |

The corpus, both gateways and the Cognito machine client are Track A below.

## Console checklist (do these by hand)

Five steps, in order, in the AWS Quick console (account `<account-id>`, `us-east-1`)
unless a step says *terminal*. Sign in as a member of `retail-ops-builders` (for
example `admin/jdoe`). Steps 1, 3, 4 and 5 have no public API (the reason is
in each step); step 2 is the repo command that has to follow step 1.

- [x] **1. Create the MCP connector** (detail [§3b](#3b-connector-console)). *Connectors → Create for your team → Model Context Protocol (MCP)*:
  - *Done 2026-10-23* by the owner `admin/jdoe`: connector `<connector-id>`, `CREATION_SUCCESSFUL`, the 4 OEM read actions on. `describe_action_connector` lists 5 enabled action ids without names; if the fifth is the Gateway's `x_amz_bedrock_agentcore_search`, turn it off on the connector (the Gateway metrics show no call to it).
  - Name: `Retail Ops OEM Read Gateway` (exactly; the scripts find it by this name. Connector names allow letters, digits, spaces, `_` and `-` only, so the runbook's earlier `(Gateway)` lost its parentheses)
  - Description: `Scoped and cited OEM reads for accounts, alerts and incentive programs`
  - MCP server endpoint: `https://retail-ops-dev-crosspersona-quick-<gateway-suffix>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp` · Connection type: Public network
  - Authentication: **Service authentication (Service-to-Service)**, not "User authentication (OAuth)" (that one redirects and fails with `redirect_mismatch`)
  - Client ID: `<quick-mcp-client-id>`
  - Client secret: the `client_secret` value of `aws secretsmanager get-secret-value --secret-id retail-ops-dev-quick-mcp-oauth --query SecretString --output text` (paste it, don't save it)
  - Token URL: `https://retail-ops-dev-<domain-suffix>.auth.us-east-1.amazoncognito.com/oauth2/token` · Scope, if asked: `retail-ops-oem/read`
  - Turn on the 4 actions `oem-read___oem_list_accounts`, `oem-read___oem_get_account`, `oem-read___oem_get_alerts`, `oem-read___oem_get_incentive_programs`; leave the Gateway's `x_amz_bedrock_agentcore_search` off.
  - *Done when:* the connector lists those 4 actions as enabled, and `python3 -m app.infra.deploy_quick_content --status` no longer prints `MISSING connector`.
  - *Why by hand:* `CreateActionConnector` rejects the MCP connector type (`InvalidParameterValueException`, 2026-10-23; the API's type list has no MCP entry).
- [x] **2. Link the connector** (*terminal*; detail [§4](#4--add-resources-to-the-space), [§5](#5--field-sales-chat-agent-retail-ops-field-sales-agent)). `python3 -m app.infra.deploy_quick_content --apply --only space,agent`
  (or `deploy/deploy.sh --apply --only quick`). It shares the connector (owner `retail-ops-builders`
  and each builder user, viewers the 4 `retail-ops-oem-*` groups), adds it to the space and links it
  to the chat agent; the Flows reach the OEM reads through that agent.
  - *Done 2026-10-23:* the space holds 20 resources (the connector added) and `describe_agent` lists the connector under `ActionConnectors`.
  - *Done when:* the output shows `[update] agent retail-ops-field-sales-agent (connector linked)` and
    `[add] 1 resource(s) to space retail-ops-space: ACTION_CONNECTOR`. If it prints `could not share`,
    share the connector with `retail-ops-builders` as Owner in the console (*Connectors → ⋮ → Share*) and re-run.
- [x] **3. Test the chat agent once** (detail [§5](#5--field-sales-chat-agent-retail-ops-field-sales-agent)). *Chat agents → `Retail Ops Field Sales Agent` → Chat*, ask:
  "List my accounts and their open alerts" · "Should we cut D-105's wholesale next month?" · "What does the EV charging bulletin say?"
  - *Done when:* answers cite account/alert ids or a `source_ref`; the wholesale question is declined naming GR-001/GR-005 with a gap analysis offered.
  - *Done 2026-10-23:* the chat agent opened and answered for the owner. The connector path works: between 21:24 and 21:34 PDT the Quick gateway served 12 `tools/call` (`oem_get_account` 4, `oem_get_alerts` 4, `oem_list_accounts` 3, `oem_get_incentive_programs` 1) with 0 errors, and the target Lambda ran 12 times with 0 errors (AgentCore and Lambda metrics; the metrics don't say which calls came from the chat and which from a Flow run). The cited answers and the GR-001/GR-005 refusal have not been checked yet.
  - *Why by hand:* the API has no call that chats with an agent. Chat is metered in agent hours: an Enterprise license includes 18 a month, then $3 per agent hour.
    Source: [AWS Quick pricing](https://aws.amazon.com/quick/pricing/), "Entitlements & overages" FAQ (read 2026-10-23).
- [ ] **4. Run Flow 1 once** (detail [§6](#6--field-sales-quick-flows-eight-flows), [§6.3](#63-finish)). *Flows → `Retail Ops Account Plan` → Run*, `Account ID` = `ACC-US-00001`.
  - *Done when:* step `OEM facts` returns the account and its alerts, and the draft has the five sections (Performance vs objective, Gaps, Opportunities, Recommended actions, Risks), cites every figure and calls itself a draft.
  - *Why by hand:* the API has no call that runs a Flow. A run is metered in agent hours like chat (step 3).
  - *Flows rewritten 2026-10-23* (§6): the step ids now have the console's shape, which clears "Invalid reference in your prompt" and "Invalid dependencies in …", and each `OEM facts` step names only the tools its Flow needs, with arguments, in one turn. Reload the Flow in the console, then re-run Flow 1 and compare its time with the earlier run.
  - If a step still shows "Invalid reference" or "Invalid dependencies", open that step, delete the reference and re-insert it with `@` from the menu, then **Save** and **Publish**. The steps that carry references:
    Account Plan: `OEM facts`, `Notes`, `Account plan draft` · Opportunity Brief: `OEM facts`, `Notes`, `Opportunity brief draft` · Win Strategy: `OEM facts`, `Playbook`, `Win strategy draft` · Action Plan: `OEM facts`, `Action plan draft` · Contact Report: `OEM facts`, `Plan history`, `Contact report draft` · Meeting Package: `OEM facts`, `Notes`, `Funnel`, `Meeting package draft` · District Roll-up: `OEM facts`, `Funnel`, `District roll-up draft` · Sales Ops Digest: `OEM facts`, `Bulletins`, `Sales ops digest draft`. A later `deploy_quick_content --apply --only flows` overwrites console edits to these Flows.
- [ ] **5. Schedule the District Roll-up** (detail [§6.3](#63-finish)). *Flows → `Retail Ops District Roll-up` → Run mode → scheduling icon → Create schedule*:
  name `District roll-up weekly` · Repeat Weekly, Monday, 5:00 AM, `America/New_York` · input `District ID` = `DIST-07` → Save.
  - *Done when:* the schedule is listed under *Flows → My schedules*. Schedules are private: each district manager creates their own.
  - *Why by hand:* the API has no Flow schedule call. Each scheduled run uses agent hours.

**After a deploy, reload the Quick console.** The scripts grant each builder user the
owner set directly, next to the group `retail-ops-builders`, because the console lists
spaces, chat agents and Flows by direct user permission, not group membership (with the
group grant alone, `admin/jdoe` saw none of them on 2026-10-23). Resources the
scripts create show the IAM user's raw id (`AIDA…`) as creator; console-created ones show
the Quick user name.

**Later, only when the corpus changes** (detail [§2b](#2b-create-the-knowledge-bases)): after `deploy_quick_index --apply`, open each
`Retail Ops …` knowledge base (*Knowledge*) and choose **Sync now**; the API has no sync
call. Check with `deploy_quick_content --status` (`sync=COMPLETED`, document counts as in §2b).

**Naming.** The space is `Automotive Retail Operations`; every other Quick resource users
see is named `Retail Ops …` (the datasets `Retail Ops · <table>`), and ids keep the
`retail-ops-` prefix. Names users see never carry agent codes or the internal shorthand:
the chat agent is `Retail Ops Field Sales Agent`, the Flows are `Retail Ops <output>`. Codes
such as A5 and A6 appear in this runbook's prose only, to point at the spec. Quick content
is not row-scoped (§8); access is by the §P1 groups.

---

## Where the status lives

Which of the build steps below are complete, and the verification evidence for
each, is recorded once in `docs/ACCEPTANCE.md` under "AWS Quick console
content". This runbook only says *how* to do each step.

**Status, 2026-10-23 (read from the live account).** Track A is done, and so is every
Track B item that has an API: `deploy_quick_content --apply` created the two custom-SQL
datasets, the topic `Retail Ops Journey Funnel`, the S3 knowledge source and the 9
`Retail Ops …` knowledge bases (first sync completed, 538 documents, the same counts as
the corpus), the space `Automotive Retail Operations`, the chat agent
`Retail Ops Field Sales Agent`, the 8 `Retail Ops …` Flows (Published) and the dashboard
`Retail Ops Campaign ROI & Funnel` (Published), each shared as in §6.3 and §8. A second
`--apply` changed nothing. The `l4` stage now passes the console origin as the embed URL's
`AllowedDomains`, so the Quick admin's domain list isn't needed. Signed in as the OEM
district manager `U-US-00044`, `/oem/quick/flows` lists the 8 Flows, `/oem/quick/dashboards`
lists the dashboard and its embed-url route returns a URL. The owner built the MCP connector
in the console (checklist step 1) and the repo linked it (step 2): the space holds 20
resources and the agent references the connector. The builder `admin/jdoe` saw
no space, chat agent or Flow until each resource was also shared with the user directly;
the deployers now grant every builder user directly (§P1), and a re-run on 2026-10-23 found
`admin/jdoe` complete and granted `admin` where it was missing. The owner then
opened the chat agent, which answered and called the OEM reads through the connector
(step 3). The same day the Flows were rewritten in place to clear the console's "Invalid
reference" errors and to make `OEM facts` faster (§6). What's left is the
[Console checklist](#console-checklist-do-these-by-hand): one run of Flow 1 and the
District Roll-up schedule.

**Legacy content from the retired stack (deleted 2026-10-23).** The account held 33
Quick resources built on 2026-10-05 to 10-14 under the retired stem and agent codes,
owned by `admin/jdoe` and wired to deleted resources (the old lake bucket,
Athena workgroup, gateway and Cognito domain): a space, a topic, 9 knowledge bases and
their two S3 knowledge data sources, an Athena data source with 7 SPICE datasets, two
analyses, a chat agent `OEM Sales Administrator`, an MCP connector and 8 Published
Flows `A6 …`. All 33 were deleted on 2026-10-23, dependents first, after each
definition and permission set was saved to a local, git-ignored backup; none was
skipped. The two analyses keep the API's default 30-day recovery window (restorable
with `restore-analysis` until 2026-11-22). `Example Flow`, a never-run draft by the same
owner whose action step used the deleted connector, was backed up the same way and
deleted later that day at the owner's request. The account's 7 sample Flows and three
demo dashboards are kept.

**How to check.** `python3 -m app.infra.deploy_quick_content --status` lists every
resource the repo created, with its id, the knowledge bases' document counts and sync
state, and what is still missing. `python3 -m app.infra.deploy_quick` (dry-run) lists the
account's Flows, dashboards and action connectors. The repo's `.venv` boto3 (1.43.116)
has the Flow, agent, space, knowledge base, topic V2 and action connector calls; the AWS
CLI v2.30 installed here lacks them. The space, knowledge base and agent lists return
only what the caller can open; the repo's content is shared with `retail-ops-builders` and
with each builder user directly, so a builder sees it (`search_spaces` / `search_agents` with
the filter `DIRECT_QUICKSIGHT_OWNER` = the user's ARN return the space and the agent). Flow
schedules are not checkable from the API; verify them in the console.

**Repo tooling that provisions the above (all idempotent, `--status`/dry-run supported):**
`deploy_lake.py` (journey_events), `deploy_lake_analytics.py` (generator-mirror prefixes),
`deploy_lake_rollups.py` (rdr/csi/marketintel/leads/campaigns), `deploy_quick_index.py`
(538-doc corpus → S3), `deploy_quick_datasets.py` (Athena source + 13 datasets),
`deploy_quick_groups.py` (sharing groups + memberships), `deploy_quick_content.py`
(custom-SQL datasets, topic, knowledge bases, space, chat agent, Flows, dashboard,
sharing), `deploy_quick.py` (discover Flows/dashboards into `quick-config.json`).

---

## Segregation — script-driven vs. by hand

Every build item sorts into one of two tracks. **Track A** is done by **repo
scripts** (infra, data, IAM, the gateway, and the Quick content that has an API).
**Track B** has no public API and is done by a human, in the order of the
[Console checklist](#console-checklist-do-these-by-hand).

### Track A — Script driven

| Item | Script / command |
|---|---|
| P · Athena/S3/Glue/Dynamo access for Quick | IAM inline `retail-ops-lake-and-data-read` on `aws-quicksight-service-role-v0` |
| P · Cognito Hosted-UI domain | `cognito_setup.py` (`retail-ops-dev-<domain-suffix>`) |
| P1 · Quick sharing **groups** + memberships | `deploy_quick_groups.py --apply` (§P1) |
| 1 · Athena data source `retail-ops-lake` + 13 datasets, owned by `retail-ops-builders` | `deploy_quick_datasets.py --apply` (after `deploy_quick_groups`) |
| 1 · Lake tables (`journey_events` + roll-ups) | `deploy_lake.py` / `deploy_lake_analytics.py` / `deploy_lake_rollups.py` |
| 1a · Custom-SQL datasets `retail-ops-journey-events-sql`, `retail-ops-campaign-roi-sql` (Direct query) | `deploy_quick_content.py --apply --only datasets` |
| 1b · Topic `Retail Ops Journey Funnel` (field meanings and synonyms as custom instructions) | `deploy_quick_content.py --apply --only topic` |
| 2a · 538-doc Quick Index corpus → S3 | `deploy_quick_index.py --apply` |
| 2b · S3 knowledge source + 9 knowledge bases (ACLs off, sidecars excluded) | `deploy_quick_content.py --apply --only knowledge` |
| 3a-prereq · Cognito resource server `retail-ops-oem` (scope `retail-ops-oem/read`) + client-credentials app client `retail-ops-quick-mcp` | `cognito_setup.py --apply` (deploy stage `cognito`; secret in SM `retail-ops-dev-quick-mcp-oauth`) |
| 3a · JWT-authorized gateway (`CUSTOM_JWT`) + token-derived claims + Draft-7 schemas | `deploy_gateway_quick.py --apply` |
| 0, 4 · Space `Automotive Retail Operations` + its resources + sharing | `deploy_quick_content.py --apply --only space` |
| 5 · Chat agent `Retail Ops Field Sales Agent` (instructions, space, connector once it exists) + sharing | `deploy_quick_content.py --apply --only agent` |
| 6 · 8 Flows `Retail Ops …` (published) + sharing | `deploy_quick_content.py --apply --only flows` |
| 7 · Analysis + dashboard `Retail Ops Campaign ROI & Funnel` (published) + sharing | `deploy_quick_content.py --apply --only dashboard` |
| 5/7 · Console origin allowed for embedding (`AllowedDomains`) | `deploy_l4.py` (`RETAIL_OPS_QUICK_EMBED_DOMAINS`, stage `l4`) |
| 8 · Per-user Quick reader mapping (replace shared `default/admin` embed) | `quick.py register_reader` + `ENABLE_QUICK=true` (set by the `l4` stage) and `RETAIL_OPS_QUICK_PER_USER_EMBED=1` (set by hand; off in dev, §9) on the service Lambda |
| 9 · Re-discover Flows/dashboards into `/quick` | `deploy_quick.py --apply` / `--status` |
| *(opt)* Gateway write tools (save plan drafts, log guardrails, create tasks) | repo gateway change |

### Track B — By hand (no public API)

| Item | Where | Why |
|---|---|---|
| P · Builders can author chat agents, knowledge bases and Flows | Quick admin | met: user confirmation, see §P |
| 3b · MCP action connector `Retail Ops OEM Read Gateway` + its 4 actions | Connectors | `CreateActionConnector` rejects the MCP type |
| 3b · Link the connector to the space and agent | terminal (`deploy_quick_content --only space,agent`) | runs after the console step |
| 5 · One test chat with the agent | Chat agents | no chat API |
| 6 · One test run of Flow 1; the weekly District Roll-up schedule | Flows | no run or schedule API |
| 2b · Sync the knowledge bases after the corpus changes | Knowledge | no sync API |

**One-line rule:** everything except the MCP connector, the test runs and the Flow
schedule is a **script** (Track A). The connector points at the CUSTOM_JWT gateway
`retail-ops-dev-crosspersona-quick-<gateway-suffix>` and authenticates with the
`retail-ops-quick-mcp` client (secret in Secrets Manager `retail-ops-dev-quick-mcp-oauth`).

---

## Build order

`deploy_quick_content` runs its steps in the order `datasets → topic → knowledge →
space → agent → flows → dashboard` and adds the new dashboard to the space at the end.

| # | Build | Where | Depends on |
|---|---|---|---|
| P | Prerequisites | Quick admin + AWS IAM | — |
| 0 | Space `Automotive Retail Operations` | repo (`--only space`) | P |
| 1 | Datasets incl. custom-SQL `journey_events` + Topic | repo (`deploy_quick_datasets`, `--only datasets,topic`) | P |
| 2 | Knowledge bases (S3) | repo (`deploy_quick_index`, `--only knowledge`) | P |
| 3 | MCP action connector to the AgentCore Gateway | repo gateway → **console** connector (checklist 1) | P |
| 4 | Add resources to the space | repo (`--only space`; the connector after checklist 2) | 0–3 |
| 5 | Field Sales chat agent | repo (`--only agent`); test in the console (checklist 3) | 4 |
| 6 | Field Sales Quick Flows (8) | repo (`--only flows`); test run + schedule in the console (checklist 4, 5) | 5 |
| 7 | Campaign ROI dashboard | repo (`--only dashboard`) | 1 |
| 8 | Access control (not row-scoped) | repo (sharing in every step) | 1, 2 |
| 9 | Re-discover into the repo console | repo (`deploy_quick --apply`) | 6, 7 |

---

## P — Prerequisites

- [x] **AWS Quick Enterprise** subscription (required for MCP connectors). Check: `aws quicksight describe-account-settings --aws-account-id <account-id> --region us-east-1` reports `ENTERPRISE`.
  - *Verified 2026-10-23:* `describe-account-settings` → `Edition: ENTERPRISE`; `describe-account-subscription` → `Edition: ENTERPRISE`, `AccountSubscriptionStatus: ACCOUNT_CREATED`.
- [x] Builders can author chat agents, knowledge bases and Flows.
  - *Met, 2026-10-23, on the owner's confirmation:* the owner confirmed admin rights in AWS Quick. `admin/jdoe` (role `ADMIN`) authored a chat agent, knowledge bases and 8 Flows in this account on 2026-10-07 to 10-14 (the legacy content in "Where the status lives"), and on 2026-10-23 the IAM user `admin` (role `ADMIN`) created this runbook's agent, 9 knowledge bases and 8 Flows through the API. No role, tier or subscription was changed.
  - *For another account:* the API reports role `ADMIN` for both builders, not `ADMIN_PRO` / `AUTHOR_PRO`, and no custom permissions profile is set. The Pro tiers are documented as the ones that build knowledge bases, configure actions, and use chat agents, spaces and Flows; Admin Pro is the same subscription as an AWS Quick Enterprise user.
    Source: [AWS Quick user types](https://docs.aws.amazon.com/quick/latest/userguide/user-types.html).
  - *If a builder can't create these (another account):* check *Manage account → Your subscriptions*, then upgrade only the human builder (a user decision; it adds cost). Upgrading `admin` (the IAM user, also the shared embed user `default/admin`) is optional:
    ```bash
    aws quicksight update-user --aws-account-id <account-id> --namespace default --region us-east-1 \
      --user-name "admin/jdoe" --role ADMIN_PRO \
      --email "$(aws quicksight describe-user --aws-account-id <account-id> --namespace default \
          --region us-east-1 --user-name 'admin/jdoe' --query User.Email --output text)"
    ```
    Or in the console: *Manage account → Manage users → role → Admin Pro*.
  - *Cost:* an Enterprise user is $40/user/month. Professional and Enterprise accounts also pay a flat $250/account/month infrastructure fee.
    Source: [AWS Quick pricing](https://aws.amazon.com/quick/pricing/), plan table and infrastructure-fee FAQ (read 2026-10-23).
  - *Usage:* an Enterprise license includes 18 agent hours a month; overage is $3 per agent hour.
    Source: [AWS Quick pricing](https://aws.amazon.com/quick/pricing/), "Entitlements & overages" FAQ (read 2026-10-23).
  - *Free upgrade:* BI Author licenses on Enterprise accounts get a free upgrade to Enterprise licenses until 12/31/2026.
    Source: [AWS Quick pricing](https://aws.amazon.com/quick/pricing/), "Entitlements & overages" FAQ (read 2026-10-23).
- [x] **Athena access for Quick.** The Quick service role `aws-quicksight-service-role-v0` needs the inline policy `retail-ops-lake-and-data-read` (S3 lake + Athena results, workgroup `retail-ops-dev-lake`, Glue `retail_ops_dev_lake`, DynamoDB read). `deploy_quick_datasets.py` (deploy stage `quick`) puts it on the role. Check: `aws iam get-role-policy --role-name aws-quicksight-service-role-v0 --policy-name retail-ops-lake-and-data-read`. The grant it provides:
  - `s3:GetObject`, `s3:ListBucket` on `arn:aws:s3:::retail-ops-dev-event-lake-<account-id>` and `/*`
  - `s3:PutObject` on `…/athena-results/*`
  - `athena:StartQueryExecution`, `GetQueryExecution`, `GetQueryResults`, `GetWorkGroup` on workgroup `retail-ops-dev-lake`
  - `glue:GetDatabase`, `GetTable(s)`, `GetPartition(s)` on `retail_ops_dev_lake`
  - *Verified 2026-10-23:* `get-role-policy` returns all of the above in five statements: `LakeRead` (also `s3:GetBucketLocation`), `AthenaResultsWrite` (also multipart upload actions), `AthenaWorkgroup` (also `StopQueryExecution`, on `workgroup/retail-ops-dev-lake`), `GlueCatalog` (catalog, `database/retail_ops_dev_lake`, `table/retail_ops_dev_lake/*`) and `DynamoRead` (`DescribeTable`, `GetItem`, `Query`, `Scan` on `table/retail-ops-dev-*`). No re-apply needed.


### P1. Quick groups (who can open the shared content)

Every resource in this runbook (space, topic, knowledge bases, connector, Field Sales
chat agent, Flows, Campaign ROI dashboard, datasets and data sources) is shared with
**AWS Quick groups**; `deploy_quick_content` and `deploy_quick_datasets` make every share.
The groups live in the Quick `default` namespace of the account (they are Quick groups,
not Cognito groups or IAM groups).

**Builders also get direct grants.** The Quick console lists spaces, chat agents and Flows
by direct user permission, not group membership, so the owner set that
`retail-ops-builders` holds is also granted to each builder user: the group's current
members plus the Quick user names in `RETAIL_OPS_QUICK_BUILDERS`. A name that isn't a
registered Quick user is skipped with a note. The grant is idempotent, the dry run prints
each one, and nothing is ever revoked. Viewers stay group-only. *Verified 2026-10-23:*
`admin` and `admin/jdoe` hold the owner set directly on the 25 resources
`deploy_quick_content` creates, on the connector, and on `retail-ops-lake` and its 13
datasets.

| Group | Shares as | Members |
|---|---|---|
| `retail-ops-builders` | Owner / Co-owner | the people who edit this runbook's content (Quick authors and admins) and the IAM user `admin` that runs the scripts; console users with the Administrator role `platform_admin` (SSO allowlist) join as readers, so they can open everything shared |
| `retail-ops-oem-dm` | Viewer | console users with role `oem_dm` |
| `retail-ops-oem-regional` | Viewer | role `oem_regional` |
| `retail-ops-oem-ci-planning` | Viewer | role `oem_ci_planning` |
| `retail-ops-oem-marketing` | Viewer | roles `oem_marketing`, `oem_finance`, `oem_cx` (they land on the A5 marketing screens) |

The role → group map lives in one place, `app/services/platform/quick.py`
(`QUICK_GROUP_BY_ROLE`). Role `oem_sales_ops` has no group; share with those users
directly if they need a resource.

**Create the groups and memberships (repo, idempotent):**

```bash
# dry-run first: lists groups to create and members to add
python3 -m app.infra.deploy_quick_groups
# builders are named by their Quick user name (aws quicksight list-users)
RETAIL_OPS_QUICK_BUILDERS="admin/<your-quick-user>" python3 -m app.infra.deploy_quick_groups --apply
python3 -m app.infra.deploy_quick_groups --status   # members of each group
```

`--apply` creates any missing group, adds each builder in `RETAIL_OPS_QUICK_BUILDERS`
to `retail-ops-builders`, and adds every already-registered console reader to the group
for their role. It never deletes a group or removes a member. Console readers are
registered in Quick the first time they open an embedded dashboard; the service
adds them to their group at that moment (`QuickClient.register_reader`), so no
manual membership is needed for them. `deploy/deploy.sh --apply --only quick`
runs the same step.

Readers are only registered when the service Lambda has `ENABLE_QUICK=true` and
`RETAIL_OPS_QUICK_PER_USER_EMBED=1` (§9). The `l4` stage sets the first; the second is
off by default and set by hand. With it off (dev, 2026-10-23), embeds use the shared
`default/admin` reader and the four `retail-ops-oem-*` groups stay empty.

**Groups decide what someone can open.** Quick content is not row-scoped (§8):
anyone who can open a dataset or dashboard sees all of its rows. Adding a reader
to `retail-ops-oem-dm` therefore gives them the dashboard with national data,
every district and dealer.

- [x] `deploy_quick_groups --status` lists the five groups, with you in `retail-ops-builders`.
  - *Verified 2026-10-23:* the five groups already existed; `RETAIL_OPS_QUICK_BUILDERS="admin/jdoe,admin" … --apply` added both builders to `retail-ops-builders`. `--status` → `retail-ops-builders: admin, admin/jdoe`; the four `retail-ops-oem-*` groups have no members yet, because none of the 13 planned console users (12 OEM persona readers and the console administrator) is registered in Quick. They join their group at first embed. The account also has a group `Sales-Management`, which this repo doesn't manage; leave it.

---

## 0 — Create the space

> **Created by the repo.** `deploy_quick_content --apply --only space` creates the space
> `retail-ops-space` and shares it (owner `retail-ops-builders`, 14 actions; viewer the 4
> `retail-ops-oem-*` groups, `DescribeSpace`/`GetDocument`/`ListDocument`). Sharing the
> space doesn't share what's in it, so the same step shares each resource too (§4, §8).
> Verify: `python3 -m app.infra.deploy_quick_content --status` lists `space retail-ops-space`.

| Field | Value |
|---|---|
| Name | `Automotive Retail Operations` |
| Description | `Field Sales Agent and Campaign Agent; event-lake and knowledge-base backed.` |
| Owner | `retail-ops-builders` |
| Viewer | `retail-ops-oem-dm`, `retail-ops-oem-regional`, `retail-ops-oem-ci-planning`, `retail-ops-oem-marketing` |

*Console fallback:* **Spaces → Create space** with the values above, then **Share**.

- [x] Space created and shared. *Verified 2026-10-23:* `describe_space` → name and description above; `describe_space_permissions` → `retail-ops-builders` (14 actions) and the 4 OEM groups (3 actions).

---

## 1 — Dataset `journey_events` and Topic

> **Provisioned by the repo.**
> `python3 -m app.infra.deploy_quick_datasets --apply` creates the `retail-ops-lake`
> Athena data source and **13 DIRECT_QUERY datasets** over `retail_ops_dev_lake`
> (`journey_events` plus `lake_journeys`, `lake_service`, `lake_rdr`, `lake_csi`,
> `lake_marketintel`, `lake_campaigns`, `lake_recommendations`, `lake_outcomes`,
> `lake_touchpoints`, `lake_service_events`, `lake_agent_traces`, `lake_leads`).
> `deploy_quick_content --apply --only datasets` adds the two **custom-SQL** datasets of
> §1a and §7 on the same source, also DIRECT_QUERY (no SPICE import).
> **Note:** the lake tables are JSON schema-on-read, so numeric columns arrive as
> STRING — cast them in the dataset/analysis (e.g. `CAST(units AS INT)`).

**Who can see them.** Quick only lists a data source or dataset to principals with
permissions on it. On `--apply`, `deploy_quick_datasets` grants the group
`retail-ops-builders` and each builder user (§P1) the standard owner actions on
`retail-ops-lake` and on each of the 13 datasets (idempotent; the dry run prints each
grant), and `deploy_quick_content` does the same for its two datasets. The `quick` stage creates the groups first; if
`retail-ops-builders` is missing, the grant is skipped with a note. The
`retail-ops-oem-*` groups get nothing here: they open the dashboards and Flows shared
with them. Check (granted 2026-10-23):

```bash
aws quicksight describe-data-source-permissions --aws-account-id <account-id> --region us-east-1 \
  --data-source-id retail-ops-lake --query 'Permissions[].Principal'   # …:group/default/retail-ops-builders
```

Fallback if the stage cannot run: share them from *Manage account → Manage assets*.

### 1a. Dataset `journey_events` (custom SQL)

> **Created by the repo:** dataset `retail-ops-journey-events-sql`, named
> `Retail Ops · journey_events (funnel)`, custom SQL on `retail-ops-lake`, Direct query.
> Verify: `aws quicksight describe-data-set --aws-account-id <account-id> --data-set-id retail-ops-journey-events-sql --query 'DataSet.[Name,ImportMode]'`.

The events table holds `stage` only in the event payload (`stage`, or `to` on a
`stage_changed` event) and no dealer, so the SQL takes the dealer, district and market
from the journey (`lake_journeys`). The runbook's first draft read `payload.dealer_id`
and `actor.role`, which no row has.

```sql
SELECT e.journey_id, e.event_type,
       CAST(from_iso8601_timestamp(e.event_ts) AS timestamp) AS event_ts,
       e.correlation_id, e.evidence, e.dt,
       e.stage,                                   -- payload.stage, else payload.to
       CASE e.stage WHEN 'DISCOVER' THEN 1 … WHEN 'OWN' THEN 8 END AS stage_order,
       COALESCE(json_extract_scalar(e.payload, '$.source'), j.source) AS source,
       j.dealer_id, j.district_id, j.market_id AS market,
       json_extract_scalar(e.actor, '$.type') AS actor_type
FROM (SELECT *, COALESCE(json_extract_scalar(payload, '$.stage'),
                         json_extract_scalar(payload, '$.to')) AS stage
      FROM retail_ops_dev_lake.journey_events) e
LEFT JOIN retail_ops_dev_lake.lake_journeys j ON j.journey_id = e.journey_id
```

The full text is `JOURNEY_SQL` in `app/infra/deploy_quick_content.py`.

- [x] Rows and fields. *Verified 2026-10-23 in Athena:* 8,339 rows (not the ≈31k an earlier draft expected), 550 journeys; `stage` is set on 2,909 rows (the stage-moving events), `source` and `dealer_id` on 7,737. Distinct journeys by stage: DISCOVER 500, QUALIFY 310, CONFIGURE 405, TRADE_IN 224, FINANCE 252, AGREE 210, DELIVER 193, OWN 181.
- [x] Shared with `retail-ops-builders` (owner). The OEM groups reach it through the topic and the dashboard.

*Console fallback:* **Datasets → New dataset → existing source `Retail Ops event lake (Athena)` → Use custom SQL**, paste `JOURNEY_SQL`, Direct query, name `Retail Ops · journey_events (funnel)`.

### 1b. Topic

> **Created by the repo:** topic `retail-ops-journey-funnel`, named
> `Retail Ops Journey Funnel` (Topic V2, published), on the §1a dataset. The V2 API takes
> no per-field synonyms, so the field meanings, synonyms (stage: funnel stage, step;
> source: channel, platform; dealer_id: dealer, store, rooftop) and the date field
> `event_ts` are the topic's custom instructions (`TOPIC_INSTRUCTIONS`). Shared: owner
> `retail-ops-builders`, viewer (`DescribeTopic`, `PassTopic`) the 4 OEM groups.
> Verify: `deploy_quick_content --status` lists `topic retail-ops-journey-funnel` (the AWS
> CLI here has no topic V2 commands).

- [x] Created, published and shared, 2026-10-23.

*Console fallback:* **Topics → Create topic → dataset `Retail Ops · journey_events (funnel)`**, name `Retail Ops Journey Funnel`, paste `TOPIC_INSTRUCTIONS` as the topic's instructions, **Share**.

---

## 2 — Knowledge bases from `PLAT_KnowledgeCollections`

**Table shape:** 538 items, key `doc_id`. Each item has exactly 8 attributes: `doc_id`, `kind`, `collection_id` (`KC-<MARKET>-<kind>`), `market_id` (`us`/`de`), `title`, `source_ref`, `body_ref`, `index_target`. All 538 have `index_target = quick_index`.

**The table holds no document text.** Every `body_ref` is `corpus:<doc_id>`; step 2a rebuilds each body from the synthetic-data generator's export and writes it to S3.

### 2a. Export to S3 (repo)

> **Provisioned by the repo.** `python3 -m app.infra.deploy_quick_index --apply` rebuilds
> each `corpus:` body from the generator's local export
> (`sample-data/synthetic/<market>/quick_index/`), masks email/phone PII, and writes
> **538 markdown docs** to `s3://retail-ops-dev-event-lake-<account-id>/quick-index/<kind>/<market>/<doc_id>.md`
> in the format below. Verify with `deploy_quick_index.py --status` (expect 538).

| `kind` | Docs (us + de) | `source_ref` example | Rebuilt from |
|---|---|---|---|
| `crm_notes` | 240 | `synthetic:crm_note; entity:C-DE-00350` | generator corpus (body) |
| `templates` | 84 | `entity:T-DE-FINANCE-chat; config:nurture v1` | generator corpus |
| `transcripts` | 80 | `entity:J-DE-00368` | generator corpus |
| `objection_strategies` | 34 | `curated:objection_strategy:timing_not_ready` | generator corpus |
| `bulletins` | 24 | `entity:COMM-US-00006` | generator corpus |
| `prior_plans` | 24 | `entity:AP-US-00967` | generator corpus |
| `repair_procedures` | 24 | `entity:LOP-COOLANT` | generator corpus |
| `product_guides` | 14 | `entity:MER-RIDGELINE` | generator corpus |
| `tsbs` | 14 | `entity:U0100` | generator corpus |

- The generator produces each `corpus:` body; `deploy_quick_index.py` reads it and writes the files.
- PII (email/phone patterns) is masked on export; synthetic names are kept (fictional by design, synthetic-data plan principle 7, *Classification-aware*).

Each file is written as:

```
s3://retail-ops-dev-event-lake-<account-id>/quick-index/<kind>/<market_id>/<doc_id>.md
```

```markdown
# <title>
source_ref: <source_ref>
collection_id: <collection_id>
market: <market_id>

<body text>
```

- [x] Markdown doc count under `quick-index/` is **538** (`deploy_quick_index.py --status`, which counts `.md` only).
  - *Verified 2026-10-23:* 538 `.md` docs with the per-kind counts above. The prefix holds 1,076 objects in all: each doc also has a `<doc>.md.metadata.json` sidecar, written by the Bedrock Knowledge Base stage (`kb`, `deploy_knowledge_base.py --sidecars`).
- [ ] *(Optional)* Schedule it nightly (EventBridge Scheduler → Lambda) if the corpus is regenerated regularly.

### 2b. Create the knowledge bases

> **Created by the repo.** `deploy_quick_content --apply --only knowledge` creates the S3
> knowledge data source `retail-ops-knowledge-s3` (`Retail Ops Knowledge S3`, bucket
> `s3://retail-ops-dev-event-lake-<account-id>`) and one knowledge base per prefix below,
> id `retail-ops-kb-<kind>` (for example `retail-ops-kb-crm-notes`). Each one: document-level
> ACLs **off** (Quick content is not row-scoped, §8; the setting can't change later),
> image/audio/video extraction off, exclusion pattern `*.metadata.json` (the Bedrock
> sidecars of 2a share the folders), deletion safeguard 20%. Quick runs the first sync on
> create. Shared: owner `retail-ops-builders`, viewer (`DescribeKnowledgeBase`) the 4 OEM
> groups. Verify: `deploy_quick_content --status` prints each knowledge base with
> `docs=` and `sync=`.

| Knowledge base | S3 prefix | Docs | Used by |
|---|---|---|---|
| `Retail Ops CRM Notes` | `quick-index/crm_notes/` | 240 | A6, A3 |
| `Retail Ops Templates` | `quick-index/templates/` | 84 | A3 (nurture) |
| `Retail Ops Transcripts` | `quick-index/transcripts/` | 80 | A3 (customer conversations; not used by the A6 flows) |
| `Retail Ops Objection Strategies` | `quick-index/objection_strategies/` | 34 | A6 Win Strategy, A3 |
| `Retail Ops Bulletins` | `quick-index/bulletins/` | 24 | A6 Sales Ops Digest |
| `Retail Ops Prior Plans` | `quick-index/prior_plans/` | 24 | A6 Account Plan |
| `Retail Ops Product Guides` | `quick-index/product_guides/` | 14 | A6, A3 |
| `Retail Ops Repair Procedures` | `quick-index/repair_procedures/` | 24 | A7 |
| `Retail Ops TSBs` | `quick-index/tsbs/` | 14 | A7 |

- [x] **Document-level ACLs:** off, set at creation.
- [x] After the first sync, each knowledge base reports the doc count in the table above. *Verified 2026-10-23:* all 9 `ACTIVE`, latest sync `COMPLETED`, and every count equals the table (538 in all), so the sidecars were not indexed.
- [x] Shared with the persona groups.
- [ ] **Sync after the corpus changes.** The API has no sync call, and none of the repo's 13 scheduler jobs exports the corpus; after `deploy_quick_index --apply`, choose **Sync now** on each knowledge base (Console checklist, last note).
- *Check* (chat, space attached): *"What are the top objection-handling notes for a customer worried about EV charging range? Show the source_ref for each."*
- *Cost:* index storage is the raw size of the source files. Enterprise includes 50 GB per user, pooled across the account, and overage is $5 per GB per month; this corpus is about 0.1 MB (101,704 bytes of Markdown).
  Source: [AWS Quick pricing](https://aws.amazon.com/quick/pricing/), "Storage, fees & getting started" FAQ (read 2026-10-23).

*Console fallback:* **Knowledge → Amazon S3 → + → + Add account** (bucket URL
`s3://retail-ops-dev-event-lake-<account-id>/quick-index/`), then from the integration →
⋯ → **Create knowledge base** → **Add specific content** with the prefix, ACLs off and an
exclusion pattern `*.metadata.json`.

---

## 3 — MCP action connector to the AgentCore Gateway

### 3a. Gateway (repo, Track A)
> **Provisioned by the repo.** `python3 -m app.infra.deploy_gateway_quick --apply`
> builds the **CUSTOM_JWT** gateway for Quick: `retail-ops-dev-crosspersona-quick-<gateway-suffix>`
> (MCP, authorizer `CUSTOM_JWT`), Lambda target `oem-read` → `retail-ops-dev-gateway-oem`.
- Gateway (discovery URL = Cognito pool `<user-pool-id>` OIDC; allowed client `<quick-mcp-client-id>`, scope `retail-ops-oem/read`).
- Cognito resource server `retail-ops-oem` (scope `read`) + client-credentials app client `retail-ops-quick-mcp`; secret in Secrets Manager `retail-ops-dev-quick-mcp-oauth`.
- The gateway Lambda derives scope from the validated token and **ignores any model-supplied `claims`** (`app/services/gateway/handler.py` `_jwt_context`). A client-credentials token resolves to one OEM service principal with OEM read-across (`_SERVICE_OEM_SCOPE`), not to the Quick user, so dealer scoping on this path is the agent's account filter (§5).
- Tool `inputSchema` is JSON-Schema Draft 7 (`required` root array; `$schema`/`additionalProperties` omitted — AgentCore rejects them).
- [x] Check: a Cognito client-credentials token + `tools/list` returns the 4 tools; no token → 401 (handles and token command in Appendix A §A6).
  - *Verified 2026-10-23:* with a token, HTTP 200 and `oem-read___oem_get_account`, `oem-read___oem_get_alerts`, `oem-read___oem_get_incentive_programs`, `oem-read___oem_list_accounts`, plus the Gateway's built-in `x_amz_bedrock_agentcore_search`; without a token, 401.

### 3b. Connector (console)

**By hand:** `CreateActionConnector` rejects the MCP type. Checked 2026-10-23 with the
values below: `InvalidParameterValueException`, while the same call with type
`GENERIC_HTTP` succeeded (that probe connector was deleted). The API's connector type list
has no MCP entry, and no call lists a connector's actions by name.

**Connectors → Create for your team → Model Context Protocol (MCP)**

| Field | Value |
|---|---|
| Name | `Retail Ops OEM Read Gateway` (no parentheses: connector names allow letters, digits, spaces, `_` and `-`; the scripts find the connector by this exact name) |
| Description | `Scoped and cited OEM reads for accounts, alerts and incentive programs` (descriptions allow letters, digits, spaces and `_ . , ! ? -`) |
| MCP server endpoint | `https://retail-ops-dev-crosspersona-quick-<gateway-suffix>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp` |
| Connection type | Public network |
| Authentication | **Service authentication (Service-to-Service)** — client-credentials. ⚠️ **Not** "User authentication (OAuth)" — that drives an authorization-code *redirect* and fails with `redirect_mismatch` (our client has no callback URLs). Service auth takes only Client ID + Secret + Token URL; **no Authorization URL, no Redirect URL.** |
| Client ID | `<quick-mcp-client-id>` |
| Client secret | the `client_secret` of Secrets Manager `retail-ops-dev-quick-mcp-oauth` (`aws secretsmanager get-secret-value --secret-id retail-ops-dev-quick-mcp-oauth --query SecretString --output text`) |
| Token URL | `https://retail-ops-dev-<domain-suffix>.auth.us-east-1.amazoncognito.com/oauth2/token` |
| Scope | `retail-ops-oem/read` |

**Exists since 2026-10-23:** built by the owner, id `<connector-id>`
(Appendix A9), `CREATION_SUCCESSFUL`.

- [x] Turn on all 4 actions: `oem_list_accounts`, `oem_get_account`, `oem_get_alerts`, `oem_get_incentive_programs`. The Gateway names them `oem-read___oem_…` and also lists its built-in `x_amz_bedrock_agentcore_search`, which this runbook doesn't use. *Done 2026-10-23;* the API lists 5 enabled action ids and doesn't name them (checklist step 1).
- [x] Re-run `deploy_quick_content --apply --only space,agent`: it shares the connector (owner `retail-ops-builders` and each builder user, viewer `DescribeActionConnector`/`ListActionConnectors` for the 4 OEM groups), adds it to the space and links it to the agent. *Done 2026-10-23.*
- [ ] After any tool change on the gateway, choose **Sync** on the connector.

---

## 4 — Add resources to the space

> **Created by the repo.** `deploy_quick_content --apply --only space` adds, idempotently
> and only what exists: the topic, the two custom-SQL datasets, six lake datasets
> (`retail-ops-lake-journeys`, `-campaigns`, `-rdr`, `-csi`, `-marketintel`, `-service`),
> the 9 knowledge bases, the dashboard and, once checklist step 1 is done, the connector.
> *Verified 2026-10-23:* `list_space_resources` → 1 topic, 8 datasets, 9 knowledge bases,
> 1 dashboard and 1 action connector (20).

- [x] Datasets, topic, knowledge bases, dashboard.
- [x] Actions: `Retail Ops OEM Read Gateway` (Console checklist step 2). Added 2026-10-23.
- *(Optional, console)* Files: guardrail policy one-pager, account-plan template.
- *Check* (chat, space attached): *"Using journey_events, show the count of events by event_type for the last 90 days."*

---

## 5 — Field Sales chat agent (`Retail Ops Field Sales Agent`)

> **Created by the repo.** `deploy_quick_content --apply --only agent` creates the chat
> agent `retail-ops-field-sales-agent` as **Published**, with the values below (instructions
> in `AGENT_INSTRUCTIONS`), linked to the space, and once the connector exists (Console
> checklist steps 1–2) to `Retail Ops OEM Read Gateway`. Shared: owner `retail-ops-builders`
> and each builder user, viewer (`DescribeAgent`) `retail-ops-oem-dm`, `retail-ops-oem-regional`,
> `retail-ops-oem-ci-planning`. *Verified 2026-10-23:* `describe_agent` → `PUBLISHED`, 1 space,
> 3 starter prompts, the instructions with GR-001…008, and the connector under
> `ActionConnectors` (linked that day); `describe_agent_permissions` → the principals above.
> The one test chat is Console checklist step 3 (no chat API).

*Console fallback:* **Chat agents → Create chat agent → Skip** (builder mode) with:

| Section | Value |
|---|---|
| Name | `Retail Ops Field Sales Agent` |
| Description | Account-level planning and field toolkit for OEM district and regional managers and C&I planners. Every answer cited and checked against GR-001 to GR-008. |
| Response style | Executive; "Lead with the answer, then cited figures; bullets for lists longer than three items." |
| Knowledge sources → Link | Space **Automotive Retail Operations** |
| Actions → Link | `Retail Ops OEM Read Gateway` → all 4 actions |
| Welcome message | "Ask about your dealers, performance, alerts, or programs. I'll cite every figure and draft plans for your review." |
| Suggested prompts | "Which of my dealers are below objective this month?" · "Summarize open HIGH alerts for my district" · "Draft an account plan for D-105" |

**Persona instructions:**

```
You are the Field Sales Agent for automotive field sales. Your users are OEM district
managers, regional sales managers, and C&I planners (secondary: Sales Ops comms, OEM finance).

Scope: work at the ACCOUNT level — dealer, dealer group, fleet, or commercial account. Only use
accounts returned by the OEM Read actions; never discuss dealers outside the caller's scope.

How to answer:
- Account, alert, and incentive facts come from the OEM Read actions (oem_get_account,
  oem_list_accounts, oem_get_alerts, oem_get_incentive_programs).
- Funnel and journey questions use the journey_events data and the Retail Ops Journey Funnel topic.
- CRM notes, transcripts, objection strategies, bulletins, prior plans, and product guides come
  from the Retail Ops knowledge bases.
- Cite every figure and claim: account id, alert id, C&I program id, dataset/topic, or the
  document's source_ref. If you cannot source a figure, say so instead of estimating. Label any
  scenario or forecast as an estimate.

Guardrails — check both the request and your draft before replying. Never:
GR-001 recommend wholesale reductions · GR-002 use dealer termination/separation language ·
GR-003 give specific pricing guidance to dealers · GR-004 recommend hiring or firing ·
GR-005 promise or commit allocations · GR-006 give legal/regulatory interpretations ·
GR-007 give financial advice beyond standard metrics · GR-008 disparage competitors.
If a request would breach a rule, give the closest safe alternative (e.g. a performance-gap
analysis instead of a wholesale cut), name the rule that applied, and continue.

Human in the loop: everything you produce is a draft for the user's review. You never publish a
plan, contact a dealer, or change allocations.
```

- [ ] **Test once** (Console checklist step 3, after step 2 links the connector). The agent answered the owner on 2026-10-23 and called the OEM reads; the three checks below are not confirmed yet:
  - "List my accounts and their open alerts" → scoped and cited
  - "Should we cut D-105's wholesale next month?" → declined (GR-001/GR-005), gap analysis offered
  - "What does the EV charging bulletin say?" → cites a `source_ref`
- [x] **Published and shared** (§P1): Viewer = `retail-ops-oem-dm`, `retail-ops-oem-regional`, `retail-ops-oem-ci-planning`; Owner = `retail-ops-builders`. Created by the repo, 2026-10-23.
- The repo console embeds dashboards only (§7); it has no chat-agent panel, so users open the agent in AWS Quick.

---

## 6 — Field Sales Quick Flows (eight flows)

A **Quick Flow** is a saved, multi-step sequence you build in the **Flow editor**
and run in **Run mode**. A6 has eight document outputs (Account Plan, Opportunity
Brief, Win Strategy, Action Plan, Contact Report, Meeting Package, District
Roll-up, Sales Ops Digest); each is one Flow built from the same pattern:
**collect input → fetch data (Application actions + Quick data + Dashboards and
topics) → draft with the A6 chat agent → review the draft.** The step names below
are the ones the AWS Quick console uses (Quick Flows pages under References).

> **Created by the repo.** `deploy_quick_content --apply --only flows` creates the 8 Flows
> below. `CreateFlow` publishes them, `UpdateFlow` rewrites both the draft and the published
> version, and the step shares them: co-owner `retail-ops-builders` and each builder user
> (13 actions), viewer (7 actions) `retail-ops-oem-dm`, `retail-ops-oem-regional`,
> `retail-ops-oem-ci-planning`. The Flow ids are assigned by Quick and recorded in
> `app/infra/quick-content-config.json` (Appendix A9). The definitions are `FLOWS` in
> `app/infra/deploy_quick_content.py`; step ids are deterministic, so a re-run updates a
> Flow in place. *Verified 2026-10-23:* `list_flows` → all 8 `PUBLISHED`; `describe_flow`
> (DRAFT and PUBLISHED) returns the repo's definition unchanged; `get_flow_permissions`
> lists the principals above. Test run and schedule: Console checklist steps 4 and 5.
>
> **Step references (fixed 2026-10-23).** A step reads an earlier one only through
> `@<step id>` in its prompt, followed by a space; there is no separate dependency field.
> The console writes version-4 UUIDs as step ids. The first API-built Flows used
> version-5 ids (the API accepted them), and the console flagged every step that
> referenced one: "Invalid reference in your prompt" and "Invalid dependencies in
> `OEM facts`", `Notes`, `Account plan draft`, `Action plan draft`. The builder now writes
> version-4-shaped ids (still derived from the Flow and step title), and
> `reference_errors()` rejects a definition with a dangling, forward, title-style or
> wrongly shaped reference. The agent, knowledge base and topic references
> (`agentArn`, `unstructured-data-set/<knowledge base id>`, topic `scopeId`) already
> matched the console's shape and resolve to existing resources. Console fallback:
> Console checklist step 4.
>
> **One difference from §6.1/§6.2:** the API can't map an MCP action id to its tool name
> (`describe_action_connector` returns only the enabled action ids), and the connector is
> console-built (§3b), so the repo's Flows have no **Application actions** step. Instead, a
> first **Chat agent** step titled `OEM facts` asks `Retail Ops Field Sales Agent` to call
> only the OEM read tools that Flow needs, by name and with explicit arguments
> (`oem_get_account with account_id @Account ID`, `oem_get_alerts with market_id "us"`, …),
> all in a single turn, without searching the knowledge bases, topics or the web, and to
> return the kept records as JSON with no commentary. Alerts are matched to an account on
> `dealer_id`, the only link the Gateway's alert records carry; each alert has its title as
> `message`, plus `category`, `status`, `opened_at` and `resolved_at`, so "open alerts" can be
> kept on `status`. Programs carry `started_at`, `ended_at`, `window`, `goal` and `status`, so
> the Sales Ops Digest keeps the programs in its date range on those dates (record fields:
> Appendix A5). The **Quick data** and
> **Dashboards and topics** steps and the drafting **Chat agent** step then
> `@`-reference it. *Optional, console:* in each `OEM facts` step, narrow **Data and apps**
> to `Retail Ops OEM Read Gateway` and turn **Web search** off.

| Flow | Steps (type · title) |
|---|---|
| `Retail Ops Account Plan` | Text `Account ID` (default `ACC-US-00001`) · Chat agent `OEM facts` (`oem_get_account` + `oem_get_alerts`, alerts kept on the account's `dealer_id`) · Quick data `Notes` (CRM Notes, Prior Plans) · Chat agent `Account plan draft` |
| `Retail Ops Opportunity Brief` | Text `Account ID` · Text `Opportunity` · Chat agent `OEM facts` (`oem_get_account` + `oem_get_incentive_programs`, relevant programs kept) · Quick data `Notes` (CRM Notes) · Chat agent `Opportunity brief draft` |
| `Retail Ops Win Strategy` | Text `Account ID` · Text `Meeting date` · Chat agent `OEM facts` (`oem_get_account`) · Quick data `Playbook` (CRM Notes, Objection Strategies, Product Guides) · Chat agent `Win strategy draft` |
| `Retail Ops Action Plan` | Text `Account or alert ID` · Chat agent `OEM facts` (an `ACC-` id: `oem_get_account` + `oem_get_alerts`; an alert id: `oem_get_alerts` + `oem_list_accounts`, matched on `dealer_id`) · Chat agent `Action plan draft` |
| `Retail Ops Contact Report` | Text `Account ID` · Text `Meeting notes` · Files `Recording or transcript file` · Chat agent `OEM facts` (`oem_get_account`) · Quick data `Plan history` (Prior Plans) · Chat agent `Contact report draft` |
| `Retail Ops Meeting Package` | Text `Account ID` · Text `Meeting type` · Chat agent `OEM facts` (`oem_get_account` + `oem_get_alerts`) · Quick data `Notes` (CRM Notes, Prior Plans) · Dashboards and topics `Funnel` (topic `Retail Ops Journey Funnel`) · Chat agent `Meeting package draft` |
| `Retail Ops District Roll-up` | Text `District ID` (default `DIST-07`) · Chat agent `OEM facts` (`oem_list_accounts` + `oem_get_alerts`, kept for the district) · Dashboards and topics `Funnel` · Chat agent `District roll-up draft` |
| `Retail Ops Sales Ops Digest` | Text `From date` · Text `To date` · Chat agent `OEM facts` (`oem_get_incentive_programs`, kept for the range) · Quick data `Bulletins` (Bulletins) · Chat agent `Sales ops digest draft` |

Each drafting step requires the sections listed in §6.2, cites every figure, runs the
GR-001 to GR-008 check and returns a draft for review. §6.0–§6.2 below are the console
build, kept as the fallback.

**Before you start a console build (all must exist — a Flow references them by name):**
§P1 groups · §3b connector `Retail Ops OEM Read Gateway` with its 4 actions enabled · §2 knowledge
bases (`Retail Ops CRM Notes`, `Retail Ops Prior Plans`, …) · §1b topic `Retail Ops Journey Funnel`
· §5 chat agent `Retail Ops Field Sales Agent` (launched and shared with you) ·
your user has a subscription that allows creating flows (admin: *Custom
permissions* can restrict Flows; the **General knowledge** step additionally
needs *"Enable bedrock model usage in General knowledge step for output
refinement"* — not required for the pattern below, which uses the chat agent).

### 6.0 How the Flow editor works (read once)

| Concept | What it is in the console |
|---|---|
| **Flows** | Left navigation pane → **Flows** (the Flows library: *My flows*, shared flows, *My schedules*). |
| **Create** | **Create Flow** → either **type a natural-language prompt → Generate Flow** (Quick builds the steps for you) or **Create a blank flow** (you add every step). Use *blank* for the 8 A6 flows so each step is deliberate; use the prompt method only as the "one-shot" fallback. |
| **Editor mode / Run mode** | Editor = build and configure steps. Run = execute with real inputs; a chat panel lets you ask follow-ups. Toggle at the top of the flow. |
| **Add step** | Toolbar → **Add step** → pick a type → drag it into position → configure it in the right-hand panel. |
| **Step groups** | **User input:** Text · Files. **Data insights:** Quick data (spaces / knowledge bases) · Dashboards and topics. **Actions:** Application actions (connectors, incl. MCP). **AI responses:** Chat agent · Research · Web search · General knowledge · UI agent · Create Image. **Flow logic:** Reasoning group (conditions / loops / validation). |
| **Passing data between steps (`@`)** | Every step has a **Title**. In any later step's prompt, type **`@`** and pick a previous step from the menu; its output is inserted as context (e.g. `@Account ID`). There is no `{{variable}}` syntax and no argument-mapping form — action parameters are given in the prompt, by text or by `@` reference. |
| **Output** | There is no separate "Output" step. Whatever the last step produces is the flow's result in Run mode. A valid flow needs at least one step that produces output (an AI response / data insight step) or an action step. |
| **Save / Publish / Share** | **Save** keeps a draft only you (and co-owners) see. **Publish → Publish** makes the current version visible to everyone the flow is shared with. **Share** → individuals / groups / everyone → **Viewer** (run only) or **Co-owner** (edit, share, publish). |
| **Duplicate** | Flows library → **⋮** next to a flow → **Duplicate**. Build Flow 1 fully, then duplicate it 7 times and edit. |
| **Schedule** | Open the flow in **Run mode** → **scheduling icon** → **Create schedule**. Schedules are private to the person who creates them. |
| **Rules** | Flow names are unique per Quick account, ≤ 128 chars. A reasoning group needs ≥ 1 instruction and cannot contain another reasoning group or a Research step. Chat agent steps are single-turn. |

### 6.1 Worked example — Flow 1 `Retail Ops Account Plan` (console fallback)

**Create the flow**
1. Left nav → **Flows** → **Create Flow** → **Create a blank flow**.
2. **Name** `Retail Ops Account Plan` · **Description** `Quarterly account plan for one dealer account, drafted by the Field Sales Agent from OEM reads + CRM notes + prior plans. Draft for review.` → the Flow editor opens in Editor mode.

**Add the steps** (for each: **Add step** → type → drag below the previous step → fill the panel). Use the Titles exactly as written — they are what you will `@`-reference.

| # | Add step → | Title | Configuration panel |
|---|---|---|---|
| 1 | **User input → Text** | `Account ID` | Placeholder `e.g. ACC-US-00001` · Default Value `ACC-US-00001` · **Allow override of default value** ON |
| 2 | **Actions → Application actions** | `Account` | Connector `Retail Ops OEM Read Gateway` · Action `oem_get_account` · Prompt: `Get the account whose account_id is @Account ID.` |
| 3 | **Actions → Application actions** | `Alerts` | Connector `Retail Ops OEM Read Gateway` · Action `oem_get_alerts` · Prompt: `Get the open alerts for market_id "us". Keep only alerts whose dealer or account matches @Account (account_id, dealer_id or district_id).` |
| 4 | **Data insights → Quick data** | `Notes` | Prompt: `Find CRM notes and prior account plans about the account in @Account (match on account_id, dealer name or dealer_id). Return each passage with its source_ref.` · **Link specific resources** → select knowledge bases `Retail Ops CRM Notes` and `Retail Ops Prior Plans` only |
| 5 | **AI responses → Chat agent** | `Account plan draft` | Chat agent `Retail Ops Field Sales Agent` · **Data and apps**: leave as the agent's defaults · **Web search** OFF · Prompt instruction: see below |

**Step 5 — Prompt instruction (paste, then re-insert the three `@` references from the menu so they resolve):**
> Draft a quarterly **account plan** for this account. Account data: @Account. Open alerts: @Alerts. Relevant notes and prior plans: @Notes. Sections, in this order: **Performance vs objective · Gaps · Opportunities · Recommended actions · Risks.** Cite the source of every figure (account id, alert id, or the note's `source_ref`). Run the GR-001…008 guardrail check on your own draft; if a rule would fire, name the rule and give the safe alternative instead. Return it as a **draft for human review** — do not publish, send, or save it anywhere.

> Do **not** add an action step that writes anywhere (the connector only exposes reads, so this is enforced; keep it that way).

**Save, test, publish, share**
6. **Save**.
7. Switch to **Run mode** → leave `Account ID` = `ACC-US-00001` (Decatur Meridian, `DIST-07`) → **Run**. Check: steps 2–4 return data (step 3 may be empty if the account has no open alerts — that is fine), step 5 returns the five sections, every figure has a citation, and the text says it is a draft.
8. Back in Editor mode fix anything, **Save** → **Publish** → **Publish** (confirm).
9. **Share** → **Groups** (§P1) → `retail-ops-oem-dm`, `retail-ops-oem-regional`, `retail-ops-oem-ci-planning` as **Viewer**; `retail-ops-builders` as **Co-owner** → **Share**.

**One-shot alternative (faster, less control):** **Flows → Create Flow**, paste the
prompt below in the natural-language field → **Generate Flow** → review the generated
steps against the table above (rename Titles, fix the connector/action, link the two
knowledge bases) → **Save**.
> Create a flow "Retail Ops Account Plan". Ask the user for an account_id (default ACC-US-00001). Using the Retail Ops OEM Read Gateway connector, call oem_get_account for that account and oem_get_alerts for market "us". Search the Retail Ops CRM Notes and Retail Ops Prior Plans knowledge bases for that account. Then use the "Retail Ops Field Sales Agent" chat agent to draft a quarterly account plan with sections: performance vs objective, gaps, opportunities, recommended actions, risks; cite every figure; run the GR-001…008 guardrail check; return a draft for review and do not publish.

### 6.2 Flows 2–8 — duplicate Flow 1 and edit (console fallback)

For each row: Flows library → **⋮** on `Retail Ops Account Plan` → **Duplicate** → rename →
change the steps as listed → **Save → Run (test) → Publish → Share** (same groups as
6.1 step 9). Column "Steps" uses the same *type → Title → key settings* form as 6.1;
the Chat agent step is always last and always `Retail Ops Field Sales Agent`.
Every data step shows the prompt to paste into its configuration panel; after
pasting, re-insert each `@` reference from the menu so it resolves (as in 6.1).

| # | Flow name | Steps (replace Flow 1's steps 1–4 with these; keep the agent step last) | Agent prompt — sections to require |
|---|---|---|---|
| 2 | `Retail Ops Opportunity Brief` | Text `Account ID` · Text `Opportunity` (placeholder *"Describe the opportunity in 1–3 sentences"*) · Application actions `Account` (`oem_get_account`; prompt *"Get the account whose account_id is @Account ID."*) · Application actions `Programs` (`oem_get_incentive_programs`; prompt *"Get incentive programs for market_id "us". Keep those relevant to @Opportunity."*) · Quick data `Notes` (KB `Retail Ops CRM Notes`; prompt *"Find CRM notes relevant to @Opportunity. Return each passage with its source_ref."*) | Opportunity · Customer context · Eligible programs · Next steps |
| 3 | `Retail Ops Win Strategy` | Text `Account ID` · Text `Meeting date` · Application actions `Account` (`oem_get_account`; prompt *"Get the account whose account_id is @Account ID."*) · Quick data `Playbook` (KBs `Retail Ops CRM Notes`, `Retail Ops Objection Strategies`, `Retail Ops Product Guides`; prompt *"Find objection strategies, product points and CRM history relevant to the account in @Account. Return each passage with its source_ref."*) | Objectives · Stakeholders · Value points · Objection handling · Asks |
| 4 | `Retail Ops Action Plan` | Text `Account or alert ID` (placeholder *"ACC-… or an alert id"*) · Application actions `Alerts` (`oem_get_alerts`; prompt *"Get the open alerts for market_id "us". Keep the alert(s) matching @Account or alert ID."*) · Application actions `Account` (`oem_get_account`; prompt *"Get the account whose account_id is in @Account or alert ID, or the account named in @Alerts."*) | Issue · Root-cause hypotheses · Actions with owners and milestones |
| 5 | `Retail Ops Contact Report` | Text `Account ID` · Text `Meeting notes` (placeholder *"Paste notes or a transcript"*) · **Files** `Recording or transcript file` (optional; **Allow override** ON, no default) · Application actions `Account` (`oem_get_account`; prompt *"Get the account whose account_id is @Account ID."*) · Quick data `Plan history` (KB `Retail Ops Prior Plans` only; prompt *"Find the account plans for the dealer in @Account (match on dealer_id). For each, return the plan id, version, status and its source_ref."*) | Attendees · Discussion · Commitments · Follow-ups with deadlines · Next visit focus (tie follow-ups to the plan actions in @Plan history) |
| 6 | `Retail Ops Meeting Package` | Text `Account ID` · Text `Meeting type` · Application actions `Account` (`oem_get_account`; prompt *"Get the account whose account_id is @Account ID."*) · Application actions `Alerts` (`oem_get_alerts`; prompt *"Get the open alerts for market_id "us". Keep only alerts whose dealer or account matches @Account."*) · Quick data `Notes` (KBs `Retail Ops CRM Notes`, `Retail Ops Prior Plans`; prompt *"Find CRM notes and prior account plans about the account in @Account (match on account_id, dealer name or dealer_id). Return each passage with its source_ref."*) · **Dashboards and topics** `Funnel` (Quick Sight source **Topic** → `Retail Ops Journey Funnel`; prompt *"Journey counts by stage and source for the dealer in @Account, last 90 days."*) | Agenda · KPI snapshot · Plan status · Talking points |
| 7 | `Retail Ops District Roll-up` | Text `District ID` (default `DIST-07`) · Application actions `Accounts` (`oem_list_accounts`; prompt *"List accounts for market_id "us". Keep those whose district_id is @District ID."*) · Application actions `Alerts` (`oem_get_alerts`; prompt *"Get the open alerts for market_id "us". Keep alerts for the accounts in @Accounts."*) · Dashboards and topics `Funnel` (Topic `Retail Ops Journey Funnel`; prompt *"Journey counts by stage for district @District ID, last 30 days."*) | District KPIs · Accounts below threshold · Top alerts · Drafted actions |
| 8 | `Retail Ops Sales Ops Digest` | Text `From date` · Text `To date` · Application actions `Programs` (`oem_get_incentive_programs`; prompt *"Get incentive programs for market_id "us". Keep those that start, change or end between @From date and @To date."*) · Quick data `Bulletins` (KB `Retail Ops Bulletins`; prompt *"Find bulletins published between @From date and @To date. Return each passage with its source_ref."*) | New or changed programs · Deadlines · What to action this week |

Notes on the table:
- **`market_id`:** the OEM service scope has no default market, so every
  `oem_list_accounts` / `oem_get_alerts` / `oem_get_incentive_programs` prompt must
  say `market_id "us"` explicitly.
- **What the actions return** (Appendix A5): alerts have `message` (the title), `category`,
  `status`, `opened_at`, `resolved_at`; programs have `started_at`, `ended_at` (null while
  open-ended), `window`, `goal`, `status`. Flow 8's date range applies to `started_at` and
  `ended_at`, and an "open alerts" prompt keeps the alerts whose `status` is `open`.
- **Flow 4 branching:** there is no "if/else" step. Either (a) rely on the prompts
  above — the action model decides from the id shape — or (b) wrap the two action
  steps in a **Flow logic → Reasoning group** whose *Instructions* read:
  *"If @Account or alert ID starts with 'ACC-', run the Account step first and then
  the Alerts step; otherwise run the Alerts step first and get the account it names."*
- **Flow 5 voice path:** Quick has **no transcription step**. The **Files** step
  accepts a *document, image, or video* (one file per step, same limits as chat
  upload); audio files are not listed as supported. Practical path: the DM pastes
  dictated text into `Meeting notes`, or uploads a video recording to the Files step
  and the agent prompt says *"Use @Meeting notes if present, otherwise summarise
  @Recording or transcript file."*
- **Flow 5 history:** earlier contact reports are stored in `OEM_ContactReports`,
  which is not in the Quick Index and has no connector action, so the flow cannot
  cite previous visits. It uses the dealer's account plans instead; `Retail Ops Prior
  Plans` is the only knowledge base whose documents name the dealer. Do not use
  `Retail Ops Transcripts` here: it holds customer conversations from purchase journeys,
  not dealer visits, and none of its documents name a dealer.
- **Flow 6** is the "everything" flow — if a run is slow, remove the `Alerts` step
  and let the agent cite the funnel + notes only.

### 6.3 Finish

- [x] All 8 Flows **Published → Shared** (Viewer: `retail-ops-oem-dm`, `retail-ops-oem-regional`, `retail-ops-oem-ci-planning`; Co-owner: `retail-ops-builders`). Created by the repo, 2026-10-23.
- [ ] **Run Flow 1 once** on the test values (Console checklist step 4): data steps return rows, the draft has the required sections, every figure is cited, the text calls itself a draft, and a GR-001/GR-005-tripping ask (e.g. *"tell D-105 to cut wholesale"*) is declined with the rule named. The API has no run call; runs are metered in agent hours.
- [ ] **Schedule `Retail Ops District Roll-up`** (spec §4.10.2; Console checklist step 5): open it in **Run mode → scheduling icon → Create schedule** → Name `District roll-up weekly` · Repeat **Weekly, Monday, 5:00 AM**, time zone `America/New_York` · **Next** → default input `District ID` = `DIST-07` (one schedule per district a DM owns) → **Next** → no action-permission prompt appears (read-only connector) → **Save**. Schedules are private: each DM creates their own. Results arrive by e-mail with a link; history is kept 30 days under **Flows → My schedules**. The API has no schedule call.
- *(Optional)* Daily schedule on `Retail Ops Action Plan` for milestone follow-ups, same path.
- **Test values (dev):** `account_id` = `ACC-US-00001` (Decatur Meridian, `DIST-07`) · `district_id` = `DIST-07` · `market_id` = `us`.
- [x] `deploy_quick --apply` so `/quick` in the console lists the 8 Flows (§9). Done 2026-10-23.

---

## 7 — Campaign ROI dashboard ("Campaign ROI & Funnel")

> **Created by the repo.** `deploy_quick_content --apply --only dashboard` creates the
> analysis and the dashboard `retail-ops-campaign-roi-funnel`, both named
> `Retail Ops Campaign ROI & Funnel`, from one definition (`dashboard_definition()` in
> `app/infra/deploy_quick_content.py`). The dashboard is published on create, and a changed
> definition publishes a new version. Shared: owner `retail-ops-builders`, viewer
> (`DescribeDashboard`, `ListDashboardVersions`, `QueryDashboard`) the 4 `retail-ops-oem-*`
> groups; the embed user `default/admin` is the IAM user that creates it, so it owns it.
> The space step adds it to the space. *Verified 2026-10-23:* analysis
> `CREATION_SUCCESSFUL` with no errors; dashboard version published, `CREATION_SUCCESSFUL`,
> no errors; `describe_dashboard_permissions` lists the principals above.

Datasets: the §1a funnel dataset and `retail-ops-campaign-roi-sql` (`Retail Ops · campaign ROI`,
custom SQL, Direct query). The lake has no per-dealer spend, and `lake_campaigns` and
`lake_journeys` write campaign ids differently (`CMP-US-00004`, `CMP-US-04`), so the ROI
dataset joins them on market and number and allocates each campaign's `budget` to source
and dealer by its share of the campaign's journeys. That allocation is an estimate, and the
table title says so. *Checked 2026-10-23 in Athena:* the 6 US campaigns (budget 230,000 in
all) match journeys for two of them (`CMP-US-00002`, `CMP-US-00004`); the German journeys
name campaigns that `lake_campaigns` doesn't hold.

- [x] Funnel: distinct `journey_id` by `stage`, in funnel order (`stage_order`)
- [x] Journeys by stage, stacked by `source`
- [x] ROI table: campaign × platform (the journey's `source`) × dealer: allocated spend, journeys, sold (`DELIVER`/`OWN`), spend per sale
- [x] Filters: event date (`event_ts`, date range), `dealer_id`, `source`, `market` (the last three apply to both datasets)
- [x] **Published** and shared with the 4 `retail-ops-oem-*` groups (§P1; console readers reach it through their group) and the shared embed user `arn:aws:quicksight:us-east-1:<account-id>:user/default/admin` (fallback when per-user embed is off)
- [x] Added to the space
- [x] **Embed domain:** the `l4` stage writes the console origin (`url` in `app/infra/console-config.json`, override `RETAIL_OPS_QUICK_EMBED_DOMAINS` in `deploy/config.env`) to `RETAIL_OPS_QUICK_EMBED_DOMAINS` on the service Lambda, and the embed route passes it as `AllowedDomains` to `GenerateEmbedUrlForRegisteredUser` (up to three https origins). That replaces the Quick admin's domain allowlist for these URLs. *Verified 2026-10-23:* the Lambda holds `https://<distribution-domain>`, and the embed route returns a URL for the dashboard. On a fresh account the console stage runs after `l4`, so re-run `deploy.sh --apply --only l4` once after the first full deploy. *Fallback* if the embed shows a domain error: *profile icon → Manage AWS Quick → Domains and Embedding* → add `https://<distribution-domain>`.
- Dashboard ID for §9: `retail-ops-campaign-roi-funnel`.

*Console fallback:* **Analyses → New analysis → dataset `Retail Ops · journey_events (funnel)`** (+ `Retail Ops · campaign ROI`), build the visuals and filters above, **Publish dashboard**, share, add to the space.

---

## 8 — Access control (not row-scoped)

> **No row-level security.** Row-level security was removed by user decision on
> 2026-10-21 (spec §13). The datasets carry no rule set: anyone who can open a
> dataset or dashboard sees all of its rows, every district and dealer. What
> someone can open is decided by Quick group membership (§P1). A stack deployed
> before 2026-10-21 loses its old rule sets at the next
> `deploy_quick_datasets --apply`; teardown removes the leftover RLS datasets and
> `lake_rls_*` tables.

| Layer | Mechanism | Notes |
|---|---|---|
| Datasets and dashboards | Shared with the §P1 groups (`deploy_quick_content`) | Not row-scoped: every reader with access sees all rows |
| Knowledge bases | Shared with the §P1 groups; document-level ACLs **off** | Set at creation (2b); can't be changed later |
| Gateway tools | Scope derived from the OAuth token (3a): one OEM service principal with read-across, not per user | Enforced server-side; model-supplied `claims` ignored |
| Guardrails GR-001…008 | Field Sales Agent instructions + the drafting step of each Flow | Steps 5, 6 |

| Resource | Owner | Viewer |
|---|---|---|
| Space, topic, knowledge bases, connector | `retail-ops-builders` + each builder user | the 4 `retail-ops-oem-*` groups |
| Chat agent, 8 Flows | `retail-ops-builders` + each builder user (Flows: co-owner) | `retail-ops-oem-dm`, `retail-ops-oem-regional`, `retail-ops-oem-ci-planning` |
| Dashboard | `retail-ops-builders` + each builder user | the 4 `retail-ops-oem-*` groups and the embed user `default/admin` |
| Datasets, data sources, analysis | `retail-ops-builders` + each builder user | — |

`retail-ops-builders` holds `admin` (the IAM user that runs the scripts) and
`admin/jdoe`, and both also hold the owner set directly (§P1), so the human
builder sees and can edit everything the scripts create.

- **Embedded per-user reader:** with `RETAIL_OPS_QUICK_PER_USER_EMBED=1`, the embed route (`GET /oem/quick/dashboards/{id}/embed-url`) registers the signed-in console user as a Quick Sight READER named by their `user_id`, adds them to the §P1 group for their role (so the shared dashboard opens), and embeds with their ARN. The dashboard is not row-scoped. The shared `default/admin` reader is the fallback when the flag is off.

---

## 9 — Re-discover into the repo console

`/quick` and the embed route call Quick only when the service Lambda
`retail-ops-dev-journeys-service` has `ENABLE_QUICK=true`. The `l4` stage sets it to
`true` when `describe-account-subscription` reports an active AWS Quick subscription
(`false` when there is none; unchanged when the subscription can't be read). Override
it with `ENABLE_QUICK=true|false` in `deploy/config.env`. Set in dev on 2026-10-23. It
turns on read calls only: list Flows, list dashboards, and an embed URL for the shared
embed user.

`RETAIL_OPS_QUICK_PER_USER_EMBED=1` (per-user readers, §P1, §8) is off by default, and
no stage sets it. With it off, the four `retail-ops-oem-*` groups stay empty and every
embed uses the shared `default/admin` reader. Turning it on registers each console user
who opens a dashboard as a Quick reader, which can add per-user subscription cost. Set
it by hand in the Lambda console under *Configuration → Environment variables → Edit*;
`deploy_l4` keeps it on later deploys.

`deploy_quick --apply` records every Flow and dashboard in the account, including the
7 sample Flows and the three demo dashboards. The `quick` stage runs it after
`deploy_quick_content`. Run it without a flag first (dry-run) to see the list.

```bash
python3 -m app.infra.deploy_quick --apply
python3 -m app.infra.deploy_quick --status
python3 -m app.infra.deploy_lake --status
python3 -m app.infra.deploy_gateway --status
```

- [x] `/quick` lists the 8 Flows and the new dashboard; `GET /oem/quick/dashboards/{id}/embed-url` returns a URL.
  - *Verified 2026-10-23:* `deploy_quick --apply` → `flows=15 dashboards=4` (the 8 `Retail Ops …` Flows and 7 samples; the dashboard and 3 demos). Signed in as `U-US-00044` (`oem_dm`, native client `<staff-web-client-id>`): `GET /oem/quick/flows` → 200, `enabled: true`, all 8 `Retail Ops …` Flows; `GET /oem/quick/dashboards` → 200, includes `retail-ops-campaign-roi-funnel`; `GET /oem/quick/dashboards/retail-ops-campaign-roi-funnel/embed-url` → 200 with an embed URL (`embed_mode: registered_user`).

---

## Final verification

| Check | Expected | Status, 2026-10-23 |
|---|---|---|
| Groups | 5 groups; you are in `retail-ops-builders`; registered OEM readers are in their role's group (`deploy_quick_groups.py --status`) | met |
| Content | `deploy_quick_content --status` lists 25 resources and only `MISSING connector` | met (connector: checklist 1) |
| Space | `retail-ops-space` exists, shared by group, 19 resources (20 with the connector) | met |
| Datasets | 13 `retail-ops-lake-*` + 2 custom-SQL datasets, Direct query; `journey_events` 8,339 rows; topic answers "events by event_type, last 90 days" | datasets met; topic answer: checklist 3 |
| Analytics tables | `lake_rdr`/`lake_csi`/`lake_marketintel`/`lake_campaigns`/`lake_service` queryable in Athena (`deploy_lake_rollups.py --status`) | met |
| Knowledge bases | 9 knowledge bases, 538 docs, sync `COMPLETED` (`deploy_quick_content --status`); answers show `source_ref` | counts met; answers: checklist 3 |
| Connector | 4 actions; calls run as the OEM service principal (OEM read-across, not per user, §3a) | checklist 1–2 |
| Agent | Published, shared; cited answers; GR-001/GR-005 prompt declined with a safe alternative | created; answers: checklist 3 |
| Flows | 8 Flows published and shared; outputs cited and returned as drafts | created; output: checklist 4 |
| Dashboard | Published; `/quick` lists it and the embed route returns a URL; it renders in the console | URL met; rendering: open `/quick` in the console |
| Teardown | `deploy/destroy.sh --only quick_content,quick` (dry run) lists the content, dependents first | met (45 resources found, nothing deleted) |

## Repo tooling for the Quick build (all idempotent; `--apply` / `--status` / dry-run)

| Tool | Provisions |
|---|---|
| `app/infra/deploy_lake.py` | `journey_events` Firehose + Athena table (live event lake) |
| `app/infra/deploy_lake_analytics.py` | generator-mirror lake prefixes (journeys, service, recommendations, outcomes, touchpoints, agent_traces, …) → S3 + Athena |
| `app/infra/deploy_lake_rollups.py` | derived roll-ups `rdr`/`csi`/`marketintel`/`leads` + `campaigns` mirror → S3 + Athena (deterministic, storyline-shaped) |
| `app/infra/deploy_quick_index.py` | 538-doc Quick Index corpus → `s3://…/quick-index/` (knowledge bases) |
| `app/infra/deploy_quick_datasets.py` | `retail-ops-lake` Athena data source + one Quick dataset per lake table |
| `app/infra/deploy_quick_groups.py` | the 5 Quick groups and their memberships (§P1) |
| `app/infra/deploy_quick_content.py` | custom-SQL datasets, topic, S3 knowledge source + 9 knowledge bases, space, chat agent, 8 Flows, analysis + dashboard, and every share; links the console-built connector; `--delete` (dry run unless `--apply`) removes it all, dependents first |
| `app/infra/deploy_quick.py` | discover Flows/dashboards into `quick-config.json` |
| `app/infra/deploy_gateway_quick.py` | CUSTOM_JWT AgentCore Gateway for the Quick MCP connector (Cognito-token auth) |
| `app/infra/deploy_l4.py` | `ENABLE_QUICK` and `RETAIL_OPS_QUICK_EMBED_DOMAINS` on the service Lambda |
| `deploy/destroy.sh` (`app/infra/teardown.py`) | stage `quick_content` removes the content above and the connector named `Retail Ops OEM Read Gateway`, dependents first; stage `quick` the lake datasets, data source, groups and service-role policy. Dry run by default |

*Optional extension, not built:* Gateway write tools to save plan drafts to
`OEM_AccountPlans`, log to `OEM_GuardrailEvents`, and create `DEALER_Tasks`. Until
then the connector is read-only, which §6 relies on.

## References
- Spaces — https://docs.aws.amazon.com/quicksuite/latest/userguide/working-with-spaces.html
- Knowledge bases — https://docs.aws.amazon.com/quick/latest/userguide/knowledge-base-integrations.html
- S3 knowledge base setup — https://docs.aws.amazon.com/quick/latest/userguide/s3-setup-manage.html
- MCP integration — https://docs.aws.amazon.com/quick/latest/userguide/mcp-integration.html
- Custom chat agents — https://docs.aws.amazon.com/quicksuite/latest/userguide/custom-agents.html
- Quick Flows (overview) — https://docs.aws.amazon.com/quick/latest/userguide/using-amazon-quick-flows.html
- Quick Flows: creating — https://docs.aws.amazon.com/quick/latest/userguide/creating-flows.html
- Quick Flows: editing & step configuration — https://docs.aws.amazon.com/quick/latest/userguide/editing-flows.html
- Quick Flows: terminology, step groups, `@` references — https://docs.aws.amazon.com/quick/latest/userguide/terminology-and-key-concepts.html
- Quick Flows: step types — https://docs.aws.amazon.com/quick/latest/userguide/user-input-steps.html · action-steps-in-flows.html · data-insight-steps.html · ai-response-steps.html · flow-logic-steps.html
- Quick Flows: sharing — https://docs.aws.amazon.com/quick/latest/userguide/sharing-flows.html
- Quick Flows: schedules — https://docs.aws.amazon.com/quick/latest/userguide/schedules-in-quick-flows.html
- Athena datasets — https://docs.aws.amazon.com/quick/latest/userguide/create-a-data-set-athena.html
- AgentCore Gateway inbound auth — https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-inbound-auth.html

---

# Appendix A — Deployed handles (dev, live)

> Read from the live account `<account-id>` (`retail-ops-dev-*` stack) and checked against `app/infra/*.json` on 2026-10-23.
> Re-checked on 2026-10-29 after the full redeploy from `main` `60af486`: every handle below is unchanged; the runtimes are at version 6 (§A2).

The concrete, resolvable handles a Quick builder connects to in the development
account. Environment-specific; re-run the §A8 commands to confirm them or to read
them for another account.

## A0. Environment coordinates

| Field | Value |
|---|---|
| AWS account | `<account-id>` |
| Region | `us-east-1` |
| Environment | `dev` |
| Table name prefix | `retail-ops-dev-` (env `RETAIL_OPS_ENV_PREFIX`; 68 tables live) |
| Service REST API base | `https://<api-id>.execute-api.us-east-1.amazonaws.com/dev` (stable across redeploys; `deploy_l4` reuses the REST API id) |
| Operator console | `https://<distribution-domain>` (CloudFront `<distribution-id>`, `app/infra/console-config.json`); the domain to allow for embedding |
| EventBridge bus | `app-bus-dev` |

> **Table-name rule:** code/specs use **bare** names (`OEM_Accounts`); the live
> tables carry the `retail-ops-dev-` prefix. A Quick connector / dataset must use the
> **full** name `retail-ops-dev-OEM_Accounts`.

## A1. DynamoDB — tables the Quick app reads

On-demand, `us-east-1`, acct `<account-id>`. ARN:
`arn:aws:dynamodb:us-east-1:<account-id>:table/<full-name>`.

| Bare name | Full name | PK | SK | Role |
|---|---|---|---|---|
| `OEM_Accounts` | `retail-ops-dev-OEM_Accounts` | `account_id` | — | Account model; A6 get/list accounts |
| `OEM_Alerts` | `retail-ops-dev-OEM_Alerts` | `alert_id` | — | AI Alerts |
| `OEM_AccountPlans` | `retail-ops-dev-OEM_AccountPlans` | `plan_id` | — | Flow outputs (plans/briefs) |
| `OEM_ContactReports` | `retail-ops-dev-OEM_ContactReports` | `report_id` | — | Contact Report Flow output |
| `OEM_GuardrailEvents` | `retail-ops-dev-OEM_GuardrailEvents` | `event_id` | — | GR-001…008 block log |
| `OEM_Comms` | `retail-ops-dev-OEM_Comms` | `comm_id` | — | Sales Ops bulletins |
| `OEM_CIPrograms` | `retail-ops-dev-OEM_CIPrograms` | `program_id` | — | C&I programs |
| `OEM_Incentives` | `retail-ops-dev-OEM_Incentives` | `incentive_id` | — | Incentive detail |
| `OEM_Compliance` | `retail-ops-dev-OEM_Compliance` | `compliance_id` | — | Compliance |
| `OEM_Markets` | `retail-ops-dev-OEM_Markets` | `market_id` | — | Market/brand config |
| `OEM_Campaigns` | `retail-ops-dev-OEM_Campaigns` | `campaign_id` | — | A5 campaign catalog |
| `DEALER_JourneyEvents` | `retail-ops-dev-DEALER_JourneyEvents` | `journey_id` | `sk` | Sales journey events (lake source) |
| `DEALER_ServiceJourneyEvents` | `retail-ops-dev-DEALER_ServiceJourneyEvents` | `service_journey_id` | `sk` | Service journey events |
| `PLAT_KnowledgeCollections` | `retail-ops-dev-PLAT_KnowledgeCollections` | `doc_id` | — | **Quick Index corpus** — 538 docs w/ `source_ref` |

Quick never reads these tables directly: it reads them through the Gateway
actions (§A6) and the S3 lake datasets (§A3). **No Quick-side write path** — all
writes go through the solution's agents.

## A2. AgentCore runtimes (same-persona compute)

One runtime per custom agent (ADR 0009), all `READY` at version 6 (2026-10-29). The Field
Sales Agent (A6) has none: it is the Quick-native agent this guide builds.

| Agent | Runtime id |
|---|---|
| Discovery Agent (A1) | `retail_ops_dev_a1_discovery-TAGChmDiXz` |
| Journey Agent (A2) | `retail_ops_dev_a2_journey-Ez9rO1E1x2` |
| Sales Agent (A3) | `retail_ops_dev_a3_sales-j2r7Y94vev` |
| Trade-In Agent (A4) | `retail_ops_dev_a4_trade_in-Tn4bo8FUiH` |
| Campaign Agent (A5) | `retail_ops_dev_a5_campaign-34qc3p888n` |
| Service Agent (A7) | `retail_ops_dev_a7_service-7LgWZ0Hssf` |

ARN: `arn:aws:bedrock-agentcore:us-east-1:<account-id>:runtime/<runtime id>`.
Exec role (shared): `arn:aws:iam::<account-id>:role/AmazonBedrockAgentCore-retail-ops-exec`.
Code: `s3://bedrock-agentcore-retail-ops-<account-id>/agent/<runtime name>.zip`.

Invoke: `bedrock-agentcore InvokeAgentRuntime` with a typed request
`{kind: ping | tool | job | agent, …, claims}`; what each runtime serves is in
`app/agentcore/registry.py`. Same-persona only: Quick does not call these
runtimes; cross-persona OEM reads go through the Gateway (§A5/A6).

## A3. S3 event lake (A5 / QuickSight source)

| Field | Value |
|---|---|
| Lake bucket | `s3://retail-ops-dev-event-lake-<account-id>/journey-events/` (Hive `dt=YYYY-MM-DD/`) |
| Firehose | `retail-ops-dev-event-lake` (DirectPut → S3) |
| EventBridge rule | `retail-ops-dev-event-lake-rule` on `app-bus-dev` (source `retail-ops.journeys`) |
| Glue db.table | `retail_ops_dev_lake.journey_events` (external, JSON SerDe, partition `dt`) |
| Athena workgroup | `retail-ops-dev-lake` (results `…/athena-results/`) |

Columns: `journey_id, event_type, event_ts, correlation_id, evidence, actor(json),
payload(json)` + `dt`. 8,339 rows on 2026-10-23 (`deploy_lake --status`); live events
≤60 s. Quick/QuickSight dataset → workgroup `retail-ops-dev-lake`, db `retail_ops_dev_lake`,
table `journey_events`; parse `payload`/`actor` with `json_extract`. (The repo provisions 13
lake datasets and the two custom-SQL datasets — §1, §A9.)

## A4. Identity & embedding

| Field | Value |
|---|---|
| Cognito staff pool | `<user-pool-id>` (`retail-ops-dev-staff`), domain `retail-ops-dev-<domain-suffix>` |
| Shared embed user | `arn:aws:quicksight:us-east-1:<account-id>:user/default/admin` (`RETAIL_OPS_QUICK_EMBED_USER_ARN`) |
| Per-user embed | `RETAIL_OPS_QUICK_PER_USER_EMBED=1` — the embed route registers the signed-in user as a READER named by `user_id`, adds them to their role's group (§P1); the dashboard is not row-scoped (§8). Off by default, set by hand; not set in dev on 2026-10-23, so the OEM groups stay empty and embeds use the shared embed user |
| Service Quick switch | `ENABLE_QUICK=true` on `retail-ops-dev-journeys-service`, set by the `l4` stage when the account has AWS Quick; override with `ENABLE_QUICK=true\|false` in `deploy/config.env` (§9). Set in dev on 2026-10-23 |
| Embed allowed domains | `RETAIL_OPS_QUICK_EMBED_DOMAINS=https://<distribution-domain>` on `retail-ops-dev-journeys-service`, from `console-config.json` by the `l4` stage; passed as `AllowedDomains` on every embed URL (§7). Set in dev on 2026-10-23 |
| Cognito app clients | `<staff-web-client-id>` (`retail-ops-dev-staff-web`), `<staff-hostedui-client-id>` (`retail-ops-dev-staff-hostedui`), `<quick-mcp-client-id>` (`retail-ops-quick-mcp`, §A6) |

## A5. AgentCore Gateway — `retail-ops-dev-crosspersona` (AWS_IAM, console/runtime path)

The console's cross-persona boundary (dealer→OEM). `MCP` / `AWS_IAM`.
MCP endpoint `https://retail-ops-dev-crosspersona-rtst6zkxc3.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp`;
target `oem-read` → `retail-ops-dev-gateway-oem`; roles `retail-ops-dev-gateway-role` /
`retail-ops-dev-gateway-lambda-role`. Tools: `oem_get_account`, `oem_list_accounts`,
`oem_get_alerts`, `oem_get_incentive_programs`. Config `app/infra/gateway-config.json`.

What the tools return (the same for A5 and A6; one Lambda serves both). Every record carries
`source_ref` (the Gateway path) and `data_source_ref` (the row's own citation):

| Tool | Record fields |
|---|---|
| `oem_get_account`, `oem_list_accounts` | the `OEM_Accounts` row minus the OEM-internal fields: `account_id`, `account_type`, `name`, `dealer_id`, `group_id`, `district_id`, `region_id`, `market_id`, `tier`, `condition`, `days_supply`, `sales_health_index`, `service_health_index` |
| `oem_get_alerts` | `alert_id`, `severity`, `message` (the alert title), `title`, `category` (the alert kind, e.g. `lead_sla_breach`), `status` (`open`/`resolved`), `dealer_id`, `district_id`, `opened_at`, `resolved_at`. No `account_id`: match alerts to an account on `dealer_id` |
| `oem_get_incentive_programs` | `program_id`, `name`, `window` (e.g. `from 2026-08-08 (no end date)`), `started_at`, `ended_at` (null while open-ended), `goal`, `summary` (the goal when the row has no summary), `status`, `district_id`, `dealer_id` (the target dealer of a dealer-specific program; null for a district initiative, and for another dealer's program when a dealer-scoped caller asks through A5) |

*Fixed 2026-10-24:* `message`, `window` and `summary` used to come back empty, because the tools
read `message`/`summary` and `window`/`period` while the rows carry `title`, `started_at` and
`goal`. Verified through the A6 gateway: all 68 alerts per market now have `message`, all 7
programs per market have `window`, `started_at`, `goal` and `status`. A code-only redeploy of
the shared Lambda is `python3 -m app.infra.deploy_gateway --code-only --apply` (dry run without
`--apply`); it leaves both gateways, their targets and their auth as they are.

## A6. AgentCore Gateway — `retail-ops-dev-crosspersona-quick` (CUSTOM_JWT, **the one Quick uses**)

| Field | Value |
|---|---|
| Gateway id | `retail-ops-dev-crosspersona-quick-<gateway-suffix>` |
| MCP endpoint | `https://retail-ops-dev-crosspersona-quick-<gateway-suffix>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp` |
| Auth | `CUSTOM_JWT` — discovery `https://cognito-idp.us-east-1.amazonaws.com/<user-pool-id>/.well-known/openid-configuration` |
| OAuth client | `<quick-mcp-client-id>` (client-credentials), scope `retail-ops-oem/read` |
| Token URL | `https://retail-ops-dev-<domain-suffix>.auth.us-east-1.amazoncognito.com/oauth2/token` |
| Secret | Secrets Manager `retail-ops-dev-quick-mcp-oauth` |
| Target / tools | `oem-read` → `retail-ops-dev-gateway-oem`; same 4 tools and record fields as A5 |
| Config | `app/infra/gateway-quick-config.json` |

Smoke test the connector's auth path:
```bash
SECRET=$(aws secretsmanager get-secret-value --secret-id retail-ops-dev-quick-mcp-oauth \
  --query SecretString --output text | python3 -c "import sys,json;print(json.load(sys.stdin)['client_secret'])")
TOKEN=$(curl -s -X POST https://retail-ops-dev-<domain-suffix>.auth.us-east-1.amazoncognito.com/oauth2/token \
  -H "Content-Type: application/x-www-form-urlencoded" -u "<quick-mcp-client-id>:$SECRET" \
  -d "grant_type=client_credentials&scope=retail-ops-oem/read" | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -s -X POST https://retail-ops-dev-crosspersona-quick-<gateway-suffix>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"1","method":"tools/list"}'   # → 4 oem-read___oem_* tools + x_amz_bedrock_agentcore_search
```

## A7. IAM roles (Quick-relevant)

| Role | Grants |
|---|---|
| `aws-quicksight-service-role-v0` | AWS-managed Quick policies + inline `retail-ops-lake-and-data-read` (S3 lake read, Athena wg `retail-ops-dev-lake`, Glue `retail_ops_dev_lake`, DynamoDB `retail-ops-dev-*` read) |
| `retail-ops-dev-service-role` | DynamoDB read and write, `quicksight` list/describe + `GenerateEmbedUrlForRegisteredUser` + `RegisterUser`/`DescribeUser`/`CreateGroupMembership` (group membership at embed time, §P1), invoke the Gateway target |
| `retail-ops-dev-gateway-role` / `retail-ops-dev-gateway-lambda-role` | Gateway invoke / OEM-table read (A5/A6) |

## A8. Re-generate / verify against live

```bash
aws bedrock-agentcore-control list-agent-runtimes --region us-east-1 \
  --query 'agentRuntimes[].{name:agentRuntimeName,arn:agentRuntimeArn,status:status}'
aws apigateway get-rest-apis --region us-east-1 \
  --query "items[?name=='retail-ops-dev-journeys-api'].id | [0]"      # current API id
python3 -m app.infra.deploy_lake --status                       # lake / Athena rows
python3 -m app.infra.deploy_gateway --status                    # AWS_IAM gateway (A5)
python3 -m app.infra.deploy_gateway_quick --status              # CUSTOM_JWT gateway (A6)
python3 -m app.infra.deploy_quick_datasets --status             # 13 datasets
python3 -m app.infra.deploy_quick_groups --status               # groups + members
python3 -m app.infra.deploy_quick_content --status              # repo Quick content (A9), KB docs + sync
python3 -m app.infra.deploy_quick                               # live Flows / dashboards / connectors (dry-run)
python3 -m app.infra.deploy_quick --status                      # what quick-config.json recorded last
```

## A9. Quick content created by the repo (`deploy_quick_content`)

Live on 2026-10-23. Server-assigned ids (the Flows and the connector) are recorded in the
git-ignored `app/infra/quick-content-config.json`.

| Resource | Name | Id |
|---|---|---|
| Space | `Automotive Retail Operations` | `retail-ops-space` |
| Chat agent | `Retail Ops Field Sales Agent` | `retail-ops-field-sales-agent` |
| Topic (V2) | `Retail Ops Journey Funnel` | `retail-ops-journey-funnel` |
| Dataset (custom SQL) | `Retail Ops · journey_events (funnel)` | `retail-ops-journey-events-sql` |
| Dataset (custom SQL) | `Retail Ops · campaign ROI` | `retail-ops-campaign-roi-sql` |
| S3 knowledge data source | `Retail Ops Knowledge S3` | `retail-ops-knowledge-s3` |
| Knowledge bases | `Retail Ops CRM Notes` · `Templates` · `Transcripts` · `Objection Strategies` · `Bulletins` · `Prior Plans` · `Product Guides` · `Repair Procedures` · `TSBs` | `retail-ops-kb-crm-notes` · `-templates` · `-transcripts` · `-objection-strategies` · `-bulletins` · `-prior-plans` · `-product-guides` · `-repair-procedures` · `-tsbs` |
| Analysis + dashboard | `Retail Ops Campaign ROI & Funnel` | `retail-ops-campaign-roi-funnel` |
| Flow | `Retail Ops Account Plan` | `<flow-id>` |
| Flow | `Retail Ops Opportunity Brief` | `<flow-id>` |
| Flow | `Retail Ops Win Strategy` | `<flow-id>` |
| Flow | `Retail Ops Action Plan` | `<flow-id>` |
| Flow | `Retail Ops Contact Report` | `<flow-id>` |
| Flow | `Retail Ops Meeting Package` | `<flow-id>` |
| Flow | `Retail Ops District Roll-up` | `<flow-id>` |
| Flow | `Retail Ops Sales Ops Digest` | `<flow-id>` |
| MCP connector (console, checklist 1, built 2026-10-23) | `Retail Ops OEM Read Gateway` | `<connector-id>` |

---

# Appendix B — Console chat prompts

The repo creates the knowledge bases, agent, Flows and dashboard (§A9), so the
authoring prompts below are a console fallback only. The checks (dataset, retrieval) are
useful in any case. AWS Quick chat is an assistant *inside each builder* — not a spec
compiler. Author **one artifact per ask**, grounded in a real handle. Prompts for each step:

**Dataset sanity check** (after §1):
> Using the `Retail Ops · journey_events (funnel)` dataset in this space, show the count of events by `event_type` for the last 90 days.

**Quick Index collection** (§2, repeat per `kind`):
> Create a knowledge base from the S3 prefix `quick-index/crm_notes/`. Index each markdown doc's body and keep its `source_ref` line as a returnable citation field.

**Index retrieval check** (§2):
> Using the Retail Ops CRM Notes knowledge base, what are the top objection-handling notes for a customer worried about EV charging range? Cite the source_ref for each.

**A6 agent system instructions** (§5): paste the persona block in §5 verbatim.

**A6 agent — attach data** (§5):
> Give this agent the Retail Ops CRM Notes / Bulletins / Product Guides / Prior Plans knowledge bases, the `Retail Ops Journey Funnel` topic, and the `Retail Ops OEM Read Gateway` connector (all 4 actions). For account/alert/incentive facts call the connector; for funnel questions use the topic; cite every figure.

**Flow (§6 one-shot path — *Flows → Create Flow → natural-language field → Generate Flow*; Account Plan shown, adapt per row of §6.2):**
> Create a flow "Retail Ops Account Plan". Ask the user for an account_id (default ACC-US-00001). Using the Retail Ops OEM Read Gateway connector, call oem_get_account for that account and oem_get_alerts for market "us". Search the Retail Ops CRM Notes and Retail Ops Prior Plans knowledge bases for that account. Then use the "Retail Ops Field Sales Agent" chat agent to draft a quarterly account plan with sections: performance vs objective, gaps, opportunities, recommended actions, risks; cite every figure; run the GR-001…008 guardrail check; return a draft for review and do not publish.

> After **Generate Flow**, check the generated steps against §6.1: Titles, the connector/action on each **Application actions** step, and that the **Quick data** step is linked to the two knowledge bases only.

**A5 dashboard (§7):**
> Build a dashboard "Retail Ops Campaign ROI & Funnel" on the `Retail Ops · journey_events (funnel)` + `Retail Ops · campaign ROI` datasets: a ROI table by campaign/platform/dealer (allocated spend, journeys, sold), a funnel chart of distinct journeys by stage, journeys by stage and source, and filters for date, dealer, source and market. Enable registered-user embedding.
