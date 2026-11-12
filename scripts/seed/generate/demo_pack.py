"""Demo pack — ready subjects for the scenarios that use one up on every run.

The end-to-end demo scenarios (app/console/src/lib/replay.ts) each consume something: the
repurchase handoff hands one equity-positive owner in today's lane to sales, the district
Monday acknowledges one HIGH alert. The bulk data held two and four of them, so the third
rehearsal of a day found nothing to show. This phase adds about ten of each, at levels a real
store and district would have:

  * PACK_SIZE repurchase owners at the demo store: a customer whose own sales advisor is the
    demo floor advisor, an owned car of a catalog model, a guidebook valuation with positive
    equity, the purchase journey that sold it (resting in OWN), and an open service visit in
    today's lane — the exact facts the lane's equity flag and the handoff read.
  * up to PACK_SIZE more open HIGH district alerts, one per store + condition that has none open
    (the monitor keeps one card per store and condition; see oem.py).

Determinism: its own RNG (`{seed}:{market}:demo-pack`) and fixed `DP` ids, and it runs last
(band 99, after platform_late), so no earlier row, id counter or RNG draw moves.
Reset to it with deploy/demo-reset.sh (app/demo/reset.py) before a demo day.
"""
from __future__ import annotations

import copy
import random
from typing import TYPE_CHECKING

from . import phase_registry as _pr
from .config import iso
from .vins import synthetic_vin

if TYPE_CHECKING:  # pragma: no cover
    from .core import G

PACK_SIZE = 10
DEMO_DEALER_IDX = 0                 # the demo store: D-101 (us), D-201 (de)
DEMO_ADVISOR = {"us": "U-US-00002"}  # Elizabeth Thomas — the demo floor advisor
SRC = "synthetic:demo_pack (scenario-ready subject)"


def _short(brand: str, name: str) -> tuple[str, str]:
    """'Meridian Motors' + 'Meridian Ion C' -> ('Meridian', 'Ion C'), as common/vehicles does."""
    short = brand.split()[0] if brand else ""
    if short and name.startswith(short + " "):
        return short, name[len(short) + 1:]
    return short or brand, name


