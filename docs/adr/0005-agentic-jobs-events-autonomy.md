# ADR 0005 — Agentic behaviour: scheduled jobs, event subscribers, autonomy levels

**Status:** Accepted (2026-10-08); amended 2026-10-22. Twelve jobs deployed, each
run for `us` and `de`; eleven ship enabled; all triggers emitted.

## Context

"Agentic" in this system means the software acts without a click: on a clock,
on an event, or by planning its own tool use (ADR 0002). The specification
defines the triggers (§4.10.2) and an autonomy model (§4.10.1). The first build
had four schedules (three shipped disabled) and Play subscribers for five event
types, of which only one event was ever emitted — so the Plays never fired on
their own.

## Decision

1. **Every agentic job is one row in one table.** Four places must agree:
   `app/infra/deploy_l6.JOBS` lists job name, Lambda, schedule and whether it
   ships enabled; `app/services/api/job_handler.JOBS` maps the name to the
   function; `app/infra/iam_roles.WORKER_FUNCTIONS` grants the log groups; and
   the agent registry's `jobs` (`app/agentcore/registry.py`) names the owner
   agent and its payload keys. The deployer checks at import time that they
   agree, including the registry. Adding a job is adding a row to each.
2. **Every subscribed event has an emitter.** The code that observes the
   condition emits the event once (idempotent per entity), and the bus
   subscriber runs the Play.
3. **Writers ship disabled; environments opt in.** `deploy_l6 --enable-all`
   turns every schedule on (dev/demo), `--disable-writers` turns the writers
   off again; a redeploy never changes an operator's choice.

   *Amended 2026-10-22 (agent-panel honesty audit, user decision):* every job
   the console's agent bar shows as "Scheduled" now ships **enabled**, for both
   markets, because the bar must not claim a schedule that never fires; a
   redeploy re-enables them. Rule 4 keeps the safety this rule bought: they run
   at the tenant's published autonomy level, `recommend` for all of them today,
   so they write cards, leads and drafts and send nothing to a customer. Only
   `autonomy-promotion` (not on the bar) still ships disabled and keeps an
   operator's choice; `--disable-writers` quiets every writer until the next
   deploy. Two jobs were added: `predictive-scan` (A7, nightly) and
   `store-digest` (A5/A7, nightly, the one job that writes with Bedrock, so it
   runs in its Lambda in `aws` mode).
4. **Every effect is gated by a per-action autonomy level** read from
   `PLAT_ConfigAutonomy` at run time: `off · recommend · act_with_approval ·
   act_and_notify · autonomous`. At `recommend` (the default) a job writes a
   recommendation card for a human; at `act_and_notify`/`autonomous` it executes
   (sends, books). Nine actions have hard ceilings no configuration can raise:
   budget shifts and plan publishing stop at `act_with_approval`; incentive
   allocation and estimate decisions stop at `recommend`.

## The jobs

| Job | Agent | Schedule (UTC) | What it does at `recommend` |
|---|---|---|---|
| `equity-monitor` | A4 | nightly 06:00 | `equity_flag` cards; emits `equity.crossed_positive` (first sight) and `lease.end_window_entered` |
| `nurture-tick` | A2 | every 6 h | due nurture touches → `nurture_touch` cards (sends at execute levels) |
| `stall-sweep` | A2 | daily 05:30 | flags journeys idle past `RETAIL_OPS_STALL_SLA_DAYS` once; emits `journey.stalled` → Callback Brief play |
| `rescore-profiles` | A1 | daily 05:45 | re-ranks shopping journeys; emits `discovery.match_found` when the top trim is in stock |
| `service-lane-monitor` | A7 | nightly 07:00 | FOLLOW_UP journeys crossing into positive equity → `service_handoff` cards |
| `shop-load` | A7 | 05:00 local per market (10:00 `us`, 03:00 `de`) | dispatch technician, stage parts, re-forecast promise, pre-present small lines → cards |
| `service-followup-tick` | A7 | hourly | due declined-work reminders → cards (sends at execute levels); `service_due` cards for vehicles due within `RETAIL_OPS_DUE_SERVICE_DAYS` |
| `nightly-attribution` | A5 | nightly 08:00 | ROI recompute; budget-shift approval drafts when lift clears the threshold |
| `district-cycle` | A6 | Mon 05:00 | composites, alerts, draft account plans for red dealers |
| `predictive-scan` | A7 | 06:15 | predictive part-wear leads + customer offers, up to 10 per store |
| `store-digest` | A5 / A7 | 06:30 | Bedrock-written Store Cockpit tab insights and Service Cockpit insight (`store_insight` cards) |
| `autonomy-promotion` | platform | Mon 06:00 (ships disabled) | outcome stats → autonomy level proposals |

