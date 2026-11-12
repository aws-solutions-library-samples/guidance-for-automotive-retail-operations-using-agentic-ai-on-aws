"""Nightly store digest (agentic job `store-digest`): model-written [AI INSIGHT]s.

The Store Cockpit (spec §5.14) and the Service Cockpit (§5.20) compose a
figures-only insight on every request from the same numbers they show — there
is no agent in that request path (R5). This job is the agent half: once a night
it materializes each store's cockpit, asks Amazon Bedrock (`generate.ground`,
grounding-only prompt + the managed "digest" guardrail: the main guardrail's
content, topic, word and PII policies with a relaxed grounding check, user
decision 2026-10-22) to write the insight from exactly those figures, and stores the result as a recommendation card for the store's
manager. The cockpits then show the written insight, labelled with who wrote it
and when, and fall back to the figures-only text when none is on file.

  run_store_digest(market, apply, limit)   -> summary (the job body)
  stored_insight(market, scope, surface)   -> the written insight for a cockpit, or None
  scope_key(scope, dealer_id)              -> "ALL" | "<dealer_id>" | None

Who writes what (the agent bar names these automations):
  * Store Cockpit tabs  -> the Campaign Agent (A5), `a5.store_digest`
  * Service Cockpit     -> the Service Agent (A7), `a7.cockpit_insight`

Scopes: one per store in the market plus "ALL" (the market-wide view the OEM and
admin roles open). A multi-store scope has no digest and keeps the figures-only
text.

Storage: DEALER_Recommendations, one row per (market, scope, surface), id
`REC-INSIGHT-<market>-<scope>-<surface>`, action `store_insight`. A row whose
figures are unchanged since the last run is re-confirmed without a model call
(`facts_hash`), so a quiet store costs nothing. A model failure or a guardrail
fallback never overwrites a written insight; it is counted and the old one
ages out (STALE_HOURS) to the figures-only text. A grounding-check fallback is
retried once; a topic block (credit decisions, wholesale allocation, …) is
policy and is not. The figures of a fallback are remembered (`attempted_hash`),
so a later night with the same figures makes no call (status `held`): the
nightly cost follows how many figures changed, not how many tabs exist.

Grounding / invariants:
  * the model sees only the cockpit's own KPIs, the figures-only summary and a
    few rows of the tab's detail table — the same numbers on screen;
  * every stored insight carries the cockpit's `sources` and its engine/model;
  * dry run (apply=False) computes the facts and makes no model call.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..common import ddb
from ..platform import generate
from . import cockpit

_RECS = "DEALER_Recommendations"
ACTION = "store_insight"
SERVICE_SURFACE = "service_cockpit"
STALE_HOURS = 26          # a nightly insight older than this falls back to the figures-only text
RECONFIRM_DAYS = 7        # unchanged figures re-use the written text for up to a week
_WORKERS = 4
SOURCE = "agentic:store_digest (nightly; Bedrock grounded on the cockpit read models)"

# Tight on purpose: the managed guardrail's contextual-grounding check rejects text
# that adds advice, causes or numbers the figures do not hold (measured on a live
# store, 2026-10-22: this phrasing kept more insights on the figures than an open
# "pattern + action" ask).
QUESTION = ("Rewrite the figures-only summary for the {label} as a sharper insight for the manager: at most "
            "two sentences. State only what the figures given say, quoting them exactly; if you name an action, "
            "use only the one the figures-only summary names. Do not add advice, causes or numbers of your own. "
            "Plain text: no markdown, no heading or label.")
GROUNDING_FALLBACK = "deterministic (guardrail grounding check)"
_STORE_PERSONA = ("the Campaign Agent writing the nightly store digest for a dealership's general "
                  "manager. Write in English")
_SERVICE_PERSONA = ("the Service Agent writing the nightly fixed-ops digest for a dealership's "
                    "service manager. Write in English")


def _now_dt() -> _dt.datetime:
    return _dt.datetime.now(_dt.UTC).replace(microsecond=0)


def _iso(d: _dt.datetime) -> str:
    return d.isoformat().replace("+00:00", "Z")


def _parse(ts: Any) -> _dt.datetime | None:
    try:
        return _dt.datetime.fromisoformat(str(ts))
    except ValueError:
        return None


def scope_key(scope: dict[str, Any], dealer_id: str | None = None) -> str | None:
    """The digest scope a cockpit request reads: one store, the market-wide view,
    or None (a multi-store scope, which has no digest)."""
    dealers, is_oem = cockpit._dealer_scope(scope, dealer_id)
    if len(dealers) == 1:
        return next(iter(dealers))
    if is_oem and not dealers:
        return "ALL"
    return None


def rec_id(market: str, scope: str, surface: str) -> str:
    return f"REC-INSIGHT-{market}-{scope}-{surface}"


_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set[float]:
    out: set[float] = set()
    for tok in _NUMBER.findall(text or ""):
        try:
            out.add(round(float(tok.replace(",", "")), 3))
        except ValueError:
            pass
    return out


def figures_agree(text: str, facts: list[str]) -> bool:
    """True when every number the written insight states is still among the current facts.
    The nightly text is written once; the figures under it move during the day (an order
    is received, an estimate is decided), and a sentence like "2 ROs at 190.3 hours" must
    not outlive the figures that said so. Percent facts count in both forms (78% / 0.78)."""
    have = _numbers(" ".join(facts))
    have |= {round(v * 100, 3) for v in have if 0 < v <= 1.5} | {round(v / 100, 3) for v in have}
    return _numbers(text) <= have


def stored_insight(market: str, scope: str | None, surface: str, env_prefix: str | None = None,
                   now: _dt.datetime | None = None, facts: list[str] | None = None) -> dict[str, Any] | None:
    """The written insight for a cockpit surface when one is on file, fresh, and — when the
    caller passes today's `facts` — still stating only figures that are true now."""
    if not scope:
        return None
    try:
        row = ddb.get_item(_RECS, {"recommendation_id": rec_id(market, scope, surface)}, env_prefix)
    except Exception:  # noqa: BLE001 - a read failure keeps the figures-only text
        return None
    if not row or not row.get("text"):
        return None
    checked = _parse(row.get("checked_at") or row.get("generated_at"))
    if checked is None or ((now or _now_dt()) - checked).total_seconds() > STALE_HOURS * 3600:
        return None
    if facts is not None and not figures_agree(clean_text(row["text"]), facts):
        return None   # the figures moved since the night run: show the figures-only text
    return {"text": clean_text(row["text"]), "sources": list(row.get("sources") or []),
            "written_by": row.get("agent_id"), "written_at": row.get("generated_at"),
            "checked_at": row.get("checked_at"), "engine": row.get("engine"), "model": row.get("model"),
            "recommendation_id": row["recommendation_id"]}


