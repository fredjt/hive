#!/usr/bin/env bash
set -euo pipefail

# validate-workspace.sh - Workspace integrity checker
# Usage: validate-workspace.sh <project-root>
# Returns: 0 on valid, 1 with error message on invalid

if [ $# -lt 1 ]; then
  echo "Usage: validate-workspace.sh <project-root>" >&2
  exit 1
fi

PROJECT_ROOT="$1"
COMPANY_DIR="$PROJECT_ROOT/.company"
ERRORS=0

echo "Validating workspace at $COMPANY_DIR..." >&2

# Check if .company exists
if [ ! -d "$COMPANY_DIR" ]; then
  echo "ERROR: .company/ directory does not exist at $PROJECT_ROOT" >&2
  exit 1
fi

# Check required directories
REQUIRED_DIRS=(
  "departments/hr"
  "departments/it"
  "departments/rd"
  "departments/marketing"
  "chat"
  "logs"
  "tickets"
  "research/ongoing"
  "marketing/reports"
)

for dir in "${REQUIRED_DIRS[@]}"; do
  if [ ! -d "$COMPANY_DIR/$dir" ]; then
    echo "ERROR: Missing required directory: .company/$dir" >&2
    ((ERRORS++))
  fi
done

# Check required JSON files and validate they're valid JSON
REQUIRED_JSON=(
  "manifest.json"
  "roster.json"
  "tickets/global-counter.json"
)

for json_file in "${REQUIRED_JSON[@]}"; do
  if [ ! -f "$COMPANY_DIR/$json_file" ]; then
    echo "ERROR: Missing required JSON file: .company/$json_file" >&2
    ((ERRORS++))
  elif ! jq empty "$COMPANY_DIR/$json_file" 2>/dev/null; then
    echo "ERROR: Invalid JSON in: .company/$json_file" >&2
    ((ERRORS++))
  fi
done

# Validate manifest.json has required fields
if [ -f "$COMPANY_DIR/manifest.json" ]; then
  for field in project_name status mode created_at; do
    if ! jq -e ".$field" "$COMPANY_DIR/manifest.json" >/dev/null 2>&1; then
      echo "ERROR: manifest.json missing required field: $field" >&2
      ((ERRORS++))
    fi
  done
fi

# Validate roster.json has agents array
if [ -f "$COMPANY_DIR/roster.json" ]; then
  if ! jq -e '.agents' "$COMPANY_DIR/roster.json" >/dev/null 2>&1; then
    echo "ERROR: roster.json missing 'agents' array" >&2
    ((ERRORS++))
  fi
fi

# Check that roster.json agent worktrees exist (if any agents listed)
if [ -f "$COMPANY_DIR/roster.json" ]; then
  AGENT_COUNT=$(jq -r '.agents | length' "$COMPANY_DIR/roster.json" 2>/dev/null || echo "0")
  if [ "$AGENT_COUNT" -gt 0 ]; then
    jq -r '.agents[] | select(.worktree_path) | .worktree_path' "$COMPANY_DIR/roster.json" 2>/dev/null | while read -r worktree; do
      if [ -n "$worktree" ] && [ ! -d "$worktree" ]; then
        echo "WARNING: Roster references missing worktree: $worktree" >&2
      fi
    done
  fi
fi

# Summary
if [ $ERRORS -eq 0 ]; then
  echo "✓ Workspace validation passed" >&2
  exit 0
else
  echo "✗ Workspace validation failed with $ERRORS errors" >&2
  exit 1
fi
