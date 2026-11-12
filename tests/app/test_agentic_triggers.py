"""The §4.10.2 trigger emitters: stall sweep, A1 re-score, equity monitor
crossing / lease window, service follow-up tick, and the job table."""
from __future__ import annotations

import datetime as dt

from app.services.agents import equity_monitor, rescore
from app.services.api import job_handler
from app.services.common import ddb
from app.services.events import bus
from app.services.journeys import stall_sweep, supervisor
from app.services.service import followup_tick


def _emitted(event_type: str) -> list[dict]:
    return [e for e in bus.EMITTED if e["type"] == f"{event_type}@v1"]


# --- stall sweep ---------------------------------------------------------------

def test_stall_sweep_flags_idle_active_journeys_once(fake_ddb):
    bus.EMITTED.clear()
    far_future = dt.datetime(2030, 1, 1, tzinfo=dt.timezone.utc)   # everything is idle
    dry = stall_sweep.run_stall_sweep("us", apply=False, now=far_future)
    assert dry["newly_stalled"] > 0 and dry["applied"] is False
    assert not _emitted("journey.stalled")                        # dry run writes nothing

    out = stall_sweep.run_stall_sweep("us", apply=True, now=far_future, limit=5)
    assert out["newly_stalled"] == 5
    evs = _emitted("journey.stalled")
    assert len(evs) == 5 and all(e["idle_days"] >= out["sla_days"] for e in evs)
    jid = evs[0]["journey_id"]
    j = [r for r in fake_ddb.rows("DEALER_Journeys") if r["journey_id"] == jid][0]
    assert j["stalled"] is True and j["stall_notified_at"]
    assert j["stage"] in stall_sweep._ACTIVE
    # the Callback Brief play subscriber ran for it (local dispatch)
    assert any(r.get("journey_id") == jid for r in fake_ddb.rows("DEALER_PlayRuns"))

    bus.EMITTED.clear()
    again = stall_sweep.run_stall_sweep("us", apply=True, now=far_future, limit=5)
    flagged_again = {e["journey_id"] for e in _emitted("journey.stalled")}
    assert jid not in flagged_again                                # idempotent per stall
    assert again["newly_stalled"] <= 5


def test_advance_clears_stall_so_it_can_fire_again(fake_ddb):
    far_future = dt.datetime(2030, 1, 1, tzinfo=dt.timezone.utc)
    stall_sweep.run_stall_sweep("us", apply=True, now=far_future)
    j = [r for r in fake_ddb.rows("DEALER_Journeys") if r["journey_id"] == "J-US-J2"][0]
    assert j.get("stall_notified_at")
    supervisor.advance_journey("J-US-J2", evidence="build saved", actor="u")
    j = [r for r in fake_ddb.rows("DEALER_Journeys") if r["journey_id"] == "J-US-J2"][0]
    assert j["stalled"] is False and "stall_notified_at" not in j


# --- A1 re-score ---------------------------------------------------------------

def test_rescore_announces_in_stock_match_once(fake_ddb):
    bus.EMITTED.clear()
    out = rescore.run_rescore_profiles("us", apply=True, limit=40)
    assert out["scanned"] > 0
    evs = _emitted("discovery.match_found")
    assert len(evs) == out["announced"]
    if evs:
        ev = evs[0]
        assert ev["in_stock_count"] > 0 and ev["example_vin"] and ev["trim_id"]
        j = [r for r in fake_ddb.rows("DEALER_Journeys") if r["journey_id"] == ev["journey_id"]][0]
        assert j["context"]["discovery_match_trim"] == ev["trim_id"]
        # NOTE: the seed packs carry no "Recommend Vehicle" play yet, so the
        # subscriber runs but finds no play; the event + journey context are the contract.
    bus.EMITTED.clear()
    out2 = rescore.run_rescore_profiles("us", apply=True, limit=40)
    assert out2["announced"] == 0 and not _emitted("discovery.match_found")


# --- equity monitor events -------------------------------------------------------

