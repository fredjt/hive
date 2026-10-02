#!/usr/bin/env bash
# Stops all company processes for a project.
# Usage: company-stop.sh <project-root>
#
# Strategy:
#   1. Kill PIDs recorded in processes.json (preferred - clean stop)
#   2. Pattern-kill any stragglers matching hive's process signatures
#      (covers both current and legacy paths so upgrades from prior versions
#      still clean up properly)
#   3. Update processes.json to reflect the stop
#
# Self-protection: NEVER kills our own PID, our parent, or any ancestor up the
# tree. This is critical because company-start.sh calls company-stop.sh as its
# first step (to clean stale state) - so the running company-start IS one of
# our ancestors. Without ancestor exclusion we'd SIGTERM our own caller.

set -uo pipefail

PROJECT_ROOT="${1:-.}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" && pwd)"
COMPANY_DIR="$PROJECT_ROOT/.company"

# Build set of ancestor PIDs we must never kill (us, parent, grandparent, ...).
ANCESTOR_PIDS=" $$ "
_walk_pid="$PPID"
while [ -n "$_walk_pid" ] && [ "$_walk_pid" != "0" ] && [ "$_walk_pid" != "1" ]; do
  ANCESTOR_PIDS="$ANCESTOR_PIDS$_walk_pid "
  _walk_pid=$(awk '{print $4}' "/proc/$_walk_pid/stat" 2>/dev/null || echo "")
done

is_ancestor() {
  case "$ANCESTOR_PIDS" in *" $1 "*) return 0 ;; *) return 1 ;; esac
}

echo "Stopping company at $PROJECT_ROOT"
killed=0

# 1. Recorded PIDs (if processes.json exists)
if [ -f "$COMPANY_DIR/processes.json" ]; then
  PIDS=$(python3 -c "
import json
try:
    data=json.load(open('$COMPANY_DIR/processes.json'))
    for p in data.get('processes',[]):
        pid=p.get('pid')
        if pid: print(pid)
except Exception:
    pass
" 2>/dev/null)
  for pid in $PIDS; do
    if kill -0 "$pid" 2>/dev/null; then
      # Kill the whole process group (the bash loop + everything it spawned)
      kill -- -"$pid" 2>/dev/null || kill "$pid" 2>/dev/null
      killed=$((killed + 1))
    fi
  done
fi

# 2. Aggressive pattern-kill for stragglers. Patterns are scoped to either
#    $COMPANY_DIR or $PROJECT_ROOT so we don't touch other projects' processes.
#
# IMPORTANT: We intentionally do NOT kill the dashboard (dashboard/server.js)
# or the MCP server (hive_mcp/server.py). These are shared resources managed
# by `hive` (foreground Ctrl-C) or company-start.sh as daemons. Stopping the
# company should only stop background work, not the UI or tool infrastructure.
#
# Includes legacy paths so upgrades from older hive versions clean up properly.
PATTERNS=(
  # company-start.sh itself (the supervisor process)
  "company-start.sh.*$PROJECT_ROOT"
  # The sprint-lifecycle runner: SOLE consumer of agent-spawn under
  # the ticket-driven lifecycle. Without this pattern, a stale
  # sprint-lifecycle survives hive stop.
  "sprint-lifecycle.sh.*$PROJECT_ROOT"
  "release-test-suite.sh.*$PROJECT_ROOT"
  "release-publish-runner.sh.*$PROJECT_ROOT"
  # Background daemon loops, all scoped to this project
  "while true.*$COMPANY_DIR"
  "while true.*$PROJECT_ROOT"
  "agent-spawn.sh.*$PROJECT_ROOT"
  "sprint-fill.*$COMPANY_DIR"
  "sprint-progress.*$COMPANY_DIR"
  "sprint-lifecycle.*$PROJECT_ROOT"
  "verification-runner.sh.*$PROJECT_ROOT"
  "verification-gates.sh.*$PROJECT_ROOT"
  "worktree-merger.sh.*$PROJECT_ROOT"
  "roster-rebuild.sh.*$COMPANY_DIR"
  "status-rebuild.sh.*$COMPANY_DIR"
  "org-chart-rebuild.sh.*$COMPANY_DIR"
  "verification-gates.sh.*$PROJECT_ROOT"
  "company-health.sh.*$PROJECT_ROOT"
  "status-rebuild.sh.*$COMPANY_DIR"
  "org-chart-rebuild.sh.*$COMPANY_DIR"
  # Orphaned agent-harness processes for this project
  "agent-harness\.py.*$PROJECT_ROOT"
)
# MCP server: NOT killed by stop — it's a shared resource that the
# dashboard uses for tool calls (ticket create/move, gates, etc.).
# Killing it would break the dashboard after stop. The MCP is managed
# by `hive` (foreground Ctrl-C) or company-start.sh as a daemon.
# PATTERNS+=( ... )  # intentionally empty — no MCP kill patterns
for pat in "${PATTERNS[@]}"; do
  for pid in $(pgrep -f "$pat" 2>/dev/null); do
    is_ancestor "$pid" && continue
    if kill "$pid" 2>/dev/null; then
      killed=$((killed + 1))
    elif kill -9 "$pid" 2>/dev/null; then
      killed=$((killed + 1))
    fi
  done
done

# Brief pause then SIGKILL anything still alive matching the patterns
sleep 1
for pat in "${PATTERNS[@]}"; do
  for pid in $(pgrep -f "$pat" 2>/dev/null); do
    is_ancestor "$pid" && continue
    kill -9 "$pid" 2>/dev/null && killed=$((killed + 1))
  done
done

# 3. Update processes.json
if [ -f "$COMPANY_DIR/processes.json" ]; then
  python3 -c "
import json
try:
    data=json.load(open('$COMPANY_DIR/processes.json'))
    data['stopped_at']='$(date -u +%Y-%m-%dT%H:%M:%SZ)'
    data['processes']=[]
    json.dump(data, open('$COMPANY_DIR/processes.json','w'), indent=2)
except Exception:
    pass
" 2>/dev/null
fi

echo "Stopped $killed processes"
