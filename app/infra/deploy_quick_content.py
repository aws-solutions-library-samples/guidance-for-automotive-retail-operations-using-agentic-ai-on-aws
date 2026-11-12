#!/usr/bin/env python3
"""Amazon Quick content for Automotive Retail Operations, created through the Quick API.

Builds the Quick-native content of docs/QUICK_BUILD.md §1–§8 that has a working
public API, in dependency order, idempotently (create when missing, update when
it drifted, otherwise leave it alone):

  datasets   two custom-SQL DIRECT_QUERY datasets on the `retail-ops-lake` Athena
             source (deploy_quick_datasets): `retail-ops-journey-events-sql`
             (typed event_ts, funnel stage, source, dealer, district, market) and
             `retail-ops-campaign-roi-sql` (campaign spend from lake_campaigns
             allocated over the journeys each campaign started). No SPICE import.
  topic      `Retail Ops Journey Funnel` (TopicV2) on the journey dataset; the
             field meanings and synonyms travel as the topic's custom instructions.
  knowledge  S3 knowledge data source `retail-ops-knowledge-s3` and nine knowledge
             bases `Retail Ops …`, one per `quick-index/<kind>` prefix of the lake
             bucket. Document-level ACLs off; the `.md.metadata.json` sidecars of
             the Bedrock knowledge base are excluded by pattern. Quick runs the
             first sync on create.
  space      `Automotive Retail Operations` with the topic, datasets, knowledge
             bases, the dashboard and, once it exists, the MCP connector.
  agent      chat agent `Retail Ops Field Sales Agent` (persona + GR-001…008
             instructions, linked to the space and, once it exists, the connector).
  flows      the eight `Retail Ops …` Flows (CreateFlow publishes them; UpdateFlow
             rewrites the draft and the published version). Steps refer to
             earlier steps as `@<step id>` with version-4-shaped ids, the
             console's encoding (reference_errors checks it). The "OEM facts"
             step names the OEM read tools it needs, with explicit arguments,
             in one turn. Schedules have no API.
  dashboard  analysis + dashboard `Retail Ops Campaign ROI & Funnel` (published as
             version 1 on create; a changed definition publishes a new version).

Every resource is shared (deploy_quick_groups makes the groups): the owner
action set to the group `retail-ops-builders` AND to each builder user directly
(the group's current members plus the Quick user names in
RETAIL_OPS_QUICK_BUILDERS; names that are not registered Quick users are skipped
with a note). The direct grant is needed because the Quick console lists spaces,
chat agents and Flows by direct user permission, not group membership: with the
group grant alone a builder does not see them. The viewer set goes to the
`retail-ops-oem-*` groups the runbook names (groups only), and the dashboard
viewer set also to the shared embed user. Grants only add; nothing is revoked.
Resources this script creates record the calling IAM identity (its raw id) as
creator, where console-created ones show the Quick user name.

NOT created here, because the API cannot: the MCP action connector
`Retail Ops OEM Read Gateway` (CreateActionConnector rejects the
MODEL_CONTEXT_PROTOCOL type; build it in the Quick console). Once it exists, a
re-run finds it by name, shares it, adds it to the space and links it to the
agent. The Flows reach the OEM reads through the agent, so they need no action
ids (the API has no call that maps an action id to its tool name).

Server-assigned ids (the Flows) are recorded in the git-ignored
app/infra/quick-content-config.json.

Usage:
  python3 -m app.infra.deploy_quick_content                    # dry-run
  python3 -m app.infra.deploy_quick_content --apply            # create / update everything
  python3 -m app.infra.deploy_quick_content --apply --only topic,flows
  python3 -m app.infra.deploy_quick_content --status           # what exists, with ids
  python3 -m app.infra.deploy_quick_content --delete           # list what --delete --apply removes
  python3 -m app.infra.deploy_quick_content --delete --apply   # remove it, dependents first
"""
from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..services.platform.quick import QUICK_BUILDERS_GROUP
from . import deploy_quick_datasets as dqd
from . import naming

ROOT = Path(__file__).resolve().parents[2]
REGION = "us-east-1"
NAMESPACE = "default"
STEM = "retail-ops"
CONFIG_PATH = naming.config_path("quick-content")
DATABASE = dqd.DATABASE
STEPS = ("datasets", "topic", "knowledge", "space", "agent", "flows", "dashboard")

# --- names and ids (ids are deterministic wherever the API lets us choose) --------
SPACE_NAME = "Automotive Retail Operations"
SPACE_DESCRIPTION = "Field Sales Agent and Campaign Agent; event-lake and knowledge-base backed."
JOURNEY_DATASET = "journey-events-sql"
ROI_DATASET = "campaign-roi-sql"
TOPIC = "journey-funnel"
KB_SOURCE = "knowledge-s3"
SPACE = "space"
AGENT = "field-sales-agent"
DASHBOARD = "campaign-roi-funnel"
TOPIC_NAME = "Retail Ops Journey Funnel"
AGENT_NAME = "Retail Ops Field Sales Agent"
DASHBOARD_NAME = "Retail Ops Campaign ROI & Funnel"
KB_SOURCE_NAME = "Retail Ops Knowledge S3"
# Console-created (see the module docstring). The API name pattern allows letters,
# digits, spaces, "_" and "-" only, so the runbook's "(Gateway)" is written without
# parentheses.
CONNECTOR_NAME = "Retail Ops OEM Read Gateway"
OEM_VIEWERS = ("retail-ops-oem-dm", "retail-ops-oem-regional", "retail-ops-oem-ci-planning",
               "retail-ops-oem-marketing")
FIELD_SALES_VIEWERS = OEM_VIEWERS[:3]   # agent and Flows (runbook §5, §6.3)


def rid(name: str, stem: str = STEM) -> str:
    """The deterministic Quick id of one of this deployer's resources."""
    return f"{stem}-{name}"


# kind, knowledge base name, documents in the corpus (deploy_quick_index), who uses it
KNOWLEDGE_BASES: tuple[tuple[str, str, int, str], ...] = (
    ("crm_notes", "Retail Ops CRM Notes", 240, "Field Sales Agent and Sales Agent"),
    ("templates", "Retail Ops Templates", 84, "Sales Agent nurture"),
    ("transcripts", "Retail Ops Transcripts", 80, "Sales Agent customer conversations"),
    ("objection_strategies", "Retail Ops Objection Strategies", 34, "Field Sales Agent win strategy and Sales Agent"),
    ("bulletins", "Retail Ops Bulletins", 24, "Field Sales Agent sales ops digest"),
    ("prior_plans", "Retail Ops Prior Plans", 24, "Field Sales Agent account plans"),
    ("product_guides", "Retail Ops Product Guides", 14, "Field Sales Agent and Sales Agent"),
    ("repair_procedures", "Retail Ops Repair Procedures", 24, "Service Agent"),
    ("tsbs", "Retail Ops TSBs", 14, "Service Agent"),
)
# The Bedrock knowledge base stage writes `<doc>.md.metadata.json` next to each
# document; they are not documents.
KB_EXCLUDE_PATTERNS = ["*.metadata.json"]


def kb_id(kind: str, stem: str = STEM) -> str:
    return rid(f"kb-{kind.replace('_', '-')}", stem)


# Datasets of the lake that the space carries next to the two custom-SQL ones.
SPACE_LAKE_DATASETS = ("journeys", "campaigns", "rdr", "csi", "marketintel", "service")

# --- permission action sets ----------------------------------------------------------
OWNER: dict[str, tuple[str, ...]] = {
    "dataset": dqd.DATASET_OWNER_ACTIONS,
    "data source": dqd.DATA_SOURCE_OWNER_ACTIONS,
    "topic": ("quicksight:DescribeTopic", "quicksight:DescribeTopicPermissions", "quicksight:PassTopic",
              "quicksight:UpdateTopic", "quicksight:UpdateTopicPermissions", "quicksight:DeleteTopic"),
    "knowledge base": (
        "quicksight:DescribeKnowledgeBase", "quicksight:DescribeKnowledgeBasePermissions",
        "quicksight:UpdateKnowledgeBase", "quicksight:UpdateKnowledgeBasePermissions",
        "quicksight:DeleteKnowledgeBase", "quicksight:CreateKnowledgeBaseRefreshSchedule",
        "quicksight:DescribeKnowledgeBaseRefreshSchedule", "quicksight:ListKnowledgeBaseRefreshSchedules",
        "quicksight:UpdateKnowledgeBaseRefreshSchedule", "quicksight:DeleteKnowledgeBaseRefreshSchedule",
        "quicksight:CreateKnowledgeBaseIngestion", "quicksight:CancelKnowledgeBaseIngestion",
        "quicksight:DescribeKnowledgeBaseIngestion", "quicksight:ListKnowledgeBaseIngestions"),
    "connector": ("quicksight:DescribeActionConnector", "quicksight:DescribeActionConnectorPermissions",
                  "quicksight:ListActionConnectors", "quicksight:UpdateActionConnector",
                  "quicksight:UpdateActionConnectorPermissions", "quicksight:DeleteActionConnector"),
    "space": ("quicksight:DescribeSpace", "quicksight:DescribeSpacePermissions", "quicksight:UpdateSpace",
              "quicksight:UpdateSpacePermissions", "quicksight:DeleteSpace", "quicksight:CreateSpaceFolder",
              "quicksight:UpdateSpaceFolder", "quicksight:DeleteSpaceFolder", "quicksight:ListSpaceFolderMembers",
              "quicksight:MoveSpaceFolderMember", "quicksight:CreateDocument", "quicksight:GetDocument",
              "quicksight:ListDocument", "quicksight:DeleteDocument"),
    "agent": ("quicksight:DescribeAgent", "quicksight:DescribeAgentPermissions", "quicksight:UpdateAgent",
              "quicksight:UpdateAgentPermissions", "quicksight:DeleteAgent"),
    # Flow owner and viewer sets are the documented ones (API_Permission).
    "flow": ("quicksight:GetFlow", "quicksight:UpdateFlow", "quicksight:DeleteFlow",
             "quicksight:PublishFlow", "quicksight:UnpublishFlow", "quicksight:UpdateFlowPermissions",
             "quicksight:DescribeFlowPermissions", "quicksight:StartFlowSession",
             "quicksight:UpdateFlowSession", "quicksight:GetFlowSession", "quicksight:StopFlowSession",
             "quicksight:CreatePresignedUrl", "quicksight:GetFlowStages"),
    "analysis": ("quicksight:DescribeAnalysis", "quicksight:DescribeAnalysisPermissions",
                 "quicksight:QueryAnalysis", "quicksight:UpdateAnalysis", "quicksight:UpdateAnalysisPermissions",
                 "quicksight:DeleteAnalysis", "quicksight:RestoreAnalysis"),
    "dashboard": ("quicksight:DescribeDashboard", "quicksight:ListDashboardVersions",
                  "quicksight:QueryDashboard", "quicksight:UpdateDashboard", "quicksight:DeleteDashboard",
                  "quicksight:UpdateDashboardPermissions", "quicksight:DescribeDashboardPermissions",
                  "quicksight:UpdateDashboardPublishedVersion"),
}
VIEWER: dict[str, tuple[str, ...]] = {
    "topic": ("quicksight:DescribeTopic", "quicksight:PassTopic"),
    "knowledge base": ("quicksight:DescribeKnowledgeBase",),
    "connector": ("quicksight:DescribeActionConnector", "quicksight:ListActionConnectors"),
    "space": ("quicksight:DescribeSpace", "quicksight:GetDocument", "quicksight:ListDocument"),
    "agent": ("quicksight:DescribeAgent",),
    "flow": ("quicksight:GetFlow", "quicksight:StartFlowSession", "quicksight:UpdateFlowSession",
             "quicksight:GetFlowSession", "quicksight:StopFlowSession", "quicksight:CreatePresignedUrl",
             "quicksight:GetFlowStages"),
    "dashboard": ("quicksight:DescribeDashboard", "quicksight:ListDashboardVersions",
                  "quicksight:QueryDashboard"),
}
# kind -> (describe-permissions call, update-permissions call, id parameter)
_PERM_API: dict[str, tuple[str, str, str]] = {
    "dataset": ("describe_data_set_permissions", "update_data_set_permissions", "DataSetId"),
    "data source": ("describe_data_source_permissions", "update_data_source_permissions", "DataSourceId"),
    "topic": ("describe_topic_permissions_v2", "update_topic_permissions_v2", "TopicId"),
    "knowledge base": ("describe_knowledge_base_permissions", "update_knowledge_base_permissions",
                       "KnowledgeBaseId"),
    "connector": ("describe_action_connector_permissions", "update_action_connector_permissions",
                  "ActionConnectorId"),
    "space": ("describe_space_permissions", "update_space_permissions", "SpaceId"),
    "agent": ("describe_agent_permissions", "update_agent_permissions", "AgentId"),
    "flow": ("get_flow_permissions", "update_flow_permissions", "FlowId"),
    "analysis": ("describe_analysis_permissions", "update_analysis_permissions", "AnalysisId"),
    "dashboard": ("describe_dashboard_permissions", "update_dashboard_permissions", "DashboardId"),
}

