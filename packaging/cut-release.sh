#!/usr/bin/env bash
set -euo pipefail

# cut-release.sh — the release cut, as a command (E1).
#
# The 0.7.0 and 0.7.1 releases were cut by hand: suite -> build ->
# tar listing check -> credential scan -> sha256 -> tag -> push ->
# gh release. This script is that dance, in that order, with a hard
# gate at every step: no step runs unless the previous one passed.
#
# Precondition (the cut commit, done BEFORE running this script — this
# script verifies but never edits the tree):
#   * VERSION, the pyproject version, and the newest CHANGELOG.md
#     section all agree on the new version (TestVersionFile pins this)
#   * the tree is clean and on a branch that can be pushed
#
# Usage: cut-release.sh [--version X.Y.Z] [--out-dir DIR] [--suite CMD]
#                       [--dry-run]
#   --version  Version to cut (default: the VERSION file)
#   --out-dir  Build output directory (default: dist/)
#   --suite    Replace the test-suite command (default: full suite,
#              stop at first failure). The gate itself never changes —
#              only what the suite runs is replaceable.
#   --dry-run  Run every gate up to the tag: suite, build, listing
#              check, credential scan, sha256, release notes — then
#              stop. No tag, no push, no release. The tarball, sha256
#              sidecar, scan report, and notes file all land in
#              --out-dir.

SOURCE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VERSION=""
OUT_DIR=""
DRY_RUN=0
SUITE="python -m pytest tests/ -q -x"

while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --suite)   SUITE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help)
      cat <<'EOF'
Usage: cut-release.sh [--version X.Y.Z] [--out-dir DIR] [--suite CMD]
                      [--dry-run]

