---
name: agentic-delivery-model-selection
description: >
  Reasoning skill for choosing the right AWS delivery model per agentic use case and persona, and for
  composing several models into one coherent system, based on the automotive retail and service reference
  architecture. Use when deciding between a custom agent on Amazon Bedrock AgentCore, an AgentCore
  real-time streaming path, an AWS Quick agent / Flows / Index / Research, Quick or QuickSight analytics,
  deterministic read models, brand customer web, or an outreach provider; when designing autonomy levels
  and hard rules for proactive agents; or when reviewing a proposal that puts every use case on one stack.
  Triggers include "AgentCore vs Quick", "AWS Quick agent", "Quick Flows", "Quick Index", "Quick Research",
  "Strands agent", "AgentCore Gateway", "AgentCore Memory", "Code Interpreter", "supervisor agent",
  "multi-agent", "delivery model", "which stack", "autonomy level", "human in the loop", "act with approval",
  "hard rules", "guardrails", "event-driven agent", "scheduled agent", "proactive agent", "outcome loop",
  "recommendation logging", "persona-based architecture".
usage: Invoke when selecting or reviewing the technology stack for an agentic use case, composing AgentCore and AWS Quick into one solution, or designing autonomy and control for proactive agents.
version: 1.0.0
tags: [skill, category:reasoning, aws-architecture, agentic, automotive]
---

# Agentic Delivery-Model Selection — Reasoning Skill

## When to Use

- A solution has several personas who work differently (a technician on a tablet, a district manager reading a weekly package, a customer approving on a phone, an advisor mid-conversation) and one stack will not fit them all.
- A proposal must be reviewed for stack fit, composition, and control before build.
- Agents are meant to act proactively (on events and schedules) and the customer needs a way to govern that.

## Framework Selection

Apply the rules in order; the **first matching rule decides** the primary delivery model for the use case. Secondary models may attach for specific paths.

```
R1  Stateful, long-running orchestration over a journey (owns a state machine), tools into operational
    systems, schedules and events            → Custom agent on Amazon Bedrock AgentCore (Strands; Runtime + Memory + Gateway + Code Interpreter)
R2  Sub-2-second streaming on a live conversation
                                             → AgentCore real-time path, even inside a Quick-hosted use case; it asks Quick for content
R3  Document-style generation and cited Q&A over enterprise content for a knowledge worker who lives in chat and documents
                                             → AWS Quick: agent + Flows + Index (+ Research for external synthesis)
R4  Analytics, dashboards, natural-language questions on a lake
                                             → AWS Quick / QuickSight; AgentCore only for the traceable, approvable recommendation step
R5  Deterministic boards, queues, worksheets, approvals
                                             → Next.js + Lambda read models; agents pre-compute into the tables the screen reads
R6  Customer self-service on brand web or mobile
                                             → Customer web calling the same AgentCore agents; customers never see Quick
R7  Voice or telephony as a channel          → OutreachProvider interface (SES/SNS default; Amazon Connect optional)
```

## Response Format

- Lead with the delivery model per use case and the one-line reason (the rule that fired), then the composition points that make the pieces one system (≤5 sentences).
- Then: use-case → persona → rule → primary model → secondary model table → composition checklist → autonomy and hard-rule plan → anti-patterns found.
- The rule ladder and decision logic are for internal reasoning only; do not narrate them. Present decisions, reasons, and consequences.

## Core Procedure

1. **List use cases as (persona, job, access pattern, latency need, state need, content need).** A use case with two access patterns (advisor in chat and advisor on the floor) is two rows.
2. **Run each row through R1–R7** and record the rule that fired. If none fires, the use case is under-specified; go back to the persona.
3. **Attach secondary models** only for concrete paths: a Quick-hosted assistant gets an AgentCore streaming path for the live floor; an AgentCore service agent uses Quick Index for procedures and TSBs; a Quick analytics use case uses AgentCore for `recommend_*` tools.
4. **Choose supervisors by state ownership, not by org chart.** One supervisor per state machine (the purchase journey, the service journey). Supervisors call specialists as tools or agent-to-agent; a supervisor never drives another supervisor, they meet through events.
5. **Decide where content is authored once.** One suggestion engine per domain: the real-time path asks the Quick agent for suggestion content; the service agent cites indexed procedures rather than generating repair advice.
6. **Design the composition layer before the agents:**
   - one sign-in and one scope model (staff pool with native and federated login; Identity Center federation for Quick embedding; separate customer pool) driving API authorization, search filters, and autonomy scope (Quick content is shared by Quick group and not row-scoped);
   - one shell (app switcher, scope, search, tasks, notifications) wrapping custom apps and Quick embeds, with a resource-URL contract for deep links across stacks;
   - one event bus with a schema registry; Quick-hosted agents publish through a Gateway Lambda;
   - one agent bridge (AgentCore Gateway) exposing Quick agents and Flows as tools;
   - one trace (`correlation_id`) across API, agents, Gateway, Quick, and events;
   - market and autonomy as configuration that applies across all surfaces.
