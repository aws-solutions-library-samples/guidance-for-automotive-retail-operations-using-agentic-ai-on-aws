"""Cognito JWT (RS256) verification.

Production path (hardening pass): PyJWT + cryptography via the retail-ops-dev-jwt
Lambda layer verifies the RS256 Cognito token with a maintained crypto library
and PyJWKClient for JWKS/kid handling. If that library isn't importable (local
dev without the layer), it falls back to the original stdlib-only pure-integer
RSA verify so the same interface works everywhere.

Either way the public interface is unchanged: `verify_cognito_jwt(token, issuer,
client_id) -> claims`, raising `JwtError`. It validates the signature plus
`iss`, `token_use`, `aud`/`client_id`, and `exp`.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
import urllib.request
from typing import Any

_JWKS_CACHE: dict[str, dict] = {}
# SHA-256 DigestInfo prefix for PKCS#1 v1.5 (RFC 8017).
_SHA256_DIGESTINFO = bytes.fromhex("3031300d060960864801650304020105000420")

# Prefer PyJWT + cryptography (the retail-ops-dev-jwt layer) when present.
try:
    import jwt as _pyjwt  # PyJWT
    from jwt import PyJWKClient as _PyJWKClient
    _HAVE_PYJWT = True
except Exception:  # noqa: BLE001 - any import failure -> stdlib fallback
    _HAVE_PYJWT = False

_PYJWK_CLIENTS: dict[str, Any] = {}


class JwtError(Exception):
    pass


def _verify_with_pyjwt(token: str, issuer: str, client_id: str | None) -> dict[str, Any]:
    """Verify using PyJWT + cryptography (maintained library path)."""
    allowed = _audiences(client_id)
    jwks_url = issuer.rstrip("/") + "/.well-known/jwks.json"
    client = _PYJWK_CLIENTS.get(issuer)
    if client is None:
        client = _PyJWKClient(jwks_url, cache_keys=True)
        _PYJWK_CLIENTS[issuer] = client
    try:
        signing_key = client.get_signing_key_from_jwt(token).key
        options = {"require": ["exp", "iss"], "verify_aud": bool(allowed)}
        claims = _pyjwt.decode(
            token, signing_key, algorithms=["RS256"], issuer=issuer,
            audience=allowed or None, options=options,
        )
    except _pyjwt.ExpiredSignatureError:
        raise JwtError("token expired")
    except _pyjwt.InvalidIssuerError:
        raise JwtError("issuer mismatch")
    except _pyjwt.InvalidAudienceError:
        raise JwtError("audience mismatch")
    except Exception as e:  # noqa: BLE001
        raise JwtError(f"invalid token: {e}")
    # Access tokens carry client_id (not aud); PyJWT can't verify that as audience.
    if allowed and claims.get("token_use") == "access" and claims.get("client_id") not in allowed:
        raise JwtError("audience mismatch")
    if claims.get("token_use") not in ("id", "access"):
        raise JwtError("unexpected token_use")
    return claims


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _b64url_int(s: str) -> int:
    return int.from_bytes(_b64url_decode(s), "big")


def _fetch_jwks(issuer: str) -> dict[str, dict]:
    if issuer in _JWKS_CACHE:
        return _JWKS_CACHE[issuer]
    url = issuer.rstrip("/") + "/.well-known/jwks.json"
    with urllib.request.urlopen(url, timeout=8) as r:  # trusted AWS endpoint
        data = json.load(r)
    keys = {k["kid"]: k for k in data.get("keys", [])}
    _JWKS_CACHE[issuer] = keys
    return keys


def _rsa_verify(msg: bytes, sig: bytes, n: int, e: int) -> bool:
    """PKCS#1 v1.5 RSA-SHA256 verify with pure integer math."""
    k = (n.bit_length() + 7) // 8
    if len(sig) != k:
        return False
    s = int.from_bytes(sig, "big")
    if s >= n:
        return False
    m = pow(s, e, n)                      # RSA public-key operation
    em = m.to_bytes(k, "big")
    # Expected EM = 0x00 01 FF..FF 00 || DigestInfo || SHA256(msg)
    digest = hashlib.sha256(msg).digest()
    suffix = b"\x00" + _SHA256_DIGESTINFO + digest
    ps_len = k - len(suffix) - 2
    if ps_len < 8:
        return False
    expected = b"\x00\x01" + (b"\xff" * ps_len) + suffix
    # constant-time-ish compare
    return _consteq(em, expected)


def _consteq(a: bytes, b: bytes) -> bool:
    if len(a) != len(b):
        return False
    r = 0
    for x, y in zip(a, b):
        r |= x ^ y
    return r == 0


def _audiences(client_id: str | None) -> list[str]:
    """One pool issues tokens from several app clients — the native sign-in client
    and the Hosted-UI/federated (SSO) client — so the accepted audience is a
    comma-separated allowlist, not a single value."""
    if not client_id:
        return []
    return [c.strip() for c in client_id.split(",") if c.strip()]


def verify_cognito_jwt(token: str, issuer: str, client_id: str | None) -> dict[str, Any]:
    """Verify a Cognito ID/access token and return its claims, or raise JwtError.

    `client_id` may be a single app-client id or a comma-separated list (native
    + Hosted-UI SSO clients of the same pool); the token must match one of them.

    Uses PyJWT + cryptography when the retail-ops-dev-jwt layer is present; otherwise
    the stdlib pure-integer RSA path below (identical validation semantics)."""
    if _HAVE_PYJWT:
        return _verify_with_pyjwt(token, issuer, client_id)
    return _verify_stdlib(token, issuer, client_id)


def _verify_stdlib(token: str, issuer: str, client_id: str | None) -> dict[str, Any]:
    """Original dependency-free RS256 verify (fallback when PyJWT is absent)."""
    parts = token.split(".")
    if len(parts) != 3:
        raise JwtError("not a JWT (expected 3 segments)")
    try:
        header = json.loads(_b64url_decode(parts[0]))
        claims = json.loads(_b64url_decode(parts[1]))
        sig = _b64url_decode(parts[2])
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise JwtError(f"malformed JWT segment: {e}")
    signing_input = (parts[0] + "." + parts[1]).encode("ascii")

    if header.get("alg") != "RS256":
        raise JwtError(f"unsupported alg {header.get('alg')}")
    kid = header.get("kid")
    keys = _fetch_jwks(issuer)
    jwk = keys.get(kid)
    if jwk is None:
        # key rotation: refresh once
        _JWKS_CACHE.pop(issuer, None)
        jwk = _fetch_jwks(issuer).get(kid)
    if jwk is None:
        raise JwtError("signing key not found in JWKS")

    n, e = _b64url_int(jwk["n"]), _b64url_int(jwk["e"])
    if not _rsa_verify(signing_input, sig, n, e):
        raise JwtError("signature verification failed")

    # standard claim checks
    if claims.get("iss") != issuer:
        raise JwtError("issuer mismatch")
    if claims.get("token_use") not in ("id", "access"):
        raise JwtError("unexpected token_use")
    now = int(time.time())
    if claims.get("exp") is not None and now >= int(claims["exp"]):
        raise JwtError("token expired")
    # ID tokens carry aud=clientId; access tokens carry client_id
    allowed = _audiences(client_id)
    if allowed:
        aud = claims.get("aud") or claims.get("client_id")
        if aud not in allowed:
            raise JwtError("audience mismatch")
    return claims
