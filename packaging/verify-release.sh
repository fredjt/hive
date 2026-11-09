#!/usr/bin/env bash
# verify-release.sh — T4 (0.13): verify a PUBLISHED release in one
# command.
#
# Usage: bash packaging/verify-release.sh <tag> [workdir]
#   <tag>      release tag, e.g. v0.12.0. Receipts start at v0.12.0 —
#              earlier releases have no receipt and are unverifiable by
#              design (0.13 promises nothing about them).
#   [workdir]  scratch dir for the downloaded assets (default: mktemp,
#              removed on exit).
#
# Fetches the release's three assets (tarball, sha256 sidecar, cut
# receipt) via `gh release download` and verifies:
#   1. the sidecar matches the downloaded tarball (sha256sum -c)
#   2. the receipt's `asset: <tarball> sha256=` line matches the
#      recomputed tarball digest
#   3. the receipt's `manifest: MANIFEST.txt sha256=` matches the
#      tarball's MANIFEST.txt member, recomputed from the tarball
#      itself (S1: the bill of materials travels with the package)
#   4. the receipt parses as `key: value` lines and carries the
#      required keys — receipt, version, tag, commit, gate-0..gate-7
#      (every gate PASS), both asset lines, manifest, release — and
#      agrees with the tag (version/tag consistency)
#   5. prints the commit + release URL for the human
#
# Exit 0 when every check passes, 1 otherwise — each failure names
# the mismatch. The S3 receipt is machine-verifiable; this is the
# machine that verifies it. Not a CLI command on purpose: 0.13 stays
# feature-light, and packaging scripts stay in packaging/.
set -uo pipefail
TAG="${1:-}"
if [ -z "$TAG" ]; then
  echo "usage: bash packaging/verify-release.sh <tag> [workdir]" >&2
  echo "  e.g. bash packaging/verify-release.sh v0.12.0" >&2
  exit 1
fi
if [ "$TAG" = "-h" ] || [ "$TAG" = "--help" ]; then
  sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

WORKDIR="${2:-}"
CLEANED_WORKDIR=0
if [ -z "$WORKDIR" ]; then
  WORKDIR="$(mktemp -d -t hive-verify-release-XXXXXX)"
  CLEANED_WORKDIR=1
fi
mkdir -p "$WORKDIR"
trap '[ "$CLEANED_WORKDIR" = 1 ] && rm -rf "$WORKDIR"' EXIT

VERSION="${TAG#v}"
TARBALL_NAME="hive-$VERSION.tar.gz"
SIDE_NAME="hive-$VERSION.sha256"
RECEIPT_NAME="hive-$VERSION.receipt.txt"

FAIL=0
fail() { echo "FAIL: $*" >&2; FAIL=1; }
ok()   { echo "    $*"; }

echo "==> verify-release: $TAG"
echo "    downloading assets to $WORKDIR"
if ! gh release download "$TAG" --dir "$WORKDIR" 2>/dev/null; then
  fail "gh release download $TAG failed — is the tag published?"
  echo "    nothing to verify" >&2
  exit 1
fi
cd "$WORKDIR" || exit 1
for f in "$TARBALL_NAME" "$SIDE_NAME" "$RECEIPT_NAME"; do
  [ -f "$f" ] || fail "asset $f is missing from release $TAG"
done
[ "$FAIL" = 0 ] || { echo "    cannot continue — assets incomplete" >&2; exit 1; }

# ---- 1. the sidecar matches the downloaded tarball --------------------
ACTUAL_TAR="$(sha256sum "$TARBALL_NAME" | awk '{print $1}' )"
if sha256sum -c "$SIDE_NAME" --status 2>/dev/null; then
  ok "1/4 sidecar verifies: $TARBALL_NAME"
else
  EXPECTED_SIDE="$(awk '{print $1}' "$SIDE_NAME" | head -1)"
  fail "1/4 sidecar mismatch: $SIDE_NAME says $EXPECTED_SIDE, the downloaded $TARBALL_NAME is $ACTUAL_TAR"
fi

# ---- 2. the receipt's tarball digest matches ---------------------------
RECEIPT_TAR="$(grep -F "asset: $TARBALL_NAME sha256=" "$RECEIPT_NAME" | head -1 | awk '{print $3}' | cut -d= -f2)"
if [ -z "$RECEIPT_TAR" ]; then
  fail "2/4 receipt has no 'asset: $TARBALL_NAME sha256=' line"
