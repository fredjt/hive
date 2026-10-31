#!/usr/bin/env bash
set -uo pipefail

# release-gates.sh — deterministic pre-release gates. Mirrors the shape of
# verification-gates.sh but operates against the project as a whole, not a
# single ticket.
#
# Usage: release-gates.sh --version vX.Y.Z [--project-root <path>] [--dry-run]
# Exits 0 if all required gates pass; non-zero with a summary on stderr otherwise.
# Writes a per-release report to .company/releases/<version>-gates.json.
#
# --dry-run: run only the cheap read-only gates (semver, changelog,
# all-tickets-done, local main-clean). Skips the heavy/side-effecting
# gates (project test suite, lint, security audit, remote comparisons)
# and does NOT write the report file.

VERSION=""
PROJECT_ROOT="${PROJECT_ROOT:-$PWD}"
DRY_RUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --version)      VERSION="$2"; shift 2 ;;
    --project-root) PROJECT_ROOT="$2"; shift 2 ;;
    --dry-run)      DRY_RUN=1; shift ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done
[ -z "$VERSION" ] && { echo "Usage: release-gates.sh --version vX.Y.Z" >&2; exit 2; }

PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd)" || { echo "bad project root" >&2; exit 2; }
SKILL_DIR="${HIVE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
COMPANY_DIR="$PROJECT_ROOT/.company"
REPORT="$COMPANY_DIR/releases/${VERSION}-gates.json"

. "$SKILL_DIR/lib/tools/gh-helpers.sh" 2>/dev/null || true

declare -A RESULTS DETAIL
PASS=0; FAIL=0; SKIP=0
record() {
  RESULTS[$1]="$2"; DETAIL[$1]="$3"
  case "$2" in pass) PASS=$((PASS+1)) ;; fail) FAIL=$((FAIL+1)) ;; skip) SKIP=$((SKIP+1)) ;; esac
  printf "  %-22s %-5s  %s\n" "$1" "$2" "$3" >&2
}

cd "$PROJECT_ROOT"

# Gate: semver — version parses, > previous tag
if ! echo "$VERSION" | grep -qE '^v?[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.-]+)?$'; then
  record "semver" "fail" "version '$VERSION' is not valid semver"
else
  prev_tag=$(git tag --list 'v*' --sort=-version:refname | head -1)
  if [ -n "$prev_tag" ]; then
    if [ "$(printf '%s\n%s\n' "$prev_tag" "$VERSION" | sort -V | tail -1)" != "$VERSION" ] || [ "$prev_tag" = "$VERSION" ]; then
      record "semver" "fail" "$VERSION is not greater than previous tag $prev_tag"
    else
      record "semver" "pass" "$VERSION > $prev_tag"
    fi
  else
    record "semver" "pass" "$VERSION (no prior tag)"
  fi
fi

# Gate: main-clean — no uncommitted changes; no commits ahead of origin/main
status_porcelain=$(git -C "$PROJECT_ROOT" status --porcelain 2>/dev/null)
if [ -n "$status_porcelain" ]; then
  record "main-clean" "fail" "uncommitted changes in working tree ($(echo "$status_porcelain" | wc -l) lines)"
else
  if [ "$DRY_RUN" -eq 1 ]; then
    record "main-clean" "pass" "working tree clean (dry-run: remote comparison skipped)"
  elif gh_remote_ok; then
    git fetch -q origin main 2>/dev/null || true
    ahead=$(git rev-list --count origin/main..main 2>/dev/null || echo 0)
    behind=$(git rev-list --count main..origin/main 2>/dev/null || echo 0)
    if [ "$ahead" -gt 0 ]; then
      record "main-clean" "fail" "local main is $ahead commits ahead of origin/main (push first)"
    elif [ "$behind" -gt 0 ]; then
      record "main-clean" "fail" "local main is $behind commits behind origin/main (pull first)"
    else
      record "main-clean" "pass" "main matches origin/main exactly"
    fi
  else
    record "main-clean" "pass" "no remote — local-only check ok"
  fi
fi

# Gate: changelog — a CHANGELOG file mentions this version
clog=""
for c in CHANGELOG.md CHANGELOG marketing/output/CHANGELOG.md .company/marketing/output/CHANGELOG.md; do
  if [ -f "$PROJECT_ROOT/$c" ]; then clog="$c"; break; fi
done
if [ -n "$clog" ]; then
  if grep -qF "$VERSION" "$PROJECT_ROOT/$clog" 2>/dev/null; then
    record "changelog" "pass" "$clog contains $VERSION"
  else
    record "changelog" "fail" "$clog has no entry for $VERSION"
  fi
else
  record "changelog" "skip" "no CHANGELOG file present"
fi

# Gate: all-tickets-done — no in-progress/in-review tickets targeted at this version
inflight=0
if [ -d "$COMPANY_DIR/departments" ]; then
  for st in in-progress in-review; do
    while IFS= read -r f; do
      [ -f "$f" ] || continue
      tgt=$(jq -r '.release.target_version // empty' "$f" 2>/dev/null)
      [ "$tgt" = "$VERSION" ] && inflight=$((inflight + 1))
    done < <(find "$COMPANY_DIR/departments" -path "*/tickets/$st/*.json" 2>/dev/null)
  done
