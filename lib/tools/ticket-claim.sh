#!/bin/bash
# Usage: ticket-claim.sh <department> <ticket-id> <role> <agent-id> <agent-name> [worktree-path]
# Example: ticket-claim.sh backend-api-services TICK-API-0015 IC-Engineer ic-api-0015 "IC-Engineer (backend/TICK-API-0015)" /path/.worktrees/TICK-API-0015
#
# Claims a ticket: sets assignee, links git branch, moves to in-progress.
#
# G1/G2: the mutation is the state-layer op ticket-claim (same accessors
# the MCP and lifecycle use). This wrapper keeps the CLI contract.

COMPANY_DIR="${COMPANY_DIR:-.company}"
export COMPANY_DIR
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/hive-state.sh"

DEPT="${1:-}"
TICKET_ID="${2:-}"
ROLE="${3:-}"
AGENT_ID="${4:-}"
AGENT_NAME="${5:-}"
WORKTREE_PATH="${6:-}"

if [ -z "$DEPT" ] || [ -z "$TICKET_ID" ] || [ -z "$ROLE" ] || [ -z "$AGENT_NAME" ]; then
  echo "Usage: ticket-claim.sh <department> <ticket-id> <role> <agent-id> <agent-name> [worktree-path]" >&2
  exit 1
fi

PROJECT_ROOT=$(cd "$COMPANY_DIR/.." 2>/dev/null && pwd)

CLAIM_ARGS=(ticket-claim --dept "$DEPT" --ticket "$TICKET_ID" --role "$ROLE" \
  --agent-id "$AGENT_ID" --agent-name "$AGENT_NAME" --project-root "$PROJECT_ROOT")
[ -n "$WORKTREE_PATH" ] && CLAIM_ARGS+=(--worktree-path "$WORKTREE_PATH")
RESULT=$(hs "${CLAIM_ARGS[@]}" 2>&1)
RC=$?
if [ $RC -ne 0 ]; then
  printf '%s\n' "$RESULT" >&2
  exit $RC
fi

echo "Claimed $TICKET_ID by $AGENT_NAME ($ROLE) -> in-progress (branch: feature/$TICKET_ID)"
