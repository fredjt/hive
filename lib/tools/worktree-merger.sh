#!/usr/bin/env bash
set -uo pipefail

# worktree-merger.sh — backstop for PR opening + worktree cleanup. Refreshes
# local main from origin so newly created worktrees fork off the latest tip.
#
# Under the sprint-lifecycle + GitHub-remote architecture, this script does NOT
# merge anything itself. The verification IC merges the PR via gh-pr-merge.sh
# during its own queue entry. PR opening is normally done SYNCHRONOUSLY by
# ticket-move.sh when a ticket transitions to in-review. This daemon is a
# backstop for tickets that landed in in-review without going through
# ticket-move.sh (pre-upgrade tickets, manual file moves, crashes mid-move).
# gh-pr-open.sh is idempotent so the backstop is safe.
#
#   1. Pulls main from origin (lightweight; no-op if no remote)
#   2. For each in-review ticket WITHOUT an open PR → opens one (backstop)
#   3. For each worktree whose ticket is in done/ → removes the worktree
#      (the merge already happened upstream when verification IC merged the PR)
#
# Falls back to the old local-merge path when no GitHub remote is configured,
# so local-only setups still work.
#
# Usage: worktree-merger.sh <project-root> [--quiet] [--dry-run]

if [ $# -lt 1 ]; then
  echo "Usage: worktree-merger.sh <project-root> [--quiet] [--dry-run]" >&2
  exit 1
fi

PROJECT_ROOT="$(cd "$1" && pwd)"
shift
QUIET=false
DRY_RUN=false
while [ $# -gt 0 ]; do
  case "$1" in
    --quiet)   QUIET=true; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    *) shift ;;
  esac
done

COMPANY_DIR="$PROJECT_ROOT/.company"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="${HIVE_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"

[ -d "$COMPANY_DIR/departments" ] || exit 0
git -C "$PROJECT_ROOT" rev-parse --git-dir >/dev/null 2>&1 || exit 0

log() { [ "$QUIET" = false ] && echo "$@" >&2; return 0; }

. "$SCRIPT_DIR/gh-helpers.sh"
HAS_REMOTE=0
gh_remote_ok && HAS_REMOTE=1

