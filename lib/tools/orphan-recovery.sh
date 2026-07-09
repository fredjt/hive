#!/usr/bin/env bash
set -uo pipefail

# orphan-recovery.sh — bounce in-review tickets where a verification IC ran
# but didn't act (didn't call ticket_approve or ticket_bounce). Runs
# synchronously after stage 5 (verify) drains. Guarantees the end-of-sprint
# invariant: no tickets stuck in in-review with no path forward.
#
# A ticket is "orphaned" if BOTH:
#   1. it's in in-review with verified_by empty, AND
#   2. a verification entry for it in the current round has status=done
#      with a non-null ended_at (meaning a verifier actually ran).
#
# Tickets that JUST arrived in in-review (from a recent ic-cycle-eng exit-0
# move) and haven't been picked up by stage-enqueue-verify yet are NOT
# orphaned — they're newly-arrived. Bouncing them prematurely would create
# busy-loops where the engineer finishes, ticket moves to in-review, and
# orphan-recovery immediately bounces it before any verifier sees it.
# (photonic-proc file-analyzer hit this — 3 IT tickets bounced 0 seconds
# after engineer exit because the verify queue had a long-running entry
# for a different ticket, and the new arrivals piled up during the drain.)
#
# Bounce default: skip_tdd_red=false (re-run RED next loop). The verification
# IC is the only entity that can set skip_tdd_red=true (via --skip-tdd-red on
# bounce); orphaned tickets get the safe default.
#
# Usage: orphan-recovery.sh <project-root>

PROJECT_ROOT="${1:-${PROJECT_ROOT:-$PWD}}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd)" || { echo "orphan-recovery: bad project root" >&2; exit 2; }
COMPANY_DIR="${COMPANY_DIR:-$PROJECT_ROOT/.company}"
SKILL_DIR="${SKILL_DIR:-${HIVE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}}"
export COMPANY_DIR PROJECT_ROOT SKILL_DIR

# shellcheck disable=SC1091
. "$SKILL_DIR/lib/tools/sprint-state.sh"

LOG="$COMPANY_DIR/logs/orphan-recovery.log"
mkdir -p "$(dirname "$LOG")"
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

ROUND_FILE="$COMPANY_DIR/rounds/current-round.json"

# Returns 0 if a verification entry exists in the current round for this
# ticket with status=done and ended_at set (meaning a verifier actually
# ran for it). Returns 1 if no such entry exists (ticket is newly-arrived
# or queued but not yet picked up).
verifier_ran_for() {
  local tid="$1"
  [ -f "$ROUND_FILE" ] || return 1
  jq -e --arg t "$tid" '
    .entries // [] |
    map(select(
      .kind == "verification"
      and .ticket == $t
      and .status == "done"
      and (.ended_at // null) != null
    )) | length > 0
  ' "$ROUND_FILE" >/dev/null 2>&1
}

N=0
SKIPPED_NEW=0
while IFS=' ' read -r dept tid; do
  [ -z "$dept" ] && continue

  # Only bounce if a verifier actually ran for this ticket. Newly-arrived
  # in-review tickets that haven't had a verifier spawned yet are left
  # alone so the next stage-enqueue-verify pass can pick them up.
  if ! verifier_ran_for "$tid"; then
    log "orphan-skip: $dept/$tid (no verification entry in current round yet; will be picked up next verify pass)"
    SKIPPED_NEW=$((SKIPPED_NEW + 1))
    continue
  fi

  log "orphan-bounce: $dept/$tid (verification IC ran but did not act)"

  # Mark skip_tdd_red=false on the ticket file so the next loop's
  # tdd-red stage re-enqueues it.
  TICKET_FILE="$COMPANY_DIR/departments/$dept/tickets/in-review/${tid}.json"
  if [ -f "$TICKET_FILE" ]; then
    tmp="${TICKET_FILE}.tmp.$$"
    jq '.skip_tdd_red = false' "$TICKET_FILE" > "$tmp" && mv "$tmp" "$TICKET_FILE"
  fi

  # Bounce via ticket-move.sh with scripted-move authority.
  HIVE_SCRIPTED_MOVE=1 HIVE_ROLE=Verification-IC HIVE_AGENT=orphan-recovery \
    bash "$SKILL_DIR/lib/tools/ticket-move.sh" \
      "$dept" "$tid" in-progress \
      "orphan-bounce: verification IC ran but did not approve or reject explicitly; auto-bounced for next loop." \
      "orphan-recovery" \
      >> "$LOG" 2>&1 || log "  bounce of $tid failed (see above)"

  N=$((N + 1))
done < <(ss_unverified_in_review_tickets)

log "swept $N orphaned ticket(s)${SKIPPED_NEW:+ (skipped $SKIPPED_NEW newly-arrived ticket(s) awaiting first verify pass)}"
echo "orphan-recovery: bounced $N ticket(s); skipped $SKIPPED_NEW newly-arrived (see $LOG)"
