#!/bin/bash
# Read a single ticket's details without exposing file paths.
# Usage: ticket-get.sh <department> <ticket-id> [--json]
# Example: ticket-get.sh it IT-0056
# Example: ticket-get.sh it IT-0056 --json

source "$(dirname "$0")/ticket-paths.sh"

DEPT="$1"
TICKET_ID="$2"
JSON=false
[ "$3" = "--json" ] && JSON=true

if [ -z "$DEPT" ] || [ -z "$TICKET_ID" ]; then
  echo "Usage: ticket-get.sh <department> <ticket-id> [--json]" >&2
  exit 1
fi

TICKET_DIR="$(ticket_dir "$DEPT")"

# Find the ticket in any status directory
FOUND=""
FOUND_STATUS=""
for status in backlog sprint-current in-progress in-review done archive; do
  if [ -f "$TICKET_DIR/$status/$TICKET_ID.json" ]; then
    FOUND="$TICKET_DIR/$status/$TICKET_ID.json"
    FOUND_STATUS="$status"
    break
  fi
done

if [ -z "$FOUND" ]; then
  echo "Ticket $TICKET_ID not found in department $DEPT" >&2
  exit 1
fi

if [ "$JSON" = true ]; then
  cat "$FOUND"
else
  TG_FOUND="$FOUND" TG_STATUS="$FOUND_STATUS" python3 - <<'PYEOF' 2>/dev/null
import json, os
t = json.load(open(os.environ['TG_FOUND']))
print(f"Ticket: {t.get('id','?')}")
print(f"Status: {os.environ['TG_STATUS']}")
print(f"Title: {t.get('title','')}")
print(f"Type: {t.get('type','')}")
print(f"Priority: {t.get('priority','')}")
a = t.get('assignee', {})
name = a.get('name', 'Unassigned') if isinstance(a, dict) else str(a) if a else 'Unassigned'
print(f'Assignee: {name}')
print(f"Created: {t.get('created_at','')}")
print(f"Updated: {t.get('updated_at','')}")
print()
print('Description:')
print(t.get('description', '(none)'))
ac = t.get('acceptance_criteria', [])
if ac:
    print()
    print('Acceptance Criteria:')
    for i, c in enumerate(ac, 1):
        print(f'  {i}. {c}')
comments = t.get('comments', [])
if comments:
    print()
    print(f'Comments ({len(comments)}):')
    for c in comments:
        print(f"  [{c.get('timestamp','')}] {c.get('author','')}: {c.get('body','')}")
r = t.get('resolution', {})
if r and isinstance(r, dict) and r.get('status'):
    print()
    print(f"Resolution: {r.get('status','')} by {r.get('resolved_by','')} at {r.get('resolved_at','')}")
    if r.get('git_branch'): print(f"  Branch: {r['git_branch']}")
    if r.get('commit'): print(f"  Commit: {r['commit']}")
PYEOF
fi
