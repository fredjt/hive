#!/usr/bin/env bash
set -uo pipefail

# round-enqueue.sh — append one entry to a round queue (current or next).
#
# This is the canonical write path. Used by:
#   - ic-spawner.sh        (--queued-by ic-spawner)
#   - verification-runner.sh (--queued-by verification-runner)
#   - sprint-lifecycle.sh   (--queued-by sprint-lifecycle)
#   - standup-runner.sh    (--queued-by ceo-standup-synth, briefs+synth)
#   - manual CLI / dashboard / MCP tool
#
# Usage:
#   round-enqueue.sh --agent <name> --dept <dept> --role <role> --kind <kind>
#                    [--ticket <id>] [--prompt-extra <str> | --prompt-inline-stdin]
#                    [--queued-by <src>] [--log-to <path>]
#                    [--target current|next]   (default: current)
#
# On success, prints the new entry id to stdout.

AGENT=""
DEPT=""
ROLE=""
KIND=""
TICKET=""
PROMPT_EXTRA=""
PROMPT_INLINE=""
PROMPT_INLINE_STDIN=0
QUEUED_BY="manual-cli"
LOG_TO=""
TARGET="current"

while [ $# -gt 0 ]; do
  case "$1" in
    --agent)               AGENT="$2"; shift 2 ;;
    --dept)                DEPT="$2"; shift 2 ;;
    --role)                ROLE="$2"; shift 2 ;;
    --kind)                KIND="$2"; shift 2 ;;
    --ticket)              TICKET="$2"; shift 2 ;;
    --prompt-extra)        PROMPT_EXTRA="$2"; shift 2 ;;
    --prompt-inline)       PROMPT_INLINE="$2"; shift 2 ;;
    --prompt-inline-stdin) PROMPT_INLINE_STDIN=1; shift ;;
    --queued-by)           QUEUED_BY="$2"; shift 2 ;;
    --log-to)              LOG_TO="$2"; shift 2 ;;
    --target)              TARGET="$2"; shift 2 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

for var in AGENT DEPT ROLE KIND; do
  if [ -z "${!var}" ]; then
    echo "ERROR: --${var,,} required" >&2
    exit 2
  fi
done

if [ "$PROMPT_INLINE_STDIN" = "1" ]; then
  PROMPT_INLINE="$(cat)"
fi

if [ "$TARGET" != "current" ] && [ "$TARGET" != "next" ]; then
  echo "ERROR: --target must be current or next" >&2
  exit 2
fi

COMPANY_DIR="${COMPANY_DIR:-${PROJECT_ROOT:-$PWD}/.company}"
ROUNDS_DIR="$COMPANY_DIR/rounds"
mkdir -p "$ROUNDS_DIR/history"

case "$TARGET" in
  current) FILE="$ROUNDS_DIR/current-round.json" ;;
  next)    FILE="$ROUNDS_DIR/next-plan.json" ;;
esac

LOCK="$ROUNDS_DIR/queue-lock"
touch "$LOCK"

# Default log_to per kind so callers can omit it
if [ -z "$LOG_TO" ]; then
  case "$KIND" in
    standup-brief|standup-synth) LOG_TO="$COMPANY_DIR/logs/standup.log" ;;
    sprint-retro|sprint-planning|sprint-review)

    verification)                LOG_TO="$COMPANY_DIR/logs/verification-${DEPT}.log" ;;
    ic-cycle)                    LOG_TO="$COMPANY_DIR/logs/ic-${TICKET:-$AGENT}.log" ;;
    *)                           LOG_TO="$COMPANY_DIR/logs/${AGENT}.log" ;;
  esac
fi

# Build entry and append atomically under flock
exec 7>"$LOCK"
flock -x 7

ENTRY_ID="turn-$(date -u +%Y%m%dT%H%M%SZ)-$$-$RANDOM"
NOW_ISO=$(date -u +%Y-%m-%dT%H:%M:%SZ)

ENTRY_ID="$ENTRY_ID" AGENT="$AGENT" DEPT="$DEPT" ROLE="$ROLE" KIND="$KIND" \
TICKET="$TICKET" PROMPT_EXTRA="$PROMPT_EXTRA" PROMPT_INLINE="$PROMPT_INLINE" \
LOG_TO="$LOG_TO" QUEUED_BY="$QUEUED_BY" NOW_ISO="$NOW_ISO" FILE="$FILE" TARGET="$TARGET" \
python3 - <<'PYEOF'
import json, os, sys, datetime
file = os.environ["FILE"]
target = os.environ["TARGET"]
entry = {
  "id":            os.environ["ENTRY_ID"],
  "agent":         os.environ["AGENT"],
  "dept":          os.environ["DEPT"],
  "role":          os.environ["ROLE"],
  "kind":          os.environ["KIND"],
  "ticket":        os.environ.get("TICKET") or None,
  "prompt_extra":  os.environ.get("PROMPT_EXTRA") or "",
  "prompt_inline": os.environ.get("PROMPT_INLINE") or None,
  "log_to":        os.environ["LOG_TO"],
  "queued_at":     os.environ["NOW_ISO"],
  "queued_by":     os.environ["QUEUED_BY"],
  "status":        "queued",
  "started_at":    None,
  "ended_at":      None,
  "spawn_id":      None,
  "exit_code":     None,
  "duration_s":    None,
  "skip_reason":   None,
}
if os.path.exists(file):
  try:
    data = json.load(open(file))
  except Exception:
    data = None
else:
  data = None
if not isinstance(data, dict) or "entries" not in data:
  # Build a fresh shell. current-round.json gets started_at right away;
  # next-plan.json stays unstarted until promotion.
  data = {
    "round_id":   ("round-" + datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ"))
                  if target == "current" else None,
    "started_at": os.environ["NOW_ISO"] if target == "current" else None,
    "ended_at":   None,
    "source":     "first-boot" if target == "current" else "ceo-plan-draft",
    "cursor":     0,
    "entries":    [],
  }
data["entries"].append(entry)
tmp = file + ".tmp"
with open(tmp, "w") as f:
  json.dump(data, f, indent=2)
os.replace(tmp, file)
print(entry["id"])
PYEOF
RC=$?
flock -u 7
exit $RC
