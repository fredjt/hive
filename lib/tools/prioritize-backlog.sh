#!/usr/bin/env bash
set -euo pipefail

# prioritize-backlog.sh - Pick the next ticket to promote from backlog to sprint.
# Uses deterministic ordering — no external model needed.
#
# Ordering rules:
#   1. Sort by priority (CRITICAL > HIGH > MEDIUM > LOW)
#   2. Within same priority, oldest first (by ticket creation date)
#
# Usage: prioritize-backlog.sh <dept> [--limit N]
# Output: Ticket ID(s) of the highest-priority pick(s), one per line.

if [ $# -lt 1 ]; then
  echo "Usage: prioritize-backlog.sh <dept> [--limit N]" >&2
  exit 1
fi

DEPT="$1"
shift
LIMIT=1
while [ $# -gt 0 ]; do
  case "$1" in
    --limit) LIMIT="$2"; shift 2 ;;
    *) shift ;;
  esac
done

COMPANY_DIR="${COMPANY_DIR:-.company}"
BACKLOG_DIR="$COMPANY_DIR/departments/$DEPT/tickets/backlog"

[ -d "$BACKLOG_DIR" ] || { echo "No backlog for $DEPT" >&2; exit 0; }

# Build (priority_rank, mtime, ticket_id, title, description) per ticket and sort.
PICKS=$(find "$BACKLOG_DIR" -maxdepth 1 -name '*.json' -type f 2>/dev/null | while read -r f; do
  jq -r '
    [
      ((.priority // "MEDIUM") | ascii_upcase | (
        if . == "CRITICAL" then 0
        elif . == "HIGH" then 1
        elif . == "MEDIUM" then 2
        elif . == "LOW" then 3
        else 4 end)),
      (.created_at // ""),
      .id,
      (.title // ""),
      (.description // "" | .[0:200])
    ] | @tsv
  ' "$f" 2>/dev/null
done | sort -t$'\t' -k1,1n -k2,2)

if [ -z "$PICKS" ]; then
  exit 0
fi

# Group by priority - if top group has only one ticket, just emit it.
# If multiple, break ties by age (deterministic — no model involved).
TOP_RANK=$(echo "$PICKS" | head -1 | cut -f1)
TOP_GROUP=$(echo "$PICKS" | awk -F'\t' -v r="$TOP_RANK" '$1 == r')
TOP_COUNT=$(echo "$TOP_GROUP" | wc -l)

emit_default() {
  echo "$PICKS" | head -n "$LIMIT" | cut -f3
}

if [ "$TOP_COUNT" -le 1 ] || [ "$TOP_COUNT" -le "$LIMIT" ]; then
  emit_default
  exit 0
fi

# Multiple tickets at the top priority — use deterministic ordering:
# oldest first (already sorted by created_at), capped at --limit.
emit_default
