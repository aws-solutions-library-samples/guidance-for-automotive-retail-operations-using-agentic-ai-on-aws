"""
AgentCore Observability integration (Requirement 11).

Emits trace spans, structured logs, and metrics to Amazon CloudWatch and AWS X-Ray
so that every prospect identification, enrichment, scoring, and recommendation step
is fully auditable.
"""
from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Generator, Optional

import boto3
from botocore.exceptions import ClientError

from .config import config

# ── CloudWatch clients ────────────────────────────────────────────────────────
_logs_client: Optional[Any] = None
_cw_client: Optional[Any] = None
_sequence_token: Optional[str] = None


def _logs() -> Any:
    global _logs_client
    if _logs_client is None:
        _logs_client = boto3.client("logs", region_name=config.aws_region)
    return _logs_client


def _cw() -> Any:
    global _cw_client
    if _cw_client is None:
        _cw_client = boto3.client("cloudwatch", region_name=config.aws_region)
    return _cw_client


def _ensure_log_group_and_stream() -> None:
    """Create CloudWatch log group and stream if they do not exist."""
    try:
        _logs().create_log_group(logGroupName=config.cloudwatch_log_group)
    except _logs().exceptions.ResourceAlreadyExistsException:
        pass
    except ClientError:
        pass

    try:
        _logs().create_log_stream(
            logGroupName=config.cloudwatch_log_group,
            logStreamName=config.cloudwatch_log_stream,
        )
    except _logs().exceptions.ResourceAlreadyExistsException:
        pass
    except ClientError:
        pass


def _put_log(message: str) -> None:
    global _sequence_token
    try:
        _ensure_log_group_and_stream()
        kwargs: dict[str, Any] = {
            "logGroupName": config.cloudwatch_log_group,
            "logStreamName": config.cloudwatch_log_stream,
            "logEvents": [
                {"timestamp": int(time.time() * 1000), "message": message}
            ],
        }
        if _sequence_token:
            kwargs["sequenceToken"] = _sequence_token
        response = _logs().put_log_events(**kwargs)
        _sequence_token = response.get("nextSequenceToken")
    except Exception:
        # Never let observability failures crash the agent
        pass


def _put_metric(name: str, value: float, unit: str = "None") -> None:
    try:
        _cw().put_metric_data(
            Namespace=config.metrics_namespace,
            MetricData=[
                {
                    "MetricName": name,
                    "Value": value,
                    "Unit": unit,
                    "Timestamp": datetime.utcnow(),
                }
            ],
        )
    except Exception:
        pass


# ── Public API ────────────────────────────────────────────────────────────────

@contextmanager
def trace_span(
    name: str,
    prospect_id: str = "N/A",
    attributes: Optional[dict[str, Any]] = None,
) -> Generator[str, None, None]:
    """
    Context manager that emits a trace span to AgentCore Observability (Req 11.1).
    Yields the span_id for downstream correlation.
    """
    span_id = str(uuid.uuid4())
    t0 = time.perf_counter()
    try:
        yield span_id
        duration_ms = (time.perf_counter() - t0) * 1000
        _log_span(name, span_id, prospect_id, "SUCCESS", duration_ms, attributes)
    except Exception as exc:
        duration_ms = (time.perf_counter() - t0) * 1000
        _log_span(name, span_id, prospect_id, "ERROR", duration_ms, attributes, error=str(exc))
        raise


def _log_span(
    name: str,
    span_id: str,
    prospect_id: str,
    status: str,
    duration_ms: float,
    attributes: Optional[dict[str, Any]],
    error: Optional[str] = None,
) -> None:
    entry = {
        "eventType": "SPAN",
        "spanId": span_id,
        "spanName": name,
        "prospectId": prospect_id,
        "status": status,
        "durationMs": round(duration_ms, 2),
        "timestamp": datetime.utcnow().isoformat(),
        "attributes": attributes or {},
    }
    if error:
        entry["error"] = error
    _put_log(json.dumps(entry))
    _put_metric(f"SpanDuration.{name}", duration_ms, "Milliseconds")


def log_error(
    error_type: str,
    prospect_id: str,
    processing_step: str,
    details: str = "",
) -> None:
    """Emit an error event to AgentCore Observability (Req 11.3)."""
    entry = {
        "eventType": "ERROR",
        "errorType": error_type,
        "prospectId": prospect_id,
        "processingStep": processing_step,
        "details": details,
        "timestamp": datetime.utcnow().isoformat(),
    }
    _put_log(json.dumps(entry))


def log_data_request(
    source: str,
    request_id: str,
    duration_ms: float,
    status: str,
    prospect_id: str = "N/A",
) -> None:
    """Log all data source requests and responses (Req 11.2)."""
    entry = {
        "eventType": "DATA_REQUEST",
        "dataSource": source,
        "requestId": request_id,
        "prospectId": prospect_id,
        "durationMs": round(duration_ms, 2),
        "status": status,
        "timestamp": datetime.utcnow().isoformat(),
    }
    _put_log(json.dumps(entry))


def emit_prospect_metrics(
    prospect_type: str,
    count_identified: int,
    count_scored: int,
    count_recommended: int,
) -> None:
    """Emit per-cycle prospect counts to AgentCore Observability (Req 11.4)."""
    _put_metric(f"ProspectsIdentified.{prospect_type}", count_identified, "Count")
    _put_metric(f"ProspectsScored.{prospect_type}", count_scored, "Count")
    _put_metric(f"ProspectsRecommended.{prospect_type}", count_recommended, "Count")
