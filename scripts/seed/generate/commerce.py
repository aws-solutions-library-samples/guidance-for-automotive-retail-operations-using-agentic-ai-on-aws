"""Commerce component (U3): DEALER_* and CUST_* tables the sales spine doesn't
already emit.

`core.py` `G` already generates the sales-spine DEALER_/CUST_ tables (Inventory,
Customers, Signals, Leads, LeadSLA, Journeys, JourneyEvents, Handoffs,
Appointments, Consent). `gen_commerce(g)` adds the rest, in dependency order:

  sales close   — Deals, FinanceOffers, FIProducts, Deliveries, SalesPackages, Touchpoints
  trade / F&I   — Valuations, TradeDispositions, Coverage
  service (P2)  — ServiceJourneys (PK service_journey_id), ServiceJourneyEvents,
                  RepairOrders, ServiceEstimates, DeclinedWork, Inspections,
                  InspectionTemplates, Parts, PartsOrders, LaborOps, DTCReference
  agentic/floor — Recommendations, Outcomes, Approvals, Tasks, Notifications,
                  NurturePlans, ObjectionStrategies, Plays, PlayRuns (the ten
                  DEALER_ tables U2 deferred, BR5.6)
  customer      — CUST_Messages, CUST_Documents

Every table emitted here is DEALER_/CUST_ per OWNER_MAP (BR-U3.1). All randomness
is from g.rng (NFR1); dates are SEED_NOW offsets via g.cfg.ago/ahead (NFR6);
monetary/rate values carry source_ref (NFR4); two markets via g.m (NFR7).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from . import anchors as A
from .config import STAGES, iso
from .owner_map import owner_matches
from .storylines import JOURNEYS, OBJECTION_TYPES
from .vins import synthetic_vin

if TYPE_CHECKING:  # pragma: no cover
    from .core import G

FINANCE_STAGES = ("FINANCE", "TRADE_IN", "AGREE", "DELIVER", "OWN")  # a deal exists from FINANCE on
DELIVERED_STAGES = ("DELIVER", "OWN")

# Service journey stages (spec §2.1.1) and their SEED_NOW distribution (plan §3.10).
SERVICE_STAGES = ("BOOK", "CHECK_IN", "INSPECT", "DIAGNOSE", "APPROVE", "REPAIR", "READY", "FOLLOW_UP", "COMPLETED")

# 17 objection strategies (plan §3.9) — sales + the 4 service types.
OBJECTION_STRATEGY_TYPES = tuple(OBJECTION_TYPES) + ("repair_cost", "can_it_wait", "second_opinion", "warranty_dispute")

# Golden service storylines S1–S8 (plan §3.10 service storylines).
SERVICE_GOLDEN = ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8")

# Bodies for inbound (customer-authored) messages. Market-neutral: no currency,
# no distance units, so one list serves every market (§2.5).
_CUSTOMER_MESSAGES = [
    "Is the one we looked at still available?",
    "What would my monthly payment be on the build I saved?",
    "Can I bring my current car in for an appraisal this weekend?",
    "Do I qualify for any of the incentives you mentioned?",
    "Could we move the test drive to Saturday morning?",
    "How long does delivery usually take once I sign?",
]
_NURTURE_BODY = "Rendered from a nurture template with customer facts."


def _message_body(direction: str, message_id: str) -> str:
    """Body matching the direction, keyed off the id so no RNG draw is spent."""
    if direction != "inbound":
        return _NURTURE_BODY
    h = int(hashlib.sha1(message_id.encode()).hexdigest()[:8], 16)
    return _CUSTOMER_MESSAGES[h % len(_CUSTOMER_MESSAGES)]

LABOR_OPS = [
    ("LOP-OIL", "Oil & filter change", 0.5), ("LOP-BRAKE-F", "Front brake pads & rotors", 1.8),
    ("LOP-BRAKE-R", "Rear brake pads & rotors", 1.6), ("LOP-TIRE-ROT", "Tire rotation", 0.4),
    ("LOP-TIRE-REPL", "Tire replacement (mount & balance, per pair)", 0.9),
    ("LOP-BATT", "12V battery replacement", 0.6), ("LOP-CABIN-FILTER", "Cabin air filter", 0.3),
    ("LOP-ALIGN", "Wheel alignment", 1.0), ("LOP-COOLANT", "Coolant flush", 1.1),
    ("LOP-EV-HV-INSPECT", "HV battery health inspection", 0.8), ("LOP-EV-CHARGE-PORT", "Charge-port service", 1.2),
    ("LOP-TRANS", "Transmission service", 1.5), ("LOP-DIAG", "Diagnostic scan", 1.0),
]
# MPI items: the unit each one is measured in and the thresholds that make a reading
# green / yellow / red (OEM inspection-sheet conventions; `ev` restricts an item to EV or
# non-EV vehicles, None = both). `lo`/`hi` is the reading range the generator draws from.
MPI_ITEMS = [
    {"item": "Front brakes", "unit": "mm", "lo": 2.0, "hi": 11.0, "yellow": 5.0, "red": 3.0, "lower_is_worse": True, "lop": "LOP-BRAKE-F",
     "part": ("Front brake pad & rotor kit", 230.0, 1)},
    {"item": "Rear brakes", "unit": "mm", "lo": 2.0, "hi": 11.0, "yellow": 5.0, "red": 3.0, "lower_is_worse": True, "lop": "LOP-BRAKE-R",
     "part": ("Rear brake pad & rotor kit", 210.0, 1)},
    {"item": "Tires", "unit": "32nds", "lo": 2.0, "hi": 10.0, "yellow": 5.0, "red": 3.0, "lower_is_worse": True, "lop": "LOP-TIRE-REPL",
     "part": ("Tire (OE size)", 185.0, 2)},
    {"item": "12V battery", "unit": "V", "lo": 11.6, "hi": 12.8, "yellow": 12.2, "red": 11.9, "lower_is_worse": True, "lop": "LOP-BATT",
     "part": ("12V battery", 189.0, 1), "ev": False},
    {"item": "Coolant", "unit": "% protection", "lo": 30, "hi": 60, "yellow": 45, "red": 35, "lower_is_worse": True, "lop": "LOP-COOLANT",
     "part": ("Coolant (OEM spec)", 48.0, 1), "ev": False},
    {"item": "Engine oil", "unit": "% life", "lo": 5, "hi": 95, "yellow": 25, "red": 10, "lower_is_worse": True, "lop": "LOP-OIL",
     "part": ("Oil & filter kit", 45.0, 1), "ev": False},
    {"item": "Cabin filter", "unit": "condition", "lo": 1, "hi": 5, "yellow": 3, "red": 2, "lower_is_worse": True, "lop": "LOP-CABIN-FILTER",
     "part": ("Cabin air filter", 42.0, 1)},
    {"item": "Wipers", "unit": "condition", "lo": 1, "hi": 5, "yellow": 3, "red": 2, "lower_is_worse": True, "lop": "LOP-DIAG",
     "part": ("Wiper blade pair", 38.0, 1)},
    {"item": "HV battery SoH", "unit": "%", "lo": 84, "hi": 100, "yellow": 90, "red": 86, "lower_is_worse": True, "lop": "LOP-EV-HV-INSPECT",
     "part": None, "ev": True},
    {"item": "Charge port", "unit": "condition", "lo": 1, "hi": 5, "yellow": 3, "red": 2, "lower_is_worse": True, "lop": "LOP-EV-CHARGE-PORT",
     "part": ("Charge-port seal kit", 65.0, 1), "ev": True},
]
_CONDITION_WORDS = {1: "worn", 2: "worn", 3: "fair", 4: "good", 5: "good"}


def _mpi_reading(rng, spec: dict, force: str | None = None) -> tuple[str, float | str]:
    """Draw a reading in the item's unit; the status follows from the thresholds (mostly green)."""
    roll = {"green": 0.0, "yellow": 0.8, "red": 0.99}[force] if force else rng.random()
    if roll < 0.76:        # green
        lo = spec["yellow"] + (spec["hi"] - spec["yellow"]) * 0.05
        val = rng.uniform(lo, spec["hi"])
    elif roll < 0.93:      # yellow
        val = rng.uniform(spec["red"], spec["yellow"])
    else:                  # red
        val = rng.uniform(spec["lo"], spec["red"])
    if spec["unit"] == "condition":
        val = int(round(val))
        status = "red" if val <= spec["red"] else "yellow" if val <= spec["yellow"] else "green"
        return status, _CONDITION_WORDS[max(1, min(5, val))]
    val = round(val, 1)
    status = "red" if val <= spec["red"] else "yellow" if val <= spec["yellow"] else "green"
    return status, val


