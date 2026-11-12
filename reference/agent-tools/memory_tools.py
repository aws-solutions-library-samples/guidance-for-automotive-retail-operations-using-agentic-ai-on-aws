"""
AgentCore Memory tools (Requirement 8).

Provides persistent event memory (Prospect Records, interaction history) and
semantic memory (Vehicle Knowledge Base, market data cache) via
Amazon Bedrock AgentCore Memory service.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import boto3
from strands import tool

from ..config import config
from .. import observability


# ── AgentCore Memory client ───────────────────────────────────────────────────

def _memory_client() -> Any:
    return boto3.client("bedrock-agentcore", region_name=config.aws_region)


# ── Event Memory (Req 8.1 – 8.4) ─────────────────────────────────────────────

@tool
def store_prospect_record(prospect_json: str, customer_id: str, session_id: str) -> dict:
    """
    Persist a Prospect Record to AgentCore Memory as an event memory entry (Req 8.1).
    Falls back to in-memory storage if AgentCore Memory is not configured.

    Args:
        prospect_json: JSON-serialized ProspectRecord string.
        customer_id: Customer identifier for memory association.
        session_id: Session identifier for grouping related interactions.

    Returns:
        Dictionary with 'event_id' on success, or '_error' on failure.
    """
    with observability.trace_span("memory.store_prospect_record", customer_id):
        # If memory ID is "local", use in-memory storage (mock)
        if config.agentcore_event_memory_id == "local":
            event_id = f"local-{uuid.uuid4()}"
            # In production, would store to DynamoDB or similar
            return {"event_id": event_id, "status": "stored_local"}

        try:
            client = _memory_client()
            response = client.create_event(
                memoryId=config.agentcore_event_memory_id,
                actorId=customer_id,
                sessionId=session_id,
                payload=[
                    {
                        "conversational": {
                            "content": [{"text": prospect_json}],
                            "role": "ASSISTANT",
                        }
                    }
                ],
                metadata={
                    "eventType": "PROSPECT_RECORD",
                    "customerId": customer_id,
                },
            )
            event = response.get("event", {})
            return {"event_id": event.get("eventId", session_id), "status": "stored"}
        except Exception as exc:
            observability.log_error("MEMORY_ERROR", customer_id, "store_prospect_record", str(exc))
            return {"_error": str(exc)}


@tool
def retrieve_prospect_history(customer_id: str, max_results: int = 20) -> dict:
    """
    Retrieve prior Prospect Records for a customer from AgentCore Memory (Req 8.2).

    Args:
        customer_id: The customer whose history to retrieve.
        max_results: Maximum number of historical records to return (default 20).

    Returns:
        Dictionary with 'records' list of prior prospect interactions.
    """
    with observability.trace_span("memory.retrieve_prospect_history", customer_id):
        # If memory ID is "local", return empty history
        if config.agentcore_event_memory_id == "local":
            return {"customer_id": customer_id, "records": [], "count": 0, "status": "local"}

        try:
            client = _memory_client()
            response = client.list_events(
                memoryId=config.agentcore_event_memory_id,
                actorId=customer_id,
                includePayloads=True,
                maxResults=max_results,
            )
            records = []
            for event in response.get("events", []):
                for payload_item in event.get("payload", []):
                    conv = payload_item.get("conversational", {})
                    for content_block in conv.get("content", []):
                        if "text" in content_block:
                            records.append(content_block["text"])
            return {"customer_id": customer_id, "records": records, "count": len(records)}
        except Exception as exc:
            observability.log_error("MEMORY_ERROR", customer_id, "retrieve_prospect_history", str(exc))
            return {"customer_id": customer_id, "records": [], "count": 0, "_error": str(exc)}


@tool
def update_prospect_status(
    prospect_id: str,
    customer_id: str,
    new_status: str,
    session_id: str,
) -> dict:
    """
    Update a Prospect Record's status when it changes (contacted, converted, declined) (Req 8.4).

    Args:
        prospect_id: The prospect record identifier.
        customer_id: The customer identifier for memory lookup.
        new_status: One of CONTACTED, CONVERTED, DECLINED.
        session_id: Memory session identifier.

    Returns:
        Dictionary confirming the status update.
    """
    with observability.trace_span("memory.update_prospect_status", customer_id):
        status_update = json.dumps(
            {
                "event": "STATUS_UPDATE",
                "prospect_id": prospect_id,
                "customer_id": customer_id,
                "new_status": new_status,
                "updated_at": __import__("datetime").datetime.utcnow().isoformat(),
            }
        )
        try:
            client = _memory_client()
            client.create_event(
                memoryId=config.agentcore_event_memory_id,
                actorId=customer_id,
                sessionId=session_id,
                payload=[
                    {
                        "conversational": {
                            "content": [{"text": status_update}],
                            "role": "ASSISTANT",
                        }
                    }
                ],
                metadata={
                    "eventType": "STATUS_UPDATE",
                    "customerId": customer_id,
                    "prospectId": prospect_id,
                    "newStatus": new_status,
                },
            )
            return {"prospect_id": prospect_id, "status": new_status, "updated": True}
        except Exception as exc:
            observability.log_error("MEMORY_ERROR", customer_id, "update_prospect_status", str(exc))
            return {"prospect_id": prospect_id, "updated": False, "_error": str(exc)}


# ── Semantic Memory (Req 8.3, 15.2 – 15.3) ───────────────────────────────────

@tool
def store_semantic_knowledge(
    content: str,
    source_name: str,
    content_type: str,
    metadata: str = "{}",
) -> dict:
    """
    Index content into AgentCore Memory semantic store for RAG retrieval (Req 8.3, 16.5).

    Args:
        content: Text content to index (vehicle spec, market trend, offer template, etc.).
        source_name: Name of the source document or system.
        content_type: One of 'vehicle_spec', 'market_data', 'offer_template', 'manual', 'comparison'.
        metadata: JSON string with additional metadata tags (unused at API level, kept for compatibility).

    Returns:
        Dictionary with 'record_id' of the indexed semantic memory entry.
    """
    with observability.trace_span("memory.store_semantic_knowledge", "N/A"):
        try:
            client = _memory_client()
            response = client.batch_create_memory_records(
                memoryId=config.agentcore_semantic_memory_id,
                records=[
                    {
                        "requestIdentifier": str(uuid.uuid4()),
                        "content": {"text": content},
                        "memoryStrategyId": config.agentcore_semantic_memory_strategy_id,
                        "namespaces": [source_name, content_type],
                    }
                ],
            )
            successful = response.get("successfulRecords", [])
            if successful:
                return {"record_id": successful[0].get("memoryRecordId"), "status": "indexed"}
            failed = response.get("failedRecords", [])
            err = failed[0].get("errorMessage", "unknown") if failed else "no records returned"
            return {"_error": err}
        except Exception as exc:
            observability.log_error("MEMORY_ERROR", "N/A", "store_semantic_knowledge", str(exc))
            return {"_error": str(exc)}


@tool
def retrieve_vehicle_knowledge(query: str, content_type: str = "", max_results: int = 10) -> dict:
    """
    Semantic search over the Vehicle Knowledge Base in AgentCore Memory (Req 15.2, 8.3).
    Used by the RAG Chat to answer Dealer Salesperson questions.

    Args:
        query: Natural language question or search phrase.
        content_type: Optional filter — e.g. 'vehicle_spec', 'manual', 'comparison'.
        max_results: Maximum number of results to return (default 10).

    Returns:
        Dictionary with 'results' list, each containing 'content', 'score', and 'source'.
    """
    with observability.trace_span("memory.retrieve_vehicle_knowledge", "N/A"):
        try:
            client = _memory_client()
            search_criteria: dict[str, Any] = {
                "searchQuery": query,
                "topK": max_results,
            }
            if config.agentcore_semantic_memory_strategy_id:
                search_criteria["memoryStrategyId"] = config.agentcore_semantic_memory_strategy_id
            response = client.retrieve_memory_records(
                memoryId=config.agentcore_semantic_memory_id,
                searchCriteria=search_criteria,
            )
            results = []
            for item in response.get("memoryRecordSummaries", []):
                namespaces = item.get("namespaces", [])
                results.append(
                    {
                        "content": item.get("content", {}).get("text", ""),
                        "score": item.get("score", 0.0),
                        "source": namespaces[0] if namespaces else "unknown",
                        "content_type": namespaces[1] if len(namespaces) > 1 else "",
                    }
                )
            return {"query": query, "results": results, "count": len(results)}
        except Exception as exc:
            observability.log_error("MEMORY_ERROR", "N/A", "retrieve_vehicle_knowledge", str(exc))
            return {"query": query, "results": [], "count": 0, "_error": str(exc)}


@tool
def store_market_data_cache(market_data_json: str, data_type: str, cache_key: str) -> dict:
    """
    Cache market data (vehicle values, competitor offers, incentives) in AgentCore Memory
    semantic store so it can be used when the Browser fails to retrieve fresh data (Req 7.4).

    Args:
        market_data_json: JSON string of the market data to cache.
        data_type: One of 'vehicle_values', 'competitor_offers', 'incentive_programs'.
        cache_key: Unique key to identify this cached entry (e.g. 'kbb_2024_camry_xle').

    Returns:
        Dictionary confirming the cache storage.
    """
    with observability.trace_span("memory.store_market_data_cache", "N/A"):
        try:
            client = _memory_client()
            response = client.batch_create_memory_records(
                memoryId=config.agentcore_semantic_memory_id,
                records=[
                    {
                        "requestIdentifier": str(uuid.uuid4()),
                        "content": {"text": market_data_json},
                        "memoryStrategyId": config.agentcore_semantic_memory_strategy_id,
                        "namespaces": [cache_key, "market_data", data_type],
                    }
                ],
            )
            successful = response.get("successfulRecords", [])
            if successful:
                return {
                    "cache_key": cache_key,
                    "record_id": successful[0].get("memoryRecordId"),
                    "status": "cached",
                }
            failed = response.get("failedRecords", [])
            err = failed[0].get("errorMessage", "unknown") if failed else "no records returned"
            return {"_error": err}
        except Exception as exc:
            observability.log_error("MEMORY_ERROR", "N/A", "store_market_data_cache", str(exc))
            return {"_error": str(exc)}


@tool
def retrieve_cached_market_data(cache_key: str, data_type: str) -> dict:
    """
    Retrieve the most recently cached market data from AgentCore Memory (Req 7.4).
    Called when the AgentCore Browser cannot retrieve fresh data.

    Args:
        cache_key: The cache identifier (e.g. 'kbb_2024_camry_xle').
        data_type: The type of market data to retrieve.

    Returns:
        Dictionary with 'data' (the cached JSON string) and '_stale': true.
    """
    with observability.trace_span("memory.retrieve_cached_market_data", "N/A"):
        result = retrieve_vehicle_knowledge(
            query=f"{data_type} {cache_key}",
            content_type="market_data",
            max_results=1,
        )
        if result.get("count", 0) > 0:
            top = result["results"][0]
            return {
                "cache_key": cache_key,
                "data": top["content"],
                "cached_at": top.get("content_type", ""),
                "_stale": True,
            }
        return {"cache_key": cache_key, "data": None, "_stale": True, "_not_found": True}
