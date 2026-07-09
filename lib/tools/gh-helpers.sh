#!/usr/bin/env bash
# gh-helpers.sh — sourced by every gh-*.sh script. Resolves auth, remote
# URL, default branch. Designed to be silent on success and emit one-line
# errors to stderr on failure (so callers can grep / decide).
#
# Auth resolution:
#   1. Source $HIVE_ENV_FILE (default ~/.config/hive/env.sh) if present.
#   2. If $GITHUB_TOKEN is non-empty after that, export GH_TOKEN=$GITHUB_TOKEN
#      so `gh` uses it without falling back to its keyring.
#   3. Otherwise rely on `gh`'s own stored creds (`gh auth status`).

# Don't `set -e` — callers typically want to handle non-zero gracefully.

HIVE_ENV_FILE="${HIVE_ENV_FILE:-$HOME/.config/hive/env.sh}"
[ -f "$HIVE_ENV_FILE" ] && . "$HIVE_ENV_FILE"

# Prefer GITHUB_TOKEN from env; fall through to gh's keyring otherwise.
if [ -n "${GITHUB_TOKEN:-}" ]; then
  export GH_TOKEN="$GITHUB_TOKEN"
fi

# Returns 0 if any usable auth path is available.
gh_auth_ok() {
  command -v gh >/dev/null 2>&1 || { echo "gh-helpers: gh CLI not installed" >&2; return 2; }
  if [ -n "${GH_TOKEN:-}" ]; then return 0; fi
  gh auth status >/dev/null 2>&1
}

# Returns 0 if the cwd has a `origin` remote that points to GitHub.
gh_remote_ok() {
  local url
  url=$(git -C "${PROJECT_ROOT:-$PWD}" remote get-url origin 2>/dev/null) || return 1
  [ -n "$url" ] && case "$url" in *github.com*) return 0 ;; esac
  return 1
}

# Echo origin's https URL form (transformed from git@github.com:owner/repo.git
# if needed) so it's safe to print in logs.
gh_remote_url() {
  local url
  url=$(git -C "${PROJECT_ROOT:-$PWD}" remote get-url origin 2>/dev/null) || return 1
  case "$url" in
    git@github.com:*)
      url="https://github.com/${url#git@github.com:}"
      url="${url%.git}"
      ;;
    https://github.com/*)
      url="${url%.git}"
      ;;
  esac
  echo "$url"
}

# Default branch on the remote (usually main). Returns empty on failure.
gh_default_branch() {
  gh repo view --json defaultBranchRef -q .defaultBranchRef.name 2>/dev/null
}

# Echo the open PR number for $1 (a branch name); empty if none open.
gh_pr_for_branch() {
  local branch="$1"
  gh pr list --head "$branch" --state open --json number -q '.[0].number' 2>/dev/null
}

# All-in-one preflight. Returns 0 when gh is usable AND remote is set up;
# emits a single human-readable reason on stderr otherwise.
gh_preflight() {
  command -v gh >/dev/null 2>&1 || { echo "no-gh: gh CLI not installed" >&2; return 2; }
  gh_auth_ok                    || { echo "no-auth: gh not authed (set GITHUB_TOKEN in env.sh or run gh auth login)" >&2; return 3; }
  gh_remote_ok                  || { echo "no-remote: no GitHub origin configured for $(pwd)" >&2; return 4; }
  return 0
}
