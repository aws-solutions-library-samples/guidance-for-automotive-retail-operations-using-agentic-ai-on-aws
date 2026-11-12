"""Fixtures for offline unit tests of `app/services` (no AWS calls).

`fake_ddb` swaps the boto3 DynamoDB client behind `app.services.common.ddb` for
an in-memory store seeded from the committed synthetic packs (us + de). The
seed is parsed once per session; each test gets a copy-on-write view, so writes
in one test never leak into another.

Every other boto3 client is replaced by a stub that raises, so a test that
would reach a real AWS service (Bedrock, EventBridge, SES, ...) exercises the
code's offline fallback instead of the network. `boto3.Session` is stubbed the
same way, the AWS config/credential files and credential variables are hidden,
the agent-runtime routing variables are unset (so a developer's shell cannot
route a test to a deployed runtime), and `subprocess.run` refuses to spawn pip.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.common import authz, ddb  # noqa: E402
from app.services.common.local_ddb import FakeDynamoDB  # noqa: E402


@pytest.fixture(scope="session")
def _seeded() -> FakeDynamoDB:
    fake = FakeDynamoDB()
    fake.seed_markets()
    return fake


class _NoAws:
    def __init__(self, service: str) -> None:
        self.service = service

    def __getattr__(self, name: str) -> Any:
        def _blocked(*_a: Any, **_k: Any) -> Any:
            raise RuntimeError(f"offline test: boto3 {self.service}.{name} is not available")
        return _blocked


class _NoSession:
    """Stands in for boto3.Session / boto3.session.Session: clients are the
    raising `_NoAws` stub and there are never credentials."""

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    def client(self, service: str, *_a: Any, **_k: Any) -> _NoAws:
        return _NoAws(service)

    def get_credentials(self) -> Any:
        raise RuntimeError("offline test: AWS credentials are not available")


# Variables that would let a test find AWS credentials or a deployed runtime.
# The disable switch is unset too, never set: see design §16.
_UNSET_ENV = (
    "AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "RETAIL_OPS_AGENT_RUNTIMES", "RETAIL_OPS_AGENT_RUNTIME_ARN", "RETAIL_OPS_AGENT_RUNTIME_USE_CONFIG",
    "RETAIL_OPS_AGENT_RUNTIME_DISABLE", "RETAIL_OPS_AGENT_ID",
)
_PIP = re.compile(r"^pip[0-9.]*(\.exe)?$")


def _is_pip_call(argv: Any) -> bool:
    """True when argv runs pip (`-m pip`, `pip`, `pip3.12`, ...)."""
    if argv is None:
        return False
    parts = argv.split() if isinstance(argv, str) else [os.fspath(a) for a in argv
                                                         if isinstance(a, (str, os.PathLike))]
    return any(_PIP.match(os.path.basename(p)) for p in parts)


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    import boto3
    import boto3.session
    monkeypatch.setattr(boto3, "client", lambda service, *a, **k: _NoAws(service))
    monkeypatch.setattr(boto3, "Session", _NoSession)
    monkeypatch.setattr(boto3.session, "Session", _NoSession)
    monkeypatch.setenv("RETAIL_OPS_EVENT_BUS_LIVE", "0")
    monkeypatch.setenv("AWS_CONFIG_FILE", "/dev/null")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/dev/null")
    for name in _UNSET_ENV:
        monkeypatch.delenv(name, raising=False)

    real_run = subprocess.run

    def _run(*args: Any, **kwargs: Any) -> Any:
        if _is_pip_call(args[0] if args else kwargs.get("args")):
            raise RuntimeError("offline test: pip is not available")
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _run)


@pytest.fixture(scope="session")
def _seeded_today() -> FakeDynamoDB:
    """The seed rebased so its frozen clock lands on now — what `ddb_load --rebase-now` deploys. For
    checks that reason about 'days ago' (the invariant sweep, readiness)."""
    import datetime as _dt
    fake = FakeDynamoDB()
    fake.seed_markets(rebase_to=_dt.datetime.now(_dt.UTC).replace(microsecond=0))
    return fake


@pytest.fixture
def fake_ddb_today(_seeded_today: FakeDynamoDB, monkeypatch: pytest.MonkeyPatch) -> FakeDynamoDB:
    fake = FakeDynamoDB(_seeded_today.env_prefix)
    fake.keys = dict(_seeded_today.keys)
    fake.tables = {name: dict(rows) for name, rows in _seeded_today.tables.items()}
    ddb._client.cache_clear()
    monkeypatch.setattr(ddb, "_client", lambda: fake)
    authz._org_nodes_cached.cache_clear()
    return fake


@pytest.fixture
def fake_ddb(_seeded: FakeDynamoDB, monkeypatch: pytest.MonkeyPatch) -> FakeDynamoDB:
    fake = FakeDynamoDB(_seeded.env_prefix)
    fake.keys = dict(_seeded.keys)
    fake.tables = {name: dict(rows) for name, rows in _seeded.tables.items()}
    ddb._client.cache_clear()
    monkeypatch.setattr(ddb, "_client", lambda: fake)
    authz._org_nodes_cached.cache_clear()
    return fake


def api_event(method: str, path: str, *, user: str = "u-test", role: str = "dealer_advisor",
              dealer_ids: str = "", is_oem: bool = False, market_id: str = "us",
              customer_id: str = "", body: Any = None,
              query: dict[str, str] | None = None) -> dict[str, Any]:
    """An API Gateway proxy event as the authorizer would hand it to the handler."""
    return {
        "httpMethod": method,
        "path": path,
        "queryStringParameters": query,
        "body": json.dumps(body) if body is not None else None,
        "requestContext": {"authorizer": {
            "user_id": user, "role": role, "dealer_ids": dealer_ids,
            "is_oem": str(is_oem).lower(), "market_id": market_id,
            "customer_id": customer_id, "scope_node_id": "",
        }},
    }


def call(event: dict[str, Any]) -> tuple[int, Any]:
    from app.services.api import handler
    resp = handler._route(event)
    return resp["statusCode"], json.loads(resp["body"])