# Curated advisor lines per objection type (sales-floor coaching convention: acknowledge,
# reframe on something concrete, offer a next step; no prices, rates or approvals promised).
OBJECTION_PHRASING = {
    "us": {
        "price_too_high": "I hear you — let's look at what's in that number. With the incentives you qualify for and the trade applied, the figure that matters is the monthly, so let me show you two ways to structure it.",
        "payment_too_high": "Understood. Payment is a mix of term, rate and what we put down — let me run a 60- and 72-month version side by side so you can see where it lands before we talk about anything else.",
        "range_anxiety": "That's the most common question we get. Let's start from how you actually drive: for your daily miles a full charge covers well over a week, and I'll show you the DC fast-chargers on your usual routes.",
        "charging_access": "Fair point, and it's worth sorting before you buy. Most owners charge at home overnight on a Level 2 unit — there's a rebate for the install — and I can map public chargers near your home and work right now.",
        "resale_value": "Good question. Residuals on this model have held up better than the segment, and the battery warranty transfers to the next owner, which is what keeps resale strong — I can pull the current used values for you.",
        "incentive_confusion": "There are a lot of them, so let me simplify: here's which ones apply to you today, which depend on how you finance, and which have expired. We'll only build the deal on the ones that are live.",
        "interest_rate": "Rates have moved, so that's a real concern. The captive-finance special on this model is the lever here — let me check what term it applies to and what a shorter loan does to the total interest.",
        "trade_value_low": "I understand — your car is worth more to you than a number on a screen. The valuation is anchored on recent comps; let me walk you through them and show how the equity, positive or negative, rolls into the deal.",
        "timing_not_ready": "No pressure at all. What I can do is hold the build and the incentive snapshot so you don't lose either, and send you a reminder when anything that affects your price changes.",
        "competitor_better": "They make a good car. Rather than argue the spec sheet, let's compare the two on what you told me matters — cargo space, charging speed and five-year cost — and you decide.",
        "trust_reliability": "That's fair; it's a big purchase. This model carries an 8-year battery warranty and I can show you the warranty-claim rate and owner survey scores, not just our brochure.",
        "feature_missing": "Let me check that against the trims — it may be available one level up or as an accessory. If it isn't, I'd rather tell you now than have you find out after delivery.",
        "tariff_price_increase": "Pricing did move with the tariff changes. The in-stock units were built before the increase, so today's price on this VIN is the earlier one — that's the honest reason to decide on this car rather than wait.",
        "repair_cost": "I know that's more than you expected. Let me separate what's a safety item from what can wait, and show you the estimate line by line so you decide what we do today.",
        "can_it_wait": "Some of it can. The brake measurement is the one I'd do now; the rest I'll put on a 60-day reminder with today's price so nothing sneaks up on you.",
        "second_opinion": "Absolutely — take the inspection photos and measurements with you. If another shop quotes it, bring it back and I'll match the scope line for line.",
        "warranty_dispute": "Let me check the coverage rule rather than guess. Here's the warranty term and the in-service date on your VIN; if it's covered, it's covered, and I'll show you the rule we applied.",
    },
    "de": {
        "price_too_high": "Verstehe ich. Schauen wir, was in der Zahl steckt: mit den Prämien, die Sie bekommen, und der Inzahlungnahme zählt die Monatsrate — ich zeige Ihnen zwei Varianten.",
        "payment_too_high": "Verstanden. Die Rate hängt von Laufzeit, Zins und Anzahlung ab — ich rechne 48 und 60 Monate nebeneinander, dann sehen Sie, wo sie landet.",
        "range_anxiety": "Die häufigste Frage. Gehen wir von Ihrem Alltag aus: bei Ihren Tageskilometern reicht eine Ladung deutlich über eine Woche, und ich zeige Ihnen die Schnelllader auf Ihren Strecken.",
        "charging_access": "Berechtigter Punkt. Die meisten laden nachts an einer Wallbox — dafür gibt es Förderung — und ich zeige Ihnen jetzt die öffentlichen Ladepunkte bei Wohnort und Arbeit.",
        "trade_value_low": "Ich verstehe — Ihr Auto ist Ihnen mehr wert als eine Zahl. Die Bewertung stützt sich auf aktuelle Vergleichsangebote; ich gehe sie mit Ihnen durch und zeige, wie der Restwert in die Finanzierung einfließt.",
        "timing_not_ready": "Kein Druck. Ich halte Konfiguration und Prämienstand für Sie fest und melde mich, sobald sich etwas am Preis ändert.",
        "competitor_better": "Ein gutes Auto. Statt Datenblätter zu vergleichen, vergleichen wir, was Ihnen wichtig ist — Kofferraum, Ladeleistung und Fünfjahreskosten — und Sie entscheiden.",
        "repair_cost": "Mehr als erwartet, ich weiß. Ich trenne Sicherheitspunkte von dem, was warten kann, und zeige Ihnen den Kostenvoranschlag Zeile für Zeile.",
        "can_it_wait": "Teilweise ja. Die Bremsen würde ich heute machen; den Rest setze ich mit dem heutigen Preis auf eine 60-Tage-Erinnerung.",
    },
}

_MPI_BY_ITEM = {s["item"]: s for s in MPI_ITEMS}
# conquest nameplates that are battery-electric (the brand's own models carry `powertrains`)
_CONQUEST_EV = {A.DISPLAY_MODEL[k] for k in (
    "Model 3", "Model Y", "EV6", "IONIQ 5", "IONIQ 6", "Kona Electric", "Mustang Mach-E", "Equinox EV",
    "ID.4", "ID.3", "ID.7", "i4", "Born", "Enyaq", "E-208", "5 E-Tech")}
def _is_ev(g: "G", cv: dict) -> bool:
    if cv.get("model") in _CONQUEST_EV:
        return True
    for mod in g.ix["models"].values():
        if mod["name"] == cv.get("model") or mod["model_id"] == cv.get("model"):
            return list(mod.get("powertrains") or []) == ["EV"]
    return False
_LOP_HOURS = {}


def _estimate_line(finding: dict, cur: str, market_id: str) -> dict:
    """One priced estimate line for a red/yellow finding — same fields A7's build_estimate writes."""
    if not _LOP_HOURS:
        _LOP_HOURS.update({lop: hrs for lop, _, hrs in LABOR_OPS})
    spec = _MPI_BY_ITEM[finding["item"]]
    rate = 165 if market_id == "us" else 145
    hours = _LOP_HOURS.get(spec["lop"], 1.0)
    labor = round(hours * rate, 2)
    parts = []
    if spec["part"]:
        desc, price, qty = spec["part"]
        parts.append({"description": desc, "qty": qty, "unit_price": price, "amount": round(price * qty, 2),
                      "source_ref": f"parts_catalog:{spec['lop']}"})
    parts_amt = round(sum(p["amount"] for p in parts), 2)
    total = round(labor + parts_amt, 2)
    wear = any(w in finding["item"].lower() for w in ("brake", "tire", "wiper", "filter", "oil"))
    return {
        "line_id": f"LN-{finding['inspection_id']}", "finding_ref": finding["inspection_id"],
        "item": finding["item"], "finding_status": finding["status"], "urgency": finding["urgency"],
        "measurement": finding["measurement"], "unit": finding["unit"], "evidence_doc_id": finding["evidence_doc_id"],
        "labor_op_id": spec["lop"], "labor_op": next((n for l, n, _ in LABOR_OPS if l == spec["lop"]), spec["lop"]),
        "labor_hours": hours, "labor_rate": rate, "labor_amount": labor, "labor_source_ref": f"DEALER_LaborOps:{spec['lop']}",
        "parts": parts, "parts_amount": parts_amt,
        "coverage": {"pay_type": "customer_pay", "rule": "wear item" if wear else "outside warranty", "vehicle_age_months": None,
                     "source_ref": "coverage_rule:basic_warranty (DEALER_Vehicles.delivered_at)"},
        "customer_amount": total, "line_total": total,
        "price_source": "labor guide flat-rate × shop rate + parts catalog",
        "formula": f"{hours} h × {rate} + {parts_amt}", "currency": cur,
        "decision": None, "decided_by": None, "decided_at": None, "decision_reason": None,
        "line_status": "pending", "audit": [],
    }


