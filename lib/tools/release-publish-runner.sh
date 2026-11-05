#!/usr/bin/env bash
set -uo pipefail

# release-publish-runner.sh — stage 8 of the sprint cycle. Synchronous; no spawn.
# Resolves the release publisher script (config or default) and shells to it
# with 4 args: (version, tarball_path, sha256_path, changelog_section_path).
#
# The publisher script is fully pluggable. Default is the GitHub releases
# publisher at $HIVE_ROOT/lib/templates/publishers/github.sh. Users override
# by setting [release].publisher_script in ~/.config/hive/config.toml to an
# absolute path of their own script (same 4-arg signature).
#
# Built-in alternatives (for reference / examples):
#   $HIVE_ROOT/lib/templates/publishers/github.sh    — gh release create
#   $HIVE_ROOT/lib/templates/publishers/local-only.sh — copy to dist/releases/<version>/
#
# Sprint state's release.publish_status is updated to "published" on success
# or "failed" on failure.
#
# Exit codes:
#   0  publisher exited 0 (release published)
#   1  publisher exited non-zero
#   2  bad usage / missing tarball
#   3  publisher script missing or not executable

PROJECT_ROOT="${1:-${PROJECT_ROOT:-$PWD}}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd)" || { echo "release-publish: bad project root" >&2; exit 2; }
# Publishers run from the project root: local-only.sh (and cwd-sensitive
# custom publishers) place artifacts under paths RELATIVE to the CWD.
# Everything below this point is absolute, so the cd is safe.
cd "$PROJECT_ROOT"
COMPANY_DIR="${COMPANY_DIR:-$PROJECT_ROOT/.company}"
SKILL_DIR="${SKILL_DIR:-${HIVE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}}"
export COMPANY_DIR PROJECT_ROOT SKILL_DIR

# shellcheck disable=SC1091
. "$SKILL_DIR/lib/tools/sprint-state.sh"

VERSION=$(ss_get_field .release.version)
TARBALL=$(ss_get_field .release.tarball)
SHA256=$(ss_get_field .release.sha256)

[ -z "$VERSION" ] && { echo "release-publish: sprint state has no release.version" >&2; exit 2; }
[ -z "$TARBALL" ] && { echo "release-publish: sprint state has no release.tarball" >&2; exit 2; }
[ -f "$TARBALL" ] || { echo "release-publish: tarball not found at $TARBALL" >&2; exit 2; }

# Default sha256 path next to tarball if not set explicitly
if [ -z "$SHA256" ]; then
  SHA256="${TARBALL%.tar.gz}.sha256"
fi
[ -f "$SHA256" ] || { echo "release-publish: sha256 not found at $SHA256" >&2; exit 2; }

# Resolve publisher script. ~/.config/hive/config.toml overrides default.
PUBLISHER=""
CFG="${HIVE_CONFIG:-$HOME/.config/hive/config.toml}"
if [ -f "$CFG" ]; then
  # Naive grep — TOML parsing in pure bash is a swamp; the value should be
  # a quoted absolute path. Format: publisher_script = "/path/to/script.sh"
  PUBLISHER=$(awk -F= '/^[[:space:]]*publisher_script[[:space:]]*=/{
    gsub(/^[[:space:]]+|[[:space:]]+$/,"",$2);
    gsub(/^"|"$/,"",$2); print $2
  }' "$CFG" 2>/dev/null | sed -n 1p)
fi
[ -z "$PUBLISHER" ] && PUBLISHER="$SKILL_DIR/lib/templates/publishers/github.sh"

if [ ! -x "$PUBLISHER" ]; then
  echo "release-publish: publisher script not executable: $PUBLISHER" >&2
  ss_set_release_field publish_status failed
  exit 3
fi

# Build a changelog section. Prefer CHANGELOG.md section for $VERSION, else
# a stub generated from `git log` since the previous tag.
CHANGELOG_SECTION=$(mktemp -t hive-changelog.XXXXXX.md)
trap 'rm -f "$CHANGELOG_SECTION"' EXIT
{
  echo "# Release $VERSION"
  echo
  if [ -f "$PROJECT_ROOT/CHANGELOG.md" ]; then
    awk -v v="$VERSION" '
      /^##[[:space:]]/ {
        if (in_section) exit
        if (index($0, v)) { in_section=1; next }
      }
      in_section { print }
    ' "$PROJECT_ROOT/CHANGELOG.md"
  else
    PREV_TAG=$(git -C "$PROJECT_ROOT" describe --tags --abbrev=0 2>/dev/null || true)
    if [ -n "$PREV_TAG" ]; then
      echo "Changes since $PREV_TAG:"
      git -C "$PROJECT_ROOT" log "$PREV_TAG..HEAD" --pretty=format:'- %s' 2>/dev/null
    else
      echo "First release."
    fi
  fi
} > "$CHANGELOG_SECTION"

LOG="$COMPANY_DIR/releases/${VERSION}-publish.log"
mkdir -p "$(dirname "$LOG")"

echo "release-publish: invoking $PUBLISHER for v$VERSION"
echo "release-publish: log → $LOG"

if HIVE_PR_OP_AUTHORIZED=1 bash "$PUBLISHER" "$VERSION" "$TARBALL" "$SHA256" "$CHANGELOG_SECTION" \
     >> "$LOG" 2>&1; then
  ss_set_release_field publish_status published
  echo "release-publish: PUBLISHED v$VERSION"
  exit 0
else
  rc=$?
  ss_set_release_field publish_status failed
  echo "release-publish: FAILED v$VERSION (publisher exit=$rc, log=$LOG)" >&2
  exit 1
fi
