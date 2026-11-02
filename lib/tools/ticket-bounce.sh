#!/usr/bin/env bash
set -uo pipefail

# ticket-bounce.sh — atomically reject an in-review ticket back to in-progress
# with a mandatory substantial rationale comment recorded on the ticket itself.
#
# This exists because verification ICs were posting rejections to chat but
# (a) skipping ticket-comment.sh and (b) failing the ticket-move.sh call
# because they didn't know to pass --force when bouncing in-review→in-progress.
# This wrapper does both atomically with one call.
#
# Usage: ticket-bounce.sh <dept> <ticket-id> <author> <rationale> [--skip-tdd-red]
#
# Rationale must be >=80 characters. Should be a numbered list of every issue
# found, with file:line references where applicable.
#
# --skip-tdd-red sets `skip_tdd_red: true` on the ticket so the next loop
# iteration skips stage 3 (tdd-red) and goes straight to stage 4 (tdd-green).
# Use ONLY when the existing test is sound and the implementation is wrong;
# i.e., adding more tests wouldn't help. Default (no flag) re-runs RED.

if [ $# -lt 4 ]; then
  echo "Usage: ticket-bounce.sh <dept> <ticket-id> <author> <rationale> [--skip-tdd-red]" >&2
  echo "  rationale: numbered list of every issue, with file:line references where applicable (>=80 chars)" >&2
  exit 1
fi

DEPT="$1"
TICKET_ID="$2"
AUTHOR="$3"
shift 3

# Pull off any trailing flags before treating the remainder as RATIONALE.
SKIP_TDD_RED=0
RATIONALE_ARGS=()
for a in "$@"; do
  case "$a" in
    --skip-tdd-red) SKIP_TDD_RED=1 ;;
    *) RATIONALE_ARGS+=("$a") ;;
  esac
done
RATIONALE="${RATIONALE_ARGS[*]}"

if [ ${#RATIONALE} -lt 80 ]; then
  echo "ERROR: rationale must be >=80 chars (got ${#RATIONALE}). A bounce must enumerate the actual problems." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPANY_DIR="${COMPANY_DIR:-.company}"
export COMPANY_DIR

# 1. The rejection comment is written by the ticket-move call below (the
# move comment IS the canonical rejection comment - one comment per
# bounce, full rationale, no duplicate). A separate pre-move
# ticket-comment.sh used to write a second near-identical comment.

# 2. If --skip-tdd-red, set the flag on the ticket file BEFORE moving it,
# so sprint-lifecycle bounces it to tdd-red on the next loop.
if [ "$SKIP_TDD_RED" = 1 ]; then
  for st in in-review in-progress sprint-current; do
    f="$COMPANY_DIR/departments/$DEPT/tickets/$st/${TICKET_ID}.json"
    if [ -f "$f" ]; then
      tmp="${f}.tmp.$$"
      jq '.skip_tdd_red = true' "$f" > "$tmp" && mv "$tmp" "$f"
      break
    fi
  done
fi

# 2b. Increment bounce_count on the ticket. This is used by
# sprint-lifecycle.sh to enforce a per-ticket bounce limit
# (MAX_VERIFICATION_BOUNCES, default 5) so a single ticket cannot
# burn the entire sprint's iterations when verification repeatedly
# bounces it.  (photonic-proc ENGINEERING-0047 had 47 bounces.)
for st in in-review in-progress sprint-current; do
  f="$COMPANY_DIR/departments/$DEPT/tickets/$st/${TICKET_ID}.json"
  if [ -f "$f" ]; then
    tmp="${f}.tmp.$$"
    jq '.bounce_count = ((.bounce_count // 0) + 1)' \
      "$f" > "$tmp" && mv "$tmp" "$f"
    break
  fi
done

# 3. Move back to in-progress as a scripted move (in-review→in-progress
# requires HIVE_SCRIPTED_MOVE=1 or HIVE_ROLE=Verification-IC). The move
# comment carries the FULL rationale with the 'REJECTED by verification:'
# prefix that sprint-lifecycle.sh's rework/conflict prompts select on -
# do not truncate or reword it.
if ! HIVE_SCRIPTED_MOVE=1 HIVE_ROLE=Verification-IC \
     bash "$SCRIPT_DIR/ticket-move.sh" "$DEPT" "$TICKET_ID" in-progress \
       "REJECTED by verification: $RATIONALE" "$AUTHOR"; then
  echo "ERROR: ticket-move.sh failed; ticket may be in inconsistent state" >&2
  exit 1
fi

if [ "$SKIP_TDD_RED" = 1 ]; then
  echo "[ticket-bounce] $TICKET_ID rejected by $AUTHOR — comment added, status moved to in-progress, skip_tdd_red=true"
else
  echo "[ticket-bounce] $TICKET_ID rejected by $AUTHOR — comment added, status moved to in-progress"
fi
