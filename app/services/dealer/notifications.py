"""Notification Hub (spec §5.18.4).

Producers raise notification events; the hub applies per-role rules
(PLAT_ConfigNotifications, versioned) to route each event to the users in the
matching roles, writing an in-shell DEALER_Notifications row per recipient. The
shell renders these as the bell + inbox.

  get_inbox(user_id)              -> that user's notifications + unread count
  mark_read(notification_id, uid) -> mark one read (self only)
  mark_all_read(uid)              -> mark every unread notification read (self only)
  notify(event, ...)              -> route an event to matching-role users (producer)

Grounding: rules come from the ACTIVE PLAT_ConfigNotifications version; recipients
are the real users holding the matched roles within the event's dealer/scope.
Nothing is delivered outside the configured rule.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from ..common import ddb

_NOTIFS = "DEALER_Notifications"
_CONFIG = "PLAT_ConfigNotifications"
_USERS = "PLAT_Users"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class NotFound(Exception):
    pass


class Forbidden(Exception):
    pass


def get_inbox(user_id: str, unread_only: bool = False, limit: int = 50,
              env_prefix: str | None = None) -> dict[str, Any]:
    """The user's notification inbox, newest first, with the unread count."""
    rows = ddb.scan_filter(_NOTIFS, "user_id", user_id, env_prefix)
    rows.sort(key=lambda n: n.get("created_at") or "", reverse=True)
    unread = sum(1 for n in rows if not n.get("read"))
    if unread_only:
        rows = [n for n in rows if not n.get("read")]
    return {
        "user_id": user_id,
        "unread_count": unread,
        "total": len(rows),
        "notifications": [{"notification_id": n.get("notification_id"), "event": n.get("event"),
                           "journey_id": n.get("journey_id"), "read": bool(n.get("read")),
                           "read_at": n.get("read_at"),
                           "created_at": n.get("created_at"), "title": n.get("title"),
                           "body": n.get("body"), "severity": n.get("severity"),
                           "channels": n.get("channels"),
                           "resource_url": _resource_url(n)} for n in rows[:limit]],
        "source_ref": "NotificationHub.get_inbox",
    }


def mark_read(notification_id: str, user_id: str, env_prefix: str | None = None) -> dict[str, Any]:
    """Mark a notification read — only the owner may (self-scope)."""
    n = ddb.get_item(_NOTIFS, {"notification_id": notification_id}, env_prefix)
    if n is None:
        raise NotFound(f"notification {notification_id} not found")
    if n.get("user_id") != user_id:
        raise Forbidden("not your notification")
    n = dict(n)
    n["read"] = True
    n["read_at"] = _now()
    ddb.put_item(_NOTIFS, n, env_prefix)
    return {"notification_id": notification_id, "read": True}


def _resource_url(n: dict[str, Any]) -> str | None:
    """Canonical deep link for a notification (§5.18.2) — never a screen path."""
    from ..common import resource_url as ru
    if n.get("resource_url") and ru.is_canonical(n["resource_url"]):
        return n["resource_url"]
    for attr, rtype in (("service_journey_id", "service_journey"), ("journey_id", "journey"),
                        ("alert_id", "alert"), ("task_id", "task"), ("approval_id", "approval")):
        if n.get(attr):
            return ru.resource_url(rtype, n[attr])
    return None


def mark_all_read(user_id: str, env_prefix: str | None = None) -> dict[str, Any]:
    """Mark all of the caller's unread notifications read (self-scope)."""
    ts = _now()
    marked = 0
    for n in ddb.scan_filter(_NOTIFS, "user_id", user_id, env_prefix):
        if not n.get("read"):
            n = dict(n)
            n["read"] = True
            n["read_at"] = ts
            ddb.put_item(_NOTIFS, n, env_prefix)
            marked += 1
    return {"user_id": user_id, "marked": marked}


def _active_rules(market: str, env_prefix: str | None) -> list[dict[str, Any]]:
    versions = ddb.query_hash(_CONFIG, "item_key", f"notifications#{market}", env_prefix)
    if not versions:
        # some deployments key differently; fall back to any config row
        versions = ddb.scan_all(_CONFIG, env_prefix)
    if not versions:
        return []
    active = [v for v in versions if v.get("is_active")]
    chosen = (active or sorted(versions, key=lambda v: v.get("version") or 0, reverse=True))[0]
    return chosen.get("rules") or []


def notify(event: str, journey_id: str | None = None, dealer_id: str | None = None,
           market: str = "us", title: str | None = None, severity: str = "MEDIUM",
           env_prefix: str | None = None) -> dict[str, Any]:
    """Route a notification event to matching-role users (the producer path).

    Looks up the event's rule (roles + channels) in the active config, resolves
    the users holding those roles within the dealer (or market), and writes one
    in-shell DEALER_Notifications row per recipient. Returns who was notified."""
    rules = _active_rules(market, env_prefix)
    rule = next((r for r in rules if r.get("event") == event), None)
    if rule is None:
        return {"event": event, "delivered": 0, "recipients": [],
                "note": f"no notification rule for event {event!r}"}

    roles = set(rule.get("roles") or [])
    channels = rule.get("channels") or ["push"]

    # resolve recipients: users whose group is in `roles`, scoped to dealer if given
    users = ddb.scan_filter(_USERS, "dealer_id", dealer_id, env_prefix) if dealer_id \
        else [u for u in ddb.scan_all(_USERS, env_prefix) if (u.get("market_id") == market)]
    recipients = [u for u in users if roles & set(u.get("groups") or [])]

    ts = _now()
    written = []
    for u in recipients:
        uid = u.get("user_id")
        nid = f"NTF-{event.replace('.', '_')}-{uid}-{int(_dt.datetime.now(_dt.timezone.utc).timestamp()*1000)}"
        ddb.put_item(_NOTIFS, {
            "notification_id": nid, "user_id": uid, "event": event,
            "journey_id": journey_id, "title": title or event, "severity": severity,
            "channels": channels, "read": False, "created_at": ts,
            "source_ref": "NotificationHub.notify",
        }, env_prefix)
        written.append(uid)

    return {
        "event": event, "delivered": len(written), "recipients": written,
        "channels": channels, "roles": sorted(roles),
        "source_ref": "NotificationHub.notify (PLAT_ConfigNotifications rules)",
    }
