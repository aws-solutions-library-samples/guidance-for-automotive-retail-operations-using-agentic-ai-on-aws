#!/usr/bin/env python3
"""L6 deploy via boto3: the agentic scheduler layer.

One Lambda per agentic job (all on the scoped worker role, all running
app.services.api.job_handler) and one EventBridge Scheduler schedule per job
(two for shop-load, whose run time is local: one per market). Every schedule
runs its job for each market in MARKETS. The table `JOBS` below is the single
source of truth; add a row to add a job.

  job                    schedule                              default state
  equity-monitor         cron(0 6 * * ? *)   nightly 06:00 UTC  ENABLED
  nurture-tick           rate(6 hours)                          ENABLED (writer)
  stall-sweep            cron(30 5 * * ? *)  daily 05:30 UTC    ENABLED (writer)
  rescore-profiles       cron(45 5 * * ? *)  daily 05:45 UTC    ENABLED (writer)
  service-lane-monitor   cron(0 7 * * ? *)   nightly 07:00 UTC  ENABLED (writer)
  shop-load              us cron(0 10 * * ? *), de cron(0 3 * * ? *) — 05:00 local  ENABLED (writer)
  service-followup-tick  rate(1 hour)                           ENABLED (writer)
  predictive-scan        cron(15 6 * * ? *)  nightly 06:15 UTC  ENABLED (writer)
  nightly-attribution    cron(0 8 * * ? *)   nightly 08:00 UTC  ENABLED (writer)
  district-cycle         cron(0 5 ? * MON *) weekly Mon 05:00   ENABLED (writer)
  store-digest           cron(30 6 * * ? *)  nightly 06:30 UTC  ENABLED (writer, Bedrock)
  autonomy-promotion     cron(0 6 ? * MON *) weekly Mon 06:00   DISABLED (writer)

Every job the console's agent bar shows as "Scheduled" ships ENABLED (user
decision 2026-10-22, the agent-panel honesty audit): an automation the bar says
runs on a clock must run on one. They run at the autonomy level the tenant has
published (today `recommend` for every one of them: they write recommendation
cards, leads and drafts, not customer messages). Enabled jobs are re-enabled on
every deploy; autonomy-promotion is not on the bar, ships DISABLED and keeps
whatever state an operator set (preserve_state). `--enable-all` also enables
autonomy-promotion; `--disable-writers` disables every writer for this deploy.

This is what makes the system agentic: the schedule is the caller, no user in
the loop.

The agent-owned jobs run in their owner's AgentCore runtime when
agentcore-config.json names it (each job Lambda gets a one-agent runtime map,
ADR 0009) and make no model call there (D6d); district-cycle, store-digest and
autonomy-promotion always run in-process. store-digest is the one job that
writes with Amazon Bedrock, so its Lambda alone gets INTEGRATION_MODE=aws
(MODEL_JOBS).

Idempotent. Dry-run by default; --apply to create/update; --delete to tear down;
--run-once <job> to invoke a deployed job immediately. `--disable-agents
A4|all` / `--enable-agents A4|all` (dry run unless --apply) change only
RETAIL_OPS_AGENT_RUNTIME_DISABLE on those agents' job Lambdas (merge-safe).
"""
from __future__ import annotations

import argparse
import io
import json
import time
import zipfile
from pathlib import Path

try:
    from app.agentcore import registry
    from app.infra import deploy_l4, iam_roles, naming
except ImportError:
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.agentcore import registry
    from app.infra import deploy_l4, iam_roles, naming

ROOT = Path(__file__).resolve().parents[2]
REGION = "us-east-1"
EXEC_ROLE = iam_roles.ROLE_WORKER                    # per-function worker role (scoped)
SCHED_ROLE = naming.name("scheduler-role")
_n = naming.name                                   # retail-ops-<env>-<suffix>
RUNTIME = "python3.12"
BEDROCK_MODEL = "us.anthropic.claude-opus-4-5-20251101-v1:0"

