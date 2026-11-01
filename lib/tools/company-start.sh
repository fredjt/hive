#!/bin/bash
# Starts the entire company. Run from the project root directory.
# Usage: company-start.sh <project-root>
# Starts all VP loops, sprint-fill, sprint-archive, and records PIDs.
# No artificial sleeps - agents run back-to-back as fast as they can.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# SCRIPT_DIR is .../lib/tools, so the hive root is two levels up.
# Honor a pre-set HIVE_ROOT or SKILL_DIR if the caller already resolved it.
SKILL_DIR="${HIVE_ROOT:-${SKILL_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}}"

# Load the user's hive env file (LLM endpoint config, etc.)
# so every spawned daemon inherits it
# regardless of whether company-start was launched from an interactive shell.
HIVE_ENV_FILE="${HIVE_ENV_FILE:-$HOME/.config/hive/env.sh}"
if [ -f "$HIVE_ENV_FILE" ]; then
  set -a
  . "$HIVE_ENV_FILE"
  set +a
fi
PROJECT_ROOT="${1:-.}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" && pwd)"
COMPANY_DIR="$PROJECT_ROOT/.company"
export SKILL_DIR

if [ ! -d "$COMPANY_DIR" ]; then
  echo "Error: No .company directory found at $PROJECT_ROOT" >&2
  echo "Run 'hive init' then 'hive company start' to create the initial project." >&2
  exit 1
fi

# Refuse to start if .company is corrupted (most commonly: an agent
# replaced it with a self-symlink, which crashes everything that opens
# the directory with ELOOP). The check prints concrete recovery steps.
if [ -x "$SCRIPT_DIR/check-company-dir.sh" ]; then
  bash "$SCRIPT_DIR/check-company-dir.sh" "$PROJECT_ROOT" || exit 1
fi

# Kill any existing company processes first
if [ -f "$COMPANY_DIR/processes.json" ]; then
  echo "Stopping existing company processes..."
  bash "$SCRIPT_DIR/company-stop.sh" "$PROJECT_ROOT" 2>/dev/null
  sleep 2
fi

# Clean up bogus department directories (only keep org-chart departments)
if [ -f "$COMPANY_DIR/org-chart.json" ]; then
  CS_OC_FILE="$COMPANY_DIR/org-chart.json" CS_DEPT_DIR="$COMPANY_DIR/departments" python3 - <<'PYEOF' 2>/dev/null
import json, os, shutil
oc=json.load(open(os.environ['CS_OC_FILE']))
valid=set(oc.get('departments',{}).keys())
dept_dir=os.environ['CS_DEPT_DIR']
for d in os.listdir(dept_dir):
    if d not in valid and os.path.isdir(os.path.join(dept_dir,d)):
        shutil.rmtree(os.path.join(dept_dir,d))
        print(f'  Removed bogus department: {d}')
PYEOF
fi

# Clean up roster: dedup VPs, ensure verification managers exist
CS_ROSTER_FILE="$COMPANY_DIR/roster.json" CS_ORG_FILE="$COMPANY_DIR/org-chart.json" python3 - <<'PYEOF'
import json, os
roster_file=os.environ['CS_ROSTER_FILE']
org_file=os.environ['CS_ORG_FILE']
if not os.path.exists(roster_file): exit(0)
r=json.load(open(roster_file))
agents=r.get('agents',r) if isinstance(r,dict) else r
# Dedup active VPs per department
seen={}
for i,a in enumerate(agents):
    if not isinstance(a,dict): continue
    if 'VP' in a.get('role','') and a.get('status')=='active':
        dept=a.get('department','')
        if dept in seen: agents[i]['status']='retired'; agents[i]['notes']='Startup dedup'
        else: seen[dept]=i
