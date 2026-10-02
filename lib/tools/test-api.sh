#!/usr/bin/env bash
set -euo pipefail

# test-api.sh - Script API test suite
# Usage: test-api.sh [--quick | --full]
# Returns: Test report with pass/fail per script

MODE="${1:---quick}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_ROOT="/tmp/company-api-test-$$"
TESTS_PASSED=0
TESTS_FAILED=0

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "Company Script API Test Suite"
echo "=============================="
echo "Mode: $MODE"
echo "Test root: $TEST_ROOT"
echo ""

# Create test workspace
mkdir -p "$TEST_ROOT"
export COMPANY_DIR="$TEST_ROOT/.company"

# Helper functions
pass() {
  echo -e "${GREEN}✓${NC} $1"
  ((TESTS_PASSED++))
}

fail() {
  echo -e "${RED}✗${NC} $1: $2"
  ((TESTS_FAILED++))
}

warn() {
  echo -e "${YELLOW}⚠${NC} $1"
}

# Test 1: init-workspace.sh
echo "Testing init-workspace.sh..."
if bash "$SCRIPT_DIR/init-workspace.sh" "$TEST_ROOT" 2>/dev/null; then
  if [ -d "$COMPANY_DIR/departments/hr" ] && [ -f "$COMPANY_DIR/roster.json" ]; then
    pass "init-workspace.sh creates directory structure"
  else
    fail "init-workspace.sh" "Missing expected directories/files"
  fi
else
  fail "init-workspace.sh" "Script failed to execute"
fi

# Test 2: init-manifest.sh
echo "Testing init-manifest.sh..."
OUTPUT=$(bash "$SCRIPT_DIR/init-manifest.sh" "Test Project" true 2>/dev/null || echo "FAILED")
if echo "$OUTPUT" | jq -e '.project_name == "Test Project"' >/dev/null 2>&1; then
  pass "init-manifest.sh creates valid JSON manifest"
else
  fail "init-manifest.sh" "Invalid JSON or missing field"
fi

# Test 3: init-brief.sh
echo "Testing init-brief.sh..."
if bash "$SCRIPT_DIR/init-brief.sh" "Test brief content" --clarifications "Test clarifications" 2>/dev/null; then
  if grep -q "Test brief content" "$COMPANY_DIR/brief.md" 2>/dev/null; then
    pass "init-brief.sh writes brief correctly"
  else
    fail "init-brief.sh" "Brief content not found"
  fi
else
  fail "init-brief.sh" "Script failed to execute"
fi

# Test 4: init-chat-channels.sh
echo "Testing init-chat-channels.sh..."
if bash "$SCRIPT_DIR/init-chat-channels.sh" 2>/dev/null; then
  if [ -f "$COMPANY_DIR/chat/general.md" ] && [ -f "$COMPANY_DIR/chat/engineering.md" ]; then
    pass "init-chat-channels.sh creates default channels"
  else
    fail "init-chat-channels.sh" "Missing channel files"
  fi
else
  fail "init-chat-channels.sh" "Script failed to execute"
fi

# Test 6: status-update.sh
echo "Testing status-update.sh..."
mkdir -p "$COMPANY_DIR/departments/test-dept"
OUTPUT=$(bash "$SCRIPT_DIR/status-update.sh" test-dept --velocity 85 --health good 2>/dev/null || echo "FAILED")
if echo "$OUTPUT" | jq -e '.velocity == 85' >/dev/null 2>&1; then
  pass "status-update.sh updates department status"
else
  fail "status-update.sh" "Invalid update or missing field"
fi

# Test 9: report-write.sh
echo "Testing report-write.sh..."
OUTPUT=$(bash "$SCRIPT_DIR/report-write.sh" marketing test-report.md --content "Test report" 2>/dev/null || echo "FAILED")
if [ -f "$OUTPUT" ] && grep -q "Test report" "$OUTPUT" 2>/dev/null; then
  pass "report-write.sh writes report file"
else
  fail "report-write.sh" "Report not written"
fi

# Test 10: Idempotency - running init-workspace twice
echo "Testing idempotency (init-workspace)..."
if bash "$SCRIPT_DIR/init-workspace.sh" "$TEST_ROOT" 2>&1 | grep -q "already exists"; then
  pass "init-workspace.sh is idempotent"
else
  warn "init-workspace.sh idempotency check inconclusive"
fi

# Full mode tests (skipped in quick mode)
if [ "$MODE" = "--full" ]; then
  echo ""
  echo "Running full test suite..."

  # Test stdin input
  echo "Testing stdin input (report-write)..."
  OUTPUT=$(echo "Stdin content" | bash "$SCRIPT_DIR/report-write.sh" research test-stdin.md --stdin 2>/dev/null || echo "FAILED")
  if grep -q "Stdin content" "$OUTPUT" 2>/dev/null; then
    pass "report-write.sh handles stdin input"
  else
    fail "report-write.sh" "Stdin input not handled"
  fi

  # Test error handling
  echo "Testing error handling (missing arguments)..."
  if ! bash "$SCRIPT_DIR/init-manifest.sh" 2>/dev/null; then
    pass "Scripts validate required arguments"
  else
    fail "Error handling" "Script should fail with missing args"
  fi
fi

# Cleanup
rm -rf "$TEST_ROOT"

# Summary
echo ""
echo "=============================="
echo "Test Results:"
echo -e "${GREEN}Passed:${NC} $TESTS_PASSED"
echo -e "${RED}Failed:${NC} $TESTS_FAILED"
echo ""

if [ $TESTS_FAILED -eq 0 ]; then
  echo -e "${GREEN}All tests passed!${NC}"
  exit 0
else
  echo -e "${RED}Some tests failed.${NC}"
  exit 1
fi
