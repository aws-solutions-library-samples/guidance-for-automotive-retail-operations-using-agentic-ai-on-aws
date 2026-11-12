"""Admin Console — data-subject requests (spec §5.17 Data requests, §8.2, acceptance #25).

A customer (via My Garage) or an admin on their behalf files a request to
**download** or **delete** their data. An admin runs it:

  download — assembles the customer's rows across the operational tables with
             every C4 field removed (C4 never leaves F&I / My Garage self-view);
             returned for the admin to hand over, never written anywhere.
  delete   — anonymises the customer across DynamoDB: C3 contact/identity and
             free-text fields are tombstoned, C4 fields dropped, consent forced
             to opt-out, and each touched row marked `erased_at`. Operational
             records (deals, ROs) are kept for legal retention but carry no PII.
             Personalize and Quick Index purges are listed as pending external
             steps (they run in those services, not in this sandbox).

Requests live on the customer's `CUST_Consent` row as `data_requests: [{request_id,
kind, status, requested_at, …}]` — the same list My Garage writes
(customer/portal.request_data) and shows back to the customer — so both sides see
one status: received → in_progress → completed | failed. Every run is audit-logged.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
from typing import Any

from ..common import ddb
from . import audit_log

_STORE = "CUST_Consent"
TYPES = ("download", "delete")   # = customer.portal.DATA_REQUEST_KINDS

# Tables holding customer-linked rows (spec §4.2).
CUSTOMER_TABLES = [
    "DEALER_Customers", "CUST_Consent", "CUST_Messages", "CUST_Documents", "DEALER_Signals", "DEALER_Leads",
    "DEALER_Journeys", "DEALER_Appointments", "DEALER_Vehicles", "DEALER_Valuations", "DEALER_Deals",
    "DEALER_Deliveries", "DEALER_ServiceJourneys", "DEALER_RepairOrders", "DEALER_DeclinedWork",
    "DEALER_Touchpoints", "DEALER_NurturePlans", "DEALER_SalesPackages", "DEALER_TradeDispositions",
    "DEALER_Recommendations", "PLAT_Configurations",
]
# §8.2 C3 personal fields (tombstoned on delete; kept in a self-download)
C3_FIELDS = frozenset({"given_name", "family_name", "first_name", "last_name", "name", "email", "phone",
                       "address", "lat", "lon", "notes", "body", "comments", "summary", "transcript",
                       "uri", "share_token"})
# §8.2 C4 sensitive fields (never exported; dropped on delete)
C4_FIELDS = frozenset({"credit_score", "fico", "fico_score", "credit_tier", "ssn", "ssn_last4", "income",
                       "income_band", "payoff", "equity", "contract_terms", "prequal_result",
                       "lender_decision", "adverse_action_status", "ofac_status", "red_flags_status",
                       "apr_pct", "monthly_payment", "down_payment"})
EXTERNAL_STEPS = [
    {"system": "Amazon Personalize", "action": "delete user from dataset group app-vehicles (hashed user id)"},
    {"system": "Amazon Quick Index", "action": "purge CRM-note documents mentioning the customer"},
    {"system": "S3 lake", "action": "lake rows carry hashed ids only (§8.2); no purge needed"},
]


class NotFound(Exception):
    pass


class InvalidState(Exception):
    pass


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _find(rid: str, env_prefix: str | None) -> tuple[dict[str, Any], int]:
    for row in ddb.scan_all(_STORE, env_prefix):
        for i, r in enumerate(row.get("data_requests") or []):
            if r.get("request_id") == rid:
                return dict(row), i
    raise NotFound(f"data request {rid} not found")


def _save_request(customer_id: str, rid: str, patch: dict[str, Any], env_prefix: str | None) -> dict[str, Any]:
    """Merge `patch` into one request entry on the (possibly just-anonymised) consent row."""
    row = dict(ddb.get_item(_STORE, {"customer_id": customer_id}, env_prefix) or {"customer_id": customer_id})
    reqs = [dict(r) for r in (row.get("data_requests") or [])]
    for r in reqs:
        if r.get("request_id") == rid:
            r.update(patch)
            break
    else:
        reqs.append({"request_id": rid, **patch})
    row["data_requests"] = reqs
    row["updated_at"] = _now()
    ddb.put_item(_STORE, row, env_prefix)
    return next(r for r in reqs if r.get("request_id") == rid)


def create_request(customer_id: str, rtype: str, requested_by: str, channel: str = "admin",
                   env_prefix: str | None = None) -> dict[str, Any]:
    """Admin files a request on the customer's behalf (customers file via My Garage)."""
    if rtype not in TYPES:
        raise ValueError(f"type must be one of {TYPES}")
    if ddb.get_item("DEALER_Customers", {"customer_id": customer_id}, env_prefix) is None:
        raise NotFound(f"customer {customer_id} not found")
    row = ddb.get_item(_STORE, {"customer_id": customer_id}, env_prefix) or {}
    if any(r.get("kind") == rtype and r.get("status") in ("received", "in_progress")
           for r in row.get("data_requests") or []):
        raise InvalidState(f"a {rtype} request is already open for {customer_id}")
    ts = _now()
    rid = "DSR-" + hashlib.sha1(f"{customer_id}|{rtype}|{ts}|{requested_by}".encode()).hexdigest()[:10]
    req = _save_request(customer_id, rid, {"kind": rtype, "status": "received", "requested_at": ts,
                                           "requested_by": requested_by, "channel": channel}, env_prefix)
    audit_log.record(requested_by, f"data_request.create.{rtype}", rid, {"customer_id": customer_id},
                     env_prefix=env_prefix)
    return _public(customer_id, req)


