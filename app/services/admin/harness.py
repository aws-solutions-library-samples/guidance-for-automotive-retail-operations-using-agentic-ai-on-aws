"""Admin › Guardrails & policies — test harnesses (spec §5.17, acceptance #15b, #24).

  test_guardrails(text, draft_rules)   -> outcome under the ACTIVE rules (the real
                                          check_guardrails, not logged) and, when a
                                          draft is supplied, the outcome the draft
                                          would produce for the same matches
  sweep_guardrails(prompts)            -> batch run (allow / deny sets) with counts
  preview_suggestions(journey, cfg)    -> A3 contextual suggestions for a seeded
                                          journey under the active config and,
                                          optionally, a draft config
"""
from __future__ import annotations

from typing import Any

from ..agents import suggestions as a3
from ..common import ddb
from ..oem import guardrails

_SEV = {"allow": 0, "warn": 1, "require_approval": 2, "block": 3}


def test_guardrails(text: str, market: str = "us", draft_rules: list[dict[str, Any]] | None = None,
                    env_prefix: str | None = None) -> dict[str, Any]:
    active = guardrails.check_guardrails(text, {"market_id": market, "log": False, "agent_id": "ADMIN",
                                                "tool": "policy_test_harness"}, env_prefix)
    out = {"text_length": len(text or ""), "active": active}
    if draft_rules is not None:
        actions = {r["rule_id"]: r["action"] for r in draft_rules}
        outcome = "allow"
        matched = []
        for v in active["violations"]:
            act = actions.get(v["rule_id"], v["action"])
            matched.append({**v, "action": act})
            if _SEV.get(act, 3) > _SEV[outcome]:
                outcome = act
        out["draft"] = {"outcome": outcome, "allowed": outcome != "block", "violations": matched,
                        "changed": outcome != active["outcome"]}
    return out


def sweep_guardrails(prompts: list[dict[str, Any]], market: str = "us",
                     env_prefix: str | None = None) -> dict[str, Any]:
    """prompts: [{text, expect: 'allow'|'block'}] -> per-prompt result + accuracy."""
    results = []
    for p in prompts[:200]:
        r = guardrails.check_guardrails(p.get("text") or "", {"market_id": market, "log": False}, env_prefix)
        got = "block" if r["outcome"] in ("block", "require_approval") else "allow"
        results.append({"text": (p.get("text") or "")[:160], "expect": p.get("expect"), "outcome": r["outcome"],
                        "rules": [v["rule_id"] for v in r["violations"]],
                        "pass": p.get("expect") in (None, got)})
    allow = [x for x in results if x["expect"] == "allow"]
    deny = [x for x in results if x["expect"] == "block"]
    return {"total": len(results), "passed": sum(x["pass"] for x in results),
            "false_positives": sum(1 for x in allow if not x["pass"]),
            "false_negatives": sum(1 for x in deny if not x["pass"]), "results": results}


def _with_config(journey: dict[str, Any], cfg: dict[str, Any], env_prefix: str | None) -> dict[str, Any]:
    """Same rule as A3.generate_contextual_suggestions, evaluated under `cfg`."""
    threshold = float(cfg.get("confidence_threshold") or 0.0)
    allowed = set(cfg.get("precompute_stages") or a3.JOURNEY_STAGES)
    stage = journey.get("stage")
    activity = a3._recent_activity(journey.get("customer_id"), journey.get("journey_id"), env_prefix)
    by_type: dict[str, dict[str, Any]] = {}
    for a in activity:
        by_type.setdefault(a.get("type"), a)
    out = []
    for cand in a3._MATRIX.get(stage, []):
        if cand["confidence"] < threshold:
            continue
        cited = next((by_type[t] for t in cand["signal_types"] if t in by_type), None)
        if cited is None:
            continue
        out.append({"text": cand["text"], "action": cand["action"], "confidence": cand["confidence"],
                    "why": f"{cited.get('type')} on {(cited.get('ts') or '')[:10]}"})
    out.sort(key=lambda s: s["confidence"], reverse=True)
    return {"in_precompute_scope": stage in allowed, "confidence_threshold": threshold, "suggestions": out}


def preview_suggestions(journey_id: str, draft: dict[str, Any] | None = None,
                        env_prefix: str | None = None) -> dict[str, Any]:
    j = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id}, env_prefix)
    if j is None:
        raise KeyError(f"journey {journey_id} not found")
    active = a3.generate_contextual_suggestions(j, env_prefix=env_prefix)
    out = {"journey_id": journey_id, "stage": j.get("stage"), "active": active}
    if draft is not None:
        out["draft"] = _with_config(j, draft, env_prefix)
    return out