# Ensure verification managers
if os.path.exists(org_file):
    org=json.load(open(org_file))
    depts=list(org.get('departments',{}).keys())
    existing={a.get('department') for a in agents if isinstance(a,dict) and 'verif' in a.get('role','').lower() and a.get('status')=='active'}
    for d in depts:
        if d not in existing:
            agents.append({'id':'verification-mgr-'+d,'role':'Manager-Verification','department':d,'status':'active'})
if isinstance(r,dict): r['agents']=agents; json.dump(r,open(roster_file,'w'),indent=2)
else: json.dump(agents,open(roster_file,'w'),indent=2)
PYEOF

# Clean up stale worktrees — but ONLY ones whose tickets are no longer
# active. The previous logic deleted any worktree with "0 commits ahead
# of main", which killed worktrees freshly created for an IC that hadn't
# yet committed. With the sprint-staged lifecycle, an IC may have a
# worktree open for the entire planning + first-edit window; deleting
# under them on every `hive start` was destroying live work.
if git -C "$PROJECT_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  echo "Cleaning stale git worktrees..."
  git -C "$PROJECT_ROOT" worktree prune 2>/dev/null
  PROJECT_ROOT="$PROJECT_ROOT" COMPANY_DIR="$COMPANY_DIR" python3 - <<'PYEOF' 2>/dev/null
import os, subprocess, glob, json

PROJECT_ROOT = os.environ['PROJECT_ROOT']
COMPANY_DIR  = os.environ['COMPANY_DIR']

# Resolve the actual default branch (was hardcoded "main"; this killed
# every cleanup on master-based projects and could even delete an active
# worktree because git log main..<branch> errored and stdout was empty).
def default_branch():
    for cand in ('main', 'master'):
        r = subprocess.run(['git', 'rev-parse', '--verify', cand],
                           capture_output=True, cwd=PROJECT_ROOT)
        if r.returncode == 0:
            return cand
    r = subprocess.run(['git', 'symbolic-ref', '--short', 'refs/remotes/origin/HEAD'],
                       capture_output=True, text=True, cwd=PROJECT_ROOT)
    if r.returncode == 0:
        return r.stdout.strip().removeprefix('origin/')
    return None

base = default_branch()

# Build the set of TICKET IDs that are currently active (sprint-current,
# in-progress, or in-review). Worktrees for these MUST NOT be deleted —
# an IC may be mid-spawn working on them, possibly pre-first-commit.
active_tickets = set()
for status in ('sprint-current', 'in-progress', 'in-review'):
    pattern = os.path.join(COMPANY_DIR, 'departments', '*', 'tickets', status, '*.json')
    for f in glob.glob(pattern):
        tid = os.path.basename(f).removesuffix('.json')
        active_tickets.add(tid)

# Walk worktrees
result = subprocess.run(['git', 'worktree', 'list', '--porcelain'],
                        capture_output=True, text=True, cwd=PROJECT_ROOT)
worktrees = []
wt = {}
for line in result.stdout.split('\n'):
    if line.startswith('worktree '):
        if wt.get('path'):
            worktrees.append(wt)
        wt = {'path': line[9:]}
    elif line.startswith('branch '):
        wt['branch'] = line[7:].replace('refs/heads/', '')
if wt.get('path'):
    worktrees.append(wt)

removed = 0
kept_active = 0
for w in worktrees:
    path = w.get('path')
    branch = w.get('branch')
    if not path or not branch or path == PROJECT_ROOT:
        continue
    # Extract ticket id from feature/<ID> branch names
    ticket = branch.removeprefix('feature/') if branch.startswith('feature/') else None
    if ticket and ticket in active_tickets:
        kept_active += 1
        continue   # active ticket — never auto-clean
    # Only delete if branch has 0 commits ahead of base AND base resolved
    if not base:
        continue
    ahead = subprocess.run(['git', 'log', f'{base}..{branch}', '--oneline'],
                           capture_output=True, text=True, cwd=PROJECT_ROOT)
    if ahead.returncode != 0:
        # Branch can't be diff'd against base (detached, missing, etc.) — skip
        # rather than delete (the old code mistakenly treated "git error"
        # the same as "0 commits ahead" because stdout was empty in both).
        continue
    if ahead.stdout.strip():
        continue   # has commits — preserve
    subprocess.run(['git', 'worktree', 'remove', path, '--force'],
                   capture_output=True, cwd=PROJECT_ROOT)
    subprocess.run(['git', 'branch', '-D', branch],
                   capture_output=True, cwd=PROJECT_ROOT)
    removed += 1

