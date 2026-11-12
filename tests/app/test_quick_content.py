# ruff: noqa: S101
"""AWS Quick content through the Quick API (app/infra/deploy_quick_content.py):
dry run, apply, idempotence, sharing (the group and each builder user directly),
the naming rule, no secret handling, the console connector link, the Flow step
references and "OEM facts" prompts, the teardown order, and the embed allowed
domains (platform/quick.py, deploy_l4). boto3 is faked."""
from __future__ import annotations

import copy
import json
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

import boto3
import pytest

from app.infra import deploy_l4, teardown
from app.infra import deploy_quick_content as dqc
from app.infra import deploy_quick_datasets as dqd
from app.services.gateway import oem_read
from app.services.platform import quick

ROOT = Path(__file__).resolve().parents[2]
ACCOUNT = "123456789012"
CALLER = f"arn:aws:quicksight:us-east-1:{ACCOUNT}:user/default/admin"
EMBED = f"arn:aws:quicksight:us-east-1:{ACCOUNT}:user/default/embed"
BUILDERS = f"arn:aws:quicksight:us-east-1:{ACCOUNT}:group/default/retail-ops-builders"
BUILDER = f"arn:aws:quicksight:us-east-1:{ACCOUNT}:user/default/admin/jdoe"   # a console builder


class NotFound(Exception):
    """Named like botocore's ResourceNotFoundException for the deployer's check."""


NotFound.__name__ = "ResourceNotFoundException"

_ID = {"data_set": "DataSetId", "topic_v2": "TopicId", "data_source": "DataSourceId",
       "knowledge_base": "KnowledgeBaseId", "space": "SpaceId", "agent": "AgentId", "analysis": "AnalysisId",
       "dashboard": "DashboardId", "action_connector": "ActionConnectorId", "flow": "FlowId"}
_PERM = {"describe_data_set_permissions": "data_set", "describe_topic_permissions_v2": "topic_v2",
         "describe_data_source_permissions": "data_source", "describe_knowledge_base_permissions": "knowledge_base",
         "describe_space_permissions": "space", "describe_agent_permissions": "agent",
         "describe_analysis_permissions": "analysis", "describe_dashboard_permissions": "dashboard",
         "describe_action_connector_permissions": "action_connector", "get_flow_permissions": "flow"}


