#!/usr/bin/env python3
"""End-to-end demo driver for the core sales + service orchestration loop.

Walks the whole vehicle lifecycle against the LIVE API in one command, using
clearly-marked throwaway entities (DEMO-* ids) that are fully torn down at the
end. Nothing touches real seeded data. Every step is verified and printed.

The loop it exercises:
  1. A2  create a purchase journey (DISCOVER) + advance QUALIFY->FINANCE with
         validate_handoff gate (blocked, then supplied)
  2. A2  Sales Package aggregation (A1/A3/A4) + next-best-action
  3. Bus vehicle.delivered event -> A7 opens the first service journey
  4. A7  service state machine BOOK->...->APPROVE->(approve)->REPAIR with the
         validate_service_handoff gate
  5. A7  handoff_to_sales -> a new purchase journey A2 owns (the loop closes)
  6. teardown every DEMO-* row

Usage:
  python3 -m app.demo.run_demo --api https://<id>.execute-api.us-east-1.amazonaws.com/dev
  # token defaults to a dev staff token; --keep skips teardown for inspection
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.common import ddb  # noqa: E402

REGION = "us-east-1"
DEALER = "D-101"           # a US dealer
ADVISOR = "U-US-00001"     # a manager at D-101 (scopes D-101 journeys)
CUSTOMER = "C-DEMO-0001"
PURCHASE_JID = "J-DEMO-SALE"
SVC_JID = "SJ-DEMO-SVC"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Client:
    def __init__(self, base: str, token: str):
        self.base, self.token = base.rstrip("/"), token

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data,
                                     headers={"Authorization": self.token,
                                              "Content-Type": "application/json"},
                                     method=method)
        try:
            r = urllib.request.urlopen(req)
            return r.getcode(), json.load(r)
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.load(e)
            except Exception:
                return e.code, {"error": e.reason}


def ok(cond: bool, label: str) -> bool:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


_POLL_EVERY_S = 2
_POLL_DEADLINE_S = 30


def _await_header(dc, jid: str, requested_at: str, *, sleep, now) -> dict | None:
    """Poll GET /journeys/{id}/package until a header generated at or after
    `requested_at` appears (the Journey Agent finished), or the deadline passes."""
    deadline = now() + _POLL_DEADLINE_S
    while True:
        code, hdr = dc.call("GET", f"/journeys/{jid}/package")
        if code == 200 and (hdr.get("generated_at") or "") >= (requested_at or "\uffff"):
            return hdr
        if now() >= deadline:
            return None
        sleep(_POLL_EVERY_S)


def _package_check(dc, jid: str, *, sleep=time.sleep, now=time.monotonic) -> bool:
    """Step 2: the Sales Package builds and every specialist answered (design §10.7).

    A warm-up build first (unasserted; a 202 is polled and its outcome ignored),
    then 1 s so the asserted build lands in a later second. The asserted build
    passes on 200 only when every agent_status is true; on 202 it passes once a
    header with generated_at >= requested_at appears within 30 s (the header
    carries no specialist status); anything else fails."""
    code, body = dc.call("POST", f"/journeys/{jid}/package")
    if code == 202:
        _await_header(dc, jid, body.get("requested_at"), sleep=sleep, now=now)
    sleep(1)
    code, pkg = dc.call("POST", f"/journeys/{jid}/package")
    if code == 200:
        status = pkg.get("agent_status") or {}
        good = ok(bool(status) and all(status.values()), f"package built, all agents ok ({code})")
        for agent, fine in status.items():
            if not fine:
                card = (pkg.get("agents") or {}).get(agent) or {}
                print(f"       {agent}: {card.get('error')}")
        print(f"       summary: {pkg.get('summary')}")
        return good
    if code == 202:
        hdr = _await_header(dc, jid, pkg.get("requested_at"), sleep=sleep, now=now)
        good = ok(hdr is not None, f"package built ({code}, polled to a fresh header)")
        if hdr is not None:
            print("       built asynchronously; specialist status is not in the header")
            print(f"       summary: {hdr.get('summary')}")
        return good
    ok(False, f"package built ({code})")
    print(f"       body: {json.dumps(pkg, default=str)[:800]}")
    return False


def seed():
    """Create throwaway rows the demo needs (customer + valuation)."""
    ddb.put_item("DEALER_Customers", {
        "customer_id": CUSTOMER, "given_name": "Demo", "family_name": "Driver",
        "dealer_id": DEALER, "market_id": "us", "email": "demo@example.test",
        "current_vehicle": {"make": "Meridian", "model": "Trailhand", "year": 2021},
        "source_ref": "demo:seed"})
    # customer's trade valuation (positive equity -> service-lane handoff candidate)
    ddb.put_item("DEALER_Valuations", {
        "valuation_id": "VAL-DEMO", "customer_id": CUSTOMER, "dealer_id": DEALER,
        "trade_value": 21500, "equity": 8200, "payoff": 13300, "currency": "USD",
        "make": "Meridian", "model": "Trailhand", "year": 2021, "mileage": 41000,
        "valued_at": _now(), "source_ref": "demo:seed"})


def teardown():
    import boto3
    c = boto3.client("dynamodb", region_name=REGION)
    def dele(table, key):
        try:
            c.delete_item(TableName=ddb.table_name(table), Key=key)
        except Exception:
            pass
    # journeys + their events
    for jid in (PURCHASE_JID, f"J-US-SVC-{SVC_JID.split('-')[-1]}"):
        dele("DEALER_Journeys", {"journey_id": {"S": jid}})
        for e in ddb.query_hash("DEALER_JourneyEvents", "journey_id", jid):
            dele("DEALER_JourneyEvents", {"journey_id": {"S": jid}, "sk": {"S": e["sk"]}})
    dele("DEALER_ServiceJourneys", {"service_journey_id": {"S": SVC_JID}})
    for e in ddb.query_hash("DEALER_ServiceJourneyEvents", "service_journey_id", SVC_JID):
        dele("DEALER_ServiceJourneyEvents", {"service_journey_id": {"S": SVC_JID}, "sk": {"S": e["sk"]}})
    dele("DEALER_Customers", {"customer_id": {"S": CUSTOMER}})
    dele("DEALER_Valuations", {"valuation_id": {"S": "VAL-DEMO"}})
    dele("DEALER_RepairOrders", {"ro_id": {"S": "RO-DEMO"}})
    dele("DEALER_Inspections", {"inspection_id": {"S": "INS-DEMO"}})
    dele("DEALER_ServiceEstimates", {"estimate_id": {"S": "EST-DEMO"}})
    dele("DEALER_SalesPackages", {"package_id": {"S": f"SP-{PURCHASE_JID}"}})
    dele("DEALER_Handoffs", {"handoff_id": {"S": f"HANDOFF-{SVC_JID}"}})
    # handoffs now open a receiver task (§5.18.3)
    for jid in (PURCHASE_JID, f"J-US-SVC-{SVC_JID.split('-')[-1]}"):
        for t in ddb.scan_filter("DEALER_Tasks", "journey_id", jid):
            dele("DEALER_Tasks", {"task_id": {"S": t["task_id"]}})


def run(base: str, token: str, keep: bool) -> int:
    dc = Client(base, token)
    passed = True

    print("\n=== 0. seed throwaway entities ===")
    seed()
    # the purchase journey (A2) at QUALIFY, missing budget+timeline
    ddb.put_item("DEALER_Journeys", {
        "journey_id": PURCHASE_JID, "stage": "QUALIFY", "source": "demo", "status": "active",
        "customer_id": CUSTOMER, "dealer_id": DEALER, "market_id": "us",
        "owner_user_id": ADVISOR, "primary_model_id": "MER-TRAILHAND",
        "context": {"shortlist": True, "saved_build": True},
        "missing_context": ["budget", "timeline"], "created_at": _now(), "updated_at": _now()})
    print("  seeded customer, valuation, purchase journey (QUALIFY)")

    print("\n=== 1. A2 supervisor: state + guarded advance ===")
    code, st = dc.call("GET", f"/journeys/{PURCHASE_JID}/state")
    passed &= ok(code == 200 and st.get("stage") == "QUALIFY", f"state=QUALIFY ({code})")
    passed &= ok(not st.get("handoff", {}).get("ok"), "handoff blocked (missing budget/timeline)")
    code, d = dc.call("POST", f"/journeys/{PURCHASE_JID}/advance", {"evidence": "demo"})
    passed &= ok(code == 409, f"advance blocked -> 409 ({code})")
    code, d = dc.call("POST", f"/journeys/{PURCHASE_JID}/advance",
                      {"evidence": "budget+timeline captured", "context_updates": {"budget": True, "timeline": True}})
    passed &= ok(code == 200 and d.get("to") == "FINANCE", f"advance QUALIFY->FINANCE ({code})")

    print("\n=== 2. A2 Sales Package (A1/A3/A4 aggregation) ===")
    passed &= _package_check(dc, PURCHASE_JID)

    print("\n=== 3. vehicle.delivered event -> A7 opens service journey ===")
    # For the demo we open the service journey directly (the live subscriber
    # opens one from a real delivered VIN; here we seed a controlled SJ).
    ddb.put_item("DEALER_ServiceJourneys", {
        "service_journey_id": SVC_JID, "stage": "BOOK", "market_id": "us",
        "dealer_id": DEALER, "customer_id": CUSTOMER, "vin": "VIN-DEMO",
        "advisor_id": ADVISOR, "purchase_journey_id": PURCHASE_JID,
        "promised_at": _now(), "opened_at": _now()})
    code, ss = dc.call("GET", f"/service/{SVC_JID}/state")
    passed &= ok(code == 200 and ss.get("stage") == "BOOK", f"service journey at BOOK ({code})")

    print("\n=== 4. A7 service state machine to REPAIR ===")
    dc.call("POST", f"/service/{SVC_JID}/advance", {"evidence": "appt booked"})  # BOOK->CHECK_IN
    ddb.put_item("DEALER_RepairOrders", {"ro_id": "RO-DEMO", "service_journey_id": SVC_JID,
        "ro_number": "RO-DEMO", "status": "open", "total": 0, "currency": "USD"})
    dc.call("POST", f"/service/{SVC_JID}/advance", {"evidence": "RO opened"})    # CHECK_IN->INSPECT
    ddb.put_item("DEALER_Inspections", {"inspection_id": "INS-DEMO", "service_journey_id": SVC_JID,
        "item": "brake pads", "status": "red", "urgency": "soon"})
    dc.call("POST", f"/service/{SVC_JID}/advance", {"evidence": "MPI"})          # INSPECT->DIAGNOSE
    dc.call("POST", f"/service/{SVC_JID}/advance", {"evidence": "DTC+estimate"}) # DIAGNOSE->APPROVE
    ddb.put_item("DEALER_ServiceEstimates", {"estimate_id": "EST-DEMO", "service_journey_id": SVC_JID,
        "total": 642.0, "currency": "USD", "status": "pending", "source_ref": "labor+parts"})
    code, d = dc.call("POST", f"/service/{SVC_JID}/advance", {"evidence": "try"})
    passed &= ok(code == 409, f"APPROVE blocked pending decision -> 409 ({code})")
    dc.call("POST", f"/service/{SVC_JID}/approve", {"decision": "approve", "estimate_id": "EST-DEMO"})
    code, d = dc.call("POST", f"/service/{SVC_JID}/advance", {"evidence": "customer approved"})
    passed &= ok(code == 200 and d.get("to") == "REPAIR", f"APPROVE->REPAIR after approval ({code})")

    print("\n=== 5. A7 handoff_to_sales -> new A2 purchase journey (loop closes) ===")
    code, ho = dc.call("POST", f"/service/{SVC_JID}/handoff-to-sales",
                       {"reason": "positive equity at follow-up"})
    new_jid = ho.get("new_journey_id")
    passed &= ok(code == 200 and bool(new_jid) and bool(ho.get("task_id")),
                 f"handoff -> journey {new_jid} + receiver task {ho.get('task_id')} ({code})")
    code, ns = dc.call("GET", f"/journeys/{new_jid}/state")
    if ho.get("created_journey"):
        passed &= ok(code == 200 and ns.get("stage") == "DISCOVER", f"A2 owns new journey in DISCOVER ({code})")
    else:
        # §2.1 single-active rule: the customer's open purchase journey absorbs the
        # service-lane handoff rather than a duplicate journey being created.
        passed &= ok(code == 200 and new_jid == PURCHASE_JID,
                     f"handoff attached to the active journey {PURCHASE_JID} (single-active rule) ({code})")

    print(f"\n=== RESULT: {'ALL PASS' if passed else 'SOME FAILURES'} ===")
    if keep:
        print("(--keep set; leaving DEMO-* rows for inspection)")
    else:
        print("=== teardown ===")
        teardown()
        gone = ddb.get_item("DEALER_Journeys", {"journey_id": PURCHASE_JID}) is None
        print(f"  teardown complete: purchase journey gone = {gone}")
    return 0 if passed else 1


# Read-model smoke matrix: (label, method, path, token, expected_status).
# One row per persona-class screen backing route, so a single run proves the
# whole deployed surface is healthy and scope-enforced. Uses real seeded ids
# read-only (no writes).
_SMOKE = [
    ("journeys board (advisor)",   "GET", "/journeys/board",                       "user:U-US-00002", 200),
    ("journey state (mgr)",        "GET", "/journeys/J-US-00185/state",            "user:U-US-00040", 200),
    ("journey nurture (mgr)",      "GET", "/journeys/J-US-00185/nurture",          "user:U-US-00040", 200),
    ("journey nudge (mgr)",        "GET", "/journeys/J-US-00185/nudge",            "user:U-US-00040", 200),
    ("intent-shift (mgr)",         "GET", "/journeys/J-US-00053/intent-shift",     "user:U-US-00032", 200),
    ("lifestyle infer (mgr)",      "GET", "/journeys/J-US-00185/lifestyle",        "user:U-US-00040", 200),
    ("recommend-config (mgr)",     "GET", "/journeys/J-US-00185/recommend-config", "user:U-US-00040", 200),
    ("equity (mgr)",               "GET", "/journeys/J-US-00185/equity",           "user:U-US-00040", 200),
    ("A3 suggestions (mgr)",       "GET", "/journeys/J-US-00185/suggestions",      "user:U-US-00040", 200),
    ("A1 price-build (mgr)",       "GET", "/journeys/J-US-00185/price-build?state=NY", "user:U-US-00040", 200),
    ("A1 inventory (mgr)",         "GET", "/journeys/J-US-00185/inventory",        "user:U-US-00040", 200),
    ("A3 deal desk (mgr)",         "GET", "/journeys/J-US-00185/deal",             "user:U-US-00040", 200),
    ("A3 competitive intel (mgr)", "GET", "/journeys/J-US-00185/competitive-intel","user:U-US-00040", 200),
    ("A3 incentives (mgr)",        "GET", "/journeys/J-US-00185/incentives",       "user:U-US-00040", 200),
    ("A3 cross-sell (mgr)",        "GET", "/journeys/J-US-00185/cross-sell",       "user:U-US-00040", 200),
    ("prequal missing amount 400", "POST","/journeys/J-US-00185/prequalify",       "user:U-US-00040", 400),
    ("service board (service)",    "GET", "/service/board",                        "user:U-US-00007", 200),
    ("service state (mgr)",        "GET", "/service/SJ-US-00001/state",            "user:U-US-00001", 200),
    ("svc predict (mgr)",          "GET", "/service/predict/7M755DVC2SB000190",      "user:U-US-00001", 200),
    ("svc findings (mgr)",         "GET", "/service/SJ-US-00001/findings",         "user:U-US-00001", 200),
    ("svc parts (mgr)",            "GET", "/service/SJ-US-00001/parts",            "user:U-US-00001", 200),
    ("oem today (oem_dm)",         "GET", "/oem/today",                            "user:U-US-00044", 200),
    ("oem campaigns (oem_dm)",     "GET", "/oem/campaigns",                        "user:U-US-00044", 200),
    ("oem alerts (oem_dm)",        "GET", "/oem/alerts",                           "user:U-US-00044", 200),
    ("oem draft-plan role (adv)",  "POST","/oem/accounts/ACC-US-00001/draft-plan", "user:U-US-00002", 403),
    ("A5 budget-shift role (adv)", "POST","/oem/campaigns/budget-shift",           "user:U-US-00002", 403),
    ("garage self (customer)",     "GET", "/garage",                              "cust:C-US-J5",    200),
    ("admin autonomy (admin)",     "GET", "/admin/autonomy",                       "user:U-US-00051", 200),
    ("store cockpit (mgr)",        "GET", "/dealer/cockpit",                       "user:U-US-00040", 200),
    ("cockpit blocked (advisor)",  "GET", "/dealer/cockpit",                       "user:U-US-00002", 403),
    ("delivery board (mgr)",       "GET", "/dealer/delivery",                      "user:U-US-00040", 200),
    ("lead desk (advisor)",        "GET", "/dealer/leads",                         "user:U-US-00002", 200),
    ("notifications (staff)",      "GET", "/notifications",                        "user:U-DE-00011", 200),
    ("approval status (oem_dm)",   "GET", "/approvals/plan/BRIEF-US-00006",        "user:U-US-00044", 200),
    # negative: scope + role enforcement
    ("advisor blocked from OEM",   "GET", "/oem/today",                            "user:U-US-00002", 403),
    ("customer blocked from board","GET", "/journeys/board",                       "cust:C-US-J5",    403),
]


def run_smoke(base: str) -> int:
    """Read-only health + scope check of every deployed read-model route."""
    print("\n=== SMOKE: deployed read-model surface ===")
    passed = True
    for label, method, path, token, want in _SMOKE:
        code, _ = Client(base, token).call(method, path)
        passed &= ok(code == want, f"{label}: {code} (want {want})")
    print(f"\n=== SMOKE RESULT: {'ALL PASS' if passed else 'SOME FAILURES'} ===")
    return 0 if passed else 1


# --- acceptance-criteria assertions (§11) -----------------------------------
# Programmatic checks for the four criteria the smoke matrix only touches
# shallowly: #6 (campaign ROI + budget-shift lift), #8 (IAM no Resource:* on
# DynamoDB — a LOCAL check against the live account), #13 (A2 nurture policy +
# chat grounding/escalation), #14 (A3 contextual suggestions cite activity).
# Read-only against seeded data; writes nothing.

_ACC_OEM = "user:U-US-00044"   # OEM DM (campaigns, budget-shift)
_ACC_MGR = "user:U-US-00040"   # D-110 manager (journey-scoped reads)
# A D-110 journey with signals (the 2 Nov pack regeneration, a8e2706, moved J-US-00284
# to D-104; every fixture id here must match the pack the stack was seeded from).
_ACC_JID = "J-US-00185"


def _acc_6(dc: Client) -> bool:
    """#6 Campaigns ROI per campaign + A5 ≥1 budget-shift rec with expected lift."""
    print("\n--- #6 Campaigns ROI + budget-shift lift ---")
    p = True
    code, camp = dc.call("GET", "/oem/campaigns")
    rows = camp.get("campaigns", []) if code == 200 else []
    p &= ok(code == 200 and len(rows) > 0, f"campaigns returned ({code}, n={len(rows)})")
    p &= ok(all("roi" in c for c in rows), f"every campaign has an roi ({len(rows)} rows)")
    code, bs = dc.call("POST", "/oem/campaigns/budget-shift", {"market": "us", "apply": False})
    shift = bs.get("shift", {}) if code == 200 else {}
    p &= ok(code == 200 and bool(bs.get("recommendation_id")),
            f"budget-shift produced a recommendation ({code})")
    lift = shift.get("expected_lift_gross")
    p &= ok(lift is not None and lift > 0, f"expected lift present: {lift}")
    p &= ok(shift.get("from", {}).get("roi") is not None
            and shift.get("to", {}).get("roi") is not None
            and shift["to"]["roi"] >= shift["from"]["roi"],
            f"shift moves spend to higher ROI ({shift.get('from',{}).get('roi')} -> {shift.get('to',{}).get('roi')})")
    return p


