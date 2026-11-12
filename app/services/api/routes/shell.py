"""Unified Shell support routes: AI Log traces and ⌘K global search (§5.2.1, §5.18.5).

  GET /traces?journey_id=|service_journey_id=|lead_id=|correlation_id=&limit=   AI Log (newest first)
  GET /traces/latest?runs=<run names>                                   agent bar "last ran" per automation
  GET /search?q=&types=                                                 scope-filtered search

Search is a scope-filtered scan over the operational tables (sandbox). The
production path is the OpenSearch Serverless index fed from Streams (§5.18.5);
the response shape is the same, so swapping the backend changes no client.
Results carry (type, id) for the resource-URL resolver — never a screen path.
"""
from __future__ import annotations

import re
import time
from typing import Any

from ...common import authz, ddb
from .. import routing as r

# Roles that must not discover customers by contact data (technician/parts: §5.20.5).
_NO_CUSTOMER_SEARCH = frozenset({"dealer_technician", "dealer_parts"})
_MAX = 8


def _traces(event, claims, _m):
    r.staff(claims)
    q = r.qs(event)
    limit = max(1, min(int(q.get("limit") or 25), 100))
    if q.get("journey_id"):
        r.journey_in_scope(claims, q["journey_id"])
        rows = ddb.scan_filter("PLAT_AgentTraces", "journey_id", q["journey_id"])
    elif q.get("service_journey_id"):
        sj = r.service_journey_in_scope(claims, q["service_journey_id"])
        rows = ddb.scan_filter("PLAT_AgentTraces", "service_journey_id", sj["service_journey_id"]) + \
            ddb.scan_filter("PLAT_AgentTraces", "journey_id", sj["service_journey_id"])
    elif q.get("lead_id"):
        # the Lead Desk's feed: what the agents did on this lead (intake, draft, send, assign,
        # booking) plus, once the lead has a journey, everything on that journey
        lead = ddb.get_item("DEALER_Leads", {"lead_id": q["lead_id"]})
        if lead is None:
            raise r.Bad(404, f"lead {q['lead_id']} not found")
        r.dealer_in_scope(claims, lead.get("dealer_id"))
        rows = ddb.scan_filter("PLAT_AgentTraces", "lead_id", q["lead_id"])
        if lead.get("journey_id"):
            seen = {t.get("trace_id") for t in rows}
            rows += [t for t in ddb.scan_filter("PLAT_AgentTraces", "journey_id", lead["journey_id"])
                     if t.get("trace_id") not in seen]
    elif q.get("correlation_id"):
        rows = ddb.scan_filter("PLAT_AgentTraces", "correlation_id", q["correlation_id"])
    else:
        raise r.Bad(400, "query must include journey_id, service_journey_id, lead_id, or correlation_id")
    rows.sort(key=lambda t: t.get("ts") or "", reverse=True)
    keep = ("trace_id", "ts", "agent_id", "tool", "outcome", "latency_ms", "autonomy_level",
            "correlation_id", "routing_decision", "citations", "rule_id")
    return 200, {"count": len(rows), "traces": [{k: t.get(k) for k in keep} for t in rows[:limit]]}


_AGENTS = frozenset(f"A{n}" for n in range(1, 8))
_RUN_KEY = re.compile(r"^[a-z0-9][a-z0-9_.\-]{2,63}$")
_MAX_KEYS = 40
_LATEST_TTL_S = 30.0
_LATEST_CACHE: dict[str, Any] = {"at": 0.0, "rows": []}
_LATEST_FIELDS = ("trace_id", "ts", "tool", "agent_id", "outcome", "market_id", "dealer_id")


def _agent_runs() -> list[dict[str, Any]]:
    """Agent trace rows (A1–A7; no access-layer or admin-audit rows), newest
    first, re-read at most every _LATEST_TTL_S per warm Lambda."""
    now = time.monotonic()
    if now - _LATEST_CACHE["at"] > _LATEST_TTL_S or not _LATEST_CACHE["at"]:
        # only the fields "last ran" shows, read in parallel: the full rows (payloads, citations)
        # of a 40k-row log took 6–8 s and held every landing page open
        rows = [t for t in ddb.scan_projected("PLAT_AgentTraces", _LATEST_FIELDS, "trace_id")
                if t.get("agent_id") in _AGENTS]
        rows.sort(key=lambda t: t.get("ts") or "", reverse=True)
        _LATEST_CACHE.update(at=now, rows=rows)
    return _LATEST_CACHE["rows"]


