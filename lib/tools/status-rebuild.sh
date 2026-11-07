#!/usr/bin/env bash
set -euo pipefail

# status-rebuild.sh - Rebuild .company/departments/<dept>/status.json for all departments
# from authoritative sources. This script is the SOLE writer of status.json files.
#
# Derives:
#   - ticket counts (backlog, sprint-current, in-progress, in-review, done, archive)
#   - velocity   (done-count delta vs the snapshot from VELOCITY_WINDOW seconds ago)
#   - health     ("good" | "warning" | "critical") from velocity + stuck tickets
#   - stuck_tickets (in-progress tickets with mtime older than STUCK_THRESHOLD)
#   - last_active (mtime of vp-<dept>.log)
#
# Maintains a JSONL history at .company/departments/<dept>/status-history.jsonl
# (append-only, capped at HISTORY_KEEP entries) used to compute velocity.
#
# Agents MUST NOT write status.json. status-update.sh is a deprecated no-op wrapper.
#
# Usage: status-rebuild.sh [--quiet] [--dept DEPT]

QUIET=false
ONLY_DEPT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --quiet) QUIET=true; shift ;;
    --dept)  ONLY_DEPT="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

COMPANY_DIR="${COMPANY_DIR:-.company}"
# shellcheck source=/dev/null
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/hive-state.sh"
VELOCITY_WINDOW="${VELOCITY_WINDOW:-1800}"     # 30 minutes - velocity = done delta over this window
STUCK_THRESHOLD="${STUCK_THRESHOLD:-3600}"     # 1 hour - tickets older than this in in-progress = stuck
HISTORY_KEEP="${HISTORY_KEEP:-200}"            # cap status-history.jsonl at this many entries

[ -d "$COMPANY_DIR/departments" ] || { echo "No departments directory: $COMPANY_DIR/departments" >&2; exit 0; }

NOW=$(date +%s)
NOW_ISO=$(date -u -d "@$NOW" +"%Y-%m-%dT%H:%M:%SZ")

count_files() {
  # count *.json files in a directory, 0 if dir missing
  local dir="$1"
  [ -d "$dir" ] || { echo 0; return; }
  find "$dir" -maxdepth 1 -name '*.json' -type f 2>/dev/null | wc -l
}

count_stuck() {
  # count in-progress tickets older than STUCK_THRESHOLD
  local dir="$1"
  [ -d "$dir" ] || { echo 0; return; }
  local cutoff=$((NOW - STUCK_THRESHOLD))
  local n=0
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    local mt
    mt=$(stat -c %Y "$f" 2>/dev/null || echo 0)
    [ "$mt" -lt "$cutoff" ] && n=$((n + 1))
  done < <(find "$dir" -maxdepth 1 -name '*.json' -type f 2>/dev/null)
  echo "$n"
}

