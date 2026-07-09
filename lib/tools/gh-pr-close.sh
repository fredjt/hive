#!/usr/bin/env bash
set -uo pipefail

# gh-pr-close.sh — close an open PR without merging (the rejection path).
# Posts a rejection comment, closes the PR, leaves the branch alone.
#
# **Scripted only.** Direct calls from agents are denied. Invoked by
# ticket-move.sh as a side-effect of any → done with reason=rejected.
#
# Renames the old gh-pr-reject.sh; same shape, but now gated and called
# only from ticket-move.sh.
#
# Exit codes:
#   0  PR closed (or already closed; or no remote → no-op)
#   1  generic failure
#   2  bad usage
#   4  not authorized — caller did not export HIVE_PR_OP_AUTHORIZED=1
#
# Usage: gh-pr-close.sh <branch> <ticket-id> [project-root] --reason "<rationale>"

[ "${HIVE_PR_OP_AUTHORIZED:-}" = "1" ] || {
  echo "gh-pr-close: PR ops are scripted via ticket-move.sh; direct calls are not allowed." >&2
  exit 4
}

BRANCH="${1:-}"
TICKET_ID="${2:-}"
PROJECT_ROOT="${3:-${PROJECT_ROOT:-$PWD}}"
shift 3 2>/dev/null || true

[ -z "$BRANCH" ] || [ -z "$TICKET_ID" ] && {
  echo "Usage: gh-pr-close.sh <branch> <ticket-id> [project-root] --reason '<rationale>'" >&2
  exit 2
}

PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd)" || { echo "gh-pr-close: bad project root" >&2; exit 2; }
SKILL_DIR="${HIVE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"

REASON=""
while [ $# -gt 0 ]; do
  case "$1" in
    --reason) REASON="$2"; shift 2 ;;
    *) shift ;;
  esac
done

[ -z "$REASON" ] && { echo "gh-pr-close: --reason is required" >&2; exit 2; }

. "$SKILL_DIR/lib/tools/gh-helpers.sh"
gh_preflight 2>/tmp/gh-pr-close.preflight || {
  cat /tmp/gh-pr-close.preflight >&2
  exit 0   # local-only mode: ticket-move proceeds with the rejection regardless
}

cd "$PROJECT_ROOT"
PR_NUM=$(gh_pr_for_branch "$BRANCH")
[ -z "$PR_NUM" ] && { echo "gh-pr-close: no open PR for $BRANCH (already closed?)"; exit 0; }

COMMENT="❌ **Ticket rejected** for \`$TICKET_ID\`:

$REASON

Closing this PR without merging. The branch is preserved for reference."

echo "gh-pr-close: commenting on PR #$PR_NUM"
gh pr comment "$PR_NUM" --body "$COMMENT" 2>&1 || \
  echo "gh-pr-close: comment failed (continuing)" >&2

echo "gh-pr-close: closing PR #$PR_NUM (branch kept)"
gh pr close "$PR_NUM" 2>&1 || {
  echo "gh-pr-close: close failed" >&2
  exit 1
}
