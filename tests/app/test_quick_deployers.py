# ruff: noqa: S101
"""AWS Quick deploy wiring: the `quick` stage grants retail-ops-builders and each
builder user owner on the repo's data source and datasets (deploy_quick_datasets), and the `l4`
stage decides ENABLE_QUICK on the service Lambda (deploy_l4). boto3 is faked."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import boto3
import pytest

from app.infra import deploy_l4
from app.infra import deploy_quick_datasets as dqd

ROOT = Path(__file__).resolve().parents[2]
ACCOUNT = "123456789012"
BUILDERS = f"arn:aws:quicksight:us-east-1:{ACCOUNT}:group/default/retail-ops-builders"


class _NotFound(Exception):
    pass


class _Exists(Exception):
    pass


class FakeQuick:
    """The quicksight calls deploy_quick_datasets makes, with recorded writes."""

    exceptions = type("E", (), {"ResourceNotFoundException": _NotFound, "ResourceExistsException": _Exists})

    def __init__(self, *, group: bool = True, ds_perms: list | None = None,
                 set_perms: dict[str, list] | None = None, members: tuple[str, ...] = (),
                 users: tuple[str, ...] = ()) -> None:
        self.group = group
        self.ds_perms = ds_perms            # None = the data source does not exist
        self.set_perms = set_perms or {}    # dataset id -> permissions; absent = no dataset
        self.members, self.users = members, users   # retail-ops-builders members, registered Quick users
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_group_memberships(self, **kw: Any) -> dict:
        if not self.group:
            raise _NotFound(kw["GroupName"])
        return {"GroupMemberList": [{"MemberName": m} for m in self.members]}

    def list_users(self, **kw: Any) -> dict:
        return {"UserList": [{"UserName": u} for u in self.users]}

    def writes(self, name: str) -> list[dict[str, Any]]:
        return [kw for n, kw in self.calls if n == name]

    def describe_group(self, **kw: Any) -> dict:
        self.calls.append(("describe_group", kw))
        if not self.group:
            raise _NotFound(kw["GroupName"])
        return {"Group": {"GroupName": kw["GroupName"]}}

    def describe_data_source_permissions(self, **kw: Any) -> dict:
        if self.ds_perms is None:
            raise _NotFound(kw["DataSourceId"])
        return {"Permissions": self.ds_perms}

    def update_data_source_permissions(self, **kw: Any) -> dict:
        self.calls.append(("update_data_source_permissions", kw))
        self.ds_perms = (self.ds_perms or []) + kw["GrantPermissions"]
        return {}

    def describe_data_set_permissions(self, **kw: Any) -> dict:
        if kw["DataSetId"] not in self.set_perms:
            raise _NotFound(kw["DataSetId"])
        return {"Permissions": self.set_perms[kw["DataSetId"]]}

    def update_data_set_permissions(self, **kw: Any) -> dict:
        self.calls.append(("update_data_set_permissions", kw))
        self.set_perms[kw["DataSetId"]] = self.set_perms.get(kw["DataSetId"], []) + kw["GrantPermissions"]
        return {}

    # --- ensure_data_source / ensure_dataset on --apply (both already exist) ---
    def create_data_source(self, **kw: Any) -> dict:
        raise _Exists(kw["DataSourceId"])

    def update_data_source(self, **kw: Any) -> dict:
        self.calls.append(("update_data_source", kw))
        return {}

    def describe_data_source(self, **kw: Any) -> dict:
        return {"DataSource": {"Status": "UPDATE_SUCCESSFUL"}}

    def create_data_set(self, **kw: Any) -> dict:
        raise _Exists(kw["DataSetId"])

    def describe_data_set(self, **kw: Any) -> dict:
        return {"DataSet": {}}

    def update_data_set(self, **kw: Any) -> dict:
        self.calls.append(("update_data_set", kw))
        return {}


class _Pages:
    def __init__(self, pages: list[dict]) -> None:
        self.pages = pages

    def paginate(self, **_kw: Any) -> list[dict]:
        return self.pages


class FakeGlue:
    def get_paginator(self, _name: str) -> _Pages:
        return _Pages([{"TableList": [{"Name": "journey_events"}, {"Name": "lake_rdr"},
                                      {"Name": "lake_rls_dealer"}]}])

    def get_table(self, **_kw: Any) -> dict:
        return {"Table": {"StorageDescriptor": {"Columns": [{"Name": "event_id"}]},
                          "PartitionKeys": [{"Name": "market"}]}}


class FakeIam:
    exceptions = type("E", (), {"NoSuchEntityException": _NotFound})

    def get_paginator(self, _name: str) -> _Pages:
        return _Pages([{"Roles": [{"RoleName": "aws-quicksight-service-role-v0"}]}])

    def get_role_policy(self, **_kw: Any) -> dict:   # already up to date: no IAM write, no wait
        return {"PolicyDocument": dqd.quick_role_policy(ACCOUNT, dqd._lake_bucket(ACCOUNT))}


class FakeSession:
    def __init__(self, qs: FakeQuick) -> None:
        self.qs = qs

    def client(self, service: str, **_kw: Any) -> Any:
        if service == "sts":
            return type("Sts", (), {"get_caller_identity": staticmethod(lambda: {"Account": ACCOUNT})})()
        return {"quicksight": self.qs, "glue": FakeGlue(), "iam": FakeIam()}[service]


def _run(monkeypatch, qs: FakeQuick, apply: bool) -> None:
    monkeypatch.setattr(boto3, "Session", lambda **_kw: FakeSession(qs))
    monkeypatch.setattr(dqd, "_embed_user_arn", lambda: None)
    dqd.run(apply)


@pytest.fixture(autouse=True)
def _no_builders_env(monkeypatch):
    monkeypatch.delenv(dqd.BUILDERS_ENV, raising=False)


# --- deploy_quick_datasets: builder grants -------------------------------------------------

def test_builders_group_arn_and_the_owner_action_sets():
    assert dqd.builders_group_arn(ACCOUNT) == BUILDERS
    assert len(dqd.DATA_SOURCE_OWNER_ACTIONS) == 6 and "quicksight:UpdateDataSourcePermissions" in dqd.DATA_SOURCE_OWNER_ACTIONS
    assert len(dqd.DATASET_OWNER_ACTIONS) == 10 and "quicksight:PassDataSet" in dqd.DATASET_OWNER_ACTIONS


def test_dry_run_prints_each_grant_and_sends_none(monkeypatch, capsys):
    full = [{"Principal": BUILDERS, "Actions": list(dqd.DATASET_OWNER_ACTIONS)}]
    qs = FakeQuick(ds_perms=[], set_perms={"retail-ops-lake-journey-events": full})
    _run(monkeypatch, qs, apply=False)
    out = capsys.readouterr().out
    assert "[dry-run] would grant retail-ops-builders owner (6 actions) on data source retail-ops-lake" in out
    assert "retail-ops-builders already owns dataset retail-ops-lake-journey-events" in out
    assert "[dry-run] would grant retail-ops-builders owner (10 actions) on dataset retail-ops-lake-rdr once it exists" in out
    assert "lake-rls" not in out                                   # leftover rule tables are never published
    assert qs.writes("update_data_source_permissions") == [] and qs.writes("update_data_set_permissions") == []


def test_apply_grants_owner_to_the_builders_group_only_and_is_idempotent(monkeypatch, capsys):
    reader = {"Principal": f"arn:aws:quicksight:us-east-1:{ACCOUNT}:user/default/admin",
              "Actions": list(dqd.DATASET_OWNER_ACTIONS)}
    partial = {"Principal": BUILDERS, "Actions": ["quicksight:DescribeDataSet"]}
    qs = FakeQuick(ds_perms=[], set_perms={"retail-ops-lake-journey-events": [reader],
                                           "retail-ops-lake-rdr": [partial]})
    _run(monkeypatch, qs, apply=True)
    (ds_grant,) = qs.writes("update_data_source_permissions")
    assert ds_grant == {"AwsAccountId": ACCOUNT, "DataSourceId": "retail-ops-lake",
                        "GrantPermissions": [{"Principal": BUILDERS, "Actions": list(dqd.DATA_SOURCE_OWNER_ACTIONS)}]}
    set_grants = qs.writes("update_data_set_permissions")
    assert [g["DataSetId"] for g in set_grants] == ["retail-ops-lake-journey-events", "retail-ops-lake-rdr"]
    for g in set_grants:
        assert g["GrantPermissions"] == [{"Principal": BUILDERS, "Actions": list(dqd.DATASET_OWNER_ACTIONS)}]
    principals = {p["Principal"] for g in [ds_grant, *set_grants] for p in g["GrantPermissions"]}
    assert principals == {BUILDERS}                                # never a retail-ops-oem-* group
    capsys.readouterr()
    _run(monkeypatch, qs, apply=True)                              # second run: nothing to grant
    assert len(qs.writes("update_data_source_permissions")) == 1 and len(qs.writes("update_data_set_permissions")) == 2
    assert capsys.readouterr().out.count("retail-ops-builders already owns") == 3


def test_a_missing_builders_group_skips_the_grants_without_failing(monkeypatch, capsys):
    qs = FakeQuick(group=False, ds_perms=[], set_perms={})
    _run(monkeypatch, qs, apply=True)
    out = capsys.readouterr().out
    assert "Quick group retail-ops-builders does not exist; skipping the builder grants" in out
    assert len(qs.writes("update_data_set")) == 2                  # the datasets are still ensured
    assert qs.writes("update_data_source_permissions") == [] and qs.writes("update_data_set_permissions") == []


ADMIN = f"arn:aws:quicksight:us-east-1:{ACCOUNT}:user/default/admin"
BUILDER = f"arn:aws:quicksight:us-east-1:{ACCOUNT}:user/default/admin/jdoe"


def test_builder_users_are_the_group_members_plus_the_listed_names_registered_only(monkeypatch, capsys):
    monkeypatch.setenv(dqd.BUILDERS_ENV, " admin/jdoe, ghost ,,admin")
    qs = FakeQuick(members=("admin", "departed"), users=("admin", "admin/jdoe", "reader"))
    assert dqd.builder_user_arns(qs, ACCOUNT) == (ADMIN, BUILDER)       # de-duplicated, sorted, registered only
    out = capsys.readouterr().out
    assert "ghost is not a registered Quick user; no direct grant" in out
    assert "departed is not a registered Quick user; no direct grant" in out
    assert "direct owner grants to 2 builder user(s): admin, admin/jdoe" in out
    assert dqd.principal_name(BUILDER) == "admin/jdoe" and dqd.principal_name(BUILDERS) == "retail-ops-builders"


def test_apply_grants_each_builder_user_directly_and_is_idempotent(monkeypatch, capsys):
    group_ds = {"Principal": BUILDERS, "Actions": list(dqd.DATA_SOURCE_OWNER_ACTIONS)}
    group_set = {"Principal": BUILDERS, "Actions": list(dqd.DATASET_OWNER_ACTIONS)}
    hand_grant = {"Principal": BUILDER, "Actions": list(dqd.DATASET_OWNER_ACTIONS)}   # granted by hand earlier
    qs = FakeQuick(ds_perms=[group_ds], set_perms={"retail-ops-lake-journey-events": [group_set, hand_grant],
                                                   "retail-ops-lake-rdr": [group_set]},
                   members=("admin/jdoe", "admin"), users=("admin", "admin/jdoe"))
    _run(monkeypatch, qs, apply=True)
    (ds_grant, ds_grant2) = qs.writes("update_data_source_permissions")
    assert [g["GrantPermissions"][0]["Principal"] for g in (ds_grant, ds_grant2)] == [ADMIN, BUILDER]
    assert ds_grant["GrantPermissions"][0]["Actions"] == list(dqd.DATA_SOURCE_OWNER_ACTIONS)
    set_grants = [(g["DataSetId"], g["GrantPermissions"][0]["Principal"]) for g in qs.writes("update_data_set_permissions")]
    assert set_grants == [("retail-ops-lake-journey-events", ADMIN), ("retail-ops-lake-rdr", ADMIN),
                          ("retail-ops-lake-rdr", BUILDER)]                 # the hand grant is not repeated
    every = qs.writes("update_data_source_permissions") + qs.writes("update_data_set_permissions")
    assert all("RevokePermissions" not in g for g in every)               # grants only add
    capsys.readouterr()
    _run(monkeypatch, qs, apply=True)                                     # second run: nothing to grant
    assert len(qs.writes("update_data_source_permissions")) == 2 and len(qs.writes("update_data_set_permissions")) == 3
    out = capsys.readouterr().out
    assert out.count("admin/jdoe already owns") == 3 and out.count("[grant] admin already owns") == 3


def test_dry_run_prints_each_builder_user_grant(monkeypatch, capsys):
    qs = FakeQuick(ds_perms=[], set_perms={}, members=("admin/jdoe",), users=("admin/jdoe",))
    _run(monkeypatch, qs, apply=False)
    out = capsys.readouterr().out
    assert "[dry-run] would grant admin/jdoe owner (6 actions) on data source retail-ops-lake" in out
    assert "[dry-run] would grant admin/jdoe owner (10 actions) on dataset retail-ops-lake-rdr once it exists" in out
    assert qs.writes("update_data_source_permissions") == [] and qs.writes("update_data_set_permissions") == []


def test_deploy_sh_creates_the_quick_groups_before_the_datasets():
    script = (ROOT / "deploy" / "deploy.sh").read_text()
    stage = script[script.index("if should_run quick; then"):script.index("if should_run console; then")]
    assert stage.index("app.infra.deploy_quick_groups") < stage.index("app.infra.deploy_quick_datasets") \
        < stage.index("app.infra.deploy_quick $APPLY_FLAG")
    subprocess.run(["bash", "-n", str(ROOT / "deploy" / "deploy.sh")], check=True)  # noqa: S603,S607


# --- deploy_l4: ENABLE_QUICK on the service Lambda -----------------------------------------

class FakeSubscription:
    exceptions = type("E", (), {"ResourceNotFoundException": _NotFound})

    def __init__(self, result: dict | Exception) -> None:
        self.result = result
        self.calls = 0

    def describe_account_subscription(self, AwsAccountId: str) -> dict:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return {"AccountInfo": self.result}


@pytest.mark.parametrize(("result", "value"), [
    ({"Edition": "ENTERPRISE", "AccountSubscriptionStatus": "ACCOUNT_CREATED"}, "true"),
    ({"Edition": "ENTERPRISE", "AccountSubscriptionStatus": "UNSUBSCRIBED"}, "false"),
    (_NotFound("no subscription"), "false"),
    (RuntimeError("AccessDenied"), None),
])
def test_quick_flag_follows_the_account_subscription(result, value):
    qs = FakeSubscription(result)
    got, why = deploy_l4.quick_flag(qs, ACCOUNT)
    assert got == value and qs.calls == 1 and why


@pytest.mark.parametrize(("override", "value"), [("true", "true"), (" FALSE ", "false"), ("True", "true")])
def test_the_deploy_environment_override_wins_without_a_lookup(override, value):
    qs = FakeSubscription({"AccountSubscriptionStatus": "ACCOUNT_CREATED"})
    assert deploy_l4.quick_flag(qs, ACCOUNT, override) == (value, "deploy environment override")
    assert qs.calls == 0


def test_a_bad_override_stops_the_deploy():
    with pytest.raises(SystemExit, match="use true or false"):
        deploy_l4.quick_flag(FakeSubscription({}), ACCOUNT, "yes")


def test_quick_env_sets_or_keeps_the_flag_through_the_redeploy_merge(capsys):
    current = {"ENABLE_QUICK": "true", "RETAIL_OPS_QUICK_EMBED_USER_ARN": "arn:user"}
    on = deploy_l4.quick_env(FakeSubscription({"AccountSubscriptionStatus": "ACCOUNT_CREATED"}), ACCOUNT)
    assert on == {"ENABLE_QUICK": "true"}
    off = deploy_l4.quick_env(FakeSubscription({}), ACCOUNT, "false")
    assert deploy_l4.merge_preserved(current, {"A": "1", **off}, deploy_l4._PRESERVE_ENV_KEYS)["ENABLE_QUICK"] == "false"
    undecided = deploy_l4.quick_env(FakeSubscription(RuntimeError("x")), ACCOUNT)
    assert undecided == {}
    assert deploy_l4.merge_preserved(current, {"A": "1"}, deploy_l4._PRESERVE_ENV_KEYS)["ENABLE_QUICK"] == "true"
    out = capsys.readouterr().out
    assert f"[quick] ENABLE_QUICK=true on {deploy_l4.SVC_FN}" in out and "ENABLE_QUICK unchanged" in out
    assert "RETAIL_OPS_QUICK_PER_USER_EMBED" not in on | off       # per-user embed is never set here


def test_config_example_documents_the_quick_override_commented_out():
    text = (ROOT / "deploy" / "config.env.example").read_text()
    assert re.search(r"^# export ENABLE_QUICK=", text, re.MULTILINE)
    assert not re.search(r"^export (ENABLE_QUICK|RETAIL_OPS_QUICK_PER_USER_EMBED)", text, re.MULTILINE)
