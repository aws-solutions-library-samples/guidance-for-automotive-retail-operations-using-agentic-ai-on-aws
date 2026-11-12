"""The environment name the deployed code runs under, read from the process environment.

app/infra/naming.py owns resource naming: it validates RETAIL_OPS_ENV (default
`dev`) and the deployers set RETAIL_OPS_ENV, RETAIL_OPS_ENV_PREFIX and
RETAIL_OPS_EVENT_BUS on every Lambda and agent runtime. This module only reads
those variables, with the same derivation rules, for code that runs where
app/infra is not packaged (the Lambdas, the agent runtimes, the dev server).
tests/app/test_naming.py keeps the two in step. Stdlib only.
"""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_ENV = "dev"


def env() -> str:
    """RETAIL_OPS_ENV, or `dev` when unset or empty."""
    return os.environ.get("RETAIL_OPS_ENV") or DEFAULT_ENV


def table_prefix() -> str:
    """RETAIL_OPS_ENV_PREFIX, else `retail-ops-<env>-`."""
    return os.environ.get("RETAIL_OPS_ENV_PREFIX") or f"retail-ops-{env()}-"


def event_bus() -> str:
    """RETAIL_OPS_EVENT_BUS, else `app-bus-<env>`."""
    return os.environ.get("RETAIL_OPS_EVENT_BUS") or f"app-bus-{env()}"


def config_file(infra_dir: Path, basename: str) -> Path:
    """One env's generated deployment-state file under app/infra/:
    `<basename>-config.json` for dev, `<basename>-config.<env>.json` otherwise
    (the rule of app/infra/naming.config_path)."""
    e = env()
    return infra_dir / (f"{basename}-config.json" if e == DEFAULT_ENV else f"{basename}-config.{e}.json")
