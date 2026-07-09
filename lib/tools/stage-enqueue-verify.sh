#!/usr/bin/env bash
set -uo pipefail

# stage-enqueue-verify.sh — STAGE 5.
# Enqueues 1 verification entry per in-review ticket where:
#   - gates_passed=true (deterministic gates already cleared by verification-gates.sh)
#   - verified_by is empty (not yet approved)
#
# Replaces verification-runner.sh's per-dept loop. Same prompt body (now
# sourced from lib/agents/verification-ic.md when present, else inlined).
#
# Usage: stage-enqueue-verify.sh <project-root>

PROJECT_ROOT="${1:-${PROJECT_ROOT:-$PWD}}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd)" || { echo "bad project root" >&2; exit 2; }
COMPANY_DIR="$PROJECT_ROOT/.company"
SKILL_DIR="${HIVE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
export COMPANY_DIR PROJECT_ROOT SKILL_DIR

# shellcheck disable=SC1091
. "$SKILL_DIR/lib/tools/dept-helpers.sh"
# Load the canonical framework primer that prepends to every spawn prompt.
. "$SKILL_DIR/lib/tools/load-agent-prompt.sh"

VERIFY_AGENT_FILE="$SKILL_DIR/lib/agents/verification-ic.md"
VERIFY_ADMIN_AGENT_FILE="$SKILL_DIR/lib/agents/verification-ic-admin.md"

# Per-ticket verification bounce limit — mirrors stage-advance.sh so both
# scripts agree on the cap.  Overridable via HIVE_MAX_VERIFICATION_BOUNCES.
MAX_VERIFICATION_BOUNCES="${HIVE_MAX_VERIFICATION_BOUNCES:-5}"

N=0
for ticket_file in "$COMPANY_DIR"/departments/*/tickets/in-review/*.json; do
  [ -f "$ticket_file" ] || continue

  TID=$(jq -r '.id // empty' "$ticket_file")
  [ -z "$TID" ] && continue

  DEPT=$(echo "$ticket_file" | sed -n 's|.*/departments/\([^/]*\)/tickets/.*|\1|p')

  GATES_OK=$(jq -r '.gates_passed // false' "$ticket_file" 2>/dev/null)
  VERIFIED=$(jq -r '.verified_by // ""' "$ticket_file" 2>/dev/null)

  [ "$GATES_OK" = "true" ] || continue
  [ -z "$VERIFIED" ] || continue

  # Skip if a verification entry for this ticket is still pending. Ignore
  # finished entries (done/failed/skipped) — the round persists across stage
  # transitions within a sprint, so a stale entry from a prior loop iteration
  # would otherwise keep the ticket from ever being re-enqueued.
  if jq -e --arg t "$TID" '.entries[] | select(.kind=="verification" and .ticket==$t and (.status=="queued" or .status=="running"))' \
       "$COMPANY_DIR/rounds/current-round.json" >/dev/null 2>&1; then
    continue
  fi

  # Skip if this ticket has hit the verification bounce limit. After
  # MAX_VERIFICATION_BOUNCES bounces, the ticket is deferred to backlog
  # so the sprint can advance.  (photonic-proc ENGINEERING-0047 had 47
  # bounces before the global loop cap fired.)
  BOUNCE_COUNT=$(jq -r '.verification_bounce_count // 0' "$ticket_file" 2>/dev/null)
  if [ "${BOUNCE_COUNT:-0}" -ge "${MAX_VERIFICATION_BOUNCES:-5}" ]; then
    echo "stage-enqueue-verify: skipping $TID (verification_bounce_count=$BOUNCE_COUNT >= ${MAX_VERIFICATION_BOUNCES:-5})"
    HIVE_SCRIPTED_MOVE=1 HIVE_ROLE=System HIVE_AGENT=stage-enqueue-verify \
      bash "$SKILL_DIR/lib/tools/ticket-move.sh" "$DEPT" "$TICKET_ID" backlog \
        "Deferred to backlog: verification bounced $BOUNCE_COUNT times (limit: ${MAX_VERIFICATION_BOUNCES:-5})" \
        "stage-enqueue-verify"
    continue
  fi

  TITLE=$(jq -r '.title // ""' "$ticket_file")
  GATES_FILE="$COMPANY_DIR/departments/$DEPT/components/$TID/gates.json"
  IS_ADMIN=0
  is_admin_dept "$DEPT" && IS_ADMIN=1

  # Pick the body: admin tickets get the deliverable-review prompt; engineering
  # tickets get the adversarial code-review prompt.
  if [ "$IS_ADMIN" = 1 ] && [ -f "$VERIFY_ADMIN_AGENT_FILE" ]; then
    BODY=$(load_agent_prompt "$VERIFY_ADMIN_AGENT_FILE")
    SUFFIX="
--- STAGE 5: verify (ticket: $TID, dept: $DEPT, admin) ---

Ticket file:  $ticket_file
Title:        $TITLE

