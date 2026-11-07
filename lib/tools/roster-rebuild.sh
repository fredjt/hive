#!/usr/bin/env bash
set -euo pipefail

# roster-rebuild.sh - Rebuild .company/roster.json from authoritative sources.
#
# This script is the SOLE writer of roster.json. It derives the roster from:
#   - VP log activity (.company/logs/vp-*.log mtime → VP active/inactive)
#   - In-progress tickets (assignee field → active IC)
#   - Git worktrees (branch → worktree_path lookup)
#   - Org chart (.company/org-chart.json → known departments)
#
# Agents MUST NOT call roster-update.sh anymore. This script runs on a schedule
# (every 60s via company-start.sh background process) and on-demand.
#
# Usage: roster-rebuild.sh [--quiet]

QUIET=false
[ "${1:-}" = "--quiet" ] && QUIET=true

COMPANY_DIR="${COMPANY_DIR:-.company}"
# shellcheck source=/dev/null
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/hive-state.sh"
PROJECT_ROOT="$(cd "$(dirname "$COMPANY_DIR")" && pwd)"
ROSTER_FILE="$COMPANY_DIR/roster.json"

# VPs are considered active if their log was written in the last N seconds.
VP_ACTIVE_WINDOW="${VP_ACTIVE_WINDOW:-1800}"   # 30 minutes default

mkdir -p "$COMPANY_DIR"

# Discover known departments: org-chart + filesystem + always-present static set.
declare -A DEPT_SET
for d in hr it rd marketing; do
  DEPT_SET[$d]=1
done
if [ -f "$COMPANY_DIR/org-chart.json" ]; then
  while read -r d; do
    [ -n "$d" ] && DEPT_SET[$d]=1
  # Item 9: org-chart.json's .departments is a KEYED object (name ->
  # metadata); '.departments[]?.name' yielded nothing, so org-chart
  # departments never reached the roster (vp-finance / vp-legal missing).
  done < <(jq -r 'if (.departments | type) == "object" then (.departments | keys[]) elif (.departments | type) == "array" then (.departments[]?.name // empty) else empty end' "$COMPANY_DIR/org-chart.json" 2>/dev/null || true)