# (job, lambda name, schedule name, schedule expression, description, ships enabled?)
JOBS: list[tuple[str, str, str, str, str, bool]] = [
    ("equity-monitor", _n("equity-monitor"), _n("equity-monitor-nightly"),
     "cron(0 6 * * ? *)", "Nightly A4 trade-equity monitor (agentic).", True),
    ("nurture-tick", _n("nurture-tick"), _n("nurture-tick"),
     "rate(6 hours)", "A2 automated nurture cadence sweep (agentic).", True),
    ("stall-sweep", _n("stall-sweep"), _n("stall-sweep"),
     "cron(30 5 * * ? *)", "A2 stalled-journey sweep -> journey.stalled (agentic).", True),
    ("rescore-profiles", _n("rescore-profiles"), _n("rescore-profiles"),
     "cron(45 5 * * ? *)", "A1 proactive re-rank -> discovery.match_found (agentic).", True),
    ("service-lane-monitor", _n("service-lane-monitor"), _n("service-lane-monitor"),
     "cron(0 7 * * ? *)", "A7 service-lane repurchase/upgrade monitor (agentic).", True),
    ("shop-load", _n("shop-load"), _n("shop-load"),
     "cron(0 10 * * ? *)", "A7 05:00 morning shop-load — dispatch/stage-parts/reforecast (agentic).", True),
    ("service-followup-tick", _n("service-followup-tick"), _n("service-followup-tick"),
     "rate(1 hour)", "A7 hourly declined-work reminders + due-service cards (agentic).", True),
    ("predictive-scan", _n("predictive-scan"), _n("predictive-scan"),
     "cron(15 6 * * ? *)", "A7 nightly predictive part watch -> Lead Desk leads + offers (agentic).", True),
    ("nightly-attribution", _n("nightly-attribution"), _n("nightly-attribution"),
     "cron(0 8 * * ? *)", "A5 nightly ROI attribution -> budget-shift approval drafts (agentic).", True),
    ("district-cycle", _n("district-cycle"), _n("district-cycle"),
     "cron(0 5 ? * MON *)", "A6 weekly district cycle — composites, alerts, draft plans (agentic).", True),
    ("store-digest", _n("store-digest"), _n("store-digest"),
     "cron(30 6 * * ? *)", "A5/A7 nightly cockpit insights written with Amazon Bedrock (agentic).", True),
    ("autonomy-promotion", _n("autonomy-promotion"), _n("autonomy-promotion"),
     "cron(0 6 ? * MON *)", "Weekly autonomy ladder loop — outcome stats -> level proposals.", False),
]
JOB_FN = {j[0]: j[1] for j in JOBS}
# Markets every schedule runs (OEM_Markets). A job whose run time is LOCAL gets one
# schedule per market instead: shop-load is the 05:00-local morning work.
MARKETS = ("us", "de")
LOCAL_TIME_SCHEDULES: dict[str, dict[str, str]] = {
    "shop-load": {"us": "cron(0 10 * * ? *)", "de": "cron(0 3 * * ? *)"},
}
# Jobs whose Lambda may call Amazon Bedrock (INTEGRATION_MODE=aws). Every other
# job Lambda runs without it, so a nightly sweep never makes one call per row.
MODEL_JOBS = frozenset({"store-digest"})
JOB_TIMEOUT_S = 900            # one run per market; a runtime call may take up to 280 s


def schedules_for(job: str, sname: str, expr: str) -> list[tuple[str, str, list[str]]]:
    """(schedule name, expression, markets) for one JOBS row. The first market
    keeps the row's schedule name, so an existing schedule is updated in place."""
    local = LOCAL_TIME_SCHEDULES.get(job)
    if not local:
        return [(sname, expr, list(MARKETS))]
    return [(sname if i == 0 else f"{sname}-{m}", local[m], [m]) for i, m in enumerate(MARKETS)]
assert set(JOB_FN.values()) == set(iam_roles.WORKER_FUNCTIONS), \
    "deploy_l6.JOBS and iam_roles.WORKER_FUNCTIONS must list the same Lambdas"
if not set(registry.job_names()) <= set(JOB_FN):
    raise RuntimeError("every agent-owned job in the agent registry needs a job Lambda in deploy_l6.JOBS")
# Env keys an operator or deploy_agentcore owns on the job Lambdas; a redeploy keeps them.
_PRESERVE_ENV_KEYS = ("RETAIL_OPS_AGENT_RUNTIMES", "RETAIL_OPS_AGENT_RUNTIME_DISABLE")
# kept for callers that import the old names
MONITOR_FN = JOB_FN["equity-monitor"]
NURTURE_FN = JOB_FN["nurture-tick"]
SVCLANE_FN = JOB_FN["service-lane-monitor"]
SHOPLOAD_FN = JOB_FN["shop-load"]


def _zip_code() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for py in (ROOT / "app").rglob("*.py"):
            if "__pycache__" in py.parts or "console" in py.parts or py.parts[1] == "infra" \
                    or py.name in ("devserver.py", "ddb_load.py"):
                continue
            z.write(py, str(py.relative_to(ROOT)))
    return buf.getvalue()


