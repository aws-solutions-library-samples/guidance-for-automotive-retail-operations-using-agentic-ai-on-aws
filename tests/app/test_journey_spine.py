"""A2 purchase-journey spine: validate_handoff gate, guarded advance, events."""
from __future__ import annotations

import pytest

from app.services.events import bus
from app.services.journeys import supervisor


def _emitted(event_type: str) -> list[dict]:
    return [e for e in bus.EMITTED if e["type"] == f"{event_type}@v1"]


def test_advance_writes_event_then_flips_and_emits(fake_ddb):
    bus.EMITTED.clear()
    out = supervisor.advance_journey("J-US-J2", evidence="build saved", actor="U-US-00011")
    assert (out["from"], out["to"]) == ("CONFIGURE", "QUALIFY")

    j = [r for r in fake_ddb.rows("DEALER_Journeys") if r["journey_id"] == "J-US-J2"][0]
    assert j["stage"] == "QUALIFY" and j["previous_stage"] == "CONFIGURE"
    events = [e for e in fake_ddb.rows("DEALER_JourneyEvents")
              if e["journey_id"] == "J-US-J2" and e["event_type"] == "stage.advanced"]
    assert events and events[-1]["actor"]["user_id"] == "U-US-00011"
    assert events[-1]["evidence"] == "build saved"

    [ev] = _emitted("journey.stage_changed")
    assert ev["journey_id"] == "J-US-J2" and ev["to"] == "QUALIFY"
    assert ev["scope"] == {"dealer_id": "D-102"}
    assert ev["resource_url"] == "/dealer/journeys/J-US-J2"


def test_gate_blocks_missing_context_and_writes_nothing(fake_ddb):
    before = len(fake_ddb.rows("DEALER_JourneyEvents"))
    with pytest.raises(supervisor.HandoffIncomplete) as exc:
        supervisor.advance_journey("J-US-J9", evidence="x", actor="u",
                                   context_updates={"shortlist": False})
    assert exc.value.missing == ["shortlist"]
    assert len(fake_ddb.rows("DEALER_JourneyEvents")) == before


def test_context_update_clears_gate(fake_ddb):
    out = supervisor.advance_journey("J-US-J3", evidence="trade captured", actor="u",
                                     context_updates={"trade_condition": True})
    assert out["to"] == "TRADE_IN"


def test_agree_requires_credit_compliance(fake_ddb):
    check = supervisor.validate_handoff(
        {"stage": "AGREE", "journey_id": "J-X", "context": {"deal_structure": True}})
    assert not check["ok"]
    assert "credit_compliance" in check["missing"]


def test_own_is_terminal(fake_ddb):
    with pytest.raises(supervisor.InvalidTransition):
        supervisor.advance_journey("J-US-J6", evidence="x", actor="u")


def test_no_stage_skipping(fake_ddb):
    with pytest.raises(supervisor.InvalidTransition):
        supervisor.advance_journey("J-US-J2", evidence="x", actor="u", to_stage="FINANCE")


def test_nba_learns_from_outcomes(fake_ddb):
    j = {"journey_id": "J-X", "stage": "QUALIFY", "stalled": False, "idle_days": 1,
         "context": {"budget": True, "timeline": True}}
    base = supervisor.select_next_best_action(j)
    assert base["chosen"]["action"] == "book_test_drive"
    # stalled: policy says nudge; outcomes for this context favour the test drive
    j_stalled = {**j, "stalled": True}
    policy = supervisor.select_next_best_action(j_stalled)
    assert policy["chosen"]["action"] == "nudge"
    learned = supervisor.select_next_best_action(j_stalled, outcomes={"book_test_drive": 0.4, "nudge": 0.1})
    assert learned["chosen"]["action"] == "book_test_drive"
    assert learned["learned_from_outcomes"]["overrode"] == "nudge"


def test_nba_learning_never_overrides_priority(fake_ddb):
    j = {"journey_id": "J-X", "stage": "QUALIFY", "stalled": True, "idle_days": 30, "context": {}}
    out = supervisor.select_next_best_action(j, outcomes={"book_test_drive": 0.9})
    assert out["chosen"]["action"] == "handoff_to_human"
