#!/usr/bin/env sh
# Hive one-line installer.
#
# Usage (this repo's release; no env needed):
#   curl -fsSL https://raw.githubusercontent.com/fredjt/hive/main/packaging/install-online.sh | sh
#
# Overrides:
#   curl -fsSL <url-to-this-file> | HIVE_PKG_URL=https://... sh
#   curl -fsSL <url-to-this-file> | HIVE_PKG_FILE=/path/to/hive-X.Y.Z.tar.gz sh
#   curl -fsSL <url-to-this-file> | HIVE_REPO=owner/repo sh
#   curl -fsSL <url-to-this-file> | HIVE_INSTALL_DIR=$HOME/opt/hive sh
#
# The tarball source resolves in this order:
#   1. HIVE_PKG_URL   explicit tarball URL
#   2. HIVE_PKG_FILE  a local tarball (no download)
#   3. default: this repo's GitHub release —
#      https://github.com/$HIVE_REPO/releases/download/v$VERSION/hive-$VERSION.tar.gz
#      where VERSION is fetched from the raw branch (HIVE_BRANCH,
#      default main) and HIVE_REPO defaults to fredjt/hive. The
#      sha256 sidecar next to the tarball is verified when present.
#
# Env vars:
#   HIVE_PKG_URL      Explicit tarball URL (wins over everything)
#   HIVE_PKG_FILE     Path to a local tarball (alternative to URL)
#   HIVE_REPO         owner/repo for the default GitHub release URL
#                     (default: fredjt/hive)
#   HIVE_BRANCH       Branch to fetch VERSION from (default: main)
#   HIVE_INSTALL_DIR  Where to install (default: $HOME/.local/share/hive)
#   HIVE_PRINT_URL    If set: resolve the source, print it, and exit —
#                     no download, no install (the hermetic test seam)
#
# Pure POSIX sh - no bashisms. Works on minimal containers.

set -eu

INSTALL_DIR="${HIVE_INSTALL_DIR:-$HOME/.local/share/hive}"
PKG_URL="${HIVE_PKG_URL:-}"
PKG_FILE="${HIVE_PKG_FILE:-}"
REPO="${HIVE_REPO:-fredjt/hive}"
BRANCH="${HIVE_BRANCH:-main}"

err() { echo "ERROR: $*" >&2; exit 1; }
log() { echo "[hive-install] $*"; }

fetch() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$@"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- "$@"
  else
    return 1
  fi
}

# Resolve the package source. No env set → this repo's GitHub release:
# VERSION comes from the raw branch, the tarball from the release assets.
if [ -z "$PKG_URL" ] && [ -z "$PKG_FILE" ]; then
  VERSION_RAW="$(fetch "https://raw.githubusercontent.com/$REPO/$BRANCH/VERSION")" ||
    err "Cannot fetch VERSION from https://raw.githubusercontent.com/$REPO/$BRANCH/VERSION — set HIVE_PKG_URL or HIVE_PKG_FILE."
  VERSION="$(printf '%s' "$VERSION_RAW" | tr -d '[:space:]')"
  if ! printf '%s' "$VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
    err "VERSION '$VERSION' from the raw branch is not X.Y.Z — set HIVE_PKG_URL."
  fi
  PKG_URL="https://github.com/$REPO/releases/download/v$VERSION/hive-$VERSION.tar.gz"
fi

# Hermetic seam: HIVE_PRINT_URL resolves and prints, then stops — no
# download, no tools required, no install.
if [ -n "${HIVE_PRINT_URL:-}" ]; then
  if [ -n "$PKG_URL" ]; then
    printf '%s\n' "$PKG_URL"
  else
    printf 'file:%s\n' "$PKG_FILE"
  fi
  exit 0
fi

if ! command -v tar >/dev/null 2>&1; then
  err "tar is required."
fi
if ! command -v python3 >/dev/null 2>&1; then
  err "python3 is required."
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT INT TERM

if [ -n "$PKG_FILE" ]; then
  log "Using local package: $PKG_FILE"
  cp "$PKG_FILE" "$TMP/hive.tar.gz"
else
  log "Downloading $PKG_URL ..."
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL -o "$TMP/hive.tar.gz" "$PKG_URL"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO "$TMP/hive.tar.gz" "$PKG_URL"
  else
    err "Need curl or wget to download."
  fi
fi

# Verify checksum if a sidecar .sha256 is provided next to the tarball
if [ -n "$PKG_URL" ]; then
  SUM_URL="${PKG_URL%.tar.gz}.sha256"
  if command -v curl >/dev/null 2>&1 && curl -fsSL -o "$TMP/sum" "$SUM_URL" 2>/dev/null; then
    log "Verifying checksum..."
    EXPECTED="$(awk '{print $1}' "$TMP/sum")"
    ACTUAL="$(sha256sum "$TMP/hive.tar.gz" | awk '{print $1}')"
    if [ "$EXPECTED" != "$ACTUAL" ]; then
      err "Checksum mismatch: expected $EXPECTED, got $ACTUAL"
    fi
    log "Checksum OK."
  fi
fi

log "Extracting..."
tar -xzf "$TMP/hive.tar.gz" -C "$TMP"
EXTRACTED="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -1)"
[ -d "$EXTRACTED" ] || err "Extraction failed (no top-level directory)"

log "Installing to $INSTALL_DIR ..."
# T2 (0.15): install.sh is a BASH script (the staging loops use process
# substitution); on systems where /bin/sh is dash (Debian, Ubuntu, the
# CI runners), `sh install.sh` dies with a syntax error before it can
# refuse or stage. Delegate with bash — the documented manual path.
if ! command -v bash >/dev/null 2>&1; then
  echo "ERROR: bash is required (install.sh is a bash script)." >&2
  exit 1
fi
bash "$EXTRACTED/packaging/install.sh" --copy-to "$INSTALL_DIR"

log ""
log "Hive installed. Try:"
log "  hive doctor"
log "  hive version"
log "  hive --help"
