#!/usr/bin/env bash
# company-health.sh — lightweight supervisor for hive daemons.
#
# Runs every 30s as a background loop inside company-start.sh.
#
# Responsibilities:
#   1. Check that every recorded process (from processes.json) is still alive.
#      If a process has died, attempt a one-shot restart.
#   2. Check that derived state files have recent timestamps (heartbeat).
#      If a file is stale, log a one-time warning per file (5-minute dedup
#      window) so the operator can investigate. Prevents repeated noise on
#      every 30-second health cycle.
#   3. Clean up stale active spawn records (files in logs/active/ whose pid
#      is no longer alive in /proc). These accumulate when a parent dies
#      before its EXIT trap fires.
#   4. Monitor disk usage and alert when thresholds are exceeded.
#      Reports disk usage of the project directory, ticket directories,
#      and log files. Alerts at WARNING (80%) and CRITICAL (90%) levels.
#      Deduped: same threshold breach only logged once per 15-minute window.
#   5. Report disk usage of ticket directories (done/, archive/).
#      Summarizes total ticket storage and per-department breakdown.
#      Runs every 5 minutes to avoid frequent stat() calls.
#
# Usage: company-health.sh <project-root>
# Called by company-start.sh as a background loop (every 30s).
#
# Restart policy:
#   - Each daemon can be restarted at most MAX_RESTARTS times (default 5)
#     within the current company session. This prevents restart loops from
#     burning API calls or disk space.
#   - After MAX_RESTARTS, the daemon is marked "dead" in the log and
#     requires manual intervention (hive stop + hive start).
#   - The restart counter is stored in processes.json as per-process
#     restart_count fields.

set -uo pipefail

PROJECT_ROOT="${1:-.}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" && pwd)"
COMPANY_DIR="$PROJECT_ROOT/.company"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="${HIVE_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
INSTALL_DIR="${HIVE_INSTALL_DIR:-$SKILL_DIR}"

MAX_RESTARTS="${HIVE_MAX_RESTARTS:-5}"
HEALTH_INTERVAL=30

[ -d "$COMPANY_DIR" ] || exit 0

# --- Helpers ---

is_pid_alive() {
  kill -0 "$1" 2>/dev/null
}

get_proc_role() {
  python3 -c "
import json, sys
try:
    data = json.load(open('$COMPANY_DIR/processes.json'))
    procs = data.get('processes', data.get('agents', []))
    for p in procs:
        if p.get('pid') == $1:
            print(p.get('role', 'unknown'))
            break
except Exception:
    pass
" 2>/dev/null
}

get_proc_interval() {
  python3 -c "
import json, sys
try:
    data = json.load(open('$COMPANY_DIR/processes.json'))
    procs = data.get('processes', data.get('agents', []))
    for p in procs:
        if p.get('pid') == $1:
            print(p.get('interval', 'continuous'))
            break
except Exception:
    pass
" 2>/dev/null
}

