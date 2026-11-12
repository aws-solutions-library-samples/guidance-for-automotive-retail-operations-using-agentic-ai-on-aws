# ruff: noqa: S101
"""Persona access fixes and per-persona landing (spec §5.2, §5.18.1, §13 2026-10-28).

- The service advisor uses Trade-In: valuation and upgrade prospects serve it;
  the purchase-journey writes stay with sales.
- The sales advisor holds the Lead Desk, as the API's Lead Desk role set already did.
- OEM Sales Ops holds no Admin Console screen.
- Every role's home is in its own screen set and has a console page.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.api.routes import sales_floor
from app.services.common import authz, personas
from tests.app.conftest import api_event, call

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app" / "console" / "src" / "app"

SERVICE = dict(user="U-US-00007", role="dealer_service", dealer_ids="D-101")
ADV = dict(user="U-US-00002", role="dealer_advisor", dealer_ids="D-101")
SALES_OPS = dict(user="U-US-00047", role="oem_sales_ops", is_oem=True)


def _vin(fdb) -> str:
    """A D-101 owner's vehicle with valuation history, as test_sales_floor_api picks it."""
    return next(v["vin"] for v in fdb.rows("DEALER_Vehicles") if v.get("dealer_id") == "D-101"
                and any(x.get("customer_id") == v["customer_id"] for x in fdb.rows("DEALER_Valuations")))


# --- the persona matrix -------------------------------------------------------------

def test_every_home_is_in_its_screen_set_and_has_a_page():
    for role, (home, screens) in personas.PERSONAS.items():
        assert home in screens, role
        route = personas.SCREENS[home]
        assert "{id}" not in route and "?" not in route, (role, route)
        page = APP.joinpath(*route.strip("/").split("/")) / "page.tsx" if route != "/" else APP / "page.tsx"
        assert page.exists(), (role, route)


def test_sales_advisor_holds_the_lead_desk():
    assert personas.may_open("dealer_advisor", "dealer.leads")
    assert "dealer_advisor" in sales_floor.LEAD_ROLES


def test_oem_sales_ops_holds_no_admin_screen():
    home, screens = personas.PERSONAS["oem_sales_ops"]
    assert home == "oem.comms"
    assert not any(s.startswith("admin.") for s in screens)
    assert screens == {"oem.comms", "oem.reports", "notifications"}
    assert "oem_sales_ops" not in authz.ADMIN_ROLES


def test_service_advisor_holds_trade_in_and_the_route_set_matches():
    assert personas.may_open("dealer_service", "dealer.trade_in")
    # every role that holds the Trade-In screen is served by its routes
    holders = {r for r, (_h, sc) in personas.PERSONAS.items() if "dealer.trade_in" in sc} - {personas.PLATFORM_ADMIN}
    assert holders <= sales_floor.TRADE_IN
    # a small dedicated set: the shared sales read set is unchanged
    assert sales_floor.TRADE_IN == authz.ADVISOR_READ_ROLES | {"dealer_service"}
    assert "dealer_service" not in authz.ADVISOR_READ_ROLES


# --- the API ------------------------------------------------------------------------

def test_service_advisor_values_trades_and_lists_prospects(fake_ddb):
    code, v = call(api_event("POST", "/trade-in/valuation", **SERVICE, body={"vin": _vin(fake_ddb)}))
    assert code == 200, v
    assert v["range"]["low"] <= v["trade_value"] <= v["range"]["high"]
    code, p = call(api_event("GET", "/trade-in/prospects", **SERVICE))
    assert code == 200 and "prospects" in p, p


def test_service_advisor_valuation_never_persists(fake_ddb):
    before = len(fake_ddb.rows("DEALER_Valuations"))
    code, _ = call(api_event("POST", "/trade-in/valuation", **SERVICE, body={"vin": _vin(fake_ddb), "persist": True}))
    assert code == 200
    assert len(fake_ddb.rows("DEALER_Valuations")) == before


def test_purchase_journey_writes_stay_with_sales(fake_ddb):
    code, _ = call(api_event("POST", "/trade-in/payment-delta", **SERVICE, body={"journey_id": "J-US-J1"}))
    assert code == 403
    code, _ = call(api_event("POST", "/journeys/J-US-J1/trade", **SERVICE, body={}))
    assert code == 403
    code, _ = call(api_event("GET", "/journeys/J-US-J1/advisor", **SERVICE))
    assert code == 403


@pytest.mark.parametrize("role", sorted(authz.ADVISOR_READ_ROLES))
def test_sales_roles_keep_trade_in(fake_ddb, role):
    who = dict(user="U-T", role=role, dealer_ids="D-101")
    assert call(api_event("POST", "/trade-in/valuation", **who, body={"vin": _vin(fake_ddb)}))[0] == 200
    assert call(api_event("GET", "/trade-in/prospects", **who))[0] == 200


