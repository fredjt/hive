#!/usr/bin/env bash
set -euo pipefail

# init.sh — workspace initialization.
#
# Usage: init.sh <project-root> [--brief "..." | --brief-file FILE] [--auto] [--name NAME]
#                                [--github private|public|skip|<url>]
#
# Creates the .company/ workspace, initial manifest, brief, chat channels,
# AND a git repo with a GitHub remote. Without the git+
# GitHub setup, verification IC approvals crash on PR merge (gh-pr-merge.sh
# fails because there's no remote).
#
# --github modes:
#   private (default)   create a private GitHub repo via `gh repo create`
#   public              create a public GitHub repo
#   skip                no GitHub setup at all (PR-merge approvals will fail)
#   git@github.com:…    set this URL as origin (no creation)
#
# Idempotent: if .company/ already exists, only fills in missing pieces.

if [ $# -lt 1 ]; then
  echo "Usage: init.sh <project-root> [--brief \"...\" | --brief-file FILE] [--auto] [--name NAME] [--github private|public|skip|<url>]" >&2
  exit 1
fi

PROJECT_ROOT="$(cd "$1" && pwd)"
shift

BRIEF=""
BRIEF_FILE=""
AUTO=false
NAME=""
GITHUB="private"   # private (default) | public | skip | <git-url>

while [ $# -gt 0 ]; do
  case "$1" in
    --brief)      BRIEF="$2"; shift 2 ;;
    --brief-file) BRIEF_FILE="$2"; shift 2 ;;
    --auto)       AUTO=true; shift ;;
    --name)       NAME="$2"; shift 2 ;;
    --github)     GITHUB="$2"; shift 2 ;;
    *)            echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [ -n "$BRIEF_FILE" ]; then
  [ -f "$BRIEF_FILE" ] || { echo "Brief file not found: $BRIEF_FILE" >&2; exit 1; }
  BRIEF="$(cat "$BRIEF_FILE")"
fi

[ -z "$NAME" ] && NAME="$(basename "$PROJECT_ROOT")"

HIVE_ROOT="${HIVE_ROOT:-$HOME/.local/share/hive}"
TOOLS="$HIVE_ROOT/lib/tools"
export COMPANY_DIR="$PROJECT_ROOT/.company"
export SKILL_DIR="$HIVE_ROOT"

echo "Initializing hive workspace at $PROJECT_ROOT" >&2

# 1. Workspace + .gitignore
bash "$TOOLS/init-workspace.sh" "$PROJECT_ROOT"

# 2. Manifest
bash "$TOOLS/init-manifest.sh" "$NAME" "$AUTO" --mode build >/dev/null

# 3. Brief
if [ -n "$BRIEF" ]; then
  bash "$TOOLS/init-brief.sh" "$BRIEF"
else
  echo "(skipping brief - none provided; you can edit $COMPANY_DIR/brief.md later)" >&2
fi

# 4. Chat channels
bash "$TOOLS/init-chat-channels.sh"

# 5. Marketing scaffold (real deliverables, in <project_root>/marketing/)
bash "$TOOLS/init-marketing-scaffold.sh" "$PROJECT_ROOT"

# 6. Legal scaffold (privacy, ToS, license inventory, risk register)
bash "$TOOLS/init-legal-scaffold.sh" "$PROJECT_ROOT"

# 7. Finance scaffold (budget, runway, unit economics, projections)
bash "$TOOLS/init-finance-scaffold.sh" "$PROJECT_ROOT"

# 8. Git repo + GitHub remote.
#
# Without this, the very first verification approval crashes: ticket-move.sh
# tries to merge the PR via gh-pr-merge.sh, which fails (no remote, possibly
# no git repo at all). The verification IC has no way to recover and the
# ticket is left in a half-moved state.
if ! git -C "$PROJECT_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  echo "Initializing git repo at $PROJECT_ROOT" >&2
  git -C "$PROJECT_ROOT" init -q
  # Make sure main is the default branch name (newer git defaults vary).
  git -C "$PROJECT_ROOT" symbolic-ref HEAD refs/heads/main 2>/dev/null || true
fi

# Initial commit so `gh repo create --push` and subsequent worktree branches
# have a base. Skip if there's already at least one commit.
if ! git -C "$PROJECT_ROOT" rev-parse HEAD >/dev/null 2>&1; then
  # Drop a minimal README so the initial commit isn't empty (gh repo create
  # --push refuses an empty tree). User can replace at will.
  if [ ! -f "$PROJECT_ROOT/README.md" ]; then
    cat > "$PROJECT_ROOT/README.md" <<EOF
# $NAME

Project workspace managed by Hive.
EOF
  fi
  git -C "$PROJECT_ROOT" add -- README.md .gitignore 2>/dev/null || true
  git -C "$PROJECT_ROOT" -c user.email=hive@local -c user.name=hive \
    commit -q -m "Initial commit (hive init)" 2>/dev/null || true
fi

# GitHub remote / repo creation.
case "$GITHUB" in
  skip)
    echo "(skipping GitHub setup per --github skip; PR-merge approvals will fail until you set up a remote)" >&2
    ;;
  private|public)
    bash "$TOOLS/gh-init.sh" "$PROJECT_ROOT" --create-repo "$GITHUB" || \
      echo "WARNING: gh-init.sh exited non-zero. PR ops may not work — re-run 'bash $TOOLS/gh-init.sh $PROJECT_ROOT --create-repo $GITHUB' once 'gh auth status' is OK." >&2
    ;;
  *)
    # Treat anything else as a literal git URL.
    bash "$TOOLS/gh-init.sh" "$PROJECT_ROOT" --remote "$GITHUB" || \
      echo "WARNING: gh-init.sh exited non-zero. Verify the URL ($GITHUB) is reachable." >&2
    ;;
esac

echo "Workspace ready at $COMPANY_DIR" >&2
