"""OEM component (U4): the OEM_* tables the foundation/sales-spine doesn't emit.

Per OWNER_MAP, OEM owns 14 tables. Four are emitted upstream and left untouched:
OEM_Markets / OEM_StateRules (foundation, core.gen_org_users) and OEM_Catalog /
OEM_CatalogModels (sales spine, core.gen_catalog). This module owes the other 10:

    OEM_Accounts        district dealers + groups + fleet + prospects
    OEM_AccountPlans    draft/published plans (red-dealer v1->v2) + meeting briefs
    OEM_ContactReports  DM field-visit reports across the district
    OEM_Alerts          district-health alerts (open + resolved)
    OEM_Campaigns       OEM marketing campaigns per market
    OEM_Incentives      active/expired incentive programs (federal/state/oem)
    OEM_CIPrograms      continuous-improvement programs (red-dealer conditions)
    OEM_Comms           OEM->dealer bulletins (incl. GR-003/GR-006 boundary cases)
    OEM_Compliance      per-dealer compliance posture
    OEM_GuardrailEvents observed guardrail evaluations citing GR-001..GR-008

Every table emitted here is OEM_* per OWNER_MAP (BR-U4.1, enforced by _add). All
randomness is from g.rng (single seeded RNG, NFR1); dates are SEED_NOW offsets via
g.cfg.ago/ahead (NFR6); monetary/rate/allocation values carry a source_ref (NFR4);
two markets via g.m (NFR7). District shaping uses storylines.DISTRICT.

Registered as a single phase at band 30 (after commerce, before corpora/late).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from . import anchors as A
from . import phase_registry as _pr
from .config import iso
from .owner_map import owner_matches
from .platform import GUARDRAIL_IDS, GUARDRAIL_RULES
from .storylines import DISTRICT

if TYPE_CHECKING:  # pragma: no cover
    from .core import G

# rule_id -> configured action (block / require_approval / warn), from platform.py.
_GR_ACTION = {rid: action for rid, _name, action in GUARDRAIL_RULES}
_GR_NAME = {rid: name for rid, name, _action in GUARDRAIL_RULES}

# Continuous-improvement program archetypes keyed to storyline dealer conditions.
_CI_ARCHETYPES = {
    "csi_decline_lead_sla": ("Lead-response SLA recovery", "Restore <30-min lead response and reverse CSI decline"),
    "high_days_supply_ev_aging": ("EV aged-inventory turn", "Reduce EV days-supply and aged units"),
    "volume_down_conquest_pressure": ("Conquest volume recovery", "Recover sales volume under conquest pressure"),
}

_ALERT_KINDS = (
    ("lead_sla_breach", "Lead SLA breaches trending up", "csi_decline_lead_sla"),
    ("csi_decline", "CSI score declining month over month", "csi_decline_lead_sla"),
    ("ev_days_supply", "EV days-supply above target", "high_days_supply_ev_aging"),
    ("aged_inventory", "Aged units (>90 days) above threshold", "high_days_supply_ev_aging"),
    ("volume_gap", "Sales volume below district plan", "volume_down_conquest_pressure"),
    ("conquest_pressure", "Conquest share loss to competitor", "volume_down_conquest_pressure"),
)


def _add(g: "G", table: str, row: dict) -> dict:
    if not owner_matches(table):  # BR-U4.1 — U4 emits OEM_ tables only
        raise ValueError(f"oem.py may only emit OEM_ tables; got {table!r}")
    return g.add(table, row)


def _red_dealer_code(g: "G") -> str:
    """The district's red-condition dealer (csi_decline_lead_sla): D-101 / D-201."""
    for d in g.m.dealers:
        if d.condition == "csi_decline_lead_sla":
            return d.code
    return g.m.dealers[0].code


def _amount_field(mid: str) -> str:
    return "amount_usd" if mid == "us" else "amount_eur"


