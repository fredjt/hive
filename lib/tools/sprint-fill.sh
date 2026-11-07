#!/bin/bash
# Promotes top-priority backlog tickets into sprint-current, capped at
# `--max N` per department (default 5).
#
# Usage: sprint-fill.sh [--max N | --min N] [--dept DEPT]
# Options:
#   --max N     Cap sprint-current+in-progress at N per dept (default: 5).
#               If active >= N, no promotion happens. Otherwise top
#               (N - active) tickets are promoted, picked by priority then
#               age.
#   --min N     Back-compat alias for --max (same semantics).
#   --dept DEPT Only fill a specific department (default: all).
#
# Idempotent and safe to run repeatedly.
# Priority order: critical > high > medium > low; ties broken by oldest first.

COMPANY_DIR="${COMPANY_DIR:-.company}"
# shellcheck source=/dev/null
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/hive-state.sh"
hs_rel() { echo "${1#"$COMPANY_DIR"/}"; }
source "$(dirname "$0")/ticket-paths.sh"
MAX_ACTIVE=5
TARGET_DEPT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --max|--min) MAX_ACTIVE="$2"; shift 2 ;;
    --dept) TARGET_DEPT="$2"; shift 2 ;;
    *) shift ;;
  esac
done

DEPT_DIR="$COMPANY_DIR/departments"

if [ ! -d "$DEPT_DIR" ]; then
  echo "No departments directory found at $DEPT_DIR" >&2
  exit 1
fi

TOTAL_PROMOTED=0

for dept_path in "$DEPT_DIR"/*/; do
  dept=$(basename "$dept_path")
  ticket_dir="$(ticket_dir "$dept")"

  # Skip if no ticket board
  [ -d "$ticket_dir/backlog" ] || continue
  [ -d "$ticket_dir/sprint-current" ] || mkdir -p "$ticket_dir/sprint-current"

  # Skip if filtering to specific dept
  if [ -n "$TARGET_DEPT" ] && [ "$dept" != "$TARGET_DEPT" ]; then
    continue
  fi

  # Count active tickets
  sc=$(find "$ticket_dir/sprint-current" -name "*.json" 2>/dev/null | wc -l)
  ip=$(find "$ticket_dir/in-progress" -name "*.json" 2>/dev/null | wc -l)
  active=$((sc + ip))

  # Count backlog
  bl=$(find "$ticket_dir/backlog" -name "*.json" 2>/dev/null | wc -l)

  if [ "$active" -ge "$MAX_ACTIVE" ]; then
    continue
  fi

  if [ "$bl" -eq 0 ]; then
    if [ "$active" -eq 0 ]; then
      echo "WARNING: $dept has ZERO tickets (backlog=0, active=0). Needs new work from CEO/R&D."
    fi
    continue
  fi

  need=$((MAX_ACTIVE - active))

  # Prefer the prioritizer helper (deterministic priority+age) if present.
  # Falls back to the inline sort below if it's missing or fails.
  PRIORITIZER="$(dirname "$0")/prioritize-backlog.sh"
  tickets=""
  if [ -x "$PRIORITIZER" ]; then
    tickets=$(COMPANY_DIR="$COMPANY_DIR" bash "$PRIORITIZER" "$dept" --limit "$need" 2>/dev/null) || tickets=""
  fi

  # Fallback: deterministic priority+age sort.
  # Priority is normalized so ALL-CAPS / synonym values from older tickets
  # don't sink to the bottom — same vocabulary the dashboard knows.
  if [ -z "$tickets" ]; then
    # Env-var inputs (ticket_dir/need) — inline interpolation would break
    # on paths containing quotes or spaces.
    tickets=$(SF_TICKET_DIR="$ticket_dir" SF_NEED="$need" python3 - <<'PYEOF' 2>/dev/null
import json, glob, os

PRIORITY_ORDER = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
SYNONYMS = {
    'p0': 'critical', 'urgent': 'critical', 'blocker': 'critical', 'severe': 'critical',
    'p1': 'high', 'important': 'high',
    'p2': 'medium', 'normal': 'medium', 'standard': 'medium',
    'p3': 'low', 'nice-to-have': 'low', 'minor': 'low',
}
def normalize(p):
    p = (p or 'low').lower()
    return SYNONYMS.get(p, p)

ticket_dir = os.environ['SF_TICKET_DIR']
need = int(os.environ['SF_NEED'])
tickets = []
for f in glob.glob(os.path.join(ticket_dir, 'backlog', '*.json')):
    try:
        t = json.load(open(f))
        pri = normalize(t.get('priority'))
        order = PRIORITY_ORDER.get(pri, 4)
        # Tie-break by created_at ascending (oldest first), then by id.
        tickets.append((order, t.get('created_at', ''), t.get('id', ''), f))
    except Exception:
        pass

tickets.sort()
for _, _, tid, f in tickets[:need]:
    print(tid)
PYEOF
)
  fi

  promoted=0
  for ticket_id in $tickets; do
    src="$ticket_dir/backlog/$ticket_id.json"
    dst="$ticket_dir/sprint-current/$ticket_id.json"

    if [ ! -f "$src" ]; then
      continue
    fi

    # Update status in JSON and move — one state-layer step each (the
    # move is a locked atomic rename with destination validation).
    if ! HIVE_SCRIPTED_MOVE=1 hs --company-dir "$COMPANY_DIR" ticket-move \
         --dept "$dept" --ticket "$ticket_id" --status sprint-current \
         --author sprint-fill --comment "sprint-fill: promoted to sprint" >/dev/null 2>&1; then
      continue
    fi
    promoted=$((promoted + 1))
    TOTAL_PROMOTED=$((TOTAL_PROMOTED + 1))
  done

  if [ "$promoted" -gt 0 ]; then
    echo "$dept: promoted $promoted tickets (active: $active -> $((active + promoted)), backlog: $bl -> $((bl - promoted)))"
  fi
done

if [ "$TOTAL_PROMOTED" -gt 0 ]; then
  echo "Total: promoted $TOTAL_PROMOTED tickets across departments (cap: $MAX_ACTIVE per dept)"
else
  echo "All departments at or above sprint cap (max: $MAX_ACTIVE per dept)"
fi