class FakeQuick:
    """The quicksight calls deploy_quick_content makes, over an in-memory account."""

    exceptions = type("E", (), {"ResourceNotFoundException": NotFound})

    def __init__(self, connector: bool = False, members: tuple[str, ...] = (), users: tuple[str, ...] = ()) -> None:
        self.res: dict[str, dict[str, dict]] = {k: {} for k in _ID}
        self.perms: dict[tuple[str, str], list[dict]] = {}
        self.space_res: dict[str, list[str]] = {}
        self.calls: list[tuple[str, dict]] = []
        self.flow_seq = 0
        self.members, self.users = list(members), list(users)   # retail-ops-builders members, registered users
        if connector:
            self.res["action_connector"]["c-1"] = {"Name": dqc.CONNECTOR_NAME,
                                                   "Arn": f"arn:aws:quicksight:us-east-1:{ACCOUNT}:action-connector/c-1"}

    # -- bookkeeping --
    def writes(self, prefix: str = "") -> list[tuple[str, dict]]:
        return [(n, kw) for n, kw in self.calls if n.split("_")[0] in {"create", "update", "delete", "restore"}
                and n.startswith(prefix)]

    def _get(self, kind: str, kw: dict) -> dict:
        rid = kw[_ID[kind]]
        if rid not in self.res[kind]:
            raise NotFound(f"{kind} {rid}")
        return self.res[kind][rid]

    def _create(self, kind: str, kw: dict, record: dict) -> None:
        rid = kw[_ID[kind]]
        self.res[kind][rid] = record
        self.perms[(kind, rid)] = copy.deepcopy(kw.get("Permissions") or [{"Principal": CALLER, "Actions": ["x"]}])

    def __getattr__(self, name: str):
        if name in _PERM:
            def describe_perms(**kw):
                kind = _PERM[name]
                self._get(kind, kw)
                return {"Permissions": copy.deepcopy(self.perms.get((kind, kw[_ID[kind]]), []))}
            return describe_perms
        if name.startswith("update_") and name.endswith("_permissions") or name == "update_topic_permissions_v2":
            def update_perms(**kw):
                self.calls.append((name, kw))
                kind = next(k for k in _ID if _ID[k] in kw)
                self.perms.setdefault((kind, kw[_ID[kind]]), []).extend(kw["GrantPermissions"])
                return {}
            return update_perms
        if name.startswith("delete_"):
            def delete(**kw):
                self.calls.append((name, kw))
                kind = next(k for k in _ID if _ID[k] in kw)
                self.res[kind].pop(kw[_ID[kind]], None)
                return {}
            return delete
        raise AttributeError(name)

    def describe_group(self, **kw):
        return {"Group": {}}

    def list_group_memberships(self, **kw):
        return {"GroupMemberList": [{"MemberName": m} for m in self.members]}

    def list_users(self, **kw):
        return {"UserList": [{"UserName": u} for u in self.users]}

    # datasets
    def describe_data_set(self, **kw):
        return {"DataSet": copy.deepcopy(self._get("data_set", kw))}

    def create_data_set(self, **kw):
        self.calls.append(("create_data_set", kw))
        self._create("data_set", kw, {k: kw[k] for k in ("Name", "PhysicalTableMap", "ImportMode")})

    def update_data_set(self, **kw):
        self.calls.append(("update_data_set", kw))

    # topic
    def describe_topic_v2(self, **kw):
        t = self._get("topic_v2", kw)
        return copy.deepcopy(t)

    def create_topic_v2(self, **kw):
        self.calls.append(("create_topic_v2", kw))
        self._create("topic_v2", kw, {"Topic": kw["Topic"], "CustomInstructions": kw["CustomInstructions"]})

    def update_topic_v2(self, **kw):
        self.calls.append(("update_topic_v2", kw))

    # data source
    def describe_data_source(self, **kw):
        self._get("data_source", kw)
        return {"DataSource": {"Status": "CREATION_SUCCESSFUL"}}

    def create_data_source(self, **kw):
        self.calls.append(("create_data_source", kw))
        self._create("data_source", kw, {})

    # knowledge bases
    def describe_knowledge_base(self, **kw):
        kb = copy.deepcopy(self._get("knowledge_base", kw))
        return {"KnowledgeBase": kb}

    def create_knowledge_base(self, **kw):
        self.calls.append(("create_knowledge_base", kw))
        kind = kw["KnowledgeBaseId"].removeprefix("retail-ops-kb-").replace("-", "_")
        docs = {k: d for k, _, d, _ in dqc.KNOWLEDGE_BASES}[kind]
        self._create("knowledge_base", kw, {
            "Name": kw["Name"], "Description": kw["Description"], "Status": "ACTIVE", "DocumentCount": docs,
            "KnowledgeBaseConfiguration": kw["KnowledgeBaseConfiguration"],
            "LatestIngestionSummary": {"IngestionStatus": "COMPLETED"}})

    # connector
    def list_action_connectors(self, **kw):
        return {"ActionConnectorSummaries": [{"ActionConnectorId": k, "Status": "CREATION_SUCCESSFUL", **v}
                                             for k, v in self.res["action_connector"].items()]}

    def describe_action_connector(self, **kw):
        return {"ActionConnector": self._get("action_connector", kw)}

    # space
    def describe_space(self, **kw):
        return {"Space": copy.deepcopy(self._get("space", kw))}

    def create_space(self, **kw):
        self.calls.append(("create_space", kw))
        self._create("space", kw, {"name": kw["Name"], "description": kw["Description"]})

    def list_space_resources(self, **kw):
        return {"SpaceResources": [{"ResourceDetails": {"resourceArn": a}} for a in self.space_res.get(kw["SpaceId"], [])]}

    def update_space_resources(self, **kw):
        self.calls.append(("update_space_resources", kw))
        self.space_res.setdefault(kw["SpaceId"], []).extend(r["ResourceDetails"]["resourceArn"] for r in kw["AddResources"])

    # agent
    def describe_agent(self, **kw):
        return {"Agent": copy.deepcopy(self._get("agent", kw))}

    def create_agent(self, **kw):
        self.calls.append(("create_agent", kw))
        self._create("agent", kw, {
            "Name": kw["Name"], "Description": kw["Description"], "WelcomeMessage": kw["WelcomeMessage"],
            "StarterPrompts": kw["StarterPrompts"], "Spaces": list(kw["Spaces"]),
            "ActionConnectors": list(kw["ActionConnectors"]),
            "CustomPromptInterface": {"CustomInstructions": kw["CustomPromptInput"]["NewPrompt"]["CustomInstructions"]}})

    def update_agent(self, **kw):
        self.calls.append(("update_agent", kw))
        ag = self.res["agent"][kw["AgentId"]]
        ag["Spaces"] += kw.get("SpacesToAdd", [])
        ag["ActionConnectors"] += kw.get("ActionConnectorsToAdd", [])

    # flows
    def list_flows(self, **kw):
        return {"FlowSummaryList": [{"FlowId": k, "Name": v["Name"], "PublishState": "DRAFT"}
                                    for k, v in self.res["flow"].items()]}

    def create_flow(self, **kw):
        self.calls.append(("create_flow", kw))
        self.flow_seq += 1
        fid = f"00000000-0000-4000-8000-{self.flow_seq:012d}"
        self.res["flow"][fid] = {"Name": kw["Name"], "Description": kw["Description"],
                                 "FlowDefinition": copy.deepcopy(kw["FlowDefinition"])}
        self.perms[("flow", fid)] = copy.deepcopy(kw["Permissions"])
        return {"FlowId": fid}

    def describe_flow(self, **kw):
        return {"Flow": copy.deepcopy(self._get("flow", kw))}

    def update_flow(self, **kw):
        self.calls.append(("update_flow", kw))

    # analysis + dashboard
    def describe_analysis(self, **kw):
        self._get("analysis", kw)
        return {"Analysis": {"Status": "CREATION_SUCCESSFUL", "Errors": []}}

    def describe_analysis_definition(self, **kw):
        return {"Definition": copy.deepcopy(self._get("analysis", kw)["Definition"])}

    def create_analysis(self, **kw):
        self.calls.append(("create_analysis", kw))
        self._create("analysis", kw, {"Definition": kw["Definition"]})

    def describe_dashboard(self, **kw):
        self._get("dashboard", kw)
        return {"Dashboard": {"Version": {"VersionNumber": 1, "Status": "CREATION_SUCCESSFUL"}}}

    def describe_dashboard_definition(self, **kw):
        return {"Definition": copy.deepcopy(self._get("dashboard", kw)["Definition"])}

    def create_dashboard(self, **kw):
        self.calls.append(("create_dashboard", kw))
        self._create("dashboard", kw, {"Definition": kw["Definition"]})


