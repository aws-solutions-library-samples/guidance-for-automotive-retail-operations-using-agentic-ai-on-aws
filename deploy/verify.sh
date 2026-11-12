#!/usr/bin/env bash
# verify.sh — prove a deployment works, end to end, in one command.
#
# Resolves the live API base and Cognito client from the account, signs in as
# the seed advisor, and runs every check we have against the deployed stack:
#
#   0. freshness    every Lambda and agent runtime runs the source in this checkout (a
#                   partial deploy fails here, before any check passes on old code)
#                                                                  (app.infra.freshness)
#   1. smoke        read/scope matrix over the REST API           (run_demo --smoke)
#   2. acceptance   asserted §11 criteria incl. the live IAM scan (run_demo --acceptance)
#   3. loop         full write loop with throwaway DEMO-* rows, torn down (run_demo)
#   4. workflow7    demo workflow 7, one buyer from web lead to delivery, under each
#                   persona in turn; a throwaway buyer, torn down          (run_demo --workflow7)
#   5. invariants   the stack's records agree with each other, after real use: a sold car
#                   is never reserved, a service lead never opens a sales journey, one open
#                   alert per store, Today and Accounts count alike …  read-only (app.demo.invariants)
#      textlint     no raw code in any field a person reads, over every demo screen's read
#                   routes as every persona (table names, ids, snake_case, "(synthetic)",
#                   lowercase acronyms, doubled words …)             read-only (app.demo.textlint)
#      readiness    how many rehearsals each demo scenario has left (app.demo.readiness);
#                   a scenario with none fails — run deploy/demo-reset.sh
#   6. agent        the A3 reasoning agent through the API: golden cases (run_eval --mode api)
#   7. guardrail    the Bedrock Guardrail calibration lists         (deploy_guardrails --calibrate)
#   8. knowledge    one Retrieve against the Knowledge Base         (deploy_knowledge_base --query)
#   9. runtime/jobs one AgentCore runtime per agent READY + the nine schedules
#  10. agents       a read-only ping to each agent runtime          (deploy_agentcore --invoke)
#
# Usage:
#   deploy/verify.sh                 # everything
#   deploy/verify.sh --quick         # freshness + smoke + acceptance + invariants only (~1 min)
#   deploy/verify.sh --skip loop,workflow7,agent
#   deploy/verify.sh --env g4f2a91   # another environment (default: RETAIL_OPS_ENV, else dev)
#
# Exit code is non-zero if any selected check fails. Read-only except the loop and
# workflow7 (both clean up after themselves) and the agent eval (which may leave a
# task and a held outreach card for the seed advisor — by design; see app/eval/README.md).
# The smoke, acceptance, loop and workflow7 checks use the plain development tokens, so the
# authorizer must accept them while this runs (deploy_l4 --dev-token on, the default).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1
export AWS_PAGER=""          # never open a pager: the run must not wait for a terminal

REGION="${AWS_REGION:-us-east-1}"
SKIP=""
QUICK=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --quick)  QUICK=1; shift ;;
    --skip)   SKIP="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --env)    export RETAIL_OPS_ENV="$2"; shift 2 ;;
    -h|--help) sed -n '2,36p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done
[[ "$QUICK" -eq 1 ]] && SKIP="${SKIP:+$SKIP,}loop,workflow7,agent,guardrail,knowledge,runtime,agents"

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
"$PY" -m app.infra.naming --check || exit 1
PREFIX="$("$PY" -c 'from app.infra import naming; print(naming.PREFIX)')"
COGNITO_CONFIG="$("$PY" -c 'from app.infra import naming; print(naming.config_path("cognito"))')"

TMP_DIR="${TMPDIR:-/tmp}"
skip() { [[ ",$SKIP," == *",$1,"* ]]; }
FAILED=()
run() {  # run <name> <command...>
  local name="$1"; shift
  if skip "$name"; then echo "-- $name: skipped"; return; fi
  echo; echo "== $name"; echo "----------------------------------------------------------------------"
  if "$@"; then echo "-- $name: OK"; else echo "-- $name: FAILED"; FAILED+=("$name"); fi
}