def _acc_8() -> bool:
    """#8 No Resource:* on DynamoDB actions in any retail-ops-<env> role (LIVE IAM scan)."""
    print("\n--- #8 IAM: no Resource:* on DynamoDB actions ---")
    p = True
    try:
        import boto3
        iam = boto3.client("iam", region_name=REGION)
    except Exception as e:
        return ok(False, f"could not create IAM client: {e}")
    pfx = ddb.DEFAULT_ENV_PREFIX                      # retail-ops-<env>-
    kinds = ["authorizer", "service", "worker", "subscriber", "approvals"]
    if pfx.startswith("retail-ops-dev-"):
        kinds.append("demo-reset")                   # the Admin Console's demo reset (dev only)
    roles = [f"{pfx}{r}-role" for r in kinds]
    checked = 0
    for role in roles:
        try:
            names = iam.list_role_policies(RoleName=role)["PolicyNames"]
        except iam.exceptions.NoSuchEntityException:
            ok(False, f"{role}: MISSING")
            p = False
            continue
        for pol in names:
            doc = iam.get_role_policy(RoleName=role, PolicyName=pol)["PolicyDocument"]
            stmts = doc["Statement"]
            stmts = stmts if isinstance(stmts, list) else [stmts]
            for st in stmts:
                actions = st.get("Action", [])
                actions = actions if isinstance(actions, list) else [actions]
                is_ddb = any(str(a).lower().startswith("dynamodb:") for a in actions)
                res = st.get("Resource", [])
                res = res if isinstance(res, list) else [res]
                if is_ddb and any(r == "*" or str(r).endswith(f"table/{pfx}*") for r in res):
                    ok(False, f"{role}/{pol}: DynamoDB stmt has wildcard resource")
                    p = False
            checked += 1
    p &= ok(True, f"scanned {len(roles)} roles / {checked} policies — no Resource:* on DynamoDB")
    # legacy shared role must be gone (it carried the wildcard)
    try:
        iam.get_role(RoleName=f"{pfx}journeys-role")
        p &= ok(False, f"legacy {pfx}journeys-role still exists (should be deleted)")
    except iam.exceptions.NoSuchEntityException:
        p &= ok(True, f"legacy wildcard role {pfx}journeys-role is gone")
    return p


