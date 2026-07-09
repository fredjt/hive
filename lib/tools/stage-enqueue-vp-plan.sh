#!/usr/bin/env bash
set -uo pipefail

# stage-enqueue-vp-plan.sh — STAGE 2.
# Enqueues 1 vp entry per dept. Each VP reads the CEO direction document,
# files tickets into BACKLOG (NOT sprint-current — sprint-fill does that next).
#
# Usage: stage-enqueue-vp-plan.sh <project-root>

PROJECT_ROOT="${1:-${PROJECT_ROOT:-$PWD}}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd)" || { echo "bad project root" >&2; exit 2; }
COMPANY_DIR="$PROJECT_ROOT/.company"
SKILL_DIR="${HIVE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
export COMPANY_DIR PROJECT_ROOT SKILL_DIR

# shellcheck disable=SC1091
. "$SKILL_DIR/lib/tools/sprint-state.sh"
# Load the canonical framework primer that prepends to every spawn prompt.
. "$SKILL_DIR/lib/tools/load-agent-prompt.sh"
DIRECTION=$(ss_get_field .ceo_direction_path)
[ -z "$DIRECTION" ] && DIRECTION="(no direction document was written by CEO; act on prior sprint context)"

DEPTS=(hr it rd marketing legal finance)
if [ -f "$COMPANY_DIR/org-chart.json" ]; then
  while IFS= read -r d; do
    [ -z "$d" ] && continue
    case " ${DEPTS[*]} " in *" $d "*) ;; *) DEPTS+=("$d") ;; esac
  done < <(jq -r '.departments | to_entries[]? | select(.value.type=="dynamic") | .key' "$COMPANY_DIR/org-chart.json" 2>/dev/null)
fi

agent_for_dept() {
  case "$1" in
    hr) echo "vp-hr" ;;
    it) echo "vp-it" ;;
    rd) echo "vp-rd" ;;
    marketing) echo "vp-marketing" ;;
    legal) echo "vp-legal" ;;
    finance) echo "vp-finance" ;;
    *) echo "vp-dept" ;;
  esac
}

N=0
for dept in "${DEPTS[@]}"; do
  agent=$(agent_for_dept "$dept")
  agent_file="$SKILL_DIR/lib/agents/${agent}.md"
  [ -f "$agent_file" ] || { echo "skipping $dept: missing $agent_file"; continue; }

  PROMPT="$(load_agent_prompt "$agent_file")

--- STAGE 2: vp-plan (department: $dept) ---
Mode: vp-plan.

Read the CEO direction at: $DIRECTION
(That file has a .departments.\"$dept\" section if the CEO addressed your dept.)

Your job this turn:
1. Read your dept's direction + your existing backlog.
2. File tickets via ticket-create.sh INTO BACKLOG (not sprint-current — the
   sprint-fill script promotes tickets after you finish).
3. Refine any backlog tickets that the CEO's direction implies need updating.
4. If you decide an existing backlog ticket is no longer worth doing, you
   may reject it (move to done with reason=rejected): only allowed during
   vp-plan/vp-review/ceo-bugfix-plan/vp-bugfix-plan stages.

You MUST NOT:
  - move tickets to sprint-current (sprint-fill does this)
  - move tickets to in-progress / in-review / done with reason=implemented
  - touch PRs (PR creation is automatic when ICs move tickets to in-review)
  - spawn any ICs (the round-runner does this stage-by-stage)
  - call any company lifecycle MCP tool

After filing/refining, EXIT. Stage 2.5 (sprint-fill) and then stage 3 (tdd-red)
run next automatically."

  COMPANY_DIR="$COMPANY_DIR" SKILL_DIR="$SKILL_DIR" \
    bash "$SKILL_DIR/lib/tools/round-enqueue.sh" \
      --kind vp-plan --agent "$agent" --dept "$dept" --role VP \
      --queued-by stage-advance \
      --log-to "$COMPANY_DIR/logs/vp-${dept}.log" \
      --prompt-inline-stdin <<<"$PROMPT"
  N=$((N + 1))
done

echo "stage-enqueue-vp-plan: $N entries enqueued"
