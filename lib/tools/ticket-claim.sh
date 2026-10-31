#!/bin/bash
# Usage: ticket-claim.sh <department> <ticket-id> <role> <agent-id> <agent-name> [worktree-path]
# Example: ticket-claim.sh backend-api-services TICK-API-0015 IC-Engineer ic-api-0015 "IC-Engineer (backend/TICK-API-0015)" /path/.worktrees/TICK-API-0015
#
# Claims a ticket: sets assignee, links git branch, moves to in-progress.

COMPANY_DIR="${COMPANY_DIR:-.company}"
source "$(dirname "$0")/ticket-paths.sh"

DEPT="$1"
TICKET_ID="$2"
ROLE="$3"
AGENT_ID="$4"
AGENT_NAME="$5"
WORKTREE_PATH="$6"

if [ -z "$DEPT" ] || [ -z "$TICKET_ID" ] || [ -z "$ROLE" ] || [ -z "$AGENT_NAME" ]; then
  echo "Usage: ticket-claim.sh <department> <ticket-id> <role> <agent-id> <agent-name> [worktree-path]" >&2
  exit 1
fi

TICKET_DIR="$(ticket_dir "$DEPT")"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Find the ticket
FOUND=""
for status in backlog sprint-current in-progress in-review done; do
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

# Block claiming tickets that are already done or archived
if [ "$FOUND_STATUS" = "done" ] || [ "$FOUND_STATUS" = "archive" ]; then
  echo "ERROR: Cannot claim $TICKET_ID - it is already in $FOUND_STATUS." >&2
  exit 1
fi

# Auto-detect git branch and worktree if not provided
PROJECT_ROOT=$(cd "$COMPANY_DIR/.." 2>/dev/null && pwd)
GIT_BRANCH="feature/$TICKET_ID"

if [ -z "$WORKTREE_PATH" ] && [ -d "$PROJECT_ROOT/.worktrees/$TICKET_ID" ]; then
  WORKTREE_PATH="$PROJECT_ROOT/.worktrees/$TICKET_ID"
fi

mkdir -p "$TICKET_DIR/in-progress"
DEST="$TICKET_DIR/in-progress/$TICKET_ID.json"

# Update assignee, link git branch, move to in-progress.
# CRITICAL: inputs come in via environment vars and the heredoc is
# single-quoted, so bash does NOT interpolate into the python source. Any
# character in $AGENT_NAME / $ROLE / $AGENT_ID / paths (quote, backslash,
# newline) is safe. The previous form interpolated '''$AGENT_NAME''' inline
# — a quote in the name raised SyntaxError, and because the script never
# checked python's exit status, the unconditional rm below then DELETED
# the ticket while still reporting success.
TC_FOUND="$FOUND" \
TC_DEST="$DEST" \
TC_ROLE="$ROLE" \
TC_AGENT_ID="$AGENT_ID" \
TC_AGENT_NAME="$AGENT_NAME" \
TC_TIMESTAMP="$TIMESTAMP" \
TC_GIT_BRANCH="$GIT_BRANCH" \
TC_WORKTREE_PATH="${WORKTREE_PATH:-}" \
python3 - <<'PYEOF'
import json, os
FOUND        = os.environ['TC_FOUND']
DEST         = os.environ['TC_DEST']
ROLE         = os.environ['TC_ROLE']
AGENT_ID     = os.environ.get('TC_AGENT_ID', '')
AGENT_NAME   = os.environ['TC_AGENT_NAME']
TIMESTAMP    = os.environ['TC_TIMESTAMP']
GIT_BRANCH   = os.environ.get('TC_GIT_BRANCH', '')
WORKTREE     = os.environ.get('TC_WORKTREE_PATH', '')

ticket = json.load(open(FOUND))
ticket['assignee'] = {'role': ROLE, 'agent_id': AGENT_ID, 'name': AGENT_NAME}
ticket['status'] = 'in-progress'
ticket['updated_at'] = TIMESTAMP

# Link git branch at claim time
if not ticket.get('resolution') or ticket['resolution'] is None:
    ticket['resolution'] = {}
if GIT_BRANCH:
    ticket['resolution']['git_branch'] = GIT_BRANCH
if WORKTREE:
    ticket['resolution']['worktree'] = WORKTREE
ticket['resolution']['started_at'] = TIMESTAMP

# Merge comments from destination if it exists (prevents comment loss)
if os.path.exists(DEST) and DEST != FOUND:
    try:
        existing = json.load(open(DEST))
        existing_comments = existing.get('comments') or []
        current_comments = ticket.get('comments') or []
        seen = set()
        merged = []
        for c in existing_comments + current_comments:
            key = (c.get('timestamp',''), c.get('author',''), c.get('body','')[:100])
            if key not in seen:
                seen.add(key)
                merged.append(c)
        merged.sort(key=lambda x: x.get('timestamp',''))
        ticket['comments'] = merged
    except Exception: pass

json.dump(ticket, open(DEST, 'w'), indent=2)
PYEOF

# Guard: a python failure above must never delete the ticket. Only remove
# the source file if the destination file was actually written.
if [ ! -f "$DEST" ]; then
  echo "ERROR: claim failed for $TICKET_ID — destination $DEST was not written; source ticket left in place." >&2
  exit 1
fi
if [ "$FOUND" != "$DEST" ] && [ -f "$FOUND" ]; then
  rm "$FOUND"
fi

# Clean up any duplicate copies in OTHER directories
for dup_status in backlog sprint-current in-progress in-review done; do
  dup_file="$TICKET_DIR/$dup_status/$TICKET_ID.json"
  if [ "$dup_status" != "in-progress" ] && [ -f "$dup_file" ]; then
    rm "$dup_file"
  fi
done

echo "Claimed $TICKET_ID by $AGENT_NAME ($ROLE) -> in-progress (branch: $GIT_BRANCH)"
