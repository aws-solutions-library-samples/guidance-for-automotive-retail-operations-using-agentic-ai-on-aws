"""Admin Console — Agents panel (spec §9A AR-9, §8 observability, acceptance #34).

Per agent: invocation count, tool error rate, latency p50/p95, last activity,
top tools, recent traces, guardrail blocks; per action: current autonomy level
(and hard-rule ceiling) plus shown / accepted / converted from the outcome loop
(`DEALER_Recommendations`). Read model over PLAT_AgentTraces, OEM_GuardrailEvents,
PLAT_ConfigAutonomy, DEALER_Recommendations; admin audit rows are excluded.

Each row also says where the agent runs (ADR 0009): `runtime.mode` is
`agentcore` when this process has the agent's runtime configured, `in_process`
otherwise, and `quick` for A6 (Amazon Quick). `runtime_calls` counts trace rows
the agent's runtime tagged `transport: agentcore-runtime` (its `runtime.*` spans
and A3's `assistant_agent` row); `fallbacks` counts the client's
`in-process-fallback` spans, which are left out of the other figures.
"""
from __future__ import annotations

from typing import Any

from ...agentcore import client as _agent_client
from ...agentcore import registry as _registry
from ..common import ddb
from ..platform import recommendations
from . import audit_log
from . import autonomy

AGENTS = {
    "A1": "Personalized Vehicle Discovery", "A2": "Purchase Journey Orchestration", "A3": "Dealer Sales Advisor",
    "A4": "Trade-In & Upgrade Advisor", "A5": "Campaign ROI Optimizer", "A6": "OEM Sales Administrator",
    "A7": "Service Operations",
}
_OK = ("ok", "success", "allowed", "allow")
_QUICK = "A6"                        # hosted in Amazon Quick, never an AgentCore runtime
_RUNTIME_TRANSPORT = "agentcore-runtime"
_FALLBACK_TRANSPORT = "in-process-fallback"


def _runtime(aid: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Where the agent runs and how often its runtime answered or was bypassed (planner P3)."""
    if aid == _QUICK:
        mode, engine = "quick", "quick"
    else:
        spec = _registry.agent(aid)
        mode = "agentcore" if _agent_client.enabled(aid) else "in_process"
        engine = spec.engine if spec else None
    calls = sum(1 for t in rows if t.get("transport") == _RUNTIME_TRANSPORT
                and ((t.get("tool") or "").startswith("runtime.") or t.get("tool") == "assistant_agent"))
    fallbacks = sum(1 for t in rows if t.get("transport") == _FALLBACK_TRANSPORT)
    return {"mode": mode, "engine": engine, "runtime_calls": calls, "fallbacks": fallbacks}


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    k = max(0, min(len(v) - 1, int(round(q * (len(v) - 1)))))
    return round(v[k], 1)


def health(market: str = "us", agent: str | None = None, recent: int = 8,
           env_prefix: str | None = None) -> dict[str, Any]:
    traces = [t for t in ddb.scan_all("PLAT_AgentTraces", env_prefix)
              if t.get("agent_id") != audit_log.AGENT_ID and (t.get("market_id") in (market, None))]
    blocks = [g for g in ddb.scan_filter("OEM_GuardrailEvents", "market_id", market, env_prefix)
              if g.get("outcome") == "blocked"]
    auto = autonomy.list_autonomy(market, env_prefix)["actions"]
    stats = recommendations.stats(env_prefix=env_prefix)["actions"]

    out = []
    for aid, name in AGENTS.items():
        if agent and aid != agent:
            continue
        mine = [t for t in traces if t.get("agent_id") == aid]
        ts = [t for t in mine if t.get("transport") != _FALLBACK_TRANSPORT]
        lat = [float(t["latency_ms"]) for t in ts if isinstance(t.get("latency_ms"), (int, float))]
        errors = sum(1 for t in ts if (t.get("outcome") or "ok") not in _OK)
        tools: dict[str, int] = {}
        for t in ts:
            tools[t.get("tool") or "?"] = tools.get(t.get("tool") or "?", 0) + 1
        ts.sort(key=lambda t: t.get("ts") or "", reverse=True)
        prefix = aid.lower() + "."
        actions = []
        for a in auto:
            if not (a.get("action_id") or "").startswith(prefix):
                continue
            act = a["action_id"][len(prefix):]
            s = next((x for x in stats if x["agent_id"] == aid and x["action"] == act), None)
            actions.append({"action_id": a["action_id"], "level": a.get("level"), "ceiling": a.get("ceiling"),
                            "hard_rule": a.get("hard_rule"), "version": a.get("version"),
                            "shown": (s or {}).get("shown", 0), "accepted": (s or {}).get("accepted", 0),
                            "converted": (s or {}).get("converted", 0),
                            "acceptance_rate": (s or {}).get("acceptance_rate"),
                            "conversion_rate": (s or {}).get("conversion_rate")})
        # outcome-loop actions with no autonomy row still show up
        for s in stats:
            if s["agent_id"] == aid and not any(x["action_id"] == f"{prefix}{s['action']}" for x in actions):
                actions.append({"action_id": f"{prefix}{s['action']}", "level": None, "ceiling": None,
                                "hard_rule": False, "version": None, **{k: s[k] for k in
                                ("shown", "accepted", "converted", "acceptance_rate", "conversion_rate")}})
        n = len(ts)
        err_rate = round(errors / n, 4) if n else None
        status = "idle" if not n else "degraded" if (err_rate or 0) > 0.05 or (_pct(lat, .95) or 0) > 6000 else "healthy"
        out.append({
            "agent_id": aid, "name": name, "status": status, "invocations": n, "errors": errors,
            "error_rate": err_rate, "latency_p50_ms": _pct(lat, .5), "latency_p95_ms": _pct(lat, .95),
            "last_activity": ts[0].get("ts") if ts else None,
            "top_tools": sorted(({"tool": k, "count": v} for k, v in tools.items()), key=lambda x: -x["count"])[:6],
            "guardrail_blocks": sum(1 for g in blocks if g.get("agent_id") == aid),
            "recent_traces": [{k: t.get(k) for k in ("trace_id", "ts", "tool", "outcome", "latency_ms",
                                                     "autonomy_level", "correlation_id", "journey_id",
                                                     "routing_decision")} for t in ts[:recent]],
            "actions": sorted(actions, key=lambda x: x["action_id"]),
            "runtime": _runtime(aid, mine),
        })
    return {"market_id": market, "agents": out, "total_invocations": sum(a["invocations"] for a in out),
            "total_guardrail_blocks": len(blocks),
            "source_ref": "PLAT_AgentTraces + OEM_GuardrailEvents + PLAT_ConfigAutonomy + DEALER_Recommendations"}
