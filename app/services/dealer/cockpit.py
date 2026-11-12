"""Store Cockpit read model (dealer principal / GM), spec §5.14, G-03.

Tabs: Funnel, Sales, Inventory, Trade & Used, F&I, Service, CX, Alerts, OEM
Comms. Each tab returns a KPI grid (every KPI with a deep link), detail tables,
and a sourced [AI INSIGHT] composed from the same numbers — no agent in the
request path (R5), no invented figures; a missing denominator or feed is
reported as null with a note. When the nightly store digest (store_digest.py,
the Campaign Agent) has written a fresh insight for the scope, the tab shows
that one instead, labelled with who wrote it and when.

  build_cockpit(scope, dealer_id?, market?)   -> summary tiles (all tabs, compact)
  build_tab(scope, tab, dealer_id?, market?)  -> one tab in full
  snapshot(scope, dealer_id?, market?)        -> materialized snapshot of every tab

Materialization: the spec wants event-materialized read models (§4.6). Here
`snapshot()` is the materializer; results are cached per (dealer scope, market)
and invalidated by the domain events that change them (subscribed below via
`bus.subscribe`), so a page load re-uses the last materialization until an
event lands. A persisted read-model table fed by the subscriber Lambda is the
production step (needs a table + subscriber deployment).
"""
from __future__ import annotations

import datetime as _dt
import time
from typing import Any

from ..common import ddb, labels, people, vehicles
from ..oem import guardrails
from ..service import metrics as svc_metrics
from ..events import bus as _bus
from ..journeys.assist import JOURNEY_STAGES

TABS = ("funnel", "sales", "inventory", "trade", "fi", "service", "cx", "alerts", "comms")
TAB_LABELS = {"funnel": "Funnel", "sales": "Sales", "inventory": "Inventory", "trade": "Trade & Used",
              "fi": "F&I", "service": "Service", "cx": "CX", "alerts": "Alerts", "comms": "OEM Comms"}
_ACTIVE = ("DISCOVER", "CONFIGURE", "QUALIFY", "FINANCE", "TRADE_IN", "AGREE", "DELIVER")
_CACHE_TTL_S = 60   # bounded staleness when the invalidating event lands in another process
_CACHE: dict[tuple, tuple[float, dict[str, Any]]] = {}

# events that change cockpit numbers (§4.6 catalog)
_INVALIDATING = ("journey.stage_changed", "journey.created", "deal.structured", "deal.booked", "deal.pushed",
                 "vehicle.delivered", "inventory.changed", "service.stage_changed", "ro.opened", "ro.closed",
                 "estimate.line_decided", "inspection.submitted", "alert.raised", "alert.acknowledged",
                 "lead.received", "service.handoff_to_sales")


def _invalidate(detail: dict[str, Any], env_prefix: str | None = None) -> dict[str, Any]:
    dealer = (detail.get("scope") or {}).get("dealer_id") or detail.get("dealer_id")
    dropped = [k for k in list(_CACHE) if dealer is None or dealer in k[0] or not k[0]]
    for k in dropped:
        _CACHE.pop(k, None)
    return {"handled": True, "cockpit_invalidated": len(dropped)}


for _t in _INVALIDATING:
    if _invalidate not in _bus._EXTRA_SUBSCRIBERS.get(_t, []):
        _bus.subscribe(_t, _invalidate)


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None and not isinstance(v, bool) else None
    except (TypeError, ValueError):
        return None


def _rate(n: float, d: float) -> float | None:
    return round(n / d, 3) if d else None


def _dealer_scope(scope: dict[str, Any], dealer_id: str | None) -> tuple[set[str], bool]:
    if scope.get("is_oem"):
        return ({dealer_id} if dealer_id else set()), True
    allowed = set(scope.get("dealer_ids") or set())
    if dealer_id:
        allowed &= {dealer_id}
    return allowed, False


class _Ctx:
    """Rows in scope, loaded once per materialization."""

    def __init__(self, scope: dict[str, Any], dealer_id: str | None, market: str, env_prefix: str | None):
        self.market = market
        self.env = env_prefix
        self.dealers, self.is_oem = _dealer_scope(scope, dealer_id)
        self._rows: dict[str, list[dict[str, Any]]] = {}

    def ok(self, row: dict[str, Any]) -> bool:
        d = row.get("dealer_id")
        if self.is_oem and not self.dealers:
            # market-wide view: every store of THIS market. A row without a
            # market_id counts when its store is in the market; one with neither
            # is kept (it is joined to in-market rows by id).
            if row.get("market_id"):
                return row["market_id"] == self.market
            return d is None or d in self.market_dealers()
        return (self.is_oem and d in self.dealers) or d in self.dealers

    def market_dealers(self) -> set[str]:
        if "__market_dealers__" not in self._rows:
            self._rows["__market_dealers__"] = [
                {"dealer_id": d} for d in {j.get("dealer_id") for j in
                                          ddb.scan_filter("DEALER_Journeys", "market_id", self.market, self.env)} if d]
        return {r["dealer_id"] for r in self._rows["__market_dealers__"]}

    def rows(self, table: str, by_market: bool = False) -> list[dict[str, Any]]:
        if table not in self._rows:
            src = ddb.scan_filter(table, "market_id", self.market, self.env) if by_market else ddb.scan_all(table, self.env)
            self._rows[table] = [r for r in src if self.ok(r)]
        return self._rows[table]

    def all_rows(self, table: str) -> list[dict[str, Any]]:
        key = f"__all__{table}"
        if key not in self._rows:
            self._rows[key] = ddb.scan_all(table, self.env)
        return self._rows[key]


