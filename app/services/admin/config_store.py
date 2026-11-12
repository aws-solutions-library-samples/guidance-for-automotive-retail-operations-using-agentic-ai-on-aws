"""Admin Console / Journey Ops — versioned configuration store (spec §2.6.5, §5.16, §5.17, G-25).

One mechanism for every `PLAT_Config*` document an operator edits without a
redeploy: guardrail rules, the A3 suggestion matrix settings, Notification Hub
rules, feature flags, the connector registry, and tenant settings.

  get(kind, market)                      -> active version + content + history + draft
  save_draft(kind, market, content, …)   -> validated draft (NOT live) + diff vs active
  publish(kind, market, content|draft, …) -> new active version; prior retired;
                                            emits config.published (acceptance #24)
  rollback(kind, market, to_version, …)  -> re-publish an old version as a new one
  versions(kind, market)                 -> full history (newest first)

Versions are append-only rows keyed (item_key="<config_key>#<market>", version).
Publishing always writes max+1 as the single active row, so readers that pick
"the active row" (suggestions, notifications) and readers that pick "the highest
version" (guardrails) agree. Drafts live under item_key "<config_key>#<market>#draft"
with no market_id and the content nested under `draft`, so no reader can ever
mistake a draft for live configuration.

Hard rules (§4.10.1): GR-001…GR-008 cannot be removed or relaxed below their
default action; the validator rejects the attempt (HardRuleViolation).
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from ..common import ddb, personas
from . import audit_log

# kind -> (table, config_key)
KINDS: dict[str, tuple[str, str]] = {
    "guardrails": ("PLAT_ConfigGuardrails", "guardrails"),
    "suggestions": ("PLAT_ConfigSuggestions", "suggestions"),
    "notifications": ("PLAT_ConfigNotifications", "notifications"),
    "flags": ("PLAT_Config", "flags"),
    "connectors": ("PLAT_Config", "connectors"),
    "tenant": ("PLAT_Config", "tenant"),
}

_META = frozenset({"item_key", "version", "is_active", "effective_from", "retired_at", "published_by",
                   "note", "source_ref", "config_key", "market_id", "draft", "draft_of", "saved_by",
                   "saved_at", "rolled_back_from"})

# Built-in guardrail rules and their default (minimum) actions — spec §3.6.1.
GUARDRAIL_DEFAULTS: dict[str, str] = {
    "GR-001": "block", "GR-002": "block", "GR-003": "block", "GR-004": "block", "GR-005": "block",
    "GR-006": "require_approval", "GR-007": "require_approval", "GR-008": "warn",
}
_GR_SEVERITY = {"warn": 1, "require_approval": 2, "block": 3}

FLAG_DEFAULTS: dict[str, bool] = {
    "ENABLE_CONNECT": False, "ENABLE_PERSONALIZE": False, "ENABLE_QUICK_EMBED": False,
    "ENABLE_FEDERATE": False, "ENABLE_TELEMATICS": False,
}
NOTIFICATION_CHANNELS = frozenset({"push", "inapp", "email", "sms", "voice"})
_STAGES = frozenset(["DISCOVER", "CONFIGURE", "QUALIFY", "FINANCE", "TRADE_IN", "AGREE", "DELIVER", "OWN",
                     "BOOK", "CHECK_IN", "INSPECT", "DIAGNOSE", "APPROVE", "REPAIR", "READY", "FOLLOW_UP"])


class UnknownKind(ValueError):
    pass


class InvalidConfig(ValueError):
    pass


class HardRuleViolation(Exception):
    pass


class NotFound(Exception):
    pass


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _kind(kind: str) -> tuple[str, str]:
    if kind not in KINDS:
        raise UnknownKind(f"unknown config kind {kind!r} (one of {sorted(KINDS)})")
    return KINDS[kind]


def _item_key(kind: str, market: str) -> str:
    return f"{_kind(kind)[1]}#{market}"


def content_of(row: dict[str, Any] | None) -> dict[str, Any]:
    return {k: v for k, v in (row or {}).items() if k not in _META}


# --- defaults (registry-style documents that may not be seeded) -------------

def _default_content(kind: str, market: str) -> dict[str, Any]:
    if kind == "flags":
        return {"flags": dict(FLAG_DEFAULTS)}
    if kind == "connectors":
        from . import connectors
        return {"connectors": connectors.default_registry(market)}
    if kind == "guardrails":
        return {"rules": [{"rule_id": rid, "name": rid, "action": act} for rid, act in GUARDRAIL_DEFAULTS.items()]}
    return {}


# --- validation ---------------------------------------------------------------

def validate(kind: str, content: dict[str, Any]) -> dict[str, Any]:
    """Return normalised content or raise InvalidConfig / HardRuleViolation."""
    _kind(kind)
    if not isinstance(content, dict):
        raise InvalidConfig("content must be an object")
    c = {k: v for k, v in content.items() if k not in _META}
    if kind == "guardrails":
        rules = c.get("rules")
        if not isinstance(rules, list) or not rules:
            raise InvalidConfig("guardrails need a non-empty `rules` list")
        seen: dict[str, dict[str, Any]] = {}
        for r in rules:
            rid = (r or {}).get("rule_id")
            if rid not in GUARDRAIL_DEFAULTS:
                raise InvalidConfig(f"unknown rule {rid!r}: only GR-001…GR-008 have detection patterns")
            if rid in seen:
                raise InvalidConfig(f"duplicate rule {rid}")
            act = r.get("action")
            if act not in _GR_SEVERITY:
                raise InvalidConfig(f"{rid}: action must be one of {sorted(_GR_SEVERITY)}")
            if _GR_SEVERITY[act] < _GR_SEVERITY[GUARDRAIL_DEFAULTS[rid]]:
                raise HardRuleViolation(f"{rid} is a hard rule: action cannot be relaxed below "
                                        f"{GUARDRAIL_DEFAULTS[rid]!r} (§4.10.1)")
            seen[rid] = {"rule_id": rid, "name": str(r.get("name") or rid)[:120], "action": act,
                         **({"description": str(r["description"])[:500]} if r.get("description") else {})}
        missing = sorted(set(GUARDRAIL_DEFAULTS) - set(seen))
        if missing:
            raise HardRuleViolation(f"hard rules cannot be removed: {missing} (§4.10.1)")
        c["rules"] = [seen[rid] for rid in sorted(seen)]
    elif kind == "suggestions":
        th = c.get("confidence_threshold")
        try:
            th = float(th)
        except (TypeError, ValueError) as e:
            raise InvalidConfig("confidence_threshold must be a number") from e
        if not 0.0 <= th <= 1.0:
            raise InvalidConfig("confidence_threshold must be between 0 and 1")
        c["confidence_threshold"] = round(th, 4)
        stages = c.get("precompute_stages") or []
        bad = [s for s in stages if s not in _STAGES]
        if bad:
            raise InvalidConfig(f"unknown stage(s) {bad}")
        c["precompute_stages"] = list(dict.fromkeys(stages))
        if "objection_types" in c and not all(isinstance(x, str) and x for x in c["objection_types"]):
            raise InvalidConfig("objection_types must be non-empty strings")
    elif kind == "notifications":
        rules = c.get("rules")
        if not isinstance(rules, list):
            raise InvalidConfig("notifications need a `rules` list")
        events = set()
        for r in rules:
            ev = (r or {}).get("event")
            if not ev or not isinstance(ev, str):
                raise InvalidConfig("every rule needs an event")
            if ev in events:
                raise InvalidConfig(f"duplicate rule for event {ev}")
            events.add(ev)
            roles = r.get("roles") or []
            bad = [x for x in roles if x not in personas.PERSONAS]
            if bad or not roles:
                raise InvalidConfig(f"{ev}: unknown or missing roles {bad}")
            ch = r.get("channels") or []
            badc = [x for x in ch if x not in NOTIFICATION_CHANNELS]
            if badc or not ch:
                raise InvalidConfig(f"{ev}: unknown or missing channels {badc}")
            r["quiet_hours_respect"] = bool(r.get("quiet_hours_respect", True))
    elif kind == "flags":
        flags = c.get("flags")
        if not isinstance(flags, dict):
            raise InvalidConfig("flags must be an object")
        unknown = sorted(set(flags) - set(FLAG_DEFAULTS))
        if unknown:
            raise InvalidConfig(f"unknown flag(s) {unknown}")
        if not all(isinstance(v, bool) for v in flags.values()):
            raise InvalidConfig("flag values must be true/false")
        c["flags"] = {**FLAG_DEFAULTS, **flags}
    elif kind == "connectors":
        from . import connectors
        c["connectors"] = connectors.validate_registry(c.get("connectors"))
    elif kind == "tenant":
        if "lead_sla_minutes" in c:
            v = c["lead_sla_minutes"]
            if not isinstance(v, (int, float)) or not 1 <= v <= 1440:
                raise InvalidConfig("lead_sla_minutes must be 1…1440")
    return c


# --- diff -----------------------------------------------------------------------

def _flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k in sorted(obj):
            out.update(_flatten(obj[k], f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
        for i, x in enumerate(obj):
            key = x.get("rule_id") or x.get("event") or x.get("connector_id") or str(i)
            out.update(_flatten(x, f"{prefix}[{key}]"))
    else:
        out[prefix] = obj
    return out


def diff(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    a, b = _flatten(content_of(old)), _flatten(content_of(new))
    changes = []
    for k in sorted(set(a) | set(b)):
        if k not in a:
            changes.append({"path": k, "change": "added", "to": b[k]})
        elif k not in b:
            changes.append({"path": k, "change": "removed", "from": a[k]})
        elif a[k] != b[k]:
            changes.append({"path": k, "change": "changed", "from": a[k], "to": b[k]})
    return changes


# --- read -------------------------------------------------------------------------

def versions(kind: str, market: str, env_prefix: str | None = None) -> list[dict[str, Any]]:
    table, _ = _kind(kind)
    rows = ddb.query_hash(table, "item_key", _item_key(kind, market), env_prefix)
    return sorted(rows, key=lambda r: int(r.get("version") or 0), reverse=True)


def active(kind: str, market: str, env_prefix: str | None = None) -> dict[str, Any] | None:
    vs = versions(kind, market, env_prefix)
    act = [v for v in vs if v.get("is_active")]
    return (act or vs or [None])[0]


def _draft_row(kind: str, market: str, env_prefix: str | None) -> dict[str, Any] | None:
    table, _ = _kind(kind)
    rows = ddb.query_hash(table, "item_key", f"{_item_key(kind, market)}#draft", env_prefix)
    rows = [r for r in rows if r.get("draft")]
    return rows[0] if rows else None


def get(kind: str, market: str, env_prefix: str | None = None) -> dict[str, Any]:
    vs = versions(kind, market, env_prefix)
    act = active(kind, market, env_prefix)
    draft = _draft_row(kind, market, env_prefix)
    content = content_of(act) if act else _default_content(kind, market)
    return {
        "kind": kind, "market_id": market,
        "active_version": int(act.get("version")) if act else 0,
        "effective_from": (act or {}).get("effective_from"),
        "published_by": (act or {}).get("published_by"),
        "content": content,
        "is_default": act is None,
        "draft": ({"content": draft["draft"], "saved_by": draft.get("saved_by"), "saved_at": draft.get("saved_at"),
                   "diff": diff(content, draft["draft"])} if draft else None),
        "history": [{"version": int(v.get("version") or 0), "is_active": bool(v.get("is_active")),
                     "effective_from": v.get("effective_from"), "published_by": v.get("published_by"),
                     "note": v.get("note"), "rolled_back_from": v.get("rolled_back_from")} for v in vs],
    }


# --- write ------------------------------------------------------------------------

def save_draft(kind: str, market: str, content: dict[str, Any], actor: str,
               env_prefix: str | None = None) -> dict[str, Any]:
    table, _ = _kind(kind)
    norm = validate(kind, content)
    ddb.put_item(table, {"item_key": f"{_item_key(kind, market)}#draft", "version": 0, "draft": norm,
                         "draft_of": _item_key(kind, market), "saved_by": actor, "saved_at": _now()}, env_prefix)
    act = active(kind, market, env_prefix)
    base = content_of(act) if act else _default_content(kind, market)
    audit_log.record(actor, f"config.draft.{kind}", f"{kind}#{market}", {"changes": len(diff(base, norm))},
                     env_prefix=env_prefix)
    return {"kind": kind, "market_id": market, "draft": norm, "diff": diff(base, norm)}


def discard_draft(kind: str, market: str, actor: str, env_prefix: str | None = None) -> None:
    table, _ = _kind(kind)
    if _draft_row(kind, market, env_prefix):
        ddb.put_item(table, {"item_key": f"{_item_key(kind, market)}#draft", "version": 0, "draft": None,
                             "saved_by": actor, "saved_at": _now()}, env_prefix)


def publish(kind: str, market: str, actor: str, content: dict[str, Any] | None = None,
            note: str | None = None, rolled_back_from: int | None = None,
            env_prefix: str | None = None) -> dict[str, Any]:
    """Publish `content` (or the saved draft) as the new single active version."""
    table, config_key = _kind(kind)
    if content is None:
        d = _draft_row(kind, market, env_prefix)
        if not d:
            raise NotFound(f"no draft to publish for {kind}#{market}")
        content = d["draft"]
    norm = validate(kind, content)
    vs = versions(kind, market, env_prefix)
    prior = active(kind, market, env_prefix)
    base = content_of(prior) if prior else _default_content(kind, market)
    changes = diff(base, norm)
    ts = _now()
    for v in vs:
        if v.get("is_active"):
            v = dict(v)
            v["is_active"] = False
            v["retired_at"] = ts
            ddb.put_item(table, v, env_prefix)
    new_version = (max(int(v.get("version") or 0) for v in vs) if vs else 0) + 1
    row = {**norm, "item_key": _item_key(kind, market), "config_key": config_key, "market_id": market,
           "version": new_version, "is_active": True, "effective_from": ts, "published_by": actor,
           "note": note, "source_ref": f"admin-console:publish by {actor}"}
    if rolled_back_from is not None:
        row["rolled_back_from"] = rolled_back_from
    ddb.put_item(table, row, env_prefix)
    discard_draft(kind, market, actor, env_prefix)
    from ..events import bus
    ev = bus.emit("config.published", {"kind": kind, "market_id": market, "version": new_version,
                                        "changes": len(changes), "published_by": actor}, actor=actor,
                  env_prefix=env_prefix)
    audit_log.record(actor, f"config.publish.{kind}", f"{kind}#{market}",
                     {"version": new_version, "changes": [c["path"] for c in changes][:40],
                      "rolled_back_from": rolled_back_from}, env_prefix=env_prefix)
    return {"kind": kind, "market_id": market, "version": new_version, "effective_from": ts,
            "changes": changes, "bus_event": ev}


def rollback(kind: str, market: str, to_version: int, actor: str,
             env_prefix: str | None = None) -> dict[str, Any]:
    target = next((v for v in versions(kind, market, env_prefix) if int(v.get("version") or 0) == int(to_version)), None)
    if target is None:
        raise NotFound(f"{kind}#{market} has no version {to_version}")
    out = publish(kind, market, actor, content=content_of(target), note=f"rollback to v{to_version}",
                  rolled_back_from=int(to_version), env_prefix=env_prefix)
    return {**out, "rolled_back_to": int(to_version)}


def flags(market: str, env_prefix: str | None = None) -> dict[str, bool]:
    """Active feature flags (defaults when never published)."""
    return {**FLAG_DEFAULTS, **(get("flags", market, env_prefix)["content"].get("flags") or {})}
