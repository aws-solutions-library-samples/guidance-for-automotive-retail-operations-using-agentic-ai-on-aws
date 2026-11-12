"""SSO: one Cognito pool, several app clients (§8.1).

Native username/password sign-in and the Hosted-UI SSO flow use *different* app
clients of the same user pool, so a token minted by either must verify. These
tests pin that the accepted audience is an allowlist rather than a single id —
the bug that would otherwise reject every federated sign-in with "audience
mismatch" while native sign-in kept working.
"""
from __future__ import annotations

import base64
import json
import time

import pytest

from app.services.common import jwt_verify

NATIVE = "2jppee1dopea8f4v3tmtk0pn0v"
HOSTED = "7kkqff2eppfb9g5w4unul1qo1w"


def test_audiences_parses_single_and_list():
    assert jwt_verify._audiences(NATIVE) == [NATIVE]
    assert jwt_verify._audiences(f"{NATIVE},{HOSTED}") == [NATIVE, HOSTED]
    # tolerate operator whitespace in the env var
    assert jwt_verify._audiences(f" {NATIVE} , {HOSTED} ") == [NATIVE, HOSTED]


@pytest.mark.parametrize("configured", [None, "", "   ", ",,"])
def test_no_audience_configured_means_no_audience_check(configured):
    assert jwt_verify._audiences(configured) == []


ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_test"


def _token(aud: str) -> str:
    """A structurally real RS256 ID token. Only the signature is faked, so the
    claim validation under test is the production code."""
    def seg(obj):
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")
    header = seg({"alg": "RS256", "kid": "test-kid"})
    body = seg({"iss": ISSUER, "token_use": "id", "aud": aud,
                "cognito:username": "U-US-00002", "exp": int(time.time()) + 3600})
    return f"{header}.{body}.{base64.urlsafe_b64encode(b'sig').decode().rstrip('=')}"


@pytest.fixture
def stdlib_verify(monkeypatch):
    """Exercise the real `_verify_stdlib` claim checks with crypto stubbed out."""
    monkeypatch.setattr(jwt_verify, "_HAVE_PYJWT", False)
    monkeypatch.setattr(jwt_verify, "_fetch_jwks",
                        lambda issuer: {"test-kid": {"n": "AQAB", "e": "AQAB"}})
    monkeypatch.setattr(jwt_verify, "_rsa_verify", lambda *a, **k: True)
    return jwt_verify.verify_cognito_jwt


def test_either_app_client_verifies(stdlib_verify):
    """Native and Hosted-UI (SSO) tokens both pass when both ids are allowed."""
    both = f"{NATIVE},{HOSTED}"
    for aud in (NATIVE, HOSTED):
        claims = stdlib_verify(_token(aud), ISSUER, both)
        assert claims["aud"] == aud
        assert claims["cognito:username"] == "U-US-00002"


def test_unknown_app_client_is_rejected(stdlib_verify):
    with pytest.raises(jwt_verify.JwtError, match="audience mismatch"):
        stdlib_verify(_token("some-other-pools-client"), ISSUER, f"{NATIVE},{HOSTED}")


def test_sso_token_rejected_when_only_native_is_allowed(stdlib_verify):
    """The regression this guards: SSO configured on the pool but the authorizer
    still pinned to one client id — every federated sign-in would 401."""
    with pytest.raises(jwt_verify.JwtError, match="audience mismatch"):
        stdlib_verify(_token(HOSTED), ISSUER, NATIVE)