# --- facts -----------------------------------------------------------------------

def _fmt(value: Any, fmt: str | None, currency: str | None) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        if fmt == "pct":
            return f"{round(float(value) * 100)}%"
        if fmt == "money":
            return f"{float(value):,.0f}{' ' + currency if currency else ''}"
        return f"{value:g}" if isinstance(value, float) else str(value)
    return str(value)


def _row_text(row: dict[str, Any]) -> str:
    keep = [(k, v) for k, v in row.items()
            if k not in ("link", "source_ref", "body") and isinstance(v, (str, int, float)) and not isinstance(v, bool)]
    return ", ".join(f"{k}={v}" for k, v in keep[:8])


def tab_facts(tab: str, data: dict[str, Any], currency: str | None = None) -> list[str]:
    """The figures the model may use for one Store Cockpit tab: its KPIs, the
    figures-only summary and up to five rows of its main detail table. Money is
    stated in the tab's currency, else the market's (`currency`), and the
    currency is a fact of its own, so the model never guesses a symbol."""
    currency = data.get("currency") or currency
    facts = [f"Currency for every money figure: {currency}"] if currency else []
    for k in data.get("kpis") or []:
        v = _fmt(k.get("value"), k.get("format"), currency)
        if v is None:
            continue
        extra = f" ({k['delta']})" if k.get("delta") else ""
        facts.append(f"{k['label']}: {v}{extra}")
    insight = (data.get("insight") or {}).get("text")
    if insight:
        facts.append(f"Figures-only summary: {insight}")
    for key, rows in data.items():
        if key in ("kpis", "insight") or not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            continue
        facts.extend(f"{key} row: {_row_text(r)}" for r in rows[:5] if _row_text(r))
        break
    return facts


