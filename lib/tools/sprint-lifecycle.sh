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
# load_agent_prompt is used on code paths that skip planning (resume at the
# ticket loop, vp-review after a halted relaunch) - source it unconditionally
# instead of relying on run_vp_plan to have sourced it earlier in this shell.
. "$SKILL_DIR/lib/tools/load-agent-prompt.sh"
ss_init

LOG="$COMPANY_DIR/logs/sprint-lifecycle.log"
mkdir -p "$COMPANY_DIR/logs"
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() {
  # Item 6: stdout only. The runner is started with
  # ">> sprint-lifecycle.log 2>&1" (company-start.sh) and health
  # restarts use the same redirect (item 6 fixes the filename), so the
  # file gets EVERY line exactly once. The old "| tee -a $LOG >&2"
  # wrote each line twice (tee + the redirected copy) and doubled
  # every sequence in the log the operator reads.
  echo "[$(ts)] $*"
}

# ── Config ──────────────────────────────────────────────────────────
MAX_VERIFICATION_BOUNCES="${HIVE_MAX_VERIFICATION_BOUNCES:-5}"
MAX_RELEASE_RETRIES="${HIVE_MAX_RELEASE_RETRIES:-3}"
MAX_LOOP_ITERATIONS="${HIVE_MAX_LOOP_ITERATIONS:-100}"
HIVE_SPRINT_MAX_ACTIVE_TICKETS="${HIVE_SPRINT_MAX_ACTIVE_TICKETS:-5}"

# A1: how often an idle runner polls for work (env-overridable so tests can
# run the wait fast, same convention as HIVE_HEALTH_INTERVAL).
IDLE_POLL_INTERVAL="${HIVE_IDLE_POLL_INTERVAL:-30}"

# A2: the LLM-outage circuit breaker. A planning/release-verify spawn that
# dies because the endpoint did not answer bumps a persisted counter; at
# the threshold the runner HALTS with escalation instead of spinning empty
# sprints. Env-overridable so tests can trip the halt fast (same convention
# as HIVE_IDLE_POLL_INTERVAL).
LLM_OUTAGE_LIMIT="${HIVE_LLM_OUTAGE_LIMIT:-3}"
LLM_OUTAGE_COUNTER_FILE="$COMPANY_DIR/status/llm-outage-count"

# ── Singleton lock ──────────────────────────────────────────────────
RUNNER_PID="$COMPANY_DIR/rounds/runner.pid"
RUNNER_HEARTBEAT="$COMPANY_DIR/rounds/runner-heartbeat"
mkdir -p "$(dirname "$RUNNER_PID")"
PRIOR_PID=$(cat "$RUNNER_PID" 2>/dev/null | head -1 | tr -dc '0-9')
if [ -n "$PRIOR_PID" ] && [ "$PRIOR_PID" != "$$" ] && kill -0 "$PRIOR_PID" 2>/dev/null; then
  if [ "${HIVE_RUNNER_FORCE:-0}" != "1" ]; then
    # Item 6: a LIVE prior runner is refused, not killed. The 2026-11-06
    # double-runner mess came from silent takeovers (this script used
    # to SIGTERM/KILL the prior runner and carry on); two runners in
    # one company interleave sprints and rounds. Refusal is explicit
    # and names the operator actions; HIVE_RUNNER_FORCE=1 overrides
    # for a deliberate takeover.
    echo "Refusing to start: another sprint-lifecycle runner is active (pid=$PRIOR_PID)." >&2
    echo "Two runners in one company corrupt sprints and rounds. Run hive stop first," >&2
    echo "or set HIVE_RUNNER_FORCE=1 to deliberately take over." >&2
    exit 1
  fi
  echo "HIVE_RUNNER_FORCE=1: taking over from live runner pid=$PRIOR_PID" >&2
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
  echo "Refusing to start: another sprint-lifecycle holds the runner lock." >&2
  exit 1
fi
echo $$ >&9

# Item 6: heartbeat - lets health and ops distinguish a live runner
# from a crashed one (dead pid + stale heartbeat = crash, not just a
# cleaned-up pidfile). Written each loop iteration and while idling.
runner_heartbeat() {
  printf '{ "pid": %s, "ts": "%s" }\n' "$$" "$(ts)" > "$RUNNER_HEARTBEAT" 2>/dev/null || true
}
runner_heartbeat

ROUND_FILE="$COMPANY_DIR/rounds/current-round.json"
# G1: rounds/current-round.json is an append-only ACTIVITY LOG with one
# entry schema, and the lifecycle is its ONLY writer — through the state
# layer (lib/hive_state, op round-append). Per-file flock + atomic
# tmp+os.replace + schema validation live there; a racing writer can no
# longer clobber an entry that lands mid read-modify-write cycle.
round_ensure(){ :; }  # round-append creates the log on first write
round_entry(){ # kind agent dept role ticket status
  hs round-append --stage "$1" --agent "$2" --dept "$3" --role "$4" \
    --ticket "$5" --status "$6" >/dev/null 2>&1 || true
}
round_archive(){
  hs round-archive >/dev/null 2>&1 || true
}
# Absolute .company/ path -> company-relative (for hs <op> rel-path args).
hs_rel(){ local f="$1"; printf '%s\n' "${f#"$COMPANY_DIR"/}"; }
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

