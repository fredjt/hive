#!/usr/bin/env bash
set -euo pipefail

# prioritize-backlog.sh - Pick the next ticket to promote from backlog to sprint.
# Uses the small model to break ties when explicit priority fields are equal.
#
# The default ordering rules are deterministic and run without the small model:
#   1. Sort by priority (CRITICAL > HIGH > MEDIUM > LOW)
#   2. Within same priority, oldest first (by ticket creation date)
# The small model only gets called when there are 3+ tickets at the SAME
# priority and we want a smarter pick (e.g. blockers, dependencies).
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
# If multiple, ask small model to pick the most important.
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

# Multiple tickets at the top priority - ask small model to pick.
# Build a numbered list of ticket titles for the model to choose from.
NUMBERED=""
IDS=()
i=0
while IFS=$'\t' read -r _ _ id title desc; do
  i=$((i + 1))
  IDS+=("$id")
  NUMBERED+="$i. $title — $desc"$'\n'
  [ "$i" -ge 10 ] && break    # cap context for tiny model
done <<<"$TOP_GROUP"

# Build a choice string "1|2|3|..."
CHOICES=$(seq 1 "$i" | paste -sd'|')

PROMPT="Pick the ticket that should be worked on FIRST. Prefer ones that:
- Block other work (security, infrastructure, broken dependencies)
- Affect users in production
- Look quick to ship

Output ONLY the number (1-$i), nothing else.

Tickets:
$NUMBERED"

if PICK=$(bash "$SCRIPT_DIR/llm-tiny.sh" --choice "$CHOICES" "$PROMPT" 2>/dev/null); then
  IDX=$((PICK - 1))
  if [ "$IDX" -ge 0 ] && [ "$IDX" -lt "${#IDS[@]}" ]; then
    echo "${IDS[$IDX]}"
    # Emit additional picks in original deterministic order if --limit > 1
    if [ "$LIMIT" -gt 1 ]; then
      remaining=$((LIMIT - 1))
      echo "$PICKS" | cut -f3 | grep -v "^${IDS[$IDX]}$" | head -n "$remaining"
    fi
    exit 0
  fi
fi

# Fall back to deterministic order
emit_default
