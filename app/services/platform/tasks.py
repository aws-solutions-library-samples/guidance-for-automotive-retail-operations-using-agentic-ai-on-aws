"""§5.18.4 — Unified Tasks (`DEALER_Tasks`): one API every agent and screen uses.

  create_task(title, assignee, ...)   -> open task with SLA (due_at) + source link
  list_tasks(user_id | scope, status) -> the caller's queue, overdue flagged
  update_task(task_id, ...)           -> assign / complete / reopen
  sweep_overdue()                     -> emit task.overdue once per overdue task

Every task carries a `resource_url` (§5.18.2) so the shell can deep-link to the
thing the task is about, and every transition emits a `task.*` event (§4.6) so
the Notification Hub and activity feed react without polling.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
from typing import Any

from ..common import ddb

_TABLE = "DEALER_Tasks"
STATUSES = ("open", "in_progress", "done", "cancelled")
_DEFAULT_SLA_HOURS = 48


class NotFound(Exception):
    pass


class Forbidden(Exception):
    pass


class InvalidTransition(Exception):
    pass


def _now_dt() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def _iso(d: _dt.datetime) -> str:
    return d.isoformat().replace("+00:00", "Z")


def _parse(ts: str | None) -> _dt.datetime | None:
    if not ts:
        return None
    try:
        return _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _emit(event_type: str, row: dict[str, Any], env_prefix: str | None) -> dict[str, Any]:
    from ..events import bus
    return bus.emit(event_type, {k: row.get(k) for k in (
        "task_id", "title", "assigned_user_id", "journey_id", "service_journey_id",
        "dealer_id", "status", "due_at", "source_agent")},
        resource_url=row.get("resource_url"), env_prefix=env_prefix)


def create_task(title: str, assignee: str, *, created_by: str,
                journey_id: str | None = None, service_journey_id: str | None = None,
                dealer_id: str | None = None, due_at: str | None = None,
                sla_hours: float | None = None, resource_url: str | None = None,
                source_agent: str | None = None, task_id: str | None = None,
                payload: dict[str, Any] | None = None, team: str | None = None,
                handoff_id: str | None = None,
                env_prefix: str | None = None) -> dict[str, Any]:
    if not title or not assignee:
        raise ValueError("a task needs a title and an assignee")
    now = _now_dt()
    due = due_at or _iso(now + _dt.timedelta(hours=sla_hours or _DEFAULT_SLA_HOURS))
    tid = task_id or "TSK-" + hashlib.sha1(
        f"{title}|{assignee}|{journey_id}|{service_journey_id}|{_iso(now)}".encode()).hexdigest()[:12]
    if resource_url is None:
        from ..common import resource_url as _ru
        if service_journey_id:
            resource_url = _ru.resource_url("service_journey", service_journey_id)
        elif journey_id:
            resource_url = _ru.resource_url("journey", journey_id)
    row = {
        "task_id": tid, "title": title, "assigned_user_id": assignee,
        "journey_id": journey_id, "service_journey_id": service_journey_id,
        "dealer_id": dealer_id, "status": "open", "due_at": due,
        "sla_due_at": due,   # SLA mirror of due_at (passthrough for SLA-aware views)
        "created_at": _iso(now), "created_by": created_by, "updated_at": _iso(now),
        "resource_url": resource_url, "source_agent": source_agent,
        "payload": payload or {}, "overdue_notified": False,
        "source_ref": f"tasks.create_task:{source_agent or created_by}",
    }
    if team:
        row["team"] = team
    if handoff_id:
        row["handoff_id"] = handoff_id
    ddb.put_item(_TABLE, row, env_prefix)
    row["bus_event"] = _emit("task.created", row, env_prefix)
    return row


def _decorate(row: dict[str, Any], now: _dt.datetime) -> dict[str, Any]:
    due = _parse(row.get("due_at"))
    out = dict(row)
    out["overdue"] = bool(due and due < now and row.get("status") in ("open", "in_progress"))
    # passthrough fields where the data exists: sla_due_at mirrors due_at;
    # team / handoff_id surface from the row or its payload (no invention).
    out["sla_due_at"] = row.get("sla_due_at") or row.get("due_at")
    payload = row.get("payload") or {}
    if row.get("team") is None and payload.get("team"):
        out["team"] = payload["team"]
    if row.get("handoff_id") is None and payload.get("handoff_id"):
        out["handoff_id"] = payload["handoff_id"]
    return out


def list_tasks(*, user_id: str | None = None, dealer_ids: set[str] | None = None,
               status: str | None = None, env_prefix: str | None = None) -> dict[str, Any]:
    """The caller's own tasks (user_id), or every task in a set of dealers."""
    if user_id:
        rows = ddb.scan_filter(_TABLE, "assigned_user_id", user_id, env_prefix)
    else:
        rows = [r for r in ddb.scan_all(_TABLE, env_prefix)
                if dealer_ids is None or r.get("dealer_id") in dealer_ids]
    if status:
        rows = [r for r in rows if r.get("status") == status]
    now = _now_dt()
    items = sorted((_decorate(r, now) for r in rows),
                   key=lambda r: (not r["overdue"], r.get("due_at") or ""))
    from ..common import people
    names = people.directory(env_prefix)
    for t in items:   # names for the screen; the stored ids are unchanged
        t["assigned_user_name"] = people.name(t.get("assigned_user_id"), names)
        t["created_by_name"] = people.name(t.get("created_by"), names)
    return {"count": len(items), "overdue": sum(1 for r in items if r["overdue"]), "tasks": items}


