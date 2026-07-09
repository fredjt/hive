#!/usr/bin/env bash
set -euo pipefail

# summarize-thread.sh - Condense a chat channel into a standup-friendly summary.
# Replaces what meeting-runner.sh would otherwise spend a main-model turn on.
#
# Usage: summarize-thread.sh <channel> [--last N]
# Output: 2-3 sentence summary of recent activity. Falls back to last-message
#         echo if the small model is unreachable.

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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPANY_DIR="${COMPANY_DIR:-.company}"
CHAT_FILE="$COMPANY_DIR/chat/$CHANNEL.md"

if [ ! -f "$CHAT_FILE" ]; then
  echo "$CHANNEL: no activity"
  exit 0
fi

# Use existing chat-read.sh if present (handles message parsing)
MESSAGES=""
if [ -x "$SCRIPT_DIR/chat-read.sh" ]; then
  MESSAGES=$(COMPANY_DIR="$COMPANY_DIR" bash "$SCRIPT_DIR/chat-read.sh" "$CHANNEL" --last "$LAST" 2>/dev/null || true)
fi
# Fallback to raw tail if chat-read isn't available or fails
if [ -z "$MESSAGES" ]; then
  MESSAGES=$(tail -n $((LAST * 4)) "$CHAT_FILE" 2>/dev/null || true)
fi

if [ -z "$MESSAGES" ]; then
  echo "$CHANNEL: no recent messages"
  exit 0
fi

# Cap at 3KB - tiny model context is precious
if [ "${#MESSAGES}" -gt 3072 ]; then
  MESSAGES="...[truncated]...$(echo "$MESSAGES" | tail -c 3072)"
fi

PROMPT="Summarize the recent activity in this team chat in 2 SHORT sentences. Focus on: what was decided, what got shipped, what is blocked. No fluff.

Chat:
$MESSAGES"

if RESULT=$(LLM_TINY_MAX_TOKENS=120 bash "$SCRIPT_DIR/llm-tiny.sh" "$PROMPT" 2>/dev/null); then
  echo "$RESULT" | sed 's/^"//; s/"$//'
else
  # Fallback: echo last message line that looks substantive
  echo "$MESSAGES" | grep -v '^$' | grep -v '^---' | tail -1
fi
