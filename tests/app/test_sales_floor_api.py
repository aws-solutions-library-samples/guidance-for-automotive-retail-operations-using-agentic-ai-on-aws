"""Sales floor (Advisor, Discovery, Trade-In, Assistant, Lead Desk, Service Lane)."""
from __future__ import annotations

import pytest

from app.services.events import bus
from app.services.platform import generate
from tests.app.conftest import api_event, call

ADV = dict(user="U-US-00002", role="dealer_advisor", dealer_ids="D-101")
BDC = dict(user="U-US-00005", role="dealer_bdc", dealer_ids="D-101")
OTHER = dict(user="U-US-00011", role="dealer_advisor", dealer_ids="D-102")


@pytest.fixture
def fdb(fake_ddb):
    return fake_ddb


def _types():
    return [e["type"].split("@")[0] for e in bus.EMITTED]


def test_tco_comparison_is_market_correct_and_audited(fdb):
    code, t = call(api_event("GET", "/journeys/J-US-J1/tco", **ADV))
    assert code == 200, t
    assert t["ev"]["powertrain"] == "EV" and t["ice"]["powertrain"] in ("ICE", "HEV")
    tco_row = next(r for r in t["rows"] if r["key"] == "tco")
    assert tco_row["winner"] in ("ev", "ice")
    assert t["audit"]["tco"]["vehicles"][0]["formula"]
    assert t["currency"] == "USD" and t["assumptions"]["distance_unit"] == "mi"
    # DE journey renders in EUR/km with no code change (acceptance #10)
    de = next(j for j in fdb.rows("DEALER_Journeys") if j["journey_id"].startswith("J-DE-") and j["stage"] != "OWN")
    from app.services.agents import tco
    out = tco.build_tco_comparison(de["journey_id"])
    assert out["currency"] == "EUR" and out["assumptions"]["distance_unit"] == "km"


def test_advisor_aggregate_records_shown_recommendations(fdb):
    code, a = call(api_event("GET", "/journeys/J-US-J1/advisor", **ADV))
    assert code == 200
    assert a["state"]["stage"] == "QUALIFY"
    assert a["customer_360"]["name"]
    assert a["matches"]["matches"], "discovery returns vehicles (packages excluded)"
    assert all(m.get("powertrain") for m in a["matches"]["matches"])
    assert a["recommendation_ids"].get("nba")
    rid = a["recommendation_ids"]["nba"]
    row = next(r for r in fdb.rows("DEALER_Recommendations") if r["recommendation_id"] == rid)
    assert row["shown_at"] and row["journey_id"] == "J-US-J1"


def test_advisor_scope_enforced(fdb):
    code, _ = call(api_event("GET", "/journeys/J-US-J1/advisor", **OTHER))
    assert code == 403


def test_present_writes_event_and_accepts_recommendation(fdb):
    _, a = call(api_event("GET", "/journeys/J-US-J1/advisor", **ADV))
    m = a["matches"]["matches"][0]
    rid = a["recommendation_ids"][f"match:{m['trim_id']}"]
    code, p = call(api_event("POST", "/journeys/J-US-J1/present", **ADV,
                             body={"model_id": m["model_id"], "trim_id": m["trim_id"], "recommendation_id": rid}))
    assert code == 200
    ev = [e for e in fdb.rows("DEALER_JourneyEvents") if e.get("event_id") == p["event_id"]]
    assert ev and ev[0]["event_type"] == "vehicle_presented"
    rec = next(r for r in fdb.rows("DEALER_Recommendations") if r["recommendation_id"] == rid)
    assert rec["accepted_by"] == "U-US-00002"


def test_objection_flow_and_outcome(fdb):
    code, o = call(api_event("POST", "/journeys/J-US-J1/objection", **ADV,
                             body={"utterance": "Honestly this is way too expensive for us"}))
    assert code == 200 and o["objection_detected"] and o["objection_type"] == "price_too_high"
    assert o["recommendation_id"] and o["suggested_response"]
    code, out = call(api_event("POST", "/journeys/J-US-J1/objection/outcome", **ADV,
                               body={"strategy_id": o["strategy_id"], "outcome": "used",
                                     "recommendation_id": o["recommendation_id"]}))
    assert code == 200 and out["outcome"] == "used"
    rec = next(r for r in fdb.rows("DEALER_Recommendations") if r["recommendation_id"] == o["recommendation_id"])
    assert rec["accepted_at"]


