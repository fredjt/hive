#!/usr/bin/env bash
set -uo pipefail

# Default release publisher: GitHub releases.
# Called by release-publish-runner.sh with 4 args.
#
# Args:
#   $1 version            (e.g. "0.4.0")
#   $2 tarball_path       (e.g. "dist/hive-0.4.0.tar.gz")
#   $3 sha256_path        (e.g. "dist/hive-0.4.0.sha256")
#   $4 changelog_section  (path to .md file with the release notes)
#
# Behavior:
#   - tag the version (annotated, signed if user has a key) if not already
#   - push the tag
#   - gh release create with the tarball, sha256, and changelog
#
# Requires: gh CLI authed, git remote 'origin' configured.

VERSION="$1"
TARBALL="$2"
SHA256="$3"
CHANGELOG="$4"

[ -z "$VERSION" ] || [ -z "$TARBALL" ] && { echo "publishers/github.sh: missing args" >&2; exit 2; }

TAG="v${VERSION#v}"   # accept either "0.4.0" or "v0.4.0" → always "vX.Y.Z"

if ! command -v gh >/dev/null 2>&1; then
  echo "publishers/github.sh: gh CLI not installed; cannot publish to GitHub releases" >&2
  exit 1
fi

# Tag if missing locally
if ! git rev-parse "$TAG" >/dev/null 2>&1; then
  echo "publishers/github.sh: tagging $TAG"
  git tag -a "$TAG" -m "Release $TAG" || { echo "tag failed" >&2; exit 1; }
fi

# Push tag
echo "publishers/github.sh: pushing $TAG to origin"
git push origin "$TAG" 2>&1 || echo "publishers/github.sh: tag push reported error (may be already pushed; continuing)"

echo "publishers/github.sh: creating GitHub release for $TAG"
gh release create "$TAG" "$TARBALL" "$SHA256" \
  --title "$TAG" \
  --notes-file "$CHANGELOG" \
  --target main 2>&1
