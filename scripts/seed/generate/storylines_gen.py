"""Storylines weaving component (U6): the coherence layer over the golden slices.

The golden data itself is already materialized by earlier phases — J1-J9 purchase
journeys and the J10 cold cohort (core.gen_journeys), S1-S8 service visits
(commerce.gen_commerce), the District-7 org tree + OEM accounts/plans/alerts/CI
(core.gen_org_users + oem.gen_oem), and the golden AgentTraces
(platform.gen_platform_late). What was missing is the *connective tissue*: a
single, resolved index that ties each named storyline to the concrete rows it
spans across the entity units, so the demo, the exporters (U7), and the
validation invariants (U8) can assert "every golden storyline is present and
wired end to end" without re-deriving it.

`gen_storylines(g)` (registered at band 50 — after corpora at 40, before
platform.gen_platform_late at 90) reads the populated accumulator and builds
`g.ix["storylines"]`: a list of storyline dicts (kind purchase|service|district),
each carrying the resolved ids of the customer, journey, service journey,
dealer, campaign, and the counts of events/recommendations that back it. It also
records a small `coverage` summary. It writes NO tables (the rows already exist
and are owned by other units), so there is no owner-boundary concern; the index
is in-memory, exactly like corpora's `g.ix["corpus"]`.

Determinism: pure reads over the already-seeded accumulator, no RNG draws, so it
adds nothing to the RNG stream (NFR1). Two markets via `g.m` (NFR7).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from . import phase_registry as _pr
from .storylines import COLD_COHORT_ID, DISTRICT, JOURNEYS

if TYPE_CHECKING:  # pragma: no cover
    from .core import G

# The eight golden service storylines (mirror of commerce.SERVICE_GOLDEN, kept
# local so this module has no import dependency on commerce internals).
SERVICE_GOLDEN = ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8")


def gen_storylines(g: "G") -> None:
    m = g.m
    mid = m.id
    stories: list[dict] = []

    journeys = g.t.get("DEALER_Journeys", [])
    by_golden = {j.get("golden_story"): j for j in journeys if j.get("golden_story")}
    jevents_by_j: dict[str, int] = {}
    for e in g.t.get("DEALER_JourneyEvents", []):
        jevents_by_j[e["journey_id"]] = jevents_by_j.get(e["journey_id"], 0) + 1
    recs_by_journey: dict[str, int] = {}
    for r in g.t.get("DEALER_Recommendations", []):
        jid = r.get("journey_id")
        if jid:
            recs_by_journey[jid] = recs_by_journey.get(jid, 0) + 1

    # ── Purchase storylines J1-J9 ───────────────────────────────────────────
    for spec in JOURNEYS:
        sid = spec["id"]
        j = by_golden.get(sid)
        journey_id = j["journey_id"] if j else None
        stories.append({
            "storyline_id": sid, "kind": "purchase", "market_id": mid,
            "title": spec["summary"], "journey_id": journey_id,
            "customer_id": j["customer_id"] if j else None,
            "dealer_id": j["dealer_id"] if j else None,
            "stage": j["stage"] if j else spec["stage"],
            "campaign_id": (j.get("campaign_id") if j else None),
            "event_count": jevents_by_j.get(journey_id, 0),
            "recommendation_count": recs_by_journey.get(journey_id, 0),
            "present": journey_id is not None,
            "source_ref": f"storyline:{sid}; entity:{journey_id}",
        })

    # ── Cold cohort J10 (a set of stalled journeys, not a single one) ───────
    cold = [j for j in journeys if j.get("golden_story") == COLD_COHORT_ID]
    stories.append({
        "storyline_id": COLD_COHORT_ID, "kind": "cold_cohort", "market_id": mid,
        "title": "Went-cold cohort — stalled journeys for the Kanban stalled filter",
        "journey_ids": [j["journey_id"] for j in cold], "count": len(cold),
        "present": len(cold) > 0, "source_ref": f"storyline:{COLD_COHORT_ID}",
    })

    # ── Service storylines S1-S8 ────────────────────────────────────────────
    sjourneys = g.t.get("DEALER_ServiceJourneys", [])
    by_sgolden = {s.get("golden_story"): s for s in sjourneys if s.get("golden_story")}
    sevents_by_s: dict[str, int] = {}
    for e in g.t.get("DEALER_ServiceJourneyEvents", []):
        sevents_by_s[e["service_journey_id"]] = sevents_by_s.get(e["service_journey_id"], 0) + 1
    for sid in SERVICE_GOLDEN:
        s = by_sgolden.get(sid)
        sj_id = s["service_journey_id"] if s else None
        stories.append({
            "storyline_id": sid, "kind": "service", "market_id": mid,
            "service_journey_id": sj_id,
            "customer_id": s["customer_id"] if s else None,
            "dealer_id": s["dealer_id"] if s else None,
            "stage": s["stage"] if s else None,
            "purchase_journey_id": s.get("purchase_journey_id") if s else None,
            "event_count": sevents_by_s.get(sj_id, 0),
            "present": sj_id is not None,
            "source_ref": f"storyline:{sid}; entity:{sj_id}",
        })

    # ── District storyline (District 7 / Bezirk Süd-West) ───────────────────
    d = DISTRICT[mid]
    accounts = [a for a in g.t.get("OEM_Accounts", []) if a.get("district_id") == d["district_id"]]
    alerts = [a for a in g.t.get("OEM_Alerts", []) if a.get("district_id") == d["district_id"]]
    plans = [p for p in g.t.get("OEM_AccountPlans", []) if p.get("district_id") == d["district_id"]]
    campaigns = [c for c in g.t.get("OEM_Campaigns", []) if c.get("district_id") == d["district_id"]]
    red = next((dl.code for dl in m.dealers if dl.condition == "csi_decline_lead_sla"), None)
    red_plan_versions = sorted(
        p["version"] for p in plans if p.get("dealer_id") == red and p.get("status") == "published"
    )
    stories.append({
        "storyline_id": "DISTRICT", "kind": "district", "market_id": mid,
        "district_id": d["district_id"], "district_name": d["name"], "dm": d["dm"],
        "red_dealer_id": red, "red_dealer_plan_versions": red_plan_versions,
        "account_count": len(accounts), "alert_count": len(alerts),
        "plan_count": len(plans), "campaign_count": len(campaigns),
        "present": bool(accounts and alerts and plans),
        "source_ref": f"storyline:DISTRICT; district:{d['district_id']}",
    })

    g.ix["storylines"] = stories

    # A compact coverage summary the validation stage (U8) can assert on.
    g.ix["storyline_coverage"] = {
        "purchase_present": sum(1 for s in stories if s["kind"] == "purchase" and s["present"]),
        "purchase_total": len(JOURNEYS),
        "service_present": sum(1 for s in stories if s["kind"] == "service" and s["present"]),
        "service_total": len(SERVICE_GOLDEN),
        "cold_cohort_present": any(s["kind"] == "cold_cohort" and s["present"] for s in stories),
        "district_present": any(s["kind"] == "district" and s["present"] for s in stories),
    }


# ── Phase registration (U4 phase-registry refactor) ─────────────────────────
# Storylines runs at band 50 — after corpora (40), before platform-late (90).
_pr.register(_pr.BAND_STORYLINES, gen_storylines)  # 50
