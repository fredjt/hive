#!/usr/bin/env bash
set -uo pipefail

# agent-spawn.sh — wraps the agent harness for all hive-spawned agents.
#
# This is the SOLE place that invokes an AI agent. Every script and
# agent prompt that needs to spawn an agent must go through here.
#
# Responsibilities:
#   1. Point the harness at the hive MCP server over localhost HTTP.
#   2. Apply HIVE_AGENT, HIVE_DEPT, HIVE_ROLE, HIVE_TICKET identity vars.
#   3. If HIVE_ROLE contains "verification" and $HIVE_VERIFICATION_MODEL is
#      set, inject --model "$HIVE_VERIFICATION_MODEL".
#   4. Log the spawn to .company/logs/spawns.log for audit.
#   5. Register the in-flight spawn in .company/logs/active/<id>.json so the
#      dashboard can show "what's currently running"; remove on exit.
#   6. Tee the stream-json events to .company/logs/streams/<id>.jsonl so the
#      dashboard can replay tool calls + text per spawn.
#   7. On exit append a one-line summary to .company/logs/cycles.jsonl for a
#      cycle history view in the dashboard.
#
# Usage:
#   echo "<prompt>" | agent-spawn.sh
#   agent-spawn.sh -- "<prompt>"
#   agent-spawn.sh --no-mcp -- "<prompt>"
#
# Caller-supplied identity (recommended — read by the dashboard):
#   HIVE_AGENT              agent name, e.g. "vp-it", "ceo", "ic-engineer"
#   HIVE_DEPT               dept the spawn is for, e.g. "it", "frontend-web"
#   HIVE_ROLE               role label, e.g. "CEO", "VP", "IC", "verification-IC"
#   HIVE_TICKET             ticket id this spawn is acting on (optional)
#
# Model override (optional):
#   HIVE_VERIFICATION_MODEL - model name for verification agents only.
#      When HIVE_ROLE contains "verification" and this is set, the harness
#      CLI is invoked with --model "$HIVE_VERIFICATION_MODEL".

# Source the user's hive env file (if present) so daemons spawned via nohup,
# systemd, etc. — which don't see ~/.zshrc or ~/.bashrc — still get
# LLM endpoint config, etc.
HIVE_ENV_FILE="${HIVE_ENV_FILE:-$HOME/.config/hive/env.sh}"
[ -f "$HIVE_ENV_FILE" ] && . "$HIVE_ENV_FILE"

HIVE_ROOT="${HIVE_ROOT:-$HOME/.local/share/hive}"
COMPANY_DIR="${COMPANY_DIR:-${PWD}/.company}"
MCP_HOST="${HIVE_MCP_HOST:-127.0.0.1}"
MCP_PORT="${HIVE_MCP_PORT:-7891}"
HIVE_AGENT="${HIVE_AGENT:-unknown}"
HIVE_DEPT="${HIVE_DEPT:-}"
HIVE_ROLE="${HIVE_ROLE:-}"
HIVE_TICKET="${HIVE_TICKET:-}"

# D2: resolve and export the FULL env the framework primer's section 6
# promises — not just whatever the caller happened to have in its
# environment. In the 0.10.0 field run the CEO spent a turn cat'ing an
# empty $HIVE_ROOT/config.toml: $HIVE_ROOT was not exported into the
# agent's shell, so $HIVE_ROOT/... expanded to /... .
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${HIVE_INSTALL_DIR:-$(dirname "$(dirname "$SCRIPT_DIR")")}"
HIVE_INSTALL_DIR="$INSTALL_DIR"
SKILL_DIR="${SKILL_DIR:-$HIVE_ROOT}"
PROJECT_ROOT="$(dirname "$COMPANY_DIR")"
export HIVE_ROOT SKILL_DIR HIVE_INSTALL_DIR INSTALL_DIR COMPANY_DIR PROJECT_ROOT
export HIVE_AGENT HIVE_DEPT HIVE_ROLE HIVE_TICKET
# Queue-entry id, present when the round runner set it for this spawn.
export HIVE_ROUND_ENTRY

# Generate a unique spawn ID
SPAWN_ID="spawn-$(date +%s)-$$"
STREAM_DIR="$COMPANY_DIR/logs/streams"
ACTIVE_DIR="$COMPANY_DIR/logs/active"
mkdir -p "$STREAM_DIR" "$ACTIVE_DIR"
STREAM_FILE="$STREAM_DIR/${SPAWN_ID}.jsonl"
ACTIVE_FILE="$ACTIVE_DIR/${SPAWN_ID}.json"

# Write active spawn metadata
cat > "$ACTIVE_FILE" <<META
{
  "id": "$SPAWN_ID",
  "agent": "$HIVE_AGENT",
  "dept": "${HIVE_DEPT:-}",
  "role": "${HIVE_ROLE:-}",
  "ticket": "${HIVE_TICKET:-}",
  "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "pid": "$$"
}
META

