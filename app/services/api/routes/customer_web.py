"""Customer Web routes (spec §6 `/garage/*`, `/garage/service/*`, `/customers/{id}/consent`,
`/shop/*`; §8.1 magic links).

Customer routes are SELF-SCOPE: the customer id always comes from the session
(authorizer claims), never from the request. Staff may read/update a customer's
consent and mint a magic link only for customers in their dealer scope.

  POST /auth/magic-link                 staff: mint a link for a customer {customer_id, purpose}
  POST /auth/magic-link/redeem          customer: exchange the link (Bearer mlk:<link>) for a session
  GET  /garage/journey | builds | appointments | finance | trade | documents | ownership
  GET  /garage/builds/{id}/availability
  PATCH /garage/appointments/{id}       {action: cancel|reschedule, slot_start?}
  POST /garage/finance/prequal          {amount?, down?, term_months?}
  POST /garage/trade/refresh            {mileage?, condition?}
  POST /garage/survey                   {rating 1-5, comment?, vin?} delivery survey
  POST /garage/data-request             {kind: download|delete}
  GET/PUT /customers/{id}/consent
  GET  /garage/service | /garage/service/slots?vin= | /garage/service/history | /garage/service/{id}
  POST /garage/service/book             {vin, services[], wait_type, slot_start?}
  POST /garage/service/estimates/{id}/decisions  {decisions:[{line_id, decision, reason?}]}
  POST /shop/intake                     {answers}
  POST /shop/shortlist                  {answers, zip?}
  POST /shop/builds                     {model_id, trim_id?, packages?, color?}
  POST /shop/test-drive                 {slot_start?}
  GET/POST /shop/chat                   thread / {text}
"""
from __future__ import annotations

import re

from ...common import ddb
from ...customer import magic_link, portal, service_portal, shop
from .. import routing as r

# staff roles allowed to see customer contact/consent (not technician / parts — §5.20.5)
_CONSENT_STAFF = frozenset({"dealer_advisor", "dealer_bdc", "dealer_fni", "dealer_manager", "dealer_service",
                            "dealer_service_manager", "dealer_delivery", "admin_tenant", "admin_dealer", "oem_cx",
                            "platform_admin"})


def _cid(claims):
    return r.customer(claims)


def _actor(claims):
    if claims.get("role") == "customer":
        return {"type": "customer", "customer_id": claims.get("customer_id")}
    return {"type": "user", "user_id": claims.get("user_id"), "role": claims.get("role")}


def _purpose(event) -> str | None:
    return ((event.get("requestContext") or {}).get("authorizer") or {}).get("purpose") or None


# --- magic links -----------------------------------------------------------------

def _ml_issue(event, claims, _m):
    r.roles(claims, _CONSENT_STAFF)
    b = r.body(event)
    r.require(b, "customer_id")
    cust = ddb.get_item("DEALER_Customers", {"customer_id": b["customer_id"]})
    if cust is None:
        raise r.Bad(404, "customer not found")
    r.dealer_in_scope(claims, cust.get("dealer_id"))
    out = magic_link.issue_magic_link(b["customer_id"], b.get("purpose") or "garage",
                                      int(b.get("ttl_s") or magic_link.LINK_TTL_S))
    return 201, out


def _ml_redeem(event, claims, _m):
    _cid(claims)
    hdr = {k.lower(): v for k, v in (event.get("headers") or {}).items()}.get("authorization") or ""
    tok = hdr[7:].strip() if hdr.lower().startswith("bearer ") else hdr.strip()
    if not tok.startswith(magic_link.PREFIX):
        tok = r.body(event).get("token") or ""
    if not tok:
        raise r.Bad(400, "no magic-link token presented")
    try:
        out = magic_link.redeem(tok)
    except magic_link.InvalidToken as e:
        raise r.Bad(401, f"link cannot be used: {e}") from e
    if out["customer_id"] != claims.get("customer_id"):
        raise r.Bad(403, "link belongs to another customer")
    return 200, out


# --- My Garage -----------------------------------------------------------------------

def _get(fn):
    return lambda event, claims, _m: (200, fn(_cid(claims)))


def _availability(event, claims, m):
    return 200, portal.build_availability(_cid(claims), m.group(1))


def _appt_patch(event, claims, m):
    b = r.body(event)
    r.require(b, "action")
    return 200, portal.change_appointment(_cid(claims), m.group(1), action=b["action"], slot_start=b.get("slot_start"))


def _prequal(event, claims, _m):
    b = r.body(event)
    return 201, portal.start_prequal(_cid(claims), amount=b.get("amount"), down=b.get("down") or 0,
                                     term_months=b.get("term_months"))


def _trade_refresh(event, claims, _m):
    b = r.body(event)
    if b.get("mileage") is None and not b.get("condition"):
        raise r.Bad(400, "give a mileage or condition to refresh the value")
    return 200, portal.refresh_trade(_cid(claims), mileage=b.get("mileage"), condition=b.get("condition"))


def _survey(event, claims, _m):
    """Delivery survey from My Garage (customer self-scope)."""
    b = r.body(event)
    r.require(b, "rating")
    return 201, portal.submit_delivery_survey(_cid(claims), rating=b.get("rating"),
                                              comment=b.get("comment"), vin=b.get("vin"))


def _data_request(event, claims, _m):
    b = r.body(event)
    r.require(b, "kind")
    return 201, portal.request_data(_cid(claims), b["kind"])


