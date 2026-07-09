#!/bin/bash
# Monitors for IC spawn failures across all departments
# Usage: ic-spawn-monitor.sh [--alert-threshold N]
# Checks ticket comments for "IC spawn failed" or "empty output" patterns
# Exits 1 if failures exceed threshold (default: 2)

COMPANY_DIR="${COMPANY_DIR:-.company}"
THRESHOLD="${2:-2}"

if [ ! -d "$COMPANY_DIR" ]; then
  echo "ERROR: COMPANY_DIR not found at $COMPANY_DIR" >&2
  exit 1
fi

FAILURES=()

# Scan all ticket files for spawn failure indicators
while IFS= read -r ticket_file; do
  # Check if ticket has spawn failure comments
  if grep -q "IC spawn failed\|empty output\|spawn attempt.*failed" "$ticket_file" 2>/dev/null; then
    ticket_id=$(basename "$ticket_file" .json)
    dept=$(echo "$ticket_file" | sed -n 's|.*/departments/\([^/]*\)/tickets/.*|\1|p')

    # Count failure mentions
    failure_count=$(grep -c "IC spawn failed\|empty output\|spawn attempt.*failed" "$ticket_file" 2>/dev/null)

    FAILURES+=("$dept/$ticket_id ($failure_count failures)")
  fi
done < <(find "$COMPANY_DIR/departments" -type f -name "*.json" 2>/dev/null)

# Report results
if [ ${#FAILURES[@]} -eq 0 ]; then
  echo "✓ No IC spawn failures detected"
  exit 0
elif [ ${#FAILURES[@]} -le $THRESHOLD ]; then
  echo "⚠ IC spawn failures detected (${#FAILURES[@]} tickets, threshold: $THRESHOLD):"
  for failure in "${FAILURES[@]}"; do
    echo "  - $failure"
  done
  exit 0
else
  echo "✗ CRITICAL: IC spawn failures exceed threshold (${#FAILURES[@]} tickets > $THRESHOLD)" >&2
  echo "" >&2
  echo "Affected tickets:" >&2
  for failure in "${FAILURES[@]}"; do
    echo "  - $failure" >&2
  done
  echo "" >&2
  echo "Action required: Check VP logs and validate spawn commands" >&2
  echo "See: SKILL_DIR/lib/tools/ic-spawn-validate.sh" >&2
  exit 1
fi
