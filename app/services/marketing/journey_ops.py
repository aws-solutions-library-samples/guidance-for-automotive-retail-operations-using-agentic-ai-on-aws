"""Journey Ops (spec §5.16, G-08): the OEM CX lead's editor for A2 nurture.

All of it lives in the versioned PLAT_ConfigNurture row family
(item_key = "nurture#<market>", one row per version). A2 (`journeys.nurture`)
always reads the ACTIVE version, so publishing changes agent behaviour on the
next invocation with no redeploy (acceptance #24).

  get_journey_ops(market)                 -> active + draft + version history
  save_draft(market, changes, actor)      -> create/update the single draft version
  publish(market, version, actor)         -> draft -> active; prior active retired;
                                             emits config.published
  rollback(market, to_version, actor)     -> republish an old version's content as
                                             a NEW version (history is never rewritten)
  preview(market, template, journey_id)   -> render a template (or A/B variant) with a
                                             seeded customer's real facts
  analytics(market)                       -> touches / replies / conversions

Sections: cadence_caps, policy (quiet hours default, stop conditions,
escalation), templates (per stage/trigger × channel, optional A/B variants),
consent_rules (by state/region), surface_content (intake questions, shortlist
copy, FAQ used by answer_customer_question).
"""
from __future__ import annotations

import copy
import datetime as _dt
import re
from typing import Any

from ..common import ddb

_TABLE = "PLAT_ConfigNurture"
SECTIONS = ("cadence_caps", "policy", "templates", "consent_rules", "surface_content")
CHANNELS = ("email", "sms", "chat")
FAMILIES = ("sales", "service")
# Placeholders A2 can fill from real records (journeys.nurture._facts).
ALLOWED_TOKENS = frozenset({"first_name", "last_name", "brand", "advisor_name", "dealer_name", "model",
                            "item", "delivery_date", "trade_make", "trade_model"})
STOP_CONDITIONS = ("opt_out", "booked", "purchased", "human_takeover", "unresponsive_30d", "stage_exit")
_TOKEN_RE = re.compile(r"\{(\w+)\}")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

DEFAULT_POLICY = {
    "quiet_hours": {"start": "20:00", "end": "08:00"},
    "stop_conditions": ["opt_out", "booked", "purchased", "human_takeover"],
    "escalation": {"credit_or_legal_questions": "handoff_to_human", "negative_sentiment": "handoff_to_human",
                   "unanswered_after_hours": 4},
}
DEFAULT_SURFACE = {
    "intake_questions": [
        "What will you mostly use the vehicle for?",
        "How many people usually ride with you?",
        "What monthly payment range works for you?",
        "Are you open to an electric vehicle?",
    ],
    "shortlist_copy": "Here's why these fit you",
    "faq": [],
}


class NotFound(Exception):
    pass


