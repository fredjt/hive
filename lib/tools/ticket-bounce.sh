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
# G1/G2: flag-set + bounce_count increment + the move is ONE state-layer op
# (lib/hive_state, op ticket-bounce) — the same accessors the MCP
# ticket_bounce tool and the lifecycle share.
#
# Usage: ticket-bounce.sh <dept> <ticket-id> <author> <rationale> [--skip-tdd-red]
#
# Rationale must be >=80 characters. Should be a numbered list of every issue
# found, with file:line references where applicable.
#
# --skip-tdd-red sets `skip_tdd_red: true` on the ticket so the next loop
# iteration skips stage 3 (tdd-red) and goes straight to stage 4 (tdd-green).
# Use ONLY when the existing test is sound and the implementation is wrong;
# i.e. adding more tests wouldn't help. Default (no flag) re-runs RED.

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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPANY_DIR="${COMPANY_DIR:-.company}"
export COMPANY_DIR
source "$SCRIPT_DIR/hive-state.sh"

BOUNCE_ARGS=(ticket-bounce --dept "$DEPT" --ticket "$TICKET_ID" \
  --author "$AUTHOR" --rationale "$RATIONALE")
[ "$SKIP_TDD_RED" = 1 ] && BOUNCE_ARGS+=(--skip-tdd-red)
RESULT=$(hs "${BOUNCE_ARGS[@]}" 2>&1)
RC=$?
if [ $RC -ne 0 ]; then
  printf '%s\n' "$RESULT" >&2
  exit $RC
fi

if [ "$SKIP_TDD_RED" = 1 ]; then
  echo "[ticket-bounce] $TICKET_ID rejected by $AUTHOR — comment added, status moved to in-progress, skip_tdd_red=true"
else
  echo "[ticket-bounce] $TICKET_ID rejected by $AUTHOR — comment added, status moved to in-progress"
fi
