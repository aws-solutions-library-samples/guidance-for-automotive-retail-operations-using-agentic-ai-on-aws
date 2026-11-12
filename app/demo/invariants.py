"""Data invariants — the rules that must hold in any state of the stack.

Every demo gap round found records that disagreed with each other after real use: a sold car
still "reserved", a service lead with a sales journey, five open copies of one alert, two
screens counting dealers differently. Unit tests start from a clean seed, so they never saw
the state a demoed, reloaded stack is actually in. This sweep reads that state.

    python -m app.demo.invariants                 # the deployed tables (RETAIL_OPS_ENV, default dev)
    python -m app.demo.invariants --market us     # one market
    python -m app.demo.invariants --json          # machine-readable

Read-only: it scans tables and calls read models; it writes nothing. Exit code 1 when any rule
is broken. `check()` is also what tests/app/test_invariants.py runs against the seed.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.services.common import ddb

_ACTIVE_PURCHASE = ("DISCOVER", "CONFIGURE", "QUALIFY", "FINANCE", "TRADE_IN", "AGREE", "DELIVER")
_OPEN_VISIT = ("BOOK", "CHECK_IN", "INSPECT", "DIAGNOSE", "APPROVE", "REPAIR", "READY")
_SAMPLE = 5   # offending ids reported per rule


@dataclass
class Rule:
    rule_id: str
    gap: str            # the demo gap it guards against (round.item), so a failure says why it matters
    text: str
    run: Callable[[_Data], list[str]]


@dataclass
class Result:
    rule_id: str
    gap: str
    text: str
    ok: bool
    count: int = 0
    sample: list[str] = field(default_factory=list)
    error: str | None = None


class _Data:
    """Lazy, memoised table reads (each table is scanned once per sweep)."""

    def __init__(self, markets: tuple[str, ...], env_prefix: str | None):
        self.markets, self.env_prefix = markets, env_prefix
        self._t: dict[str, list[dict[str, Any]]] = {}

    def t(self, table: str) -> list[dict[str, Any]]:
        if table not in self._t:
            self._t[table] = ddb.scan_all(table, self.env_prefix)
        return self._t[table]

    def by_market(self, table: str) -> list[dict[str, Any]]:
        return [r for r in self.t(table) if str(r.get("market_id") or "").lower() in self.markets]


def _iso_days_ago(ts: Any) -> float | None:
    try:
        when = _dt.datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.UTC)
    return (_dt.datetime.now(_dt.UTC) - when).total_seconds() / 86400


# --- lifecycle: an action finished on every record it touches --------------------------

def _sold_unit_has_no_hold(d: _Data) -> list[str]:
    return [u["vin"] for u in d.by_market("DEALER_Inventory")
            if u.get("status") == "sold" and u.get("reserved_for_journey_id")]


def _delivered_car_is_not_in_stock(d: _Data) -> list[str]:
    inv = {u["vin"]: u for u in d.t("DEALER_Inventory") if u.get("vin")}
    return [v["vin"] for v in d.by_market("DEALER_Vehicles")
            if str(v.get("source_ref") or "").startswith("DeliveryBoard.mark_delivered")
            and v["vin"] in inv and inv[v["vin"]].get("status") != "sold"]


def _stack_delivery_records_its_car(d: _Data) -> list[str]:
    # deliveries completed on the stack (mark_delivered stamps delivered_at); the seed's are separate
    return [x["delivery_id"] for x in d.t("DEALER_Deliveries")
            if x.get("status") == "completed" and x.get("delivered_at") and not x.get("vin")]


def _delivered_journey_has_no_open_appointment(d: _Data) -> list[str]:
    """r6: My Garage offered 'Reschedule' on a test drive for a car the customer already owned."""
    owned = {j["journey_id"] for j in d.by_market("DEALER_Journeys") if j.get("stage") == "OWN"}
    return [f"{a.get('appointment_id')} ({a.get('type')}) on {a.get('journey_id')}" for a in d.t("DEALER_Appointments")
            if a.get("journey_id") in owned and a.get("type") in ("test_drive", "delivery")
            and a.get("status") in ("scheduled", "booked")]


def _open_visits_are_current(d: _Data) -> list[str]:
    """r6.7: a car in the bay with a promise two days old is a stale record, not a lane. A visit in an
    in-bay stage was opened within the last 3 days; its promise is no more than 36 h behind now; a
    booked visit's slot is not in the past by more than a day."""
    bad = []
    for sj in d.by_market("DEALER_ServiceJourneys"):
        stage = sj.get("stage")
        if stage not in _OPEN_VISIT:
            continue
        opened = _iso_days_ago(sj.get("opened_at"))
        promised = _iso_days_ago(sj.get("promised_at"))
        if stage == "BOOK":
            if promised is not None and promised > 1:
                bad.append(f"{sj['service_journey_id']} booked for {promised:.0f}d ago")
        elif (opened is not None and opened > 3) or (promised is not None and promised > 1.5):
            bad.append(f"{sj['service_journey_id']} {stage} opened {opened:.0f}d ago, promised {promised:.0f}d ago"
                       if opened is not None and promised is not None else f"{sj['service_journey_id']} {stage}")
    return bad


