#!/usr/bin/env bash
set -uo pipefail

# stage-enqueue-tdd-green.sh — STAGE 4.
# Enqueues 1 ic-cycle-eng entry per in-progress ticket whose feature branch
# has a [TDD-RED] commit but not yet a [TDD-GREEN] commit (i.e., the test
# exists but the implementation doesn't).
#
# Also enqueues for tickets flagged `skip_tdd_red: true` (verification IC
# bypass when "tests fine, code is wrong" — these go straight to TDD-GREEN
# without a fresh RED).
#
# Replaces the engineering half of the old ic-spawner.sh.
#
# Usage: stage-enqueue-tdd-green.sh <project-root>

PROJECT_ROOT="${1:-${PROJECT_ROOT:-$PWD}}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd)" || { echo "bad project root" >&2; exit 2; }
COMPANY_DIR="$PROJECT_ROOT/.company"
SKILL_DIR="${HIVE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
export COMPANY_DIR PROJECT_ROOT SKILL_DIR

# shellcheck disable=SC1091
. "$SKILL_DIR/lib/tools/dept-helpers.sh"
# Load the canonical framework primer that prepends to every spawn prompt.
. "$SKILL_DIR/lib/tools/load-agent-prompt.sh"

# Pick the doer agent for a ticket. Engineering depts get ic-engineer
# (or ic-infra for infrastructure-typed tickets). Admin depts get
# ic-admin — the document-deliverable agent (no code, no PR).
agent_for_ticket() {
  local f="$1" dept="$2"
  if is_admin_dept "$dept"; then
    echo "ic-admin"
    return
  fi
  local type; type=$(jq -r '.type // "feature"' "$f" 2>/dev/null)
  case "$type" in
    infra|infrastructure|chore-infra) echo "ic-infra" ;;
    *) echo "ic-engineer" ;;
  esac
}

