#!/usr/bin/env bash
set -uo pipefail

# gh-issue-poll.sh — mirror open GitHub issues into hive tickets.
# Run by round-promote.sh between rounds.
#
# Routing: labels first (`dept:<x>`, `type:<y>`, `priority:<z>`), then
# tiny-LLM classification for any unset value.
# Dedupe: .company/integrations/github-mirror.json
# Comment back on the issue with the new ticket id.

PROJECT_ROOT="${1:-${PROJECT_ROOT:-$PWD}}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd)" || exit 0
SKILL_DIR="${HIVE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
COMPANY_DIR="$PROJECT_ROOT/.company"
[ -d "$COMPANY_DIR" ] || exit 0

. "$SKILL_DIR/lib/tools/gh-helpers.sh"
gh_preflight 2>/tmp/gh-issue-poll.preflight || {
  echo "[gh-issue-poll] $(cat /tmp/gh-issue-poll.preflight) — skipping" >> "$COMPANY_DIR/logs/gh-issue-poll.log"
  exit 0
}

MIRROR="$COMPANY_DIR/integrations/github-mirror.json"
LOG="$COMPANY_DIR/logs/gh-issue-poll.log"
mkdir -p "$(dirname "$MIRROR")" "$(dirname "$LOG")"
[ -f "$MIRROR" ] || echo '{"issues":{},"prs":{}}' > "$MIRROR"

# Load configurable label prefixes (default to dept:/type:/priority:)
DEPT_PREFIX="${HIVE_GH_LABEL_DEPT:-dept:}"
TYPE_PREFIX="${HIVE_GH_LABEL_TYPE:-type:}"
PRI_PREFIX="${HIVE_GH_LABEL_PRIORITY:-priority:}"

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

cd "$PROJECT_ROOT"

# Fetch all open issues — exclude PRs (gh `issue list` already does)
RAW=$(gh issue list --state open --limit 200 --json number,title,body,labels,author,createdAt 2>/dev/null) || {
  log "gh issue list failed"
  exit 0
}

mirrored=0; skipped=0; failed=0

# Iterate each issue
echo "$RAW" | jq -c '.[]' | while IFS= read -r issue; do
  num=$(echo "$issue" | jq -r '.number')
  title=$(echo "$issue" | jq -r '.title // ""')
  body=$(echo "$issue" | jq -r '.body // ""')
  author=$(echo "$issue" | jq -r '.author.login // "unknown"')
  labels=$(echo "$issue" | jq -r '[.labels[].name] | join(",")')

  # Skip if already mirrored
  if jq -e --arg n "$num" '.issues[$n]' "$MIRROR" >/dev/null 2>&1; then
    skipped=$((skipped + 1))
    continue
  fi

  # Resolve dept/type/priority from labels
  dept=$(echo "$labels" | tr ',' '\n' | grep -i "^${DEPT_PREFIX}" | head -1 | sed "s/^${DEPT_PREFIX}//I")
  type=$(echo "$labels" | tr ',' '\n' | grep -i "^${TYPE_PREFIX}" | head -1 | sed "s/^${TYPE_PREFIX}//I")
  pri=$( echo "$labels" | tr ',' '\n' | grep -i "^${PRI_PREFIX}"  | head -1 | sed "s/^${PRI_PREFIX}//I")

  # Local keyword-based classification for missing fields
  if [ -z "$dept" ] || [ -z "$type" ]; then
    if [ -x "$SKILL_DIR/lib/tools/classify-ticket.sh" ]; then
      cls=$(COMPANY_DIR="$COMPANY_DIR" bash "$SKILL_DIR/lib/tools/classify-ticket.sh" "$title

$body" 2>/dev/null) || cls=""
      if [ -n "$cls" ]; then
        [ -z "$dept" ] && dept=$(echo "$cls" | jq -r '.department // ""' 2>/dev/null)
        [ -z "$type" ] && type=$(echo "$cls" | jq -r '.type // ""' 2>/dev/null)
      fi
    fi
  fi

  # Defaults if still unknown
  [ -z "$dept" ] || [ "$dept" = "unknown" ] && dept="it"
  [ -z "$type" ] && type="feature"
  [ -z "$pri" ] && pri="medium"

  # Make sure dept board exists
  if [ ! -d "$COMPANY_DIR/departments/$dept" ]; then
    log "issue #$num: dept '$dept' has no board, routing to it"
    dept="it"
  fi

  log "issue #$num: routing to $dept (type=$type priority=$pri labels='$labels')"

  # Create the ticket
  desc="$body

---
Mirrored from GitHub issue: #$num by @$author"
  ticket_id=$(COMPANY_DIR="$COMPANY_DIR" bash "$SKILL_DIR/lib/tools/ticket-create.sh" \
    "$dept" "$type" "$pri" "$title" "$desc" "github-issue-$author" "github-issue" \
    --external-id "gh-issue-$num" 2>>"$LOG") || {
    log "ticket-create failed for issue #$num"
    failed=$((failed + 1))
    continue
  }
  ticket_id=$(echo "$ticket_id" | tail -1 | tr -d '[:space:]')

  # Comment back on the GH issue
  gh issue comment "$num" --body "🐝 Mirrored as hive ticket \`$ticket_id\` on the **$dept** board. Status updates will be posted here as work progresses." \
    >/dev/null 2>>"$LOG" || log "gh issue comment failed for #$num"

  # Record in mirror
  NUM="$num" TICKET_ID="$ticket_id" NOW="$(ts)" MIRROR="$MIRROR" python3 - <<'PYEOF' || true
import json, os
m = json.load(open(os.environ["MIRROR"]))
m.setdefault("issues", {})[os.environ["NUM"]] = {
  "ticket_id":       os.environ["TICKET_ID"],
  "mirrored_at":     os.environ["NOW"],
  "last_seen_state": "open",
  "last_synced_at":  os.environ["NOW"],
}
tmp = os.environ["MIRROR"] + ".tmp"
json.dump(m, open(tmp, "w"), indent=2)
os.replace(tmp, os.environ["MIRROR"])
PYEOF
  mirrored=$((mirrored + 1))
done

log "pass complete: mirrored=$mirrored skipped=$skipped failed=$failed"