def _delivery_date_matches_the_journey(d: _Data) -> list[str]:
    """r6: 'vehicle delivered 193d ago' in the feed next to 'took delivery Nov 4' on the card. The VIN
    master's delivered_at and the journey's vehicle.delivered event are one moment (within a day)."""
    events: dict[str, str] = {}
    for e in d.t("DEALER_JourneyEvents"):
        if e.get("event_type") == "vehicle.delivered" and e.get("journey_id"):
            events[e["journey_id"]] = max(events.get(e["journey_id"], ""), str(e.get("event_ts") or ""))
    jm = {j["journey_id"]: j for j in d.by_market("DEALER_Journeys")}
    bad = []
    for v in d.by_market("DEALER_Vehicles"):
        jid = v.get("journey_id") or next((j for j, r in jm.items() if r.get("customer_id") == v.get("customer_id")
                                           and r.get("stage") == "OWN" and r.get("primary_model_id") == v.get("model_id")), None)
        ev = events.get(jid or "")
        if not ev or not v.get("delivered_at") or str(v.get("source_ref") or "").endswith("(owned)"):
            continue
        gap = (_iso_days_ago(v["delivered_at"]) or 0) - (_iso_days_ago(ev) or 0)
        if abs(gap) > 1:
            bad.append(f"{v['vin']} delivered {v['delivered_at'][:10]} vs event {ev[:10]}")
    return bad


def _one_payment_per_booked_deal(d: _Data) -> list[str]:
    """r6: $711 / $356 / $387.61 for one buyer. A booked deal's monthly payment equals its accepted
    offer's; a journey with a booked deal has exactly one accepted offer."""
    offers_by_journey: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for o in d.t("DEALER_FinanceOffers"):
        offers_by_journey[o.get("journey_id")].append(o)
    bad = []
    for deal in d.by_market("DEALER_Deals"):
        if deal.get("status") not in ("booked", "contracted", "signed") or deal.get("type") == "cash":
            continue
        accepted = [o for o in offers_by_journey.get(deal.get("journey_id"), []) if o.get("status") == "accepted"]
        if len(accepted) > 1:
            bad.append(f"{deal['deal_id']}: {len(accepted)} accepted offers")
        elif accepted and deal.get("monthly_payment") is not None \
                and abs(float(accepted[0].get("monthly_payment") or 0) - float(deal["monthly_payment"])) > 0.5:
            bad.append(f"{deal['deal_id']}: deal {deal['monthly_payment']} vs offer {accepted[0].get('monthly_payment')}")
    return bad


