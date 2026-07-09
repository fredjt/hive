#!/usr/bin/env bash
set -euo pipefail

# narrate-status.sh - Turn a derived status.json into a one-sentence narrative
# for use in chat messages, marketing reports, or standup summaries.
#
# Replaces what marketing-reporter would otherwise spend a main-model turn on.
#
# Usage: narrate-status.sh <dept>
# Output: One sentence on stdout. Falls back to a templated summary if the
#         small model is unreachable.

if [ $# -lt 1 ]; then
  echo "Usage: narrate-status.sh <dept>" >&2
  exit 1
fi

DEPT="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPANY_DIR="${COMPANY_DIR:-.company}"
STATUS_FILE="$COMPANY_DIR/departments/$DEPT/status.json"

if [ ! -f "$STATUS_FILE" ]; then
  echo "$DEPT: status unavailable"
  exit 0
fi

# Extract the data the model needs - keep it compact
SUMMARY=$(jq -r '
  "health=" + .health +
  " vp=" + .vp_status +
  " sprint=" + (.tickets.sprint_current | tostring) +
  " in_progress=" + (.tickets.in_progress | tostring) +
  " in_review=" + (.tickets.in_review | tostring) +
  " done=" + (.tickets.done | tostring) +
  " stuck=" + (.stuck_tickets | tostring) +
  " velocity=" + (.velocity_per_window | tostring)
' "$STATUS_FILE")

# Templated fallback - good enough if small model is down
fallback_narrate() {
  local health vp_status sprint in_prog in_rev done_ct stuck velocity
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
}

PROMPT="Write ONE short sentence summarizing the health of department '$DEPT'. Be specific and factual, no fluff. Reference the numbers.

Status: $SUMMARY"

if RESULT=$(LLM_TINY_MAX_TOKENS=80 bash "$SCRIPT_DIR/llm-tiny.sh" "$PROMPT" 2>/dev/null); then
  # Strip any leading/trailing quotes the model might add
  echo "$RESULT" | sed 's/^"//; s/"$//'
else
  fallback_narrate
fi