def _kpi(label: str, value: Any, link: str | None = None, delta: str | None = None,
         trend: str | None = None, fmt: str = "number", source: str | None = None) -> dict[str, Any]:
    return {"label": label, "value": value, "link": link, "delta": delta, "trend": trend, "format": fmt,
            "source_ref": source}


def _month(ts: str | None) -> str:
    return (ts or "")[:7]


# --- tabs ------------------------------------------------------------------------

def _funnel(c: _Ctx) -> dict[str, Any]:
    js = c.rows("DEALER_Journeys", by_market=True)
    by_stage = {s: 0 for s in JOURNEY_STAGES}
    for j in js:
        if j.get("stage") in by_stage:
            by_stage[j["stage"]] += 1
    stalled = [j for j in js if j.get("stalled") and j.get("stage") in _ACTIVE]
    names = people.directory(c.env)
    by_source: dict[str, dict[str, int]] = {}
    for j in js:
        s = j.get("source") or "unknown"
        b = by_source.setdefault(s, {"journeys": 0, "reached_agree": 0, "delivered": 0})
        b["journeys"] += 1
        idx = JOURNEY_STAGES.index(j["stage"]) if j.get("stage") in JOURNEY_STAGES else 0
        b["reached_agree"] += 1 if idx >= JOURNEY_STAGES.index("AGREE") else 0
        b["delivered"] += 1 if j.get("stage") == "OWN" else 0
    sources = sorted(({"source": k, **v, "conversion": _rate(v["delivered"], v["journeys"])} for k, v in by_source.items()),
                     key=lambda r: -r["journeys"])
    active = sum(by_stage[s] for s in _ACTIVE)
    missing = sum(1 for j in js if j.get("stage") in _ACTIVE and (j.get("missing_context") or []))
    best = max((s for s in sources if s["journeys"] >= 5), key=lambda s: s["conversion"] or 0, default=None)
    return {
        "kpis": [
            _kpi("Active journeys", active, "/dealer/journeys", source="DEALER_Journeys"),
            _kpi("Stalled", len(stalled), "/dealer/journeys?stalled=1", trend="down" if stalled else None,
                 delta=f"{_rate(len(stalled), active) and round(100 * _rate(len(stalled), active))}% of active" if active else None),
            _kpi("Missing context", missing, "/dealer/journeys?missing=1", source="validate_handoff gaps"),
            _kpi("Owned (delivered)", by_stage["OWN"], "/dealer/delivery"),
        ],
        "by_stage": [{"stage": s, "count": by_stage[s], "link": f"/dealer/journeys?stage={s}"} for s in JOURNEY_STAGES],
        "by_source": sources,
        "stalled": [{"journey_id": j["journey_id"], "stage": j.get("stage"), "idle_days": j.get("idle_days"),
                     "owner_user_id": j.get("owner_user_id"), "owner_name": people.name(j.get("owner_user_id"), names),
                     "link": f"/r/journey/{j['journey_id']}"}
                    for j in sorted(stalled, key=lambda j: -(j.get("idle_days") or 0))[:10]],
        "insight": {
            "text": (f"{len(stalled)} of {active} active journeys are stalled"
                     + (f" and {missing} are missing handoff context" if missing else "")
                     + (f". Leads from {labels.lead_source_label(best['source'])} convert best "
                        f"({round(100 * (best['conversion'] or 0))}% to delivery) — "
                        f"weight follow-up there first." if best else ".")),
            "sources": ["DEALER_Journeys.stage/stalled/source", "validate_handoff"],
        },
    }