def _screens_agree_on_fixed_ops(d: _Data) -> list[str]:
    """r7: 'promise at risk' was 1 on the GM cockpit, 12 on the service board and '25% kept' on the service
    cockpit for one store. The three read models carry the shared summary (service/metrics.py); here they
    must report the same numbers for the demo store."""
    from app.services.dealer import cockpit
    from app.services.service import desk
    claims = {"user_id": "U-US-00056", "role": "dealer_service_manager", "market_id": "us",
              "dealer_ids": {"D-101"}, "is_oem": False}
    gm = cockpit.snapshot({"dealer_ids": {"D-101"}, "is_oem": False, "market_id": "us", "role": "dealer_manager"},
                          "D-101", "us", d.env_prefix, fresh=True, overlay=False)["tabs"]["service"]["metrics"]
    board = desk.list_journeys(claims, {}, d.env_prefix)["kpis"]["shared_metrics"]
    svc = desk.cockpit(claims, "us", env_prefix=d.env_prefix)["tiles"]["shared_metrics"]
    from app.services.service import board as board_mod
    lane = board_mod.build_service_board({"dealer_ids": {"D-101"}, "is_oem": False, "market_id": "us"}, "us", d.env_prefix)["kpis"]["shared_metrics"]
    bad = []
    for k in ("open_visits", "at_risk", "past_promise", "parts_hold", "close_rate", "lane_load"):
        vals = {gm.get(k), board.get(k), svc.get(k), lane.get(k)}
        if len(vals) > 1:
            bad.append(f"{k}: cockpit {gm.get(k)} / board {board.get(k)} / service cockpit {svc.get(k)} / lane board {lane.get(k)}")
    return bad


def _no_upgrade_nudge_on_a_new_car(d: _Data) -> list[str]:
    """r7: 'upgrade with $16,554 equity' on the day the car was bought. The equity-flag recommendations
    and loyalty offers must be on cars inside the upgrade window (common/vehicles.upgrade_window)."""
    from app.services.common import vehicles
    bad = []
    for r in d.t("DEALER_Recommendations"):
        if r.get("action") == "equity_flag" and r.get("customer_id") and (r.get("status") or "open") in ("open", "shown"):
            if not vehicles.upgrade_window(r["customer_id"], d.env_prefix):
                bad.append(f"{r.get('recommendation_id')} for {r['customer_id']}")
    return bad


def _delivered_trade_reached_used_stock(d: _Data) -> list[str]:
    """r7: Hannah's Trek 4 never showed in Trade dispositions. A delivery completed on the stack whose
    journey had a valuation leaves a used unit and a pending disposition behind."""
    deliveries = [x for x in d.t("DEALER_Deliveries") if x.get("status") == "completed" and x.get("delivered_at")
                  and str(x.get("source_ref") or "") == "" or (x.get("status") == "completed" and x.get("vin") and x.get("delivered_at"))]
    jm = {j["journey_id"]: j for j in d.t("DEALER_Journeys")}
    traded = {v.get("traded_on_journey_id") for v in d.t("DEALER_Valuations") if v.get("disposition") == "traded"}
    units = {u.get("from_journey_id") for u in d.t("DEALER_Inventory") if u.get("acquired_via") == "trade"}
    bad = []
    for x in deliveries:
        j = jm.get(x.get("journey_id"))
        if not j or not (j.get("context") or {}).get("valuation"):
            continue
        if str(x.get("delivered_at") or "") < "2026-11-09":      # before the rule existed
            continue
        if x["journey_id"] not in traded or x["journey_id"] not in units:
            bad.append(f"{x.get('delivery_id')} ({x['journey_id']})")
    return bad


def _one_active_purchase_per_customer(d: _Data) -> list[str]:
    c = Counter(j.get("customer_id") for j in d.by_market("DEALER_Journeys")
                if (j.get("status") or "active") == "active" and j.get("stage") in _ACTIVE_PURCHASE)
    return [f"{cid} ×{n}" for cid, n in c.items() if cid and n > 1]


def _one_open_visit_per_car(d: _Data) -> list[str]:
    c = Counter(s.get("vin") for s in d.by_market("DEALER_ServiceJourneys") if s.get("stage") in _OPEN_VISIT)
    return [f"{vin} ×{n}" for vin, n in c.items() if vin and n > 1]


def _service_lead_has_no_sales_journey(d: _Data) -> list[str]:
    return [f"{lead['lead_id']} → {lead['journey_id']}" for lead in d.by_market("DEALER_Leads")
            if lead.get("source") == "predictive_maintenance" and lead.get("journey_id")]