def _guardrail_env() -> dict[str, str]:
    cfg = naming.config_path("guardrail")
    gc = json.loads(cfg.read_text()) if cfg.exists() else {}
    out = ({"RETAIL_OPS_GUARDRAIL_ID": gc["guardrail_id"], "RETAIL_OPS_GUARDRAIL_VERSION": str(gc.get("version") or "DRAFT")}
           if gc.get("guardrail_id") else {})
    # the store digest's own guardrail (deploy_guardrails --which digest); job_env
    # keeps it on the MODEL_JOBS only
    dg = naming.config_path("guardrail-digest")
    if dg.exists():
        d = json.loads(dg.read_text())
        if d.get("guardrail_id"):
            out["RETAIL_OPS_DIGEST_GUARDRAIL_ID"] = d["guardrail_id"]
            out["RETAIL_OPS_DIGEST_GUARDRAIL_VERSION"] = str(d.get("version") or "DRAFT")
    kc = naming.config_path("knowledge-base")
    if kc.exists():
        k = json.loads(kc.read_text())
        if k.get("knowledge_base_id"):
            out["RETAIL_OPS_KNOWLEDGE_BASE_ID"] = k["knowledge_base_id"]
    # AgentCore Memory (deploy_memory.py): jobs that touch a customer (nurture
    # tick's send path) record promises there in aws mode.
    mc = naming.config_path("memory")
    if mc.exists():
        m = json.loads(mc.read_text())
        if m.get("memory_id"):
            out["RETAIL_OPS_MEMORY_ID"] = m["memory_id"]
    return out


def _wait_updated(lam, name):
    for _ in range(30):
        st = lam.get_function(FunctionName=name)["Configuration"].get("LastUpdateStatus")
        if st in (None, "Successful"):
            return
        if st == "Failed":
            raise RuntimeError(f"{name} update failed")
        time.sleep(2)


def _base_env() -> dict[str, str]:
    # Jobs publish to the live bus so subscribers (Plays, conversion joiner) run in
    # the subscriber Lambda, as in production. Generation stays deterministic in
    # batch jobs (no INTEGRATION_MODE=aws): a nightly sweep must not make one
    # model call per journey.
    return {"RETAIL_OPS_ENV": naming.ENV, "RETAIL_OPS_ENV_PREFIX": naming.PREFIX, "RETAIL_OPS_BEDROCK_MODEL": BEDROCK_MODEL,
            "RETAIL_OPS_EVENT_BUS_LIVE": "1", **_guardrail_env(),
            # nurture-tick / service-followup-tick send email: the SES sender comes
            # from the deploy environment (no default; dealer-task fallback without it)
            **deploy_l4.ses_from_env()}


def job_env(base: dict[str, str], runtime_env: dict[str, str], job: str) -> dict[str, str]:
    """A job Lambda's environment. An agent-owned job gets the runtime map
    restricted to its owner, so the job runs in that agent's runtime (ADR 0009);
    district-cycle, store-digest and autonomy-promotion get none and always run
    in-process. No INTEGRATION_MODE (batch jobs make no model call) except on
    the MODEL_JOBS, which exist to write with Bedrock."""
    env = {k: v for k, v in base.items() if k not in ("INTEGRATION_MODE", "RETAIL_OPS_AGENT_RUNTIMES")}
    if job in MODEL_JOBS:
        env["INTEGRATION_MODE"] = "aws"
    else:                                   # the digest guardrail belongs to the model jobs only
        env = {k: v for k, v in env.items() if not k.startswith("RETAIL_OPS_DIGEST_GUARDRAIL_")}
    owner = registry.job_owner(job)
    if owner and runtime_env.get("RETAIL_OPS_AGENT_RUNTIMES"):
        arns = json.loads(runtime_env["RETAIL_OPS_AGENT_RUNTIMES"])
        env["RETAIL_OPS_AGENT_RUNTIMES"] = json.dumps({owner: arns[owner]} if owner in arns else {},
                                                      separators=(",", ":"))
    return env


def agent_job_functions(codes: list[str] | str) -> list[str]:
    """The job Lambdas of the named agents' jobs ("all": every agent-owned job)."""
    return [JOB_FN[j] for j in registry.job_names() if codes == "all" or registry.job_owner(j) in codes]