def _sales(c: _Ctx) -> dict[str, Any]:
    deals = c.rows("DEALER_Deals")
    booked = [d for d in deals if d.get("status") == "booked"]
    months = sorted({_month(d.get("booked_at") or d.get("created_at")) for d in booked if (d.get("booked_at") or d.get("created_at"))})
    mtd_key = months[-1] if months else None
    mtd = [d for d in booked if _month(d.get("booked_at") or d.get("created_at")) == mtd_key]
    gross = sum(_num(d.get("price_total")) or 0 for d in booked)
    fip = [p for p in c.all_rows("DEALER_FIProducts") if p.get("accepted")]
    booked_ids = {d["deal_id"] for d in booked}
    fi_rev = sum(_num(p.get("price")) or 0 for p in fip if p.get("deal_id") in booked_ids)
    objective = None
    for p in c.rows("OEM_AccountPlans"):
        for o in p.get("objectives") or []:
            if o.get("kpi") in ("retail_units", "units_mtd", "sales_objective"):
                objective = _num(o.get("target"))
    js = c.rows("DEALER_Journeys", by_market=True)
    by_owner: dict[str, dict[str, Any]] = {}
    jowner = {j["journey_id"]: j.get("owner_user_id") for j in js}
    for j in js:
        o = j.get("owner_user_id") or "unassigned"
        b = by_owner.setdefault(o, {"user_id": o, "active": 0, "booked": 0, "gross": 0.0, "revenue": 0.0})
        if j.get("stage") in _ACTIVE:
            b["active"] += 1
    for d in booked:
        o = jowner.get(d.get("journey_id")) or "unassigned"
        b = by_owner.setdefault(o, {"user_id": o, "active": 0, "booked": 0, "gross": 0.0, "revenue": 0.0})
        b["booked"] += 1
        b["gross"] += _num(d.get("price_total")) or 0
        b["revenue"] = b["gross"]
    board = sorted(by_owner.values(), key=lambda b: (-b["booked"], -b["active"]))[:10]
    for b in board:
        u = ddb.get_item("PLAT_Users", {"user_id": b["user_id"]}, c.env) if b["user_id"] != "unassigned" else None
        b["name"] = " ".join(filter(None, [(u or {}).get("given_name"), (u or {}).get("family_name")])) or b["user_id"]
        b["gross"] = round(b["gross"], 2)
    currency = booked[0].get("currency") if booked else None
    return {
        "currency": currency,
        "kpis": [
            _kpi("Units booked (latest month)", len(mtd), "/dealer/fni", delta=(f"vs objective {int(objective)}" if objective else "no unit objective on file"),
                 source=f"DEALER_Deals booked {mtd_key or ''}"),
            # the deal records the selling price; gross profit needs vehicle cost, which the DMS holds —
            # so this is revenue, and says so
            _kpi("Revenue (all booked)", round(gross, 2), None, fmt="money", source="DEALER_Deals.price_total (selling price)"),
            _kpi("Avg selling price", round(gross / len(booked), 2) if booked else None, None, fmt="money"),
            _kpi("PVR (F&I)", round(fi_rev / len(booked), 2) if booked else None, "/dealer/fni", fmt="money",
                 source="DEALER_FIProducts accepted / booked deals"),
        ],
        "objective_units": objective,
        "by_month": [{"month": m, "units": sum(1 for d in booked if _month(d.get("booked_at") or d.get("created_at")) == m)} for m in months[-6:]],
        "leaderboard": board,
        "insight": {"text": (f"{len(booked)} deals booked; {len(mtd)} in {mtd_key}. "
                             + (f"Top producer {board[0]['name']} has {board[0]['booked']} booked with {board[0]['active']} active. " if board else "")
                             + ("No unit objective is on the OEM account plan, so pacing vs objective is not shown." if objective is None else "")),
                    "sources": ["DEALER_Deals", "DEALER_Journeys.owner_user_id", "OEM_AccountPlans.objectives"]},
    }


def _inventory(c: _Ctx) -> dict[str, Any]:
    from . import inventory as inv_mod
    inv = c.rows("DEALER_Inventory", by_market=True)
    in_stock = [v for v in inv if v.get("status") in ("in_stock", "reserved")]
    booked = [d for d in c.rows("DEALER_Deals") if d.get("status") == "booked"]
    js = {j["journey_id"]: j for j in c.rows("DEALER_Journeys", by_market=True)}
    sold_by_model: dict[str, int] = {}
    for d in booked:
        m = (js.get(d.get("journey_id")) or {}).get("primary_model_id")
        if m:
            sold_by_model[m] = sold_by_model.get(m, 0) + 1
    demand = inv_mod.demand_by_trim(c.market, c.env)["model"]
    models = sorted({v.get("model_id") for v in inv if v.get("model_id")})
    names = vehicles.model_names(c.env)
    window = 90
    rows = []
    for m in models:
        units = sum(1 for v in in_stock if v.get("model_id") == m)
        rate = sold_by_model.get(m, 0) / window
        ages = [_num(v.get("age_days")) or 0 for v in in_stock if v.get("model_id") == m]
        rows.append({"model_id": m, "model_name": names.get(m, m), "in_stock": units, "sold_window": sold_by_model.get(m, 0),
                     "days_supply": round(units / rate, 0) if rate else None,
                     "aged_60": sum(1 for a in ages if a >= 60), "demand": demand.get(m, 0),
                     "demand_vs_stock": round(demand.get(m, 0) / units, 2) if units else None,
                     "link": f"/dealer/inventory?model={m}"})
    ages = [_num(v.get("age_days")) or 0 for v in in_stock]
    buckets = {"0-30": 0, "31-60": 0, "61-90": 0, "90+": 0}
    for a in ages:
        buckets["0-30" if a <= 30 else "31-60" if a <= 60 else "61-90" if a <= 90 else "90+"] += 1
    total_rate = sum(sold_by_model.values()) / window
    short = [r for r in rows if r["demand_vs_stock"] and r["demand_vs_stock"] > 1]
    heavy = sorted((r for r in rows if r["aged_60"]), key=lambda r: -r["aged_60"])
    return {
        "kpis": [
            _kpi("In stock", len(in_stock), "/dealer/inventory", source="DEALER_Inventory"),
            _kpi("Days supply", round(len(in_stock) / total_rate, 0) if total_rate else None, "/dealer/inventory",
                 delta=f"{window}-day sales rate", source="booked deals / in-stock units"),
            _kpi("Aged 60+ days", buckets["61-90"] + buckets["90+"], "/dealer/inventory?aged=60",
                 trend="down" if buckets["90+"] else None),
            _kpi("In transit", sum(1 for v in inv if v.get("status") == "in_transit"), "/dealer/inventory?status=in_transit"),
        ],
        "by_model": rows, "aging": [{"bucket": k, "units": v} for k, v in buckets.items()],
        "insight": {"text": ((f"Demand exceeds stock for {labels.join_words(r.get('model_name') or r['model_id'] for r in short[:3])}. " if short else "")
                             + (f"{(heavy[0].get('model_name') or heavy[0]['model_id'])} carries the most aged units ({heavy[0]['aged_60']} over 60 days) — "
                                "consider a dealer trade or a targeted incentive." if heavy else "No aged inventory.")),
                    "sources": ["DEALER_Inventory.age_days", "A1 demand (active journeys × builds)", "DEALER_Deals booked"]},
    }


