# ruff: noqa: S101, S603
"""Per-agent bundles (ADR 0009, design §8): closures, reproducible zips, the
no-op hash, a fresh-interpreter import of every bundle, and the shared role
covering every AWS call the bundled code makes."""
from __future__ import annotations

import fnmatch
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from app.agentcore import bundle, registry
from app.infra import deploy_agentcore
from tests.app.conftest import ROOT

CODES = list(registry.AGENTS)
ACCOUNT = "123456789012"
_EXCLUDED_FILES = ("app/services/api/handler.py", "app/services/devserver.py", "app/services/l1_data/ddb_load.py")
_EXCLUDED_DIRS = ("app/services/api/routes/", "app/infra/")


def _module_file(module: str) -> str:
    base = ROOT.joinpath(*module.split("."))
    path = base.with_suffix(".py") if base.with_suffix(".py").is_file() else base / "__init__.py"
    return path.relative_to(ROOT).as_posix()


# --- closures ---------------------------------------------------------------------------

@pytest.mark.parametrize("code", CODES)
def test_closure_contains_the_entry_modules_and_leaves_out_the_api_and_infra(code):
    spec = registry.AGENTS[code]
    files = bundle.bundle_files(spec)
    for module in bundle.entry_modules(spec):
        assert _module_file(module) in files, module
    for parent in ("app/__init__.py", "app/services/__init__.py", "app/agentcore/tools/__init__.py"):
        assert parent in files
    assert not [f for f in files if f in _EXCLUDED_FILES or f.startswith(_EXCLUDED_DIRS)]
    assert files == sorted(files) and all(f.endswith(".py") for f in files)


@pytest.mark.parametrize("code", CODES)
def test_bundle_holds_only_its_own_agents_adapter(code):
    files = bundle.bundle_files(registry.AGENTS[code])
    adapters = sorted(f for f in files if f.startswith("app/agentcore/tools/a"))
    assert adapters == [f"app/agentcore/tools/{code.lower()}.py"]


def test_closure_follows_function_level_and_relative_imports(tmp_path):
    pkg = tmp_path / "app" / "services" / "demo"
    pkg.mkdir(parents=True)
    for d in (tmp_path / "app", tmp_path / "app" / "services", pkg):
        (d / "__init__.py").write_text("")
    (pkg / "entry.py").write_text("from . import sibling\n\ndef later():\n    from .lazy import thing\n"
                                  "    import importlib\n    importlib.import_module('app.services.demo.hidden')\n")
    (pkg / "sibling.py").write_text("from ..demo import named\n")
    (pkg / "named.py").write_text("")
    (pkg / "lazy.py").write_text("thing = 1\n")
    (pkg / "hidden.py").write_text("")
    spec = registry.AgentSpec(code="A1", slug="demo", display="Demo", engine="tools", deps="tools", reasoning=None,
                              tools=(registry.ToolSpec("t", "app.services.demo.entry:later", {}, scope="none",
                                                       roles=None, read_only=True, idempotent=True),),
                              jobs=(), calls=())
    files = bundle.closure(spec, root=tmp_path)
    assert files == ["app/__init__.py", "app/services/__init__.py", "app/services/demo/__init__.py",
                     "app/services/demo/entry.py", "app/services/demo/lazy.py", "app/services/demo/named.py",
                     "app/services/demo/sibling.py"]      # importlib strings are not followed: no hidden.py


# --- zips, hashes, BUILD_INFO --------------------------------------------------------------

def _stub_deps(root: Path, *dist: str) -> Path:
    deps = root / "deps-tree"
    (deps / "boto3").mkdir(parents=True, exist_ok=True)
    (deps / "boto3" / "__init__.py").write_text("# stub\n")
    (deps / "boto3" / "__pycache__").mkdir(exist_ok=True)
    (deps / "boto3" / "__pycache__" / "x.pyc").write_bytes(b"\0")
    for name in dist or ("boto3-1.43.0.dist-info",):
        (deps / name).mkdir(exist_ok=True)
        (deps / name / "METADATA").write_text(name)
    return deps


