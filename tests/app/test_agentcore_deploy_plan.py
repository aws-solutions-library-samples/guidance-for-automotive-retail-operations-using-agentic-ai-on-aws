# ruff: noqa: S101
"""The registry-driven AgentCore deployer (ADR 0009, design §10.1) against
recording fakes: the dry run, apply, the no-op skip, the config, the stale A2
map, deletion, the deps seam and the arm64 check. No test builds a real
boto3.Session or spawns pip."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError

from app.agentcore import bundle, registry
from app.infra import deploy_agentcore as dac
from app.infra import deploy_l4
from tests.app.conftest import ROOT

ACCOUNT = "123456789012"
NAMES = registry.runtime_names()
ORDER = ["A1", "A3", "A4", "A5", "A7", "A2"]
# Other runtimes that live in the same account and must never be touched (P4).
FOREIGN = ("vehicle_maintenance_agent", "tire_ordering", "appointment_scheduler")
MUTATING = ("create", "update", "put", "delete")


def _err(code: str, op: str = "Op") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, op)


def _arn(name: str) -> str:
    return f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:runtime/{_rid(name)}"


def _rid(name: str) -> str:
    return f"{name}-{hashlib.sha256(name.encode()).hexdigest()[:10]}"


class _Client:
    """Records every call; answers from the account's handlers; unknown calls fail."""

    def __init__(self, service: str, account: FakeAccount) -> None:
        self.service, self.account = service, account

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        handler = getattr(self.account, f"{self.service.replace('-', '_')}__{name}", None)
        if handler is None:
            raise AssertionError(f"unexpected call {self.service}.{name}")

        def call(**kw: Any) -> Any:
            self.account.calls.append((self.service, name, kw))
            return handler(**kw)
        return call


