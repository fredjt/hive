#!/usr/bin/env bash
# block-company-writes.sh - PreToolUse hook to forbid Write/Edit on .company/ paths
#
# Reads PreToolUse JSON from stdin. Blocks Write/Edit tool calls whose
# file_path contains ".company/" anywhere in the path.
#
# Exit codes:
#   0 = allow tool call
#   2 = block tool call (Claude sees stderr as the reason)

set -euo pipefail

# Read the hook payload from stdin
PAYLOAD=$(cat)

# Extract tool name and file_path
TOOL_NAME=$(echo "$PAYLOAD" | jq -r '.tool_name // empty')
FILE_PATH=$(echo "$PAYLOAD" | jq -r '.tool_input.file_path // empty')

# Only act on Write/Edit
if [ "$TOOL_NAME" != "Write" ] && [ "$TOOL_NAME" != "Edit" ]; then
  exit 0
fi

# Allow if no file_path (shouldn't happen for Write/Edit, but be safe)
if [ -z "$FILE_PATH" ]; then
  exit 0
fi

# Check if path targets .company/ directory
case "$FILE_PATH" in
  */.company/*|*/.company|.company/*|.company)
    cat >&2 <<EOF
BLOCKED: Direct Write/Edit on .company/ is forbidden.

File: $FILE_PATH

The .company/ directory must only be written via scripts in:
  ~/.claude/skills/company/tools/

Common replacements:
  .company/departments/<dept>/status.json    -> bash SKILL_DIR/lib/tools/status-update.sh <dept> --key value
  .company/marketing/reports/*.md            -> bash SKILL_DIR/lib/tools/report-write.sh marketing <name> --stdin
  .company/research/ongoing/*-findings.md    -> bash SKILL_DIR/lib/tools/research-findings.sh <ticket-id> --stdin
  .company/departments/*/components/*/test-results.md -> bash SKILL_DIR/lib/tools/test-results.sh <dept> <component> --stdin
  .company/meetings/history/*.md             -> bash SKILL_DIR/lib/tools/meeting-record.sh <type> <timestamp> --stdin
  .company/inbox/user-requests.md            -> bash SKILL_DIR/lib/tools/inbox-write.sh <type> --content "..."
  .company/outbox/*.md                       -> bash SKILL_DIR/lib/tools/outbox-write.sh <type> --content "..."
  .company/chat/*.md                         -> bash SKILL_DIR/lib/tools/chat-send.sh <channel> <author> <message>
  .company/manifest.json                     -> bash SKILL_DIR/lib/tools/init-manifest.sh ...

See ~/.claude/skills/company/tools/company-api.md for the full script API.
EOF
    exit 2
    ;;
esac

# Allow everything else
exit 0
