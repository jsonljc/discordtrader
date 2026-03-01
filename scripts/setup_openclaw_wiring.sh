#!/usr/bin/env bash
#
# Setup OpenClaw skill files for the oct-* CLI entrypoints.
#
# Usage:
#   ./scripts/setup_openclaw_wiring.sh [--state-dir DIR] [--venv PATH]
#
# Writes SKILL.md files so OpenClaw agents can run listener, interpreter,
# risk, executor, or paper mode. Set OPENCLAW_STATE_DIR or pass --state-dir
# to target a specific OpenClaw home.
#
set -euo pipefail

STATE_DIR="${OPENCLAW_STATE_DIR:-$HOME/.openclawdiscord}"
VENV_PATH=""
PROFILE="discord_equities"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --state-dir)
      STATE_DIR="$2"
      shift 2
      ;;
    --venv)
      VENV_PATH="$2"
      shift 2
      ;;
    --profile)
      PROFILE="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

WORKSPACE="${STATE_DIR}/workspace"
SKILLS_BASE="${WORKSPACE}/skills"

if [[ -n "$VENV_PATH" ]]; then
  CMD_PREFIX="${VENV_PATH}/bin/"
else
  CMD_PREFIX=""
fi

mkdir -p "${SKILLS_BASE}/run-listener"
mkdir -p "${SKILLS_BASE}/run-interpreter"
mkdir -p "${SKILLS_BASE}/run-risk"
mkdir -p "${SKILLS_BASE}/run-executor"
mkdir -p "${SKILLS_BASE}/run-paper"
mkdir -p "${SKILLS_BASE}/run-review"

_write_skill() {
  local name="$1"
  local desc="$2"
  local cmd="$3"
  local dir="${SKILLS_BASE}/${name}"
  mkdir -p "$dir"
  cat > "${dir}/SKILL.md" << EOF
# ${name}

${desc}

## Prerequisites

- Package installed: \`pip install -e .\` or \`pip install openclawtrader\`
- \`.env\` configured (DISCORD_BOT_TOKEN, IBKR_*, etc.)
- \`OPENCLAWTRADER_CONFIG_DIR\` set if config is not in default location

## Command

\`\`\`bash
${cmd}
\`\`\`

## Profile

Set \`PROFILE=${PROFILE}\` in .env or pass \`--profile ${PROFILE}\` to override.
EOF
  echo "Wrote ${dir}/SKILL.md"
}

_write_skill "run-listener" \
  "Run the Discord Listener agent. Connects to Discord, filters messages, and enqueues SignalEvents." \
  "${CMD_PREFIX}oct-listener --profile ${PROFILE}"

_write_skill "run-interpreter" \
  "Run the Interpreter agent. Consumes SignalEvents, parses to TradeIntents, enqueues for Risk Officer." \
  "${CMD_PREFIX}oct-interpreter --profile ${PROFILE}"

_write_skill "run-risk" \
  "Run the Risk Officer agent. Consumes TradeIntents, enforces constraints, emits RiskDecisions." \
  "${CMD_PREFIX}oct-risk --profile ${PROFILE}"

_write_skill "run-executor" \
  "Run the IBKR Executor agent. Consumes RiskDecisions, places orders, emits ExecutionReceipts." \
  "${CMD_PREFIX}oct-executor --profile ${PROFILE}"

_write_skill "run-paper" \
  "Run all agents in one process (paper / dev only). Listener + Interpreter + Risk + Executor + Review." \
  "${CMD_PREFIX}oct-paper --profile ${PROFILE}"

_write_skill "run-review" \
  "Run the Review Dispatcher. Consumes ExecutionReceipts, dispatches NEEDS_APPROVAL to log or webhook." \
  "${CMD_PREFIX}oct-review --profile ${PROFILE}"

echo ""
echo "OpenClaw wiring complete. Skills written to ${SKILLS_BASE}"
echo "State dir: ${STATE_DIR}"
