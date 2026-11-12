"""Golden storylines: consumer journeys J1–J10, district dealer conditions, marketing campaigns.

Localized per market; ids are identical across markets so the market-switch test compares like with like.
"""
from __future__ import annotations

# Each journey: id, names per market, stage, model_id (synthetic brand model), powertrain, stage-specific flags.
JOURNEYS = [
    {"id": "J1", "us": ("Jordan", "Alvarez"), "de": ("Jonas", "Albrecht"), "stage": "QUALIFY",
     "model": {"us": "MER-VOLT-C", "de": "NW-VELA"}, "alt_model": {"us": "MER-CROSSTRAIL", "de": "NW-KORA"},
     "powertrain": "EV", "dealer_idx": 0, "household": 4, "commute": 35, "intake_answered": True,
     # the current car is in the lane today with a presented estimate: My Garage opens on
     # "Estimate waiting for you" and the phone-approval demo has something to approve
     "service_stage": "APPROVE",
     "objections": ["range_anxiety", "charging_access"], "test_drive_in_days": 2, "nurture_touches": 3, "inbound_questions": 1,
     # 77,500 mi puts the front pads and cabin filter (40k / 20k wear curves) ~2,500 mi out, so
     # the Service Agent's predictive watch has a real, near-term prediction for the demo car
     "current_vehicle": {"us": ("Halden", "Alder", 2017, 77500), "de": ("Rheinmark", "Tarn", 2017, 118500)},
     "summary": "EV-curious family; thin signals → lifestyle intake; range objection; expired vs active incentives"},
    {"id": "J2", "us": ("Priya", "Natarajan"), "de": ("Petra", "Neumann"), "stage": "CONFIGURE", "stalled_days": 9,
     "model": {"us": "MER-RIDGELINE", "de": "NW-HAVEL"}, "powertrain": "HEV", "dealer_idx": 1, "household": 3, "commute": 22,
     "competitor_compare": True, "nurture_touches": 2, "inbound_questions": 1,
     "summary": "Configured then stalled 9 days; compared a competitor → intent shift → SMS nudge + dealer task"},
    {"id": "J3", "us": ("Marcus", "Webb"), "de": ("Markus", "Weber"), "stage": "FINANCE", "prev_stage": "TRADE_IN",
     "model": {"us": "MER-TRAILHAND", "de": "NW-ELBE"}, "powertrain": "ICE", "dealer_idx": 2, "household": 2, "commute": 48,
     "trade": {"equity": -3800, "us": ("Larkspur", "Trailmark", 2021, 41000, 24900), "de": ("Lindau", "Signum", 2020, 74000, 18900)},
     "deal_exception": True, "objections": ["trade_value_low"], "demo_missing_context": ["trade_condition"],
     "summary": "Negative-equity trade; payment-delta framing; F&I scenarios roll equity; GAP attach; deal exception approval"},
    # J4 is the F&I storyline (deal ready to sign, push to DMS); it lives at the demo store too
    {"id": "J4", "us": ("Elena", "Rossi"), "de": ("Elena", "Roth"), "stage": "AGREE",
     "model": {"us": "MER-VOLT-M", "de": "NW-AURIN"}, "powertrain": "EV", "dealer_idx": 0, "household": 2, "commute": 30,
     "objections": ["price_too_high", "interest_rate"], "competitor": {"us": ("Ampera", "Spark X"), "de": ("Ampera", "Spark X")},
     "handoff_chain": True, "documents": ["deal_worksheet", "fi_menu", "delivery_checklist"],
     "summary": "Ready to sign; payment objection live; competitive intel vs Spark X; push to DMS; docs to My Garage; 3-step handoff"},
    # J5 and J6 sit at the demo store (dealer_idx 0) with J1: the delivery-week and the
    # service-lane-to-showroom stories must be showable by the demo staff, who are all there.
    {"id": "J5", "us": ("Chidi", "Okafor"), "de": ("Emre", "Özdemir"), "stage": "DELIVER", "delivery_in_days": 3,
     "model": {"us": "MER-VOLT-M", "de": "NW-AURIN"}, "powertrain": "EV", "dealer_idx": 0, "household": 5, "commute": 18,
     "charger_install_pending": True, "summary": "Delivery week; prep checklist; charger install pending; delivered → OWN → survey"},
    {"id": "J6", "us": ("Sam", "Kowalski"), "de": ("Sabine", "Kowalski"), "stage": "OWN",
     "model": {"us": "MER-VOLT-C", "de": "NW-VELA"}, "powertrain": "EV", "dealer_idx": 0, "household": 1, "commute": 60,
     "owned": {"lease_months_left": 5, "annual_over_allowance": 4200, "equity": 2900, "service_due_days": 4,
               "service_stage": "INSPECT"},
     "summary": "EV owner with positive equity and lease-end in 5 months, in today's service lane with no open sales journey; "
                "Service Lane flag → warm handoff → new journey"},
    {"id": "J7", "us": ("Dana", "Whitfield"), "de": ("Daniel", "Winter"), "stage": "QUALIFY",
     "model": {"us": "MER-ATLASX", "de": "NW-SPREE"}, "powertrain": "ICE", "dealer_idx": 5, "household": 6, "commute": 25,
     "credit_question": True, "sms_opt_out_days_ago": 1, "nurture_touches": 2,
     "summary": "Asks about credit approval → handoff_to_human; opts out of SMS → channel paused"},
    {"id": "J8", "us": ("Riverside Logistics", "Fleet Desk"), "de": ("Spedition Falkenberg", "Fuhrpark"), "stage": "QUALIFY",
     "model": {"us": "MER-TRAILHAND", "de": "NW-HAVEL"}, "powertrain": "HEV", "dealer_idx": 7, "household": 0, "commute": 0,
     "fleet_account": True, "units": 24, "summary": "Fleet buyer linked to A6 account; program pricing; BiK / Section 179 talk track"},
    {"id": "J9", "us": ("Lucía", "Fernández"), "de": ("Lucia", "Fischer"), "stage": "DISCOVER",
     "model": {"us": "MER-VOLT-S", "de": "NW-STRATOS"}, "powertrain": "EV", "dealer_idx": 3, "household": 2, "commute": 40,
     "conquest": True, "current_vehicle": {"us": ("Ampera", "Spark S", 2020, 52000), "de": ("Ampera", "Spark S", 2020, 78000)},
     "summary": "Conquest campaign click; owns competitor; A1 similar-items; A5 attribution"},
]

