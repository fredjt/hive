#!/usr/bin/env bash
# triage-test-failure.sh — Classify a test failure for the verification IC.
#
# Used during the verify stage (stage 5) to triage test failures into
# actionable categories. The verification IC uses this to decide whether
# to loop back to the engineer (real bug), retry the test (flaky), or
# skip it (intentionally disabled / known-bad).
#
# Usage:
#   triage-test-failure.sh "<test_name>" "<failure_output>"
#
# Output: one of flaky|real|setup|skip (on stdout)
# Exit codes:
#   0 = success (triage result on stdout)
#   1 = invalid usage
#   2 = tiny model unavailable (falls back to "real")
#
# Called by: verification-gates daemon, verification IC spawn hooks
# Depends on: llm-tiny.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ $# -lt 2 ]; then
  echo "Usage: triage-test-failure.sh '<test_name>' '<failure_output>'" >&2
  exit 1
fi

TEST_NAME="$1"
FAILURE_OUTPUT="$2"

# Use the tiny model to classify the failure
LLM_TINY_CALLER="triage-test-failure" \
  bash "$SCRIPT_DIR/llm-tiny.sh" --choice "flaky|real|setup|skip" \
  "You are a test failure classifier. Classify this test failure:
Test: $TEST_NAME

Failure output:
$FAILURE_OUTPUT

Is this:
- flaky: intermittent, would pass on retry, race condition, timing-dependent
- real: consistent failure indicating a bug in the code or test logic
- setup: environment or test infrastructure issue (missing dependency, wrong config, fixture problem)
- skip: intentionally disabled test, known-bad test that should be removed

Output exactly one of: flaky, real, setup, skip" || {
  # Tiny model unavailable — default to "real" (conservative: treat as real bug)
  echo "[triage-test-failure] tiny model unavailable, defaulting to 'real'" >&2
  echo "real"
  exit 2
}