DTC_CODES = [
    ("P0300", "Random/multiple cylinder misfire"), ("P0420", "Catalyst efficiency below threshold"),
    ("P0455", "EVAP large leak"), ("B1318", "Battery voltage low"), ("P0A80", "Replace hybrid battery pack"),
    ("C1234", "Wheel speed sensor fault"), ("U0100", "Lost comms with ECM"),
]


def _amortized(principal: float, apr_pct: float, term: int) -> float:
    """Standard amortized monthly payment — the SAME formula the F&I desk uses
    (app/services/finance/prequalify._monthly), so a seeded deal's payment matches
    the lender offer and the payment grid the desk computes from the same inputs."""
    if principal <= 0 or term <= 0:
        return 0.0
    r = (apr_pct / 100) / 12
    if r == 0:
        return round(principal / term, 2)
    return round(principal * r / (1 - (1 + r) ** (-term)), 2)


# Miles at which an item is typically due; a car under half of it reads green (it cannot be
# worn yet: a red cabin filter on a 1,128-mile car reads as fake).
_WEAR_MILES = {"Front brakes": 40000, "Rear brakes": 50000, "Tires": 40000, "Cabin filter": 20000,
               "Engine oil": 7500, "Wipers": 12000}
_ICE_ONLY = {"Engine oil"}   # an EV still has coolant loops and a 12V battery


def _green_reading(spec: dict, frac: float = 0.2) -> tuple[str, float | str]:
    """A fixed green reading for `spec` (RNG-free): `frac` of the way from new toward yellow."""
    if spec["unit"] == "condition":
        return "green", _CONDITION_WORDS[5 if frac < 0.25 else 4]
    return "green", round(spec["hi"] - (spec["hi"] - spec["yellow"]) * min(frac, 0.6), 1)


def _bay_vehicle(g: "G", c: dict, vin: str | None, vehicle_by_vin: dict) -> tuple[bool, float | None]:
    """(is EV, odometer in miles) for the car on the service journey: its VIN-master row when
    there is one (catalog powertrain + odometer), else the customer's current vehicle."""
    row = vehicle_by_vin.get(vin) or {}
    cv = c.get("current_vehicle") or {}
    mod = g.ix["models"].get(row.get("model_id")) if row.get("model_id") else None
    if mod:
        ev = list(mod.get("powertrains") or []) == ["EV"]
    else:
        name = str(row.get("model") or cv.get("model") or "")
        ev = _is_ev(g, {"model": name}) or any(
            list(m_.get("powertrains") or []) == ["EV"] and str(m_.get("name") or "").endswith(" " + name)
            for m_ in g.ix["models"].values()) if name else False
    odo = row.get("odometer") if row.get("odometer") is not None else cv.get("mileage")
    if odo is None:
        return ev, None
    km = (row.get("market_id") or c.get("market_id")) == "de" or cv.get("distance_unit") == "km"
    return ev, float(odo) / 1.609 if km else float(odo)


def _fit_reading(spec: dict, status: str, measurement, is_ev: bool, miles: float | None,
                 used: set[str]) -> tuple[dict, str, float | str]:
    """Make a drawn MPI reading fit the car in the bay, deterministically:
    an item that cannot exist on this powertrain becomes one that does (unused on this
    inspection, with a fixed green reading); a wear item on a car under half its wear
    interval reads green. Never makes a reading worse."""
    wrong_ev = is_ev and spec["item"] in _ICE_ONLY
    wrong_ice = not is_ev and spec.get("ev") is True
    if wrong_ev or wrong_ice:
        pool = [s for s in MPI_ITEMS if s["item"] not in used
                and ((s.get("ev") is True) if wrong_ev else (s.get("ev") is False))]
        if pool:
            spec = pool[0]
            used.add(spec["item"])
            status, measurement = _green_reading(spec)
    due = _WEAR_MILES.get(spec["item"])
    if due and miles is not None and miles < due * 0.5 and status != "green":
        status, measurement = _green_reading(spec, miles / due)
    return spec, status, measurement


def _owned_car(g: "G", c: dict, j: dict) -> tuple[int, str, int, int] | None:
    """(delivered days ago, ownership, odometer, year) when the customer's current vehicle
    IS the journey's model (a storyline owner such as J6), else None. RNG-free."""
    cv = c.get("current_vehicle") or {}
    mod = g.ix["models"].get(j.get("primary_model_id")) or {}
    name = str(mod.get("name") or "")
    make = str(cv.get("make") or "")
    full = f"{make} {cv.get('model') or ''}".strip()
    if not cv.get("mileage") or not name or name not in (cv.get("model"), full):
        return None
    year = int(cv.get("year") or g.cfg.now.year)
    lease = cv.get("ownership") == "lease"
    if j.get("delivered_at"):
        # the journey's own delivery moment (core.py ends an owner's journey when the car arrived)
        days = max(1, (g.cfg.now - datetime.fromisoformat(j["delivered_at"])).total_seconds() / 86400)
    elif lease and cv.get("months_remaining"):
        days = round(max(1, 36 - int(cv["months_remaining"])) * 30.4)   # a 36-month lease
    else:
        days = max(1, (g.cfg.now.year - year) * 365 + 120)
    return days, "lease" if lease else "purchase", int(cv["mileage"]), year


def _reserve_unit_for_delivery(g: "G", j: dict) -> str | None:
    """Reserve a unit at the journey's dealer for a booked deal in delivery week, the way
    inventory.reserve() would: the journey's model, its trim when one is in stock, the
    lowest VIN first. Deterministic and RNG-free, so every later generated row is unchanged.
    None when the dealer has no unit of that model (the delivery then records no VIN)."""
    stock = [v for v in g.t.get("DEALER_Inventory", [])
             if v.get("dealer_id") == j.get("dealer_id") and v.get("model_id") == j.get("primary_model_id")
             and v.get("status") == "in_stock" and not v.get("reserved_for_journey_id")]
    if not stock:
        return None
    trim = j.get("primary_trim_id")
    unit = min(stock, key=lambda v: (v.get("trim_id") != trim, str(v.get("vin"))))
    unit.update({"status": "reserved", "status_before_reserve": "in_stock",
                 "reserved_for_journey_id": j["journey_id"], "reserved_at": j.get("updated_at") or j.get("created_at"),
                 "reserved_by": j.get("owner_user_id")})
    return unit.get("vin")


def _add(g: "G", table: str, row: dict) -> dict:
    if not owner_matches(table):
        raise ValueError(f"commerce.py may only emit DEALER_/CUST_ tables; got {table!r}")
    prefix = table.split("_", 1)[0]
    if prefix not in ("DEALER", "CUST"):
        raise ValueError(f"commerce.py owns DEALER_/CUST_ only; got {table!r}")
    return g.add(table, row)


def _within_weekly_cap(cadence: str) -> str:
    """A nurture plan never promises more touches than the 3-a-week cadence cap allows: a drawn
    "daily" plan (7 a week) runs every 3 days. Same RNG draw, so nothing downstream drifts."""
    return "every_3_days" if cadence == "daily" else cadence


_APPROVAL_ROLE = {"budget_shift": "oem_finance", "district_action_plan": "oem_regional",
                  "stage_parts": "dealer_service_manager"}


