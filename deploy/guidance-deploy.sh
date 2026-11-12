#!/usr/bin/env bash
# guidance-deploy.sh — one-click, no-input deployment of Guidance for Automotive
# Retail Operations with Agentic AI on AWS, for the AWS Guidance automated sample
# code validation.
#
# Runs on Linux (AWS CodeBuild, Linux container, image
# aws/codebuild/amazonlinux-x86_64-standard:5.0 = Amazon Linux 2023 x86_64) and
# needs only AWS credentials for the target account. It follows the "Deploy the
# Guidance" section of README.md step by step: install what the image lacks,
# clone the repository, create the Python virtual environment, enable Bedrock
# model access, install the console dependencies, deploy with
# deploy/bootstrap.sh, verify with deploy/verify.sh, switch the development
# token off, and print the console URL and the API base.
#
# Every resource is named retail-ops-<env>-… with a generated environment name
# (printed as GUIDANCE_ENV_NAME=…), so the deployment never collides with
# another one in the same account. Tear it down with
#   deploy/destroy.sh --env <env> --confirm --account <account-id> --include-data
# AWS Quick is skipped (it needs a paid Enterprise subscription and console
# steps that have no API); see "Optional: AWS Quick" in README.md.
set -euo pipefail

# =============================================================================
# Parameters (all static inputs are defined here; the script takes no input)
# =============================================================================
# Must match the published GitHub repository. REPO_URL and REPO_BRANCH may be
# overridden from the environment (a pre-publication run clones a git bundle).
REPO_URL="${REPO_URL:-https://github.com/aws-solutions-library-samples/guidance-for-automotive-retail-operations-using-agentic-ai-on-aws.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
export AWS_REGION="us-east-1"
export AWS_DEFAULT_REGION="$AWS_REGION"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
RUN_ID="$(date -u +%Y%m%d%H%M%S)"
# Environment name: "g" + 6 random hex characters, e.g. g4f2a91. Every resource
# the deployment creates carries it (retail-ops-<env>-…, retail_ops_<env>_…,
# app-bus-<env>, and the account id in every S3 bucket name). Hex digits cannot
# spell a word Cognito refuses in a domain prefix ("aws", "amazon", "cognito").
export RETAIL_OPS_ENV="${RETAIL_OPS_ENV:-g$(od -An -N3 -tx1 /dev/urandom | tr -d ' \n')}"
MARKETS="us"                                   # synthetic market packs to load (us, or us,de)
SKIP_STAGES="quick"                            # AWS Quick: optional add-on, not deployed here
export ENABLE_QUICK="false"                    # console hides its AWS Quick panels
export RETAIL_OPS_ENABLE_SSO="0"               # no federated sign-in (needs an external identity provider)
export BEDROCK_USE_CASE_EMAIL="wwso-guidance-deployments-ignore@amazon.com"   # Bedrock use case form contact
PYTHON_VERSION="3.12"                          # deployers and the agent runtimes use Python 3.12
WORK_DIR="${WORK_DIR:-${TMPDIR:-/tmp}/guidance-retail-ops-${RUN_ID}}"
# Bedrock models (base model ids from the Bedrock model cards; the solution calls
# Claude through the us. cross-Region inference profiles of the two Claude models):
#   anthropic.claude-opus-4-5-20251101-v1:0    grounded phrasing (RETAIL_OPS_BEDROCK_MODEL)
#   anthropic.claude-sonnet-4-5-20250929-v1:0  Sales Agent reasoning loop (RETAIL_OPS_A3_AGENT_MODEL)
#   amazon.titan-embed-text-v2:0               Knowledge Base embeddings
export AWS_PAGER=""                            # never open a pager: no terminal in CodeBuild

# =============================================================================
STEP="0. start"
DEV_TOKEN_CLEANUP=0
banner() { STEP="$*"; echo; echo "=================================================================="; echo "== $*"; echo "=================================================================="; }
on_exit() {
  local rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "ERROR: guidance-deploy.sh failed in step \"$STEP\" (exit $rc)." >&2
    if [[ "$DEV_TOKEN_CLEANUP" -eq 1 ]]; then
      echo "Switching the development token off before exiting (safe default)." >&2
      python3 -m app.infra.deploy_l4 --dev-token off --apply >&2 || true
    fi
    echo "GUIDANCE_ENV_NAME=${RETAIL_OPS_ENV}" >&2
    echo "Tear down: deploy/destroy.sh --env ${RETAIL_OPS_ENV} --confirm --account ${ACCOUNT_ID} --include-data" >&2
  fi
  return $rc
}
trap on_exit EXIT

