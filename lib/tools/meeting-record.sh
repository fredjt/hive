#!/usr/bin/env bash
set -euo pipefail

# meeting-record.sh - Record meeting notes
# Usage: meeting-record.sh <type> <timestamp> [--content "..." | --stdin]
# Types: standup, sprint-review, sprint-planning, sprint-retro, emergency
# Returns: Absolute path to written file

if [ $# -lt 2 ]; then
  echo "Usage: meeting-record.sh <type> <timestamp> [--content \"...\" | --stdin]" >&2
  exit 1
fi

TYPE="$1"
TIMESTAMP="$2"
shift 2

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

# Validate type
case "$TYPE" in
  standup|sprint-review|sprint-planning|sprint-retro|emergency)
    ;;
  *)
    echo "Error: Unknown meeting type: $TYPE" >&2
    echo "Valid types: standup, sprint-review, sprint-planning, sprint-retro, emergency" >&2
    exit 1
    ;;
esac

# Determine COMPANY_DIR
COMPANY_DIR="${COMPANY_DIR:-.company}"
MEETINGS_DIR="$COMPANY_DIR/meetings/history"
mkdir -p "$MEETINGS_DIR"

# Create meeting file
MEETING_FILE="$MEETINGS_DIR/${TIMESTAMP}-${TYPE}.md"

# Write content
if [ "$USE_STDIN" = true ]; then
  cat > "$MEETING_FILE"
else
  echo "$CONTENT" > "$MEETING_FILE"
fi

# Output absolute path
realpath "$MEETING_FILE"
