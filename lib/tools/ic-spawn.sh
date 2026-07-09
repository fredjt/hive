#!/bin/bash
# Safe IC spawn wrapper with automatic validation
# Usage: ic-spawn.sh <department> <ticket-id> <project-root> <skill-dir>
# Automatically constructs and validates spawn command before execution
# Prevents empty output failures from unsubstituted placeholders

set -euo pipefail

DEPT="$1"
TICKET_ID="$2"
PROJECT_ROOT="$3"
SKILL_DIR="$4"

if [ -z "$DEPT" ] || [ -z "$TICKET_ID" ] || [ -z "$PROJECT_ROOT" ] || [ -z "$SKILL_DIR" ]; then
  echo "ERROR: Missing required arguments" >&2
  echo "Usage: ic-spawn.sh <department> <ticket-id> <project-root> <skill-dir>" >&2
  echo "Example: ic-spawn.sh backend-api-services TICK-API-0097 /home/user/project /home/user/.claude/skills/company" >&2
  exit 1
fi

# Validate inputs exist
if [ ! -d "$PROJECT_ROOT" ]; then
  echo "ERROR: PROJECT_ROOT does not exist: $PROJECT_ROOT" >&2
  exit 1
fi

if [ ! -d "$SKILL_DIR" ]; then
  echo "ERROR: SKILL_DIR does not exist: $SKILL_DIR" >&2
  exit 1
fi

COMPANY_DIR="${PROJECT_ROOT}/.company"
if [ ! -d "$COMPANY_DIR" ]; then
  echo "ERROR: COMPANY_DIR not found at $COMPANY_DIR" >&2
  exit 1
fi

# Find ticket file
TICKET_PATH=""
for status_dir in in-progress sprint-current backlog; do
  candidate="${COMPANY_DIR}/departments/${DEPT}/tickets/${status_dir}/${TICKET_ID}.json"
  if [ -f "$candidate" ]; then
    TICKET_PATH="$candidate"
    break
  fi
done

if [ -z "$TICKET_PATH" ]; then
  echo "ERROR: Ticket not found: $TICKET_ID in department $DEPT" >&2
  echo "Searched: in-progress, sprint-current, backlog" >&2
  exit 1
fi

# Determine role based on ticket ID prefix
ROLE="IC-Engineer"
if [[ "$TICKET_ID" == IT-* ]]; then
  ROLE="IC-Infra"
fi

# Create worktree
echo "Creating worktree for $TICKET_ID..."
WORKTREE_OUTPUT=$(bash "$SKILL_DIR/lib/tools/git-worktree.sh" create "$TICKET_ID" "$PROJECT_ROOT" 2>&1)
if [ $? -ne 0 ]; then
  echo "ERROR: Failed to create worktree" >&2
  echo "$WORKTREE_OUTPUT" >&2
  exit 1
fi

# Extract worktree path from output
WORKTREE_PATH=$(echo "$WORKTREE_OUTPUT" | grep -o "/[^[:space:]]*\.worktrees/${TICKET_ID}" | head -1)
if [ -z "$WORKTREE_PATH" ]; then
  echo "ERROR: Could not determine worktree path from output" >&2
  echo "$WORKTREE_OUTPUT" >&2
  exit 1
fi

# Update roster
echo "Updating roster..."
bash "$SKILL_DIR/lib/tools/roster-update.sh" add "$ROLE" "$DEPT" active "ic-${TICKET_ID}" "$TICKET_ID"

# Construct spawn command based on role
if [ "$ROLE" = "IC-Infra" ]; then
  # IT infrastructure role
  SPAWN_CMD="You are an IC-Infra engineer. Your ticket is at ${TICKET_PATH}. Read it. You work in your own git worktree at ${WORKTREE_PATH} (branch feature/${TICKET_ID}). Implement what it describes - build system config, CI/CD, dependency updates, server setup, tooling, environment config. Commit with format ${TICKET_ID}: description. When done, write a brief summary of what you changed, then move to IN-REVIEW (not done - verification will review it): export COMPANY_DIR=${COMPANY_DIR} && bash ${SKILL_DIR}/tools/ticket-move.sh it ${TICKET_ID} in-review 'YOUR_SUMMARY_OF_CHANGES' 'IC-Infra' && bash ${SKILL_DIR}/tools/chat-send.sh engineering IC-Infra it 'Ready for review: ${TICKET_ID} - YOUR_SUMMARY'"
