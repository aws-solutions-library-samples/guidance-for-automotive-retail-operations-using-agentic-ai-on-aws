"""A2 — Sales Package aggregation (invoke_agent + aggregate_package), spec §3.2.

A2's supervisor/A2A role: pull the specialist agents' outputs for a journey into
one SalesPackage for the dealer console. Inside the Journey Agent's AgentCore
runtime (`remote=True`, ADR 0009) each specialist is called on its own runtime
with an A2A-shaped InvokeAgentRuntime request, in parallel. Without runtimes
(`remote=False`: the service Lambda, local dev) `invoke_agent` dispatches
in-process (same contract, no network hop). Independent calls are gathered and
combined; the package is persisted to DEALER_SalesPackages and is the poll
target for GET /journeys/{id}/package.

  invoke_agent(agent, journey_id)       -> that specialist's output (or an error card)
  build_sales_package(journey, actor)   -> aggregate A1/A3/A4 + finance into a package
  get_package(journey_id)               -> the latest persisted package for a journey

Grounding: every figure in the package comes from the specialist's own tool
output (which itself carries source_refs); A2 only combines, never invents.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from ...agentcore import registry
from ..agents import discovery, equity
from ..common import ddb
from . import assist, supervisor

_PACKAGES = "DEALER_SalesPackages"

# The specialists A2 aggregates for a purchase package.
#   A1 discovery (vehicle matches), A3 assist (customer 360 + recs + objections),
#   A4 equity (trade/upgrade). A5/A6 are pulled elsewhere (campaigns / Gateway).
_AGENTS = ("A1", "A3", "A4")
# Each specialist's runtime tool (agent registry, ADR 0009) and its time budget.
_TOOLS = {a: registry.agent(a).tools[0].name for a in _AGENTS}   # rank_vehicles, build_assist, build_equity
_SPECIALIST_TIMEOUT_S = 8


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def invoke_agent(agent: str, journey_id: str, env_prefix: str | None = None) -> dict[str, Any]:
    """A2A dispatch to one specialist (in-process). Returns {agent, ok, result|error}.

    Never raises — a specialist failing (e.g. NotFound) yields an error card so
    the package still assembles from the agents that did respond."""
    try:
        if agent == "A1":
            return {"agent": "A1", "ok": True, "result": discovery.rank_vehicles(journey_id, env_prefix)}
        if agent == "A3":
            return {"agent": "A3", "ok": True, "result": assist.build_assist(journey_id, env_prefix)}
        if agent == "A4":
            return {"agent": "A4", "ok": True, "result": equity.build_equity(journey_id, env_prefix)}
        return {"agent": agent, "ok": False, "error": f"unknown agent {agent!r}"}
    except Exception as e:
        return {"agent": agent, "ok": False, "error": f"{type(e).__name__}: {e}"}


def _summarize(journey: dict[str, Any], results: dict[str, dict[str, Any]],
               nba: dict[str, Any]) -> str:
    """One-line package summary grounded in what the specialists returned."""
    parts = [f"{journey.get('stage')} journey"]
    a1 = results.get("A1", {})
    if a1.get("ok"):
        matches = a1["result"].get("matches") or a1["result"].get("ranked") or []
        if matches:
            parts.append(f"{len(matches)} vehicle matches")
    a4 = results.get("A4", {})
    if a4.get("ok"):
        pos = a4["result"].get("equity_position")
        if pos:
            parts.append(f"{pos} equity")
    a3 = results.get("A3", {})
    if a3.get("ok"):
        recs = a3["result"].get("recommendations") or []
        if recs:
            parts.append(f"{len(recs)} live recommendations")
    parts.append(f"NBA: {nba.get('chosen', {}).get('action', '—')}")
    return "; ".join(parts)


def _remote_card(agent: str, journey_id: str, claims: dict[str, Any], corr: str | None,
                 env_prefix: str | None) -> tuple[dict[str, Any], str]:
    """One specialist through its AgentCore runtime: (card, transport).

    ok -> its result; not configured / unavailable (nothing ran) -> the same
    function in-process; lost -> an error card and NO local re-run, so the
    specialist's model calls are not repeated; failed / caller_error -> an
    error card carrying the status."""
    from ...agentcore import client
    res = client.invoke_tool(agent, _TOOLS[agent], {"journey_id": journey_id}, claims=claims,
                             correlation_id=corr, caller="A2", timeout_s=_SPECIALIST_TIMEOUT_S)
    if res.status == "ok":
        return {"agent": agent, "ok": True, "result": res.body}, "agentcore-runtime"
    if res.status in ("not_configured", "unavailable"):
        return invoke_agent(agent, journey_id, env_prefix), "in-process"
    if res.status == "lost":
        display = registry.agent(agent).display
        return ({"agent": agent, "ok": False,
                 "error": f"the {display} did not answer in {_SPECIALIST_TIMEOUT_S} s"}, "agentcore-runtime")
    return ({"agent": agent, "ok": False, "status": res.status,
             "error": res.error or f"runtime answered {res.code}"}, "agentcore-runtime")


def _fan_out(journey: dict[str, Any], claims: dict[str, Any], env_prefix: str | None
             ) -> tuple[dict[str, dict[str, Any]], dict[str, Any], dict[str, str]]:
    """A1, A3, A4 on their runtimes and the next-best-action, all four in flight
    together (design §6.4: the package fits in the 20 s A2 budget)."""
    from concurrent.futures import ThreadPoolExecutor

    from ..common import trace
    jid = journey.get("journey_id")
    # ContextVars are not copied into pool threads: pass the correlation id explicitly.
    corr = trace.CURRENT_CORRELATION_ID.get()
    with ThreadPoolExecutor(max_workers=4) as pool:
        cards = {a: pool.submit(_remote_card, a, jid, claims, corr, env_prefix) for a in _AGENTS}
        nba_f = pool.submit(supervisor.select_next_best_action, journey, env_prefix=env_prefix)
        done = {a: f.result() for a, f in cards.items()}
        nba = nba_f.result()
    return {a: c for a, (c, _t) in done.items()}, nba, {a: t for a, (_c, t) in done.items()}


def build_sales_package(journey: dict[str, Any], actor: str = "A2",
                        env_prefix: str | None = None, *, claims: dict[str, Any] | None = None,
                        remote: bool = False) -> dict[str, Any]:
    """Aggregate the specialist outputs for a journey into a SalesPackage.

    Persists to DEALER_SalesPackages (deterministic id per journey so repeated
    builds upsert). Returns the full package including each agent's result and a
    combined summary + next-best-action.

    remote=False (the service Lambda, local dev): the specialists run
    in-process, one after another. remote=True (inside the Journey Agent's
    runtime, ADR 0009): A1, A3 and A4 are called on their own runtimes with the
    caller's `claims`, in parallel with the next-best-action, and the package
    gains `agent_transport` (which path served each specialist)."""
    jid = journey.get("journey_id")
    transport: dict[str, str] | None = None
    if remote:
        if claims is None:
            raise ValueError("remote Sales Package builds need the caller's claims")
        results, nba, transport = _fan_out(journey, claims, env_prefix)
    else:
        # invoke_agent for each specialist (independent — safe to run sequentially in-process).
        results = {a: invoke_agent(a, jid, env_prefix) for a in _AGENTS}
        nba = supervisor.select_next_best_action(journey, env_prefix=env_prefix)

    # "What we know" (durable-memory contract): the customer's durable memory — preferences,
    # objections, promises — rides on the package so the advisor opens the deal
    # with it. Guarded: an empty recall adds no block.
    from ..platform import memory as _memory
    what_we_know = _memory.recall(journey.get("customer_id"), k=5, env_prefix=env_prefix)

    pkg_id = f"SP-{jid}"
    now = _now()
    summary = _summarize(journey, results, nba)
    package = {
        "package_id": pkg_id,
        "journey_id": jid,
        "customer_id": journey.get("customer_id"),
        "stage": journey.get("stage"),
        "summary": summary,
        "next_best_action": nba["chosen"].get("action"),
        "agents": {a: (r.get("result") if r.get("ok") else {"error": r.get("error")})
                   for a, r in results.items()},
        "agent_status": {a: r.get("ok") for a, r in results.items()},
        "generated_at": now,
        "generated_by": actor,
        "source_ref": f"A2.build_sales_package:{jid}",
    }
    if transport is not None:
        package["agent_transport"] = transport
    if what_we_know:
        package["what_we_know"] = what_we_know
    ddb.put_item(_PACKAGES, {
        "package_id": pkg_id, "journey_id": jid, "customer_id": journey.get("customer_id"),
        "summary": summary, "next_best_action": package["next_best_action"],
        "generated_at": now, "source_ref": package["source_ref"],
    }, env_prefix)
    return package


def get_package(journey_id: str, env_prefix: str | None = None) -> dict[str, Any] | None:
    """Latest persisted package header for a journey (the poll target)."""
    rows = ddb.scan_filter(_PACKAGES, "journey_id", journey_id, env_prefix)
    if not rows:
        return None
    return max(rows, key=lambda p: p.get("generated_at") or "")
