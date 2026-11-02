#!/usr/bin/env bash
# block-admin-code-writes.sh — INERT no-op passthrough (since 0.6.0).
#
# This script used to be a PreToolUse hook that forbade admin departments
# (hr, it, rd, marketing, legal, finance, executive) from writing
# application source code. Since 0.6.0 nothing registers it anymore:
# install.sh no longer writes any agent settings, and the agent runtime
# (lib/core/agent-harness.py) enforces its own safety limits instead.
#
# The file is kept as an always-allow passthrough because hook entries from
# 0.5.x installs may still reference it in an operator's settings file
# until packaging/uninstall.sh's one-time legacy cleanup runs. Invoking it
# must never break tooling: it drains the hook payload from stdin and
# exits 0 (allow).
#
# The admin-dept planning-only rule now lives in the prompts
# (lib/agents/_FRAMEWORK_PRIMER.md) and the admin department list itself in
# lib/tools/dept-helpers.sh (is_admin_dept).
#
# Exit codes:
#   0 = allow (always)

set -euo pipefail

# Drain any hook payload on stdin so a blocking pipe reader can't hang us.
cat >/dev/null 2>&1 || true

exit 0
