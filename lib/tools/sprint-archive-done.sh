#!/usr/bin/env bash
set -uo pipefail

# sprint-archive-done.sh — sweep every department's done/*.json into archive/
# at sprint completion. Stamps `archived_at` and `archived_in_sprint` on each
# ticket so historical provenance survives, then writes the sprint's cost
# summary report (sprint-cost-summary.sh — per-round tokens/cost aggregated
# from the LLM usage log) to .company/reports/sprint-reviews/.
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
# shellcheck source=/dev/null
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/hive-state.sh"
hs_rel() { echo "${1#"$COMPANY_DIR"/}"; }
[ -d "$COMPANY_DIR/departments" ] || { echo "sprint-archive-done: no departments dir at $COMPANY_DIR" >&2; exit 0; }

NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
# G1: the sweep is one state-layer operation (locked stamp + rename per
# ticket; an existing archive copy is authoritative and a stray done/
# duplicate is dropped — same contract as the pre-G1 loop).
ARCHIVE_OUT=$(hs --company-dir "$COMPANY_DIR" ticket-archive --sprint "$SPRINT_ID" 2>/dev/null)
ARCHIVE_RC=$?
if [ $ARCHIVE_RC -ne 0 ]; then
  echo "sprint-archive-done: state-layer archive failed" >&2
  exit 1
fi
MOVED=$(printf '%s' "$ARCHIVE_OUT" | jq -r '.moved // 0')
DEPTS_TOUCHED=$(printf '%s' "$ARCHIVE_OUT" | jq -r '.depts | length')

echo "sprint-archive-done: archived $MOVED ticket(s) across $DEPTS_TOUCHED dept(s) under sprint=$SPRINT_ID"

# B4 (0.9.0): the archive is when "what did the sprint burn" becomes a
# file. sprint-cost-summary.sh prices every round in rounds/history/ from
# the LLM usage log and writes one report via report-write.sh. Non-fatal:
# a report failure must never block (or roll back) the ticket sweep.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -x "$SCRIPT_DIR/sprint-cost-summary.sh" ]; then
  bash "$SCRIPT_DIR/sprint-cost-summary.sh" "$PROJECT_ROOT" "$SPRINT_ID" \
    || echo "sprint-archive-done: cost summary failed (non-fatal)" >&2
fi
