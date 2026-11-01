#!/usr/bin/env bash
set -uo pipefail

# sprint-lifecycle.sh — the single driver for the entire sprint lifecycle.
#
# Replaces round-runner.sh + stage-advance.sh. One script, one ticket at a
# time: pick a ticket from sprint-current, run its full lifecycle (tester →
# engineer → gates → verifier), then pick the next one. When all tickets are
# done or bounced to backlog, advance to the release pipeline.
#
# Usage: sprint-lifecycle.sh <project-root>

PROJECT_ROOT="${1:-${PROJECT_ROOT:-$PWD}}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd)" || { echo "bad project root" >&2; exit 2; }
COMPANY_DIR="$PROJECT_ROOT/.company"
SKILL_DIR="${HIVE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
export COMPANY_DIR PROJECT_ROOT SKILL_DIR

# shellcheck disable=SC1091
. "$SKILL_DIR/lib/tools/sprint-state.sh"
ss_init

LOG="$COMPANY_DIR/logs/sprint-lifecycle.log"
mkdir -p "$COMPANY_DIR/logs"
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "[$(ts)] $*" | tee -a "$LOG" >&2; }

# ── Config ──────────────────────────────────────────────────────────
MAX_VERIFICATION_BOUNCES="${HIVE_MAX_VERIFICATION_BOUNCES:-5}"
MAX_RELEASE_RETRIES="${HIVE_MAX_RELEASE_RETRIES:-3}"
MAX_LOOP_ITERATIONS="${HIVE_MAX_LOOP_ITERATIONS:-100}"
HIVE_SPRINT_MAX_ACTIVE_TICKETS="${HIVE_SPRINT_MAX_ACTIVE_TICKETS:-5}"

# ── Singleton lock ──────────────────────────────────────────────────
RUNNER_PID="$COMPANY_DIR/rounds/runner.pid"
mkdir -p "$(dirname "$RUNNER_PID")"
PRIOR_PID=$(cat "$RUNNER_PID" 2>/dev/null | head -1 | tr -dc '0-9')
if [ -n "$PRIOR_PID" ] && [ "$PRIOR_PID" != "$$" ] && kill -0 "$PRIOR_PID" 2>/dev/null; then
  log "found stale runner pid=$PRIOR_PID; sending SIGTERM"
  kill -TERM "$PRIOR_PID" 2>/dev/null || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    kill -0 "$PRIOR_PID" 2>/dev/null || break
    sleep 0.5
  done
  if kill -0 "$PRIOR_PID" 2>/dev/null; then
    kill -KILL "$PRIOR_PID" 2>/dev/null || true
    sleep 0.3
  fi
fi
exec 9>"$RUNNER_PID"
if ! flock -n 9; then
  echo "another sprint-lifecycle is active" >&2
  exit 0
fi
echo $$ >&9

# ── Helpers ─────────────────────────────────────────────────────────

# Pick the first ticket from sprint-current, or the first bouncing ticket
# from in-progress. Returns "dept ticket_id" or empty.
pick_next_ticket() {
  local depts_dir="$COMPANY_DIR/departments"
  [ -d "$depts_dir" ] || return 0

  # Priority 1: first ticket in sprint-current
  for dept_path in "$depts_dir"/*/; do
    [ -d "$dept_path" ] || continue
    local dept sc_dir f tid
    dept=$(basename "$dept_path")
    sc_dir="$dept_path/tickets/sprint-current"
    [ -d "$sc_dir" ] || continue
    f=$(find "$sc_dir" -maxdepth 1 -name '*.json' -type f | sort | head -1)
    if [ -n "$f" ] && [ -f "$f" ]; then
      tid=$(basename "$f" .json)
      echo "$dept $tid"
      return 0
    fi
  done

  # Priority 2: first bouncing ticket in in-progress (bounce_count > 0)
  for dept_path in "$depts_dir"/*/; do
    [ -d "$dept_path" ] || continue
    local dept ip_dir f bc tid
    dept=$(basename "$dept_path")
    ip_dir="$dept_path/tickets/in-progress"
    [ -d "$ip_dir" ] || continue
    f=$(find "$ip_dir" -maxdepth 1 -name '*.json' -type f | sort | head -1)
    if [ -n "$f" ] && [ -f "$f" ]; then
      bc=$(jq -r '.bounce_count // 0' "$f" 2>/dev/null)
      if [ "${bc:-0}" -gt 0 ]; then
        tid=$(basename "$f" .json)
        echo "$dept $tid"
        return 0
      fi
    fi
  done

  return 1
}