class FakeAccount:
    """One AWS account's AgentCore control plane, IAM role, bucket and data plane."""

    def __init__(self, *, existing: tuple[str, ...] = (), role: bool = True, bucket: bool = True,
                 page_size: int = 3, statuses: dict[str, list[str]] | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.role, self.bucket, self.page_size = role, bucket, page_size
        self.statuses = statuses or {}
        self.runtimes: dict[str, dict[str, Any]] = {}
        for name in (*FOREIGN, *existing):
            self._add(name, env={})
        self.workload_identities = ["retail_ops_agent-wi1", "vehicle_maintenance_agent-wi2"]

    def _add(self, name: str, *, env: dict[str, str], artifact: dict | None = None) -> dict[str, Any]:
        rid = _rid(name)
        self.runtimes[rid] = {"agentRuntimeName": name, "agentRuntimeId": rid, "agentRuntimeArn": _arn(name),
                              "agentRuntimeVersion": "1", "status": "READY", "environmentVariables": env,
                              "agentRuntimeArtifact": artifact or {"codeConfiguration": {"code": {"s3": {
                                  "bucket": "old", "prefix": f"agent/{name}.zip"}}}}}
        return self.runtimes[rid]

    def by_name(self, name: str) -> dict[str, Any]:
        return next(r for r in self.runtimes.values() if r["agentRuntimeName"] == name)

    def session(self) -> FakeSession:
        return FakeSession(self)

    def mutations(self) -> list[tuple[str, str, dict[str, Any]]]:
        return [c for c in self.calls if c[1].startswith(MUTATING)]

    def touched(self) -> set[str]:
        """Runtime ids named by any call."""
        return {kw["agentRuntimeId"] for _, _, kw in self.calls if "agentRuntimeId" in kw}

    # sts / iam / s3
    def sts__get_caller_identity(self) -> dict:
        return {"Account": ACCOUNT, "Arn": f"arn:aws:iam::{ACCOUNT}:user/test"}

    def iam__get_role(self, RoleName: str) -> dict:
        if not self.role:
            raise _err("NoSuchEntity", "GetRole")
        return {"Role": {"RoleName": RoleName}}

    def iam__create_role(self, **kw: Any) -> dict:
        self.role = True
        return {}

    def iam__update_assume_role_policy(self, **kw: Any) -> dict:
        return {}

    def iam__put_role_policy(self, **kw: Any) -> dict:
        return {}

    def s3__head_bucket(self, Bucket: str) -> dict:
        if not self.bucket:
            raise _err("404", "HeadBucket")
        return {}

    def s3__create_bucket(self, **kw: Any) -> dict:
        self.bucket = True
        return {}

    def s3__put_object(self, **kw: Any) -> dict:
        return {}

    def s3__delete_object(self, **kw: Any) -> dict:
        return {}

    # bedrock-agentcore-control
    def bedrock_agentcore_control__list_agent_runtimes(self, maxResults: int = 100,
                                                       nextToken: str | None = None) -> dict:
        items = [{k: r[k] for k in ("agentRuntimeName", "agentRuntimeId", "agentRuntimeArn", "status")}
                 for r in self.runtimes.values()]
        start = int(nextToken or 0)
        page = {"agentRuntimes": items[start:start + self.page_size]}
        if start + self.page_size < len(items):
            page["nextToken"] = str(start + self.page_size)
        return page

    def bedrock_agentcore_control__get_agent_runtime(self, agentRuntimeId: str) -> dict:
        r = dict(self.runtimes[agentRuntimeId])
        queue = self.statuses.get(r["agentRuntimeName"])
        if queue:
            r["status"] = queue.pop(0) if len(queue) > 1 else queue[0]
        return r

    def bedrock_agentcore_control__create_agent_runtime(self, **kw: Any) -> dict:
        r = self._add(kw["agentRuntimeName"], env=kw["environmentVariables"], artifact=kw["agentRuntimeArtifact"])
        return {"agentRuntimeArn": r["agentRuntimeArn"], "agentRuntimeId": r["agentRuntimeId"],
                "agentRuntimeVersion": "1", "status": "CREATING"}

    def bedrock_agentcore_control__update_agent_runtime(self, **kw: Any) -> dict:
        r = self.runtimes[kw["agentRuntimeId"]]
        r["environmentVariables"] = kw["environmentVariables"]
        r["agentRuntimeArtifact"] = kw["agentRuntimeArtifact"]
        r["agentRuntimeVersion"] = str(int(r["agentRuntimeVersion"]) + 1)
        return {"agentRuntimeArn": r["agentRuntimeArn"], "agentRuntimeId": r["agentRuntimeId"],
                "agentRuntimeVersion": r["agentRuntimeVersion"], "status": "UPDATING"}

    def bedrock_agentcore_control__delete_agent_runtime(self, agentRuntimeId: str) -> dict:
        self.runtimes.pop(agentRuntimeId)
        return {"status": "DELETING"}

    def bedrock_agentcore_control__list_workload_identities(self, maxResults: int = 100,
                                                            nextToken: str | None = None) -> dict:
        return {"workloadIdentities": [{"name": n} for n in self.workload_identities]}

    def bedrock_agentcore_control__delete_workload_identity(self, name: str) -> dict:
        self.workload_identities.remove(name)
        return {}

    # data plane and logs
    def bedrock_agentcore__invoke_agent_runtime(self, agentRuntimeArn: str, **kw: Any) -> dict:
        payload = json.loads(kw["payload"])
        assert payload == {"kind": "ping"} and len(kw["runtimeSessionId"]) >= 33
        name = next(r["agentRuntimeName"] for r in self.runtimes.values() if r["agentRuntimeArn"] == agentRuntimeArn)
        code = next(a.code for a in registry.AGENTS.values() if a.runtime_name == name)
        body = {"statusCode": 200, "result": {"agent": code, "runtime": name, "version": "abc"}, "version": "abc"}
        return {"response": io.BytesIO(json.dumps(body).encode())}

    def logs__describe_log_groups(self, logGroupNamePrefix: str, **kw: Any) -> dict:
        return {"logGroups": [{"logGroupName": f"{logGroupNamePrefix}x-DEFAULT"}]}


class FakeSession:
    def __init__(self, account: FakeAccount) -> None:
        self.account = account

    def client(self, service: str, **_kw: Any) -> _Client:
        return _Client(service, self.account)


def _clients(account: FakeAccount) -> dac.Clients:
    s = account.session()
    return dac.Clients(sts=s.client("sts"), iam=s.client("iam"), s3=s.client("s3"),
                       control=s.client("bedrock-agentcore-control"), data=s.client("bedrock-agentcore"),
                       logs=s.client("logs"))


def stub_build_deps(profile: str, deps_root: Path) -> Path:
    """The deps seam: a stub tree instead of pip (review pass 2, M3)."""
    d = deps_root / profile
    (d / "boto3").mkdir(parents=True, exist_ok=True)
    (d / "boto3" / "__init__.py").write_text("# stub\n")
    (d / "boto3-1.43.0.dist-info").mkdir(exist_ok=True)
    if profile == "strands":
        (d / "strands_agents-1.59.0.dist-info").mkdir(exist_ok=True)
    (d / ".requirements").write_text((ROOT / bundle.PROFILES[profile]).read_text())
    return d


@pytest.fixture
def files(tmp_path, monkeypatch):
    """Config paths in tmp; a guardrail and a Knowledge Base are deployed."""
    guard, kb = tmp_path / "guardrail-config.json", tmp_path / "knowledge-base-config.json"
    guard.write_text(json.dumps({"guardrail_id": "gr-abcd1234", "version": "3"}))
    kb.write_text(json.dumps({"knowledge_base_id": "KBID5678"}))
    monkeypatch.setattr(dac, "GUARDRAIL_CONFIG", guard)
    monkeypatch.setattr(dac, "KB_CONFIG", kb)
    monkeypatch.setattr(dac, "CONFIG_PATH", tmp_path / "agentcore-config.json")
    monkeypatch.setattr(dac, "DEPS_ROOT", tmp_path / "deps")
    monkeypatch.setattr(dac, "build_deps_pip", stub_build_deps)
    return tmp_path


def _apply(account: FakeAccount, files: Path, agents=None, **kw: Any) -> dac.ApplyResult:
    plan = dac.build_plan(_clients(account), agents or dac.select_agents(None), ACCOUNT)
    return dac.run_apply(_clients(account), plan, force=kw.pop("force", False),
                         allow_no_guardrail=kw.pop("allow_no_guardrail", False),
                         config_path=files / "agentcore-config.json", deps_root=files / "deps",
                         build_deps=kw.pop("build_deps", stub_build_deps), sleep=lambda _s: None, **kw)


# --- dry run ------------------------------------------------------------------------------

def test_dry_run_plans_six_creates_in_order_with_read_calls_only(files, capsys):
    account = FakeAccount(page_size=2)
    plan = dac.build_plan(_clients(account), dac.select_agents(None), ACCOUNT)
    assert [p.spec.code for p in plan.agents] == ORDER
    assert [p.spec.runtime_name for p in plan.agents] == [registry.AGENTS[c].runtime_name for c in ORDER]
    assert {p.action for p in plan.agents} == {"create"} and plan.legacy is None
    assert account.mutations() == []
    assert {(s, m) for s, m, _ in account.calls} == {
        ("iam", "get_role"), ("s3", "head_bucket"), ("bedrock-agentcore-control", "list_agent_runtimes")}
    dac.print_plan(plan, "DRY-RUN")
    lines = [ln for ln in capsys.readouterr().out.splitlines() if "[runtime]" in ln]
    assert [ln.split()[2].rstrip(":") for ln in lines] == [registry.AGENTS[c].runtime_name for c in ORDER]
    assert all("would create" in ln for ln in lines)
    a2 = lines[-1]
    assert "code agent/retail_ops_dev_a2_journey.zip" in a2 and "deps tools" in a2
    assert "RETAIL_OPS_GUARDRAIL_ID=...1234" in a2 and "gr-abcd1234" not in a2
    assert "A2A A1 retail_ops_dev_a1_discovery, A3 retail_ops_dev_a3_sales, A4 retail_ops_dev_a4_trade_in" in a2
    a3 = next(ln for ln in lines if "retail_ops_dev_a3_sales" in ln.split()[2])
    assert "RETAIL_OPS_KNOWLEDGE_BASE_ID=...5678" in a3 and "deps strands" in a3


def test_main_dry_run_end_to_end_on_the_fakes(files, capsys):
    account = FakeAccount(existing=("retail_ops_agent",))
    assert dac.main([], session=account.session()) == 0
    out = capsys.readouterr().out
    assert sum("[runtime]" in ln and "would create" in ln for ln in out.splitlines()) == 6
    assert "retail_ops_agent (" in out and "legacy (left untouched)" in out
    assert "[DRY-RUN] done: 6 would create, 0 would update" in out
    assert account.mutations() == []
    assert account.touched() == set()     # the legacy and foreign runtimes are only listed


def test_list_runtimes_follows_next_token_to_page_two():
    account = FakeAccount(existing=("retail_ops_dev_a7_service",), page_size=2)
    names = [r["agentRuntimeName"] for r in dac.list_runtimes(_clients(account).control)]
    assert names == [*FOREIGN, "retail_ops_dev_a7_service"]
    assert len([c for c in account.calls if c[1] == "list_agent_runtimes"]) == 2


def test_plan_json_and_print_policy_are_offline(capsys):
    assert dac.main(["--plan-json"], session=None) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["deploy_order"] == ORDER and [a["runtime_name"] for a in plan["agents"]][-1] == NAMES[1]
    keys = {a["code"]: a["env_keys"] for a in plan["agents"]}
    assert all("RETAIL_OPS_MEMORY_ID" not in k for k in keys.values())
    assert [c for c, k in keys.items() if "RETAIL_OPS_AGENT_RUNTIMES" in k] == ["A2"]
    assert [c for c, k in keys.items() if "RETAIL_OPS_KNOWLEDGE_BASE_ID" in k] == ["A3"]
    assert dac.main(["--print-policy"]) == 0
    assert json.loads(capsys.readouterr().out)["Statement"]


@pytest.mark.parametrize("argv", [["--agent", "A9"], ["--agent", "A6"], ["--agent", ","], ["--account", "123"]])
def test_cli_rejects_unknown_codes_and_bad_accounts(argv, capsys):
    with pytest.raises(SystemExit) as e:
        dac.main([*argv, "--plan-json"])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert ("known codes: A1, A2, A3, A4, A5, A7" in err) or ("12-digit" in err)


def test_agent_subset_keeps_dependency_order():
    assert [a.code for a in dac.select_agents("a2,A3")] == ["A3", "A2"]


# --- apply --------------------------------------------------------------------------------

def test_apply_creates_six_runtimes_with_the_shared_role_env_and_tags(files, capsys):
    account = FakeAccount(role=False, bucket=False)
    result = _apply(account, files)
    assert result.exit_code == 0 and result.done == ORDER and result.arns_changed
    creates = [kw for s, m, kw in account.calls if m == "create_agent_runtime"]
    assert [c["agentRuntimeName"] for c in creates] == [registry.AGENTS[c].runtime_name for c in ORDER]
    role_arn = f"arn:aws:iam::{ACCOUNT}:role/AmazonBedrockAgentCore-retail-ops-exec"
    for code, req in zip(ORDER, creates, strict=True):
        env = req["environmentVariables"]
        assert req["roleArn"] == role_arn
        assert req["tags"] == {"project": "retail-ops", "env": "dev", "agent": code}
        assert req["protocolConfiguration"] == {"serverProtocol": "HTTP"}
        assert req["networkConfiguration"] == {"networkMode": "PUBLIC"}
        assert req["lifecycleConfiguration"] == {"idleRuntimeSessionTimeout": 300, "maxLifetime": 3600}
        assert "authorizerConfiguration" not in req and "platformVersion" not in req
        code_cfg = req["agentRuntimeArtifact"]["codeConfiguration"]
        assert code_cfg["code"]["s3"] == {"bucket": f"bedrock-agentcore-retail-ops-{ACCOUNT}",
                                          "prefix": f"agent/{registry.AGENTS[code].runtime_name}.zip"}
        assert code_cfg["runtime"] == "PYTHON_3_12" and code_cfg["entryPoint"] == ["app/agentcore/agent.py"]
        assert env["RETAIL_OPS_AGENT_ID"] == code and env["RETAIL_OPS_GUARDRAIL_ID"] == "gr-abcd1234"
        assert env["RETAIL_OPS_EVENT_BUS_LIVE"] == "1" and env["INTEGRATION_MODE"] == "aws"
        assert "RETAIL_OPS_MEMORY_ID" not in env
        assert ("RETAIL_OPS_KNOWLEDGE_BASE_ID" in env) == (code == "A3")
        assert ("RETAIL_OPS_A3_AGENT_FORCE" in env) == (code == "A3")
        assert ("RETAIL_OPS_AGENT_RUNTIMES" in env) == (code == "A2")
    a2_map = json.loads(creates[-1]["environmentVariables"]["RETAIL_OPS_AGENT_RUNTIMES"])
    assert a2_map == {c: _arn(registry.AGENTS[c].runtime_name) for c in ("A1", "A3", "A4")}
    puts = [kw["Key"] for s, m, kw in account.calls if m == "put_object"]
    assert puts == [f"agent/{registry.AGENTS[c].runtime_name}.zip" for c in ORDER]
    assert ("s3", "create_bucket") in {(s, m) for s, m, _ in account.calls}
    assert not [c for c in account.calls if "resource_policy" in c[1]]
    assert account.touched() <= {_rid(n) for n in NAMES}            # foreign runtimes untouched
    assert "re-run `deploy_l4 --apply` and `deploy_l6 --apply`" in capsys.readouterr().out


def test_config_v2_is_written_atomically_and_round_trips_into_l4(files):
    account = FakeAccount()
    result = _apply(account, files)
    path = files / "agentcore-config.json"
    cfg = json.loads(path.read_text())
    assert not (files / "agentcore-config.json.tmp").exists()
    assert cfg["schema_version"] == 2 and cfg["env"] == "dev" and "legacy" not in cfg
    assert list(cfg["agents"]) == list(registry.AGENTS)
    a3 = cfg["agents"]["A3"]
    assert a3["runtime_name"] == "retail_ops_dev_a3_sales" and a3["engine"] == "reasoning" and a3["name"] == "Sales Agent"
    assert len(a3["code_sha256"]) == 64
    wired = json.loads(deploy_l4.runtime_env_from_config(path)["RETAIL_OPS_AGENT_RUNTIMES"])
    assert wired == result.arns and list(wired) == list(registry.AGENTS)


def test_an_unchanged_second_apply_makes_no_runtime_update(files, capsys):
    account = FakeAccount()
    _apply(account, files)
    account.calls.clear()
    result = _apply(account, files)
    assert result.unchanged == ORDER and result.done == [] and result.exit_code == 0
    assert not [c for c in account.calls if c[1] in ("update_agent_runtime", "create_agent_runtime", "put_object")]
    assert "runtime ARNs changed" not in capsys.readouterr().out.split("summary:")[-1]


def test_a_code_change_uploads_and_updates_and_force_always_does(files):
    account = FakeAccount()
    _apply(account, files)
    cfg = json.loads((files / "agentcore-config.json").read_text())
    cfg["agents"]["A5"]["code_sha256"] = "0" * 64              # as if A5's code had changed since
    (files / "agentcore-config.json").write_text(json.dumps(cfg))
    account.calls.clear()
    result = _apply(account, files)
    assert result.done == ["A5"] and set(result.unchanged) == set(ORDER) - {"A5"}
    assert [kw["Key"] for _, m, kw in account.calls if m == "put_object"] == ["agent/retail_ops_dev_a5_campaign.zip"]
    account.calls.clear()
    assert _apply(account, files, force=True).done == ORDER
    assert len([c for c in account.calls if c[1] == "update_agent_runtime"]) == 6


def test_apply_refuses_without_a_guardrail_before_any_call(files, monkeypatch):
    monkeypatch.setattr(dac, "GUARDRAIL_CONFIG", files / "missing.json")
    account = FakeAccount()
    with pytest.raises(SystemExit, match="ADR 0003"):
        _apply(account, files)
    assert account.mutations() == []
    result = _apply(account, files, allow_no_guardrail=True)
    assert result.exit_code == 0
    assert all("RETAIL_OPS_GUARDRAIL_ID" not in kw["environmentVariables"]
               for _, m, kw in account.calls if m == "create_agent_runtime")


def test_a_failed_create_stops_the_run_and_keeps_the_ready_agents(files, capsys):
    account = FakeAccount(statuses={"retail_ops_dev_a4_trade_in": ["CREATE_FAILED"]})
    result = _apply(account, files)
    assert result.exit_code == 1 and result.done == ["A1", "A3"] and result.failed == ["A4"]
    assert result.not_attempted == ["A5", "A7", "A2"]
    assert list(json.loads((files / "agentcore-config.json").read_text())["agents"]) == ["A1", "A3"]
    out = capsys.readouterr().out
    assert "/aws/bedrock-agentcore/runtimes/retail_ops_dev_a4_trade_in-*" in out and "not attempted" in out


def test_a_ready_timeout_is_not_written_and_callers_do_not_wire_it(files, capsys):
    account = FakeAccount(statuses={"retail_ops_dev_a1_discovery": ["CREATING"]})
    result = _apply(account, files)
    assert result.exit_code == 1 and result.not_ready == ["A1"] and "A1" not in result.arns
    assert set(result.done) == set(ORDER) - {"A1"}
    a2 = next(kw for _, m, kw in account.calls
              if m == "create_agent_runtime" and kw["agentRuntimeName"] == "retail_ops_dev_a2_journey")
    assert set(json.loads(a2["environmentVariables"]["RETAIL_OPS_AGENT_RUNTIMES"])) == {"A3", "A4"}
    assert "A1" not in json.loads((files / "agentcore-config.json").read_text())["agents"]
    assert "re-run --apply once the runtime is READY" in capsys.readouterr().out


def test_main_apply_end_to_end_on_the_fakes(files):
    account = FakeAccount()
    assert dac.main(["--apply", "--agent", "A4"], session=account.session()) == 0
    assert [kw["agentRuntimeName"] for _, m, kw in account.calls if m == "create_agent_runtime"] == \
        ["retail_ops_dev_a4_trade_in"]
    assert list(json.loads((files / "agentcore-config.json").read_text())["agents"]) == ["A4"]


# --- deps seam and the arm64 check ----------------------------------------------------------

def _elf(path: Path, machine: int) -> None:
    head = bytearray(64)
    head[:4], head[4], head[5] = b"\x7fELF", 2, 1          # 64-bit, little-endian
    head[18:20] = machine.to_bytes(2, "little")
    path.write_bytes(bytes(head))


def test_check_arm64_on_crafted_elf_headers(tmp_path):
    (tmp_path / "pkg").mkdir()
    _elf(tmp_path / "pkg" / "ok.cpython-312-aarch64-linux-gnu.so", 0xB7)
    _elf(tmp_path / "pkg" / "x86.cpython-312-x86_64-linux-gnu.so", 0x3E)
    _elf(tmp_path / "pkg" / "libz.so.1", 0xB7)
    (tmp_path / "pkg" / "fake.so").write_bytes(b"not an elf file at all")
    (tmp_path / "pkg" / "module.py").write_text("")
    assert dac.check_arm64(tmp_path) == [tmp_path / "pkg" / "fake.so",
                                         tmp_path / "pkg" / "x86.cpython-312-x86_64-linux-gnu.so"]


def test_an_x86_binary_stops_apply_before_any_s3_or_runtime_call(files):
    def x86_deps(profile: str, deps_root: Path) -> Path:
        d = stub_build_deps(profile, deps_root)
        _elf(d / "boto3" / "_speedups.cpython-312-x86_64-linux-gnu.so", 0x3E)
        return d

    account = FakeAccount()
    with pytest.raises(SystemExit, match="_speedups.cpython-312-x86_64-linux-gnu.so"):
        _apply(account, files, build_deps=x86_deps)
    assert account.mutations() == []


# --- the stale A2 map (review pass 2, L11) --------------------------------------------------

def test_a_changed_callee_arn_puts_a2_in_the_plan_as_an_env_only_update(files, capsys):
    account = FakeAccount(existing=("retail_ops_dev_a1_discovery",))
    stale = {"A1": f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:runtime/retail_ops_dev_a1_discovery-OldOld1234"}
    a2 = account._add("retail_ops_dev_a2_journey", env={"RETAIL_OPS_AGENT_RUNTIMES": json.dumps(stale)})
    plan = dac.build_plan(_clients(account), dac.select_agents("A1"), ACCOUNT)
    assert [(p.spec.code, p.action) for p in plan.agents] == [("A1", "update"), ("A2", "env-only")]
    assert "would update (env only: A1 ARN changed)" in plan.agents[1].label
    old_artifact = a2["agentRuntimeArtifact"]
    account.calls.clear()
    result = dac.run_apply(_clients(account), plan, force=False, allow_no_guardrail=False,
                           config_path=files / "agentcore-config.json", deps_root=files / "deps",
                           build_deps=stub_build_deps, sleep=lambda _s: None)
    assert result.exit_code == 0 and result.done == ["A1", "A2"]
    upd = [kw for _, m, kw in account.calls if m == "update_agent_runtime" and kw["agentRuntimeId"] == a2["agentRuntimeId"]]
    assert len(upd) == 1 and upd[0]["agentRuntimeArtifact"] == old_artifact          # env only: same code
    assert json.loads(upd[0]["environmentVariables"]["RETAIL_OPS_AGENT_RUNTIMES"]) == \
        {"A1": _arn("retail_ops_dev_a1_discovery")}
    assert [kw["Key"] for _, m, kw in account.calls if m == "put_object"] == ["agent/retail_ops_dev_a1_discovery.zip"]
    assert "re-run `deploy_l4 --apply` and `deploy_l6 --apply`" in capsys.readouterr().out


def test_a_current_a2_map_adds_nothing(files):
    account = FakeAccount(existing=("retail_ops_dev_a1_discovery",))
    current = {"A1": _arn("retail_ops_dev_a1_discovery")}
    account._add("retail_ops_dev_a2_journey", env={"RETAIL_OPS_AGENT_RUNTIMES": json.dumps(current)})
    plan = dac.build_plan(_clients(account), dac.select_agents("A1"), ACCOUNT)
    assert [p.spec.code for p in plan.agents] == ["A1"]


# --- ping, delete, legacy ---------------------------------------------------------------

def test_invoke_pings_each_runtime_and_fails_on_a_missing_one(files, capsys):
    account = FakeAccount(existing=tuple(NAMES))
    assert dac.main(["--invoke"], session=account.session()) == 0
    assert capsys.readouterr().out.count(": OK agent=") == 6
    account = FakeAccount(existing=("retail_ops_dev_a1_discovery",))
    assert dac.main(["--invoke"], session=account.session()) == 1
    assert "retail_ops_dev_a2_journey: not deployed" in capsys.readouterr().out
    assert account.mutations() == []


@pytest.mark.parametrize("flag", ["--delete", "--delete-legacy"])
def test_delete_without_apply_only_lists(files, flag, capsys):
    account = FakeAccount(existing=(*NAMES, "retail_ops_agent"))
    assert dac.main([flag], session=account.session()) == 0
    assert account.mutations() == [] and "would delete" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [["--delete", "--apply"], ["--delete", "--apply", "--account", "210987654321"],
                                  ["--delete-legacy", "--apply"]])
def test_destructive_modes_need_the_callers_account(files, argv):
    account = FakeAccount(existing=tuple(NAMES))
    with pytest.raises(SystemExit, match=ACCOUNT):
        dac.main(argv, session=account.session())
    assert account.mutations() == []


def test_delete_apply_removes_exactly_the_six_runtimes_and_code_objects(files):
    account = FakeAccount(existing=(*NAMES, "retail_ops_agent"))
    _apply(account, files)
    account.calls.clear()
    assert dac.main(["--delete", "--apply", "--account", ACCOUNT], session=account.session()) == 0
    deleted = {kw["agentRuntimeId"] for _, m, kw in account.calls if m == "delete_agent_runtime"}
    assert deleted == {_rid(n) for n in NAMES}
    keys = sorted(kw["Key"] for _, m, kw in account.calls if m == "delete_object")
    assert keys == sorted(f"agent/{n}.zip" for n in NAMES)
    assert not [c for c in account.calls if c[0] == "iam" or c[1] in ("delete_bucket", "create_bucket")]
    assert sorted(r["agentRuntimeName"] for r in account.runtimes.values()) == sorted([*FOREIGN, "retail_ops_agent"])
    cfg = json.loads((files / "agentcore-config.json").read_text())
    assert cfg["agents"] == {} and cfg["legacy"]["agent_runtime_name"] == "retail_ops_agent"


def test_delete_apply_with_an_agent_deletes_only_that_runtime(files):
    account = FakeAccount(existing=tuple(NAMES))
    assert dac.main(["--delete", "--apply", "--account", ACCOUNT, "--agent", "A5"], session=account.session()) == 0
    assert [kw["agentRuntimeId"] for _, m, kw in account.calls if m == "delete_agent_runtime"] == \
        [_rid("retail_ops_dev_a5_campaign")]
    assert [kw["Key"] for _, m, kw in account.calls if m == "delete_object"] == ["agent/retail_ops_dev_a5_campaign.zip"]


def test_delete_legacy_apply_targets_only_the_legacy_items(files, capsys):
    account = FakeAccount(existing=(*NAMES, "retail_ops_agent"))
    assert dac.main(["--delete-legacy", "--apply", "--account", ACCOUNT], session=account.session()) == 0
    mutations = [(s, m, kw) for s, m, kw in account.mutations()]
    assert mutations == [
        ("bedrock-agentcore-control", "delete_agent_runtime", {"agentRuntimeId": _rid("retail_ops_agent")}),
        ("s3", "delete_object", {"Bucket": f"bedrock-agentcore-retail-ops-{ACCOUNT}", "Key": "agent/retail-ops-agent.zip"}),
        ("bedrock-agentcore-control", "delete_workload_identity", {"name": "retail_ops_agent-wi1"}),
    ]
    assert account.workload_identities == ["vehicle_maintenance_agent-wi2"]
    out = capsys.readouterr().out
    assert "kept: role AmazonBedrockAgentCore-retail-ops-exec" in out and "kept: log group" in out


# --- the shared execution role -----------------------------------------------------------

def _main_exec_statements(account: str) -> list[dict]:
    """The single runtime's role policy as it is on main (59d6e68)."""
    return [
        {"Effect": "Allow", "Action": ["logs:DescribeLogStreams", "logs:CreateLogGroup", "logs:CreateLogStream",
                                       "logs:PutLogEvents", "logs:PutResourcePolicy", "logs:DescribeLogGroups"],
         "Resource": [f"arn:aws:logs:us-east-1:{account}:log-group:/aws/bedrock-agentcore/runtimes/*",
                      f"arn:aws:logs:us-east-1:{account}:log-group:*"]},
        {"Effect": "Allow", "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords",
                                       "xray:GetSamplingRules", "xray:GetSamplingTargets"], "Resource": "*"},
        {"Effect": "Allow", "Action": "cloudwatch:PutMetricData", "Resource": "*",
         "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}}},
        # Bedrock is narrower than main's statement (security review 2026-10-27):
        # the Claude family, the account's inference and guardrail profiles, the
        # guardrail itself and the knowledge base; main's account-wide
        # `arn:aws:bedrock:<region>:<account>:*` and `foundation-model/*` are gone.
        {"Sid": "BedrockModelInvocation", "Effect": "Allow",
         "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream", "bedrock:ApplyGuardrail",
                    "bedrock:Retrieve"],
         "Resource": ["arn:aws:bedrock:*::foundation-model/anthropic.claude-*",
                      f"arn:aws:bedrock:*:{account}:inference-profile/*",
                      f"arn:aws:bedrock:*:{account}:guardrail-profile/*",
                      f"arn:aws:bedrock:us-east-1:{account}:guardrail/*",
                      f"arn:aws:bedrock:us-east-1:{account}:knowledge-base/*"]},
        {"Sid": "RetailOpsDynamo", "Effect": "Allow",
         "Action": ["dynamodb:GetItem", "dynamodb:BatchGetItem", "dynamodb:Query", "dynamodb:Scan",
                    "dynamodb:PutItem", "dynamodb:UpdateItem"],
         "Resource": [f"arn:aws:dynamodb:us-east-1:{account}:table/retail-ops-dev-*",
                      f"arn:aws:dynamodb:us-east-1:{account}:table/retail-ops-dev-*/index/*"]},
        {"Sid": "RetailOpsCode", "Effect": "Allow", "Action": ["s3:GetObject"],
         "Resource": [f"arn:aws:s3:::bedrock-agentcore-retail-ops-{account}/*"]},
        {"Sid": "RetailOpsEvents", "Effect": "Allow", "Action": ["events:PutEvents"],
         "Resource": [f"arn:aws:events:us-east-1:{account}:event-bus/app-bus-dev"]},
    ]