def test_outreach_callback_creates_task_and_finance_link_sends(fdb):
    code, cb = call(api_event("POST", "/journeys/J-US-J1/outreach", **ADV, body={"kind": "callback"}))
    assert code == 200 and cb["task_id"]
    bus.EMITTED.clear()
    code, fl = call(api_event("POST", "/journeys/J-US-J1/outreach", **ADV, body={"kind": "finance_link", "channel": "email"}))
    assert code == 200 and "message_id" in fl or fl.get("reason") == "no_consent"
    code, bad = call(api_event("POST", "/journeys/J-US-J1/outreach", **ADV, body={"kind": "spam"}))
    assert code == 400


def test_record_shortlist_saves_build_and_emits(fdb):
    bus.EMITTED.clear()
    code, opts = call(api_event("GET", "/configurations/options", **ADV, query={"model": "MER-VOLT-C"}))
    assert code == 200 and opts["trims"] and opts["packages"]
    trim = opts["trims"][1]
    adv104 = dict(user="U-US-00029", role="dealer_advisor", dealer_ids="D-104")
    code, out = call(api_event("POST", "/configurations", **adv104,
                               body={"journey_id": "J-US-J9", "model_id": "MER-VOLT-C", "trim_id": trim["trim_id"],
                                     "packages": [opts["packages"][0]["trim_id"]]}))
    assert code == 201, out
    cfg = out["configuration"]
    assert cfg["price_vehicle"] == trim["list_price"] + opts["packages"][0]["list_price"]
    assert "build.saved" in _types()
    assert out["journey_advance"]["to"] == "CONFIGURE"   # J9 was in DISCOVER


def test_trade_valuation_by_vin_and_payment_delta(fdb):
    veh = next(v for v in fdb.rows("DEALER_Vehicles") if v.get("dealer_id") == "D-101"
               and any(x.get("customer_id") == v["customer_id"] for x in fdb.rows("DEALER_Valuations")))
    code, v = call(api_event("POST", "/trade-in/valuation", **ADV, body={"vin": veh["vin"], "condition": "fair"}))
    assert code == 200, v
    assert v["range"]["low"] <= v["trade_value"] <= v["range"]["high"]
    code, d = call(api_event("POST", "/trade-in/payment-delta", **ADV, body={"journey_id": "J-US-J1"}))
    assert code == 200 and d["new_monthly"] > 0 and d["formula"]
    code, p = call(api_event("GET", "/trade-in/prospects", **ADV))
    assert code == 200


def test_assistant_chat_is_agent_specific_cited_and_refuses_credit(fdb, monkeypatch):
    calls = []

    def grounded(question, facts, passages, **kwargs):
        calls.append({"question": question, "facts": list(facts), "persona": kwargs["persona"]})
        return {"text": f"{kwargs['persona']}: {question}", "engine": "bedrock-test", "model": "test",
                "guardrail": {"applied": True, "intervened": False}}

    monkeypatch.setattr(generate, "ground", grounded)
    answers = {}
    for agent_id in ("A1", "A2", "A3", "A4"):
        code, answer = call(api_event(
            "POST", "/assistant/chat", **ADV,
            body={"journey_id": "J-US-J1", "question": "What should I do next?", "agent_id": agent_id}))
        assert code == 200 and answer["citations"], answer
        assert answer["engine"] == "bedrock-test" and answer["agent_id"] == agent_id
        answers[agent_id] = answer["answer"]
    assert len(set(answers.values())) == 4
    assert len({call_["persona"] for call_ in calls}) == 4
    assert len({tuple(call_["facts"]) for call_ in calls}) == 4

    code, refused = call(api_event(
        "POST", "/assistant/chat", **ADV,
        body={"journey_id": "J-US-J1", "question": "Will she be approved for credit?", "agent_id": "A4"}))
    assert code == 200 and refused["guardrail"]
    assert len(calls) == 4  # deterministic refusal occurs before any model call

    code, invalid = call(api_event(
        "POST", "/assistant/chat", **ADV,
        body={"journey_id": "J-US-J1", "question": "hello", "agent_id": "A9"}))
    assert code == 400 and "unsupported" in invalid["error"]