def _ctx(qs: FakeQuick, apply: bool) -> dqc.Ctx:
    ctx = dqc.Ctx(qs, ACCOUNT, CALLER, apply)
    ctx.embed_user = EMBED
    return ctx


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.delenv(dqd.BUILDERS_ENV, raising=False)
    monkeypatch.setattr(dqc, "CONFIG_PATH", tmp_path / "quick-content-config.json")
    monkeypatch.setattr(dqc, "load_config", dict)
    monkeypatch.setattr(dqc, "lake_bucket", lambda account: f"retail-ops-dev-event-lake-{account}")
    monkeypatch.setattr(dqc.time, "sleep", lambda _s: None)


# --- dry run / apply / idempotence ----------------------------------------------------------

def test_dry_run_plans_every_resource_and_sends_no_write(capsys):
    qs = FakeQuick()
    dqc.run(_ctx(qs, apply=False))
    out = capsys.readouterr().out
    assert qs.writes() == []
    for needle in ("would create dataset retail-ops-journey-events-sql", "would create and publish topic",
                   "would create knowledge base retail-ops-kb-crm-notes", "would create space retail-ops-space",
                   "would create and publish agent retail-ops-field-sales-agent",
                   "would create Flow 'Retail Ops Account Plan'", "would create analysis retail-ops-campaign-roi-funnel",
                   f"{dqc.CONNECTOR_NAME} not found: create it in the Quick console"):
        assert needle in out, needle


def test_apply_creates_everything_once_and_a_second_run_writes_nothing(capsys):
    qs = FakeQuick()
    dqc.run(_ctx(qs, apply=True))
    made = [n for n, _ in qs.writes("create")]
    assert made.count("create_data_set") == 2 and made.count("create_knowledge_base") == 9
    assert made.count("create_flow") == 8 and made.count("create_topic_v2") == 1
    for one in ("create_data_source", "create_space", "create_agent", "create_analysis", "create_dashboard"):
        assert made.count(one) == 1, one
    (ds,) = [kw for n, kw in qs.calls if n == "create_data_set" and kw["DataSetId"] == "retail-ops-journey-events-sql"]
    assert ds["ImportMode"] == "DIRECT_QUERY"                         # no SPICE
    for _, kw in qs.writes("create_knowledge_base"):
        assert kw["AccessControlConfiguration"] == {"isACLEnabled": False}
        flt = kw["KnowledgeBaseConfiguration"]["templateConfiguration"]["template"]["filterConfiguration"]
        assert flt["exclusionPatterns"] == ["*.metadata.json"] and flt["inclusionPrefixes"][0].startswith("quick-index/")
    capsys.readouterr()
    before = len(qs.writes())
    dqc.run(_ctx(qs, apply=True))
    assert qs.writes()[before:] == []                                 # idempotent: nothing created, updated or shared
    out = capsys.readouterr().out
    assert not re.search(r"\[(create|update|add|publish)\]", out), out
    assert " -> " not in out                                          # no new share


def test_every_resource_is_owned_by_builders_and_viewed_by_the_named_groups():
    qs = FakeQuick()
    dqc.run(_ctx(qs, apply=True))

    def principals(kind: str, rid: str, actions: tuple[str, ...]) -> set[str]:
        return {p["Principal"].rsplit("/", 1)[-1] for p in qs.perms[(kind, rid)] if set(actions) <= set(p["Actions"])}

    assert "retail-ops-builders" in principals("space", "retail-ops-space", dqc.OWNER["space"])
    assert set(dqc.OEM_VIEWERS) <= principals("space", "retail-ops-space", dqc.VIEWER["space"])
    assert "retail-ops-builders" in principals("agent", "retail-ops-field-sales-agent", dqc.OWNER["agent"])
    assert principals("agent", "retail-ops-field-sales-agent", dqc.VIEWER["agent"]) >= set(dqc.FIELD_SALES_VIEWERS)
    assert "retail-ops-oem-marketing" not in principals("agent", "retail-ops-field-sales-agent", dqc.VIEWER["agent"])
    dash = principals("dashboard", "retail-ops-campaign-roi-funnel", dqc.VIEWER["dashboard"])
    assert set(dqc.OEM_VIEWERS) | {"embed", "retail-ops-builders"} <= dash
    for kind, *_ in dqc.KNOWLEDGE_BASES:
        assert "retail-ops-builders" in principals("knowledge_base", dqc.kb_id(kind), dqc.OWNER["knowledge base"])
    for fid in qs.res["flow"]:
        assert "retail-ops-builders" in principals("flow", fid, dqc.OWNER["flow"])
        assert principals("flow", fid, dqc.VIEWER["flow"]) >= set(dqc.FIELD_SALES_VIEWERS)
    for dsid in ("retail-ops-journey-events-sql", "retail-ops-campaign-roi-sql"):   # datasets: builders only
        assert principals("data_set", dsid, ("quicksight:DescribeDataSet",)) == {"admin", "retail-ops-builders"}


