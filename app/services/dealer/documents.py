"""Document service (spec §4.9, G-24) — sandbox implementation.

`CUST_Documents { document_id, type/kind, owner_scope, resource_url, s3_key,
mime, version, retention_class, created_by, created_at, shared_with[] }`.

  create_document(kind, ...)          -> metadata row + `document.created` event; the
                                         rendered body is generated from records
  list_documents(...)                 -> by journey / deal / customer
  signed_url(document_id, ttl)        -> short-lived signed content URL (the sandbox
                                         stand-in for an S3 pre-signed GET)
  render(document_id, sig, exp)       -> verifies the signature, returns the content
  share(document_id, actor)           -> adds "customer" to shared_with (My Garage) +
                                         notification.requested

Production stores the body in S3 `app-docs` (SSE-KMS, versioning, lifecycle by
retention class) and `signed_url` becomes `generate_presigned_url`; the contract
and metadata row are the same. C4 documents (contract terms, credit results)
are only ever shared with the customer they belong to.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import json
import os
import time
from typing import Any

from ..common import ddb
from ..events import bus as _bus

_DOCS = "CUST_Documents"

# retention class by document kind (§4.9: operational 2y, deal 7y)
_RETENTION = {
    "deal_worksheet": "deal", "fi_menu": "deal", "contract": "deal", "buyers_order": "deal",
    "adverse_action_notice": "deal", "delivery_checklist": "operational", "invoice": "deal",
}
KINDS = tuple(_RETENTION)


class NotFound(Exception):
    pass


class Forbidden(Exception):
    pass


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _secret() -> bytes:
    s = os.environ.get("RETAIL_OPS_DOC_SECRET") or os.environ.get("RETAIL_OPS_CTX_SECRET")
    if not s:
        if os.environ.get("RETAIL_OPS_STAGE", "dev") not in ("dev", "demo"):
            raise Forbidden("RETAIL_OPS_DOC_SECRET must be set outside dev/demo")
        s = "retail-ops-dev-doc-secret-change-me"
    return s.encode()


def create_document(kind: str, *, customer_id: str | None, actor: str, journey_id: str | None = None,
                    deal_id: str | None = None, dealer_id: str | None = None, title: str | None = None,
                    data_class: str = "C3", share_with_customer: bool = False,
                    body: dict[str, Any] | None = None, env_prefix: str | None = None) -> dict[str, Any]:
    if kind not in _RETENTION:
        raise ValueError(f"unknown document kind {kind!r}")
    ts = _now()
    did = "DOC-" + hashlib.sha1(f"{kind}|{deal_id}|{journey_id}|{customer_id}|{ts}".encode()).hexdigest()[:12]
    prior = [d for d in (ddb.scan_filter(_DOCS, "deal_id", deal_id, env_prefix) if deal_id else [])
             if d.get("kind") == kind]
    row = {
        "document_id": did, "kind": kind, "title": title or kind.replace("_", " ").title(),
        "customer_id": customer_id, "journey_id": journey_id, "deal_id": deal_id, "dealer_id": dealer_id,
        "owner_scope": dealer_id, "mime": "application/json", "version": len(prior) + 1,
        "retention_class": _RETENTION[kind], "data_class": data_class,
        "s3_key": f"app-docs/{customer_id or dealer_id}/{did}.json",
        "uri": f"app-docs/{customer_id or dealer_id}/{did}.json",
        # canonical link to the owning resource (no `document` resource type yet)
        "resource_url": (f"/r/deal/{deal_id}" if deal_id else f"/r/journey/{journey_id}" if journey_id else None),
        "created_by": actor, "created_at": ts,
        "shared_with": ["customer"] if share_with_customer else [],
        "body": body or {}, "status": "issued",
        "source_ref": "DocumentService.create (sandbox; S3 app-docs live path)",
    }
    ddb.put_item(_DOCS, row, env_prefix)
    _bus.emit("document.created", {"document_id": did, "kind": kind, "journey_id": journey_id,
                                   "deal_id": deal_id, "dealer_id": dealer_id, "customer_id": customer_id,
                                   "shared_with": row["shared_with"]},
              actor=actor, env_prefix=env_prefix)
    return {k: v for k, v in row.items() if k != "body"}


def get_document(document_id: str, env_prefix: str | None = None) -> dict[str, Any]:
    d = ddb.get_item(_DOCS, {"document_id": document_id}, env_prefix)
    if d is None:
        raise NotFound(f"document {document_id} not found")
    return d


def list_documents(*, journey_id: str | None = None, deal_id: str | None = None,
                   customer_id: str | None = None, env_prefix: str | None = None) -> list[dict[str, Any]]:
    if deal_id:
        rows = ddb.scan_filter(_DOCS, "deal_id", deal_id, env_prefix)
    elif journey_id:
        rows = ddb.scan_filter(_DOCS, "journey_id", journey_id, env_prefix)
    elif customer_id:
        rows = ddb.scan_filter(_DOCS, "customer_id", customer_id, env_prefix)
    else:
        raise ValueError("journey_id, deal_id, or customer_id is required")
    rows.sort(key=lambda d: d.get("created_at") or "", reverse=True)
    return [{k: v for k, v in r.items() if k != "body"} for r in rows]


def _sig(document_id: str, exp: int) -> str:
    return hmac.new(_secret(), f"{document_id}|{exp}".encode(), hashlib.sha256).hexdigest()[:32]


def signed_url(document_id: str, ttl_s: int = 300, env_prefix: str | None = None) -> dict[str, Any]:
    get_document(document_id, env_prefix)
    exp = int(time.time()) + ttl_s
    return {"document_id": document_id, "expires_at": exp,
            "url": f"/documents/{document_id}/content?exp={exp}&sig={_sig(document_id, exp)}"}


def render(document_id: str, sig: str | None, exp: str | None, env_prefix: str | None = None) -> dict[str, Any]:
    try:
        exp_i = int(exp or 0)
    except ValueError:
        raise Forbidden("bad expiry")
    if not sig or exp_i < time.time() or not hmac.compare_digest(sig, _sig(document_id, exp_i)):
        raise Forbidden("document link expired or invalid")
    d = get_document(document_id, env_prefix)
    return {"document_id": document_id, "kind": d.get("kind"), "title": d.get("title"),
            "mime": d.get("mime") or "application/json", "version": d.get("version"),
            "created_at": d.get("created_at"), "content": d.get("body") or {"note": "Document on file", "uri": d.get("uri")}}


def share(document_id: str, actor: str, env_prefix: str | None = None) -> dict[str, Any]:
    d = dict(get_document(document_id, env_prefix))
    if not d.get("customer_id"):
        raise Forbidden("document has no customer owner to share with")
    shared = list(d.get("shared_with") or [])
    if "customer" not in shared:
        shared.append("customer")
    d["shared_with"] = shared
    d["shared_at"] = _now()
    d["shared_by"] = actor
    ddb.put_item(_DOCS, d, env_prefix)
    _bus.emit("notification.requested", {"kind": "document.shared", "document_id": document_id,
                                         "customer_id": d.get("customer_id"), "journey_id": d.get("journey_id"),
                                         "dealer_id": d.get("dealer_id"), "title": d.get("title")},
              actor=actor, env_prefix=env_prefix)
    return {"document_id": document_id, "shared_with": shared}


def body_json(obj: Any) -> dict[str, Any]:
    """Strip to JSON-safe content for a document body."""
    return json.loads(json.dumps(obj, default=str))