def test_shift_list_publish_creates_tasks_idempotently(fdb):
    code, s = call(api_event("GET", "/assistant/shift-list", **ADV))
    assert code == 200
    code, p1 = call(api_event("POST", "/assistant/shift-list/publish", **ADV))
    code, p2 = call(api_event("POST", "/assistant/shift-list/publish", **ADV))
    assert len(p1["published_tasks"]) == s["count"]
    assert p2["published_tasks"] == []


def test_follow_up_draft_grounded(fdb):
    code, d = call(api_event("POST", "/assistant/flows/follow-up-draft", **ADV, body={"journey_id": "J-US-J1"}))
    assert code == 200 and d["body"].startswith("Hi Jordan") and d["facts_used"]


def test_lead_assign_goes_through_handoff_and_respond_stamps_sla(fdb):
    lead = next(l for l in fdb.rows("DEALER_Leads") if l["dealer_id"] == "D-101" and not l.get("first_response_at")) \
        if any(not l.get("first_response_at") for l in fdb.rows("DEALER_Leads") if l["dealer_id"] == "D-101") \
        else next(l for l in fdb.rows("DEALER_Leads") if l["dealer_id"] == "D-101")
    code, q = call(api_event("GET", "/leads/queue", **BDC))
    assert code == 200 and q["total"] > 0
    code, s = call(api_event("GET", f"/leads/{lead['lead_id']}/script", **BDC))
    assert code == 200 and s["opening"]
    code, a = call(api_event("POST", f"/leads/{lead['lead_id']}/assign", **BDC, body={"assignee": "U-US-00003"}))
    assert code == 200, a
    if a.get("handoff_id"):
        h = next(x for x in fdb.rows("DEALER_Handoffs") if x["handoff_id"] == a["handoff_id"])
        assert h["to_user"] == "U-US-00003"
    code, r = call(api_event("POST", f"/leads/{lead['lead_id']}/respond", **BDC, body={"channel": "call"}))
    assert code == 200 and r["first_response_at"]


def test_service_lane_and_customer_360(fdb):
    code, lane = call(api_event("GET", "/service/lane", user="U-US-00007", role="dealer_service", dealer_ids="D-101"))
    assert code == 200
    for it in lane["items"]:
        assert it["dealer_id"] == "D-101" and "upsell" in it
    code, c = call(api_event("GET", "/customers/C-US-J1", **ADV))
    assert code == 200 and c["customer"]["customer_id"] == "C-US-J1" and c["journeys"]
    code, _ = call(api_event("GET", "/customers/C-US-J1", **OTHER))
    assert code == 403
    code, _ = call(api_event("GET", "/customers/C-US-J1", user="U-T", role="dealer_technician", dealer_ids="D-101"))
    assert code == 403


def test_board_cards_enriched(fdb):
    code, b = call(api_event("GET", "/journeys/board", **ADV))
    assert code == 200
    card = next(c for c in b["lanes"]["QUALIFY"] if c["journey_id"] == "J-US-J1")
    assert card["customer_name"] == "Jordan Alvarez" and "gaps" in card and card["days_in_stage"] is not None


def test_bulk_nudge_reports_per_journey(fdb):
    code, r = call(api_event("POST", "/journeys/bulk/nudge", **ADV, body={"journey_ids": ["J-US-J1", "J-US-J2"]}))
    assert code == 200
    by = {x["journey_id"]: x for x in r["results"]}
    assert by["J-US-J1"]["ok"] and not by["J-US-J2"]["ok"]   # J2 is another dealer


def test_replay_script_exercises_objection_library(fdb):
    code, s = call(api_event("GET", "/journeys/J-US-J1/replay-script", **ADV))
    assert code == 200 and len(s["lines"]) >= 3
    detected = 0
    for line in s["lines"]:
        _, o = call(api_event("POST", "/journeys/J-US-J1/objection", **ADV, body={"utterance": line}))
        detected += bool(o.get("objection_detected"))
    assert detected >= 2


