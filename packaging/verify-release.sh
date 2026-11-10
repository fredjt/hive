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
#   bash packaging/verify-release.sh <new-tag> --from <prev-tag> [tree]
#   T1 (0.15) — upgrade verification: NEW-TAG verifies as usual (1-4),
#   PLUS the upgrade checks (5-7 below). [tree] in this mode is the
#   install dir (default: $HIVE_INSTALL_DIR, else the checkout this
#   script runs from).
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
  echo "       bash packaging/verify-release.sh <new-tag> --from <prev-tag> [tree]" >&2
  echo "  e.g. bash packaging/verify-release.sh v0.12.0" >&2
  exit 1
fi
if [ "$TAG" = "-h" ] || [ "$TAG" = "--help" ]; then
  sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi
FROM=""
if [ "${2:-}" = "--from" ]; then
  FROM="${3:-}"
  [ -n "$FROM" ] || { echo "usage: --from PREV-TAG" >&2; exit 1; }
fi
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -n "$FROM" ]; then
  TREE="${4:-${HIVE_INSTALL_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}}"
  WORKDIR=""
else
  WORKDIR="${2:-}"
fi
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

PREV_VERSION=""
if [ -n "$FROM" ]; then
  PREV_VERSION="${FROM#v}"
  PREV_RECEIPT="hive-$PREV_VERSION.receipt.txt"
  mkdir -p "$WORKDIR/prev"
  if ! gh release download "$FROM" --pattern "$PREV_RECEIPT" --dir "$WORKDIR/prev" 2>/dev/null \
      || [ ! -f "$WORKDIR/prev/$PREV_RECEIPT" ]; then
    fail "upgrade: cannot download the previous receipt ($FROM/$PREV_RECEIPT) — the chain cannot be verified"
  fi
fi

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
# receipt_keys_ok RECEIPT VERSION TAG — validates the required keys, the
# 'key: value' line shape, every gate PASS, and version/tag agreement.
# T1 (0.15): shared with the upgrade mode's previous-receipt check.
receipt_keys_ok() {
  local f="$1" bad="" missing="" al rv rt key n
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    case "$line" in
      [a-zA-Z0-9._-]*:" "*) : ;;
      *) bad="$bad|$line" ;;
    esac
  done < "$f"
  [ -z "$bad" ] || fail "receipt $f has lines that are not 'key: value':$bad"
  for key in receipt version tag commit manifest release; do
    grep -q "^$key: .\\+" "$f" || missing="$missing $key"
  done
  for n in 0 1 2 3 4 5 6 7; do
    grep -qE "^gate-$n: PASS " "$f" || missing="$missing gate-$n-PASS"
  done
  al="$(grep -c '^asset: ' "$f" || true)"
  [ "$al" = 2 ] || missing="$missing asset-lines($al/2)"
  [ -z "$missing" ] || fail "receipt $f is missing required keys:$missing"
  rv="$(grep '^version: ' "$f" | head -1 | awk '{print $2}')"
  rt="$(grep '^tag: ' "$f" | head -1 | awk '{print $2}')"
  [ "$rv" = "$2" ] || fail "receipt $f version ($rv) does not match $2"
  [ "$rt" = "$3" ] || fail "receipt $f tag ($rt) does not match $3"
  return 0
}
receipt_keys_ok "$RECEIPT_NAME" "$VERSION" "$TAG"
if [ "$FAIL" = 0 ]; then
  ok "4/4 receipt parses; all required keys present; every gate PASS"
fi
# ---- 5-7. T1 (0.15): the upgrade checks (only with --from) -----------------
if [ -n "$FROM" ]; then
  echo "    upgrade verification: $TREE (from $FROM)"
  ENTRY_TOP="$WORKDIR/.entry-top"
  tar -xzf "$TARBALL_NAME" -O "hive-$VERSION/MANIFEST.txt" 2>/dev/null \
    | awk -F/ '{print $1}' | LC_ALL=C sort -u > "$ENTRY_TOP"
  [ -s "$ENTRY_TOP" ] || fail "upgrade: no MANIFEST.txt in the new tarball — cannot compare the tree"
  RESIDUE=""
  for p in "$TREE"/* "$TREE"/.[!.]*; do
    [ -e "$p" ] || continue
    n="$(basename "$p")"
    case "$n" in venv|.hive-install) continue ;; esac
    grep -qxF "$n" "$ENTRY_TOP" || RESIDUE="$RESIDUE $n"
  done
  [ -z "$RESIDUE" ] || fail "upgrade: $TREE has top-level entries outside the $TAG entry set:$RESIDUE"
  REQ_NEW="$WORKDIR/.required-new"
  tar -xzf "$TARBALL_NAME" -O "hive-$VERSION/packaging/required-entries.list" 2>/dev/null > "$REQ_NEW"
  [ -s "$REQ_NEW" ] || fail "upgrade: no packaging/required-entries.list in the new tarball"
  MISSING=""
  while IFS= read -r req; do
    [ -n "$req" ] || continue
    [ -e "$TREE/$req" ] || MISSING="$MISSING $req"
  done < <(awk '{ sub(/#.*/, "") } /^[[:space:]]*$/ { next } $0 == "REQUIRED" { in_sec=1; next } $0 == "STATE" { in_sec=0; next } in_sec && NF==1 { print $1 }' "$REQ_NEW")
  if [ -x "$TREE/venv/bin/python" ]; then
    LOCK_NEW="$WORKDIR/.lock-new"
    tar -xzf "$TARBALL_NAME" -O "hive-$VERSION/packaging/requirements.lock.txt" 2>/dev/null > "$LOCK_NEW"
    [ -s "$LOCK_NEW" ] || fail "upgrade: no packaging/requirements.lock.txt in the new tarball"
    OUTSIDE="$( "$TREE/venv/bin/python" "$SCRIPT_DIR/../lib/hive_cli/commands/venv_prune.py" --list --venv "$TREE/venv" --lock "$LOCK_NEW" 2>/dev/null )"
    [ -z "$OUTSIDE" ] || fail "upgrade: venv has names outside the $TAG exact set: $(echo "$OUTSIDE" | tr '\n' ' ')"
  else
    echo "    6/7 no venv in $TREE — the venv exact-set check is skipped"
  fi
  [ -z "$MISSING" ] || fail "upgrade: required entries missing from $TREE:$MISSING"
  receipt_keys_ok "$WORKDIR/prev/$PREV_RECEIPT" "$PREV_VERSION" "$FROM"
  if [ "$FAIL" = 0 ]; then
    ok "5-7/7 upgrade verified: the tree is exactly the $TAG entry set, the venv is in the exact set, and the $FROM receipt is valid"
  fi
fi

# ---- 8. the human summary ----------------------------------------------
COMMIT="$(grep '^commit: ' "$RECEIPT_NAME" | head -1 | awk '{print $2}' )"
RELEASE_URL="$(grep '^release: ' "$RECEIPT_NAME" | head -1 | cut -d' ' -f2)"
echo "    commit:  $COMMIT"
echo "    release: $RELEASE_URL"

if [ "$FAIL" = 0 ]; then
  echo "verify-release: $TAG OK — sidecar, receipt digests, and receipt keys all verified"
  if [ -n "$FROM" ]; then
    echo "verify-release: upgrade $FROM → $TAG OK — the tree is exactly the new entry set, the venv is in the new exact set, and the previous receipt is valid"
  fi
  exit 0
fi
echo "verify-release: $TAG FAILED — see the FAIL lines above" >&2
exit 1