def test_space_holds_topic_datasets_kbs_and_dashboard():
    qs = FakeQuick()
    for t in dqc.SPACE_LAKE_DATASETS:                                  # the lake datasets already exist
        qs.res["data_set"][f"retail-ops-lake-{t}"] = {}
    dqc.run(_ctx(qs, apply=True))
    arns = qs.space_res["retail-ops-space"]
    assert len(arns) == len(set(arns))
    kinds = [a.split(":")[5].split("/")[0] for a in arns]
    assert kinds.count("knowledge-base") == 9 and kinds.count("topic") == 1 and kinds.count("dashboard") == 1
    assert kinds.count("dataset") == 2 + len(dqc.SPACE_LAKE_DATASETS)
    assert all(len(kw["AddResources"]) <= 10 for _, kw in qs.writes("update_space_resources"))


def test_a_console_connector_is_shared_added_to_the_space_and_linked_to_the_agent():
    qs = FakeQuick()
    dqc.run(_ctx(qs, apply=True))                                      # first run: no connector yet
    assert qs.res["agent"]["retail-ops-field-sales-agent"]["ActionConnectors"] == []
    qs.res["action_connector"]["c-1"] = {"Name": dqc.CONNECTOR_NAME,
                                         "Arn": f"arn:aws:quicksight:us-east-1:{ACCOUNT}:action-connector/c-1"}
    dqc.run(_ctx(qs, apply=True), {"space", "agent"})                  # re-run after the console step
    (upd,) = [kw for n, kw in qs.calls if n == "update_agent"]
    assert upd["ActionConnectorsToAdd"] == [qs.res["action_connector"]["c-1"]["Arn"]]
    assert qs.res["action_connector"]["c-1"]["Arn"] in qs.space_res["retail-ops-space"]
    assert any(p["Principal"] == BUILDERS for p in qs.perms[("action_connector", "c-1")])
    assert not [n for n, _ in qs.calls if n == "create_action_connector"]   # never created by the API


# --- direct grants to the builder users ------------------------------------------------------

# FakeQuick resource kind -> the deployer's permission kind
_KIND = {"data_set": "dataset", "topic_v2": "topic", "data_source": "data source", "knowledge_base": "knowledge base",
         "space": "space", "agent": "agent", "analysis": "analysis", "dashboard": "dashboard",
         "action_connector": "connector", "flow": "flow"}


def _builders_qs(**kw: Any) -> FakeQuick:
    """An account whose retail-ops-builders group holds the caller and a console builder."""
    return FakeQuick(members=("admin/jdoe", "admin"), users=("admin", "admin/jdoe"), **kw)


def _builder_ctx(qs: FakeQuick, apply: bool) -> dqc.Ctx:
    ctx = dqc.Ctx(qs, ACCOUNT, CALLER, apply, dqd.builder_user_arns(qs, ACCOUNT))
    ctx.embed_user = EMBED
    return ctx


def _held(perms: list[dict], principal: str) -> set[str]:
    return {a for p in perms if p["Principal"] == principal for a in p["Actions"]}


def test_each_builder_user_gets_the_owner_set_on_every_resource_directly():
    qs = _builders_qs(connector=True)
    ctx = _builder_ctx(qs, apply=True)
    assert ctx.builder_users == (CALLER, BUILDER) and ctx.owners == (BUILDERS, CALLER, BUILDER)
    dqc.run(ctx)
    # 2 SQL datasets, topic, S3 source, 9 knowledge bases, space, agent, analysis, dashboard, connector, 8 Flows
    assert len(qs.perms) == 26
    for (kind, rid), perms in qs.perms.items():
        for principal in (BUILDERS, CALLER, BUILDER):
            assert set(dqc.OWNER[_KIND[kind]]) <= _held(perms, principal), (kind, rid, principal)
        users = {p["Principal"] for p in perms if ":user/" in p["Principal"]}
        assert users <= {CALLER, BUILDER, EMBED}                        # viewers stay groups (and the embed user)


def test_builder_grants_are_idempotent_and_never_revoke(capsys):
    qs = _builders_qs(connector=True)
    dqc.run(_builder_ctx(qs, apply=True))
    grants = [kw for n, kw in qs.calls if n.startswith("update_") and "_permissions" in n]
    assert any(p["Principal"] == BUILDER for kw in grants for p in kw["GrantPermissions"])
    assert all("RevokePermissions" not in kw for kw in grants)         # grants only add
    capsys.readouterr()
    before = len(qs.calls)
    dqc.run(_builder_ctx(qs, apply=True))
    assert [n for n, _ in qs.calls[before:] if "permissions" in n and n.startswith("update")] == []
    out = capsys.readouterr().out
    assert "admin/jdoe already has space retail-ops-space" in out and " -> " not in out


def test_a_user_holding_more_than_the_owner_set_is_left_alone():
    qs = _builders_qs()
    fid = "00000000-0000-4000-8000-000000000001"
    qs.res["flow"][fid] = {"Name": "Retail Ops Account Plan"}
    qs.perms[("flow", fid)] = [{"Principal": BUILDER, "Actions": [*dqc.OWNER["flow"], "quicksight:GetFlow"]}]
    assert dqc.share(_builder_ctx(qs, apply=True), "flow", fid, BUILDER, dqc.OWNER["flow"], "x") is False
    assert qs.writes("update_flow_permissions") == []


