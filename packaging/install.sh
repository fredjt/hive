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

# 3a. Register the PreToolUse hooks in ~/.claude/settings.json so agents
#     cannot clobber .company/ or (for admin depts) write application source
#     code directly. The agent prompts describe these guardrails as live
#     protection, so the install must actually wire them up — see
#     lib/core/CLAUDE.md. Sourced from hooks.sh so uninstall.sh shares the
#     exact matching/migration logic in reverse.
. "$(cd "$(dirname "$0")" && pwd)/hooks.sh"
hive_hooks_register "$INSTALL_DIR"

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
echo "Hive installed. Try:"
echo "  hive doctor"
echo "  hive version"
echo "  hive init <project-dir> --brief 'description'"
echo "  hive             # launches the dashboard"

# 5. Env-file hint — daemons started via nohup/systemd don't read .zshrc/.bashrc,
#    so endpoint config (ANTHROPIC_BASE_URL etc.) belongs in this file.
HIVE_ENV_FILE_HINT="${HIVE_ENV_FILE:-$HOME/.config/hive/env.sh}"
if [ ! -f "$HIVE_ENV_FILE_HINT" ]; then
  echo ""
  echo "  TIP: hive sources $HIVE_ENV_FILE_HINT on every spawn (if it exists)."
  echo "  Put per-machine LLM / model endpoint config there so daemons inherit"
  echo "  it without depending on your interactive shell rc files. Example:"
  echo "      mkdir -p \"\$(dirname '$HIVE_ENV_FILE_HINT')\""
  echo "      cat > '$HIVE_ENV_FILE_HINT' <<'ENVEOF'"
  echo "      export ANTHROPIC_BASE_URL=\"http://localhost:8080\""
  echo "      export ANTHROPIC_API_KEY='sk-no-key-required'"
  echo "      export LLM_TINY_URL=http://localhost:8081/v1/chat/completions"
  echo "      ENVEOF"
fi
