#!/usr/bin/env bash
set -uo pipefail

# stage-enqueue-tdd-red.sh — STAGE 3.
# Enqueues 1 ic-cycle-tester entry per sprint-current ticket that:
#   - does NOT yet have a [TDD-RED] commit on its feature branch
#   - is NOT flagged with `skip_tdd_red: true` (verification IC sets this when
#     "tests are fine, code is wrong" — bypass to TDD-GREEN next loop)
#
# Replaces the testing half of the old ic-spawner.sh.
#
# Usage: stage-enqueue-tdd-red.sh <project-root>

PROJECT_ROOT="${1:-${PROJECT_ROOT:-$PWD}}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd)" || { echo "bad project root" >&2; exit 2; }
COMPANY_DIR="$PROJECT_ROOT/.company"
SKILL_DIR="${HIVE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
export COMPANY_DIR PROJECT_ROOT SKILL_DIR

# shellcheck disable=SC1091
. "$SKILL_DIR/lib/tools/dept-helpers.sh"
# Load the canonical framework primer that prepends to every spawn prompt.
. "$SKILL_DIR/lib/tools/load-agent-prompt.sh"

# Detect "fresh project" — first ic-tester on this project sets up the
# test framework (npm init / pytest skel / etc.). The hint flag is added
# to the prompt when no test framework signal exists yet.
FRESH=0
if   [ -f "$PROJECT_ROOT/package.json" ] && jq -e '.scripts.test' "$PROJECT_ROOT/package.json" >/dev/null 2>&1; then :
elif [ -f "$PROJECT_ROOT/pyproject.toml" ] || [ -f "$PROJECT_ROOT/pytest.ini" ] || [ -f "$PROJECT_ROOT/setup.cfg" ]; then :
elif [ -f "$PROJECT_ROOT/Cargo.toml" ] || [ -f "$PROJECT_ROOT/go.mod" ]; then :
else FRESH=1
fi

TESTER_AGENT_FILE="$SKILL_DIR/lib/agents/ic-tester.md"
[ -f "$TESTER_AGENT_FILE" ] || { echo "missing ic-tester.md" >&2; exit 1; }

N=0
# Scan BOTH sprint-current and in-progress. A ticket in in-progress
# without [TDD-RED] is an orphan from a previous failed tester turn —
# re-enqueue it. A ticket in sprint-current WITH [TDD-RED] is a
# reconciliation case — promote it to in-progress so tdd-green can
# pick it up next stage.
for ticket_file in "$COMPANY_DIR"/departments/*/tickets/sprint-current/*.json \
                   "$COMPANY_DIR"/departments/*/tickets/in-progress/*.json; do
  [ -f "$ticket_file" ] || continue
  TID=$(jq -r '.id // empty' "$ticket_file")
  [ -z "$TID" ] && continue

  DEPT=$(echo "$ticket_file" | sed -n 's|.*/departments/\([^/]*\)/tickets/.*|\1|p')
  CUR_STATUS=$(echo "$ticket_file" | sed -n 's|.*/tickets/\([^/]*\)/.*|\1|p')

  # Admin departments don't have a TDD-RED phase — there's no production
  # code to write a failing test for. The doer (ic-admin) goes straight
  # in stage 4 (tdd-green). But stage-enqueue-tdd-green only iterates
  # in-progress/, so we must promote admin tickets from sprint-current
  # → in-progress here. Without this promotion they sit in sprint-current
  # forever and Loop A busy-loops until the safety valve fires (which
  # defers them to backlog with no work done — the bug).
  if is_admin_dept "$DEPT"; then
    if [ "$CUR_STATUS" = "sprint-current" ]; then
      HIVE_SCRIPTED_MOVE=1 HIVE_ROLE=System HIVE_AGENT=stage-enqueue-tdd-red \
        bash "$SKILL_DIR/lib/tools/ticket-move.sh" \
          "$DEPT" "$TID" in-progress \
          "Reconcile: admin ticket; no TDD-RED phase. Promoting to in-progress so ic-admin runs in tdd-green stage." \
          "stage-enqueue-tdd-red" \
          >/dev/null 2>&1 || true
      echo "stage-enqueue-tdd-red: promoted admin $DEPT/$TID sprint-current → in-progress"
    fi
    continue
  fi

  # Skip if a tester entry for this ticket is still pending. Ignore finished
  # entries (done/failed/skipped) — the round persists across stage transitions
  # within a sprint, so a stale entry from a prior loop iteration would otherwise
  # keep the ticket from ever being re-enqueued.
  if jq -e --arg t "$TID" '.entries[] | select(.kind=="ic-cycle-tester" and .ticket==$t and (.status=="queued" or .status=="running"))' \
       "$COMPANY_DIR/rounds/current-round.json" >/dev/null 2>&1; then
    continue
  fi

  # Check this ticket's specific [TDD-RED] commit. Match `[TDD-RED] $TID:`
  # — NOT a generic `[TDD-RED]` grep, which would match commits for OTHER
  # tickets that happen to share branch history (e.g., a ticket forked off
  # another's feature branch, or a merged main containing a prior ticket's
  # [TDD-RED] commit).
  BRANCH="feature/$TID"
  HAS_RED=0
  if git -C "$PROJECT_ROOT" log "main..$BRANCH" --oneline 2>/dev/null \
       | grep -qE "\[TDD-RED\] $TID(:|\s|$)"; then
    HAS_RED=1
  fi

  if [ "$HAS_RED" = "1" ]; then
    # RED is already done. Don't re-enqueue. But if the ticket is still
    # in sprint-current, RECONCILE it to in-progress so tdd-green picks
    # it up next stage. Without this reconciliation, the ticket sits in
    # sprint-current forever and the sprint busy-loops.
    if [ "$CUR_STATUS" = "sprint-current" ]; then
      HIVE_SCRIPTED_MOVE=1 HIVE_ROLE=System HIVE_AGENT=stage-enqueue-tdd-red \
        bash "$SKILL_DIR/lib/tools/ticket-move.sh" \
          "$DEPT" "$TID" in-progress \
          "Reconcile: branch has [TDD-RED] $TID; promoting to in-progress for tdd-green." \
          "stage-enqueue-tdd-red" \
          >/dev/null 2>&1 || true
      echo "stage-enqueue-tdd-red: reconciled $TID sprint-current → in-progress (RED already done)"
    fi
    continue
  fi

  # Skip if ticket has skip_tdd_red=true
  if [ "$(jq -r '.skip_tdd_red // false' "$ticket_file" 2>/dev/null)" = "true" ]; then
    continue
  fi

  TITLE=$(jq -r '.title // ""' "$ticket_file")
  DESC=$(jq -r '.description // ""' "$ticket_file")

  FRESH_HINT=""
  if [ "$FRESH" = 1 ] && [ "$N" = "0" ]; then
    FRESH_HINT="