# Check if any active tickets remain (sprint-current + in-progress + in-review)
has_active_tickets() {
  local depts_dir="$COMPANY_DIR/departments"
  [ -d "$depts_dir" ] || return 1
  local n
  n=$(find "$depts_dir"/*/tickets/{sprint-current,in-progress,in-review} -maxdepth 1 -name '*.json' -type f 2>/dev/null | wc -l)
  [ "$n" -gt 0 ]
}

# Create worktree for a ticket. Idempotent.
ensure_worktree() {
  local dept="$1" ticket="$2"
  if [ -x "$SKILL_DIR/lib/tools/git-worktree.sh" ]; then
    bash "$SKILL_DIR/lib/tools/git-worktree.sh" create "$ticket" "$PROJECT_ROOT" \
      >> "$LOG" 2>&1 || log "  worktree ensure for $ticket failed (continuing)"
  fi
}

# Move ticket and log
scripted_move() {
  local dept="$1" ticket="$2" new_status="$3" comment="$4"
  HIVE_SCRIPTED_MOVE=1 HIVE_ROLE=System HIVE_AGENT=sprint-lifecycle \
    bash "$SKILL_DIR/lib/tools/ticket-move.sh" \
      "$dept" "$ticket" "$new_status" "$comment" "sprint-lifecycle" \
      >> "$LOG" 2>&1 || log "  move $dept/$ticket → $new_status failed"
}

# Defer all remaining active tickets to backlog
defer_remaining_to_backlog() {
  local reason_text="$1"
  local dept_path sc_dir ip_dir review_dir f tid d
  for dept_path in "$COMPANY_DIR/departments"/*/; do
    [ -d "$dept_path" ] || continue
    d=$(basename "$dept_path")
    for status in sprint-current in-progress in-review; do
      local sd="$dept_path/tickets/$status"
      [ -d "$sd" ] || continue
      for f in "$sd"/*.json; do
        [ -f "$f" ] || continue
        tid=$(basename "$f" .json)
        scripted_move "$d" "$tid" "backlog" "$reason_text"
      done
    done
  done
  log "deferred remaining tickets to backlog: $reason_text"
}

# ── CEO planning ────────────────────────────────────────────────────
run_ceo_plan() {
  log "CEO planning stage"
  local CEO_AGENT_FILE="$SKILL_DIR/lib/agents/ceo.md"
  [ -f "$CEO_AGENT_FILE" ] || { log "missing ceo.md"; return 1; }

  local DEPTS=(hr it rd marketing legal finance)
  if [ -f "$COMPANY_DIR/org-chart.json" ]; then
    while IFS= read -r d; do
      [ -z "$d" ] && continue
      case " ${DEPTS[*]} " in *" $d "*) ;; *) DEPTS+=("$d") ;; esac
    done < <(jq -r '.departments | to_entries[]? | select(.value.type=="dynamic") | .key' "$COMPANY_DIR/org-chart.json" 2>/dev/null)
  fi
  local DEPT_LIST; DEPT_LIST=$(printf '%s, ' "${DEPTS[@]}" | sed 's/, $//')

  local PROMPT
  . "$SKILL_DIR/lib/tools/load-agent-prompt.sh"
  PROMPT="$(load_agent_prompt "$CEO_AGENT_FILE")

--- STAGE: ceo-plan ---
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

After writing the direction file, EXIT. Do NOT spawn ICs, do NOT move tickets,
do NOT touch PRs, do NOT call any company lifecycle MCP tool. VP planning
runs next automatically."

  local LOG_TO="$COMPANY_DIR/logs/ceo.log"
  mkdir -p "$(dirname "$LOG_TO")"

  HIVE_AGENT="ceo" HIVE_DEPT="executive" HIVE_ROLE="CEO" HIVE_TICKET="" \
  bash "$SKILL_DIR/lib/core/agent-spawn.sh" -- "$PROMPT" \
    9<&- >> "$LOG_TO" 2>&1
  local EC=$?
  if [ $EC -eq 0 ]; then
    log "CEO plan completed successfully"
  else
    log "CEO plan failed (exit=$EC), continuing without direction"
  fi
}

# ── VP planning ─────────────────────────────────────────────────────
run_vp_plan() {
  log "VP planning stage"
  local DIRECTION
  DIRECTION=$(ss_get_field .ceo_direction_path)
  [ -z "$DIRECTION" ] && DIRECTION="(no direction document was written by CEO; act on prior sprint context)"

  local DEPTS=(hr it rd marketing legal finance)
  if [ -f "$COMPANY_DIR/org-chart.json" ]; then
    while IFS= read -r d; do
      [ -z "$d" ] && continue
      case " ${DEPTS[*]} " in *" $d "*) ;; *) DEPTS+=("$d") ;; esac
    done < <(jq -r '.departments | to_entries[]? | select(.value.type=="dynamic") | .key' "$COMPANY_DIR/org-chart.json" 2>/dev/null)
  fi

  local dept agent agent_file
  for dept in "${DEPTS[@]}"; do
    case "$dept" in
      hr) agent="vp-hr" ;; it) agent="vp-it" ;; rd) agent="vp-rd" ;;
      marketing) agent="vp-marketing" ;; legal) agent="vp-legal" ;;
      finance) agent="vp-finance" ;; *) agent="vp-dept" ;;
    esac
    agent_file="$SKILL_DIR/lib/agents/${agent}.md"
    [ -f "$agent_file" ] || { log "skipping $dept: missing $agent_file"; continue; }

    local PROMPT
    . "$SKILL_DIR/lib/tools/load-agent-prompt.sh"
    PROMPT="$(load_agent_prompt "$agent_file")

--- STAGE: vp-plan (department: $dept) ---
Read the CEO direction at: $DIRECTION
(That file has a .departments.\"$dept\" section if the CEO addressed your dept.)

Your job this turn:
1. Read your dept's direction + your existing backlog.
2. File tickets via ticket-create.sh INTO BACKLOG (not sprint-current — sprint-fill does that next).
3. Refine any backlog tickets that the CEO's direction implies need updating.
4. If you decide an existing backlog ticket is no longer worth doing, you
   may reject it (move to done with reason=rejected): only allowed during
   vp-plan, vp-review, ceo-bugfix-plan, vp-bugfix-plan stages.

You MUST NOT:
  - move tickets to sprint-current (sprint-fill does this)
  - move tickets to in-progress / in-review / done with reason=implemented
  - touch PRs
  - spawn any ICs
  - call any company lifecycle MCP tool

After filing/refining, EXIT. Sprint-fill and ticket processing run next."

    local LOG_TO="$COMPANY_DIR/logs/vp-${dept}.log"
    mkdir -p "$(dirname "$LOG_TO")"

    HIVE_AGENT="$agent" HIVE_DEPT="$dept" HIVE_ROLE="VP" HIVE_TICKET="" \
    bash "$SKILL_DIR/lib/core/agent-spawn.sh" -- "$PROMPT" \
      9<&- >> "$LOG_TO" 2>&1 || log "  VP $dept planning failed"
  done

  log "VP planning complete"
}

# ── Sprint-fill ─────────────────────────────────────────────────────
run_sprint_fill() {
  log "sprint-fill: promoting backlog tickets"
  COMPANY_DIR="$COMPANY_DIR" bash "$SKILL_DIR/lib/tools/sprint-fill.sh" \
    --max "$HIVE_SPRINT_MAX_ACTIVE_TICKETS" >> "$LOG" 2>&1 || true

  # Update tickets_in_sprint
  local TICKETS_JSON
  TICKETS_JSON=$(find "$COMPANY_DIR/departments"/*/tickets/sprint-current -maxdepth 1 -name '*.json' -type f 2>/dev/null \
    | while read -r f; do basename "$f" .json; done | jq -R . | jq -s -c .)
  ss_set_tickets_in_sprint "$TICKETS_JSON"
  ss_reset_loop_iteration
}

