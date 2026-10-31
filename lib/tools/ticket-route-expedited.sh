#!/bin/bash
# Usage: ticket-route-expedited.sh <type> <priority> <title> <description> <created_by> <target_department> [estimate]
# Example: ticket-route-expedited.sh bug critical "Database connection pool exhausted" "Production blocker..." "VP-Backend" "database" "medium"
#
# Expedited routing for HIGH/CRITICAL blocker tickets.
# Bypasses review queue, routes directly to target department board.
# CEO receives notification for tracking, not approval.
#
# CRITERIA FOR EXPEDITED ROUTING:
# - Priority: HIGH or CRITICAL
# - Type: bug or infrastructure (clear blockers)
# - Target department is known and valid
#
# This reduces critical blocker resolution from hours to minutes by eliminating
# the review bottleneck for clear infrastructure/blocker issues.

COMPANY_DIR="${COMPANY_DIR:-.company}"
SKILLS_DIR="${SKILLS_DIR:-$HIVE_ROOT/lib/agents}"

TYPE="$1"
PRIORITY="$2"
TITLE="$3"
DESC="$4"
CREATED_BY="$5"
TARGET_DEPT="$6"
ESTIMATE="${7:-medium}"

if [ -z "$TYPE" ] || [ -z "$PRIORITY" ] || [ -z "$TITLE" ] || [ -z "$CREATED_BY" ] || [ -z "$TARGET_DEPT" ]; then
  echo "Usage: ticket-route-expedited.sh <type> <priority> <title> <description> <created_by> <target_department> [estimate]" >&2
  echo "" >&2
  echo "Expedited routing for HIGH/CRITICAL blocker tickets." >&2
  echo "" >&2
  echo "Types: bug, infrastructure (clear blockers)" >&2
  echo "Priorities: critical, high" >&2
  echo "Estimate: small, medium, large, xl (default: medium)" >&2
  exit 1
fi

# Validate expedited routing criteria
PRIORITY_LOWER=$(echo "$PRIORITY" | tr '[:upper:]' '[:lower:]')
TYPE_LOWER=$(echo "$TYPE" | tr '[:upper:]' '[:lower:]')

if [ "$PRIORITY_LOWER" != "critical" ] && [ "$PRIORITY_LOWER" != "high" ]; then
  echo "ERROR: Expedited routing requires HIGH or CRITICAL priority" >&2
  echo "Got: $PRIORITY" >&2
  echo "Use standard ticket-create.sh for lower priorities" >&2
  exit 1
fi

if [ "$TYPE_LOWER" != "bug" ] && [ "$TYPE_LOWER" != "infrastructure" ]; then
  echo "ERROR: Expedited routing only for bug/infrastructure tickets" >&2
  echo "Got: $TYPE" >&2
  echo "Use standard ticket-create.sh for other types" >&2
  exit 1
fi

# Validate target department exists (env-var inputs; quotes/spaces in the
# path or dept name would break inline python interpolation)
if [ -f "$COMPANY_DIR/org-chart.json" ]; then
  VALID_DEPT=$(TE_OC_FILE="$COMPANY_DIR/org-chart.json" TE_TARGET="$TARGET_DEPT" python3 - <<'PYEOF' 2>/dev/null
import json, os, sys
try:
    oc=json.load(open(os.environ['TE_OC_FILE']))
    depts=list(oc.get('departments',{}).keys())
    target = os.environ['TE_TARGET']

    # Exact match
    if target in depts:
        print(target)
        sys.exit(0)

    # Case-insensitive match
    target_lower = target.lower()
    for d in depts:
        if d.lower() == target_lower:
            print(d)
            sys.exit(0)

    # Fuzzy substring match
    for d in depts:
        if target_lower in d.lower() or d.lower() in target_lower:
            print(d)
            sys.exit(0)

    print('')
except Exception as e:
    print('', file=sys.stderr)
    print(f'Error: {e}', file=sys.stderr)
PYEOF
)

  if [ -z "$VALID_DEPT" ]; then
    echo "ERROR: Target department '$TARGET_DEPT' not found in org-chart.json" >&2
    echo "Valid departments:" >&2
    TE_OC_FILE="$COMPANY_DIR/org-chart.json" python3 - <<'PYEOF' 2>/dev/null
import json, os
oc = json.load(open(os.environ['TE_OC_FILE']))
for d in oc.get('departments', {}).keys():
    print('  ' + d)
PYEOF
    exit 1
  fi

  if [ "$VALID_DEPT" != "$TARGET_DEPT" ]; then
    echo "CORRECTED: '$TARGET_DEPT' -> '$VALID_DEPT'" >&2
    TARGET_DEPT="$VALID_DEPT"
  fi
fi

# Use standard ticket-create.sh to create the ticket
# This ensures proper ID allocation and prevents duplicates
TICKET_ID=$(bash "$SKILLS_DIR/tools/ticket-create.sh" \
  "$TARGET_DEPT" \
  "$TYPE" \
  "$PRIORITY" \
  "$TITLE" \
  "$DESC" \
  "$CREATED_BY" \
  "expedited-routing" \
  "$ESTIMATE" 2>&1)

if [ $? -ne 0 ]; then
  echo "ERROR: Failed to create ticket: $TICKET_ID" >&2
  exit 1
fi

# Extract just the ticket ID (in case there were warnings)
TICKET_ID=$(echo "$TICKET_ID" | tail -1)

# Notify CEO for tracking (not approval)
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
CEO_NOTIFICATION="$COMPANY_DIR/logs/expedited-notice.md"

# Ensure file exists
touch "$CEO_NOTIFICATION"

# Append notification
cat >> "$CEO_NOTIFICATION" << EOF

---

## EXPEDITED TICKET ROUTING - $TIMESTAMP

**Ticket:** $TICKET_ID
**Priority:** $PRIORITY (EXPEDITED)
**Type:** $TYPE
**Department:** $TARGET_DEPT
**Filed by:** $CREATED_BY

**Title:** $TITLE

**Reason for Expedited Routing:**
High/critical priority blocker ticket bypassed review and was routed directly to $TARGET_DEPT board for immediate processing.

**Status:** Ticket created in backlog, ready for sprint assignment.

**Action Required:** NONE (notification only - department will process)

EOF

# Log to cycle log if it exists
if [ -f "$COMPANY_DIR/logs/cycle-log.md" ]; then
  echo "[$TIMESTAMP] EXPEDITED ROUTING: $TICKET_ID ($PRIORITY $TYPE) -> $TARGET_DEPT (by $CREATED_BY)" >> "$COMPANY_DIR/logs/cycle-log.md"
fi

# Output the ticket ID for the caller
echo "$TICKET_ID"
echo "EXPEDITED: Ticket routed directly to $TARGET_DEPT, CEO notified for tracking" >&2
