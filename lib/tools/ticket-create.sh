#!/bin/bash
# Usage: ticket-create.sh <department> <type> <priority> <title> <description> <created_by> <source> [estimate]
# Example: ticket-create.sh backend-api-services bug high "Fix rate limiting" "Rate limiter not configured" "VP-R&D" "rd-discovery" "medium"
#
# Creates a ticket in the department's backlog with all metadata filled in.
# Returns the ticket ID on stdout.
#
# Validation:
#   TYPE     ∈ {feature, bug, improvement, chore, infrastructure, research}
#   PRIORITY ∈ {critical, high, medium, low}
#   ESTIMATE ∈ {trivial, small, medium, large, xlarge}
# All three are normalized to lowercase. Unknown values exit with a list
# of valid options. If TYPE looks like a priority AND PRIORITY looks like
# a type, the script auto-swaps them and warns (common agent mistake).

COMPANY_DIR="${COMPANY_DIR:-.company}"
source "$(dirname "$0")/ticket-paths.sh"

DEPT="$1"
TYPE="$2"
PRIORITY="$3"
TITLE="$4"
DESC="$5"
CREATED_BY="$6"
SOURCE="$7"
ESTIMATE="${8:-medium}"
shift 7 2>/dev/null || true
[ "${1:-}" != "" ] && [ "${1:0:2}" != "--" ] && shift   # consume the optional estimate positional

# Optional --external-id <id>: links the ticket to a foreign id (e.g.
# (empty for native hive tickets).
EXTERNAL_ID=""
while [ $# -gt 0 ]; do
  case "$1" in
    --external-id) EXTERNAL_ID="$2"; shift 2 ;;
    --estimate)    ESTIMATE="$2"; shift 2 ;;
    *) shift ;;
  esac
done

if [ -z "$DEPT" ] || [ -z "$TYPE" ] || [ -z "$TITLE" ]; then
  echo "Usage: ticket-create.sh <department> <type> <priority> <title> <description> <created_by> <source> [estimate] [--external-id <id>]" >&2
  echo "Types: feature, bug, improvement, chore, infrastructure, research" >&2
  echo "Priorities: critical, high, medium, low" >&2
  echo "Sources: sprint-planning, rd-discovery, qa-finding, user-request, bug-report, improvement, verification" >&2
  exit 1
fi

# ── Normalize + validate enum fields ──
# Lowercase everything; reject unknown values with a clear list.
VALID_TYPES="feature bug improvement chore infrastructure research"
VALID_PRIORITIES="critical high medium low"
VALID_ESTIMATES="trivial small medium large xlarge"

# Common synonym map → canonical value. Agents reach for these instinctively;
# normalize once instead of rejecting and forcing them to retry.
normalize_type() {
  local v; v=$(echo "$1" | tr '[:upper:]' '[:lower:]')
  case "$v" in
    feat|features|enhancement|story)     v="feature" ;;
    defect|fix|hotfix|bugfix)            v="bug" ;;
    improve|improvements|polish|refactor) v="improvement" ;;
    task|chores|maintenance)             v="chore" ;;
    infra|ops|devops|infra-task)         v="infrastructure" ;;
    investigate|spike|study)             v="research" ;;
  esac
  echo "$v"
}
normalize_priority() {
  local v; v=$(echo "$1" | tr '[:upper:]' '[:lower:]')
  case "$v" in
    p0|urgent|blocker|severe) v="critical" ;;
    p1|important)             v="high" ;;
    p2|normal|standard)       v="medium" ;;
    p3|nice-to-have|minor)    v="low" ;;
  esac
  echo "$v"
}
normalize_estimate() {
  local v; v=$(echo "$1" | tr '[:upper:]' '[:lower:]')
  case "$v" in
    xs|tiny)             v="trivial" ;;
    s|sm)                v="small" ;;
    m|md)                v="medium" ;;
    l|lg|big)            v="large" ;;
    xl|xxl|huge|epic)    v="xlarge" ;;
  esac
  echo "$v"
}