def test_zip_is_byte_identical_across_builds_with_sorted_fixed_entries(tmp_path):
    spec = registry.AGENTS["A4"]
    deps = _stub_deps(tmp_path)
    req = ROOT / bundle.PROFILES[spec.deps]
    one, info = bundle.build_zip(spec, deps, requirements=req, git_commit="abc123")
    two, _ = bundle.build_zip(spec, deps, requirements=req, git_commit="abc123")
    assert one == two
    with zipfile.ZipFile(io.BytesIO(one)) as z:
        names = z.namelist()
        assert names == sorted(names)
        assert {i.date_time for i in z.infolist()} == {bundle.ZIP_DATE}
        assert {i.external_attr for i in z.infolist()} == {(0o100644 & 0xFFFF) << 16}
        assert "requirements.txt" in names and "deps/boto3/__init__.py" in names
        assert not [n for n in names if "__pycache__" in n]
        assert json.loads(z.read("BUILD_INFO.json")) == info
        assert z.read("requirements.txt") == req.read_bytes()
    assert info == {"agent": "A4", "runtime_name": "retail_ops_dev_a4_trade_in", "code_sha256": info["code_sha256"],
                    "deps_profile": "tools", "deps": ["boto3-1.43.0"], "git_commit": "abc123"}


def test_code_sha256_changes_with_code_or_resolved_deps_only(tmp_path):
    (tmp_path / "app").mkdir()
    code = tmp_path / "app" / "m.py"
    code.write_text("x = 1\n")
    deps = _stub_deps(tmp_path)
    files = ["app/m.py"]
    base = bundle.code_sha256(files, deps, root=tmp_path)
    assert bundle.code_sha256(files, deps, root=tmp_path) == base
    (deps / "boto3" / "__init__.py").write_text("# rebuilt, same version\n")   # not code, not a dist name
    assert bundle.code_sha256(files, deps, root=tmp_path) == base
    code.write_text("x = 2\n")
    changed_code = bundle.code_sha256(files, deps, root=tmp_path)
    assert changed_code != base
    (deps / "boto3-1.43.0.dist-info").rename(deps / "boto3-1.44.0.dist-info")
    assert bundle.code_sha256(files, deps, root=tmp_path) != changed_code
    assert bundle.code_sha256(files, None, root=tmp_path) != changed_code


# --- fresh-interpreter import smoke test (review H3) -------------------------------------------

@pytest.mark.parametrize("code", CODES)
def test_code_only_bundle_imports_in_an_isolated_interpreter(tmp_path, code):
    spec = registry.AGENTS[code]
    data, _ = bundle.build_zip(spec, None, requirements=ROOT / bundle.PROFILES[spec.deps])
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(tmp_path)
    targets = bundle.entry_modules(spec)
    script = (f"import sys; sys.path[:0] = [{str(tmp_path)!r}]; import importlib, app.agentcore.agent; "
              f"[importlib.import_module(m) for m in {targets!r}]\n"
              "try:\n    import app.services.api.handler\nexcept ModuleNotFoundError:\n    pass\n"
              "else:\n    sys.exit('the API handler must not be importable from an agent bundle')\n")
    r = subprocess.run([sys.executable, "-I", "-c", script], cwd=tmp_path, timeout=60, check=False,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]


# --- AWS calls and the shared role (review H1, pass-2 L7) ---------------------------------------

def test_aws_call_scanner_sees_factories_bound_names_and_chained_calls(tmp_path):
    (tmp_path / "m.py").write_text(
        "import boto3\n"
        "def _client():\n    return boto3.client('dynamodb', region_name='x')\n"
        "def a():\n    _client().get_item(Key=1)\n    c = _client()\n    c.scan()\n"
        "def b():\n    rt = boto3.client('bedrock-runtime')\n    rt.converse()\n"
        "    boto3.client('sns').publish(Message='m')\n"
        "class K:\n    def __init__(self):\n        self.ses = boto3.client('ses')\n"
        "    def send(self):\n        self.ses.send_email()\n        other.put_item()\n")
    assert bundle.aws_calls(["m.py"], root=tmp_path) == {
        ("dynamodb", "get_item"), ("dynamodb", "scan"), ("bedrock-runtime", "converse"),
        ("sns", "publish"), ("ses", "send_email")}


@pytest.mark.parametrize("code", CODES)
def test_every_bundle_uses_dynamodb_and_outreach_agents_use_ses_and_sns(code):
    services = {svc for svc, _ in bundle.aws_calls(bundle.bundle_files(registry.AGENTS[code]))}
    assert "dynamodb" in services
    if code in ("A2", "A3", "A7"):
        assert {"ses", "sns"} <= services


