"""Demo readiness (app/demo/readiness.py) and the demo pack (scripts/seed/generate/demo_pack.py):
a freshly loaded stack can rehearse every scenario about ten times, and the count drops as
rehearsals use their subjects up."""
from __future__ import annotations

from app.demo import readiness
from app.services.common import ddb


def _left() -> dict[str, int]:
    return {s.scenario_id: s.runs_left for s in readiness.report()}


def test_a_fresh_stack_can_rehearse_every_scenario_about_ten_times(fake_ddb):
    left = _left()
    assert all(n >= 1 for n in left.values()), left
    for sid in ("ev-purchase", "service-visit", "equity-upgrade", "inventory-morning", "dm-monday", "predictive-offer"):
        assert left[sid] >= 8, (sid, left[sid])


def test_the_pack_owners_are_lane_candidates_who_go_to_the_demo_advisor(fake_ddb):
    from app.services.service import supervisor
    lane = readiness._lane_candidates(None)
    pack = [i for i in lane if "-DP" in i["service_journey_id"]]
    assert len(pack) == 10
    out = supervisor.handoff_to_sales(pack[0]["service_journey_id"], "positive_equity", actor="U-US-00007")
    assert ddb.get_item("DEALER_Handoffs", {"handoff_id": out["handoff_id"]})["to_user"] == "U-US-00002"


def test_a_rehearsal_uses_up_one_candidate(fake_ddb):
    from app.services.service import supervisor
    before = _left()["equity-upgrade"]
    sj = next(i for i in readiness._lane_candidates(None))
    supervisor.handoff_to_sales(sj["service_journey_id"], "positive_equity", actor="U-US-00007")
    assert _left()["equity-upgrade"] == before - 1


def test_a_booked_service_owner_is_no_longer_free(fake_ddb):
    from app.services.service import booking
    before = _left()["service-visit"]
    vin = next(v["vin"] for v in ddb.scan_all("DEALER_Vehicles") if v.get("customer_id") == readiness.SERVICE_OWNERS[0])
    booking.book_service(vin, services=["Oil & filter"], preferred_slot="2026-11-20T15:00:00Z")
    assert _left()["service-visit"] == before - 1


def test_rebooking_a_car_opens_a_new_visit_never_overwrites_the_last(fake_ddb):
    from app.services.service import booking
    vin = next(v["vin"] for v in ddb.scan_all("DEALER_Vehicles") if v.get("customer_id") and v.get("dealer_id") == "D-101")
    first = booking.book_service(vin, services=["Oil & filter"], preferred_slot="2026-11-20T15:00:00Z")
    sj = ddb.get_item("DEALER_ServiceJourneys", {"service_journey_id": first["service_journey_id"]})
    ddb.put_item("DEALER_ServiceJourneys", {**sj, "stage": "FOLLOW_UP"})          # that visit is done
    second = booking.book_service(vin, services=["Tire rotation"], preferred_slot="2026-12-20T15:00:00Z")
    assert second["service_journey_id"] != first["service_journey_id"]
    assert ddb.get_item("DEALER_ServiceJourneys", {"service_journey_id": first["service_journey_id"]})["stage"] == "FOLLOW_UP"


def test_a_car_in_the_bay_gets_no_book_ahead_heads_up(fake_ddb):
    from app.services.service import predictive
    sj = next(s for s in ddb.scan_all("DEALER_ServiceJourneys") if s.get("stage") == "APPROVE" and s.get("vin"))
    rows = predictive.predictions(None, sj.get("market_id") or "us", vin=sj["vin"])["predictions"]
    assert all(p["in_bay"] for p in rows)
    out = predictive.run_scan(None, sj.get("market_id") or "us", "A7", vin=sj["vin"])
    assert out["leads_raised"] == 0           # its due work goes on the visit it is in, not a second one
