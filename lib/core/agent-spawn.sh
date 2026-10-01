#!/usr/bin/env bash
set -uo pipefail

# agent-spawn.sh — wraps the built-in Python agent harness for all hive-spawned agents.
#
# This is the SOLE place that spawns an agent inside hive. Every script and
# agent prompt that needs to spawn an agent must go through here.
#
# Responsibilities:
#   1. Inject an MCP config so the spawned agent can call the hive MCP server.
#   2. If HIVE_ROLE contains "verification" and $HIVE_VERIFICATION_MODEL is
#      set, pass it to the harness for model selection.
#   3. Log the spawn to .company/logs/spawns.log for audit.
#   4. Register the in-flight spawn in .company/logs/active/<id>.json so the
#      dashboard can show "what's currently running"; remove on exit.
#   5. Tee the output to .company/logs/streams/<id>.jsonl so the dashboard
#      can replay tool calls + text per spawn.
#   6. On exit append a one-line summary to .company/logs/cycles.jsonl for
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
#      uses a stronger model than the default.
#
# LLM API configuration (for OpenAI-compatible endpoints):
#   HIVE_API_BASE           base URL for the LLM API
#                           Examples:
#                             https://api.openai.com/v1        (OpenAI)
#                             https://api.anthropic.com/v1    (Anthropic)
#                             http://localhost:11434/v1       (Ollama)
#                             https://openrouter.ai/api/v1    (OpenRouter)
#                             https://api.groq.com/openai/v1  (Groq)
#   HIVE_API_KEY            API key for authentication
#   HIVE_MODEL              model name (e.g. "gpt-4o", "claude-sonnet-4-20250514")
#   HIVE_TEMPERATURE        sampling temperature 0.0-2.0 (default: 0.7)
#   HIVE_MAX_TOKENS         max response tokens (default: 8192)

# Source the user's hive env file (if present) so daemons spawned via nohup,
# systemd, etc. — which don't see ~/.zshrc or ~/.bashrc — still get
# LLM endpoint config, HIVE_ROOT, etc.
HIVE_ENV_FILE="${HIVE_ENV_FILE:-$HOME/.config/hive/env.sh}"
[ -f "$HIVE_ENV_FILE" ] && . "$HIVE_ENV_FILE"

HIVE_ROOT="${HIVE_ROOT:-$HOME/.local/share/hive}"
COMPANY_DIR="${COMPANY_DIR:-${PWD}/.company}"
MCP_HOST="${HIVE_MCP_HOST:-127.0.0.1}"
MCP_PORT="${HIVE_MCP_PORT:-7891}"

HIVE_AGENT="${HIVE_AGENT:-unknown}"
HIVE_DEPT="${HIVE_DEPT:-unknown}"
HIVE_ROLE="${HIVE_ROLE:-unknown}"
HIVE_TICKET="${HIVE_TICKET:-}"

# Derive PROJECT_ROOT from COMPANY_DIR so we can locate the worktree.
PROJECT_ROOT="${PROJECT_ROOT:-}"
if [ -z "$PROJECT_ROOT" ] && [ -n "$COMPANY_DIR" ]; then
  PROJECT_ROOT="$(cd "$COMPANY_DIR" 2>/dev/null && cd .. && pwd)" || true
fi

# If this spawn is for a ticket (HIVE_TICKET set), cd into its worktree so the
# agent starts in the right directory. Without this, agents spawn in the
# caller's cwd and run tests / make changes in main instead of their feature
# branch worktree.
WORKTREE_DIR=""
if [ -n "$HIVE_TICKET" ] && [ -n "$PROJECT_ROOT" ]; then
  WORKTREE_DIR="$PROJECT_ROOT/.worktrees/$HIVE_TICKET"
  if [ -d "$WORKTREE_DIR" ]; then
    cd "$WORKTREE_DIR"
  fi
fi