def service_facts(view: dict[str, Any], currency: str | None = None) -> list[str]:
    """The figures the model may use for the Service Cockpit: the market's
    currency, its scalar tiles and the figures-only summary."""
    facts = [f"Currency for every money figure: {currency}"] if currency else []
    facts += [f"{k.replace('_', ' ')}: {_fmt(v, 'pct' if k.endswith('_rate') else None, None)}"
              for k, v in sorted((view.get("tiles") or {}).items())
              if _fmt(v, None, None) is not None]
    insight = (view.get("insight") or {}).get("text")
    if insight:
        facts.append(f"Figures-only summary: {insight}")
    return facts


def _hash(facts: list[str]) -> str:
    return hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()[:16]


def _guardrail_key() -> str:
    """The guardrail id:version this run carries ("none" when none is set)."""
    cfg = generate.guardrail_config("digest") or {}
    return f"{cfg.get('guardrailIdentifier', 'none')}:{cfg.get('guardrailVersion', '')}"


_MARKUP = re.compile(r"\*\*|__|`")
_LABEL = re.compile(r"^\s*(?:#+\s*)?\[?\s*AI\s+INSIGHT\s*\]?\s*[:\-—–]?\s*", re.IGNORECASE)


def clean_text(text: str) -> str:
    """The insight as plain text: the cockpit already shows the [AI INSIGHT]
    label, so a model's own label and markdown emphasis are dropped."""
    return _LABEL.sub("", _MARKUP.sub("", text or "")).strip()


# --- the job ---------------------------------------------------------------------

def _market_currency(market: str, env_prefix: str | None) -> str | None:
    """The market's ISO currency (OEM_Markets), or None when the row has none."""
    row = ddb.get_item("OEM_Markets", {"market_id": market}, env_prefix) or {}
    return row.get("currency")


def _scopes(market: str, env_prefix: str | None) -> list[tuple[str, dict[str, Any]]]:
    dealers = sorted({j.get("dealer_id") for j in ddb.scan_filter("DEALER_Journeys", "market_id", market, env_prefix)
                      if j.get("dealer_id")})
    out = [("ALL", {"is_oem": True, "market_id": market, "dealer_ids": set()})]
    out += [(d, {"is_oem": False, "market_id": market, "dealer_ids": {d}}) for d in dealers]
    return out