def test_dry_run_prints_each_planned_builder_grant_and_sends_none(capsys):
    qs = _builders_qs(connector=True)
    dqc.run(_ctx(qs, apply=True))                                      # deployed before: group grants only
    capsys.readouterr()
    before = len(qs.writes())
    dqc.run(_builder_ctx(qs, apply=False))
    out = capsys.readouterr().out
    assert qs.writes()[before:] == []
    assert "direct owner grants to 2 builder user(s): admin, admin/jdoe" in out
    for kind, label in (("space", "retail-ops-space"), ("agent", "retail-ops-field-sales-agent"),
                        ("topic", "retail-ops-journey-funnel"), ("dataset", "retail-ops-campaign-roi-sql"),
                        ("knowledge base", "retail-ops-kb-tsbs"), ("data source", "retail-ops-knowledge-s3"),
                        ("analysis", "retail-ops-campaign-roi-funnel"), ("dashboard", "retail-ops-campaign-roi-funnel"),
                        ("connector", dqc.CONNECTOR_NAME), ("flow", "Retail Ops Account Plan")):
        assert f"would share {kind} {label} with admin/jdoe (" in out, kind
        assert f"retail-ops-builders already has {kind} {label}" in out, kind
    for kind, label in (("space", "retail-ops-space"), ("agent", "retail-ops-field-sales-agent")):
        assert f"would share {kind} {label} with admin (" in out        # the caller is not a creator-owner here


def test_main_grants_the_builders_group_members_directly(monkeypatch, capsys):
    class Session:
        def client(self, service: str, **_kw: Any):
            if service == "sts":
                return type("Sts", (), {"get_caller_identity": staticmethod(
                    lambda: {"Account": ACCOUNT, "Arn": f"arn:aws:iam::{ACCOUNT}:user/admin"})})()
            return qs

    qs = FakeQuick(members=("admin/jdoe",), users=("admin", "admin/jdoe"))
    monkeypatch.setenv(dqd.BUILDERS_ENV, "ghost-builder")
    monkeypatch.setattr(boto3, "Session", lambda **_kw: Session())
    monkeypatch.setattr(dqc, "embed_user_arn", lambda _a: EMBED)
    assert dqc.main(["--apply", "--only", "space,agent"]) == 0
    assert "ghost-builder is not a registered Quick user; no direct grant" in capsys.readouterr().out
    for kind, rid in (("space", "retail-ops-space"), ("agent", "retail-ops-field-sales-agent")):
        assert set(dqc.OWNER[kind]) <= _held(qs.perms[(kind, rid)], BUILDER)
    assert not any("ghost-builder" in p["Principal"] for perms in qs.perms.values() for p in perms)


def test_no_secret_is_read_and_none_reaches_the_output(monkeypatch, capsys):
    asked: list[str] = []

    class Session:
        def client(self, service: str, **_kw: Any):
            asked.append(service)
            if service == "sts":
                return type("Sts", (), {"get_caller_identity": staticmethod(
                    lambda: {"Account": ACCOUNT, "Arn": f"arn:aws:iam::{ACCOUNT}:user/admin"})})()
            return qs

    qs = FakeQuick(connector=True)
    monkeypatch.setattr(boto3, "Session", lambda **_kw: Session())
    monkeypatch.setattr(dqc, "embed_user_arn", lambda _a: EMBED)
    assert dqc.main(["--apply"]) == 0
    out = capsys.readouterr().out
    assert set(asked) == {"sts", "quicksight"}                         # never Secrets Manager
    assert not re.search(r"(?i)client.?secret|password|access.?token", out)
    cfg = json.loads(dqc.CONFIG_PATH.read_text())
    assert cfg["connector_id"] == "c-1" and len(cfg["flows"]) == 8
    assert not re.search(r"(?i)secret|password|token", dqc.CONFIG_PATH.read_text())


# --- naming rule, flow definitions --------------------------------------------------------------

# The retired stems and prefixes, assembled so the repo never spells them out.
_RETIRED = re.compile("|".join((r"\b" + "is" + "so" + r"\b", "IS" + "SO_", "IR" + "PO_", r"\bS" + "SO_")),
                      re.IGNORECASE)
_AGENT_CODE = re.compile(r"\bA[1-7]\b")


def _user_visible_text() -> list[str]:
    texts = [dqc.SPACE_NAME, dqc.SPACE_DESCRIPTION, dqc.TOPIC_NAME, dqc.TOPIC_INSTRUCTIONS, dqc.AGENT_NAME,
             dqc.AGENT_DESCRIPTION, dqc.AGENT_WELCOME, *dqc.AGENT_STARTERS, *dqc.AGENT_PROMPT.values(),
             dqc.DASHBOARD_NAME, dqc.KB_SOURCE_NAME, dqc.CONNECTOR_NAME, *(v[0] for v in dqc.DATASETS.values())]
    for kind, name, _d, used in dqc.KNOWLEDGE_BASES:
        texts += [name, dqc.kb_description(kind, used)]
    for f in dqc.FLOWS:
        texts += [f["name"], f["description"]] + [s[1] for s in f["steps"]] + [s[2] for s in f["steps"] if len(s) > 2]
    texts += json.dumps(dqc.dashboard_definition(ACCOUNT)).split('"')
    return texts


