#!/usr/bin/env python3
"""Deploy one Amazon Bedrock AgentCore runtime per custom agent (ADR 0009; code/S3 path).

No Docker, no ECR: each agent's code bundle (its own import closure from
app/agentcore/bundle.py plus Linux arm64 wheels for its dependency profile) is
zipped, uploaded to S3, and AgentCore runs it on the managed PYTHON_3_12
runtime, serving the HTTP contract (POST /invocations, GET /ping on 8080) that
app/agentcore/agent.py implements. The agent registry (app/agentcore/registry.py)
says which runtimes exist; A2 is deployed last because its environment carries
the ARNs of the agents it calls.

Creates, in the dev account:
  * IAM role   AmazonBedrockAgentCore-retail-ops-exec, shared by every runtime
               (logs, X-Ray, metrics, workload tokens, Bedrock, DynamoDB on
               retail-ops-<env>-*, the code bucket, the event bus, SES/SNS outreach,
               InvokeAgentRuntime on runtime/retail_ops_<env>_*)
  * S3 objects s3://bedrock-agentcore-retail-ops-<account>/agent/<runtime_name>.zip
  * Runtimes   retail_ops_<env>_<code>_<slug> for A1 A3 A4 A5 A7, then A2
               (codeConfiguration -> that S3 zip, HTTP, PUBLIC, no authorizer)

Dry run by default (read-only calls only). Usage:
  python3 -m app.infra.deploy_agentcore                  # plan for all six
  python3 -m app.infra.deploy_agentcore --agent A3,A2    # subset, still in dependency order
  python3 -m app.infra.deploy_agentcore --plan-json      # offline plan, no AWS call
  python3 -m app.infra.deploy_agentcore --print-policy   # offline: the shared role policy
  python3 -m app.infra.deploy_agentcore --build          # also build bundles + deps locally; uploads nothing
  python3 -m app.infra.deploy_agentcore --apply [--force] [--allow-no-guardrail]
  python3 -m app.infra.deploy_agentcore --invoke [--agent A1]   # kind "ping" to each runtime (read-only)
  python3 -m app.infra.deploy_agentcore --delete [--agent A5]   # lists what would be deleted
  python3 -m app.infra.deploy_agentcore --delete --apply --account <id> [--agent A5]
  python3 -m app.infra.deploy_agentcore --delete-legacy [--apply --account <id>]

Deletion only targets exact registry or legacy names, and never the shared
role or the code bucket (teardown.py removes those with the rest of the stack).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from app.agentcore import bundle, registry
    from app.infra import iam_roles, naming
except ImportError:  # run as a script from app/infra/
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.agentcore import bundle, registry
    from app.infra import iam_roles, naming

ROOT = Path(__file__).resolve().parents[2]
REGION = "us-east-1"
ENV = naming.ENV
EXEC_ROLE = naming.agentcore_exec_role()       # AmazonBedrockAgentCore-retail-ops-exec for dev
POLICY_NAME = "retail-ops-agentcore-exec"
RUNTIME = "PYTHON_3_12"
ENTRYPOINT = ["app/agentcore/agent.py"]   # the runtime launches this file; it starts the HTTP server
LEGACY_NAME = registry.LEGACY_RUNTIME_NAME
LEGACY_CODE_KEY = "agent/retail-ops-agent.zip"
ENV_PREFIX = naming.PREFIX
EVENT_BUS = naming.EVENT_BUS
BEDROCK_MODEL = "us.anthropic.claude-opus-4-5-20251101-v1:0"
DEPS_ROOT = Path("/tmp/retail_ops_deps")       # one subdirectory per dependency profile
CONFIG_PATH = naming.config_path("agentcore")
GUARDRAIL_CONFIG = naming.config_path("guardrail")
KB_CONFIG = naming.config_path("knowledge-base")
SCHEMA_VERSION = 2
NETWORK = {"networkMode": "PUBLIC"}
PROTOCOL = {"serverProtocol": "HTTP"}
LIFECYCLE = {"idleRuntimeSessionTimeout": 300, "maxLifetime": 3600}
READY_TIMEOUT_S = 300
RUNTIME_PATTERN = f"{naming.PREFIX_US}*"                    # retail_ops_<env>_*
_ACCOUNT = re.compile(r"^\d{12}$")
_ID_KEYS = ("RETAIL_OPS_GUARDRAIL_ID", "RETAIL_OPS_KNOWLEDGE_BASE_ID")
EM_AARCH64 = 0xB7


class DeployError(RuntimeError):
    """A runtime ended CREATE_FAILED / UPDATE_FAILED (fatal for the run)."""


class NotReadyError(RuntimeError):
    """A runtime did not reach READY within READY_TIMEOUT_S (recoverable)."""


def _aws_errors() -> tuple[type[BaseException], ...]:
    from botocore.exceptions import BotoCoreError, ClientError
    return (ClientError, BotoCoreError)


def _error_code(e: BaseException) -> str:
    return str((getattr(e, "response", None) or {}).get("Error", {}).get("Code", ""))


# --- names, trust and the shared policy ------------------------------------------------

def _bucket(account: str) -> str:
    return naming.agentcore_code_bucket(account)


def code_key(runtime_name: str) -> str:
    """The S3 key of one runtime's code bundle."""
    return f"agent/{runtime_name}.zip"


def _trust(account: str) -> dict:
    return {"Version": "2012-10-17", "Statement": [{
        "Sid": "AssumeRolePolicy", "Effect": "Allow",
        "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
        "Action": "sts:AssumeRole",
        "Condition": {"StringEquals": {"aws:SourceAccount": account},
                      "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{REGION}:{account}:*"}},
    }]}


def _workload_identities(account: str) -> list[str]:
    """The workload-identity directory plus one identity per runtime name (and the legacy one)."""
    base = f"arn:aws:bedrock-agentcore:{REGION}:{account}:workload-identity-directory/default"
    names = [*registry.runtime_names(), LEGACY_NAME]
    return [base] + [f"{base}/workload-identity/{name}-*" for name in names]


