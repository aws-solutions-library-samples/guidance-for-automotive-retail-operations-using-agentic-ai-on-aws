"""The environment name (app/infra/naming.py): dev keeps every name, other envs fit AWS limits.

`tests/golden/naming_dev.json` is the output of tests/app/naming_collect.py run
against the deployers BEFORE the env name became configurable (every name was a
`retail-ops-dev-` literal then), including the IAM policies, so the default env
must reproduce it byte for byte: the live dev stack keeps every name.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from app.infra import naming
from app.services.common import envname

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests" / "golden" / "naming_dev.json"
LONG_ENV = "abcdefgh"            # the longest env name naming accepts


def _collect(env: str | None, *extra: str) -> dict:
    run_env = {k: v for k, v in os.environ.items()
               if k not in ("RETAIL_OPS_ENV", "RETAIL_OPS_ENV_PREFIX", "RETAIL_OPS_EVENT_BUS")}
    if env is not None:
        run_env["RETAIL_OPS_ENV"] = env
    out = subprocess.run([sys.executable, "-m", "tests.app.naming_collect", *extra], cwd=ROOT, env=run_env,
                         capture_output=True, text=True, check=True, timeout=120)
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def long_names() -> dict:
    return _collect(LONG_ENV, "--extras")


# --- dev is unchanged -------------------------------------------------------------------

@pytest.mark.parametrize("env", [None, "", "dev"])
def test_default_env_reproduces_the_pre_change_names(env):
    assert _collect(env) == json.loads(GOLDEN.read_text())


def test_golden_spot_checks():
    g = json.loads(GOLDEN.read_text())
    assert g["deploy_l4.API_NAME"] == "retail-ops-dev-journeys-api"
    assert g["deploy_lake.DATABASE"] == "retail_ops_dev_lake"
    assert g["deploy_events.BUS_NAME"] == "app-bus-dev"
    assert g["deploy_agentcore.EXEC_ROLE"] == "AmazonBedrockAgentCore-retail-ops-exec"
    assert g["deploy_agentcore.bucket"] == "bedrock-agentcore-retail-ops-123456789012"
    assert g["registry.runtime_names"][0] == "retail_ops_dev_a1_discovery"
    assert g["deploy_lake.CONFIG_PATH"] == "lake-config.json"


def test_dev_cognito_domain_prefix_is_unchanged():
    from app.infra import cognito_setup
    # the inline rule before the change: f"retail-ops-dev-{pool_id.split('_')[-1].lower()}"
    assert cognito_setup.default_domain_prefix("us-east-1_AbCdEfGh1") == "retail-ops-dev-abcdefgh1"


# --- a custom env renames everything --------------------------------------------------------

def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _strings(v)]
    return []


def test_custom_env_leaves_no_dev_name(long_names):
    leftovers = {k: v for k, v in long_names.items() if k != "deploy_l4.STAGE"
                 and any(re.search(r"retail-ops-dev|retail_ops_dev|app-bus-dev|-config\.json$", s) for s in _strings(v))}
    assert leftovers == {}
    assert long_names["deploy_l4.STAGE"] == "dev"            # inside the env-named API; documented as fixed


def test_custom_env_names_carry_the_env(long_names):
    assert long_names["deploy_agentcore.EXEC_ROLE"] == f"AmazonBedrockAgentCore-retail-ops-{LONG_ENV}-exec"
    assert long_names["deploy_agentcore.bucket"] == f"bedrock-agentcore-retail-ops-{LONG_ENV}-123456789012"
    assert long_names["deploy_events.BUS_NAME"] == f"app-bus-{LONG_ENV}"
    assert long_names["deploy_lake.DATABASE"] == f"retail_ops_{LONG_ENV}_lake"
    assert long_names["deploy_lake.CONFIG_PATH"] == f"lake-config.{LONG_ENV}.json"
    assert long_names["services.ddb.DEFAULT_ENV_PREFIX"] == f"retail-ops-{LONG_ENV}-"
    assert long_names["services.bus.BUS_NAME"] == f"app-bus-{LONG_ENV}"
    assert long_names["teardown.runtime_names_for"][1:] == long_names["registry.runtime_names"]
    assert all(n.startswith(f"retail_ops_{LONG_ENV}_") for n in long_names["registry.runtime_names"])
    # AWS Quick ids stay env-less (one Quick deployment per account)
    assert long_names["deploy_quick_datasets.DATA_SOURCE_ID"] == "retail-ops-lake"


# --- AWS name limits with the longest env ---------------------------------------------------

def _limit(names: list[str], max_len: int, pattern: str | None = None) -> list[str]:
    return [n for n in names if len(n) > max_len or (pattern and not re.fullmatch(pattern, n))]


def test_long_env_names_fit_aws_limits(long_names):
    n = long_names
    lambdas = [n["deploy_l4.AUTH_FN"], n["deploy_l4.SVC_FN"], n["deploy_events.SUB_FN"],
               n["deploy_approvals.SUBMIT_FN"], n["deploy_gateway.GW_LAMBDA"], n["deploy_streaming.STREAM_FN"],
               *n["iam_roles.WORKER_FUNCTIONS"], *n["deploy_alarms.FUNCTIONS"]]
    roles = [n[k] for k in ("iam_roles.ROLE_AUTHORIZER", "iam_roles.ROLE_SERVICE", "iam_roles.ROLE_WORKER",
                            "iam_roles.ROLE_SUBSCRIBER", "iam_roles.ROLE_APPROVALS", "iam_roles.ROLE_STREAM",
                            "iam_roles.LEGACY_SHARED_ROLE", "deploy_l6.SCHED_ROLE", "deploy_approvals.SFN_ROLE",
                            "deploy_lake.FIREHOSE_ROLE", "deploy_lake.EVENTS_ROLE", "deploy_knowledge_base.ROLE",
                            "deploy_gateway.GW_LAMBDA_ROLE", "deploy_gateway.GW_ROLE", "deploy_agentcore.EXEC_ROLE")]
    buckets = [n["deploy_lake.bucket"], n["deploy_knowledge_base.vector_bucket"], n["deploy_console.bucket"],
               n["deploy_agentcore.bucket"]]
    schedules = [s[0] for s in n["deploy_l6.schedules"]]
    alarms = n["deploy_alarms.alarms"] + [a[0] for a in n["deploy_alarms.runtime_unknown"]]
    policies = [p[0] for k, p in n.items() if k.startswith("iam_roles.policy[")]
    assert _limit(lambdas, 64, r"[A-Za-z0-9_-]+") == []                                  # Lambda FunctionName
    assert _limit(roles, 64, r"[\w+=,.@-]+") == []                                       # IAM role name
    assert _limit(policies, 128, r"[\w+=,.@-]+") == []                                   # IAM inline policy name
    assert _limit(buckets, 63, r"[a-z0-9][a-z0-9.-]*[a-z0-9]") == []                     # S3 bucket name
    assert _limit(n["registry.runtime_names"], 48, r"[a-zA-Z][a-zA-Z0-9_]{0,47}") == []  # AgentCore runtime
    assert _limit([n["deploy_memory.API_NAME"]], 48, r"[a-zA-Z][a-zA-Z0-9_]{0,47}") == []  # AgentCore memory
    assert _limit([n["deploy_gateway.GW_NAME"], n["deploy_gateway_quick.GW_NAME"]], 96,
                  r"([0-9a-zA-Z][-]?){1,48}") == []                                      # AgentCore gateway
    assert _limit([n["deploy_approvals.STATE_MACHINE"]], 80, r"[A-Za-z0-9_-]+") == []    # Step Functions
    assert _limit(alarms, 255) == []                                                      # CloudWatch alarm
    assert _limit(schedules, 64, r"[0-9a-zA-Z_.-]+") == []                               # Scheduler schedule
    assert _limit([n["deploy_events.RULE_NAME"], n["deploy_lake.RULE"]], 64, r"[.\-_A-Za-z0-9]+") == []
    assert _limit([n["deploy_events.DLQ_NAME"]], 80, r"[A-Za-z0-9_-]+") == []            # SQS queue
    assert _limit([n["deploy_lake.STREAM"]], 64, r"[a-zA-Z0-9_.-]+") == []               # Firehose stream
    assert _limit([n["deploy_lake.WORKGROUP"]], 128, r"[a-zA-Z0-9._-]+") == []           # Athena workgroup
    assert _limit([n["deploy_lake.DATABASE"]], 255, r"[a-z0-9_]+") == []                 # Glue database
    assert _limit([n["deploy_guardrails.NAME"], n["deploy_guardrails.DIGEST_NAME"]], 50, r"[0-9a-zA-Z_-]+") == []
    assert _limit([n["deploy_knowledge_base.KB_NAME"]], 100, r"([0-9a-zA-Z][_-]?){1,100}") == []
    assert _limit([n["deploy_console.OAC_NAME"], n["deploy_console.STREAM_OAC_NAME"],
                   n["deploy_console.REWRITE_FN"], n["deploy_l4.jwt_layer"]], 64, r"[a-zA-Z0-9_-]+") == []
    assert _limit([n["deploy_waf.ACL_NAME"], n["deploy_waf.IPSET_NAME"]], 128, r"[\w#:\.\-/]+") == []
    assert _limit([n["cognito_setup.POOL_NAME"], n["cognito_setup.CLIENT_NAME"],
                   n["cognito_setup.HOSTED_UI_CLIENT_NAME"]], 128, r"[\w\s+=,.@-]+") == []
    domain = n["cognito_setup.domain_prefix"]
    assert re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", domain)              # Cognito domain prefix
    assert not any(w in domain for w in naming.RESERVED_WORDS)


def test_cognito_domain_prefix_avoids_reserved_words():
    from app.infra import cognito_setup
    prefix = cognito_setup.default_domain_prefix("us-east-1_xAWSxyz12")      # the pool suffix spells "aws"
    assert prefix.startswith("retail-ops-dev-") and "aws" not in prefix
    assert re.fullmatch(r"retail-ops-dev-[0-9a-f]{10}", prefix)


# --- validation ------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["dev", "ab", "g4f2a91", "abcdefgh", "e2e01"])
def test_valid_env_names(value):
    assert naming.validate_env(value) == value


@pytest.mark.parametrize("value", ["", "a", "abcdefghi", "Dev", "g-1", "g_1", "g.1", " dev", "gaws12",
                                   "amazon1", "cognito", None, 12])
def test_invalid_env_names(value):
    with pytest.raises(naming.NamingError):
        naming.validate_env(value)


def test_invalid_env_fails_fast_with_a_message():
    run_env = {**os.environ, "RETAIL_OPS_ENV": "Not-Valid"}
    r = subprocess.run([sys.executable, "-m", "app.infra.naming", "--check"], cwd=ROOT, env=run_env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode != 0
    assert "RETAIL_OPS_ENV='Not-Valid' is not valid" in r.stderr
    assert "Traceback" not in r.stderr


def test_naming_cli_prints_the_stems():
    run_env = {**os.environ, "RETAIL_OPS_ENV": "g4f2a91"}
    r = subprocess.run([sys.executable, "-m", "app.infra.naming"], cwd=ROOT, env=run_env,
                       capture_output=True, text=True, check=True, timeout=60)
    assert "env=g4f2a91 prefix=retail-ops-g4f2a91- prefix_us=retail_ops_g4f2a91_ event_bus=app-bus-g4f2a91" in r.stdout


# --- config files and the runtime-side reader ----------------------------------------------------

def test_config_paths_are_keyed_by_env():
    assert naming.config_path("lake", "dev").name == "lake-config.json"
    assert naming.config_path("lake", "g4f2a91").name == "lake-config.g4f2a91.json"
    assert naming.config_glob("dev") == "*-config.json"
    assert naming.config_glob("g4f2a91") == "*-config.g4f2a91.json"
    # the dev glob never picks up another env's file
    assert not Path("lake-config.g4f2a91.json").match(naming.config_glob("dev"))


@pytest.mark.parametrize("env", ["dev", "g4f2a91"])
def test_runtime_reader_agrees_with_naming(monkeypatch, env):
    monkeypatch.setenv("RETAIL_OPS_ENV", env)
    monkeypatch.delenv("RETAIL_OPS_ENV_PREFIX", raising=False)
    monkeypatch.delenv("RETAIL_OPS_EVENT_BUS", raising=False)
    assert envname.env() == env
    assert envname.table_prefix() == naming.prefix(env)
    assert envname.event_bus() == naming.event_bus(env)
    for base in ("agentcore", "guardrail", "knowledge-base", "gateway", "memory", "quick"):
        assert envname.config_file(naming.INFRA, base) == naming.config_path(base, env)


def test_runtime_reader_prefers_the_explicit_variables(monkeypatch):
    monkeypatch.setenv("RETAIL_OPS_ENV", "g4f2a91")
    monkeypatch.setenv("RETAIL_OPS_ENV_PREFIX", "retail-ops-other-")
    monkeypatch.setenv("RETAIL_OPS_EVENT_BUS", "app-bus-other")
    assert envname.table_prefix() == "retail-ops-other-"
    assert envname.event_bus() == "app-bus-other"


def test_gitignore_covers_env_config_files():
    text = (ROOT / ".gitignore").read_text()
    assert "app/infra/*-config.json" in text and "app/infra/*-config.*.json" in text


def test_teardown_skips_quick_for_a_non_default_env_and_archives_only_its_files():
    from app.infra import teardown
    assert teardown.QUICK_STAGES == ("quick_content", "quick")
    assert naming.agentcore_exec_role("dev", stem="acme") == "AmazonBedrockAgentCore-acme-exec"
    assert naming.agentcore_code_bucket("123456789012", "g4f2a91") == "bedrock-agentcore-retail-ops-g4f2a91-123456789012"
