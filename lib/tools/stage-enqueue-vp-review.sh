#!/usr/bin/env bash
set -uo pipefail

# stage-enqueue-vp-review.sh — STAGE 9 (post-publish).
# Enqueues 1 vp entry per dept. Each VP writes a post-sprint report including
# all bug-fix work that landed in the release.
#
# Usage: stage-enqueue-vp-review.sh <project-root>

PROJECT_ROOT="${1:-${PROJECT_ROOT:-$PWD}}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd)" || { echo "bad project root" >&2; exit 2; }
COMPANY_DIR="$PROJECT_ROOT/.company"
SKILL_DIR="${HIVE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
export COMPANY_DIR PROJECT_ROOT SKILL_DIR

# shellcheck disable=SC1091
. "$SKILL_DIR/lib/tools/sprint-state.sh"
# Load the canonical framework primer that prepends to every spawn prompt.
. "$SKILL_DIR/lib/tools/load-agent-prompt.sh"
SPRINT_ID=$(ss_get_field .sprint_id)
VERSION=$(ss_get_field .release.version)
PUBLISH_STATUS=$(ss_get_field .release.publish_status)

DEPTS=(hr it rd marketing legal finance)
if [ -f "$COMPANY_DIR/org-chart.json" ]; then
  while IFS= read -r d; do
    [ -z "$d" ] && continue
    case " ${DEPTS[*]} " in *" $d "*) ;; *) DEPTS+=("$d") ;; esac
  done < <(jq -r '.departments | to_entries[]? | select(.value.type=="dynamic") | .key' "$COMPANY_DIR/org-chart.json" 2>/dev/null)
fi

agent_for_dept() {
  case "$1" in
    hr) echo "vp-hr" ;; it) echo "vp-it" ;; rd) echo "vp-rd" ;;
    marketing) echo "vp-marketing" ;; legal) echo "vp-legal" ;; finance) echo "vp-finance" ;;
    *) echo "vp-dept" ;;
  esac
}

REPORT_DIR="$COMPANY_DIR/meetings/sprint-reviews"
mkdir -p "$REPORT_DIR"

N=0
for dept in "${DEPTS[@]}"; do
  agent=$(agent_for_dept "$dept")
  agent_file="$SKILL_DIR/lib/agents/${agent}.md"
  [ -f "$agent_file" ] || continue
  REPORT_PATH="$REPORT_DIR/${SPRINT_ID}-${dept}.md"

  PROMPT="$(load_agent_prompt "$agent_file")

--- STAGE 9: vp-review (department: $dept) ---
Mode: vp-review.

This is post-release. Sprint id: $SPRINT_ID
Release status: $PUBLISH_STATUS  (version: ${VERSION:-none})

Write the dept's sprint review report to:
  $REPORT_PATH

Format (~400 words max):

# $dept sprint review — $SPRINT_ID

## What shipped
List tickets your dept completed this sprint (look in tickets/done/ files
modified during this sprint). Group: features, bug-fixes (separately because
the bug-fix sub-cycle ran), rejected tickets.

## What didn't ship
Tickets bounced multiple times, system-rejected at loop-cap, blocked on
external deps. Why.

## Release outcome
${VERSION:+Release v$VERSION publish_status=$PUBLISH_STATUS.}
If publish failed: what blocked it; whether retries exhausted; what should
change next sprint to avoid the same failure.

## Signals to leadership
What you noticed mid-sprint that the CEO should consider for next sprint's
direction. Be specific (1-3 bullets).

After writing, EXIT. The sprint will archive automatically and a fresh
ceo-plan starts."

  COMPANY_DIR="$COMPANY_DIR" SKILL_DIR="$SKILL_DIR" \
    bash "$SKILL_DIR/lib/tools/round-enqueue.sh" \
      --kind vp-review --agent "$agent" --dept "$dept" --role VP \
      --queued-by stage-advance \
      --log-to "$COMPANY_DIR/logs/vp-${dept}.log" \
      --prompt-inline-stdin <<<"$PROMPT"
  N=$((N + 1))
done

echo "stage-enqueue-vp-review: $N entries enqueued"
