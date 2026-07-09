#!/usr/bin/env bash
set -uo pipefail

# cleanup-worktrees.sh — clean up stale git worktrees that the worktree-merger
# doesn't handle. The merger only removes worktrees for tickets in done/; this
# script handles the rest:
#   - Prunable entries (gitdir points to non-existent location)
#   - Rebase helper branches (hive-check-rebase-*, ci-rebase-test-*)
#   - Nested temp worktrees (paths containing tmp. segments)
#   - Detached non-feature worktrees (no branch, or branch not matching feature/*)
#
# Skips:
#   - Main worktree (project root)
#   - Worktrees for active tickets (sprint-current, in-progress, in-review)
#   - Worktrees with commits ahead of main (preserves active work)
#
# Called by the health supervisor (company-health.sh) on a dedup'd schedule
# (default every 5 minutes). Also safe to run manually:
#   cleanup-worktrees.sh <project-root> [--dry-run]
#
# Configuration (env vars, all optional):
#   HIVE_WORKTREE_CLEANUP_PRUNE        — remove prunable entries (default: true)
#   HIVE_WORKTREE_CLEANUP_REBASE_HELPERS — remove rebase helper branches (default: true)
#   HIVE_WORKTREE_CLEANUP_NESTED_TMP   — remove nested tmp.* worktrees (default: true)
#   HIVE_WORKTREE_CLEANUP_DETACHED     — remove detached non-feature worktrees (default: true)

PROJECT_ROOT="${1:-${PROJECT_ROOT:-$PWD}}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd)" || { echo "cleanup-worktrees: bad project root" >&2; exit 2; }
git -C "$PROJECT_ROOT" rev-parse --git-dir >/dev/null 2>&1 || { echo "cleanup-worktrees: not a git repo" >&2; exit 0; }

DRY_RUN=false
[ "${2:-}" = "--dry-run" ] && DRY_RUN=true

# Configuration
CLEANUP_PRUNE="${HIVE_WORKTREE_CLEANUP_PRUNE:-true}"
CLEANUP_REBASE="${HIVE_WORKTREE_CLEANUP_REBASE_HELPERS:-true}"
CLEANUP_TMP="${HIVE_WORKTREE_CLEANUP_NESTED_TMP:-true}"
CLEANUP_DETACHED="${HIVE_WORKTREE_CLEANUP_DETACHED:-true}"

# ── Helpers ───────────────────────────────────────────────────────────────────

# Check if a branch name is a rebase helper we should clean up.
is_rebase_helper() {
  local branch="$1"
  case "$branch" in
    hive-check-rebase-*|ci-rebase-test-*) return 0 ;;
  esac
  return 1
}

# Check if a worktree path is a nested temp directory.
is_nested_tmp() {
  local path="$1"
  case "$path" in
    */tmp.*) return 0 ;;
  esac
  return 1
}

