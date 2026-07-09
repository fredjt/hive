# Agent Write Access Policy

## CRITICAL RULE: .company/ Directory is Read-Only via Write/Edit Tools

ALL agents must use scripts for ANY write operation to `.company/` directory.

## Allowed Write Operations

Agents CAN use Write/Edit tools for:
- Project source code (src/, backend/, frontend/, etc.)
- Project configuration files (package.json, tsconfig.json, etc.)
- Project documentation (README.md, docs/, etc.)
- Any file OUTSIDE `.company/` directory

## Forbidden Write Operations

Agents MUST NOT use Write/Edit tools for:
- `.company/` directory files (use scripts instead)

## Scripts for .company/ Operations

ALL .company/ writes MUST go through these scripts:

### Initialization (SKILL.md only)
- `bash SKILL_DIR/lib/tools/init-workspace.sh`
- `bash SKILL_DIR/lib/tools/init-manifest.sh`
- `bash SKILL_DIR/lib/tools/init-brief.sh`
- `bash SKILL_DIR/lib/tools/init-chat-channels.sh`
- `bash SKILL_DIR/lib/tools/init-meeting-schedule.sh`

### Status & Reporting
- `bash SKILL_DIR/lib/tools/status-update.sh <dept> --key value`
- `bash SKILL_DIR/lib/tools/report-write.sh <type> <filename> --stdin`
- `bash SKILL_DIR/lib/tools/research-findings.sh <ticket-id> --stdin`
- `bash SKILL_DIR/lib/tools/test-results.sh <dept> <component> --stdin`
- `bash SKILL_DIR/lib/tools/meeting-record.sh <type> <timestamp> --stdin`

### Communication
- `bash SKILL_DIR/lib/tools/inbox-write.sh <type> --content "..."`
- `bash SKILL_DIR/lib/tools/outbox-write.sh <type> --content "..."`
- `bash SKILL_DIR/lib/tools/chat-send.sh <channel> <author> <message>`

### Tickets (unchanged)
- All `ticket-*.sh` scripts
- All `sprint-*.sh` scripts

See `tools/company-api.md` for complete reference.