def test_exec_policy_keeps_mains_statements_and_adds_outreach_identities_and_invoke():
    policy = dac._exec_policy(ACCOUNT)
    statements = policy["Statement"]
    for st in _main_exec_statements(ACCOUNT):
        assert st in statements, st.get("Sid") or st["Action"]
    tokens = next(s for s in statements if s.get("Sid") == "GetAgentAccessToken")
    base = f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:workload-identity-directory/default"
    assert tokens["Resource"] == [base] + [f"{base}/workload-identity/{n}-*" for n in (*NAMES, "retail_ops_agent")]
    assert {"Sid": "Outreach", "Effect": "Allow", "Action": ["ses:SendEmail", "ses:SendRawEmail", "sns:Publish"],
            "Resource": "*"} in statements
    invoke = next(s for s in statements if s.get("Sid") == "AgentRuntime")
    assert invoke["Action"] == ["bedrock-agentcore:InvokeAgentRuntime"]
    assert invoke["Resource"] == [f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:runtime/retail_ops_dev_*"]
    assert len(statements) == len(_main_exec_statements(ACCOUNT)) + 3
    assert len(json.dumps(policy, separators=(",", ":"))) < 10240      # IAM inline policy limit
    assert not [s for s in statements if "Principal" in s]


def test_runtime_env_never_carries_memory_and_maps_only_declared_calls():
    arns = {c: f"arn-{c}" for c in registry.AGENTS}
    for spec in registry.AGENTS.values():
        env = dac.runtime_env(spec, guardrail={"RETAIL_OPS_GUARDRAIL_ID": "g"}, kb_id="kb", callee_arns=arns)
        assert "RETAIL_OPS_MEMORY_ID" not in env
        mapped = json.loads(env.get("RETAIL_OPS_AGENT_RUNTIMES", "{}"))
        assert set(mapped) == set(spec.calls)
    a2 = dac.runtime_env(registry.AGENTS["A2"], guardrail={}, kb_id=None, callee_arns={"A1": "arn-A1"})
    assert json.loads(a2["RETAIL_OPS_AGENT_RUNTIMES"]) == {"A1": "arn-A1"}
    assert "RETAIL_OPS_AGENT_RUNTIMES" not in dac.runtime_env(registry.AGENTS["A2"], guardrail={}, kb_id=None,
                                                              callee_arns={})
