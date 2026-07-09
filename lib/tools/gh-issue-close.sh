#!/usr/bin/env bash
set -uo pipefail

# gh-issue-close.sh — close a GitHub issue when its mirrored hive ticket
# reaches done. Called by ticket-move.sh.
#
# Usage: gh-issue-close.sh <issue-number> <ticket-id> [project-root]

ISSUE_NUM="${1:-}"
TICKET_ID="${2:-}"
PROJECT_ROOT="${3:-${PROJECT_ROOT:-$PWD}}"
[ -z "$ISSUE_NUM" ] || [ -z "$TICKET_ID" ] && {
  echo "Usage: gh-issue-close.sh <issue-number> <ticket-id> [project-root]" >&2
  exit 2
}
PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd)" || exit 0
SKILL_DIR="${HIVE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
COMPANY_DIR="${COMPANY_DIR:-$PROJECT_ROOT/.company}"

. "$SKILL_DIR/lib/tools/gh-helpers.sh"
gh_preflight 2>/tmp/gh-issue-close.preflight || {
  echo "[gh-issue-close] $(cat /tmp/gh-issue-close.preflight) — skipping issue #$ISSUE_NUM" >> "$COMPANY_DIR/logs/gh-issue-poll.log"
  exit 0
}

cd "$PROJECT_ROOT"

# Idempotent: if the issue is already closed, just refresh the mirror entry.
state=$(gh issue view "$ISSUE_NUM" --json state -q .state 2>/dev/null) || state=""
if [ "$state" = "OPEN" ]; then
  gh issue comment "$ISSUE_NUM" --body "✅ Hive ticket \`$TICKET_ID\` reached **done** — closing this issue. If the resolution is unsatisfactory, please reopen with a comment." >/dev/null 2>&1 || true
  gh issue close "$ISSUE_NUM" --reason completed >/dev/null 2>&1 \
    || echo "gh-issue-close: failed to close issue #$ISSUE_NUM" >&2
fi

# Update mirror sidecar
MIRROR="$COMPANY_DIR/integrations/github-mirror.json"
if [ -f "$MIRROR" ]; then
  NUM="$ISSUE_NUM" NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ) MIRROR="$MIRROR" python3 - <<'PYEOF' || true
import json, os
m = json.load(open(os.environ["MIRROR"]))
issues = m.setdefault("issues", {})
if os.environ["NUM"] in issues:
  issues[os.environ["NUM"]]["last_seen_state"] = "closed"
  issues[os.environ["NUM"]]["last_synced_at"]  = os.environ["NOW"]
tmp = os.environ["MIRROR"] + ".tmp"
json.dump(m, open(tmp, "w"), indent=2)
os.replace(tmp, os.environ["MIRROR"])
PYEOF
fi
