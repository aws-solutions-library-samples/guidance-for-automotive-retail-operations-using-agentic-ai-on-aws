"""Regressions for the pre-publication security review (2026-10-27).

Critical  the outreach SES sender had a built-in default (a real mailbox);
          it is deploy configuration only, and the deployers carry it.
High      the authenticated API answered CORS with `*`; it answers for the
          console's origin (RETAIL_OPS_CONSOLE_ORIGIN) and the local console only.
High      the agent runtimes' Bedrock grant covered every Bedrock resource in
          the account; it covers the Claude family, the inference and guardrail
          profiles, the guardrail and the knowledge base.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.infra import deploy_agentcore, deploy_l4
from app.services.api import handler
from app.services.platform import outreach

ROOT = Path(__file__).resolve().parents[2]
ACCOUNT = "123456789012"


# --- Critical: no default SES sender ----------------------------------------------------------

def test_outreach_source_has_no_builtin_sender():
    src = (ROOT / "app" / "services" / "platform" / "outreach.py").read_text()
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", src), "a literal mailbox in the outreach module"
    assert 'os.environ.get("RETAIL_OPS_SES_FROM")' in src          # no second argument = no default


def test_email_without_a_configured_sender_is_recorded_and_falls_back(fake_ddb, monkeypatch):
    monkeypatch.delenv("RETAIL_OPS_SES_FROM", raising=False)
    before = len(fake_ddb.rows("DEALER_Tasks"))
    out = outreach.BasicOutreachProvider().send("email", "someone@example.com", "Hello", "body",
                                               customer_id=None, dealer_id="D-101")
    assert out["sent"] is False and out["fallback_task"] is True
    assert "RETAIL_OPS_SES_FROM must be set" in out["provider_ref"]
    assert len(fake_ddb.rows("DEALER_Tasks")) == before + 1
    msg = next(m for m in fake_ddb.rows("CUST_Messages") if m["message_id"] == out["message_id"])
    assert msg["status"] == "queued_task" and "RETAIL_OPS_SES_FROM" in msg["provider_ref"]


def test_deployers_carry_the_sender_from_the_deploy_environment(monkeypatch):
    monkeypatch.delenv("RETAIL_OPS_SES_FROM", raising=False)
    assert deploy_l4.ses_from_env() == {}
    assert deploy_l4.ses_from_env("  ") == {}
    monkeypatch.setenv("RETAIL_OPS_SES_FROM", " no-reply@example.com ")
    assert deploy_l4.ses_from_env() == {"RETAIL_OPS_SES_FROM": "no-reply@example.com"}
    assert "RETAIL_OPS_SES_FROM" in deploy_l4._PRESERVE_ENV_KEYS
    from app.agentcore import registry
    env = deploy_agentcore.runtime_env(registry.AGENTS["A3"], guardrail={}, kb_id=None, callee_arns={})
    assert env["RETAIL_OPS_SES_FROM"] == "no-reply@example.com"
    monkeypatch.delenv("RETAIL_OPS_SES_FROM")
    assert "RETAIL_OPS_SES_FROM" not in deploy_agentcore.runtime_env(registry.AGENTS["A3"], guardrail={},
                                                                     kb_id=None, callee_arns={})


# --- High: CORS is scoped to the console ------------------------------------------------------

CONSOLE = "https://d1234567890abc.cloudfront.net"


def _preflight(origin: str | None) -> dict:
    headers = {"Origin": origin} if origin else {}
    return handler.lambda_handler({"httpMethod": "OPTIONS", "path": "/journeys/board", "headers": headers})


def test_allowed_origins_are_the_console_and_local_dev(monkeypatch):
    monkeypatch.delenv(handler.CONSOLE_ORIGIN_ENV, raising=False)
    assert handler.allowed_origins() == ["http://localhost:3100", "http://127.0.0.1:3100"]
    monkeypatch.setenv(handler.CONSOLE_ORIGIN_ENV, f"{CONSOLE}/, https://other.example ,,")
    assert handler.allowed_origins() == [CONSOLE, "https://other.example",
                                         "http://localhost:3100", "http://127.0.0.1:3100"]


@pytest.mark.parametrize("origin,want", [
    (CONSOLE, CONSOLE),                                  # the console: echoed
    ("http://localhost:3100", "http://localhost:3100"),  # local dev: echoed
    ("https://evil.example", CONSOLE),                   # anything else: the console, so the browser refuses
    (None, CONSOLE),                                     # non-browser client: the intended origin
])
def test_preflight_never_answers_with_a_wildcard(monkeypatch, origin, want):
    monkeypatch.setenv(handler.CONSOLE_ORIGIN_ENV, CONSOLE)
    resp = _preflight(origin)
    assert resp["statusCode"] == 204
    assert resp["headers"]["Access-Control-Allow-Origin"] == want
    assert resp["headers"]["Access-Control-Allow-Origin"] != "*"
    assert resp["headers"]["Vary"] == "Origin"
    assert handler.current_origin() is None             # reset after the request


def test_responses_carry_the_request_origin_only_while_handling_it(monkeypatch):
    monkeypatch.setenv(handler.CONSOLE_ORIGIN_ENV, CONSOLE)
    assert handler._response(200, {})["headers"]["Access-Control-Allow-Origin"] == CONSOLE
    assert "*" not in (ROOT / "app" / "services" / "api" / "cors.py").read_text().split(
        '"Access-Control-Allow-Origin": ')[1][:40]


def test_l4_wires_the_console_origin_from_the_console_config(tmp_path, capsys):
    cfg = tmp_path / "console-config.json"
    cfg.write_text(json.dumps({"url": f"{CONSOLE}/"}))
    assert deploy_l4.console_origin_env(None, cfg) == {deploy_l4.CONSOLE_ORIGIN: CONSOLE}
    assert deploy_l4.console_origin_env("https://x.example/, https://y.example", cfg) == {
        deploy_l4.CONSOLE_ORIGIN: "https://x.example,https://y.example"}
    assert deploy_l4.console_origin_env(None, tmp_path / "missing.json") == {}
    assert "re-run l4 after the console stage" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="https origins"):
        deploy_l4.console_origin_env("http://insecure.example", cfg)
    assert deploy_l4.CONSOLE_ORIGIN in deploy_l4._PRESERVE_ENV_KEYS
    assert deploy_l4.CONSOLE_ORIGIN == handler.CONSOLE_ORIGIN_ENV


# --- High: the Bedrock grant names what the agents invoke -------------------------------------

def test_exec_role_bedrock_grant_has_no_account_wide_resource():
    stmt = next(s for s in deploy_agentcore._exec_policy(ACCOUNT)["Statement"]
                if s.get("Sid") == "BedrockModelInvocation")
    resources = stmt["Resource"]
    assert f"arn:aws:bedrock:us-east-1:{ACCOUNT}:*" not in resources
    assert "arn:aws:bedrock:*::foundation-model/*" not in resources
    assert not any(r.endswith(f":{ACCOUNT}:*") for r in resources)
    # what IS granted: the model family the runtimes are configured with ...
    assert deploy_agentcore.BEDROCK_MODEL.startswith("us.anthropic.claude-")
    assert "arn:aws:bedrock:*::foundation-model/anthropic.claude-*" in resources
    assert f"arn:aws:bedrock:*:{ACCOUNT}:inference-profile/*" in resources
    # ... the guardrail Converse attaches, and the knowledge base Retrieve reads
    assert f"arn:aws:bedrock:us-east-1:{ACCOUNT}:guardrail/*" in resources
    assert f"arn:aws:bedrock:us-east-1:{ACCOUNT}:knowledge-base/*" in resources
    assert set(stmt["Action"]) == {"bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream",
                                   "bedrock:ApplyGuardrail", "bedrock:Retrieve"}
