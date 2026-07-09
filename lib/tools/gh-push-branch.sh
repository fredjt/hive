#!/usr/bin/env bash
set -uo pipefail

# gh-push-branch.sh — push a feature branch to origin.
# No-op if no GitHub remote is configured. Tolerant: never errors out
# the caller (used as a hook in git-worktree create + IC commits).
#
# Usage: gh-push-branch.sh <branch> [project-root]

BRANCH="${1:-}"
PROJECT_ROOT="${2:-${PROJECT_ROOT:-$PWD}}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd)" || exit 0
SKILL_DIR="${HIVE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"

[ -z "$BRANCH" ] && { echo "Usage: gh-push-branch.sh <branch> [project-root]" >&2; exit 2; }

. "$SKILL_DIR/lib/tools/gh-helpers.sh"

# Soft no-op when no GitHub remote — local-only mode is supported.
gh_remote_ok || { echo "gh-push-branch: no remote configured, skipping push of $BRANCH"; exit 0; }

# Push, setting upstream on first push. -u is idempotent (re-running just
# updates the tracking ref).
if git -C "$PROJECT_ROOT" push -u origin "$BRANCH" 2>&1 | tee /tmp/gh-push-branch.log; then
  exit 0
fi
echo "gh-push-branch: push failed for $BRANCH (see /tmp/gh-push-branch.log)" >&2
exit 1
