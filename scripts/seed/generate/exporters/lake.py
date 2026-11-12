"""S3-lake exporter (U7): analytics prefixes as NDJSON + CSV, infra-free local.

The spec's S3 lake feeds QuickSight/analytics. Locally it is materialized under
`sample-data/synthetic/<market>/lake/<prefix>/` with two serializations per
prefix so both the NDJSON (row-per-line, what Athena/QuickSight ingest) and a
flat CSV (spreadsheet-friendly) exist:

    lake/<prefix>/data.ndjson
    lake/<prefix>/data.csv

Prefixes (plan §3.10/§3.11 + the funnel/marketing lake):
    journeys/            DEALER_Journeys
    journey_events/      DEALER_JourneyEvents
    service/             DEALER_ServiceJourneys + events (Process 2 analytics)
    service_events/      DEALER_ServiceJourneyEvents
    recommendations/     DEALER_Recommendations + DEALER_Outcomes (agentic funnel)
    outcomes/            DEALER_Outcomes
    touchpoints/         DEALER_Touchpoints (A5 attribution)
    agent_traces/        PLAT_AgentTraces

Only prefixes whose source table exists in the run are written. NDJSON rows are
canonical (sorted keys) so bytes are stable across same-seed runs (NFR1). CSV
columns are the sorted union of keys across the prefix's rows; nested values are
JSON-encoded in the cell. Nothing here calls AWS — it is the infra-free lake.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from ..config import RunConfig

# prefix -> the accumulator table(s) that feed it (in order).
LAKE_PREFIXES: dict[str, tuple[str, ...]] = {
    "journeys": ("DEALER_Journeys",),
    "journey_events": ("DEALER_JourneyEvents",),
    "service": ("DEALER_ServiceJourneys",),
    "service_events": ("DEALER_ServiceJourneyEvents",),
    "recommendations": ("DEALER_Recommendations",),
    "outcomes": ("DEALER_Outcomes",),
    "touchpoints": ("DEALER_Touchpoints",),
    "agent_traces": ("PLAT_AgentTraces",),
}


def _ndjson_bytes(rows: list[dict]) -> bytes:
    # One canonical JSON object per line (sorted keys → stable bytes).
    lines = [json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows]
    return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")


def _csv_bytes(rows: list[dict]) -> bytes:
    if not rows:
        return b""
    columns = sorted({k for r in rows for k in r})
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({
            # Nested values are JSON-encoded (sorted) so the cell is deterministic.
            k: (json.dumps(r[k], ensure_ascii=False, sort_keys=True)
                if isinstance(r.get(k), (list, dict)) else r.get(k, ""))
            for k in columns
        })
    return buf.getvalue().encode("utf-8")


def export_lake(cfg: RunConfig, tables: dict[str, list[dict]]) -> list[dict]:
    """Write each present lake prefix as NDJSON + CSV. Returns artifact rows."""
    lake_dir = cfg.out_dir() / "lake"
    artifacts: list[dict] = []
    for prefix, sources in LAKE_PREFIXES.items():
        rows: list[dict] = []
        for src in sources:
            rows.extend(tables.get(src, []))
        if not rows:
            continue  # a prefix with no source rows is not materialized
        pdir = lake_dir / prefix
        pdir.mkdir(parents=True, exist_ok=True)
        for fmt, data in (("ndjson", _ndjson_bytes(rows)), ("csv", _csv_bytes(rows))):
            path = pdir / f"data.{fmt}"
            path.write_bytes(data)
            artifacts.append({
                "path": f"lake/{prefix}/data.{fmt}", "kind": "lake",
                "prefix": prefix, "format": fmt, "rows": len(rows),
                "sha256": _sha256(data),
            })
    return artifacts


def _sha256(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()
