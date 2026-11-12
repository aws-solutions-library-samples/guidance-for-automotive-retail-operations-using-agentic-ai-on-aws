# Acceptance criteria — current status

This document is the single status map for the acceptance criteria in
`docs/specs/intelligent-sales-service-orchestration.md` §11. It describes the
current implementation and verification evidence; it does not retain superseded
baseline assessments.

## Status definitions

- **PASS** — implemented and covered by an automated check or demonstrated live path.
- **PARTIAL** — the core path exists, but a named measurement, integration, or exhaustive check remains.
- **PENDING ENVIRONMENT** — requires customer/account configuration or console-authored content that the repository cannot supply.
- **REMOVED** — taken out of scope by a recorded decision (spec §13); the row is kept so the change is visible.

## Verification commands

```bash
python3 -m pytest tests -q                       # complete offline suite
python3 -m app.eval.run_eval --mode offline      # deterministic golden-prompt CI gate
python3 -m app.demo.run_demo --smoke --api <API> # role/scope and read-path checks
python3 -m app.demo.run_demo --acceptance --api <API>
python3 -m app.demo.run_demo --api <API>         # write loop with throwaway DEMO-* rows
```

The current branch passes the offline suite, the offline evaluation gate, the
console literal/type checks, and deployment-script syntax validation. Live
checks are run with `deploy/verify.sh` after deployment.

## Process 1 — retail funnel