7. **Register every effectful agent action** (message, booking, dispatch, order, document, data change) with an `action_id`, a default autonomy level, and a constraints schema (max amount, channels, quiet hours, daily cap).
8. **Define the autonomy ladder:** `observe` → `recommend` → `act_with_approval` → `act_and_notify`, configurable per action per tenant, dealer, or role; versioned with publish and rollback.
9. **Name the hard rules and put them above configuration:** consent and quiet hours; regulated-advice guardrails (for OEM field sales: no pricing, allocation, or franchise-law guidance); customer owns approvals; no automatic spend or allocation; data masking by role; safety certifications for hazardous work. The editor rejects attempts to raise these.
10. **Set pilot defaults conservatively:** everything at `recommend` for an observation period (14 days), then per-action defaults; raise autonomy on evidence, not on enthusiasm.
11. **Make agents proactive by subscription, not by prompt.** Each agent has an explicit table of events and schedules it reacts to (inventory or incentive changes, repair-order events, nightly equity, 05:00 shop load, weekly district cycle). Anything not in the table does not trigger the agent.
12. **Close the outcome loop.** Every recommendation from any agent is logged as shown → accepted → converted → value, with the autonomy level in force. Agents rank alternatives from these aggregates; the control panel shows acceptance and conversion per action so humans can raise or lower autonomy on evidence.
13. **Keep agents out of the render path.** Boards and cockpits read pre-computed tables; agents write to them on events. Page load never invokes an agent.
14. **Keep customers on the brand.** Customer-facing surfaces call the same agents through the API layer; what the customer approves is what the advisor presented. Quick is a staff tool.
15. **Put computation in a sandbox.** Financial and estimate math runs in Code Interpreter with inputs, code, and result retained; the agent quotes the result.
16. **Ground every figure.** No number without a tool result and a `source_ref`. This is a design constraint, not a prompt instruction.
17. **Write acceptance criteria that start with no user action** for every proactive behaviour ("at 05:00 with 12 booked repair orders and nobody logged in…"). If it cannot be tested that way, it is not agentic.
18. **Review for the anti-patterns** (table below) and reject any that appear.

## Reference Tables

| Persona and access pattern | Rule | Primary | Secondary |
|---|---|---|---|
| Customer on brand web / phone: shop, chat, approve, book | R6 | Customer web on AgentCore agents | OutreachProvider for messages (R7) |
| Purchase-journey owner (system, schedules, events) | R1 | AgentCore supervisor | Gateway → Quick for content |
| Advisor in chat asking "what should I say" | R3 | Quick agent + Flows + Index | — |
| Advisor on the floor mid-conversation | R2 | AgentCore streaming path | Asks the Quick agent for content |
| Trade / equity, continuous monitoring | R1 | AgentCore | Read-model desk (R5) |
| Marketing analyst: ROI, dashboards, budget shift | R4 | Quick / QuickSight | AgentCore `recommend_budget_shift` |
| District manager: weekly package, chat, dictation | R3 | Quick Flows + Index + Research | Gateway when a supervisor needs a brief |
| Service-journey owner; technician tablet; parts | R1 | AgentCore supervisor | Quick Index for procedures; read-model board (R5); customer web for approval (R6) |
| Cockpits, boards, desks | R5 | Next.js + Lambda read models | QuickSight tiles (R4) |

| Autonomy level | Human sees |
|---|---|
| observe | Trace only |
| recommend | Card or task with reasoning; human acts |
| act_with_approval | Prepared payload as an approval or one-tap confirm |
| act_and_notify | Executed action with notification and undo where possible |

