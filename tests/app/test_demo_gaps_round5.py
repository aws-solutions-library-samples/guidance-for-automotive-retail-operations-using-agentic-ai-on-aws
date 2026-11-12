"""Demo gaps, round 5: a booking without a time lands in store hours, the predictive scan
raises a mix of parts, the service board shows store-local times and only today's
arrivals, and the seed's approvals are complete (no stub budget shift left pending)."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from app.services.common import ddb
from app.services.service import booking, desk, predictive

ROOT = Path(__file__).resolve().parents[2]
CLAIMS = {"dealer_ids": {"D-101"}, "is_oem": False, "market_id": "us", "role": "dealer_service"}


def _local(ts: str, dealer: str = "D-101") -> dt.datetime:
    tz = desk._zone(desk._dealer_timezone(dealer, None))
    return dt.datetime.fromisoformat(ts).astimezone(tz)


def _free_vin() -> str:
    """A D-101 car with no open visit, so a booking opens a fresh one."""
    open_vins = {s.get("vin") for s in ddb.scan_all("DEALER_ServiceJourneys") if s.get("stage") != "COMPLETED"}
    return next(v["vin"] for v in ddb.scan_filter("DEALER_Vehicles", "dealer_id", "D-101")
                if v.get("customer_id") and v["vin"] not in open_vins)


# --- booking time -----------------------------------------------------------------------

def test_store_slots_are_weekdays_in_store_hours_from_tomorrow(fake_ddb):
    now = dt.datetime(2026, 11, 6, 21, 0, tzinfo=dt.UTC)        # a Friday afternoon in Atlanta
    s = booking.store_slots("D-101", days=3, now=now)
    assert s["slots"] and s["timezone"] != "UTC"
    for slot in s["slots"]:
        loc = _local(slot["slot_start"])
        assert loc.weekday() < 5 and booking.STORE_OPEN_HOUR <= loc.hour <= booking.STORE_CLOSE_HOUR
        assert loc.date() > now.astimezone(loc.tzinfo).date()
    assert _local(booking.next_open_slot("D-101", now=now)).weekday() == 0      # Monday, not Sunday


def test_a_booking_without_a_time_takes_the_next_open_store_slot(fake_ddb):
    out = booking.book_service(_free_vin(), services=["Front brake pads"], wait_type="drop", actor="lead_desk")
    loc = _local(out["slot_start"])
    assert loc.weekday() < 5 and booking.STORE_OPEN_HOUR <= loc.hour <= booking.STORE_CLOSE_HOUR


def test_a_chosen_slot_is_kept(fake_ddb):
    chosen = booking.store_slots("D-101", days=2)["slots"][-1]["slot_start"]
    out = booking.book_service(_free_vin(), services=["x"], wait_type="drop", preferred_slot=chosen)
    assert out["slot_start"] == chosen


# --- predictive mix -----------------------------------------------------------------------

def test_varied_by_part_interleaves_and_keeps_urgency_within_a_part():
    preds = [{"part": "F", "n": 1}, {"part": "F", "n": 2}, {"part": "F", "n": 3}, {"part": "T", "n": 4},
             {"part": "B", "n": 5}, {"part": "T", "n": 6}]
    assert [p["n"] for p in predictive.varied_by_part(preds)] == [1, 4, 5, 2, 6, 3]
    assert predictive.varied_by_part([]) == []


def test_the_scan_raises_a_mix_of_parts_not_one_cohort(fake_ddb):
    out = predictive.run_scan({"D-101"}, "us", "test")
    parts = [lead["part"] for lead in out["leads"]]
    assert len(parts) >= 3 and len(set(parts)) >= 3, parts


# --- service board ------------------------------------------------------------------------

def test_needs_attention_reads_in_store_time_and_carries_the_raw_times(fake_ddb):
    items = [i for i in desk.attention(CLAIMS)["items"] if i["kind"] == "past_promise"]
    assert items
    for i in items:
        assert "Z" not in i["detail"] and "T" not in i["detail"].split("promised ", 1)[1][:12]
        assert i["promised_at"].endswith("Z")


def test_arrivals_hold_only_today_and_missed_appointments(fake_ddb):
    out = booking.book_service(_free_vin(), services=["x"], wait_type="drop")   # next open slot: a later day
    ids = {a["service_journey_id"] for a in desk.arrivals(CLAIMS)}
    assert out["service_journey_id"] not in ids
    tz = desk._zone(desk._dealer_timezone("D-101", None))
    today = dt.datetime.now(dt.UTC).astimezone(tz).date()
    assert all(_local(a["slot_start"]).date() <= today for a in desk.arrivals(CLAIMS) if a.get("slot_start"))


# --- seeded approvals ----------------------------------------------------------------------

def test_seeded_approvals_are_complete_and_no_budget_shift_is_left_pending():
    for m in ("us", "de"):
        rows = json.loads((ROOT / "sample-data" / "synthetic" / m / "tables" / "DEALER_Approvals.json").read_text())
        assert rows
        for a in rows:
            assert a["type"] and a["approver_role"] and a["scope"] and a["payload"] and a["reason"], a
            assert a["market_id"] == m
            if a["type"] == "budget_shift":
                assert a["status"] == "approved" and a["decided_by"] and a["decided_at"] > a["requested_at"]
            if a["type"] == "stage_parts":
                assert a["scope"]["dealer_id"] and a["dealer_id"] == a["scope"]["dealer_id"]
