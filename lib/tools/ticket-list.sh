#!/bin/bash
# Usage: ticket-list.sh <department> [status] [--json] [--full]
# Example: ticket-list.sh backend-api-services sprint-current
# Example: ticket-list.sh backend-api-services --json
# Example: ticket-list.sh backend-api-services in-progress --json
# Example: ticket-list.sh backend-api-services in-progress --json --full
#
# --json by default returns SUMMARY fields only (id, status, title, priority,
# assignee, gates_passed, verified_by, comment_count). Pass --full to include
# the entire ticket body (description + all comments). The full bodies can
# blow past an MCP client's token budget on tickets with rejection-comment
# history (a single in-progress bounce-loop ticket can grow to 100KB+); use
# ticket-get for the full body of a specific ticket.

COMPANY_DIR="${COMPANY_DIR:-.company}"
source "$(dirname "$0")/ticket-paths.sh"

DEPT="$1"
STATUS="$2"
JSON=false
FULL=false

# Allow --json and --full anywhere after the dept arg.
for arg in "$@"; do
  case "$arg" in
    --json) JSON=true ;;
    --full) FULL=true ;;
  esac
done
# If $2 is a flag, there's no status filter.
case "$STATUS" in
  --json|--full|"") STATUS="" ;;
esac

if [ -z "$DEPT" ]; then
  echo "Usage: ticket-list.sh <department> [status] [--json]" >&2
  echo "Statuses: backlog, sprint-current, in-progress, in-review, done (omit for all)" >&2
  exit 1
fi

TICKET_DIR="$(ticket_dir "$DEPT")"

if [ -n "$STATUS" ] && [ "$STATUS" != "--json" ]; then
  STATUSES="$STATUS"
else
  STATUSES="backlog sprint-current in-progress in-review done"
fi

if [ "$JSON" = true ]; then
  FULL_VAR="$FULL" python3 -c "
import json, os, glob
full = os.environ.get('FULL_VAR') == 'true'

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
for status in '$STATUSES'.split():
    d = '$TICKET_DIR/' + status
    if not os.path.isdir(d):
        continue
    for f in sorted(glob.glob(d + '/*.json')):
        try:
            t = json.load(open(f))
            t['_status_dir'] = status
            t['_file'] = f
            tickets.append(t if full else summarize(t))
        except:
            pass

print(json.dumps({'department': '$DEPT', 'count': len(tickets), 'tickets': tickets, 'mode': 'full' if full else 'summary'}, indent=2))
"
else
  for status in $STATUSES; do
    dir="$TICKET_DIR/$status"
    [ -d "$dir" ] || continue
    count=$(ls "$dir"/*.json 2>/dev/null | wc -l)
    [ "$count" -eq 0 ] && continue
    echo "=== $status ($count) ==="
    for f in "$dir"/*.json; do
      [ -f "$f" ] || continue
      python3 -c "
import json
t = json.load(open('$f'))
pri = t.get('priority','?')
assignee = t.get('assignee',{})
name = assignee.get('name','Unassigned') if isinstance(assignee,dict) else str(assignee) if assignee else 'Unassigned'
print(f\"  {t.get('id','?'):15s} [{pri:8s}] {t.get('title','')[:60]:60s} -> {name}\")
" 2>/dev/null
    done
  done
fi
