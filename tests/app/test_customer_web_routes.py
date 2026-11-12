"""Customer Web: My Garage, My Garage › Service, /shop, magic links (spec §5.10, §5.12, §8.1)."""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import pytest

from app.services.api import authorizer
from app.services.common import ddb
from app.services.customer import magic_link
from app.services.events import bus
from app.services.journeys import nurture
from app.services.l1_data import ddb_load
from tests.app.conftest import api_event, call

ROOT = Path(__file__).resolve().parents[2]
# Tables whose PK_OVERRIDES entry does not match the seed rows; the real loader
# falls back to the first unique *_id (resolve_pk_field). Re-key them so the
# in-memory store matches AWS (see report: local_ddb.key_schema).
_REKEY = ("CUST_Consent", "DEALER_DeclinedWork", "OEM_Catalog", "DEALER_FinanceOffers")


@pytest.fixture
def db(fake_ddb):
    for t in _REKEY:
        rows = []
        for m in ("us", "de"):
            p = ROOT / "sample-data" / "synthetic" / m / "tables" / f"{t}.json"
            rows += json.loads(p.read_text())
        pk = ddb_load.resolve_pk_field(t, rows, frozenset(), lambda b: tuple(b.split("_", 1)))
        fake_ddb.keys[t] = (pk,)
        fake_ddb.tables[t] = {}
        fake_ddb.seed(t, rows)
    return fake_ddb


def cust(method, path, body=None, cid="C-US-J1", query=None):
    return call(api_event(method, path, user=cid, role="customer", customer_id=cid, body=body, query=query))


def _types():
    return [e["type"].split("@")[0] for e in bus.EMITTED]


# --- self scope + reads ----------------------------------------------------------

def test_staff_cannot_use_customer_routes(db):
    code, _ = call(api_event("GET", "/garage/journey", role="dealer_advisor", dealer_ids="D-101"))
    assert code == 403


def test_garage_sections_are_customer_safe(db):
    code, j = cust("GET", "/garage/journey")
    assert code == 200 and j["journey"]["stage"] == "QUALIFY"
    assert [s["state"] for s in j["journey"]["steps"]][:3] == ["done", "done", "current"]
    assert "owner_user_id" not in json.dumps(j)
    code, b = cust("GET", "/garage/builds")
    assert code == 200 and b["builds"][0]["trim_name"] != b["builds"][0]["trim_id"]
    code, t = cust("GET", "/garage/trade")
    assert code == 200 and t["current"]["range"]["low"] < t["current"]["trade_value"] < t["current"]["range"]["high"]
    code, o = cust("GET", "/garage/ownership")
    assert code == 200 and o["vehicles"][0]["next_service"]["due_by"]
    assert o["upgrade_offer"] and o["upgrade_offer"]["equity"] > 0
    code, d = cust("GET", "/garage/documents", cid="C-US-J4")   # J4 carries the seeded documents
    assert code == 200 and d["documents"] and "uri" not in json.dumps(d)


def test_build_availability_hides_cost(db):
    code, b = cust("GET", "/garage/builds")
    code, a = cust("GET", f"/garage/builds/{b['builds'][0]['config_id']}/availability")
    assert code == 200
    assert "cost" not in json.dumps(a)
    code, _ = cust("GET", f"/garage/builds/{b['builds'][0]['config_id']}/availability", cid="C-US-J2")
    assert code == 404


# --- consent stops A2 (acceptance #22) -------------------------------------------------

def test_consent_change_stops_nurture_channel(db):
    at = _dt.datetime(2026, 9, 1, 15, 0, tzinfo=_dt.timezone.utc)  # 11:00 New York, outside quiet hours
    before = nurture.check_consent("C-US-J1", "email", at)
    assert before["ok"] or "cap" in before.get("reason", ""), before
    code, c = cust("PUT", "/customers/C-US-J1/consent", {"email_opt_in": False, "sms_opt_in": False})
    assert code == 200 and set(c["changed"]) >= {"email_opt_in", "sms_opt_in"}
    assert not nurture.check_consent("C-US-J1", "email", at)["ok"]
    sms = nurture.check_consent("C-US-J1", "sms", at)
    assert not sms["ok"] and sms["reason"] == "sms opted out"
    assert "consent.changed" in _types()


