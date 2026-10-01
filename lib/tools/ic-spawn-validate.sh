#!/bin/bash
# Helper script to validate IC spawn commands before execution
# Usage: ic-spawn-validate.sh '<agent-harness command>'
# Returns 0 if valid, 1 if invalid with error message

COMMAND="$1"

if [ -z "$COMMAND" ]; then
  echo "ERROR: No command provided" >&2
  echo "Usage: ic-spawn-validate.sh '<agent harness command>'" >&2
  exit 1
fi

# Check for common placeholder variables that indicate incomplete substitution
PLACEHOLDERS=(
  "TICKET_PATH"
  "WORKTREE_PATH"
  "PROJECT_ROOT"
  "SKILL_DIR"
  "DEPT"
  "TICKET_ID"
)

ERRORS=()

for placeholder in "${PLACEHOLDERS[@]}"; do
  if echo "$COMMAND" | grep -q "\b$placeholder\b"; then
    ERRORS+=("Found unsubstituted placeholder: $placeholder")
  fi
done

# Check for required components
if ! echo "$COMMAND" | grep -q "agent-harness.py\|agent-spawn.sh"; then
  ERRORS+=("Missing agent harness command (agent-harness.py or agent-spawn.sh)")
fi

if ! echo "$COMMAND" | grep -q "Your ticket is at"; then
  ERRORS+=("Missing ticket path specification")
fi

if ! echo "$COMMAND" | grep -q "worktree at"; then
  ERRORS+=("Missing worktree path specification")
fi

if ! echo "$COMMAND" | grep -q "ticket-move.sh"; then
  ERRORS+=("Missing ticket-move.sh completion command")
fi

# Report results
if [ ${#ERRORS[@]} -eq 0 ]; then
  echo "✓ Spawn command validation PASSED"
  echo "✓ All placeholders substituted"
  echo "✓ Command structure valid"
  exit 0
else
  echo "✗ Spawn command validation FAILED" >&2
  echo "" >&2
  echo "Errors found:" >&2
  for error in "${ERRORS[@]}"; do
    echo "  - $error" >&2
  done
  echo "" >&2
  echo "This command will likely produce EMPTY OUTPUT if executed." >&2
  echo "Fix the errors above before running the spawn command." >&2
  exit 1
fi