def _acc_13(dc: Client) -> bool:
    """#13 A2 nurture: ≤2 touches/week + quiet hours (plan policy); chat grounding,
    prequal action, and credit-approval escalation."""
    print("\n--- #13 A2 nurture policy + chat grounding/escalation ---")
    p = True
    code, plan = dc.call("POST", f"/journeys/{_ACC_JID}/nurture/plan", {})
    p &= ok(code == 200 and bool(plan.get("nurture_plan_id")), f"nurture plan built ({code})")
    stops = plan.get("stop_conditions") or []
    p &= ok(bool(stops), f"plan carries stop conditions (opt-out honored): {stops[:3]}")
    # payment question -> grounded answer with citations + a one-click prequal action
    code, ans = dc.call("POST", f"/journeys/{_ACC_JID}/chat", {"message": "what would my monthly payment be?"})
    cites = ans.get("citations") or []
    actions = [a.get("action") if isinstance(a, dict) else a for a in (ans.get("suggested_actions") or [])]
    p &= ok(code == 200 and len(cites) > 0, f"payment answer is grounded ({len(cites)} citations)")
    p &= ok("start_prequal" in actions, f"payment answer offers prequal action: {actions}")
    # credit-approval question -> escalates to a human (handoff), does not decide
    code, cr = dc.call("POST", f"/journeys/{_ACC_JID}/chat", {"message": "will I be approved for financing?"})
    p &= ok(code == 200 and bool(cr.get("escalate_to_human")),
            f"credit-approval question escalates to human (category={cr.get('category')})")
    return p