def _trade(c: _Ctx) -> dict[str, Any]:
    from . import inventory as inv_mod
    js = c.rows("DEALER_Journeys", by_market=True)
    vals = {}
    for v in c.all_rows("DEALER_Valuations"):
        cid = v.get("customer_id")
        if cid and (cid not in vals or (v.get("valued_at") or "") > (vals[cid].get("valued_at") or "")):
            vals[cid] = v
    with_trade = [j for j in js if j.get("customer_id") in vals]
    svc_lane = [j for j in js if j.get("source") == "service_lane"]
    equity_mined = sum(max(0.0, _num(vals.get(j.get("customer_id"), {}).get("equity")) or 0) for j in svc_lane)
    disp = inv_mod.trade_dispositions({"is_oem": c.is_oem, "dealer_ids": c.dealers}, c.market, env_prefix=c.env)
    by_decision: dict[str, int] = {}
    for d in disp["dispositions"]:
        k = d.get("decision") or f"pending:{d['recommendation']}"
        by_decision[k] = by_decision.get(k, 0) + 1
    return {
        "kpis": [
            _kpi("Trade capture rate", _rate(len(with_trade), len(js)), "/dealer/trade-in", fmt="pct",
                 source="journeys with a DEALER_Valuations row"),
            _kpi("Service-lane journeys", len(svc_lane), "/dealer/assistant/service", source="DEALER_Journeys.source=service_lane"),
            _kpi("Equity mined (service lane)", round(equity_mined, 2), "/dealer/assistant/service", fmt="money"),
            _kpi("Dispositions pending", disp["pending"], "/dealer/inventory?tab=trades"),
        ],
        "dispositions": [{"decision": k, "count": v} for k, v in sorted(by_decision.items())],
        "insight": {"text": (f"{len(with_trade)} of {len(js)} journeys carry a trade. "
                             f"The service lane has produced {len(svc_lane)} sales journeys"
                             + (f" with positive equity mined" if equity_mined else "") + f". {disp['pending']} trades await a disposition decision."),
                    "sources": ["DEALER_Valuations", "DEALER_Journeys.source", "DEALER_TradeDispositions"]},
    }


def _fi(c: _Ctx) -> dict[str, Any]:
    from . import fni
    # market-wide: this market's stores (desk_kpis with None would span every market)
    stores = c.dealers or {d.get("dealer_id") for d in c.rows("DEALER_Deals") if d.get("dealer_id")}
    k = fni.desk_kpis(stores, c.env)
    mix = sorted(({"code": code, "count": n} for code, n in k["product_mix"].items()), key=lambda r: -r["count"])
    return {
        "currency": k["currency"],
        "kpis": [
            _kpi("Attach rate", k["attach_rate"], "/dealer/fni", fmt="pct", source=k["source_ref"]),
            _kpi("PVR", k["pvr"], "/dealer/fni", fmt="money", source=k["source_ref"]),
            _kpi("Products / deal", k["products_per_deal"], "/dealer/fni"),
            _kpi("In the F&I desk", k["in_desk"], "/dealer/fni"),
        ],
        "product_mix": mix,
        "insight": {"text": (f"Attach rate {round(100 * k['attach_rate'])}% across {k['booked_deals']} booked deals; "
                             f"{labels.fi_product_label(mix[0]['code']) if mix else 'no product'} leads the mix. "
                             + ("Lowest-attach products are where menu coaching pays back first." if mix else ""))
                    if k["attach_rate"] is not None else "No booked deals yet, so attach rate is not computable.",
                    "sources": ["DEALER_Deals booked", "DEALER_FIProducts accepted"]},
    }


