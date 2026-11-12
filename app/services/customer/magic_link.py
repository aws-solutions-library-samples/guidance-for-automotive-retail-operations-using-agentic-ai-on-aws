"""Customer magic links (spec §8.1 passwordless; §5.12, §5.10, acceptance #27).

Every nurture message, estimate-presented SMS/email, and chat resume link
carries a signed, purpose-limited link token. The customer's browser presents
it once to `POST /auth/magic-link/redeem` (as `Authorization: Bearer mlk:<link>`)
and receives a short-lived customer session token, also `mlk:`-prefixed, which
the authorizer accepts as a customer principal scoped to that one customer.

Token format: `<b64 header>.<b64 claims>.<b64 HMAC-SHA256>` — the same compact
HS256 JWT as the §5.18.3 context envelope, signed with RETAIL_OPS_CTX_SECRET.
Claims: {kind: "link"|"session", sub: customer_id, purpose, exp, iat, jti}.

  issue_magic_link(customer_id, purpose, ttl_s) -> {token, url, expires_at}
  verify(token)                                 -> claims (raises InvalidToken)
  redeem(link_token)                            -> {token (session), expires_at, purpose}
  principal(token)                              -> authorizer customer claims

The production identity for customers is the `app-customers` Cognito pool with
email-OTP custom auth; these tokens are the link half of that design and work
against the same authorizer contract.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Any

from ..common import ddb, resource_url

PREFIX = "mlk:"
LINK_TTL_S = 72 * 3600          # a link in a message stays valid for 3 days
SESSION_TTL_S = 8 * 3600        # a redeemed session lasts a working day
# purpose -> landing path (purpose values are "<kind>" or "<kind>:<resource id>")
_LANDING = {
    "garage": "/garage",
    "service": "/garage/service/{id}",
    "estimate": "/garage/service/{id}",
    "chat": "/shop/chat",
    "shop": "/shop",
}


class InvalidToken(ValueError):
    pass


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(claims: dict[str, Any]) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64(hmac.new(resource_url._secret(), f"{header}.{body}".encode(), hashlib.sha256).digest())
    return f"{header}.{body}.{sig}"


def _landing(purpose: str) -> str:
    kind, _, rid = purpose.partition(":")
    path = _LANDING.get(kind, "/garage")
    return path.replace("{id}", rid) if "{id}" in path else path


def issue_magic_link(customer_id: str, purpose: str = "garage", ttl_s: int = LINK_TTL_S,
                     env_prefix: str | None = None) -> dict[str, Any]:
    """Mint a link for a customer. The customer must exist (no links to nobody)."""
    if not customer_id:
        raise InvalidToken("customer_id is required")
    if (purpose.partition(":")[0]) not in _LANDING:
        raise InvalidToken(f"unknown purpose {purpose!r}")
    cust = ddb.get_item("DEALER_Customers", {"customer_id": customer_id}, env_prefix)
    if cust is None:
        raise InvalidToken(f"unknown customer {customer_id}")
    now = int(time.time())
    # `mkt`: the customer's market, signed into the token so the authorizer needs no lookup
    tok = _sign({"kind": "link", "sub": customer_id, "purpose": purpose, "iat": now,
                 "exp": now + int(ttl_s), "jti": uuid.uuid4().hex[:16], "mkt": cust.get("market_id") or ""})
    # the link carries the token as `?token=` so the console's API client
    # presents it on the very first request (no pre-auth page needed)
    return {"token": tok, "url": f"/garage/login?token={PREFIX}{tok}", "landing": _landing(purpose),
            "expires_at": now + int(ttl_s), "purpose": purpose}


def verify(token: str) -> dict[str, Any]:
    tok = token[len(PREFIX):] if token.startswith(PREFIX) else token
    try:
        header, body, sig = tok.split(".")
    except ValueError as e:
        raise InvalidToken("malformed token") from e
    want = _b64(hmac.new(resource_url._secret(), f"{header}.{body}".encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(want, sig):
        raise InvalidToken("signature mismatch")
    try:
        claims = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError) as e:
        raise InvalidToken("malformed claims") from e
    if claims.get("kind") not in ("link", "session") or not claims.get("sub"):
        raise InvalidToken("not a customer token")
    if int(claims.get("exp") or 0) < time.time():
        raise InvalidToken("token expired")
    return claims


def redeem(link_token: str) -> dict[str, Any]:
    """Exchange a link token for a session token (keeps the purpose for audit)."""
    c = verify(link_token)
    if c["kind"] != "link":
        raise InvalidToken("only a link token can be redeemed")
    now = int(time.time())
    exp = min(now + SESSION_TTL_S, int(c["exp"]) + SESSION_TTL_S)
    session = _sign({"kind": "session", "sub": c["sub"], "purpose": c.get("purpose", "garage"),
                     "iat": now, "exp": exp, "jti": uuid.uuid4().hex[:16], "link_jti": c.get("jti"),
                     "mkt": c.get("mkt") or ""})
    return {"token": PREFIX + session, "expires_at": exp, "purpose": c.get("purpose"),
            "landing": _landing(c.get("purpose", "garage")), "customer_id": c["sub"]}


def principal(token: str, env_prefix: str | None = None) -> dict[str, Any]:
    """Authorizer context claims for an `mlk:` token (link or session).

    Runs in the API Gateway authorizer, whose role reads only PLAT_Users and
    PLAT_OrgHierarchy (iam_roles.AUTHORIZER_READ_TABLES, least privilege on the
    untrusted edge). So it must NOT touch DEALER_Customers: that read was denied and
    every customer link failed as "Unauthorized". The signed token is the whole proof;
    the market rides in it (`mkt`, set at issue) and customer routes resolve the rest
    from customer_id, exactly as for the `cust:` principal."""
    c = verify(token)
    return {
        "user_id": c["sub"], "role": "customer", "scope_node_id": "",
        "market_id": c.get("mkt") or "", "dealer_ids": "", "is_oem": "false",
        "customer_id": c["sub"], "token_kind": c["kind"], "purpose": c.get("purpose") or "",
    }
