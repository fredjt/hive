#!/bin/bash
# Usage: ticket-list.sh [department] [status] [--priority <p>] [--json] [--full]
# Example: ticket-list.sh backend-api-services sprint-current
# Example: ticket-list.sh backend-api-services --json
# Example: ticket-list.sh                          # company-wide (0.17)
# Example: ticket-list.sh --json                   # company-wide docs
#
# The department argument is optional (0.17): omitted, every department
# in the company is listed, grouped. --json emits
# {"departments": {<dept>: <per-dept doc>}, "total": N, "mode": ...};
# the human face prints one block per department. A NAMED department
# that does not exist is an error ("Department '<dept>' not found");
# an existing one with zero tickets is a count-0 doc — the "no tickets"
# vs "doesn't exist" distinction (an empty company is visible, not
# silent).
#
# --json by default returns SUMMARY fields only (id, status, title,
# priority, assignee, gates_passed, verified_by, comment_count). Pass
# --full to include the entire ticket body (description + all comments).
# The full bodies can blow past an MCP client's token budget on tickets
# with rejection-comment history (a single in-progress bounce-loop
# ticket can grow to 100KB+); use ticket-get for the full body of a
# specific ticket.

COMPANY_DIR="${COMPANY_DIR:-.company}"
source "$(dirname "$0")/ticket-paths.sh"
DEPTS_DIR="${COMPANY_DIR}/departments"

DEPT=""
STATUS=""
JSON=false
FULL=false
PRIORITY=""
POSITIONAL=()
_i=1
while [ "$_i" -le "$#" ]; do
  arg="$(eval echo "\$$_i")"
  case "$arg" in
    --json) JSON=true ;;
    --full) FULL=true ;;
    --priority) _i=$((_i + 1)); PRIORITY="$(eval echo "\$$_i" 2>/dev/null || true)" ;;
    -*) : ;;
    *) POSITIONAL+=("$arg") ;;
  esac
  _i=$((_i + 1))
done
[ "${#POSITIONAL[@]}" -ge 1 ] && DEPT="${POSITIONAL[0]}"
[ "${#POSITIONAL[@]}" -ge 2 ] && STATUS="${POSITIONAL[1]}"

if [ -n "$DEPT" ]; then
  DEPT_DIR="$DEPTS_DIR/$DEPT"
  if [ ! -d "$DEPT_DIR" ]; then
    echo "Department '$DEPT' not found" >&2
    exit 1
  fi
fi

if [ -n "$STATUS" ]; then
  STATUSES="$STATUS"
else
  STATUSES="backlog sprint-current in-progress in-review done"
fi


if [ "$JSON" = true ]; then
  if [ -z "$DEPT" ]; then
    # Company-wide: one doc per department, plus the company total.
    TL_DEPTS_DIR="$DEPTS_DIR" TL_STATUSES="$STATUSES" TL_PRIORITY="$PRIORITY" TL_FULL="$FULL" python3 - <<'PYEOF'
import json, os, glob

DEPTS_DIR = os.environ.get('TL_DEPTS_DIR', '')
STATUSES = os.environ.get('TL_STATUSES', '').split()
PRIORITY = os.environ.get('TL_PRIORITY', '')
full = os.environ.get('TL_FULL', 'false') == 'true'

def summarize(t):
    a = t.get('assignee') or {}
    if isinstance(a, dict):
        assignee = a.get('name') or a.get('agent_id') or 'Unassigned'
    else:
        assignee = str(a) if a else 'Unassigned'
    comments = t.get('comments') or []
    return {
        'id':             t.get('id'),
        'title':          t.get('title'),
        'status':         t.get('status'),
        'type':           t.get('type'),
        'priority':       t.get('priority'),
        'assignee':       assignee,
        'gates_passed':   t.get('gates_passed', False),
        'verified_by':    t.get('verified_by') or '',
        'skip_tdd_red':   t.get('skip_tdd_red', False),
        'comment_count':  len(comments),
        'last_comment':   (comments[-1].get('body','')[:160] + '...') if comments else None,
    }

def dept_doc(dept):
    ticket_dir = os.path.join(DEPTS_DIR, dept, 'tickets')
    tickets = []
    for status in STATUSES:
        d = os.path.join(ticket_dir, status)
        if not os.path.isdir(d):
            continue
        for f in sorted(glob.glob(os.path.join(d, '*.json'))):
            try:
                t = json.load(open(f))
                t['_status_dir'] = status
                t['_file'] = f
                tickets.append(t if full else summarize(t))
            except Exception:
                pass
    if PRIORITY:
        tickets = [t for t in tickets if t.get('priority') == PRIORITY]
    return {'department': dept, 'count': len(tickets), 'tickets': tickets,
            'mode': 'full' if full else 'summary'}

