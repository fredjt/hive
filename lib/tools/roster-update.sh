#!/usr/bin/env bash
# DEPRECATED: roster-update.sh is no longer the source of truth.
#
# The roster is now DERIVED automatically by roster-rebuild.sh from:
#   - In-progress tickets (assignee field)
#   - VP log activity
#   - Git worktrees
#
# This wrapper exists only for backward compatibility. It accepts the old
# add/retire/list commands but does NOT mutate roster.json directly.
#
#   add <role> <dept> <status> [agent-id] [ticket]   -> triggers a rebuild (no-op otherwise)
#   retire <agent-id-or-role> <dept>                 -> triggers a rebuild (no-op otherwise)
#   list [--active] [--dept DEPT]                    -> still works (read-only)
#
# Why deprecated: the roster used to drift from reality whenever an agent
# forgot to call retire (or crashed mid-cycle). Now the roster is a view
# computed from authoritative state, so it cannot drift.

set -euo pipefail

COMPANY_DIR="${COMPANY_DIR:-.company}"
ROSTER_FILE="$COMPANY_DIR/roster.json"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ACTION="${1:-}"

case "$ACTION" in
  add|retire)
    # Old behavior was a write; new behavior is a no-op + rebuild.
    # This makes legacy agent prompts safe without giving them roster mutation power.
    echo "[roster-update.sh] DEPRECATED: '$ACTION' is now a no-op." >&2
    echo "[roster-update.sh] Roster is derived from tickets/worktrees/logs by roster-rebuild.sh." >&2
    echo "[roster-update.sh] Triggering a rebuild instead..." >&2
    bash "$SCRIPT_DIR/roster-rebuild.sh" --quiet
    exit 0
    ;;
  list)
    # Read-only listing - keep this working.
    shift
    FILTER_ACTIVE=false
    FILTER_DEPT=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --active) FILTER_ACTIVE=true; shift ;;
        --dept)   FILTER_DEPT="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    # Make sure the cache is fresh before reading.
    bash "$SCRIPT_DIR/roster-rebuild.sh" --quiet 2>/dev/null || true
    if [ ! -f "$ROSTER_FILE" ]; then
      echo "[]"
      exit 0
    fi
    JQ_FILTER='.agents'
    [ "$FILTER_ACTIVE" = true ] && JQ_FILTER="$JQ_FILTER | map(select(.status == \"active\"))"
    [ -n "$FILTER_DEPT" ]      && JQ_FILTER="$JQ_FILTER | map(select(.department == \"$FILTER_DEPT\"))"
    jq "$JQ_FILTER" "$ROSTER_FILE"
    ;;
  ""|-h|--help)
    cat >&2 <<EOF
Usage: roster-update.sh <add|retire|list> [args...]

NOTE: This script is DEPRECATED. The roster is now derived automatically
by roster-rebuild.sh. add/retire are accepted for backward compatibility
but do not mutate the roster - they trigger a rebuild instead.

Use these instead:
  - To see who's working: bash roster-update.sh list [--active] [--dept DEPT]
  - To force a refresh:   bash roster-rebuild.sh
EOF
    exit 1
    ;;
  *)
    echo "[roster-update.sh] Unknown action: $ACTION" >&2
    exit 1
    ;;
esac