TYPE=$(normalize_type "$TYPE")
PRIORITY=$(normalize_priority "$PRIORITY")
ESTIMATE=$(normalize_estimate "$ESTIMATE")

# Auto-swap detection: agents sometimes reverse type and priority.
# Detect: TYPE is a known priority value AND PRIORITY is a known type value.
type_is_priority=0; case " $VALID_PRIORITIES " in *" $TYPE "*) type_is_priority=1 ;; esac
priority_is_type=0; case " $VALID_TYPES "      in *" $PRIORITY "*) priority_is_type=1 ;; esac
if [ "$type_is_priority" = 1 ] && [ "$priority_is_type" = 1 ]; then
  echo "WARN: type='$TYPE' looks like a priority and priority='$PRIORITY' looks like a type — swapping." >&2
  tmp="$TYPE"; TYPE="$PRIORITY"; PRIORITY="$tmp"
fi

# Final enum validation.
case " $VALID_TYPES " in
  *" $TYPE "*) ;;
  *) echo "ERROR: type='$TYPE' is not valid. Must be one of: $VALID_TYPES" >&2; exit 1 ;;
esac
case " $VALID_PRIORITIES " in
  *" $PRIORITY "*) ;;
  *) echo "ERROR: priority='$PRIORITY' is not valid. Must be one of: $VALID_PRIORITIES" >&2; exit 1 ;;
esac
case " $VALID_ESTIMATES " in
  *" $ESTIMATE "*) ;;
  *) echo "WARN: estimate='$ESTIMATE' is not valid; defaulting to medium. Valid: $VALID_ESTIMATES" >&2; ESTIMATE="medium" ;;
esac

TICKET_DIR="$(ticket_dir "$DEPT")"
COUNTER_FILE="$TICKET_DIR/counter.json"

# Ensure ticket board exists
for d in backlog sprint-current in-progress in-review done archive; do
  mkdir -p "$TICKET_DIR/$d"
done
mkdir -p "$TICKET_DIR/sprints/history"

# Validate department exists in org-chart
if [ -f "$COMPANY_DIR/org-chart.json" ]; then
  # Inputs via env vars + single-quoted heredoc so no character in the
  # path or dept name (quote, space, backslash) can break the python.
  VALID_DEPT=$(TC_OC_FILE="$COMPANY_DIR/org-chart.json" TC_DEPT="$DEPT" python3 - <<'PYEOF' 2>/dev/null
import json, os
oc = json.load(open(os.environ['TC_OC_FILE']))
depts = list(oc.get('departments', {}).keys())
dept = os.environ['TC_DEPT']
if dept in depts:
    print('valid')
else:
    # Try case-insensitive match first
    dept_lower = dept.lower()
    for d in depts:
        if d.lower() == dept_lower:
            print(d)
            exit(0)
    # Try fuzzy substring match (case-insensitive)
    for d in depts:
        if dept_lower in d.lower() or d.lower() in dept_lower:
            print(d)
            exit(0)
    print('')
PYEOF
)

  if [ -z "$VALID_DEPT" ]; then
    echo "ERROR: Department '$DEPT' not found in org-chart.json" >&2
    echo "Valid departments:" >&2
    TC_OC_FILE="$COMPANY_DIR/org-chart.json" python3 - <<'PYEOF' 2>/dev/null
import json, os
oc = json.load(open(os.environ['TC_OC_FILE']))
for d in oc.get('departments', {}).keys():
    print('  ' + d)
PYEOF
    exit 1
  elif [ "$VALID_DEPT" != "valid" ]; then
    echo "CORRECTED: '$DEPT' -> '$VALID_DEPT'" >&2
    DEPT="$VALID_DEPT"
    TICKET_DIR="$(ticket_dir "$DEPT")"
    COUNTER_FILE="$TICKET_DIR/counter.json"
  fi
fi

