"""Guard: the streaming Lambda must never be world-accessible.

A public Function URL (AuthType NONE + `Principal: "*"`) is an unauthenticated
entry point into the account, and the `lambda_function_policy_block_public_access`
control auto-mitigates it — stripping the resource policy so the endpoint 403s no
matter what the handler does. These tests pin the shape of the DEPLOYER so the
regression cannot be reintroduced by editing it back.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPLOYER = (ROOT / "app" / "infra" / "deploy_streaming.py").read_text()
CONSOLE = (ROOT / "app" / "infra" / "deploy_console.py").read_text()


def test_function_url_is_iam_only() -> None:
    assert '"AuthType": "AWS_IAM"' in DEPLOYER
    assert '"AuthType": "NONE"' not in DEPLOYER, "a public Function URL is auto-mitigated"


def test_no_wildcard_invoke_grant() -> None:
    # No add_permission with Principal "*" anywhere in the streaming deployer.
    for call in re.findall(r"add_permission\((.*?)\)", DEPLOYER, re.S):
        assert 'Principal="*"' not in call.replace(" ", ""), "wildcard invoke principal"
        assert 'FunctionUrlAuthType="NONE"' not in call.replace(" ", "")


def test_guard_assertion_exists() -> None:
    # The deployer refuses to finish in an auto-mitigable state.
    assert "_assert_not_public" in DEPLOYER
    assert "only AWS_IAM is allowed" in DEPLOYER


def test_cloudfront_is_the_only_invoker() -> None:
    assert 'Principal="cloudfront.amazonaws.com"' in DEPLOYER
    assert "SourceArn=distribution_arn" in DEPLOYER


def test_console_fronts_stream_with_oac() -> None:
    assert '"OriginAccessControlOriginType": "lambda"' in CONSOLE
    assert '"SigningBehavior": "always"' in CONSOLE
    assert "ensure_stream_origin" in CONSOLE
