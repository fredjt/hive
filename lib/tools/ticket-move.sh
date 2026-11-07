#!/bin/bash
# Usage: ticket-move.sh <department> <ticket-id> <new-status> [completion-comment] [author] [reason]
# Example: ticket-move.sh backend-api-services TICK-API-0015 done "Implemented rate limiting middleware" "IC-Engineer" implemented
#
# When moving to done, a completion comment (arg 4) and reason (arg 6) are REQUIRED.
# Valid reasons: implemented, rejected, done
#   implemented - ticket was built and delivered
#   rejected    - ticket was reviewed and rejected (won't do)
#   done        - ticket was completed through non-code means (e.g. config change, manual fix)
# The script auto-detects the git branch and latest commit for the ticket.
# The ticket file is NEVER deleted - only moved.
#
# G1/G2: the state mutation (find, role/gate policy, field updates, atomic
# move, duplicate cleanup) lives in the state layer (lib/hive_state, op
# ticket-move) — the SAME code path the MCP ticket_move tool and the
# sprint lifecycle use. This wrapper keeps the CLI contract and the
# process edge: git detection + gh PR side-effects (pr-side-effects.sh).

COMPANY_DIR="${COMPANY_DIR:-.company}"
export COMPANY_DIR
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="${SKILL_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
source "$SCRIPT_DIR/hive-state.sh"
source "$SCRIPT_DIR/pr-side-effects.sh"

DEPT="$1"
TICKET_ID="$2"
NEW_STATUS="$3"
COMMENT="$4"
AUTHOR="${5:-system}"
REASON="$6"

if [ -z "$DEPT" ] || [ -z "$TICKET_ID" ] || [ -z "$NEW_STATUS" ]; then
  echo "Usage: ticket-move.sh <department> <ticket-id> <new-status> [comment] [author] [reason]" >&2
  echo "Statuses: backlog, sprint-current, in-progress, in-review, done" >&2
  echo "When moving to done: comment, author, and reason are required." >&2
  echo "Valid reasons: implemented, rejected, done" >&2
  exit 1
fi

PROJECT_ROOT=$(cd "$COMPANY_DIR/.." 2>/dev/null && pwd)

# ── Git detection (process edge; stays in bash) ──
GIT_BRANCH=""
GIT_COMMIT=""
GIT_WORKTREE=""

if [ -n "$PROJECT_ROOT" ] && git -C "$PROJECT_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  GIT_BRANCH=$(git -C "$PROJECT_ROOT" branch --list "feature/$TICKET_ID" 2>/dev/null | tr -d '* ')
  if [ -d "$PROJECT_ROOT/.worktrees/$TICKET_ID" ]; then
    GIT_WORKTREE="$PROJECT_ROOT/.worktrees/$TICKET_ID"
    GIT_COMMIT=$(git -C "$GIT_WORKTREE" log --oneline -1 --format="%H" 2>/dev/null)
    GIT_BRANCH="feature/$TICKET_ID"
  fi
  if [ -z "$GIT_COMMIT" ]; then
    GIT_COMMIT=$(git -C "$PROJECT_ROOT" log --all --oneline -1 --format="%H" --grep="$TICKET_ID" 2>/dev/null)
  fi
fi
GIT_COMMIT_SHORT=""
[ -n "$GIT_COMMIT" ] && GIT_COMMIT_SHORT=$(echo "$GIT_COMMIT" | cut -c1-8)

# ── The state mutation (one shared code path) ──
# Role/gate policy denials (exit 3) and errors are emitted by the state
# layer on stderr — they redirect confused agents to ticket_approve /
# ticket_bounce exactly as before.
MOVE_ARGS=(ticket-move --dept "$DEPT" --ticket "$TICKET_ID" --status "$NEW_STATUS" \
  --comment "$COMMENT" --author "$AUTHOR" \
  --git-branch "$GIT_BRANCH" --git-commit "$GIT_COMMIT_SHORT" \
  --git-worktree "$GIT_WORKTREE" --project-root "$PROJECT_ROOT")
[ -n "$REASON" ] && MOVE_ARGS+=(--reason "$REASON")
RESULT=$(hs "${MOVE_ARGS[@]}" 2>&1)
RC=$?
if [ $RC -ne 0 ]; then
  # separate stderr (denial messages) from the JSON result
  printf '%s\n' "$RESULT" | grep -v '^{' | head -20 >&2
  printf '%s\n' "$RESULT" | grep -A1000 '^{' | head -30 >&2
  exit $RC
fi

NOOP=$(printf '%s' "$RESULT" | jq -r '.noop // false')
FROM_STATUS=$(printf '%s' "$RESULT" | jq -r '.from_status // empty')
PR_ACTION=$(printf '%s' "$RESULT" | jq -r '.pr_action // "none"')

if [ "$NOOP" = "true" ]; then
  echo "Ticket $TICKET_ID is already in $NEW_STATUS"
  exit 0
fi

# ── PR side-effects (process edge; shared with ticket-approve.sh) ──
hive_pr_side_effects "$PR_ACTION" "$DEPT" "$TICKET_ID" "$REASON" "$COMMENT" "$AUTHOR" "$PROJECT_ROOT"

# ── Outcome message ──
case "$NEW_STATUS" in
  backlog)
    echo "Moved $TICKET_ID: $FROM_STATUS -> backlog (deferred)"
    ;;
  done)
    if [ -f "$COMPANY_DIR/departments/$DEPT/tickets/in-progress/$TICKET_ID.json" ]; then
      : # merge conflict: "Rolled back ..." was printed by the side-effects
    else
      echo "Moved $TICKET_ID: $FROM_STATUS -> done (branch: ${GIT_BRANCH:-none}, commit: ${GIT_COMMIT_SHORT:-none})"
    fi
    ;;
  *)
    echo "Moved $TICKET_ID: $FROM_STATUS -> $NEW_STATUS"
    ;;
esac
exit 0
