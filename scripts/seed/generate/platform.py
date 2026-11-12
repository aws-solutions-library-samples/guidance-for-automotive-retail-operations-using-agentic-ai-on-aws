"""Platform component (U2): generators for the PLAT_* tables per OWNER_MAP.

Two phases plug into the orchestrator's PHASES list (ordered callables f(g)):

  gen_platform_config(g)  — early (right after G.gen_org_users). The versioned
      config family: PLAT_Config, PLAT_ConfigAutonomy (v1/v2/v3 — the day-14
      observation-fortnight flip), PLAT_ConfigNurture (v1 active + v2 draft),
      PLAT_ConfigNotifications, PLAT_ConfigSuggestions, PLAT_ConfigGuardrails
      (GR-001..GR-008 + A7 scare-language denylist). References only market/org
      entities. Emits NO PLAT_KnowledgeCollections rows (U5 is the sole writer;
      U2 publishes the KNOWLEDGE_COLLECTIONS vocabulary).

  gen_platform_late(g)    — LAST generation phase (after Storylines). PLAT_EntityMap
      (customer/dealer/deal/repair_order x crm/dms/cdp + 12 deterministic
      merge-queue rows) and PLAT_AgentTraces (golden J1-J9 + S1-S8). Guarded: any
      absent upstream table is skipped and reported, never stubbed.

Shapes follow the spec §4 key schema. Every config-family row carries
config_key / version / is_active / market_id; the spec is internally inconsistent
on PLAT_ConfigAutonomy (one row per action vs PK config_key + SK version), so each
row ALSO carries a deterministic composite `item_key` that U7 uses as the load-time
PK (contract-summary C1 note). All randomness comes from g.rng (single seeded RNG,
NFR1); all dates are SEED_NOW offsets via g.cfg.ago/ahead (NFR6).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .config import STAGES, iso
from .owner_map import owner_matches
from .storylines import COLD_COHORT_ID, JOURNEYS, OBJECTION_TYPES

if TYPE_CHECKING:  # pragma: no cover
    from .core import G

# ─────────────────────────────────────────────────────────────────────────────
# Constants (BR6.6, BR6.7, BR6.8, BR7.4, BR7.7)
# ─────────────────────────────────────────────────────────────────────────────

#: Spec §4.10.1 closed autonomy-level enum (BR6.6).
LEVELS = ("observe", "recommend", "act_with_approval", "act_and_notify")

#: Hard rules are NOT autonomy-configurable and never appear as rows (BR6.7).
HARD_RULE_ACTIONS = frozenset({
    "consent.enforce", "quiet_hours.enforce", "guardrails.gr_check",
    "estimate.customer_approval", "budget.auto_allocate", "incentive.auto_allocate",
    "technician.data_masking",
})

#: Action registry — constant until agents/common/actions.json ships (BR6.8).
#: (action_id, default_level_in_v2, undoable). ~45 non-hard-rule actions, by agent.
ACTION_REGISTRY: tuple[tuple[str, str, bool], ...] = (
    # A1 — vehicle match / discovery
    ("a1.match_vehicles", "recommend", True), ("a1.match_touch", "recommend", True),
    ("a1.build_shortlist", "recommend", True), ("a1.explain_fit", "recommend", True),
    ("a1.compare_competitors", "recommend", True), ("a1.suggest_package", "recommend", True),
    # A2 — nurture / next-best-action (nurture sends ship act_and_notify per spec)
    ("a2.plan_nurture", "recommend", True), ("a2.compose_message", "act_and_notify", True),
    ("a2.nudge", "act_and_notify", True), ("a2.schedule_outreach", "recommend", True),
    ("a2.book_test_drive", "recommend", True), ("a2.answer_customer_question", "recommend", True),
    ("a2.select_next_best_action", "recommend", True), ("a2.handoff", "recommend", True),
    # A3 — floor suggestions / objections
    ("a3.generate_contextual_suggestions", "recommend", True), ("a3.classify_objection", "recommend", True),
    ("a3.suggest_response", "recommend", True), ("a3.precompute_suggestions", "recommend", True),
    ("a3.retrieve_context", "recommend", True),
    # A4 — equity / acquisition (acquisition stays recommend per spec)
    ("a4.detect_equity", "recommend", True), ("a4.value_trade", "recommend", True),
    ("a4.propose_acquisition", "recommend", True), ("a4.flag_upgrade_timing", "recommend", True),
    # A5 — marketing / analytics (publishing = act_with_approval)
    ("a5.build_audience", "recommend", True), ("a5.publish_campaign", "act_with_approval", False),
    ("a5.shift_budget", "act_with_approval", True), ("a5.ask_quick", "recommend", True),
    ("a5.get_personalize_segments", "recommend", True),
    # A6 — OEM field sales (publishing = act_with_approval)
    ("a6.draft_account_plan", "recommend", True), ("a6.publish_account_plan", "act_with_approval", False),
    ("a6.run_district_cycle", "recommend", True), ("a6.simulate_scenario", "recommend", True),
    ("a6.build_meeting_package", "recommend", True), ("a6.forward_comms", "recommend", True),
    ("a6.get_compliance", "recommend", True),
    # A7 — service (morning work act_and_notify; reforecast is day-time → recommend)
    ("a7.dispatch_ro", "act_and_notify", True), ("a7.stage_parts", "act_and_notify", True),
    ("a7.auto_present_estimate", "act_and_notify", True), ("a7.reforecast_promise", "recommend", True),
    ("a7.prioritize_findings", "recommend", True), ("a7.record_inspection", "recommend", True),
    ("a7.book_service", "recommend", True), ("a7.send_due_service_reminder", "act_and_notify", True),
    ("a7.notify_vehicle_ready", "act_and_notify", True), ("a7.explain_finding", "recommend", True),
    ("a7.check_coverage", "recommend", True), ("a7.lookup_dtc", "recommend", True),
)

#: A7 morning-work actions raised to act_and_notify in v2 (spec §4.10.1).
A7_MORNING_WORK = ("a7.dispatch_ro", "a7.stage_parts", "a7.auto_present_estimate")

#: The ONLY actions allowed a non-`recommend` level in v2 (BR6.4 catch-all: all others recommend).
#: Justified by spec §4.10.1 shipped defaults + plan §3.11: A7 morning work, A5/A6 publishing
#: (+ A5 budget shift), A2 nurture sends, A7 reminders / ready notifications.
V2_NON_RECOMMEND = frozenset({
    *A7_MORNING_WORK,
    "a5.publish_campaign", "a5.shift_budget", "a6.publish_account_plan",
    "a2.compose_message", "a2.nudge",
    "a7.send_due_service_reminder", "a7.notify_vehicle_ready",
})

#: Spec §3.6.1 guardrail rules — exactly GR-001..GR-008 (BR7.4).
GUARDRAIL_RULES: tuple[tuple[str, str, str], ...] = (
    ("GR-001", "Wholesale reduction recommendations", "block"),
    ("GR-002", "Dealer termination / separation language", "block"),
    ("GR-003", "Specific pricing guidance to dealers", "block"),
    ("GR-004", "Personnel hiring / firing recommendations", "block"),
    ("GR-005", "Allocation promises or commitments", "block"),
    ("GR-006", "Legal / regulatory interpretations", "require_approval"),
    ("GR-007", "Financial advice beyond standard metrics", "require_approval"),
    ("GR-008", "Competitive disparagement", "warn"),
)
GUARDRAIL_IDS = tuple(r[0] for r in GUARDRAIL_RULES)

#: A7 scare-language denylist per market language (plan §3.9; R-16).
SCARE_LANGUAGE_DENYLIST = {
    "us": ["dangerous to drive", "could fail any moment", "you must fix this today",
           "unsafe for your family", "catastrophic failure", "will leave you stranded"],
    "de": ["lebensgefährlich", "kann jederzeit ausfallen", "muss heute repariert werden",
           "unsicher für Ihre Familie", "Totalausfall", "bleiben Sie liegen"],
}

#: Service objection types (U2 constant; U6 may fold into storylines.py).
SERVICE_OBJECTION_TYPES = ("repair_cost", "can_it_wait", "second_opinion", "warranty_dispute")

#: Service nurture triggers (plan service template family) (BR7.3).
SERVICE_TRIGGERS = ("due_service", "declined_reminder_30d", "declined_reminder_60d", "declined_reminder_90d",
                    "estimate_presented", "vehicle_ready", "promise_moved", "csi_survey", "recall")

#: Knowledge-collection vocabulary U5 must use as collection_id/kind (BR7.7).
KNOWLEDGE_KINDS = ("crm_notes", "bulletins", "product_guides", "prior_plans", "repair_procedures",
                   "tsbs", "transcripts", "templates", "objection_strategies")


def knowledge_collections(market_id: str) -> list[dict]:
    """The fixed collection vocabulary for a market (no rows are emitted by U2)."""
    titles = {
        "crm_notes": "CRM notes", "bulletins": "Incentive & product bulletins", "product_guides": "Product guides",
        "prior_plans": "Prior account plans & reports", "repair_procedures": "Repair procedures",
        "tsbs": "Technical service bulletins", "transcripts": "Call & chat transcripts",
        "templates": "Message templates", "objection_strategies": "Objection strategy scripts",
    }
    return [{"collection_id": f"KC-{market_id.upper()}-{k}", "kind": k, "title": titles[k],
             "market_id": market_id, "index_target": "quick_index"} for k in KNOWLEDGE_KINDS]


KNOWLEDGE_COLLECTIONS = {m: knowledge_collections(m) for m in ("us", "de")}

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _item_key(*parts: str | None) -> str:
    """Deterministic composite load-time PK (R-14 resolution)."""
    return "#".join("-" if p is None else str(p) for p in parts)


def _add(g: "G", table: str, row: dict) -> dict:
    if not owner_matches(table):  # BR5.1 — U2 emits PLAT_ tables only
        raise ValueError(f"platform.py may only emit PLAT_ tables; got {table!r}")
    return g.add(table, row)


def _red_dealer_code(g: "G") -> str:
    """The district's red-condition dealer (csi_decline_lead_sla): D-101 / D-201."""
    for d in g.m.dealers:
        if d.condition == "csi_decline_lead_sla":
            return d.code
    return g.m.dealers[0].code


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — config family (BR5.2)
# ─────────────────────────────────────────────────────────────────────────────

