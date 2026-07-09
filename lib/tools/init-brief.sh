#!/usr/bin/env bash
set -euo pipefail

# init-brief.sh - Write project brief
# Usage: init-brief.sh <brief-text> [--clarifications "text"]
# Returns: 0 on success

BRIEF_TEXT=""
CLARIFICATIONS=""

# Parse arguments
if [ $# -lt 1 ]; then
  echo "Usage: init-brief.sh <brief-text> [--clarifications \"text\"]" >&2
  exit 1
fi

BRIEF_TEXT="$1"
shift

# Parse optional flags
while [ $# -gt 0 ]; do
  case "$1" in
    --clarifications)
      CLARIFICATIONS="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

# Determine COMPANY_DIR
COMPANY_DIR="${COMPANY_DIR:-.company}"

# Create brief.md
cat > "$COMPANY_DIR/brief.md" <<EOF
# Project Brief

$BRIEF_TEXT
EOF

# Add clarifications section if provided
if [ -n "$CLARIFICATIONS" ]; then
  cat >> "$COMPANY_DIR/brief.md" <<EOF

## Clarifications
$CLARIFICATIONS
EOF
fi

echo "Brief written to $COMPANY_DIR/brief.md" >&2
exit 0
