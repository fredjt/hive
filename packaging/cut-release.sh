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
#   * every version-bearing surface agrees on the new version —
#     VERSION, the pyproject version, the newest CHANGELOG.md section,
#     and the "Latest release" + tarball-name lines in README.md and
#     share/docs/README.md (gate 0 checks all of them and names each
#     stale file; --check-surface runs just that gate)
#   * the tree is clean and on a branch that can be pushed
#
# Usage: cut-release.sh [--version X.Y.Z] [--out-dir DIR] [--suite CMD]
#                       [--dry-run] [--check-surface]
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
#   --check-surface  Run only gate 0 (every version-bearing surface
#              agrees) and stop. Validates the cut commit before the
#              cut runs; no git, no suite, no build.

SOURCE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VERSION=""
OUT_DIR=""
DRY_RUN=0
CHECK_SURFACE=0
SUITE="python -m pytest tests/ -q -x"

while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --suite)   SUITE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --check-surface) CHECK_SURFACE=1; shift ;;
    -h|--help)
      cat <<'EOF'
Usage: cut-release.sh [--version X.Y.Z] [--out-dir DIR] [--suite CMD]
                      [--dry-run] [--check-surface]

The release cut, as a command (E1). Gates, in order:
  0  version sanity — every version-bearing surface agrees (VERSION,
     packaging/pyproject.toml, newest CHANGELOG section, and the
     "Latest release" + tarball-name lines in README.md and
     share/docs/README.md; a mismatch fails naming each stale file)
  1  git state (repo, clean tree, no existing tag, on a branch)
  2  test suite green (default: python -m pytest tests/ -q -x)
  3  build-package
  4  shipping manifest (S1): MANIFEST.txt extracted from the tarball —
     required runtime entries present, runtime-state entries absent,
     each offending entry named
  5  credential-pattern scan of every tarball entry (scan report)
  6  sha256 sidecar verifies
  7  release notes extracted from the CHANGELOG section
  then: git tag, push branch, push tag, gh release create (tarball +
  sha256 sidecar + notes), then the S3 cut receipt is written and
  uploaded as the third asset (--clobber-safe on re-run)

--dry-run runs gates 0-7 and stops before the tag; the tarball, sha256,
scan report, and notes all land in --out-dir.

--check-surface runs only gate 0 and stops: it validates the cut
commit (all version-bearing surfaces at the same version) before the
cut runs. No git, no suite, no build.
EOF
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
 done

echo "==> Hive release cut"