else
  # Regular IC-Engineer role
  SPAWN_CMD="You are an IC Engineer. Your ticket is at ${TICKET_PATH}. Read it. You work in your own git worktree at ${WORKTREE_PATH} (branch feature/${TICKET_ID}). Implement what it describes by writing code there. Git commit with format ${TICKET_ID}: description. IMPORTANT: Do NOT do infrastructure work (build config, dependency updates, CI/CD, server setup, linting config). If your ticket requires infrastructure changes, file an IT ticket instead: export COMPANY_DIR=${COMPANY_DIR} && bash ${SKILL_DIR}/tools/ticket-create.sh it infrastructure medium 'title' 'description' 'IC-Engineer' 'improvement'. Only write application code for your department. When done, write a brief summary of what you changed, then move to IN-REVIEW (not done - verification will review it): export COMPANY_DIR=${COMPANY_DIR} && bash ${SKILL_DIR}/tools/ticket-move.sh ${DEPT} ${TICKET_ID} in-review 'YOUR_SUMMARY_OF_CHANGES' 'IC-Engineer' && bash ${SKILL_DIR}/tools/chat-send.sh dept-${DEPT} IC-Engineer ${DEPT} 'Ready for review: ${TICKET_ID} - YOUR_SUMMARY'"
fi

# Validate command before execution
echo "Validating spawn command..."
VALIDATION_OUTPUT=$(bash "$SKILL_DIR/lib/tools/ic-spawn-validate.sh" "claude -p \"$SPAWN_CMD\" --output-format json" 2>&1)
VALIDATION_EXIT=$?

if [ $VALIDATION_EXIT -ne 0 ]; then
  echo "ERROR: Spawn command validation FAILED" >&2
  echo "$VALIDATION_OUTPUT" >&2
  echo "" >&2
  echo "This spawn would have produced EMPTY OUTPUT." >&2
  echo "Rolling back worktree creation..." >&2
  # Clean up the worktree we just created (git-worktree.sh has no "remove" action).
  BRANCH="feature/$TICKET_ID"
  WT_PATH="$PROJECT_ROOT/.worktrees/$TICKET_ID"
  git -C "$PROJECT_ROOT" worktree remove "$WT_PATH" --force 2>/dev/null || true
  git -C "$PROJECT_ROOT" branch -D "$BRANCH" 2>/dev/null || true
  rm -rf "$WT_PATH" 2>/dev/null || true
  bash "$SKILL_DIR/lib/tools/roster-update.sh" retire "ic-${TICKET_ID}" "$DEPT" 2>/dev/null || true
  exit 1
fi

echo "$VALIDATION_OUTPUT"
echo ""
echo "Spawning IC for $TICKET_ID..."
echo "Command: claude -p \"$SPAWN_CMD\" --output-format json"
echo ""

# Execute spawn — tag identity for the dashboard's Activity view
HIVE_AGENT_NAME="ic-engineer"
[ "$ROLE" = "IC-Infra" ] && HIVE_AGENT_NAME="ic-infra"
HIVE_AGENT="$HIVE_AGENT_NAME" HIVE_DEPT="$DEPT" HIVE_ROLE="IC" HIVE_TICKET="$TICKET_ID" \
bash "$SKILL_DIR/lib/core/claude-spawn.sh" -- "$SPAWN_CMD"

SPAWN_EXIT=$?

if [ $SPAWN_EXIT -eq 0 ]; then
  echo ""
  echo "✓ IC spawn completed successfully for $TICKET_ID"
  echo "✓ Worktree: $WORKTREE_PATH"
  echo "✓ Ticket should now be in in-review status"
else
  echo "" >&2
  echo "✗ IC spawn failed with exit code $SPAWN_EXIT" >&2
  echo "Manual intervention may be required" >&2
  exit $SPAWN_EXIT
fi
