#!/usr/bin/env bash
set -euo pipefail

# verification-gates.sh — Run deterministic quality gates against a single
# in-review ticket. Called synchronously by sprint-lifecycle.sh after the
# engineer exits and moves the ticket to in-review.
#
# THIS IS A GATE, NOT A SHORTCUT. Tickets that fail any gate are bounced
# back to in-progress with a comment explaining what failed.
#
# Gates (all blocking, all must pass):
#   1. Worktree exists for the ticket
#   2. Branch has commits beyond main (work was actually done)
#   3. TDD order — first commit mentioning this ticket touches a test file
#   4. Tests EXIST for changed files (no untested code)
#   5. All tests pass (no failures, no errors)
#   6. Linter passes
#   7. Security audit clean
#
# Usage: verification-gates.sh <dept> <project-root> [--ticket <id>] [--quiet]
#   --ticket <id>  Gate this specific in-review ticket (the lifecycle passes
#                  its own ticket). Without it, the first ticket in the
#                  dept's in-review dir is used (the 90s daemon).
#   --quiet        Accepted for compatibility with the daemon; output is
#                  captured by the caller either way.
#
# Return codes:
#   0 — all gates passed
#   1 — usage error (bad arguments)
#   2 — one or more gates failed (ticket bounced to in-progress)
#
# Configuration via env:
#   GATE_COVERAGE_MIN         - minimum coverage % for new code (default: 80)
#   GATE_REQUIRE_TESTS        - bounce tickets without test files (default: true)
#   GATE_REQUIRE_LINT         - bounce on lint failure (default: true)
#   GATE_REQUIRE_SECURITY     - bounce on security audit failure (default: true)
#   GATE_REQUIRE_COMMITS      - bounce if branch has no commits beyond main (default: true)

if [ $# -lt 2 ]; then
  echo "Usage: verification-gates.sh <dept> <project-root> [--ticket <id>] [--quiet]" >&2
  exit 1
fi

DEPT="$1"
PROJECT_ROOT="$(cd "$2" && pwd)"
GATE_TICKET=""
shift 2
for arg in "$@"; do
  case "$arg" in
    --quiet) : ;;
    --ticket) : ;;           # its value arrives as the next bare arg
    --ticket=*) GATE_TICKET="${arg#--ticket=}" ;;
    -*) : ;;                 # unknown flag: ignore
    *) [ -z "$GATE_TICKET" ] && GATE_TICKET="$arg" ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPANY_DIR="$PROJECT_ROOT/.company"
export COMPANY_DIR

# Per-project gate configuration
GATES_CONFIG="$COMPANY_DIR/gates.json"
if [ -f "$GATES_CONFIG" ]; then
  GATE_REQUIRE_TESTS=$(jq -r '.require_tests   // true'  "$GATES_CONFIG" 2>/dev/null)
  GATE_REQUIRE_LINT=$(jq  -r '.require_lint    // true'  "$GATES_CONFIG" 2>/dev/null)
  GATE_REQUIRE_SECURITY=$(jq -r '.require_security // true' "$GATES_CONFIG" 2>/dev/null)
  GATE_REQUIRE_COMMITS=$(jq  -r '.require_commits  // true'  "$GATES_CONFIG" 2>/dev/null)
  GATE_COVERAGE_MIN=$(jq    -r '.coverage_min // 0'         "$GATES_CONFIG" 2>/dev/null)
else
  GATE_REQUIRE_TESTS="${GATE_REQUIRE_TESTS:-true}"
  GATE_REQUIRE_LINT="${GATE_REQUIRE_LINT:-true}"
  GATE_REQUIRE_SECURITY="${GATE_REQUIRE_SECURITY:-true}"
  GATE_REQUIRE_COMMITS="${GATE_REQUIRE_COMMITS:-true}"
  GATE_COVERAGE_MIN="${GATE_COVERAGE_MIN:-0}"
fi
GATE_REQUIRE_TESTS="${GATE_REQUIRE_TESTS_OVERRIDE:-$GATE_REQUIRE_TESTS}"
GATE_REQUIRE_LINT="${GATE_REQUIRE_LINT_OVERRIDE:-$GATE_REQUIRE_LINT}"
GATE_REQUIRE_SECURITY="${GATE_REQUIRE_SECURITY_OVERRIDE:-$GATE_REQUIRE_SECURITY}"
GATE_REQUIRE_COMMITS="${GATE_REQUIRE_COMMITS_OVERRIDE:-$GATE_REQUIRE_COMMITS}"