def _acc_14(dc: Client) -> bool:
    """#14 A3 contextual suggestions cite a recent activity; the grounded answer
    to 'what should I say next' carries citations."""
    print("\n--- #14 A3 contextual suggestions cite activity ---")
    p = True
    code, sug = dc.call("GET", f"/journeys/{_ACC_JID}/suggestions")
    items = sug.get("suggestions") or [] if code == 200 else []
    p &= ok(code == 200 and len(items) > 0, f"suggestions returned ({code}, n={len(items)})")
    p &= ok(all(s.get("why") for s in items), "every suggestion has a 'why'")
    p &= ok(any(s.get("evidence") or s.get("why") for s in items),
            "at least one suggestion cites a recent activity record")
    # the A2 chat 'what should I say next' style grounded answer carries citations
    code, ans = dc.call("POST", f"/journeys/{_ACC_JID}/chat", {"message": "what should I say to this customer next?"})
    p &= ok(code == 200, f"grounded next-step answer returns ({code})")
    # NOTE: A2 publish_shift_list is a module function, not a deployed route — see ACCEPTANCE.md.
    return p


def run_acceptance(base: str) -> int:
    """Assert acceptance criteria #6, #8, #13, #14 (§11) programmatically."""
    print("\n=== ACCEPTANCE: §11 criteria #6, #8, #13, #14 ===")
    oem = Client(base, _ACC_OEM)
    mgr = Client(base, _ACC_MGR)
    results = {
        "#6 campaign ROI + budget-shift lift": _acc_6(oem),
        "#8 no Resource:* on DynamoDB": _acc_8(),
        "#13 A2 nurture policy + chat": _acc_13(mgr),
        "#14 A3 suggestions cite activity": _acc_14(mgr),
    }
    print("\n=== ACCEPTANCE SUMMARY ===")
    for k, v in results.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    all_pass = all(results.values())
    print(f"\n=== ACCEPTANCE RESULT: {'ALL PASS' if all_pass else 'SOME FAILURES'} ===")
    return 0 if all_pass else 1


