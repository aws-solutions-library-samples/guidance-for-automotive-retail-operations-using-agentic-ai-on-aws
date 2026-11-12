"""Integration mode — one reader for INTEGRATION_MODE, with a per-request override.

Every mode reader in `app/services/platform` (generate, memory, ranker,
knowledge, compute) asks `integration_mode()` instead of reading the
environment itself, so one switch covers every model and AWS-backed call.

  integration_mode()       -> the override when one is active, else
                              INTEGRATION_MODE (default "local"), lower-cased
  override(mode)           -> context manager that forces `mode` for the
                              current request (thread / context) only

The override lives in a ContextVar, so it is scoped to the code running inside
the `with` block: a concurrent request on another thread keeps seeing the
environment. The AgentCore runtime dispatcher wraps `kind: job` in
`override("local")`, so a batch job makes the same zero model calls in a runtime
as it does in its job Lambda (design D6d, ADR 0009).
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_OVERRIDE: ContextVar[str | None] = ContextVar("retail_ops_integration_mode", default=None)


def integration_mode() -> str:
    """The active integration mode: the override if set, else the environment."""
    forced = _OVERRIDE.get()
    if forced is not None:
        return forced
    return os.environ.get("INTEGRATION_MODE", "local").lower()


@contextmanager
def override(mode: str) -> Iterator[None]:
    """Force `mode` for the current context; the previous value comes back on
    exit, also when the block raises."""
    token = _OVERRIDE.set(mode.lower())
    try:
        yield
    finally:
        _OVERRIDE.reset(token)