departments = {}
if os.path.isdir(DEPTS_DIR):
    for entry in sorted(os.listdir(DEPTS_DIR)):
        if os.path.isdir(os.path.join(DEPTS_DIR, entry)):
            departments[entry] = dept_doc(entry)
total = sum(doc['count'] for doc in departments.values())
print(json.dumps({'departments': departments, 'total': total,
                  'mode': 'full' if full else 'summary'}, indent=2))
PYEOF
    exit $?
  fi
  TICKET_DIR="$(ticket_dir "$DEPT")"
  FULL_VAR="$FULL" TL_DEPT="$DEPT" TL_STATUSES="$STATUSES" TL_TICKET_DIR="$TICKET_DIR" TL_PRIORITY="$PRIORITY" python3 - <<'PYEOF'
import json, os, glob
full = os.environ.get('FULL_VAR') == 'true'
DEPT = os.environ.get('TL_DEPT', '')
STATUSES = os.environ.get('TL_STATUSES', '')
TICKET_DIR = os.environ.get('TL_TICKET_DIR', '')

def summarize(t):
    a = t.get('assignee') or {}
    if isinstance(a, dict):
        assignee = a.get('name') or a.get('agent_id') or 'Unassigned'
    else:
        assignee = str(a) if a else 'Unassigned'
    comments = t.get('comments') or []
    return {
        'id':             t.get('id'),
        'title':          t.get('title'),
        'status':         t.get('status'),
        'type':           t.get('type'),
        'priority':       t.get('priority'),
        'assignee':       assignee,
        'gates_passed':   t.get('gates_passed', False),
        'verified_by':    t.get('verified_by') or '',
        'skip_tdd_red':   t.get('skip_tdd_red', False),
        'comment_count':  len(comments),
        'last_comment':   (comments[-1].get('body','')[:160] + '...') if comments else None,
    }

tickets = []
for status in STATUSES.split():
    d = os.path.join(TICKET_DIR, status)
    if not os.path.isdir(d):
        continue
    for f in sorted(glob.glob(os.path.join(d, '*.json'))):
        try:
            t = json.load(open(f))
            t['_status_dir'] = status
            t['_file'] = f
            tickets.append(t if full else summarize(t))
        except Exception:
            pass

PRIORITY = os.environ.get('TL_PRIORITY', '')
if PRIORITY:
    tickets = [t for t in tickets if t.get('priority') == PRIORITY]

print(json.dumps({'department': DEPT, 'count': len(tickets), 'tickets': tickets, 'mode': 'full' if full else 'summary'}, indent=2))
PYEOF
  exit $?
fi

# Human face: one line per ticket, grouped by status.
list_dept_human() {
  local dept="$1" dir f
  dir="$(ticket_dir "$dept")"
  for status in $STATUSES; do
    dir="$dir/$status"
    [ -d "$dir" ] || continue
    count=$(ls "$dir"/*.json 2>/dev/null | wc -l)
    [ "$count" -eq 0 ] && continue
    echo "=== $status ($count) ==="
    for f in "$dir"/*.json; do
      [ -f "$f" ] || continue
      TL_FILE="$f" TL_PRI="$PRIORITY" python3 - <<'PYEOF' 2>/dev/null
import json, os
t = json.load(open(os.environ['TL_FILE']))
pri = t.get('priority','?')
if os.environ.get('TL_PRI') and pri != os.environ['TL_PRI']:
    raise SystemExit(0)
assignee = t.get('assignee',{})
name = assignee.get('name','Unassigned') if isinstance(assignee,dict) else str(assignee) if assignee else 'Unassigned'
print(f"  {t.get('id','?'):15s} [{pri:8s}] {t.get('title','')[:60]:60s} -> {name}")
PYEOF
    done
  done
}

if [ -z "$DEPT" ]; then
  # Company-wide human face: one labeled block per department.
  found_any=0
  if [ -d "$DEPTS_DIR" ]; then
    for d in "$DEPTS_DIR"/*/; do
      [ -d "$d" ] || continue
      dept_name="$(basename "$d")"
      found_any=1
      echo "=== $dept_name ==="
      list_dept_human "$dept_name"
    done
  fi
  [ "$found_any" -eq 1 ] || echo "No departments found in $DEPTS_DIR"
else
  list_dept_human "$DEPT"
fi