def _plan_draft_is_the_newest_version(d: _Data) -> list[str]:
    plans = d.by_market("OEM_AccountPlans")
    by_dealer: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in plans:
        by_dealer[p.get("dealer_id")].append(p)
    bad = []
    for p in plans:
        if p.get("status") != "draft" or "-DRAFT-" not in str(p.get("plan_id")):
            continue
        others = [int(o.get("version") or 0) for o in by_dealer[p.get("dealer_id")] if o["plan_id"] != p["plan_id"]]
        if others and int(p.get("version") or 0) <= max(others):
            bad.append(f"{p['plan_id']} v{p.get('version')} ≤ v{max(others)}")
    return bad


def _pending_handoffs_wait_on_a_person(d: _Data) -> list[str]:
    """A handoff waits on someone who can accept it: an active staff user, not an agent id."""
    users = {u["user_id"]: u for u in d.t("PLAT_Users")}
    jids = {j["journey_id"]: j for j in d.t("DEALER_Journeys")}
    bad = []
    for h in d.t("DEALER_Handoffs"):
        j = jids.get(h.get("journey_id"))
        if h.get("accepted_at") or not j or str(j.get("market_id") or "").lower() not in d.markets:
            continue
        if (j.get("pending_handoff") or {}).get("handoff_id") != h.get("handoff_id"):
            continue   # no longer the journey's open handoff
        u = users.get(h.get("to_user"))
        if not u or not u.get("active", True):
            bad.append(f"{h['handoff_id']} → {h.get('to_user')}")
    return bad


def _booked_heads_up_is_linked(d: _Data) -> list[str]:
    """A car with an open heads-up AND a booked visit: the lead knows about that visit (else the
    BDC books the car a second time, and My Garage keeps offering to book)."""
    booked: dict[str, str] = {}
    sjs = {s["service_journey_id"]: s for s in d.t("DEALER_ServiceJourneys")}
    for a in d.t("DEALER_Appointments"):
        s = sjs.get(a.get("service_journey_id") or "")
        if a.get("type") == "service" and a.get("status") == "booked" and s and s.get("stage") == "BOOK":
            booked[s.get("vin")] = s["service_journey_id"]
    return [f"{lead['lead_id']} (visit {booked[lead['predicted_vin']]})" for lead in d.by_market("DEALER_Leads")
            if lead.get("source") == "predictive_maintenance" and lead.get("status") in ("new", "working")
            and lead.get("predicted_vin") in booked and not lead.get("service_appointment")]


# --- references: nothing points at a record that is gone ---------------------------------

def _handoffs_point_at_journeys(d: _Data) -> list[str]:
    jids = {j["journey_id"] for j in d.t("DEALER_Journeys")}
    return [h["handoff_id"] for h in d.t("DEALER_Handoffs") if h.get("journey_id") and h["journey_id"] not in jids]


def _open_tasks_point_at_journeys(d: _Data) -> list[str]:
    jids = {j["journey_id"] for j in d.t("DEALER_Journeys")}
    return [t["task_id"] for t in d.t("DEALER_Tasks")
            if t.get("status") in ("open", "in_progress") and t.get("journey_id") and t["journey_id"] not in jids]


def _leads_point_at_journeys(d: _Data) -> list[str]:
    jids = {j["journey_id"] for j in d.t("DEALER_Journeys")}
    return [lead["lead_id"] for lead in d.by_market("DEALER_Leads")
            if lead.get("journey_id") and lead["journey_id"] not in jids]


def _revaluations_point_at_their_anchor(d: _Data) -> list[str]:
    ids = {v["valuation_id"] for v in d.t("DEALER_Valuations")}
    return [v["valuation_id"] for v in d.t("DEALER_Valuations")
            if v.get("baseline_valuation_id") and v["baseline_valuation_id"] not in ids]


# --- one fact, one answer: screens that show the same thing agree --------------------------