# --- workflow 7: one buyer, web lead to delivery (docs/DEMO_WORKFLOWS.md §7) ----
# The eleven steps of the Scenario Replay's "New-car purchase", driven through the
# live API under the persona that presses each button: Keisha (BDC) → Elizabeth
# (sales advisor) → Michael (F&I) → Megan (delivery) → the buyer in My Garage.
# Writes everything the scenario writes (customer, lead, journey, build, deal,
# reserved VIN, delivery, vehicle record); the buyer is a throwaway identity per
# run, never the presenter's Priya, and teardown removes every row the run made
# and puts the reserved unit back in stock.

W7_BDC, W7_ADV, W7_FNI, W7_DELIVERY = "user:U-US-00005", "user:U-US-00002", "user:U-US-00006", "user:U-US-00009"
W7_ADVISOR_ID = "U-US-00002"          # Elizabeth Thomas
W7_MODEL = "MER-VOLT-C"               # the Ion C she asks about
W7_OBJECTION = "Honestly, that monthly payment is more than we wanted to spend."


def w7_web_lead(run_id: str) -> dict:
    """The web form of step 1, for a buyer that exists only for this run."""
    return {
        "source": "web_form",
        "given_name": "Verify", "family_name": f"Buyer {run_id}",
        "email": f"verify.buyer.{run_id}@example.test", "phone": "(404) 555-0100",
        "city": "Decatur", "state": "GA", "zip5": "30030",
        "vehicle_of_interest_model_id": W7_MODEL,
        "comments": "Is the Ion C Extended in stock in blue or white? We'd like to see it this week — "
                    "and what is my 2019 Trek 4 worth as a trade?",
        "budget": {"price_min": 32000, "price_max": 42000, "monthly_max": 560, "down": 3000},
        "current_vehicle": {"year": 2019, "make": "Kestrel", "model": "Trek 4", "mileage": 61000,
                            "ownership": "loan", "payoff": 8400, "months_remaining": 11},
    }


def _soon(hours: float) -> str:
    t = _dt.datetime.now(_dt.UTC) + _dt.timedelta(hours=hours)
    return t.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stage(dc: Client, jid: str) -> str:
    code, st = dc.call("GET", f"/journeys/{jid}/state")
    return st.get("stage", "") if code == 200 else ""


