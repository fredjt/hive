#!/usr/bin/env bash
set -uo pipefail

# claude-spawn.sh — wraps `claude -p` for all hive-spawned agents.
#
# This is the SOLE place that invokes the claude CLI binary. Every script and
# agent prompt that needs to spawn a Claude Code agent must go through here.
#
# Responsibilities:
#   1. Inject an MCP config so the spawned agent sees the hive MCP server.
#   2. Apply $CLAUDE_FLAGS (defaults to --dangerously-skip-permissions
#      --output-format stream-json --verbose so the dashboard can tail tool
#      calls + assistant text in real time).
#   3. If HIVE_ROLE contains "verification" and $HIVE_VERIFICATION_MODEL is
#      set, inject --model "$HIVE_VERIFICATION_MODEL" so verification agents
#      use a stronger model than the default (e.g. claude-sonnet-5 over
#      unsloth/Qwen3.6-35B-A3B-MTP-GGUF:Q4_K_M).
#   4. Log the spawn to .company/logs/spawns.log for audit.
#   5. Register the in-flight spawn in .company/logs/active/<id>.json so the
#      dashboard can show "what's currently running"; remove on exit.
#   6. Tee the stream-json events to .company/logs/streams/<id>.jsonl so the
#      dashboard can replay tool calls + text per spawn.
#   7. On exit append a one-line summary to .company/logs/cycles.jsonl for a
#      cycle history view in the dashboard.
#
# Usage:
#   echo "<prompt>" | claude-spawn.sh
#   claude-spawn.sh -- "<prompt>"
#   claude-spawn.sh --no-mcp -- "<prompt>"
#
# Caller-supplied identity (recommended — read by the dashboard):
#   HIVE_AGENT              agent name, e.g. "vp-it", "ceo", "ic-engineer"
#   HIVE_DEPT               dept the spawn is for, e.g. "it", "frontend-web"
#   HIVE_ROLE               role label, e.g. "CEO", "VP", "IC", "verification-IC"
#   HIVE_TICKET             ticket id this spawn is acting on (optional)
#
# Model override (optional):
#   HIVE_VERIFICATION_MODEL - model name for verification agents only.
#      When HIVE_ROLE contains "verification" and this is set, the Claude
#      CLI is invoked with --model "$HIVE_VERIFICATION_MODEL". This lets
#      verification use a stronger model than the default without changing
#      the model for all other agents.

# Source the user's hive env file (if present) so daemons spawned via nohup,
# systemd, etc. — which don't see ~/.zshrc or ~/.bashrc — still get
# LLM endpoint config, CLAUDE_BIN, etc.
HIVE_ENV_FILE="${HIVE_ENV_FILE:-$HOME/.config/hive/env.sh}"
[ -f "$HIVE_ENV_FILE" ] && . "$HIVE_ENV_FILE"

CLAUDE_BIN="${CLAUDE_BIN:-claude}"
# stream-json + verbose gives the dashboard live tool-call + assistant-text
# visibility. Final result event is still the last line so existing log
# consumers that grep for `"type":"result"` still work.
CLAUDE_FLAGS_DEFAULT=(--dangerously-skip-permissions --output-format stream-json --verbose)
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
# claude process starts in the right directory. Without this, agents spawn in
# the caller's cwd (usually the project root) and run tests / make changes in
# main instead of their feature branch worktree.
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

# Build CLI flags
read -r -a CLAUDE_FLAGS <<<"${CLAUDE_FLAGS:-${CLAUDE_FLAGS_DEFAULT[*]}}"

# Verification agents get a stronger model when HIVE_VERIFICATION_MODEL is set.
# This addresses the pain point where a weak model (e.g. quantized/distilled)
# cannot reliably assess code correctness during verification.
# Usage: export HIVE_VERIFICATION_MODEL="claude-sonnet-5" (or any model the
# Claude Code CLI recognizes). Falls back to the default model.
ROLE_LOWER="${HIVE_ROLE,,}"
if [[ "$ROLE_LOWER" == *verification* ]] && [ -n "${HIVE_VERIFICATION_MODEL:-}" ]; then
  CLAUDE_FLAGS+=(--model "$HIVE_VERIFICATION_MODEL")
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
  CLAUDE_FLAGS+=(--mcp-config "$MCP_CONFIG")
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
  echo "flags: ${CLAUDE_FLAGS[*]}"
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
      "session_id":    d.get("session_id"),
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

# Invoke claude. tee stream events into the per-spawn stream file AND to
# stdout (so existing callers' redirects to vp-X.log etc. still capture
# everything). pipefail is OFF intentionally — we want claude's exit code to
# propagate even if tee succeeds.
#
# (HIVE_REQUIRE_PLAN-driven two-phase plan/execute was attempted in 0.4.18-
# 0.4.26. It hit two un-fixable issues in non-interactive `-p` mode:
# (1) `--permission-mode plan` and `--dangerously-skip-permissions` conflict;
# (2) ExitPlanMode prompts a user that doesn't exist and auto-declines,
# trapping the agent in a loop. Reverted to single-phase. Planning is now a
# prompt-level expectation, not a runtime enforcement.)
"$CLAUDE_BIN" -p "$PROMPT" "${CLAUDE_FLAGS[@]}" | tee "$STREAM_FILE"
exit ${PIPESTATUS[0]:-0}