def gen_platform_config(g: "G") -> None:
    m, cfg = g.m, g.cfg
    mid = m.id
    src = "curated:platform_config"

    # PLAT_Config — tenant settings, v1 active (BR7.1)
    _add(g, "PLAT_Config", {
        "item_key": _item_key("tenant", mid), "config_key": "tenant", "version": 1, "market_id": mid,
        "is_active": True, "effective_from": iso(cfg.ago(days=14)),
        "quiet_hours_default": {"start": "20:00", "end": "08:00", "tz": m.tz},
        "lead_sla_minutes": 30, "autonomy_observation_days": 14,
        "source_ref": "spec:§3.1 lead SLA 30min; plan:§3.11 observation fortnight",
    })

    # PLAT_ConfigAutonomy — v1 / v2 / v3 flat rows (BR6.1–BR6.7)
    red = _red_dealer_code(g)
    for action_id, v2_level, undoable in ACTION_REGISTRY:
        if action_id in HARD_RULE_ACTIONS:
            raise ValueError(f"hard-rule action {action_id!r} must not be in ACTION_REGISTRY (BR6.7)")
        base = {"config_key": "autonomy", "market_id": mid, "action_id": action_id, "undoable": undoable,
                "source_ref": "spec:§4.10.1; plan:§3.11"}
        # v1 — observation fortnight, every action recommend, SEED_NOW is day 14 (BR6.3)
        # item_key includes market_id so US and DE tenant rows never collide (R-02).
        _add(g, "PLAT_ConfigAutonomy", {**base, "item_key": _item_key("autonomy", mid, "tenant", None, action_id),
             "version": 1, "scope": "tenant", "scope_id": None, "level": "recommend", "constraints": None,
             "is_active": True, "effective_from": iso(cfg.ago(days=14))})
        # v2 — scheduled at SEED_NOW+1d with pilot defaults + plan constraints (BR6.4)
        constraints = None
        if action_id == "a7.stage_parts":
            constraints = {"confidence_min": 0.8, "on_hand": 0, "daily_cap": 2500 if mid == "us" else 2200,
                           "source_ref": "plan:§3.11 trailing-30-day parts spend"}
        elif action_id == "a7.auto_present_estimate":
            constraints = {"max_amount": 750 if mid == "us" else 650, "source_ref": "plan:§3.11"}
        elif action_id in ("a2.compose_message", "a2.nudge", "a7.send_due_service_reminder"):
            constraints = {"requires_consent": True, "quiet_hours": True, "channels": ["email", "sms"]}
        _add(g, "PLAT_ConfigAutonomy", {**base, "item_key": _item_key("autonomy", mid, "tenant", None, action_id),
             "version": 2, "scope": "tenant", "scope_id": None, "level": v2_level, "constraints": constraints,
             "is_active": False, "effective_from": iso(cfg.ahead(days=1))})
        # v3 — dealer-scoped draft for the red dealer; raises a1.match_touch (BR6.5)
        v3_level = "act_and_notify" if action_id == "a1.match_touch" else v2_level
        _add(g, "PLAT_ConfigAutonomy", {**base, "item_key": _item_key("autonomy", mid, "dealer_id", red, action_id),
             "version": 3, "scope": "dealer_id", "scope_id": red, "level": v3_level, "constraints": constraints,
             "is_active": False, "effective_from": None})

    # PLAT_ConfigNurture — v1 active + v2 draft (BR7.2, BR7.3, BR7.6)
    templates = _nurture_templates(g)
    caps = {"per_day": 1, "per_week": 3, "per_channel_week": {"email": 2, "sms": 1, "chat": 3},
            "source_ref": "curated:nurture_cadence_caps (industry 1/day 3/week)"}
    for version, active in ((1, True), (2, False)):
        tpl = templates if version == 1 else _draft_variant(g, templates)
        _add(g, "PLAT_ConfigNurture", {
            "item_key": _item_key("nurture", mid), "config_key": "nurture", "version": version, "market_id": mid,
            "is_active": active, "effective_from": iso(cfg.ago(days=30)) if active else None,
            "templates": tpl, "cadence_caps": caps, "source_ref": "plan:§3.9 nurture templates",
        })

    # PLAT_ConfigNotifications — v1 active
    roles_dealer = ["dealer_manager", "dealer_advisor", "dealer_bdc", "dealer_fni", "dealer_service"]
    rules = [
        {"event": "lead.received", "roles": ["dealer_bdc", "dealer_advisor"], "channels": ["push", "email"], "quiet_hours_respect": False},
        {"event": "lead.sla_breach", "roles": ["dealer_manager"], "channels": ["push"], "quiet_hours_respect": False},
        {"event": "estimate.presented", "roles": ["dealer_service"], "channels": ["push"], "quiet_hours_respect": True},
        {"event": "parts.received", "roles": ["dealer_service"], "channels": ["push"], "quiet_hours_respect": True},
        {"event": "vehicle.ready", "roles": ["dealer_service", "dealer_advisor"], "channels": ["push", "email"], "quiet_hours_respect": True},
        {"event": "approval.requested", "roles": ["dealer_manager"], "channels": ["push", "email"], "quiet_hours_respect": True},
        {"event": "handoff.created", "roles": roles_dealer, "channels": ["push"], "quiet_hours_respect": True},
        {"event": "guardrail.blocked", "roles": ["admin_tenant"], "channels": ["email"], "quiet_hours_respect": False},
    ]
    _add(g, "PLAT_ConfigNotifications", {
        "item_key": _item_key("notifications", mid), "config_key": "notifications", "version": 1, "market_id": mid,
        "is_active": True, "effective_from": iso(cfg.ago(days=30)), "rules": rules,
        "source_ref": "spec:§2.6.6 notification rules",
    })

    # PLAT_ConfigSuggestions — v1 active (BR7.6)
    _add(g, "PLAT_ConfigSuggestions", {
        "item_key": _item_key("suggestions", mid), "config_key": "suggestions", "version": 1, "market_id": mid,
        "is_active": True, "effective_from": iso(cfg.ago(days=30)),
        "objection_types": list(OBJECTION_TYPES) + list(SERVICE_OBJECTION_TYPES),
        # Sales stages (config.STAGES) where A3 precomputes, plus the two service-journey
        # stages (spec §2.1.1 BOOK/CHECK_IN) where the plan pre-computes service-lane upsell briefs.
        "precompute_stages": ["DISCOVER", "CONFIGURE", "QUALIFY", "BOOK", "CHECK_IN"],
        "precompute_stages_source": "spec:§3.3 (sales stages); spec:§2.1.1 + plan:§3.13 service-lane upsell briefs (BOOK, CHECK_IN)",
        "confidence_threshold": 0.72, "source_ref": "spec:§3.3 A3 suggestion matrix; curated threshold",
    })

    # PLAT_ConfigGuardrails — v1 active, exactly GR-001..GR-008 + denylist (BR7.4, R-16)
    _add(g, "PLAT_ConfigGuardrails", {
        "item_key": _item_key("guardrails", mid), "config_key": "guardrails", "version": 1, "market_id": mid,
        "is_active": True, "effective_from": iso(cfg.ago(days=30)),
        "rules": [{"rule_id": rid, "name": name, "action": action} for rid, name, action in GUARDRAIL_RULES],
        "scare_language_denylist": SCARE_LANGUAGE_DENYLIST[mid],
        "source_ref": "spec:§3.6.1 GR-001..GR-008; plan:§3.9 A7 denylist",
    })
    # NOTE: no PLAT_KnowledgeCollections rows here — U5 is the sole writer (BR7.7).


