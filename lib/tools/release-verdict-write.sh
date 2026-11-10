#!/usr/bin/env bash
set -uo pipefail

# release-verdict-write.sh — write the release-ic verdict file.
#
# sprint-lifecycle parses the first `VERDICT:` line and routes the sprint:
#   GO    -> release-publish (stage 8)
#   NO-GO -> ceo-bugfix-plan (Loop B)
#
# This script exists because release-ic prompts told the agent to
# `cat > "$VERIFY_LOG" <<EOF ... EOF`. VERIFY_LOG lives under
# .company/releases/. This tool wraps the write operation.
#
# Usage:
#   release-verdict-write.sh <GO|NO-GO> [--rationale "..."] [--blockers-stdin]
#                            [--rationale-stdin]
#
# Examples:
#   # GO with inline rationale
#   release-verdict-write.sh GO --rationale "All 12 PRs verified clean; ran 3 random tests, all green; tarball install OK."
#
#   # NO-GO with rationale + bulleted blockers from stdin
#   release-verdict-write.sh NO-GO \
#     --rationale "Tarball install crashes on missing libfoo dependency." \
#     --blockers-stdin <<'EOF'
#   - packaging/install.sh:42 — libfoo install missing from apt-get list
#   - tests/integration/test_install.py — skipped, not exercised in CI
#   EOF
#
#   # All-stdin form (full body, pre-formatted)
#   release-verdict-write.sh GO --rationale-stdin <<'EOF'
#   Full rationale paragraph here.
#   Multi-line OK.
#   EOF
#
# The script reads `release.verify_log` from sprint state and writes there.

VERDICT="${1:-}"
shift 2>/dev/null || true

case "$VERDICT" in
  GO|NO-GO) ;;
  *)
    echo "Usage: release-verdict-write.sh <GO|NO-GO> [--rationale TEXT|--rationale-stdin] [--blockers-stdin]" >&2
    exit 1
    ;;
esac

RATIONALE=""
RATIONALE_STDIN=0
BLOCKERS_STDIN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --rationale)        RATIONALE="$2"; shift 2 ;;
    --rationale-stdin)  RATIONALE_STDIN=1; shift ;;
    --blockers-stdin)   BLOCKERS_STDIN=1; shift ;;
    *) echo "release-verdict-write: unknown flag: $1" >&2; exit 1 ;;
  esac
done

if [ "$RATIONALE_STDIN" = 1 ] && [ "$BLOCKERS_STDIN" = 1 ]; then
  echo "release-verdict-write: --rationale-stdin and --blockers-stdin are mutually exclusive (only one can read stdin)" >&2
  exit 1
fi

SKILL_DIR="${HIVE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
. "$SKILL_DIR/lib/tools/sprint-state.sh"

VERIFY_LOG=$(ss_get_field .release.verify_log)
if [ -z "$VERIFY_LOG" ] || [ "$VERIFY_LOG" = "null" ]; then
  echo "release-verdict-write: .release.verify_log is not set in sprint state. Release-IC must be running under sprint-lifecycle." >&2
  exit 1
fi

mkdir -p "$(dirname "$VERIFY_LOG")"

# Read stdin if requested
STDIN_BUF=""
if [ "$RATIONALE_STDIN" = 1 ] || [ "$BLOCKERS_STDIN" = 1 ]; then
  STDIN_BUF=$(cat)
fi

[ "$RATIONALE_STDIN" = 1 ] && RATIONALE="$STDIN_BUF"

if [ -z "$RATIONALE" ]; then
  echo "release-verdict-write: rationale required (--rationale TEXT or --rationale-stdin)" >&2
  exit 1
fi

# Build the file. Format MUST match what sprint-lifecycle parses:
#   first line: "VERDICT: GO" or "VERDICT: NO-GO"
#   subsequent lines: free-form (RATIONALE / BLOCKERS sections recommended).
{
  echo "VERDICT: $VERDICT"
  echo "RATIONALE:"
  echo "$RATIONALE"
  if [ "$BLOCKERS_STDIN" = 1 ] && [ -n "$STDIN_BUF" ]; then
    echo "BLOCKERS:"
    echo "$STDIN_BUF"
  fi
# T3 (0.15): tmp + rename — a single commit point. sprint-lifecycle
# (a different process) parses the first VERDICT: line; a truncate-then-fill
# write would expose a partial/empty verdict mid-write.
} > "$VERIFY_LOG.tmp.$$"
mv -f "$VERIFY_LOG.tmp.$$" "$VERIFY_LOG"

echo "release-verdict-write: $VERDICT recorded at $VERIFY_LOG"
realpath "$VERIFY_LOG"
exit 0
