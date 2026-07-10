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
  cp -r "$SOURCE_DIR"/. "$INSTALL_DIR"/
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

# 3a. Patch ~/.claude/settings.json to wire hive's PreToolUse hooks.
#     - block-company-writes.sh:    forbids Write/Edit/NotebookEdit on
#                                   .company/ AND forbids destructive
#                                   Bash commands targeting .company
#                                   (rm/mv/ln/cp/chmod/redirect/sed -i/...).
#                                   THE BASH MATCHER IS CRITICAL — without
#                                   it the agent's `ln -s ... .company`
#                                   self-symlink crash isn't blocked.
#     - block-admin-code-writes.sh: forbids admin depts from writing
#                                   git-tracked files.
#
# Matcher must include Bash so the bash branch of block-company-writes.sh
# actually fires. (Pre-0.4.47 the matcher was Write|Edit only — the script
# correctly handled Bash but Claude never invoked it for Bash calls.)
SETTINGS_FILE="${HOME}/.claude/settings.json"
HOOK_MATCHER="Write|Edit|NotebookEdit|Bash"
HOOKS_TO_INSTALL=(
  "bash $INSTALL_DIR/lib/core/block-company-writes.sh"
  "bash $INSTALL_DIR/lib/core/block-admin-code-writes.sh"
)
if command -v jq >/dev/null 2>&1; then
  mkdir -p "$(dirname "$SETTINGS_FILE")"
  [ -f "$SETTINGS_FILE" ] || echo '{}' > "$SETTINGS_FILE"

  # Migration: an older install registered these hooks under matcher
  # "Write|Edit" — REMOVE the old registration before adding the new one
  # under the broader "Write|Edit|NotebookEdit|Bash" matcher. Without this
  # migration, existing installs would silently keep the narrow matcher
  # and Bash mutations of .company would still bypass the hook.
  for hookcmd in "${HOOKS_TO_INSTALL[@]}"; do
    tmp="$SETTINGS_FILE.tmp.$$"
    jq --arg c "$hookcmd" '
      .hooks //= {} |
      .hooks.PreToolUse = (.hooks.PreToolUse // [] | map(
        .hooks |= ((. // []) | map(select(.command != $c))) |
        select((.hooks // []) | length > 0)
      ))
    ' "$SETTINGS_FILE" > "$tmp" && mv "$tmp" "$SETTINGS_FILE"
  done

  for hookcmd in "${HOOKS_TO_INSTALL[@]}"; do
    tmp="$SETTINGS_FILE.tmp.$$"
    jq --arg c "$hookcmd" --arg m "$HOOK_MATCHER" '
      .hooks //= {} |
      .hooks.PreToolUse //= [] |
      .hooks.PreToolUse |=
        if (map(.matcher) | index($m)) == null then
          . + [{matcher: $m, hooks: [{type: "command", command: $c, timeout: 5}]}]
        else
          map(if .matcher == $m
              then .hooks += [{type: "command", command: $c, timeout: 5}]
              else . end)
        end
    ' "$SETTINGS_FILE" > "$tmp" && mv "$tmp" "$SETTINGS_FILE"
    echo "  Installed PreToolUse hook ($HOOK_MATCHER): $(basename "${hookcmd##* }")"
  done

  # Verification: confirm at least one of our hooks is now registered under
  # a matcher that includes Bash. If not, the install silently failed.
  if ! jq -e --arg n "block-company-writes.sh" '
    (.hooks.PreToolUse // []) |
    map(select((.matcher // "") | test("Bash"))) |
    map(.hooks // []) | flatten | map(.command) |
    map(test($n)) | any
  ' "$SETTINGS_FILE" >/dev/null 2>&1; then
    echo ""
    echo "  WARNING: hook verification failed. block-company-writes.sh is NOT registered" >&2
    echo "           under a matcher including Bash. Agents will still be able to do" >&2
    echo "           rm/mv/ln on .company. Inspect ~/.claude/settings.json manually." >&2
  fi
else
  echo ""
  echo "  WARNING: jq not found; skipping ~/.claude/settings.json hook setup." >&2
  echo "  Manually add these PreToolUse hooks to ~/.claude/settings.json with" >&2
  echo "  matcher=\"$HOOK_MATCHER\":" >&2
  for hookcmd in "${HOOKS_TO_INSTALL[@]}"; do
    echo "    command=\"$hookcmd\"" >&2
  done
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
# upgrading mid-sprint kills the sprint-lifecycle and any in-flight Claude
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
  echo "  Put per-machine claude / model endpoint config there so daemons inherit"
  echo "  it without depending on your interactive shell rc files. Example:"
  echo "      mkdir -p \"\$(dirname '$HIVE_ENV_FILE_HINT')\""
  echo "      cat > '$HIVE_ENV_FILE_HINT' <<'ENVEOF'"
  echo "      export ANTHROPIC_BASE_URL=\"http://localhost:8080\""
  echo "      export ANTHROPIC_API_KEY='sk-no-key-required'"
  echo "      export LLM_TINY_URL=http://localhost:8081/v1/chat/completions"
  echo "      ENVEOF"
fi
