#!/bin/bash
# Usage: ticket-comment.sh <department> <ticket-id> <author> <comment>
# Example: ticket-comment.sh backend-api-services TICK-API-0015 "IC-Engineer" "Implementation complete, ready for review"

COMPANY_DIR="${COMPANY_DIR:-.company}"
source "$(dirname "$0")/ticket-paths.sh"

DEPT="$1"
TICKET_ID="$2"
AUTHOR="$3"
shift 3
COMMENT="$*"

if [ -z "$DEPT" ] || [ -z "$TICKET_ID" ] || [ -z "$AUTHOR" ] || [ -z "$COMMENT" ]; then
  echo "Usage: ticket-comment.sh <department> <ticket-id> <author> <comment>" >&2
  exit 1
fi

TICKET_DIR="$(ticket_dir "$DEPT")"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Find the ticket
FOUND=""
for status in backlog sprint-current in-progress in-review done; do
  if [ -f "$TICKET_DIR/$status/$TICKET_ID.json" ]; then
    FOUND="$TICKET_DIR/$status/$TICKET_ID.json"
    break
  fi
done

if [ -z "$FOUND" ]; then
  echo "Ticket $TICKET_ID not found in department $DEPT" >&2
  exit 1
fi

# Inputs via env vars (not inline interpolation) so any character in $COMMENT
# — single quote, double quote, backtick, backslash, triple-quote, newline —
# is safe. The previous form interpolated `'''$COMMENT'''` into python source
# inside a double-quoted `python3 -c "..."` block; a trailing single quote in
# the comment produced four consecutive quotes which closes the triple-quoted
# literal early and raises SyntaxError (the destructive version of the same
# bug also bit ticket-move.sh — there it deleted the ticket; here it loses
# the comment silently).
TC_FOUND="$FOUND" TC_AUTHOR="$AUTHOR" TC_TIMESTAMP="$TIMESTAMP" \
TC_COMMENT="$COMMENT" \
python3 - <<'PYEOF' || { echo "ticket-comment: python step failed; comment NOT recorded on $TC_FOUND" >&2; exit 1; }
import json, os
FOUND     = os.environ['TC_FOUND']
AUTHOR    = os.environ['TC_AUTHOR']
TIMESTAMP = os.environ['TC_TIMESTAMP']
COMMENT   = os.environ.get('TC_COMMENT', '')

ticket = json.load(open(FOUND))
if 'comments' not in ticket or ticket['comments'] is None:
    ticket['comments'] = []
ticket['comments'].append({
    'author': AUTHOR,
    'timestamp': TIMESTAMP,
    'body': COMMENT,
})
ticket['updated_at'] = TIMESTAMP

# Atomic write: tmp + rename so a crash mid-write doesn't truncate the ticket.
tmp = FOUND + '.tmp.' + str(os.getpid())
with open(tmp, 'w') as fh:
    json.dump(ticket, fh, indent=2)
os.replace(tmp, FOUND)
PYEOF

echo "Comment added to $TICKET_ID by $AUTHOR"
