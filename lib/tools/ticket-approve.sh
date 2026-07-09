#!/usr/bin/env bash
set -euo pipefail

# ticket-approve.sh - Verification IC's explicit approval of a ticket.
#
# ATOMIC: stamps verified_by + verified_at + verification_rationale, AND
# moves the ticket to done with reason=implemented. The verifier's intent
# is unambiguous — there's no reason to require a separate ticket-move call.
#
# The done move triggers ticket-move.sh's PR-merge side-effect (gh-pr-merge,
# Closes #N → linked GH issue auto-close, etc.), so a single approve call
# fully ships the ticket.
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
source "$SCRIPT_DIR/ticket-paths.sh"
TICKET_BOARD="$(ticket_dir "$DEPT")"

# Reject lazy approvals
if [ "${#RATIONALE}" -lt 30 ]; then
  echo "ERROR: rationale too short (${#RATIONALE} chars, need >=30)." >&2
  echo "Rubber-stamp approvals are not allowed. Describe what you actually checked." >&2
  exit 1
fi

# Find the ticket - must be in in-review
TICKET_FILE="$TICKET_BOARD/in-review/$TICKET_ID.json"
if [ ! -f "$TICKET_FILE" ]; then
  echo "ERROR: ticket $TICKET_ID is not in in-review for $DEPT (cannot approve)." >&2
  exit 1
fi

# Require gates to have passed first
GATES_OK=$(jq -r '.gates_passed // false' "$TICKET_FILE" 2>/dev/null)
if [ "$GATES_OK" != "true" ]; then
  echo "ERROR: deterministic gates have not passed for $TICKET_ID." >&2
  echo "Run: bash $SCRIPT_DIR/verification-gates.sh $DEPT \$PROJECT_ROOT" >&2
  echo "If gates fail, the ticket will be bounced back to in-progress for the IC to fix." >&2
  exit 1
fi

# Set approval fields
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
jq \
  --arg by "$VERIFIER" \
  --arg at "$TS" \
  --arg rationale "$RATIONALE" \
  '.verified_by = $by | .verified_at = $at | .verification_rationale = $rationale' \
  "$TICKET_FILE" > "$TICKET_FILE.tmp" && mv "$TICKET_FILE.tmp" "$TICKET_FILE"

# Add a visible comment
bash "$SCRIPT_DIR/ticket-comment.sh" "$DEPT" "$TICKET_ID" "$VERIFIER" \
  "✅ APPROVED. Rationale: $RATIONALE" 2>/dev/null || true

# Move the ticket to done in one atomic step. ticket-move.sh's PR-merge
# side-effect runs from here. On gh-pr-merge exit-5 (conflict),
# ticket-move catches it, rolls the ticket back to in-progress with
# skip_tdd_red=true, and exits 0 so the verifier's turn doesn't error.
SHORT_RATIONALE=$(echo "$RATIONALE" | head -c 200 | tr '\n' ' ')
HIVE_SCRIPTED_MOVE=1 HIVE_ROLE=Verification-IC HIVE_AGENT="$VERIFIER" \
  bash "$SCRIPT_DIR/ticket-move.sh" "$DEPT" "$TICKET_ID" done \
    "Verified by $VERIFIER: $SHORT_RATIONALE" "$VERIFIER" implemented

# Detect whether the move actually landed the ticket in done/ or whether
# the conflict rollback fired and bounced it to in-progress. Read the
# ticket file from the destination first; if it's not there, look in
# in-progress (the rollback target).
DONE_FILE="$TICKET_BOARD/done/$TICKET_ID.json"
INPROGRESS_FILE="$TICKET_BOARD/in-progress/$TICKET_ID.json"
if [ -f "$DONE_FILE" ]; then
  echo "$TICKET_ID approved by $VERIFIER at $TS — moved to done (PR merged)"
  exit 0
elif [ -f "$INPROGRESS_FILE" ]; then
  # Conflict rollback fired. The verifier's APPROVE was correct but the
  # PR couldn't be merged cleanly. The ticket is back in in-progress
  # with skip_tdd_red=true so the engineering IC will rebase next loop.
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