source "$SCRIPT_DIR/ticket-paths.sh"
TICKET_DIR="$(ticket_dir "$DEPT")"

# ── Per-ticket gate output log ──────────────────────────────────────
# When GATE_LOG is set (by sprint-lifecycle.sh), redirect all output
# (stdout + stderr) to a per-ticket file so the dashboard can show
# gate run stdout/stderr via the "View" button.
if [ -n "${GATE_LOG:-}" ]; then
  mkdir -p "$(dirname "$GATE_LOG")"
  : > "$GATE_LOG"        # truncate: each gate run gets a fresh log
  exec 1>>"$GATE_LOG"
  exec 2>&1
fi

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] gates dept=$DEPT $*" >&2; }

log "starting gate run (strict=$GATE_REQUIRE_TESTS,$GATE_REQUIRE_LINT,$GATE_REQUIRE_SECURITY,$GATE_REQUIRE_COMMITS)"

# Detect project test framework.
#
# The returned string is executed via `bash -c`, so the worktree path is
# shell-quoted with printf %q — an unquoted $wt broke command parsing for
# any project path containing a space (cd failed → every ticket bounced
# on the tests-pass gate even though the tests themselves pass).
detect_test_cmd() {
  local wt="$1" qwt
  qwt=$(printf '%q' "$wt")
  if [ -f "$wt/package.json" ] && jq -e '.scripts.test' "$wt/package.json" >/dev/null 2>&1; then
    echo "npm test --silent --prefix $qwt -- --reporter=min"
  elif [ -f "$wt/pyproject.toml" ] || [ -f "$wt/pytest.ini" ] || [ -f "$wt/setup.cfg" ]; then
    echo "cd $qwt && pytest -q --tb=line"
  elif [ -f "$wt/Cargo.toml" ]; then
    echo "cd $qwt && cargo test --quiet"
  elif [ -f "$wt/go.mod" ]; then
    echo "cd $qwt && go test ./..."
  else
    echo ""
  fi
}

detect_lint_cmd() {
  local wt="$1" qwt
  qwt=$(printf '%q' "$wt")
  if [ -f "$wt/package.json" ] && jq -e '.scripts.lint' "$wt/package.json" >/dev/null 2>&1; then
    echo "npm run lint --silent --prefix $qwt"
  elif [ -f "$wt/pyproject.toml" ] && command -v ruff >/dev/null; then
    echo "cd $qwt && ruff check ."
  elif [ -f "$wt/Cargo.toml" ]; then
    echo "cd $qwt && cargo clippy --quiet -- -D warnings"
  elif [ -f "$wt/go.mod" ]; then
    echo "cd $qwt && go vet ./..."
  else
    echo ""
  fi
}

# Record a gate result for a ticket.
record_gate() {
  local ticket_id="$1" gate="$2" status="$3" detail="$4"
  local gates_file="$COMPANY_DIR/departments/$DEPT/components/$ticket_id/gates.json"
  mkdir -p "$(dirname "$gates_file")"
  if [ ! -f "$gates_file" ]; then
    echo '{"ticket_id":"'"$ticket_id"'","gates":{}}' > "$gates_file"
  fi
  jq --arg gate "$gate" --arg status "$status" --arg detail "$detail" --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '.gates[$gate] = {status: $status, detail: $detail, checked_at: $ts}' \
    "$gates_file" > "$gates_file.tmp" && mv "$gates_file.tmp" "$gates_file"
}

# Bounce a ticket back to in-progress with a comment explaining why.
# This is called synchronously from sprint-lifecycle.sh, so the ticket is
# already in in-review (meaning it has work on its branch).
bounce_ticket() {
  local ticket_id="$1" reason="$2"
  log "  BOUNCE $ticket_id: $reason"
  # One comment per bounce: the move comment IS the rejection comment.
  # (A separate pre-move ticket-comment.sh used to write a second
  # near-identical comment on every gate bounce.)
  HIVE_SCRIPTED_MOVE=1 HIVE_ROLE=Verification-IC \
    bash "$SCRIPT_DIR/ticket-move.sh" "$DEPT" "$ticket_id" in-progress \
      "Gate failure: $reason" "verification-gates" 2>/dev/null || true
}