def _public(customer_id: str, r: dict[str, Any]) -> dict[str, Any]:
    return {"customer_id": customer_id, "type": r.get("kind"), "channel": r.get("channel") or "my_garage",
            "requested_by": r.get("requested_by") or customer_id, "steps": r.get("steps") or [],
            **{k: v for k, v in r.items() if k not in ("export",)}}


def list_requests(status: str | None = None, env_prefix: str | None = None) -> dict[str, Any]:
    items = []
    for row in ddb.scan_all(_STORE, env_prefix):
        for r in row.get("data_requests") or []:
            items.append(_public(row.get("customer_id"), r))
    items.sort(key=lambda r: r.get("requested_at") or "", reverse=True)
    if status:
        items = [r for r in items if r.get("status") == status]
    open_ = sum(1 for r in items if r.get("status") in ("received", "in_progress"))
    return {"count": len(items), "pending": open_, "requests": items}


def _customer_rows(customer_id: str, env_prefix: str | None) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for t in CUSTOMER_TABLES:
        try:
            if t == "DEALER_Customers":
                row = ddb.get_item(t, {"customer_id": customer_id}, env_prefix)
                rows = [row] if row else []
            else:
                rows = ddb.scan_filter(t, "customer_id", customer_id, env_prefix)
        except Exception:
            rows = []
        if rows:
            out[t] = rows
    return out


def build_export(customer_id: str, env_prefix: str | None = None) -> dict[str, Any]:
    """Self-download: every customer-linked row, C4 fields removed."""
    data = _customer_rows(customer_id, env_prefix)
    removed = set()
    clean: dict[str, list[dict[str, Any]]] = {}
    for t, rows in data.items():
        clean[t] = []
        for r in rows:
            removed |= C4_FIELDS & set(r)
            clean[t].append({k: v for k, v in r.items() if k not in C4_FIELDS})
    return {"customer_id": customer_id, "tables": clean, "row_count": sum(len(v) for v in clean.values()),
            "c4_fields_excluded": sorted(removed), "generated_at": _now()}


def _erase(customer_id: str, env_prefix: str | None) -> list[dict[str, Any]]:
    ts = _now()
    steps = []
    for t, rows in _customer_rows(customer_id, env_prefix).items():
        n, err = 0, None
        for r in rows:
            r = {k: v for k, v in r.items() if k not in C4_FIELDS}
            for f in C3_FIELDS & set(r):
                r[f] = None if f in ("lat", "lon", "address") else "[erased]"
            if t == "CUST_Consent":
                r.update({"email_opt_in": False, "sms_opt_in": False, "voice_opt_in": False,
                          "sms_opt_out_at": ts, "preferred_channel": None})
            r["erased_at"] = ts
            try:
                ddb.put_item(t, r, env_prefix)
                n += 1
            except Exception as e:     # record, keep going: a partial erase must be visible
                err = f"{type(e).__name__}: {e}"
        steps.append({"system": f"DynamoDB {t}", "action": "anonymised", "rows": n,
                      "status": "failed" if err else "done", "error": err, "at": ts})
    steps += [{**s, "status": "pending_external"} for s in EXTERNAL_STEPS]
    return steps


def run_request(rid: str, actor: str, env_prefix: str | None = None) -> dict[str, Any]:
    row, i = _find(rid, env_prefix)
    req = row["data_requests"][i]
    cid = row["customer_id"]
    if req.get("status") not in ("received", "failed"):
        raise InvalidState(f"request {rid} is already {req.get('status')}")
    _save_request(cid, rid, {"status": "in_progress", "run_by": actor}, env_prefix)
    ts = _now()
    out: dict[str, Any] = {}
    if req.get("kind") == "download":
        export = build_export(cid, env_prefix)
        steps = [{"system": "DynamoDB", "action": "assembled export", "rows": export["row_count"], "status": "done", "at": ts}]
        out["export"] = export
    else:
        steps = _erase(cid, env_prefix)
    failed = any(st.get("status") == "failed" for st in steps)
    pending_ext = sum(1 for st in steps if st.get("status") == "pending_external")
    saved = _save_request(cid, rid, {"status": "failed" if failed else "completed", "steps": steps, "run_by": actor,
                                     "run_at": ts, "completed_at": None if failed else ts,
                                     "pending_external_steps": pending_ext}, env_prefix)
    audit_log.record(actor, f"data_request.run.{req.get('kind')}", rid,
                     {"customer_id": cid, "status": saved["status"],
                      "rows": sum(int(st.get("rows") or 0) for st in steps)}, env_prefix=env_prefix)
    return {**_public(cid, saved), **out}
