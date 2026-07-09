#!/usr/bin/env sh
# Hive one-line installer.
#
# Usage:
#   curl -fsSL <url-to-this-file> | sh
#   curl -fsSL <url-to-this-file> | HIVE_PKG_URL=https://... sh
#   curl -fsSL <url-to-this-file> | HIVE_INSTALL_DIR=$HOME/opt/hive sh
#
# Env vars:
#   HIVE_PKG_URL      Where to download the tarball (required if no PKG_FILE)
#   HIVE_PKG_FILE     Path to a local tarball (alternative to URL)
#   HIVE_INSTALL_DIR  Where to install (default: $HOME/.local/share/hive)
#   HIVE_VERSION      Expected version (default: read from tarball name)
#
# Pure POSIX sh - no bashisms. Works on minimal containers.

set -eu

INSTALL_DIR="${HIVE_INSTALL_DIR:-$HOME/.local/share/hive}"
PKG_URL="${HIVE_PKG_URL:-}"
PKG_FILE="${HIVE_PKG_FILE:-}"

err() { echo "ERROR: $*" >&2; exit 1; }
log() { echo "[hive-install] $*"; }

if [ -z "$PKG_URL" ] && [ -z "$PKG_FILE" ]; then
  err "Set HIVE_PKG_URL or HIVE_PKG_FILE. See header of this script."
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
sh "$EXTRACTED/packaging/install.sh" --copy-to "$INSTALL_DIR"

log ""
log "Hive installed. Try:"
log "  hive doctor"
log "  hive version"
log "  hive --help"