def ensure_job_fn(lam, name, role_arn, code, apply, env=None) -> str:
    """Create/update an agentic-job Lambda (all use the same job_handler entry)."""
    env = dict(env if env is not None else _base_env())
    handler = "app.services.api.job_handler.lambda_handler"
    try:
        lam.get_function(FunctionName=name); exists = True
    except lam.exceptions.ResourceNotFoundException:
        exists = False
    if not apply:
        print(f"  [lambda] {'would update' if exists else 'would create'} {name}")
        return f"(arn {name})"
    if exists:
        # Keep the runtime map (when this machine has no agentcore-config.json) and
        # the operator's rollback switch, instead of replacing the environment wholesale.
        cur = (lam.get_function(FunctionName=name)["Configuration"].get("Environment") or {}).get("Variables", {})
        env = deploy_l4.merge_preserved(cur, env, _PRESERVE_ENV_KEYS)
        lam.update_function_code(FunctionName=name, ZipFile=code); _wait_updated(lam, name)
        lam.update_function_configuration(FunctionName=name, Handler=handler,
                                          Role=role_arn, Runtime=RUNTIME, Timeout=JOB_TIMEOUT_S, MemorySize=512,
                                          Environment={"Variables": env}); _wait_updated(lam, name)
        print(f"  [lambda] updated {name}")
    else:
        lam.create_function(FunctionName=name, Runtime=RUNTIME, Role=role_arn, Handler=handler,
                            Code={"ZipFile": code}, Timeout=JOB_TIMEOUT_S, MemorySize=512,
                            Environment={"Variables": env}); _wait_updated(lam, name)
        print(f"  [lambda] created {name}")
    from app.infra import freshness
    freshness.stamp_lambda(lam, name, code)   # verify.sh freshness: which source this Lambda runs
    return lam.get_function(FunctionName=name)["Configuration"]["FunctionArn"]


def ensure_scheduler_role(iam, account, apply) -> str:
    arn = f"arn:aws:iam::{account}:role/{SCHED_ROLE}"
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
             "Principal": {"Service": "scheduler.amazonaws.com"}, "Action": "sts:AssumeRole"}]}
    if not apply:
        print(f"  [role] would ensure {SCHED_ROLE}")
        return arn
    try:
        iam.get_role(RoleName=SCHED_ROLE)
    except iam.exceptions.NoSuchEntityException:
        iam.create_role(RoleName=SCHED_ROLE, AssumeRolePolicyDocument=json.dumps(trust),
                        Description=f"Retail Ops {naming.ENV} EventBridge Scheduler invoke role")
        print(f"  [role] created {SCHED_ROLE}"); time.sleep(8)
    iam.put_role_policy(RoleName=SCHED_ROLE, PolicyName="invoke-monitor",
                        PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [{
                            "Effect": "Allow", "Action": "lambda:InvokeFunction",
                            "Resource": [f"arn:aws:lambda:{REGION}:{account}:function:{fn}"
                                         for fn in JOB_FN.values()]}]}))
    return arn


def ensure_named_schedule(sch, name, expr, target_arn, sched_role_arn, job_input, desc, apply,
                          state="ENABLED", preserve_state=False, force_state=None):
    """Create/update a schedule. When preserve_state=True an existing schedule's
    ENABLED/DISABLED state is kept (so a redeploy never silently re-enables a job
    an operator disabled on purpose). force_state overrides both."""
    if not apply:
        print(f"  [schedule] would ensure {name} ({expr}), state={force_state or state}"); return
    desired_state = state
    params = dict(Name=name, ScheduleExpression=expr, FlexibleTimeWindow={"Mode": "OFF"},
                  Target={"Arn": target_arn, "RoleArn": sched_role_arn, "Input": json.dumps(job_input)},
                  Description=desc)
    try:
        cur = sch.get_schedule(Name=name)
        if preserve_state:
            desired_state = cur.get("State", state)
        if force_state:
            desired_state = force_state
        sch.update_schedule(State=desired_state, **params)
        print(f"  [schedule] updated {name} (state={desired_state})")
    except sch.exceptions.ResourceNotFoundException:
        if force_state:
            desired_state = force_state
        sch.create_schedule(State=desired_state, **params)
        print(f"  [schedule] created {name} ({expr}), state={desired_state}")


