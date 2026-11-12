"""A6 OEM Workspace read models for the Next.js shell (spec §5.9, §5.9 ext).

The Quick-hosted A6 agent / Flows / Research are the Quick App; these are the
main-repo, sandbox-backed views the OEM shell renders today:

  list_accounts(scope)                  -> accounts with target gap + at-risk flags
  account_detail(account_id, scope)     -> account + composite + EV + compliance +
                                           plans + C&I programs + alerts + contact reports
  region_view(scope)                    -> district comparison + HIGH-alert roll-up
  update_alert(alert_id, action, ...)   -> acknowledge / resolve (alert.* events)
  ask(question, view, scope, dealer_id) -> guardrail-checked, cited workspace answer
                                           (the Quick A6 `ask` replaces it when enabled)
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any

from ..common import authz, ddb
from . import planner

_ALERT_ACTIONS = {"acknowledge": ("acknowledged", "alert.acknowledged"),
                  "resolve": ("resolved", "alert.resolved")}


class NotFound(Exception):
    pass


class Forbidden(Exception):
    pass


class InvalidAction(ValueError):
    pass


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _districts(scope: dict[str, Any], env_prefix: str | None) -> set[str] | None:
    node = scope.get("scope_node_id") or "OEM"
    if node == "OEM":
        return None
    return authz.district_ids_under(node, env_prefix)


def _in_scope(scope: dict[str, Any], district_id: str | None, env_prefix: str | None) -> bool:
    ds = _districts(scope, env_prefix)
    return ds is None or district_id in ds


# Dealer "conditions" that are healthy or neutral: a store flagged this way is not at risk
# just because the field is set (a top performer is the opposite of at risk).
_HEALTHY_CONDITIONS = {"top_performer", "normal", "strong_weekend_weak_monday", None, ""}


# One rule for "at risk", everywhere (accounts, region, Today, the account page): a store whose
# sales health is low, or that trails its district's DEALER peers by 5 pts or more. The stored
# `condition` (a seeded archetype like high_days_supply_ev_aging) is a watch note, not the label —
# it made a store +8 pts above peers read "at risk" and one 7 pts below read "on track".
AT_RISK_GAP = -0.05
LOW_SALES_HEALTH = 0.6


def peer_gaps(accounts: list[dict[str, Any]]) -> dict[str, tuple[float | None, float | None]]:
    """account_id -> (district dealer-peer average, gap vs it). Peers are dealer accounts only:
    groups, fleet and prospects are not stores and would skew the average."""
    by_district: dict[str, list[float]] = {}
    for a in accounts:
        sh = _f(a.get("sales_health_index"))
        if sh is not None and a.get("account_type", "dealer") == "dealer":
            by_district.setdefault(a.get("district_id"), []).append(sh)
    out: dict[str, tuple[float | None, float | None]] = {}
    for a in accounts:
        peers = by_district.get(a.get("district_id")) or []
        peer = sum(peers) / len(peers) if peers else None
        sh = _f(a.get("sales_health_index"))
        out[a.get("account_id")] = (peer, round(sh - peer, 3) if sh is not None and peer is not None else None)
    return out


def _at_risk(a: dict[str, Any], gap: float | None = None) -> bool:
    sh = _f(a.get("sales_health_index"))
    return (sh is not None and sh < LOW_SALES_HEALTH) or (gap is not None and gap <= AT_RISK_GAP)


def watch_note(a: dict[str, Any]) -> str | None:
    """The store's flagged condition as words (EV aging, compliance), shown beside the risk label."""
    cond = a.get("condition")
    return None if cond in _HEALTHY_CONDITIONS else str(cond).replace("_", " ")