def test_names_follow_the_naming_rule_and_carry_no_agent_codes_or_retired_stems():
    assert dqc.SPACE_NAME == "Automotive Retail Operations"
    names = [dqc.TOPIC_NAME, dqc.AGENT_NAME, dqc.DASHBOARD_NAME, dqc.KB_SOURCE_NAME, dqc.CONNECTOR_NAME,
             *(n for _, n, _, _ in dqc.KNOWLEDGE_BASES), *dqc.FLOW_NAMES, *(v[0] for v in dqc.DATASETS.values())]
    assert all(n.startswith("Retail Ops") for n in names)
    assert all(len(n) <= 50 for n in (dqc.AGENT_NAME,)) and all(len(n) <= 128 for n in dqc.FLOW_NAMES)
    for text in _user_visible_text():
        assert not _RETIRED.search(text), text
        assert not _AGENT_CODE.search(text), text
    ids = [dqc.rid(x) for x in (dqc.SPACE, dqc.AGENT, dqc.TOPIC, dqc.DASHBOARD, dqc.KB_SOURCE, *dqc.DATASETS)]
    assert all(i.startswith("retail-ops-") for i in ids)
    # API patterns: connector name/description, flow description
    assert re.fullmatch(r"[A-Za-z0-9](?:[\w\- ]*[A-Za-z0-9])?", dqc.CONNECTOR_NAME)
    assert all(not re.search(r'[{}"\\<>]', f["description"]) for f in dqc.FLOWS)
    assert len(dqc.TOPIC_INSTRUCTIONS) <= 5000 and len(dqc.AGENT_WELCOME) <= 300
    assert len(dqc.AGENT_STARTERS) <= 3 and all(len(p) <= 100 for p in dqc.AGENT_STARTERS)


def test_flow_definitions_reference_earlier_steps_and_the_repo_resources():
    agent = f"arn:aws:quicksight:us-east-1:{ACCOUNT}:agent/retail-ops-field-sales-agent"
    for spec in dqc.FLOWS:
        steps = dqc.flow_definition(ACCOUNT, spec)["Steps"]
        assert steps[-1]["StepType"] == "CHAT_AGENT"                    # the agent drafts last
        seen: set[str] = set()
        for st in steps:
            cfg = next(iter(st["StepConfig"].values()))
            for ref in re.findall(r"@([0-9a-f-]{36})", cfg.get("prompt", "")):
                assert ref in seen, (spec["name"], st["StepTitle"])     # only earlier steps
            assert "{" not in cfg.get("prompt", "")
            if st["StepType"] == "CHAT_AGENT":
                assert cfg["agentArn"] == agent
            if st["StepType"] == "QUICK_SUITE_DATA":
                assert all(d["details"]["arn"].split("/")[-1].startswith("retail-ops-kb-") for d in cfg["dataSources"])
            if st["StepType"] == "DASHBOARDS_AND_TOPICS":
                assert cfg["scopeId"] == "retail-ops-journey-funnel"
            seen.add(st["StepId"])
        assert dqc.flow_definition(ACCOUNT, spec) == dqc.flow_definition(ACCOUNT, spec)   # deterministic ids
        drafts = [s for s in steps if s["StepType"] == "CHAT_AGENT"][-1]["StepConfig"]["ChatAgentConfig"]["prompt"]
        assert "draft for human review" in drafts and "GR-001 to GR-008" in drafts


# The shape the Quick console writes (legacy console Flows, 2026-10-23): no step key
# beyond these four, and these config keys per step type. Dependencies live only as
# "@<earlier step id>" inside the prompt; resources (agent, knowledge bases, topic)
# sit in their own config fields, never in a prompt.
_CONSOLE_STEP_KEYS = {"StepId", "StepType", "StepTitle", "StepConfig"}
_CONSOLE_CONFIG_KEYS = {"TextInputConfig": {"placeholder", "defaultValue", "allowOverride"},
                        "FileInputConfig": set(),
                        "ChatAgentConfig": {"prompt", "dataSources", "agentArn"},
                        "QuickSuiteDataConfig": {"prompt", "dataSources"},
                        "DashboardsAndTopicsConfig": {"prompt", "scopeType", "scopeId"}}


def test_every_flow_has_the_console_shape_and_each_dependency_is_an_earlier_step_id():
    for spec in dqc.FLOWS:
        definition = dqc.flow_definition(ACCOUNT, spec)
        assert dqc.reference_errors(definition) == [], spec["name"]
        earlier: list[str] = []
        for st in definition["Steps"]:
            assert set(st) == _CONSOLE_STEP_KEYS
            (cfg_name, cfg), = st["StepConfig"].items()
            assert set(cfg) == _CONSOLE_CONFIG_KEYS[cfg_name], (spec["name"], st["StepTitle"])
            assert uuid.UUID(st["StepId"]).version == 4                 # the console's id shape (v5 was rejected)
            prompt = cfg.get("prompt", "")
            deps = re.findall(r"@(\S+)", prompt)
            assert all(d in earlier for d in deps), (spec["name"], st["StepTitle"], deps)
            assert "arn:" not in prompt and prompt.count("@") == len(deps)   # no ARN; every @ is a step id
            if st["StepType"] != "TEXT_INPUT" and st["StepType"] != "FILE_UPLOAD":
                assert deps, (spec["name"], st["StepTitle"])            # every reading step names its inputs
            earlier.append(st["StepId"])
        assert len(set(earlier)) == len(earlier)