# Check if a worktree is detached (no branch) or has a non-feature branch.
is_detached_non_feature() {
  local branch="$1"
  # No branch at all → detached
  [ -z "$branch" ] && return 0
  # Main branch → skip (valid)
  case "$branch" in main|master) return 1 ;; esac
  # Feature branch → skip (valid, handled by worktree-merger)
  case "$branch" in feature/*) return 1 ;; esac
  # Everything else → detached non-feature
  return 0
}

# ── Resolve default branch ────────────────────────────────────────────────────

resolve_default_branch() {
  for cand in main master; do
    git -C "$PROJECT_ROOT" rev-parse --verify "$cand" >/dev/null 2>&1 && echo "$cand" && return 0
  done
  # Fallback: try remote HEAD
  local head
  head=$(git -C "$PROJECT_ROOT" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null || true)
  if [ -n "$head" ]; then
    echo "${head#origin/}"
    return 0
  fi
  echo ""
  return 1
}

DEFAULT_BRANCH=$(resolve_default_branch)

# ── Build set of active ticket IDs ────────────────────────────────────────────

COMPANY_DIR="${COMPANY_DIR:-$PROJECT_ROOT/.company}"
declare -A ACTIVE_TICKETS
for status in sprint-current in-progress in-review; do
  for dept_dir in "$COMPANY_DIR"/departments/*/tickets/"$status"/*.json; do
    [ -f "$dept_dir" ] || continue
    tid=$(basename "$dept_dir" .json)
    ACTIVE_TICKETS[$tid]=1
  done
done

# ── Parse worktrees and classify ──────────────────────────────────────────────

WT_PATH=""
BRANCH=""
IS_PRUNABLE=false
PRUNED=0
REBASE_HELPERS=0
NESTED_TMP=0
DETACHED_NF=0
SKIPPED_MAIN=0
SKIPPED_ACTIVE=0
SKIPPED_AHEAD=0
SKIPPED_OTHER=0

flush_entry() {
  # Empty BRANCH means detached worktree — still needs processing
  # (but no branch-dependent cleanup can apply)
  [ -z "$WT_PATH" ] && return 0

  # Skip main worktree
  case "$BRANCH" in main|master)
    # Also verify it's the project root
    if [ "$WT_PATH" = "$PROJECT_ROOT" ]; then
      SKIPPED_MAIN=$((SKIPPED_MAIN + 1))
    fi
    return 0
  ;; esac

  # Skip active ticket worktrees (feature/ID where ID is active)
  local tid=""
  case "$BRANCH" in
    feature/*) tid="${BRANCH#feature/}" ;;
  esac
  if [ -n "$tid" ] && [ -n "${ACTIVE_TICKETS[$tid]:-}" ]; then
    SKIPPED_ACTIVE=$((SKIPPED_ACTIVE + 1))
    return 0
  fi

  # Handle prunable entries
  if [ "$IS_PRUNABLE" = true ]; then
    if [ "$CLEANUP_PRUNE" = true ]; then
      if [ "$DRY_RUN" = true ]; then
        echo "[dry-run] Would remove prunable worktree at $WT_PATH (branch=$BRANCH)"
      else
        echo "Removing prunable worktree at $WT_PATH"
        git -C "$PROJECT_ROOT" worktree remove "$WT_PATH" --force >/dev/null 2>&1 || true
        # Clean up the branch if it still exists
        if [ -n "$BRANCH" ]; then
          git -C "$PROJECT_ROOT" branch -D "$BRANCH" >/dev/null 2>&1 || true
        fi
      fi
      PRUNED=$((PRUNED + 1))
    fi
    return 0
  fi

  # Check for rebase helper branches
  if [ -n "$BRANCH" ] && [ "$CLEANUP_REBASE" = true ] && is_rebase_helper "$BRANCH"; then
    if [ "$DRY_RUN" = true ]; then
      echo "[dry-run] Would remove rebase helper worktree at $WT_PATH ($BRANCH)"
    else
      echo "Removing rebase helper worktree at $WT_PATH ($BRANCH)"
      git -C "$PROJECT_ROOT" worktree remove "$WT_PATH" --force >/dev/null 2>&1 || true
      git -C "$PROJECT_ROOT" branch -D "$BRANCH" >/dev/null 2>&1 || true
    fi
    REBASE_HELPERS=$((REBASE_HELPERS + 1))
    return 0
  fi

  # Check for nested temp worktrees
  if [ "$CLEANUP_TMP" = true ] && is_nested_tmp "$WT_PATH"; then
    if [ "$DRY_RUN" = true ]; then
      echo "[dry-run] Would remove nested temp worktree at $WT_PATH"
    else
      echo "Removing nested temp worktree at $WT_PATH"
      git -C "$PROJECT_ROOT" worktree remove "$WT_PATH" --force >/dev/null 2>&1 || true
      if [ -n "$BRANCH" ]; then
        git -C "$PROJECT_ROOT" branch -D "$BRANCH" >/dev/null 2>&1 || true
      fi
    fi
    NESTED_TMP=$((NESTED_TMP + 1))
    return 0
  fi

  # Check for detached non-feature worktrees
  if [ "$CLEANUP_DETACHED" = true ] && is_detached_non_feature "$BRANCH"; then
    if [ "$DRY_RUN" = true ]; then
      echo "[dry-run] Would remove detached non-feature worktree at $WT_PATH (branch=${BRANCH:-<detached>})"
    else
      echo "Removing detached non-feature worktree at $WT_PATH (branch=${BRANCH:-<detached>})"
      git -C "$PROJECT_ROOT" worktree remove "$WT_PATH" --force >/dev/null 2>&1 || true
      if [ -n "$BRANCH" ]; then
        git -C "$PROJECT_ROOT" branch -D "$BRANCH" >/dev/null 2>&1 || true
      fi
    fi
    DETACHED_NF=$((DETACHED_NF + 1))
    return 0
  fi

  # Check if worktree has commits ahead of main — preserve active work
  if [ -n "$DEFAULT_BRANCH" ] && [ -n "$BRANCH" ]; then
    local ahead
    ahead=$(git -C "$PROJECT_ROOT" log --oneline "${DEFAULT_BRANCH}..${BRANCH}" 2>/dev/null || true)
    if [ -n "$ahead" ]; then
      SKIPPED_AHEAD=$((SKIPPED_AHEAD + 1))
      return 0
    fi
  fi

  # Everything else — leave alone (may be intentional)
  SKIPPED_OTHER=$((SKIPPED_OTHER + 1))
}

while IFS= read -r line; do
  case "$line" in
    "worktree "*)
      flush_entry
      WT_PATH="${line#worktree }"
      BRANCH=""
      IS_PRUNABLE=false
      ;;
    "branch refs/heads/"*)
      BRANCH="${line#branch refs/heads/}"
      ;;
    "prunable"*)
      IS_PRUNABLE=true
      ;;
  esac
done < <(git -C "$PROJECT_ROOT" worktree list --porcelain 2>/dev/null)
flush_entry

# Prune any remaining stale entries that weren't caught above.
if [ "$CLEANUP_PRUNE" = true ]; then
  git -C "$PROJECT_ROOT" worktree prune 2>/dev/null || true
fi

TOTAL=$((PRUNED + REBASE_HELPERS + NESTED_TMP + DETACHED_NF))
if [ "$TOTAL" -gt 0 ] || [ "$DRY_RUN" = true ]; then
  echo "cleanup-worktrees: removed $TOTAL worktree(s) " \
    "(pruned=$PRUNED, rebase-helpers=$REBASE_HELPERS, nested-tmp=$NESTED_TMP, detached-nf=$DETACHED_NF)" \
    "(skipped: main=$SKIPPED_MAIN, active=$SKIPPED_ACTIVE, ahead=$SKIPPED_AHEAD, other=$SKIPPED_OTHER)"
fi