NOTE: This is the FIRST ticket on a fresh project — no test framework is set
up yet. Your [TDD-RED] commit must include test framework scaffolding
appropriate to the project's primary language (e.g., \`npm init -y && npm i
--save-dev jest\` + a jest config; or a pytest skeleton with conftest.py).
Pick a sensible default if the language is ambiguous."
  fi

  PROMPT="$(load_agent_prompt "$TESTER_AGENT_FILE")

--- STAGE 3: tdd-red (ticket: $TID, dept: $DEPT) ---
Mode: ic-cycle-tester (TDD-RED).

Ticket: $TID — $TITLE
Description:
$DESC

Worktree: $PROJECT_ROOT/.worktrees/$TID  (round-runner has already moved
the ticket to in-progress and ensured the worktree+branch exist)

═══════════════════════════════════════════════════════════════════════════
THINK BEFORE YOU EDIT
═══════════════════════════════════════════════════════════════════════════
Don't dive straight into Write. First do these in order:

  1. Read the ticket: mcp__hive__ticket_get(dept=\"$DEPT\", ticket_id=\"$TID\")
     Note every acceptance criterion.

  2. Read existing tests in the project to learn naming + layout conventions
     (Read / Grep / Glob).

  3. Think through the test before writing it. In the same turn, briefly
     consider:
     - Which acceptance criterion you'll test (if multiple, pick the most
       behavior-defining one)
     - The concrete input → assertion contract (\"calling X with Y returns
       Z\", not \"tests X works\")
     - The expected failure mode that proves the test exercises behavior
       that doesn't exist yet (ImportError, \"function not defined\",
       \"expected X got None from missing handler\", etc.)
     - Path where the test file goes; what production module it imports

  4. Write the test, run it to confirm it fails for the anticipated
     reason, commit \`[TDD-RED] $TID: <summary>\`, push.

Thinking up front helps you avoid tests that fail for the wrong reason
(syntax error, wrong import). Don't stop after thinking — move straight
to writing the test in the same turn.

Do NOT:
  - implement any production code
  - move the ticket (the round-runner handles status moves)
  - touch PRs (they open automatically when ic-engineer moves to in-review)$FRESH_HINT

After committing the failing test, EXIT. Stage 4 (tdd-green) will run an
ic-engineer on this ticket next."

  COMPANY_DIR="$COMPANY_DIR" SKILL_DIR="$SKILL_DIR" \
    bash "$SKILL_DIR/lib/tools/round-enqueue.sh" \
      --kind ic-cycle-tester --agent ic-tester --dept "$DEPT" --role IC-Tester \
      --ticket "$TID" \
      --queued-by stage-advance \
      --log-to "$COMPANY_DIR/logs/ic-${TID}.log" \
      --prompt-inline-stdin <<<"$PROMPT"
  N=$((N + 1))
done

echo "stage-enqueue-tdd-red: $N entries enqueued"