def list_accounts(scope: dict[str, Any], market: str = "us", env_prefix: str | None = None) -> dict[str, Any]:
    accs = [a for a in ddb.scan_filter("OEM_Accounts", "market_id", market, env_prefix)
            if _in_scope(scope, a.get("district_id"), env_prefix)]
    alerts = [x for x in ddb.scan_filter("OEM_Alerts", "market_id", market, env_prefix) if x.get("status") != "resolved"]
    gaps = peer_gaps(accs)
    rows = []
    for a in accs:
        peer, gap = gaps.get(a["account_id"], (None, None))
        sh = _f(a.get("sales_health_index"))
        open_alerts = [x for x in alerts if a.get("dealer_id") and x.get("dealer_id") == a.get("dealer_id")]
        rows.append({
            "account_id": a["account_id"], "name": a.get("name"), "account_type": a.get("account_type"),
            "dealer_id": a.get("dealer_id"), "district_id": a.get("district_id"), "tier": a.get("tier"),
            "sales_health_index": sh, "service_health_index": _f(a.get("service_health_index")),
            "days_supply": _f(a.get("days_supply")), "condition": a.get("condition"),
            "peer_avg_sales_health": round(peer, 3) if peer is not None else None,
            "target_gap": gap,
            "at_risk": _at_risk(a, gap),
            "watch": watch_note(a),
            "open_alerts": len(open_alerts),
            "high_alerts": sum(1 for x in open_alerts if str(x.get("severity")).lower() == "high"),
            "source_ref": a.get("source_ref"),
        })
    rows.sort(key=lambda r: (not r["at_risk"], r["target_gap"] if r["target_gap"] is not None else 9))
    dealers = [r for r in rows if r["account_type"] == "dealer"]
    return {"market_id": market, "accounts": rows, "count": len(rows),
            # the store counts every screen shows: dealers only (groups, fleet, prospects are listed, not counted)
            "dealers": len(dealers),
            "at_risk": sum(1 for r in dealers if r["at_risk"]),
            "method": (f"target gap = sales health index minus the district's dealer-peer average; at risk = "
                       f"sales health below {LOW_SALES_HEALTH} or {int(-AT_RISK_GAP * 100)}+ pts below peers")}


def account_detail(account_id: str, scope: dict[str, Any], market: str = "us",
                   env_prefix: str | None = None) -> dict[str, Any]:
    a = ddb.get_item("OEM_Accounts", {"account_id": account_id}, env_prefix)
    if a is None:
        raise NotFound(f"account {account_id} not found")
    if not _in_scope(scope, a.get("district_id"), env_prefix):
        raise Forbidden("account outside caller scope")
    dealer = a.get("dealer_id")
    by_dealer = (lambda t: ddb.scan_filter(t, "dealer_id", dealer, env_prefix)) if dealer else (lambda t: [])
    # newest first: a fresh draft (no effective_from yet) is the latest version, not the oldest
    plans = sorted(by_dealer("OEM_AccountPlans"),
                   key=lambda p: (p.get("updated_at") or p.get("created_at") or p.get("effective_from") or "",
                                  int(p.get("version") or 0)),
                   reverse=True)
    from ..common import people
    names = people.directory(env_prefix)
    for p in plans:
        p["dated_at"] = p.get("effective_from") or p.get("updated_at") or p.get("created_at")
        p["author_user_name"] = people.name(p.get("author_user_id"), names)
    reports = sorted(by_dealer("OEM_ContactReports"), key=lambda r: r.get("visit_ts") or "", reverse=True)
    alerts = sorted(by_dealer("OEM_Alerts"), key=lambda x: x.get("opened_at") or "", reverse=True)
    summary = next((r for r in list_accounts(scope, market, env_prefix)["accounts"] if r["account_id"] == account_id), None)
    return {
        "account": a, "summary": summary,
        "composite": planner.compute_composite(account_id, market=market, env_prefix=env_prefix),
        "ev_readiness": planner.get_ev_readiness(account_id, env_prefix=env_prefix) if dealer else None,
        "compliance": planner.get_compliance(account_id, env_prefix=env_prefix) if dealer else None,
        "plans": plans, "ci_programs": by_dealer("OEM_CIPrograms"),
        "alerts": alerts, "contact_reports": reports[:10],
        "source_ref": f"OEM_Accounts:{account_id}",
    }


_VISIT_TYPES = ("in_person", "phone", "video", "email")
_SENTIMENTS = ("positive", "neutral", "negative")
_CONTACT_REPORTS = "OEM_ContactReports"


def _account_for_dealer(dealer_id: str, market: str, env_prefix: str | None) -> dict[str, Any] | None:
    return next((a for a in ddb.scan_filter("OEM_Accounts", "dealer_id", dealer_id, env_prefix)
                 if (a.get("market_id") or market) == market), None)


def _due_at(due: str | None) -> str | None:
    """A follow-up deadline from the form (YYYY-MM-DD) as an end-of-day instant."""
    if not due:
        return None
    s = str(due).strip()
    if len(s) == 10:
        return f"{s}T17:00:00Z"
    try:
        return _dt.datetime.fromisoformat(s.replace("Z", "+00:00")).replace(microsecond=0) \
            .isoformat().replace("+00:00", "Z")
    except ValueError:
        return None