def _latest(event, claims, _m):
    """GET /traces/latest?runs=job.stall-sweep,a2.publish_shift_list

    The newest trace of each named automation run in the caller's market — what
    the agent bar shows as "last ran" when no journey or RO is open. A run name
    matches a trace whose tool is the name or ends with ".<name>" (the runtime
    writes `runtime.job.<job>`). A dealer-scoped caller sees runs from its own
    stores and market-wide runs (scheduled jobs carry no dealer)."""
    r.staff(claims)
    keys = [k for k in (r.qs(event).get("runs") or "").split(",") if k]
    if not keys or len(keys) > _MAX_KEYS or not all(_RUN_KEY.match(k) for k in keys):
        raise r.Bad(400, f"runs must list 1–{_MAX_KEYS} run names (lower-case letters, digits, . _ -)")
    market = claims.get("market_id") or "us"
    dealers = None if claims.get("is_oem") else set(claims.get("dealer_ids") or ())
    out: dict[str, Any] = {}
    want = set(keys)
    for t in _agent_runs():
        if not want:
            break
        if (t.get("market_id") or market) != market:
            continue
        if dealers is not None and t.get("dealer_id") and t["dealer_id"] not in dealers:
            continue
        tool = str(t.get("tool") or "")
        for k in [k for k in want if tool == k or tool.endswith("." + k)]:
            out[k] = {key: t.get(key) for key in ("ts", "tool", "agent_id", "outcome", "trace_id")}
            want.discard(k)
    return 200, {"market_id": market, "runs": out, "source_ref": "PLAT_AgentTraces (agent rows)"}


def _match(needle: str, *fields: Any) -> bool:
    return any(needle in str(f).lower() for f in fields if f)


def _model_names() -> dict[str, tuple[str, str]]:
    """model_id → (make, model) from the catalog, read once per search (not per vehicle);
    the same split as common/vehicles.display_fields ("Meridian Ion C" → Meridian · Ion C)."""
    out: dict[str, tuple[str, str]] = {}
    for row in ddb.scan_all("OEM_CatalogModels"):
        brand, name = str(row.get("brand") or ""), str(row.get("name") or "")
        short = brand.split()[0] if brand else ""
        if short and name.startswith(short + " "):
            brand, name = short, name[len(short) + 1:]
        if row.get("model_id"):
            out[row["model_id"]] = (brand, name)
    return out


def _car_label(v: dict[str, Any], names: dict[str, tuple[str, str]]) -> str:
    """"2023 Meridian Ion C" for a vehicle or stock row (catalog names, else its own make/model)."""
    make, model = names.get(v.get("model_id") or "", (v.get("make") or "", v.get("model") or ""))
    return " ".join(str(x) for x in (v.get("year"), make, model) if x)


