#!/usr/bin/env bash
# block-admin-code-writes.sh — DEPRECATED. No longer installed by hive.
# Kept for reference only. Formerly a PreToolUse hook to forbid admin departments
# (hr, it, rd, marketing, legal, finance, executive) from writing
# APPLICATION SOURCE CODE — i.e., the program logic engineering departments
# implement. Configuration, build/CI infrastructure, documentation, and
# everything else is allowed.
#
# The rule: admin depts plan + scaffold. They file tickets via MCP and
# engineering departments implement the actual program logic. IT, in
# particular, legitimately needs to write build infrastructure (CI yaml,
# Dockerfiles, Makefiles, dev scripts, lint/format configs, etc.) —
# that's IT's job. Marketing needs to write marketing site / output.
# Legal needs LICENSE / privacy / terms. Etc.
#
# What counts as "application source code" (BLOCKED for admins):
#   - Files matching common source-language extensions: .py / .js / .ts /
#     .go / .rs / .c / .cpp / .java / .kt / .swift / .rb / .php / etc.
#   - Files under common source directories: src/, lib/, app/, backend/,
#     frontend/, server/, client/, packages/, apps/, internal/, pkg/, cmd/
#   - Test files: tests/, __tests__/, spec/, *.test.*, *.spec.*, *_test.*
#
# What is NOT considered code (ALLOWED for admins):
#   - Build / CI / dev tooling: Dockerfile, Makefile, CMakeLists.txt,
#     Cargo.toml, package.json, pyproject.toml, tsconfig.json,
#     .github/workflows/*.yml, scripts/*.sh, .editorconfig, .clang-format,
#     .clang-tidy, .pre-commit-config.yaml, etc.
#   - Documentation: README, CONTRIBUTING, docs/, *.md, *.rst, *.adoc
#   - Configuration: .gitignore, .env.example, .nvmrc, .python-version
#   - Anything under .company/ (the block-company-writes hook handles that)
#   - Anything outside the project working tree (/tmp/, /var/tmp/)
#
# Reads $HIVE_DEPT from the spawn's environment (set by agent-spawn.sh).
# If empty (manual agent session), the hook is a no-op so humans aren't
# restricted.
#
# Exit codes:
#   0 = allow tool call
#   2 = block tool call (agent sees stderr as the reason)

set -euo pipefail

PAYLOAD=$(cat)

TOOL_NAME=$(echo "$PAYLOAD" | jq -r '.tool_name // empty')
FILE_PATH=$(echo "$PAYLOAD" | jq -r '.tool_input.file_path // empty')

# Only act on Write/Edit
case "$TOOL_NAME" in
  Write|Edit) ;;
  *) exit 0 ;;
esac

# Allow if no file_path (shouldn't happen for Write/Edit, defensive).
[ -z "$FILE_PATH" ] && exit 0

# Read HIVE_DEPT from env. Unset → human session → allow.
HIVE_DEPT_ENV="${HIVE_DEPT:-}"
[ -z "$HIVE_DEPT_ENV" ] && exit 0

# Admin depts: anything in this set is BLOCKED from writing git-tracked files.
case "$HIVE_DEPT_ENV" in
  hr|it|rd|marketing|legal|finance|executive) ;;
  *) exit 0 ;;   # engineering dept — allow
esac

# Allow .company/ writes — the block-company-writes hook handles those.
case "$FILE_PATH" in
  *"/.company/"*|".company/"*) exit 0 ;;
esac

# Allow paths clearly outside any project working tree (tmp scratch).
case "$FILE_PATH" in
  /tmp/*|/var/tmp/*|/dev/*) exit 0 ;;
esac

# Strip everything before the last "/<project>/" segment so we can match
# basenames and relative-to-project paths uniformly. We work with both
# the full path (for /src/, etc.) and the basename (for *.py, etc.).
BASENAME="${FILE_PATH##*/}"

# Decide if this looks like application source code (BLOCK) vs. config /
# build / docs / etc. (ALLOW). Match either by extension OR by being
# under a common source/test directory.

IS_CODE=0

