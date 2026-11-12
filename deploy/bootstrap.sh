#!/usr/bin/env bash
# bootstrap.sh — stand up the ENTIRE Retail Ops stack in a fresh AWS account, one command.
#
# This is the "new account" front door. It preflights the environment, publishes
# the optional JWT Lambda layer, runs the full ordered deploy (deploy/deploy.sh
# --apply, which covers cognito → data → l4 → events → lake → approvals → l6 →
# alarms → agentcore → gateway → quick), optionally loads the second market, then
# verifies the deployed surface with the smoke suite.
#
# It is a thin, SAFE wrapper over the existing idempotent deployers — it adds the
# fresh-account concerns deploy.sh assumes away: prerequisite checks, an identity
# confirmation gate, the JWT layer, and an end-to-end verify.
#
#   Order:
#     0. preflight  — aws cli, credentials, Python 3.12+ with boto3, env name, region
#     1. identity   — print the target account/arn; REQUIRE --yes to proceed
#     2. jwt-layer  — publish retail-ops-<env>-jwt (best-effort; stack works without it)
#     3. deploy     — deploy/deploy.sh --apply (the whole stack, in order)
#     4. data (DE)  — optionally also load the German market (US is loaded by deploy)
#     5. verify     — run the smoke suite against the new API base
#
# Usage:
#   deploy/bootstrap.sh                      # DRY-RUN: preflight + print plan, deploy nothing
#   deploy/bootstrap.sh --yes                # deploy the whole stack to the current account
#   deploy/bootstrap.sh --yes --region us-east-1
#   deploy/bootstrap.sh --yes --markets us,de   # load both markets (default: us only)
#   deploy/bootstrap.sh --yes --skip quick,gateway   # pass-through skip to deploy.sh
#   deploy/bootstrap.sh --yes --enable-jobs    # also enable autonomy-promotion (the agent-bar jobs ship enabled)
#   deploy/bootstrap.sh --yes --env g4f2a91 --skip quick   # a separately named environment
#
# Notes:
#   * DRY-RUN (no --yes) makes NO changes — it validates prerequisites and prints
#     the account it WOULD deploy into. Always dry-run first in a new account.
#   * Idempotent: safe to re-run. Underlying deployers reuse existing resources.
#   * Environment name: --env <name> or RETAIL_OPS_ENV (default dev); every
#     resource is named retail-ops-<env>-… (app/infra/naming.py).
#   * Unattended: no prompts. Python is $PYTHON, else the active virtual
#     environment, else ./.venv, else python3. Any failed step stops the run.
#   * The API base URL is stable across re-deploys; this script prints it and
#     the verify command.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export AWS_PAGER=""          # never open a pager: the run must not wait for a terminal

REGION="us-east-1"
MARKETS="us"          # comma list; us is also loaded by deploy.sh's data stage
ENABLE_JOBS=0         # --enable-jobs: turn every writer schedule on (dev/demo)
YES=0
SKIP=""

usage() {
  sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|--confirm) YES=1; shift ;;
    --region)        REGION="$2"; shift 2 ;;
    --markets)       MARKETS="$2"; shift 2 ;;
    --skip)          SKIP="$2"; shift 2 ;;
    --enable-jobs)   ENABLE_JOBS=1; shift ;;
    --env)           export RETAIL_OPS_ENV="$2"; shift 2 ;;
    -h|--help)       usage 0 ;;
    *) echo "unknown argument: $1" >&2; usage 1 ;;
  esac
done

MODE=$([[ "$YES" -eq 1 ]] && echo BOOTSTRAP || echo "DRY-RUN")
export AWS_DEFAULT_REGION="$REGION"
STEP="setup"

say()  { STEP="$*"; echo -e "\n== $* =="; }
fail() { echo "ERROR: $*" >&2; exit 1; }
on_exit() {
  local rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "ERROR: bootstrap stopped in step \"$STEP\" (exit $rc)." >&2
  fi
  return $rc
}
trap on_exit EXIT

# --- 0. preflight ----------------------------------------------------------
say "0. preflight (tools + credentials)"
command -v aws >/dev/null 2>&1 || fail "aws CLI not found on PATH"
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
[[ -n "$PY" ]] || fail "no Python 3.12+ with boto3 found (tried: ${CANDIDATES[*]}); create .venv and pip install -r requirements-agentcore.txt"
export PYTHON="$PY"
"$PY" -m app.infra.naming --check || fail "invalid environment name (see the message above)"
ENV_NAME="$("$PY" -c 'from app.infra import naming; print(naming.ENV)')"
PREFIX="$("$PY" -c 'from app.infra import naming; print(naming.PREFIX)')"
CONSOLE_CONFIG="$("$PY" -c 'from app.infra import naming; print(naming.config_path("console"))')"
echo "  aws:     $(command -v aws) ($(aws --version 2>&1 | cut -d' ' -f1))"
echo "  python:  $PY ($("$PY" -V 2>&1))"
echo "  boto3:   $("$PY" -c 'import boto3;print(boto3.__version__)' 2>/dev/null)"
echo "  env:     $ENV_NAME (resources named ${PREFIX}*)"

