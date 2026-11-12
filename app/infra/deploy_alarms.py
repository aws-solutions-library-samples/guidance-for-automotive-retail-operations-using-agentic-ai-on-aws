#!/usr/bin/env python3
"""Observability deploy via boto3: CloudWatch alarms on the Retail Ops Lambdas (spec §8).

Creates one Errors alarm and one Throttles alarm per deployed Lambda, so a
function that starts failing or getting throttled surfaces without anyone
watching the logs. Alarms are the operational half of AR-6 (the correlation-id
trace helper in app/services/common/trace.py is the in-app half).

  * Errors     — ALARM when SUM(Errors) >= 1 over a 5-minute period (1 datapoint).
  * Throttles  — ALARM when SUM(Throttles) >= 1 over a 5-minute period.

Optional --sns <topic-arn> attaches an existing SNS topic as the alarm action;
without it the alarms still evaluate and show state in the console (no notify),
which is the right default for a dev account with no on-call wiring.

The nine agent-owned job Lambdas also get a `runtime_job_unknown` alarm (ADR
0009): when a job was sent to its agent's runtime and no answer came back, the
job Lambda logs one JSON line `{"event": "runtime_job_unknown", ...}` instead of
raising (a retry could start a second run). A metric filter on its log group
turns that line into RetailOps/Jobs RuntimeJobUnknown, and `<fn>-runtime-unknown`
alarms on SUM >= 1 over 5 minutes.

Idempotent (put_metric_alarm / put_metric_filter overwrite). Dry-run by default;
--apply to create; --delete to remove every retail-ops-<env>-*-{errors,throttles,
runtime-unknown} alarm and the metric filters.
"""
from __future__ import annotations

import argparse

REGION = "us-east-1"

# Every Lambda the stack deploys (L4 + L6 + events + approvals + gateway). The job
# Lambdas come from the same list the IAM module and deploy_l6 agree on.
try:
    from app.agentcore import registry
    from app.infra import deploy_l6, naming
    from app.infra.iam_roles import WORKER_FUNCTIONS as _JOB_FUNCTIONS
except ImportError:  # run as a script from app/infra
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
    from app.agentcore import registry
    from app.infra import deploy_l6, naming
    from app.infra.iam_roles import WORKER_FUNCTIONS as _JOB_FUNCTIONS
FUNCTIONS = [
    naming.name("journeys-authorizer"),
    naming.name("journeys-service"),
    *_JOB_FUNCTIONS,
    naming.name("event-subscriber"),
    naming.name("approvals-submit"),
    naming.name("gateway-oem"),
]

# (metric, alarm-name-suffix, human description)
METRICS = [
    ("Errors", "errors", "Lambda invocation errors"),
    ("Throttles", "throttles", "Lambda throttles"),
]

PERIOD = 300          # 5 minutes
EVAL_PERIODS = 1
THRESHOLD = 1.0       # >= 1 error/throttle in the window trips the alarm


def _alarm_name(fn: str, suffix: str) -> str:
    return f"{fn}-{suffix}"


def ensure_alarms(cw, sns_arn: str | None, apply: bool) -> int:
    n = 0
    for fn in FUNCTIONS:
        for metric, suffix, desc in METRICS:
            name = _alarm_name(fn, suffix)
            if not apply:
                print(f"  [alarm] would ensure {name} ({metric} >= {THRESHOLD:g}/5m)")
                n += 1
                continue
            kwargs = dict(
                AlarmName=name,
                AlarmDescription=f"{desc} on {fn} (Retail Ops {naming.ENV})",
                Namespace="AWS/Lambda",
                MetricName=metric,
                Dimensions=[{"Name": "FunctionName", "Value": fn}],
                Statistic="Sum",
                Period=PERIOD,
                EvaluationPeriods=EVAL_PERIODS,
                Threshold=THRESHOLD,
                ComparisonOperator="GreaterThanOrEqualToThreshold",
                TreatMissingData="notBreaching",
            )
            if sns_arn:
                kwargs["AlarmActions"] = [sns_arn]
                kwargs["OKActions"] = [sns_arn]
            cw.put_metric_alarm(**kwargs)
            print(f"  [alarm] set {name}")
            n += 1
    return n


UNKNOWN_PATTERN = '{ $.event = "runtime_job_unknown" }'
UNKNOWN_METRIC = "RuntimeJobUnknown"
# one metric namespace per env, so two envs in one account never share the metric
UNKNOWN_NAMESPACE = "RetailOps/Jobs" if naming.IS_DEFAULT else f"RetailOps/Jobs/{naming.ENV}"