# --- SQL ---------------------------------------------------------------------------
# One row per journey event. stage = the stage the event moved the journey to
# (payload.stage, or payload.to on stage_changed); source, dealer, district and
# market come from the journey (lake_journeys), which the events table lacks.
JOURNEY_SQL = f"""SELECT e.journey_id,
       e.event_type,
       CAST(from_iso8601_timestamp(e.event_ts) AS timestamp) AS event_ts,
       e.correlation_id,
       e.evidence,
       e.dt,
       e.stage,
       CASE e.stage WHEN 'DISCOVER' THEN 1 WHEN 'QUALIFY' THEN 2 WHEN 'CONFIGURE' THEN 3
                    WHEN 'TRADE_IN' THEN 4 WHEN 'FINANCE' THEN 5 WHEN 'AGREE' THEN 6
                    WHEN 'DELIVER' THEN 7 WHEN 'OWN' THEN 8 END AS stage_order,
       COALESCE(json_extract_scalar(e.payload, '$.source'), j.source) AS source,
       j.dealer_id,
       j.district_id,
       j.market_id AS market,
       json_extract_scalar(e.actor, '$.type') AS actor_type
FROM (SELECT *, COALESCE(json_extract_scalar(payload, '$.stage'),
                         json_extract_scalar(payload, '$.to')) AS stage
      FROM {DATABASE}.journey_events) e
LEFT JOIN {DATABASE}.lake_journeys j ON j.journey_id = e.journey_id"""
JOURNEY_COLUMNS = [("journey_id", "STRING"), ("event_type", "STRING"), ("event_ts", "DATETIME"),
                   ("correlation_id", "STRING"), ("evidence", "STRING"), ("dt", "STRING"),
                   ("stage", "STRING"), ("stage_order", "INTEGER"), ("source", "STRING"),
                   ("dealer_id", "STRING"), ("district_id", "STRING"), ("market", "STRING"),
                   ("actor_type", "STRING")]

# Campaign spend from lake_campaigns, allocated to source x dealer by the share of
# the campaign's journeys (an estimate; the lake holds no per-dealer spend). The
# two tables write campaign ids differently (CMP-US-00004 vs CMP-US-04), so both
# are joined on market prefix + number. `source` is the journey's lead source,
# shown as "Platform" on the dashboard.
_CAMPAIGN_KEY = ("regexp_extract({c}, '^CMP-[A-Z]+') || '-' || "
                 "CAST(CAST(regexp_extract({c}, '([0-9]+)$') AS integer) AS varchar)")
ROI_SQL = f"""WITH c AS (
  SELECT campaign_id, name, objective, status, district_id, market, CAST(budget AS double) AS budget,
         {_CAMPAIGN_KEY.format(c='campaign_id')} AS campaign_key
  FROM {DATABASE}.lake_campaigns),
j AS (
  SELECT journey_id, source, dealer_id, stage,
         {_CAMPAIGN_KEY.format(c='campaign_id')} AS campaign_key
  FROM {DATABASE}.lake_journeys WHERE campaign_id IS NOT NULL),
g AS (
  SELECT c.campaign_id, c.name AS campaign, c.objective, c.status, c.market, c.district_id,
         j.source, j.dealer_id, c.budget,
         count(DISTINCT j.journey_id) AS journeys,
         count(DISTINCT CASE WHEN j.stage IN ('DELIVER', 'OWN') THEN j.journey_id END) AS sold
  FROM c LEFT JOIN j ON j.campaign_key = c.campaign_key
  GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9)
SELECT campaign_id, campaign, objective, status, market, district_id, source, dealer_id,
       journeys, sold,
       COALESCE(budget * journeys / NULLIF(sum(journeys) OVER (PARTITION BY campaign_id), 0), budget)
           AS spend_allocated,
       budget AS campaign_budget
FROM g"""
ROI_COLUMNS = [("campaign_id", "STRING"), ("campaign", "STRING"), ("objective", "STRING"),
               ("status", "STRING"), ("market", "STRING"), ("district_id", "STRING"),
               ("source", "STRING"), ("dealer_id", "STRING"), ("journeys", "INTEGER"),
               ("sold", "INTEGER"), ("spend_allocated", "DECIMAL"), ("campaign_budget", "DECIMAL")]
DATASETS = {
    JOURNEY_DATASET: ("Retail Ops · journey_events (funnel)", "journey-events-sql", JOURNEY_SQL, JOURNEY_COLUMNS),
    ROI_DATASET: ("Retail Ops · campaign ROI", "campaign-roi-sql", ROI_SQL, ROI_COLUMNS),
}

TOPIC_INSTRUCTIONS = (
    "Journey events of the Automotive Retail Operations purchase journeys (synthetic data). One row per "
    "event. journey_id identifies one customer purchase journey: count distinct journey_id to count "
    "journeys. event_ts is the date and time of the event; use it for every date filter such as last 90 "
    "days. stage is the funnel stage the event moved the journey to: DISCOVER, QUALIFY, CONFIGURE, "
    "TRADE_IN, FINANCE, AGREE, DELIVER, OWN; stage_order sorts the stages in funnel order. Synonyms of "
    "stage: funnel stage, step, phase. source is the lead source; synonyms: channel, platform, lead "
    "source; values web, walk_in, service_lane, campaign, third_party_lead, phone. dealer_id is the "
    "dealer; synonyms: dealer, store, rooftop. district_id is the sales district (for example DIST-07). "
    "market is us or de. event_type is the kind of event (for example stage_changed). Funnel questions: "
    "count distinct journey_id by stage, ordered by stage_order.")

# --- chat agent (runbook §5) ---------------------------------------------------------
AGENT_DESCRIPTION = ("Account-level planning and field toolkit for OEM district and regional managers and "
                     "C&I planners. Every answer cited and checked against GR-001 to GR-008.")
AGENT_WELCOME = ("Ask about your dealers, performance, alerts, or programs. I'll cite every figure and "
                 "draft plans for your review.")
AGENT_STARTERS = ["Which of my dealers are below objective this month?",
                  "Summarize open HIGH alerts for my district",
                  "Draft an account plan for D-105"]
AGENT_INSTRUCTIONS = """You are the Field Sales Agent for automotive field sales. Your users are OEM district
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
plan, contact a dealer, or change allocations."""
AGENT_PROMPT = {
    "Identity": "Field Sales Agent for OEM district managers, regional sales managers and C&I planners.",
    "Tone": "Executive, factual and calm.",
    "OutputStyle": "Lead with the answer, then cited figures; bullets for lists longer than three items.",
    "ResponseLength": "Concise: as short as the answer allows.",
    "CustomInstructions": AGENT_INSTRUCTIONS,
}
AGENT_ICON = "id_agent_icon_ai"

# --- Flows (runbook §6) ----------------------------------------------------------------
# Step specs: ("text", title, placeholder, default) · ("files", title)
# · ("kb", title, prompt, [kinds]) · ("topic", title, prompt) · ("agent", title, prompt).
# "{Title}" in a prompt becomes an @-reference to that earlier step.
_DRAFT_RULES = (" Cite the source of every figure (account id, alert id, program id, or the note's "
                "source_ref). Run the GR-001 to GR-008 guardrail check on your own draft; if a rule would "
                "fire, name the rule and give the safe alternative instead. Return it as a draft for human "
                "review; do not publish, send or save it anywhere.")
# The "OEM facts" step: the chat agent calls only the tools this Flow needs, by
# name and with explicit arguments, all in one turn, and hands back the raw
# records. Naming the tools skips tool search and planning; one turn avoids a
# model round trip per tool; raw output is short to generate and to read in the
# next steps. (A direct connector action step would skip the model entirely, but
# no API maps the connector's action ids to tool names.) The Gateway returns
# alerts with dealer_id (no account_id), so alerts are matched on dealer_id.
_FACTS = ("Call only these Retail Ops OEM Read Gateway tools, all together in a single turn, with exactly "
          "these arguments: ")
_FACTS_END = (" Call no other tool and do not search the knowledge bases, topics, datasets or the web. Return "
              "the records you keep exactly as the tools returned them, as JSON with every field, id and "
              "source_ref, and no commentary. Do not draft anything.")
