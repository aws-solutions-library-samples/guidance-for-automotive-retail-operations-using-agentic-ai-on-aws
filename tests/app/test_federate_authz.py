"""Federated sign-in resolves to a PLAT_Users row (§8.1).

Enterprise IdP (OIDC) tokens arrive with the username prefixed by the IdP
name and no `email` claim — the IdP's OIDC discovery only publishes the
`openid` scope and `sub, groups, amr` claims. These tests pin that the
authorizer's resolution chain lands on the right PLAT_Users row anyway, so a
federated sign-in gets the same role and scope as native username/password.
"""
from __future__ import annotations

import pytest

from app.services.api import authorizer
from app.services.common import ddb

TEST_USER = "U-US-00044"
TEST_USERNAME = "casey.brennan"
TEST_EMAIL = "casey.brennan@meridian-oem.example.com"


@pytest.fixture
def federated_user(fake_ddb):
    """Seed a PLAT_Users row the IdP tokens below should resolve to."""
    ddb.put_item("PLAT_Users", {
        "user_id": TEST_USER, "username": TEST_USERNAME, "email": TEST_EMAIL,
        "role": "oem_dm", "scope_node_id": "DIST-07", "market_id": "us",
        "active": True, "dealer_ids": [],
    })
    yield TEST_USER


def _run(monkeypatch, claims):
    """Drive the real authorizer with the Cognito verifier stubbed to `claims`."""
    monkeypatch.setattr(authorizer.jwt_verify, "verify_cognito_jwt",
                        lambda *a, **k: claims)
    monkeypatch.setattr(authorizer, "COGNITO_ISSUER", "https://cognito-idp.local")
    monkeypatch.setattr(authorizer, "COGNITO_CLIENT_ID", "test-client")
    return authorizer.resolve_principal("Bearer eyJhbGciOiJub25lIn0.e30.sig")


def test_federate_username_resolves_via_preferred_username(monkeypatch, federated_user):
    """Federate's `sub` is the alias; Cognito maps it to preferred_username."""
    claims = {"cognito:username": f"CorpDirectory_{TEST_USERNAME}",
              "preferred_username": TEST_USERNAME, "token_use": "id"}
    assert _run(monkeypatch, claims)["user_id"] == TEST_USER


def test_federate_username_resolves_via_username_claim(monkeypatch, federated_user):
    """Alternate claim shape: a `username` claim instead of preferred_username."""
    claims = {"cognito:username": f"CorpOkta_{TEST_USERNAME}",
              "username": TEST_USERNAME, "token_use": "id"}
    assert _run(monkeypatch, claims)["user_id"] == TEST_USER


def test_sub_in_prefix_resolves_when_it_is_the_user_id(monkeypatch, federated_user):
    """OIDC IdPs whose `sub` IS the user_id still work (step 2 of the chain)."""
    claims = {"cognito:username": f"CorpOkta_{TEST_USER}", "token_use": "id"}
    assert _run(monkeypatch, claims)["user_id"] == TEST_USER


def test_email_claim_still_resolves_for_oidc_idps_that_return_it(monkeypatch, federated_user):
    """SAML / Okta / Azure AD typically send `email`; keep that path working."""
    claims = {"cognito:username": "CorpSAML_abcdef", "email": TEST_EMAIL, "token_use": "id"}
    assert _run(monkeypatch, claims)["user_id"] == TEST_USER


def test_unresolvable_federated_identity_is_rejected(monkeypatch, federated_user):
    """A federated identity with no matching PLAT_Users row cannot sign in."""
    claims = {"cognito:username": "CorpDirectory_someone.else",
              "preferred_username": "someone.else", "token_use": "id"}
    with pytest.raises(authorizer.authz.Unauthenticated, match="unknown user"):
        _run(monkeypatch, claims)


def test_native_cognito_token_still_resolves_directly(monkeypatch, federated_user):
    """Non-federated tokens whose username IS the user_id must still work unchanged."""
    claims = {"cognito:username": TEST_USER, "token_use": "id"}
    assert _run(monkeypatch, claims)["user_id"] == TEST_USER
