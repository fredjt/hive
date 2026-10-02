#!/usr/bin/env bash
set -euo pipefail

# scaffold-extract.sh — copy the scaffold (agents, tools, templates, core,
# config defaults) from the global hive install into <project>/.hive/.
# All files are set read-only after extraction to prevent agents from
# accidentally corrupting them during runtime. Use --force + re-run if you
# need to update the scaffold (requires owner write permission first).
#
# Layout produced (matches the global install's lib/ structure verbatim so
# scripts find each other via the existing walk-up convention — no script
# changes required):
#
#   <project>/.hive/
#     lib/
#       agents/
#       tools/
#       templates/
#       core/   (claude-spawn.sh, init.sh, orchestrator.sh only —
#                block-*.sh hooks stay in the global install)
#     config.toml      (was etc/config.defaults.toml)
#     VERSION
#     .manifest        (JSON: {"path": "sha256", ...})
#
# Usage:
#   scaffold-extract.sh <project-root> [--force] [--install-dir <path>]

PROJECT_ROOT=""
FORCE=0
INSTALL_DIR_OVERRIDE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --force)        FORCE=1; shift ;;
    --install-dir)  INSTALL_DIR_OVERRIDE="$2"; shift 2 ;;
    -h|--help)
      sed -n '3,22p' "$0"; exit 0 ;;
    -*)
      echo "scaffold-extract: unknown flag: $1" >&2; exit 2 ;;
    *)
      if [ -z "$PROJECT_ROOT" ]; then
        PROJECT_ROOT="$1"
      else
        echo "scaffold-extract: unexpected positional arg: $1" >&2; exit 2
      fi
      shift ;;
  esac
done

if [ -z "$PROJECT_ROOT" ]; then
  echo "scaffold-extract: project root required" >&2
  exit 2
fi

PROJECT_ROOT="$(cd "$PROJECT_ROOT" && pwd)"

# Resolve install dir (where this script lives, two levels up: lib/tools/scaffold-extract.sh → install root)
if [ -n "$INSTALL_DIR_OVERRIDE" ]; then
  INSTALL_DIR="$(cd "$INSTALL_DIR_OVERRIDE" && pwd)"
else
  INSTALL_DIR="${HIVE_INSTALL_DIR:-$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)}"
fi

[ -d "$INSTALL_DIR/lib" ] || { echo "scaffold-extract: install dir invalid: $INSTALL_DIR" >&2; exit 1; }

SCAFFOLD="$PROJECT_ROOT/.hive"

if [ -d "$SCAFFOLD" ] && [ "$FORCE" != "1" ]; then
  echo "scaffold-extract: $SCAFFOLD already exists. Use --force to overwrite." >&2
  exit 1
fi

mkdir -p "$SCAFFOLD/lib"

# Sources to copy (relative to install dir → relative to .hive/).
copy_tree() {
  local src="$1" dest="$2"
  if [ -d "$INSTALL_DIR/$src" ]; then
    mkdir -p "$SCAFFOLD/$dest"
    # Copy preserving mode/timestamps; -T-equivalent merge by walking the src.
    cp -aT "$INSTALL_DIR/$src" "$SCAFFOLD/$dest"
  fi
}

copy_tree "lib/agents"    "lib/agents"
copy_tree "lib/tools"     "lib/tools"
copy_tree "lib/templates" "lib/templates"

# Core: only the three runtime scripts move into .hive/ — the block-*.sh
# hooks stay global because they're wired into ~/.claude/settings.json.
mkdir -p "$SCAFFOLD/lib/core"
for f in claude-spawn.sh init.sh orchestrator.sh; do
  if [ -f "$INSTALL_DIR/lib/core/$f" ]; then
    cp -a "$INSTALL_DIR/lib/core/$f" "$SCAFFOLD/lib/core/$f"
  fi
done

# Config: lift etc/config.defaults.toml into .hive/config.toml so users can
# edit it directly without managing a separate file.
if [ -f "$INSTALL_DIR/etc/config.defaults.toml" ]; then
  cp -a "$INSTALL_DIR/etc/config.defaults.toml" "$SCAFFOLD/config.toml"
fi

# VERSION sidecar.
if [ -f "$INSTALL_DIR/VERSION" ]; then
  cp -a "$INSTALL_DIR/VERSION" "$SCAFFOLD/VERSION"
fi

# Write the manifest: sha256 of every file under .hive/, keyed by path
# relative to .hive/. Used by scaffold-update.sh to detect user tweaks.
MANIFEST="$SCAFFOLD/.manifest"
(
  cd "$SCAFFOLD"
  find . -type f ! -name '.manifest' ! -name '*.new' -print0 \
    | sort -z \
    | xargs -0 sha256sum \
    | awk '{path=$2; sub(/^\.\//,"",path); printf "%s\t%s\n", path, $1}' \
    | jq -Rsn '
        [inputs | split("\n")[] | select(length>0) | split("\t") | {key:.[0], value:.[1]}]
        | from_entries
      ' > "$MANIFEST"
)

# Lock down the scaffold — prevent agents from accidentally corrupting
# these files during runtime. Owner retains read-only access; no writes.
chmod -R a-w "$SCAFFOLD"

echo "scaffold-extract: wrote $SCAFFOLD (manifest: $(jq 'length' "$MANIFEST") files, all read-only)"