if removed:
    print(f'  Removed {removed} stale empty worktrees')
if kept_active:
    print(f'  Preserved {kept_active} worktree(s) for active sprint tickets')
PYEOF
fi

# Sweep stale done-duplicates BEFORE any background daemon starts. Common
# trigger: while hive was stopped, a git checkout/merge/pull restored an
# old in-progress snapshot of a ticket that has since been moved to done.
# Without this sweep the bug-fix daemons would treat the stale copy as
# active work.
if [ -x "$SKILL_DIR/lib/tools/done-protector.sh" ]; then
  bash "$SKILL_DIR/lib/tools/done-protector.sh" "$PROJECT_ROOT" || true
fi

echo "Starting company at $PROJECT_ROOT"
echo "================================================"

PIDS=()
PROCS='[]'

add_proc() {
  local pid=$1 role=$2 dept=$3 interval=$4
  PROCS=$(echo "$PROCS" | CS_PID="$pid" CS_ROLE="$role" CS_DEPT="$dept" CS_INTERVAL="$interval" CS_STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)" python3 - <<'PYEOF'
import json,sys,os
procs=json.load(sys.stdin)
procs.append({'pid':int(os.environ['CS_PID']),'role':os.environ['CS_ROLE'],'department':os.environ['CS_DEPT'],'interval':os.environ['CS_INTERVAL'],'started_at':os.environ['CS_STARTED_AT']})
json.dump(procs,sys.stdout)
PYEOF
)
  PIDS+=($pid)
  echo "  Started $role ($dept) PID=$pid"
}

# --- Sprint-lifecycle is the SOLE consumer of agent-spawn.sh ---------------
# Under this architecture there are no per-VP loops, no CEO continuous loop,
# no per-dept verification loops, no standup/sprint-review pollers. Every
# agent turn runs serially through the sprint-lifecycle's FIFO queue, drained
# one entry at a time. When a round drains, the runner fires standup
# automatically and the CEO drafts the next round's queue.

# --- Marketing website (if it exists) ---

WEBSITE_DIR="$COMPANY_DIR/marketing/website"
if [ -d "$WEBSITE_DIR" ]; then
  # Find and start the website server (look for common entry points)
  WEBSITE_ENTRY=""
  for entry in server.cjs server.js app.cjs app.js index.cjs index.js app.py server.py main.py; do
    if [ -f "$WEBSITE_DIR/$entry" ]; then
      WEBSITE_ENTRY="$entry"
      break
    fi
  done
  if [ -n "$WEBSITE_ENTRY" ]; then
    case "$WEBSITE_ENTRY" in
      *.cjs|*.js) (cd "$WEBSITE_DIR" && node "$WEBSITE_ENTRY" >> "$COMPANY_DIR/logs/website.log" 2>&1) & ;;
      *.py) (cd "$WEBSITE_DIR" && python3 "$WEBSITE_ENTRY" >> "$COMPANY_DIR/logs/website.log" 2>&1) & ;;
    esac
    add_proc $! "company-website" "marketing" "continuous"
  fi
fi

# --- Hive MCP server (HTTP transport) ---
# The dashboard's mutation endpoints, the CLI, and spawned agents all call
# tools through this server. Without it the dashboard's "Run gates", "Start
# Company", ticket create/move, etc. fail with `MCP unreachable: fetch failed`.
MCP_HOST="${HIVE_MCP_HOST:-127.0.0.1}"
MCP_PORT="${HIVE_MCP_PORT:-7891}"
MCP_URL="http://$MCP_HOST:$MCP_PORT/mcp"