banner "0. parameters"
echo "  account:     $ACCOUNT_ID"
echo "  region:      $AWS_REGION"
echo "  repository:  $REPO_URL ($REPO_BRANCH)"
echo "  environment: $RETAIL_OPS_ENV  (resources named retail-ops-${RETAIL_OPS_ENV}-*)"
echo "  markets:     $MARKETS    skipped stages: $SKIP_STAGES"
echo "  work dir:    $WORK_DIR"
echo "GUIDANCE_ENV_NAME=${RETAIL_OPS_ENV}"

# -----------------------------------------------------------------------------
banner "1. install packages the image does not provide"
SUDO=""
if [[ "$(id -u)" -ne 0 ]] && command -v sudo >/dev/null 2>&1; then SUDO="sudo"; fi
python_ok() {  # python_ok <interpreter>: it runs and is exactly $PYTHON_VERSION
  "$1" -c "import sys; sys.exit(sys.version_info[:2] != tuple(map(int, '${PYTHON_VERSION}'.split('.'))))" >/dev/null 2>&1
}
PYTHON_BIN=""
if command -v "python${PYTHON_VERSION}" >/dev/null 2>&1 && python_ok "python${PYTHON_VERSION}"; then
  PYTHON_BIN="$(command -v "python${PYTHON_VERSION}")"
