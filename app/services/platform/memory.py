"""Agent memory — sourced customer context with AgentCore enrichment.

One interface every agent uses to persist and recall durable customer facts
(preferences stated, objections raised, promises made) across journeys and
sessions, so an advisor-facing answer can say "last time you told us…" with a
source behind it:

  remember(customer_id, kind, text, *, source_ref, actor)  -> store one memory
  recall(customer_id, k=8)        -> newest-first [{kind, text, at, source_ref}]

kinds: "preference" | "objection" | "promise" | "fact".

Storage is layered the same way the knowledge base is (D10):
  * The system of record is a DynamoDB table `PLAT_AgentMemory` (pk memory_id =
    "<customer_id>#<ts>#<kind>") — deterministic, offline-friendly, and the only
    place `source_ref`/actor provenance is guaranteed. deploy_memory.py ensures
    the table exists in AWS accounts; locally the FakeDynamoDB serves it.
  * In aws mode with a provisioned AgentCore Memory (RETAIL_OPS_MEMORY_ID env or
    app/infra/memory-config.json, written by deploy_memory.py), each remember()
    ALSO lands a `create_event` on the bedrock-agentcore data plane
    (actor_id = customer_id, session derived from the source_ref so one
    journey's writes share a session), and recall() merges
    `retrieve_memory_records` results in. Every AgentCore call is wrapped
    per-call: an API-shape or access failure degrades to the DynamoDB rows,
    never breaks the caller.

Like tracing, memory is enrichment, not correctness: remember() never raises
(it reports {stored, engine}), and recall() returns [] on total failure. Every
record carries source_ref; recall de-duplicates by (kind, text), newest first.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from ..common import ddb, envname
from .mode import integration_mode

_TABLE = "PLAT_AgentMemory"
_CFG = envname.config_file(Path(__file__).resolve().parents[2] / "infra", "memory")

KINDS = ("preference", "objection", "promise", "fact")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def memory_id() -> str | None:
    """The AgentCore Memory id from env (RETAIL_OPS_MEMORY_ID, set by the deployers)
    or the local config file; None when no memory resource is provisioned."""
    mid = os.environ.get("RETAIL_OPS_MEMORY_ID")
    if not mid and _CFG.exists():
        try:
            mid = json.loads(_CFG.read_text()).get("memory_id")
        except Exception:
            mid = None
    return mid or None


# AgentCore Memory runs only in aws mode (same gate as generation): the local
# sandbox stays on the DynamoDB table so tests never depend on a network call.
def enabled() -> bool:
    return integration_mode() == "aws" and memory_id() is not None


def _session_for(source_ref: str | None) -> str:
    """One AgentCore session per originating context (journey, run, template):
    writes born from the same source_ref share a session."""
    return "s-" + hashlib.sha1((source_ref or "unscoped").encode()).hexdigest()[:16]


def _agentcore_write(customer_id: str, kind: str, text: str, source_ref: str,
                     actor: str) -> bool:
    """Best-effort create_event on the bedrock-agentcore data plane."""
    try:
        import boto3
        rt = boto3.client("bedrock-agentcore", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        rt.create_event(
            memoryId=memory_id(),
            actorId=customer_id,
            sessionId=_session_for(source_ref),
            eventTimestamp=_dt.datetime.now(_dt.timezone.utc),
            payload=[{"conversational": {
                "content": {"text": f"[{kind}] {text} (by {actor}; source: {source_ref})"},
                "role": "ASSISTANT"}}],
        )
        return True
    except Exception:
        return False  # degrade to the DynamoDB row (already written)


def _agentcore_recall(customer_id: str, k: int) -> list[dict[str, Any]]:
    """Best-effort retrieve_memory_records against the long-term store. The
    extracted records carry no source_ref of their own, so they are tagged with
    the memory-record id; the DynamoDB rows win the (kind, text) de-dup."""
    out: list[dict[str, Any]] = []
    try:
        import boto3
        rt = boto3.client("bedrock-agentcore", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        resp = rt.retrieve_memory_records(
            memoryId=memory_id(),
            namespace=f"/preferences/{customer_id}",
            searchCriteria={"searchQuery": "customer preferences, objections raised, promises made, facts",
                            "topK": k},
        )
        for rec in resp.get("memoryRecordSummaries") or resp.get("memoryRecords") or []:
            content = rec.get("content") or {}
            text = content.get("text") if isinstance(content, dict) else str(content)
            if not text:
                continue
            out.append({"kind": "fact", "text": str(text)[:400],
                        "at": str(rec.get("createdAt") or ""),
                        "source_ref": f"bedrock-agentcore:memory_record:{rec.get('memoryRecordId')}"})
    except Exception:
        return []  # degrade to the DynamoDB rows
    return out


def remember(customer_id: str, kind: str, text: str, *, source_ref: str, actor: str,
             env_prefix: str | None = None) -> dict[str, Any]:
    """Store one customer memory. Never raises — memory is enrichment, and a
    failed write must not break the action that produced the signal."""
    if kind not in KINDS:
        return {"stored": False, "error": f"kind must be one of {KINDS}"}
    if not (customer_id and (text or "").strip()):
        return {"stored": False, "error": "customer_id and text are required"}
    ts = _now()
    row = {
        "memory_id": f"{customer_id}#{ts}#{kind}",
        "customer_id": customer_id,
        "kind": kind,
        "text": text.strip()[:400],
        "at": ts,
        "source_ref": source_ref,
        "actor": actor,
    }
    try:
        ddb.put_item(_TABLE, row, env_prefix)
        stored = True
    except Exception as e:  # table missing / offline — report, never raise
        return {"stored": False, "error": f"{type(e).__name__}", "engine": "none"}
    engine = "dynamodb"
    if enabled() and _agentcore_write(customer_id, kind, row["text"], source_ref, actor):
        engine = "dynamodb+agentcore"
    return {"stored": stored, "memory_id": row["memory_id"], "engine": engine}


def recall(customer_id: str, k: int = 8, env_prefix: str | None = None) -> list[dict[str, Any]]:
    """Newest-first memories for a customer, de-duplicated by (kind, text).
    Merges the DynamoDB rows (provenance-bearing) with any AgentCore long-term
    records; returns [] rather than raising when neither store answers."""
    if not customer_id:
        return []
    rows: list[dict[str, Any]] = []
    try:
        for r in ddb.scan_filter(_TABLE, "customer_id", customer_id, env_prefix):
            rows.append({"kind": r.get("kind") or "fact", "text": r.get("text") or "",
                         "at": r.get("at") or "", "source_ref": r.get("source_ref")})
    except Exception:
        pass  # table missing locally — AgentCore (if any) may still answer
    if enabled():
        rows.extend(_agentcore_recall(customer_id, k))
    rows.sort(key=lambda r: r.get("at") or "", reverse=True)
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for r in rows:
        key = (r["kind"], r["text"])
        if not r["text"] or key in seen:
            continue
        seen.add(key)
        out.append(r)
        if len(out) >= k:
            break
    return out