# ── Ticket lifecycle ────────────────────────────────────────────────
# Run one ticket's full lifecycle. Returns 0 if ticket done, 1 if bounced.
run_ticket_lifecycle() {
  local dept="$1" ticket="$2"
  local log_prefix="  $dept/$ticket"
  local TFILE BRANCH EC

  log "$log_prefix: starting lifecycle"

  # Scripted pre-spawn claim: sprint-current -> in-progress. The rest of
  # this function assumes the ticket file lives at TFILE (the in-progress
  # path), but a freshly-promoted ticket is still in sprint-current until
  # we move it. Without this move the tester/engineer prompts get empty
  # title/description and the tdd_red_attempts counter is never persisted
  # (jq on a missing file fails silently), so the 3-strike skip_tdd_red
  # fallback can never trigger. Bounced tickets are already in
  # in-progress - ticket-move no-ops then.
  scripted_move "$dept" "$ticket" in-progress "sprint-lifecycle: starting ticket lifecycle"

  # Ensure worktree exists
  ensure_worktree "$dept" "$ticket"

  # Read ticket state
  local skip_red tdd_red_attempts bc
  TFILE="$COMPANY_DIR/departments/$dept/tickets/in-progress/${ticket}.json"
  skip_red=$(jq -r '.skip_tdd_red // false' "$TFILE" 2>/dev/null)
  tdd_red_attempts=$(jq -r '.tdd_red_attempts // 0' "$TFILE" 2>/dev/null)

  # ── Phase 1: TDD-RED (tester) ──
  # Bounded: each lifecycle pass gets at most MAX_TESTER_ITER tester spawns.
  # An unbounded `while true` here spun forever when the tester never
  # produced a [TDD-RED] commit: skip_red was read ONCE before the loop,
  # so the file-level skip_tdd_red=true written at strike 3 was invisible
  # to the loop (stale local), and `continue` re-spawned the tester
  # indefinitely — same for a persistently failing agent-spawn (EC!=0).
  local HAS_RED TITLE DESC FRESH_HINT PROMPT LOG_TO
  local TESTER_ITER=0
  local MAX_TESTER_ITER=3
  while [ $TESTER_ITER -lt $MAX_TESTER_ITER ]; do
    BRANCH="feature/$ticket"
    HAS_RED=0
    if git -C "$PROJECT_ROOT" log "main..$BRANCH" --oneline 2>/dev/null \
         | grep -qE "\[TDD-RED\] $ticket(:|\s|$)"; then
      HAS_RED=1
    fi

    if [ "$HAS_RED" = "1" ]; then
      log "$log_prefix: [TDD-RED] commit found, proceeding to engineer"
      break
    fi

    if [ "$skip_red" = "true" ]; then
      log "$log_prefix: skip_tdd_red=true, skipping tester"
      break
    fi

    # Spawn tester
    log "$log_prefix: spawning ic-cycle-tester"
    TITLE=$(jq -r '.title // ""' "$TFILE" 2>/dev/null)
    DESC=$(jq -r '.description // ""' "$TFILE" 2>/dev/null)

    # Detect fresh project
    local FRESH=0
    if ! { [ -f "$PROJECT_ROOT/package.json" ] && jq -e '.scripts.test' "$PROJECT_ROOT/package.json" >/dev/null 2>&1; } &&
       ! { [ -f "$PROJECT_ROOT/pyproject.toml" ] || [ -f "$PROJECT_ROOT/pytest.ini" ] || [ -f "$PROJECT_ROOT/setup.cfg" ]; } &&
       ! { [ -f "$PROJECT_ROOT/Cargo.toml" ] || [ -f "$PROJECT_ROOT/go.mod" ]; }; then
      FRESH=1
    fi

    FRESH_HINT=""
    if [ "$FRESH" = "1" ] && [ "$tdd_red_attempts" = "0" ]; then
      FRESH_HINT="

NOTE: This is the FIRST ticket on a fresh project — no test framework is set
up yet. Your [TDD-RED] commit must include test framework scaffolding
appropriate to the project's primary language (e.g., \`npm init -y && npm i
--save-dev jest\` + a jest config; or a pytest skeleton with conftest.py).
Pick a sensible default if the language is ambiguous."
    fi

    PROMPT="$(load_agent_prompt "$SKILL_DIR/lib/agents/ic-tester.md")

--- STAGE: tdd-red (ticket: $ticket, dept: $dept) ---
Mode: ic-cycle-tester (TDD-RED).

Ticket: $ticket — $TITLE
Description:
$DESC

Worktree: $PROJECT_ROOT/.worktrees/$ticket

═══════════════════════════════════════════════════════════════════════════
THINK BEFORE YOU EDIT
═══════════════════════════════════════════════════════════════════════════
Don't dive straight into Write. First do these in order:

  1. Read the ticket: mcp__hive__ticket_get(dept=\"$dept\", ticket_id=\"$ticket\")
     Note every acceptance criterion.
  2. Read existing tests in the project to learn naming + layout conventions.
  3. Think through the test before writing it.
  4. Write the test, run it to confirm it fails for the anticipated
     reason, commit \`[TDD-RED] $ticket: <summary>\`, push.

Do NOT:
  - implement any production code
  - move the ticket (this script handles status moves)
  - touch PRs

After committing the failing test, EXIT. The engineer will run next.$FRESH_HINT"

    LOG_TO="$COMPANY_DIR/logs/ic-${ticket}.log"
    mkdir -p "$(dirname "$LOG_TO")"

    HIVE_AGENT="ic-tester" HIVE_DEPT="$dept" HIVE_ROLE="IC-Tester" HIVE_TICKET="$ticket" \
    bash "$SKILL_DIR/lib/core/agent-spawn.sh" -- "$PROMPT" \
      9<&- >> "$LOG_TO" 2>&1
    EC=$?

    if [ $EC -ne 0 ]; then
      TESTER_ITER=$((TESTER_ITER + 1))
      log "$log_prefix: tester failed (exit=$EC); iteration $TESTER_ITER/$MAX_TESTER_ITER"
      continue
    fi

    # Check for [TDD-RED] commit
    HAS_RED=0
    if git -C "$PROJECT_ROOT" log "main..$BRANCH" --oneline 2>/dev/null \
         | grep -qE "\[TDD-RED\] $ticket(:|\s|$)"; then
      HAS_RED=1
    fi

    if [ "$HAS_RED" = "0" ]; then
      tdd_red_attempts=$((tdd_red_attempts + 1))
      jq --argjson n "$tdd_red_attempts" '.tdd_red_attempts = $n' "$TFILE" > "${TFILE}.tmp.$$" && mv "${TFILE}.tmp.$$" "$TFILE"
      log "$log_prefix: no [TDD-RED] commit (attempt $tdd_red_attempts)"
      if [ "$tdd_red_attempts" -ge 3 ]; then
        jq '.skip_tdd_red = true' "$TFILE" > "${TFILE}.tmp.$$" && mv "${TFILE}.tmp.$$" "$TFILE"
        bash "$SKILL_DIR/lib/tools/ticket-comment.sh" "$dept" "$ticket" "sprint-lifecycle" \
          "ic-tester ran $tdd_red_attempts times without producing a [TDD-RED] $ticket commit. Setting skip_tdd_red=true so the engineer writes both the test AND the implementation." \
          >> "$LOG" 2>&1 || true
        log "$log_prefix: skip_tdd_red=true after $tdd_red_attempts failed attempts"
        break
      fi
      TESTER_ITER=$((TESTER_ITER + 1))
      continue
    fi

    log "$log_prefix: tester produced [TDD-RED] commit"
    break
  done

  # ── Phase 2: TDD-GREEN (engineer) ──
  log "$log_prefix: spawning ic-cycle-engineer"
  local AGENT TYPE HAS_REJECTION REWORK_HINT CONFLICT_HINT LAST_REJECTION LAST_CONFLICT

  TITLE=$(jq -r '.title // ""' "$TFILE" 2>/dev/null)
  DESC=$(jq -r '.description // ""' "$TFILE" 2>/dev/null)
  TYPE=$(jq -r '.type // "feature"' "$TFILE" 2>/dev/null)
  case "$TYPE" in
    infra|infrastructure|chore-infra) AGENT="ic-infra" ;;
    *) AGENT="ic-engineer" ;;
  esac

  # Detect bounce-rework state
  HAS_REJECTION=$(jq -r '
    (.comments // []) |
    map(select((.body // "") |
      test("REJECTED by verification|GATE FAILURE|Gate failure"; "i"))) |
    length
  ' "$TFILE" 2>/dev/null)

  REWORK_HINT=""
  if [ "${HAS_REJECTION:-0}" -gt 0 ]; then
    LAST_REJECTION=$(jq -r '
      (.comments // []) |
      map(select((.body // "") | test("REJECTED by verification"; "i"))) |
      last.body // ""
    ' "$TFILE" 2>/dev/null)
    REWORK_HINT="

REWORK MODE: This ticket was previously implemented and bounced by
verification. The branch already has a [TDD-GREEN] commit but it does
NOT satisfy the verifier. Read the rejection comment below + the full
ticket comment history for context. Address EVERY numbered issue in the
rationale. Make new commits on the same branch (subject prefix
\`[TDD-GREEN] $ticket: <what you changed>\`). Push.

Most recent rejection rationale:
${LAST_REJECTION}"
  fi

  # Detect conflict resolution mode
  CONFLICT_HINT=""
  if [ "$skip_red" = "true" ]; then
    LAST_CONFLICT=$(jq -r '.comments // [] | reverse | map(select((.body // "") | test("PR merge conflict"; "i"))) | first.body // ""' "$TFILE" 2>/dev/null)
    if [ -n "$LAST_CONFLICT" ]; then
      CONFLICT_HINT="

CONFLICT-RESOLUTION MODE: This ticket was bounced because the PR had merge
conflicts. Your FIRST action must be:
  cd $PROJECT_ROOT/.worktrees/$ticket
  git fetch origin
  git rebase origin/main
  # resolve conflicts, then:
  git push --force-with-lease

Then proceed with any remaining implementation work."
    fi
  fi

  PROMPT="$(load_agent_prompt "$SKILL_DIR/lib/agents/${AGENT}.md")

--- STAGE: tdd-green (ticket: $ticket, dept: $dept) ---
Mode: ic-cycle-eng (TDD-GREEN).

Ticket: $ticket — $TITLE
Description:
$DESC

Worktree: $PROJECT_ROOT/.worktrees/$ticket
Branch:   $BRANCH

The branch already has a [TDD-RED] commit (the failing test).

═══════════════════════════════════════════════════════════════════════════
THINK BEFORE YOU EDIT
═══════════════════════════════════════════════════════════════════════════
Don't dive straight into Edit. First do these in order:

  1. Read the ticket: mcp__hive__ticket_get(dept=\"$dept\", ticket_id=\"$ticket\")
  2. Find and read the failing test:
       cd $PROJECT_ROOT/.worktrees/$ticket
       git log main..HEAD --grep '\\[TDD-RED\\] $ticket' -1 --stat
     Then cat the test file the stat lists.
  3. Read the production code paths the test imports / depends on.
  4. Think through the approach before implementing.
  5. Implement, in the same turn:
       a. Write the minimum production code to make the test pass.
       b. Commit with subject prefix \`[TDD-GREEN] $ticket: <impl summary>\`.
       c. Run the test locally to confirm it passes.
       d. Push the branch.

Do NOT call ticket-move.sh. This script moves you to in-review on exit 0.
Do NOT touch PRs (they open automatically on the move).$CONFLICT_HINT$REWORK_HINT

After exit 0, this script will:
  - Move ticket to in-review
  - Open the PR (or update existing)
  - Run deterministic gates
  - Run verification"

  LOG_TO="$COMPANY_DIR/logs/ic-${ticket}.log"
  mkdir -p "$(dirname "$LOG_TO")"

  HIVE_AGENT="$AGENT" HIVE_DEPT="$dept" HIVE_ROLE="IC" HIVE_TICKET="$ticket" \
  bash "$SKILL_DIR/lib/core/agent-spawn.sh" -- "$PROMPT" \
    9<&- >> "$LOG_TO" 2>&1
  EC=$?

  if [ $EC -ne 0 ]; then
    log "$log_prefix: engineer failed (exit=$EC), bouncing to in-progress"
    scripted_move "$dept" "$ticket" in-progress "Engineer failed (exit=$EC)"
    return 1
  fi

  # ── Phase 3: Move to in-review + run gates ──
  log "$log_prefix: moving to in-review, running gates"

  # Move ticket to in-review
  scripted_move "$dept" "$ticket" in-review "TDD-GREEN complete; engineer exited cleanly"

  # Reset gates_passed before running gates (fresh evaluation)
  local _GF
  for _GF in "$COMPANY_DIR/departments/$dept/tickets/in-review/${ticket}.json" \
             "$COMPANY_DIR/departments/$dept/tickets/in-progress/${ticket}.json"; do
    if [ -f "$_GF" ]; then
      jq '.gates_passed = false' "$_GF" > "${_GF}.tmp.$$" && mv "${_GF}.tmp.$$" "$_GF"
      break
    fi
  done

  # Per-ticket gate output log (for dashboard "View gate" button)
  local GATE_LOG="$COMPANY_DIR/logs/gate-${ticket}.log"

  # Run gates synchronously
  local GATES_RC=0
  GATE_LOG="$GATE_LOG" bash "$SKILL_DIR/lib/tools/verification-gates.sh" \
    "$dept" "$PROJECT_ROOT" --ticket "$ticket" || GATES_RC=$?

  if [ "$GATES_RC" -eq 1 ]; then
    # skip_tdd_red flag set — bounce to tdd-red
    log "$log_prefix: skip_tdd_red flag set — bouncing to tdd-red"
    jq '.bounce_count = ((.bounce_count // 0) + 1)' "$TFILE" > "${TFILE}.tmp.$$" && mv "${TFILE}.tmp.$$" "$TFILE"
    scripted_move "$dept" "$ticket" in-progress "Engineer flagged skip_tdd_red: test needs rework"
    jq '.skip_tdd_red = true' "$TFILE" > "${TFILE}.tmp.$$" && mv "${TFILE}.tmp.$$" "$TFILE"
    return 1
  elif [ "$GATES_RC" -ne 0 ]; then
    # Gates failed — bounce to tdd-green
    log "$log_prefix: gates failed (rc=$GATES_RC) — bouncing to tdd-green"
    jq '.bounce_count = ((.bounce_count // 0) + 1)' "$TFILE" > "${TFILE}.tmp.$$" && mv "${TFILE}.tmp.$$" "$TFILE"
    scripted_move "$dept" "$ticket" in-progress "Gate failure"
    return 1
  fi

  log "$log_prefix: gates passed"

  # ── Phase 4: Verification ──
  log "$log_prefix: spawning verification IC"
  local VERIF_AGENT VERIF_FILE LOG_TO CURRENT_STATUS

  VERIF_AGENT="verification-ic"
  # Check if admin dept
  local is_admin=0
  if [ -f "$SKILL_DIR/lib/tools/dept-helpers.sh" ]; then
    # shellcheck disable=SC1091
    . "$SKILL_DIR/lib/tools/dept-helpers.sh"
    if is_admin_dept "$dept"; then
      VERIF_AGENT="verification-ic-admin"
    fi
  fi

  VERIF_FILE="$SKILL_DIR/lib/agents/${VERIF_AGENT}.md"
  [ -f "$VERIF_FILE" ] || VERIF_FILE="$SKILL_DIR/lib/agents/verification-ic.md"

  PROMPT="$(load_agent_prompt "$VERIF_FILE")

--- STAGE: verify (ticket: $ticket, dept: $dept) ---
Mode: verification IC.

Ticket: $ticket — $TITLE
Description:
$DESC

This ticket has passed all deterministic gates (worktree, commits, TDD order,
tests-exist, tests-pass, lint-pass, security-clean). Your job is to review
the implementation for logic, design, and correctness.

Steps:
1. Read the ticket: mcp__hive__ticket_get(dept=\"$dept\", ticket_id=\"$ticket\")
2. Read the PR diff (or the branch diff):
     cd $PROJECT_ROOT/.worktrees/$ticket
     git diff main...HEAD
3. Re-read the failing test and confirm it now passes.
4. Check edge cases, error handling, and code quality.
5. If satisfied: approve via mcp__hive__ticket_approve.
6. If not satisfied: bounce via mcp__hive__ticket_bounce with a numbered
   rationale (>=80 chars, file:line references where applicable).

You MUST NOT:
  - move the ticket manually (use ticket_approve or ticket_bounce)
  - touch PRs
  - implement code

After approve or bounce, EXIT. This script handles the status move."

  LOG_TO="$COMPANY_DIR/logs/verification-${dept}.log"
  mkdir -p "$(dirname "$LOG_TO")"

  HIVE_AGENT="$VERIF_AGENT" HIVE_DEPT="$dept" HIVE_ROLE="Verification-IC" HIVE_TICKET="$ticket" \
  bash "$SKILL_DIR/lib/core/agent-spawn.sh" -- "$PROMPT" \
    9<&- >> "$LOG_TO" 2>&1
  EC=$?

  if [ $EC -ne 0 ]; then
    log "$log_prefix: verifier failed (exit=$EC), bouncing to in-progress"
    jq '.bounce_count = ((.bounce_count // 0) + 1)' "$TFILE" > "${TFILE}.tmp.$$" && mv "${TFILE}.tmp.$$" "$TFILE"
    scripted_move "$dept" "$ticket" in-progress "Verifier failed (exit=$EC)"
    return 1
  fi

  # Check verifier result: approved → done; bounced → in-progress
  CURRENT_STATUS=$(jq -r '.status // "unknown"' \
    "$TFILE" 2>/dev/null)
  if [ "$CURRENT_STATUS" = "unknown" ]; then
    CURRENT_STATUS=$(jq -r '.status // "unknown"' \
      "$COMPANY_DIR/departments/$dept/tickets/done/${ticket}.json" 2>/dev/null)
  fi

  if [ "$CURRENT_STATUS" = "done" ]; then
    log "$log_prefix: verified and approved → done"
    return 0
  else
    # Bounced — check bounce_count
    bc=$(jq -r '.bounce_count // 0' "$TFILE" 2>/dev/null)
    if [ "${bc:-0}" -ge "$MAX_VERIFICATION_BOUNCES" ]; then
      log "$log_prefix: bounce limit exceeded ($bc >= $MAX_VERIFICATION_BOUNCES) → backlog"
      scripted_move "$dept" "$ticket" backlog "Bounce limit exceeded ($bc >= $MAX_VERIFICATION_BOUNCES)"
      # Reset bounce_count for future sprints
      jq 'del(.bounce_count)' "$TFILE" > "${TFILE}.tmp.$$" 2>/dev/null && mv "${TFILE}.tmp.$$" "$TFILE"
    else
      log "$log_prefix: bounced by verifier (bounce_count=$bc) → in-progress"
    fi
    return 1
  fi
}

# ── Main lifecycle loop ─────────────────────────────────────────────

log "starting sprint lifecycle (project=$PROJECT_ROOT)"

# ── Sprint initialization (CEO + VP planning) ──
run_ceo_plan
run_vp_plan
run_sprint_fill

log "sprint-fill complete, entering ticket processing loop"

LOOP_ITERATION=0
SAFETY_DEPT=""
SAFETY_TICKET=""

while true; do
  # Check if any active tickets remain
  if ! has_active_tickets; then
    log "no active tickets remaining → advancing to build-and-test"
    break
  fi

  # Pick next ticket
  PICKED=$(pick_next_ticket)
  if [ -z "$PICKED" ]; then
    log "no tickets to process → advancing to build-and-test"
    break
  fi

  DEPT=$(echo "$PICKED" | awk '{print $1}')
  TICKET=$(echo "$PICKED" | awk '{print $2}')

  # Safety valve: check loop iteration count
  LOOP_ITERATION=$((LOOP_ITERATION + 1))
  if [ "$LOOP_ITERATION" -gt "$MAX_LOOP_ITERATIONS" ]; then
    SAFETY_DEPT="$DEPT"
    SAFETY_TICKET="$TICKET"
    break
  fi

  # Run this ticket's full lifecycle
  run_ticket_lifecycle "$DEPT" "$TICKET"
  RC=$?

  if [ $RC -eq 0 ]; then
    log "$DEPT/$TICKET: lifecycle complete → next ticket"
  else
    log "$DEPT/$TICKET: bounced → re-enqueue or next ticket"
  fi

  # Brief pause between tickets to let filesystem settle
  sleep 0.5
done

# ── Safety valve: defer only the stuck ticket ──
if [ -n "$SAFETY_TICKET" ]; then
  log "safety valve: $SAFETY_DEPT/$SAFETY_TICKET exceeded MAX_LOOP_ITERATIONS → backlog"
  scripted_move "$SAFETY_DEPT" "$SAFETY_TICKET" backlog "Safety valve: loop iteration $LOOP_ITERATION exceeded"
  TFILE="$COMPANY_DIR/departments/$SAFETY_DEPT/tickets/backlog/${SAFETY_TICKET}.json"
  jq 'del(.bounce_count)' "$TFILE" > "${TFILE}.tmp.$$" 2>/dev/null && mv "${TFILE}.tmp.$$" "$TFILE"
  log "stuck ticket deferred; checking for remaining active tickets"
fi

# ── Release pipeline ──
if has_active_tickets; then
  log "active tickets remain after loop → deferring remaining and advancing"
  defer_remaining_to_backlog "Deferred: sprint advancement"
fi

# Build-and-test
log "build-and-test: running release test suite"
if bash "$SKILL_DIR/lib/tools/release-test-suite.sh" "$PROJECT_ROOT"; then
  log "build-and-test PASS"
else
  log "build-and-test FAIL"
  ss_inc_release_retry
  RETRY=$(ss_get_field .release_retry_count)
  if [ "$RETRY" -gt "$MAX_RELEASE_RETRIES" ]; then
    log "release retry cap exceeded ($RETRY > $MAX_RELEASE_RETRIES) → vp-review"
    if [ -x "$SKILL_DIR/lib/tools/ticket-create.sh" ]; then
      scripted_move "it" "RELEASE-BLOCKER" backlog \
        "Release blocked after $RETRY retries"
    fi
    ss_set_release_field publish_status failed
    ss_set_stage vp-review
  else
    log "running bugfix cycle (attempt $RETRY)"
    run_ceo_plan
    run_vp_plan
    run_sprint_fill
    LOOP_ITERATION=0
    ss_set_stage tdd-red
    exit 0
  fi
fi

# Release-verify
log "release-verify: spawning release IC"
VERSION=$(ss_get_field .release.version)
TEST_LOG=$(ss_get_field .release.test_log)
TARBALL=$(ss_get_field .release.tarball)
SHA256=$(ss_get_field .release.sha256)

. "$SKILL_DIR/lib/tools/load-agent-prompt.sh"
PROMPT="$(load_agent_prompt "$SKILL_DIR/lib/agents/release-ic.md")

--- STAGE: release-verify ---
Mode: release-ic (adversarial global review).

You are reviewing the build before publish. Your job is to find ANY reason
this release should NOT ship.

Sprint id:    $(ss_get_field .sprint_id)
Version:      ${VERSION:-(none)}
Tarball:      ${TARBALL:-(none yet)}
SHA256:       ${SHA256:-(none yet)}
Test log:     ${TEST_LOG:-(no test log)}

Steps (be thorough):
1. Read the test log. Sample 1-2 tests and re-run them.
2. Extract the tarball; run packaging/install.sh; run hive doctor.
3. Verify VERSION matches.
4. Recompute sha256; verify it matches.
5. Compare changelog to merged PRs.
6. Try the documented happy path.

Write your verdict to stdout:
  VERDICT: GO|NO-GO
  RATIONALE: <one paragraph>

After writing the verdict, EXIT."

VERIFY_LOG_PATH="$COMPANY_DIR/releases/${VERSION:-unversioned}-verify.log"
ss_set_release_field verify_log "$VERIFY_LOG_PATH"
mkdir -p "$(dirname "$VERIFY_LOG_PATH")"

HIVE_AGENT="release-ic" HIVE_DEPT="executive" HIVE_ROLE="Release-IC" HIVE_TICKET="" \
bash "$SKILL_DIR/lib/core/agent-spawn.sh" -- "$PROMPT" \
  9<&- >> "$VERIFY_LOG_PATH" 2>&1

VERDICT=$(grep -m1 -E '^VERDICT:' "$VERIFY_LOG_PATH" 2>/dev/null | awk -F: '{gsub(/^[ ]+/,"",$2); print $2}')

if [ "$VERDICT" = "GO" ]; then
  log "release-verify GO"
else
  log "release-verify NO-GO (verdict=$VERDICT)"
  ss_inc_release_retry
  RETRY=$(ss_get_field .release_retry_count)
  if [ "$RETRY" -gt "$MAX_RELEASE_RETRIES" ]; then
    log "release retry cap exceeded → vp-review"
    if [ -x "$SKILL_DIR/lib/tools/ticket-create.sh" ]; then
      scripted_move "it" "RELEASE-BLOCKER" backlog \
        "Release blocked after $RETRY retries — release-IC verdict NO-GO"
    fi
    ss_set_release_field publish_status failed
    ss_set_stage vp-review
  else
    log "running bugfix cycle (attempt $RETRY)"
    run_ceo_plan
    run_vp_plan
    run_sprint_fill
    LOOP_ITERATION=0
    ss_set_stage tdd-red
    exit 0
  fi
fi

# Release-publish
log "release-publish: running"
if bash "$SKILL_DIR/lib/tools/release-publish-runner.sh" "$PROJECT_ROOT"; then
  log "release-publish OK"
else
  log "release-publish FAILED — proceeding to vp-review anyway"
fi

# VP-review
log "vp-review: spawning VPs"
SPRINT_ID=$(ss_get_field .sprint_id)
PUBLISH_STATUS=$(ss_get_field .release.publish_status)
REPORT_DIR="$COMPANY_DIR/reports/sprint-reviews"
mkdir -p "$REPORT_DIR"

DEPTS=(hr it rd marketing legal finance)
if [ -f "$COMPANY_DIR/org-chart.json" ]; then
  while IFS= read -r d; do
    [ -z "$d" ] && continue
    case " ${DEPTS[*]} " in *" $d "*) ;; *) DEPTS+=("$d") ;; esac
  done < <(jq -r '.departments | to_entries[]? | select(.value.type=="dynamic") | .key' "$COMPANY_DIR/org-chart.json" 2>/dev/null)
fi

for dept in "${DEPTS[@]}"; do
  case "$dept" in
    hr) agent="vp-hr" ;; it) agent="vp-it" ;; rd) agent="vp-rd" ;;
    marketing) agent="vp-marketing" ;; legal) agent="vp-legal" ;;
    finance) agent="vp-finance" ;; *) agent="vp-dept" ;;
  esac
  agent_file="$SKILL_DIR/lib/agents/${agent}.md"
  [ -f "$agent_file" ] || continue
  REPORT_PATH="$REPORT_DIR/${SPRINT_ID}-${dept}.md"

  PROMPT="$(load_agent_prompt "$agent_file")

--- STAGE: vp-review (department: $dept) ---
Mode: vp-review.

This is post-release. Sprint id: $SPRINT_ID
Release status: ${PUBLISH_STATUS:-(unknown)}

Write the dept's sprint review report to:
  $REPORT_PATH

Format (~400 words max):
# $dept sprint review — $SPRINT_ID
## What shipped
## What didn't ship
## Release outcome
## Signals to leadership

After writing, EXIT."

  LOG_TO="$COMPANY_DIR/logs/vp-${dept}.log"
  mkdir -p "$(dirname "$LOG_TO")"

  HIVE_AGENT="$agent" HIVE_DEPT="$dept" HIVE_ROLE="VP" HIVE_TICKET="" \
  bash "$SKILL_DIR/lib/core/agent-spawn.sh" -- "$PROMPT" \
    9<&- >> "$LOG_TO" 2>&1 || log "  VP $dept review failed"
done

log "vp-review complete"

# ── Archive and start new sprint ──
log "sprint complete; archiving and starting new sprint"
ss_archive_and_start_new

log "sprint lifecycle complete (new sprint at ceo-plan)"
exit 0
