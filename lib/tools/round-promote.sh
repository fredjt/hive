#!/usr/bin/env bash
set -uo pipefail

# round-promote.sh — runs between rounds. Archives current-round.json,
# then promotes next-plan.json into a fresh current-round.json (or falls
# back to a minimal failsafe round).
#
# Usage: round-promote.sh <project-root> [--first-boot]
#
# This script is the SOLE place the discovery scripts run. They no longer
# tick on a 60s timer in company-start.sh.
#
# In the ticket-driven lifecycle (sprint-lifecycle.sh), rounds are archived
# at sprint boundaries. Within a sprint, the round persists across stages
# and entries are appended by the sprint-lifecycle script.

PROJECT_ROOT="${1:-.}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" && pwd)" || { echo "bad project root" >&2; exit 2; }
FIRST_BOOT=0
[ "${2:-}" = "--first-boot" ] && FIRST_BOOT=1

SKILL_DIR="${HIVE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
COMPANY_DIR="$PROJECT_ROOT/.company"
ROUNDS_DIR="$COMPANY_DIR/rounds"
LOG="$COMPANY_DIR/logs/sprint-lifecycle.log"
mkdir -p "$ROUNDS_DIR/history" "$COMPANY_DIR/logs"
export COMPANY_DIR PROJECT_ROOT SKILL_DIR

HIVE_ENV_FILE="${HIVE_ENV_FILE:-$HOME/.config/hive/env.sh}"
[ -f "$HIVE_ENV_FILE" ] && . "$HIVE_ENV_FILE"

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "[$(ts)] [promote] $*" >> "$LOG"; }

LOCK="$ROUNDS_DIR/queue-lock"
touch "$LOCK"
exec 8>"$LOCK"
flock -x 8

CURRENT="$ROUNDS_DIR/current-round.json"
NEXT="$ROUNDS_DIR/next-plan.json"
SPRINT_FILE="$COMPANY_DIR/sprints/current.json"

# Decide whether to archive the current round or keep it.
#
# A "round" is one full sprint pass. The sprint walks through many stages
# (ceo-plan → vp-plan → ticket processing → release → vp-review) and
# sprint-lifecycle drains the queue between every stage transition. We
# keep the same round across all in-sprint stage transitions and only
# archive at sprint boundaries.
#
# Archive when:
#   - There is no current round (nothing to keep)
#   - There is no sprint state (legacy / first boot)
#   - The sprint has reached `complete` (it's about to start a new one)
#   - The current round was tagged with a different sprint_id than the
#     active sprint (sprint already rolled over)
KEEP_ROUND=0
SPRINT_ID=""
SPRINT_STAGE=""
if [ -f "$SPRINT_FILE" ]; then
  SPRINT_ID=$(jq -r '.sprint_id // empty' "$SPRINT_FILE" 2>/dev/null)
  SPRINT_STAGE=$(jq -r '.stage // empty' "$SPRINT_FILE" 2>/dev/null)
fi

if [ -f "$CURRENT" ] && [ -n "$SPRINT_ID" ] && [ "$SPRINT_STAGE" != "complete" ]; then
  CUR_SID=$(jq -r '.sprint_id // empty' "$CURRENT" 2>/dev/null)
  if [ "$CUR_SID" = "$SPRINT_ID" ]; then
    KEEP_ROUND=1
  fi
fi

if [ "$KEEP_ROUND" = "1" ]; then
  # Same sprint, mid-stage drain. Leave entries[] intact — the
  # sprint-lifecycle script appends to it. Just refresh the source label
  # so the dashboard shows the latest stage that drove enqueues.
  SPRINT_STAGE="$SPRINT_STAGE" python3 - "$CURRENT" <<'PYEOF' 2>/dev/null || true
import json, os, sys
f = sys.argv[1]
d = json.load(open(f))
d["source"] = os.environ.get("SPRINT_STAGE") or d.get("source") or "pending"
json.dump(d, open(f, "w"), indent=2)
PYEOF
  RID=$(jq -r '.round_id // empty' "$CURRENT" 2>/dev/null)
  log "kept round $RID for in-sprint stage transition (sprint=$SPRINT_ID, next stage handled by sprint-lifecycle)"
else
  # 1. Archive current → history (if it exists and has a round_id)
  if [ -f "$CURRENT" ]; then
    RID=$(jq -r '.round_id // empty' "$CURRENT" 2>/dev/null)
    if [ -n "$RID" ] && [ "$RID" != "null" ]; then
      NOW="$(ts)"
      RP_CURRENT="$CURRENT" NOW="$NOW" python3 - <<'PYEOF' 2>/dev/null || true
import json, os
f = os.environ['RP_CURRENT']
d = json.load(open(f))
d['ended_at'] = os.environ['NOW']
json.dump(d, open(f, 'w'), indent=2)
PYEOF
      mv "$CURRENT" "$ROUNDS_DIR/history/${RID}.json"
      log "archived $RID (sprint boundary or first boot)"
    else
      rm -f "$CURRENT"
    fi
  fi

  # 2. Build a fresh current-round.json shell with NO entries; tag with
  #    the active sprint_id so subsequent drains within this sprint can
  #    detect "same round" and skip the archive.
  NEW_RID="round-$(date -u +%Y%m%dT%H%M%SZ)"
  NOW=$(ts)
  NEW_RID="$NEW_RID" NOW="$NOW" SPRINT_ID="$SPRINT_ID" SPRINT_STAGE="$SPRINT_STAGE" python3 - <<'PYEOF' > "$CURRENT"
import json, os
print(json.dumps({
  "round_id":   os.environ["NEW_RID"],
  "sprint_id":  os.environ.get("SPRINT_ID") or None,
  "started_at": os.environ["NOW"],
  "ended_at":   None,
  "source":     os.environ.get("SPRINT_STAGE") or "pending",
  "cursor":     0,
  "entries":    [],
}, indent=2))
PYEOF
  log "started shell round $NEW_RID (sprint=$SPRINT_ID)"
fi

# Drop the lock for any other operation that needs it.
flock -u 8

# (GitHub Issues poller removed — no longer mirrors GH issues into hive tickets)

flock -u 8
log "promote complete (round-shell ready; sprint-lifecycle.sh handles ticket lifecycle)"