def test_consent_validation_and_scope(db):
    code, _ = cust("PUT", "/customers/C-US-J1/consent", {"preferred_channel": "fax"})
    assert code == 400
    code, _ = cust("PUT", "/customers/C-US-J1/consent", {"quiet_hours": {"start": "25:00", "end": "08:00"}})
    assert code == 400
    code, _ = cust("GET", "/customers/C-US-J2/consent")
    assert code == 403
    # staff in scope may read; technician may not
    code, _ = call(api_event("GET", "/customers/C-US-J1/consent", role="dealer_advisor", dealer_ids="D-101"))
    assert code == 200
    code, _ = call(api_event("GET", "/customers/C-US-J1/consent", role="dealer_technician", dealer_ids="D-101"))
    assert code == 403
    code, _ = call(api_event("GET", "/customers/C-US-J1/consent", role="dealer_advisor", dealer_ids="D-102"))
    assert code == 403


def test_unsubscribe_all_and_data_request(db):
    code, c = cust("PUT", "/customers/C-US-J1/consent", {"unsubscribe_all": True})
    assert code == 200 and c["unsubscribed_all"] and not c["email_opt_in"] and c["sms_opted_out"]
    code, r1 = cust("POST", "/garage/data-request", {"kind": "download"})
    assert code == 201 and r1["status"] == "received"
    code, _ = cust("POST", "/garage/data-request", {"kind": "download"})
    assert code == 400
    code, c = cust("GET", "/customers/C-US-J1/consent")
    assert c["data_requests"][0]["kind"] == "download"


# --- appointments --------------------------------------------------------------------------

def test_reschedule_and_cancel_upcoming_only(db):
    future = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=3)).replace(microsecond=0)
    ddb.put_item("DEALER_Appointments", {"appointment_id": "AP-T1", "customer_id": "C-US-J1", "dealer_id": "D-101",
                                         "type": "test_drive", "status": "scheduled", "journey_id": "J-US-J1",
                                         "slot_start": future.isoformat().replace("+00:00", "Z"),
                                         "slot_end": (future + _dt.timedelta(hours=1)).isoformat().replace("+00:00", "Z")})
    later = (future + _dt.timedelta(days=1)).isoformat().replace("+00:00", "Z")
    code, a = cust("PATCH", "/garage/appointments/AP-T1", {"action": "reschedule", "slot_start": later})
    assert code == 200 and a["slot_start"] == later and "appointment.changed" in _types()
    code, a = cust("PATCH", "/garage/appointments/AP-T1", {"action": "cancel"})
    assert code == 200 and a["status"] == "cancelled"
    code, _ = cust("PATCH", "/garage/appointments/AP-T1", {"action": "cancel"})
    assert code == 400
    code, _ = cust("PATCH", "/garage/appointments/AP-US-00001", {"action": "cancel"}, cid="C-US-J2")
    assert code == 404


def test_trade_refresh(db):
    code, t = cust("POST", "/garage/trade/refresh", {"mileage": 72000})
    assert code == 200 and t["current"]["mileage"] == 72000 and t["history"]
    code, _ = cust("POST", "/garage/trade/refresh", {"condition": "mint"})
    assert code == 400


# --- service: estimate approval (acceptance #27, customer side) -----------------------------

def _approve_ready(db, sj="SJ-US-00001"):
    """Give SJ-US-00001 three actionable findings so line-level decisions are exercised.
    The seeded estimate carries persisted lines; drop them so A7 re-prices from these findings."""
    for est in [e for e in db.rows("DEALER_ServiceEstimates") if e.get("service_journey_id") == sj]:
        db.seed("DEALER_ServiceEstimates", [{**est, "lines": [], "lines_persisted": False}])
    for i, (item, status) in enumerate([("Front brakes", "red"), ("Tires", "yellow"), ("Wipers", "yellow")]):
        ddb.put_item("DEALER_Inspections", {"inspection_id": f"INS-T-{i}", "service_journey_id": sj, "ro_id": "RO-US-00001",
                                            "item": item, "status": status, "urgency": "safety" if status == "red" else "soon",
                                            "measurement": 2, "unit": "mm", "evidence_doc_id": f"DOC-T-{i}"})