def _exec_policy(account: str) -> dict:
    """The shared execution role's inline policy (D5): every statement of the
    single runtime's role, plus Outreach (SES/SNS, copied from the worker role)
    and InvokeAgentRuntime on this project's per-agent runtimes."""
    worker_outreach = next(s for s in iam_roles.worker_policy(account)["Statement"] if s.get("Sid") == "Outreach")
    return {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": ["logs:DescribeLogStreams", "logs:CreateLogGroup",
                                       "logs:CreateLogStream", "logs:PutLogEvents", "logs:PutResourcePolicy",
                                       "logs:DescribeLogGroups"],
         "Resource": [f"arn:aws:logs:{REGION}:{account}:log-group:/aws/bedrock-agentcore/runtimes/*",
                      f"arn:aws:logs:{REGION}:{account}:log-group:*"]},
        {"Effect": "Allow", "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords",
                                       "xray:GetSamplingRules", "xray:GetSamplingTargets"], "Resource": "*"},
        {"Effect": "Allow", "Action": "cloudwatch:PutMetricData", "Resource": "*",
         "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}}},
        {"Sid": "GetAgentAccessToken", "Effect": "Allow",
         "Action": ["bedrock-agentcore:GetWorkloadAccessToken",
                    "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                    "bedrock-agentcore:GetWorkloadAccessTokenForUserId"],
         "Resource": _workload_identities(account)},
        # Scoped to what the agents invoke: the Claude family (every runtime's
        # RETAIL_OPS_BEDROCK_MODEL is a `us.anthropic.claude-*` cross-region
        # inference profile, hence the regionless foundation-model ARN and the
        # account's inference profiles), the guardrail Converse attaches
        # (ApplyGuardrail), and the knowledge base the KB-backed agents search
        # (Retrieve). No account-wide `bedrock:*` resource.
        {"Sid": "BedrockModelInvocation", "Effect": "Allow",
         "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream",
                    "bedrock:ApplyGuardrail", "bedrock:Retrieve"],
         "Resource": ["arn:aws:bedrock:*::foundation-model/anthropic.claude-*",
                      f"arn:aws:bedrock:*:{account}:inference-profile/*",
                      f"arn:aws:bedrock:*:{account}:guardrail-profile/*",
                      f"arn:aws:bedrock:{REGION}:{account}:guardrail/*",
                      f"arn:aws:bedrock:{REGION}:{account}:knowledge-base/*"]},
        # The Retail Ops app itself: read+write its DynamoDB tables + read code from S3.
        {"Sid": "RetailOpsDynamo", "Effect": "Allow",
         "Action": ["dynamodb:GetItem", "dynamodb:BatchGetItem", "dynamodb:Query", "dynamodb:Scan",
                    "dynamodb:PutItem", "dynamodb:UpdateItem"],
         "Resource": [f"arn:aws:dynamodb:{REGION}:{account}:table/{ENV_PREFIX}*",
                      f"arn:aws:dynamodb:{REGION}:{account}:table/{ENV_PREFIX}*/index/*"]},
        {"Sid": "RetailOpsCode", "Effect": "Allow", "Action": ["s3:GetObject"],
         "Resource": [f"arn:aws:s3:::{_bucket(account)}/*"]},
        # agent tools emit domain events (task.created, recommendation.shown) to the live bus
        {"Sid": "RetailOpsEvents", "Effect": "Allow", "Action": ["events:PutEvents"],
         "Resource": [f"arn:aws:events:{REGION}:{account}:event-bus/{EVENT_BUS}"]},
        # nurture-tick, service-followup-tick and A3's send_outreach send via SES/SNS
        dict(worker_outreach),
        # the Journey Agent calls the Discovery, Sales and Trade-In Agents (Sales Package)
        iam_roles._agent_runtime_stmt(account, patterns=(RUNTIME_PATTERN,)),
    ]}


# --- per-agent runtime environment --------------------------------------------------------

def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def guardrail_env(path: Path | None = None) -> dict[str, str]:
    """Guardrail id/version from guardrail-config.json (deploy_guardrails), or {}."""
    g = _read_json(path or GUARDRAIL_CONFIG)
    if not g.get("guardrail_id"):
        return {}
    return {"RETAIL_OPS_GUARDRAIL_ID": str(g["guardrail_id"]),
            "RETAIL_OPS_GUARDRAIL_VERSION": str(g.get("version") or "DRAFT")}


def knowledge_base_id(path: Path | None = None) -> str | None:
    """The Knowledge Base id from knowledge-base-config.json, or None."""
    kb = _read_json(path or KB_CONFIG).get("knowledge_base_id")
    return str(kb) if kb else None


def runtime_env(spec: registry.AgentSpec, *, guardrail: dict[str, str], kb_id: str | None,
                callee_arns: dict[str, str]) -> dict[str, str]:
    """One runtime's environment (design §6.3). The guardrail goes on every
    runtime, the KB id only where the agent searches knowledge, the runtime map
    only on agents that call others (restricted to their calls), and never
    RETAIL_OPS_MEMORY_ID (managed Memory stays off, D7)."""
    env = {"RETAIL_OPS_AGENT_ID": spec.code, "RETAIL_OPS_ENV": ENV, "RETAIL_OPS_ENV_PREFIX": ENV_PREFIX,
           "RETAIL_OPS_BEDROCK_MODEL": BEDROCK_MODEL,
           # the runtime IS the agent tier: generation on, guardrail attached
           "INTEGRATION_MODE": "aws", "RETAIL_OPS_EVENT_BUS_LIVE": "1", "RETAIL_OPS_EVENT_BUS": EVENT_BUS,
           **guardrail}
    # A3's send_outreach emails through SES: the sender is deploy configuration
    # (a verified identity), never a built-in default.
    ses_from = (os.environ.get("RETAIL_OPS_SES_FROM") or "").strip()
    if ses_from:
        env["RETAIL_OPS_SES_FROM"] = ses_from
    if spec.needs_kb and kb_id:
        env["RETAIL_OPS_KNOWLEDGE_BASE_ID"] = kb_id
    if spec.reasoning:
        env["RETAIL_OPS_A3_AGENT_FORCE"] = "1"
    calls = {code: callee_arns[code] for code in spec.calls if callee_arns.get(code)}
    if calls:
        env["RETAIL_OPS_AGENT_RUNTIMES"] = json.dumps(calls, separators=(",", ":"), sort_keys=True)
    return env