def file_contact_report(payload: dict[str, Any], actor: str, scope: dict[str, Any], market: str = "us",
                        env_prefix: str | None = None) -> dict[str, Any]:
    """File a dealer contact report (§5.9 field tools) into OEM_ContactReports.

    Each follow-up becomes a unified task with its deadline (DEALER_Tasks via the
    platform task API), so "what I promised the dealer" is tracked where every
    other commitment is. Writes a `contact_report.filed` event. The report is
    scoped to the dealer's district: a DM cannot file against another district."""
    import hashlib
    from ..platform import tasks as plat_tasks
    from ..events import bus

    dealer_id = (payload.get("dealer_id") or "").strip()
    summary = (payload.get("summary") or "").strip()
    if not dealer_id:
        raise InvalidAction("dealer_id is required")
    if not summary:
        raise InvalidAction("summary is required")
    visit_type = payload.get("visit_type") or "in_person"
    if visit_type not in _VISIT_TYPES:
        raise InvalidAction(f"visit_type must be one of {_VISIT_TYPES}")
    sentiment = payload.get("sentiment") or "neutral"
    if sentiment not in _SENTIMENTS:
        raise InvalidAction(f"sentiment must be one of {_SENTIMENTS}")
    topics = [str(t).strip() for t in (payload.get("topics") or []) if str(t).strip()]
    if not isinstance(payload.get("followups") or [], list):
        raise InvalidAction("followups must be a list of {text, due}")

    account = _account_for_dealer(dealer_id, market, env_prefix)
    district = (account or {}).get("district_id")
    if account is None:
        raise NotFound(f"no account for dealer {dealer_id} in {market}")
    if not _in_scope(scope, district, env_prefix):
        raise Forbidden("dealer outside caller scope")

    ts = _now()
    visit_ts = payload.get("visit_ts") or ts
    report_id = "CR-" + hashlib.sha1(f"{dealer_id}|{actor}|{visit_ts}|{summary}".encode()).hexdigest()[:12].upper()

    followups, task_ids = [], []
    for f in payload.get("followups") or []:
        text = (f or {}).get("text")
        text = str(text).strip() if text else ""
        if not text:
            continue
        due_at = _due_at((f or {}).get("due"))
        task = plat_tasks.create_task(
            text, actor, created_by=actor, dealer_id=dealer_id, due_at=due_at,
            resource_url=f"/r/account/{account.get('account_id')}", source_agent="A6",
            team="oem_field", payload={"contact_report_id": report_id, "dealer_id": dealer_id},
            env_prefix=env_prefix)
        followups.append({"text": text, "due": (f or {}).get("due"), "due_at": task.get("due_at"),
                          "task_id": task["task_id"]})
        task_ids.append(task["task_id"])

    row = {
        "report_id": report_id, "dealer_id": dealer_id, "district_id": district, "market_id": market,
        "account_id": account.get("account_id"), "author_user_id": actor,
        "visit_type": visit_type, "visit_ts": visit_ts, "sentiment": sentiment,
        "summary": summary, "topics": topics, "next_action": payload.get("next_action"),
        "followups": followups, "followup_task_ids": task_ids,
        "created_at": ts, "source_ref": f"A6 field tools: filed by {actor} (OEM Workspace › Contact Reports)",
    }
    ddb.put_item(_CONTACT_REPORTS, row, env_prefix)
    ev = bus.emit("contact_report.filed",
                  {"report_id": report_id, "dealer_id": dealer_id, "district_id": district,
                   "sentiment": sentiment, "visit_type": visit_type, "followups": len(followups)},
                  actor=actor, resource_url=f"/r/account/{account.get('account_id')}", env_prefix=env_prefix)
    return {**row, "tasks_created": len(task_ids), "bus_event": ev}


_COMMS = "OEM_Comms"


def _dealer_digest(dealer_id: str, market: str, env_prefix: str | None) -> list[str]:
    """The week's open items for one dealer — the same alerts and contact reports
    the Comms digest view consolidates, rebuilt server-side so the stored body is
    grounded in rows rather than in whatever the client posted."""
    items = []
    for a in ddb.scan_filter("OEM_Alerts", "dealer_id", dealer_id, env_prefix):
        if (a.get("market_id") or market) == market and a.get("status") in (None, "open", "acknowledged"):
            items.append(f"{str(a.get('severity') or 'low').upper()}: {a.get('title')}")
    reports = sorted([r for r in ddb.scan_filter(_CONTACT_REPORTS, "dealer_id", dealer_id, env_prefix)
                      if (r.get("market_id") or market) == market],
                     key=lambda r: r.get("visit_ts") or "", reverse=True)[:2]
    for r in reports:
        items.append(f"Contact report ({r.get('sentiment') or 'neutral'}): {r.get('summary')}")
    return items