def test_service_detail_lines_bundles_and_explanations(db):
    _approve_ready(db)
    code, d = cust("GET", "/garage/service/SJ-US-00001")
    assert code == 200 and d["estimate"]["decidable"]
    lines = d["estimate"]["lines"]
    assert len(lines) >= 3 and all(ln["price_source"] and ln["finding_ref"] for ln in lines)
    tiers = [b["tier"] for b in d["estimate"]["bundles"]]
    assert {"good", "better"} <= set(tiers) and len({tuple(b["line_ids"]) for b in d["estimate"]["bundles"]}) == len(tiers)
    assert d["estimate"]["total"] == pytest.approx(sum(ln["line_total"] for ln in lines))
    assert "technician_id" not in json.dumps(d) and "labor_rate" not in json.dumps(d)
    code, _ = cust("GET", "/garage/service/SJ-US-00001", cid="C-US-J2")
    assert code == 404


def test_partial_approval_dispatches_approved_and_records_declined(db):
    _approve_ready(db)
    code, d = cust("GET", "/garage/service/SJ-US-00001")
    lines = d["estimate"]["lines"]
    est = d["estimate"]["estimate_id"]
    decline = lines[-1]["line_id"]
    bus.EMITTED.clear()
    # first decision: not complete yet
    code, r1 = cust("POST", f"/garage/service/estimates/{est}/decisions",
                    {"decisions": [{"line_id": lines[0]["line_id"], "decision": "approve"}]})
    assert code == 200 and not r1["complete"]
    code, r2 = cust("POST", f"/garage/service/estimates/{est}/decisions",
                    {"decisions": [{"line_id": ln["line_id"], "decision": "decline" if ln["line_id"] == decline else "approve"}
                                   for ln in lines[1:]]})
    # a partial approval is its own status (Service Desk single implementation, acc #27)
    assert code == 200 and r2["complete"] and r2["status"] == "partially_approved"
    assert decline in r2["declined_line_ids"] and len(r2["approved_line_ids"]) == len(lines) - 1
    row = ddb.get_item("DEALER_ServiceEstimates", {"estimate_id": est})
    assert row["line_decisions"][decline]["decided_by"] == "customer"
    assert row["line_decisions"][decline]["presented"]["finding_ref"]
    dw = ddb.scan_filter("DEALER_DeclinedWork", "customer_id", "C-US-J1")
    assert any(i.get("line_id") == decline for r in dw for i in (r.get("items") or []))
    assert _types().count("estimate.line_decided") >= len(lines)
    # decisions are closed once complete
    code, _ = cust("POST", f"/garage/service/estimates/{est}/decisions",
                   {"decisions": [{"line_id": decline, "decision": "approve"}]})
    assert code == 409


def test_decision_validation(db):
    _approve_ready(db)
    code, _ = cust("POST", "/garage/service/estimates/EST-US-00001/decisions",
                   {"decisions": [{"line_id": "L99", "decision": "approve"}]})
    assert code == 400
    code, _ = cust("POST", "/garage/service/estimates/EST-US-00001/decisions",
                   {"decisions": [{"line_id": "L01", "decision": "maybe"}]})
    assert code == 400


def test_book_service_owned_vin_only(db):
    vin = next(v["vin"] for v in db.rows("DEALER_Vehicles") if v["customer_id"] == "C-US-J1")
    code, s = cust("GET", "/garage/service/slots", query={"vin": vin})
    assert code == 200 and s["slots"]
    code, b = cust("POST", "/garage/service/book", {"vin": vin, "services": ["Oil & filter"],
                                                    "wait_type": "loaner", "slot_start": s["slots"][0]["slot_start"]})
    assert code == 201 and b["stage"] == "BOOK"
    assert "service.appointment.booked" in _types()
    code, _ = cust("POST", "/garage/service/book", {"vin": vin, "wait_type": "teleport"})
    assert code == 400
    code, _ = cust("POST", "/garage/service/book", {"vin": vin}, cid="C-US-J2")
    assert code == 404


# --- /shop (acceptance #12, #13) -----------------------------------------------------------------

def test_shop_intake_asks_at_most_four(db):
    code, i = cust("POST", "/shop/intake", {"answers": {}}, cid="C-US-00007")
    assert code == 200 and len(i["questions"]) <= 4


