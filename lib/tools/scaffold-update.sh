#!/usr/bin/env bash
set -euo pipefail

# scaffold-update.sh — reconcile a project's .hive/ scaffold against the
# current global install. Three-way diff per file: project's current sha
# vs. .manifest's original sha (what we extracted) vs. global install's
# current sha (what's available now).
#
#   project unchanged (proj == manifest)         → overwrite from global,
#                                                   update manifest
#   project tweaked (proj != manifest)           → write global as
#                                                   <path>.new sibling,
#                                                   leave project file
#                                                   alone, log to summary
#   new file in global (not in manifest)         → copy in fresh, add
#                                                   to manifest
#   gone from global (in manifest, missing
#     from global tree)                          → leave project file as-is
#                                                   (defensive; hive rarely
#                                                   removes files)
#
# Usage:
#   scaffold-update.sh <project-root> [--dry-run] [--install-dir <path>]

PROJECT_ROOT=""
DRY_RUN=0
INSTALL_DIR_OVERRIDE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)      DRY_RUN=1; shift ;;
    --install-dir)  INSTALL_DIR_OVERRIDE="$2"; shift 2 ;;
    -h|--help)      sed -n '3,22p' "$0"; exit 0 ;;
    -*)             echo "scaffold-update: unknown flag: $1" >&2; exit 2 ;;
    *)
      if [ -z "$PROJECT_ROOT" ]; then PROJECT_ROOT="$1"
      else echo "scaffold-update: unexpected arg: $1" >&2; exit 2; fi
      shift ;;
  esac
done

[ -n "$PROJECT_ROOT" ] || { echo "scaffold-update: project root required" >&2; exit 2; }
PROJECT_ROOT="$(cd "$PROJECT_ROOT" && pwd)"

SCAFFOLD="$PROJECT_ROOT/.hive"
MANIFEST="$SCAFFOLD/.manifest"
[ -d "$SCAFFOLD" ] || { echo "scaffold-update: $SCAFFOLD does not exist; run 'hive scaffold extract' first" >&2; exit 1; }
[ -f "$MANIFEST" ] || { echo "scaffold-update: $MANIFEST missing; scaffold is corrupt — re-extract with --force" >&2; exit 1; }

if [ -n "$INSTALL_DIR_OVERRIDE" ]; then
  INSTALL_DIR="$(cd "$INSTALL_DIR_OVERRIDE" && pwd)"
else
  INSTALL_DIR="${HIVE_INSTALL_DIR:-$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)}"
fi
[ -d "$INSTALL_DIR/lib" ] || { echo "scaffold-update: install dir invalid: $INSTALL_DIR" >&2; exit 1; }