# Mark a ticket as gates-passed (does NOT move it to done - IC still reviews)
mark_gates_passed() {
  local ticket_id="$1"
  log "  PASS $ticket_id: all gates passed - awaiting IC review"
  bash "$SCRIPT_DIR/ticket-comment.sh" "$DEPT" "$ticket_id" "verification-gates" \
    "All deterministic gates passed. Awaiting verification IC for logic/design review." 2>/dev/null || true
  local ticket_file
  ticket_file=$(find "$TICKET_DIR/in-review" -name "$ticket_id.json" -type f 2>/dev/null | head -1)
  if [ -n "$ticket_file" ]; then
    jq --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      '.gates_passed_at = $ts | .gates_passed = true' \
      "$ticket_file" > "$ticket_file.tmp" && mv "$ticket_file.tmp" "$ticket_file"
  fi
}

# ── Find the ticket file ────────────────────────────────────────────
# The caller (sprint-lifecycle.sh) moves the ticket to in-review before
# calling this script and names it explicitly (--ticket). The 90s daemon
# calls without a name and gets the first ticket in the dept's in-review
# dir. A named ticket that is not in in-review is an error (exit 2 — the
# caller's move raced or failed); an unnamed run with nothing in
# in-review is the normal idle state (exit 0, not an error).
if [ -n "$GATE_TICKET" ]; then
  TICKET_FILE="$TICKET_DIR/in-review/$GATE_TICKET.json"
  [ -f "$TICKET_FILE" ] || TICKET_FILE=""
else
  TICKET_FILE=$(find "$TICKET_DIR/in-review" -name '*.json' -type f 2>/dev/null | sort | head -1)
fi
if [ -z "$TICKET_FILE" ] || [ ! -f "$TICKET_FILE" ]; then
  if [ -n "$GATE_TICKET" ]; then
    log "ticket $GATE_TICKET not found in in-review for dept=$DEPT"
    exit 2
  fi
  log "no ticket in in-review for dept=$DEPT — nothing to gate"
  exit 0
fi

TID=$(jq -r '.id // empty' "$TICKET_FILE")
[ -z "$TID" ] && { log "no ticket id in file"; exit 2; }

# Already gates_passed and the caller did not force a re-run: nothing to
# do. Without this, the 90s daemon re-ran the full test suite AND appended
# a fresh 'All deterministic gates passed' comment on an already-passed
# ticket every single cycle. (The lifecycle resets gates_passed=false
# before its synchronous run, so it always gets a fresh evaluation.)
ALREADY_PASSED=$(jq -r '.gates_passed // false' "$TICKET_FILE" 2>/dev/null)
if [ "$ALREADY_PASSED" = "true" ]; then
  log "  $TID already gates_passed — skipping re-run"
  exit 0
fi

WORKTREE="$PROJECT_ROOT/.worktrees/$TID"
BRANCH="feature/$TID"
GATE_FAIL=""

# ── Pre-gate: classify code vs. non-code ticket ─────────────────────
# Admin departments NEVER produce code; their tickets are documents/configs.
# Auto-pass: the verification IC will review the deliverable content.
if [ -f "$SCRIPT_DIR/dept-helpers.sh" ]; then
  # shellcheck disable=SC1091
  . "$SCRIPT_DIR/dept-helpers.sh"
  if is_admin_dept "$DEPT"; then
    record_gate "$TID" "admin-dept-ticket" "pass" "admin dept ($DEPT) — no scripted gates apply"
    mark_gates_passed "$TID"
    exit 0
  fi
fi

# Engineering ticket without worktree/branch is also non-code
IS_CODE=false
if [ -d "$WORKTREE" ]; then
  IS_CODE=true
elif git -C "$PROJECT_ROOT" rev-parse --verify "$BRANCH" >/dev/null 2>&1; then
  IS_CODE=true
fi

if [ "$IS_CODE" = false ]; then
  record_gate "$TID" "non-code-ticket" "pass" "no worktree or feature branch — code gates skipped"
  mark_gates_passed "$TID"
  exit 0
