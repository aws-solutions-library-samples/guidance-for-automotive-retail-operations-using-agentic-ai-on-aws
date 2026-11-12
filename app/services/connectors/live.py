"""Live connectors — typed boundaries until customer endpoints are configured.

A live connector reads its registry entry (Admin Console connector registry,
`admin/connectors.py`) for the credential *reference* (a Secrets Manager secret
NAME — the value is never read or stored here) and the endpoint. No live
endpoint ships in the pilot, so every operation raises `NotConfigured` — the
exact error the Admin UI already degrades on ("live connector not implemented")
— rather than faking a pass with sandbox data under a live label.
"""
from __future__ import annotations

from typing import Any

from .base import Connector, NotConfigured


class LiveConnector(Connector):
    mode = "live"

    def __init__(self, domain: str, env_prefix: str | None = None,
                 registry_entry: dict[str, Any] | None = None) -> None:
        super().__init__(domain, env_prefix)
        entry = registry_entry or {}
        self.credential_ref = entry.get("credential_ref") or ""
        self.endpoint = entry.get("endpoint") or ""

    def _not_configured(self) -> NotConfigured:
        return NotConfigured(f"live connector {self.domain!r} has no endpoint configured"
                             + (f" (credential_ref {self.credential_ref})" if self.credential_ref else ""))

    def lookup_dtc(self, code: str, market: str | None = None) -> dict[str, Any]:
        raise self._not_configured()

    def get_labor_op(self, labor_op_id: str, market: str | None = None) -> dict[str, Any]:
        raise self._not_configured()

    def list_labor_ops(self, market: str = "us") -> dict[str, Any]:
        raise self._not_configured()

    def lookup_parts(self, part_numbers: list[str] | None = None,
                     labor_op_id: str | None = None, ro_id: str | None = None,
                     market: str | None = None) -> dict[str, Any]:
        raise self._not_configured()

    def health(self) -> dict[str, Any]:
        return {"status": "not_configured",
                "detail": "live connector not implemented (Phase 6); sandbox data in use",
                "tables": {}, "connector": self.name}