class InvalidConfig(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


class Conflict(Exception):
    pass


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _key(market: str) -> str:
    return f"nurture#{market}"


def _versions(market: str, env_prefix: str | None) -> list[dict[str, Any]]:
    rows = ddb.query_hash(_TABLE, "item_key", _key(market), env_prefix)
    if not rows:
        raise NotFound(f"no nurture config for market {market}")
    return sorted(rows, key=lambda r: int(r.get("version") or 0))


def _status(row: dict[str, Any]) -> str:
    if row.get("is_active"):
        return "active"
    if row.get("status"):
        return row["status"]
    return "draft" if not row.get("effective_from") else "retired"


def _with_defaults(row: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(row)
    out.setdefault("policy", copy.deepcopy(DEFAULT_POLICY))
    out.setdefault("consent_rules", {})
    out.setdefault("surface_content", copy.deepcopy(DEFAULT_SURFACE))
    out["status"] = _status(row)
    return out


def _summary(row: dict[str, Any]) -> dict[str, Any]:
    return {"version": row.get("version"), "status": _status(row), "effective_from": row.get("effective_from"),
            "published_by": row.get("published_by"), "updated_by": row.get("updated_by"),
            "updated_at": row.get("updated_at"), "note": row.get("note"),
            "template_count": len(row.get("templates") or [])}


def get_journey_ops(market: str = "us", env_prefix: str | None = None) -> dict[str, Any]:
    rows = _versions(market, env_prefix)
    active = next((r for r in reversed(rows) if r.get("is_active")), rows[-1])
    draft = next((r for r in reversed(rows) if _status(r) == "draft"), None)
    return {"market_id": market, "active": _with_defaults(active),
            "draft": _with_defaults(draft) if draft else None,
            "versions": [_summary(r) for r in reversed(rows)],
            "allowed_tokens": sorted(ALLOWED_TOKENS), "stop_conditions": list(STOP_CONDITIONS),
            "channels": list(CHANNELS), "families": list(FAMILIES)}


def validate(cfg: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    caps = cfg.get("cadence_caps") or {}
    for k in ("per_day", "per_week"):
        v = caps.get(k)
        if not isinstance(v, int) or v < 0:
            errs.append(f"cadence_caps.{k} must be a non-negative integer")
    if isinstance(caps.get("per_day"), int) and isinstance(caps.get("per_week"), int) and caps["per_day"] > caps["per_week"]:
        errs.append("cadence_caps.per_day cannot exceed per_week")
    for ch, v in (caps.get("per_channel_week") or {}).items():
        if ch not in CHANNELS or not isinstance(v, int) or v < 0:
            errs.append(f"cadence_caps.per_channel_week.{ch} invalid")
        elif isinstance(caps.get("per_week"), int) and v > caps["per_week"]:
            # a channel can never send more than the customer may receive in a week overall
            errs.append(f"cadence_caps.per_channel_week.{ch} ({v}) cannot exceed per_week ({caps['per_week']})")
    pol = cfg.get("policy") or {}
    qh = pol.get("quiet_hours") or {}
    for k in ("start", "end"):
        if qh and not _HHMM.match(str(qh.get(k) or "")):
            errs.append(f"policy.quiet_hours.{k} must be HH:MM")
    bad = [s for s in pol.get("stop_conditions") or [] if s not in STOP_CONDITIONS]
    if bad:
        errs.append(f"unknown stop conditions {bad}")
    if "opt_out" not in (pol.get("stop_conditions") or ["opt_out"]):
        errs.append("opt_out is a hard rule and must remain a stop condition")
    seen = set()
    for i, t in enumerate(cfg.get("templates") or []):
        tid = t.get("template_id") or f"#{i}"
        for f in ("template_id", "channel", "family", "stage_or_trigger", "body"):
            if not t.get(f):
                errs.append(f"template {tid}: {f} is required")
        if t.get("channel") and t["channel"] not in CHANNELS:
            errs.append(f"template {tid}: channel must be one of {CHANNELS}")
        if t.get("family") and t["family"] not in FAMILIES:
            errs.append(f"template {tid}: family must be one of {FAMILIES}")
        k = (t.get("family"), t.get("stage_or_trigger"), t.get("channel"))
        if k in seen:
            errs.append(f"template {tid}: duplicate {k}")
        seen.add(k)
        texts = [t.get("subject") or "", t.get("body") or ""] + \
            [f"{v.get('subject') or ''} {v.get('body') or ''}" for v in t.get("variants") or []]
        unknown = sorted({tok for x in texts for tok in _TOKEN_RE.findall(x)} - ALLOWED_TOKENS)
        if unknown:
            errs.append(f"template {tid}: unknown placeholders {unknown}")
        weights = [v.get("weight") for v in t.get("variants") or []]
        if weights and (any(not isinstance(w, (int, float)) or w < 0 for w in weights) or sum(weights) > 100):
            errs.append(f"template {tid}: variant weights must be >= 0 and total <= 100")
    for region, rule in (cfg.get("consent_rules") or {}).items():
        if not isinstance(rule, dict):
            errs.append(f"consent_rules.{region} must be an object")
    sc = cfg.get("surface_content") or {}
    if len(sc.get("intake_questions") or []) > 4:
        errs.append("surface_content.intake_questions: at most 4 (spec §3.1)")
    return errs


def save_draft(market: str, changes: dict[str, Any], actor: str, note: str | None = None,
               env_prefix: str | None = None) -> dict[str, Any]:
    """Apply section changes to the draft (creating it from the active version)."""
    unknown = [k for k in changes if k not in SECTIONS]
    if unknown:
        raise InvalidConfig([f"unknown section(s) {unknown}"])
    state = get_journey_ops(market, env_prefix)
    rows = _versions(market, env_prefix)
    base = state["draft"] or state["active"]
    draft = {k: copy.deepcopy(v) for k, v in base.items() if k != "status"}
    if state["draft"] is None:
        draft["version"] = int(rows[-1].get("version") or 0) + 1
        draft["created_from"] = base.get("version")
    for k, v in changes.items():
        draft[k] = copy.deepcopy(v)
    errs = validate(draft)
    if errs:
        raise InvalidConfig(errs)
    draft.update({"is_active": False, "status": "draft", "effective_from": None, "updated_by": actor,
                  "updated_at": _now(), "note": note if note is not None else draft.get("note")})
    ddb.put_item(_TABLE, draft, env_prefix)
    return _with_defaults(draft)


def publish(market: str, version: int, actor: str, env_prefix: str | None = None) -> dict[str, Any]:
    from ..events import bus
    rows = _versions(market, env_prefix)
    target = next((r for r in rows if int(r.get("version") or 0) == int(version)), None)
    if target is None:
        raise NotFound(f"nurture#{market} has no version {version}")
    if target.get("is_active"):
        raise Conflict(f"version {version} is already active")
    if _status(target) != "draft":
        raise Conflict(f"version {version} is {_status(target)}; roll back to republish it")
    errs = validate(target)
    if errs:
        raise InvalidConfig(errs)
    ts = _now()
    retired = []
    for r in rows:
        if r.get("is_active"):
            r = dict(r)
            r.update(is_active=False, status="retired", retired_at=ts)
            ddb.put_item(_TABLE, r, env_prefix)
            retired.append(r.get("version"))
    t = dict(target)
    t.update(is_active=True, status="active", effective_from=ts, published_by=actor, published_at=ts)
    ddb.put_item(_TABLE, t, env_prefix)
    ev = bus.emit("config.published", {"config_key": "nurture", "item_key": _key(market), "version": t["version"],
                                       "market_id": market, "retired_versions": retired},
                  actor=actor, env_prefix=env_prefix)
    return {"published": t["version"], "retired": retired, "effective_from": ts, "bus_event": ev}


def rollback(market: str, to_version: int, actor: str, env_prefix: str | None = None) -> dict[str, Any]:
    rows = _versions(market, env_prefix)
    src = next((r for r in rows if int(r.get("version") or 0) == int(to_version)), None)
    if src is None:
        raise NotFound(f"nurture#{market} has no version {to_version}")
    if src.get("is_active"):
        raise Conflict(f"version {to_version} is already active")
    # discard an open draft so the rollback becomes the single new version
    new_v = int(rows[-1].get("version") or 0) + 1
    for r in rows:
        if _status(r) == "draft":
            r = dict(r)
            r.update(status="discarded", discarded_at=_now(), discarded_by=actor)
            ddb.put_item(_TABLE, r, env_prefix)
    row = {k: copy.deepcopy(v) for k, v in src.items()
           if k not in ("published_by", "published_at", "retired_at", "status")}
    row.update(version=new_v, is_active=False, status="draft", effective_from=None, rolled_back_from=to_version,
               updated_by=actor, updated_at=_now(), note=f"rollback to v{to_version}")
    ddb.put_item(_TABLE, row, env_prefix)
    out = publish(market, new_v, actor, env_prefix)
    return {**out, "rolled_back_to": int(to_version)}


def preview(market: str, template: dict[str, Any], journey_id: str | None = None,
            variant_id: str | None = None, env_prefix: str | None = None) -> dict[str, Any]:
    """Render with a seeded customer's facts (the same filler A2 uses)."""
    from ..journeys import nurture
    journey = None
    if journey_id:
        journey = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id}, env_prefix)
        if journey is None:
            raise NotFound(f"journey {journey_id} not found")
    else:
        journey = next(iter(ddb.scan_filter("DEALER_Journeys", "market_id", market, env_prefix, limit=1)), None)
    if journey is None:
        raise NotFound("no journey available to preview with")
    t = dict(template)
    if variant_id:
        v = next((v for v in t.get("variants") or [] if v.get("variant_id") == variant_id), None)
        if v is None:
            raise NotFound(f"variant {variant_id} not found")
        t.update({k: v[k] for k in ("subject", "body") if v.get(k)})
    facts = nurture._facts(journey, env_prefix)
    tokens = set(_TOKEN_RE.findall(f"{t.get('subject') or ''} {t.get('body') or ''}"))
    return {"journey_id": journey.get("journey_id"), "customer_id": journey.get("customer_id"),
            "channel": t.get("channel"),
            "subject": nurture._render(t.get("subject") or "", facts) if t.get("channel") == "email" else None,
            "body": nurture._render(t.get("body") or "", facts),
            "unknown_tokens": sorted(tokens - ALLOWED_TOKENS),
            "facts_used": {k: facts[k] for k in sorted(tokens & set(facts))}}


# propose_policy_change thresholds (§3.2, 2026-09-22). Deliberately explicit so
# a reviewer can see why a proposal appeared and tune the bar.
QUIET_BREACH_PCT = 0.05        # > 5% of touches outside quiet hours -> propose a cap/window change
LOW_REPLY_RATE = 0.10          # a channel replying under 10% -> propose a cadence cut
MIN_CHANNEL_TOUCHES = 10       # don't propose off a handful of sends
MIN_INTENT_PLANS = 5           # don't propose off a handful of plans


def _hhmm_to_minutes(v: str | None) -> int | None:
    if not v or not _HHMM.match(str(v)):
        return None
    h, m = str(v).split(":")
    return int(h) * 60 + int(m)


def _in_quiet_hours(ts: str | None, start: str | None, end: str | None) -> bool:
    """Is this send inside the configured quiet window? Windows wrap midnight
    (20:00 → 08:00), which is the normal case."""
    s, e = _hhmm_to_minutes(start), _hhmm_to_minutes(end)
    if s is None or e is None or not ts:
        return False
    try:
        t = _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return False
    minutes = t.hour * 60 + t.minute
    return (minutes >= s or minutes < e) if s > e else (s <= minutes < e)


def propose_policy_changes(market: str = "us", env_prefix: str | None = None) -> dict[str, Any]:
    """A2's `propose_policy_change` review feed (spec §3.2, 2026-09-22).

    Derived deterministically from the outcome loop against the ACTIVE nurture
    policy: outbound touches landing inside the configured quiet hours, channels
    whose reply rate sits under the bar, and intents with plans but no
    conversions. Each proposal carries the evidence figure that produced it and a
    concrete change to the section a human would edit.

    A2 never self-publishes: these are `open` review items, applied (or not)
    through the normal draft → publish flow. With everything inside policy the
    response is an empty, well-shaped list."""
    state = get_journey_ops(market, env_prefix)
    active = state["active"]
    policy = active.get("policy") or {}
    caps = active.get("cadence_caps") or {}
    quiet = policy.get("quiet_hours") or {}
    stats = analytics(market, env_prefix)

    custs = {c.get("customer_id") for c in ddb.scan_filter("DEALER_Customers", "market_id", market, env_prefix)}
    outbound = [m for m in ddb.scan_all("CUST_Messages", env_prefix)
                if m.get("customer_id") in custs and m.get("direction") == "outbound"]
    breaches = [m for m in outbound if _in_quiet_hours(m.get("sent_at"), quiet.get("start"), quiet.get("end"))]
    breach_rate = round(len(breaches) / len(outbound), 4) if outbound else None

    # Proposals are recomputed per request, so the timestamp is this run.
    ts = _now()
    proposals: list[dict[str, Any]] = []

    def _add(kind: str, key: str, summary: str, evidence: str, change: dict[str, Any], section: str) -> None:
        proposals.append({
            "proposal_id": f"NP-{market.upper()}-{kind.upper().replace('_', '')}-{key}",
            "kind": kind, "status": "open", "summary": summary, "description": summary,
            "evidence": evidence, "reason": evidence,
            "section": section, "proposed_change": change,
            "active_version": active.get("version"),
            "created_at": ts,
            "proposed_by": "A2.propose_policy_change",
            "source_ref": stats.get("source_ref"),
        })

    if breach_rate is not None and breach_rate > QUIET_BREACH_PCT:
        evidence = (f"{len(breaches)} of {len(outbound)} outbound touches ({breach_rate:.1%}) were sent inside "
                    f"the configured quiet window {quiet.get('start')}–{quiet.get('end')}, over the "
                    f"{QUIET_BREACH_PCT:.0%} bar.")
        per_day = caps.get("per_day")
        if isinstance(per_day, int) and per_day > 1:
            # Still room to cut volume: fewer sends per day, fewer that spill into the window.
            _add("cadence_cap", "QUIETHOURS",
                 f"Lower cadence_caps.per_day from {per_day} to {per_day - 1} so fewer sends spill into the "
                 f"quiet window {quiet.get('start')}–{quiet.get('end')}.",
                 evidence, {"cadence_caps": {"per_day": per_day - 1}}, "cadence_caps")
        else:
            # The cap is already at its floor, so the breach is an enforcement gap
            # at send time, not a volume problem — say that instead of proposing a
            # cap change that cannot be made.
            _add("policy", "QUIETHOURS",
                 (f"Hold sends until the quiet window closes: the daily cap is already at its floor "
                  f"({per_day}/day), so the window is being crossed at send time, not by volume."),
                 evidence,
                 {"policy": {"quiet_hours": {"start": quiet.get("start"), "end": quiet.get("end")},
                             "action": "enforce_at_send"}}, "policy")

    per_channel = dict(caps.get("per_channel_week") or {})
    for row in stats.get("by_channel") or []:
        touches, replies = row.get("touches") or 0, row.get("replies") or 0
        if touches < MIN_CHANNEL_TOUCHES:
            continue
        rate = replies / touches
        if rate >= LOW_REPLY_RATE:
            continue
        ch = row.get("channel")
        current = per_channel.get(ch)
        proposed = max(1, int(current) - 1) if isinstance(current, int) and current > 1 else current
        _add("cadence_cap", f"CHANNEL-{str(ch).upper()}",
             (f"Cut the weekly {ch} cap"
              + (f" from {current} to {proposed}" if current is not None else "")
              + " — the channel is sending more than it earns."),
             f"{ch}: {replies} replies on {touches} touches ({rate:.1%}), under the {LOW_REPLY_RATE:.0%} bar.",
             {"cadence_caps": {"per_channel_week": {ch: proposed}}}, "cadence_caps")

    for row in stats.get("by_intent") or []:
        plans, converted = row.get("plans") or 0, row.get("converted") or 0
        if plans < MIN_INTENT_PLANS or converted:
            continue
        intent = row.get("intent")
        _add("template", f"INTENT-{str(intent).upper()}",
             f"Review the {intent} templates — rewrite the opener or retire the sequence.",
             f"{plans} {intent} nurture plans produced 0 journeys reaching OWN.",
             {"templates": {"stage_or_trigger": intent, "action": "review_copy"}}, "templates")

    return {"market_id": market, "active_version": active.get("version"),
            "proposals": proposals, "count": len(proposals),
            "policy_in_force": {"quiet_hours": quiet, "cadence_caps": caps},
            "signals": {"outbound_touches": len(outbound), "quiet_hour_breaches": len(breaches),
                        "quiet_hour_breach_rate": breach_rate,
                        "reply_rate": stats.get("reply_rate"), "plans": stats.get("plans")},
            "thresholds": {"quiet_breach_pct": QUIET_BREACH_PCT, "low_reply_rate": LOW_REPLY_RATE,
                           "min_channel_touches": MIN_CHANNEL_TOUCHES, "min_intent_plans": MIN_INTENT_PLANS},
            "note": ("The Journey Agent drafts these from the outcome loop and never self-publishes — apply one to the draft "
                     "and publish it through the normal flow."
                     if proposals else
                     "No proposals: quiet-hour compliance, channel reply rates and intent conversions are all "
                     "within the policy bounds in force."),
            "source_ref": "A2.propose_policy_change (PLAT_ConfigNurture active policy vs CUST_Messages + "
                          "DEALER_NurturePlans outcomes)"}


def analytics(market: str = "us", env_prefix: str | None = None) -> dict[str, Any]:
    """Nurture analytics: outbound touches, inbound replies, and conversions
    (journeys with an active nurture plan that reached OWN), by channel and intent."""
    msgs = [m for m in ddb.scan_all("CUST_Messages", env_prefix)]
    custs = {c.get("customer_id") for c in ddb.scan_filter("DEALER_Customers", "market_id", market, env_prefix)}
    msgs = [m for m in msgs if m.get("customer_id") in custs]
    by_channel: dict[str, dict[str, int]] = {}
    for m in msgs:
        b = by_channel.setdefault(m.get("channel") or "other", {"touches": 0, "replies": 0})
        b["touches" if m.get("direction") == "outbound" else "replies"] += 1
    plans = [p for p in ddb.scan_all("DEALER_NurturePlans", env_prefix) if p.get("customer_id") in custs]
    journeys = {j["journey_id"]: j for j in ddb.scan_filter("DEALER_Journeys", "market_id", market, env_prefix)}
    by_intent: dict[str, dict[str, int]] = {}
    for p in plans:
        intent = p.get("template_trigger") or p.get("intent") or p.get("family") or "sales"
        b = by_intent.setdefault(intent, {"plans": 0, "active": 0, "converted": 0})
        b["plans"] += 1
        b["active"] += 1 if p.get("active") else 0
        j = journeys.get(p.get("journey_id"))
        b["converted"] += 1 if j and j.get("stage") == "OWN" else 0
    touches = sum(b["touches"] for b in by_channel.values())
    replies = sum(b["replies"] for b in by_channel.values())
    return {"market_id": market, "touches": touches, "replies": replies,
            "reply_rate": round(replies / touches, 4) if touches else None,
            "plans": len(plans), "by_channel": [{"channel": k, **v} for k, v in sorted(by_channel.items())],
            "by_intent": [{"intent": k, **v} for k, v in sorted(by_intent.items())],
            "source_ref": "CUST_Messages + DEALER_NurturePlans + DEALER_Journeys"}