## Options considered

* **One Lambda for all jobs.** Fewer resources, but one failing job's errors
  and throttles hide the others; one Lambda per job keeps CloudWatch alarms
  per job.
* **Plays triggered only on demand.** Simpler, but then nothing is proactive —
  the whole point of §4.10.2.
* **Enable writers by default.** Convenient in dev, dangerous elsewhere; opt-in
  per environment keeps the safe default. (Superseded 2026-10-22 for the jobs on the agent bar: see
  the amendment to rule 3.)

## Implementation

```
app/infra/deploy_l6.py                       JOBS table, --enable-all / --disable-writers / --run-once <job>
app/services/api/job_handler.py              JOBS map; POST /jobs/{job} runs one on demand (apply/limit)
app/services/journeys/stall_sweep.py         journey.stalled
app/services/agents/rescore.py               discovery.match_found
app/services/agents/equity_monitor.py        equity.crossed_positive, lease.end_window_entered
app/services/service/followup_tick.py        hourly reminders + due-service cards
app/services/oem/district_cycle.py           district-cycle, nightly-attribution
app/services/service/predictive.py           predictive-scan
app/services/dealer/store_digest.py          store-digest (Bedrock; read by the cockpits)
app/services/events/bus.py                   catalogue (80 types), emit(), subscribers, Play triggers
app/services/journeys/plays.py               trigger → Play mapping (matches the seed's play tags)
app/services/common/autonomy_config.py       levels, WRITE/EXECUTE sets, HARD_CEILINGS, active_level()
tests/app/test_agentic_triggers.py
```

The nine agent-owned jobs (every job above except `district-cycle`,
`store-digest` and `autonomy-promotion`) run in the owner agent's AgentCore runtime (ADR 0009): the
job Lambda stays the schedule target and sends the runtime a `kind: job` request
when `RETAIL_OPS_AGENT_RUNTIMES` names the owner, and runs the job in-process
otherwise. `district-cycle` (A6, Quick-side) and `autonomy-promotion` (platform)
stay in their Lambdas. When a runtime call was sent but no answer came back
(`lost`), the job Lambda returns `unknown_after_send` instead of raising, so
Lambda's async retry cannot start an overlapping run, and prints one
`runtime_job_unknown` line that the job's CloudWatch alarm counts.

Events are written **after** the state change and published to EventBridge
(`RETAIL_OPS_EVENT_BUS_LIVE=1`) so subscribers run in the subscriber Lambda with a
dead-letter queue; locally the same subscribers run in-process.

## Consequences

* The dev account runs every schedule except `autonomy-promotion`, at `recommend`:
  cards, leads, drafts and events, no customer messages. Expect a burst of `journey.stalled` events on the first run
  over the synthetic data (most journeys are idle).
* The seed packs carry no "Recommend Vehicle" or "Full Prep" play, so the
  `discovery.match_found` and `service.handoff_to_sales` subscribers run and find
  no play until those are added to the generator.
* Jobs do not call the model, except `store-digest`, which exists to write with
  Bedrock (its own guardrail, ADR 0003) and is the only job Lambda in aws mode. The
  other job Lambdas run without `INTEGRATION_MODE=aws`, a runtime runs every job under
  `platform.mode.override("local")`, and the three helpers that used to call
  Bedrock whatever the mode (`equity._explain`, `discovery._rationale`,
  `campaigns._insight`) now return templated text outside AWS mode (ADR 0009). A
  nightly sweep must not make one model call per journey.

## Revisit when

* An action is ready to move up the ladder: use the outcome loop
  (`GET /recommendations/stats`) as the evidence, change the level in the Admin
  console (versioned, reversible), observe, repeat.
* A job needs the model (e.g. a drafted message per card): add a bounded,
  per-card generation step with the guardrail, not a sweep-wide one.