| Anti-pattern | Why it fails | Rule violated |
|---|---|---|
| Document generation for a knowledge worker built as a custom agent | Re-implements Quick's index, citations, flows | R3 |
| Live-floor assistance inside Quick | Cannot stream at sub-2 s | R2 |
| Quick in front of customers | Brand and licensing; customers need the brand surface | R6 |
| Agent computes dashboard tiles at page load | Latency, cost, non-determinism | R5 |
| One autonomy setting for the whole system | Stores need per-action control | Step 8 |
| Hard rules stored as configuration | Someone will raise them | Step 9 |
| A2A calls between two supervisors | Couples two state machines; use events | Step 4 |
| Numbers in prose without a tool result | Fabrication risk; unauditable | Step 16 |

## Worked Example

Use case: "Help the service advisor sell more recommended work." Decomposed into rows: (a) advisor asks for a talk track in chat → R3 → Quick agent over indexed procedures and past outcomes; (b) technician submits an inspection and the system must build an estimate the customer can approve on their phone → R1 for the service supervisor (state machine BOOK → FOLLOW_UP, Code Interpreter for totals), R6 for the customer approval surface, R5 for the advisor's estimate screen; (c) declined work should be re-presented at 30/60/90 days → subscription on a schedule, autonomy `act_and_notify` with consent as a hard rule; (d) the service manager wants presentation and approval rates → R4 QuickSight over the lake. Composition: one sign-in, technician masking at the API, estimate events on the bus, every presented line logged as a recommendation, one correlation id from inspection to approval. Acceptance: "with 12 booked ROs and nobody logged in, at 05:00 the lane is dispatched, parts staged, and briefs prepared."

## Common Mistakes

1. **Wrong:** Choosing the stack by team preference ("we know Quick") or by vendor pressure.
   **Right:** Run the persona and access pattern through R1–R7.
   **Why:** The persona's way of working is the requirement; the stack follows.

2. **Wrong:** One supervisor agent for everything.
   **Right:** One supervisor per state machine; they meet through events.
   **Why:** Different owners, cadences, and systems of record; a single supervisor becomes a monolith.

3. **Wrong:** Building the real-time floor path inside the Quick agent.
   **Right:** AgentCore streaming path that asks Quick for content.
   **Why:** Streaming latency and one suggestion engine.

4. **Wrong:** Rebuilding document generation and citations on AgentCore for a chat-and-documents persona.
   **Right:** Quick agent, Flows, Index, Research.
   **Why:** You would re-implement Quick, worse.

5. **Wrong:** Letting agents render dashboards on page load.
   **Right:** Agents pre-compute into read models; screens read tables.
   **Why:** Milliseconds, determinism, cost.

6. **Wrong:** Exposing Quick to customers to save a front end.
   **Right:** Brand customer web on the same agents.
   **Why:** Customers need the brand; Quick is a staff tool.

7. **Wrong:** One global "autonomy on/off".
   **Right:** Per-action levels per dealer, versioned.
   **Why:** A store may want parts staged automatically but every customer message reviewed.

8. **Wrong:** Storing consent, guardrails, or approval ownership as configurable settings.
   **Right:** Hard rules above configuration; the editor rejects attempts to raise them.
   **Why:** Someone will set them to `act_and_notify`.

9. **Wrong:** "Proactive" agents driven by a prompt that says "be proactive".
   **Right:** Explicit event and schedule subscriptions per agent.
   **Why:** Untestable behaviour is not behaviour.

10. **Wrong:** Logging outcomes only for one agent's suggestions.
    **Right:** Every recommendation from every agent in one table with the autonomy level in force.
    **Why:** You cannot raise autonomy on evidence you did not collect.

11. **Wrong:** Two supervisors calling each other's tools.
    **Right:** Events between supervisors; tools only from a supervisor to its specialists.
    **Why:** Direct calls couple state machines that must evolve independently.

12. **Wrong:** Acceptance criteria that all begin with a user click.
    **Right:** At least some begin with a clock or an event and end with visible agent work.
    **Why:** Otherwise nothing proves the system acts on its own.

## When to Escalate to a Human

- Regulatory scope (consumer finance, franchise law, data residency) that changes which rules are hard.
- Licensing or cost constraints that would push a use case to a model the rules do not select; record the deviation and the reason.
- Any request to make a hard rule configurable.

## References

- Intelligent Sales & Service Orchestration requirements spec v1.3 §2.2.1 (use-case classification), §2.2.2 (delivery-model selection R1–R7 and composition rules), §4.10 (agentic operating model), §11 criteria 31–37
- `docs/specs/retail-ops-architecture.md` §2, §3, §13 (agentic operating model diagram)
- Amazon Bedrock AgentCore, AWS Quick, Strands Agents SDK documentation for current service capabilities
