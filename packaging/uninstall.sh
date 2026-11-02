#!/usr/bin/env bash
set -euo pipefail

# Removes the Hive launcher symlink and venv, and unregisters the PreToolUse
# hooks that install.sh wrote to ~/.claude/settings.json. Does NOT touch the
# source tree (in case it's a working copy) or any project-local .company/
# workspaces.

INSTALL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BIN_LINK="${HOME}/.local/bin/hive"

[ -L "$BIN_LINK" ] && rm "$BIN_LINK" && echo "Removed $BIN_LINK"
[ -d "$INSTALL_DIR/venv" ] && rm -rf "$INSTALL_DIR/venv" && echo "Removed venv"

. "$(cd "$(dirname "$0")" && pwd)/hooks.sh"
hive_hooks_unregister

echo ""
echo "Hive uninstalled. The source tree at $INSTALL_DIR is untouched — delete it"
echo "manually if you want to fully remove Hive."
echo ""
echo "Per-project .company/ workspaces are also untouched."
