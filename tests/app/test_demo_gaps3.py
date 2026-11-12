"""Third round of demo gaps (2026-11-05); each test pins one reported symptom."""
from __future__ import annotations

import json
from pathlib import Path

from app.services.common import ddb
from app.services.service import booking, desk, estimates

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "sample-data" / "synthetic" / "us" / "tables"
SVC = {"user_id": "U-US-00007", "role": "dealer_service", "market_id": "us", "dealer_ids": {"D-101"}, "is_oem": False}


def _rows(table: str) -> list[dict]:
    return json.loads((SEED / f"{table}.json").read_text())


def test_a_scheduled_declined_follow_up_sticks(fake_ddb):
    d = next(x for x in desk.declined(SVC)["items"] if x["status"] == "open")
    assert d["followup_at"] is None                      # nothing scheduled yet
    out = desk.update_declined(SVC, d["declined_id"], status=None, cadence_days=60)
    after = next(x for x in desk.declined(SVC)["items"] if x["declined_id"] == d["declined_id"])
    assert after["cadence_days"] == 60 and after["followup_at"] and after["followup_at"] == out["followup_at"]
    assert after["followup_at"] > after["followup_scheduled_at"]   # runs from when it was scheduled


def test_an_ev_is_never_due_an_oil_change(fake_ddb):
    models = {m["model_id"]: m for m in ddb.scan_all("OEM_CatalogModels")}
    ev = next(v for v in ddb.scan_all("DEALER_Vehicles")
              if (models.get(v.get("model_id")) or {}).get("powertrains") == ["EV"])
    due = booking.predict_next_service(ev["vin"])["recommended_services"]
    assert "Oil & filter" not in due and "HV battery health check" in due


def test_maintenance_items_are_customer_pay_even_on_a_new_car():
    new = {"delivered_at": "2026-10-20T09:00:00Z", "odometer": 412, "market_id": "us"}
    for item in ("Cabin filter", "Wipers", "Engine oil"):
        assert estimates.line_coverage(new, item)["pay_type"] == "customer_pay", item
    assert estimates.line_coverage(new, "Rear brakes")["pay_type"] == "warranty"      # still a defect


def test_a_labor_op_lists_only_its_own_parts(fake_ddb):
    from app.services.service import connectors
    align = connectors.lookup_parts(labor_op_id="LOP-ALIGN")
    assert align["parts"] == []                          # alignment needs no parts
    brakes = connectors.lookup_parts(labor_op_id="LOP-BRAKE-R")
    assert brakes["parts"] and all("brake" in p["description"].lower() for p in brakes["parts"])


def test_the_service_lane_shows_one_card_per_car(fake_ddb):
    from app.services.api import handler
    from tests.app.conftest import api_event
    vin = next(s["vin"] for s in ddb.scan_all("DEALER_ServiceJourneys")
               if s.get("dealer_id") == "D-101" and s.get("stage") == "APPROVE" and s.get("vin"))
    booking.book_service(vin, wait_type="wait", preferred_slot="2026-11-10T14:00:00Z")   # a stale extra BOOK
    ev = api_event("GET", "/service/lane", user="U-US-00002", role="dealer_advisor", dealer_ids="D-101")
    items = json.loads(handler.lambda_handler(ev, None)["body"])["items"]
    cards = [i for i in items if i["vin"] == vin]
    assert len(cards) == 1 and cards[0]["stage"] == "APPROVE"


def test_a_warranty_finding_is_not_an_upsell(fake_ddb):
    from app.services.agents import assistant
    sj = next(s for s in ddb.scan_all("DEALER_ServiceJourneys") if s.get("vin"))
    car = ddb.get_item("DEALER_Vehicles", {"vin": sj["vin"]}) or {}
    ddb.put_item("DEALER_Vehicles", {**car, "vin": sj["vin"], "delivered_at": "2026-10-20T09:00:00Z", "odometer": 412})
    ddb.put_item("DEALER_Inspections", {"inspection_id": "INS-TEST-CP", "service_journey_id": sj["service_journey_id"],
                                        "item": "Charge port", "status": "red", "urgency": "safety"})
    items = assistant.service_upsell_brief(sj)["items"]
    assert "Charge port" not in [i["item"] for i in items]


def test_the_seed_has_a_part_to_order_on_the_demo_visit():
    ro = next(r["ro_id"] for r in _rows("DEALER_RepairOrders") if r["service_journey_id"] == "SJ-US-00003")
    needed = [p for p in _rows("DEALER_Parts") if p["ro_id"] == ro and p.get("line_id")]
    assert needed and any(not p["on_hand"] for p in needed)
    assert not [o for o in _rows("DEALER_PartsOrders") if o["ro_id"] == ro]      # nothing ordered yet


def test_the_deploy_loads_data_rebased_to_today():
    assert "--rebase-now" in (ROOT / "deploy" / "deploy.sh").read_text()


def test_the_authorizer_accepts_a_customer_link_with_only_its_own_table_grants(fake_ddb, monkeypatch):
    """The deployed authorizer role reads only iam_roles.AUTHORIZER_READ_TABLES; any other
    read is AccessDenied there and turned into "Unauthorized". Enforce the same here."""
    from app.infra import iam_roles
    from app.services.api import authorizer
    from app.services.customer import magic_link
    link = magic_link.issue_magic_link("C-US-J1", "garage")        # issued by the service Lambda
    allowed = set(iam_roles.AUTHORIZER_READ_TABLES)
    real_get, real_scan = ddb.get_item, ddb.scan_filter

    def guard(name):
        if name not in allowed:
            raise PermissionError(f"AccessDenied: authorizer role may not read {name}")

    monkeypatch.setattr(ddb, "get_item", lambda t, *a, **k: (guard(t), real_get(t, *a, **k))[1])
    monkeypatch.setattr(ddb, "scan_filter", lambda t, *a, **k: (guard(t), real_scan(t, *a, **k))[1])
    p = authorizer.resolve_principal(f"Bearer mlk:{link['token']}")
    assert p["customer_id"] == "C-US-J1" and p["role"] == "customer" and p["market_id"] == "us"
    ev = {"type": "TOKEN", "authorizationToken": f"Bearer mlk:{link['token']}",
          "methodArn": "arn:aws:execute-api:us-east-1:1:abc/dev/POST/auth/magic-link/redeem"}
    assert authorizer.lambda_handler(ev, None)["policyDocument"]["Statement"][0]["Effect"] == "Allow"
