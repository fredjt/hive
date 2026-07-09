#!/usr/bin/env bash
set -uo pipefail

# ticket-reject.sh — atomically close an in-review ticket as won't-do (done
# with reason=rejected), writing a mandatory substantial rationale comment
# onto the ticket itself.
#
# This is the verification IC's RAPID-CLOSE path: distinct from `ticket-bounce`
# (which sends back to in-progress for another loop) and `ticket-approve`
# (which ships to done as implemented). Use it when more loop iterations
# cannot fix the ticket — wrong premise, wrong department, duplicates an
# already-done ticket, depends on unavailable infrastructure, etc. Without
# this path, the verifier would have to bounce repeatedly until the loop
# cap deferred it to backlog, wasting a full sprint of compute and engineer
# turns before any human or VP saw it.
#
# Authority: HIVE_ROLE=Verification-IC OR HIVE_ROLE=VP (during a VP stage)
# OR HIVE_SCRIPTED_MOVE=1. Enforcement lives in ticket-move.sh's done:rejected
# case.
#
# Usage: ticket-reject.sh <dept> <ticket-id> <author> <rationale>
#
# Rationale must be >=80 characters. Should explain WHY this ticket cannot
# be fixed by more loops: what's structurally wrong, what the alternate path
# is (e.g., refile in <other-dept>, defer until <dependency> exists, etc.).

if [ $# -lt 4 ]; then
  echo "Usage: ticket-reject.sh <dept> <ticket-id> <author> <rationale>" >&2
  echo "  rationale: explain WHY more loops cannot fix this (>=80 chars). Name the alternate path if any." >&2
  exit 1
fi

DEPT="$1"
TICKET_ID="$2"
AUTHOR="$3"
shift 3
RATIONALE="$*"

if [ ${#RATIONALE} -lt 80 ]; then
  echo "ERROR: rationale must be >=80 chars (got ${#RATIONALE}). A rejection must explain why this is structural, not iterable." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPANY_DIR="${COMPANY_DIR:-.company}"

# Move to done with reason=rejected. ticket-move.sh enforces the role gate
# (Verification-IC, VP-during-stage, or scripted system) AND records the
# rationale as a "REJECTED: ..." comment automatically — no separate
# ticket-comment.sh call needed.
bash "$SCRIPT_DIR/ticket-move.sh" "$DEPT" "$TICKET_ID" done \
  "$RATIONALE" "$AUTHOR" rejected