# Check if any backlog tickets exist anywhere (A1: the wake condition).
has_backlog_tickets() {
  local depts_dir="$COMPANY_DIR/departments"
  [ -d "$depts_dir" ] || return 1
  local n
  n=$(find "$depts_dir"/*/tickets/backlog -maxdepth 1 -name '*.json' -type f 2>/dev/null | wc -l)
  [ "$n" -gt 0 ]
}

# ── A1: idle on a zero-ticket sprint ────────────────────────────────
# A company with no work is a legitimate state, not a bug to paper over:
# the old fall-through ("no active tickets → advancing to build-and-test")
# let a brand-new company "complete" sprints with failed unversioned
# releases and no archived work, and an exiting runner burned the health
# restart budget on pure idling. Now: after planning + fill, if there are
# no active tickets AND the backlog is empty, the runner goes IDLE — no
# build-and-test, no release-verify, no vp-review, no archive, no exit.
# It polls for backlog / sprint-current work; when work appears it runs
# sprint-fill and re-enters the ticket loop. Returns 1 (and does nothing)
# when there IS work, so the caller just falls through.
enter_idle_if_no_work() {
  has_active_tickets && return 1
  has_backlog_tickets && return 1

  ss_set_stage idle
  ss_sync_manifest '.idle_mode = true'
  log "IDLE: no active tickets and empty backlog — going idle (polling every ${IDLE_POLL_INTERVAL}s; no release, no archive, no exit)"
  while true; do
    sleep "$IDLE_POLL_INTERVAL"
    runner_heartbeat
    if has_active_tickets || has_backlog_tickets; then
      break
    fi
  done

  ss_sync_manifest '.idle_mode = false'
  log "IDLE: work detected — running sprint-fill and re-entering the ticket loop"
  run_sprint_fill
  ss_set_stage tdd-red
  return 0
}

# ── A2: LLM-outage circuit breaker ────────────────────────────────
# The harness already fails fast with result subtype llm-unavailable when
# the endpoint cannot answer (the 0.9.0 B.4 fail-fast), but the lifecycle
# used to ignore the exit codes and "complete" the sprint anyway — 14 dead
# spawns per sprint, a failed unversioned release, nothing escalated. Now
# every planning spawn (ceo-plan, vp-plan) and release-verify spawn that
# fails llm-unavailable increments a persisted counter under .company/status/
# (the existing health-status-dir pattern); any successful planning spawn
# resets it to zero. At LLM_OUTAGE_LIMIT the runner HALTS: an incident in
# chat/incidents.md names the error, the endpoint and the sprint; the
# RELEASE-BLOCKER ticket makes the blockage visible to the next planning;
# a halt marker in .company/rounds/halt tells company-health.sh that this
# death is an operator action, not a crash to heal (no restart, no
# restart-budget burn). 'hive start' clears the marker and the counter.
llm_outage_count() {
  local n
  n=$(cat "$LLM_OUTAGE_COUNTER_FILE" 2>/dev/null | tr -dc '0-9')
  echo "${n:-0}"
}

# $1: the spawn log; $2: the stage; $3: the spawn exit code.
# Counts ONLY a failure that is specifically "the endpoint did not
# answer": the harness's fail-fast result event is the LAST line of the
# spawn log (a stale llm-unavailable line from an earlier run in the same
# appended log must not count), and a generic spawn failure (MCP hiccup,
# bad prompt, non-LLM crash) never halts the company.
llm_outage_note_failure() {
  local spawn_log="$1" stage="$2" exit_code="$3"
  [ "$exit_code" -eq 0 ] 2>/dev/null && return 0
  [ -f "$spawn_log" ] || return 0
  tail -n 1 "$spawn_log" 2>/dev/null | grep -q '"subtype": "llm-unavailable"' || return 0
  local n
  n=$(( $(llm_outage_count) + 1 ))
  mkdir -p "$(dirname "$LLM_OUTAGE_COUNTER_FILE")"
  echo "$n" > "$LLM_OUTAGE_COUNTER_FILE"
  log "LLM outage counter: $n/$LLM_OUTAGE_LIMIT (stage=$stage)"
  if [ "$n" -ge "$LLM_OUTAGE_LIMIT" ]; then
    llm_outage_halt "$stage"
  fi
}

# A successful planning spawn means the endpoint answers — reset the
# outage counter (the marker is cleared by 'hive start' itself).
llm_outage_reset() {
  rm -f "$LLM_OUTAGE_COUNTER_FILE" 2>/dev/null || true
}

# Resolve the endpoint the harness would call: HIVE_API_BASE env first
# (what the daemons run with), then [llm].base_url from the config chain
# (user config > defaults). Named in the incident and the halt marker so
# the operator knows WHICH endpoint to fix.
llm_outage_endpoint() {
  # G5: config is read through config-get.sh (one TOML parser) — the awk
  # re-parse of the config chain lived here and drifted from the Python
  # resolver.
  local e="${HIVE_API_BASE:-}"
  if [ -z "$e" ]; then
    e="$(bash "$SKILL_DIR/lib/tools/config-get.sh" llm base_url 2>/dev/null || true)"
  fi
  echo "${e:-unconfigured}"
}

llm_outage_halt() {
  local stage="$1"
  local sprint_id endpoint ts failed_spawns halt_reason
  sprint_id=$(ss_get_field .sprint_id)
  endpoint=$(llm_outage_endpoint)
  failed_spawns=$(llm_outage_count)
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  halt_reason="LLM unavailable after $failed_spawns failed LLM spawns (last stage: $stage; threshold: $LLM_OUTAGE_LIMIT)"

  # 1. The incident: who, what, where, and the operator's next step.
  mkdir -p "$COMPANY_DIR/chat"
  {
    echo ""
    echo "## LLM outage — runner halted ($ts)"
    echo ""
    echo "- Sprint: $sprint_id"
    echo "- Endpoint: $endpoint"
    echo "- Failed LLM spawns: $failed_spawns (threshold $LLM_OUTAGE_LIMIT; last stage $stage)"
    echo "- Error: LLM unavailable — the endpoint answered no usable response"
    echo "- Action: fix the endpoint (availability / auth / model), then run 'hive start' to re-arm the runner."
  } >> "$COMPANY_DIR/chat/incidents.md"
  log "LLM outage incident written to chat/incidents.md"

  # 2. A visible critical bug ticket in the it backlog, so the blockage
  # survives the halt — the retry-cap blocker path can never fire from an
  # outage (an unparseable verdict deliberately does not burn a retry).
  # The text names the OUTAGE and the stage that halted; this is not a
  # release-pipeline failure (no release was attempted at $stage).
  file_release_blocker \
    "BLOCKER ($sprint_id): LLM unavailable - runner halted at $stage after $failed_spawns failed LLM spawns" \
    "The sprint runner for $sprint_id halted because the LLM endpoint did not answer (last stage: $stage, $failed_spawns consecutive failed spawns; threshold $LLM_OUTAGE_LIMIT). See .company/rounds/halt and .company/logs/sprint-lifecycle.log. Fix the endpoint (availability / auth / model), then run 'hive start' to re-arm the runner."

  # 3. The halt marker: company-health.sh marks a runner that dies under
  # a fresh marker dead for the session (no restart, no budget burn);
  # 'hive start' clears it.
  mkdir -p "$COMPANY_DIR/rounds"
  cat > "$COMPANY_DIR/rounds/halt" <<HALT
{
  "reason": "$halt_reason",
  "endpoint": "$endpoint",
  "sprint_id": "$sprint_id",
  "failed_spawns": $failed_spawns,
  "limit": $LLM_OUTAGE_LIMIT,
  "stage": "$stage",
  "ts": "$ts"
}
HALT

  log "HALT: $halt_reason"
  log "HALT: halt marker at .company/rounds/halt — the runner is exiting; 'hive start' clears the marker and re-arms"
  exit 1
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

# Defer one ticket to backlog after it hit the per-ticket bounce cap.
# The bounce_count is reset on the ticket's NEW (backlog) location — the
# scripted move has already relocated the file, so jq on the old path would
# be a silent no-op and the stale counter would poison the next sprint
# (the ticket would be cap-deferred on its very first bounce there).
defer_on_bounce_cap() {
  local dept="$1" ticket="$2" bc="$3" source="$4"
  local f
  scripted_move "$dept" "$ticket" backlog \
    "Bounce limit exceeded ($bc >= $MAX_VERIFICATION_BOUNCES, $source)"
  f="$COMPANY_DIR/departments/$dept/tickets/backlog/${ticket}.json"
  if [ -f "$f" ]; then
    hs jq "$(hs_rel "$f")" 'del(.bounce_count)' >/dev/null || log "  bounce_count clear failed for $f"
  else
    log "  defer_on_bounce_cap: $dept/$ticket not found in backlog after move"
  fi
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

# File a blocker ticket in the it backlog so a blockage is visible to the
# next sprint's planning (sprint-fill promotes it there).
# The old code did `scripted_move "it" "RELEASE-BLOCKER" backlog` — a MOVE
# of a ticket that no code path ever creates (ids are generated IT-NNNN),
# which always failed, leaving the blockage visible only in sprint state.
# $1: title, $2: description — each caller names ITS OWN blockage. The old
# single hardcoded body said "release blocked after N failed release
# attempts" and pointed at .company/releases/ logs; for an LLM-outage halt
# at vp-plan that was fiction (no release was ever attempted) — the 2026-11-06
# field run's IT-0002 read exactly that way.
file_release_blocker() {
  local title="$1" description="$2"
  local tid
  [ -x "$SKILL_DIR/lib/tools/ticket-create.sh" ] || return 0
  tid=$(bash "$SKILL_DIR/lib/tools/ticket-create.sh" \
      "it" "bug" "critical" "$title" "$description" \
      "sprint-lifecycle" "sprint-planning" 2>>"$LOG")
  if [ -n "$tid" ]; then
    log "blocker ticket filed: $tid"
  else
    log "  failed to file blocker ticket ($title)"
  fi
}

# ── CEO planning ────────────────────────────────────────────────────
# $1: the stage to record while the CEO plans (ceo-plan for a new sprint,
# ceo-bugfix-plan for a release bugfix cycle). The stage is the dispatch
# contract: ticket-move.sh keys VP rejection windows off it, and startup
# decides whether a relaunch re-plans or resumes.
# Item 4: post-spawn verification. A planning spawn that exits 0 is NOT
# proof a plan happened: the 2026-11-06 field run's CEO ended 0 after
# executing ZERO actions (its one command sat in a fence the old parser
# dropped) and the sprint ran directionless — every VP planned blind. So
# after each planning spawn the runner reads the harness's FINAL result
# event from the spawn log (the "type":"result" line the harness prints
# last) and checks num_actions > 0 (plus the CEO's direction file); on
# an unverified plan it warns and retries ONCE, and never logs
# "completed successfully" for a plan with no observable effect.
#
# spawn_num_actions_since <spawn_log> <lines_before>
#   Prints the num_actions of the LAST result event recorded AFTER line
#   <lines_before> of the log (the log is append-mode, shared by
#   repeated spawns, so a stale result line from an earlier spawn must
#   not count). Empty when this spawn wrote no result event (killed, or
#   died before the harness could report).
spawn_num_actions_since() {
  local spawn_log="$1" start_line="$2" line
  [ -f "$spawn_log" ] || return 0
  line=$(tail -n +"$((start_line + 1))" "$spawn_log" 2>/dev/null \
         | grep -F '"type": "result"' | tail -1)
  [ -z "$line" ] && return 0
  printf '%s' "$line" | jq -r '.num_actions // empty' 2>/dev/null
}

run_ceo_plan() {
  log "CEO planning stage"
  ss_set_stage "${1:-ceo-plan}"
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

  # Item 4: verify the plan actually happened. Verified = a result event
  # with num_actions > 0 AND the direction file the CEO was told to
  # write (sprint-direction-write.sh records its path in the sprint
  # state). Neither holding means the CEO did nothing observable —
  # warn + retry ONCE; a second unverified plan is logged loudly and
  # the sprint proceeds without direction (the VPs plan on prior
  # context) instead of claiming success.
  local CEO_VERIFIED=0 attempt EC NA DIRECTION_FILE LINES_BEFORE
  for attempt in 1 2; do
    LINES_BEFORE=$([ -f "$LOG_TO" ] && wc -l < "$LOG_TO" || echo 0)
    round_entry "plan-ceo" "ceo" "executive" "CEO" "" "running" 2>/dev/null || true
    HIVE_AGENT="ceo" HIVE_DEPT="executive" HIVE_ROLE="CEO" HIVE_TICKET="" \
    bash "$SKILL_DIR/lib/core/agent-spawn.sh" -- "$PROMPT" \
      9<&- >> "$LOG_TO" 2>&1
    EC=$?
    round_entry "plan-ceo" "ceo" "executive" "CEO" "" "$([ "$EC" -eq 0 ] && echo done || echo failed)" 2>/dev/null || true
    if [ $EC -ne 0 ]; then
      log "CEO plan failed (exit=$EC), continuing without direction"
      llm_outage_note_failure "$LOG_TO" "${1:-ceo-plan}" "$EC"
      return 0
    fi
    NA=$(spawn_num_actions_since "$LOG_TO" "$LINES_BEFORE")
    DIRECTION_FILE=$(ss_get_field .ceo_direction_path)
    if [ "${NA:-0}" -gt 0 ] 2>/dev/null \
       && [ -n "$DIRECTION_FILE" ] && [ "$DIRECTION_FILE" != "null" ] \
       && [ -f "$DIRECTION_FILE" ]; then
      CEO_VERIFIED=1
      break
    fi
    log "CEO plan UNVERIFIED (num_actions=${NA:-0}, direction file: ${DIRECTION_FILE:-missing}) — exit 0 but the plan has no observable effect"
    if [ "$attempt" = "1" ]; then
      log "CEO plan: retrying once"
    fi
  done
  if [ "$CEO_VERIFIED" = "1" ]; then
    log "CEO plan completed and verified (num_actions=${NA}, direction=${DIRECTION_FILE})"
  else
    log "WARNING: CEO plan not verified after retry — proceeding without a direction document; VPs will plan on prior context"
  fi
  # The endpoint answered in both branches (a down LLM fails the spawn
  # with llm-unavailable before any result event), so clear the outage
  # counter either way.
  llm_outage_reset
}

# ── VP planning ─────────────────────────────────────────────────────
# $1: vp-plan (new sprint) or vp-bugfix-plan (release bugfix cycle).
# Must be recorded BEFORE the VPs spawn: their rejection window is exactly
# these stages (enforced by ticket-move.sh).
run_vp_plan() {
  log "VP planning stage"
  ss_set_stage "${1:-vp-plan}"
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

    local VP_ATTEMPT VPNA VPLINES_BEFORE
    for VP_ATTEMPT in 1 2; do
      VPLINES_BEFORE=$([ -f "$LOG_TO" ] && wc -l < "$LOG_TO" || echo 0)
      round_entry "plan-vp" "$agent" "$dept" "VP" "" "running" 2>/dev/null || true
      HIVE_AGENT="$agent" HIVE_DEPT="$dept" HIVE_ROLE="VP" HIVE_TICKET="" \
      bash "$SKILL_DIR/lib/core/agent-spawn.sh" -- "$PROMPT" \
        9<&- >> "$LOG_TO" 2>&1
      VPEC=$?
      round_entry "plan-vp" "$agent" "$dept" "VP" "" "$([ "$VPEC" -eq 0 ] && echo done || echo failed)" 2>/dev/null || true
      if [ "$VPEC" -ne 0 ]; then
        break
      fi
      # Item 4: an exit-0 VP that executed zero actions planned nothing
      # (the field run's 4/6 VPs ended exactly that way) — warn + retry
      # ONCE before accepting the round.
      VPNA=$(spawn_num_actions_since "$LOG_TO" "$VPLINES_BEFORE")
      if [ "${VPNA:-0}" -gt 0 ] 2>/dev/null; then
        break
      fi
      log "  VP $dept plan UNVERIFIED (num_actions=${VPNA:-0}) — exit 0 but zero actions"
      if [ "$VP_ATTEMPT" = "1" ]; then
        log "  VP $dept plan: retrying once"
      fi
    done
    if [ "$VPEC" -ne 0 ]; then
      log "  VP $dept planning failed (exit=$VPEC)"
      llm_outage_note_failure "$LOG_TO" "${1:-vp-plan}" "$VPEC"
    else
      llm_outage_reset
    fi
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
  ss_set_stage tdd-red
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
  # produced a [TDD-RED] commit: the strike-3 marker was read ONCE before
  # the loop, so the file-level write at strike 3 was invisible to the
  # loop (stale local), and `continue` re-spawned the tester indefinitely
  # — same for a persistently failing agent-spawn (EC!=0).
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

    if [ "$skip_red" = "true" ] || [ "$tdd_red_attempts" -ge 3 ]; then
      log "$log_prefix: skipping tester (skip_tdd_red=$skip_red, attempts=$tdd_red_attempts)"
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

  1. Read the ticket: ticket_get(dept=\"$dept\", ticket_id=\"$ticket\")
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

    round_entry "tdd-red" "ic-tester" "$dept" "IC-Tester" "$ticket" "running" 2>/dev/null || true
    HIVE_AGENT="ic-tester" HIVE_DEPT="$dept" HIVE_ROLE="IC-Tester" HIVE_TICKET="$ticket" \
    bash "$SKILL_DIR/lib/core/agent-spawn.sh" -- "$PROMPT" \
      9<&- >> "$LOG_TO" 2>&1
    EC=$?
    round_entry "tdd-red" "ic-tester" "$dept" "IC-Tester" "$ticket" "$([ "$EC" -eq 0 ] && echo done || echo failed)" 2>/dev/null || true

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
      hs jq "$(hs_rel "$TFILE")" '.tdd_red_attempts = $n' --argjson n "$tdd_red_attempts" >/dev/null \
        || log "  tdd_red_attempts write failed for $TFILE"
      log "$log_prefix: no [TDD-RED] commit (attempt $tdd_red_attempts)"
      if [ "$tdd_red_attempts" -ge 3 ]; then
        # Do NOT set skip_tdd_red here: that flag means 'skip the tester,
        # the engineer works it' and persists across bounces within the
        # sprint (ticket-move only clears it at done), so it would also
        # poison the verifier bounce / merge-conflict semantics later. The
        # persisted tdd_red_attempts counter above is what makes later
        # passes skip the tester.
        bash "$SKILL_DIR/lib/tools/ticket-comment.sh" "$dept" "$ticket" "sprint-lifecycle" \
          "ic-tester ran $tdd_red_attempts times without producing a [TDD-RED] $ticket commit. Skipping the tester from now on; the engineer writes both the test AND the implementation." \
          >> "$LOG" 2>&1 || true
        log "$log_prefix: tester exhausted after $tdd_red_attempts failed attempts"
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
  local AGENT TYPE HAS_REJECTION REWORK_HINT CONFLICT_HINT LAST_REJECTION LAST_CONFLICT TDD_STATE

  TITLE=$(jq -r '.title // ""' "$TFILE" 2>/dev/null)
  DESC=$(jq -r '.description // ""' "$TFILE" 2>/dev/null)
  TYPE=$(jq -r '.type // "feature"' "$TFILE" 2>/dev/null)
  case "$TYPE" in
    infra|infrastructure|chore-infra) AGENT="ic-infra" ;;
    *) AGENT="ic-engineer" ;;
  esac

  # HAS_RED survived the TDD-RED loop: 1 = a [TDD-RED] commit is on the
  # branch; 0 = the tester never produced one (skipped or exhausted), so
  # the engineer must write the failing test itself.
  if [ "$HAS_RED" = "1" ]; then
    TDD_STATE="The branch has a [TDD-RED] commit (the failing test)."
  else
    TDD_STATE="The branch has NO [TDD-RED] commit - the tester could not produce one. Write the failing test first (commit prefix [TDD-RED] $ticket: <summary>), THEN the implementation. Both commits are required."
  fi

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

$TDD_STATE

═══════════════════════════════════════════════════════════════════════════
THINK BEFORE YOU EDIT
═══════════════════════════════════════════════════════════════════════════
Don't dive straight into Edit. First do these in order:

  1. Read the ticket: ticket_get(dept=\"$dept\", ticket_id=\"$ticket\")
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

  round_entry "tdd-green" "$AGENT" "$dept" "IC" "$ticket" "running" 2>/dev/null || true
  HIVE_AGENT="$AGENT" HIVE_DEPT="$dept" HIVE_ROLE="IC" HIVE_TICKET="$ticket" \
  bash "$SKILL_DIR/lib/core/agent-spawn.sh" -- "$PROMPT" \
    9<&- >> "$LOG_TO" 2>&1
  EC=$?
  round_entry "tdd-green" "$AGENT" "$dept" "IC" "$ticket" "$([ "$EC" -eq 0 ] && echo done || echo failed)" 2>/dev/null || true

  if [ $EC -ne 0 ]; then
    log "$log_prefix: engineer failed (exit=$EC), bouncing to in-progress"
    scripted_move "$dept" "$ticket" in-progress "Engineer failed (exit=$EC)"
    return 1
  fi

  # Item 7: a clean exit is not proof of code. The field run moved a ticket
  # to in-review 21 s after an engineer that executed zero actions (bare
  # fences the old parser dropped) - a branch with only a RED commit then
  # sailed through the gates. Require a NEW commit on the feature branch
  # (since the TDD-RED commit, or any commit when RED was skipped) before
  # the in-review move; admin/non-code tickets (no worktree, no branch)
  # are exempt, as are tickets whose RED commit is all that exists.
  local _I7_WT="$PROJECT_ROOT/.worktrees/$ticket"
  local _I7_BR="feature/$ticket"
  local _I7_GIT=""
  if [ -d "$_I7_WT" ]; then
    _I7_GIT="$_I7_WT"
  elif git -C "$PROJECT_ROOT" rev-parse --verify "$_I7_BR" >/dev/null 2>&1; then
    _I7_GIT="$PROJECT_ROOT"
  fi
  if [ -n "$_I7_GIT" ]; then
    local _I7_RED _I7_NEW _I7_BASE
    _I7_RED=$(git -C "$_I7_GIT" log --grep="\[TDD-RED\] ${ticket}" "$_I7_BR" --format=%H -1 2>/dev/null)
    if [ -n "$_I7_RED" ]; then
      _I7_NEW=$(git -C "$_I7_GIT" log "${_I7_RED}..${_I7_BR}" --oneline 2>/dev/null | wc -l)
    else
      _I7_BASE=$(git -C "$_I7_GIT" symbolic-ref --short HEAD 2>/dev/null || echo main)
      _I7_NEW=$(git -C "$_I7_GIT" log "${_I7_BASE}..${_I7_BR}" --oneline 2>/dev/null | wc -l)
    fi
    if [ "${_I7_NEW:-0}" -eq 0 ]; then
      log "$log_prefix: engineer exited 0 but added no commit on $_I7_BR - bouncing to in-progress"
      scripted_move "$dept" "$ticket" in-progress "Engineer committed nothing (no new commit on $_I7_BR)"
      bash "$SKILL_DIR/lib/tools/ticket-comment.sh" "$dept" "$ticket" "sprint-lifecycle" \
        "Engineer exited cleanly but committed nothing on $_I7_BR (no new commit) - bounced to in-progress" \
        >> "$LOG" 2>&1 || true
      hs jq "$(hs_rel "$TFILE")" '.bounce_count = ((.bounce_count // 0) + 1)' >/dev/null \
        || log "  bounce_count increment failed for $TFILE"
      local _I7_BC
      _I7_BC=$(jq -r '.bounce_count // 0' "$TFILE" 2>/dev/null)
      if [ "${_I7_BC:-0}" -ge "$MAX_VERIFICATION_BOUNCES" ]; then
        log "$log_prefix: bounce limit exceeded ($_I7_BC >= $MAX_VERIFICATION_BOUNCES, engineer-no-commit) -> backlog"
        defer_on_bounce_cap "$dept" "$ticket" "$_I7_BC" "engineer-no-commit"
      fi
      return 1
    fi
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
      hs jq "$(hs_rel "$_GF")" '.gates_passed = false' >/dev/null || log "  gates_passed reset failed for $_GF"
      break
    fi
  done

  # Per-ticket gate output log (for dashboard "View gate" button)
  local GATE_LOG="$COMPANY_DIR/logs/gate-${ticket}.log"

  # Run gates synchronously. --wait: block on the per-ticket gate lock if
  # the 90s daemon is mid-run on this ticket (without it a concurrent run
  # corrupts this evaluation; with a non-blocking skip we'd proceed on no
  # evaluation at all).
  local GATES_RC=0
  GATE_LOG="$GATE_LOG" bash "$SKILL_DIR/lib/tools/verification-gates.sh" \
    "$dept" "$PROJECT_ROOT" --ticket "$ticket" --wait || GATES_RC=$?

  if [ "$GATES_RC" -ne 0 ]; then
    # Gates failed (rc=2) or usage error (rc=1) — bounce to tdd-green.
    # On a real gate failure, verification-gates.sh's bounce_ticket has
    # already moved the ticket back to in-progress, so the scripted move
    # below is a no-op and TFILE exists for the counter write; on a usage
    # error (rc=1) the ticket is still in in-review and the move lands
    # it. Move first, then increment — the same order as the
    # verifier-crash path (jq on the in-progress path before the move is
    # a silent no-op on the missing file).
    log "$log_prefix: gates failed (rc=$GATES_RC) — bouncing to tdd-green"
    scripted_move "$dept" "$ticket" in-progress "Gate failure (rc=$GATES_RC)"
    hs jq "$(hs_rel "$TFILE")" '.bounce_count = ((.bounce_count // 0) + 1)' >/dev/null \
        || log "  bounce_count increment failed for $TFILE"
    # Per-ticket bounce cap (release goal A.2): a ticket whose
    # deterministic gates keep failing (flaky test, skip_tdd_red rework
    # loop) previously bounced only against the GLOBAL
    # MAX_LOOP_ITERATIONS safety valve, so one bad ticket could burn up
    # to 100 loop iterations and starve the rest of the sprint. It now
    # defers to backlog at MAX_VERIFICATION_BOUNCES, exactly like the
    # verifier-bounce path.
    bc=$(jq -r '.bounce_count // 0' "$TFILE" 2>/dev/null)
    if [ "${bc:-0}" -ge "$MAX_VERIFICATION_BOUNCES" ]; then
      log "$log_prefix: bounce limit exceeded ($bc >= $MAX_VERIFICATION_BOUNCES, gate-failure) → backlog"
      defer_on_bounce_cap "$dept" "$ticket" "$bc" "gate-failure"
    fi
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
1. Read the ticket: ticket_get(dept=\"$dept\", ticket_id=\"$ticket\")
2. Read the PR diff (or the branch diff):
     cd $PROJECT_ROOT/.worktrees/$ticket
     git diff main...HEAD
3. Re-read the failing test and confirm it now passes.
4. Check edge cases, error handling, and code quality.
5. If satisfied: approve via ticket_approve.
6. If not satisfied: bounce via ticket_bounce with a numbered
   rationale (>=80 chars, file:line references where applicable).

You MUST NOT:
  - move the ticket manually (use ticket_approve or ticket_bounce)
  - touch PRs
  - implement code

After approve or bounce, EXIT. This script handles the status move."

  LOG_TO="$COMPANY_DIR/logs/verification-${dept}.log"
  mkdir -p "$(dirname "$LOG_TO")"

  round_entry "verify" "$VERIF_AGENT" "$dept" "Verification-IC" "$ticket" "running" 2>/dev/null || true
  HIVE_AGENT="$VERIF_AGENT" HIVE_DEPT="$dept" HIVE_ROLE="Verification-IC" HIVE_TICKET="$ticket" \
  bash "$SKILL_DIR/lib/core/agent-spawn.sh" -- "$PROMPT" \
    9<&- >> "$LOG_TO" 2>&1
  EC=$?
  round_entry "verify" "$VERIF_AGENT" "$dept" "Verification-IC" "$ticket" "$([ "$EC" -eq 0 ] && echo done || echo failed)" 2>/dev/null || true

  if [ $EC -ne 0 ]; then
    log "$log_prefix: verifier failed (exit=$EC), bouncing to in-progress"
    # Move FIRST, then write the counter: the ticket is still in in-review
    # here, so $TFILE (the in-progress path) does not exist yet. Writing
    # the increment before the move was jq-on-a-missing-file and the
    # bounce_count silently stayed at 0 — pick_next_ticket's priority-2
    # bounce picker never saw the ticket again, and a crashing verifier
    # abandoned it in in-progress instead of re-processing (or capping) it.
    scripted_move "$dept" "$ticket" in-progress "Verifier failed (exit=$EC)"
    hs jq "$(hs_rel "$TFILE")" '.bounce_count = ((.bounce_count // 0) + 1)' >/dev/null \
        || log "  bounce_count increment failed for $TFILE"
    # A persistently crashing verifier counts toward the same per-ticket
    # cap as bounces (the increment was always meant to feed it).
    bc=$(jq -r '.bounce_count // 0' "$TFILE" 2>/dev/null)
    if [ "${bc:-0}" -ge "$MAX_VERIFICATION_BOUNCES" ]; then
      log "$log_prefix: bounce limit exceeded ($bc >= $MAX_VERIFICATION_BOUNCES, verifier-crash) → backlog"
      defer_on_bounce_cap "$dept" "$ticket" "$bc" "verifier-crash"
    fi
    return 1
  fi

  # Check verifier result: approved → done; bounced → in-progress
  # jq on a missing file prints NOTHING (the // fallback only covers a
  # null value), so an approved ticket (moved to done, TFILE gone) used to
  # read as empty status and fall into the "bounced" branch below - wrong
  # log line, and the done-path check never ran. Treat empty as
  # "file gone; look at the done location".
  CURRENT_STATUS=$(jq -r '.status // empty' "$TFILE" 2>/dev/null)
  if [ -z "$CURRENT_STATUS" ]; then
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
      defer_on_bounce_cap "$dept" "$ticket" "$bc" "verifier-bounce"
    else
      log "$log_prefix: bounced by verifier (bounce_count=$bc) → in-progress"
    fi
    return 1
  fi
}

# ── Main lifecycle loop ─────────────────────────────────────────────

log "starting sprint lifecycle (project=$PROJECT_ROOT)"

# ── Sprint initialization (CEO + VP planning) ──
# Dispatch on the recorded stage: a fresh sprint (ceo-plan) plans; a
# relaunch on an in-progress sprint (crash, or the bugfix-cycle exit 0 —
# which already re-planned inline) resumes the ticket loop instead of
# re-running planning. Re-planning a live sprint double-spends the CEO +
# every VP and risks duplicate ticket filings.
START_STAGE=$(ss_get_stage)
if [ "$START_STAGE" = "ceo-plan" ]; then
  run_ceo_plan
  run_vp_plan
  run_sprint_fill
  ss_set_stage tdd-red
else
  log "stage=$START_STAGE — sprint already planned; resuming at the ticket loop (no re-planning)"
fi

round_ensure
log "entering ticket processing loop"

# A1: the only exit for "there is no work". No-op whenever a ticket is
# active or the backlog holds work (companies starting with tickets behave
# exactly as before); on a zero-ticket company it waits for work instead of
# manufacturing an empty release.
enter_idle_if_no_work

LOOP_ITERATION=0
SAFETY_DEPT=""
SAFETY_TICKET=""

while true; do
  runner_heartbeat
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
  hs jq --no-require "$(hs_rel "$TFILE")" 'del(.bounce_count)' >/dev/null 2>&1
  log "stuck ticket deferred; checking for remaining active tickets"
fi

# ── Release pipeline ──
if has_active_tickets; then
  log "active tickets remain after loop → deferring remaining and advancing"
  defer_remaining_to_backlog "Deferred: sprint advancement"
fi

# RELEASE_BLOCKED=1 once the release is ruled unshippable (retry cap
# exceeded or an unparseable verdict). release-verify and release-publish
# are then skipped: falling through used to spawn a release-ic on a build
# that never happened, and — worse — hand a real tarball to the publisher
# after the release was already marked failed, with a successful publish
# flipping publish_status back to "published".
RELEASE_BLOCKED=0

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
    SPRINT_ID_B=$(ss_get_field .sprint_id)
    file_release_blocker \
      "RELEASE-BLOCKER ($SPRINT_ID_B): release blocked after $RETRY failed release attempts" \
      "The release pipeline for $SPRINT_ID_B stopped: build-and-test failed on attempt $RETRY (retry cap exceeded). Review .company/logs/sprint-lifecycle.log and the release test/verify logs under .company/releases/, fix the root cause, then re-run the release pipeline."
    ss_set_release_field publish_status failed
    ss_set_stage vp-review
    RELEASE_BLOCKED=1
  else
    log "running bugfix cycle (attempt $RETRY)"
    run_ceo_plan ceo-bugfix-plan
    run_vp_plan vp-bugfix-plan
    run_sprint_fill
    LOOP_ITERATION=0
    ss_set_stage tdd-red
    exit 0
  fi
fi

# Release-verify — defined as a function so the release-publish stage
# (and the verify stage itself, on a build that never happened) can be
# skipped when the release is already ruled unshippable.
run_release_verify() {
  ss_set_stage release-verify
  log "release-verify: spawning release IC"
  VERSION=$(ss_get_field .release.version)
  TEST_LOG=$(ss_get_field .release.test_log)
  TARBALL=$(ss_get_field .release.tarball)
  SHA256=$(ss_get_field .release.sha256)

  # A1 defense in depth: a release needs something to release. Require
  # release.version to be set OR the sprint to have carried at least one
  # ticket — a fresh company with no VERSION file can never produce an
  # unversioned-* release of nothing, even on the non-idle path (a ticket
  # that was deferred after bounces still counts: the sprint carried work,
  # and the release test suite ran against its code).
  local TIX SHIPPED=0
  TIX=$(ss_get_field .tickets_in_sprint)
  case "$TIX" in
    ""|null) SHIPPED=0 ;;
    *) SHIPPED=$(jq 'length' <<<"$TIX" 2>/dev/null || echo 0) ;;
  esac
  if [ -z "$VERSION" ] && [ "${SHIPPED:-0}" -eq 0 ]; then
    log "release-verify SKIPPED: no release.version and no tickets in this sprint — nothing to release (a fresh company must never cut an unversioned release)"
    ss_set_release_field verdict_error "nothing to release: no version, no tickets in sprint"
    ss_set_release_field publish_status skipped
    ss_set_stage vp-review
    RELEASE_BLOCKED=1
    return 0
  fi

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

  Record your verdict with the release_verdict MCP tool (or the script
  equivalent, per lib/agents/release-ic.md):
    release_verdict(verdict=GO|NO-GO, rationale=one-paragraph,
        blockers=bullets - only for NO-GO)
    bash $SKILL_DIR/lib/tools/release-verdict-write.sh GO|NO-GO \
      --rationale one-paragraph [--blockers-stdin]
  Both write the release verify log (sprint-state release.verify_log
  path) in the format:
    VERDICT: GO|NO-GO
    RATIONALE: <one paragraph>
    [BLOCKERS: - ...]
  The lifecycle parses the first VERDICT: line of that file. Anything else
  (crash, free text, missing line) is an explicit release FAILURE - there
  is no silent NO-GO default.

  After writing the verdict, EXIT."

  # B1: the verify log is per sprint. The old path (releases/<version>-verify.log,
  # shared by EVERY unversioned sprint and appended across runs) let a VERDICT: GO
  # written by sprint N be parsed inside sprint N+1 (grep -m1 of a file that
  # never ended). Keying the path by sprint id makes each sprint's verdict file
  # its own; consumers (release-verdict-write.sh, the agents, the dashboard)
  # read the path from .release.verify_log, so the switch is seamless.
  local VERIFY_SPRINT_ID
  VERIFY_SPRINT_ID=$(ss_get_field .sprint_id)
  VERIFY_LOG_PATH="$COMPANY_DIR/releases/${VERIFY_SPRINT_ID:-unknown}-${VERSION:-unversioned}-verify.log"
  ss_set_release_field verify_log "$VERIFY_LOG_PATH"
  mkdir -p "$(dirname "$VERIFY_LOG_PATH")"

  round_entry "release-verify" "release-ic" "executive" "Release-IC" "" "running" 2>/dev/null || true
  HIVE_AGENT="release-ic" HIVE_DEPT="executive" HIVE_ROLE="Release-IC" HIVE_TICKET="" \
  bash "$SKILL_DIR/lib/core/agent-spawn.sh" -- "$PROMPT" \
    9<&- >> "$VERIFY_LOG_PATH" 2>&1
  local RIC_EC=$?
  round_entry "release-verify" "release-ic" "executive" "Release-IC" "" "$([ "$RIC_EC" -eq 0 ] && echo done || echo failed)" 2>/dev/null || true
  llm_outage_note_failure "$VERIFY_LOG_PATH" "release-verify" "$RIC_EC"

  VERDICT=$(grep -m1 -E '^VERDICT:' "$VERIFY_LOG_PATH" 2>/dev/null | awk -F: '{gsub(/^[ ]+/,"",$2); print $2}')

  case "$VERDICT" in
    GO)
      log "release-verify GO"
      ss_set_release_field verdict GO
      ;;
    NO-GO)
      log "release-verify NO-GO"
      ss_set_release_field verdict NO-GO
      ss_inc_release_retry
      RETRY=$(ss_get_field .release_retry_count)
      if [ "$RETRY" -gt "$MAX_RELEASE_RETRIES" ]; then
        log "release retry cap exceeded → vp-review"
        local SPRINT_ID_N
        SPRINT_ID_N=$(ss_get_field .sprint_id)
        file_release_blocker \
          "RELEASE-BLOCKER ($SPRINT_ID_N): release blocked after $RETRY failed release attempts" \
          "The release pipeline for $SPRINT_ID_N stopped: the release-IC ruled NO-GO on attempt $RETRY (retry cap exceeded). Review .company/logs/sprint-lifecycle.log and the release test/verify logs under .company/releases/, fix the blockers, then re-run the release pipeline."
        ss_set_release_field publish_status failed
        ss_set_stage vp-review
        RELEASE_BLOCKED=1
      else
        log "running bugfix cycle (attempt $RETRY)"
        run_ceo_plan ceo-bugfix-plan
        run_vp_plan vp-bugfix-plan
        run_sprint_fill
        LOOP_ITERATION=0
        ss_set_stage tdd-red
        exit 0
      fi
      ;;
    *)
      # Unparseable verdict: the release IC recorded no GO/NO-GO line
      # (crashed mid-verification, wrote free text, or used a different
      # format). Fail explicitly - do NOT count it as a NO-GO release
      # decision (it is not one) and do NOT consume a retry. The stage
      # moves to vp-review so the operator sees the failure on the
      # dashboard (verdict_error + publish_status=failed); the retry cap
      # is untouched.
      log "release-verify FAILED: no parseable verdict in $VERIFY_LOG_PATH (got: '${VERDICT:-<empty>}')"
      ss_set_release_field verdict_error "no parseable verdict - see $VERIFY_LOG_PATH"
      ss_set_release_field publish_status failed
      ss_set_stage vp-review
      RELEASE_BLOCKED=1
      ;;
  esac
}

if [ "$RELEASE_BLOCKED" = "1" ]; then
  log "release blocked; skipping release-verify (no build to review)"
else
  run_release_verify
fi

# Release-publish — skipped when the release is already ruled
# unshippable: a failed or unverifiable release must never reach the
# publisher (a successful publish would flip publish_status back to
# 'published').
run_release_publish() {
  ss_set_stage release-publish
  log "release-publish: running"
  if bash "$SKILL_DIR/lib/tools/release-publish-runner.sh" "$PROJECT_ROOT"; then
    ss_set_release_field publish_status published
    log "release-publish OK"
  else
    ss_set_release_field publish_status failed
    log "release-publish FAILED — proceeding to vp-review anyway"
  fi
}

if [ "$RELEASE_BLOCKED" = "1" ]; then
  log "release blocked; skipping release-publish (publish_status already failed)"
else
  run_release_publish
fi

# VP-review
ss_set_stage vp-review
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
round_archive
log "sprint complete; archiving and starting new sprint"
ss_archive_and_start_new

log "sprint lifecycle complete (new sprint at ceo-plan)"
exit 0