# Map a scaffold-relative path back to the install-dir source path it came from.
# lib/{agents,tools,templates}/X → lib/{agents,tools,templates}/X
# lib/core/X                     → lib/core/X
# config.toml                    → etc/config.defaults.toml
# VERSION                        → VERSION
install_source_for() {
  local rel="$1"
  case "$rel" in
    config.toml)             echo "etc/config.defaults.toml" ;;
    VERSION)                 echo "VERSION" ;;
    lib/*)                   echo "$rel" ;;
    *)                       echo "" ;;  # unknown — skip
  esac
}

# Enumerate every file currently in the install dir that scaffold-extract
# would copy. Mirror scaffold-extract's source list.
list_install_scaffold_files() {
  local f
  for sub in lib/agents lib/tools lib/templates; do
    if [ -d "$INSTALL_DIR/$sub" ]; then
      (cd "$INSTALL_DIR" && find "$sub" -type f)
    fi
  done
  for f in claude-spawn.sh init.sh orchestrator.sh; do
    [ -f "$INSTALL_DIR/lib/core/$f" ] && echo "lib/core/$f"
  done
  [ -f "$INSTALL_DIR/etc/config.defaults.toml" ] && echo "config.toml"
  [ -f "$INSTALL_DIR/VERSION" ] && echo "VERSION"
}

UPDATED=0
TWEAKED=0
ADDED=0
SKIPPED=0
declare -a TWEAK_PATHS

# Build the list of (rel_path, global_sha) pairs.
while IFS= read -r rel; do
  src="$(install_source_for "$rel")"
  [ -z "$src" ] && continue
  global_path="$INSTALL_DIR/$src"
  proj_path="$SCAFFOLD/$rel"

  global_sha=$(sha256sum "$global_path" | awk '{print $1}')
  manifest_sha=$(jq -r --arg k "$rel" '.[$k] // ""' "$MANIFEST")

  if [ -z "$manifest_sha" ]; then
    # New file in global, not in manifest — add it (treat as fresh extract).
    if [ "$DRY_RUN" = "1" ]; then
      echo "would add:     $rel"
    else
      mkdir -p "$(dirname "$proj_path")"
      cp -a "$global_path" "$proj_path"
    fi
    ADDED=$((ADDED+1))
    continue
  fi

  if [ ! -f "$proj_path" ]; then
    # Manifest knows it but project file is gone — user deleted it. Leave
    # the deletion alone, don't resurrect.
    SKIPPED=$((SKIPPED+1))
    continue
  fi

  proj_sha=$(sha256sum "$proj_path" | awk '{print $1}')

  if [ "$proj_sha" = "$global_sha" ]; then
    # Already current.
    continue
  fi

  if [ "$proj_sha" = "$manifest_sha" ]; then
    # Unmodified by user → safe to replace with global.
    if [ "$DRY_RUN" = "1" ]; then
      echo "would update:  $rel"
    else
      cp -a "$global_path" "$proj_path"
    fi
    UPDATED=$((UPDATED+1))
  else
    # User tweaked it → write .new sibling.
    if [ "$DRY_RUN" = "1" ]; then
      echo "would .new:    $rel  (project tweaked; new global version → ${rel}.new)"
    else
      cp -a "$global_path" "$proj_path.new"
    fi
    TWEAKED=$((TWEAKED+1))
    TWEAK_PATHS+=("$rel")
  fi
done < <(list_install_scaffold_files)

# Refresh the manifest. Files we cp'd from global → record global sha so a
# subsequent run sees them as current. Files with a .new sibling on disk
# (user tweaks, including those we just wrote a .new for) → keep prev sha
# so the divergence stays visible across runs. Newly-added files (not in
# prev manifest) → record current sha.
if [ "$DRY_RUN" != "1" ] && [ $((UPDATED+ADDED)) -gt 0 ]; then
  TMP="$MANIFEST.tmp.$$"
  # Find all files INCLUDING .new siblings so jq can see them.
  (
    cd "$SCAFFOLD"
    find . -type f ! -name '.manifest' -print0 \
      | sort -z \
      | xargs -0 sha256sum \
      | awk '{path=$2; sub(/^\.\//,"",path); printf "%s\t%s\n", path, $1}' \
      | jq -Rsn --slurpfile old "$MANIFEST" '
          ([inputs | split("\n")[] | select(length>0) | split("\t") | {key:.[0], value:.[1]}] | from_entries) as $all
          | ($old[0]) as $prev
          # Working set = files NOT ending in .new (the real scaffold files).
          | ($all | to_entries | map(select(.key | endswith(".new") | not))) as $cur_entries
          | $cur_entries
          | map(
              . as $e
              | if ($all[$e.key + ".new"] // null) != null
                then  # .new sibling on disk → preserve prev sha (user-tweaked)
                  if $prev[$e.key] != null
                  then {key:$e.key, value:$prev[$e.key]}
                  else $e  # no prev: brand new + already conflicted (weird) → record cur
                  end
                else $e   # no .new sibling → record current sha
                end
            )
          | from_entries
        ' > "$TMP"
  )
  mv "$TMP" "$MANIFEST"
fi

echo "scaffold-update: updated=$UPDATED, tweaked=$TWEAKED (.new siblings written), added=$ADDED, missing=$SKIPPED"
if [ "$TWEAKED" -gt 0 ]; then
  echo "  tweaked files (review .new vs current, then delete .new when done):"
  for p in "${TWEAK_PATHS[@]}"; do
    echo "    $p"
  done
fi
