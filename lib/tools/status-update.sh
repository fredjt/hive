#!/usr/bin/env bash
# DEPRECATED: status-update.sh is no longer the source of truth.
#
# Department status (.company/departments/<dept>/status.json) is now DERIVED
# automatically by status-rebuild.sh from:
#   - ticket directory counts (backlog, sprint-current, in-progress, in-review, done, archive)
#   - VP log activity (.company/logs/vp-<dept>.log mtime)
#   - velocity history (.company/departments/<dept>/status-history.jsonl)
#
# This wrapper exists only for backward compatibility. It accepts any args
# and triggers a rebuild instead of mutating status.json.
#
# Why deprecated: status used to drift from reality whenever a VP forgot
# to update it (or crashed mid-cycle). Now status is a view computed from
# ticket directories, so it cannot drift.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPT="${1:-}"

if [ -z "$DEPT" ] || [ "$DEPT" = "-h" ] || [ "$DEPT" = "--help" ]; then
  cat >&2 <<EOF
Usage: status-update.sh <dept> [args...]

NOTE: This script is DEPRECATED. Department status is now derived automatically
by status-rebuild.sh. Any args are accepted but ignored - a rebuild is triggered
instead.

Use these instead:
  - To force refresh:  bash status-rebuild.sh [--dept DEPT]
  - To read status:    cat .company/departments/<dept>/status.json
EOF
  exit 1
fi

echo "[status-update.sh] DEPRECATED: status.json is auto-derived." >&2
echo "[status-update.sh] Triggering status-rebuild for '$DEPT' instead..." >&2
bash "$SCRIPT_DIR/status-rebuild.sh" --quiet --dept "$DEPT"

# For backward compat with callers that piped output, emit the new status.
COMPANY_DIR="${COMPANY_DIR:-.company}"
STATUS_FILE="$COMPANY_DIR/departments/$DEPT/status.json"
[ -f "$STATUS_FILE" ] && cat "$STATUS_FILE"
exit 0