def _nurture_templates(g: "G") -> list[dict]:
    """Sales templates per STAGES x channel + the service family, in the market language (BR7.3)."""
    m = g.m
    de = m.id == "de"
    out: list[dict] = []
    sales_intents = {
        "DISCOVER": ("welcome", "Welcome, {first_name} — let's find your {brand}" if not de else "Willkommen, {first_name} – finden wir Ihren {brand}"),
        "CONFIGURE": ("build_saved", "Your saved {model} build is ready to revisit" if not de else "Ihre gespeicherte {model}-Konfiguration wartet"),
        "QUALIFY": ("prequal", "A quick pre-qualification could clarify your monthly" if not de else "Eine kurze Vorqualifizierung klärt Ihre Monatsrate"),
        "FINANCE": ("offer_ready", "Your {model} finance offer is ready" if not de else "Ihr Finanzierungsangebot für den {model} ist fertig"),
        "TRADE_IN": ("trade_value", "Here's what your {trade_make} {trade_model} is worth today" if not de else "So viel ist Ihr {trade_make} {trade_model} heute wert"),
        "AGREE": ("deal_summary", "Your {model} deal summary and next steps" if not de else "Ihre Vertragsübersicht für den {model}"),
        "DELIVER": ("delivery_prep", "Getting your {model} ready for delivery on {delivery_date}" if not de else "Ihr {model} wird für die Übergabe am {delivery_date} vorbereitet"),
        "OWN": ("welcome_owner", "Welcome to ownership, {first_name}" if not de else "Willkommen als {brand}-Fahrer, {first_name}"),
    }
    channels = ("email", "sms", "chat")
    for stage in STAGES:
        intent, subject = sales_intents[stage]
        for ch in channels:
            body = (f"Hi {{first_name}}, {subject.lower()}. Your advisor {{advisor_name}} at {{dealer_name}} is here to help."
                    if not de else f"Hallo {{first_name}}, {subject.lower()}. Ihr Berater {{advisor_name}} bei {{dealer_name}} hilft gern.")
            if ch == "sms":
                body = body[:140]
            out.append({"template_id": f"T-{m.id.upper()}-{stage}-{ch}", "family": "sales", "stage_or_trigger": stage,
                        "channel": ch, "intent": intent, "subject": subject if ch == "email" else None, "body": body})
    service_subjects = {
        "due_service": ("Your {model} is due for service" if not de else "Ihr {model} ist zum Service fällig"),
        "declined_reminder_30d": ("About the {item} we recommended last month" if not de else "Zur empfohlenen Arbeit ({item}) vom letzten Monat"),
        "declined_reminder_60d": ("A friendly reminder on {item}" if not de else "Eine freundliche Erinnerung zu {item}"),
        "declined_reminder_90d": ("Still time to take care of {item}" if not de else "Noch Zeit, sich um {item} zu kümmern"),
        "estimate_presented": ("Your service estimate is ready to review" if not de else "Ihr Kostenvoranschlag ist bereit"),
        "vehicle_ready": ("Your {model} is ready for pickup" if not de else "Ihr {model} ist abholbereit"),
        "promise_moved": ("An update on your {model}'s ready time" if not de else "Aktualisierte Fertigstellung Ihres {model}"),
        "csi_survey": ("How did we do, {first_name}?" if not de else "Wie zufrieden waren Sie, {first_name}?"),
        "recall": ("Important: a safety recall applies to your {model}" if not de else "Wichtig: Rückruf für Ihren {model}"),
    }
    for trig in SERVICE_TRIGGERS:
        subject = service_subjects[trig]
        for ch in ("email", "sms"):
            body = (f"Hi {{first_name}}, {subject.lower()}. Reply or call {{dealer_name}} to schedule."
                    if not de else f"Hallo {{first_name}}, {subject.lower()}. Antworten Sie oder rufen Sie {{dealer_name}} an.")
            out.append({"template_id": f"T-{m.id.upper()}-SVC-{trig}-{ch}", "family": "service", "stage_or_trigger": trig,
                        "channel": ch, "intent": trig, "subject": subject if ch == "email" else None, "body": body})
    return out


