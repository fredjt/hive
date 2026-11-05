#!/usr/bin/env bash
set -uo pipefail

# release-test-suite.sh — stage 6 of the sprint cycle. Synchronous; no spawn.
# Detects + runs the project's full test suite at $PROJECT_ROOT, captures
# output to .company/releases/<version>-test.log, updates sprint state.
#
# Detection logic mirrors verification-gates.sh (so per-ticket and full-build
# test runs are consistent).
#
# Exit codes:
#   0  test suite passed (or no test framework detected — treated as PASS
#      with a warning, since the absence of tests can't fail what doesn't
#      exist; the release-ic stage will catch this in adversarial review)
#   1  test suite failed (actual test failures)
#   2  bad usage / missing project root
#   3  resource-killed (timed out or OOM'd by memory limit — logged to
#      test log with details; sprint-lifecycle treats same as exit 1 for
#      retry cap purposes but the log clearly signals a resource issue)

PROJECT_ROOT="${1:-${PROJECT_ROOT:-$PWD}}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd)" || { echo "release-test-suite: bad project root" >&2; exit 2; }
COMPANY_DIR="${COMPANY_DIR:-$PROJECT_ROOT/.company}"
SKILL_DIR="${SKILL_DIR:-${HIVE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}}"
export COMPANY_DIR PROJECT_ROOT SKILL_DIR

# shellcheck disable=SC1091
. "$SKILL_DIR/lib/tools/sprint-state.sh"

VERSION=$(ss_get_field .release.version)
[ -z "$VERSION" ] && VERSION="unversioned-$(date -u +%Y%m%dT%H%M%SZ)"

REL_DIR="$COMPANY_DIR/releases"
mkdir -p "$REL_DIR"
LOG="$REL_DIR/${VERSION}-test.log"
ss_set_release_field test_log "$LOG"

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# ── Resource guard ──────────────────────────────────────────────────
# Wraps a command with memory and timeout limits to prevent OOM kills
# and runaway builds from blocking the sprint cycle.
#
# Configurable via environment:
#   BUILD_TIMEOUT_MIN  — max wall-clock time in minutes (default: 30)
#   BUILD_MEM_GB       — max virtual memory in GB (default: 4)
#
# Exit codes:
#   0  command succeeded
#   1  command failed (non-zero exit, not resource-killed)
#   3  resource-killed (timed out or OOM'd by ulimit)

_run_with_resource_guard() {
  local cmd="$1" log_file="$2"
  local timeout_sec=$(( ${BUILD_TIMEOUT_MIN:-30} * 60 ))
  local mem_kb=$(( ${BUILD_MEM_GB:-4} * 1024 * 1024 ))

  # Save and restore ulimit so this function is a clean wrapper.
  local saved_vmem
  saved_vmem=$(ulimit -v 2>/dev/null || echo "")

  # Set virtual memory limit (KB). Silently skip if ulimit -v is unavailable.
  if [ -n "$saved_vmem" ]; then
    ulimit -v "$mem_kb" 2>/dev/null || true
  fi

  # Run with timeout; capture both stdout and stderr to the log file.
  # timeout exit code: 124 = timed out, 137 = killed by SIGKILL (ulimit OOM).
  timeout -s TERM "$timeout_sec" bash -c "$cmd" >> "$log_file" 2>&1
  local rc=$?

  # Restore original ulimit.
  if [ -n "$saved_vmem" ]; then
    ulimit -v "$saved_vmem" 2>/dev/null || true
  fi

  if [ "$rc" -eq 124 ]; then
    echo "[$(ts)] TIMEOUT after ${timeout_sec}s ($(($timeout_sec / 60)) min)" >> "$log_file"
    return 3
  elif [ "$rc" -eq 137 ]; then
    echo "[$(ts)] OOM killed by ulimit (mem limit ${mem_kb} KB = $(($mem_kb / 1024 / 1024)) GB)" >> "$log_file"
    return 3
  fi
  return "$rc"
}

# B2: the version header must not lie — a leading v on the
# unversioned-<ts> fallback made the run's logs read "vunversioned-…". Prefix v
# only when the value looks like semver (leading digit); the unversioned
# fallback prints as-is.
VERSION_LABEL="$VERSION"
if [[ "$VERSION" =~ ^[0-9] ]]; then
  VERSION_LABEL="v$VERSION"
