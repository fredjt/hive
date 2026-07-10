#!/usr/bin/env bash
set -uo pipefail

# done-protector.sh — enforce the invariant: a ticket that has ever reached
# `done` MUST stay in done. Removes any non-done copy of a ticket that also
# exists in done.
#
# Why this exists. .company/ used to be (still is, in some workspaces) git-
# tracked. Live ticket-move operations write directly to the working tree,
# but git operations (checkout, merge, pull, worktree add) restore HEAD's
# snapshot of those files — which is from BEFORE the ticket was moved to
# done. The result: the same ticket ends up in done/ (the live truth)
# AND in in-progress/ or backlog/ (stale, restored by git), confusing
# every downstream consumer (sprint-fill picks it as backlog, sprint-lifecycle
# tries to spawn an engineer, worktree-merger trips over the
# done-vs-active state, etc.).
#
# This sweeper enforces the invariant unconditionally: if a ticket exists
# in done AND anywhere else, the non-done copy is deleted. Done wins.
# Logs each removal so the operator can see what happened.
#
# Usage:
#   done-protector.sh <project-root>
#
# Exits 0 always (it's a sweep, not a gate).

PROJECT_ROOT="${1:-${PROJECT_ROOT:-$PWD}}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd)" || exit 0
COMPANY_DIR="$PROJECT_ROOT/.company"
[ -d "$COMPANY_DIR/departments" ] || exit 0

LOG="$COMPANY_DIR/logs/done-protector.log"
mkdir -p "$(dirname "$LOG")"
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "[$(ts)] $*" | tee -a "$LOG" >&2; }

REMOVED=0
for dept_dir in "$COMPANY_DIR"/departments/*/; do
  done_dir="$dept_dir/tickets/done"
  [ -d "$done_dir" ] || continue
  for done_file in "$done_dir"/*.json; do
    [ -f "$done_file" ] || continue
    tid=$(basename "$done_file" .json)
    for stale_status in backlog sprint-current in-progress in-review; do
      stale_file="$dept_dir/tickets/$stale_status/$tid.json"
      if [ -f "$stale_file" ]; then
        dept=$(basename "$dept_dir")
        log "remove $dept/$tid from $stale_status (already in done; done is authoritative)"
        rm -f "$stale_file"
        REMOVED=$((REMOVED + 1))
      fi
    done
  done
done

if [ "$REMOVED" -gt 0 ]; then
  echo "done-protector: removed $REMOVED stale duplicate(s) (see $LOG)"
fi
exit 0