fi

# NOTE: skip_tdd_red=true is deliberately NOT a gate. The flag means
# "skip the tester; the engineer works the ticket directly" (set by
# ticket-bounce.sh --skip-tdd-red, the PR-merge-conflict rollback, and
# the 3-strike tester fallback). ticket-move clears it when the ticket
# reaches done, so re-promoted tickets start a fresh TDD cycle. Bouncing
# every flagged ticket here made them permanent zombies:
# engineer re-spawns, ticket bounces, repeat, until the loop safety
# valve defers it, for every sprint, forever. The ticket is in in-review
# because the engineer just exited 0, so the real gates below evaluate
# its actual work; a branch with no test commit fails Gate 3a (TDD
# order) for the right reason.

# ── Gate 1: Worktree exists ─────────────────────────────────────────
if [ ! -d "$WORKTREE" ]; then
  record_gate "$TID" "worktree-exists" "fail" "no worktree at $WORKTREE"
  GATE_FAIL="no worktree found"
else
  record_gate "$TID" "worktree-exists" "pass" "$WORKTREE"
fi

# ── Gate 2: Branch has commits beyond main ──────────────────────────
if [ -z "$GATE_FAIL" ] && [ "$GATE_REQUIRE_COMMITS" = "true" ]; then
  COMMIT_COUNT=$(git -C "$PROJECT_ROOT" rev-list --count "main..$BRANCH" 2>/dev/null || echo "0")
  if [ "$COMMIT_COUNT" -eq 0 ]; then
    record_gate "$TID" "has-commits" "fail" "0 commits beyond main"
    GATE_FAIL="branch has no commits beyond main"
  else
    record_gate "$TID" "has-commits" "pass" "$COMMIT_COUNT commits"
  fi
fi

# ── Gate 3a: TDD order ─────────────────────────────────────────────
# The first commit on this branch THAT MENTIONS THIS TICKET must touch a
# test file. Filtering by ticket id prevents misfires from foreign commits
# on branch ancestry.
if [ -z "$GATE_FAIL" ] && [ "$GATE_REQUIRE_TESTS" = "true" ]; then
  BASE=$(git -C "$PROJECT_ROOT" merge-base main "$BRANCH" 2>/dev/null || echo "")
  if [ -n "$BASE" ]; then
    FIRST_COMMIT=$(git -C "$PROJECT_ROOT" log --reverse --format='%H %s' "$BASE..$BRANCH" 2>/dev/null \
      | grep -F "$TID" | head -1 | awk '{print $1}')
    if [ -z "$FIRST_COMMIT" ]; then
      record_gate "$TID" "tdd-order" "fail" "no commit on branch mentions $TID"
      GATE_FAIL="TDD violation: no commit on the feature branch mentions ticket $TID in its message."
    else
      FIRST_FILES=$(git -C "$PROJECT_ROOT" diff-tree --no-commit-id --name-only -r "$FIRST_COMMIT" 2>/dev/null)
      if echo "$FIRST_FILES" | grep -qE '(^|/)(tests?|__tests__|spec)(/|$)' \
         || echo "$FIRST_FILES" | grep -qE '(test_|_test\.|\.test\.|\.spec\.)'; then
        record_gate "$TID" "tdd-order" "pass" "first $TID commit ($FIRST_COMMIT) touches a test file"
      else
        record_gate "$TID" "tdd-order" "fail" "first $TID commit ($FIRST_COMMIT) does not touch a test file"
        GATE_FAIL="TDD violation: the first commit for $TID must add/modify a test file."
      fi
    fi
  fi
fi

# ── Gate 3b: Tests EXIST for changed code ───────────────────────────
if [ -z "$GATE_FAIL" ] && [ "$GATE_REQUIRE_TESTS" = "true" ]; then
  CHANGED=$(git -C "$PROJECT_ROOT" diff --name-only "main..$BRANCH" 2>/dev/null | \
    grep -vE '(test|spec|__tests__)' | \
    grep -E '\.(js|ts|jsx|tsx|py|rs|go|java|c|cpp|h)$' || true)
  TEST_FILES=$(git -C "$PROJECT_ROOT" diff --name-only "main..$BRANCH" 2>/dev/null | \
    grep -E '(test|spec|__tests__)' || true)
  if [ -n "$CHANGED" ] && [ -z "$TEST_FILES" ]; then
    record_gate "$TID" "tests-exist" "fail" "code changed but no test files added/modified"
    GATE_FAIL="no tests for changed code"
  else
    record_gate "$TID" "tests-exist" "pass" "$(echo "$TEST_FILES" | wc -l) test files touched"
  fi
