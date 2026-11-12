"""Connector interface (spec §4.1/§4.7) — wave 1.

One `Connector` base for every external system behind the app: `sandbox`
implementations read the committed DynamoDB mirrors (`sandbox.py`), `live`
implementations are typed stubs that fail honestly until Phase 6 wires real
endpoints (`live.py`). Wave 1 migrates A7's DMS/parts/labor lookups
(`service/connectors.py` delegates here through `get_connector()`).

Contract:
  * domain-named operations, never raw table reads at the call site:
    `lookup_dtc`, `get_labor_op`, `list_labor_ops`, `lookup_parts`;
  * every return dict carries a `source_ref` naming the connector AND the
    backing record (`connector:dms-sandbox DEALER_DTCReference:P0300`), so a
    cited figure says which boundary it crossed;
  * external ids are translated to ours at the boundary (`entity_map.py`,
    PLAT_EntityMap) — no external id leaks past a connector;
  * `health()` reports the same status vocabulary the Admin Console registry
    uses (ok | degraded | failed | not_configured).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class NotFound(Exception):
    """A referenced record does not exist in the backing system."""


class NotConfigured(RuntimeError):
    """A live connector was selected but has no endpoint/credentials configured.
    The Admin Console already degrades on this ("live connector not
    implemented"); callers surface it, never fake a pass."""


class Connector(ABC):
    """Base for one external-system connector."""

    mode: str = "sandbox"  # "sandbox" | "live"

    def __init__(self, domain: str, env_prefix: str | None = None) -> None:
        self.domain = domain
        self.env_prefix = env_prefix

    @property
    def name(self) -> str:
        return f"{self.domain}-{self.mode}"

    def source_ref(self, detail: str) -> str:
        """`connector:<domain>-<mode> <backing record>` — the boundary citation."""
        return f"connector:{self.name} {detail}"

    # --- wave-1 domain operations (A7 DMS / labor / parts lookups) -----------

    @abstractmethod
    def lookup_dtc(self, code: str, market: str | None = None) -> dict[str, Any]:
        """Diagnostic trouble code -> reference row (spec §3.7, acc #28)."""

    @abstractmethod
    def get_labor_op(self, labor_op_id: str, market: str | None = None) -> dict[str, Any]:
        """Flat-rate labor hours + shop rate for a labor op (spec §3.7)."""

    @abstractmethod
    def list_labor_ops(self, market: str = "us") -> dict[str, Any]:
        """The labor-op catalog for a market."""

    @abstractmethod
    def lookup_parts(self, part_numbers: list[str] | None = None,
                     labor_op_id: str | None = None, ro_id: str | None = None,
                     market: str | None = None) -> dict[str, Any]:
        """Parts availability + price by part numbers, labor op, or RO."""

    @abstractmethod
    def health(self) -> dict[str, Any]:
        """{status, detail, tables} with the Admin registry's status vocabulary."""
