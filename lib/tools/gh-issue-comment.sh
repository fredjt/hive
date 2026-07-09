#!/usr/bin/env bash
set -uo pipefail

# gh-issue-comment.sh — post a comment to a GitHub issue tied to a hive ticket.
# Used by ticket-move.sh (status transitions) and ticket-comment.sh (mirror
# local comments to GH).
#
# Usage:
#   gh-issue-comment.sh <issue-number> <ticket-id> <body> [project-root]
#
# No-ops cleanly when no GitHub remote is configured. Logs to
# .company/logs/gh-issue-poll.log so all GH-issue activity ends up in one
# place.

ISSUE_NUM="${1:-}"
TICKET_ID="${2:-}"
BODY="${3:-}"
PROJECT_ROOT="${4:-${PROJECT_ROOT:-$PWD}}"

[ -z "$ISSUE_NUM" ] || [ -z "$TICKET_ID" ] || [ -z "$BODY" ] && {
  echo "Usage: gh-issue-comment.sh <issue-number> <ticket-id> <body> [project-root]" >&2
  exit 2
}

PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd)" || exit 0
SKILL_DIR="${HIVE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
COMPANY_DIR="${COMPANY_DIR:-$PROJECT_ROOT/.company}"
LOG="$COMPANY_DIR/logs/gh-issue-poll.log"
mkdir -p "$(dirname "$LOG")"

. "$SKILL_DIR/lib/tools/gh-helpers.sh"
gh_preflight 2>/tmp/gh-issue-comment.preflight || {
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] gh-issue-comment skipped (no remote / not authed) for #$ISSUE_NUM ($TICKET_ID)" >> "$LOG"
  exit 0
}

cd "$PROJECT_ROOT"

# Skip if the issue is already closed; commenting on closed issues is allowed
# but adds noise we don't want for "ticket moved to done" follow-ups (those
# are handled by gh-issue-close.sh's single closing comment).
state=$(gh issue view "$ISSUE_NUM" --json state -q .state 2>/dev/null) || state=""
if [ "$state" != "OPEN" ]; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] gh-issue-comment skipped (issue #$ISSUE_NUM state=$state)" >> "$LOG"
  exit 0
fi

if gh issue comment "$ISSUE_NUM" --body "$BODY" >/dev/null 2>&1; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] gh-issue-comment ok #$ISSUE_NUM ($TICKET_ID)" >> "$LOG"
else
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] gh-issue-comment FAILED #$ISSUE_NUM ($TICKET_ID)" >> "$LOG"
  exit 1
fi
