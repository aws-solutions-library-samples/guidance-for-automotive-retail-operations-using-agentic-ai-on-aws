#!/usr/bin/env bash
# run-validation.sh — run deploy/guidance-deploy.sh in AWS CodeBuild from a local
# snapshot (pre-publication mode), check on it, and clean the CodeBuild side up.
#
# Run it from a clone of this repository on a workstation (Linux or macOS) with
# AWS credentials for the VALIDATION account (for example AWS_PROFILE=...).
# It refuses to run when the credentials belong to any other account.
#
#   run      bundle the committed HEAD (git bundle), upload it to a new private S3
#            bucket, create or update the CodeBuild service role and project,
#            start the build, and print the build id and its log group/stream
#   status   show a build's status and the environment name it deployed
#   cleanup  list (or with --yes delete) the CodeBuild project, its service role
#            and the snapshot buckets; the deployed stack itself is removed with
#            deploy/destroy.sh --env <env> (see the end of the cleanup output)
#
# Usage:
#   deploy/guidance-validation/run-validation.sh run     --account <account-id>
#   deploy/guidance-validation/run-validation.sh status  --account <account-id> [--build <build-id>]
#   deploy/guidance-validation/run-validation.sh cleanup --account <account-id>          # list only
#   deploy/guidance-validation/run-validation.sh cleanup --account <account-id> --yes    # delete
#
# The CodeBuild service role gets the AWS managed AdministratorAccess policy: the
# deployment creates IAM roles and policies and resources in some twenty
# services (Lambda, DynamoDB, Cognito, Bedrock, AgentCore, S3 Vectors,
# CloudFront, WAF, ...), so a scoped policy would be as broad in practice. Use a
# dedicated validation account, and run cleanup when the validation is done.
set -euo pipefail

# --- parameters -------------------------------------------------------------
export AWS_REGION="us-east-1"
export AWS_DEFAULT_REGION="$AWS_REGION"
export AWS_PAGER=""
PROJECT_NAME="guidance-validation-retail-ops"
ROLE_NAME="guidance-validation-retail-ops-role"
BUCKET_PREFIX="guidance-val"                               # bucket: guidance-val-<account>-<yyyymmdd>
IMAGE="aws/codebuild/amazonlinux-x86_64-standard:5.0"
COMPUTE_TYPE="${COMPUTE_TYPE:-BUILD_GENERAL1_MEDIUM}"
TIMEOUT_MINUTES=120
BUILDSPEC="deploy/guidance-validation/buildspec.yml"
LOG_GROUP="/aws/codebuild/${PROJECT_NAME}"
ADMIN_POLICY_ARN="arn:aws:iam::aws:policy/AdministratorAccess"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MODE="${1:-}"; [[ $# -gt 0 ]] && shift
EXPECTED_ACCOUNT=""; BUILD_ID=""; YES=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --account) EXPECTED_ACCOUNT="$2"; shift 2 ;;
    --build)   BUILD_ID="$2"; shift 2 ;;
    --yes)     YES=1; shift ;;
    -h|--help) sed -n '2,28p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
case "$MODE" in
  run|status|cleanup) ;;
  -h|--help|"") sed -n '2,28p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) echo "unknown mode: $MODE (run, status or cleanup)" >&2; exit 2 ;;
esac
say() { echo; echo "== $*"; }

# --- identity gate ---------------------------------------------------------
[[ "$EXPECTED_ACCOUNT" =~ ^[0-9]{12}$ ]] || { echo "ERROR: pass --account <12-digit validation account id>" >&2; exit 2; }
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)" \
  || { echo "ERROR: no AWS credentials (set AWS_PROFILE for the validation account)" >&2; exit 1; }
if [[ "$ACCOUNT_ID" != "$EXPECTED_ACCOUNT" ]]; then
  echo "ERROR: these credentials belong to account $ACCOUNT_ID, not $EXPECTED_ACCOUNT; refusing to run." >&2
  exit 1