# Source-language file extensions (case-sensitive intentionally — these
# are the standard forms; uppercase variants would be unusual).
case "$BASENAME" in
  *.py|*.pyi|*.pyx|*.pxd)                                  IS_CODE=1 ;;
  *.js|*.jsx|*.mjs|*.cjs|*.ts|*.tsx|*.mts|*.cts)           IS_CODE=1 ;;
  *.java|*.kt|*.kts|*.scala|*.sc|*.groovy|*.clj|*.cljs)    IS_CODE=1 ;;
  *.c|*.cc|*.cpp|*.cxx|*.c++|*.h|*.hh|*.hpp|*.hxx|*.h++)   IS_CODE=1 ;;
  *.cs|*.fs|*.vb)                                          IS_CODE=1 ;;
  *.go|*.rs|*.zig|*.nim|*.v|*.vala)                        IS_CODE=1 ;;
  *.rb|*.php|*.phtml|*.phps)                               IS_CODE=1 ;;
  *.swift|*.m|*.mm|*.dart)                                 IS_CODE=1 ;;
  *.elm|*.ex|*.exs|*.erl|*.hrl|*.ml|*.mli|*.hs|*.lhs)      IS_CODE=1 ;;
  *.lua|*.r|*.R|*.jl|*.cr|*.pl|*.pm|*.pod)                 IS_CODE=1 ;;
  *.vue|*.svelte|*.astro)                                  IS_CODE=1 ;;
  *.proto|*.thrift|*.fbs|*.capnp)                          IS_CODE=1 ;;
  *.sol|*.tla|*.tlapm)                                     IS_CODE=1 ;;
esac

# Test files (any language) are always code.
case "$BASENAME" in
  *.test.*|*.spec.*|*_test.*|test_*.*)                     IS_CODE=1 ;;
esac

# Files under common source / test directories — match both project-relative
# and absolute paths.
case "$FILE_PATH" in
  */src/*|*/lib/*|*/app/*|*/backend/*|*/frontend/*) IS_CODE=1 ;;
  */server/*|*/client/*|*/packages/*|*/apps/*)      IS_CODE=1 ;;
  */internal/*|*/pkg/*|*/cmd/*)                     IS_CODE=1 ;;
  */tests/*|*/test/*|*/__tests__/*|*/spec/*|*/specs/*) IS_CODE=1 ;;
esac
case "$FILE_PATH" in
  src/*|lib/*|app/*|backend/*|frontend/*) IS_CODE=1 ;;
  server/*|client/*|packages/*|apps/*)    IS_CODE=1 ;;
  internal/*|pkg/*|cmd/*)                 IS_CODE=1 ;;
  tests/*|test/*|__tests__/*|spec/*|specs/*) IS_CODE=1 ;;
esac

# Not code → allow. Config, build, CI, docs, etc. all fall through here.
[ "$IS_CODE" = "0" ] && exit 0

# Matched code pattern → BLOCK.
DEPT_DISPLAY="$HIVE_DEPT_ENV"
FILE_DISPLAY="$FILE_PATH"
export DEPT_DISPLAY FILE_DISPLAY
cat >&2 <<'EOF'
BLOCKED: Admin departments (hr, it, rd, marketing, legal, finance,
executive) cannot Write/Edit application source code.
EOF
echo "" >&2
echo "Department: $DEPT_DISPLAY" >&2
echo "File:       $FILE_DISPLAY" >&2
cat >&2 <<'EOF'

Reason:  This file looks like application source code (matches a known
         source-language extension or sits under a source/test directory
         like src/, lib/, tests/, etc.). Application code is implemented
         by engineering departments (ic-engineer / ic-infra) during
         stage 4 (tdd-green) of a sprint.

         Admin departments CAN freely write build/CI/infra/config/docs
         (Dockerfile, Makefile, CMakeLists.txt, .github/workflows/*.yml,
         scripts/*.sh, package.json, *.md, .editorconfig, etc.). The
         block applies only to source-language files.

What to do for actual source-code work:
  1. Pick the engineering department that owns this work. Look in
     .company/org-chart.json for entries with "type": "dynamic".
  2. File the ticket via MCP:
        mcp__hive__ticket_create(
          dept="<engineering-dept-name>",
          type="feature|bug|improvement|chore|infrastructure",
          priority="critical|high|medium|low",
          title="<imperative summary>",
          description="<what needs to change + why + acceptance criteria>",
          created_by="<your-vp>",
          source="vp-plan"
        )
  3. Your own work product (reports, plans, sprint direction) goes under
     .company/ via the bash script tools:
        report-write.sh, sprint-direction-write.sh, chat-send.sh,
        status-update.sh.

If you genuinely need to bypass this (manual operator override), exit
your spawn and edit the file by hand outside the agent context.
EOF
exit 2