| # | Acceptance area | Current evidence | Status |
|---|---|---|---|
| 1 | Purchase journey and audited transitions | Demo advances DISCOVER through delivery; event rows retain actor and evidence | PASS |
| 2 | Missing-context handoff gate | Supervisor returns the missing fields and blocks the transition | PASS |
| 3 | Advisor latency objectives | Functional endpoints and streaming-shaped SSE response exist; load-test percentiles are not recorded | PARTIAL |
| 4 | Traceable calculations and figures | Responses retain `source_ref`; compute retains inputs, expression/code, and result | PASS |
| 5 | Sales Package combines vehicle, finance, and trade | A1/A3/A4 outputs are combined through the package route | PASS |
| 6 | Campaign ROI and budget-shift recommendation | Acceptance runner asserts per-campaign ROI and expected-lift output | PASS |
| 7 | Reproducible deployment | Dry-run-first `deploy/` orchestration, direct arm64 AgentCore bundle, verification and guarded teardown | PASS |
| 8 | Least-privilege DynamoDB resources | Acceptance runner checks deployed function-role DynamoDB resources for wildcard access | PASS |
| 9 | Market formatting and literal gate | US/DE configuration plus `npm run lint:units` | PASS |
| 10 | US/DE behavior without code changes | Both deterministic packs use the same services and market configuration | PASS |
| 10a | Open-data provenance | Generated anchors and quantitative values retain resolvable `source_ref` values | PASS |
| 10b | Native and federated staff sign-in | Cognito password/JWT path is live; an external OIDC provider (`CorpDirectory`) is registered on the development pool and a Hosted UI sign-in reaches `/me/home` as the mapped staff user. The authorizer accepts tokens from both the native and the Hosted UI app clients. Other accounts register their own provider with `cognito_setup --federate` | PASS |
| 11 | Governed outreach channels | Consent and cadence gates, SES/SNS path, task fallback; Amazon Connect remains optional | PARTIAL |
| 12 | Lifestyle intake and grounded shortlist | Intake, recommendation, price build, and VIN availability paths are tested | PASS |
| 13 | Nurture controls and sensitive-question handoff | Acceptance runner covers frequency/consent facts, payment grounding, and credit escalation | PASS |
| 14 | Advisor suggestions and shift work | Sourced suggestions and grounded answers pass; shift-list behavior is delivered through Tasks/Plays rather than the original dedicated route | PARTIAL |
| 15 | Sourced OEM account plans and human control | Draft plans cite metrics/alerts/programs and never publish or allocate automatically | PASS |
| 15a | Complete Quick-hosted OEM workspace | API/read-model capabilities exist; since 2026-10-23 the repo creates the space, topic, knowledge bases, Field Sales chat agent, 8 Flows and Campaign ROI dashboard (`deploy_quick_content`), and the MCP connector, built in the console that day, is linked; a Flow test run and the District Roll-up schedule have no API and remain ("Amazon Quick console content" below) | PARTIAL |
| 15b | OEM guardrails | Managed Bedrock Guardrails (the main one on every model call; the nightly store digest's copy with the same topics and a relaxed grounding check) plus GR-001…008 rules, calibration set, safe alternatives, and event logging; the specification's larger allow-set sweep remains | PARTIAL |
| 16 | Role visibility and Quick group access | Negative role/scope checks are automated; Quick content is not row-scoped (row-level security removed by user decision on 2026-10-21, spec §13), so Quick access is by group membership; since 2026-10-23 the repo shares every Quick resource it creates with the groups (verified by the describe-permissions calls below), while per-user reader registration stays off | PARTIAL |
| 17 | Every persona has a valid home | All declared personas have a valid home and screen catalog; exhaustive deployed navigation of every role/screen pair remains | PARTIAL |
| 18 | Cross-surface correlation | Correlation IDs flow through API and trace paths; the MCP connector for the final Quick-console hop exists since 2026-10-23, and a traced Flow run remains | PARTIAL |
| 19 | Canonical resource URLs | `/nav/resolve`, signed context, and `/r/<type>/<id>` links are implemented; a repository-wide hard-coded-path lint remains | PARTIAL |
| 20 | Four-hop dealer handoff chain | Handoff/task primitives and service-to-sales path work; the complete scripted BDC→advisor→F&I→delivery chain remains | PARTIAL |
| 21 | Notification timing and channels | Shell inbox/read state and SES/SNS path exist; end-to-end timing and optional Connect delivery remain | PARTIAL |
| 22 | Customer garage and consent | Self-scoped garage, journey, service, preferences, and staff mirror paths are tested | PASS |
| 23 | Finance, delivery, and ownership | F&I, delivery board, state transitions, and delivery event path are implemented | PASS |
| 24 | Versioned configuration without redeploy | Guardrails, autonomy, nurture, notifications, connectors, flags, and suggestions publish/rollback from data | PASS |
| 25 | Data classification in traces and exports | Query hashing, role masking, and data-request field removal are tested; complete lake/export classification audit remains | PARTIAL |

## Process 2 — service and ownership

| # | Acceptance area | Current evidence | Status |
|---|---|---|---|
| 26 | Service journey and approval gate | BOOK through FOLLOW_UP state machine, events, RO mirror, and estimate-decision gate are exercised | PASS |
| 27 | Estimate integrity and partial approval | Sourced labor/parts/coverage, audited computation, magic-link decision, and declined-work paths exist; delivery-time objective is not load-tested | PARTIAL |
| 28 | Inspection grounding and safe explanation | Evidence checks, sourced findings, guarded explanations, and deny rules are tested; the full specification sweep remains | PARTIAL |
| 29 | Service-to-sales handoff | Creates a new DISCOVER journey, handoff, and task from the service lane | PASS |
| 30 | Market parity and service-role masking | US/DE service data plus server-side technician/parts limits are tested | PASS |

## Agentic operating model

| # | Acceptance area | Current evidence | Status |
|---|---|---|---|
| 31 | Event/clock-driven action | Core subscribers and 12 scheduled jobs (11 enabled, each run for `us` and `de`) invoke guarded domain actions; every job the agent panel shows as scheduled ships enabled, and each applied run is traced so the panel's "last ran" is real. The full §4.10.2 event-subscription matrix, including intraday A7 parts/line recovery, is not wired | PARTIAL |
| 32 | A7 morning work | The shop-load job runs at 05:00 local per market (10:00 UTC `us`, 03:00 UTC `de`) in the Service Agent runtime, scans BOOK visits and writes dispatch, predicted-parts, and promise-slip recommendations. Per-dealer time-zone scheduling, brief/estimate preparation inside the sweep, constraint-aware order execution, and event-driven reforecast remain target behavior | PARTIAL |
| 33 | Per-action autonomy ladder | Configuration, hard ceilings, promotion/demotion loop, dry run, history, and rollback are tested; exhaustive deployed action-level sweep remains | PARTIAL |
| 34 | Recommendation outcome loop | Shown, accepted/dismissed, converted, and aggregate statistics are implemented | PASS |
| 35 | Equity monitoring | Scheduled monitor emits positive-equity/lease-window signals and recommendation cards | PASS |
| 36 | OEM cycle and attribution | Weekly district cycle and nightly attribution produce drafts/recommendations without changing spend or publishing plans | PASS |
| 37 | Journey continuity and compliance gate | Previous/superseding journeys, VIN ownership, and AGREE compliance checks are implemented | PASS |

## Implemented managed-service paths

These are current capabilities, not open gaps:

- **AgentCore Runtime:** one runtime per custom agent (A1, A2, A3, A4, A5's
  recommendation step, A7; ADR 0009), with the A3 Strands reasoning agent in the
  Sales Agent runtime and an in-process fallback when no runtime is configured.
- **AgentCore Memory:** sourced `PLAT_AgentMemory` system of record with AWS
  `create_event`/recall enrichment and graceful DynamoDB fallback. Not deployed
  in the development account: `deploy_memory.py` is not a `deploy.sh` stage, so
  neither the table nor the Memory resource exists and customer memory is off.
  The Lambda roles also lack the Memory data-plane actions, so after
  `deploy_memory.py --apply` only the DynamoDB half works until `iam_roles.py`
  grants them (`docs/DEPLOYMENT.md` §8.3).
- **AgentCore Code Interpreter:** real AWS execution in `INTEGRATION_MODE=aws`,
  with an audited restricted local fallback.
- **Bedrock Guardrails and Knowledge Base:** managed safety (two guardrails, ADR
  0003) and semantic retrieval with deterministic/keyword fallbacks.
- **Nightly store digest:** Bedrock writes the Store Cockpit and Service Cockpit
  [AI INSIGHT] text from each store's figures; the cockpits fall back to the
  figures-only text.
- **Connector interface:** shared sandbox/live contract for DMS, labor, and
  parts; live endpoint configuration remains customer-specific.
- **Streaming-shaped floor path:** authenticated SSE through the main API. The
  current Python/API Gateway transport buffers the document, so progressive
  token delivery is not claimed.

## Amazon Quick console content

Criteria 15a, 16 and 18 depend on Amazon Quick content. The build steps are in
`docs/QUICK_BUILD.md`; their status is kept here and nowhere else. Track A items are
created by the repository (`deploy.sh --apply --only quick`, which runs
`deploy_quick_content.py`); Track B items have no public API and are the runbook's
"Console checklist". Everything below was read from the live `retail-ops` stack in the
development account on 2026-10-23.

| # | Build step | Track | Status | Evidence |
|---|---|---|---|---|
| P | Amazon Quick Enterprise edition; Athena/S3/Glue/DynamoDB access for Quick; Cognito Hosted UI domain | A | PASS | 2026-10-23: `describe-account-settings` → `Edition: ENTERPRISE`; `get-role-policy` → inline policy `retail-ops-lake-and-data-read` on `aws-quicksight-service-role-v0` with all five statements (lake read, `athena-results/*` write, workgroup `retail-ops-dev-lake`, Glue `retail_ops_dev_lake`, DynamoDB `retail-ops-dev-*` read); domain `retail-ops-dev-<domain-suffix>` `ACTIVE` |
| P1 | 5 Quick sharing groups (`retail-ops-builders` + 4 `retail-ops-oem-*`) and memberships | A | PASS | 2026-10-23: the 5 groups exist; `deploy_quick_groups --apply` with `RETAIL_OPS_QUICK_BUILDERS="admin/jdoe,admin"` added both builders, and `--status` shows them in `retail-ops-builders`. The console lists spaces, chat agents and Flows by direct user permission, so `deploy_quick_content` and `deploy_quick_datasets` also grant each builder user the owner set directly: `describe_*_permissions` on a sample of every type shows both users with it, and `search_spaces` / `search_agents` (`DIRECT_QUICKSIGHT_OWNER`) return the space and the agent for both. The 4 `retail-ops-oem-*` groups have no members yet: none of the 13 planned console users is registered in Quick. The service adds each one at first embed (`QuickClient.register_reader`) only with `RETAIL_OPS_QUICK_PER_USER_EMBED=1`, which is off by default and not set in dev |
| P | Builders can author chat agents, knowledge bases and Flows | B | PASS | 2026-10-23: the owner confirmed admin rights in Amazon Quick; both builders have role `ADMIN`, and the IAM user `admin` created the agent, 9 knowledge bases and 8 Flows below through the API. No role, tier or subscription was changed |
| 0 | Space `Automotive Retail Operations` and sharing | A | PASS | 2026-10-23: `describe_space retail-ops-space` → the name and description; `describe_space_permissions` → `retail-ops-builders` (14 actions) and the 4 `retail-ops-oem-*` groups (3 actions) |
| 1 | Athena data source `retail-ops-lake` and datasets incl. `journey_events`; lake tables and roll-ups | A | PASS | data source `retail-ops-lake` `CREATION_SUCCESSFUL`; 13 datasets `retail-ops-lake-<table>`, none with row-level security; `retail-ops-builders` and both builder users (directly, 2026-10-23) own the source (6 actions) and each dataset (10 actions). 2026-10-23: custom-SQL datasets `retail-ops-journey-events-sql` and `retail-ops-campaign-roi-sql` created, `DIRECT_QUERY`, 13 and 12 columns, owned by `retail-ops-builders`; the funnel SQL returns 8,339 rows over 550 journeys in Athena |
| 1b | Topic `Retail Ops Journey Funnel` | A | PASS | 2026-10-23: `describe_topic_v2 retail-ops-journey-funnel` → the name, dataset `retail-ops-journey-events-sql`, custom instructions (932 characters: field meanings, synonyms, date field); published; `describe_topic_permissions_v2` → `retail-ops-builders` (6 actions) and the 4 OEM groups (`DescribeTopic`, `PassTopic`) |
| 2a | 538-document corpus exported to `s3://…/quick-index/` | A | PASS | 538 Markdown objects under `quick-index/`; the Bedrock Knowledge Base `<knowledge-base-id>` ingested all 538 |
| 2b | S3 knowledge source and 9 knowledge bases over the corpus prefixes, document-level ACLs off | A | PASS | 2026-10-23: `retail-ops-knowledge-s3` `CREATION_SUCCESSFUL`; 9 knowledge bases `retail-ops-kb-<kind>` `ACTIVE`, first sync `COMPLETED`, document counts 240/84/80/34/24/24/14/24/14 = the corpus (538; the `*.metadata.json` sidecars are excluded); shared with `retail-ops-builders` (14 actions) and the 4 OEM groups. A later sync after a corpus change is by hand (no sync API) |
| 3a | CUSTOM_JWT AgentCore Gateway `retail-ops-dev-crosspersona-quick-<gateway-suffix>`; Cognito resource server `retail-ops-oem` + client-credentials client `retail-ops-quick-mcp` | A | PASS | gateway and target `oem-read` READY; client-credentials token → `tools/list` returns the 4 OEM tools (plus the Gateway's built-in search tool); no token → 401 |
| 3b | MCP action connector `Retail Ops OEM Read Gateway` | B | PASS | Console checklist step 1. `CreateActionConnector` rejects type `MODEL_CONTEXT_PROTOCOL` (`InvalidParameterValueException`, 2026-10-23), so the owner `admin/jdoe` built it in the console on 2026-10-23: `describe_action_connector <connector-id>` → `MODEL_CONTEXT_PROTOCOL`, `CREATION_SUCCESSFUL`, OAuth client credentials, 5 enabled action ids (the API doesn't name them; the owner turned on the 4 OEM reads); shared with `retail-ops-builders`, both builder users (6 actions) and the 4 OEM groups. On 2026-10-23 between 21:24 and 21:34 PDT the Quick gateway served 12 `tools/call` (`oem_get_account` 4, `oem_get_alerts` 4, `oem_list_accounts` 3, `oem_get_incentive_programs` 1), 0 user or system errors; the target Lambda `retail-ops-dev-gateway-oem` ran 12 times, 0 errors |
| 4 | Add datasets, topic, knowledge bases, dashboard and connector to the space | A | PASS | 2026-10-23: `list_space_resources retail-ops-space` → 1 topic, 8 datasets, 9 knowledge bases, 1 dashboard and the connector (20), the connector added by `deploy_quick_content --apply --only space,agent` after 3b (Console checklist step 2) |
| 5 | Field Sales chat agent | A | PARTIAL | 2026-10-23: `describe_agent retail-ops-field-sales-agent` → `Retail Ops Field Sales Agent`, `PUBLISHED`, linked to the space and to the connector (`ActionConnectors`), instructions with GR-001…008; shared with `retail-ops-builders`, both builder users (5 actions) and `retail-ops-oem-dm`/`-regional`/`-ci-planning`; `search_agents` with `DIRECT_QUICKSIGHT_OWNER` returns it for `admin` and `admin/jdoe`. The owner opened the agent and it answered (checklist step 3), with OEM tool calls through the connector (row 3b). Open: confirming the cited answers and the GR-001/GR-005 refusal (no chat API) |
| 6 | 8 Quick Flows and the weekly District Roll-up schedule | A | PARTIAL | 2026-10-23: `list_flows` → the 8 `Retail Ops …` Flows, all `PUBLISHED`; `get_flow_permissions` → `retail-ops-builders`, `admin` and `admin/jdoe` (13 actions), the 3 field-sales OEM groups (7 actions). The console flagged "Invalid reference" / "Invalid dependencies" on every step that referenced an earlier one: the API-written step ids were version-5 UUIDs, and the console uses version 4. `--apply --only flows` rewrote all 8 in place with version-4-shaped ids and single-turn `OEM facts` prompts; `describe_flow` DRAFT and PUBLISHED return the repo's definitions, and every `@` reference names an earlier step of the same Flow. `Example Flow` was backed up and deleted. Open: the console reload check, one test run of Flow 1 and the schedule (checklist steps 4–5; no run or schedule API) |
| 7 | Campaign ROI analysis and dashboard `Retail Ops Campaign ROI & Funnel` | A | PASS | 2026-10-23: analysis and dashboard `retail-ops-campaign-roi-funnel` `CREATION_SUCCESSFUL`, no errors, dashboard published; shared with the 4 OEM groups (3 actions) and owned by `retail-ops-builders` and the embed user `admin`; in the space. Not checked: rendering in a browser |
| 8 | Row-level security: 2 rule sets attached to 6 datasets | A | REMOVED | Removed by user decision on 2026-10-21 (spec §13): Quick content is not row-scoped, and access is by Quick group membership only. `deploy_quick_rls.py` and its deploy stage are deleted, so no rule sets, RLS datasets or `lake_rls_*` tables are deployed; the next `deploy_quick_datasets --apply` drops a rule set left on an older stack's datasets. Last live state before removal: 6 datasets `RLS ON`. The per-user embed stays, as group access only (row P1) |
| 9 | Re-discover Flows and dashboards into `quick-config.json` | A | PASS | 2026-10-23: `deploy_quick --apply` → `flows=15 dashboards=4`. As the OEM district manager `U-US-00044`: `GET /oem/quick/flows` → 200, `enabled: true`, the 8 `Retail Ops …` Flows and 7 samples; `GET /oem/quick/dashboards` → 200 with `retail-ops-campaign-roi-funnel`; its `embed-url` route → 200 with a URL |
| — | Allow the console origin for embedding | A | PASS | 2026-10-23: the `l4` stage set `RETAIL_OPS_QUICK_EMBED_DOMAINS=https://<distribution-domain>` on `retail-ops-dev-journeys-service` (from `console-config.json`), and the embed route passes it as `AllowedDomains`, so the Quick admin's domain list is not needed; the embed URL above was generated with it |
| — | Teardown covers the Quick content | A | PASS | 2026-10-23: `deploy/destroy.sh --only quick_content,quick` (dry run) → 25 content resources (dependents first) plus the 13 lake datasets, data source, 5 groups and policy; nothing deleted |

## Remaining environment-dependent boundaries

- Amazon Quick steps without an API (Track B above): one test run of a Flow and the
  District Roll-up schedule (the MCP connector and a first chat were done on
  2026-10-23). Follow the "Console checklist" in `docs/QUICK_BUILD.md`.
- External identity provider registration in accounts other than the
  development account (`cognito_setup --federate`).
- Live CRM/DMS/parts/labor endpoints and credential references.
- A trained Amazon Personalize campaign.
- Amazon Connect voice/channel configuration.
- A streaming-capable runtime/transport if progressive token delivery is
  required.
- Full A7 target orchestration from architecture §6a: per-dealer time-zone
  scheduling, intraday parts/line event subscribers, constraint-aware parts
  execution, and brief/estimate preparation within the shift-start sweep.
