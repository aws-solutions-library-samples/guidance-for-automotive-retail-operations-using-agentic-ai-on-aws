"""L4 — API Gateway Lambda authorizer (REQUEST/TOKEN) + local equivalent.

Resolves the caller from the Authorization header to a principal and a set of
context claims (role, scope_node_id, market_id, dealer_ids) that the proxy
handler enforces. In production the token is a Cognito JWT (app-staff pool); for
the local slice we accept a dev token of the form `Bearer user:<user_id>` that
resolves against PLAT_Users — the SAME resolution path, so local == cloud.

Two entry points:
  lambda_handler(event, ctx)  — API Gateway TOKEN-authorizer contract
  resolve_principal(header)   — plain function the dev server calls

API Gateway caches the returned policy per token; claims ride in
`context` and reappear on the proxy event as
`requestContext.authorizer.<key>`.
"""
from __future__ import annotations

import json
import os
from typing import Any

from ..common import authz, jwt_verify, sso_allowlist

# Cognito config (set on the deployed Lambda; empty locally unless exported).
COGNITO_ISSUER = os.environ.get("RETAIL_OPS_COGNITO_ISSUER", "")
COGNITO_CLIENT_ID = os.environ.get("RETAIL_OPS_COGNITO_CLIENT_ID", "")
# Dev token accepted only when explicitly allowed (default on for local/dev).
ALLOW_DEV_TOKEN = os.environ.get("RETAIL_OPS_ALLOW_DEV_TOKEN", "1") == "1"


def _user_by_email(email: str | None, env_prefix: str | None):
    """Resolve a PLAT_Users.user_id by email (federated-login fallback)."""
    return _user_by_attr("email", email, env_prefix)


def _user_by_attr(attr: str, value: str | None, env_prefix: str | None):
    """Scan PLAT_Users for the first row with `attr == value`."""
    if not value:
        return None
    try:
        from ..common import ddb
        rows = ddb.scan_filter("PLAT_Users", attr, value, env_prefix, limit=1)
        return rows[0].get("user_id") if rows else None
    except Exception:
        return None