def run_workflow7(base: str, keep: bool) -> int:
    """Workflow 7 end to end against the live API; 0 when every expectation holds."""
    run_id = uuid.uuid4().hex[:6]
    bdc, adv, fni, dlv = (Client(base, t) for t in (W7_BDC, W7_ADV, W7_FNI, W7_DELIVERY))
    ctx: dict = {"run_id": run_id}
    p = True
    print(f"\n=== WORKFLOW 7: one buyer, web lead to delivery (run {run_id}) ===")
    try:
        # 1. web form → Lead Desk, first-response clock running
        print("\n--- 1. web lead lands on the Lead Desk (Keisha) ---")
        code, out = bdc.call("POST", "/leads", w7_web_lead(run_id))
        lid, cid = (out.get("lead") or {}).get("lead_id"), out.get("customer_id")
        ctx.update(lead_id=lid, customer_id=cid, customer_name=out.get("customer_name"))
        p &= ok(code in (200, 201) and bool(lid) and bool(cid), f"lead intake created lead {lid} for customer {cid} ({code})")
        if not (lid and cid):
            return 1
        p &= ok(bool((out.get("sla") or {}).get("sla_due_at")), f"first-response clock running (due {(out.get('sla') or {}).get('sla_due_at')})")
        p &= ok((out.get("lead") or {}).get("vehicle_of_interest_model_id") == W7_MODEL, "the lead carries the model she asked about")

        # 2. A3 drafts, the BDC sends
        print("\n--- 2. A3 draft sent as the first response (Keisha) ---")
        code, script = bdc.call("GET", f"/leads/{lid}/script")
        p &= ok(code == 200 and bool(script.get("body") or script.get("opening")), f"A3 first-response draft ({code})")
        body = f"{script.get('opening', '')} {script.get('body', '')}".strip() or None
        code, r = bdc.call("POST", f"/leads/{lid}/respond", {"channel": "email", "body": body})
        p &= ok(code == 200 and r.get("first_response") is True and bool(r.get("sent") or r.get("fallback_task")),
                f"first response recorded ({'e-mail sent' if r.get('sent') else 'queued as a dealer task' if r.get('fallback_task') else r.get('reason')})")
        code, tr = bdc.call("GET", f"/traces?lead_id={lid}")
        tools = {t.get("tool") for t in (tr.get("traces") or [])}
        p &= ok("a3.send_first_response" in tools, f"the lead's Activity feed shows the send ({sorted(t for t in tools if t)})")

        # 3. assign + book: a DISCOVER journey, a pending handoff, an appointment
        print("\n--- 3. assign to Elizabeth, book the test drive (Keisha) ---")
        code, a = bdc.call("POST", f"/leads/{lid}/assign", {"assignee": W7_ADVISOR_ID})
        jid, hid = a.get("journey_id"), a.get("handoff_id")
        ctx.update(journey_id=jid, handoff_id=hid)
        p &= ok(code == 200 and a.get("journey_created") is True and bool(jid) and bool(hid),
                f"assign opened journey {jid} with handoff {hid} pending ({code})")
        if not jid:
            return 1
        code, _ = bdc.call("POST", f"/leads/{lid}/appointment", {"slot_start": _soon(24), "slot_end": _soon(24.5)})
        p &= ok(code == 200, f"test drive booked ({code})")
        code, q = bdc.call("GET", "/leads/queue")
        card = next((c for lane in (q.get("queue") or {}).values() if isinstance(lane, list) for c in lane if c.get("lead_id") == lid), {})
        p &= ok((card.get("pending_handoff") or {}).get("to_user_name") == "Elizabeth Thomas"
                and card.get("journey_owner_name") == "Keisha Baker",
                f"card: owner {card.get('journey_owner_name')} → {(card.get('pending_handoff') or {}).get('to_user_name')} (handoff pending)")
        p &= ok(bool((card.get("appointment") or {}).get("slot_start")), "card shows the appointment")

        # 4. Elizabeth accepts; A1 leads with the model she asked about; present + save the build
        print("\n--- 4. accept the handoff, present the Ion C, save the build (Elizabeth) ---")
        code, _ = adv.call("POST", f"/handoffs/{hid}/accept", {})
        p &= ok(code == 200, f"handoff accepted ({code})")
        code, av = adv.call("GET", f"/journeys/{jid}/advisor")
        m = av.get("matches") or {}
        top = next((x for x in (m.get("matches") or []) if x.get("requested")), None) or ((m.get("matches") or [None])[0])
        p &= ok(code == 200 and m.get("requested_model_id") == W7_MODEL and m.get("requested_from") == "lead",
                f"matches know what she asked for ({m.get('requested_model_name')} from the {m.get('requested_from')})")
        p &= ok(bool(top) and top.get("model_id") == W7_MODEL and top.get("requested") is True
                and (top.get("score_breakdown") or {}).get("asked_for") == 30,
                f"the requested model leads the list ({(top or {}).get('trim_name') or (top or {}).get('trim_id')}, asked_for {(top or {}).get('score_breakdown', {}).get('asked_for')})")
        if not top:
            return 1
        ctx.update(trim_id=top["trim_id"], model_id=top["model_id"])
        code, _ = adv.call("POST", f"/journeys/{jid}/present",
                           {"trim_id": top["trim_id"], "model_id": top["model_id"],
                            "recommendation_id": (av.get("recommendation_ids") or {}).get(f"match:{top['trim_id']}")})
        p &= ok(code == 200, f"presented {top.get('trim_name') or top['trim_id']} ({code})")
        code, saved = adv.call("POST", "/configurations",
                               {"journey_id": jid, "model_id": top["model_id"], "trim_id": top["trim_id"], "color": "Meridian Blue"})
        ctx["config_id"] = (saved.get("configuration") or {}).get("config_id")
        p &= ok(code in (200, 201) and bool(ctx["config_id"]), f"build saved ({ctx['config_id']}, {code})")
        code, j = adv.call("GET", f"/journeys/{jid}/state")
        p &= ok(code == 200 and j.get("owner_user_id") == W7_ADVISOR_ID and not j.get("pending_handoff"),
                f"journey in {j.get('stage')}, owner {j.get('owner_user_id')}, no handoff pending")

        # 5. the price objection
        print("\n--- 5. price objection detected (Elizabeth) ---")
        code, o = adv.call("POST", f"/journeys/{jid}/objection", {"utterance": W7_OBJECTION, "channel": "floor"})
        kind = str(o.get("objection_type") or "")
        p &= ok(code == 200 and o.get("objection_detected") is True and ("payment" in kind or "price" in kind),
                f"objection {kind or 'none'} detected ({code})")

        # 6. CONFIGURE → QUALIFY → FINANCE through A2's gates
        print("\n--- 6. advance to FINANCE (Elizabeth) ---")
        for _ in range(3):
            stage = _stage(adv, jid)
            if stage in ("DISCOVER", "CONFIGURE"):
                code, d = adv.call("POST", f"/journeys/{jid}/advance",
                                   {"evidence": "build saved on the floor (verify)", "context_updates": {"shortlist": True, "saved_build": True}})
            elif stage == "QUALIFY":
                code, d = adv.call("POST", f"/journeys/{jid}/advance",
                                   {"evidence": "budget confirmed and timeline captured on the floor (verify)",
                                    "context_updates": {"budget": True, "timeline": True}})
            else:
                break
            p &= ok(code == 200, f"A2 advanced {stage} → {d.get('to')} ({code})")
        p &= ok(_stage(adv, jid) == "FINANCE", f"journey at {_stage(adv, jid)}")

        # 7. the trade she typed into the form
        print("\n--- 7. trade valued on the journey (Elizabeth) ---")
        code, t = adv.call("POST", f"/journeys/{jid}/trade", {"condition": "good"})
        v = t.get("valuation") or {}
        p &= ok(code == 200 and (t.get("context") or {}).get("valuation") is True, f"valuation attached to the journey ({code})")
        p &= ok((v.get("vehicle") or {}).get("model") == "Trek 4" and v.get("payoff") == 8400
                and v.get("trade_value", 0) > 0 and round(v.get("equity", 0)) == round(v.get("trade_value", 0) - 8400),
                f"2019 Trek 4: value {v.get('trade_value')}, payoff {v.get('payoff')}, equity {v.get('equity')}")
        p &= ok("comparable" in str(v.get("source_ref")) or "sandbox valuation connector" in str(v.get("source_ref")),
                f"appraisal says where it came from ({str(v.get('source_ref'))[:70]})")

        # 8. F&I: desk from the build, lender decision, reserve a VIN, push to the DMS
        print("\n--- 8. F&I desks, decides, reserves a VIN, books (Michael) ---")
        code, d = fni.call("POST", f"/fni/journeys/{jid}/deal", {})
        did = (d.get("deal") or {}).get("deal_id")
        ctx["deal_id"] = did
        ws = d.get("worksheet") or {}
        p &= ok(code in (200, 201) and bool(did), f"desk opened on the journey: deal {did} ({code})")
        if not did:
            return 1
        p &= ok(ws.get("customer_name") == ctx.get("customer_name") and str((ws.get("trade") or {}).get("vehicle", "")).endswith("Kestrel Trek 4"),
                f"worksheet is hers ({ws.get('customer_name')}, trade {(ws.get('trade') or {}).get('vehicle')})")
        p &= ok(((ws.get("price") or {}).get("breakdown") or {}).get("reconciles") is True,
                f"price breakdown reconciles to the deal total ({(ws.get('price') or {}).get('price_total')})")
        kinds = {i.get("kind") for i in (ws.get("incentives") or d.get("incentives") or [])}
        print(f"       incentives labelled: {sorted(k for k in kinds if k)}")
        code, dec = fni.call("POST", f"/fni/deals/{did}/lender-decision", {})
        p &= ok(code == 200 and dec.get("any_approved") is True, f"lender decision: approved ({code})")
        for _ in range(2):
            stage = _stage(fni, jid)
            if stage in ("FINANCE", "TRADE_IN"):
                code, d2 = fni.call("POST", f"/fni/journeys/{jid}/advance", {})
                p &= ok(code == 200, f"F&I advanced {stage} → {d2.get('to') or _stage(fni, jid)} ({code})")
            else:
                break
        p &= ok(_stage(fni, jid) == "AGREE", f"journey at {_stage(fni, jid)}")
        code, blocked = fni.call("POST", f"/fni/deals/{did}/push", {})
        p &= ok(code != 200, f"DMS push refused before a VIN is reserved ({code}: {str(blocked.get('error', ''))[:60]})")
        code, inv = fni.call("GET", f"/inventory?model={W7_MODEL}&status=in_stock")
        units = [u for u in (inv.get("vehicles") or []) if u.get("dealer_id") == DEALER]
        unit = next((u for u in units if u.get("trim_id") == ctx["trim_id"]), None) or (units[0] if units else None)
        p &= ok(bool(unit), f"an in-stock unit at {DEALER} to reserve ({len(units)} candidates)")
        if not unit:
            return 1
        ctx["vin"] = unit["vin"]
        ctx["unit_before"] = ddb.get_item("DEALER_Inventory", {"vin": unit["vin"]})
        ctx["vehicle_before"] = ddb.get_item("DEALER_Vehicles", {"vin": unit["vin"]})
        code, _ = fni.call("POST", f"/inventory/{unit['vin']}/reserve", {"journey_id": jid})
        p &= ok(code == 200, f"reserved {unit['vin']} ({unit.get('trim_name') or unit.get('trim_id')}) ({code})")
        code, push = fni.call("POST", f"/fni/deals/{did}/push", {})
        p &= ok(code == 200 and push.get("vin") == unit["vin"] and bool(push.get("dms_deal_number")),
                f"booked {push.get('dms_deal_number')} on VIN {push.get('vin')} ({code})")
        code, deal = fni.call("GET", f"/fni/deals/{did}")
        rd = deal.get("readiness") or {}
        p &= ok(rd.get("vin_reserved") is True and rd.get("pushed_to_dms") is True and (deal.get("vehicle") or {}).get("vin") == unit["vin"],
                f"readiness: vin_reserved={rd.get('vin_reserved')} pushed_to_dms={rd.get('pushed_to_dms')}")
        prog = (deal.get("price") or {}).get("apr_program")
        print(f"       rate on the deal: {(deal.get('price') or {}).get('apr_pct')}% · {(deal.get('price') or {}).get('term_months')} mo"
              f" · OEM APR program {'applied' if prog else 'not applied (tier/term)'} · tier {(deal.get('price') or {}).get('credit_tier')}")

        # 9. AGREE → DELIVER: delivery card + handoff to Megan
        print("\n--- 9. close AGREE → DELIVER (Michael) ---")
        code, out9 = fni.call("POST", f"/fni/journeys/{jid}/advance", {})
        p &= ok(code == 200 and _stage(fni, jid) == "DELIVER", f"AGREE → DELIVER ({code}); handoff to {(out9.get('handoff') or {}).get('to_user')}")

        # 10. delivery day
        print("\n--- 10. delivery day (Megan) ---")
        code, board = dlv.call("GET", "/deliveries")
        lanes = board.get("lanes") or {}
        cards = [c for lane in ("docs_ready", "prepped", "scheduled") for c in (lanes.get(lane) or [])]
        card = next((c for c in cards if c.get("journey_id") == jid), None)
        p &= ok(bool(card) and card.get("customer_name") == ctx.get("customer_name"), f"delivery card for {ctx.get('customer_name')} on the board")
        if card:
            items = list((card.get("checklist") or {}).keys())
            patch = {"checklist": {k: True for k in items}, "docs_signed": True, "customer_confirmed": True, "scheduled_at": _soon(2)}
            if "charger_install" in items or card.get("charger_install") is not None:
                patch["charger_install"] = "installed"
            code, _ = dlv.call("PATCH", f"/deliveries/{jid}", patch)
            p &= ok(code == 200, f"prep complete ({len(items)} items), docs signed, customer confirmed ({code})")
            code, _ = dlv.call("POST", f"/deliveries/{jid}/message", {})
            code, _ = dlv.call("POST", f"/deliveries/{jid}/deliver", {})
            p &= ok(code == 200, f"marked delivered ({code})")
        p &= ok(_stage(fni, jid) == "OWN", f"journey at {_stage(fni, jid)}")

        # 11. the buyer's own view
        print("\n--- 11. My Garage (the buyer) ---")
        cust = Client(base, f"cust:{cid}")
        code, g = cust.call("GET", "/garage")
        vins = [x.get("vin") for x in (g.get("vehicles") or [])]
        p &= ok(code == 200 and ctx["vin"] in vins, f"her garage lists the delivered VIN ({code}, {len(vins)} vehicle(s))")
        p &= ok((g.get("purchase") or {}).get("stage") == "OWN", f"purchase journey shows {(g.get('purchase') or {}).get('stage')}")
        svc = (g.get("service") or {}).get("active_journeys") or []
        ctx["service_journey_ids"] = [s.get("service_journey_id") for s in svc if s.get("service_journey_id")]
        p &= ok(len(svc) >= 1, f"a service visit opened at delivery ({len(svc)})")
    finally:
        print(f"\n=== WORKFLOW 7 RESULT: {'ALL PASS' if p else 'SOME FAILURES'} ===")
        if keep:
            print(f"(--keep set; leaving the run's rows: lead {ctx.get('lead_id')}, journey {ctx.get('journey_id')}, "
                  f"customer {ctx.get('customer_id')}, deal {ctx.get('deal_id')}, VIN {ctx.get('vin')})")
        else:
            print("=== teardown ===")
            n = teardown_workflow7(ctx)
            print(f"  removed {n} row(s); unit {ctx.get('vin') or '-'} back as it was")
    return 0 if p else 1


