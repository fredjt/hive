#!/usr/bin/env bash
set -uo pipefail

# ticket-approve.sh - Verification IC's explicit approval of a ticket.
#
# ATOMIC: stamps verified_by + verified_at + verification_rationale, AND
# moves the ticket to done with reason=implemented. The verifier's intent
# is unambiguous — there's no reason to require a separate ticket-move call.
#
# G1/G2: the stamp + move is ONE state-layer op (lib/hive_state, op
# ticket-approve) — the same accessors the MCP ticket_approve tool and the
# lifecycle share. The PR-merge side-effect is the process edge
# (pr-side-effects.sh), shared with ticket-move.sh.
#
# Usage: ticket-approve.sh <dept> <ticket-id> <verifier-name> <rationale>
#
# The rationale is mandatory and stored on the ticket. It should describe what
# the verifier actually checked (logic, edge cases, contract compliance, etc.)
# beyond what the deterministic gates already covered. One-word approvals are
# rejected to discourage rubber-stamping.

if [ $# -lt 4 ]; then
  echo "Usage: ticket-approve.sh <dept> <ticket-id> <verifier-name> <rationale>" >&2
  echo "" >&2
  echo "The rationale must explain what was checked (min 30 chars):" >&2
  echo "  - logic correctness against ticket description" >&2
  echo "  - edge cases / error paths considered" >&2
  echo "  - contract compliance verified" >&2
  echo "  - any code-quality concerns surfaced" >&2
  exit 1
fi

DEPT="$1"
TICKET_ID="$2"
VERIFIER="$3"
RATIONALE="$4"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPANY_DIR="${COMPANY_DIR:-.company}"
export COMPANY_DIR
source "$SCRIPT_DIR/hive-state.sh"
source "$SCRIPT_DIR/pr-side-effects.sh"

PROJECT_ROOT=$(cd "$COMPANY_DIR/.." 2>/dev/null && pwd)

RESULT=$(hs ticket-approve --dept "$DEPT" --ticket "$TICKET_ID" \
  --verifier "$VERIFIER" --rationale "$RATIONALE" 2>&1)
RC=$?
[ $RC -ne 0 ] && { printf '%s\n' "$RESULT" >&2; exit $RC; }

PR_ACTION=$(printf '%s' "$RESULT" | jq -r '.pr_action // "merge"')
hive_pr_side_effects "$PR_ACTION" "$DEPT" "$TICKET_ID" "implemented" "" "$VERIFIER" "$PROJECT_ROOT"

TICKET_BOARD="$COMPANY_DIR/departments/$DEPT/tickets"
DONE_FILE="$TICKET_BOARD/done/$TICKET_ID.json"
INPROGRESS_FILE="$TICKET_BOARD/in-progress/$TICKET_ID.json"
if [ -f "$DONE_FILE" ]; then
  echo "$TICKET_ID approved by $VERIFIER — moved to done (PR merged)"
  exit 0
elif [ -f "$INPROGRESS_FILE" ]; then
  echo "$TICKET_ID approval RECORDED but PR has merge conflicts."
  echo "  Ticket was automatically bounced back to in-progress with"
  echo "  skip_tdd_red=true; the engineering IC will rebase the feature"
  echo "  branch on main, resolve conflicts, force-push, and the ticket"
  echo "  will return to in-review for re-verification."
  echo ""
  echo "  No further action required from you. Your verification stands."
  exit 0
else
  echo "WARNING: $TICKET_ID is in neither done/ nor in-progress/ after approve." >&2
  echo "  This indicates a ticket-move bug. Inspect manually." >&2
  exit 1
fi
