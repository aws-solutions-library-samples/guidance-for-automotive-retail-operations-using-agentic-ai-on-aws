"""Sandbox connectors — DynamoDB-backed reference implementations.

The lookup logic MOVED here from `service/connectors.py` (which now delegates
through `get_connector()`): market-aware composite-key reference reads for DTC
and labor ops, and the parts catalog lookups. The backing tables are the
committed DMS/reference mirrors, so nothing is fabricated (acceptance #27/#28).
"""
from __future__ import annotations

from typing import Any

from ..common import ddb
from . import entity_map
from .base import Connector, NotFound

_DTC = "DEALER_DTCReference"
_LABOR = "DEALER_LaborOps"
_PARTS = "DEALER_Parts"

# Backing tables per wave-1 domain (health checks + the Admin registry view).
BACKING_TABLES: dict[str, list[str]] = {
    "dms": [_DTC],
    "labor": [_LABOR],
    "parts": [_PARTS],
}


class SandboxConnector(Connector):
    """DynamoDB-backed implementation of every wave-1 operation. One class
    serves all three domains — the domain picks the registry entry, mode and
    source_ref naming, not the storage."""

    mode = "sandbox"

    def _market_ref(self, table: str, sort_attr: str, sort_value: str,
                    market: str | None) -> dict[str, Any] | None:
        """Read a market-scoped reference row (PK=market_id, SK=<sort_attr>). When
        the caller has no market, fall back to any market's row so a lookup still
        answers — the row carries its own market_id for the caller to see."""
        if market:
            hit = ddb.get_item(table, {"market_id": market, sort_attr: sort_value},
                               self.env_prefix)
            if hit:
                return hit
        rows = ddb.scan_filter(table, sort_attr, sort_value, self.env_prefix)
        return rows[0] if rows else None

    def lookup_dtc(self, code: str, market: str | None = None) -> dict[str, Any]:
        row = self._market_ref(_DTC, "dtc", code, market)
        if row is None:
            return {"dtc": code, "found": False,
                    "note": "not in DEALER_DTCReference — a finding may not cite an unknown DTC",
                    "source_ref": self.source_ref(f"{_DTC} (no match)")}
        return {"dtc": code, "found": True, "system": row.get("system"),
                "description": row.get("description"),
                "source_ref": self.source_ref(f"{_DTC}:{code}")}

    def get_labor_op(self, labor_op_id: str, market: str | None = None) -> dict[str, Any]:
        row = self._market_ref(_LABOR, "labor_op_id", labor_op_id, market)
        if row is None:
            raise NotFound(f"labor op {labor_op_id} not found")
        hours = row.get("flat_rate_hours")
        rate = row.get("shop_rate")
        try:
            labor_total = round(float(hours) * float(rate), 2) if hours is not None and rate is not None else None
        except (TypeError, ValueError):
            labor_total = None
        return {"labor_op_id": labor_op_id, "name": row.get("name"),
                "flat_rate_hours": hours, "shop_rate": rate, "labor_total": labor_total,
                "currency": row.get("currency", "USD"), "market_id": row.get("market_id"),
                "source_ref": self.source_ref(f"{_LABOR}:{labor_op_id}")}

    def list_labor_ops(self, market: str = "us") -> dict[str, Any]:
        rows = [o for o in ddb.scan_all(_LABOR, self.env_prefix)
                if (o.get("market_id") or market) == market]
        return {"market_id": market, "count": len(rows), "labor_ops": rows,
                "source_ref": self.source_ref(_LABOR)}

    def lookup_parts(self, part_numbers: list[str] | None = None,
                     labor_op_id: str | None = None, ro_id: str | None = None,
                     market: str | None = None) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        translated = None
        if ro_id:
            # Boundary translation (spec §4.7): a DMS RO number maps to our ro_id
            # via PLAT_EntityMap; an already-ours id passes through unchanged.
            translated = entity_map.translate("dms", ro_id, self.env_prefix)
            rows = ddb.scan_filter(_PARTS, "ro_id", translated["app_id"], self.env_prefix)
        elif part_numbers:
            allp = ddb.scan_all(_PARTS, self.env_prefix)
            want = set(part_numbers)
            rows = [p for p in allp if p.get("part_number") in want]
        elif labor_op_id:
            # The parts a labor op calls for are the rows tagged with that op (labor_op_id).
            # An op with none (wheel alignment, a diagnostic scan) needs no parts: return none,
            # never an unrelated slice of the catalog (alignment listed wipers and cabin filters).
            seen, rows = set(), []
            for p in ddb.scan_filter(_PARTS, "labor_op_id", labor_op_id, self.env_prefix):
                if p.get("description") not in seen:      # one line per part kind, not per RO
                    seen.add(p.get("description"))
                    rows.append(p)
        else:
            rows = ddb.scan_all(_PARTS, self.env_prefix, limit=200)
        parts = [{"part_id": p.get("part_id"), "part_number": p.get("part_number"),
                  "description": p.get("description"), "qty": p.get("qty"),
                  "unit_price": p.get("unit_price"), "on_hand": bool(p.get("on_hand")),
                  "currency": p.get("currency", "USD")} for p in rows]
        out = {"count": len(parts), "parts": parts,
               "all_on_hand": all(p["on_hand"] for p in parts) if parts else True,
               "source_ref": self.source_ref(_PARTS)}
        if translated and translated["mapped"]:
            out["ro_id"] = translated["app_id"]
            out["entity_map"] = translated["source_ref"]
        return out

    def health(self) -> dict[str, Any]:
        """Same status vocabulary + table probe as the Admin registry test path."""
        tables = BACKING_TABLES.get(self.domain, [])
        if not tables:
            return {"status": "not_configured", "detail": "no sandbox backing for this connector",
                    "tables": {}, "connector": self.name}
        rows: dict[str, Any] = {}
        for t in tables:
            try:
                rows[t] = len(ddb.scan_all(t, self.env_prefix, limit=1))
            except Exception as e:  # missing table / access denied
                rows[t] = f"error: {type(e).__name__}"
        errors = [t for t, v in rows.items() if not isinstance(v, int)]
        empty = [t for t, v in rows.items() if v == 0]
        status = "failed" if errors else "degraded" if empty else "ok"
        detail = (f"unreachable: {errors}" if errors else f"empty: {empty}" if empty
                  else "sandbox tables reachable")
        return {"status": status, "detail": detail, "tables": rows, "connector": self.name}
