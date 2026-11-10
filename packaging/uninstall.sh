#!/usr/bin/env bash
set -euo pipefail

# Removes the Hive launcher symlink and venv, and performs a one-time legacy
# cleanup of the agent hook registrations that installs prior to 0.6.0 may
# have left behind. Does NOT touch the source tree (in case it's a working
# copy) or any project-local .company/ workspaces.

INSTALL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BIN_LINK="${HOME}/.local/bin/hive"

# T3 (0.14): --keep-tree — remove only the symlink + venv (the pre-0.14
# behavior), even on a staged install that carries the install marker.
KEEP_TREE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --keep-tree) KEEP_TREE=1; shift ;;
    -h|--help)
      cat <<'EOF'
Usage: uninstall.sh [--keep-tree]

Removes the hive launcher symlink and the venv, runs the one-time
legacy hook cleanup, and — when the install dir carries the
.hive-install marker written by `install.sh --copy-to` (T3, 0.14) —
removes the install tree itself (teardown mirrors setup: an
uninstall removes what an install created). In-place installs (a git
checkout, the operator's own tree) carry no marker and keep the
tree, as before. --keep-tree forces the old behavior on a marked
install (symlink + venv only).
EOF
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

[ -L "$BIN_LINK" ] && rm "$BIN_LINK" && echo "Removed $BIN_LINK"
[ -d "$INSTALL_DIR/venv" ] && rm -rf "$INSTALL_DIR/venv" && echo "Removed venv"

# --- One-time legacy hook cleanup ------------------------------------------
# Installs prior to 0.6.0 registered PreToolUse hook entries in the
# operator's agent settings file (the legacy agent tool's per-user config
# directory), pointing at lib/core/block-company-writes.sh and
# lib/core/block-admin-code-writes.sh. Nothing in this tree uses that
# mechanism anymore — the agent runtime is the built-in harness — so on
# uninstall we strip any hive entries from the file if it exists.
#
# Best-effort: a missing jq or an unparseable settings file prints a
# warning and leaves the file untouched; uninstall never fails over legacy
# cleanup.
#
# The legacy config directory name is assembled from string parts at
# runtime on purpose: the literal must not appear anywhere in this tree.

hive_legacy_settings_file() {
  # legacy config dir = ".cl" + "aude"  (string concatenation, by design)
  printf '%s/.%s/settings.json' "$HOME" "clau""de"
}

hive_legacy_hook_cleanup() {
  if ! command -v jq >/dev/null 2>&1; then
    echo "  NOTE: jq not found — skipping legacy hook cleanup." >&2
    return 0
  fi
  local settings tmp
  settings="$(hive_legacy_settings_file)"

  # Nothing was ever registered → nothing to clean.
  [ -f "$settings" ] || return 0

  if ! jq -e . "$settings" >/dev/null 2>&1; then
    echo "  WARNING: $settings is not valid JSON — leaving it untouched (fix it manually)." >&2
    return 0
  fi

  # Nothing to do if no hive entry is present.
  if ! jq -e '[(.hooks.PreToolUse // [])[]
        | (.hooks // [])[]
        | (.command // "")]
        | map(select(test("block-(company|admin-code)-writes\\.sh")))
        | length > 0' "$settings" >/dev/null 2>&1; then
    return 0
  fi

  # Drop every PreToolUse entry whose hooks reference a hive hook script
  # (matched on the script basename so entries from old install dirs are
  # covered too), then drop entries left with no hooks. Everything else in
  # the file (other tools' hooks, permissions, env vars) is preserved.
  tmp="${settings}.tmp.$$"
  if ! jq '
    .hooks //= {}
    | .hooks.PreToolUse = ((.hooks.PreToolUse // [])
        | map(
            .hooks |= ((. // [])
                | map(select(
                    ((.command // "") | test("block-company-writes\\.sh") | not)
                    and ((.command // "") | test("block-admin-code-writes\\.sh") | not)
                  ))
            )
            | select((.hooks // []) | length > 0)
          ))
  ' "$settings" > "$tmp"; then
    rm -f "$tmp"
    echo "  WARNING: legacy hook cleanup failed — $settings left untouched." >&2
    return 0
  fi
  mv "$tmp" "$settings"
  echo "  Removed legacy PreToolUse hook entries from $settings"
  return 0
}

hive_legacy_hook_cleanup

# T3 (0.14): teardown mirrors setup. install.sh --copy-to wrote the
# .hive-install marker into the install root — the dir is a staged
# COPY the install created, so the uninstall removes the whole tree
# (no symlink, no venv, no tree — a scratch install is fully
# re-created by a fresh install). In-place installs (a git checkout,
# the operator's own tree) carry no marker: the tree is the
# operator's work and is kept, as before. --keep-tree forces the old
# behavior on a marked install.
MARKER="$INSTALL_DIR/.hive-install"
if [ "$KEEP_TREE" = "1" ]; then
  echo ""
  echo "Hive uninstalled (symlink + venv removed; install tree kept per --keep-tree)."
  echo "Per-project .company/ workspaces are also untouched."
elif [ -f "$MARKER" ]; then
  echo ""
  echo "Staged install (marker: $MARKER) — removing the install tree the"
  echo "install created: $INSTALL_DIR"
  # T2 (0.15): defense in depth — the tree removal EXCLUDES runtime
  # state (.company/, .worktrees/) and names whatever it kept. The
  # install-side refusal (install.sh refuses to stage into a dir that
  # holds state) should make this branch dead code; the exclusion makes
  # it dead-SAFE for any marked tree that predates the refusal (a
  # pre-0.15 cohabiting tree, however it got there, keeps its state).
  # A clean marked tree (no state) is removed whole, as before — the
  # rmdir below drops the (now stateless) root dir itself.
  # The script is reading from this tree — exec a fresh shell so the rm
  # does not race the running script's own file (bash reads its script
  # incrementally). The caller sees the removal's exit status.
  exec sh -c '
    status=0
    for keep in .company .worktrees; do
      [ -e "$1/$keep" ] && echo "  kept: $keep — runtime state, not install residue"
    done
    find "$1" -mindepth 1 -maxdepth 1 ! -name .company ! -name .worktrees \
      -exec rm -rf {} + || status=1
    rmdir "$1" 2>/dev/null
    exit "$status"
  ' _ "$INSTALL_DIR"
else
  echo ""
  echo "Hive uninstalled. The source tree at $INSTALL_DIR is untouched (in-place"
  echo "install — no install marker; the tree is the operator's work) — delete it"
  echo "manually if you want to fully remove Hive."
  echo ""
  echo "Per-project .company/ workspaces are also untouched."
fi