fi
if [ "$inflight" -gt 0 ]; then
  record "all-tickets-done" "fail" "$inflight tickets targeted at $VERSION still in-flight"
else
  record "all-tickets-done" "pass" "no targeted tickets in-flight"
fi

# Gate: ci-green-on-main — last CI run on main was success
if [ "$DRY_RUN" -eq 1 ]; then
  record "ci-green-on-main" "skip" "dry-run"
elif gh_preflight 2>/dev/null; then
  conc=$(gh run list --branch main --limit 1 --json conclusion -q '.[0].conclusion' 2>/dev/null)
  if [ -z "$conc" ]; then
    record "ci-green-on-main" "skip" "no CI runs on main yet"
  elif [ "$conc" = "success" ]; then
    record "ci-green-on-main" "pass" "last main run = success"
  else
    record "ci-green-on-main" "fail" "last main run = $conc"
  fi
else
  record "ci-green-on-main" "skip" "gh not available"
fi

# Gate: tests — full project test suite passes
TEST_CMD=""
if [ -f package.json ] && jq -e '.scripts.test' package.json >/dev/null 2>&1; then TEST_CMD="npm test --silent"
elif [ -f pyproject.toml ] || [ -f pytest.ini ] || [ -f setup.cfg ]; then TEST_CMD="pytest -q"
elif [ -f Cargo.toml ]; then TEST_CMD="cargo test --quiet"
elif [ -f go.mod ]; then TEST_CMD="go test ./..."
fi
if [ -n "$TEST_CMD" ]; then
  if [ "$DRY_RUN" -eq 1 ]; then
    record "tests" "skip" "dry-run (would run: $TEST_CMD)"
  elif eval "$TEST_CMD" >/tmp/release-gates-tests.log 2>&1; then
    record "tests" "pass" "$TEST_CMD"
  else
    record "tests" "fail" "$TEST_CMD failed (see /tmp/release-gates-tests.log)"
  fi
else
  record "tests" "skip" "no detected test framework"
fi

# Gate: lint
LINT_CMD=""
if [ -f package.json ] && jq -e '.scripts.lint' package.json >/dev/null 2>&1; then LINT_CMD="npm run lint --silent"
elif [ -f pyproject.toml ] && command -v ruff >/dev/null 2>&1; then LINT_CMD="ruff check ."
elif [ -f Cargo.toml ]; then LINT_CMD="cargo clippy --quiet -- -D warnings"
elif [ -f go.mod ]; then LINT_CMD="go vet ./..."
fi
if [ -n "$LINT_CMD" ]; then
  if [ "$DRY_RUN" -eq 1 ]; then
    record "lint" "skip" "dry-run (would run: $LINT_CMD)"
  elif eval "$LINT_CMD" >/tmp/release-gates-lint.log 2>&1; then
    record "lint" "pass" "$LINT_CMD"
  else
    record "lint" "fail" "$LINT_CMD failed (see /tmp/release-gates-lint.log)"
  fi
else
  record "lint" "skip" "no detected linter"
fi

# Gate: security
SEC_CMD=""
if [ -f package.json ]; then SEC_CMD="npm audit --audit-level=high --silent"
elif [ -f pyproject.toml ] && command -v pip-audit >/dev/null 2>&1; then SEC_CMD="pip-audit --disable-pip"
elif [ -f Cargo.toml ] && command -v cargo-audit >/dev/null 2>&1; then SEC_CMD="cargo audit --quiet"
fi
if [ -n "$SEC_CMD" ]; then
  if [ "$DRY_RUN" -eq 1 ]; then
    record "security" "skip" "dry-run (would run: $SEC_CMD)"
  elif eval "$SEC_CMD" >/tmp/release-gates-sec.log 2>&1; then
    record "security" "pass" "$SEC_CMD"
  else
    record "security" "fail" "$SEC_CMD found issues (see /tmp/release-gates-sec.log)"
  fi
else
  record "security" "skip" "no detected security auditor"
fi

# Write the report (a dry-run has no side effects, so skip the file)
if [ "$DRY_RUN" -eq 1 ]; then
  echo "release-gates (dry-run): no report written. pass=$PASS fail=$FAIL skip=$SKIP"
else
  mkdir -p "$COMPANY_DIR/releases"
  {
  echo "{"
  echo "  \"version\":   \"$VERSION\","
  echo "  \"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\","
  echo "  \"summary\":   { \"pass\": $PASS, \"fail\": $FAIL, \"skip\": $SKIP },"
  echo "  \"gates\":     {"
  first=1
  for g in "${!RESULTS[@]}"; do
    [ $first -eq 0 ] && echo ","
    first=0
    printf '    "%s": { "status": "%s", "detail": %s }' \
      "$g" "${RESULTS[$g]}" "$(printf '%s' "${DETAIL[$g]}" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')"
  done
  echo
  echo "  }"
  echo "}"
  } > "$REPORT"
  echo
  echo "release-gates report: $REPORT"
  echo "  pass=$PASS  fail=$FAIL  skip=$SKIP"
fi

if [ "$FAIL" -gt 0 ]; then
  echo "release-gates: $FAIL gate(s) failed — release blocked." >&2
  exit 1
fi
exit 0
