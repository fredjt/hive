#!/usr/bin/env bash
set -euo pipefail

# inbox-write.sh - Write a user request to the inbox.
# Usage: inbox-write.sh <type:auto|bug|feature|feedback> [--dept auto|<name>] [--content "..." | --stdin]
# Returns: Entry number in inbox (on stdout)
#
# When type=auto or dept=auto, classify-ticket.sh is called - that script
# routes through the small CPU model (no main-model turn) and falls back
# to safe defaults if the model is unreachable.

if [ $# -lt 1 ]; then
  echo "Usage: inbox-write.sh <type:auto|bug|feature|feedback> [--dept auto|<name>] [--content \"...\" | --stdin]" >&2
  exit 1
fi

TYPE="$1"
shift

DEPT="auto"
CONTENT=""
USE_STDIN=false

while [ $# -gt 0 ]; do
  case "$1" in
    --dept)    DEPT="$2"; shift 2 ;;
    --content) CONTENT="$2"; shift 2 ;;
    --stdin)   USE_STDIN=true; shift ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

case "$TYPE" in
  auto|bug|feature|feedback) ;;
  *) echo "Error: type must be 'auto', 'bug', 'feature', or 'feedback', got: $TYPE" >&2; exit 1 ;;
esac

COMPANY_DIR="${COMPANY_DIR:-.company}"
INBOX_FILE="$COMPANY_DIR/inbox/user-requests.md"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$COMPANY_DIR/inbox"
if [ ! -f "$INBOX_FILE" ]; then
  printf '# User Requests Inbox\n\n' > "$INBOX_FILE"
fi

# Read content
if [ "$USE_STDIN" = true ]; then
  CONTENT=$(cat)
fi

# Auto-classify type and/or department via the small model (no main-model turn).
# Falls back to "feature"/"unknown" silently if the small model is unreachable.
CLASSIFICATION_NOTE=""
if [ "$TYPE" = "auto" ] || [ "$DEPT" = "auto" ]; then
  if CLASS_JSON=$(COMPANY_DIR="$COMPANY_DIR" bash "$SCRIPT_DIR/classify-ticket.sh" "$CONTENT" 2>/dev/null); then
    [ "$TYPE" = "auto" ] && TYPE=$(echo "$CLASS_JSON" | jq -r '.type')
    [ "$DEPT" = "auto" ] && DEPT=$(echo "$CLASS_JSON" | jq -r '.department')
    CLASS_SOURCE=$(echo "$CLASS_JSON" | jq -r '.source')
    CLASSIFICATION_NOTE=" (classified-by: $CLASS_SOURCE)"
  else
    [ "$TYPE" = "auto" ] && TYPE="feature"
    [ "$DEPT" = "auto" ] && DEPT="unknown"
    CLASSIFICATION_NOTE=" (classified-by: fallback)"
  fi
fi

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
# grep -c exits 1 when no matches - guard against that producing "0\n0"
ENTRY_NUM=$(grep -c "^\[" "$INBOX_FILE" 2>/dev/null) || ENTRY_NUM=0
ENTRY_NUM=$((ENTRY_NUM + 1))

{
  echo "[$TIMESTAMP] TYPE: $TYPE DEPT: $DEPT$CLASSIFICATION_NOTE"
  echo "$CONTENT"
  echo ""
} >> "$INBOX_FILE"

echo "$ENTRY_NUM"
