"""Agent-bar automations that run on a user's click, by API route.

Every API request writes one PLAT_AgentTraces span (handler.lambda_handler). For
the routes below — the on-demand automations the console's agent bar lists — a
successful request (status < 400) writes that span as the owning agent's run
instead of a plain access-layer (`L4`) row: `agent_id` is the agent and `tool`
the automation's run name, so the bar's "last ran" (GET /traces/latest, and
/traces?journey_id= for journey routes) reads real runs. A refused or failed
request stays an `L4` row: it is not a run.

  match(method, path) -> AutomationRun | None

The run names are what `runs` in app/console/src/lib/agents.ts lists; keep the
two in step (tests/app/test_agent_panel_honesty.py checks every name there is
written somewhere).
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class AutomationRun:
    agent_id: str
    tool: str
    journey_id: str | None = None
    service_journey_id: str | None = None
    lead_id: str | None = None


# (method, path pattern, agent, run name, what group 1 is: "journey" | "service_journey" | "lead" | None)
_ROUTES: tuple[tuple[str, str, str, str, str | None], ...] = (
    # Journey Agent (A2)
    ("POST", r"^/journeys/([^/]+)/outreach$", "A2", "a2.send_outreach", "journey"),
    ("POST", r"^/journeys/([^/]+)/book-test-drive$", "A2", "a2.book_test_drive", "journey"),
    ("POST", r"^/journeys/bulk/nudge$", "A2", "a2.bulk_nudge", None),
    ("POST", r"^/assistant/shift-list/publish$", "A2", "a2.publish_shift_list", None),
    ("POST", r"^/leads/([^/]+)/appointment$", "A2", "a2.book_appointment", "lead"),
    ("POST", r"^/leads/([^/]+)/assign$", "A2", "a2.assign_lead", "lead"),
    ("PATCH", r"^/leads/([^/]+)/assign$", "A2", "a2.assign_lead", "lead"),
    ("POST", r"^/deliveries/([^/]+)/message$", "A2", "a2.delivery_day_message", "journey"),
    ("POST", r"^/deliveries/([^/]+)/deliver$", "A2", "a2.mark_delivered", "journey"),
    ("POST", r"^/config/nurture/preview$", "A2", "a2.preview_policy", None),
    # Sales Agent (A3)
    ("GET", r"^/leads/([^/]+)/script$", "A3", "a3.draft_first_response", "lead"),
    ("POST", r"^/leads/([^/]+)/respond$", "A3", "a3.send_first_response", "lead"),
    ("POST", r"^/fni/deals/([^/]+)/lender-decision$", "A3", "a3.request_lender_decision", None),
    # Service Agent (A7)
    ("POST", r"^/service/predictive/run$", "A7", "a7.predictive_scan", None),
    ("POST", r"^/service/estimates/re-present$", "A7", "a7.re_present_estimates", None),
    ("POST", r"^/service/ros/([^/]+)/estimate$", "A7", "a7.build_estimate", None),
    ("POST", r"^/service/ros/([^/]+)/inspection$", "A7", "a7.submit_inspection", None),
    ("POST", r"^/service/([^/]+)/handoff-to-sales$", "A7", "a7.handoff_to_sales", "service_journey"),
    # Field Sales Agent (A6)
    ("POST", r"^/oem/ask$", "A6", "a6.oem_ask", None),
    ("POST", r"^/oem/accounts/([^/]+)/draft-plan$", "A6", "a6.draft_account_plan", None),
    ("POST", r"^/accounts/([^/]+)/plans/([^/]+)/generate$", "A6", "a6.draft_account_plan", None),
    ("POST", r"^/oem/comms/forward$", "A6", "a6.forward_comms", None),
)
_COMPILED = tuple((m, re.compile(p), a, t, k) for m, p, a, t, k in _ROUTES)

RUN_NAMES: frozenset[str] = frozenset(t for _m, _p, _a, t, _k in _ROUTES)


def match(method: str, path: str) -> AutomationRun | None:
    """The automation run a request is, or None for every other route."""
    for m, rx, agent, tool, kind in _COMPILED:
        if m != method:
            continue
        hit = rx.match(path)
        if hit:
            ident = hit.group(1) if kind and hit.lastindex else None
            return AutomationRun(agent, tool,
                                 journey_id=ident if kind == "journey" else None,
                                 service_journey_id=ident if kind == "service_journey" else None,
                                 lead_id=ident if kind == "lead" else None)
    return None