def _draft_variant(g: "G", templates: list[dict]) -> list[dict]:
    """v2 draft: copy with a few bodies edited (deterministic pick)."""
    out = [dict(t) for t in templates]
    for i in sorted(g.rng.sample(range(len(out)), 4)):
        out[i]["body"] = out[i]["body"] + (" (draft v2)" if g.m.id == "us" else " (Entwurf v2)")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — platform-late: EntityMap + AgentTraces (BR5.3, BR8.x)
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEMS_FOR = {"customer": ("crm", "cdp"), "dealer": ("crm", "dms"), "deal": ("crm", "dms"), "repair_order": ("dms",)}
_PK_FIELD = {"customer": "customer_id", "deal": "deal_id", "repair_order": "ro_id"}
_TABLE_FOR = {"customer": "DEALER_Customers", "deal": "DEALER_Deals", "repair_order": "DEALER_RepairOrders"}

TOOLS_BY_AGENT = {
    "A1": ("match_vehicles", "explain_fit", "compare_competitors"),
    "A2": ("plan_nurture", "compose_message", "select_next_best_action", "book_test_drive"),
    "A3": ("generate_contextual_suggestions", "classify_objection", "retrieve_context"),
    "A4": ("detect_equity", "value_trade", "flag_upgrade_timing"),
    "A5": ("build_audience", "ask_quick"),
    "A6": ("draft_account_plan", "run_district_cycle", "simulate_scenario"),
    "A7": ("prioritize_findings", "stage_parts", "auto_present_estimate", "reforecast_promise", "explain_finding", "check_coverage"),
}
PURCHASE_AGENTS = ("A1", "A2", "A3", "A4")
SERVICE_AGENTS = ("A7", "A3", "A2", "A4")


