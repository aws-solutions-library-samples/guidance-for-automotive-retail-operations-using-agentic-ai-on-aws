"""Federated IdP wiring is provider-agnostic (§8.1).

The pilot signs in through an enterprise directory (OIDC or SAML) and that IdP
comes off before any external release, so the provider — its name, protocol,
issuer and metadata — must be configuration, never code. These tests pin that
contract, including the attribute mapping the authorizer depends on to resolve a
federated identity to a PLAT_Users row.
"""
from __future__ import annotations

import pytest

from app.infra.cognito_setup import DEFAULT_IDP_NAME, _idp_definition

OIDC_CREDS = {"protocol": "oidc", "client_id": "cid", "client_secret": "sec"}


def test_no_inputs_means_no_idp():
    assert _idp_definition({}) is None
    assert _idp_definition({"protocol": "oidc", "client_id": "cid"}) is None
    assert _idp_definition({"protocol": "oidc", "client_secret": "sec"}) is None
    assert _idp_definition({"protocol": "saml"}) is None


def test_oidc_definition_defaults():
    name, ptype, details, mapping = _idp_definition(OIDC_CREDS)
    assert (name, ptype) == (DEFAULT_IDP_NAME, "OIDC")
    assert details["client_id"] == "cid" and details["client_secret"] == "sec"
    assert details["oidc_issuer"] == ""
    assert details["authorize_scopes"] == "openid profile email"
    assert mapping == {"username": "sub", "email": "email"}


def test_oidc_definition_sub_is_alias_shape():
    _, _, details, mapping = _idp_definition({**OIDC_CREDS, "sub_is_alias": True})
    assert details["authorize_scopes"] == "openid"
    assert mapping == {"username": "sub", "preferred_username": "sub", "email": "EMAIL"}


def test_saml_definition_from_metadata_url():
    name, ptype, details, mapping = _idp_definition(
        {"protocol": "saml", "metadata_url": "https://idp.example/metadata", "name": "CorpSAML"})
    assert (name, ptype) == ("CorpSAML", "SAML")
    assert details == {"MetadataURL": "https://idp.example/metadata"}
    assert mapping["username"].endswith("/nameidentifier")
    assert mapping["email"].endswith("/emailaddress")


def test_saml_definition_from_metadata_file(tmp_path):
    f = tmp_path / "idp.xml"
    f.write_text("<EntityDescriptor/>")
    _, ptype, details, _ = _idp_definition({"protocol": "saml", "metadata_file": str(f)})
    assert ptype == "SAML"
    assert details == {"MetadataFile": "<EntityDescriptor/>"}


def test_unreadable_saml_metadata_file_is_not_fatal():
    assert _idp_definition({"protocol": "saml", "metadata_file": "/nope/missing.xml"}) is None


@pytest.mark.parametrize("name", ["CorpDirectory", "CorpOkta", "PartnerAzureAD"])
def test_provider_name_is_configuration(name):
    assert _idp_definition({**OIDC_CREDS, "name": name})[0] == name


def test_protocol_is_case_insensitive():
    assert _idp_definition({"protocol": "SAML", "metadata_url": "https://x/m"})[1] == "SAML"
    assert _idp_definition({**OIDC_CREDS, "protocol": "OIDC"})[1] == "OIDC"


def test_oidc_issuer_and_scopes_are_overridable():
    _, _, details, _ = _idp_definition(
        {**OIDC_CREDS, "oidc_issuer": "https://login.corp.example", "authorize_scopes": "openid email"})
    assert details["oidc_issuer"] == "https://login.corp.example"
    assert details["authorize_scopes"] == "openid email"