def resolve_principal(auth_header: str | None, env_prefix: str | None = None) -> dict[str, Any]:
    """Header -> {user_id, role, scope_node_id, market_id, dealer_ids(csv), is_oem}.

    Raises authz.Unauthenticated / authz.Forbidden. dealer_ids is a CSV string
    because API Gateway authorizer context values must be strings/numbers/bools.
    """
    if not auth_header:
        raise authz.Unauthenticated("missing Authorization header")
    token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else auth_header.strip()

    # 0a) Customer magic-link token (signed, purpose-limited; §8.1 passwordless).
    # Always accepted — it is a real credential, not a dev token.
    if token.startswith("mlk:"):
        from ..customer import magic_link
        try:
            return magic_link.principal(token, env_prefix)
        except magic_link.InvalidToken as e:
            raise authz.Unauthenticated(f"invalid customer link: {e}")

    # 0) Customer dev token: cust:<customer_id>. Customers are NOT staff (separate
    # Cognito pool in prod); they get a customer principal, not a PLAT_Users scope.
    if token.startswith("cust:"):
        if not ALLOW_DEV_TOKEN:
            raise authz.Unauthenticated("dev token disabled")
        cid = token[len("cust:"):]
        if not cid:
            raise authz.Unauthenticated("empty customer id")
        return {
            "user_id": cid, "role": "customer", "scope_node_id": "",
            "market_id": "", "dealer_ids": "", "is_oem": "false",
            "customer_id": cid,
        }

    claims: dict[str, Any] | None = None   # set for a Cognito JWT only

    # 1) Dev token: user:<user_id> (allowed in dev; disable via RETAIL_OPS_ALLOW_DEV_TOKEN=0).
    if token.startswith("user:"):
        if not ALLOW_DEV_TOKEN:
            raise authz.Unauthenticated("dev token disabled")
        user_id = token[len("user:"):]
    # 2) Cognito JWT: verify signature + claims, map username -> user_id.
    elif token.count(".") == 2 and COGNITO_ISSUER:
        try:
            claims = jwt_verify.verify_cognito_jwt(token, COGNITO_ISSUER, COGNITO_CLIENT_ID or None)
        except jwt_verify.JwtError as e:
            raise authz.Unauthenticated(f"invalid Cognito token: {e}")
        user_id = claims.get("cognito:username") or claims.get("username") or claims.get("sub")
        if not user_id:
            raise authz.Unauthenticated("token carries no username claim")
        # Federated sign-in: Cognito prefixes the username with the IdP name
        # (e.g. 'CorpDirectory_<sub>'). We need that identity to land on the
        # same PLAT_Users row the user would reach via native sign-in, so try
        # several resolutions in order of specificity before giving up:
        #   1) the raw username (works for native tokens unchanged)
        #   2) '<IdP>_<sub>' with the IdP prefix stripped — covers OIDC IdPs
        #      whose `sub` is the user_id itself
        #   3) the `email` claim — present on most SAML IdPs, and on OIDC IdPs
        #      that return the `email` scope
        #   4) a `username`/`preferred_username` claim matched against the
        #      PLAT_Users.username field — this is the path for the enterprise IdP,
        #      whose only claims are sub + groups + amr (no email).
        #   5) the SSO allowlist (spec §8.1, 2026-10-21): the same alias claims,
        #      lower-cased and without the optional configured corp suffix, looked up as the
        #      U-ADMIN-<alias> row the sso-admins deploy step wrote. Federated
        #      tokens from the IdP recorded on the row only, and only rows
        #      carrying the allowlist marker, so it reaches no other row; an
        #      alias that is not listed stays unknown.
        if authz.get_user(user_id, env_prefix) is None:
            candidates: list[tuple[str, str | None]] = []
            if "_" in user_id:
                candidates.append(("get_user", user_id.split("_", 1)[1]))
            candidates.extend([
                ("email", claims.get("email")),
                ("username", claims.get("username") or claims.get("preferred_username")),
            ])
            resolved = False
            for attr, value in candidates:
                if not value:
                    continue
                hit = authz.get_user(value, env_prefix) if attr == "get_user" \
                    else _user_by_attr(attr, value, env_prefix)
                if hit:
                    user_id = hit if attr != "get_user" else value
                    resolved = True
                    break
            if not resolved:
                user_id = sso_allowlist.resolve(claims, env_prefix) or user_id
    else:
        raise authz.Unauthenticated("unsupported token (Cognito JWT or 'user:<id>' dev token)")

    user = authz.get_user(user_id, env_prefix)
    if user is None:
        raise authz.Unauthenticated(f"unknown user {user_id!r}")
    # An Administrator row (allowlist marker) is reached from a Cognito token
    # only under the allowlist rule, whichever step above found it: a sign-in
    # through the IdP recorded on the row, naming the row's alias (spec §8.1).
    # A native token or another IdP's token stays "unknown user"; every other
    # row resolves exactly as before.
    if claims is not None and user.get("provisioned_by") == sso_allowlist.MARKER \
            and not sso_allowlist.accepts(claims, user):
        raise authz.Unauthenticated(f"unknown user {user_id!r}")
    scope = authz.resolve_scope(user, env_prefix)
    return {
        "user_id": scope["user_id"],
        "role": scope["role"] or "",
        # the FULL group list + per-user screen grants, so authorization can
        # union screens across roles (admin "assign roles/screens to a user")
        "groups": ",".join(scope.get("groups") or []),
        "extra_screens": ",".join(user.get("extra_screens") or []),
        "scope_node_id": scope["scope_node_id"] or "",
        "market_id": scope["market_id"] or "",
        "dealer_ids": ",".join(sorted(scope["dealer_ids"])),
        "is_oem": "true" if scope["is_oem"] else "false",
        "customer_id": "",  # staff principals carry no customer identity
    }


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """API Gateway TOKEN authorizer. Returns an IAM policy + context claims."""
    header = event.get("authorizationToken") or event.get("headers", {}).get("Authorization")
    method_arn = event.get("methodArn", "*")
    try:
        claims = resolve_principal(header)
    except Exception:
        # Any failure to establish identity denies. Raising "Unauthorized" yields
        # 401 at the gateway; anything else would surface as a 500, so we map all
        # auth failures (bad token, malformed JWT, verify error) to Unauthorized.
        raise Exception("Unauthorized")
    # Grant across the whole API/stage, not just the one method ARN. The
    # authorizer result is cached per token; a method-specific Resource would
    # authorize only the first route hit and 403 the rest. Coarse resource
    # authorization is safe here because per-resource scope (dealer/market) is
    # enforced in the handler from the context claims below.
    api_resource = _api_wildcard(method_arn)
    return {
        "principalId": claims["user_id"],
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [{
                "Action": "execute-api:Invoke",
                "Effect": "Allow",
                "Resource": api_resource,
            }],
        },
        "context": claims,  # all values are strings (API GW requirement)
    }


def _api_wildcard(method_arn: str) -> str:
    """From arn:.../<apiId>/<stage>/<METHOD>/<path...> -> arn:.../<apiId>/*/*.

    Authorizes every method+path on the API so one cached authorization covers
    all routes for the token."""
    # arn:aws:execute-api:region:acct:apiId/stage/METHOD/resource
    parts = method_arn.split(":", 5)
    if len(parts) < 6 or "/" not in parts[5]:
        return "*"
    prefix = parts[5].split("/", 1)[0]  # apiId
    parts[5] = f"{prefix}/*/*"
    return ":".join(parts)


if __name__ == "__main__":  # tiny manual check
    import sys
    hdr = sys.argv[1] if len(sys.argv) > 1 else "Bearer user:U-US-00002"
    print(json.dumps(resolve_principal(hdr), default=str, indent=2))