N=0
for ticket_file in "$COMPANY_DIR"/departments/*/tickets/in-progress/*.json; do
  [ -f "$ticket_file" ] || continue
  TID=$(jq -r '.id // empty' "$ticket_file")
  [ -z "$TID" ] && continue

  DEPT=$(echo "$ticket_file" | sed -n 's|.*/departments/\([^/]*\)/tickets/.*|\1|p')

  # Skip if a doer entry for this ticket is still pending. Ignore finished
  # entries (done/failed/skipped) — the round persists across stage transitions
  # within a sprint, so a stale entry from a prior loop iteration would otherwise
  # keep the ticket from ever being re-enqueued.
  if jq -e --arg t "$TID" '.entries[] | select(.kind=="ic-cycle-eng" and .ticket==$t and (.status=="queued" or .status=="running"))' \
       "$COMPANY_DIR/rounds/current-round.json" >/dev/null 2>&1; then
    continue
  fi

  BRANCH="feature/$TID"
  IS_ADMIN=0
  if is_admin_dept "$DEPT"; then
    IS_ADMIN=1
  fi

  if [ "$IS_ADMIN" = 0 ]; then
    # Engineering ticket: needs a TICKET-SPECIFIC [TDD-RED] commit OR
    # skip_tdd_red=true. The grep is anchored to "$TID" so we don't
    # match an unrelated [TDD-RED] commit that happens to be in this
    # branch's history (e.g., when feature/X was branched off feature/Y
    # or main contains another ticket's [TDD-RED]).
    HAS_RED=0
    if git -C "$PROJECT_ROOT" log "main..$BRANCH" --oneline 2>/dev/null \
         | grep -qE "\[TDD-RED\] $TID(:|\s|$)"; then
      HAS_RED=1
    fi
    SKIP_RED=$(jq -r '.skip_tdd_red // false' "$ticket_file" 2>/dev/null)
    if [ "$HAS_RED" = "0" ] && [ "$SKIP_RED" != "true" ]; then
      continue
    fi

    # Check ticket-specific [TDD-GREEN] (same anti-collision rationale).
    HAS_GREEN=0
    if git -C "$PROJECT_ROOT" log "main..$BRANCH" --oneline 2>/dev/null \
         | grep -qE "\[TDD-GREEN\] $TID(:|\s|$)"; then
      HAS_GREEN=1
    fi
    if [ "$HAS_GREEN" = "1" ]; then
      # GREEN is already done. Two interpretations:
      #
      # (a) Crash-stuck: the engineer's exit-0 post-spawn move was missed
      #     (round-runner crash, or pre-upgrade ticket). No verifier has
      #     reviewed it. Reconcile by promoting to in-review.
      # (b) Bounce-rework needed: the ticket WAS in in-review, the
      #     verifier rejected it (writes "REJECTED by verification"
      #     comment), it bounced back to in-progress. The [TDD-GREEN]
      #     commit is still on the branch from the prior implementation —
      #     but the verifier wants changes. Need a FRESH engineer spawn,
      #     NOT a reconcile-to-in-review (which would just put it right
      #     back into the verifier's queue with no fixes).
      #
      # Distinguish via the ticket's comment history. ticket-bounce.sh and
      # ticket-move's conflict rollback both write "REJECTED by verification"
      # as their comment prefix. If such a comment exists with timestamp
      # newer than any existing engineer activity, this is case (b).
      # `(.body // "")` guards against legacy comments that have a null body
      # field (older schemas used `text`+`time` instead of `body`+`timestamp`).
      # Without the guard, jq's test() throws "null cannot be matched", the
      # error is swallowed by 2>/dev/null, HAS_REJECTION becomes empty, the
      # script wrongly takes case (a), and the ticket bounces straight back
      # to in-review with no engineer ever running. (BACKEN-0018 hit this.)
      #
      # Match BOTH the human verification IC's bounce comments
      # ("REJECTED by verification" — from ticket-bounce.sh) AND the
      # verification-gates daemon's bounce comments ("GATE FAILURE" /
      # "Gate failure" — from verification-gates.sh). Without the
      # gate-failure pattern, every gate bounce is mis-classified as
      # case (a) "crash-stuck", which reconciles to in-review where
      # verification-gates immediately re-bounces — no engineer ever
      # gets re-enqueued and Loop A spins until the safety valve fires.
      # (photonic-proc Loop A iteration 38+ hit this on every dept.)
      HAS_REJECTION=$(jq -r '
        (.comments // []) |
        map(select((.body // "") |
          test("REJECTED by verification|GATE FAILURE|Gate failure"; "i"))) |
        length
      ' "$ticket_file" 2>/dev/null)
      if [ "${HAS_REJECTION:-0}" -gt 0 ]; then
        # Case (b): bounce-rework. Fall through to enqueue a fresh engineer
        # (don't `continue` here). The engineer's prompt will see the
        # rejection comment and revise.
        :
      else
        # Case (a): crash-stuck. Promote to in-review.
        HIVE_SCRIPTED_MOVE=1 HIVE_ROLE=System HIVE_AGENT=stage-enqueue-tdd-green \
          bash "$SKILL_DIR/lib/tools/ticket-move.sh" \
            "$DEPT" "$TID" in-review \
            "Reconcile: branch has [TDD-GREEN] $TID and no verifier rejection on file; promoting to in-review." \
            "stage-enqueue-tdd-green" \
            >/dev/null 2>&1 || true
        echo "stage-enqueue-tdd-green: reconciled $TID in-progress → in-review (GREEN done, no prior rejection)"
        continue
      fi
    fi
  fi
  # Admin ticket: no TDD requirement at all. Skip the branch checks.

  AGENT=$(agent_for_ticket "$ticket_file" "$DEPT")
  AGENT_FILE="$SKILL_DIR/lib/agents/${AGENT}.md"
  [ -f "$AGENT_FILE" ] || { echo "skipping $TID: missing $AGENT_FILE" >&2; continue; }

  TITLE=$(jq -r '.title // ""' "$ticket_file")
  DESC=$(jq -r '.description // ""' "$ticket_file")

  # Detect bounce-rework state for the prompt: a "REJECTED by verification"
  # comment exists on the ticket. The reconciliation block above already
  # falls through (instead of moving to in-review) when this is true; here
  # we just inject a hint into the engineer's prompt.
  BOUNCE_REWORK=0
  if [ "${HAS_REJECTION:-0}" -gt 0 ]; then
    BOUNCE_REWORK=1
  fi

  if [ "$IS_ADMIN" = 1 ]; then
    # Admin ticket: no test, no branch, no PR. Doer writes deliverable
    # to .company/ via the script tools and exits.
    PROMPT="$(load_agent_prompt "$AGENT_FILE")

--- STAGE 4: deliverable (ticket: $TID, dept: $DEPT, admin) ---
Mode: ic-admin.

Ticket: $TID — $TITLE
Description:
$DESC

This is an ADMIN-DEPT ticket. There is no test, no branch, no worktree,
no PR. Your deliverable is a document, status update, config edit, or
similar non-code output that lives under \`.company/\` and is written via
the bash script tools (see ic-admin.md for the full list).

Steps:
1. Read the ticket (above) — note every acceptance criterion.
2. Plan briefly (2-4 bullets) what you'll write and where.
3. Use the appropriate script tool to write the deliverable. Common ones:
   - \`report-write.sh\` — markdown reports under .company/<dept>/reports/
   - \`status-update.sh\` — update dept's status.json
   - \`chat-send.sh\` — message a chat channel
   - \`meeting-record.sh\` — record a meeting note
   - \`org-chart-rebuild.sh\` — propose / apply org-chart edits
   - \`ticket-create.sh\` — file follow-up tickets if your work surfaces new needs
4. Add a comment on this ticket summarizing what you delivered + paths.
5. Exit. The round-runner moves the ticket to in-review automatically.

Do NOT call ticket-move.sh. Do NOT touch PRs (admin tickets don't have
PRs). Do NOT write to project source files (the PreToolUse hook will
block you). Do NOT spawn other agents."
  else
    # Engineering ticket: TDD-GREEN flow as before.
    CONFLICT_HINT=""
    if [ "$SKIP_RED" = "true" ]; then
      LAST_COMMENT=$(jq -r '.comments // [] | reverse | map(select((.body // "") | test("PR merge conflict"; "i"))) | first.body // ""' "$ticket_file" 2>/dev/null)
      if [ -n "$LAST_COMMENT" ]; then
        CONFLICT_HINT="

CONFLICT-RESOLUTION MODE: This ticket was bounced because the PR had merge
conflicts. Your FIRST action must be:
  cd $PROJECT_ROOT/.worktrees/$TID
  git fetch origin
  git rebase origin/main
  # resolve conflicts, then:
  git push --force-with-lease

Then proceed with any remaining implementation work."
      fi
    fi

    REWORK_HINT=""
    if [ "$BOUNCE_REWORK" = 1 ] && [ -z "$CONFLICT_HINT" ]; then
      # Pull the most recent verifier rejection comment so the engineer
      # has the rationale right in their prompt.
      LAST_REJECTION=$(jq -r '
        (.comments // []) |
        map(select((.body // "") | test("REJECTED by verification"; "i"))) |
        last.body // ""
      ' "$ticket_file" 2>/dev/null)
      REWORK_HINT="

REWORK MODE: This ticket was previously implemented and bounced by
verification. The branch already has a [TDD-GREEN] commit but it does
NOT satisfy the verifier. Read the rejection comment below + the full
ticket comment history (mcp__hive__ticket_get) for context. Address
EVERY numbered issue in the rationale. Make new commits on the same
branch (subject prefix \`[TDD-GREEN] $TID: <what you changed>\`). Push.

Most recent rejection rationale:
${LAST_REJECTION}

Do NOT ignore items from the rationale. Do NOT add new features beyond
what the verifier asked for. After your changes, the round-runner moves
to in-review and the verifier re-reviews."
    fi

    PROMPT="$(load_agent_prompt "$AGENT_FILE")

--- STAGE 4: tdd-green (ticket: $TID, dept: $DEPT) ---
Mode: ic-cycle-eng (TDD-GREEN).

Ticket: $TID — $TITLE
Description:
$DESC

Worktree: $PROJECT_ROOT/.worktrees/$TID
Branch:   $BRANCH

The branch already has a [TDD-RED] commit (the failing test).

═══════════════════════════════════════════════════════════════════════════
THINK BEFORE YOU EDIT
═══════════════════════════════════════════════════════════════════════════
Don't dive straight into Edit. First do these in order:

  1. Read the ticket: mcp__hive__ticket_get(dept=\"$DEPT\", ticket_id=\"$TID\")
     Note every acceptance criterion.

  2. Find and read the failing test:
       cd $PROJECT_ROOT/.worktrees/$TID
       git log main..HEAD --grep '\\[TDD-RED\\] $TID' -1 --stat
     Then cat the test file the stat lists. The assertions are your
     contract — your code must make those exact assertions pass.

  3. Read the production code paths the test imports / depends on, plus
     any related modules (Read / Grep / Glob).

  4. Think through the approach before implementing. In the same turn,
     briefly consider:
     - Files you'll change (one-line per-file summary)
     - Approach (what algorithm / data flow / API; why this over alternatives)
     - Per-acceptance-criterion mapping (each AC named, satisfied by what
       specific code change)
     - Edge cases you'll cover beyond the test
     - How the failing test will pass (trace each assertion through your
       intended code; confirm each assertion will hold)

  5. Implement, in the same turn:
       a. Write the minimum production code to make the test pass.
       b. Commit with subject prefix \`[TDD-GREEN] $TID: <impl summary>\`.
       c. Run the test locally to confirm it passes.
       d. Push the branch.

If you discover mid-implementation that your approach was wrong,
reconsider and continue — don't silently change direction.

Do NOT call ticket-move.sh. The round-runner moves you to in-review on
exit 0. Do NOT touch PRs (they open automatically on the move).$CONFLICT_HINT$REWORK_HINT

After exit 0, the round-runner will:
  - Move ticket to in-review
  - Open the PR (or update existing)
Stage 5 (verify) runs next."
  fi

  COMPANY_DIR="$COMPANY_DIR" SKILL_DIR="$SKILL_DIR" \
    bash "$SKILL_DIR/lib/tools/round-enqueue.sh" \
      --kind ic-cycle-eng --agent "$AGENT" --dept "$DEPT" --role IC \
      --ticket "$TID" \
      --queued-by stage-advance \
      --log-to "$COMPANY_DIR/logs/ic-${TID}.log" \
      --prompt-inline-stdin <<<"$PROMPT"
  N=$((N + 1))
done

echo "stage-enqueue-tdd-green: $N entries enqueued"