def forward_comms_digest(payload: dict[str, Any], actor: str, scope: dict[str, Any], market: str = "us",
                         env_prefix: str | None = None) -> dict[str, Any]:
    """Forward the consolidated comms digest to dealers with the DM's comments
    (§5.9 Sales Ops Comms → §5.14 Store Cockpit › OEM Comms).

    Writes one OEM_Comms row per targeted dealer — the row family the dealer's
    Store Cockpit comms tab reads — carrying the DM's comment and that dealer's
    own items. The comment is guardrail-checked first (GR-001..GR-008): a comment
    that crosses an OEM boundary (e.g. instructing retail price) is still
    recorded, tagged with the rule it crossed, and the cockpit withholds its body
    exactly as it does for the seeded boundary bulletins."""
    from ..events import bus
    from . import guardrails as oem_guardrails

    dealers = [str(d).strip() for d in (payload.get("digest") or payload.get("dealers") or []) if str(d).strip()]
    if not dealers:
        raise InvalidAction("digest must list at least one dealer_id")
    comment = (payload.get("comment") or "").strip()
    subject = (payload.get("subject") or "District digest — open items and next steps").strip()

    rule_id = None
    if comment:
        gr = oem_guardrails.check_guardrails(comment, {
            "market_id": market, "user_id": actor, "district_id": scope.get("scope_node_id"),
            "agent_id": "A6", "tool": "forward_comms"}, env_prefix)
        if gr.get("outcome") == "block":
            rule_id = next((v.get("rule_id") for v in gr.get("violations") or []), "GR")

    ts = _now()
    sent, skipped = [], []
    for dealer_id in dealers:
        account = _account_for_dealer(dealer_id, market, env_prefix)
        district = (account or {}).get("district_id")
        if account is None:
            skipped.append({"dealer_id": dealer_id, "reason": "no account in this market"})
            continue
        if not _in_scope(scope, district, env_prefix):
            skipped.append({"dealer_id": dealer_id, "reason": "outside caller scope"})
            continue
        items = _dealer_digest(dealer_id, market, env_prefix)
        body = "\n".join(filter(None, [comment, *(f"• {i}" for i in items)])) or subject
        comm_id = f"COMM-{market.upper()}-FWD-{dealer_id}-{ts.replace(':', '').replace('-', '')}"
        row = {"comm_id": comm_id, "subject": subject, "channel": "digest", "audience": dealer_id,
               "dealer_id": dealer_id, "district_id": district, "market_id": market,
               "sent_at": ts, "sent_by": actor, "body": body, "comment": comment,
               "items": items, "item_count": len(items),
               "gr_boundary_rule_id": rule_id,
               "source_ref": f"A6 forward_comms_digest by {actor} (OEM_Alerts + OEM_ContactReports digest)"}
        ddb.put_item(_COMMS, row, env_prefix)
        bus.emit("notification.requested",
                 {"comm_id": comm_id, "dealer_id": dealer_id, "district_id": district,
                  "kind": "oem_comms_digest", "subject": subject, "items": len(items),
                  "guardrail_rule": rule_id},
                 actor=actor, resource_url=f"/r/dealer/{dealer_id}", env_prefix=env_prefix)
        sent.append({"comm_id": comm_id, "dealer_id": dealer_id, "items": len(items)})

    return {"forwarded": len(sent), "comms": sent, "skipped": skipped,
            "subject": subject, "comment": comment, "sent_at": ts,
            "guardrail_rule": rule_id,
            "note": ("Comment crosses an OEM guardrail — it is recorded and flagged, and each dealer's "
                     "cockpit withholds the body." if rule_id else
                     "Each dealer sees this in Store Cockpit › OEM Comms."),
            "source_ref": "A6 forward_comms_digest -> OEM_Comms (read by the Store Cockpit comms tab)"}


