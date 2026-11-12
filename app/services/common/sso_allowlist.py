"""SSO allowlist for the Administrator role (spec §8.1, decided 2026-10-21).

Only the the enterprise IdP aliases listed in `RETAIL_OPS_SSO_ADMINS`
(comma-separated, set in the git-ignored deploy/config.env) get the
Administrator role (`platform_admin`). The deploy step `app/infra/sso_admins.py`
writes one PLAT_Users row per listed alias; this module holds the rules both
sides share, so the rows the deploy step writes are exactly the rows the
authorizer accepts:

  normalize_alias(raw)       -> "jdoe" | None     (trim, lower-case, drop optional corp suffix)
  parse(text)                -> (aliases, invalid entries)
  admin_user_id(alias)       -> "U-ADMIN-jdoe"
  admin_row(alias, market)   -> the PLAT_Users row for that alias
  accepts(claims, row)       -> may this Cognito token sign in as that allowlist row?
  resolve(claims, env_prefix)-> user_id of a provisioned row for a federated token, or None

Assumption (stated in the spec): the IdP's `sub` is the user's alias. Cognito
maps it to `preferred_username`; the federated Cognito username is
`<IdP>_<sub>`. An alias is matched case-insensitively and an optional configured corp suffix
suffix is ignored, and only for a token from the IdP recorded on the row
(`federated_identity` = `<IdP>:<alias>`): the Cognito username's prefix before
the first '_' must equal that IdP. A native (unprefixed) token, a token from any
other IdP, or a token for any other alias resolves to nothing, so the authorizer
refuses it exactly as before ("unknown user", 401).
"""
from __future__ import annotations

import os
import re
from typing import Any

from . import authz
from .personas import PLATFORM_ADMIN

ENV_VAR = "RETAIL_OPS_SSO_ADMINS"
MARKER = "sso-allowlist"          # PLAT_Users.provisioned_by on every allowlist row
USER_ID_PREFIX = "U-ADMIN-"
# Optional corporate email suffix dropped from an IdP alias claim (e.g.
# "@corp.example.com"), read at call time via RETAIL_OPS_IDP_ALIAS_DOMAIN.
# Empty by default: a generic OIDC/SAML sub/preferred_username is used as-is.
def _alias_domain() -> str:
    return os.environ.get("RETAIL_OPS_IDP_ALIAS_DOMAIN", "").strip().lower()
# IdP aliases: lower-case letters and digits, optionally '.', '_' or '-'
# inside; short enough that U-ADMIN-<alias> stays a valid claims user_id.
_ALIAS = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")


def normalize_alias(raw: Any) -> str | None:
    """The canonical alias for a config entry or a token claim, or None when it
    is not a valid alias. `" JDoe@Corp.Example.com "` -> `"jdoe"` (when RETAIL_OPS_IDP_ALIAS_DOMAIN=@corp.example.com)."""
    if not isinstance(raw, str):
        return None
    alias = raw.strip().lower()
    _d = _alias_domain()
    if _d:
        alias = alias.removesuffix(_d)
    return alias if _ALIAS.fullmatch(alias) else None


def parse(text: str | None) -> tuple[list[str], list[str]]:
    """(distinct aliases in listed order, entries that are not valid aliases)."""
    aliases: list[str] = []
    invalid: list[str] = []
    for entry in (text or "").split(","):
        if not entry.strip():
            continue
        alias = normalize_alias(entry)
        if alias is None:
            invalid.append(entry.strip())
        elif alias not in aliases:
            aliases.append(alias)
    return aliases, invalid


def admin_user_id(alias: str) -> str:
    return f"{USER_ID_PREFIX}{alias}"


def admin_row(alias: str, market_id: str, idp_name: str = "CorpDirectory") -> dict[str, Any]:
    """The PLAT_Users row for an allowlisted alias: the Administrator role,
    tenant-wide scope (the OEM root of `market_id`), the allowlist marker."""
    return {
        "user_id": admin_user_id(alias),
        "username": alias,
        "groups": [PLATFORM_ADMIN],
        "scope_node_id": "OEM",
        "dealer_id": None,
        "district_id": None,
        "market_id": market_id,
        "active": True,
        "given_name": alias,
        "family_name": None,
        "federated_identity": f"{idp_name}:{alias}",
        "quick_provisioned": False,
        "provisioned_by": MARKER,
    }


def _federated(claims: dict[str, Any]) -> tuple[str, str] | None:
    """(IdP, sub) from a Cognito username `<IdP>_<sub>`, split at the first '_';
    None for a native (unprefixed) username."""
    cognito_username = claims.get("cognito:username")
    if not isinstance(cognito_username, str) or "_" not in cognito_username:
        return None
    idp, sub = cognito_username.split("_", 1)
    return (idp, sub) if idp and sub else None


def _row_idp(row: dict[str, Any]) -> str | None:
    """The IdP an allowlist row was written for (`federated_identity` = `<IdP>:<alias>`)."""
    fid = row.get("federated_identity")
    if not isinstance(fid, str):
        return None
    idp, sep, _alias = fid.partition(":")
    return idp if sep and idp else None


def _token_aliases(claims: dict[str, Any], sub: str) -> list[str]:
    """The aliases a federated token names, most specific claim first."""
    aliases: list[str] = []
    for raw in (claims.get("preferred_username"), claims.get("username"), sub):
        alias = normalize_alias(raw)
        if alias and alias not in aliases:
            aliases.append(alias)
    return aliases


def accepts(claims: dict[str, Any], row: dict[str, Any] | None) -> bool:
    """True when the Cognito token `claims` may sign in as the allowlist row `row`.

    All three must hold: the row carries the allowlist marker; the token's
    Cognito username is `<IdP>_<sub>` with <IdP> (the part before the first '_')
    equal to the IdP recorded on the row; and one of the token's alias claims
    (`preferred_username`, `username`, <sub>) names the row's alias. A native
    token, or a token from any other IdP, is never accepted."""
    if not row or row.get("provisioned_by") != MARKER:
        return False
    federated = _federated(claims)
    if federated is None:
        return False                       # native sign-in: never an allowlist identity
    idp, sub = federated
    if idp != _row_idp(row):
        return False                       # another IdP: not the one the row was written for
    return row.get("user_id") in {admin_user_id(a) for a in _token_aliases(claims, sub)}


def resolve(claims: dict[str, Any], env_prefix: str | None = None) -> str | None:
    """user_id of the allowlist row a FEDERATED Cognito token belongs to, or None.

    Only tokens whose Cognito username carries an IdP prefix (`<IdP>_<sub>`) are
    considered, and a row is returned only when `accepts` holds for it (allowlist
    marker, same IdP as the row, same alias), so no other PLAT_Users row, and no
    token from another IdP, is reachable through this path."""
    federated = _federated(claims)
    if federated is None:
        return None                        # native sign-in: never an allowlist identity
    for alias in _token_aliases(claims, federated[1]):
        row = authz.get_user(admin_user_id(alias), env_prefix)
        if accepts(claims, row):
            return row["user_id"]
    return None
