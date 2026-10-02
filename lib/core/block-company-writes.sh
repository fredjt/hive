#!/usr/bin/env bash
# block-company-writes.sh — DEPRECATED. No longer installed by hive.
# Kept for reference only. Formerly a PreToolUse hook to forbid agents from clobbering
# .company/ via either direct file edits OR bash commands.
#
# (Formerly read PreToolUse JSON from stdin.) Blocks:
#
#   1. Write / Edit / NotebookEdit on any path under .company/.
#      Agents must use the script tools so live state changes go through
#      the move/comment/etc. wrappers that the rest of hive expects.
#
#   2. Bash commands that mutate .company/ directly — `rm`, `mv`, `ln`,
#      `cp`, `chmod`, `chown`, `tar -x`, `dd`, `truncate`, `sed -i`, etc.,
#      OR shell redirects (`>`, `>>`) targeting a .company path.
#      This catches the symlink-replacement and `rm -rf .company` classes
#      of incidents that destroy live state. The agent can still READ
#      .company freely (cat, ls, jq, find, grep, …).
#
# Exit codes:
#   0 = allow
#   2 = block (agent sees stderr as the reason)

set -euo pipefail

PAYLOAD=$(cat)
TOOL_NAME=$(echo "$PAYLOAD" | jq -r '.tool_name // empty')

