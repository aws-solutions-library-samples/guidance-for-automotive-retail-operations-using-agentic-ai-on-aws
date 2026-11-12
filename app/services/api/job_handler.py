"""L6 — Scheduler-invoked Lambda entrypoint for agentic jobs.

EventBridge Scheduler invokes this with a plain payload like
{"job": "equity-monitor", "market": "us"} (no API Gateway envelope). It runs the
named agentic job with apply=True and returns a summary that lands in the
Lambda logs / scheduler's retry record.

This is the "acts without a user request" entrypoint — the clock is the caller.

The eight agent-owned jobs run on their agent's AgentCore runtime when the
RETAIL_OPS_AGENT_RUNTIMES map names it (ADR 0009), else in-process as before.
`run_job` returns a JobOutcome; a call whose answer was lost after sending is
`unknown_after_send` and is never re-run here (the job may still be running).

Jobs (one table, shared by the Lambda entrypoint and the on-demand API route):

  equity-monitor          A4 nightly equity scan -> equity_flag cards,
                          equity.crossed_positive / lease.end_window_entered
  nurture-tick            A2 sales nurture cadence sweep
  stall-sweep             A2 stalled-journey detector -> journey.stalled
  rescore-profiles        A1 proactive re-rank -> discovery.match_found
  service-lane-monitor    A7 FOLLOW_UP equity -> service_handoff cards
  shop-load               A7 05:00 morning work
  service-followup-tick   A7 hourly declined-work reminders + due-service cards
  nightly-attribution     A5 ROI recompute -> budget-shift approval drafts
  predictive-scan         A7 nightly predictive part watch -> predictive Lead Desk leads + offers
  district-cycle          A6 weekly composites / alerts / draft plans
  store-digest            A5/A7 nightly model-written cockpit insights (the one job that calls
                          Bedrock, so it runs in its job Lambda, not a model-free runtime)
  autonomy-promotion      weekly outcome-loop -> autonomy level proposals (autonomy-promotion contract)

A schedule names its markets (`{"job": ..., "markets": ["us", "de"]}`); the
Lambda runs the job once per market. Every applied in-process run writes one
PLAT_AgentTraces row `job.<name>` (a runtime run writes `runtime.job.<name>`),
which is what the agent bar's "last ran" reads.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, NoReturn

from ...agentcore import client, registry
from ..agents import equity_monitor, rescore
from ..common import trace
from ..dealer import store_digest
from ..journeys import nurture_tick, stall_sweep
from ..oem import district_cycle
from ..platform import autonomy_promotion
from ..service import followup_tick, lane_monitor, predictive, shop_load


def _common(payload: dict[str, Any], apply: bool) -> dict[str, Any]:
    return {"market": payload.get("market", "us"), "apply": apply, "limit": payload.get("limit")}


JOBS: dict[str, Callable[[dict[str, Any], bool], dict[str, Any]]] = {
    "equity-monitor": lambda p, a: equity_monitor.run_equity_monitor(**_common(p, a)),
    "nurture-tick": lambda p, a: nurture_tick.run_nurture_tick(**_common(p, a)),
    "stall-sweep": lambda p, a: stall_sweep.run_stall_sweep(**_common(p, a)),
    "rescore-profiles": lambda p, a: rescore.run_rescore_profiles(**_common(p, a)),
    "service-lane-monitor": lambda p, a: lane_monitor.run_service_lane_monitor(**_common(p, a)),
    "shop-load": lambda p, a: shop_load.run_shop_load(**_common(p, a), dealer_id=p.get("dealer_id")),
    "service-followup-tick": lambda p, a: followup_tick.run_service_followup_tick(**_common(p, a)),
    "nightly-attribution": lambda p, a: district_cycle.run_nightly_attribution(**_common(p, a)),
    "predictive-scan": lambda p, a: predictive.run_predictive_scan(**_common(p, a)),
    "district-cycle": lambda p, a: district_cycle.run_district_cycle(
        **_common(p, a), district_id=p.get("district_id")),
    "store-digest": lambda p, a: store_digest.run_store_digest(**_common(p, a)),
    # apply=False maps to dry=True: the on-demand route can evaluate the loop
    # without publishing a level change (autonomy-promotion contract).
    "autonomy-promotion": lambda p, a: autonomy_promotion.run(
        market=p.get("market", "us"), dry=not a),
}


# Jobs with no runtime owner, and the agent each one runs as (trace `agent_id`).
# autonomy-promotion is platform plumbing, not an agent automation: no trace.
LOCAL_JOB_AGENT: dict[str, str] = {"district-cycle": "A6", "store-digest": "A5"}

_MARKET_RE = re.compile(r"^[a-z]{2}$")


class RuntimeJobError(RuntimeError):
    """A delegated job ended `failed` or `caller_error` on its agent's runtime."""


def _run_local(job: str, runner: Callable[[dict[str, Any], bool], dict[str, Any]],
               payload: dict[str, Any], apply: bool) -> dict[str, Any]:
    """Run a job in this process; an applied run writes one `job.<name>` trace
    (best-effort, like every trace) so "last ran" reflects it."""
    start, outcome = time.monotonic(), "error"
    try:
        out = runner(payload, apply)
        outcome = "ok"
        return out
    finally:
        agent = registry.job_owner(job) or LOCAL_JOB_AGENT.get(job)
        if apply and agent and trace.enabled():
            trace.write_trace(f"job.{job}", agent_id=agent, outcome=outcome,
                              latency_ms=int((time.monotonic() - start) * 1000),
                              market=payload.get("market") or "us",
                              routing_decision=f"scheduled job {job} (in-process)",
                              extra={"transport": "job-lambda"})