_ACCOUNT = "oem_get_account with account_id {Account ID}"
_ALERTS = "oem_get_alerts with market_id \"us\""
_KEEP_ALERTS = " Keep only the alerts whose dealer_id is the account's dealer_id."
FLOWS: tuple[dict[str, Any], ...] = (
    {"name": "Retail Ops Account Plan",
     "description": "Quarterly account plan for one dealer account, drafted by the Field Sales Agent from "
                    "OEM reads, CRM notes and prior plans. Draft for review.",
     "steps": [("text", "Account ID", "e.g. ACC-US-00001", "ACC-US-00001"),
               ("agent", "OEM facts", _FACTS + _ACCOUNT + "; " + _ALERTS + "." + _KEEP_ALERTS + _FACTS_END),
               ("kb", "Notes", ("Find CRM notes and prior account plans about the account in {OEM facts} (match "
                               "on account_id, dealer name or dealer_id). Return each passage with its "
                               "source_ref."), ["crm_notes", "prior_plans"]),
               ("agent", "Account plan draft",
                "Draft a quarterly account plan for this account. Account and open alerts: {OEM facts}. "
                "Relevant notes and prior plans: {Notes}. Sections, in this order: Performance vs objective, "
                "Gaps, Opportunities, Recommended actions, Risks." + _DRAFT_RULES)]},
    {"name": "Retail Ops Opportunity Brief",
     "description": "Opportunity brief for one account - opportunity, customer context, eligible programs, "
                    "next steps. Draft for review.",
     "steps": [("text", "Account ID", "e.g. ACC-US-00001", "ACC-US-00001"),
               ("text", "Opportunity", "Describe the opportunity in 1 to 3 sentences", ""),
               ("agent", "OEM facts", _FACTS + _ACCOUNT + "; oem_get_incentive_programs with market_id \"us\". "
                                      "Keep only the programs relevant to {Opportunity}." + _FACTS_END),
               ("kb", "Notes", ("Find CRM notes relevant to {Opportunity} for the account in {OEM facts}. "
                               "Return each passage with its source_ref."), ["crm_notes"]),
               ("agent", "Opportunity brief draft",
                "Draft an opportunity brief. Opportunity: {Opportunity}. Account and programs: {OEM facts}. "
                "CRM notes: {Notes}. Sections, in this order: Opportunity, Customer context, Eligible "
                "programs, Next steps." + _DRAFT_RULES)]},
    {"name": "Retail Ops Win Strategy",
     "description": "Win strategy for a dealer meeting - objectives, stakeholders, value points, objection "
                    "handling, asks. Draft for review.",
     "steps": [("text", "Account ID", "e.g. ACC-US-00001", "ACC-US-00001"),
               ("text", "Meeting date", "e.g. 2026-11-03", ""),
               ("agent", "OEM facts", _FACTS + _ACCOUNT + "." + _FACTS_END),
               ("kb", "Playbook", ("Find objection strategies, product points and CRM history relevant to the "
                                  "account in {OEM facts}. Return each passage with its source_ref."),
                ["crm_notes", "objection_strategies", "product_guides"]),
               ("agent", "Win strategy draft",
                "Draft a win strategy for the meeting on {Meeting date}. Account: {OEM facts}. Playbook "
                "material: {Playbook}. Sections, in this order: Objectives, Stakeholders, Value points, "
                "Objection handling, Asks." + _DRAFT_RULES)]},
    {"name": "Retail Ops Action Plan",
     "description": "Action plan for an account or an alert - issue, root-cause hypotheses, actions with "
                    "owners and milestones. Draft for review.",
     "steps": [("text", "Account or alert ID", "ACC-US-00001 or an alert id", "ACC-US-00001"),
               ("agent", "OEM facts",
                _FACTS + "if {Account or alert ID} starts with ACC-, oem_get_account with account_id {Account "
                "or alert ID} and " + _ALERTS + ", then keep only the alerts whose dealer_id is the account's "
                "dealer_id; otherwise it is an alert id: " + _ALERTS + " and oem_list_accounts with market_id "
                "\"us\", then keep only that alert and the account whose dealer_id is the alert's dealer_id."
                + _FACTS_END),
               ("agent", "Action plan draft",
                "Draft an action plan for {Account or alert ID}. Account and alerts: {OEM facts}. Sections, "
                "in this order: Issue, Root-cause hypotheses, Actions with owners and milestones."
                + _DRAFT_RULES)]},
    {"name": "Retail Ops Contact Report",
     "description": "Contact report after a dealer visit - attendees, discussion, commitments, follow-ups, "
                    "next visit focus. Draft for review.",
     "steps": [("text", "Account ID", "e.g. ACC-US-00001", "ACC-US-00001"),
               ("text", "Meeting notes", "Paste notes or a transcript", ""),
               ("files", "Recording or transcript file"),
               ("agent", "OEM facts", _FACTS + _ACCOUNT + "." + _FACTS_END),
               ("kb", "Plan history", ("Find the account plans for the dealer in {OEM facts} (match on "
                                      "dealer_id). For each, return the plan id, version, status and its "
                                      "source_ref."), ["prior_plans"]),
               ("agent", "Contact report draft",
                "Draft a contact report. Use {Meeting notes} if present, otherwise summarise {Recording or "
                "transcript file}. Account: {OEM facts}. Tie the follow-ups to the plan actions in {Plan "
                "history}. Sections, in this order: Attendees, Discussion, Commitments, Follow-ups with "
                "deadlines, Next visit focus." + _DRAFT_RULES)]},
    {"name": "Retail Ops Meeting Package",
     "description": "Meeting package for a dealer meeting - agenda, KPI snapshot, plan status, talking "
                    "points. Draft for review.",
     "steps": [("text", "Account ID", "e.g. ACC-US-00001", "ACC-US-00001"),
               ("text", "Meeting type", "e.g. quarterly business review", ""),
               ("agent", "OEM facts", _FACTS + _ACCOUNT + "; " + _ALERTS + "." + _KEEP_ALERTS + _FACTS_END),
               ("kb", "Notes", ("Find CRM notes and prior account plans about the account in {OEM facts} (match "
                               "on account_id, dealer name or dealer_id). Return each passage with its "
                               "source_ref."), ["crm_notes", "prior_plans"]),
               ("topic", "Funnel", ("Journey counts by stage and source for the dealer_id in {OEM facts}, last "
                                   "90 days.")),
               ("agent", "Meeting package draft",
                "Draft a meeting package for a {Meeting type} meeting. Account and open alerts: {OEM facts}. "
                "Notes and prior plans: {Notes}. Funnel: {Funnel}. Sections, in this order: Agenda, KPI "
                "snapshot, Plan status, Talking points." + _DRAFT_RULES)]},
    {"name": "Retail Ops District Roll-up",
     "description": "Weekly district roll-up - district KPIs, accounts below threshold, top alerts, drafted "
                    "actions. Draft for review.",
     "steps": [("text", "District ID", "e.g. DIST-07", "DIST-07"),
               ("agent", "OEM facts", _FACTS + "oem_list_accounts with market_id \"us\"; " + _ALERTS + ". Keep "
                                      "only the accounts whose district_id is {District ID}, and only the "
                                      "alerts whose dealer_id is the dealer_id of one of those accounts."
                                      + _FACTS_END),
               ("topic", "Funnel", "Journey counts by stage for district {District ID}, last 30 days."),
               ("agent", "District roll-up draft",
                "Draft the district roll-up for {District ID}. Accounts and alerts: {OEM facts}. Funnel: "
                "{Funnel}. Sections, in this order: District KPIs, Accounts below threshold, Top alerts, "
                "Drafted actions." + _DRAFT_RULES)]},
    {"name": "Retail Ops Sales Ops Digest",
     "description": "Sales ops digest for a date range - new or changed programs, deadlines, what to action "
                    "this week. Draft for review.",
     "steps": [("text", "From date", "e.g. 2026-10-01", ""),
               ("text", "To date", "e.g. 2026-10-31", ""),
               ("agent", "OEM facts", _FACTS + "oem_get_incentive_programs with market_id \"us\". Keep only the "
                                      "programs that start, change or end between {From date} and {To "
                                      "date}." + _FACTS_END),
               ("kb", "Bulletins", ("Find bulletins published between {From date} and {To date}. Return each "
                                   "passage with its source_ref."), ["bulletins"]),
               ("agent", "Sales ops digest draft",
                "Draft the sales ops digest for {From date} to {To date}. Programs: {OEM facts}. Bulletins: "
                "{Bulletins}. Sections, in this order: New or changed programs, Deadlines, What to action "
                "this week." + _DRAFT_RULES)]},
)
FLOW_NAMES = tuple(f["name"] for f in FLOWS)


# --- helpers -------------------------------------------------------------------------
class Ctx:
    """Account, principals and the clients one run needs."""

    def __init__(self, qs, account: str, caller_arn: str, apply: bool,
                 builder_users: tuple[str, ...] = ()) -> None:
        self.qs, self.account, self.apply = qs, account, apply
        self.caller = caller_arn                      # the Quick user the API calls run as
        self.builders = group_arn(account, QUICK_BUILDERS_GROUP)
        # Quick user ARNs granted the owner set directly (dqd.builder_user_arns).
        self.builder_users = tuple(dict.fromkeys(builder_users))
        self.embed_user = embed_user_arn(account)
        self.cfg: dict[str, Any] = load_config()

    @property
    def owners(self) -> tuple[str, ...]:
        """Who gets the owner action set: the builders group, then each builder user."""
        return (self.builders, *self.builder_users)

    def arn(self, kind: str, resource_id: str) -> str:
        return f"arn:aws:quicksight:{REGION}:{self.account}:{kind}/{resource_id}"


def group_arn(account: str, group: str) -> str:
    return f"arn:aws:quicksight:{REGION}:{account}:group/{NAMESPACE}/{group}"


def embed_user_arn(account: str) -> str:
    """The shared embed user (quick-config.json, written by deploy_quick), else default/admin."""
    path = naming.config_path("quick")
    if path.exists():
        arn = json.loads(path.read_text()).get("embed_user_arn")
        if arn:
            return arn
    return f"arn:aws:quicksight:{REGION}:{account}:user/{NAMESPACE}/admin"


def load_config() -> dict[str, Any]:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except ValueError:
            return {}
    return {}


