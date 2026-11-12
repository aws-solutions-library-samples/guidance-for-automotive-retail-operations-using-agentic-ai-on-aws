"""Observability — correlation-id trace helper (spec §8 / AR-6).

One `correlation_id` threads a single request across the agents and tools it
touches, and each meaningful step is a row in PLAT_AgentTraces (the UI's "AI
Log"). This is the code-side half of AR-6 (AgentCore Observability): a small,
dependency-free span writer that every service path can call.

  new_correlation_id()                      -> a fresh request correlation id
  write_trace(tool, ...) -> trace_id        -> append one PLAT_AgentTraces row

Design rules:
  * Tracing must NEVER break the request. Every write is best-effort; a failure
    is swallowed (the caller gets a trace_id back regardless).
  * Rows match the seeded PLAT_AgentTraces shape (trace_id PK, agent_id, tool,
    ts, latency_ms, outcome, correlation_id, journey_id, autonomy_level,
    citations, routing_decision, source_ref) so the AI-Log read model and the
    lake see one consistent schema.
  * No PII in traces (spec §8): pass ids and decisions, not customer contact
    fields or raw model output.
"""
from __future__ import annotations

import datetime as _dt
import os
import time
import uuid
from contextvars import ContextVar
from typing import Any

from . import ddb

_TRACES = "PLAT_AgentTraces"

# The correlation id of the request/event currently being handled. Set by the
# API entrypoint (and job/subscriber entrypoints); read by the event envelope so
# one id spans API → agents → bus events (§8, acceptance #18).
CURRENT_CORRELATION_ID: ContextVar[str | None] = ContextVar("retail_ops_correlation_id", default=None)

# How the current request reached this process, e.g. "agentcore-runtime" (set by
# the AgentCore runtime dispatcher for every request it serves, ADR 0009). When
# set, write_trace tags each row with it; an explicit extra["transport"] wins.
CURRENT_TRANSPORT: ContextVar[str | None] = ContextVar("retail_ops_transport", default=None)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_correlation_id() -> str:
    """A fresh correlation id for one inbound request/event (threads across the
    agents + tools it fans out to)."""
    return f"corr-{uuid.uuid4().hex[:16]}"


def _market_tag(market: str | None) -> str:
    return (market or "us").upper()


def write_trace(tool: str, *, agent_id: str = "A2", correlation_id: str | None = None,
                journey_id: str | None = None, journey_kind: str | None = None,
                outcome: str = "ok", latency_ms: int | None = None,
                autonomy_level: str | None = None, citations: list[str] | None = None,
                routing_decision: str | None = None, rule_id: str | None = None,
                market: str | None = None, extra: dict[str, Any] | None = None,
                env_prefix: str | None = None) -> str:
    """Append one PLAT_AgentTraces row and return its trace_id.

    Best-effort: any write failure is swallowed so tracing can never break the
    traced operation. `correlation_id` ties this span to the rest of the request;
    if omitted a standalone one is generated."""
    corr = correlation_id or new_correlation_id()
    market = market or "us"
    trace_id = f"TR-{_market_tag(market)}-{uuid.uuid4().hex[:12]}"
    row: dict[str, Any] = {
        "trace_id": trace_id,
        "correlation_id": corr,
        "agent_id": agent_id,
        "tool": tool,
        "ts": _now(),
        "latency_ms": latency_ms,
        "outcome": outcome,
        "autonomy_level": autonomy_level,
        "journey_id": journey_id,
        "journey_kind": journey_kind,
        "citations": citations or [],
        "routing_decision": routing_decision,
        "rule_id": rule_id,
        "market_id": market,
        "source_ref": "runtime:write_trace (AR-6 correlation span)",
    }
    transport = CURRENT_TRANSPORT.get()
    if transport:
        row.setdefault("transport", transport)
    if extra:
        row.update(extra)
    try:
        ddb.put_item(_TRACES, row, env_prefix)
    except Exception:
        # Tracing is observability, not correctness — never propagate.
        pass
    return trace_id


class Span:
    """Context manager that times an operation and writes one trace on exit,
    recording outcome=error (not the exception text — no PII/secret leakage)
    when the block raises. Re-raises the original exception.

        with Span("build_estimate", agent_id="A7", correlation_id=corr,
                  journey_id=sj_id) as sp:
            ...work...
            sp.citations = [...]
    """

    def __init__(self, tool: str, **kw: Any):
        self.tool = tool
        self.kw = kw
        self.citations: list[str] | None = kw.pop("citations", None)
        self._start = 0.0
        self.trace_id: str | None = None

    def __enter__(self) -> "Span":
        self._start = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        latency_ms = int((time.monotonic() - self._start) * 1000)
        outcome = "ok" if exc_type is None else "error"
        self.trace_id = write_trace(self.tool, outcome=outcome, latency_ms=latency_ms,
                                    citations=self.citations, **self.kw)
        return False  # never suppress the exception


# Tracing can be turned off with RETAIL_OPS_TRACE=0 (e.g. in tight local loops).
def enabled() -> bool:
    return os.environ.get("RETAIL_OPS_TRACE", "1") != "0"