rebuild_dept() {
  local dept="$1"
  local dept_dir="$COMPANY_DIR/departments/$dept"
  local tickets_dir="$dept_dir/tickets"
  local status_file="$dept_dir/status.json"
  local history_file="$dept_dir/status-history.jsonl"
  local vp_log="$COMPANY_DIR/logs/vp-${dept}.log"

  mkdir -p "$dept_dir"

  # Ticket counts by status
  local backlog sprint in_progress in_review done_count archive_count stuck
  backlog=$(count_files "$tickets_dir/backlog")
  sprint=$(count_files "$tickets_dir/sprint-current")
  in_progress=$(count_files "$tickets_dir/in-progress")
  in_review=$(count_files "$tickets_dir/in-review")
  done_count=$(count_files "$tickets_dir/done")
  archive_count=$(count_files "$tickets_dir/archive")
  stuck=$(count_stuck "$tickets_dir/in-progress")

  # Total completed (done + archive) - velocity is computed against this
  local total_done=$((done_count + archive_count))

  # Compute velocity: done delta vs snapshot from VELOCITY_WINDOW seconds ago
  local velocity=0
  local window_start=$((NOW - VELOCITY_WINDOW))
  if [ -f "$history_file" ]; then
    # Find most recent snapshot older than window_start
    local prior_done
    prior_done=$(awk -v cutoff="$window_start" '
      {
        # parse {"ts":1234567890,"total_done":N,...}
        match($0, /"ts":[0-9]+/)
        ts = substr($0, RSTART+5, RLENGTH-5) + 0
        match($0, /"total_done":[0-9]+/)
        td = substr($0, RSTART+13, RLENGTH-13) + 0
        if (ts <= cutoff) prior = td
      }
      END { print (prior == "" ? "" : prior) }
    ' "$history_file")
    if [ -n "$prior_done" ]; then
      velocity=$((total_done - prior_done))
      [ "$velocity" -lt 0 ] && velocity=0
    fi
  fi

  # VP last activity
  local last_active="null"
  local vp_status="inactive"
  if [ -f "$vp_log" ]; then
    local mt
    mt=$(stat -c %Y "$vp_log" 2>/dev/null || echo 0)
    last_active="\"$(date -u -d "@$mt" +%Y-%m-%dT%H:%M:%SZ)\""
    [ $((NOW - mt)) -lt "$VELOCITY_WINDOW" ] && vp_status="active"
  fi

  # Health: derive from velocity + stuck + backlog pressure
  # critical: vp inactive AND has work, OR many stuck tickets (>=5)
  # warning:  any stuck tickets, OR velocity 0 with work in progress
  # good:     velocity > 0, no stuck
  local health="good"
  local has_work=$((sprint + in_progress + in_review))
  if [ "$stuck" -ge 5 ] || { [ "$vp_status" = "inactive" ] && [ "$has_work" -gt 0 ] && [ -f "$vp_log" ]; }; then
    health="critical"
  elif [ "$stuck" -gt 0 ] || { [ "$velocity" -eq 0 ] && [ "$in_progress" -gt 0 ]; }; then
    health="warning"
  fi

  # Append to history via the state layer: one locked append+trim step.
  local hist_rel="departments/$dept/status-history.jsonl"
  local hist_line
  hist_line=$(printf '{"ts":%d,"iso":"%s","backlog":%d,"sprint":%d,"in_progress":%d,"in_review":%d,"done":%d,"archive":%d,"total_done":%d,"stuck":%d}' \
    "$NOW" "$NOW_ISO" "$backlog" "$sprint" "$in_progress" "$in_review" "$done_count" "$archive_count" "$total_done" "$stuck")
  hs jsonl-append "$hist_rel" --line "$hist_line" --keep "$HISTORY_KEEP" >/dev/null || \
    echo "status-rebuild: history append failed for $dept" >&2

  # Write status.json atomically
  jq -n \
    --arg dept "$dept" \
    --arg ts "$NOW_ISO" \
    --arg health "$health" \
    --arg vp_status "$vp_status" \
    --argjson last "$last_active" \
    --argjson backlog "$backlog" \
    --argjson sprint "$sprint" \
    --argjson in_progress "$in_progress" \
    --argjson in_review "$in_review" \
    --argjson done "$done_count" \
    --argjson archive "$archive_count" \
    --argjson stuck "$stuck" \
    --argjson velocity "$velocity" \
    '{
      department: $dept,
      updated_at: $ts,
      source: "status-rebuild.sh",
      note: "DERIVED FILE. Do not edit. Regenerated from ticket directories and VP logs.",
      health: $health,
      vp_status: $vp_status,
      last_active: $last,
      tickets: {
        backlog: $backlog,
        sprint_current: $sprint,
        in_progress: $in_progress,
        in_review: $in_review,
        done: $done,
        archive: $archive
      },
      velocity_per_window: $velocity,
      stuck_tickets: $stuck
    }' | hs write "departments/$dept/status.json" --stdin >/dev/null

  if [ "$QUIET" = false ]; then
    echo "  $dept: health=$health velocity=$velocity stuck=$stuck (bl=$backlog sp=$sprint ip=$in_progress ir=$in_review d=$done_count)" >&2
  fi
}

# Iterate departments
if [ "$QUIET" = false ]; then
  echo "Rebuilding status for departments..." >&2
fi
n=0
for dept_dir in "$COMPANY_DIR/departments"/*/; do
  [ -d "$dept_dir" ] || continue
  dept="$(basename "$dept_dir")"
  if [ -n "$ONLY_DEPT" ] && [ "$dept" != "$ONLY_DEPT" ]; then
    continue
  fi
  rebuild_dept "$dept"
  n=$((n + 1))
done
if [ "$QUIET" = false ]; then
  echo "Status rebuilt for $n departments" >&2
fi
exit 0
