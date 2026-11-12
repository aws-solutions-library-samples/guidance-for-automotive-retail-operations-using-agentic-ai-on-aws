"""§5.18.2/§5.18.3 — Resource URL contract, role-aware resolver, context envelope.

Every entity has ONE canonical URL, minted only here (and by the TS twin in
`app/console/src/lib/resourceUrl.ts`):

  resource_url("journey", "J-US-J1")        -> "/r/journey/J-US-J1"
  resource_url("alert", "AL-1", ctx=token)  -> "/r/alert/AL-1?ctx=<signed>"

`resolve(type, id, claims)` (behind GET /nav/resolve) turns a canonical URL into
the best screen the *caller's* role may open, or refuses. Agents, tasks,
notifications, and emails carry canonical URLs only; screens are a resolver
decision, so a link can never send a user somewhere their role cannot go.

The context envelope (`?ctx=`) is a compact HS256 JWT over
{from_app, from_screen, reason_type, reason_id, scope, exp}; the target screen
renders "You're here because…" from the verified claims.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from typing import Any
from urllib.parse import quote

from . import personas

# type -> candidate screens in preference order (the "Resolves to (by role)"
# column of §5.18.2, plus the service types added for Process 2).
RESOLUTION: dict[str, list[str]] = {
    "journey": ["dealer.advisor", "dealer.fni_deal", "dealer.journey", "customer.garage"],
    "customer": ["dealer.customer360", "dealer.assistant", "customer.garage"],
    "dealer": ["dealer.cockpit", "oem.account"],
    "account": ["oem.account", "dealer.cockpit"],
    "deal": ["dealer.fni_deal"],
    "alert": ["oem.alert", "dealer.cockpit"],
    "task": ["dealer.task"],
    "plan": ["oem.plan"],
    "campaign": ["marketing.campaign", "dealer.campaigns"],
    "vin": ["dealer.inventory_vin", "service.history", "customer.shop"],
    "appointment": ["dealer.leads", "dealer.delivery", "service.board", "customer.garage"],
    "service_journey": ["service.journey", "dealer.service_lane", "customer.garage_service"],
    "ro": ["service.journey", "customer.garage_service"],
    "estimate": ["service.estimate", "customer.garage_service"],
    "approval": ["approvals", "admin.approvals"],
}
TYPES = frozenset(RESOLUTION)

_CANON_RE = re.compile(r"^/r/([a-z_]+)/([^?#]+)(?:\?ctx=([A-Za-z0-9_\-\.]+))?$")
_CTX_TTL_S = 7 * 24 * 3600


class UnknownType(ValueError):
    pass


class InvalidContext(ValueError):
    pass


def resource_url(rtype: str, rid: str, ctx: str | None = None) -> str:
    if rtype not in TYPES:
        raise UnknownType(f"{rtype!r} is not a resource type ({sorted(TYPES)})")
    if not rid:
        raise ValueError("resource id is required")
    url = f"/r/{rtype}/{quote(str(rid), safe='-_.~')}"
    return f"{url}?ctx={ctx}" if ctx else url


def parse(url: str) -> dict[str, str | None]:
    m = _CANON_RE.match(url or "")
    if not m or m.group(1) not in TYPES:
        raise UnknownType(f"{url!r} is not a canonical resource URL")
    return {"type": m.group(1), "id": m.group(2), "ctx": m.group(3)}


def is_canonical(url: str | None) -> bool:
    try:
        parse(url or "")
        return True
    except UnknownType:
        return False


# --- context envelope -------------------------------------------------------

def _secret() -> bytes:
    secret = os.environ.get("RETAIL_OPS_CTX_SECRET")
    if not secret:
        if os.environ.get("RETAIL_OPS_STAGE", "dev") not in ("dev", "demo"):
            raise InvalidContext("RETAIL_OPS_CTX_SECRET must be set outside dev/demo")
        secret = "retail-ops-dev-ctx-secret-change-me"   # dev/demo only
    return secret.encode()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign_ctx(from_app: str, from_screen: str, reason_type: str, reason_id: str,
             scope: dict[str, Any] | None = None, ttl_s: int = _CTX_TTL_S) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps({
        "from_app": from_app, "from_screen": from_screen, "reason_type": reason_type,
        "reason_id": reason_id, "scope": scope or {}, "exp": int(time.time()) + ttl_s,
    }, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64(hmac.new(_secret(), f"{header}.{body}".encode(), hashlib.sha256).digest())
    return f"{header}.{body}.{sig}"


def verify_ctx(token: str) -> dict[str, Any]:
    try:
        header, body, sig = token.split(".")
    except ValueError as e:
        raise InvalidContext("malformed context envelope") from e
    want = _b64(hmac.new(_secret(), f"{header}.{body}".encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(want, sig):
        raise InvalidContext("context envelope signature mismatch")
    claims = json.loads(_unb64(body))
    if int(claims.get("exp") or 0) < time.time():
        raise InvalidContext("context envelope expired")
    return claims


def because(claims: dict[str, Any]) -> str:
    """The "You're here because…" strip text for verified ctx claims."""
    return f"From {claims.get('from_app')} › {claims.get('from_screen')}: " \
           f"{claims.get('reason_type')} {claims.get('reason_id')}"


# --- resolver ---------------------------------------------------------------

def resolve(rtype: str, rid: str, role: str | None, ctx: str | None = None) -> dict[str, Any]:
    """Best permitted screen for `role`, or allowed=False with the reason.

    Scope (dealer/district/self) is enforced by the screen's own API call; the
    resolver decides only *which* screen, so it never leaks data itself."""
    if rtype not in TYPES:
        raise UnknownType(f"{rtype!r} is not a resource type")
    out: dict[str, Any] = {"type": rtype, "id": rid, "role": role,
                           "canonical": resource_url(rtype, rid)}
    if ctx:
        try:
            claims = verify_ctx(ctx)
            out["context"] = {**claims, "because": because(claims)}
        except InvalidContext as e:
            out["context_error"] = str(e)
    for screen in RESOLUTION[rtype]:
        if role and personas.may_open(role, screen):
            url = personas.route(screen, rid)
            if ctx and "context" in out:
                url += ("&" if "?" in url else "?") + f"ctx={ctx}"
            return {**out, "allowed": True, "screen": screen, "url": url}
    fallback = personas.home(role) if role in personas.PERSONAS else None
    return {**out, "allowed": False, "screen": None, "url": None,
            "home": personas.route(fallback) if fallback else None,
            "reason": f"role {role!r} may not open any screen for a {rtype}"}