def test_competitive_intel_uses_named_competitors_in_market(fdb):
    from app.services.agents import intel
    j = next(x for x in fdb.rows("DEALER_Journeys") if x["journey_id"] == "J-US-J1")
    out = intel.get_competitive_intel(j)
    assert out["competitors"] and all(c["win_rate"] is not None for c in out["competitors"])
    assert all(c["model_id"].startswith("MER-") for c in out["comparables"])   # no other-market trims


def test_prospects_have_structured_reasons(fdb):
    code, p = call(api_event("GET", "/trade-in/prospects", **ADV))
    assert code == 200 and p["prospects"]
    first = p["prospects"][0]
    assert first["name"] and first["currency"] and all(not any(ch.isdigit() for ch in r) for r in first["reasons"])


def test_role_gates(fdb):
    tech = dict(user="U-T", role="dealer_technician", dealer_ids="D-101")
    for method, path, body in [("GET", "/journeys/J-US-J1/advisor", None), ("GET", "/assistant/shift-list", None),
                               ("GET", "/leads/queue", None), ("POST", "/assistant/chat", {"question": "hi"})]:
        code, _ = call(api_event(method, path, **tech, body=body))
        assert code == 403, path
    # OEM read-across may view the Advisor but not write
    dm = dict(user="U-US-00044", role="oem_dm", is_oem=True)
    assert call(api_event("GET", "/journeys/J-US-J1/advisor", **dm))[0] == 200
    assert call(api_event("POST", "/journeys/J-US-J1/present", **dm, body={"trim_id": "X"}))[0] == 403


# --- one buyer, end to end: web lead → desk → floor → trade → F&I → delivery ---------------

FNI = dict(user="U-US-00006", role="dealer_fni", dealer_ids="D-101")


def _traced(event):
    """Through lambda_handler, so the request writes its PLAT_AgentTraces run (call() routes only)."""
    import json
    from app.services.api import handler
    resp = handler.lambda_handler(event)
    return resp["statusCode"], json.loads(resp["body"])

WEB_LEAD = {"source": "web_form", "given_name": "Priya", "family_name": "Natarajan", "email": "priya.natarajan@example.com",
            "phone": "(404) 555-0147", "city": "Decatur", "state": "GA", "zip5": "30030",
            "vehicle_of_interest_model_id": "MER-VOLT-C",
            "comments": "Is the Ion C Extended in stock in blue or white? And what is my Trek 4 worth as a trade?",
            "budget": {"price_min": 32000, "price_max": 42000, "monthly_max": 560, "down": 3000},
            "current_vehicle": {"year": 2019, "make": "Kestrel", "model": "Trek 4", "mileage": 61000,
                                "ownership": "loan", "payoff": 8400, "months_remaining": 11}}


def test_web_lead_intake_lands_new_with_a_running_sla_clock(fdb):
    code, out = call(api_event("POST", "/leads", **BDC, body=WEB_LEAD))
    assert code == 201, out
    lead, sla = out["lead"], out["sla"]
    assert lead["status"] == "new" and lead["first_response_at"] is None and lead["dealer_id"] == "D-101"
    assert sla["sla_minutes"] == 30 and sla["sla_due_at"] > lead["received_at"] and sla["breached"] is False
    assert out["customer_created"] is True and out["customer_name"] == "Priya Natarajan"
    cust = next(c for c in fdb.rows("DEALER_Customers") if c["customer_id"] == out["customer_id"])
    assert cust["budget"]["price_max"] == 42000 and cust["current_vehicle"]["model"] == "Trek 4"
    code, q = call(api_event("GET", "/leads/queue", **BDC))
    card = next(c for c in q["queue"]["new"] if c["lead_id"] == lead["lead_id"])
    assert card["customer_name"] == "Priya Natarajan" and card["vehicle_name"] == "Meridian Ion C" and card["sla_due_at"]
    assert "lead.received" in _types()
    # the same e-mail again is the same customer (CRM dedupe), a new lead
    code, again = call(api_event("POST", "/leads", **BDC, body=WEB_LEAD))
    assert code == 201 and again["customer_id"] == out["customer_id"] and again["customer_created"] is False
    assert again["lead"]["lead_id"] != lead["lead_id"]
    # validation
    code, _ = call(api_event("POST", "/leads", **BDC, body={"source": "carrier_pigeon", "customer_id": "C-US-J1"}))
    assert code == 400
    code, _ = call(api_event("POST", "/leads", **BDC, body={"source": "web_form", "customer_id": "C-US-J1",
                                                             "vehicle_of_interest_model_id": "MER-NOPE"}))
    assert code == 400
    code, _ = call(api_event("POST", "/leads", **BDC, body={"source": "web_form"}))
    assert code == 400
    code, _ = call(api_event("POST", "/leads", user="U-US-00007", role="dealer_service", dealer_ids="D-101", body=WEB_LEAD))
    assert code == 403