# Resolve the global install dir (where venv + Python/Node sources live).
# SKILL_DIR points to the global install (projects use installed scripts directly) —
# HIVE_INSTALL_DIR is the authoritative path for global resources.
INSTALL_DIR="${HIVE_INSTALL_DIR:-$SKILL_DIR}"

# Reuse an already-running MCP (e.g. started by `hive` or `hive dashboard`)
if curl -sf -o /dev/null --max-time 1 "http://$MCP_HOST:$MCP_PORT/" 2>/dev/null \
   || nc -z "$MCP_HOST" "$MCP_PORT" 2>/dev/null; then
  echo "MCP server already running on $MCP_URL"
else
  # venv and hive_mcp/server.py live in the global install (INSTALL_DIR
  # was resolved at the top of this script).
  VENV_PY="$INSTALL_DIR/venv/bin/python"
  if [ -x "$VENV_PY" ] && [ -f "$INSTALL_DIR/lib/hive_mcp/server.py" ]; then
    echo "Starting Hive MCP server on $MCP_URL ..."
    HIVE_ROOT="$SKILL_DIR" HIVE_INSTALL_DIR="$INSTALL_DIR" \
      "$VENV_PY" "$INSTALL_DIR/lib/hive_mcp/server.py" \
        --transport http --host "$MCP_HOST" --port "$MCP_PORT" \
        >> "$COMPANY_DIR/logs/mcp-server.log" 2>&1 &
    add_proc $! "hive-mcp" "infrastructure" "continuous"
    # Wait briefly for the port to come up so the dashboard's first call works
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      nc -z "$MCP_HOST" "$MCP_PORT" 2>/dev/null && break
      sleep 0.5
    done
  else
    echo "WARNING: hive venv or hive_mcp/server.py missing — MCP server NOT started." >&2
    echo "         Dashboard mutation endpoints (Run Gates, Start/Stop Company, etc.) will fail." >&2
  fi
fi

# --- Management Dashboard ---
# Only start a dashboard if one isn't already running (e.g., started by
# `hive` with no args). When the user clicks "Start Company" from an
# existing dashboard, we must NOT kill and restart it — that would break
# the live WebSocket connection. We only start a new dashboard when
# company-start is run standalone (no prior `hive`).

DASHBOARD_PORT=3456
DASHBOARD_ALREADY_RUNNING=false
if nc -z 127.0.0.1 "$DASHBOARD_PORT" 2>/dev/null; then
  echo "Dashboard already running on port $DASHBOARD_PORT — reusing it."
  DASHBOARD_ALREADY_RUNNING=true
fi

# Free the dashboard port only when we actually need to start a new one.
# lsof is the cleanest tool but isn't always on PATH (especially when
# company-start runs from a non-interactive shell context that doesn't
# pick up /usr/bin). Fall back to fuser, then ss+grep, then a no-op —
# better to log a stale port and let the dashboard fail loudly than to
# silently restart-loop because lsof wasn't found.
free_port() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -ti:"$port" 2>/dev/null | xargs -r kill -9 2>/dev/null || true
  elif command -v fuser >/dev/null 2>&1; then
    fuser -k -n tcp "$port" 2>/dev/null || true
  elif command -v ss >/dev/null 2>&1; then
    ss -tlnpH "sport = :$port" 2>/dev/null \
      | grep -oE 'pid=[0-9]+' | cut -d= -f2 \
      | xargs -r kill -9 2>/dev/null || true
  else
    echo "WARNING: no lsof/fuser/ss available — cannot free port $port if stale" >&2
  fi
}
if [ "$DASHBOARD_ALREADY_RUNNING" = false ]; then
  free_port "$DASHBOARD_PORT"
fi