# ── Direct file-edit tools ────────────────────────────────────────────────
if [ "$TOOL_NAME" = "Write" ] || [ "$TOOL_NAME" = "Edit" ] || [ "$TOOL_NAME" = "NotebookEdit" ]; then
  FILE_PATH=$(echo "$PAYLOAD" | jq -r '.tool_input.file_path // .tool_input.notebook_path // empty')
  [ -z "$FILE_PATH" ] && exit 0
  case "$FILE_PATH" in
    */.company/*|*/.company|.company/*|.company)
      cat >&2 <<EOF
BLOCKED: Direct ${TOOL_NAME} on .company/ is forbidden.

File: $FILE_PATH

The .company/ directory must only be written via the script tools in
\$HIVE_ROOT/lib/tools/ (or the corresponding mcp__hive__* tools). Common
replacements:

  .company/departments/<dept>/status.json    -> mcp__hive__status_update / status-update.sh
  .company/marketing/reports/*.md            -> report-write.sh marketing <name> --stdin
  .company/meetings/history/*.md             -> meeting-record.sh <type> <ts> --stdin
  .company/chat/*.md                         -> mcp__hive__chat_send / chat-send.sh
  .company/manifest.json                     -> init-manifest.sh ...
  .company/departments/*/tickets/*.json      -> mcp__hive__ticket_* (never edit by hand)

See \$HIVE_ROOT/lib/tools/ for the full API.
EOF
      exit 2
      ;;
  esac
  exit 0
fi

# ── Bash commands ─────────────────────────────────────────────────────────
if [ "$TOOL_NAME" = "Bash" ]; then
  CMD=$(echo "$PAYLOAD" | jq -r '.tool_input.command // empty')
  [ -z "$CMD" ] && exit 0

  # Fast path: if .company isn't even mentioned, allow.
  case "$CMD" in
    *.company*) ;;
    *) exit 0 ;;
  esac

  reason=""
  block_reason() { [ -z "$reason" ] && reason="$1"; }

  # Removal: rm / unlink / rmdir / trash-* targeting .company.
  echo "$CMD" | grep -Eq '(^|[^[:alnum:]_/-])(rm|unlink|rmdir|trash|trash-put)([[:space:]]+(-[[:alpha:]-]+[[:space:]]+)*)+([^[:space:]]*\.company([[:space:]/'"'"'"]|$))' \
    && block_reason "rm/unlink targeting .company"

  # Move .company anywhere, or move anything OVER .company.
  echo "$CMD" | grep -Eq '(^|[^[:alnum:]_/-])mv([[:space:]]+(-[[:alpha:]-]+[[:space:]]+)*)+[^|;&]*\.company' \
    && block_reason "mv touching .company"

  # Symlink/hardlink involving .company (the symlink-self incident).
  echo "$CMD" | grep -Eq '(^|[^[:alnum:]_/-])ln([[:space:]]+(-[[:alpha:]-]+[[:space:]]+)*)+[^|;&]*\.company' \
    && block_reason "ln involving .company (this is what caused the .company-symlink-to-self crash)"

  # Copy INTO .company (overwrites live state). Reading via cp is rare.
  echo "$CMD" | grep -Eq '(^|[^[:alnum:]_/-])cp([[:space:]]+(-[[:alpha:]-]+[[:space:]]+)*)+[^|;&]+[[:space:]]+[^[:space:]]*\.company([/[:space:]]|$)' \
    && block_reason "cp writing into .company"

  # Permission/ownership changes.
  echo "$CMD" | grep -Eq '(^|[^[:alnum:]_/-])(chmod|chown|chgrp|setfacl)([[:space:]]+(-[[:alpha:]-]+[[:space:]]+)*)+[^|;&]*\.company' \
    && block_reason "chmod/chown/setfacl on .company"

  # Tar / unarchive into .company (silent overwrite).
  echo "$CMD" | grep -Eq '(^|[^[:alnum:]_/-])(tar|unzip|gunzip|bsdtar)([[:space:]]+[^|;&]*)*[[:space:]](-C[[:space:]]+|--directory[[:space:]]*=?)[^[:space:]]*\.company' \
    && block_reason "tar/unzip extracting into .company"

  # rsync writing INTO .company. (Reading FROM .company is fine.)
  echo "$CMD" | grep -Eq '(^|[^[:alnum:]_/-])rsync([[:space:]]+[^|;&]+)+[[:space:]][^[:space:]]*\.company/?(/?[[:space:]]|$)' \
    && block_reason "rsync targeting .company"

  # In-place edit tools (sed -i, perl -i, awk -i inplace).
  echo "$CMD" | grep -Eq '(^|[^[:alnum:]_/-])(sed[[:space:]]+(-[[:alpha:]]*i|-i[[:space:]])|perl[[:space:]]+(-[[:alpha:]]*i|-i[[:space:]])|awk[[:space:]]+-i[[:space:]]+inplace)[^|;&]*\.company' \
    && block_reason "in-place editor targeting .company"

  # Truncate / dd / mkfifo / mknod inside .company.
  echo "$CMD" | grep -Eq '(^|[^[:alnum:]_/-])(truncate|dd|mkfifo|mknod)[[:space:]][^|;&]*\.company' \
    && block_reason "truncate/dd/mkfifo on .company"

  # Shell redirect (>, >>, &>, |&) writing into .company.
  echo "$CMD" | grep -Eq '(>>?|&>|\|&)[[:space:]]*[^[:space:]|;&]*\.company' \
    && block_reason "shell redirect (> / >>) into .company"

  # Tee writing to .company.
  echo "$CMD" | grep -Eq '(^|[^[:alnum:]_/-])tee([[:space:]]+(-[[:alpha:]]+))*[[:space:]]+[^|;&]*\.company' \
    && block_reason "tee writing to .company"

  # mkdir/rmdir of .company itself.
  echo "$CMD" | grep -Eq '(^|[^[:alnum:]_/-])(mkdir|rmdir)([[:space:]]+(-[[:alpha:]-]+[[:space:]]+)*)+[^|;&]*\.company([/[:space:]]|$)' \
    && block_reason "mkdir/rmdir on .company"

  if [ -n "$reason" ]; then
    cat >&2 <<EOF
BLOCKED: Bash command would mutate .company/ — $reason.

Command: $CMD

The .company/ directory holds live company state (tickets, sprints, chat,
roster, ...). Direct shell mutation bypasses the move/lock/comment plumbing
and corrupts state. Use the script tools / MCP wrappers instead:

  ticket state            -> mcp__hive__ticket_move / ticket_approve / ticket_bounce
  ticket comments         -> mcp__hive__ticket_comment
  status / chat / report  -> mcp__hive__status_update / chat_send / report_write
  any other .company file -> the matching script in \$HIVE_ROOT/lib/tools/

You may freely READ from .company (cat, ls, jq, grep, find, head, tail, …).
EOF
    exit 2
  fi
fi

exit 0