def _one_open_alert_per_store_and_condition(d: _Data) -> list[str]:
    c = Counter((a.get("dealer_id"), a.get("kind")) for a in d.by_market("OEM_Alerts") if a.get("status") == "open")
    return [f"{dealer}/{kind} ×{n}" for (dealer, kind), n in c.items() if n > 1]


def _today_and_accounts_agree(d: _Data) -> list[str]:
    from app.services.oem import today, workspace
    bad = []
    for m in d.markets:
        scope = {"is_oem": True, "market_id": m, "scope_node_id": "OEM", "role": "oem_dm"}
        t = today.build_today(scope, m, d.env_prefix)["kpis"]
        a = workspace.list_accounts(scope, m, d.env_prefix)
        if (t["dealers"], t["dealers_at_risk"]) != (a["dealers"], a["at_risk"]):
            bad.append(f"{m}: Today {t['dealers']} dealers/{t['dealers_at_risk']} at risk vs "
                       f"Accounts {a['dealers']}/{a['at_risk']}")
        for r in a["accounts"]:
            gap, sh = r.get("target_gap"), r.get("sales_health_index")
            if r["account_type"] != "dealer" or gap is None:
                continue
            if gap >= 0 and (sh or 1) >= workspace.LOW_SALES_HEALTH and r["at_risk"]:
                bad.append(f"{r['dealer_id']} at risk at {gap:+.2f} vs peers")
            if gap <= workspace.AT_RISK_GAP and not r["at_risk"]:
                bad.append(f"{r['dealer_id']} on track at {gap:+.2f} vs peers")
    return bad


def _approvals_belong_to_a_market(d: _Data) -> list[str]:
    from app.services.approvals import generic
    dm = generic._dealer_markets(d.env_prefix)
    return [a["approval_id"] for a in d.t("DEALER_Approvals") if not generic._row_market(generic._norm(a), dm)]


# --- plausibility: the numbers could be real ------------------------------------------------

def _channel_caps_fit_the_weekly_cap(d: _Data) -> list[str]:
    bad = []
    for cfg in d.by_market("PLAT_ConfigNurture"):
        caps = cfg.get("cadence_caps") or {}
        week = caps.get("per_week")
        for ch, v in (caps.get("per_channel_week") or {}).items():
            if isinstance(week, (int, float)) and isinstance(v, (int, float)) and v > week:
                bad.append(f"{cfg.get('item_key')} v{cfg.get('version')}: {ch} {v}/week > {week}/week")
    return bad


def _nurture_plans_fit_the_cap(d: _Data) -> list[str]:
    from app.services.journeys import nurture
    caps = {str(c.get("market_id")).lower(): (c.get("cadence_caps") or {}).get("per_week")
            for c in d.by_market("PLAT_ConfigNurture") if c.get("is_active")}
    custs = {c["customer_id"]: str(c.get("market_id") or "").lower() for c in d.by_market("DEALER_Customers")}
    bad = []
    for p in d.t("DEALER_NurturePlans"):
        m = custs.get(p.get("customer_id"))
        week = caps.get(m) if m else None
        if week and p.get("cadence") and 7 / max(nurture._cadence_days(p["cadence"]), 1) > week:
            bad.append(f"{p.get('nurture_plan_id')} {p['cadence']}")
    return bad


def _consent_is_not_universal(d: _Data) -> list[str]:
    bad = []
    for m in d.markets:
        rows = [c for c in d.t("CUST_Consent") if str(c.get("market_id") or "").lower() == m]
        if not rows:
            continue
        rate = sum(1 for c in rows if c.get("email_opt_in") or c.get("sms_opt_in")) / len(rows)
        if rate >= 0.99 or rate < 0.3:
            bad.append(f"{m}: {rate:.0%} of {len(rows)} customers marketable")
    return bad


def _campaign_status_matches_its_dates(d: _Data) -> list[str]:
    now = _dt.datetime.now(_dt.UTC).isoformat()
    return [f"{c['campaign_id']} {c.get('status')} from {str(c.get('start_at'))[:10]}"
            for c in d.by_market("OEM_Campaigns")
            if c.get("status") == "scheduled" and str(c.get("start_at") or "") <= now]