elif [ "$RECEIPT_TAR" = "$ACTUAL_TAR" ]; then
  ok "2/4 receipt tarball digest matches: $ACTUAL_TAR"
else
  fail "2/4 receipt tarball digest mismatch: receipt says $RECEIPT_TAR, the tarball is $ACTUAL_TAR"
fi
# ---- 3. the receipt's manifest digest matches the tarball member -------
if tar -xzf "$TARBALL_NAME" -O "hive-$VERSION/MANIFEST.txt" 2>/dev/null > "$WORKDIR/.manifest-check"; then
  ACTUAL_MAN="$(sha256sum "$WORKDIR/.manifest-check" | awk '{print $1}' )"
else
  ACTUAL_MAN=""
fi
RECEIPT_MAN="$(grep -F "manifest: MANIFEST.txt sha256=" "$RECEIPT_NAME" | head -1 | awk '{print $3}' | cut -d= -f2)"
if [ -z "$ACTUAL_MAN" ]; then
  fail "3/4 tarball has no hive-$VERSION/MANIFEST.txt member (the S1 bill of materials)"
elif [ -z "$RECEIPT_MAN" ]; then
  fail "3/4 receipt has no 'manifest: MANIFEST.txt sha256=' line"
elif [ "$RECEIPT_MAN" = "$ACTUAL_MAN" ]; then
  ok "3/4 receipt manifest digest matches the tarball's MANIFEST.txt: $ACTUAL_MAN"
else
  fail "3/4 receipt manifest digest mismatch: receipt says $RECEIPT_MAN, the tarball's MANIFEST.txt is $ACTUAL_MAN"
fi
rm -f "$WORKDIR/.manifest-check"

# ---- 4. the receipt parses and carries the required keys ---------------
BAD_LINES=""
while IFS= read -r line; do
  [ -n "$line" ] || continue
  case "$line" in
    [a-zA-Z0-9._-]*:" "*) : ;;
    *) BAD_LINES="$BAD_LINES|$line" ;;
  esac
done < "$RECEIPT_NAME"
if [ -n "$BAD_LINES" ]; then
  fail "4/4 receipt lines that are not 'key: value':${BAD_LINES}"
fi
MISSING_KEYS=""
for key in receipt version tag commit manifest release; do
  grep -q "^$key: .\+" "$RECEIPT_NAME" || MISSING_KEYS="$MISSING_KEYS $key"
done
for n in 0 1 2 3 4 5 6 7; do
  grep -qE "^gate-$n: PASS " "$RECEIPT_NAME" || MISSING_KEYS="$MISSING_KEYS gate-$n-PASS"
done
ASSET_LINES="$(grep -c '^asset: ' "$RECEIPT_NAME" || true)"
[ "$ASSET_LINES" = 2 ] || MISSING_KEYS="$MISSING_KEYS asset-lines($ASSET_LINES/2)"
if [ -n "$MISSING_KEYS" ]; then
  fail "4/4 receipt is missing required keys:$MISSING_KEYS"
fi
RECEIPT_VERSION="$(grep '^version: ' "$RECEIPT_NAME" | head -1 | awk '{print $2}' )"
RECEIPT_TAG="$(grep '^tag: ' "$RECEIPT_NAME" | head -1 | awk '{print $2}' )"
[ "$RECEIPT_VERSION" = "$VERSION" ] || fail "4/4 receipt version ($RECEIPT_VERSION) does not match tag $TAG"
[ "$RECEIPT_TAG" = "$TAG" ] || fail "4/4 receipt tag ($RECEIPT_TAG) does not match $TAG"
if [ "$FAIL" = 0 ] && [ -z "$BAD_LINES" ] && [ -z "$MISSING_KEYS" ]; then
  ok "4/4 receipt parses; all required keys present; every gate PASS"
fi
# ---- 5. the human summary ----------------------------------------------
COMMIT="$(grep '^commit: ' "$RECEIPT_NAME" | head -1 | awk '{print $2}' )"
RELEASE_URL="$(grep '^release: ' "$RECEIPT_NAME" | head -1 | cut -d' ' -f2)"
echo "    commit:  $COMMIT"
echo "    release: $RELEASE_URL"

if [ "$FAIL" = 0 ]; then
  echo "verify-release: $TAG OK — sidecar, receipt digests, and receipt keys all verified"
  exit 0
fi
echo "verify-release: $TAG FAILED — see the FAIL lines above" >&2
exit 1
