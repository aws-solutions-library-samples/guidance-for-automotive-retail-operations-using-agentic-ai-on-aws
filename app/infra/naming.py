#!/usr/bin/env python3
"""Resource naming: the one place the environment name and its stems come from.

Every AWS resource this solution creates is named from one environment name,
read from RETAIL_OPS_ENV (default `dev`):

  PREFIX       retail-ops-<env>-     DynamoDB tables, Lambdas, IAM roles, REST API,
                                     schedules, alarms, guardrails, buckets, ...
  PREFIX_US    retail_ops_<env>_     Glue database, AgentCore runtimes and memory
  EVENT_BUS    app-bus-<env>         the EventBridge bus

`dev` keeps the names the first deployments used, byte for byte, including the
two account-wide AgentCore names that carry no environment (the runtime
execution role and the code bucket). Every other environment puts its name into
those two as well, so two environments in one account never share a resource.
The AWS Quick ids (`retail-ops-lake`, `retail-ops-builders`, ...) carry no
environment for any env: one Quick deployment per account is supported.

The generated, git-ignored deployment state follows the same rule:
`app/infra/<name>-config.json` for `dev` (the files the first deployments wrote)
and `app/infra/<name>-config.<env>.json` for any other env (config_path), so one
environment never reads or overwrites another's handles.

Environment names are 2-8 lowercase letters and digits and must not contain
`aws`, `amazon` or `cognito` (Cognito refuses a hosted-UI domain prefix holding
those words, and the domain prefix carries the env name). With 8 characters
every derived name stays inside its AWS length limit (tests/app/test_naming.py).

The deployers, teardown and the deploy scripts import this module; the deployed
code learns the env from the RETAIL_OPS_ENV, RETAIL_OPS_ENV_PREFIX and
RETAIL_OPS_EVENT_BUS variables the deployers set. Stdlib only.

  python3 -m app.infra.naming            # print the env and its stems
  python3 -m app.infra.naming --check    # validate RETAIL_OPS_ENV; non-zero exit with a message
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ENV_VAR = "RETAIL_OPS_ENV"
DEFAULT_ENV = "dev"
STEM = "retail-ops"               # technical stem (spec §13, decided 2026-10-19)
STEM_US = "retail_ops"            # the same stem where only underscores are allowed
ENV_PATTERN = re.compile(r"[a-z0-9]{2,8}")
RESERVED_WORDS = ("aws", "amazon", "cognito")
INFRA = Path(__file__).resolve().parent


class NamingError(ValueError):
    """RETAIL_OPS_ENV is not a usable environment name; the message says why."""


def validate_env(value: object) -> str:
    """`value` when it is a usable environment name, else NamingError."""
    if not isinstance(value, str) or not ENV_PATTERN.fullmatch(value):
        raise NamingError(f"{ENV_VAR}={value!r} is not valid: use 2-8 lowercase letters and digits "
                          f"(for example `dev` or `g4f2a91`)")
    hit = next((w for w in RESERVED_WORDS if w in value), None)
    if hit:
        raise NamingError(f"{ENV_VAR}={value!r} is not valid: it contains the reserved word {hit!r}, "
                          "which Amazon Cognito refuses in a hosted-UI domain prefix")
    return value


def current_env() -> str:
    """The environment name from RETAIL_OPS_ENV (unset or empty: `dev`), validated."""
    return validate_env(os.environ.get(ENV_VAR) or DEFAULT_ENV)


def prefix(env: str | None = None) -> str:
    """`retail-ops-<env>-`."""
    return f"{STEM}-{validate_env(env or ENV)}-"


def prefix_us(env: str | None = None) -> str:
    """`retail_ops_<env>_`."""
    return f"{STEM_US}_{validate_env(env or ENV)}_"


def event_bus(env: str | None = None) -> str:
    """`app-bus-<env>`."""
    return f"app-bus-{validate_env(env or ENV)}"


def name(suffix: str, env: str | None = None) -> str:
    """`retail-ops-<env>-<suffix>`, the name of most resources."""
    return f"{prefix(env)}{suffix}"


def name_us(suffix: str, env: str | None = None) -> str:
    """`retail_ops_<env>_<suffix>`."""
    return f"{prefix_us(env)}{suffix}"


def agentcore_exec_role(env: str | None = None, stem: str = STEM) -> str:
    """The AgentCore runtime execution role shared by one env's runtimes:
    `AmazonBedrockAgentCore-retail-ops-exec` for dev (unchanged),
    `AmazonBedrockAgentCore-retail-ops-<env>-exec` for any other env. `stem`
    lets teardown name the role of an earlier stem."""
    env = validate_env(env or ENV)
    return f"AmazonBedrockAgentCore-{stem}-exec" if env == DEFAULT_ENV \
        else f"AmazonBedrockAgentCore-{stem}-{env}-exec"


def agentcore_code_bucket(account: str, env: str | None = None, stem: str = STEM) -> str:
    """The S3 bucket holding the agent bundles: `bedrock-agentcore-retail-ops-<account>`
    for dev (unchanged), `bedrock-agentcore-retail-ops-<env>-<account>` otherwise."""
    env = validate_env(env or ENV)
    return f"bedrock-agentcore-{stem}-{account}" if env == DEFAULT_ENV \
        else f"bedrock-agentcore-{stem}-{env}-{account}"


def config_path(basename: str, env: str | None = None) -> Path:
    """The generated deployment-state file of one env: `app/infra/<basename>-config.json`
    for dev, `app/infra/<basename>-config.<env>.json` for any other env."""
    env = validate_env(env or ENV)
    return INFRA / (f"{basename}-config.json" if env == DEFAULT_ENV else f"{basename}-config.{env}.json")


def config_glob(env: str | None = None) -> str:
    """The glob that matches exactly one env's config files under app/infra/."""
    env = validate_env(env or ENV)
    return "*-config.json" if env == DEFAULT_ENV else f"*-config.{env}.json"


try:
    ENV = current_env()
except NamingError as _e:      # fail fast with the reason, not a traceback
    raise SystemExit(f"ERROR: {_e}") from None

IS_DEFAULT = ENV == DEFAULT_ENV
PREFIX = prefix()               # retail-ops-dev-
PREFIX_US = prefix_us()         # retail_ops_dev_
EVENT_BUS = event_bus()         # app-bus-dev


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m app.infra.naming",
                                 description="Print or check the environment name and its stems.")
    ap.add_argument("--check", action="store_true", help="validate RETAIL_OPS_ENV only (quiet on success)")
    args = ap.parse_args(argv)
    if not args.check:
        print(f"env={ENV} prefix={PREFIX} prefix_us={PREFIX_US} event_bus={EVENT_BUS} "
              f"config={config_path('<name>').name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