# 1. Refresh local main from origin (so new worktrees fork off the latest tip).
if [ "$HAS_REMOTE" -eq 1 ] && [ "$DRY_RUN" = false ]; then
  git -C "$PROJECT_ROOT" fetch --quiet origin main 2>/dev/null || true
  # Only fast-forward — if local main has diverged, leave it alone (a VP
  # cycle made a maintenance commit and that's fine).
  current=$(git -C "$PROJECT_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null)
  if [ "$current" = "main" ]; then
    git -C "$PROJECT_ROOT" merge --ff-only origin/main 2>/dev/null || true
  fi
fi

# 2. Build sets of in-review and done ticket IDs.
#
# A ticket only counts as "done" if it ONLY exists in done/. If the same
# ticket id also appears in in-progress/, in-review/, sprint-current/,
# or backlog/, that's a corrupted state (sync race, half-finished move,
# stale copy from a previous run). Removing the worktree in that case
# nukes work that the in-progress copy implies is still active. The
# safer behavior is to skip cleanup and surface the inconsistency for
# the operator to resolve.
declare -A IN_REVIEW
declare -A DONE_TICKETS
declare -A NON_DONE_TICKETS
for dept_dir in "$COMPANY_DIR/departments"/*/; do
  rev_dir="$dept_dir/tickets/in-review"
  done_dir="$dept_dir/tickets/done"
  if [ -d "$rev_dir" ]; then
    while IFS= read -r f; do
      [ -z "$f" ] && continue
      tid=$(basename "$f" .json)
      IN_REVIEW[$tid]=1
      NON_DONE_TICKETS[$tid]=1
    done < <(find "$rev_dir" -maxdepth 1 -name '*.json' -type f 2>/dev/null)
  fi
  if [ -d "$done_dir" ]; then
    while IFS= read -r f; do
      [ -z "$f" ] && continue
      tid=$(basename "$f" .json)
      DONE_TICKETS[$tid]=1
    done < <(find "$done_dir" -maxdepth 1 -name '*.json' -type f 2>/dev/null)
  fi
  for st in in-progress sprint-current backlog; do
    sd="$dept_dir/tickets/$st"
    [ -d "$sd" ] || continue
    while IFS= read -r f; do
      [ -z "$f" ] && continue
      tid=$(basename "$f" .json)
      NON_DONE_TICKETS[$tid]=1
    done < <(find "$sd" -maxdepth 1 -name '*.json' -type f 2>/dev/null)
  done
done

# 3. Prune stale entries before iterating (ensures git's internal database
#    is clean, so the iteration below doesn't see ghost entries left by
#    prior merges/crashes).
git -C "$PROJECT_ROOT" worktree prune 2>/dev/null || true

# 4. Iterate worktrees
PR_OPENED=0
WT_REMOVED=0
WT_KEPT=0
LOCAL_MERGED=0
FAILED=0

WT_PATH=""; BRANCH=""
flush_entry() {
  [ -z "$BRANCH" ] && return 0
  case "$BRANCH" in main|master) return 0 ;; esac
  TID="${BRANCH#feature/}"

  if [ -n "${IN_REVIEW[$TID]:-}" ] && [ "$HAS_REMOTE" -eq 1 ]; then
    # Open PR if none exists yet — idempotent.
    existing=$(gh_pr_for_branch "$BRANCH")
    if [ -z "$existing" ] && [ "$DRY_RUN" = false ]; then
      log "Opening PR for $TID (in-review)"
      bash "$SCRIPT_DIR/gh-pr-open.sh" "$BRANCH" "$TID" "$PROJECT_ROOT" >/dev/null 2>&1 \
        && PR_OPENED=$((PR_OPENED + 1))
    fi
  fi

  if [ -n "${DONE_TICKETS[$TID]:-}" ]; then
    # Refuse to nuke the worktree when the same ticket also lives in
    # a non-done status — that's a corrupted state (the in-progress
    # copy implies an IC is actively working on it, while the done
    # copy is probably stale). Removing the worktree under those
    # circumstances repeatedly destroys IC work mid-spawn.
    if [ -n "${NON_DONE_TICKETS[$TID]:-}" ]; then
      log "SKIP $TID worktree cleanup — ticket exists in BOTH done/ and another status; resolve the duplicate first"
      WT_KEPT=$((WT_KEPT + 1))
      return 0
    fi
    if [ "$HAS_REMOTE" -eq 1 ]; then
      # Verification IC already merged the PR. Just clean up the worktree.
      if [ "$DRY_RUN" = true ]; then
        log "[dry-run] Would remove worktree for $TID"
      else
        log "Removing worktree for done ticket $TID"
        git -C "$PROJECT_ROOT" worktree remove "$WT_PATH" --force >/dev/null 2>&1 \
          && WT_REMOVED=$((WT_REMOVED + 1))
        git -C "$PROJECT_ROOT" branch -D "$BRANCH" >/dev/null 2>&1 || true
      fi
    else
      # Local-only fallback: do the merge ourselves.
      if [ "$DRY_RUN" = true ]; then
        log "[dry-run] Would local-merge $TID"
      else
        log "Local-merging $TID (no GitHub remote)"
        if bash "$SCRIPT_DIR/git-worktree.sh" merge "$TID" "$PROJECT_ROOT" >/dev/null 2>&1; then
          LOCAL_MERGED=$((LOCAL_MERGED + 1))
        else
          log "  FAILED: $TID (manual review needed)"
          FAILED=$((FAILED + 1))
        fi
      fi
    fi
  else
    WT_KEPT=$((WT_KEPT + 1))
  fi
}

while IFS= read -r line; do
  case "$line" in
    "worktree "*)
      flush_entry
      WT_PATH="${line#worktree }"
      BRANCH=""
      ;;
    "branch refs/heads/"*)
      BRANCH="${line#branch refs/heads/}"
      ;;
  esac
done < <(git -C "$PROJECT_ROOT" worktree list --porcelain 2>/dev/null)
flush_entry

if [ "$QUIET" = false ] && \
   [ "$((PR_OPENED + WT_REMOVED + LOCAL_MERGED + FAILED))" -gt 0 ]; then
  echo "worktree-merger: PRs opened=$PR_OPENED, worktrees removed=$WT_REMOVED, local-merged=$LOCAL_MERGED, failed=$FAILED, kept=$WT_KEPT" >&2
fi