def delete_stack(iam, lam, sch):
    for job, fn, sname, expr, *_ in JOBS:
        for name, _expr, _markets in schedules_for(job, sname, expr):
            try:
                sch.delete_schedule(Name=name); print(f"  deleted schedule {name}")
            except sch.exceptions.ResourceNotFoundException:
                pass
        try:
            lam.delete_function(FunctionName=fn); print(f"  deleted lambda {fn}")
        except lam.exceptions.ResourceNotFoundException:
            pass
    try:
        for p in iam.list_role_policies(RoleName=SCHED_ROLE)["PolicyNames"]:
            iam.delete_role_policy(RoleName=SCHED_ROLE, PolicyName=p)
        iam.delete_role(RoleName=SCHED_ROLE); print(f"  deleted role {SCHED_ROLE}")
    except iam.exceptions.NoSuchEntityException:
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Deploy the L6 agentic scheduler.")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--delete", action="store_true")
    ap.add_argument("--run-once", metavar="JOB", nargs="?", const="equity-monitor",
                    help="invoke a deployed job now (" + " | ".join(JOB_FN) + ")")
    ap.add_argument("--enable-all", action="store_true",
                    help="set every schedule ENABLED (dev/demo: writers run at level 'recommend')")
    ap.add_argument("--disable-writers", action="store_true",
                    help="set every writer schedule DISABLED (equity-monitor stays on)")
    switch = ap.add_mutually_exclusive_group()
    switch.add_argument("--disable-agents", metavar="CODES|all",
                        help="run these agents' jobs in-process on their job Lambdas (rollback switch; merge-safe)")
    switch.add_argument("--enable-agents", metavar="CODES|all",
                        help="run these agents' jobs in their runtimes again")
    args = ap.parse_args(argv)
    try:
        disable = deploy_l4.parse_agent_list(args.disable_agents) if args.disable_agents else None
        enable = deploy_l4.parse_agent_list(args.enable_agents) if args.enable_agents else None
    except ValueError as e:
        ap.error(str(e))

    import boto3
    s = boto3.Session(region_name=REGION)
    account = s.client("sts").get_caller_identity()["Account"]
    iam, lam, sch = s.client("iam"), s.client("lambda"), s.client("scheduler")
    print(f"account={account} region={REGION} runtime={RUNTIME}")

    if disable or enable:
        fns = agent_job_functions(disable or enable)
        if not fns:
            print("  the named agents own no scheduled job; nothing to change")
            return 0
        return deploy_l4.toggle_agents(lam, fns, disable=disable, enable=enable, apply=args.apply,
                                       sales_package_note=False)
    if args.delete:
        delete_stack(iam, lam, sch); return 0

    if args.run_once:
        fn = JOB_FN.get(args.run_once)
        if not fn:
            print(f"unknown job {args.run_once!r}; known: {', '.join(JOB_FN)}"); return 2
        from botocore.config import Config
        lam_wait = s.client("lambda", config=Config(read_timeout=JOB_TIMEOUT_S + 10, retries={"max_attempts": 0}))
        r = lam_wait.invoke(FunctionName=fn, Payload=json.dumps({"job": args.run_once, "markets": list(MARKETS)}).encode())
        print(json.dumps(json.load(r["Payload"]), indent=1)[:4000]); return 0

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] deploying L6 scheduler ({len(JOBS)} jobs)")
    role_arn = iam_roles.ensure_role(iam, account, EXEC_ROLE, args.apply)
    if args.apply:
        time.sleep(8)  # let the worker role propagate before create_function
    code = _zip_code()
    print(f"  [pkg] app zip = {len(code)} bytes (worker role: {EXEC_ROLE})")
    sched_role_arn = ensure_scheduler_role(iam, account, args.apply)
    runtime_env = deploy_l4.runtime_env_or_exit(quiet=True)
    base = _base_env()
    for job, fn, sname, expr, desc, ships_enabled in JOBS:
        env = job_env(base, runtime_env, job)
        if "RETAIL_OPS_AGENT_RUNTIMES" in env and env["RETAIL_OPS_AGENT_RUNTIMES"] != "{}":
            owner = registry.job_owner(job)
            print(f"  [agentcore] {job} runs in the {registry.AGENTS[owner].display} runtime")
        arn = ensure_job_fn(lam, fn, role_arn, code, args.apply, env)
        force = None
        if args.enable_all:
            force = "ENABLED"
        elif args.disable_writers and not ships_enabled:
            force = "DISABLED"
        for name, sexpr, markets in schedules_for(job, sname, expr):
            ensure_named_schedule(sch, name, sexpr, arn, sched_role_arn, {"job": job, "markets": markets},
                                  desc, args.apply, state="ENABLED" if ships_enabled else "DISABLED",
                                  preserve_state=not ships_enabled, force_state=force)
    print(f"[{mode}] done." + ("" if args.apply else "  (re-run with --apply)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
