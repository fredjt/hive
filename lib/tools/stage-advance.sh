#!/usr/bin/env bash
set -uo pipefail

# stage-advance.sh — the brain. Called by round-runner when current-round
# drains. Reads current sprint stage, decides next stage, performs sync
# actions (sprint-fill, build-and-test, release-publish, orphan-recovery,
# loop-cap rejection, archive) and/or enqueues the next stage's entries.
#
# Loops internally through sync stages until it lands on a stage that
# enqueues queue entries (or until the sprint completes). Then exits so
# round-runner drains the new batch.
#
# Usage: stage-advance.sh <project-root>

PROJECT_ROOT="${1:-${PROJECT_ROOT:-$PWD}}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd)" || { echo "stage-advance: bad project root" >&2; exit 2; }
COMPANY_DIR="$PROJECT_ROOT/.company"
SKILL_DIR="${HIVE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
export COMPANY_DIR PROJECT_ROOT SKILL_DIR

# shellcheck disable=SC1091
. "$SKILL_DIR/lib/tools/sprint-state.sh"
ss_init

LOG="$COMPANY_DIR/logs/stage-advance.log"
mkdir -p "$(dirname "$LOG")"
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "[$(ts)] $*" | tee -a "$LOG" >&2; }

# Config defaults — overridable via etc/config.defaults.toml or HIVE_*_OVERRIDE env
MAX_LOOP_ITERATIONS="${HIVE_MAX_LOOP_ITERATIONS:-100}"                       # safety valve only; defers stuck tickets to backlog
MAX_RELEASE_RETRIES="${HIVE_MAX_RELEASE_RETRIES:-3}"
MAX_VERIFICATION_BOUNCES="${HIVE_MAX_VERIFICATION_BOUNCES:-5}"               # per-ticket bounce limit; defers to backlog