# district action plans a seeded request may carry (picked by the approval's number, not the RNG)
_PLAN_VARIANTS = [
    (["Lift test-drive follow-up within 24 h", "Rebalance aged EV stock across the district"],
     "closing rate below the regional median for three weeks."),
    (["Add a Saturday service-to-sales clinic at two stores", "Re-target lapsed service owners with equity offers"],
     "service retention down 4 points quarter on quarter."),
    (["Move two demo units to the stores with the longest EV wait", "Share the top store's delivery checklist"],
     "delivery CSI trailing the region on EV handovers."),
]


def _seed_approval(aid: str, agent: str, action: str, requested, home: dict, market: str, cur: str,
                   finance_user: str | None) -> dict:
    """One seeded approval request, shaped like approvals.request writes it (no RNG of its own)."""
    dealer, district = home.get("node_id"), home.get("district_id")
    row = {"approval_id": aid, "type": action, "action": action, "requested_by": agent,
           "requested_at": iso(requested), "approver_role": _APPROVAL_ROLE[action], "status": "pending",
           # the inbox is per market: a US approver never sees a German request
           "market_id": market, "source_ref": "synthetic:approval"}
    if action == "budget_shift":
        row.update(scope={"node": "OEM", "market_id": market},
                   payload={"amount": 15000, "currency": cur, "from": {"name": "Conquest display"},
                            "to": {"name": "Service-to-sales retention"}},
                   reason="Weekly reallocation from the lowest-ROI campaign to the highest.",
                   status="approved", decided_by=finance_user or "oem_finance", decision="approve",
                   decided_at=iso(requested + timedelta(hours=4)))
    elif action == "district_action_plan":
        actions, why = _PLAN_VARIANTS[int(aid.rsplit("-", 1)[-1]) % len(_PLAN_VARIANTS)]
        row.update(scope={"district_id": district, "market_id": market},
                   payload={"district_id": district, "actions": actions},
                   reason=f"District {district}: {why}")
    else:
        row.update(scope={"dealer_id": dealer, "market_id": market}, dealer_id=dealer,
                   payload={"parts": ["Front brake pad & rotor kit", "Cabin air filter"], "amount": 1840,
                            "cap": 1500, "currency": cur},
                   reason="Stock for predicted brake and filter work this week; the order is over the parts cap.")
    return row


# what a declined item plausibly costs (parts + labour, USD-ish; EUR rows use the same bands)
_DECLINED_PRICE = {"Rear brakes": (320, 620), "Cabin filter": (55, 110), "Alignment": (120, 220), "Coolant flush": (140, 240)}

# open visit stages and how long ago such a visit was opened (days): BOOK is ahead of its slot, a car in
# the bay came in today or yesterday, READY/FOLLOW_UP finished within the week
_OPEN_STAGES = ("BOOK", "CHECK_IN", "INSPECT", "DIAGNOSE", "APPROVE", "REPAIR", "READY", "FOLLOW_UP")
_OPEN_AGE_DAYS = {
    "BOOK": lambda rng: rng.uniform(0.1, 2.0), "CHECK_IN": lambda rng: rng.uniform(0.05, 0.6),
    "INSPECT": lambda rng: rng.uniform(0.1, 0.9), "DIAGNOSE": lambda rng: rng.uniform(0.2, 1.2),
    "APPROVE": lambda rng: rng.uniform(0.2, 1.5), "REPAIR": lambda rng: rng.uniform(0.3, 2.5),
    "READY": lambda rng: rng.uniform(0.2, 3.0), "FOLLOW_UP": lambda rng: rng.uniform(1.0, 7.0),
}


