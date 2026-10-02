#!/usr/bin/env bash
set -euo pipefail

# vp-idle-check.sh — decides whether a VP should run its cycle.
#
# Used by company-start.sh's VP loops as a pre-check, so VPs don't burn a
# main-model turn when there's nothing to do. Static admin VPs (HR/IT/RD/
# Marketing/Legal/Finance) and dynamic dept VPs all use this.
#
# Exit codes:
#   0 = VP SHOULD run (work pending)
#   1 = VP can skip this cycle (nothing to do)
#
# Heuristics:
#   - Has tickets in sprint-current/in-progress/in-review → run
#   - Has new chat messages in dept-<name> since last run → run
#   - Status health is "critical" → run (something needs attention)
#   - Otherwise → skip
#
# Usage: vp-idle-check.sh <dept>
#
# State: $COMPANY_DIR/logs/vp-last-run.<dept> records the last cycle ts.

if [ $# -lt 1 ]; then
  echo "Usage: vp-idle-check.sh <dept>" >&2
  exit 0   # default to run on bad usage to be safe
fi

DEPT="$1"
COMPANY_DIR="${COMPANY_DIR:-.company}"
DEPT_DIR="$COMPANY_DIR/departments/$DEPT"
STATUS_FILE="$DEPT_DIR/status.json"
LAST_RUN_FILE="$COMPANY_DIR/logs/vp-last-run.$DEPT"

# If we can't determine state, default to RUN (don't accidentally never run a VP)
[ -d "$DEPT_DIR" ] || exit 0

# Read status counts. If status.json is missing, run (status-rebuild may be slow on first cycle).
if [ -f "$STATUS_FILE" ]; then
  HEALTH=$(jq -r '.health // "unknown"' "$STATUS_FILE" 2>/dev/null || echo "unknown")
  SPRINT=$(jq -r '.tickets.sprint_current // 0' "$STATUS_FILE" 2>/dev/null || echo 0)
  INPROG=$(jq -r '.tickets.in_progress    // 0' "$STATUS_FILE" 2>/dev/null || echo 0)
  INREV=$(jq  -r '.tickets.in_review      // 0' "$STATUS_FILE" 2>/dev/null || echo 0)
  STUCK=$(jq  -r '.stuck_tickets          // 0' "$STATUS_FILE" 2>/dev/null || echo 0)
  ACTIVE=$((SPRINT + INPROG + INREV + STUCK))
else
  exit 0
fi

# Critical health → always run, even if no tickets
if [ "$HEALTH" = "critical" ]; then
  echo "run" > "$LAST_RUN_FILE.reason"
  date -u +%s > "$LAST_RUN_FILE"
  exit 0
fi

# Active tickets → run
if [ "$ACTIVE" -gt 0 ]; then
  echo "run:$ACTIVE active tickets" > "$LAST_RUN_FILE.reason"
  date -u +%s > "$LAST_RUN_FILE"
  exit 0
fi

LAST_RUN_TS=0
[ -f "$LAST_RUN_FILE" ] && LAST_RUN_TS=$(cat "$LAST_RUN_FILE" 2>/dev/null || echo 0)

# Chat freshness checks
if file_changed_since "$COMPANY_DIR/chat/dept-$DEPT.md" "$LAST_RUN_TS"; then
  echo "run:dept-chat-updated" > "$LAST_RUN_FILE.reason"
  date -u +%s > "$LAST_RUN_FILE"
  exit 0
fi
if file_changed_since "$COMPANY_DIR/chat/leadership.md" "$LAST_RUN_TS"; then
  echo "run:leadership-chat-updated" > "$LAST_RUN_FILE.reason"
  date -u +%s > "$LAST_RUN_FILE"
  exit 0
fi

# Force a run at least every 30 minutes regardless, so the VP can refresh strategy docs etc.
NOW=$(date -u +%s)
if [ $((NOW - LAST_RUN_TS)) -gt 1800 ]; then
  echo "run:30-min-heartbeat" > "$LAST_RUN_FILE.reason"
  date -u +%s > "$LAST_RUN_FILE"
  exit 0
fi

# Nothing to do
echo "skip:idle (active=0, no chat changes, last run $((NOW - LAST_RUN_TS))s ago)" > "$LAST_RUN_FILE.reason"
exit 1
