"""Supervisor promotion: offline the deterministic paths choose
(and say so via chosen_by / decision_engine), the model's ordering fence rejects
anything that is not a pure permutation, and the recommendations row records
which engine chose."""
from __future__ import annotations

from app.services.common import ddb
from app.services.journeys import actions, supervisor
from app.services.service import morning_work, shop_load


def _journey(jid: str):
    return ddb.get_item("DEALER_Journeys", {"journey_id": jid})


# --- A2 select_next_best_action -------------------------------------------------

def test_nba_offline_is_deterministic_and_keeps_shape(fake_ddb):
    out = supervisor.select_next_best_action(_journey("J-US-J2"))
    assert out["chosen_by"] == "deterministic" and out["rationale"] is None
    assert out["chosen"]["action"] and out["context_hash"]      # shape unchanged
    assert isinstance(out["alternatives"], list)


def test_log_nba_records_which_engine_chose(fake_ddb):
    j = _journey("J-US-J1")
    res = actions.log_nba(j)
    row = ddb.get_item("DEALER_Recommendations", {"recommendation_id": res["recommendation_id"]})
    assert row["chosen_by"] == "deterministic"
    assert row["rationale"] is None
    assert row["action"] == res["chosen"]["action"]


def test_nba_model_pick_returns_none_offline(fake_ddb):
    j = _journey("J-US-J1")
    cands = [{"action": "a", "reason": "r"}, {"action": "b", "reason": "r"}]
    assert supervisor._model_pick(cands, j, None) is None       # local mode: no model


# --- A7 morning prioritize --------------------------------------------------------

def test_shop_load_reports_deterministic_engine_offline(fake_ddb):
    out = shop_load.run_shop_load("us", apply=False)
    assert out["decision_engine"] == "deterministic"
    for card in out["sample"]:
        assert "service_journey_id" in card and "actions" in card
        assert "agent_rank" not in card                         # no fabricated ranks offline


def test_prioritize_cards_passthrough_offline_and_single_card(fake_ddb):
    cards = [{"service_journey_id": "SJ-1", "dealer_id": "D-1", "actions": ["dispatch_ro"]},
             {"service_journey_id": "SJ-2", "dealer_id": "D-1", "actions": ["stage_parts"]}]
    ordered, engine = morning_work.prioritize_cards(list(cards))
    assert ordered == cards and engine == "deterministic"       # local mode: untouched
    one, engine = morning_work.prioritize_cards(cards[:1])
    assert one == cards[:1] and engine == "deterministic"       # nothing to order
