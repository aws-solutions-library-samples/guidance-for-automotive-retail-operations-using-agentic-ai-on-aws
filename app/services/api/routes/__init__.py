"""Per-domain API route modules (spec §6), discovered automatically.

Every module in this package that defines `ROUTES` (and optionally `ERRORS`) is
registered; the main handler dispatches here after the platform routes. Add a
domain by adding a module — no edits to handler.py or the API Gateway deployer
(the `{proxy+}` resource forwards every path to the service Lambda).
"""
from __future__ import annotations

import importlib
import pkgutil
from functools import lru_cache
from typing import Any

from ..routing import Respond, Route, dispatch as _dispatch


@lru_cache(maxsize=1)
def _registry() -> tuple[tuple[Route, ...], tuple[tuple[type, int], ...]]:
    routes: list[Route] = []
    errors: list[tuple[type, int]] = []
    for mod in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        if mod.name.startswith("_"):
            continue
        m = importlib.import_module(f"{__name__}.{mod.name}")
        routes.extend(getattr(m, "ROUTES", []))
        errors.extend(getattr(m, "ERRORS", []))
    errors.append((ValueError, 400))
    return tuple(routes), tuple(errors)


def all_routes() -> tuple[Route, ...]:
    return _registry()[0]


def dispatch(event: dict[str, Any], path: str, method: str, claims: dict[str, Any],
             respond: Respond) -> dict[str, Any] | None:
    routes, errors = _registry()
    return _dispatch(routes, errors, event, path, method, claims, respond)