# Cleanup on exit.
# E3: the log must say HOW the spawn ended. In the 0.10.0 field run
# vp-it was SIGTERMed mid-turn by `hive stop` and still landed in
# cycles.jsonl as "done" — indistinguishable from a completed run in the
# log the dashboard and every post-mortem read. Clean exit -> "done",
# non-zero harness exit -> "failed", death by signal -> "killed" with
# the signal name. (SIGKILL cannot be trapped: a -9'd spawn leaves its
# active/<id>.json behind, which is orphan-recovery's business.)
CLEANUP_RAN=0
SIGNAL_EXIT=""
cleanup() {
  # MUST be the first statement: any command before it (including the
  # guard test below) would clobber $? — and this once logged every
  # clean run as "failed" (the guard's [ 0 = 1 ] test leaves $? = 1).
  local rc=$?
  [ "$CLEANUP_RAN" = 1 ] && return 0
  CLEANUP_RAN=1
  local exit_code status signame=""
  if [ -n "$SIGNAL_EXIT" ]; then
    exit_code=$((128 + SIGNAL_EXIT))
    status="killed"
    signame=$(kill -l "$SIGNAL_EXIT" 2>/dev/null || echo "?")
  else
    exit_code=$rc
    if [ "$exit_code" -gt 128 ]; then
      status="killed"
      signame=$(kill -l $((exit_code - 128)) 2>/dev/null || echo "?")
    elif [ "$exit_code" -ne 0 ]; then
      status="failed"
    else
      status="done"
    fi
  fi
  rm -f "$ACTIVE_FILE"
  local sigfield=""
  [ -n "$signame" ] && sigfield=", \"signal\": \"$signame\""
  # killed/failed are always logged (even with an empty stream); "done"
  # only when the run actually produced events (pre-E3 semantics).
  if [ "$status" != "done" ] || { [ -f "$STREAM_FILE" ] && [ -s "$STREAM_FILE" ]; }; then
    echo "{\"spawn_id\": \"$SPAWN_ID\", \"agent\": \"$HIVE_AGENT\", \"status\": \"$status\", \"exit_code\": $exit_code$sigfield, \"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" >> "$COMPANY_DIR/logs/cycles.jsonl"
  fi
}
on_signal() {
  SIGNAL_EXIT="$1"
  trap '' TERM INT
  cleanup
  exit $((128 + $1))
}
trap cleanup EXIT
trap 'on_signal 15' TERM
trap 'on_signal 2' INT

# Parse arguments
NO_MCP=0
PROMPT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-mcp) NO_MCP=1; shift ;;
    --)       shift; PROMPT="$*"; break ;;
    *)        if [ -z "$PROMPT" ]; then PROMPT="$1"; else PROMPT="$PROMPT $1"; fi; shift ;;
  esac
done

# Read from stdin if no prompt provided
if [ -z "$PROMPT" ] && [ ! -t 0 ]; then
  PROMPT=$(cat)
fi

if [ -z "$PROMPT" ]; then
  echo "agent-spawn: prompt required (pipe to stdin or pass as argument)" >&2
  exit 1
fi

# Build model override for verification agents.
# Precedence: HIVE_VERIFICATION_MODEL env > [llm].verification_model config.
MODEL_ARG=""
if echo "${HIVE_ROLE:-}" | grep -qi "verification" && [ -z "${HIVE_VERIFICATION_MODEL:-}" ]; then
  # Read [llm].verification_model: user config first, then defaults
  # (same precedence as the rest of the config: env > user > defaults).
  for _cfg in "$HOME/.config/hive/config.toml" "$HIVE_ROOT/etc/config.defaults.toml"; do
    [ -f "$_cfg" ] || continue
    _vm=$(awk '/^[[:space:]]*\[llm\]/{s=1;next} /^[[:space:]]*\[/{s=0} s && /^[[:space:]]*verification_model[[:space:]]*=/{sub(/^[[:space:]]*verification_model[[:space:]]*=[[:space:]]*/,""); gsub(/["'"'"']/,""); print; exit}' "$_cfg" 2>/dev/null) || _vm=""
    if [ -n "$_vm" ]; then
      HIVE_VERIFICATION_MODEL="$_vm"
      break
    fi
  done
fi
if echo "${HIVE_ROLE:-}" | grep -qi "verification" && [ -n "${HIVE_VERIFICATION_MODEL:-}" ]; then
  MODEL_ARG="--model ${HIVE_VERIFICATION_MODEL}"
fi

# Log the spawn
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $SPAWN_ID agent=$HIVE_AGENT role=${HIVE_ROLE:-} ticket=${HIVE_TICKET:-}" >> "$COMPANY_DIR/logs/spawns.log"

# Run the agent harness — under the install's venv python. The venv is
# where the harness's dependencies (httpx, ...) live; bare python3 on PATH
# usually lacks them, in which case the harness silently disables LLM and
# MCP calls ("LLM unavailable: httpx not installed") even though
# `hive doctor` — which checks the venv interpreter — reports all green.
# (INSTALL_DIR was already resolved + exported in the D2 block above:
# HIVE_INSTALL_DIR if set, else this script's own install location.)
# G4 stream exclusion: the harness's sandbox attributes any .company
# mutation during a command to that command — including the tee below
# appending this spawn's own stream events to $STREAM_FILE between the
# harness's before/after snapshots (race: the flake that turned
# test_spawn_env red on CI, 2026-11-08 — a spawn "failing" on its own
# telemetry). Tell the harness which stream file is its own so it can
# exclude it from the mutation diff.
export HIVE_STREAM_FILE="$STREAM_FILE"

HARNESS_PY="python3"
if [ -x "$INSTALL_DIR/venv/bin/python" ]; then
  HARNESS_PY="$INSTALL_DIR/venv/bin/python"
else
  echo "agent-spawn: WARNING: no venv python at $INSTALL_DIR/venv/bin/python — falling back to python3 on PATH (LLM/MCP calls may be disabled if it lacks httpx)" >&2
fi
exec "$HARNESS_PY" -u "$HIVE_ROOT/lib/core/agent-harness.py" \
  --mcp-url "http://${MCP_HOST}:${MCP_PORT}/mcp" \
  $MODEL_ARG \
  -- "$PROMPT" 2>&1 | tee "$STREAM_FILE"
