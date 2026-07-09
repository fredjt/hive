#!/usr/bin/env bash
set -uo pipefail

# stage-enqueue-vp-bugfix-plan.sh — STAGE 6b (Loop B).
# Enqueues 1 vp entry per dept that the CEO bugfix-direction lists.
# Each VP files bug-fix tickets (NOT new features) into BACKLOG.
#
# Usage: stage-enqueue-vp-bugfix-plan.sh <project-root>

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
BUGFIX_DIRECTION="$COMPANY_DIR/sprints/${SPRINT_ID}-bugfix-direction.json"

if [ ! -f "$BUGFIX_DIRECTION" ]; then
  echo "stage-enqueue-vp-bugfix-plan: no bugfix-direction at $BUGFIX_DIRECTION; nothing to enqueue" >&2
  exit 0
fi

# Affected depts = those with .departments.<dept>.bugs in the bugfix-direction
DEPTS=()
while IFS= read -r d; do
  [ -z "$d" ] && continue
  DEPTS+=("$d")
done < <(jq -r '.departments | to_entries[] | select(.value.bugs | length > 0) | .key' "$BUGFIX_DIRECTION" 2>/dev/null)

if [ ${#DEPTS[@]} -eq 0 ]; then
  echo "stage-enqueue-vp-bugfix-plan: no depts in bugfix-direction; nothing to enqueue"
  exit 0
fi

agent_for_dept() {
  case "$1" in
    hr) echo "vp-hr" ;; it) echo "vp-it" ;; rd) echo "vp-rd" ;;
    marketing) echo "vp-marketing" ;; legal) echo "vp-legal" ;; finance) echo "vp-finance" ;;
    *) echo "vp-dept" ;;
  esac
}

N=0
for dept in "${DEPTS[@]}"; do
  agent=$(agent_for_dept "$dept")
  agent_file="$SKILL_DIR/lib/agents/${agent}.md"
  [ -f "$agent_file" ] || continue

  PROMPT="$(load_agent_prompt "$agent_file")

--- STAGE 6b: vp-bugfix-plan (department: $dept) ---
Mode: vp-bugfix-plan. THIS IS BUG-FIX ONLY — no new features.

Read the bugfix-direction at: $BUGFIX_DIRECTION
Your dept's bugs are in .departments.\"$dept\".bugs.

Your job:
1. For each bug listed, file a ticket via ticket-create.sh INTO BACKLOG with:
   - title from the bug
   - type=bug
   - priority from the bug (critical/high/medium)
   - description including the failing test name / failure mode
2. If you believe a listed bug is a duplicate of an existing in-progress
   ticket, leave a comment on that ticket and skip filing.
3. Do NOT file feature tickets or chores. Bugs only.

You MUST NOT:
  - move tickets to sprint-current (sprint-fill does this next)
  - move tickets to in-progress / in-review / done with reason=implemented
  - touch PRs

After filing, EXIT. Stage 6c (sprint-fill) and then stage 3 (tdd-red) run
next."

  COMPANY_DIR="$COMPANY_DIR" SKILL_DIR="$SKILL_DIR" \
    bash "$SKILL_DIR/lib/tools/round-enqueue.sh" \
      --kind vp-bugfix-plan --agent "$agent" --dept "$dept" --role VP \
      --queued-by stage-advance \
      --log-to "$COMPANY_DIR/logs/vp-${dept}.log" \
      --prompt-inline-stdin <<<"$PROMPT"
  N=$((N + 1))
done

echo "stage-enqueue-vp-bugfix-plan: $N entries enqueued"
