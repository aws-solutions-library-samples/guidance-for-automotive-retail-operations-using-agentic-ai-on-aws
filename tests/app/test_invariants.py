"""The invariant sweep (app/demo/invariants.py): clean on the seed, and each rule catches the
break it exists for — a sweep whose rules cannot fail would prove nothing."""
from __future__ import annotations

import pytest

from app.demo import invariants
from app.services.common import ddb


def _failed(only: str) -> invariants.Result:
    (r,) = invariants.check(only={only})
    return r


def test_the_seed_breaks_no_rule(fake_ddb_today):
    bad = [(r.rule_id, r.error or r.sample) for r in invariants.check() if not r.ok]
    assert not bad, bad


def test_every_rule_has_a_gap_and_a_unique_id():
    ids = [r.rule_id for r in invariants.RULES]
    assert len(ids) == len(set(ids)) and all(r.gap and r.text for r in invariants.RULES)


def _first(table, pred=lambda r: True):
    return next(r for r in ddb.scan_all(table) if pred(r))


def _plant_sold_unit_with_hold():
    u = _first("DEALER_Inventory", lambda r: r.get("market_id") == "us")
    ddb.put_item("DEALER_Inventory", {**u, "status": "sold", "reserved_for_journey_id": "J-X"})


def _plant_service_lead_with_journey():
    j = _first("DEALER_Journeys", lambda r: r.get("market_id") == "us")
    ddb.put_item("DEALER_Leads", {"lead_id": "LD-PRED-T", "market_id": "us", "source": "predictive_maintenance",
                                  "journey_id": j["journey_id"], "status": "working"})


def _plant_second_active_journey():
    j = _first("DEALER_Journeys", lambda r: r.get("market_id") == "us" and r.get("stage") == "DISCOVER")
    ddb.put_item("DEALER_Journeys", {**j, "journey_id": "J-US-DUP", "stage": "QUALIFY", "status": "active"})


def _plant_duplicate_alert():
    a = _first("OEM_Alerts", lambda r: r.get("status") == "open")
    ddb.put_item("OEM_Alerts", {**a, "alert_id": "AL-DUP"})


def _plant_orphan_handoff():
    h = _first("DEALER_Handoffs")
    ddb.put_item("DEALER_Handoffs", {**h, "handoff_id": "HO-ORPHAN", "journey_id": "J-GONE"})


def _plant_channel_cap_over_weekly():
    c = _first("PLAT_ConfigNurture", lambda r: r.get("is_active"))
    caps = dict(c["cadence_caps"])
    caps["per_channel_week"] = {**caps["per_channel_week"], "email": 7}
    ddb.put_item("PLAT_ConfigNurture", {**c, "cadence_caps": caps})


def _plant_daily_nurture_plan():
    p = _first("DEALER_NurturePlans")
    ddb.put_item("DEALER_NurturePlans", {**p, "cadence": "daily"})


def _plant_universal_consent():
    for c in ddb.scan_all("CUST_Consent"):
        if c.get("market_id") == "us":
            ddb.put_item("CUST_Consent", {**c, "email_opt_in": True})


def _plant_stale_draft():
    p = _first("OEM_AccountPlans", lambda r: r.get("dealer_id") and r.get("version"))
    ddb.put_item("OEM_AccountPlans", {**p, "plan_id": f"BRIEF-US-DRAFT-{p['dealer_id']}", "status": "draft", "version": 1})


def _plant_handoff_to_an_agent():
    j = _first("DEALER_Journeys", lambda r: r.get("market_id") == "us" and r.get("stage") == "DISCOVER")
    ddb.put_item("DEALER_Handoffs", {"handoff_id": "HO-AGENT", "journey_id": j["journey_id"], "to_user": "A2"})
    ddb.put_item("DEALER_Journeys", {**j, "pending_handoff": {"handoff_id": "HO-AGENT", "to_user": "A2"}})


def _plant_unlinked_booked_heads_up():
    from app.services.service import booking, predictive
    pred = next(p for p in predictive.predictions({"D-101"}, "us")["predictions"] if p.get("customer_id"))
    predictive.raise_lead(pred, "U-US-00005")
    booking.book_service(pred["vin"], services=["Oil & filter"], preferred_slot="2026-12-01T15:00:00Z")


@pytest.mark.parametrize("rule, plant", [
    ("handoff-waits-on-a-person", _plant_handoff_to_an_agent),
    ("booked-heads-up-linked", _plant_unlinked_booked_heads_up),
    ("sold-unit-has-no-hold", _plant_sold_unit_with_hold),
    ("service-lead-no-sales-journey", _plant_service_lead_with_journey),
    ("one-active-purchase", _plant_second_active_journey),
    ("one-open-alert", _plant_duplicate_alert),
    ("handoffs-have-journeys", _plant_orphan_handoff),
    ("channel-cap-within-weekly", _plant_channel_cap_over_weekly),
    ("nurture-plans-within-cap", _plant_daily_nurture_plan),
    ("consent-not-universal", _plant_universal_consent),
    ("plan-draft-newest", _plant_stale_draft),
])
def test_each_rule_catches_its_break(fake_ddb, rule, plant):
    assert _failed(rule).ok
    plant()
    r = _failed(rule)
    assert not r.ok and r.count >= 1 and r.sample, r


def test_a_rule_that_cannot_run_is_reported_not_skipped(fake_ddb, monkeypatch):
    monkeypatch.setattr(invariants.RULES[0], "run", lambda d: 1 / 0)
    (r,) = invariants.check(only={invariants.RULES[0].rule_id})
    assert not r.ok and "ZeroDivisionError" in (r.error or "")