# Check for duplicate title (skip if title already exists in any status).
# Title comes in via env var — the previous '''$TITLE''' form corrupted
# backslashes and SyntaxError'd on triple quotes.
DUP=$(TC_TICKET_DIR="$TICKET_DIR" TC_TITLE="$TITLE" python3 - <<'PYEOF' 2>/dev/null
import json, os, glob
ticket_dir = os.environ['TC_TICKET_DIR']
title = os.environ['TC_TITLE'].strip().lower()
for status in ['backlog','sprint-current','in-progress','in-review']:
    d = os.path.join(ticket_dir, status)
    if not os.path.isdir(d): continue
    for f in glob.glob(os.path.join(d, '*.json')):
        try:
            t = json.load(open(f))
            if t.get('title','').strip().lower() == title:
                print(t.get('id',''))
                exit(0)
        except Exception: pass
print('')
PYEOF
)

if [ -n "$DUP" ]; then
  echo "DUPLICATE: '$TITLE' already exists as $DUP" >&2
  echo "$DUP"
  exit 0
fi

# Compute the canonical prefix from the dept name. Any non-letter
# characters are stripped and the first 12 chars uppercased (e.g.
# "backend-api" → "BACKENDAPI", "rd" → "RD", "legal" → "LEGAL").
# This is the FALLBACK; counter.json's stored prefix wins if it's
# non-empty (some workspaces have hand-edited custom prefixes).
DEPT_PREFIX=$(echo "$DEPT" | tr '[:lower:]' '[:upper:]' | tr -d -c '[:upper:]' | cut -c1-12)
[ -z "$DEPT_PREFIX" ] && DEPT_PREFIX="TICK"   # last resort

# Read and increment counter. Validate both fields — empty/zero values
# in counter.json caused tickets to come out as "-0000" through "-0002"
# (Legal hit this on a fresh workspace where the counter was seeded with
# {prefix: "", next_id: 0}). .get(k, default) only fires when k is MISSING,
# not when it has a falsy value, so we re-validate here.
if [ -f "$COUNTER_FILE" ]; then
  PREFIX=$(TC_COUNTER_FILE="$COUNTER_FILE" python3 - <<'PYEOF' 2>/dev/null
import json, os, sys
c = json.load(open(os.environ['TC_COUNTER_FILE']))
sys.stdout.write(c.get('prefix') or '')
PYEOF
)
  NEXT_ID=$(TC_COUNTER_FILE="$COUNTER_FILE" python3 - <<'PYEOF' 2>/dev/null
import json, os, sys
c = json.load(open(os.environ['TC_COUNTER_FILE']))
sys.stdout.write(str(int(c.get('next_id') or 0)))
PYEOF
)
else
  PREFIX=""
  NEXT_ID=0
fi
# Validate prefix: blank / whitespace / pure-punctuation → derive from dept
case "$PREFIX" in
  ""|" "|"-"|"_") PREFIX="$DEPT_PREFIX" ;;
  *)
    # Strip and check that something readable remains
    if [ -z "$(echo "$PREFIX" | tr -d -c '[:alnum:]')" ]; then
      PREFIX="$DEPT_PREFIX"
    fi
    ;;
esac
# Validate next_id: must be >= 1 (else first ticket would be XXX-0000,
# off-by-one and ugly). If counter is corrupted, scan existing ticket
# files in this dept and start at max(seen) + 1 so we don't collide.
if [ -z "$NEXT_ID" ] || [ "$NEXT_ID" -lt 1 ] 2>/dev/null; then
  NEXT_ID=$(TC_TICKET_DIR="$TICKET_DIR" python3 - <<'PYEOF' 2>/dev/null
import os, glob, re
hi = 0
ticket_dir = os.environ['TC_TICKET_DIR']
for st in ['backlog','sprint-current','in-progress','in-review','done','archive']:
    for f in glob.glob(os.path.join(ticket_dir, st, '*.json')):
        m = re.search(r'-(\\d+)\\.json\$', os.path.basename(f))
        if m:
            try: hi = max(hi, int(m.group(1)))
            except Exception: pass
print(hi + 1)
PYEOF
)
  [ -z "$NEXT_ID" ] && NEXT_ID=1
fi

