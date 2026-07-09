#!/usr/bin/env bash
set -uo pipefail

# Local-only release publisher. Copies tarball + sha256 + notes into
# <project>/dist/releases/<version>/. No remote interaction.
#
# Args: same 4-arg signature as publishers/github.sh.

VERSION="$1"
TARBALL="$2"
SHA256="$3"
CHANGELOG="$4"

[ -z "$VERSION" ] || [ -z "$TARBALL" ] && { echo "publishers/local-only.sh: missing args" >&2; exit 2; }

# Place releases under whatever PWD is (release-publish-runner cd's to PROJECT_ROOT)
DEST_DIR="dist/releases/${VERSION}"
mkdir -p "$DEST_DIR"

cp -f "$TARBALL"   "$DEST_DIR/"
cp -f "$SHA256"    "$DEST_DIR/"
cp -f "$CHANGELOG" "$DEST_DIR/RELEASE_NOTES.md"

echo "publishers/local-only.sh: published v$VERSION to $DEST_DIR/"
ls -la "$DEST_DIR/"
