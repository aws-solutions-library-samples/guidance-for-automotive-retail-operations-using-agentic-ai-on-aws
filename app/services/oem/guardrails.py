"""A6 — GR-001..GR-008 guardrail rules engine (spec §3.6.1, acceptance 15b).

The DM (District Manager) guardrail policy, enforced on BOTH the user query and
any generated output before an A6 answer or artifact is returned. This is the
"rules Lambda" half of the spec's design (the Bedrock Guardrails denied-topics
half is not available in this plain-engineering build; the rules engine covers
all eight rules on its own here).

  check_guardrails(text, context) -> {
      outcome: allow | warn | require_approval | block,
      violations: [{rule_id, rule_name, action, matched}],
      safe_alternative: str | None,   # present when outcome == block
      logged_event_ids: [...],        # OEM_GuardrailEvents rows written
  }

Rule set (from PLAT_ConfigGuardrails, active version, market-scoped; the code
carries the match patterns since the config stores only id/name/action):

  GR-001 Wholesale reduction recommendations          block
  GR-002 Dealer termination / separation language     block
  GR-003 Specific pricing guidance to dealers         block
  GR-004 Personnel hiring / firing recommendations    block
  GR-005 Allocation promises or commitments           block
  GR-006 Legal / regulatory interpretations           require_approval
  GR-007 Financial advice beyond standard metrics      require_approval
  GR-008 Competitive disparagement                     warn

Every block/warn is logged to OEM_GuardrailEvents (rule id, query hash, user,
timestamp) to power the Admin "blocks this week" / effectiveness view. Blocked
answers return a safe alternative.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import re
import uuid
from typing import Any

from ..common import ddb
from ..events import bus as _bus

_CONFIG = "PLAT_ConfigGuardrails"
_EVENTS = "OEM_GuardrailEvents"

# Outcome severity ordering (worst wins when several rules fire).
_SEVERITY = {"allow": 0, "warn": 1, "require_approval": 2, "block": 3}

# Per-rule match patterns. The config table stores only rule_id/name/action, so
# the detection lives here (spec: "rules Lambda for pattern matching on
# inventory/allocation language"). Patterns are deliberately conservative —
# aimed at the intent the rule blocks, phrased to catch the seeded test prompts
# (e.g. GR-001 "Should Decatur cut wholesale by 20%?", GR-003 "Tell Marietta to
# price the Ion M at $41,900") while leaving normal district talk untouched.
_PATTERNS: dict[str, list[str]] = {
    # GR-001 — recommending a wholesale/floor-plan reduction.
    "GR-001": [
        r"\b(cut|reduce|lower|drop|slash|trim)\b.{0,30}\bwholesale\b",
        r"\bwholesale\b.{0,30}\b(cut|reduction|reduce|lower|drop)\b",
        r"\breduce\b.{0,20}\b(floor ?plan|floorplan)\b",
    ],
    # GR-002 — dealer termination / separation language.
    "GR-002": [
        r"\bterminat\w*\b.{0,25}\b(dealer(ship)?|store|account|franchise|point)\b",
        r"\b(dealer(ship)?|store|account)\b.{0,25}\bterminat\w*\b",
        # "a termination notice / separation letter" — the document itself (eval a6-gr002)
        r"\b(termination|separation|de-?franchis\w*)\s+(notice|letter|plan|package)\b",
        r"\b(end|sever|cancel|revoke)\b.{0,20}\b(franchise|dealer agreement)\b",
        r"\bseparat\w*\b.{0,20}\bdealer(ship)?\b",
        # "their franchise will be terminated" — the object precedes the verb
        r"\b(franchise|dealer agreement|sales and service agreement)\b.{0,30}\b(terminat|cancel|revok|end)\w*\b",
    ],
    # GR-003 — specific pricing guidance to a dealer (set/tell a price).
    "GR-003": [
        r"\b(price|sell|list|mark)\b.{0,40}\bat\b\s*[$€£]?\s?\d",
        r"\b(set|tell|advise|instruct)\b.{0,40}\bprice\b",
        r"\bprice\b.{0,20}\bthe\b.{0,30}\bat\b\s*[$€£]?\s?\d",
    ],
    # GR-004 — personnel hiring / firing recommendations.
    "GR-004": [
        r"\b(fire|terminate|lay ?off|dismiss|replace)\b.{0,30}\b(gm|manager|advisor|staff|salesperson|employee|technician)\b",
        r"\b(hire|recruit)\b.{0,30}\b(a new|another)\b.{0,20}\b(gm|manager|advisor|staff|salesperson|technician)\b",
        r"\bshould\b.{0,20}\b(fire|hire|let go)\b",
    ],
    # GR-005 — allocation promises or commitments.
    "GR-005": [
        r"\b(guarantee|promise|commit|assure|ensure)\b.{0,30}\b(allocat\w*|units|inventory|cars|vehicles)\b",
        r"\b(allocat\w*)\b.{0,30}\b(guarantee|promise|commit|extra|more units|priority)\b",
        r"\bwe('| wi)ll\b.{0,20}\b(get|give) you\b.{0,20}\b(units|allocation|inventory)\b",
    ],
    # GR-006 — legal / regulatory interpretations.
    "GR-006": [
        r"\b(is|are|it'?s)\b.{0,20}\b(legal|illegal|lawful|unlawful)\b",
        r"\b(legally|by law|under the law|regulation says|statute)\b",
        r"\b(interpret|interpretation)\b.{0,20}\b(law|regulation|statute|franchise law)\b",
    ],
    # GR-007 — financial advice beyond standard metrics.
    "GR-007": [
        r"\b(invest|financing structure|take a loan|refinanc\w*|tax strategy|write.?off)\b",
        r"\b(financial advice|should (you|they) (borrow|invest|finance))\b",
    ],
    # GR-008 — competitive disparagement.
    "GR-008": [
        r"\b(competitor|rival|other brand)\b.{0,30}\b(inferior|garbage|junk|terrible|unreliable|worse|trash)\b",
        r"\b(toyota|honda|ford|gm|chevy|nissan|kia|hyundai|tesla|bmw|vw|volkswagen)\b.{0,25}\b(inferior|garbage|junk|terrible|unreliable|trash|worse)\b",
        r"\b(trash|bad ?mouth|slam|disparage)\b.{0,20}\b(the )?competition\b",
    ],
}

_SAFE_ALTERNATIVES: dict[str, str] = {
    # Every alternative opens by saying what is refused, so the reply reads as an answer to the
    # ask rather than a non sequitur (2026-10-27 eval, a6-gr001 relevance 0.3 before this line).
    "GR-001": "I can't recommend cutting or changing a dealer's wholesale — that follows the published "
              "allocation process. I can show their current days supply and objective status instead.",
    "GR-002": "I can't advise on dealer termination or separation. I can summarize "
              "performance against objectives and the escalation process.",
    "GR-003": "I can't set dealer pricing. I can show MSRP, current incentives, and "
              "market pricing context for the model.",
    "GR-004": "I can't make hiring or firing recommendations. I can show staffing "
              "KPIs (advisor productivity, lead response) and coaching resources.",
    "GR-005": "I can't promise or commit allocation. I can show current days supply "
              "and objective status; allocation decisions follow the published process.",
    "GR-006": "I can't give legal or regulatory interpretations. This needs review by "
              "the compliance/legal team — I can route it as a draft for approval.",
    "GR-007": "I can share standard performance metrics, but financial advice beyond "
              "those is out of scope — I can route this for finance review.",
    "GR-008": "I'll keep this factual and avoid disparaging competitors. I can compare "
              "on published specs, pricing, and objective data.",
}


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _query_hash(text: str) -> str:
    """Stable short hash of the evaluated text (privacy: the raw query is not
    stored in the guardrail event, only its hash — spec §3.6.1)."""
    return hashlib.sha256((text or "").strip().lower().encode("utf-8")).hexdigest()[:16]


class _Rule:
    __slots__ = ("rule_id", "name", "action", "patterns")

    def __init__(self, rule_id: str, name: str, action: str, patterns: list[str]):
        self.rule_id = rule_id
        self.name = name
        self.action = action
        self.patterns = [re.compile(p, re.IGNORECASE) for p in patterns]

    def match(self, text: str) -> str | None:
        for pat in self.patterns:
            m = pat.search(text)
            if m:
                return m.group(0)
        return None


def rule_label(rule_id: str | None, market: str = "us", env_prefix: str | None = None) -> str:
    """'GR-001' → 'the wholesale-reduction rule' (the config's name), never the bare id."""
    for r in _load_rules(market, env_prefix):
        if r.rule_id == rule_id:
            return f"the {str(r.name).lower().rstrip('.')} rule"
    return "an OEM guardrail rule"


def _load_rules(market: str, env_prefix: str | None) -> list[_Rule]:
    """Load the active guardrail rules for a market from PLAT_ConfigGuardrails,
    joining the config's id/name/action with the code's match patterns. Falls
    back to the built-in rule set (all 8) if the config row is missing."""
    row = None
    try:
        candidates = ddb.scan_filter(_CONFIG, "market_id", market, env_prefix)
        # the active published version for this market (fall back to the
        # highest version only when no row carries an is_active flag)
        candidates = [c for c in candidates if c.get("rules")]
        active = [c for c in candidates if c.get("is_active") is True]
        flagged = any("is_active" in c for c in candidates)
        pool = active if flagged else candidates
        if pool:
            row = max(pool, key=lambda c: int(c.get("version", 1)))
    except Exception:
        row = None

    if row and row.get("rules"):
        rules: list[_Rule] = []
        for r in row["rules"]:
            rid = r.get("rule_id")
            if not rid:
                continue
            rules.append(_Rule(rid, r.get("name", rid), r.get("action", "block"),
                               _PATTERNS.get(rid, [])))
        return rules

    # Built-in fallback (default actions per spec §3.6.1).
    defaults = {
        "GR-001": ("Wholesale reduction recommendations", "block"),
        "GR-002": ("Dealer termination / separation language", "block"),
        "GR-003": ("Specific pricing guidance to dealers", "block"),
        "GR-004": ("Personnel hiring / firing recommendations", "block"),
        "GR-005": ("Allocation promises or commitments", "block"),
        "GR-006": ("Legal / regulatory interpretations", "require_approval"),
        "GR-007": ("Financial advice beyond standard metrics", "require_approval"),
        "GR-008": ("Competitive disparagement", "warn"),
    }
    return [_Rule(rid, name, action, _PATTERNS.get(rid, []))
            for rid, (name, action) in defaults.items()]


_OUTCOME_FOR_ACTION = {"block": "block", "require_approval": "require_approval", "warn": "warn"}
_LOG_OUTCOME = {"block": "blocked", "require_approval": "held_for_approval", "warn": "warned"}


def check_guardrails(text: str, context: dict[str, Any] | None = None,
                     env_prefix: str | None = None) -> dict[str, Any]:
    """Evaluate `text` (query or generated output) against GR-001..GR-008.

    context (optional): {market_id, user_id, district_id, agent_id, tool,
    log (bool, default True), phase ('query'|'output')}.

    Returns the worst outcome across all matched rules, the matched rules, a
    safe-alternative message when blocked, and the ids of any logged events."""
    context = context or {}
    market = (context.get("market_id") or "us").lower()
    do_log = context.get("log", True)
    rules = _load_rules(market, env_prefix)

    violations: list[dict[str, Any]] = []
    for rule in rules:
        matched = rule.match(text or "")
        if matched:
            violations.append({"rule_id": rule.rule_id, "rule_name": rule.name,
                               "action": rule.action, "matched": matched})

    outcome = "allow"
    for v in violations:
        act = _OUTCOME_FOR_ACTION.get(v["action"], "block")
        if _SEVERITY[act] > _SEVERITY[outcome]:
            outcome = act

    # The worst-severity rule drives the safe alternative shown to the user.
    safe_alt = None
    if outcome in ("block", "require_approval"):
        worst = max(violations, key=lambda v: _SEVERITY[_OUTCOME_FOR_ACTION.get(v["action"], "block")])
        alt = _SAFE_ALTERNATIVES.get(worst["rule_id"])
        if alt:
            # name the rule in the refusal so the user (and the demo) can see which policy fired
            safe_alt = f"{alt} (Guardrail {worst['rule_id']} — {worst['rule_name']}.)"

    logged: list[str] = []
    if do_log and violations:
        logged = _log_events(text, violations, context, market, env_prefix)

    return {
        "outcome": outcome,
        "allowed": outcome != "block",
        "violations": violations,
        "safe_alternative": safe_alt,
        "logged_event_ids": logged,
        "query_hash": _query_hash(text),
        "market_id": market,
    }


def _log_events(text: str, violations: list[dict[str, Any]], context: dict[str, Any],
                market: str, env_prefix: str | None) -> list[str]:
    """Write one OEM_GuardrailEvents row per fired rule (matches the seeded
    shape: rule id, rule name, query hash, user, timestamp, outcome)."""
    ts = _now()
    qhash = _query_hash(text)
    agent = context.get("agent_id", "A6")
    tool = context.get("tool", "check_guardrails")
    ids: list[str] = []
    for v in violations:
        eid = f"GRE-{market.upper()}-{uuid.uuid4().hex[:10]}"
        row = {
            "event_id": eid,
            "rule_id": v["rule_id"],
            "rule_name": v["rule_name"],
            "action": v["action"],
            "outcome": _LOG_OUTCOME.get(v["action"], "blocked"),
            "agent_id": agent,
            "tool": tool,
            "user": context.get("user_id"),
            "district_id": context.get("district_id"),
            "market_id": market,
            "query_hash": qhash,
            "detail": f"Guardrail {v['rule_id']} matched during {agent}.{tool}",
            "evaluated_at": ts,
            "source_ref": f"spec:§3.6.1 {v['rule_id']}",
        }
        try:
            ddb.put_item(_EVENTS, row, env_prefix)
            ids.append(eid)
            if row["outcome"] == "blocked":
                _bus.emit("guardrail.blocked", {
                    "guardrail_event_id": eid, "rule_id": v["rule_id"], "agent_id": agent,
                    "tool": tool, "district_id": context.get("district_id"),
                    "query_hash": qhash}, actor=context.get("user_id"), env_prefix=env_prefix)
        except Exception:
            # Logging must never break the guardrail decision itself.
            pass
    return ids


def guardrail_rules(market: str = "us", env_prefix: str | None = None) -> list[dict[str, Any]]:
    """The active rule table (for the Admin › AI Guardrails view)."""
    return [{"rule_id": r.rule_id, "name": r.name, "action": r.action,
             "pattern_count": len(r.patterns)} for r in _load_rules(market, env_prefix)]


def recent_blocks(market: str = "us", days: int = 7,
                  env_prefix: str | None = None) -> dict[str, Any]:
    """"Blocks this week" rollup for the Admin view: counts by rule over the
    trailing `days`, from OEM_GuardrailEvents."""
    cutoff = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)) \
        .replace(microsecond=0).isoformat().replace("+00:00", "Z")
    rows = [r for r in ddb.scan_filter(_EVENTS, "market_id", market, env_prefix)
            if (r.get("evaluated_at") or "") >= cutoff]
    by_rule: dict[str, int] = {}
    by_outcome: dict[str, int] = {}
    for r in rows:
        by_rule[r.get("rule_id", "?")] = by_rule.get(r.get("rule_id", "?"), 0) + 1
        by_outcome[r.get("outcome", "?")] = by_outcome.get(r.get("outcome", "?"), 0) + 1
    names = {r.rule_id: r.name for r in _load_rules(market, env_prefix)}
    return {"window_days": days, "since": cutoff, "total": len(rows),
            "by_rule": by_rule, "by_outcome": by_outcome,
            "rule_names": {rid: names.get(rid, rid) for rid in by_rule}}
