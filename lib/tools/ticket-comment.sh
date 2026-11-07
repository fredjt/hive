#!/bin/bash
# Usage: ticket-comment.sh <department> <ticket-id> <author> <comment>
# Example: ticket-comment.sh backend-api-services TICK-API-0015 "IC-Engineer" "Implementation complete, ready for review"
#
# G1/G2: the comment append is the state-layer op ticket-comment (locked,
# validated, atomic) — shared with the MCP ticket_comment tool.

COMPANY_DIR="${COMPANY_DIR:-.company}"
export COMPANY_DIR
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/hive-state.sh"

DEPT="${1:-}"
TICKET_ID="${2:-}"
AUTHOR="${3:-}"
shift 3
COMMENT="$*"

if [ -z "$DEPT" ] || [ -z "$TICKET_ID" ] || [ -z "$AUTHOR" ] || [ -z "$COMMENT" ]; then
  echo "Usage: ticket-comment.sh <department> <ticket-id> <author> <comment>" >&2
  exit 1
fi

RESULT=$(hs ticket-comment --dept "$DEPT" --ticket "$TICKET_ID" --author "$AUTHOR" --text "$COMMENT" 2>&1)
RC=$?
if [ $RC -ne 0 ]; then
  printf '%s\n' "$RESULT" >&2
  exit $RC
fi

echo "Comment added to $TICKET_ID by $AUTHOR"