fi
{
  echo "[$(ts)] release-test-suite for $VERSION_LABEL (project_root=$PROJECT_ROOT)"
} > "$LOG"

# Detect + run. Same detection table as verification-gates.sh detect_test_cmd,
# plus CMake/ctest for C/C++ projects.
#
# The command string is executed via `bash -c` (inside the resource guard),
# so the project root is shell-quoted with printf %q — an unquoted path
# containing a space broke command parsing and failed the build for no reason.
QROOT=$(printf '%q' "$PROJECT_ROOT")
TEST_CMD=""
if   [ -f "$PROJECT_ROOT/package.json" ] && jq -e '.scripts.test' "$PROJECT_ROOT/package.json" >/dev/null 2>&1; then
  TEST_CMD="cd $QROOT && npm test --silent"
elif [ -f "$PROJECT_ROOT/pyproject.toml" ] || [ -f "$PROJECT_ROOT/pytest.ini" ] || [ -f "$PROJECT_ROOT/setup.cfg" ]; then
  TEST_CMD="cd $QROOT && pytest -q --tb=line"
elif [ -f "$PROJECT_ROOT/Cargo.toml" ]; then
  TEST_CMD="cd $QROOT && cargo test --quiet"
elif [ -f "$PROJECT_ROOT/go.mod" ]; then
  TEST_CMD="cd $QROOT && go test ./..."
elif [ -f "$PROJECT_ROOT/CMakeLists.txt" ]; then
  TEST_CMD="cd $QROOT && ctest --output-on-failure"
fi

if [ -z "$TEST_CMD" ]; then
  echo "[$(ts)] no test framework detected — skipping test run (release-ic will flag this)" | tee -a "$LOG" >&2
else
  echo "[$(ts)] running: $TEST_CMD" >> "$LOG"
  TEST_RC=0
  _run_with_resource_guard "$TEST_CMD" "$LOG" || TEST_RC=$?
  if [ "$TEST_RC" -eq 3 ]; then
    # Resource-killed (timeout or memory limit). Not a code defect — the
    # build/test was too heavy for this environment. Increment retry but
    # do NOT loop back to bugfix (there's nothing for the LLM to fix).
    echo "[$(ts)] test suite RESOURCE-KILLED (timeout=$BUILD_TIMEOUT_MIN min, mem=$BUILD_MEM_GB GB)" >> "$LOG"
    echo "release-test-suite: RESOURCE-KILLED (log: $LOG)" >&2
    exit 3
  elif [ "$TEST_RC" -ne 0 ]; then
    echo "[$(ts)] test suite FAILED (exit=$TEST_RC)" >> "$LOG"
    echo "release-test-suite: FAIL exit=$TEST_RC (log: $LOG)" >&2
    exit 1
  fi
  echo "[$(ts)] test suite PASSED" >> "$LOG"
fi

# Tests passed — populate sprint state with release artifact metadata so
# release-publish-runner has version/tarball/sha256 to ship. Without these,
# release-publish-runner exits 2 ("sprint state has no release.version") and
# the release is silently skipped despite an adversarial GO verdict. Detection
# is best-effort: if artifacts can't be found, leave the fields null and let
# the release stage surface a clearer error.

# 1. VERSION discovery — first hit wins.
DETECTED_VERSION=""
if [ -f "$PROJECT_ROOT/VERSION" ]; then
  DETECTED_VERSION=$(head -1 "$PROJECT_ROOT/VERSION" | tr -d '[:space:]')
elif [ -f "$PROJECT_ROOT/pyproject.toml" ]; then
  DETECTED_VERSION=$(awk -F'"' '/^[[:space:]]*version[[:space:]]*=/{print $2; exit}' "$PROJECT_ROOT/pyproject.toml")
elif [ -f "$PROJECT_ROOT/package.json" ]; then
  DETECTED_VERSION=$(jq -r '.version // empty' "$PROJECT_ROOT/package.json" 2>/dev/null)
elif [ -f "$PROJECT_ROOT/Cargo.toml" ]; then
  DETECTED_VERSION=$(awk -F'"' '/^[[:space:]]*version[[:space:]]*=/{print $2; exit}' "$PROJECT_ROOT/Cargo.toml")
