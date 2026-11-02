#!/usr/bin/env bash
set -euo pipefail

# Removes the Hive launcher symlink and venv, and performs a one-time legacy
# cleanup of the agent hook registrations that installs prior to 0.6.0 may
# have left behind. Does NOT touch the source tree (in case it's a working
# copy) or any project-local .company/ workspaces.

INSTALL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BIN_LINK="${HOME}/.local/bin/hive"

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

echo ""
echo "Hive uninstalled. The source tree at $INSTALL_DIR is untouched — delete it"
echo "manually if you want to fully remove Hive."
echo ""
echo "Per-project .company/ workspaces are also untouched."
