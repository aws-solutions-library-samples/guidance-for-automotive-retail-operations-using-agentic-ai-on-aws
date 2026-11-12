"""PLAT_EntityMap translation at the connector boundary (spec §4.7).

External ids (CRM contact id, DMS RO/customer number, CDP profile id) never
cross a connector untranslated: `translate()` maps a `(system, external_id)`
pair to the solution-minted id the app uses, and `to_external()` goes the other way
for payloads a live system will receive. Sandbox pass-through: when no row maps
the id is returned unchanged with `mapped: False`, so the sandbox mirrors —
which are already keyed by our ids — keep working without a fully populated map.
"""
from __future__ import annotations

from typing import Any

from ..common import ddb

_TABLE = "PLAT_EntityMap"


def translate(system: str, external_id: str | None,
              env_prefix: str | None = None) -> dict[str, Any]:
    """`system`'s external id -> ours. Pass-through when no row maps."""
    if not external_id:
        return {"app_id": external_id, "external_id": external_id, "system": system,
                "mapped": False, "source_ref": "PLAT_EntityMap (no id — pass-through)"}
    try:
        rows = ddb.scan_filter(_TABLE, "external_id", external_id, env_prefix)
    except Exception:  # table missing locally — behave as unmapped
        rows = []
    hit = next((r for r in rows if r.get("system") == system), None)
    if hit is None:
        return {"app_id": external_id, "external_id": external_id, "system": system,
                "mapped": False,
                "source_ref": f"PLAT_EntityMap (no row for {system}:{external_id} — pass-through)"}
    app_id = hit.get("app_id") or (hit.get("item_key") or "").split("#", 1)[-1]
    return {"app_id": app_id, "external_id": external_id, "system": system, "mapped": True,
            "entity_type": hit.get("entity_type"), "confidence": hit.get("confidence"),
            "source_ref": f"PLAT_EntityMap:{hit.get('item_key')}/{system}"}


def to_external(system: str, app_id: str | None,
                env_prefix: str | None = None) -> dict[str, Any]:
    """Our id -> `system`'s external id. Pass-through when no row maps."""
    if not app_id:
        return {"external_id": app_id, "app_id": app_id, "system": system,
                "mapped": False, "source_ref": "PLAT_EntityMap (no id — pass-through)"}
    try:
        rows = ddb.scan_filter(_TABLE, "app_id", app_id, env_prefix)
    except Exception:
        rows = []
    hit = next((r for r in rows if r.get("system") == system), None)
    if hit is None:
        return {"external_id": app_id, "app_id": app_id, "system": system, "mapped": False,
                "source_ref": f"PLAT_EntityMap (no row for {system}:{app_id} — pass-through)"}
    return {"external_id": hit.get("external_id"), "app_id": app_id, "system": system,
            "mapped": True, "entity_type": hit.get("entity_type"),
            "confidence": hit.get("confidence"),
            "source_ref": f"PLAT_EntityMap:{hit.get('item_key')}/{system}"}