elif [ -f "$PROJECT_ROOT/CMakeLists.txt" ]; then
  DETECTED_VERSION=$(awk 'match($0, /project\(.*VERSION[[:space:]]+([0-9][^[:space:])]*)/, m){print m[1]; exit}' "$PROJECT_ROOT/CMakeLists.txt")
fi

# Honor sprint state's pre-existing version if it was set by ceo-plan or
# manually; otherwise install what we detected.
CURRENT_VERSION=$(ss_get_field .release.version)
if [ -z "$CURRENT_VERSION" ] && [ -n "$DETECTED_VERSION" ]; then
  ss_set_release_field version "$DETECTED_VERSION"
  echo "[$(ts)] detected version: $DETECTED_VERSION" >> "$LOG"
  CURRENT_VERSION="$DETECTED_VERSION"
fi

# 2. TARBALL discovery — only if a version is known.
DETECTED_TARBALL=""
if [ -n "$CURRENT_VERSION" ]; then
  # Search common build output dirs for a tarball matching the version.
  # Prefer the most-recently-modified hit.
  #
  # Null-delimited end to end (find -print0 + per-candidate stat). The old
  # `find ... | xargs -r ls -t` word-split paths on whitespace: for a
  # project under e.g. "/home/u/My Projects/app" the tarball argument
  # became the bare directory "/home/u/My", and ls listing a directory
  # printed its "dir:" header line — which the loop below then stored as
  # .release.tarball, and release-publish-runner rejected ("tarball not
  # found"). Every release from a space-path project failed publish.
  BEST_TARBALL_MTIME=0
  while IFS= read -r -d '' cand; do
    [ -z "$cand" ] && continue
    cand_mtime=$(stat -c %Y "$cand" 2>/dev/null || echo 0)
    if [ "$cand_mtime" -gt "$BEST_TARBALL_MTIME" ]; then
      BEST_TARBALL_MTIME=$cand_mtime
      DETECTED_TARBALL="$cand"
    fi
  done < <(
    find "$PROJECT_ROOT/dist" "$PROJECT_ROOT/build" "$PROJECT_ROOT/target/release" \
         -maxdepth 3 -type f \
         \( -name "*${CURRENT_VERSION}*.tar.gz" -o -name "*${CURRENT_VERSION}*.tgz" \) \
         -print0 2>/dev/null
  )
fi

CURRENT_TARBALL=$(ss_get_field .release.tarball)
if [ -z "$CURRENT_TARBALL" ] && [ -n "$DETECTED_TARBALL" ]; then
  ss_set_release_field tarball "$DETECTED_TARBALL"
  echo "[$(ts)] detected tarball: $DETECTED_TARBALL" >> "$LOG"
  CURRENT_TARBALL="$DETECTED_TARBALL"
fi

# 3. SHA256 sidecar — prefer existing sidecar, else compute one in place.
CURRENT_SHA256=$(ss_get_field .release.sha256)
if [ -z "$CURRENT_SHA256" ] && [ -n "$CURRENT_TARBALL" ] && [ -f "$CURRENT_TARBALL" ]; then
  SIDECAR="${CURRENT_TARBALL%.tar.gz}.sha256"
  [ "$SIDECAR" = "$CURRENT_TARBALL" ] && SIDECAR="${CURRENT_TARBALL%.tgz}.sha256"
  if [ ! -f "$SIDECAR" ]; then
    (cd "$(dirname "$CURRENT_TARBALL")" && sha256sum "$(basename "$CURRENT_TARBALL")" > "$SIDECAR")
    echo "[$(ts)] computed sha256 sidecar at $SIDECAR" >> "$LOG"
  fi
  ss_set_release_field sha256 "$SIDECAR"
fi

# If version was just detected, the test_log path we wrote earlier embeds
# the "unversioned-TIMESTAMP" stub. Rename it to <version>-test.log so the
# release-IC and downstream history reference a stable, version-tagged path.
if [ -n "$CURRENT_VERSION" ] && [[ "$LOG" == */unversioned-*-test.log ]]; then
  NEW_LOG="$REL_DIR/${CURRENT_VERSION}-test.log"
  if [ ! -e "$NEW_LOG" ]; then
    mv "$LOG" "$NEW_LOG"
    ss_set_release_field test_log "$NEW_LOG"
    echo "[$(ts)] test_log renamed to $NEW_LOG"
    LOG="$NEW_LOG"
  fi
fi

echo "release-test-suite: PASS (log: $LOG)"
exit 0