def _budget_shift_returns_more_than_it_moves(d: _Data) -> list[str]:
    from app.services.oem import campaigns
    bad = []
    for m in d.markets:
        scope = {"market_id": m, "is_oem": True, "scope_node_id": "OEM", "role": "oem_marketing"}
        s = campaigns.recommend_budget_shift(scope, m, env_prefix=d.env_prefix)
        sh = s.get("shift") if s.get("recommended") else None
        if sh and not ((sh.get("expected_lift_gross") or 0) > (sh.get("amount") or 0) and (sh.get("expected_net_gain") or 0) > 0):
            bad.append(f"{m}: moves {sh.get('amount')} for {sh.get('expected_lift_gross')} gross")
        if sh and (sh["to"].get("roi") or 0) <= 0:
            bad.append(f"{m}: shifts into {sh['to']['campaign_id']} at ROI {sh['to']['roi']}")
    return bad


def _technician_lists_hold_only_current_work(d: _Data) -> list[str]:
    from app.services.service import desk
    techs = [u for u in d.t("PLAT_Users") if "dealer_technician" in (u.get("groups") or [])
             and str(u.get("market_id") or "").lower() in d.markets]
    bad = []
    for u in techs:
        claims = {"user_id": u["user_id"], "role": "dealer_technician", "market_id": u.get("market_id"),
                  "dealer_ids": {u.get("dealer_id")} if u.get("dealer_id") else set(), "is_oem": False}
        rows = {s["service_journey_id"]: s for s in d.t("DEALER_ServiceJourneys")}
        for c in desk.list_journeys(claims, {"unassigned": "1"}, d.env_prefix)["cards"]:
            s = rows.get(c["service_journey_id"]) or {}
            age = _iso_days_ago(s.get("updated_at"))
            if c.get("technician_id") == u["user_id"] and c["stage"] == "READY" and age is not None and age > 1.5:
                bad.append(f"{u['user_id']}: {c['service_journey_id']} READY {age:.0f} days")
    return bad