INJECT_MCP=true
if [ "${1:-}" = "--no-mcp" ]; then
  INJECT_MCP=false
  shift
fi
[ "${1:-}" = "--" ] && shift

# Read prompt from arg or stdin
if [ $# -gt 0 ]; then
  PROMPT="$1"
else
  PROMPT="$(cat)"
fi

# Build MCP config (temp file, deleted by trap below).
MCP_CONFIG=""
if [ "$INJECT_MCP" = true ]; then
  MCP_CONFIG="$(mktemp -t hive-mcp-XXXXXX.json)"
  cat > "$MCP_CONFIG" <<EOF
{
  "mcpServers": {
    "hive": {
      "type": "http",
      "url": "http://${MCP_HOST}:${MCP_PORT}/mcp"
    }
  }
}
EOF
fi

# Spawn identity + observability dirs
mkdir -p "$COMPANY_DIR/logs/active" "$COMPANY_DIR/logs/streams"
SPAWN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$-$RANDOM"
ACTIVE_FILE="$COMPANY_DIR/logs/active/$SPAWN_ID.json"
STREAM_FILE="$COMPANY_DIR/logs/streams/$SPAWN_ID.jsonl"
CYCLES_LOG="$COMPANY_DIR/logs/cycles.jsonl"
SPAWN_LOG="$COMPANY_DIR/logs/spawns.log"
START_TS_EPOCH=$(date +%s)
START_TS_ISO=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# Build start record via python3 for safe JSON quoting.
SPAWN_ID="$SPAWN_ID" PROMPT_CHARS="${#PROMPT}" \
  HIVE_AGENT="$HIVE_AGENT" HIVE_DEPT="$HIVE_DEPT" HIVE_ROLE="$HIVE_ROLE" HIVE_TICKET="$HIVE_TICKET" \
  HIVE_ROUND_ENTRY="${HIVE_ROUND_ENTRY:-}" \
  START_TS_ISO="$START_TS_ISO" START_TS_EPOCH="$START_TS_EPOCH" \
  STREAM_FILE="$STREAM_FILE" INJECT_MCP="$INJECT_MCP" \
  python3 - "$ACTIVE_FILE" <<'PYEOF'
import json, os, sys
out = sys.argv[1]
rec = {
  "spawn_id":   os.environ["SPAWN_ID"],
  "agent":      os.environ["HIVE_AGENT"],
  "dept":       os.environ["HIVE_DEPT"],
  "role":       os.environ["HIVE_ROLE"],
  "ticket":     os.environ.get("HIVE_TICKET") or None,
  "round_entry": os.environ.get("HIVE_ROUND_ENTRY") or None,
  "started_at": os.environ["START_TS_ISO"],
  "started_ts": int(os.environ["START_TS_EPOCH"]),
  "prompt_chars": int(os.environ["PROMPT_CHARS"]),
  "stream_file": os.environ["STREAM_FILE"],
  "pid":         os.getppid(),
  "mcp_injected": os.environ["INJECT_MCP"] == "true",
}
with open(out, "w") as f:
  json.dump(rec, f)
PYEOF

{
  echo "=== $START_TS_ISO spawn ==="
  echo "spawn_id: $SPAWN_ID"
  echo "agent: $HIVE_AGENT  dept: $HIVE_DEPT  role: $HIVE_ROLE  ticket: ${HIVE_TICKET:-(none)}"
  echo "prompt_chars: ${#PROMPT}"
  echo "mcp_injected: $INJECT_MCP"
  echo "stream_file: $STREAM_FILE"
} >> "$SPAWN_LOG"

# Cleanup: append a cycle summary, drop the active file, drop the temp mcp.
finish() {
  local exit_code=$?
  local end_ts=$(date +%s)
  local duration=$((end_ts - START_TS_EPOCH))
  # Extract result info from the LAST line of the stream (the result event)
  local last_line=""
  [ -f "$STREAM_FILE" ] && last_line=$(tail -1 "$STREAM_FILE" 2>/dev/null || true)
  END_TS=$end_ts DURATION=$duration EXIT=$exit_code SPAWN_ID="$SPAWN_ID" \
    HIVE_AGENT="$HIVE_AGENT" HIVE_DEPT="$HIVE_DEPT" HIVE_ROLE="$HIVE_ROLE" HIVE_TICKET="$HIVE_TICKET" \
    LAST_LINE="$last_line" CYCLES_LOG="$CYCLES_LOG" \
    python3 - <<'PYEOF' || true
import json, os, sys
last = os.environ.get("LAST_LINE", "")
result = {}
try:
  d = json.loads(last)
  if d.get("type") == "result":
    result = {
      "is_error":      d.get("is_error"),
      "subtype":       d.get("subtype"),
      "num_turns":     d.get("num_turns"),
      "duration_ms":   d.get("duration_ms"),
      "total_cost_usd":d.get("total_cost_usd"),
      "result_text":   (d.get("result") or "")[:500],
    }
except Exception:
  pass

cycle = {
  "spawn_id":   os.environ["SPAWN_ID"],
  "agent":      os.environ["HIVE_AGENT"],
  "dept":       os.environ["HIVE_DEPT"],
  "role":       os.environ["HIVE_ROLE"],
  "ticket":     os.environ.get("HIVE_TICKET") or None,
  "exit_code":  int(os.environ["EXIT"]),
  "duration_s": int(os.environ["DURATION"]),
  "ended_at":   __import__("datetime").datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
  "result":     result,
}
with open(os.environ["CYCLES_LOG"], "a") as f:
  f.write(json.dumps(cycle) + "\n")
PYEOF

  rm -f "$ACTIVE_FILE" 2>/dev/null || true
  [ -n "$MCP_CONFIG" ] && rm -f "$MCP_CONFIG"
  return $exit_code
}
trap finish EXIT

# Invoke the agent harness. tee stream events into the per-spawn stream file AND to
# stdout (so existing callers' redirects still capture everything).
AGENT_HARNESS="$HIVE_ROOT/lib/core/agent-harness.py"
if [ ! -f "$AGENT_HARNESS" ]; then
  echo "ERROR: agent-harness.py not found at $AGENT_HARNESS" >&2
  exit 1
fi

# Build environment for the harness
ENV_ARGS=()
[ -n "${HIVE_API_BASE:-}" ] && ENV_ARGS+=("-e" "HIVE_API_BASE=$HIVE_API_BASE")
[ -n "${HIVE_API_KEY:-}" ] && ENV_ARGS+=("-e" "HIVE_API_KEY=$HIVE_API_KEY")
[ -n "${HIVE_MODEL:-}" ] && ENV_ARGS+=("-e" "HIVE_MODEL=$HIVE_MODEL")

# Pass MCP URL if MCP is injected
if [ "$INJECT_MCP" = true ]; then
  MCP_URL="http://${MCP_HOST}:${MCP_PORT}/mcp"
else
  MCP_URL=""
fi

# Run the harness, tee output to stream file
# Build harness arguments
if [ "$INJECT_MCP" = true ]; then
  HARNESS_ARGS=(--mcp-url "$MCP_URL")
else
  HARNESS_ARGS=(--no-mcp)
fi

# Export env vars for the harness to pick up from os.environ
export HIVE_API_BASE="${HIVE_API_BASE:-}"
export HIVE_API_KEY="${HIVE_API_KEY:-}"
export HIVE_MODEL="${HIVE_MODEL:-}"
export HIVE_TEMPERATURE="${HIVE_TEMPERATURE:-0.7}"
export HIVE_MAX_TOKENS="${HIVE_MAX_TOKENS:-8192}"

python3 "$AGENT_HARNESS" "${HARNESS_ARGS[@]}" -- "$PROMPT" 2>/dev/null | tee "$STREAM_FILE"
exit ${PIPESTATUS[0]:-0}
