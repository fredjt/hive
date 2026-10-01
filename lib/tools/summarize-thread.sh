#!/usr/bin/env bash
set -euo pipefail

# summarize-thread.sh - Condense a chat channel into a standup-friendly summary.
# Uses the last substantive messages as the summary — no external model needed.
#
# Usage: summarize-thread.sh <channel> [--last N]
# Output: 1-2 sentence summary of recent activity from the latest messages.

if [ $# -lt 1 ]; then
  echo "Usage: summarize-thread.sh <channel> [--last N]" >&2
  exit 1
fi

CHANNEL="$1"
shift
LAST=20
while [ $# -gt 0 ]; do
  case "$1" in
    --last) LAST="$2"; shift 2 ;;
    *) shift ;;
  esac
done

COMPANY_DIR="${COMPANY_DIR:-.company}"
CHAT_FILE="$COMPANY_DIR/chat/$CHANNEL.md"

if [ ! -f "$CHAT_FILE" ]; then
  echo "$CHANNEL: no activity"
  exit 0
fi

# Read recent messages (4 lines per message is a rough estimate)
MESSAGES=$(tail -n $((LAST * 4)) "$CHAT_FILE" 2>/dev/null || true)

if [ -z "$MESSAGES" ]; then
  echo "$CHANNEL: no recent messages"
  exit 0
fi

# Extract the last 3 substantive lines (skip blanks and separators)
SUBSTANTIVE=$(echo "$MESSAGES" | grep -v '^$' | grep -v '^---' | tail -3)
if [ -z "$SUBSTANTIVE" ]; then
  echo "$CHANNEL: no recent activity"
  exit 0
fi

# Build summary from the last substantive message (usually a decision or update)
LAST_MSG=$(echo "$SUBSTANTIVE" | tail -1)
SECOND_LAST=$(echo "$SUBSTANTIVE" | sed -n '2p')

if [ -n "$SECOND_LAST" ]; then
  echo "$SECOND_LAST. $LAST_MSG"
else
  echo "$LAST_MSG"
fi