def test_equity_monitor_emits_crossing_only_on_first_sight(fake_ddb):
    bus.EMITTED.clear()
    out = equity_monitor.run_equity_monitor("us", apply=True, limit=60)
    assert out["recommendations_written"] == out["positive_equity_flagged"]
    crossed = _emitted("equity.crossed_positive")
    assert len(crossed) == out["equity_crossed_events"]
    lease = _emitted("lease.end_window_entered")
    assert len(lease) == out["lease_window_entered"]
    for e in crossed:
        assert e["recommendation_id"] == f"REC-MON-{e['journey_id']}"
    bus.EMITTED.clear()
    out2 = equity_monitor.run_equity_monitor("us", apply=True, limit=60)
    assert out2["equity_crossed_events"] == 0 and out2["lease_window_entered"] == 0
    assert out2["recommendations_written"] == out["recommendations_written"]   # upsert, no pile-up


# --- service follow-up tick -------------------------------------------------------

def test_service_followup_tick_writes_due_service_cards_idempotently(fake_ddb, monkeypatch):
    monkeypatch.setenv("RETAIL_OPS_DUE_SERVICE_DAYS", "100000")       # everything is "due"
    out = followup_tick.run_service_followup_tick("us", apply=True, limit=30)
    assert out["autonomy_level"] == "recommend"
    assert out["due_service_cards_written"] == out["due_service_candidates"] > 0
    cards = [r for r in fake_ddb.rows("DEALER_Recommendations") if r.get("action") == "service_due"]
    assert cards and all(c["recommendation_id"] == f"REC-DUE-{c['vin']}" for c in cards)
    again = followup_tick.run_service_followup_tick("us", apply=True, limit=30)
    assert again["due_service_cards_written"] == 0


def test_service_followup_tick_handles_declined_plans(fake_ddb):
    from app.services.service import followup
    sj = next(r for r in fake_ddb.rows("DEALER_ServiceJourneys") if r.get("market_id") == "us"
              and r.get("stage") in ("FOLLOW_UP", "COMPLETED"))
    followup.schedule_declined_followup(sj["service_journey_id"], [{"item": "Rear brake pads", "amount": 420}],
                                        cadence_days=30)
    plan = next(p for p in fake_ddb.rows("DEALER_NurturePlans")
                if p["nurture_plan_id"] == f"NP-DECLINED-{sj['service_journey_id']}")
    plan = dict(plan); plan["next_touch_at"] = "2020-01-01T00:00:00Z"   # make it due now
    ddb.put_item("DEALER_NurturePlans", plan)
    out = followup_tick.run_service_followup_tick("us", apply=True)
    assert out["declined_due"] == 1
    assert out["recommended"] + out["suppressed"] + out["stopped"] == 1
    if out["recommended"]:
        card = next(r for r in fake_ddb.rows("DEALER_Recommendations")
                    if r["recommendation_id"] == f"REC-DECLINED-{sj['service_journey_id']}")
        assert card["action"] == "declined_reminder" and card["agent_id"] == "A7"
        plan2 = next(p for p in fake_ddb.rows("DEALER_NurturePlans")
                     if p["nurture_plan_id"] == f"NP-DECLINED-{sj['service_journey_id']}")
        assert plan2["next_touch_at"] > "2020-01-01T00:00:00Z"


# --- job table -------------------------------------------------------------------

def test_job_table_covers_every_scheduled_job():
    from app.infra import deploy_l6
    assert set(j[0] for j in deploy_l6.JOBS) <= set(job_handler.JOBS)
    assert job_handler.lambda_handler({"job": "nope"})["ok"] is False


def test_jobs_dry_run_through_run_job(fake_ddb):
    for job in ("stall-sweep", "rescore-profiles", "service-followup-tick",
                "nightly-attribution", "district-cycle"):
        out = job_handler.run_job_dict(job, {"market": "us", "limit": 3}, apply=False)
        assert out.get("applied") is False or out.get("apply") is False or "market" in out
