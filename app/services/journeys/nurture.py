"""A2 — Nurture (plan / consent / compose), spec §3.2, Phase 2b.

The long-running engagement side of the A2 supervisor. A2 keeps a prospect warm
between touches with personalized, consented, cadence-bounded messages grounded
in the customer's own record — never fabricated content or numbers.

  check_consent(customer_id, channel)     -> may we send on this channel now?
  plan_nurture(journey)                    -> build/refresh the NurturePlan
  compose_message(journey, channel, ...)   -> render a versioned template w/ facts
  get_nurture_state(journey_id|customer)   -> read model for the UI

Guardrails honored (spec §3.2 / §8.2):
  * Consent is per-channel opt-in (CUST_Consent); an opt-out blocks that channel.
  * Quiet hours (per customer, tz-aware) suppress sends inside the window.
  * Cadence caps (PLAT_ConfigNurture.cadence_caps: per_day, per_week,
    per_channel_week) bound frequency — counted from CUST_Messages actually sent.
  * Templates come from the ACTIVE PLAT_ConfigNurture version; placeholders are
    filled only from real records. A missing fact yields a neutral fallback, so a
    message never ships an unresolved "{token}" or an invented value.
  * source_ref is stamped on every plan and message (template_id + config version).
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any

from ..common import authz, ddb
from .assist import JOURNEY_STAGES

_PLANS = "DEALER_NurturePlans"
_CONSENT = "CUST_Consent"
_MESSAGES = "CUST_Messages"
_CONFIG = "PLAT_ConfigNurture"
_CUSTOMERS = "DEALER_Customers"
_USERS = "PLAT_Users"
_ORG = "PLAT_OrgHierarchy"

CHANNELS = ("email", "sms", "chat")

# Stage -> the template intent used for a proactive nurture touch (sales family).
_STAGE_INTENT = {
    "DISCOVER": "welcome", "CONFIGURE": "build_saved", "QUALIFY": "prequal",
    "FINANCE": "offer_ready", "TRADE_IN": "trade_value", "AGREE": "deal_summary",
    "DELIVER": "delivery_prep", "OWN": "welcome_owner",
}

_TOKEN_RE = re.compile(r"\{(\w+)\}")


class NotFound(Exception):
    pass


class Suppressed(Exception):
    """Raised when a send is blocked by consent, quiet hours, or cadence caps."""

    def __init__(self, reason: str, detail: dict[str, Any] | None = None):
        self.reason, self.detail = reason, detail or {}
        super().__init__(reason)


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(dt: _dt.datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_ts(s: str | None) -> _dt.datetime | None:
    if not s:
        return None
    try:
        return _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


# --- config -----------------------------------------------------------------

def _active_config(market: str, env_prefix: str | None) -> dict[str, Any]:
    """The active (or highest-version) PLAT_ConfigNurture row for a market."""
    versions = ddb.query_hash(_CONFIG, "item_key", f"nurture#{market}", env_prefix)
    if not versions:
        raise NotFound(f"no nurture config for market {market}")
    active = [v for v in versions if v.get("is_active")]
    return (active or sorted(versions, key=lambda v: v.get("version") or 0, reverse=True))[0]


def _find_template(cfg: dict[str, Any], stage_or_trigger: str, channel: str,
                   family: str = "sales") -> dict[str, Any] | None:
    for t in cfg.get("templates") or []:
        if (t.get("stage_or_trigger") == stage_or_trigger and t.get("channel") == channel
                and t.get("family") == family):
            return t
    return None


# --- consent + quiet hours + cadence ----------------------------------------

def _in_quiet_hours(consent: dict[str, Any], at: _dt.datetime) -> bool:
    """True if `at` falls inside the customer's quiet-hours window.

    quiet_hours = {start:'20:00', end:'08:00', tz:'America/New_York'}. We compare
    on wall-clock HH:MM in the customer's tz; a window that wraps midnight
    (start > end) is handled. Falls back to UTC if the tz is unknown."""
    qh = consent.get("quiet_hours") or {}
    start, end = qh.get("start"), qh.get("end")
    if not start or not end:
        return False
    tz = qh.get("tz")
    local = at
    try:
        from zoneinfo import ZoneInfo
        if tz:
            local = at.astimezone(ZoneInfo(tz))
    except Exception:
        pass  # unknown tz -> compare in UTC (documented fallback)
    hhmm = local.strftime("%H:%M")
    if start <= end:
        return start <= hhmm < end
    return hhmm >= start or hhmm < end  # wraps midnight


def _sent_counts(customer_id: str, channel: str, at: _dt.datetime,
                 env_prefix: str | None) -> dict[str, int]:
    """Outbound message counts for the customer in the trailing day/week."""
    msgs = ddb.scan_filter(_MESSAGES, "customer_id", customer_id, env_prefix)
    day_ago = at - _dt.timedelta(days=1)
    week_ago = at - _dt.timedelta(days=7)
    per_day = per_week = per_channel_week = 0
    for m in msgs:
        if m.get("direction") != "outbound":
            continue
        ts = _parse_ts(m.get("sent_at"))
        if ts is None:
            continue
        if ts >= day_ago:
            per_day += 1
        if ts >= week_ago:
            per_week += 1
            if m.get("channel") == channel:
                per_channel_week += 1
    return {"per_day": per_day, "per_week": per_week, "per_channel_week": per_channel_week}


def check_consent(customer_id: str, channel: str, at: _dt.datetime | None = None,
                  env_prefix: str | None = None) -> dict[str, Any]:
    """May A2 send on `channel` to this customer right now?

    Returns {ok, reason?, ...}; raises nothing (callers decide). Chat is treated
    as an owned inbound-initiated channel (no explicit opt-in flag), still bound
    by quiet hours and caps."""
    if channel not in CHANNELS:
        return {"ok": False, "reason": f"unknown channel {channel!r}"}
    at = at or _now()
    consent = ddb.get_item(_CONSENT, {"customer_id": customer_id}, env_prefix) or {}

    # per-channel opt-in / opt-out
    if channel == "email" and not consent.get("email_opt_in", False):
        return {"ok": False, "reason": "email not opted in"}
    if channel == "sms":
        if consent.get("sms_opt_out_at"):
            return {"ok": False, "reason": "sms opted out"}
        if not consent.get("sms_opt_in", False):
            return {"ok": False, "reason": "sms not opted in"}

    if _in_quiet_hours(consent, at):
        return {"ok": False, "reason": "within quiet hours",
                "quiet_hours": consent.get("quiet_hours")}

    # cadence caps from active config
    market = consent.get("market_id") or "us"
    try:
        caps = (_active_config(market, env_prefix).get("cadence_caps") or {})
    except NotFound:
        caps = {}
    counts = _sent_counts(customer_id, channel, at, env_prefix)
    per_day_cap = caps.get("per_day")
    per_week_cap = caps.get("per_week")
    per_channel_week_cap = (caps.get("per_channel_week") or {}).get(channel)
    if per_day_cap is not None and counts["per_day"] >= per_day_cap:
        return {"ok": False, "reason": "per-day cadence cap reached", "counts": counts, "caps": caps}
    if per_week_cap is not None and counts["per_week"] >= per_week_cap:
        return {"ok": False, "reason": "per-week cadence cap reached", "counts": counts, "caps": caps}
    if per_channel_week_cap is not None and counts["per_channel_week"] >= per_channel_week_cap:
        return {"ok": False, "reason": f"per-week {channel} cap reached", "counts": counts, "caps": caps}

    return {"ok": True, "channel": channel, "counts": counts, "caps": caps}


# --- fact resolution + rendering --------------------------------------------

# The model catalog carries no brand column and is keyed by trim, so brand is
# resolved from an explicit OEM brand map, then from the model-id prefix as a
# documented fallback (model ids are '<BRANDPREFIX>-<MODEL>', e.g. MER-TRAILHAND).
_BRAND_BY_PREFIX = {"MER": "Meridian"}


def _brand_for_journey(journey: dict[str, Any], env_prefix: str | None) -> str | None:
    """Best-effort brand for the customer's primary model. Never raises — a
    missing brand just yields a neutral fallback in the rendered message."""
    model_id = journey.get("primary_model_id")
    if not model_id:
        return None
    try:
        rows = ddb.scan_filter("OEM_Catalog", "model_id", model_id, env_prefix, limit=1)
        if rows:
            b = rows[0].get("brand") or rows[0].get("make") or rows[0].get("oem_brand")
            if b:
                return b
    except Exception:
        pass  # catalog lookup is enrichment only
    prefix = str(model_id).split("-", 1)[0]
    return _BRAND_BY_PREFIX.get(prefix)


def _facts(journey: dict[str, Any], env_prefix: str | None) -> dict[str, str]:
    """Real facts for placeholder substitution, from the records only."""
    cust = ddb.get_item(_CUSTOMERS, {"customer_id": journey.get("customer_id")}, env_prefix) or {}
    dealer = authz.org_node(journey.get("dealer_id"), journey.get("market_id"), env_prefix) or {}
    advisor = {}
    owner = journey.get("owner_user_id")
    if owner:
        advisor = ddb.get_item(_USERS, {"user_id": owner}, env_prefix) or {}
    adv_name = " ".join(x for x in (advisor.get("given_name"), advisor.get("family_name")) if x)
    ctx = journey.get("context") or {}
    cur = cust.get("current_vehicle") or {}
    # model: prefer a resolved trim/model name, else the model id, else neutral.
    model = journey.get("primary_model_id") or ""
    facts = {
        "first_name": cust.get("given_name") or cust.get("first_name") or "there",
        "last_name": cust.get("family_name") or "",
        "brand": _brand_for_journey(journey, env_prefix) or "your next vehicle",
        "advisor_name": adv_name or "your advisor",
        "dealer_name": dealer.get("name") or "your dealership",
        # Context-dependent facts: filled when the record carries them, else a
        # neutral, non-fabricated fallback (never an invented number/date).
        "model": model or "your build",
        "item": ctx.get("item") or model or "your vehicle",
        "delivery_date": ctx.get("delivery_date") or "your scheduled date",
        "trade_make": (cur.get("make") if isinstance(cur, dict) else None) or "your current vehicle",
        "trade_model": (cur.get("model") if isinstance(cur, dict) else None) or "",
    }
    return {k: str(v) for k, v in facts.items()}


def _render(text: str, facts: dict[str, str]) -> str:
    """Substitute {token}s from facts; leave a neutral blank for unknown tokens
    rather than shipping a literal '{token}'."""
    return _TOKEN_RE.sub(lambda m: facts.get(m.group(1), ""), text or "").strip()


def compose_message(journey: dict[str, Any], channel: str = "email",
                    intent: str | None = None, family: str = "sales",
                    env_prefix: str | None = None) -> dict[str, Any]:
    """Render a nurture message for a journey from the ACTIVE template config.

    intent defaults to the stage-appropriate sales intent; pass an explicit
    trigger (e.g. 'due_service') with family='service' for service templates."""
    market = journey.get("market_id") or "us"
    cfg = _active_config(market, env_prefix)
    stage_or_trigger = intent or _STAGE_INTENT.get(journey.get("stage"), "welcome")
    # For sales templates the config keys on the STAGE; for service on the trigger.
    key = journey.get("stage") if (family == "sales" and intent is None) else stage_or_trigger
    tmpl = _find_template(cfg, key, channel, family)
    if tmpl is None:
        raise NotFound(f"no {family}/{channel} template for {key!r} (market {market})")

    facts = _facts(journey, env_prefix)
    subject = _render(tmpl.get("subject", ""), facts) if channel == "email" else None
    body = _render(tmpl.get("body", ""), facts)
    # Any template token with no fact behind it (should be none for standard
    # templates now that every placeholder is mapped) — surfaced for honesty.
    tmpl_tokens = set(_TOKEN_RE.findall(f"{tmpl.get('subject','')} {tmpl.get('body','')}"))
    unresolved = sorted(t for t in tmpl_tokens if t not in facts)
    # "What we know" (durable-memory contract): the customer's durable memory rides alongside the
    # rendered message so whoever approves/sends the touch personalizes against
    # stated preferences and open promises. Guarded: empty recall = no block
    # (the body itself is never altered — templates stay the only copy source).
    from ..platform import memory as _memory
    what_we_know = _memory.recall(journey.get("customer_id"), k=3, env_prefix=env_prefix)
    out = {
        "journey_id": journey.get("journey_id"),
        "customer_id": journey.get("customer_id"),
        "channel": channel,
        "intent": tmpl.get("intent"),
        "family": family,
        "subject": subject,
        "body": body,
        "template_id": tmpl.get("template_id"),
        "source_ref": f"{tmpl.get('template_id')}@nurture#{market}.v{cfg.get('version')}",
        "unresolved_tokens": unresolved,
    }
    if what_we_know:
        out["what_we_know"] = what_we_know
    return out


# --- plan_nurture -----------------------------------------------------------

_GOAL_BY_STAGE = {
    "DISCOVER": "advance_to_configure", "CONFIGURE": "advance_to_qualify",
    "QUALIFY": "book_test_drive", "FINANCE": "present_offer",
    "TRADE_IN": "capture_trade", "AGREE": "close", "DELIVER": "prep_delivery",
    "OWN": "retain_and_upgrade",
}


def _cadence_for(journey: dict[str, Any]) -> str:
    """A stalled or cold journey gets a gentler cadence; fresh ones a tighter one."""
    idle = journey.get("idle_days") or 0
    if idle >= 21:
        return "every_7_days"
    # never tighter than every 3 days: 7/3 ≈ 2.3 touches a week stays inside the 3-a-week cap
    # (every 2 days is 3.5 a week — the plan would promise touches the cap then refuses)
    return "every_3_days"


_NAMED_CADENCE_DAYS = {"daily": 1, "weekly": 7, "twice_weekly": 3, "biweekly": 14, "monthly": 30}


def _cadence_days(cadence: str) -> int:
    """'every_3_days' → 3, 'weekly' → 7 (a named cadence is not "3 days" just because it has no digit)."""
    c = str(cadence or "").lower()
    if c in _NAMED_CADENCE_DAYS:
        return _NAMED_CADENCE_DAYS[c]
    m = re.search(r"(\d+)", c)
    return int(m.group(1)) if m else 3


def plan_nurture(journey: dict[str, Any], actor: str = "A2",
                 env_prefix: str | None = None) -> dict[str, Any]:
    """Build or refresh the NurturePlan for a journey's customer.

    Chooses a goal + cadence from the journey's stage and idle time, schedules
    the next touch respecting the preferred channel's consent, and records
    stop-conditions. Append/replace by nurture_plan_id keyed to the journey."""
    customer_id = journey.get("customer_id")
    journey_id = journey.get("journey_id")
    market = journey.get("market_id") or "us"
    goal = _GOAL_BY_STAGE.get(journey.get("stage"), "re_engage")
    cadence = _cadence_for(journey)

    consent = ddb.get_item(_CONSENT, {"customer_id": customer_id}, env_prefix) or {}
    preferred = consent.get("preferred_channel") or "email"
    # Fall back through channels the customer will actually accept.
    order = [preferred] + [c for c in CHANNELS if c != preferred]
    chosen_channel, gate = None, None
    at = _now()
    for ch in order:
        g = check_consent(customer_id, ch, at, env_prefix)
        if g["ok"]:
            chosen_channel, gate = ch, g
            break
    # next touch = now + cadence (or None if no channel is currently permitted)
    next_touch = _iso(at + _dt.timedelta(days=_cadence_days(cadence))) if chosen_channel else None

    # keep the existing plan id if one exists for this journey
    existing = [p for p in ddb.scan_filter(_PLANS, "journey_id", journey_id, env_prefix)]
    plan_id = existing[0]["nurture_plan_id"] if existing else \
        f"NP-{(journey_id or '').replace('J-', '')}"

    plan = {
        "nurture_plan_id": plan_id,
        "journey_id": journey_id,
        "customer_id": customer_id,
        "goal": goal,
        "cadence": cadence,
        "channel": chosen_channel,
        "next_touch_at": next_touch,
        "stop_conditions": ["opt_out", "converted", "stage_AGREE_reached"],
        "active": bool(chosen_channel),
        "blocked_reason": None if chosen_channel else "no permitted channel right now",
        "updated_at": _iso(at),
        "updated_by": actor,
        "source_ref": f"A2.plan_nurture:{journey_id}",
    }
    ddb.put_item(_PLANS, plan, env_prefix)
    return plan


# --- read model -------------------------------------------------------------

def get_nurture_state(journey: dict[str, Any], env_prefix: str | None = None) -> dict[str, Any]:
    """The nurture view for a journey: current plan, consent snapshot, per-channel
    send-eligibility right now, and a PREVIEW of the next message (not sent)."""
    customer_id = journey.get("customer_id")
    plans = ddb.scan_filter(_PLANS, "journey_id", journey.get("journey_id"), env_prefix)
    plan = plans[0] if plans else None
    consent = ddb.get_item(_CONSENT, {"customer_id": customer_id}, env_prefix) or {}
    at = _now()
    eligibility = {ch: check_consent(customer_id, ch, at, env_prefix) for ch in CHANNELS}
    # preview on the first currently-eligible channel (else preferred/email)
    preview_ch = next((ch for ch in CHANNELS if eligibility[ch]["ok"]),
                      consent.get("preferred_channel") or "email")
    try:
        preview = compose_message(journey, channel=preview_ch, env_prefix=env_prefix)
    except NotFound as e:
        preview = {"error": str(e)}
    return {
        "journey_id": journey.get("journey_id"),
        "customer_id": customer_id,
        "stage": journey.get("stage"),
        "plan": plan,
        "consent": {
            "email_opt_in": consent.get("email_opt_in"),
            "sms_opt_in": consent.get("sms_opt_in"),
            "sms_opted_out": bool(consent.get("sms_opt_out_at")),
            "preferred_channel": consent.get("preferred_channel"),
            "quiet_hours": consent.get("quiet_hours"),
        },
        "channel_eligibility": eligibility,
        "message_preview": preview,
    }
