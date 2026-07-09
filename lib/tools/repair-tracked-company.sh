#!/usr/bin/env bash
set -uo pipefail

# repair-tracked-company.sh — untrack .company / .worktrees from git history
# and break the on-disk symlink-to-self so the workspace can be recovered.
#
# Background. .company/ is in .gitignore but the symlink itself was committed
# (mode 120000) by hive's autocommit logic running `git add -A` inside a
# worktree where .company was already a symlink to the project root's
# .company. Once the symlink lands in main's tree, every `git checkout main`
# and every `git worktree add` restores it — even after the operator deletes
# the symlink, git puts it back. Same for .worktrees if it ever got staged.
#
# This script:
#   1. Detects whether .company / .worktrees are tracked at HEAD as symlinks.
#   2. Removes them from the index with `git rm --cached`.
#   3. Commits the removal with a descriptive message.
#   4. Breaks the on-disk symlink at .company (it's safe to remove —
#      the directory it pointed to either doesn't exist or is the loop
#      target, in either case there's nothing under there).
#   5. Re-runs `hive init` semantics if the operator passes --reinit, which
#      scaffolds a fresh .company/. Otherwise prints next steps.
#
# Usage: repair-tracked-company.sh <project-root> [--reinit] [--apply]
#
# By default, prints what it would do (dry-run). Pass --apply to commit.

PROJECT_ROOT="${1:-${PROJECT_ROOT:-$PWD}}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd)" || { echo "bad project root" >&2; exit 1; }
APPLY=0
REINIT=0
shift 2>/dev/null || true
for arg in "$@"; do
  case "$arg" in
    --apply)  APPLY=1 ;;
    --reinit) REINIT=1 ;;
  esac
done

cd "$PROJECT_ROOT"
[ -d .git ] || { echo "$PROJECT_ROOT is not a git repo" >&2; exit 1; }

[ "$APPLY" = 1 ] && MODE="APPLY" || MODE="DRY-RUN (pass --apply to commit)"
echo "repair-tracked-company: $MODE  ($PROJECT_ROOT)"

# Migrate .gitignore: ".company/" → ".company" (trailing-slash patterns
# only match directories, NOT symlinks — which is exactly how .company
# slipped into git in the first place).
if [ -f .gitignore ]; then
  upgraded=0
  for ent in .company .worktrees; do
    if grep -qxF "${ent}/" .gitignore && ! grep -qxF "$ent" .gitignore; then
      echo "  GITIGNORE: '${ent}/' should be '${ent}' (no trailing slash, so symlinks are also ignored)"
      if [ "$APPLY" = 1 ]; then
        sed -i "s|^${ent}/\$|${ent}|" .gitignore
        echo "    upgraded in .gitignore"
        upgraded=$((upgraded + 1))
      else
        echo "    [dry-run] would upgrade in .gitignore"
      fi
    fi
  done
  if [ "$APPLY" = 1 ] && [ "$upgraded" -gt 0 ]; then
    git add .gitignore 2>/dev/null || true
  fi
fi

REMOVED=0

for path in .company .worktrees; do
  if git ls-files --error-unmatch -- "$path" >/dev/null 2>&1; then
    mode=$(git ls-tree HEAD -- "$path" 2>/dev/null | awk '{print $1}')
    target=""
    [ "$mode" = "120000" ] && target=" (symlink → $(git show HEAD:$path 2>/dev/null))"
    echo "  TRACKED: $path  mode=$mode$target"
    if [ "$APPLY" = 1 ]; then
      git rm --cached -r "$path" >/dev/null
      echo "    git rm --cached $path"
      REMOVED=$((REMOVED + 1))
    else
      echo "    [dry-run] would: git rm --cached -r $path"
    fi
  else
    echo "  not tracked: $path"
  fi
done

# Break the on-disk symlink at .company (if it's a symlink). The dir it
# loops to is the same path, so removing the symlink doesn't lose data.
if [ -L "$PROJECT_ROOT/.company" ]; then
  echo "  ON-DISK SYMLINK: $PROJECT_ROOT/.company → $(readlink "$PROJECT_ROOT/.company")"
  if [ "$APPLY" = 1 ]; then
    rm "$PROJECT_ROOT/.company"
    echo "    rm $PROJECT_ROOT/.company"
  else
    echo "    [dry-run] would: rm $PROJECT_ROOT/.company"
  fi
fi

# Same for any worktree that has a broken .company symlink
for wt in "$PROJECT_ROOT/.worktrees"/*/; do
  [ -d "$wt" ] || continue
  wcompany="$wt.company"
  if [ -L "$wcompany" ]; then
    target=$(readlink "$wcompany")
    # Only break if it points back to a path that loops or no longer exists
    if [ "$target" = "$PROJECT_ROOT/.company" ] && [ -L "$PROJECT_ROOT/.company" ]; then
      :  # parent is also broken, will be cleaned up
    fi
  fi
done

if [ "$APPLY" = 1 ] && [ "$REMOVED" -gt 0 ]; then
  git -c user.email=hive@local -c user.name=hive \
    commit -m "Untrack .company and .worktrees (agent state, must not be committed)

The .company symlink was committed in error by hive's autocommit logic
running 'git add -A' inside a worktree. Each worktree's .company is a
symlink to the project root's .company; staging it propagates a
self-loop into main's tree, and every subsequent 'git checkout main'
restores the symlink, breaking the workspace. Remove from the index so
git stops re-creating it; .gitignore already lists both paths so they
won't be re-added unless explicit." 2>/dev/null && echo "  committed: $REMOVED path(s) removed from index"
fi

# Re-init the .company workspace if requested
if [ "$REINIT" = 1 ] && [ "$APPLY" = 1 ]; then
  if [ ! -d "$PROJECT_ROOT/.company" ]; then
    echo "  scaffolding fresh .company via 'hive init'..."
    hive init "$PROJECT_ROOT" --auto --github skip 2>&1 | sed 's/^/    /' || true
  fi
fi

echo
if [ "$APPLY" = 0 ]; then
  echo "(dry-run only — re-run with --apply to commit the changes)"
  echo "Recommended: --apply --reinit to scaffold a fresh .company in the same step."
elif [ "$REMOVED" -gt 0 ]; then
  echo "Done. Push the commit so collaborators don't re-introduce the symlink:"
  echo "  git -C $PROJECT_ROOT push origin main"
  if [ ! -d "$PROJECT_ROOT/.company" ] && [ "$REINIT" = 0 ]; then
    echo "Then scaffold a fresh .company and start hive:"
    echo "  hive init $PROJECT_ROOT --auto"
    echo "  hive start"
  fi
fi
exit 0