fi

# ── Gate 4: All tests pass ──────────────────────────────────────────
if [ -z "$GATE_FAIL" ]; then
  TEST_CMD=$(detect_test_cmd "$WORKTREE")
  if [ -z "$TEST_CMD" ]; then
    record_gate "$TID" "tests-pass" "skip" "no test framework detected"
  else
    if TEST_OUT=$(bash -c "$TEST_CMD" 2>&1); then
      record_gate "$TID" "tests-pass" "pass" "exit 0"
    else
      FAIL_FILE="$COMPANY_DIR/departments/$DEPT/components/$TID/test-output.log"
      mkdir -p "$(dirname "$FAIL_FILE")"
      echo "$TEST_OUT" > "$FAIL_FILE"
      record_gate "$TID" "tests-pass" "fail" "see $FAIL_FILE"
      GATE_FAIL="tests failed (output saved to $FAIL_FILE)"
    fi
  fi
fi

# ── Gate 5: Linter passes ───────────────────────────────────────────
if [ -z "$GATE_FAIL" ] && [ "$GATE_REQUIRE_LINT" = "true" ]; then
  LINT_CMD=$(detect_lint_cmd "$WORKTREE")
  if [ -z "$LINT_CMD" ]; then
    record_gate "$TID" "lint-pass" "skip" "no linter detected"
  else
    if LINT_OUT=$(bash -c "$LINT_CMD" 2>&1); then
      record_gate "$TID" "lint-pass" "pass" "clean"
    else
      record_gate "$TID" "lint-pass" "fail" "$(echo "$LINT_OUT" | head -3)"
      GATE_FAIL="linter failed"
    fi
  fi
fi

# ── Gate 6: Security audit ──────────────────────────────────────────
if [ -z "$GATE_FAIL" ] && [ "$GATE_REQUIRE_SECURITY" = "true" ]; then
  SEC_CMD=$(bash "$SCRIPT_DIR/security-audit.sh" cmd "$WORKTREE" 2>/dev/null)
  if [ -z "$SEC_CMD" ]; then
    # No security tool installed for this stack (e.g. Python without
    # bandit/pip-audit). Skip, like the tests-pass and lint-pass gates do
    # when no framework/linter is detected — hard-failing here bounced
    # EVERY code ticket on any project without the audit tool installed.
    # The detail names the stack and what to install, and says NOT
    # scanned: on the dashboard a skip must never read like a pass.
    SEC_HINT=$(bash "$SCRIPT_DIR/security-audit.sh" hint "$WORKTREE" 2>/dev/null)
    SEC_DETAIL="SKIPPED - no security tool available for this stack; NOT scanned (install: ${SEC_HINT:-unknown stack})"
    record_gate "$TID" "security-clean" "skip" "$SEC_DETAIL"
    # Log the skip too: the gate output is what operators read, and a
    # silent skip reads exactly like a pass.
    log "  SKIP security-clean: $SEC_DETAIL"
  else
    if SEC_OUT=$(bash -c "$SEC_CMD" 2>&1); then
      record_gate "$TID" "security-clean" "pass" "no high-severity vulnerabilities"
      log "  PASS security-clean: clean"
    else
      SEC_FAIL_DETAIL=$(echo "$SEC_OUT" | head -3)
      record_gate "$TID" "security-clean" "fail" "$SEC_FAIL_DETAIL"
      log "  FAIL security-clean: $SEC_FAIL_DETAIL"
      GATE_FAIL="security audit failed"
    fi
  fi
fi

# ── Final: bounce or mark passed ────────────────────────────────────
if [ -n "$GATE_FAIL" ]; then
  bounce_ticket "$TID" "$GATE_FAIL"
  exit 2
else
  mark_gates_passed "$TID"
  exit 0
fi
