#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -t 1 ]; then GREEN='\033[0;32m' YELLOW='\033[1;33m' RESET='\033[0m'; else GREEN='' YELLOW='' RESET=''; fi
info()    { printf "${YELLOW}%s${RESET}\n" "$*"; }
success() { printf "${GREEN}%s${RESET}\n" "$*"; }

usage() {
  cat <<EOF
Usage: $(basename "$0") --target <kiro|claude-code|quick-desktop> [OPTIONS]

Targets:
  kiro            Copy skills and agent config to ~/.kiro/ (or --path DIR/.kiro/)
  claude-code     Symlink skills into .claude/skills/ in a project directory
  quick-desktop   Print manual upload instructions for Amazon Quick

Options:
  --target TARGET       Target platform (required)
  --mode MODE           single (default) or multiagent (kiro only)
  --path DIR            Install to DIR/.kiro/ instead of ~/.kiro/ (kiro only)
  --project-dir DIR     Project directory for claude-code (default: .)
  --include-build       Also install build-time skills from skills-build/ (kiro only)
  --help                Show this help
EOF
  exit "${1:-0}"
}

TARGET="" PROJECT_DIR="." KIRO_PATH="" MODE="single" INCLUDE_BUILD="0"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)      TARGET="${2:-}"; shift 2 ;;
    --mode)        MODE="${2:-}"; shift 2 ;;
    --path)        KIRO_PATH="${2:-}"; shift 2 ;;
    --project-dir) PROJECT_DIR="${2:-}"; shift 2 ;;
    --include-build) INCLUDE_BUILD="1"; shift ;;
    --help)        usage 0 ;;
    *)             echo "Unknown option: $1"; usage 1 ;;
  esac
done
[[ -z "$TARGET" ]] && { echo "Error: --target is required."; usage 1; }

SKILL_COUNT="$(find "$SCRIPT_DIR/skills" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')"

install_kiro() {
  local dest="${KIRO_PATH:-$HOME}/.kiro"
  mkdir -p "$dest/skills" "$dest/agents"
  cp -r "$SCRIPT_DIR"/skills/* "$dest/skills/"
  if [[ "$INCLUDE_BUILD" == "1" ]]; then cp -r "$SCRIPT_DIR"/skills-build/* "$dest/skills/"; fi
  if [[ "$MODE" == "multiagent" ]]; then
    cp "$SCRIPT_DIR"/agents/multiagent/*.json "$dest/agents/"
    success "Installed $SKILL_COUNT automotive skills and multiagent configs to $dest/"
    if [[ -z "$KIRO_PATH" ]]; then info "Switch with: /agent automotive-multiagent"; fi
  else
    cp "$SCRIPT_DIR/agents/automotive-agent.json" "$dest/agents/"
    success "Installed $SKILL_COUNT automotive skills to $dest/skills/ and agent config to $dest/agents/automotive-agent.json"
    if [[ -z "$KIRO_PATH" ]]; then info "Switch with: /agent automotive"; fi
  fi
}

install_claude_code() {
  local dest="$PROJECT_DIR/.claude/skills"
  mkdir -p "$dest"
  for skill_dir in "$SCRIPT_DIR"/skills/*/; do
    ln -sfn "$skill_dir" "$dest/$(basename "$skill_dir")"
  done
  success "Installed $SKILL_COUNT automotive skills as symlinks in $dest/"
}

print_domain() { printf "\n%s\n" "$1:"; shift; for s in "$@"; do [[ -d "$SCRIPT_DIR/skills/$s" ]] && echo "  skills/$s/SKILL.md"; done; }

install_quick_desktop() {
  info "Amazon Quick requires manual upload of each SKILL.md file."
  info "Recommended order (by domain):"
  print_domain "Fixed Operations" service-estimate-integrity multi-point-inspection-triage service-morning-work service-retention-and-declined-work
  print_domain "Retail Sales and F&I" ev-incentive-eligibility trade-equity-and-upgrade-timing deal-structuring-and-disclosure objection-handling-on-the-floor lead-response-and-nurture-policy
  print_domain "OEM Field Sales" oem-field-sales-guardrails dealer-account-plan-generation
  print_domain "Agentic Architecture on AWS" agentic-delivery-model-selection autonomy-ladder-and-hard-rules two-supervisor-orchestration
  echo ""
  info "Upload path: Settings → Capabilities → Skills → Upload → select a SKILL.md file"
}

case "$TARGET" in
  kiro)          install_kiro ;;
  claude-code)   install_claude_code ;;
  quick-desktop) install_quick_desktop ;;
  *)             echo "Unknown target: $TARGET"; usage 1 ;;
esac
