#!/usr/bin/env bash
set -uo pipefail

# stage-enqueue-ceo-plan.sh — STAGE 1.
# Enqueues a single CEO entry whose prompt asks the CEO to write a per-dept
# direction document for this sprint via sprint-direction-write.sh.
#
# Usage: stage-enqueue-ceo-plan.sh <project-root>

PROJECT_ROOT="${1:-${PROJECT_ROOT:-$PWD}}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd)" || { echo "bad project root" >&2; exit 2; }
COMPANY_DIR="$PROJECT_ROOT/.company"
SKILL_DIR="${HIVE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
export COMPANY_DIR PROJECT_ROOT SKILL_DIR
# Load the canonical framework primer that prepends to every spawn prompt.
. "$SKILL_DIR/lib/tools/load-agent-prompt.sh"

CEO_AGENT_FILE="$SKILL_DIR/lib/agents/ceo.md"
[ -f "$CEO_AGENT_FILE" ] || { echo "missing ceo.md" >&2; exit 1; }

# Build the dept list dynamically from org-chart + the 6 fixed admin depts.
DEPTS=(hr it rd marketing legal finance)
if [ -f "$COMPANY_DIR/org-chart.json" ]; then
  while IFS= read -r d; do
    [ -z "$d" ] && continue
    case " ${DEPTS[*]} " in *" $d "*) ;; *) DEPTS+=("$d") ;; esac
  done < <(jq -r '.departments | to_entries[]? | select(.value.type=="dynamic") | .key' "$COMPANY_DIR/org-chart.json" 2>/dev/null)
fi
DEPT_LIST=$(printf '%s, ' "${DEPTS[@]}" | sed 's/, $//')

PROMPT="$(load_agent_prompt "$CEO_AGENT_FILE")

--- STAGE 1: ceo-plan ---
Mode: ceo-plan (sprint planning).

Read these inputs to ground your direction:
  .company/chat/leadership.md
  .company/chat/general.md
  .company/chat/incidents.md
  .company/sprints/history/  (last 1-2 sprint review reports if they exist)
  .company/departments/*/status.json
  .company/departments/*/tickets/done/   (recent completions)

Write a per-dept direction JSON for this sprint and pipe it through:
  bash \$SKILL_DIR/lib/tools/sprint-direction-write.sh

Departments to address: $DEPT_LIST

Schema (pipe to sprint-direction-write.sh on stdin):
{
  \"rationale\": \"<2-4 sentences: what is this sprint about?>\",
  \"departments\": {
    \"<dept>\": {
      \"direction\": \"<paragraph: focus area for this dept this sprint>\",
      \"seed_tickets\": [
        {\"title\":\"...\",\"type\":\"feature|bug|chore\",\"priority\":\"critical|high|medium|low\",\"description\":\"...\"}
      ]
    }
  }
}

seed_tickets is optional — VPs will file additional tickets in stage 2.

After writing the direction file, EXIT. Do NOT spawn ICs, do NOT move tickets,
do NOT touch PRs, do NOT call any company lifecycle MCP tool. Stage 2 (vp-plan)
runs next automatically."

COMPANY_DIR="$COMPANY_DIR" SKILL_DIR="$SKILL_DIR" \
  bash "$SKILL_DIR/lib/tools/round-enqueue.sh" \
    --kind ceo-plan --agent ceo --dept executive --role CEO \
    --queued-by stage-advance \
    --log-to "$COMPANY_DIR/logs/ceo.log" \
    --prompt-inline-stdin <<<"$PROMPT"

echo "stage-enqueue-ceo-plan: 1 entry enqueued"