@pytest.mark.parametrize("role", ["dealer_technician", "dealer_parts", "dealer_service_manager"])
def test_other_service_roles_still_refused(fake_ddb, role):
    who = dict(user="U-T", role=role, dealer_ids="D-101")
    assert call(api_event("POST", "/trade-in/valuation", **who, body={"vin": _vin(fake_ddb)}))[0] == 403
    assert call(api_event("GET", "/trade-in/prospects", **who))[0] == 403


def test_oem_read_across_on_prospects_is_unchanged(fake_ddb):
    dm = dict(user="U-US-00044", role="oem_dm", is_oem=True)
    assert call(api_event("GET", "/trade-in/prospects", **dm, query={"dealer": "D-101"}))[0] == 200


def test_sales_advisor_opens_the_lead_queue(fake_ddb):
    code, q = call(api_event("GET", "/leads/queue", **ADV))
    assert code == 200, q
    code, me = call(api_event("GET", "/me/home", **ADV))
    assert code == 200 and "dealer.leads" in me["screens"] and me["url"] == "/dealer/journeys"


# One seed user per role and the GETs its home page makes on load (app/console/src/app/<home>/page.tsx).
HOME_READS: dict[str, tuple[str, str, list[str]]] = {
    "dealer_advisor": ("U-US-00002", "/dealer/journeys", ["/journeys/board"]),
    "dealer_bdc": ("U-US-00005", "/dealer/leads", ["/leads/queue"]),
    "dealer_fni": ("U-US-00006", "/dealer/fni", ["/fni/queue"]),
    "dealer_service": ("U-US-00007", "/dealer/service",
                       ["/service/journeys", "/service/attention", "/service/arrivals"]),
    "dealer_inventory": ("U-US-00008", "/dealer/inventory",
                         ["/inventory", "/trades/dispositions", "/inventory/acquisitions"]),
    "dealer_delivery": ("U-US-00009", "/dealer/delivery", ["/deliveries"]),
    "dealer_manager": ("U-US-00001", "/dealer/cockpit", ["/dealer/cockpit/funnel"]),
    "dealer_technician": ("U-US-00053", "/dealer/service/my-ros", ["/service/journeys?unassigned=1"]),
    "dealer_parts": ("U-US-00055", "/dealer/service/parts", ["/service/parts/desk"]),
    "dealer_service_manager": ("U-US-00056", "/dealer/service/cockpit", ["/service/cockpit"]),
    "oem_dm": ("U-US-00044", "/oem", ["/oem/today", "/oem/weekend?district=DIST-07"]),
    "oem_regional": ("U-US-00045", "/oem/region", ["/oem/region"]),
    "oem_ci_planning": ("U-US-00046", "/oem/accounts", ["/oem/accounts"]),
    "oem_sales_ops": ("U-US-00047", "/oem/comms", ["/oem/today"]),
    "oem_finance": ("U-US-00048", "/marketing/campaigns", ["/marketing/campaigns"]),
    "oem_marketing": ("U-US-00049", "/marketing/campaigns", ["/marketing/campaigns"]),
    "oem_cx": ("U-US-00050", "/marketing/journey-ops", ["/config/nurture", "/config/nurture/analytics"]),
    "admin_tenant": ("U-US-00051", "/admin", ["/admin/overview"]),
    "admin_dealer": ("U-US-00052", "/admin", ["/admin/overview"]),
    "customer": ("C-US-J1", "/garage", ["/garage/journey", "/garage/service", "/garage/ownership"]),
}


def test_home_reads_cover_every_role():
    assert set(HOME_READS) | {personas.PLATFORM_ADMIN} == set(personas.PERSONAS)
    for role, (_uid, path, _reads) in HOME_READS.items():
        assert personas.route(personas.home(role)) == path, role


@pytest.mark.parametrize("role", sorted(HOME_READS))
def test_each_persona_lands_on_a_home_its_api_serves(fake_ddb, role):
    from app.services.api import authorizer
    uid, path, reads = HOME_READS[role]
    token = f"cust:{uid}" if role == "customer" else f"user:{uid}"
    principal = authorizer.resolve_principal(f"Bearer {token}")
    assert principal["role"] == role

    def get(p: str) -> tuple[int, dict]:
        route, _, q = p.partition("?")
        query = dict(kv.split("=", 1) for kv in q.split("&")) if q else None
        return call({"httpMethod": "GET", "path": route, "queryStringParameters": query, "headers": {},
                     "body": None, "requestContext": {"authorizer": dict(principal)}})

    code, me = get("/me/home")
    assert code == 200 and me["url"] == path and personas.home(role) in me["screens"], me
    for p in reads:
        code, body = get(p)
        assert code == 200, (role, p, body)


def test_oem_sales_ops_home_and_reads(fake_ddb):
    code, me = call(api_event("GET", "/me/home", **SALES_OPS))
    assert code == 200 and me["url"] == "/oem/comms"
    assert not any(s.startswith("admin.") for s in me["screens"])
    # Sales Ops Comms and the Report Library both read GET /oem/today
    assert call(api_event("GET", "/oem/today", **SALES_OPS))[0] == 200