def save_config(cfg: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n")


def caller_quick_user(qs, sts, account: str) -> str:
    """The Quick user the caller's IAM identity maps to (an IAM user `admin` is the
    Quick user `admin`; an assumed role session is `<role>/<session>`)."""
    arn = sts.get_caller_identity()["Arn"]
    tail = arn.split(":", 5)[-1]
    if tail.startswith("user/"):
        name = tail.split("/")[-1]
    elif tail.startswith("assumed-role/"):
        _, role, session = tail.split("/", 2)
        name = f"{role}/{session}"
    else:
        name = tail
    return f"arn:aws:quicksight:{REGION}:{account}:user/{NAMESPACE}/{name}"


def _not_found(e: Exception) -> bool:
    return "NotFound" in type(e).__name__ or "ResourceNotFound" in str(e)


def _try(fn: Callable, **kw) -> dict | None:
    """Call a describe; None when the resource does not exist."""
    try:
        return fn(**kw)
    except Exception as e:
        if _not_found(e):
            return None
        raise


def _wait(label: str, probe: Callable[[], str], done: tuple[str, ...], failed: tuple[str, ...] = (),
          timeout_s: int = 300, every_s: float = 5) -> str:
    """Poll `probe` until it returns a status in `done`; raise on `failed` or timeout."""
    deadline = time.monotonic() + timeout_s
    while True:
        status = probe()
        if status in done:
            return status
        if status in failed:
            raise SystemExit(f"{label}: {status}")
        if time.monotonic() > deadline:
            raise SystemExit(f"{label}: still {status} after {timeout_s}s")
        time.sleep(every_s)


def share(ctx: Ctx, kind: str, resource_id: str, principal: str, actions: tuple[str, ...], label: str) -> bool:
    """Grant `actions` on one resource to `principal`; nothing is sent when the
    principal already holds them all. Dry run unless ctx.apply. True when granted."""
    describe, update, id_key = _PERM_API[kind]
    who = dqd.principal_name(principal)
    try:
        perms = getattr(ctx.qs, describe)(AwsAccountId=ctx.account, **{id_key: resource_id}).get("Permissions") or []
    except Exception as e:
        if not _not_found(e):
            raise
        perms = None
    have: set[str] = set()
    for p in perms or []:
        if p.get("Principal") == principal:
            have.update(p.get("Actions", []))
    if set(actions) <= have:
        print(f"    [share] {who} already has {kind} {label}")
        return False
    if not ctx.apply:
        print(f"    [dry-run] would share {kind} {label} with {who} ({len(actions)} actions)")
        return True
    getattr(ctx.qs, update)(AwsAccountId=ctx.account, **{id_key: resource_id},
                            GrantPermissions=[{"Principal": principal, "Actions": list(actions)}])
    print(f"    [share] {kind} {label} -> {who} ({len(actions)} actions)")
    return True


def share_owner(ctx: Ctx, kind: str, resource_id: str, label: str) -> None:
    """The owner set to retail-ops-builders and to each builder user directly (the
    console lists spaces, chat agents and Flows by direct user permission)."""
    for p in ctx.owners:
        share(ctx, kind, resource_id, p, OWNER[kind], label)


def share_all(ctx: Ctx, kind: str, resource_id: str, label: str, viewers: tuple[str, ...] = (),
              extra_viewers: tuple[str, ...] = ()) -> None:
    """Owner to retail-ops-builders and the builder users, viewer to each named
    group (and extra principals)."""
    share_owner(ctx, kind, resource_id, label)
    for g in viewers:
        share(ctx, kind, resource_id, group_arn(ctx.account, g), VIEWER[kind], label)
    for p in extra_viewers:
        share(ctx, kind, resource_id, p, VIEWER[kind], label)


def _owner_principals(ctx: Ctx) -> list[str]:
    """The caller (so it keeps managing what it made), retail-ops-builders and the
    builder users, each once."""
    return list(dict.fromkeys((ctx.caller, *ctx.owners)))


def _owner_perms(ctx: Ctx, kind: str) -> list[dict[str, Any]]:
    """Owner permissions for a create call."""
    return [{"Principal": p, "Actions": list(OWNER[kind])} for p in _owner_principals(ctx)]


# --- step 1: custom-SQL datasets ----------------------------------------------------------
def dataset_definition(ctx: Ctx, key: str) -> dict[str, Any]:
    name, _, sql, cols = DATASETS[key]
    return {
        "AwsAccountId": ctx.account, "DataSetId": rid(key), "Name": name,
        "PhysicalTableMap": {"sql": {"CustomSql": {
            "DataSourceArn": ctx.arn("datasource", dqd.DATA_SOURCE_ID), "Name": key, "SqlQuery": sql,
            "Columns": [{"Name": c, "Type": t} for c, t in cols]}}},
        "ImportMode": "DIRECT_QUERY",
    }


def ensure_datasets(ctx: Ctx) -> None:
    print("  [datasets] custom-SQL DIRECT_QUERY datasets on data source retail-ops-lake")
    for key in DATASETS:
        dsid = rid(key)
        want = dataset_definition(ctx, key)
        cur = _try(ctx.qs.describe_data_set, AwsAccountId=ctx.account, DataSetId=dsid)
        if cur is None:
            if ctx.apply:
                ctx.qs.create_data_set(**want, Permissions=_owner_perms(ctx, "dataset"))
                print(f"    [create] dataset {dsid} ({want['Name']})")
            else:
                print(f"    [dry-run] would create dataset {dsid} ({want['Name']})")
        else:
            d = cur["DataSet"]
            sql = d.get("PhysicalTableMap", {}).get("sql", {}).get("CustomSql") or {}
            same = (d.get("Name") == want["Name"] and sql.get("SqlQuery") == DATASETS[key][2]
                    and [c["Name"] for c in sql.get("Columns", [])] == [c for c, _ in DATASETS[key][3]]
                    and d.get("ImportMode") == "DIRECT_QUERY")
            if same:
                print(f"    [ok] dataset {dsid} up to date")
            elif ctx.apply:
                ctx.qs.update_data_set(**want)
                print(f"    [update] dataset {dsid}")
            else:
                print(f"    [dry-run] would update dataset {dsid}")
        share_all(ctx, "dataset", dsid, dsid)


# --- step 2: topic --------------------------------------------------------------------------
def topic_details(ctx: Ctx) -> dict[str, Any]:
    return {"Name": TOPIC_NAME,
            "Description": "Journey funnel over the Retail Ops journey events: stage, source, dealer, date.",
            "DataSets": [{"DataSetArn": ctx.arn("dataset", rid(JOURNEY_DATASET)), "DataSetName": "journey_events"}]}


def ensure_topic(ctx: Ctx) -> None:
    tid = rid(TOPIC)
    print(f"  [topic] {TOPIC_NAME} ({tid})")
    want = topic_details(ctx)
    cur = _try(ctx.qs.describe_topic_v2, AwsAccountId=ctx.account, TopicId=tid)
    if cur is None:
        if ctx.apply:
            ctx.qs.create_topic_v2(AwsAccountId=ctx.account, TopicId=tid, Topic=want,
                                   CustomInstructions={"CustomInstructionsString": TOPIC_INSTRUCTIONS})
            print(f"    [create] topic {tid}")
            ctx.qs.update_topic_v2(AwsAccountId=ctx.account, TopicId=tid, Topic=want,
                                   CustomInstructions={"CustomInstructionsString": TOPIC_INSTRUCTIONS},
                                   PublishOption="PUBLISH")
            print(f"    [publish] topic {tid}")
        else:
            print(f"    [dry-run] would create and publish topic {tid}")
    else:
        t = cur.get("Topic", {})
        have_sets = [d.get("DataSetArn") for d in t.get("DataSets", [])]
        have_instr = (cur.get("CustomInstructions") or {}).get("CustomInstructionsString")
        if (t.get("Name") == want["Name"] and have_sets == [want["DataSets"][0]["DataSetArn"]]
                and have_instr == TOPIC_INSTRUCTIONS):
            print(f"    [ok] topic {tid} up to date")
        elif ctx.apply:
            ctx.qs.update_topic_v2(AwsAccountId=ctx.account, TopicId=tid, Topic=want,
                                   CustomInstructions={"CustomInstructionsString": TOPIC_INSTRUCTIONS},
                                   PublishOption="PUBLISH")
            print(f"    [update] topic {tid} (published)")
        else:
            print(f"    [dry-run] would update topic {tid}")
    share_all(ctx, "topic", tid, tid, viewers=OEM_VIEWERS)


# --- step 3: knowledge bases ----------------------------------------------------------------
def lake_bucket(account: str) -> str:
    return dqd._lake_bucket(account)


def kb_template(account: str, kind: str) -> dict[str, Any]:
    return {"type": "S3V2",
            "connectionConfiguration": {"bucketName": lake_bucket(account), "bucketOwnerAccountId": account},
            "filterConfiguration": {"inclusionPrefixes": [f"quick-index/{kind}"], "inclusionPatterns": [],
                                    "exclusionPrefixes": [], "exclusionPatterns": list(KB_EXCLUDE_PATTERNS),
                                    "maxFileSizeInMegaBytes": "10240"},
            # Quick refuses a sync that would delete more than 20% of the documents.
            "deletionProtectionConfiguration": {"enableDeletionProtection": "true",
                                                "deletionProtectionThreshold": "20"}}


def kb_description(kind: str, used_by: str) -> str:
    return f"Synthetic corpus, s3 quick-index {kind}. Used by the {used_by}."


_NO_MEDIA = {"imageExtractionConfiguration": {"imageExtractionStatus": "DISABLED"},
             "audioExtractionConfiguration": {"audioExtractionStatus": "DISABLED"},
             "videoExtractionConfiguration": {"videoExtractionStatus": "DISABLED"}}


def ensure_kb_source(ctx: Ctx) -> str:
    sid = rid(KB_SOURCE)
    arn = ctx.arn("datasource", sid)
    cur = _try(ctx.qs.describe_data_source, AwsAccountId=ctx.account, DataSourceId=sid)
    url = f"s3://{lake_bucket(ctx.account)}"
    if cur is None:
        if not ctx.apply:
            print(f"    [dry-run] would create S3 knowledge data source {sid} ({url})")
            return arn
        ctx.qs.create_data_source(AwsAccountId=ctx.account, DataSourceId=sid, Name=KB_SOURCE_NAME,
                                  Type="S3_KNOWLEDGE_BASE",
                                  DataSourceParameters={"S3KnowledgeBaseParameters": {"BucketUrl": url}},
                                  Permissions=[{"Principal": p["Principal"], "Actions": list(OWNER["data source"])}
                                               for p in _owner_perms(ctx, "data source")])
        print(f"    [create] S3 knowledge data source {sid} ({url})")
        _wait(f"data source {sid}", lambda: ctx.qs.describe_data_source(
            AwsAccountId=ctx.account, DataSourceId=sid)["DataSource"]["Status"],
            ("CREATION_SUCCESSFUL", "UPDATE_SUCCESSFUL"), ("CREATION_FAILED", "UPDATE_FAILED"), 180, 3)
    else:
        print(f"    [ok] S3 knowledge data source {sid} ({cur['DataSource'].get('Status')})")
    share_owner(ctx, "data source", sid, sid)
    return arn


def ensure_knowledge(ctx: Ctx) -> None:
    print("  [knowledge] S3 knowledge bases over quick-index/ (document-level ACLs off)")
    source_arn = ensure_kb_source(ctx)
    created: list[str] = []
    updated: list[str] = []
    for kind, name, _docs, used_by in KNOWLEDGE_BASES:
        kid = kb_id(kind)
        desc = kb_description(kind, used_by)
        cur = _try(ctx.qs.describe_knowledge_base, AwsAccountId=ctx.account, KnowledgeBaseId=kid)
        if cur is None:
            if ctx.apply:
                ctx.qs.create_knowledge_base(
                    AwsAccountId=ctx.account, KnowledgeBaseId=kid, Name=name, Description=desc,
                    DataSourceArn=source_arn,
                    KnowledgeBaseConfiguration={"templateConfiguration": {"template": kb_template(ctx.account, kind)}},
                    MediaExtractionConfiguration=_NO_MEDIA,
                    AccessControlConfiguration={"isACLEnabled": False},
                    Permissions=_owner_perms(ctx, "knowledge base"))
                created.append(kid)
                print(f"    [create] knowledge base {kid} ({name}); first sync starts")
            else:
                print(f"    [dry-run] would create knowledge base {kid} ({name}) over quick-index/{kind}")
        else:
            kb = cur["KnowledgeBase"]
            tmpl = ((kb.get("KnowledgeBaseConfiguration") or {}).get("templateConfiguration") or {}).get("template") or {}
            filt = tmpl.get("filterConfiguration") or {}
            same_filter = (filt.get("inclusionPrefixes") == [f"quick-index/{kind}"]
                           and filt.get("exclusionPatterns") == KB_EXCLUDE_PATTERNS)
            if kb.get("Name") == name and kb.get("Description") == desc and same_filter:
                print(f"    [ok] knowledge base {kid} up to date ({kb.get('DocumentCount')} docs)")
            elif ctx.apply:
                upd: dict[str, Any] = {"AwsAccountId": ctx.account, "KnowledgeBaseId": kid, "Name": name,
                                       "Description": desc}
                if not same_filter:
                    upd["KnowledgeBaseConfiguration"] = {
                        "templateConfiguration": {"template": kb_template(ctx.account, kind)}}
                ctx.qs.update_knowledge_base(**upd)
                updated.append(kid)
                print(f"    [update] knowledge base {kid} ({name})")
            else:
                print(f"    [dry-run] would update knowledge base {kid}")
        # Sharing is refused while a knowledge base is CREATING or UPDATING.
        if ctx.apply and (kid in created or kid in updated):
            _wait(f"knowledge base {kid}", lambda k=kid: ctx.qs.describe_knowledge_base(
                AwsAccountId=ctx.account, KnowledgeBaseId=k)["KnowledgeBase"].get("Status", ""),
                ("ACTIVE",), ("FAILED", "CREATION_FAILED"), 300, 5)
        share_all(ctx, "knowledge base", kid, kid, viewers=OEM_VIEWERS)
    if ctx.apply and created:
        wait_first_sync(ctx, created)


def wait_first_sync(ctx: Ctx, kb_ids: list[str], timeout_s: int = 1200) -> dict[str, Any]:
    """Wait for the first sync of each new knowledge base; report its document count
    against the corpus count (a doubled count would mean sidecars were indexed)."""
    want = {kb_id(k): d for k, _, d, _ in KNOWLEDGE_BASES}
    deadline = time.monotonic() + timeout_s
    result: dict[str, Any] = {}
    pending = set(kb_ids)
    while pending and time.monotonic() < deadline:
        for kid in sorted(pending):
            kb = ctx.qs.describe_knowledge_base(AwsAccountId=ctx.account, KnowledgeBaseId=kid)["KnowledgeBase"]
            st = (kb.get("LatestIngestionSummary") or {}).get("IngestionStatus")
            if st in ("COMPLETED", "FAILED"):
                n = kb.get("DocumentCount")
                ok = "matches the corpus" if n == want.get(kid) else f"corpus has {want.get(kid)}"
                print(f"    [sync] {kid}: {st}, {n} documents ({ok})")
                result[kid] = {"status": st, "documents": n}
                pending.discard(kid)
        if pending:
            time.sleep(15)
    for kid in sorted(pending):
        print(f"    [sync] {kid}: first sync still running after {timeout_s}s (check --status later)")
    return result


# --- connector (console-created; discovered) ------------------------------------------------
def find_connector(ctx: Ctx) -> dict[str, Any] | None:
    """The console-created MCP connector, found by name."""
    token = None
    while True:
        resp = ctx.qs.list_action_connectors(AwsAccountId=ctx.account, **({"NextToken": token} if token else {}))
        for c in resp.get("ActionConnectorSummaries", []):
            if c.get("Name") == CONNECTOR_NAME:
                return c
        token = resp.get("NextToken")
        if not token:
            return None


def connector_step(ctx: Ctx) -> dict[str, Any] | None:
    c = find_connector(ctx)
    if not c:
        print(f"  [connector] {CONNECTOR_NAME} not found: create it in the Quick console "
              "(docs/QUICK_BUILD.md, Console checklist), then re-run this step")
        return None
    print(f"  [connector] {CONNECTOR_NAME} found ({c['ActionConnectorId']}, {c.get('Status')})")
    ctx.cfg["connector_id"] = c["ActionConnectorId"]
    try:
        share_all(ctx, "connector", c["ActionConnectorId"], CONNECTOR_NAME, viewers=OEM_VIEWERS)
    except Exception as e:  # noqa: BLE001 - a console owner may have to share it by hand
        print(f"    [share] could not share {CONNECTOR_NAME}: {type(e).__name__}: {str(e)[:160]} "
              "(share it with retail-ops-builders and each builder user in the console)")
    return c


# --- step 4: space ---------------------------------------------------------------------------
def space_resources(ctx: Ctx, connector: dict[str, Any] | None, dashboard: bool) -> list[tuple[str, str]]:
    out = [("TOPIC", ctx.arn("topic", rid(TOPIC)))]
    out += [("DATA_SET", ctx.arn("dataset", rid(k))) for k in DATASETS]
    out += [("DATA_SET", ctx.arn("dataset", f"retail-ops-lake-{t}")) for t in SPACE_LAKE_DATASETS]
    out += [("KNOWLEDGE_BASE", ctx.arn("knowledge-base", kb_id(k))) for k, *_ in KNOWLEDGE_BASES]
    if dashboard:
        out.append(("DASHBOARD", ctx.arn("dashboard", rid(DASHBOARD))))
    if connector:
        out.append(("ACTION_CONNECTOR", connector["Arn"]))
    return out


def _exists(ctx: Ctx, rtype: str, arn: str) -> bool:
    rid_ = arn.rsplit("/", 1)[-1]
    q, a = ctx.qs, ctx.account
    probe = {"TOPIC": lambda: q.describe_topic_v2(AwsAccountId=a, TopicId=rid_),
             "DATA_SET": lambda: q.describe_data_set(AwsAccountId=a, DataSetId=rid_),
             "KNOWLEDGE_BASE": lambda: q.describe_knowledge_base(AwsAccountId=a, KnowledgeBaseId=rid_),
             "DASHBOARD": lambda: q.describe_dashboard(AwsAccountId=a, DashboardId=rid_),
             "ACTION_CONNECTOR": lambda: q.describe_action_connector(AwsAccountId=a, ActionConnectorId=rid_)}[rtype]
    try:
        probe()
        return True
    except Exception as e:
        if _not_found(e):
            return False
        raise


def ensure_space(ctx: Ctx, connector: dict[str, Any] | None) -> None:
    sid = rid(SPACE)
    print(f"  [space] {SPACE_NAME} ({sid})")
    cur = _try(ctx.qs.describe_space, AwsAccountId=ctx.account, SpaceId=sid)
    if cur is None:
        if not ctx.apply:
            print(f"    [dry-run] would create space {sid}, share it and add its resources")
            return
        ctx.qs.create_space(AwsAccountId=ctx.account, SpaceId=sid, Name=SPACE_NAME, Description=SPACE_DESCRIPTION)
        print(f"    [create] space {sid}")
    else:
        sp = cur.get("Space", {})
        if sp.get("name") == SPACE_NAME and sp.get("description") == SPACE_DESCRIPTION:
            print(f"    [ok] space {sid} exists")
        elif ctx.apply:
            ctx.qs.update_space(AwsAccountId=ctx.account, SpaceId=sid, Name=SPACE_NAME, Description=SPACE_DESCRIPTION)
            print(f"    [update] space {sid}")
    share_all(ctx, "space", sid, sid, viewers=OEM_VIEWERS)
    have = set()
    if ctx.apply or cur is not None:
        lst = ctx.qs.list_space_resources(AwsAccountId=ctx.account, SpaceId=sid)
        have = {r["ResourceDetails"]["resourceArn"] for r in lst.get("SpaceResources", [])}
    dash = _exists(ctx, "DASHBOARD", ctx.arn("dashboard", rid(DASHBOARD)))
    wanted = [(t, a) for t, a in space_resources(ctx, connector, dash) if a not in have]
    missing = [(t, a) for t, a in wanted if not _exists(ctx, t, a)]
    add = [(t, a) for t, a in wanted if (t, a) not in missing]
    for t, a in missing:
        print(f"    [skip] {t} {a.rsplit('/', 1)[-1]} does not exist yet")
    if not add:
        print(f"    [ok] space {sid} holds every resource ({len(have)})")
        return
    if not ctx.apply:
        print(f"    [dry-run] would add {len(add)} resource(s) to space {sid}")
        return
    for i in range(0, len(add), 10):
        ctx.qs.update_space_resources(AwsAccountId=ctx.account, SpaceId=sid, AddResources=[
            {"ResourceType": t, "ResourceDetails": {"resourceArn": a}} for t, a in add[i:i + 10]])
    print(f"    [add] {len(add)} resource(s) to space {sid}: "
          + ", ".join(sorted({t for t, _ in add})))


# --- step 5: chat agent ------------------------------------------------------------------------
def ensure_agent(ctx: Ctx, connector: dict[str, Any] | None) -> None:
    aid = rid(AGENT)
    space_arn = ctx.arn("space", rid(SPACE))
    print(f"  [agent] {AGENT_NAME} ({aid})")
    common = {"AwsAccountId": ctx.account, "AgentId": aid, "Name": AGENT_NAME, "Description": AGENT_DESCRIPTION,
              "IconId": AGENT_ICON, "StarterPrompts": AGENT_STARTERS, "WelcomeMessage": AGENT_WELCOME,
              "CustomPromptInput": {"NewPrompt": AGENT_PROMPT}}
    cur = _try(ctx.qs.describe_agent, AwsAccountId=ctx.account, AgentId=aid)
    if cur is None:
        if not ctx.apply:
            print(f"    [dry-run] would create and publish agent {aid} (space"
                  f"{' + connector' if connector else ''})")
            return
        ctx.qs.create_agent(**common, AgentLifecycle="PUBLISHED", Spaces=[space_arn],
                            ActionConnectors=[connector["Arn"]] if connector else [])
        print(f"    [create] agent {aid} (published; space{' + connector' if connector else ''})")
    else:
        ag = cur["Agent"]
        add_spaces = [] if space_arn in (ag.get("Spaces") or []) else [space_arn]
        add_conn = [connector["Arn"]] if connector and connector["Arn"] not in (ag.get("ActionConnectors") or []) else []
        instr = (ag.get("CustomPromptInterface") or {}).get("CustomInstructions") or ""
        same = (ag.get("Name") == AGENT_NAME and ag.get("Description") == AGENT_DESCRIPTION
                and ag.get("WelcomeMessage") == AGENT_WELCOME and ag.get("StarterPrompts") == AGENT_STARTERS
                and "GR-008" in instr and "Field Sales Agent" in instr)
        if same and not add_spaces and not add_conn:
            print(f"    [ok] agent {aid} up to date")
        elif ctx.apply:
            upd = dict(common)
            if add_spaces:
                upd["SpacesToAdd"] = add_spaces
            if add_conn:
                upd["ActionConnectorsToAdd"] = add_conn
            ctx.qs.update_agent(**upd)
            print(f"    [update] agent {aid}" + (" (connector linked)" if add_conn else ""))
        else:
            print(f"    [dry-run] would update agent {aid}" + (" and link the connector" if add_conn else ""))
    share_all(ctx, "agent", aid, aid, viewers=FIELD_SALES_VIEWERS)


# --- step 6: Flows -----------------------------------------------------------------------------
def step_id(flow: str, title: str) -> str:
    """Deterministic step id, so an update keeps the ids the @-references use.

    Shaped as a version-4 UUID, like the ids the Quick console writes: the console
    rejected @-references to version-5 ids ("Invalid reference in your prompt",
    "Invalid dependencies in <step>") although the API accepted them."""
    seed = uuid.uuid5(uuid.NAMESPACE_URL, f"https://retail-ops/quick-flow/{flow}/{title}")
    return str(uuid.UUID(bytes=seed.bytes, version=4))


# A console step reference: "@" + the referenced step's id (a version-4 UUID),
# then a space or the end of the prompt. Titles ("@Account") are not references.
_UUID4 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


def reference_errors(definition: dict[str, Any]) -> list[str]:
    """What the Quick console would reject in a FlowDefinition: a step id that is
    not a version-4 UUID, or an @-reference that is not the id of an EARLIER step
    of the same Flow (dangling, forward, a title, or glued to the next word).
    Empty when every reference resolves."""
    errors: list[str] = []
    earlier: set[str] = set()
    for st in definition.get("Steps", []):
        sid, title = st.get("StepId", ""), st.get("StepTitle", "")
        if not _UUID4.fullmatch(sid):
            errors.append(f"{title}: step id {sid!r} is not a version-4 UUID")
        cfg = next(iter((st.get("StepConfig") or {}).values()), {}) or {}
        for token in re.findall(r"@(\S*)", cfg.get("prompt", "")):
            if token not in earlier:
                errors.append(f"{title}: @{token} is not the id of an earlier step")
        earlier.add(sid)
    return errors


def flow_definition(account: str, spec: dict[str, Any]) -> dict[str, Any]:
    """The FlowDefinition document for one Flow spec (shape of describe_flow)."""
    ids: dict[str, str] = {}
    agent_arn = f"arn:aws:quicksight:{REGION}:{account}:agent/{rid(AGENT)}"

    def refs(text: str) -> str:
        for title, sid in ids.items():
            text = text.replace("{" + title + "}", f"@{sid} ")
        if "{" in text:
            raise ValueError(f"{spec['name']}: unresolved reference in {text!r}")
        return text.replace("  ", " ").strip()

    steps = []
    for s in spec["steps"]:
        kind, title = s[0], s[1]
        sid = step_id(spec["name"], title)
        if kind == "text":
            st = {"StepType": "TEXT_INPUT", "StepConfig": {"TextInputConfig": {
                "placeholder": s[2], "defaultValue": s[3], "allowOverride": True}}}
        elif kind == "files":
            st = {"StepType": "FILE_UPLOAD", "StepConfig": {"FileInputConfig": {}}}
        elif kind == "kb":
            st = {"StepType": "QUICK_SUITE_DATA", "StepConfig": {"QuickSuiteDataConfig": {
                "prompt": refs(s[2]),
                "dataSources": [{"details": {"arn": f"arn:aws:quicksight:{REGION}:{account}:unstructured-data-set/{kb_id(k)}"},
                                 "type": "UNSTRUCTURED_DATA_SET"} for k in s[3]]}}}
        elif kind == "topic":
            st = {"StepType": "DASHBOARDS_AND_TOPICS", "StepConfig": {"DashboardsAndTopicsConfig": {
                "prompt": refs(s[2]), "scopeType": "topic", "scopeId": rid(TOPIC)}}}
        elif kind == "agent":
            st = {"StepType": "CHAT_AGENT", "StepConfig": {"ChatAgentConfig": {
                "prompt": refs(s[2]),
                "dataSources": [{"details": {"arn": f"arn:aws:quicksight:{REGION}:aws:space/ALL"}, "type": "SPACE"}],
                "agentArn": agent_arn}}}
        else:
            raise ValueError(f"unknown step kind {kind}")
        steps.append({"StepId": sid, "StepType": st["StepType"], "StepTitle": title, "StepConfig": st["StepConfig"]})
        ids[title] = sid
    definition = {"Steps": steps}
    errors = reference_errors(definition)
    if errors:
        raise ValueError(f"{spec['name']}: " + "; ".join(errors))
    return definition


def list_flows(qs, account: str) -> list[dict[str, Any]]:
    out, token = [], None
    while True:
        resp = qs.list_flows(AwsAccountId=account, **({"NextToken": token} if token else {}))
        out += resp.get("FlowSummaryList", [])
        token = resp.get("NextToken")
        if not token:
            return out


def _flow_perms(ctx: Ctx) -> list[dict[str, Any]]:
    return (_owner_perms(ctx, "flow")
            + [{"Principal": group_arn(ctx.account, g), "Actions": list(VIEWER["flow"])} for g in FIELD_SALES_VIEWERS])


def _flow_matches(ctx: Ctx, fid: str, state: str, want: dict[str, Any], description: str) -> bool:
    """True when the DRAFT or PUBLISHED version holds `want` (a version that cannot
    be read counts as different, so the update rewrites it)."""
    try:
        cur = ctx.qs.describe_flow(AwsAccountId=ctx.account, FlowId=fid, PublishState=state)["Flow"]
    except Exception as e:  # noqa: BLE001 - e.g. "appVersion is not found for the stage published"
        print(f"    [flows] {fid} {state}: {type(e).__name__}")
        return False
    return cur.get("FlowDefinition") == want and cur.get("Description") == description


def ensure_flows(ctx: Ctx) -> None:
    print(f"  [flows] {len(FLOWS)} Flows (published on create)")
    by_name: dict[str, list[dict]] = {}
    for f in list_flows(ctx.qs, ctx.account):
        by_name.setdefault(f.get("Name"), []).append(f)
    ids = ctx.cfg.setdefault("flows", {})
    for spec in FLOWS:
        name = spec["name"]
        want = flow_definition(ctx.account, spec)
        found = by_name.get(name) or []
        if len(found) > 1:
            print(f"    [warn] {len(found)} Flows named {name!r}; using {found[0]['FlowId']}")
        if not found:
            if not ctx.apply:
                print(f"    [dry-run] would create Flow {name!r} ({len(want['Steps'])} steps)")
                continue
            r = ctx.qs.create_flow(AwsAccountId=ctx.account, Name=name, Description=spec["description"],
                                   FlowDefinition=want, Permissions=_flow_perms(ctx))
            ids[name] = r["FlowId"]
            print(f"    [create] Flow {name!r} -> {r['FlowId']}")
        else:
            fid = found[0]["FlowId"]
            ids[name] = fid
            if all(_flow_matches(ctx, fid, state, want, spec["description"]) for state in ("DRAFT", "PUBLISHED")):
                print(f"    [ok] Flow {name!r} ({fid}) up to date (draft and published)")
            elif ctx.apply:
                # UpdateFlow replaces the steps of both the DRAFT and the PUBLISHED version.
                ctx.qs.update_flow(AwsAccountId=ctx.account, FlowId=fid, Name=name,
                                   Description=spec["description"], FlowDefinition=want)
                print(f"    [update] Flow {name!r} ({fid}) (draft and published)")
            else:
                print(f"    [dry-run] would update Flow {name!r} ({fid})")
        if name in ids:
            share_owner(ctx, "flow", ids[name], name)
            for g in FIELD_SALES_VIEWERS:
                share(ctx, "flow", ids[name], group_arn(ctx.account, g), VIEWER["flow"], name)


# --- step 7: analysis + dashboard ---------------------------------------------------------------
_SHEET = "campaign-roi-funnel"
_J, _R = "journey_events", "campaign_roi"


def _col(ds: str, name: str) -> dict[str, str]:
    return {"DataSetIdentifier": ds, "ColumnName": name}


def _title(text: str) -> dict[str, Any]:
    return {"Visibility": "VISIBLE", "FormatText": {"PlainText": text}}


def _stage_sort() -> dict[str, Any]:
    return {"CategorySort": [{"ColumnSort": {
        "SortBy": _col(_J, "stage_order"), "Direction": "ASC",
        "AggregationFunction": {"NumericalAggregationFunction": {"SimpleNumericalAggregation": "MIN"}}}}]}


def _category_filter(fid: str, column: str) -> dict[str, Any]:
    return {"FilterGroupId": f"fg-{fid}", "CrossDataset": "ALL_DATASETS", "Status": "ENABLED",
            "ScopeConfiguration": {"SelectedSheets": {"SheetVisualScopingConfigurations": [
                {"SheetId": _SHEET, "Scope": "ALL_VISUALS"}]}},
            "Filters": [{"CategoryFilter": {"FilterId": f"f-{fid}", "Column": _col(_J, column),
                                            "Configuration": {"FilterListConfiguration": {
                                                "MatchOperator": "CONTAINS",
                                                "SelectAllOptions": "FILTER_ALL_VALUES",
                                                "NullOption": "ALL_VALUES"}}}}]}


def dashboard_definition(account: str) -> dict[str, Any]:
    """Funnel by stage, journeys by stage and source, and the campaign ROI table
    (campaign x platform x dealer), with event date, dealer, source and market filters."""
    arn = f"arn:aws:quicksight:{REGION}:{account}:dataset/"

    def num(fid: str, column: str, agg: str | None = "SUM", ds: str = _R) -> dict[str, Any]:
        f: dict[str, Any] = {"FieldId": fid, "Column": _col(ds, column)}
        if agg:
            f["AggregationFunction"] = {"SimpleNumericalAggregation": agg}
        return {"NumericalMeasureField": f}

    def cat(fid: str, column: str, ds: str = _R) -> dict[str, Any]:
        return {"CategoricalDimensionField": {"FieldId": fid, "Column": _col(ds, column)}}

    journeys = {"CategoricalMeasureField": {"FieldId": "{}", "Column": _col(_J, "journey_id"),
                                            "AggregationFunction": "DISTINCT_COUNT"}}

    def distinct(fid: str) -> dict[str, Any]:
        f = json.loads(json.dumps(journeys))
        f["CategoricalMeasureField"]["FieldId"] = fid
        return f

    visuals = [
        {"FunnelChartVisual": {
            "VisualId": "funnel", "Title": _title("Journey funnel: distinct journeys by stage"),
            "ChartConfiguration": {
                "FieldWells": {"FunnelChartAggregatedFieldWells": {
                    "Category": [cat("funnel-stage", "stage", _J)], "Values": [distinct("funnel-journeys")]}},
                "SortConfiguration": _stage_sort(),
                "DataLabelOptions": {"Visibility": "VISIBLE", "MeasureLabelVisibility": "VISIBLE"}},
            "Actions": []}},
        {"BarChartVisual": {
            "VisualId": "by-source", "Title": _title("Journeys by stage and source"),
            "ChartConfiguration": {
                "FieldWells": {"BarChartAggregatedFieldWells": {
                    "Category": [cat("bar-stage", "stage", _J)], "Values": [distinct("bar-journeys")],
                    "Colors": [cat("bar-source", "source", _J)]}},
                "SortConfiguration": _stage_sort(), "Orientation": "VERTICAL", "BarsArrangement": "STACKED"},
            "Actions": []}},
        {"TableVisual": {
            "VisualId": "roi-table",
            "Title": _title("Campaign ROI by campaign, platform and dealer (spend allocated by journey share, an estimate)"),
            "ChartConfiguration": {
                "FieldWells": {"TableAggregatedFieldWells": {
                    "GroupBy": [cat("roi-campaign", "campaign"), cat("roi-source", "source"),
                                cat("roi-dealer", "dealer_id")],
                    "Values": [num("roi-spend", "spend_allocated"), num("roi-journeys", "journeys"),
                               num("roi-sold", "sold"), num("roi-cps", "cost_per_sale", None)]}},
                "FieldOptions": {"SelectedFieldOptions": [
                    {"FieldId": "roi-campaign", "CustomLabel": "Campaign"},
                    {"FieldId": "roi-source", "CustomLabel": "Platform"},
                    {"FieldId": "roi-dealer", "CustomLabel": "Dealer"},
                    {"FieldId": "roi-spend", "CustomLabel": "Spend (allocated)"},
                    {"FieldId": "roi-journeys", "CustomLabel": "Journeys"},
                    {"FieldId": "roi-sold", "CustomLabel": "Sold"},
                    {"FieldId": "roi-cps", "CustomLabel": "Spend per sale"}]}},
            "Actions": []}},
    ]
    filters = [_category_filter("dealer", "dealer_id"), _category_filter("source", "source"),
               _category_filter("market", "market"),
               {"FilterGroupId": "fg-date", "CrossDataset": "SINGLE_DATASET", "Status": "ENABLED",
                "ScopeConfiguration": {"SelectedSheets": {"SheetVisualScopingConfigurations": [
                    {"SheetId": _SHEET, "Scope": "ALL_VISUALS"}]}},
                "Filters": [{"TimeRangeFilter": {"FilterId": "f-date", "Column": _col(_J, "event_ts"),
                                                 "IncludeMinimum": True, "IncludeMaximum": True,
                                                 "NullOption": "ALL_VALUES", "TimeGranularity": "DAY"}}]}]
    controls = [{"DateTimePicker": {"FilterControlId": "c-date", "Title": "Event date", "SourceFilterId": "f-date",
                                    "Type": "DATE_RANGE"}},
                {"Dropdown": {"FilterControlId": "c-dealer", "Title": "Dealer", "SourceFilterId": "f-dealer",
                              "Type": "MULTI_SELECT"}},
                {"Dropdown": {"FilterControlId": "c-source", "Title": "Source", "SourceFilterId": "f-source",
                              "Type": "MULTI_SELECT"}},
                {"Dropdown": {"FilterControlId": "c-market", "Title": "Market", "SourceFilterId": "f-market",
                              "Type": "MULTI_SELECT"}}]
    return {
        "DataSetIdentifierDeclarations": [{"Identifier": _J, "DataSetArn": arn + rid(JOURNEY_DATASET)},
                                          {"Identifier": _R, "DataSetArn": arn + rid(ROI_DATASET)}],
        "CalculatedFields": [{"DataSetIdentifier": _R, "Name": "cost_per_sale",
                              "Expression": "sum({spend_allocated}) / sum({sold})"}],
        "FilterGroups": filters,
        "Sheets": [{
            "SheetId": _SHEET, "Name": "Campaign ROI & Funnel", "ContentType": "INTERACTIVE",
            "Visuals": visuals, "FilterControls": controls,
            "SheetControlLayouts": [{"Configuration": {"GridLayout": {"Elements": [
                {"ElementId": c[next(iter(c))]["FilterControlId"], "ElementType": "FILTER_CONTROL",
                 "ColumnSpan": 2, "RowSpan": 1} for c in controls]}}}],
            "Layouts": [{"Configuration": {"GridLayout": {"Elements": [
                {"ElementId": "funnel", "ElementType": "VISUAL", "ColumnSpan": 18, "RowSpan": 12},
                {"ElementId": "by-source", "ElementType": "VISUAL", "ColumnSpan": 18, "RowSpan": 12},
                {"ElementId": "roi-table", "ElementType": "VISUAL", "ColumnSpan": 36, "RowSpan": 12}]}}}]}],
        "AnalysisDefaults": {"DefaultNewSheetConfiguration": {
            "InteractiveLayoutConfiguration": {"Grid": {"CanvasSizeOptions": {"ScreenCanvasSizeOptions": {
                "ResizeOption": "FIXED", "OptimizedViewPortWidth": "1600px"}}}},
            "SheetContentType": "INTERACTIVE"}},
    }


def _analysis_errors(ctx: Ctx, aid: str) -> list:
    return ctx.qs.describe_analysis(AwsAccountId=ctx.account, AnalysisId=aid)["Analysis"].get("Errors") or []


def ensure_dashboard(ctx: Ctx) -> None:
    aid = did = rid(DASHBOARD)
    print(f"  [dashboard] {DASHBOARD_NAME} (analysis + dashboard {did})")
    definition = dashboard_definition(ctx.account)
    an = _try(ctx.qs.describe_analysis, AwsAccountId=ctx.account, AnalysisId=aid)
    an_status = (an or {}).get("Analysis", {}).get("Status")
    if an is None:
        if not ctx.apply:
            print(f"    [dry-run] would create analysis {aid}")
        else:
            ctx.qs.create_analysis(AwsAccountId=ctx.account, AnalysisId=aid, Name=DASHBOARD_NAME,
                                   Definition=definition, Permissions=[
                                       {"Principal": p["Principal"], "Actions": list(OWNER["analysis"])}
                                       for p in _owner_perms(ctx, "analysis")])
            print(f"    [create] analysis {aid}")
    else:
        if an_status == "DELETED" and ctx.apply:   # still in its recovery window
            ctx.qs.restore_analysis(AwsAccountId=ctx.account, AnalysisId=aid)
            print(f"    [restore] analysis {aid}")
        have = {} if an_status == "DELETED" else ctx.qs.describe_analysis_definition(
            AwsAccountId=ctx.account, AnalysisId=aid).get("Definition", {})
        if _same_definition(have, definition):
            print(f"    [ok] analysis {aid} up to date")
        elif ctx.apply:
            ctx.qs.update_analysis(AwsAccountId=ctx.account, AnalysisId=aid, Name=DASHBOARD_NAME,
                                   Definition=definition)
            print(f"    [update] analysis {aid}")
        else:
            print(f"    [dry-run] would update analysis {aid}")
    if ctx.apply:
        st = _wait(f"analysis {aid}", lambda: ctx.qs.describe_analysis(
            AwsAccountId=ctx.account, AnalysisId=aid)["Analysis"]["Status"],
            ("CREATION_SUCCESSFUL", "UPDATE_SUCCESSFUL"), ("CREATION_FAILED", "UPDATE_FAILED"), 300, 3)
        errs = _analysis_errors(ctx, aid)
        print(f"    [ok] analysis {aid} {st}" + (f"; errors: {errs}" if errs else ""))
    share_owner(ctx, "analysis", aid, aid)        # a dry run prints the planned grants

    publish = {"AdHocFilteringOption": {"AvailabilityStatus": "ENABLED"},
               "ExportToCSVOption": {"AvailabilityStatus": "ENABLED"},
               "SheetControlsOption": {"VisibilityState": "EXPANDED"}}
    dash = _try(ctx.qs.describe_dashboard, AwsAccountId=ctx.account, DashboardId=did)
    if dash is None:
        if not ctx.apply:
            print(f"    [dry-run] would create and publish dashboard {did}, shared with the OEM groups and the embed user")
            return
        ctx.qs.create_dashboard(AwsAccountId=ctx.account, DashboardId=did, Name=DASHBOARD_NAME,
                                Definition=definition, DashboardPublishOptions=publish,
                                VersionDescription="Created by deploy_quick_content",
                                Permissions=[{"Principal": p["Principal"], "Actions": list(OWNER["dashboard"])}
                                             for p in _owner_perms(ctx, "dashboard")])
        print(f"    [create] dashboard {did}")
    else:
        published = dash["Dashboard"]["Version"].get("VersionNumber")
        cur_def = ctx.qs.describe_dashboard_definition(AwsAccountId=ctx.account, DashboardId=did).get("Definition", {})
        if _same_definition(cur_def, definition):
            print(f"    [ok] dashboard {did} up to date (version {published})")
        elif ctx.apply:
            r = ctx.qs.update_dashboard(AwsAccountId=ctx.account, DashboardId=did, Name=DASHBOARD_NAME,
                                        Definition=definition, DashboardPublishOptions=publish,
                                        VersionDescription="Updated by deploy_quick_content")
            version = int(r["VersionArn"].rsplit("/", 1)[-1])
            _wait(f"dashboard {did} v{version}", lambda: ctx.qs.describe_dashboard(
                AwsAccountId=ctx.account, DashboardId=did, VersionNumber=version)["Dashboard"]["Version"]["Status"],
                ("CREATION_SUCCESSFUL", "UPDATE_SUCCESSFUL"), ("CREATION_FAILED", "UPDATE_FAILED"), 300, 3)
            ctx.qs.update_dashboard_published_version(AwsAccountId=ctx.account, DashboardId=did, VersionNumber=version)
            print(f"    [update] dashboard {did}: published version {version}")
        else:
            print(f"    [dry-run] would publish a new version of dashboard {did}")
    if ctx.apply:
        st = _wait(f"dashboard {did}", lambda: ctx.qs.describe_dashboard(
            AwsAccountId=ctx.account, DashboardId=did)["Dashboard"]["Version"]["Status"],
            ("CREATION_SUCCESSFUL", "UPDATE_SUCCESSFUL"), ("CREATION_FAILED", "UPDATE_FAILED"), 300, 3)
        errs = ctx.qs.describe_dashboard(AwsAccountId=ctx.account, DashboardId=did)["Dashboard"]["Version"].get("Errors")
        print(f"    [ok] dashboard {did} {st}" + (f"; errors: {errs}" if errs else ""))
    share_all(ctx, "dashboard", did, did, viewers=OEM_VIEWERS,
              extra_viewers=(ctx.embed_user,) if ctx.embed_user != ctx.caller else ())


def _same_definition(have: dict[str, Any], want: dict[str, Any]) -> bool:
    """Compare the parts this deployer sets (Quick adds defaults to what it returns)."""
    def visual_ids(d: dict) -> list[str]:
        return [next(iter(v.values()))["VisualId"] for s in d.get("Sheets", []) for v in s.get("Visuals", [])]

    def expressions(d: dict) -> list[str]:   # Quick returns sum({x}) as sum(x)
        return sorted(c["Expression"].replace("{", "").replace("}", "") for c in d.get("CalculatedFields", []))
    return (visual_ids(have) == visual_ids(want)
            and sorted(d["DataSetArn"] for d in have.get("DataSetIdentifierDeclarations", []))
            == sorted(d["DataSetArn"] for d in want["DataSetIdentifierDeclarations"])
            and [f["FilterGroupId"] for f in have.get("FilterGroups", [])]
            == [f["FilterGroupId"] for f in want["FilterGroups"]]
            and expressions(have) == expressions(want))


# --- status / delete -----------------------------------------------------------------------
def inventory(qs, account: str, stem: str = STEM) -> list[tuple[str, str, str, dict[str, Any]]]:
    """What exists of this deployer's content, DEPENDENTS FIRST (delete order):
    [(kind, id, label, delete kwargs)]. Flows and the console connector are matched
    by their exact names, and only for the current stem."""
    out: list[tuple[str, str, str, dict[str, Any]]] = []
    a = {"AwsAccountId": account}
    if stem == STEM:
        for f in list_flows(qs, account):
            if f.get("Name") in FLOW_NAMES:
                out.append(("flow", f["FlowId"], f["Name"], {**a, "FlowId": f["FlowId"]}))
    if _try(qs.describe_agent, **a, AgentId=rid(AGENT, stem)):
        out.append(("agent", rid(AGENT, stem), AGENT_NAME, {**a, "AgentId": rid(AGENT, stem)}))
    if _try(qs.describe_space, **a, SpaceId=rid(SPACE, stem)):
        out.append(("space", rid(SPACE, stem), SPACE_NAME, {**a, "SpaceId": rid(SPACE, stem)}))
    if _try(qs.describe_dashboard, **a, DashboardId=rid(DASHBOARD, stem)):
        out.append(("dashboard", rid(DASHBOARD, stem), DASHBOARD_NAME, {**a, "DashboardId": rid(DASHBOARD, stem)}))
    an = _try(qs.describe_analysis, **a, AnalysisId=rid(DASHBOARD, stem))
    if an and an["Analysis"].get("Status") != "DELETED":
        out.append(("analysis", rid(DASHBOARD, stem), DASHBOARD_NAME,
                    {**a, "AnalysisId": rid(DASHBOARD, stem), "ForceDeleteWithoutRecovery": True}))
    if _try(qs.describe_topic_v2, **a, TopicId=rid(TOPIC, stem)):
        out.append(("topic", rid(TOPIC, stem), TOPIC_NAME, {**a, "TopicId": rid(TOPIC, stem)}))
    for kind, name, *_ in KNOWLEDGE_BASES:
        if _try(qs.describe_knowledge_base, **a, KnowledgeBaseId=kb_id(kind, stem)):
            out.append(("knowledge base", kb_id(kind, stem), name, {**a, "KnowledgeBaseId": kb_id(kind, stem)}))
    if _try(qs.describe_data_source, **a, DataSourceId=rid(KB_SOURCE, stem)):
        out.append(("data source", rid(KB_SOURCE, stem), KB_SOURCE_NAME, {**a, "DataSourceId": rid(KB_SOURCE, stem)}))
    if stem == STEM:
        resp = qs.list_action_connectors(**a)
        for c in resp.get("ActionConnectorSummaries", []):
            if c.get("Name") == CONNECTOR_NAME:
                out.append(("connector", c["ActionConnectorId"], CONNECTOR_NAME,
                            {**a, "ActionConnectorId": c["ActionConnectorId"]}))
    for key in DATASETS:
        if _try(qs.describe_data_set, **a, DataSetId=rid(key, stem)):
            out.append(("dataset", rid(key, stem), DATASETS[key][0], {**a, "DataSetId": rid(key, stem)}))
    return out


DELETE_CALL = {"flow": "delete_flow", "agent": "delete_agent", "space": "delete_space",
               "dashboard": "delete_dashboard", "analysis": "delete_analysis", "topic": "delete_topic_v2",
               "knowledge base": "delete_knowledge_base", "data source": "delete_data_source",
               "connector": "delete_action_connector", "dataset": "delete_data_set"}


def status(qs, account: str) -> None:
    items = inventory(qs, account)
    print(f"Quick content for {SPACE_NAME}: {len(items)} resource(s)")
    for kind, rid_, label, _ in items:
        extra = ""
        if kind == "knowledge base":
            kb = qs.describe_knowledge_base(AwsAccountId=account, KnowledgeBaseId=rid_)["KnowledgeBase"]
            extra = f"  {kb.get('Status')} docs={kb.get('DocumentCount')} sync={(kb.get('LatestIngestionSummary') or {}).get('IngestionStatus')}"
        elif kind == "flow":
            extra = "  " + next((f.get("PublishState", "") for f in list_flows(qs, account) if f["FlowId"] == rid_), "")
        print(f"  {kind:15s} {rid_:42s} {label}{extra}")
    have = {(k, i) for k, i, *_ in items}
    for name in FLOW_NAMES:
        if not any(k == "flow" and lbl == name for k, _, lbl, _ in items):
            print(f"  MISSING flow {name}")
    if not any(k == "connector" for k, *_ in have):
        print(f"  MISSING connector {CONNECTOR_NAME} (console step)")


def delete(qs, account: str, apply: bool) -> int:
    items = inventory(qs, account)
    print(f"[{'DELETE' if apply else 'DRY-RUN'}] {len(items)} resource(s), dependents first")
    failed = 0
    for kind, rid_, label, kw in items:
        if not apply:
            print(f"  would delete {kind} {rid_} ({label})")
            continue
        try:
            getattr(qs, DELETE_CALL[kind])(**kw)
            print(f"  deleted {kind} {rid_} ({label})")
        except Exception as e:  # noqa: BLE001 - report and continue
            if _not_found(e):
                print(f"  already gone: {kind} {rid_}")
            else:
                failed += 1
                print(f"  ERROR {kind} {rid_}: {type(e).__name__}: {str(e)[:160]}")
    if apply and CONFIG_PATH.exists() and not failed:
        CONFIG_PATH.unlink()
    return 1 if failed else 0


# --- main ----------------------------------------------------------------------------------
def run(ctx: Ctx, only: set[str] | None = None) -> None:
    want = lambda s: not only or s in only
    if want("datasets"):
        ensure_datasets(ctx)
    if want("topic"):
        ensure_topic(ctx)
    if want("knowledge"):
        ensure_knowledge(ctx)
    connector = connector_step(ctx) if (want("space") or want("agent")) else None
    if want("space"):
        ensure_space(ctx, connector)
    if want("agent"):
        ensure_agent(ctx, connector)
    if want("flows"):
        ensure_flows(ctx)
    if want("dashboard"):
        ensure_dashboard(ctx)
        if want("space"):
            ensure_space(ctx, connector)     # adds the new dashboard to the space
    if ctx.apply:
        save_config({**ctx.cfg, "space_id": rid(SPACE), "agent_id": rid(AGENT), "topic_id": rid(TOPIC),
                     "dashboard_id": rid(DASHBOARD), "analysis_id": rid(DASHBOARD),
                     "knowledge_base_ids": {k: kb_id(k) for k, *_ in KNOWLEDGE_BASES},
                     "dataset_ids": [rid(k) for k in DATASETS], "knowledge_data_source_id": rid(KB_SOURCE)})
        print(f"  [config] wrote app/infra/{CONFIG_PATH.name}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Create the Amazon Quick content of docs/QUICK_BUILD.md through the API")
    ap.add_argument("--apply", action="store_true", help="make the changes (default: dry run)")
    ap.add_argument("--only", help="comma-separated steps: " + ",".join(STEPS))
    ap.add_argument("--status", action="store_true", help="list what exists")
    ap.add_argument("--delete", action="store_true", help="delete it (dry run unless --apply)")
    args = ap.parse_args(argv)
    only = {s.strip() for s in args.only.split(",")} if args.only else None
    if only and only - set(STEPS):
        ap.error(f"unknown step(s): {', '.join(sorted(only - set(STEPS)))}")

    import boto3
    s = boto3.Session(region_name=REGION)
    sts, qs = s.client("sts"), s.client("quicksight")
    account = sts.get_caller_identity()["Account"]
    print(f"account={account} region={REGION}")
    if args.status:
        status(qs, account)
        return 0
    if args.delete:
        return delete(qs, account, args.apply)
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] Amazon Quick content for {SPACE_NAME}"
          + (f" (only {', '.join(sorted(only))})" if only else ""))
    try:
        qs.describe_group(AwsAccountId=account, Namespace=NAMESPACE, GroupName=QUICK_BUILDERS_GROUP)
    except Exception as e:
        if _not_found(e):
            print(f"  Quick group {QUICK_BUILDERS_GROUP} does not exist: run deploy_quick_groups --apply first")
            return 1
        raise
    ctx = Ctx(qs, account, caller_quick_user(qs, sts, account), args.apply,
              dqd.builder_user_arns(qs, account))
    run(ctx, only)
    print(f"[{mode}] done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