def _service(c: _Ctx) -> dict[str, Any]:
    sjs = c.rows("DEALER_ServiceJourneys", by_market=True)
    sj_ids = {s["service_journey_id"] for s in sjs}
    ros = [r for r in c.all_rows("DEALER_RepairOrders") if r.get("service_journey_id") in sj_ids]
    ins = [i for i in c.all_rows("DEALER_Inspections") if i.get("service_journey_id") in sj_ids]
    ests = [e for e in c.all_rows("DEALER_ServiceEstimates") if e.get("service_journey_id") in sj_ids]
    declined = [d for d in c.all_rows("DEALER_DeclinedWork") if d.get("service_journey_id") in sj_ids]
    past_inspect = [s for s in sjs if s.get("stage") in ("DIAGNOSE", "APPROVE", "REPAIR", "READY", "FOLLOW_UP", "COMPLETED")]
    inspected = {i.get("service_journey_id") for i in ins}
    handoffs = [j for j in c.rows("DEALER_Journeys", by_market=True) if j.get("source") == "service_lane"]
    vehicles = c.rows("DEALER_Vehicles")
    serviced_vins = {s.get("vin") for s in sjs if s.get("vin")}
    ro_ids = {r["ro_id"] for r in ros if r.get("ro_id")}
    orders = [o for o in c.all_rows("DEALER_PartsOrders") if o.get("ro_id") in ro_ids]
    # the shared fixed-ops definitions (service/metrics.py): the Service Board and Service Cockpit
    # read the same ones, so the GM, the advisor and the service manager see one number
    m = svc_metrics.summary(sjs, ros, orders, ests)
    return {
        "kpis": [
            _kpi("Open service journeys", m["open_visits"], "/dealer/service", source="DEALER_ServiceJourneys"),
            _kpi("Repair orders", len(ros), "/dealer/service", source="DEALER_RepairOrders"),
            _kpi("MPI completion", _rate(len(inspected & {s['service_journey_id'] for s in past_inspect}), len(past_inspect)),
                 "/dealer/service/cockpit", fmt="pct", source="DEALER_Inspections / journeys past INSPECT"),
            _kpi("Recommended-work close rate", m["close_rate"], "/dealer/service/cockpit", fmt="pct",
                 delta=f"{m['decided_estimates']} decided" if m["decided_estimates"] else "no decided estimates yet",
                 source="DEALER_ServiceEstimates decided, share not declined"),
            _kpi("Parts-hold ROs", m["parts_hold"], "/dealer/service/parts"),
            _kpi("Awaiting approval", m["waiting_approval"], "/dealer/service?filter=waiting_approval"),
            _kpi("Promise at risk", m["at_risk"] + m["past_promise"], "/dealer/service?filter=overdue",
                 delta=f"{m['past_promise']} past · {m['at_risk']} projected late", trend="down" if (m["at_risk"] or m["past_promise"]) else None),
            _kpi("Repurchase handoffs", len(handoffs), "/dealer/assistant/service", source="journeys source=service_lane"),
        ],
        "metrics": m,
        "declined_open": len(declined),
        "retention": {"owned_vehicles": len(vehicles), "serviced": len(serviced_vins & {v.get("vin") for v in vehicles}),
                      "rate": _rate(len(serviced_vins & {v.get("vin") for v in vehicles}), len(vehicles))},
        "insight": {"text": (f"{m['open_visits']} open service journeys, {m['parts_hold']} held for parts, "
                             f"{m['past_promise']} past their promised time and {m['at_risk']} projected to miss it. "
                             "Close rate on recommended work is "
                             + (f"{round(100 * m['close_rate'])}% over {m['decided_estimates']} decided estimates" if m["close_rate"] is not None else "not yet measurable (no estimate has been decided)")
                             + f"; {len(declined)} declined items are queued for follow-up."),
                    "sources": ["DEALER_ServiceJourneys", "DEALER_ServiceEstimates", "DEALER_DeclinedWork"]},
    }


