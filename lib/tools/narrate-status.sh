#!/usr/bin/env bash
set -euo pipefail

# narrate-status.sh - Turn a derived status.json into a one-sentence narrative
# for use in chat messages, marketing reports, or standup summaries.
#
# Uses a templated summary based on health status — no external model needed.
#
# Usage: narrate-status.sh <dept>
# Output: One sentence on stdout.

if [ $# -lt 1 ]; then
  echo "Usage: narrate-status.sh <dept>" >&2
  exit 1
fi

DEPT="$1"
COMPANY_DIR="${COMPANY_DIR:-.company}"
STATUS_FILE="$COMPANY_DIR/departments/$DEPT/status.json"

if [ ! -f "$STATUS_FILE" ]; then
  echo "$DEPT: status unavailable"
  exit 0
fi

# Templated summary — good enough for all cases
health=$(jq -r '.health' "$STATUS_FILE")
vp_status=$(jq -r '.vp_status' "$STATUS_FILE")
in_prog=$(jq -r '.tickets.in_progress' "$STATUS_FILE")
in_rev=$(jq -r '.tickets.in_review' "$STATUS_FILE")
done_ct=$(jq -r '.tickets.done' "$STATUS_FILE")
stuck=$(jq -r '.stuck_tickets' "$STATUS_FILE")
velocity=$(jq -r '.velocity_per_window' "$STATUS_FILE")

case "$health" in
  critical)
    echo "$DEPT is in critical state with $stuck stuck tickets and VP $vp_status."
    ;;
  warning)
    echo "$DEPT is degraded: $in_prog in progress, $stuck stuck, velocity $velocity."
    ;;
  good)
    echo "$DEPT is healthy: $in_prog in progress, $done_ct done this window, $in_rev awaiting review."
    ;;
  *)
    echo "$DEPT: $in_prog active, $in_rev pending review, $done_ct completed, health=$health."
    ;;
esac