# Gate results (S3): recorded as each gate passes; written into the cut
# receipt after the publish step. Defined before gate 0 — the first
# record_gate call is in the surface check, above the cd.
GATE_RESULTS=""
record_gate() { GATE_RESULTS="$GATE_RESULTS
$1"; }

# ---- Gate 0: version sanity — every version-bearing surface -------------
# The cut commit must move ALL version-bearing surfaces together:
#   VERSION, packaging/pyproject.toml, the newest CHANGELOG.md section,
#   and the "Latest release" + tarball-name lines in README.md and
#   share/docs/README.md. The 0.10.0 cut moved the first three (and
#   the tag) but not the READMEs — the gate had no README surface, so
#   it could not catch the rot; it shipped and was caught post-push.
#   Every stale surface is collected and named in one FAIL (only the
#   stale ones), so a mismatch says exactly which files the cut commit
#   must fix. TestReleaseDocVersion pins the same README surface for
#   the suite; this gate pins it for the cut.
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

STALE=""

FILE_VERSION="$(tr -d '[:space:]' < "$SOURCE_DIR/VERSION")"
if [ "$FILE_VERSION" != "$VERSION" ]; then
  STALE="$STALE
      VERSION (says $FILE_VERSION)"
fi

PY_VERSION=""
[ -f "$SOURCE_DIR/packaging/pyproject.toml" ] && \
  PY_VERSION="$(sed -n 's/^version = "\(.*\)"$/\1/p' \
    "$SOURCE_DIR/packaging/pyproject.toml" | sed -n 1p)"
if [ "$PY_VERSION" != "$VERSION" ]; then
  STALE="$STALE
      packaging/pyproject.toml (version = \"$PY_VERSION\")"
fi

TOP_ENTRY="$(sed -n '/^## /{s/^## //;p;q}' "$SOURCE_DIR/CHANGELOG.md")"
if [ "$TOP_ENTRY" != "$VERSION" ]; then
  STALE="$STALE
      CHANGELOG.md (newest section is $TOP_ENTRY)"
fi

for README in "$SOURCE_DIR/README.md" "$SOURCE_DIR/share/docs/README.md"; do
  REL=""
  [ -f "$README" ] && \
    REL="$(sed -n 's/^Latest release: \*\*v\{0,1\}\([0-9.]*\)\*\*.*/\1/p' \
      "$README" | sed -n 1p)"
  if [ "$REL" != "$VERSION" ]; then
    STALE="$STALE
      ${README#$SOURCE_DIR/} ('Latest release: **v$REL**')"
  elif ! grep -q "hive-${VERSION}\.tar\.gz" "$README"; then
    STALE="$STALE
      ${README#$SOURCE_DIR/} (install steps do not reference hive-${VERSION}.tar.gz)"
  fi
done

if [ -n "$STALE" ]; then
  echo "FAIL: version-bearing surfaces disagree — the cut commit moves" >&2
  echo "      every one of them in a single commit. Stale surfaces:" >&2
  echo "$STALE" >&2
  echo "      Resume: make the cut commit (every version-bearing surface" >&2
  echo "      at v$VERSION), validate it with" >&2
  echo "      'bash packaging/cut-release.sh --check-surface', then re-run:" >&2
  echo "      bash packaging/cut-release.sh --version $VERSION" >&2
  exit 1
fi
echo "    all version-bearing surfaces agree on v$VERSION"
record_gate "gate-0: PASS (all version-bearing surfaces agree on v$VERSION)"

if [ "$CHECK_SURFACE" = 1 ]; then
  echo ""
  echo "==> --check-surface: gate 0 validated — nothing more to run"
  echo "    (full cut: bash packaging/cut-release.sh)"
  exit 0
fi

cd "$SOURCE_DIR"
[ -n "$OUT_DIR" ] || OUT_DIR="$SOURCE_DIR/dist"
mkdir -p "$OUT_DIR"

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
record_gate "gate-1: PASS (clean tree, no existing tag, on branch $BRANCH)"

# ---- Gate 2: the suite must be green -----------------------------------------
echo "==> Gate 2: test suite"
SUITE_LOG="$OUT_DIR/hive-$VERSION.suite-log"
# shellcheck disable=SC2086  # SUITE is a command line, splitting is the point
if ! $SUITE > "$SUITE_LOG" 2>&1; then
  tail -25 "$SUITE_LOG" >&2
  echo "FAIL: test suite is not green — the release is refused (log: $SUITE_LOG)" >&2
  echo "Resume: re-run after fixing: bash packaging/cut-release.sh --version $VERSION" >&2
  exit 1
fi
# The receipt (S3) carries the suite summary line (pytest's final count,
# e.g. "1285 passed, 7 skipped in 746.92s").
SUITE_SUMMARY="$(grep -E '^[0-9]+ (passed|failed)' "$SUITE_LOG" | tail -1)"
[ -n "$SUITE_SUMMARY" ] || SUITE_SUMMARY="$(tail -1 "$SUITE_LOG")"
echo "    suite green ($SUITE_SUMMARY)"
record_gate "gate-2: PASS (suite: $SUITE_SUMMARY)"

# ---- Gate 3: build -----------------------------------------------------------
echo "==> Gate 3: build-package"
bash "$SOURCE_DIR/packaging/build-package.sh" \
  --version "$VERSION" --out-dir "$OUT_DIR"

TARBALL="$OUT_DIR/hive-$VERSION.tar.gz"
CHECKSUM="$OUT_DIR/hive-$VERSION.sha256"
[ -f "$TARBALL" ] || { echo "FAIL: tarball not produced" >&2; exit 1; }
record_gate "gate-3: PASS (build-package produced $TARBALL)"

# ---- Gate 4: shipping manifest (S1, 0.12) ------------------------------------
# The tarball carries its own bill of materials (packaging/build-package.sh
# writes MANIFEST.txt into the staged package). Gate 4 verifies the
# manifest FROM THE TARBALL — the staging dir is not trusted:
#   (a) required runtime paths present — the four original required
#       entries plus the runtime core; a required path missing fails the
#       gate naming it
#   (b) runtime-state paths ABSENT — .company, .worktrees, venv, dist,
#       __pycache__, .git; a state path present fails the gate naming
#       each offending entry
# D.2 proved the shipped surface can drift from the public surface with
# no guard firing; this is the guard.
echo "==> Gate 4: shipping manifest"
LISTING="$(tar -tzf "$TARBALL")"
if ! echo "$LISTING" | grep -q "MANIFEST\.txt$"; then
  echo "FAIL: tarball does not carry MANIFEST.txt — build-package.sh must" >&2
  echo "      write the shipping manifest into the staged package before tarring" >&2
  exit 1
fi
MSTAGE="$(mktemp -d -t hive-cut-manifest-XXXXXX)"
tar -xzf "$TARBALL" -C "$MSTAGE" "hive-$VERSION/MANIFEST.txt" || {
  echo "FAIL: cannot extract MANIFEST.txt from the tarball" >&2
  rm -rf "$MSTAGE"; exit 1
}
MANIFEST="$MSTAGE/hive-$VERSION/MANIFEST.txt"

MISSING=""
for ENTRY in bin/hive lib/core/agent-spawn.sh lib/core/agent-harness.py \
             lib/hive_mcp/server.py lib/dashboard/server.js \
             etc/config.defaults.toml etc/config.template.toml \
             packaging/requirements.txt; do
  # the manifest lists entries relative to the package root
  if ! grep -qxF "$ENTRY" "$MANIFEST"; then
    MISSING="$MISSING $ENTRY"
  fi
done
# the dashboard frontend ships as a directory of static files: require
# at least one entry under lib/dashboard/static/
if ! grep -q '^lib/dashboard/static/' "$MANIFEST"; then
  MISSING="$MISSING lib/dashboard/static/"
fi
if [ -n "$MISSING" ]; then
  echo "FAIL: manifest is missing required runtime entries:$MISSING" >&2
  rm -rf "$MSTAGE"; exit 1
fi

STATE=""
# Runtime-state entries: the six state paths at the package root or
# nested anywhere (a staging entry is a path relative to the package
# root; the prune list in build-package.sh is a second hand-maintained
# list, and the manifest is what pins the two together).
is_state_entry() {
  case "$1" in
    .company|.company/*|.worktrees|.worktrees/*) return 0 ;;
    venv|venv/*|dist|dist/*|__pycache__|__pycache__/*) return 0 ;;
    .git|.git/*) return 0 ;;
    */.company|*/.company/*|*/.worktrees|*/.worktrees/*) return 0 ;;
    */venv|*/venv/*|*/dist|*/dist/*|*/__pycache__|*/__pycache__/*) return 0 ;;
    */.git|*/.git/*) return 0 ;;
  esac
  return 1
}
while IFS= read -r E; do
  if is_state_entry "$E"; then STATE="$STATE $E"; fi
done < "$MANIFEST"
if [ -n "$STATE" ]; then
  echo "FAIL: manifest carries runtime-state entries:$STATE" >&2
  rm -rf "$MSTAGE"; exit 1
fi
rm -rf "$MSTAGE"
echo "    manifest verified: required set present, state-free"
record_gate "gate-4: PASS (shipping manifest verified from the tarball)"

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
record_gate "gate-5: PASS (credential scan clean)"

# ---- Gate 6: sha256 sidecar ---------------------------------------------------
echo "==> Gate 6: sha256"
[ -f "$CHECKSUM" ] || { echo "FAIL: no sha256 sidecar" >&2; exit 1; }
( cd "$OUT_DIR" && sha256sum -c "hive-$VERSION.sha256" ) || {
  echo "FAIL: sha256 sidecar does not verify" >&2
  exit 1
}
record_gate "gate-6: PASS (sha256 sidecar verifies)"

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
record_gate "gate-7: PASS (release notes extracted: $TITLE)"

if [ "$DRY_RUN" = 1 ]; then
  echo ""
  echo "==> DRY RUN — stopping before the tag"
  echo "    would run:"
  echo "      git tag v$VERSION"
  echo "      git push origin $BRANCH"
  echo "      git push origin v$VERSION"
  echo "      gh release create v$VERSION --title \"$TITLE\" \\"
  echo "          --notes-file $NOTES \"$TARBALL\" \"$CHECKSUM\""
  echo "      write $OUT_DIR/hive-$VERSION.receipt.txt (the S3 cut"
  echo "      receipt), then: gh release upload v$VERSION --clobber <receipt>"
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

echo "==> Writing the cut receipt (S3)"
COMMIT="$(git rev-parse HEAD)"
RELEASE_URL="$(gh release view "v$VERSION" --json url --jq .url 2>/dev/null || true)"
if [ -z "$RELEASE_URL" ]; then
  ORIGIN_URL="$(git remote get-url origin 2>/dev/null || true)"
  case "$ORIGIN_URL" in
    https://github.com/*) RELEASE_URL="${ORIGIN_URL%.git}/releases/tag/v$VERSION" ;;
    *) RELEASE_URL="(unavailable — run: gh release view v$VERSION)" ;;
  esac
fi
RECEIPT="$OUT_DIR/hive-$VERSION.receipt.txt"
MANIFEST_SHA="$(tar -xzf "$TARBALL" -O "hive-$VERSION/MANIFEST.txt" | sha256sum | awk '{print $1}')"
TAR_SHA="$(awk '{print $1}' "$CHECKSUM")"
SIDE_SHA="$(sha256sum "$CHECKSUM" | awk '{print $1}')"
{
  echo "receipt: hive-$VERSION"
  echo "version: $VERSION"
  echo "branch: $BRANCH"
  echo "commit: $COMMIT"
  echo "tag: v$VERSION"
  printf '%s\n' "$GATE_RESULTS" | sed '/^$/d'
  echo "asset: hive-$VERSION.tar.gz sha256=$TAR_SHA"
  echo "asset: hive-$VERSION.sha256 sha256=$SIDE_SHA"
  echo "manifest: MANIFEST.txt sha256=$MANIFEST_SHA"
  echo "release: $RELEASE_URL"
  echo "timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$RECEIPT"
gh release upload "v$VERSION" --clobber "$RECEIPT" || {
  echo "FAIL: gh release upload (receipt) failed" >&2
  echo "Resume: upload it (gh release upload v$VERSION --clobber $RECEIPT) or remove the tag (git tag -d v$VERSION && git push origin :refs/tags/v$VERSION), then re-run: bash packaging/cut-release.sh --version $VERSION" >&2
  exit 1
}
echo "    receipt written + uploaded: $RECEIPT"

echo ""
echo "==> Done: v$VERSION tagged, pushed, and published."