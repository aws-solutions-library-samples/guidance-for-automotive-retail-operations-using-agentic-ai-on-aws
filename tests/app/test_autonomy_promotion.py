"""The autonomy ladder as a feedback loop: promotion fires at the
documented thresholds through the same guarded publish path a human uses,
hard-rule ceilings are never crossed, collapse in acceptance demotes, and a run
with nothing qualifying writes nothing (idempotent)."""
from __future__ import annotations

from app.services.admin import autonomy as admin_autonomy
from app.services.common import ddb
from app.services.events import bus
from app.services.platform import autonomy_promotion as loop


def _seed_outcomes(action: str, agent: str, shown: int, accepted: int, rec_id: str,
                   journey_id: str | None = "J-US-J1", account_id: str | None = None) -> None:
    ddb.put_item("DEALER_Recommendations", {
        "recommendation_id": rec_id, "agent_id": agent, "action": action,
        "journey_id": journey_id, "account_id": account_id,
        "shown": shown, "accepted": accepted, "converted": 0,
        "shown_at": "2026-10-01T00:00:00Z", "source_ref": "test:outcome_seed"})


def _level(action_id: str) -> str:
    row = next(a for a in admin_autonomy.list_autonomy("us")["actions"]
               if a["action_id"] == action_id)
    return row["level"]


def test_promotion_fires_at_thresholds_with_stats_note(fake_ddb):
    bus.EMITTED.clear()
    assert _level("a2.schedule_outreach") == "recommend"
    _seed_outcomes("nurture_touch", "A2", shown=30, accepted=20, rec_id="REC-T-PROMOTE")
    out = loop.run(market="us")
    promoted = {p["action_id"]: p for p in out["promoted"]}
    assert "a2.schedule_outreach" in promoted
    assert promoted["a2.schedule_outreach"]["to_level"] == "act_and_notify"
    assert _level("a2.schedule_outreach") == "act_and_notify"
    # admin visibility: the published version's note carries the stats
    head = admin_autonomy.autonomy_history("a2.schedule_outreach")["versions"][0]
    assert head["is_active"] and "promoted by outcome loop" in head["source_ref"]
    assert "shown" in head["source_ref"] and "% accepted" in head["source_ref"]
    # bookkeeping: catalogued event + admin task
    assert any(e["type"] == "autonomy.promoted@v1" for e in bus.EMITTED)
    assert any(t.get("source_agent") == "autonomy_promotion"
               for t in fake_ddb.rows("DEALER_Tasks"))


def test_promotion_needs_both_volume_and_acceptance(fake_ddb):
    _seed_outcomes("nurture_touch", "A2", shown=10, accepted=10, rec_id="REC-T-THIN")
    out = loop.run(market="us")                      # 10 < MIN_SHOWN: no evidence
    assert all(p["action_id"] != "a2.schedule_outreach" for p in out["promoted"])
    _seed_outcomes("nurture_touch", "A2", shown=30, accepted=10, rec_id="REC-T-LOWACC")
    out = loop.run(market="us")                      # 40 shown but 50% < 0.6
    assert all(p["action_id"] != "a2.schedule_outreach" for p in out["promoted"])
    assert _level("a2.schedule_outreach") == "recommend"


def test_promotion_respects_hard_rule_ceilings(fake_ddb):
    # a5.shift_budget is capped at act_with_approval (§4.10.1): stellar stats
    # must be skipped with the reason recorded, never published past the cap.
    _seed_outcomes("shift_budget", "A5", shown=40, accepted=35, rec_id="REC-T-CEIL",
                   journey_id=None, account_id="ACC-US-00001")
    out = loop.run(market="us")
    skipped = {s["action_id"]: s for s in out["skipped"]}
    assert "a5.shift_budget" in skipped
    assert "ceiling" in skipped["a5.shift_budget"]["reason"]
    assert _level("a5.shift_budget") == "recommend"  # untouched


def test_demotion_guard_and_idempotency(fake_ddb):
    bus.EMITTED.clear()
    admin_autonomy.set_autonomy("a2.schedule_outreach", "act_and_notify", "test-setup")
    _seed_outcomes("nurture_touch", "A2", shown=30, accepted=2, rec_id="REC-T-DEMOTE")
    out = loop.run(market="us")
    demoted = {d["action_id"]: d for d in out["demoted"]}
    assert demoted["a2.schedule_outreach"]["to_level"] == "recommend"
    assert _level("a2.schedule_outreach") == "recommend"
    assert any(e["type"] == "autonomy.demoted@v1" for e in bus.EMITTED)
    head = admin_autonomy.autonomy_history("a2.schedule_outreach")["versions"][0]
    assert "demoted by outcome loop" in head["source_ref"]
    # idempotent: 7% acceptance at 'recommend' qualifies for nothing — the next
    # run publishes no version and opens no task.
    versions_before = len(admin_autonomy.autonomy_history("a2.schedule_outreach")["versions"])
    tasks_before = len(fake_ddb.rows("DEALER_Tasks"))
    again = loop.run(market="us")
    assert not again["promoted"] and not again["demoted"]
    assert len(admin_autonomy.autonomy_history("a2.schedule_outreach")["versions"]) == versions_before
    assert len(fake_ddb.rows("DEALER_Tasks")) == tasks_before


def test_dry_run_evaluates_without_publishing(fake_ddb):
    _seed_outcomes("nurture_touch", "A2", shown=30, accepted=25, rec_id="REC-T-DRY")
    out = loop.run(market="us", dry=True)
    assert any(p["action_id"] == "a2.schedule_outreach" and p.get("dry") for p in out["promoted"])
    assert _level("a2.schedule_outreach") == "recommend"  # nothing published
