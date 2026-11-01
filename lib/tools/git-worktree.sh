#!/bin/bash
# Git worktree manager for company agents
# Usage:
#   git-worktree.sh create <ticket-id> <project-root>   - Create a worktree + branch for a ticket
#   git-worktree.sh merge  <ticket-id> <project-root>   - Merge branch to main, delete worktree
#   git-worktree.sh list   <project-root>                - List active worktrees
#   git-worktree.sh cleanup <project-root>               - Remove stale worktrees
#
# Worktrees are created at <project-root>/.worktrees/<ticket-id>/

ACTION="$1"
TICKET_ID="$2"
PROJECT_ROOT="$3"

if [ -z "$ACTION" ]; then
  echo "Usage: git-worktree.sh <create|merge|list|cleanup> [ticket-id] <project-root>" >&2
  exit 1
fi

# For list/cleanup, ticket-id is actually project-root
if [ "$ACTION" = "list" ] || [ "$ACTION" = "cleanup" ]; then
  PROJECT_ROOT="$2"
fi

if [ -z "$PROJECT_ROOT" ]; then
  echo "Error: project-root required" >&2
  exit 1
fi

WORKTREE_DIR="$PROJECT_ROOT/.worktrees"

case "$ACTION" in
  create)
    if [ -z "$TICKET_ID" ]; then
      echo "Error: ticket-id required for create" >&2
      exit 1
    fi

    BRANCH="feature/$TICKET_ID"
    WT_PATH="$WORKTREE_DIR/$TICKET_ID"

    # Ensure we're in a git repo
    if ! git -C "$PROJECT_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
      echo "Initializing git repo at $PROJECT_ROOT"
      git -C "$PROJECT_ROOT" init
      # Exclude agent-state dirs from the initial commit. Without this,
      # the symlinked .company in each worktree gets staged + committed,
      # propagating a self-loop into every clone.
      git -C "$PROJECT_ROOT" add -A -- ':!.company' ':!.company/**' ':!.worktrees' ':!.worktrees/**'
      git -C "$PROJECT_ROOT" commit -m "Initial commit" 2>/dev/null || true
    fi

    # Ensure main branch exists and we're on it
    git -C "$PROJECT_ROOT" checkout main 2>/dev/null || git -C "$PROJECT_ROOT" checkout -b main 2>/dev/null || true

    # Create worktree directory
    mkdir -p "$WORKTREE_DIR"

    # IDEMPOTENT + AUTHORITATIVE: if the worktree already exists,
    # just return it. Uses `git worktree list --porcelain` as the
    # authoritative source (git's internal database) rather than
    # checking directory existence, which can be stale after a
    # worktree-merger removes the dir but leaves git's entry intact.
    #
    # Pass `create --force <ticket>` if you actually want the destructive
    # rebuild (e.g., manual recovery from a corrupted worktree).
    FORCE_REBUILD=0
    for arg in "$@"; do
      [ "$arg" = "--force" ] && FORCE_REBUILD=1
    done

    # ── Authoritative pre-flight: is the branch already a git worktree? ──
    # Porcelain groups lines per worktree:
    #   worktree /path
    #   HEAD <sha>
    #   branch refs/heads/<branch>
    # (blank line between entries). The old implementation piped the
    # output through `sed -n 's/^worktree //p'`, which strips exactly the
    # `branch` lines it then tried to `read` — so detection always missed
    # and every create over an existing worktree fell through to the
    # force-rebuild cleanup: worktree remove --force + branch -D + fresh
    # branch off main. In-progress commits on the ticket's branch were
    # destroyed every time the lifecycle re-ran ensure_worktree (bugfix
    # cycle, daemon restart, second pass).
    ALREADY_WORKTREE=""   # path where git says the worktree lives
    ALREADY_WORKTREE=$(git -C "$PROJECT_ROOT" worktree list --porcelain 2>/dev/null | \
      awk -v want="refs/heads/$BRANCH" '
        /^worktree /{wt=substr($0,10)}  # path starts after "worktree " (may contain spaces)
        /^branch / && $2 == want {print wt; exit}
      ')

    if [ "$FORCE_REBUILD" = 0 ] && [ -n "$ALREADY_WORKTREE" ]; then
      if [ "$ALREADY_WORKTREE" = "$WT_PATH" ]; then
        # ── Case 1: branch is our worktree at the expected path ──
        # Re-link .company if missing (symlink may have been pruned).
        if [ ! -e "$WT_PATH/.company" ]; then
          ln -s "$PROJECT_ROOT/.company" "$WT_PATH/.company" 2>/dev/null || true
        fi
        echo "WORKTREE_PATH=$WT_PATH"
        echo "BRANCH=$BRANCH"
        echo "Reusing existing worktree for $TICKET_ID at $WT_PATH on branch $BRANCH"
        exit 0
      else
        # ── Case 2: branch is a worktree at a DIFFERENT path ──
        # Stale entry — clean it up so we can create at the right path.
        echo "Cleaning up stale worktree for $BRANCH at $ALREADY_WORKTREE" >&2
        git -C "$PROJECT_ROOT" worktree remove "$ALREADY_WORKTREE" --force 2>/dev/null || true
        git -C "$PROJECT_ROOT" branch -D "$BRANCH" 2>/dev/null || true
      fi
    fi

    # ── Force-rebuild / inconsistent state cleanup ──
    if [ -d "$WT_PATH" ]; then
      git -C "$PROJECT_ROOT" worktree remove "$WT_PATH" --force 2>/dev/null
      rm -rf "$WT_PATH" 2>/dev/null
    fi
    # Delete the branch if it exists (may be a bare branch from a prior run).
    git -C "$PROJECT_ROOT" branch -D "$BRANCH" 2>/dev/null || true

    # Prune any stale worktree entries (e.g. from worktree-merger that
    # removed the dir but left git's internal registry entry intact).
    git -C "$PROJECT_ROOT" worktree prune 2>/dev/null || true

    # Create worktree with new branch off the resolved default branch
    DEFAULT_BRANCH=""
    for cand in main master; do
      git -C "$PROJECT_ROOT" rev-parse --verify "$cand" >/dev/null 2>&1 && DEFAULT_BRANCH="$cand" && break
    done
    [ -z "$DEFAULT_BRANCH" ] && DEFAULT_BRANCH=main

    # Attempt creation; on failure, retry once with --force (handles a
    # race where another process created the worktree between our
    # pre-flight check and this call).
    CREATE_RC=0
    git -C "$PROJECT_ROOT" worktree add "$WT_PATH" -b "$BRANCH" "$DEFAULT_BRANCH" 2>&1 || CREATE_RC=$?
    if [ "$CREATE_RC" -ne 0 ]; then
      git -C "$PROJECT_ROOT" worktree prune 2>/dev/null || true
      git -C "$PROJECT_ROOT" worktree add "$WT_PATH" -b "$BRANCH" "$DEFAULT_BRANCH" --force 2>&1 || CREATE_RC=$?
    fi
    if [ "$CREATE_RC" -ne 0 ]; then
      echo "ERROR: Failed to create worktree for $TICKET_ID (exit=$CREATE_RC)" >&2
      exit 1
    fi

    # Copy .company directory as symlink so agent can access it
    if [ ! -e "$WT_PATH/.company" ]; then
      ln -s "$PROJECT_ROOT/.company" "$WT_PATH/.company" 2>/dev/null || true
    fi

    # Push the new branch to origin (no-op if no GitHub remote configured).
    # Establishes upstream so subsequent IC commits can be pushed without
    # extra setup.
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [ -x "$SCRIPT_DIR/gh-push-branch.sh" ]; then
      bash "$SCRIPT_DIR/gh-push-branch.sh" "$BRANCH" "$PROJECT_ROOT" >/dev/null 2>&1 || true
    fi

    echo "WORKTREE_PATH=$WT_PATH"
    echo "BRANCH=$BRANCH"
    echo "Created worktree for $TICKET_ID at $WT_PATH on branch $BRANCH"
    ;;

  merge)
    if [ -z "$TICKET_ID" ]; then
      echo "Error: ticket-id required for merge" >&2
      exit 1
    fi

    BRANCH="feature/$TICKET_ID"
    WT_PATH="$WORKTREE_DIR/$TICKET_ID"

    if [ ! -d "$WT_PATH" ]; then
      echo "Error: worktree $WT_PATH does not exist" >&2
      exit 1
    fi

    # Commit any uncommitted changes in the worktree.
    # Exclude .company and .worktrees — they're agent state. Committing
    # the .company symlink propagates a self-loop into every clone of
    # main and breaks future git checkouts. (This is what created the
    # FINANCE-0003 commit on photonic-proc that introduced .company as a
    # symlink in main's tree.)
    if git -C "$WT_PATH" status --porcelain | grep -q .; then
      git -C "$WT_PATH" add -A -- ':!.company' ':!.company/**' ':!.worktrees' ':!.worktrees/**'
      git -C "$WT_PATH" commit -m "$TICKET_ID: Final changes before merge" 2>/dev/null || true
    fi

    # Switch main repo to main and merge
    git -C "$PROJECT_ROOT" checkout main 2>/dev/null

    # Merge the feature branch with --no-verify to skip hooks
    # This prevents post-commit hooks (like changelog generation) from
    # modifying files during merge, which would create a conflict loop
    if git -C "$PROJECT_ROOT" merge "$BRANCH" --no-edit --no-verify -m "Merge $BRANCH: $TICKET_ID complete"; then
      echo "Merged $BRANCH into main successfully"
    else
      # If merge conflicts, try to auto-resolve
      echo "Merge conflict detected, attempting auto-resolve..."
      git -C "$PROJECT_ROOT" merge --abort 2>/dev/null
      # Try with theirs strategy (accept the feature branch changes)
      if git -C "$PROJECT_ROOT" merge "$BRANCH" --no-edit --no-verify -X theirs -m "Merge $BRANCH: $TICKET_ID complete (auto-resolved)"; then
        echo "Merged $BRANCH with auto-resolution"
      else
        echo "Error: Could not merge $BRANCH. Manual resolution required." >&2
        git -C "$PROJECT_ROOT" merge --abort 2>/dev/null
        exit 1
      fi
    fi

    # Manually run the changelog generation after merge is complete
    # This ensures it only runs once, after all merge operations are done
    CHANGELOG_SCRIPT="$PROJECT_ROOT/.company/marketing/scripts/generate-changelog.sh"
    if [ -f "$CHANGELOG_SCRIPT" ]; then
      echo "🔄 Generating updated changelog..."
      bash "$CHANGELOG_SCRIPT" > /dev/null 2>&1
      # If changelog was updated, add it to the merge commit
      if git -C "$PROJECT_ROOT" status --porcelain | grep -q "CHANGELOG.md"; then
        git -C "$PROJECT_ROOT" add .company/marketing/output/CHANGELOG.md
        git -C "$PROJECT_ROOT" commit --amend --no-edit --no-verify
        echo "✅ Changelog updated and included in merge commit"
      fi
    fi

    # Remove worktree and branch
    git -C "$PROJECT_ROOT" worktree remove "$WT_PATH" --force 2>/dev/null
    git -C "$PROJECT_ROOT" branch -d "$BRANCH" 2>/dev/null

    echo "Merged and cleaned up $TICKET_ID"
    ;;

  list)
    if ! git -C "$PROJECT_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
      echo "Not a git repo: $PROJECT_ROOT" >&2
      exit 1
    fi
    git -C "$PROJECT_ROOT" worktree list
    ;;

  cleanup)
    if ! git -C "$PROJECT_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
      echo "Not a git repo: $PROJECT_ROOT" >&2
      exit 1
    fi
    # Prune dead worktrees
    git -C "$PROJECT_ROOT" worktree prune
    # Remove empty worktree directories
    if [ -d "$WORKTREE_DIR" ]; then
      find "$WORKTREE_DIR" -maxdepth 1 -type d -empty -delete 2>/dev/null
    fi
    echo "Cleaned up stale worktrees"
    git -C "$PROJECT_ROOT" worktree list
    ;;

  *)
    echo "Unknown action: $ACTION" >&2
    echo "Usage: git-worktree.sh <create|merge|list|cleanup> [ticket-id] <project-root>" >&2
    exit 1
    ;;
esac
