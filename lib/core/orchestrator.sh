#!/usr/bin/env bash
set -euo pipefail

# orchestrator.sh — initial-build CEO spawn + transition to perpetual mode.
# Replaces SKILL.md Steps 4-7.
#
# Usage: orchestrator.sh <project-root> [--auto]
#
# Flow:
#   1. Read CEO prompt from lib/agents/ceo.md
#   2. Append the assignment block (project root, mode FRESH/RESUME, auto flag)
#   3. Invoke agent-spawn.sh with the prompt - CEO does Phases 0-5 (build the
#      initial system, spawn VPs, run waves, integration, final report)
#   4. When CEO returns INITIAL_BUILD_COMPLETE, exec company-start.sh to launch
#      all perpetual daemons
#
# This script blocks until the CEO finishes initial build. Use `hive start`
# (which calls company-start.sh) to launch daemons without going through CEO.

if [ $# -lt 1 ]; then
  echo "Usage: orchestrator.sh <project-root> [--auto]" >&2
  exit 1
fi

PROJECT_ROOT="$(cd "$1" && pwd)"
shift

AUTO=false
[ "${1:-}" = "--auto" ] && AUTO=true

HIVE_ROOT="${HIVE_ROOT:-$HOME/.local/share/hive}"
export COMPANY_DIR="$PROJECT_ROOT/.company"
export SKILL_DIR="$HIVE_ROOT"

# shellcheck source=/dev/null
. "$HIVE_ROOT/lib/tools/hive-state.sh"
MANIFEST="$COMPANY_DIR/manifest.json"
[ -f "$MANIFEST" ] || { echo "No manifest at $MANIFEST. Run 'hive init' first." >&2; exit 1; }

# Determine FRESH vs RESUME from manifest mode
MODE_FROM_MANIFEST=$(jq -r '.mode // "build"' "$MANIFEST")
SPAWN_MODE="FRESH"
[ "$MODE_FROM_MANIFEST" = "perpetual" ] && SPAWN_MODE="RESUME"

CEO_PROMPT_FILE="$HIVE_ROOT/lib/agents/ceo.md"
[ -f "$CEO_PROMPT_FILE" ] || { echo "CEO prompt not found at $CEO_PROMPT_FILE" >&2; exit 1; }

CEO_PROMPT="$(cat "$CEO_PROMPT_FILE")
--- Assignment ---
Project root: $PROJECT_ROOT
Brief path: $COMPANY_DIR/brief.md
Manifest path: $MANIFEST
Mode: $SPAWN_MODE
Auto-mode: $AUTO

Hive MCP server is available. Use MCP tools (ticket_create, chat_send, etc.)
in preference to bash scripts wherever possible.

When initial build is complete, return the literal string INITIAL_BUILD_COMPLETE
on its own line."

echo "Spawning CEO ($SPAWN_MODE mode)..." >&2
CEO_OUTPUT="$(echo "$CEO_PROMPT" | bash "$HIVE_ROOT/lib/core/agent-spawn.sh")"

# Save CEO output for debugging
# Raw agent transcript (not guaranteed JSON) — a debug log, not company state.
printf '%s\n' "$CEO_OUTPUT" > "$COMPANY_DIR/logs/ceo-initial-build.log"

if echo "$CEO_OUTPUT" | grep -q "INITIAL_BUILD_COMPLETE"; then
  echo "CEO initial build complete. Transitioning to perpetual mode..." >&2

  # Update manifest mode to perpetual (state-layer locked write)
  jq '.mode = "perpetual" | .perpetual_since = (now | strftime("%Y-%m-%dT%H:%M:%SZ"))' \
    "$MANIFEST" | hs --company-dir "$COMPANY_DIR" write "manifest.json" --stdin >/dev/null

  # Launch all daemons
  exec bash "$HIVE_ROOT/lib/tools/company-start.sh" "$PROJECT_ROOT"
else
  echo "CEO did not return INITIAL_BUILD_COMPLETE. See $COMPANY_DIR/logs/ceo-initial-build.log" >&2
  exit 1
fi
