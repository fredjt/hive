#!/usr/bin/env bash
set -euo pipefail

# research-findings.sh - Write R&D research findings
# Usage: research-findings.sh <ticket-id> [--content "..." | --stdin] [--append]
# Returns: Absolute path to written file

if [ $# -lt 1 ]; then
  echo "Usage: research-findings.sh <ticket-id> [--content \"...\" | --stdin] [--append]" >&2
  exit 1
fi

TICKET_ID="$1"
shift

CONTENT=""
USE_STDIN=false
APPEND=false

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
    --append)
      APPEND=true
      shift
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

# Determine COMPANY_DIR
COMPANY_DIR="${COMPANY_DIR:-.company}"
FINDINGS_DIR="$COMPANY_DIR/research/ongoing"
mkdir -p "$FINDINGS_DIR"

FINDINGS_FILE="$FINDINGS_DIR/${TICKET_ID}-findings.md"

# Write content
if [ "$APPEND" = true ]; then
  if [ "$USE_STDIN" = true ]; then
    cat >> "$FINDINGS_FILE"
  else
    echo "$CONTENT" >> "$FINDINGS_FILE"
  fi
else
  # T3 (0.15): tmp + rename — a single commit point. The findings file
  # is read cross-process (other agents); a truncate-then-fill write
  # would expose a truncated file mid-write. The append branch above
  # keeps O_APPEND (>>) — the log class, not the truncate class.
  if [ "$USE_STDIN" = true ]; then
    cat > "$FINDINGS_FILE.tmp.$$"
  else
    printf '%s\n' "$CONTENT" > "$FINDINGS_FILE.tmp.$$"
  fi
  mv -f "$FINDINGS_FILE.tmp.$$" "$FINDINGS_FILE"
fi

# Output absolute path
realpath "$FINDINGS_FILE"
