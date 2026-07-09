#!/usr/bin/env bash
set -uo pipefail

# stage-enqueue-ceo-bugfix-plan.sh — STAGE 6a (Loop B entry).
# Fires when stage 6 (build-and-test) or stage 7 (release-verify) failed.
# CEO reads the failure log(s) from sprint state and writes a per-dept
# bug-list to <sprint>-bugfix-direction.json.
#
# Usage: stage-enqueue-ceo-bugfix-plan.sh <project-root>

PROJECT_ROOT="${1:-${PROJECT_ROOT:-$PWD}}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd)" || { echo "bad project root" >&2; exit 2; }
COMPANY_DIR="$PROJECT_ROOT/.company"
SKILL_DIR="${HIVE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
export COMPANY_DIR PROJECT_ROOT SKILL_DIR

# shellcheck disable=SC1091
. "$SKILL_DIR/lib/tools/sprint-state.sh"
# Load the canonical framework primer that prepends to every spawn prompt.
. "$SKILL_DIR/lib/tools/load-agent-prompt.sh"

CEO_AGENT_FILE="$SKILL_DIR/lib/agents/ceo.md"
[ -f "$CEO_AGENT_FILE" ] || { echo "missing ceo.md" >&2; exit 1; }

TEST_LOG=$(ss_get_field .release.test_log)
VERIFY_LOG=$(ss_get_field .release.verify_log)
RETRY=$(ss_get_field .release.retry_count 2>/dev/null || echo 0)
[ -z "$RETRY" ] && RETRY=0

PROMPT="$(load_agent_prompt "$CEO_AGENT_FILE")

--- STAGE 6a: ceo-bugfix-plan ---
Mode: ceo-bugfix-plan.

The release pipeline failed. Read the failure log(s) and write a per-dept
bug-fix list. THIS IS BUGS-ONLY. Do NOT add new features.

Failure logs:
  Test suite (stage 6):     ${TEST_LOG:-none}
  Adversarial review (st 7):${VERIFY_LOG:-none}

Retry attempt: $RETRY (cap is configured in [release].max_release_retries).

Pipe a JSON document through:
  bash \$SKILL_DIR/lib/tools/sprint-direction-write.sh --bugfix

Schema:
{
  \"rationale\": \"<why are we in bugfix mode? 1-2 sentences summarizing the failure.>\",
  \"departments\": {
    \"<dept>\": {
      \"bugs\": [
        {\"title\":\"...\",\"priority\":\"critical|high|medium\",\"description\":\"<concrete fix needed; reference the failing test or rationale>\"}
      ]
    }
  }
}

Only include departments that own code in the failure path. Empty deptss:
omit them.

After writing, EXIT. Stage 6b (vp-bugfix-plan) runs next."

COMPANY_DIR="$COMPANY_DIR" SKILL_DIR="$SKILL_DIR" \
  bash "$SKILL_DIR/lib/tools/round-enqueue.sh" \
    --kind ceo-bugfix-plan --agent ceo --dept executive --role CEO \
    --queued-by stage-advance \
    --log-to "$COMPANY_DIR/logs/ceo.log" \
    --prompt-inline-stdin <<<"$PROMPT"

echo "stage-enqueue-ceo-bugfix-plan: 1 entry enqueued"