You are: Verification-IC for the $DEPT department (admin / non-engineering).
This is a NON-CODE ticket. There is no worktree, no branch, no PR, no
tests, no scripted gates to inspect. Your job is to review the deliverable
written by the ic-admin doer.

Look for the doer's last comment on the ticket — it names the file paths
of the deliverable. Read those files and judge whether the work was
actually accomplished and properly so.

After deciding, EXIT. The round-runner moves to the next ticket."
  elif [ -f "$VERIFY_AGENT_FILE" ]; then
    BODY=$(load_agent_prompt "$VERIFY_AGENT_FILE")

    # Detect the "previously approved, merge conflicted, rebased, now back
    # in in-review for re-merge" scenario. When ticket-move rolls back a
    # post-approve merge conflict it writes a comment whose body starts with
    # "REJECTED by verification: PR merge conflict on approve". If that
    # comment exists AND the verification fields are currently cleared
    # (verified_by empty, gates_passed false) AND the ticket is back in
    # in-review, the prior approval has been wiped and we need a fresh
    # one. Without an explicit instruction in the prompt, the verifier
    # tends to read the prior "✅ APPROVED" comment, conclude "I already
    # approved this," and exit without calling ticket_approve — which
    # orphan-recovery then bounces, and the loop spins forever.
    POST_CONFLICT_HINT=""
    HAS_CONFLICT_BOUNCE=$(jq -r '
      (.comments // []) |
      map(select((.body // "") | test("PR merge conflict on approve"; "i"))) |
      length
    ' "$ticket_file" 2>/dev/null)
    VERIFIED_BY=$(jq -r '.verified_by // ""' "$ticket_file" 2>/dev/null)
    if [ "${HAS_CONFLICT_BOUNCE:-0}" -gt 0 ] && [ -z "$VERIFIED_BY" ]; then
      POST_CONFLICT_HINT="

POST-CONFLICT RE-VERIFICATION: a previous turn of you approved this
ticket. The PR merge then hit a conflict with main, so ticket-move
rolled the approval back (.verified_by cleared, .gates_passed reset)
and the engineer rebased the feature branch. You are now seeing the
ticket back in in-review for a FRESH approval cycle.

Your prior \"✅ APPROVED\" comment is still on the ticket as audit
history, but it has NO effect on the current loop iteration. You MUST
re-verify the post-rebase state and either:
  - call mcp__hive__ticket_approve again (which will retry the merge,
    most often succeeding now that the rebase is in), OR
  - call mcp__hive__ticket_bounce / ticket_reject if the rebase broke
    something or the merged-in main work invalidates the approach.

Do NOT exit without calling one of those three tools. Exiting silently
will leave the ticket stuck — orphan-recovery will bounce it and the
loop will re-spawn you in ~30 s with the same state."
    fi

    SUFFIX="
--- STAGE 5: verify (ticket: $TID, dept: $DEPT) ---

Ticket file:    $ticket_file
Title:          $TITLE
Worktree:       $PROJECT_ROOT/.worktrees/$TID  (branch: feature/$TID)
Gate report:    $GATES_FILE  (deterministic gates already passed)

You are: Verification-IC for the $DEPT department (engineering).
${POST_CONFLICT_HINT}

After deciding, EXIT. The round-runner moves to the next ticket."
  else
    # Fallback when both verification-ic.md files are missing on disk.
    # Still prepend the framework primer so the agent at least knows the
    # rules of the road.
    PRIMER="$SKILL_DIR/lib/agents/_FRAMEWORK_PRIMER.md"
    if [ -f "$PRIMER" ]; then
      BODY="$(cat "$PRIMER")

You are an ADVERSARIAL Verification IC. Read the ticket, the gates, the diff. Then APPROVE via mcp__hive__ticket_approve(dept, ticket_id, verifier=\"Verification-IC\", rationale=\"<≥30 chars>\") OR REJECT via mcp__hive__ticket_bounce(dept, ticket_id, verifier=\"Verification-IC\", rationale=\"<≥80 chars: numbered list of every issue with file:line refs>\"). Do NOT call ticket_move directly. Do NOT touch PRs. Exit cleanly."
    else
      BODY="You are an ADVERSARIAL Verification IC. Approve via mcp__hive__ticket_approve; reject via mcp__hive__ticket_bounce. Do NOT call ticket_move or gh-pr-* scripts."
    fi
    SUFFIX=""
  fi

  PROMPT="${BODY}${SUFFIX}"

  COMPANY_DIR="$COMPANY_DIR" SKILL_DIR="$SKILL_DIR" \
    bash "$SKILL_DIR/lib/tools/round-enqueue.sh" \
      --kind verification --agent verification-ic --dept "$DEPT" --role Verification-IC \
      --ticket "$TID" \
      --queued-by stage-advance \
      --log-to "$COMPANY_DIR/logs/verification-${TID}.log" \
      --prompt-inline-stdin <<<"$PROMPT"
  N=$((N + 1))
done

echo "stage-enqueue-verify: $N entries enqueued"
