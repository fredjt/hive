#!/usr/bin/env bash
set -euo pipefail

# init-manifest.sh - Create initial manifest.json
# Usage: init-manifest.sh <project-name> <auto-mode:true|false> [options]
# Options: --mode build|perpetual, --cycle-count N
# Returns: JSON on stdout

PROJECT_NAME=""
AUTO_MODE="false"
MODE="build"
CYCLE_COUNT=0

# Parse arguments
if [ $# -lt 2 ]; then
  echo "Usage: init-manifest.sh <project-name> <auto-mode:true|false> [--mode build|perpetual] [--cycle-count N]" >&2
  exit 1
fi

PROJECT_NAME="$1"
AUTO_MODE="$2"
shift 2

# Parse optional flags
while [ $# -gt 0 ]; do
  case "$1" in
    --mode)
      MODE="$2"
      shift 2
      ;;
    --cycle-count)
      CYCLE_COUNT="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

# Validate auto-mode
if [ "$AUTO_MODE" != "true" ] && [ "$AUTO_MODE" != "false" ]; then
  echo "Error: auto-mode must be 'true' or 'false', got: $AUTO_MODE" >&2
  exit 1
fi

# Validate mode
if [ "$MODE" != "build" ] && [ "$MODE" != "perpetual" ]; then
  echo "Error: mode must be 'build' or 'perpetual', got: $MODE" >&2
  exit 1
fi

# Get timestamp
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Determine COMPANY_DIR (use env var or default to current dir)
COMPANY_DIR="${COMPANY_DIR:-.company}"
# shellcheck source=/dev/null
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/hive-state.sh"

# Create manifest JSON
hs --company-dir "$COMPANY_DIR" write "manifest.json" --stdin <<EOF
{
  "project_name": "$PROJECT_NAME",
  "status": "intake",
  "mode": "$MODE",
  "auto_mode": $AUTO_MODE,
  "created_at": "$TIMESTAMP",
  "updated_at": "$TIMESTAMP",
  "cycle_count": $CYCLE_COUNT,
  "current_sprint": null,
  "perpetual_since": null,
  "idle_mode": false
}
EOF

# Output the created JSON to stdout
cat "$COMPANY_DIR/manifest.json"