def region_view(scope: dict[str, Any], market: str = "us", env_prefix: str | None = None) -> dict[str, Any]:
    node = scope.get("scope_node_id") or "OEM"
    org = authz.org_nodes(env_prefix, market)
    districts = sorted(authz.district_ids_under(node, env_prefix))
    names = {n["node_id"]: n.get("name") or n["node_id"] for n in org}
    accs = ddb.scan_filter("OEM_Accounts", "market_id", market, env_prefix)
    alerts = [x for x in ddb.scan_filter("OEM_Alerts", "market_id", market, env_prefix) if x.get("status") != "resolved"]
    reports = ddb.scan_filter("OEM_ContactReports", "market_id", market, env_prefix)
    plans = ddb.scan_filter("OEM_AccountPlans", "market_id", market, env_prefix)
    rows = []
    gaps = peer_gaps(accs)
    for d in districts:
        da = [a for a in accs if a.get("district_id") == d and a.get("account_type") == "dealer"]
        sh = [_f(a.get("sales_health_index")) for a in da if _f(a.get("sales_health_index")) is not None]
        ds = [_f(a.get("days_supply")) for a in da if _f(a.get("days_supply")) is not None]
        dal = [x for x in alerts if x.get("district_id") == d]
        rows.append({
            "district_id": d, "name": names.get(d, d), "dealers": len(da),
            "avg_sales_health": round(sum(sh) / len(sh), 3) if sh else None,
            "avg_days_supply": round(sum(ds) / len(ds), 1) if ds else None,
            "dealers_at_risk": sum(1 for a in da if _at_risk(a, gaps.get(a["account_id"], (None, None))[1])),
            "alerts_open": len(dal),
            "alerts_high": sum(1 for x in dal if str(x.get("severity")).lower() == "high"),
            "contact_reports": sum(1 for r in reports if r.get("district_id") == d),
            "action_plans": sum(1 for p in plans if p.get("district_id") == d and p.get("status") != "archived"),
            "plans_published": sum(1 for p in plans if p.get("district_id") == d and p.get("status") == "published"),
        })
    high = sorted([x for x in alerts if x.get("district_id") in districts and str(x.get("severity")).lower() == "high"],
                  key=lambda x: x.get("opened_at") or "", reverse=True)
    return {"market_id": market, "scope_node": node, "scope_name": names.get(node, node),
            "districts": rows, "high_alerts": high[:25],
            "source_ref": "OEM_Accounts + OEM_Alerts + OEM_ContactReports + OEM_AccountPlans by district"}


def conquest_offers(scope: dict[str, Any], market: str = "us", env_prefix: str | None = None) -> dict[str, Any]:
    """Competitor offers near the district's dealers (§3.6.1 get_competitive_offers).

    Derived from rows that exist: the open `conquest_pressure` alerts name the
    stores under pressure, and the competitor makes are the non-brand makes
    actually owned in each store's customer base (the same signal the conquest
    audience uses), counted per store. Our side of the comparison is the market's
    live incentive stack.

    What is NOT derivable here is stated rather than invented: a competitor's
    advertised APR/lease/cash, its ad spend and impressions need the
    market-intelligence and ad-platform connectors, so those fields come back
    null and are listed in `data_gaps`. With no conquest signal in scope this
    returns an empty, well-shaped list — never a 404."""
    brand = None
    m = ddb.get_item("OEM_Markets", {"market_id": market}, env_prefix)
    if m:
        brand = ((m.get("brand") or {}).get("name") or "").split()[0].lower() or None

    alerts = [a for a in ddb.scan_filter("OEM_Alerts", "market_id", market, env_prefix)
              if a.get("kind") == "conquest_pressure" and a.get("status") != "resolved"
              and _in_scope(scope, a.get("district_id"), env_prefix)]
    org = {n["node_id"]: n.get("name") or n["node_id"] for n in authz.org_nodes(env_prefix, market)}
    incentives = [i for i in ddb.scan_filter("OEM_Incentives", "market_id", market, env_prefix)
                  if str(i.get("status") or "active").startswith("active")]
    best = max(incentives, key=lambda i: float(i.get("amount_usd") or i.get("amount") or 0), default=None)
    our_offer = (f"{(best.get('amount_usd') or best.get('amount'))} — {best.get('name')}"
                 if best else None)

    # competitor makes owned in each pressured store's customer base
    by_dealer: dict[str, dict[str, int]] = {}
    for c in ddb.scan_filter("DEALER_Customers", "market_id", market, env_prefix):
        make = ((c.get("current_vehicle") or {}).get("make") or "").strip()
        dealer = c.get("dealer_id")
        if not make or not dealer:
            continue
        if brand and brand in make.lower():
            continue
        by_dealer.setdefault(dealer, {})[make] = by_dealer.setdefault(dealer, {}).get(make, 0) + 1

    offers = []
    for a in sorted(alerts, key=lambda x: ({"high": 0, "medium": 1, "low": 2}.get(str(x.get("severity")).lower(), 3),
                                           x.get("opened_at") or "")):
        dealer = a.get("dealer_id")
        makes = sorted((by_dealer.get(dealer) or {}).items(), key=lambda kv: -kv[1])[:3]
        for make, owners in makes:
            offers.append({
                "offer_id": f"CQ-{market.upper()}-{dealer}-{make.upper().replace(' ', '')}",
                "competitor_dealer": f"{make} franchise in {org.get(dealer, dealer)}'s PMA",
                "make": make,
                "nearest_store": org.get(dealer, dealer),
                "dealer_id": dealer, "district_id": a.get("district_id"),
                "owners_in_base": owners,
                "conquest_signal": {"alert_id": a.get("alert_id"), "severity": a.get("severity"),
                                    "title": a.get("title"), "opened_at": a.get("opened_at")},
                "our_offer": our_offer,
                # needs the market-intel / ad-platform connectors — never guessed
                "advertised_offer": None, "est_monthly_spend": None, "impressions": None,
                "vs_our_offer": None, "expires_at": None,
                "source_ref": (f"OEM_Alerts:{a.get('alert_id')} + DEALER_Customers.current_vehicle.make"
                               f" ({owners} owners)"),
            })
    return {"market_id": market, "scope_node": scope.get("scope_node_id") or "OEM",
            "offers": offers, "count": len(offers),
            "dealers_under_pressure": sorted({a.get("dealer_id") for a in alerts if a.get("dealer_id")}),
            "our_offer": our_offer,
            "data_gaps": ["advertised_offer", "est_monthly_spend", "impressions", "vs_our_offer", "expires_at"],
            "note": ("Competitor makes and owner counts are derived from the customer base and the open "
                     "conquest alerts. Advertised offer, spend, impressions and expiry need the "
                     "market-intelligence and ad-platform connectors — reported as null until those are live."
                     if offers else
                     "No open conquest pressure in scope, so there is nothing to compare — competitor rows "
                     "appear here when a conquest alert opens for one of your stores."),
            "source_ref": "A6 conquest offers (OEM_Alerts conquest_pressure + DEALER_Customers "
                          "competitor makes + OEM_Incentives)"}


