"""Knowledge/grounding interface — local index | Bedrock Knowledge Base (AR-1).

The spec grounds A3/A6/A7 answers via `retrieve_context` over an indexed corpus.
This interface — `retrieve(query, collection, k)` -> passages with source refs —
has two implementations selected by `get_knowledge_base()`:

  * LocalKnowledgeBase    keyword overlap over the DynamoDB knowledge collections
                          and reference tables. Offline, deterministic, cited.
  * BedrockKnowledgeBase  Amazon Bedrock Knowledge Bases `Retrieve` over the
                          538-document corpus in S3 (app/infra/deploy_knowledge_base.py;
                          Titan v2 embeddings, S3 Vectors store). Selected when
                          INTEGRATION_MODE=aws and RETAIL_OPS_KNOWLEDGE_BASE_ID (or the
                          deployer's config file) is present. Any failure falls
                          back to the local index so an answer is always possible,
                          and the result says which engine ran.

`collection` filters the corpus kind (crm_notes, bulletins, product_guides,
repair_procedures, tsbs, transcripts, objection_strategies, prior_plans,
templates) through the metadata sidecars the deployer writes. Every passage
carries a `source_ref`: the document's own front-matter line when the chunk
contains it, otherwise the S3 URI.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from ..common import ddb, envname
from .mode import integration_mode

_KB_COLLECTIONS = "PLAT_KnowledgeCollections"


def _mode() -> str:
    return integration_mode()


def _quick_config() -> dict:
    """The provisioned AWS Quick (Q Business) app config, if deploy_quick ran.
    Env vars override the file so the Lambda can carry them."""
    import json
    from pathlib import Path
    app_id = os.environ.get("RETAIL_OPS_QBUSINESS_INSTANCE_ID")
    retr_id = os.environ.get("RETAIL_OPS_QBUSINESS_RETRIEVER_ID")
    if app_id:
        return {"application_id": app_id, "retriever_id": retr_id}
    cfg_path = envname.config_file(Path(__file__).resolve().parents[3] / "app" / "infra", "quick")
    if cfg_path.exists():
        try:
            return json.loads(cfg_path.read_text())
        except Exception:
            return {}
    return {}


class KnowledgeBase:
    def retrieve(self, query: str, collection: str | None = None, k: int = 3,
                 env_prefix: str | None = None) -> dict[str, Any]:
        raise NotImplementedError


class LocalKnowledgeBase(KnowledgeBase):
    """Keyword-overlap retrieval over PLAT_KnowledgeCollections rows plus a few
    grounding tables (DTC references, objection strategies). Not embeddings, but
    it returns real, cited passages — the reference grounding for dev/sandbox."""

    _SOURCES = [
        (_KB_COLLECTIONS, ("title", "text", "content", "abstract", "name")),
        ("DEALER_DTCReference", ("description", "system", "dtc")),
        ("DEALER_ObjectionStrategies", ("name", "strategy", "response", "objection")),
    ]

    def _tokens(self, s: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", (s or "").lower()))

    def retrieve(self, query: str, collection: str | None = None, k: int = 3,
                 env_prefix: str | None = None) -> dict[str, Any]:
        q = self._tokens(query)
        scored: list[tuple[float, dict[str, Any]]] = []
        for table, fields in self._SOURCES:
            try:
                rows = ddb.scan_all(table, env_prefix, limit=300)
            except Exception:
                continue
            for r in rows:
                text = " ".join(str(r.get(f, "")) for f in fields if r.get(f))
                if not text:
                    continue
                overlap = len(q & self._tokens(text))
                if overlap:
                    key = r.get("collection_id") or r.get("dtc") or r.get("objection_strategy_id") or table
                    scored.append((overlap, {"passage": text[:400], "source_ref": f"{table}:{key}",
                                             "collection": table}))
        scored.sort(key=lambda x: x[0], reverse=True)
        passages = [p for _, p in scored[:k]]
        return {"query": query, "passages": passages, "count": len(passages),
                "engine": "local_keyword",
                "source_ref": "platform.LocalKnowledgeBase (keyword; Bedrock KB swap-in)"}


_KB_CFG = envname.config_file(Path(__file__).resolve().parents[2] / "infra", "knowledge-base")
_KINDS = ("crm_notes", "templates", "transcripts", "objection_strategies", "bulletins",
          "prior_plans", "product_guides", "repair_procedures", "tsbs")
# callers may pass a table name (local vocabulary) — map it to a corpus kind
_TABLE_TO_KIND = {"DEALER_ObjectionStrategies": "objection_strategies",
                  "DEALER_DTCReference": "repair_procedures", "PLAT_KnowledgeCollections": None}
# chunks may have their newlines collapsed by ingestion, so do not anchor on line starts
_SOURCE_REF_RE = re.compile(r"source_ref:\s*(.+?)(?=\s+(?:collection_id|market):|\n|$)")
_META_KEYS = ("source_ref", "collection_id", "market", "kind", "doc_id")
_META_HEAD_RE = re.compile(r"^#+\s*[^\n]*?(?=(?:%s):|\n|$)" % "|".join(_META_KEYS))
# a value runs to the next meta key or the end of its line; in a chunk whose
# newlines were collapsed, a trailing value is a single token (`market: de`)
_META_PAIR_RE = re.compile(
    r"^(?:%(k)s):\s*(?:[^\n]*?(?=\s+(?:%(k)s):)|[^\n]*?\n|\S+)[ \t]*"
    % {"k": "|".join(_META_KEYS)})


def strip_chunk_metadata(text: str) -> str:
    """Drop a chunk's leading heading and `key: value` front matter so the
    passage reads as prose. Works whether ingestion kept or collapsed newlines."""
    t = (text or "").strip()
    m = _META_HEAD_RE.match(t)
    if m:
        t = t[m.end():].lstrip()
    while True:
        m = _META_PAIR_RE.match(t)
        if not m:
            return t.strip() or (text or "").strip()
        t = t[m.end():].lstrip()


def knowledge_base_id() -> str | None:
    kb = os.environ.get("RETAIL_OPS_KNOWLEDGE_BASE_ID")
    if kb:
        return kb
    if _KB_CFG.exists():
        try:
            return json.loads(_KB_CFG.read_text()).get("knowledge_base_id")
        except Exception:
            return None
    return None


class BedrockKnowledgeBase(KnowledgeBase):
    """Amazon Bedrock Knowledge Bases Retrieve over the S3 corpus."""

    def __init__(self, kb_id: str, region: str | None = None):
        self.kb_id = kb_id
        self.region = region or os.environ.get("AWS_REGION", "us-east-1")

    @staticmethod
    def _kind(collection: str | None) -> str | None:
        if not collection:
            return None
        if collection in _TABLE_TO_KIND:
            return _TABLE_TO_KIND[collection]
        c = collection.lower().replace("-", "_").replace(" ", "_")
        # Quick knowledge-base display names carry a product prefix
        # ("Retail Ops CRM Notes"; older builds "Retail Ops CRM Notes") — strip it.
        for prefix in ("retail_ops_", "retail_ops_"):
            if c.startswith(prefix):
                c = c[len(prefix):]
                break
        return c if c in _KINDS else None

    @staticmethod
    def _passage(r: dict[str, Any]) -> dict[str, Any]:
        text = ((r.get("content") or {}).get("text") or "").strip()
        uri = ((r.get("location") or {}).get("s3Location") or {}).get("uri") or ""
        meta = r.get("metadata") or {}
        m = _SOURCE_REF_RE.search(text)
        source_ref = m.group(1).strip() if m else (uri or "bedrock-kb")
        body = strip_chunk_metadata(text)
        return {"passage": body[:600], "source_ref": source_ref, "uri": uri,
                "collection": meta.get("kind") or meta.get("collection_id") or "corpus",
                "score": round(float(r.get("score") or 0), 4)}

    def retrieve(self, query: str, collection: str | None = None, k: int = 3,
                 env_prefix: str | None = None, market: str | None = None) -> dict[str, Any]:
        import boto3
        rt = boto3.client("bedrock-agent-runtime", region_name=self.region)
        vs: dict[str, Any] = {"numberOfResults": max(1, min(int(k), 25))}
        kind = self._kind(collection)
        market = (market or os.environ.get("RETAIL_OPS_DEFAULT_MARKET") or "").lower() or None
        clauses = []
        if kind:
            clauses.append({"equals": {"key": "kind", "value": kind}})
        if market:
            clauses.append({"equals": {"key": "market", "value": market}})
        if len(clauses) == 1:
            vs["filter"] = clauses[0]
        elif clauses:
            vs["filter"] = {"andAll": clauses}
        out = rt.retrieve(knowledgeBaseId=self.kb_id, retrievalQuery={"text": query[:1000]},
                          retrievalConfiguration={"vectorSearchConfiguration": vs})
        passages = [self._passage(r) for r in out.get("retrievalResults", [])]
        return {"query": query, "passages": passages, "count": len(passages),
                "engine": "bedrock_kb", "knowledge_base_id": self.kb_id, "kind": kind, "market": market,
                "source_ref": "platform.BedrockKnowledgeBase (Amazon Bedrock Knowledge Bases Retrieve)"}


class _FallbackKnowledgeBase(KnowledgeBase):
    """Bedrock first; local keyword index if the managed call fails for any reason."""

    def __init__(self, primary: BedrockKnowledgeBase):
        self.primary = primary
        self.local = LocalKnowledgeBase()

    def retrieve(self, query: str, collection: str | None = None, k: int = 3,
                 env_prefix: str | None = None, market: str | None = None) -> dict[str, Any]:
        try:
            out = self.primary.retrieve(query, collection, k, env_prefix, market=market)
            if out["count"]:
                return out
            local = self.local.retrieve(query, collection, k, env_prefix)
            local["engine"] = "local_keyword (bedrock_kb returned nothing)"
            return local
        except Exception as e:
            local = self.local.retrieve(query, collection, k, env_prefix)
            local["engine"] = f"local_keyword (bedrock_kb unavailable: {type(e).__name__})"
            return local


def get_knowledge_base() -> KnowledgeBase:
    kb_id = knowledge_base_id()
    if _mode() == "aws" and kb_id:
        return _FallbackKnowledgeBase(BedrockKnowledgeBase(kb_id))
    return LocalKnowledgeBase()
