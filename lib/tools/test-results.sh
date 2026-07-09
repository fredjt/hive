#!/usr/bin/env bash
set -euo pipefail

# test-results.sh - Write test results for components
# Usage: test-results.sh <dept> <component> [--content "..." | --stdin] [--format json|markdown]
# Returns: Absolute path to written file

if [ $# -lt 2 ]; then
  echo "Usage: test-results.sh <dept> <component> [--content \"...\" | --stdin] [--format json|markdown]" >&2
  exit 1
fi

DEPT="$1"
COMPONENT="$2"
shift 2

CONTENT=""
USE_STDIN=false
FORMAT="markdown"

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
    --format)
      FORMAT="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

# Validate format
if [ "$FORMAT" != "json" ] && [ "$FORMAT" != "markdown" ]; then
  echo "Error: format must be 'json' or 'markdown', got: $FORMAT" >&2
  exit 1
fi

# Determine COMPANY_DIR
COMPANY_DIR="${COMPANY_DIR:-.company}"
COMPONENT_DIR="$COMPANY_DIR/departments/$DEPT/components/$COMPONENT"
mkdir -p "$COMPONENT_DIR"

# Determine file extension
if [ "$FORMAT" = "json" ]; then
  RESULTS_FILE="$COMPONENT_DIR/test-results.json"
else
  RESULTS_FILE="$COMPONENT_DIR/test-results.md"
fi

# Write content
if [ "$USE_STDIN" = true ]; then
  cat > "$RESULTS_FILE"
else
  echo "$CONTENT" > "$RESULTS_FILE"
fi

# Output absolute path
realpath "$RESULTS_FILE"
