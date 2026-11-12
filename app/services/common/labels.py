"""Words for the codes the record keeps (stages, context keys, statuses).

The record keeps machine codes (TRADE_IN, shortlist, partially_approved); people read words.
Every message a screen shows — an error, an agent's reason, a status line — goes through
these instead of printing the code or a Python list.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

STAGE_LABEL: dict[str, str] = {
    # purchase journey (§2.1)
    "DISCOVER": "Discover", "CONFIGURE": "Configure", "QUALIFY": "Qualify", "FINANCE": "Finance",
    "TRADE_IN": "Trade-in", "AGREE": "Agree", "DELIVER": "Deliver", "OWN": "Own",
    # service journey (§2.1.1)
    "BOOK": "Book", "CHECK_IN": "Check-in", "INSPECT": "Inspect", "DIAGNOSE": "Diagnose",
    "APPROVE": "Approve", "REPAIR": "Repair", "READY": "Ready", "FOLLOW_UP": "Follow-up",
    "COMPLETED": "Completed",
}

# what each required-context key means, as the phrase that finishes "still needs …"
CONTEXT_LABEL: dict[str, str] = {
    "shortlist": "a saved shortlist of vehicles",
    "saved_build": "a saved build (trim, packs and price)",
    "budget": "the customer's budget",
    "timeline": "the purchase timeline",
    "finance_offer": "a finance offer presented",
    "valuation": "a trade-in valuation",
    "deal_structure": "an agreed deal structure",
    "credit_compliance": "the credit compliance checks",
    "delivered": "the delivery completed",
    "estimate_priced_with_source": "a priced, sourced estimate",
    "customer_decision": "the customer's decision on the estimate",
}


DISPOSITION_LABEL: dict[str, str] = {
    "keep_cpo": "Keep as certified pre-owned", "retail": "Retail it", "wholesale": "Wholesale",
    "auction": "Send to auction",
}
LEAD_SOURCE_LABEL: dict[str, str] = {
    "web_form": "web form", "web": "the website", "walk_in": "walk-in", "phone": "phone", "campaign": "a campaign",
    "third_party_lead": "a third-party lead", "service_lane": "the service lane", "predictive_maintenance": "a service heads-up",
    "referral": "a referral", "event": "an event",
}
FI_PRODUCT_LABEL: dict[str, str] = {
    "gap": "GAP protection", "vsc": "Vehicle service contract", "maint": "Prepaid maintenance",
    "tire_wheel": "Tire & wheel", "appearance": "Appearance protection",
}
# tokens that keep their capitals when a part name is lowered into a sentence
_KEEP_CASE = ("12V", "EV", "HV", "DC", "AC", "ABS", "LED", "USB", "GPS", "OEM", "MPI", "DTC", "VIN")
_PLURAL_PARTS = ("pads", "tires", "tyres", "filters", "brakes", "wipers", "rotors", "plugs", "belts", "hoses", "lights")


def part_phrase(name: str | None) -> str:
    """A part name as it reads mid-sentence: 'Front brake pads' → 'front brake pads', 'Tires (set)' →
    'tires', '12V battery' stays '12V battery' (never '12v battery')."""
    s = str(name or "").split(" (")[0].strip()
    return " ".join(w if w.upper() in _KEEP_CASE else w.lower() for w in s.split())


def is_are(part: str | None) -> str:
    """'is' or 'are' for a part name: 'the front brake pads are', 'the 12V battery is'."""
    words = str(part or "").split(" (")[0].split()
    return "are" if words and words[-1].lower() in _PLURAL_PARTS else "is"


def disposition_label(code: str | None) -> str:
    return DISPOSITION_LABEL.get(str(code or ""), humanize(code))


def lead_source_label(code: str | None) -> str:
    return LEAD_SOURCE_LABEL.get(str(code or ""), humanize(code).lower() or "an enquiry")


def fi_product_label(code: str | None) -> str:
    return FI_PRODUCT_LABEL.get(str(code or ""), humanize(code))


AGENT_LABEL: dict[str, str] = {
    "A1": "Discovery Agent", "A2": "Journey Agent", "A3": "Sales Agent", "A4": "Trade-In Agent",
    "A5": "Campaign Agent", "A6": "Field Sales Agent", "A7": "Service Agent", "L4": "the console",
}
# what the seeded / recorded `evidence` of a journey event means in words
EVIDENCE_LABEL: dict[str, str] = {
    "DEALER_Leads": "lead received", "validate_handoff ok": "handoff checks passed",
    "journey.created": "journey opened", "A1 DiscoveryResult": "Discovery Agent shortlist",
    "budget+timeline recorded": "budget and timeline recorded", "Advisor Present button": "presented by the advisor",
    "My Garage prequal link": "started from My Garage", "F&I sandbox": "pre-qualification answered",
    "valuation connector": "trade valued", "My Garage": "accepted in My Garage", "structure_deal": "deal structured",
    "push_deal_to_dms": "sent to the DMS", "deal worksheet → My Garage": "worksheet shared to My Garage",
    "DMS booked": "booked in the DMS", "mark_delivered": "keys handed over", "OutreachProvider email": "survey email sent",
    "accepted": "accepted", "BDC → advisor": "BDC to advisor", "nurture plan": "nurture message",
    "live transcript": "heard on the floor", "A3 strategy": "Sales Agent reply used", "/shop/chat": "My Garage chat",
}


def agent_label(code: str | None) -> str | None:
    return AGENT_LABEL.get(str(code or "")) if code else None


def actor_label(actor: Any, names: dict[str, str]) -> str | None:
    """A person's name, an agent's name, or both for an agent acting for a person
    ('A7:U-US-00007' → 'Service Agent for Anthony Nguyen'); never a raw id."""
    if not actor:
        return None
    if isinstance(actor, dict):
        if actor.get("agent"):
            return agent_label(actor["agent"]) or humanize(actor["agent"])
        if actor.get("user_id"):
            return names.get(str(actor["user_id"]), humanize(actor["user_id"]))
        return humanize(actor.get("connector") or actor.get("type"))
    s = str(actor)
    if ":" in s:
        a, _, u = s.partition(":")
        who = names.get(u)
        return f"{agent_label(a) or humanize(a)}" + (f" for {who}" if who else "")
    return agent_label(s) or names.get(s) or (humanize(s) if not s.startswith(("U-", "C-")) else "a colleague")


def recommendation_label(action: Any, trims: dict[str, str] | None = None) -> str | None:
    """An agent recommendation's action as words: 'present:MER-VOLT-C-T1' → 'suggested presenting the
    Ion C Standard' (a suggestion, not the presenting itself), 'objection:payment_too_high' →
    'coached an objection — payment too high'."""
    if not action:
        return None
    s = str(action)
    if s.startswith("present:"):
        tid = s.split(":", 1)[1]
        return f"suggested presenting the {(trims or {}).get(tid) or humanize(tid)}"
    if s.startswith("match:"):
        return "matched a vehicle to the customer's needs"
    if s.startswith("objection:"):
        return f"coached an objection — {humanize(s.split(':', 1)[1]).lower()}"
    return humanize(s)


def handoff_note_label(note: Any) -> str | None:
    """'service_lane handoff: positive_equity, lease_end' → 'from the service lane: positive equity, lease end'."""
    if not note:
        return None
    s = str(note)
    if s.startswith("service_lane handoff:"):
        reasons = [humanize(x.strip()).lower() for x in s.split(":", 1)[1].split(",") if x.strip()]
        return "from the service lane: " + join_words(reasons) if reasons else "from the service lane"
    return s


def evidence_label(evidence: Any, trims: dict[str, str] | None = None) -> str | None:
    """A journey event's evidence as words: 'lead source=service_lane' → 'from the service lane',
    'present:MER-VOLT-C-T3' → 'presented the Ion C Extended AWD' (with the catalog's trim names)."""
    if not evidence:
        return None
    s = str(evidence)
    if s in EVIDENCE_LABEL:
        return EVIDENCE_LABEL[s]
    if s.startswith("present:"):
        tid = s.split(":", 1)[1]
        return f"presented the {(trims or {}).get(tid) or humanize(tid)}"
    if s.startswith("match:"):
        return "vehicle matched to the customer's needs"
    if s.startswith("test drive 20"):
        return "test drive booked"
    if s.startswith("lead source="):
        return f"from {lead_source_label(s.split('=', 1)[1])}"
    if s.startswith("objection:"):
        return f"objection — {humanize(s.split(':', 1)[1]).lower()}"
    # a recommendation action code (acquisition_recommended, budget_shift) reads as words
    if "_" in s and " " not in s and s == s.lower():
        return humanize(s)
    return s


def humanize(code: str | None) -> str:
    """snake/SCREAMING code → words: partially_approved → Partially approved."""
    if not code:
        return ""
    s = str(code).replace("_", " ").replace("-", " ").strip()
    return s[:1].upper() + s[1:].lower() if s else ""


def stage_label(stage: str | None) -> str:
    return STAGE_LABEL.get(str(stage or ""), humanize(stage))


def context_label(key: str) -> str:
    k = str(key)
    if k.startswith("line_unsourced:"):
        return f"labor op, price source and coverage on estimate line {k.split(':', 1)[1]}"
    return CONTEXT_LABEL.get(k, humanize(k).lower())


def join_words(items: Iterable[str]) -> str:
    xs = [x for x in items if x]
    if len(xs) <= 1:
        return "".join(xs)
    return ", ".join(xs[:-1]) + " and " + xs[-1]


def missing_message(stage: str | None, missing: Iterable[str]) -> str:
    """'cannot leave DISCOVER: missing ['shortlist']' → 'Can't move on from Discover yet: it still
    needs a saved shortlist of vehicles.'"""
    return (f"Can't move on from {stage_label(stage)} yet: it still needs "
            f"{join_words(context_label(m) for m in missing)}.")