# Helper: enumerate (dept, ticket_id) pairs in a status dir
list_tickets_in() {
  local status="$1"
  local depts_dir="$COMPANY_DIR/departments"
  [ -d "$depts_dir" ] || return 0
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    local tid; tid=$(basename "$f" .json)
    local dept; dept=$(echo "$f" | sed -n 's|.*/departments/\([^/]*\)/tickets/.*|\1|p')
    echo "$dept $tid"
  done < <(find "$depts_dir"/*/tickets/"$status" -maxdepth 1 -name '*.json' -type f 2>/dev/null)
}

# Defer all remaining sprint tickets to BACKLOG (loop-cap safety valve).
# This replaces the old "system-reject to done" behavior — a ticket the
# verification IC is still rejecting MUST NOT be auto-accepted (which is
# what `done` even with reason=rejected effectively does — the ticket
# leaves the queue and is treated as resolved). Demote to backlog so the
# verification IC's standing rejection is preserved, the PR + branch stay
# open, and a future sprint can pick the work back up.
defer_remaining_to_backlog() {
  local reason_text="$1"
  local n=0
  for status in sprint-current in-progress in-review; do
    while IFS=' ' read -r dept tid; do
      [ -z "$dept" ] && continue
      log "defer-to-backlog: $dept/$tid ($reason_text)"
      HIVE_SCRIPTED_MOVE=1 HIVE_ROLE=System HIVE_AGENT=stage-advance \
        bash "$SKILL_DIR/lib/tools/ticket-move.sh" \
          "$dept" "$tid" backlog \
          "Deferred to backlog: $reason_text" \
          "stage-advance" \
        >> "$LOG" 2>&1 || log "  defer of $tid failed (continuing)"
      n=$((n + 1))
    done < <(list_tickets_in "$status")
  done
  log "deferred $n ticket(s) to backlog so sprint can advance"
}

# File a critical IT ticket (used when release retries exhausted)
file_release_blocker_ticket() {
  local version="$1" rationale="$2"
  if [ -x "$SKILL_DIR/lib/tools/ticket-create.sh" ]; then
    HIVE_SCRIPTED_MOVE=1 HIVE_ROLE=System HIVE_AGENT=stage-advance \
      bash "$SKILL_DIR/lib/tools/ticket-create.sh" it bug critical \
        "Release v$version BLOCKED after $MAX_RELEASE_RETRIES retries" \
        "$rationale" "system" "stage-advance" \
        >> "$LOG" 2>&1 || log "  blocker ticket-create failed"
  fi
}

# Helper: enqueue current stage's work + mark enqueued + return.
# Each "spawn stage" (one that enqueues queue entries for the round-runner
# to drain) goes through this. Sync stages (sprint-fill, build-and-test,
# release-publish, complete) don't use this — they're transitions.
enqueue_and_mark() {
  local script="$1"
  bash "$SKILL_DIR/lib/tools/$script" "$PROJECT_ROOT" >> "$LOG" 2>&1
  ss_mark_enqueued
}

# Main advance loop.
#
# Spawn-stage decision: stage_enqueued tracks whether THIS stage's entries
# have been queued already.
#   - false → first call at this stage. Enqueue current stage's work; mark
#     enqueued; return so round-runner drains it.
#   - true  → second call at this stage (queue just drained the work we
#     enqueued). Run any sync side-effects (orphan-recovery, loop-cap
#     check, release retry check, etc.); set next stage (which auto-resets
#     stage_enqueued to false); loop to handle the new stage.
#
# Sync-stage decision: build-and-test / release-publish / complete /
# sprint-fill don't have a "spawn → drain → drain again" pattern. They run
# their script synchronously here and immediately advance.

# Sweep any stale done-duplicates before deciding what to enqueue. If a
# git checkout / merge / pull restored an old "in-progress" or "backlog"
# copy of a ticket that's already in done, that copy gets deleted here so
# downstream stage-enqueue scripts don't try to re-run work on a ticket
# the verifier already approved.
bash "$SKILL_DIR/lib/tools/done-protector.sh" "$PROJECT_ROOT" >> "$LOG" 2>&1 || true

ADVANCED=0
while true; do
  STAGE=$(ss_get_stage)
  ENQUEUED=$(ss_is_enqueued)
  log "advance: stage=$STAGE enqueued=$ENQUEUED"

  case "$STAGE" in

    ceo-plan)
      if [ "$ENQUEUED" != "true" ]; then
        enqueue_and_mark stage-enqueue-ceo-plan.sh
        ADVANCED=1; break
      fi
      ss_set_stage vp-plan
      continue
      ;;

    vp-plan)
      if [ "$ENQUEUED" != "true" ]; then
        enqueue_and_mark stage-enqueue-vp-plan.sh
        ADVANCED=1; break
      fi
      # vp-plan drained → sprint-fill (sync) → tdd-red
      log "running sprint-fill (script-driven)"
      COMPANY_DIR="$COMPANY_DIR" bash "$SKILL_DIR/lib/tools/sprint-fill.sh" \
        --max "${HIVE_SPRINT_MAX_ACTIVE_TICKETS:-5}" >> "$LOG" 2>&1 || true
      TICKETS_JSON=$(list_tickets_in sprint-current | awk '{print $2}' | jq -R . | jq -s -c .)
      ss_set_tickets_in_sprint "$TICKETS_JSON"
      ss_reset_loop_iteration
      ss_set_stage tdd-red
      continue
      ;;

    tdd-red)
      if [ "$ENQUEUED" != "true" ]; then
        enqueue_and_mark stage-enqueue-tdd-red.sh
        ADVANCED=1; break
      fi
      ss_set_stage tdd-green
      continue
      ;;

    tdd-green)
      if [ "$ENQUEUED" != "true" ]; then
        enqueue_and_mark stage-enqueue-tdd-green.sh
        ADVANCED=1; break
      fi
      ss_set_stage verify
      continue
      ;;

    verify)
      if [ "$ENQUEUED" != "true" ]; then
        enqueue_and_mark stage-enqueue-verify.sh
        ADVANCED=1; break
      fi
      # verify drained → orphan-recovery → loop-or-advance
      log "running orphan-recovery"
      bash "$SKILL_DIR/lib/tools/orphan-recovery.sh" "$PROJECT_ROOT" >> "$LOG" 2>&1 || true

      ACTIVE=$(ss_active_ticket_count)
      log "after verify: $ACTIVE active tickets remaining"
      if [ "$ACTIVE" -gt 0 ]; then
        ss_inc_loop_iteration
        ITER=$(ss_get_field .loop_iteration)
        # Loop A normally runs without a cap — verification IC is the only
        # entity that decides "done", and a stuck ticket loops until it
        # passes. The cap below is a SAFETY VALVE, not a quality bar:
        # without it, a single truly-broken ticket would block the sprint
        # from ever reaching release. When it fires, remaining tickets are
        # DEFERRED TO BACKLOG (their verification rejection still stands;
        # PRs stay open). Set max_loop_iterations very high (or unset, in
        # which case 100 is used) — only fires if a ticket is genuinely
        # never going to converge.
        if [ -n "$MAX_LOOP_ITERATIONS" ] && [ "$MAX_LOOP_ITERATIONS" -gt 0 ] \
             && [ "$ITER" -gt "$MAX_LOOP_ITERATIONS" ]; then
          log "Loop A safety valve fired ($ITER > $MAX_LOOP_ITERATIONS); deferring stuck tickets to backlog"
          defer_remaining_to_backlog "loop-safety-valve after $ITER iterations"
          ss_set_stage build-and-test
          continue
        fi
        log "Loop A iteration $ITER: re-entering tdd-red"
        ss_set_stage tdd-red
        continue
      fi
      ss_set_stage build-and-test
      continue
      ;;

    build-and-test)
      # Synchronous (no spawn). Run the project test suite.
      # Exit codes: 0 = pass, 1 = test failure, 3 = resource-killed
      # (timeout/OOM — same loop-back as test failure, but release-test-suite
      # logs the reason so agents know it's a resource issue).
      log "running release-test-suite"
      if bash "$SKILL_DIR/lib/tools/release-test-suite.sh" "$PROJECT_ROOT"; then
        log "build-and-test PASS → release-verify"
        ss_set_stage release-verify
        continue
      fi
      log "build-and-test FAIL → ceo-bugfix-plan (Loop B)"
      ss_inc_release_retry
      RETRY=$(ss_get_field .release_retry_count)
      if [ "$RETRY" -gt "$MAX_RELEASE_RETRIES" ]; then
        log "release retry-cap exceeded ($RETRY > $MAX_RELEASE_RETRIES); skipping bugfix loop"
        file_release_blocker_ticket "$(ss_get_field .release.version)" \
          "Release blocked after $RETRY retries — see $(ss_get_field .release.test_log)."
        ss_set_release_field publish_status failed
        ss_set_stage vp-review
        continue
      fi
      ss_set_stage ceo-bugfix-plan
      continue
      ;;

    release-verify)
      if [ "$ENQUEUED" != "true" ]; then
        enqueue_and_mark stage-enqueue-release-verify.sh
        ADVANCED=1; break
      fi
      # release-verify drained → read verdict
      VERIFY_LOG=$(ss_get_field .release.verify_log)
      VERDICT=""
      if [ -n "$VERIFY_LOG" ] && [ -f "$VERIFY_LOG" ]; then
        VERDICT=$(grep -m1 -E '^VERDICT:' "$VERIFY_LOG" | awk -F: '{gsub(/^[ ]+/,"",$2); print $2}')
      fi
      if [ "$VERDICT" = "GO" ]; then
        log "release-verify GO → release-publish"
        ss_set_stage release-publish
        continue
      fi
      log "release-verify NO-GO (verdict=$VERDICT) → ceo-bugfix-plan (Loop B)"
      ss_inc_release_retry
      RETRY=$(ss_get_field .release_retry_count)
      if [ "$RETRY" -gt "$MAX_RELEASE_RETRIES" ]; then
        log "release retry-cap exceeded ($RETRY > $MAX_RELEASE_RETRIES); skipping bugfix loop"
        file_release_blocker_ticket "$(ss_get_field .release.version)" \
          "Release blocked after $RETRY retries — release-ic verdict NO-GO. See verify log $VERIFY_LOG."
        ss_set_release_field publish_status failed
        ss_set_stage vp-review
        continue
      fi
      ss_set_stage ceo-bugfix-plan
      continue
      ;;

    release-publish)
      log "running release-publish-runner"
      if bash "$SKILL_DIR/lib/tools/release-publish-runner.sh" "$PROJECT_ROOT"; then
        log "release-publish OK → vp-review"
      else
        log "release-publish FAILED — proceeding to vp-review anyway (sprint reports the failure)"
      fi
      ss_set_stage vp-review
      continue
      ;;

    ceo-bugfix-plan)
      if [ "$ENQUEUED" != "true" ]; then
        enqueue_and_mark stage-enqueue-ceo-bugfix-plan.sh
        ADVANCED=1; break
      fi
      ss_set_stage vp-bugfix-plan
      continue
      ;;

    vp-bugfix-plan)
      if [ "$ENQUEUED" != "true" ]; then
        enqueue_and_mark stage-enqueue-vp-bugfix-plan.sh
        ADVANCED=1; break
      fi
      # vp-bugfix-plan drained → sprint-fill (sync) → tdd-red (Loop A
      # re-entry; after Loop A drains, verify's exit advances to
      # build-and-test, NOT vp-review)
      log "running sprint-fill (bugfix-driven)"
      COMPANY_DIR="$COMPANY_DIR" bash "$SKILL_DIR/lib/tools/sprint-fill.sh" \
        --max "${HIVE_SPRINT_MAX_ACTIVE_TICKETS:-5}" >> "$LOG" 2>&1 || true
      TICKETS_JSON=$(list_tickets_in sprint-current | awk '{print $2}' | jq -R . | jq -s -c .)
      ss_set_tickets_in_sprint "$TICKETS_JSON"
      ss_reset_loop_iteration
      ss_set_stage tdd-red
      continue
      ;;

    vp-review)
      if [ "$ENQUEUED" != "true" ]; then
        enqueue_and_mark stage-enqueue-vp-review.sh
        ADVANCED=1; break
      fi
      ss_set_stage complete
      continue
      ;;

    complete)
      log "sprint complete; archiving and starting new sprint"
      ss_archive_and_start_new
      # Fresh sprint at stage=ceo-plan, stage_enqueued=false. Loop will
      # enqueue ceo-plan's work on the next iteration.
      continue
      ;;

    sprint-fill)
      # This stage isn't used by the new design (sprint-fill runs as a
      # transition action, not a tracked stage). Recovery if encountered.
      ss_set_stage vp-plan
      continue
      ;;

    *)
      log "unknown stage '$STAGE' — resetting to ceo-plan"
      ss_set_stage ceo-plan
      continue
      ;;
  esac
done

if [ "$ADVANCED" = 0 ]; then
  log "stage-advance: no advancement (this should not happen)"
  exit 1
fi
log "stage-advance: done; new stage=$(ss_get_stage)"
exit 0