_RT = f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:runtime/retail_ops_dev_a1_discovery-AbCdEf1234"
_TABLE = f"arn:aws:dynamodb:us-east-1:{ACCOUNT}:table/retail-ops-dev-DEALER_Journeys"
_GRANTS = {
    ("dynamodb", "get_item"): ("dynamodb:GetItem", _TABLE),
    ("dynamodb", "batch_get_item"): ("dynamodb:BatchGetItem", _TABLE),
    ("dynamodb", "query"): ("dynamodb:Query", _TABLE),
    ("dynamodb", "scan"): ("dynamodb:Scan", _TABLE),
    ("dynamodb", "put_item"): ("dynamodb:PutItem", _TABLE),
    ("dynamodb", "update_item"): ("dynamodb:UpdateItem", _TABLE),
    # Converse on a `us.anthropic.claude-*` inference profile is authorized on the
    # profile AND on the foundation model in whichever region it routes to.
    ("bedrock-runtime", "converse"):
        ("bedrock:InvokeModel", "arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-opus-4-5-20251101-v1:0"),
    ("bedrock-runtime", "converse_stream"):
        ("bedrock:InvokeModelWithResponseStream",
         f"arn:aws:bedrock:us-east-1:{ACCOUNT}:inference-profile/us.anthropic.claude-opus-4-5-20251101-v1:0"),
    ("bedrock-agent-runtime", "retrieve"):
        ("bedrock:Retrieve", f"arn:aws:bedrock:us-east-1:{ACCOUNT}:knowledge-base/KB12345678"),
    ("events", "put_events"): ("events:PutEvents", f"arn:aws:events:us-east-1:{ACCOUNT}:event-bus/app-bus-dev"),
    ("ses", "send_email"): ("ses:SendEmail", f"arn:aws:ses:us-east-1:{ACCOUNT}:identity/example.com"),
    ("sns", "publish"): ("sns:Publish", "arn:aws:sns:us-east-1:123456789012:x"),
    ("bedrock-agentcore", "invoke_agent_runtime"): ("bedrock-agentcore:InvokeAgentRuntime", _RT),
}
# AgentCore Memory: reachable only when RETAIL_OPS_MEMORY_ID is set, which no runtime gets (D7).
_MEMORY = {("bedrock-agentcore", "create_event"), ("bedrock-agentcore", "retrieve_memory_records")}


def _required_grant(svc: str, method: str) -> tuple[str, str]:
    try:
        return _GRANTS[(svc, method)]
    except KeyError:
        raise LookupError(f"no IAM mapping for {svc}.{method}: add it to the shared role and this map") from None


def _granted(policy: dict, action: str, resource: str) -> bool:
    for st in policy["Statement"]:
        actions = st["Action"] if isinstance(st["Action"], list) else [st["Action"]]
        resources = st["Resource"] if isinstance(st["Resource"], list) else [st["Resource"]]
        if st["Effect"] == "Allow" and "Condition" not in st and action in actions \
                and any(fnmatch.fnmatchcase(resource, r) for r in resources):
            return True
    return False


def _all_runtime_envs() -> list[dict[str, str]]:
    guard = {"RETAIL_OPS_GUARDRAIL_ID": "gr-1", "RETAIL_OPS_GUARDRAIL_VERSION": "1"}
    arns = {c: f"arn-{c}" for c in registry.AGENTS}
    return [deploy_agentcore.runtime_env(s, guardrail=guard, kb_id="KB1", callee_arns=arns)
            for s in registry.AGENTS.values()]


@pytest.mark.parametrize("code", CODES)
def test_the_shared_role_grants_every_call_in_the_bundle(code):
    policy = deploy_agentcore._exec_policy(ACCOUNT)
    calls = bundle.aws_calls(bundle.bundle_files(registry.AGENTS[code]))
    assert calls
    if calls & _MEMORY:   # accepted only because managed Memory is never configured on a runtime
        assert all("RETAIL_OPS_MEMORY_ID" not in env for env in _all_runtime_envs())
    for svc, method in sorted(calls - _MEMORY):
        action, resource = _required_grant(svc, method)
        assert _granted(policy, action, resource), f"{code}: {svc}.{method} needs {action} on {resource}"


def test_an_unmapped_call_fails_with_its_name():
    with pytest.raises(LookupError, match="start_code_interpreter_session"):
        _required_grant("bedrock-agentcore", "start_code_interpreter_session")


def test_the_journey_agent_may_invoke_only_this_projects_runtimes():
    assert ("bedrock-agentcore", "invoke_agent_runtime") in bundle.aws_calls(bundle.bundle_files(registry.AGENTS["A2"]))
    policy = deploy_agentcore._exec_policy(ACCOUNT)
    assert _granted(policy, "bedrock-agentcore:InvokeAgentRuntime", _RT)
    for other in ("vehicle_maintenance_agent-AbCdEf1234", "tire_ordering-AbCdEf1234", "retail_ops_agent-AbCdEf1234"):
        arn = f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:runtime/{other}"
        assert not _granted(policy, "bedrock-agentcore:InvokeAgentRuntime", arn), other
