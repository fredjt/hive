#!/usr/bin/env bash
set -euo pipefail

# outbox-write.sh - Write CEO notification/approval to outbox
# Usage: outbox-write.sh <type:notification|approval|question> [--content "..." | --stdin]
# Returns: Absolute path to written file

if [ $# -lt 1 ]; then
  echo "Usage: outbox-write.sh <type:notification|approval|question> [--content \"...\" | --stdin]" >&2
  exit 1
fi

TYPE="$1"
shift

CONTENT=""
USE_STDIN=false

# Parse options
while [ $# -gt 0 ]; do
  case "$1" in
    --content)
      CONTENT="$2"
      shift 2
      ;;
    --stdin)
      USE_STDIN=true
      shift
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

# Determine target file based on type
case "$TYPE" in
  notification)
    OUTBOX_FILE="notifications.md"
    ;;
  approval)
    OUTBOX_FILE="pending-approvals.md"
    ;;
  question)
    OUTBOX_FILE="user-questions.md"
    ;;
  *)
    echo "Error: type must be 'notification', 'approval', or 'question', got: $TYPE" >&2
    exit 1
    ;;
esac

# Determine COMPANY_DIR
COMPANY_DIR="${COMPANY_DIR:-.company}"
OUTBOX_PATH="$COMPANY_DIR/outbox/$OUTBOX_FILE"

# Ensure outbox directory exists
mkdir -p "$COMPANY_DIR/outbox"

# Create outbox file if it doesn't exist
if [ ! -f "$OUTBOX_PATH" ]; then
  case "$TYPE" in
    notification)
      echo "# CEO Notifications" > "$OUTBOX_PATH"
      ;;
    approval)
      echo "# Pending Approvals" > "$OUTBOX_PATH"
      ;;
    question)
      echo "# User Questions" > "$OUTBOX_PATH"
      ;;
  esac
  echo "" >> "$OUTBOX_PATH"
fi

# Get timestamp
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Read content from stdin if requested
if [ "$USE_STDIN" = true ]; then
  CONTENT=$(cat)
fi

# Append entry to outbox
{
  echo "---"
  echo "**[$TIMESTAMP]**"
  echo ""
  echo "$CONTENT"
  echo ""
} >> "$OUTBOX_PATH"

# Output absolute path
realpath "$OUTBOX_PATH"