API_ID=$(aws apigateway get-rest-apis --region "$REGION" \
  --query "items[?name=='${PREFIX}journeys-api'].id | [0]" --output text 2>/dev/null)
[[ -z "${API_ID:-}" || "$API_ID" == "None" ]] && { echo "no REST API ${PREFIX}journeys-api found — deploy first (deploy/deploy.sh --apply)"; exit 1; }
API="https://${API_ID}.execute-api.${REGION}.amazonaws.com/dev"
echo "API base: $API"

# Sign in as the seed advisor with boto3: the password is read from the
# environment inside Python, so it never appears on a command line or in the log.
TOKEN=""
if [[ -z "${RETAIL_OPS_SEED_PASSWORD:-}" ]]; then
  echo "warning: RETAIL_OPS_SEED_PASSWORD is not set; the agent sign-in check will be skipped"
elif [[ -f "$COGNITO_CONFIG" ]]; then
  TOKEN=$("$PY" - "$COGNITO_CONFIG" "$REGION" <<'PYEOF' 2>/dev/null || true
import json, os, sys
import boto3
cfg = json.load(open(sys.argv[1]))
r = boto3.client("cognito-idp", region_name=sys.argv[2]).initiate_auth(
    AuthFlow="USER_PASSWORD_AUTH", ClientId=cfg["client_id"],
    AuthParameters={"USERNAME": "U-US-00002", "PASSWORD": os.environ["RETAIL_OPS_SEED_PASSWORD"]})
print(r["AuthenticationResult"]["IdToken"])
PYEOF
)
fi
[[ -z "$TOKEN" || "$TOKEN" == "None" ]] && { echo "warning: could not sign in as U-US-00002; the agent check will be skipped"; SKIP="${SKIP:+$SKIP,}agent"; }

run freshness   "$PY" -m app.infra.freshness
run smoke       "$PY" -m app.demo.run_demo --smoke --api "$API"
run acceptance  "$PY" -m app.demo.run_demo --acceptance --api "$API"
run loop        "$PY" -m app.demo.run_demo --api "$API"
run workflow7   "$PY" -m app.demo.run_demo --workflow7 --api "$API"
run invariants  "$PY" -m app.demo.invariants
run textlint    "$PY" -m app.demo.textlint
run readiness   "$PY" -m app.demo.readiness
run agent       "$PY" -m app.eval.run_eval --mode api --api "$API" --token "$TOKEN" --out "$(mktemp "${TMP_DIR%/}/retail-ops-eval-api.XXXXXX")"
run guardrail   "$PY" -m app.infra.deploy_guardrails --calibrate --which all
run knowledge   "$PY" -m app.infra.deploy_knowledge_base --query "brake pad replacement procedure" --kind repair_procedures

runtime_and_jobs() {
  # One runtime per agent (ADR 0009); the names come from the agent registry.
  local names name rts rt sched total=0 ready=0
  names=$("$PY" -m app.agentcore.registry --names) || return 1
  rts=$(aws bedrock-agentcore-control list-agent-runtimes --region "$REGION" \
    --query 'agentRuntimes[].[agentRuntimeName,status]' --output text 2>/dev/null)
  for name in $names; do
    rt=$(echo "$rts" | awk -v n="$name" '$1 == n {print $2; exit}')
    echo "  runtime $name: ${rt:-missing}"
    total=$((total + 1))
    [[ "$rt" == "READY" ]] && ready=$((ready + 1))
  done
  sched=$(aws scheduler list-schedules --region "$REGION" \
    --query "Schedules[?starts_with(Name,\`${PREFIX}\`)].[Name,State]" --output text 2>/dev/null)
  while IFS= read -r line; do [[ -n "$line" ]] && echo "  schedule $line"; done <<< "$sched"
  [[ "$total" -ge 6 && "$ready" -eq "$total" ]] && [[ $(echo "$sched" | grep -c .) -ge 9 ]]
}
run runtime runtime_and_jobs
run agents      "$PY" -m app.infra.deploy_agentcore --invoke

echo
echo "======================================================================"
if [[ ${#FAILED[@]} -eq 0 ]]; then
  echo " VERIFY: ALL PASS   ($API)"
else
  echo " VERIFY: FAILED — ${FAILED[*]}"
  exit 1
fi