def _description(spec: registry.AgentSpec) -> str:
    what = "reasoning agent and tools" if spec.reasoning else "tools and jobs"
    return f"Retail Ops {spec.display}: {what} (ADR 0009)"


def _tags(spec: registry.AgentSpec) -> dict[str, str]:
    return {"project": "retail-ops", "env": ENV, "agent": spec.code}


def _tail(value: str) -> str:
    return f"...{value[-4:]}" if len(value) > 4 else value


def redact_env(env: dict[str, str]) -> str:
    """`KEY=value` pairs with ids and ARNs cut to their last 4 characters."""
    parts = []
    for key in sorted(env):
        value = env[key]
        if key in _ID_KEYS:
            value = _tail(value)
        elif key == "RETAIL_OPS_AGENT_RUNTIMES":
            arns = json.loads(value)
            value = "{" + ",".join(f"{c}:{_tail(a) if a.startswith('arn:') else a}"
                                   for c, a in sorted(arns.items())) + "}"
        parts.append(f"{key}={value}")
    return ", ".join(parts)


def select_agents(raw: str | None) -> list[registry.AgentSpec]:
    """`--agent A3,A2` -> those agents in deploy order (callees first); None -> all six."""
    codes = registry.parse_codes(raw) if raw else list(registry.AGENTS)
    return [registry.AGENTS[c] for c in registry.deploy_order() if c in codes]


def plan_json(agents: list[registry.AgentSpec]) -> dict[str, Any]:
    """The offline plan (no AWS call): names, bundles, env keys and A2A targets."""
    out = []
    for spec in agents:
        files = bundle.bundle_files(spec)
        env = runtime_env(spec, guardrail={"RETAIL_OPS_GUARDRAIL_ID": "<guardrail-id>",
                                           "RETAIL_OPS_GUARDRAIL_VERSION": "<version>"},
                          kb_id="<knowledge-base-id>", callee_arns={c: f"<{c} runtime arn>" for c in spec.calls})
        out.append({"code": spec.code, "name": spec.display, "runtime_name": spec.runtime_name,
                    "engine": spec.engine, "deps_profile": spec.deps, "requirements": bundle.PROFILES[spec.deps],
                    "code_key": code_key(spec.runtime_name), "entry_point": ENTRYPOINT[0],
                    "service_modules": bundle.service_module_count(files), "files": len(files),
                    "env_keys": sorted(env), "calls": list(spec.calls),
                    "tools": [t.name for t in spec.tools], "jobs": [j.name for j in spec.jobs],
                    "tags": _tags(spec), "description": _description(spec)})
    return {"schema_version": SCHEMA_VERSION, "region": REGION, "env": ENV, "account": "<acct>",
            "role": EXEC_ROLE, "bucket": _bucket("<acct>"), "deploy_order": [a["code"] for a in out],
            "agents": out}


# --- AWS reads (the dry run makes only these) ------------------------------------------

@dataclass
class Clients:
    """The AWS clients the deployer uses; built once in main(), fakes in tests."""
    sts: Any
    iam: Any
    s3: Any
    control: Any
    data: Any | None = None
    logs: Any | None = None


def list_runtimes(control: Any) -> list[dict[str, Any]]:
    """Every AgentCore runtime in the account (follows nextToken across pages)."""
    out: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        resp = control.list_agent_runtimes(maxResults=100, **({"nextToken": token} if token else {}))
        out.extend(resp.get("agentRuntimes", []))
        token = resp.get("nextToken")
        if not token:
            return out


def _role_exists(iam: Any) -> bool:
    try:
        iam.get_role(RoleName=EXEC_ROLE)
    except _aws_errors() as e:
        if _error_code(e) == "NoSuchEntity":
            return False
        raise
    return True


def _bucket_exists(s3: Any, bucket: str) -> bool:
    try:
        s3.head_bucket(Bucket=bucket)
    except _aws_errors() as e:
        if _error_code(e) in ("404", "NoSuchBucket", "NotFound"):
            return False
        raise
    return True


@dataclass
class AgentPlan:
    """One runtime in the plan: create, update, or an env-only update of a caller."""
    spec: registry.AgentSpec
    action: str                          # "create" | "update" | "env-only"
    existing: dict[str, Any] | None      # its ListAgentRuntimes entry when it exists
    files: list[str]
    reason: str = ""

    @property
    def label(self) -> str:
        if self.action == "env-only":
            return f"would update (env only: {self.reason})"
        return f"would {self.action}"


@dataclass
class Plan:
    account: str
    role_arn: str
    role_exists: bool
    bucket: str
    bucket_exists: bool
    agents: list[AgentPlan]
    found: dict[str, dict[str, Any]]     # registry runtime name -> ListAgentRuntimes entry
    legacy: dict[str, Any] | None
    guardrail: dict[str, str]
    kb_id: str | None


def _stale_callees(control: Any, caller: registry.AgentSpec, found: dict[str, dict[str, Any]],
                   creating: set[str]) -> str:
    """Why a deployed caller's runtime map is stale ('' when it is current)."""
    env = control.get_agent_runtime(agentRuntimeId=found[caller.runtime_name]["agentRuntimeId"]) \
        .get("environmentVariables") or {}
    try:
        wired = json.loads(env.get("RETAIL_OPS_AGENT_RUNTIMES") or "{}")
    except ValueError:
        wired = {}
    wired = wired if isinstance(wired, dict) else {}
    changed = []
    for code in caller.calls:
        now = (found.get(registry.AGENTS[code].runtime_name) or {}).get("agentRuntimeArn")
        if code in creating:
            changed.append(f"{code} ARN will change")
        elif (now or None) != (wired.get(code) or None):
            changed.append(f"{code} ARN changed")
    return ", ".join(changed)