The release cut, as a command (E1). Gates, in order:
  0  version sanity (VERSION file + newest CHANGELOG section agree)
  1  git state (repo, clean tree, no existing tag, on a branch)
  2  test suite green (default: python -m pytest tests/ -q -x)
  3  build-package
  4  tar listing check (E2's required entries)
  5  credential-pattern scan of every tarball entry (scan report)
  6  sha256 sidecar verifies
  7  release notes extracted from the CHANGELOG section
  then: git tag, push branch, push tag, gh release create (assets + notes)

--dry-run runs gates 0-7 and stops before the tag; the tarball, sha256,
scan report, and notes all land in --out-dir.
EOF
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
 done

echo "==> Hive release cut"

# ---- Gate 0: version sanity -------------------------------------------------
if [ -z "$VERSION" ]; then
  if [ ! -f "$SOURCE_DIR/VERSION" ]; then
    echo "FAIL: no VERSION file and no --version given" >&2
    exit 1
  fi
  VERSION="$(tr -d '[:space:]' < "$SOURCE_DIR/VERSION")"
fi
if ! echo "$VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
  echo "FAIL: version '$VERSION' is not X.Y.Z" >&2
  exit 1
fi
echo "    cutting v$VERSION"

FILE_VERSION="$(tr -d '[:space:]' < "$SOURCE_DIR/VERSION")"
if [ "$FILE_VERSION" != "$VERSION" ]; then
  echo "FAIL: VERSION file says $FILE_VERSION, cutting $VERSION." >&2
  echo "      Make the cut commit first (VERSION + CHANGELOG section)." >&2
  exit 1
fi

TOP_ENTRY="$(sed -n '/^## /{s/^## //;p;q}' "$SOURCE_DIR/CHANGELOG.md")"
if [ "$TOP_ENTRY" != "$VERSION" ]; then
  echo "FAIL: newest CHANGELOG entry is '$TOP_ENTRY', cutting $VERSION." >&2
  echo "      Add the $VERSION changelog section first (the cut commit)." >&2
  exit 1
fi

cd "$SOURCE_DIR"

# ---- Gate 1: git state -------------------------------------------------------
git rev-parse --git-dir >/dev/null 2>&1 || {
  echo "FAIL: not a git repository — the tag/push steps need one" >&2
  exit 1
}
DIRTY="$(git status --porcelain)"
if [ -n "$DIRTY" ]; then
  echo "FAIL: working tree is dirty — commit or stash first:" >&2
  echo "$DIRTY" | sed 's/^/      /' >&2
  exit 1
fi
if git tag -l "v$VERSION" | grep -q .; then
  echo "FAIL: tag v$VERSION already exists" >&2
  echo "Resume: complete the publish (gh release create v$VERSION <tarball> <sha256> --notes-file <notes>) or remove the stale tag (git tag -d v$VERSION && git push origin :refs/tags/v$VERSION), then re-run: bash packaging/cut-release.sh --version $VERSION" >&2
  exit 1
fi
BRANCH="$(git branch --show-current)"
[ -n "$BRANCH" ] || { echo "FAIL: detached HEAD" >&2; exit 1; }

# ---- Gate 2: the suite must be green -----------------------------------------
echo "==> Gate 2: test suite"
# shellcheck disable=SC2086  # SUITE is a command line, splitting is the point
if ! $SUITE; then
  echo "FAIL: test suite is not green — the release is refused" >&2
  echo "Resume: re-run after fixing: bash packaging/cut-release.sh --version $VERSION" >&2
  exit 1
fi
echo "    suite green"

# ---- Gate 3: build -----------------------------------------------------------
[ -n "$OUT_DIR" ] || OUT_DIR="$SOURCE_DIR/dist"
echo "==> Gate 3: build-package"
bash "$SOURCE_DIR/packaging/build-package.sh" \
  --version "$VERSION" --out-dir "$OUT_DIR"

TARBALL="$OUT_DIR/hive-$VERSION.tar.gz"
CHECKSUM="$OUT_DIR/hive-$VERSION.sha256"
[ -f "$TARBALL" ] || { echo "FAIL: tarball not produced" >&2; exit 1; }

# ---- Gate 4: tar listing (E2's guard, as a step) -----------------------------
echo "==> Gate 4: tar listing"
LISTING="$(tar -tzf "$TARBALL")"
MISSING=""
for ENTRY in bin/hive etc/config.template.toml etc/config.defaults.toml \
             packaging/requirements.txt; do
  # the tar prefixes every entry with the pkg dir, so match the tail
  if ! echo "$LISTING" | grep -q "/$ENTRY$"; then
    MISSING="$MISSING $ENTRY"
  fi
done
if [ -n "$MISSING" ]; then
  echo "FAIL: tarball is missing install-runtime entries:$MISSING" >&2
  exit 1
fi
echo "    listing complete"

# ---- Gate 5: credential-pattern scan of every tarball entry ------------------
echo "==> Gate 5: credential scan"
SCAN_REPORT="$OUT_DIR/hive-$VERSION.scan-report"
STAGE="$(mktemp -d -t hive-cut-scan-XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT
tar -xzf "$TARBALL" -C "$STAGE"
# Same pattern set the history-scrub guard pins (tests/test_history_scrub.py):
# a credential in the public tarball is a leak of the same class as one in
# history. The private-key header is assembled from string parts so this
# script's own blob stays clean under the guard it invokes (the guard
# assembles the same pattern from fragments for the same reason).
_PKEY="PRIVATE"
PATTERNS="gho_[A-Za-z0-9]{8,}|ghp_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{8,}|sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|BEGIN [A-Z ]*${_PKEY} KEY|xox[baprs]-[A-Za-z0-9-]{8,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"
HITS="$(grep -rInE "$PATTERNS" "$STAGE" || true)"
{
  echo "# Credential-pattern scan — hive-$VERSION.tar.gz"
  echo "# Source: $(git rev-parse HEAD) on $BRANCH"
  echo "# Patterns: tests/test_history_scrub.py CREDENTIAL_PATTERNS"
  if [ -n "$HITS" ]; then
    echo "RESULT: FINDINGS"
    echo "$HITS"
  else
    echo "RESULT: CLEAN — no credential patterns in any tarball entry"
  fi
} > "$SCAN_REPORT"
if [ -n "$HITS" ]; then
  echo "FAIL: credential patterns found in the tarball (see $SCAN_REPORT):" >&2
  echo "$HITS" | head -20 >&2
  echo "Resume: remove the credential from the tree, commit the fix, then re-run: bash packaging/cut-release.sh --version $VERSION" >&2
  exit 1
fi
echo "    scan clean — report: $SCAN_REPORT"

# ---- Gate 6: sha256 sidecar ---------------------------------------------------
echo "==> Gate 6: sha256"
[ -f "$CHECKSUM" ] || { echo "FAIL: no sha256 sidecar" >&2; exit 1; }
( cd "$OUT_DIR" && sha256sum -c "hive-$VERSION.sha256" ) || {
  echo "FAIL: sha256 sidecar does not verify" >&2
  exit 1
}

# ---- Gate 7: release notes from the CHANGELOG section -------------------------
echo "==> Gate 7: release notes"
NOTES="$OUT_DIR/hive-$VERSION.release-notes.md"
awk -v ver="## $VERSION" '
  $0 == ver { insec = 1; next }
  insec && /^## / { exit }
  insec { print }
' "$SOURCE_DIR/CHANGELOG.md" > "$NOTES"
if [ ! -s "$NOTES" ]; then
  echo "FAIL: no CHANGELOG section for $VERSION (empty notes)" >&2
  exit 1
fi
# Title: the section's first prose line, e.g.
#   "Hive 0.7.1 — config streamlining"
# sed, not head: head exits after one line and, under set -o pipefail,
# SIGPIPEs the middle grep the instant the notes exceed the pipe buffer
# (exit 141 aborts the cut via set -e). sed drains the pipe.
FIRST_LINE="$(grep -v '^#' "$NOTES" | grep -v '^\s*$' | sed -n 1p)"
TITLE="Hive $VERSION"
[ -n "$FIRST_LINE" ] && TITLE="Hive $VERSION — $(echo "$FIRST_LINE" | sed 's/\s\{2,\}/ /g')"

if [ "$DRY_RUN" = 1 ]; then
  echo ""
  echo "==> DRY RUN — stopping before the tag"
  echo "    would run:"
  echo "      git tag v$VERSION"
  echo "      git push origin $BRANCH"
  echo "      git push origin v$VERSION"
  echo "      gh release create v$VERSION --title \"$TITLE\" \\"
  echo "          --notes-file $NOTES \"$TARBALL\" \"$CHECKSUM\""
  echo "    artifacts in $OUT_DIR:"
  echo "      $(basename "$TARBALL")"
  echo "      $(basename "$CHECKSUM")"
  echo "      $(basename "$SCAN_REPORT")"
  echo "      $(basename "$NOTES")"
  exit 0
fi

# ---- Tag, push, publish --------------------------------------------------------
echo "==> Tagging v$VERSION"
git tag "v$VERSION"
git push origin "$BRANCH"
git push origin "v$VERSION"

echo "==> Publishing the GitHub release"
command -v gh >/dev/null || {
  echo "FAIL: gh CLI is required to publish (or run it by hand)" >&2
  echo "      gh release create v$VERSION --title \"$TITLE\" \"$TARBALL\" \"$CHECKSUM\""
  exit 1
}
PUBLISH_RESUME="Resume: the tag v$VERSION is already in place — complete the publish (gh release create v$VERSION --title \"$TITLE\" --notes-file $NOTES $TARBALL $CHECKSUM) or remove the tag (git tag -d v$VERSION && git push origin :refs/tags/v$VERSION), then re-run: bash packaging/cut-release.sh --version $VERSION"
if gh release view "v$VERSION" >/dev/null 2>&1; then
  # Re-run against an existing release: update title/notes and
  # re-upload the assets — the cut is idempotent. (gh release create
  # has no --clobber across versions; upload does.)
  echo "    release v$VERSION exists — updating title, notes, assets"
  gh release edit "v$VERSION" --title "$TITLE" --notes-file "$NOTES" || {
    echo "FAIL: gh release edit failed" >&2; echo "$PUBLISH_RESUME" >&2; exit 1; }
  gh release upload "v$VERSION" --clobber "$TARBALL" "$CHECKSUM" || {
    echo "FAIL: gh release upload failed" >&2; echo "$PUBLISH_RESUME" >&2; exit 1; }
else
  gh release create "v$VERSION" \
    --title "$TITLE" \
    --notes-file "$NOTES" \
    "$TARBALL" "$CHECKSUM" || {
    echo "FAIL: gh release create failed" >&2; echo "$PUBLISH_RESUME" >&2; exit 1; }
fi

echo ""
echo "==> Done: v$VERSION tagged, pushed, and published."