TICKET_ID="${PREFIX}-$(printf '%04d' $NEXT_ID)"
NEW_NEXT=$((NEXT_ID + 1))
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Write updated counter (env-var inputs; see ticket-create header note)
TC_PREFIX="$PREFIX" TC_NEW_NEXT="$NEW_NEXT" TC_COUNTER_FILE="$COUNTER_FILE" python3 - <<'PYEOF'
import json, os
counter = {'prefix': os.environ['TC_PREFIX'], 'next_id': int(os.environ['TC_NEW_NEXT'])}
json.dump(counter, open(os.environ['TC_COUNTER_FILE'], 'w'), indent=2)
PYEOF

# Create ticket JSON.
# CRITICAL: user text ($TITLE/$DESC/$EXTERNAL_ID) comes in via environment
# vars and the heredoc is single-quoted, so bash does NOT interpolate into
# the python source. The previous '''$TITLE''' form corrupted backslashes
# (C:\\temp\\new → 'C:<TAB>emp<NEWLINE>ew'), SyntaxError'd on titles with
# quotes, and — because the counter above had already advanced — printed a
# phantom ticket ID with exit 0 while writing no file at all.
TICKET_FILE="$TICKET_DIR/backlog/$TICKET_ID.json"
TC_TICKET_FILE="$TICKET_FILE" \
TC_TICKET_ID="$TICKET_ID" \
TC_TYPE="$TYPE" \
TC_TITLE="$TITLE" \
TC_DESC="$DESC" \
TC_CREATED_BY="$CREATED_BY" \
TC_DEPT="$DEPT" \
TC_SOURCE="$SOURCE" \
TC_PRIORITY="$PRIORITY" \
TC_ESTIMATE="$ESTIMATE" \
TC_TIMESTAMP="$TIMESTAMP" \
TC_EXTERNAL_ID="$EXTERNAL_ID" \
python3 - <<'PYEOF'
import json, os
ticket = {
    'id': os.environ['TC_TICKET_ID'],
    'type': os.environ['TC_TYPE'],
    'title': os.environ['TC_TITLE'],
    'description': os.environ['TC_DESC'],
    'created_by': os.environ.get('TC_CREATED_BY', ''),
    'filed_by_department': os.environ['TC_DEPT'],
    'source': os.environ.get('TC_SOURCE', ''),
    'priority': os.environ['TC_PRIORITY'],
    'status': 'backlog',
    'target_department': os.environ['TC_DEPT'],
    'assignee': {'role': 'unassigned', 'agent_id': '', 'name': 'Unassigned'},
    'sprint': None,
    'estimate': os.environ['TC_ESTIMATE'],
    'dependencies': [],
    'blocked_by': [],
    'labels': [],
    'acceptance_criteria': [],
    'comments': [],
    'resolution': {'status': None, 'resolved_at': None, 'resolved_by': None, 'git_branch': None, 'commit': None, 'notes': None},
    'created_at': os.environ['TC_TIMESTAMP'],
    'updated_at': os.environ['TC_TIMESTAMP'],
    'external_id': os.environ['TC_EXTERNAL_ID'] or None,
}
json.dump(ticket, open(os.environ['TC_TICKET_FILE'], 'w'), indent=2)
PYEOF

# Guard: never report a ticket ID for a file that was not actually written.
# The counter advanced above; roll it back and fail loudly instead of
# printing a phantom ID with exit 0.
if [ ! -f "$TICKET_FILE" ]; then
  echo "ERROR: ticket $TICKET_ID was not written to $TICKET_FILE — rolling back counter." >&2
  TC_PREFIX="$PREFIX" TC_NEXT_ID="$NEXT_ID" TC_COUNTER_FILE="$COUNTER_FILE" python3 - <<'PYEOF' 2>/dev/null
import json, os
counter = {'prefix': os.environ['TC_PREFIX'], 'next_id': int(os.environ['TC_NEXT_ID'])}
json.dump(counter, open(os.environ['TC_COUNTER_FILE'], 'w'), indent=2)
PYEOF
  exit 1
fi

echo "$TICKET_ID"
