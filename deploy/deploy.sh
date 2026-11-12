#!/usr/bin/env bash
# deploy.sh — one-command deploy of the Retail Ops stack (AR-12 companion of destroy.sh).
#
# Orchestrates the boto3 deployers in dependency order. Each underlying deployer
# is idempotent and dry-runs unless --apply is passed, so the default here is a
# DRY-RUN of the whole stack (safe to run to preview).
#
#   Order (dependencies flow downward; see docs/DEPLOYMENT.md for the guide):
#     cognito    — staff user pool, 19 groups, seed users, Quick MCP machine client
#     data       — create + load the 68 retail-ops-<env>-* DynamoDB tables from the synthetic packs
#     sso-admins — Administrator rows in PLAT_Users for the RETAIL_OPS_SSO_ADMINS aliases
#     events     — app-bus-<env> event bus + subscriber Lambda + DLQ
#     lake       — S3 event lake (Firehose, Glue, Athena) + analytics mirrors + roll-ups
#     corpus     — export the 538-document knowledge corpus to s3://<lake>/quick-index/
#     guardrails — Bedrock Guardrail attached to every model call
#     kb         — Bedrock Knowledge Base over the corpus (needs corpus)
#     l4         — per-function IAM roles, authorizer + service Lambdas, REST API
#                  (ENABLE_QUICK=true on the service when the account has Amazon Quick, and the
#                  console URL as the CORS and Quick embed origin; on a first deploy l4 runs once
#                  more right after the console stage to pick that URL up)
#     approvals  — plan-approval Step Functions state machine + submit Lambda
#     l6         — 12 agentic scheduler jobs, both markets (every job on the agent bar ships ENABLED;
#                  --enable-jobs also enables autonomy-promotion)
#     demo-reset — dev only: the Lambda behind the Admin Console's "Reset demo data" (seed bundled)
#     alarms     — CloudWatch Errors/Throttles alarms per Lambda
#     agentcore  — AgentCore runtimes, one per agent (A1 A2 A3 A4 A5 A7); re-run l4 and l6 after
#     gateway    — AgentCore Gateways (AWS_IAM for the console, CUSTOM_JWT for Amazon Quick)
#     quick      — sharing groups + Quick datasets (owned by retail-ops-builders), then the
#                  Quick content (deploy_quick_content: custom-SQL datasets, topic, 9 knowledge
#                  bases, space, Field Sales chat agent, 8 Flows, Campaign ROI dashboard),
#                  then discovery of Flows / dashboards for the console
#     console    — Next.js static export -> S3 + CloudFront
#     waf        — WAFv2 web ACL on the console distribution
#
# Usage:
#   deploy/deploy.sh                 # DRY-RUN the whole stack
#   deploy/deploy.sh --apply         # deploy the whole stack
#   deploy/deploy.sh --apply --only l4,events   # deploy just those stages
#   deploy/deploy.sh --apply --skip data        # deploy all but the data load
#   deploy/deploy.sh --region us-east-1 --market us --apply
#   deploy/deploy.sh --apply --enable-jobs   # also ENABLE autonomy-promotion (the rest ship enabled)
#   deploy/deploy.sh --apply --only sso-admins  # re-sync the SSO allowlist after editing config.env
#   deploy/deploy.sh --apply --env g4f2a91 --skip quick   # a second, separately named environment
#
# Environment name: --env <name> or RETAIL_OPS_ENV (default dev; 2-8 lowercase
# letters and digits). Every resource is named retail-ops-<env>-… (app/infra/naming.py),
# so environments never share a resource; dev keeps the names it always had.
# Python: $PYTHON, else the active virtual environment, else ./.venv, else python3
# (3.12+ with boto3). Runs unattended: no prompts, and the first failing stage
# stops the run with its name.
#
# Notes:
#   * The REST API id and the CloudFront URL are stable across re-deploys; the
#     script prints both at the end.
#   * Data load is a one-time step for a fresh account; use --skip data on
#     redeploys to avoid re-writing rows.
#   * Everything above is scriptable. What is NOT (no public API): the Quick MCP
#     connector, publishing and scheduling the Flows, and test runs; the
#     ordered list is "Console checklist" in docs/QUICK_BUILD.md. Re-run
#     `--only quick` after building the connector so it is linked to the agent.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export AWS_PAGER=""          # never open a pager: the run must not wait for a terminal

REGION="us-east-1"
MARKET="us"
APPLY=0
ONLY=""
SKIP=""
ENABLE_JOBS=0