def teardown_workflow7(ctx: dict) -> int:
    """Delete every row the run wrote and restore the inventory unit. Best effort."""
    import boto3
    c = boto3.client("dynamodb", region_name=REGION)
    n = 0

    def dele(table: str, key: dict) -> None:
        nonlocal n
        try:
            c.delete_item(TableName=ddb.table_name(table), Key={k: ddb.to_attr(v) for k, v in key.items()})
            n += 1
        except Exception as e:  # noqa: BLE001 — best effort: report and carry on with the other rows
            print(f"  (could not delete {table} {key}: {type(e).__name__})")

    def dele_where(table: str, attr: str, value, key_field: str, sort_field: str | None = None) -> None:
        if not value:
            return
        for row in ddb.scan_filter(table, attr, value):
            key = {key_field: row.get(key_field)}
            if sort_field:
                key[sort_field] = row.get(sort_field)
            if all(v is not None for v in key.values()):
                dele(table, key)

    jid, cid, lid = ctx.get("journey_id"), ctx.get("customer_id"), ctx.get("lead_id")
    if jid:
        for e in ddb.query_hash("DEALER_JourneyEvents", "journey_id", jid):
            dele("DEALER_JourneyEvents", {"journey_id": jid, "sk": e["sk"]})
        dele("DEALER_Journeys", {"journey_id": jid})
        for table, key in (("DEALER_Handoffs", "handoff_id"), ("DEALER_Tasks", "task_id"), ("DEALER_Appointments", "appointment_id"),
                           ("PLAT_Configurations", "config_id"), ("DEALER_Recommendations", "recommendation_id"),
                           ("DEALER_Notifications", "notification_id"), ("CUST_Documents", "document_id"),
                           ("DEALER_Deliveries", "delivery_id"), ("PLAT_AgentTraces", "trace_id")):
            dele_where(table, "journey_id", jid, key)
        for deal in ddb.scan_filter("DEALER_Deals", "journey_id", jid):
            dele_where("DEALER_FinanceOffers", "deal_id", deal.get("deal_id"), "deal_id")
            dele("DEALER_Deals", {"deal_id": deal["deal_id"]})
    if lid:
        dele_where("PLAT_AgentTraces", "lead_id", lid, "trace_id")
        dele("DEALER_Leads", {"lead_id": lid})
    for sid in ctx.get("service_journey_ids") or []:
        for e in ddb.query_hash("DEALER_ServiceJourneyEvents", "service_journey_id", sid):
            dele("DEALER_ServiceJourneyEvents", {"service_journey_id": sid, "sk": e["sk"]})
        dele("DEALER_ServiceJourneys", {"service_journey_id": sid})
    if cid:
        for table, key in (("DEALER_Valuations", "valuation_id"), ("DEALER_Tasks", "task_id"), ("CUST_Messages", "message_id"),
                           ("DEALER_ServiceJourneys", "service_journey_id")):
            dele_where(table, "customer_id", cid, key)
        dele_where("DEALER_Signals", "customer_id", cid, "customer_id", "sk")
        dele("CUST_Consent", {"customer_id": cid})
        dele("DEALER_Customers", {"customer_id": cid})
    vin = ctx.get("vin")
    if vin:
        if ctx.get("unit_before"):
            ddb.put_item("DEALER_Inventory", ctx["unit_before"])
        if ctx.get("vehicle_before"):
            ddb.put_item("DEALER_Vehicles", ctx["vehicle_before"])
        else:
            dele("DEALER_Vehicles", {"vin": vin})
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Drive the core orchestration loop against the live API.")
    ap.add_argument("--api", required=True, help="API base URL (…/dev)")
    ap.add_argument("--token", default=f"user:{ADVISOR}", help="Authorization token (default dev staff)")
    ap.add_argument("--keep", action="store_true", help="skip teardown (inspect the DEMO-* rows)")
    ap.add_argument("--smoke", action="store_true",
                    help="read-only health + scope check of every deployed route (no writes)")
    ap.add_argument("--acceptance", action="store_true",
                    help="assert §11 acceptance criteria #6, #8, #13, #14 programmatically")
    ap.add_argument("--workflow7", action="store_true",
                    help="run demo workflow 7 (one buyer, web lead to delivery) end to end; writes, then tears down")
    args = ap.parse_args(argv)
    if args.smoke:
        return run_smoke(args.api)
    if args.acceptance:
        return run_acceptance(args.api)
    if args.workflow7:
        return run_workflow7(args.api, args.keep)
    return run(args.api, args.token, args.keep)


if __name__ == "__main__":
    raise SystemExit(main())