def test_shortlist_reasons_cite_profile_fields(db):
    code, s = cust("POST", "/shop/shortlist", {"answers": {"household_size": 4, "powertrain_openness": "ev"}})
    assert code == 200 and 1 <= len(s["shortlist"]) <= 3
    for item in s["shortlist"]:
        assert item["why"] and all(w["field"] for w in item["why"])
        assert item["price"]["est_monthly"]["buy"] > 0
    code, _ = cust("POST", "/shop/shortlist", {"answers": {"household_size": "lots"}})
    assert code == 400


def test_save_build_advances_journey_and_emits(db):
    code, s = cust("POST", "/shop/shortlist", {"answers": {}}, cid="C-US-J9")
    pick = s["shortlist"][0]
    bus.EMITTED.clear()
    code, b = cust("POST", "/shop/builds", {"model_id": pick["model_id"], "trim_id": pick["recommended"]["trim_id"]},
                   cid="C-US-J9")
    assert code == 201 and b["journey_id"] == "J-US-J9" and b["stage"] == "CONFIGURE"
    assert "build.saved" in _types()
    code, _ = cust("POST", "/shop/builds", {"model_id": "NOPE"}, cid="C-US-J9")
    assert code == 400


def test_save_build_for_owner_creates_new_journey_and_supersedes(db):
    code, s = cust("POST", "/shop/shortlist", {"answers": {}}, cid="C-US-J6")
    pick = s["shortlist"][0]
    code, b = cust("POST", "/shop/builds", {"model_id": pick["model_id"]}, cid="C-US-J6")
    assert code == 201 and b["journey_created"]
    own = ddb.get_item("DEALER_Journeys", {"journey_id": "J-US-J6"})
    assert own["superseded_by"] == b["journey_id"]


def test_chat_grounded_and_escalates_credit(db):
    code, c = cust("POST", "/shop/chat", {"text": "What would my monthly payment be?"})
    assert code == 200 and c["answer"] and c["citations"]
    code, c = cust("POST", "/shop/chat", {"text": "Will I be approved for credit?"})
    assert code == 200 and c["escalated"] and c["escalated"]["task_id"]
    code, t = cust("GET", "/shop/chat")
    # the two questions above are in the transcript (the seed may carry earlier chat for this customer)
    bodies = [m.get("body") or m.get("text") or "" for m in t["messages"] if m["direction"] == "inbound"]
    assert sum(1 for b in bodies if "monthly payment" in b or "approved for credit" in b) == 2
    assert "message.inbound" in _types() and "message.outbound" in _types()


# --- magic links (§8.1) ---------------------------------------------------------------------

def test_magic_link_issue_redeem_and_authorizer(db):
    code, _ = call(api_event("POST", "/auth/magic-link", role="dealer_service", dealer_ids="D-102",
                             body={"customer_id": "C-US-J1", "purpose": "estimate:SJ-US-00001"}))
    assert code == 403
    code, link = call(api_event("POST", "/auth/magic-link", role="dealer_service", dealer_ids="D-101",
                                body={"customer_id": "C-US-J1", "purpose": "estimate:SJ-US-00001"}))
    assert code == 201 and link["landing"] == "/garage/service/SJ-US-00001"
    p = authorizer.resolve_principal(f"Bearer mlk:{link['token']}")
    assert p["role"] == "customer" and p["customer_id"] == "C-US-J1" and p["purpose"].startswith("estimate")
    ev = api_event("POST", "/auth/magic-link/redeem", user="C-US-J1", role="customer", customer_id="C-US-J1")
    ev["headers"] = {"Authorization": f"Bearer mlk:{link['token']}"}
    code, sess = call(ev)
    assert code == 200 and sess["token"].startswith("mlk:")
    assert authorizer.resolve_principal(f"Bearer {sess['token']}")["customer_id"] == "C-US-J1"
    # a session token cannot be redeemed again; tampering is rejected
    with pytest.raises(magic_link.InvalidToken):
        magic_link.redeem(sess["token"])
    h, b, s = link["token"].split(".")
    with pytest.raises(Exception):
        authorizer.resolve_principal(f"Bearer mlk:{h}.{b}.{s[:-2]}aa")


def test_magic_link_expiry(db):
    out = magic_link.issue_magic_link("C-US-J1", "garage", ttl_s=-1)
    with pytest.raises(magic_link.InvalidToken):
        magic_link.verify(out["token"])
    with pytest.raises(magic_link.InvalidToken):
        magic_link.issue_magic_link("C-NOPE", "garage")
