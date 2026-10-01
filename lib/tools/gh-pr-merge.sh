#!/usr/bin/env bash
set -uo pipefail

# gh-pr-merge.sh — merge an open PR via the GitHub API (regular merge
# commit, delete remote branch on success). Pulls main locally afterwards
# so worktrees branch off the new tip next round.
#
# **Scripted only.** Direct calls from agents are denied. Invoked by
# ticket-move.sh as a side-effect of in-review → done (reason ≠ rejected).
#
# Exit codes:
#   0  merged successfully
#   1  generic failure (gh not authed, no remote, etc.)
#   2  bad usage / required CI checks failing
#   4  not authorized — caller did not export HIVE_PR_OP_AUTHORIZED=1
#   5  PR has merge conflicts; ticket-move rolls the ticket back to
#      in-progress with skip_tdd_red=true so the engineering IC can rebase
#
# Usage: gh-pr-merge.sh <branch> [project-root] [--method merge|squash|rebase]

[ "${HIVE_PR_OP_AUTHORIZED:-}" = "1" ] || {
  echo "gh-pr-merge: PR ops are scripted via ticket-move.sh; direct calls are not allowed." >&2
  exit 4
}

BRANCH="${1:-}"
PROJECT_ROOT="${2:-${PROJECT_ROOT:-$PWD}}"
shift 2 2>/dev/null || true
[ -z "$BRANCH" ] && { echo "Usage: gh-pr-merge.sh <branch> [project-root] [--method ...]" >&2; exit 2; }

PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd)" || { echo "gh-pr-merge: bad project root" >&2; exit 2; }
SKILL_DIR="${HIVE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"

METHOD="merge"
while [ $# -gt 0 ]; do
  case "$1" in --method) METHOD="$2"; shift 2 ;; *) shift ;; esac
done

. "$SKILL_DIR/lib/tools/gh-helpers.sh"
gh_preflight 2>/tmp/gh-pr-merge.preflight || {
  cat /tmp/gh-pr-merge.preflight >&2
  exit 1   # caller (verification IC) needs to know merge didn't happen
}

cd "$PROJECT_ROOT"

PR_NUM=$(gh_pr_for_branch "$BRANCH")
[ -z "$PR_NUM" ] && { echo "gh-pr-merge: no open PR for $BRANCH" >&2; exit 1; }

# CI gating: rely on `gh pr merge` itself to enforce required-status-checks.
# The previous pre-check `gh pr checks --required` was buggy — it returned
# non-zero (exit 8 "no checks ran") on every PR for a project without
# branch protection / required checks configured, silently blocking every
# merge with a misleading "required CI checks failing" error. GitHub
# already enforces required checks at the merge API level, so the
# defensive pre-check was redundant AND wrong.

case "$METHOD" in
  merge)  flag="--merge" ;;
  squash) flag="--squash" ;;
  rebase) flag="--rebase" ;;
  *) echo "gh-pr-merge: unknown method $METHOD" >&2; exit 2 ;;
esac

# Conflict pre-check: if the PR is not in MERGEABLE state, exit 5 so
# ticket-move can roll the ticket back to in-progress with skip_tdd_red=true.
# `gh pr view --json mergeable` returns one of: MERGEABLE | CONFLICTING | UNKNOWN.
MERGEABLE=$(gh pr view "$PR_NUM" --json mergeable --jq '.mergeable' 2>/dev/null)
case "$MERGEABLE" in
  CONFLICTING)
    echo "gh-pr-merge: PR #$PR_NUM has merge conflicts; ticket needs rebase" >&2
    exit 5
    ;;
  UNKNOWN|"")
    # GitHub still computing mergeability; treat as transient — try the merge,
    # gh pr merge will surface the conflict if any.
    ;;
esac

echo "gh-pr-merge: merging PR #$PR_NUM ($BRANCH → main, method=$METHOD)"
MERGE_OUT=$(gh pr merge "$PR_NUM" "$flag" --delete-branch 2>&1)
MERGE_RC=$?
if [ $MERGE_RC -ne 0 ]; then
  # Distinguish conflict from other failures via the error text.
  if echo "$MERGE_OUT" | grep -qiE 'conflict|not mergeable|merge failed'; then
    echo "gh-pr-merge: PR #$PR_NUM merge failed due to conflicts" >&2
    echo "$MERGE_OUT" >&2
    exit 5
  fi
  echo "gh-pr-merge: gh pr merge failed for PR #$PR_NUM" >&2
  echo "$MERGE_OUT" >&2
  exit 1
fi

# (Mirror sidecar removed — GitHub issues integration removed)

# Pull main locally so subsequent worktree creates fork off the new tip.
git fetch origin main >/dev/null 2>&1 || true
git -C "$PROJECT_ROOT" checkout main >/dev/null 2>&1 || true
git -C "$PROJECT_ROOT" pull --ff-only origin main >/dev/null 2>&1 || true

echo "gh-pr-merge: PR #$PR_NUM merged; local main updated"