# Generate the sprint dashboard HTML initially
DASHBOARD_GENERATOR="$PROJECT_ROOT/tools/dashboard/generate-dashboard.cjs"
DASHBOARD_HTML="$COMPANY_DIR/dashboard.html"
if [ -f "$DASHBOARD_GENERATOR" ]; then
  (cd "$PROJECT_ROOT" && node "$DASHBOARD_GENERATOR" --auto "$DASHBOARD_HTML") >> "$COMPANY_DIR/logs/dashboard-generator.log" 2>&1
fi

# Start the company-board dashboard (live ticket board + chat + reports)
# Only start a new dashboard if one isn't already running (e.g., started by
# `hive` with no args). Reusing avoids breaking the live connection.
if [ "$DASHBOARD_ALREADY_RUNNING" = false ]; then
  (HIVE_MCP_URL="$MCP_URL" HIVE_ROOT="$SKILL_DIR" HIVE_INSTALL_DIR="$INSTALL_DIR" \
    node "$INSTALL_DIR/lib/dashboard/server.js" --dir "$COMPANY_DIR" --port $DASHBOARD_PORT \
    >> "$COMPANY_DIR/logs/company-board.log" 2>&1) &
  add_proc $! "company-board" "infrastructure" "continuous"
else
  echo "Dashboard already running on port $DASHBOARD_PORT — reusing it."
fi

# Periodically regenerate the full sprint dashboard (every 2 minutes)
if [ -f "$DASHBOARD_GENERATOR" ]; then
  (while true; do
    sleep 120
    cd "$PROJECT_ROOT" && node "$DASHBOARD_GENERATOR" --auto "$DASHBOARD_HTML" >> "$COMPANY_DIR/logs/dashboard-generator.log" 2>&1
    rc=$?
    if [ "$rc" -ne 0 ]; then
      echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] dashboard-generator exited code=$rc, restarting in 5s" >> "$COMPANY_DIR/logs/dashboard-generator.log"
      sleep 5
    fi
  done) &
  add_proc $! "dashboard-generator" "infrastructure" "120s"
fi

# --- Sprint-lifecycle: the only main-LLM consumer ---
# Singleton-locked. Drains current-round.json one entry at a time, fires
# standup at end-of-round, runs round-promote.sh between rounds (which
# invokes ic-spawner / verification-runner / sprint-lifecycle in
# --enqueue-only mode to discover new IC + verification + sprint work).
(HIVE_ROOT="$SKILL_DIR" bash "$SCRIPT_DIR/sprint-lifecycle.sh" "$PROJECT_ROOT" \
  >> "$COMPANY_DIR/logs/sprint-lifecycle.log" 2>&1) &
add_proc $! "sprint-lifecycle" "infrastructure" "fifo-serial"

# --- Infrastructure processes (pure bash, no LLM) ---

# Roster auto-rebuild (every 60s - derives roster.json from tickets/worktrees/logs)
(while true; do
  COMPANY_DIR="$COMPANY_DIR" bash "$SCRIPT_DIR/roster-rebuild.sh" --quiet >> "$COMPANY_DIR/logs/roster-rebuild.log" 2>&1
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] roster-rebuild exited code=$rc, restarting in 5s" >> "$COMPANY_DIR/logs/roster-rebuild.log"
    sleep 5
  fi
  sleep 60
done) &
add_proc $! "roster-rebuild" "infrastructure" "60s"

# Status auto-rebuild (every 60s - derives status.json for all departments)
(while true; do
  COMPANY_DIR="$COMPANY_DIR" bash "$SCRIPT_DIR/status-rebuild.sh" --quiet >> "$COMPANY_DIR/logs/status-rebuild.log" 2>&1
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] status-rebuild exited code=$rc, restarting in 5s" >> "$COMPANY_DIR/logs/status-rebuild.log"
    sleep 5
  fi
  sleep 60
done) &
add_proc $! "status-rebuild" "infrastructure" "60s"