def _store_hours(dt, tz_name: str, now):
    """A promised time inside the store's hours (8:00–17:00 local): earlier than opening moves to
    9:00 that day, later than closing to 9:00 the next weekday. A promise already behind `now`
    stays behind it (it is a past promise, which the board must still show)."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(tz_name)
    loc = dt.astimezone(tz)
    if loc.hour < 8:
        loc = loc.replace(hour=9, minute=0, second=0)
    elif loc.hour >= 17:
        loc = (loc + timedelta(days=1)).replace(hour=9, minute=0, second=0)
    while loc.weekday() >= 5:
        loc += timedelta(days=1)
    out = loc.astimezone(dt.tzinfo)
    if dt < now <= out:                       # snapping must not turn a past promise into a future one
        return dt
    return out


def _promise_times(stage: str, opened, now, rng, tz_name: str = "UTC") -> dict:
    """promised_at / projected_at that fit the stage: a booked visit is promised for its slot (ahead),
    a car in the bay for the next day or so (a repair running long had its promise re-forecast, so the
    promise hangs off today, not off the day it came in), about one in six in-bay visits projects past
    its promise so the board's at-risk list has real cases, and one in twelve is already past it.
    The two draws keep their old order."""
    a, b = rng.randint(2, 48), rng.randint(2, 72)
    if stage == "BOOK":
        promised = _store_hours(max(opened, now) + timedelta(hours=24 + a), tz_name, now)   # the slot, ahead of today
        return {"promised_at": iso(promised), "projected_at": iso(promised)}
    if stage in _OPEN_STAGES:
        promised = _store_hours(now + timedelta(hours=(a % 30) - (5 if a % 12 == 0 else 0) + 1), tz_name, now)  # -4 h … +30 h
        late = (b % 6 == 0)
        projected = promised + timedelta(hours=2 + b % 20) if late else promised - timedelta(hours=b % 3)
        return {"promised_at": iso(promised), "projected_at": iso(projected)}
    return {"promised_at": iso(opened + timedelta(hours=a)), "projected_at": iso(opened + timedelta(hours=b))}


def gen_commerce(g: "G") -> None:
    m, cfg, rng = g.m, g.cfg, g.rng
    cur = m.currency

    journeys = g.t.get("DEALER_Journeys", [])
    customers = g.t.get("DEALER_Customers", [])
    cust_by_id = {c["customer_id"]: c for c in customers}
    configs = {c["config_id"]: c for c in g.t.get("PLAT_Configurations", [])}

    # ── 1. Sales close: deals, finance, F&I, deliveries, touchpoints ─────────
    fi_products = [("gap", "GAP protection"), ("vsc", "Vehicle service contract"),
                   ("maint", "Prepaid maintenance"), ("tire_wheel", "Tire & wheel"), ("appearance", "Appearance protection")]
    for j in journeys:
        if j["stage"] not in FINANCE_STAGES:
            continue
        c = cust_by_id.get(j["customer_id"])
        if not c:
            continue
        cfg_row = configs.get(j.get("config_id"))
        price_total = cfg_row["price_total"] if cfg_row else round(rng.uniform(28000, 62000))
        down = c["budget"]["down"]
        closed = j["status"] == "closed_won" or j["stage"] in DELIVERED_STAGES
        deal_id = g.nid("DL")
        apr = round(rng.choice([3.49, 4.9, 6.4, 9.8]) if m.id == "us" else rng.choice([3.49, 4.99, 6.99]), 2)
        term = rng.choice([48, 60, 72])
        deal_row = _add(g, "DEALER_Deals", {
            "deal_id": deal_id, "journey_id": j["journey_id"], "customer_id": c["customer_id"],
            "dealer_id": j["dealer_id"], "market_id": m.id, "type": rng.choice(["finance", "finance", "lease", "cash"]),
            "status": "booked" if closed else rng.choice(["pending", "desking", "signed"]),
            "price_total": price_total, "down_payment": down, "apr_pct": apr, "term_months": term,
            "monthly_payment": _amortized(price_total - down, apr, term),
            "currency": cur, "created_at": j["created_at"], "booked_at": iso(cfg.ago(days=rng.randint(0, 40))) if closed else None,
            "source_ref": "synthetic:deal; apr from market.py credit_tiers",
        })
        _add(g, "DEALER_FinanceOffers", {
            "offer_id": g.nid("FO"), "deal_id": deal_id, "journey_id": j["journey_id"], "lender": m.captive_finance,
            "apr_pct": apr, "term_months": term, "down_payment": down, "amount_financed": price_total - down,
            "monthly_payment": _amortized(price_total - down, apr, term), "currency": cur,
            "status": "accepted" if closed else "presented", "source_ref": "synthetic:finance_offer; market.py credit_tiers",
        })
        for pi, (code, name) in enumerate(rng.sample(fi_products, rng.randint(0, 3))):
            price = round(rng.uniform(400, 2500))
            _add(g, "DEALER_FIProducts", {
                "fi_product_id": g.nid("FIP"), "deal_id": deal_id, "code": code, "name": name,
                "price": price, "accepted": closed and rng.random() < 0.5, "currency": cur,
                "source_ref": "synthetic:fi_product",
            })
        # Touchpoints — the outcome/activity feed on the deal
        for _ in range(rng.randint(1, 4)):
            _add(g, "DEALER_Touchpoints", {
                "touchpoint_id": g.nid("TP"), "journey_id": j["journey_id"], "customer_id": c["customer_id"],
                "channel": rng.choice(["phone", "email", "sms", "in_person", "chat"]),
                "direction": rng.choice(["outbound", "inbound"]), "ts": iso(cfg.ago(days=rng.uniform(0, 40))),
                "user_id": j.get("owner_user_id"),
            })
        if j["stage"] == "OWN" or (closed and j["stage"] != "DELIVER"):
            _add(g, "DEALER_Deliveries", {
                "delivery_id": g.nid("DLV"), "deal_id": deal_id, "journey_id": j["journey_id"],
                "customer_id": c["customer_id"], "dealer_id": j["dealer_id"],
                "scheduled_at": iso(cfg.ago(days=rng.randint(0, 20))), "status": "completed",
                "vehicle_model_id": j.get("primary_model_id"), "source_ref": "synthetic:delivery",
            })
        elif j["stage"] == "DELIVER":
            # an upcoming delivery with the prep checklist part-way through, so the delivery
            # board has cards in Scheduled / Prepped / Docs ready (its lanes derive from this)
            is_ev_model = list((g.ix["models"].get(j.get("primary_model_id")) or {}).get("powertrains") or []) == ["EV"]
            items = ["pdi", "detail", "accessories", "charge_level"] + (["charger_install"] if is_ev_model else [])
            done_n = rng.choice([1, 2, len(items), len(items)])
            checklist = {k: (i < done_n) for i, k in enumerate(items)}
            prepped = all(checklist.values())
            # A booked deal in delivery week is booked against a unit: reserve one so the
            # delivery has a VIN and "Mark delivered" records the car as the customer's
            # (DEALER_Vehicles → My Garage). Picked without the RNG, so no later row moves.
            vin = _reserve_unit_for_delivery(g, j)
            if vin:
                deal_row["vin"] = vin
            _add(g, "DEALER_Deliveries", {
                "delivery_id": g.nid("DLV"), "deal_id": deal_id, "journey_id": j["journey_id"],
                "customer_id": c["customer_id"], "dealer_id": j["dealer_id"], "vin": vin,
                "scheduled_at": iso(cfg.ahead(days=rng.randint(1, 6), hours=rng.choice([10, 14, 17]))), "status": "scheduled",
                "checklist": checklist, "docs_signed": prepped and rng.random() < 0.5,
                "charger_install": (rng.choice(["scheduled", "installed"]) if is_ev_model else None),
                "customer_confirmed": rng.random() < 0.6,
                "vehicle_model_id": j.get("primary_model_id"), "source_ref": "synthetic:delivery (upcoming)",
            })

    # SalesPackages — 10 golden pre-aggregations (A2 fast render)
    golden_journeys = [j for j in journeys if j.get("golden_story") and j["golden_story"].startswith("J")]
    for j in golden_journeys[:10]:
        _add(g, "DEALER_SalesPackages", {
            "package_id": g.nid("SP"), "journey_id": j["journey_id"], "customer_id": j["customer_id"],
            "next_best_action": rng.choice(["schedule_test_drive", "present_finance", "value_trade", "follow_up"]),
            "summary": j.get("summary") or "Precomputed A2 aggregation", "generated_at": iso(cfg.now),
        })

    # ── 2. Trade / valuation / coverage ──────────────────────────────────────
    for c in customers:
        cv = c.get("current_vehicle")
        if not cv:
            continue
        val = round(rng.uniform(6000, 34000))
        payoff = cv.get("payoff", 0)
        if cv.get("ownership") == "lease":
            # A lease is never "paid off": its payoff is the buyout (residual + remaining payments).
            # Anchor it to the appraisal so a lease near its end carries a little positive equity
            # and one with many months left sits under water — the shape of a real lease curve.
            # Derived, not drawn, so the RNG sequence (and every later row) is unchanged.
            payoff = round(val * (0.78 + 0.012 * int(cv.get("months_remaining") or 0)))
            cv["payoff"] = payoff
        _add(g, "DEALER_Valuations", {
            "valuation_id": g.nid("VAL"), "customer_id": c["customer_id"], "dealer_id": c["dealer_id"],
            "make": cv["make"], "model": cv["model"], "year": cv["year"], "mileage": cv["mileage"],
            "trade_value": val, "payoff": payoff, "equity": val - payoff, "currency": cur,
            "valued_at": iso(cfg.ago(days=rng.randint(0, 60))),
            "source_ref": "synthetic:valuation (guidebook-anchored)",
        })
        if rng.random() < 0.4:
            _add(g, "DEALER_TradeDispositions", {
                "disposition_id": g.nid("TD"), "customer_id": c["customer_id"],
                "disposition": rng.choice(["retail", "wholesale", "auction"]),
                "acquired_at": iso(cfg.ago(days=rng.randint(0, 60))), "recon_cost": round(rng.uniform(200, 2500)),
                "currency": cur, "source_ref": "synthetic:trade_disposition",
            })
    for code, name in [("gap", "GAP"), ("vsc", "Service contract"), ("maint", "Prepaid maintenance")]:
        _add(g, "DEALER_Coverage", {
            "coverage_id": f"COV-{m.id.upper()}-{code}", "code": code, "name": name, "market_id": m.id,
            "term_months": rng.choice([36, 48, 60]), "price_from": round(rng.uniform(400, 1800)), "currency": cur,
            "source_ref": "synthetic:coverage_catalog",
        })

    # ── 2a. Reserved units: a hold is for someone. A unit the inventory step marked `reserved` with no
    # journey goes to an active late-stage journey for its model at the store, else back in stock.
    late = [j for j in journeys if j["status"] == "active" and j["stage"] in ("FINANCE", "TRADE_IN", "AGREE", "DELIVER")]
    taken = {v.get("reserved_for_journey_id") for v in g.t.get("DEALER_Inventory", []) if v.get("reserved_for_journey_id")}
    for v in g.t.get("DEALER_Inventory", []):
        if v.get("status") == "reserved" and not v.get("reserved_for_journey_id"):
            j = next((j for j in sorted(late, key=lambda j: j["journey_id"]) if j["dealer_id"] == v.get("dealer_id")
                      and j.get("primary_model_id") == v.get("model_id") and j["journey_id"] not in taken), None)
            if j:
                taken.add(j["journey_id"])
                v.update({"reserved_for_journey_id": j["journey_id"], "reserved_at": j.get("updated_at") or j.get("created_at"),
                          "reserved_by": j.get("owner_user_id"), "status_before_reserve": "in_stock"})
            else:
                v["status"] = "in_stock"
    # ── 2b. Vehicles (the VIN master) — every delivered/owned vehicle has a row ─
    # Delivered vehicles from bookings + owners' current vehicles. One VIN master
    # so service history (RepairOrders/ServiceJourneys) keys off it (plan §3).
    vehicles_by_customer: dict[str, str] = {}
    for j in journeys:
        if j["stage"] not in DELIVERED_STAGES:
            continue
        c = cust_by_id.get(j["customer_id"])
        if not c:
            continue
        vin = synthetic_vin(g.nid('VIN'), m.id)
        vehicles_by_customer[c["customer_id"]] = vin
        # the draws stay in this order whatever is kept, so every later row is unchanged
        delivered_days, ownership, odometer = rng.randint(0, 30), rng.choice(["purchase", "lease"]), rng.randint(50, 4000)
        year = None
        owned = _owned_car(g, c, j)
        if owned:
            # the storyline's car (e.g. J6's 2023 lease at 41,000 mi), not a fresh delivery:
            # its mileage, year and ownership, delivered when that lease or model year began
            delivered_days, ownership, odometer, year = owned
        # a fresh delivery happened when the journey's own vehicle.delivered event says it did — My Garage
        # shows that date and the activity feed its event, so the two must be one moment
        delivered_iso = j.get("delivered_at") if (j.get("delivered_at") and not owned) else iso(cfg.ago(days=delivered_days))
        _add(g, "DEALER_Vehicles", {
            "vin": vin, "customer_id": c["customer_id"], "dealer_id": j["dealer_id"], "market_id": m.id,
            "model_id": j.get("primary_model_id"), "trim_id": j.get("primary_trim_id"),
            "delivered_at": delivered_iso, "ownership": ownership,
            "odometer": odometer, **({"year": year} if year else {}),
            "source_ref": "synthetic:vehicle_master (delivered)",
        })
    # Owners with a current vehicle but no delivery record still get a VIN master row
    for c in customers:
        if c.get("current_vehicle") and c["customer_id"] not in vehicles_by_customer:
            vin = synthetic_vin(g.nid('VIN'), m.id)
            vehicles_by_customer[c["customer_id"]] = vin
            cv = c["current_vehicle"]
            _add(g, "DEALER_Vehicles", {
                "vin": vin, "customer_id": c["customer_id"], "dealer_id": c["dealer_id"], "market_id": m.id,
                "model_id": None, "trim_id": None, "make": cv["make"], "model": cv["model"], "year": cv["year"],
                "delivered_at": iso(cfg.ago(days=rng.randint(60, 900))), "ownership": cv.get("ownership", "owned"),
                "odometer": cv.get("mileage", 0), "source_ref": "synthetic:vehicle_master (owned)",
            })

    vehicle_by_vin = {v["vin"]: v for v in g.t.get("DEALER_Vehicles", [])}
    # ── 3. Service (Process 2) ────────────────────────────────────────────────
    # Reference tables first
    for lop_id, name, hrs in LABOR_OPS:
        _add(g, "DEALER_LaborOps", {"labor_op_id": lop_id, "name": name, "flat_rate_hours": hrs,
                                    "shop_rate": 165 if m.id == "us" else 145, "currency": cur, "market_id": m.id,
                                    "source_ref": "curated:flat_rate_manual"})
    for code, desc in DTC_CODES:
        _add(g, "DEALER_DTCReference", {"dtc": code, "description": desc, "system": code[0], "market_id": m.id,
                                        "source_ref": "curated:dtc_reference"})
    for tmpl, ver, ev in [("ICE/Hybrid MPI", 3, True), ("EV MPI", 3, True), ("Express (no MPI)", 1, False)]:
        _add(g, "DEALER_InspectionTemplates", {"template_id": f"MPI-{m.id.upper()}-{tmpl[:3].upper()}-v{ver}",
             "name": tmpl, "version": ver, "has_mpi": ev, "market_id": m.id, "source_ref": "prior:vsa.json + OEM schedules"})

    # Service journeys: golden S1-S8 + bulk. Only customers who own a vehicle get service.
    owners = [c for c in customers if c.get("current_vehicle")]
    n_service = min(len(owners), max(len(SERVICE_GOLDEN) + 4, cfg.volumes.customers // 12))
    picked = (owners[:len(SERVICE_GOLDEN)] + rng.sample(owners[len(SERVICE_GOLDEN):], max(0, n_service - len(SERVICE_GOLDEN)))
              if len(owners) > n_service else owners)   # no owner twice in the lane
    for idx, c in enumerate(picked):
        golden = SERVICE_GOLDEN[idx] if idx < len(SERVICE_GOLDEN) else None
        story = next((j for j in JOURNEYS if j["id"] == c.get("golden_story")), None)
        pinned_stage = (story or {}).get("service_stage") or ((story or {}).get("owned") or {}).get("service_stage")
        if pinned_stage:
            # the storyline puts this owner in today's lane (service-lane → showroom handoff)
            stage = pinned_stage
            opened = cfg.ago(hours=rng.uniform(2, 5))
        else:
            stage = rng.choices(SERVICE_STAGES, [14, 3, 4, 2, 6, 9, 4, 18, 40])[0] if not golden else rng.choice(SERVICE_STAGES)
            opened = cfg.ago(days=rng.uniform(0, 90))
            if stage in _OPEN_STAGES:
                # a visit still open was opened lately: a car in the bay since September, promised for
                # October, is not a lane — it is a stale record every rebase carried forward
                opened = cfg.ago(days=_OPEN_AGE_DAYS[stage](rng))
        sj_id = g.nid("SJ")
        vin_val = round(rng.uniform(6000, 30000))
        _add(g, "DEALER_ServiceJourneys", {
            "service_journey_id": sj_id, "customer_id": c["customer_id"], "dealer_id": c["dealer_id"], "market_id": m.id,
            "vin": vehicles_by_customer.get(c["customer_id"], synthetic_vin(g.nid('VIN'), m.id)),
            "stage": stage, "wait_type": rng.choice(["wait", "drop", "loaner", "mobile", "pickup_delivery"]),
            "advisor_id": c.get("assigned_advisor_user_id"), "technician_id": None,
            **_promise_times(stage, opened, cfg.now, rng, m.tz),
            "parts_hold": stage == "REPAIR" and rng.random() < 0.33, "golden_story": golden,
            "purchase_journey_id": g.ix.get("journey_by_customer", {}).get(c["customer_id"], {}).get("journey_id"),
            "opened_at": iso(opened), "source_ref": "synthetic:service_journey",
        })
        # Events (event_ts — same field name as purchase events)
        n_ev = rng.randint(20, 45) if golden else rng.randint(3, 12)
        for k in range(n_ev):
            _add(g, "DEALER_ServiceJourneyEvents", {
                "service_journey_id": sj_id, "event_ts": iso(opened + timedelta(hours=k * rng.uniform(0.5, 3))),
                "event_type": rng.choice(["service.stage_changed", "ro.opened", "inspection.submitted", "estimate.presented",
                                          "estimate.line_decided", "parts.ordered", "ro.line_completed", "vehicle.ready"]),
                "actor": rng.choice([{"type": "advisor"}, {"type": "technician"}, {"type": "agent", "agent": "A7"}]),
                "evidence": "synthetic", "payload": {},
            })
        # Repair order
        ro_id = g.nid("RO")
        labor_total = round(rng.uniform(120, 1400))
        parts_total = round(rng.uniform(0, 900))
        _add(g, "DEALER_RepairOrders", {
            "ro_id": ro_id, "service_journey_id": sj_id, "customer_id": c["customer_id"], "dealer_id": c["dealer_id"],
            "ro_number": f"RO{rng.randint(100000, 999999)}", "status": "closed" if stage in ("FOLLOW_UP", "COMPLETED") else "open",
            "labor_total": labor_total, "parts_total": parts_total, "total": labor_total + parts_total, "currency": cur,
            "opened_at": iso(opened), "source_ref": "synthetic:repair_order",
        })
        # Inspection (MPI): each item once, measured in its own unit, status derived from the
        # measurement (a 2.9/32" tire is red; coolant is never measured in 32nds).
        is_ev = _is_ev(g, c.get("current_vehicle") or {})
        items = [it for it in MPI_ITEMS if it.get("ev") is None or it["ev"] == is_ev]
        # The car in the bay is the journey's VIN, not the customer's other car: its
        # powertrain and odometer decide which items apply and how worn they can be.
        # Applied after every draw (below), so the RNG sequence — and every later row — is unchanged.
        bay_ev, bay_miles = _bay_vehicle(g, c, g.t["DEALER_ServiceJourneys"][-1]["vin"], vehicle_by_vin)
        drawn = rng.sample(items, rng.randint(6, min(9, len(items))))
        used = {s["item"] for s in drawn}
        findings = []
        for k, spec in enumerate(drawn):
            status, measurement = _mpi_reading(rng, spec)
            if stage == "APPROVE":
                # a visit waiting on approval has something to approve — two or three actionable
                # findings, not a car where everything is red (that reads as fake, not as a story)
                if k < 2 and status == "green":
                    status, measurement = _mpi_reading(rng, spec, force="yellow")
                elif k >= 3 and status != "green":
                    status, measurement = _mpi_reading(rng, spec, force="green")
            drawn_status = status
            spec, status, measurement = _fit_reading(spec, status, measurement, bay_ev, bay_miles, used)
            # the DOC id counter follows the drawn reading, so later document ids do not move
            doc_id = g.nid("DOC") if drawn_status in ("yellow", "red") else None
            row = {
                "inspection_id": g.nid("INS"), "ro_id": ro_id, "service_journey_id": sj_id,
                "item": spec["item"], "status": status, "measurement": measurement, "unit": spec["unit"],
                "evidence_doc_id": doc_id if status in ("yellow", "red") else None,
                "urgency": {"green": "none", "yellow": "soon", "red": "now"}[status], "source_ref": "synthetic:mpi",
            }
            _add(g, "DEALER_Inspections", row)
            findings.append(row)
        # Estimate: priced lines for every red/yellow finding, built the way A7's
        # build_estimate prices them (LaborOps flat rate × shop rate + part), so the
        # total on the estimate is the sum of lines the customer can actually see.
        est_id = g.nid("EST")
        est_lines = [_estimate_line(f, cur, m.id) for f in findings if f["status"] in ("red", "yellow")]
        est_total = round(sum(l["line_total"] for l in est_lines), 2)
        _add(g, "DEALER_ServiceEstimates", {
            "estimate_id": est_id, "ro_id": ro_id, "service_journey_id": sj_id, "total": est_total, "currency": cur,
            "labor_total": round(sum(l["labor_amount"] for l in est_lines), 2),
            "parts_total": round(sum(l["parts_amount"] for l in est_lines), 2),
            "lines": est_lines, "lines_persisted": True,
            # a journey waiting in APPROVE has a presented, undecided estimate; otherwise mixed history
            "status": ("presented" if stage == "APPROVE" else rng.choice(["presented", "approved", "partially_approved", "declined"]))
            if est_lines else "no_findings",
            "presented_at": iso(opened + timedelta(hours=rng.randint(1, 24))), "source_ref": "synthetic:estimate; LaborOps flat-rate",
        })
        if rng.random() < 0.35:
            item = rng.choice(["Rear brakes", "Cabin filter", "Alignment", "Coolant flush"])
            lo, hi = _DECLINED_PRICE[item]
            u, d_days = rng.uniform(80, 600), rng.randint(0, 3)        # the draws keep their order
            _add(g, "DEALER_DeclinedWork", {
                "declined_id": g.nid("DW"), "estimate_id": est_id, "service_journey_id": sj_id, "customer_id": c["customer_id"],
                "item": item, "amount": round(lo + (u - 80) / 520 * (hi - lo)),
                # declined when the estimate was presented, never after now (a decline dated tomorrow)
                "declined_at": iso(min(opened + timedelta(days=d_days), cfg.now - timedelta(hours=1))),
                "currency": cur, "source_ref": "synthetic:declined_work",
            })
        # Parts: the parts the estimate lines call for (same description / qty / price), plus
        # the odd stock item — so the parts counter sees what the RO needs, not random SKUs
        for ln in est_lines:
            for part in ln["parts"]:
                _add(g, "DEALER_Parts", {
                    "part_id": g.nid("PRT"), "ro_id": ro_id, "part_number": f"P{rng.randint(10000, 99999)}",
                    "description": part["description"], "qty": part["qty"], "unit_price": part["unit_price"],
                    "on_hand": rng.random() < 0.7, "labor_op_id": ln["labor_op_id"], "line_id": ln["line_id"],
                    "currency": cur, "source_ref": "synthetic:parts (from estimate line)",
                })
        if rng.random() < 0.3:
            _add(g, "DEALER_Parts", {
                "part_id": g.nid("PRT"), "ro_id": ro_id, "part_number": f"P{rng.randint(10000, 99999)}",
                "description": rng.choice(["Oil filter", "Wiper blade", "Key fob battery"]),
                "qty": rng.randint(1, 2), "unit_price": round(rng.uniform(8, 60)), "on_hand": rng.random() < 0.7,
                "currency": cur, "source_ref": "synthetic:parts",
            })
        held = False
        if rng.random() < 0.4:
            # the order is for a part the RO needs and does not have on hand (else the first part on the RO)
            ro_parts = [p for p in g.t.get("DEALER_Parts", []) if p.get("ro_id") == ro_id]
            ordered = next((p for p in ro_parts if not p.get("on_hand")), ro_parts[0] if ro_parts else None)
            if ordered is None:
                # an RO with no parts yet still orders what the inspection called for (a stock item)
                ordered = _add(g, "DEALER_Parts", {
                    "part_id": g.nid("PRT"), "ro_id": ro_id, "part_number": f"P{rng.randint(10000, 99999)}",
                    "description": rng.choice(["Cabin air filter", "Wiper blade set", "Brake fluid DOT 4"]),
                    "qty": 1, "unit_price": round(rng.uniform(18, 60)), "on_hand": False,
                    "currency": cur, "source_ref": "synthetic:parts (ordered for the RO)",
                })
            po = _add(g, "DEALER_PartsOrders", {
                "parts_order_id": g.nid("PO"), "ro_id": ro_id, "status": rng.choice(["ordered", "backordered", "received"]),
                "eta": iso(cfg.ahead(days=rng.randint(1, 7))), "source_ref": "synthetic:parts_order",
                **({"part_number": ordered["part_number"], "description": ordered["description"], "qty": ordered["qty"],
                    "part_id": ordered["part_id"]} if ordered else {}),
            })
            if ordered is not None:
                ordered["on_hand"] = False                   # a part on order is not on the shelf
            if stage in ("READY", "COMPLETED", "FOLLOW_UP"):
                po["status"] = "received"    # the car is past repair: its parts came in
            held = po["status"] in ("ordered", "backordered", "in_transit")
        # One definition of "held for parts" (the runtime keeps it the same way, desk.py):
        # a journey in REPAIR with an order still out on its RO. The board's filter, the
        # cockpit tile and its insight all read this flag.
        sj_row = g.t["DEALER_ServiceJourneys"][-1]
        sj_row["parts_hold"] = stage == "REPAIR" and held
        if golden == "S3":
            # The order-and-receive story (demo workflow: parts counter): the part this visit's
            # estimate calls for is NOT on the shelf and nothing is ordered yet, so the counter
            # places the order (the RO goes on parts hold), then receives it (the hold clears).
            # RNG-free and applied after every draw, so no other generated row moves.
            needed = [p for p in g.t["DEALER_Parts"] if p.get("ro_id") == ro_id and p.get("line_id")]
            for p in needed[:1]:
                p["on_hand"] = False
                p["source_ref"] = "synthetic:parts (from estimate line; not on the shelf — order it)"
            g.t["DEALER_PartsOrders"] = [o for o in g.t.get("DEALER_PartsOrders", []) if o.get("ro_id") != ro_id]
            sj_row["parts_hold"] = False

    # ── 4. Agentic + floor (the 10 U2-deferred DEALER_ tables, BR5.6) ────────
    # ObjectionStrategies (17 × market) with a usable curated line each — this phrasing is
    # what the floor shows whenever the model is unavailable or the guardrail withholds.
    for ot in OBJECTION_STRATEGY_TYPES:
        _add(g, "DEALER_ObjectionStrategies", {
            "strategy_id": f"OBJ-{m.id.upper()}-{ot}", "objection_type": ot, "market_id": m.id,
            "example_phrasing": OBJECTION_PHRASING[m.id].get(ot) or OBJECTION_PHRASING["us"].get(ot)
            or f"Acknowledge the {ot.replace('_', ' ')} concern, reframe on value, offer a concrete next step.",
            "source_ref": "curated:objection_strategies",
        })
    # Recorded outcomes per strategy (~6 each, mostly used/converted) so the coach can show a
    # success rate with its basis instead of "no recorded outcomes yet".
    _journey_pool = [j for j in journeys if j["status"] == "active"] or journeys
    for ot in OBJECTION_STRATEGY_TYPES:
        for k in range(rng.randint(5, 8)):
            tj = rng.choice(_journey_pool)
            _add(g, "DEALER_Outcomes", {
                "outcome_id": g.nid("OUT"), "journey_id": tj["journey_id"], "strategy_id": f"OBJ-{m.id.upper()}-{ot}",
                "recommendation_id": None,
                "outcome": rng.choices(["converted", "used", "alternative", "dismissed"], [38, 34, 16, 12])[0],
                "recorded_at": iso(cfg.ago(days=rng.uniform(1, 75))), "recorded_by": tj.get("owner_user_id"),
                "source_ref": "synthetic:objection_outcome (A3.record_outcome shape)",
            })
    # Plays + PlayRuns
    plays = []
    for pid, name, trigger in [("PLAY-STALLED", "Re-engage stalled journey", "idle_days>7"),
                               ("PLAY-EQUITY", "Equity upgrade outreach", "positive_equity"),
                               ("PLAY-SERVICE-UPSELL", "Service-lane upsell", "declined_work"),
                               ("PLAY-CONQUEST", "Conquest nurture", "campaign_lead")]:
        _add(g, "DEALER_Plays", {"play_id": f"{pid}-{m.id.upper()}", "name": name, "trigger": trigger, "market_id": m.id,
                                 "steps": ["identify", "compose", "send", "measure"], "source_ref": "curated:plays"})
        plays.append(f"{pid}-{m.id.upper()}")
    # Recommendations (shown>=accepted>=converted funnels) + Outcomes + Tasks + Notifications + NurturePlans
    active_journeys = [j for j in journeys if j["status"] == "active"]
    # an equity flag is only ever raised on a customer who has positive equity in a car they own
    has_equity = {v["customer_id"] for v in g.t.get("DEALER_Valuations", []) if (v.get("equity") or 0) > 0} \
        & set(vehicles_by_customer)
    for j in active_journeys:
        shown = rng.randint(1, 6)
        accepted = rng.randint(0, shown)
        converted = rng.randint(0, accepted)
        rec_id = g.nid("REC")
        agent, action = rng.choice(["A1", "A2", "A3", "A4"]), rng.choice(["next_best_action", "objection_response", "match", "equity_flag"])
        if action == "equity_flag" and j["customer_id"] not in has_equity:
            agent, action = "A2", "next_best_action"
        _add(g, "DEALER_Recommendations", {
            "recommendation_id": rec_id, "journey_id": j["journey_id"], "customer_id": j["customer_id"],
            "agent_id": agent, "action": action,
            "context_hash": f"ctx-{rng.randint(1000, 9999)}", "shown": shown, "accepted": accepted, "converted": converted,
            "shown_at": iso(cfg.ago(days=rng.uniform(0, 30))), "source_ref": "synthetic:recommendation_funnel (plan §4.10.3)",
        })
        _add(g, "DEALER_Outcomes", {
            "outcome_id": g.nid("OUT"), "recommendation_id": rec_id, "journey_id": j["journey_id"],
            "outcome": "converted" if converted else ("accepted" if accepted else "shown"),
            "recorded_at": iso(cfg.ago(days=rng.uniform(0, 30))),
        })
        if rng.random() < 0.5:
            _add(g, "DEALER_Tasks", {
                "task_id": g.nid("TSK"), "journey_id": j["journey_id"], "assigned_user_id": j.get("owner_user_id"),
                "title": rng.choice(["Follow up on finance", "Send trade offer", "Confirm test drive", "Answer question"]),
                "status": rng.choice(["open", "in_progress", "done"]), "due_at": iso(cfg.ahead(days=rng.randint(0, 5))),
            })
        if rng.random() < 0.4:
            _add(g, "DEALER_Notifications", {
                "notification_id": g.nid("NTF"), "user_id": j.get("owner_user_id"), "journey_id": j["journey_id"],
                "event": rng.choice(["lead.received", "task.due", "handoff.created"]), "read": rng.random() < 0.6,
                "created_at": iso(cfg.ago(days=rng.uniform(0, 15))),
            })
        if j.get("nurture_active"):
            _add(g, "DEALER_NurturePlans", {
                "nurture_plan_id": g.nid("NP"), "journey_id": j["journey_id"], "customer_id": j["customer_id"],
                "goal": rng.choice(["book_test_drive", "present_offer", "re_engage"]), "cadence": _within_weekly_cap(rng.choice(["daily", "every_3_days", "weekly"])),
                "next_touch_at": iso(cfg.ahead(minutes=rng.randint(5, 4000))), "stop_conditions": ["opt_out", "converted"],
                "source_ref": "synthetic:nurture_plan",
            })
    # PlayRuns — a few executions per play
    for play_id in plays:
        for _ in range(rng.randint(2, 6)):
            tj = rng.choice(active_journeys) if active_journeys else None
            _add(g, "DEALER_PlayRuns", {
                "play_run_id": g.nid("PR"), "play_id": play_id, "journey_id": tj["journey_id"] if tj else None,
                "status": rng.choice(["running", "completed", "stopped"]), "started_at": iso(cfg.ago(days=rng.uniform(0, 20))),
            })
    # Approvals — a few agent requests, each complete enough to decide (type, scope, payload,
    # reason, approver). A budget shift is only ever raised live (A5's nightly draft, the ROI
    # scenario), so a seeded one is history an approver already decided — never a stub left
    # pending in finance's inbox next to the real one. The draws (and so every later row in
    # this RNG stream) are the same as before; only the row built from them changed.
    home = next((n for n in g.t.get("PLAT_OrgHierarchy", []) if n.get("type") == "dealer"), {})
    finance = next((u["user_id"] for u in g.t.get("PLAT_Users", []) if "oem_finance" in (u.get("groups") or [])), None)
    for _ in range(rng.randint(2, 5)):
        aid, agent = g.nid("APR"), rng.choice(["A5", "A6", "A7"])
        action = rng.choice(["budget_shift", "district_action_plan", "stage_parts"])
        _add(g, "DEALER_Approvals", _seed_approval(aid, agent, action, cfg.ago(days=rng.uniform(0, 3)), home, m.id, cur,
                                                     finance))

    # ── 5. Customer: messages + documents ────────────────────────────────────
    for c in customers:
        if rng.random() < 0.5:
            # inbound = from the customer, outbound = the dealer/A2 nurture send,
            # so the body has to match the direction (the customer thread renders
            # inbound as "You"). Chosen from the id, not the shared RNG, to keep
            # the draw sequence — and therefore every other table — unchanged.
            direction = rng.choice(["outbound", "inbound"])
            mid = g.nid("MSG")
            _add(g, "CUST_Messages", {
                "message_id": mid, "customer_id": c["customer_id"], "direction": direction,
                "channel": rng.choice(["email", "sms", "chat"]), "body": _message_body(direction, mid),
                "sent_at": iso(cfg.ago(days=rng.uniform(0, 30))),
            })
        # the storyline's declared documents always exist (J4: worksheet, F&I menu, delivery
        # checklist shared to My Garage); everyone else gets one at random
        story = next((j for j in JOURNEYS if j["id"] == c.get("golden_story")), None)
        declared = list((story or {}).get("documents") or [])
        kinds = declared or (["random"] if rng.random() < 0.3 else [])
        for kind in kinds:
            _add(g, "CUST_Documents", {
                "document_id": g.nid("CDOC"), "customer_id": c["customer_id"],
                "kind": kind if kind != "random" else rng.choice(["contract", "invoice", "id_verification", "insurance"]),
                "uri": f"app-docs/{c['customer_id']}/{g.nid('F')[-6:]}.pdf", "data_class": "C3",
                "shared_with": ["customer"] if declared else [],
                "created_at": iso(cfg.ago(days=rng.uniform(0, 60) if not declared else rng.uniform(0, 3))),
            })


# ── Phase registration (U4 phase-registry refactor) ─────────────────────────
# gen_commerce runs after the six sales-spine phases (bands 20..25) and before
# OEM (band 30) and platform-late (band 90). Order 26 keeps it immediately after
# the spine, matching the pre-refactor PHASES literal. See phase_registry.py.
from . import phase_registry as _pr  # noqa: E402

_pr.register(_pr.BAND_COMMERCE + 6, gen_commerce)  # 26
