#!/usr/bin/env bash
set -uo pipefail

# stage-enqueue-release-verify.sh — STAGE 7.
# Enqueues 1 release-ic entry to do an adversarial global quality review
# of the build before publish. Reads sprint state for paths to test log,
# tarball, sha256.
#
# Usage: stage-enqueue-release-verify.sh <project-root>

PROJECT_ROOT="${1:-${PROJECT_ROOT:-$PWD}}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd)" || { echo "bad project root" >&2; exit 2; }
COMPANY_DIR="$PROJECT_ROOT/.company"
SKILL_DIR="${HIVE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
export COMPANY_DIR PROJECT_ROOT SKILL_DIR

# shellcheck disable=SC1091
. "$SKILL_DIR/lib/tools/sprint-state.sh"
# Load the canonical framework primer that prepends to every spawn prompt.
. "$SKILL_DIR/lib/tools/load-agent-prompt.sh"

VERSION=$(ss_get_field .release.version)
TEST_LOG=$(ss_get_field .release.test_log)
TARBALL=$(ss_get_field .release.tarball)
SHA256=$(ss_get_field .release.sha256)
VERIFY_LOG_PATH="$COMPANY_DIR/releases/${VERSION:-unversioned}-verify.log"
ss_set_release_field verify_log "$VERIFY_LOG_PATH"

RELEASE_IC_FILE="$SKILL_DIR/lib/agents/release-ic.md"
[ -f "$RELEASE_IC_FILE" ] || { echo "missing release-ic.md" >&2; exit 1; }

PROMPT="$(load_agent_prompt "$RELEASE_IC_FILE")

--- STAGE 7: release-verify ---
Mode: release-ic (adversarial global review).

You are reviewing the build before publish. Your job is to find ANY reason
this release should NOT ship.

Sprint id:    $(ss_get_field .sprint_id)
Version:      ${VERSION:-(none — release-runner did not set version)}
Tarball:      ${TARBALL:-(none yet)}
SHA256:       ${SHA256:-(none yet)}
Test log:     ${TEST_LOG:-(no test log)}

Steps (be thorough):
1. Read the test log. Sample 1-2 tests from it and re-run them to confirm
   they pass deterministically.
2. Extract the tarball into a tmp dir; run packaging/install.sh against
   the extract; run \`hive doctor\` (or equivalent project sanity command).
3. Verify the VERSION inside the extracted tarball == $VERSION.
4. Recompute sha256 on the tarball; verify it matches the .sha256 sidecar.
5. Compare the changelog/release-notes to the merged PRs since the last
   tag. Use:
     prev_tag=\$(git -C $PROJECT_ROOT describe --tags --abbrev=0 2>/dev/null)
     prev_date=\$(git -C $PROJECT_ROOT log -1 --format=%cI \"\$prev_tag\" 2>/dev/null)
     gh pr list --state merged --base main --search \"merged:>\$prev_date\"
   Flag mismatches (PRs that landed but aren't in the changelog).
6. Try the documented happy path from the project README. If it fails, NO-GO.

You MUST NOT call gh-pr-* or release-publish-runner. Verdict only.

Write your verdict to: $VERIFY_LOG_PATH
Format:
  VERDICT: GO|NO-GO
  RATIONALE:
  <one paragraph explaining your call. Reference specific tests / commands
   you ran. If NO-GO, list each blocker as a bullet so ceo-bugfix-plan can
   ticketize them.>

After writing the verdict, EXIT. Stage 8 (release-publish) reads the
verdict; on NO-GO it advances to ceo-bugfix-plan."

COMPANY_DIR="$COMPANY_DIR" SKILL_DIR="$SKILL_DIR" \
  bash "$SKILL_DIR/lib/tools/round-enqueue.sh" \
    --kind release-verify --agent release-ic --dept executive --role Release-IC \
    --queued-by stage-advance \
    --log-to "$COMPANY_DIR/logs/release-ic.log" \
    --prompt-inline-stdin <<<"$PROMPT"

echo "stage-enqueue-release-verify: 1 entry enqueued"