def gen_demo_pack(g: G) -> None:
    m, cfg, mid = g.m, g.cfg, g.m.id
    M = mid.upper()
    rng = random.Random(f"{cfg.seed}:{mid}:demo-pack")
    dealer = m.dealers[DEMO_DEALER_IDX]
    staff = g.ix["users_by_dealer"].get(dealer.code, {})
    advisor = DEMO_ADVISOR.get(mid) if DEMO_ADVISOR.get(mid) in (staff.get("dealer_advisor") or []) \
        else (staff.get("dealer_advisor") or [None])[0]
    service_advisor = (staff.get("dealer_service") or [None])[0]
    template = next(c for c in g.t["DEALER_Customers"] if not c.get("golden_story") and c.get("dealer_id") == dealer.code)
    models = {r["model_id"]: r for r in g.t.get("OEM_CatalogModels", [])}
    # real trims only: the catalog also lists option packages (…-P1, no trim name or powertrain)
    trims = [t for t in g.t.get("OEM_Catalog", [])
             if t.get("model_id") in models and t.get("list_price") and t.get("trim_name") and t.get("powertrain")]
    trims.sort(key=lambda t: t["trim_id"])
    currency = "EUR" if mid == "de" else "USD"
    unit = "km" if mid == "de" else "mi"
    used_names = {(c.get("given_name"), c.get("family_name")) for c in g.t["DEALER_Customers"]}

    for n in range(1, PACK_SIZE + 1):
        tag = f"DP{n:02d}"
        cid, vin, jid, sjid = f"C-{M}-{tag}", synthetic_vin(f"VIN-{M}-{tag}", mid), f"J-{M}-{tag}", f"SJ-{M}-{tag}"
        while True:
            fn, ln = rng.choice(m.first_names), rng.choice(m.last_names)
            if (fn, ln) not in used_names:
                used_names.add((fn, ln))
                break
        trim = trims[rng.randrange(len(trims))]
        model = models[trim["model_id"]]
        make, model_name = _short(str(model.get("brand") or ""), str(model.get("name") or ""))
        year = cfg.now.year - rng.randint(2, 4)
        odo = rng.randint(38, 62) * 1000 if mid == "us" else rng.randint(60, 98) * 1000
        lease = rng.random() < 0.4
        months_left = rng.randint(2, 6) if lease else rng.randint(10, 30)
        # whole currency units, as every seeded valuation (equity is exactly value − payoff)
        trade_value = int(round(float(trim["list_price"]) * rng.uniform(0.52, 0.66), -2))
        payoff = int(round(trade_value - rng.uniform(1800, 7200), -1))
        delivered = cfg.ago(days=365 * (cfg.now.year - year) + rng.randint(0, 200))
        addr = m.address(rng, dealer.city, dealer.region)

        cust = copy.deepcopy(template)
        cust.update({
            "customer_id": cid, "given_name": fn, "family_name": ln,
            "email": m.email(fn, ln, rng), "phone": m.phone(rng), "address": addr,
            "lat": round(dealer.lat + rng.uniform(-.1, .1), 4), "lon": round(dealer.lon + rng.uniform(-.1, .1), 4),
            "dealer_id": dealer.code, "assigned_advisor_user_id": advisor, "golden_story": None,
            "current_vehicle": {"make": make, "model": model_name, "year": year, "mileage": odo, "distance_unit": unit,
                                "ownership": "lease" if lease else "loan", "payoff": payoff,
                                "months_remaining": months_left},
            "created_at": iso(delivered), "notes": [], "source_ref": SRC,
        })
        g.add("DEALER_Customers", cust)
        g.add("CUST_Consent", {"customer_id": cid, "email_opt_in": True, "sms_opt_in": True, "sms_opt_out_at": None,
                               "voice_opt_in": False, "quiet_hours": {"start": "20:00", "end": "08:00", "tz": m.tz},
                               "preferred_channel": rng.choice(["email", "sms"]), "market_id": mid,
                               "updated_at": iso(cfg.ago(days=rng.randint(10, 200)))})
        g.add("DEALER_Vehicles", {"vin": vin, "customer_id": cid, "dealer_id": dealer.code, "market_id": mid,
                                  "model_id": trim["model_id"], "trim_id": trim["trim_id"], "year": year,
                                  "odometer": odo, "ownership": "lease" if lease else "loan",
                                  "delivered_at": iso(delivered), "source_ref": SRC})
        g.add("DEALER_Valuations", {"valuation_id": f"VAL-{M}-{tag}", "customer_id": cid, "dealer_id": dealer.code,
                                    "make": make, "model": model_name, "year": year, "mileage": odo,
                                    "trade_value": trade_value, "payoff": payoff,
                                    "equity": trade_value - payoff, "currency": currency,
                                    "valued_at": iso(cfg.ago(days=rng.randint(5, 40))),
                                    "source_ref": "synthetic:valuation (guidebook-anchored); " + SRC})
        g.add("DEALER_Journeys", {"journey_id": jid, "customer_id": cid, "dealer_id": dealer.code, "market_id": mid,
                                  "stage": "OWN", "previous_stage": "DELIVER", "status": "active",
                                  "source": "web_form", "owner_user_id": advisor,
                                  "primary_model_id": trim["model_id"], "primary_trim_id": trim["trim_id"],
                                  "context": {"delivered": True}, "created_at": iso(delivered),
                                  "updated_at": iso(delivered), "last_event_at": iso(delivered),
                                  "source_ref": SRC})
        g.add("DEALER_ServiceJourneys", {"service_journey_id": sjid, "customer_id": cid, "vin": vin,
                                         "dealer_id": dealer.code, "market_id": mid,
                                         "stage": rng.choice(["CHECK_IN", "INSPECT", "INSPECT"]),
                                         "advisor_id": service_advisor, "technician_id": None,
                                         "purchase_journey_id": jid, "parts_hold": False,
                                         "wait_type": rng.choice(["wait", "drop", "loaner"]),
                                         "opened_at": iso(cfg.ago(hours=rng.uniform(0.5, 4))),
                                         "promised_at": iso(cfg.ahead(hours=rng.uniform(3, 8))),
                                         "services": ["Multi-point inspection", rng.choice(["Oil & filter", "Tire rotation",
                                                                                             "Brake inspection"])],
                                         "golden_story": None, "source_ref": SRC})

    # HIGH alerts: one per store + condition that has none open, up to PACK_SIZE
    from .oem import _ALERT_KINDS
    first = (g.t.get("OEM_Alerts") or [{}])[0]
    district = first.get("district_id")
    # the district health monitor's own provenance (spec reference), marked as the demo pack's
    alert_src = f"{first.get('source_ref') or 'spec:§3.5 district health monitor'}; {SRC}"
    open_keys = {(a["dealer_id"], a["kind"]) for a in g.t.get("OEM_Alerts", []) if a.get("status") == "open"}
    combos = [(d.code, kind, title) for d in m.dealers for kind, title, _c in _ALERT_KINDS
              if (d.code, kind) not in open_keys]
    rng.shuffle(combos)
    for n, (code, kind, title) in enumerate(sorted(combos[:PACK_SIZE]), start=1):
        g.add("OEM_Alerts", {"alert_id": f"AL-{M}-DP{n:02d}", "dealer_id": code, "market_id": mid,
                             "district_id": district, "kind": kind, "title": title, "severity": "high",
                             "status": "open", "opened_at": iso(cfg.ago(days=rng.randint(1, 10))),
                             "resolved_at": None, "source_ref": alert_src})

    # Service-visit owners (DS01..): an owner whose car is due its scheduled service and is NOT
    # in the bay, so the service scenario books, checks in, inspects and hands back a car without
    # stacking a second open visit on one that is already there (Jordan's golden S1 car is).
    for n in range(1, PACK_SIZE + 1):
        tag = f"DS{n:02d}"
        cid, vin, jid = f"C-{M}-{tag}", synthetic_vin(f"VIN-{M}-{tag}", mid), f"J-{M}-{tag}"
        while True:
            fn, ln = rng.choice(m.first_names), rng.choice(m.last_names)
            if (fn, ln) not in used_names:
                used_names.add((fn, ln))
                break
        trim = trims[rng.randrange(len(trims))]
        year = cfg.now.year - rng.randint(2, 3)
        odo = rng.randint(37, 41) * 1000 if mid == "us" else rng.randint(57, 62) * 1000   # at the 40k mi / 60k km service
        delivered = cfg.ago(days=365 * (cfg.now.year - year) + rng.randint(0, 120))
        cust = copy.deepcopy(template)
        cust.update({
            "customer_id": cid, "given_name": fn, "family_name": ln,
            "email": m.email(fn, ln, rng), "phone": m.phone(rng), "address": m.address(rng, dealer.city, dealer.region),
            "lat": round(dealer.lat + rng.uniform(-.1, .1), 4), "lon": round(dealer.lon + rng.uniform(-.1, .1), 4),
            "dealer_id": dealer.code, "assigned_advisor_user_id": advisor, "golden_story": None,
            "current_vehicle": None, "created_at": iso(delivered), "notes": [], "source_ref": SRC,
        })
        g.add("DEALER_Customers", cust)
        g.add("CUST_Consent", {"customer_id": cid, "email_opt_in": True, "sms_opt_in": True, "sms_opt_out_at": None,
                               "voice_opt_in": False, "quiet_hours": {"start": "20:00", "end": "08:00", "tz": m.tz},
                               "preferred_channel": "email", "market_id": mid,
                               "updated_at": iso(cfg.ago(days=rng.randint(10, 200)))})
        g.add("DEALER_Vehicles", {"vin": vin, "customer_id": cid, "dealer_id": dealer.code, "market_id": mid,
                                  "model_id": trim["model_id"], "trim_id": trim["trim_id"], "year": year,
                                  "odometer": odo, "ownership": "owned", "delivered_at": iso(delivered),
                                  "source_ref": SRC})
        g.add("DEALER_Journeys", {"journey_id": jid, "customer_id": cid, "dealer_id": dealer.code, "market_id": mid,
                                  "stage": "OWN", "previous_stage": "DELIVER", "status": "active",
                                  "source": "walk_in", "owner_user_id": advisor,
                                  "primary_model_id": trim["model_id"], "primary_trim_id": trim["trim_id"],
                                  "context": {"delivered": True}, "created_at": iso(delivered),
                                  "updated_at": iso(delivered), "last_event_at": iso(delivered),
                                  "source_ref": SRC})


_pr.register(_pr.BAND_PLATFORM_LATE + 9, gen_demo_pack)   # 99: last — nothing after it draws
