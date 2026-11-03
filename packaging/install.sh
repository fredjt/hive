#!/usr/bin/env bash
set -euo pipefail

# Hive installer — sets up venv, installs Python deps, symlinks `hive` onto PATH.
#
# Idempotent: re-running upgrades the venv and re-creates the symlink. Does
# NOT touch project-local .company/ workspaces.
#
# By default the source tree (where this script lives) is treated as the install
# location; pass --copy-to <dir> to first copy the tree elsewhere and install there.

SOURCE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR="$SOURCE_DIR"
BIN_LINK="${HOME}/.local/bin/hive"

while [ $# -gt 0 ]; do
  case "$1" in
    --copy-to)   INSTALL_DIR="$2"; shift 2 ;;
    --bin-link)  BIN_LINK="$2"; shift 2 ;;
    -h|--help)
      cat <<EOF
Usage: install.sh [--copy-to <dir>] [--bin-link <path>]

  --copy-to <dir>   Copy this tree to <dir> first, then install there.
                    Default: install in place at $SOURCE_DIR.
  --bin-link <path> Where to symlink the hive launcher.
                    Default: $BIN_LINK
EOF
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [ "$INSTALL_DIR" != "$SOURCE_DIR" ]; then
  echo "Copying $SOURCE_DIR → $INSTALL_DIR ..."
  mkdir -p "$INSTALL_DIR"
  # Use rsync if available (preserves permissions, excludes .git), otherwise cp
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' "$SOURCE_DIR/" "$INSTALL_DIR/"
  else
    cp -r --no-dereference "$SOURCE_DIR"/. "$INSTALL_DIR/" 2>/dev/null || \
      cp -r "$SOURCE_DIR"/. "$INSTALL_DIR/" 2>/dev/null || true
    # Remove .git if it was copied (not needed for installation)
    rm -rf "$INSTALL_DIR/.git"
  fi
fi

echo "Installing Hive at $INSTALL_DIR"

# 1. Python venv via uv (preferred) or stdlib venv
VENV_DIR="$INSTALL_DIR/venv"
if [ -d "$VENV_DIR" ]; then
  echo "  Reusing existing venv at $VENV_DIR (re-installing requirements)"
elif command -v uv >/dev/null 2>&1; then
  echo "  Creating venv with uv..."
  uv venv --seed --quiet "$VENV_DIR"
elif python3 -c 'import venv' 2>/dev/null; then
  echo "  Creating venv with python3 -m venv..."
  python3 -m venv "$VENV_DIR"
else
  echo "  ERROR: need either 'uv' or python3 with the 'venv' module." >&2
  echo "  On Debian/Ubuntu: sudo apt install python3-venv" >&2
  echo "  Or: snap install --classic uv" >&2
  exit 1
fi

# Install/update requirements
if [ -x "$VENV_DIR/bin/pip" ]; then
  "$VENV_DIR/bin/pip" install --quiet --upgrade pip 2>/dev/null || true
  "$VENV_DIR/bin/pip" install --quiet -r "$INSTALL_DIR/packaging/requirements.txt"
else
  "$VENV_DIR/bin/python" -m ensurepip --quiet 2>/dev/null || true
  "$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip 2>/dev/null || true
  "$VENV_DIR/bin/python" -m pip install --quiet -r "$INSTALL_DIR/packaging/requirements.txt"
fi

# 2. Make scripts executable
chmod +x "$INSTALL_DIR/bin/hive"
chmod +x "$INSTALL_DIR/lib/core/"*.sh
chmod +x "$INSTALL_DIR/lib/tools/"*.sh

# 3. Symlink launcher onto PATH
mkdir -p "$(dirname "$BIN_LINK")"
ln -sf "$INSTALL_DIR/bin/hive" "$BIN_LINK"
echo "  Symlinked $BIN_LINK → $INSTALL_DIR/bin/hive"

# 3a. (0.6.0) Agent settings are no longer touched on install. The agent
#     runtime is the built-in harness (lib/core/agent-harness.py); nothing
#     in this tree registers PreToolUse hooks anymore. Installs prior to
#     0.6.0 may have left hook entries in the operator's agent settings
#     file — uninstall.sh's one-time legacy cleanup removes them.

# 3b. Per-machine user config — scaffold from the tracked template so the
#     config is VISIBLE from install time. hive doctor points the operator
#     at this file for [llm]; a stranger must not have to invent a file
#     that no documentation shows. Never overwrite an existing file — it
#     may hold operator edits.
USER_CONFIG="${HIVE_USER_CONFIG:-$HOME/.config/hive/config.toml}"
if [ -f "$USER_CONFIG" ]; then
  echo "  User config: $USER_CONFIG (kept as-is)"
