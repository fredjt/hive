#!/usr/bin/env bash
set -uo pipefail

# gh-init.sh — first-run GitHub setup for a hive project.
#
# Idempotent. Safe to run on every `hive init`. Does:
#   1. Source gh-helpers.sh; preflight gh + auth.
#   2. If the current dir is a git repo with no `origin`, offer (or auto-set
#      with --remote URL) the remote.
#   3. Copy CI workflow templates + PR template into <project>/.github/ if
#      not already present.
#   4. Print next-step hints when something needs human action.
#
# Usage:
#   gh-init.sh <project-root> [--remote <url>] [--create-repo private|public|skip]

PROJECT_ROOT="${1:-.}"
shift || true
PROJECT_ROOT="$(cd "$PROJECT_ROOT" && pwd)"
SKILL_DIR="${HIVE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"

REMOTE_URL=""
CREATE_REPO="skip"
while [ $# -gt 0 ]; do
  case "$1" in
    --remote)      REMOTE_URL="$2"; shift 2 ;;
    --create-repo) CREATE_REPO="$2"; shift 2 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

. "$SKILL_DIR/lib/tools/gh-helpers.sh"

cd "$PROJECT_ROOT"

# Step 1 — git repo presence
if ! git -C "$PROJECT_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  echo "gh-init: $PROJECT_ROOT is not a git repo (run 'git init' first)" >&2
  exit 1
fi

# Step 2 — gh CLI presence (warn, don't block — local-only fallback works)
if ! command -v gh >/dev/null 2>&1; then
  echo "gh-init: WARNING — gh CLI not installed. GitHub features (PR open/merge,"
  echo "         issue mirror, release publish) will be skipped. Hive will keep"
  echo "         working in local-only mode."
fi

# Step 3 — remote setup
if ! gh_remote_ok; then
  if [ -n "$REMOTE_URL" ]; then
    echo "gh-init: setting origin to $REMOTE_URL"
    git -C "$PROJECT_ROOT" remote add origin "$REMOTE_URL" 2>/dev/null \
      || git -C "$PROJECT_ROOT" remote set-url origin "$REMOTE_URL"
  elif [ "$CREATE_REPO" != "skip" ] && command -v gh >/dev/null 2>&1; then
    if gh_auth_ok; then
      visibility="--$CREATE_REPO"  # --private or --public
      name=$(basename "$PROJECT_ROOT")
      echo "gh-init: creating GitHub repo for $name ($CREATE_REPO)…"
      ( cd "$PROJECT_ROOT" && gh repo create "$name" "$visibility" --source=. --remote=origin --push )
    else
      echo "gh-init: WARNING — gh not authed; skipping repo create. Run 'gh auth login' or set GITHUB_TOKEN."
    fi
  else
    echo "gh-init: no GitHub origin configured. To enable PRs/CI/releases:"
    echo "    cd $PROJECT_ROOT"
    echo "    gh repo create <name> --private --source=. --remote=origin --push"
    echo "  or:"
    echo "    git remote add origin git@github.com:<owner>/<repo>.git"
    echo "    git push -u origin main"
  fi
fi

# Step 4 — CI workflow templates
WORKFLOW_DIR="$PROJECT_ROOT/.github/workflows"
mkdir -p "$WORKFLOW_DIR"
TEMPLATE_DIR="$SKILL_DIR/lib/templates/github-workflows"
copied=0
for tpl in ci.yml release.yml; do
  src="$TEMPLATE_DIR/$tpl"
  dst="$WORKFLOW_DIR/$tpl"
  if [ ! -f "$src" ]; then
    echo "gh-init: WARNING — template missing: $src" >&2
    continue
  fi
  if [ -f "$dst" ]; then
    continue   # respect any user customizations
  fi
  cp "$src" "$dst"
  copied=$((copied + 1))
  echo "gh-init: installed .github/workflows/$tpl"
done

# PR template
PR_TPL_SRC="$SKILL_DIR/lib/templates/PULL_REQUEST_TEMPLATE.md"
PR_TPL_DST="$PROJECT_ROOT/.github/PULL_REQUEST_TEMPLATE.md"
if [ -f "$PR_TPL_SRC" ] && [ ! -f "$PR_TPL_DST" ]; then
  cp "$PR_TPL_SRC" "$PR_TPL_DST"
  echo "gh-init: installed .github/PULL_REQUEST_TEMPLATE.md"
fi

# Step 5 — make sure these workflow files get committed (they often slip past
# the per-ticket git diff). Don't actually commit; just hint.
if [ "$copied" -gt 0 ]; then
  echo
  echo "gh-init: $copied template(s) installed. Commit them to enable CI:"
  echo "    cd $PROJECT_ROOT && git add .github && git commit -m 'CI: install hive workflow templates'"
fi

echo "gh-init: done."