def build_plan(clients: Clients, agents: list[registry.AgentSpec], account: str) -> Plan:
    """The deploy plan, from read-only calls: iam:GetRole, s3:HeadBucket,
    ListAgentRuntimes, and GetAgentRuntime for a deployed caller (stale-map check)."""
    bucket = _bucket(account)
    role_exists = _role_exists(clients.iam)
    bucket_exists = _bucket_exists(clients.s3, bucket)
    by_name = {r.get("agentRuntimeName"): r for r in list_runtimes(clients.control)}
    found = {a.runtime_name: by_name[a.runtime_name] for a in registry.AGENTS.values() if a.runtime_name in by_name}
    selected = {a.code for a in agents}
    plans = [AgentPlan(spec, "update" if spec.runtime_name in found else "create", found.get(spec.runtime_name),
                       bundle.bundle_files(spec))
             for spec in (registry.AGENTS[c] for c in registry.deploy_order() if c in selected)]
    creating = {p.spec.code for p in plans if p.action == "create"}
    for code in registry.deploy_order():          # callers that are not in this run but hold stale ARNs
        caller = registry.AGENTS[code]
        if caller.calls and code not in selected and caller.runtime_name in found:
            reason = _stale_callees(clients.control, caller, found, creating)
            if reason:
                plans.append(AgentPlan(caller, "env-only", found[caller.runtime_name],
                                       bundle.bundle_files(caller), reason))
    return Plan(account=account, role_arn=f"arn:aws:iam::{account}:role/{EXEC_ROLE}", role_exists=role_exists,
                bucket=bucket, bucket_exists=bucket_exists, agents=plans, found=found,
                legacy=by_name.get(LEGACY_NAME), guardrail=guardrail_env(), kb_id=knowledge_base_id())


def print_plan(plan: Plan, mode: str) -> None:
    order = " ".join(p.spec.code for p in plan.agents)
    print(f"[{mode}] one AgentCore runtime per agent (ADR 0009): {len(plan.agents)} runtime(s), order {order}")
    statements = len(_exec_policy(plan.account)["Statement"])
    print(f"  [role] {'exists' if plan.role_exists else 'would create'} {EXEC_ROLE} "
          f"(shared by every runtime; inline policy {POLICY_NAME}, {statements} statements)")
    print(f"  [s3] {'exists' if plan.bucket_exists else 'would create'} {plan.bucket}")
    known = {c: e["agentRuntimeArn"] for c, e in
             ((a.code, plan.found.get(a.runtime_name)) for a in registry.AGENTS.values()) if e}
    for p in plan.agents:
        spec = p.spec
        pending = {c: known.get(c) or "pending" for c in spec.calls}   # created earlier in the same run
        env = runtime_env(spec, guardrail=plan.guardrail, kb_id=plan.kb_id, callee_arns=pending)
        a2a = ", ".join(f"{c} {registry.AGENTS[c].runtime_name}" for c in spec.calls) or "-"
        print(f"  [runtime] {spec.code} {spec.runtime_name}: {p.label} | role {EXEC_ROLE} | "
              f"code {code_key(spec.runtime_name)} | deps {spec.deps} | "
              f"{bundle.service_module_count(p.files)} service modules ({len(p.files)} files) | "
              f"env {redact_env(env)} | A2A {a2a}")
    if plan.legacy:
        print(f"  [runtime] {LEGACY_NAME} ({plan.legacy.get('agentRuntimeId')}): legacy (left untouched)")
    if not plan.guardrail:
        print("  [guardrail] guardrail-config.json not found: --apply refuses to deploy without it "
              "(every model call carries the guardrail, ADR 0003) unless --allow-no-guardrail")


# --- deps, bundles and the arm64 check ----------------------------------------------------

