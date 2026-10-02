#!/usr/bin/env bash
set -uo pipefail

# agent-spawn.sh — wraps the agent harness for all hive-spawned agents.
#
# This is the SOLE place that invokes an AI agent. Every script and
# agent prompt that needs to spawn an agent must go through here.
#
# Responsibilities:
#   1. Inject an MCP config so the spawned agent sees the hive MCP server.
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

# Cleanup on exit
cleanup() {
  rm -f "$ACTIVE_FILE"
  # Append cycle summary
  if [ -f "$STREAM_FILE" ] && [ -s "$STREAM_FILE" ]; then
    local last_line
    last_line=$(tail -1 "$STREAM_FILE")
    echo "{\"spawn_id\": \"$SPAWN_ID\", \"agent\": \"$HIVE_AGENT\", \"status\": \"done\", \"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" >> "$COMPANY_DIR/logs/cycles.jsonl"
  fi
}
trap cleanup EXIT

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

# Build MCP config if enabled
MCP_CONFIG=""
if [ "$NO_MCP" != "1" ]; then
  MCP_CONFIG="$HIVE_ROOT/lib/hive_mcp/server.py"
fi

# Build model override for verification agents
MODEL_ARG=""
if echo "${HIVE_ROLE:-}" | grep -qi "verification" && [ -n "${HIVE_VERIFICATION_MODEL:-}" ]; then
  MODEL_ARG="--model" "$HIVE_VERIFICATION_MODEL"
fi

# Log the spawn
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $SPAWN_ID agent=$HIVE_AGENT role=${HIVE_ROLE:-} ticket=${HIVE_TICKET:-}" >> "$COMPANY_DIR/logs/spawns.log"

# Run the agent harness
exec python3 -u "$HIVE_ROOT/lib/core/agent-harness.py" \
  --mcp-url "http://${MCP_HOST}:${MCP_PORT}/mcp" \
  $MODEL_ARG \
  -- "$PROMPT" 2>&1 | tee "$STREAM_FILE"