# --- Cleanup stale active spawn records ---
cleanup_active_spawns() {
  local active_dir="$COMPANY_DIR/logs/active"
  [ -d "$active_dir" ] || return 0

  local cleaned=0
  for af in "$active_dir"/*.json; do
    [ -f "$af" ] || continue
    local pid
    pid=$(jq -r '.pid // 0' "$af" 2>/dev/null || echo "0")
    if [ "$pid" != "0" ] && [ -n "$pid" ]; then
      if ! is_pid_alive "$pid"; then
        rm -f "$af"
        cleaned=$((cleaned + 1))
      fi
    fi
  done
  [ "$cleaned" -gt 0 ] && echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] health: cleaned $cleaned stale active spawn record(s)" >> "$COMPANY_DIR/logs/health.log"
}

# --- Check derived state freshness ---
#
# Logs one-time alerts per stale heartbeat file (5-minute dedup window).
# Prevents repeated noise on every 30-second health cycle.
check_heartbeat() {
  # Expected heartbeat files and their max staleness (in seconds)
  local -a heartbeat_files=(
    "$COMPANY_DIR/roster.json:180"
    "$COMPANY_DIR/departments/*/status.json:180"
    "$COMPANY_DIR/org-chart.json:240"
  )

  # Status dir for per-file dedup (same pattern as other health checks)
  local _status_dir="$COMPANY_DIR/status"
  mkdir -p "$_status_dir" 2>/dev/null || true

  local now
  now=$(date +%s)

  for entry in "${heartbeat_files[@]}"; do
    local hf="${entry%%:*}"
    local max_age="${entry##*:}"

    # Expand glob patterns
    for f in $hf; do
      [ -f "$f" ] || continue
      local mt
      mt=$(stat -c %Y "$f" 2>/dev/null || echo "0")
      local age=$((now - mt))
      if [ "$age" -gt "$max_age" ]; then
        # Dedup: skip if we already alerted on this file within the last 5 minutes
        local _dedup_key
        _dedup_key=$(echo "$f" | sed 's|[^a-zA-Z0-9]|_|g')
        local _last_file="$_status_dir/last-hb-$_dedup_key"
        local _last=0
        [ -f "$_last_file" ] && _last=$(cat "$_last_file" 2>/dev/null || echo 0)
        if [ $(( now - _last )) -ge 300 ]; then
          echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] health: heartbeat stale $f (age=${age}s, limit=${max_age}s)" >> "$COMPANY_DIR/logs/health.log"
          echo "$now" > "$_last_file"
        fi
      fi
    done
  done
}

# --- Tiny model check REMOVED ---
# The llm-tiny.sh small-model provider was replaced with local keyword heuristics.
# No external model endpoint to monitor — all classification is done locally.

# --- Check disk usage and alert on thresholds ---
#
# Checks the disk usage percentage of the filesystem containing the project.
# Logs a one-time alert per threshold breach (WARNING at 80%, CRITICAL at 90%)
# with a 15-minute dedup window to avoid repeated noise.
check_disk_usage() {
  local mount_point
  mount_point=$(df "$PROJECT_ROOT" 2>/dev/null | awk 'NR==2 {print $6}' || echo "")
  [ -z "$mount_point" ] && return 0

  local usage_pct
  usage_pct=$(df "$PROJECT_ROOT" 2>/dev/null | awk 'NR==2 {gsub(/%/,""); print $5}' || echo "0")
  [ "$usage_pct" -eq 0 ] 2>/dev/null && return 0

  local now
  now=$(date +%s)
  local warn_key="disk_usage_warn"
  local crit_key="disk_usage_crit"
  local warn_threshold="${HIVE_DISK_USAGE_WARN_PCT:-80}"
  local crit_threshold="${HIVE_DISK_USAGE_CRIT_PCT:-90}"

  # Track last alert time per threshold to dedup (15-min window)
  local _status_dir="$COMPANY_DIR/status"
  mkdir -p "$_status_dir" 2>/dev/null || true

  if [ "$usage_pct" -ge "$crit_threshold" ]; then
    local last_crit=0
    [ -f "$_status_dir/last-disk-crit" ] && last_crit=$(cat "$_status_dir/last-disk-crit" 2>/dev/null || echo 0)
    if [ $(( now - last_crit )) -ge 900 ]; then
      local total_kb avail_kb
      total_kb=$(df -k "$PROJECT_ROOT" 2>/dev/null | awk 'NR==2 {print $2}')
      avail_kb=$(df -k "$PROJECT_ROOT" 2>/dev/null | awk 'NR==2 {print $4}')
      echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] health: CRITICAL disk usage ${usage_pct}% on $mount_point (${avail_kb}KB free of ${total_kb}KB) — immediate action required" >> "$COMPANY_DIR/logs/health.log"
      echo "$now" > "$_status_dir/last-disk-crit"
    fi
  elif [ "$usage_pct" -ge "$warn_threshold" ]; then
    local last_warn=0
    [ -f "$_status_dir/last-disk-warn" ] && last_warn=$(cat "$_status_dir/last-disk-warn" 2>/dev/null || echo 0)
    if [ $(( now - last_warn )) -ge 900 ]; then
      local total_kb avail_kb
      total_kb=$(df -k "$PROJECT_ROOT" 2>/dev/null | awk 'NR==2 {print $2}')
      avail_kb=$(df -k "$PROJECT_ROOT" 2>/dev/null | awk 'NR==2 {print $4}')
      echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] health: WARNING disk usage ${usage_pct}% on $mount_point (${avail_kb}KB free of ${total_kb}KB)" >> "$COMPANY_DIR/logs/health.log"
      echo "$now" > "$_status_dir/last-disk-warn"
    fi
  fi
}