fi
if [ -d "$COMPANY_DIR/departments" ]; then
  for dept_dir in "$COMPANY_DIR/departments"/*/; do
    [ -d "$dept_dir" ] || continue
    DEPT_SET["$(basename "$dept_dir")"]=1
  done
fi

# Build worktree branch -> path map (one git call, not one per ticket).
declare -A WORKTREE_MAP
if git -C "$PROJECT_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  current_path=""
  while IFS= read -r line; do
    case "$line" in
      worktree\ *)
        current_path="${line#worktree }"
        ;;
      branch\ refs/heads/*)
        branch="${line#branch refs/heads/}"
        WORKTREE_MAP["$branch"]="$current_path"
        ;;
    esac
  done < <(git -C "$PROJECT_ROOT" worktree list --porcelain 2>/dev/null || true)
fi

NOW=$(date +%s)
NOW_ISO=$(date -u -d "@$NOW" +"%Y-%m-%dT%H:%M:%SZ")

AGENTS="[]"

# 1. VPs - one entry per department, active if log is fresh.
for dept in "${!DEPT_SET[@]}"; do
  vp_log="$COMPANY_DIR/logs/vp-${dept}.log"
  status="inactive"
  last_active="null"
  if [ -f "$vp_log" ]; then
    last_mod=$(stat -c %Y "$vp_log" 2>/dev/null || echo 0)
    age=$((NOW - last_mod))
    if [ "$age" -lt "$VP_ACTIVE_WINDOW" ]; then
      status="active"
    fi
    last_active="\"$(date -u -d "@$last_mod" +%Y-%m-%dT%H:%M:%SZ)\""
  fi
  AGENTS=$(jq --arg dept "$dept" --arg status "$status" --argjson la "$last_active" \
    '. + [{
      agent_id: ("vp-" + $dept),
      name: ("VP-" + ($dept | ascii_upcase)),
      role: "VP",
      department: $dept,
      status: $status,
      assigned_ticket: null,
      worktree_path: null,
      last_active: $la
    }]' <<<"$AGENTS")
done

# 2. ICs - one entry per in-progress ticket across all departments.
# Build the set of ticket ids that have a live spawn record. An assignee
# in a ticket file is just a paper claim — the IC is only "active" if
# agent-spawn.sh has registered it (file in logs/active/) AND the recorded
# pid is still alive.
ACTIVE_TICKETS_DELIM="|"
if [ -d "$COMPANY_DIR/logs/active" ]; then
  for af in "$COMPANY_DIR/logs/active"/*.json; do
    [ -f "$af" ] || continue
    rec_ticket=$(jq -r '.ticket // empty' "$af" 2>/dev/null || echo "")
    rec_pid=$(jq -r '.pid // 0' "$af" 2>/dev/null || echo 0)
    [ -z "$rec_ticket" ] && continue
    # Liveness check on pid (orphaned active files happen if a parent dies
    # before its EXIT trap fires).
    if [ "$rec_pid" -gt 0 ] && [ -d "/proc/$rec_pid" ]; then
      ACTIVE_TICKETS_DELIM="${ACTIVE_TICKETS_DELIM}${rec_ticket}|"
    fi
  done
fi

if [ -d "$COMPANY_DIR/departments" ]; then
  for dept_dir in "$COMPANY_DIR/departments"/*/; do
    dept="$(basename "$dept_dir")"
    in_prog_dir="$dept_dir/tickets/in-progress"
    [ -d "$in_prog_dir" ] || continue
    for ticket_file in "$in_prog_dir"/*.json; do
      [ -f "$ticket_file" ] || continue
      ticket_id=$(jq -r '.id // empty' "$ticket_file" 2>/dev/null || echo "")
      assignee=$(jq -r '.assignee // empty' "$ticket_file" 2>/dev/null || echo "")
      branch=$(jq -r '.branch // empty' "$ticket_file" 2>/dev/null || echo "")
      [ -z "$ticket_id" ] || [ -z "$assignee" ] && continue

      worktree_path=""
      [ -n "$branch" ] && worktree_path="${WORKTREE_MAP[$branch]:-}"
      wt_json="null"
      [ -n "$worktree_path" ] && wt_json="\"$worktree_path\""

      # Distinguish "claimed" (paper assignment, no live process) from
      # "active" (agent harness process for this ticket is running).
      ic_status="claimed"
      case "$ACTIVE_TICKETS_DELIM" in
        *"|${ticket_id}|"*) ic_status="active" ;;
      esac

      AGENTS=$(jq \
        --arg id "ic-${ticket_id}" \
        --arg name "$assignee" \
        --arg dept "$dept" \
        --arg ticket "$ticket_id" \
        --arg branch "$branch" \
        --arg st "$ic_status" \
        --argjson wt "$wt_json" \
        '. + [{
          agent_id: $id,
          name: $name,
          role: "IC",
          department: $dept,
          status: $st,
          assigned_ticket: $ticket,
          branch: ($branch | if . == "" then null else . end),
          worktree_path: $wt
        }]' <<<"$AGENTS")
    done
  done
fi

# 3. Write roster atomically (state layer: flock + tmp+replace + validation).
jq -n --argjson agents "$AGENTS" --arg ts "$NOW_ISO" \
  '{
    agents: $agents,
    updated_at: $ts,
    source: "roster-rebuild.sh",
    note: "DERIVED FILE. Do not edit. Regenerated from tickets, worktrees, and VP logs."
  }' | hs write "roster.json" --stdin >/dev/null

if [ "$QUIET" = false ]; then
  vp_count=$(jq '[.agents[] | select(.role == "VP")] | length' "$ROSTER_FILE")
  vp_active=$(jq '[.agents[] | select(.role == "VP" and .status == "active")] | length' "$ROSTER_FILE")
  ic_count=$(jq '[.agents[] | select(.role == "IC")] | length' "$ROSTER_FILE")
  echo "Roster rebuilt: ${vp_active}/${vp_count} VPs active, ${ic_count} ICs working" >&2
fi
