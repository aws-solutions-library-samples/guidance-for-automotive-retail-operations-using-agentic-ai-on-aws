#!/usr/bin/env bash
# destroy.sh — delete every AWS resource this solution created for one naming stem and env.
#
# DESTRUCTIVE with --confirm. Without it, the script lists what it found and
# deletes nothing (a dry run). Discovery is by resource NAME (`<stem>-<env>-…`),
# so it removes a stack deployed under an earlier stem too (`--stem <old-stem>`;
# the default is the current `retail-ops`). `--env` (default RETAIL_OPS_ENV, else
# dev) picks the environment, the name the stack was deployed with.
#
# Covers: schedules, alarms, CloudFront distribution + function + OACs, WAF,
# Amazon Quick content (the eight `Retail Ops …` Flows, chat agent, space,
# dashboard + analysis, topic, knowledge bases + their S3 source, custom-SQL
# datasets, and the console-built MCP connector `Retail Ops OEM Read Gateway`;
# dependents first), Amazon Quick datasets / data source / groups / service-role
# policy, AgentCore gateways / runtime / memory / workload identities, Bedrock
# knowledge base + S3 Vectors + guardrail, REST API, Step Functions, Lambdas +
# layers, EventBridge rules (+ the app-bus-<env> bus once empty), SQS, Firehose,
# Glue + Athena, secrets, Cognito pool (+ domain, clients, IdP), IAM roles, S3
# buckets, and log groups. The implementation is app/infra/teardown.py.
#
# Kept unless asked: the DynamoDB tables and the event-lake bucket
# (--include-data), and the Quick readers the app registered
# (--include-quick-users). Other Quick content in the account (sample Flows,
# other dashboards, datasets and agents) is never touched: Quick content is
# matched by the repo's ids (`<stem>-…`) and, for the Flows and the connector,
# by their exact `Retail Ops …` names. Quick ids carry no env, so for an env
# other than dev the Quick stages run only with --include-quick.
#
# Gates: --confirm needs --account <id> equal to the caller's account; an
# environment name containing "prod" is refused; a Cognito pool or table with
# deletion protection ON is skipped and reported, never switched off.
#
# Usage:
#   deploy/destroy.sh                                               # dry run (env dev)
#   deploy/destroy.sh --env g4f2a91                                 # dry run of another env
#   deploy/destroy.sh --env g4f2a91 --confirm --account <id> --include-data   # delete all of it
#   deploy/destroy.sh --confirm --account <id>                      # keep tables + lake
#   deploy/destroy.sh --confirm --account <id> --only quick,alarms  # selected stages
#   deploy/destroy.sh --only quick_content,quick                    # Amazon Quick only (dry run)
#   deploy/destroy.sh --stem <old-stem> --confirm --account <id>    # a stack under an earlier stem
#
# A CloudFront distribution is disabled first and deleted once it has finished
# disabling; if the run ends while it is still disabling, run the same command
# again about 15 minutes later.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export AWS_PAGER=""

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  sed -n '2,44p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 0
fi

# Python: $PYTHON, else the active virtual environment, else ./.venv, else python3.
PY=""
if [[ -n "${PYTHON:-}" ]]; then
  CANDIDATES=("$PYTHON")
else
  CANDIDATES=()
  [[ -n "${VIRTUAL_ENV:-}" ]] && CANDIDATES+=("$VIRTUAL_ENV/bin/python")
  CANDIDATES+=("$ROOT/.venv/bin/python" python3)
fi
for c in "${CANDIDATES[@]}"; do
  if command -v "$c" >/dev/null 2>&1 \
      && "$c" -c 'import sys, boto3; sys.exit(sys.version_info < (3, 12))' >/dev/null 2>&1; then
    PY="$(command -v "$c")"; break
  fi
done
[[ -n "$PY" ]] || { echo "ERROR: no Python 3.12+ with boto3 found (tried: ${CANDIDATES[*]})" >&2; exit 1; }

exec "$PY" -m app.infra.teardown "$@"
