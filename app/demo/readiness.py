"""Demo readiness — how many more rehearsals each end-to-end scenario has before it runs dry.

Each scenario in app/console/src/lib/replay.ts uses something up when it runs: a fresh buyer
and an in-stock car, an equity-positive owner in the lane, a pending trade disposition, an open
HIGH alert, an unbooked predictive heads-up. This counts what is left on the stack, so a demo
never starts on an empty pool. When a count is low, run deploy/demo-reset.sh.

    python -m app.demo.readiness          # exit 1 when any scenario has no run left
    python -m app.demo.readiness --json

Read-only.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any

from app.services.common import ddb

DEMO_DEALER = "D-101"
DEMO_DISTRICT = "DIST-07"
DEMO_MODEL = "MER-VOLT-C"        # the Ion C the purchase scenario's buyer asks for
# replay.ts DEMO_BUYERS — the purchase scenario's buyers, one per rehearsal
DEMO_BUYERS = ("priya natarajan", "daniel okafor", "hannah lindqvist", "marcus bell", "sofia restrepo",
               "ethan park", "amara diallo", "lucas moreau", "grace holloway", "noah castellano")
SERVICE_OWNERS = tuple(f"C-US-DS{n:02d}" for n in range(1, 11))   # demo_pack.py service-visit owners
UNLIMITED = 99
_OPEN_VISIT = ("CHECK_IN", "INSPECT", "DIAGNOSE", "APPROVE", "REPAIR", "READY")


@dataclass
class Scenario:
    scenario_id: str
    name: str
    runs_left: int
    uses: str
    detail: str


def _lane_candidates(env_prefix: str | None) -> list[dict[str, Any]]:
    """Equity-positive owners in the demo store's lane who are not already with sales — the
    lane read model itself, so this counts exactly what the scenario sees."""
    from app.services.api import handler
    ev = {"httpMethod": "GET", "path": "/service/lane", "headers": {}, "queryStringParameters": None,
          "requestContext": {"authorizer": {"user_id": "U-US-00002", "role": "dealer_advisor",
                                            "dealer_ids": DEMO_DEALER, "is_oem": "false", "market_id": "us",
                                            "customer_id": "", "scope_node_id": DEMO_DEALER}}}
    res = handler.lambda_handler(ev, None)
    if res.get("statusCode") != 200:
        raise RuntimeError(f"lane read failed ({res.get('statusCode')})")
    items = json.loads(res["body"]).get("items") or []
    return [i for i in items if any(f.get("flag") == "positive_equity" for f in i.get("flags") or [])
            and not i.get("sales_journeys")]


def report(env_prefix: str | None = None) -> list[Scenario]:
    out: list[Scenario] = []
    with ddb.read_memo():
        stock = [u for u in ddb.scan_all("DEALER_Inventory", env_prefix)
                 if u.get("dealer_id") == DEMO_DEALER and u.get("status") == "in_stock"]
        ion_c = sum(1 for u in stock if u.get("model_id") == DEMO_MODEL)
        custs = {c["customer_id"]: c for c in ddb.scan_filter("DEALER_Customers", "market_id", "us", env_prefix)}
        # a demo buyer is used once she is on the Lead Desk (the scenario's first step puts her there)
        on_desk = {f"{c.get('given_name')} {c.get('family_name')}".lower()
                   for lead in ddb.scan_filter("DEALER_Leads", "market_id", "us", env_prefix)
                   if (c := custs.get(lead.get("customer_id")))}
        buyers = sum(1 for b in DEMO_BUYERS if b not in on_desk)
        out.append(Scenario("ev-purchase", "New-car purchase — web lead to delivery", min(buyers, ion_c),
                            "a new demo buyer + one in-stock Ion C",
                            f"{buyers} of {len(DEMO_BUYERS)} demo buyers unused; {ion_c} Ion C in stock at {DEMO_DEALER}"))

        visits = ddb.scan_filter("DEALER_ServiceJourneys", "market_id", "us", env_prefix)
        busy = {s.get("customer_id") for s in visits if s.get("stage") in ("BOOK", *_OPEN_VISIT)}
        free = [cid for cid in SERVICE_OWNERS if cid in custs and cid not in busy]
        out.append(Scenario("service-visit", "Service visit — booking to pickup", len(free),
                            "one owner whose car is due service and not in the bay",
                            f"{len(free)} of {len(SERVICE_OWNERS)} service-visit owners free"))

        lane = _lane_candidates(env_prefix)
        out.append(Scenario("equity-upgrade", "Service lane to showroom — repurchase handoff", len(lane),
                            "one equity-positive owner not yet with sales",
                            ", ".join(i.get("customer_name") or i["service_journey_id"] for i in lane[:6])
                            + (" …" if len(lane) > 6 else "")))

        from app.services.dealer import inventory
        disp = inventory.trade_dispositions({"market_id": "us", "dealer_ids": {DEMO_DEALER}, "is_oem": False},
                                            "us", env_prefix=env_prefix)["pending"]
        out.append(Scenario("inventory-morning", "Inventory manager — aging vs demand, dispositions",
                            min(disp, len(stock)), "one pending trade disposition + one in-stock VIN",
                            f"{disp} pending dispositions; {len(stock)} units in stock at {DEMO_DEALER}"))

        high = [a for a in ddb.scan_filter("OEM_Alerts", "market_id", "us", env_prefix)
                if a.get("status") == "open" and str(a.get("severity")).lower() == "high"
                and a.get("district_id") == DEMO_DISTRICT]
        out.append(Scenario("dm-monday", "District manager — the Monday cycle", len(high),
                            "one open HIGH alert (acknowledged)", f"{len(high)} open HIGH alerts in {DEMO_DISTRICT}"))

        from app.services.service import predictive
        leads = [lead for lead in ddb.scan_filter("DEALER_Leads", "market_id", "us", env_prefix)
                 if lead.get("source") == "predictive_maintenance" and lead.get("dealer_id") == DEMO_DEALER
                 and lead.get("status") in ("new", "working")]
        unbooked = sum(1 for lead in leads if not lead.get("service_appointment"))
        due = predictive.predictions({DEMO_DEALER}, "us", env_prefix)["predictions"]
        not_raised = len({p["vin"] for p in due if p.get("customer_id") and not p.get("lead_open") and not p.get("in_bay")})
        out.append(Scenario("predictive-offer", "Predictive maintenance — a heads-up becomes an offer",
                            unbooked + not_raised, "one unbooked heads-up (the scan raises new ones)",
                            f"{unbooked} open unbooked heads-ups; {not_raised} more cars the scan would raise"))

        out.append(Scenario("service-cockpit", "Service manager — cockpit, what-if, goodwill", UNLIMITED,
                            "nothing lasting (re-uses its approval)", "always ready"))
        out.append(Scenario("roi-chain", "Marketing studio — ROI to approved budget shift", UNLIMITED,
                            "nothing lasting (re-submits its approval)", "always ready"))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rows = report()
    dry = [s for s in rows if s.runs_left <= 0]
    if args.json:
        print(json.dumps([s.__dict__ for s in rows], indent=1))
    else:
        print("=== DEMO READINESS: rehearsals left per scenario ===")
        for s in rows:
            left = "∞" if s.runs_left >= UNLIMITED else str(s.runs_left)
            print(f"  {'OK ' if s.runs_left > 0 else 'DRY'}  {s.scenario_id:18s} {left:>3s} left  — {s.detail}")
        low = [s for s in rows if 0 < s.runs_left < 3]
        print(f"=== DEMO READINESS RESULT: {'READY' if not dry else f'{len(dry)} SCENARIO(S) DRY'}"
              + (f"; low: {', '.join(s.scenario_id for s in low)}" if low else "")
              + ("" if not (dry or low) else " — run deploy/demo-reset.sh") + " ===")
    return 1 if dry else 0


if __name__ == "__main__":
    sys.exit(main())
