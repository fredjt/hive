#!/bin/bash
# IC Spawn Diagnostics and Repair Tool
# Usage: ic-spawn-diagnostics.sh [--fix]
# Analyzes IC spawn failures and optionally attempts to fix them

COMPANY_DIR="${COMPANY_DIR:-.company}"
FIX_MODE=false

if [ "$1" = "--fix" ]; then
  FIX_MODE=true
fi

if [ ! -d "$COMPANY_DIR" ]; then
  echo "ERROR: COMPANY_DIR not found at $COMPANY_DIR" >&2
  exit 1
fi

echo "==============================================="
echo "IC Spawn Failure Diagnostics Report"
echo "==============================================="
echo ""
echo "Analysis Date: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "Company Dir: $COMPANY_DIR"
echo ""

# Find all spawn failures
FAILURES=()
FAILURE_DETAILS=()

while IFS= read -r ticket_file; do
  if grep -q "IC spawn failed\|empty output\|spawn attempt.*failed" "$ticket_file" 2>/dev/null; then
    ticket_id=$(basename "$ticket_file" .json)
    dept=$(echo "$ticket_file" | sed -n 's|.*/departments/\([^/]*\)/tickets/.*|\1|p')
    status=$(echo "$ticket_file" | sed -n 's|.*/tickets/\([^/]*\)/.*|\1|p')
    
    # Extract failure details from comments
    failure_count=$(grep -c "IC spawn failed\|empty output\|spawn attempt.*failed" "$ticket_file" 2>/dev/null)
    
    # Get ticket title
    title=$(jq -r '.title // "No title"' "$ticket_file" 2>/dev/null)
    
    FAILURES+=("$dept/$ticket_id")
    FAILURE_DETAILS+=("Dept: $dept | Ticket: $ticket_id | Status: $status | Failures: $failure_count | Title: $title")
  fi
done < <(find "$COMPANY_DIR/departments" -type f -name "*.json" 2>/dev/null)

# Report Summary
echo "SUMMARY"
echo "-------"
echo "Total spawn failures detected: ${#FAILURES[@]}"
echo ""

if [ ${#FAILURES[@]} -eq 0 ]; then
  echo "✓ No IC spawn failures detected"
  echo "✓ System is healthy"
  exit 0
fi

# Detailed breakdown
echo "DETAILED BREAKDOWN"
echo "------------------"
for detail in "${FAILURE_DETAILS[@]}"; do
  echo "  $detail"
done
echo ""

# Root cause analysis
echo "ROOT CAUSE ANALYSIS"
echo "-------------------"
echo ""
echo "Common causes of IC spawn failures (empty output):"
echo "  1. Unsubstituted placeholder variables in spawn command"
echo "  2. Missing or incorrect file paths (ticket, worktree, skill dir)"
echo "  3. VP not using proper variable substitution before spawning"
echo "  4. Worktree creation failures"
echo "  5. Missing project dependencies or environment setup"
echo ""

# Check for monitoring/validation tools
echo "TOOLING STATUS"
echo "--------------"
SKILL_DIR="${HIVE_ROOT:-$HOME/.local/share/hive}"

if [ -n "$SKILL_DIR" ]; then
  echo "✓ Company skill directory found: $SKILL_DIR"
  
  # Check for validation script
  if [ -f "$SKILL_DIR/lib/tools/ic-spawn-validate.sh" ]; then
    echo "✓ ic-spawn-validate.sh exists"
  else
    echo "✗ ic-spawn-validate.sh NOT FOUND (validation tool missing)"
  fi
  
  # Check for monitor script
  if [ -f "$SKILL_DIR/lib/tools/ic-spawn-monitor.sh" ]; then
    echo "✓ ic-spawn-monitor.sh exists"
  else
    echo "✗ ic-spawn-monitor.sh NOT FOUND (monitoring tool missing)"
  fi
  
  # Check if new spawn wrapper exists
  if [ -f "$SKILL_DIR/lib/tools/ic-spawn.sh" ]; then
    echo "✓ ic-spawn.sh wrapper exists (safe spawn with auto-validation)"
  else
    echo "⚠ ic-spawn.sh wrapper NOT FOUND (recommended upgrade not installed)"
  fi
else
  echo "✗ Company skill directory NOT FOUND"
fi
echo ""

# Recommendations
echo "RECOMMENDATIONS"
echo "---------------"
echo ""
echo "Immediate Actions:"
echo "  1. Use agent-spawn.sh wrapper for all agent spawns"
echo "  2. Always validate spawn commands with ic-spawn-validate.sh before execution"
echo "  3. Ensure VPs substitute ALL placeholder variables before spawning"
echo ""
echo "Preventive Measures:"
echo "  1. Install ic-spawn.sh to ${SKILL_DIR}/tools/ (automated safe spawning)"
echo "  2. Add pre-spawn validation hook to VP workflows"
echo "  3. Set up automated monitoring with ic-spawn-monitor.sh"
echo "  4. Update VP documentation to mandate spawn wrapper usage"
echo ""

if [ "$FIX_MODE" = true ]; then
  echo "FIX MODE: Attempting automated repairs..."
  echo ""
  
  # This would require more context about each failure
  # For now, just report that manual intervention is needed
  echo "⚠ Automated fix not yet implemented for spawn failures"
  echo "⚠ Manual intervention required to retry failed tickets"
  echo ""
  echo "Manual fix procedure for each failed ticket:"
  echo "  1. Verify ticket still needs to be implemented"
  echo "  2. Use: bash ic-spawn.sh <dept> <ticket-id> <project-root> <skill-dir>"
  echo "  3. Monitor spawn output for success"
  echo "  4. If still failing, check ticket acceptance criteria complexity"
fi

echo ""
echo "==============================================="
echo "End of Diagnostics Report"
echo "==============================================="

exit 0