def _search(event, claims, _m):
    q = r.qs(event)
    needle = (q.get("q") or "").strip().lower()
    if len(needle) < 2:
        raise r.Bad(400, "query must be at least 2 characters")
    types = set(filter(None, (q.get("types") or "").split(","))) or None
    want = lambda t: types is None or t in types  # noqa: E731
    role = claims.get("role")
    out: list[dict[str, Any]] = []

    if role == "customer":
        r.customer(claims)
        for j in ddb.scan_filter("DEALER_Journeys", "customer_id", claims["customer_id"]):
            if _match(needle, j.get("journey_id"), j.get("stage")):
                out.append({"type": "journey", "id": j["journey_id"], "label": j["journey_id"], "detail": j.get("stage")})
        return 200, {"q": needle, "results": out[:_MAX]}

    r.staff(claims)
    phone_needle = re.sub(r"\D", "", needle)
    if want("customer") and role not in _NO_CUSTOMER_SEARCH:
        hits = []
        for c in r.scoped_rows(claims, ddb.scan_all("DEALER_Customers")):
            name = f"{c.get('given_name', '')} {c.get('family_name', '')}".strip()
            phone = re.sub(r"\D", "", str(c.get("phone") or ""))
            if _match(needle, name, c.get("email"), c.get("customer_id")) or (len(phone_needle) >= 4 and phone_needle in phone):
                hits.append({"type": "customer", "id": c["customer_id"], "label": name or c["customer_id"],
                             "detail": c.get("dealer_id")})
        out += hits[:_MAX]
    if want("vin"):
        # Owned cars first (that is what Book service looks up), found by VIN, plate, owner
        # name or model as an advisor would type them; then stock units by VIN / stock no.
        # Detail reads "2023 Meridian Ion C · Sam Kowalski", never a raw model code.
        show_owner = role not in _NO_CUSTOMER_SEARCH
        names = _model_names()
        owners = ({c["customer_id"]: f"{c.get('given_name', '')} {c.get('family_name', '')}".strip()
                   for c in r.scoped_rows(claims, ddb.scan_all("DEALER_Customers"))} if show_owner else {})
        owned, stock = [], []
        for v in r.scoped_rows(claims, ddb.scan_all("DEALER_Vehicles")):
            car = _car_label(v, names)
            owner = owners.get(v.get("customer_id"), "")
            if _match(needle, v.get("vin"), v.get("plate"), car, owner if show_owner else None):
                owned.append({"type": "vin", "id": v["vin"], "label": v["vin"],
                              "detail": " · ".join(x for x in (car, owner) if x) or "owned vehicle"})
        for v in r.scoped_rows(claims, ddb.scan_all("DEALER_Inventory")):
            if _match(needle, v.get("vin"), v.get("stock_number")):
                stock.append({"type": "vin", "id": v.get("vin") or v.get("stock_number"),
                              "label": v.get("vin") or v.get("stock_number"),
                              "detail": f"{_car_label(v, names) or 'in stock'} · stock {v.get('stock_number')}"})
        out += (owned + stock)[:_MAX]
    if want("journey") and role not in _NO_CUSTOMER_SEARCH:
        out += [{"type": "journey", "id": j["journey_id"], "label": j["journey_id"], "detail": j.get("stage")}
                for j in r.scoped_rows(claims, ddb.scan_all("DEALER_Journeys"))
                if _match(needle, j.get("journey_id"))][:_MAX]
    if want("service_journey"):
        out += [{"type": "service_journey", "id": s["service_journey_id"], "label": s["service_journey_id"],
                 "detail": f"{s.get('stage')} · {s.get('vin') or ''}"}
                for s in r.scoped_rows(claims, ddb.scan_all("DEALER_ServiceJourneys"))
                if _match(needle, s.get("service_journey_id"), s.get("vin"))][:_MAX]
    if want("dealer"):
        out += [{"type": "dealer", "id": n["node_id"], "label": n.get("name") or n["node_id"], "detail": n["node_id"]}
                for n in authz.org_nodes(None, claims.get("market_id"))
                if n.get("type") == "dealer" and (claims.get("is_oem") or n["node_id"] in claims.get("dealer_ids", set()))
                and _match(needle, n.get("name"), n.get("node_id"))][:_MAX]
    if claims.get("is_oem") and want("account"):
        out += [{"type": "account", "id": a["account_id"], "label": a.get("name") or a["account_id"],
                 "detail": a.get("account_type")}
                for a in ddb.scan_all("OEM_Accounts") if _match(needle, a.get("name"), a.get("account_id"))][:_MAX]
    if want("campaign") and (claims.get("is_oem") or role == "dealer_manager"):
        out += [{"type": "campaign", "id": c["campaign_id"], "label": c.get("name") or c["campaign_id"],
                 "detail": c.get("objective")}
                for c in ddb.scan_all("OEM_Campaigns") if _match(needle, c.get("name"), c.get("campaign_id"))][:_MAX]
    return 200, {"q": needle, "results": out[: _MAX * 3]}


ROUTES = [
    ("GET", re.compile(r"^/traces$"), _traces),
    ("GET", re.compile(r"^/traces/latest$"), _latest),
    ("GET", re.compile(r"^/search$"), _search),
]
