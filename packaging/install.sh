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

# The shared entry-set parser (S2): --copy-to stages the SAME set the
# build ships (packaging/entries.list) — never the whole tree.
. "$SOURCE_DIR/packaging/entries.sh"

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
  # T2 (0.15): a marked tree never holds runtime state. A staged install
  # writes the .hive-install marker, and the uninstall removes the WHOLE
  # tree on that marker — so staging into a dir that already holds
  # runtime state would make the teardown delete what the setup never
  # created (the install stages silently, the uninstall then removes the
  # project's live state as 'install residue'). Refuse fail-fast, naming
  # the collision. In-place installs are the operator's own tree — no
  # marker, no refusal, unchanged.
  #
  # A dir that carries ONLY the existing .hive-install marker is NOT a
  # collision: re-running the install over a marked tree is the upgrade
  # path (T1, 0.15) — the post-stage prune makes the tree exactly the
  # new release's entry set. (A marked dir that ALSO holds runtime state
  # is a pre-T2 cohabiting tree — the state refusal below still applies:
  # the upgrade prune must not be allowed to delete live state.)
  STAGE_REFUSAL=""
  for STATE_ENTRY in .company .worktrees; do
    if [ -e "$INSTALL_DIR/$STATE_ENTRY" ]; then
      STAGE_REFUSAL="$STAGE_REFUSAL  - $INSTALL_DIR/$STATE_ENTRY (runtime state)\n"
    fi
  done
  if [ -n "$STAGE_REFUSAL" ]; then
    echo "ERROR: refusing to stage into $INSTALL_DIR — it holds" >&2
    printf '%b' "$STAGE_REFUSAL" >&2
    echo "  A staged install is removed whole-tree by the uninstall" >&2
    echo "  (teardown mirrors setup, T3 0.14); staging here would make" >&2
    echo "  teardown delete the project's live state." >&2
    if [ -e "$INSTALL_DIR/.hive-install" ]; then
      echo "  The dir also carries an existing .hive-install marker:" >&2
      echo "  a pre-0.15 cohabiting tree. Move the state out (or" >&2
      echo "  'hive stop' first), then re-run the install — or pick a" >&2
      echo "  different install dir." >&2
    else
      echo "  Pick a different install dir (the default:" >&2
      echo "  $HOME/.local/share/hive)." >&2
    fi
    exit 1
  fi
  if [ -e "$INSTALL_DIR/.hive-install" ]; then
    echo "  Existing staged install detected ($INSTALL_DIR/.hive-install) —"
    echo "  this run is the UPGRADE: re-staging over it; the post-stage"
    echo "  prune (T1, 0.15) leaves exactly the new release's entry set."
  fi

  # S2: the whole-tree rsync is gone. Only the shared entry set
  # (packaging/entries.list — the same list build-package.sh stages) is
  # copied. Runtime state (.company/, .worktrees/), build artifacts
  # (dist/), and local junk are outside the set, so they can never
  # reach the install; unreadable or unexpected paths elsewhere in the
  # source are never touched, so they cannot abort the copy either
  # (the old rsync died with exit 23 under set -e on unreadable
  # unrelated paths — the 0.11.0 scratch-install validation hit it).
  echo "Staging the shared entry set (packaging/entries.list) → $INSTALL_DIR ..."
  mkdir -p "$INSTALL_DIR"
  MISSING=""
  while read -r SRC DEST; do
    if [ ! -e "$SOURCE_DIR/$SRC" ]; then
      MISSING="$MISSING $SRC"
      continue
    fi
    if [ -d "$SOURCE_DIR/$SRC" ]; then
      mkdir -p "$INSTALL_DIR/$DEST"
      if command -v rsync >/dev/null 2>&1; then
        rsync -a \
          --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
          --exclude='*.pyc.*' --exclude='*.pyo' \
          "$SOURCE_DIR/$SRC/" "$INSTALL_DIR/$DEST/"
      else
        cp -r --no-dereference "$SOURCE_DIR/$SRC/." "$INSTALL_DIR/$DEST/" 2>/dev/null || \
          cp -r "$SOURCE_DIR/$SRC/." "$INSTALL_DIR/$DEST/"
      fi
    else
      mkdir -p "$(dirname "$INSTALL_DIR/$DEST")"
      cp "$SOURCE_DIR/$SRC" "$INSTALL_DIR/$DEST"
    fi
  done < <(entry_pairs "$SOURCE_DIR/packaging/entries.list")
  if [ -n "$MISSING" ]; then
    echo "  NOTE: source entries missing (skipped):$MISSING" >&2
  fi
  # Post-stage verification: the runtime core must be present, else the
  # install is broken and the chmod/symlink steps below would fail
  # obscurely. Names each missing entry. T3 (0.13): the required set is
  # the shared constant (packaging/required-entries.list) — the same
  # list cut gate 4 and the doctor installed-set check use.
  while IFS= read -r REQUIRED; do
    [ -n "$REQUIRED" ] || continue
    if [ ! -e "$INSTALL_DIR/$REQUIRED" ]; then
      echo "  ERROR: $REQUIRED missing after staging — the install would" >&2
      echo "  be incomplete (source entry missing or unreadable)." >&2
      exit 1
    fi
  done < <(required_entries "$SOURCE_DIR/packaging/required-entries.list" REQUIRED)

  # T1 (0.15): the entry set is the EXACT source of truth for the
  # tree. A staged install re-run over an older release (the upgrade
  # path — the .hive-install branch above) must leave EXACTLY what the
  # new release defines: a top-level install-root entry not in the
  # entry set, outside {venv, .hive-install}, is install residue of a
  # superseded release (a removed tool, a renamed file, a deleted doc)
  # and is removed, each named. With T2's invariant (a marked tree
  # never holds runtime state — staging into a state dir is refused,
  # and the pre-T2 cohabiting case is refused too), no runtime-state
  # exclusions are needed in the prune: the state cannot be here.
  # venv and .hive-install are the installer's own lifecycle (reused
  # below, rewritten below) — never prune material. In-place installs
  # never reach this branch (the tree is the operator's work, T2).
  entry_in_set() {
    local name="$1" src dest
    while read -r src dest; do
      [ "$dest" = "$name" ] && return 0
    done < <(entry_pairs "$SOURCE_DIR/packaging/entries.list")
    return 1
  }
  PRUNED=""
  for ENTRY_PATH in "$INSTALL_DIR"/* "$INSTALL_DIR"/.[!.]*; do
    [ -e "$ENTRY_PATH" ] || continue
    ENTRY_NAME="$(basename "$ENTRY_PATH")"
    case "$ENTRY_NAME" in
      venv|.hive-install) continue ;;
    esac
    if ! entry_in_set "$ENTRY_NAME"; then
      rm -rf -- "$ENTRY_PATH"
      PRUNED="$PRUNED  - $ENTRY_NAME\n"
    fi
  done
  if [ -n "$PRUNED" ]; then
    echo "  Pruned install residue (not in this release's entry set):"
    printf '%b' "$PRUNED"
  fi

  # T3 (0.14): the install marker. This dir is a STAGED COPY made by
  # this install — teardown mirrors setup: uninstall.sh removes the
  # install dir itself ONLY on this marker (an uninstall removes what
  # an install created). In-place installs (a git checkout, the
  # operator's own tree) write no marker: the tree is the operator's
  # work, and removing it would destroy it. The marker is a root FILE
  # written at install time — not part of the entry set, never in the
  # tarball, and neutral to the doctor installed-set check (pinned by
  # a regression test).
  {
    echo "version=$(tr -d '[:space:]' < "$SOURCE_DIR/VERSION" 2>/dev/null || echo unknown)"
    echo "installed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "mode=copy-to"
  } > "$INSTALL_DIR/.hive-install"
  echo "  Install marker written: $INSTALL_DIR/.hive-install"
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

# Install/update the Python environment. T1 (0.14): when the release
# ships the venv lock (packaging/requirements.lock.txt — exact pins, the
# reproducible artifact), install FROM IT: the venv resolves to the
# versions the release locked, and hive doctor's "venv lock" check can
# verify them. Pre-lock releases (pre-0.14) carry no lock: fall back to
# the floor pins (requirements.txt) with a note.
REQS_FILE="$INSTALL_DIR/packaging/requirements.txt"
LOCK_FILE="$INSTALL_DIR/packaging/requirements.lock.txt"
if [ -f "$LOCK_FILE" ]; then
  DEP_FILE="$LOCK_FILE"
  echo "  Installing the locked Python environment (packaging/requirements.lock.txt)"
else
  DEP_FILE="$REQS_FILE"
  echo "  NOTE: no venv lock in this release — installing from floor pins (requirements.txt)"
fi
if [ -x "$VENV_DIR/bin/pip" ]; then
  "$VENV_DIR/bin/pip" install --quiet --upgrade pip 2>/dev/null || true
  "$VENV_DIR/bin/pip" install --quiet -r "$DEP_FILE"
else
  "$VENV_DIR/bin/python" -m ensurepip --quiet 2>/dev/null || true
  "$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip 2>/dev/null || true
  "$VENV_DIR/bin/python" -m pip install --quiet -r "$DEP_FILE"
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
# A2: the location is steered by HIVE_CONFIG — the one spelling doctor
# and release-publish-runner.sh already read (0.7.1 introduced the second
# spelling, HIVE_USER_CONFIG, for this script; it keeps working as a
# deprecated alias for one release and loses when both are set).
USER_CONFIG="${HIVE_CONFIG:-${HIVE_USER_CONFIG:-$HOME/.config/hive/config.toml}}"
if [ -z "${HIVE_CONFIG:-}" ] && [ -n "${HIVE_USER_CONFIG:-}" ]; then
  echo "  NOTE: HIVE_USER_CONFIG is deprecated — use HIVE_CONFIG (same file)."
fi
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
echo "Hive installed (no agent settings written — the built-in harness is the agent runtime)."
echo ""
echo "Next steps:"
echo "  1. Edit [llm] in $USER_CONFIG — pick provider + model (hive config edit)"
echo "  2. hive doctor"
echo "  3. hive init <project-dir> --brief 'description'"

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
