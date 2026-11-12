"""A7 service spine + the service → sales repurchase handoff (§2.1, §2.1.1)."""
from __future__ import annotations

import pytest

from app.services.events import bus
from app.services.journeys import handoffs
from app.services.service import supervisor as svc


def _types() -> list[str]:
    return [e["type"].split("@")[0] for e in bus.EMITTED]


def _row(fake, table, key, value):
    return next(r for r in fake.rows(table) if r.get(key) == value)


def test_approve_gate_blocks_without_decision(fake_ddb):
    sj = _row(fake_ddb, "DEALER_ServiceJourneys", "service_journey_id", "SJ-US-00001")
    check = svc.validate_service_handoff(sj)
    est = [e for e in fake_ddb.rows("DEALER_ServiceEstimates")
           if e.get("service_journey_id") == "SJ-US-00001"]
    if est and est[0].get("status") in ("approved", "declined"):
        pytest.skip("seed estimate already decided")
    assert not check["ok"]


def test_record_approval_emits_line_decided_and_converts(fake_ddb):
    from app.services.platform import recommendations as recs
    rec = recs.record_shown("A7", "auto_present_estimate", shown_to="dealer_service",
                            service_journey_id="SJ-US-00001", dealer_id="D-101")
    recs.accept(rec["recommendation_id"], "U-US-00007")
    bus.EMITTED.clear()

    out = svc.record_approval("SJ-US-00001", "approve", actor="C-US-J1")
    assert out["estimate_status"] == "approved"
    assert "estimate.line_decided" in _types()
    row = _row(fake_ddb, "DEALER_Recommendations", "recommendation_id", rec["recommendation_id"])
    assert row["converted_at"] and row["converted"] == 1
    assert "recommendation.converted" in _types()


def test_handoff_from_owned_vehicle_creates_new_journey_and_supersedes(fake_ddb):
    bus.EMITTED.clear()
    sj3 = _row(fake_ddb, "DEALER_ServiceJourneys", "service_journey_id", "SJ-US-00003")   # J6's car in the lane
    advisor = sj3["advisor_id"]
    out = svc.handoff_to_sales("SJ-US-00003", reason="repair > 40% of value", actor=advisor)
    assert out["created_journey"] is True
    new = _row(fake_ddb, "DEALER_Journeys", "journey_id", out["new_journey_id"])
    assert new["stage"] == "DISCOVER" and new["source"] == "service_lane"
    assert new["previous_journey_id"] == "J-US-J6"
    own = _row(fake_ddb, "DEALER_Journeys", "journey_id", "J-US-J6")
    assert own["superseded_by"] == out["new_journey_id"]

    h = _row(fake_ddb, "DEALER_Handoffs", "handoff_id", out["handoff_id"])
    assert h["to_user"] == advisor and h["accepted_at"] is None
    task = _row(fake_ddb, "DEALER_Tasks", "task_id", out["task_id"])
    assert task["assigned_user_id"] == advisor
    assert task["resource_url"] == f"/r/journey/{out['new_journey_id']}"
    for t in ("journey.created", "handoff.created", "task.created", "service.handoff_to_sales"):
        assert t in _types()


def test_handoff_attaches_to_existing_active_journey(fake_ddb):
    # SJ-US-00001's customer already has an active QUALIFY journey (J-US-J1)
    out = svc.handoff_to_sales("SJ-US-00001", reason="lease end", actor="U-US-00002")
    assert out["created_journey"] is False and out["new_journey_id"] == "J-US-J1"
    active = [j for j in fake_ddb.rows("DEALER_Journeys")
              if j.get("customer_id") == "C-US-J1" and j["stage"] != "OWN"]
    assert len(active) == 1  # single-active rule holds


def test_accept_handoff_flips_owner_and_completes_task(fake_ddb):
    h = handoffs.create_handoff("J-US-J1", "U-US-00003", from_user="U-US-00005", note="BDC → advisor")
    with pytest.raises(handoffs.Forbidden):
        handoffs.accept_handoff(h["handoff_id"], "U-US-00004")
    out = handoffs.accept_handoff(h["handoff_id"], "U-US-00003")
    assert out["owner_user_id"] == "U-US-00003" and out["previous_owner"] == "U-US-00002"
    assert _row(fake_ddb, "DEALER_Journeys", "journey_id", "J-US-J1")["owner_user_id"] == "U-US-00003"
    assert _row(fake_ddb, "DEALER_Tasks", "task_id", h["task_id"])["status"] == "done"
    with pytest.raises(handoffs.InvalidState):
        handoffs.accept_handoff(h["handoff_id"], "U-US-00003")


def test_activity_feed_customer_subset_hides_internal(fake_ddb):
    handoffs.create_handoff("J-US-J1", "U-US-00003", from_user="U-US-00005", note="internal note")
    staff = handoffs.activity_feed("J-US-J1")
    cust = handoffs.activity_feed("J-US-J1", audience="customer")
    assert any(i["kind"] == "task" for i in staff["items"])
    assert all(i["kind"] in ("event", "handoff") for i in cust["items"])
    assert not any("summary" in i or "actor" in i for i in cust["items"])