def build_deps_pip(profile: str, deps_root: Path) -> Path:
    """pip-install one dependency profile into deps_root/<profile> for vendoring.
    AgentCore Runtime is **Linux arm64** and validates every .so's ELF header, so
    wheels are resolved for aarch64-manylinux2014 / cp312 (binary-only); a
    pure-Python fallback is not attempted because a mismatched binary fails the
    whole update. The only function that spawns pip."""
    req = ROOT / bundle.PROFILES[profile]
    dest = deps_root / profile
    stamp = dest / ".requirements"
    if (dest / "boto3").is_dir() and stamp.exists() and stamp.read_text() == req.read_text():
        return dest  # already built for these requirements
    print(f"  [deps] vendoring {req.name} into {dest} ...")
    shutil.rmtree(dest, ignore_errors=True)
    cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--target", str(dest), "-r", str(req),
           "--platform", "manylinux2014_aarch64", "--implementation", "cp", "--python-version", "3.12",
           "--only-binary=:all:", "--upgrade"]
    r = subprocess.run(cmd, check=False)
    if r.returncode != 0:
        raise SystemExit(f"  [deps] arm64 wheel resolution failed: a dependency in {req.name} has no aarch64 "
                         f"wheel; fix {req.name} rather than shipping x86 binaries")
    for pyc in dest.rglob("__pycache__"):    # never ship bytecode compiled on the build machine
        shutil.rmtree(pyc, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    stamp.write_text(req.read_text())
    return dest


def check_arm64(deps_dir: Path) -> list[Path]:
    """Every shared object in deps_dir that is not an ELF EM_AARCH64 binary."""
    bad: list[Path] = []
    for so in sorted(deps_dir.rglob("*")):
        if not so.is_file() or not (so.name.endswith(".so") or ".so." in so.name):
            continue
        with so.open("rb") as f:
            head = f.read(20)
        if len(head) < 20 or head[:4] != b"\x7fELF":
            bad.append(so)
            continue
        order = "little" if head[5] == 1 else "big"
        if int.from_bytes(head[18:20], order) != EM_AARCH64:
            bad.append(so)
    return bad


def _arm64_or_exit(profile: str, deps_dir: Path) -> None:
    bad = check_arm64(deps_dir)
    if bad:
        names = ", ".join(str(p.relative_to(deps_dir)) for p in bad[:10])
        raise SystemExit(f"  [deps] not Linux arm64 (AgentCore fails the update with no logs): {names}; "
                         f"fix {bundle.PROFILES[profile]} rather than shipping x86 binaries")


def _git_commit(root: Path = ROOT) -> str | None:
    """HEAD's commit id, read from the git metadata (no subprocess); None outside a checkout."""
    try:
        git = root / ".git"
        if git.is_file():                       # a worktree: ".git" holds "gitdir: <path>"
            git = (root / git.read_text().split(":", 1)[1].strip()).resolve()
        head = (git / "HEAD").read_text().strip()
        if not head.startswith("ref: "):
            return head or None
        ref = head[5:]
        common = git
        if (git / "commondir").is_file():
            common = (git / (git / "commondir").read_text().strip()).resolve()
        for base in (git, common):
            if (base / ref).is_file():
                return (base / ref).read_text().strip()
        packed = common / "packed-refs"
        for line in packed.read_text().splitlines() if packed.is_file() else []:
            sha, _, name = line.partition(" ")
            if name == ref:
                return sha
    except (OSError, IndexError):
        return None
    return None


# --- apply ----------------------------------------------------------------------------

@dataclass
class ApplyResult:
    done: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    not_ready: list[str] = field(default_factory=list)
    not_attempted: list[str] = field(default_factory=list)
    arns: dict[str, str] = field(default_factory=dict)
    arns_changed: bool = False
    exit_code: int = 0


def read_config(path: Path) -> dict[str, Any]:
    """agentcore-config.json, or {} when missing or unreadable (v1 files keep no agent entries)."""
    return _read_json(path)


def _previous_agents(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    agents = cfg.get("agents") if cfg.get("schema_version") == SCHEMA_VERSION else None
    return {c: e for c, e in agents.items() if isinstance(e, dict)} if isinstance(agents, dict) else {}


def write_config(path: Path, previous: dict[str, Any], reached: dict[str, dict[str, Any]],
                 legacy: dict[str, Any] | None, *, drop: tuple[str, ...] = ()) -> dict[str, Any]:
    """Write config v2 atomically (`.tmp`, then os.replace): the agents that
    reached READY in this run merged over the previous entries, minus `drop`,
    plus a `legacy` block while the old runtime exists."""
    agents = {**_previous_agents(previous), **reached}
    cfg: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "region": REGION, "env": ENV,
                           "agents": {c: agents[c] for c in registry.AGENTS if c in agents and c not in drop}}
    if legacy:
        cfg["legacy"] = {"agent_runtime_name": LEGACY_NAME, "agent_runtime_arn": legacy.get("agentRuntimeArn")}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n")
    os.replace(tmp, path)
    return cfg


def ensure_role(iam: Any, account: str, *, exists: bool, sleep: Callable[[float], None] = time.sleep) -> str:
    """Create (if absent) the shared execution role and set its trust and inline policy."""
    arn = f"arn:aws:iam::{account}:role/{EXEC_ROLE}"
    if not exists:
        iam.create_role(RoleName=EXEC_ROLE, AssumeRolePolicyDocument=json.dumps(_trust(account)),
                        Description="Retail Ops AgentCore Runtime execution role (shared by the per-agent runtimes)")
        print(f"  [role] created {EXEC_ROLE}")
    else:
        iam.update_assume_role_policy(RoleName=EXEC_ROLE, PolicyDocument=json.dumps(_trust(account)))
    iam.put_role_policy(RoleName=EXEC_ROLE, PolicyName=POLICY_NAME, PolicyDocument=json.dumps(_exec_policy(account)))
    print("  [role] inline policy set")
    if not exists:
        print("  [role] waiting for propagation ...")
        sleep(10)
    return arn


def _wait_ready(control: Any, runtime_id: str, name: str, *, timeout_s: int = READY_TIMEOUT_S,
                sleep: Callable[[float], None] = time.sleep) -> str | None:
    """Poll until READY; DeployError on *_FAILED, NotReadyError on timeout. Returns the version."""
    for _ in range(max(1, timeout_s // 5)):
        try:
            cur = control.get_agent_runtime(agentRuntimeId=runtime_id)
        except _aws_errors():
            cur = {}
        st = cur.get("status")
        print(f"    {name} status={st}")
        if st in ("READY", "ACTIVE"):
            return cur.get("agentRuntimeVersion")
        if st in ("CREATE_FAILED", "UPDATE_FAILED", "FAILED"):
            raise DeployError(f"{name} {st}: {cur.get('failureReason') or 'no reason given'}; "
                              f"see /aws/bedrock-agentcore/runtimes/{name}-*")
        sleep(5)
    raise NotReadyError(name)


def _artifact(bucket: str, key: str) -> dict[str, Any]:
    return {"codeConfiguration": {"code": {"s3": {"bucket": bucket, "prefix": key}},
                                  "runtime": RUNTIME, "entryPoint": ENTRYPOINT}}


def _deploy_one(clients: Clients, plan: Plan, ap: AgentPlan, role_arn: str, deps: dict[str, Path],
                callee_arns: dict[str, str], previous: dict[str, dict[str, Any]], *, force: bool,
                git_commit: str | None, sleep: Callable[[float], None]) -> tuple[dict[str, Any], bool]:
    """Create or update one runtime. Returns its config entry and whether it changed."""
    spec, name = ap.spec, ap.spec.runtime_name
    env = runtime_env(spec, guardrail=plan.guardrail, kb_id=plan.kb_id, callee_arns=callee_arns)
    prev = previous.get(spec.code) or {}
    settings = {"roleArn": role_arn, "networkConfiguration": NETWORK, "protocolConfiguration": PROTOCOL,
                "lifecycleConfiguration": LIFECYCLE, "environmentVariables": env, "description": _description(spec)}
    existing = ap.existing
    if ap.action == "env-only" and existing:
        cur = clients.control.get_agent_runtime(agentRuntimeId=existing["agentRuntimeId"])
        sha = prev.get("code_sha256")
        if (cur.get("environmentVariables") or {}) == env:
            print(f"  [runtime] {spec.code} {name} unchanged (env)")
            return _entry(spec, existing["agentRuntimeArn"], role_arn, sha, cur.get("agentRuntimeVersion")), False
        resp = clients.control.update_agent_runtime(agentRuntimeId=existing["agentRuntimeId"],
                                                    agentRuntimeArtifact=cur["agentRuntimeArtifact"], **settings)
        print(f"  [runtime] {spec.code} updated {name} (env only: {ap.reason})")
        version = _wait_ready(clients.control, existing["agentRuntimeId"], name, sleep=sleep)
        return _entry(spec, existing["agentRuntimeArn"], role_arn, sha,
                      version or resp.get("agentRuntimeVersion")), True
    data, info = bundle.build_zip(spec, deps[spec.deps], requirements=ROOT / bundle.PROFILES[spec.deps],
                                  git_commit=git_commit)
    sha, key = info["code_sha256"], code_key(name)
    same_code = prev.get("code_sha256") == sha
    if existing and same_code and not force:
        cur = clients.control.get_agent_runtime(agentRuntimeId=existing["agentRuntimeId"])
        if (cur.get("environmentVariables") or {}) == env:
            print(f"  [runtime] {spec.code} {name} unchanged (code {sha[:12]} and env); no new version")
            return _entry(spec, existing["agentRuntimeArn"], role_arn, sha, cur.get("agentRuntimeVersion")), False
    if force or not same_code or not existing:
        clients.s3.put_object(Bucket=plan.bucket, Key=key, Body=data)
        print(f"  [s3] uploaded s3://{plan.bucket}/{key} ({len(data)} bytes, code {sha[:12]})")
    if existing:
        resp = clients.control.update_agent_runtime(agentRuntimeId=existing["agentRuntimeId"],
                                                    agentRuntimeArtifact=_artifact(plan.bucket, key), **settings)
        runtime_id, arn = existing["agentRuntimeId"], existing["agentRuntimeArn"]
        print(f"  [runtime] {spec.code} updated {name} ({runtime_id})")
    else:
        resp = clients.control.create_agent_runtime(agentRuntimeName=name,
                                                    agentRuntimeArtifact=_artifact(plan.bucket, key),
                                                    tags=_tags(spec), **settings)
        runtime_id, arn = resp["agentRuntimeId"], resp["agentRuntimeArn"]
        print(f"  [runtime] {spec.code} created {name} -> {arn}")
    version = _wait_ready(clients.control, runtime_id, name, sleep=sleep)
    return _entry(spec, arn, role_arn, sha, version or resp.get("agentRuntimeVersion")), True


def _entry(spec: registry.AgentSpec, arn: str, role_arn: str, sha: str | None, version: Any) -> dict[str, Any]:
    return {"name": spec.display, "runtime_name": spec.runtime_name, "runtime_arn": arn, "role_arn": role_arn,
            "engine": spec.engine, "code_sha256": sha, "version": None if version is None else str(version)}


def run_apply(clients: Clients, plan: Plan, *, force: bool, allow_no_guardrail: bool, config_path: Path,
              deps_root: Path, build_deps: Callable[[str, Path], Path] = build_deps_pip,
              sleep: Callable[[float], None] = time.sleep, git_commit: str | None = None) -> ApplyResult:
    """Deploy the plan: guardrail check, deps per profile (arm64-checked), the
    shared role, the bucket, then each runtime in order. A failure stops the run;
    the config keeps every agent that reached READY."""
    if not plan.guardrail and not allow_no_guardrail:
        raise SystemExit("  [guardrail] guardrail-config.json is missing: every model call carries the guardrail "
                         "(ADR 0003); run the guardrails stage first, or pass --allow-no-guardrail")
    deps: dict[str, Path] = {}
    for profile in sorted({p.spec.deps for p in plan.agents if p.action != "env-only"}):
        deps[profile] = build_deps(profile, deps_root)
        _arm64_or_exit(profile, deps[profile])
    previous_cfg = read_config(config_path)
    previous = _previous_agents(previous_cfg)
    result = ApplyResult()
    reached: dict[str, dict[str, Any]] = {}
    callee_arns = {a.code: plan.found[a.runtime_name]["agentRuntimeArn"]
                   for a in registry.AGENTS.values() if a.runtime_name in plan.found}
    try:
        role_arn = ensure_role(clients.iam, plan.account, exists=plan.role_exists, sleep=sleep)
        if not plan.bucket_exists:
            clients.s3.create_bucket(Bucket=plan.bucket)    # us-east-1: no LocationConstraint
            print(f"  [s3] created {plan.bucket}")
    except _aws_errors() as e:
        print(f"  ERROR shared role / bucket: {type(e).__name__}: {str(e)[:200]}")
        result.not_attempted = [p.spec.code for p in plan.agents]
        result.exit_code = 1
        _summary(result)
        return result
    for i, ap in enumerate(plan.agents):
        code = ap.spec.code
        try:
            entry, changed = _deploy_one(clients, plan, ap, role_arn, deps, callee_arns, previous, force=force,
                                         git_commit=git_commit, sleep=sleep)
        except NotReadyError:
            print(f"  WARNING {ap.spec.runtime_name} is not READY after {READY_TIMEOUT_S} s; its ARN is not "
                  "written this run: re-run --apply once the runtime is READY")
            result.not_ready.append(code)
            callee_arns.pop(code, None)                 # callers do not wire a runtime that is not READY
            result.exit_code = 1
            continue
        except (DeployError, *_aws_errors()) as e:
            print(f"  ERROR {code} {ap.spec.runtime_name}: {type(e).__name__}: {str(e)[:300]}")
            result.failed.append(code)
            result.not_attempted = [p.spec.code for p in plan.agents[i + 1:]]
            result.exit_code = 1
            break
        reached[code] = entry
        callee_arns[code] = entry["runtime_arn"]
        result.arns[code] = entry["runtime_arn"]
        try:   # verify.sh freshness: which source this runtime runs (also when it was unchanged)
            from app.infra import freshness
            freshness.stamp_runtime(clients.control, entry["runtime_arn"], ap.spec)
        except Exception as e:  # noqa: BLE001 - an unstamped runtime is reported by freshness, not fatal here
            print(f"  WARNING {code}: source stamp not written ({type(e).__name__}); freshness will report it")
        (result.done if changed else result.unchanged).append(code)
        if ap.action == "env-only" and changed:
            result.arns_changed = True
        if (previous.get(code) or {}).get("runtime_arn") != entry["runtime_arn"]:
            result.arns_changed = True
    write_config(config_path, previous_cfg, reached, plan.legacy)
    print(f"  wrote {_rel(config_path)} (schema {SCHEMA_VERSION}; deploy_l4 and deploy_l6 wire the runtime map)")
    _summary(result)
    if result.arns_changed:
        print("  runtime ARNs changed: re-run `deploy_l4 --apply` and `deploy_l6 --apply` "
              "(deploy/deploy.sh --apply does both after this stage)")
    return result


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _summary(result: ApplyResult) -> None:
    print("  summary:")
    for label, codes in (("done", result.done), ("unchanged", result.unchanged), ("failed", result.failed),
                         ("not READY", result.not_ready), ("not attempted", result.not_attempted)):
        if codes:
            print(f"    {label:14s} {' '.join(codes)}")


def build_local(plan: Plan, *, deps_root: Path, build_deps: Callable[[str, Path], Path] = build_deps_pip) -> None:
    """`--build`: build deps and bundles locally and report their sizes; uploads nothing."""
    deps: dict[str, Path] = {}
    for ap in plan.agents:
        profile = ap.spec.deps
        if profile not in deps:
            deps[profile] = build_deps(profile, deps_root)
            _arm64_or_exit(profile, deps[profile])
        data, info = bundle.build_zip(ap.spec, deps[profile], requirements=ROOT / bundle.PROFILES[profile])
        print(f"  [build] {ap.spec.runtime_name}: {len(data) / 1e6:.1f} MB zipped, code {info['code_sha256'][:12]}, "
              f"{len(info['deps'])} distributions")


# --- ping, delete, legacy -------------------------------------------------------------

def invoke_ping(clients: Clients, agents: list[registry.AgentSpec]) -> int:
    """Send {"kind": "ping"} to each runtime (read-only: no claims, no data access)."""
    found = {r.get("agentRuntimeName"): r for r in list_runtimes(clients.control)}
    failures = 0
    for spec in sorted(agents, key=lambda a: list(registry.AGENTS).index(a.code)):
        r = found.get(spec.runtime_name)
        if not r:
            print(f"  [ping] {spec.code} {spec.runtime_name}: not deployed")
            failures += 1
            continue
        try:
            resp = clients.data.invoke_agent_runtime(
                agentRuntimeArn=r["agentRuntimeArn"], runtimeSessionId=f"{spec.code}-ping-{uuid.uuid4().hex}",
                contentType="application/json", accept="application/json",
                payload=json.dumps({"kind": "ping"}).encode())
            body = resp["response"]
            raw = body.read() if hasattr(body, "read") else body
            answer = json.loads(raw)
        except (*_aws_errors(), KeyError, ValueError) as e:
            print(f"  [ping] {spec.code} {spec.runtime_name}: FAILED {type(e).__name__}: {str(e)[:200]}")
            failures += 1
            continue
        result = answer.get("result") if isinstance(answer, dict) else None
        result = result if isinstance(result, dict) else {}
        ok = answer.get("statusCode") == 200 and result.get("agent") == spec.code
        failures += 0 if ok else 1
        print(f"  [ping] {spec.code} {spec.runtime_name}: {'OK' if ok else 'FAILED'} "
              f"agent={result.get('agent')} version={answer.get('version')}")
    return 1 if failures else 0


def _delete_object(s3: Any, bucket: str, key: str) -> None:
    try:
        s3.delete_object(Bucket=bucket, Key=key)
    except _aws_errors() as e:
        if _error_code(e) not in ("NoSuchBucket", "NoSuchKey", "404"):
            raise


def delete_runtimes(clients: Clients, agents: list[registry.AgentSpec], account: str, *, apply: bool,
                    config_path: Path) -> int:
    """List (dry run) or delete the named registry runtimes and their code objects."""
    bucket = _bucket(account)
    found = {r.get("agentRuntimeName"): r for r in list_runtimes(clients.control)}
    verb = "deleting" if apply else "would delete"
    deleted: list[str] = []
    for spec in agents:
        r = found.get(spec.runtime_name)
        if r:
            print(f"  [delete] {verb} runtime {spec.runtime_name} ({r.get('agentRuntimeId')})")
            if apply:
                clients.control.delete_agent_runtime(agentRuntimeId=r["agentRuntimeId"])
                deleted.append(spec.code)
        else:
            print(f"  [delete] runtime {spec.runtime_name}: not deployed")
        print(f"  [delete] {verb} code object s3://{bucket}/{code_key(spec.runtime_name)} (if present)")
        if apply:
            _delete_object(clients.s3, bucket, code_key(spec.runtime_name))
    print(f"  [delete] kept: role {EXEC_ROLE} and bucket {bucket} (teardown.py removes them with the stack)")
    if apply and deleted:
        cfg = read_config(config_path)
        write_config(config_path, cfg, {}, found.get(LEGACY_NAME), drop=tuple(deleted))
        print("  runtime ARNs changed: re-run `deploy_l4 --apply` and `deploy_l6 --apply`; until then the "
              "Lambdas fall back to in-process calls for the deleted agents")
    if not apply:
        print("  (dry run: nothing deleted; add --apply --account <id>)")
    return 0


def _list_workload_identities(control: Any) -> list[str]:
    names: list[str] = []
    token: str | None = None
    while True:
        resp = control.list_workload_identities(maxResults=100, **({"nextToken": token} if token else {}))
        names += [w.get("name", "") for w in resp.get("workloadIdentities", [])]
        token = resp.get("nextToken")
        if not token:
            return names


def _legacy_log_groups(logs: Any | None) -> list[str]:
    if logs is None:
        return []
    prefix, names, token = f"/aws/bedrock-agentcore/runtimes/{LEGACY_NAME}-", [], None
    while True:
        resp = logs.describe_log_groups(logGroupNamePrefix=prefix, **({"nextToken": token} if token else {}))
        names += [g["logGroupName"] for g in resp.get("logGroups", [])]
        token = resp.get("nextToken")
        if not token:
            return names


def delete_legacy(clients: Clients, account: str, *, apply: bool, config_path: Path) -> int:
    """List (dry run) or delete the single legacy runtime's items (design §10.1)."""
    bucket = _bucket(account)
    legacy = next((r for r in list_runtimes(clients.control) if r.get("agentRuntimeName") == LEGACY_NAME), None)
    verb = "deleting" if apply else "would delete"
    if legacy:
        print(f"  [legacy] {verb} runtime {LEGACY_NAME} ({legacy.get('agentRuntimeId')})")
    else:
        print(f"  [legacy] runtime {LEGACY_NAME}: not deployed")
    print(f"  [legacy] {verb} code object s3://{bucket}/{LEGACY_CODE_KEY} (if present)")
    wids = [w for w in _list_workload_identities(clients.control) if w.startswith(f"{LEGACY_NAME}-")]
    for w in wids:
        print(f"  [legacy] {verb} workload identity {w} (if still present after the runtime)")
    for g in _legacy_log_groups(clients.logs):
        print(f"  [legacy] kept: log group {g} (teardown.py removes it with the stack)")
    print(f"  [legacy] kept: role {EXEC_ROLE} (shared by the per-agent runtimes)")
    if not apply:
        print("  (dry run: nothing deleted; add --apply --account <id>)")
        return 0
    if legacy:
        clients.control.delete_agent_runtime(agentRuntimeId=legacy["agentRuntimeId"])
    _delete_object(clients.s3, bucket, LEGACY_CODE_KEY)
    for w in [w for w in _list_workload_identities(clients.control) if w.startswith(f"{LEGACY_NAME}-")]:
        clients.control.delete_workload_identity(name=w)
        print(f"  [legacy] deleted workload identity {w}")
    if config_path.exists():
        write_config(config_path, read_config(config_path), {}, None)
        print(f"  [legacy] removed the legacy block from {_rel(config_path)}")
    return 0


# --- CLI ------------------------------------------------------------------------------

def main(argv: list[str] | None = None, *, session: Any | None = None) -> int:
    ap = argparse.ArgumentParser(description="Deploy one AgentCore runtime per agent (code/S3; ADR 0009).")
    ap.add_argument("--apply", action="store_true", help="create/update (default: dry run)")
    ap.add_argument("--agent", help="comma list of agent codes (default: all); deploy order still respects calls")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--plan-json", action="store_true", help="offline plan as JSON (no AWS call)")
    mode.add_argument("--print-policy", action="store_true", help="offline: print the shared exec-role policy")
    mode.add_argument("--invoke", action="store_true", help='send {"kind": "ping"} to each runtime (read-only)')
    mode.add_argument("--delete", action="store_true", help="list (or with --apply delete) the runtimes")
    mode.add_argument("--delete-legacy", action="store_true", help=f"list (or with --apply delete) {LEGACY_NAME}")
    ap.add_argument("--build", action="store_true", help="also build bundles + deps locally (dry run uploads nothing)")
    ap.add_argument("--force", action="store_true", help="upload and update even when the code is unchanged")
    ap.add_argument("--allow-no-guardrail", action="store_true", help="deploy without guardrail-config.json")
    ap.add_argument("--account", help="required with --delete/--delete-legacy --apply: the caller's account id")
    args = ap.parse_args(argv)
    try:
        agents = select_agents(args.agent)
    except ValueError as e:
        ap.error(str(e))
    if args.account is not None and not _ACCOUNT.fullmatch(args.account):
        ap.error("--account must be a 12-digit AWS account id")

    if args.plan_json:
        print(json.dumps(plan_json(agents), indent=2))
        return 0
    if args.print_policy:
        print(json.dumps(_exec_policy("<acct>"), indent=2))
        return 0

    if session is None:
        import boto3
        session = boto3.Session(region_name=REGION)
    clients = Clients(sts=session.client("sts"), iam=session.client("iam"), s3=session.client("s3"),
                      control=session.client("bedrock-agentcore-control"))
    account = clients.sts.get_caller_identity()["Account"]
    print(f"account={account} region={REGION} runtime={RUNTIME}")
    if (args.delete or args.delete_legacy) and args.apply and args.account != account:
        raise SystemExit(f"--apply with --delete/--delete-legacy needs --account {account} "
                         "(the caller's account, guarding against the wrong credentials)")

    if args.invoke:
        from botocore.config import Config
        clients.data = session.client("bedrock-agentcore", config=Config(connect_timeout=3, read_timeout=60,
                                                                         retries={"max_attempts": 1}))
        return invoke_ping(clients, agents)
    if args.delete:
        return delete_runtimes(clients, agents, account, apply=args.apply, config_path=CONFIG_PATH)
    if args.delete_legacy:
        clients.logs = session.client("logs")
        return delete_legacy(clients, account, apply=args.apply, config_path=CONFIG_PATH)

    label = "APPLY" if args.apply else "DRY-RUN"
    plan = build_plan(clients, agents, account)
    print_plan(plan, label)
    if not args.apply:
        if args.build:
            build_local(plan, deps_root=DEPS_ROOT, build_deps=build_deps_pip)
        creates = sum(1 for p in plan.agents if p.action == "create")
        print(f"[DRY-RUN] done: {creates} would create, {len(plan.agents) - creates} would update; "
              "nothing changed (re-run with --apply)")
        return 0
    result = run_apply(clients, plan, force=args.force, allow_no_guardrail=args.allow_no_guardrail,
                       config_path=CONFIG_PATH, deps_root=DEPS_ROOT, build_deps=build_deps_pip,
                       git_commit=_git_commit())
    print(f"[APPLY] done (exit {result.exit_code}).")
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
