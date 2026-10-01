#!/usr/bin/env bash
# triage-test-failure.sh — Classify a test failure for the verification IC.
#
# Used during the verify stage (stage 5) to triage test failures into
# actionable categories using local keyword heuristics. No external model needed.
#
# Usage:
#   triage-test-failure.sh "<test_name>" "<failure_output>"
#
# Output: one of flaky|real|setup|skip (on stdout)
# Exit codes:
#   0 = success (triage result on stdout)
#   1 = invalid usage
#
# Called by: verification-gates daemon, verification IC spawn hooks

set -uo pipefail

if [ $# -lt 2 ]; then
  echo "Usage: triage-test-failure.sh '<test_name>' '<failure_output>'" >&2
  exit 1
fi

TEST_NAME="$1"
FAILURE_OUTPUT="$2"

# Classify via keyword heuristics (conservative defaults)
classify() {
  local output_lower
  output_lower=$(echo "$FAILURE_OUTPUT" | tr '[:upper:]' '[:lower:]')

  # Skip: intentionally disabled, known-bad, xfail, skipif
  if echo "$output_lower" | grep -qiE '\b(xfail|skip(if)?|disabled|not.?implemented|todo|wontfix|known.?bad)\b'; then
    echo "skip"
    return
  fi

  # Setup: missing dependency, wrong config, fixture, environment
  if echo "$output_lower" | grep -qiE '\b(no such file|cannot import|module not found|package not found|dependency.*missing|config.*(error|not found|invalid)|fixture.*(not found|error|missing)|environment.*(error|not found)|permission denied|access denied|connection refused|address already in use)\b'; then
    echo "setup"
    return
  fi

  # Flaky: timing, race condition, intermittent, timeout
  if echo "$output_lower" | grep -qiE '\b(timeout|timed out|deadline exceeded|race.?condition|intermittent|flaky|sporadic|retry|transient|eventual consistency)\b'; then
    echo "flaky"
    return
  fi

  # Default to real (conservative: treat as real bug)
  echo "real"
}

classify