COLD_COHORT_ID = "J10"

# Objection types present in strategies (existing 11 + 6 new)
OBJECTION_TYPES = ["price_too_high", "payment_too_high", "range_anxiety", "charging_access", "resale_value", "incentive_confusion",
                   "interest_rate", "trade_value_low", "timing_not_ready", "competitor_better", "trust_reliability",
                   "feature_missing", "tariff_price_increase"]

DISTRICT = {
    "us": {"district_id": "DIST-07", "name": "District 7 – Southeast", "region_id": "REG-SE", "region_name": "Southeast Region",
           "dm": "Casey Brennan", "regional": "Morgan Ellis"},
    "de": {"district_id": "DIST-SW", "name": "Bezirk Süd-West", "region_id": "REG-S", "region_name": "Region Süd",
           "dm": "Katrin Vogel", "regional": "Bernd Sailer"},
}

# Condition → shaping parameters used by lake/oem generators
CONDITIONS = {
    "csi_decline_lead_sla": {"csi_delta_3m": -16, "lead_sla_breach": 0.38, "obj_pct": 0.91, "days_supply": 48, "vs_py": -0.04},
    "high_days_supply_ev_aging": {"csi_delta_3m": -2, "lead_sla_breach": 0.12, "obj_pct": 0.88, "days_supply": 68, "vs_py": -0.06, "ev_aging": True},
    "volume_down_conquest_pressure": {"csi_delta_3m": -4, "lead_sla_breach": 0.15, "obj_pct": 0.78, "days_supply": 52, "vs_py": -0.22, "conquest": True},
    "top_performer": {"csi_delta_3m": +5, "lead_sla_breach": 0.04, "obj_pct": 1.12, "days_supply": 38, "vs_py": +0.14},
    "ev_cert_incomplete_noncompliant": {"csi_delta_3m": -1, "lead_sla_breach": 0.10, "obj_pct": 0.95, "days_supply": 45, "vs_py": +0.01, "ev_cert": "incomplete", "compliance": "non_compliant"},
    "compliance_conditional": {"csi_delta_3m": 0, "lead_sla_breach": 0.09, "obj_pct": 0.97, "days_supply": 44, "vs_py": +0.02, "compliance": "conditional"},
    "strong_weekend_weak_monday": {"csi_delta_3m": +2, "lead_sla_breach": 0.08, "obj_pct": 1.02, "days_supply": 41, "vs_py": +0.05, "weekend_strong": True},
    "normal": {"csi_delta_3m": 0, "lead_sla_breach": 0.09, "obj_pct": 0.99, "days_supply": 46, "vs_py": +0.02},
}

CAMPAIGNS = {
    "us": [
        {"id": "CMP-US-01", "name": "Ion M Launch – Paid Search", "platform": "google_search", "objective": "leads", "budget": 180000, "flag": "overspent_low_delivery"},
        {"id": "CMP-US-02", "name": "Conquest: Spark X Owners", "platform": "meta", "objective": "conquest", "budget": 95000, "flag": "conquest_source"},
        {"id": "CMP-US-03", "name": "Crosstrail Hybrid – Regional TV/CTV", "platform": "ctv", "objective": "awareness", "budget": 220000},
        {"id": "CMP-US-04", "name": "Owner Loyalty – Service Lane Email", "platform": "oem_email", "objective": "retention", "budget": 12000, "flag": "service_lane_reentry"},
        {"id": "CMP-US-05", "name": "Trailhand Tow Season – Social", "platform": "meta", "objective": "leads", "budget": 60000},
        {"id": "CMP-US-06", "name": "Labor Day APR Special", "platform": "google_search", "objective": "conversion", "budget": 75000},
    ],
    "de": [
        {"id": "CMP-DE-01", "name": "Aurin Markteinführung – Suche", "platform": "google_search", "objective": "leads", "budget": 160000, "flag": "overspent_low_delivery"},
        {"id": "CMP-DE-02", "name": "Eroberung: Model-Y-Fahrer", "platform": "meta", "objective": "conquest", "budget": 85000, "flag": "conquest_source"},
        {"id": "CMP-DE-03", "name": "Kora Hybrid – Regional-TV", "platform": "ctv", "objective": "awareness", "budget": 190000},
        {"id": "CMP-DE-04", "name": "Bestandskunden – Service-E-Mail", "platform": "oem_email", "objective": "retention", "budget": 10000, "flag": "service_lane_reentry"},
        {"id": "CMP-DE-05", "name": "Elbe Kombi – Familien-Social", "platform": "meta", "objective": "leads", "budget": 55000},
        {"id": "CMP-DE-06", "name": "E-Auto-Förderung 2026 – Aktion", "platform": "google_search", "objective": "conversion", "budget": 70000},
    ],
}