def _cx(c: _Ctx) -> dict[str, Any]:
    sla = c.rows("DEALER_LeadSLA")
    mins = sorted(_num(s.get("response_minutes")) for s in sla if _num(s.get("response_minutes")) is not None)
    breached = sum(1 for s in sla if s.get("breached"))
    cust_ids = {x.get("customer_id") for x in c.rows("DEALER_Customers")}
    msgs = [m for m in c.all_rows("CUST_Messages") if m.get("customer_id") in cust_ids]
    out_n = sum(1 for m in msgs if m.get("direction") == "outbound")
    in_n = sum(1 for m in msgs if m.get("direction") == "inbound")
    csi_target = None
    for p in c.rows("OEM_AccountPlans"):
        for o in p.get("objectives") or []:
            if o.get("kpi") == "csi":
                csi_target = _num(o.get("target"))
    # CSI: the OEM owner-survey roll-up on the store's account (the same figure the CSI alert is about)
    csi_rows = [a.get("csi") for a in c.rows("OEM_Accounts") if isinstance(a.get("csi"), dict) and a.get("csi", {}).get("score") is not None]
    csi = round(sum(float(x["score"]) for x in csi_rows) / len(csi_rows)) if csi_rows else None
    csi_prior = round(sum(float(x.get("prior_month") or x["score"]) for x in csi_rows) / len(csi_rows)) if csi_rows else None
    median = mins[len(mins) // 2] if mins else None
    return {
        "kpis": [
            _kpi("Median lead response (min)", median, "/dealer/leads", source="DEALER_LeadSLA.response_minutes"),
            _kpi("Lead SLA breached", _rate(breached, len(sla)), "/dealer/leads", fmt="pct",
                 trend="down" if breached else None),
            _kpi("Nurture reply rate", _rate(in_n, out_n), "/dealer/journeys", fmt="pct", source="CUST_Messages inbound/outbound"),
            _kpi("CSI", csi, "/oem/alerts" if csi is not None else None,
                 delta=(f"{csi - csi_prior:+d} vs prior month" if csi is not None and csi_prior is not None else None)
                       or (f"target {csi_target}" if csi_target else None),
                 trend=("down" if csi is not None and csi_prior is not None and csi < csi_prior - 10 else None),
                 source="OEM_Accounts.csi (OEM owner-survey roll-up)" if csi is not None else "no survey results on file"),
        ],
        "messages": {"outbound": out_n, "inbound": in_n},
        "insight": {"text": (f"Median first response is {median} minutes with {breached} of {len(sla)} leads breaching SLA. "
                             if mins else "No lead SLA records. ") + (f"CSI is {csi} ({csi - csi_prior:+d} vs prior month)." if csi is not None and csi_prior is not None else "CSI is not shown: no survey results are on file yet."),
                    "sources": ["DEALER_LeadSLA", "CUST_Messages", "OEM_AccountPlans.objectives[csi]"]},
    }


def _alerts(c: _Ctx) -> dict[str, Any]:
    alerts = [a for a in c.rows("OEM_Alerts") if (a.get("market_id") or c.market) == c.market]
    open_a = [a for a in alerts if a.get("status") in (None, "open", "acknowledged")]
    sev = {"high": 0, "medium": 0, "low": 0}
    for a in open_a:
        sev[str(a.get("severity") or "low").lower()] = sev.get(str(a.get("severity") or "low").lower(), 0) + 1
    rank = {"high": 0, "medium": 1, "low": 2}
    return {
        "kpis": [
            _kpi("Open alerts", len(open_a), None, source="OEM_Alerts"),
            _kpi("High", sev.get("high", 0), None, trend="down" if sev.get("high") else None),
            _kpi("Medium", sev.get("medium", 0), None),
            _kpi("Low", sev.get("low", 0), None),
        ],
        "alerts": [{"alert_id": a["alert_id"], "title": a.get("title"), "kind": a.get("kind"), "severity": a.get("severity"),
                    "status": a.get("status") or "open", "opened_at": a.get("opened_at"),
                    "acknowledged_by": a.get("acknowledged_by"), "link": f"/r/alert/{a['alert_id']}",
                    "source_ref": a.get("source_ref")}
                   for a in sorted(open_a, key=lambda a: (rank.get(str(a.get("severity")).lower(), 3), a.get("opened_at") or ""))],
        "insight": {"text": (f"{sev.get('high', 0)} high-severity alerts from your District Manager need a response. "
                             if sev.get("high") else "No high-severity alerts. ")
                            + (f"Oldest open: {sorted(open_a, key=lambda a: a.get('opened_at') or '')[0].get('title')}." if open_a else ""),
                    "sources": ["OEM_Alerts (A6 detect_alerts)"]},
    }


def _comms(c: _Ctx) -> dict[str, Any]:
    districts = {r.get("district_id") for r in c.rows("OEM_Accounts") if r.get("district_id")}
    comms = [m for m in c.all_rows("OEM_Comms")
             if (m.get("market_id") or c.market) == c.market and (not districts or m.get("district_id") in districts)]
    comms.sort(key=lambda m: m.get("sent_at") or "", reverse=True)
    flagged = [m for m in comms if m.get("gr_boundary_rule_id")]
    return {
        "kpis": [
            _kpi("Comms (district)", len(comms), None, source="OEM_Comms"),
            _kpi("Guardrail-flagged", len(flagged), None, trend="down" if flagged else None),
            _kpi("Latest", (comms[0].get("sent_at") or "")[:10] if comms else None, None, fmt="text"),
            _kpi("Channels", len({m.get("channel") for m in comms}), None),
        ],
        "comms": [{"comm_id": m["comm_id"], "subject": m.get("subject"), "channel": m.get("channel"),
                   "sent_at": m.get("sent_at"), "audience": m.get("audience"),
                   "guardrail_rule": m.get("gr_boundary_rule_id"),
                   "body": None if m.get("gr_boundary_rule_id") else m.get("body")} for m in comms[:20]],
        "insight": {"text": f"{len(comms)} district communications"
                            + (f"; {len(flagged)} were withheld because they cross an OEM guardrail "
                               f"({guardrails.rule_label(flagged[0]['gr_boundary_rule_id'], c.market, c.env)})." if flagged else "."),
                    "sources": ["OEM_Comms", "PLAT_ConfigGuardrails"]},
    }


_BUILDERS = {"funnel": _funnel, "sales": _sales, "inventory": _inventory, "trade": _trade, "fi": _fi,
             "service": _service, "cx": _cx, "alerts": _alerts, "comms": _comms}


def _key(scope: dict[str, Any], dealer_id: str | None, market: str) -> tuple:
    dealers, is_oem = _dealer_scope(scope, dealer_id)
    return (tuple(sorted(dealers)), is_oem, market)


def _overlay_digest(tabs: dict[str, Any], scope: dict[str, Any], dealer_id: str | None, market: str,
                    env_prefix: str | None) -> None:
    """Swap each tab's figures-only insight for the one the nightly store digest
    wrote (store_digest.py), when a fresh one is on file for this scope. The
    figures-only text stays alongside it as `figures_only`."""
    from . import store_digest
    key = store_digest.scope_key(scope, dealer_id)
    if not key:
        return
    for t in TABS:
        # today's figures for the tab: a written insight that states any other number is stale
        written = store_digest.stored_insight(market, key, t, env_prefix,
                                              facts=store_digest.tab_facts(t, tabs[t]))
        if written:
            tabs[t]["insight"] = {**written, "figures_only": (tabs[t].get("insight") or {}).get("text")}


def snapshot(scope: dict[str, Any], dealer_id: str | None = None, market_id: str | None = None,
             env_prefix: str | None = None, fresh: bool = False, overlay: bool = True) -> dict[str, Any]:
    """`overlay=False` keeps every tab's figures-only insight (the store digest
    reads those as its facts) and bypasses the cache in both directions."""
    market = market_id or scope.get("market_id") or "us"
    k = _key(scope, dealer_id, market)
    hit = _CACHE.get(k)
    if hit and not fresh and overlay and time.time() - hit[0] < _CACHE_TTL_S:
        return hit[1]
    c = _Ctx(scope, dealer_id, market, env_prefix)
    tabs = {t: _BUILDERS[t](c) for t in TABS}
    if overlay:
        _overlay_digest(tabs, scope, dealer_id, market, env_prefix)
    snap = {"market_id": market, "dealer_scope": sorted(c.dealers) if c.dealers else ("(all)" if c.is_oem else []),
            "materialized_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "tabs": tabs, "tab_order": list(TABS), "tab_labels": TAB_LABELS,
            "source_ref": "StoreCockpit.snapshot (materialized read model; invalidated by §4.6 events)"}
    if overlay:
        _CACHE[k] = (time.time(), snap)
    return snap


def build_tab(scope: dict[str, Any], tab: str, dealer_id: str | None = None, market_id: str | None = None,
              env_prefix: str | None = None) -> dict[str, Any]:
    if tab not in TABS:
        raise ValueError(f"unknown cockpit tab {tab!r} (expected one of {TABS})")
    snap = snapshot(scope, dealer_id, market_id, env_prefix)
    return {"tab": tab, "label": TAB_LABELS[tab], "market_id": snap["market_id"], "dealer_scope": snap["dealer_scope"],
            "materialized_at": snap["materialized_at"], **snap["tabs"][tab]}


def build_cockpit(scope: dict[str, Any], dealer_id: str | None = None,
                  market_id: str | None = None, env_prefix: str | None = None) -> dict[str, Any]:
    """Summary across tabs (first KPIs of each) — the legacy /dealer/cockpit shape."""
    snap = snapshot(scope, dealer_id, market_id, env_prefix)
    return {"market_id": snap["market_id"], "dealer_scope": snap["dealer_scope"],
            "materialized_at": snap["materialized_at"], "tab_order": snap["tab_order"], "tab_labels": TAB_LABELS,
            "tiles": {t: {"kpis": snap["tabs"][t]["kpis"], "insight": snap["tabs"][t]["insight"]} for t in TABS},
            "source_ref": snap["source_ref"]}


def acknowledge_alert(alert_id: str, scope: dict[str, Any], actor: str, note: str | None = None,
                      env_prefix: str | None = None) -> dict[str, Any]:
    a = ddb.get_item("OEM_Alerts", {"alert_id": alert_id}, env_prefix)
    if a is None:
        raise LookupError(f"alert {alert_id} not found")
    if not scope.get("is_oem") and a.get("dealer_id") not in (scope.get("dealer_ids") or set()):
        raise PermissionError("alert outside caller scope")
    a = dict(a)
    if a.get("status") in ("resolved",):
        raise ValueError("alert is already resolved")
    a.update({"status": "acknowledged", "acknowledged_by": actor,
              "acknowledged_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
              "dealer_response": note})
    ddb.put_item("OEM_Alerts", a, env_prefix)
    ev = _bus.emit("alert.acknowledged", {"alert_id": alert_id, "dealer_id": a.get("dealer_id"),
                                          "district_id": a.get("district_id"), "note": note},
                   actor=actor, resource_url=f"/r/alert/{alert_id}", env_prefix=env_prefix)
    return {"alert_id": alert_id, "status": "acknowledged", "bus_event": ev}


def store_campaigns(scope: dict[str, Any], dealer_id: str | None = None, market_id: str | None = None,
                    env_prefix: str | None = None) -> dict[str, Any]:
    """Store-scoped campaign ROI (§5.7 dealer view): the OEM campaigns this store's
    journeys came from, with attributed deliveries counted for this store only.
    Spend is allocated to the store by its share of each campaign's journeys —
    an allocation estimate, flagged as such (the ad platforms report spend per
    campaign, not per dealer)."""
    from ..oem import campaigns as oem_campaigns
    market = market_id or scope.get("market_id") or "us"
    dealers, is_oem = _dealer_scope(scope, dealer_id)
    gross_per = oem_campaigns._ASSUMED_GROSS_PER_DELIVERY
    camps = ddb.scan_filter("OEM_Campaigns", "market_id", market, env_prefix)
    js = ddb.scan_filter("DEALER_Journeys", "market_id", market, env_prefix)
    done = {d.get("journey_id") for d in ddb.scan_all("DEALER_Deliveries", env_prefix) if d.get("status") == "completed"}
    by_camp_all: dict[int, int] = {}
    by_camp_store: dict[int, list[dict[str, Any]]] = {}
    for j in js:
        ck = oem_campaigns._norm_campaign(j.get("campaign_id"))
        if ck is None:
            continue
        by_camp_all[ck] = by_camp_all.get(ck, 0) + 1
        if (is_oem and (not dealers or j.get("dealer_id") in dealers)) or j.get("dealer_id") in dealers:
            by_camp_store.setdefault(ck, []).append(j)
    rows = []
    for c in camps:
        ck = oem_campaigns._norm_campaign(c.get("campaign_id"))
        store_js = by_camp_store.get(ck, [])
        if not store_js:
            continue
        share = len(store_js) / by_camp_all.get(ck, 1)
        # store share of the media spent so far (ROI is on money spent, not the whole flight budget)
        spend = round(oem_campaigns.spend_to_date(c) * share, 2)
        delivered = sum(1 for j in store_js if j["journey_id"] in done or j.get("stage") == "OWN")
        gross = delivered * gross_per
        funnel = {s: sum(1 for j in store_js if j.get("stage") == s) for s in JOURNEY_STAGES}
        rows.append({"campaign_id": c.get("campaign_id"), "name": c.get("name"), "objective": c.get("objective"),
                     "model_id": c.get("model_id"), "journeys": len(store_js), "share_of_campaign": round(share, 3),
                     "allocated_spend": spend, "currency": c.get("currency") or ("EUR" if market == "de" else "USD"), "deliveries": delivered,
                     "cost_per_delivery": round(spend / delivered, 2) if delivered else None,
                     "roi": round((gross - spend) / spend, 2) if spend else None, "funnel": funnel,
                     "link": f"/r/campaign/{c.get('campaign_id')}", "source_ref": c.get("source_ref")})
    rows.sort(key=lambda r: (r["roi"] if r["roi"] is not None else -999), reverse=True)
    total_spend = sum(r["allocated_spend"] for r in rows)
    total_del = sum(r["deliveries"] for r in rows)
    best, worst = (rows[0], rows[-1]) if len(rows) >= 2 else (None, None)
    return {
        "market_id": market, "dealer_scope": sorted(dealers),
        "kpis": [_kpi("Campaigns", len(rows), None), _kpi("Allocated spend", round(total_spend, 2), None, fmt="money"),
                 _kpi("Attributed deliveries", total_del, "/dealer/delivery"),
                 _kpi("Cost / delivery", round(total_spend / total_del, 2) if total_del else None, None, fmt="money")],
        "campaigns": rows,
        # A5's shift recommendation, scoped to this store's rows (§5.7 dealer view).
        "budget_shift": oem_campaigns.recommend_store_budget_shift(rows, market, sorted(dealers),
                                                                   env_prefix=env_prefix),
        "assumptions": {"gross_per_delivery": gross_per,
                        "spend_allocation": "store share of each campaign's journeys × campaign spend to date (estimate)"},
        "insight": {"text": (f"{best['name']} returns best for this store (ROI {best['roi']}); {worst['name']} trails "
                             f"(ROI {worst['roi']}). Share this with your District Manager before co-op planning."
                             if best and worst and best is not worst else
                             f"{len(rows)} campaign(s) brought journeys to this store."),
                    "sources": ["OEM_Campaigns", "DEALER_Journeys.campaign_id", "DEALER_Deliveries"]},
        "source_ref": "StoreCockpit.store_campaigns (A5 ROI filtered to store; allocation estimate)",
    }