def get_task(task_id: str, env_prefix: str | None = None) -> dict[str, Any]:
    row = ddb.get_item(_TABLE, {"task_id": task_id}, env_prefix)
    if row is None:
        raise NotFound(f"task {task_id} not found")
    return row


def task_view(task_id: str, env_prefix: str | None = None) -> dict[str, Any]:
    """One task as the API returns it — the stored row plus the computed fields
    (`overdue`, and the `sla_due_at` / `team` / `handoff_id` passthroughs). Kept
    separate from `get_task` so a write path never persists a computed field."""
    return _decorate(get_task(task_id, env_prefix), _now_dt())


def update_task(task_id: str, *, actor: str, status: str | None = None,
                assignee: str | None = None, note: str | None = None,
                may_manage: bool = False, env_prefix: str | None = None) -> dict[str, Any]:
    """Assign, progress, complete, or cancel a task.

    Only the assignee may progress/complete their own task; reassignment and
    cancellation need `may_manage` (a manager role, decided by the caller)."""
    row = dict(get_task(task_id, env_prefix))
    is_owner = row.get("assigned_user_id") == actor
    events: list[str] = []
    if assignee and assignee != row.get("assigned_user_id"):
        if not (may_manage or is_owner):
            raise Forbidden("only the assignee or a manager may reassign a task")
        row["assigned_user_id"] = assignee
        events.append("task.assigned")
    if status:
        if status not in STATUSES:
            raise InvalidTransition(f"status must be one of {STATUSES}")
        if status == "cancelled" and not may_manage:
            raise Forbidden("only a manager may cancel a task")
        if status in ("in_progress", "done") and not (is_owner or may_manage):
            raise Forbidden("only the assignee may progress or complete a task")
        if row.get("status") in ("done", "cancelled") and status != "open":
            raise InvalidTransition(f"task is {row['status']}; reopen it first")
        row["status"] = status
        if status == "done":
            row["completed_at"] = _iso(_now_dt())
            row["completed_by"] = actor
            events.append("task.completed")
    if note:
        row.setdefault("notes", [])
        row["notes"] = list(row["notes"]) + [{"by": actor, "at": _iso(_now_dt()), "note": note}]
    row["updated_at"] = _iso(_now_dt())
    ddb.put_item(_TABLE, row, env_prefix)
    row["bus_events"] = [_emit(e, row, env_prefix) for e in events]
    return row


def sweep_overdue(env_prefix: str | None = None) -> dict[str, Any]:
    """Emit task.overdue once per task past due (escalation hook for the Hub)."""
    now = _now_dt()
    flagged = []
    for r in ddb.scan_all(_TABLE, env_prefix):
        due = _parse(r.get("due_at"))
        if due and due < now and r.get("status") in ("open", "in_progress") \
                and not r.get("overdue_notified"):
            r = dict(r)
            r["overdue_notified"] = True
            ddb.put_item(_TABLE, r, env_prefix)
            _emit("task.overdue", r, env_prefix)
            flagged.append(r["task_id"])
    return {"overdue_flagged": flagged, "count": len(flagged)}