def test_one_buyer_follows_the_lead_through_assign_matches_trade_and_the_desk(fdb):
    code, out = _traced(api_event("POST", "/leads", **BDC, body=WEB_LEAD))
    lid, cid = out["lead"]["lead_id"], out["customer_id"]
    # the desk responds, then hands to Elizabeth: a handoff is pending, the owner has not flipped yet
    code, r = _traced(api_event("POST", f"/leads/{lid}/respond", **BDC, body={"channel": "email"}))
    assert code == 200 and r["first_response"] is True and (r.get("sent") or r.get("fallback_task"))   # offline: SES → task
    code, a = _traced(api_event("POST", f"/leads/{lid}/assign", **BDC, body={"assignee": "U-US-00002"}))
    assert code == 200 and a["journey_created"] is True and a["handoff_id"]
    jid = a["journey_id"]
    j = next(x for x in fdb.rows("DEALER_Journeys") if x["journey_id"] == jid)
    assert j["owner_user_id"] == "U-US-00005" and j["pending_handoff"]["to_user"] == "U-US-00002"
    assert j["context"]["requested_model_id"] == "MER-VOLT-C" and j["context"]["lead_comments"].startswith("Is the Ion C")
    code, q = call(api_event("GET", "/leads/queue", **BDC))
    card = next(c for c in q["queue"]["worked"] if c["lead_id"] == lid)
    assert card["pending_handoff"]["to_user_name"] == "Elizabeth Thomas" and card["assigned_user_name"] == "Elizabeth Thomas"
    assert card["journey_owner_name"] == "Keisha Baker" and card["first_response_channel"] == "email"
    # the lead's own activity feed has the desk's runs, before and after the journey existed
    code, tr = call(api_event("GET", "/traces", **BDC, query={"lead_id": lid}))
    tools = {t["tool"] for t in tr["traces"]}
    assert {"a2.lead_intake", "a3.send_first_response", "a2.assign_lead"} <= tools
    code, b = call(api_event("POST", f"/leads/{lid}/appointment", **BDC, body={}))
    assert code == 200
    code, q = call(api_event("GET", "/leads/queue", **BDC))
    assert next(c for c in q["queue"]["worked"] if c["lead_id"] == lid)["appointment"]["slot_start"]
    # Elizabeth accepts: owner flips, the pending marker clears
    code, acc = call(api_event("POST", f"/handoffs/{a['handoff_id']}/accept", **ADV))
    assert code == 200
    j = next(x for x in fdb.rows("DEALER_Journeys") if x["journey_id"] == jid)
    assert j["owner_user_id"] == "U-US-00002" and "pending_handoff" not in j
    # A1's matches lead with what she asked for
    code, adv = call(api_event("GET", f"/journeys/{jid}/advisor", **ADV))
    assert code == 200
    m = adv["matches"]
    assert m["requested_model_id"] == "MER-VOLT-C" and m["requested_from"] == "lead"
    assert m["matches"][0]["model_id"] == "MER-VOLT-C" and m["matches"][0]["requested"] is True
    assert m["matches"][0]["score_breakdown"]["asked_for"] == 30 and m["matches"][0]["score"] <= 100
    assert "asked about" in m["matches"][0]["rationale"]
    # the trade she typed into the form is appraised from comparables and attached to the journey
    code, v = call(api_event("POST", "/trade-in/valuation", **ADV, body={"customer_id": cid}))
    assert code == 200 and v["vehicle"]["model"] == "Trek 4" and v["trade_value"] > 0 and v["payoff"] == 8400
    assert "comparables" in v["source_ref"] or "sandbox valuation connector" in v["source_ref"]
    assert v["active_journeys"] == [jid] and v["loan_lease"]["payoff_kind"] == "loan payoff"
    code, t = call(api_event("POST", f"/journeys/{jid}/trade", **ADV, body={"condition": "good"}))
    assert code == 200 and t["context"]["valuation"] is True
    # the desk opens on the presented trim; a VIN is reserved; the deal books with that VIN
    code, _ = call(api_event("POST", f"/journeys/{jid}/present", **ADV, body={"model_id": "MER-VOLT-C", "trim_id": "MER-VOLT-C-T2"}))
    code, d = call(api_event("POST", f"/fni/journeys/{jid}/deal", **FNI))
    assert code == 201 and d["worksheet"]["customer_name"] == "Priya Natarajan"
    assert d["worksheet"]["trade"]["vehicle"].endswith("Kestrel Trek 4") and d["worksheet"]["price"]["breakdown"]["reconciles"]
    unit = next(x for x in fdb.rows("DEALER_Inventory") if x["dealer_id"] == "D-101" and x["status"] == "in_stock" and x["trim_id"] == "MER-VOLT-C-T2")
    code, _ = call(api_event("POST", f"/inventory/{unit['vin']}/reserve", **FNI, body={"journey_id": jid}))
    assert code == 200
    did = d["deal"]["deal_id"]
    code, dec = call(api_event("POST", f"/fni/deals/{did}/lender-decision", **FNI))
    assert code == 200 and dec["any_approved"]
    code, p = call(api_event("POST", f"/fni/deals/{did}/push", **FNI))
    assert code == 200 and p["vin"] == unit["vin"]
    code, ws = call(api_event("GET", f"/fni/deals/{did}", **FNI))
    assert ws["vehicle"]["vin"] == unit["vin"] and ws["readiness"]["vin_reserved"] is True
    # a second web lead from the same buyer after this journey rests in OWN opens a NEW journey
    j = next(x for x in fdb.rows("DEALER_Journeys") if x["journey_id"] == jid)
    fdb.seed("DEALER_Journeys", [{**j, "stage": "OWN"}])
    code, out2 = call(api_event("POST", "/leads", **BDC, body=WEB_LEAD))
    code, a2 = call(api_event("POST", f"/leads/{out2['lead']['lead_id']}/assign", **BDC, body={"assignee": "U-US-00002"}))
    assert code == 200 and a2["journey_created"] is True and a2["journey_id"] != jid
    j2 = next(x for x in fdb.rows("DEALER_Journeys") if x["journey_id"] == a2["journey_id"])
    assert j2["previous_journey_id"] == jid and j2["stage"] == "DISCOVER"


