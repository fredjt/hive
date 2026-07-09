#!/usr/bin/env bash
set -uo pipefail

# sprint-archive-done.sh — sweep every department's done/*.json into archive/
# at sprint completion. Stamps `archived_at` and `archived_in_sprint` on each
# ticket so historical provenance survives.
#
# Without this sweep, ticket files accumulate in done/ forever; the dashboard
# boards stay cluttered with last-sprint's resolved tickets, every dept-
# status count keeps growing, and new sprints inherit visual noise that has
# nothing to do with their current scope.
#
# Idempotent: it's safe to re-run. Files already in archive/ aren't touched;
# only done/*.json get moved.
#
# Usage: sprint-archive-done.sh <project-root> <sprint-id>

PROJECT_ROOT="${1:-${PROJECT_ROOT:-$PWD}}"
SPRINT_ID="${2:-${HIVE_SPRINT_ID:-unknown}}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd)" || { echo "sprint-archive-done: bad project root" >&2; exit 2; }
COMPANY_DIR="${COMPANY_DIR:-$PROJECT_ROOT/.company}"
[ -d "$COMPANY_DIR/departments" ] || { echo "sprint-archive-done: no departments dir at $COMPANY_DIR" >&2; exit 0; }

NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
MOVED=0
DEPTS_TOUCHED=0

for dept_dir in "$COMPANY_DIR"/departments/*/; do
  [ -d "$dept_dir" ] || continue
  done_dir="$dept_dir/tickets/done"
  archive_dir="$dept_dir/tickets/archive"
  [ -d "$done_dir" ] || continue
  mkdir -p "$archive_dir"

  dept_moved=0
  for ticket_file in "$done_dir"/*.json; do
    [ -f "$ticket_file" ] || continue
    base=$(basename "$ticket_file")
    dest="$archive_dir/$base"

    # If a ticket with the same id is already in archive (e.g. a prior crashed
    # archive run partially completed), keep the existing archive copy and
    # remove the stray done duplicate — they're keyed by ticket id and the
    # archive copy is authoritative.
    if [ -f "$dest" ]; then
      rm -f "$ticket_file"
      continue
    fi

    # Stamp archived_at + archived_in_sprint via jq, write atomically.
    tmp="$ticket_file.archive.tmp.$$"
    if jq --arg ts "$NOW" --arg sid "$SPRINT_ID" \
         '. + {archived_at:$ts, archived_in_sprint:$sid}' \
         "$ticket_file" > "$tmp" 2>/dev/null; then
      mv "$tmp" "$dest" && rm -f "$ticket_file"
      MOVED=$((MOVED + 1))
      dept_moved=$((dept_moved + 1))
    else
      # jq failure: do a plain move so we don't strand the ticket. The
      # archived_in_sprint field stays unset, which is recoverable but
      # less useful than the stamped version.
      rm -f "$tmp"
      mv "$ticket_file" "$dest"
      MOVED=$((MOVED + 1))
      dept_moved=$((dept_moved + 1))
    fi
  done

  if [ "$dept_moved" -gt 0 ]; then
    DEPTS_TOUCHED=$((DEPTS_TOUCHED + 1))
  fi
done

echo "sprint-archive-done: archived $MOVED ticket(s) across $DEPTS_TOUCHED dept(s) under sprint=$SPRINT_ID"