def _consent_target(claims, cid):
    if claims.get("role") == "customer":
        if claims.get("customer_id") != cid:
            raise r.Bad(403, "you can only manage your own preferences")
        return
    r.roles(claims, _CONSENT_STAFF)
    cust = ddb.get_item("DEALER_Customers", {"customer_id": cid})
    if cust is None:
        raise r.Bad(404, "customer not found")
    r.dealer_in_scope(claims, cust.get("dealer_id"))


def _consent_get(event, claims, m):
    _consent_target(claims, m.group(1))
    return 200, portal.get_consent(m.group(1))


def _consent_put(event, claims, m):
    _consent_target(claims, m.group(1))
    return 200, portal.put_consent(m.group(1), r.body(event), actor=_actor(claims))


# --- My Garage › Service ----------------------------------------------------------------

def _svc_slots(event, claims, _m):
    q = r.qs(event)
    if not q.get("vin"):
        raise r.Bad(400, "query must include vin")
    return 200, service_portal.slots(_cid(claims), q["vin"], int(q.get("days") or 5))


def _svc_book(event, claims, _m):
    b = r.body(event)
    r.require(b, "vin")
    return 201, service_portal.book(_cid(claims), b["vin"], services=b.get("services"),
                                    wait_type=b.get("wait_type") or "drop", slot_start=b.get("slot_start"))


def _svc_detail(event, claims, m):
    return 200, service_portal.detail(_cid(claims), m.group(1))


def _svc_decide(event, claims, m):
    b = r.body(event)
    via = "magic_link" if (_purpose(event) or "").startswith(("estimate", "service")) else "my_garage"
    return 200, service_portal.decide_lines(_cid(claims), m.group(1), b.get("decisions") or [], via=via)


# --- /shop -------------------------------------------------------------------------------

def _shop_intake(event, claims, _m):
    return 200, shop.intake(_cid(claims), r.body(event).get("answers"))


def _shop_shortlist(event, claims, _m):
    b = r.body(event)
    zip5 = b.get("zip")
    if zip5 is not None and not re.fullmatch(r"[0-9A-Za-z \-]{3,10}", str(zip5)):
        raise r.Bad(400, "invalid postal code")
    return 200, shop.shortlist(_cid(claims), b.get("answers"), zip5)


def _shop_build(event, claims, _m):
    b = r.body(event)
    r.require(b, "model_id")
    return 201, shop.save_build(_cid(claims), b["model_id"], b.get("trim_id"), b.get("packages"), b.get("color"))


def _shop_td(event, claims, _m):
    return 201, shop.test_drive(_cid(claims), r.body(event).get("slot_start"))


def _shop_chat_get(event, claims, _m):
    return 200, shop.thread(_cid(claims))


def _shop_chat_post(event, claims, _m):
    b = r.body(event)
    r.require(b, "text")
    return 200, shop.chat(_cid(claims), b["text"])


ROUTES = [
    ("POST", re.compile(r"^/auth/magic-link$"), _ml_issue),
    ("POST", re.compile(r"^/auth/magic-link/redeem$"), _ml_redeem),
    ("GET", re.compile(r"^/garage/journey$"), _get(portal.journey)),
    ("GET", re.compile(r"^/garage/builds$"), _get(portal.builds)),
    ("GET", re.compile(r"^/garage/builds/([^/]+)/availability$"), _availability),
    ("GET", re.compile(r"^/garage/appointments$"), _get(portal.appointments)),
    ("PATCH", re.compile(r"^/garage/appointments/([^/]+)$"), _appt_patch),
    ("GET", re.compile(r"^/garage/finance$"), _get(portal.finance)),
    ("POST", re.compile(r"^/garage/finance/prequal$"), _prequal),
    ("GET", re.compile(r"^/garage/trade$"), _get(portal.trade)),
    ("POST", re.compile(r"^/garage/trade/refresh$"), _trade_refresh),
    ("GET", re.compile(r"^/garage/documents$"), _get(portal.documents)),
    ("GET", re.compile(r"^/garage/ownership$"), _get(portal.ownership)),
    ("POST", re.compile(r"^/garage/survey$"), _survey),
    ("POST", re.compile(r"^/garage/data-request$"), _data_request),
    ("GET", re.compile(r"^/customers/([^/]+)/consent$"), _consent_get),
    ("PUT", re.compile(r"^/customers/([^/]+)/consent$"), _consent_put),
    ("GET", re.compile(r"^/garage/service$"), _get(service_portal.overview)),
    ("GET", re.compile(r"^/garage/service/slots$"), _svc_slots),
    ("GET", re.compile(r"^/garage/service/history$"), _get(service_portal.history)),
    ("POST", re.compile(r"^/garage/service/book$"), _svc_book),
    ("POST", re.compile(r"^/garage/service/estimates/([^/]+)/decisions$"), _svc_decide),
    ("GET", re.compile(r"^/garage/service/([^/]+)$"), _svc_detail),
    ("POST", re.compile(r"^/shop/intake$"), _shop_intake),
    ("POST", re.compile(r"^/shop/shortlist$"), _shop_shortlist),
    ("POST", re.compile(r"^/shop/builds$"), _shop_build),
    ("POST", re.compile(r"^/shop/test-drive$"), _shop_td),
    ("GET", re.compile(r"^/shop/chat$"), _shop_chat_get),
    ("POST", re.compile(r"^/shop/chat$"), _shop_chat_post),
]

ERRORS = [
    (portal.NotFound, 404), (service_portal.NotFound, 404),
    (service_portal.Forbidden, 403),
    (service_portal.NotDecidable, 409),
    (portal.Invalid, 400), (service_portal.Invalid, 400), (shop.Invalid, 400),
    (magic_link.InvalidToken, 400),
]