def test_matches_keep_the_requested_model_on_the_list_for_a_seeded_lead(fdb):
    # a seeded D-101 web lead asked about the Trailhand; the matches lead with it
    lead = next(l for l in fdb.rows("DEALER_Leads") if l["dealer_id"] == "D-101" and l["status"] == "new"
                and l["vehicle_of_interest_model_id"] == "MER-TRAILHAND" and l["source"] == "web_form")
    code, a = call(api_event("POST", f"/leads/{lead['lead_id']}/assign", **BDC, body={"assignee": "U-US-00002"}))
    code, adv = call(api_event("GET", f"/journeys/{a['journey_id']}/advisor", **ADV))
    top = adv["matches"]["matches"][0]
    assert top["model_id"] == "MER-TRAILHAND" and adv["matches"]["requested_model_name"] == "Meridian Trailhand"


def test_lease_payoff_is_the_buyout_not_zero(fdb):
    custs = {c["customer_id"]: c for c in fdb.rows("DEALER_Customers")}
    leases = [v for v in fdb.rows("DEALER_Valuations")
              if (custs.get(v["customer_id"], {}).get("current_vehicle") or {}).get("ownership") == "lease"]
    assert leases and all(v["payoff"] > 0 and v["equity"] == v["trade_value"] - v["payoff"] for v in leases)
    c = next(c for c in custs.values() if (c.get("current_vehicle") or {}).get("ownership") == "lease" and c["dealer_id"] == "D-101")
    code, v = call(api_event("POST", "/trade-in/valuation", **ADV, body={"customer_id": c["customer_id"]}))
    assert code == 200 and v["payoff"] > 0 and v["loan_lease"]["payoff_kind"] == "lease buyout"