def runtime_unknown_specs() -> list[dict]:
    """One metric filter + alarm per agent-owned job Lambda (nine). A metric
    filter's dimension values must come from the log line, so the dimension is
    `Job` from `$.job`; each job Lambda runs exactly one job, so the alarm is
    still per function."""
    specs = []
    for job in registry.job_names():
        fn = deploy_l6.JOB_FN[job]
        specs.append({"job": job, "function": fn, "log_group": f"/aws/lambda/{fn}",
                      "filter_name": f"{fn}-runtime-job-unknown", "pattern": UNKNOWN_PATTERN,
                      "metric": UNKNOWN_METRIC, "namespace": UNKNOWN_NAMESPACE,
                      "dimension": {"Name": "Job", "Value": job}, "alarm": _alarm_name(fn, "runtime-unknown")})
    return specs


def ensure_runtime_unknown(logs, cw, sns_arn: str | None, apply: bool) -> int:
    """Create the log group if needed, then the metric filter and the alarm, per job Lambda."""
    n = 0
    for spec in runtime_unknown_specs():
        n += 1
        if not apply:
            print(f"  [alarm] would ensure {spec['alarm']} ({UNKNOWN_METRIC} >= {THRESHOLD:g}/5m, "
                  f"filter {spec['filter_name']} on {spec['log_group']})")
            continue
        try:   # a job Lambda that never ran has no log group yet, and PutMetricFilter needs one
            logs.create_log_group(logGroupName=spec["log_group"])
        except logs.exceptions.ResourceAlreadyExistsException:
            pass
        logs.put_metric_filter(
            logGroupName=spec["log_group"], filterName=spec["filter_name"], filterPattern=spec["pattern"],
            metricTransformations=[{"metricName": UNKNOWN_METRIC, "metricNamespace": UNKNOWN_NAMESPACE,
                                    "metricValue": "1", "dimensions": {"Job": "$.job"}}])
        kwargs = {
            "AlarmName": spec["alarm"],
            "AlarmDescription": f"{spec['job']} was sent to its agent runtime and no answer came back "
                                f"(runtime_job_unknown) on {spec['function']} (Retail Ops {naming.ENV})",
            "Namespace": UNKNOWN_NAMESPACE,
            "MetricName": UNKNOWN_METRIC,
            "Dimensions": [spec["dimension"]],
            "Statistic": "Sum",
            "Period": PERIOD,
            "EvaluationPeriods": EVAL_PERIODS,
            "Threshold": THRESHOLD,
            "ComparisonOperator": "GreaterThanOrEqualToThreshold",
            "TreatMissingData": "notBreaching",
        }
        if sns_arn:
            kwargs["AlarmActions"] = [sns_arn]
            kwargs["OKActions"] = [sns_arn]
        cw.put_metric_alarm(**kwargs)
        print(f"  [alarm] set {spec['alarm']} (+ metric filter {spec['filter_name']})")
    return n


def delete_runtime_unknown(logs) -> None:
    """Remove the runtime_job_unknown metric filters (the alarms go with delete_alarms)."""
    for spec in runtime_unknown_specs():
        try:
            logs.delete_metric_filter(logGroupName=spec["log_group"], filterName=spec["filter_name"])
            print(f"  deleted metric filter {spec['filter_name']}")
        except logs.exceptions.ResourceNotFoundException:
            pass


def delete_alarms(cw) -> None:
    names = [_alarm_name(fn, suffix) for fn in FUNCTIONS for _, suffix, _ in METRICS]
    names += [spec["alarm"] for spec in runtime_unknown_specs()]
    # delete in chunks of 100 (API limit)
    for i in range(0, len(names), 100):
        chunk = names[i:i + 100]
        try:
            cw.delete_alarms(AlarmNames=chunk)
            print(f"  deleted {len(chunk)} alarms")
        except Exception as e:
            print(f"  delete note: {e}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Deploy CloudWatch alarms for the Retail Ops Lambdas.")
    ap.add_argument("--apply", action="store_true", help="create/update (default: dry-run)")
    ap.add_argument("--delete", action="store_true", help="remove all Retail Ops alarms")
    ap.add_argument("--sns", default=None, help="optional SNS topic ARN for alarm/OK actions")
    args = ap.parse_args(argv)

    import boto3
    s = boto3.Session(region_name=REGION)
    account = s.client("sts").get_caller_identity()["Account"]
    cw, logs = s.client("cloudwatch"), s.client("logs")
    print(f"account={account} region={REGION}")

    if args.delete:
        delete_alarms(cw)
        delete_runtime_unknown(logs)
        return 0

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] deploying CloudWatch alarms for {len(FUNCTIONS)} functions"
          + (f" (SNS: {args.sns})" if args.sns else " (no SNS action)"))
    total = ensure_alarms(cw, args.sns, args.apply)
    total += ensure_runtime_unknown(logs, cw, args.sns, args.apply)
    print(f"[{mode}] {'set' if args.apply else 'would set'} {total} alarms"
          + ("" if args.apply else "  (re-run with --apply)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
