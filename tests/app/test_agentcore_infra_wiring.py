# ruff: noqa: S101
"""Wiring the per-agent runtimes into the Lambdas, roles, alarms and teardown
(ADR 0009, design §10.3-§10.8), through the deployers' pure seams and fakes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import boto3
import pytest

from app.agentcore import registry
from app.infra import deploy_alarms, deploy_l4, deploy_l6, iam_roles, naming, teardown

ACCOUNT = "123456789012"
NAMES = registry.runtime_names()


def _arn(name: str, suffix: str = "AbCdEf1234") -> str:
    return f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:runtime/{name}-{suffix}"


ARNS = {a.code: _arn(a.runtime_name) for a in registry.AGENTS.values()}


def _v2(tmp_path: Path, agents: dict[str, Any], **extra: Any) -> Path:
    path = tmp_path / "agentcore-config.json"
    path.write_text(json.dumps({"schema_version": 2, "region": "us-east-1", "env": "dev", "agents": agents, **extra}))
    return path


# --- deploy_l4: the runtime map from agentcore-config.json ----------------------------------

def test_runtime_env_from_config_missing_v1_and_v2(tmp_path):
    assert deploy_l4.runtime_env_from_config(tmp_path / "absent.json") == {}
    v1 = tmp_path / "v1.json"
    v1.write_text(json.dumps({"agent_runtime_name": "retail_ops_agent", "agent_runtime_arn": _arn("retail_ops_agent"),
                              "region": "us-east-1", "agents": ["A3"]}))
    assert deploy_l4.runtime_env_from_config(v1) == {
        "RETAIL_OPS_AGENT_RUNTIMES": json.dumps({"A3": _arn("retail_ops_agent")}, separators=(",", ":"))}
    path = _v2(tmp_path, {c: {"runtime_arn": ARNS[c]} for c in reversed(list(registry.AGENTS))},
               legacy={"agent_runtime_name": "retail_ops_agent", "agent_runtime_arn": _arn("retail_ops_agent")})
    raw = deploy_l4.runtime_env_from_config(path)["RETAIL_OPS_AGENT_RUNTIMES"]
    assert " " not in raw and json.loads(raw) == ARNS and list(json.loads(raw)) == list(registry.AGENTS)
    assert deploy_l4.runtime_env_from_config(_v2(tmp_path, {})) == {}


@pytest.mark.parametrize(("body", "field"), [
    ("{not json", "(file)"),
    (json.dumps([1, 2]), "(file)"),
    (json.dumps({"schema_version": 3, "agents": {}}), "schema_version"),
    (json.dumps({"schema_version": "2", "agents": {}}), "schema_version"),
    (json.dumps({"schema_version": 2, "agents": {"A9": {"runtime_arn": _arn("retail_ops_dev_a9_x")}}}), "agents.A9"),
    (json.dumps({"schema_version": 2, "agents": {"A6": {"runtime_arn": _arn("retail_ops_dev_a6_x")}}}), "agents.A6"),
    (json.dumps({"schema_version": 2, "agents": {"A1": {"runtime_arn": "arn:aws:lambda:us-east-1:1:function:x"}}}),
     "agents.A1.runtime_arn"),
    (json.dumps({"schema_version": 2, "agents": {"A1": {"runtime_arn": _arn("retail_ops_dev_a1_discovery", "short")}}}),
     "agents.A1.runtime_arn"),
    (json.dumps({"schema_version": 2, "agents": {"A1": {}}}), "agents.A1.runtime_arn"),
    (json.dumps({"agent_runtime_arn": "nope"}), "agent_runtime_arn"),
])
def test_a_malformed_config_raises_config_error_naming_the_field(tmp_path, body, field):
    path = tmp_path / "agentcore-config.json"
    path.write_text(body)
    with pytest.raises(deploy_l4.ConfigError) as e:
        deploy_l4.runtime_env_from_config(path)
    assert e.value.field == field and field in str(e.value)
    with pytest.raises(SystemExit, match="agentcore-config.json"):
        deploy_l4.runtime_env_or_exit(path)


def test_merge_preserved_keeps_the_runtime_map_and_the_disable_switch():
    current = {"RETAIL_OPS_AGENT_RUNTIMES": '{"A2":"x"}', "RETAIL_OPS_AGENT_RUNTIME_DISABLE": "A5",
               "RETAIL_OPS_AGENT_RUNTIME_ARN": "old", "RETAIL_OPS_EVENT_BUS": "app-bus-dev"}
    assert {"RETAIL_OPS_AGENT_RUNTIMES", "RETAIL_OPS_AGENT_RUNTIME_DISABLE"} <= set(deploy_l4._PRESERVE_ENV_KEYS)
    merged = deploy_l4.merge_preserved(current, {"A": "1"}, deploy_l4._PRESERVE_ENV_KEYS)
    assert merged == {"A": "1", "RETAIL_OPS_AGENT_RUNTIMES": '{"A2":"x"}', "RETAIL_OPS_AGENT_RUNTIME_DISABLE": "A5",
                      "RETAIL_OPS_EVENT_BUS": "app-bus-dev"}          # the retired single-ARN key is not kept
    fresh = deploy_l4.merge_preserved(current, {"RETAIL_OPS_AGENT_RUNTIMES": "{}"}, deploy_l4._PRESERVE_ENV_KEYS)
    assert fresh["RETAIL_OPS_AGENT_RUNTIMES"] == "{}"                  # a new map wins over the current one


# --- the disable switch --------------------------------------------------------------------

@pytest.mark.parametrize(("current", "disable", "enable", "after"), [
    (None, ["A2"], None, "A2"),
    ("A2", ["A5"], None, "A2,A5"),
    ("A5,A2", ["A2"], None, "A2,A5"),
    ("A2,A5", None, ["A2"], "A5"),
    ("A5", None, ["A5"], None),
    ("A2", "all", None, "1"),
    ("1", None, ["A2"], "A1,A3,A4,A5,A7"),
    ("A2,A5", None, "all", None),
    (None, ["A1", "A2", "A3", "A4", "A5", "A7"], None, "1"),
])
def test_toggle_disable(current, disable, enable, after):
    assert deploy_l4.toggle_disable(current, disable, enable) == after


class FakeLambda:
    """get_function_configuration / update_function_configuration / get_function, recorded."""

    def __init__(self, envs: dict[str, dict[str, str]]) -> None:
        self.envs = envs
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get_function_configuration(self, FunctionName: str) -> dict:
        self.calls.append(("get_function_configuration", {"FunctionName": FunctionName}))
        return {"Environment": {"Variables": dict(self.envs[FunctionName])}}

    def get_function(self, FunctionName: str) -> dict:
        return {"Configuration": {"LastUpdateStatus": "Successful", "FunctionArn": f"arn:fn:{FunctionName}",
                                  "Environment": {"Variables": dict(self.envs[FunctionName])}}}

    def update_function_configuration(self, **kw: Any) -> dict:
        self.calls.append(("update_function_configuration", kw))
        if "Environment" in kw:
            self.envs[kw["FunctionName"]] = dict(kw["Environment"]["Variables"])
        return {}

    def update_function_code(self, **kw: Any) -> dict:
        self.calls.append(("update_function_code", kw))
        return {}

    def updates(self) -> list[dict[str, Any]]:
        return [kw for name, kw in self.calls if name == "update_function_configuration"]


_SERVICE_ENV = {"RETAIL_OPS_GUARDRAIL_ID": "gr-1", "RETAIL_OPS_KNOWLEDGE_BASE_ID": "kb-1",
                "RETAIL_OPS_AGENT_RUNTIMES": json.dumps(ARNS), "INTEGRATION_MODE": "aws"}


def test_disable_agents_changes_only_that_key_and_writes_every_other_variable_back():
    lam = FakeLambda({deploy_l4.SVC_FN: dict(_SERVICE_ENV)})
    assert deploy_l4.set_disable(lam, deploy_l4.SVC_FN, disable=["A2"], apply=True) == (None, "A2")
    (update,) = lam.updates()
    assert update == {"FunctionName": deploy_l4.SVC_FN,
                      "Environment": {"Variables": {**_SERVICE_ENV, "RETAIL_OPS_AGENT_RUNTIME_DISABLE": "A2"}}}
    assert deploy_l4.set_disable(lam, deploy_l4.SVC_FN, enable="all", apply=True) == ("A2", None)
    assert lam.updates()[-1]["Environment"]["Variables"] == _SERVICE_ENV       # the key is removed


def test_the_disable_dry_run_and_a_no_change_make_no_update(capsys):
    lam = FakeLambda({deploy_l4.SVC_FN: dict(_SERVICE_ENV)})
    assert deploy_l4.set_disable(lam, deploy_l4.SVC_FN, disable="all", apply=False) == (None, "1")
    assert deploy_l4.set_disable(lam, deploy_l4.SVC_FN, enable=["A3"], apply=True) == (None, None)
    assert lam.updates() == []
    out = capsys.readouterr().out
    assert "(unset) -> 1 (would set)" in out and "(unchanged)" in out


class _FakeSession:
    def __init__(self, lam: FakeLambda) -> None:
        self.lam = lam

    def client(self, service: str, **_kw: Any) -> Any:
        if service == "sts":
            return type("Sts", (), {"get_caller_identity": staticmethod(lambda: {"Account": ACCOUNT})})()
        if service == "lambda":
            return self.lam
        return object()


def test_l4_cli_toggles_the_service_lambda_and_prints_the_a2_note(monkeypatch, capsys):
    lam = FakeLambda({deploy_l4.SVC_FN: dict(_SERVICE_ENV)})
    monkeypatch.setattr(boto3, "Session", lambda **_kw: _FakeSession(lam))
    assert deploy_l4.main(["--disable-agents", "a1,A3"]) == 0
    out = capsys.readouterr().out
    assert lam.updates() == []                                                   # dry run
    assert "the Journey Agent runtime still calls the Discovery Agent, Sales Agent" in out
    assert deploy_l4.main(["--disable-agents", "A1,A2", "--apply"]) == 0
    assert "still calls" not in capsys.readouterr().out                          # A2 is disabled too
    assert lam.envs[deploy_l4.SVC_FN]["RETAIL_OPS_AGENT_RUNTIME_DISABLE"] == "A1,A2"
    assert deploy_l4.main(["--enable-agents", "all", "--apply"]) == 0
    assert "RETAIL_OPS_AGENT_RUNTIME_DISABLE" not in lam.envs[deploy_l4.SVC_FN]
    for bad in (["--disable-agents", "A6"], ["--enable-agents", "B2"], ["--disable-agents", "A1", "--enable-agents", "A2"]):
        with pytest.raises(SystemExit) as e:
            deploy_l4.main(bad)
        assert e.value.code == 2


# --- deploy_l6: job Lambdas ---------------------------------------------------------------

def test_job_env_gives_each_agent_owned_job_its_owner_only():
    runtime_env = {"RETAIL_OPS_AGENT_RUNTIMES": json.dumps(ARNS)}
    base = {"RETAIL_OPS_ENV_PREFIX": "retail-ops-dev-", "INTEGRATION_MODE": "aws"}
    assert set(registry.job_names()) <= set(deploy_l6.JOB_FN)
    for job in deploy_l6.JOB_FN:
        env = deploy_l6.job_env(base, runtime_env, job)
        # only the store digest, which exists to write with Bedrock, runs in aws mode
        assert env.get("INTEGRATION_MODE") == ("aws" if job == "store-digest" else None), job
        assert env["RETAIL_OPS_ENV_PREFIX"] == "retail-ops-dev-"
        owner = registry.job_owner(job)
        if owner:
            assert json.loads(env["RETAIL_OPS_AGENT_RUNTIMES"]) == {owner: ARNS[owner]}, job
        else:
            assert job in ("district-cycle", "store-digest", "autonomy-promotion") \
                and "RETAIL_OPS_AGENT_RUNTIMES" not in env
    assert deploy_l6.MODEL_JOBS == {"store-digest"} and registry.job_owner("store-digest") is None
    assert "RETAIL_OPS_AGENT_RUNTIMES" not in deploy_l6.job_env(base, {}, "equity-monitor")
    partial = {"RETAIL_OPS_AGENT_RUNTIMES": json.dumps({"A4": ARNS["A4"]})}
    assert deploy_l6.job_env(base, partial, "shop-load")["RETAIL_OPS_AGENT_RUNTIMES"] == "{}"


def test_agent_job_functions():
    assert sorted(deploy_l6.agent_job_functions("all")) == sorted(deploy_l6.JOB_FN[j] for j in registry.job_names())
    assert len(deploy_l6.agent_job_functions("all")) == 9
    assert deploy_l6.agent_job_functions(["A7"]) == ["retail-ops-dev-shop-load", "retail-ops-dev-service-followup-tick",
                                                     "retail-ops-dev-service-lane-monitor",
                                                     "retail-ops-dev-predictive-scan"]
    assert deploy_l6.agent_job_functions(["A3"]) == []


def test_l6_cli_toggles_only_the_named_agents_job_lambdas(monkeypatch, capsys):
    lam = FakeLambda({fn: {"RETAIL_OPS_ENV_PREFIX": "retail-ops-dev-"} for fn in deploy_l6.JOB_FN.values()})
    monkeypatch.setattr(boto3, "Session", lambda **_kw: _FakeSession(lam))
    assert deploy_l6.main(["--disable-agents", "A4", "--apply"]) == 0
    assert [u["FunctionName"] for u in lam.updates()] == ["retail-ops-dev-equity-monitor"]
    assert lam.envs["retail-ops-dev-equity-monitor"]["RETAIL_OPS_AGENT_RUNTIME_DISABLE"] == "A4"
    assert "still calls" not in capsys.readouterr().out
    assert deploy_l6.main(["--disable-agents", "A3", "--apply"]) == 0
    assert "own no scheduled job" in capsys.readouterr().out and len(lam.updates()) == 1
    assert deploy_l6.main(["--enable-agents", "all"]) == 0                      # dry run
    assert len(lam.updates()) == 1
    with pytest.raises(SystemExit):
        deploy_l6.main(["--disable-agents", "A6"])


def test_ensure_job_fn_preserves_the_runtime_map_and_the_switch_on_redeploy():
    fn = deploy_l6.JOB_FN["equity-monitor"]
    current = {"RETAIL_OPS_AGENT_RUNTIMES": json.dumps({"A4": ARNS["A4"]}), "RETAIL_OPS_AGENT_RUNTIME_DISABLE": "A4",
               "STALE": "x"}

    class Lam(FakeLambda):
        exceptions = type("E", (), {"ResourceNotFoundException": KeyError})

    lam = Lam({fn: current})
    deploy_l6.ensure_job_fn(lam, fn, "arn:role", b"zip", True, {"RETAIL_OPS_ENV_PREFIX": "retail-ops-dev-"})
    (update,) = lam.updates()
    assert update["Environment"]["Variables"] == {"RETAIL_OPS_ENV_PREFIX": "retail-ops-dev-",
                                                  "RETAIL_OPS_AGENT_RUNTIMES": current["RETAIL_OPS_AGENT_RUNTIMES"],
                                                  "RETAIL_OPS_AGENT_RUNTIME_DISABLE": "A4"}


# --- IAM -----------------------------------------------------------------------------------

def test_worker_role_may_invoke_only_the_per_agent_runtimes():
    stmts = [s for s in iam_roles.worker_policy(ACCOUNT)["Statement"]
             if "bedrock-agentcore:InvokeAgentRuntime" in s["Action"]]
    assert stmts == [{"Sid": "AgentRuntime", "Effect": "Allow", "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
                      "Resource": [f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:runtime/retail_ops_dev_*"]}]


def test_service_role_policy_is_byte_identical_to_main():
    policy = iam_roles.service_policy(ACCOUNT)
    # the one addition since main 59d6e68: on dev, start the demo-reset Lambda (that function only)
    added = [s for s in policy["Statement"] if s.get("Sid") == "StartDemoReset"]
    assert added == ([{"Sid": "StartDemoReset", "Effect": "Allow", "Action": ["lambda:InvokeFunction"],
                       "Resource": f"arn:aws:lambda:us-east-1:{ACCOUNT}:function:retail-ops-dev-demo-reset"}]
                     if naming.ENV == "dev" else [])
    rest = {**policy, "Statement": [s for s in policy["Statement"] if s.get("Sid") != "StartDemoReset"]}
    doc = json.dumps(rest)
    # sha256 of json.dumps(service_policy("123456789012")) on main 59d6e68
    assert hashlib.sha256(doc.encode()).hexdigest() == "71036356a95b80f7e7163a347179b6a4118d20d27f5e1dcd779a4601cb8e0bb1"
    assert iam_roles._agent_runtime_stmt(ACCOUNT)["Resource"] == \
        [f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:runtime/*"]


# --- teardown: exact runtime names only (P4) -------------------------------------------------

def test_select_runtime_names_returns_exactly_the_seven_retail_ops_runtimes():
    seven = ["retail_ops_agent", *NAMES]
    listed = ["vehicle_maintenance_agent", "tire_ordering", "appointment_scheduler",
              "retail_ops_agent_v2", "retail_ops_dev_a1_discovery_old", "retail_ops_dev_a9_x", "retail_ops",
              *seven, "retail_ops_dev_a2_journey"]
    assert teardown.select_runtime_names(listed, "retail-ops", "dev") == sorted(seven)
    assert teardown.select_runtime_names(listed, "retail-ops", "prod") == ["retail_ops_agent"]
    assert teardown.runtime_names_for("retail-ops", "dev") == seven


# --- alarms: runtime_job_unknown (review M3) -------------------------------------------------

def test_runtime_unknown_specs_cover_the_nine_agent_owned_job_lambdas():
    specs = deploy_alarms.runtime_unknown_specs()
    assert [s["job"] for s in specs] == list(registry.job_names()) and len(specs) == 9
    for s in specs:
        fn = deploy_l6.JOB_FN[s["job"]]
        assert s["function"] == fn and s["log_group"] == f"/aws/lambda/{fn}"
        assert s["pattern"] == '{ $.event = "runtime_job_unknown" }'
        assert (s["metric"], s["namespace"]) == ("RuntimeJobUnknown", "RetailOps/Jobs")
        assert s["alarm"] == f"{fn}-runtime-unknown" and s["dimension"] == {"Name": "Job", "Value": s["job"]}
    assert not {deploy_l6.JOB_FN["district-cycle"], deploy_l6.JOB_FN["store-digest"],
                deploy_l6.JOB_FN["autonomy-promotion"]} & {s["function"] for s in specs}


class _Exists(Exception):
    pass


class _Missing(Exception):
    pass


class FakeLogs:
    exceptions = type("E", (), {"ResourceAlreadyExistsException": _Exists, "ResourceNotFoundException": _Missing})

    def __init__(self, existing: set[str]) -> None:
        self.existing, self.calls = existing, []

    def create_log_group(self, logGroupName: str) -> None:
        self.calls.append(("create_log_group", logGroupName))
        if logGroupName in self.existing:
            raise _Exists(logGroupName)
        self.existing.add(logGroupName)

    def put_metric_filter(self, **kw: Any) -> None:
        assert kw["logGroupName"] in self.existing        # PutMetricFilter needs the log group
        self.calls.append(("put_metric_filter", kw))

    def delete_metric_filter(self, **kw: Any) -> None:
        self.calls.append(("delete_metric_filter", kw))
        if kw["logGroupName"].endswith("nurture-tick"):
            raise _Missing(kw["logGroupName"])


class FakeCloudWatch:
    def __init__(self) -> None:
        self.alarms: list[dict[str, Any]] = []
        self.deleted: list[str] = []

    def put_metric_alarm(self, **kw: Any) -> None:
        self.alarms.append(kw)

    def delete_alarms(self, AlarmNames: list[str]) -> None:
        self.deleted += AlarmNames


def test_runtime_unknown_alarms_dry_run_apply_and_delete():
    logs, cw = FakeLogs({"/aws/lambda/retail-ops-dev-equity-monitor"}), FakeCloudWatch()
    assert deploy_alarms.ensure_runtime_unknown(logs, cw, None, apply=False) == 9
    assert logs.calls == [] and cw.alarms == []
    assert deploy_alarms.ensure_runtime_unknown(logs, cw, "arn:sns", apply=True) == 9
    filters = [kw for name, kw in logs.calls if name == "put_metric_filter"]
    assert len(filters) == 9 and len([c for c in logs.calls if c[0] == "create_log_group"]) == 9
    assert filters[0]["filterPattern"] == '{ $.event = "runtime_job_unknown" }'
    assert filters[0]["metricTransformations"] == [{"metricName": "RuntimeJobUnknown", "metricNamespace": "RetailOps/Jobs",
                                                    "metricValue": "1", "dimensions": {"Job": "$.job"}}]
    alarm = next(a for a in cw.alarms if a["AlarmName"] == "retail-ops-dev-equity-monitor-runtime-unknown")
    assert alarm["Dimensions"] == [{"Name": "Job", "Value": "equity-monitor"}]
    assert (alarm["Statistic"], alarm["Period"], alarm["EvaluationPeriods"], alarm["Threshold"]) == ("Sum", 300, 1, 1.0)
    assert alarm["ComparisonOperator"] == "GreaterThanOrEqualToThreshold" and alarm["TreatMissingData"] == "notBreaching"
    assert alarm["AlarmActions"] == ["arn:sns"]
    deploy_alarms.delete_alarms(cw)
    assert {s["alarm"] for s in deploy_alarms.runtime_unknown_specs()} <= set(cw.deleted)
    deploy_alarms.delete_runtime_unknown(logs)
    assert len([c for c in logs.calls if c[0] == "delete_metric_filter"]) == 9


# --- deploy_l4: memory per function --------------------------------------------------------

class _MemLambda:
    """Records the MemorySize ensure_function sends on create and update."""

    class exceptions:
        class ResourceNotFoundException(Exception):
            pass

        class ClientError(Exception):
            pass

    def __init__(self, exists: bool):
        self.exists = exists
        self.created: set[str] = set()
        self.memory: list[int] = []

    def get_function(self, **kw: Any) -> dict:
        name = kw["FunctionName"]
        if not self.exists and name not in self.created:
            raise self.exceptions.ResourceNotFoundException(name)
        return {"Configuration": {"Environment": {"Variables": {}}, "LastUpdateStatus": "Successful",
                                  "FunctionArn": f"arn:aws:lambda:us-east-1:111122223333:function:{name}"}}

    def update_function_code(self, **kw: Any) -> dict:
        return {}

    def update_function_configuration(self, **kw: Any) -> dict:
        self.memory.append(kw["MemorySize"])
        return {}

    def create_function(self, **kw: Any) -> dict:
        self.memory.append(kw["MemorySize"])
        self.created.add(kw["FunctionName"])
        return {}


@pytest.mark.parametrize("exists", [True, False])
def test_ensure_function_memory_default_and_override(monkeypatch, exists):
    monkeypatch.setattr(deploy_l4, "_wait_updated", lambda lam, name: None)
    lam = _MemLambda(exists)
    deploy_l4.ensure_function(lam, "fn-a", "h.handler", "arn:role", b"zip", {}, True)
    deploy_l4.ensure_function(lam, "fn-b", "h.handler", "arn:role", b"zip", {}, True,
                              memory_mb=deploy_l4.SVC_MEMORY_MB)
    assert lam.memory == [deploy_l4.DEFAULT_MEMORY_MB, deploy_l4.SVC_MEMORY_MB]
    assert deploy_l4.DEFAULT_MEMORY_MB == 512 and deploy_l4.SVC_MEMORY_MB == 1769