def job_payload(job: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Exactly the keys the job's runner reads: market, limit and the job's
    passthrough keys (shop-load: dealer_id). Every other key (job, apply,
    local, ...) is dropped, then the result is checked with
    registry.validate_job_payload (raises registry.ArgError naming the field)."""
    spec = registry.job_spec(job)
    passthrough = spec.passthrough if spec else ()
    raw = raw or {}
    kept = {k: raw[k] for k in ("market", "limit", *passthrough) if k in raw}
    return registry.validate_job_payload(kept, passthrough)


@dataclass
class JobOutcome:
    """One job run. `body` is the job summary for ok/local, else the status or error object."""
    status: str      # "ok" | "local" | "unknown_after_send" | "caller_error" | "failed"
    code: int        # 200 for ok/local, 202 for unknown_after_send, the runtime's code otherwise
    body: dict[str, Any]


def run_job(job: str, payload: dict[str, Any], apply: bool = True, *,
            timeout_s: int | None = None) -> JobOutcome:
    """Run a job: on its agent's runtime when one is configured (ADR 0009),
    else in-process. The payload is checked before the path is chosen, so both
    paths accept and reject the same input. A `lost` runtime call is never
    re-run here: the job may still be running there (`unknown_after_send`)."""
    runner = JOBS.get(job)
    if runner is None:
        raise ValueError(f"unknown job {job!r}")
    raw = payload or {}
    owner = registry.job_owner(job)
    if owner is None:                    # district-cycle, store-digest, autonomy-promotion: in-process
        return JobOutcome("local", 200, _run_local(job, runner, raw, apply))
    local = bool(raw.get("local"))
    p = job_payload(job, raw)
    if not local and client.enabled(owner):
        corr = trace.new_correlation_id()
        res = client.invoke_job(owner, job, p, apply=apply, correlation_id=corr, timeout_s=timeout_s or 280)
        if res.status == "ok":
            return JobOutcome("ok", 200, res.body)
        if res.status == "lost":
            return JobOutcome("unknown_after_send", 202,
                              {"status": "running", "agent": owner, "job": job, "correlation_id": corr})
        if res.status in ("failed", "caller_error"):
            body = dict(res.body)
            body.setdefault("error", res.error or f"runtime answered {res.code}")
            return JobOutcome(res.status, res.code, body)
        # not_configured / unavailable: nothing ran on the runtime; run here
    return JobOutcome("local", 200, _run_local(job, runner, p, apply))


def _unknown(job: str, out: JobOutcome) -> dict[str, Any]:
    return {"ok": False, "status": "unknown_after_send", "job": job, "agent": out.body.get("agent"),
            "correlation_id": out.body.get("correlation_id")}


def _raise(job: str, out: JobOutcome) -> NoReturn:
    agent = registry.job_owner(job)
    raise RuntimeJobError(f"job {job} on the {agent} runtime ended {out.status} "
                          f"({out.code}): {str(out.body.get('error'))[:200]}")


def run_job_dict(job: str, payload: dict[str, Any], apply: bool = True) -> dict[str, Any]:
    """The job summary as a dict (the older run_job contract): the summary for
    ok/local, the unknown_after_send object, or RuntimeJobError for failed and
    caller_error."""
    out = run_job(job, payload, apply)
    if out.status in ("ok", "local"):
        return out.body
    if out.status == "unknown_after_send":
        return _unknown(job, out)
    _raise(job, out)


def _run_scheduled(job: str, event: dict[str, Any]) -> dict[str, Any]:
    """One market's scheduled run -> the Lambda's answer for that market."""
    out = run_job(job, event, apply=True)
    if out.status in ("ok", "local"):
        return {"ok": True, "job": job, "summary": out.body}
    if out.status == "unknown_after_send":
        # Do not raise: Lambda's async retry would start a second run while the
        # first may still be working. One plain JSON line on stdout feeds the
        # job log group's metric filter {$.event = "runtime_job_unknown"} (P7).
        unknown = _unknown(job, out)
        print(json.dumps({"event": "runtime_job_unknown", "level": "ERROR", "job": job,
                          "agent": unknown["agent"], "correlation_id": unknown["correlation_id"]}), flush=True)
        return unknown
    _raise(job, out)


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    event = event or {}
    job = event.get("job", "equity-monitor")
    if job not in JOBS:
        return {"ok": False, "error": f"unknown job {job!r}", "known": sorted(JOBS)}
    markets = event.get("markets")
    if markets is None:
        return _run_scheduled(job, event)
    if not isinstance(markets, list) or not markets or not all(isinstance(m, str) and _MARKET_RE.match(m)
                                                                for m in markets):
        return {"ok": False, "error": "markets must be a non-empty list of two-letter market codes"}
    # Every market runs even when one fails; a failure is raised after the rest
    # finish (the jobs are idempotent, so the async retry re-running them is safe).
    by_market: dict[str, Any] = {}
    failed: list[RuntimeJobError] = []
    for m in dict.fromkeys(markets):
        try:
            by_market[m] = _run_scheduled(job, {k: v for k, v in event.items() if k != "markets"} | {"market": m})
        except RuntimeJobError as e:
            by_market[m] = {"ok": False, "error": str(e)[:300]}
            failed.append(e)
    if failed:
        raise failed[0]
    return {"ok": all(r.get("ok") for r in by_market.values()), "job": job, "by_market": by_market}
