#!/usr/bin/env bash
set -euo pipefail

# hive-killall.sh — emergency kill of every hive-related process.
#
# When `hive stop` can't find/reach orphaned processes (e.g. processes.json
# missing or stale, or company-start crashed before recording PIDs), this
# script kills anything that looks like hive: bash loops running our daemons,
# any spawned agent-harness processes, and the Node dashboard.
#
# Use sparingly — this is a sledgehammer.
#
# Usage: hive-killall.sh [--dry-run]

DRY=false
[ "${1:-}" = "--dry-run" ] && DRY=true

PATTERNS=(
  "hive_mcp.server"                                 # Python MCP server
  "lib/dashboard/server.js"                         # Node dashboard
  "lib/tools/company-start.sh"                      # orchestrator
  "lib/tools/.*-rebuild.sh"                         # roster-rebuild, status-rebuild
  "lib/tools/sprint-fill.sh"
  "lib/tools/sprint-progress.sh"
  "lib/tools/sprint-lifecycle.sh"
  "lib/tools/meeting-runner.sh"
  "lib/tools/verification-runner.sh"
  "lib/tools/verification-gates.sh"
  "lib/tools/worktree-merger.sh"
  "lib/core/agent-spawn.sh"                       # in-flight agent spawns
)

found=0
for pat in "${PATTERNS[@]}"; do
  pids=$(pgrep -f "$pat" 2>/dev/null || true)
  for pid in $pids; do
    [ "$pid" = "$$" ] && continue
    cmdline=$(tr '\0' ' ' < /proc/$pid/cmdline 2>/dev/null | head -c 120 || echo "?")
    if [ "$DRY" = true ]; then
      echo "[dry-run] would kill $pid: $cmdline"
    else
      if kill "$pid" 2>/dev/null; then
        echo "killed $pid: $cmdline"
      elif kill -9 "$pid" 2>/dev/null; then
        echo "force-killed $pid: $cmdline"
      else
        echo "FAILED to kill $pid: $cmdline" >&2
      fi
    fi
    found=$((found+1))
  done
done

if [ "$found" -eq 0 ]; then
  echo "No hive processes found."
  exit 0
fi

if [ "$DRY" = false ]; then
  sleep 1
  survivors=0
  for pat in "${PATTERNS[@]}"; do
    for pid in $(pgrep -f "$pat" 2>/dev/null || true); do
      [ "$pid" = "$$" ] && continue
      kill -9 "$pid" 2>/dev/null && survivors=$((survivors+1)) || true
    done
  done
  [ "$survivors" -gt 0 ] && echo "force-killed $survivors stragglers"
  echo "Killed $found hive processes total."
fi