def gen_platform_late(g: "G") -> dict:
    """EntityMap + AgentTraces over the populated accumulator. Returns a shortfall report."""
    m, cfg, rng = g.m, g.cfg, g.rng
    mid = m.id
    shortfall: dict[str, str] = {}

    # ── EntityMap (BR8.1, BR8.2) ────────────────────────────────────────────
    def emit_map(entity_type: str, app_id: str, system: str, external_id: str, confidence: float, duplicate_of: str | None = None):
        _add(g, "PLAT_EntityMap", {
            "item_key": _item_key(entity_type, app_id), "entity_type": entity_type, "app_id": app_id,
            "system": system, "external_id": external_id, "confidence": round(confidence, 2),
            "duplicate_of": duplicate_of, "market_id": mid, "source_ref": "synthetic:entity_map",
        })

    # dealers come from ix["dealer"] (always present after gen_org_users)
    for code in g.ix.get("dealer", {}):
        for s in _SYSTEMS_FOR["dealer"]:
            emit_map("dealer", code, s, _det_ext(rng, s, code), rng.uniform(0.9, 1.0))
    customers = g.t.get("DEALER_Customers")
    for etype in ("customer", "deal", "repair_order"):
        rows = g.t.get(_TABLE_FOR[etype])
        if not rows:
            shortfall[etype] = f"{_TABLE_FOR[etype]} absent — {etype} mappings skipped"
            continue
        pk = _PK_FIELD[etype]
        for row in rows:
            for s in _SYSTEMS_FOR[etype]:
                emit_map(etype, row[pk], s, _det_ext(rng, s, row[pk]), rng.uniform(0.9, 1.0))
    # 12 merge-queue rows: customer B collides with A's crm external_id (BR8.2)
    if customers and len(customers) >= 24:
        picks = rng.sample(customers, 24)
        crm_by_id = {r["app_id"]: r for r in g.t["PLAT_EntityMap"] if r["entity_type"] == "customer" and r["system"] == "crm"}
        for a, b in zip(picks[0::2], picks[1::2]):
            a_id, b_id = a["customer_id"], b["customer_id"]
            # replace B's primary crm row with a colliding low-confidence one
            g.t["PLAT_EntityMap"] = [r for r in g.t["PLAT_EntityMap"]
                                     if not (r["entity_type"] == "customer" and r["app_id"] == b_id and r["system"] == "crm")]
            emit_map("customer", b_id, "crm", crm_by_id[a_id]["external_id"], rng.uniform(0.35, 0.59), duplicate_of=a_id)

    # ── AgentTraces (BR8.4, BR8.5) ──────────────────────────────────────────
    journeys = g.t.get("DEALER_Journeys")
    jevents = g.t.get("DEALER_JourneyEvents")
    sjourneys = g.t.get("DEALER_ServiceJourneys")
    sevents = g.t.get("DEALER_ServiceJourneyEvents")
    golden_ids = {j["id"] for j in JOURNEYS if j["id"] != COLD_COHORT_ID}
    n_traces = 0

    resolve_level = _autonomy_resolver(g)

    def emit_traces(journey_id: str, kind: str, window: tuple[str, str], agents: tuple[str, ...], count: int,
                    dealer_id: str | None = None):
        nonlocal n_traces
        lo, hi = window
        for _ in range(count):
            agent = rng.choice(agents)
            tool = rng.choice(TOOLS_BY_AGENT[agent])
            blocked = rng.random() < 0.025
            ts = _between(rng, lo, hi)
            action_id = f"{agent.lower()}.{tool}"
            _add(g, "PLAT_AgentTraces", {
                "trace_id": g.nid("TR"), "journey_id": journey_id, "journey_kind": kind, "agent_id": agent,
                "routing_decision": f"{agent} selected for {tool} ({kind})", "tool": tool,
                # The RESOLVED level at trace time (spec §4.10.1), not a random draw (BR8.4).
                "autonomy_level": resolve_level(action_id, dealer_id, ts),
                "latency_ms": int(rng.lognormvariate(6.2, 0.45)),
                "citations": [f"quick_index:{rng.choice(KNOWLEDGE_KINDS)}:{rng.randint(1, 400):04d}"],
                "outcome": "guardrail_blocked" if blocked else ("error" if rng.random() < 0.01 else "ok"),
                "rule_id": rng.choice(GUARDRAIL_IDS) if blocked else None,
                "ts": ts, "market_id": mid,
                "source_ref": "synthetic:distribution (lognormal latency); plan:§3.4",
            })
            n_traces += 1

    # Traces are agent activity that has already happened, so the trace window
    # must never run past SEED_NOW (R-08). Golden journeys carry FUTURE-dated
    # events (test-drive/delivery appointments via cfg.ahead), so the raw last
    # event_ts (ts[-1]) can exceed SEED_NOW; clamp the window high bound to
    # min(last event_ts, SEED_NOW). If the earliest event is itself >= SEED_NOW
    # the whole window collapses to SEED_NOW (a valid single-instant window).
    now_iso = iso(cfg.now)

    def _clamped_window(sorted_ts: list[str]) -> tuple[str, str]:
        lo = min(sorted_ts[0], now_iso)
        hi = min(sorted_ts[-1], now_iso)
        return lo, hi

    # Event timestamp field is `event_ts` (core.py ev(); spec §4 key table SK event_ts).
    if journeys and jevents:
        by_j: dict[str, list[str]] = {}
        for e in jevents:
            by_j.setdefault(e["journey_id"], []).append(e["event_ts"])
        for j in journeys:
            if j.get("golden_story") in golden_ids and j["journey_id"] in by_j:
                ts = sorted(by_j[j["journey_id"]])
                emit_traces(j["journey_id"], "purchase", _clamped_window(ts), PURCHASE_AGENTS, rng.randint(55, 80),
                            dealer_id=j.get("dealer_id"))
    else:
        shortfall["purchase_traces"] = "DEALER_Journeys/JourneyEvents absent — purchase traces skipped"
    if sjourneys and sevents:
        by_s: dict[str, list[str]] = {}
        for e in sevents:
            by_s.setdefault(e["service_journey_id"], []).append(e["event_ts"])
        for s in sjourneys:
            if s.get("golden_story") and s["service_journey_id"] in by_s:
                ts = sorted(by_s[s["service_journey_id"]])
                emit_traces(s["service_journey_id"], "service", _clamped_window(ts), SERVICE_AGENTS, rng.randint(50, 75),
                            dealer_id=s.get("dealer_id"))
    else:
        shortfall["service_traces"] = "DEALER_ServiceJourneys/ServiceJourneyEvents absent — service traces skipped"

    shortfall["_traces_emitted"] = str(n_traces)
    return shortfall