def _task(market: str, scope: str, surface: str, agent: str, label: str, facts: list[str],
          fallback: str, sources: list[str], apply: bool, now: _dt.datetime,
          env_prefix: str | None) -> dict[str, Any]:
    rid = rec_id(market, scope, surface)
    digest = _hash(facts)
    out: dict[str, Any] = {"scope": scope, "surface": surface, "facts": len(facts)}
    if not facts:
        return {**out, "status": "no_facts"}
    if not apply:
        return {**out, "status": "would_write"}
    prev = ddb.get_item(_RECS, {"recommendation_id": rid}, env_prefix)
    gen_at = _parse((prev or {}).get("generated_at"))
    if prev and prev.get("facts_hash") == digest and prev.get("text") and gen_at \
            and (now - gen_at).total_seconds() <= RECONFIRM_DAYS * 86400:
        ddb.put_item(_RECS, {**prev, "checked_at": _iso(now)}, env_prefix)
        return {**out, "status": "unchanged"}
    tried_at = _parse((prev or {}).get("attempted_at"))
    guard = _guardrail_key()
    if prev and prev.get("attempted_hash") == digest and prev.get("attempt_guardrail") == guard and tried_at \
            and (now - tried_at).total_seconds() <= RECONFIRM_DAYS * 86400:
        # this guardrail already held text for these exact figures: no new call
        # (a changed guardrail or new figures try again)
        return {**out, "status": "held"}
    persona = _SERVICE_PERSONA if surface == SERVICE_SURFACE else _STORE_PERSONA
    question = QUESTION.format(label=label)
    gen = generate.ground(question, facts, [], persona=persona, fallback=fallback, max_tokens=220,
                          guardrail="digest")
    if gen.get("engine") == GROUNDING_FALLBACK:
        # The guardrail's grounding check found the text drifting from the figures;
        # one fresh sample often stays on them. A topic or content block is policy
        # and is never retried.
        gen = generate.ground(question, facts, [], persona=persona, fallback=fallback, max_tokens=220,
                              guardrail="digest")
        out["retried"] = True
    text = clean_text(gen.get("text") or "")
    if gen.get("engine") != "bedrock" or not text:
        # Keep any written insight (it ages out on its own) and remember these
        # figures, so a later night with the same figures makes no call.
        base = prev or {"recommendation_id": rid, "action": ACTION, "agent_id": agent, "market_id": market,
                        "dealer_id": None if scope == "ALL" else scope, "surface": surface,
                        "shown": 0, "accepted": 0, "converted": 0, "source_ref": SOURCE}
        ddb.put_item(_RECS, {**base, "attempted_hash": digest, "attempted_at": _iso(now),
                             "attempt_engine": gen.get("engine"), "attempt_guardrail": guard}, env_prefix)
        return {**out, "status": "fallback", "engine": gen.get("engine")}
    ddb.put_item(_RECS, {
        "recommendation_id": rid, "action": ACTION, "agent_id": agent, "market_id": market,
        "dealer_id": None if scope == "ALL" else scope, "surface": surface,
        "text": text, "sources": sources, "facts_hash": digest,
        "engine": gen["engine"], "model": gen.get("model"),
        "generated_at": _iso(now), "checked_at": _iso(now),
        "shown": 0, "accepted": 0, "converted": 0, "shown_at": _iso(now),
        "autonomy_level": "recommend", "reason": f"Nightly {label} insight",
        "source_ref": SOURCE,
    }, env_prefix)
    return {**out, "status": "written"}


def run_store_digest(market: str = "us", apply: bool = False, limit: int | None = None,
                     env_prefix: str | None = None, now: _dt.datetime | None = None) -> dict[str, Any]:
    """Write the nightly cockpit insights for every store scope in a market.

    apply=False is a dry run: it builds every scope's facts and reports what it
    would write, without a model call or a write."""
    from ..service import desk
    at = (now or _now_dt()).replace(microsecond=0)
    scopes = _scopes(market, env_prefix)
    if limit is not None:
        scopes = scopes[:limit]
    currency = _market_currency(market, env_prefix)
    tasks = []
    for key, scope in scopes:
        snap = cockpit.snapshot(scope, None, market, env_prefix, fresh=True, overlay=False)
        for tab in cockpit.TABS:
            data = snap["tabs"][tab]
            tasks.append((key, tab, "A5", f"store's {cockpit.TAB_LABELS[tab]} tab", tab_facts(tab, data, currency),
                          (data.get("insight") or {}).get("text") or "",
                          list((data.get("insight") or {}).get("sources") or [])))
        svc = desk.cockpit({**scope, "role": "dealer_service_manager"}, market, env_prefix=env_prefix)
        tasks.append((key, SERVICE_SURFACE, "A7", "Service Cockpit", service_facts(svc, currency),
                      (svc.get("insight") or {}).get("text") or "",
                      list((svc.get("insight") or {}).get("sources") or [])))
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        results = list(pool.map(lambda t: _task(market, t[0], t[1], t[2], t[3], t[4], t[5], t[6],
                                                apply, at, env_prefix), tasks))
    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"job": "store-digest", "market": market, "applied": apply, "ran_at": _iso(at),
            "scopes": len(scopes), "insights": len(results), "by_status": counts,
            "retried": sum(1 for r in results if r.get("retried")),
            "model": generate.MODEL if apply and generate.enabled() else None,
            "sample": [r for r in results if r["status"] not in ("unchanged",)][:12],
            "source_ref": SOURCE}
