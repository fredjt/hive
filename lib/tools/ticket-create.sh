#!/bin/bash
# Usage: ticket-create.sh <department> <type> <priority> <title> <description> <created_by> <source> [estimate] [--labels "a,b"] [--ac "criterion per line"]
# Example: ticket-create.sh backend-api-services bug high "Fix rate limiting" "Rate limiter not configured" "VP-R&D" "rd-discovery" "medium"
#
# Creates a ticket in the department's backlog with all metadata filled in.
# Returns the ticket ID on stdout.
#
# The mutation itself lives in the state layer (lib/hive_state, op
# ticket-create): enum normalization/synonyms, auto-swap detection,
# dept resolution against org-chart, duplicate-title check, counter
# increment and ticket write — one locked, validated code path shared
# with the MCP ticket_create tool. This wrapper keeps the CLI contract.

COMPANY_DIR="${COMPANY_DIR:-.company}"
export COMPANY_DIR
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/hive-state.sh"

DEPT="${1:-}"
TYPE="${2:-}"
PRIORITY="${3:-}"
TITLE="${4:-}"
DESC="${5:-}"
CREATED_BY="${6:-user}"
SOURCE="${7:-manual}"
# Estimate is the OPTIONAL 8th positional; if the 8th arg is a flag
# (--labels / --ac / --external-id ...), there is no positional estimate.
ESTIMATE=""
if [ "${8:-}" != "" ] && [ "${8:0:2}" != "--" ]; then
  ESTIMATE="$8"
fi
shift 7 2>/dev/null || true
[ "${1:-}" != "" ] && [ "${1:0:2}" != "--" ] && shift   # consume the optional estimate positional

# Optional flags: --external-id <id> links the ticket to a foreign id
# (empty for native hive tickets); --labels "a,b" and --ac "line per
# criterion" populate the ticket's labels / acceptance_criteria.
EXTERNAL_ID=""
LABELS=""
AC=""
while [ $# -gt 0 ]; do
  case "$1" in
    --external-id) EXTERNAL_ID="$2"; shift 2 ;;
    --estimate)    ESTIMATE="$2"; shift 2 ;;
    --labels)      LABELS="$2"; shift 2 ;;
    --ac)          AC="$2"; shift 2 ;;
    *) shift ;;
  esac
done

if [ -z "$DEPT" ] || [ -z "$TYPE" ] || [ -z "$TITLE" ]; then
  echo "Usage: ticket-create.sh <department> <type> <priority> <title> <description> <created_by> <source> [estimate] [--external-id <id>] [--labels \"a,b\"] [--ac \"per line\"]" >&2
  echo "Types: feature, bug, improvement, chore, infrastructure, research" >&2
  echo "Priorities: critical, high, medium, low" >&2
  echo "Sources: sprint-planning, rd-discovery, qa-finding, user-request, bug-report, improvement, verification" >&2
  exit 1
fi
[ -z "$ESTIMATE" ] && ESTIMATE="medium"

RESULT=$(hs ticket-create --dept "$DEPT" --type "$TYPE" --priority "$PRIORITY" \
  --title "$TITLE" --desc "$DESC" --created-by "$CREATED_BY" --source "$SOURCE" \
  --estimate "$ESTIMATE" --labels "$LABELS" --ac "$AC" --external-id "$EXTERNAL_ID" 2>&1)
RC=$?
[ $RC -ne 0 ] && { echo "$RESULT" >&2; exit $RC; }

DUP=$(printf '%s' "$RESULT" | jq -r '.duplicate_of // empty' 2>/dev/null)
if [ -n "$DUP" ]; then
  echo "DUPLICATE: '$TITLE' already exists as $DUP" >&2
  echo "$DUP"
  exit 0
fi
TICKET_ID=$(printf '%s' "$RESULT" | jq -r '.ticket_id // empty' 2>/dev/null)
[ -z "$TICKET_ID" ] && { echo "$RESULT" >&2; exit 1; }
echo "$TICKET_ID"