RULES: list[Rule] = [
    Rule("sold-unit-has-no-hold", "r4.3", "A sold unit is never also reserved", _sold_unit_has_no_hold),
    Rule("delivered-car-not-in-stock", "r4.3", "A car delivered on the stack is sold in inventory",
         _delivered_car_is_not_in_stock),
    Rule("delivery-records-its-car", "r4.3", "A delivery completed on the stack records the VIN handed over",
         _stack_delivery_records_its_car),
    Rule("one-active-purchase", "r4.17", "A customer has at most one active purchase journey",
         _one_active_purchase_per_customer),
    Rule("delivered-no-open-appointment", "r6.2", "A delivered journey has no test drive or delivery still scheduled",
         _delivered_journey_has_no_open_appointment),
    Rule("open-visits-current", "r6.7", "Every open service visit was opened lately and its promise is not days old",
         _open_visits_are_current),
    Rule("delivery-date-matches-journey", "r6.x", "A car's delivery date is the journey's delivery event (one moment)",
         _delivery_date_matches_the_journey),
    Rule("one-payment-per-deal", "r6.2", "A booked finance deal has one accepted offer and the deal's payment is that offer's",
         _one_payment_per_booked_deal),
    Rule("screens-agree-fixed-ops", "r7.3", "GM cockpit, service board and service cockpit report the same fixed-ops numbers",
         _screens_agree_on_fixed_ops),
    Rule("no-upgrade-nudge-on-new-car", "r7.5", "Equity nudges only inside the upgrade window (a year owned, or a lease ending)",
         _no_upgrade_nudge_on_a_new_car),
    Rule("delivered-trade-in-stock", "r7.6", "A delivery with a trade leaves a used unit and a pending disposition",
         _delivered_trade_reached_used_stock),
    Rule("one-open-visit-per-car", "r3.lane", "A car has at most one open service visit", _one_open_visit_per_car),
    Rule("service-lead-no-sales-journey", "r4.5", "A predictive service lead never opens a sales journey",
         _service_lead_has_no_sales_journey),
    Rule("handoff-waits-on-a-person", "r4.w3", "Every open handoff waits on an active person (never an agent id)",
         _pending_handoffs_wait_on_a_person),
    Rule("booked-heads-up-linked", "r4.w4", "A heads-up whose car has a booked visit is linked to that visit",
         _booked_heads_up_is_linked),
    Rule("plan-draft-newest", "r4.6", "A plan draft is the newest version of its dealer's plan",
         _plan_draft_is_the_newest_version),
    Rule("handoffs-have-journeys", "r4.17", "Every handoff points at a journey that exists", _handoffs_point_at_journeys),
    Rule("open-tasks-have-journeys", "r4.17", "Every open task on a journey points at one that exists",
         _open_tasks_point_at_journeys),
    Rule("leads-have-journeys", "r4.17", "Every lead's journey exists", _leads_point_at_journeys),
    Rule("revaluations-have-anchor", "r4.10", "Every re-valuation points at the valuation it was computed from",
         _revaluations_point_at_their_anchor),
    Rule("one-open-alert", "r4.11", "One open alert per store and condition", _one_open_alert_per_store_and_condition),
    Rule("today-matches-accounts", "r4.12-13",
         "Today and Accounts agree on dealer and at-risk counts; at risk follows the target gap",
         _today_and_accounts_agree),
    Rule("approvals-have-market", "r4.8", "Every approval belongs to a market (so its inbox can scope it)",
         _approvals_belong_to_a_market),
    Rule("channel-cap-within-weekly", "r4.14", "No channel's weekly cap exceeds the weekly cap",
         _channel_caps_fit_the_weekly_cap),
    Rule("nurture-plans-within-cap", "r4.14", "No nurture plan's cadence promises more than the weekly cap",
         _nurture_plans_fit_the_cap),
    Rule("consent-not-universal", "r4.15", "Marketing consent is between 30% and 99% of customers",
         _consent_is_not_universal),
    Rule("campaign-status-matches-dates", "r4.7", "No campaign is 'scheduled' after its start date",
         _campaign_status_matches_its_dates),
    Rule("budget-shift-pays", "r4.7", "A recommended budget shift returns more than it moves, into a campaign that pays",
         _budget_shift_returns_more_than_it_moves),
    Rule("tech-list-current", "r4.18", "No technician's list holds a car finished more than a day ago",
         _technician_lists_hold_only_current_work),
]


def check(markets: tuple[str, ...] = ("us", "de"), env_prefix: str | None = None,
          only: set[str] | None = None) -> list[Result]:
    data = _Data(tuple(m.lower() for m in markets), env_prefix)
    out: list[Result] = []
    with ddb.read_memo():
        for rule in RULES:
            if only and rule.rule_id not in only:
                continue
            try:
                bad = rule.run(data)
                out.append(Result(rule.rule_id, rule.gap, rule.text, not bad, len(bad), bad[:_SAMPLE]))
            except Exception as e:  # noqa: BLE001 - a rule that cannot run is reported, never skipped silently
                out.append(Result(rule.rule_id, rule.gap, rule.text, False, error=f"{type(e).__name__}: {e}"))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--market", default="us,de", help="comma-separated markets (default us,de)")
    ap.add_argument("--only", default="", help="comma-separated rule ids")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    results = check(tuple(m for m in args.market.split(",") if m),
                    only={r for r in args.only.split(",") if r} or None)
    if args.json:
        print(json.dumps([r.__dict__ for r in results], indent=1))
    else:
        print("=== INVARIANTS: the stack's records agree with each other ===")
        for r in results:
            mark = "PASS" if r.ok else "FAIL"
            detail = f"  — {r.error}" if r.error else (f"  — {r.count}: {', '.join(r.sample)}" if not r.ok else "")
            print(f"  {mark}  {r.rule_id:32s} ({r.gap}) {r.text}{detail}")
        failed = [r for r in results if not r.ok]
        print(f"=== INVARIANTS RESULT: {'ALL PASS' if not failed else f'{len(failed)} FAILED'} "
              f"({len(results) - len(failed)}/{len(results)}) ===")
    return 1 if any(not r.ok for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