# --- Report ticket directory disk usage ---
#
# Reports disk usage of ticket directories (done/, archive/) per department.
# Runs every 5 minutes (dedup'd) to avoid frequent stat() calls.
# Does NOT delete anything — reporting only.
report_ticket_disk_usage() {
  local _status_dir="$COMPANY_DIR/status"
  mkdir -p "$_status_dir" 2>/dev/null || true
  local _last_file="$_status_dir/last-ticket-disk-report"
  local _now=$(date +%s)
  local _last=0
  [ -f "$_last_file" ] && _last=$(cat "$_last_file" 2>/dev/null || echo 0)
  local _interval="${HIVE_TICKET_DISK_REPORT_INTERVAL:-300}"
  [ $(( _now - _last )) -ge "$_interval" ] || return 0

  local total_bytes=0
  local dept_count=0
  local report=""

  for dept_dir in "$COMPANY_DIR"/departments/*/; do
    [ -d "$dept_dir" ] || continue
    dept_count=$((dept_count + 1))

    # Sum done/ and archive/ directory sizes
    for sub in done archive; do
      local sub_dir="$dept_dir/tickets/$sub"
      [ -d "$sub_dir" ] || continue
      local size
      size=$(du -sb "$sub_dir" 2>/dev/null | awk '{print $1}' || echo 0)
      size=${size:-0}
      total_bytes=$((total_bytes + size))
      local dept_name
      dept_name=$(basename "$dept_dir")
      report="${report}  ${dept_name}/${sub}: $(du -sh "$sub_dir" 2>/dev/null | awk '{print $1}')\n"
    done
  done

  if [ "$total_bytes" -gt 0 ]; then
    local human_size
    human_size=$(du -sh "$COMPANY_DIR"/departments 2>/dev/null | awk '{print $1}' || echo "${total_bytes}B")
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] health: ticket storage ${human_size} across ${dept_count} department(s) (done/ + archive/):" >> "$COMPANY_DIR/logs/health.log"
    echo -e "$report" >> "$COMPANY_DIR/logs/health.log"
  fi

  echo "$_now" > "$_last_file"
}

# --- Attempt to restart a dead daemon ---
restart_daemon() {
  local pid="$1"
  local role="$2"
  local interval="$3"

  # Check restart count
  local restart_count
  restart_count=$(python3 -c "
import json
try:
    data = json.load(open('$COMPANY_DIR/processes.json'))
    for p in data.get('processes', []):
        if p.get('pid') == $pid:
            print(p.get('restart_count', 0))
            break
    else:
        print(0)
except Exception:
    print(0)
" 2>/dev/null)
  restart_count=${restart_count:-0}

  if [ "$restart_count" -ge "$MAX_RESTARTS" ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] health: $role (pid=$pid) dead after $restart_count restarts — marking as dead, manual restart required" >> "$COMPANY_DIR/logs/health.log"
    return 1
  fi

  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] health: $role (pid=$pid) dead, attempting restart (attempt $((restart_count + 1))/$MAX_RESTARTS)" >> "$COMPANY_DIR/logs/health.log"

  # Launch the restart in background, detached from this script.
  # Redirect output to the appropriate log file.
  local log_file="$COMPANY_DIR/logs/$(echo "$role" | tr '-' '_').log"

  _do_restart() {
    local new_pid
    case "$role" in
      roster-rebuild)
        COMPANY_DIR="$COMPANY_DIR" bash "$SCRIPT_DIR/roster-rebuild.sh" --quiet >> "$log_file" 2>&1 &
        new_pid=$!
        ;;
      status-rebuild)
        COMPANY_DIR="$COMPANY_DIR" bash "$SCRIPT_DIR/status-rebuild.sh" --quiet >> "$log_file" 2>&1 &
        new_pid=$!
        ;;
      org-chart-rebuild)
        COMPANY_DIR="$COMPANY_DIR" bash "$SCRIPT_DIR/org-chart-rebuild.sh" --quiet >> "$log_file" 2>&1 &
        new_pid=$!
        ;;
      worktree-merger)
        bash "$SCRIPT_DIR/worktree-merger.sh" "$PROJECT_ROOT" --quiet >> "$log_file" 2>&1 &
        new_pid=$!
        ;;
      verification-gates)
        # Verification gates runs per-dept; for a restart we just re-launch
        # the outer loop, which will re-discover all departments.
        # We can't easily restart the while-true wrapper, so we mark it
        # as needing restart and let the next interval handle it.
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] health: verification-gates (pid=$pid) dead — outer loop restart not supported, waiting for next cycle" >> "$COMPANY_DIR/logs/health.log"
        return 1
        ;;
      company-board)
        local dashboard_port=3456
        local mcp_url="http://${HIVE_MCP_HOST:-127.0.0.1}:${HIVE_MCP_PORT:-7891}/mcp"
        HIVE_MCP_URL="$mcp_url" HIVE_ROOT="$SKILL_DIR" HIVE_INSTALL_DIR="$INSTALL_DIR" \
          node "$INSTALL_DIR/lib/dashboard/server.js" --dir "$COMPANY_DIR" --port "$dashboard_port" >> "$log_file" 2>&1 &
        new_pid=$!
        ;;
      hive-mcp)
        local mcp_host="${HIVE_MCP_HOST:-127.0.0.1}"
        local mcp_port="${HIVE_MCP_PORT:-7891}"
        local venv_py="$INSTALL_DIR/venv/bin/python"
        if [ -x "$venv_py" ]; then
          HIVE_ROOT="$SKILL_DIR" HIVE_INSTALL_DIR="$INSTALL_DIR" \
            "$venv_py" "$INSTALL_DIR/lib/hive_mcp/server.py" --transport http --host "$mcp_host" --port "$mcp_port" >> "$log_file" 2>&1 &
          new_pid=$!
        else
          echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] health: hive-mcp restart skipped — venv python not found at $venv_py" >> "$COMPANY_DIR/logs/health.log"
          return 1
        fi
        ;;
      sprint-lifecycle)
        HIVE_ROOT="$SKILL_DIR" bash "$SCRIPT_DIR/sprint-lifecycle.sh" "$PROJECT_ROOT" >> "$log_file" 2>&1 &
        new_pid=$!
        ;;
      dashboard-generator)
        local gen_script="$PROJECT_ROOT/tools/dashboard/generate-dashboard.cjs"
        local html="$COMPANY_DIR/dashboard.html"
        if [ -f "$gen_script" ]; then
          (cd "$PROJECT_ROOT" && node "$gen_script" --auto "$html") >> "$log_file" 2>&1 &
          new_pid=$!
        else
          echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] health: dashboard-generator restart skipped — generator script not found" >> "$COMPANY_DIR/logs/health.log"
          return 1
        fi
        ;;
      company-website)
        # Website daemons are project-specific; skip auto-restart.
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] health: company-website (pid=$pid) dead — project-specific, manual restart required" >> "$COMPANY_DIR/logs/health.log"
        return 1
        ;;
      *)
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] health: unknown role '$role' (pid=$pid), skipping restart" >> "$COMPANY_DIR/logs/health.log"
        return 1
        ;;
    esac

    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] health: $role restarted as pid=$new_pid" >> "$COMPANY_DIR/logs/health.log"

    # Update processes.json with the new PID and incremented restart count.
    python3 -c "
import json, os, sys
from datetime import datetime, timezone

procs_file = '$COMPANY_DIR/processes.json'
new_pid = $new_pid
old_pid = $pid
role = '$role'

try:
    data = json.load(open(procs_file))
except Exception:
    data = {'processes': []}

procs = data.get('processes', [])
found = False
for p in procs:
    if p.get('pid') == old_pid:
        p['pid'] = new_pid
        p['restart_count'] = p.get('restart_count', 0) + 1
        p['restarted_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        found = True
        break

if not found:
    # New process not in original list — add it
    procs.append({
        'pid': new_pid,
        'role': role,
        'department': 'infrastructure',
        'interval': '$interval',
        'started_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'restart_count': 1,
        'restarted_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'original_pid': old_pid,
    })

data['processes'] = procs
tmp = procs_file + '.tmp.' + str(os.getpid())
json.dump(data, open(tmp, 'w'), indent=2)
os.replace(tmp, procs_file)
" 2>/dev/null

    # Wait for the restarted process to exit
    wait $new_pid 2>/dev/null || true
  }

  # Run the restart in a subshell so it doesn't block the health loop
  _do_restart &

  return 0
}

# --- Main loop ---

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] health: starting" >> "$COMPANY_DIR/logs/health.log"

while true; do
  # 1. Check process liveness
  if [ -f "$COMPANY_DIR/processes.json" ]; then
    python3 -c "
import json, os, sys

procs_file = '$COMPANY_DIR/processes.json'
try:
    data = json.load(open(procs_file))
except Exception:
    sys.exit(0)

for p in data.get('processes', []):
    pid = p.get('pid')
    if pid is None:
        continue
    pid = int(pid)
    # Check if process is alive
    try:
        os.kill(pid, 0)
    except OSError:
        # Process is dead
        print(f'{pid}:{p.get(\"role\", \"unknown\")}:{p.get(\"interval\", \"continuous\")}')
" 2>/dev/null | while IFS=: read -r pid role interval; do
    [ -z "$pid" ] && continue
    restart_daemon "$pid" "$role" "$interval" || true
  done
  fi

  # 2. Cleanup stale active spawn records
  cleanup_active_spawns

  # 3. Check heartbeat freshness
  check_heartbeat

  # 4. Compress oversized log files (fast — only acts when thresholds exceeded)
  bash "$SCRIPT_DIR/log-rotate.sh" "$COMPANY_DIR" >> "$COMPANY_DIR/logs/health.log" 2>&1 || true

  # 5. Clean up stale worktrees (dedup'd — runs only every 5 minutes)
  _wt_cleanup_interval="${HIVE_WORKTREE_CLEANUP_INTERVAL:-300}"
  _wt_status_dir="$COMPANY_DIR/status"
  mkdir -p "$_wt_status_dir" 2>/dev/null || true
  _wt_last_file="$_wt_status_dir/last-worktree-cleanup"
  _wt_now=$(date +%s)
  _wt_last=0
  [ -f "$_wt_last_file" ] && _wt_last=$(cat "$_wt_last_file" 2>/dev/null || echo 0)
  if [ $(( _wt_now - _wt_last )) -ge "$_wt_cleanup_interval" ]; then
    bash "$SCRIPT_DIR/cleanup-worktrees.sh" "$PROJECT_ROOT" >> "$COMPANY_DIR/logs/health.log" 2>&1 || true
    echo "$_wt_now" > "$_wt_last_file"
  fi

  # 6. (removed — llm-tiny.sh was replaced with local keyword heuristics)

  # 7. Monitor disk usage and alert on threshold breaches (dedup'd per threshold)
  check_disk_usage

  # 8. Report ticket directory disk usage (dedup'd — every 5 minutes)
  report_ticket_disk_usage

  sleep "$HEALTH_INTERVAL"
done
