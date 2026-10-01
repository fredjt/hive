#!/usr/bin/env bash
set -uo pipefail

# check-company-dir.sh — verify .company/ is a real directory, not a broken
# symlink (most commonly: a self-loop, .company -> /path/to/.company itself,
# which crashes everything that opens the directory with ELOOP).
#
# Run by company-start.sh on boot AND by sprint-lifecycle.sh every iteration so
# corruption is caught the moment it happens, not 8 hours later when the
# operator notices the dashboard is empty.
#
# Exit codes:
#   0 — .company is healthy (real directory, has expected children)
#   2 — .company is corrupted (symlink loop OR symlink to non-existent path
#       OR is a regular file). Caller should refuse to operate.
#
# The check ALSO writes a remediation hint to stderr so the operator sees
# concrete recovery steps instead of a cryptic ELOOP error.
#
# Usage: check-company-dir.sh <project-root>

if [ $# -eq 0 ]; then
  echo "Usage: check-company-dir.sh <project-root>" >&2
  exit 1
fi
PROJECT_ROOT="$(cd "$1" 2>/dev/null && pwd)" || {
  echo "check-company-dir: bad project root: $1" >&2
  exit 2
}

CDIR="$PROJECT_ROOT/.company"

# Symlink? Investigate target.
if [ -L "$CDIR" ]; then
  TARGET=$(readlink "$CDIR")
  # Resolve relative targets against the symlink's directory
  case "$TARGET" in
    /*) RESOLVED="$TARGET" ;;
    *)  RESOLVED="$PROJECT_ROOT/$TARGET" ;;
  esac
  RESOLVED_REAL=$(readlink -m "$CDIR" 2>/dev/null || echo "")
  cat >&2 <<EOF

========================================================================
ERROR: .company is a SYMLINK, not a directory.
========================================================================
  $CDIR -> $TARGET

This is the recurring "agent overwrote .company with a self-referencing
symlink" crash. Recovery options, in order of preference:

1. The original directory may still exist somewhere. Check:
       ls -la $PROJECT_ROOT/.company.bak   # in case agent moved it
       ls -la /tmp/photonic-proc-company-* # any rescue copies
       find $PROJECT_ROOT -maxdepth 4 -name '.company' -type d 2>/dev/null

2. Restore from a worktree's snapshot (each worktree has its own copy):
       freshest=\$(ls -dt $PROJECT_ROOT/.worktrees/*/.company 2>/dev/null | head -1)
       rm $CDIR
       cp -a "\$freshest" $CDIR

3. Restore from git HEAD (if .company is git-tracked):
       cd $PROJECT_ROOT
       git rm --cached .company || true
       rm $CDIR
       git checkout HEAD -- .company  # only works if it was committed

4. Restore from your latest backup (e.g., tanchin sync):
       rm $CDIR
       rsync -a fredt@tanchin.org:photonic-proc/.company/ $CDIR/

After restoring, also verify the .gitignore lists '.company/' so future
git operations don't re-create the loop. And install or re-install hive
to get the PreToolUse hook that blocks this class of mutation:
       jq '.hooks.PreToolUse' ~/.claude/settings.json | grep -q block-company-writes \\
         || bash ~/.local/share/hive/packaging/install.sh --copy-to ~/.local/share/hive

========================================================================
EOF
  exit 2
fi

# Regular file (extremely weird — agent overwrote with a text file?)
if [ -f "$CDIR" ]; then
  cat >&2 <<EOF

ERROR: $CDIR is a regular FILE, not a directory.
       Move it aside: mv "$CDIR" "$CDIR.broken" and restore the directory
       from a worktree, git, or backup as above.
EOF
  exit 2
fi

# Doesn't exist at all
if [ ! -d "$CDIR" ]; then
  echo "ERROR: $CDIR does not exist. Run 'hive init' to scaffold." >&2
  exit 2
fi

# Sanity check: expected children. If they're missing the dir is hollow
# (newly init'd or partially restored). Warn but allow.
MISSING=()
for sub in departments rounds sprints logs inbox outbox; do
  [ -d "$CDIR/$sub" ] || MISSING+=("$sub")
done
if [ "${#MISSING[@]}" -gt 0 ]; then
  echo "check-company-dir: warning — $CDIR is missing expected subdirs: ${MISSING[*]}" >&2
fi

exit 0