def update_alert(alert_id: str, action: str, actor: str, scope: dict[str, Any], note: str | None = None,
                 env_prefix: str | None = None) -> dict[str, Any]:
    from ..events import bus
    if action not in _ALERT_ACTIONS:
        raise InvalidAction(f"action must be one of {sorted(_ALERT_ACTIONS)}")
    a = ddb.get_item("OEM_Alerts", {"alert_id": alert_id}, env_prefix)
    if a is None:
        raise NotFound(f"alert {alert_id} not found")
    if not _in_scope(scope, a.get("district_id"), env_prefix):
        raise Forbidden("alert outside caller scope")
    if a.get("status") == "resolved":
        raise InvalidAction(f"alert {alert_id} is already resolved")
    status, event_type = _ALERT_ACTIONS[action]
    ts = _now()
    a = dict(a)
    a["status"] = status
    a[f"{status}_at"] = ts
    a[f"{status}_by"] = actor
    if note:
        a["notes"] = list(a.get("notes") or []) + [{"by": actor, "at": ts, "note": note}]
    ddb.put_item("OEM_Alerts", a, env_prefix)
    ev = bus.emit(event_type, {"alert_id": alert_id, "dealer_id": a.get("dealer_id"),
                               "district_id": a.get("district_id"), "severity": a.get("severity"),
                               "kind": a.get("kind")},
                  actor=actor, resource_url=f"/r/alert/{alert_id}", env_prefix=env_prefix)
    return {**a, "bus_event": ev}


