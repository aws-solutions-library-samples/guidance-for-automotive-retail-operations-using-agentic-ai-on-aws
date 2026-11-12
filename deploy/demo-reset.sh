#!/usr/bin/env bash
# demo-reset.sh — put the demo stack back to the seed, rebased to today, ready to rehearse.
#
# Rehearsals write rows the seed does not have (journeys, handoffs, bookings, approvals,
# traces) and use up each scenario's subjects; a data reload cannot take them away. This:
#
#   1. syncs every table to exactly the seed rows, rebased to today (app.demo.reset):
#      rows the seed does not have are deleted, seed rows are written; tables are never
#      dropped. Every table is backed up first (on-demand backup, demoreset-<time>-<table>).
#   2. restores the SSO administrators (deploy.sh --only sso-admins, both markets).
#   3. checks the result: data invariants, then how many rehearsals each scenario has.
#
# Usage:
#   deploy/demo-reset.sh              # dry run: what would be deleted and written
#   deploy/demo-reset.sh --apply      # reset (asks to confirm)
#   deploy/demo-reset.sh --apply --yes --no-backup
#
# Only the dev stack (retail-ops-dev-*) unless RETAIL_OPS_ENV names another and --any-env is passed.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1
export AWS_PAGER=""
APPLY=0; YES=0; EXTRA=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --yes) YES=1; shift ;;
    --no-backup|--any-env) EXTRA+=("$1"); shift ;;
    -h|--help) sed -n '2,19p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done
PY="${PYTHON:-}"
for c in ${PY:+"$PY"} "${VIRTUAL_ENV:+$VIRTUAL_ENV/bin/python}" "$ROOT/.venv/bin/python" python3; do
  [[ -n "$c" ]] && command -v "$c" >/dev/null 2>&1 && "$c" -c 'import boto3' >/dev/null 2>&1 && { PY="$(command -v "$c")"; break; }
done
[[ -n "$PY" ]] || { echo "ERROR: no Python with boto3 found" >&2; exit 1; }

if [[ "$APPLY" -eq 0 ]]; then
  "$PY" -m app.demo.reset ${EXTRA[@]+"${EXTRA[@]}"}
  echo; echo "Dry run only. Re-run with --apply to reset."
  exit 0
fi
if [[ "$YES" -eq 0 ]]; then
  "$PY" -m app.demo.reset ${EXTRA[@]+"${EXTRA[@]}"} || exit 1
  echo
  read -r -p "Delete the rows above and reload the seed? Type 'reset' to continue: " ans
  [[ "$ans" == "reset" ]] || { echo "aborted"; exit 1; }
fi
"$PY" -m app.demo.reset --apply --yes ${EXTRA[@]+"${EXTRA[@]}"} || { echo "ERROR: reset failed"; exit 1; }
for m in us de; do
  bash deploy/deploy.sh --apply --only sso-admins --market "$m" >/dev/null 2>&1 \
    && echo "  SSO administrators restored ($m)" || echo "  WARNING: sso-admins ($m) failed — run deploy/deploy.sh --apply --only sso-admins --market $m"
done
echo
"$PY" -m app.demo.invariants; inv=$?
"$PY" -m app.demo.readiness; rdy=$?
exit $(( inv || rdy ))