def _autonomy_resolver(g: "G"):
    """Return resolve(action_id, dealer_id, ts_iso) -> the level in force at ts (spec §4.10.1).

    Picks the version whose effective_from <= ts with the greatest version number,
    preferring a dealer_id-scoped row for that dealer over the tenant row. Before the
    earliest effective_from the level is `observe` (nothing was configured yet).
    Unknown action ids fall back to `recommend` (the v1 observation default).
    """
    from datetime import datetime

    rows = g.t.get("PLAT_ConfigAutonomy", [])
    by_action: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("effective_from"):  # drafts (null) never take effect
            by_action.setdefault(r["action_id"], []).append(r)

    def parse(s: str) -> datetime:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))

    def resolve(action_id: str, dealer_id: str | None, ts_iso: str) -> str:
        cands = by_action.get(action_id)
        if not cands:
            return "recommend"
        ts = parse(ts_iso)
        live = [r for r in cands if parse(r["effective_from"]) <= ts]
        if not live:
            return "observe"
        scoped = [r for r in live if r["scope"] == "dealer_id" and r["scope_id"] == dealer_id]
        pool = scoped or [r for r in live if r["scope"] == "tenant"]
        return max(pool, key=lambda r: r["version"])["level"]

    return resolve


def _det_ext(rng, system: str, app_id: str) -> str:
    """Deterministic external id from the seeded RNG (never Python hash())."""
    n = rng.randint(0, 99_999_999)
    return {"crm": f"CRM-{n % 10_000_000:07d}", "dms": f"DMS-{n % 1_000_000:06d}", "cdp": f"CDP-{n:08d}"}[system]


def _between(rng, lo_iso: str, hi_iso: str) -> str:
    from datetime import datetime
    lo = datetime.fromisoformat(lo_iso.replace("Z", "+00:00"))
    hi = datetime.fromisoformat(hi_iso.replace("Z", "+00:00"))
    if hi <= lo:
        return lo_iso
    return iso(lo + (hi - lo) * rng.random())


# ── Phase registration (U4 phase-registry refactor) ─────────────────────────
# gen_platform_config runs early (band 10, right after foundation); gen_platform_late
# runs LAST (band 90 — it needs the fully populated accumulator). See phase_registry.py.
from . import phase_registry as _pr  # noqa: E402

_pr.register(_pr.BAND_PLATFORM_CONFIG, gen_platform_config)  # 10
_pr.register(_pr.BAND_PLATFORM_LATE, gen_platform_late)      # 90