fi
echo "account: $ACCOUNT_ID   region: $AWS_REGION   mode: $MODE"

PY="$(command -v python3)" || { echo "ERROR: python3 is required (standard library only)" >&2; exit 1; }

env_name_of() {  # env_name_of <build-id>: the GUIDANCE_ENV_NAME the build printed, or ""
  local stream="${1#*:}"
  aws logs filter-log-events --log-group-name "$LOG_GROUP" --log-stream-names "$stream" \
    --filter-pattern '"GUIDANCE_ENV_NAME="' --query 'events[0].message' --output text 2>/dev/null \
    | sed -n 's/.*GUIDANCE_ENV_NAME=\([a-z0-9]*\).*/\1/p' | head -n 1
}

# =============================================================================
if [[ "$MODE" == "run" ]]; then
  cd "$ROOT"
  BRANCH="$(git rev-parse --abbrev-ref HEAD)"
  [[ "$BRANCH" != "HEAD" ]] || { echo "ERROR: check out a branch (the bundle carries one branch ref)" >&2; exit 1; }
  if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    echo "note: the working tree has uncommitted changes; the build uses the committed HEAD only."
  fi
  STAMP="$(date -u +%Y%m%d)"
  BUCKET="${BUCKET_PREFIX}-${ACCOUNT_ID}-${STAMP}"
  KEY="source/$(date -u +%Y%m%dT%H%M%SZ)-$(git rev-parse --short HEAD)/source.zip"

  say "1. snapshot bucket s3://$BUCKET (private, encrypted, TLS only)"
  if aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
    echo "  exists"
  else
    aws s3api create-bucket --bucket "$BUCKET" >/dev/null
    echo "  created"
  fi
  aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  aws s3api put-bucket-ownership-controls --bucket "$BUCKET" \
    --ownership-controls 'Rules=[{ObjectOwnership=BucketOwnerEnforced}]'
  aws s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}'
  aws s3api put-bucket-policy --bucket "$BUCKET" --policy "$("$PY" -c 'import json,sys; b=sys.argv[1]; print(json.dumps({"Version":"2012-10-17","Statement":[{"Sid":"TlsOnly","Effect":"Deny","Principal":"*","Action":"s3:*","Resource":[f"arn:aws:s3:::{b}",f"arn:aws:s3:::{b}/*"],"Condition":{"Bool":{"aws:SecureTransport":"false"}}}]}))' "$BUCKET")"

  say "2. package HEAD ($(git rev-parse --short HEAD) on $BRANCH) as a git bundle"
  TMP="$(mktemp -d "${TMPDIR:-/tmp}/guidance-validation.XXXXXX")"
  trap 'rm -rf "$TMP"' EXIT
  git bundle create "$TMP/repo.bundle" "$BRANCH"
  git bundle verify "$TMP/repo.bundle" >/dev/null
  git archive --format=zip -o "$TMP/source.zip" HEAD deploy/guidance-deploy.sh "$BUILDSPEC"
  "$PY" -c 'import sys, zipfile; z = zipfile.ZipFile(sys.argv[1], "a", zipfile.ZIP_STORED); z.write(sys.argv[2], "repo.bundle"); z.close()' \
    "$TMP/source.zip" "$TMP/repo.bundle"
  aws s3 cp "$TMP/source.zip" "s3://$BUCKET/$KEY" --only-show-errors
  echo "  uploaded s3://$BUCKET/$KEY ($(du -h "$TMP/source.zip" | cut -f1))"

  say "3. CodeBuild service role $ROLE_NAME (AdministratorAccess: see the header)"
  TRUST="$("$PY" -c 'import json,sys; print(json.dumps({"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"codebuild.amazonaws.com"},"Action":"sts:AssumeRole","Condition":{"StringEquals":{"aws:SourceAccount":sys.argv[1]}}}]}))' "$ACCOUNT_ID")"
  if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
    aws iam update-assume-role-policy --role-name "$ROLE_NAME" --policy-document "$TRUST"
    echo "  updated"
  else
    aws iam create-role --role-name "$ROLE_NAME" --assume-role-policy-document "$TRUST" \
      --description "CodeBuild role for the Guidance sample code validation (full-stack deploy)" >/dev/null
    echo "  created; waiting for IAM propagation"
    sleep 15
  fi
  aws iam attach-role-policy --role-name "$ROLE_NAME" --policy-arn "$ADMIN_POLICY_ARN"
  ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

  say "4. CodeBuild project $PROJECT_NAME"
  PROJECT_JSON="$TMP/project.json"
  "$PY" - "$PROJECT_JSON" <<PYEOF
import json, sys
json.dump({
    "name": "$PROJECT_NAME",
    "description": "Guidance sample code validation: deploy/guidance-deploy.sh from a git bundle",
    "source": {"type": "S3", "location": "$BUCKET/$KEY", "buildspec": "$BUILDSPEC"},
    "artifacts": {"type": "NO_ARTIFACTS"},
    "environment": {
        "type": "LINUX_CONTAINER", "image": "$IMAGE", "computeType": "$COMPUTE_TYPE",
        "privilegedMode": True, "imagePullCredentialsType": "CODEBUILD",
        "environmentVariables": [
            {"name": "REPO_URL", "value": "repo.bundle", "type": "PLAINTEXT"},
            {"name": "REPO_BRANCH", "value": "$BRANCH", "type": "PLAINTEXT"},
        ],
    },
    "serviceRole": "$ROLE_ARN",
    "timeoutInMinutes": $TIMEOUT_MINUTES,
    "logsConfig": {"cloudWatchLogs": {"status": "ENABLED", "groupName": "$LOG_GROUP"}},
}, open(sys.argv[1], "w"))
PYEOF
  if aws codebuild batch-get-projects --names "$PROJECT_NAME" --query 'projects[0].name' --output text 2>/dev/null \
      | grep -qx "$PROJECT_NAME"; then
    aws codebuild update-project --cli-input-json "file://$PROJECT_JSON" >/dev/null
    echo "  updated"
  else
    for attempt in 1 2 3 4 5 6; do
      if aws codebuild create-project --cli-input-json "file://$PROJECT_JSON" >/dev/null 2>"$TMP/create.err"; then
        echo "  created"; break
      fi
      grep -Eq "not authorized to perform: sts:AssumeRole|InvalidInputException" "$TMP/create.err" && [[ $attempt -lt 6 ]] \
        || { cat "$TMP/create.err" >&2; exit 1; }
      echo "  waiting for the service role to propagate ($attempt/6)"; sleep 10
    done
  fi

  say "5. start the build"
  BUILD_ID="$(aws codebuild start-build --project-name "$PROJECT_NAME" --query 'build.id' --output text)"
  STREAM="${BUILD_ID#*:}"
  echo "  build id:   $BUILD_ID"
  echo "  log group:  $LOG_GROUP"
  echo "  log stream: $STREAM"
  echo
  echo "Follow the log:  aws logs tail $LOG_GROUP --log-stream-names $STREAM --follow --region $AWS_REGION"
  echo "Check status:    deploy/guidance-validation/run-validation.sh status --account $ACCOUNT_ID --build $BUILD_ID"
  echo "Console:         https://${AWS_REGION}.console.aws.amazon.com/codesuite/codebuild/${ACCOUNT_ID}/projects/${PROJECT_NAME}/build/${BUILD_ID/:/%3A}/log"
  exit 0
fi

# =============================================================================
if [[ "$MODE" == "status" ]]; then
  if [[ -z "$BUILD_ID" ]]; then
    BUILD_ID="$(aws codebuild list-builds-for-project --project-name "$PROJECT_NAME" --query 'ids[0]' --output text)"
  fi
  aws codebuild batch-get-builds --ids "$BUILD_ID" --output table \
    --query 'builds[0].{id:id,status:buildStatus,phase:currentPhase,start:startTime,end:endTime}'
  # shellcheck disable=SC2016  # the backticks are a JMESPath literal, not a shell expansion
  aws codebuild batch-get-builds --ids "$BUILD_ID" --output text \
    --query 'builds[0].phases[?phaseStatus!=`SUCCEEDED`].[phaseType,phaseStatus]' | sed 's/^/  phase /'
  ENV_NAME="$(env_name_of "$BUILD_ID")"
  echo "environment name: ${ENV_NAME:-not printed yet}"
  [[ -n "$ENV_NAME" ]] && echo "stack teardown:   deploy/destroy.sh --env $ENV_NAME --confirm --account $ACCOUNT_ID --include-data"
  exit 0
fi

# =============================================================================
# cleanup
ENVS=""
if aws codebuild batch-get-projects --names "$PROJECT_NAME" --query 'projects[0].name' --output text 2>/dev/null \
    | grep -qx "$PROJECT_NAME"; then
  for b in $(aws codebuild list-builds-for-project --project-name "$PROJECT_NAME" --query 'ids[]' --output text); do
    e="$(env_name_of "$b")"; [[ -n "$e" ]] && ENVS="$ENVS $e"
  done
  HAVE_PROJECT=1
else
  HAVE_PROJECT=0
fi
BUCKETS="$(aws s3api list-buckets --query "Buckets[?starts_with(Name, '${BUCKET_PREFIX}-${ACCOUNT_ID}-')].Name" --output text)"
HAVE_ROLE=0; aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1 && HAVE_ROLE=1
say "CodeBuild side of the validation"
echo "  project:  $([[ $HAVE_PROJECT -eq 1 ]] && echo "$PROJECT_NAME" || echo none)"
echo "  role:     $([[ $HAVE_ROLE -eq 1 ]] && echo "$ROLE_NAME" || echo none)"
echo "  buckets:  ${BUCKETS:-none}"
echo "  log group $LOG_GROUP is kept (build evidence); delete it with: aws logs delete-log-group --log-group-name $LOG_GROUP"
if [[ "$YES" -ne 1 ]]; then
  echo; echo "Nothing deleted. Re-run with --yes to delete the items above."
else
  if [[ $HAVE_PROJECT -eq 1 ]]; then
    aws codebuild delete-project --name "$PROJECT_NAME" && echo "  deleted project $PROJECT_NAME"
  fi
  if [[ $HAVE_ROLE -eq 1 ]]; then
    for p in $(aws iam list-attached-role-policies --role-name "$ROLE_NAME" --query 'AttachedPolicies[].PolicyArn' --output text); do
      aws iam detach-role-policy --role-name "$ROLE_NAME" --policy-arn "$p"
    done
    for p in $(aws iam list-role-policies --role-name "$ROLE_NAME" --query 'PolicyNames[]' --output text); do
      aws iam delete-role-policy --role-name "$ROLE_NAME" --policy-name "$p"
    done
    aws iam delete-role --role-name "$ROLE_NAME" && echo "  deleted role $ROLE_NAME"
  fi
  for b in $BUCKETS; do
    aws s3 rm "s3://$b" --recursive --only-show-errors
    aws s3api delete-bucket --bucket "$b" && echo "  deleted bucket $b"
  done
fi
say "The deployed stack"
if [[ -n "${ENVS// /}" ]]; then
  for e in $ENVS; do
    echo "  env $e: deploy/destroy.sh --env $e                                   # dry run first"
    echo "          deploy/destroy.sh --env $e --confirm --account $ACCOUNT_ID --include-data"
  done
else
  echo "  no environment name found in the build logs; run: deploy/destroy.sh --env <env> --confirm --account $ACCOUNT_ID --include-data"
fi
echo "  (run destroy.sh from a clone with .venv set up, with the same credentials, BEFORE deleting the log group)"
