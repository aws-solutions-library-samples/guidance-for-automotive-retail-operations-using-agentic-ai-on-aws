"""A6 — AI Alerts feed (OEM workspace + Store Cockpit), spec §5.9.

The HIGH/MEDIUM/LOW alert feed the district manager sees, scoped to their org
node (district → its dealers; region → all districts; national → all). Alerts
are the read surface of A6 detect_alerts (the district health monitor writes
OEM_Alerts rows; this presents the open ones by severity).

  build_alerts(scope, market, status?) -> alerts grouped by severity + counts

Grounding: alerts come from OEM_Alerts rows (real district-health signals);
severity/kind/status are stored facts. Scope is enforced by district subtree,
same as the OEM Today dashboard.
"""
from __future__ import annotations

from typing import Any

from ..common import authz, ddb

_ALERTS = "OEM_Alerts"
_SEVERITIES = ("HIGH", "MEDIUM", "LOW")


def build_alerts(scope: dict[str, Any], market_id: str | None = None,
                 status: str = "open", env_prefix: str | None = None) -> dict[str, Any]:
    """The AI alerts feed for the OEM caller's district scope (spec §5.9).

    status: 'open' (default), 'resolved', or 'all'. Grouped by severity, newest
    first, scoped to the districts under the caller's org node."""
    market = market_id or scope.get("market_id") or "us"
    node = scope.get("scope_node_id") or "OEM"
    districts = authz.district_ids_under(node, env_prefix)
    national = (node == "OEM") or not districts

    rows = ddb.scan_filter(_ALERTS, "market_id", market, env_prefix)
    if not national:
        rows = [a for a in rows if a.get("district_id") in districts]
    if status != "all":
        want_open = (status == "open")
        rows = [a for a in rows if (a.get("status") != "resolved") == want_open]

    def _sev(a):
        s = str(a.get("severity") or "").upper()
        return s if s in _SEVERITIES else "LOW"

    groups: dict[str, list[dict[str, Any]]] = {s: [] for s in _SEVERITIES}
    for a in rows:
        groups[_sev(a)].append({
            "alert_id": a.get("alert_id"), "severity": _sev(a), "kind": a.get("kind"),
            "title": a.get("title"), "dealer_id": a.get("dealer_id"),
            "district_id": a.get("district_id"), "status": a.get("status"),
            "opened_at": a.get("opened_at"), "resolved_at": a.get("resolved_at"),
        })
    for s in groups:
        groups[s].sort(key=lambda a: a.get("opened_at") or "", reverse=True)

    # kind breakdown for the district-health summary
    kinds: dict[str, int] = {}
    for a in rows:
        k = a.get("kind") or "other"
        kinds[k] = kinds.get(k, 0) + 1

    return {
        "market_id": market,
        "scope_node": node,
        "national": national,
        "status_filter": status,
        "severities": list(_SEVERITIES),
        "by_severity": groups,
        "counts": {s: len(groups[s]) for s in _SEVERITIES},
        "total": sum(len(v) for v in groups.values()),
        "by_kind": dict(sorted(kinds.items(), key=lambda kv: kv[1], reverse=True)),
        "source_ref": "A6.detect_alerts feed (OEM_Alerts; district-scoped)",
    }
