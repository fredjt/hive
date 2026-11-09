#!/usr/bin/env bash
# T4 (0.14): the workspace reclaims its own scratch.
#
# The pytest basetemp and the mktemp-shaped litter the suite leaves
# under $TMPDIR grow unboundedly (each session dir and hive-*/
# pip-unpack-* dir is kept for the whole session, and nothing ever
# removes the old ones). This script reclaims the SCRATCH only —
# never the workspace itself, and never anything a live session is
# using:
#
#   1. pytest basetemp: pytest-of-<user>/pytest-<N> session dirs, kept
#      back to the newest 3 generations (a generation = the numeric
#      N; xdist workers' -gwM dirs ride with their generation).
#      pytest-current (a symlink) is never touched. The current run's
#      generation is by definition the newest, so a running suite
#      keeps its own dirs.
#   2. mktemp litter under $TMPDIR: tmp.*, hive-*, pip-unpack-* older
#      than MIN_AGE_DAYS, and only when no process has a file in it
#      open (lsof, best-effort — if lsof is missing the age gate is
#      the only gate).
#
# Usage: clean-scratch.sh [--dry-run] [--min-age DAYS] [--keep N]

set -euo pipefail

DRY=0; MIN_AGE=3; KEEP=3
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --min-age) MIN_AGE="$2"; shift 2 ;;
    --keep) KEEP="$2"; shift 2 ;;
    -h|--help) grep '^# ' "$0" | sed 's/^# //'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

TMPDIR_E="${TMPDIR:-/tmp}"
removed=0

rmdir_p() {
  if [ "$DRY" = "1" ]; then echo "  would remove: $1"; else rm -rf -- "$1"; fi
  removed=$((removed + 1))
}

echo "clean-scratch: TMPDIR=$TMPDIR_E  min-age=${MIN_AGE}d  keep=${KEEP} generations  (dry-run: $DRY)"

# --- 1. pytest basetemp -----------------------------------------------------
for base in "$TMPDIR_E"/pytest-of-*; do
  [ -d "$base" ] || continue
  # generations: numeric N from pytest-<N> / pytest-<N>-gw* ; sort desc
  gens="$(for d in "$base"/pytest-*; do
    [ -d "$d" ] || continue
    n=$(basename "$d" | sed -n 's/^pytest-\([0-9][0-9]*\).*/\1/p')
    if [ -n "$n" ]; then echo "$n"; fi
  done | sort -rn | uniq; true)"
  [ -n "$gens" ] || continue
  # keep the newest KEEP generations; every other gen's dirs go
  # the newest KEEP generations (gens is sorted desc) stay
  keepset=""
  i=0
  for k in $gens; do
    i=$((i + 1))
    if [ $i -le $KEEP ]; then keepset="$keepset $k"; fi
  done
  removed_here=0
  for d in "$base"/pytest-*; do
    [ -d "$d" ] || continue
    n=$(basename "$d" | sed -n 's/^pytest-\([0-9][0-9]*\).*/\1/p')
    [ -n "$n" ] || continue   # pytest-current & co. are never touched
    keep=0
    for k in $keepset; do
      if [ "$k" = "$n" ]; then keep=1; fi
    done
    if [ $keep -eq 1 ]; then continue; fi
    # protect the freshest dirs of a running suite (fresh mtime)
    if [ -n "$(find "$d" -maxdepth 0 -mmin -60 2>/dev/null)" ]; then
      echo "  kept (fresh): $d"
      continue
    fi
    rmdir_p "$d"
    removed_here=$((removed_here + 1))
  done
  echo "basetemp $base: $removed_here dir(s) removed (newest $KEEP generation(s) kept)"
done

# --- 2. mktemp-shaped litter -------------------------------------------------
for d in "$TMPDIR_E"/tmp.* "$TMPDIR_E"/hive-* "$TMPDIR_E"/pip-unpack-*; do
  [ -d "$d" ] || continue
  # (The mktemp patterns above — tmp.*/hive-*/pip-unpack-* — name the
  # scratch shapes only, and only DIRECT CHILDREN of TMPDIR are
  # considered: the workspace root and any non-scratch path are out of
  # scope by construction, even when TMPDIR sits inside it.)
  if [ -n "$(find "$d" -maxdepth 0 -mtime -"$MIN_AGE" 2>/dev/null)" ]; then
    continue   # fresh (touched within the min age) — keep
  fi
  if command -v lsof >/dev/null 2>&1 && lsof -n +D "$d" >/dev/null 2>&1; then
    echo "  kept (open files): $d"
    continue
  fi
  rmdir_p "$d"
done

echo "clean-scratch: $removed path(s) removed (dry-run: $DRY)"