elif command -v pyenv >/dev/null 2>&1; then
  # CodeBuild images ship several Pythons through pyenv; use an installed 3.12 directly.
  PYENV_PY="$(pyenv versions --bare 2>/dev/null | grep -E "^${PYTHON_VERSION//./\\.}(\\.[0-9]+)?$" | sort -V | tail -n 1 || true)"
  if [[ -n "$PYENV_PY" ]] && python_ok "$(pyenv prefix "$PYENV_PY")/bin/python${PYTHON_VERSION}"; then
    PYTHON_BIN="$(pyenv prefix "$PYENV_PY")/bin/python${PYTHON_VERSION}"
  fi
fi
if [[ -z "$PYTHON_BIN" ]] && command -v dnf >/dev/null 2>&1; then
  echo "  python${PYTHON_VERSION} not found: installing it with dnf"
  $SUDO dnf install -y "python${PYTHON_VERSION}" "python${PYTHON_VERSION}-pip"
  python_ok "/usr/bin/python${PYTHON_VERSION}" && PYTHON_BIN="/usr/bin/python${PYTHON_VERSION}"
fi
[[ -n "$PYTHON_BIN" ]] || { echo "ERROR: python${PYTHON_VERSION} is not available and could not be installed" >&2; exit 1; }
if ! "$PYTHON_BIN" -c 'import venv, ensurepip' >/dev/null 2>&1 && command -v dnf >/dev/null 2>&1; then
  echo "  the venv/ensurepip modules are missing: installing python${PYTHON_VERSION}-pip"
  $SUDO dnf install -y "python${PYTHON_VERSION}-pip"
fi
for tool in git aws node npm; do
  command -v "$tool" >/dev/null 2>&1 || { echo "ERROR: $tool is required and is provided by the CodeBuild image" >&2; exit 1; }
done
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
[[ "$NODE_MAJOR" -ge 18 ]] || { echo "ERROR: Node.js 18 or newer is required (found $(node -v))" >&2; exit 1; }
echo "  python: $PYTHON_BIN ($("$PYTHON_BIN" -V 2>&1))"
echo "  node:   $(node -v)   npm: $(npm -v)   git: $(git --version | cut -d' ' -f3)   aws: $(aws --version 2>&1 | cut -d' ' -f1)"
echo "  not installed (already in the image): git, AWS CLI, Node.js/npm"

# -----------------------------------------------------------------------------
banner "2. clone the repository"
mkdir -p "$WORK_DIR"
CLONE_ARGS=(--branch "$REPO_BRANCH")
[[ "$REPO_URL" == http* ]] && CLONE_ARGS+=(--depth 1)     # a local git bundle is cloned in full
git clone "${CLONE_ARGS[@]}" "$REPO_URL" "$WORK_DIR/repo"
cd "$WORK_DIR/repo"
echo "  cloned $(git rev-parse --short HEAD) into $(pwd)"

# -----------------------------------------------------------------------------
banner "3. Python virtual environment and requirements"
"$PYTHON_BIN" -m venv .venv
# shellcheck disable=SC1091  # created just above
source .venv/bin/activate
python3 -m pip install --quiet --upgrade pip
python3 -m pip install --quiet -r requirements-agentcore.txt
echo "  $(python3 -V) at $(command -v python3); boto3 $(python3 -c 'import boto3; print(boto3.__version__)')"

# -----------------------------------------------------------------------------
banner "4. Amazon Bedrock model access"
set +e   # each block below checks its own result
pip install --upgrade boto3
python3 enable-bedrock.py 'anthropic.claude-opus-4-5-20251101-v1:0' 'us-east-1'
RESULT=$?
if [ $RESULT -eq 0 ]; then
    echo "Model enabled"
else
    echo "Model enable failed"
    exit 1  # Exit the shell script with failure
fi

pip install --upgrade boto3
python3 enable-bedrock.py 'anthropic.claude-sonnet-4-5-20250929-v1:0' 'us-east-1'
RESULT=$?
if [ $RESULT -eq 0 ]; then
    echo "Model enabled"
else
    echo "Model enable failed"
    exit 1  # Exit the shell script with failure
fi

pip install --upgrade boto3
python3 enable-bedrock.py 'amazon.titan-embed-text-v2:0' 'us-east-1'
RESULT=$?
if [ $RESULT -eq 0 ]; then
    echo "Model enabled"
else
    echo "Model enable failed"
    exit 1  # Exit the shell script with failure
fi
set -e

# -----------------------------------------------------------------------------
banner "5. console dependencies"
(cd app/console && npm ci --no-audit --no-fund)

# -----------------------------------------------------------------------------
banner "6. seed-user password (generated, never printed)"
# 24 characters with a lower-case letter, an upper-case letter, a digit and a
# symbol: the Cognito password policy of the staff pool (app/infra/cognito_setup.py).
RETAIL_OPS_SEED_PASSWORD="$(python3 -c 'import secrets,string; r=secrets.SystemRandom(); s="-_.%"; c=[r.choice(string.ascii_lowercase),r.choice(string.ascii_uppercase),r.choice(string.digits),r.choice(s)]+[r.choice(string.ascii_letters+string.digits+s) for _ in range(20)]; r.shuffle(c); print("".join(c))')"
export RETAIL_OPS_SEED_PASSWORD
echo "  RETAIL_OPS_SEED_PASSWORD set (${#RETAIL_OPS_SEED_PASSWORD} characters)"

# -----------------------------------------------------------------------------
banner "7. deploy the stack (deploy/bootstrap.sh)"
DEV_TOKEN_CLEANUP=1
deploy/bootstrap.sh --yes --env "$RETAIL_OPS_ENV" --skip "$SKIP_STAGES" --markets "$MARKETS"

# -----------------------------------------------------------------------------
banner "8. verify the deployment (deploy/verify.sh)"
deploy/verify.sh --env "$RETAIL_OPS_ENV"

# -----------------------------------------------------------------------------
banner "9. switch the development token off"
python3 -m app.infra.deploy_l4 --dev-token off --apply
DEV_TOKEN_CLEANUP=0

# -----------------------------------------------------------------------------
banner "10. outputs"
CONSOLE_URL="$(python3 -c 'import json; from app.infra import naming; print(json.load(open(naming.config_path("console")))["url"])')"
API_ID="$(aws apigateway get-rest-apis --query "items[?name=='retail-ops-${RETAIL_OPS_ENV}-journeys-api'].id | [0]" --output text)"
echo "  Console URL:  $CONSOLE_URL"
echo "  API base:     https://${API_ID}.execute-api.${AWS_REGION}.amazonaws.com/dev"
echo "  Environment:  $RETAIL_OPS_ENV"
echo "  Tear down:    deploy/destroy.sh --env $RETAIL_OPS_ENV --confirm --account $ACCOUNT_ID --include-data"
echo "GUIDANCE_ENV_NAME=${RETAIL_OPS_ENV}"
echo "Deployment complete"