else
  mkdir -p "$(dirname "$USER_CONFIG")"
  cp "$INSTALL_DIR/etc/config.template.toml" "$USER_CONFIG"
  chmod 600 "$USER_CONFIG"
  echo "  Wrote $USER_CONFIG (edit the [llm] section, then run hive doctor)"
fi

# 4. PATH check
case ":$PATH:" in
  *":$(dirname "$BIN_LINK"):"*) ;;
  *)
    echo ""
    echo "  NOTE: $(dirname "$BIN_LINK") is not on your PATH."
    echo "  Add to ~/.bashrc or ~/.zshrc:"
    echo "      export PATH=\"$(dirname "$BIN_LINK"):\$PATH\""
    ;;
esac


# Detect (but DO NOT kill) running daemons that may need restart for the
# new code to take effect. The installer is non-destructive: stopping
# anything is the operator's job via `hive stop`. Without this rule,
# upgrading mid-sprint kills the sprint-lifecycle and any in-flight agents
# turn, which is surprising and destructive — operators expect the
# installer to update files on disk only.
#
# What needs a restart for changes to take effect:
#   - hive_mcp server: tool registry frozen at process start. New MCP
#     tools / new tool args won't appear to spawned agents until restart.
#   - sprint-lifecycle: new sprint-fill / round-promote / ticket-move
#     logic loads on next iteration; long-running daemons keep running
#     the old logic until they restart.
#   - Background daemons (worktree-merger, verification-gates, etc.):
#     each spawns a fresh subshell per iteration, so they pick up new
#     script logic on the next tick. No restart needed for those.
#
# Print a one-line hint listing which long-lived processes are still
# running with the OLD code, so the operator can choose `hive stop &&
# hive start` (full reset) or just leave it (sprint completes on old
# code, next sprint uses new code).
NEEDS_RESTART=()
if pgrep -f 'hive_mcp/server\.py.*--transport http' >/dev/null 2>&1 \
   || pgrep -f 'hive_mcp\.server.*--transport http' >/dev/null 2>&1; then
  NEEDS_RESTART+=("MCP server (new tool defs won't appear in agent prompts)")
fi
if pgrep -f 'lib/tools/sprint-lifecycle\.sh' >/dev/null 2>&1; then
  NEEDS_RESTART+=("sprint-lifecycle (new ticket-move / round-promote logic loads on restart)")
fi
if [ "${#NEEDS_RESTART[@]}" -gt 0 ]; then
  echo ""
  echo "  Note: hive is currently running. The new code is now on disk but the"
  echo "  following long-lived processes are still running the OLD code:"
  for item in "${NEEDS_RESTART[@]}"; do echo "    - $item"; done
  echo "  Run 'hive stop && hive start' to apply the update (will interrupt any"
  echo "  in-flight agent turn). Otherwise the old daemons keep running until"
  echo "  the operator restarts them."
fi

echo ""
echo "Hive installed (no agent settings written — the built-in harness is the agent runtime). Try:"
echo "  hive doctor"
echo "  hive version"
echo "  hive init <project-dir> --brief 'description'"
echo "  hive             # launches the dashboard"

# 5. Env-file hint — daemons started via nohup/systemd don't read .zshrc/.bashrc,
#    so shell-level per-machine env belongs in this file. LLM settings do NOT
#    belong here — they live in $USER_CONFIG ([llm] section, scaffolded in 3b);
#    env.sh is for PATH additions and GITHUB_TOKEN (publisher auth when you
#    don't use `gh auth login`).
HIVE_ENV_FILE_HINT="${HIVE_ENV_FILE:-$HOME/.config/hive/env.sh}"
if [ ! -f "$HIVE_ENV_FILE_HINT" ]; then
  echo ""
  echo "  TIP: hive sources $HIVE_ENV_FILE_HINT on every spawn (if it exists)."
  echo "  Use it for shell-level daemon env only — LLM settings go in"
  echo "  $USER_CONFIG ([llm] section). Example:"
  echo "      mkdir -p \"\$(dirname '$HIVE_ENV_FILE_HINT')\""
  echo "      cat > '$HIVE_ENV_FILE_HINT' <<'ENVEOF'"
  echo "      export PATH=\"\$HOME/.npm-global/bin:\$HOME/.local/bin:\$PATH\""
  echo "      # export GITHUB_TOKEN=...   # only if the publisher shouldn't use gh auth"
  echo "      ENVEOF"
fi