ALL_STAGES=(cognito data sso-admins events lake corpus guardrails kb l4 approvals l6 demo-reset alarms agentcore gateway quick console waf)

usage() {
  sed -n '2,60p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)   APPLY=1; shift ;;
    --region)  REGION="$2"; shift 2 ;;
    --market)  MARKET="$2"; shift 2 ;;
    --only)    ONLY="$2"; shift 2 ;;
    --skip)    SKIP="$2"; shift 2 ;;
    --enable-jobs) ENABLE_JOBS=1; shift ;;
    --env)     export RETAIL_OPS_ENV="$2"; shift 2 ;;
    -h|--help) usage 0 ;;
    *) echo "unknown argument: $1" >&2; usage 1 ;;
  esac
done

# Comma-list membership test.
in_list() {  # in_list <needle> <comma-list>
  local needle="$1" list="$2" item
  IFS=',' read -ra items <<< "$list"
  for item in "${items[@]}"; do
    [[ "$item" == "$needle" ]] && return 0
  done
  return 1
}

# Fail fast on a mistyped stage name in --only / --skip.
for stage_arg in ${ONLY//,/ } ${SKIP//,/ }; do
  in_list "$stage_arg" "$(IFS=,; echo "${ALL_STAGES[*]}")" || {
    echo "ERROR: unknown stage '$stage_arg' (stages: ${ALL_STAGES[*]})" >&2
    exit 1
  }
done

should_run() {  # should_run <stage>
  local stage="$1"
  if [[ -n "$ONLY" ]]; then
    in_list "$stage" "$ONLY"
    return
  fi
  if [[ -n "$SKIP" ]] && in_list "$stage" "$SKIP"; then
    return 1
  fi
  return 0
}

# --- Python + environment name -------------------------------------------------
resolve_python() {  # sets PY: the interpreter that runs the deployers (3.12+ with boto3)
  local cand
  if [[ -n "${PYTHON:-}" ]]; then
    cand=("$PYTHON")
  else
    cand=()
    [[ -n "${VIRTUAL_ENV:-}" ]] && cand+=("$VIRTUAL_ENV/bin/python")
    cand+=("$ROOT/.venv/bin/python" python3)
  fi
  for c in "${cand[@]}"; do
    if command -v "$c" >/dev/null 2>&1 \
        && "$c" -c 'import sys, boto3; sys.exit(sys.version_info < (3, 12))' >/dev/null 2>&1; then
      PY="$(command -v "$c")"
      return 0
    fi
  done
  echo "ERROR: no Python 3.12+ with boto3 found (tried: ${cand[*]})." >&2
  echo "Create the virtual environment first: python3.12 -m venv .venv && .venv/bin/pip install -r requirements-agentcore.txt" >&2
  exit 1
}
resolve_python
export PYTHON="$PY"
"$PY" -m app.infra.naming --check            # fails fast on a bad RETAIL_OPS_ENV
ENV_NAME="$("$PY" -c 'from app.infra import naming; print(naming.ENV)')"
PREFIX="$("$PY" -c 'from app.infra import naming; print(naming.PREFIX)')"
EVENT_BUS="$("$PY" -c 'from app.infra import naming; print(naming.EVENT_BUS)')"
CONSOLE_CONFIG="$("$PY" -c 'from app.infra import naming; print(naming.config_path("console"))')"
API_NAME="${PREFIX}journeys-api"
console_url() {  # the deployed console URL from this env's console config, or ""
  [[ -f "$CONSOLE_CONFIG" ]] || return 0
  "$PY" -c "import json,sys;print(json.load(open(sys.argv[1])).get('url',''))" "$CONSOLE_CONFIG" 2>/dev/null || true
}
CONSOLE_URL_AT_START="$(console_url)"

APPLY_FLAG=""
[[ "$APPLY" -eq 1 ]] && APPLY_FLAG="--apply"
MODE=$([[ "$APPLY" -eq 1 ]] && echo APPLY || echo "DRY-RUN")

if [[ "$APPLY" -eq 1 ]] && should_run cognito && [[ -z "${RETAIL_OPS_SEED_PASSWORD:-}" ]]; then
  echo "ERROR: RETAIL_OPS_SEED_PASSWORD is required for the applied Cognito seed-user stage." >&2
  echo "Set it in the environment or source the git-ignored deploy/config.env." >&2
  exit 1
fi

echo "======================================================================"
echo " Retail Ops deploy  mode=$MODE  env=$ENV_NAME  region=$REGION  market=$MARKET"
[[ -n "$ONLY" ]] && echo " only=$ONLY"
[[ -n "$SKIP" ]] && echo " skip=$SKIP"
echo "======================================================================"

# Confirm identity so an operator can see WHICH account they're deploying into.
if [[ "$APPLY" -eq 1 ]]; then
  echo "-- caller identity --"
  aws sts get-caller-identity --output text --query '[Account,Arn]' || {
    echo "ERROR: could not resolve AWS identity (aws sts get-caller-identity failed)." >&2
    exit 1
  }
  echo
fi

CURRENT_STAGE="setup"
on_exit() {
  local rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "ERROR: deploy stopped in stage \"$CURRENT_STAGE\" (exit $rc)." >&2
  fi
  return $rc
}
trap on_exit EXIT

run_stage() {  # run_stage <label> <command...>
  local label="$1"; shift
  CURRENT_STAGE="$label"
  echo "----------------------------------------------------------------------"
  echo ">> stage: $label"
  echo "----------------------------------------------------------------------"
  "$@"
  echo
}

if should_run cognito; then
  # --federate provisions the SSO surface: Hosted UI domain + the Hosted-UI OAuth
  # client (callbacks registered for the deployed console and localhost), plus the
  # federated IdP when its inputs are available (OIDC: RETAIL_OPS_IDP_SECRET or
  # RETAIL_OPS_IDP_CLIENT_ID+SECRET; SAML: RETAIL_OPS_IDP_METADATA_URL). Safe to run
  # without them — the IdP is simply skipped. The provider is configuration
  # (RETAIL_OPS_IDP_NAME / RETAIL_OPS_IDP_PROTOCOL): the pilot uses Amazon's internal
  # directory, and `cognito_setup --remove-idp --apply` detaches it before any
  # external release so a customer IdP can take its place.
  # Opt out of the whole SSO surface with RETAIL_OPS_ENABLE_SSO=0.
  SSO_FLAG=""
  [[ "${RETAIL_OPS_ENABLE_SSO:-1}" == "1" ]] && SSO_FLAG="--federate"
  run_stage "cognito (staff user pool${SSO_FLAG:+ + SSO hosted UI})" \
    "$PY" -m app.infra.cognito_setup $APPLY_FLAG $SSO_FLAG
fi

if should_run data; then
  # --rebase-now: the pack's frozen clock (SEED_NOW) lands on today, so promised times,
  # due dates and "today" views read as current instead of the seed's September dates
  run_stage "data (L1 DynamoDB load, market=$MARKET)" \
    "$PY" app/services/l1_data/ddb_load.py --market "$MARKET" --region "$REGION" --rebase-now $APPLY_FLAG
fi

if should_run sso-admins; then
  # SSO allowlist (spec §8.1): only the enterprise-IdP aliases in
  # RETAIL_OPS_SSO_ADMINS (deploy/config.env) get the Administrator role. Syncs
  # their PLAT_Users rows (adds listed aliases, removes delisted ones, never
  # touches other rows). Runs after `data`: PLAT_Users must exist, and a data
  # reload must not drop the rows. Unset variable = no change.
  run_stage "sso-admins (Administrator rows for the SSO allowlist, market=$MARKET)" \
    "$PY" -m app.infra.sso_admins --market "$MARKET" --region "$REGION" $APPLY_FLAG
fi

if should_run events; then
  run_stage "events ($EVENT_BUS + subscriber)" \
    "$PY" -m app.infra.deploy_events $APPLY_FLAG
fi

if should_run lake; then
  # S3 event lake: EventBridge($EVENT_BUS) -> Firehose -> S3 -> Glue/Athena (spec §4).
  # --backfill loads existing DynamoDB events so Athena has rows immediately; the
  # analytics mirrors and derived roll-ups (rdr/csi/marketintel/leads/campaigns)
  # give A5 and the OEM views their Athena/Quick tables.
  run_stage "lake (S3 event lake for A5/QuickSight — Firehose + Glue + Athena)" \
    "$PY" -m app.infra.deploy_lake $APPLY_FLAG ${APPLY_FLAG:+--backfill}
  run_stage "lake (analytics mirrors -> Athena tables)" \
    "$PY" -m app.infra.deploy_lake_analytics $APPLY_FLAG
  run_stage "lake (derived roll-ups: rdr/csi/marketintel/leads)" \
    "$PY" -m app.infra.deploy_lake_rollups $APPLY_FLAG
fi

if should_run corpus; then
  # Knowledge corpus (538 Markdown docs with source_ref front-matter) -> s3://<lake>/quick-index/.
  # Feeds the Bedrock Knowledge Base (kb) and the Amazon Quick knowledge bases.
  run_stage "corpus (knowledge corpus -> S3 quick-index/)" \
    "$PY" -m app.infra.deploy_quick_index $APPLY_FLAG
fi

if should_run guardrails; then
  # Bedrock Guardrail (denied topics, content/word filters, PII, grounding). Writes
  # guardrail-config.json that l4/l6/agentcore read into RETAIL_OPS_GUARDRAIL_ID/_VERSION.
  run_stage "guardrails (Bedrock Guardrail for every model call)" \
    "$PY" -m app.infra.deploy_guardrails $APPLY_FLAG
fi

if should_run kb; then
  # Bedrock Knowledge Base over s3://<lake>/quick-index/ (Titan v2 + S3 Vectors).
  # --sidecars/--ingest only on apply: writes metadata files and indexes the corpus.
  run_stage "kb (Bedrock Knowledge Base over the knowledge corpus)" \
    "$PY" -m app.infra.deploy_knowledge_base $APPLY_FLAG ${APPLY_FLAG:+--sidecars --ingest}
fi

if should_run l4; then
  run_stage "l4 (IAM roles + authorizer + service + REST API)" \
    "$PY" -m app.infra.deploy_l4 $APPLY_FLAG
fi

if should_run approvals; then
  run_stage "approvals (plan-approval state machine)" \
    "$PY" -m app.infra.deploy_approvals $APPLY_FLAG
fi

if should_run l6; then
  ENABLE_FLAG=""
  [[ "$ENABLE_JOBS" -eq 1 ]] && ENABLE_FLAG="--enable-all"
  run_stage "l6 (12 agentic scheduler jobs, both markets${ENABLE_FLAG:+ — all ENABLED})" \
    "$PY" -m app.infra.deploy_l6 $APPLY_FLAG $ENABLE_FLAG
fi

if should_run demo-reset; then
  # The Admin Console's "Demo data" card starts this Lambda (dev only; skipped elsewhere).
  # Its zip bundles the seed: re-run this stage after regenerating sample-data/synthetic.
  run_stage "demo-reset (Admin Console demo reset Lambda, dev only)" \
    "$PY" -m app.infra.deploy_demo_reset $APPLY_FLAG
fi

if should_run alarms; then
  run_stage "alarms (per-Lambda Errors + Throttles CloudWatch alarms)" \
    "$PY" -m app.infra.deploy_alarms $APPLY_FLAG
fi

if should_run agentcore; then
  # One runtime per agent (ADR 0009): each bundle = that agent's own code closure +
  # arm64 wheels for its profile (boto3; A3 also strands-agents). Writes
  # agentcore-config.json (schema 2); the service Lambda and the job Lambdas learn
  # the runtime ARNs on the next l4 / l6 run, so both are re-run here when they
  # are in the same deploy.
  run_stage "agentcore (AgentCore runtimes, one per agent: A1 A2 A3 A4 A5 A7)" \
    "$PY" -m app.infra.deploy_agentcore $APPLY_FLAG
  if should_run l4 && [[ "$APPLY" -eq 1 ]]; then
    run_stage "l4 (re-wire the runtime map onto the service Lambda)" \
      "$PY" -m app.infra.deploy_l4 --apply
  fi
  if should_run l6 && [[ "$APPLY" -eq 1 ]]; then
    ENABLE_FLAG=""
    [[ "$ENABLE_JOBS" -eq 1 ]] && ENABLE_FLAG="--enable-all"
    run_stage "l6 (re-wire each job's owner runtime onto the job Lambdas)" \
      "$PY" -m app.infra.deploy_l6 --apply $ENABLE_FLAG
  fi
fi

if should_run gateway; then
  # AgentCore Gateway: cross-persona data bridge (spec §6). Non-OEM personas read
  # OEM-owned data only through this Gateway's scoped MCP tools, never directly.
  run_stage "gateway (AgentCore Gateway — cross-persona OEM read bridge)" \
    "$PY" -m app.infra.deploy_gateway $APPLY_FLAG
  # Second gateway with a CUSTOM_JWT authorizer for Amazon Quick's MCP connector
  # (client-credentials token from the retail-ops-quick-mcp Cognito client).
  run_stage "gateway (CUSTOM_JWT gateway for the Amazon Quick MCP connector)" \
    "$PY" -m app.infra.deploy_gateway_quick $APPLY_FLAG
fi

if should_run quick; then
  # Sharing groups decide who can open Quick content; Quick content is not
  # row-scoped (row-level security removed by user decision, 2026-10-21).
  # Groups first: the datasets step grants retail-ops-builders owner on the
  # data source and datasets, and skips that grant if the group is missing.
  run_stage "quick (sharing groups and memberships)" \
    "$PY" -m app.infra.deploy_quick_groups $APPLY_FLAG
  run_stage "quick (Athena data source + one dataset per lake table, owned by retail-ops-builders)" \
    "$PY" -m app.infra.deploy_quick_datasets $APPLY_FLAG
  # Quick content through the Quick API (docs/QUICK_BUILD.md §1-§8): needs the
  # groups, the lake datasets and the corpus (corpus stage). Links the MCP
  # connector to the space and agent once it has been built in the console.
  run_stage "quick (content: topic, knowledge bases, space, chat agent, Flows, dashboard)" \
    "$PY" -m app.infra.deploy_quick_content $APPLY_FLAG
  # Amazon Quick (quicksight) resource discovery -> quick-config.json (Flows / dashboards for the console).
  run_stage "quick (discover Flows / dashboards / embed user for the console)" \
    "$PY" -m app.infra.deploy_quick $APPLY_FLAG
fi

if should_run console; then
  # Operator console -> S3 + CloudFront. Build the static export wired to the
  # CURRENT API base (l4 recreates the API each apply, so resolve it now), then
  # upload + invalidate. Needs npm; skips the build gracefully if npm is absent.
  run_console() {
    local api_id api_base
    api_id=$(aws apigateway get-rest-apis --region "$REGION" \
      --query "items[?name=='${API_NAME}'].id | [0]" --output text 2>/dev/null || true)
    if [[ -z "${api_id:-}" || "$api_id" == "None" ]]; then
      echo "  SKIP: no REST API found — deploy the l4 stage first."
      return 0
    fi
    api_base="https://${api_id}.execute-api.${REGION}.amazonaws.com/dev"
    echo "  building console static export against $api_base"
    # Bake the Cognito app-client ids so the console's real sign-in works. Select
    # each by NAME: the pool has two clients once SSO is wired, and picking
    # [0] would hand the native flow the Hosted-UI client (or vice versa).
    local pool_id cog_client sso_client hosted_ui=""
    pool_id=$(aws cognito-idp list-user-pools --max-results 20 --region "$REGION" \
      --query "UserPools[?Name=='${PREFIX}staff'].Id | [0]" --output text 2>/dev/null)
    if [[ -n "${pool_id:-}" && "$pool_id" != "None" ]]; then
      cog_client=$(aws cognito-idp list-user-pool-clients --user-pool-id "$pool_id" \
        --region "$REGION" --max-results 60 \
        --query "UserPoolClients[?ClientName=='${PREFIX}staff-web'].ClientId | [0]" --output text 2>/dev/null)
      sso_client=$(aws cognito-idp list-user-pool-clients --user-pool-id "$pool_id" \
        --region "$REGION" --max-results 60 \
        --query "UserPoolClients[?ClientName=='${PREFIX}staff-hostedui'].ClientId | [0]" --output text 2>/dev/null)
      # Hosted UI base, only if a domain is actually provisioned on the pool.
      local domain
      domain=$(aws cognito-idp describe-user-pool --user-pool-id "$pool_id" --region "$REGION" \
        --query 'UserPool.Domain' --output text 2>/dev/null)
      [[ -n "${domain:-}" && "$domain" != "None" ]] \
        && hosted_ui="https://${domain}.auth.${REGION}.amazoncognito.com"
    fi
    [[ "${cog_client:-None}" == "None" ]] && cog_client=""
    [[ "${sso_client:-None}" == "None" ]] && sso_client=""
    # The EXTERNAL identity providers on the pool decide whether an SSO button is
    # shown at all. With none, the Hosted UI only renders Cognito's own
    # username/password form — a second prompt for credentials the console
    # already collects — so the console shows just its own form instead.
    local sso_idps=""
    if [[ -n "$pool_id" && "$pool_id" != "None" ]]; then
      sso_idps=$(aws cognito-idp list-identity-providers --user-pool-id "$pool_id" \
        --max-results 20 --region "$REGION" \
        --query "Providers[?ProviderName!='COGNITO'].ProviderName" --output text 2>/dev/null \
        | tr '\t' ',' | sed 's/,$//')
      [[ "$sso_idps" == "None" ]] && sso_idps=""
    fi
    if [[ -z "$hosted_ui" ]]; then
      echo "  SSO: no hosted UI (run: python3 -m app.infra.cognito_setup --federate --apply)"
    elif [[ -z "$sso_idps" ]]; then
      echo "  SSO: hosted UI present but NO external IdP — SSO button hidden"
      echo "       (password sign-in only; add an IdP to turn SSO on)"
    else
      echo "  SSO: hosted UI $hosted_ui — IdPs: $sso_idps"
    fi
    if command -v npm >/dev/null 2>&1; then
      # ENABLE_QUICK=false (no Amazon Quick in the account) hides the console's Quick panels.
      local build_log
      local tmp_dir="${TMPDIR:-/tmp}"
      build_log="$(mktemp "${tmp_dir%/}/retail_ops_console_build.XXXXXX")"
      ( cd app/console && rm -rf out \
        && RETAIL_OPS_STATIC_EXPORT=1 NEXT_PUBLIC_ENABLE_QUICK="${ENABLE_QUICK:-true}" \
           NEXT_PUBLIC_RETAIL_OPS_API="$api_base" \
           NEXT_PUBLIC_COGNITO_CLIENT_ID="${cog_client:-}" \
           NEXT_PUBLIC_COGNITO_HOSTED_UI="$hosted_ui" \
           NEXT_PUBLIC_COGNITO_HOSTED_UI_CLIENT_ID="${sso_client:-}" \
           NEXT_PUBLIC_SSO_IDPS="$sso_idps" \
           NEXT_PUBLIC_COGNITO_REGION="$REGION" \
           npm run build >"$build_log" 2>&1 ) || {
        echo "  console build FAILED (log: $build_log); last lines:"
        tail -n 40 "$build_log" | sed 's/^/    /'
        if [[ "$APPLY" -eq 1 ]]; then return 1; fi
        echo "  (dry run: continuing without a build)"; return 0
      }
      echo "  console build OK (log: $build_log)"
    else
      echo "  npm not found — skipping build; will upload whatever is in app/console/out"
    fi
    "$PY" -m app.infra.deploy_console $APPLY_FLAG
  }
  run_stage "console (operator console -> S3 + CloudFront)" run_console
  # l4 sets the console URL as the API's CORS origin and the Quick embed origin.
  # On a first deploy the console stage runs after l4, so wire the new URL now.
  if [[ "$APPLY" -eq 1 ]] && should_run l4 && [[ "$(console_url)" != "$CONSOLE_URL_AT_START" ]]; then
    run_stage "l4 (wire the new console URL as the API CORS and embed origin)" \
      "$PY" -m app.infra.deploy_l4 --apply
  fi
fi

if should_run waf; then
  # Edge protection for the console distribution: AWS managed rule groups + a
  # per-IP rate limit. Runs after `console` because it attaches to the
  # distribution that stage creates. Restrict by source IP with
  # RETAIL_OPS_WAF_ALLOW_CIDRS="1.2.3.0/24,..." (off by default — an allowlist blocks
  # everything else, which is easy to lock yourself out with mid-demo).
  run_stage "waf (WAFv2 web ACL -> console CloudFront)" \
    "$PY" -m app.infra.deploy_waf $APPLY_FLAG
fi

echo "======================================================================"
if [[ "$APPLY" -eq 1 ]]; then
  # The API base URL is stable across --apply (deploy_l4 reuses the REST API id);
  # still surface it so the demo links and smoke command are copy-pasteable.
  if should_run l4; then
    API_ID=$(aws apigateway get-rest-apis --region "$REGION" \
      --query "items[?name=='${API_NAME}'].id | [0]" --output text 2>/dev/null || true)
    if [[ -n "${API_ID:-}" && "$API_ID" != "None" ]]; then
      echo " API base URL: https://${API_ID}.execute-api.${REGION}.amazonaws.com/dev"
      echo " Verify:       python3 -m app.demo.run_demo --smoke --api https://${API_ID}.execute-api.${REGION}.amazonaws.com/dev"
    fi
  fi
  if should_run console && [[ -f "$CONSOLE_CONFIG" ]]; then
    CONSOLE_URL="$(console_url)"
    [[ -n "${CONSOLE_URL:-}" ]] && echo " Console URL:  $CONSOLE_URL  (persona access: docs/ACCESS_BY_PERSONA.md)"
  fi
  echo " deploy complete."
else
  echo " DRY-RUN complete. Re-run with --apply to deploy."
fi
echo "======================================================================"