def gen_oem(g: "G") -> None:
    m, cfg, rng = g.m, g.cfg, g.rng
    mid = m.id
    d = DISTRICT[mid]
    dm_user = g.ix.get("oem_users", {}).get("oem_dm")
    dealers = list(m.dealers)
    dealer_codes = [dl.code for dl in dealers]
    red = _red_dealer_code(g)
    src = "spec:§3.5 OEM field sales; plan:§3.12"

    # ── OEM_Accounts: dealers as accounts + groups + fleet + prospects ───────
    for dl in dealers:
        health = 0.55 if dl.condition == "csi_decline_lead_sla" else round(rng.uniform(0.68, 0.92), 2)
        _csi_prior = rng.randint(860, 930)
        _csi_now = _csi_prior - rng.randint(28, 55) if dl.condition == "csi_decline_lead_sla" else _csi_prior + rng.randint(-8, 10)
        _add(g, "OEM_Accounts", {
            "account_id": g.nid("ACC"), "account_type": "dealer", "dealer_id": dl.code, "group_id": None,
            "name": dl.name, "district_id": d["district_id"], "region_id": d["region_id"], "market_id": mid,
            "tier": dl.tier, "condition": dl.condition,
            "sales_health_index": health, "service_health_index": round(min(1.0, health + rng.uniform(-0.05, 0.1)), 2),
            "days_supply": rng.randint(78, 140) if dl.condition == "high_days_supply_ev_aging" else rng.randint(38, 70),
            # CSI (OEM survey, 0–1000): the figure the GM cockpit shows and the CSI alert is about — one source,
            # so an alert never fires on a metric no screen can show. A declining store trails its prior month.
            "csi": {"score": _csi_now, "prior_month": _csi_prior, "responses": rng.randint(18, 60),
                    "as_of": iso(cfg.ago(days=rng.randint(2, 9)))},
            "source_ref": f"{src} (synthetic health indices; CSI = OEM owner survey roll-up)",
        })
    for gid, gname in (("DG-01", "Dealer Group 01"), ("DG-02", "Dealer Group 02")):
        _add(g, "OEM_Accounts", {
            "account_id": g.nid("ACC"), "account_type": "dealer_group", "dealer_id": None, "group_id": gid,
            "name": gname, "district_id": d["district_id"], "region_id": d["region_id"], "market_id": mid,
            "tier": "A", "condition": None, "sales_health_index": round(rng.uniform(0.7, 0.9), 2),
            "service_health_index": round(rng.uniform(0.7, 0.9), 2), "days_supply": rng.randint(45, 70),
            "source_ref": f"{src} (dealer group rollup)",
        })
    _add(g, "OEM_Accounts", {
        "account_id": g.nid("ACC"), "account_type": "fleet", "dealer_id": None, "group_id": None,
        "name": "Regional Fleet Account" if mid == "us" else "Regionaler Flottenkunde",
        "district_id": d["district_id"], "region_id": d["region_id"], "market_id": mid, "tier": "A",
        "condition": None, "sales_health_index": round(rng.uniform(0.72, 0.9), 2),
        "service_health_index": round(rng.uniform(0.72, 0.9), 2), "days_supply": None,
        "source_ref": f"{src} (fleet)",
    })
    for i in range(3):
        _add(g, "OEM_Accounts", {
            "account_id": g.nid("ACC"), "account_type": "prospect", "dealer_id": None, "group_id": None,
            "name": f"Prospect Dealer {i + 1}" if mid == "us" else f"Interessent Händler {i + 1}",
            "district_id": d["district_id"], "region_id": d["region_id"], "market_id": mid,
            "tier": rng.choice(("B", "C")), "condition": None, "sales_health_index": None,
            "service_health_index": None, "days_supply": None, "source_ref": f"{src} (open-point prospect)",
        })

    # ── OEM_AccountPlans: red-dealer v1 published + v2 revised, drafts + briefs ──
    def _plan(dealer_id, version, status, effective):
        return {
            "plan_id": g.nid("AP"), "dealer_id": dealer_id, "version": version, "status": status,
            "market_id": mid, "district_id": d["district_id"], "author_user_id": dm_user,
            "objectives": [
                {"kpi": "lead_sla_minutes", "target": 30, "source_ref": "spec:§3.1 lead SLA 30min"},
                {"kpi": "csi", "target": 0.9, "source_ref": f"{src} (district CSI target)"},
            ],
            "allocations": [{"program": "co-op marketing", "amount": 15000 if mid == "us" else 13000,
                             "source_ref": f"{src} (co-op allocation)"}],
            "effective_from": iso(effective) if effective else None,
            "publish_action": "a6.publish_account_plan", "publish_level": "act_with_approval",
            "source_ref": src,
        }
    _add(g, "OEM_AccountPlans", _plan(red, 1, "published", cfg.ago(days=45)))
    _add(g, "OEM_AccountPlans", _plan(red, 2, "published", cfg.ago(days=7)))  # revision
    for dl in dealers:
        if dl.code == red:
            continue
        _add(g, "OEM_AccountPlans", _plan(dl.code, 1, "draft", None))
    # meeting briefs (one per dealer visit)
    for dl in dealers:
        _add(g, "OEM_AccountPlans", {
            "plan_id": g.nid("BRIEF"), "dealer_id": dl.code, "version": 1, "status": "brief",
            "market_id": mid, "district_id": d["district_id"], "author_user_id": dm_user,
            "objectives": [{"kpi": "meeting", "target": "quarterly business review",
                            "source_ref": f"{src} (meeting brief)"}],
            "allocations": [], "effective_from": None, "publish_action": "a6.build_meeting_package",
            "publish_level": "recommend", "source_ref": f"{src} (meeting brief)",
        })

    # ── OEM_ContactReports: ~45 DM field-visit reports ───────────────────────
    for _ in range(45):
        dl = rng.choice(dealers)
        _add(g, "OEM_ContactReports", {
            "report_id": g.nid("CR"), "dealer_id": dl.code, "author_user_id": dm_user,
            "market_id": mid, "district_id": d["district_id"], "visit_type": rng.choice(("in_person", "call", "virtual")),
            "visit_ts": iso(cfg.ago(days=rng.randint(1, 90), hours=rng.randint(0, 8))),
            "summary": f"District visit to {dl.name}: reviewed sales pace and CSI.",
            "next_action": rng.choice(("follow up on lead SLA", "review aged EV units",
                                       "co-op plan check-in", "conquest offer review")),
            "sentiment": rng.choice(("positive", "neutral", "concern")), "source_ref": src,
        })

    # ── OEM_Alerts: ~28 open + ~40 resolved district-health alerts ───────────
    def _alert(status):
        # red dealer over-represented
        dl = red if rng.random() < 0.4 else rng.choice(dealer_codes)
        kind, title, _cond = rng.choice(_ALERT_KINDS)
        opened = cfg.ago(days=rng.randint(3, 120))
        return {
            "alert_id": g.nid("AL"), "dealer_id": dl, "market_id": mid, "district_id": d["district_id"],
            "kind": kind, "title": title, "severity": rng.choice(("low", "medium", "high")),
            "status": status, "opened_at": iso(opened),
            "resolved_at": iso(cfg.ago(days=rng.randint(0, 2))) if status == "resolved" else None,
            "source_ref": f"{src} (district health monitor)",
        }
    opened_now = [_alert("open") for _ in range(28)]
    # A health monitor keeps ONE open alert per store and condition; a repeat signal refreshes it
    # rather than raising a second card (the district saw five "Lead SLA" cards for one store).
    # Older repeats are recorded as superseded by the newest. RNG-free: the draws above are unchanged.
    newest: dict[tuple[str, str], dict] = {}
    for a in opened_now:
        k = (a["dealer_id"], a["kind"])
        if k not in newest or a["opened_at"] > newest[k]["opened_at"]:
            newest[k] = a
    for a in opened_now:
        keep = newest[(a["dealer_id"], a["kind"])]
        if a is not keep:
            a.update({"status": "resolved", "resolved_at": keep["opened_at"], "superseded_by": keep["alert_id"]})
        _add(g, "OEM_Alerts", a)
    for _ in range(40):
        _add(g, "OEM_Alerts", _alert("resolved"))

    # ── OEM_Incentives: real curated programs published to dealers ───────────
    amt = _amount_field(mid)
    for inc in A.incentives(mid):
        # the curated file qualifies a few names for provenance — "(generic template)", "(synthetic)";
        # a customer reads the program's name, the qualifier rides along as `provenance`
        name, _, qual = str(inc["name"]).rstrip(")").rpartition(" (")
        qualified = qual in ("synthetic", "generic template")
        _add(g, "OEM_Incentives", {
            "incentive_id": inc["incentive_id"], "tier": inc["tier"], "scope": inc["scope"],
            "name": name if qualified else inc["name"], "powertrains": inc["powertrains"], amt: inc.get(amt),
            **({"provenance": qual} if qualified else {}),
            "status": inc["status"], "valid_from": inc.get("valid_from"), "valid_to": inc.get("valid_to"),
            "market_id": mid, "source_ref": "; ".join(inc.get("source_urls", [])) or f"{src} (incentive)",
            # a captive-finance APR program carries its rate card (by term) and the credit tier it
            # requires, so the F&I desk can apply it to a lender offer instead of listing it as a
            # price incentive with no amount
            **({"apr_special": inc["apr_special"]} if inc.get("apr_special") else {}),
        })

    # ── OEM_Campaigns: 6 per market referencing models/incentives ────────────
    models = [r for r in g.t.get("OEM_CatalogModels", [])]
    active_inc = [i["incentive_id"] for i in A.incentives(mid) if i["status"] == "active"]
    for i in range(6):
        model = rng.choice(models) if models else None
        # District digital-media flights sized to the deliveries the pack attributes to them (a few
        # to a dozen per flight), so ROI reads as a mix of winners and losers rather than a loss on
        # every line. Same RNG draws as before (only the values changed), so nothing downstream drifts.
        # a district digital flight runs tens of thousands, so the cost per attributed delivery lands in
        # the hundreds-to-low-thousands a marketer recognises (an $8k budget made it $149, and a shift of
        # $1,600 "bought" 11 deliveries)
        budget = rng.choice((18000, 28000, 40000)) if mid == "us" else rng.choice((16000, 25000, 36000))
        cid = g.nid("CMP")
        short = str(model["name"]).split(" ", 1)[1] if model and " " in str(model.get("name", "")) else (model or {}).get("name")
        objective = rng.choice(("conquest", "loyalty", "ev_adoption", "service_retention"))
        theme = {"conquest": "conquest", "loyalty": "loyalty", "ev_adoption": "EV test-drive", "service_retention": "owner retention"}[objective]
        name = f"Q4 {short} {theme}" if short else f"Seasonal campaign {i + 1}"
        taken_names = {c_["name"] for c_ in g.t.get("OEM_Campaigns", [])}
        if name in taken_names:
            name = f"{name} (wave {sum(1 for n in taken_names if n.startswith(name)) + 1})"
        incentive_ids = rng.sample(active_inc, min(len(active_inc), 2)) if active_inc else []
        audience = rng.choice(("in_market_ev", "lease_maturity", "service_due", "conquest_competitive"))
        start_at = iso(cfg.ago(days=rng.randint(5, 30)))
        end_at = iso(cfg.ahead(days=rng.randint(20, 60)))
        rng.choice(("active", "scheduled"))   # kept draw: every flight here has already started, so it is live
        _add(g, "OEM_Campaigns", {
            "campaign_id": cid, "market_id": mid, "district_id": d["district_id"],
            "name": name, "objective": objective,
            "model_id": model.get("model_id") if model else None,
            "incentive_ids": incentive_ids, "audience": audience,
            "budget": budget, "start_at": start_at, "end_at": end_at, "status": "active",
            "source_ref": f"{src} (campaign budget synthetic)",
        })

    # ── OEM_CIPrograms: ~10 continuous-improvement programs ──────────────────
    n_ci = 0
    for dl in dealers:
        arche = _CI_ARCHETYPES.get(dl.condition)
        if not arche:
            continue
        name, goal = arche
        _add(g, "OEM_CIPrograms", {
            "program_id": g.nid("CIP"), "dealer_id": dl.code, "market_id": mid, "district_id": d["district_id"],
            "name": name, "goal": goal, "condition": dl.condition, "status": "active",
            "owner_user_id": g.ix.get("oem_users", {}).get("oem_ci_planning"),
            "started_at": iso(cfg.ago(days=rng.randint(20, 90))), "source_ref": f"{src} (CI program)",
        })
        n_ci += 1
    # district-wide programs to reach ~10
    for label in ("Conquest playbook rollout", "EV service readiness", "Digital retail adoption",
                  "CSI coaching cohort")[: max(0, 10 - n_ci)]:
        _add(g, "OEM_CIPrograms", {
            "program_id": g.nid("CIP"), "dealer_id": None, "market_id": mid, "district_id": d["district_id"],
            "name": label, "goal": f"District initiative: {label.lower()}", "condition": None, "status": "active",
            "owner_user_id": g.ix.get("oem_users", {}).get("oem_ci_planning"),
            "started_at": iso(cfg.ago(days=rng.randint(20, 120))), "source_ref": f"{src} (district CI program)",
        })

    # ── OEM_Comms: 12 bulletins per market; 2 are GR boundary test cases ─────
    comms_rows = []
    # Deterministic boundary bulletins: text that would trip GR-003 (pricing
    # guidance) and GR-006 (legal/regulatory interpretation) — used to exercise
    # the guardrail check. These are marked so downstream can assert on them.
    boundary = [
        ("GR-003", "Recommended retail pricing for the new EV trim",
         "Set the customer price at exactly the figure below for every deal this month."),
        ("GR-006", "Interpreting the new emissions regulation",
         "Our legal reading is that dealers are exempt from the new statute; act accordingly."),
    ]
    for rid, subject, body in boundary:
        comms_rows.append({
            "comm_id": g.nid("COMM"), "market_id": mid, "district_id": d["district_id"],
            "audience": "all_dealers", "subject": subject, "body": body, "channel": "bulletin",
            "sent_at": iso(cfg.ago(days=rng.randint(2, 20))), "gr_boundary_rule_id": rid,
            "source_ref": f"{src} (guardrail boundary test bulletin)",
        })
    ordinary_subjects = ("Q4 incentive summary", "EV charging partner update", "Service parts availability",
                         "New model launch timing", "CSI program reminder", "Co-op marketing deadline",
                         "Training webinar invite", "Conquest offer refresh", "Recall action notice",
                         "Holiday hours guidance")
    for i in range(10):
        comms_rows.append({
            "comm_id": g.nid("COMM"), "market_id": mid, "district_id": d["district_id"],
            "audience": rng.choice(("all_dealers", "dealer_group", "single_dealer")),
            "subject": ordinary_subjects[i], "body": f"{ordinary_subjects[i]} — details enclosed.",
            "channel": "bulletin", "sent_at": iso(cfg.ago(days=rng.randint(1, 40))),
            "gr_boundary_rule_id": None, "source_ref": f"{src} (OEM bulletin)",
        })
    for row in comms_rows:
        _add(g, "OEM_Comms", row)

    # ── OEM_Compliance: ~8 rows per dealer ───────────────────────────────────
    areas = ("brand_standards", "data_privacy", "advertising", "sales_disclosure", "finance_disclosure",
             "safety_recall", "ev_charging_cert", "warranty_admin")
    for dl in dealers:
        for area in areas:
            _add(g, "OEM_Compliance", {
                "compliance_id": g.nid("CMPL"), "dealer_id": dl.code, "market_id": mid,
                "district_id": d["district_id"], "area": area,
                "status": rng.choices(("compliant", "action_needed", "overdue"), [80, 15, 5])[0],
                "last_reviewed_at": iso(cfg.ago(days=rng.randint(5, 180))), "source_ref": f"{src} (compliance posture)",
            })

    # ── OEM_GuardrailEvents: ~30 observed evaluations citing GR-001..GR-008 ──
    boundary_by_rule = {r["gr_boundary_rule_id"]: r for r in comms_rows if r["gr_boundary_rule_id"]}
    agents_tools = (("A6", "draft_account_plan"), ("A6", "forward_comms"), ("A5", "publish_campaign"),
                    ("A6", "run_district_cycle"), ("A5", "build_audience"))
    # First, one event per boundary bulletin (referential consistency, test #5).
    for rid, comm in boundary_by_rule.items():
        action = _GR_ACTION[rid]
        _add(g, "OEM_GuardrailEvents", {
            "event_id": g.nid("GRE"), "market_id": mid, "district_id": d["district_id"],
            "rule_id": rid, "rule_name": _GR_NAME[rid], "action": action,
            "agent_id": "A6", "tool": "forward_comms", "comm_id": comm["comm_id"],
            "outcome": "blocked" if action == "block" else ("approval_required" if action == "require_approval" else "warned"),
            "detail": f"Guardrail {rid} triggered on bulletin {comm['comm_id']}",
            "evaluated_at": comm["sent_at"], "source_ref": f"{src}; spec:§3.6.1 {rid}",
        })
    # Then background events across the rule set.
    for _ in range(30 - len(boundary_by_rule)):
        rid = rng.choice(GUARDRAIL_IDS)
        action = _GR_ACTION[rid]
        agent, tool = rng.choice(agents_tools)
        _add(g, "OEM_GuardrailEvents", {
            "event_id": g.nid("GRE"), "market_id": mid, "district_id": d["district_id"],
            "rule_id": rid, "rule_name": _GR_NAME[rid], "action": action,
            "agent_id": agent, "tool": tool, "comm_id": None,
            "outcome": "blocked" if action == "block" else ("approval_required" if action == "require_approval" else "warned"),
            "detail": f"Guardrail {rid} evaluated during {agent}.{tool}",
            "evaluated_at": iso(cfg.ago(days=rng.randint(1, 60), hours=rng.randint(0, 12))),
            "source_ref": f"{src}; spec:§3.6.1 {rid}",
        })


# ── Phase registration (U4 phase-registry refactor) ─────────────────────────
# OEM runs at band 30 — after commerce (26), before corpora (40) and platform-late (90).
_pr.register(_pr.BAND_OEM, gen_oem)  # 30