def ask(question: str, scope: dict[str, Any], view: str | None = None, dealer_id: str | None = None,
        market: str = "us", env_prefix: str | None = None) -> dict[str, Any]:
    """Answer an OEM workspace question with grounded Bedrock generation.

    Deterministic read models assemble question-specific facts and citations;
    the model phrases the answer from those facts. The deterministic text is a
    fallback only when Bedrock is unavailable or the grounding guardrail
    intervenes, never the normal deployed response path.
    """
    from ..platform import generate, knowledge
    from . import alerts as oem_alerts, guardrails

    gr = guardrails.check_guardrails(question, {"market_id": market, "user_id": scope.get("user_id"),
                                                "district_id": scope.get("scope_node_id"),
                                                "agent_id": "A6", "tool": "ask"})
    if gr.get("outcome") == "block":
        return {"answer": gr.get("safe_alternative") or "I can't help with that request.",
                "guardrail": [v.get("rule_id") for v in gr.get("violations") or []],
                "citations": [], "engine": "deterministic_guardrail", "model": None}

    q = question.lower()
    account_result = list_accounts(scope, market, env_prefix)
    accounts = account_result["accounts"]
    if dealer_id:
        accounts = [a for a in accounts if a.get("dealer_id") == dealer_id] or accounts
    dealers = [a for a in accounts if a.get("account_type") == "dealer"]
    citations: list[dict[str, str]] = []
    facts: list[str] = [
        f"Workspace view={view or 'workspace'}; market={market}; accounts in scope={len(accounts)}.",
        f"Peer-gap method: {account_result['method']}.",
    ]
    fallback_parts: list[str] = []
    intent = "account_overview"

    peer_terms = ("furthest", "below", "peer", "gap", "worst")
    # An account named by id, or an EV-readiness ask: the read models already answer both (account
    # detail + planner.get_ev_readiness), but no intent branch surfaced them, so the model was left
    # with the overview facts and said it had no data (2026-10-27 eval, a6-ev).
    named = [a for a in accounts if a.get("account_id") and a["account_id"].lower() in q]
    ev_ask = "ev" in re.findall(r"[a-z]+", q) or "electric" in q or "charger" in q
    if named or (ev_ask and any(t in q for t in ("ready", "readiness", "certif", "charg", "train"))):
        intent = "ev_readiness" if ev_ask else "account_detail"
        from . import planner
        for a in (named or dealers)[:5]:
            facts.append(f"Account {a['account_id']} is {a['name']} (dealer {a.get('dealer_id')}, tier {a.get('tier')}): "
                         f"sales health={a.get('sales_health_index')}; gap vs peers={a.get('target_gap')}; "
                         f"days supply={a.get('days_supply')}; open alerts={a.get('open_alerts')}; at risk={a.get('at_risk')}.")
            citations.append({"label": a["account_id"], "ref": a.get("source_ref") or f"OEM_Accounts:{a['account_id']}"})
            if ev_ask and a.get("account_type") == "dealer":
                ev = planner.get_ev_readiness(a["account_id"], env_prefix=env_prefix)
                areas = "; ".join(f"{x.get('area')}={x.get('status')}" for x in ev.get("ev_areas") or []) or "no EV compliance areas on file"
                facts.append(f"EV readiness for {a['account_id']} ({a['name']}): "
                             f"{'certified' if ev.get('ev_certified') else 'not certified'}; areas: {areas}.")
                citations.append({"label": f"ev_readiness:{a['account_id']}", "ref": ev.get("source_ref") or "OEM_Compliance"})
                fallback_parts.append(f"{a['name']} ({a['account_id']}) is {'EV certified' if ev.get('ev_certified') else 'not yet EV certified'}"
                                      f" — {areas}.")
        if not ev_ask:
            fallback_parts.append("; ".join(
                f"{a['name']} ({a['account_id']}): sales health {a.get('sales_health_index')}, "
                f"{a.get('open_alerts')} open alerts" for a in (named or dealers)[:5]) + ".")
    elif any(term in q for term in peer_terms):
        intent = "peer_gap_ranking"
        ranked = sorted((a for a in dealers if a.get("target_gap") is not None),
                        key=lambda a: a["target_gap"])[:5]
        for rank, a in enumerate(ranked, 1):
            facts.append(
                f"Peer-gap rank {rank}: {a['name']} ({a.get('dealer_id') or a['account_id']}) "
                f"is {abs(a['target_gap']):.2f} below its district peer average; "
                f"sales health={a.get('sales_health_index')}; peer average={a.get('peer_avg_sales_health')}."
            )
            citations.append({"label": a["account_id"],
                              "ref": a.get("source_ref") or f"OEM_Accounts:{a['account_id']}"})
        fallback_parts.append(
            "Furthest below peers: " + "; ".join(
                f"{a['name']} ({abs(a['target_gap']):.2f} below peer average)" for a in ranked
            ) + "." if ranked else "No dealer peer-gap data is available in this scope."
        )
    elif any(term in q for term in ("risk", "at-risk", "why", "attention", "priorit")):
        intent = "risk_explanation"
        at_risk = sorted((a for a in dealers if a.get("at_risk")),
                         key=lambda a: (a.get("sales_health_index") is None,
                                        a.get("sales_health_index") or 9))[:5]
        risk_summaries: list[str] = []
        for a in at_risk:
            reasons = []
            if a.get("condition"):
                reasons.append(str(a["condition"]).replace("_", " "))
            if a.get("sales_health_index") is not None and a["sales_health_index"] < 0.6:
                reasons.append(f"sales health {a['sales_health_index']:.2f}")
            if a.get("target_gap") is not None and a["target_gap"] < 0:
                reasons.append(f"{abs(a['target_gap']):.2f} below peers")
            if a.get("high_alerts"):
                reasons.append(f"{a['high_alerts']} HIGH alert(s)")
            if a.get("days_supply") is not None:
                reasons.append(f"{a['days_supply']:.0f} days supply")
            why = ", ".join(reasons) or "account risk flag is set"
            facts.append(f"At-risk dealer: {a['name']} ({a.get('dealer_id')}): {why}.")
            risk_summaries.append(f"{a['name']} ({why})")
            citations.append({"label": a["account_id"],
                              "ref": a.get("source_ref") or f"OEM_Accounts:{a['account_id']}"})
        fallback_parts.append(
            "Dealers at risk and why: " + "; ".join(risk_summaries)
            if risk_summaries else "No dealers are currently flagged at risk in this scope."
        )
    elif any(term in q for term in ("alert", "urgent")):
        intent = "alert_priority"
        alert_result = oem_alerts.build_alerts(scope, market_id=market, env_prefix=env_prefix)
        facts.append(f"Open alert counts: HIGH={alert_result['counts']['HIGH']}, "
                     f"MEDIUM={alert_result['counts']['MEDIUM']}, LOW={alert_result['counts']['LOW']}.")
        high = alert_result["by_severity"]["HIGH"][:5]
        facts.extend(f"HIGH alert: dealer={a.get('dealer_id')}; title={a.get('title')}; kind={a.get('kind')}."
                     for a in high)
        citations.append({"label": "OEM_Alerts", "ref": alert_result["source_ref"]})
        fallback_parts.append("Start with: " + "; ".join(
            f"{a.get('dealer_id')} — {a.get('title')}" for a in high) + "." if high
            else "There are no open HIGH alerts in this scope.")
    elif any(term in q for term in ("supply", "inventory", "days")):
        intent = "inventory_supply"
        high_supply = sorted((a for a in dealers if a.get("days_supply") is not None),
                             key=lambda a: -a["days_supply"])[:5]
        facts.extend(f"Inventory: {a['name']} ({a.get('dealer_id')}) has {a['days_supply']:.0f} days supply."
                     for a in high_supply)
        citations.extend({"label": a["account_id"],
                          "ref": a.get("source_ref") or f"OEM_Accounts:{a['account_id']}"}
                         for a in high_supply)
        fallback_parts.append("Highest days supply: " + ", ".join(
            f"{a['name']} ({a['days_supply']:.0f} days)" for a in high_supply) + "." if high_supply
            else "No days-supply data is available in this scope.")
    else:
        for a in dealers[:8]:
            facts.append(f"Dealer account: {a['name']}; sales health={a.get('sales_health_index')}; "
                         f"gap vs peers={a.get('target_gap')}; days supply={a.get('days_supply')}; "
                         f"open alerts={a.get('open_alerts')}; at risk={a.get('at_risk')}.")
            citations.append({"label": a["account_id"],
                              "ref": a.get("source_ref") or f"OEM_Accounts:{a['account_id']}"})
        fallback_parts.append("I could not answer that from the scoped account facts. Ask about peer gaps, "
                              "risk reasons, alerts, or days supply.")

    kb = knowledge.get_knowledge_base().retrieve(question, k=2)
    passages = [str(p.get("passage") or "") for p in kb.get("passages") or [] if p.get("passage")]
    for p in kb.get("passages") or []:
        ref = p.get("source_ref") or p.get("id") or "knowledge"
        citations.append({"label": ref, "ref": ref})

    fallback = " ".join(fallback_parts)
    generated = generate.ground(
        question, facts, passages,
        persona=f"an OEM field-sales assistant answering the {view or 'workspace'} view",
        fallback=fallback,
    )
    model_guardrail = generated.get("guardrail") or {}
    guardrail_badge = None
    if model_guardrail.get("intervened"):
        guardrail_badge = model_guardrail.get("matched") or ["BedrockGuardrail"]

    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for citation in citations:
        key = (citation.get("label") or "source", citation.get("ref") or "")
        if key not in seen:
            seen.add(key)
            deduped.append(citation)
    return {"answer": generated["text"], "citations": deduped,
            "guardrail": guardrail_badge, "model_guardrail": model_guardrail,
            "view": view, "intent": intent, "engine": generated["engine"],
            "model": generated.get("model"), "retrieval": kb.get("engine"),
            "via": f"{generated['engine']} over scoped OEM read models and Knowledge Base"}