@pytest.mark.parametrize(("prompt", "why"), [
    ("Use @{missing}", "not the id of an earlier step"),                  # dangling
    ("Use @{self}", "not the id of an earlier step"),                     # itself (not earlier)
    ("Use @Account ID", "@Account is not the id"),                        # a title, not an id
    ("Use @{first}.", "is not the id of an earlier step"),                # glued to the next character
])
def test_reference_errors_flags_dangling_or_wrongly_encoded_references(prompt, why):
    first, second, missing = (str(uuid.uuid4()) for _ in range(3))
    text = prompt.format(missing=missing, self=second, first=first)
    definition = {"Steps": [
        {"StepId": first, "StepType": "TEXT_INPUT", "StepTitle": "Account ID", "StepConfig": {"TextInputConfig": {}}},
        {"StepId": second, "StepType": "CHAT_AGENT", "StepTitle": "OEM facts",
         "StepConfig": {"ChatAgentConfig": {"prompt": text}}}]}
    errors = dqc.reference_errors(definition)
    assert len(errors) == 1 and why in errors[0] and errors[0].startswith("OEM facts:")
    definition["Steps"][1]["StepConfig"]["ChatAgentConfig"]["prompt"] = f"Use @{first} now"
    assert dqc.reference_errors(definition) == []


def test_reference_errors_flags_a_version_5_step_id():
    v5 = str(uuid.uuid5(uuid.NAMESPACE_URL, "x"))
    definition = {"Steps": [{"StepId": v5, "StepType": "TEXT_INPUT", "StepTitle": "Account ID",
                             "StepConfig": {"TextInputConfig": {}}}]}
    assert dqc.reference_errors(definition) == [f"Account ID: step id {v5!r} is not a version-4 UUID"]
    assert uuid.UUID(dqc.step_id("Retail Ops Account Plan", "Account ID")).version == 4
    assert dqc.step_id("F", "T") == dqc.step_id("F", "T") != dqc.step_id("F", "U")   # deterministic, distinct


# The OEM read tools each Flow's "OEM facts" step needs (and nothing more).
_FACT_TOOLS = {
    "Retail Ops Account Plan": {"oem_get_account", "oem_get_alerts"},
    "Retail Ops Opportunity Brief": {"oem_get_account", "oem_get_incentive_programs"},
    "Retail Ops Win Strategy": {"oem_get_account"},
    "Retail Ops Action Plan": {"oem_get_account", "oem_get_alerts", "oem_list_accounts"},
    "Retail Ops Contact Report": {"oem_get_account"},
    "Retail Ops Meeting Package": {"oem_get_account", "oem_get_alerts"},
    "Retail Ops District Roll-up": {"oem_list_accounts", "oem_get_alerts"},
    "Retail Ops Sales Ops Digest": {"oem_get_incentive_programs"},
}


def test_oem_facts_steps_call_only_the_tools_they_need_with_arguments_in_one_turn():
    assert set(_FACT_TOOLS) == set(dqc.FLOW_NAMES)
    for spec in dqc.FLOWS:
        steps = dqc.flow_definition(ACCOUNT, spec)["Steps"]
        (facts,) = [s for s in steps if s["StepTitle"] == "OEM facts"]
        prompt = facts["StepConfig"]["ChatAgentConfig"]["prompt"]
        named = set(re.findall(r"\boem_[a-z_]+\b", prompt))
        assert named == _FACT_TOOLS[spec["name"]], spec["name"]
        assert named <= set(oem_read.TOOLS)                             # real Gateway tool names
        for tool in named:                                              # explicit arguments
            arg = next(iter(oem_read.TOOLS[tool]["args"]))
            assert re.search(rf"{tool} with {arg} ", prompt), (spec["name"], tool)
        for needle in ("single turn", "Call no other tool", "do not search the knowledge bases",
                       "exactly as the tools returned them", "no commentary"):
            assert needle in prompt, (spec["name"], needle)


def test_a_flow_with_old_step_ids_is_updated_in_place(capsys):
    qs = FakeQuick()
    dqc.run(_ctx(qs, apply=True), {"flows"})
    (fid,) = [f for f, v in qs.res["flow"].items() if v["Name"] == "Retail Ops Account Plan"]
    old = qs.res["flow"][fid]["FlowDefinition"]
    for st in old["Steps"]:                                             # as written before the fix: v5 ids
        st["StepId"] = st["StepId"][:14] + "5" + st["StepId"][15:]
    capsys.readouterr()
    dqc.run(_ctx(qs, apply=True), {"flows"})
    ((_, upd),) = qs.writes("update_flow")
    assert upd["FlowId"] == fid and upd["FlowDefinition"] == dqc.flow_definition(ACCOUNT, dqc.FLOWS[0])
    assert len(qs.writes("create_flow")) == 8                           # nothing recreated
    assert "(draft and published)" in capsys.readouterr().out


# --- delete / teardown ---------------------------------------------------------------------------

_ORDER = ["flow", "agent", "space", "dashboard", "analysis", "topic", "knowledge base", "data source",
          "connector", "dataset"]


def test_inventory_lists_dependents_first_and_only_this_repos_content():
    qs = FakeQuick(connector=True)
    dqc.run(_ctx(qs, apply=True))
    qs.res["flow"]["99999999-0000-4000-8000-000000000000"] = {"Name": "Example Flow"}      # not ours
    qs.res["action_connector"]["other"] = {"Name": "Someone else", "Arn": "x"}
    items = dqc.inventory(qs, ACCOUNT)
    kinds = [k for k, *_ in items]
    assert [k for k in _ORDER if k in kinds] == sorted(set(kinds), key=_ORDER.index)
    assert kinds == sorted(kinds, key=_ORDER.index)
    assert kinds.count("flow") == 8 and kinds.count("knowledge base") == 9 and kinds.count("connector") == 1
    labels = {lbl for _, _, lbl, _ in items}
    assert "Example Flow" not in labels and "Someone else" not in labels
    (an,) = [kw for k, _, _, kw in items if k == "analysis"]
    assert an["ForceDeleteWithoutRecovery"] is True
    assert [k for k, *_ in dqc.inventory(qs, ACCOUNT, stem="other")] == []   # another stem: ids differ, names skipped