echo "######################################################################"
echo "#  Retail Ops bootstrap   mode=$MODE   env=$ENV_NAME   region=$REGION   markets=$MARKETS"
[[ -n "$SKIP" ]] && echo "#  skip=$SKIP"
echo "######################################################################"

# --- 1. identity gate ------------------------------------------------------
say "1. target account"
IDENTITY="$(aws sts get-caller-identity --output text --query '[Account,Arn]' 2>/dev/null)" \
  || fail "could not resolve AWS identity — configure credentials for the target account"
ACCOUNT="$(echo "$IDENTITY" | awk '{print $1}')"
echo "  account: $ACCOUNT"
echo "  arn:     $(echo "$IDENTITY" | awk '{print $2}')"
echo "  region:  $REGION"

# Warn if this looks like it already has a stack under this env (re-run is fine, but say so).
EXISTING="$(aws dynamodb list-tables --region "$REGION" --output text \
  --query "TableNames[?starts_with(@,\`${PREFIX}\`)]" 2>/dev/null | tr '\t' '\n' | grep -c . || true)"
EXISTING="${EXISTING:-0}"
if [[ "$EXISTING" -gt 0 ]] 2>/dev/null; then
  echo "  note: $EXISTING existing ${PREFIX}* tables found — this is a RE-RUN (idempotent)."
fi

if [[ "$YES" -ne 1 ]]; then
  echo
  echo "DRY-RUN — nothing was changed. This is the account that WOULD be deployed into."
  echo "Re-run with --yes to deploy the full stack here."
  echo "######################################################################"
  exit 0
fi

# --- 2. JWT layer (best-effort) -------------------------------------------
say "2. jwt layer (${PREFIX}jwt — optional hardening)"
if "$PY" -m app.infra.publish_jwt_layer --apply; then
  echo "  jwt layer published."
else
  echo "  jwt layer NOT published (build/pip unavailable) — continuing; the"
  echo "  authorizer uses its stdlib RSA fallback (identical validation)."
fi

# --- 3. full stack deploy --------------------------------------------------
say "3. deploy the full stack (deploy/deploy.sh --apply)"
DEPLOY_ARGS=(--apply --region "$REGION" --market us)
[[ -n "$SKIP" ]] && DEPLOY_ARGS+=(--skip "$SKIP")
[[ "$ENABLE_JOBS" -eq 1 ]] && DEPLOY_ARGS+=(--enable-jobs)
bash deploy/deploy.sh "${DEPLOY_ARGS[@]}"

# --- 4. extra markets ------------------------------------------------------
say "4. data load for additional markets"
IFS=',' read -ra MKTS <<< "$MARKETS"
for m in "${MKTS[@]}"; do
  if [[ "$m" == "us" ]]; then
    echo "  us: already loaded by the deploy 'data' stage — skipping"
    continue
  fi
  echo "  loading market '$m'…"
  "$PY" app/services/l1_data/ddb_load.py --market "$m" --region "$REGION" --apply
done

# --- 5. resolve base + verify ---------------------------------------------
say "5. resolve API base + smoke"
API_ID="$(aws apigateway get-rest-apis --region "$REGION" \
  --query "items[?name=='${PREFIX}journeys-api'].id | [0]" --output text 2>/dev/null || true)"
if [[ -z "${API_ID:-}" || "$API_ID" == "None" ]]; then
  echo "  WARN: could not resolve the REST API id; skipping smoke."
  echo "######################################################################"
  echo "# bootstrap complete (API base unresolved — check the l4 stage output)."
  echo "######################################################################"
  exit 0
fi
BASE="https://${API_ID}.execute-api.${REGION}.amazonaws.com/dev"
echo "  API base: $BASE"

# Smoke against the fresh stack. Don't fail the whole bootstrap on a smoke miss —
# print the result and let the operator inspect (deploy/verify.sh is the gate).
if "$PY" -m app.demo.run_demo --smoke --api "$BASE"; then
  SMOKE="ALL PASS"
else
  SMOKE="SOME FAILURES (inspect above)"
fi

echo
echo "######################################################################"
echo "# bootstrap complete."
echo "#   account:  $ACCOUNT   env: $ENV_NAME   region: $REGION"
echo "#   API base: $BASE"
echo "#   smoke:    $SMOKE"
echo "#"
if [[ -f "$CONSOLE_CONFIG" ]]; then
  CONSOLE_URL="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1])).get('url',''))" "$CONSOLE_CONFIG" 2>/dev/null || true)"
  [[ -n "${CONSOLE_URL:-}" ]] && echo "#   Console:  $CONSOLE_URL"
fi
echo "#   Next:"
echo "#     - verify everything: deploy/verify.sh${RETAIL_OPS_ENV:+ --env $ENV_NAME}"
echo "#     - persona access + demo tokens: docs/ACCESS_BY_PERSONA.md"
echo "#     - console (hosted): see Console URL above, or local: cd app/console && NEXT_PUBLIC_RETAIL_OPS_API=$BASE npm run dev"
echo "#     - deployed dependency handles: docs/specs/retail-ops-quick-dependencies.md"
echo "######################################################################"