# Org chart auto-rebuild (every 120s - derives org-chart.json from filesystem)
(while true; do
  COMPANY_DIR="$COMPANY_DIR" bash "$SCRIPT_DIR/org-chart-rebuild.sh" --quiet >> "$COMPANY_DIR/logs/org-chart-rebuild.log" 2>&1
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] org-chart-rebuild exited code=$rc, restarting in 5s" >> "$COMPANY_DIR/logs/org-chart-rebuild.log"
    sleep 5
  fi
  sleep 120
done) &
add_proc $! "org-chart-rebuild" "infrastructure" "120s"

# Worktree auto-merger (every 60s - merges worktrees whose tickets are in done/)
(while true; do
  bash "$SCRIPT_DIR/worktree-merger.sh" "$PROJECT_ROOT" --quiet >> "$COMPANY_DIR/logs/worktree-merger.log" 2>&1
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] worktree-merger exited code=$rc, restarting in 5s" >> "$COMPANY_DIR/logs/worktree-merger.log"
    sleep 5
  fi
  sleep 60
done) &
add_proc $! "worktree-merger" "infrastructure" "60s"

# IC discovery is no longer a 60s ticker — round-promote.sh runs ic-spawner
# in --enqueue-only mode exactly once between rounds, so newly-arrived
# tickets land in the next round (not the current one mid-flight).


# Verification gates (every 90s per dept - runs deterministic gates BEFORE
# spawning a verification IC). Gates are blocking - tickets that fail gates
# get bounced back to in-progress with a comment. Passing gates does NOT
# auto-promote to done; the verification IC still reviews logic/design.
(while true; do
  _vg_rc=0
  for dept_dir in "$COMPANY_DIR"/departments/*/; do
    [ -d "$dept_dir" ] || continue
    dept="$(basename "$dept_dir")"
    bash "$SCRIPT_DIR/verification-gates.sh" "$dept" "$PROJECT_ROOT" --quiet \
      >> "$COMPANY_DIR/logs/verification-gates.log" 2>&1 || _vg_rc=$?
  done
  if [ "$_vg_rc" -ne 0 ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] verification-gates exited code=$_vg_rc, restarting in 5s" >> "$COMPANY_DIR/logs/verification-gates.log"
    sleep 5
  fi
  sleep 90
done) &
add_proc $! "verification-gates" "infrastructure" "90s"

# --- Health supervisor (every 30s — checks process liveness, heartbeat freshness, cleanup stale spawns) ---
(while true; do
  bash "$SCRIPT_DIR/company-health.sh" "$PROJECT_ROOT" >> "$COMPANY_DIR/logs/health.log" 2>&1
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] company-health exited code=$rc, restarting in 5s" >> "$COMPANY_DIR/logs/health.log"
    sleep 5
  fi
  sleep 30
done) &
add_proc $! "company-health" "infrastructure" "30s"

# --- Save process info ---

CS_PROCS="$PROCS" CS_STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)" CS_PROJECT_ROOT="$PROJECT_ROOT" CS_PROCS_FILE="$COMPANY_DIR/processes.json" python3 - <<'PYEOF'
import json, os
procs=json.loads(os.environ['CS_PROCS'])
result={
  'started_at': os.environ['CS_STARTED_AT'],
  'project_root': os.environ['CS_PROJECT_ROOT'],
  'processes': procs
}
json.dump(result, open(os.environ['CS_PROCS_FILE'],'w'), indent=2)
PYEOF

echo ""
echo "================================================"
echo "Company started with ${#PIDS[@]} processes"
echo "PIDs: ${PIDS[*]}"
echo "Logs: $COMPANY_DIR/logs/"
echo "Processes: $COMPANY_DIR/processes.json"
echo ""
echo "Management Dashboard: http://localhost:8080"
echo "Ticket Board:        http://localhost:$DASHBOARD_PORT"
echo "Sprint Dashboard:    http://localhost:$DASHBOARD_PORT/dashboard"
echo "To stop: bash $SCRIPT_DIR/company-stop.sh $PROJECT_ROOT"