def test_delete_is_a_dry_run_unless_apply(capsys):
    qs = FakeQuick()
    dqc.run(_ctx(qs, apply=True))
    n = len(qs.writes("delete"))
    assert dqc.delete(qs, ACCOUNT, apply=False) == 0 and len(qs.writes("delete")) == n
    assert "would delete flow" in capsys.readouterr().out
    assert dqc.delete(qs, ACCOUNT, apply=True) == 0
    deleted = [n for n, _ in qs.writes("delete")]
    assert deleted.index("delete_flow") < deleted.index("delete_agent") < deleted.index("delete_space") \
        < deleted.index("delete_knowledge_base") < deleted.index("delete_data_source") < deleted.index("delete_data_set")
    assert dqc.inventory(qs, ACCOUNT) == []


def test_teardown_runs_quick_content_before_the_lake_datasets_and_deletes_nothing_in_dry_run(monkeypatch, capsys):
    stages = teardown.Teardown.STAGES
    assert stages.index("quick_content") < stages.index("quick") < stages.index("agentcore")
    qs = FakeQuick(connector=True)
    dqc.run(_ctx(qs, apply=True))
    t = teardown.Teardown.__new__(teardown.Teardown)
    t.stem, t.account, t.confirm, t.found, t.deleted, t.errors, t.skipped = "retail-ops", ACCOUNT, False, {}, 0, [], []
    t.c = lambda name, **_kw: qs
    n = len(qs.writes("delete"))
    t.quick_content()
    assert len(qs.writes("delete")) == n                               # dry run
    assert len(t.found["quick-content"]) == 8 + 1 + 1 + 1 + 1 + 1 + 9 + 1 + 1 + 2
    t.confirm = True
    t.quick_content()
    assert t.deleted == len(t.found["quick-content"]) and dqc.inventory(qs, ACCOUNT) == []


def test_deploy_sh_runs_the_content_step_inside_the_quick_stage():
    script = (ROOT / "deploy" / "deploy.sh").read_text()
    stage = script[script.index("if should_run quick; then"):script.index("if should_run console; then")]
    order = [stage.index(m) for m in ("app.infra.deploy_quick_groups", "app.infra.deploy_quick_datasets",
                                      "app.infra.deploy_quick_content", "app.infra.deploy_quick $APPLY_FLAG")]
    assert order == sorted(order)
    help_text = subprocess.run(["bash", str(ROOT / "deploy" / "deploy.sh"), "--help"],  # noqa: S603,S607
                               capture_output=True, text=True, check=True).stdout
    assert "deploy_quick_content" in help_text and "Console checklist" in help_text
    destroy_help = subprocess.run(["bash", str(ROOT / "deploy" / "destroy.sh"), "--help"],  # noqa: S603,S607
                                  capture_output=True, text=True, check=True).stdout
    assert "quick_content" in destroy_help and "Retail Ops OEM Read Gateway" in destroy_help


# --- embed allowed domains ------------------------------------------------------------------------

class FakeEmbedQs:
    def __init__(self) -> None:
        self.kw: dict[str, Any] = {}

    def generate_embed_url_for_registered_user(self, **kw):
        self.kw = kw
        return {"EmbedUrl": "https://example.invalid/embed"}


@pytest.mark.parametrize(("env", "want"), [
    ("https://d1.cloudfront.net", ["https://d1.cloudfront.net"]),
    ("https://a.example/, http://b.example,https://c.example,https://d.example,https://e.example",
     ["https://a.example", "https://c.example", "https://d.example"]),
    ("", None),
])
def test_embed_url_passes_the_configured_allowed_domains(monkeypatch, env, want):
    fake = FakeEmbedQs()
    monkeypatch.setenv(quick.EMBED_DOMAINS_ENV, env)
    monkeypatch.setenv("RETAIL_OPS_QUICK_EMBED_USER_ARN", EMBED)
    monkeypatch.setenv("RETAIL_OPS_ACCOUNT_ID", ACCOUNT)
    monkeypatch.delenv("RETAIL_OPS_QUICK_PER_USER_EMBED", raising=False)
    monkeypatch.setattr(quick.QuickClient, "_qs", lambda self: fake)
    out = quick.QuickClient().embed_url("retail-ops-campaign-roi-funnel")
    assert out["embed_url"] and fake.kw["UserArn"] == EMBED
    assert fake.kw.get("AllowedDomains") == want


def test_l4_sets_the_embed_domain_from_the_console_config(tmp_path, capsys):
    cfg = tmp_path / "console-config.json"
    cfg.write_text(json.dumps({"url": "https://d1234567890abc.cloudfront.net/"}))
    assert deploy_l4.embed_domains_env(None, cfg) == {deploy_l4.EMBED_DOMAINS: "https://d1234567890abc.cloudfront.net"}
    assert deploy_l4.embed_domains_env("https://x.example, https://y.example", cfg) == {
        deploy_l4.EMBED_DOMAINS: "https://x.example,https://y.example"}
    assert deploy_l4.embed_domains_env(None, tmp_path / "missing.json") == {}
    assert "re-run l4 after the console stage" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="https origins"):
        deploy_l4.embed_domains_env("http://insecure.example", cfg)
    assert deploy_l4.EMBED_DOMAINS in deploy_l4._PRESERVE_ENV_KEYS
